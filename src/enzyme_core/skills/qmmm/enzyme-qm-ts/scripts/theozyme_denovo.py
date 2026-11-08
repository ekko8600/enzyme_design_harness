#!/usr/bin/env python3
"""theozyme_denovo.py — Build a transferable theozyme (catalytic motif) for de novo enzyme design.

Given a catalytic-state structure, expresses each catalytic functional-group atom by its
INTERNAL coordinates relative to a substrate-fixed frame (3 substrate anchor atoms):
  distance(X–a0), angle(X–a0–a1), dihedral(X–a0–a1–a2)
These 3 numbers locate X independent of the global frame → transferable to ANY new scaffold.
Two+ atoms per catalytic group fully fix that group's 6-DOF pose.

Outputs:
  * theozyme_denovo.json — per state, per group, per atom: (d, angle, dihedral) + the raw distances
  * motif_<state>.pdb    — coordinate motif (substrate reacting atoms + catalytic group atoms),
                           directly usable as an RFdiffusion/motif-scaffolding motif
  * theozyme.cst         — Rosetta-enzdes-style constraint block (TS state) as a starting template

Config JSON (--config) reuses the extract config keys:
  ref_pdb, states{name:restart}, key substrate anchors, and 'motif_groups' {label:[[resid,name]..]}
plus 'anchors':[["C6"],["O2"],["O1"]] referencing key_atoms.
"""
import argparse, json, re
import numpy as np

def coords_from_restart(fn):
    t = open(fn, encoding='latin-1').read()
    m = re.search(r'&COORD(.*?)&END COORD', t, re.S)
    rows = [l.split() for l in m.group(1).strip().splitlines() if len(l.split()) >= 4]
    return np.array([[float(x) for x in r[1:4]] for r in rows])

def read_ref(fn):
    L, box = [], None
    for l in open(fn, encoding='latin-1'):
        if l.startswith('CRYST1'): box = l
        elif l[:6] in ('ATOM  ', 'HETATM'): L.append(l.rstrip('\n'))
    return L, box

def aidx(ref, resid, name):
    for i, l in enumerate(ref):
        if int(l[22:26]) == resid and l[12:16].strip() == name: return i
    raise KeyError(f"{name}@{resid}")

def dist(x, i, j): return float(np.linalg.norm(x[i]-x[j]))
def angle(x, i, j, k):
    v1, v2 = x[i]-x[j], x[k]-x[j]
    return float(np.degrees(np.arccos(np.clip(np.dot(v1, v2)/(np.linalg.norm(v1)*np.linalg.norm(v2)), -1, 1))))
def dihedral(x, i, j, k, l):
    b1, b2, b3 = x[j]-x[i], x[k]-x[j], x[l]-x[k]
    n1, n2 = np.cross(b1, b2), np.cross(b2, b3)
    m = np.cross(n1, b2/np.linalg.norm(b2))
    return float(np.degrees(np.arctan2(np.dot(m, n2), np.dot(n1, n2))))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--config', required=True)
    ap.add_argument('--outdir', default='theozyme')
    a = ap.parse_args()
    cfg = json.load(open(a.config)); import os; os.makedirs(a.outdir, exist_ok=True)
    ref, box = read_ref(cfg['ref_pdb'])
    ka = {k: aidx(ref, v[0], v[1]) for k, v in cfg['key_atoms'].items()}
    a0, a1, a2 = [ka[n] for n in cfg['anchors']]        # substrate frame anchors (0-based)
    groups = cfg['motif_groups']

    out = {'anchors': cfg['anchors'],
           'note': 'internal coords of catalytic atoms in substrate frame (d[Å], angle[°]∠(X-a0-a1), dihedral[°](X-a0-a1-a2)); transferable to new scaffolds',
           'per_state': {}}
    for state, rst in cfg['states'].items():
        x = coords_from_restart(rst)
        gd = {}
        for g, atoms in groups.items():
            gd[g] = {}
            for r, n in atoms:
                i = aidx(ref, r, n)
                gd[g][n] = {'d': round(dist(x, i, a0), 3),
                            'ang': round(angle(x, i, a0, a1), 1),
                            'dih': round(dihedral(x, i, a0, a1, a2), 1)}
        out['per_state'][state] = gd
        # motif PDB: substrate anchors + all motif-group atoms (functional groups only)
        keep = {a0, a1, a2} | {aidx(ref, r, n) for atoms in groups.values() for r, n in atoms}
        with open(f"{a.outdir}/motif_{state}.pdb", 'w') as f:
            if box: f.write(box)
            for i in sorted(keep):
                xx, yy, zz = x[i]
                f.write(f"{ref[i][:30]}{xx:8.3f}{yy:8.3f}{zz:8.3f}{ref[i][54:]}\n")
            f.write("END\n")
    json.dump(out, open(f"{a.outdir}/theozyme_denovo.json", 'w'), indent=2, ensure_ascii=False)

    # Rosetta-enzdes-style cst template from the TS state (one block per catalytic group, first 2 atoms)
    ts = out['per_state'].get('TS') or list(out['per_state'].values())[-1]
    sub_anchor_names = cfg['anchors']
    with open(f"{a.outdir}/theozyme.cst", 'w') as f:
        f.write("# Rosetta-enzdes-style template (TS). Fill residue3-letter as needed; angles/dihedrals from QM/MM TS.\n")
        f.write(f"# substrate anchors: {sub_anchor_names}\n")
        for g, atoms in groups.items():
            f.write(f"\n# --- {g} ---\nCST::BEGIN\n")
            for (r, n) in atoms[:1]:
                ic = ts[g][n]
                f.write(f"  TEMPLATE::  ATOM_MAP: 1 atom_name: {n}\n")
                f.write(f"  TEMPLATE::  ATOM_MAP: 2 atom_name: {sub_anchor_names[0]}\n")
                f.write(f"  CONSTRAINT:: distanceAB: {ic['d']:.2f} 0.30 100. 0\n")
                f.write(f"  CONSTRAINT:: angle_A:   {ic['ang']:.1f} 10.0 50. 360.\n")
                f.write(f"  CONSTRAINT:: torsion_A: {ic['dih']:.1f} 15.0 25. 360.\n")
            f.write("CST::END\n")
    print(f"wrote theozyme_denovo.json + motif_<state>.pdb + theozyme.cst in {a.outdir}/")

if __name__ == '__main__':
    main()
