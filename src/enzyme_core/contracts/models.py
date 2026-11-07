from __future__ import annotations

from dataclasses import dataclass, field
import math
from pathlib import Path


@dataclass(frozen=True)
class ArtifactSpec:
    path: str
    format: str = "file"
    required_keys: tuple[str, ...] = ()
    required_columns: tuple[str, ...] = ()

    def __post_init__(self):
        if not isinstance(self.path, str) or not self.path or Path(self.path).is_absolute() or ".." in Path(self.path).parts:
            raise ValueError("ARTIFACT_PATH_MUST_BE_RELATIVE")
        if self.format not in {"file", "json", "csv", "fasta", "pdb", "html", "tsv", "cif", "image", "collection"}:
            raise ValueError("ARTIFACT_FORMAT_UNSUPPORTED")
        if any(not isinstance(value, str) for value in self.required_keys + self.required_columns):
            raise ValueError("ARTIFACT_FIELDS_INVALID")


@dataclass(frozen=True)
class SkillRequest:
    skill_id: str
    operation: str = "default"
    argv: tuple[str, ...] = ()
    artifacts: tuple[ArtifactSpec, ...] = ()

    def __post_init__(self):
        if not isinstance(self.argv, (list, tuple)) or any(not isinstance(x, str) or "\0" in x for x in self.argv):
            raise ValueError("ARGV_MUST_BE_STRINGS")
        if not isinstance(self.skill_id, str) or not isinstance(self.operation, str):
            raise ValueError("SKILL_OPERATION_INVALID")
        if any(not isinstance(x, ArtifactSpec) for x in self.artifacts):
            raise ValueError("ARTIFACT_SPEC_INVALID")


@dataclass(frozen=True)
class ExecutionContext:
    work_dir: Path
    python: str | None = None
    environment: dict[str, str] = field(default_factory=dict)
    resources: dict | None = None
    timeout_seconds: float | None = None
    implementation: dict | None = None
    credential_env_names: tuple[str, ...] | None = None
    native_isolation: bool = False
    isolation_read_paths: tuple[str, ...] = ()
    network: bool = False

    def __post_init__(self):
        if self.credential_env_names is not None and any(not isinstance(n,str) or not n.isidentifier() for n in self.credential_env_names):raise ValueError('CREDENTIAL_ENV_NAME_INVALID')
        if self.timeout_seconds is not None and (not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0):
            raise ValueError("TIMEOUT_MUST_BE_FINITE_POSITIVE")
        if any(not isinstance(k, str) or not isinstance(v, str) or "\0" in k + v for k, v in self.environment.items()):
            raise ValueError("ENVIRONMENT_MUST_BE_STRINGS")
