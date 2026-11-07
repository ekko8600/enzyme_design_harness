---
name: pyrosetta-pack-design
description: 'Use PyRosetta packing and design workflows, including repacking, point
  mutation scans, resfile-guided design, and score-based mutation ranking. Use this
  skill for Rosetta sequence design around fixed or flexible backbones.

  '
metadata:
  category: design
  tags: pyrosetta, packing, design, mutation-scan, resfile
  legacy_category: design-tools
  taxonomy_version: enzyme-agent/taxonomy/v1
  capability_ids:
  - design.molecular_design
---

# PyRosetta Pack And Design

Use this skill for side-chain packing and mutation/design scans.

PyRosetta packing is CPU-bound. GPU hardware does not accelerate standard
PyRosetta score functions, rotamer packing, or FastRelax. Use parallel
processes and local repacking for saturation scans, then reserve relax for a
shortlist.

## Commands

Smoke test the local PyRosetta runtime:

```bash
enzyme-core run pyrosetta-pack-design --work-dir ./runs/pyrosetta-pack-design --operation pyrosetta_tools -- smoke-test --relax
```

Point mutation scan by Pose position:

```bash
enzyme-core run pyrosetta-pack-design --work-dir ./runs/pyrosetta-pack-design --operation pyrosetta_tools -- \
  mutate-scan \
  --pdb input.pdb \
  --positions 35,42,88 \
  --out out/pyrosetta-design/mutations.tsv
```

Parallel saturation/ddG scan by Pose position:

```bash
enzyme-core run pyrosetta-pack-design --work-dir ./runs/pyrosetta-pack-design --operation pyrosetta_tools -- \
  mutation-ddg-scan \
  --pdb input.pdb \
  --positions 35,42,88 \
  --workers 8 \
  --pack-radius 8 \
  --skip-wt \
  --resume \
  --out out/pyrosetta-design/mutation_ddg.tsv
```

Repack side chains:

```bash
enzyme-core run pyrosetta-pack-design --work-dir ./runs/pyrosetta-pack-design --operation pyrosetta_tools -- \
  pack-design \
  --pdb input.pdb \
  --repack-only \
  --out out/pyrosetta-design/repacked.pdb
```

## Decision Rules

- Mutation positions are Pose positions, not PDB residue numbers.
- Use repacking for interface cleanup before scoring.
- For full resfile design, create a task/resfile workflow explicitly rather than
  mutating unconstrained positions.
- For saturation scans, prefer `mutation-ddg-scan` over serial `mutate-scan`.

## PyRosetta Performance Policy

Standard PyRosetta/Rosetta score functions, rotamer packing, and FastRelax are
CPU-bound and do not use GPU acceleration. For saturation mutagenesis, use the
shared `mutation-ddg-scan` command with multiple CPU workers, local repacking,
`--skip-wt`, and `--resume`; reserve FastRelax for shortlisted or conflicting
mutations.

## Independent mutation output checks

`verify_mutation_results` checks paired-panel or scan TSV outputs against the original PDB and a frozen JSON list of pose-numbered mutation strings. Paired-panel mode checks all WT/variant identities and all original backbone coordinates (0.001 A), seeds and nstruct coverage. Both modes check complete coverage and paired-score arithmetic. Read complete numeric output JSON. It does not recompute Rosetta energies or establish biological accuracy.

## Retained saturation scan

`saturation_scan` uses a frozen mutations_file JSON list and input_pdb. It locally repacks per-site WT controls and mutants, saves every generated structure, scan.tsv and manifest.json. Controls are reused per site and identified explicitly. Primary scores are evaluated on exported/reloaded PDB coordinates; raw in-memory scores are separate fields. Use `verify_mutation_results`, mode=scan and require_structures=true, against the input PDB and the same expected mutation list. Report complete coverage and all failures; REU differences are not experimental ddG or activity.
