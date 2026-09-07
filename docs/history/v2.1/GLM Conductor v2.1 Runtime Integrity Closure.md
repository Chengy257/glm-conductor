# GLM Conductor v2.1 Runtime Integrity Closure
## Agent Implementation & Release Hardening Plan

> **文档用途**：直接交付实施 Agent 执行  
> **项目**：`Chengy257/glm-conductor`  
> **当前基线**：`v2.1.0-alpha2`  
> **基线 commit**：`5c5aa73376df9c761e83efe884b2c0136c656a1d`  
> **目标版本**：`v2.1.0-alpha3 → v2.1.0-rc1 → v2.1.0`  
> **实施性质**：Runtime Integrity Closure / Release Hardening  
> **核心原则**：**不新增大功能，不重构已稳定模块；集中关闭恢复、事务、provenance 与 fail-closed 语义的最后缺口。**

---

# 0. Executive Summary

v2.1-alpha2 已完成主体架构：

```text
Execution Policy
→ Dispatch Permit
→ Agent Lifecycle
→ Session Recovery
→ Dispatch Wave
→ Bounded Parallelism
→ Runtime Quota
→ Authorized Resume
→ Verification / Review Provenance
→ Completion Gate
```

真实 dogfood 已验证：

- 双 worker wave 可实际并发；
- implementation Agent 无 permit 的控制路径已经存在；
- Agent launch 可由 runtime 自动记账；
- SessionStart recovery 已落地；
- Agent crash 后可进入 reconcile；
- quota 自动解析、一次性 wake 与跨窗口恢复已实际运行；
- verification receipt 已升级为 runtime 主动执行产生；
- Stop Completion Gate 继续保持 v2.0.1 的强约束。

因此本阶段**不再增加 orchestration feature**。

当前剩余问题集中在五类 runtime integrity gap：

1. quota wake 恢复链没有真正接入 `force refresh + agent reconcile`；
2. review receipt 尚不能机械证明 reviewer Agent 真实执行；
3. dispatch wave 在 permit 创建中途失败时缺少补偿事务；
4. malformed permit expiration 可能被错误视为有效；
5. `reconcile_agent_run()` 未完整继承 v2.0.1 multi-repository 双根语义。

本计划将上述问题定义为 **v2.1 stable release blockers**。

---

# 1. 顶层实施原则

## 1.1 本轮禁止功能扩张

禁止：

```text
新增 Agent 类型
新增 >4 worker 并发模式
新增 daemon
新增 SQLite 正式依赖
重写整个 Task Manager
新增新的 quota provider
新增新的 orchestration DSL
重新设计整个 state schema
```

允许：

```text
修复 runtime control-plane closure
补事务 rollback
补 provenance binding
补恢复语义
补 fail-closed validation
补 fault-injection tests
补 release metadata / docs
```

---

## 1.2 保持 v2.0.1 / v2.1 已有不变量

不得削弱：

```text
completed = Completion Gate only

Work Unit completed
= fresh unit-bound verification required

high assurance / audit / full
= fresh ship review required

ownership
lease
dependency
quota
parallel budget
dispatch permit
```

不得以“兼容旧行为”为理由绕过现有 gate。

---

## 1.3 Truth Source 优先级

继续坚持：

```text
runtime state
> runtime-observed event / receipt
> repository evidence
> native agent archive
> model narrative
```

任何 safety / completion / authorization 事实都不能只依赖模型文字声明。

---

# 2. Release Blockers

---

# RB-21-01 — Quota Resume Control-Plane Closure

**优先级：P0**

## 2.1 当前问题

当前已有：

```python
runtime.quota.resolver.resolve_quota_status(..., force_refresh=True)
```

scheduler 已明确：

```text
EXHAUSTED
→ max(blocking reset_at) + grace
→ force_refresh_before_resume = True
```

但 `task_manager.resume_from_quota()` 当前普通调用 resolver：

```python
resolve_quota_status(repo_root)
```

没有：

```python
force_refresh=True
```

因此 wake 后可能继续读取旧的 fresh cache。

同时：

```text
running
→ handle_quota_exhausted()
→ waiting_quota
→ resume_from_quota()
→ ready
```

