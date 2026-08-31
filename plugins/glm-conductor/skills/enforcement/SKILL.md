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

活动任务（state.json 存在且 status 非终态）在主会话 turn 结束时按固定顺序做四重检查，任一失败即 block（报文可行动：列出缺失项/过期证据与恢复动作）。**参与判定**：ownership 声明非空、verification.required 非空、review.required 为 true **或 route 推导要求审查（mode 为 audit/full 或 assurance=high，同钩子 `_route_requires_review` 推导）**、visual_evidence 非空——四者任一成立即受门跟踪（route 推导保证手写 state 漏写 review.required 也参与，无法绕过）；全部为空的任务不参与（普通会话零干预）。

| # | 检查 | 通过条件 | 失败形态（journal `check` 字段） |
| --- | --- | --- | --- |
| 1 | ownership（Layer A 原有） | git 改动文件 ⊆ 声明 ownership.files | `ownership`（报文列 out-of-scope 路径与两条出路） |
| 2 | 验证 | required 命令全部 completed，且 verification.fingerprint = 当前指纹 | `verification_missing` / `verification_stale` |
| 3 | 审查（review.required=true **或 route 推导要求审查——mode 为 audit/full 或 assurance=high**——时同样受查，手写 state 漏写标志无法绕过） | verdict = ship，且 review.fingerprint = 当前指纹 | `review_missing` / `review_rejected`（fix-first/rethink）/ `review_stale` |
| 4 | 视觉证据 | visual_evidence 每项文件字节 sha256 与记录一致 | `visual_stale` |
| 5 | 状态完整性（H3/P0-3） | state.json 可读 | `corrupt_state`（state.json 损坏 + journal 高保障证据 → fail-closed 拦截；见下方「损坏状态的处置与恢复指引」） |

- **证据指纹（stale 检测的核心）**：验证/审查证据通过 `runtime.fingerprint.task_fingerprint`（基线修订 + 相关文件集归一化内容状态的 sha256）绑定到记录时的仓库状态；完成门用**同一入口**重算当前指纹，与记录值不一致 = 证据过期。任何后续编辑（包括"顺手小改"）都会使 verification_stale / review_stale 拦截完成——这是「任何修复使先前验证/审查失效」的自动强制。记录时机契约见 continuity 技能「证据指纹的记录时机」
- **完成提交（生命周期封口）**：收尾时把 status 写为 `finalizing` 即请求完成（`state.transition_task_status`，公共状态 API 无法直达 completed）；四重检查全绿放行时，钩子对全部 `finalizing` 任务经 `state.commit_completion` 原子提交 `completed` 并记 `completed` 事件（via=completion_gate）——有违规照常 block（状态保持 finalizing，修复后重新 Stop）；连续 block 达上限（gate_exhausted）放行或任何降级路径都**不会**提交完成
- **无活动任务 / 任务不参与** → 静默放行，普通会话零干预
- `.glm-conductor/` 运行时目录豁免（编排器自身账本不算仓库改动）
- **per-task 仓库求值（RB-2）**：账本根不再同时充当 git 根——每个参与任务按 state 顶层可选 `repository.root` 解析其专属仓库根（无绑定 = legacy，回退账本根，单仓行为不变），ownership / 指纹 / 视觉检查全部在该根上求值；同一仓库根的 touched + base 单次 Stop 至多取一次（per-repo 快照缓存：每根 1 次 status + 1 次 rev-parse）；账本（state.json / events.jsonl / 完成门记账）恒在账本根。workspace 根无需是 Git 仓库——任务绑定嵌套仓即可受门强制；多嵌套仓歧义不猜：报出候选并要求显式绑定，绝不自动猜根
- **单元完成证据门与时序纪律（RB-1）**：Work Unit 以 `completed` 出 `finish_unit` 前，每个 required command 必须各有一条单元绑定（`unit` 逐字精确）、指纹新鲜、status=pass 的验证事件（all-match）——拒绝零副作用，git/指纹读取失败 fail-closed。时序恒为 `record_unit_verification` → `finish_unit` → git commit：**record 与 finish 之间任何 git 提交都会改变基线修订、使已记录证据失效（verification_stale）**，须重跑验证并重新记录

### 损坏状态的处置与恢复指引（H3/P0-3）

