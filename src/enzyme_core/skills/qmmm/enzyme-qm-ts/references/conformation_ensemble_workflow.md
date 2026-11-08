# 四态催化构象建模 工作流（供生成模型条件化）— 可复用流程

把 QM/MM 反应路径导出为蛋白全原子生成模型的条件化输入:每催化态(R/NAC/TS/P)的
①全原子/活性位点构象 ②几何约束/接触图 ③可迁移 theozyme(新 scaffold) ④MD 构象系综。
由 glucoamylase 运行沉淀(worked example: `/data/luoys/tang-mi-new/`)。

## 前置

已完成的 QM/MM 反应路径(restraint 扫描的各窗 restart,见通用扫描流程 + acid-base playbook)。

## 步骤

### 1. 状态 → 扫描窗映射

- **R**(Michaelis)= 反应物阱窗(反应坐标最负端)。
- **NAC**(近攻)= 垒前、亲核原子已到攻击距(~2.8-3.0Å)、scissile 键仍基本完整的窗。
- **TS** = 能垒峰窗(或 Dimer 精修结构)。
- **P**(产物)= 产物阱窗(反应坐标最正端)。
  （用 `analyze_scan.py`/各窗 cv 里的键长挑窗。）

### 2. 单构象 + 几何约束 + theozyme

配置 `extract_cfg.json`(见下)后:

```
extract_conformations.py --config extract_cfg.json --outdir conformations
theozyme_denovo.py       --config theozyme_cfg.json --outdir theozyme   # theozyme_cfg = extract_cfg + anchors + motif_groups
```

产出: `<state>_full.pdb` / `<state>_activesite.pdb` / `constraints_<state>.json` / `contactmap_<state>.csv`
/ `theozyme_denovo.json`(内坐标 d/角/二面角) / `motif_<state>.pdb`(RFdiffusion) / `theozyme.cst`(Rosetta)。

### 3. MD 构象系综（OpenMM CPU/CUDA）

- **R / P(稳态)**:自由 MD。`run_md_ensemble.sh -p PRM -c <state>_full.pdb -t R -g <GPU> -d md`
- **TS(鞍点,不稳)**:反应核心正约束。加 `-m '101,105-110'`；这里是
  `prmtop` 的显式 1-based 原子索引/范围，不是 Amber mask。
- **P 需先做产物再参数化**(见 §4)——MM 拓扑不认断键,不能沿用反应物 prmtop。
- 每态 min→heat(NVT 0→300 K,500 ps)→equil(NPT,500 ps)→prod(NPT,30 ns)。
  OpenMM 读取 AmberTools 生成的同一 `prmtop`，所以 ff14SB/GAFF2/TIP3P 参数
  不变；积分器改为 Langevin-middle，恒压器改为 Monte Carlo barostat。
  这会改变轨迹的随机实现和动力学细节，但在固定系综/力场/约束条件下可用于
  构象采样。不同引擎结果不要按逐帧轨迹比较。

### 4. ★产物 P 再参数化(最费事,必做)★

产物 MM 拓扑 ≠ 反应物(键已断/成、质子已转移)。做法(以 glucoamylase 糖苷水解为例):

1. 按 §1 的 P 几何,把底物**拆成产物分子**(BFS 从反应中心不跨 scissile 键分割),各取重原子。
2. `obabel <frag>_heavy.pdb -O <frag>_h.mol2 -p 7` 补氢(中性)→ `antechamber ... -c bcc -nc 0 -at gaff2` + `parmchk2`。
3. 蛋白**改催化残基质子化态**(酸给了质子→去质子;碱夺了质子→质子化);复用已 pdb4amber 清理的蛋白 PDB 改残基名最稳。
4. `tleap`: 蛋白 + 各产物 mol2 + solvate + addions → 产物 prmtop/inpcrd(产物几何为起点)。
   坑:tleap C 端 OXT 需 pdb4amber 处理过的蛋白(否则 "atom does not have a type");pdb4amber 若 numpy 报错,
   复用反应物的 `protein_clean.pdb` 改残基名。

### 5. 聚类 + NAC 过滤

```
cluster_ensemble.sh -p PRM -x md/<state>_prod.dcd -t <state> -n 20 [--nac "Ow C6 O2 3.2 150"]  # NAC 仅 R
```

产出每态 `<state>_rep.cN.pdb`(N 簇心)+ `<state>_clusters.summary`(布居);R 额外报 NAC 布居%。

## extract_cfg.json 关键字段

```
ref_pdb, states{name:restart}, active_site_resids[], substrate_resid, reacting_center,
key_atoms{label:[resid,name]}, distances[[a,b]..], angles[[a,b,c]..],
anchors[3 个 substrate key_atoms 标签], motif_groups{label:[[resid,name]..]},
theozyme_groups{label:[[resid,name]..]}
```

