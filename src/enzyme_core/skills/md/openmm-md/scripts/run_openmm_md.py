"""Convenience CLI for OpenMM protein-ligand MD workflows.

This wrapper keeps the skill invocation stable while delegating chemistry-heavy
work to the bundled simulate.py module.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
from pathlib import Path

STEP_SIZE_PS = 0.002
STEPS_PER_NS = int(1000 / STEP_SIZE_PS)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run OpenMM protein-ligand MD")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--pdb-id", help="RCSB/PDB-REDO ID to download")
    source.add_argument("--protein-pdb", help="Local protein or complex PDB/mmCIF file")
    source.add_argument(
        "--amber-prmtop",
        help="Pre-parameterized Amber topology; preserves heme/Compound I/multiple ligands",
    )
    parser.add_argument(
        "--amber-coordinates",
        help="Amber rst7/inpcrd coordinates (required with --amber-prmtop)",
    )

    parser.add_argument("--ligand-id", help="Three-letter ligand residue ID in PDB")
    parser.add_argument("--ligand-chain", help="Ligand chain ID for extraction")
    parser.add_argument("--ligand-sdf", help="Prepared ligand SDF/MOL file")
    parser.add_argument("--protein-only", action="store_true", help="Run a protein-only system")
    parser.add_argument(
        "--strip-structure-ions",
        action="store_true",
        help="Explicitly remove supported crystallographic monatomic ions; preserved by default",
    )
    parser.add_argument("--out-dir", default="out/openmm-md", help="Output directory")
    parser.add_argument("--run-name", default=None, help="Output prefix name")
    parser.add_argument("--num-steps", type=int, default=5000, help="Production MD steps")
    parser.add_argument(
        "--simulation-time-ns",
        type=float,
        default=None,
        help="Production MD time in ns; overrides --num-steps using a 2 fs timestep",
    )
    parser.add_argument("--equilibration-steps", type=int, default=1000)
    parser.add_argument("--temperature", type=float, default=300.0)
    parser.add_argument("--use-solvent", action="store_true")
    parser.add_argument(
        "--platform",
        default="auto",
        choices=["auto", "CUDA", "OpenCL", "CPU", "Reference"],
        help="Preferred OpenMM platform. 'auto' tries GPU first and falls back to CPU/Reference.",
    )
    parser.add_argument(
        "--disable-platform-fallback",
        action="store_true",
        help="Fail immediately if the preferred platform cannot create a context.",
    )
    parser.add_argument("--minimize-only", action="store_true")
    parser.add_argument("--minimize-pdb", action="store_true")
    parser.add_argument("--checkpoint", help="Checkpoint output path")
    parser.add_argument("--restart-from", help="Restart from an OpenMM checkpoint")
    parser.add_argument(
        "--segment-ns",
        type=float,
        default=None,
        help="Run production in restartable segments of this many ns",
    )
    parser.add_argument(
        "--report-interval-ps",
        type=float,
        default=None,
        help="Reporter/checkpoint interval in ps",
    )
    parser.add_argument("--use-pdb-redo", action="store_true")
    parser.add_argument(
        "--mutation",
        action="append",
        default=[],
        help="PDBFixer mutation as FROM-RESNUM-TO-CHAINS, e.g. TYR-65-ILE-A",
    )
    parser.add_argument("--decoy-smiles", default=None)
    parser.add_argument(
        "--scoring-tool",
        default="none",
        choices=["none", "gnina"],
        help="Use gnina for heuristic affinity scoring, or none to skip scoring",
    )
    return parser.parse_args()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _record(path: str | Path) -> dict:
    resolved = Path(path).resolve()
    return {
        "path": str(resolved),
        "sha256": _sha256(resolved),
        "size_bytes": resolved.stat().st_size,
    }


def _artifact_records(results: dict) -> dict:
    return {
        key: _record(value)
        for key, value in results.items()
        if isinstance(value, str) and Path(value).is_file()
    }


def _classify_atoms(topology) -> dict:
    water_names = {"HOH", "WAT", "TIP3", "TIP3P", "SPC", "OPC"}
    ion_names = {
        "NA", "CL", "K", "CA", "MG", "ZN", "CS", "LI", "RB", "F", "BR", "I"
    }
    heme_names = {"HEM", "HEME", "HEC", "HEO", "CPI", "CP1"}
    amino_acids = {
        "ALA", "ARG", "ASN", "ASP", "CYS", "GLN", "GLU", "GLY", "HIS", "ILE",
        "LEU", "LYS", "MET", "PHE", "PRO", "SER", "THR", "TRP", "TYR", "VAL",
        "ASH", "GLH", "HID", "HIE", "HIP", "CYM", "CYX", "LYN",
    }
    groups = {"protein": [], "heme": [], "water": [], "ions": [], "substrates": {}}
    for residue in topology.residues():
        name = residue.name.upper()
        indices = [atom.index for atom in residue.atoms()]
        if name in amino_acids:
            groups["protein"].extend(indices)
        elif name in heme_names:
            groups["heme"].extend(indices)
        elif name in water_names:
            groups["water"].extend(indices)
        elif name in ion_names:
            groups["ions"].extend(indices)
        else:
            key = f"{name}:{residue.chain.id}:{residue.id}"
            groups["substrates"][key] = indices
    groups["receptor"] = sorted(groups["protein"] + groups["heme"])
    groups["all_substrates"] = sorted(
        index for indices in groups["substrates"].values() for index in indices
    )
    return groups


def run_preparameterized_amber(args: argparse.Namespace, out_dir: Path, run_name: str) -> dict:
    if not args.amber_coordinates:
        raise SystemExit("--amber-coordinates is required with --amber-prmtop")
    if args.ligand_sdf or args.ligand_id or args.mutation:
        raise SystemExit(
            "Amber mode uses the pre-parameterized system unchanged; ligand extraction "
            "and PDBFixer mutation options are not allowed"
        )
    from openmm import (
        LangevinMiddleIntegrator,
        MonteCarloBarostat,
        Platform,
        XmlSerializer,
        __version__ as openmm_version,
        unit,
    )
    from openmm.app import (
        AmberInpcrdFile,
        AmberPrmtopFile,
        CheckpointReporter,
        DCDReporter,
        HBonds,
        PDBFile,
        Simulation,
        StateDataReporter,
    )

    prmtop_path = Path(args.amber_prmtop).resolve()
    coordinates_path = Path(args.amber_coordinates).resolve()
    prmtop = AmberPrmtopFile(str(prmtop_path))
    coordinates = AmberInpcrdFile(str(coordinates_path))
    nonbonded_method = (
        __import__("openmm.app", fromlist=["PME"]).PME
        if coordinates.boxVectors is not None
        else __import__("openmm.app", fromlist=["NoCutoff"]).NoCutoff
    )
    system = prmtop.createSystem(
        nonbondedMethod=nonbonded_method,
        nonbondedCutoff=1.0 * unit.nanometer,
        constraints=HBonds,
    )
    if coordinates.boxVectors is not None:
        system.setDefaultPeriodicBoxVectors(*coordinates.boxVectors)
        system.addForce(
            MonteCarloBarostat(1.0 * unit.bar, args.temperature * unit.kelvin, 25)
        )
    integrator = LangevinMiddleIntegrator(
        args.temperature * unit.kelvin,
        1.0 / unit.picosecond,
        STEP_SIZE_PS * unit.picoseconds,
    )
    preferred = args.platform
    available = [Platform.getPlatform(i).getName() for i in range(Platform.getNumPlatforms())]
    candidates = available if preferred == "auto" else [preferred]
    if not args.disable_platform_fallback:
        candidates += [name for name in available if name not in candidates]
    last_error = None
    simulation = None
    active_platform = None
    for candidate in candidates:
        try:
            simulation = Simulation(
                prmtop.topology,
                system,
                integrator,
                Platform.getPlatformByName(candidate),
            )
            active_platform = candidate
            break
        except Exception as exc:
            last_error = exc
    if simulation is None:
        raise RuntimeError(f"could not create OpenMM context: {last_error}")
    simulation.context.setPositions(coordinates.positions)
    if coordinates.boxVectors is not None:
        simulation.context.setPeriodicBoxVectors(*coordinates.boxVectors)

    prefix = out_dir / run_name
    full_topology = prefix.with_name(prefix.name + "_complex.pdb")
    system_xml = prefix.with_name(prefix.name + "_system.xml")
    atom_map = prefix.with_name(prefix.name + "_atom_map.json")
    groups_json = prefix.with_name(prefix.name + "_atom_groups.json")
    minimized = prefix.with_name(prefix.name + "_minimized.pdb")
    trajectory = prefix.with_name(prefix.name + "_traj.dcd")
    state_file = prefix.with_name(prefix.name + "_state.tsv")
    checkpoint = Path(args.checkpoint).resolve() if args.checkpoint else prefix.with_name(
        prefix.name + "_checkpoint.chk"
    )
    with full_topology.open("w", encoding="utf-8") as handle:
        PDBFile.writeFile(prmtop.topology, coordinates.positions, handle, keepIds=True)
    system_xml.write_text(XmlSerializer.serialize(system), encoding="utf-8")
    mapping = [
        {
            "index": atom.index,
            "name": atom.name,
            "element": atom.element.symbol if atom.element else None,
            "residue_name": atom.residue.name,
            "residue_id": atom.residue.id,
            "chain_id": atom.residue.chain.id,
        }
        for atom in prmtop.topology.atoms()
    ]
    atom_map.write_text(json.dumps(mapping, indent=2) + "\n", encoding="utf-8")
    groups_json.write_text(
        json.dumps(_classify_atoms(prmtop.topology), indent=2) + "\n",
        encoding="utf-8",
    )
    if args.restart_from:
        simulation.loadCheckpoint(args.restart_from)
    else:
        simulation.minimizeEnergy()
    state = simulation.context.getState(getPositions=True, enforcePeriodicBox=False)
    with minimized.open("w", encoding="utf-8") as handle:
        PDBFile.writeFile(prmtop.topology, state.getPositions(), handle, keepIds=True)
    num_steps = (
        int(round(args.simulation_time_ns * STEPS_PER_NS))
        if args.simulation_time_ns is not None
        else args.num_steps
    )
    results = {
        "complex_pdb": str(full_topology),
        "minimized_pdb": str(minimized),
        "system_xml": str(system_xml),
        "atom_map_json": str(atom_map),
        "atom_groups_json": str(groups_json),
        "openmm_platform": active_platform,
    }
    if not args.minimize_only:
        if not args.restart_from and args.equilibration_steps:
            simulation.context.setVelocitiesToTemperature(
                args.temperature * unit.kelvin
            )
            simulation.step(args.equilibration_steps)
        interval = max(
            1,
            int(round(args.report_interval_ps / STEP_SIZE_PS))
            if args.report_interval_ps
            else max(1, num_steps // 100),
        )
        simulation.reporters.append(DCDReporter(str(trajectory), interval, enforcePeriodicBox=False))
        simulation.reporters.append(CheckpointReporter(str(checkpoint), interval))
        simulation.reporters.append(
            StateDataReporter(
                str(state_file),
                interval,
                step=True,
                potentialEnergy=True,
                temperature=True,
                separator="\t",
            )
        )
        segment_steps = (
            max(1, int(round(args.segment_ns * STEPS_PER_NS)))
            if args.segment_ns is not None
            else num_steps
        )
        remaining = num_steps
        while remaining > 0:
            simulation.step(min(segment_steps, remaining))
            simulation.saveCheckpoint(str(checkpoint))
            remaining -= min(segment_steps, remaining)
        results.update(
            {
                "traj_dcd": str(trajectory),
                "state_tsv": str(state_file),
                "checkpoint": str(checkpoint),
            }
        )
    return {
        "prepared": {
            "mode": "preparameterized_amber",
            "prmtop": _record(prmtop_path),
            "coordinates": _record(coordinates_path),
        },
        "results": results,
        "analysis_topology": str(prmtop_path),
        "atom_groups": read_json(groups_json),
        "software": {
            "openmm": openmm_version,
            "python": platform.python_version(),
        },
        "periodic_box": {
            "enabled": coordinates.boxVectors is not None,
            "vectors_nm": (
                [
                    [
                        float(component.value_in_unit(unit.nanometer))
                        for component in vector
                    ]
                    for vector in coordinates.boxVectors
                ]
                if coordinates.boxVectors is not None
                else None
            ),
        },
        "parameters": {
            "temperature_K": args.temperature,
            "timestep_fs": STEP_SIZE_PS * 1000.0,
            "nonbonded_method": "PME" if coordinates.boxVectors is not None else "NoCutoff",
            "constraints": "HBonds",
        },
    }


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def convert_cif_to_pdb(input_path: str, out_dir: Path) -> str:
    """Convert mmCIF/CIF to PDB using OpenMM's PDBxFile reader."""
    from openmm.app import PDBFile, PDBxFile

    in_path = Path(input_path)
    out_path = out_dir / f"{in_path.stem}.pdb"
    try:
        cif = PDBxFile(str(in_path))
        with open(out_path, "w", encoding="utf-8") as out:
            PDBFile.writeFile(cif.topology, cif.positions, out, keepIds=True)
    except Exception as exc:
        raise SystemExit(f"Failed to convert CIF/mmCIF to PDB: {input_path}: {exc}") from exc
    return str(out_path)


