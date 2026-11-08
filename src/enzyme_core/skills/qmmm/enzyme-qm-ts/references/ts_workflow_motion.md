# 酶 QM/MM 过渡态搜索：四阶段 MOTION 段模板

skill 的计算编排层对**同一个 `&FORCE_EVAL`（QM/MM，见 qmmm_template.md）**替换不同的
`&GLOBAL RUN_TYPE` + `&MOTION`，串成完整 TS 搜索链路。以下为每阶段的 CP2K 2025.2 语法骨架。

## 阶段 0 — 端点几何优化（反应物 R / 产物 P 各一次）
```
&GLOBAL
  RUN_TYPE GEO_OPT
&END GLOBAL
&MOTION
  &GEO_OPT
    OPTIMIZER BFGS          ! 小体系 BFGS；大体系用 LBFGS
    MAX_ITER 300
    MAX_FORCE 4.5E-4        ! [a.u.] ~ 0.023 eV/Å
    &END GEO_OPT
  &CONSTRAINT               ! 可选：QM/MM 中冻结远离活性位点的 MM 骨架
    &FIXED_ATOMS
      LIST <mm_backbone_indices>
    &END FIXED_ATOMS
  &END CONSTRAINT
&END MOTION
```

## 阶段 1 — 最小能量路径（CI-NEB，需 R、P 两个已优化几何）
```
&GLOBAL
  RUN_TYPE BAND
&END GLOBAL
&MOTION
  &BAND
    BAND_TYPE CI-NEB        ! 爬坡镜像，收敛到真实鞍点
    NUMBER_OF_REPLICA 8     ! 起步 7–11
    K_SPRING 0.05
    &CI_NEB
      NSTEPS_IT 5
    &END CI_NEB
    &OPTIMIZE_BAND
      OPT_TYPE DIIS
      &DIIS
        MAX_STEPS 500
      &END DIIS
    &END OPTIMIZE_BAND
    &REPLICA                ! 重复 NUMBER_OF_REPLICA 次，或用 &PROGRAM_RUN_INFO 提供端点
      COORD_FILE_NAME R.xyz
    &END REPLICA
    &REPLICA
      COORD_FILE_NAME P.xyz
    &END REPLICA
  &END BAND
&END MOTION
```
产出：最高能镜像 = 候选 TS，交给阶段 2 精修。

## 阶段 2 — TS 精修（Dimer 法，从候选 TS + 反应坐标向量出发）
```
&GLOBAL
  RUN_TYPE GEO_OPT
&END GLOBAL
&MOTION
  &GEO_OPT
    TYPE TRANSITION_STATE
    OPTIMIZER CG
    MAX_ITER 200
    MAX_FORCE 1.5E-3
    &CG
      &LINE_SEARCH
        TYPE 2PNT          ! ★必须★ TS 搜索的 CG 只支持 2PNT，默认类型会 CPABORT
      &END LINE_SEARCH
    &END CG
    &TRANSITION_STATE
      METHOD DIMER
      &DIMER
        DR 0.01
        INTERPOLATE_GRADIENT
        &ROT_OPT
          OPTIMIZER CG
          MAX_ITER 40
          &CG
            &LINE_SEARCH
              TYPE 2PNT      ! ★旋转优化器同样必须★
            &END LINE_SEARCH
          &END CG
        &END ROT_OPT
      &END DIMER
    &END TRANSITION_STATE
  &END GEO_OPT
  &CONSTRAINT            ! 冻结 MM 只动 QM 区（快；但定量能垒偏高，见下）
    &FIXED_ATOMS
      MM_SUBSYS MOLECULAR
    &END FIXED_ATOMS
  &END CONSTRAINT
&END MOTION
```
（源码证据：`MOTION%GEO_OPT%TRANSITION_STATE%DIMER%ROT_OPT`，input_cp2k_check.F:518）

**Dimer 实战经验（BsCM 校准验证，完整踩坑与结果见 `chorismate_mutase_calibration.md`）：**
- 好的 TS 初猜是关键：有晶体 TS 类似物(TSA)时，用它定成键/断键原子对，并把初猜几何摆成两键半成半断(~2.2Å)。
- 省去 `&DIMER_VECTOR`：从好初猜出发，dimer 自动旋转即可找到负曲率反应模式（曲率由正转负 = 锁定虚频模式）。
- 收敛后：`OPT| Local curvature` 应为负（单一虚频=一阶鞍点）；再做阶段 3 频率正式确认。
- ⚠️ **frozen-MM 使 ΔE‡ 系统性偏高**（BsCM 得 37 vs 实验 15.4）。定量能垒须放开活性位点 MM 壳层
  （去掉 MM_SUBSYS MOLECULAR，改按距离列可动原子）+ 视需要扩 QM 区纳入更多催化残基。

## 阶段 3 — 频率确认（振动分析，验证单一虚频 = 真 TS）
```
&GLOBAL
  RUN_TYPE VIBRATIONAL_ANALYSIS
&END GLOBAL
&VIBRATIONAL_ANALYSIS
  NPROC_REP <n>
  FULLY_PERIODIC .FALSE.
  INTENSITIES .TRUE.
  &PRINT
    &MOLDEN_VIB               ! 输出振型供可视化
    &END MOLDEN_VIB
  &END PRINT
&END VIBRATIONAL_ANALYSIS
```
判据：恰好一个虚频（负本征值），且虚振型沿反应坐标 → 确认 TS。否则回阶段 2 换 dimer 向量/收敛更严。

## 能垒与决速步（编排层计算，非 CP2K 关键字）
- ΔE‡ = E(TS) − E(R)；ΔE_rxn = E(P) − E(R)。多基元步各求一次，最大 ΔE‡ 对应步 = RDS。
- QM/MM 下取电子能；若要自由能垒 → 阶段 1 换 metadynamics/伞形采样（plumed2 已在镜像内）。

## 大体系提速
- QM 区可先用 **xTB (GFN)** 或 **DFTB** 快速扫 MEP，再用 GPW-DFT 精修 TS（CP2K 原生支持切换 QM 方法）。
