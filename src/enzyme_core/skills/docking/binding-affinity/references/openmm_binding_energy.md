# OpenMM Binding Energy Reference

This skill implements an OpenMM-only approximation inspired by MM/GBSA-style
rescoring workflows, but it is intentionally narrower than full MM/GBSA or FEP.

## Computed Quantity

For each complex:

```text
delta_e_binding = E_complex - E_receptor - E_partner
```

where all three potential energies are computed with the same OpenMM force
field and implicit-solvent configuration. If minimization is enabled, the
complex is minimized first, then receptor and partner energies are evaluated
from the minimized complex coordinates.

## Interpretation

- More negative values usually indicate more favorable modeled interaction.
- Values are best used for ranking variants of the same protein complex.
- The value is not an experimental binding free energy because it omits
  conformational entropy, rigorous solvent sampling, protonation uncertainty,
  and ensemble averaging.
- The score can be sensitive to unresolved clashes, missing atoms, chain
  selection, termini treatment, and force-field support.

## Recommended Comparisons

Use this score for:

- WT vs mutant complex comparison.
- Ranking designed partner variants against the same receptor.
- Filtering obvious steric/interface failures before expensive MD or Rosetta.
- Adding a physics-based feature to an agent ranking table.

Avoid using it for:

- Absolute Kd or pKd prediction.
- Comparing unrelated targets.
- Complexes containing unsupported cofactors or ligands unless separately
  parameterized.
- Claims of experimental affinity improvement without wet-lab validation.
