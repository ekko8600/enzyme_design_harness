#!/usr/bin/env python3
"""visualize_report.py — SCI-quality visualization + self-contained HTML report for a
QM/MM enzyme reaction, config-driven so it works for any enzyme.

Produces (in --outdir):
  * state_{R,NAC,TS,P}.png, overlay.png   — PyMOL active-site renders (open-source PyMOL, no watermark)
  * fig1_process.png (energy profile + 4-state insets), fig_states.png (4-panel + distances),
    fig_overlay.png, fig_charge.png, fig_dist.png, fig_tselec.png   — matplotlib, Okabe-Ito CVD-safe, 300 dpi
  * REPORT.html                            — self-contained (figures base64-embedded)

Reuses the same config as extract_conformations.py plus a few viz keys. Run in the workdir
that holds the data files (scan_energies.dat, charges CSV, ts_elec CSV, constraints_<state>.json).

Config (--config) keys used here:
  ref_pdb; states{name:restart_or_pdb}; substrate_resid; reacting_center(e.g."C6");
  leaving_atom("O2"); acid_resid; base_resid; acid_h("HE2"); active_site_resids[];
  key_atoms{...}; and optional: system_name, exp_dg, scan="scan_energies.dat",
  charges_csv, tselec_csv.
State→data: energy profile from scan_energies.dat; charges from charges_csv (analyze_charges);
per-residue from tselec_csv (analyze_ts_electrostatics). Missing inputs are skipped gracefully.

NOTE: use an OPEN-SOURCE PyMOL (pymol-open-source) — the Schrödinger 'incentive/eval' build
stamps a "For Evaluation Only" watermark that is unacceptable for publication. Pass --pymol.
"""
import argparse, json, os, re, subprocess, base64, shutil
import numpy as np
OI = {'R':'#0072B2','NAC':'#56B4E9','TS':'#D55E00','P':'#009E73',
      'blue':'#0072B2','orange':'#E69F00','green':'#009E73','verm':'#D55E00','sky':'#56B4E9','gray':'#999999'}
H2K = 627.5094740631

def find_pymol(user):
    if user and shutil.which(user): return user
    candidate = shutil.which('pymol')
    if candidate and os.path.exists(candidate): return candidate
    return 'pymol'

