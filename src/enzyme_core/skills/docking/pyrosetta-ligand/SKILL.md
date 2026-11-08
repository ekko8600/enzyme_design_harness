---
name: pyrosetta-ligand
description: 'Guide PyRosetta ligand workflows including ligand params, ligand pose
  setup, local/global ligand docking, and GALigandDock-style workflows. Use this skill
  for protein-small-molecule Rosetta modeling rather than OpenMM MD.

  '
metadata:
  category: docking
  tags: pyrosetta, ligand, docking, small-molecule
  legacy_category: design-tools
  taxonomy_version: enzyme-agent/taxonomy/v1
  capability_ids:
  - structure.interaction_assessment
---

# PyRosetta Ligand

Use this skill for Rosetta ligand modeling.

## Workflow

1. Prepare receptor PDB and ligand structure.
2. Generate Rosetta ligand params outside this wrapper using Rosetta tooling.
3. Load receptor/ligand Pose with params enabled via PyRosetta init options.
4. Run local/global ligand docking or GALigandDock-style protocols.
5. Score and inspect poses; do not interpret Rosetta scores as binding free energy.

## Useful Command Pattern

Pass extra init options through the local wrapper when params files are needed:

```bash
enzyme-core run pyrosetta-ligand --work-dir ./runs/pyrosetta-ligand --operation pyrosetta_tools -- \
  score \
  --pdb ligand_complex.pdb \
  --options "-extra_res_fa ligand.params" \
  --out out/pyrosetta-ligand/scores.tsv
```

## Decision Rules

- Use `openmm-md` for MD/PBSA workflows; use this skill for Rosetta ligand scoring
  and docking.
- Ligand params quality controls whether the protocol is meaningful.
- Stop if ligand protonation, tautomer, or params are unspecified for serious work.

## PyRosetta Performance Policy

Standard PyRosetta/Rosetta score functions, rotamer packing, and FastRelax are
CPU-bound and do not use GPU acceleration. For saturation mutagenesis, use the
shared `mutation-ddg-scan` command with multiple CPU workers, local repacking,
`--skip-wt`, and `--resume`; reserve FastRelax for shortlisted or conflicting
mutations.
