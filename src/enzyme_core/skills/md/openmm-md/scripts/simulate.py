"""
Simple protein-ligand simulation using openmm
"""

import hashlib
import json
import os
import re
import subprocess
import time
import urllib.request
from tqdm.auto import tqdm

from shutil import copy2
from tempfile import NamedTemporaryFile
from pathlib import Path
from warnings import warn

import mdtraj as md
import numpy as np
import pandas as pd

import MDAnalysis as mda
from MDAnalysis.coordinates.PDB import PDBWriter

from openff.toolkit.topology import Molecule
from openmm import app, unit, LangevinIntegrator, MonteCarloBarostat, Platform, XmlSerializer
from openmm.app import (
    CheckpointReporter,
    DCDReporter,
    Modeller,
    PDBFile,
    Simulation,
    StateDataReporter,
)

from openmmforcefields.generators import SystemGenerator
from pdbfixer import PDBFixer
from rdkit import Chem
from rdkit.Chem import AllChem
from rdkit.Chem import rdMolTransforms, rdShapeHelpers

# for convenience so I can use as a script or a module
try:
    from .extract_ligands import SUPPORTED_STRUCTURE_IONS, extract_ligand
except ImportError:
    from extract_ligands import SUPPORTED_STRUCTURE_IONS, extract_ligand

GNINA, GNINA_BIN = "gnina", "gnina"
OBABEL, OBABEL_BIN = "obabel", "obabel"
binaries = {GNINA: GNINA_BIN, OBABEL: OBABEL_BIN}

PDB_PH = 7.4
PDB_TEMPERATURE = 300 * unit.kelvin
FRICTION_COEFF = 1.0 / unit.picosecond
# https://docs.openforcefield.org/projects/toolkit/en/stable/faq.html
# With step size of 2 femtoseconds
STEP_SIZE = 0.002 * unit.picosecond
SOLVENT_PADDING = 10.5 * unit.angstroms
BAROSTAT_PRESSURE = 1.0 * unit.atmospheres
BAROSTAT_FREQUENCY = 25
# "hydrogen-involving bond constraints" is recommended
FORCEFIELD_KWARGS = {
    "constraints": app.HBonds,
    "rigidWater": True,
    "removeCMMotion": True,
    "hydrogenMass": 4 * unit.amu,
}
FORCEFIELD_PROTEIN = "amber/ff14SB.xml"
FORCEFIELD_SOLVENT = "amber/tip3p_standard.xml"
FORCEFIELD_SMALL_MOLECULE = "gaff-2.11"

# when I run a simulation with a protein and an SDF file,
# openmm calls the ligand "UNK" in the combined output PDB file
OPENMM_DEFAULT_LIGAND_ID = "UNK"
PDB_ID_RE = re.compile(r"^[A-Za-z0-9]{4}$")


def _structure_ion_records(
    topology, positions, reject_unsupported: bool = True
) -> list[dict]:
    """Collect supported ions and reject unknown inorganic monatomic residues."""
    records = []
    for residue in topology.residues():
        residue_name = residue.name.strip().upper()
        atoms = list(residue.atoms())
        if residue_name not in SUPPORTED_STRUCTURE_IONS:
            if (
                reject_unsupported
                and len(atoms) == 1
                and atoms[0].element is not None
                and atoms[0].element.symbol not in {"H", "C", "N", "O", "P", "S"}
            ):
                raise ValueError(
                    f"unsupported structure ion {residue_name}:{residue.chain.id}:"
                    f"{residue.id} ({atoms[0].element.symbol}); use a pre-parameterized "
                    "Amber system or explicitly pass --strip-structure-ions"
                )
            continue
        expected_element = SUPPORTED_STRUCTURE_IONS[residue_name]
        if len(atoms) != 1 or atoms[0].element is None or atoms[0].element.symbol != expected_element:
            observed = [atom.element.symbol if atom.element is not None else None for atom in atoms]
            raise ValueError(
                f"malformed supported ion {residue_name}:{residue.chain.id}:{residue.id}; "
                f"expected one {expected_element} atom, observed {observed}"
            )
        atom = atoms[0]
        records.append({
            "residue_name": residue_name,
            "residue_id": residue.id,
            "chain_id": residue.chain.id,
            "atom_name": atom.name,
            "atom_id": atom.id,
            "element": atom.element,
            "position": positions[atom.index],
        })
    return records


def _restore_structure_ions(modeller, ion_records: list[dict]) -> None:
    """Add retained ions back after PDBFixer removes other heterogens."""
    if not ion_records:
        return
    from openmm.app import Topology

    ion_topology = Topology()
    chains = {}
    ion_positions = []
    for record in ion_records:
        chain_id = record["chain_id"]
        if chain_id not in chains:
            chains[chain_id] = ion_topology.addChain(chain_id)
        chain = chains[chain_id]
        residue = ion_topology.addResidue(
            record["residue_name"], chain, id=record["residue_id"]
        )
        ion_topology.addAtom(
            record["atom_name"], record["element"], residue, id=record["atom_id"]
        )
        ion_positions.append(record["position"])
    modeller.add(ion_topology, ion_positions)


def _public_ion_records(records: list[dict], disposition: str) -> list[dict]:
    return [
        {
            "residue_name": record["residue_name"],
            "residue_id": record["residue_id"],
            "chain_id": record["chain_id"],
            "element": record["element"].symbol,
            "disposition": disposition,
        }
        for record in records
    ]


def _download_structure(url: str, destination: str) -> str:
    """Download a structure with a timeout and return its SHA-256 digest."""
    request = urllib.request.Request(url, headers={"User-Agent": "enzyme-design-platform/0.1"})
    digest = hashlib.sha256()
    with urllib.request.urlopen(request, timeout=60) as response, open(destination, "wb") as handle:
        while chunk := response.read(1024 * 1024):
            handle.write(chunk)
            digest.update(chunk)
    return digest.hexdigest()


