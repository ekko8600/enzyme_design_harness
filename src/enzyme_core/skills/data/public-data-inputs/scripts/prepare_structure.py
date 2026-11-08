"""Deterministic local structure preparation; never infers chemical conditions."""
import argparse
import hashlib
import json
from pathlib import Path


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def prepare(args):
    from Bio import SeqIO
    from Bio.PDB import PDBParser, MMCIFParser, PDBIO, MMCIFIO, Select
    from Bio.SeqUtils import seq1
    path=Path(args.structure)
    parser=MMCIFParser(QUIET=True) if path.suffix.lower() in {'.cif','.mmcif'} else PDBParser(QUIET=True)
    structure=parser.get_structure('input',str(path));models=list(structure)
    if args.model_index is None and len(models)!=1:raise ValueError('AMBIGUOUS_MODEL_SELECTION')
    if args.model_index is not None and not 1<=args.model_index<=len(models):raise ValueError('MODEL_SELECTION_OUT_OF_RANGE')
    model=models[(args.model_index or 1)-1]
    chain_ids=args.chains.split(',') if args.chains else [c.id for c in model]
    if len(set(chain_ids))!=len(chain_ids) or not set(chain_ids)<={c.id for c in model}:raise ValueError('CHAIN_SELECTION_INVALID')
    rows=[];pose=0;sequences={}
    for chain in model:
        if chain.id not in chain_ids:continue
        residues=[r for r in chain if r.id[0]==' ']
        if not residues:
            pose+=len(chain)
            continue
        if any(r.is_disordered()==2 for r in residues):raise ValueError('AMBIGUOUS_RESIDUE_IDENTITY')
        sequence=''.join(seq1(r.resname,undef_code='X') for r in residues)
        if 'X' in sequence:raise ValueError('UNSUPPORTED_RESIDUE_IDENTITY')
        sequences[chain.id]=sequence
        for residue in chain:
            pose+=1
            if residue.id[0]!=' ':continue
            rows.append({'chain':chain.id,'residue_number':residue.id[1],'insertion_code':residue.id[2].strip(),
                'structure_aa':seq1(residue.resname),'pose_position':pose})
    result={'schema_version':'enzyme-core/local-preparation/v1','action':args.action,
            'input_sha256':sha(path),'chains':sequences,'limitations':['Observed atoms only; no atom rebuilding or chemical-state inference.']}
    result['non_protein_residues']=[{'chain':c.id,'residue_number':r.id[1],'insertion_code':r.id[2].strip(),'resname':r.resname}
        for c in model if c.id in chain_ids for r in c if r.id[0]!=' ']
    if not sequences and args.action in {'map_residues','extract_sequence'}:raise ValueError('NO_PROTEIN_RESIDUES')
    output=Path(args.output);output.parent.mkdir(parents=True,exist_ok=True)
    if args.action=='map_residues':
        if not args.sequence:raise ValueError('SEQUENCE_REQUIRED')
        records=list(SeqIO.parse(args.sequence,'fasta'))
        if len(records)!=1:raise ValueError('ONE_ASSAY_SEQUENCE_REQUIRED')
        assay=str(records[0].seq).upper()
        for chain,sequence in sequences.items():
            starts=[i for i in range(len(assay)-len(sequence)+1) if assay[i:i+len(sequence)]==sequence]
            if len(starts)!=1:raise ValueError('AMBIGUOUS_OR_INCOMPLETE_SEQUENCE_MAPPING:'+chain)
            selected=[r for r in rows if r['chain']==chain]
            for i,row in enumerate(selected):row.update(assay_position=starts[0]+i+1,assay_aa=assay[starts[0]+i])
        output.write_text(json.dumps(rows,indent=2)+'\n')
        result['sequence_sha256']=sha(args.sequence)
    elif args.action=='extract_sequence':
        output.write_text(''.join('>'+chain+'\n'+seq+'\n' for chain,seq in sequences.items()))
    elif args.action in {'convert_structure','select_model'}:
        class Chosen(Select):
            def accept_model(self,item):return item.id==model.id
            def accept_chain(self,item):return item.id in chain_ids
        writer=MMCIFIO() if output.suffix.lower() in {'.cif','.mmcif'} else PDBIO()
        writer.set_structure(structure);writer.save(str(output),select=Chosen())
        result['limitations'].append('Format conversion is not chemical/connectivity validation; non-coordinate header records are not preserved.')
    elif args.action=='check_constraints':
        if not args.constraints:raise ValueError('SCIENTIFIC_CONDITIONS_REQUIRED')
        declarations=json.loads(Path(args.constraints).read_text())
        required={'no_mutation','no_repacking','no_movement'}
        if not required<=declarations.keys():raise ValueError('CONSTRAINT_SEMANTICS_REQUIRED')
        known={(r['chain'],r['residue_number'],r['insertion_code']) for r in rows}
        for kind in required:
            if not isinstance(declarations[kind],list):raise ValueError('INVALID_CONSTRAINT_LIST:'+kind)
            for r in declarations[kind]:
                if (r['chain'],r['residue_number'],r.get('insertion_code','')) not in known:raise ValueError('CONSTRAINT_RESIDUE_NOT_FOUND')
        result['declared_constraints']=declarations;result['chemical_identity_validation']='NOT_INFERRED'
        output.write_text(json.dumps(result,indent=2)+'\n')
    else:
        result['residues']=rows
        output.write_text(json.dumps(result,indent=2)+'\n')
    result['output_sha256']=sha(output)
    Path(args.manifest).write_text(json.dumps(result,indent=2)+'\n')
    return result


def parser():
    p=argparse.ArgumentParser()
    p.add_argument('--action',required=True,choices=['extract_sequence','select_model','convert_structure','map_residues','check_inputs','check_constraints'])
    p.add_argument('--structure',required=True)
    p.add_argument('--sequence');p.add_argument('--constraints')
    p.add_argument('--model-index',type=int);p.add_argument('--chains')
    p.add_argument('--output',required=True);p.add_argument('--manifest',required=True)
    return p


if __name__=='__main__':prepare(parser().parse_args())
