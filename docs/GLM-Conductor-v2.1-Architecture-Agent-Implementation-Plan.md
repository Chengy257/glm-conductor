# GLM Conductor v2.1 Architecture & Agent Implementation Plan

> **文档用途**：直接交付实施 Agent 执行  
> **目标版本**：`v2.1.0`  
> **基线版本**：`v2.0.1` stable  
> **基线分支**：`main`  
> **v2.0.1 基线事实**：已合入 `main` 并正式发布；RB-1 / RB-2 已完成；828 tests；validator 14/14  
> **文档性质**：架构冻结 + 实施计划 + 回归/验收标准  
> **核心定位**：将 GLM Conductor 从“具备 orchestration runtime 机制”升级为“围绕 ZCode native Agent 生命周期的本地控制平面（local control plane）”。
> **决策记录（※DR）**：2026-08-30 实施前缺口探查完成，D1（permit 范围）/ D4（verify 执行主体）/ D6（hook 失败降级）/ R3（permit 存储）/ M5 wake 形态 / CLI 入口 / 分批交付七项决策已由用户锁定；证据与理由见 `docs/GLM-Conductor-v2.1-实施前缺口探查与设计决策记录.md`。本计划已按结论修订，修订处标注 ※DR。
> **实施分支**：沿用 v2 既定策略——全部 v2.1 开发在 `v2-dev`，里程碑完成后合入 `main`。

---

# 0. Executive Summary

v2.0.1 已基本完成 **completion integrity**：

- task 只有经过 Completion Gate 才能 `completed`；
- Work Unit 只有存在 fresh unit-bound verification evidence 才能 `completed`；
- route/review invariant、corrupt fail-closed、Task Manager transaction、lease recovery、multi-repo repository binding 已落地。

但真实 dogfood 暴露出一个新的共同根因：

> **runtime 能力已经存在，但关键执行路径仍可依赖主会话的 advisory 文本；主会话会自然选择最简路径，从而绕过 dispatcher、恢复入口、自动 checkpoint、quota 准入或并行调度。**

两个实施期报告分别证明：

1. **Continuity gap**
   - quota 观测是时点性的；
   - 无 SessionStart 恢复入口；
   - resumable/checkpoint/wake 依赖模型记忆；
   - 中断后恢复语义存在，但没有自动触发面；
   - dispatch quota 仍依赖调用方手传。

2. **Subagent dispatch / parallel gap**
   - ZCode 原生完整持久化 subagent session；
   - 同一主会话内可续接旧 Agent；
   - 跨主会话后无法直接恢复原进程，但可读取旧 transcript 重建进度；
   - metadata `status=running` 可能成为僵尸状态，不能作为真相；
   - dispatcher / leases / bounded parallel 已存在，但真实任务完全绕过 `prepare_dispatch` / `commit_dispatch`；
   - 默认 `max_workers=1`，技能文本又偏向“串行永远合法”，导致并行机制长期不启用。

因此 v2.1 不再以“增加若干功能”为目标，而定义为：

> **Runtime Control Plane Closure**

即将以下能力统一纳入可执行、可验证、不可轻易绕过的控制平面：

```text
Execution Policy
    ↓
Dispatch Gate
    ↓
Background Agent Execution
    ↓
Agent Lifecycle Tracking
    ↓
Session Recovery / Reconcile
    ↓
Bounded Parallel Waves
    ↓
Quota-Aware Authorized Resume
    ↓
Verification / Review Provenance
    ↓
Completion Gate
```

---

# 1. v2.1 顶层设计原则

## 1.1 Prompt → Runtime Enforcement

所有关键约束优先落到：

```text
state
transaction API
hook
journal
invariant
```

而不是只写：

```text
SKILL / prompt / documentation
```

技能文本用于指导模型，但不得成为关键正确性的唯一保障。

## 1.2 Conservative Defaults, Explicit Escalation

自动化能力允许变强，但资源消耗与持续执行必须有明确授权边界。

默认：

```text
background subagent execution = enabled
parallel workers = 2
hard concurrency limit = 4
SessionStart recovery = enabled
quota auto-resume = disabled
cross-window persistent execution = never implicit
```

需要显式升级授权的行为：

```text
长期跨 quota window 自动执行
提高并发规模
未来任何 >4 worker 的扩展
```

## 1.3 Recoverable ≠ Automatically Consuming Quota

必须严格区分：

### Recovery Awareness

```text
SessionStart
→ 自动发现未完成任务
→ 注入 resume context
```

该能力默认开启，不代表自动继续调用模型，也不应额外制造长期额度消费。

### Automatic Resume Execution

```text
quota reset
→ scheduled wake
→ new session
→ continue implementation
```

该能力会继续消费额度，必须获得用户授权，必须有机械上限，不得默认开启。

## 1.4 Background ≠ Fire-and-Forget

v2.1 默认让子智能体在后台执行，以避免阻塞主会话。

但任何 background worker 都必须属于：

```text
task
work unit
dispatch wave
agent run
```

并最终进入：

```text
join
verification
finish_unit
review
completion
```

不存在“启动后不再跟踪”的合法路径。

## 1.5 Bounded Parallel, Not Maximum Parallel

v2.1 目标是安全利用 **2–4 worker bounded parallelism**，不是发现大量 independent units 就全部派出。

默认：

```text
max_workers = 2
```

允许标准范围：

```text
2–4
```

v2.1 hard limit：

```text
4
```

超过 4：

```text
out of scope / reject
```

## 1.6 ZCode Native First

优先使用 ZCode native Agent、foreground/background execution、SessionStart / PreToolUse / PostToolUse / PostToolUseFailure hooks、native subagent persistence / session context、native scheduled automation。

GLM Conductor 负责：

```text
policy
identity
mapping
decision
enforcement
reconcile
audit
```

而不是重新实现 subagent conversation store、agent process runtime 或 daemon scheduler。

---

# 2. 事实基础与 v2.0.1 已完成边界

## 2.1 v2.0.1 已完成

不得在 v2.1 回退：

