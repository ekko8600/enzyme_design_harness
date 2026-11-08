---
name: rational-stability-environment
description: 'Diagnose rational enzyme thermostability, pH tolerance, hydration, solvent
  tolerance, and surface-electrostatic hypotheses. Use through rational-design-advanced
  to attach condition-specific physical reasoning and validation metrics to existing
  design candidates.

  '
metadata:
  category: stability
  legacy_category: design-tools
  taxonomy_version: enzyme-agent/taxonomy/v1
  capability_ids:
  - science.general_operation
---

# Rational Stability And Environment

- Use explicitly chosen PyRosetta operations as ranking evidence; do not report predicted ΔΔG as measured stability.
- Make pH decisions from protonation and local networks, not a blanket charge-sign rule.
- Distinguish observed water bridges from hydration thermodynamics; use OpenMM
  trajectories for hypotheses rather than adding WaterMap or 3D-RISM.
- Specify relevant Tm, half-life, pH-activity, aggregation, or solvent-residual-activity assays and their conditions.
