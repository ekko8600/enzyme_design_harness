---
name: rational-design-advanced
description: 'Mechanism-driven rational-design planning, candidate explanation, and
  multi-method arbitration after enzyme-design candidates exist. Use to decide whether
  physical-chemistry review is warranted; present methods, prerequisites, availability,
  estimated resources, risks, and experimental readouts; or turn PLM, MPNN, Rosetta,
  literature, or other candidate mutations into auditable rational hypotheses. Never
  start costly downstream calculations without user confirmation.

  '
metadata:
  category: design
  tags: enzyme-design, rational-design, mechanism, candidate-review, routing
  local_script: scripts/rational_advanced.py
  legacy_category: orchestration
  taxonomy_version: enzyme-agent/taxonomy/v1
  capability_ids:
  - design.molecular_design
---



# Advanced Rational Design

Use this Skill for explicit annotation, evidence-bundle construction and method
availability checks. Each operation produces an artifact for the caller; it does
not control the research loop or accept/reject candidates automatically.

## Inputs and dependencies

- The caller supplies target identity, numbering, protected sites, evidence and structure state.
- Atomic PyRosetta operations can provide declared-model ranking observations.
- `enzyme-qm-ts` and `openmm-md` are independently selected operations when needed.
- Isolated PLIP and CAVER use the `external_tools` operation; read
  [tool selection](references/tool-selection.md) before choosing an external route.
- Candidate integration and final decisions belong to the newly built driver and its decision owner.

## Two Paths Overview

This Skill has two independent paths. They never block each other:

- **Path A — Candidate annotation & review (lightweight, do on every review).**
  Turn candidate mutations into auditable mechanism hypotheses, validate them,
  emit the evidence bundle, excluded hypotheses, a hypothesis registry, and a
  user-facing HTML report. No plan and no heavy calculation are required.
- **Path B — Method arbitration & heavy routing (optional, confirmation-gated).**
  Select method cards from the catalog, present prerequisites/estimates/boundaries,
  and route only user-confirmed heavy calculations (QM/MM, MD, PLIP, CAVER,
  PyRosetta). Never start these without explicit confirmation.

## Path A: Candidate Annotation And Review

### Rationalize

Use `rationalize` reasoning to create `rational_annotations.json` with status
`support`, `conditional`, `conflict`, or `insufficient_evidence`. Each decision
card must state phenotype, falsifiable mechanism hypothesis, strategy, regions,
mutation patterns, computational evidence, experiment, counterevidence/risk,
and confidence. For annotations/v2 additionally:

- Tag every evidence item with a `level` from
  [evidence-levels.json](references/evidence-levels.json) (`experimental`,
  `structural_biochemistry`, `dynamics`, `electronic_structure`, `evolutionary`,
  `zero_shot`, `expert_opinion`). The level's `kinds` must match the item `kind`.
- Write the optional card fields `falsifiable_prediction` (what an experiment
  must observe if the hypothesis holds) and `distinguishing_experiment` (the
  experiment that separates this hypothesis from its alternatives).
- Status is constrained by evidence level: `support` needs at least one direct
  level (experimental/structural_biochemistry) or two distinct computational
  levels; `conditional` needs at least one leveled item; `conflict` needs
  non-empty counterevidence. Only-hypothesis annotations cap at
  `insufficient_evidence`.

### Validate

```bash
enzyme-core run rational-design-advanced --work-dir ./runs/rational-design-advanced -- validate-annotations \
  --annotations rational/rational_annotations.json --candidates merged_candidates.json
```

`validate-annotations` accepts schema v1 (legacy rules, warnings only) and v2
(level and status-level enforcement). A mutation absent from the candidate input
is always an error.

### Build evidence bundle, excluded hypotheses, and registry

```bash
enzyme-core run rational-design-advanced --work-dir ./runs/rational-design-advanced -- build-evidence-bundle \
  --candidates merged_candidates.json --annotations rational/rational_annotations.json \
  --output rational/rational_evidence_bundle.json \
  --excluded-output rational/excluded_hypotheses.json \
  --registry-output rational/mechanism_hypothesis_registry.json
```

- `rational_evidence_bundle.json`: candidates enriched with their
  `rational_advanced` annotation. The caller interprets it and records candidate decisions.
- `excluded_hypotheses.json`: every `conflict`/`insufficient_evidence`
  annotation with its counterevidence and prediction, for the QC
  already-excluded list.
