# 本机 CP2K / OpenMM 部署（验证于 2026-07-30）

## 已验证路径

本 skill 直接使用本地软件，不需要 Docker 或 Singularity。

| 用途 | 二进制 / 环境 | 启动脚本 |
|---|---|---|
| CP2K CPU | `${ENZYME_DATA_ROOT}/cp2k-main/cpu/install/bin/cp2k.psmp` | `scripts/run_cp2k.sh` |
| CP2K CUDA | `${ENZYME_DATA_ROOT}/cp2k-main/gpu/install/bin/cp2k.psmp` | `scripts/run_cp2k_gpu.sh` |
| 经典 MD | OpenMM 8.5.2，conda `agent` | `scripts/run_md_ensemble.sh` |
| 拓扑/参数化 | AmberTools 24.8，conda `agent` | `param_substrate.sh` / tleap |

CP2K 两套均为 2026.2（revision `2589a58106`）。安装二进制依赖编译时
toolchain；`scripts/cp2k_local_env.sh` 在子进程内加载所需库，并设置
`CP2K_DATA_DIR`。这不会写入 shell 配置，也不会改变 conda `agent`。

## 现场验证结果

同一 H2O/PBE/DZVP 单点：

- CPU：`-32.116398053851945` hartree，正常结束。
- GPU：`-32.116398053851952` hartree，正常结束。
- GPU 输出确认 `ACC: Number of devices/node 1` 和
  `ACC: GPU backend is enabled T`。

CPU/GPU 能量差约 `7e-15` hartree，仅为浮点舍入差。运行
`scripts/selfcheck.sh` 可重复 CPU/GPU 双路验证。

## 构建能力边界

- CPU flags 包含 `omp fftw3 libxc elpa parallel scalapack mpi_f08 libxs
  libxsmm ... tblite`。
- GPU 额外包含 `dbcsr_acc offload_cuda`。
- 本地 CPU/GPU 构建均已启用 `plumed2` 和 `libint`；GPU 使用独立 CUDA 12.8 Toolkit。不要把 PLUMED
  metadynamics 或依赖缺失可选库的任务标为可用；需要时重编 CP2K。
- 本次冒烟验证证明本地 CPU/GPU DFT 计算及 CUDA 后端可执行，不等同于
  对每一种 QM/MM 输入、泛函或增强采样方法做过科学验证。

## 变体选择：默认 GPU，不可用才降级 CPU

两个变体都已在 `the resource configuration selected by ENZYME_CORE_RESOURCES (default: runtime/resources.json)` 的 `runtime_binaries` 中声明：
`cp2k_gpu`（`role: preferred`）与 `cp2k_cpu`（`role: fallback`）。条目里的
`path` 是可校验的本机绝对路径，`env_var` 是直接覆盖可执行文件的变量，
`override_env_var` 是覆盖安装目录的变量。`container` 为 `null`——**不使用
Docker 或 Singularity 镜像**。

`scripts/run_cp2k_auto.sh` 是默认入口：优先 GPU，当 CUDA 加速不可用时降级到
CPU，并打印解析结果与原因，使选择可审计而不是静默发生：

```text
>> CP2K requested=auto variant=gpu reason=cuda_available
>> CP2K requested=auto variant=cpu reason=no_visible_device
```

探测顺序与可能的原因值：

| 原因 | 含义 |
|---|---|
| `cuda_available` | GPU 构建可执行、`nvidia-smi` 存在且有可见设备 |
| `gpu_build_missing` | GPU 构建的 `cp2k.psmp` 不存在或不可执行 |
| `nvidia_smi_missing` | PATH 中没有 `nvidia-smi` |
| `no_visible_device` | `nvidia-smi -L` 没有列出任何 GPU |

`CP2K_VARIANT=auto|gpu|cpu` 控制行为（默认 `auto`）。**显式选择永不被静默改写**：
`CP2K_VARIANT=gpu` 而 CUDA 不可用时，`run_cp2k_auto.sh` 与
`run_cp2k_gpu.sh` 都会以退出码 4 明确失败，而不是悄悄改用 CPU。此时报告中的
`reason` 为 `forced_by_CP2K_VARIANT`，便于与自动降级区分。

## 调用

```bash
# 默认：优先 GPU，CUDA 不可用则降级 CPU 并记录原因
scripts/run_cp2k_auto.sh -i input.inp -o output.out -d WORKDIR -g 0 -t 8

# 只解析变体、不计算（就绪检查）
scripts/run_cp2k_auto.sh --dry-run

# 明确要求 GPU（CUDA 不可用时以退出码 4 失败）
scripts/run_cp2k_gpu.sh -i input.inp -o output.out -d WORKDIR -g 0 -t 8

# 明确要求 CPU
scripts/run_cp2k.sh -i input.inp -o output.out -d WORKDIR -n 1 -t 2
```

默认路径可用 `CP2K_CPU_ROOT`、`CP2K_CPU_SETUP`、`CP2K_GPU_ROOT`、
`CP2K_GPU_SETUP` 覆盖；`CP2K_CPU_BIN` / `CP2K_GPU_BIN` 可直接指定可执行文件
（优先级最高，且正是 manifest 注入的形式）。多 rank 使用当前系统的 OpenMPI
`mpiexec`，可用 `MPIEXEC` 覆盖。

生产 QM/MM 默认走 `run_cp2k_auto.sh`，因此在有 GPU 的机器上默认使用 CUDA
构建；小 QM/MM 或 GPU 不可用时自动落到 CPU 构建。多 GPU 更适合同时运行独立
窗口/突变体。

## OpenMM

`conda agent` 已验证 OpenMM 8.5.2 的 `Reference`、`CPU`、`CUDA`
平台；当前 CUDA 平台可识别 NVIDIA GeForce RTX 5060 Ti。默认用 CUDA
mixed precision，失败时不会静默回退；显式传 `--platform CPU` 才改用 CPU。

OpenMM 读取 AmberTools 生成的 `prmtop` 和 `inpcrd/rst7/PDB`，因此力场
参数不变。引擎替换会带来积分器/恒压器实现差异，详见
`conformation_ensemble_workflow.md`。