会抹掉单元原来的执行语义。

已有的：

```python
reconcile.reconcile_agent_run()
```

没有进入 quota resume 主链。

结果：

> quota recovery 当前主要是“状态复位”，还不是完整的“恢复对账”。

---

## 2.2 必须实现的目标

quota wake 正式恢复链应变为：

```text
wake / manual resume
        ↓
force-refresh quota
        ↓
AVAILABLE / PRESSURE ?
        ↓ yes
identify previously interrupted units
        ↓
Agent Run reconcile
        ↓
reuse_result
resume_with_progress
redispatch_clean
manual_ruling
        ↓
refresh readiness
        ↓
normal permit/wave dispatch
```

禁止：

```text
所有 waiting_quota 一律直接 ready
```

---

## 2.3 修复 A：wake 强制 quota refresh

修改：

```text
plugins/glm-conductor/runtime/task_manager.py
```

目标：

当：

```python
resume_from_quota(..., status=None)
```

时调用：

```python
resolver.resolve_quota_status(
    repo_root,
    force_refresh=True,
)
```

显式：

```python
status="AVAILABLE"
```

等测试/人工注入路径仍保持：

```text
explicit status
→ zero network
→ zero resolver
```

---

## 2.4 修复 B：统一 resume planning 算法

当前 Task Manager `_recommended_resume_at()` 不得继续独立维护：

```text
min(EXHAUSTED reset) + grace
```

必须统一复用：

```python
runtime.quota.scheduler.plan_resume()
```

正式规则：

```text
resume_at =
max(all blocking EXHAUSTED reset_at)
+ grace
```

理由：

若同时存在：

```text
5h window EXHAUSTED
weekly window EXHAUSTED
```

早期窗口 reset 不意味着真正可恢复。

尤其：

```text
auto_once
```

只有一个自动窗口预算，不能浪费在理论上不可能成功的 premature wake。

### 要求

删除或降级 `_recommended_resume_at()` 的独立决策能力。

允许保留 wrapper，但内部必须调用：

```python
scheduler.plan_resume(...)
```

不得存在第二套 reset 规划算法。

---

## 2.5 修复 C：保存 quota interruption origin

必须能够区分：

```text
ready → waiting_quota
running → waiting_quota
verifying → waiting_quota
```

推荐最小实现：

在 Work Unit 增加可选 runtime metadata，例如：

```json
{
  "runtime": {
    "quota_interrupted_from": "running"
  }
}
```

或项目现有 schema 中选择等价、最小的 durable 记录方式。

要求：

- 仅 quota transition 时写；
- successful resume / terminal transition 后清理；
- legacy task 缺字段完全合法；
- validator 必须支持；
- 不建立第二份 Work Unit truth。

---

## 2.6 修复 D：恢复时接入 Agent reconcile

对于：

```text
quota_interrupted_from == running
```

不得直接：

```text
waiting_quota → ready
```

必须先调用：

```python
reconcile_agent_run(...)
```

根据 classification：

### reuse_result

```text
不得自动重新派 implementation
→ 转入 parent result recovery / verification
```

Runtime 不负责读取 transcript 内容。

允许返回：

```json
{
  "classification": "reuse_result",
  "action_required": "recover_agent_result"
}
```

供主 Agent 读取 native transcript/result。

---

### resume_with_progress

```text
不得 clean redispatch
```

返回：

```text
resume_with_progress
+ evidence handles
```

由主 Agent：

```text
读取 transcript
+ owned diff
→ Previous Progress Package
→ new worker continuation
```

---

### redispatch_clean

可以：

```text
waiting_quota → ready
```

重新进入：

```text
prepare_dispatch / prepare_dispatch_wave
```

---

### manual_ruling

禁止自动派发。

保持：

```text
waiting_user
```

或其它已有合法人工裁决状态。

必须向调用方返回具体 rationale。

---

## 2.7 推荐高层 API

本轮可新增一个薄 orchestration helper：

```python
resume_task_from_quota(...)
```

但不得重构全部 Task Manager。

推荐：

```python
resume_from_quota()
```

返回增加：

