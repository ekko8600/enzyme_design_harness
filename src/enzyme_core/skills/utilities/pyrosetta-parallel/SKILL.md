---
name: pyrosetta-parallel
description: 'Run or plan parallel PyRosetta workflows with many decoys or mutations.
  Use this skill for GNU parallel, dask, PyRosettaCluster-style batching, and local
  batch execution of scoring, relax, docking, or design jobs.

  '
metadata:
  category: utilities
  tags: pyrosetta, parallel, batch, decoys, local
  legacy_category: design-tools
  taxonomy_version: enzyme-agent/taxonomy/v1
  capability_ids:
  - utility.data_processing
---

# PyRosetta Parallel

Use this skill for batch decoy generation and large scans.

PyRosetta is normally CPU-bound; standard score functions, packing, and relax
do not use GPU acceleration. For mutation scans, parallelize independent
mutants across CPU processes and keep each job resumable.

## Patterns

Local batch scoring:

```bash
for pdb in inputs/*.pdb; do
  enzyme-core run pyrosetta-parallel --work-dir ./runs/pyrosetta-parallel --operation pyrosetta_tools -- \
    score --pdb "$pdb" --out "out/pyrosetta-scores/$(basename "$pdb").tsv"
done
```

Local batch for heavier jobs:

```bash
enzyme-core run pyrosetta-parallel --work-dir ./runs/pyrosetta-parallel --operation pyrosetta_tools -- \
  relax \
  --pdb input.pdb \
  --nstruct 20 \
  --out-dir out/pyrosetta-relax
```

Preferred local saturation scan:

```bash
enzyme-core run pyrosetta-parallel --work-dir ./runs/pyrosetta-parallel --operation pyrosetta_tools -- \
  mutation-ddg-scan \
  --pdb input.pdb \
  --positions 17,61,246 \
  --workers 8 \
  --pack-radius 8 \
  --skip-wt \
  --resume \
  --out out/pyrosetta-design/mutation_ddg.tsv
```

## Decision Rules

- Keep each decoy independent and write separate outputs.
- Merge score TSVs after all workers finish.
- Prefer deterministic seeds when comparing protocol variants.
- Use local repack-only scans for hundreds of mutants; follow with relax on the
  top/conflicting subset.

## PyRosetta Performance Policy

Standard PyRosetta/Rosetta score functions, rotamer packing, and FastRelax are
CPU-bound and do not use GPU acceleration. For saturation mutagenesis, use the
shared `mutation-ddg-scan` command with multiple CPU workers, local repacking,
`--skip-wt`, and `--resume`; reserve FastRelax for shortlisted or conflicting
mutations.
