#!/usr/bin/env python3
"""extract_conformations.py — Export per-catalytic-state conformations & catalytic
geometric constraints from a QM/MM reaction path, for conditioning protein generative models.

For each state (e.g. R / NAC / TS / P) it writes:
  * <state>_full.pdb        — full all-atom coordinates (same scaffold conditioning)
  * <state>_activesite.pdb  — active-site subset only
  * constraints_<state>.json — key distances/angles/dihedrals (geometric constraints)
  * contactmap_<state>.csv   — active-site residue↔residue / residue↔substrate min-distances
And once:
  * theozyme.json           — catalytic functional groups expressed as INTERNAL coordinates
                              relative to the substrate reacting center (transferable to NEW scaffolds)

State geometries are read from CP2K restart files (&COORD, prmtop order). Atom/residue
names come from a reference PDB with identical atom ordering.

NOTE (frozen-MM caveat): if the path was run with the MM region frozen, only the QM atoms
differ between states; the protein backbone is the fixed input scaffold. These conformations
therefore capture the *local catalytic-state geometry* (theozyme / NAC / TS), NOT protein
backbone/global conformational change (that needs MD).

Config is a JSON (--config) with:
  { "ref_pdb":..., "states": {"R":"cm_scan_00-1.restart", ...},
    "active_site_resids": [...], "substrate_resid": N,
    "key_atoms": {"C6":[resid,name], "O2":[...], "Ow":[...], ...},
    "distances": [["C6","O2"],...], "angles": [["Ow","C6","O2"],...],
    "theozyme_groups": {"acid_Glu181":[[resid,name],...], ...} }
"""
import argparse, json, re
import numpy as np

def coords_from_restart(fn):
    t = open(fn, encoding='latin-1').read()
    m = re.search(r'&COORD(.*?)&END COORD', t, re.S)
    rows = [l.split() for l in m.group(1).strip().splitlines() if len(l.split()) >= 4]
    return np.array([[float(x) for x in r[1:4]] for r in rows])

def read_ref_pdb(fn):
    lines, box = [], None
    for l in open(fn, encoding='latin-1'):
        if l.startswith('CRYST1'):
            box = l
        elif l[:6] in ('ATOM  ', 'HETATM'):
            lines.append(l.rstrip('\n'))
    return lines, box

def atom_index(ref_lines, resid, name):
    """0-based atom index of (resid, atomname) in the reference PDB order."""
    for i, l in enumerate(ref_lines):
        if int(l[22:26]) == resid and l[12:16].strip() == name:
            return i
    raise KeyError(f"atom {name} in resid {resid} not found")

def write_pdb(path, ref_lines, box, xyz, keep_idx=None):
    with open(path, 'w') as f:
        if box: f.write(box)
        for i, l in enumerate(ref_lines):
            if keep_idx is not None and i not in keep_idx:
                continue
            x, y, z = xyz[i]
            f.write(f"{l[:30]}{x:8.3f}{y:8.3f}{z:8.3f}{l[54:]}\n")
        f.write("END\n")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--config', required=True)
    ap.add_argument('--outdir', default='conformations')
    a = ap.parse_args()
    cfg = json.load(open(a.config))
    import os; os.makedirs(a.outdir, exist_ok=True)
    ref, box = read_ref_pdb(cfg['ref_pdb'])

    # resolve key atoms -> 0-based indices
    ka = {k: atom_index(ref, v[0], v[1]) for k, v in cfg['key_atoms'].items()}
    # active-site atom indices (all atoms of listed resids)
    as_res = set(cfg['active_site_resids'])
    as_idx = set(i for i, l in enumerate(ref) if int(l[22:26]) in as_res)

    def d(x, i, j): return float(np.linalg.norm(x[i] - x[j]))
    def ang(x, i, j, k):
        v1, v2 = x[i]-x[j], x[k]-x[j]
        c = np.dot(v1, v2)/(np.linalg.norm(v1)*np.linalg.norm(v2))
        return float(np.degrees(np.arccos(np.clip(c, -1, 1))))

    theo = {'note': 'catalytic groups relative to substrate reacting center; for new-scaffold conditioning',
            'per_state': {}}
    for state, rst in cfg['states'].items():
        x = coords_from_restart(rst)
        write_pdb(f"{a.outdir}/{state}_full.pdb", ref, box, x)
        write_pdb(f"{a.outdir}/{state}_activesite.pdb", ref, box, x, keep_idx=as_idx)
        # geometric constraints
        con = {'distances_A': {}, 'angles_deg': {}}
        for i, j in cfg.get('distances', []):
            con['distances_A'][f"{i}-{j}"] = round(d(x, ka[i], ka[j]), 3)
        for i, j, k in cfg.get('angles', []):
            con['angles_deg'][f"{i}-{j}-{k}"] = round(ang(x, ka[i], ka[j], ka[k]), 1)
        json.dump(con, open(f"{a.outdir}/constraints_{state}.json", 'w'), indent=2)
        # contact map (active-site residues + substrate): min inter-residue distance
        resids = sorted(as_res)
        res_atoms = {r: [i for i, l in enumerate(ref) if int(l[22:26]) == r] for r in resids}
        with open(f"{a.outdir}/contactmap_{state}.csv", 'w') as f:
            f.write("resA,resB,min_dist_A\n")
            for ii in range(len(resids)):
                for jj in range(ii+1, len(resids)):
                    ra, rb = resids[ii], resids[jj]
                    A, B = x[res_atoms[ra]], x[res_atoms[rb]]
                    dm = float(np.linalg.norm(A[:, None, :]-B[None, :, :], axis=2).min())
                    if dm < 6.0:
                        f.write(f"{ra},{rb},{dm:.2f}\n")
        # theozyme: catalytic group atoms as internal coords vs substrate center (C6)
        c6 = ka.get(cfg.get('reacting_center', 'C6'))
        tg = {}
        for gname, atoms in cfg.get('theozyme_groups', {}).items():
            gi = [atom_index(ref, r, n) for r, n in atoms]
            tg[gname] = {ref[i][12:16].strip(): round(d(x, i, c6), 3) for i in gi}
        theo['per_state'][state] = tg

    json.dump(theo, open(f"{a.outdir}/theozyme.json", 'w'), indent=2, ensure_ascii=False)
    print(f"wrote per-state PDBs + constraints + contact maps + theozyme.json in {a.outdir}/")
    print(f"states: {list(cfg['states'])}; active-site resids: {sorted(as_res)}")

if __name__ == '__main__':
    main()
