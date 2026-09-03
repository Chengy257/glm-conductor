# GLM-Conductor v2.2 Release Hardening 修复实施计划

> 适用分支：`v2-dev`  
> 审查基线：`6d2ee2dd30fcc69b4586a5f5e49e388c7a40edc9`  
> 上一架构基线：`75be3ecc2a17647a590956483706370f4f5536e9`  
> 文档定位：**v2.2 stable 前的 release-hardening 实施文档**  
> 优先级：本文件高于当前未完成的扩展性计划；本阶段禁止继续扩展新 transport / 新 agent capability。  
> 目标：修复副作用幂等、授权边界、账本崩溃一致性与真实跨窗验收问题，并完成 stable release gate。

---

## 1. 当前状态与总体结论

相对 `75be3ec`，当前 `v2-dev` 已推进 21 个提交，C-series 主体已经落地，包括：

- C2 Quota Epoch / 双 boundary；
- C3 Real-Time Quota Watcher；
- C4 Window Primer；
- C5 Quota Subscription；
- C6 Activation Transport / scheduler facts / Cron hooks；
- C7 resume-time quota consumption；
- C8 Stop Gate continuation health；
- 对应 CLI、journal、state、tests 与 release-face 文档。

当前实现已经从“架构未完成”进入“release hardening”阶段。

### 1.1 本轮不再重新设计的部分

以下结构经审查后继续保留，不作为本轮重构目标：

1. `runtime/quota/observer.py` 保持纯决策层，Watcher 独立承担长运行 I/O；
2. Quota Epoch 身份基于 `(kind, reset_at)`，不包含 percent/status；
3. probe boundary 与 executable boundary 分离；
4. `recurring_bridge` 仍为唯一 stable Activation Transport；
5. `probe_then_hold` / `self_retiming` / `session_injector` 继续保持 reserved / experimental；
6. arm / fire / CronCreate 不再消费 quota window；
7. quota window 仅在 authorized resume commit point 消费；
8. `new_consumed >= legacy_consumed` 的保守迁移原则保持；
9. Watcher 当前仍是 skeleton-first，负责 quota truth，不直接唤醒 dormant Session。

### 1.2 当前 release 结论

当前代码可以继续作为：

```text
v2.2 alpha / beta
```

但暂不应直接发布：

```text
v2.2 stable
```

Stable 前应至少关闭：

- P1-A Primer timeout duplicate-call 风险；
- P1-B Primer mechanical authorization gap；
- P2 quota consumption crash-consistency；
- RG-22-06 新 C-series 双 quota epoch end-to-end dogfood。

---

# 2. 修复范围与实施顺序

本轮冻结实施顺序：

```text
RH-01 Primer single-attempt hardening
    ↓
RH-02 Primer mechanical authorization
    ↓
RH-03 Quota consumption crash consistency
    ↓
RH-04 Boundary evidence semantic cleanup
    ↓
RH-05 CI / release verification hardening
    ↓
RH-06 RG-22-06 real multi-epoch dogfood
    ↓
RH-07 stable release decision
```

禁止为了完成本轮修复而引入：

- 新 Activation Transport；
- Session Injector；
- Probe-Then-Hold 正式实现；
- CronUpdate correctness path；
- 数据库 / 外部服务；
- 新 daemon/service manager；
- 大规模 state schema 重构；
- 与问题无关的代码格式化或目录重组。

## 决策锁定（2026-09-04）

> [修正 2026-09-04] 新增本节：记录本轮已锁定的实施决策，后续 Work Unit 按此执行，不再在 §3–§7 的多方案之间重新选择。

### RH-01 决策

- 移除 retry loop；
- timeout 类「可能已到达 provider」的网络失败改为 post-refresh 确认：epoch 推进 → `materialized=True`；未确认 → `materialized=False` + error；
- 明确未到达 provider 的失败（no-credential / URL 配置错 / 本地参数错）零 post-refresh；
- HTTP 5xx / auth / malformed 不重发、不做歧义确认；
- primed 语义不变（= 模型调用完成即 error 为 None）；
- timeout + `materialized=True` 为合法记录组合；
- `PRIMER_RECORD_FIELDS` 七键零变化。

### RH-02 决策

- 方案 A：`prime_once` 降为 `_prime_once_unchecked`，新增公共 `prime_authorized(repo_root, task_id, ...)` 内嵌三重闸。

### RH-03 决策

