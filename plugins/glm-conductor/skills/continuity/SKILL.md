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
| foreground（交互默认） | 普通任务、当前会话内可完成、需用户实时互动 | 正常执行，不创建任何 continuation |
| resumable | 长前台任务、可能跨额度窗口/因可用性中断、仍希望延续当前目标 | 里程碑后写 checkpoint + 安排同目标定时唤醒，唤醒后先检查再恢复 |
| idle | 非紧急长任务、可无人值守、验证可自动完成 | 优先交给 ZCode 原生闲时任务执行 |

- **foreground** 是交互默认值（普通未追踪短任务的产品默认；durable 任务 execution_policy 保守默认 resumable + manual + max_quota_windows=0，见 architecture §7 两层默认语义）：不创建任何 continuation，任务随会话自然开始与结束
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
- 原子写（先写临时文件再替换）；status 只能按任务生命周期推进，不回退（`runtime/state.py` 的 `TASK_TRANSITIONS` 转换表逐次校验）
- 生命周期封口：进入 `finalizing` 即请求完成（用 `state.transition_task_status(repo, task_id, "finalizing")`，自动记 `status_changed` 事件）；`completed` 仅由完成门在四重检查全部通过后原子提交（钩子记 `completed` 事件）——任何运行时写入路径都不能直接把任务置为 completed（`save_state` / `transition_task_status` 按转换表无条件拒绝）
- repository 文件仍是代码状态真相源，state.json 只是运行时任务状态

**events.jsonl**：append-only 执行日志，只在实质性节点追加一行结构化事实，禁止重写或截断。事件时点：

| 事件 | 追加时机 |
| --- | --- |
| task_created | 创建 state.json 时 |
| route_selected / route_reassessment | SELECTIVE ROUTE 声明 / 路由重估后 |
| implementation_started | 实施者派发后 |
| verification / review | 主会话验证完成 / 审查者裁决后 |
| checkpoint_written | checkpoint 落盘后 |
| status_changed | 任务状态迁移后（`transition_task_status` 自动记录） |
| gate_passed / gate_blocked / gate_degraded / gate_exhausted | 完成门放行 / 拦截 / 降级跳过 / 达上限放行（由 Stop 钩子记录） |
| dispatch_permit_created / dispatch_permit_invalidated | `prepare_dispatch` 签发 permit / `abort_dispatch` 作废（v2.1 M2） |
| agent_launched / agent_dispatch_failed / agent_launch_replay_skipped | PostToolUse 钩子观察到的派发生命周期（v2.1 M2——runtime-observed，与手写 implementation_started 互不替代） |
| manifest_write_failed | resume manifest 写入失败警告（v2.1 M3——派生物降级，不阻断事务） |
| dispatch_wave_prepared / wave_closed | `prepare_dispatch_wave` 批量准备 / 成员全部终态或 verifying 时 `finish_unit` 自动关 wave（v2.1 M4） |
| quota_resolved | prepare 链缺省额度解析（resolver 四级层级，v2.1 M5——绝不默认 AVAILABLE） |
| quota_waiting / quota_resumed / quota_wake_recorded / auto_resume_authorization_exhausted | 额度耗尽转态 / `quota-resume` 恢复 / wake automation 窗口扣减记账（**legacy/deprecated（v2.2）**：仅 v2.1 one-shot 兼容保留） / 授权预算耗尽转 waiting_user（v2.1 M5） |
| wake_bridge_requested / wake_bridge_armed / wake_bridge_fired / wake_bridge_cancelled / wake_bridge_stale / wake_bridge_retargeted / wake_bridge_paused / wake_bridge_degraded / wake_bridge_reconciled | Persistent Wake Bridge 生命周期（v2.2 M5/C1a：plan 裁决 → 主会话宿主 CronCreate → `arm` 记账；对账经 `wake-reconcile`，绝不制造 armed） |
| quota_boundary_consumed / quota_accounting_migrated | §15.1 resume commit point 消费记账（v2.2 C1b/C7：授权恢复实际开始才 +1，同 epoch 幂等；arm/fire/create 一律不消费）/ 存量窗口记账保守迁移一次性事件 |
| quota_consumption_pending / quota_accounting_migration_pending | RH-03 write-ahead pending marker（v2.2 release hardening：消费/迁移在 state 动写前冻结证据；重入先扫 committed 幂等、再按 pending 恢复闭合——先于授权三查，矛盾形态零写待人工裁决） |
| quota_subscription_registered | `register_quota_subscription` 幂等注册（v2.2 C5a——订阅事实记任务 journal；激活记账走控制面） |
| scheduler_capability_observed / scheduled_nested_create_rejected | Cron* 工具观察到的宿主调度能力证据 / 嵌套创建被拒记账（v2.2 C6，PostToolUse 钩子经 `observe_scheduler_context`——journal 唯一写点在该 API 内） |
| verification_receipt / review_receipt | runtime 亲测验证 / 审查裁决申报的 durable receipt 落盘（v2.1 M6——receipt 是完成门证据唯一权威） |
| completed / cancelled / failed | 进入终态时 |

