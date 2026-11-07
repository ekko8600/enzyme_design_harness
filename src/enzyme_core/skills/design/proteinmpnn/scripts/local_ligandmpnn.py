#!/usr/bin/env python3
"""Thin entry point for the shared LigandMPNN-family implementation."""
from pathlib import Path
import sys
import os
import runpy

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "skills" / "registry.py").is_file()).parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from enzyme_core.skills.registry import resolve_skill_file

CANONICAL = resolve_skill_file('ligandmpnn', 'scripts/local_ligandmpnn.py')
os.environ.setdefault("MPNN_MODEL_VARIANT", "proteinmpnn")
globals().update(runpy.run_path(str(CANONICAL), run_name="__main__" if __name__ == "__main__" else "_shared_ligandmpnn"))