- 方案 A：write-ahead pending marker；
- 迁移同款 pending marker（语义按 §5.4 修正版：先冻结 legacy/verified/attributed 三数再动 state）。

### RH-04 决策

- 方案 A：新事件字段 `representative_boundary_id` + 双键兼容读，不重写旧 journal。

### RH-05 决策

- 按修正一（§7）塌缩：RH-01..05 本地 commit 完成后统一 push 触发新一轮 CI，四路全绿即关闭 §9.2「CI 独立 green」。

---

# 3. RH-01 — P1：Primer 模型调用必须改为 single-attempt

## 3.1 问题

当前 `runtime/quota/primer.py` 同时存在两个互相冲突的语义：

设计语义：

```text
同一 provider_identity_hash + boundary_id
最多执行一次具有 provider 副作用的 prime
```

但实际实现允许：

```text
MAX_PRIME_ATTEMPTS = 2
```

并将网络 timeout 判定为 retryable。

可能产生：

```text
Prime Request #1
    ↓
Provider 已执行模型调用并 materialize 新窗口
    ↓
Response 在客户端 timeout
    ↓
Primer 自动发送 Request #2
```

该行为与 Primer 的 single-flight / P0-QP-07 设计冲突。

Primer 是具有 provider-side state effect 的调用，不应按普通幂等 GET 请求处理。

---

## 3.2 决策

冻结：

```text
一个 boundary_id 下，Primer 模型调用最多发出一次。
```

即：

```python
MAX_PRIME_ATTEMPTS = 1
```

或直接移除 retry loop。

### timeout 后的正确处理

不得立即重复模型请求。

应执行：

```text
prime request
    ↓ timeout / ambiguous response
force-refresh quota
    ↓
检查 epoch/reset_at 是否已推进
    ├─ 已推进 → materialized=True
    └─ 未确认 → materialized=False + INDETERMINATE/NETWORK failure
```

重点：

> timeout 表示“结果未知”，不是“provider 一定没有执行”。

---

## 3.3 推荐实现

调整 `_prime_call()`：

```python
def _prime_call(...):
    return _attempt_once(...)
```

删除 timeout retry。

如果希望区分语义，可新增内部 error state：

```text
network_timeout_indeterminate
```

但不强制增加 public enum；也可保留：

```json
{
  "kind": "network",
  "detail": "socket.timeout"
}
```

前提是后续仍执行 post-refresh confirmation。

### 关键变化

当前逻辑：

```text
call failure
→ _fail()
→ 不做 post-refresh
```

应调整为：

```text
call attempted
→ 无论 success / timeout（仅对可能已到达 provider 的网络失败）
→ post-refresh
→ 判定是否 materialized
```

对于明确未到达 provider 的错误：

- no credential；
- URL/config validation failure；
- local parameter failure；

可继续零 post-refresh。

---

## 3.4 必须修改的测试

更新：

```text
tests/test_primer.py
```

移除或改写：

```text
test_timeout_retry_then_success
```

新增至少：

### PRIMER-RH-01

```text
timeout → transport calls == 1
```

### PRIMER-RH-02

```text
timeout + post-refresh epoch advanced
→ materialized=True
→ no second model call
```

### PRIMER-RH-03

```text
timeout + post-refresh same epoch
→ materialized=False
→ no second model call
```

### PRIMER-RH-04

```text
replay same provider_identity_hash + boundary_id
→ zero transport
→ zero quota refresh
→ idempotent=True
```

### PRIMER-RH-05

```text
HTTP 5xx / auth / malformed
→ no model-call retry
```

---

## 3.5 验收

必须满足：

```text
同一个 idempotency key 下，任何代码路径 transport call count <= 1
```

并通过静态测试确保不存在：

```text
retryable timeout → second model request
```

---

# 4. RH-02 — P1：Primer 授权必须在真正发模型请求的 public API 内机械强制

## 4.1 问题

当前代码已有：

```python
authorize_prime(policy_view)
```

但真正发模型调用的：

```python
prime_once(...)
```

本身不读取 task/policy，也不验证授权。

当前模式实质是：

```text
caller discipline
    ↓
authorize_prime()
    ↓
prime_once()
```

而不是：

```text
runtime public API
    ↓
mechanical authorization
    ↓
model call
```

这违背项目一贯原则：

> 关键安全 / quota / authorization 条件不能只依赖 Agent prompt discipline。

---

## 4.2 决策

将“是否允许发 Primer 模型调用”的授权闸收口至**唯一 public execution API**。