```json
{
  "resumed": true,
  "status": "AVAILABLE",
  "unit_recovery": {
    "wu-1": {
      "classification": "resume_with_progress"
    },
    "wu-2": {
      "classification": "redispatch_clean"
    }
  }
}
```

保留现有键，增量扩展，避免 breaking API。

---

# RB-21-02 — Review Provenance Must Bind to Real Reviewer Invocation

**优先级：P0**

## 3.1 当前问题

当前：

```python
provenance.run_review(
    reviewer=...,
    verdict=...,
    tool_use_id=...
)
```

会：

```text
绑定 final fingerprint
→ 创建 durable review receipt
→ Stop Gate 采信
```

但 runtime 当前不验证：

```text
tool_use_id 是否真实存在
是否对应 Agent invocation
是否是 reviewer
是否对应当前 task
是否已消费
```

因此理论上主会话可以提交：

```text
fake tool_use_id
+ reviewer="glm-reviewer"
+ verdict="ship"
```

并得到正式 receipt。

这与：

```text
review receipt = completion gate authority
```

的目标不完全一致。

---

## 3.2 正式目标

Review receipt 必须证明两个事实：

```text
1. reviewer invocation 真实发生
2. verdict 对应当前 final fingerprint
```

即：

```text
runtime-observed invocation
+
runtime-bound verdict
+
fresh repository fingerprint
```

---

## 3.3 推荐实现

新增 review invocation ledger event。

例如：

```json
{
  "event": "reviewer_invoked",
  "tool_use_id": "...",
  "reviewer": "glm-reviewer",
  "task_id": "...",
  "agent_id": "...",
  "execution_mode": "foreground"
}
```

来源必须是：

```text
PostToolUse Agent
```

或其它 host runtime 可真实观察到的 reviewer invocation。

不得由普通 CLI 手工创建此事件。

---

## 3.4 `run_review()` 新验证链

在创建 review receipt 前：

```text
tool_use_id exists
→ invocation event exists
→ reviewer identity matches
→ task matches
→ invocation not already consumed by another receipt
→ current fingerprint calculated
→ create receipt
```

不存在真实 invocation：

```text
ProvenanceError
zero receipt
zero review event
zero state mutation
```

---

## 3.5 Reviewer identity

reviewer 必须来自允许的 reviewer profile。

建议允许：

```text
glm-reviewer
visual-reviewer
```

实际名称以当前 agents 配置为准。

不要使用“任意非空字符串”。

---

## 3.6 Receipt replay protection

同一：

```text
tool_use_id
```

最多产生一次正式 review receipt。

重复调用：

```text
idempotent return
```

或：

```text
explicit duplicate error
```

二者任选其一，但不得生成多份相互矛盾的 receipt。

推荐：

```text
idempotent
```

---

## 3.7 Stop Gate 规则不降级

Completion Gate 继续只认：

```text
fresh
ship
runtime-valid review receipt
```

不得回退为：

```text
state.review.verdict
```

---

# RB-21-03 — Dispatch Wave Transaction Compensation

**优先级：P1**

## 4.1 当前问题

当前顺序：

```text
plan
→ acquire all leases
→ append wave to state
→ save_state
→ create permit 1
→ create permit 2
→ ...
→ journal dispatch_wave_prepared
```

如果：

```text
permit N 创建失败
```

可能残留：

```text
active wave
+ all leases
+ partial permits
+ no dispatch_wave_prepared event
```

与：

```text
all-or-safe-degrade
```

契约不一致。

---

## 4.2 正式事务不变量

成功时必须：

```text
wave
leases
permits
journal
```

全部一致。

失败时允许：

```text
无 wave
无本轮 leases
无 active permits
```

不得出现半完成正式 wave。

---

## 4.3 推荐实现：Compensating Transaction

不新增复杂两阶段状态机。

推荐：

```text
1 plan
2 acquire all leases
3 create permits into temporary prepared set
4 persist wave
5 journal prepared
```

或者维持现有顺序，但捕获 permit failure 后执行完整补偿。

优先推荐：

### 方案 A

```text
acquire leases
→ create all permits
→ save wave
→ journal
```

若 wave save 失败：

```text
invalidate all created permits
release all leases
```

---

## 4.4 失败补偿必须包括

