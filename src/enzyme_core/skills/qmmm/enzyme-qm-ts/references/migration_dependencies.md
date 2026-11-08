# 运行依赖与迁移

## 当前节点

默认激活 conda `agent`。不要安装 Amber/PMEMD 24，也不需要 CP2K
Docker 镜像。

| 组件 | 用途 | 当前状态 |
|---|---|---|
| 本地 CP2K 2026.2 CPU/GPU | QM/MM、路径与 TS | 已验证 CPU/GPU 单点 |
| OpenMM 8.5.2 | 经典 MD 系综 | CPU/CUDA 平台已验证 |
| AmberTools 24.8 | antechamber、parmchk2、tleap、pdb4amber、cpptraj | 保留 |
| OpenBabel、ParmEd、NumPy | 参数化与体系处理 | conda `agent` |
| matplotlib、MDAnalysis、MDTraj | 分析与轨迹处理 | conda `agent` |
| pymol-open-source | 发表图（可选） | 按报告需要 |

AmberTools 只生成/检查 AMBER 格式的力场与拓扑；OpenMM 直接读取这些
参数进行 MD。不要把 “AMBER 力场” 与商业 Amber/PMEMD 引擎混为一谈。

## 冒烟检查

```bash
conda run -n agent python -c \
  "import openmm; print(openmm.version.version, [openmm.Platform.getPlatform(i).getName() for i in range(openmm.Platform.getNumPlatforms())])"
conda run -n agent bash -lc \
  "command -v antechamber parmchk2 tleap pdb4amber cpptraj"
scripts/selfcheck.sh
```

## 本机路径覆盖

| 变量 | 默认 |
|---|---|
| `OPENMM_CONDA_ENV` | `agent` |
| `PROJECT_CONDA_SH` | `${ENZYME_CONDA_ROOT}/etc/profile.d/conda.sh` |
| `CP2K_CPU_ROOT` | `${ENZYME_DATA_ROOT}/cp2k-main/cpu/install` |
| `CP2K_CPU_SETUP` | `${ENZYME_DATA_ROOT}/cp2k-main/cpu/source/tools/toolchain/install/setup` |
| `CP2K_GPU_ROOT` | `${ENZYME_DATA_ROOT}/cp2k-main/gpu/install` |
| `CP2K_GPU_SETUP` | `${ENZYME_DATA_ROOT}/cp2k-main/gpu/source-conda/tools/toolchain/install/setup` |
| `MPIEXEC` | `mpiexec` |

CP2K toolchain 仅由启动脚本的子进程加载，不会持久修改 conda 环境。

## 迁移到新服务器

1. 安装一套可运行的 CP2K `cp2k.psmp`，记录 prefix 与 toolchain setup。
2. 创建 conda 环境，安装 `openmm ambertools openbabel parmed numpy
   matplotlib mdanalysis mdtraj`。
3. 通过上述环境变量指向新 CP2K 路径。
4. 运行 `selfcheck.sh`，再用真实体系做一个短 OpenMM 检查。
5. 本地 CPU/GPU 构建均已带 `plumed2` 与 `libint`，GPU 使用独立 CUDA 12.8 Toolkit。只有需要 PLUMED metadynamics 时才
   构建没有该 flag。

## MD 方法注意事项

- R/P 可自由 MD；TS 必须对反应核心施加显式 1-based 原子索引的位置约束。
- 产物发生断键/成键或质子转移后必须重新生成产物拓扑。
- 默认协议为最小化、500 ps 0→300 K NVT、500 ps NPT、30 ns NPT，
  2 fs、含氢键约束、10 Å PME cutoff。
- OpenMM 使用 `LangevinMiddleIntegrator` 与
  `MonteCarloBarostat`；它们不与 PMEMD 的 Langevin/弱耦合恒压算法逐步
  等价。比较突变体或催化态时固定版本、平台、精度、随机种子和协议。
- 聚类仍可使用 AmberTools 的 `cpptraj` 读取 OpenMM DCD。

## 科学边界

QM 区、质子化态、link atom、产物拓扑必须人工复核。成功运行只证明软件
链路可用，不证明能垒或 TS 已经科学验证。frozen-MM 能垒偏差和反应类型
选择规则仍按本 skill 的校准文档执行。
