#!/usr/bin/env python3
"""Data preparation and scoring helpers for the configured mut_model checkout."""

from __future__ import annotations

import argparse
import csv
import itertools
import json
import os
from pathlib import Path
import pickle
import random
import re
import sys
from typing import Iterable

AA = "ACDEFGHIKLMNPQRSTVWY"
AA_SET = set(AA)
MUT_RE = re.compile(r"^([ACDEFGHIKLMNPQRSTVWY])([1-9][0-9]*)([ACDEFGHIKLMNPQRSTVWY])$")


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError(f"Empty CSV: {path}")
        return [dict(row) for row in reader]


def write_csv(path: Path, rows: list[dict[str, object]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def clean_sequence(sequence: str) -> str:
    seq = re.sub(r"\s+", "", sequence).upper()
    invalid = sorted(set(seq) - AA_SET)
    if invalid:
        raise ValueError(f"Sequence contains unsupported amino acids: {''.join(invalid)}")
    return seq


def mutation_from_row(row: dict[str, str], column: str | None = None) -> str | None:
    columns = [column] if column else ["mutation", "mutant"]
    for col in columns:
        if col and row.get(col):
            return row[col].strip().upper()
    return None


def parse_mutation_group(mutation: str) -> list[tuple[str, int, str]]:
    parsed: list[tuple[str, int, str]] = []
    seen: set[int] = set()
    for item in mutation.split(":"):
        match = MUT_RE.match(item.strip().upper())
        if not match:
            raise ValueError(f"Invalid mutation {mutation!r}; expected A23V or A23V:G57D")
        wt, pos_text, mt = match.groups()
        pos = int(pos_text)
        if pos in seen:
            raise ValueError(f"{mutation}: duplicate position {pos}")
        seen.add(pos)
        parsed.append((wt, pos, mt))
    return parsed


def mutate_sequence(sequence: str, mutation: str) -> str:
    mutated = list(sequence)
    for wt, pos, mt in parse_mutation_group(mutation):
        if pos > len(sequence):
            raise ValueError(f"{mutation}: position {pos} exceeds sequence length {len(sequence)}")
        observed = sequence[pos - 1]
        if observed != wt:
            raise ValueError(f"{mutation}: expected {wt}{pos}, observed {observed}{pos}")
        mutated[pos - 1] = mt
    return "".join(mutated)


def sequence_from_row(sequence: str | None, row: dict[str, str], mutation_column: str | None = None) -> tuple[str | None, str]:
    mutation = mutation_from_row(row, mutation_column)
    if row.get("mutated_sequence"):
        return mutation, clean_sequence(row["mutated_sequence"])
    if sequence and mutation:
        return mutation, mutate_sequence(sequence, mutation)
    raise ValueError("Each row needs mutated_sequence or a mutation column plus --sequence")


def command_generate_library(args: argparse.Namespace) -> None:
    sequence = clean_sequence(args.sequence)
    rng = random.Random(args.seed)
    seen: set[str] = set()
    rows: list[dict[str, object]] = []

    def add_mutation(parts: Iterable[str]) -> None:
        mutation = ":".join(parts)
        if mutation in seen:
            return
        seen.add(mutation)
        rows.append({"mutation": mutation, "mutated_sequence": mutate_sequence(sequence, mutation)})

    for pos, wt in enumerate(sequence, start=1):
        for mt in AA:
            if mt != wt:
                add_mutation([f"{wt}{pos}{mt}"])

    if args.random_multisite > 0 and args.max_sites > 1:
        positions = list(range(1, len(sequence) + 1))
        attempts = 0
        max_attempts = args.random_multisite * 50
        while len(rows) < len(sequence) * 19 + args.random_multisite and attempts < max_attempts:
            attempts += 1
            n_sites = rng.randint(2, args.max_sites)
            sampled = sorted(rng.sample(positions, min(n_sites, len(positions))))
            parts = []
            for pos in sampled:
                wt = sequence[pos - 1]
                choices = [aa for aa in AA if aa != wt]
                parts.append(f"{wt}{pos}{rng.choice(choices)}")
            add_mutation(parts)

    write_csv(Path(args.output), rows, ["mutation", "mutated_sequence"])
    print(f"Wrote {len(rows)} mutants to {args.output}")


def command_prepare_pretrain(args: argparse.Namespace) -> None:
    sequence = clean_sequence(args.sequence) if args.sequence else None
    rows = []
    for row in read_csv(Path(args.input)):
        mutation, mutated = sequence_from_row(sequence, row, args.mutation_column)
        out = {"mutated_sequence": mutated}
        if mutation:
            out["mutation"] = mutation
        for col in args.y_columns:
            if row.get(col) in {None, ""}:
                raise ValueError(f"Missing pseudo-label column {col!r}")
            out[col] = float(row[col])
        rows.append(out)
    fields = ["mutation", "mutated_sequence"] if any("mutation" in row for row in rows) else ["mutated_sequence"]
    fields.extend(args.y_columns)
    write_csv(Path(args.output), rows, fields)
    print(f"Wrote {len(rows)} pretraining rows to {args.output}")


def command_prepare_finetune(args: argparse.Namespace) -> None:
    sequence = clean_sequence(args.sequence) if args.sequence else None
    reference_columns = args.reference_columns or []
    rows = []
    for row in read_csv(Path(args.input)):
        mutation, mutated = sequence_from_row(sequence, row, args.mutation_column)
        if row.get(args.score_column) in {None, ""}:
            raise ValueError(f"Missing score column {args.score_column!r}")
        out = {"mutated_sequence": mutated, "DMS_score": float(row[args.score_column])}
        for column in reference_columns:
            if row.get(column) in {None, ""}:
                raise ValueError(f"Missing DPO reference column {column!r}")
            out[column] = float(row[column])
        if mutation:
            out["mutation"] = mutation
        rows.append(out)
    fields = ["mutation", "mutated_sequence", "DMS_score"] if any("mutation" in row for row in rows) else ["mutated_sequence", "DMS_score"]
    fields.extend(column for column in reference_columns if column not in fields)
    write_csv(Path(args.output), rows, fields)
    print(f"Wrote {len(rows)} finetuning rows to {args.output}")

def onehot_batch(sequences: list[str]):
    import numpy as np

    aa_to_idx = {aa: i for i, aa in enumerate(AA)}
    seq_len = len(sequences[0])
    arr = np.zeros((len(sequences), seq_len, len(AA)), dtype="float32")
    for row_idx, seq in enumerate(sequences):
        if len(seq) != seq_len:
            raise ValueError("All sequences must have the same length for one model weight")
        for pos, aa in enumerate(seq):
            if aa not in aa_to_idx:
                raise ValueError(f"Unsupported amino acid {aa!r} in sequence")
            arr[row_idx, pos, aa_to_idx[aa]] = 1.0
    return arr


def command_score_library(args: argparse.Namespace) -> None:
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

    import numpy as np

    # JAX 0.7.x may call np.asarray(copy=...) while some envs still have NumPy 1.x.
    original_asarray = np.asarray

    def patched_asarray(a, dtype=None, order=None, *, like=None, copy=None, device=None):
        return original_asarray(a, dtype=dtype, order=order)

    np.asarray = patched_asarray

    import jax
    import jax.numpy as jnp
    from flax import nnx
    from flax.nnx import bridge

    mut_model_dir = Path(args.mut_model_dir).resolve()
    sys.path.insert(0, str(mut_model_dir / "model_run"))
    from models.model_direct import get_model_att_with_sequence_logits

    rows = read_csv(Path(args.input))
    sequences = [clean_sequence(row["mutated_sequence"]) for row in rows]
    if not sequences:
        raise ValueError(f"No candidate rows found in {args.input}")
    seq_len = len(sequences[0])
    x_init = jax.random.randint(jax.random.PRNGKey(42), (1, seq_len, len(AA)), 0, 1)
    model_raw = get_model_att_with_sequence_logits(
        sequence_length=seq_len,
        vocab_size=len(AA),
        task_type="both",
        use_temperature=True,
        use_sequence_embedding=True,
        embed_dim=args.embed_dim,
    )
    model = bridge.ToNNX(model_raw, rngs=nnx.Rngs(42))
    model = bridge.lazy_init(model, x_init)
    graph, _ = nnx.split(model)
    with Path(args.weight).open("rb") as handle:
        state = pickle.load(handle)
    model = nnx.merge(graph, jax.device_put(state))

    scores: list[float] = []
    for start in range(0, len(sequences), args.batch_size):
        batch = sequences[start : start + args.batch_size]
        x = jnp.array(onehot_batch(batch))
        pred = model(x)["global"]
        scores.extend(float(value) for value in np.array(jax.device_get(pred)).reshape(-1))

    output_rows = []
    for row, score in zip(rows, scores):
        out = dict(row)
        out[args.score_column] = score
        output_rows.append(out)
    fields = list(rows[0].keys())
    if args.score_column not in fields:
        fields.append(args.score_column)
    write_csv(Path(args.output), output_rows, fields)
    print(f"Wrote {len(output_rows)} scored rows to {args.output}")


def scored_rows_quality(rows: list[dict[str, str]], *, mutation_column: str, score_column: str) -> dict[str, object]:
    mutations = [str(row.get(mutation_column, "")).strip().upper() for row in rows]
    nonempty_mutations = [item for item in mutations if item]
    missing_mutation = len(rows) - len(nonempty_mutations)
    duplicate_count = len(nonempty_mutations) - len(set(nonempty_mutations))
    scored = 0
    invalid_scores = 0
    for row in rows:
        value = row.get(score_column)
        if value in {None, ""}:
            continue
        try:
            float(value)
            scored += 1
        except ValueError:
            invalid_scores += 1
    coverage = scored / len(rows) if rows else 0.0
    issues = []
    if not rows:
        issues.append("no_rows")
    if missing_mutation:
        issues.append("missing_mutation_values")
    if duplicate_count:
        issues.append("duplicate_mutations")
    if invalid_scores:
        issues.append("invalid_score_values")
    if coverage < 0.95:
        issues.append("score_coverage_below_95_percent")
    return {
        "row_count": len(rows),
        "scored_count": scored,
        "score_coverage": round(coverage, 6),
        "missing_mutation_count": missing_mutation,
        "duplicate_mutation_count": duplicate_count,
        "invalid_score_count": invalid_scores,
        "status": "pass" if not issues else "blocked",
        "issues": issues,
    }


def command_quality_report(args: argparse.Namespace) -> None:
    rows = read_csv(Path(args.input))
    report = {
        "schema_version": "mut-model-supervised-quality/v1",
        "input": str(args.input),
        "mutation_column": args.mutation_column,
        "score_column": args.score_column,
        **scored_rows_quality(rows, mutation_column=args.mutation_column, score_column=args.score_column),
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


def command_export_candidate_set(args: argparse.Namespace) -> None:
    rows = read_csv(Path(args.input))
    candidates = []
    for row in rows:
        mutation = str(row.get(args.mutation_column, "")).strip().upper()
        if not mutation:
            continue
        score_value = row.get(args.score_column, "")
        try:
            score = float(score_value)
        except (TypeError, ValueError):
            score = None
        candidates.append(
            {
                "mutation": mutation,
                "source_workflow": "mut-model-supervised-finetuning",
                "scores": {"mut_model_score": score} if score is not None else {},
                "risk_flags": [],
                "evidence": [
                    {
                        "type": "supervised_finetuning",
                        "summary": f"mut_model supervised score = {score}" if score is not None else "mut_model score unavailable",
                        "source": str(args.input),
                    }
                ],
                "rationale": "Supervised mut_model score from measured-mutant fine-tuning workflow.",
            }
        )
    payload = {
        "schema_version": "candidate-set/v1",
        "source_workflow": "mut-model-supervised-finetuning",
        "quality": scored_rows_quality(rows, mutation_column=args.mutation_column, score_column=args.score_column),
        "candidates": candidates,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "candidate_count": len(candidates), "quality_status": payload["quality"]["status"]}, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="mut_model data preparation and scoring helpers")
    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser("generate-library")
    gen.add_argument("--sequence", required=True)
    gen.add_argument("--output", required=True)
    gen.add_argument("--max-sites", type=int, default=5)
    gen.add_argument("--random-multisite", type=int, default=0)
    gen.add_argument("--seed", type=int, default=42)
    gen.set_defaults(func=command_generate_library)

    pre = sub.add_parser("prepare-pretrain")
    pre.add_argument("--sequence")
    pre.add_argument("--input", required=True)
    pre.add_argument("--output", required=True)
    pre.add_argument("--mutation-column")
    pre.add_argument("--y-columns", nargs="+", required=True)
    pre.set_defaults(func=command_prepare_pretrain)

    ft = sub.add_parser("prepare-finetune")
    ft.add_argument("--sequence")
    ft.add_argument("--input", required=True)
    ft.add_argument("--output", required=True)
    ft.add_argument("--mutation-column")
    ft.add_argument("--score-column", required=True)
    ft.add_argument(
        "--reference-columns",
        nargs="+",
        help="Numeric teacher columns to retain for DPO fine-tuning",
    )
    ft.set_defaults(func=command_prepare_finetune)

    score = sub.add_parser("score-library")
    score.add_argument("--mut-model-dir", default=os.environ.get("MUT_MODEL_HOME"), required=not bool(os.environ.get("MUT_MODEL_HOME")))
    score.add_argument("--weight", required=True)
    score.add_argument("--input", required=True)
    score.add_argument("--output", required=True)
    score.add_argument("--batch-size", type=int, default=256)
    score.add_argument("--embed-dim", type=int, default=512)
    score.add_argument("--score-column", default="mut_model_score")
    score.set_defaults(func=command_score_library)

    quality = sub.add_parser("quality-report")
    quality.add_argument("--input", required=True)
    quality.add_argument("--output", required=True)
    quality.add_argument("--mutation-column", default="mutation")
    quality.add_argument("--score-column", default="mut_model_score")
    quality.set_defaults(func=command_quality_report)

    export = sub.add_parser("export-candidate-set")
    export.add_argument("--input", required=True)
    export.add_argument("--output", required=True)
    export.add_argument("--mutation-column", default="mutation")
    export.add_argument("--score-column", default="mut_model_score")
    export.set_defaults(func=command_export_candidate_set)
    return parser

def main() -> int:
    args = build_parser().parse_args()
    try:
        args.func(args)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
