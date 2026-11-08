---
name: rational-transport-dynamics
description: 'Diagnose rational enzyme tunnel, gate, flexibility, conformational-preorganization,
  and allosteric-network hypotheses. Use through rational-design-advanced when substrate
  access, product release, functional motion, or long-range coupling may explain candidate
  behavior; use openmm-md and explicitly supplied trajectory analyses.

  '
metadata:
  category: md
  legacy_category: design-tools
  taxonomy_version: enzyme-agent/taxonomy/v1
  capability_ids:
  - science.general_operation
---

# Rational Transport And Dynamics

- Distinguish a geometric channel from transport kinetics; use isolated CAVER
  through `rational-design-advanced/scripts/external_tools.py`, not MOLE or CaverDock.
- Use matched, replicated MD for dynamic claims and protect functional gate loops from indiscriminate rigidification.
- Treat RMSF, contact occupancy, and near-attack conformations as supporting observables, not chemical barriers.
- Require convergence evidence before interpreting MSM, metadynamics, or allosteric-network conclusions.
