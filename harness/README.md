# Enzyme Harness

本地后台任务控制器。主 Agent 可以使用 **Codex CLI** 或 **DeepSeek Harness（`dsh`）**，自主规划、调用 Skill、执行和调试；随后由独立的只读会话审查结果。Agent 与模型按任务选择：Codex 运行 GPT 系列模型，`dsh` 运行 DeepSeek 模型，也可以通过 `dsh` 的 provider 路由接入其他 OpenAI 兼容模型。审查路径校验失败会携带实际文件候选回到审查节点重试，已完成科学计算不会因此重跑。任务、进程、日志和收据保存在本地文件中。

```sh
# 使用 Codex + GPT
enzyme-harness setup --agent codex --model gpt-6-luna --reasoning-effort high

# 使用 dsh + DeepSeek
enzyme-harness setup --agent dsh --model deepseek-flash --provider deepseek-official

# 使用 dsh + 其他 provider 路由（需要 OPENAI_API_KEY 等凭据）
enzyme-harness setup --agent dsh --provider openai --model gpt-5.2

# 检查选中的 Agent、版本、bwrap 与模型配置
enzyme-harness doctor
```

`setup` 写入 `$ENZYME_HARNESS_ROOT/config.json`；单个任务可在 `request.json` 中覆盖 `agent`、`model`、`provider`、`reasoning_effort`。切换 `agent` 而未指定 `model` 时使用该 Agent 的默认模型（Codex `gpt-6-luna`，dsh `deepseek-flash`）。

## 开发安装

直接从本仓库运行改过的 Harness（不经过已有 release 指针）时，用独立虚拟环境与独立 root：

```sh
python -m venv .venv && . .venv/bin/activate
python -m pip install -e .
python -m pip install -e ./harness
enzyme-harness --root ~/.local/share/enzyme-harness-dev setup --agent dsh \
  --agent-binary /path/to/deepseek-harness --sandbox-backend none
```

`enzyme-harness` 启动器会优先使用 `--root` 下 `active-release.json` 指向的已激活 release；新的开发 root 没有该指针，因此直接运行当前代码。需要更新已有 root 的 release 时，请按发布流程生成并验证新的 release，不要手工替换。

## 常用命令

```sh
enzyme-harness task submit --request request.json
enzyme-harness task status TASK_ID
enzyme-harness task inspect TASK_ID
enzyme-harness task respond TASK_ID --response response.json
enzyme-harness task resume TASK_ID
enzyme-harness task stop TASK_ID
enzyme-harness task report TASK_ID
```

请求示例：

```json
{"schema_version":"enzyme-harness/request/v1","goal":"分析给定结构并保存结果","inputs":{"structure":"/absolute/path/model.pdb"}}
```

## Agent 与模型

| 字段 | 说明 |
| --- | --- |
| `agent` | `codex` 或 `dsh`。默认 `codex`。 |
| `model` | 模型 id。Codex 直接传给 `codex exec --model`；dsh 写入任务级 `agent-default-model` 覆盖层。 |
| `provider` | 仅 dsh 使用，默认 `deepseek-official`。其他路由名由 `dsh` 的 pi-ai 目录或 `dsh_providers` 声明。 |
| `reasoning_effort` | 适配器努力级别；空字符串表示不传。 |
| `sandbox_backend` | `auto`（默认）或 `none`，见下节。 |
| `agent_binary` | Agent 可执行文件；dsh 也可指向 `bin.js` 或 checkout 目录。 |
| `agent_version` | 可选的精确版本要求。默认接受当前安装的任意版本，并把实际版本写入调用记录；只有显式设置时才要求完全匹配。 |
| `dsh_providers` | dsh 的 `llm-pi-ai` providers 字典，覆盖默认路由声明。 |
| `dsh_patch` | 额外的 dsh `--patch` 覆盖层，先于 Harness 的任务级覆盖应用。 |

## 隔离与沙箱