def _platform_names_by_speed() -> list[str]:
    return [
        Platform.getPlatform(i).getName()
        for i in sorted(
            range(Platform.getNumPlatforms()),
            key=lambda idx: Platform.getPlatform(idx).getSpeed(),
            reverse=True,
        )
    ]


def resolve_platform_candidates(preferred: str = "auto") -> list[str]:
    available = _platform_names_by_speed()
    if preferred != "auto":
        return [preferred] + [name for name in available if name != preferred]
    priority = ["CUDA", "OpenCL", "CPU", "Reference"]
    return [name for name in priority if name in available] + [
        name for name in available if name not in priority
    ]


def configure_platform(platform):
    if platform.getName() in {"CUDA", "OpenCL"}:
        platform.setPropertyDefaultValue("Precision", "mixed")
        print(f"Set precision for platform {platform.getName()} to mixed\n")
    return platform


def should_retry_platform(exc: Exception) -> bool:
    text = str(exc).upper()
    markers = [
        "CUDA_ERROR_UNSUPPORTED_PTX_VERSION",
        "UNSUPPORTED_PTX",
        "NO COMPATIBLE CUDA DEVICE",
        "CUDA_ERROR",
        "OPENCL_ERROR",
        "PLATFORM",
    ]
    return any(marker in text for marker in markers)


def create_simulation_with_platform_fallback(
    topology,
    system,
    integrator,
    positions,
    preferred_platform: str = "auto",
    allow_fallback: bool = True,
):
    errors = []
    candidates = resolve_platform_candidates(preferred_platform)
    for platform_name in candidates:
        try:
            platform = configure_platform(Platform.getPlatformByName(platform_name))
            simulation = Simulation(topology, system, integrator, platform=platform)
            simulation.context.setPositions(positions)
            simulation.context.getState(getEnergy=False, getPositions=False)
            print(f"# OpenMM platform selected: {platform_name}")
            return simulation, platform_name
        except Exception as exc:
            errors.append({"platform": platform_name, "error": str(exc)})
            if not allow_fallback or not should_retry_platform(exc):
                raise
            print(f"# OpenMM platform {platform_name} failed, trying fallback: {exc}")
    raise RuntimeError(f"Unable to create an OpenMM context on any platform: {errors}")


def _disulfide_records(topology):
    def key(atom):
        residue = atom.residue
        return (residue.chain.id, residue.id, residue.insertionCode)
    return [(key(a), key(b)) for a,b in topology.bonds() if a.name == b.name == 'SG'
            and a.element.symbol == b.element.symbol == 'S']


def _restore_disulfides(topology, records):
    atoms = {(a.residue.chain.id, a.residue.id, a.residue.insertionCode): a
             for a in topology.atoms() if a.name == 'SG'}
    present = {frozenset((a.index,b.index)) for a,b in topology.bonds()}
    for first, second in records:
        if first not in atoms or second not in atoms:
            raise ValueError('DISULFIDE_SG_MISSING_AFTER_PREPARATION:' + str((first,second)))
        a,b = atoms[first],atoms[second]
        if frozenset((a.index,b.index)) not in present:
            topology.addBond(a,b)
    if set(tuple(sorted(pair)) for pair in _disulfide_records(topology)) != set(tuple(sorted(pair)) for pair in records):
        raise ValueError('DISULFIDE_TOPOLOGY_CHANGED_AFTER_PREPARATION')


def _remove_disulfide_thiol_hydrogens(fixer, disulfides):
    cystines = {key for pair in disulfides for key in pair}
    modeller = Modeller(fixer.topology, fixer.positions)
    hydrogens = [a for a in modeller.topology.atoms()
                 if (a.residue.chain.id, a.residue.id, a.residue.insertionCode) in cystines
                 and a.name == 'HG' and a.element.symbol == 'H']
    modeller.delete(hydrogens)
    fixer.topology, fixer.positions = modeller.topology, modeller.positions
    _restore_disulfides(fixer.topology, disulfides)
    return len(hydrogens)


