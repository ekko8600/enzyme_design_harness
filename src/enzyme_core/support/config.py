"""Portable project configuration with optional workstation-local overrides."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from copy import deepcopy
from pathlib import Path
from typing import Any


# ``config.py`` lives under ``enzyme_core/support``; runtime resources are
# stored beside the Skills and contain no Host execution policy.
ROOT = Path(__file__).resolve().parents[1]
BASE_CONFIG = ROOT / "runtime" / "resources.json"
LOCAL_CONFIG = ROOT / "runtime" / "resources.local.json"
# Source code that ships with the package for Skills that execute local project
# checkouts. ``${ENZYME_CORE_VENDOR}`` resolves here for source and wheel installs.
VENDOR = ROOT / "vendor"
_VENDOR_TOKEN = "${ENZYME_CORE_VENDOR}"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def _expand(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _expand(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_expand(item) for item in value]
    if isinstance(value, str):
        if _VENDOR_TOKEN in value:
            value = value.replace(_VENDOR_TOKEN, str(VENDOR))
        return os.path.expanduser(os.path.expandvars(value))
    return value


def load_config(
    base_path: Path | None = None,
    local_path: Path | None = None,
) -> dict[str, Any]:
    """Load base configuration and overlay an ignored workstation-local file."""
    base_path = Path(base_path or os.environ.get("ENZYME_CORE_RESOURCES") or BASE_CONFIG)
    local_path = Path(local_path or os.environ.get("ENZYME_CORE_RESOURCES_LOCAL") or LOCAL_CONFIG)
    base = json.loads(base_path.read_text(encoding="utf-8"))
    if not isinstance(base, dict):
        raise ValueError("resource configuration must be a JSON object")
    if local_path.is_file():
        override = json.loads(local_path.read_text(encoding="utf-8"))
        if not isinstance(override, dict):
            raise ValueError(f"local configuration must be a JSON object: {local_path}")
        base = _merge(base, override)
    return _expand(base)


def conda_env_for_skill(config: dict[str, Any], skill: str) -> str:
    skills = config.get("skills", {})
    groups = config.get("groups", {})
    return str(skills.get(skill) or groups.get("agent") or config.get("default_env") or "").strip()


def conda_sh(config: dict[str, Any]) -> str:
    return str(
        os.environ.get("CONDA_SH")
        or os.environ.get("PROJECT_CONDA_SH")
        or config.get("execution_policy", {}).get("conda_sh")
        or ""
    ).strip()


def path_status(path: str, path_type: str, *, min_bytes: int = 0) -> dict[str, Any]:
    """Return a deterministic, non-mutating availability result for one path."""
    if not path:
        return {"path": path, "available": False, "reason": "not configured"}
    if "${" in path or (path.startswith("$") and os.path.expandvars(path) == path):
        return {"path": path, "available": False, "reason": "unresolved environment variable"}
    resolved = None
    if path_type in {"system_executable", "local_executable"} and not Path(path).is_absolute():
        resolved = shutil.which(path)
        if not resolved:
            return {"path": path, "resolved_path": None, "available": False, "reason": "executable not found"}
    candidate = Path(resolved or path)
    exists = candidate.is_dir() if path_type == "local_directory" else candidate.exists()
    result: dict[str, Any] = {
        "path": path,
        "available": exists,
        **({"resolved_path": resolved} if resolved else {}),
    }
    if not exists:
        result["reason"] = "path not found"
        return result
    if path_type in {"system_executable", "local_executable"} and not os.access(candidate, os.X_OK):
        result.update({"available": False, "reason": "path is not executable"})
        return result
    if min_bytes and candidate.is_file() and candidate.stat().st_size < min_bytes:
        result.update({
            "available": False,
            "reason": f"file smaller than minimum size: {candidate.stat().st_size} < {min_bytes}",
            "size_bytes": candidate.stat().st_size,
            "min_bytes": min_bytes,
        })
    return result


def resource_status(item: dict[str, Any]) -> dict[str, Any]:
    """Check a configured resource and its declared required content."""
    path = str(item.get("path", ""))
    path_type = str(item.get("path_type", ""))
    result = path_status(path, path_type, min_bytes=int(item.get("min_bytes") or 0))
    if not result.get("available"):
        return result
    root = Path(result.get("resolved_path") or path)
    checks: list[dict[str, Any]] = []
    for raw in item.get("required_files", []) or []:
        if not isinstance(raw, dict) or not raw.get("path"):
            checks.append({"available": False, "reason": "invalid required_files entry"})
            continue
        declared = Path(str(raw["path"]))
        candidate = declared if declared.is_absolute() else root / declared
        check = path_status(
            str(candidate),
            str(raw.get("path_type") or "local_file"),
            min_bytes=int(raw.get("min_bytes") or 1),
        )
        check["declared_path"] = str(raw["path"])
        expected_sha256 = str(raw.get("sha256") or "").lower()
        if check.get("available") and expected_sha256:
            digest = _sha256_file(candidate)
            check["sha256"] = digest
            check["expected_sha256"] = expected_sha256
            if digest != expected_sha256:
                check.update({"available": False, "reason": "sha256 mismatch"})
        checks.append(check)
    if checks:
        result["content_checks"] = checks
    failed = [check for check in checks if not check.get("available")]
    if failed:
        result.update({
            "available": False,
            "reason": "; ".join(
                f"{check.get('declared_path', '<invalid>')}: {check.get('reason', 'unavailable')}"
                for check in failed
            ),
        })
    return result


def resource_environment_for_skill(config: dict[str, Any], skill: str) -> dict[str, str]:
    """Resolve configured model/binary variables for a selected Skill process."""
    environment: dict[str, str] = {}
    models = config.get("model_weights", {}).get("models", {}) or {}
    for item in models.values():
        if skill not in item.get("skills", []):
            continue
        for name, value in (item.get("env_vars") or {}).items():
            rendered = str(value).strip()
            if rendered and "${" not in rendered:
                environment[str(name)] = rendered
    for item in (config.get("runtime_binaries", {}) or {}).values():
        if skill not in item.get("skills", []):
            continue
        name = str(item.get("env_var") or "").strip()
        value = str(item.get("path") or "").strip()
        if name and value and "${" not in value:
            environment[name] = value
    return environment
