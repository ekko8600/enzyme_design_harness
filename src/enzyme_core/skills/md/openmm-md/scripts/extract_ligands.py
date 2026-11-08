"""Extract protein and ligand files from a PDB structure.

This project implementation uses ProDy for atom selection and RDKit for bond
order assignment. It intentionally contains no code copied from the historical
gist implementations that previously occupied this file.
"""

from __future__ import annotations

import argparse
from io import StringIO
from pathlib import Path
from typing import Any

from rdkit import Chem
from rdkit.Chem import AllChem

import prody
import pypdb


# Residue/template names supplied by amber/tip3p_standard.xml for common
# monatomic ions.  These components belong to the receptor environment and
# must never be sent through RDKit/OpenFF ligand charge assignment.
SUPPORTED_STRUCTURE_IONS: dict[str, str] = {
    "NA": "Na", "K": "K", "LI": "Li", "RB": "Rb", "CS": "Cs",
    "CL": "Cl", "F": "F", "BR": "Br", "IOD": "I",
    "MG": "Mg", "CA": "Ca", "SR": "Sr", "BA": "Ba",
    "ZN": "Zn", "MN": "Mn", "FE": "Fe", "FE2": "Fe",
    "CU": "Cu", "CO": "Co", "NI": "Ni", "CD": "Cd",
}


def is_supported_structure_ion(residue_name: str) -> bool:
    return residue_name.strip().upper() in SUPPORTED_STRUCTURE_IONS


def _parse_structure(pdb_name: str):
    path = Path(pdb_name)
    if "." in pdb_name and not path.exists():
        raise FileNotFoundError(f"PDB path does not exist: {path}")
    structure = prody.parsePDB(pdb_name)
    if structure is None:
        raise ValueError(f"ProDy could not parse structure: {pdb_name}")
    return structure


def _chemical_component_smiles(residue_name: str) -> str:
    description: dict[str, Any] = pypdb.describe_chemical(residue_name)
    descriptor = description.get("rcsb_chem_comp_descriptor") or {}
    component_id = str(descriptor.get("comp_id", "")).upper()
    if component_id and component_id != residue_name.upper():
        raise ValueError(
            f"RCSB chemical-component mismatch: requested {residue_name}, received {component_id}"
        )
    smiles = descriptor.get("smilesstereo") or descriptor.get("smiles")
    if not smiles:
        raise ValueError(f"RCSB returned no SMILES for chemical component {residue_name}")
    return str(smiles)


def _selection_as_pdb_block(selection) -> str:
    stream = StringIO()
    prody.writePDBStream(stream, selection)
    block = stream.getvalue()
    if not block.strip():
        raise ValueError("selected ligand produced an empty PDB block")
    return block


def _ligand_molecule(structure, residue_name: str, chain: str | None, smiles: str | None):
    if is_supported_structure_ion(residue_name):
        raise ValueError(
            f"{residue_name} is a supported structure ion, not an OpenFF ligand; "
            "leave it in the receptor structure"
        )
    selection = structure.select(
        f"resname {residue_name}" if not chain else f"resname {residue_name} and chain {chain}"
    )
    if selection is None or selection.numAtoms() == 0:
        raise ValueError(f"ligand {residue_name} was not found in chain {chain or '*'}")
    if not chain:
        chains = sorted(set(selection.getChids()))
        if len(chains) != 1:
            raise ValueError(f"ligand {residue_name} occurs in multiple chains; specify --ligand-chain")
        chain = chains[0]

    resolved_smiles = smiles or _chemical_component_smiles(residue_name)
    template = Chem.MolFromSmiles(resolved_smiles)
    if template is None:
        raise ValueError(f"RDKit could not parse ligand SMILES: {resolved_smiles}")
    observed = Chem.MolFromPDBBlock(
        _selection_as_pdb_block(selection), removeHs=False, sanitize=False
    )
    if observed is None:
        raise ValueError(f"RDKit could not parse ligand coordinates for {residue_name}")
    try:
        molecule = AllChem.AssignBondOrdersFromTemplate(template, observed)
        Chem.SanitizeMol(molecule)
    except Exception as exc:
        raise ValueError(
            f"failed to assign bond orders for {residue_name} in chain {chain}: {exc}"
        ) from exc
    return molecule, resolved_smiles


