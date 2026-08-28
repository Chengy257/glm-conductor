---
name: continuity
description: GLM 长任务连续性编排。与路由正交的生命周期层：foreground / resumable / idle 三种模式；CONTINUITY CHECKPOINT 结构化检查点；恢复时先检查仓库真实状态（repository > checkpoint）；定时唤醒与闲时执行优先使用 ZCode 原生能力。适用于长时间编码任务、可能跨会话或跨额度窗口中断的工作、可无人值守的批量任务。
---

# GLM 连续性：长任务生命周期

## Purpose

一次任务的路由（solo / delegate / audit / full）由 orchestration 技能决定；continuity 只回答一个问题——任务如果很长，如何持续执行。两者组合而非替代：orchestration 决定"谁实施、如何验证、是否需要审查"，continuity 决定"未完成的工作如何恢复"。

continuity 是与路由正交的生命周期维度，不是第五种 route。

## Modes

| 模式 | 适用 | 行为 |
| --- | --- | --- |
| foreground（默认） | 普通任务、当前会话内可完成、需用户实时互动 | 正常执行，不创建任何 continuation |
| resumable | 长前台任务、可能跨额度窗口/因可用性中断、仍希望延续当前目标 | 里程碑后写 checkpoint + 安排同目标定时唤醒，唤醒后先检查再恢复 |
| idle | 非紧急长任务、可无人值守、验证可自动完成 | 优先交给 ZCode 原生闲时任务执行 |

- **foreground** 是默认值：不创建任何 continuation，任务随会话自然开始与结束
- **resumable** 仍在前台工作，但为中断做好准备：checkpoint 是恢复的依据，定时唤醒是恢复的触发器
- **idle** 把整段工作交给 ZCode 原生闲时任务，主会话不占用前台交互

## Selection

按以下判据选择模式：

- 预计当前会话内完成 → foreground
- 预计会中断但需要延续当前目标 → resumable
- 不紧急且无人值守可行 → idle

推荐优先级：普通任务用 foreground；长交互任务用 resumable（定位是安全的前台会话连续性，不是额度绕过）；无人值守可接受的长任务优先 idle。

continuity 字段随 SELECTIVE ROUTE 声明携带（见 orchestration 技能），可随任务进展变更；变更同样需要证据（例如观察到额度窗口耗尽），不得凭直觉切换。

## Checkpoint

resumable / idle 任务在实质性里程碑后（不是每次工具调用后）写入 CONTINUITY CHECKPOINT 到任务专属路径 `.glm-conductor/tasks/<task-id>/checkpoint.md`；TASK_ID 规则与完整模板见 references/long-horizon.md。

原则：

- checkpoint 是导航状态，不是仓库真相源——不复制完整 diff、不复制大量代码、不声称未验证内容
- repository 状态始终优先：checkpoint 与仓库冲突时以仓库为准
- 每个长任务一个机械唯一（语义前缀+随机后缀）的 TASK_ID 与专属目录；并行长任务互不覆盖、互不删除

## 任务状态与执行日志（state.json / events.jsonl）

active task（continuity 为 resumable / idle 的任务，或需要 Stop 完成门保护的 delegate / full 任务）在专属目录内维护两个机器可读文件，与 checkpoint.md 同目录：

```
.glm-conductor/tasks/<task-id>/
├── checkpoint.md     # 导航状态（模型可读的恢复叙述）
├── state.json        # 强制状态源（Stop 完成门按它校验完成条件）
├── events.jsonl      # 执行溯源（append-only，一行一事件）
└── visual-evidence/
```

**state.json**：任务的确定性运行时状态（goal、route 五字段、ownership.files、verification、review、status 等；schema 与状态词汇以插件 `runtime/state.py` 为准）。写入规则：

- 创建即受跟踪：state.json 存在且 status 非终态（completed / cancelled / failed）= active task，Stop 完成门将跟踪其完成条件
- foreground 普通短任务不创建 state.json——无状态文件时完成门零干预
- 原子写（先写临时文件再替换）；status 只能按任务生命周期推进，不回退
- repository 文件仍是代码状态真相源，state.json 只是运行时任务状态

**events.jsonl**：append-only 执行日志，只在实质性节点追加一行结构化事实，禁止重写或截断。事件时点：