- gate-only task completion；
- fresh Work Unit evidence gate；
- route/review invariant；
- corrupt task fail-closed；
- Task Manager prepare / commit / abort / finish transaction；
- lease TTL / generation / recovery；
- unit-bound verification；
- dependency readiness refresh；
- quota cache deepcopy；
- repository.root；
- per-task Stop Gate repository evaluation；
- Linux/Windows + Python 3.8/3.13 CI。

## 2.2 v2.1 已知输入问题

### Continuity

```text
no SessionStart recovery hook
quota caller-supplied
checkpoint is advisory
wake scheduling is advisory
```

### Agent Lifecycle

```text
implementation_started may be hand-written
no durable Work Unit ↔ native Agent invocation mapping
```

### Parallel

```text
DEFAULT_MAX_WORKERS = 1
dispatcher can be bypassed
real tasks observed strict serial dispatch
```

### Provenance

```text
verification event is structurally bound
but runtime does not necessarily execute the command itself
```

---

## 2.3 宿主硬约束（※DR，2026-08-30 探查实锤）

以下宿主事实改变部分设计前提，v2.1 全部设计必须在其边界内（证据清单见决策记录文档）：

### H1 — hooks 在子代理内不触发

实证（Phase 0 复测 8/8 + 代码级：子会话构造不传 hookRunner）。后果：PreToolUse Bash policy 只覆盖主会话调用；子代理内执行的验证命令对 PostToolUse 观察不可见 → §16 被动观察路线不完整（D4 锁定主动执行的依据之一）。

### H2 — 后台 Agent 的 PostToolUse 只见 launch 确认

Agent tool 调用在 launch 成功即“完成”，最终结果经异步通知送达，runtime 无法 hook 通知。后果：PostToolUse(Agent) 承担“绑定”（tool_use_id ↔ agentId ↔ permit/unit），不承担后台“结果回收”；结果回收两路——① 模型转述进 finish_unit / 结果记录 API；② reconcile 时读原生档案（`~/.zcode/cli/agents/<主会话id>/<agentId>/metadata.json` + output.txt，文件级，不依赖 SQLite）。前台 Agent 的 PostToolUse 可直接见最终报告。

### H3 — automation 槽位与可靠性

总量 20 槽上限（含暂停/完成/失败，删除才释放）；app 关闭期间触发=跳过不补；会话内创建的任务触发时回原会话（触发时不重放 SessionStart → wake prompt 必须自足）；本机实战存在 CronDelete/Update glitch（绝不重试）。wake 设计约束见 §14.4 修订。

### H4 — hook 失败三层语义

```text
hook 无法启动（脚本/解释器缺失）→ 阻断所有匹配调用（2026-08-29 卸载事故实测）
hook 运行但崩溃（非 0 退出 ≠2）  → fail-open 放行（官方文档）
超时                            → 未写明（残余探针）
```

设计含义：permit enforcement 唯一静默 bypass 窗口是“脚本运行中崩溃”→ permit 检查路径必须极简（只读 permit 文件 + 状态查询，无网络无 quota）并充分测试；DoD 措辞按 D6 校准（§23）。

另：钩子配置按会话启动快照解析、不热加载；插件缓存更新、子代理定义变更均需新会话生效 → dogfood 每轮迭代必须“更新插件缓存 → 新建会话”（§21 协议）。

---

# 3. 新顶层对象：Execution Policy

v2.1 第一优先级是建立“自动化授权事实源”。

建议 state 顶层新增：

```json
{
  "execution_policy": {
    "worker_execution": {
      "default_mode": "background"
    },
    "parallelism": {
      "mode": "standard",
      "default_workers": 2,
      "max_workers": 2,
      "hard_limit": 4
    },
    "continuity": {
      "mode": "resumable",
      "auto_resume": "manual",
      "max_quota_windows": 0
    },
    "authorization": {
      "source": "default",
      "confirmed_at": null,
      "scope": "task"
    }
  }
}
```

## 3.1 Worker execution mode

允许：

```text
background
foreground
```

默认：

```text
background
```

Background 默认适用于 implementation worker、independent research、documentation、long-running tests，以及结果不是立即阻塞下一动作的 reviewer。

Foreground 仅用于：

- 该 Agent 的结论决定下一步任务分解；
- 当前主会话无法进行其它有价值动作；
- 短时诊断 / Explore；
- 明确要求立即 join。

## 3.2 Parallel policy

建议：

```json
{
  "parallelism": {
    "mode": "standard",
    "default_workers": 2,
    "max_workers": 2,
    "hard_limit": 4
  }
}
```

允许：

```text
serial
standard
```

v2.1 暂不实现 `extended`。

语义：

```text
serial   → max_workers = 1
standard → max_workers ∈ [2, 4]
```

默认：

```text
default_workers = 2
max_workers = 2
hard_limit = 4
```

任务分析明确显示适合 3–4 worker 时，可以向用户推荐提高本任务并发预算；用户确认后写入 state。不得由 Agent 在任务中途无提示升级长期并发预算。

> 本计划冻结规则：默认 2；3–4 属于 task-level concurrency escalation，需要用户确认；>4 v2.1 直接拒绝。

## 3.3 Continuity / auto resume policy

建议：

```json
{
  "continuity": {
    "mode": "resumable",
    "auto_resume": "manual",
    "max_quota_windows": 0
  }
}
```

`auto_resume`：

```text
manual
notify
auto_once
until_done
```

### manual

默认。额度耗尽后进入 `waiting_quota`，不创建 scheduled continuation；未来 SessionStart 自动提示。

### notify

可创建提醒 / resume entry，但不自动执行 implementation。

### auto_once

必须用户明确授权，只允许跨 1 个 quota reset 自动继续一次：

```text
max_quota_windows = 1
```

### until_done

必须用户明确授权，并要求：

```text
max_quota_windows >= 1
```

不得存在真正无限的 `unlimited`。

## 3.4 Authorization metadata

推荐：

```json
{
  "authorization": {
    "source": "default|user",
    "confirmed_at": "ISO8601|null",
    "scope": "task"
  }
}
```

