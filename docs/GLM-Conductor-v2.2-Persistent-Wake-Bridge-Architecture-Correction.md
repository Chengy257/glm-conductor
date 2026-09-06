# GLM-Conductor v2.2 架构修正：Persistent Wake Bridge 与 Scheduled-Owned Session 约束

> **文档类型**：v2.2 Architecture Correction / Agent Implementation Patch  
> **适用版本**：GLM-Conductor v2.2 开发中版本  
> **基线**：v2.1.0 stable + v2.2 Quota Continuity Control Loop 方案  
> **触发原因**：Phase 0 宿主实测发现 Scheduled Task 会话存在不可嵌套创建约束  
> **优先级**：P0 / 必须在 WU-22-05、WU-22-06 完成前吸收  
> **状态**：Implementation Ready

---

## 1. 新发现与结论

Phase 0 实验中，在已经由 Scheduled Task 唤醒的会话内创建新的 Scheduled Task 时，宿主明确拒绝：

```text
Cannot create a scheduled task inside a session that already belongs to a scheduled task
```

实测进一步表明：

- 当前会话一旦曾由 Scheduled Task 触发；
- 即使该 Scheduled Task 已 completed；
- 当前 session 仍被宿主永久视为 `scheduled-task-owned session`；
- 该 session 内不能再创建新的 Scheduled Task / cron。

该限制直接推翻原 v2.2 中隐含的链式模型：

```text
window N
  ↓
create one-shot wake N+1
  ↓
wake N+1 fires
  ↓
resume work
  ↓
create one-shot wake N+2
```

因此 v2.2 必须从 **Per-window Wake Chain** 修正为 **Persistent Wake Bridge**。

---

## 2. 新的核心架构原则

### 2.1 禁止链式创建下一窗口 wake

冻结不变量：

> A scheduled-task-owned session MUST NOT rely on creating another Scheduled Task for future quota continuity.

中文：

> **由 Scheduled Task 唤醒的会话，不得再依赖创建新的 Scheduled Task 来维持后续额度窗口连续性。**

### 2.2 Bridge 必须在 clean session 中建立

Scheduler 创建能力应视为 **session-local transient capability**：

```text
interactive clean session
    → Scheduled Task create allowed

scheduled-task-owned session
    → Scheduled Task create forbidden
```

因此：

> **跨窗口 bridge 必须在会话仍拥有 scheduler create capability 时预先建立。**

---

## 3. Persistent Wake Bridge

Persistent Wake Bridge 定义为：

> 在 clean interactive session 中提前创建、并可跨多个 quota window 被重复使用的 Scheduled Task bridge。

目标拓扑：

```text
clean session
    ↓
create ONE persistent bridge
    ↓
normal work
    ↓
quota exhaustion
    ↓
bridge fires
    ↓
resume / warm
    ↓
bridge remains available
    ↓
next quota boundary
    ↓
same bridge fires again
```

核心变化：

```text
one task + one automation identity
```

替代：

```text
one quota boundary + one new automation
```

---

## 4. `until_done` / `auto_once` 的创建时机提前

原 v2.2 设计：

```text
PRESSURE → pre-arm wake
```

对 `auto_resume = auto_once / until_done` 已不够稳健，因为 scheduler create capability 可能在进入 PRESSURE 前就因其他 Scheduled Task 触发而永久失去。

因此修改为：

```text
task active
↓
user authorizes auto_resume
↓
authorization.source = user
↓
MUST eager-arm Persistent Wake Bridge
↓
normal execution
```

即：

> **auto_once / until_done 在用户授权后应立即 eager-arm，而不是等到 PRESSURE。**

对于 `manual / notify`：

```text
PRESSURE → SHOULD arm
DRAINING → MUST attempt arm if create capability exists
```

若 session 已 scheduled-owned 且无法创建：

```text
continuity degraded
→ SessionStart fallback
```

不得无限阻塞。

---

## 5. 新 Scheduler Capability 模型

建议运行时新增：

```json
{
  "scheduler_context": {
    "origin": "interactive",
    "create": "allowed",
    "update": "unknown",
    "pause": "unknown",
    "delete": "unknown",
    "parent_automation_id": null
  }
}
```

### 5.1 origin

```text
interactive
scheduled_task
unknown
```

### 5.2 capability

```text
allowed
forbidden
unknown
```

分别用于：

```text
create
update
pause
delete
```

---

## 6. Phase 0 必须新增的宿主实验

继续 WU-22-05 / WU-22-06 前，必须完成：