```text
invalidate successfully-created permits
release all leases acquired by this wave
remove / rollback wave state if already persisted
journal optional transaction_aborted
```

若补偿过程本身部分失败：

```text
fail closed
+ explicit journal warning
+ recovery-visible state
```

不得静默吞掉。

---

## 4.5 必须增加 fault injection

测试必须人工注入：

```text
第 2 张 permit create 抛 OSError
```

验证：

```text
no active wave
no active permits
no leases
no dispatch_wave_prepared
```

再增加：

```text
state.save_state failure after permit creation
```

验证同样安全收敛。

---

# RB-21-04 — Permit Integrity Must Fail Closed

**优先级：P1**

## 5.1 当前问题

当前 permit：

```json
{
  "expires_at": "..."
}
```

若 timestamp 无法解析，代码按：

```text
无法判断过期
→ 当作未过期
```

这适合 lease 的“不自动释放”语义，但不适合 authorization permit。

Authorization token 必须：

```text
cannot prove valid
→ deny
```

---

## 5.2 新 permit integrity chain

`validate_permit()` 必须检查：

```text
permit dict
permit_id shape
permit_id matches filename/request
task_id valid
unit_id valid
wave_id valid type
mode valid
reason invariant
created_at parseable
expires_at parseable
expires_at >= created_at
consumed == false
expires_at > now
```

任何 malformed：

```text
(False, "permit malformed")
```

或者更具体 reason。

推荐保持有限 reason vocabulary。

---

## 5.3 时间边界

建议统一：

```text
expires_at <= now
→ expired
```

当前“恰好等于 now 仍有效”的语义没有明显价值。

如为兼容性决定保留：

```text
expires_at < now
```

亦可，但必须写入测试和文档。

关键是：

```text
invalid timestamp != valid permit
```

---

## 5.4 Regression tests

必须新增：

```text
malformed_expires_at_denied
malformed_created_at_denied
expires_before_created_denied
invalid_mode_in_file_denied
background_reason_in_file_denied
permit_id_payload_mismatch_denied
consumed_true_payload_denied
```

---

# RB-21-05 — Multi-Repository Reconcile Root Consistency

**优先级：P1**

## 6.1 当前问题

v2.0.1 已建立：

```text
ledger root
!=
git repository root
```

例如：

```text
workspace/
  .glm-conductor/
  repo-A/.git/
```

当前：

```text
verify_unit
finish_unit
Stop Gate
```

已经使用：

```python
state.resolve_repository_root()
```

但：

```python
reconcile_agent_run()
```

仍直接把 `repo_root` 当 git root。

因此：

```text
normal completion 正常
crash recovery 可能在错误 repo 求 residue
```

---

## 6.2 正式修复

`reconcile_agent_run()` 应明确接收：

```text
ledger_root
```

并从 state 解析：

```python
git_root = state.resolve_repository_root(st, ledger_root)
```

所有 repository evaluation：

```text
git_touched_files
ownership classify
fingerprint
```

必须使用：

```text
git_root
```

所有 ledger：

```text
state
journal
agent runs
```

继续使用：

```text
ledger_root
```

---

## 6.3 不得复制 root resolution

优先直接复用：

```python
state.resolve_repository_root()
```

避免再实现第三套 repository binding。

若循环 import 成为问题，应通过：

```text
局部 import
```

解决，而不是复制逻辑。

---

## 6.4 必须新增 multi-repo recovery test

场景：

```text
ledger_root/
  .glm-conductor/tasks/<task>/
  repo-real/
    .git/
    src/file.py
```

state：

```json
{
  "repository": {
    "root": ".../repo-real"
  }
}
```

要求：

```text
reconcile_agent_run
```

能够正确读取：

```text
repo-real 的 residue
```

而非 ledger root。

---

# 7. Secondary Hardening

以下建议纳入 alpha3，但优先级低于 RB-21-01~05。

---

# SH-21-01 — `record_quota_wake()` Idempotence

当前同一 automation 多次：

```python
record_quota_wake(...)
```

会重复消耗窗口。

改为：

```text
automation_id = idempotency key
```

若 journal/state 已记录：

```text
same automation_id
→ return existing consumption
→ do not increment
```

