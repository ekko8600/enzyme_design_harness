#!/usr/bin/env python3
"""Run the skill's Amber-force-field ensemble protocol with OpenMM."""

from __future__ import annotations

import argparse
import json
import platform as py_platform
import re
import sys
from pathlib import Path

import openmm
from openmm import CustomExternalForce, LangevinMiddleIntegrator, MonteCarloBarostat, Platform, XmlSerializer, unit
from openmm.app import (
    AmberInpcrdFile,
    AmberPrmtopFile,
    CheckpointReporter,
    DCDReporter,
    HBonds,
    NoCutoff,
    PME,
    PDBFile,
    Simulation,
    StateDataReporter,
)


def atom_indices(spec: str, natoms: int) -> list[int]:
    """Parse a comma-separated 1-based list/ranges and return unique 0-based indices."""
    if not spec.strip():
        return []
    result: list[int] = []
    for token in spec.split(","):
        token = token.strip()
        match = re.fullmatch(r"(\d+)(?:-(\d+))?", token)
        if not match:
            raise ValueError(f"invalid restraint index token: {token!r}")
        start = int(match.group(1))
        end = int(match.group(2) or start)
        if start > end or start < 1 or end > natoms:
            raise ValueError(f"restraint range outside 1..{natoms}: {token}")
        result.extend(range(start - 1, end))
    return list(dict.fromkeys(result))


def load_coordinates(path: Path):
    if path.suffix.lower() in {".pdb", ".ent"}:
        source = PDBFile(str(path))
        return source.positions, source.topology.getPeriodicBoxVectors()
    source = AmberInpcrdFile(str(path))
    return source.positions, source.boxVectors


def select_platform(name: str, gpu: str, precision: str):
    available = [Platform.getPlatform(i).getName() for i in range(Platform.getNumPlatforms())]
    selected = name
    if name == "auto":
        selected = "CUDA" if "CUDA" in available else "CPU"
    if selected not in available:
        raise RuntimeError(f"OpenMM platform {selected!r} unavailable; available={available}")
    properties: dict[str, str] = {}
    if selected == "CUDA":
        properties = {"DeviceIndex": gpu, "Precision": precision}
    elif selected == "CPU":
        properties = {"Threads": str(max(1, args.cpu_threads))}
    return Platform.getPlatformByName(selected), properties, available


def add_restraint(system, positions, indices: list[int], periodic: bool):
    if not indices:
        return None
    distance = "periodicdistance(x,y,z,x0,y0,z0)^2" if periodic else "(x-x0)^2+(y-y0)^2+(z-z0)^2"
    force = CustomExternalForce(f"0.5*k*({distance})")
    force.addGlobalParameter("k", 0.0)
    for parameter in ("x0", "y0", "z0"):
        force.addPerParticleParameter(parameter)
    for index in indices:
        xyz = positions[index].value_in_unit(unit.nanometer)
        force.addParticle(index, xyz)
    force.setForceGroup(31)
    system.addForce(force)
    return force


def set_restraint(context, kcal_per_mol_a2: float):
    # 1 kcal mol^-1 A^-2 = 418.4 kJ mol^-1 nm^-2.
    context.setParameter("k", kcal_per_mol_a2 * 418.4)


def write_state(simulation: Simulation, topology, path: Path):
    state = simulation.context.getState(getPositions=True, enforcePeriodicBox=True)
    with path.open("w", encoding="utf-8") as handle:
        PDBFile.writeFile(topology, state.getPositions(), handle, keepIds=True)


def parse_args():
    parser = argparse.ArgumentParser(
        description="OpenMM min -> 0–300 K NVT heat -> NPT equilibration -> NPT production"
    )
    parser.add_argument("-p", "--prmtop", required=True, type=Path)
    parser.add_argument("-c", "--coordinates", required=True, type=Path)
    parser.add_argument("-t", "--tag", default="md")
    parser.add_argument("-o", "--output-dir", type=Path, default=Path("."))
    parser.add_argument("-g", "--gpu", default="0", help="CUDA DeviceIndex")
    parser.add_argument("--platform", choices=("auto", "CUDA", "CPU", "Reference"), default="auto")
    parser.add_argument("--precision", choices=("single", "mixed", "double"), default="mixed")
    parser.add_argument("--cpu-threads", type=int, default=4)
    parser.add_argument(
        "-r",
        "--restraint-indices",
        default="",
        help="1-based atom indices/ranges, e.g. 10,12-18 (not an Amber mask)",
    )
    parser.add_argument("--temperature", type=float, default=300.0)
    parser.add_argument("--pressure", type=float, default=1.0)
    parser.add_argument("--friction", type=float, default=2.0, help="ps^-1")
    parser.add_argument("--timestep-fs", type=float, default=2.0)
    parser.add_argument("--min-iterations", type=int, default=2000)
    parser.add_argument("--heat-steps", type=int, default=250_000)
    parser.add_argument("--equil-steps", type=int, default=250_000)
    parser.add_argument("--prod-steps", type=int, default=15_000_000)
    parser.add_argument("--report-steps", type=int, default=5_000)
    parser.add_argument("--checkpoint-steps", type=int, default=500_000)
    parser.add_argument("--seed", type=int, default=0)
    return parser.parse_args()


