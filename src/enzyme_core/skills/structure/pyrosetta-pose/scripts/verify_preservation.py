"""Audit original atoms, components, numbering and explicitly declared bonds.

Geometry never establishes chemical state. Additional atoms are listed, not hidden.
"""
import argparse
import collections
import hashlib
import json
import math
from pathlib import Path

AA=dict(zip(['ALA','ARG','ASN','ASP','CYS','GLN','GLU','GLY','HIS','ILE','LEU','LYS','MET','PHE','PRO','SER','THR','TRP','TYR','VAL'], 'ARNDCQEGHILKMFPSTWYV'))


def read(path):
    atoms={};residues={}
    for line in Path(path).read_text().splitlines():
        if line[:6].strip() not in {'ATOM','HETATM'} or line[16] not in {' ','A'}:continue
        residue=(line[21],int(line[22:26]),line[26].strip())
        name=line[12:16].strip();key=(*residue,name)
        if key in atoms:raise ValueError('DUPLICATE_ATOM_KEY:'+str(key))
        xyz=tuple(float(line[a:b]) for a,b in [(30,38),(38,46),(46,54)])
        if not all(math.isfinite(x) for x in xyz):raise ValueError('NONFINITE_COORDINATE')
        element=(line[76:78].strip() or name.lstrip('0123456789')[:1]).upper()
        atoms[key]=(element,xyz);residues[residue]=line[17:20].strip()
    if not atoms:raise ValueError('EMPTY_STRUCTURE')
    return atoms,residues


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def compare(source,candidate,tolerance=.001,mapping=None,required_bonds=None,sequence_file=None):
    if not math.isfinite(tolerance) or tolerance<0:raise ValueError('INVALID_TOLERANCE')
    before,first=read(source);after,second=read(candidate)
    missing=[];elements=[];errors=[];changed=[];preserved=[];heavy=[]
    for key,(element,xyz) in before.items():
        if key not in after:missing.append(key);continue
        distance=math.dist(xyz,after[key][1]);errors.append(distance)
        if element not in {'H','D'}:heavy.append(distance)
        if after[key][0]!=element:elements.append(key)
        if distance>tolerance or after[key][0]!=element:changed.append({'atom':key,'distance_A':distance,'before_element':element,'after_element':after[key][0]})
        else:preserved.append(key)
    mapping_errors=[];mapping_rows=[]
    sequence=''.join(line.strip() for line in Path(sequence_file).read_text().splitlines() if not line.startswith('>')) if sequence_file else None
    pose_index={key:i+1 for i,key in enumerate(first)}
    if mapping:
        rows=json.loads(Path(mapping).read_text())
        if not isinstance(rows,list):raise ValueError('MAPPING_LIST_REQUIRED')
        seen=set();pose_seen=set()
        for row in rows:
            key=(row['chain'],int(row['residue_number']),row.get('insertion_code','').strip())
            pose=(row['chain'],row.get('pose_position'))
            if key in seen or pose in pose_seen:mapping_errors.append({'residue':key,'error':'DUPLICATE_MAPPING'})
            seen.add(key);pose_seen.add(pose)
            aa=row.get('structure_aa') or row.get('assay_aa')
            ok=key in first and key in second and first[key]==second[key] and (not aa or AA.get(first[key],first[key])==aa)
            if row.get('pose_position')!=pose_index.get(key):ok=False
            if sequence:
                position=row.get('assay_position')
                if type(position) is not int or not 1<=position<=len(sequence) or sequence[position-1]!=row.get('assay_aa') or row.get('assay_aa')!=aa:ok=False
            if not ok:mapping_errors.append({'residue':key,'error':'MAPPING_IDENTITY_MISMATCH'})
            mapping_rows.append({'residue':key,'pose_position':row.get('pose_position'),'assay_position':row.get('assay_position'),'pass':ok})
        if seen != set(first):mapping_errors.append({'error':'MAPPING_COVERAGE_MISMATCH','unmapped_residues':sorted(set(first)-seen),'unknown_residues':sorted(seen-set(first))})
    bonds=[]
    if required_bonds:
        declarations=json.loads(Path(required_bonds).read_text())
        for pair in declarations:
            a=tuple(pair['a']);b=tuple(pair['b'])
            present=all(k in before and k in after for k in [a,b])
            original=math.dist(before[a][1],before[b][1]) if present else None
            observed=math.dist(after[a][1],after[b][1]) if present else None
            bonds.append({'a':a,'b':b,'kind':pair.get('kind','DECLARED_COVALENT'),'declared_source':pair.get('source','UNSPECIFIED'),
                'original_distance_A':original,'candidate_distance_A':observed,'pass':present and abs(observed-original)<=2*tolerance,
                'chemical_state':'DECLARATION_NOT_INFERRED_FROM_DISTANCE'})
    annotations=lambda p:[line for line in Path(p).read_text().splitlines() if line.startswith(('SSBOND','LINK  ','CONECT'))]
    sg=sorted(k for k in before if k[-1]=='SG');geometry=[]
    for i,a in enumerate(sg):
        for b in sg[i+1:]:
            if math.dist(before[a][1],before[b][1])<=2.5:
                geometry.append({'a':a,'b':b,'distance_A':math.dist(before[a][1],before[b][1]),'interpretation':'GEOMETRIC_NEIGHBOR_ONLY'})
    passed=not missing and not elements and first==second and len(errors)==len(before) and max(errors,default=0)<=tolerance and not mapping_errors and all(b['pass'] for b in bonds)
    return {'schema_version':'enzyme-core/preservation/v2','preservation_status':'PASS' if passed else 'FAIL',
        'input_sha256':sha(source),'candidate_sha256':sha(candidate),'mapping_sha256':sha(mapping) if mapping else None,
        'required_bonds_sha256':sha(required_bonds) if required_bonds else None,'sequence_sha256':sha(sequence_file) if sequence_file else None,
        'original_atom_count':len(before),'candidate_atom_count':len(after),
        'original_heavy_atom_count':sum(x[0] not in {'H','D'} for x in before.values()),'compared_heavy_atom_count':len(heavy),
        'missing_original_heavy_atoms':[k for k in missing if before[k][0] not in {'H','D'}],
        'missing_original_atoms':missing,'element_mismatches':elements,'changed_atoms':changed,'preserved_atoms':preserved,
        'added_atoms':sorted(set(after)-set(before)),'residue_identity_and_numbering_equal':first==second,
        'component_identity_equal':collections.Counter(first.values())==collections.Counter(second.values()),
        'original_components':dict(collections.Counter(first.values())),'candidate_components':dict(collections.Counter(second.values())),
        'maximum_original_atom_coordinate_error_A':max(errors,default=0),'maximum_original_heavy_coordinate_error_A':max(heavy,default=0),
        'mapping_status':'PASS' if mapping and not mapping_errors else 'FAIL' if mapping_errors else 'NOT_PROVIDED',
        'mapping_rows':mapping_rows,'mapping_errors':mapping_errors,'declared_bonds':bonds,
        'input_connectivity_annotations':annotations(source),'candidate_connectivity_annotations':annotations(candidate),
        'disulfide_geometry':geometry,'undeclared_chemical_state':'UNVERIFIED',
        'altloc_policy':'Blank/A only; duplicate atom keys rejected; other conformers are excluded explicitly.',
        'tolerance_A':tolerance,'claim_ceiling':'Static preservation of supplied atom identities and declared geometry; no inference of chemical state, native fold, assembly or activity.'}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input-pdb',required=True);p.add_argument('--candidate-pdb',required=True)
    p.add_argument('--output',required=True);p.add_argument('--tolerance',type=float,default=.001)
    p.add_argument('--sequence-file',help='Frozen assay WT FASTA for mapping validation')
    p.add_argument('--mapping',help='Complete chain/residue/insertion/pose/assay mapping JSON')
    p.add_argument('--required-bonds',help='Explicit atom-pair declarations JSON; geometry is not bond inference')
    args=p.parse_args();result=compare(args.input_pdb,args.candidate_pdb,args.tolerance,args.mapping,args.required_bonds,args.sequence_file)
    path=Path(args.output);path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    if result['preservation_status']!='PASS':raise SystemExit(2)


if __name__=='__main__':main()
