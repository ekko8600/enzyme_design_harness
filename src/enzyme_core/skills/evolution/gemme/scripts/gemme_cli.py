#!/usr/bin/env python3
"""Run GEMME Docker zero-shot scoring from a normalized MSA and mutations."""

from __future__ import annotations

import argparse
import csv
import os
from pathlib import Path
import sys
_CORE_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "skills" / "registry.py").is_file())
if str(_CORE_ROOT.parent) not in sys.path:
    sys.path.insert(0, str(_CORE_ROOT.parent))
import re
import shutil
import subprocess
import sys
import tempfile

AA_LIST = set("ACDEFGHIKLMNPQRSTVWY")
MUTATION_RE = re.compile(r"^([ACDEFGHIKLMNPQRSTVWY])([1-9][0-9]*)([ACDEFGHIKLMNPQRSTVWY])$")


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


def ungapped_sequence(aligned: str) -> str:
    return "".join(ch.upper() for ch in aligned if ch.isalpha() and not ch.islower() and ch != ".")


def query_aligned_sequence(aligned: str) -> str:
    return "".join(ch.upper() if ch.isalpha() else "-" for ch in aligned if not ch.islower() and ch != ".")


def parse_point_mutation(text: str) -> tuple[str, int, str]:
    match = MUTATION_RE.match(text.strip().upper())
    if not match:
        raise ValueError(f"Invalid mutation {text!r}; expected format like K5A")
    wt, pos_text, mt = match.groups()
    return wt, int(pos_text), mt


def validate_mutation_group(
    sequence: str,
    mutation_group: str,
    msa_start: int,
    msa_end: int,
) -> list[tuple[str, int, str]]:
    parsed = [parse_point_mutation(item) for item in mutation_group.split(":")]
    seen: set[int] = set()
    for wt, pos, _ in parsed:
        if pos in seen:
            raise ValueError(f"{mutation_group}: duplicate mutation position {pos}")
        seen.add(pos)
        if pos < msa_start or pos > msa_end:
            raise ValueError(
                f"{mutation_group}: position {pos} is outside MSA range "
                f"{msa_start}-{msa_end}"
            )
        observed = sequence[pos - msa_start]
        if observed != wt:
            raise ValueError(
                f"{mutation_group}: wild-type residue mismatch at {pos}; "
                f"expected {wt}, observed {observed}"
            )
    return parsed


def offset_mutation_group(mutation_group: str, msa_start: int) -> str:
    shifted = []
    for item in mutation_group.split(":"):
        wt, pos, mt = parse_point_mutation(item)
        shifted.append(f"{wt}{pos - msa_start + 1}{mt}")
    return ",".join(shifted)


def sanitize_header(header: str, index: int, msa_start: int, msa_end: int) -> str:
    prefix = header.split()[0].split("\t")[0]
    if "/" in prefix:
        prefix = prefix.split("/", 1)[0]
    clean = re.sub(r"[^A-Za-z0-9]", "", prefix) or f"seq{index}"
    return f"{clean}/{msa_start}-{msa_end}"


def write_gemme_msa(records: list[tuple[str, str]], output: Path, msa_start: int, msa_end: int) -> None:
    with output.open("w", encoding="utf-8") as handle:
        for idx, (header, seq) in enumerate(records, start=1):
            handle.write(f">{sanitize_header(header, idx, msa_start, msa_end)}\n")
            handle.write(query_aligned_sequence(seq).replace(".", "-") + "\n")


def read_mutations(args: argparse.Namespace) -> list[str]:
    mutations: list[str] = []
    if args.mutations:
        mutations.extend(args.mutations)
    if args.mutation_csv:
        with Path(args.mutation_csv).open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames:
                raise ValueError(f"Empty mutation CSV: {args.mutation_csv}")
            column = args.mutation_column
            if column not in reader.fieldnames:
                if column == "mutation" and "mutant" in reader.fieldnames:
                    column = "mutant"
                else:
                    raise ValueError(
                        f"Column {args.mutation_column!r} not found in {args.mutation_csv}; "
                        f"available columns: {', '.join(reader.fieldnames)}"
                    )
            for row in reader:
                value = (row.get(column) or "").strip()
                if value:
                    mutations.append(value)
    if not mutations:
        raise ValueError("Provide --mutations or --mutation-csv")
    return mutations


