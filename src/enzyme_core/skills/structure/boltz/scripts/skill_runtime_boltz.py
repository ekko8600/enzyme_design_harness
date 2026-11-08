#!/usr/bin/env python3
"""Typed SkillRuntime adapter for a local Boltz structure-prediction run."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("--input", type=Path, required=True); parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(); payload = json.loads(args.input.read_text(encoding="utf-8")); roles = payload["roles"]
    sequence = roles.get("reference_sequence"); msa = roles.get("msa"); config = roles.get("structure_prediction_config")
    if not isinstance(sequence, str) or not sequence.strip(): raise SystemExit("reference_sequence is required")
    if not isinstance(msa, str) or not msa.strip(): raise SystemExit("msa is required; the adapter never enables a remote MSA server")
    if not isinstance(config, dict): raise SystemExit("structure_prediction_config is required")
    cache = config.get("checkpoint_root") or os.environ.get("BOLTZ_CHECKPOINT")
    if not isinstance(cache, str) or not cache: raise SystemExit("BOLTZ_CHECKPOINT_UNCONFIGURED")
    root = args.output.parent; input_yaml = root / "boltz_input.yaml"; msa_path = root / "input.a3m"; output_dir = root / "boltz_output"
    msa_path.write_text(msa, encoding="utf-8")
    input_yaml.write_text("sequences:\n  - protein:\n      id: A\n      sequence: " + sequence.strip().upper() + "\n      msa: " + str(msa_path) + "\n", encoding="utf-8")
    command = [sys.executable, "-m", "boltz.main", "predict", str(input_yaml), "--out_dir", str(output_dir), "--cache", cache, "--accelerator", str(config.get("accelerator", "gpu")), "--devices", str(config.get("devices", 1)), "--seed", str(config.get("seed", 42))]
    subprocess.run(command, check=True)
    models = sorted(output_dir.rglob("*.cif")); confidence = sorted(output_dir.rglob("*confidence*.json"))
    if not models: raise SystemExit("BOLTZ_STRUCTURE_MODEL_MISSING")
    args.output.write_text(json.dumps({"observation": {"structure_model_path": str(models[0].relative_to(root)), "quality_paths": [str(path.relative_to(root)) for path in confidence]}, "interpretation": {"property": "predicted_structure_model", "relation": "computationally_predicted", "scope": "reference_sequence", "strength": "descriptive"}, "provenance": {"runner": "boltz", "checkpoint_root": cache}}, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__": main()
