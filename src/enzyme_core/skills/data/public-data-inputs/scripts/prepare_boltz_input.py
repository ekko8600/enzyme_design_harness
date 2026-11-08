"""Bind a frozen sequence and MSA to an explicit existing chemical template."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import yaml


def sequence(path, a3m=False):
    records=[];current=None
    for line in Path(path).read_text().splitlines():
        if line.startswith('>'):
            if current is not None:records.append(current)
            current=''
        elif current is not None:current+=line.strip()
    if current is not None:records.append(current)
    if not records or not a3m and len(records)!=1:raise ValueError('SEQUENCE_RECORD_COUNT_INVALID')
    value=re.sub(r'[a-z.\-]','',records[0]) if a3m else records[0]
    if not value or not re.fullmatch('[ACDEFGHIKLMNPQRSTVWY]+',value):raise ValueError('SEQUENCE_ALPHABET_INVALID')
    return value


def prepare(args):
    seq=sequence(args.fasta)
    if sequence(args.msa,a3m=True)!=seq:raise ValueError('FASTA_MSA_QUERY_MISMATCH')
    data=yaml.safe_load(Path(args.template).read_text())
    proteins=[s['protein'] for s in data['sequences'] if 'protein' in s]
    if len(proteins)!=1:raise ValueError('AMBIGUOUS_PROTEIN_IDENTITY')
    if data.get('templates'):raise ValueError('TEMPLATES_NOT_ALLOWED')
    before=proteins[0]['sequence'];proteins[0]['sequence']=seq;proteins[0]['msa']=str(Path(args.msa).resolve())
    out=Path(args.output);out.parent.mkdir(parents=True,exist_ok=True);out.write_text(yaml.safe_dump(data,sort_keys=False))
    result={'status':'PREPARED','sequence_changed':before!=seq,'sequence_length':len(seq),
            'chemical_components_preserved':True,'input_hashes':{k:hashlib.sha256(Path(getattr(args,k)).read_bytes()).hexdigest() for k in ('fasta','msa','template')},
            'output_sha256':hashlib.sha256(out.read_bytes()).hexdigest()}
    Path(args.manifest).parent.mkdir(parents=True,exist_ok=True);Path(args.manifest).write_text(json.dumps(result,indent=2)+'\n')
    return result


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--template',required=True)
    p.add_argument('--fasta',required=True)
    p.add_argument('--msa',required=True)
    p.add_argument('--output',required=True)
    p.add_argument('--manifest',required=True)
    prepare(p.parse_args())


if __name__=='__main__':main()
