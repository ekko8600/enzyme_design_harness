"""Linux execution isolation for task-local repair implementations."""
from pathlib import Path
import os
import shutil
import subprocess
import sys


def _python_runtime_roots(python):
    """Return interpreter roots needed by a venv, including its base runtime.

    A venv created with ``--copies`` has an executable under the venv, while
    ``pyvenv.cfg`` still points at the base interpreter that supplies the
    standard library.  Both roots must be visible inside bwrap.
    """
    py = Path(python).absolute()
    roots = {py.parent.parent, py.resolve().parent.parent}
    cfg = py.parent.parent / 'pyvenv.cfg'
    if cfg.is_file():
        for line in cfg.read_text(errors='replace').splitlines():
            key, sep, value = line.partition('=')
            if sep and key.strip().lower() == 'home':
                home = Path(value.strip()).expanduser()
                if not home.is_absolute():
                    home = (cfg.parent / home).resolve()
                # CPython writes the interpreter's bin directory in `home`.
                base = home.parent if home.name.lower() in {'bin', 'scripts'} else home
                if base.exists():
                    roots.add(base)
                break
    return roots


def collapse_mounts(paths):
    """Same-path read binds below a bound directory are already visible."""
    kept=[]
    for path in sorted({Path(p).absolute() for p in paths},key=lambda p:(len(str(p)),str(p))):
        if any(parent in path.parents and parent.is_dir() for parent in kept):continue
        kept.append(path)
    return kept


def command(argv, *, python, reads=(), writes=(), network=False, socket_dir=None, preserve_environment=False):
    binary=shutil.which('bwrap')
    if not binary: raise RuntimeError('REPAIR_SANDBOX_UNAVAILABLE')
    cmd=[binary,'--die-with-parent','--unshare-all']
    if not preserve_environment:cmd+=['--new-session']
    if network: cmd+=['--share-net']
    cmd+=['--proc','/proc','--dev-bind' if preserve_environment else '--dev','/dev']
    if preserve_environment:cmd+=['/dev']
    cmd+=['--tmpfs','/tmp']
    if not preserve_environment:cmd+=['--clearenv','--setenv','PATH','/usr/bin:/bin','--setenv','HOME','/tmp']
    cmd+=['--setenv','PYTHONDONTWRITEBYTECODE','1']
    mounts=[Path(p) for p in ['/usr','/bin','/lib','/lib64','/etc/ld.so.cache','/etc/ssl/certs'] if Path(p).exists()]+list(_python_runtime_roots(python))+[Path(p).absolute() for p in reads]
    for p in set(mounts):
        if p==Path('/') or any(part in {'.codex','.ssh'} for part in p.parts) or p.name.endswith('.sqlite'): raise ValueError('REPAIR_SANDBOX_MOUNT_FORBIDDEN')
        # A custom interpreter prefix must not expose the user's authentication
        # or default task database merely because its bin/ directory is broad.
        sensitive=[Path.home()/'.codex',Path.home()/'.ssh',Path.home()/'.aws',Path.home()/'.config/gcloud',Path.home()/'.local/share/enzyme-harness']
        resolved=p.resolve()
        if p.is_dir() and any(secret.resolve()==resolved or resolved in secret.resolve().parents for secret in sensitive):
            raise ValueError('REPAIR_SANDBOX_SENSITIVE_PARENT_FORBIDDEN')
    for p in collapse_mounts(mounts):
        cmd+=['--ro-bind',str(p),str(p)]
    for p in writes:
        p=Path(p).absolute();cmd+=['--bind',str(p),str(p)]
    if socket_dir: cmd+=['--bind',str(socket_dir),str(socket_dir)]
    if network and Path('/etc/resolv.conf').exists():cmd+=['--ro-bind','/etc/resolv.conf','/etc/resolv.conf']
    return cmd+['--']+list(argv)


def probe():
    try:
        result=subprocess.run(command([sys.executable,'-I','-c','import socket; s=socket.socket(); print("isolated")'],python=sys.executable),capture_output=True,text=True,timeout=15)
        return {'status':'AVAILABLE' if result.returncode==0 else 'UNAVAILABLE','returncode':result.returncode,'error':result.stderr[-1000:]}
    except (OSError,ValueError,RuntimeError,subprocess.TimeoutExpired) as exc:return {'status':'UNAVAILABLE','error':str(exc)}


# Public read-only runtime-path introspection for independent drivers.
python_runtime_roots = _python_runtime_roots