| 事件 | 追加时机 |
| --- | --- |
| task_created | 创建 state.json 时 |
| route_selected / route_reassessment | SELECTIVE ROUTE 声明 / 路由重估后 |
| implementation_started | 实施者派发后 |
| verification / review | 主会话验证完成 / 审查者裁决后 |
| checkpoint_written | checkpoint 落盘后 |
| gate_passed / gate_blocked / gate_degraded / gate_exhausted | 完成门放行 / 拦截 / 降级跳过 / 达上限放行（由 Stop 钩子记录） |
| completed / cancelled / failed | 进入终态时 |

约束：不写入任何秘密值（密钥、Authorization 头）、不写入完整 prompt 或完整源码；它不是遥测。

### 证据指纹的记录时机（verification / review / visual-evidence）

验证与审查证据必须绑定到记录时的仓库状态：state.json 的 `verification.fingerprint` / `review.fingerprint` / `visual_evidence[].sha256` 会被 Stop 完成门与**同一入口**（`runtime.fingerprint.task_fingerprint`）算出的当前指纹比对，不一致 = 证据过期（stale），完成被拦后必须重走证据链。记录时机契约：

| 证据 | 记录时机 | 记录动作 |
| --- | --- | --- |
| verification.fingerprint | 主会话**亲自**重跑全部 verification.required 命令且通过后，立即记录 | `state.record_verification(st, cmd, fingerprint.task_fingerprint(repo, st))` 后 save_state |
| review.fingerprint | 审查者返回裁决后、且裁决对应的 diff 未再变动时 | `state.record_review(st, verdict, fingerprint.task_fingerprint(repo, st))` 后 save_state |
| visual_evidence | 视觉验收通过时，对每张截图按原始字节哈希记录 | `state.record_visual_evidence(st, path, sha256)` 后 save_state |

红线：

- 指纹必须经 `runtime.fingerprint.task_fingerprint` 计算（与完成门同一入口、同一范围规则：声明了 ownership 时取「当前改动 ∩ 声明范围」，未声明时取全部当前改动）；不得手算、不得另定范围——两侧口径不一致的指纹永远无法通过比对
- 记录指纹后不得再改动 owned 文件：任何后续编辑都使指纹过期——这是设计意图（任何修复使先前验证/审查失效）。确需改动 → 改完后重走「验证 →（审查）→ 记指纹」
- fix-first / rethink 修复后的重新审查是新裁决：verdict 与 fingerprint 一并重记
- 记录证据后执行 git commit 会改变基线修订 → 证据随之 stale（指纹公式含 base，属设计内保守行为；提交前重走「验证 →（审查）→ 记指纹」即可）
- stale 的恢复路径只有一条：重跑验证 / 重新审查并记录新指纹；禁止回写旧指纹"续命"

**写入方式**：主会话定位已安装插件的 runtime 模块（含 `runtime/state.py` 的插件根，通常在 `~/.zcode/cli/plugins/cache/` 下），用 Bash 调 python3：

```bash
python3 -c "
import sys; sys.path.insert(0, r'<插件根>')
from runtime import state, journal
st = state.new_task_state('<task-id>', '<goal>', <route 五字段 dict>,
    ownership_files=[...], verification_required=[...])
state.save_state('<用户仓库根>', st)
journal.append_event('<用户仓库根>', '<task-id>', {'event': 'task_created'})
"
```

验证 / 审查 / 视觉证据的指纹记录（时机契约见上节）同一接法：

```bash
python3 -c "
import sys; sys.path.insert(0, r'<插件根>')
from runtime import state, fingerprint
st = state.load_state('<用户仓库根>', '<task-id>')
fp = fingerprint.task_fingerprint('<用户仓库根>', st)
state.record_verification(st, '<命令>', fp)  # 或 record_review(st, verdict, fp)
state.save_state('<用户仓库根>', st)
"
```

runtime 模块不可得时，按 `runtime/state.py` 的 schema 手写 state.json（字段与枚举必须逐项一致），恢复优先用模块读取（自动归一 v1.x 遗留标识）。

## Runtime State 与 Git

`.glm-conductor/` 是本地运行时状态，不得侵入用户受版本控制的仓库内容：

1. 不为运行时状态自动修改 tracked `.gitignore`
2. 在 Git 仓库中优先将 `.glm-conductor/` 写入本地排除文件 `.git/info/exclude`
3. 无安全排除机制可用时（如非 Git 工作区），明确告知用户该目录的存在与位置
4. 运行时文件不得静默污染 `git diff`

## Resume Procedure

每次重新激活后必须依次执行八步：

检查目标 → 按 TASK_ID 读取 checkpoint → 检查仓库状态 → 检查当前 diff → 判断先前变更是否仍在 → 判断目标是否已完成 → 检查验证状态 → 从 NEXT ACTION 恢复

