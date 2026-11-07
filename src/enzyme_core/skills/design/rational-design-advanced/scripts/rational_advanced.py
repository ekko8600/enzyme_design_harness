#!/usr/bin/env python3
"""Create and validate auditable rational-design planning artifacts."""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
from pathlib import Path
from typing import Any


ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "skills" / "registry.py").is_file()).parent
CATALOG = Path(__file__).resolve().parents[1] / "references" / "method_catalog.json"
EVIDENCE_LEVELS_FILE = Path(__file__).resolve().parents[1] / "references" / "evidence-levels.json"
MUTATION_RE = re.compile(r"^[A-Z][1-9][0-9]*[A-Z](?::[A-Z][1-9][0-9]*[A-Z])*$")
STATUSES = {"support", "conditional", "conflict", "insufficient_evidence"}
ANNOTATION_SCHEMAS = {"rational-design-advanced-annotations/v1", "rational-design-advanced-annotations/v2"}
CARD_FIELDS = {
    "phenotype", "mechanism_hypothesis", "decision_layer", "strategy",
    "priority_regions", "mutation_patterns", "computational_evidence",
    "primary_experimental_metric", "counterevidence_or_risk", "confidence",
}
# Optional v2 decision-card fields: falsifiable prediction and the experiment
# that would distinguish this hypothesis from its alternatives.
OPTIONAL_CARD_FIELDS = ("falsifiable_prediction", "distinguishing_experiment")

# Evidence-level catalogue. The authoritative copy lives in
# references/evidence-levels.json (single source of truth); this fallback keeps
# the script usable when the file is missing or the skill is copied standalone.
_DEFAULT_LEVELS = {
    "schema_version": "rational-design-advanced-evidence-levels/v1",
    "levels": {
        "experimental": {
            "label": "Experimental observation",
            "kinds": ["source_evidence"],
            "weight": 5,
            "note": "Direct assay, kinetics, or structure determination",
        },
        "structural_biochemistry": {
            "label": "Structural biochemistry",
            "kinds": ["source_evidence"],
            "weight": 4,
            "note": "Catalytic geometry, PLIP contacts, pocket analysis from a validated structure",
        },
        "dynamics": {
            "label": "Molecular dynamics",
            "kinds": ["computational_inference"],
            "weight": 3,
            "note": "OpenMM MD ensembles, RMSF, contact occupancy",
        },
        "electronic_structure": {
            "label": "Electronic structure (QM/MM)",
            "kinds": ["computational_inference"],
            "weight": 3,
            "note": "CP2K QM/MM transition states and barriers",
        },
        "evolutionary": {
            "label": "Evolutionary / MSA",
            "kinds": ["computational_inference"],
            "weight": 2,
            "note": "Conservation, GEMME, homolog support",
        },
        "zero_shot": {
            "label": "Zero-shot model (PLM)",
            "kinds": ["computational_inference"],
            "weight": 1,
            "note": "ESM2/SaProt masked-marginal scores",
        },
        "expert_opinion": {
            "label": "Expert / literature interpretation",
            "kinds": ["source_evidence", "hypothesis"],
            "weight": 1,
            "note": "Reported mutations or domain judgement; kind depends on direct report vs interpretation",
        },
    },
    "status_rule": {
        "support": "at least one experimental/structural_biochemistry item, or at least two distinct computational levels",
        "conditional": "at least one evidence item with a level",
        "insufficient_evidence": "no leveled evidence required (absence of support)",
        "conflict": "requires non-empty counterevidence_or_risk on at least one decision card",
    },
}
_COMPUTATIONAL_LEVELS = {"dynamics", "electronic_structure", "evolutionary", "zero_shot"}
_DIRECT_LEVELS = {"experimental", "structural_biochemistry"}


def load_evidence_levels() -> dict[str, Any]:
    try:
        data = load_json(EVIDENCE_LEVELS_FILE)
        if isinstance(data, dict) and isinstance(data.get("levels"), dict) and data["levels"]:
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return _DEFAULT_LEVELS


def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def candidates_from(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict) and isinstance(data.get("candidates"), list):
        return [item for item in data["candidates"] if isinstance(item, dict)]
    raise ValueError("candidate input must be a JSON list or an object with candidates")


def objective_text(context: dict[str, Any]) -> str:
    target = context.get("target") if isinstance(context.get("target"), dict) else {}
    constraints = context.get("design_constraints") if isinstance(context.get("design_constraints"), dict) else {}
    return " ".join(str(item).lower() for item in (target.get("goal"), constraints.get("goal"), constraints.get("objective"), constraints.get("primary_metric")) if item)


def selected_methods(catalog: dict[str, Any], context: dict[str, Any]) -> list[str]:
    objective = objective_text(context)
    result: list[str] = []
    for method in catalog.get("methods", []):
        triggers = method.get("objectives", [])
        if not triggers or any(str(trigger).lower() in objective for trigger in triggers):
            result.append(str(method["id"]))
    return result or [str(item["id"]) for item in catalog.get("methods", [])]


