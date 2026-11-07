"""Explicit filesystem mounts for native science and candidate verification."""
from pathlib import Path
import os
import shutil
import subprocess
import sys
from enzyme_core.runtime.isolation import collapse_mounts,python_runtime_roots

_USABLE = {}


def bwrap_usable(binary=None, timeout=20):
    """Probe the exact outer wall the agent backends rely on, once per process.

    ``shutil.which`` is not enough: hosts that restrict unprivileged user
    namespaces (for example Ubuntu with AppArmor's userns restriction) install
    bwrap but reject its uid map. A broken probe must fail closed with a clear
    remedy instead of an opaque bootstrap error.
    """
    if 'value' in _USABLE: return _USABLE['value']
    executable = binary or shutil.which('bwrap')
    if not executable: _USABLE['value'] = False; return False
    try:
        result = subprocess.run([executable, '--ro-bind', '/', '/', '--dev-bind', '/dev', '/dev',
                                 '--proc', '/proc', '--', 'true'], capture_output=True, timeout=timeout)
        _USABLE['value'] = result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        _USABLE['value'] = False
    return _USABLE['value']


def wrap(command, *, writes=(), reads=(), network=False, die_with_parent=True):
    binary=shutil.which('bwrap')
    if not binary:raise RuntimeError('BWRAP_REQUIRED')
    args=[binary,'--proc','/proc','--dev-bind','/dev','/dev','--tmpfs','/tmp']
    if die_with_parent:args+=['--die-with-parent']
    if not network:args+=['--unshare-net']
    mounts={Path(p) for p in ['/usr','/bin','/lib','/lib64','/etc/ld.so.cache','/etc/ssl/certs'] if Path(p).exists()}
    mounts.update(python_runtime_roots(sys.executable))
    # Candidate import directories may contain package symlinks. Mount their
    # actual code roots too, or a nested /tmp namespace falls back to installed code.
    import enzyme_core
    mounts.update([Path(enzyme_core.__file__).resolve().parent,Path(__file__).resolve().parent])
    mounts.update(Path(p).absolute() for p in sys.path if p and Path(p).is_dir() and Path(p).absolute() not in {Path('/'),Path('/tmp'),Path('/var/tmp'),Path.home()})
    mounts.update(Path(p).absolute() for p in reads)
    if network and Path('/etc/resolv.conf').exists():mounts.add(Path('/etc/resolv.conf'))
    for mount in mounts:
        if mount==Path('/') or any(part in {'.codex','.ssh','.aws'} for part in mount.parts):raise ValueError('SANDBOX_SENSITIVE_MOUNT')
    for mount in collapse_mounts(mounts):
        args+=['--ro-bind',str(mount),str(mount)]
    for path in writes:args+=['--bind',str(path),str(path)]
    return args+['--',*command]
