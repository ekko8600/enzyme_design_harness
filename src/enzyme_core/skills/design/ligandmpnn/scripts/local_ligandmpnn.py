#!/usr/bin/env python3
"""Run LigandMPNN, ProteinMPNN, or SolubleMPNN locally."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path


MODEL_CHECKPOINTS = {
    "ligandmpnn": "ligandmpnn_v_32_005_25.pt",
    "proteinmpnn": "proteinmpnn_v_48_020.pt",
    "solublempnn": "solublempnn_v_48_020.pt",
}

def extract_chains_inplace(pdb_file: Path, extract_chains: str) -> None:
    from prody import parsePDB, writePDB

    chains = parsePDB(str(pdb_file), chain=extract_chains.replace(",", ""))
    writePDB(str(pdb_file), chains)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local LigandMPNN-family runner")
    parser.add_argument("--input-pdb", "--pdb-path", dest="input_pdb", required=True)
    parser.add_argument("--ligandmpnn-dir", default=os.environ.get("LIGANDMPNN_DIR"))
    parser.add_argument("--params-str")
    parser.add_argument(
        "--model", choices=sorted(MODEL_CHECKPOINTS),
        default=os.environ.get("MPNN_MODEL_VARIANT", "ligandmpnn"),
        help="Checkpoint family; defaults to the calling Skill's family.",
    )
    parser.add_argument("--num-seq-per-target", type=int)
    parser.add_argument("--sampling-temp")
    parser.add_argument("--require-cuda", action="store_true", help="Fail before execution if CUDA is unavailable; never accept CPU fallback")
    parser.add_argument("--threads", type=int, choices=range(1,5), default=4)
    parser.add_argument("--seed", type=int, default=1729)
    parser.add_argument("--fixed-residues-file", help="JSON list of PDB chain/residue identifiers")
    parser.add_argument("--design-residues-file", help="JSON list of the only residues allowed to change")
    parser.add_argument("--calc-score", action="store_true")
    parser.add_argument("--score-params-str")
    parser.add_argument("--extract-chains")
    parser.add_argument("--out-dir", default="./out/ligandmpnn")
    parser.add_argument("--run-name")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    for key in ["OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"]:
        os.environ[key] = str(args.threads)
    execution_control = {"threads": args.threads, "require_cuda": args.require_cuda}
    if args.require_cuda:
        import torch
        torch.set_num_threads(args.threads)
        if not torch.cuda.is_available():
            raise RuntimeError("CUDA_REQUIRED_CPU_FALLBACK_FORBIDDEN")
        torch.empty(1, device="cuda:0")
        execution_control.update(device="cuda:0", device_name=torch.cuda.get_device_name(0),
                                 cuda_visible_devices=os.environ.get("CUDA_VISIBLE_DEVICES"))
    if not args.ligandmpnn_dir:
        raise SystemExit("Set --ligandmpnn-dir or LIGANDMPNN_DIR to a local LigandMPNN checkout")
    ligandmpnn_dir = Path(args.ligandmpnn_dir).resolve()
    if args.params_str is None:
        model_params = os.environ.get("LIGANDMPNN_MODEL_PARAMS") or os.environ.get("PROTEINMPNN_MODEL_PARAMS")
        if not model_params:
            raise SystemExit("Set PROTEINMPNN_MODEL_PARAMS or LIGANDMPNN_MODEL_PARAMS through enzyme_core/runtime/resources.json")
        checkpoint = Path(model_params) / MODEL_CHECKPOINTS[args.model]
        if not checkpoint.is_file():
            raise SystemExit(f"{args.model} checkpoint is missing: {checkpoint}")
        checkpoint_flag = {"proteinmpnn": "--checkpoint_protein_mpnn", "ligandmpnn": "--checkpoint_ligand_mpnn", "solublempnn": "--checkpoint_soluble_mpnn"}[args.model]
        model_type = {"proteinmpnn": "protein_mpnn", "ligandmpnn": "ligand_mpnn", "solublempnn": "soluble_mpnn"}[args.model]
        args.params_str = f'--seed {args.seed} --model_type {model_type} {checkpoint_flag} "{checkpoint}" --save_stats 1'
    run_py = ligandmpnn_dir / "run.py"
    if not run_py.exists():
        raise FileNotFoundError(f"LigandMPNN run.py not found: {run_py}")

    run_name = args.run_name or datetime.now().strftime("%y%m%d%H%M")
    out_dir = Path(args.out_dir) / run_name
    out_dir.mkdir(parents=True, exist_ok=True)
    input_pdb = out_dir / Path(args.input_pdb).name
    shutil.copy2(args.input_pdb, input_pdb)
    if args.extract_chains:
        extract_chains_inplace(input_pdb, args.extract_chains)

    cmd = [sys.executable, str(run_py), "--pdb_path", str(input_pdb), "--out_folder", str(out_dir)]
    params = shlex.split(args.params_str or "")
    if args.seed < 1:
        raise ValueError("seed must be positive")
    constraints = {}
    import re
    for field, flag in [("fixed_residues_file", "--fixed_residues"), ("design_residues_file", "--redesigned_residues")]:
        source = getattr(args, field)
        if source:
            values = json.loads(Path(source).read_text())
            if not isinstance(values, list) or not values or not all(isinstance(x, str) and re.fullmatch(r"[A-Za-z][1-9][0-9]*", x) for x in values):
                raise ValueError("Invalid or empty constraint residue list")
            constraints[field] = values
            params.extend([flag, " ".join(values)])
    if set(constraints.get("fixed_residues_file", [])) & set(constraints.get("design_residues_file", [])):
        raise ValueError("Fixed and designed residues overlap")
    if args.num_seq_per_target is not None:
        params.extend(["--number_of_batches", str(args.num_seq_per_target)])
    if args.sampling_temp is not None:
        params.extend(["--temperature", args.sampling_temp])
    cmd.extend(params)
    (out_dir / "invocation.json").write_text(json.dumps({"command": cmd, "model": args.model,
        "seed": args.seed, "execution_control": execution_control, "constraints": constraints, "input_sha256": hashlib.sha256(input_pdb.read_bytes()).hexdigest(),
        "claim_ceiling": "Structure-conditioned sequence samples; no experimental activity, stability or solubility validation."}, indent=2) + "\n")
    print("+ " + " ".join(shlex.quote(part) for part in cmd))
    subprocess.run(cmd, cwd=str(ligandmpnn_dir), check=True)

    if args.calc_score:
        score_py = ligandmpnn_dir / "score.py"
        if not score_py.exists():
            raise FileNotFoundError(f"LigandMPNN score.py not found: {score_py}")
        score_args = shlex.split(args.score_params_str or "")
        cmd_score = [sys.executable, str(score_py), "--out_folder", str(out_dir), "--pdb_path", str(input_pdb)]
        cmd_score.extend(score_args)
        print("+ " + " ".join(shlex.quote(part) for part in cmd_score))
        subprocess.run(cmd_score, cwd=str(ligandmpnn_dir), check=True)

    # Stable adapter products hide backend naming and run-directory conventions.
    candidates = sorted((out_dir / "seqs").glob("*.fa")) + sorted((out_dir / "seqs").glob("*.fasta"))
    if len(candidates) != 1:
        raise ValueError("MPNN_SEQUENCE_ARTIFACT_AMBIGUOUS_OR_MISSING")
    stable = Path(args.out_dir)
    for source, name in [(candidates[0], "sequences.fasta"), (out_dir / "invocation.json", "invocation.json"), (input_pdb, "input.pdb")]:
        target = stable / name
        if source.resolve() != target.resolve():shutil.copy2(source,target)
    artifacts = {name: {"path":name,"sha256":hashlib.sha256((stable/name).read_bytes()).hexdigest()}
                 for name in ["sequences.fasta","invocation.json","input.pdb"]}
    (stable/"design_manifest.json").write_text(json.dumps({"schema_version":"enzyme-core/design-artifacts/v1","artifacts":artifacts,"backend_directory":out_dir.relative_to(stable).as_posix(),"method":args.model,"seed":args.seed},indent=2)+"\n")
    print(f"Output saved to: {out_dir}")


if __name__ == "__main__":
    main()