def command_init_plan(args: argparse.Namespace) -> int:
    context = load_json(args.context)
    if not isinstance(context, dict):
        raise SystemExit("context must be a JSON object")
    catalog = load_json(CATALOG)
    candidates: list[dict[str, Any]] = []
    if args.candidates:
        candidates = candidates_from(load_json(args.candidates))
    protected = (context.get("design_constraints") or {}).get("protected_positions", [])
    write_json(args.output, {
        "schema_version": "rational-design-advanced-plan/v1",
        "status": "planned",
        "context_path": str(args.context.resolve()),
        "candidate_input_path": str(args.candidates.resolve()) if args.candidates else None,
        "candidate_count": len(candidates),
        "protected_positions": protected,
        "selected_method_ids": selected_methods(catalog, context),
        "method_catalog_path": str(CATALOG.resolve()),
        "requires_user_confirmation": True,
        "resource_confirmation": {"required": True, "confirmed": False, "scope": None, "estimate": catalog.get("time_note")},
        "mechanism_hypotheses": [],
        "limitations": ["Populate mechanism hypotheses with source evidence, computational inference, or explicit hypotheses before execution."]
    })
    print(args.output)
    return 0


def command_validate_plan(args: argparse.Namespace) -> int:
    plan = load_json(args.plan)
    errors: list[str] = []
    if not isinstance(plan, dict) or plan.get("schema_version") != "rational-design-advanced-plan/v1":
        errors.append("invalid rational-design advanced plan schema")
    if not isinstance(plan.get("selected_method_ids") if isinstance(plan, dict) else None, list):
        errors.append("selected_method_ids must be a list")
    confirmation = plan.get("resource_confirmation") if isinstance(plan, dict) else None
    if not isinstance(confirmation, dict) or not isinstance(confirmation.get("confirmed"), bool):
        errors.append("resource_confirmation.confirmed must be boolean")
    if errors:
        raise SystemExit("\n".join(errors))
    print(args.plan)
    return 0


def validate_annotations(data: Any, candidate_mutations: set[str] | None = None) -> tuple[list[str], list[str]]:
    """Validate annotations; returns (errors, warnings).

    schema v1 keeps the legacy rules exactly. schema v2 additionally enforces
    evidence `level` membership, level/kind compatibility, and status-level
    consistency (support needs direct or multi-level computational evidence;
    conditional needs at least one leveled item; conflict needs counterevidence).
    """
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(data, dict) or data.get("schema_version") not in ANNOTATION_SCHEMAS:
        return ["invalid rational annotation schema (expected annotations/v1 or /v2)"], []
    schema_version = str(data.get("schema_version"))
    is_v2 = schema_version.endswith("/v2")
    if not is_v2:
        warnings.append(f"{schema_version}: legacy schema; evidence-level enforcement is only active for annotations/v2")
    levels = load_evidence_levels().get("levels", {})
    annotations = data.get("annotations")
    if not isinstance(annotations, list) or not annotations:
        return ["annotations must be a non-empty list"], warnings
    for index, annotation in enumerate(annotations):
        prefix = f"annotations[{index}]"
        if not isinstance(annotation, dict):
            errors.append(f"{prefix} must be an object")
            continue
        mutation = annotation.get("mutation")
        if not isinstance(mutation, str) or not MUTATION_RE.fullmatch(mutation):
            errors.append(f"{prefix}.mutation must use canonical mutation notation")
        elif candidate_mutations is not None and mutation not in candidate_mutations:
            errors.append(f"{prefix}.mutation is absent from candidate input")
        status = annotation.get("status")
        if status not in STATUSES:
            errors.append(f"{prefix}.status is invalid")
        cards = annotation.get("decision_cards")
        if not isinstance(cards, list) or not cards:
            errors.append(f"{prefix}.decision_cards must be non-empty")
        else:
            for card_index, card in enumerate(cards):
                if not isinstance(card, dict):
                    errors.append(f"{prefix}.decision_cards[{card_index}] must be an object")
                    continue
                missing = CARD_FIELDS - set(card)
                if missing:
                    errors.append(f"{prefix}.decision_cards[{card_index}] missing {sorted(missing)}")
        evidence = annotation.get("evidence")
        valid_kinds = {"source_evidence", "computational_inference", "hypothesis"}
        if not isinstance(evidence, list) or not evidence:
            errors.append(f"{prefix}.evidence must be non-empty")
            continue
        for evidence_index, item in enumerate(evidence):
            ev_prefix = f"{prefix}.evidence[{evidence_index}]"
            if not isinstance(item, dict):
                errors.append(f"{ev_prefix} must be an object")
                continue
            kind = item.get("kind")
            if kind not in valid_kinds:
                errors.append(f"{ev_prefix}.kind is invalid")
            level = item.get("level")
            if not is_v2:
                continue
            if level is None:
                warnings.append(f"{ev_prefix}.level is missing (recommended for annotations/v2)")
                continue
            if level not in levels:
                errors.append(f"{ev_prefix}.level is invalid (see references/evidence-levels.json)")
                continue
            allowed_kinds = levels[level].get("kinds", [])
            if kind in allowed_kinds:
                continue
            if kind is not None:
                errors.append(f"{ev_prefix}.level {level!r} is incompatible with kind {kind!r}")
        if is_v2 and status in STATUSES:
            leveled = [
                str(item.get("level"))
                for item in evidence
                if isinstance(item, dict) and item.get("kind") != "hypothesis" and item.get("level") in levels
            ]
            has_counterevidence = any(
                isinstance(card, dict) and str(card.get("counterevidence_or_risk", "")).strip()
                for card in cards if isinstance(cards, list)
            ) if isinstance(cards, list) else False
            direct = [level for level in leveled if level in _DIRECT_LEVELS]
            computational = {level for level in leveled if level in _COMPUTATIONAL_LEVELS}
            if status == "support" and not (direct or len(computational) >= 2):
                errors.append(
                    f"{prefix}.status 'support' needs ≥1 direct level (experimental/structural_biochemistry) "
                    "or ≥2 distinct computational levels"
                )
            elif status == "conditional" and not leveled:
                errors.append(f"{prefix}.status 'conditional' needs at least one evidence item with a level")
            elif status == "conflict" and not has_counterevidence:
                errors.append(f"{prefix}.status 'conflict' needs non-empty counterevidence_or_risk on a decision card")
    return errors, warnings


