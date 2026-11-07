"""Public command line for the new file-based harness only."""
from pathlib import Path
import argparse
import json
import os
import subprocess
import sys
from .agents import BACKENDS, DEFAULT_MODELS, DEFAULT_PROVIDERS
from .agents.base import records_for
from .files import alive, digest, identity, load, sha, terminate, write
from .store import DEFAULT_ROOT, Task, is_native, selection


def status_view(task):
    state=task.state()
    if is_native(state):
        from .native import managed
        from .native_report import token_usage
        request=task.request()
        return {**state,'activity':{'task_directory':str(task.path),'agent':request.get('agent') or 'codex',
                 'model':request.get('model'),'provider':request.get('provider'),'processes':managed(task),
                 'main_session':load(records_for(task)/'main-session.json'),
                 'report':str(task.path/'reports/report.html') if (task.path/'reports/report.html').exists() else None,
                 'token_usage':token_usage(task)}}
    return {**state,'activity':{'task_directory':str(task.path),'legacy_read_only':True,
             'report':str(task.path/'reports/report.html') if (task.path/'reports/report.html').exists() else None}}

def start(task,foreground):
    if foreground:
        from .native import run
        result=run(task)
        if result['status']=='RESTART_REQUIRED':
            write(task.root/'handoff.json',{'task_id':task.id})
        return {'task_id':task.id,'directory':str(task.path),'state':result}
    command=[sys.executable,'-m','enzyme_harness.launcher','--root',str(task.root),'task','resume',task.id,'--foreground']
    with (task.path/'controller.log').open('ab') as log:
        process=subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
    return {'task_id':task.id,'directory':str(task.path),'controller':identity(process.pid)}

def respond(task,response):
    state=task.state()
    if not is_native(state):raise ValueError('LEGACY_TASK_READ_ONLY: submit a new task from its request')
    if alive(state.get('controller')):raise RuntimeError('TASK_STILL_RUNNING')
    if state.get('status')!='WAITING_INPUT':raise ValueError('TASK_NOT_WAITING_FOR_INPUT')
    if not isinstance(response,dict):raise ValueError('RESPONSE_OBJECT_REQUIRED')
    if response.get('inputs'):
        import shutil
        from .store import identifier
        request=task.request();version=state['revision']+1
        write(task.path/'requests'/f'{version}-previous.json',request)
        for name,source in response['inputs'].items():
            identifier(name);source=Path(source).absolute();before=sha(source)
            target=task.path/'inputs'/f'v{version}'/name/source.name;target.parent.mkdir(parents=True)
            if source.is_dir():shutil.copytree(source,target)
            else:shutil.copy2(source,target)
            if sha(source)!=before or sha(target)!=before:raise ValueError('INPUT_CHANGED_DURING_SNAPSHOT')
            request['inputs'][name]={'path':str(target),'sha256':before}
        write(task.path/'request.json',request)
    write(task.path/'response.json',response)
    task.update(status='READY',stage='main',blocker=None,feedback='User response is available in response.json.')

def doctor_view(root):
    from .release import runtime_identity
    from .sandbox import bwrap_usable
    import shutil
    config=load(root/'config.json',{})
    selected=config.get('agent') or 'codex'
    result={'runtime':runtime_identity(),'config':config,'agent':selected,
            'sandbox_backend':config.get('sandbox_backend') or 'auto',
            'bwrap':shutil.which('bwrap'),'bwrap_usable':bwrap_usable(),'agents':{}}
    for name in BACKENDS:
        request={'agent':name,'model':DEFAULT_MODELS[name],
                 'agent_binary':config.get('agent_binary') if selected==name else None,
                 'agent_version':config.get('agent_version') if selected==name else None}
        probe=type('Probe',(),{'request':staticmethod(lambda request=request: request)})()
        entry={'selected':selected==name,'pinned_version':None,'binary':None,'version':None,'ok':False}
        try:
            backend=BACKENDS[name](probe); entry['pinned_version']=backend.version_pin
            entry['binary']=' '.join(backend.command_prefix()); entry['version']=backend.version()
            entry['ok']=bool(entry['version']) and (entry['pinned_version'] is None
                                                    or entry['version']==entry['pinned_version'])
        except Exception as exc:
            entry['error']=str(exc)
        result['agents'][name]=entry
    return result

