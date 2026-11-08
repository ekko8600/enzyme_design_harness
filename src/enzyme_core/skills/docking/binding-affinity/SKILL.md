---
name: binding-affinity
description: 'Estimate protein-protein or protein-peptide complex binding energies
  with OpenMM. Use this skill when Codex needs to score a complex PDB, compare wild-type
  and mutant complexes, rank designed binders by OpenMM interaction-energy proxies,
  or batch-score receptor/partner chain pairs in enzyme design runs. The implementation
  is OpenMM-only and does not use empirical ML, RDKit, Rosetta, docking, or consensus
  fallback scoring.

  '
metadata:
  category: docking
  legacy_category: design-tools
  taxonomy_version: enzyme-agent/taxonomy/v1
  capability_ids:
  - structure.interaction_assessment
---

# Binding Affinity

## Overview

Use this skill to estimate relative binding strength for protein complexes from
3D structures. The primary output is:

`delta_e_binding_kcal_per_mol = E_complex - E_receptor - E_partner`

All energies are computed with OpenMM using an Amber protein force field and
implicit solvent. Treat the result as a relative ranking proxy, not an
experimental binding free energy.

## Requirements

Install OpenMM in the active environment:

```bash
conda install -c conda-forge openmm numpy
```

Optional but useful for preparing structures before scoring:

```bash
conda install -c conda-forge pdbfixer pdb-tools
```

## Single Complex

Score a complex PDB by specifying receptor and partner chains:

```bash
python rational-design-methods/binding-affinity/scripts/score_complex.py \
  --complex-pdb complex.pdb \
  --receptor-chains A \
  --partner-chains B \
  --output out/binding_affinity/complex_binding.json \
  --csv-output out/binding_affinity/complex_binding.csv \
  --minimize-steps 500
```

Use `--platform CUDA`, `--platform OpenCL`, or `--platform CPU` to control the
OpenMM backend. The default `auto` tries GPU platforms first and falls back to
CPU/Reference.

## Separate Receptor And Partner Files

When receptor and partner are available as separate PDB files in complex
geometry, combine them on the fly:

```bash
python rational-design-methods/binding-affinity/scripts/score_complex.py \
  --receptor-pdb receptor.pdb \
  --partner-pdb designed_partner.pdb \
  --output out/binding_affinity/designed_partner.json
```

The script rewrites temporary chain IDs for separate inputs and evaluates the
combined complex. Use this only when the two files are already aligned in the
same coordinate frame.

## Batch Scoring

Batch mode accepts CSV rows with either complex-chain inputs or separate PDBs.
Recommended columns:

```csv
name,complex_pdb,receptor_chains,partner_chains,receptor_pdb,partner_pdb
design_001,complex_001.pdb,A,B,,
design_002,,,,receptor.pdb,partner_002.pdb
```

Run:

```bash
python rational-design-methods/binding-affinity/scripts/score_complex.py \
  --batch-csv designs.csv \
  --output out/binding_affinity/batch_binding.json \
  --csv-output out/binding_affinity/batch_binding.csv \
  --minimize-steps 200
```

The CSV output is intended for downstream ranking. More negative
`delta_e_binding_kcal_per_mol` means stronger predicted binding under this
approximation.

## Output Integrity

- Report script outputs exactly as generated.
- Do not relabel `delta_e_binding_kcal_per_mol` as experimental ΔG.
- Do not compare absolute values across unrelated proteins or very different
  preparation protocols.
- Use identical preparation, force field, solvent model, minimization settings,
  and chain definitions when comparing WT vs mutant designs.
- Failed rows are kept in batch JSON/CSV with `status=failed` and an error
  message; do not silently drop them.

## Decision Rules

- Prefer `--minimize-steps 0` first as a geometry sanity check, then rerun with
  200-1000 minimization steps for final ranking.
- Use `--no-add-hydrogens` only for already protonated/prepared PDBs. Otherwise
  let OpenMM add hydrogens at pH 7.
- Strip ligands, waters, and unsupported cofactors before scoring protein-only
  interfaces unless they are intentionally part of the modeled interface and
  parameterized by the selected OpenMM force field.
- If OpenMM setup fails because of missing atoms, nonstandard residues, or
  cofactors, fix the PDB first instead of fabricating a score.

## Reference

For method caveats and interpretation details, read
`references/openmm_binding_energy.md`.
