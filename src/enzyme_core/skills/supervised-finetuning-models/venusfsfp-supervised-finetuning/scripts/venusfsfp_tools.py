#!/usr/bin/env python3
"""Utilities for preparing arbitrary protein data for VenusFSFP."""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

AA = set("ACDEFGHIKLMNPQRSTVWY")
MUT_RE = re.compile(r"^([ACDEFGHIKLMNPQRSTVWY])([1-9][0-9]*)([ACDEFGHIKLMNPQRSTVWY])$")


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError(f"Empty CSV: {path}")
        return [dict(row) for row in reader]


def write_csv(path: Path, rows: list[dict[str, object]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def clean_sequence(sequence: str) -> str:
    seq = re.sub(r"\s+", "", sequence).upper()
    invalid = sorted(set(seq) - AA)
    if invalid:
        raise ValueError(f"Sequence contains unsupported amino acids: {''.join(invalid)}")
    return seq


def mutation_value(row: dict[str, str], column: str | None = None) -> str:
    candidates = [column] if column else ["mutant", "mutation"]
    for candidate in candidates:
        if candidate and row.get(candidate):
            return row[candidate].strip().upper()
    raise ValueError(f"Cannot find mutation column in row: {row}")


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
            raise ValueError(f"{mutation}: expected wild-type {wt}{pos}, observed {observed}{pos}")
        mutated[pos - 1] = mt
    return "".join(mutated)


def bin_scores(values: list[float]) -> list[int]:
    if not values:
        return []
    ordered = sorted(values)
    mid = len(ordered) // 2
    threshold = ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2
    return [1 if value >= threshold else 0 for value in values]


def read_gemme_scores(path: Path | None) -> dict[str, str]:
    if not path:
        return {}
    scores: dict[str, str] = {}
    for row in read_csv(path):
        mutation = (row.get("mutation") or row.get("mutant") or "").strip().upper()
        if not mutation:
            continue
        value = row.get("GEMME_score") or row.get("score") or row.get("GEMME") or ""
        scores[mutation] = value
    return scores


def normalize_rows(
    *,
    sequence: str,
    input_path: Path,
    score_column: str | None,
    mutation_column: str | None,
    gemme_scores: dict[str, str],
    extra_columns: list[str] | None = None,
    unlabeled: bool = False,
) -> list[dict[str, object]]:
    rows = read_csv(input_path)
    scores: list[float] = []
    normalized: list[dict[str, object]] = []
    for row in rows:
        mutation = mutation_value(row, mutation_column)
        venus_row: dict[str, object] = {
            "mutant": mutation,
            "mutated_sequence": row.get("mutated_sequence") or mutate_sequence(sequence, mutation),
        }
        if extra_columns:
            for col in extra_columns:
                val = row.get(col, "")
                if val not in {None, ""}:
                    try:
                        venus_row[col] = float(val)
                    except (ValueError, TypeError):
                        venus_row[col] = val
        if unlabeled:
            venus_row["DMS_score"] = row.get(score_column or "DMS_score") or 0.0
            venus_row["DMS_score_bin"] = row.get("DMS_score_bin") or 0
        else:
            if not score_column:
                score_column = "DMS_score"
            if score_column not in row or row[score_column] == "":
                raise ValueError(f"Missing score column {score_column!r} for mutation {mutation}")
            score = float(row[score_column])
            scores.append(score)
            venus_row["DMS_score"] = score
            venus_row["DMS_score_bin"] = row.get("DMS_score_bin", "")
        if mutation in gemme_scores:
            venus_row["GEMME"] = gemme_scores[mutation]
        normalized.append(venus_row)

    bins = bin_scores(scores)
    bin_index = 0
    for row in normalized:
        if row["DMS_score_bin"] == "":
            row["DMS_score_bin"] = bins[bin_index]
            bin_index += 1
    return normalized


def safe_dataset_name(protein: str, assay: str) -> str:
    protein = re.sub(r"[^A-Za-z0-9]+", "", protein.upper()) or "TARGET"
    assay = re.sub(r"[^A-Za-z0-9]+", "", assay) or "custom"
    return f"{protein}_{assay}"


def output_with_name(output: Path | None, protein: str, assay: str) -> Path:
    if output:
        return output
    return Path(f"{safe_dataset_name(protein, assay)}.csv")


def command_prepare_training(args: argparse.Namespace) -> None:
    sequence = clean_sequence(args.sequence)
    gemme_scores = read_gemme_scores(Path(args.gemme_csv) if args.gemme_csv else None)
    extra_columns = args.extra_columns or []
    rows = normalize_rows(
        sequence=sequence,
        input_path=Path(args.input),
        score_column=args.score_column,
        mutation_column=args.mutation_column,
        gemme_scores=gemme_scores,
        extra_columns=extra_columns,
    )
    fields = ["mutant", "mutated_sequence", "DMS_score", "DMS_score_bin"]
    for col in extra_columns:
        if col not in fields:
            fields.append(col)
    if gemme_scores and "GEMME" not in fields:
        fields.append("GEMME")
    output = output_with_name(Path(args.output) if args.output else None, args.protein, args.assay)
    write_csv(output, rows, fields)
    metadata = {
        "dataset": safe_dataset_name(args.protein, args.assay),
        "training_rows": len(rows),
        "output": str(output),
        "has_gemme": bool(gemme_scores),
        "extra_columns": extra_columns,
    }
    print(json.dumps(metadata, indent=2))


def command_prepare_candidates(args: argparse.Namespace) -> None:
    sequence = clean_sequence(args.sequence)
    gemme_scores = read_gemme_scores(Path(args.gemme_csv) if args.gemme_csv else None)
    extra_columns = args.extra_columns or []
    train_rows = normalize_rows(
        sequence=sequence,
        input_path=Path(args.training_csv),
        score_column=args.score_column,
        mutation_column=args.mutation_column,
        gemme_scores=gemme_scores,
        extra_columns=extra_columns,
    )
    candidate_rows = normalize_rows(
        sequence=sequence,
        input_path=Path(args.candidates_csv),
        score_column=args.candidate_score_column,
        mutation_column=args.candidate_mutation_column or args.mutation_column,
        gemme_scores=gemme_scores,
        extra_columns=extra_columns,
        unlabeled=True,
    )
    fields = ["mutant", "mutated_sequence", "DMS_score", "DMS_score_bin"]
    for col in extra_columns:
        if col not in fields:
            fields.append(col)
    if gemme_scores and "GEMME" not in fields:
        fields.append("GEMME")
    output = output_with_name(Path(args.output) if args.output else None, args.protein, args.assay)
    write_csv(output, train_rows + candidate_rows, fields)
    metadata = {
        "dataset": safe_dataset_name(args.protein, args.assay),
        "training_rows": len(train_rows),
        "candidate_rows": len(candidate_rows),
        "train_size_for_test": len(train_rows),
        "output": str(output),
        "has_gemme": bool(gemme_scores),
        "extra_columns": extra_columns,
    }
    print(json.dumps(metadata, indent=2))


def command_merge_gemme(args: argparse.Namespace) -> None:
    venus_path = Path(args.venus_csv)
    rows = read_csv(venus_path)
    gemme_scores = read_gemme_scores(Path(args.gemme_csv))
    missing: list[str] = []
    for row in rows:
        mutation = mutation_value(row)
        score = gemme_scores.get(mutation)
        if score is None or score == "":
            missing.append(mutation)
            row["GEMME"] = ""
        else:
            row["GEMME"] = score
    fields = list(rows[0].keys()) if rows else ["mutant", "mutated_sequence", "DMS_score", "DMS_score_bin"]
    if "GEMME" not in fields:
        fields.append("GEMME")
    output = Path(args.output) if args.output else venus_path
    write_csv(output, rows, fields)
    print(json.dumps({"output": str(output), "rows": len(rows), "missing_gemme": missing}, indent=2))


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
        "schema_version": "venusfsfp-supervised-quality/v1",
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
                "source_workflow": "venusfsfp-supervised-finetuning",
                "scores": {"venusfsfp_score": score} if score is not None else {},
                "risk_flags": [],
                "evidence": [
                    {
                        "type": "supervised_finetuning",
                        "summary": f"VenusFSFP supervised score = {score}" if score is not None else "VenusFSFP score unavailable",
                        "source": str(args.input),
                    }
                ],
                "rationale": "Supervised VenusFSFP score from measured-mutant fine-tuning workflow.",
            }
        )
    payload = {
        "schema_version": "candidate-set/v1",
        "source_workflow": "venusfsfp-supervised-finetuning",
        "quality": scored_rows_quality(rows, mutation_column=args.mutation_column, score_column=args.score_column),
        "candidates": candidates,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "candidate_count": len(candidates), "quality_status": payload["quality"]["status"]}, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prepare arbitrary protein data for VenusFSFP")
    sub = parser.add_subparsers(dest="command", required=True)

    prep = sub.add_parser("prepare-training")
    prep.add_argument("--sequence", required=True)
    prep.add_argument("--protein", required=True)
    prep.add_argument("--assay", default="custom")
    prep.add_argument("--input", required=True)
    prep.add_argument("--score-column", required=True)
    prep.add_argument("--mutation-column")
    prep.add_argument("--gemme-csv")
    prep.add_argument("--extra-columns", nargs="+", help="Additional columns to preserve from input CSV (e.g. saprot_fitness)")
    prep.add_argument("--output")
    prep.set_defaults(func=command_prepare_training)

    cand = sub.add_parser("prepare-candidates")
    cand.add_argument("--sequence", required=True)
    cand.add_argument("--protein", required=True)
    cand.add_argument("--assay", default="custom")
    cand.add_argument("--training-csv", required=True)
    cand.add_argument("--candidates-csv", required=True)
    cand.add_argument("--score-column", required=True)
    cand.add_argument("--mutation-column")
    cand.add_argument("--candidate-mutation-column")
    cand.add_argument("--candidate-score-column")
    cand.add_argument("--gemme-csv")
    cand.add_argument("--extra-columns", nargs="+", help="Additional columns to preserve from input CSV (e.g. saprot_fitness)")
    cand.add_argument("--output")
    cand.set_defaults(func=command_prepare_candidates)

    merge = sub.add_parser("merge-gemme")
    merge.add_argument("--venus-csv", required=True)
    merge.add_argument("--gemme-csv", required=True)
    merge.add_argument("--output")
    merge.set_defaults(func=command_merge_gemme)

    quality = sub.add_parser("quality-report")
    quality.add_argument("--input", required=True)
    quality.add_argument("--output", required=True)
    quality.add_argument("--mutation-column", default="mutant")
    quality.add_argument("--score-column", default="VenusFSFP_score")
    quality.set_defaults(func=command_quality_report)

    export = sub.add_parser("export-candidate-set")
    export.add_argument("--input", required=True)
    export.add_argument("--output", required=True)
    export.add_argument("--mutation-column", default="mutant")
    export.add_argument("--score-column", default="VenusFSFP_score")
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
