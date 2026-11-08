"""Bounded deterministic analyses of existing artifacts; never runs a producer."""
import argparse
import csv
import hashlib
import json
import math
from pathlib import Path


def checksum(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def prediction(args):
    import numpy as np
    root=Path(args.input_dir).resolve()
    if not root.is_dir():raise ValueError('PREDICTION_DIRECTORY_MISSING')
    rows=[]
    for path in sorted(root.rglob('*')):
        if not path.is_file() or path.suffix.lower() not in {'.npy','.npz'}:continue
        if path.suffix.lower()=='.npy' and not any(k in path.name.lower() for k in ['plddt','pae','pde']):continue
        if path.is_symlink() or root not in path.resolve().parents:raise ValueError('ARRAY_ESCAPES_INPUT')
        loaded=np.load(path,allow_pickle=False,mmap_mode='r' if path.suffix=='.npy' else None)
        try:
            arrays={k:loaded[k] for k in loaded.files if any(metric in (path.name+' '+k).lower() for metric in ['plddt','pae','pde'])} if hasattr(loaded,'files') else {'array':loaded}
            for key,value in arrays.items():
                if value.dtype.kind not in 'fiu':raise ValueError('NON_NUMERIC_ARRAY:'+str(path))
                if value.size>100_000_000:raise ValueError('ARRAY_SIZE_LIMIT')
                finite=np.isfinite(value);numbers=value[finite]
                label=(path.name+' '+key).lower()
                field='plddt' if 'plddt' in label else 'pae' if 'pae' in label else 'pde' if 'pde' in label else 'other'
                row={'path':path.relative_to(root).as_posix(),'sha256':checksum(path),'key':key,'field':field,
                     'shape':list(value.shape),'dtype':str(value.dtype),'size':int(value.size),
                     'nonfinite_count':int(value.size-finite.sum()),'minimum':float(numbers.min()) if numbers.size else None,
                     'maximum':float(numbers.max()) if numbers.size else None}
                row['range_valid']=bool(numbers.size and numbers.min()>=0 and (field!='plddt' or numbers.max()<=100))
                row['token_axis_coverage']=None if args.expected_token_count is None else bool(value.ndim and value.shape[-1]==args.expected_token_count and (field not in {'pae','pde'} or value.ndim>=2 and value.shape[-2]==args.expected_token_count))
                rows.append(row)
        finally:
            if hasattr(loaded,'close'):loaded.close()
    missing=sorted({'pae','pde','plddt'}-{r['field'] for r in rows})
    return {'arrays':rows,'missing_fields':missing,'pass':bool(rows) and not missing and all(r['nonfinite_count']==0 and r['range_valid'] and r['token_axis_coverage'] is not False for r in rows),
            'expected_token_count':args.expected_token_count,
            'coverage_status':'TOKEN_COUNT_CHECKED' if args.expected_token_count is not None else 'TOKEN_MAPPING_UNVERIFIED',
            'limitations':['Array integrity is not structural accuracy or ligand atom mapping. Protein/ligand coverage requires the producer token mapping.']}


def msa(args):
    counts=[];total=0;query=None;current=None
    def consume(seq):
        nonlocal total,query,counts
        # A3M insertion letters do not occupy query-aligned columns.
        aligned=''.join(c.upper() for c in seq if not c.islower() and c!='.')
        if query is None:query=aligned;counts=[0]*len(query)
        if len(aligned)!=len(query):raise ValueError('MSA_ALIGNED_LENGTH_MISMATCH')
        for i,c in enumerate(aligned):counts[i]+=int(c=='-')
        total+=1
    with Path(args.msa).open() as stream:
        for line in stream:
            line=line.strip()
            if line.startswith('>'):
                if current is not None:consume(''.join(current))
                current=[]
            elif line:
                if current is None:raise ValueError('MSA_HEADER_REQUIRED')
                current.append(line)
        if current is not None:consume(''.join(current))
    if not total or not query:raise ValueError('MSA_EMPTY')
    position=0;rows=[]
    for col,(residue,gaps) in enumerate(zip(query,counts),1):
        if residue!='-':position+=1
        rows.append({'column':col,'query_residue':residue,'query_position':position if residue!='-' else None,'gap_count':gaps,'gap_fraction':gaps/total,'coverage':1-gaps/total})
    return {'msa_sha256':checksum(args.msa),'record_count':total,'aligned_length':len(query),'query_sequence':query.replace('-',''),'columns':rows,'pass':True,'limitations':['Evolutionary coverage is not experimental activity evidence.']}


def collection(args):
    root=Path(args.input_dir).resolve()
    if not root.is_dir():raise ValueError('COLLECTION_DIRECTORY_MISSING')
    rows=[]
    for path in sorted(root.rglob('*')):
        if path.is_symlink():raise ValueError('COLLECTION_SYMLINK_FORBIDDEN')
        if path.is_file():rows.append({'path':path.relative_to(root).as_posix(),'sha256':checksum(path),'size_bytes':path.stat().st_size})
    return {'members':rows,'member_count':len(rows),'pass':bool(rows),'limitations':['Inventory does not establish scientific method execution or structural correctness.']}


def parser():
    p=argparse.ArgumentParser()
    p.add_argument('--mode',required=True,choices=['prediction','msa','collection'])
    p.add_argument('--input-dir')
    p.add_argument('--msa')
    p.add_argument('--expected-token-count',type=int)
    p.add_argument('--output-json',required=True)
    p.add_argument('--output-csv')
    return p


def main():
    p=parser();args=p.parse_args()
    if args.mode=='msa' and not args.msa or args.mode!='msa' and not args.input_dir:p.error('mode-specific input required')
    value={'schema_version':'enzyme-core/derived-artifact-analysis/v1','mode':args.mode,**{'prediction':prediction,'msa':msa,'collection':collection}[args.mode](args)}
    out=Path(args.output_json);out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(value,allow_nan=False,indent=2)+'\n')
    if args.output_csv:
        rows=value.get('arrays',value.get('columns',value.get('members',[])));target=Path(args.output_csv);target.parent.mkdir(parents=True,exist_ok=True)
        with target.open('w',newline='') as f:
            w=csv.DictWriter(f,fieldnames=list(rows[0]) if rows else ['empty']);w.writeheader();w.writerows(rows)
    if not value['pass']:raise SystemExit(1)


if __name__=='__main__':main()