**控制面 journal（v2.2 C4/C5a 起）**：`.glm-conductor/quota/events.jsonl` 承载跨任务控制面事件——`quota_epoch_advanced`（mark_activation_epoch，QC-07 每 epoch 恰一次）与 `window_primed`（Window Primer 落账，含失败尝试）。它与任务 `events.jsonl` 分层：订阅注册记任务 journal，激活记账记控制面 journal。

约束：不写入任何秘密值（密钥、Authorization 头）、不写入完整 prompt 或完整源码；它不是遥测。

### 自动恢复面（v2.1 M3：SessionStart 注入 + Resume Manifest）

连续性不再完全依赖模型纪律——两个 runtime 自动面已落地（新会话生效）：

- **SessionStart 恢复注入**：`hooks/session_start.py`（matcher `startup|clear|compact`）在每个新会话自动发现未完成任务并注入 `GLM CONDUCTOR RESUME CONTEXT`（任务/状态/已完成单元/被中断单元（含 possibly_zombie 标注——原生档案 status=running 不是真相）/等待单元/quota-resume 授权/建议步骤）。纯本地：无网络、无模型调用、零 quota 消耗；无活动任务时完全安静。崩溃后**不需要**用户提醒模型"还有任务"。
- **Resume Manifest**：`commit_dispatch` / `abort_dispatch` / `finish_unit` 事务后自动刷新 `tasks/<task-id>/manifest.json`（active units / verification due / agent runs / next ready candidates / quota snapshot / resume authorization）。它是**派生压缩层不是 truth**——读取用 CLI `manifest-show <repo> <task>`；写失败只记 journal 警告（manifest_write_failed），绝不阻断主事务。
- **崩溃后 reconcile**：对 running 单元先跑 `runtime/reconcile.py` 的 `reconcile_agent_run`（四分：reuse_result 捞结果不重派 / resume_with_progress 组进度包续作 / redispatch_clean 全新派发 / manual_ruling 人工裁决）——不再一律重派；进度包组装（旧 transcript 摘要）由主会话经 ReadSessionContext 完成（runtime 只给分类与证据句柄）。同会话内对已完成的旧 agent 可用 SendMessage 轻量续接问询。
- **runtime 调用入口**：一律 `python3 plugins/glm-conductor/runtime/cli.py <子命令>`（policy-show / policy-set-parallel / policy-set-resume / permits / permit-show / permit-consume / agent-runs [unit] / manifest-show；额度/连续性面另见「Quota-Aware Scheduling」节的 v2.2 命令清单；退出码 0/2/1，quota-resume 另有 3）——**禁止 `python3 -c` 内联**（引号/换行陷阱实战已多次炸）。

### 证据指纹的记录时机（verification / review / visual-evidence）

验证与审查证据必须绑定到记录时的仓库状态：state.json 的 `verification.fingerprint` / `review.fingerprint` / `visual_evidence[].sha256` 会被 Stop 完成门与**同一入口**（`runtime.fingerprint.task_fingerprint`）算出的当前指纹比对，不一致 = 证据过期（stale），完成被拦后必须重走证据链。记录时机契约：