def main(argv=None):
    parser=argparse.ArgumentParser(description='Native agent task harness (Codex CLI or DeepSeek Harness)')
    parser.add_argument('--root',default=os.environ.get('ENZYME_HARNESS_ROOT',str(DEFAULT_ROOT)));parser.add_argument('--json',action='store_true')
    commands=parser.add_subparsers(dest='command',required=True)
    setup=commands.add_parser('setup')
    setup.add_argument('--agent',choices=sorted(BACKENDS),default=None,help='agent runtime; default codex')
    setup.add_argument('--model',default=None,help="model id; agent defaults: codex '%s', dsh '%s'"%(DEFAULT_MODELS['codex'],DEFAULT_MODELS['dsh']))
    setup.add_argument('--provider',default=None,help="dsh model route (default '%s')"%DEFAULT_PROVIDERS['dsh'])
    setup.add_argument('--reasoning-effort',default='high',help="adapter effort; pass an empty string to omit")
    setup.add_argument('--sandbox-backend',choices=['auto','none'],default=None,help='auto requires a working OS sandbox; none runs unconfined after artifact checks')
    setup.add_argument('--agent-binary',default=None,help='agent executable, bin.js, or checkout directory')
    setup.add_argument('--codex-binary',default=None,help='legacy alias for --agent-binary with --agent codex')
    setup.add_argument('--agent-version',default=None,help='optional exact version to require; default accepts the installed CLI')
    setup.add_argument('--dsh-providers',default=None,help='JSON llm-pi-ai providers dictionary for dsh')
    setup.add_argument('--dsh-patch',default=None,help='extra dsh --patch overlay applied before harness rows')
    setup.add_argument('--python',default=sys.executable);setup.add_argument('--environments',help='JSON name-to-python mapping');setup.add_argument('--resources-path');setup.add_argument('--budget',help='JSON explicit limits')
    doctor=commands.add_parser('doctor');doctor.add_argument('--skill');doctor.add_argument('--operation',default='default')
    process=commands.add_parser('process').add_subparsers(dest='process_action',required=True)
    for action in ('run','status','stop'):
        proc=process.add_parser(action);proc.add_argument('--task',default=os.environ.get('ENZYME_HARNESS_TASK_ID'));proc.add_argument('--id',required=True)
        if action=='run':
            proc.add_argument('--cwd',default='.');proc.add_argument('--input',action='append',default=[],help='Existing input file or directory to hash before and after execution.');proc.add_argument('--output',action='append',default=[],help='File or directory the command itself will create, relative to the task workspace; the supervisor receipt is stored separately.');proc.add_argument('argv',nargs=argparse.REMAINDER,help='Command and arguments following --')
    sub=commands.add_parser('task').add_subparsers(dest='action',required=True)
    submit=sub.add_parser('submit');submit.add_argument('--request',required=True);submit.add_argument('--foreground',action='store_true');submit.add_argument('--idempotency-key');submit.add_argument('--prepare-only',action='store_true')
    for action in ('status','inspect','respond','resume','stop','report'):
        command=sub.add_parser(action);command.add_argument('task_id')
        if action=='respond':command.add_argument('--response',required=True)
        if action=='resume':command.add_argument('--foreground',action='store_true')
        if action=='stop':command.add_argument('--reason',default='USER_STOPPED')
    args=parser.parse_args(argv);root=Path(args.root).expanduser().resolve()
    try:
        if args.command=='setup':
            if args.agent_binary and args.codex_binary and args.agent_binary!=args.codex_binary:
                raise ValueError('AGENT_BINARY_CONFLICT')
            explicit={'agent':args.agent,'model':args.model,'reasoning_effort':args.reasoning_effort,
                      'sandbox_backend':args.sandbox_backend,'agent_binary':args.agent_binary or args.codex_binary,
                      'agent_version':args.agent_version}
            if args.provider is not None: explicit['provider']=args.provider
            if args.dsh_providers is not None: explicit['dsh_providers']=json.loads(args.dsh_providers)
            if args.dsh_patch is not None: explicit['dsh_patch']=args.dsh_patch
            value={**selection({},explicit),'python':str(Path(args.python).absolute()),
                   'environments':json.loads(args.environments or '{}'),'budget':json.loads(args.budget or '{}')}
            if args.resources_path:value['resources_path']=str(Path(args.resources_path).resolve())
            write(root/'config.json',value);result={'config':str(root/'config.json'),**value}
        elif args.command=='doctor':
            result=doctor_view(root)
            if args.skill:
                from enzyme_core.api import doctor as core_doctor
                result['skill']=core_doctor(args.skill,args.operation)
        elif args.command=='process':
            if not args.task:raise ValueError('PROCESS_TASK_REQUIRED')
            task=Task(root,args.task)
            if not is_native(task.state()):raise ValueError('LEGACY_TASK_READ_ONLY')
            from .native_process import start as process_start, wait as process_wait, inspect as process_inspect, stop as process_stop
            if args.process_action=='run':
                argv=args.argv[1:] if args.argv and args.argv[0]=='--' else args.argv
                process_start(task,args.id,argv,args.cwd,args.input,args.output);result=process_wait(task,args.id)
            elif args.process_action=='status':result=process_inspect(task,args.id)
            else:result=process_stop(task,args.id)
        elif args.action=='submit':
            request=load(args.request)
            if not isinstance(request,dict):raise ValueError('REQUEST_REQUIRED')
            if args.idempotency_key:
                from .files import lock
                key=digest(args.idempotency_key)
                with lock(root/'submissions'/f'{key}.lock'):
                    prior=load(root/'submissions'/f'{key}.json');fingerprint=digest({'request':request,'defaults':load(root/'config.json',{}),'inputs':{name:sha(path) for name,path in request.get('inputs',{}).items()}})
                    if prior:
                        if prior['request_sha256']!=fingerprint:raise ValueError('IDEMPOTENCY_KEY_CONFLICT')
                        task=Task(root,prior['task_id'])
                    else:
                        task=Task.create(root,request);write(root/'submissions'/f'{key}.json',{'task_id':task.id,'request_sha256':fingerprint})
            else:task=Task.create(root,request)
            result={'task_id':task.id,'directory':str(task.path),'state':task.state()} if args.prepare_only else start(task,args.foreground)
        else:
            task=Task(root,args.task_id)
            if args.action=='status':result=status_view(task)
            elif args.action=='inspect':
                if is_native(task.state()):result={'request':task.request(),'state':task.state(),'activity':status_view(task)['activity']}
                else:result={'request':task.request(),'state':task.state(),'task_directory':str(task.path),'legacy_read_only':True}
            elif args.action=='respond':respond(task,load(args.response));result=start(task,False)
            elif args.action=='resume':
                if not is_native(task.state()):raise ValueError('LEGACY_TASK_READ_ONLY: submit a new task from its request')
                if task.state()['stop_requested']:
                    from .native import managed
                    if any(p['status'] in {'UNKNOWN','RUNNING'} for p in managed(task)):raise RuntimeError('STOPPED_ATTEMPT_REQUIRES_RECONCILIATION')
                    task.update(stop_requested=False,status='READY',epoch=task.state().get('epoch',0)+1)
                result=start(task,args.foreground)
            elif args.action=='stop':
                if not is_native(task.state()):raise ValueError('LEGACY_TASK_READ_ONLY')
                task.update(stop_requested=True,status='STOPPED',stop_reason=args.reason)
                from .native_process import stop as process_stop
                for p in (task.path/'processes').glob('*/invocation.json'):process_stop(task,p.parent.name)
                for folder in records_for(task).glob('*'):
                    terminate(load(folder/'process.json',{}).get('identity'))
                result=task.state()
            else:
                if is_native(task.state()):
                    from .native_report import render
                    result={'report':render(task)}
                else:
                    existing=task.path/'reports/report.html'
                    if existing.is_file():result={'report':str(existing)}
                    else:
                        import html
                        destination=task.root/'legacy_reports'/task.id/'report.html';destination.parent.mkdir(parents=True,exist_ok=True)
                        data={'request':task.request(),'state':task.state(),'original_task_directory':str(task.path)}
                        destination.write_text('<!doctype html><html lang="zh"><meta charset="utf-8"><title>历史任务</title><h1>历史任务（只读）</h1><pre>'+html.escape(json.dumps(data,ensure_ascii=False,indent=2))+'</pre></html>')
                        result={'report':str(destination),'legacy_read_only':True}
        print(json.dumps(result,ensure_ascii=False,indent=2))
        if args.command=='process' and args.process_action=='run' and result['status']!='COMPLETED':return 1
        return 75 if isinstance(result,dict) and result.get('state',{}).get('status')=='RESTART_REQUIRED' else 0
    except Exception as exc:
        print(json.dumps({'error':str(exc)},ensure_ascii=False),file=sys.stderr);return 1


if __name__=='__main__':raise SystemExit(main())
