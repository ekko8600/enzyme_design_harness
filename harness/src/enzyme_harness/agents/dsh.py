"""DeepSeek Harness (dsh) backend: one-shot ``dsh headless`` calls.

Every task gets its own ``DSH_HOME`` with a task-owned ``headless`` profile, so
the user's interactive profile, plugins, and MCP servers never enter a call.
The model route, session store, and storage root are supplied per call through
a ``--patch`` overlay; the structured answer is the JSON object the agent prints
as its final message.
"""
from pathlib import Path
import json
import os
import shutil
import subprocess
import sys

import yaml

from ..files import load, write
from ..sandbox import bwrap_usable
from .base import AgentBackend, InvalidResponse

PROFILE = {'name': 'dsh-profile-headless', 'private': True, 'dependencies': {},
           'dsh': {'profile': {'bundles': ['@deepseek-ai/dsh-base', '@deepseek-ai/dsh-headless']}}}
PROFILE_PATCH = ('# Task-owned headless profile. enzyme-harness passes the model, session,\n'
                 '# and storage rows of each call with --patch overlays.\n[]\n')


def extract_json(text):
    """Return the first JSON object in a final message, tolerating fences/prose."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError('DSH_EMPTY_FINAL_MESSAGE')
    stripped = text.strip()
    try:
        value = json.loads(stripped)
        if isinstance(value, dict):
            return value
    except ValueError:
        pass
    decoder = json.JSONDecoder()
    for index, character in enumerate(text):
        if character != '{':
            continue
        try:
            value, _ = decoder.raw_decode(text[index:])
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    raise ValueError('DSH_FINAL_MESSAGE_NOT_JSON')


def normalize_usage(value):
    if not isinstance(value, dict):
        return None
    cached = (value.get('cacheReadTokens') or 0) + (value.get('cacheWriteTokens') or 0)
    return {'input_tokens': (value.get('inputTokens') or 0) + cached,
            'cached_input_tokens': cached,
            'output_tokens': value.get('outputTokens') or 0,
            'reasoning_output_tokens': value.get('reasoningTokens') or 0,
            'total_tokens': value.get('totalTokens')}


class DshBackend(AgentBackend):
    name = 'dsh'
    default_model = 'deepseek-flash'
    default_provider = 'deepseek-official'

    # -- executable ----------------------------------------------------
    @staticmethod
    def _node():
        node = shutil.which('node')
        if not node:
            raise RuntimeError('NODE_BINARY_NOT_FOUND: node is required to launch a dsh checkout')
        return node

    def _resolve(self, value):
        candidate = Path(str(value)).expanduser()
        if candidate.is_file():
            if candidate.suffix in {'.js', '.mjs'}:
                return [self._node(), str(candidate.resolve())]
            return [str(candidate.resolve())]
        if candidate.is_dir():
            entry = candidate / 'apps' / 'cli' / 'lib' / 'bin.js'
            if entry.is_file():
                return [self._node(), str(entry)]
            raise RuntimeError('DSH_BINARY_NOT_FOUND:' + str(candidate))
        found = shutil.which(str(candidate))
        if found:
            return [found]
        checkout = os.environ.get('DSH_CHECKOUT')
        if checkout:
            entry = Path(checkout).expanduser() / 'apps' / 'cli' / 'lib' / 'bin.js'
            if entry.is_file():
                return [self._node(), str(entry.resolve())]
        raise RuntimeError('DSH_BINARY_NOT_FOUND: install dsh on PATH, or configure agent_binary '
                           'with the executable, its bin.js, or the checkout directory')

    def command_prefix(self):
        value = self.request.get('agent_binary') or os.environ.get('DSH_BINARY') or 'dsh'
        return self._resolve(value)

    def version(self):
        return subprocess.run([*self.command_prefix(), '--version'], capture_output=True, text=True,
                              check=True).stdout.strip()

    # -- task-local state ----------------------------------------------
    def state_root(self):
        return self.task.path / 'dsh'

    def home(self):
        home = self.state_root() / 'home'
        profile = home / 'profiles' / 'headless'
        profile.mkdir(parents=True, exist_ok=True)
        write(profile / 'package.json', PROFILE)
        patch = profile / 'cordis.patch.yml'
        if not patch.exists():
            patch.write_text(PROFILE_PATCH)
        return home

    def _patch(self, role, folder):
        selection = {'provider': self.provider, 'model': self.model}
        if self.reasoning_effort:
            selection['reasoningEffort'] = self.reasoning_effort
        rows = [{'id': 'agent-default-model', 'config': selection},
                {'id': 'session-persistence-jsonl', 'config': {'root': str(self.state_root() / 'sessions')}},
                {'id': 'storage-json', 'config': {'root': str(self.state_root() / 'storages')}}]
        providers = self.request.get('dsh_providers')
        if isinstance(providers, dict) and providers:
            rows.append({'id': 'llm-pi-ai', 'config': {'providers': providers}})
        elif self.provider and self.provider != 'deepseek-official':
            # A catalog route needs no endpoint/protocol: pi-ai supplies those,
            # and an omitted apiKeyEnv falls back to the provider's ambient key.
            rows.append({'id': 'llm-pi-ai', 'config': {'providers': {self.provider: {}}}})
        path = Path(folder) / 'patch.yml'
        path.write_text(yaml.safe_dump(rows, sort_keys=False, allow_unicode=True))
        return path

    # -- one call ------------------------------------------------------
    def _permission(self, role):
        if role == 'main':
            # The outer mount namespace confines a full-access DSH, whose own
            # sandbox would otherwise hide CUDA devices.
            return 'danger-full-access'
        if role == 'review':
            return 'read-only' if self.sandbox_backend == 'auto' else 'danger-full-access'
        return 'workspace-write' if self.sandbox_backend == 'auto' else 'danger-full-access'

    def prepare(self, role, folder, work, prompt):
        folder = Path(folder); work = Path(work)
        home = self.home()
        patch = self._patch(role, folder)
        mode = self._permission(role)
        cmd = [*self.command_prefix(), '--profile', 'headless']
        extra = self.request.get('dsh_patch')
        if extra:
            path = Path(str(extra)).expanduser().resolve()
            if not path.is_file():
                raise ValueError('DSH_PATCH_NOT_FOUND:' + str(path))
            cmd += ['--patch', str(path)]
        cmd += ['--patch', str(patch), '--json', '--permission-mode', mode, '--approval-policy', 'never']
        session = load(self.session_file(), {}) if role == 'main' else {}
        if session.get('session_id'):
            cmd += ['--session-id', session['session_id']]
        cmd += ['-']

        environment = os.environ.copy()
        environment['ENZYME_HARNESS_ROOT'] = str(self.task.root)
        environment['ENZYME_HARNESS_TASK_ID'] = self.task.id
        environment['PATH'] = str(Path(sys.executable).parent) + os.pathsep + environment.get('PATH', '')
        environment['DSH_HOME'] = str(home)
        environment.pop('DSH_PROFILE', None)
        unconfined = self.sandbox_backend == 'none'
        if role == 'main':
            if self.sandbox_backend == 'auto' and not bwrap_usable():
                raise RuntimeError('BWRAP_UNAVAILABLE_USE_SANDBOX_BACKEND_NONE')
            temporary = self.task.path / 'workspace' / '.tmp'
            temporary.mkdir(exist_ok=True)
            environment['TMPDIR'] = str(temporary)
            if self.sandbox_backend == 'auto':
                state = self.state_root(); state.mkdir(parents=True, exist_ok=True)
                processes = self.task.path / 'processes'; processes.mkdir(parents=True, exist_ok=True)
                cmd = ['bwrap', '--ro-bind', '/', '/', '--dev-bind', '/dev', '/dev', '--proc', '/proc',
                       '--bind', str(work), str(work), '--bind', str(processes), str(processes),
                       '--bind', str(state), str(state), '--', *cmd]
        return {'command': cmd, 'environment': environment, 'cwd': str(work),
                'output_path': str(folder / 'output.json'), 'auth_directory': '',
                'sandbox': mode, 'unconfined': unconfined}

    def prompt_suffix(self, role, folder):
        schema = load(Path(folder) / 'schema.json')
        return ('\nReturn your final answer as exactly one JSON object that satisfies this JSON Schema:\n'
                + json.dumps(schema, ensure_ascii=False) + '\nPrint the object itself, with every required key, '
                'no Markdown fence and no prose outside it.\n')

    # -- records -------------------------------------------------------
    def session(self, folder):
        events = self.events(folder)
        for event in events:
            if event.get('type') == 'session' and event.get('sessionId'):
                return str(event['sessionId'])
        for event in reversed(events):
            if event.get('type') == 'final' and event.get('sessionId'):
                return str(event['sessionId'])
        return None

    def terminal(self, folder):
        for event in self.events(folder):
            if event.get('type') == 'final':
                reason = event.get('reason')
                return isinstance(reason, dict) and reason.get('kind') == 'completed'
        return False

    def response(self, folder):
        folder = Path(folder)
        finals = [event for event in self.events(folder) if event.get('type') == 'final']
        if not finals:
            final = load(folder / 'final.json')
            if not isinstance(final, dict):
                return None
        else:
            final = finals[-1]
            write(folder / 'final.json', final)
        reason = final.get('reason')
        if not isinstance(reason, dict) or reason.get('kind') != 'completed':
            return None
        text = final.get('text') or ''
        try:
            return extract_json(text)
        except ValueError as exc:
            raise InvalidResponse(str(exc), text=str(text), folder=folder) from None

    def usage(self, folder):
        total = None
        for event in self.events(folder):
            if event.get('type') != 'status' or event.get('phase') != 'step_end':
                continue
            part = normalize_usage(event.get('usage'))
            if part is None:
                continue
            if total is None:
                total = dict(part)
            else:
                for key in ('input_tokens', 'cached_input_tokens', 'output_tokens', 'reasoning_output_tokens'):
                    total[key] += part[key]
                if part['total_tokens'] is None:
                    total['total_tokens'] = None
                elif total['total_tokens'] is not None:
                    total['total_tokens'] += part['total_tokens']
        return total

    def tool_counts(self, folder):
        kinds = {}
        for event in self.events(folder):
            if event.get('type') == 'tool_call':
                kind = str(event.get('tool') or 'tool')
                kinds[kind] = kinds.get(kind, 0) + 1
        return kinds

    def outstanding_commands(self, folder):
        events = self.events(folder)
        completed = {event.get('callId') for event in events if event.get('type') == 'tool_result'}
        pending = []
        for event in events:
            if event.get('type') != 'tool_call' or event.get('tool') != 'bash':
                continue
            if event.get('callId') in completed:
                continue
            payload = event.get('input') or {}
            command = payload.get('command') if isinstance(payload, dict) else ''
            pending.append({'id': event.get('callId'), 'command': str(command or '')})
        return pending