| 证据 | 记录时机 | 记录动作 |
| --- | --- | --- |
| verification.fingerprint | 主会话**亲自**重跑全部 verification.required 命令且通过后，立即记录 | `state.record_verification(st, cmd, fingerprint.task_fingerprint(repo, st))` 后 save_state |
| 单元级验证证据（work unit） | 主会话**亲自**跑完该单元验证命令且通过后、`finish_unit` 收尾前 | `task_manager.record_unit_verification(repo, task_id, uid, cmd, fingerprint)` 追加 journal verification 事件——事件显式携带 `unit`（work unit id）字段，reconcile 恢复对账按 `unit` 逐字精确匹配；**不含 `unit` 字段的旧格式事件不再被采信**（保守按无证据处理，主会话重新验证；v2.0.1 H6 有意的破坏性变更） |
| review.fingerprint | 审查者返回裁决后、且裁决对应的 diff 未再变动时 | `state.record_review(st, verdict, fingerprint.task_fingerprint(repo, st))` 后 save_state |
| visual_evidence | 视觉验收通过时，对每张截图按原始字节哈希记录 | `state.record_visual_evidence(st, path, sha256)` 后 save_state |

口径正交（v2.0.1 H6）：单元级证据只写 journal（恢复对账用，reconcile 按 unit 匹配）；任务级完成门证据写 state.json（`state.record_verification`，Stop 完成门比对）——多单元任务两者都要记，互不替代。

红线：

- 指纹必须经 `runtime.fingerprint.task_fingerprint` 计算（与完成门同一入口、同一范围规则：声明了 ownership 时取「当前改动 ∩ 声明范围」，未声明时取全部当前改动）；不得手算、不得另定范围——两侧口径不一致的指纹永远无法通过比对
- **单元级指纹口径（v2.1 实战教训）**：`record_unit_verification` 的 fingerprint 必须取**单元作用域**——直接用 `reconcile.fresh_unit_verification(repo, task, unit, events=journal.read_events(repo, task))` 返回的 `fingerprint` 字段值记录；用任务作用域 `task_fingerprint(repo, st)` 在工作树还含其他单元/任务级改动（如 .gitignore、validator）时与 RB-1 门的单元口径不一致，finish 会被拒（missing 证据）且原因晦涩
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

验证 / 审查证据的指纹记录（时机契约见上节）一律走 CLI（同上禁 `python3 -c` 内联；`verify-task` / `review-record` 以与完成门同一入口的同刻指纹自动落 receipt 并同步证据流，替代手记指纹）：

```bash
python3 plugins/glm-conductor/runtime/cli.py verify-task '<用户仓库根>' '<task-id>' ['<单条命令>']
python3 plugins/glm-conductor/runtime/cli.py review-record '<用户仓库根>' '<task-id>' '<reviewer>' '<verdict>' '<tool_use_id>' ['<route>'] ['<note>']
```

视觉证据无 CLI 子命令：仍按上表时机以 `state.record_visual_evidence(st, path, sha256)` 后 save_state 记录。

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

resumable 模式的唤醒不用 sleep 直到固定时间，而用持久唤醒或安全周期性再激活（v2.2）：

唤醒 → 检查目标是否已完成（完成即停止并清理）→ 检查执行是否可用（可用则恢复；不可用则不触碰仓库，等下次触发）

调度触发本身就是存活探针：唤醒成功启动即说明模型执行当前可用；唤醒失败或未启动则不会产生任何仓库改动，自然等待下次触发。额度感知调度（见「Quota-Aware Scheduling」节）在此之上提供精确的 reset 时间规划；凭证不可得或查询失败时自动回退到本周期性探针机制。

v2.2 的两条标准载体（均不改变上面的检查-恢复语义）：

