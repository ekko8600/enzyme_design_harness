"""Verify installed atomic-core assets and operation/dependency closure."""
from __future__ import annotations

import ast
from collections import Counter
import json
from pathlib import Path

from enzyme_core.skills.registry import describe, list_skills, project_root, resolve_skill_file


def main() -> int:
    root = project_root()
    skills = list_skills()
    errors = []
    kinds = Counter()
    blocked_roots = {"skill_plane", "research_plane", "adapters", "assurance_plane", "experience_memory", "enzyme_domain", "core"}
    for record in skills:
        value = describe(record["id"])
        profile = value["execution_profile"]
        if profile["schema_version"] != "enzyme-core/skill-execution-profile/v1" or profile["default_operation"] not in profile["operations"]:
            errors.append("invalid profile:" + record["id"])
        for name, op in profile["operations"].items():
            kinds[op["kind"]] += 1
            if op["kind"].startswith("local_"):
                try:
                    resolve_skill_file(op.get("target_skill", record["id"]), op["path"])
                except (KeyError, ValueError, OSError) as exc:
                    errors.append(record["id"] + ":" + name + ":" + str(exc))
        if value["scientific_contract"]["skill_id"] != record["id"]:
            errors.append("contract identity:" + record["id"])
    for path in root.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError as exc:
            errors.append(str(path) + ":" + str(exc)); continue
        for node in ast.walk(tree):
            modules = []
            if isinstance(node, ast.Import):
                modules = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                modules = [node.module.split(".")[0]]
            if blocked_roots.intersection(modules):
                errors.append("legacy import:" + str(path))
    saved = json.loads((root / "skill_catalog.json").read_text())
    if saved["skills"] != skills:
        errors.append("stale catalog")
    provenance = json.loads((root / "skill_provenance.json").read_text())
    coverage = {s for group in provenance["groups"] for s in group["skills"]}
    if coverage != {r["id"] for r in skills}:
        errors.append("attribution coverage")
    for group in provenance["groups"]:
        for key in ("notice", "terms_notice", "license_file"):
            if group.get(key) and not (root / group[key]).is_file():
                errors.append("missing notice:" + group[key])
    for path in root.glob("skills/**/*.py"):
        if path.name.startswith("benchmark_") or path.name == "validate_stability.py":
            errors.append("excluded legacy script:" + str(path))
    if any(r["id"] == "reflect-skill-experience" for r in skills):
        errors.append("excluded experience skill")
    result = {"status": "FAILED" if errors else "PASS", "skills": len(skills), "operations": dict(kinds), "errors": errors,
              "scope": "package and operation integrity; runtime readiness and science are separate"}
    print(json.dumps(result, indent=2))
    return bool(errors)


if __name__ == "__main__":
    raise SystemExit(main())
