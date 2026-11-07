"""Protected minimal entrypoint. No scientific or repair logic lives here."""
from pathlib import Path
import json
import os
import subprocess
import sys


def main(argv=None):
    argv=list(sys.argv[1:] if argv is None else argv)
    root=Path.home()/'.local/share/enzyme-harness'
    if '--root' in argv:root=Path(argv[argv.index('--root')+1]).expanduser().resolve()
    for _ in range(11):
        python=sys.executable;pointer=root/'active-release.json'
        if pointer.is_file():
            value=json.loads(pointer.read_text());release=root/'releases'/value['id'];manifest=release/'release.json'
            import hashlib
            if not value.get('validated') or release.resolve().parent!=(root/'releases').resolve():raise RuntimeError('ACTIVE_RELEASE_INVALID')
            if hashlib.sha256(manifest.read_bytes()).hexdigest()!=value['manifest_sha256']:raise RuntimeError('ACTIVE_RELEASE_MANIFEST_CHANGED')
            if Path(value['python']).absolute()!=release/'environment/bin/python':raise RuntimeError('ACTIVE_RELEASE_PYTHON_INVALID')
            python=value['python']
            expected=value['runtime_identity']
            site=next((release/'environment/lib').glob('python*/site-packages'))
            def content_hash(path):return hashlib.sha256(path.read_bytes()).hexdigest()
            def record_hash(record):return hashlib.sha256(json.dumps(record,ensure_ascii=False,sort_keys=True,allow_nan=False,indent=2).encode()).hexdigest()
            package=site/'enzyme_harness';core=site/'enzyme_core'
            def harness_hashes(root):
                return {p.relative_to(root).as_posix():content_hash(p) for p in sorted(root.rglob('*.py'))
                        if '__pycache__' not in p.relative_to(root).parts and 'acceptance' not in p.relative_to(root).parts}
            actual={
                'harness_sha256':record_hash(harness_hashes(package)),
                'acceptance_sha256':record_hash({p.relative_to(package/'acceptance').as_posix():content_hash(p) for p in sorted((package/'acceptance').rglob('*.py'))}),
                'core_runtime_sha256':record_hash({p.relative_to(core).as_posix():content_hash(p) for p in sorted(core.rglob('*.py')) if not {'skills','third_party','__pycache__'}.intersection(p.relative_to(core).parts)}),
                'core_interfaces_sha256':content_hash(core/'runtime/operation_interfaces.json'),
                'core_api_sha256':content_hash(core/'api.py')}
            if any(expected.get(k)!=v for k,v in actual.items()):raise RuntimeError('ACTIVE_RELEASE_CODE_CHANGED')
        result=subprocess.run([python,'-m','enzyme_harness.cli',*argv])
        if result.returncode!=75:return result.returncode
        handoff=json.loads((root/'handoff.json').read_text())
        argv=['--root',str(root),'task','resume',handoff['task_id'],'--foreground']
    raise RuntimeError('RELEASE_HANDOFF_LIMIT_EXCEEDED')


if __name__=='__main__':raise SystemExit(main())
