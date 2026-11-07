"""Native agent task controller; scientific processes have independent receipts."""
from pathlib import Path
import json
import re
import shlex
import subprocess
import sys
import time
import uuid
from jsonschema import validate, ValidationError
from .agents import InvalidResponse, backend_for
from .files import alive, digest, identity, load, lock, sha, terminate, write
from .native_process import inspect as inspect_process
from .store import is_native

MAIN_SCHEMA = {'type': 'object', 'additionalProperties': False,
 'properties': {'status': {'enum': ['COMPLETED', 'WAIT_USER', 'FAILED']}, 'summary': {'type': 'string'},
                'artifacts': {'type': 'array', 'items': {'type': 'string', 'description': 'Exact existing path, not a Markdown link.'}},
                'limitations': {'type': 'array', 'items': {'type': 'string'}},
                'questions': {'type': 'array', 'items': {'type': 'string'}}},
 'required': ['status', 'summary', 'artifacts', 'limitations', 'questions']}
REVIEW_SCHEMA = {'type': 'object', 'additionalProperties': False,
 'properties': {'status': {'enum': ['PASS', 'REPAIR', 'INSUFFICIENT', 'WAIT_USER']},
                'summary': {'type': 'string'}, 'findings': {'type': 'array', 'items': {'type': 'string'}},
                'evidence': {'type': 'array', 'items': {'type': 'string', 'description': 'Exact existing file path, without explanation or Markdown.'}},
                'repair_request': {'type': 'string'}, 'limitations': {'type': 'array', 'items': {'type': 'string'}}},
 'required': ['status', 'summary', 'findings', 'evidence', 'repair_request', 'limitations']}
REPAIR_SCHEMA = {'type': 'object', 'additionalProperties': False,
 'properties': {'status': {'enum': ['FIXED', 'WAIT', 'UNRESOLVED']}, 'summary': {'type': 'string'},
                'diagnosis': {'type': 'string'},
                'evidence_paths': {'type': 'array', 'items': {'type': 'string', 'description': 'Exact existing task-local file paths only.'}},
                'resume_from': {'enum': ['main', 'review']}, 'changed_files': {'type': 'array', 'items': {'type': 'string'}}},
 'required': ['status', 'summary', 'diagnosis', 'evidence_paths', 'resume_from', 'changed_files']}
SCHEMAS = {'main': MAIN_SCHEMA, 'review': REVIEW_SCHEMA, 'repair': REPAIR_SCHEMA}
_OWNED = {}


def managed(task):
    return [inspect_process(task, p.parent.name) for p in sorted((task.path / 'processes').glob('*/invocation.json'))]


def _paths(task, paths):
    result = []
    for value in paths:
        match = re.fullmatch(r'\[[^\]]+\]\((?:<([^>]+)>|([^()]+))\)', value.strip())
        if match: value = match.group(1) or match.group(2)
        candidates = [value] + re.findall(r'`([^`]+)`', value)
        found = []
        for candidate in candidates:
            raw = Path(candidate)
            possible = [raw] if raw.is_absolute() else [task.path / raw, task.path / 'workspace' / raw]
            for item in possible:
                path = item.resolve()
                if task.path.resolve() in path.parents and path.exists():
                    found.append(path)
                    # Relative paths prefer task-root references, then workspace references.
                    break
        if not found:
            raise ValueError('TASK_ARTIFACT_MISSING_OR_OUTSIDE_TASK:' + value)
        for path in dict.fromkeys(found): result.append({'path': str(path), 'sha256': sha(path)})
    return result


