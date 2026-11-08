---
name: rational-chemical-mechanism
description: 'Diagnose enzyme chemical-step hypotheses for low activity, proton or
  hydrogen transfer, transition-state stabilization, and metal/cofactor chemistry.
  Use through rational-design-advanced when reaction identity, protonation, and atom
  mapping are known; route validated high-cost questions to enzyme-qm-ts.

  '
metadata:
  category: qmmm
  legacy_category: design-tools
  taxonomy_version: enzyme-agent/taxonomy/v1
  capability_ids:
  - science.general_operation
---

# Rational Chemical Mechanism

Use this child Skill for chemical-step hypotheses only. Separate chemical
barrier, binding, conformational, and product-release explanations before
selecting mutations.

- Prioritize second-shell electrostatics, catalytic-water geometry, and transition-state contacts.
- Keep catalytic residues and first-shell metal ligands protected by default.
- Use `enzyme-qm-ts` for QM/MM paths; use KIE, pH-rate profiles, `kcat`, and
  `kcat/Km` as distinct experimental diagnostics.
- Treat donor-acceptor geometry and static contacts as hypotheses, not proof of tunnelling or transition-state stabilization.