args = parse_args()
args.output_dir.mkdir(parents=True, exist_ok=True)
prefix = args.output_dir / args.tag
prmtop = AmberPrmtopFile(str(args.prmtop))
positions, box_vectors = load_coordinates(args.coordinates)
natoms = sum(1 for _ in prmtop.topology.atoms())
if len(positions) != natoms:
    raise ValueError(f"atom count mismatch: topology={natoms}, coordinates={len(positions)}")
if box_vectors is not None:
    prmtop.topology.setPeriodicBoxVectors(box_vectors)
periodic = prmtop.topology.getPeriodicBoxVectors() is not None

system = prmtop.createSystem(
    nonbondedMethod=PME if periodic else NoCutoff,
    nonbondedCutoff=1.0 * unit.nanometer,
    constraints=HBonds,
    rigidWater=True,
    removeCMMotion=True,
)
barostat = None
if periodic:
    barostat = MonteCarloBarostat(args.pressure * unit.bar, args.temperature * unit.kelvin, 0)
    if args.seed:
        barostat.setRandomNumberSeed(args.seed)
    system.addForce(barostat)
restrained = atom_indices(args.restraint_indices, natoms)
restraint_force = add_restraint(system, positions, restrained, periodic)

integrator = LangevinMiddleIntegrator(
    1.0 * unit.kelvin,
    args.friction / unit.picosecond,
    args.timestep_fs * unit.femtosecond,
)
if args.seed:
    integrator.setRandomNumberSeed(args.seed)
platform, properties, available = select_platform(args.platform, args.gpu, args.precision)
simulation = Simulation(prmtop.topology, system, integrator, platform, properties)
simulation.context.setPositions(positions)
if box_vectors is not None:
    simulation.context.setPeriodicBoxVectors(*box_vectors)

with Path(f"{prefix}_system.xml").open("w", encoding="utf-8") as handle:
    handle.write(XmlSerializer.serialize(system))

if restraint_force:
    set_restraint(simulation.context, 10.0)
simulation.minimizeEnergy(maxIterations=args.min_iterations)
write_state(simulation, prmtop.topology, Path(f"{prefix}_min.pdb"))
simulation.saveCheckpoint(str(prefix) + "_min.chk")

if restraint_force:
    set_restraint(simulation.context, 5.0)
simulation.context.setVelocitiesToTemperature(1.0 * unit.kelvin, args.seed or 1)
heat_chunks = min(100, max(1, args.heat_steps))
base, extra = divmod(args.heat_steps, heat_chunks)
for chunk in range(heat_chunks):
    temperature = args.temperature * (chunk + 1) / heat_chunks
    integrator.setTemperature(temperature * unit.kelvin)
    simulation.step(base + (1 if chunk < extra else 0))
write_state(simulation, prmtop.topology, Path(f"{prefix}_heat.pdb"))
simulation.saveCheckpoint(str(prefix) + "_heat.chk")

if periodic and barostat is not None:
    barostat.setFrequency(25)
    simulation.context.reinitialize(preserveState=True)
if restraint_force:
    set_restraint(simulation.context, 2.0)
integrator.setTemperature(args.temperature * unit.kelvin)
simulation.step(args.equil_steps)
write_state(simulation, prmtop.topology, Path(f"{prefix}_equil.pdb"))
simulation.saveCheckpoint(str(prefix) + "_equil.chk")

simulation.reporters.append(DCDReporter(str(prefix) + "_prod.dcd", args.report_steps, enforcePeriodicBox=periodic))
simulation.reporters.append(
    StateDataReporter(
        str(prefix) + "_prod.tsv",
        args.report_steps,
        step=True,
        time=True,
        potentialEnergy=True,
        kineticEnergy=True,
        temperature=True,
        volume=periodic,
        density=periodic,
        speed=True,
        separator="\t",
    )
)
simulation.reporters.append(CheckpointReporter(str(prefix) + "_prod.chk", args.checkpoint_steps))
simulation.step(args.prod_steps)
write_state(simulation, prmtop.topology, Path(f"{prefix}_prod.pdb"))
simulation.saveCheckpoint(str(prefix) + "_prod.chk")

manifest = {
    "engine": "OpenMM",
    "openmm_version": openmm.version.version,
    "python": py_platform.python_version(),
    "platform": platform.getName(),
    "platform_properties": properties,
    "available_platforms": available,
    "prmtop": str(args.prmtop.resolve()),
    "coordinates": str(args.coordinates.resolve()),
    "periodic": periodic,
    "atoms": natoms,
    "restraint_indices_1based": [index + 1 for index in restrained],
    "protocol": {
        "min_iterations": args.min_iterations,
        "heat_steps": args.heat_steps,
        "equil_steps": args.equil_steps,
        "prod_steps": args.prod_steps,
        "timestep_fs": args.timestep_fs,
        "temperature_K": args.temperature,
        "pressure_bar": args.pressure if periodic else None,
        "friction_ps-1": args.friction,
        "restraint_kcal_mol_A2": {"min": 10, "heat": 5, "equil": 2, "prod": 2},
    },
}
Path(f"{prefix}_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
print(f">> OpenMM ensemble complete: {prefix}_prod.dcd")
