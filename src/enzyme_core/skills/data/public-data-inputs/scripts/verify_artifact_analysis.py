"""Independent verification of derived analyses against unchanged input bytes."""
import argparse
import hashlib
import json
from pathlib import Path


def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify(args):
    value=json.loads(Path(args.summary_json).read_text());errors=[]
    if value.get('mode')=='prediction':
        import numpy as np
        root=Path(args.input_dir).resolve()
        expected_count=getattr(args,'expected_token_count',None)
        if value.get('expected_token_count')!=expected_count:errors.append('expected_token_count')
        expected={(p.relative_to(root).as_posix(),k) for p in root.rglob('*') if p.is_file() and p.suffix in {'.npz','.npy'} and (p.suffix=='.npz' or any(metric in p.name.lower() for metric in ['plddt','pae','pde'])) for k in keys(p) if any(metric in (p.name+' '+k).lower() for metric in ['plddt','pae','pde'])}
        seen=set()
        for row in value['arrays']:
            path=(root/row['path']).resolve()
            if root not in path.parents:raise ValueError('ARRAY_PATH_INVALID')
            seen.add((row['path'],row['key']))
            loaded=np.load(path,allow_pickle=False)
            try:
                a=loaded[row['key']] if hasattr(loaded,'files') else loaded
                flattened=a.reshape(-1);finite=flattened[np.isfinite(flattened)]
                actual={'sha256':digest(path),'shape':list(a.shape),'dtype':str(a.dtype),'size':int(a.size),'nonfinite_count':int(a.size-finite.size),'minimum':float(np.min(finite)) if finite.size else None,'maximum':float(np.max(finite)) if finite.size else None}
                for key,number in actual.items():
                    if row.get(key)!=number:errors.append(row['path']+':'+key)
                coverage=None if expected_count is None else bool(a.ndim and a.shape[-1]==expected_count and (row['field'] not in {'pae','pde'} or a.ndim>=2 and a.shape[-2]==expected_count))
                if row.get('token_axis_coverage')!=coverage:errors.append('token_axis_coverage:'+row['path'])
                if coverage is False:errors.append('array_shape_coverage:'+row['path'])
            finally:
                if hasattr(loaded,'close'):loaded.close()
        if seen!=expected or len(seen)!=len(value['arrays']):errors.append('array_coverage')
        if sorted({'pae','pde','plddt'}-{row['field'] for row in value['arrays']})!=value['missing_fields']:errors.append('missing_fields')
    elif value.get('mode')=='msa':
        records=[];seq=None
        for line in Path(args.msa).read_text().splitlines():
            if line.startswith('>'):
                seq=[];records.append(seq)
            elif line.strip():
                if seq is None:raise ValueError('MSA_HEADER_REQUIRED')
                seq.append(line.strip())
        aligned=[''.join(c for c in ''.join(r) if c==c.upper() and c!='.') for r in records]
        if len(aligned)!=value['record_count'] or len(aligned[0])!=value['aligned_length'] or digest(args.msa)!=value['msa_sha256']:errors.append('msa_identity')
        if len(value['columns'])!=len(aligned[0]):errors.append('column_coverage')
        for index,column in enumerate(value['columns']):
            gaps=sum(r[index]=='-' for r in aligned)
            if column['gap_count']!=gaps or abs(column['gap_fraction']-gaps/len(aligned))>1e-12:errors.append('column:'+str(index))
    else:
        root=Path(args.input_dir).resolve();actual={p.relative_to(root).as_posix():digest(p) for p in root.rglob('*') if p.is_file()}
        if {r['path']:r['sha256'] for r in value.get('members',[])}!=actual:errors.append('members')
    if value.get('mode')=='prediction':
        fields=set()
        for row in value['arrays']:
            label=(row['path']+' '+row['key']).lower()
            field='plddt' if 'plddt' in label else 'pae' if 'pae' in label else 'pde' if 'pde' in label else 'other'
            fields.add(field)
            if row['field']!=field or row['nonfinite_count'] or row['minimum'] is None or row['minimum']<0 or field=='plddt' and row['maximum']>100:errors.append('array_integrity:'+row['path'])
        if not {'pae','pde','plddt'}<=fields:errors.append('required_arrays_missing')
    if value.get('pass') is not True:errors.append('producer_not_pass')
    return {'schema_version':'enzyme-core/derived-analysis-verification/v1','pass':not errors,'differences':errors,'summary_sha256':digest(args.summary_json),'scope':'Independent artifact metrics; not structural accuracy, chemistry or activity validation.'}


def keys(path):
    import numpy as np
    a=np.load(path,allow_pickle=False)
    try:return list(a.files) if hasattr(a,'files') else ['array']
    finally:
        if hasattr(a,'close'):a.close()


def main():
    p=argparse.ArgumentParser();p.add_argument('--summary-json',required=True);p.add_argument('--input-dir');p.add_argument('--msa');p.add_argument('--expected-token-count',type=int);p.add_argument('--output-json',required=True)
    args=p.parse_args();value=verify(args);out=Path(args.output_json);out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(value,indent=2)+'\n')
    if not value['pass']:raise SystemExit(1)


if __name__=='__main__':main()
