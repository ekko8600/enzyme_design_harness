"""Deterministic, symlink-free directory identity and file manifests."""
import hashlib
import json
from pathlib import Path

def manifest(root):
    root=Path(root)
    if root.is_symlink() or not root.is_dir():raise ValueError('COLLECTION_ROOT_INVALID')
    rows=[]
    for p in sorted(root.rglob('*')):
        if p.is_symlink():raise ValueError('COLLECTION_SYMLINK_FORBIDDEN')
        if p.is_file():
            h=hashlib.sha256()
            with p.open('rb') as f:
                for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
            rows.append({'path':p.relative_to(root).as_posix(),'sha256':h.hexdigest(),'size_bytes':p.stat().st_size})
        elif not p.is_dir():raise ValueError('COLLECTION_SPECIAL_FILE_FORBIDDEN')
    return rows

def tree_hash(root):
    return hashlib.sha256(json.dumps(manifest(root),sort_keys=True,separators=(',',':')).encode()).hexdigest()