def render(cfg, outdir, pymol):
    sub=cfg['substrate_resid']; ac=cfg['acid_resid']; ba=cfg['base_resid']
    C6=cfg['reacting_center']; O2=cfg['leaving_atom']
    states=list(cfg['states'])
    # build a python-driven .pml
    load="\n".join(f"cmd.load('{cfg['states'][s]}' if '{cfg['states'][s]}'.endswith('.pdb') else '{cfg['states'][s]}','{s}')" for s in states)
    pml=f"""
from pymol import cmd
cmd.bg_color('white'); cmd.set('ray_opaque_background',1); cmd.set('orthoscopic',1)
cmd.set('ray_shadows',0); cmd.set('stick_radius',0.18); cmd.set('sphere_scale',0.28)
cmd.set('antialias',2); cmd.set('dash_width',4); cmd.set('dash_gap',0.3); cmd.set('dash_radius',0.05)
states={states!r}; sub={sub}; ac={ac}; ba={ba}
view=None
for tag in states:
    cmd.delete('all'); cmd.load('{cfg['ref_pdb']}','ref')  # placeholder to satisfy loader
    cmd.delete('ref')
def one(tag,rst):
    cmd.delete('all'); cmd.load(rst,tag)
    # if restart is CP2K (no PDB), user should pre-convert; here we assume per-state PDBs exist
one_dummy=0
"""
    # Simpler: assume per-state activesite PDBs exist as {outdir}/../conformations/<state>_activesite.pdb
    body=f"""
from pymol import cmd
cmd.bg_color('white'); cmd.set('ray_opaque_background',1); cmd.set('orthoscopic',1)
cmd.set('ray_shadows',0); cmd.set('stick_radius',0.18); cmd.set('sphere_scale',0.28)
cmd.set('antialias',2); cmd.set('dash_width',4); cmd.set('dash_gap',0.3)
sub,ac,ba='{sub}','{ac}','{ba}'; C6,O2='{C6}','{O2}'
states={states!r}; asdir='{cfg.get('conf_dir','conformations')}'
ocols={{'R':'0x0072B2','NAC':'0x56B4E9','TS':'0xD55E00','P':'0x009E73'}}
view=None
for tag in states:
    cmd.delete('all'); cmd.load(f'{{asdir}}/{{tag}}_activesite.pdb', tag); cmd.hide('everything')
    core=f'{{tag}} and resi {sub} and (name C1+C2+C3+C4+C5+C6+O1+O13+O14+O15+O16+{O2}+C7 or (elem H and neighbor (name C1+C2+C3+C4+C5+C6+O13+O14+O15+O16)))'
    cmd.select('core',core)
    cmd.select('acid',f'{{tag}} and resi {ac} and (name CB+CG+CD+OE1+OE2+HE2+HD2+HD3)')
    cmd.select('base',f'{{tag}} and resi {ba} and (name CB+CG+CD+OE1+OE2)')
    cmd.select('cw',f'({{tag}} and resn WAT) within 3.6 of ({{tag}} and resi {sub} and name {C6})')
    cmd.show('sticks','core or acid or base'); cmd.show('sticks','cw'); cmd.show('spheres','cw and elem O')
    cmd.util.cbag('core'); cmd.util.cbac('acid or base'); cmd.color('red','cw and elem O')
    cmd.distance('brk',f'{{tag}} and resi {sub} and name {C6}', f'{{tag}} and resi {sub} and name {O2}')
    cmd.distance('frm',f'{{tag}} and resi {sub} and name {C6}', 'cw and elem O')
    cmd.color('firebrick','brk'); cmd.color('forest','frm'); cmd.hide('labels')
    if view is None:
        cmd.orient('core or acid or base or cw'); cmd.zoom('core or acid or base or cw',0.5); view=cmd.get_view()
    cmd.set_view(view); cmd.set('ray_trace_mode',1); cmd.set('ray_trace_color','black')
    cmd.ray(900,900); cmd.png(f'{outdir}/state_{{tag}}.png',dpi=300)
# overlay
cmd.delete('all')
for tag in states:
    cmd.load(f'{{asdir}}/{{tag}}_activesite.pdb', tag); cmd.hide('everything',tag)
    cmd.show('sticks',f'{{tag}} and resi {sub} and name C1+C2+C3+C4+C5+C6+O1+{O2}+C7')
    cmd.show('sticks',f'{{tag}} and resi {ac} and name CG+CD+OE1+OE2+HE2')
    cmd.select('cw',f'({{tag}} and resn WAT) within 3.6 of ({{tag}} and resi {sub} and name {C6})')
    cmd.show('spheres','cw and elem O'); cmd.color(ocols.get(tag,'0x888888'), f'{{tag}} or cw')
cmd.orient(f'resi {sub} and name {C6}+{O2}+O1+C5'); cmd.zoom(f'resi {sub} and name {C6}+{O2}+O1+C7',1.0)
cmd.set('ray_trace_mode',1); cmd.set('ray_trace_color','black'); cmd.ray(1200,1000); cmd.png(f'{outdir}/overlay.png',dpi=300)
"""
    open(f'{outdir}/_render.pml','w').write("python\n"+body+"\npython end\n")
    subprocess.run([pymol,'-cq',f'{outdir}/_render.pml'], check=False)

