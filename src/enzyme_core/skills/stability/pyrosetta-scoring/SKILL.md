---
name: pyrosetta-scoring
description: 'Score protein structures with PyRosetta score functions and compute
  energy or interface metrics. Use this skill for ref2015 scoring, residue/complex
  assessment, score tables, and PyRosetta interface dG/dSASA/shape complementarity.

  '
metadata:
  category: stability
  tags: pyrosetta, scoring, ref2015, interface, protein-qc
  legacy_category: evaluation
  taxonomy_version: enzyme-agent/taxonomy/v1
  capability_ids:
  - science.general_operation
---

# PyRosetta Scoring

Use this skill for Rosetta score functions and interface metrics.

PyRosetta/Rosetta scoring and packing are CPU-bound in normal PyRosetta builds;
do not expect GPU acceleration for saturation scanning. For large scans, speed
comes from process-level parallelism, local repacking, resumable batches, and
limiting full relax to the best or most ambiguous candidates.

## Commands

Score a structure:

```bash
enzyme-core run pyrosetta-scoring --work-dir ./runs/pyrosetta-scoring --operation pyrosetta_tools -- \
  score \
  --pdb input.pdb \
  --scorefxn ref2015 \
  --out out/pyrosetta-scoring/scores.tsv
```

Compute interface metrics:

```bash
enzyme-core run pyrosetta-scoring --work-dir ./runs/pyrosetta-scoring --operation pyrosetta_tools -- \
  interface \
  --pdb complex.pdb \
  --partners A_B \
  --out out/pyrosetta-scoring/interface.tsv
```

Fast mutation ddG scan with local repacking:

```bash
enzyme-core run pyrosetta-scoring --work-dir ./runs/pyrosetta-scoring --operation pyrosetta_tools -- \
  mutation-ddg-scan \
  --pdb input.pdb \
  --positions 17,61,246 \
  --workers 8 \
  --pack-radius 8 \
  --skip-wt \
  --resume \
  --out out/pyrosetta-design/mutation_ddg.tsv
```

When the PDB uses a different residue numbering scheme from the target
sequence, pass a Host-provided JSON map such as `{"1": 24, "2": 25}`
with `--numbering-map`. The output then includes `full_sequence_position`
and `full_mutation`; do not infer this mapping from the output pose index.

For a full single-site saturation scan, start with local repack only. Add
`--relax --relax-rounds 25` only for a smaller follow-up set because FastRelax
dominates runtime.

For matched structural interpretation of a small known-variant panel, use the
shared structure-producing companion command:

```bash
enzyme-core run pyrosetta-scoring --work-dir ./runs/pyrosetta-scoring --operation pyrosetta_tools -- \
  mutation-relax-panel \
  --pdb input.pdb \
  --mutation-csv mutation_panel.csv \
  --scorefxn ref2015 \
  --pack-radius 8 \
  --relax-rounds 5 \
  --options "-constant_seed -jran 111" \
  --out-dir out/pyrosetta-relaxed-panel
```

This emits position-matched WT controls, variant PDBs, a `relaxed_panel.tsv`
and a manifest. The output is a comparative structural proxy, not a measured
stability or activity result.

To inspect which weighted score terms drive the relaxed comparison:

```bash
enzyme-core run pyrosetta-scoring --work-dir ./runs/pyrosetta-scoring --operation pyrosetta_tools -- \
  score-terms-panel \
  --panel-dir out/pyrosetta-relaxed-panel
```

Term decomposition remains score-function interpretation; it is not a
thermodynamic free-energy decomposition.

For detailed local structural interpretation, pass the matched panel through
the Host postprocessor after the run completes.  The postprocessor emits the
existing geometry/context figures plus local coordinate displacement, contact
network turnover, heavy-atom donor/acceptor geometry, and catalytic/Ca spatial
relation figures.  The structured JSON keeps per-seed and per-residue details.
These outputs remain computational proxies; a nonconverged panel is reported
as such and cannot be treated as mechanism validation.

```bash
python -m research_plane.cli physchem-relaxed-geometry \
  --result-root result \
  --project-id <project-id> \
  --source-run-id <run-id> \
  --panel-dir seed_111=<panel-dir-1> \
  --panel-dir seed_222=<panel-dir-2> \
  --panel-dir seed_333=<panel-dir-3> \
  --site-spec-json '<target-site-specification>'
```

## Decision Rules

- Use `ref2015` unless the workflow requires a specific score function.
- Interface metrics require a correct partners string such as `A_B`.
- Treat Rosetta scores as comparative model scores, not physical free energies.
- Use `mutation-ddg-scan --workers N` rather than one long serial scan.
- Keep `--pack-radius` near 6-10 Angstrom for screening; broader repacking or
  FastRelax should be reserved for shortlisted mutations.

## PyRosetta Performance Policy

Standard PyRosetta/Rosetta score functions, rotamer packing, and FastRelax are
CPU-bound and do not use GPU acceleration. For saturation mutagenesis, use the
shared `mutation-ddg-scan` command with multiple CPU workers, local repacking,
`--skip-wt`, and `--resume`; reserve FastRelax for shortlisted or conflicting
mutations.
