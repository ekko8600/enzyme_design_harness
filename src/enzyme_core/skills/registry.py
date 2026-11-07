"""Metadata-only discovery for the migrated atomic Skills.

This module deliberately does not load Scientific Contracts, Host phase rules,
project state, or execution policy.  Those files may travel with a Skill as
reference material, but are not executable discovery constraints.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml


def project_root() -> Path:
    """Installed package root, independent of the caller's working directory."""
    return Path(__file__).resolve().parents[1]


def skills_root() -> Path:
    return project_root() / "skills"


def _skill_dirs():
    for category in sorted(skills_root().iterdir()):
        if not category.is_dir() or category.name.startswith("_"):
            continue
        for skill_dir in sorted(category.iterdir()):
            if skill_dir.is_dir() and (skill_dir / "SKILL.md").is_file():
                yield category.name, skill_dir


def _frontmatter(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return {}
    closing = text.find("\n---", 4)
    if closing < 0:
        return {}
    try:
        value = yaml.safe_load(text[4:closing])
    except yaml.YAMLError:
        return {}
    return value if isinstance(value, dict) else {}


@lru_cache(maxsize=1)
def list_skills() -> list[dict[str, Any]]:
    records = []
    for category, directory in _skill_dirs():
        instruction = directory / "SKILL.md"
        profile = directory / "execution_profile.json"
        metadata = _frontmatter(instruction)
        records.append({
            "id": directory.name,
            "description": str(metadata.get("description", "")).replace("\n", " ").strip(),
            "category": category,
            "canonical_path": directory.relative_to(project_root()).as_posix(),
            "instruction_path": instruction.relative_to(project_root()).as_posix(),
            "profile_path": profile.relative_to(project_root()).as_posix() if profile.is_file() else None,
            "capability_ids": metadata.get("metadata", {}).get("capability_ids", []),
        })
    if len({record["id"] for record in records}) != len(records):
        raise ValueError("DUPLICATE_SKILL_ID")
    return records


def resolve_skill_dir(skill_id: str) -> Path:
    for _, directory in _skill_dirs():
        if directory.name == skill_id:
            return directory
    raise KeyError(f"Unknown Skill: {skill_id}")


def resolve_skill_file(skill_id: str, relative_path: str = "SKILL.md") -> Path:
    directory = resolve_skill_dir(skill_id).resolve()
    path = (directory / relative_path).resolve()
    if path != directory and directory not in path.parents:
        raise ValueError("Skill file path escapes its canonical directory")
    if not path.is_file():
        raise FileNotFoundError(path)
    return path


def discover(query: str = "") -> list[dict[str, Any]]:
    query = query.strip().casefold()
    records = list_skills()
    if not query:
        return records
    return [record for record in records if query in " ".join((
        record["id"], record["description"], record["category"],
        " ".join(record["capability_ids"]),
    )).casefold()]


def describe(skill_id: str) -> dict[str, Any]:
    record = next((item for item in list_skills() if item["id"] == skill_id), None)
    if record is None:
        raise KeyError(f"Unknown Skill: {skill_id}")
    instruction = resolve_skill_file(skill_id, "SKILL.md")
    profile_path = record["profile_path"]
    profile = None
    if profile_path:
        import json
        profile = json.loads((project_root() / profile_path).read_text(encoding="utf-8"))
    return {"skill": record, "instruction_path": str(instruction),
            "instruction_markdown": instruction.read_text(encoding="utf-8"),
            "execution_profile": profile,
            "scientific_contract": yaml.safe_load(resolve_skill_file(skill_id, "scientific_contract.yaml").read_text())}


# Small compatibility names used by migrated Skill scripts.
resolve_skill_path = resolve_skill_file