| 编号 | 实验 | 决定内容 |
|---|---|---|
| P0-SCHED-01 | scheduled-owned session 能否 Update parent task | 是否可实现 Self-Retiming Bridge |
| P0-SCHED-02 | 能否 Pause parent task | 完成后能否自停 |
| P0-SCHED-03 | 能否 Delete parent task | ghost wake cleanup |
| P0-SCHED-04 | Recurring task 第二次触发是否仍回同 session | Persistent Bridge 基础语义 |
| P0-SCHED-05 | Recurring task 内能否修改自身 schedule | 是否可按真实 reset_at retarget |
| P0-SCHED-06 | 上一轮 run 未结束时下一 trigger 行为 | 防止并发恢复 |
| P0-SCHED-07 | 宿主关闭/休眠后 recurring task 行为 | 离线恢复能力 |
| P0-SCHED-08 | Chat 创建与 Automations Form 创建是否同样受限 | 是否存在 clean-controller 路径 |
| P0-SCHED-09 | payload 是否暴露 parent automation identity | 机械识别 scheduler context |

前三项优先级最高。

---

## 7. Persistent Bridge 的实现策略

### Strategy A：Self-Retiming Persistent Bridge

若 scheduled-owned session 可以 update 既有 task，则优先采用：

```text
clean session
↓
create automation A
wake_at = current_reset_at + grace
↓
A fires
↓
force refresh quota
↓
resume / warm
↓
read next reset_at
↓
UPDATE SAME automation A
↓
next_fire = next reset_at + grace
```

状态建议：

```json
{
  "wake_bridge": {
    "automation_id": "A",
    "mode": "self_retiming",
    "generation": 3,
    "current_boundary_id": "five_hour:2026-09-02T01:00:00Z",
    "next_wake_at": "2026-09-02T01:05:00Z"
  }
}
```

### Strategy B：Bounded Recurring Bridge

若 create forbidden、update 也 forbidden，但 recurring Scheduled Task 可持续存在：

```text
one recurring bridge
```

每次 trigger 都运行 Universal Wake：

```text
force quota refresh
↓
task completed?
  yes → no-op
  no  → inspect authorization
↓
manual/notify
  → warm-only

auto_once/until_done
  → resume if quota available
```

禁止把固定 5h 周期当作 provider truth；优先依据 provider 可验证的窗口语义，固定周期只能作为 fallback。

---

## 8. Universal Wake 修订

Universal Wake 保留，但必须新增硬红线：

```text
DO NOT CREATE A NEW SCHEDULED TASK FROM THIS WAKE SESSION.
Reuse or retarget the existing bridge only if host capability permits.
```

触发后固定流程：

```text
1. Force-refresh quota.
2. Load task state / manifest.
3. Inspect repository state.
4. Check whether goal is complete.
5. Complete → cleanup/no-op.
6. manual/notify → warm-only.
7. auto_once/until_done → reconcile + resume.
8. Never create another Scheduled Task from this scheduled-owned session.
```

---

## 9. Wake Bridge 记账语义重构

Persistent Bridge 后：

```text
automation count != quota window count
```

一个 automation 可能跨多个 quota windows。

因此必须拆分：

### 9.1 Bridge Lifecycle

```text
bridge_armed
bridge_retargeted
bridge_fired
bridge_paused
bridge_cancelled
bridge_degraded
```

### 9.2 Quota Window Consumption

`max_quota_windows / consumed_quota_windows` 必须按：

```text
task_id + boundary_id
```

消费，而不是按 `automation_id`。

建议：

```text
boundary_id = <window-kind>:<reset-at>
```

例如：

```text
five_hour:2026-09-02T01:00:00Z
```

同一 `task_id + boundary_id` 重放必须幂等。

### 9.3 auto_once 新语义

`auto_once` 不应等于“automation fire 一次”，而应表示：

> 只允许一次成功的跨 quota boundary 自动恢复。

如果 wake 触发时 quota 仍耗尽、weekly cap 仍阻塞、provider unavailable：

```text
DO NOT consume auto_once budget
```

只有：

```text
new executable boundary confirmed
AND authorized resume actually starts
```

才消费一个窗口预算。

---

## 10. Continuation Obligation 修正

原规则：

```text
DRAINING + known reset + no armed wake
→ Stop BLOCK
```

现在必须加入 scheduler capability：

```text
DRAINING
↓
bridge armed?
├─ yes → OK
└─ no
    ↓
create capability?
    ├─ allowed
    │   → BLOCK until bridge armed
    ├─ forbidden
    │   ↓
    │  existing bridge update capability?
    │   ├─ allowed → require retarget
    │   └─ unavailable → continuity degraded
    │                    do NOT infinite-block
    │                    SessionStart fallback
    └─ unknown
        → conservative handling
```

