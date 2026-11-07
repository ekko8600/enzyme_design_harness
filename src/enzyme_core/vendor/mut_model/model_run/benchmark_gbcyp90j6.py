#!/usr/bin/env python3
"""Fair SFT / DPO-style / online-distillation benchmark for gbcyp90j6.

The existing DPO entrypoint depends on an external SaProt structure workflow
and contains task-specific reference-logit assumptions.  This benchmark keeps
the mutation-fitness task comparable by using the same encoder, the same
teacher pretraining data, the same DMS split, and a frozen-SFT-reference
pairwise DPO objective for all three methods.
"""

from __future__ import annotations

import argparse
import copy
import json
import math
import os
import sys
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence, Tuple

os.environ.setdefault("XLA_PYTHON_CLIENT_PREALLOCATE", "false")

import jax
import jax.numpy as jnp
import numpy as np
import pandas as pd
import yaml

from finetune_online_distill import (
    DEFAULT_CONFIG,
    EmpiricalCDFNormalizer,
    FitnessTransformer,
    TrainState,
    create_state,
    encode_sequences,
    make_dms_split,
    predict_ensemble,
    ranking_metrics,
    rank01,
    run as run_online,
)


@jax.jit
def kd_step(state: TrainState, tokens: jax.Array, mask: jax.Array, target: jax.Array) -> Tuple[TrainState, jax.Array]:
    def loss_fn(params):
        out = state.apply_fn({"params": params}, tokens, mask, deterministic=True)
        teacher_head = out["teachers"][:, 0]
        loss = jnp.mean((out["consensus"] - target) ** 2) + jnp.mean((teacher_head - target) ** 2)
        return loss
    loss, grads = jax.value_and_grad(loss_fn)(state.params)
    return state.apply_gradients(grads=grads), loss


def pairwise_loss(scores: jax.Array, target: jax.Array) -> jax.Array:
    other_scores = jnp.roll(scores, 1)
    other_target = jnp.roll(target, 1)
    signs = jnp.sign(target - other_target)
    valid = signs != 0
    return jnp.sum(jax.nn.softplus(-signs * (scores - other_scores)) * valid) / jnp.maximum(jnp.sum(valid), 1)


@jax.jit
def sft_step(state: TrainState, tokens: jax.Array, mask: jax.Array, target: jax.Array) -> Tuple[TrainState, jax.Array]:
    def loss_fn(params):
        scores = state.apply_fn({"params": params}, tokens, mask, deterministic=True)["dms"]
        return jnp.mean((scores - target) ** 2) + 0.5 * pairwise_loss(scores, target)
    loss, grads = jax.value_and_grad(loss_fn)(state.params)
    return state.apply_gradients(grads=grads), loss


@jax.jit
def dpo_step(
    state: TrainState,
    reference_params: Any,
    chosen_tokens: jax.Array,
    chosen_mask: jax.Array,
    rejected_tokens: jax.Array,
    rejected_mask: jax.Array,
    chosen_target: jax.Array,
    rejected_target: jax.Array,
    beta: float,
) -> Tuple[TrainState, jax.Array]:
    def loss_fn(params):
        chosen = state.apply_fn({"params": params}, chosen_tokens, chosen_mask, deterministic=True)["dms"]
        rejected = state.apply_fn({"params": params}, rejected_tokens, rejected_mask, deterministic=True)["dms"]
        ref_chosen = state.apply_fn({"params": reference_params}, chosen_tokens, chosen_mask, deterministic=True)["dms"]
        ref_rejected = state.apply_fn({"params": reference_params}, rejected_tokens, rejected_mask, deterministic=True)["dms"]
        preference_logit = beta * ((chosen - rejected) - (ref_chosen - ref_rejected))
        dpo = -jnp.mean(jax.nn.log_sigmoid(preference_logit))
        anchor = 0.5 * (jnp.mean((chosen - chosen_target) ** 2) + jnp.mean((rejected - rejected_target) ** 2))
        return dpo + 0.3 * anchor
    loss, grads = jax.value_and_grad(loss_fn)(state.params)
    return state.apply_gradients(grads=grads), loss


def batches(indices: np.ndarray, batch_size: int, rng: np.random.Generator):
    shuffled = rng.permutation(indices)
    for start in range(0, len(shuffled), batch_size):
        batch = shuffled[start : start + batch_size]
        if len(batch) < batch_size:
            batch = rng.choice(shuffled, size=batch_size, replace=True)
        yield batch


def selection_score(target: np.ndarray, prediction: np.ndarray) -> float:
    return ranking_metrics(target, prediction)["selection_score"]