目的：

- runtime 区分默认保守行为与用户明确授权；
- resume / parallel escalation 可机械检查；
- 审计时可回答为什么允许持续自动执行。

---

# 4. 任务启动时的 Execution Policy 协议

流程调整为：

```text
ROUTING PREFLIGHT
      ↓
SELECTIVE ROUTE
      ↓
EXECUTION POLICY
      ↓
DECOMPOSE
      ↓
DISPATCH
```

## 4.1 默认无需打扰用户

可直接采用：

```text
worker_execution = background
max_workers = 2
auto_resume = manual
SessionStart recovery = on
```

## 4.2 必须询问用户的 escalation

### Cross-window auto execution

当系统预判任务明显长程、当前 quota 较低、多阶段 implementation 或很可能跨 Coding Plan reset 时，在编排开始时询问一次：

```text
是否允许额度恢复后自动继续？
- 不自动续跑
- 自动恢复一次
- 在最多 N 个额度窗口内持续执行
```

只有明确授权才写 `auto_once / until_done`。

### Higher parallelism

默认 2。若计划认为长期使用 3–4 workers 明显有益，则可在任务开始时推荐提高 `max_workers`，由用户确认。

---

# 5. Milestone M1 — Execution Policy Runtime

## 5.1 目标

把所有自动化强度变成 state 中的运行时事实。

## 5.2 推荐文件

```text
runtime/state.py
runtime/execution_policy.py
skills/orchestration/SKILL.md
docs/architecture.md
```

## 5.3 推荐 API

```python
default_execution_policy()
validate_execution_policy(policy)
set_parallel_authorization(...)
set_resume_authorization(...)
effective_worker_budget(...)
```

## 5.4 Invariants

```text
hard_limit == 4
1 <= max_workers <= 4

auto_resume == manual
→ max_quota_windows == 0

auto_resume == auto_once
→ max_quota_windows == 1

auto_resume == until_done
→ max_quota_windows >= 1
→ authorization.source == user

max_workers > 2
→ authorization.source == user
```

※DR：新增 continuity 状态（waiting_quota / waiting_user）与 window 扣减写入 TASK_TRANSITIONS 时，不得破坏既有状态机不变量（实战锁定：finalizing 只能 completed/failed/cancelled；completed 仅 Stop 完成门可写；wu ready→verifying 须经 running）。


---

# 6. Milestone M2 — Agent Lifecycle Hooks & Default Background Dispatch

## 6.1 目标

禁止 active glm-conductor task 直接绕过 Task Manager 派发 implementation Agent。

## 6.2 hooks.json 增加

建议：

```text
SessionStart
PreToolUse Agent|Task
PostToolUse Agent|Task
PostToolUseFailure Agent|Task
Stop
PreToolUse Bash
```

现有 PreToolUse Agent 仅 ownership advisory，需要升级为：

```text
ownership injection
+
dispatch permit enforcement
+
background policy injection/validation
```

## 6.3 Dispatch Permit

建议新增 durable permit，例如：

```json
{
  "permit_id": "dp-...",
  "task_id": "...",
  "unit_id": "wu3",
  "wave_id": "wave-2",
  "mode": "background",
  "created_at": "...",
  "consumed": false
}
```

存储优先选择能与现有 state/journal 原子事务对齐的路径。

※DR（R3 锁定）：照抄 leases.json 模式——任务目录内独立 permit 文件（`.glm-conductor/tasks/<task-id>/`，tmp + os.replace 原子写，单写者前提同租约层）。防重放采用“每 permit 一文件、文件名 = permit_id、消费 = 原子 rename 为 consumed”：PreToolUse 校验只读不写，PostToolUse 成功记账时原子改名；两次重放派发时第二次因文件已改名而 deny，无需文件锁，规避 hook 进程与主会话双写者竞态。

## 6.4 Agent tool call 绑定

由于 Agent tool 输入未必有自定义 metadata 字段，允许采用机器可验证 marker：

```text
GLM_CONDUCTOR_DISPATCH=<permit_id>
```

Task Manager 生成真实 permit。

PreToolUse：

```text
Agent call
    ↓
active glm-conductor task?
    ↓ yes
subagent_type is implementation-executor?
    ↓ yes
permit marker present?
    ↓
permit exists?
    ↓
unit ready/prepared?
    ↓
wave/member valid?
    ↓
mode matches execution_policy?
    ↓
allow
```

没有 permit：

```text
deny
```

并给主会话 actionable feedback：

```text
Agent dispatch blocked:
no valid GLM Conductor dispatch permit.
Plan/prepare a dispatch wave first.
```

marker 只是 lookup key；模型手写不存在的 permit 必须 reject。

※DR（D1 锁定）：permit 义务按 `tool_input.subagent_type` 分级——插件自有实施者类型（flash-implementer / visual-implementer / glm-reviewer / visual-reviewer 及别名）必须 permit；只读类（Explore / advisor 类）免 permit。“把实施包装成只读类型”的绕路由 Layer A Stop Gate ownership 校验兜底（越界 diff 挡完成，v2.0.1 已强制）。判别字段必须是 `tool_input.subagent_type`——顶层 `agent_type` 在主会话载荷中为 undefined 省略（Phase 0 实测），不得依赖。

## 6.5 Default Background

所有 conductor-managed Agent dispatch 默认：

```text
background
```

如果 Agent tool schema 可由 PreToolUse `updatedInput` 修改 background 字段：

```text
hook 强制修改
```

如果宿主当前不暴露可稳定修改的 background 参数：

```text
skill 必须要求 background
hook 至少验证 tool_input
```

实施 Agent 必须先确认当前 ZCode Agent tool 的实际 schema，不得猜测字段名。

※DR：此项已证实——`updatedInput`（全量替换、按 schema 重校验）与 `permissionDecision` 双重成立（官方文档 + Phase 0 活体捕获）；后台字段名为 `run_in_background`（Agent tool schema 实锤）。残余探针仅一项：updatedInput 改写 run_in_background 后宿主是否实际按改写值执行（实施首周验证）。updatedInput 为全量替换——hook 改写时必须回写全部字段（description / prompt / subagent_type / run_in_background）。