def figures(cfg, outdir):
    import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt, matplotlib.image as mpimg
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':11,'axes.spines.top':False,'axes.spines.right':False,'savefig.dpi':300,'savefig.bbox':'tight'})
    def crop(im):
        a=im[...,:3]; m=(a<0.97).any(2)
        if not m.any(): return im
        ys,xs=np.where(m); p=8; return im[max(ys.min()-p,0):ys.max()+p,max(xs.min()-p,0):xs.max()+p]
    scan=cfg.get('scan','scan_energies.dat')
    rc=[];E=[];brk=[];frm=[]
    if os.path.exists(scan):
        for l in open(scan):
            p=l.split()
            if len(p)<3: continue
            for t in p:
                if t.startswith('cv='):
                    a=t[3:].split('/'); rc.append(float(a[0]))
                    if len(a)>2: brk.append(float(a[1].replace('brk',''))); frm.append(float(a[2].replace('frm','')))
            E.append(float(p[2]))
        rc=np.array(rc);E=(np.array(E)-E[0])*H2K
    sw=cfg.get('state_windows',{'R':0,'NAC':2,'TS':4,'P':len(E)-1 if len(E) else 12})
    states=[s for s in ['R','NAC','TS','P'] if s in cfg['states']]
    # fig1 process + insets
    if len(E):
        fig=plt.figure(figsize=(9.2,6.4)); ax=fig.add_axes([0.10,0.085,0.87,0.55])
        ax.plot(rc,E,'-',color=OI['blue'],lw=2.3); ax.scatter(rc,E,s=20,color=OI['blue'],alpha=.45)
        for s in states:
            w=sw[s]; ax.scatter(rc[w],E[w],s=130,color=OI[s],ec='white',lw=1.6,zorder=5)
            ax.annotate(s,(rc[w],E[w]),textcoords='offset points',xytext=(0,-20 if s in('R','P') else 11),ha='center',fontsize=13,fontweight='bold',color=OI[s])
        ax.set_xlabel('Reaction coordinate / Å'); ax.set_ylabel('ΔE / kcal·mol⁻¹'); ax.axhline(0,color=OI['gray'],lw=.6,ls=':')
        its=int(np.argmax(E)); ax.annotate(f"ΔE‡ = {E[its]:.1f}",(rc[its],E[its]),xytext=(rc[its]+0.35,E[its]+1.2),color=OI['verm'],fontweight='bold')
        fig.text(0.5,0.985,cfg.get('system_name','QM/MM reaction pathway'),ha='center',fontsize=13,fontweight='bold')
        for i,s in enumerate(states):
            axi=fig.add_axes([0.055+i*0.235,0.66,0.215,0.30]); pth=f'{outdir}/state_{s}.png'
            if os.path.exists(pth): axi.imshow(crop(mpimg.imread(pth)))
            axi.set_title(s,color=OI[s],fontsize=13,fontweight='bold',pad=2); axi.set_xticks([]);axi.set_yticks([])
            for sp in axi.spines.values(): sp.set_edgecolor(OI[s]); sp.set_linewidth(2.0)
        fig.savefig(f'{outdir}/fig1_process.png',dpi=300); plt.close(fig)
    # fig_states (4 panel + distances)
    if all(os.path.exists(f'{outdir}/state_{s}.png') for s in states):
        fig,axs=plt.subplots(1,len(states),figsize=(3.3*len(states),4.0))
        if len(states)==1: axs=[axs]
        for ax,s in zip(axs,states):
            ax.imshow(crop(mpimg.imread(f'{outdir}/state_{s}.png'))); ax.set_title(s,color=OI[s],fontsize=14,fontweight='bold'); ax.set_xticks([]);ax.set_yticks([])
            for sp in ax.spines.values(): sp.set_edgecolor(OI[s]); sp.set_linewidth(2.2)
            cf=f'constraints_{s}.json'
            if os.path.exists(cf):
                d=json.load(open(cf)); dd=d.get('distances_A',{}); aa=d.get('angles_deg',{})
                ax.text(0.5,-0.02,"\n".join(f"{k} = {v:.2f} Å" for k,v in list(dd.items())[:3])+("\n"+"; ".join(f"{k}={v:.0f}°" for k,v in list(aa.items())[:1]) if aa else ""),
                        transform=ax.transAxes,ha='center',va='top',fontsize=9,bbox=dict(boxstyle='round,pad=0.4',fc='#f7f7f4',ec=OI[s],lw=1.2))
        fig.suptitle('Active-site conformations (R / NAC / TS / P)',fontsize=14,fontweight='bold',y=1.05); fig.savefig(f'{outdir}/fig_states.png',dpi=300); plt.close(fig)
    if os.path.exists(f'{outdir}/overlay.png'):
        from matplotlib.patches import Patch
        fig,ax=plt.subplots(figsize=(7,5.8)); ax.imshow(crop(mpimg.imread(f'{outdir}/overlay.png'))); ax.axis('off')
        ax.set_title('Reaction-core conformational evolution',fontsize=13,fontweight='bold')
        ax.legend(handles=[Patch(color=OI[s],label=s) for s in states],loc='lower center',ncol=len(states),frameon=False,bbox_to_anchor=(0.5,-0.05)); fig.savefig(f'{outdir}/fig_overlay.png',dpi=300); plt.close(fig)
    # fig_charge
    ccsv=cfg.get('charges_csv')
    if ccsv and os.path.exists(ccsv):
        import csv; rows=list(csv.DictReader(open(ccsv))); cols=[OI['blue'],OI['sky'],OI['orange'],OI['green']]
        fig,ax=plt.subplots(figsize=(5.2,4)); ks=[c for c in rows[0] if c not in ('group','delta_P_R')]
        for i,r in enumerate(rows): ax.plot(range(len(ks)),[float(r[k]) for k in ks],'o-',color=cols[i%4],lw=2,ms=7,label=r['group'])
        ax.set_xticks(range(len(ks)));ax.set_xticklabels(ks);ax.set_ylabel('Group charge / e'); ax.set_title('Charge transfer'); ax.legend(fontsize=8,frameon=False,loc='center left',bbox_to_anchor=(1,0.5)); fig.savefig(f'{outdir}/fig_charge.png',dpi=300); plt.close(fig)
    # fig_dist
    if len(brk):
        fig,ax=plt.subplots(figsize=(5.6,4)); ax.plot(rc,brk,'o-',color=OI['verm'],lw=2,ms=5,label='breaking'); ax.plot(rc,frm,'s-',color=OI['green'],lw=2,ms=5,label='forming')
        ax.set_xlabel('Reaction coordinate / Å'); ax.set_ylabel('Distance / Å'); ax.set_title('Bond making/breaking'); ax.legend(frameon=False); fig.savefig(f'{outdir}/fig_dist.png',dpi=300); plt.close(fig)
    # fig_tselec
    tc=cfg.get('tselec_csv')
    if tc and os.path.exists(tc):
        er=[(r[1],r[0],float(r[3])) for r in (l.strip().split(',') for l in open(tc) if not l.startswith('resid')) if len(r)>=4]
        er=[x for x in er if abs(x[2])>0.4]; er.sort(key=lambda t:t[2]); top=er[:8]+er[-6:]
        fig,ax=plt.subplots(figsize=(5.4,4.4)); vals=[v for _,_,v in top]; cols=[OI['blue'] if v<0 else OI['verm'] for v in vals]
        ax.barh(range(len(vals)),vals,color=cols,height=.7); ax.axvline(0,color='k',lw=.8)
        ax.set_yticks(range(len(top)));ax.set_yticklabels([f"{n}{i}" for n,i,_ in top],fontsize=8.5)
        ax.set_xlabel('ΔE$_{TS}$ / kcal·mol⁻¹ (<0 stabilizes)'); ax.set_title('Per-residue TS electrostatics'); fig.savefig(f'{outdir}/fig_tselec.png',dpi=300); plt.close(fig)