Stop 完成门用 `state.discover_tasks` 对任务目录四分类（active / terminal / corrupt / orphaned）——state.json 损坏不再被解释成「没有任务需要强制」，no task（静默零干预）与 unreadable task（结构化报警）从此可区分：

- **orphaned**（任务目录存在但无 state.json）→ 永不拦截：stderr 报警（ENFORCEMENT DEGRADED）+ 该目录 journal 记 `gate_degraded`（reason=orphaned_task）
- **corrupt**（state.json 存在但解析 / 任务标识归一失败或读取异常）→ 读该目录 journal 找高保障证据：
  - 有 `route_selected`（mode 为 audit / full 或 assurance=high）或 `status_changed`（to=finalizing）证据 → **fail-closed 拦截**：`corrupt_state` 并入违规清单，与其他违规同走统一 block / gate_exhausted 机器，报文含恢复指引
  - 无证据 → 降级放行：stderr 报警 + journal 记 `gate_degraded`（reason=corrupt_state）

被 `corrupt_state` 拦截时的恢复方法（报文内含同样指引）：

1. 从 `events.jsonl` 与 checkpoint 证据重建 `state.json`（仓库状态权威于运行时记录），重建后重新完成
2. 或经用户确认任务已废弃后，归档（改名 / 移除）该任务目录

### v2.1 强制面增量（M1-M3，新会话生效）

钩子清单从 2 条扩到 6 条（SessionStart / PreToolUse(Agent|Task) / PreToolUse(Bash) / PostToolUse(Agent|Task) / PostToolUseFailure(Agent|Task) / Stop）：

- **PreToolUse(Agent|Task)——dispatch permit 门（M2）**：存在活动任务时，实施者类型子智能体（`flash-implementer` / `visual-implementer`，含 `glm-conductor:` 前缀变体）的派发**必须携带有效 permit marker**（`GLM_CONDUCTOR_DISPATCH=<permit_id>`，来自 `task_manager.prepare_dispatch` 签发的持久 permit）——无 marker / 伪造 / 过期 / 已消费（重放）/ 任务不匹配一律 `permissionDecision: deny`（英文可行动报文：先 prepare_dispatch 再把 marker 放进 prompt）。只读类型（Explore / reviewer 等）不受此门，继续走 v2.0.1 的 ownership advisory 注入。background permit 且未显式 `run_in_background: true` 时，hook 经 `updatedInput` 全量改写强制后台。无活动任务时零干预（v2.0.1 行为不回退）
- **PostToolUse / PostToolUseFailure——runtime-observed 生命周期（M2）**：Agent 派发成功返回即由 hook 自动消费 permit（原子 rename，重放机械拒绝）并 journal `agent_launched`（tool_use_id / permit_id / agent_id / execution_mode 绑定）；派发失败自动作废 permit 并 journal `agent_dispatch_failed`。这两个事件是 runtime 亲眼观察到的生命周期事实，与手写 `implementation_started` **互不替代**——崩溃后对账以它们 + 仓库真相为准。注意：后台 Agent 的结果经异步通知到达（PostToolUse 只见 launch 确认），结果回收 = 模型转述 + 原生档案对账（`agent_runs`）两路
- **SessionStart——恢复注入（M3）**：新会话自动注入 `GLM CONDUCTOR RESUME CONTEXT`（未完成任务/中断单元/僵尸感知/建议步骤）；纯本地零 quota；无活动任务时完全安静
- **失败语义三层（宿主实测定型）**：hook 无法启动（脚本/解释器缺失）→ 宿主阻断所有匹配调用（非静默）；hook 运行中崩溃 → fail-open 放行 + stderr `ENFORCEMENT DEGRADED`（唯一静默 bypass 窗口——但该路径无法产生合法生命周期证据，完成门仍拒）；超时语义宿主未文档化。整体分层：**派发面 fail-open（降级可见）+ 完成面 fail-closed（Stop 门）**——绕过派发门的任务最终无法合法 completed
- **execution_policy（M1）**：state 可选顶层块承载自动化授权事实（并发预算上限 4 冻结、auto_resume 升档与 max_workers>2 须 `authorization.source == "user"`、保存时校验）；legacy 缺块按保守默认解释

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
| 任务仓库解析 / git 失败（RB-2 起**只降级该任务**，其余任务照常求值） | 跳过该任务校验 + 该任务 journal 记 `gate_degraded`（reason 见下方词汇表） | stderr `ENFORCEMENT DEGRADED: ...`（按 reason 分行） |
| 求值阶段结构性错误（指纹 rev-parse 失败 / 声明模式非法，同样按任务隔离） | 跳过该任务校验 + 记 `gate_degraded`（reason=evaluation_error） | stderr `ENFORCEMENT DEGRADED: cannot evaluate completion gate ...` |
| python3 不在 PATH | 钩子无法启动（ZCode 侧报 hook 运行失败） | 见环境自检；文档前置要求已声明 |