## 6.6 Foreground override

只有 permit 明确写：

```text
mode = foreground
```

才允许同步派发。

foreground permit 需记录原因：

```text
synchronous_dependency
decision_blocking
short_diagnostic
```

---

# 7. Milestone M3 — Agent Run Ledger & SessionStart Recovery

## 7.1 目标

建立：

```text
Work Unit
↔ Dispatch Permit
↔ Tool Use
↔ Native Agent Run
↔ Child Session
```

的稳定映射。

## 7.2 不重复保存 transcript

禁止把完整 native Agent transcript 复制进 glm-conductor state。

推荐只保存 handle：

```json
{
  "unit_id": "wu4",
  "attempt": 1,
  "permit_id": "dp-...",
  "wave_id": "wave-2",
  "tool_use_id": "...",
  "agent_id": "...",
  "child_session_id": "...",
  "execution_mode": "background",
  "started_at": "...",
  "observed_status": "launched"
}
```

## 7.3 Agent metadata.status 不是 truth

真实 dogfood 已发现：

```text
主会话异常退出
→ agent metadata status 可能永久 running
```

因此 reconcile 证据优先级：

```text
Repository state
    ↓
Verification evidence
    ↓
Native Agent transcript
    ↓
Agent run metadata
    ↓
Resume manifest/checkpoint
```

## 7.4 PostToolUse

成功后记录：

```text
tool_use_id
structured tool_response
可获得的 agent_id / child_session_id
```

※DR（H2）：后台派发时 tool_response 为 launch 确认（含 agentId + 档案路径 `~/.zcode/cli/agents/<主会话id>/<agentId>/`，实测确认）——PostToolUse 由此完成“绑定”；最终结果不在 tool_response 中（经异步通知到达），结果回收按 §2.3-H2 两路处理。

如果 tool_response 不公开 agent id：

- 使用 tool_use_id 作为稳定 primary key；
- 可选 adapter 查 native agent metadata；
- 不允许把 SQLite 内部 schema 变成核心依赖。

## 7.5 PostToolUseFailure

记录：

```text
agent_dispatch_failed
tool_use_id
unit
wave
error category
```

并调用：

```text
abort_dispatch / recovery transition
```

失败后 permit 必须 consumed 或 invalidated，不能继续重复使用。

---

# 8. SessionStart Recovery

## 8.1 目标

新会话自动知道：

```text
存在什么未完成任务
哪些 work unit 被中断
哪些 agent run 需要 reconcile
下一步应该做什么
```

## 8.2 SessionStart 必须纯本地

Hook 执行：

```text
discover_tasks()
read state
read journal tail
recover lease metadata
build recovery summary
```

不得：

- 调模型；
- 调网络；
- 直接执行 implementation；
- 自动消耗 quota。

## 8.3 Injected Resume Context

示例：

```text
GLM CONDUCTOR RESUME CONTEXT

Task:
  v21-control-plane-...

Repository:
  ...

Status:
  executing

Completed:
  wu1, wu2

Interrupted:
  wu3
  previous agent run: attempt 1
  child session: ...

Waiting:
  wu4 depends on wu3

Quota:
  last known: UNKNOWN
  auto-resume authorization: manual

Recommended:
  1. reconcile interrupted agent run
  2. inspect repository residue
  3. retrieve prior transcript if needed
  4. continue with a new dispatch permit
```

---

# 9. Agent Reconcile 三分模型

建议新增：

```python
reconcile_agent_run(...)
```

必须是 pure / read-mostly decision API，返回：

```text
reuse_result
resume_with_progress
redispatch_clean
manual_ruling
```

※DR（拆两层）：四分 API 落地为两层协作——runtime 层（pure）基于机械证据（repo 状态 / journal / leases / 原生档案 metadata.json 的 status 与 token 形状）产出“分类建议 + 证据句柄”；模型层经 ReadSessionContext 读子会话 transcript 组装 resume_with_progress 所需进度包（transcript 内容级重建无法在 runtime 完成）。同会话内 reuse_result 增加轻量选项：SendMessage 续接已完成 agent 直接问询（实测可用）。僵尸判据不采信 metadata.status（§7.3），机械判据以 parent 会话存活性 + 档案 token 形状近似，内容级裁决归模型。

## 9.1 reuse_result

适用：

```text
Agent 实际已完成
但结果没回主会话
```

动作：

```text
读取 native transcript / result
不重派 implementation
继续 parent verification
```

## 9.2 resume_with_progress

适用：

```text
Agent 做了一部分
当前 repo 有一致 residue
没有完整 pass evidence
```

生成 `Previous Progress Package`：

```text
old transcript summary
owned diff
decisions already made
open questions
verification not yet run
```

然后新 Agent process 继续工作，而不是从零重做。

## 9.3 redispatch_clean

适用：

```text
没有有效执行内容
没有可信 residue
没有可用结果
```

全新派发。

## 9.4 manual_ruling

适用：

```text
transcript 与 repository 冲突
ownership crossed
unattributable edits
corrupt evidence
```

禁止自动猜测。

---

# 10. Resume Manifest（原 checkpoint 改造）

## 10.1 定位

不是第二份 task truth，而是：

> **可重建的恢复上下文压缩层**

## 10.2 内容

建议：

```json
{
  "task_id": "...",
  "last_event_seq": 123,
  "active_units": ["wu3"],
  "verification_due": ["..."],
  "agent_runs": ["..."],
  "next_ready_candidates": ["wu4"],
  "quota_snapshot": {
    "status": "PRESSURE",
    "observed_at": "..."
  },
  "resume_authorization": {
    "mode": "manual",
    "remaining_windows": 0
  }
}
```

## 10.3 自动写入点

推荐：

```text
commit_dispatch
finish_unit
quota transition
task waiting_quota
task finalizing
```