不同 automation_id 才：

```text
consumed += 1
```

---

## 写入前重新验证授权

`record_quota_wake()` 必须检查：

```text
auto_resume ∈ {auto_once, until_done}
authorization.source == user
remaining > 0
```

否则拒绝。

不能允许：

```text
manual task
```

通过直接调用 API 制造 consumed window。

---

# SH-21-02 — Release Metadata

alpha / rc GitHub releases：

```text
prerelease = true
```

正式：

```text
v2.1.0
prerelease = false
```

当前 alpha2 名称虽是 alpha，但 GitHub release metadata 不应标稳定。

---

# SH-21-03 — Python Version Contract

当前 CI：

```text
Python 3.8
Python 3.13
```

因此 release docs 不应声称：

```text
Python 3.7 compatible
```

除非真正增加：

```text
Python 3.7 CI
```

本轮推荐：

```text
official minimum = Python 3.8
```

保持 CI 与文档一致。

---

# 8. 明确延后到 v2.2 的内容

以下不要混入 alpha3：

---

## 8.1 Full RecoveryCoordinator

v2.2 可正式引入：

```python
resume_task()
```

统一：

```text
quota
reconcile
result recovery
progress package
readiness
wave dispatch
join
verification
```

v2.1-alpha3 只需关闭 correctness gap，不进行大规模 orchestration 重构。

---

## 8.2 Rich Native Agent Identity

当前：

```text
agent_id
tool_use_id
```

后续可升级：

```text
task_id
unit_id
permit_id
tool_use_id
parent_session_id
agent_id
child_session_id
```

解决跨 session archive 查找的唯一性问题。

本轮仅在不增加复杂度时顺带补字段，否则进入 v2.2。

---

# 9. Work Package 划分

建议按以下顺序实施。

---

## WU-A3-01 — Quota Resume Closure

修改：

```text
runtime/task_manager.py
runtime/quota/scheduler.py（原则上只复用，不重写）
runtime/quota/resolver.py（原则上只消费现有 force_refresh）
runtime/reconcile.py
runtime/state.py（如需 interruption metadata）
runtime/work_unit.py（如需 transition/schema 支持）

tests/test_quota_*.py
tests/test_task_manager.py
tests/test_reconcile.py
```

完成：

```text
force-refresh wake
max(reset)+grace
origin preservation
reconcile before redispatch
```

---

## WU-A3-02 — Review Invocation Provenance

修改：

```text
hooks/post_tool_use.py
runtime/agent_run.py
runtime/provenance.py
hooks/stop_gate.py（如需要适配 receipt schema）
runtime/journal.py（只有确有需要时）

tests/test_agent_run.py
tests/test_provenance.py
tests/test_stop_gate.py
tests/test_hooks*.py
```

完成：

```text
real reviewer invocation
→ tool_use_id binding
→ one receipt
→ final fingerprint
```

---

## WU-A3-03 — Wave Transaction Compensation

修改：

```text
runtime/task_manager.py
runtime/dispatch_wave.py
tests/test_wave_dispatch.py
```

完成：

```text
permit/write failure
→ no half wave
```

---

## WU-A3-04 — Permit Integrity Hardening

修改：

```text
runtime/dispatch_wave.py
hooks/pre_tool_use.py（仅当 reason 映射需要）
tests/test_dispatch_wave.py
tests/test_hooks*.py
```

完成：

```text
malformed permit
→ deny
```

---

## WU-A3-05 — Multi-Repo Reconcile

修改：

```text
runtime/reconcile.py
runtime/state.py（仅复用 root resolution）
tests/test_reconcile.py
```

完成：

```text
ledger root / git root split
```

---

## WU-A3-06 — Secondary Hardening

修改：

```text
runtime/task_manager.py
CHANGELOG.md
README.md
README.en.md
release documentation
```

完成：

```text
quota wake idempotence
release prerelease metadata guidance
Python version consistency
```

---

# 10. Critical Regression Tests

除现有 1257 tests 外，必须新增以下测试。

---

## 10.1 Quota