新增不变量：

> **Continuation enforcement MUST NOT require an action that the current scheduler context is mechanically unable to perform.**

也就是说：

```text
scheduled-owned
+
create forbidden
+
no editable bridge
```

不能无限 Stop block “please create wake”。

必须转：

```text
continuity.degraded
reason = scheduler_create_forbidden
```

---

## 11. State / Journal / Manifest 修正

### 11.1 State

建议：

```json
{
  "continuation": {
    "obligation": "armed",
    "wake_bridge": {
      "status": "armed",
      "mode": "self_retiming",
      "automation_id": "abc",
      "generation": 2,
      "current_boundary_id": "five_hour:...",
      "next_wake_at": "...",
      "scheduler_origin": "interactive"
    }
  }
}
```

`wake_bridge.status` 建议：

```text
none
requested
armed
fired
retarget_required
degraded
paused
cancelled
stale
failed
```

### 11.2 Journal

新增：

```text
scheduler_capability_observed
wake_bridge_armed
wake_bridge_retargeted
wake_bridge_fired
wake_bridge_pause_requested
wake_bridge_paused
wake_bridge_cancelled
wake_bridge_degraded
quota_boundary_consumed
scheduled_nested_create_rejected
```

### 11.3 Manifest

增加：

```json
{
  "quota_control": {
    "scheduler_origin": "scheduled_task",
    "scheduler_create_capability": "forbidden",
    "wake_bridge_mode": "self_retiming",
    "wake_bridge_status": "armed",
    "automation_id": "...",
    "generation": 3,
    "boundary_id": "...",
    "next_wake_at": "..."
  }
}
```

---

## 12. Work Unit 修订

### WU-22-05 — Persistent Wake Bridge

替代原 `Wake Planner`。

职责：

1. eager-arm；
2. persistent automation identity；
3. boundary generation；
4. self-retiming / recurring strategy；
5. Universal Wake；
6. bridge idempotence；
7. completion cleanup；
8. degraded handling。

禁止：

```text
one quota boundary = one automation
```

### WU-22-06 — Scheduler Capability & Bridge Lifecycle Adapter

替代原 `Scheduled Task Hook Closure`。

职责：

1. 识别 `interactive / scheduled_task / unknown`；
2. 记录 create/update/pause/delete capability；
3. Scheduled Task PreToolUse / PostToolUse / Failure；
4. parent automation binding；
5. automation identity extraction；
6. arm / retarget / fire / pause / cancel 生命周期；
7. nested create fail-fast；
8. impossible obligation → degraded。

### WU-22-07 — Continuation Stop Gate

必须消费：

```text
scheduler_context
+
wake_bridge.status
+
execution_quota_phase
```

不得只判断“是否 armed”。

### WU-22-08 — Resume Controller

每次 wake：

```text
force quota refresh
↓
record bridge fired
↓
load state/repo/manifest
↓
goal complete?
↓
manual/notify or auto resume
↓
consume boundary budget only on successful authorized resume
↓
if update supported:
    retarget SAME bridge
else:
    keep recurring bridge
```

严禁创建新的 Scheduled Task。

---

## 13. Completion Cleanup 修订

优先：

```text
pause existing bridge
```

或：

```text
delete existing bridge
```

取决于 Phase 0 capability。

若 pause/delete 均不可用：

```text
task completed
→ future bridge fires
→ Universal Wake detects completed
→ zero implementation
→ exit
```

允许 ghost wake 存在，但必须安全 no-op。

---

## 14. Host Offline / Sleep 风险

Persistent Wake Bridge 仍受宿主可用性限制。

因此对外不应称：

```text
guaranteed wake
```

建议：

```text
Persistent Quota Wake Bridge
```

或：

```text
Durable Wake Bridge under host availability
```

正确性仍由：

```text
SessionStart recovery
```

兜底。

---

## 15. 新核心不变量

### INV-22-PB-01
```text
scheduled-owned session
→ MUST NOT create new Scheduled Task
```

### INV-22-PB-02
```text
auto_once / until_done authorization
→ Persistent Bridge MUST be eager-armed while create capability exists
```

### INV-22-PB-03
```text
one task
→ prefer one persistent automation identity
```

### INV-22-PB-04
```text
automation fire count
!=
quota window consumption count
```

### INV-22-PB-05
```text
quota window budget
→ keyed by boundary_id
```

### INV-22-PB-06
```text
auto_once
→ consume only on successful executable-boundary resume
```

