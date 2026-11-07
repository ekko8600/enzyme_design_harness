"""Verify ProteinMPNN native/generated FASTA records against a fixed PDB backbone.

No design generation or activity inference. Standard residues and explicit PDB
chain/residue identifiers only; unsupported insertions and ambiguous inputs fail.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re

AA=dict(zip('ALA ARG ASN ASP CYS GLN GLU GLY HIS ILE LEU LYS MET PHE PRO SER THR TRP TYR VAL'.split(),'ARNDCQEGHILKMFPSTWYV'))


def fasta(path):
    rows=[]
    for line in Path(path).read_text().splitlines():
        if line.startswith('>'):rows.append([line[1:],''])
        elif line.strip():
            if not rows:raise ValueError('FASTA_SEQUENCE_BEFORE_HEADER')
            rows[-1][1]+=line.strip()
    if not rows or any(not s or not re.fullmatch('[ACDEFGHIKLMNPQRSTVWY/]+',s) for _,s in rows):
        raise ValueError('EMPTY_OR_UNSUPPORTED_FASTA')
    return rows


def verify(pdb, fasta_file, fixed_file, design_file, expected_count):
    if expected_count<1:raise ValueError('INVALID_EXPECTED_COUNT')
    chains={};identities={}
    for line in Path(pdb).read_text().splitlines():
        if not line.startswith('ATOM  ') or line[16] not in {' ','A'}:continue
        if line[26].strip():raise ValueError('INSERTION_CODE_REQUIRES_EXPLICIT_MAPPING')
        if line[17:20] not in AA:raise ValueError('UNSUPPORTED_RESIDUE')
        chain=line[21];number=int(line[22:26]);key=f'{chain}{number}'
        aa=AA[line[17:20]]
        if key in identities and identities[key]!=aa:raise ValueError('CONFLICTING_RESIDUE_IDENTITY')
        if key not in identities:chains.setdefault(chain,[]).append(key)
        identities[key]=aa
    if not identities:raise ValueError('EMPTY_PROTEIN')
    def residues(path):
        value=json.loads(Path(path).read_text())
        if not isinstance(value,list) or not value or not all(isinstance(x,str) for x in value) or len(value)!=len(set(value)):
            raise ValueError('INVALID_RESIDUE_LIST')
        if not set(value)<=identities.keys():raise ValueError('CONSTRAINT_RESIDUE_NOT_IN_PDB')
        return set(value)
    fixed=residues(fixed_file);design=residues(design_file)
    if fixed&design:raise ValueError('FIXED_DESIGN_OVERLAP')
    chain_order=list(chains);keys=[k for c in chain_order for k in chains[c]]
    native='/'.join(''.join(identities[k] for k in chains[c]) for c in chain_order)
    rows=fasta(fasta_file);issues=[]
    if len(rows)!=expected_count+1:issues.append('GENERATED_RECORD_COUNT')
    native_header=rows[0][0]
    if re.search(r'(?:^|,)\s*id\s*=',native_header) or rows[0][1]!=native:
        issues.append('NATIVE_RECORD_IDENTITY_OR_CHAIN_ORDER')
    checks=[];ids=set()
    for header,sequence in rows[1:]:
        match=re.search(r'(?:^|,)\s*id\s*=\s*(\d+)(?:,|$)',header)
        violations=[];mutations=[]
        if not match or match[1] in ids:violations.append('MISSING_OR_DUPLICATE_GENERATED_ID')
        if match:ids.add(match[1])
        parts=sequence.split('/')
        if len(parts)!=len(chains) or any(len(s)!=len(chains[c]) for s,c in zip(parts,chain_order)):
            violations.append('SEQUENCE_LENGTH_OR_CHAIN_BOUNDARY')
        else:
            for key,aa in zip(keys,sequence.replace('/','')):
                if identities[key]!=aa:
                    mutations.append({'residue':key,'native':identities[key],'generated':aa})
                    if key in fixed or key not in design:violations.append(key)
        checks.append({'id':match[1] if match else None,'header':header,'checked_residues':len(keys) if not any(x=='SEQUENCE_LENGTH_OR_CHAIN_BOUNDARY' for x in violations) else 0,
                       'mutations':mutations,'violations':violations,'pass':not violations})
    return {'constraint_status':'PASS' if not issues and checks and all(x['pass'] for x in checks) else 'FAIL',
            'native_identity_equal':rows[0][1]==native,'native_header':native_header,
            'chain_order':chain_order,'residue_count':len(keys),'expected_generated_count':expected_count,
            'observed_generated_count':len(rows)-1,'fixed_count':len(fixed),'design_count':len(design),
            'issues':issues,'generated_records':checks,
            'input_sha256':{name:hashlib.sha256(Path(path).read_bytes()).hexdigest() for name,path in
                            [('pdb',pdb),('fasta',fasta_file),('fixed',fixed_file),('design',design_file)]},
            'claim_ceiling':'Sequence identity and fixed/non-design position constraints only; no activity, folding or stability validation.'}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input-pdb',required=True)
    p.add_argument('--fasta-file',required=True)
    p.add_argument('--fixed-residues-file',required=True)
    p.add_argument('--design-residues-file',required=True)
    p.add_argument('--expected-count',type=int,required=True)
    p.add_argument('--output',required=True)
    a=p.parse_args()
    result=verify(a.input_pdb,a.fasta_file,a.fixed_residues_file,a.design_residues_file,a.expected_count)
    target=Path(a.output);target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    if result['constraint_status']!='PASS':raise SystemExit(2)


if __name__=='__main__':main()