def prepare_protein(
    in_pdb_file: str,
    out_pdb_file: str,
    minimize_pdb: bool = False,
    mutations: list | None = None,
    platform_name: str = "auto",
    allow_platform_fallback: bool = True,
    preserve_structure_ions: bool = True,
) -> dict:
    """
    Prepare a protein for simulation using pdbfixer and optionally minimize it using openmm.

    This function fixes common issues in PDB files and prepares them for simulation.
    It identifies missing residues, atoms, non-standard residues and heterogens, then fixes these issues.
    It also adds missing hydrogens according to the specified pH value.
    If the 'minimize_pdb' flag is set, the function additionally minimizes the energy of the
    system using a Langevin integrator.

    Parameters:
    in_pdb_file (str): Path to the input PDB file.
    out_pdb_file (str): Path to the output PDB file where the prepared protein will be saved.
    minimize_pdb (bool, optional): A flag indicating whether to minimize the PDB file using openmm.
        If it's a crystal from PDB, then you would not want to minimize it.
        If it's a docked pose, then you may want to minimize it. Defaults to False.
    mutation (tuple, optional): A mutation to apply to the protein. Defaults to None.
        Format is (from_aa, resn, to_aa, chains).
    preserve_structure_ions (bool, optional): Retain supported monatomic ions from
        the input structure. Defaults to True; disabling it is an explicit strip policy.
    Returns:
    dict: Structure-ion provenance for the run manifest.

    Warnings:
    This function issues a warning if DMSO is found in the PDB file, suggesting manual removal.
    """

    fixer = PDBFixer(filename=in_pdb_file)
    disulfides = _disulfide_records(fixer.topology)
    # Some crystallographic PDBs contain thiol hydrogens despite an S-S bond.
    # Remove only these contradictory hydrogens before assigning cystine templates.
    removed_thiol_hydrogens = _remove_disulfide_thiol_hydrogens(fixer, disulfides)
    structure_ions = _structure_ion_records(
        fixer.topology,
        fixer.positions,
        reject_unsupported=preserve_structure_ions,
    )
    for mutation in mutations or []:
        mutation = mutation.split("-")
        for chain in mutation[3]:  # e.g., AB
            fixer.applyMutations([f"{mutation[0]}-{mutation[1]}-{mutation[2]}"], chain)
    fixer.findMissingResidues()
    fixer.findMissingAtoms()
    fixer.findNonstandardResidues()

    print(
        f"# Preparing protein:\n"
        f"- Missing residues: {fixer.missingResidues}\n"
        f"- Atoms: {fixer.missingAtoms}\n"
        f"- Terminals: {fixer.missingTerminals}\n"
        f"- Non-standard: {fixer.nonstandardResidues}\n"
    )

    fixer.addMissingAtoms()
    # PDBFixer 1.x may rebuild topology without the original disulfide bonds.
    # Restore before hydrogen assignment, so cystines cannot become protonated thiols.
    _restore_disulfides(fixer.topology, disulfides)
    fixer.addMissingHydrogens(PDB_PH)
    _restore_disulfides(fixer.topology, disulfides)
    fixer.removeHeterogens(keepWater=False)
    modeller = Modeller(fixer.topology, fixer.positions)
    if preserve_structure_ions:
        _restore_structure_ions(modeller, structure_ions)
    _restore_disulfides(modeller.topology, disulfides)

    for res in modeller.topology.residues():
        if res.name == "DMS":
            warn("DMSO found in PDB file. Maybe remove?")

    # Note in the process of adding missing residues, some weird proteins are created
    # .e.g, the PDB file could list the first N residues, but have no co-ordinates
    # Then some combination of PDBFixer and openmm place these residues but in a straight line
    with open(out_pdb_file, "w", encoding="utf-8") as out:
        PDBFile.writeFile(modeller.topology, modeller.positions, file=out, keepIds=True)

    if minimize_pdb is True:
        system_generator = SystemGenerator(
            forcefields=[FORCEFIELD_PROTEIN, FORCEFIELD_SOLVENT]
        )
        system = system_generator.create_system(modeller.topology)
        integrator = LangevinIntegrator(PDB_TEMPERATURE, FRICTION_COEFF, STEP_SIZE)
        simulation, _ = create_simulation_with_platform_fallback(
            modeller.topology,
            system,
            integrator,
            modeller.positions,
            preferred_platform=platform_name,
            allow_fallback=allow_platform_fallback,
        )
        simulation.minimizeEnergy()

        mlig_path = f"{Path(out_pdb_file).with_suffix('')}_minimized_no_ligand.pdb"

        with open(mlig_path, "w", encoding="utf-8") as out:
            PDBFile.writeFile(
                modeller.topology,
                simulation.context.getState(
                    getPositions=True, enforcePeriodicBox=False
                ).getPositions(),
                file=out,
                keepIds=True,
            )

    disposition = "retained" if preserve_structure_ions else "stripped_by_explicit_policy"
    return {
        "structure_ions": _public_ion_records(structure_ions, disposition),
        "preserve_structure_ions": preserve_structure_ions,
        "disulfide_bonds": disulfides,
        "removed_inconsistent_thiol_hydrogens": removed_thiol_hydrogens,
    }


def get_pdb_and_extract_ligand(
    pdb_id: str,
    ligand_id: str | None = None,
    ligand_chain: str | None = None,
    out_dir: str = ".",
    use_pdb_redo: bool = False,
    minimize_pdb: bool = False,
    mutations: list | None = None,
    platform_name: str = "auto",
    allow_platform_fallback: bool = True,
    preserve_structure_ions: bool = True,
) -> dict:
    """
    Download a PDB file, prepare it for MD, and extract a ligand.

    This function downloads a PDB file, prepares it to a {pdb_id}_fixed.pdb file,
    then extracts one ligand and saves it as {pdb_id}_{ligand_id}.sdf and {pdb_id}_{ligand_id}.smi

    Parameters:
    pdb_id (str): The 4-letter PDB ID.
    ligand_id (str): The 3-letter ligand ID.
    ligand_chain (str): If you want to specify the chain of the ligand. Defaults to None.
    out_dir (str): The output directory. Defaults to '.'.
    use_pdb_redo (bool): If True, use get pdb_redo PDB ({pdb_id}_pdbredo.pdb). Defaults to False.
    minimize_pdb (bool): If True, minimize the PDB file. Defaults to False.
    remove_H_from_ligand (bool): If True, remove hydrogens from the ligand. Defaults to True.

    Returns:
    filename_dict (dict): a dict of the files produced
    """
    Path(out_dir).mkdir(parents=True, exist_ok=True)

    if pdb_id.endswith(".pdb"):
        pdb_file = str(Path(out_dir) / f"{Path(pdb_id).stem}.pdb")
        copy2(pdb_id, pdb_file)
        pdb_id = Path(pdb_id).stem
    elif use_pdb_redo:
        if not PDB_ID_RE.fullmatch(pdb_id):
            raise ValueError(f"invalid PDB ID: {pdb_id!r}")
        pdb_file = str(Path(out_dir) / f"{pdb_id}_pdbredo.pdb")
        _download_structure(
            f"https://pdb-redo.eu/db/{pdb_id.lower()}/{pdb_id.lower()}_final.pdb",
            pdb_file,
        )
    else:
        if not PDB_ID_RE.fullmatch(pdb_id):
            raise ValueError(f"invalid PDB ID: {pdb_id!r}")
        pdb_file = str(Path(out_dir) / f"{pdb_id}.pdb")
        _download_structure(
            f"https://files.rcsb.org/download/{pdb_id.upper()}.pdb",
            pdb_file,
        )

    if ligand_id is None:  # then extract nothing, just prepare the protein
        prepared_pdb_file = str(Path(out_dir) / f"{pdb_id}_fixed.pdb")
        preparation = prepare_protein(
            pdb_file,
            prepared_pdb_file,
            minimize_pdb=minimize_pdb,
            mutations=mutations,
            platform_name=platform_name,
            allow_platform_fallback=allow_platform_fallback,
            preserve_structure_ions=preserve_structure_ions,
        )
        return {
            "original_pdb": pdb_file,
            "pdb": prepared_pdb_file,
            **preparation,
        }

    out_sdf_file = str(Path(out_dir) / f"{pdb_id}_{ligand_id}.sdf")
    # The extractor always writes a receptor PDB. Passing None used to cause a
    # deterministic TypeError on the valid ligand path.
    extracted_protein = str(Path(out_dir) / f"{pdb_id}_protein.pdb")
    _, _, out_sdf_smiles = extract_ligand(
        pdb_file, ligand_id, ligand_chain, out_pdb_file=extracted_protein,
        out_sdf_file=out_sdf_file,
    )
    prepared_pdb_file = str(Path(out_dir) / f"{pdb_id}_fixed.pdb")
    preparation = prepare_protein(
        pdb_file,
        prepared_pdb_file,
        minimize_pdb=minimize_pdb,
        mutations=mutations,
        platform_name=platform_name,
        allow_platform_fallback=allow_platform_fallback,
        preserve_structure_ions=preserve_structure_ions,
    )

    return {
        "original_pdb": pdb_file,
        "pdb": prepared_pdb_file,
        "sdf": out_sdf_file,
        "smi": out_sdf_smiles,
        **preparation,
    }


