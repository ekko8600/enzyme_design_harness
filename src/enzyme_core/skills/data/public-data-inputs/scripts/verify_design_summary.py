"""Independent recomputation of design-summary invariants; does not import producer code."""
import argparse
import hashlib
import itertools
import json
import math
import re
from pathlib import Path


def verify(args):
    report=json.loads(Path(args.summary_json).read_text());issues=[]
    sequences=[];names=[]
    for block in Path(args.sequences_fasta).read_text().split('>')[1:]:
        lines=block.splitlines();names.append(lines[0]);sequences.append(''.join(lines[1:]).strip())
    fixed=json.loads(Path(args.fixed_file).read_text());design=json.loads(Path(args.design_file).read_text())
    rule=json.loads(Path(args.context_rule_file).read_text());chain=rule['protein_chain']
    # Independent record grouping, residue mapping and geometric distance evaluation.
    amino={'ALA':'A','CYS':'C','ASP':'D','GLU':'E','PHE':'F','GLY':'G','HIS':'H','ILE':'I','LYS':'K','LEU':'L',
           'MET':'M','ASN':'N','PRO':'P','GLN':'Q','ARG':'R','SER':'S','THR':'T','VAL':'V','TRP':'W','TYR':'Y'}
    residues={};ligands=[]
    for line in Path(args.structure_input).read_text().splitlines():
        if line.startswith('ENDMDL'):break
        if not line.startswith(('ATOM  ','HETATM')) or line[16:17] not in {' ','A'}:continue
        element=line[76:78].strip() or line[12:16].strip().lstrip('0123456789')[0]
        if element in {'H','D'}:continue
        xyz=tuple(map(float,(line[30:38],line[38:46],line[46:54])))
        if line[21] in rule['ligand_chains']:ligands.append(xyz)
        if line.startswith('ATOM  ') and line[21]==chain:
            if line[26:27].strip():raise ValueError('INSERTION_CODE_REQUIRES_MAPPING')
            key=chain+str(int(line[22:26]));residues.setdefault(key,{'aa':amino[line[17:20]],'xyz':[]})['xyz'].append(xyz)
    native=''.join(r['aa'] for r in residues.values())
    if not sequences or sequences[0]!=native:issues.append('native_sequence')
    variants=sequences[1:]
    def check(key,value):
        if report.get(key)!=value:issues.append(key)
    check('sequence_count',len(variants));check('sequence_length',len(native))
    check('unique_sequences',len(set(variants)));check('duplicate_sequences',len(variants)-len(set(variants)))
    check('fixed_count',len(fixed));check('design_count',len(design));check('context_rule',rule)
    counter=re.search(r'num_ligand_res=(\d+)',names[0])
    check('model_reported_context_count',int(counter.group(1)) if counter else None)
    hashes={name:hashlib.sha256(Path(getattr(args,name)).read_bytes()).hexdigest()
            for name in ('sequences_fasta','structure_input','fixed_file','design_file','context_rule_file')}
    check('input_hashes',hashes)
    fixed_bad=[];outside_bad=[];keys=list(residues);expected_rows=[]
    for name,variant in zip(names[1:],variants):
        if len(variant)!=len(native):issues.append('variant_length');continue
        changed={key for key,old,new in zip(keys,native,variant) if old!=new}
        expected_rows.append({'sequence':name,'mutations':len(changed),'fixed_changes':len(changed.intersection(fixed)),
                              'outside_design_changes':len(changed.difference(design))})
        for key,old,new in zip(keys,native,variant):
            if old!=new:
                if key in fixed:fixed_bad.append(key)
                if key not in design:outside_bad.append(key)
    if len(report.get('fixed_changes',[]))!=len(fixed_bad):issues.append('fixed_changes')
    if len(report.get('outside_design_changes',[]))!=len(outside_bad):issues.append('outside_design_changes')
    check('status','VALID' if not fixed_bad and not outside_bad else 'CONSTRAINT_VIOLATION')
    check('per_sequence',expected_rows)
    if fixed_bad:issues.append('fixed_residue_changed')
    if outside_bad:issues.append('mutation_outside_design')
    distances=[sum(a!=b for a,b in zip(x,y)) for x,y in itertools.combinations(variants,2)]
    check('pairwise_hamming',{'pairs':len(distances),'minimum':min(distances) if distances else None,
                             'maximum':max(distances) if distances else None,'mean':sum(distances)/len(distances) if distances else None})
    context=sorted(key for key,r in residues.items() if min((math.dist(p,q) for p in r['xyz'] for q in ligands),default=math.inf)<=rule['distance_angstrom'])
    check('pocket_residues',context);check('design_ligand_context_residues',sorted(set(context).intersection(design)))
    check('design_ligand_context_count',len(set(context).intersection(design)))
    check('fixed_pocket_count',len(set(context).intersection(fixed)))
    mutable=len(set(context).difference(fixed));check('mutable_pocket_count',mutable)
    check('pocket_design_coverage',len(set(context).intersection(design))/mutable if mutable else None)
    return {'schema_version':'enzyme-core/design-summary-validation/v1','pass':not issues,'differences':sorted(set(issues)),
            'scope':'Independent constraint, diversity, mapping, source hash and geometric context recomputation; no activity claim.'}


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--summary-json',required=True)
    p.add_argument('--sequences-fasta',required=True)
    p.add_argument('--structure-input',required=True)
    p.add_argument('--fixed-file',required=True)
    p.add_argument('--design-file',required=True)
    p.add_argument('--context-rule-file',required=True)
    p.add_argument('--output-json',required=True)
    args=p.parse_args();value=verify(args);out=Path(args.output_json);out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(value,indent=2)+'\n');print(json.dumps(value))
    if not value['pass']:raise SystemExit(1)


if __name__=='__main__':main()