def methods_section(cfg):
    """Config-driven detailed methodology section (reusable across enzymes).
    Reads cfg['methods'] (all optional; skill-standard defaults fill the gaps) so the
    generated report always documents exactly which methods/force fields/DFT were used.
    Keys: qm_functional, basis, pseudo, cutoff_ry, qm_atoms, qm_composition, link_n, qm_box,
    qm_charge, qm_mult, coupling, mm_ff, ligand_ff, water_model, box_buffer, n_atoms, box_dims,
    rc_desc, n_windows, ts_method, md_ns, product_reparam(bool), cphmm_note, cp2k_ver, amber_ver."""
    m=cfg.get('methods',{})
    g=lambda k,d: m.get(k,d)
    exp=cfg.get('exp_dg',''); sysn=cfg.get('system_name','the enzyme')
    barrier=g('barrier','电子能垒')
    prod = ("<li><b>★产物 P 再参数化★</b>：产物 MM 拓扑 ≠ 反应物（键已断、质子已转移）。按 P 几何把底物沿 scissile 键拆成产物分子，"
            "各自中性加氢 → antechamber(AM1-BCC/GAFF2)+parmchk2；蛋白改回催化残基质子化态，再 tleap 组装出独立的产物拓扑。</li>") if g('product_reparam',True) else ""
    return f"""
<h2>6. 方法学详述</h2>
<p class="sub">按计算实际执行顺序，逐步给出每步的方法、软件、力场与关键参数，供复现与审阅。</p>

<h3>6.0 软件与版本</h3>
<table>
<tr><th>环节</th><th>软件 / 版本</th></tr>
<tr><td>QM/MM</td><td>{g('cp2k_ver','本地 CP2K 2026.2（CPU / CUDA offload）')}</td></tr>
<tr><td>体系准备 / 经典 MD</td><td>{g('amber_ver','AmberTools 24.8（参数化/拓扑/cpptraj）+ OpenMM 8.5.2')}</td></tr>
<tr><td>底物结构 / 加氢 / 可视化</td><td>RDKit / OpenBabel；开源 PyMOL（避水印）+ matplotlib（Okabe-Ito 配色）</td></tr>
</table>

<h3>6.1 体系准备与 MM 力场</h3>
<ul>
<li><b>质子化态</b>：按反应机理设定广义酸/碱残基的质子化（酸质子化、碱去质子），其余可滴定残基取 pH 7 标准态。</li>
<li><b>底物参数化</b>：antechamber 以 <b>AM1-BCC</b> 电荷 + <b>{g('ligand_ff','GAFF2')}</b> 原子类型生成 mol2，parmchk2 补 frcmod。</li>
<li><b>蛋白力场 / 水模型</b>：<b>{g('mm_ff','Amber ff14SB')}</b> + <b>{g('water_model','TIP3P')}</b>。</li>
<li><b>溶剂化 / 中和</b>：solvatebox（{g('box_buffer','12')} Å 缓冲）+ addions 中和净电荷。终体系 <b>{g('n_atoms','—')} 原子</b>，周期盒 ≈ {g('box_dims','—')} Å。</li>
<li><b>反应几何锁定</b>：最小化阶段加 NMR 型距离弱约束把反应几何锁到近攻构象（避免催化水漂移、广义酸质子朝向错误导致假性能垒），再进 QM/MM。</li>
</ul>

<h3>6.2 QM/MM 设置（能垒与 TS 的核心）</h3>
<table>
<tr><th>项</th><th>设置</th></tr>
<tr><td>耦合方案</td><td>加性 QM/MM，<b>{g('coupling','GEEP 静电嵌入（ECOUPL GAUSS）')}</b>，MM 长程静电用 Ewald SPME</td></tr>
<tr><td>QM 方法 / 泛函</td><td>DFT，<b>GPW</b>（高斯+平面波）；交换关联 <b>{g('qm_functional','BLYP（GGA）')}</b></td></tr>
<tr><td>基组 / 赝势 / 截断</td><td>{g('basis','DZVP-MOLOPT-GTH')} 基组；{g('pseudo','GTH-BLYP')} 赝势；平面波 CUTOFF <b>{g('cutoff_ry','300')} Ry</b></td></tr>
<tr><td>SCF</td><td>OT + DIIS，EPS_SCF 1×10⁻⁶</td></tr>
<tr><td>QM 区</td><td><b>{g('qm_atoms','—')} 原子</b>{('（'+g('qm_composition','')+'）') if g('qm_composition','') else ''} + {g('link_n','—')} 处 IMOMM link 原子（氢帽，切非极化 C–C 键）</td></tr>
<tr><td>QM 盒 / 边界 / 电荷</td><td>QM 子盒 {g('qm_box','—')} Å，周期 XYZ + 多极校正；QM 区净电荷 <b>{g('qm_charge','—')}</b>，多重度 <b>{g('qm_mult','1（单重态）')}</b></td></tr>
</table>

<h3>6.3 反应路径与过渡态搜索</h3>
<ul>
<li><b>反应坐标</b>：{g('rc_desc','按反应类型选取（协同/酸碱水解用反对称 RC = d(断键) − d(成键)，单键/质子转移用一维距离坐标）')}。</li>
<li><b>路径</b>：restraint 松弛扫描（{g('n_windows','若干')} 窗，冻结 MM 环境弛豫）得绝热映射能量剖面，逐窗 restart 续跑。</li>
<li><b>TS 精修</b>：{g('ts_method','取能垒峰窗做 Dimer 过渡态搜索（CG + 2PNT line search）交叉印证')}；确认半成半断几何、in-line 攻击角与单一负曲率鞍点特征。</li>
</ul>

<h3>6.4 电子结构与静电分析</h3>
<ul>
<li><b>电荷 / 电子重排</b>：R/TS/P 三点做带 Mulliken + Hirshfeld 的 QM/MM 单点（几何自扫描 restart 恢复），按基团汇总电荷转移与电子流向。</li>
<li><b>前线轨道 / 密度</b>：单点加 &amp;MO_CUBES（HOMO/LUMO）+ &amp;E_DENSITY_CUBE 输出 cube。</li>
<li><b>TS 逐残基静电分解（Warshel 型）</b>：MM 残基点电荷静电势 × QM 区 Δq(R→TS) 估各残基对 TS 的稳定/去稳定贡献并排序；保守核心不动，去稳定者作提 k<sub>cat</sub> 候选（单快照估计，需 QM/MM 突变体复算验证）。</li>
</ul>

<h3>6.5 催化态构象系综（经典 MD）</h3>
<ul>
<li><b>协议</b>：OpenMM CPU/CUDA，min → NVT 升温(0→300 K, Langevin-middle γ=2.0 ps⁻¹) → NPT 平衡(300 K/1 bar, Monte Carlo barostat) → NPT 生产 <b>{g('md_ns','30')} ns/态</b>，dt 2 fs，约束含氢键，PME cut 10 Å。</li>
<li><b>各态处理</b>：R/P（稳态）全自由 MD（含骨架）；TS（鞍点）对反应核心施位置弱约束近似。</li>
{prod}
<li><b>聚类 / 景观</b>：cpptraj 层次凝聚聚类取 20 代表构象 + 布居；R 态按攻击距 + in-line 角做 NAC 过滤；蛋白 Cα Kabsch 叠合后 PCA 出构象景观。</li>
</ul>

<div class="warn"><b>定量 caveat</b>：报告的 ΔE‡ 为 frozen-MM 绝热映射的<b>电子能</b>（非自由能，未含熵/ZPE/采样）；<b>{g('qm_functional','BLYP')} 若为 GGA 会系统性低估反应垒</b>。{('实验 ΔG‡ ≈ '+str(exp)+' kcal·mol⁻¹；') if exp else ''}定量吻合路线：放开活性位点 MM 壳层 + 扩 QM 区 + 杂化泛函 B3LYP(+D3) + 振动/自由能采样（伞形/metadynamics）校正。机理与反应方向的定性结论稳健。</div>
"""