- **Persistent Wake Bridge（stable 主路径）**：`wake-plan` 裁决 → 主会话执行宿主 CronCreate 建 recurring automation → `wake-arm` 记账（含 next_run_at=wake_at 锚定，fail-open）→ 观测对账 `wake-reconcile`。一任务 ↔ 一常驻桥，跨窗口重复触发合法；arm/fire/create 一律不消费窗口预算；retarget 用 `retarget_wake_bridge` 记账而非另建 automation。完成或降级时由会话侧**单次尝试**清理（绝不重试）
- **额度 Watcher（可选常驻观察面）**：`quota-watcher start` 启动的本地进程，只做 poll-only 观察（零模型调用、绝不发激活）——它把额度观察从任务循环里解耦出来；v2.3 起仅为可选观察与诊断面，**≠ 额度时钟**（窗口连续滚动由 Global Quota Clock 承担，clock 不依赖 watcher）；是否启动由用户/主会话决定，不启动时调度触发探针与刷新点查询照常工作

安排定时任务时使用结构化 resume prompt（`wake-plan` / `wake-prompt` 产出自足文本；通用模板见 references/long-horizon.md，必须携带 TASK_ID 与精确 checkpoint 路径）。若希望结果回到当前会话，续作必须从当前聊天内创建绑定本会话的定时任务。

## Idle Execution

idle 模式优先使用 ZCode 原生闲时任务（支持配置了自定义模型的子智能体，可直接让 flash-implementer / visual-implementer 在闲时执行段落工作）。

不自行实现 daemon、while-loop 监督者、OS cron 包装器或私造额度轮询服务——v2.2 起如需常驻额度观察，用 runtime 提供的可选 `quota-watcher`（本地 poll-only 进程，`start|status|stop|once`），不要自写轮询脚本。

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

### v2.2 额度控制环（双层模型 + Quota Epoch + Watcher + Primer）

**窗口物化机制（2026-09-03 用户裁决，实测证据 `docs/history/v2.2/GLM-Conductor-v2.2-Phase0-Primer-Experiments.md`）**：reset_at 时刻窗口恢复 100%（周窗优先）；**下一个 reset_at 只在新窗口内发生模型调用时才物化——纯查询绝不推进它**；reset_at 锚定物化时刻 +5h00m01s；会话空闲会推迟窗口起点。编排含义：跨窗口等待的任务恢复时必须先触发一次真实模型调用，窗口才算真正换新——单靠轮询查询看到的旧 reset_at 不会自己滚动；v2.3 起生产物化路径 = Global Quota Clock 的 Scheduled Clock Tick，primer 仅为 manual/experimental fallback（见下）。

**双层额度模型**：provider 四态（AVAILABLE / PRESSURE / EXHAUSTED / UNKNOWN）与 execution phase 四态（NORMAL / PRESSURE / DRAINING / BLOCKED）是两个维度——最小编余 ≤20% 即 DRAINING（即使 provider 报 AVAILABLE）；DRAINING 禁开新实施波次只许收尾白名单动作（join/verify/review/checkpoint/wake）；`quota-phase <repo> <task>` 可查当前相与连续性义务，`quota-observe <repo> [task]` 出自适应观测（间隔 NORMAL 1800s → DRAINING/BLOCKED 300s，下限 60s）。

**Quota Epoch**：epoch 身份 = 窗口多重集 `(kind, reset_at)` 的确定性指纹（不含 status/百分比——provider 状态翻转不推进 epoch），`epoch_id = "glm:"+指纹前 16 位`；有意义的恢复触发是「新的可执行 epoch 出现」，不是「定时器触发」。probe boundary（观察收紧用）与 executable boundary（恢复资格用，取阻塞窗最晚 reset+grace）分离，绝不混用。

**额度 Watcher（可选常驻，默认不运行）**：`quota-watcher start|status|stop|once`——本地常驻 poll-only 进程（零模型调用、单实例所有权凭据是独立锁文件 `.glm-conductor/quota/watcher.lock`——O_EXCL 机械原子、锁含 pid+generation+身份指纹；观察状态 `.glm-conductor/quota/watcher.json` 为纯观察面，heartbeat 仅是锁仲裁的 advisory 证据、陈旧判定锚定其新鲜度，见 docs/architecture.md §7.5），Session 休眠时独立观察真实 provider 额度状态（v2.3 W4 职责降级：只负责 task execution quota observation（ACTIVE/PASSIVE 自适应观察）、diagnostics、optional active-work observation——不再负责 quota window continuity 维持、activation timing、window materialization，三者分别由 Global Quota Clock 与 wake bridge retime 承担；**Quota Watcher ≠ Quota Clock**，clock 的运行不依赖 watcher）；ACTIVE/PASSIVE 逐 tick 重判（有 waiting_quota 或自动续跑任务才 ACTIVE；manual/notify 恒 PASSIVE，可观察但绝不 prime、绝不发激活）。它是加速观察面，不是正确性前提——不运行时既有刷新点查询照常工作。

