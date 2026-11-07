# Harness user and implementation notes

This document describes the current local agent Harness package (version in `pyproject.toml`). It is not a hosted service and does not run a background server.

## Task flow

1. `task submit` resolves the agent and model selection, then snapshots the request and supplied files into a local task directory.
2. The main agent session inspects inputs, plans, invokes Skills, and writes task outputs. Codex runs `codex exec`; the dsh backend runs `dsh --profile headless`.
3. Long scientific commands can run through `enzyme-harness process run`, which records invocation details, logs, process identity, return code, resource measurements, and declared output hashes.
4. A separate read-only session checks the request, evidence, execution records, and outputs. A repair request returns to the main session with the review findings.
5. `task resume` reconciles saved state and process receipts before continuing. Unknown process state requires inspection instead of automatic rerun.
6. `task report` creates a local HTML report.

Task state, input snapshots, agent event files, and outputs are stored below the Harness root. Protect this directory as research data. Review files before sharing them.

## Agent selection

`enzyme-harness setup` stores the default selection in `config.json`; a `request.json` may override `agent`, `model`, `provider`, `reasoning_effort`, `sandbox_backend`, `agent_binary`, `agent_version`, `dsh_providers`, and `dsh_patch`.

- `agent: codex` runs the installed Codex CLI with `--model`, `--output-schema`, and `-o`. Its final answer is schema-enforced by Codex itself.
- `agent: dsh` runs `dsh --profile headless --json`. Its final answer is the agent's final message, which must be one JSON object matching the call schema; the controller extracts and validates it, and retries the same role once more when it is malformed instead of entering code repair.
- Any installed agent version is accepted by default; `doctor` reports the detected version, and each call records it. Set `agent_version` only when a task's evidence requires one exact CLI version.

## Per-call records

Each call gets `tasks/ID/calls/NNNN-ROLE/` (tasks created before agent selection keep their historical `codex/` directory). A call folder contains the prompt, schema, JSONL events, stderr, and a bootstrap spec recording the agent, effective sandbox, and the one process identity. The normalized response is `output.json` and the receipt is `receipt.json`. `main-session.json` records the agent's own session identity; the main role resumes that session for feedback and repairs, and a changed identity fails closed.

Backends interpret their own event dialects:

| Concern | Codex CLI | dsh headless `--json` |
| --- | --- | --- |
| Session | `thread.started.thread_id` | `session.sessionId` / `final.sessionId` |
| Terminal turn | `turn.completed` | `final.reason.kind == "completed"` |
| Usage | `turn.completed.usage` (cumulative per resumed session, deduplicated by max) | `status.step_end.usage` (summed per step) |
| Tool actions | `item.completed` types | `tool_call` names |
| Unfinished shell commands | `item.started` command executions | `tool_call` bash entries without `tool_result` |
| Structured answer | `-o` file produced by Codex | first JSON object in `final.text` |

## dsh isolation and model routing

Each dsh task owns `tasks/ID/dsh/home` with a minimal `headless` profile whose bundle list contains only in-box `dsh-base` and `dsh-headless`. The user's interactive profile, plugins, and MCP servers therefore never enter a task. Every call receives a task-local `--patch` overlay:

- `agent-default-model` — provider, model, and optional reasoning effort;
- `session-persistence-jsonl` and `storage-json` — roots under `tasks/ID/dsh/`;
- `llm-pi-ai` — a route declaration for a non-DeepSeek provider (catalog defaults when `dsh_providers` is omitted, explicit profiles when supplied).

The command runs in the call workspace, so dsh's own skill discovery reads the task snapshot at `workspace/.agents/skills`. Model credentials come from the launch environment (or the workspace `.env`); the shipped headless profile deliberately ignores credentials saved by the GUI. Use `dsh_patch` to re-enable saved credentials or change other profile rows.

## Sandbox policy

`sandbox_backend: auto` (default) enforces confinement: the main Codex or dsh process is wrapped in bwrap with a read-only host mount, native `/dev` for GPUs, and writable task workspace/process/state directories; Codex and dsh themselves run with full access only inside that namespace. Reviews use the agent's own read-only sandbox (Codex's built-in sandbox or dsh's local `read-only` policy); repairs use its workspace-write policy.

`sandbox_backend: none` is the explicit unconfined mode for hosts without a usable OS sandbox (for example a kernel that blocks unprivileged user namespaces). Every role runs with full access, reviews are constrained by the prompt and the controller's artifact-digest checks, and each call records `unconfined: true`; reports sum `unconfined_calls`. Do not treat an unconfined review as equivalent to a sandboxed one.

The bwrap probe is functional, not a `which` check: a bwrap binary that cannot create its uid map fails the task early with `BWRAP_UNAVAILABLE_USE_SANDBOX_BACKEND_NONE`.

## Commands

```bash
enzyme-harness setup --agent codex --model gpt-6-luna
enzyme-harness setup --agent dsh --model deepseek-flash --provider deepseek-official
enzyme-harness doctor
enzyme-harness task submit --request request.json
enzyme-harness task status TASK_ID
enzyme-harness task inspect TASK_ID
enzyme-harness task respond TASK_ID --response response.json
enzyme-harness task resume TASK_ID
enzyme-harness task stop TASK_ID
enzyme-harness task report TASK_ID
```

For a long process, invoke `enzyme-harness process run` from the task workspace. Declare the actual input files and outputs created by the command. The process receipt is stored separately from scientific outputs.

## Limits

The selected agent CLI must be installed and authenticated. dsh runs need a reachable model route and its key in the launch environment. Local GPU visibility and sandbox behavior depend on the host OS; `doctor` reports the detected bwrap usability and both agent versions. Each Skill may have separate Python dependencies, native software, model weights, credentials, or external service requirements. A `PASS` review supports that the requested computational workflow and evidence were checked; it does not establish experimental activity, predictive accuracy, or mechanism unless the task includes appropriate independent evidence.
