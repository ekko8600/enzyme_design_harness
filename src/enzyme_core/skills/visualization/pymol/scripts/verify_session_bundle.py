"""Verify a frozen PyMOL session and provenance files from a readable bundle."""
import argparse
import hashlib
import json
import os
from pathlib import Path


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def inside(root, name):
    if name not in {'geometry.pse','geometry.json'}:
        raise ValueError('BUNDLE_MEMBER_NOT_ALLOWED')
    raw=root/name
    if raw.is_symlink():raise ValueError('BUNDLE_MEMBER_SYMLINK')
    path=raw.resolve()
    if path.parent!=root.resolve() or not path.is_file():
        raise ValueError('BUNDLE_MEMBER_INVALID')
    return path


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-bundle',required=True);parser.add_argument('--output-json',required=True)
    args=parser.parse_args();root=Path(args.input_bundle).resolve()
    manifest_path=root/'bundle_manifest.json'
    if not root.is_dir() or not manifest_path.is_file():raise ValueError('BUNDLE_MANIFEST_MISSING')
    manifest=json.loads(manifest_path.read_text())
    session=inside(root,manifest.get('session_file',''));geometry=inside(root,manifest.get('geometry_file',''))
    actual_session=sha(session);actual_geometry=sha(geometry)
    if actual_session!=manifest.get('session_sha256'):raise ValueError('SESSION_SHA256_MISMATCH')
    if actual_geometry!=manifest.get('geometry_sha256'):raise ValueError('GEOMETRY_SHA256_MISMATCH')
    geometry_data=json.loads(geometry.read_text())
    if geometry_data.get('input_sha256')!=manifest.get('source_structure_sha256'):
        raise ValueError('GEOMETRY_SOURCE_STRUCTURE_HASH_MISMATCH')
    os.environ.setdefault('PYOPENGL_PLATFORM','osmesa')
    import pymol
    pymol.finish_launching(['pymol','-cq']);cmd=pymol.cmd
    try:
        cmd.load(str(session),'session_validation');objects=cmd.get_object_list('all')
        details=[{'object':name,'atoms':int(cmd.count_atoms(name))} for name in objects]
        total=sum(row['atoms'] for row in details)
    finally:
        cmd.delete('session_validation');cmd.quit()
    expected=int(manifest['expected_atoms'])
    result={'schema_version':'enzyme-core/pymol-session-bundle-validation/v1',
        'pass':bool(details and total==expected),'errors':[] if details and total==expected else ['SESSION_ATOM_COUNT_MISMATCH'],
        'parent_task_id':manifest['parent_task_id'],'manifest_sha256':sha(manifest_path),
        'session_sha256':actual_session,'geometry_source_sha256':actual_geometry,
        'source_structure_sha256':geometry_data['input_sha256'],'expected_atoms':expected,
        'objects':details,'total_atoms':total,
        'method':'Verify bundle member hashes and geometry provenance, then load the PSE in PyMOL and count objects/atoms.',
        'claim_boundary':'Input provenance and session reloadability only; no geometric interpretation or enzyme activity claim.'}
    out=Path(args.output_json);out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    print(json.dumps({'pass':result['pass'],'total_atoms':total,'output':str(out)}))
    if not result['pass']:raise SystemExit(2)


if __name__=='__main__':main()