- **Codex 主会话**：外层 bwrap 只读挂载主机、绑定任务工作区与 `processes`、可见 GPU 设备，Codex 自身以 `danger-full-access` 在该命名空间内运行。审查使用 Codex 只读沙箱，修复使用 `workspace-write`。
- **dsh 主会话**：每个任务有独立的 `DSH_HOME`（`tasks/ID/dsh/home`，任务自有的 `headless` profile），会话与 storage 根也落在任务目录；同样在外层 bwrap 中运行，`dsh` 自身为 `danger-full-access`。审查使用 `read-only`，修复使用 `workspace-write`。
- **`sandbox_backend: auto`** 需要可用的 bwrap（Codex/dsh 主会话）；审查/修复使用各 Agent 自己的沙箱（Codex 的内置沙箱或 dsh 本地沙箱）。bwrap 存在但无法创建用户命名空间时（例如 Ubuntu 的 AppArmor userns 限制），任务会明确报错并提示改用 `none`；本机连 Codex/dsh 自身沙箱也不可用时，同样只有 `none` 能运行。
- **`sandbox_backend: none`** 是显式的“本机没有可用 OS 沙箱”模式：所有角色以完整权限运行，审查仅靠提示词约束并由控制器复核产物摘要；报告和 token 用量会记录 `unconfined_calls`。只有在你接受这一权衡时才使用。

dsh 的 headless profile 不读取 GUI 保存的凭据。模型密钥通过启动环境或任务工作区的 `.env` 提供（如 `DEEPSEEK_API_KEY`、`OPENAI_API_KEY`）；需要恢复已保存凭据或其他 profile 行为时，用 `--dsh-patch` 指向自己的覆盖层。`dsh` 未安装到 `PATH` 时，把 `--agent-binary` 指向可执行文件、`apps/cli/lib/bin.js` 或 DeepSeek Harness checkout 目录（也可设置 `DSH_CHECKOUT`）。

```sh
# 可选：固定到精确版本（默认接受已安装版本）
enzyme-harness setup --agent codex --agent-version 'codex-cli 0.167.0'
enzyme-harness setup --agent codex --sandbox-backend none   # 本机无可用 bwrap 时显式选择
```

普通操作直接使用 Agent 原生工具。长科学计算使用本地包装器，输出参数指命令实际创建的文件；包装器收据独立保存：

```sh
enzyme-harness process run --id analysis-1 --input /absolute/input.pdb --output results/output.tsv -- python analysis.py
```

包装器完成后在任务 `processes/ID/` 保存实际命令、输入摘要、进程身份、stdout/stderr、退出状态、资源及声明的产物摘要。重复 ID 与相同调用复用已有运行，不同调用报冲突。任务恢复先核对运行进程与收据；未知状态不会自动重跑。直接绕过包装器的进程仅保证观察到的覆盖范围。

新任务默认不设置时间、调用、科学运行、存储或下载上限。显式调用次数上限（`max_codex_calls`，统计 Agent 调用次数）可以执行；原生 shell 无法完整计量的科学运行、下载、存储、修复或科学时间上限会在任务创建时明确拒绝，不能静默忽略。`max_science_seconds=0` 和 `max_storage_bytes=0` 表示不限。

独立审查输出 PASS、REPAIR、INSUFFICIENT 或 WAIT_USER；审查要求修复时主会话继续使用已有结果。结构化回复不合法时，控制器会在同一角色上有限重试，而不是直接进入代码修复。源代码修复在候选副本中完成，需要原错误的基线复现、候选复验、可信回归以及隔离安装验证，才能切换 release。任务报告包括结果、审查、进程收据和按 session 去重的 token 用量；dsh 的 `inputTokens` 为未缓存输入，报告会与缓存输入合并成总输入。

旧 MCP 工作流任务保留历史数据，只能查看状态、原始记录与已有报告；旧 `codex-native/v1` 任务按 Codex 继续运行，新任务使用 `native/v2` 协议并把调用记录写入 `calls/`。科学计算结果不等同于实验活性或稳定性。
