---
name: pyrosetta-loops
description: 'Plan PyRosetta loop modeling and loop refinement workflows, including
  loop selection, loop files, GenKIC-style modeling, and post-loop relaxation/scoring.

  '
metadata:
  category: structure
  tags: pyrosetta, loops, loop-modeling, genkic
  legacy_category: design-tools
  taxonomy_version: enzyme-agent/taxonomy/v1
  capability_ids:
  - structure.analysis
---

# PyRosetta Loops

Use this skill for loop modeling and refinement.

## Workflow

1. Identify loop start, stop, and cutpoint in Pose numbering.
2. Build or validate the loop definition.
3. Run loop modeling or GenKIC-style protocols in PyRosetta.
4. Relax loop decoys.
5. Rank by Rosetta score, closure quality, and task-specific geometry.

## Supporting Commands

Use cleanup, relax, and scoring wrappers around loop protocol outputs:

```bash
enzyme-core run pyrosetta-loops --work-dir ./runs/pyrosetta-loops --operation pyrosetta_tools -- \
  relax \
  --pdb loop_model.pdb \
  --nstruct 3 \
  --out-dir out/pyrosetta-loops
```

## Decision Rules

- Confirm loop positions are Pose positions.
- Keep loop modeling separate from whole-protein sequence design unless the user
  explicitly asks for combined design.

## PyRosetta Performance Policy

Standard PyRosetta/Rosetta score functions, rotamer packing, and FastRelax are
CPU-bound and do not use GPU acceleration. For saturation mutagenesis, use the
shared `mutation-ddg-scan` command with multiple CPU workers, local repacking,
`--skip-wt`, and `--resume`; reserve FastRelax for shortlisted or conflicting
mutations.
