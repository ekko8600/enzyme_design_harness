"""Bounded read-only checks of operation outputs, not scientific recalculation."""
import csv
import hashlib
from html.parser import HTMLParser
import json
import math
from pathlib import Path
import re


def _require(condition,code):
    if not condition:raise ValueError(code)


class _Document(HTMLParser):
    def __init__(self):
        super().__init__();self.lang=None;self.text=[];self.hidden=0
    def handle_starttag(self,tag,attrs):
        if tag=='html':self.lang=dict(attrs).get('lang')
        if tag in {'style','script'}:self.hidden+=1
    def handle_endtag(self,tag):
        if tag in {'style','script'}:self.hidden=max(0,self.hidden-1)
    def handle_data(self,data):
        if not self.hidden:self.text.append(data)


def _html(inputs,outputs,parameters):
    source=Path(inputs['input']).read_text() if inputs.get('input') else str(parameters.get('body',''))
    original=_Document();original.feed(source)
    expected=parameters.get('lang') or original.lang
    if not expected or expected=='auto':expected='zh-CN' if re.search(r'[\u3400-\u9fff]',str(parameters.get('title',''))+' '.join(original.text)) else 'en'
    files=[Path(p) for p in outputs.values() if Path(p).suffix.lower() in {'.html','.htm'}]
    _require(bool(files),'HTML_OUTPUT_MISSING')
    for path in files:
        doc=_Document();doc.feed(path.read_text());_require(doc.lang==expected,'HTML_LANGUAGE_MISMATCH:'+str(doc.lang)+':expected='+expected)
    return {'language':expected,'documents':len(files),'scope':'Document language and real content only; rendering does not validate scientific claims.'}


