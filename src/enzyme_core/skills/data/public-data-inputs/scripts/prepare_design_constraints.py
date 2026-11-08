"""Deterministically subtract fixed sequence identities from an explicit design set."""
import argparse
import hashlib
import json
from pathlib import Path
import re


def prepare(args):
    values = {}
    for role in ('design', 'fixed'):
        value = json.loads(Path(getattr(args, role)).read_text())
        if not isinstance(value, list) or any(not isinstance(x, str) or not re.fullmatch(r'[A-Za-z0-9]-?\d+', x) for x in value) or len(value) != len(set(value)):
            raise ValueError('RESIDUE_LIST_INVALID:' + role)
        values[role] = value
    residues = set()
    for line in Path(args.structure).read_text().splitlines():
        if line.startswith('ATOM  '):
            if line[26:27].strip(): raise ValueError('INSERTION_CODE_REQUIRES_EXPLICIT_MAPPING')
            residues.add(line[21] + str(int(line[22:26])))
    if (set(values['design']) | set(values['fixed'])) - residues:
        raise ValueError('CONSTRAINT_RESIDUE_NOT_IN_STRUCTURE')
    excluded = [x for x in values['design'] if x in values['fixed']]
    effective = [x for x in values['design'] if x not in values['fixed']]
    if not effective: raise ValueError('EMPTY_EFFECTIVE_DESIGN_SET')
    out = Path(args.output); out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(effective, indent=2) + '\n')
    manifest = {'status':'PREPARED', 'fixed_semantics':'no_mutation', 'excluded_fixed_residues':excluded,
                'input_hashes':{k:hashlib.sha256(Path(getattr(args,k)).read_bytes()).hexdigest() for k in ('structure','design','fixed')},
                'output_sha256':hashlib.sha256(out.read_bytes()).hexdigest(),
                'no_repacking_or_movement_constraints_inferred':True}
    Path(args.manifest).parent.mkdir(parents=True,exist_ok=True)
    Path(args.manifest).write_text(json.dumps(manifest,indent=2)+'\n')
    return manifest


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--structure',required=True)
    p.add_argument('--design',required=True)
    p.add_argument('--fixed',required=True)
    p.add_argument('--output',required=True)
    p.add_argument('--manifest',required=True)
    prepare(p.parse_args())


if __name__=='__main__':main()
