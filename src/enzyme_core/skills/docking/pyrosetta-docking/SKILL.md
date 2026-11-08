---
name: pyrosetta-docking
description: 'Plan and run PyRosetta protein-protein docking and interface analysis
  workflows. Use this skill for docking setup, partners strings, interface metrics,
  decoy generation, and post-docking ranking.

  '
metadata:
  category: docking
  tags: pyrosetta, docking, interface, protein-protein
  legacy_category: design-tools
  taxonomy_version: enzyme-agent/taxonomy/v1
  capability_ids:
  - structure.interaction_assessment
---

# PyRosetta Docking

Use this skill for protein-protein docking and interface analysis.

## Commands

Interface analysis:

```bash
enzyme-core run pyrosetta-docking --work-dir ./runs/pyrosetta-docking --operation pyrosetta_tools -- \
  interface \
  --pdb complex.pdb \
  --partners A_B \
  --out out/pyrosetta-docking/interface.tsv
```

## Workflow

1. Confirm chain grouping and partners string.
2. Clean and optionally relax the input complex.
3. Generate docking decoys with PyRosetta/Rosetta docking protocols when needed.
4. Rank by interface dG, dSASA, shape complementarity, and task-specific filters.

## Decision Rules

- Stop if chain mapping is ambiguous.
- Use `pyrosetta-scoring` for post-docking score tables.
- Use `protein-qc` for broader binder QC thresholds.

## PyRosetta Performance Policy

Standard PyRosetta/Rosetta score functions, rotamer packing, and FastRelax are
CPU-bound and do not use GPU acceleration. For saturation mutagenesis, use the
shared `mutation-ddg-scan` command with multiple CPU workers, local repacking,
`--skip-wt`, and `--resume`; reserve FastRelax for shortlisted or conflicting
mutations.
