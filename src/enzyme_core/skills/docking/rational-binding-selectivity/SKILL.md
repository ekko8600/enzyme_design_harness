---
name: rational-binding-selectivity
description: 'Diagnose rational enzyme pocket-binding, substrate-recognition, regioselectivity,
  and stereoselectivity hypotheses after candidates exist. Use through rational-design-advanced
  to explain or challenge mutations with ligand poses, contact geometry, and appropriate
  binding or product assays.

  '
metadata:
  category: docking
  legacy_category: design-tools
  taxonomy_version: enzyme-agent/taxonomy/v1
  capability_ids:
  - structure.interaction_assessment
---

# Rational Binding And Selectivity

- Compare multiple chemically credible poses; do not treat one docking pose as a binding or selectivity measurement.
- Use `pymol`, `pyrosetta-ligand`, and the isolated PLIP wrapper from
  `rational-design-advanced/scripts/external_tools.py` only as structural evidence.
- Propose pocket-wall, gate, and second-shell changes before catalytic-residue changes.
- Separate `Km` from `Kd`; use kinetic assays, ITC/SPR/MST where applicable, and
  chiral HPLC/GC or regioisomer product quantification for selectivity.