def _path_hints(task, paths):
    """Suggest exact task-local alternatives without accepting or rewriting a bad path."""
    hints = []
    task_root = task.path.resolve()
    task_parent = task.root.resolve() / 'tasks'
    for value in paths:
        raw_value = str(value).strip()
        match = re.fullmatch(r'\[[^\]]+\]\((?:<([^>]+)>|([^()]+))\)', raw_value)
        if match: raw_value = match.group(1) or match.group(2)
        candidates = [raw_value] + re.findall(r'`([^`]+)`', raw_value)
        alternatives = []
        reference_is_valid = False
        for candidate in candidates:
            raw = Path(candidate)
            possible = [raw] if raw.is_absolute() else [task.path / raw, task.path / 'workspace' / raw]
            for item in possible:
                resolved = item.resolve()
                if task_root in resolved.parents and resolved.exists():
                    reference_is_valid = True
                    break
            if reference_is_valid:
                break
        if reference_is_valid:
            continue
        for candidate in candidates:
            raw = Path(candidate)
            if raw.is_absolute():
                try:
                    relative = raw.relative_to(task_parent)
                    parts = relative.parts
                    if len(parts) > 1:
                        task_relative = (task.path / Path(*parts[1:])).resolve()
                        if task_root in task_relative.parents and task_relative.exists(): alternatives.append(task_relative)
                except ValueError:
                    pass
                # Installed Core sources may have an identical task Skill snapshot.
                if 'enzyme_core' in raw.parts and 'skills' in raw.parts:
                    skill_root = task.path / 'workspace' / '.agents' / 'skills'
                    if skill_root.is_dir():
                        alternatives.extend(p.resolve() for p in skill_root.rglob(raw.name) if p.is_file())
        unique = []
        for path in alternatives:
            if path not in unique: unique.append(path)
        hints.append({'invalid_reference': raw_value,
                      'task_local_candidates': [
                          {'path': str(p), 'sha256': sha(p)} for p in unique[:5]
                      ]})
    return hints


def _prompt(task, role, agent, folder):
    request = task.request(); state = task.state()
    header = ('Use the native shell, file editing and available Skills. Work autonomously, inspect the actual inputs, '
      'preserve frozen inputs, record commands and save results under the task workspace. Before scientific calculation '
      'check GPU support; explicitly use an available GPU when supported, and explain CPU only if the method lacks GPU '
      'support or the user explicitly requests CPU. Do not silently fall back. For long computations run '
      '`enzyme-harness --root ' + shlex.quote(str(task.root)) + ' process run --id UNIQUE_ID --input INPUT --output OUTPUT -- COMMAND...` '
      'so the process and receipt survive interruption. --input and --output are optional repeatable declarations; '
      '--output names a file created by the command, not the supervisor receipt. '
      'Never overwrite recorded artifacts; write each revised computation or analysis into a new output path. '
      'In the request, max_science_seconds=0 and max_storage_bytes=0 mean unlimited; null budgets also mean unlimited. '
      'The task Skill snapshot is for discovery and reference: scripts may depend on the original Core package layout. '
      'Use the installed Core operation or the source path recorded in skills.json when a script needs that layout. '
      'Return exact existing paths in artifacts and evidence, not Markdown links. '
      'If feedback reports an artifact-path validation error and the scientific work is already complete, correct only the returned artifact list; do not rerun the science. Use supplied task_local_candidates when they match the cited evidence. '
      'Read the local request and current feedback as needed. Avoid repeated polling. '
      'Computational results do not prove experimental activity. Reply in Chinese.\n')
    details = {'task_directory': str(task.path), 'request': str(task.path / 'request.json'),
               'workspace': str(task.path / 'workspace'), 'gpu_policy': request.get('gpu_policy'),
               'feedback': state.get('feedback'), 'response': str(task.path / 'response.json') if (task.path / 'response.json').exists() else None}
    if role == 'review':
        header = ('Independently review the actual user request, computation commands, receipts, artifacts and scientific '
                  'claims using read-only access to the task. Give PASS only for supported completed work; REPAIR for concrete '
                  'engineering or execution problems, INSUFFICIENT for evidenced scientific limitations and WAIT_USER '
                  'for missing information. Verify the selected method used its supported GPU backend unless CPU was '
                  'explicitly requested; distinguish unsupported GPU from an unavailable GPU. '
                  'Put exact existing task-local paths only in evidence; put explanations in findings. When feedback includes task_local_candidates, use the matching path and verify its content. '
                  'Do not perform modifications. Reply in Chinese.\n')
        details['execution'] = state.get('execution')
    elif role == 'repair':
        header = ('Diagnose the failure using the task evidence and process records. Do not rerun confirmed scientific '
                  'computations. Work only in the candidate workspace. Report the root cause, exact task-local file paths in evidence_paths, and explanation in diagnosis. '
                  'For ordinary artifact or review-path errors, prefer a no-code correction and resume from the failing stage; do not change scientific files. '
                  'Report the concrete changes '
                  'and earliest recovery stage; do not claim a fix without actual verification. For code changes, write '
                  'reproduction.json in the candidate with {"argv":[...],"expected_error":"substring of the failure"}; '
                  'the actual command must fail on the installed code with that error and pass on the candidate. '
                  'The launcher is protected. Reply in Chinese.\n')
        details['failure'] = state.get('failure')
        details['candidate'] = state.get('repair_candidate')
    details['path_hints'] = state.get('path_hints', [])
    details['agent'] = {'name': agent.name, 'model': agent.model, 'provider': agent.provider,
                        'sandbox_backend': agent.sandbox_backend}
    return header + json.dumps(details, ensure_ascii=False) + agent.prompt_suffix(role, folder)