写 manifest 失败不得覆盖 state truth；可记录 journal warning，但不能让正常 transaction 半提交。

---

# 11. Milestone M4 — Dispatch Wave & Bounded Parallel Activation

## 11.1 当前问题

`prepare_dispatch(task_id, uid)` API 形状天然鼓励：

```text
一个单元
→ 派发
→ 等待
→ 下一单元
```

v2.1 新增 wave API。

## 11.2 推荐 API

```python
prepare_dispatch_wave(
    repo_root,
    task_id,
    *,
    quota=None,
    max_workers=None,
)
```

返回：

```json
{
  "wave_id": "wave-...",
  "units": ["wu1", "wu2"],
  "permits": ["dp-1", "dp-2"],
  "worker_budget": 2,
  "deferred": {
    "wu3": "ownership_conflict"
  }
}
```

## 11.3 Wave decision

统一经过：

```text
dependency
quota
ownership
lease
parallel budget
execution policy
```

不得由主会话手工挑“我觉得 wu1/wu2 能并行”。

## 11.4 Wave transaction

建议：

```text
plan candidates
    ↓
acquire all required leases
    ↓
create wave
    ↓
create permits
    ↓
journal dispatch_wave_prepared
```

如果任一核心操作失败：

```text
all-or-safe-degrade
```

不要出现：

```text
wave 记录 2 units
但只拿到 1 个 lease
```

## 11.5 Wave launch contract

如果：

```text
wave.units > 1
```

orchestration 明确要求：

```text
do not wait for first worker before launching remaining wave members
```

默认全部 background。

主会话继续做：

- prepare next independent wave；
- collect already-returned results；
- verification planning；
- non-conflicting analysis；
- documentation；
- quota observation。

---

# 12. Parallel Budget Policy

v2.1 推荐：

```text
AVAILABLE
→ allowed up to task max_workers

PRESSURE
→ reduce effective workers to 1

UNKNOWN
→ effective workers = 1

EXHAUSTED
→ 0
```

例如 task policy `max_workers = 4`：

```text
AVAILABLE → 4
PRESSURE  → 1
UNKNOWN   → 1
EXHAUSTED → 0
```

这比当前 `UNKNOWN → 整批停止` 更可用，同时仍然保守。

---

# 13. Milestone M5 — Quota Integration & User-Authorized Resume

## 13.1 `prepare_dispatch` 不再默认 AVAILABLE

v2.1 改为：

```python
quota_status=None
```

语义：

```text
None
→ runtime quota resolver
→ provider/cache
→ AVAILABLE/PRESSURE/EXHAUSTED/UNKNOWN
```

读取失败：

```text
UNKNOWN
```

绝不默认 AVAILABLE。

## 13.2 Quota observation

推荐层级：

```text
fresh cache
→ provider fetch
→ stale cache
→ UNKNOWN
```

保持 timeout、provider allowlist、no redirects、credential safety 和 fail-safe semantics。

---

# 14. Automatic Resume Authorization

## 14.1 waiting_quota transition

当：

```text
EXHAUSTED
```

Task Manager：

```text
stop new dispatch
→ task waiting_quota
→ write resume manifest
→ compute reset + grace
```

然后检查：

```text
execution_policy.continuity.auto_resume
```

## 14.2 manual

不创建自动执行任务，只记录：

```text
recommended_resume_at
```

未来 SessionStart 自动提示。

## 14.3 notify

允许创建 reminder / resume suggestion，但任务实际执行前仍需用户动作。

## 14.4 auto_once

必须：

```text
authorization.source == user
remaining_quota_windows >= 1
```

然后可创建一次 ZCode scheduled wake。

※DR（wake 形态锁定）：一律使用一次性 automation（`recurring=false, maxRuns=1`，自完成，绝不依赖 CronUpdate 改参数）；until_done 惰性逐窗创建（任一时刻最多 1 个活跃 wake，上一窗口触发后才创建下一个）；CronDelete/Update 在本机有已知 glitch——绝不重试，清理失败容忍（20 槽位耗尽 → 降级 SessionStart 恢复，不阻塞正确性）；window 扣减记账永远由 runtime 写 state，与 automation 存活状态解耦；wake prompt 必须自足（自带 task_id 与恢复指令——触发时不重放 SessionStart）。

成功创建后：

```text
remaining_quota_windows -= 1
```

## 14.5 until_done

同样必须：

```text
authorization.source == user
max_quota_windows > 0
```

每次自动恢复消耗一个 window budget。

耗尽：

```text
auto_resume_authorization_exhausted
→ waiting_user
```

不得继续创建新 wake。

---

# 15. Scheduled Wake 的定位

ZCode automation 是：

```text
best-effort bridge
```

不是 correctness primitive。

因此 automation missed、machine slept、app closed 不能导致任务不可恢复。

最低保证始终是：

```text
future SessionStart
→ discover task
→ resume context
```

---

# 16. Milestone M6 — Verification / Review Provenance

## 16.1 v2.0.1 已做到

```text
evidence exists
evidence unit-bound
evidence fingerprint fresh
```

## 16.2 v2.1 目标

进一步证明：

```text
evidence 是 runtime 实际观察到的
```

## 16.3 推荐 API

```python
verify_unit(...)
verify_task(...)
run_review(...)
```

返回 durable receipt：

```json
{
  "kind": "verification",
  "scope": "unit",
  "unit": "wu3",
  "command": "...",
  "exit_code": 0,
  "fingerprint": "...",
  "observed_at": "...",
  "runner": "glm-conductor-runtime"
}
```

## 16.4 Security

不要新增一个任意 subprocess runner 绕开当前 Bash policy。

verification command 必须先过：

```text
runtime.policy
```

必要时只允许 state 中已声明的 required verification command。

