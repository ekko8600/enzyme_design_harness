#!/usr/bin/env python3
"""Run AlphaFold2/ColabFold locally through the colabfold_batch CLI."""

from __future__ import annotations

import argparse
import hashlib
import json
import shlex
import subprocess
from datetime import datetime
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local ColabFold runner")
    parser.add_argument("--input-fasta", required=True)
    parser.add_argument("--msa-file", help="Frozen A3M for offline prediction")
    parser.add_argument("--seed", type=int, default=1729)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--out-dir", default="./out/alphafold")
    parser.add_argument("--run-name")
    parser.add_argument("--models", default="1")
    parser.add_argument("--num-recycles", type=int, default=3)
    parser.add_argument("--num-relax", type=int, default=0)
    parser.add_argument("--use-templates", action="store_true")
    parser.add_argument("--extra-args", help="Additional arguments passed to colabfold_batch")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    run_name = args.run_name or datetime.now().strftime("%y%m%d%H%M")
    out_dir = Path(args.out_dir) / run_name
    if out_dir.exists() and any(out_dir.iterdir()):
        raise ValueError('PREDICTION_OUTPUT_DIRECTORY_NOT_EMPTY')
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.seed < 1:
        raise ValueError("seed must be positive")
    if args.offline and (not args.msa_file or not Path(args.msa_file).is_file()):
        raise ValueError("OFFLINE_PREDICTION_REQUIRES_FROZEN_MSA")
    cmd = [
        "colabfold_batch",
        "--num-recycle",
        str(args.num_recycles),
        "--num-models",
        str(len([m for m in args.models.split(",") if m])),
    ]
    if args.num_relax:
        cmd.append("--amber")
    if args.use_templates:
        cmd.extend(["--templates"])
    if args.extra_args:
        cmd.extend(shlex.split(args.extra_args))
    cmd.extend(["--random-seed", str(args.seed)])
    if args.msa_file:
        import shutil
        msa_input = out_dir / "frozen_msa.a3m"
        shutil.copyfile(args.msa_file, msa_input)
        cmd.extend([str(msa_input), str(out_dir)])
    else:
        cmd.extend([args.input_fasta, str(out_dir)])

    print("+ " + " ".join(shlex.quote(part) for part in cmd))
    subprocess.run(cmd, check=True)
    def record(path):
        return {'path': path.relative_to(out_dir).as_posix(), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'size_bytes': path.stat().st_size}
    structures = [record(path) for path in sorted(out_dir.rglob('*.pdb')) if path.is_file()]
    if not structures:
        raise RuntimeError('PREDICTION_PRODUCED_NO_PDB')
    confidence = [record(path) for path in sorted(out_dir.rglob('*.json')) if path.is_file() and path.name != 'manifest.json']
    manifest = {'schema_version': 'enzyme-core/structure-prediction/v1',
                'input_fasta_sha256': hashlib.sha256(Path(args.input_fasta).read_bytes()).hexdigest(),
                'command': cmd, 'structures': structures, 'confidence_files': confidence,
                'claim_ceiling': 'Predicted geometry/confidence; no proof of folding or enzyme activity.'}
    (out_dir / 'manifest.json').write_text(json.dumps(manifest, indent=2))
    print(f"Output saved to: {out_dir}")


if __name__ == "__main__":
    main()
