#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "skills" / "registry.py").is_file()).parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from enzyme_core.support.html_report_renderer import main


if __name__ == "__main__":
    raise SystemExit(main())