※DR（D4 锁定）：verify_unit / verify_task 采用 **runtime 受限主动执行**——只执行 state 中已声明的 required verification command、先过 runtime.policy、执行时同刻捕获 exit code + fingerprint（零 TOCTOU），receipt 由 runtime 亲测产生。理由：① H1——子代理内命令对 hook 观察不可见，被动观察路线不完整；② Bash tool_response 的 exit code 结构化字段未证实；③ 主动执行才满足“runtime 实际观察到”的 DoD。被动观察（PostToolUse Bash 匹配声明命令）降级为非关键增强，不进 M6 关键路径。Windows 实施注意：subprocess 输出解码显式 utf-8（本机 GBK 控制台实测会 UnicodeDecodeError）。

## 16.5 Review receipt

建议：

```json
{
  "kind": "review",
  "reviewer": "...",
  "route": "...",
  "verdict": "ship|changes_requested",
  "fingerprint": "...",
  "tool_use_id": "...",
  "observed_at": "..."
}
```

`review.required == true` 时，只有 fresh `ship` receipt 才能通过 Completion Gate。

---

# 17. Hook Architecture（目标态）

v2.1 完成后：

```text
SessionStart
    ↓
recovery context injection

UserPromptSubmit
    ↓
可选 execution policy reminder

PreToolUse Agent|Task
    ↓
dispatch permit
ownership
background/foreground policy

PostToolUse Agent|Task
    ↓
agent-run binding
result capture
（※DR/H2：后台派发此处仅绑定与 launch 确认；
结果回收走模型转述或档案对账两路，见 §2.3）

PostToolUseFailure Agent|Task
    ↓
abort/recovery state

PreToolUse Bash
    ↓
safety policy

Stop
    ↓
completion gate
```

---

# 18. 推荐模块结构

建议增加：

```text
runtime/
├── execution_policy.py
├── agent_run.py
├── dispatch_wave.py
├── resume_manifest.py
├── recovery.py
└── verification.py
```

不要为了模块化而过度拆分；如果某模块没有清晰独立职责，可并回 `task_manager.py` / `reconcile.py`。


---

# 19. 推荐实施 Work Packages

## WU-21-01 — Execution Policy Schema

**目标**

- state execution_policy；
- defaults；
- validation；
- authorization invariants。

**文件**

```text
runtime/state.py
runtime/execution_policy.py
tests/test_state.py
tests/test_execution_policy.py
```

---

## WU-21-02 — Dispatch Permit

**依赖**

```text
WU-21-01
```

**目标**

- prepare permit；
- consume / expire；
- unit/wave binding；
- replay protection。

**文件**

```text
runtime/task_manager.py
runtime/dispatch_wave.py
tests/test_task_manager.py
tests/test_dispatch_wave.py
```

---

## WU-21-03 — Agent Lifecycle Hooks

**依赖**

```text
WU-21-02
```

**目标**

- PreToolUse Agent deny bypass；
- default background behavior；
- PostToolUse / Failure；
- runtime-observed `implementation_started`。

**文件**

```text
hooks/hooks.json
hooks/pre_tool_use.py
hooks/post_tool_use.py
runtime/agent_run.py
tests/test_hooks*.py
tests/test_agent_run.py
```

---

## WU-21-04 — Agent Run Ledger

**依赖**

```text
WU-21-03
```

**目标**

- tool_use_id mapping；
- optional agentId / childSessionId mapping；
- no transcript duplication；
- zombie status semantics。

---

## WU-21-05 — SessionStart Recovery

**依赖**

```text
WU-21-04
```

**目标**

- discover active/interrupted task；
- local resume context；
- no network / no model；
- `additionalContext` injection。

---

## WU-21-06 — Agent Reconcile

**依赖**

```text
WU-21-04
```

**目标**

四态：

```text
reuse_result
resume_with_progress
redispatch_clean
manual_ruling
```

---

## WU-21-07 — Resume Manifest

**依赖**

```text
WU-21-05
```

**目标**

- automatic context checkpoint；
- no state duplication；
- transaction integration。

---

## WU-21-08 — Dispatch Wave

**依赖**

```text
WU-21-02
```

**目标**

- batch readiness；
- bounded lease；
- permits；
- wave journal。

---

## WU-21-09 — Parallel Activation

**依赖**

```text
WU-21-08
WU-21-03
```

**目标**

- default 2；
- policy hard limit 4；
- background wave launch contract；
- quota-adjusted worker budget。

---

## WU-21-10 — Runtime Quota Integration

**目标**

- caller-supplied optimistic default removed；
- `None → provider/cache`；
- `UNKNOWN` on failure；
- effective worker budget。

---

## WU-21-11 — Authorized Quota Resume

**依赖**

```text
WU-21-01
WU-21-07
WU-21-10
```

**目标**

- manual / notify / auto_once / until_done；
- window budget；
- scheduled wake bridge（※DR：一次性 maxRuns=1、惰性逐窗创建、绝不重试 CronDelete/Update、容忍清理失败、state 记账解耦、wake prompt 自足）；
- `waiting_user` exhaustion。

---

## WU-21-12 — Verification Provenance

**目标**

- `verify_unit`；
- `verify_task`；
- command policy；
- durable receipts。

---

## WU-21-13 — Review Provenance

**依赖**

```text
WU-21-12
```

**目标**

- review invocation receipt；
- reviewer identity；
- final fingerprint binding。

---

## WU-21-14 — Docs / Validator / Release

**目标**

- README / README.en；
- architecture；
- CHANGELOG；
- orchestration/enforcement/continuity skills；
- validator；
- release notes。

---

## WU-21-15 — Runtime CLI 入口（※DR 新增，横切）

**依赖**

```text
随各里程碑增量交付（每落一个新 runtime API 同步补 CLI 子命令）
```

**目标**

- 正式 CLI 入口（`python3 <plugin>/runtime/cli.py <api> <args>` 或等价 `python3 -m` 形态），替代技能层 `python3 -c "..."` 内联调用；
- 理由：内联 -c 的引号/换行陷阱在真实 dogfood 已多次炸（ms_new 教训：heredoc/内嵌 python 含 `\n` 或撇号即失败）；
- orchestration / continuity / enforcement 技能中的 runtime 调用示例全部迁移到 CLI 形态；
- 退出码与 stderr 约定稳定（供主会话机械判定成败）。

