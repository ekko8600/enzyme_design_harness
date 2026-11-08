---
name: pyrosetta-relax-refine
description: 'Refine protein structures with PyRosetta FastRelax, minimization, and
  related Rosetta refinement workflows. Use this skill for structure relaxation, local
  minimization, decoy generation, and pre-scoring cleanup.

  '
metadata:
  category: structure
  tags: pyrosetta, relax, refinement, minimization
  legacy_category: design-tools
  taxonomy_version: enzyme-agent/taxonomy/v1
  capability_ids:
  - structure.analysis
---

# PyRosetta Relax And Refine

Use this skill for FastRelax and refinement.

## Commands

Run FastRelax locally:

```bash
enzyme-core run pyrosetta-relax-refine --work-dir ./runs/pyrosetta-relax-refine --operation pyrosetta_tools -- \
  relax \
  --pdb input.pdb \
  --nstruct 5 \
  --scorefxn ref2015 \
  --out-dir out/pyrosetta-relax
```

## Decision Rules

- Run `pyrosetta-pose` cleanup first for problematic PDBs.
- Use small `nstruct` for smoke tests, larger batches for production decoys.
- Score relaxed decoys with `pyrosetta-scoring` before selecting models.
- For mutation-level structural interpretation, preserve matched WT/variant
  structures and their seed/relax manifest with the `mutation-relax-panel`
  companion command exposed by `pyrosetta-scoring`.

## PyRosetta Performance Policy

Standard PyRosetta/Rosetta score functions, rotamer packing, and FastRelax are
CPU-bound and do not use GPU acceleration. For saturation mutagenesis, use the
shared `mutation-ddg-scan` command with multiple CPU workers, local repacking,
`--skip-wt`, and `--resume`; reserve FastRelax for shortlisted or conflicting
mutations.

## Repack versus FastRelax comparison

`compare_repack_relax` uses mode=run to produce nstruct WT models per method. Repack fixes the backbone and sequence; FastRelax explicitly enables backbone/chi, disables jumps, and uses declared max_iter and relax_repeats. All models and a hash manifest are saved. Raw in-memory energy and exported/reloaded PDB energy are distinct fields. Use mode=verify with the same input PDB, nstruct and produced panel_dir plus output JSON to check every model, identity, repack backbone and energy against the exact exported coordinates. Read full verification JSON. Score units are REU; do not infer experimental stability or activity.
