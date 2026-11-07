---
name: pyrosetta-specialized
description: 'Guide specialized PyRosetta workflows from the notebooks: symmetry,
  electron density, antibodies, carbohydrates, RNA, and membrane proteins. Use this
  skill when a task depends on one of these specialized Rosetta domains.

  '
metadata:
  category: design
  tags: pyrosetta, symmetry, density, antibody, carbohydrate, rna, membrane
  legacy_category: design-tools
  taxonomy_version: enzyme-agent/taxonomy/v1
  capability_ids:
  - design.molecular_design
---

# PyRosetta Specialized Workflows

Use this skill for specialized Rosetta domains.

## Domains

- Symmetry: symmetric pose setup, symmetric score functions, symmetric relax.
- Density: density maps, density-aware scoring/refinement.
- Antibody: antibody-specific numbering, CDR modeling/refinement.
- Carbohydrate: carbohydrate residue setup and glycan modeling.
- RNA: RNA pose setup, scoring, and RNA-specific protocols.
- Membrane: membrane pose setup, membrane score functions, membrane movers.

## Decision Rules

- Load the relevant Rosetta/PyRosetta domain options before importing structures.
- Do not reuse generic protein settings blindly for RNA, membrane, carbohydrate,
  antibody, density, or symmetry systems.
- Use this skill as guidance; create a task-specific script when a specialized
  workflow must be repeated.

## PyRosetta Performance Policy

Standard PyRosetta/Rosetta score functions, rotamer packing, and FastRelax are
CPU-bound and do not use GPU acceleration. For saturation mutagenesis, use the
shared `mutation-ddg-scan` command with multiple CPU workers, local repacking,
`--skip-wt`, and `--resume`; reserve FastRelax for shortlisted or conflicting
mutations.