def _prepare(task, role):
    agent = backend_for(task)
    version = agent.verify()
    task.reserve('calls')
    number = task.state()['calls']
    folder = agent.records() / f'{number:04d}-{role}'
    folder.mkdir(parents=True)
    write(folder / 'schema.json', SCHEMAS[role])
    prompt = _prompt(task, role, agent, folder); (folder / 'prompt.txt').write_text(prompt)
    work = task.path / 'workspace'
    if role == 'repair':
        work = Path(task.state()['repair_candidate'])
    (task.path / 'processes').mkdir(exist_ok=True)
    launch = agent.prepare(role, folder, work, prompt)
    token = uuid.uuid4().hex
    spec = {'id': token, 'agent': agent.name, 'role': role, 'version': version,
            'auth_directory': launch.get('auth_directory', ''), 'output_path': str(launch['output_path']),
            'context_sha256': digest({'prompt': prompt}), 'controller': identity(), 'command': launch['command'],
            'sandbox': launch.get('sandbox'), 'unconfined': bool(launch.get('unconfined'))}
    write(folder / 'bootstrap.json', spec)
    environment = launch['environment']
    with (folder / 'prompt.txt').open('rb') as stdin, (folder / 'events.jsonl').open('wb') as stdout, (folder / 'stderr.log').open('wb') as stderr:
        process = subprocess.Popen([sys.executable, '-m', 'enzyme_harness.bootstrap', str(folder)],
                                   stdin=stdin, stdout=stdout, stderr=stderr, env=environment,
                                   cwd=launch.get('cwd') or None, start_new_session=True)
    _OWNED[process.pid] = process
    for _ in range(200):
        record = load(folder / 'process.json')
        if record: break
        if process.poll() is not None: raise RuntimeError(agent.name.upper() + '_BOOTSTRAP_FAILED')
        time.sleep(.05)
    else: raise RuntimeError(agent.name.upper() + '_BOOTSTRAP_UNKNOWN')
    child = record.get('identity')
    if not child or child['pid'] != process.pid: raise RuntimeError(agent.name.upper() + '_BOOTSTRAP_IDENTITY_MISMATCH')
    task.update(status='RUNNING', pending_native={'role': role, 'agent': agent.name, 'folder': str(folder), 'identity': child})
    write(folder / 'launch.allow', {'identity': child, 'bootstrap_sha256': sha(folder / 'bootstrap.json')})
    return folder, child