def command_validate_annotations(args: argparse.Namespace) -> int:
    candidate_mutations = None
    if args.candidates:
        candidate_mutations = {str(item.get("mutation")) for item in candidates_from(load_json(args.candidates))}
    errors, warnings = validate_annotations(load_json(args.annotations), candidate_mutations)
    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)
    if errors:
        raise SystemExit("\n".join(errors))
    print(args.annotations)
    return 0


def _mutation_positions(mutation: str) -> list[int]:
    return [int(match) for match in re.findall(r"[A-Z]([1-9][0-9]*)[A-Z]", mutation)]


def _first_card_field(annotation: dict[str, Any], field: str) -> str:
    cards = annotation.get("decision_cards")
    if not isinstance(cards, list):
        return ""
    for card in cards:
        if isinstance(card, dict) and card.get(field):
            return str(card[field])
    return ""


def _annotation_levels(annotation: dict[str, Any]) -> list[str]:
    evidence = annotation.get("evidence")
    if not isinstance(evidence, list):
        return []
    return [
        str(item["level"])
        for item in evidence
        if isinstance(item, dict) and item.get("kind") != "hypothesis" and item.get("level")
    ]


def command_build_evidence_bundle(args: argparse.Namespace) -> int:
    candidates = candidates_from(load_json(args.candidates))
    annotations = load_json(args.annotations)
    errors, warnings = validate_annotations(annotations, {str(item.get("mutation")) for item in candidates})
    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)
    if errors:
        raise SystemExit("\n".join(errors))
    by_mutation = {str(item["mutation"]): item for item in annotations["annotations"]}
    enriched = []
    for candidate in candidates:
        mutation = str(candidate.get("mutation"))
        copy = dict(candidate)
        if mutation in by_mutation:
            copy["rational_advanced"] = by_mutation[mutation]
            workflows = copy.get("source_workflow")
            if not isinstance(workflows, list):
                workflows = [workflows] if workflows else []
            copy["source_workflow"] = list(dict.fromkeys([*workflows, "rational-design-advanced"]))
        enriched.append(copy)
    write_json(args.output, {"schema_version": "rational-design-advanced-evidence-bundle/v1", "candidates": enriched})
    if getattr(args, "excluded_output", None):
        excluded = [
            {
                "mutation": str(item["mutation"]),
                "status": str(item.get("status", "")),
                "decision_layer": _first_card_field(item, "decision_layer"),
                "counterevidence_or_risk": _first_card_field(item, "counterevidence_or_risk"),
                "falsifiable_prediction": _first_card_field(item, "falsifiable_prediction"),
                "evidence_levels": _annotation_levels(item),
            }
            for item in annotations["annotations"]
            if item.get("status") in {"conflict", "insufficient_evidence"}
        ]
        write_json(
            args.excluded_output,
            {
                "schema_version": "rational-design-advanced-excluded-hypotheses/v1",
                "excluded": excluded,
            },
        )
        print(args.excluded_output)
    if getattr(args, "registry_output", None):
        entries = []
        for item in annotations["annotations"]:
            entries.append(
                {
                    "mutation": str(item["mutation"]),
                    "positions": _mutation_positions(str(item["mutation"])),
                    "status": str(item.get("status", "")),
                    "decision_layer": _first_card_field(item, "decision_layer"),
                    "confidence": _first_card_field(item, "confidence"),
                    "evidence_kinds": [
                        str(entry.get("kind")) for entry in item.get("evidence", []) if isinstance(entry, dict) and entry.get("kind")
                    ],
                    "evidence_levels": _annotation_levels(item),
                    "falsifiable_prediction": _first_card_field(item, "falsifiable_prediction"),
                    "distinguishing_experiment": _first_card_field(item, "distinguishing_experiment"),
                    "experiment_outcome": "pending",
                }
            )
        write_json(
            args.registry_output,
            {
                "schema_version": "rational-design-advanced-hypothesis-registry/v1",
                "entries": entries,
            },
        )
        print(args.registry_output)
    print(args.output)
    return 0