def make_decoy(reference_rmol, decoy_smiles, num_conformers=100):
    """
    Given a reference (rdkit) molecule and a decoy SMILES,
    generate a decoy molecule (both rdkit and openff) and its closest conformer
    to the reference molecule in terms of Tanimoto shape distance.
    """

    # Convert SMILES to 3D structure
    decoy_rmol = Chem.MolFromSmiles(decoy_smiles)
    decoy_rmol = Chem.AddHs(decoy_rmol)

    # Generate conformers
    # AllChem.EmbedMolecule(decoy_rmol) # pretty sure this is unnecessary given EmbedMultipleConfs
    AllChem.EmbedMultipleConfs(decoy_rmol, numConfs=num_conformers)

    centroid_rmol = rdMolTransforms.ComputeCentroid(reference_rmol.GetConformer())

    def translate_conformer_to_reference(conformer) -> None:
        centroid_decoy = rdMolTransforms.ComputeCentroid(conformer)
        translation = centroid_rmol - centroid_decoy
        transformation_matrix = np.eye(4)
        transformation_matrix[:3, 3] = translation
        rdMolTransforms.TransformConformer(conformer, transformation_matrix)

    min_shape_dist = None
    for n, conformer in enumerate(decoy_rmol.GetConformers()):
        translate_conformer_to_reference(conformer)

        shape_dist = rdShapeHelpers.ShapeTanimotoDist(
            reference_rmol, decoy_rmol, confId1=0, confId2=n
        )
        if min_shape_dist is None or shape_dist < min_shape_dist:
            best_conformer_n = n
            min_shape_dist = shape_dist

    # convert to openff Molecule
    decoy_mol = Molecule(decoy_rmol)
    best_conformer = decoy_mol.conformers[best_conformer_n]

    print(
        "best conformer??",
        best_conformer,
        decoy_rmol.GetConformer(best_conformer_n).GetPositions(),
    )
    return decoy_rmol, decoy_mol, best_conformer


def prepare_ligand_for_MD(mol_filename: str, is_sanitize: bool = True):
    """
    Prepare a ligand for Molecular Dynamics (MD) simulation.

    This function reads an sdf or mol2 file into RDKit, adds Hydrogens (Hs) to the molecule,
    ensures all chiral centers are defined, and then creates an openff Molecule object.

    Parameters:
    mol_filename (str): Path to the sdf or mol file to read.
    is_sanitize (bool): Some SDFs are not sanitizable, which is a problem for OpenMM. Defaults to True.

    Returns:
    tuple: A tuple containing:
        - rdkit.Chem.rdchem.Mol: The RDKit molecule object with Hydrogens added.
        - openff.toolkit.topology.Molecule: The openforcefield Molecule object created from RDKit molecule.
    """
    ligand_rmol = Chem.MolFromMolFile(mol_filename, sanitize=is_sanitize)
    ligand_rmol = Chem.AddHs(ligand_rmol, addCoords=True)

    # Ensure the chiral centers are all defined
    Chem.AssignAtomChiralTagsFromStructure(ligand_rmol)
    return ligand_rmol, Molecule(ligand_rmol)


def prepare_system_generator(ligand_mol=None, use_solvent=False):
    """
    Prepare a system generator object for MD simulation.

    The solvent force field is always loaded because it also owns the standard
    monatomic-ion templates. ``use_solvent`` still controls whether water is
    added to the model. The small-molecule force field remains GAFF 2.11.

    Parameters:
    ligand_mol (openforcefield.topology.Molecule, optional): The ligand molecule to include in the system generator.
        Defaults to None.
    use_solvent (bool, optional): Whether the caller will add explicit solvent.

    Returns:
    openmmforcefields.generators.SystemGenerator: The prepared system generator.
    """

    system_generator = SystemGenerator(
        forcefields=[FORCEFIELD_PROTEIN, FORCEFIELD_SOLVENT],
        small_molecule_forcefield=FORCEFIELD_SMALL_MOLECULE,
        # Passing the ligand consistently keeps OpenMM and downstream topology
        # export in the same atom/bond ordering regardless of solvation mode.
        molecules=[ligand_mol] if ligand_mol else [],
        forcefield_kwargs=FORCEFIELD_KWARGS,
        periodic_forcefield_kwargs={"nonbondedMethod": app.PME},
        nonperiodic_forcefield_kwargs={"nonbondedMethod": app.NoCutoff},
    )

    return system_generator