```text
wake_force_refresh_bypasses_fresh_cache

two_exhausted_windows_use_latest_reset

auto_once_not_scheduled_at_first_partial_reset

running_quota_interruption_not_blindly_ready

running_interrupted_reuse_result_not_redispatched

running_interrupted_progress_requires_progress_resume

manual_ruling_does_not_dispatch

explicit_resume_status_does_not_fetch_network
```

---

## 10.2 Review Provenance

```text
fake_review_tool_use_id_rejected

non_reviewer_tool_use_id_rejected

reviewer_identity_mismatch_rejected

review_tool_use_wrong_task_rejected

review_tool_use_replay_idempotent

real_reviewer_ship_receipt_passes

review_receipt_becomes_stale_after_repo_change
```

---

## 10.3 Wave Fault Injection

```text
second_permit_create_failure_rolls_back_wave

permit_failure_releases_all_wave_leases

permit_failure_invalidates_partial_permits

wave_state_save_failure_rolls_back_permits

wave_prepare_failure_has_no_prepared_event
```

---

## 10.4 Permit Integrity

```text
malformed_expiry_denied

malformed_created_at_denied

expires_before_created_denied

payload_permit_id_mismatch_denied

invalid_mode_denied

invalid_reason_invariant_denied

consumed_true_payload_denied
```

---

## 10.5 Multi Repo

```text
agent_reconcile_uses_bound_repository_root

agent_reconcile_reads_journal_from_ledger_root

multi_repo_residue_classification_correct
```

---

## 10.6 Quota Wake Idempotence

```text
same_automation_recorded_once

different_automation_consumes_second_window

manual_mode_cannot_record_auto_wake

budget_zero_rejects_new_wake
```

---

# 11. Dogfood Protocol — RC1 必须重新实测

单元测试全绿不足以直接发布 stable。

必须重新执行以下五个真实场景。

---

# Dogfood R1 — Multi-window Quota Exhaustion

制造或模拟：

```text
window A EXHAUSTED
window B EXHAUSTED
reset(A) < reset(B)
```

要求：

```text
wake scheduled at max(reset) + grace
```

wake 后：

```text
force refresh provider
```

不得命中旧 fresh cache。

---

# Dogfood R2 — Crash During Running Worker + Quota Recovery

```text
background worker running
→ interrupt
→ quota waiting
→ new wake/session
```

要求：

```text
reconcile_agent_run first
```

不得直接：

```text
ready + clean redispatch
```

若有 residue：

```text
resume_with_progress
```

或：

```text
manual_ruling
```

---

# Dogfood R3 — Wave Partial Permit Failure

人工 fault injection：

```text
2-unit wave
permit #1 succeeds
permit #2 fails
```

最终磁盘必须：

```text
no active wave
no live permits
no wave leases
```

---

# Dogfood R4 — Fake Review Attempt

人工调用：

```text
review-record
```

并提供不存在的：

```text
tool_use_id
```

必须：

```text
reject
```

然后真实运行 reviewer：

```text
reviewer invocation
→ ship
→ receipt
→ gate passes
```

---

# Dogfood R5 — Ledger Root ≠ Git Root Recovery

真实构建：

```text
workspace ledger
+ nested git repository
```

中断 worker 后：

```text
reconcile
```

必须基于绑定 repo 求 residue。

---

# 12. Definition of Done — Alpha3

`v2.1.0-alpha3` 必须满足：

### Quota

- [ ] wake 后 resolver 强制 refresh；
- [ ] resume planning 只有 scheduler 一套算法；
- [ ] 多阻塞窗口使用最晚 reset；
- [ ] interrupted running unit 不会 blind redispatch；
- [ ] auto_once 不浪费在 premature wake。

### Review

- [ ] fake tool_use_id 无法创建有效 review receipt；
- [ ] reviewer invocation 有 runtime-observed ledger；
- [ ] reviewer identity 与 task 绑定；
- [ ] receipt 不可 replay；
- [ ] Completion Gate 仍只认 fresh ship receipt。

### Wave

- [ ] permit 创建中途失败无半 wave；
- [ ] leases / permits / wave 一致；
- [ ] fault injection 全绿。

### Permit

- [ ] malformed authorization permit fail-closed；
- [ ] timestamp 损坏不得 allow；
- [ ] payload/schema corruption 不得 allow。

