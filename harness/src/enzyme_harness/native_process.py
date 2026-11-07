"""Detached local process supervisor, callable from a native Codex shell."""
from pathlib import Path
import os
import resource
import subprocess
import sys
import time
from .files import alive, digest, identity, inside, load, lock, sha, terminate, write
from .store import Task, identifier
_OWNED = {}


def folder(task, process_id):
    return task.path / 'processes' / identifier(process_id)


def inspect(task, process_id):
    path = folder(task, process_id)
    invocation = load(path / 'invocation.json')
    if invocation is None:
        raise ValueError('PROCESS_NOT_FOUND:' + process_id)
    receipt = load(path / 'receipt.json')
    if receipt and receipt.get('invocation_sha256') != digest(invocation):
        raise ValueError('PROCESS_RECEIPT_INVOCATION_CHANGED:' + process_id)
    owned = _OWNED.get(str(path))
    if owned and owned.poll() is not None:
        owned.wait(); _OWNED.pop(str(path), None)
    process = load(path / 'process.json', load(path / 'launch.json', {}))
    status = receipt.get('status') if receipt else ('RUNNING' if alive(process.get('supervisor')) or alive(process.get('command')) else 'UNKNOWN')
    if status == 'COMPLETED' and any(not Path(name).exists() or sha(name) != expected
                                     for name, expected in receipt.get('outputs', {}).items()):
        status = 'INVALIDATED'
    return {'process_id': process_id, 'directory': str(path), 'status': status,
            'invocation': invocation, 'process': process, 'receipt': receipt}


def start(task, process_id, argv, cwd, inputs=(), outputs=()):
    if not argv: raise ValueError('COMMAND_REQUIRED')
    base = task.path / 'workspace'
    base.mkdir(parents=True, exist_ok=True)
    work = inside(base, cwd or '.')
    if not work.is_dir(): raise ValueError('PROCESS_CWD_NOT_DIRECTORY')
    inputs = [str(Path(p).expanduser().resolve()) for p in inputs]
    outputs = [str(inside(base, p)) for p in outputs]
    hashes = {p: sha(p) for p in inputs}
    inv = {'schema_version': 'enzyme-harness/process/v1', 'process_id': process_id,
           'argv': list(argv), 'cwd': str(work), 'inputs': hashes, 'outputs': outputs,
           'python': str(Path(sys.executable).absolute())}
    path = folder(task, process_id)
    (task.path / 'processes').mkdir(exist_ok=True)
    with lock(task.path / 'processes' / '.lock'):
        previous = load(path / 'invocation.json')
        if previous:
            if digest(previous) != digest(inv): raise ValueError('PROCESS_ID_CONFLICT:' + process_id)
            return inspect(task, process_id)
        for existing in (task.path / 'processes').glob('*/invocation.json'):
            prior = load(existing, {})
            for output in outputs:
                candidate = Path(output)
                for claimed in prior.get('outputs', []):
                    old = Path(claimed)
                    if candidate == old or candidate in old.parents or old in candidate.parents:
                        raise ValueError('PROCESS_OUTPUT_ALREADY_CLAIMED:' + str(candidate) + ':' + str(prior.get('process_id')))
        path.mkdir(parents=True)
        write(path / 'invocation.json', inv)
        with (path / 'supervisor.log').open('ab') as log:
            child = subprocess.Popen([sys.executable, '-m', 'enzyme_harness.native_process',
                                      str(task.root), task.id, process_id], stdin=subprocess.DEVNULL,
                                     stdout=log, stderr=log, start_new_session=True)
        _OWNED[str(path)] = child
        proc = identity(child.pid)
        if not proc: raise RuntimeError('PROCESS_SUPERVISOR_START_UNKNOWN:' + process_id)
        write(path / 'launch.json', {'supervisor': proc, 'invocation_sha256': digest(inv)})
        write(path / 'launch.allow', {'supervisor': proc, 'invocation_sha256': digest(inv)})
    return inspect(task, process_id)


def wait(task, process_id):
    while True:
        item = inspect(task, process_id)
        if item['status'] != 'RUNNING': return item
        time.sleep(.5)


def stop(task, process_id):
    item = inspect(task, process_id)
    process = item['process']
    terminate(process.get('command'))
    return inspect(task, process_id)


def worker(root, task_id, process_id):
    task = Task(root, task_id); path = folder(task, process_id)
    inv = load(path / 'invocation.json'); supervisor = identity()
    for _ in range(200):
        gate = load(path / 'launch.allow')
        if gate: break
        time.sleep(.05)
    else: return 1
    expected = gate.get('supervisor') or {}
    if any(expected.get(k) != (supervisor or {}).get(k) for k in ('pid','started','boot_id')) or gate.get('invocation_sha256') != digest(inv):
        raise RuntimeError('PROCESS_LAUNCH_GATE_INVALID')
    write(path / 'process.json', {'supervisor': supervisor, 'command': None})
    start_time = time.monotonic(); child = None
    try:
        for name, expected in inv['inputs'].items():
            if sha(name) != expected: raise ValueError('PROCESS_INPUT_CHANGED:' + name)
        environment={key:value for key,value in os.environ.items()
                     if key != 'CODEX_HOME' and not any(word in key.upper() for word in ('TOKEN','SECRET','PASSWORD','API_KEY'))}
        with (path / 'stdout.log').open('wb') as stdout, (path / 'stderr.log').open('wb') as stderr:
            child = subprocess.Popen(inv['argv'], cwd=inv['cwd'], stdin=subprocess.DEVNULL,
                                     stdout=stdout, stderr=stderr, env=environment, start_new_session=True)
            write(path / 'process.json', {'supervisor': supervisor, 'command': identity(child.pid)})
            code = child.wait()
        intact = all(Path(p).exists() and sha(p) == h for p, h in inv['inputs'].items())
        output_hashes = {p: sha(p) for p in inv['outputs'] if Path(p).exists()}
        usage = resource.getrusage(resource.RUSAGE_CHILDREN)
        write(path / 'receipt.json', {'schema_version': 'enzyme-harness/process-receipt/v1',
              'status': 'COMPLETED' if code == 0 and intact and len(output_hashes) == len(inv['outputs']) else 'FAILED',
              'returncode': code, 'input_integrity': 'PASS' if intact else 'FAIL',
              'invocation_sha256': digest(inv), 'outputs': output_hashes,
              'elapsed_seconds': time.monotonic() - start_time,
              'cpu_seconds': usage.ru_utime + usage.ru_stime,
              'peak_child_rss_bytes': usage.ru_maxrss * 1024,
              'observation': 'supervisor and direct child; detached descendants are not fully observed'})
    except BaseException as exc:
        write(path / 'receipt.json', {'status': 'UNKNOWN' if child and child.poll() is None else 'FAILED',
              'error': str(exc), 'invocation_sha256': digest(inv)})
    return 0


if __name__ == '__main__':
    raise SystemExit(worker(*sys.argv[1:]))
