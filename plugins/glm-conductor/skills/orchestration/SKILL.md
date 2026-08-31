---
name: orchestration
description: GLM 双轴选择性路由编排。GLM-5.3 主会话任架构师，按 Delegability × Assurance 两个独立维度在首次任务委派前声明 SELECTIVE ROUTE（solo/delegate/audit/full）；executor 独立选择实施者（flash-implementer / visual-implementer），assurance:high 时按任务模态引入全新上下文的只读审查者；路由可基于新证据双向重估。适用于构建并验证功能、多步骤交付、前端/视觉任务、需要委派实施或独立代码审查的任务。
---

# GLM 编排：双轴选择性路由

## 1. 角色与前置

本技能要求主会话为 GLM-5.3，担任架构师。以下职责保留在主会话，不外派：

- 需求与歧义解决
- 架构与路由判断
- 任务分解
- 实施规格编写
- 完整 diff 检查与验证重跑
- 路由重估决策
- 最终验收

子智能体只能实施或审查，不得成为新的编排者。若当前主会话模型不是 GLM-5.3，告知用户切换后再继续。

## 2. 双轴路由判断

不要把所有状态压缩成一个风险等级。先独立回答两个问题，再查路由矩阵：

**轴 A — Delegability（可委派性）**：剩余的实施工作是否足够有界、规格足够完备，可以委派出去？

**轴 B — Assurance（保障等级）**：实施完成并通过主会话验证后，是否需要一次全新上下文的独立终审？

两轴独立判断，互不推导。判据全表见 references/role-contracts.md。

## 3. ROUTING PREFLIGHT（路由前置侦察）

PREFLIGHT 不是路由，是路由的证据准备：双轴判断前若关键事实未知，先用只读侦察补齐，不用弱证据强行提前定路由。

**触发条件**（以下任一未知即触发）：根因 / 实施边界 / ownership 集合 / 接口契约 / 验证方案 / 隐藏耦合风险。任务已经显式（上述全部已知）时跳过——不为侦察而侦察。

**执行方式**：优先用 ZCode 内置只读 Explore 能力，必要时主会话亲自读码；不新增自定义侦察代理。侦察只读，不改动仓库。

**产出**（侦察完成后、SELECTIVE ROUTE 声明前输出；模板逐字，条目全部来自实际观察——文件/符号/调用链证据，不得包含推测性事实；OPEN AMBIGUITY 留空表示无未决歧义）：

```
ROUTING PREFLIGHT REPORT

ROOT CAUSE:
...

RELEVANT FILES:
- ...

INTERFACES:
- ...

TEST ENTRYPOINTS:
- ...

HIDDEN COUPLING:
- ...

OPEN AMBIGUITY:
- ...
```

该报告直接喂给双轴判断（§2）与 TASK CONTEXT PACK（见 §9 规格与报告 / references/role-contracts.md）。

## 4. SELECTIVE ROUTE 声明

在任何 Agent（任务）工具调用之前必须输出一次（原样保留代码块）：

```
SELECTIVE ROUTE
mode: solo | delegate | audit | full
delegability: low | high
assurance: standard | high
executor: main | flash-implementer | visual-implementer
continuity: foreground | resumable | idle
reason: <简明的、基于证据的理由>
```

示例：

```
SELECTIVE ROUTE
mode: delegate
delegability: high
assurance: standard
executor: flash-implementer
continuity: foreground
reason: implementation is bounded by explicit interfaces, owned files, and deterministic verification
```

声明之前不得调用任何任务工具。

active task（continuity 为 resumable / idle，或需 Stop 完成门保护的 delegate / full 任务）在声明后把 route 五字段写入 `.glm-conductor/tasks/<task-id>/state.json`（字段见 continuity 技能「任务状态与执行日志」节），并向同目录 events.jsonl 追加 `route_selected` 事件；路由重估后更新 state 并追加 `route_reassessment`。foreground 普通短任务无需创建状态文件。

## 5. 路由矩阵

