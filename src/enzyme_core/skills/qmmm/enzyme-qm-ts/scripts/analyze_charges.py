#!/usr/bin/env python3
"""analyze_charges.py — Electron reorganization & charge transfer along a reaction path.

Parses Mulliken and Hirshfeld atomic charges from CP2K output files at successive
path points (e.g. R, TS, P), maps QM atoms to chemical groups (substrate, catalytic
water, catalytic residues), and reports how charge redistributes as the reaction
proceeds — i.e. which fragments donate/accept electron density (charge transfer) and
where the electronic reorganization concentrates.

Usage:
  analyze_charges.py --pts R:ana_R.out TS:ana_TS.out P:ana_P.out \
                     --groups groups.json [--csv charges.csv] [--png charges.png]

groups.json maps a group label to a list of 1-based QM atom indices, e.g.
  {"substrate":[...], "cat_water":[...], "Glu181_acid":[...], "Glu406_base":[...]}
If omitted, only per-atom totals are reported.
"""
import argparse, json, re, sys

def parse_charges(path):
    """Return dict: {'mulliken':{idx:q}, 'hirshfeld':{idx:q}} (1-based atom idx)."""
    txt = open(path, encoding='latin-1').read()
    out = {'mulliken': {}, 'hirshfeld': {}}
    # Mulliken block: "# Atom Element Kind ... Charge" then rows "  1 O   1  ...  q"
    m = re.search(r'Mulliken Population Analysis.*?\n(.*?)\n\s*#?\s*Total', txt, re.S)
    if m:
        for l in m.group(1).splitlines():
            p = l.split()
            if len(p) >= 5 and p[0].isdigit():
                try: out['mulliken'][int(p[0])] = float(p[-1])
                except ValueError: pass
    # Hirshfeld block
    h = re.search(r'Hirshfeld Charges.*?\n(.*?)\n\s*Total', txt, re.S)
    if h:
        for l in h.group(1).splitlines():
            p = l.split()
            if len(p) >= 5 and p[0].isdigit():
                try: out['hirshfeld'][int(p[0])] = float(p[-1])
                except ValueError: pass
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--pts', nargs='+', required=True, help='LABEL:cp2k_output.out ...')
    ap.add_argument('--groups', default=None, help='JSON {group: [1-based idx,...]}')
    ap.add_argument('--scheme', default='hirshfeld', choices=['mulliken', 'hirshfeld'])
    ap.add_argument('--csv', default=None)
    ap.add_argument('--png', default=None)
    a = ap.parse_args()

    pts = [(s.split(':', 1)[0], s.split(':', 1)[1]) for s in a.pts]
    data = {lab: parse_charges(f) for lab, f in pts}
    labels = [l for l, _ in pts]
    groups = json.load(open(a.groups)) if a.groups else None

    if not groups:
        print("No groups.json; per-atom charges parsed. Provide --groups for fragment analysis.")
        for lab in labels:
            n = len(data[lab][a.scheme])
            print(f"  {lab}: {n} atoms parsed ({a.scheme})")
        return

    # group summed charge at each point
    rows = []
    print(f"=== 基团电荷 ({a.scheme}) 沿路径 ===")
    hdr = f"{'group':<16}" + "".join(f"{l:>10}" for l in labels) + f"{'Δ(P-R)':>10}"
    print(hdr)
    for g, idxs in groups.items():
        qs = [sum(data[lab][a.scheme].get(i, 0.0) for i in idxs) for lab in labels]
        d = qs[-1] - qs[0]
        rows.append((g, qs, d))
        print(f"{g:<16}" + "".join(f"{q:>10.3f}" for q in qs) + f"{d:>10.3f}")

    print("\n解读: Δ<0 = 该基团净得电子(受体); Δ>0 = 失电子(给体)。"
          "催化残基/水的电荷变化揭示 TS 处电荷如何被酶稳定。")

    if a.csv:
        import csv
        with open(a.csv, 'w', newline='') as f:
            w = csv.writer(f); w.writerow(['group'] + labels + ['delta_P_R'])
            for g, qs, d in rows: w.writerow([g] + [f'{q:.4f}' for q in qs] + [f'{d:.4f}'])
        print(f"wrote {a.csv}")

    if a.png:
        try:
            import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt
            x = range(len(labels))
            plt.figure(figsize=(6.5, 4))
            for g, qs, _ in rows:
                plt.plot(x, qs, 'o-', label=g)
            plt.xticks(list(x), labels); plt.ylabel(f'group charge / e ({a.scheme})')
            plt.title('Charge transfer along reaction path'); plt.legend(fontsize=8)
            plt.tight_layout(); plt.savefig(a.png, dpi=140)
            print(f"wrote {a.png}")
        except Exception as e:
            print("PNG skipped:", e)

if __name__ == '__main__':
    main()