OUTCOMES = {"pending", "confirmed", "rejected", "ambiguous"}


def command_update_hypothesis_outcome(args: argparse.Namespace) -> int:
    registry = load_json(args.registry)
    if not isinstance(registry, dict) or registry.get("schema_version") != "rational-design-advanced-hypothesis-registry/v1":
        raise SystemExit("invalid hypothesis registry (expected rational-design-advanced-hypothesis-registry/v1)")
    outcomes = load_json(args.outcomes)
    if not isinstance(outcomes, dict):
        raise SystemExit("--outcomes must be a JSON object mapping mutation to outcome (or an object with outcome/evidence)")
    entries = registry.get("entries")
    if not isinstance(entries, list):
        raise SystemExit("registry entries must be a list")
    by_mutation = {str(entry.get("mutation")): entry for entry in entries if isinstance(entry, dict)}
    unknown = sorted(set(outcomes) - set(by_mutation))
    if unknown:
        raise SystemExit(f"outcome mutations absent from registry: {', '.join(unknown)}")
    for mutation, spec in outcomes.items():
        if isinstance(spec, dict):
            outcome = spec.get("outcome")
            evidence = spec.get("evidence")
        else:
            outcome = spec
            evidence = None
        if outcome not in OUTCOMES:
            raise SystemExit(f"invalid outcome {outcome!r} for {mutation} (expected {sorted(OUTCOMES)})")
        entry = by_mutation[str(mutation)]
        entry["experiment_outcome"] = outcome
        if evidence is not None:
            entry["experiment_evidence"] = str(evidence)
        entry["experiment_updated_at"] = args.updated_at
    write_json(args.output or args.registry, registry)
    print(args.output or args.registry)
    return 0


# ---------------------------------------------------------------------------
# Review HTML rendering (self-contained, html-report-renderer compatible)
# ---------------------------------------------------------------------------

_FALLBACK_CSS = """
:root { --bg:#f5f7f8; --paper:#fff; --ink:#18212b; --muted:#64717f; --line:#d9e2e3;
  --accent:#216b6f; --accent-2:#d95d39; --ok:#147a55; --warn:#9a5b00; --danger:#b42318; }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--ink);
  font-family:"Aptos","Segoe UI","Noto Sans",Arial,sans-serif; line-height:1.58; }
main, .report-shell { width:min(1180px, calc(100% - 32px)); margin:0 auto; padding:28px 0 44px; }
.report-hero { background:linear-gradient(135deg,#fff,#f4faf9); border:1px solid rgba(33,107,111,.18);
  border-radius:8px; padding:clamp(22px,4vw,42px); margin:0 0 18px; }
.report-kicker { margin:0 0 8px; color:var(--accent); font-size:12px; font-weight:700;
  letter-spacing:.08em; text-transform:uppercase; }
h1 { margin:0; font-size:clamp(30px,4.8vw,54px); line-height:1.04; }
.report-subtitle { color:var(--muted); max-width:76ch; margin:14px 0 0; font-size:16px; }
.report-meta { display:flex; flex-wrap:wrap; gap:8px; margin-top:20px; }
section, .section, .panel, .card { background:rgba(255,255,255,.92); border:1px solid var(--line);
  border-radius:8px; margin:14px 0; padding:clamp(16px,2.6vw,24px); }
h2 { margin:0 0 12px; font-size:clamp(19px,2vw,24px); }
h3 { margin:18px 0 8px; font-size:17px; }
table { width:100%; border-collapse:separate; border-spacing:0; margin:10px 0 2px;
  border:1px solid var(--line); border-radius:8px; font-size:14px; }
th, td { border-bottom:1px solid var(--line); padding:9px 11px; text-align:left; vertical-align:top; }
th { background:#eef4f3; color:#33424d; font-weight:700; }
td { background:#fff; overflow-wrap:anywhere; }
tr:last-child > th, tr:last-child > td { border-bottom:0; }
code { background:#eef3f3; border:1px solid #dbe5e5; border-radius:5px; color:#164e53;
  font-family:"Cascadia Mono",Consolas,monospace; font-size:.92em; padding:1px 5px; }
.metric-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(170px,1fr)); gap:10px; }
.metric { background:#f9fbfb; border:1px solid #dfe8e8; border-radius:8px; padding:12px; }
.metric span { display:block; color:var(--muted); font-size:12px; font-weight:600; }
.metric strong { display:block; margin-top:3px; font-size:22px; font-variant-numeric:tabular-nums; }
.pill, .badge { display:inline-flex; align-items:center; gap:5px; border-radius:999px;
  padding:4px 10px; background:#e7f3f2; color:#17575b; font-size:12px; font-weight:700; white-space:nowrap; }
.ok, .badge-success { color:var(--ok); background:#ecf8f1; }
.badge-warning { color:var(--warn); background:#fff4e5; }
.badge-danger { color:var(--danger); background:#fff0ed; }
.callout { border-left:4px solid var(--accent-2); background:#fff8f4; border-radius:6px; padding:10px 12px; }
details { border:1px solid #dfe8e8; border-radius:8px; background:#fff; margin:10px 0; padding:10px 12px; }
summary { cursor:pointer; color:#243540; font-weight:700; }
.muted { color:var(--muted); }
footer { margin-top:28px; padding-top:14px; border-top:1px solid var(--line); color:var(--muted); font-size:13px; }
@media (max-width:760px) { main, .report-shell { width:min(100% - 20px,1180px); } table { display:block; overflow-x:auto; } }
"""


