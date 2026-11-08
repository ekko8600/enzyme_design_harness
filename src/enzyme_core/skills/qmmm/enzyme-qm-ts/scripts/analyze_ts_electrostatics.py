#!/usr/bin/env python3
"""analyze_ts_electrostatics.py — Per-residue electrostatic stabilization of the TS.

Decomposes, residue by residue, how the enzyme's electrostatic field interacts with the
QM charge REORGANIZATION going reactant->TS (Δq = q_TS - q_R). A residue whose MM point
charges interact favorably (ΔE<0) with Δq electrostatically stabilizes the transition
state (catalytic electrostatic preorganization, Warshel-style); ΔE>0 destabilizes it.

This turns QM/MM charge data into a ranked, physically-grounded shortlist for engineering:
  * strong stabilizers  -> catalytically important, conserve (or fine-tune orientation)
  * destabilizers / gaps -> candidate positions to mutate toward TS-complementary charge

ΔE_i = 332.0637 * Σ_{a∈res i} Σ_{k∈QM} Q_a · Δq_k / |r_a - r_k|   [kcal/mol]

Usage:
  analyze_ts_electrostatics.py -p complex.prmtop --ts-restart cm_scan_TS-1.restart \
     --charges qm_charges_RTS.json --qm-globals qm_globals.json \
     [--exclude-resids 181,406,560,572] [--rcut 15] [--top 25] [--out ts_elec.csv]

qm_globals.json: {"order":[g1,g2,...,gN]} 1-based prmtop indices in the SAME order as the
CP2K QM atoms 1..N (i.e. QM_KIND blocks concatenated: all C, then H, then O).
"""
import argparse, json, re
import numpy as np

KE = 332.0637  # kcal·mol⁻¹·Å·e⁻²

def coords_from_restart(fn):
    t = open(fn, encoding='latin-1').read()
    m = re.search(r'&COORD(.*?)&END COORD', t, re.S)
    rows = [l.split() for l in m.group(1).strip().splitlines() if len(l.split()) >= 4]
    return np.array([[float(x) for x in r[1:4]] for r in rows])  # (natom,3), 0-based order

def main():
    import parmed as pmd
    ap = argparse.ArgumentParser()
    ap.add_argument('-p', '--prmtop', required=True)
    ap.add_argument('--ts-restart', required=True)
    ap.add_argument('--charges', required=True, help='qm_charges_RTS.json with dq {local:val}')
    ap.add_argument('--qm-globals', required=True, help='json {"order":[global 1-based ...]}')
    ap.add_argument('--exclude-resids', default='', help='comma resids to exclude (QM residues)')
    ap.add_argument('--rcut', type=float, default=15.0, help='only residues with any atom within rcut of QM')
    ap.add_argument('--top', type=int, default=25)
    ap.add_argument('--out', default=None)
    a = ap.parse_args()

    top = pmd.load_file(a.prmtop)
    xyz = coords_from_restart(a.ts_restart)
    charges = np.array([at.charge for at in top.atoms])
    dqmap = json.load(open(a.charges))['dq']              # {local(str): dq}
    order = json.load(open(a.qm_globals))['order']        # global 1-based, CP2K QM order
    # QM atom positions (0-based) and Δq
    qm_g0 = [g - 1 for g in order]
    dq = np.array([dqmap[str(i + 1)] for i in range(len(order))])
    qm_pos = xyz[qm_g0]
    excl = set(int(x) for x in a.exclude_resids.split(',') if x.strip())

    res_dE = []
    for res in top.residues:
        rid = res.idx + 1
        if rid in excl or res.name in ('WAT', 'Na+', 'Cl-', 'HOH'):
            continue
        ai = [at.idx for at in res.atoms]
        rc = xyz[ai]
        # skip far residues (min distance to QM > rcut)
        dmin = float(np.linalg.norm(rc[:, None, :] - qm_pos[None, :, :], axis=2).min())
        if dmin > a.rcut:
            continue
        dE = 0.0
        for idx in ai:
            Q = charges[idx]
            if abs(Q) < 1e-6:
                continue
            r = np.linalg.norm(qm_pos - xyz[idx], axis=1)
            dE += KE * Q * np.sum(dq / r)
        res_dE.append((rid, res.name, round(dmin, 2), round(dE, 2)))

    res_dE.sort(key=lambda t: t[3])   # most negative (stabilizing) first
    print(f"{'resid':>6} {'name':>5} {'dmin/Å':>7} {'ΔE_TS/kcal.mol⁻¹':>16}   role")
    for rid, nm, dm, dE in res_dE[:a.top]:
        role = 'TS 稳定(保守/强化)' if dE < -0.5 else ('TS 去稳定(候选突变)' if dE > 0.5 else '中性')
        print(f"{rid:>6} {nm:>5} {dm:>7} {dE:>16.2f}   {role}")
    if a.out:
        import csv
        with open(a.out, 'w', newline='') as f:
            w = csv.writer(f); w.writerow(['resid', 'resname', 'dmin_A', 'dE_TS_kcalmol'])
            w.writerows(res_dE)
        print(f"\nwrote {a.out} ({len(res_dE)} residues within {a.rcut} Å)")

if __name__ == '__main__':
    main()