def find_evol_combi_file(work_dir: Path) -> Path:
    matches = sorted(work_dir.glob("*_evolCombi.txt"))
    if not matches:
        raise FileNotFoundError(f"No GEMME *_evolCombi.txt output found in {work_dir}")
    return matches[0]


def parse_gemme_scores(path: Path, fallback_mutations: list[str]) -> dict[str, float]:
    rows: dict[str, float] = {}
    with path.open(encoding="utf-8") as handle:
        reader = csv.reader(handle, delimiter=" ")
        for fields in reader:
            fields = [field for field in fields if field]
            if len(fields) < 2:
                continue
            if fields[0].lower() in {"", "x", "mutant"}:
                continue
            try:
                rows[fields[0].replace(",", ":")] = float(fields[1])
            except ValueError:
                continue
    if rows:
        return rows

    # Some GEMME/R combinations write only values and lose the mutant index.
    values: list[float] = []
    with path.open(encoding="utf-8") as handle:
        for raw in handle:
            for token in raw.split():
                try:
                    values.append(float(token))
                    break
                except ValueError:
                    continue
    return {mutation: value for mutation, value in zip(fallback_mutations, values)}


def docker_image_available(image: str) -> bool:
    result = subprocess.run(
        ["docker", "image", "inspect", image],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


def maybe_load_image(image: str, tar_path: str | None) -> None:
    if docker_image_available(image) or not tar_path:
        return
    tar = Path(tar_path).expanduser()
    if not tar.exists():
        return
    subprocess.run(["docker", "load", "-i", str(tar)], check=True)


def command_score(args: argparse.Namespace) -> None:
    if shutil.which("docker") is None:
        raise RuntimeError("docker executable not found on PATH")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    # Keep the default bind-mount inside the Host output workspace.  Docker
    # (including rootless Docker) can only bind paths visible to its daemon;
    # an otherwise valid /tmp directory may be outside that visibility
    # boundary.  Hosts using a different mounted workspace can still provide
    # an explicit --temp-dir.
    temp_root = Path(args.temp_dir) if args.temp_dir else Path(tempfile.mkdtemp(prefix="gemme_", dir=str(out_dir)))
    temp_root.mkdir(parents=True, exist_ok=True)
    # Docker may run with a remapped rootless UID.  A tempfile directory is
    # normally 0700, which makes the bind-mounted project appear empty to
    # that container even though the Host created the input successfully.
    # The workspace contains only the caller-provided MSA/mutation inputs and
    # transient model files, so expose only this bounded directory to the
    # container rather than weakening any parent or result directory.
    temp_root.chmod(0o755)
    work_dir = temp_root / "project"
    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True)
    work_dir.chmod(0o755)

    records = parse_fasta(Path(args.msa))
    inferred_sequence = ungapped_sequence(records[0][1])
    from enzyme_core.support.sequences import protein_sequence
    sequence = protein_sequence(fasta=args.sequence_fasta) if getattr(args, "sequence_fasta", None) else protein_sequence(sequence=args.sequence or inferred_sequence)
    invalid = sorted(set(sequence) - AA_LIST)
    if invalid:
        raise ValueError(f"Sequence contains unsupported amino acids: {''.join(invalid)}")
    if (args.sequence or getattr(args, "sequence_fasta", None)) and sequence != inferred_sequence[: len(sequence)]:
        raise ValueError(
            "Provided --sequence does not match the first MSA record after removing gaps"
        )

    msa_start = args.msa_start
    msa_end = args.msa_end or (msa_start + len(sequence) - 1)
    if msa_end - msa_start + 1 != len(sequence):
        raise ValueError(
            f"MSA range {msa_start}-{msa_end} length does not match sequence length {len(sequence)}"
        )

    mutations = read_mutations(args)
    parsed_by_mutation = {
        mutation: validate_mutation_group(sequence, mutation, msa_start, msa_end)
        for mutation in mutations
    }

    msa_name = "gemme_input.a2m"
    mutation_name = "gemme_mutants.txt"
    write_gemme_msa(records, work_dir / msa_name, msa_start, msa_end)
    (work_dir / msa_name).chmod(0o644)
    with (work_dir / mutation_name).open("w", encoding="utf-8") as handle:
        for mutation in mutations:
            handle.write(offset_mutation_group(mutation, msa_start) + "\n")
    (work_dir / mutation_name).chmod(0o644)

    maybe_load_image(args.docker_image, args.docker_image_tar)
    command = [
        "docker",
        "run",
        "--rm",
        "--pull=never",
        "-v",
        f"{work_dir.resolve()}:/project",
        "-w",
        "/project",
        args.docker_image,
        "python2.7",
        "/opt/GEMME/gemme.py",
        msa_name,
        "-r",
        "input",
        "-f",
        msa_name,
        "-m",
        mutation_name,
        "-N",
        str(args.nseqs),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    (out_dir / "gemme_stdout.log").write_text(result.stdout, encoding="utf-8")
    (out_dir / "gemme_stderr.log").write_text(result.stderr, encoding="utf-8")
    if result.returncode != 0:
        raise RuntimeError(
            f"GEMME Docker run failed with exit code {result.returncode}. "
            f"See {out_dir / 'gemme_stderr.log'}"
        )

    score_file = find_evol_combi_file(work_dir)
    raw_scores = parse_gemme_scores(score_file, mutations)
    output = out_dir / "mutation_scores.csv"
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["mutation", "score", "GEMME_score", "positions"],
        )
        writer.writeheader()
        for mutation in mutations:
            score = raw_scores.get(offset_mutation_group(mutation, msa_start).replace(",", ":"))
            if score is None:
                score = raw_scores.get(mutation)
            positions = ":".join(str(pos) for _, pos, _ in parsed_by_mutation[mutation])
            writer.writerow(
                {
                    "mutation": mutation,
                    "score": "" if score is None else score,
                    "GEMME_score": "" if score is None else score,
                    "positions": positions,
                }
            )
    if not args.keep_temp:
        shutil.rmtree(temp_root, ignore_errors=True)
    print(f"Output saved to: {output}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="GEMME Docker mutant scoring CLI")
    sub = parser.add_subparsers(dest="command", required=True)
    score = sub.add_parser("score", help="Score protein point mutants with GEMME")
    score.add_argument("--msa", required=True, help="A2M-like FASTA MSA with query first")
    sequence_group = score.add_mutually_exclusive_group()
    sequence_group.add_argument("--sequence", help="Wild-type sequence literal; inferred from MSA if omitted")
    sequence_group.add_argument("--sequence-fasta", help="Exactly one protein FASTA record")
    score.add_argument("--mutations", nargs="+", help="Mutations like A10G A10G:B12D")
    score.add_argument("--mutation-csv", help="CSV containing a mutation or mutant column")
    score.add_argument("--mutation-column", default="mutation")
    score.add_argument("--msa-start", type=int, default=1)
    score.add_argument("--msa-end", type=int)
    score.add_argument("--docker-image", default="elodielaine/gemme:gemme")
    score.add_argument(
        "--docker-image-tar",
        default=os.environ.get("GEMME_DOCKER_IMAGE_TAR"),
        help="Optional docker save tar to load if the image is missing",
    )
    score.add_argument("--nseqs", type=int, default=20000)
    score.add_argument("--out-dir", default="./out/gemme_zeroshot")
    score.add_argument("--temp-dir", help="Temporary parent directory to use instead of auto-created one")
    score.add_argument("--keep-temp", action="store_true")
    score.set_defaults(func=command_score)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    try:
        args.func(args)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