def _report_css() -> str:
    try:
        sys.path.insert(0, str(ROOT))
        from enzyme_core.support.html_report_renderer import report_css  # noqa: PLC0415

        return report_css()
    except Exception:
        return _FALLBACK_CSS


def _esc(value: Any) -> str:
    return html.escape("" if value is None else str(value))


_STATUS_CHIP = {
    "support": "pill ok",
    "conditional": "pill badge-warning",
    "conflict": "pill badge-danger",
    "insufficient_evidence": "pill badge-warning",
}
_KIND_BADGE = {
    "source_evidence": "badge badge-success",
    "computational_inference": "badge",
    "hypothesis": "badge badge-warning",
}
_CARD_LABELS = [
    ("phenotype", "Phenotype"),
    ("mechanism_hypothesis", "Mechanism hypothesis"),
    ("decision_layer", "Decision layer"),
    ("strategy", "Strategy"),
    ("priority_regions", "Priority regions"),
    ("mutation_patterns", "Mutation patterns"),
    ("computational_evidence", "Computational evidence"),
    ("primary_experimental_metric", "Primary experimental metric"),
    ("falsifiable_prediction", "Falsifiable prediction"),
    ("distinguishing_experiment", "Distinguishing experiment"),
    ("counterevidence_or_risk", "Counterevidence / risk"),
    ("confidence", "Confidence"),
]


def _review_entries(bundle: dict[str, Any] | None, annotations: dict[str, Any] | None) -> list[tuple[str, dict[str, Any] | None, dict[str, Any] | None]]:
    entries: list[tuple[str, dict[str, Any] | None, dict[str, Any] | None]] = []
    by_mutation: dict[str, dict[str, Any]] = {}
    if isinstance(annotations, dict):
        for ann in annotations.get("annotations", []):
            if isinstance(ann, dict) and ann.get("mutation"):
                by_mutation[str(ann["mutation"])] = ann
    if isinstance(bundle, dict) and isinstance(bundle.get("candidates"), list):
        for cand in bundle["candidates"]:
            if not isinstance(cand, dict) or not cand.get("mutation"):
                continue
            mutation = str(cand["mutation"])
            ann = cand.get("rational_advanced") if isinstance(cand.get("rational_advanced"), dict) else None
            if ann is None:
                ann = by_mutation.get(mutation)
            entries.append((mutation, cand, ann))
    elif annotations:
        for ann in annotations.get("annotations", []):
            if isinstance(ann, dict) and ann.get("mutation"):
                entries.append((str(ann["mutation"]), None, ann))
    return entries


def _render_metrics(plan: dict[str, Any], entries: list[tuple[str, dict[str, Any] | None, dict[str, Any] | None]]) -> str:
    selected = plan.get("selected_method_ids")
    method_count = len(selected) if isinstance(selected, list) else 0
    protected = plan.get("protected_positions")
    protected_text = ", ".join(str(p) for p in protected) if isinstance(protected, list) and protected else "none"
    confirmed = (plan.get("resource_confirmation") or {}).get("confirmed")
    items = [
        ("Candidate count", str(plan.get("candidate_count", len(entries)))),
        ("Annotated", str(len(entries))),
        ("Selected methods", str(method_count)),
        ("Protected positions", protected_text),
        ("Resource confirmed", "yes" if confirmed is True else "no"),
    ]
    cells = "\n".join(
        f'<div class="metric"><span>{_esc(label)}</span><strong>{_esc(value)}</strong></div>'
        for label, value in items
    )
    return f'<div class="metric-grid">{cells}</div>'


