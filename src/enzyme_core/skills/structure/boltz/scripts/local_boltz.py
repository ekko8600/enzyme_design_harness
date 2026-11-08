#!/usr/bin/env python3
"""Run Boltz locally through the boltz CLI."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
from datetime import datetime
from itertools import groupby
from pathlib import Path

ALLOWED_AAS = "ACDEFGHIKLMNPQRSTVWY"
DEFAULT_PARAMS = ""
PROTEIN_LIGAND_TEMPLATE = """sequences:
  - protein:
      id: A
      sequence: PROTEIN_SEQUENCE
  - ligand:
      id: L
      smiles: LIGAND_SMILES
"""


def fasta_iter(path: str):
    with open(path, "r", encoding="utf-8") as handle:
        faiter = (x[1] for x in groupby(handle, lambda line: line.startswith(">")))
        for header in faiter:
            header_text = next(header)[1:].strip()
            seq = "".join(line.strip() for line in next(faiter))
            yield header_text, seq


def fasta_to_yaml(input_faa: str) -> str:
    import yaml

    chains = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    yaml_dict = {"sequences": []}
    rx = re.compile(r"^([A-Z])\|([^|]+)\|?(.*)$")

    for n, (seq_id, seq) in enumerate(fasta_iter(input_faa)):
        if n >= len(chains):
            raise NotImplementedError(">26 chains not supported")
        match = rx.search(seq_id)
        if match:
            chain_id = match.group(1).upper()
            entity_type = match.group(2).lower()
        else:
            chain_id = chains[n]
            entity_type = "protein"
        if entity_type not in {"protein", "dna", "rna"}:
            raise NotImplementedError(f"Entity type {entity_type} not supported")
        if entity_type == "protein" and not all(aa.upper() in ALLOWED_AAS for aa in seq):
            raise ValueError(f"Invalid amino-acid sequence: {seq}")
        yaml_dict["sequences"].append({entity_type: {"id": chain_id, "sequence": seq}})
    return yaml.dump(yaml_dict, sort_keys=False)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local Boltz runner")
    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument("--input-faa")
    input_group.add_argument("--input-yaml")
    parser.add_argument("--params-str", default=DEFAULT_PARAMS)
    parser.add_argument("--seed", type=int, default=1729)
    parser.add_argument("--recycling-steps", type=int, default=3)
    parser.add_argument("--diffusion-samples", type=int, default=1)
    parser.add_argument("--sampling-steps", type=int, default=200)
    parser.add_argument("--use-potentials", action="store_true", help="Native Boltz2 physical/chirality guidance during generation")
    parser.add_argument("--accelerator", choices=["gpu", "cpu"], default="gpu")
    parser.add_argument("--devices", type=int, choices=[1], default=1)
    parser.add_argument("--model", choices=["boltz2"], default="boltz2")
    parser.add_argument("--num-workers", type=int, choices=[0, 1, 2], default=0)
    parser.add_argument("--offline", action="store_true", help="Require explicit local MSA for every protein")
    parser.add_argument("--cache", default=os.environ.get("BOLTZ_CHECKPOINT", "./model-cache/boltz"))
    parser.add_argument("--out-dir", default="./out/boltz")
    parser.add_argument("--run-name")
    parser.add_argument("--dry-run", action="store_true", help="Validate inputs and write the manifest without launching Boltz.")
    parser.add_argument("--show-protein-ligand-template", action="store_true", help="Print the supported protein-ligand YAML template and exit.")
    return parser


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_manifest(out_dir: Path, input_path: Path, command: list[str], *, dry_run: bool) -> None:
    manifest = {
        "schema_version": "enzyme-design-skills/run-manifest/v1",
        "skill": "boltz",
        "input": {"path": str(input_path.resolve()), "sha256": sha256_file(input_path)},
        "command": command,
        "dry_run": dry_run,
        "limitations": [
            "Boltz output is computational evidence and is not experimental validation.",
            "Ligand pose and affinity require independent review or validation.",
        ],
    }
    (out_dir / "run_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def validate_yaml(path: Path) -> None:
    """Validate the small input contract before an expensive Boltz invocation."""
    import yaml
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"invalid Boltz YAML: {exc}") from exc
    sequences = document.get("sequences") if isinstance(document, dict) else None
    if not isinstance(sequences, list) or not sequences:
        raise ValueError("Boltz YAML requires a non-empty sequences list")
    for index, item in enumerate(sequences):
        if not isinstance(item, dict) or len(item) != 1:
            raise ValueError(f"sequence item {index} must contain exactly one entity")
        kind, payload = next(iter(item.items()))
        if kind not in {"protein", "dna", "rna", "ligand"} or not isinstance(payload, dict):
            raise ValueError(f"unsupported sequence entity at item {index}: {kind!r}")
        if not str(payload.get("id", "")).strip():
            raise ValueError(f"sequence item {index} has no id")
        if kind == "protein":
            sequence = str(payload.get("sequence", "")).strip().upper()
            if not sequence or not set(sequence).issubset(ALLOWED_AAS):
                raise ValueError(f"protein item {index} has an invalid amino-acid sequence")
        elif kind == "ligand" and not (str(payload.get("smiles", "")).strip() or str(payload.get("ccd", "")).strip()):
            raise ValueError(f"ligand item {index} requires smiles or ccd")


def main() -> None:
    args = build_parser().parse_args()
    if args.show_protein_ligand_template:
        if args.input_faa or args.input_yaml:
            raise SystemExit("--show-protein-ligand-template cannot be combined with an input")
        print(PROTEIN_LIGAND_TEMPLATE, end="")
        return
    if bool(args.input_faa) == bool(args.input_yaml):
        raise SystemExit("Provide exactly one of --input-faa or --input-yaml")

    run_name = args.run_name or datetime.now().strftime("%y%m%d%H%M")
    out_dir = Path(args.out_dir) / run_name
    out_dir.mkdir(parents=True, exist_ok=True)
    cache = Path(args.cache)

    input_path = Path(args.input_yaml) if args.input_yaml else out_dir / "input.yaml"
    if args.input_faa:
        input_path.write_text(fasta_to_yaml(args.input_faa), encoding="utf-8")
    validate_yaml(input_path)
    if args.params_str:
        raise ValueError("OPAQUE_BACKEND_PARAMETERS_NOT_SUPPORTED: use named Boltz options")
    if args.seed < 1 or args.recycling_steps < 1 or args.diffusion_samples < 1 or args.sampling_steps < 1:
        raise ValueError("seed/recycling_steps/diffusion_samples must be positive")
    if args.offline:
        import yaml
        document = yaml.safe_load(input_path.read_text())
        for entity in document['sequences']:
            if 'protein' in entity:
                msa = entity['protein'].get('msa')
                if not msa or msa == 'empty' or not Path(msa).is_file():
                    raise ValueError('OFFLINE_PREDICTION_REQUIRES_FROZEN_MSA')

    # The package's stable module entrypoint is available in the configured
    # environment even where a shell-level ``boltz`` script is not on PATH.
    cmd = [sys.executable, "-m", "boltz.main", "predict", str(input_path), "--out_dir", str(out_dir), "--cache", str(cache)]
    cmd.extend(["--accelerator", args.accelerator, "--devices", str(args.devices), "--model", args.model,
                "--num_workers", str(args.num_workers), "--sampling_steps", str(args.sampling_steps),
                "--seed", str(args.seed), "--recycling_steps", str(args.recycling_steps), "--diffusion_samples", str(args.diffusion_samples)])
    if args.use_potentials:cmd.append('--use_potentials')
    write_manifest(out_dir, input_path, cmd, dry_run=args.dry_run)
    print("+ " + " ".join(shlex.quote(part) for part in cmd))
    if args.dry_run:
        print(f"Validated input; manifest saved to: {out_dir / 'run_manifest.json'}")
        return
    cache.mkdir(parents=True, exist_ok=True)
    subprocess.run(cmd, check=True)
    print(f"Output saved to: {out_dir}")


if __name__ == "__main__":
    main()