推荐结构：

```python
prime_authorized(repo_root, task_id, *, ...)
```

流程：

```text
load task state
    ↓
read execution_policy
    ↓
authorize_prime(policy)
    ↓
derive/check provider identity + boundary
    ↓
_prime_once_unchecked(...)
```

当前：

```python
prime_once(...)
```

建议：

### 方案 A（推荐）

重命名为：

```python
_prime_once_unchecked(...)
```

仅供：

- 单元测试；
- 内部已授权 wrapper。

对外只暴露：

```python
prime_authorized(...)
```

### 方案 B

保留 `prime_once` 名称，但增加：

```text
task_id
policy/state lookup
authorization gate
```

不允许调用者绕过。

---

## 4.3 授权条件冻结

必须同时满足：

```text
primer_enabled == True
AND
auto_resume ∈ {auto_once, until_done}
AND
authorization.source == "user"
```

以下必须机械拒绝：

```text
manual
notify
authorization.source != user
primer_enabled == false
policy missing / malformed
```

拒绝路径：

```text
zero model call
zero provider mutation
zero primer record
zero window consumption
```

是否记录“authorization denied”审计事件可选，但不得制造 `window_primed`。

---

## 4.4 Public API 返回建议

授权失败建议返回结构化结果，或抛专门异常：

```python
PrimerAuthorizationError
```

不建议使用裸 `ValueError` 表达未授权。

例如：

```json
{
  "authorized": false,
  "primed": false,
  "materialized": false,
  "executable": false,
  "reason": "primer_disabled"
}
```

具体 surface 可由实施者保持现有兼容风格，但必须保证：

> 未授权调用无法进入 transport。

---

## 4.5 测试

新增：

### PRIMER-AUTH-01

```text
primer_enabled=false
→ transport calls == 0
```

### PRIMER-AUTH-02

```text
manual
→ transport calls == 0
```

### PRIMER-AUTH-03

```text
notify
→ transport calls == 0
```

### PRIMER-AUTH-04

```text
auto_once + source=default
→ transport calls == 0
```

### PRIMER-AUTH-05

```text
until_done + source=user + primer_enabled=true
→ exactly one authorized attempt
```

### PRIMER-AUTH-06

直接调用对外 public API，不能绕过 authorize。

---

# 5. RH-03 — P2：quota consumption 的 state/journal 崩溃一致性

## 5.1 问题

当前：

```python
record_quota_boundary_consumed(...)
```

大致顺序：

```text
state.consumed += 1
    ↓
save_state()
    ↓
journal quota_boundary_consumed
```

若发生：

```text
save_state success
journal append failure
```

会形成：

```text
state says consumed
journal lacks epoch idempotence evidence
```

如果同 epoch 再调用 record API，有潜在二次 +1 风险。

现有 resume subscription / activation mark 对主路径提供部分保护，但 API 自身宣称的：

```text
same epoch replay idempotent
```

目前尚非完整 crash-safe 契约。

---

## 5.2 本轮目标

不要求引入数据库事务。

目标是确保：

```text
同 task_id + epoch_id
无论在 state write / journal write 的哪个位置崩溃
最多只形成一次 quota consumption
```

---

## 5.3 推荐实现方案

### 方案 A：write-ahead pending marker（推荐）

增加任务 journal 事件：

```text
quota_consumption_pending
```

字段至少：

```json
{
  "event": "quota_consumption_pending",
  "epoch_id": "...",
  "executable_boundary_id": "...",
  "target_consumed": 2,
  "resume_started_at": "..."
}
```

事务：

```text
1. scan committed + pending evidence
2. if same epoch already committed → idempotent return
3. if pending exists → reconcile instead of increment
4. append pending
5. update state projection
6. append quota_boundary_consumed committed event
7. optional append quota_consumption_reconciled / resolved
```

恢复时：

```text
pending exists + state already target_consumed
→ only finish committed journal
```

```text
pending exists + state not updated
→ apply state projection once
→ finish committed journal
```

核心原则：

```text
epoch identity exists before mutable projection is increased
```

### 方案 B：journal authoritative

长期可考虑：

```text
journal consumption events = truth
state.consumed = cached projection
```

但本轮不建议扩大为全局账本架构重构。

---

## 5.4 `migrate_quota_window_accounting` 同类问题

> [修正 2026-09-04] 本节重写。原文以「同类问题」暗示迁移与消费记账一样存在「二次上调」风险，该表述不精确。