def _render_resource_callout(plan: dict[str, Any]) -> str:
    rc = plan.get("resource_confirmation")
    if not isinstance(rc, dict):
        return ""
    required = rc.get("required")
    confirmed = rc.get("confirmed")
    if confirmed is True:
        status = '<span class="pill ok">confirmed</span>'
        body = f'Scope: <code>{_esc(rc.get("scope"))}</code>'
        if rc.get("confirmed_at"):
            body += f' · confirmed at {_esc(rc.get("confirmed_at"))}'
    else:
        status = '<span class="pill badge-warning">not confirmed</span>'
        body = "Heavy downstream calculations (QM/MM, MD, PLIP, CAVER, PyRosetta) must not start until the user confirms the resource envelope."
    required_note = "required" if required is True else "not required"
    estimate = rc.get("estimate")
    if estimate:
        body += f"<br><span class=\"muted\">Estimate: {_esc(estimate)}</span>"
    return (
        '<section class="section"><h2>Resource confirmation</h2>'
        f'<div class="callout">{status} · {_esc(required_note)}<br>{body}</div></section>'
    )


def _render_methods(plan: dict[str, Any], catalog: dict[str, Any]) -> str:
    methods = catalog.get("methods", [])
    by_id = {str(m.get("id")): m for m in methods if isinstance(m, dict)}
    selected = plan.get("selected_method_ids")
    rows = [by_id.get(str(s)) for s in selected] if isinstance(selected, list) else methods
    rows = [m for m in rows if m]
    if not rows:
        return ""
    body_rows = "\n".join(
        "<tr>"
        f"<td><code>{_esc(m.get('id'))}</code><br>{_esc(m.get('title'))}</td>"
        f"<td>{_esc(m.get('layer'))}</td>"
        f"<td><code>{_esc(m.get('route'))}</code></td>"
        f"<td>{_esc(m.get('estimate'))}</td>"
        f"<td>{_esc(m.get('boundary'))}</td>"
        "</tr>"
        for m in rows
    )
    return (
        '<section class="section"><h2>Selected method cards</h2>'
        "<table><thead><tr><th>Method</th><th>Layer</th><th>Route</th><th>Estimate</th><th>Boundary</th></tr></thead>"
        f"<tbody>{body_rows}</tbody></table></section>"
    )


def _render_candidates(bundle: dict[str, Any] | None, annotations: dict[str, Any] | None) -> str:
    entries = _review_entries(bundle, annotations)
    if not entries:
        return ""
    parts = ['<section class="section"><h2>Candidate annotations</h2>']
    for mutation, cand, ann in entries:
        parts.append('<article class="card">')
        chip = ""
        confidence_note = ""
        if ann:
            status = str(ann.get("status", ""))
            chip = f'<span class="{_STATUS_CHIP.get(status, "pill")}">{_esc(status)}</span>'
        parts.append(f"<h3><code>{_esc(mutation)}</code> {chip}</h3>")
        facts: list[str] = []
        if cand:
            scores = cand.get("scores")
            if isinstance(scores, dict) and scores:
                facts.append("scores: " + ", ".join(f"{_esc(k)}={_esc(v)}" for k, v in scores.items()))
            src = cand.get("source_workflow")
            if isinstance(src, list):
                facts.append("source: " + ", ".join(_esc(s) for s in src))
            elif src:
                facts.append("source: " + _esc(src))
        if facts:
            parts.append('<p class="muted">' + " · ".join(facts) + "</p>")
        if not ann:
            parts.append('<div class="callout">No rational annotation recorded for this candidate.</div>')
            parts.append("</article>")
            continue
        evidence = ann.get("evidence")
        if isinstance(evidence, list) and evidence:
            badges = []
            for item in evidence:
                if not isinstance(item, dict):
                    continue
                if item.get("kind"):
                    kind = str(item["kind"])
                    badges.append(f'<span class="{_KIND_BADGE.get(kind, "badge")}">{_esc(kind)}</span>')
                if item.get("level"):
                    badges.append(f'<span class="badge">{_esc(item["level"])}</span>')
            parts.append('<p class="muted">Evidence: ' + " ".join(badges) + "</p>")
        parts.append("<details><summary>Decision cards</summary>")
        cards = ann.get("decision_cards")
        if isinstance(cards, list) and cards:
            for index, card in enumerate(cards):
                if not isinstance(card, dict):
                    continue
                parts.append(f"<h3>Card {index + 1}</h3><table>")
                for key, label in _CARD_LABELS:
                    value = card.get(key)
                    if value is None:
                        continue
                    if isinstance(value, list):
                        value = "; ".join(str(item) for item in value)
                    parts.append(f"<tr><th>{_esc(label)}</th><td>{_esc(value)}</td></tr>")
                parts.append("</table>")
                if str(card.get("confidence", "")).lower() == "low":
                    confidence_note = "Low confidence: treat as exploratory; mechanism unverified."
        else:
            parts.append('<div class="callout">No decision cards recorded.</div>')
        parts.append("</details>")
        if confidence_note:
            parts.append(f'<div class="callout">{_esc(confidence_note)}</div>')
        parts.append("</article>")
    parts.append("</section>")
    return "\n".join(parts)


