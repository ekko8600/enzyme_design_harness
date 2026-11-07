from __future__ import annotations
from pathlib import Path
import sys


def project_root_from(start: Path, marker: str = "enzyme_core") -> Path:
    for parent in [start.resolve(), *start.resolve().parents]:
        if (parent / "skills" / "registry.py").is_file() and (parent / "__init__.py").is_file():
            return parent.parent
    raise ValueError("ENZYME_CORE_PACKAGE_NOT_FOUND")


def ensure_project_root_on_path(start: Path, marker: str = "enzyme_core") -> Path:
    root = project_root_from(start, marker)
    if str(root) not in sys.path: sys.path.insert(0, str(root))
    return root
