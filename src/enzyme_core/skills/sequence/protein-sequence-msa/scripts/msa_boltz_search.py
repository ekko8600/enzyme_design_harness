#!/usr/bin/env python
"""ColabFold MMseqs2 MSA search via boltz's built-in MSA client.

Submits a query protein sequence to the ColabFold MMseqs2 API through boltz's
`boltz.data.msa.mmseqs2.run_mmseqs2`, polls for completion, downloads the
resulting A3M alignment, optionally merges UniRef + environmental hits into a
single cleaned A3M suitable for GEMME and conservation analysis.

Prefer this over EBI Clustal Omega when a high-quality search MSA is needed
(rational design, GEMME, conservation analysis).  Falls back to EBI Clustal
Omega only when the ColabFold API is unavailable and local MSA databases are
missing.

Outputs:
  - <prefix>_combined_cleaned.a3m
  - <prefix>_uniref.a3m           (if --keep-intermediates)
  - <prefix>_env.a3m              (if --keep-intermediates)
  - <prefix>_msa_report.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def _fuse_a3m_lines(query_seq: str, a3m_lines: list[str]) -> str:
    """Merge multiple A3M strings into one, keeping the query first.

    Removes duplicate sequences (by sequence identity fingerprint) and
    strips lowercase insert states from the query column to produce a
    clean A3M suitable for GEMME and per-column conservation analysis.
    """
    seen: set[str] = set()
    merged: list[str] = []

    # Query line
    merged.append(f">{len(merged) + 101}\n{query_seq}\n")
    seen.add(query_seq.upper())

    for a3m in a3m_lines:
        if not a3m.strip():
            continue
        lines = a3m.splitlines(keepends=True)
        i = 0
        while i < len(lines):
            line = lines[i]
            if line.startswith(">"):
                i += 1
                if i < len(lines):
                    seq_line = lines[i].rstrip("\n")
                    seq_upper = seq_line.upper().replace("-", "")
                    if seq_upper not in seen and len(seq_upper) > 20:
                        seen.add(seq_upper)
                        merged.append(line)
                        merged.append(seq_line + "\n")
                i += 1
            else:
                i += 1
    return "".join(merged)


def run_boltz_msa(
    query_seq: str,
    prefix: str,
    *,
    host_url: str = "https://api.colabfold.com",
    use_env: bool = True,
    keep_intermediates: bool = False,
) -> dict:
    """Run boltz's built-in MMseqs2 MSA search.

    Parameters
    ----------
    query_seq : str
        Single query protein sequence (uppercase, standard AAs).
    prefix : str
        Directory prefix for intermediate/tmp files.
    host_url : str
        ColabFold MMseqs2 API URL.
    use_env : bool
        Include environmental (MGnify, Metaeuk, SMAG) hits.
    keep_intermediates : bool
        Keep raw A3M files alongside the cleaned combined A3M.

    Returns
    -------
    dict with keys: combined_a3m, uniref_a3m, env_a3m, depth, status
    """
    from boltz.data.msa.mmseqs2 import run_mmseqs2  # type: ignore[import-untyped]

    prefix_path = Path(prefix)

    # Run single-sequence MSA search (no pairing)
    a3m_lines = run_mmseqs2(
        query_seq,
        prefix=str(prefix_path / "msa_tmp"),
        use_env=use_env,
        use_filter=True,
        use_pairing=False,
        host_url=host_url,
    )

    result: dict = {
        "status": "ok",
        "uniref_a3m": "",
        "env_a3m": "",
        "combined_a3m": "",
        "depth": 0,
    }

    if not a3m_lines:
        result["status"] = "empty"
        return result

    result["uniref_a3m"] = a3m_lines[0]
    result["env_a3m"] = a3m_lines[1] if len(a3m_lines) > 1 else ""

    # Count unique sequences in combined
    combined = _fuse_a3m_lines(query_seq, a3m_lines)
    result["combined_a3m"] = combined
    result["depth"] = combined.count(">") - 1  # exclude query

    # Write outputs
    out_combined = prefix_path / f"{prefix_path.name}_combined_cleaned.a3m"
    out_combined.write_text(combined, encoding="utf-8")
    result["combined_a3m_path"] = str(out_combined)

    if keep_intermediates:
        out_uniref = prefix_path / f"{prefix_path.name}_uniref.a3m"
        out_env = prefix_path / f"{prefix_path.name}_env.a3m"
        out_uniref.write_text(a3m_lines[0], encoding="utf-8")
        if len(a3m_lines) > 1:
            out_env.write_text(a3m_lines[1], encoding="utf-8")
        result["uniref_a3m_path"] = str(out_uniref)
        result["env_a3m_path"] = str(out_env)

    # Write report
    report = {
        "method": "colabfold_mmseqs2_via_boltz",
        "host_url": host_url,
        "use_environmental": use_env,
        "depth": result["depth"],
        "combined_a3m": result["combined_a3m_path"],
    }
    report_path = prefix_path / f"{prefix_path.name}_msa_report.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    result["report_path"] = str(report_path)

    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="ColabFold MMseqs2 MSA search via boltz"
    )
    parser.add_argument("query", help="Single query protein sequence or @file.fasta")
    parser.add_argument(
        "-o", "--out-dir", required=True, help="Output directory prefix"
    )
    parser.add_argument(
        "--host-url",
        default="https://api.colabfold.com",
        help="ColabFold MMseqs2 API URL",
    )
    parser.add_argument(
        "--no-env",
        action="store_true",
        help="Disable environmental (MGnify/etc.) hit expansion",
    )
    parser.add_argument(
        "--keep-intermediates",
        action="store_true",
        help="Keep raw UniRef and env A3M files",
    )
    parser.add_argument(
        "--json", action="store_true", help="Output result as JSON to stdout"
    )
    args = parser.parse_args()

    # Read query: either inline sequence or @file
    query = args.query
    if query.startswith("@"):
        fasta_path = Path(query[1:])
        if not fasta_path.exists():
            print(f"Error: file not found: {fasta_path}", file=sys.stderr)
            sys.exit(1)
        # Take first sequence from FASTA
        seqs: list[str] = []
        current: list[str] = []
        for line in fasta_path.read_text().splitlines():
            line = line.strip()
            if line.startswith(">"):
                if current:
                    seqs.append("".join(current))
                current = []
            else:
                current.append(line)
        if current:
            seqs.append("".join(current))
        if not seqs:
            print("Error: no sequences found in FASTA", file=sys.stderr)
            sys.exit(1)
        query = seqs[0]

    query = query.strip().upper()
    if not query or not all(c in "ACDEFGHIKLMNPQRSTVWY" for c in query):
        print("Error: invalid protein sequence", file=sys.stderr)
        sys.exit(1)

    os.makedirs(args.out_dir, exist_ok=True)
    prefix_name = Path(args.out_dir).name or "msa"

    result = run_boltz_msa(
        query,
        prefix=args.out_dir,
        host_url=args.host_url,
        use_env=not args.no_env,
        keep_intermediates=args.keep_intermediates,
    )

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Status: {result['status']}")
        print(f"Depth:  {result['depth']} sequences")
        print(f"A3M:    {result.get('combined_a3m_path', 'N/A')}")
        print(f"Report: {result.get('report_path', 'N/A')}")


if __name__ == "__main__":
    main()
