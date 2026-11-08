"""Reload a PyMOL session and record its objects and atom counts."""
import argparse
import hashlib
import json
import os
from pathlib import Path


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--session-file',required=True);p.add_argument('--expected-atoms',type=int,default=0)
    p.add_argument('--output-json',required=True);a=p.parse_args()
    session=Path(a.session_file)
    if not session.is_file():raise ValueError('SESSION_FILE_MISSING')
    os.environ.setdefault('PYOPENGL_PLATFORM','osmesa')
    import pymol
    pymol.finish_launching(['pymol','-cq'])
    cmd=pymol.cmd
    try:
        cmd.load(str(session),'session_validation')
        objects=cmd.get_object_list('all')
        details=[]
        for name in objects:
            atoms=int(cmd.count_atoms(name))
            details.append({'object':name,'atoms':atoms})
        total=sum(row['atoms'] for row in details)
        result={'schema_version':'enzyme-core/pymol-session-validation/v1','pass':bool(details and total>0 and (a.expected_atoms==0 or total==a.expected_atoms)),
                'path':str(session.resolve()),'sha256':hashlib.sha256(session.read_bytes()).hexdigest(),
                'pymol_version':getattr(pymol,'pymol_version',None),'objects':details,'total_atoms':total,
                'expected_atoms':a.expected_atoms or None,'method':'Load PSE with PyMOL cmd.load and independently enumerate session objects and atoms.',
                'claim_boundary':'File reloadability and atom presence only; no scientific interpretation.'}
    finally:
        cmd.delete('session_validation');cmd.quit()
    out=Path(a.output_json);out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    print(json.dumps({'pass':result['pass'],'total_atoms':result['total_atoms'],'output':str(out)}))
    if not result['pass']:raise SystemExit(2)


if __name__=='__main__':main()
