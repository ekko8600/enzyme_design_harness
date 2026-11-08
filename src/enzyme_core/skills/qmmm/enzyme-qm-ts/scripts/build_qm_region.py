#!/usr/bin/env python3
"""build_qm_region.py — Emit CP2K &QMMM QM-region blocks from an Amber prmtop.

Given whole-residue and partial-residue selections plus covalent QM/MM boundary
bonds, prints:
  * &QM_KIND <elem> / MM_INDEX ...   (1-based prmtop atom indices, grouped by element)
  * &LINK blocks (IMOMM) for each cut bond
  * the summed MM charge of the QM atoms (integer-rounded → intended QM CHARGE)

Selection syntax (repeatable):
  --whole RESNAME_or_RESID            e.g. --whole CHO
  --part  RESID:AT1,AT2,...           e.g. --part 89:CG,CD,NE,CZ,NH1,NH2,HE,HH11,HH12,HH21,HH22,HD2,HD3,HG2,HG3
  --link  QM_RESID:QM_AT,MM_RESID:MM_AT   e.g. --link 89:CG,89:CB

Usage:
  build_qm_region.py -p cm_complex.prmtop --whole CHO --part 89:CG,... --link 89:CG,89:CB
"""
import argparse, collections
import parmed as pmd

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('-p', '--prmtop', required=True)
    ap.add_argument('--whole', action='append', default=[], help='RESNAME or 1-based RESID')
    ap.add_argument('--part', action='append', default=[], help='RESID:AT1,AT2,...')
    ap.add_argument('--link', action='append', default=[], help='QMRESID:QMAT,MMRESID:MMAT')
    a = ap.parse_args()

    top = pmd.load_file(a.prmtop)
    qm_atoms = []  # (index0, element, charge)

    def add(atom):
        qm_atoms.append((atom.idx, atom.element_name, atom.charge))

    for sel in a.whole:
        for res in top.residues:
            if sel.isdigit():
                if res.idx == int(sel) - 1:
                    for at in res.atoms: add(at)
            elif res.name == sel:
                for at in res.atoms: add(at)

    for sel in a.part:
        rid, names = sel.split(':')
        names = set(names.split(','))
        res = top.residues[int(rid) - 1]
        for at in res.atoms:
            if at.name in names: add(at)

    # dedup, keep order
    seen = set(); uniq = []
    for idx, el, q in qm_atoms:
        if idx not in seen:
            seen.add(idx); uniq.append((idx, el, q))

    by_elem = collections.defaultdict(list)
    qsum = 0.0
    for idx, el, q in uniq:
        by_elem[el].append(idx + 1)  # 1-based for CP2K
        qsum += q

    print(f"! QM region: {len(uniq)} atoms, summed MM charge = {qsum:+.3f}  (intended CHARGE {round(qsum)})")
    for el in sorted(by_elem):
        # CP2K requires all MM_INDEX values on ONE line (a wrapped continuation
        # is parsed as a new keyword and aborts). Keep it single-line.
        body = ' '.join(str(i) for i in by_elem[el])
        print(f"    &QM_KIND {el}\n      MM_INDEX {body}\n    &END QM_KIND")

    for lk in a.link:
        qm, mm = lk.split(',')
        qr, qa = qm.split(':'); mr, ma = mm.split(':')
        qidx = next(at.idx + 1 for at in top.residues[int(qr)-1].atoms if at.name == qa)
        midx = next(at.idx + 1 for at in top.residues[int(mr)-1].atoms if at.name == ma)
        print(f"    &LINK\n      QM_INDEX {qidx}\n      MM_INDEX {midx}\n      LINK_TYPE IMOMM\n    &END LINK")

if __name__ == '__main__':
    main()