- `mechanism_hypothesis_registry.json`: one entry per hypothesis with status,
  decision layer, evidence levels, prediction, and `experiment_outcome: pending`.
  This is the learning-loop artifact (see below).

### Review HTML

Generate a self-contained HTML report so the user can inspect every candidate,
decision card, evidence label (kind + level), method card, registry, and
provenance path without opening JSON files. The report is styled for the
project's html-report-renderer and can be polished in place with that skill; it
must never hide weak evidence.

```bash
enzyme-core run rational-design-advanced --work-dir ./runs/rational-design-advanced -- build-report \
  --annotations rational/rational_annotations.json \
  --bundle rational/rational_evidence_bundle.json \
  --context design_context.json \
  --registry rational/mechanism_hypothesis_registry.json \
  --output rational/rational_design_advanced_report.html
```

`--plan` is optional: annotation-only reports render overview, candidates, and
registry without an arbitration plan. All dynamic text is HTML-escaped; status
chips follow the report style (support=green, conditional/insufficient_evidence=
amber, conflict=red), and a visible callout flags demo/dry-run artifacts and
low-confidence hypotheses. Optional polish with the shared renderer:

```bash
enzyme-core run html-report-renderer --work-dir ./runs/html-report-renderer --operation render_html -- \
  --input rational/rational_design_advanced_report.html \
  --output rational/rational_design_advanced_report.html \
  --title "Rational Design Advanced — Candidate Mechanism Evidence Review"
```

Keep the polished `.html` as the user-facing review artifact alongside the
JSON/CSV machine-readable companions.

## Path B: Method Arbitration And Heavy Routing

1. Read `references/method_catalog.json` and [tool selection](references/tool-selection.md); present the relevant method cards before asking for resource confirmation.
2. Require a validated `design_context.json` for mechanism claims. With only candidate mutations, permit constrained annotation and label every missing structural or chemical input.
3. Create a plan with:

```bash
enzyme-core run rational-design-advanced --work-dir ./runs/rational-design-advanced -- init-plan \
  --context design_context.json --candidates merged_candidates.json \
  --output rational/rational_design_plan.json
```

The plan is advisory by construction: `requires_user_confirmation` is `true`
and `resource_confirmation.confirmed` is `false` until the user confirms a
scope.

4. Treat every card as a hypothesis. Protect catalytic residues, metal ligands, essential cofactors, disulfides, and numbering-ambiguous sites unless the user explicitly authorizes a changed objective.
5. After the user confirms a stated resource envelope, route only the selected child Skill and its existing project dependencies. Do not claim that docking, MD, PLIP, Rosetta, or QM/MM validates an experiment.
6. Verify isolated tools before use. Do not install a duplicate method family.

```bash
enzyme-core run rational-design-advanced --work-dir ./runs/rational-design-advanced --operation external_tools -- doctor
enzyme-core run rational-design-advanced --work-dir ./runs/rational-design-advanced --operation external_tools -- plip \
  --input complex.pdb --output rational/plip
enzyme-core run rational-design-advanced --work-dir ./runs/rational-design-advanced --operation external_tools -- caver \
  --pdb-dir caver_input --output rational/caver
```

## Evidence Levels

The authoritative enumeration lives in
[evidence-levels.json](references/evidence-levels.json) and is read by
`validate-annotations`. It aligns with `rules/05-evidence-provenance.md`:
direct observation (`experimental`, `structural_biochemistry`) maps to kind
`source_evidence`; computational inference (`dynamics`, `electronic_structure`,
`evolutionary`, `zero_shot`) maps to kind `computational_inference`; and
`expert_opinion` maps to `source_evidence` or `hypothesis` depending on whether
it is a direct report or an interpretation. Do not claim a level the artifacts
do not support, and do not hide weak evidence behind a higher level.

## Learning Loop

The hypothesis registry is the memory of this Skill across design iterations:

1. `build-evidence-bundle --registry-output` writes every hypothesis with
   `experiment_outcome: pending`.
2. After assays, record results back into the registry:

```bash
enzyme-core run rational-design-advanced --work-dir ./runs/rational-design-advanced -- update-hypothesis-outcome \
  --registry rational/mechanism_hypothesis_registry.json \
  --outcomes outcomes.json --updated-at 2026-08-05
```

`outcomes.json` maps mutation to `confirmed`, `rejected`, or `ambiguous`
optionally as `{"mutation": {"outcome": "...", "evidence": "..."}}`. Only
mutations present in the registry are accepted.

3. The caller can read the registry and excluded hypotheses when choosing a subsequent action. The operation itself does not start another iteration.
