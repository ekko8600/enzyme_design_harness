"""Offline, deterministic analysis of existing single-chain MPNN sequence designs."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import math
import re

AA = dict(zip('ALA ARG ASN ASP CYS GLN GLU GLY HIS ILE LEU LYS MET PHE PRO SER THR TRP TYR VAL'.split(), 'ARNDCQEGHILKMFPSTWYV'))


def summarize(args):
    records = []
    for line in Path(args.sequences_fasta).read_text().splitlines():
        if line.startswith('>'):
            records.append([line[1:], ''])
        elif line.strip():
            if not records: raise ValueError('FASTA_HEADER_REQUIRED')
            records[-1][1] += line.strip()
    if len(records) < 2: raise ValueError('NATIVE_AND_DESIGNS_REQUIRED')
    model_context=re.search(r'(?:^|[, ])num_ligand_res=(\d+)',records[0][0])
    rule = json.loads(Path(args.context_rule_file).read_text())
    chain = rule['protein_chain']; ligand_chains = set(rule['ligand_chains']); cutoff = rule['distance_angstrom']
    if (not isinstance(chain,str) or len(chain)!=1 or not isinstance(rule['ligand_chains'],list) or chain in ligand_chains
            or isinstance(cutoff,bool) or not isinstance(cutoff,(int,float)) or not math.isfinite(cutoff) or cutoff<=0):
        raise ValueError('EXPLICIT_CONTEXT_RULE_INVALID')
    residues, atoms, ligands = {}, {}, []
    present_ligands = set()
    for line in Path(args.structure_input).read_text().splitlines():
        if line.startswith('ENDMDL'): break
        if line[:6] not in {'ATOM  ', 'HETATM'}: continue
        if line[16:17] not in {' ', 'A'}: continue
        element=line[76:78].strip() or line[12:16].strip().lstrip('0123456789')[0]
        if element in {'H','D'}: continue
        point = tuple(float(line[i:i+8]) for i in (30,38,46))
        if not all(math.isfinite(x) for x in point): raise ValueError('NONFINITE_COORDINATE')
        c=line[21]
        if c in ligand_chains:
            ligands.append(point); present_ligands.add(c)
        if c == chain and line.startswith('ATOM  '):
            if line[26].strip(): raise ValueError('INSERTION_CODE_REQUIRES_EXPLICIT_MAPPING')
            key=c+str(int(line[22:26])); aa=AA.get(line[17:20])
            if aa is None: raise ValueError('NONSTANDARD_RESIDUE_REQUIRES_MAPPING')
            residues[key]=aa;atoms.setdefault(key,[]).append(point)
    if present_ligands != ligand_chains: raise ValueError('DECLARED_LIGAND_CHAIN_MISSING')
    native=''.join(residues.values())
    if records[0][1] != native: raise ValueError('NATIVE_SEQUENCE_STRUCTURE_MISMATCH')
    sets={}
    for key, path in [('fixed',args.fixed_file),('design',args.design_file)]:
        value=json.loads(Path(path).read_text())
        if not isinstance(value,list) or any(not isinstance(x,str) for x in value) or len(value)!=len(set(value)):
            raise ValueError('RESIDUE_LIST_INVALID:'+key)
        sets[key]=set(value)
        if sets[key]-residues.keys(): raise ValueError('UNMAPPED_RESIDUES:'+key)
    if sets['fixed'] & sets['design']: raise ValueError('DESIGN_FIXED_OVERLAP')
    index={key:i for i,key in enumerate(residues)}
    fixed_changes=[];outside_changes=[];rows=[]
    for name, sequence in records[1:]:
        if len(sequence)!=len(native) or set(sequence)-set(AA.values()): raise ValueError('DESIGN_SEQUENCE_INVALID')
        changed={r for r,i in index.items() if sequence[i]!=native[i]}
        fixed_changes += [{'sequence':name,'residue':r} for r in sorted(changed & sets['fixed'])]
        outside_changes += [{'sequence':name,'residue':r} for r in sorted(changed-sets['design'])]
        rows.append({'sequence':name,'mutations':len(changed),'fixed_changes':len(changed & sets['fixed']),
                     'outside_design_changes':len(changed-sets['design'])})
    designs=[s for _,s in records[1:]]
    distances=[sum(x!=y for x,y in zip(a,b)) for i,a in enumerate(designs) for b in designs[i+1:]]
    context=sorted(r for r,points in atoms.items() if any(sum((a-b)**2 for a,b in zip(p,q))<=cutoff**2 for p in points for q in ligands))
    summary={'schema_version':'enzyme-core/design-summary/v1','status':'VALID' if not fixed_changes and not outside_changes else 'CONSTRAINT_VIOLATION',
             'input_hashes':{name:hashlib.sha256(Path(getattr(args,name)).read_bytes()).hexdigest()
                             for name in ('sequences_fasta','structure_input','fixed_file','design_file','context_rule_file')},
             'sequence_count':len(designs),'sequence_length':len(native),'unique_sequences':len(set(designs)),
             'duplicate_sequences':len(designs)-len(set(designs)),
             'fixed_count':len(sets['fixed']),'design_count':len(sets['design']),
             'fixed_changes':fixed_changes,'outside_design_changes':outside_changes,
             'pairwise_hamming':{'pairs':len(distances),'minimum':min(distances) if distances else None,
                                 'maximum':max(distances) if distances else None,'mean':sum(distances)/len(distances) if distances else None},
             'context_rule':rule,'context_status':'APPLICABLE' if ligand_chains else 'NOT_APPLICABLE_NO_DECLARED_LIGANDS','pocket_residues':context,
             'model_reported_context_count':int(model_context.group(1)) if model_context else None,
             'design_ligand_context_residues':sorted(set(context)&sets['design']),
             'design_ligand_context_count':len(set(context)&sets['design']),
             'fixed_pocket_count':len(set(context)&sets['fixed']),
             'mutable_pocket_count':len(set(context)-sets['fixed']),
             'pocket_design_coverage':len(set(context)&sets['design'])/len(set(context)-sets['fixed']) if set(context)-sets['fixed'] else None,
             'per_sequence':rows,
             'limitations':['Single explicitly mapped protein chain; first FASTA record must be native.',
                            'Context is the frozen geometric rule, not inferred model attention or experimentally validated binding.',
                            'model_reported_context_count is a FASTA-header observation; its definition may differ from all-heavy-atom geometric context.',
                            'Sequence proposals and geometric coverage do not establish enzyme activity.']}
    return summary


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--sequences-fasta',required=True)
    p.add_argument('--structure-input',required=True)
    p.add_argument('--fixed-file',required=True)
    p.add_argument('--design-file',required=True)
    p.add_argument('--context-rule-file',required=True)
    p.add_argument('--output-json',required=True)
    p.add_argument('--output-csv',required=True)
    args=p.parse_args();result=summarize(args)
    out=Path(args.output_json);out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(result,indent=2)+'\n')
    out=Path(args.output_csv);out.parent.mkdir(parents=True,exist_ok=True)
    with out.open('w',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=['sequence','mutations','fixed_changes','outside_design_changes']);writer.writeheader();writer.writerows(result['per_sequence'])
    print(json.dumps({'status':result['status'],'sequence_count':result['sequence_count']}))


if __name__=='__main__': main()
