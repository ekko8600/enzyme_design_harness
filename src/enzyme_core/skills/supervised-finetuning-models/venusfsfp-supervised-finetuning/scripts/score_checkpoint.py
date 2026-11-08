#!/usr/bin/env python3
"""Score mutation libraries with a saved VenusFSFP ESM-2 production checkpoint."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import re
import sys


AA = set("ACDEFGHIKLMNPQRSTVWY")
MUT_RE = re.compile(r"^([ACDEFGHIKLMNPQRSTVWY])([1-9][0-9]*)([ACDEFGHIKLMNPQRSTVWY])$")


def clean_sequence(sequence: str) -> str:
    value = re.sub(r"\s+", "", sequence).upper()
    invalid = sorted(set(value) - AA)
    if not value or invalid:
        raise ValueError(
            "Wild-type sequence is empty or contains unsupported amino acids: "
            + "".join(invalid)
        )
    return value


def parse_mutation(mutation: str, sequence: str) -> list[tuple[int, str, str]]:
    parsed: list[tuple[int, str, str]] = []
    seen: set[int] = set()
    for item in mutation.strip().upper().split(":"):
        match = MUT_RE.fullmatch(item)
        if not match:
            raise ValueError(f"Invalid mutation {mutation!r}; expected A23V or A23V:G57D")
        wt, position_text, mt = match.groups()
        position = int(position_text)
        if position in seen:
            raise ValueError(f"Duplicate position {position} in {mutation!r}")
        if position > len(sequence):
            raise ValueError(f"Position {position} exceeds sequence length {len(sequence)}")
        observed = sequence[position - 1]
        if wt != observed:
            raise ValueError(
                f"Mutation {mutation!r} expects {wt}{position}, observed {observed}{position}"
            )
        seen.add(position)
        parsed.append((position, wt, mt))
    return parsed


def apply_mutation(sequence: str, mutations: list[tuple[int, str, str]]) -> str:
    result = list(sequence)
    for position, _wt, mt in mutations:
        result[position - 1] = mt
    return "".join(result)


def read_rows(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError(f"Empty CSV: {path}")
        return [dict(row) for row in reader], list(reader.fieldnames)


def write_rows(path: Path, rows: list[dict[str, object]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def checkpoint_digest(path: Path) -> tuple[str, list[str]]:
    files = sorted(item for item in path.rglob("*") if item.is_file())
    if not files:
        raise ValueError(f"Checkpoint directory is empty: {path}")
    digest = hashlib.sha256()
    relative_files: list[str] = []
    for item in files:
        relative = item.relative_to(path).as_posix()
        relative_files.append(relative)
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(sha256_file(item).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest(), relative_files


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run inference only with a saved VenusFSFP ESM-2 checkpoint"
    )
    parser.add_argument("--venusfsfp-dir", default=os.environ.get("VENUSFSFP_HOME"), required=not bool(os.environ.get("VENUSFSFP_HOME")))
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--sequence", required=True, help="Wild-type amino-acid sequence")
    parser.add_argument("--input", required=True, help="Candidate mutation CSV")
    parser.add_argument("--output", required=True)
    parser.add_argument("--mutation-column", default="mutant")
    parser.add_argument("--score-column", default="VenusFSFP_score")
    parser.add_argument("--metadata-output", help="Defaults to <output>.json")
    parser.add_argument("--device", choices=["auto", "cpu", "cuda"], default="auto")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    venusfsfp_dir = Path(args.venusfsfp_dir).expanduser().resolve()
    checkpoint = Path(args.checkpoint).expanduser().resolve()
    input_path = Path(args.input).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve()
    metadata_path = (
        Path(args.metadata_output).expanduser().resolve()
        if args.metadata_output
        else Path(str(output_path) + ".json")
    )
    if not checkpoint.is_dir():
        raise FileNotFoundError(f"Checkpoint directory not found: {checkpoint}")
    if not input_path.is_file():
        raise FileNotFoundError(f"Candidate CSV not found: {input_path}")

    checkpoint_sha256, checkpoint_files = checkpoint_digest(checkpoint)
    if "adapter_config.json" not in checkpoint_files:
        raise ValueError(
            "Expected a VenusFSFP ESM-2 LoRA checkpoint containing adapter_config.json"
        )
    sequence = clean_sequence(args.sequence)
    rows, fields = read_rows(input_path)
    if not rows:
        raise ValueError(f"No candidate rows found in {input_path}")
    if args.mutation_column not in fields:
        raise ValueError(f"Missing mutation column {args.mutation_column!r}")
    parsed_rows = []
    for index, row in enumerate(rows):
        mutations = parse_mutation(row[args.mutation_column], sequence)
        if row.get("mutated_sequence"):
            supplied = clean_sequence(row["mutated_sequence"])
            expected = apply_mutation(sequence, mutations)
            if supplied != expected:
                raise ValueError(
                    f"Row {index} mutated_sequence does not match "
                    f"{row[args.mutation_column]!r}"
                )
        parsed_rows.append(mutations)

    import torch
    from peft import PeftModel
    from transformers import EsmForMaskedLM, EsmTokenizer

    sys.path.insert(0, str(venusfsfp_dir))
    from fsfp import config

    if "esm2" not in config.model_dir:
        raise ValueError("VenusFSFP config does not define an esm2 base model")
    if args.device == "auto":
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    else:
        device = torch.device(args.device)

    base_model_path = config.model_dir["esm2"]
    tokenizer = EsmTokenizer.from_pretrained(base_model_path)
    base_model = EsmForMaskedLM.from_pretrained(base_model_path)
    model = PeftModel.from_pretrained(base_model, checkpoint, is_trainable=False)
    model.to(device)
    model.eval()

    encoded = tokenizer(sequence, return_tensors="pt").to(device)
    with torch.no_grad():
        logits = model(**encoded).logits[0]
        log_probabilities = torch.log_softmax(logits, dim=-1)

    scored_rows: list[dict[str, object]] = []
    for row, mutations in zip(rows, parsed_rows):
        score = 0.0
        for position, wt, mt in mutations:
            wt_token = tokenizer(wt, add_special_tokens=False)["input_ids"]
            mt_token = tokenizer(mt, add_special_tokens=False)["input_ids"]
            if len(wt_token) != 1 or len(mt_token) != 1:
                raise ValueError(f"Tokenizer did not produce one token for {wt}->{mt}")
            token_position = position  # ESM prepends one BOS token to the 1-indexed residue.
            score += float(
                (
                    log_probabilities[token_position, mt_token[0]]
                    - log_probabilities[token_position, wt_token[0]]
                ).detach().cpu()
            )
        output_row: dict[str, object] = dict(row)
        output_row[args.score_column] = score
        scored_rows.append(output_row)

    if args.score_column not in fields:
        fields.append(args.score_column)
    write_rows(output_path, scored_rows, fields)
    metadata = {
        "schema_version": "venusfsfp-checkpoint-inference/v1",
        "inference_only": True,
        "model": "esm2",
        "base_model": str(base_model_path),
        "checkpoint": str(checkpoint),
        "checkpoint_sha256": checkpoint_sha256,
        "checkpoint_files": checkpoint_files,
        "wild_type_sequence_sha256": hashlib.sha256(sequence.encode("ascii")).hexdigest(),
        "input": str(input_path),
        "input_sha256": sha256_file(input_path),
        "candidate_count": len(rows),
        "mutation_column": args.mutation_column,
        "score_column": args.score_column,
        "score_interpretation": "sum_log_probability_mutant_minus_wild_type",
        "output": str(output_path),
        "output_sha256": sha256_file(output_path),
        "device": str(device),
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