**Window Primer（默认结构性关闭）**：新窗口需一次最小模型调用才物化；primer 是 runtime 唯一 control-plane 模型调用面，三重授权闸 fail-closed（`primer_enabled` 默认 **false** + auto_resume ∈ {auto_once, until_done} + 授权来源必须是用户）；物化证据只有「两次强制刷新之间的窗口身份变化」——HTTP 200 与百分比下降都不是证据。v2.3 定位：default disabled 的 manual/experimental fallback——生产物化路径 = Scheduled Clock Tick（Global Quota Clock）；不接 watcher、不接 clock、不接 resume、不扩大授权，稳定运行一个版本后由后续版本决定是否删除。不要在授权闸之外自行触发「预调用」——那是一次真实消耗额度的模型调用。

**runtime 额度/连续性 CLI 清单（`python3 plugins/glm-conductor/runtime/cli.py <子命令>`）**：`quota-resolve`（[—force-refresh]）/ `quota-observe` / `quota-phase` / `quota-exhausted` / `quota-resume`（退出码 0/1/2/3——3 = durable-but-degraded，转态可能已落盘、幂等重跑安全）/ `wake-record`（legacy，deprecated）/ `wake-arm`（persistent arm：arm_transport 稳定通道记账 + next_run_at=wake_at retime 锚定，fail-open）/ `wake-retime`（fire 后重定时：可执行→停摆本桥，不可执行→下一边界或 5 分钟重试）/ `wake-prompt` / `wake-plan` / `wake-status` / `wake-reconcile` / `transport-status` / `quota-clock-plan` / `quota-clock-bind` / `quota-clock-tick` / `quota-clock-status`（v2.3 Global Quota Clock：规划 / 绑定 / tick / 状态；v2.3.1 plan/bind/status 增输出键——plan 附 `placement_guidance`，bind 附 `session_placement` + `cost_advisory`，status 附 `placement` + `host` + `needs_replacement` [+ `recovery_guidance`]）/ `quota-watcher start|status|stop|once` / `host-check`（v2.3.1：ZCode 宿主调度兼容性只读探针——表 / 必需列 / `next_run_at` 类型 / runs 表可达性 + 缺失项明细，`--db` 可覆盖发现路径；绝对零写，ZCode 升级后自检用）。宿主事实由会话侧供给（`wake-reconcile` 显式传入 CronList 观测结论）——runtime 自身零宿主 `Cron*` 调用；完成时桥接清理是会话侧单次尝试动作（绝不重试）。

### v2.3 Global Quota Clock 与职责边界（概念收敛，计划 §21）

v2.3 后额度/连续性域只表达一个模型——各组件各答一个问题，互不越界：

| 组件 | 回答的问题（语义锁定） |
| --- | --- |
| **Quota Clock**（`quota-clock-plan` / `quota-clock-bind` / `quota-clock-tick` / `quota-clock-status`） | 额度窗口什么时候持续滚动——account 级、极低成本常驻、无限期、无窗口预算；正常节拍 = 每窗口 reset_at+120s tick 后 retime，60 分钟 recurring 仅作 watchdog |
| **Task Policy** | 当前额度怎么花——35/20 阈值、worker budget、resume authorization（v2.2 语义零改动；窗口预算 N = continuity.max_quota_windows 属任务侧） |
| **Wake Bridge**（`wake-plan` / 宿主 CronCreate / `wake-arm` / `wake-retime`） | dormant task 什么时候重新获得 turn——精确 wake_at + native watchdog |
| **Watcher** | 可选观察与诊断（≠ Clock；clock 不依赖 watcher） |
| **Epoch** | 窗口身份（身份证，不是闹钟） |
| **Primer** | 非主路径 manual/experimental fallback（生产物化路径 = Scheduled Clock Tick） |