---

# 20. Critical Regression Tests

## 20.1 Execution Policy

```text
default_worker_mode_background
default_workers_two
hard_limit_four
workers_above_four_rejected
workers_above_two_require_user_authorization
auto_resume_default_manual
auto_once_requires_one_window
until_done_requires_positive_window_budget
```

## 20.2 Dispatch Bypass

```text
active_task_agent_call_without_permit_denied
fake_permit_denied
expired_permit_denied
permit_wrong_unit_denied
permit_wrong_task_denied
permit_replay_denied
valid_permit_allowed
```

## 20.3 Background

```text
implementation_permit_defaults_background
explicit_sync_dependency_allows_foreground
foreground_without_reason_rejected
background_run_remains_join_required
```

## 20.4 Agent Lifecycle

```text
post_tool_success_binds_tool_use
post_tool_failure_aborts_dispatch
implementation_started_runtime_observed
manual_implementation_started_not_sufficient
```

## 20.5 Recovery

```text
session_start_injects_active_task
session_start_injects_interrupted_unit
session_start_no_active_task_is_quiet
session_start_no_network
zombie_running_not_treated_as_live
```

## 20.6 Reconcile

```text
completed_agent_result_reused
partial_agent_progress_pack_generated
empty_agent_run_redispatched
repo_transcript_conflict_manual_ruling
```

## 20.7 Wave / Parallel

```text
wave_selects_independent_units
wave_respects_dependency
wave_respects_ownership
wave_respects_lease
wave_default_budget_two
available_policy_four_uses_four
pressure_reduces_to_one
unknown_reduces_to_one
exhausted_dispatches_zero
```

## 20.8 Quota Authorization

```text
manual_never_creates_scheduled_resume
notify_never_executes_implementation
auto_once_requires_user_authorization
auto_once_consumes_window
until_done_stops_at_window_budget
authorization_exhausted_waiting_user
```

## 20.9 Provenance

```text
verify_unit_observes_exit_zero
verify_unit_exit_nonzero_no_pass_receipt
verify_unit_fingerprint_bound
unapproved_command_rejected
review_receipt_required_for_high_assurance
```

---

# 21. Dogfood Scenarios

※DR 协议前提：钩子配置按会话启动快照解析、不热加载——每轮 dogfood 迭代必须“更新本机插件缓存（市场源重装）→ 新建会话”后才生效。

## Dogfood A — Long Task / Background Workers

准备 4 个 independent Work Units。

预期：

```text
wave-1 = 2 units
both background
main session continues
results arrive independently
join
verification
finish
```

必须证明：

```text
不存在先等 wu1 完成才启动 wu2
```

---

## Dogfood B — Crash During Agent Run

```text
wu2 background running
→ kill main session
→ restart ZCode
```

预期：

```text
SessionStart injects wu2 interrupted
metadata running alone 不视为存活
reconcile transcript + repo
→ progress reuse or progress package
```

---

## Dogfood C — Quota Exhaustion / Manual

```text
auto_resume=manual
→ quota exhausted
```

预期：

```text
waiting_quota
no automation
no future quota consumption
next manual session recovers context
```

---

## Dogfood D — Auto Once

用户明确授权：

```text
auto_once
```

预期：

```text
one scheduled resume
one quota window consumed
second exhaustion → waiting_user/manual
```

---

## Dogfood E — Parallel Authorization

默认：

```text
max_workers=2
```

任务发现 8 independent units。

预期：

```text
runtime 不自行扩大到 8
```

用户授权 4：

```text
wave size <= 4
```

hard limit 仍为 4。

---

# 22. 不得实施的错误方案

## 22.1 不得把 SQLite 变成正式 API

以下只可诊断：

```text
~/.zcode/cli/db/db.sqlite
```

不得让核心恢复依赖内部 schema。

## 22.2 不得新建 daemon

保持：

```text
no always-on glm-conductor process
```

## 22.3 不得默认无限跨窗口

禁止：

```text
auto_until_done with no max windows
```

## 22.4 不得默认大量并发

禁止：

```text
ready units count = concurrency count
```

## 22.5 不得只改 SKILL 声明“必须并行”

所有关键 parallel / dispatch 条件必须至少有：

```text
state + runtime API
```

关键 bypass 必须有：

```text
hook enforcement
```

## 22.6 不得削弱 v2.0.1 Gate

v2.1 所有 background / recovery / parallel 优化不得绕过：

```text
fresh verification
review
finalizing
Stop Gate
completed
```

## 22.7 不得把 wake 正确性建立在 automation 管理操作上（※DR）

CronUpdate / CronDelete 不得成为任何正确性路径的依赖（本机已知 glitch，绝不重试）；automation 永远只是 best-effort bridge（§15），正确性兜底恒为 SessionStart 恢复 + runtime state 记账。

---

# 23. Definition of Done

v2.1 只有全部满足以下条件才可 release。

## Control Plane

- [ ] active task 的 implementation Agent 无 permit 不能启动（※DR/D6 校准语义：enforcement 层健康时默认拒绝；hook 运行中崩溃为唯一静默窗口，fail-open 放行但该路径无法产生合法生命周期证据，完成门仍拒；hook 无法启动时宿主自身阻断全部匹配调用）；
- [ ] `implementation_started` 来自 runtime-observed lifecycle；
- [ ] Agent success/failure 可追踪到 Work Unit；
- [ ] direct manual dispatch 不再是合法执行路径。

## Background

- [ ] implementation Agent 默认 background；
- [ ] main session 不因单 worker 阻塞；
- [ ] background worker 必须 join；
- [ ] foreground 只有显式同步原因。

## Recovery

- [ ] SessionStart 自动注入未完成任务；
- [ ] crash 后无需用户提醒模型“还有任务”；
- [ ] zombie metadata.status 不作为 truth；
- [ ] 已完成 Agent 工作可以捞结果而不是重派；
- [ ] partial work 能生成 previous-progress package。

## Parallel