def _render_provenance(plan: dict[str, Any], paths: dict[str, str | None], context: dict[str, Any] | None) -> str:
    rows = []
    if context:
        rows.append(("Design context", _esc(paths.get("context") or "")))
        target = context.get("target") if isinstance(context.get("target"), dict) else {}
        if target.get("name"):
            rows.append(("Target", _esc(target["name"])))
        evidence = context.get("evidence")
        if isinstance(evidence, dict):
            for key, value in evidence.items():
                rows.append((f"Context evidence: {key}", _esc(value)))
    rows.append(("Rational design plan", _esc(paths.get("plan") or "not created (annotation-only path)")))
    rows.append(("Method catalog", _esc(plan.get("method_catalog_path") or "")))
    if paths.get("annotations"):
        rows.append(("Rational annotations", _esc(paths["annotations"])))
    if paths.get("bundle"):
        rows.append(("Evidence bundle", _esc(paths["bundle"])))
    if paths.get("registry"):
        rows.append(("Hypothesis registry", _esc(paths["registry"])))
    body_rows = "\n".join(f"<tr><th>{_esc(label)}</th><td><code>{_esc(value)}</code></td></tr>" for label, value in rows)
    return f'<section class="section"><h2>Provenance</h2><table><tbody>{body_rows}</tbody></table></section>'


_OUTCOME_CHIP = {
    "pending": "pill badge-warning",
    "confirmed": "pill ok",
    "rejected": "pill badge-danger",
    "ambiguous": "pill badge-warning",
}


def _render_registry(registry: dict[str, Any] | None) -> str:
    if not isinstance(registry, dict):
        return ""
    entries = registry.get("entries")
    if not isinstance(entries, list) or not entries:
        return ""
    rows = "\n".join(
        "<tr>"
        f"<td><code>{_esc(entry.get('mutation'))}</code></td>"
        f"<td>{_esc(entry.get('status'))}</td>"
        f"<td>{_esc(entry.get('decision_layer'))}</td>"
        f"<td>{_esc(', '.join(entry.get('evidence_levels', [])))}</td>"
        f"<td>{_esc(entry.get('falsifiable_prediction'))}</td>"
        f"<td><span class=\"{_OUTCOME_CHIP.get(str(entry.get('experiment_outcome')), 'pill')}\">{_esc(entry.get('experiment_outcome'))}</span></td>"
        "</tr>"
        for entry in entries
        if isinstance(entry, dict)
    )
    return (
        '<section class="section"><h2>Mechanism hypothesis registry</h2>'
        "<table><thead><tr><th>Mutation</th><th>Status</th><th>Layer</th><th>Evidence levels</th>"
        "<th>Falsifiable prediction</th><th>Experiment outcome</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
        '<div class="callout">Update <code>experiment_outcome</code> after assays with '
        '<code>update-hypothesis-outcome</code> so the next iteration can read which hypotheses '
        "were confirmed or excluded.</div></section>"
    )