### INV-22-PB-07
```text
Stop Gate
→ MUST NOT demand mechanically impossible scheduler actions
```

### INV-22-PB-08
```text
bridge unavailable
→ continuity degraded
→ SessionStart fallback
```

### INV-22-PB-09
```text
wake session
→ reuse/retarget existing bridge
→ never create next bridge
```

---

## 16. 新测试矩阵

### PB-01 Nested Create Rejection
```text
scheduled-owned session
→ create new scheduled task
→ host rejection
→ runtime classify create=forbidden
```

### PB-02 Eager Arm
```text
interactive
auto_resume=until_done
authorization=user
→ bridge armed immediately
```

### PB-03 Persistent Identity
```text
multiple wake cycles
→ same automation_id
```

### PB-04 Boundary Accounting
```text
same automation
boundary A resume
boundary B resume
→ consumed windows = 2
```

### PB-05 Wake Without Executable Quota
```text
wake fires
quota still exhausted
→ no window consumption
→ no implementation
```

### PB-06 auto_once
```text
first wake still exhausted
→ budget remains

later wake quota available
→ resume
→ consume 1
→ future auto resume denied
```

### PB-07 Stop Deadlock Defense
```text
DRAINING
scheduled-owned
create forbidden
no editable bridge
→ continuity degraded
→ no infinite Stop loop
```

### PB-08 Self-Retiming
若 Update 支持：
```text
bridge fires
new reset_at
→ same automation retargeted
```

### PB-09 Recurring Fallback
若 Update 不支持：
```text
same recurring automation
→ multiple wake cycles
```

### PB-10 Completion Ghost Wake
```text
task completed
bridge cannot be deleted
future wake fires
→ no-op
```

---

## 17. Release Gate 新增

### RG-22-06 — Multi-Window Until-Done

必须真实验证至少两个 quota boundary：

```text
clean interactive session
↓
until_done authorization
↓
Persistent Bridge eager-arm
↓
window 1 exhaustion
↓
bridge wake
↓
scheduled-owned session
↓
resume
↓
NO nested create
↓
same bridge survives
↓
window 2 wake
↓
resume again
```

验收：

> 第二个窗口恢复不依赖 Scheduled Task create。

### RG-22-07 — Scheduled-Owned Capability Matrix

必须记录真实宿主：

```text
Create
Update
Pause
Delete
```

行为。

### RG-22-08 — Host Offline Recurring Recovery

真实验证：

```text
one recurring trigger skipped while host unavailable
↓
next recurring trigger
↓
bridge still works
```

---

## 18. Agent 实施总指令

```text
Apply this architecture correction before completing WU-22-05/WU-22-06.

The host has proven that a session triggered by a Scheduled Task cannot
create another Scheduled Task, even after the triggering task is complete.

Therefore:

1. Remove all assumptions of per-window chained wake creation.
2. Introduce Persistent Wake Bridge semantics.
3. Eager-arm the bridge for user-authorized auto_once/until_done tasks while
   the session still has create capability.
4. Treat scheduler create/update/pause/delete as session-local capabilities.
5. Run Phase 0 experiments for Update/Pause/Delete/self-retiming.
6. Prefer one persistent automation identity per tracked task.
7. If update is allowed, implement self-retiming.
8. Otherwise use a bounded recurring bridge.
9. Separate bridge lifecycle accounting from quota-window consumption.
10. Key quota-window budget consumption by boundary_id, not automation_id.
11. Never require an impossible create operation from a scheduled-owned session.
12. Preserve SessionStart as the correctness fallback.
13. Never create another Scheduled Task from Universal Wake.
14. Add a real multi-window until_done release gate.

Do not continue the old one-shot wake chain design.
```

---

## 19. 最终结论

本次 Phase 0 发现不是普通兼容问题，而是 v2.2 quota continuity 拓扑的宿主级约束。

旧模型：

```text
每个 quota window 建一张新 wake
```

不可持续。

新模型应冻结为：

> **在仍拥有 Scheduled Task create capability 的 clean session 中，提前建立一条 Persistent Wake Bridge；后续所有 quota window 都复用、retarget 或周期性触发同一条 bridge。**

对于 `until_done / auto_once`：

> **bridge 创建时机从 PRESSURE 提前为用户授权完成后的 eager-arm。**

对于 scheduled-owned session：

> **永远不能把“再创建下一张 wake”作为 correctness path。**

该修正应成为 v2.2 WU-22-05 / WU-22-06 / WU-22-07 / WU-22-08 的新设计基线。
