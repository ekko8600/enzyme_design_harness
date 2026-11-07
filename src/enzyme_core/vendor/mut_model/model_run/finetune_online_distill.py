#!/usr/bin/env python3
"""Active online multi-teacher distillation for protein fitness ranking.

This is intentionally independent from the existing pretrain/SFT/DPO scripts.
It keeps evaluation sequences out of the teacher-query pool, queries one or
more teacher adapters round by round, and trains a small ensemble to rank the
top-fitness mutants with uncertainty estimates.

Run from the repository root:
  conda run -n agent python model_run/finetune_online_distill.py \
      --config configs/online_distill.yaml
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib
import json
import math
import os
import pickle
import sqlite3
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

os.environ.setdefault("XLA_PYTHON_CLIENT_PREALLOCATE", "false")

import jax
import jax.numpy as jnp
import numpy as np
import optax
import pandas as pd
import yaml
from flax import linen as nn
from flax import serialization
from flax.training import train_state
from scipy.optimize import nnls
from scipy.stats import rankdata, spearmanr


AA_ALPHABET = "ACDEFGHIKLMNPQRSTVWY"
AA_TO_ID = {aa: idx for idx, aa in enumerate(AA_ALPHABET)}
PAD_ID = len(AA_ALPHABET)


DEFAULT_CONFIG: Dict[str, Any] = {
    "seed": 42,
    "output_dir": "outputs/online_distill",
    "candidate": {
        "path": "data/test_protein-pretrain.csv",
        "sequence_column": "mutated_sequence",
        "wildtype_sequence": None,
    },
    "dms": {
        "path": "data/test_protein.csv",
        "sequence_column": "mutated_sequence",
        "score_column": "DMS_score",
        "train_size": 20,
        "val_size": 3,
    },
    "teachers": [],
    "model": {
        "embed_dim": 96,
        "num_heads": 4,
        "num_layers": 2,
        "mlp_dim": 192,
        "dropout": 0.0,
        "ensemble_size": 3,
    },
    "online": {
        "cold_start_size": 2048,
        "cold_start_fraction": 0.05,
        "rounds": 10,
        "queries_per_round": 512,
        "max_query_fraction": 0.20,
        "screen_size": 10000,
        "exploit_fraction": 0.40,
        "uncertainty_fraction": 0.30,
        "diversity_fraction": 0.20,
        "random_fraction": 0.10,
        "ucb_beta": 1.0,
        "min_round_improvement": 0.005,
        "stagnant_rounds": 2,
    },
    "training": {
        "batch_size": 64,
        "max_epochs": 50,
        "head_warmup_epochs": 5,
        "patience": 5,
        "learning_rate": 3e-4,
        "encoder_lr_scale": 0.1,
        "weight_decay": 1e-4,
        "teacher_huber_weight": 0.35,
        "pairwise_weight": 0.35,
        "listwise_weight": 0.20,
        "consistency_weight": 0.10,
        "dms_loss_weight": 1.0,
        "top_dms_weight": 2.0,
        "min_pair_gap": 0.05,
        "listwise_temperature": 0.10,
    },
}


def deep_update(base: Dict[str, Any], update: Mapping[str, Any]) -> Dict[str, Any]:
    for key, value in update.items():
        if isinstance(value, Mapping) and isinstance(base.get(key), dict):
            deep_update(base[key], value)
        else:
            base[key] = value
    return base


def load_config(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        user = yaml.safe_load(handle) or {}
    cfg = deep_update(copy.deepcopy(DEFAULT_CONFIG), user)
    if not cfg["teachers"]:
        raise ValueError("config.teachers must contain at least one teacher")
    fractions = [
        cfg["online"]["exploit_fraction"],
        cfg["online"]["uncertainty_fraction"],
        cfg["online"]["diversity_fraction"],
        cfg["online"]["random_fraction"],
    ]
    if not np.isclose(sum(fractions), 1.0):
        raise ValueError("online acquisition fractions must sum to 1.0")
    return cfg


def resolve_path(value: str, config_path: Path) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    repo_root = Path(__file__).resolve().parents[1]
    repo_candidate = (repo_root / path).resolve()
    config_candidate = (config_path.parent / path).resolve()
    return repo_candidate if repo_candidate.exists() else config_candidate


def stable_hash(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def sequence_hash(sequence: str) -> str:
    return hashlib.sha256(sequence.encode("ascii")).hexdigest()


def encode_sequences(sequences: Sequence[str], max_length: int) -> Tuple[np.ndarray, np.ndarray]:
    tokens = np.full((len(sequences), max_length), PAD_ID, dtype=np.int32)
    mask = np.zeros((len(sequences), max_length), dtype=np.float32)
    for row, sequence in enumerate(sequences):
        if len(sequence) > max_length:
            raise ValueError(f"sequence length {len(sequence)} exceeds max_length={max_length}")
        for col, aa in enumerate(sequence):
            if aa not in AA_TO_ID:
                raise ValueError(f"unsupported amino acid {aa!r} in sequence {row}")
            tokens[row, col] = AA_TO_ID[aa]
            mask[row, col] = 1.0
    return tokens, mask


class EmpiricalCDFNormalizer:
    """Training-only monotonic normalization that is robust across teachers."""

    def __init__(self, values: Sequence[float]):
        finite = np.asarray(values, dtype=np.float64)
        finite = finite[np.isfinite(finite)]
        if finite.size == 0:
            raise ValueError("normalizer needs at least one finite value")
        self.sorted_values = np.sort(finite)

    def transform(self, values: Sequence[float]) -> np.ndarray:
        values = np.asarray(values, dtype=np.float64)
        ranks = np.searchsorted(self.sorted_values, values, side="right")
        result = ranks / max(len(self.sorted_values), 1)
        result[~np.isfinite(values)] = np.nan
        return result.astype(np.float32)

    def to_dict(self) -> Dict[str, Any]:
        return {"sorted_values": self.sorted_values.tolist()}


@dataclass(frozen=True)
class DataSplit:
    train_indices: np.ndarray
    val_indices: np.ndarray
    test_indices: np.ndarray


def make_dms_split(size: int, train_size: int, val_size: int, seed: int) -> DataSplit:
    if train_size < 2:
        raise ValueError("dms.train_size must be at least 2")
    if train_size + val_size >= size:
        raise ValueError("DMS train_size + val_size must leave at least one test sample")
    rng = np.random.default_rng(seed)
    order = rng.permutation(size)
    return DataSplit(
        train_indices=np.sort(order[:train_size]),
        val_indices=np.sort(order[train_size : train_size + val_size]),
        test_indices=np.sort(order[train_size + val_size :]),
    )


class TeacherCache:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.execute(
            """CREATE TABLE IF NOT EXISTS teacher_scores (
                   sequence_hash TEXT NOT NULL,
                   sequence TEXT NOT NULL,
                   teacher TEXT NOT NULL,
                   version TEXT NOT NULL,
                   score REAL,
                   status TEXT NOT NULL,
                   elapsed_seconds REAL NOT NULL,
                   updated_at REAL NOT NULL,
                   PRIMARY KEY(sequence_hash, teacher, version)
               )"""
        )
        self.connection.commit()

    def get(self, sequence: str, teacher: str, version: str) -> Optional[Tuple[float, str]]:
        row = self.connection.execute(
            "SELECT score, status FROM teacher_scores WHERE sequence_hash=? AND teacher=? AND version=?",
            (sequence_hash(sequence), teacher, version),
        ).fetchone()
        return None if row is None else (float(row[0]) if row[0] is not None else np.nan, row[1])

    def put_many(
        self,
        sequences: Sequence[str],
        teacher: str,
        version: str,
        scores: Sequence[float],
        statuses: Sequence[str],
        elapsed_seconds: float,
    ) -> None:
        per_item = elapsed_seconds / max(len(sequences), 1)
        rows = []
        for sequence, score, status in zip(sequences, scores, statuses):
            rows.append(
                (
                    sequence_hash(sequence), sequence, teacher, version,
                    None if not np.isfinite(score) else float(score), status,
                    per_item, time.time(),
                )
            )
        self.connection.executemany(
            "INSERT OR REPLACE INTO teacher_scores VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()


class TeacherScorer:
    def __init__(self, name: str, version: str, config: Mapping[str, Any]):
        self.name = name
        self.version = version
        self.config = dict(config)

    def score(self, sequences: Sequence[str], context: Mapping[str, Any]) -> np.ndarray:
        raise NotImplementedError


class CSVColumnTeacher(TeacherScorer):
    def __init__(self, name: str, version: str, config: Mapping[str, Any], config_path: Path):
        super().__init__(name, version, config)
        path = resolve_path(config["path"], config_path)
        df = pd.read_csv(path)
        sequence_column = config.get("sequence_column", "mutated_sequence")
        score_column = config["score_column"]
        if df[sequence_column].duplicated().any():
            raise ValueError(f"teacher {name}: duplicate sequences in {path}")
        self.lookup = dict(zip(df[sequence_column].astype(str), pd.to_numeric(df[score_column], errors="coerce")))

    def score(self, sequences: Sequence[str], context: Mapping[str, Any]) -> np.ndarray:
        return np.asarray([self.lookup.get(sequence, np.nan) for sequence in sequences], dtype=np.float64)


class PythonCallableTeacher(TeacherScorer):
    def __init__(self, name: str, version: str, config: Mapping[str, Any]):
        super().__init__(name, version, config)
        module_name, function_name = config["callable"].split(":", 1)
        self.function = getattr(importlib.import_module(module_name), function_name)

    def score(self, sequences: Sequence[str], context: Mapping[str, Any]) -> np.ndarray:
        values = self.function(list(sequences), dict(context), dict(self.config.get("kwargs", {})))
        values = np.asarray(values, dtype=np.float64)
        if values.shape != (len(sequences),):
            raise ValueError(f"teacher {self.name} returned {values.shape}, expected {(len(sequences),)}")
        return values


def create_teachers(configs: Sequence[Mapping[str, Any]], config_path: Path) -> List[TeacherScorer]:
    teachers: List[TeacherScorer] = []
    names = set()
    for cfg in configs:
        name = cfg["name"]
        if name in names:
            raise ValueError(f"duplicate teacher name: {name}")
        names.add(name)
        teacher_type = cfg.get("type", "csv_column")
        version = str(cfg.get("version", "unknown"))
        if teacher_type == "csv_column":
            teachers.append(CSVColumnTeacher(name, version, cfg, config_path))
        elif teacher_type == "python_callable":
            teachers.append(PythonCallableTeacher(name, version, cfg))
        else:
            raise ValueError(f"unsupported teacher type: {teacher_type}")
    return teachers


def query_teachers(
    sequences: Sequence[str],
    teachers: Sequence[TeacherScorer],
    cache: TeacherCache,
    context: Mapping[str, Any],
) -> Tuple[np.ndarray, List[Dict[str, Any]]]:
    matrix = np.full((len(sequences), len(teachers)), np.nan, dtype=np.float32)
    audit: List[Dict[str, Any]] = []
    for column, teacher in enumerate(teachers):
        missing_positions, missing_sequences = [], []
        for row, sequence in enumerate(sequences):
            cached = cache.get(sequence, teacher.name, teacher.version)
            if cached is not None and cached[1] == "ok":
                matrix[row, column] = cached[0]
            else:
                missing_positions.append(row)
                missing_sequences.append(sequence)
        elapsed = 0.0
        if missing_sequences:
            started = time.time()
            try:
                scores = teacher.score(missing_sequences, context)
                statuses = ["ok" if np.isfinite(v) else "missing" for v in scores]
            except Exception as exc:  # teacher failure must not erase other teachers
                scores = np.full(len(missing_sequences), np.nan)
                statuses = [f"error:{type(exc).__name__}"] * len(missing_sequences)
            elapsed = time.time() - started
            cache.put_many(missing_sequences, teacher.name, teacher.version, scores, statuses, elapsed)
            for position, value, status in zip(missing_positions, scores, statuses):
                if status == "ok":
                    matrix[position, column] = value
        audit.append({
            "teacher": teacher.name,
            "version": teacher.version,
            "requested": len(sequences),
            "cache_misses": len(missing_sequences),
            "elapsed_seconds": elapsed,
            "finite_scores": int(np.isfinite(matrix[:, column]).sum()),
        })
    if np.all(~np.isfinite(matrix), axis=1).any():
        bad = int(np.all(~np.isfinite(matrix), axis=1).sum())
        raise RuntimeError(f"all teachers failed or had no score for {bad} queried sequences")
    return matrix, audit


def infer_wildtype(sequences: Sequence[str]) -> str:
    lengths = {len(sequence) for sequence in sequences}
    if len(lengths) != 1:
        raise ValueError("wildtype inference requires equal-length candidate sequences")
    array = np.asarray([[AA_TO_ID[aa] for aa in sequence] for sequence in sequences])
    mode = []
    for column in range(array.shape[1]):
        counts = np.bincount(array[:, column], minlength=len(AA_ALPHABET))
        mode.append(AA_ALPHABET[int(np.argmax(counts))])
    return "".join(mode)


def mutation_counts(sequences: Sequence[str], wildtype: str) -> np.ndarray:
    return np.asarray([sum(a != b for a, b in zip(sequence, wildtype)) for sequence in sequences])


def stratified_random_indices(
    available: np.ndarray, counts: np.ndarray, size: int, rng: np.random.Generator
) -> np.ndarray:
    if size >= len(available):
        return np.asarray(available, dtype=np.int64)
    groups: Dict[int, List[int]] = {}
    for idx in available:
        groups.setdefault(int(counts[idx]), []).append(int(idx))
    selected: List[int] = []
    keys = sorted(groups)
    while len(selected) < size and keys:
        next_keys = []
        for key in keys:
            if groups[key] and len(selected) < size:
                position = int(rng.integers(len(groups[key])))
                selected.append(groups[key].pop(position))
            if groups[key]:
                next_keys.append(key)
        keys = next_keys
    return np.asarray(selected, dtype=np.int64)


def rank01(values: np.ndarray) -> np.ndarray:
    values = np.asarray(values, dtype=np.float64)
    if len(values) <= 1:
        return np.zeros_like(values, dtype=np.float64)
    return (rankdata(values, method="average") - 1.0) / (len(values) - 1.0)


def hamming_diversity_select(
    candidate_indices: np.ndarray,
    queried_indices: np.ndarray,
    token_matrix: np.ndarray,
    size: int,
    rng: np.random.Generator,
    reference_limit: int = 1024,
) -> np.ndarray:
    if size <= 0 or len(candidate_indices) == 0:
        return np.empty(0, dtype=np.int64)
    candidates = np.asarray(candidate_indices, dtype=np.int64)
    if len(queried_indices) == 0:
        return rng.choice(candidates, size=min(size, len(candidates)), replace=False)
    refs = np.asarray(queried_indices, dtype=np.int64)
    if len(refs) > reference_limit:
        refs = rng.choice(refs, size=reference_limit, replace=False)
    min_dist = np.full(len(candidates), token_matrix.shape[1] + 1, dtype=np.int32)
    for start in range(0, len(refs), 64):
        ref_batch = token_matrix[refs[start : start + 64]]
        distances = np.mean(token_matrix[candidates, None, :] != ref_batch[None, :, :], axis=2)
        min_dist = np.minimum(min_dist, np.min(distances, axis=1))
    order = np.argsort(-min_dist, kind="stable")
    return candidates[order[: min(size, len(candidates))]]


def allocate_quotas(size: int, fractions: Sequence[float]) -> List[int]:
    raw = np.asarray(fractions) * size
    quotas = np.floor(raw).astype(int)
    for idx in np.argsort(-(raw - quotas))[: size - int(quotas.sum())]:
        quotas[idx] += 1
    return quotas.tolist()


def acquire_indices(
    available: np.ndarray,
    queried: np.ndarray,
    prediction_mean: np.ndarray,
    prediction_std: np.ndarray,
    tokens: np.ndarray,
    counts: np.ndarray,
    size: int,
    cfg: Mapping[str, Any],
    rng: np.random.Generator,
) -> Tuple[np.ndarray, Dict[int, str]]:
    if size >= len(available):
        return available.copy(), {int(idx): "remaining" for idx in available}
    screen_size = min(int(cfg["screen_size"]), len(available))
    screen = rng.choice(available, size=screen_size, replace=False)
    quotas = allocate_quotas(size, [
        cfg["exploit_fraction"], cfg["uncertainty_fraction"],
        cfg["diversity_fraction"], cfg["random_fraction"],
    ])
    selected: List[int] = []
    reasons: Dict[int, str] = {}

    ucb = rank01(prediction_mean[screen]) + float(cfg["ucb_beta"]) * rank01(prediction_std[screen])
    for local in np.argsort(-ucb, kind="stable"):
        idx = int(screen[local])
        if idx not in reasons and len([v for v in reasons.values() if v == "exploit_ucb"]) < quotas[0]:
            selected.append(idx); reasons[idx] = "exploit_ucb"

    for local in np.argsort(-prediction_std[screen], kind="stable"):
        idx = int(screen[local])
        if idx not in reasons and len([v for v in reasons.values() if v == "uncertainty"]) < quotas[1]:
            selected.append(idx); reasons[idx] = "uncertainty"

    remaining_screen = np.asarray([idx for idx in screen if int(idx) not in reasons], dtype=np.int64)
    diverse = hamming_diversity_select(remaining_screen, queried, tokens, quotas[2], rng)
    for idx in diverse:
        selected.append(int(idx)); reasons[int(idx)] = "diversity"

    remaining = np.asarray([idx for idx in available if int(idx) not in reasons], dtype=np.int64)
    random_pick = stratified_random_indices(remaining, counts, min(quotas[3], len(remaining)), rng)
    for idx in random_pick:
        selected.append(int(idx)); reasons[int(idx)] = "stratified_random"

    if len(selected) < size:
        remaining = np.asarray([idx for idx in available if int(idx) not in reasons], dtype=np.int64)
        fill = rng.choice(remaining, size=size - len(selected), replace=False)
        for idx in fill:
            selected.append(int(idx)); reasons[int(idx)] = "quota_fill"
    return np.asarray(selected, dtype=np.int64), reasons


class FitnessTransformer(nn.Module):
    num_teachers: int
    max_length: int
    embed_dim: int
    num_heads: int
    num_layers: int
    mlp_dim: int
    dropout: float = 0.0

    @nn.compact
    def __call__(self, tokens: jax.Array, mask: jax.Array, deterministic: bool = True) -> Dict[str, jax.Array]:
        x = nn.Embed(len(AA_ALPHABET) + 1, self.embed_dim, name="token_embedding")(tokens)
        position = self.param("position_embedding", nn.initializers.normal(0.02), (self.max_length, self.embed_dim))
        x = x + position[None, : tokens.shape[1], :]
        attention_mask = nn.make_attention_mask(mask > 0, mask > 0)
        for layer in range(self.num_layers):
            residual = x
            y = nn.LayerNorm(name=f"encoder_{layer}_ln1")(x)
            y = nn.SelfAttention(
                num_heads=self.num_heads,
                dropout_rate=self.dropout,
                name=f"encoder_{layer}_attention",
            )(y, mask=attention_mask, deterministic=deterministic)
            x = residual + y
            residual = x
            y = nn.LayerNorm(name=f"encoder_{layer}_ln2")(x)
            y = nn.Dense(self.mlp_dim, name=f"encoder_{layer}_mlp_in")(y)
            y = nn.gelu(y)
            y = nn.Dropout(self.dropout)(y, deterministic=deterministic)
            y = nn.Dense(self.embed_dim, name=f"encoder_{layer}_mlp_out")(y)
            x = residual + y
        x = nn.LayerNorm(name="encoder_final_ln")(x)
        pooled = jnp.sum(x * mask[..., None], axis=1) / jnp.maximum(jnp.sum(mask, axis=1, keepdims=True), 1.0)
        pooled = nn.Dense(self.embed_dim, name="shared_projection")(pooled)
        pooled = nn.gelu(pooled)
        return {
            "teachers": nn.Dense(self.num_teachers, name="teacher_heads")(pooled),
            "consensus": nn.Dense(1, name="consensus_head")(pooled).squeeze(-1),
            "dms": nn.Dense(1, name="dms_head")(pooled).squeeze(-1),
        }


class TrainState(train_state.TrainState):
    pass


def huber_elementwise(pred: jax.Array, target: jax.Array, delta: float = 0.1) -> jax.Array:
    error = pred - target
    absolute = jnp.abs(error)
    return jnp.where(absolute <= delta, 0.5 * error**2, delta * (absolute - 0.5 * delta))


def pairwise_ranking_loss(pred: jax.Array, target: jax.Array, min_gap: float) -> jax.Array:
    shifted_pred = jnp.roll(pred, 1)
    shifted_target = jnp.roll(target, 1)
    gap = target - shifted_target
    valid = jnp.abs(gap) >= min_gap
    signed_margin = jnp.sign(gap) * (pred - shifted_pred)
    loss = jax.nn.softplus(-signed_margin) * jnp.abs(gap)
    return jnp.sum(jnp.where(valid, loss, 0.0)) / jnp.maximum(jnp.sum(valid), 1)


def listwise_loss(pred: jax.Array, target: jax.Array, temperature: float) -> jax.Array:
    target_prob = jax.nn.softmax(target / temperature)
    top_weight = 1.0 + 4.0 * (target >= jnp.quantile(target, 0.9))
    target_prob = target_prob * top_weight
    target_prob = target_prob / jnp.sum(target_prob)
    return -jnp.sum(target_prob * jax.nn.log_softmax(pred / temperature))


def make_optimizer(params: Any, cfg: Mapping[str, Any]) -> optax.GradientTransformation:
    labels = {}
    flat = jax.tree_util.tree_flatten_with_path(params)[0]
    for path, _ in flat:
        key = "/".join(str(getattr(part, "key", part)) for part in path)
        labels[path] = "head" if any(name in key for name in ("_head", "teacher_heads")) else "encoder"
    label_tree = jax.tree_util.tree_unflatten(
        jax.tree_util.tree_structure(params),
        [labels[path] for path, _ in flat],
    )
    return optax.chain(
        optax.clip_by_global_norm(1.0),
        optax.multi_transform(
            {
                "head": optax.adamw(float(cfg["learning_rate"]), weight_decay=float(cfg["weight_decay"])),
                "encoder": optax.adamw(
                    float(cfg["learning_rate"]) * float(cfg["encoder_lr_scale"]),
                    weight_decay=float(cfg["weight_decay"]),
                ),
            },
            label_tree,
        ),
    )


def create_state(rng: jax.Array, model: FitnessTransformer, max_length: int, cfg: Mapping[str, Any]) -> TrainState:
    variables = model.init(
        {"params": rng, "dropout": rng},
        jnp.zeros((1, max_length), dtype=jnp.int32),
        jnp.ones((1, max_length), dtype=jnp.float32),
        deterministic=True,
    )
    return TrainState.create(apply_fn=model.apply, params=variables["params"], tx=make_optimizer(variables["params"], cfg))


def mask_encoder_grads(grads: Any) -> Any:
    mutable = dict(grads)
    for key in list(mutable):
        if key == "dms_head":
            continue
        mutable[key] = jax.tree_util.tree_map(jnp.zeros_like, mutable[key])
    return type(grads)(mutable) if not isinstance(grads, dict) else mutable


@jax.jit
def train_step(
    state: TrainState,
    teacher_batch: Mapping[str, jax.Array],
    dms_batch: Mapping[str, jax.Array],
    teacher_weights: jax.Array,
    loss_weights: jax.Array,
    min_pair_gap: float,
    listwise_temperature: float,
    top_dms_weight: float,
    dms_loss_weight: float,
    dropout_key: jax.Array,
    freeze_encoder: bool,
) -> Tuple[TrainState, Dict[str, jax.Array]]:
    def loss_fn(params: Any) -> Tuple[jax.Array, Dict[str, jax.Array]]:
        t_out = state.apply_fn(
            {"params": params}, teacher_batch["tokens"], teacher_batch["mask"],
            deterministic=False, rngs={"dropout": dropout_key},
        )
        targets = teacher_batch["teacher_targets"]
        valid = jnp.isfinite(targets)
        safe_targets = jnp.where(valid, targets, 0.0)
        per_head = huber_elementwise(t_out["teachers"], safe_targets) * valid
        teacher_huber = jnp.sum(per_head) / jnp.maximum(jnp.sum(valid), 1)
        sample_weights = valid * teacher_weights[None, :]
        consensus_target = jnp.sum(safe_targets * sample_weights, axis=1) / jnp.maximum(jnp.sum(sample_weights, axis=1), 1e-6)
        pair_loss = pairwise_ranking_loss(t_out["consensus"], consensus_target, min_pair_gap)
        list_loss = listwise_loss(t_out["consensus"], consensus_target, listwise_temperature)
        predicted_consensus = jnp.sum(t_out["teachers"] * teacher_weights[None, :], axis=1)
        consistency = jnp.mean((t_out["consensus"] - predicted_consensus) ** 2)
        distill_loss = (
            loss_weights[0] * teacher_huber + loss_weights[1] * pair_loss
            + loss_weights[2] * list_loss + loss_weights[3] * consistency
        )

        d_out = state.apply_fn(
            {"params": params}, dms_batch["tokens"], dms_batch["mask"],
            deterministic=False, rngs={"dropout": jax.random.fold_in(dropout_key, 1)},
        )
        d_target = dms_batch["target"]
        top_threshold = jnp.quantile(d_target, 0.9)
        d_weight = jnp.where(d_target >= top_threshold, top_dms_weight, 1.0)
        d_point = jnp.mean(huber_elementwise(d_out["dms"], d_target) * d_weight)
        d_pair = pairwise_ranking_loss(d_out["dms"], d_target, min_pair_gap)
        dms_loss = 0.5 * d_point + 0.5 * d_pair
        total = distill_loss + dms_loss_weight * dms_loss
        return total, {
            "total_loss": total, "teacher_huber": teacher_huber,
            "pairwise": pair_loss, "listwise": list_loss,
            "consistency": consistency, "dms_loss": dms_loss,
        }

    (loss, metrics), grads = jax.value_and_grad(loss_fn, has_aux=True)(state.params)
    grads = jax.lax.cond(freeze_encoder, mask_encoder_grads, lambda value: value, grads)
    return state.apply_gradients(grads=grads), metrics


@jax.jit
def predict_step(state: TrainState, tokens: jax.Array, mask: jax.Array) -> Dict[str, jax.Array]:
    return state.apply_fn({"params": state.params}, tokens, mask, deterministic=True)


def iter_batches(
    indices: np.ndarray, batch_size: int, rng: np.random.Generator, steps: Optional[int] = None
) -> Iterable[np.ndarray]:
    if len(indices) == 0:
        raise ValueError("cannot batch an empty dataset")
    shuffled = rng.permutation(indices)
    if steps is None:
        steps = max(1, math.ceil(len(indices) / batch_size))
    for step in range(steps):
        start = (step * batch_size) % len(shuffled)
        end = start + batch_size
        if end <= len(shuffled):
            yield shuffled[start:end]
        else:
            needed = end - len(shuffled)
            yield np.concatenate([shuffled[start:], shuffled[:needed]])


def predict_ensemble(
    states: Sequence[TrainState], tokens: np.ndarray, mask: np.ndarray,
    output: str, batch_size: int,
) -> Tuple[np.ndarray, np.ndarray]:
    member_predictions = []
    for state in states:
        pieces = []
        for start in range(0, len(tokens), batch_size):
            out = predict_step(state, jnp.asarray(tokens[start : start + batch_size]), jnp.asarray(mask[start : start + batch_size]))
            pieces.append(np.asarray(jax.device_get(out[output])))
        member_predictions.append(np.concatenate(pieces))
    stacked = np.stack(member_predictions)
    return stacked.mean(axis=0), stacked.std(axis=0)


def ranking_metrics(y_true: np.ndarray, y_pred: np.ndarray) -> Dict[str, float]:
    y_true = np.asarray(y_true, dtype=np.float64)
    y_pred = np.asarray(y_pred, dtype=np.float64)
    n = len(y_true)
    if n == 0:
        raise ValueError("empty evaluation set")
    result: Dict[str, float] = {}
    corr = spearmanr(y_true, y_pred).statistic if n > 1 else np.nan
    result["spearman"] = float(corr) if np.isfinite(corr) else 0.0
    for fraction, label in ((0.10, "top10"), (0.05, "top5")):
        k = max(1, int(math.ceil(n * fraction)))
        true_top = set(np.argsort(-y_true)[:k].tolist())
        pred_order = np.argsort(-y_pred)[:k]
        result[f"recall_{label}"] = len(true_top.intersection(pred_order.tolist())) / k
        relevance = rank01(y_true)
        gains = np.power(2.0, relevance[pred_order]) - 1.0
        discounts = 1.0 / np.log2(np.arange(k) + 2.0)
        dcg = float(np.sum(gains * discounts))
        ideal = np.argsort(-y_true)[:k]
        idcg = float(np.sum((np.power(2.0, relevance[ideal]) - 1.0) * discounts))
        result[f"ndcg_{label}"] = dcg / max(idcg, 1e-12)
    result["selection_score"] = 0.6 * result["ndcg_top10"] + 0.4 * result["recall_top10"]
    return result


def normalize_teacher_matrix(
    raw: np.ndarray,
) -> Tuple[np.ndarray, List[Optional[EmpiricalCDFNormalizer]]]:
    normalized = np.full(raw.shape, np.nan, dtype=np.float32)
    normalizers = []
    for column in range(raw.shape[1]):
        finite = np.isfinite(raw[:, column])
        if not finite.any():
            normalizers.append(None)
            continue
        normalizer = EmpiricalCDFNormalizer(raw[finite, column])
        normalized[:, column] = normalizer.transform(raw[:, column])
        normalizers.append(normalizer)
    return normalized, normalizers


def learn_teacher_weights(
    teacher_scores: np.ndarray, dms_scores: np.ndarray, minimum_labels: int = 40
) -> np.ndarray:
    count = teacher_scores.shape[1]
    if len(dms_scores) < minimum_labels:
        return np.full(count, 1.0 / count, dtype=np.float32)
    valid_rows = np.all(np.isfinite(teacher_scores), axis=1) & np.isfinite(dms_scores)
    if valid_rows.sum() < minimum_labels:
        return np.full(count, 1.0 / count, dtype=np.float32)
    coefficients, _ = nnls(teacher_scores[valid_rows], dms_scores[valid_rows])
    if coefficients.sum() <= 1e-8:
        return np.full(count, 1.0 / count, dtype=np.float32)
    return (coefficients / coefficients.sum()).astype(np.float32)


def save_checkpoint(output_dir: Path, round_number: int, states: Sequence[TrainState], metadata: Mapping[str, Any]) -> None:
    checkpoint_dir = output_dir / "checkpoints" / f"round_{round_number:03d}"
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    for member, state in enumerate(states):
        with (checkpoint_dir / f"member_{member}.msgpack").open("wb") as handle:
            handle.write(serialization.to_bytes(state))
    with (checkpoint_dir / "metadata.json").open("w", encoding="utf-8") as handle:
        json.dump(metadata, handle, ensure_ascii=False, indent=2)


def restore_checkpoint(output_dir: Path, states: Sequence[TrainState]) -> Tuple[int, List[TrainState], Dict[str, Any]]:
    checkpoint_root = output_dir / "checkpoints"
    candidates = sorted(checkpoint_root.glob("round_*")) if checkpoint_root.exists() else []
    if not candidates:
        raise FileNotFoundError(f"no checkpoints under {checkpoint_root}")
    latest = candidates[-1]
    restored = []
    for member, state in enumerate(states):
        with (latest / f"member_{member}.msgpack").open("rb") as handle:
            restored.append(serialization.from_bytes(state, handle.read()))
    with (latest / "metadata.json").open("r", encoding="utf-8") as handle:
        metadata = json.load(handle)
    return int(latest.name.split("_")[-1]), restored, metadata


def sample_replay_indices(
    queried: np.ndarray, round_added: np.ndarray, batch_size: int,
    rng: np.random.Generator,
) -> np.ndarray:
    latest_round = int(round_added[queried].max())
    recent = queried[round_added[queried] == latest_round]
    history = queried[round_added[queried] < latest_round]
    recent_n = min(len(recent), batch_size // 2)
    history_n = batch_size - recent_n
    recent_sample = rng.choice(recent, size=recent_n, replace=len(recent) < recent_n) if recent_n else np.empty(0, int)
    source = history if len(history) else queried
    history_sample = rng.choice(source, size=history_n, replace=len(source) < history_n)
    return np.concatenate([recent_sample, history_sample]).astype(np.int64)


def fit_round(
    states: List[TrainState], candidate_tokens: np.ndarray, candidate_mask: np.ndarray,
    queried: np.ndarray, round_added: np.ndarray, teacher_targets: np.ndarray,
    dms_tokens: np.ndarray, dms_mask: np.ndarray, dms_targets: np.ndarray,
    dms_split: DataSplit, teacher_weights: np.ndarray, cfg: Mapping[str, Any],
    rng: np.random.Generator, round_number: int,
) -> Tuple[List[TrainState], List[Dict[str, float]]]:
    training_cfg = cfg["training"]
    batch_size = int(training_cfg["batch_size"])
    steps = max(1, math.ceil(len(queried) / batch_size))
    histories: List[Dict[str, float]] = []
    new_states = []
    for member, initial_state in enumerate(states):
        state = initial_state
        best_state = state
        best_score = -np.inf
        patience = 0
        member_rng = np.random.default_rng(int(cfg["seed"]) + member * 1009 + round_number * 7919)
        key = jax.random.PRNGKey(int(cfg["seed"]) + member + round_number * 100)
        for epoch in range(int(training_cfg["max_epochs"])):
            epoch_metrics: List[Dict[str, float]] = []
            dms_batches = iter_batches(dms_split.train_indices, batch_size, member_rng, steps=steps)
            for dms_idx in dms_batches:
                teacher_idx = sample_replay_indices(queried, round_added, batch_size, member_rng)
                key, step_key = jax.random.split(key)
                state, metrics = train_step(
                    state,
                    {
                        "tokens": jnp.asarray(candidate_tokens[teacher_idx]),
                        "mask": jnp.asarray(candidate_mask[teacher_idx]),
                        "teacher_targets": jnp.asarray(teacher_targets[teacher_idx]),
                    },
                    {
                        "tokens": jnp.asarray(dms_tokens[dms_idx]),
                        "mask": jnp.asarray(dms_mask[dms_idx]),
                        "target": jnp.asarray(dms_targets[dms_idx]),
                    },
                    jnp.asarray(teacher_weights),
                    jnp.asarray([
                        training_cfg["teacher_huber_weight"], training_cfg["pairwise_weight"],
                        training_cfg["listwise_weight"], training_cfg["consistency_weight"],
                    ]),
                    float(training_cfg["min_pair_gap"]),
                    float(training_cfg["listwise_temperature"]),
                    float(training_cfg["top_dms_weight"]),
                    float(training_cfg["dms_loss_weight"]),
                    step_key,
                    epoch < int(training_cfg["head_warmup_epochs"]),
                )
                epoch_metrics.append({key_: float(value) for key_, value in jax.device_get(metrics).items()})
            val_pred, _ = predict_ensemble([state], dms_tokens[dms_split.val_indices], dms_mask[dms_split.val_indices], "dms", batch_size)
            val_metrics = ranking_metrics(dms_targets[dms_split.val_indices], val_pred)
            record = {
                "member": member, "round": round_number, "epoch": epoch,
                **{key_: float(np.mean([m[key_] for m in epoch_metrics])) for key_ in epoch_metrics[0]},
                **{f"val_{key_}": value for key_, value in val_metrics.items()},
            }
            histories.append(record)
            if val_metrics["selection_score"] > best_score + 1e-8:
                best_score, best_state, patience = val_metrics["selection_score"], state, 0
            else:
                patience += 1
            if patience >= int(training_cfg["patience"]):
                break
        new_states.append(best_state)
    return new_states, histories


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, default=float)


def run(config_path: Path, resume: bool = False, max_rounds_override: Optional[int] = None) -> Dict[str, Any]:
    cfg = load_config(config_path)
    output_dir = Path(cfg["output_dir"]).expanduser()
    if not output_dir.is_absolute():
        output_dir = (Path(__file__).resolve().parents[1] / output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    seed = int(cfg["seed"])
    rng = np.random.default_rng(seed)

    candidate_path = resolve_path(cfg["candidate"]["path"], config_path)
    dms_path = resolve_path(cfg["dms"]["path"], config_path)
    candidate_df = pd.read_csv(candidate_path)
    dms_df = pd.read_csv(dms_path)
    candidate_sequences = candidate_df[cfg["candidate"]["sequence_column"]].astype(str).tolist()
    dms_sequences = dms_df[cfg["dms"]["sequence_column"]].astype(str).tolist()
    dms_raw = pd.to_numeric(dms_df[cfg["dms"]["score_column"]], errors="raise").to_numpy(np.float32)
    if len(set(candidate_sequences)) != len(candidate_sequences):
        raise ValueError("candidate pool contains duplicate sequences")
    dms_split = make_dms_split(
        len(dms_df), int(cfg["dms"]["train_size"]), int(cfg["dms"]["val_size"]), seed
    )
    heldout_sequences = {dms_sequences[idx] for idx in np.concatenate([dms_split.val_indices, dms_split.test_indices])}
    eligible = np.asarray([idx for idx, sequence in enumerate(candidate_sequences) if sequence not in heldout_sequences], dtype=np.int64)
    if len(eligible) < 2:
        raise ValueError("fewer than two candidate sequences remain after held-out leakage filtering")

    max_length = max(max(map(len, candidate_sequences)), max(map(len, dms_sequences)))
    candidate_tokens, candidate_mask = encode_sequences(candidate_sequences, max_length)
    dms_tokens, dms_mask = encode_sequences(dms_sequences, max_length)
    wildtype = cfg["candidate"].get("wildtype_sequence") or infer_wildtype(candidate_sequences)
    counts = mutation_counts(candidate_sequences, wildtype)
    dms_normalizer = EmpiricalCDFNormalizer(dms_raw[dms_split.train_indices])
    dms_targets = dms_normalizer.transform(dms_raw)

    teachers = create_teachers(cfg["teachers"], config_path)
    cache = TeacherCache(output_dir / "teacher_cache.sqlite")
    context = {"wildtype_sequence": wildtype, "candidate_path": str(candidate_path)}
    raw_teacher_scores = np.full((len(candidate_sequences), len(teachers)), np.nan, dtype=np.float32)
    teacher_targets = raw_teacher_scores.copy()
    queried_mask = np.zeros(len(candidate_sequences), dtype=bool)
    round_added = np.full(len(candidate_sequences), -1, dtype=np.int32)

    model_cfg = cfg["model"]
    model = FitnessTransformer(
        num_teachers=len(teachers), max_length=max_length,
        embed_dim=int(model_cfg["embed_dim"]), num_heads=int(model_cfg["num_heads"]),
        num_layers=int(model_cfg["num_layers"]), mlp_dim=int(model_cfg["mlp_dim"]),
        dropout=float(model_cfg["dropout"]),
    )
    states = [
        create_state(jax.random.PRNGKey(seed + member), model, max_length, cfg["training"])
        for member in range(int(model_cfg["ensemble_size"]))
    ]

    manifest = {
        "config_hash": stable_hash(cfg),
        "candidate_hash": stable_hash(candidate_sequences),
        "dms_hash": stable_hash(dms_sequences),
        "teacher_versions": {teacher.name: teacher.version for teacher in teachers},
        "split": {
            "train": dms_split.train_indices.tolist(), "val": dms_split.val_indices.tolist(),
            "test": dms_split.test_indices.tolist(),
        },
        "heldout_candidate_overlap_removed": len(candidate_sequences) - len(eligible),
        "wildtype_sequence": wildtype,
    }
    write_json(output_dir / "run_manifest.json", manifest)

    history: List[Dict[str, Any]] = []
    start_round = 0
    if resume:
        last_round, states, metadata = restore_checkpoint(output_dir, states)
        for key in ("config_hash", "candidate_hash", "dms_hash", "teacher_versions"):
            if metadata["manifest"][key] != manifest[key]:
                raise RuntimeError(f"resume refused: {key} changed")
        queried_indices = np.asarray(metadata["queried_indices"], dtype=np.int64)
        queried_mask[queried_indices] = True
        round_added[np.asarray(metadata["round_added_indices"], dtype=np.int64)] = np.asarray(metadata["round_added_values"], dtype=np.int32)
        scores, _ = query_teachers([candidate_sequences[idx] for idx in queried_indices], teachers, cache, context)
        raw_teacher_scores[queried_indices] = scores
        history = metadata.get("round_history", [])
        start_round = last_round + 1

    online_cfg = cfg["online"]
    max_queries = min(len(eligible), max(2, int(math.ceil(len(eligible) * float(online_cfg["max_query_fraction"])))))
    cold_start = min(
        int(online_cfg["cold_start_size"]),
        max(2, int(math.ceil(len(eligible) * float(online_cfg["cold_start_fraction"])))),
        max_queries,
    )
    rounds = int(online_cfg["rounds"]) if max_rounds_override is None else min(int(online_cfg["rounds"]), max_rounds_override)
    stagnant = 0
    best_round_score = max([item.get("val_selection_score", -np.inf) for item in history], default=-np.inf)
    training_history_path = output_dir / "training_history.jsonl"
    query_history_path = output_dir / "query_history.jsonl"

    try:
        for round_number in range(start_round, rounds + 1):
            queried = np.flatnonzero(queried_mask)
            available = eligible[~queried_mask[eligible]]
            if round_number == 0 and len(queried) == 0:
                selected = stratified_random_indices(available, counts, cold_start, rng)
                reasons = {int(idx): "cold_start" for idx in selected}
            else:
                remaining_budget = max_queries - len(queried)
                if remaining_budget <= 0 or len(available) == 0:
                    break
                mean_all, std_all = predict_ensemble(
                    states, candidate_tokens, candidate_mask, "consensus", int(cfg["training"]["batch_size"])
                )
                selected, reasons = acquire_indices(
                    available, queried, mean_all, std_all, candidate_tokens,
                    counts, min(int(online_cfg["queries_per_round"]), remaining_budget),
                    online_cfg, rng,
                )

            selected_sequences = [candidate_sequences[idx] for idx in selected]
            queried_scores, audit = query_teachers(selected_sequences, teachers, cache, context)
            raw_teacher_scores[selected] = queried_scores
            queried_mask[selected] = True
            round_added[selected] = round_number
            queried = np.flatnonzero(queried_mask)
            normalized_queried, normalizers = normalize_teacher_matrix(raw_teacher_scores[queried])
            teacher_targets[:] = np.nan
            teacher_targets[queried] = normalized_queried

            # Learn teacher weights only when enough real training labels exist. With fewer
            # than 40 labels, equal weights avoid an unstable fit and unnecessary queries.
            dms_teacher_audit: List[Dict[str, Any]] = []
            dms_teacher_raw = np.full((len(dms_split.train_indices), len(teachers)), np.nan, dtype=np.float32)
            if len(dms_split.train_indices) >= 40:
                dms_train_sequences = [dms_sequences[idx] for idx in dms_split.train_indices]
                dms_teacher_raw, dms_teacher_audit = query_teachers(dms_train_sequences, teachers, cache, context)
                normalized_columns = []
                for col, normalizer in enumerate(normalizers):
                    if normalizer is None:
                        normalized_columns.append(np.full(len(dms_train_sequences), np.nan, dtype=np.float32))
                    else:
                        normalized_columns.append(normalizer.transform(dms_teacher_raw[:, col]))
                dms_teacher_normalized = np.column_stack(normalized_columns)
                teacher_weights = learn_teacher_weights(
                    dms_teacher_normalized, dms_targets[dms_split.train_indices], minimum_labels=40
                )
            else:
                teacher_weights = np.full(len(teachers), 1.0 / len(teachers), dtype=np.float32)

            states, epoch_history = fit_round(
                states, candidate_tokens, candidate_mask, queried, round_added,
                teacher_targets, dms_tokens, dms_mask, dms_targets, dms_split,
                teacher_weights, cfg, rng, round_number,
            )
            with training_history_path.open("a", encoding="utf-8") as handle:
                for record in epoch_history:
                    handle.write(json.dumps(record, ensure_ascii=False) + "\n")

            val_mean, val_std = predict_ensemble(
                states, dms_tokens[dms_split.val_indices], dms_mask[dms_split.val_indices],
                "dms", int(cfg["training"]["batch_size"]),
            )
            test_mean, test_std = predict_ensemble(
                states, dms_tokens[dms_split.test_indices], dms_mask[dms_split.test_indices],
                "dms", int(cfg["training"]["batch_size"]),
            )
            val_metrics = ranking_metrics(dms_targets[dms_split.val_indices], val_mean)
            test_metrics = ranking_metrics(dms_targets[dms_split.test_indices], test_mean)
            disagreement = np.nanstd(raw_teacher_scores[selected], axis=1)
            round_record = {
                "round": round_number, "queried_total": len(queried),
                "queried_this_round": len(selected), "teacher_weights": teacher_weights.tolist(),
                **{f"val_{key}": value for key, value in val_metrics.items()},
                **{f"test_{key}": value for key, value in test_metrics.items()},
                "mean_teacher_disagreement": float(np.nanmean(disagreement)),
            }
            history.append(round_record)
            with query_history_path.open("a", encoding="utf-8") as handle:
                for idx, score_row, disagreement_value in zip(selected, queried_scores, disagreement):
                    handle.write(json.dumps({
                        "round": round_number, "candidate_index": int(idx),
                        "sequence_hash": sequence_hash(candidate_sequences[int(idx)]),
                        "mutation_count": int(counts[int(idx)]), "reason": reasons[int(idx)],
                        "teacher_scores": {teachers[col].name: float(value) if np.isfinite(value) else None for col, value in enumerate(score_row)},
                        "teacher_disagreement": float(disagreement_value),
                    }, ensure_ascii=False) + "\n")

            metadata = {
                "manifest": manifest, "round": round_number,
                "queried_indices": queried.tolist(),
                "round_added_indices": queried.tolist(),
                "round_added_values": round_added[queried].tolist(),
                "teacher_weights": teacher_weights.tolist(),
                "teacher_normalizers": [None if item is None else item.to_dict() for item in normalizers],
                "dms_normalizer": dms_normalizer.to_dict(),
                "round_history": history,
                "teacher_audit": audit, "dms_teacher_audit": dms_teacher_audit,
            }
            save_checkpoint(output_dir, round_number, states, metadata)
            write_json(output_dir / "round_metrics.json", history)
            print(
                f"Round {round_number}: queried={len(queried)}/{max_queries}, "
                f"val_score={val_metrics['selection_score']:.4f}, "
                f"test_recall@10%={test_metrics['recall_top10']:.4f}, "
                f"test_ndcg@10%={test_metrics['ndcg_top10']:.4f}"
            )
            improvement = val_metrics["selection_score"] - best_round_score
            if improvement >= float(online_cfg["min_round_improvement"]):
                best_round_score, stagnant = val_metrics["selection_score"], 0
            else:
                stagnant += 1
            if stagnant >= int(online_cfg["stagnant_rounds"]):
                print(f"Stopping online queries after {stagnant} stagnant rounds")
                break

        all_mean, all_std = predict_ensemble(states, dms_tokens, dms_mask, "dms", int(cfg["training"]["batch_size"]))
        prediction_df = dms_df.copy()
        prediction_df["fitness_pred"] = all_mean
        prediction_df["fitness_percentile"] = rank01(all_mean)
        prediction_df["uncertainty"] = all_std
        prediction_df["teacher_disagreement"] = np.nan
        if len(dms_split.train_indices) >= 40:
            prediction_df.loc[dms_split.train_indices, "teacher_disagreement"] = np.nanstd(dms_teacher_raw, axis=1)
        prediction_df["split"] = "test"
        prediction_df.loc[dms_split.train_indices, "split"] = "train"
        prediction_df.loc[dms_split.val_indices, "split"] = "validation"
        prediction_df.to_csv(output_dir / "predictions.csv", index=False)
        final = {"manifest": manifest, "rounds": history, "output_dir": str(output_dir)}
        write_json(output_dir / "final_result.json", final)
        return final
    finally:
        cache.close()


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--max-rounds", type=int, default=None, help="Testing override; includes cold-start round 0")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    run(args.config.resolve(), resume=args.resume, max_rounds_override=args.max_rounds)
    return 0


if __name__ == "__main__":
    sys.exit(main())
