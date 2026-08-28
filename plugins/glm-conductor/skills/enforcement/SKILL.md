---
name: enforcement
description: GLM Conductor v2 强制层运行时契约。解释 Stop 完成门四重检查（ownership 越界 + 验证完成 + 审查有效 + 证据新鲜度 stale 检测）、PreToolUse Layer B（派发时注入）与 Bash 策略门控（allow/ask/deny 决策）、状态层（state.json / events.jsonl）、证据指纹绑定（task_fingerprint）、fail-open 降级行为与 gate_exhausted 后的模型义务；含环境自检步骤与被拦截时的恢复方法。用户询问钩子行为、完成门拦截原因、verification_stale / review_stale 含义、ENFORCEMENT 报文含义或强制层是否生效时使用。
---

# GLM 强制层：确定性运行时契约

本技能只**解释**强制层的运行时契约，不实现任何强制逻辑——强制由插件钩子（`hooks/`）与运行时模块（`runtime/`）确定性地执行。

## 环境自检（首先执行）

回答"强制层是否生效"之前先自检：

1. `python3 --version` 有输出（钩子解释器在 PATH）
2. `echo '{}' | python3 ~/.zcode/cli/plugins/cache/glm-conductor/glm-conductor/<version>/hooks/stop_gate.py` 退出码为 0（钩子可独立运行；无活动任务时无任何输出）
3. 钩子**仅在安装/更新插件后的新会话中生效**——当前会话若早于插件更新，回答"本会话未挂载，新会话起生效"

## 强制什么

### Layer A — Stop 完成门（确定性，alpha2 起为四重检查）

活动任务（state.json 存在且 status 非终态）在主会话 turn 结束时按固定顺序做四重检查，任一失败即 block（报文可行动：列出缺失项/过期证据与恢复动作）。**参与判定**：ownership 声明非空、verification.required 非空、review.required 为 true、visual_evidence 非空——四者任一成立即受门跟踪；全部为空的任务不参与（普通会话零干预）。

| # | 检查 | 通过条件 | 失败形态（journal `check` 字段） |
| --- | --- | --- | --- |
| 1 | ownership（Layer A 原有） | git 改动文件 ⊆ 声明 ownership.files | `ownership`（报文列 out-of-scope 路径与两条出路） |
| 2 | 验证 | required 命令全部 completed，且 verification.fingerprint = 当前指纹 | `verification_missing` / `verification_stale` |
| 3 | 审查（review.required=true 时） | verdict = ship，且 review.fingerprint = 当前指纹 | `review_missing` / `review_rejected`（fix-first/rethink）/ `review_stale` |
| 4 | 视觉证据 | visual_evidence 每项文件字节 sha256 与记录一致 | `visual_stale` |

- **证据指纹（stale 检测的核心）**：验证/审查证据通过 `runtime.fingerprint.task_fingerprint`（基线修订 + 相关文件集归一化内容状态的 sha256）绑定到记录时的仓库状态；完成门用**同一入口**重算当前指纹，与记录值不一致 = 证据过期。任何后续编辑（包括"顺手小改"）都会使 verification_stale / review_stale 拦截完成——这是「任何修复使先前验证/审查失效」的自动强制。记录时机契约见 continuity 技能「证据指纹的记录时机」
- **无活动任务 / 任务不参与** → 静默放行，普通会话零干预
- `.glm-conductor/` 运行时目录豁免（编排器自身账本不算仓库改动）

### Layer B — 派发时注入（提示级）

PreToolUse 钩子在每次 Agent/Task 派发前向主会话注入 ownership 契约提醒（声明清单 + "越界将无法通过完成门"）。Layer B 提高合规但**不构成强制**——确定性完成门强制只在 Layer A。

### Bash 策略门控（决策级，beta1）

主会话每次 Bash 调用（PreToolUse 钩子按 `tool_name == "Bash"` 分流）经 `runtime/policy.py` 的表驱动规则分类，`permissionDecision` 返回运行时：

- **deny（活动任务期间恒拒）**：`rm` 带 r/f 标志、`git reset --hard`、`git clean -f`、force push（`--force` / `-f`；`--force-with-lease` 是可控变体，归 ask）
- **ask（仅活动任务 route.assurance = high 时升级）**：任何 `git push`、模式迁移（alembic / prisma / manage.py migrate / knex）、发布操作（npm / cargo publish、push --tags、gh release create）、权限变更（chmod / chown / icacls / attrib）
- **allow**：其余全部——静默放行，无任何输出
- 无活动任务时零干预（任何命令静默放行）