def _wait_call(task, role, folder, child):
    import psutil
    agent = backend_for(task)
    observed = {str(x['pid']): x for x in load(folder / 'observed-processes.json', []) if x}
    while alive(child):
        if role == 'main': agent.observe_session(folder)
        if task.state().get('stop_requested'): terminate(child)
        try:
            for descendant in psutil.Process(child['pid']).children(recursive=True):
                record = identity(descendant.pid)
                if record and str(descendant.pid) not in observed:
                    observed[str(descendant.pid)] = record
                    write(folder / 'observed-processes.json', list(observed.values()))
        except psutil.Error: pass
        time.sleep(.25)
    owned = _OWNED.pop(child['pid'], None)
    code = owned.wait() if owned is not None else None
    if role == 'main': agent.observe_session(folder)
    agent.finalize(folder)
    agent.cleanup(folder)
    terminal = agent.terminal(folder)
    response = agent.response(folder)
    if response is not None and not (folder / 'output.json').exists(): write(folder / 'output.json', response)
    prior = load(folder / 'receipt.json')
    if prior and prior.get('output_sha256') and (not response or prior['output_sha256'] != sha(folder / 'output.json')):
        raise RuntimeError('AGENT_RECEIPT_MISMATCH:' + str(folder))
    write(folder / 'receipt.json', {'schema_version': 'enzyme-harness/agent-receipt/v2', 'agent': agent.name,
          'role': role, 'terminal': terminal, 'returncode': code,
          'output_sha256': sha(folder / 'output.json') if response else None})
    if code not in (None, 0) or not terminal or response is None:
        raise RuntimeError(agent.name.upper() + '_CALL_INCOMPLETE:' + str(folder))
    try:
        validate(response, SCHEMAS[role])
    except ValidationError as exc:
        raise InvalidResponse('schema:' + exc.message, text=json.dumps(response, ensure_ascii=False), folder=folder) from None
    return response


def _response_feedback(task, exc):
    """Ask the same agent role once more for a well-formed structured answer."""
    pending = task.state().get('pending_native') or {}
    role = pending.get('role') or task.state().get('stage')
    task.update(pending_native=None)
    fingerprint = digest({'role': role, 'response': exc.reason})
    history = task.state().get('native_response_history', [])
    if history.count(fingerprint) >= 2:
        return False
    task.update(stage=role, status='READY', path_hints=[],
                feedback=('Your previous final answer was not a valid structured response (' + exc.reason + '). '
                          'Return exactly one JSON object matching the provided schema; do not change completed '
                          'scientific work or rerun the science.'),
                native_response_history=history + [fingerprint])
    return True


def _reconcile(task):
    pending = task.state().get('pending_native')
    agent = backend_for(task)
    if pending:
        folder = Path(pending['folder']); role = pending['role']
        if not alive(pending['identity']) and not agent.terminal(folder):
            processes = managed(task)
            while any(x['status'] == 'RUNNING' for x in processes):
                if task.state().get('stop_requested'): return None
                time.sleep(.5); processes = managed(task)
            if any(x['status'] == 'UNKNOWN' for x in processes): raise RuntimeError('UNKNOWN_PROCESS_REQUIRES_INSPECTION')
            known = {p.get('pid') for x in processes for p in x['process'].values() if isinstance(p, dict)}
            if any(alive(x) and x.get('pid') not in known for x in load(folder / 'observed-processes.json', [])):
                raise RuntimeError('UNKNOWN_NATIVE_DESCENDANT_REQUIRES_INSPECTION')
            def accounted(item):
                command = str(item.get('command', ''))
                if not re.search(r'\benzyme-harness\b.*\bprocess\s+run\b', command, re.S): return False
                match = re.search(r'(?:^|\s)--id(?:=|\s+)[\'"]?([A-Za-z0-9_.-]+)', command)
                return bool(match and any(p['process_id'] == match.group(1) for p in processes))
            if any(not accounted(item) for item in agent.outstanding_commands(folder)):
                raise RuntimeError('UNKNOWN_NATIVE_COMMAND_REQUIRES_INSPECTION')
            agent.cleanup(folder)
            task.update(pending_native=None)
        else:
            response = _wait_call(task, role, folder, pending['identity'])
            task.update(pending_native=None)
            return role, response, folder
    for item in managed(task):
        if item['status'] == 'UNKNOWN': raise RuntimeError('UNKNOWN_PROCESS_REQUIRES_INSPECTION:' + item['process_id'])
        if item['status'] == 'INVALIDATED': raise RuntimeError('PROCESS_ARTIFACT_CHANGED:' + item['process_id'])
    return None


