#!/usr/bin/env python3
"""Run isolated PLIP and CAVER installations without modifying agent packages."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "skills" / "registry.py").is_file()).parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from enzyme_core.support.config import load_config


def runtime_path(name: str) -> Path:
    item = load_config().get("runtime_binaries", {}).get(name, {})
    value = str(item.get("path", "")).strip()
    if not value:
        raise RuntimeError(f"runtime_binaries.{name}.path is not configured")
    path = Path(value)
    if not path.exists():
        raise RuntimeError(f"configured {name} path does not exist: {path}")
    return path


def command_doctor(_: argparse.Namespace) -> int:
    for name in ("plip", "caver", "caver_java"):
        try:
            path = runtime_path(name)
            print(f"{name}: available ({path})")
        except RuntimeError as exc:
            print(f"{name}: unavailable ({exc})")
    return 0


def command_plip(args: argparse.Namespace) -> int:
    plip_root = runtime_path("plip")
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(plip_root) + os.pathsep + environment.get("PYTHONPATH", "")
    command = [sys.executable, "-m", "plip.plipcmd", "-f", str(args.input), "-o", str(args.output), "-x", "-t"]
    if args.pymol:
        command.append("-y")
    args.output.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(command, env=environment, capture_output=True, text=True)
    stdout_path = args.output / "plip_stdout.log"
    stderr_path = args.output / "plip_stderr.log"
    stdout_path.write_text(result.stdout or "", encoding="utf-8")
    stderr_path.write_text(result.stderr or "", encoding="utf-8")
    if result.returncode:
        return result.returncode

    def checksum(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    reports = sorted(args.output.glob("*_report.xml"))
    if len(reports) != 1:
        (args.output / "plip_artifact_error.json").write_text(json.dumps(
            {"error": "EXPECTED_ONE_REPORT_XML", "count": len(reports)}, indent=2) + "\n", encoding="utf-8")
        return 1
    categories = ["hydrophobic_interactions", "hydrogen_bonds", "water_bridges", "salt_bridges",
                  "pi_stacks", "pi_cation_interactions", "halogen_bonds", "metal_complexes"]
    root = ET.parse(reports[0]).getroot()
    components: dict[str, dict[str, object]] = {}
    for site in root.findall("bindingsite"):
        identifiers = site.find("identifiers")
        interactions = site.find("interactions")
        if identifiers is None or interactions is None:
            raise RuntimeError("PLIP_REPORT_BINDING_SITE_SCHEMA_INVALID")
        hetid = (identifiers.findtext("hetid") or "").strip()
        if not hetid:
            raise RuntimeError("PLIP_REPORT_LIGAND_ID_MISSING")
        row = components.setdefault(hetid, {"sites": [], "interaction_counts": {name: 0 for name in categories}})
        counts = {name: (len(interactions.find(name)) if interactions.find(name) is not None else 0)
                  for name in categories}
        row["sites"].append({"chain": (identifiers.findtext("chain") or "").strip(),
                              "position": (identifiers.findtext("position") or "").strip(),
                              "interaction_counts": counts})
        for name, count in counts.items():
            row["interaction_counts"][name] += count
    summary = {"schema_version": "enzyme-core/plip-interaction-summary/v1",
        "input": str(args.input.resolve()), "input_sha256": checksum(args.input),
        "report_xml": reports[0].name, "report_xml_sha256": checksum(reports[0]),
        "components": components,
        "scope": "PLIP geometric interaction counts; zeros are explicit. Counts do not establish binding energy, affinity, or enzyme activity."}

    # If this benchmark input directory carries a frozen variant manifest, copy
    # only the matching source row so computational provenance travels with the result.
    source_manifest = args.provenance_manifest or (args.input.parent / "input_manifest.json")
    if source_manifest.is_file():
        manifest = json.loads(source_manifest.read_text(encoding="utf-8"))
        matches = [row for row in manifest.get("variants", [])
                   if Path(row.get("path", "")).name == args.input.name
                   and row.get("sha256") == checksum(args.input)]
        if len(matches) == 1:
            summary["input_provenance"] = matches[0]
            summary["source_manifest_sha256"] = checksum(source_manifest)
    summary_path = args.output / "interaction_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest = {"schema_version": "enzyme-core/plip-run-manifest/v1", "command": command,
        "backend_command": command,
        "adapter_invocation": {"argv":list(sys.argv),"input":str(args.input.resolve()),
            "provenance_manifest":str(source_manifest.resolve()) if source_manifest.is_file() else None,
            "provenance_explicitly_bound":args.provenance_manifest is not None,
            "source_manifest_sha256":checksum(source_manifest) if source_manifest.is_file() else None},
        "returncode": result.returncode, "method_status": "COMPLETED_WITH_CONTACTS" if any(sum(v["interaction_counts"].values()) for v in components.values()) else "COMPLETED_NO_CONTACTS", "input_sha256": checksum(args.input),
        "report_xml_sha256": checksum(reports[0]),
        "artifacts": {path.name: checksum(path) for path in sorted(args.output.iterdir()) if path.is_file()}}
    (args.output / "plip_run_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result.returncode


def command_caver(args: argparse.Namespace) -> int:
    caver_jar = runtime_path("caver")
    java = runtime_path("caver_java")
    caver_home = caver_jar.parent
    pdb_dir = args.pdb_dir.resolve()
    output = args.output.resolve()
    if not pdb_dir.is_dir():
        raise RuntimeError(f"CAVER --pdb-dir must be a directory: {pdb_dir}")
    output.mkdir(parents=True, exist_ok=True)
    command = [str(java), "-jar", str(caver_jar), "-home", str(caver_home), "-pdb", str(pdb_dir), "-out", str(output)]
    config = args.config or caver_jar.parents[1] / "user_guide" / "config_default.txt"
    if not config.is_file():
        raise RuntimeError(f"CAVER configuration is missing: {config}")
    command.extend(["-conf", str(config.resolve())])
    # Preserve the exact child streams inside the result collection.  The
    # Harness otherwise keeps only the supervisor stderr outside the artifact,
    # which makes warnings impossible to audit from the frozen result bundle.
    result = subprocess.run(command, cwd=output, capture_output=True, text=True)
    (output / "caver_stdout.log").write_text(result.stdout or "", encoding="utf-8")
    (output / "caver_stderr.log").write_text(result.stderr or "", encoding="utf-8")
    diagnostic = (result.stdout or "") + "\n" + (result.stderr or "")
    status = "FAILED" if result.returncode else "INCOMPLETE" if "computation was not finished" in diagnostic else "COMPLETED"
    if not result.returncode and "Starting point is more narrow" in diagnostic:
        status = "NO_ADMISSIBLE_START"
    tables = sorted(output.rglob("tunnel_characteristics.csv"))
    if status == "COMPLETED" and not tables:status = "REQUIRED_OUTPUTS_MISSING"
    elif status == "COMPLETED" and all(len(p.read_text().strip().splitlines())<=1 for p in tables):status = "COMPLETED_NO_CHANNELS"
    manifest = {"schema_version":"enzyme-core/caver-run-manifest/v1", "backend_command":command,
        "returncode":result.returncode,"method_status":status,"config_sha256":hashlib.sha256(config.read_bytes()).hexdigest(),
        "input_structures":{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(pdb_dir.glob("*.pdb"))},
        "scope":"NO_ADMISSIBLE_START and INCOMPLETE are diagnostics, not an exhaustive search with zero channels."}
    (output/"caver_run_manifest.json").write_text(json.dumps(manifest,indent=2)+"\n")
    return result.returncode or (1 if status=="REQUIRED_OUTPUTS_MISSING" else 0)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    doctor = commands.add_parser("doctor")
    doctor.set_defaults(func=command_doctor)
    plip = commands.add_parser("plip")
    plip.add_argument("--input", type=Path, required=True)
    plip.add_argument("--output", type=Path, required=True)
    plip.add_argument("--provenance-manifest", type=Path)
    plip.add_argument("--pymol", action="store_true")
    plip.set_defaults(func=command_plip)
    caver = commands.add_parser("caver")
    caver.add_argument("--pdb-dir", type=Path, required=True)
    caver.add_argument("--output", type=Path, required=True)
    caver.add_argument("--config", type=Path)
    caver.set_defaults(func=command_caver)
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