| Delegability | Assurance | Route | 实施 | 独立审查 |
| --- | --- | --- | --- | --- |
| low | standard | `solo` | GLM-5.3 主会话 | 否 |
| high | standard | `delegate` | 实施者子智能体 | 否 |
| low | high | `audit` | GLM-5.3 主会话 | 是 |
| high | high | `full` | 实施者子智能体 | 是 |

矩阵与 review/ownership/verification/executor 绑定自 v2.0.1 起由 runtime.state.validate_route_invariants 在保存时强制，Stop 完成门按 route 推导审查义务——漏写 review.required 无法绕过。

语义要点：

- **delegate 不是 solo 的"升级"**。solo = 旗舰实施，delegate = Flash 实施，两者只是实施者不同；delegate 表达的是"该实施已足够有界，可由执行模型完成"，并不天然更安全或更高级。
- **audit = 主会话实施 + 独立终审**，特别适合判断密集、敏感、架构重的实施。
- **full = 委派实施 + 独立终审**，适合大规模但有界的工作（明确规则的迁移、清晰规格的 UI 改版等）。

## 6. Executor 选择

route 决定"是否委派、是否审查"；executor 决定"谁来实施、需要什么能力"。两者分开：

- 标准编码任务（后端函数、测试、重构、CLI、配置、确定性转换）→ `flash-implementer`
- 视觉/交互任务（前端界面、布局、dashboard、表单交互、视觉回归）→ `visual-implementer`

视觉任务是 executor 维度的一种能力选择，**不是第五种 route**。判据与不适用清单见 role-contracts.md。

## 7. 审查者选择（assurance: high 时）

按任务模态选择全新上下文的只读审查者：

- 文本任务 → `glm-reviewer`（GLM-5.3，审代码 diff）
- 视觉任务 → `visual-reviewer`（GLM-5.3-Flash 多模态，主职独立视觉验收：亲自读截图对照 VISUAL ACCEPTANCE、检查用户可见回归；diff 仅作上下文）

GLM-5.3 主会话与 glm-reviewer 均为纯文本模型：**主会话在视觉链路中只能驱动采集（截图落盘），不得声称自己做了视觉判定**；视觉判定由 Flash 系角色完成。

## 8. 预检（fail-closed）

- 按声明的 executor 与审查者，核对所需子智能体是否在 Agent 工具的可用类型列表中
- solo（executor: main）无需实施者预检；assurance: standard 无需审查者预检
- 模型与思考档位已固定在子智能体定义中，调用时不得附加模型覆盖
- 视觉通道额外要求：确认截图证据可以落盘并由实施者读取；不可得即停止视觉通道
- 任何所需角色缺失、不可用或名称不符时：停止该通道，告知用户检查插件安装（Settings → Plugin Management），不得静默替换为其他子智能体类型

## 9. 规格与报告

- 委派必须使用五段式实施规格（OBJECTIVE / FILES AND OWNERSHIP / INTERFACES / CONSTRAINTS / VERIFICATION），返回后按 IMPLEMENTATION REPORT 接收；完整模板见 references/role-contracts.md（首次委派前必须阅读）。复杂或陌生代码域的委派在规格前附 TASK CONTEXT PACK（主会话压缩的有界上下文包，条目优先取自 ROUTING PREFLIGHT REPORT；模板与紧凑性规则见 role-contracts.md）
- 工作者的报告仅视为声明（implementation claim）：主会话必须亲自检查完整 diff、核对改动范围、重跑验证命令，才能形成验证证据（verification evidence）

active task 的状态同步义务：五段式规格中 FILES AND OWNERSHIP 声明的 owned 文件清单必须同步写入 state.json 的 ownership.files（完成门 Layer A 按"实际改动文件 ⊆ owned"校验，越界改动无法通过完成门；连续两次 block 后按运行时续行上限放行——gate_exhausted，stderr 报警 + journal 记账——此时任务不进入 completed（完成提交仅在全绿门路径），且模型有向用户报告 blocked 的强制义务）；派发实施者后追加 `implementation_started` 事件；主会话验证完成（含命令与结果）追加 `verification` 事件；审查裁决后追加 `review` 事件。验证与审查落账时必须同步记录证据指纹（`record_verification` / `record_review` + `fingerprint.task_fingerprint`，完成门按指纹比对拦截过期证据）——时机与红线见 continuity 技能「证据指纹的记录时机」节；任何修复使先前裁决失效（§11）由此自动强制。

