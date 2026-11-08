#!/usr/bin/env python3
"""DPO-fine-tune mut_model on preference pairs covering all experimental rows."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import pickle
import sys
import time


AA = set("ACDEFGHIKLMNPQRSTVWY")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_path(value: str | None, default: Path) -> Path:
    return Path(value).expanduser().resolve() if value else default.resolve()


def to_jsonable(value):
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(item) for item in value]
    if hasattr(value, "item"):
        return value.item()
    return value


def validate_training_csv(
    path: Path, score_column: str, reference_column: str
) -> tuple[object, int, int]:
    import numpy as np
    import pandas as pd

    if score_column == reference_column:
        raise ValueError("DPO reference column must differ from the experimental score column")
    frame = pd.read_csv(path)
    required = {"mutated_sequence", score_column, reference_column}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"Missing required columns in {path}: {', '.join(missing)}")
    if frame.empty:
        raise ValueError(f"No experimental rows found in {path}")
    if frame[list(required)].isna().any().any():
        raise ValueError(f"Null values found in required columns of {path}")

    scores = pd.to_numeric(frame[score_column], errors="raise").to_numpy(dtype=float)
    references = pd.to_numeric(frame[reference_column], errors="raise").to_numpy(dtype=float)
    if not np.isfinite(scores).all() or not np.isfinite(references).all():
        raise ValueError("Experimental and DPO reference scores must be finite")
    if float(scores.max()) == float(scores.min()):
        raise ValueError(f"{score_column} is constant")
    if float(references.max()) == float(references.min()):
        raise ValueError(f"{reference_column} is constant")
    if np.allclose(scores, references):
        raise ValueError(
            "DPO reference values duplicate experimental labels; provide independent teacher scores"
        )

    sequences = frame["mutated_sequence"].astype(str).str.strip().str.upper().tolist()
    lengths = {len(sequence) for sequence in sequences}
    if len(lengths) != 1 or 0 in lengths:
        raise ValueError("All mutated_sequence values must have the same non-zero length")
    for index, sequence in enumerate(sequences):
        invalid = sorted(set(sequence) - AA)
        if invalid:
            raise ValueError(
                f"Row {index} contains unsupported amino acids: {''.join(invalid)}"
            )
    frame = frame.copy()
    frame["mutated_sequence"] = sequences
    frame[score_column] = scores
    frame[reference_column] = references
    return frame, len(frame), lengths.pop()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "DPO-fine-tune mut_model using preference pairs that cover all experimental rows"
        )
    )
    parser.add_argument("--dms-id", required=True)
    parser.add_argument("--reference-column", required=True)
    parser.add_argument("--mut-model-dir", default=os.environ.get("MUT_MODEL_HOME"), required=not bool(os.environ.get("MUT_MODEL_HOME")))
    parser.add_argument("--input", help="Defaults to <mut-model-dir>/data/<DMS_ID>.csv")
    parser.add_argument(
        "--pretrain-weight",
        help="Defaults to <mut-model-dir>/pretrain_weight/pretrain-<DMS_ID>-random.pkl",
    )
    parser.add_argument(
        "--output-weight",
        help="Defaults to <mut-model-dir>/finetune_weight/finetune-dpo-full-<DMS_ID>-random.pkl",
    )
    parser.add_argument("--metadata-output", help="Defaults to <output-weight>.json")
    parser.add_argument("--score-column", default="DMS_score")
    parser.add_argument("--epochs", type=int, default=150)
    parser.add_argument("--steps-per-epoch", type=int, default=50)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--n-pairs", type=int, default=10000)
    parser.add_argument("--preference-percentile", type=float, default=90.0)
    parser.add_argument("--beta", type=float, default=0.1)
    parser.add_argument("--global-loss-weight", type=float, default=0.3)
    parser.add_argument("--learning-rate", type=float, default=2e-6)
    parser.add_argument("--warmup-epochs", type=int, default=3)
    parser.add_argument("--patience", type=int, default=8)
    parser.add_argument("--min-delta", type=float, default=0.002)
    parser.add_argument("--embed-dim", type=int, default=512)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--cuda-visible-devices")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    for name in ("epochs", "steps_per_epoch", "batch_size", "n_pairs"):
        if getattr(args, name) <= 0:
            raise ValueError(f"--{name.replace('_', '-')} must be positive")
    if not 0 < args.preference_percentile < 100:
        raise ValueError("--preference-percentile must be between 0 and 100")
    if args.learning_rate <= 0 or args.beta <= 0:
        raise ValueError("--learning-rate and --beta must be positive")
    if args.warmup_epochs < 0 or args.warmup_epochs >= args.epochs:
        raise ValueError("--warmup-epochs must be non-negative and smaller than --epochs")

    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    os.environ.setdefault("XLA_PYTHON_CLIENT_PREALLOCATE", "false")
    if args.cuda_visible_devices is not None:
        os.environ["CUDA_VISIBLE_DEVICES"] = args.cuda_visible_devices

    mut_model_dir = Path(args.mut_model_dir).expanduser().resolve()
    input_path = resolve_path(args.input, mut_model_dir / "data" / f"{args.dms_id}.csv")
    pretrain_path = resolve_path(
        args.pretrain_weight,
        mut_model_dir / "pretrain_weight" / f"pretrain-{args.dms_id}-random.pkl",
    )
    output_path = resolve_path(
        args.output_weight,
        mut_model_dir / "finetune_weight" / f"finetune-dpo-full-{args.dms_id}-random.pkl",
    )
    metadata_path = resolve_path(args.metadata_output, Path(str(output_path) + ".json"))
    for path, label in ((input_path, "experimental CSV"), (pretrain_path, "pretrain weight")):
        if not path.is_file() or path.stat().st_size == 0:
            raise FileNotFoundError(f"Missing or empty {label}: {path}")

    frame, row_count, sequence_length = validate_training_csv(
        input_path, args.score_column, args.reference_column
    )

    import numpy as np

    original_asarray = np.asarray

    def patched_asarray(a, dtype=None, order=None, *, like=None, copy=None, device=None):
        return original_asarray(a, dtype=dtype, order=order)

    np.asarray = patched_asarray
    np.random.seed(args.seed)

    import grain.python as grain
    import jax
    import jax.numpy as jnp
    from flax import nnx
    from flax.nnx import bridge

    sys.path.insert(0, str(mut_model_dir / "model_run"))
    from models.model_direct import get_model_att_with_sequence_logits
    from models.pair_dataset import PreferenceDataGenerator
    from models.trainer import SequenceDPOTrainer

    class FullDataSource(grain.RandomAccessDataSource):
        def __init__(self):
            self.df = frame.copy()
            self.df["DMS_score"] = frame[args.score_column]
            values = frame[args.score_column].to_numpy(dtype=float)
            value_min, value_max = float(values.min()), float(values.max())
            self.targets = (values - value_min) / (value_max - value_min) * 2.0 - 1.0
            self.references = frame[args.reference_column].to_numpy(dtype=float)
            self.sequences = frame["mutated_sequence"].tolist()
            self.aa_to_idx = {aa: index for index, aa in enumerate("ACDEFGHIKLMNPQRSTVWY")}

        def __len__(self):
            return len(self.sequences)

        def __getitem__(self, index):
            sequence = self.sequences[index]
            onehot = np.zeros((len(sequence), 20), dtype="float32")
            for position, aa in enumerate(sequence):
                onehot[position, self.aa_to_idx[aa]] = 1.0
            return {
                "y": jnp.asarray(self.targets[index]),
                "sequence_onehot": jnp.asarray(onehot),
                "sequence": sequence,
                "idx": index,
                "saprot_logits": jnp.asarray(self.references[index]),
            }

    source = FullDataSource()
    generator = PreferenceDataGenerator(source)
    pairs = generator.create_preference_pairs(
        n_pairs=max(args.n_pairs, row_count),
        strategy="threshold",
        percentile=args.preference_percentile,
    )
    if not pairs:
        raise RuntimeError("No DPO preference pairs were generated")
    covered_indices = {
        int(pair[key])
        for pair in pairs
        for key in ("preferred_idx", "rejected_idx")
    }
    expected_indices = set(range(row_count))
    if covered_indices != expected_indices:
        missing = sorted(expected_indices - covered_indices)
        raise RuntimeError(f"Preference pairs do not cover all experimental rows: {missing}")

    class BatchSizedGenerator:
        def create_batch(self):
            return generator.create_batch(batch_size=args.batch_size)

    x_init = jax.random.randint(
        jax.random.PRNGKey(args.seed),
        (min(args.batch_size, row_count), sequence_length, 20),
        0,
        1,
    )
    raw_model = get_model_att_with_sequence_logits(
        sequence_length=sequence_length,
        vocab_size=20,
        task_type="both",
        use_temperature=True,
        use_sequence_embedding=True,
        embed_dim=args.embed_dim,
    )
    model = bridge.ToNNX(raw_model, rngs=nnx.Rngs(args.seed))
    model = bridge.lazy_init(model, x_init)
    graph, _ = nnx.split(model)
    with pretrain_path.open("rb") as handle:
        pretrain_state = pickle.load(handle)
    model = nnx.merge(graph, jax.device_put(pretrain_state))

    trainer = SequenceDPOTrainer(
        model=model,
        beta=args.beta,
        global_loss_weight=args.global_loss_weight,
        learning_rate=args.learning_rate,
        warmup_epochs=args.warmup_epochs,
        total_epochs=args.epochs,
        seed=args.seed,
    )
    started_at = time.time()
    training_result = trainer.train(
        BatchSizedGenerator(),
        num_epochs=args.epochs,
        steps_per_epoch=args.steps_per_epoch,
        patience=args.patience,
        min_delta=args.min_delta,
        monitor_metric="total_loss",
    )

    _, final_state = nnx.split(model)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_weight = output_path.with_name(output_path.name + ".tmp")
    with temporary_weight.open("wb") as handle:
        pickle.dump(final_state, handle)
    os.replace(temporary_weight, output_path)

    result = to_jsonable(training_result)
    final_loss = float(result["epoch_losses"][-1])
    if not math.isfinite(final_loss):
        raise RuntimeError("DPO training produced a non-finite final loss")
    metadata = {
        "schema_version": "mut-model-full-data-dpo-finetune/v1",
        "dms_id": args.dms_id,
        "training_mode": "dpo_all_experimental_rows_no_split",
        "experimental_csv": str(input_path),
        "experimental_csv_sha256": sha256_file(input_path),
        "experimental_row_count": row_count,
        "preference_covered_row_count": len(covered_indices),
        "preference_pair_count": len(pairs),
        "preference_percentile": args.preference_percentile,
        "score_column": args.score_column,
        "reference_column": args.reference_column,
        "pretrain_weight": str(pretrain_path),
        "pretrain_weight_sha256": sha256_file(pretrain_path),
        "output_weight": str(output_path),
        "output_weight_sha256": sha256_file(output_path),
        "epochs_requested": args.epochs,
        "epochs_completed": int(result["total_epochs"]),
        "steps_per_epoch": args.steps_per_epoch,
        "batch_size": args.batch_size,
        "beta": args.beta,
        "global_loss_weight": args.global_loss_weight,
        "learning_rate": args.learning_rate,
        "warmup_epochs": args.warmup_epochs,
        "patience": args.patience,
        "min_delta": args.min_delta,
        "embed_dim": args.embed_dim,
        "seed": args.seed,
        "device": str(jax.devices()[0]),
        "best_training_loss": float(result["best_loss"]),
        "final_training_loss": final_loss,
        "early_stopped_on_training_loss": bool(result["early_stopped"]),
        "elapsed_seconds": round(time.time() - started_at, 3),
    }
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(metadata, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