def _render(task):
    from .native_report import render
    return render(task)


def _prepare_repair(task, error, stage):
    from .release import candidate_sources
    state = task.state(); fingerprint = digest({'stage': stage, 'error': error})
    history = state.get('repair_history', [])
    if history.count(fingerprint) >= 2: raise RuntimeError('REPAIR_NO_PROGRESS:' + error)
    failure = {'id': fingerprint[:24], 'error': error, 'stage': stage, 'task_directory': str(task.path)}
    task.update(repairs=state.get('repairs', 0) + 1, stage='repair', status='READY', failure=failure,
                repair_history=history + [fingerprint])
    candidate = candidate_sources(task, failure)
    write(candidate.parent / 'failure.json', failure)
    task.update(repair_candidate=str(candidate))


def _apply_repair(task, response):
    if response['status'] != 'FIXED':
        task.update(status='BLOCKED', blocker=response['summary']); return False
    _paths(task, response['evidence_paths'])
    from .release import changed_files, validate_candidate, build_release
    candidate = Path(task.state()['repair_candidate']); actual = changed_files(candidate)
    if sorted(response['changed_files']) != actual: raise ValueError('CANDIDATE_CHANGED_FILES_MISMATCH')
    target = None; pending_release = None
    if actual:
        validate_candidate(task, candidate)
        release = build_release(task, candidate, activate=False)
        target = release['runtime_identity']
        pending_release = {**release, 'manifest_sha256': sha(task.root/'releases'/release['id']/'release.json')}
    task.update(stage=response['resume_from'], status='RESTART_REQUIRED', feedback=response['summary'],
                target_release=target, pending_release=pending_release)
    if pending_release: write(task.root/'active-release.json', pending_release)
    return True


def _incomplete_folder(error):
    for prefix in ('CODEX_CALL_INCOMPLETE:', 'DSH_CALL_INCOMPLETE:'):
        if error.startswith(prefix):
            return Path(error.split(':', 1)[1])
    return None


