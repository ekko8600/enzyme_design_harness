#!/usr/bin/env python3
"""analyze_scan.py — Turn a relaxed-scan energy table into a reaction-energy profile.

Reads scan_energies.dat (lines: "window target_A energy_hartree converged=N"),
converts to kcal/mol relative to the reactant (first window), identifies the barrier
(highest point) and the reaction energy, and writes a text profile + optional PNG.

Usage: analyze_scan.py -i scan_energies.dat [-o profile.png] [--ref-exp 15.4]
"""
import argparse
HARTREE2KCAL = 627.5094740631

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('-i', '--infile', default='scan_energies.dat')
    ap.add_argument('-o', '--png', default=None)
    ap.add_argument('--ref-exp', type=float, default=None, help='experimental barrier (kcal/mol) for comparison')
    a = ap.parse_args()

    rows = []
    for l in open(a.infile):
        p = l.split()
        if len(p) < 3 or p[2] in ('FAILED', 'NA'):
            continue
        # actual CV preferred over the target for the x-axis. Token forms:
        #   cv=<rc>            or   cv=<rc>/brk<d>/frm<d>   (RC = leading number)
        cv = None
        for tok in p:
            if tok.startswith('cv='):
                v = tok[3:].split('/')[0]
                cv = None if v in ('NA', '') else float(v)
        try:
            d = cv if cv is not None else float(p[1])
            rows.append((int(p[0]), d, float(p[2])))
        except ValueError:
            continue
    if not rows:
        print("no usable data in", a.infile); return
    rows.sort(key=lambda r: r[0])
    e0 = rows[0][2]
    prof = [(w, d, (e - e0) * HARTREE2KCAL) for w, d, e in rows]

    # barrier = max relative energy; reaction energy = last point
    ts = max(prof, key=lambda r: r[2])
    react = prof[-1]
    print(f"{'win':>3} {'d(C-C)/A':>9} {'ΔE/kcal.mol-1':>14}")
    for w, d, de in prof:
        mark = '  <-- TS (max)' if (w, d) == (ts[0], ts[1]) else ''
        print(f"{w:>3} {d:>9.2f} {de:>14.2f}{mark}")
    print(f"\nForward barrier  ΔE‡ = {ts[2]:.1f} kcal/mol  (at d(C-C)={ts[1]:.2f} A)")
    print(f"Reaction energy  ΔE  = {react[2]:.1f} kcal/mol  (product side, d={react[1]:.2f} A)")
    if a.ref_exp is not None:
        print(f"Experimental ref ΔG‡ ≈ {a.ref_exp} kcal/mol  → deviation {ts[2]-a.ref_exp:+.1f} kcal/mol")
        print("(Note: electronic ΔE‡ vs experimental ΔG‡ differ by ZPE/entropy; expect a few kcal/mol offset.)")

    if a.png:
        try:
            import matplotlib
            matplotlib.use('Agg')
            import matplotlib.pyplot as plt
            xs = [d for _, d, _ in prof]; ys = [de for _, _, de in prof]
            plt.figure(figsize=(6, 4))
            plt.plot(xs, ys, 'o-', color='#2b6cb0')
            plt.plot(ts[1], ts[2], 's', color='#c53030', ms=10, label=f'TS {ts[2]:.1f}')
            plt.gca().invert_xaxis()
            plt.xlabel('forming C–C distance / Å'); plt.ylabel('ΔE / kcal·mol⁻¹')
            plt.title('Chorismate mutase QM/MM reaction profile'); plt.legend(); plt.tight_layout()
            plt.savefig(a.png, dpi=140)
            print(f"\nwrote {a.png}")
        except Exception as e:
            print("PNG skipped:", e)

if __name__ == '__main__':
    main()