def equilibrate_system(simulation, temperature, equilibration_steps, use_solvent):
    """
    Multi-stage equilibration with gradual heating.

    Protocol:
    1. Gradual heating: 0K → target_temperature (20% of steps)
    2. NVT equilibration: constant volume (40-80% of steps)
    3. NPT equilibration: constant pressure (40% if solvent)
    """
    print("## Equilibrating system...")
    context = simulation.context

    if not isinstance(temperature, unit.Quantity):
        temperature = temperature * unit.kelvin

    # Stage 1: Gradual heating (20% of steps)
    heating_steps = equilibration_steps // 5
    num_heating_stages = 10
    steps_per_stage = heating_steps // num_heating_stages

    print(
        f"  - Stage 1: Gradual heating 0K → {temperature.value_in_unit(unit.kelvin):.0f}K ({heating_steps} steps)"
    )
    for i in range(num_heating_stages):
        fraction = (i + 1) / num_heating_stages
        current_temp = temperature * fraction
        context.setVelocitiesToTemperature(current_temp)
        simulation.step(steps_per_stage)
        if (i + 1) % 3 == 0:
            print(f"    - Heated to {current_temp.value_in_unit(unit.kelvin):.0f}K")

    # Stage 2: NVT equilibration
    if use_solvent:
        nvt_steps = (equilibration_steps - heating_steps) // 2
    else:
        nvt_steps = equilibration_steps - heating_steps

    print(
        f"  - Stage 2: NVT equilibration at {temperature.value_in_unit(unit.kelvin):.0f}K ({nvt_steps} steps)"
    )
    context.setVelocitiesToTemperature(temperature)
    simulation.step(nvt_steps)

    # Stage 3: NPT equilibration (only if solvent)
    if use_solvent:
        npt_steps = equilibration_steps - heating_steps - nvt_steps
        print(f"  - Stage 3: NPT equilibration ({npt_steps} steps)")
        simulation.step(npt_steps)

    print(f"  - Equilibration complete ({equilibration_steps} total steps)\n")


def analyze_traj(
    traj_dcd: str,
    topol_in: str,
    output_traj_analysis: str,
    ligand_chain_id="1",
    backbone_chain_id="0",
) -> pd.DataFrame:
    """
    Analyze trajectory for RMSD of backbone and ligand using mdtraj.

    This function calculates the RMSD (root mean square deviation) for the ligand (assumed to be chainid 1)
    and the backbone (assumed to be chainid 0) over the course of a trajectory. The resulting data is saved
    to a tab-separated CSV file.

    Warning: This function assumes that the ligand is chainid 1 and the protein is chainid 0.

    Parameters:
    traj_in (str): Path to the input trajectory file.
    topol_in (str): Path to the input topology file.
    output_traj_analysis (str): Path to the output CSV file where the RMSD analysis will be saved.

    Returns:
    pandas.DataFrame: A DataFrame containing the RMSD analysis. Each row represents a time point in the trajectory.
                      The columns are 'time' (simulation time), 'rmsd_bck' (RMSD of the backbone),
                      and 'rmsd_lig' (RMSD of the ligand).
    """

    traj = md.load(traj_dcd, top=topol_in)

    # Do not rewrite the source DCD here.  The trajectory is an execution
    # artifact and must remain byte-for-byte stable for provenance and for
    # downstream feature extraction.
    lig_atoms = traj.topology.select(f"chainid {ligand_chain_id}")
    if len(lig_atoms):
        rmsds_lig = md.rmsd(
            traj,
            traj,
            frame=0,
            atom_indices=lig_atoms,
            parallel=False,
            precentered=False,
        )
    else:
        # Protein-only runs are valid OpenMM inputs.  Keep the schema stable,
        # but make the unavailable ligand channel explicit instead of asking
        # MDTraj to calculate an RMSD over an empty atom set.
        rmsds_lig = np.full(traj.n_frames, np.nan, dtype=float)

    # Prefer the configured protein chain, but fall back to the topology-wide
    # backbone for PDBs that do not preserve the original chain numbering.
    bb_atoms = traj.topology.select(f"chainid {backbone_chain_id} and backbone")
    if not len(bb_atoms):
        bb_atoms = traj.topology.select("backbone")
    if not len(bb_atoms):
        raise ValueError("OPENMM_TRAJECTORY_BACKBONE_SELECTION_EMPTY")
    rmsds_bck = md.rmsd(
        traj,
        traj,
        frame=0,
        atom_indices=bb_atoms,
        parallel=False,
        precentered=False,
    )

    print(
        f"Topology:\n"
        f"- {traj.topology} with n_frames={traj.n_frames}\n"
        f"- {len(lig_atoms)} ligand atoms\n"
        f"- {len(bb_atoms)} backbone atoms"
    )

    df_traj = (
        pd.DataFrame([traj.time, rmsds_bck, rmsds_lig])
        .T.map(lambda x: round(x, 8))
        .rename(columns={0: "time", 1: "rmsd_bck", 2: "rmsd_lig"})
    )

    df_traj.to_csv(output_traj_analysis, sep="\t", index=False)

    return df_traj


def get_affinity(
    pdb_in: str,
    ligand_id: str,
    convert_to_pdbqt: bool = False,
    scoring_tool: str = GNINA,
) -> float:
    """
    Calculates the predicted binding affinity of a molecule to a protein using gnina.
    The lower the binding affinity, the stronger the expected binding.

    Due to a weird intermittent bug in gnina, allow conversion of the
    PDB file to PDBQT format with obabel. I am still unclear on what's going on here.

    Parameters:
    pdb_in (str): The path to the input protein+ligand PDB file.
    ligand_id (str): The id of the ligand within the PDB file.
    convert_to_pdbqt (bool): If True, convert the PDB file to PDBQT format before running gnina.
        Defaults to False.
    scoring_tool (str): Defaults to 'gnina'.

    Returns:
    float: The predicted binding affinity of the ligand to the protein.
    """
    gnina_affinity_pattern = r"Affinity:\s*([\-\.\d+]+)"

    with (
        NamedTemporaryFile("w", suffix="_ligand.pdb", delete=False) as gn_ligand_pdb,
        NamedTemporaryFile("w", suffix="_protein.pdb", delete=False) as gn_prot_pdb,
        NamedTemporaryFile("w", suffix="_protein.pdbqt", delete=False) as gn_prot_pdbqt,
    ):
        for line in open(pdb_in, encoding="utf-8"):
            if (
                line.startswith("HETATM")
                and line[17:20] == ligand_id
                or line.startswith("CONECT")
            ):
                gn_ligand_pdb.write(line)
            elif not line.startswith("HETATM"):
                gn_prot_pdb.write(line)
        gn_ligand_pdb.flush()
        gn_ligand_pdb.seek(0)
        gn_prot_pdb.flush()
        gn_prot_pdb.seek(0)

        # convert pdb to pdbqt too to get flexible side chains? In theory MD sorts this out
        num_cpus = max(1, (os.cpu_count() or 1) - 1)
        if convert_to_pdbqt:
            conversion_cmd = [binaries[OBABEL], gn_prot_pdb.name, "-O", gn_prot_pdbqt.name]
            print(f"- Preparing receptor: {subprocess.list2cmdline(conversion_cmd)}")
            subprocess.run(conversion_cmd, check=True, capture_output=True)
            receptor = gn_prot_pdbqt.name
        else:
            receptor = gn_prot_pdb.name
        cmd = [
            binaries[scoring_tool],
            "--cpu",
            str(num_cpus),
            "--score_only",
            "-r",
            receptor,
            "-l",
            gn_ligand_pdb.name,
        ]
        print(f"- Calculating score: {subprocess.list2cmdline(cmd)}")
        gnina_run = subprocess.run(cmd, check=True, capture_output=True)
        gnina_out = gnina_run.stdout.decode("ascii")

    affinity = float(re.findall(gnina_affinity_pattern, gnina_out)[0])

    return affinity


