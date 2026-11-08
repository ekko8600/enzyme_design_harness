---
name: pyrosetta-setup
description: 'Set up and troubleshoot PyRosetta for OpenCode protein modeling workflows.
  Use this skill when PyRosetta is missing, licensing or wheel installation is unclear,
  initialization fails, or a user needs a working local runtime.

  '
metadata:
  category: utilities
  tags: pyrosetta, rosetta, setup, installation
  taxonomy_version: enzyme-agent/taxonomy/v1
  capability_ids:
  - utility.data_processing
---

# PyRosetta Setup

Use this skill before running PyRosetta protocols.

## Local Setup

```bash
pip install pyrosetta-installer
python -c "import pyrosetta_installer; pyrosetta_installer.install_pyrosetta()"
```

Then smoke test:

```bash
enzyme-core run pyrosetta-setup --work-dir ./runs/pyrosetta-setup --operation pyrosetta_tools -- --help
```


## Python 3.12 Compatibility Check

The current `agent` environment may use Python 3.12. PyRosetta wheels are tied
to the Python ABI, and some low-level movers can segfault even when import and
`pyrosetta.init()` succeed. Always run this smoke test after installing or
updating PyRosetta:

```bash
enzyme-core run pyrosetta-setup --work-dir ./runs/pyrosetta-setup --operation pyrosetta_tools -- smoke-test --relax
```

Known local fix: avoid `pyrosetta.rosetta.protocols.simple_moves.MutateResidue`
in Python 3.12 wrappers. Use `pyrosetta.toolbox.mutate_residue` instead. The
local `pyrosetta_tools.py mutate-scan` command already uses the safer path.

If additional segfaults appear in production protocols, create a dedicated
PyRosetta environment with Python 3.11 and point PyRosetta registry entries to
that environment, for example `conda:pyrosetta-py311`.

## Decision Rules

- PyRosetta is licensed software; do not vendor wheels into this skill.
- Prefer local CLI for small scoring/relax tasks.
- Prefer local batch execution for relax, docking, and design jobs once PyRosetta is installed.
- If import fails, report installation/licensing steps rather than rewriting
  protocols around missing PyRosetta.

## PyRosetta Performance Policy

Standard PyRosetta/Rosetta score functions, rotamer packing, and FastRelax are
CPU-bound and do not use GPU acceleration. For saturation mutagenesis, use the
shared `mutation-ddg-scan` command with multiple CPU workers, local repacking,
`--skip-wt`, and `--resume`; reserve FastRelax for shortlisted or conflicting
mutations.
