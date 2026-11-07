"""Codex CLI backend: non-interactive exec with schema-enforced answers."""
from pathlib import Path
import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid

from ..files import load
from ..sandbox import bwrap_usable
from .base import AgentBackend


class CodexBackend(AgentBackend):
    name = 'codex'
    default_model = 'gpt-6-luna'
    default_provider = None

    # -- executable ----------------------------------------------------
    def resolved_binary(self):
        value = self.request.get('agent_binary') or self.request.get('codex_binary') or 'codex'
        binary = shutil.which(str(Path(str(value)).expanduser()))
        if not binary:
            raise RuntimeError('CODEX_BINARY_NOT_FOUND')
        return binary

    def command_prefix(self):
        return [self.resolved_binary()]

    def version(self):
        binary = self.resolved_binary()
        return subprocess.run([binary, '--version'], capture_output=True, text=True, check=True).stdout.strip()

    # -- one call ------------------------------------------------------
    def prepare(self, role, folder, work, prompt):
        binary = self.resolved_binary()
        folder = Path(folder)
        cmd = [binary, 'exec', '--model', self.model,
               '--sandbox', 'read-only' if role == 'review' else 'workspace-write',
               '--skip-git-repo-check', '--json', '--output-schema', str(folder / 'schema.json'),
               '-o', str(folder / 'output.json'), '-C', str(work),
               '-c', 'approval_policy="never"']
        if self.reasoning_effort:
            cmd += ['-c', 'model_reasoning_effort=' + json.dumps(self.reasoning_effort)]
        # A configured external MCP server can write independently of the Codex
        # shell sandbox; reviews and repairs must not load the user's config.
        if role in {'review', 'repair'}:
            cmd += ['--ignore-user-config']
        if role == 'main':
            cmd += ['--add-dir', str(self.task.path / 'processes')]
        session = load(self.session_file(), {}) if role == 'main' else {}
        cmd += ['resume', session['session_id'], '-'] if session.get('session_id') else ['-']

        environment = os.environ.copy()
        environment['ENZYME_HARNESS_ROOT'] = str(self.task.root)
        environment['ENZYME_HARNESS_TASK_ID'] = self.task.id
        environment['PATH'] = str(Path(sys.executable).parent) + os.pathsep + environment.get('PATH', '')
        auth_directory = ''
        output_path = folder / 'output.json'
        sandbox = 'read-only' if role == 'review' else 'workspace-write'
        unconfined = False
        if role == 'main':
            # Codex's Linux workspace sandbox mounts a minimal /dev, hiding CUDA.
            # Confine the whole CLI with a read-only host mount and native GPU
            # devices; its own shell has full access only inside this namespace.
            if self.sandbox_backend == 'auto' and not bwrap_usable():
                raise RuntimeError('BWRAP_UNAVAILABLE_USE_SANDBOX_BACKEND_NONE')
            original = Path(environment.get('CODEX_HOME', str(Path.home() / '.codex')))
            private = Path(tempfile.mkdtemp(prefix='enzyme-codex-auth-')); private.chmod(0o700)
            auth_directory = str(private)
            for name in ('auth.json', 'config.toml'):
                if (original / name).is_file():
                    shutil.copy2(original / name, private / name)
            if (private / 'auth.json').exists():
                (private / 'auth.json').chmod(0o600)
            for name in ('skills', 'plugins', 'rules'):
                if (original / name).exists():
                    (private / name).symlink_to(original / name, target_is_directory=(original / name).is_dir())
            sessions = self.task.root / 'sessions' / self.task.id / 'main'
            sessions.mkdir(parents=True, exist_ok=True)
            (private / 'sessions').symlink_to(sessions, target_is_directory=True)
            environment['CODEX_HOME'] = str(private)
            temporary = self.task.path / 'workspace' / '.tmp'
            temporary.mkdir(exist_ok=True)
            environment['TMPDIR'] = str(temporary)
            # The answer is staged in the workspace; traces stay outside the
            # writable mount.
            staging = self.task.path / 'workspace' / '.codex-output'
            staging.mkdir(exist_ok=True)
            output_path = staging / (folder.name + '.json')
            sandbox = 'danger-full-access'
            cmd[cmd.index('--sandbox') + 1] = sandbox
            cmd[cmd.index('-o') + 1] = str(output_path)
            # --add-dir is unnecessary inside the enclosing mount namespace.
            index = cmd.index('--add-dir'); del cmd[index:index + 2]
            if self.sandbox_backend == 'auto':
                cmd = ['bwrap', '--ro-bind', '/', '/', '--dev-bind', '/dev', '/dev', '--proc', '/proc',
                       '--bind', str(work), str(work),
                       '--bind', str(self.task.path / 'processes'), str(self.task.path / 'processes'),
                       '--bind', str(private), str(private), '--bind', str(sessions), str(sessions),
                       '--', *cmd]
            else:
                # The host has no usable OS sandbox; run unconfined and record it.
                unconfined = True
        elif self.sandbox_backend == 'none':
            # No usable Codex sandbox on this host either; the prompt constrains
            # the role and the controller verifies artifacts afterwards.
            sandbox = 'danger-full-access'
            cmd[cmd.index('--sandbox') + 1] = sandbox
            unconfined = True
        return {'command': cmd, 'environment': environment, 'cwd': None, 'output_path': str(output_path),
                'auth_directory': auth_directory, 'sandbox': sandbox, 'unconfined': unconfined}

    def finalize(self, folder):
        folder = Path(folder)
        spec = load(folder / 'bootstrap.json', {})
        staged = Path(spec.get('output_path', str(folder / 'output.json')))
        if staged != folder / 'output.json' and staged.exists() and not (folder / 'output.json').exists():
            (folder / 'output.json').write_bytes(staged.read_bytes())

    def cleanup(self, folder):
        auth = load(Path(folder) / 'bootstrap.json', {}).get('auth_directory')
        if not auth:
            return
        target = Path(auth)
        if target.parent == Path(tempfile.gettempdir()) and target.name.startswith('enzyme-codex-auth-') and target.is_dir():
            shutil.rmtree(target)

    # -- records -------------------------------------------------------
    def session(self, folder):
        for event in self.events(folder):
            if event.get('type') == 'thread.started':
                sid = event.get('thread_id')
                uuid.UUID(str(sid))
                return str(sid)
        return None

    def terminal(self, folder):
        return any(event.get('type') == 'turn.completed' for event in self.events(folder))

    def usage(self, folder):
        usage = None
        for event in self.events(folder):
            if event.get('type') == 'turn.completed' and isinstance(event.get('usage'), dict):
                usage = event['usage']
        return usage

    def tool_counts(self, folder):
        kinds = {}
        for event in self.events(folder):
            item = event.get('item', {})
            if event.get('type') == 'item.completed' and item.get('type') in {
                    'command_execution', 'file_change', 'mcp_tool_call', 'web_search'}:
                kinds[item['type']] = kinds.get(item['type'], 0) + 1
        return kinds

    def outstanding_commands(self, folder):
        pending = {}
        for event in self.events(folder):
            item = event.get('item', {})
            if item.get('type') != 'command_execution':
                continue
            if event.get('type') == 'item.started':
                pending[item.get('id')] = {'id': item.get('id'), 'command': str(item.get('command', ''))}
            if event.get('type') == 'item.completed':
                pending.pop(item.get('id'), None)
        return list(pending.values())

    def response(self, folder):
        return super().response(folder)

