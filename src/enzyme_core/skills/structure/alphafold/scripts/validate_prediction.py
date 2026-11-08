"""Independently validate a predicted PDB sequence map and confidence arrays."""
import argparse
import hashlib
import json
import math
from pathlib import Path

AA3 = {
    "ALA":"A", "ARG":"R", "ASN":"N", "ASP":"D", "CYS":"C",
    "GLN":"Q", "GLU":"E", "GLY":"G", "HIS":"H", "ILE":"I",
    "LEU":"L", "LYS":"K", "MET":"M", "PHE":"F", "PRO":"P",
    "SER":"S", "THR":"T", "TRP":"W", "TYR":"Y", "VAL":"V",
}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_fasta(path):
    lines=Path(path).read_text().splitlines()
    seq=''.join(line.strip() for line in lines if line.strip() and not line.startswith('>')).upper()
    if not seq or any(x not in set('ACDEFGHIKLMNPQRSTVWY') for x in seq):
        raise ValueError('INVALID_FASTA_SEQUENCE')
    return seq


def pdb_residues(path):
    residues=[];seen=set()
    for line in Path(path).read_text().splitlines():
        if not line.startswith('ATOM  '):
            continue
        name=line[17:20].strip().upper();chain=line[21].strip();number=line[22:26].strip();icode=line[26].strip()
        key=(chain,number,icode)
        if key in seen:
            continue
        if name not in AA3:
            raise ValueError('NONSTANDARD_ATOM_RESIDUE:'+name)
        seen.add(key);residues.append({'chain':chain,'number':int(number),'insertion_code':icode,'resname':name,'aa':AA3[name]})
    if not residues:
        raise ValueError('PDB_HAS_NO_STANDARD_ATOM_RESIDUES')
    chains={r['chain'] for r in residues}
    if len(chains)!=1:
        raise ValueError('EXPECTED_SINGLE_CHAIN:'+','.join(sorted(chains)))
    return residues


def validate(pdb,fasta,scores,variant,parent_fasta=None):
    expected=read_fasta(fasta);residues=pdb_residues(pdb);observed=''.join(r['aa'] for r in residues)
    errors=[]
    if observed!=expected:errors.append('PDB_FASTA_SEQUENCE_MISMATCH')
    mutation=None
    if variant!='WT':
        import re
        m=re.fullmatch(r'([A-Z])([1-9][0-9]*)([A-Z])',variant)
        if not m or not parent_fasta:errors.append('INVALID_VARIANT_OR_PARENT_FASTA')
        else:
            before,pos,after=m.group(1),int(m.group(2)),m.group(3);parent=read_fasta(parent_fasta)
            if len(parent)!=len(expected) or pos>len(parent):errors.append('PARENT_FASTA_LENGTH_MISMATCH')
            elif parent[pos-1]!=before or expected[pos-1]!=after:errors.append('VARIANT_FASTA_MISMATCH')
            else:
                r=residues[pos-1]
                mutation={'variant':variant,'sequence_position':pos,'parent_residue':parent[pos-1],
                          'variant_residue':expected[pos-1],'chain':r['chain'],'pdb_residue_number':r['number'],
                          'insertion_code':r['insertion_code']}
    data=json.loads(Path(scores).read_text())
    plddt=data.get('plddt');pae=data.get('pae',data.get('predicted_aligned_error'));ptm=data.get('ptm')
    if not isinstance(plddt,list) or len(plddt)!=len(expected):errors.append('PLDDT_LENGTH_MISMATCH')
    elif any(not isinstance(x,(int,float)) or not math.isfinite(x) or not 0<=x<=100 for x in plddt):errors.append('PLDDT_VALUE_OUT_OF_RANGE')
    if not isinstance(pae,list) or len(pae)!=len(expected) or any(not isinstance(row,list) or len(row)!=len(expected) for row in pae):
        errors.append('PAE_DIMENSION_MISMATCH')
        pae_range=None
    else:
        values=[x for row in pae for x in row]
        if any(not isinstance(x,(int,float)) or not math.isfinite(x) or not 0<=x<=100 for x in values):errors.append('PAE_VALUE_OUT_OF_RANGE')
        pae_range={'minimum':min(values),'maximum':max(values),'elements':len(values)} if values else None
        if not values:errors.append('PAE_EMPTY')
    if not isinstance(ptm,(int,float)) or not math.isfinite(ptm) or not 0<=ptm<=1:errors.append('PTM_VALUE_OUT_OF_RANGE')
    result={'schema_version':'enzyme-core/prediction-validation/v1','pass':not errors,'errors':errors,'variant':variant,
        'method':'Exact ATOM-record standard-residue sequence mapping to frozen FASTA; full confidence-array dimension, finite-value, and range checks.',
        'inputs':{'pdb_sha256':digest(pdb),'fasta_sha256':digest(fasta),'scores_sha256':digest(scores),
                  'parent_fasta_sha256':digest(parent_fasta) if parent_fasta else None},
        'sequence':{'expected_length':len(expected),'observed_length':len(residues),'exact_match':observed==expected,
                    'chain':residues[0]['chain'],'residue_map':residues,'mutation':mutation},
        'confidence':{'plddt':{'length':len(plddt) if isinstance(plddt,list) else None,
                               'minimum':min(plddt) if isinstance(plddt,list) and plddt else None,
                               'maximum':max(plddt) if isinstance(plddt,list) and plddt else None,'range':'0..100'},
                      'ptm':ptm,'ptm_range':'0..1','pae':{'shape':[len(pae),len(pae[0])] if isinstance(pae,list) and pae and isinstance(pae[0],list) else None,
                             'range':pae_range,'allowed_range':'0..100'}},
        'claim_boundary':'Prediction sequence and confidence integrity only; no fold correctness, leakage-free generalization, or enzyme activity claim.'}
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--pdb',required=True);p.add_argument('--fasta',required=True);p.add_argument('--scores-file',required=True)
    p.add_argument('--variant',required=True);p.add_argument('--parent-fasta');p.add_argument('--output-json',required=True)
    a=p.parse_args();result=validate(a.pdb,a.fasta,a.scores_file,a.variant,a.parent_fasta)
    out=Path(a.output_json);out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    print(json.dumps({'pass':result['pass'],'errors':result['errors'],'output':str(out)}))
    if not result['pass']:raise SystemExit(2)


if __name__=='__main__':main()