def train_sft_or_dpo(
    method: str,
    model: FitnessTransformer,
    candidate_tokens: np.ndarray,
    candidate_mask: np.ndarray,
    teacher_target: np.ndarray,
    dms_tokens: np.ndarray,
    dms_mask: np.ndarray,
    dms_target: np.ndarray,
    split: Any,
    seed: int,
    pretrain_epochs: int,
    finetune_epochs: int,
    dpo_epochs: int,
    batch_size: int,
    training_cfg: Mapping[str, Any],
) -> TrainState:
    rng = np.random.default_rng(seed)
    state = create_state(jax.random.PRNGKey(seed), model, candidate_tokens.shape[1], training_cfg)
    candidate_indices = np.arange(len(candidate_tokens))
    for _ in range(pretrain_epochs):
        for idx in batches(candidate_indices, batch_size, rng):
            state, _ = kd_step(state, jnp.asarray(candidate_tokens[idx]), jnp.asarray(candidate_mask[idx]), jnp.asarray(teacher_target[idx]))

    best_state, best_score, stale = state, -np.inf, 0
    for _ in range(finetune_epochs):
        for idx in batches(split.train_indices, batch_size, rng):
            state, _ = sft_step(state, jnp.asarray(dms_tokens[idx]), jnp.asarray(dms_mask[idx]), jnp.asarray(dms_target[idx]))
        prediction, _ = predict_ensemble([state], dms_tokens[split.val_indices], dms_mask[split.val_indices], "dms", batch_size)
        score = selection_score(dms_target[split.val_indices], prediction)
        if score > best_score + 1e-8:
            best_state, best_score, stale = state, score, 0
        else:
            stale += 1
        if stale >= 6:
            break
    state = best_state
    if method == "sft":
        return state

    reference_params = state.params
    train_scores = dms_target[split.train_indices]
    order = split.train_indices[np.argsort(dms_target[split.train_indices])]
    midpoint = max(1, len(order) // 2)
    low, high = order[:midpoint], order[midpoint:]
    best_state, best_score, stale = state, -np.inf, 0
    for _ in range(dpo_epochs):
        chosen_idx = rng.choice(high, size=batch_size, replace=True)
        rejected_idx = rng.choice(low, size=batch_size, replace=True)
        state, _ = dpo_step(
            state, reference_params,
            jnp.asarray(dms_tokens[chosen_idx]), jnp.asarray(dms_mask[chosen_idx]),
            jnp.asarray(dms_tokens[rejected_idx]), jnp.asarray(dms_mask[rejected_idx]),
            jnp.asarray(dms_target[chosen_idx]), jnp.asarray(dms_target[rejected_idx]), 0.1,
        )
        prediction, _ = predict_ensemble([state], dms_tokens[split.val_indices], dms_mask[split.val_indices], "dms", batch_size)
        score = selection_score(dms_target[split.val_indices], prediction)
        if score > best_score + 1e-8:
            best_state, best_score, stale = state, score, 0
        else:
            stale += 1
        if stale >= 6:
            break
    return best_state


def evaluate(state: TrainState, tokens: np.ndarray, mask: np.ndarray, target: np.ndarray, split: Any, batch_size: int) -> Dict[str, float]:
    prediction, uncertainty = predict_ensemble([state], tokens[split.test_indices], mask[split.test_indices], "dms", batch_size)
    metrics = ranking_metrics(target[split.test_indices], prediction)
    metrics["mean_uncertainty"] = float(np.mean(uncertainty))
    return {key: float(value) for key, value in metrics.items()}


def run_benchmark(args: argparse.Namespace) -> Dict[str, Any]:
    root = Path(__file__).resolve().parents[1]
    output_dir = root / args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    candidate_df = pd.read_csv(root / "data/gbcyp90j6-pretrain.csv")
    dms_df = pd.read_csv(root / "data/gbcyp90j6.csv")
    candidate_sequences = candidate_df.mutated_sequence.astype(str).tolist()
    dms_sequences = dms_df.mutated_sequence.astype(str).tolist()
    max_length = max(max(map(len, candidate_sequences)), max(map(len, dms_sequences)))
    candidate_tokens, candidate_mask = encode_sequences(candidate_sequences, max_length)
    dms_tokens, dms_mask = encode_sequences(dms_sequences, max_length)
    split = make_dms_split(len(dms_df), args.train_size, args.val_size, args.seed)
    teacher_normalizer = EmpiricalCDFNormalizer(candidate_df.gemme_fitness.to_numpy())
    teacher_target = teacher_normalizer.transform(candidate_df.gemme_fitness.to_numpy())
    dms_normalizer = EmpiricalCDFNormalizer(dms_df.DMS_score.to_numpy()[split.train_indices])
    dms_target = dms_normalizer.transform(dms_df.DMS_score.to_numpy())

    model = FitnessTransformer(num_teachers=1, max_length=max_length, embed_dim=args.embed_dim, num_heads=4, num_layers=1, mlp_dim=args.embed_dim * 2)
    training_cfg = {"learning_rate": args.learning_rate, "encoder_lr_scale": 0.1, "weight_decay": 1e-4}
    sft_state = train_sft_or_dpo("sft", model, candidate_tokens, candidate_mask, teacher_target, dms_tokens, dms_mask, dms_target, split, args.seed, args.pretrain_epochs, args.finetune_epochs, 0, args.batch_size, training_cfg)
    dpo_state = train_sft_or_dpo("dpo", model, candidate_tokens, candidate_mask, teacher_target, dms_tokens, dms_mask, dms_target, split, args.seed, args.pretrain_epochs, args.finetune_epochs, args.dpo_epochs, args.batch_size, training_cfg)

    online_cfg = copy.deepcopy(DEFAULT_CONFIG)
    online_cfg.update({"seed": args.seed, "output_dir": str(output_dir / "online"), "candidate": {"path": "data/gbcyp90j6-pretrain.csv", "sequence_column": "mutated_sequence"}, "dms": {"path": "data/gbcyp90j6.csv", "sequence_column": "mutated_sequence", "score_column": "DMS_score", "train_size": args.train_size, "val_size": args.val_size}})
    online_cfg["teachers"] = [{"name": "gemme", "type": "csv_column", "version": "gbcyp90j6-local", "path": "data/gbcyp90j6-pretrain.csv", "sequence_column": "mutated_sequence", "score_column": "gemme_fitness"}]
    online_cfg["model"] = {"embed_dim": args.embed_dim, "num_heads": 4, "num_layers": 1, "mlp_dim": args.embed_dim * 2, "dropout": 0.0, "ensemble_size": 1}
    online_cfg["online"] = {"cold_start_size": 20, "cold_start_fraction": 0.20, "rounds": 2, "queries_per_round": 10, "max_query_fraction": 0.40, "screen_size": 100, "exploit_fraction": 0.40, "uncertainty_fraction": 0.30, "diversity_fraction": 0.20, "random_fraction": 0.10, "ucb_beta": 1.0, "min_round_improvement": 0.0, "stagnant_rounds": 3}
    online_cfg["training"] = {"batch_size": args.batch_size, "max_epochs": args.online_epochs, "head_warmup_epochs": 2, "patience": 6, "learning_rate": args.learning_rate, "encoder_lr_scale": 0.1, "weight_decay": 1e-4, "teacher_huber_weight": 0.35, "pairwise_weight": 0.35, "listwise_weight": 0.20, "consistency_weight": 0.10, "dms_loss_weight": 1.0, "top_dms_weight": 2.0, "min_pair_gap": 0.05, "listwise_temperature": 0.10}
    online_config_path = output_dir / "online_config.yaml"
    with online_config_path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(online_cfg, handle, sort_keys=False)
    online_result = run_online(online_config_path)
    online_last = online_result["rounds"][-1]

    results = {
        "protocol": {"protein": "gbcyp90j6", "seed": args.seed, "train_size": args.train_size, "val_size": args.val_size, "test_size": len(split.test_indices), "teacher": "GEMME", "candidate_teacher_labels": len(candidate_df), "online_teacher_query_budget": 40},
        "sft": evaluate(sft_state, dms_tokens, dms_mask, dms_target, split, args.batch_size),
        "dpo_style": evaluate(dpo_state, dms_tokens, dms_mask, dms_target, split, args.batch_size),
        "online_distill": {key.removeprefix("test_"): float(value) for key, value in online_last.items() if key.startswith("test_")} | {"queried_total": int(online_last["queried_total"])},
    }
    with (output_dir / "benchmark_results.json").open("w", encoding="utf-8") as handle:
        json.dump(results, handle, ensure_ascii=False, indent=2)
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return results


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default="outputs/benchmark_gbcyp90j6")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--train-size", type=int, default=20)
    parser.add_argument("--val-size", type=int, default=10)
    parser.add_argument("--embed-dim", type=int, default=64)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--learning-rate", type=float, default=3e-4)
    parser.add_argument("--pretrain-epochs", type=int, default=20)
    parser.add_argument("--finetune-epochs", type=int, default=30)
    parser.add_argument("--dpo-epochs", type=int, default=30)
    parser.add_argument("--online-epochs", type=int, default=30)
    return parser.parse_args(argv)


if __name__ == "__main__":
    run_benchmark(parse_args())