禁止盲目重播旧指令。若 checkpoint 与仓库不一致：repository > checkpoint——分析变化来源后更新认知，不得为恢复 checkpoint 而回滚仓库新改动。恢复执行前必须重新输出 SELECTIVE ROUTE 声明（沿用或基于新证据重估）。

详细步骤与禁令见 references/long-horizon.md。

## Scheduled Resume

resumable 模式的唤醒不用 sleep 直到固定时间，而用安全周期性再激活：

唤醒 → 检查目标是否已完成（完成即停止并清理）→ 检查执行是否可用（可用则恢复；不可用则不触碰仓库，等下次触发）

调度触发本身就是存活探针：唤醒成功启动即说明模型执行当前可用；唤醒失败或未启动则不会产生任何仓库改动，自然等待下次触发。额度感知调度（见「Quota-Aware Scheduling」节）在此之上提供精确的 reset 时间规划；凭证不可得或查询失败时自动回退到本周期性探针机制，不引入独立的常驻额度轮询器。

安排定时任务时使用 references/long-horizon.md 中的结构化 resume prompt（自包含，不依赖会话上下文，且必须携带 TASK_ID 与精确 checkpoint 路径）。若希望结果回到当前会话，续作必须从当前聊天内创建绑定本会话的定时任务。

## Idle Execution

idle 模式优先使用 ZCode 原生闲时任务（支持配置了自定义模型的子智能体，可直接让 flash-implementer / visual-implementer 在闲时执行段落工作）。

不自行实现 daemon、while-loop 监督者、OS cron 包装器或额度轮询服务。

## Goal Integration

continuity 不重新实现 Goal 模式。职责分工：

- **Goal 模式**回答"总体目标是否完成"
- **orchestration** 回答"谁实施、如何验证、是否需要审查"
- **continuity** 回答"未完成的工作如何恢复"

三者组合使用。

## Failure Handling

- **定时/闲时能力不可用**：不得声称已启用连续性；保留 checkpoint 并向用户报告"手动可恢复"及恢复方法
- **所需 executor（如 visual-implementer）缺失**：fail-closed，不自动换成其他执行者
- **唤醒后发现目标已完成**：立即停止 continuation 并清理（见下）

## Quota-Aware Scheduling（alpha3）

额度感知是 continuity 层的增强，不是路由轴——额度决定"何时能继续工作"，不决定"谁来做"。

**provider-api 模式（当前实现）**：经 `runtime/quota/` 用 Coding Plan API Key 查询已验证的监控端点（`api.z.ai` / `open.bigmodel.cn` 的 `/api/monitor/usage/quota/limit`），解析为标准化快照（5h 窗必选、周窗可选——lite 套餐无周窗；按语义字段 unit/number 判窗）。四态评估（AVAILABLE / PRESSURE / EXHAUSTED / UNKNOWN）与恢复规划（EXHAUSTED 时 max(reset)+grace 精确唤醒、reset 未知或查询失败 → 周期性回退、唤醒后强制刷新）由 `scheduler.py` 确定性给出。诊断命令：`/glm-conductor:quota`（文本 + `--json` 双输出）。

**凭证**：环境变量 `GLM_CONDUCTOR_QUOTA_API_KEY` 优先，已登录 ZCode 的 `~/.zcode/v2/config.json` provider 配置为文档化回退；凭证零落盘（不进 state.json / events.jsonl / checkpoint / 日志 / 任何输出）。凭证不可得 → unavailable 模式 → 周期性存活探针回退。

**不变边界**：原生插件级 quota API（getQuotaRemaining / getQuotaResetTime / onQuotaReset 等）仍不存在，不得虚构；不硬编码 5 小时重置；不把额度观察当作路由证据；不实现常驻轮询——查询只发生在任务开始 / 路由选定后 / 大段派发前 / 里程碑后 / 调度恢复前后等刷新点。额度观察除本节 provider-api 通道外仍可来自用户告知或 UI，只作为证据使用。

## Completion Cleanup

目标完成并验收后，只清理本任务的状态：

1. 删除本任务目录 `.glm-conductor/tasks/<task-id>/`（仅此目录，不得触碰其他任务的 checkpoint 或视觉证据）
2. 停止并移除与该 TASK_ID 关联的定时任务
3. 终止该任务的闲时任务排队

避免幽灵唤醒重复执行；并行任务下删除全局或他人状态是禁止操作。