def _decomposition(inputs,outputs,parameters):
    _require('input_dir' in inputs,'DECOMPOSITION_INPUT_REQUIRED')
    source=Path(inputs['input_dir']).resolve()
    directories=[Path(p) for p in outputs.values() if Path(p).is_dir() and (Path(p)/'manifest.json').is_file()]
    _require(len(directories)==1,'DECOMPOSITION_COLLECTION_REQUIRED');out=directories[0]
    info=json.loads((out/'manifest.json').read_text());_require(info.get('schema_version')=='enzyme-core/energy-decomposition/v1','DECOMPOSITION_SCHEMA')
    for field in ['scorefxn','seed']:
        if field in parameters:_require(info[field]==parameters[field],'DECOMPOSITION_PARAMETER_MISMATCH:'+field)
    models={m['model']:m for m in info['models']};weights={t['term']:t['weight'] for t in info['terms']}
    _require(bool(models) and len(models)==len(info['models']),'DECOMPOSITION_DUPLICATE_OR_EMPTY_MODEL')
    _require(bool(weights) and len(weights)==len(info['terms']),'DECOMPOSITION_DUPLICATE_OR_EMPTY_TERM')
    files={p.relative_to(source).as_posix():p for p in source.rglob('*.pdb')}
    _require(set(models)==set(files),'DECOMPOSITION_MODEL_COVERAGE')
    pdb_residues={};pdb_atoms={}
    for name,m in models.items():
        path=files[name];_require(not path.is_symlink(),'DECOMPOSITION_SYMLINK_INPUT')
        _require(hashlib.sha256(path.read_bytes()).hexdigest()==m['sha256'],'DECOMPOSITION_INPUT_HASH:'+name)
        residues=set();atoms=set()
        for line in path.read_text().splitlines():
            if line.startswith(('ATOM  ','HETATM')) and len(line)>=27:
                identity=(line[21].strip(),str(int(line[22:26])),line[26].strip(),line[17:20].strip())
                residues.add(identity)
                if line.startswith('ATOM  '):atoms.add(identity[:3])
        pdb_residues[name]=residues;pdb_atoms[name]=atoms
    class Sum:
        def __init__(self):self.total=0.;self.correction=0.
        def add(self,value):
            total=self.total+value
            self.correction+=((self.total-total)+value) if abs(self.total)>=abs(value) else ((value-total)+self.total)
            self.total=total
        def value(self):return self.total+self.correction
    term_index={term:i for i,term in enumerate(weights)};term_count=len(weights)
    # One bit per expected model/position/term, rather than retaining every
    # row as Python tuples and floats. Memory grows with input size, not object count.
    seen={};expected_rows=0;csv_size=(out/'energy_terms.csv').stat().st_size
    for name,m in models.items():
        residues=m['residues']
        _require(isinstance(residues,int) and not isinstance(residues,bool) and 0<residues<=len(pdb_residues[name]),'DECOMPOSITION_RESIDUE_COUNT')
        expected_rows+=(residues+1)*term_count
        # Each CSV row needs at least one byte; reject impossible manifest
        # dimensions before allocating the compact verification bitmap.
        _require(expected_rows<=csv_size,'DECOMPOSITION_COUNT_TOO_LARGE')
        seen[name]=bytearray(((residues+1)*term_count+7)//8)
    sums={(name,scope):Sum() for name in models for scope in ['global','residue']};counts={(name,scope):0 for name in models for scope in ['global','residue']}
    positions={name:{} for name in models};rows=0;max_weight_error=0.
    with (out/'energy_terms.csv').open(newline='') as stream:
        reader=csv.DictReader(stream)
        needed={'model','scope','pose_position','chain','residue_number','insertion_code','resname','term','raw_energy','weight','weighted_energy','unit'}
        _require(needed.issubset(reader.fieldnames or []),'DECOMPOSITION_CSV_FIELDS')
        for row in reader:
            name,scope,term=row['model'],row['scope'],row['term']
            _require(name in models and scope in {'global','residue'} and term in weights,'DECOMPOSITION_ROW_IDENTITY')
            position=int(row['pose_position'])
            _require((scope=='global' and position==0) or (scope=='residue' and 1<=position<=models[name]['residues']),'DECOMPOSITION_POSE_POSITION')
            index=position*term_count+term_index[term];byte,bit=divmod(index,8);mask=1<<bit
            _require(not seen[name][byte]&mask,'DECOMPOSITION_DUPLICATE_ROW');seen[name][byte]|=mask
            raw,weight,value=map(float,[row['raw_energy'],row['weight'],row['weighted_energy']])
            _require(all(math.isfinite(x) for x in [raw,weight,value]),'DECOMPOSITION_NONFINITE')
            _require(row['unit']=='REU' and weight==weights[term],'DECOMPOSITION_UNIT_OR_WEIGHT')
            error=abs(raw*weight-value);max_weight_error=max(max_weight_error,error);_require(error<=1e-8,'DECOMPOSITION_WEIGHTED_VALUE')
            if scope=='global':_require(position==0,'DECOMPOSITION_GLOBAL_POSITION')
            else:
                _require(1<=position<=models[name]['residues'],'DECOMPOSITION_POSE_POSITION')
                identity=(row['chain'].strip(),row['residue_number'],row['insertion_code'].strip(),row['resname'])
                _require(identity in pdb_residues[name],'DECOMPOSITION_PDB_MAPPING')
                _require(positions[name].get(position,identity)==identity,'DECOMPOSITION_INCONSISTENT_MAPPING')
                positions[name][position]=identity
            sums[name,scope].add(value);counts[name,scope]+=1;rows+=1
    max_sum_error=0.
    for name,m in models.items():
        _require(len(positions[name])==m['residues'] and len(set(positions[name].values()))==m['residues'],'DECOMPOSITION_RESIDUE_COVERAGE')
        _require(pdb_atoms[name].issubset({identity[:3] for identity in positions[name].values()}),'DECOMPOSITION_MISSING_PDB_RESIDUES')
        for scope,field,count in [('global','global_term_sum_REU',len(weights)),('residue','residue_term_sum_REU',len(weights)*m['residues'])]:
            _require(counts[name,scope]==count,'DECOMPOSITION_TERM_COVERAGE')
            total=sums[name,scope].value();error=max(abs(total-m[field]),abs(total-m['total_REU']))
            _require(math.isfinite(error) and error<=1e-6,'DECOMPOSITION_TOTAL_MISMATCH');max_sum_error=max(max_sum_error,error)
    return {'models':len(models),'active_terms':len(weights),'csv_rows':rows,'input_hashes_match':True,'pdb_residue_mapping_checked':True,'all_pdb_atom_residues_covered':True,'max_weighted_energy_error':max_weight_error,'max_sum_error_REU':max_sum_error,'scope':'All rows, input identities, PDB residue mapping, weights and sums. No independent Rosetta energy recomputation, biochemical validation or experimental claim.'}


def verify_operation_output(skill_id,operation,*,inputs,outputs,parameters=None):
    """Verify supported output semantics; unsupported methods stay explicit."""
    verifier={('pyrosetta-scoring','decompose_structures'):_decomposition,('html-report-renderer','render_html'):_html}.get((skill_id,operation))
    if verifier is None:return {'status':'NOT_SUPPORTED','scope':'No operation-specific verifier registered; independent review remains required.'}
    try:return {'status':'PASS','checks':verifier(inputs,outputs,parameters or {})}
    except (ValueError,KeyError,TypeError,OSError,OverflowError,csv.Error) as exc:return {'status':'FAIL','error':str(exc),'scope':'Output consistency only; does not change process receipts.'}
