#!/usr/bin/env python3
"""Fine-tune mut_model on every experimental row and save one production state."""

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


def validate_training_csv(path: Path, score_column: str) -> tuple[int, int]:
    import numpy as np
    import pandas as pd

    frame = pd.read_csv(path)
    required = {"mutated_sequence", score_column}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"Missing required columns in {path}: {', '.join(missing)}")
    if frame.empty:
        raise ValueError(f"No experimental rows found in {path}")
    if frame[list(required)].isna().any().any():
        raise ValueError(f"Null values found in required columns of {path}")

    scores = pd.to_numeric(frame[score_column], errors="raise").to_numpy(dtype=float)
    if not np.isfinite(scores).all():
        raise ValueError(f"Non-finite values found in {score_column}")
    if float(scores.max()) == float(scores.min()):
        raise ValueError(f"{score_column} is constant; supervised fine-tuning is not meaningful")

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
    return len(frame), lengths.pop()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Fine-tune mut_model on all experimental rows without train/test or CV splits"
        )
    )
    parser.add_argument("--dms-id", required=True)
    parser.add_argument("--mut-model-dir", default=os.environ.get("MUT_MODEL_HOME"), required=not bool(os.environ.get("MUT_MODEL_HOME")))
    parser.add_argument("--input", help="Defaults to <mut-model-dir>/data/<DMS_ID>.csv")
    parser.add_argument(
        "--pretrain-weight",
        help="Defaults to <mut-model-dir>/pretrain_weight/pretrain-<DMS_ID>-random.pkl",
    )
    parser.add_argument(
        "--output-weight",
        help="Defaults to <mut-model-dir>/finetune_weight/finetune-full-<DMS_ID>-random.pkl",
    )
    parser.add_argument(
        "--metadata-output",
        help="Defaults to <output-weight>.json",
    )
    parser.add_argument("--score-column", default="DMS_score")
    parser.add_argument("--epochs", type=int, default=130)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--learning-rate", type=float, default=1e-4)
    parser.add_argument("--embed-dim", type=int, default=512)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--cuda-visible-devices")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.epochs <= 0:
        raise ValueError("--epochs must be positive")
    if args.batch_size <= 0:
        raise ValueError("--batch-size must be positive")
    if args.learning_rate <= 0:
        raise ValueError("--learning-rate must be positive")

    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
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
        mut_model_dir / "finetune_weight" / f"finetune-full-{args.dms_id}-random.pkl",
    )
    metadata_path = resolve_path(
        args.metadata_output,
        Path(str(output_path) + ".json"),
    )
    for path, label in ((input_path, "experimental CSV"), (pretrain_path, "pretrain weight")):
        if not path.is_file() or path.stat().st_size == 0:
            raise FileNotFoundError(f"Missing or empty {label}: {path}")

    row_count, sequence_length = validate_training_csv(input_path, args.score_column)

    import numpy as np

    original_asarray = np.asarray

    def patched_asarray(a, dtype=None, order=None, *, like=None, copy=None, device=None):
        return original_asarray(a, dtype=dtype, order=order)

    np.asarray = patched_asarray

    import grain.python as grain
    import jax
    import jax.numpy as jnp
    import optax
    from flax import nnx
    from flax.nnx import bridge

    sys.path.insert(0, str(mut_model_dir / "model_run"))
    from models.dataloder_direct import MySource_seqs
    from models.model_direct import get_model_att_with_sequence_logits

    source = MySource_seqs(str(input_path), args.score_column)
    if len(source) != row_count:
        raise RuntimeError(
            f"Data loader row count {len(source)} does not match validated row count {row_count}"
        )
    train_dataset = (
        grain.MapDataset.source(source)
        .shuffle(seed=args.seed)
        .batch(batch_size=args.batch_size)
    )

    init_batch_size = min(args.batch_size, row_count)
    init_key = jax.random.PRNGKey(args.seed)
    x_init = jax.random.randint(
        init_key, (init_batch_size, sequence_length, 20), 0, 1
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
    optimizer = nnx.Optimizer(model, optax.adam(args.learning_rate))

    @nnx.jit
    def train_step(model_new, optimizer_new, x, y):
        def loss_fn(candidate_model):
            prediction = candidate_model(x)["global"]
            return jnp.mean((y - prediction) ** 2)

        loss, grads = nnx.value_and_grad(loss_fn)(model_new)
        optimizer_new.update(grads)
        return loss

    started_at = time.time()
    epoch_losses: list[float] = []
    for epoch in range(args.epochs):
        total_loss = 0.0
        batch_count = 0
        rows_seen = 0
        for batch in train_dataset:
            loss = train_step(
                model,
                optimizer,
                batch["sequence_onehot"],
                batch["y"],
            )
            current_batch_size = int(batch["sequence_onehot"].shape[0])
            total_loss += float(jax.device_get(loss)) * current_batch_size
            rows_seen += current_batch_size
            batch_count += 1
        if rows_seen != row_count:
            raise RuntimeError(
                f"Epoch {epoch + 1} consumed {rows_seen} rows; expected all {row_count}"
            )
        mean_loss = total_loss / rows_seen
        if not math.isfinite(mean_loss):
            raise RuntimeError(f"Non-finite training loss at epoch {epoch + 1}")
        epoch_losses.append(mean_loss)
        print(
            f"Epoch {epoch + 1:3d}/{args.epochs}: loss={mean_loss:.6f}, "
            f"rows={rows_seen}, batches={batch_count}"
        )

    _, final_state = nnx.split(model)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_weight = output_path.with_name(output_path.name + ".tmp")
    with temporary_weight.open("wb") as handle:
        pickle.dump(final_state, handle)
    os.replace(temporary_weight, output_path)

    metadata = {
        "schema_version": "mut-model-full-data-finetune/v1",
        "dms_id": args.dms_id,
        "training_mode": "all_experimental_rows_no_split",
        "experimental_csv": str(input_path),
        "experimental_csv_sha256": sha256_file(input_path),
        "experimental_row_count": row_count,
        "sequence_length": sequence_length,
        "score_column": args.score_column,
        "pretrain_weight": str(pretrain_path),
        "pretrain_weight_sha256": sha256_file(pretrain_path),
        "output_weight": str(output_path),
        "output_weight_sha256": sha256_file(output_path),
        "epochs": args.epochs,
        "batch_size": args.batch_size,
        "learning_rate": args.learning_rate,
        "embed_dim": args.embed_dim,
        "seed": args.seed,
        "device": str(jax.devices()[0]),
        "first_epoch_loss": epoch_losses[0],
        "final_epoch_loss": epoch_losses[-1],
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