## ★方法边界 / 给生成模型的建议★

- frozen-MM 扫描的各态几何 = **活性位点局部(theozyme)精确,骨架不变**;骨架系综靠 §3 的全自由 MD 补。
- TS 系综 = 核心约束近似(经典 MD 局限)。
- NAC 自由-MD 布居可能低估(只追初始水;水会交换)——可靠 NAC 单构象用 QM/MM 的 `conformations/NAC_*`;要 NAC 系综做近攻几何约束采样。
- **本套相对 MD/晶体的独有增量 = 电子结构催化指纹**:TS 电荷分布、in-line 攻击角(~175°,NAC/TS 最有判别力的描述子)、
  逐残基静电稳定化(`analyze_charges.py`/`analyze_ts_electrostatics.py`/`REPORT_QMMM.md`)。**强烈建议一并作为条件**——纯几何来源没有。

```

## 可视化 + HTML 报告（`visualize_report.py`）
一步出 SCI 级图 + 自包含 HTML 报告(config 驱动,复用 extract_cfg + 几个 viz 键):
```

visualize_report.py --config viz_cfg.json --outdir figures [--pymol <open-source pymol></open>] [--skip-render]

```
产出:state_<S>.png/overlay.png(PyMOL 活性位点渲染)、fig1_process(能量剖面+四态插图)、
fig_states(四态并排+距离)、fig_overlay、fig_charge、fig_dist、fig_tselec、fig_ensembles(§系综)、**REPORT.html**(图 base64 内嵌)。
报告章节:1 机理能量学 / 2 电荷转移 / 3 断成键 / 4 突变候选 / 5 构象系综 / **6 方法学详述**。
viz_cfg 额外键:system_name, exp_dg, substrate_resid, acid_resid, base_resid, reacting_center, leaving_atom,
conf_dir, scan/charges_csv/tselec_csv 路径, state_windows{R,NAC,TS,P:窗号}。
**★必须用开源 PyMOL(pymol-open-source)★**:Schrödinger incentive/eval 版会打 "For Evaluation Only" 水印,
发表不可用。请使用 `conda agent` 中的开源 `pymol`，或通过 `--pymol` 指定开源版本。
图配色用 Okabe-Ito(色盲安全);中文标题会因 DejaVu 缺 CJK 变方块 → 图内标注用英文。

### ★方法学详述节(`methods_section`,config 驱动,发表必备)★
报告的「6. 方法学详述」逐步记录每步用的方法/软件/力场/DFT/关键参数(供复现审阅),由 `viz_cfg["methods"]` 驱动——
**给了精确值就填精确值,缺省用 skill 标准默认**。子节:6.0 软件版本 / 6.1 体系准备+MM力场 / 6.2 QM/MM设置 /
6.3 路径+TS / 6.4 电子结构+静电 / 6.5 MD系综 + 定量 caveat。`methods` 可选键(全部有默认):
```

cp2k_ver, amber_ver, mm_ff(ff14SB), ligand_ff(GAFF2), water_model(TIP3P), box_buffer(12),
n_atoms, box_dims, coupling(GEEP/ECOUPL GAUSS), qm_functional(BLYP), basis(DZVP-MOLOPT-GTH),
pseudo(GTH-BLYP), cutoff_ry(300), qm_atoms, qm_composition, link_n, qm_box, qm_charge, qm_mult,
rc_desc, n_windows, ts_method, md_ns(30), product_reparam(true)

```
新酶只需按实际运行把这些填进 `methods` 块(见 tang 的 `viz_cfg.json`),报告即自带完整方法学。**每次交付报告务必核对 QM 泛函/基组/截断、MM 力场、QM 区组成与电荷、路径/TS 方法、MD 协议这几项与实际输入一致(勿沿用他酶的旧值)。**

## 系综可视化（`visualize_ensembles.py`）
把 cluster_ensemble.sh 的产物(各态 <state>_rep.cN.pdb + _clusters.summary)可视化:
```

visualize_ensembles.py --ens-dir ensembles --states R,TS,P --nprot <蛋白末残基> 
    --active-site 'resi <底物> or resi <酸> or resi <碱>' [--pymol<open-source></open>] [--outdir figures]

```
产出 `fig_ensembles.png`:蛋白 Cα PCA 构象景观(点=簇心,大小∝布居,按态着色)+ 各态活性位点叠合(构象云)+ top 簇布居条形。
注:PCA 用蛋白 Cα(各态体系蛋白原子一致;底物在 R/TS 与 P 拓扑不同,故不用底物做 PCA)。
```
