"""Acceptance fixture with a reproducible UTF-8 BOM parsing defect."""
import argparse
import csv
p=argparse.ArgumentParser();p.add_argument('--input',required=True);p.add_argument('--output',required=True);a=p.parse_args()
with open(a.input,encoding='utf-8',newline='') as f:
    reader=csv.DictReader(f);rows=list(reader);fields=reader.fieldnames
    if 'SMILES' not in fields:raise ValueError('SMILES header required')
with open(a.output,'w',encoding='utf-8',newline='') as f:
    writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader();writer.writerows(rows)
