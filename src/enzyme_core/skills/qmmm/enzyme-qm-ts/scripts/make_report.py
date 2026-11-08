#!/usr/bin/env python3
"""make_report.py — Synthesize QM/MM reaction results into an enzyme-engineering report.

Pulls together the energy profile (scan_energies.dat), fragment charge transfer
(charges CSV from analyze_charges.py), and optional key-distance/proton-transfer data
into a Markdown report: mechanism summary, electronic/charge picture, TS character,
and heuristic mutation guidance (which residues to conserve vs. candidate hotspots).

The heuristics are data-driven starting points, NOT final designs — the calling agent
should overlay domain knowledge (conservation, structure, feasibility) before proposing
mutations, and every quantitative barrier carries the QM/MM method caveats.

Usage:
  make_report.py --energies scan_energies.dat --charges tang_charges.csv \
                 --system "glucoamylase (GH15) + trisaccharide" \
                 --exp-dg 15-18 --out REPORT.md [--extra key_distances.json]
"""
import argparse, csv, json, os
HARTREE2KCAL = 627.5094740631

def load_profile(fn):
    rows = []
    for l in open(fn):
        p = l.split()
        if len(p) < 3 or p[2] in ('FAILED', 'NA'):
            continue
        rc = None
        for t in p:
            if t.startswith('cv='):
                v = t[3:].split('/')[0]
                rc = None if v in ('NA', '') else float(v)
        try:
            rows.append((int(p[0]), rc if rc is not None else float(p[1]), float(p[2])))
        except ValueError:
            continue
    if not rows:
        return None
    e0 = rows[0][2]
    prof = [(w, rc, (e - e0) * HARTREE2KCAL) for w, rc, e in rows]
    ts = max(prof, key=lambda r: r[2])
    return {'profile': prof, 'barrier': ts[2], 'ts_rc': ts[1], 'rxn': prof[-1][2]}

def load_charges(fn):
    rows = list(csv.DictReader(open(fn)))
    return rows  # each: group, R, TS, P.., delta_P_R

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--energies', required=True)
    ap.add_argument('--charges', default=None)
    ap.add_argument('--system', default='enzyme + substrate')
    ap.add_argument('--exp-dg', default=None, help='experimental ΔG‡ range (kcal/mol) for context')
    ap.add_argument('--extra', default=None, help='JSON with key distances / proton transfers')
    ap.add_argument('--out', default='REPORT.md')
    a = ap.parse_args()

    en = load_profile(a.energies)
    ch = load_charges(a.charges) if a.charges and os.path.exists(a.charges) else None
    extra = json.load(open(a.extra)) if a.extra and os.path.exists(a.extra) else {}

    L = []
    L.append(f"# QM/MM 反应能垒与机理分析报告 — {a.system}\n")
    L.append("> 由 `enzyme-qm-ts` skill 自动生成。定量值为 frozen-MM 绝热映射的电子能 ΔE‡"
             "（非自由能），受 QM 泛函/环境近似影响；作机理与相对趋势解读，定量需按报告末尾精修。\n")

    # 1. energetics
    L.append("## 1. 反应能量学\n")
    if en:
        L.append(f"- **正向能垒 ΔE‡ = {en['barrier']:.1f} kcal/mol**（TS 在 RC={en['ts_rc']:.2f}）")
        L.append(f"- **反应能 ΔE = {en['rxn']:+.1f} kcal/mol**（{'放热/热力学有利' if en['rxn']<0 else '吸热'}）")
        if a.exp_dg:
            L.append(f"- 实验 ΔG‡ ≈ {a.exp_dg} kcal/mol（对照；电子能 vs 自由能差 ZPE/熵）")
        L.append("\n| window | RC | ΔE (kcal/mol) |\n|---|---|---|")
        for w, rc, de in en['profile']:
            mark = " **← TS**" if de == en['barrier'] else ""
            L.append(f"| {w} | {rc:.2f} | {de:.1f}{mark} |")
        L.append("")

    # 2. charge transfer
    if ch:
        L.append("## 2. 电子重排与电荷转移\n")
        L.append("| 基团 | R | TS | P | Δ(P−R) | 角色 |\n|---|---|---|---|---|---|")
        for r in ch:
            keys = [k for k in r if k not in ('group', 'delta_P_R')]
            d = float(r['delta_P_R'])
            role = "得电子(受体)" if d < -0.05 else ("失电子(给体)" if d > 0.05 else "≈不变")
            vals = " | ".join(r[k] for k in keys)
            L.append(f"| {r['group']} | {vals} | {d:+.3f} | {role} |")
        L.append("\n**解读**：Δ 揭示电子如何沿反应重排；TS 处电荷分布指示酶靠哪些相互作用稳定过渡态。\n")

    # 3. proton transfers / key distances
    if extra.get('proton_transfers'):
        L.append("## 3. 质子转移 / 关键键长（沿路径）\n")
        L.append("| 事件 | R | TS | P |\n|---|---|---|---|")
        for name, (r, t, p) in extra['proton_transfers'].items():
            L.append(f"| {name} | {r:.2f} | {t:.2f} | {p:.2f} |")
        L.append("")

    # 4. mutation guidance (heuristics + caveats)
    L.append("## 4. 酶工程指导（启发式，需领域复核）\n")
    if ch:
        movers = sorted(ch, key=lambda r: abs(float(r['delta_P_R'])), reverse=True)
        crit = [r['group'] for r in movers if abs(float(r['delta_P_R'])) > 0.1]
        L.append(f"- **催化关键、勿轻易突变（TS 电荷稳定核心）**：{', '.join(crit) if crit else '—'}。"
                 "这些基团电荷随反应显著变化，直接参与化学步；改动风险高。")
        L.append("- **提活性思路**：增强对 TS 电荷分布的静电互补（在 TS 上电荷增大的基团附近引入互补极性/带电残基），"
                 "可降低 ΔE‡。具体位点需结合 TS 结构在活性位点二壳层寻找。")
        L.append("- **稳定性/底物谱思路**：远离上述催化核心、且不接触底物过渡态的位点，改造对化学步影响小，"
                 "适合做热稳定性(见 erd-l3-thermostability)或底物结合口袋工程。")
    L.append("\n> 突变候选必须叠加：序列保守性、结构可行性、与 TS 的距离/取向，再排序。"
             "本报告给出的是基于电子结构的化学层证据（出处：QM/MM 电荷+能垒），置信度中等。\n")

    # 5. caveats
    L.append("## 5. 方法与不确定性\n")
    L.append("- frozen-MM 绝热映射：MM 环境冻结，未采样构象/自由能；ΔE‡ 为电子能。")
    L.append("- GGA(BLYP) 倾向低估反应垒；定量宜用 B3LYP(+D3) 重算 QM 区。")
    L.append("- 定量精修路线：放开活性位点 MM 壳层 + 杂化泛函 + 振动/采样校正。\n")

    open(a.out, 'w').write("\n".join(L))
    print(f"wrote {a.out}  ({len(L)} lines)")

if __name__ == '__main__':
    main()
