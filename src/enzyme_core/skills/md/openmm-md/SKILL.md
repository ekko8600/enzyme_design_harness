---
name: openmm-md
description: 'Run local OpenMM molecular dynamics for protein-ligand complexes. Use
  this skill when preparing a PDB for MD, extracting a bound ligand to SDF, applying
  simple PDBFixer mutations, minimizing a protein-ligand complex, running short or
  100 ns OpenMM production simulations, or restarting from checkpoints.

  '
metadata:
  category: md
  tags: molecular-dynamics, openmm, protein-ligand, simulation, trajectory
  legacy_category: design-tools
  taxonomy_version: enzyme-agent/taxonomy/v1
  capability_ids:
  - science.general_operation
---

# OpenMM Protein-Ligand MD

Use this skill for local molecular dynamics of a protein or protein-ligand
complex. The bundled scripts are adapted from `biomodals/MD_protein_ligand` and
live in `scripts/`:

- `run_openmm_md.py`: stable OpenCode CLI wrapper for common workflows.
- `simulate.py`: OpenMM/OpenFF simulation engine and trajectory analysis.
- `extract_ligands.py`: ligand extraction and bond-order assignment from PDB.
- `mutate_pdb.py`: optional PyMOL-based mutation helper.

## Requirements

Install chemistry dependencies in an environment that supports OpenMM:

```bash
conda install -c conda-forge openmm pdbfixer mdtraj mdanalysis rdkit openff-toolkit openmmforcefields parmed prody pypdb pandas numpy tqdm
```

`ParmEd` remains part of the supported topology-inspection stack. For
single-trajectory MM/GBSA, start from the pre-parameterized
`--amber-prmtop + --amber-coordinates` mode below; its manifest records that
`prmtop` as `analysis_topology`. The default PDB/OpenFF route does not promise
an Amber topology and must not advertise one.

For ligand affinity scoring, also install `gnina` and `openbabel` (`obabel`) and
pass `--scoring-tool gnina`. Scoring is disabled by default so minimization and
MD can run without docking binaries.

## Common Workflows

Prepare a PDB ID, extract a ligand by residue ID and chain, then run a short
non-solvated smoke-test MD:

```bash
enzyme-core run openmm-md --work-dir ./runs/openmm-md --operation run_openmm_md -- \
  --pdb-id 1A1O \
  --ligand-id XXX \
  --ligand-chain A \
  --out-dir out/openmm-md/1A1O \
  --num-steps 5000 \
  --equilibration-steps 1000
```

Run only minimization for a local protein and ligand SDF:

```bash
enzyme-core run openmm-md --work-dir ./runs/openmm-md --operation run_openmm_md -- \
  --protein-pdb target.pdb \
  --ligand-sdf ligand.sdf \
  --out-dir out/openmm-md/test \
  --run-name target_ligand_min \
  --minimize-only
```

Local structures can be PDB, CIF, or mmCIF. CIF/mmCIF inputs are converted to
PDB before setup.

Run explicit-solvent MD:

```bash
enzyme-core run openmm-md --work-dir ./runs/openmm-md --operation run_openmm_md -- \
  --protein-pdb target.pdb \
  --ligand-sdf ligand.sdf \
  --use-solvent \
  --num-steps 50000 \
  --equilibration-steps 5000
```

Run a restartable 100 ns explicit-solvent production simulation:

```bash
enzyme-core run openmm-md --work-dir ./runs/openmm-md --operation run_openmm_md -- \
  --protein-pdb complex.pdb \
  --ligand-id LIG \
  --ligand-chain B \
  --simulation-time-ns 100 \
  --use-solvent \
  --segment-ns 5 \
  --report-interval-ps 100 \
  --checkpoint out/openmm-md/run1/run1_checkpoint.chk \
  --out-dir out/openmm-md/run1
```

Restart from a checkpoint:

```bash
enzyme-core run openmm-md --work-dir ./runs/openmm-md --operation run_openmm_md -- \
  --protein-pdb complex.pdb \
  --ligand-id LIG \
  --ligand-chain B \
  --simulation-time-ns 20 \
  --use-solvent \
  --restart-from out/openmm-md/run1/run1_checkpoint.chk \
  --out-dir out/openmm-md/run1_restart
```

Enable heuristic GNINA affinity scoring when the binaries are installed:

```bash
enzyme-core run openmm-md --work-dir ./runs/openmm-md --operation run_openmm_md -- \
  --protein-pdb target.pdb \
  --ligand-sdf ligand.sdf \
  --scoring-tool gnina \
  --num-steps 10000
```

Apply a PDBFixer mutation before setup:

```bash
enzyme-core run openmm-md --work-dir ./runs/openmm-md --operation run_openmm_md -- \
  --protein-pdb complex.pdb \
  --ligand-id ATP \
  --ligand-chain B \
  --mutation TYR-65-ILE-A \
  --num-steps 10000
```

## Output Files

The wrapper writes a manifest JSON plus files from `simulate.py`:

- `<run>_complex.pdb`: initial OpenMM complex.
- `<run>_minimized.pdb`: minimized structure.
- `<run>_traj.dcd`: trajectory, unless `--minimize-only`.
- `<run>_state.tsv`: potential energy and temperature over time.
- `<run>_analysis.tsv`: backbone and ligand RMSD.
- `<run>_checkpoint.chk`: OpenMM checkpoint for restartable production runs.
- `<run>_affinity.tsv`: GNINA scores when `--scoring-tool gnina` is used.
- `<run>_args.json`: low-level simulation arguments.
- `<run>_system.xml`: exact serialized OpenMM System used for production.
- `<run>_atom_map.json`: topology index/name/residue/chain mapping.
- `<run>_atom_groups.json`: protein, heme, substrate, solvent and ion groups
  in pre-parameterized Amber mode.
- `<run>_manifest.json`: full run metadata and configuration, including every
  recognized input structure ion and whether it was retained or explicitly stripped.

## Decision Rules

- Prefer `--minimize-only` first for new systems; inspect the minimized PDB before
  launching longer MD.
- Use short implicit-solvent runs for smoke tests and rough stability checks.
- Use `--use-solvent` for more realistic production-like behavior; expect more
  atoms, slower setup, and slower simulation.
- Use `--simulation-time-ns 100 --segment-ns 5 --checkpoint <path>` for long
  production runs so failures can restart from the latest checkpoint.
- Use prepared ligand SDF/MOL files when PDB ligand bond-order assignment fails.
- Supported monatomic structure ions (including `CA`, `MG`, `ZN`, `NA`, `K` and
  halides) are receptor components and are retained by default. Do not pass them
  as `--ligand-id`; the ligand extractor excludes them from OpenFF charge assignment.
- Use `--strip-structure-ions` only as an explicit scientific choice. Unknown
  inorganic monatomic residues fail closed instead of being silently deleted.
- Standard Amber ion templates describe nonbonded ions; they do not establish a
  valid coordination or covalent metal-site model. Use pre-parameterized Amber
  inputs for catalytic metals, heme, unusual oxidation states or bonded ions.
- Treat GNINA affinity values as heuristic scores, not binding free energies.
- Report dependency failures plainly; do not silently reinterpret a failed MD run
  as a scientific result.

## Pre-parameterized Amber systems

Use an existing `prmtop + rst7/inpcrd` for P450 heme, Compound I, covalent
cofactor, membrane or multiple-substrate systems. This mode bypasses PDBFixer
and preserves the prepared chemistry:

```bash
enzyme-core run openmm-md --work-dir ./runs/openmm-md --operation run_openmm_md -- \
  --amber-prmtop cpdi_dual_substrate.prmtop \
  --amber-coordinates cpdi_dual_substrate.rst7 \
  --out-dir out/openmm-md/cpdi --run-name cpdi-r1 \
  --simulation-time-ns 100 --segment-ns 5
```

Every run writes `system.xml`, a complete topology PDB, atom-index mapping,
chemical atom groups, input hashes, simulation parameters, periodic-box
metadata and OpenMM/Python versions in an `openmm-md-run/v2` manifest. Preserve
this manifest with the trajectory as the provenance basis for recalculation
and interaction-energy analysis.

## Registered protein trajectory analysis

`analyze_protein_trajectory` consumes `--trajectory` DCD, `--topology` complex PDB, `--manifest` openmm-md-run/v2 JSON, and `--state` TSV using explicit artifact bindings, and writes to `--out-dir`. Outputs: timeseries.csv, rmsf.csv, summary.json, md_overview.png, flexibility_calcium.png, structure_overlay.png, trajectory.gif. Timeseries has time_ns, rmsd_backbone_A, radius_gyration_A, temperature_K, potential_energy_kJ_mol and per-calcium nearest-protein-oxygen distances. RMSF has residue_id and rmsf_CA_A.

This operation verifies manifest source hashes and derives production time from actual reporter steps and equilibration count, ignoring DCD loader default times. It supports fresh runs only, validates the full declared duration and frame correspondence, excludes water/ions from protein metrics, and fits the backbone to the first saved production frame for RMSD/RMSF. It does not interpret the legacy rmsd_lig channel as a ligand in a protein-only system. Atomic nonbonded calcium remains an approximate coordination model. A single 1 ns run is a pipeline test, not a convergence or biochemical activity conclusion.

### Native disulfide preservation
Preparation preserves the input topology's SG–SG disulfide pairs through PDBFixer topology reconstruction. Inconsistent HG hydrogens on those cystines are removed before hydrogen assignment; their count and original residue identities are recorded in preparation provenance. Missing sulfur atoms or changed disulfide topology fail preparation. Both minimization-only and production results report `disulfide_validation`, confirming that every topology S–S bond occurs in the parameterized HarmonicBondForce. Do not continue from a previously prepared thiol model when a native disulfide was lost; prepare a new attempt from the original input.
