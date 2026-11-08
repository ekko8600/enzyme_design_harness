# Chorismate Mutase 校准总结（enzyme-qm-ts skill）

标定体系：Bacillus subtilis chorismate mutase (BsCM, PDB 2CHT)，底物 chorismate 的 [3,3]-sigmatropic
Claisen 重排 → prephenate。目的：验证 skill 全流程 + CP2K QM/MM 方法学。

## 结论：方法学验证成功，定量校准受 frozen-MM 近似限制

### 找到真正的协同过渡态（Dimer-from-TSA）
- 用晶体 TSA（过渡态类似物）确定成键对 = **C9–C4**（六元环 [3,3]，非最近的 C9–C2 五元环），断键 = C–O 醚。
- 用 TSA 构建 TS 初猜 + CP2K Dimer 直接搜鞍点。
- **TS 几何**：C–O = 2.44 Å（半断）、C9–C4 = 2.55 Å（半成）——教科书级协同 Claisen TS。
- **局部曲率 −0.345**（单一负曲率）= 确凿一阶鞍点。
- **ΔE‡ = 37.0 kcal/mol**（E(TS) −464.5819 Ha − E(反应物) −464.6409 Ha）。
- TS 几何等测试工作文件已随清理删除；本文件保留关键数值与经验。

### 为何 37 vs 实验 15.4（偏高 ~2.4×）
1. **冻结全部 MM**（`FIXED_ATOMS MM_SUBSYS MOLECULAR`）→ 酶环境无法弛豫/极化稳定 TS 电荷分布。
   这是"冻结环境绝热映射"的已知系统性偏高——酶催化正是靠环境稳定化把 ~40-50 降到 ~15。
2. QM 区仅底物 + Arg90；其他催化残基（Arg7、Glu78、Arg63、Tyr108）冻在 MM，无法参与。
3. BLYP(GGA) 电子能 ΔE‡，非自由能 ΔG‡（差 ZPE/熵/环境涨落）。

### 要达定量吻合（未做，下一步可选）
- 放开 QM 区周围 ~8–10 Å 的 MM 壳层（残基+水）重优化 TS（去掉 MM_SUBSYS MOLECULAR，改按距离列 mobile 原子）。
- 把关键催化残基（Arg7/Glu78/Arg63）纳入 QM 区（加对应 &QM_KIND/&LINK）。
- 用 B3LYP(+D3) 提精度；如需 ΔG‡ 再叠加频率/采样校正。

## 反应坐标经验（对 skill 通用，重要）
- **协同周环反应（Claisen 等）**：一维单坐标扫描会走错路（只断不成或反之）；须用**反对称 RC** 或
  直接 **Dimer-from-好初猜**。成键/断键原子对最好用晶体 TS 类似物（若有）权威确定。
- **多数酶反应（质子转移、单键形成/断裂、酸碱催化）**：一维 restraint 扫描即可（已验证能驱动）。

## 全流程踩坑清单（都已固化进模板/脚本）
1. CP2K MM_INDEX 必须单行。
2. 非周期体系 `&POISSON` 放 `&DFT` 内 + PSOLVER WAVELET。
3. 40k 原子 GEO_OPT 必须 OPTIMIZER LBFGS（非 BFGS）。
4. GEO_OPT 不执行 SHAKE 硬约束——须用 `&RESTRAINT K` 谐波势驱动 CV。
5. `&COLLECTIVE` 需 `INTERMOLECULAR .TRUE.`（绝对原子索引）。
6. 窗间链接用 EXT_RESTART（勿用轨迹 XYZ，离子名 NA+ 无法映射元素）。
7. COMBINE_COLVAR 须加 `ERROR_LIMIT 1.0E-6`（默认 1e-11 在 TS 附近 MPI_Abort）。
8. TS 搜索的 CG 优化器（平移+旋转）须 `LINE_SEARCH TYPE 2PNT`。
9. 后台长跑用 setsid nohup 单独启动（勿与会报错的清理命令同行）。