**会话放置与 SessionStart advisory（v2.3.1）**：clock automation 应放置在**专用低成本 Flash 交互会话**（勿绑定主编排 / 编码会话——周期 tick 会注入该会话）；插件不具备会话探测能力，`dedicated_session` 恒为 `not_mechanically_verifiable`（绝不伪造布尔，须人工确认）。`needs_replacement` 两极：True ⇔ `db_row_missing` / `db_inspect_failed`（此时附 `recovery_guidance`：在专用低成本 Flash 会话显式 `quota-clock-bind <new_automation_id>` replace，并声明「未执行自动会话迁移」）；`target_mismatch` / `tick_stale` / `runtime_path_missing` 仅 advisory 不触发。SessionStart 钩子另有**只读 advisory**：clock state 的 `last_tick_at` 陈旧（超 2×fallback_interval_minutes，state 文件启发式）时在恢复注入文本后附加同语义指引——插件绝不自动迁移 / rebind / 写入，权威诊断以 `quota-clock-status`（DB 级）为准。

### 授权续跑（v2.1 M5：authorized resume）

额度 EXHAUSTED 的处置不再依赖模型即兴——`quota-exhausted <repo> <task>`（CLI）走确定性转态链：执行态任务与单元转 waiting_quota，按 `execution_policy.continuity.auto_resume` 四态授权裁决：

| auto_resume | 授权要求 | EXHAUSTED 行为 |
| --- | --- | --- |
| manual（默认） | — | 不建自动化；降级 SessionStart 恢复注入提示用户 |
| notify | — | 只产出提醒文本（wake.required=False）——提醒允许、自动恢复不允许 |
| auto_once | `authorization.source == "user"`（保存时强制校验） | 一次性 wake：烧穿 1 个窗口预算后停 |
| until_done | `authorization.source == "user"` | 惰性逐窗续跑，直至完成或预算耗尽 |

机制要点：

