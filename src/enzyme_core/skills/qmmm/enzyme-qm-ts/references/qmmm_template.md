# 酶 QM/MM `&FORCE_EVAL` 模板（由 MQAE benchmark 验证，CP2K 2025.2）

skill 的所有 TS 阶段共用此 `&FORCE_EVAL`，仅换 `&GLOBAL RUN_TYPE` + `&MOTION`（见 ts_workflow_motion.md）。
占位符 `<...>` 由体系准备层填充。

```
&FORCE_EVAL
  METHOD QMMM
  &DFT
    BASIS_SET_FILE_NAME BASIS_MOLOPT
    POTENTIAL_FILE_NAME POTENTIAL
    CHARGE <qm_charge>          ! QM 区净电荷
    MULTIPLICITY <qm_mult>      ! 通常 1（闭壳）
    &MGRID
      COMMENSURATE
      CUTOFF 300                ! 生产建议 400+，收敛测试后定
    &END MGRID
    &QS
      EPS_DEFAULT 1.0E-10
      METHOD GPW                ! 快速扫描可临时改 xTB/DFTB
    &END QS
    &SCF
      EPS_SCF 1.0E-6
      MAX_SCF 50
      &OT T
        MINIMIZER DIIS
        PRECONDITIONER FULL_ALL
      &END OT
      &OUTER_SCF
        EPS_SCF 1.0E-6
        MAX_SCF 20
      &END OUTER_SCF
    &END SCF
    &XC
      &XC_FUNCTIONAL BLYP       ! 酶机理常用 BLYP/B3LYP(+libint)；据体系选
      &END XC_FUNCTIONAL
      &VDW_POTENTIAL            ! 色散校正（DFT-D3，libdftd4 已在镜像）
        POTENTIAL_TYPE PAIR_POTENTIAL
        &PAIR_POTENTIAL
          TYPE DFTD3
          REFERENCE_FUNCTIONAL BLYP
          PARAMETER_FILE_NAME dftd3.dat
        &END PAIR_POTENTIAL
      &END VDW_POTENTIAL
    &END XC
  &END DFT
  &MM
    &FORCEFIELD
      PARMTYPE AMBER
      PARM_FILE_NAME <system>.prmtop   ! 由 AmberTools tleap 生成
      EI_SCALE14 1.0
      VDW_SCALE14 1.0
      &SPLINE
        EMAX_SPLINE 1.0E8
        RCUT_NB [angstrom] 12
      &END SPLINE
    &END FORCEFIELD
    &POISSON
      &EWALD
        EWALD_TYPE SPME
        ALPHA .40
        GMAX <gmax>            ! 奇数，覆盖盒子
      &END EWALD
    &END POISSON
  &END MM
  &QMMM
    ECOUPL GAUSS               ! GEEP 静电嵌入
    USE_GEEP_LIB 15
    &CELL
      ABC <qm_box_abc>         ! QM 子盒，包住 QM 区 + 余量
      PERIODIC XYZ
    &END CELL
    &PERIODIC
      &MULTIPOLE ON
      &END MULTIPOLE
    &END PERIODIC
    ! --- QM 区：按元素列 MM_INDEX（prmtop 原子序号，1-based）---
    &QM_KIND O
      MM_INDEX <o_indices>
    &END QM_KIND
    &QM_KIND N
      MM_INDEX <n_indices>
    &END QM_KIND
    &QM_KIND C
      MM_INDEX <c_indices>
    &END QM_KIND
    &QM_KIND H
      MM_INDEX <h_indices>
    &END QM_KIND
    ! --- 切断共价键处的 link atom（残基切割必需）---
    &LINK
      MM_INDEX <mm_boundary_atom>
      QM_INDEX <qm_boundary_atom>
      LINK_TYPE IMOMM
    &END LINK
  &END QMMM
  &SUBSYS
    &CELL
      ABC <full_box_abc>       ! 全体系盒（含溶剂）
      PERIODIC XYZ
    &END CELL
    &KIND H
      BASIS_SET DZVP-MOLOPT-GTH
      POTENTIAL GTH-BLYP-q1
    &END KIND
    ! ... C(q4) N(q5) O(q6) S(q6) 等，按 QM 区元素补全 ...
    &TOPOLOGY
      CONN_FILE_FORMAT AMBER
      CONN_FILE_NAME <system>.prmtop
      COORD_FILE_FORMAT PDB
      COORD_FILE_NAME <system>.pdb
    &END TOPOLOGY
  &END SUBSYS
&END FORCE_EVAL
```

## 体系准备层要产出的占位符（QM/MM 最难、最需人工确认的部分）
| 占位符 | 来源 | 注意 |
|---|---|---|
| `<system>.prmtop/.pdb` | AmberTools `tleap`（蛋白+底物+溶剂+离子） | 底物需 GAFF/参数化（`antechamber`+`parmchk2`） |
| QM 区 `MM_INDEX` | 活性位点：底物 + 催化残基侧链 + 关键水 | 沿 Cα–Cβ 或 C–C 单键切，避开极性键/共轭 |
| `&LINK` | 每条被切断的 QM–MM 共价键 | 数目 = 切割键数；IMOMM/氢帽 |
| `<qm_charge>/<qm_mult>` | QM 区加和 | 错了 SCF 不收敛或结果错 |
| `<qm_box_abc>` | 包住 QM 区 + ≥6 Å 余量 | |

## 关键提醒
QM 区选择、质子化态（His/Asp/Glu/Cys 的 pKa）、link atom 位置——是结果正确性的命门，
skill 只能给模板+默认+校验，**必须人工复核**。建议先在已知参考体系上标定。
