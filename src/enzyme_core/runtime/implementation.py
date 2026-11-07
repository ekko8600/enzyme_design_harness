"""Hash-bound replacement entrypoints; the canonical scientific contract stays fixed."""
from pathlib import Path
import hashlib
import json


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify(value, canonical):
    if value.get('schema_version')!='enzyme-core/task-implementation/v1':raise ValueError('IMPLEMENTATION_SCHEMA_INVALID')
    root=Path(value['root']).resolve();entry=Path(value['entrypoint'])
    if entry.is_symlink() or not entry.is_file() or root not in entry.resolve().parents:raise ValueError('IMPLEMENTATION_PATH_INVALID')
    if sha(canonical)!=value['baseline_sha256']:raise ValueError('IMPLEMENTATION_BASELINE_CHANGED')
    actual={p.relative_to(root).as_posix():sha(p) for p in root.rglob('*') if p.is_file()}
    if any(p.is_symlink() for p in root.rglob('*')) or actual!=value['files']:raise ValueError('IMPLEMENTATION_CHANGED')
    if value.get('validation_status')!='ACCEPTED' or not value.get('authorization_id'):raise ValueError('IMPLEMENTATION_NOT_AUTHORIZED')
    return str(entry.resolve())