事实：

`new_consumed = max(legacy_consumed, verified_consumed)` 在结构上防止二次上调——崩溃重跑时 `legacy_consumed` 已是新值，`max` 不会再次抬高。

真实风险是：

1. migration marker 缺失导致幂等闸失效后重跑；
2. 崩溃后重跑时 `legacy_consumed` 从已更新 state 误读，迁移事件记录的 `legacy_consumed` / `attributed_consumed` / `legacy_unattributed_consumed` 归属拆分失真。

修法（与消费记账同款 pending marker）：

```text
quota_accounting_migration_pending
```

在动 state 之前先冻结 legacy / verified / attributed 三个数字；重跑时按 pending 记录补齐 migration marker，不依赖可能已被污染的现场 state。

`MIGRATE-TXN-01` 的期望相应修正（见 §5.5 标注）：重跑只按 pending 补齐 marker、不二次上调、迁移记录不失真。

---

## 5.5 必须增加 fault-injection 测试

### CONSUME-TXN-01

模拟：

```text
pending journal success
state save failure
```

重跑不得多消费。

### CONSUME-TXN-02

模拟：

```text
state save success
committed journal failure
```

重跑不得多消费。

### CONSUME-TXN-03

```text
same epoch normal replay
→ consumed unchanged
```

### CONSUME-TXN-04

```text
different epoch
→ consumed +1
```

### CONSUME-TXN-05

```text
pending event survives process restart
→ reconciliation closes transaction
```

### MIGRATE-TXN-01

> [修正 2026-09-04] 期望按 §5.4 修正版改为：重跑只按 pending 补齐 marker、不二次上调、迁移记录（legacy_consumed / attributed_consumed / legacy_unattributed_consumed 拆分）不失真。原文仅覆盖「不二次上调」，未覆盖归属拆分不失真。

```text
migration state save success
migration event failure
→ rerun only completes marker
→ no second upward projection
```

---

# 6. RH-04 — P3：`executable_boundary_id` 命名与来源清理

## 6.1 问题

当前 C7 辅助证据：

```text
executable_boundary_id
```

由：

```python
_bridge_boundary(windows, 0)
```

生成。

该 helper 是：

```text
earliest parseable reset
```

而 C2 真正冻结的 executable boundary 是：

```text
max(blocking EXHAUSTED reset_at) + grace
```

因此当前字段名与实际来源并不严格一致。

核心幂等性不受影响，因为真正权威键已经是：

```text
task_id + epoch_id
```

所以本项为 P3 semantic cleanup，不是 correctness blocker。

---

## 6.2 决策

二选一。

### 推荐 A：重命名辅助证据

若当前字段表达的是“epoch 中用于人类审计的代表窗口”：

```text
representative_boundary_id
```

比 `executable_boundary_id` 更准确。

### 或 B：真正按 C2 executable boundary 派生

使用：

```python
quota.epoch.executable_boundary_at(...)
```

并构造与 blocking latest window 对应的 id。

---

## 6.3 兼容要求

如果已有 journal 中包含：

```text
executable_boundary_id
```

不要破坏历史读取。

允许：

```text
legacy executable_boundary_id
+
new representative_boundary_id
```

迁移读取兼容。

不要为了改名重写旧 journal。

---

# 7. RH-05 — CI / Release Engineering Hardening

## 7.1 当前事实

> [修正 2026-09-04] 本节重写。原文前提「GitHub 当前 Head 没有对应 combined CI status」已失效：`.github/workflows/validate.yml` 早已存在，独立 CI 证据链已经建立。

事实：

`.github/workflows/validate.yml` 早已存在：

- 由 `1103e15` 引入（feat: add lightweight static validator and CI workflow）；
- `61518b4` 增加全量单测步骤；
- `2a18785` 增加平台矩阵。

当前工作流形态：

- 触发：push + pull_request；
- Matrix：Linux + Windows × Python 3.8 / 3.13，共四路；
- 每路执行：

```text
python scripts/validate_plugin.py
python -m unittest discover -s tests -v
```

其中 `unittest discover` 为全量套件（约 2076 项）。

审查基线 `6d2ee2d` 对应的 run `33769790818` 已于 `2026-09-03T14:56:31Z` 四路全绿（4m6s）。

### RH-05 范围塌缩

RH-05 范围塌缩为：

```text
RH-01..05 本地 commit 完成后统一 push
    ↓ 触发新一轮 CI
四路全绿
    ↓ 即关闭 §9.2「CI 独立 green」
```