def render_report(plan: dict[str, Any] | None, annotations: dict[str, Any] | None, bundle: dict[str, Any] | None, context: dict[str, Any] | None, catalog: dict[str, Any], paths: dict[str, str | None], registry: dict[str, Any] | None = None) -> str:
    plan = plan if isinstance(plan, dict) else {}
    demo = bool((annotations and annotations.get("demo")) or (context and context.get("demo")) or (bundle and bundle.get("demo")))
    subtitle = objective_text(context) if context else ""
    plan_status = plan.get("status", "")
    if plan:
        if subtitle:
            subtitle = f"{subtitle} · plan status: {plan_status}"
        else:
            subtitle = f"Plan status: {plan_status}"
    else:
        subtitle = (subtitle + " · " if subtitle else "") + "annotation-only review (no arbitration plan created)"
    title = "Rational Design Advanced — Candidate Mechanism Evidence Review"
    entries = _review_entries(bundle, annotations)
    body = "\n".join(
        [
            _render_metrics(plan, entries),
            _render_resource_callout(plan),
            _render_methods(plan, catalog),
            _render_candidates(bundle, annotations),
            _render_registry(registry),
            _render_provenance(plan, paths, context),
        ]
    )
    if demo:
        body = (
            '<section class="section"><h2>Review status</h2>'
            '<div class="callout">Demo / dry-run artifacts: mechanism hypotheses are labelled '
            '<span class="badge badge-warning">hypothesis</span> and require host-agent review before any '
            "downstream design decision. This report is for workflow inspection only.</div></section>"
        ) + body
    return (
        "<!doctype html>\n"
        '<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{_esc(title)}</title>\n"
        '<meta name="generator" content="opencode-html-report-renderer">\n'
        f"<style>{_report_css()}</style>\n</head>\n<body>\n"
        '<main class="report-shell">\n'
        '<header class="report-hero">\n'
        '<p class="report-kicker">Rational Design Advanced</p>\n'
        f"<h1>{_esc(title)}</h1>\n"
        f'<p class="report-subtitle">{_esc(subtitle)}</p>\n'
        "</header>\n"
        f"{body}\n"
        "<footer>Rendered with OpenCode html-report-renderer. The caller owns interpretation and candidate decisions.</footer>\n"
        "</main>\n</body>\n</html>\n"
    )


def command_build_report(args: argparse.Namespace) -> int:
    plan = load_json(args.plan) if args.plan else None
    annotations = load_json(args.annotations) if args.annotations else None
    bundle = load_json(args.bundle) if args.bundle else None
    context = load_json(args.context) if args.context else None
    registry = load_json(args.registry) if getattr(args, "registry", None) else None
    paths: dict[str, str | None] = {
        "plan": str(args.plan.resolve()) if args.plan else None,
        "annotations": str(args.annotations.resolve()) if args.annotations else None,
        "bundle": str(args.bundle.resolve()) if args.bundle else None,
        "context": str(args.context.resolve()) if args.context else None,
        "registry": str(args.registry.resolve()) if getattr(args, "registry", None) else None,
    }
    report = render_report(plan, annotations, bundle, context, load_json(CATALOG), paths, registry)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report, encoding="utf-8")
    print(args.output)
    return 0


def command_method_catalog(args: argparse.Namespace) -> int:
    catalog = load_json(CATALOG)
    if args.output:
        write_json(args.output, catalog)
        print(args.output)
    else:
        print(json.dumps(catalog, indent=2, ensure_ascii=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init-plan")
    init.add_argument("--context", type=Path, required=True)
    init.add_argument("--candidates", type=Path)
    init.add_argument("--output", type=Path, required=True)
    init.set_defaults(func=command_init_plan)
    plan = sub.add_parser("validate-plan")
    plan.add_argument("--plan", type=Path, required=True)
    plan.set_defaults(func=command_validate_plan)
    ann = sub.add_parser("validate-annotations")
    ann.add_argument("--annotations", type=Path, required=True)
    ann.add_argument("--candidates", type=Path)
    ann.set_defaults(func=command_validate_annotations)
    bundle = sub.add_parser("build-evidence-bundle")
    bundle.add_argument("--candidates", type=Path, required=True)
    bundle.add_argument("--annotations", type=Path, required=True)
    bundle.add_argument("--output", type=Path, required=True)
    bundle.add_argument("--excluded-output", type=Path, help="Write conflict/insufficient_evidence annotations as excluded_hypotheses.json")
    bundle.add_argument("--registry-output", type=Path, help="Write mechanism_hypothesis_registry.json for the learning loop")
    bundle.set_defaults(func=command_build_evidence_bundle)
    outcome = sub.add_parser("update-hypothesis-outcome", help="Record assay results back into the hypothesis registry")
    outcome.add_argument("--registry", type=Path, required=True)
    outcome.add_argument("--outcomes", type=Path, required=True, help="JSON object mapping mutation to outcome (or {mutation: {outcome, evidence}})")
    outcome.add_argument("--output", type=Path, help="Output path; defaults to overwriting --registry")
    outcome.add_argument("--updated-at", default="", help="Timestamp recorded on each updated entry")
    outcome.set_defaults(func=command_update_hypothesis_outcome)
    catalog = sub.add_parser("method-catalog")
    catalog.add_argument("--output", type=Path)
    catalog.set_defaults(func=command_method_catalog)
    report = sub.add_parser("build-report", help="Render a self-contained review HTML for user inspection")
    report.add_argument("--plan", type=Path, help="Optional arbitration plan; annotation-only reports work without it")
    report.add_argument("--annotations", type=Path)
    report.add_argument("--bundle", type=Path)
    report.add_argument("--context", type=Path)
    report.add_argument("--registry", type=Path, help="Optional hypothesis registry to include")
    report.add_argument("--output", type=Path, required=True)
    report.set_defaults(func=command_build_report)
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
