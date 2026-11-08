#!/usr/bin/env python3
"""place_ligand.py — Superpose a parameterized substrate onto a bound ligand/TS-analog
in a crystal structure via a 3-point rigid (Kabsch) fit on a shared pharmacophore
(two carboxylate carbons + one ether/bridging oxygen), then write the placed substrate.

Generic prep-layer tool. Here used to seat chorismate where 2CHT's TSA sits.

Usage:
  place_ligand.py --lig-mol2 CHO.mol2 --lig-anchors C6,C8,O \
                  --target-pdb 2cht.pdb --target-resname TSA --target-chain A --target-resid 203 \
                  --target-anchors C10,C11,O7 --out CHO_placed.pdb --resname CHO
Anchors are matched in order; both carboxylate pairings are tried and the lower-RMSD fit kept.
"""
import argparse
import numpy as np

def read_mol2_atoms(path):
    lines = open(path).read().splitlines()
    ai = lines.index('@<TRIPOS>ATOM')
    bi = next(i for i, l in enumerate(lines) if l.startswith('@<TRIPOS>BOND'))
    names, elems, xyz = [], [], []
    for l in lines[ai+1:bi]:
        p = l.split()
        if len(p) < 6: continue
        names.append(p[1]); elems.append(p[1][0].upper()); xyz.append([float(v) for v in p[2:5]])
    return names, elems, np.array(xyz)

def read_pdb_ligand(path, resname, chain, resid):
    coords = {}
    for l in open(path):
        if l[:6] in ('HETATM', 'ATOM  '):
            if l[17:20].strip()==resname and l[21]==chain and int(l[22:26])==resid:
                coords[l[12:16].strip()] = np.array([float(l[30:38]), float(l[38:46]), float(l[46:54])])
    return coords

def kabsch(P, Q):
    """rotation+translation mapping P onto Q (both Nx3). returns R, t, rmsd."""
    Pc, Qc = P.mean(0), Q.mean(0)
    H = (P-Pc).T @ (Q-Qc)
    U, _S, Vt = np.linalg.svd(H)
    d = np.sign(np.linalg.det(Vt.T @ U.T))
    D = np.diag([1, 1, d])
    R = Vt.T @ D @ U.T
    t = Qc - R @ Pc
    rmsd = np.sqrt(np.mean(np.sum((( P@R.T + t) - Q)**2, axis=1)))
    return R, t, rmsd

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--lig-mol2', required=True)
    ap.add_argument('--lig-anchors', required=True, help='cC,cC,etherO  (2 carboxylate C + ether O)')
    ap.add_argument('--target-pdb', required=True)
    ap.add_argument('--target-resname', required=True)
    ap.add_argument('--target-chain', required=True)
    ap.add_argument('--target-resid', type=int, required=True)
    ap.add_argument('--target-anchors', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--resname', default='LIG')
    a = ap.parse_args()

    names, elems, xyz = read_mol2_atoms(a.lig_mol2)
    la = a.lig_anchors.split(','); ta = a.target_anchors.split(',')
    tcoords = read_pdb_ligand(a.target_pdb, a.target_resname, a.target_chain, a.target_resid)
    idx = {n: i for i, n in enumerate(names)}
    P_ether = xyz[idx[la[2]]]; Q_ether = tcoords[ta[2]]
    best = None
    # try both carboxylate pairings (la[0]->ta[0],la[1]->ta[1]) and swapped
    for perm in ([0,1],[1,0]):
        P = np.array([xyz[idx[la[0]]], xyz[idx[la[1]]], P_ether])
        Q = np.array([tcoords[ta[perm[0]]], tcoords[ta[perm[1]]], Q_ether])
        R, t, rmsd = kabsch(P, Q)
        if best is None or rmsd < best[2]:
            best = (R, t, rmsd, perm)
    R, t, rmsd, perm = best
    placed = xyz @ R.T + t
    with open(a.out, 'w') as f:
        for i, (n, e) in enumerate(zip(names, elems)):
            x, y, z = placed[i]
            f.write(f"HETATM{i+1:5d} {n:<4s} {a.resname:<3s} A 900    {x:8.3f}{y:8.3f}{z:8.3f}  1.00  0.00          {e:>2s}\n")
    print(f"placed {len(names)} atoms; anchor-fit RMSD={rmsd:.3f} A (carboxylate pairing {perm})")
    print(f"wrote {a.out}")

if __name__ == '__main__':
    main()
