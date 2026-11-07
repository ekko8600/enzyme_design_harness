"""Agent backends selectable per task: Codex CLI or DeepSeek Harness."""
from .base import AGENTS, DEFAULT_MODELS, DEFAULT_PROVIDERS, AgentBackend, InvalidResponse, records_for
from .codex import CodexBackend
from .dsh import DshBackend

BACKENDS = {'codex': CodexBackend, 'dsh': DshBackend}


def backend_for(task):
    """Resolve the backend recorded on the task, defaulting legacy tasks to Codex."""
    agent = task.request().get('agent') or task.state().get('agent') or 'codex'
    if agent not in BACKENDS:
        raise ValueError('AGENT_UNSUPPORTED:' + str(agent))
    return BACKENDS[agent](task)


__all__ = ['AGENTS', 'BACKENDS', 'DEFAULT_MODELS', 'DEFAULT_PROVIDERS', 'AgentBackend',
           'InvalidResponse', 'backend_for', 'records_for']
