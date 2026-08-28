---
name: enforcement
description: GLM Conductor v2 强制层运行时契约。解释 Stop 完成门 Layer A（ownership 越界校验）、PreToolUse Layer B（派发时注入）、状态层（state.json / events.jsonl）、fail-open 降级行为与 gate_exhausted 后的模型义务；含环境自检步骤与被拦截时的恢复方法。用户询问钩子行为、完成门拦截原因、ENFORCEMENT 报文含义或强制层是否生效时使用。
---

# GLM 强制层：确定性运行时契约

本技能只**解释**强制层的运行时契约，不实现任何强制逻辑——强制由插件钩子（`hooks/`）与运行时模块（`runtime/`）确定性地执行。

## 环境自检（首先执行）

回答"强制层是否生效"之前先自检：

1. `python3 --version` 有输出（钩子解释器在 PATH）
2. `echo '{}' | python3 ~/.zcode/cli/plugins/cache/glm-conductor/glm-conductor/<version>/hooks/stop_gate.py` 退出码为 0（钩子可独立运行；无活动任务时无任何输出）
3. 钩子**仅在安装/更新插件后的新会话中生效**——当前会话若早于插件更新，回答"本会话未挂载，新会话起生效"

## 强制什么

### Layer A — Stop 完成门（确定性）

任务不能在有越界改动时通过完成门：每个声明了 `ownership.files` 的活动任务（state.json 存在且 status 非终态），在主会话 turn 结束时校验「当前 git 工作区改动文件 ⊆ 声明 ownership」：

- **越界存在** → block：报文列出精确的 out-of-scope 路径与已声明 ownership，并给出两条出路（扩 ownership.files / 回退越界改动）
- **无越界 / 未声明 ownership / 无活动任务** → 静默放行，普通会话零干预
- `.glm-conductor/` 运行时目录豁免（编排器自身账本不算仓库改动）

### Layer B — 派发时注入（提示级）

PreToolUse 钩子在每次 Agent/Task 派发前向主会话注入 ownership 契约提醒（声明清单 + "越界将无法通过完成门"）。Layer B 提高合规但**不构成强制**——确定性强制只在 Layer A。

## 哪些钩子执行

`hooks/hooks.json`（随插件分发，安装后自动启用）：

| 事件 | 脚本 | 超时 | 性质 |
| --- | --- | --- | --- |
| Stop | `hooks/stop_gate.py` | 5s | Layer A（block 决策） |
| PreToolUse（Agent\|Task） | `hooks/pre_tool_use.py` | 3s | Layer B（advisory 注入） |

两者均为 `python3` 进程入口。

## 失败处理（fail-open）

强制层自身的故障**不阻断会话**（钩子起不来时 fail-closed 会卡死所有会话），但降级必须可见：

| 情形 | 行为 | 报文 |
| --- | --- | --- |
| 钩子进程崩溃 / import 失败 | 放行 + 报警 | stderr `ENFORCEMENT DEGRADED: stop_gate failed: ...` |
| git 不可用 / 非 git 仓库 | 跳过 ownership 校验 + 记 `gate_degraded` | stderr `ENFORCEMENT DEGRADED: cannot list touched files ...` |
| python3 不在 PATH | 钩子无法启动（ZCode 侧报 hook 运行失败） | 见环境自检；文档前置要求已声明 |

**模型侧义务**：观察到任何 ENFORCEMENT DEGRADED 时，必须如实向用户报告"强制层处于降级态"，不得默认强制仍在生效。

## Stop 循环安全（gate_exhausted）

运行时对 Stop block 续行内建上限（每 turn 最多 3 次），钩子侧同样有界：连续两次 block 后第三次不再拦截，放行并在 stderr 报 `ENFORCEMENT GATE EXHAUSTED`、journal 记 `gate_exhausted`。

**此时模型必须向用户报告 blocked 状态，不得声称任务完成**——放行是循环安全机制，不是完成许可。模型在两次 block 之间完成真实修复（journal 出现其他事件）会重置计数。

## 恢复方法

被 block 时按报文行动：

1. **越界改动是有意的** → 主会话更新 `.glm-conductor/tasks/<task-id>/state.json` 的 `ownership.files` 纳入该路径，然后重新完成
2. **越界改动是误伤** → 回退 out-of-scope 改动后重新完成
3. **反复被拦且无法修复** → 向用户报告完整 block 报文与 out-of-scope 清单，等待人工决策

## 边界（当前版本未强制的事项）

- **验证门 / 审查门 / 证据新鲜度**（alpha2）：完成条件中的"主会话验证完成、ship 裁决有效"尚未接入 Stop 门——当前 Layer A 只校验 ownership
- **审查者只读**：由 agent 定义的只读工具白名单保证（确定性），非钩子强制；reviewer 的越界写入最终会被 Layer A 捕获（纵深防御）
- **子会话内写操作**：ZCode 运行时子代理不触发钩子（Phase 0 实证），写前拦截不可实现——这正是 Layer A/B 设计的由来