## 10. 工作单元与任务图（多单元任务）

单个 delegate/full 任务需要多段有界实施时，把它分解为 Work Unit（`state.json` 的 `work_units[]`，schema 见 `runtime/work_unit.py`）：**每个单元必须仍然小到能接收一个完整的五段式规格**——需要再拆说明分解不足；单元必填 ownership 与 verification（无文件范围或无验证的单元不可派发）。

**分解**（§60-§63）：单元间依赖用 `depends_on` 表达（同一任务内引用、禁止环）；单元不是工作流 DSL——十个状态词、显式依赖、就绪推导，仅此而已。

**派发**（§64-§67）：主会话仍是唯一编排者（子智能体不得派发子智能体）。多单元派发的准入决策走 `runtime/dispatcher.py` 的 `plan_dispatch`（纯决策器，零 I/O，只产出决策）——就绪（依赖全部 completed）→ quota 四态闸（EXHAUSTED 转 waiting_quota、UNKNOWN/PRESSURE 保守抑制）→ ownership 不相交 → 租约闸（他人持有的租约挡派发，`runtime/lease.py`）→ max_workers 预算。**默认串行（max_workers=1）；有界并行（上限 4，experimental）已启用**：并行资格 = ownership 声明判定可并行 **且** 无外来活跃租约冲突（两道闸门均须通过，§66/§78；租约不豁免 ownership 闸），接口已固定、验证可分离、无顺序依赖；不确定即不并行（串行永远合法）。决策之后的派发生命周期不要手工拼接「plan→租约→状态转换→`dispatch.active` 记账→save→事件」——走 `runtime/task_manager.py` 的事务边界：`prepare_dispatch`（决策 + 获取租约（全有或全无，同 owner 幂等）+ `dispatch_prepared` 事件，不动单元状态）→ `commit_dispatch`（ready→running + 单元 id 记入 `dispatch.active` + 单次 save + `implementation_started` 事件）→ [Agent 实施] → `finish_unit`（终态转换 + 释放租约 + active 移除 + `unit_finished` 事件）；`abort_dispatch` 可回退未提交的准备（running 不可 abort）。崩溃窗口有确定性恢复路径：prepare 后中断可安全 commit 或 abort（自有租约不挡重派）；commit 后中断按 §69 由 `runtime/reconcile.py` 三分恢复。单元离开活跃写相（completed/failed/cancelled，或转 verifying 后由主会话裁决）即释放租约——`finish_unit` / `abort_dispatch` 已固化该时点。

**批量派发 wave（v2.1 M4）**：整批并行派出走 `task_manager.prepare_dispatch_wave`（CLI `wave-prepare <repo> <task> [quota_status] [max_workers]`）——一次调用完成「额度解析（缺省 None → resolver 四级层级，**绝不默认 AVAILABLE**）→ plan_dispatch 全量决策 → 逐单元租约（冲突单元剔除重试，落选面 `deferred`/`waiting_quota` 透传）→ wave 记录 + 全员 permit 签发 → 单条 `dispatch_wave_prepared` 事件」（事务性 all-or-safe-degrade：wave 记录落盘时其全部成员租约已在位），返回 `wave_id` / `units` / `permits`（marker 由 `dispatch_wave.marker_for` 构造）/ `worker_budget`（已按 quota 四态折算：AVAILABLE→策略值、PRESSURE/UNKNOWN→1、EXHAUSTED→0）。**wave launch contract（§11.5）**：`wave.units > 1` 时全部成员必须在**同一回合并发派出**（每个 prompt 携带各自 permit marker）——禁止「等第一个返回再派下一个」；并行写相期间主会话只做验证规划与结果收集，不插队实施。成员全部终态/verifying 时 `finish_unit` 自动关闭 wave（`wave_closed`）；wave 关闭/重组后旧 permit 被 PreToolUse 成员资格环 deny（报文含 re-prepare wave 指引——重备 wave + 新 marker 重派即自救）。

