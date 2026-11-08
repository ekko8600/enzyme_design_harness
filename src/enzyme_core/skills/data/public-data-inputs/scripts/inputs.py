"""Task-local public input retrieval and factual structure inspection."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import re
import time
import warnings
import urllib.parse
import urllib.request
from html.parser import HTMLParser

FORMATS = {'sdf', 'smi', 'pdb', 'cif', 'fasta', 'json', 'txt', 'html'}

def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def local(path):
    value = Path(path).resolve()
    value.relative_to(Path.cwd().resolve())
    value.parent.mkdir(parents=True, exist_ok=True)
    return value

class Text(HTMLParser):
    def __init__(self): super().__init__(); self.parts=[]; self.hidden=0
    def handle_starttag(self, tag, attrs):
        if tag in {'script','style'}: self.hidden+=1
    def handle_endtag(self, tag):
        if tag in {'script','style'}: self.hidden=max(0,self.hidden-1)
        elif tag in {'p','div','li','br','h1','h2','h3'}: self.parts.append('\n')
    def handle_data(self, data):
        if not self.hidden: self.parts.append(data)

class Redirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, url):
        if urllib.parse.urlsplit(url).scheme != 'https': raise ValueError('HTTPS_REDIRECT_REQUIRED')
        return super().redirect_request(req,fp,code,msg,headers,url)

def fetch(url, limit):
    parsed=urllib.parse.urlsplit(url)
    if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('PUBLIC_HTTPS_URL_REQUIRED')
    request=urllib.request.Request(url,headers={'User-Agent':'enzyme-core/public-inputs/0.1','Accept':'*/*'})
    with urllib.request.build_opener(Redirect()).open(request,timeout=60) as response:
        length=response.headers.get('Content-Length')
        if length and int(length)>limit: raise ValueError('DOWNLOAD_SIZE_LIMIT')
        raw=response.read(limit+1)
        if len(raw)>limit: raise ValueError('DOWNLOAD_SIZE_LIMIT')
        if not raw: raise ValueError('EMPTY_DOWNLOAD')
        return raw, response.url, response.headers.get('Content-Type',''), response.status

def retrieve(args):
    url=args.url
    fixed={'fetch_pdb':'pdb','fetch_uniprot':'fasta','fetch_alphafold':'pdb'}.get(args.operation)
    if fixed and args.format and args.format!=fixed: raise ValueError('DATABASE_FORMAT_CONFLICT')
    if args.operation=='fetch_pdb':
        if not re.fullmatch(r'[A-Za-z0-9]{4}',args.identifier or ''): raise ValueError('PDB_IDENTIFIER_INVALID')
        url='https://files.rcsb.org/download/'+args.identifier.upper()+'.pdb'; args.format='pdb'
    elif args.operation=='fetch_uniprot':
        if not re.fullmatch(r'[A-Z0-9]+(?:-\d+)?',args.identifier or ''): raise ValueError('UNIPROT_IDENTIFIER_INVALID')
        url='https://rest.uniprot.org/uniprotkb/'+args.identifier+'.fasta'; args.format='fasta'
    elif args.operation=='fetch_alphafold':
        if not re.fullmatch(r'[A-Z0-9]+',args.identifier or ''): raise ValueError('UNIPROT_IDENTIFIER_INVALID')
        metadata,_,_,_=fetch('https://alphafold.ebi.ac.uk/api/prediction/'+args.identifier,args.max_bytes)
        records=json.loads(metadata)
        if len(records)!=1: raise ValueError('ALPHAFOLD_CANDIDATE_SELECTION_REQUIRED')
        url=records[0]['pdbUrl'];args.format='pdb'
        local('database_metadata.json').write_bytes(metadata)
    raw,final_url,content_type,status=fetch(url,args.max_bytes)
    source_sha256=hashlib.sha256(raw).hexdigest();source_size_bytes=len(raw)
    if args.format not in FORMATS: raise ValueError('EXPLICIT_FORMAT_REQUIRED')
    if args.format=='html':
        parser=Text();parser.feed(raw.decode('utf-8')); raw=''.join(parser.parts).encode()
    if args.format=='json': json.loads(raw)
    elif args.format=='fasta':
        text=raw.decode('utf-8')
        if not text.startswith('>') or not ''.join(text.splitlines()[1:]).strip(): raise ValueError('FASTA_INVALID')
    elif args.format=='pdb':
        if not any(line.startswith(('ATOM  ','HETATM')) for line in raw.decode('utf-8').splitlines()): raise ValueError('PDB_COORDINATES_MISSING')
    elif args.format in {'sdf','smi'}:
        text=raw.decode('utf-8')
        if args.format=='sdf' and '$$$$' not in text: raise ValueError('SDF_INVALID')
        if '<html' in text.lower() or '<!doctype' in text.lower(): raise ValueError('MOLECULE_FILE_IS_HTML')
    output=local(args.output);output.write_bytes(raw)
    provenance={'schema_version':'enzyme-core/retrieval/v1','operation':args.operation,'identifier':args.identifier,
      'original_url':url,'final_url':final_url,'retrieved_at':time.time(),'content_type':content_type,'http_status':status,
      'source_sha256':source_sha256,'source_size_bytes':source_size_bytes,'format':args.format,'size_bytes':len(raw),'sha256':sha(output),'source_kind':'DATABASE_RECORD' if args.operation!='fetch_url' else 'PUBLIC_RESOURCE',
      'transform':'html_to_text' if args.format=='html' else 'none','scientific_validation':'NOT_VALIDATED'}
    local(args.provenance).write_text(json.dumps(provenance,ensure_ascii=False,indent=2))

def inspect(args):
    from Bio.PDB import PDBParser, MMCIFParser
    from Bio.SeqUtils import seq1
    from Bio import Align, SeqIO
    source=Path(args.structure)
    parser=MMCIFParser(QUIET=True) if source.suffix.lower() in {'.cif','.mmcif'} else PDBParser(QUIET=True)
    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter('always');parser.QUIET=False
        structure=parser.get_structure('input',str(source))
    models=list(structure)
    if not models: raise ValueError('STRUCTURE_EMPTY')
    target=None
    if args.sequence:
        records=list(SeqIO.parse(args.sequence,'fasta'))
        if len(records)!=1: raise ValueError('SINGLE_TARGET_SEQUENCE_REQUIRED')
        target=str(records[0].seq).upper()
        if not target: raise ValueError('TARGET_SEQUENCE_EMPTY')
        if len(target)>10000: raise ValueError('TARGET_SEQUENCE_TOO_LARGE_FOR_ALIGNMENT')
    chains=[]
    for chain in models[0]:
        residues=[r for r in chain if r.id[0]==' ']
        observed=''.join(seq1(r.resname,undef_code='X') for r in residues)
        item={'chain':chain.id,'residue_count':len(residues),'sequence':observed,
              'residue_ids':[str(r.id[1])+r.id[2].strip() for r in residues],
              'hetero_groups':[{'name':r.resname,'residue_id':str(r.id[1]),'atom_count':len(r)} for r in chain if r.id[0]!=' '],
              'nonstandard_residues':[{'name':r.resname,'residue_id':str(r.id[1])} for r in residues if seq1(r.resname,undef_code='X')=='X']}
        if target:
            aligner=Align.PairwiseAligner();aligner.mode='global';aligner.match_score=2;aligner.mismatch_score=-1;aligner.open_gap_score=-2;aligner.extend_gap_score=-.5
            item['target_exact_match']=observed==target
            item['target_length']=len(target)
            item['alignment']=str(aligner.align(target,observed)[0]) if observed else ''
        chains.append(item)
    if not any(c['residue_count'] for c in chains): raise ValueError('PROTEIN_RESIDUES_MISSING')
    result={'schema_version':'enzyme-core/structure-input/v1','structure_sha256':sha(source),'sequence_sha256':sha(args.sequence) if args.sequence else None,
      'model_count':len(models),'parser_warnings':[str(w.message) for w in recorded],'chains':chains,'limitations':['Observed residues only; absent residues are not rebuilt.','Identity facts do not establish MD applicability or force-field support.']}
    local(args.output).write_text(json.dumps(result,ensure_ascii=False,indent=2))

def main():
    p=argparse.ArgumentParser();p.add_argument('--operation',required=True,choices=['fetch_url','fetch_pdb','fetch_uniprot','fetch_alphafold','inspect_structure'])
    p.add_argument('--url');p.add_argument('--identifier');p.add_argument('--format',choices=sorted(FORMATS));p.add_argument('--output',required=True)
    p.add_argument('--provenance',default='retrieval.json');p.add_argument('--max-bytes',type=int,default=32*1024*1024)
    p.add_argument('--structure');p.add_argument('--sequence');args=p.parse_args()
    if args.max_bytes<1: p.error('max-bytes must be positive')
    if args.operation=='inspect_structure': inspect(args)
    else: retrieve(args)
if __name__=='__main__': main()
