#!/usr/bin/env python3
"""Export an explicitly chosen prediction from a hash-bound manifest; does not rank science."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import shutil


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest', required=True)
    p.add_argument('--candidate', required=True, help='Exact candidate path recorded in the prediction manifest')
    p.add_argument('--output', default='selected.pdb')
    p.add_argument('--provenance', default='selection.json')
    args = p.parse_args()
    manifest = Path(args.manifest).resolve()
    data = json.loads(manifest.read_text())
    if data.get('schema_version') != 'enzyme-core/structure-prediction/v1':
        raise ValueError('PREDICTION_MANIFEST_UNSUPPORTED')
    rows = [row for row in data['structures'] if row['path'] == args.candidate]
    if len(rows) != 1:
        raise ValueError('EXPLICIT_CANDIDATE_UNKNOWN_OR_DUPLICATED')
    source = (manifest.parent / args.candidate).resolve()
    if manifest.parent not in source.parents or not source.is_file():
        raise ValueError('CANDIDATE_ESCAPES_PREDICTION_DIRECTORY')
    sha = hashlib.sha256(source.read_bytes()).hexdigest()
    if sha != rows[0]['sha256']:
        raise ValueError('CANDIDATE_CHANGED')
    raw = source.read_text()
    if not any(line.startswith(('ATOM  ', 'HETATM')) for line in raw.splitlines()):
        raise ValueError('CANDIDATE_ATOMS_MISSING')
    output = Path(args.output)
    if output.exists():
        raise ValueError('SELECTED_OUTPUT_ALREADY_EXISTS')
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, output)
    provenance = {'schema_version': 'enzyme-core/structure-selection/v1', 'manifest_sha256': hashlib.sha256(manifest.read_bytes()).hexdigest(),
                  'candidate': args.candidate, 'structure_sha256': sha, 'input_fasta_sha256': data['input_fasta_sha256'],
                  'selection_owner': 'caller Host', 'scientific_validation': 'NOT_ASSESSED'}
    Path(args.provenance).write_text(json.dumps(provenance, indent=2))


if __name__ == '__main__':
    main()