**单元验证**（§70）：worker 的 IMPLEMENTATION REPORT 仍是声明——主会话亲自检查单元 diff、亲自跑单元 verification、经 `task_manager.record_unit_verification` 记录**单元级**证据（事件绑定单元 id + 单元作用域指纹），单元才算 `completed`（RB-1 起 `finish_unit` 前置 all-match 证据门：每个 required command 各需一条单元绑定、指纹新鲜、status=pass 的事件，缺一即拒绝且零副作用）；既有**任务级** `state.record_verification` 口径保留给 join 后的任务级全局验证（§71-§72）与完成门证据——单元级与任务级两种写入口不可混用。**指纹口径（v2.1 实战教训）**：单元证据必须绑定**门计算的单元作用域指纹**（`reconcile.fresh_unit_verification(...)` 返回的 `fingerprint` 字段）——工作树含其他单元或任务级改动（如 .gitignore）时，`fingerprint.task_fingerprint` 的任务作用域值与门不一致会导致 RB-1 拒绝。时序纪律：`record_unit_verification` → `finish_unit` → git commit——record 与 finish 之间任何 git 提交都会改变基线修订、使已记录证据失效，须重跑验证并重新记录。`attempt` 记录重试历史，新调用不抹除失败史（§73 有界重试）。**溯源推荐（v2.1 M6）**：「亲自跑 + 记录」优先经 CLI `verify-unit` 一次调用完成（runtime 受限主动执行：白名单闸——只执行 state 已声明的 required 命令 + policy 闸 + 零 TOCTOU 同刻指纹 + durable receipt，exit 0 自动落单元级验证证据）——runtime 亲测与 receipt 落盘把「跑过」从声明升级为机械事实；任务级全局验证（§71-§72）同构走 `verify-task`。

**Dispatch permit 与 runtime 调用入口（v2.1 M2/M3）**：`prepare_dispatch` 现在为该单元签发一张**持久 permit**（返回 dict 的 `"permit"` 键；`runtime/dispatch_wave.py`，mode 取 state `execution_policy.worker_execution.default_mode`，legacy 缺块按 background 兜底）。**实施者类型的 Agent 派发必须把 marker 放进 prompt**：`GLM_CONDUCTOR_DISPATCH=<permit_id>`（`dispatch_wave.marker_for(permit_id)`）——新会话中 PreToolUse(Agent|Task) 门会对实施者类型（flash/visual-implementer）无有效 permit 的派发直接 **deny**（只读类型不受影响仍走注入）；background permit 未显式 `run_in_background: true` 时由 hook 经 updatedInput 强制改写。PostToolUse 在 launch 成功后自动消费 permit 并记 `agent_launched`（runtime-observed 生命周期，与手写 `implementation_started` 互不替代）；派发失败自动作废 permit 并记 `agent_dispatch_failed`。permit 一次性（重放被机械拒绝）、TTL 1800s 兜底。**runtime 调用一律走 CLI**（`python3 plugins/glm-conductor/runtime/cli.py <子命令>`；退出码 0=成功 / 2=用法错 / 1=运行期拒绝）——禁止 `python3 -c` 内联（引号/换行陷阱实战已炸）。v2.1 子命令全集：M4 `wave-prepare` / `wave-show`；M5 `quota-resolve` / `quota-exhausted` / `quota-resume` / `wake-record` / `wake-prompt`；M6 `verify-unit` / `verify-task` / `review-record`；M3 既有 `policy-show` / `policy-set-parallel` / `policy-set-resume` / `permits` / `permit-show` / `permit-consume` / `agent-runs [unit]` / `manifest-show`。`commit/abort/finish` 事务后自动刷新 resume manifest（`manifest.json`，派生压缩层非 truth；写失败仅记 journal 警告不阻断事务）。执行策略升级（并发预算 3-4、auto_resume 非 manual）须用户确认后经 `policy-set-*` 写入 state，不得即兴。

