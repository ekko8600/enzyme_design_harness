# Architecture

## Components

### `enzyme-core`

Core is a Python distribution of atomic Skills and their public operation interfaces. Its main public surfaces are:

- `enzyme_core.skills.registry`: metadata discovery (`discover`, `describe`)
- `enzyme_core.api`: structured Python access to discovery, preparation, execution, and declared output validation
- `enzyme-core`: command-line access to discover, describe, doctor, prepare, run, and validate operations
- `src/enzyme_core/skills/`: canonical instructions and method resources
- `src/enzyme_core/runtime/operation_interfaces.json`: generated interfaces for the installed Skills

A Skill can be a local Python command, a local executable, or an instruction for a host-authored action. Interface registration does not mean that a backend is installed or that its scientific result has been validated.

### `enzyme-harness`

The separate `harness/` distribution runs local tasks through a selectable native agent: Codex CLI (`agent: codex`, GPT-family models) or DeepSeek Harness (`agent: dsh`, DeepSeek models and configurable provider routes). It creates task-local JSON state and input snapshots, starts the main agent session, records process invocations and receipts, resumes from saved state, requests a separate read-only review, and writes a report. Long commands can use the process wrapper for process identity, logs, exit status, and output hashes. Task content stays on the local machine unless external services used by the agent or a Skill receive it.

Agent-specific launch and record interpretation live in `harness/src/enzyme_harness/agents/`; the controller keeps the state machine, budget checks, artifact validation, repair candidates, and release activation shared. Codex calls use `codex exec` with a schema-enforced answer file; dsh calls use a task-owned headless profile with a per-call `--patch` overlay for the model route, session store, and storage root. `sandbox_backend: auto` confines the main process in a bwrap namespace and keeps reviews read-only; `sandbox_backend: none` is the explicit unconfined mode for hosts with no usable OS sandbox, and records that fact per call.

Harness does not provide hosted scheduling or a scientific database. Task success and artifact validation are separate from scientific interpretation and experimental evidence.

## Data and resource configuration

`runtime/resources.json` declares model, executable, and environment locations. The published default uses environment-variable placeholders for machine-specific paths. Set `ENZYME_CORE_RESOURCES` to select a complete alternate configuration or `ENZYME_CORE_RESOURCES_LOCAL` to overlay local settings. Keep credentials in the environment or a secret manager.

`src/enzyme_core/vendor/` bundles the MIT-licensed LigandMPNN and mut_model source that those Skills execute; `${ENZYME_CORE_VENDOR}` resolves there for source and wheel installs. Model weights, datasets, Docker images, and external tools remain external resources.

Install the Core and Harness packages from this checkout independently:

```bash
python -m pip install -e .
python -m pip install -e ./harness
```

Core's base dependencies are intentionally small. Scientific Skills may need extra Python libraries, native executables, model weights, external services, GPU drivers, or licenses. Check the individual Skill instructions and `doctor` output for the chosen operation.