### Multi-repo

- [ ] reconcile 使用 `repository.root`；
- [ ] ledger I/O 与 git evaluation 双根分离；
- [ ] v2.0.1 multi-repo 语义无回退。

### Regression

- [ ] full unittest green；
- [ ] validator green；
- [ ] Ubuntu Python 3.8 / 3.13 green；
- [ ] Windows Python 3.8 / 3.13 green。

---

# 13. Definition of Done — RC1

`v2.1.0-rc1`：

```text
禁止新增功能
```

只允许：

```text
bugfix
docs
tests
dogfood fallout
```

必须：

- [ ] Dogfood R1–R5 全通过；
- [ ] 无新的 P0/P1 runtime integrity issue；
- [ ] release notes 与真实实现一致；
- [ ] alpha/rc GitHub release 标记 prerelease；
- [ ] Python minimum version 与 CI 一致；
- [ ] README / README.en / CHANGELOG 一致；
- [ ] 当前全部现有 dogfood A–E 不回退。

---

# 14. Stable Release Gate

只有 RC1 满足以下条件才发布：

```text
v2.1.0
```

最终稳定版需要证明的核心闭环：

```text
Policy
↓
Permit
↓
Dispatch
↓
Runtime-observed Agent Run
↓
Crash / Quota interruption
↓
Evidence-based Reconcile
↓
Authorized Resume
↓
Verification
↓
Runtime-bound Review
↓
Completion Gate
```

任意一环不能仍依赖：

```text
“主模型应该记得这样做”
```

---

# 15. Agent 实施纪律

实施 Agent 必须遵守：

1. 每个 WU 独立 commit；
2. 修改前阅读现有 tests 与模块 docstring；
3. 优先复用已有 primitive，不复制已有算法；
4. 不删除已有 regression test；
5. 所有新失败路径必须有 fault-injection test；
6. 禁止用修改测试期望值掩盖行为回退；
7. schema 新字段必须 legacy-compatible；
8. 所有安全授权判断遵循：

```text
cannot prove valid
→ deny / manual ruling
```

9. 所有恢复判断遵循：

```text
cannot prove completed
→ do not mark completed
```

10. 所有 external side effect：

```text
git push
release
publish
delete
```

不得自动执行。

---

# 16. 推荐实施顺序

严格按：

```text
WU-A3-01  Quota Resume Closure
        ↓
WU-A3-05  Multi-Repo Reconcile
        ↓
WU-A3-02  Review Provenance
        ↓
WU-A3-03  Wave Transaction
        ↓
WU-A3-04  Permit Integrity
        ↓
WU-A3-06  Secondary Hardening
        ↓
Full Regression
        ↓
Dogfood R1-R5
        ↓
alpha3
        ↓
rc1
        ↓
stable
```

其中：

```text
Quota Resume
+
Multi-Repo Reconcile
```

建议连续实施，因为二者共享 recovery evidence chain。

---

# 17. v2.2 后续方向

本轮 stable 后再规划 v2.2。

最高优先级建议：

## RecoveryCoordinator

正式建立：

```python
resume_task()
```

统一：

```text
SessionStart / wake
→ quota refresh
→ interrupted-unit classification
→ native result recovery
→ progress package
→ readiness
→ bounded wave dispatch
→ join
→ verification
```

届时 GLM Conductor 将从：

> “具有大量可靠 orchestration primitives”

进一步升级为：

> **“具有确定性恢复入口的长期 Agent Local Control Plane”**

但该工作不得进入 v2.1 alpha3，以避免 release hardening 再次演变为 feature development。

---

# 18. 最终验收原则

本轮工作的判定标准不是：

```text
新增了多少代码
增加了多少测试
```

而是：

> **在 crash、quota exhaustion、partial I/O failure、fake provenance、multi-repo workspace 等异常情况下，runtime 是否仍能确定性、安全地收敛。**

v2.1 stable 的最终目标应冻结为：

```text
normal path works
AND
failure path converges safely
AND
recovery does not redo trustworthy work
AND
completion cannot be forged
```

完成上述要求后，才允许从：

```text
v2.1.0-alpha / rc
```

升级为：

```text
v2.1.0 stable
```