**额度纪律与授权续跑（v2.1 M5）**：wave 派发前可 `quota-resolve <repo> [--force-refresh]` 观察四态（resolver 四级层级：新鲜缓存 → provider → 陈旧缓存 → UNKNOWN，绝不默认 AVAILABLE、绝不重试网络）。EXHAUSTED 时调 `quota-exhausted <repo> <task>` 走授权续跑链：等待单元转 waiting_quota，按 `execution_policy.continuity.auto_resume` 四态（manual/notify/auto_once/until_done）裁决——返回 wake 指令时主会话照 `wake-prompt` 的自足文本创建一次性 automation（recurring=false、maxRuns=1），CronCreate 成功后 `wake-record` 记窗口扣减（与 automation 存活解耦）；窗口预算（consumed_quota_windows ≥ max_quota_windows）耗尽不得再建任何自动化唤醒——任务转 `waiting_user` 等用户重新授权。恢复会话首步恒为 `quota-resume <repo> <task>`（AVAILABLE/PRESSURE → 转回 executing 按账本就绪续作；EXHAUSTED/UNKNOWN → 零转态保守等待）。

**Join**（§71-§72）：全部单元 completed 后主会话执行显式 join——检查聚合 diff → 跑**任务级全局验证**（跨单元交互的集成/构建/lint——单元局部验证永不自动替代全局验证）→ 终指纹 → assurance 审查（若需要）→ Stop 完成门。并行 worker 不得集体声明父任务完成。

**中断恢复**（§68-§69，配合 continuity 技能）：恢复时**绝不盲目重放**——`completed` 单元不重跑；`running` 单元用 `runtime/reconcile.py` 按证据三分（ownership 内无残留改动 → 干净重派 ready；残留改动 + 绑定当前改动的新鲜验证证据 → completed；残留但无新鲜证据 → verifying，主会话必须亲自验证）；`verifying` 单元仅在存在新鲜验证证据时建议 completed，其余情形由主会话裁决（重跑验证或按失败处理），不做状态转换。仓库状态始终权威于运行时记录。

## 11. 评审与裁决（仅 assurance: high）

- 审查者保持只读，返回 `ship` / `fix-first` / `rethink` 三种裁决之一（文本任务用 GLM REVIEW 格式，视觉任务用 VISUAL REVIEW 格式，见 role-contracts.md）
- fix-first：audit 路由由主会话修正，full 路由由原实施者修正；修正后主会话重验，再换用全新审查者
- rethink：修订架构，不得报告完成
- 任何修复使先前裁决失效；审查者与实施者/主会话同模型家族，独立性来自全新上下文与只读隔离，不宣称跨模型独立
- **审查溯源（v2.1 M6）**：审查完成后主会话经 CLI `review-record <repo> <task> <reviewer> <verdict> <tool_use_id> ['<route>'] ['<note>']` 申报裁决——runtime 绑定终指纹（与完成门同一入口）落 durable review receipt（journal `review_receipt` 事件 + state.review 同步镜像）。Stop 完成门的审查检查**只认 fresh ship review receipt**——receipt 是审查裁决的唯一权威，state.review 手写字段不再作为通过依据

## 12. ROUTE REASSESSMENT

路由不是单向阶梯。路由变化必须来自**新观察到的证据**，可以双向重估：

```
ROUTE REASSESSMENT

delegability: <old> -> <new>
assurance: <old> -> <new>
mode: <old> -> <new>

evidence:
<新观察到的证据>
```

示例 A（下调可委派性）：delegate 执行中暴露隐藏的跨模块状态耦合 →

```
delegability: high -> low
assurance: standard -> high
mode: delegate -> audit
evidence: implementation exposed hidden cross-module state coupling
```

主会话接管实施 → 验证 → 审查者终审。

示例 B（上调可委派性）：solo 调查后 root cause、架构、文件、验证均已确定，剩余工作完全机械 →

```
delegability: low -> high
mode: solo -> delegate
evidence: root cause, architecture, owned files and verification are now fully determined; remaining work is mechanical
```

前提：主会话尚未重复完成同一实施。

无新证据时不得变更路由；不得为了省事或直觉变更。

## 13. Continuity（独立维度）

`continuity` 是与 route 正交的生命周期维度（foreground / resumable / idle），不是第五种 route。长任务的检查点、恢复与闲时执行机制见 `skills/continuity`；本技能只在路由声明中携带 continuity 字段，不在此重复实现。
