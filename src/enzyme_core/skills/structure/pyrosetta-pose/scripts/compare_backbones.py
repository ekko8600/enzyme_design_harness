"""Aligned backbone/CA displacement and severe heavy-atom proximity for saved models."""
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np
from verify_preservation import read


def alignment_metrics(reference,candidate):
    a=np.asarray(reference,dtype=float);b=np.asarray(candidate,dtype=float)
    if a.shape!=b.shape or a.ndim!=2 or a.shape[1]!=3 or len(a)<3 or not np.isfinite(a).all() or not np.isfinite(b).all():
        raise ValueError('INVALID_ALIGNMENT_COORDINATES')
    centered_a=a-a.mean(0);centered_b=b-b.mean(0)
    u,_,vt=np.linalg.svd(centered_b.T@centered_a);sign=np.eye(3);sign[-1,-1]=np.linalg.det(u@vt)
    difference=centered_b@u@sign@vt-centered_a
    return {'aligned_RMSD_A':float(np.sqrt(np.mean(np.sum(difference**2,axis=1)))),
            'maximum_unaligned_displacement_A':float(np.linalg.norm(b-a,axis=1).max()),'matched_atoms':len(a)}


def analyze(reference_pdb,panel_dir):
    reference_pdb=Path(reference_pdb);panel_dir=Path(panel_dir)
    manifest=json.loads((panel_dir/'manifest.json').read_text())
    if manifest['input_sha256']!=hashlib.sha256(reference_pdb.read_bytes()).hexdigest():raise ValueError('REFERENCE_HASH_MISMATCH')
    original,identities=read(reference_pdb);backbone=sorted(k for k in original if k[-1] in {'N','CA','C','O'});ca=sorted(k for k in original if k[-1]=='CA');rows=[]
    for row in manifest['models']:
        relative=Path(row['pdb'])
        if relative.is_absolute() or '..' in relative.parts:raise ValueError('UNSAFE_MODEL_PATH')
        path=panel_dir/relative
        if hashlib.sha256(path.read_bytes()).hexdigest()!=row['sha256']:raise ValueError('MODEL_HASH_MISMATCH')
        atoms,observed=read(path)
        if observed!=identities or any(k not in atoms for k in backbone):raise ValueError('IDENTITY_OR_BACKBONE_COVERAGE')
        bb=alignment_metrics([original[k][1] for k in backbone],[atoms[k][1] for k in backbone])
        alpha=alignment_metrics([original[k][1] for k in ca],[atoms[k][1] for k in ca])
        xyz=np.asarray([v[1] for v in atoms.values() if v[0].upper() not in {'H','D'}]);distance=np.linalg.norm(xyz[:,None,:]-xyz[None,:,:],axis=-1)
        rows.append({'method':row['method'],'decoy':row['decoy'],'pdb_sha256':row['sha256'],
                     'aligned_backbone_RMSD_A':bb['aligned_RMSD_A'],'maximum_unaligned_backbone_displacement_A':bb['maximum_unaligned_displacement_A'],
                     'backbone_atoms_matched':bb['matched_atoms'],'aligned_CA_RMSD_A':alpha['aligned_RMSD_A'],
                     'heavy_atom_pairs_below_1A':int(np.count_nonzero(np.triu(distance<1,1)))})
    if not rows:raise ValueError('EMPTY_MODEL_COLLECTION')
    return {'models':rows,'scope':'Rigid-body aligned backbone/CA metrics and heavy atom distances <1 Å, not a complete stereochemical validation or activity prediction.'}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--reference-pdb',required=True)
    p.add_argument('--panel-dir',required=True)
    p.add_argument('--output',required=True)
    a=p.parse_args();result=analyze(a.reference_pdb,a.panel_dir);path=Path(a.output);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')


if __name__=='__main__':main()
