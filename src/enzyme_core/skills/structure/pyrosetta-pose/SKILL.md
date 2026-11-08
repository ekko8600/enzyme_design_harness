---
name: pyrosetta-pose
description: 'Work with PyRosetta Pose objects, PDB loading, chain/residue inspection,
  coordinate access, geometry, aligned backbone RMSD, CA RMSD, and basic structure cleanup. Use this skill when preparing
  structures before scoring, relax, design, docking, or analysis.

  '
metadata:
  category: structure
  tags: pyrosetta, pose, pdb, structure-cleanup
  legacy_category: design-tools
  taxonomy_version: enzyme-agent/taxonomy/v1
  capability_ids:
  - structure.analysis
---

# PyRosetta Pose

Use this skill for Pose-level structure handling.

## Commands

Clean/load and dump a PDB through PyRosetta:

```bash
enzyme-core run pyrosetta-pose --work-dir ./runs/pyrosetta-pose --operation pyrosetta_tools -- \
  clean-pdb \
  --pdb input.pdb \
  --out out/pyrosetta-pose/cleaned.pdb \
  --manifest out/pyrosetta-pose/manifest.json
```

## Common Tasks

- Read PDB into a Pose and confirm residue count/chain mapping.
- Convert PDB numbering to Pose numbering before mutation/design.
- Inspect coordinates, torsions, residue identity, and chain boundaries.
- Clean structures before scoring or FastRelax.

## Decision Rules

- Always clarify whether positions are PDB numbering or Pose numbering.
- Use Pose numbering for `pyrosetta_tools.py mutate-scan`.
- Use `pdb` skill first if a structure must be downloaded from RCSB.

## PyRosetta Performance Policy

Standard PyRosetta/Rosetta score functions, rotamer packing, and FastRelax are
CPU-bound and do not use GPU acceleration. For saturation mutagenesis, use the
shared `mutation-ddg-scan` command with multiple CPU workers, local repacking,
`--skip-wt`, and `--resume`; reserve FastRelax for shortlisted or conflicting
mutations.

## Saved model backbone displacement

`compare_backbones` binds reference_pdb and a producer panel_dir containing manifest.json/models. It verifies original/model hashes and residue identity, then reports rigid-body aligned backbone RMSD, aligned CA RMSD, unaligned maximum backbone displacement and heavy atom pair count below 1 A. These are different observables: do not treat rigid-body movement as deformation. Output numeric JSON; it does not generate or optimize models.

`verify_preservation` v2 compares all supplied atoms including existing H/D, components and residue numbering. Bind `--mapping` to the complete chain-specific mapping and `--required-bonds` to explicit atom-pair declarations. It reports added/missing/changed atoms and bond geometry; geometric SG proximity does not establish chemical state. Monomer and assembly require separate comparisons.
