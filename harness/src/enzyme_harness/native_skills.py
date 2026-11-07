"""Snapshot installed Core skills into the workspace for native agent discovery.

Codex and dsh both discover `<workspace>/.agents/skills/<name>/SKILL.md`; the
snapshot is hash-verified against the installed Core source.
"""
from pathlib import Path
import shutil
import enzyme_core
from .files import digest, sha, write


def _source_identity(skill):
    return digest([{'path': p.relative_to(skill).as_posix(), 'sha256': sha(p)}
                   for p in sorted(skill.rglob('*')) if (p.is_file() or p.is_symlink())
                   and '__pycache__' not in p.parts and p.suffix != '.pyc'])


def snapshot(task_path):
    core = Path(enzyme_core.__file__).resolve().parent
    target = Path(task_path) / 'workspace' / '.agents' / 'skills'
    target.mkdir(parents=True, exist_ok=True)
    records = []
    for source in sorted((core / 'skills').glob('*/*/SKILL.md')):
        skill = source.parent
        destination = target / skill.name
        if destination.exists():
            raise ValueError('SKILL_NAME_CONFLICT:' + skill.name)
        shutil.copytree(skill, destination, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        source_hash = _source_identity(skill); snapshot_hash = sha(destination)
        if source_hash != snapshot_hash: raise ValueError('SKILL_SNAPSHOT_CHANGED:' + skill.name)
        records.append({'name': skill.name, 'source': str(skill),
                        'source_sha256': source_hash, 'snapshot_sha256': snapshot_hash,
                        'path': str(destination)})
    write(Path(task_path) / 'skills.json', {'schema_version': 'enzyme-harness/skills/v1',
          'core': str(core), 'skills': records})
    return records
