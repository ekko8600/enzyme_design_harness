"""Independent identity, coverage, backbone and arithmetic checks for mutation outputs.

Does not call Rosetta or certify score-function accuracy or biochemical effects.
"""
import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import re

AA=dict(zip('ALA ARG ASN ASP CYS GLN GLU GLY HIS ILE LEU LYS MET PHE PRO SER THR TRP TYR VAL'.split(),'ARNDCQEGHILKMFPSTWYV'))


def structure(path):
    residues={};atoms={}
    for line in Path(path).read_text().splitlines():
        if not line.startswith('ATOM  ') or line[16] not in {' ','A'}:continue
        key=(line[21],int(line[22:26]),line[26])
        if line[17:20] not in AA:raise ValueError('UNSUPPORTED_PROTEIN_RESIDUE')
        if key in residues and residues[key]!=AA[line[17:20]]:raise ValueError('RESIDUE_IDENTITY_CONFLICT')
        residues[key]=AA[line[17:20]]
        atom=(*key,line[12:16].strip())
        if atom in atoms:raise ValueError('DUPLICATE_ATOM')
        xyz=tuple(float(line[a:b]) for a,b in [(30,38),(38,46),(46,54)])
        if not all(math.isfinite(x) for x in xyz):raise ValueError('NONFINITE_COORDINATES')
        atoms[atom]=xyz
    if not residues:raise ValueError('EMPTY_STRUCTURE')
    return residues,atoms


def verify(input_pdb,table_file,expected_mutations_file,mode,nstruct=1,seed=1729,require_structures=False):
    original,atoms=structure(input_pdb);keys=list(original);native=''.join(original.values())
    expected=json.loads(Path(expected_mutations_file).read_text())
    if not isinstance(expected,list) or not expected or len(expected)!=len(set(expected)):
        raise ValueError('INVALID_EXPECTED_MUTATIONS')
    for mutant in expected:
        match=re.fullmatch(r'([ACDEFGHIKLMNPQRSTVWY])([1-9][0-9]*)([ACDEFGHIKLMNPQRSTVWY])',mutant)
        if not match or int(match[2])>len(native) or native[int(match[2])-1]!=match[1] or match[1]==match[3]:
            raise ValueError('EXPECTED_MUTATION_IDENTITY')
    if nstruct<1:raise ValueError('INVALID_NSTRUCT')
    with Path(table_file).open() as handle:rows=list(csv.DictReader(handle,delimiter='\t'))
    checks=[];observed=[];hashes={}
    for row in rows:
        errors=[];mutant=row.get('mutation');position=int(row['pose_position'])-1
        if row.get('status')!='ok':errors.append('BACKEND_FAILED')
        if mutant not in expected:errors.append('UNEXPECTED_MUTANT')
        if position<0 or position>=len(native):raise ValueError('RESULT_POSITION_OUT_OF_RANGE')
        if mutant!=native[position]+str(position+1)+row['mutant_aa'] or row['wt_aa']!=native[position]:errors.append('RESULT_IDENTITY')
        if mode=='paired-panel':
            decoy=int(row['decoy']);observed.append((mutant,decoy))
            if int(row['seed'])!=seed:errors.append('SEED')
            first,second,delta=[float(row[k]) for k in ['control_score','variant_score','delta_score']]
        else:
            observed.append((mutant,1));first,second,delta=[float(row[k]) for k in ['wt_score_local','mut_score','delta_score']]
        if not all(math.isfinite(x) for x in [first,second,delta]) or abs(delta-(second-first))>1e-8:errors.append('SCORE_ARITHMETIC')
        structure_checks=[]
        has_structures=bool(row.get('control_pdb') and row.get('variant_pdb'))
        if require_structures and not has_structures:errors.append('STRUCTURES_REQUIRED')
        if mode=='paired-panel' or has_structures:
            for role in ['control','variant']:
                relative=Path(row[role+'_pdb'])
                if relative.is_absolute() or '..' in relative.parts:raise ValueError('UNSAFE_RESULT_PATH')
                path=Path(table_file).parent/relative
                if not path.is_file():errors.append('MISSING_'+role.upper());continue
                residues,coords=structure(path)
                wanted=native if role=='control' else native[:position]+row['mutant_aa']+native[position+1:]
                identity=list(residues)==keys and ''.join(residues.values())==wanted
                backbone=[k for k in atoms if k[-1] in {'N','CA','C','O'}]
                missing=[k for k in backbone if k not in coords]
                max_shift=max((math.dist(atoms[k],coords[k]) for k in backbone if k in coords),default=0)
                if not identity:errors.append(role.upper()+'_IDENTITY')
                if missing or max_shift>.001:errors.append(role.upper()+'_BACKBONE_CHANGED')
                hashes[str(relative)]=hashlib.sha256(path.read_bytes()).hexdigest()
                structure_checks.append({'role':role,'identity_equal':identity,'missing_backbone_atoms':missing,'maximum_backbone_shift_A':max_shift})
        checks.append({'mutation':mutant,'decoy':observed[-1][1],'score_delta_arithmetic_error':abs(delta-(second-first)),
                       'structures':structure_checks,'errors':errors,'pass':not errors})
    wanted={(m,d) for m in expected for d in range(1,(nstruct if mode=='paired-panel' else 1)+1)}
    coverage=set(observed)==wanted and len(observed)==len(wanted)
    return {'verification_status':'PASS' if coverage and checks and all(c['pass'] for c in checks) else 'FAIL',
            'coverage_complete':coverage,'expected_rows':len(wanted),'observed_rows':len(rows),
            'missing':sorted(wanted-set(observed)),'checks':checks,'structure_sha256':hashes,
            'input_sha256':{name:hashlib.sha256(Path(path).read_bytes()).hexdigest() for name,path in
                            [('pdb',input_pdb),('table',table_file),('expected_mutations',expected_mutations_file)]},
            'scope':'Complete requested rows, mutation/control identity, fixed backbone and paired arithmetic only; no independent score-function or activity validation.'}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input-pdb',required=True)
    p.add_argument('--table-file',required=True)
    p.add_argument('--expected-mutations-file',required=True)
    p.add_argument('--mode',choices=['paired-panel','scan'],required=True)
    p.add_argument('--nstruct',type=int,default=1)
    p.add_argument('--seed',type=int,default=1729)
    p.add_argument('--require-structures',action='store_true')
    p.add_argument('--output',required=True)
    a=p.parse_args();r=verify(a.input_pdb,a.table_file,a.expected_mutations_file,a.mode,a.nstruct,a.seed,a.require_structures)
    path=Path(a.output);path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(r,indent=2,allow_nan=False)+'\n')
    if r['verification_status']!='PASS':raise SystemExit(2)


if __name__=='__main__':main()
