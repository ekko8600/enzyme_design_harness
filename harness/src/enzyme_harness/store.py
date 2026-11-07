"""One authoritative state.json. Attempt receipts are independently durable."""
from pathlib import Path
import copy
import os
import re
import shutil
import sys
import time
import uuid
from .files import alive, append, digest, inside, load, lock, sha, write

DEFAULT_ROOT = Path.home()/'.local/share/enzyme-harness'
STAGES = ('main','review')
NATIVE_WORKFLOWS = ('codex-native/v1','native/v2')


def is_native(state):
    """Whether a task runs the native controller (including pre-agent tasks)."""
    return state.get('workflow') in NATIVE_WORKFLOWS


def interpret_budget(limits):
    """Explain the existing field-specific sentinel values without changing them."""
    units={'max_codex_calls':'calls','max_scientific_runs':'registered_skill_runs',
           'download_bytes':'bytes','max_storage_bytes':'bytes',
           'max_repairs_per_issue':'candidates_per_issue','max_repairs':'candidates',
           'max_science_seconds':'seconds'}
    zero_is_unlimited={'max_storage_bytes','max_science_seconds'}
    result={}
    for name,unit in units.items():
        if name not in limits:continue
        value=limits[name]
        if value is None or name in zero_is_unlimited and value==0:
            mode='UNLIMITED'
        elif value==0:
            mode='ZERO_ALLOWANCE'
        else:
            mode='LIMITED'
        result[name]={'configured':value,'mode':mode,'unit':unit}
    return result


def identifier(value):
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}',value): raise ValueError('IDENTIFIER_INVALID')
    return value


def selection(config, explicit):
    """Resolve the agent, model route, and launch facts for one task.

    An explicit request wins. A request that only switches the agent falls back
    to that agent's built-in default rather than inheriting the other agent's
    configured model.
    """
    from .agents import AGENTS, DEFAULT_MODELS, DEFAULT_PROVIDERS
    from .agents.base import model_name, provider_name
    agent = explicit.get('agent') or config.get('agent') or 'codex'
    if agent not in AGENTS: raise ValueError('AGENT_UNSUPPORTED:'+str(agent))
    same_agent = agent == (config.get('agent') or 'codex')
    model = explicit.get('model')
    if model is None and same_agent: model = config.get('model')
    if model is None: model = DEFAULT_MODELS[agent]
    model = model_name(model)
    if agent == 'codex':
        if explicit.get('provider') is not None: raise ValueError('PROVIDER_REQUIRES_AGENT:dsh')
        provider = None
    else:
        provider = explicit.get('provider')
        if provider is None and same_agent: provider = config.get('provider')
        provider = provider_name(provider or DEFAULT_PROVIDERS['dsh'])
    effort = explicit['reasoning_effort'] if 'reasoning_effort' in explicit else config.get('reasoning_effort','high')
    if isinstance(effort,str) and not effort.strip():
        effort = None  # an explicit empty string omits the adapter effort
    elif effort is not None:
        if not isinstance(effort,str) or len(effort)>64: raise ValueError('REASONING_EFFORT_INVALID')
        effort = effort.strip()
    sandbox = explicit.get('sandbox_backend') or config.get('sandbox_backend') or 'auto'
    if sandbox not in {'auto','none'}: raise ValueError('SANDBOX_BACKEND_INVALID:'+str(sandbox))
    # Launch facts configured for another agent must not leak into this task.
    configured = (lambda key: config.get(key)) if same_agent else (lambda key: None)
    version = explicit.get('agent_version')
    if version is None: version = configured('agent_version')
    if version is not None and (not isinstance(version,str) or not version.strip()): raise ValueError('AGENT_VERSION_INVALID')
    binary = explicit.get('agent_binary')
    if binary is None and agent == 'codex': binary = explicit.get('codex_binary')
    if binary is None: binary = configured('agent_binary')
    if binary is None and agent == 'codex': binary = configured('codex_binary')
    binary = binary or agent
    if not isinstance(binary,str) or not binary.strip(): raise ValueError('AGENT_BINARY_INVALID')
    explicit_providers = explicit.get('dsh_providers') is not None
    explicit_patch = explicit.get('dsh_patch') is not None
    if agent != 'dsh':
        if explicit_providers: raise ValueError('DSH_PROVIDERS_REQUIRES_AGENT:dsh')
        if explicit_patch: raise ValueError('DSH_PATCH_REQUIRES_AGENT:dsh')
        providers = None; patch = None
    else:
        providers = explicit.get('dsh_providers') if explicit_providers else config.get('dsh_providers')
        patch = explicit.get('dsh_patch') if explicit_patch else config.get('dsh_patch')
        if providers is not None and (not isinstance(providers,dict)
                                      or not all(isinstance(k,str) and k and isinstance(v,dict) for k,v in providers.items())):
            raise ValueError('DSH_PROVIDERS_INVALID')
        if patch is not None and (not isinstance(patch,str) or not patch.strip()): raise ValueError('DSH_PATCH_INVALID')
    return {'agent':agent,'model':model,'provider':provider,'reasoning_effort':effort,
            'sandbox_backend':sandbox,'agent_version':version,'agent_binary':binary,
            'dsh_providers':providers,'dsh_patch':patch}