- [ ] default worker budget = 2；
- [ ] standard hard limit = 4；
- [ ] dispatcher/wave 真正在 dogfood 中被调用；
- [ ] independent units 实测并发运行；
- [ ] quota / ownership / lease / dependency 仍全部约束。

## Authorization

- [ ] auto resume 默认关闭；
- [ ] auto_once / until_done 必须 user authorization；
- [ ] 自动窗口数必须有限；
- [ ] 并发预算不能隐式升级；
- [ ] execution_policy 有 durable audit metadata。

## Quota

- [ ] dispatch 不再 optimistic default AVAILABLE；
- [ ] runtime 自动解析 quota；
- [ ] UNKNOWN → 保守单 worker；
- [ ] EXHAUSTED → zero new dispatch。

## Provenance

- [ ] verify_unit / verify_task 由 runtime 观察结果；
- [ ] review receipt 绑定最终 fingerprint；
- [ ] high assurance 不可由手写 evidence 绕过。

## Compatibility

- [ ] v2.0.1 state 可迁移；
- [ ] legacy tasks 不因缺 execution_policy 直接 corrupt；
- [ ] legacy 默认采用 conservative policy。

## Tests / CI

- [ ] validator 全绿；
- [ ] full unittest 全绿；
- [ ] Ubuntu Python 3.8 / 3.13；
- [ ] Windows Python 3.8 / 3.13；
- [ ] dogfood A–E 全通过。

---

# 24. 推荐实现顺序

必须按依赖执行：

```text
M1 Execution Policy
        ↓
M2 Dispatch Permit + Agent Hooks
        ↓
M3 Agent Run Ledger + SessionStart Recovery
        ↓
M4 Dispatch Wave + Parallel Activation
        ↓
M5 Runtime Quota + Authorized Resume
        ↓
M6 Verification / Review Provenance
        ↓
Docs + Dogfood + Release
```

※DR（分批交付锁定）：分两批实施与验收——第一批 M1→M2→M3（控制面闭合 + 恢复）+ 对应 dogfood；第二批 M4→M5→M6（并行 + 续跑 + 溯源）+ Docs/Release + dogfood A–E 全量。每批以新会话推进（成本锚点：开新会话续作省 30–50%）。WU-21-15 CLI 随批横切。

不要先：

```text
DEFAULT_MAX_WORKERS = 4
```

再补控制面。

也不要先：

```text
auto scheduled resume
```

再补用户授权。

---

# 25. Agent 实施报告格式

最终实施 Agent 必须返回：

```text
GLM CONDUCTOR V2.1 IMPLEMENTATION REPORT

Baseline:
Branch:
Final commit:

M1 Execution Policy:
- implementation
- tests
- authorization behavior

M2 Agent Lifecycle:
- permit enforcement
- background behavior
- hook coverage

M3 Recovery:
- SessionStart
- agent run ledger
- reconcile
- resume manifest

M4 Parallel:
- dispatch wave
- default workers
- hard limit
- dogfood concurrency evidence

M5 Quota:
- runtime quota resolver
- auto resume authorization
- quota window budget

M6 Provenance:
- verify_unit
- verify_task
- review receipt

Regression:
- validator
- unittest count
- Ubuntu 3.8
- Ubuntu 3.13
- Windows 3.8
- Windows 3.13

Dogfood:
- A
- B
- C
- D
- E

Known limitations:

Independent review:
- verdict
- findings

Release:
- merged to main
- tag
- GitHub Release
```

如果以下任一项未完成：

```text
dispatch bypass still possible
SessionStart missing
auto resume can run without authorization
worker limit can exceed policy
background workers are untracked
verification provenance incomplete for claimed scope
CI red
```

不得宣布 v2.1 released。

---

# 26. v2.1 Release Positioning

完成后，项目定位应从：

> Selective orchestration runtime with completion enforcement

升级为：

> **A local control plane for ZCode agent orchestration, enforcing dispatch, background execution, bounded parallelism, recovery, quota-aware authorized continuity, verification provenance, review, and completion around native ZCode agents.**

角色边界：

```text
ZCode
= execution substrate

GLM Conductor
= orchestration control plane
```

v2.1 最重要的架构收口：

> **不继续堆积 advisory orchestration 功能，而是让已有 runtime 能力成为真实 Agent 生命周期中的默认合法路径；同时确保更强自动化始终受用户授权与有界资源策略控制。**

---

# Appendix A — Source Basis

本计划整合以下项目内 dogfood 结论：

1. `GLM Conductor v2.0.1 连续性缺口分析与 v2.1 优化建议`
   - quota 时点观测；
   - SessionStart 缺失；
   - resumable/checkpoint/wake advisory；
   - Task Manager quota hand-passed；
   - no-daemon boundary。

2. `GLM Conductor v2.0.1 子智能体派发恢复与并行执行诊断报告`
   - native subagent persistence；
   - same-session continuation；
   - cross-session old process cannot be directly resumed；
   - transcript 可恢复工作级进度；
   - zombie running metadata；
   - dispatcher bypass；
   - default max_workers=1；
   - real serial execution；
   - agent mapping / reconcile / parallel activation 建议。

外部宿主能力已按当前 ZCode 官方文档核对：

- Hooks：`SessionStart`, `PreToolUse`, `PostToolUse`, `PostToolUseFailure`, `Stop`
- Subagents：foreground/background execution；background 不阻塞 main task；多个 foreground 一起 launch 可并行
- Automations：scheduled task 可作为 best-effort quota wake bridge

官方文档：

```text
https://zcode.z.ai/en/docs/hooks
https://zcode.z.ai/en/docs/subagents
https://zcode.z.ai/en/docs/automations
```

---

# Appendix B — Version Boundary

v2.1 暂不包括：

```text
>4 worker production concurrency
multi-orchestrator same task
cross-top-level-task same-repo diff attribution
custom always-on daemon
direct dependence on ZCode internal SQLite schema
unbounded auto-resume
subagent nesting
```

这些如有需要进入 v2.2+，必须重新做 dogfood 与授权模型评估。
