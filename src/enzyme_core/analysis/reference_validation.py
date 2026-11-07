"""Independent, dependency-free checks against frozen structural/numeric references."""
import csv
import hashlib
import json
import math
from pathlib import Path


def coordinate_state_sha(path):
    records=[line.rstrip() for line in Path(path).read_text().splitlines()
             if line.startswith(('ATOM  ','HETATM','CONECT','LINK  ','SSBOND','TER   '))]
    if not any(line.startswith(('ATOM  ','HETATM')) for line in records):raise ValueError('EMPTY_COORDINATE_STATE')
    return hashlib.sha256(('\n'.join(records)+'\n').encode()).hexdigest()


def evaluate_energy_terms(root,reference,tolerance=1e-6):
    expected=json.loads(Path(reference).read_text());root=Path(root)
    with (root/'energy_terms.csv').open(newline='') as handle:
        rows=list(csv.DictReader(handle))
    def key(row):return (row['model'],int(row['pose_position']),row['term'])
    actual={}
    for row in rows:
        identity=key(row)
        if identity in actual:raise ValueError('DUPLICATE_ENERGY_ROW')
        if row['unit']!='REU':raise ValueError('ENERGY_UNIT_INVALID')
        actual[identity]=row
    errors=[];wanted={key(row):row for row in expected['rows']}
    if actual.keys()!=wanted.keys():errors.append('energy_row_coverage')
    for identity,row in actual.items():
        if identity not in wanted:continue
        target=wanted[identity]
        for name,truth in [('raw_energy',target['raw_energy']),('weight',target['weight']),('weighted_energy',target['raw_energy']*target['weight'])]:
            value=float(row[name])
            if not math.isfinite(value) or abs(value-truth)>tolerance:errors.append(str(identity)+':'+name)
    manifest=json.loads((root/'manifest.json').read_text())
    if {r['model']:r['sha256'] for r in manifest['models']}!=expected['input_hashes']:errors.append('input_identity')
    return {'pass':not errors,'differences':errors,'tolerance':tolerance,'scope':'Frozen reference arithmetic and identity; not enzyme activity.'}