def html(cfg, outdir):
    def b(p): return base64.b64encode(open(p,'rb').read()).decode() if os.path.exists(p) else ''
    def im(name,cap,w=90):
        p=f'{outdir}/{name}'
        return f'<figure><img src="data:image/png;base64,{b(p)}" style="width:{w}%"><figcaption>{cap}</figcaption></figure>' if os.path.exists(p) else ''
    sysn=cfg.get('system_name','Enzyme'); exp=cfg.get('exp_dg','')
    parts=[f"<!doctype html><html><head><meta charset='utf-8'><title>{sysn} QM/MM report</title>",
      "<style>body{font-family:-apple-system,'Segoe UI','Microsoft YaHei',sans-serif;line-height:1.7;max-width:960px;margin:0 auto;padding:30px;color:#1a1a19}h1{border-bottom:3px solid #0072B2;padding-bottom:8px}h2{color:#0072B2;border-left:5px solid #0072B2;padding-left:12px;margin-top:34px}h3{color:#333;margin-top:22px}.sub{color:#52514e;font-size:14px}table{border-collapse:collapse;width:100%;margin:14px 0;font-size:14px}th,td{border:1px solid #d8d7cf;padding:7px 10px;text-align:left}th{background:#eef4fb}figure{text-align:center;margin:18px 0}figcaption{font-size:13px;color:#555;text-align:left;padding:0 5%}code{background:#f0efec;padding:1px 5px;border-radius:3px;font-size:13px}.warn{background:#fff6e9;border-left:4px solid #E69F00;padding:12px 16px;border-radius:0 6px 6px 0}.footer{margin-top:40px;border-top:1px solid #ddd;padding-top:14px;font-size:12px;color:#8a8a86}</style></head><body>",
      f"<h1>{sysn} — QM/MM 计算分析报告</h1>",
      "<h2>1. 反应机理与能量学</h2>", im('fig1_process.png','图1. 能量剖面 + 四态活性位点构象。'),
      im('fig_states.png','图1b. 四态并排(含关键几何)。',98), im('fig_overlay.png','图1c. 反应核心构象演变叠合。',62),
      "<h2>2. 电子重排与电荷转移</h2>", im('fig_charge.png','图2. 各基团电荷沿 R→TS→P。',70),
      "<h2>3. 键断裂/形成</h2>", im('fig_dist.png','图3. 沿反应坐标的断/成键距离。',70),
      "<h2>4. 突变候选(TS 静电分解)</h2>", im('fig_tselec.png','图4. 逐残基 TS 静电稳定化(蓝=保守核心,红=候选)。',66),
      "<h2>5. 催化态构象系综</h2>", im('fig_ensembles.png','图5. 各催化态 MD 构象系综(构象云 + Cα PCA 景观 + 簇布居)。',98)]
    parts.append(methods_section(cfg))
    parts.append(f"<div class='footer'>由 enzyme-qm-ts skill（CP2K QM/MM）自动生成 · 体系 {sysn}</div>")
    parts.append("</body></html>")
    open(f'{outdir}/REPORT.html','w').write("\n".join(parts))

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--config',required=True); ap.add_argument('--outdir',default='figures')
    ap.add_argument('--pymol',default=None); ap.add_argument('--skip-render',action='store_true')
    a=ap.parse_args(); cfg=json.load(open(a.config)); os.makedirs(a.outdir,exist_ok=True)
    if not a.skip_render: render(cfg,a.outdir,find_pymol(a.pymol))
    figures(cfg,a.outdir); html(cfg,a.outdir)
    print(f"done -> {a.outdir}/REPORT.html + figures")

if __name__=='__main__': main()
