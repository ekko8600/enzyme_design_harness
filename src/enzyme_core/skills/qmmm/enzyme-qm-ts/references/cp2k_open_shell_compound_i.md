# 全水 QM/MM 中 P450 Compound-I 的 CP2K 初猜与收敛

本页记录在完整蛋白、完整周期显式水、heme/Compound-I 与双底物的 CP2K 2026.2 QM/MM 体系上复现的经验。
它是数值收敛指南，不替代对质子化态、QM 区和反应机理的人工复核。

## 电子态依据

P450 Cpd-I 可表述为 `Fe(IV)=O` 三重态核与 heme π-cation radical 的耦合：

- **双重态**：铁氧核的两个 α 未成对电子与 heme π 自由基的 β 电子反铁磁耦合，净 `Nα-Nβ=1`；
- **四重态**：上述三个未成对电子同向耦合，净 `Nα-Nβ=3`。

因此仅给 `MULTIPLICITY 2` 或 `4` 常不足以得到相应解。CP2K 的 `KIND/MAGNETIZATION` 与
`KIND/BS` 仅影响 `SCF_GUESS ATOMIC` 的初始密度，但对防止解坍塌至错误电子态至关重要。

来源：

- Shaik 等的全蛋白 QM/MM Cpd-I/Cpd-II 研究给出 Cpd-I 的
  `δ² π*xz¹ π*yz¹ a2u¹` 组态，以及双重态（heme 自由基反向）和四重态（同向）耦合。
  https://pmc.ncbi.nlm.nih.gov/articles/PMC6073316/
- CP2K 2026.2 收敛文档说明复杂开放壳层需要用 `MAGNETIZATION` 或 `BS` 制备初猜，
  并建议从较低成本的已收敛波函数 restart。
  https://manual.cp2k.org/cp2k-2026_2-branch/methods/dft/convergence.html

## 推荐的初猜构造

在 `QM_KIND` 中将 **20 个 porphyrin macrocycle 碳**独立为 `CPor`，不要把整个 heme 的
全部侧链碳并入该 kind。对于每个 `CPor` 设磁化 `±1/20`，使其总自旋为一个电子；Fe 保持独立 kind。

```text
# doublet Cpd-I BS seed: total +2 (Fe) -1 (porphyrin) = +1
&KIND Fe
  ...
  MAGNETIZATION 2.0
&END KIND
&KIND CPor
  ELEMENT C
  ...
  MAGNETIZATION -0.05
&END KIND

# quartet seed: total +2 (Fe) +1 (porphyrin) = +3
&KIND CPor
  ELEMENT C
  ...
  MAGNETIZATION 0.05
&END KIND
```

上述 `MAGNETIZATION` 是初猜，而非自旋约束。收敛后必须输出并检查 Mulliken/Hirshfeld 自旋布居，
确认自旋没有坍塌；双/四重态必须在相同 cutoff、同一几何、相同严格 `EPS_SCF` 下才可比较能量。

## 全水体系的内存与 SCF 阶梯

1. 不要因 `exit 137` 自动裁剪蛋白或删除水。先保留原体系做 `CUTOFF` 阶梯测试。
2. 已验证：约 9 万原子的周期水体系，在 `CUTOFF 300 Ry` 时可超过 30 GiB 主机内存；
   `100 Ry` 的初猜阶段约需 4–5 GiB。内存主要受周期 GPW 网格影响。
3. `100 Ry` 只用于产生电子初猜。达到严格波函数后按 `100 → 150 → 200 → 300 Ry`
   restart；最终端点、路径、TS 和频率只能使用经 cutoff 收敛验证的设置。
4. 推荐先以宽松 `EPS_SCF`（例如 `1e-3` 或 `2e-3`）写出 WFN，再逐级收紧到 `1e-4`、`1e-5`。
   宽松阶段的能量不得用于自旋排序或能垒报告。
5. 若 OT-DIIS 在约 `1e-3` 平台振荡，先减小 `STEPSIZE`；若仍不收敛，改变 Cpd-I 的 BS 初猜，
   不要把未收敛能量带到下一阶段。
6. 本机全水 Cpd-I 的轨道级 Fe(IV) `3d4` BS 种子中，`EPS_SCF=5e-4` 配合
   `OT/DIIS, STEPSIZE 0.01` 可从已收敛的 `1e-3` 波函数继续至阈值；`STEPSIZE 0.02`
   则在约12步后发散。该经验只适用于同一输入与几何，仍须检查收敛能量是否稳定，不能把
   “刚低于阈值”的慢收敛解直接用于自旋排序。

## CP2K 输入与运行陷阱

- `ECOUPL GAUSS` 的 QMMM 计算需要 `&MGRID COMMENSURATE`；缺失会中止。
- 周期 QMMM 应使用 `&QMMM/&CELL ... PERIODIC XYZ` 与
  `&QMMM/&PERIODIC/&MULTIPOLE ON`；不要把 `XYZ` 作为 `&PERIODIC` 的裸关键字。
- `WFN_RESTART_FILE_NAME` 位于 `&DFT`，不是 `&SCF`。
- 对同一个 `KIND`，CP2K 不允许将非零 `MAGNETIZATION` 与 `&BS ON` 同时使用；采用轨道级
  `&BS` 时应去掉该 kind 的 `MAGNETIZATION`，并确保 `SCF_GUESS ATOMIC`。
- `&SMEAR` 需额外虚轨道，不能与默认 OT 直接混用；它是对角化 SCF 的备选手段。
- 含 Fe 的 mol2/PDB 转换可能将元素写为 `F`；运行前核对 Fe 原子的 PDB 元素栏和
  QM kind 为 `Fe`。
- GPU wrapper 是默认生产路径，但 GPU 不能替代主机 RAM；记录 GPU、主机内存峰值与退出码。

## 结果门槛

只有当严格 SCF 正常结束、总能量存在、局域自旋分布合理、双/四重态设置一致时，
才能开始端点优化和自旋能比较。任何低 cutoff、宽松 SCF、被停止或 `exit 137` 的作业都仅是诊断证据。
