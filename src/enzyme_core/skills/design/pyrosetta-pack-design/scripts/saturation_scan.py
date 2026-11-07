"""Local-repack mutation scan retaining every generated WT and mutant structure.

Primary scores describe exported/reloaded PDB coordinates. Raw in-memory scores
are separate fields. No relaxation, dynamics or activity prediction is performed.
"""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import re


def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-pdb',required=True)
    parser.add_argument('--mutations-file',required=True,help='Frozen JSON list of WT-position-mutant strings, pose numbering')
    parser.add_argument('--out-dir',required=True)
    parser.add_argument('--seed',type=int,default=1729)
    parser.add_argument('--pack-radius',type=float,default=8.)
    a=parser.parse_args()
    if a.seed<1 or not 0<a.pack_radius<=20:raise ValueError('INVALID_SEED_OR_RADIUS')
    mutations=json.loads(Path(a.mutations_file).read_text())
    if not isinstance(mutations,list) or not mutations or not all(isinstance(x,str) for x in mutations) or len(mutations)!=len(set(mutations)):
        raise ValueError('INVALID_MUTATION_LIST')
    import pyrosetta as p
    from pyrosetta.toolbox import mutate_residue
    p.init(f'-mute all -constant_seed -jran {a.seed}')
    sf=p.create_score_function('ref2015');native=p.pose_from_pdb(a.input_pdb);sequence=native.sequence()
    for mutation in mutations:
        match=re.fullmatch(r'([ACDEFGHIKLMNPQRSTVWY])([1-9][0-9]*)([ACDEFGHIKLMNPQRSTVWY])',mutation)
        if not match or int(match[2])>len(sequence) or sequence[int(match[2])-1]!=match[1] or match[1]==match[3]:
            raise ValueError('MUTATION_IDENTITY_MISMATCH:'+mutation)
    folder=Path(a.out_dir);folder.mkdir(parents=True,exist_ok=True);structures=folder/'structures';structures.mkdir(exist_ok=True)
    if (folder/'scan.tsv').exists():raise ValueError('EXISTING_SCAN_PRESERVED')
    original_raw=float(sf(native));native_path=structures/'native.pdb';native.dump_pdb(str(native_path))
    global_score=float(sf(p.pose_from_pdb(str(native_path))))
    controls={};rows=[];hashes={native_path.relative_to(folder).as_posix():digest(native_path)}
    for mutation in mutations:
        position=int(mutation[1:-1]);wt=mutation[0];aa=mutation[-1]
        if position not in controls:
            control=native.clone();mutate_residue(control,position,wt,pack_radius=a.pack_radius)
            raw=float(sf(control));path=structures/f'control_{position}_seed_{a.seed}.pdb';control.dump_pdb(str(path))
            controls[position]=(path,float(sf(p.pose_from_pdb(str(path)))),raw)
            hashes[path.relative_to(folder).as_posix()]=digest(path)
        path,wt_score,wt_raw=controls[position]
        variant=native.clone();mutate_residue(variant,position,aa,pack_radius=a.pack_radius)
        raw=float(sf(variant));variant_path=structures/f'variant_{mutation}_seed_{a.seed}.pdb';variant.dump_pdb(str(variant_path))
        score=float(sf(p.pose_from_pdb(str(variant_path))))
        hashes[variant_path.relative_to(folder).as_posix()]=digest(variant_path)
        rows.append({'mutation':mutation,'pose_position':position,'wt_aa':wt,'mutant_aa':aa,'seed':a.seed,
                     'wt_score':global_score,'wt_score_local':wt_score,'mut_score':score,
                     'delta_score':score-wt_score,'delta_score_global':score-global_score,
                     'raw_wt_score_local':wt_raw,'raw_mut_score':raw,'raw_global_wt_score':original_raw,
                     'control_pdb':path.relative_to(folder).as_posix(),'variant_pdb':variant_path.relative_to(folder).as_posix(),
                     'status':'ok','error':''})
        temporary=folder/'scan.tsv.tmp'
        with temporary.open('w',newline='') as handle:
            writer=csv.DictWriter(handle,fieldnames=list(rows[0]),delimiter='\t');writer.writeheader();writer.writerows(rows)
        temporary.replace(folder/'scan.tsv')
    (folder/'manifest.json').write_text(json.dumps({'schema_version':'enzyme-core/retained-mutation-scan/v1',
        'input_sha256':digest(a.input_pdb),'mutations_sha256':digest(a.mutations_file),'seed':a.seed,
        'pack_radius_A':a.pack_radius,'scorefxn':'ref2015','relax':False,'rows':len(rows),'structure_sha256':hashes,
        'energy_coordinate_state':'Primary WT/mutant/delta scores describe exported/reloaded PDBs; raw in-memory scores are separate fields.',
        'claim_ceiling':'Local-repack model score differences (REU), not experimental ddG or enzyme activity.'},indent=2,allow_nan=False)+'\n')


if __name__=='__main__':main()