- **窗口预算与消费点（v2.2 冻结）**：`continuity.consumed_quota_windows` / `max_quota_windows`——窗口扣减的唯一合法位置 = §15.1 resume commit point（`quota-resume` 内「转态 durable + mark 之后」的提交点，经 `record_quota_boundary_consumed` 记账、同 epoch 幂等；manual/notify 永不消费）。**arm/fire/create 一律不消费**（automation lifecycle ≠ quota epoch consumption）；**legacy/deprecated（v2.2 C1a/C1b）**：`wake-record` / `record_quota_wake` 为 v2.1 one-shot 兼容保留，persistent path 禁止调用。预算耗尽任务转 `waiting_user`（等待用户重新授权），此后**不得再创建任何自动化唤醒**
- **额度订阅与激活记账（v2.2 C5）**：任务订阅 quota 而非拥有额度时钟——state 的 `quota_subscription` 块记录注册 epoch（`quota-exhausted` 转态成功后 runtime best-effort 自动注册）；激活每 epoch 恰一次（QC-07，控制面 `quota_epoch_advanced` 事件落 `.glm-conductor/quota/events.jsonl`）；`quota-resume` 恢复门顺序冻结为 **evaluate → 转态 → mark**——同 epoch 重复唤醒零转态零重复激活，崩溃窗口宁可少记账不卡死恢复
- **Persistent Wake Bridge 与 Activation Transport（v2.2 M5/C6）**：`wake-plan` 裁决（纯计算，不创建 automation）→ 主会话执行宿主 CronCreate → `wake-arm` 记账（`arm_wake_bridge` 经 arm_transport 稳定通道的 CLI 入口，v2.3 W3；含 next_run_at=wake_at retime 锚定，fail-open）（runtime 绝不调用宿主 `Cron*`；宿主事实由会话侧供给，如 `wake-reconcile` 显式传入 CronList 观测结论——对账只能确认/降级，绝不制造 armed）。`recurring_bridge` 是唯一 stable 传输；`probe_then_hold` / `self_retiming` / `session_injector` 仅预留（arm 一律被 `TransportReservedError` 拒绝）。`transport-status` / `wake-status` 只读查询；完成时桥接清理是会话侧**单次尝试**动作（绝不重试）
- **Stop 门 continuity health（v2.2 C8）**：休眠交接域的任务（waiting_quota/waiting_user 或 PRESSURE/DRAINING + resumable + auto_resume ∈ {auto_once, until_done}）在完成门第 0 位接受三件套核查——handoff durable（checkpoint + resume manifest）、当期 epoch 已注册订阅、激活传输已 armed；`create=forbidden` 或 scheduled-origin 一律降级放行不无限拦截。**离开 Stop 前的义务**：域内任务先把三件套补齐（写 checkpoint、确认订阅注册、arm 桥）或显式降级连续性，不要反复硬闯完成门
- **wake prompt 自足**：唤醒 prompt 以用户 turn 注入同一会话（宿主实锚：wake=同会话续行、SessionStart 不重放），因此 task_id、账本/仓库根、恢复首步、额度检查口径、预算状态与红线（绝不重试 CronDelete/CronUpdate、发布动作征询用户、RB-1 指纹口径）全部内置。两种形态：persistent bridge 的触发 prompt 由 `wake-plan` 返回（`universal_wake_prompt`，recurring 桥按动态 next_run_at 精确触发、固定间隔仅 watchdog 兜底）；`wake-prompt` 是一次性唤醒 prompt（recurring=false、maxRuns=1，v2.1 兼容形态）
- **恢复首步（RB-21-01 恢复对账）**：额度唤醒触发或新会话恢复时第一步 `quota-resume <repo> <task>`——runtime 内部已**强制刷新**额度（`force_refresh=True`，wake 后不得信任休眠期缓存；wake prompt 内的 `quota-resolve` 指令同样带 `--force-refresh`），EXHAUSTED 时建议恢复时刻按 scheduler 统一口径（最晚 reset + 宽限，多窗不产生 premature wake）。AVAILABLE/PRESSURE → 订阅资格过 → 任务转回 executing、waiting_quota 单元按中断来源（`runtime.quota_interrupted_from`，`quota-exhausted` 转态时写入）分类落点：ready 来源直回 ready；running 来源先 `reconcile_agent_run` 四分对账——`redispatch_clean` 回 ready 全新派发、`reuse_result` 直达 verifying（先捞取 agent 成果，条目带 `action_required=recover_agent_result`）、`resume_with_progress` 回 ready 且**主会话必须先读子会话 transcript 组装 Previous Progress Package 随新规格续派**（编排纪律，runtime 不机械阻止）、`manual_ruling` 保持 waiting_quota 等人工裁决（此时任务不转 executing、resumed=false）；对账异常 fail-closed 按 manual_ruling 处理。分类与证据句柄经返回值 / journal `quota_resumed` 的 `unit_recovery` map 透传。实际转态后消费段按预闸三查执行（授权族 + source==user + 预算 >0），再按窗口记账。之后按账本就绪顺序继续派发（SELECTIVE ROUTE、permit 门与租约时序不得绕过）；EXHAUSTED/UNKNOWN → 零转态保守等待，不派发、不重建唤醒。CLI 退出码：0 成功 / 1 拒绝 / 2 参数 / **3 durable-but-degraded（转态可能已落盘，幂等重跑安全——先查任务 journal 再决定）**
- **SessionStart 兜底不变**：无论四态授权如何，automation 不可用时恢复语义始终回退 SessionStart 注入——automation 永远是加速器，不是正确性前提

## Completion Cleanup

目标完成并验收后，只清理本任务的状态：

1. 删除本任务目录 `.glm-conductor/tasks/<task-id>/`（仅此目录，不得触碰其他任务的 checkpoint 或视觉证据）
2. 停止并移除与该 TASK_ID 关联的定时任务
3. 终止该任务的闲时任务排队

避免幽灵唤醒重复执行；并行任务下删除全局或他人状态是禁止操作。
