#!/usr/bin/env python3
"""Normalize aligned FASTA/A3M/A2M files for GEMME-style MSA consumers."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys


def parse_fasta(path: Path) -> list[tuple[str, str]]:
    records: list[tuple[str, str]] = []
    header: str | None = None
    seq_lines: list[str] = []
    with path.open(encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if not line:
                continue
            if line.startswith(">"):
                if header is not None:
                    records.append((header, "".join(seq_lines)))
                header = line[1:].strip() or "sequence"
                seq_lines = []
            else:
                seq_lines.append(line)
    if header is not None:
        records.append((header, "".join(seq_lines)))
    if not records:
        raise ValueError(f"No FASTA records found in {path}")
    return records


def ungapped(seq: str) -> str:
    return "".join(ch.upper() for ch in seq if ch.isalpha() and not ch.islower())


def sanitize_header(header: str, index: int, msa_start: int, msa_end: int) -> str:
    prefix = header.split()[0].split("\t")[0]
    if "/" in prefix:
        prefix = prefix.split("/", 1)[0]
    clean = re.sub(r"[^A-Za-z0-9]", "", prefix) or f"seq{index}"
    return f"{clean}/{msa_start}-{msa_end}"


def choose_query_index(
    records: list[tuple[str, str]], query_id: str | None, query_sequence: str | None
) -> int:
    if query_id:
        for idx, (header, _) in enumerate(records):
            if query_id == header or query_id == header.split()[0]:
                return idx
        raise ValueError(f"Query id {query_id!r} not found in alignment")
    if query_sequence:
        target = query_sequence.strip().upper()
        for idx, (_, seq) in enumerate(records):
            if ungapped(seq) == target:
                return idx
        raise ValueError("Query sequence not found in alignment")
    return 0


def normalize_sequence(seq: str, uppercase_inserts: bool, drop_lowercase_inserts: bool) -> str:
    normalized = []
    for ch in seq:
        if drop_lowercase_inserts and ch.islower():
            continue
        if ch == ".":
            normalized.append("-")
        elif ch == "-":
            normalized.append(ch)
        elif ch.isalpha():
            normalized.append(ch.upper() if uppercase_inserts else ch)
    return "".join(normalized)


def command_convert(args: argparse.Namespace) -> None:
    records = parse_fasta(Path(args.input))
    query_idx = choose_query_index(records, args.query_id, args.query_sequence)
    if query_idx != 0:
        records = [records[query_idx], *records[:query_idx], *records[query_idx + 1 :]]

    query_len = len(ungapped(records[0][1]))
    msa_start = args.msa_start
    msa_end = args.msa_end or (msa_start + query_len - 1)
    if msa_end - msa_start + 1 != query_len:
        raise ValueError(
            f"MSA range {msa_start}-{msa_end} length does not match query length {query_len}"
        )

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        for idx, (header, seq) in enumerate(records, start=1):
            handle.write(f">{sanitize_header(header, idx, msa_start, msa_end)}\n")
            handle.write(normalize_sequence(seq, args.uppercase_inserts, args.drop_lowercase_inserts) + "\n")
    print(f"A2M-like alignment saved to: {output}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Convert aligned FASTA/A3M/A2M to GEMME-compatible A2M-like FASTA"
    )
    parser.add_argument("input", help="Input aligned FASTA/A3M/A2M")
    parser.add_argument("--output", "-o", required=True)
    parser.add_argument("--query-id", help="Header/id of the target sequence to move first")
    parser.add_argument("--query-sequence", help="Ungapped target sequence to move first")
    parser.add_argument("--msa-start", type=int, default=1)
    parser.add_argument("--msa-end", type=int)
    parser.add_argument(
        "--uppercase-inserts",
        action="store_true",
        help="Uppercase all residues, matching GEMME wrapper preparation",
    )
    parser.add_argument(
        "--drop-lowercase-inserts",
        action="store_true",
        help="Drop A3M lowercase insert states before writing query-aligned output",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    try:
        command_convert(args)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
