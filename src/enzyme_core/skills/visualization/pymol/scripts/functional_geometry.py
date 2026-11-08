"""Specified atom geometry and a heavy-atom functional-neighborhood contact network."""
import argparse,hashlib,json,math,subprocess,sys
from pathlib import Path
import numpy as np


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--input-pdb',required=True);p.add_argument('--selection-file',required=True);p.add_argument('--output-dir',required=True)
    a=p.parse_args();spec=json.loads(Path(a.selection_file).read_text());out=Path(a.output_dir);out.mkdir(parents=True,exist_ok=True)
    import pymol
    pymol.finish_launching(['pymol','-cq']);cmd=pymol.cmd;cmd.load(a.input_pdb,'enzyme')
    def selection(atom):
        chain,number,icode,name=atom
        if not all(str(v).isalnum() or v=='' for v in atom):raise ValueError('UNSAFE_ATOM_SELECTION')
        text=f'enzyme and chain {chain} and resi {number}{icode} and name {name}'
        if cmd.count_atoms(text)!=1:raise ValueError('ATOM_SELECTION_NOT_UNIQUE:'+text)
        return text
    geometry=[]
    for pair in spec['distances']:
        value=float(cmd.get_distance(*[selection(x) for x in pair]));geometry.append({'kind':'distance','atoms':pair,'value':value,'unit':'angstrom'})
    for triple in spec['angles']:
        value=float(cmd.get_angle(*[selection(x) for x in triple]));geometry.append({'kind':'angle','atoms':triple,'value':value,'unit':'degree'})
    model=cmd.get_model('enzyme and not elem H+D');atoms=model.atom
    keys=[(x.chain,int(x.resi.rstrip('ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz')),x.resi[len(x.resi.rstrip('ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz')):],x.name) for x in atoms]
    xyz=np.asarray([x.coord for x in atoms]);centers=[xyz[keys.index(tuple(atom))] for atom in spec['centers']]
    near=np.min(np.linalg.norm(xyz[:,None,:]-np.asarray(centers)[None,:,:],axis=-1),axis=1)<=float(spec['neighborhood_radius_A'])
    residues={(keys[i][0],keys[i][1],keys[i][2]) for i in np.where(near)[0]};edges={};salt=[]
    acidic={'ASP':{'OD1','OD2'},'GLU':{'OE1','OE2'}};basic={'LYS':{'NZ'},'ARG':{'NE','NH1','NH2'}}
    for i,first in enumerate(atoms):
        if keys[i][:3] not in residues:continue
        for j in range(i+1,len(atoms)):
            second=atoms[j]
            if keys[j][:3] not in residues or keys[i][:3]==keys[j][:3]:continue
            d=float(np.linalg.norm(xyz[i]-xyz[j]));pair=tuple(sorted((keys[i][:3],keys[j][:3])))
            if d<=float(spec['contact_cutoff_A']):edges[pair]=min(edges.get(pair,float('inf')),d)
            charged=(first.name in acidic.get(first.resn,set()) and second.name in basic.get(second.resn,set())) or (second.name in acidic.get(second.resn,set()) and first.name in basic.get(first.resn,set()))
            if charged and d<=float(spec['salt_bridge_cutoff_A']):salt.append({'atoms':[keys[i],keys[j]],'distance_A':d})
    result={'input_sha256':hashlib.sha256(Path(a.input_pdb).read_bytes()).hexdigest(),'selection_sha256':hashlib.sha256(Path(a.selection_file).read_bytes()).hexdigest(),'geometry':geometry,'neighborhood_residues':sorted(residues),'contacts':[{'residues':k,'minimum_heavy_atom_distance_A':v} for k,v in sorted(edges.items())],'salt_bridges':salt,'scope':'Geometric contacts and charged-atom proximity only; histidine protonation is not inferred. No catalytic rate, hydrogen-bond energy or activity prediction.'}
    (out/'geometry.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    cmd.show('cartoon','enzyme');cmd.save(str(out/'geometry.pse'))
    expected_atoms=int(cmd.count_atoms('all'));cmd.quit()
    verifier=Path(__file__).with_name('verify_session.py')
    checked=subprocess.run([sys.executable,str(verifier),'--session-file',str(out/'geometry.pse'),
                            '--expected-atoms',str(expected_atoms),'--output-json',str(out/'session_validation.json')],
                           capture_output=True,text=True,timeout=120)
    (out/'session_reload_stdout.log').write_text(checked.stdout)
    (out/'session_reload_stderr.log').write_text(checked.stderr)
    if checked.returncode:raise RuntimeError('SAVED_PYMOL_SESSION_RELOAD_FAILED:'+str(checked.returncode))
    validation=json.loads((out/'session_validation.json').read_text())
    if validation.get('pass') is not True or validation.get('total_atoms')!=expected_atoms:
        raise ValueError('SAVED_PYMOL_SESSION_VALIDATION_REJECTED')


if __name__=='__main__':main()