被拦报文形如 `GLM CONDUCTOR POLICY: deny (rm-destructive). command: <命令片段>`。恢复方法：

1. **ask** → 报文只是升级确认——向用户说明操作并获确认后继续（或改用非清单内命令）
2. **deny** → 改道：换用非破坏性等价命令（如 `git reset --soft`、`git clean -n` 预览、不带 -f 的变体）；确需破坏性操作时由用户在自己的终端执行
3. 已登记的保守误拒（beta1 取舍）：字符串中引用的破坏性文本（如 `echo 'rm -rf …'`）与 `git rm -r --cached`（仅动索引不删工作区文件）也会被拒——改道或由用户执行即可

## 哪些钩子执行

`hooks/hooks.json`（随插件分发，安装后自动启用）：

| 事件 | 脚本 | 超时 | 性质 |
| --- | --- | --- | --- |
| Stop | `hooks/stop_gate.py` | 5s | Layer A 四重检查（block 决策） |
| PreToolUse（Agent\|Task） | `hooks/pre_tool_use.py` | 3s | Layer B（advisory 注入） |
| PreToolUse（Bash） | `hooks/pre_tool_use.py` | 3s | Bash 策略门控（allow/ask/deny 决策） |

三者均为 `python3` 进程入口。

## 失败处理（fail-open）

强制层自身的故障**不阻断会话**（钩子起不来时 fail-closed 会卡死所有会话），但降级必须可见：

| 情形 | 行为 | 报文 |
| --- | --- | --- |
| 钩子进程崩溃 / import 失败 | 放行 + 报警 | stderr `ENFORCEMENT DEGRADED: stop_gate failed: ...` |
| git 不可用 / 非 git 仓库 | 跳过 ownership 校验 + 记 `gate_degraded`（reason=git_unavailable） | stderr `ENFORCEMENT DEGRADED: cannot list touched files ...` |
| 求值阶段结构性错误（rev-parse 失败 / 声明模式非法） | 跳过校验 + 记 `gate_degraded`（reason=evaluation_error） | stderr `ENFORCEMENT DEGRADED: cannot evaluate completion gate ...` |
| python3 不在 PATH | 钩子无法启动（ZCode 侧报 hook 运行失败） | 见环境自检；文档前置要求已声明 |

**模型侧义务**：观察到任何 ENFORCEMENT DEGRADED 时，必须如实向用户报告"强制层处于降级态"，不得默认强制仍在生效。

## Stop 循环安全（gate_exhausted）

运行时对 Stop block 续行内建上限（每 turn 最多 3 次），钩子侧同样有界：连续两次 block 后第三次不再拦截，放行并在 stderr 报 `ENFORCEMENT GATE EXHAUSTED`、journal 记 `gate_exhausted`。

**此时模型必须向用户报告 blocked 状态，不得声称任务完成**——放行是循环安全机制，不是完成许可。模型在两次 block 之间完成真实修复（journal 出现其他事件）会重置计数。

## 恢复方法

被 block 时按报文行动（报文自带恢复动作）：

1. **ownership 越界是有意的** → 主会话更新 `.glm-conductor/tasks/<task-id>/state.json` 的 `ownership.files` 纳入该路径，然后重新完成
2. **ownership 越界是误伤** → 回退 out-of-scope 改动后重新完成
3. **verification_missing** → 主会话亲自重跑 required 命令，`record_verification(st, cmd, task_fingerprint(repo, st))` 落账后重新完成
4. **verification_stale / review_stale** → 证据已过期（文件在验证/审查后被改过）：重跑验证 / 重新审查并记录**新**指纹；禁止回写旧指纹"续命"
5. **review_missing / review_rejected** → 派发审查者 / 按 fix-first·rethink 裁决修复后重新审查
6. **visual_stale** → 截图被替换过：重新采集视觉证据并重新视觉验收，`record_visual_evidence` 落账
7. **反复被拦且无法修复** → 向用户报告完整 block 报文，等待人工决策

## 边界（当前版本未强制的事项）

- **审查者只读**：由 agent 定义的只读工具白名单保证（确定性），非钩子强制；reviewer 的越界写入最终会被完成门捕获（纵深防御）
- **子会话内写操作**：ZCode 运行时子代理不触发钩子（Phase 0 实证），写前拦截不可实现——这正是完成门 + Layer B 设计的由来
- **指纹时延注记**：完成门每次 Stop 执行 1 次 git status + 每个需指纹比对的参与任务 ≤2 次短 git 子调用（各 3s 超时上限，钩子总预算 5s）；正常仓库毫秒级完成，git 病态缓慢时可能触发运行时超时（按降级处理）