def run(task):
    if not is_native(task.state()): raise ValueError('LEGACY_TASK_READ_ONLY: submit a new task from its request')
    with lock(task.root / '.controller.lock', blocking=False):
        state = task.state()
        if state['status'] in {'COMPLETED', 'INSUFFICIENT', 'WAITING_INPUT'}: return state
        from .release import runtime_identity
        current = runtime_identity()
        pending_release = state.get('pending_release')
        if pending_release:
            manifest=task.root/'releases'/pending_release['id']/'release.json'
            if not manifest.is_file() or sha(manifest)!=pending_release['manifest_sha256']:
                raise RuntimeError('PENDING_RELEASE_MANIFEST_CHANGED')
            active=load(task.root/'active-release.json',{})
            if active.get('id')!=pending_release['id']:
                write(task.root/'active-release.json',pending_release)
                return task.update(status='RESTART_REQUIRED')
            if current!=pending_release['runtime_identity']:
                return task.update(status='RESTART_REQUIRED')
        if state.get('release') != current:
            if state.get('target_release') != current: raise RuntimeError('RUNTIME_IDENTITY_CHANGED')
            binding=(pending_release or {}).get('environment_binding')
            if binding:
                from .environment import bind_request
                bind_request(task,binding['base_python'],binding['python'])
            task.update(release=current, target_release=None, pending_release=None, status='READY')
        task.update(controller=identity(), status='RUNNING', blocker=None)
        try:
            recovered = None
            try:
                recovered = _reconcile(task)
            except InvalidResponse as exc:
                if not _response_feedback(task, exc): raise
            while True:
                state = task.state()
                if state.get('stop_requested'): task.update(status='STOPPED'); break
                task.verify_frozen_inputs()
                if recovered:
                    role, response, folder = recovered; recovered = None
                else:
                    role = state['stage']; folder, child = _prepare(task, role)
                    try:
                        response = _wait_call(task, role, folder, child)
                    except InvalidResponse as exc:
                        if not _response_feedback(task, exc): raise
                        continue
                    task.update(pending_native=None)
                task.verify_frozen_inputs()
                active = managed(task)
                unknown = [x['process_id'] for x in active if x['status'] == 'UNKNOWN']
                if unknown: raise RuntimeError('UNKNOWN_PROCESS_REQUIRES_INSPECTION:' + ','.join(unknown))
                invalidated = [x['process_id'] for x in active if x['status'] == 'INVALIDATED']
                if invalidated: raise RuntimeError('PROCESS_ARTIFACT_CHANGED:' + ','.join(invalidated))
                if any(x['status'] == 'RUNNING' for x in active):
                    task.update(status='WAITING_OPERATION')
                    while any(x['status'] == 'RUNNING' for x in managed(task)):
                        if task.state().get('stop_requested'): break
                        time.sleep(.5)
                    if task.state().get('stop_requested'): task.update(status='STOPPED'); break
                    if any(x['status'] == 'UNKNOWN' for x in managed(task)): raise RuntimeError('UNKNOWN_PROCESS_REQUIRES_INSPECTION')
                    if any(x['status'] == 'INVALIDATED' for x in managed(task)): raise RuntimeError('PROCESS_ARTIFACT_CHANGED')
                if role == 'main':
                    if response['status'] == 'WAIT_USER':
                        if not response['questions']: raise ValueError('WAIT_USER_REQUIRES_QUESTION')
                        task.update(status='WAITING_INPUT', blocker=response); break
                    if response['status'] == 'FAILED':
                        _prepare_repair(task, response['summary'], role); continue
                    try:
                        artifacts = _paths(task, response['artifacts'])
                        if not artifacts: raise ValueError('COMPLETION_REQUIRES_ARTIFACTS')
                    except ValueError as exc:
                        fingerprint = digest({'error': str(exc), 'artifacts': response['artifacts']})
                        history = state.get('native_feedback_history', [])
                        if history.count(fingerprint) >= 2: raise
                        hints = _path_hints(task, response['artifacts'])
                        feedback = str(exc) + '\nCorrect only the artifact path/list; do not repeat completed scientific work. Exact existing task-local candidates: ' + json.dumps(hints, ensure_ascii=False)
                        task.update(stage='main', status='READY', feedback=feedback, path_hints=hints,
                                    native_feedback_history=history + [fingerprint])
                        continue
                    execution = {'response': response, 'artifacts': artifacts, 'folder': str(folder),
                                 'agent': backend_for(task).name, 'model': task.request().get('model'),
                                 'provider': task.request().get('provider')}
                    path = 'execution/' + digest(execution) + '.json'; write(task.path / path, execution)
                    task.update(stage='review', status='READY', feedback='', path_hints=[],
                                execution={'path': path, 'sha256': sha(task.path / path)})
                elif role == 'review':
                    execution = load(task.path / state['execution']['path'])
                    if any(sha(x['path']) != x['sha256'] for x in execution['artifacts']): raise ValueError('REVIEW_ARTIFACT_CHANGED')
                    try:
                        if response['status'] in {'PASS','INSUFFICIENT'} and not response['evidence']:
                            raise ValueError('REVIEW_EVIDENCE_REQUIRED')
                        validated_evidence = _paths(task, response['evidence'])
                    except ValueError as exc:
                        fingerprint = digest({'error': str(exc), 'evidence': response['evidence']})
                        history = state.get('review_validation_history', [])
                        if history.count(fingerprint) >= 2: raise
                        hints = _path_hints(task, response['evidence'])
                        feedback = str(exc) + '\nResubmit the independent review with valid evidence paths only. Do not rerun or modify scientific work. Exact existing task-local candidates: ' + json.dumps(hints, ensure_ascii=False)
                        task.update(stage='review', status='READY', feedback=feedback, path_hints=hints,
                                    review_validation_history=history + [fingerprint])
                        continue
                    response['evidence'] = [x['path'] for x in validated_evidence]
                    path = 'reviews/' + digest(response) + '.json'; write(task.path / path, response)
                    record = {'path': path, 'sha256': sha(task.path / path)}
                    if response['status'] in {'PASS', 'INSUFFICIENT'}:
                        task.update(status='COMPLETED' if response['status'] == 'PASS' else 'INSUFFICIENT', review=record,
                                    feedback='', path_hints=[])
                        _render(task); break
                    if response['status'] == 'WAIT_USER':
                        task.update(status='WAITING_INPUT', blocker=response, review=record, feedback='', path_hints=[]); break
                    if not response['repair_request'].strip(): raise ValueError('REVIEW_REPAIR_REQUEST_REQUIRED')
                    fingerprint = digest({'repair_request': response['repair_request'], 'artifacts': execution['artifacts']})
                    history = state.get('native_feedback_history', [])
                    if history.count(fingerprint) >= 1:
                        task.update(status='BLOCKED', blocker='REPAIR_NO_PROGRESS', review=record); break
                    task.update(stage='main', status='READY', review=record, feedback=response['repair_request'],
                                path_hints=[], native_feedback_history=history + [fingerprint])
                else:
                    _apply_repair(task, response); break
            return task.state()
        except Exception as exc:
            error = str(exc); state = task.state()
            protected = ('UNKNOWN_', 'PROCESS_ARTIFACT_CHANGED', 'FROZEN_INPUT_CHANGED', 'RUNTIME_IDENTITY_CHANGED',
                         'TASK_STOP_REQUESTED', 'BUDGET_EXHAUSTED', 'AGENT_VERSION_NOT_VERIFIED',
                         'BWRAP_UNAVAILABLE', 'CODEX_BINARY_NOT_FOUND', 'DSH_BINARY_NOT_FOUND',
                         'NODE_BINARY_NOT_FOUND', 'DSH_PATCH_NOT_FOUND')
            diagnostics = ''
            stderr_text = ''
            incomplete = _incomplete_folder(error)
            if incomplete is not None:
                folder = incomplete
                stderr_text = (folder/'stderr.log').read_text(errors='replace') if (folder/'stderr.log').exists() else ''
                diagnostics = stderr_text + ((folder/'events.jsonl').read_text(errors='replace') if (folder/'events.jsonl').exists() else '')
            unavailable = any(x in diagnostics.lower() for x in ('usage limit', 'rate limit', 'quota', 'authentication',
                                                               'unauthorized', 'missing_credential', 'no api key'))
            if not state.get('stop_requested') and state.get('stage') != 'repair' and not error.startswith(protected) and not unavailable and not state.get('pending_native'):
                try:
                    _prepare_repair(task, error, state['stage'])
                    folder, child = _prepare(task, 'repair'); response = _wait_call(task, 'repair', folder, child)
                    task.update(pending_native=None); _apply_repair(task, response)
                    return task.state()
                except Exception as repair_exc:
                    error = str(repair_exc)
            tail = next((line.strip() for line in reversed(stderr_text.splitlines()) if line.strip()), '')
            if not tail:
                tail = next((line.strip() for line in reversed(diagnostics.splitlines()) if line.strip()), '')
            blocker = error if not tail else error + ' | ' + tail[:300]
            task.update(status='STOPPED' if task.state().get('stop_requested') else 'BLOCKED', blocker=blocker); return task.state()
        finally:
            task.update(controller=None)
