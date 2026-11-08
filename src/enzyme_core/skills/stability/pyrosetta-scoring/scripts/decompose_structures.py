"""Enumerate every active Rosetta score term globally and for every residue."""
import argparse
import csv
import hashlib
import json
import math
from pathlib import Path


def decompose(input_dir, output_dir, scorefxn='ref2015', seed=1729):
    import pyrosetta as p
    p.init(f'-mute all -constant_seed -jran {int(seed)}')
    sf=p.create_score_function(scorefxn)
    options=sf.energy_method_options();options.hbond_options().decompose_bb_hb_into_pair_energies(True);sf.set_energy_method_options(options)
    scoring=p.rosetta.core.scoring
    terms=[(t,scoring.name_from_score_type(t),float(sf.weights()[t])) for t in sf.get_nonzero_weighted_scoretypes()]
    root=Path(input_dir);out=Path(output_dir)
    if root.resolve()==out.resolve() or root.resolve() in out.resolve().parents:raise ValueError('OUTPUT_INSIDE_FROZEN_INPUT')
    files=sorted(root.rglob('*.pdb'))
    if not files:raise ValueError('EMPTY_PDB_COLLECTION')
    out.mkdir(parents=True,exist_ok=True)
    models=[]
    fields=['model','scope','pose_position','chain','residue_number','insertion_code','resname','term','raw_energy','weight','weighted_energy','unit']
    with (out/'energy_terms.csv').open('w',newline='') as handle:
        writer=csv.DictWriter(handle,fieldnames=fields);writer.writeheader()
        for path in files:
            if path.is_symlink():raise ValueError('SYMLINK_MODEL_FORBIDDEN')
            pose=p.pose_from_pdb(str(path));total=float(sf(pose));info=pose.pdb_info()
            global_sum=0.;residue_sum=0.
            for position in range(0,pose.total_residue()+1):
                energies=pose.energies().total_energies() if position==0 else pose.energies().residue_total_energies(position)
                for term,name,weight in terms:
                    raw=float(energies[term]);weighted=raw*weight
                    if not math.isfinite(weighted):raise ValueError('NONFINITE_ENERGY')
                    if position==0:global_sum+=weighted
                    else:residue_sum+=weighted
                    writer.writerow(dict(model=path.relative_to(root).as_posix(),scope='global' if position==0 else 'residue',
                        pose_position=position,chain=info.chain(position) if position else '',residue_number=info.number(position) if position else '',
                        insertion_code=info.icode(position).strip() if position else '',resname=pose.residue(position).name3() if position else '',
                        term=name,raw_energy=raw,weight=weight,weighted_energy=weighted,unit='REU'))
            if abs(global_sum-total)>1e-6 or abs(residue_sum-total)>1e-6:raise ValueError(f'DECOMPOSITION_SUM_MISMATCH:{total}:{global_sum}:{residue_sum}')
            models.append(dict(model=path.relative_to(root).as_posix(),sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                residues=pose.total_residue(),total_REU=total,global_term_sum_REU=global_sum,residue_term_sum_REU=residue_sum))
    result=dict(schema_version='enzyme-core/energy-decomposition/v1',scorefxn=scorefxn,seed=seed,terms=[dict(term=n,weight=w) for _,n,w in terms],models=models,
        scope='All nonzero-weight terms of the saved PDB state; residue energies contain Rosetta allocated pair terms. REU is not experimental free energy or activity.')
    (out/'manifest.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-dir',required=True)
    parser.add_argument('--output-dir',required=True)
    parser.add_argument('--scorefxn',choices=['ref2015'],default='ref2015')
    parser.add_argument('--seed',type=int,default=1729)
    args=parser.parse_args();decompose(args.input_dir,args.output_dir,args.scorefxn,args.seed)


if __name__=='__main__':main()