def extract_pdbs_from_dcd(
    complex_pdb: str, trajectory_dcd: str, min_frames: int = 10, max_frames: int = 1000
) -> dict:
    """
    Extracts individual PDB structures from a molecular dynamics trajectory (DCD file)
    and stores them in a dictionary.

    This function takes as input a PDB file representing the initial structure of the system,
    and a DCD file containing the molecular dynamics trajectory. It iterates over the trajectory
    and writes out individual PDB files for each frame.

    Parameters:
    complex_pdb : str
        Path to the initial PDB complex file (output of openmm), the structure of protein + ligand.

    trajectory_dcd : str
        Path to the DCD file containing the molecular dynamics trajectory.

    Returns:
    dict: keys are simulation time points in picoseconds and values are paths to PDB files.
    """
    universe = mda.Universe(complex_pdb, trajectory_dcd)

    # stride = max(100, len(universe.trajectory) // 1000)
    if len(universe.trajectory) < 100_000:
        target_frames = max(10, len(universe.trajectory) // 200)
    else:
        target_frames = min(500, len(universe.trajectory) // 1000)
    stride = max(1, len(universe.trajectory) // target_frames)

    traj_pdbs = {}
    for ts in tqdm(universe.trajectory[::stride]):
        time_ps = round(ts.time, 2)

        traj_filename = f"{Path(complex_pdb).stem}_f{time_ps}.pdb"
        traj_pdbs[time_ps] = str(Path(complex_pdb).parent / traj_filename)
        with PDBWriter(traj_pdbs[time_ps]) as out_pdb:
            out_pdb.write(universe.atoms)

    return traj_pdbs


def simulate(
    pdb_in: str,
    mol_in: str,
    output: str,
    num_steps: int,
    use_solvent: bool = False,
    decoy_smiles: str | None = None,
    minimize_only: bool = False,
    temperature: float = PDB_TEMPERATURE,
    equilibration_steps: int = 200,
    reporting_interval: int | None = None,
    scoring_tool: str | None = GNINA,
    checkpoint: str | None = None,
    restart_from: str | None = None,
    segment_steps: int | None = None,
    platform_name: str = "auto",
    allow_platform_fallback: bool = True,
) -> dict:
    """
    Run a molecular dynamics simulation using OpenMM.

    This function simulates the interactions between a protein (from a PDB file)
    and a ligand (from a MOL/SDF file).
    The simulation can be performed in vacuum or in solvent.
    The simulation outputs are saved in several file formats.

    Parameters:
    pdb_in (str): Path to the input PDB file containing the protein structure.
    mol_in (str): Path to the input MOL/SDF file containing the ligand structure.
    output (str): The prefix of the output file names.
    num_steps (int): The number of simulation steps to be performed.
    use_solvent (bool): If True, the simulation is performed in solvent. If False, it is performed in vacuum.
    decoy_smiles (str): The SMILES string of a decoy ligand. If not None, the decoy is used instead of the input ligand.
    temperature (float): The simulation temperature in Kelvin.
    equilibration_steps (int): The number of steps for the equilibration phase of the simulation.
    reporting_interval (int): The interval (in steps) at which the simulation data is recorded. Leave as None to get a number based on num_steps.

    Returns:
    pandas.DataFrame: A DataFrame containing the RMSD analysis of the trajectory.
    """

    os.makedirs(Path(output).parent, exist_ok=True)
    output_complex_pdb = f"{output}_complex.pdb"
    output_minimized_pdb = f"{output}_minimized.pdb"
    output_affinity_tsv = f"{output}_affinity.tsv"
    if minimize_only is not True:
        output_traj_dcd = f"{output}_traj.dcd"
        output_state_tsv = f"{output}_state.tsv"
        output_analysis_tsv = f"{output}_analysis.tsv"
    output_checkpoint = checkpoint or f"{output}_checkpoint.chk"
    output_args_json = f"{output}_args.json"
    output_system_xml = f"{output}_system.xml"
    output_atom_map_json = f"{output}_atom_map.json"
    json.dump(locals(), open(output_args_json, "w", encoding="utf-8"), indent=2)

    out_affinity = None
    if scoring_tool not in (None, "", "none"):
        out_affinity = open(output_affinity_tsv, "w", encoding="utf-8")
        out_affinity.write("time_ps\taffinity\n")

    if num_steps is None:
        num_steps = 1

    # A reasonable number based on the number of steps unless explicitly set.
    if reporting_interval is None:
        max_frames_to_report = 100
        reporting_interval = max(1, min(10_000, num_steps // max_frames_to_report))
    else:
        reporting_interval = max(1, reporting_interval)

    print(f"Processing {pdb_in} and {mol_in} with {num_steps} steps")

    # -------------------------------------------------------
    # Set up system
    #

    if mol_in is not None:
        print(f"# Preparing ligand:\n- {mol_in}\n")
        ligand_rmol, ligand_mol = prepare_ligand_for_MD(mol_in)
        ligand_conformer = ligand_mol.conformers[0]
        assert len(ligand_mol.conformers) == len(ligand_rmol.GetConformers()) == 1, (
            "reference ligand should have one conformer"
        )
    elif decoy_smiles is not None:
        raise ValueError("decoy_smiles_requires_reference_ligand")
    else:
        ligand_rmol, ligand_mol, ligand_conformer = None, None, None

    # Initialize a SystemGenerator using the GAFF for the ligand and tip3p for the water.
    # Chat-GPT: To use a larger time step, artificially increase the mass of the hydrogens.
    print("# Preparing system")
    system_generator = prepare_system_generator(ligand_mol, use_solvent)

    # Use Modeller to combine the protein and ligand into a complex
    print("# Reading protein")
    protein_pdb = PDBFile(pdb_in)

    print("# Preparing complex")
    modeller = Modeller(protein_pdb.topology, protein_pdb.positions)
    print(f"- System has {modeller.topology.getNumAtoms()} atoms after adding protein")

    # This next bit is black magic.
    # Modeller needs topology and positions. Lots of trial and error found that this is what works to get
    # these from an openforcefield Molecule object that was created from a RDKit molecule.
    # The topology part is described in the openforcefield API but the positions part grabs the first
    # (and only) conformer and passes it to Modeller. It works. Don't ask why!
    # modeller.topology.setPeriodicBoxVectors([Vec3(x=8.461, y=0.0, z=0.0),
    # Vec3(x=0.0, y=8.461, z=0.0), Vec3(x=0.0, y=0.0, z=8.461)])
    if ligand_mol is not None:
        modeller.add(
            ligand_mol.to_topology().to_openmm(), ligand_mol.conformers[0].to_openmm()
        )
        print(
            f"- System has {modeller.topology.getNumAtoms()} atoms after adding ligand"
        )

    if use_solvent:
        # We use the 'padding' option to define the periodic box. The PDB file does not contain any
        # unit cell information so we just create a box that has a 10A padding around the complex.
        modeller.addSolvent(
            system_generator.forcefield, model="tip3p", padding=SOLVENT_PADDING
        )
        print(
            f"- System has {modeller.topology.getNumAtoms()} atoms after adding solvent"
        )

    # Output the complex with topology
    with open(output_complex_pdb, "w", encoding="utf-8") as out:
        PDBFile.writeFile(modeller.topology, modeller.positions, out)

    if ligand_mol is not None and out_affinity is not None:
        affinity = get_affinity(
            output_complex_pdb, OPENMM_DEFAULT_LIGAND_ID, scoring_tool=scoring_tool
        )
        out_affinity.write(f"complex\t{affinity:.4f}\n")

    system = system_generator.create_system(modeller.topology, molecules=ligand_mol)
    expected_disulfides = {frozenset((a.index, b.index)) for a, b in modeller.topology.bonds()
                           if a.name == b.name == 'SG'}
    system_bonds = set()
    for force in system.getForces():
        if force.__class__.__name__ == 'HarmonicBondForce':
            for index in range(force.getNumBonds()):
                first, second, _, _ = force.getBondParameters(index)
                system_bonds.add(frozenset((int(first), int(second))))
    if not expected_disulfides.issubset(system_bonds):
        raise ValueError('DISULFIDE_MISSING_FROM_PARAMETERIZED_SYSTEM')
    disulfide_validation = {'topology_pairs': _disulfide_records(modeller.topology),
                            'parameterized_bond_count': len(expected_disulfides),
                            'validated': True}

    integrator = LangevinIntegrator(temperature, FRICTION_COEFF, STEP_SIZE)

    # This line is present in the WithSolvent.py version of the script but unclear why
    if use_solvent:
        system.addForce(
            MonteCarloBarostat(BAROSTAT_PRESSURE, temperature, BAROSTAT_FREQUENCY)
        )

    Path(output_system_xml).write_text(
        XmlSerializer.serialize(system), encoding="utf-8"
    )
    atom_map = []
    for atom in modeller.topology.atoms():
        atom_map.append(
            {
                "index": atom.index,
                "name": atom.name,
                "element": atom.element.symbol if atom.element is not None else None,
                "residue_name": atom.residue.name,
                "residue_id": atom.residue.id,
                "chain_id": atom.residue.chain.id,
            }
        )
    Path(output_atom_map_json).write_text(
        json.dumps(atom_map, indent=2) + "\n", encoding="utf-8"
    )

    print(
        f"- Using Periodic box: {system.usesPeriodicBoundaryConditions()}\n"
        f"- Default Periodic box: {system.getDefaultPeriodicBoxVectors()}\n"
    )

    # -------------------------------------------------------
    # Run simulation
    #
    simulation, active_platform = create_simulation_with_platform_fallback(
        modeller.topology,
        system,
        integrator,
        modeller.positions,
        preferred_platform=platform_name,
        allow_fallback=allow_platform_fallback,
    )
    context = simulation.context

    if restart_from:
        print(f"# Loading checkpoint: {restart_from}")
        with open(restart_from, "rb") as checkpoint_in:
            simulation.context.loadCheckpoint(checkpoint_in.read())
    else:
        print("# Minimising ...")
        simulation.minimizeEnergy()

        # Write out the minimized PDB.
        # 'enforcePeriodicBox=False' is important otherwise the different components can end up in
        # different periodic boxes resulting in really strange looking output.
        with open(output_minimized_pdb, "w", encoding="utf-8") as out:
            PDBFile.writeFile(
                modeller.topology,
                context.getState(
                    getPositions=True, enforcePeriodicBox=False
                ).getPositions(),
                file=out,
                keepIds=True,
            )

        if ligand_mol is not None and out_affinity is not None:
            affinity = get_affinity(
                output_minimized_pdb, OPENMM_DEFAULT_LIGAND_ID, scoring_tool=scoring_tool
            )
            out_affinity.write(f"min\t{affinity:.4f}\n")
            out_affinity.flush()

    if minimize_only:
        return {
            "disulfide_validation": disulfide_validation,
            "complex_pdb": output_complex_pdb,
            "minimized_pdb": output_minimized_pdb,
            "affinity_tsv": output_affinity_tsv if out_affinity is not None else None,
            "args_json": output_args_json,
            "system_xml": output_system_xml,
            "atom_map_json": output_atom_map_json,
            "openmm_platform": active_platform,
        }

    if restart_from:
        print("## Skipping minimization/equilibration because restart_from was provided")
    else:
        print("## Equilibrating ...")
        equilibrate_system(simulation, temperature, equilibration_steps, use_solvent)

    # Run the simulation.
    # The enforcePeriodicBox arg to the reporters is important.
    # It's a bit counter-intuitive that the value needs to be False, but this is needed to ensure
    # that all parts of the simulation end up in the same periodic box when being output.
    # optional: simulation.reporters.append(PDBReporter(output_traj_pdb, reporting_interval,
    #                                       enforcePeriodicBox=False))
    simulation.reporters.append(
        DCDReporter(output_traj_dcd, reporting_interval, enforcePeriodicBox=False)
    )
    simulation.reporters.append(CheckpointReporter(output_checkpoint, reporting_interval))
    simulation.reporters.append(
        StateDataReporter(
            output_state_tsv,
            reporting_interval,
            step=True,
            potentialEnergy=True,
            temperature=True,
        )
    )

    print(f"# Starting simulation with {num_steps} steps ...")
    time_0 = time.time()
    if segment_steps and segment_steps > 0 and segment_steps < num_steps:
        steps_remaining = num_steps
        segment_n = 0
        while steps_remaining > 0:
            segment_n += 1
            steps_this_segment = min(segment_steps, steps_remaining)
            print(
                f"- Segment {segment_n}: running {steps_this_segment} steps "
                f"({steps_remaining - steps_this_segment} remaining)"
            )
            simulation.step(steps_this_segment)
            with open(output_checkpoint, "wb") as checkpoint_out:
                checkpoint_out.write(simulation.context.createCheckpoint())
            steps_remaining -= steps_this_segment
    else:
        simulation.step(num_steps)
        with open(output_checkpoint, "wb") as checkpoint_out:
            checkpoint_out.write(simulation.context.createCheckpoint())
    time_1 = time.time()
    print(f"- Simulation complete in {time_1 - time_0} seconds at {temperature}K\n")

    # -------------------------------------------------------
    # Calculate affinities during the simulation
    #
    # Extract per-frame PDBs only when a ligand affinity calculation was
    # explicitly requested.  Protein-only runs have no affinity consumer; the
    # MDAnalysis round-trip is unnecessary and has caused crashes for some
    # vacuum DCD/topology combinations.  The trajectory itself remains the
    # authoritative OpenMM output for downstream MDTraj feature extraction.
    print("# Calculating affinities along trajectory...")
    traj_pdbs = {}
    if ligand_mol is not None and out_affinity is not None:
        traj_pdbs = extract_pdbs_from_dcd(output_complex_pdb, output_traj_dcd)
        traj_affinities = {
            time_ps: get_affinity(
                traj_pdb, OPENMM_DEFAULT_LIGAND_ID, scoring_tool=scoring_tool
            )
            for time_ps, traj_pdb in traj_pdbs.items()
        }
        for time_ps, affinity in traj_affinities.items():
            out_affinity.write(f"{time_ps:.2f}\t{affinity:.4f}\n")

    print("# Running trajectory analysis...")
    df_traj = analyze_traj(output_traj_dcd, output_complex_pdb, output_analysis_tsv)

    # Fix the state data file: from csv to tsv
    (
        pd.read_csv(output_state_tsv, sep=",")
        .map(lambda x: round(x, 4) if isinstance(x, float) else x)
        .to_csv(output_state_tsv, sep="\t", index=False)
    )

    return {
        "disulfide_validation": disulfide_validation,
        "complex_pdb": output_complex_pdb,
        "traj_dcd": output_traj_dcd,
        "minimized_pdb": output_minimized_pdb,
        "affinity_tsv": output_affinity_tsv if out_affinity is not None else None,
        "args_json": output_args_json,
        "system_xml": output_system_xml,
        "atom_map_json": output_atom_map_json,
        "state_tsv": output_state_tsv,
        "analysis_tsv": output_analysis_tsv,
        "openmm_platform": active_platform,
        "checkpoint": output_checkpoint,
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="OpenMM protein-ligand simulation")
    parser.add_argument("pdb_in", type=str, help="Input PDB file path")
    parser.add_argument("mol_in", type=str, help="Input mol file path")
    parser.add_argument(
        "output", type=str, help="Output file name root, including path"
    )
    parser.add_argument("num_steps", type=int, help="Number of simulation steps")
    parser.add_argument("--use_solvent", action="store_true", help="Use solvent?")
    parser.add_argument(
        "--decoy_smiles",
        type=str,
        default=None,
        help="Use a decoy aligned to mol_in for simulation",
    )
    parser.add_argument(
        "--minimize_only", action="store_true", help="Only perform minimization"
    )
    parser.add_argument(
        "--temperature", type=float, default=300.0, help="Temperature in Kelvin"
    )
    parser.add_argument(
        "--equilibration_steps", type=int, default=200, help="Equilibration steps"
    )
    parser.add_argument(
        "--reporting_interval", type=int, default=None, help="Reporting interval"
    )
    args = parser.parse_args()

    simulate(
        args.pdb_in,
        args.mol_in,
        args.output,
        args.num_steps,
        use_solvent=args.use_solvent,
        decoy_smiles=args.decoy_smiles,
        minimize_only=args.minimize_only,
        temperature=args.temperature,
        equilibration_steps=args.equilibration_steps,
        reporting_interval=args.reporting_interval,
    )
