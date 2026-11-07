"""Shared contract for the native agent backends.

A backend owns everything the task controller cannot share between CLI agents:
which executable and version it accepts, how one non-interactive call is
composed (argv, environment, session identity), and how that call's records are
interpreted (terminal turn, token usage, trailing shell commands, and the
structured final response).
"""
from pathlib import Path
import json
import re

from ..files import load, write

AGENTS = ('codex', 'dsh')
DEFAULT_MODELS = {'codex': 'gpt-6-luna', 'dsh': 'deepseek-flash'}
DEFAULT_PROVIDERS = {'codex': None, 'dsh': 'deepseek-official'}
_PROVIDER = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}')
_MODEL = re.compile(r'[^\s-][^\s]{0,199}')
_SESSION = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,199}')


class InvalidResponse(RuntimeError):
    """A completed call whose final answer is not a usable structured response."""

    def __init__(self, reason, *, text='', folder=None):
        super().__init__(reason)
        self.reason = reason
        self.text = text
        self.folder = folder


def records_for(task):
    """The task directory holding one record folder per agent call.

    Tasks created before agents were selectable keep their historical ``codex``
    directory; every task carrying an ``agent`` field writes to ``calls``.
    """
    return task.path / ('calls' if task.state().get('agent') else 'codex')


def model_name(value):
    if not isinstance(value, str) or not _MODEL.fullmatch(value):
        raise ValueError('MODEL_INVALID')
    return value


def provider_name(value):
    if not isinstance(value, str) or not _PROVIDER.fullmatch(value):
        raise ValueError('PROVIDER_INVALID')
    return value


def session_name(value):
    """Validate a session identity before it is ever passed back as argv."""
    if not isinstance(value, str) or not _SESSION.fullmatch(value):
        raise RuntimeError('AGENT_SESSION_ID_INVALID:' + str(value))
    return value


class AgentBackend:
    """One CLI agent, bound to one task request."""

    name = ''
    default_model = ''
    default_provider = None

    def __init__(self, task):
        self.task = task
        self.request = task.request()

    # -- selection -----------------------------------------------------
    @property
    def model(self):
        value = self.request.get('model')
        if not isinstance(value, str) or not value.strip():
            raise ValueError('MODEL_REQUIRED: configure explicitly with setup or submit')
        return model_name(value)

    @property
    def provider(self):
        value = self.request.get('provider')
        if value is None:
            return self.default_provider
        return provider_name(value)

    @property
    def reasoning_effort(self):
        value = self.request.get('reasoning_effort')
        return value if isinstance(value, str) and value.strip() else None

    @property
    def sandbox_backend(self):
        value = self.request.get('sandbox_backend') or 'auto'
        if value not in {'auto', 'none'}:
            raise ValueError('SANDBOX_BACKEND_INVALID:' + str(value))
        return value

    @property
    def version_pin(self):
        """An optional exact version pin; unset accepts the installed CLI."""
        value = self.request.get('agent_version')
        return value if isinstance(value, str) and value.strip() else None

    # -- executable ----------------------------------------------------
    def command_prefix(self):
        """The argv prefix every call of this backend starts from."""
        raise NotImplementedError

    def version(self):
        raise NotImplementedError

    def verify(self):
        """Return the detected version, honoring only an explicit exact pin."""
        actual = self.version()
        if not actual:
            raise RuntimeError('AGENT_VERSION_UNKNOWN:' + self.name)
        pin = self.version_pin
        if pin is not None and actual != pin:
            raise RuntimeError('AGENT_VERSION_NOT_VERIFIED:%s:%s:expected:%s' % (self.name, actual, pin))
        return actual

    # -- task-local records --------------------------------------------
    def records(self):
        return records_for(self.task)

    def session_file(self):
        return self.records() / 'main-session.json'

    def events(self, folder):
        path = Path(folder) / 'events.jsonl'
        result = []
        if not path.exists():
            return result
        with path.open(errors='replace') as stream:
            for line in stream:
                try:
                    value = json.loads(line)
                except ValueError:
                    continue
                if isinstance(value, dict):
                    result.append(value)
        return result

    def observe_session(self, folder):
        """Record the main session identity on first sight and never let it change."""
        value = self.session(folder)
        if value is None:
            return None
        value = session_name(value)
        path = self.session_file()
        old = load(path, {})
        if old and old.get('session_id') != value:
            raise RuntimeError('MAIN_SESSION_ID_CHANGED')
        if not old:
            write(path, {'session_id': value, 'agent': self.name})
        return value

    # -- one call ------------------------------------------------------
    def prepare(self, role, folder, work, prompt):
        """Compose one call; return argv, environment, and record paths."""
        raise NotImplementedError

    def finalize(self, folder):
        """Promote any staged answer after the process exits."""

    def cleanup(self, folder):
        """Remove per-call secrets or staging state."""

    def session(self, folder):
        """The agent's own session identity from this call, if any."""
        return None

    def terminal(self, folder):
        """Whether this call reached a completed terminal turn."""
        return False

    def response(self, folder):
        """The structured final response, or None when the call produced none."""
        value = load(Path(folder) / 'output.json')
        return value if isinstance(value, dict) else None

    def usage(self, folder):
        """Normalized token usage for this call, or None when unreported."""
        return None

    def tool_counts(self, folder):
        """Counts of completed tool actions by kind."""
        return {}

    def outstanding_commands(self, folder):
        """Shell commands started but never reported complete."""
        return []

    def prompt_suffix(self, role, folder):
        """Backend-specific final-answer instructions appended to the prompt."""
        return ''