class Task:
    def __init__(self, root, tid):
        self.root=Path(root).resolve(); self.id=identifier(tid); self.path=self.root/'tasks'/tid
        if not (self.path/'state.json').is_file(): raise ValueError('TASK_NOT_FOUND:'+tid)

    @classmethod
    def create(cls, root, request):
        root=Path(root).resolve(); config=load(root/'config.json', {})
        explicit=copy.deepcopy(request);request={**config,**explicit}
        for field in ('budget','environments'):request[field]={**config.get(field,{}),**explicit.get(field,{})}
        if request.get('schema_version','enzyme-harness/request/v1')!='enzyme-harness/request/v1':raise ValueError('REQUEST_SCHEMA_UNSUPPORTED')
        chosen=selection(config,explicit)
        if not isinstance(request.get('goal'),str) or not request['goal'].strip():raise ValueError('GOAL_REQUIRED')
        request.setdefault('gpu_policy','required_if_supported')
        if request['gpu_policy'] not in {'required_if_supported','allow_cpu'}:raise ValueError('GPU_POLICY_INVALID')
        budget={'max_codex_calls':None,'max_scientific_runs':None,'download_bytes':None,'max_storage_bytes':0,
                'max_repairs_per_issue':None,'max_repairs':None,'max_science_seconds':0,**request.get('budget',{})}
        if any(v is not None and (not isinstance(v,int) or isinstance(v,bool) or v<0) for v in budget.values()):raise ValueError('BUDGET_INVALID')
        # Native shell execution cannot account for all subprocesses or downloads.
        # Refuse explicit limits we cannot enforce instead of silently ignoring them.
        unsupported=[key for key in ('max_scientific_runs','download_bytes','max_repairs_per_issue','max_repairs') if budget[key] is not None]
        unsupported += [key for key in ('max_storage_bytes','max_science_seconds') if budget[key] not in (None,0)]
        if unsupported:raise ValueError('NATIVE_BUDGET_UNSUPPORTED:'+','.join(unsupported))
        # Check storage before hashing or copying potentially large inputs.
        sources={};total=0
        for name,value in request.get('inputs',{}).items():
            identifier(name);source=Path(value).absolute()
            if source.is_symlink():raise ValueError('INPUT_SYMLINK_FORBIDDEN')
            if not source.exists():raise FileNotFoundError(source)
            sources[name]=source
            if budget['max_storage_bytes']:
                if source.is_dir():
                    for item in source.rglob('*'):
                        if item.is_symlink():raise ValueError('SYMLINK_FORBIDDEN:'+str(item))
                        if item.is_file():total+=item.stat().st_size
                else:total+=source.stat().st_size
                if total>budget['max_storage_bytes']:raise RuntimeError('STORAGE_BUDGET_EXHAUSTED:input_snapshot_bytes='+str(total))
        snapshots={name:(source,sha(source)) for name,source in sources.items()}
        tid=uuid.uuid4().hex; directory=root/'tasks'/tid; directory.mkdir(parents=True)
        inputs={}
        for name,(source,before) in snapshots.items():
            target=directory/'inputs'/name/source.name
            target.parent.mkdir(parents=True)
            if source.is_dir():shutil.copytree(source,target,symlinks=True)
            else:shutil.copyfile(source,target)
            if sha(target)!=before or sha(source)!=before:raise ValueError('INPUT_CHANGED_DURING_SNAPSHOT')
            inputs[name]={'path':str(target),'sha256':before}
        from enzyme_core.support.config import load_config
        resources=load_config(base_path=Path(request['resources_path'])) if request.get('resources_path') else load_config()
        write(directory/'resources.json',resources)
        request.update(schema_version='enzyme-harness/request/v1',inputs=inputs,budget=budget,
                       python=request.get('python') or sys.executable,**chosen)
        if not request['model']:raise ValueError('MODEL_REQUIRED: configure explicitly with setup or submit')
        if chosen['agent'] != 'codex': request.pop('codex_binary',None)
        else: request['codex_binary']=chosen['agent_binary']
        write(directory/'request.json',request)
        from .release import runtime_identity
        state={'schema_version':'enzyme-harness/state/v1','revision':0,'epoch':0,'workflow':'native/v2',
               'agent':chosen['agent'],'stage':'main','status':'READY',
               'plan':None,'execution':None,'review':None,'calls':0,'scientific_runs':0,
               'download_bytes':0,'repairs':0,'issues':[],'stop_requested':False,'controller':None,
               'release':runtime_identity(),'created_at':time.time()}
        from .native_skills import snapshot
        snapshot(directory)
        write(directory/'state.json',state)
        return cls(root,tid)

    def state(self): return load(self.path/'state.json')
    def request(self): return load(self.path/'request.json')
    def update(self, expected=None, **changes):
        with lock(self.path/'.state.lock'):
            value=self.state()
            if expected is not None and value['revision']!=expected:raise RuntimeError('STATE_REVISION_CHANGED')
            value.update(changes); value['revision']+=1; write(self.path/'state.json',value)
        return value
    def event(self, kind, **data):append(self.path/'events.jsonl',{'kind':kind,**data})
    def verify_frozen_inputs(self):
        for name,value in self.request()['inputs'].items():
            if sha(value['path'])!=value['sha256']:raise ValueError('FROZEN_INPUT_CHANGED:'+name)
    def storage_bytes(self):
        roots=[self.path,self.root/'sessions'/self.id]
        for manifest in (self.root/'releases').glob('*/release.json'):
            if load(manifest,{}).get('owner_task_id')==self.id:roots.append(manifest.parent)
        return sum(p.stat().st_size for root in roots for p in root.rglob('*') if p.is_file())
    def reserve(self, kind):
        if kind != 'calls':raise ValueError('NATIVE_RESERVATION_UNSUPPORTED:'+kind)
        with lock(self.path/'.budget.lock'):
            state=self.state();limits=self.request()['budget'];cap=limits.get('max_codex_calls')
            if cap is not None and state.get('calls',0)>=cap:raise RuntimeError('BUDGET_EXHAUSTED:calls')
            self.update(expected=state['revision'],calls=state.get('calls',0)+1)

    def context(self):
        return {'task_id':self.id,'task_directory':str(self.path),'request':self.request(),
                'state':self.state(),'legacy_read_only':self.state().get('workflow')!='codex-native/v1'}