def _write_sdf(molecule, output_path: str) -> str:
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    writer = Chem.SDWriter(str(destination))
    try:
        writer.write(molecule)
    finally:
        writer.close()
    if not destination.is_file() or destination.stat().st_size == 0:
        raise OSError(f"failed to write ligand SDF: {destination}")
    return str(destination)


def extract_ligand(
    pdb_name: str,
    ligand_name: str,
    ligand_chain: str | None,
    out_pdb_file: str,
    out_sdf_file: str | None = None,
    ligand_smiles: str | None = None,
) -> tuple[str, str, str]:
    """Write the protein and one selected ligand, returning both paths and SMILES."""
    structure = _parse_structure(pdb_name)
    protein = structure.select("protein")
    if protein is None or protein.numAtoms() == 0:
        raise ValueError(f"structure contains no protein atoms: {pdb_name}")

    protein_path = Path(out_pdb_file)
    protein_path.parent.mkdir(parents=True, exist_ok=True)
    prody.writePDB(str(protein_path), protein)
    if not protein_path.is_file() or protein_path.stat().st_size == 0:
        raise OSError(f"failed to write protein PDB: {protein_path}")

    molecule, resolved_smiles = _ligand_molecule(
        structure, ligand_name, ligand_chain, ligand_smiles
    )
    sdf_path = out_sdf_file or str(
        protein_path.with_name(f"{protein_path.stem}_{ligand_name}_{ligand_chain}.sdf")
    )
    return str(protein_path), _write_sdf(molecule, sdf_path), resolved_smiles


def extract_all_ligands(pdb_name: str, out_pdb_name: str) -> list[tuple[str, str, str]]:
    """Extract every non-polymer, non-water residue as a separate ligand."""
    structure = _parse_structure(pdb_name)
    ligands = structure.select("not (protein or nucleic or water)")
    if ligands is None:
        return []
    pairs = sorted(
        (residue, chain)
        for residue, chain in set(zip(ligands.getResnames(), ligands.getChids()))
        if not is_supported_structure_ion(residue)
    )
    output_base = Path(out_pdb_name)
    return [
        extract_ligand(
            pdb_name,
            residue,
            chain,
            out_pdb_name,
            str(output_base.with_name(f"{output_base.stem}_{residue}_{chain}.sdf")),
        )
        for residue, chain in pairs
    ]


def extract_ligand_protein(
    pdb_name: str,
    ligand_chain: str,
    out_pdb_file: str,
    out_sdf_file: str | None = None,
    ligand_smiles: str | None = None,
):
    """Select and write all atoms outside a peptide-ligand chain."""
    del out_sdf_file, ligand_smiles
    structure = _parse_structure(pdb_name)
    protein = structure.select(f"not chain {ligand_chain}")
    if protein is None or protein.numAtoms() == 0:
        raise ValueError(f"no receptor atoms remain after excluding chain {ligand_chain}")
    destination = Path(out_pdb_file)
    destination.parent.mkdir(parents=True, exist_ok=True)
    prody.writePDB(str(destination), protein)
    return protein


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Extract protein and ligand files from a PDB.")
    parser.add_argument("pdb_id", help="Local PDB path or a PDB identifier accepted by ProDy")
    parser.add_argument("--ligand_names", help="Comma-delimited ligand residue names")
    parser.add_argument("--chains", help="Comma-delimited chain IDs matching --ligand_names")
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    protein_path = f"{args.pdb_id}_protein.pdb"
    if not args.ligand_names:
        for result in extract_all_ligands(args.pdb_id, protein_path):
            print(result)
        return 0
    names = [item.strip() for item in args.ligand_names.split(",") if item.strip()]
    chains = [item.strip() for item in (args.chains or "").split(",") if item.strip()]
    if len(names) != len(chains):
        raise SystemExit("--ligand_names and --chains must contain the same number of values")
    for residue, chain in zip(names, chains):
        print(extract_ligand(args.pdb_id, residue, chain, protein_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
