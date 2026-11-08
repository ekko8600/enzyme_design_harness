#!/usr/bin/env python3
"""OpenMM-only protein-complex binding energy proxy.

The script computes:

    delta_e_binding = E_complex - E_receptor - E_partner

Use the value for relative ranking within a consistently prepared design set.
It is not an experimental binding free energy.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


STANDARD_WATER_NAMES = {"HOH", "WAT", "TIP3", "SOL"}


@dataclass
class ScoreJob:
    name: str
    complex_pdb: str | None = None
    receptor_chains: str | None = None
    partner_chains: str | None = None
    receptor_pdb: str | None = None
    partner_pdb: str | None = None


def parse_chain_set(value: str | None) -> set[str]:
    if not value:
        return set()
    chains: set[str] = set()
    for token in str(value).replace(";", ",").split(","):
        token = token.strip()
        if not token:
            continue
        chains.add(token)
    return chains


def load_openmm() -> tuple[Any, Any, Any]:
    try:
        import openmm
        import openmm.app as app
        import openmm.unit as unit
    except ImportError as exc:
        raise SystemExit("ERROR: OpenMM is required. Install with: conda install -c conda-forge openmm numpy") from exc
    return openmm, app, unit


def select_platform(openmm: Any, requested: str) -> Any:
    if requested != "auto":
        return openmm.Platform.getPlatformByName(requested)
    for name in ("CUDA", "OpenCL", "CPU", "Reference"):
        try:
            return openmm.Platform.getPlatformByName(name)
        except Exception:
            continue
    raise RuntimeError("No usable OpenMM platform found")


def atom_chain_id(atom: Any) -> str:
    return str(atom.residue.chain.id)


def chain_ids(topology: Any) -> list[str]:
    return [str(chain.id) for chain in topology.chains()]


def subset_topology(topology: Any, positions: Any, selected_chains: set[str], app: Any) -> tuple[Any, list[Any]]:
    new_topology = app.Topology()
    atom_map: dict[Any, Any] = {}
    new_positions: list[Any] = []
    selected_atoms = []
    for atom in topology.atoms():
        if atom_chain_id(atom) in selected_chains:
            selected_atoms.append(atom)
    selected_atom_set = set(selected_atoms)

    for chain in topology.chains():
        if str(chain.id) not in selected_chains:
            continue
        new_chain = new_topology.addChain(chain.id)
        for residue in chain.residues():
            new_residue = new_topology.addResidue(residue.name, new_chain, residue.id, residue.insertionCode)
            for atom in residue.atoms():
                if atom not in selected_atom_set:
                    continue
                new_atom = new_topology.addAtom(atom.name, atom.element, new_residue, atom.id)
                atom_map[atom] = new_atom
                new_positions.append(positions[atom.index])

    for bond in topology.bonds():
        atom1, atom2 = bond
        if atom1 in atom_map and atom2 in atom_map:
            new_topology.addBond(atom_map[atom1], atom_map[atom2], getattr(bond, "type", None), getattr(bond, "order", None))
    new_topology.setPeriodicBoxVectors(topology.getPeriodicBoxVectors())
    return new_topology, new_positions


def make_forcefield(app: Any, implicit_solvent: str) -> Any:
    solvent_files = {
        "obc1": "implicit/obc1.xml",
        "obc2": "implicit/obc2.xml",
        "gbn": "implicit/gbn.xml",
        "gbn2": "implicit/gbn2.xml",
        "none": None,
    }
    solvent_file = solvent_files[implicit_solvent]
    files = ["amber14-all.xml"]
    if solvent_file:
        files.append(solvent_file)
    return app.ForceField(*files)


def create_system(forcefield: Any, topology: Any, app: Any, constraints: str) -> Any:
    constraint_map = {
        "none": None,
        "hbonds": app.HBonds,
        "allbonds": app.AllBonds,
        "hangles": app.HAngles,
    }
    return forcefield.createSystem(
        topology,
        nonbondedMethod=app.NoCutoff,
        constraints=constraint_map[constraints],
        removeCMMotion=False,
    )


def potential_energy(
    topology: Any,
    positions: Any,
    forcefield: Any,
    app: Any,
    openmm: Any,
    unit: Any,
    platform: Any,
    temperature: float,
    constraints: str,
) -> float:
    system = create_system(forcefield, topology, app, constraints)
    integrator = openmm.LangevinMiddleIntegrator(
        temperature * unit.kelvin,
        1.0 / unit.picosecond,
        0.002 * unit.picoseconds,
    )
    simulation = app.Simulation(topology, system, integrator, platform)
    simulation.context.setPositions(positions)
    state = simulation.context.getState(getEnergy=True)
    energy = state.getPotentialEnergy().value_in_unit(unit.kilocalories_per_mole)
    del simulation, integrator
    return float(energy)


def prepare_modeller(
    pdb_path: str,
    forcefield: Any,
    app: Any,
    pH: float,
    add_hydrogens: bool,
    keep_water: bool,
) -> Any:
    pdb = app.PDBFile(pdb_path)
    modeller = app.Modeller(pdb.topology, pdb.positions)
    if not keep_water:
        water_residues = [residue for residue in modeller.topology.residues() if residue.name in STANDARD_WATER_NAMES]
        if water_residues:
            modeller.delete(water_residues)
    if add_hydrogens:
        modeller.addHydrogens(forcefield, pH=pH)
    return modeller


def minimize_complex(
    topology: Any,
    positions: Any,
    forcefield: Any,
    app: Any,
    openmm: Any,
    unit: Any,
    platform: Any,
    temperature: float,
    constraints: str,
    minimize_steps: int,
    tolerance_kj_per_mol: float,
) -> tuple[Any, float | None]:
    if minimize_steps <= 0:
        return positions, None
    system = create_system(forcefield, topology, app, constraints)
    integrator = openmm.LangevinMiddleIntegrator(
        temperature * unit.kelvin,
        1.0 / unit.picosecond,
        0.002 * unit.picoseconds,
    )
    simulation = app.Simulation(topology, system, integrator, platform)
    simulation.context.setPositions(positions)
    simulation.minimizeEnergy(
        tolerance=tolerance_kj_per_mol * unit.kilojoule_per_mole / unit.nanometer,
        maxIterations=minimize_steps,
    )
    state = simulation.context.getState(getPositions=True, getEnergy=True)
    minimized_positions = state.getPositions()
    minimized_energy = state.getPotentialEnergy().value_in_unit(unit.kilocalories_per_mole)
    del simulation, integrator
    return minimized_positions, float(minimized_energy)


def rewrite_pdb_chain(input_pdb: str, output_handle: Any, chain_id: str, serial_offset: int) -> int:
    serial = serial_offset
    with open(input_pdb, "r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            record = line[:6]
            if record not in {"ATOM  ", "HETATM"}:
                continue
            serial += 1
            rewritten = f"{record}{serial:5d}{line[11:21]}{chain_id:1s}{line[22:]}"
            output_handle.write(rewritten)
        output_handle.write("TER\n")
    return serial


def combine_receptor_partner(receptor_pdb: str, partner_pdb: str, tmp_dir: Path, name: str) -> tuple[str, str, str]:
    complex_path = tmp_dir / f"{name}_combined_complex.pdb"
    serial = 0
    with open(complex_path, "w", encoding="utf-8") as out:
        serial = rewrite_pdb_chain(receptor_pdb, out, "A", serial)
        rewrite_pdb_chain(partner_pdb, out, "B", serial)
        out.write("END\n")
    return str(complex_path), "A", "B"


def score_job(
    job: ScoreJob,
    *,
    output_dir: Path,
    minimize_steps: int,
    platform_name: str,
    implicit_solvent: str,
    pH: float,
    add_hydrogens: bool,
    keep_water: bool,
    temperature: float,
    constraints: str,
    tolerance_kj_per_mol: float,
    write_minimized_pdb: bool,
) -> dict[str, Any]:
    openmm, app, unit = load_openmm()
    platform = select_platform(openmm, platform_name)
    forcefield = make_forcefield(app, implicit_solvent)
    started = time.time()

    with tempfile.TemporaryDirectory(prefix="binding_affinity_") as tmp:
        tmp_dir = Path(tmp)
        complex_pdb = job.complex_pdb
        receptor_chains = parse_chain_set(job.receptor_chains)
        partner_chains = parse_chain_set(job.partner_chains)
        input_mode = "complex_pdb"
        if not complex_pdb:
            if not job.receptor_pdb or not job.partner_pdb:
                raise ValueError("Provide either --complex-pdb with chain selections or --receptor-pdb and --partner-pdb")
            complex_pdb, receptor_chain, partner_chain = combine_receptor_partner(
                job.receptor_pdb,
                job.partner_pdb,
                tmp_dir,
                job.name,
            )
            receptor_chains = {receptor_chain}
            partner_chains = {partner_chain}
            input_mode = "separate_pdbs"

        if not receptor_chains or not partner_chains:
            raise ValueError("Both receptor_chains and partner_chains are required for complex scoring")

        modeller = prepare_modeller(complex_pdb, forcefield, app, pH, add_hydrogens, keep_water)
        available = set(chain_ids(modeller.topology))
        missing = (receptor_chains | partner_chains) - available
        if missing:
            raise ValueError(f"Requested chain(s) not found after preparation: {sorted(missing)}; available={sorted(available)}")
        overlap = receptor_chains & partner_chains
        if overlap:
            raise ValueError(f"Receptor and partner chain sets overlap: {sorted(overlap)}")

        positions, minimized_complex_energy = minimize_complex(
            modeller.topology,
            modeller.positions,
            forcefield,
            app,
            openmm,
            unit,
            platform,
            temperature,
            constraints,
            minimize_steps,
            tolerance_kj_per_mol,
        )

        receptor_topology, receptor_positions = subset_topology(modeller.topology, positions, receptor_chains, app)
        partner_topology, partner_positions = subset_topology(modeller.topology, positions, partner_chains, app)

        e_complex = potential_energy(
            modeller.topology,
            positions,
            forcefield,
            app,
            openmm,
            unit,
            platform,
            temperature,
            constraints,
        )
        e_receptor = potential_energy(
            receptor_topology,
            receptor_positions,
            forcefield,
            app,
            openmm,
            unit,
            platform,
            temperature,
            constraints,
        )
        e_partner = potential_energy(
            partner_topology,
            partner_positions,
            forcefield,
            app,
            openmm,
            unit,
            platform,
            temperature,
            constraints,
        )

        minimized_pdb = None
        coordinate_state = None
        if write_minimized_pdb:
            output_dir.mkdir(parents=True, exist_ok=True)
            minimized_pdb = output_dir / f"{job.name}_minimized_complex.pdb"
            with open(minimized_pdb, "w", encoding="utf-8") as handle:
                app.PDBFile.writeFile(modeller.topology, positions, handle, keepIds=True)
            # PDB rounds coordinates to 0.001 A. Retain the actual evaluated state
            # so independent energy recomputation does not change coordinates.
            coordinate_state = output_dir / f"{job.name}_evaluated_state.json"
            coordinate_state.write_text(json.dumps({
                "schema_version": "binding-affinity/evaluated-coordinates/v1",
                "units": "nanometer", "positions": positions.value_in_unit(unit.nanometer)
                    if hasattr(positions, "value_in_unit") else [list(v.value_in_unit(unit.nanometer)) for v in positions],
                "atoms": [{"chain": a.residue.chain.id, "residue": a.residue.id,
                           "insertion_code": a.residue.insertionCode, "resname": a.residue.name,
                           "atom": a.name, "element": a.element.symbol if a.element else None}
                          for a in modeller.topology.atoms()],
            }, default=lambda v: list(v)), encoding="utf-8")

    delta_e = e_complex - e_receptor - e_partner
    elapsed = time.time() - started
    return {
        "name": job.name,
        "status": "ok",
        "method": "openmm_implicit_binding_energy",
        "input_mode": input_mode,
        "complex_pdb": job.complex_pdb,
        "receptor_pdb": job.receptor_pdb,
        "partner_pdb": job.partner_pdb,
        "receptor_chains": sorted(receptor_chains),
        "partner_chains": sorted(partner_chains),
        "platform": platform.getName(),
        "forcefield": "amber14-all.xml",
        "implicit_solvent": implicit_solvent,
        "constraints": constraints,
        "add_hydrogens": add_hydrogens,
        "keep_water": keep_water,
        "pH": pH,
        "minimize_steps": minimize_steps,
        "minimized_complex_energy_kcal_per_mol": round(minimized_complex_energy, 6) if minimized_complex_energy is not None else None,
        "complex_energy_kcal_per_mol": round(e_complex, 6),
        "receptor_energy_kcal_per_mol": round(e_receptor, 6),
        "partner_energy_kcal_per_mol": round(e_partner, 6),
        "delta_e_binding_kcal_per_mol": round(delta_e, 6),
        "elapsed_seconds": round(elapsed, 3),
        "minimized_pdb": str(minimized_pdb) if minimized_pdb else None,
        "evaluated_coordinate_state": str(coordinate_state) if coordinate_state else None,
        "minimize_tolerance_kj_per_mol_nm": tolerance_kj_per_mol,
        "interpretation": "More negative delta_e_binding_kcal_per_mol indicates more favorable modeled binding within a consistent comparison set.",
    }


def row_to_job(row: dict[str, str], index: int) -> ScoreJob:
    name = row.get("name") or row.get("id") or row.get("design_id") or f"job_{index + 1}"
    return ScoreJob(
        name=str(name),
        complex_pdb=row.get("complex_pdb") or row.get("complex") or None,
        receptor_chains=row.get("receptor_chains") or row.get("receptor_chain") or None,
        partner_chains=row.get("partner_chains") or row.get("partner_chain") or None,
        receptor_pdb=row.get("receptor_pdb") or row.get("receptor") or None,
        partner_pdb=row.get("partner_pdb") or row.get("partner") or None,
    )


def load_jobs(args: argparse.Namespace) -> list[ScoreJob]:
    if args.batch_csv:
        jobs: list[ScoreJob] = []
        with open(args.batch_csv, "r", encoding="utf-8", newline="") as handle:
            for index, row in enumerate(csv.DictReader(handle)):
                jobs.append(row_to_job(row, index))
        return jobs
    name = args.name or Path(args.complex_pdb or args.partner_pdb or "complex").stem
    return [
        ScoreJob(
            name=name,
            complex_pdb=args.complex_pdb,
            receptor_chains=args.receptor_chains,
            partner_chains=args.partner_chains,
            receptor_pdb=args.receptor_pdb,
            partner_pdb=args.partner_pdb,
        )
    ]


def flatten_for_csv(result: dict[str, Any]) -> dict[str, Any]:
    fields = [
        "name",
        "status",
        "method",
        "delta_e_binding_kcal_per_mol",
        "complex_energy_kcal_per_mol",
        "receptor_energy_kcal_per_mol",
        "partner_energy_kcal_per_mol",
        "minimized_complex_energy_kcal_per_mol",
        "receptor_chains",
        "partner_chains",
        "platform",
        "implicit_solvent",
        "minimize_steps",
        "elapsed_seconds",
        "error",
    ]
    row: dict[str, Any] = {}
    for field in fields:
        value = result.get(field, "")
        if isinstance(value, list):
            value = ",".join(str(item) for item in value)
        row[field] = value
    return row


def write_outputs(results: list[dict[str, Any]], args: argparse.Namespace) -> None:
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    ok_results = [r for r in results if r.get("status") == "ok"]
    ranked = sorted(
        ok_results,
        key=lambda r: r.get("delta_e_binding_kcal_per_mol", math.inf),
    )
    ranks = {id(result): rank + 1 for rank, result in enumerate(ranked)}
    for result in results:
        result["rank_by_delta_e"] = ranks.get(id(result))

    payload = {
        "schema_version": "binding-affinity/openmm-complex/v1",
        "method": "openmm_implicit_binding_energy",
        "n_jobs": len(results),
        "n_ok": len(ok_results),
        "n_failed": len(results) - len(ok_results),
        "settings": {
            "minimize_steps": args.minimize_steps,
            "implicit_solvent": args.implicit_solvent,
            "platform": args.platform,
            "pH": args.ph,
            "add_hydrogens": not args.no_add_hydrogens,
            "keep_water": args.keep_water,
            "constraints": args.constraints,
        },
        "note": "OpenMM potential-energy proxy for relative ranking; not experimental binding free energy.",
        "results": results,
    }
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    if args.csv_output:
        csv_path = Path(args.csv_output)
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        rows = [flatten_for_csv(result) | {"rank_by_delta_e": result.get("rank_by_delta_e", "")} for result in results]
        fieldnames = list(rows[0].keys()) if rows else ["name", "status"]
        with open(csv_path, "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

    manifest_path = output_path.parent / "_script_manifest.jsonl"
    with open(manifest_path, "a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "script": "score_complex.py",
                    "output": str(output_path),
                    "csv_output": args.csv_output,
                    "n_jobs": len(results),
                    "timestamp": time.time(),
                    "args": vars(args),
                },
                sort_keys=True,
            )
            + "\n"
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Score protein-complex binding energy with OpenMM only.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--complex-pdb", help="Complex PDB containing receptor and partner chains")
    source.add_argument("--batch-csv", help="CSV with complex_pdb/receptor_chains/partner_chains or receptor_pdb/partner_pdb")
    source.add_argument("--receptor-pdb", help="Receptor PDB aligned to partner coordinates")
    parser.add_argument("--partner-pdb", help="Partner PDB aligned to receptor coordinates")
    parser.add_argument("--receptor-chains", help="Comma-separated receptor chain IDs for --complex-pdb")
    parser.add_argument("--partner-chains", help="Comma-separated partner chain IDs for --complex-pdb")
    parser.add_argument("--name", help="Run/design name for single-job output")
    parser.add_argument("--output", required=True, help="Output JSON path")
    parser.add_argument("--csv-output", help="Optional flat CSV summary")
    parser.add_argument("--minimize-steps", type=int, default=0)
    parser.add_argument("--minimize-tolerance-kj", type=float, default=10.0, help="RMS force tolerance in kJ/(mol nm)")
    parser.add_argument("--platform", default="auto", choices=["auto", "CUDA", "OpenCL", "CPU", "Reference"])
    parser.add_argument("--implicit-solvent", default="obc2", choices=["obc1", "obc2", "gbn", "gbn2", "none"])
    parser.add_argument("--constraints", default="hbonds", choices=["none", "hbonds", "allbonds", "hangles"])
    parser.add_argument("--temperature", type=float, default=300.0)
    parser.add_argument("--ph", type=float, default=7.0)
    parser.add_argument("--no-add-hydrogens", action="store_true")
    parser.add_argument("--keep-water", action="store_true")
    parser.add_argument("--write-minimized-pdb", action="store_true")
    parser.add_argument("--fail-fast", action="store_true", help="Stop batch scoring on the first failed row")
    return parser.parse_args()


def validate_args(args: argparse.Namespace) -> None:
    if args.receptor_pdb and not args.partner_pdb:
        raise SystemExit("--partner-pdb is required with --receptor-pdb")
    if args.complex_pdb and (not args.receptor_chains or not args.partner_chains):
        raise SystemExit("--receptor-chains and --partner-chains are required with --complex-pdb")


def main() -> None:
    args = parse_args()
    validate_args(args)
    jobs = load_jobs(args)
    output_dir = Path(args.output).resolve().parent
    results: list[dict[str, Any]] = []
    for index, job in enumerate(jobs, start=1):
        print(f"[{index}/{len(jobs)}] scoring {job.name}")
        try:
            result = score_job(
                job,
                output_dir=output_dir,
                minimize_steps=args.minimize_steps,
                platform_name=args.platform,
                implicit_solvent=args.implicit_solvent,
                pH=args.ph,
                add_hydrogens=not args.no_add_hydrogens,
                keep_water=args.keep_water,
                temperature=args.temperature,
                constraints=args.constraints,
                tolerance_kj_per_mol=args.minimize_tolerance_kj,
                write_minimized_pdb=args.write_minimized_pdb,
            )
            print(f"  delta_e_binding = {result['delta_e_binding_kcal_per_mol']:.3f} kcal/mol")
            results.append(result)
        except Exception as exc:
            failed = {
                "name": job.name,
                "status": "failed",
                "method": "openmm_implicit_binding_energy",
                "error": str(exc),
                "complex_pdb": job.complex_pdb,
                "receptor_pdb": job.receptor_pdb,
                "partner_pdb": job.partner_pdb,
                "receptor_chains": sorted(parse_chain_set(job.receptor_chains)),
                "partner_chains": sorted(parse_chain_set(job.partner_chains)),
            }
            print(f"  FAILED: {exc}")
            results.append(failed)
            if args.fail_fast:
                break
    write_outputs(results, args)
    print(f"Saved JSON: {args.output}")
    if args.csv_output:
        print(f"Saved CSV: {args.csv_output}")


if __name__ == "__main__":
    main()
