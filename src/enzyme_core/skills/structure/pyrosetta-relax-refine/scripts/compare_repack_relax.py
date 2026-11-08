"""Matched WT repacking versus FastRelax, with explicit degrees of freedom.

Raw-pose energy and energy of exported/reloaded PDB are distinct observables.
The latter is reproducible from the supplied coordinate artifact; no activity claim.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path


def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def identity(pose):
    info=pose.pdb_info()
    return [(info.chain(i),info.number(i),info.icode(i),pose.residue(i).name3()) for i in range(1,pose.total_residue()+1)]


def backbone(pose):
    return {(i,name):tuple(pose.residue(i).xyz(name)) for i in range(1,pose.total_residue()+1)
            for name in ['N','CA','C','O'] if pose.residue(i).has(name)}


def run(a,p,sf):
    if a.nstruct<1 or a.max_iter<1 or a.relax_repeats<1:raise ValueError('INVALID_SAMPLING')
    folder=Path(a.out_dir);folder.mkdir(parents=True,exist_ok=True)
    if (folder/'manifest.json').exists():raise ValueError('EXISTING_RUN_PRESERVED')
    native=p.pose_from_pdb(a.input_pdb);rows=[]
    for method in ['repack','FastRelax']:
        for decoy in range(1,a.nstruct+1):
            pose=native.clone()
            if method=='repack':
                tf=p.rosetta.core.pack.task.TaskFactory()
                tf.push_back(p.rosetta.core.pack.task.operation.InitializeFromCommandline())
                tf.push_back(p.rosetta.core.pack.task.operation.IncludeCurrent())
                tf.push_back(p.rosetta.core.pack.task.operation.RestrictToRepacking())
                mover=p.rosetta.protocols.minimization_packing.PackRotamersMover(sf);mover.task_factory(tf)
            else:
                mm=p.rosetta.core.kinematics.MoveMap();mm.set_bb(True);mm.set_chi(True);mm.set_jump(False)
                mover=p.rosetta.protocols.relax.FastRelax(sf,a.relax_repeats)
                mover.set_movemap(mm);mover.max_iter(a.max_iter)
            mover.apply(pose);raw=float(sf(pose))
            file=folder/f'{method}_seed_{a.seed}_decoy_{decoy:03d}.pdb';pose.dump_pdb(str(file))
            exported=p.pose_from_pdb(str(file));saved=float(sf(exported))
            rows.append({'method':method,'decoy':decoy,'seed':a.seed,'pdb':file.name,'sha256':digest(file),
                         'raw_pose_energy_REU':raw,'exported_pdb_energy_REU':saved,
                         'export_minus_raw_REU':saved-raw,
                         'movable_backbone':method=='FastRelax','movable_sidechains':True,'movable_jumps':False})
    result={'schema_version':'enzyme-core/repack-relax-comparison/v1','input_sha256':digest(a.input_pdb),
            'scorefxn':'ref2015','seed':a.seed,'nstruct_per_method':a.nstruct,'max_iter':a.max_iter,
            'relax_repeats':a.relax_repeats,'models':rows,
            'energy_coordinate_state':'exported_pdb_energy_REU is scored after reloading the supplied PDB; raw_pose_energy_REU describes the pre-export in-memory pose. Do not compare these as identical coordinate states.',
            'claim_ceiling':'Static model scores, identity and structural displacement only; no experimental stability or activity inference.'}
    (folder/'manifest.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')


def verify(a,p,sf):
    folder=Path(a.panel_dir);manifest=json.loads((folder/'manifest.json').read_text())
    if manifest['input_sha256']!=digest(a.input_pdb):raise ValueError('INPUT_HASH_MISMATCH')
    native=p.pose_from_pdb(a.input_pdb);original=backbone(native);checks=[];seen=[]
    for row in manifest['models']:
        file=Path(row['pdb'])
        if file.is_absolute() or '..' in file.parts:raise ValueError('UNSAFE_MODEL_PATH')
        file=folder/file;pose=p.pose_from_pdb(str(file));energy=float(sf(pose));observed=backbone(pose)
        missing=sorted(set(original)-set(observed));shift=max((math.dist(v,observed[k]) for k,v in original.items() if k in observed),default=0)
        error=abs(energy-float(row['exported_pdb_energy_REU']));identical=identity(native)==identity(pose)
        passed=digest(file)==row['sha256'] and identical and not missing and error<=1e-6
        if row['method']=='repack':passed=passed and shift<=.001 and not row['movable_backbone']
        elif row['method']=='FastRelax':passed=passed and row['movable_backbone'] and not row['movable_jumps']
        else:passed=False
        seen.append((row['method'],row['decoy']))
        checks.append({'method':row['method'],'decoy':row['decoy'],'identity_equal':identical,
                       'maximum_unaligned_backbone_shift_A':shift,'missing_backbone_atoms':missing,
                       'rescored_exported_pdb_energy_REU':energy,'rescore_error_REU':error,'pass':passed})
    n=a.nstruct
    coverage=len(seen)==2*n and set(seen)=={(m,d) for m in ['repack','FastRelax'] for d in range(1,n+1)}
    result={'verification_status':'PASS' if coverage and checks and all(x['pass'] for x in checks) else 'FAIL',
            'coverage_complete':coverage,'checks':checks,'energy_tolerance_REU':1e-6,
            'scope':'Identity, fixed repack backbone, explicit protocol degrees of freedom and direct rescore of identical exported PDB coordinates. No biochemical efficacy.'}
    output=Path(a.output);output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    if result['verification_status']!='PASS':raise SystemExit(2)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode',choices=['run','verify'],required=True)
    parser.add_argument('--input-pdb',required=True)
    parser.add_argument('--out-dir')
    parser.add_argument('--panel-dir')
    parser.add_argument('--output')
    parser.add_argument('--seed',type=int,default=1729)
    parser.add_argument('--nstruct',type=int,default=1)
    parser.add_argument('--max-iter',type=int,default=50)
    parser.add_argument('--relax-repeats',type=int,default=5)
    a=parser.parse_args()
    if a.seed<1:raise ValueError('INVALID_SEED')
    if a.mode=='run' and not a.out_dir:raise ValueError('OUT_DIR_REQUIRED')
    if a.mode=='verify' and not (a.panel_dir and a.output):raise ValueError('PANEL_AND_OUTPUT_REQUIRED')
    import pyrosetta as p
    p.init(f'-mute all -constant_seed -jran {a.seed}')
    sf=p.create_score_function('ref2015')
    (run if a.mode=='run' else verify)(a,p,sf)


if __name__=='__main__':main()