def normalize_structure_path(input_path: str, out_dir: Path) -> str:
    suffix = Path(input_path).suffix.lower()
    if suffix in {".cif", ".mmcif"}:
        return convert_cif_to_pdb(input_path, out_dir)
    return input_path


def main() -> None:
    args = parse_args()
    from simulate import (
        OPENMM_DEFAULT_LIGAND_ID,
        get_pdb_and_extract_ligand,
        prepare_protein,
        simulate,
    )

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.run_name:
        run_name = args.run_name
    elif args.pdb_id:
        run_name = args.pdb_id
    elif args.protein_pdb:
        run_name = Path(args.protein_pdb).stem
    else:
        run_name = Path(args.amber_prmtop).stem
    if args.amber_prmtop:
        manifest = run_preparameterized_amber(args, out_dir, run_name)
        manifest["schema_version"] = "openmm-md-run/v2"
        manifest["args"] = vars(args)
        manifest["artifacts"] = _artifact_records(manifest["results"])
        manifest_path = out_dir / f"{run_name}_manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        print(json.dumps(manifest, indent=2))
        return
    output_prefix = str(out_dir / run_name)
    num_steps = (
        int(round(args.simulation_time_ns * STEPS_PER_NS))
        if args.simulation_time_ns is not None
        else args.num_steps
    )
    segment_steps = (
        int(round(args.segment_ns * STEPS_PER_NS))
        if args.segment_ns is not None
        else None
    )
    reporting_interval = (
        max(1, int(round(args.report_interval_ps / STEP_SIZE_PS)))
        if args.report_interval_ps is not None
        else None
    )
    # Keep the manifest's declared frame count aligned with the default used
    # by simulate().  Without this normalization, runs with no explicit
    # report interval were written as frame_count=0 even though DCDReporter
    # emitted up to 100 frames.
    if reporting_interval is None:
        reporting_interval = max(1, min(10_000, num_steps // 100))

    if args.simulation_time_ns and args.simulation_time_ns >= 100 and not args.use_solvent:
        print("Enabling explicit solvent for >=100 ns production MD.")
        args.use_solvent = True

    if args.scoring_tool == "none":
        print(
            "Affinity scoring disabled. Pass --scoring-tool gnina when gnina and "
            "obabel are installed."
        )

    if args.ligand_sdf:
        if args.pdb_id:
            prepared = get_pdb_and_extract_ligand(
                args.pdb_id,
                ligand_id=None,
                out_dir=str(out_dir),
                use_pdb_redo=args.use_pdb_redo,
                minimize_pdb=args.minimize_pdb,
                mutations=args.mutation,
                platform_name=args.platform,
                allow_platform_fallback=not args.disable_platform_fallback,
                preserve_structure_ions=not args.strip_structure_ions,
            )
            protein_pdb = prepared["pdb"]
        else:
            protein_input = normalize_structure_path(args.protein_pdb, out_dir)
            protein_pdb = str(out_dir / f"{Path(args.protein_pdb).stem}_fixed.pdb")
            preparation = prepare_protein(
                protein_input,
                protein_pdb,
                minimize_pdb=args.minimize_pdb,
                mutations=args.mutation,
                platform_name=args.platform,
                allow_platform_fallback=not args.disable_platform_fallback,
                preserve_structure_ions=not args.strip_structure_ions,
            )
        ligand_sdf = args.ligand_sdf
        if not args.pdb_id:
            prepared = {"pdb": protein_pdb, **preparation}
        prepared["sdf"] = ligand_sdf
    else:
        if args.protein_only:
            if args.ligand_id or args.ligand_sdf:
                raise SystemExit("--protein-only cannot be combined with ligand inputs")
            protein_source = args.pdb_id or normalize_structure_path(args.protein_pdb, out_dir)
            prepared = get_pdb_and_extract_ligand(
                protein_source, ligand_id=None, out_dir=str(out_dir),
                use_pdb_redo=args.use_pdb_redo, minimize_pdb=args.minimize_pdb,
                mutations=args.mutation, platform_name=args.platform,
                allow_platform_fallback=not args.disable_platform_fallback,
                preserve_structure_ions=not args.strip_structure_ions,
            )
            ligand_sdf = None
        elif not args.ligand_id:
            raise SystemExit("--ligand-id is required unless --ligand-sdf is provided")
        else:
            pdb_source = args.pdb_id or normalize_structure_path(args.protein_pdb, out_dir)
            prepared = get_pdb_and_extract_ligand(
                pdb_source,
                ligand_id=args.ligand_id,
                ligand_chain=args.ligand_chain,
                out_dir=str(out_dir),
                use_pdb_redo=args.use_pdb_redo,
                minimize_pdb=args.minimize_pdb,
                mutations=args.mutation,
                platform_name=args.platform,
                allow_platform_fallback=not args.disable_platform_fallback,
                preserve_structure_ions=not args.strip_structure_ions,
            )
            ligand_sdf = prepared["sdf"]

    results = simulate(
        prepared["pdb"],
        ligand_sdf,
        output_prefix,
        num_steps,
        use_solvent=args.use_solvent,
        decoy_smiles=args.decoy_smiles,
        minimize_only=args.minimize_only,
        temperature=args.temperature,
        equilibration_steps=args.equilibration_steps,
        reporting_interval=reporting_interval,
        scoring_tool=args.scoring_tool,
        checkpoint=args.checkpoint,
        restart_from=args.restart_from,
        segment_steps=segment_steps,
        platform_name=args.platform,
        allow_platform_fallback=not args.disable_platform_fallback,
    )

    manifest = {
        "prepared": prepared,
        "results": results,
        "schema_version": "openmm-md-run/v2",
        "analysis_topology": results.get("complex_pdb"),
        "args": vars(args),
        "derived": {
            "num_steps": num_steps,
            "step_size_ps": STEP_SIZE_PS,
            "simulation_time_ns": num_steps / STEPS_PER_NS,
            "segment_steps": segment_steps,
            "reporting_interval_steps": reporting_interval,
            "reporting_interval_ps": (
                reporting_interval * STEP_SIZE_PS
                if reporting_interval is not None
                else None
            ),
            "trajectory_duration_ps": num_steps * STEP_SIZE_PS,
            "trajectory_frame_count": (num_steps // reporting_interval) if reporting_interval else 0,
            "time_unit": "ps",
        },
        "inputs": {
            key: _record(value)
            for key, value in {
                "protein": args.protein_pdb,
                "ligand": args.ligand_sdf,
            }.items()
            if value and Path(value).is_file()
        },
    }
    try:
        from openmm import __version__ as openmm_version
        from openmm.app import PDBFile

        manifest["atom_groups"] = _classify_atoms(
            PDBFile(results["complex_pdb"]).topology
        )
        manifest["software"] = {
            "openmm": openmm_version,
            "python": platform.python_version(),
        }
    except (ImportError, OSError, KeyError):
        manifest["atom_groups"] = {"status": "not_available"}
    manifest["artifacts"] = _artifact_records(results)
    manifest_path = out_dir / f"{run_name}_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
