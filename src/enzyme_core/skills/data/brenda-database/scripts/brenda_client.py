from pathlib import Path
import sys

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "skills" / "registry.py").is_file()).parent
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
from enzyme_core.support.brenda_client import call_brenda, get_km_values, get_reactions, split_entries
