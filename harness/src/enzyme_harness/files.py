"""Atomic local records, content identities and process identities."""
from contextlib import contextmanager
from pathlib import Path
import fcntl
import hashlib
import json
import os
import signal
import tempfile
import time
import psutil


def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, indent=2)


def digest(value):
    return hashlib.sha256(dumps(value).encode()).hexdigest()


def load(path, default=None):
    path = Path(path)
    return json.loads(path.read_text()) if path.exists() else default


def write(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.'+path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(dumps(value)+'\n'); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
        folder = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(folder)
        finally: os.close(folder)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


def append(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as stream:
        stream.write(json.dumps({'time':time.time(), **value}, ensure_ascii=False, allow_nan=False)+'\n')
        stream.flush(); os.fsync(stream.fileno())


@contextmanager
def lock(path, blocking=True):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+') as stream:
        try: fcntl.flock(stream, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except BlockingIOError: raise RuntimeError('ALREADY_RUNNING') from None
        try: yield
        finally: fcntl.flock(stream, fcntl.LOCK_UN)


def inside(root, relative):
    root = Path(root).resolve(); path = (root / relative).resolve()
    if path != root and root not in path.parents: raise ValueError('PATH_ESCAPES_ROOT:'+str(relative))
    return path


def sha(path):
    path = Path(path)
    if path.is_symlink(): raise ValueError('SYMLINK_FORBIDDEN:'+str(path))
    if path.is_dir():
        return digest([{'path':p.relative_to(path).as_posix(), 'sha256':sha(p)} for p in sorted(path.rglob('*')) if p.is_file() or p.is_symlink()])
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b''): h.update(chunk)
    return h.hexdigest()


def identity(pid=None):
    try:
        process = psutil.Process(pid or os.getpid())
        return {'pid':process.pid, 'ppid':process.ppid(), 'pgid':os.getpgid(process.pid),
                'started':process.create_time(), 'boot_id':Path('/proc/sys/kernel/random/boot_id').read_text().strip()}
    except (psutil.Error, ProcessLookupError): return None


def alive(record):
    if not record: return False
    current = identity(record['pid'])
    if not current or any(current.get(k)!=record.get(k) for k in ('pid','started','boot_id')): return False
    try: return psutil.Process(record['pid']).status() != psutil.STATUS_ZOMBIE
    except psutil.Error: return False


def terminate(record):
    if alive(record):
        # Kill only the confirmed process group whose leader is the recorded PID.
        if record.get('pgid') == record['pid']: os.killpg(record['pid'], signal.SIGTERM)
        else: os.kill(record['pid'], signal.SIGTERM)