push 前无法预先验证远端 CI 结果——这是既定 push 纪律（实施期只本地 commit、末次统一 push）的时序后果，而非缺口。

默认不加 branch protection（个人仓库现状）。

---

## 7.2 最低要求

> [修正 2026-09-04] 本节既有要求视为已被现有工作流满足：全量 Python test suite + validate_plugin 已在每路 matrix 执行，matrix 已覆盖最低（3.8）与最新（3.13）Python。以下原文保留备查。

GitHub Actions 至少执行：

```text
full Python test suite
validate_plugin
```

推荐 matrix：

```text
Python 3.8+
当前项目声明支持的主要版本
```

如果成本过高，可至少保留：

```text
one stable Python
+
one minimum supported Python
```

---

## 7.3 必须检查

> [修正 2026-09-04] 本节既有要求视为已被现有工作流满足：现有 CI 仅运行 validate_plugin 与 unittest，零真实模型调用、零真实凭证、无永久 watcher、无 Scheduled Task。以下原文保留备查。

CI 不应：

- 发真实 Primer model call；
- 使用真实 Coding Plan credential；
- 启动永久 Watcher；
- 创建 Scheduled Task。

所有这些必须依靠 injected transport/fake provider。

---

# 8. RH-06 — RG-22-06：真正的 C-series 双 quota epoch dogfood

这是 stable release 的关键硬门。

## 8.0 执行环境注记

> [修正 2026-09-04] 新增本小节：记录 RH-06 执行环境的用户裁决方向与备选，最终在 RH-06 开跑时确定。

- **裁决方向（用户 2026-09-04）**：在其它项目的干净会话中，用本仓库 checkout 的代码直跑（`ledger_root` 指向该项目）；不动已安装插件（当前 marketplace 快照 2.1.0），版本 bump 议题保持延后。
- **备选**：本仓库直跑（同 wu-22-12 dogfood 路径）。
- 最终方案在 RH-06 开跑时确定。
- 届时需要用户：开新会话；确认该会话 CronCreate 可用（宿主工具注入逐会话可变）；白名单桥 prompt 步骤。

## 8.1 当前缺口

已有真实证据包括：

- 多个 provider quota boundary；
- Watcher resident observation；
- historical recurring bridge 多次 fire；
- DRAINING/PRESSURE gate；
- Session handoff；
- scheduler capability evidence。

但当前新架构尚缺：

```text
C-series corrected accounting
+
persistent recurring bridge
+
quota subscription
+
new epoch resume
+
至少连续跨两个新 quota epoch
```

的一条完整真实链。

---

## 8.2 Dogfood 场景

建立一个真实 `until_done` 长任务：

```text
max_quota_windows >= 2
authorization.source=user
recurring_bridge armed
quota_subscription enabled
```

必须真实经历：

```text
Epoch N
ACTIVE work
↓
DRAINING
↓
mechanical handoff
↓
EXHAUSTED
↓
waiting_quota
↓
Watcher observes boundary
↓
recurring bridge fire
↓
new executable epoch N+1
↓
quota-resume
↓
subscription eligibility
↓
activation mark
↓
quota consumption +1
↓
work continues
↓
再次进入 next boundary
↓
Epoch N+2
↓
same bridge identity
↓
resume again
```

---

## 8.3 必须收集的证据

### Bridge

```text
same automation_id
runCount increases
no nested CronCreate
```

### Quota Epoch

至少：

```text
epoch_N
epoch_N+1
epoch_N+2
```

三者 identity 不同。

### Accounting

必须证明：

```text
bridge fire count != consumed_quota_windows
```

例如：

```text
bridge fires: 8
successful cross-epoch resumes: 2
consumed_quota_windows delta: 2
```

### Idempotence

同 epoch 重复 fire：

```text
no second consumption
no second activation
```

### Resume

每次：

```text
waiting_quota → executing
```

都必须有：

- reconcile evidence；
- activation epoch mark；
- quota boundary consumption；
- manifest refresh。

---

# 9. Stable Release Gate

只有以下全部满足，才能把 v2.2 标记 stable。

## 9.1 Code correctness