**降级 reason 词汇（RB-2 起 `gate_degraded` 事件的 `reason` 字段，按任务隔离、可精确断言）**：

- `repository_unavailable`——任务绑定的 `repository.root` 上 git 操作失败（非 git 目录 / git 故障）
- `git_unavailable`——legacy 任务（无绑定）且账本根本身是 git 根，但 git 瞬时故障（touched / rev-parse 不可得）
- `repository_root_missing`——legacy 任务、账本根非 git 根且一级子目录扫描无任何 `.git` 候选
- `repository_ambiguous`——legacy 任务、账本根非 git 根但存在嵌套仓候选（列出候选目录名；即使只有一个也不自动猜、不自动绑定——绑定必须由 state.json `repository.root` 显式声明）
- `evaluation_error`——求值期结构性错误（指纹 rev-parse 失败 / ownership 声明模式非法等）
- 另有发现完整性路径的 `orphaned_task` / `corrupt_state`（见上方「损坏状态的处置与恢复指引」）

被降级任务不参与本轮 `gate_passed` 记账，也不会被提交完成（指纹取不到 → 保持 `finalizing`）。

**模型侧义务**：观察到任何 ENFORCEMENT DEGRADED 时，必须如实向用户报告"强制层处于降级态"，不得默认强制仍在生效。

## Stop 循环安全（gate_exhausted）

运行时对 Stop block 续行内建上限（每 turn 最多 3 次），钩子侧同样有界：连续两次 block 后第三次不再拦截，放行并在 stderr 报 `ENFORCEMENT GATE EXHAUSTED`、journal 记 `gate_exhausted`。

**此时模型必须向用户报告 blocked 状态，不得声称任务完成**——放行是循环安全机制，不是完成许可。模型在两次 block 之间完成真实修复（journal 出现其他事件）会重置计数。

重申义务：gate_exhausted 放行时任务不进入 completed（完成提交仅在全绿门路径），向用户报告 blocked 的义务不因放行而免除。

## 恢复方法

被 block 时按报文行动（报文自带恢复动作）：

1. **ownership 越界是有意的** → 主会话更新 `.glm-conductor/tasks/<task-id>/state.json` 的 `ownership.files` 纳入该路径，然后重新完成
2. **ownership 越界是误伤** → 回退 out-of-scope 改动后重新完成
3. **verification_missing** → 主会话亲自重跑 required 命令，`record_verification(st, cmd, task_fingerprint(repo, st))` 落账后重新完成
4. **verification_stale / review_stale** → 证据已过期（文件在验证/审查后被改过）：重跑验证 / 重新审查并记录**新**指纹；禁止回写旧指纹"续命"
5. **review_missing / review_rejected** → 派发审查者 / 按 fix-first·rethink 裁决修复后重新审查
6. **visual_stale** → 截图被替换过：重新采集视觉证据并重新视觉验收，`record_visual_evidence` 落账
7. **corrupt_state** → state.json 损坏：按「损坏状态的处置与恢复指引」从 events.jsonl / checkpoint 证据重建 state.json，或经用户确认后归档任务目录
8. **反复被拦且无法修复** → 向用户报告完整 block 报文，等待人工决策

## 边界（当前版本未强制的事项）

- **审查者只读**：由 agent 定义的只读工具白名单保证（确定性），非钩子强制；reviewer 的越界写入最终会被完成门捕获（纵深防御）
- **子会话内写操作**：ZCode 运行时子代理不触发钩子（Phase 0 实证），写前拦截不可实现——这正是完成门 + Layer B 设计的由来
- **指纹时延注记**：完成门每次 Stop 对每个**不同仓库根**至多执行 1 次 git status + 1 次 rev-parse（per-repo 快照缓存，同根多任务不重复 git；各 3s 超时上限，钩子总预算 5s）；正常仓库毫秒级完成，git 病态缓慢时按降级处理（RB-2 起只降级对应任务）