- [ ] Primer same-boundary transport call max 1；
- [ ] timeout 不自动重复 model request；
- [ ] timeout 后可执行 post-refresh confirmation；
- [ ] Primer public model-call API 机械授权；
- [ ] manual/notify 永远无法触发 Primer；
- [ ] quota consumption crash replay 不重复 +1；
- [ ] migration crash replay 不重复上调；
- [ ] bridge arm/fire/create 仍保持 zero consumption；
- [ ] observer 仍保持 pure；
- [ ] Watcher 仍保持 model-call-free；
- [ ] reserved transports 仍不半实现。

## 9.2 Tests

- [ ] 新 RH-01 / RH-02 / RH-03 测试全部通过；
- [ ] full suite green；
- [ ] `validate_plugin` 15/15；
- [ ] CI 独立 green；
- [ ] no real external model calls in CI。

## 9.3 Real dogfood

- [ ] RG-22-06 跨至少两个新 executable epoch；
- [ ] same persistent bridge identity；
- [ ] no nested Scheduled Task creation；
- [ ] accounting delta == successful cross-epoch resumes；
- [ ] repeated fire in same epoch zero duplicate consumption；
- [ ] watcher failure/absence does not corrupt task state；
- [ ] completion cleanup remains best-effort only；
- [ ] correctness does not depend on CronDelete/CronUpdate success。

---

# 10. Agent 实施纪律

实施 Agent 必须遵守以下规则。

## 10.1 禁止扩需求

本轮是 release-hardening，不是 v2.3。

不得新增：

```text
Session Injector
Probe-Then-Hold production path
Self-Retiming production path
automatic CronUpdate
new provider abstraction
database backend
OS service manager
```

## 10.2 不得修改已冻结核心语义

不得重新改回：

```text
arm-time quota consumption
automation_id-based window accounting
fixed +5h quota clock
earliest reset as executable resume boundary
Watcher directly managing task dispatch
Primer default-on
```

## 10.3 每个 Work Unit 必须

```text
implementation
→ targeted tests
→ full tests
→ independent review
→ journal / receipt
→ commit
```

不要把 RH-01~RH-06 合成一个超大提交。

---

# 11. 推荐 Work Unit 拆分

## `wu-22-RH01-primer-single-attempt`

范围：

- remove Primer model retry；
- timeout post-refresh confirmation；
- tests。

完成门：

```text
same idempotency key transport calls <= 1
```

---

## `wu-22-RH02-primer-authorization`

范围：

- public authorized Primer API；
- unchecked internal primitive；
- mechanical three-gate authorization；
- tests。

完成门：

```text
unauthorized → zero transport
```

---

## `wu-22-RH03-consumption-crash-consistency`

范围：

- consumption pending/reconcile；
- migration crash consistency；
- fault injection。

完成门：

```text
same task_id + epoch_id across any injected crash → max one consumption
```

---

## `wu-22-RH04-boundary-evidence-cleanup`

范围：

- auxiliary boundary naming/source；
- backward-compatible journal reader；
- tests/docs。

非 stable blocker，可与 RH03 后并行。

---

## `wu-22-RH05-ci`

范围：

- GitHub Actions；
- full suite；
- validator；
- zero external side effects。

---

## `wu-22-RH06-dogfood`

范围：

- real until_done task；
- >= 2 executable epoch crossings；
- recurring bridge reuse；
- corrected accounting evidence；
- release evidence record。

---

# 12. 最终 release 定位

当前 v2.2 的核心架构已经成立：

```text
Quota Control Plane
    =
Watcher + Epoch + optional Primer

Execution Plane
    =
Task/WorkUnit + Subscription + Resume + Reconcile + Permit/Lease

Activation Transport
    =
Persistent Recurring Bridge [STABLE]
```

但目前应继续准确表述为：

```text
real-time quota observation
+
bounded-latency same-session activation
```

暂不应宣传为：

```text
real-time quota event immediately wakes dormant session
```

因为 Watcher 当前只维护 quota truth，真正让 dormant Session 获得 execution opportunity 的 stable 路径仍然是 recurring Scheduled Task。

---

# 13. 本轮完成后的预期状态

完成 RH-01 ~ RH-06 后，v2.2 应达到：

```text
Architecture: closed
Mechanics: closed
Authorization: mechanical
Side-effect idempotence: hardened
Consumption accounting: crash-safe
Activation: bounded-latency recurring bridge
Quota truth: resident watcher
Real multi-epoch dogfood: proven
CI: independently green
```

届时再执行：

```text
RH-07 stable release review
```

若 reviewer 无新的 P0/P1 问题，可进入：

```text
v2.2.0 stable
```

在此之前，不建议继续增加新的 Continuity/Transport 功能。
