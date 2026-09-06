# GLM-Conductor v2.2 架构修正与实施交接：Quota Control Plane / Real-Time Quota Watcher / Session Activation

> **文档类型**：Architecture Correction / Agent Implementation Handoff  
> **目标版本**：GLM-Conductor v2.2.x  
> **目标分支**：`v2-dev`  
> **当前基线提交**：`75be3ecc2a17647a590956483706370f4f5536e9`  
> **当前基线状态**：M5 Persistent Wake Bridge 已落地；full suite `1665` tests green  
> **文档优先级**：P0。若本文件与旧 v2.2 Quota Continuity / Persistent Wake Bridge 计划冲突，以本文件为准。  
> **决策冻结**：2026-09-02 用户批准 13 项锁定裁决（Watcher skeleton-first、P0-QP-00/00A、P0-HOLD-00、P0-WATCH-00、bridge 默认 60、C1a/C1b 拆分、保守记账迁移、resume commit point、probe/executable 边界分工、SCHED-03 intermittent 回填、手动桥对账、C0 十三清单、旧 wu 处置），已并入正文对应章节；实施顺序为 `C0 → C1a → C2 → C1b → C3 骨架 → P0-QP → C4 → C5+`。  
> **核心主题**：Quota Control Plane、Real-Time Quota Watcher、Window Primer、Quota Epoch、Activation Transport、Persistent Recurring Bridge、跨窗口任务机械交接

---

# 0. 文档目的

当前 v2.2 已经从 v2.1 的“可恢复”推进到 Persistent Wake Bridge，但真实运行进一步暴露出一个更本质的问题：

> **Provider 的额度时间轴与 ZCode Session 的执行时间轴不是同一个系统。**

旧 v2.2 仍隐含地让 Session / Scheduled Task 同时承担：

1. 查询额度；
2. 推断 `reset_at`；
3. 等待额度刷新；
4. 维持下一窗口额度认知；
5. 唤醒同一 Session；
6. 决定是否继续任务。

这导致三个问题：

- `observer.py` 只能在 runtime 被重新进入时做 lazy heartbeat，Session 休眠时无法持续观察 provider；
- provider 的 `reset_at` 会漂移，不能用固定 5h cron 代替 provider truth；
- 即使外部已经知道额度恢复，当前公开、已验证的 ZCode Scheduled Task 能力仍缺少“任意时刻向一个休眠的既有 Session 注入新 turn”的稳定入口。

因此本修正不废弃 Persistent Wake Bridge，而是把 v2.2 拆成三个清晰层面：

```text
Quota Control Plane
        ↓
GLM-Conductor Execution Plane
        ↓
Activation Transport
        ↓
ZCode same-session continuation
```

最终目标从：

> “Session 自己负责跨额度窗口恢复”

修正为：

> **“独立 Quota Control Plane 持续维护真实额度时钟；GLM-Conductor 只消费 quota epoch / activation event 做任务调度；Activation Transport 单独负责把可执行额度变成同 Session 的执行机会。”**

---

# 1. 当前 `v2-dev` 实施基线：必须复用，不得重造

当前 `v2-dev` Head：

```text
75be3ecc2a17647a590956483706370f4f5536e9
```

已确认落地：

| 旧里程碑 | 当前状态 | 新架构处置 |
|---|---|---|
| M1 / M1a | 已完成：continuation schema、scheduler_context、persistent bridge schema | **保留** |
| M2 | 已完成：`NORMAL / PRESSURE / DRAINING / BLOCKED` execution quota phase | **保留** |
| M3 | 已完成：`runtime/quota/observer.py` pure/lazy observer | **保留，不改成 daemon** |
| M4 | 已完成：DRAINING dispatch gate | **保留** |
| M9 | 已完成：manifest / SessionStart recovery plane projection | **保留并扩展 quota-control projection** |
| M5 | 已完成：Persistent Wake Bridge、Universal Wake、tombstone、degraded、wake-plan/status | **保留主干，修正边界/记账语义** |
| M6 / M7 / M8 / M10+ | 尚未按最终控制面架构闭合 | **按本文件重写实施目标** |

当前实现的重要事实：

- `observer.py` 明确是纯决策层：
  - 零网络；
  - 零文件 I/O；
  - 无 daemon；
  - 无 timer；
  - 无 sleep loop；
  - 只有 runtime/hook/CLI 再次进入时才执行 `should_refresh()`。
- 因此它不是实时 Watcher，不能承担 Session 休眠期间的连续额度监测。
- M5 已经证明并保留：
  - one tracked task → prefer one persistent automation identity；
  - Universal Wake；
  - scheduled-owned session 内严禁 nested Scheduled Task create；
  - recurring bridge 可以重复进入同一 Session。
- Phase0 SCHED-04 已实测通过：
  - 同一 30min recurring automation 连续三次触发；
  - 都以同 Session user-turn 进入；
  - `runCount` 连续递增；
  - automation 保持 active；
  - 宿主调度抖动约 4–5 秒。

因此新的设计不是推翻 M5，而是：

> **把 Persistent Recurring Bridge 从“quota clock”降级为“session activation transport”；把真正的 quota clock 独立出去。**

---

# 2. 已冻结的宿主限制

以下限制作为架构硬约束，不再围绕其设计不存在的能力。

## 2.1 Scheduled-owned Session 不能再创建新 Scheduled Task

已实测：

```text
Cannot create a scheduled task inside a session that already belongs to a scheduled task
```

因此永久禁止：

```text
wake N
→ resume
→ create wake N+1
```

即：

```text
Per-window one-shot wake chain
```

不再是合法 correctness path。

---

## 2.2 CronUpdate 当前不能作为 correctness 前提

本机已有 glitch 红线：

```text
CronUpdate / CronDelete
→ 单次尝试
→ never retry
```

因此：

```text
reset_at changed
→ CronUpdate(existing task)
```

只能是未来优化，不能是 v2.2 stable 的基础。

2026-09-02 补记（SCHED-03 首个正样本）：scheduled-owned session 曾以单次尝试成功
删除自身 parent bridge（journal `bridge_deleted` 2026-09-02T08:31:20Z，single
attempt no retry；此前 Phase 0 尝试全部失败）。该样本只能记作：

```text
CronDelete: observed successful at least once, but reliability is intermittent
```

不得升级为 `reliably allowed`。红线 `single attempt / never retry` 不变；
`scheduler_context.delete` 仍是 session-local observation，不表达全局可靠性；
全局可靠性只写入 capability matrix / Phase0 evidence，不为此新增第四个枚举值。
Completion cleanup 的冻结措辞：

> MAY perform one best-effort pause/delete attempt. Correctness MUST NOT
> depend on success; tombstone + future no-op remains the correctness path.

---

## 2.3 Provider `reset_at` 不是固定 5h 周期

真实观察已经出现类似：

```text
21:59:00Z
→ 03:19:22Z
→ 08:35:48Z
```

周期分别约：

```text
5h20m22s
5h16m26s
```

因此永久禁止：

```text
next_reset = previous_reset + 5h
```

固定 cron 只能作为 host probing cadence，不能成为 quota truth。

---

## 2.4 Scheduled Task same-session recurring 已被证明可用

官方 ZCode Automations 语义与本项目 Phase0 一致：

- 从 Chat Session 创建的 Scheduled Task 绑定当前 Session；
- 后续 trigger 返回该 Session；
- recurring task 可持续触发；
- 上一轮尚未完成时，不应再启动一个 Run Now 重复实例；
- 宿主休眠 / App 关闭会导致 scheduled run missed/skipped。

因此：

> **Persistent Recurring Bridge 仍是当前官方能力范围内最可靠的 same-session activation transport。**

但它只能保证 bounded latency，不能自己做到 provider-reset 精确触发。

---

# 3. 新架构总览

```text
                     ┌─────────────────────────────┐
                     │      Quota Control Plane    │
                     │                             │
Provider API ───────▶│  Real-Time Quota Watcher   │
                     │  Boundary Detector          │
                     │  Window Primer              │
                     │  Quota Epoch Manager        │
                     │  Durable Quota State        │
                     │  Event Publisher            │
                     └──────────────┬──────────────┘
                                    │
                         QuotaEpoch / ActivationReady
                                    │
                                    ▼
                     ┌─────────────────────────────┐
                     │ GLM-Conductor Execution     │
                     │         Plane               │
                     │                             │
                     │ Task Graph / Work Unit      │
                     │ DRAINING / Checkpoint       │
                     │ Permit / Lease              │
                     │ Resume Manifest             │
                     │ Reconcile / Verification    │
                     │ Quota Subscription          │
                     └──────────────┬──────────────┘
                                    │
                           Resume Eligibility
                                    │
                                    ▼
                     ┌─────────────────────────────┐
                     │    Activation Transport     │
                     │                             │
                     │ recurring_bridge   [stable] │
                     │ probe_then_hold    [exp]    │
                     │ self_retiming      [future] │
                     │ session_injector   [exp]    │
                     └──────────────┬──────────────┘
                                    │
                                    ▼
                            same ZCode Session
```

核心职责边界：

```text
Watcher 不调度 task。
Conductor 不维护 provider 时钟。
Bridge 不代表 quota window。
Automation fire 不代表 quota consumption。
```

---

# 4. Layer A：Quota Control Plane

## 4.1 定义

Quota Control Plane 是一个独立于 Session turn 生命周期的本地控制面。

职责：

```text
provider quota polling
dynamic reset_at tracking
blocking-window evaluation
quota epoch detection
optional minimal window priming
durable state
event publication
```

不负责：

```text
task priority
work-unit dispatch
repository mutation
verification/review
task resume authorization
Scheduled Task create
```

---

# 5. 保留 `observer.py`，新增真正的 Watcher

## 5.1 `observer.py` 不改职责

当前：

```text
runtime/quota/observer.py
```

继续保持：

```text
PURE DECISION LAYER
```

禁止加入：

```text
while True
sleep()
background thread
provider network call
model call
state write
```

现有：

```python
next_check_interval(...)
should_refresh(...)
observe(...)
```

继续作为：

> **Watcher 和 Session runtime 共同复用的决策数学层。**

---

## 5.2 新增建议

建议新增：

```text
plugins/glm-conductor/runtime/quota/
├── observer.py             # EXISTING: pure decision
├── watcher.py              # NEW: long-running I/O control loop
├── watcher_store.py        # NEW: durable state + atomic write + lock
├── primer.py               # NEW: optional minimal quota-window activation
├── epoch.py                # NEW: boundary / executable epoch identity
├── activation_event.py     # NEW: event schema / subscription helper
├── control.py              # EXISTING
├── scheduler.py            # EXISTING
├── resolver.py             # EXISTING
└── ...
```

若希望减少文件，可合并为：

```text
watcher.py
primer.py
epoch.py
```

但不得把 I/O loop 塞回 `observer.py`。

---

# 6. Watcher 运行模式

新增：

```text
quota-watcher run
quota-watcher status
quota-watcher stop
quota-watcher once
```

建议初版为：

```text
single local process
+
single provider identity lock
```

不得允许两个 Watcher 对同一 provider/account 同时 prime。

状态锁至少包含：

```text
provider_identity_hash
pid
started_at
heartbeat_at
generation
```

v2.2 不要求解决跨多个 repository 的完整 quota arbitration。

第一阶段冻结：

> **一个 provider identity 同时最多一个 active watcher process。**

## 6.1 第一阶段范围（2026-09-02 锁定：skeleton-first，不做全量控制面）

C3 第一阶段只实现最小可运行 Watcher：

```text
独立长运行进程
single-instance / provider identity 锁
provider-only quota polling
复用 observer.py / resolver / parser（不复制额度逻辑）
ACTIVE / PASSIVE
dynamic reset_at 观察
durable watcher state
heartbeat / health
atomic state persistence
start / status / stop / once CLI
```

第一阶段明确不做：

```text
Window Primer
task subscription
ActivationReady → Session 激活
model call
session injector
system service / autostart
complicated event bus
```

C3 第一阶段要证明的命题只有一个：

> **Session dormant 时，GLM-Conductor 可以独立、持续、可靠地维护真实 provider quota clock。**

已知判断（不作为反向理由）：本 Watcher 在 `recurring_bridge` stable 模式下
**不会降低宿主唤醒延迟**；其近期价值是 quota truth 的连续性与决策质量。真正
缩短 latency 依赖后续 `probe_then_hold` / `session_injector` / `self_retiming`。

## 6.2 P0-WATCH-00（Windows 前置硬门）

C3 实现前必须在本机验证：

```text
single-instance lock
tmp + replace 原子写
watcher reader / writer 并发
PermissionError 的 bounded handling
stale lock recovery
```

工程约束：

```text
subprocess 一律使用 sys.executable
用户 CLI 文档仍写 python3
第一版不引入 Windows service
```

### 6.2.1 P0-WATCH-00 验证记录（2026-09-02/03，实施时回填）

实施工作单元 wu-22-C3 将五项硬门落地为持续门（`tests/test_watcher_store.py`，
`python3 -m unittest` 驱动；OS 原语零 mock 掉——门 5 的死 pid 用真实
subprocess 派生并 wait 后的已退出进程，门 4 仅按规格口径对 os.replace
注入 PermissionError 验证 bounded 重试）。

| # | 门 | 结果 | 测试锚（本机真实执行） |
|---|----|------|------------------------|
| 1 | single-instance lock（活 pid + 新鲜 heartbeat 拒绝第二 acquire） | PASS | `tests.test_watcher_store.SingleInstanceLockTest`（5 用例） |
| 2 | tmp + replace 原子写（写后内容完整、零 .tmp 残留） | PASS | `tests.test_watcher_store.AtomicWriteTest`（3 用例） |
| 3 | watcher reader / writer 并发（一写多读永不见 partial JSON；读方 decode 错误 bounded reread） | PASS | `tests.test_watcher_store.ReaderWriterConcurrencyTest`（2 用例） |
| 4 | PermissionError 的 bounded handling（有界重试后成功；超限抛 WatcherStoreError） | PASS | `tests.test_watcher_store.PermissionErrorBoundedTest`（3 用例） |
| 5 | stale lock recovery（死 pid / 过期 heartbeat → 接管 generation+1） | PASS | `tests.test_watcher_store.StaleLockRecoveryTest`（4 用例） |

运行证据（2026-09-03 真实执行，退出码均 0）：

```text
python3 -m unittest tests.test_watcher_store tests.test_watcher
→ Ran 48 tests in 0.608s  OK（watcher_store 17 + watcher 31）
python3 -m unittest discover -s tests
→ Ran 1779 tests in 88.905s  OK
python3 scripts/validate_plugin.py
→ 15/15 项通过, 0 项失败
```

本机环境一行：Windows 10 x64（OS build 19042；platform.platform() 报
Windows-10-10.0.19041-SP0）+ Python 3.7.9（sys.executable =
`C:\Program Files\WindowsApps\PythonSoftwareFoundation.Python.3.7_2544.0_x64__qbz5n2kfra8p0\python.exe`，
商店版），os.name=nt；pid 存活判定走 ctypes kernel32
OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION) + GetExitCodeProcess
判 STILL_ACTIVE（零外部依赖，未引入 psutil）。

实施实录补充（本机验证发现的两笔真实 Windows 现象与处置）：

```text
1. 并发读压力下 os.replace 以 WinError 5（目标被读者句柄占用）拒绝：
   默认 3 次 × 0.2s 的有界预算在 torture 级连续读压力下可能耗尽并按
   设计抛 WatcherStoreError（绝不静默吞）。写方预算按「常量可配」
   以参数放宽（attempts / interval）即可在高频读下收敛；门 3 测试
   即以该口径落地（20 次 × 1ms），默认常量口径由门 4 单独锚定。
2. watcher 循环写回丢旗标竞争：CLI stop 在 tick 抓取期间置位
   stop_requested 会被 tick 末尾的整记录覆盖写抹掉（测试实录：循环
   永不退出）。处置：watcher 循环写前重读文件并 OR 合并旗标，窗口
   收窄到合并读之后的微秒级（文件即锁、无 CAS 的固有窗口，已记入
   watcher.py docstring）。
```

---

# 7. Watcher ACTIVE / PASSIVE 模式

禁止无任务 24/7 持续进行模型 priming。

## PASSIVE

条件：

```text
无 active long-horizon continuity demand
```

行为：

```text
低频 quota observation
NO model prime
NO task activation event
```

## ACTIVE

条件至少之一：

```text
active task with auto_resume = auto_once / until_done
waiting_quota task
explicit continuity subscription
```

行为：

```text
adaptive quota polling
boundary tracking
window priming if required
activation event
```

原则：

> **Continuous quota activation is demand-coupled, not permanent.**

manual / notify 默认可以继续观察，但不得自动进行模型 prime，除非未来增加明确用户授权策略。

---

# 8. Window Primer：新增但必须严格隔离

## 8.1 为什么需要 Primer

若 provider 的真实行为是：

```text
旧额度窗口到期
↓
仅 quota query 不产生新的 next reset_at
↓
第一次新模型调用
↓
provider materializes / reveals new rolling window
↓
新的 reset_at 出现
```

则仅 Watcher polling 无法让 quota timeline 连续。

因此新增：

```text
Window Primer
```

其职责只有：

> **用一次最小、独立、可审计的模型调用，使新 quota window materialize，并随后重新查询 quota。**

---

## 8.2 Primer 不属于 Task Resume

新增两个完全不同的概念：

### Control-plane call

```text
WINDOW_PRIME
```

只用于 quota timeline continuity。

它：

```text
NO work-unit resume
NO repo mutation
NO task permit
NO lease
NO auto_once consumption
NO consumed_quota_windows increment
```

单独统计：

```text
prime_attempts
prime_successes
prime_failures
control_plane_model_calls
control_plane_token_usage
last_prime_at
```

### Task-plane call

```text
TASK_RESUME
```

只有新 executable quota epoch 已确认且 task 被授权后才发生。

---

## 8.3 Primer 必须 Feature-Gated + 用户授权

在真实 provider Phase0 未完成前：

```text
primer.enabled = false
```

不得把“最小模型调用一定刷新 reset_at”直接写成 correctness assumption。

新增硬门（2026-09-02 起含第 0 号，全部为硬前置）：

```text
P0-QP-00 / P0-QP-00A → P0-QP-01 .. P0-QP-08
```

全部通过后才能启用。

Primer 是模型调用，不是普通 quota refresh，因此恒要求三重授权条件：

```text
primer.enabled == true
AND auto_resume ∈ {auto_once, until_done}
AND authorization.source == user
```

`manual / notify` 默认不得 prime（未来若放宽须新增显式用户授权策略）。

---

# 9. Primer Phase0 实验

## P0-QP-00：凭证与执行路径可达性（硬前置）

必须先证明：

```text
quota 查询凭证可用
model prime 调用凭证也可用
二者属于预期的同一 Coding Plan / account quota domain
无 secret 落盘 / 日志
primer 请求能通过实际 provider path 发出
```

任何一条不成立 → C4 停在实验阶段，不得伪造 epoch。

## P0-QP-00A：Primer 授权条件（硬前置）

Primer 是模型调用，授权门高于普通 refresh：

```text
primer.enabled == true
AND auto_resume ∈ {auto_once, until_done}
AND authorization.source == user
```

`manual / notify` 不得 prime；PASSIVE 模式零 control-plane model call
（P0-QP-08）继续有效。

## P0-QP-01：纯 quota polling 是否自然产生新 reset_at

目标：

```text
旧 window 到期
不发模型 call
连续 polling
```

确认：

```text
reset_at 是否自然改变
```

---

## P0-QP-02：最小模型 call 是否导致新 boundary materialize

确认：

```text
old boundary Bn
→ minimal call
→ quota refresh
→ boundary Bn+1
```

必须有可重复证据。

---

## P0-QP-03：Prime 是否消费与任务相同的 Coding Plan quota

禁止错误地 prime 另一个 API 计费池。

---

## P0-QP-04：Prime 成本

记录：

```text
request tokens
response tokens
quota percentage delta
latency
```

Prime prompt 必须极短，并要求最小输出。

---

## P0-QP-05：Rolling-window anchor 行为

确定：

```text
next reset_at
```

是否受到 prime 时间影响。

若影响：

> `reset_at` 必须被定义为 provider-observed dynamic boundary，不能再从旧 boundary 推算。

---

## P0-QP-06：Weekly / multi-window blocking

若：

```text
5h available
weekly exhausted
```

Prime 不得错误地产生 `ActivationReady`。

---

## P0-QP-07：Prime 幂等

网络超时 / watcher restart 时，不能对同旧 boundary 连发多个 prime。

---

## P0-QP-08：无 demand 时禁止 Prime

证明 PASSIVE mode 零 control-plane model call。

---

# 10. Quota Epoch：替代 automation fire 作为窗口语义核心

## 10.1 核心定义

真正有意义的事件不再是：

```text
Scheduled Task fired
```

而是：

```text
a new executable quota epoch became available
```

建议：

```json
{
  "provider_identity": "...",
  "quota_epoch": 18,
  "epoch_id": "glm:<fingerprint>",
  "observed_at": "...",
  "provider_status": "AVAILABLE",
  "executable": true,
  "executable_since": "...",
  "windows": [...]
}
```

---

## 10.2 多窗口必须拆两种 boundary

### Probe / observation boundary

用途：

```text
Watcher 何时收紧 polling
何时提前进入 boundary surveillance
```

可以使用：

```text
earliest meaningful reset
= min(reset_at)
```

### Executable boundary

用途：

```text
是否允许 Task Resume
quota-window budget consumption
```

必须使用：

```text
max(reset_at of all blocking EXHAUSTED windows) + grace
```

禁止再混成一个 `wake_at`。

分工细则（2026-09-02 锁定）：现有 `_earliest_reset_plus()` 不是“错误函数应全部
替换”——它继续服务 **probe boundary** 数学（观察收紧 / DRAINING 前置观察 /
Primer surveillance），但 Resume Controller 不得再把它当作真正的
**executable boundary** 消费。

---

# 11. 状态模型修正

建议新增独立控制面状态：

```json
{
  "quota_control_plane": {
    "schema_version": 1,
    "provider_identity_hash": "...",
    "mode": "ACTIVE",
    "watcher": {
      "status": "running",
      "pid": 12345,
      "generation": 2,
      "heartbeat_at": "...",
      "last_poll_at": "...",
      "next_poll_at": "..."
    },
    "quota": {
      "epoch": 18,
      "epoch_id": "...",
      "provider_status": "AVAILABLE",
      "executable": true,
      "observed_at": "...",
      "executable_since": "...",
      "probe_boundary_at": "...",
      "target_executable_at": "...",
      "windows": []
    },
    "primer": {
      "enabled": false,
      "state": "idle",
      "previous_epoch_id": "...",
      "attempt_key": "...",
      "last_attempt_at": "...",
      "last_success_at": null
    }
  }
}
```

控制面 truth 建议 repo-local 第一版：

```text
.glm-conductor/runtime/quota-control.json
```

必须 atomic replace。

如果后续支持多个 repo 共用一个 Coding Plan，再迁移为 provider/account scoped global store；不在本阶段引入。

---

# 12. Primer 幂等状态机

冻结：

```text
NOT_REQUIRED
PRIME_PENDING
PRIME_IN_FLIGHT
PRIME_SUCCEEDED
PRIME_FAILED
NEW_EPOCH_CONFIRMED
```

幂等 key：

```text
provider_identity_hash
+
previous_epoch_id
```

同一个旧 epoch：

```text
最多一个 active prime attempt
```

只有观测到：

```text
epoch_id changed
```

或：

```text
provider window fingerprint changed
```

才允许下一轮 prime。

---

# 13. Quota Control Plane 事件

新增 journal / event vocabulary：

```text
quota_watcher_started
quota_watcher_stopped
quota_watcher_failed
quota_snapshot_changed
quota_probe_boundary_changed
quota_executable_changed
quota_epoch_advanced
quota_prime_required
quota_prime_started
quota_prime_succeeded
quota_prime_failed
activation_ready
activation_consumed
```

降噪：

不得每次 polling 都写事件。

只在：

```text
status change
boundary change
epoch change
executable change
prime lifecycle change
```

时落账。

---

# 14. Execution Plane：Task 只订阅 quota，不再拥有 quota clock

新增概念：

```text
Quota Subscription
```

建议 task state：

```json
{
  "quota_subscription": {
    "enabled": true,
    "registered_epoch": 17,
    "minimum_state": "AVAILABLE",
    "continuation_mode": "until_done",
    "last_activation_epoch": 17
  }
}
```

Watcher 不需要知道 Task Graph。

Execution Plane 负责：

```text
Quota epoch 18 available
        ↓
find waiting/eligible task
        ↓
authorization
        ↓
priority
        ↓
dependency
        ↓
permit / lease
        ↓
reconcile
        ↓
TASK_RESUME
```

---

# 15. DRAINING / Handoff 机械协议

保留当前 M4 DRAINING gate，并把跨窗口交接正式固化为：

```text
NORMAL
  ↓
PRESSURE
  ↓
DRAINING
  ↓
stop creating wide implementation work
  ↓
finish current atomic safe unit
  ↓
verification / review as required
  ↓
write checkpoint
  ↓
refresh Resume Manifest
  ↓
release execution permit / lease if safe
  ↓
task → waiting_quota
  ↓
register quota subscription
```

新窗口：

```text
Quota Control Plane:
quota_epoch advanced
+
executable = true
        ↓
Activation Transport gives Session execution opportunity
        ↓
Execution Plane validates:
new epoch?
authorized?
task active?
budget?
        ↓
reconcile
        ↓
resume next safe work unit
```

任务交接不再依赖 Wake Prompt 临场推理。

## 15.1 消费事务点冻结（2026-09-02，不得留给实施 Agent 解释）

`quota_boundary_consumed` 的唯一合法写入点定义为：

> 新 executable epoch 已确认，authorization / window budget / reconcile 均通过，
> 并且 Resume Controller 成功接受该 epoch、准备把任务从 `waiting_quota` 恢复为
> 执行态的那个 commit point。

```text
quota executable
    ↓
new epoch
    ↓
authorization OK
    ↓
budget OK
    ↓
reconcile OK
    ↓
Resume Controller commit
    ↓
quota_boundary_consumed
    ↓
waiting_quota → executing / next safe continuation
```

以下位置一律**不**消费：

```text
CronCreate
Cron fire
force-refresh
primer call
first arbitrary model token
first Agent worker dispatch
```

`quota_boundary_consumed` 必须是幂等证据；journal / state 出现部分写入时，
恢复逻辑必须能通过该事件修正 `consumed_quota_windows` 投影，绝不能对同一
epoch 二次消费。

---

# 16. Activation Transport：新增正式抽象

这是本次架构修正的关键。

Quota Watcher 能知道：

```text
08:35:51 quota available
```

但它本身不能保证：

```text
08:35:51 dormant ZCode session receives a new turn
```

因此必须单独抽象：

```text
Activation Transport
```

接口概念：

```python
transport.arm(...)
transport.status(...)
transport.wait_or_fire(...)
transport.consume_activation(...)
```

Transport 不判断 task 是否允许 resume，只负责产生 Session execution opportunity。

---

# 17. Transport A：Persistent Recurring Bridge `[STABLE]`

保留 M5 当前主路径。

```text
one tracked task
→ one persistent recurring Scheduled Task
```

间隔口径（2026-09-02 锁定）：

```text
stable code default = 60 min（D15-d 保守默认）
dogfood / 显式用户配置允许 bridge_interval_minutes = 30
```

SCHED-04 已证 30min recurring 多次 same-session trigger 稳定，但尚未证明
fully-exhausted 期间 repeated trigger 的 automation 存活（SCHED-10）与
previous run pending 时的 overlap 行为（SCHED-06）。以下两项实验通过后，再
单独裁决是否 default 60 → 30：

```text
SCHED-10 exhausted-period recurring survival
+
SCHED-06 previous-run overlap behavior
```

不得把“30 分钟已经实测能工作”与“30 分钟已经适合作为项目默认值”混为一件事。

其职责从：

```text
“在 reset_at 精确唤醒”
```

修正为：

> **“提供 bounded-latency same-session execution opportunity。”**

理论延迟：

```text
0 <= resume_lag < bridge_interval + host_jitter
```

30min 实测条件下：

```text
worst-case ≈ 30min + host jitter
```

因此对外不得称：

```text
precise reset-boundary wake
```

应称：

```text
bounded-latency recurring activation
```

---

# 18. Transport B：Probe-Then-Hold `[EXPERIMENTAL]`

Watcher 与该模式天然配套。

流程：

```text
Recurring Bridge fires
        ↓
read Quota Control Plane
        ↓
quota not yet executable
AND expected boundary within HOLD_WINDOW
        ↓
run foreground:
quota-wait --after-epoch <N>
        ↓
tool process remains pending
        ↓
Watcher advances epoch
        ↓
quota-wait returns
        ↓
same active Agent turn continues
        ↓
TASK_RESUME
```

优点：

```text
no CronUpdate
no nested CronCreate
no OS→new-user-turn injection
no model polling loop
```

如果成立，可把 30min bridge 的 post-reset lag 降到 Watcher polling 粒度。

建议：

```text
HOLD_WINDOW = 35 min
watcher blocked polling = 30–60 sec
```

---

# 19. Probe-Then-Hold Phase0

新增：

```text
P0-HOLD-00 scheduled-owned unattended turn 的 quota-wait 权限路径验证
P0-HOLD-01 scheduled run 中 foreground tool 能否 pending 20–40 min
P0-HOLD-02 tool/run hard timeout
P0-HOLD-03 pending tool return 后是否继续同一 Agent turn
P0-HOLD-04 pending 期间 quota exhausted→available 后能否继续模型 step
P0-HOLD-05 recurring trigger 与 pending previous run 的 overlap 行为
P0-HOLD-06 Stop / app close / sleep 的 waiter cleanup
P0-HOLD-07 fully exhausted 状态下 scheduled task 是否能进入到启动 waiter
P0-HOLD-08 reset_at 漂移时 watcher event 是否正确解锁
```

P0-HOLD-00 是硬前置：必须先证明 `quota-wait` 不会被 Bash / tool policy 拦截，
再做 20–40 分钟 hold 测试——否则 P0-HOLD-01 的失败没有诊断价值（权限拦截与
能力缺失无法区分，会污染实验结论）。

全部通过前保持 experimental。

---

# 20. Transport C：Self-Retiming `[FUTURE]`

只有：

```text
CronUpdate / edit parent schedule
```

在 scheduled-owned session 被稳定验证后才启用。

流程：

```text
Watcher target_executable_at
        ↓
update same automation
        ↓
same automation next fire exact boundary
```

当前：

```text
NOT correctness path
```

---

# 21. Transport D：Session Injector `[EXPERIMENTAL]`

社区 reverse-engineered ZCode app-server / ACP bridge 已显示：

```text
session/resume(existing_session_id)
session/prompt(...)
```

技术上存在外部进程重新挂载既有 ZCode session 并发送 prompt 的可能。

如果 Phase0 证明可安全使用，可形成：

```text
Watcher detects epoch
        ↓
ActivationReady
        ↓
session/resume(existing_session_id)
        ↓
session/prompt(GLM_CONDUCTOR_CONTINUE)
        ↓
same logical ZCode session
```

但当前不是官方稳定插件 API，因此：

```text
MUST remain experimental
MUST be version-gated
MUST fail back to recurring bridge
MUST avoid concurrent ownership of one active turn
```

不得作为 v2.2 stable 必需依赖。

---

# 22. M5 当前实现必须进行的语义修复

## 22.1 废弃 Persistent Path 上的 `record_quota_wake` 旧语义

当前 v2.1 遗留逻辑：

```text
CronCreate success
→ record_quota_wake
→ consumed_quota_windows += 1
```

Persistent Bridge 下这是错误的。

立即修正：

```text
bridge creation
!= quota window consumption
```

新增：

```python
record_quota_boundary_consumed(
    repo_root,
    task_id,
    *,
    epoch_id,
    boundary_id,
    resume_started_at
)
```

幂等 key：

```text
task_id + epoch_id
```

只有：

```text
new executable quota epoch confirmed
AND authorized TASK_RESUME actually begins
```

才消费。

以下全部不消费：

```text
bridge fire
bridge create
bridge arm
bridge no-op
still exhausted
weekly blocked
provider unavailable
manual/notify warm-only
primer model call
repeated recurring probe
```

`record_quota_wake()` 可：

1. 标记 deprecated；
2. 仅保留 v2.1 legacy compatibility；
3. v2.2 persistent path 禁止调用。

---

## 22.2 `arm_wake_bridge()` 不得再串联 wake-record

删除/修正文档与调用纪律：

```text
arm_wake_bridge
→ only bridge lifecycle accounting
```

不得：

```text
arm bridge
→ consume quota window
```

---

## 22.3 `retarget_wake_bridge()` 改名或改义

当前 recurring 模式并没有实际调用 CronUpdate，因此它只是：

```text
logical boundary bookkeeping
```

建议：

```python
retarget_logical_boundary(...)
```

未来真实 CronUpdate 路径：

```python
retarget_physical_schedule(...)
```

不得用一个 `retarget` 名称混淆两者。

兼容期：

```text
retarget_wake_bridge = deprecated alias of retarget_logical_boundary
```

---

## 22.4 拆分 `next_wake_at`

旧字段：

```text
next_wake_at
```

同时承担 host schedule 与 provider target，语义不清。

改成：

```text
host_next_run_at
desired_activation_at
```

其中：

```text
host_next_run_at
= CronList/CronCreate observed host fact

desired_activation_at
= provider-derived executable boundary
```

## 22.5 手动 / 历史 Bridge 对账（2026-09-02 锁定）

原则：

```text
journal / history alone MUST NOT manufacture an active bridge
```

只有 current host evidence（CronList 等宿主事实）确认 automation active 时，
才允许 `wake_bridge.status = armed`。已知已删除 / completed 的 bridge 记：

```text
status = cancelled / stale
保留 automation_id / last fired / last known host state
不得显示为 active transport
```

若 bridge 已删除而 task 仍 active：

```text
activation transport = missing
```

后续由当前 session capability 决定：clean interactive session → 可重新 arm；
scheduled-owned / 不可用 → degraded / fallback（SessionStart 兜底）。

实施分工：C1 只提供 migration / reconciliation helper（纯账本对账，**不调用
CronList**）；真正的 host adapter 归 C6。

## 22.6 存量记账迁移（保守规则，2026-09-02 锁定）

目标语义不变（automation lifecycle ≠ quota-window consumption；仅新
executable epoch 被确认 AND authorized resume 实际开始才 +1），但迁移冻结
以下规则。

**不自动退款。** legacy `consumed_quota_windows` 即使是 D14 时代的 arm-time
debit，也不得因 C1 上线自动下调——自动退款等于无用户重新授权地增加未来
自动续跑预算。因此：

```text
new_consumed >= legacy_consumed    （迁移安全不变量）
```

**用 journal 做“归属”，不是退款：**

```text
legacy_consumed     = state 当前值
verified_consumptions = journal 中可证明真实跨 boundary resume 的
                        unique quota_boundary_consumed 事件数
new_consumed        = max(legacy_consumed, count(verified_consumptions))
```

迁移写一次 `quota_accounting_migrated` 事件，至少记录：

```text
legacy_consumed
verified_boundary_ids
attributed_consumed
legacy_unattributed_consumed
migrated_at
```

其中：

```text
legacy_unattributed_consumed
= legacy_consumed − 可归属到真实 resume 的旧 debit
```

它仍然算已消费，不自动返还。以后若需扩大预算，只能走正常 authorization
路径（用户重新授权），不得偷偷修历史数字。

当前账本已有的一条人工 `quota_boundary_consumed` 证据（2026-09-01T22:20Z，
boundary `five_hour:2026-09-01T21:59:00Z`）应当用于把现有 consumed=1 正式
归属到真实跨窗恢复；**数字保持 1，不再重复 +1**。

---

# 23. Universal Wake 修正

当前 Universal Wake 主结构保留。

新第一阶段：

```text
1. load task state
2. load Quota Control Plane state
3. if watcher state stale/unhealthy:
      controlled provider refresh fallback
4. inspect epoch/executable
5. task completed → tombstone no-op
6. manual/notify → warm-only
7. auto_once/until_done:
      require new executable epoch
      require user authorization
      require budget
      reconcile
      consume epoch only when resume starts
```

仍保留硬红线：

```text
DO NOT CREATE A NEW SCHEDULED TASK FROM THIS WAKE SESSION.
```

---

# 24. M9 Recovery Plane 扩展

M9 已完成，不重写。

只增加 manifest projection：

```json
{
  "quota_control": {
    "epoch_id": "...",
    "executable": false,
    "provider_status": "EXHAUSTED",
    "probe_boundary_at": "...",
    "desired_activation_at": "...",
    "watcher_status": "running",
    "activation_transport": "recurring_bridge",
    "host_next_run_at": "..."
  }
}
```

Manifest 是恢复投影，不是 Quota Control Plane truth source。

---

# 25. Watcher 健康度与 Session fallback

Watcher status：

```text
stopped
starting
running
stale
failed
```

若：

```text
watcher stale/failed
```

不得直接声称 quota unavailable。

Session 可执行一次：

```text
resolver force refresh
```

但：

```text
Session fallback refresh
```

不能替代实时 Watcher。

若 task 是 `until_done` 且 Watcher 不健康：

```text
continuity.health = degraded
reason = quota_watcher_unavailable
```

Persistent Recurring Bridge 仍保留作为 SessionStart / periodic fallback。

---

# 26. 新实施顺序

不要继续直接按旧 M6→M10 原样执行。

采用以下修正序列（2026-09-02 锁定，含 C1a/C1b 拆分与骨架排序）：

```text
C0 → C1a → C2 → C1b → C3 watcher skeleton → P0-QP → C4 primer（仅实验通过后）→ C5+
```

旧 wu-22-06/07/08/10/11/12 保留历史状态，不再按原规格实施；新工作全部从
C 序列建立，避免同一编号同时承载旧架构与新架构语义。

---

## C0：冻结本架构修正（2026-09-02 已批准；纯 docs commit）

新增本文件并在旧 master plan 顶部引用：

```text
conflict precedence:
this correction > old v2.2 quota continuity sections
```

不删除旧文档，保留历史设计证据。本 commit 必须同时写入 13 项锁定裁决：

1. 本修正文档优先级；
2. Watcher = skeleton-first（§6.1 第一阶段范围）；
3. `primer.enabled=false` 示例修复（§11）；
4. P0-QP-00 / P0-QP-00A（§9）；
5. P0-HOLD-00（§19）；
6. P0-WATCH-00（§6.2）；
7. bridge 默认 60、30 仅为 dogfood override（§17）；
8. SCHED-03 intermittent Delete 证据（§2.2 补记 + Phase0 回填）；
9. C1 拆 C1a / C1b（本节）；
10. conservative accounting migration（§22.6）；
11. manual bridge reconciliation（§22.5）；
12. `resume started` commit point（§15.1）；
13. probe boundary 与 executable boundary 分工（§10.2）。

---

## C1a：切断旧消费语义 `[P0，先于 C2]`

目标：

```text
automation lifecycle
≠
quota epoch consumption
```

实施（不等 epoch 模型——立即止血）：

- Persistent path 停止调用 `record_quota_wake`；
- 删除 arm 后串联 wake-record 的文档纪律（`plan_wake_bridge` /
  `arm_wake_bridge` docstring 时序、v2.1 `quota_wake_prompt` 文本、CLI 指引）；
- `record_quota_wake()` 标记 legacy / deprecated（v2.1 compatibility 保留，
  v2.2 persistent path 禁止调用）；
- bridge arm / fire / create 一律不改变 `consumed_quota_windows`；
- 更新 CLI / docs / tests。

验收：

```text
10 次 recurring fire
0 次成功 resume
→ consumed_quota_windows 不变
```

（同时覆盖 arm/create 路径：arm 也不消费。）

---

## C1b：resume-time consumption + 保守迁移 `[P0，在 C2 之后]`

前置：C2 已建立 `epoch_id / executable boundary`——避免在 C1 里临时再造一个
过渡版错误 boundary identity（依赖倒置）。

实施：

- 新增 `record_quota_boundary_consumed`：幂等优先 `task_id + epoch_id`；
- 最终 `quota_boundary_consumed` 事件同时携带 `epoch_id` 与
  `executable_boundary_id`；legacy migration 才使用 `boundary_id` alias；
- 消费点 = §15.1 冻结的 Resume Controller commit point；
- §22.6 保守迁移（含一次性 `quota_accounting_migrated` 事件）；
- `auto_once` = 一次成功的跨 executable epoch resume；
- §22.5 手动桥对账 helper（纯账本，不调用 CronList）。

---

## C2：Quota Epoch / Boundary Model `[P0]`

实现：

```text
epoch.py
probe boundary
executable boundary
blocking-window canonicalization
epoch fingerprint
```

测试：

- single 5h；
- 5h + weekly；
- reset drift；
- repeated same snapshot；
- new boundary；
- unknown reset；
- provider status flip。

---

## C3：Real-Time Quota Watcher `[P0，skeleton-first]`

第一阶段只实现 §6.1 冻结的最小骨架（独立进程 / 单实例锁 / provider-only
polling / ACTIVE-PASSIVE / dynamic reset_at / durable state / heartbeat /
atomic persistence / start-status-stop-once CLI）；P0-WATCH-00（§6.2）为
Windows 前置硬门。Primer、task subscription、ActivationReady→Session 激活、
event bus 深度全部后置到 C4 / C5 及之后。

复用：

```text
observer.py
resolver/provider adapters
```

不得复制 parser/scheduler 逻辑。

---

## C4：Window Primer Phase0 + implementation `[P0 experimental gate]`

硬前置（2026-09-02）：P0-QP-00 / P0-QP-00A（§9）通过，且 §8.3 授权三条件
恒成立。先实验，再编码正式路径。

Primer 失败时：

```text
do not fabricate epoch
remain blocked
keep polling
```

不得因为 prime call 成功返回 HTTP 200 就直接 `AVAILABLE`。

必须 quota refresh 二次确认。

---

## C5：Execution Plane Quota Subscription `[P0]`

新增：

```text
quota_subscription
last_activation_epoch
```

并把：

```text
waiting_quota
```

恢复 eligibility 改由新 epoch 驱动。

---

## C6：Activation Transport Adapter `[P0]`

重写旧 M6 定位：

旧：

```text
Scheduler Capability & Bridge Lifecycle Adapter
```

新：

```text
Activation Transport & Scheduler Capability Adapter
```

必须支持至少：

```text
recurring_bridge
```

接口预留：

```text
probe_then_hold
self_retiming
session_injector
```

---

## C7：Resume Controller 修正 `[P0]`

重写旧 M8：

TASK_RESUME 前必须：

```text
new executable epoch
authorization
window budget
task active
manifest/repo reconcile
permit/lease
```

随后：

```text
record quota boundary consumed
```

消费点必须与“resume started”事务边界一致。

---

## C8：Stop Gate / Continuity Health `[P0]`

旧 M7 继续做，但增加：

```text
quota watcher health
quota subscription
activation transport armed
```

DRAINING Stop Gate 不要求：

```text
exact provider wake_at scheduled
```

而要求：

```text
handoff durable
+
quota subscription registered
+
at least one valid activation transport
```

---

## C9：Probe-Then-Hold Phase0 `[Experimental]`

若通过，加入：

```text
precision_activation = hold_open
```

不影响 recurring bridge stable fallback。

---

## C10：Session Injector Research `[Experimental]`

独立 feature flag：

```text
precision_activation = session_injector
```

不得阻塞 v2.2 stable。

---

## C11：Dogfood / Release

完成真实：

```text
>= 2 quota epochs
```

长任务。

---

# 27. 新测试矩阵

## QC-01

```text
Watcher PASSIVE
→ no prime
```

## QC-02

```text
ACTIVE + quota AVAILABLE
→ poll
→ no unnecessary prime
```

## QC-03

```text
EXHAUSTED + known boundary
→ watcher tightens polling
```

## QC-04

```text
boundary reached
→ prime required
→ prime once
→ refresh
→ new epoch confirmed
```

## QC-05

```text
prime succeeds
but weekly still EXHAUSTED
→ executable=false
→ no ActivationReady
```

## QC-06

```text
same old epoch watcher restart
→ no duplicate prime
```

## QC-07

```text
epoch advances
→ exactly one quota_epoch_advanced
```

## QC-08

```text
10 recurring bridge fires
quota never executable
→ consumed windows unchanged
```

## QC-09

```text
new executable epoch
+ until_done authorized
+ actual resume begins
→ consumed += 1
```

## QC-10

```text
same epoch duplicate wake
→ no second consumption
```

## QC-11

```text
manual/notify
→ warm-only
→ no window consumption
```

## QC-12

```text
primer model call
→ no task window consumption
```

---

# 28. 新真实 Dogfood Gates

## RG-QC-01：Real-Time Quota Observation

在 Session 无 Agent turn 的情况下：

```text
Watcher 连续观察 provider
```

证明确实能更新 quota-control state。

---

## RG-QC-02：Dynamic Reset Drift

跨两个真实窗口：

```text
reset N
reset N+1
reset N+2
```

不得假设固定 5h。

---

## RG-QC-03：Window Prime

如果 P0 证明需要 prime：

```text
boundary reached
→ one minimal prime
→ new reset_at observed
```

---

## RG-QC-04：Persistent Recurring Stable Resume

```text
Watcher epoch advances
→ next recurring same-session turn
→ consume watcher event
→ resume
```

用户无需输入“继续”。

---

## RG-QC-05：Multi-Window Until-Done

至少：

```text
epoch N
→ exhaustion
→ epoch N+1
→ resume
→ exhaustion
→ epoch N+2
→ resume
```

全程：

```text
NO nested Scheduled Task create
same persistent automation identity
```

---

## RG-QC-06：Accounting

确认：

```text
automation runCount
!=
consumed_quota_windows
```

---

## RG-QC-07：Watcher Failure

人为杀死 watcher：

```text
task/repo 不损坏
recurring bridge 仍能安全 no-op/fallback
SessionStart 仍可恢复
```

---

## RG-QC-08：Probe-Then-Hold

仅实验模式：

```text
bridge pre-boundary enters
→ quota-wait pending
→ watcher detects epoch
→ same turn continues
```

若失败：

```text
feature disabled
stable recurring path unaffected
```

---

# 29. Release Acceptance

v2.2 stable 至少满足：

1. M1/M1a/M2/M3/M4/M5/M9 既有测试不回退；
2. `observer.py` 继续保持 pure/lazy；
3. Watcher 可独立实时 polling；
4. Watcher state durable、single-instance；
5. provider `reset_at` 动态跟踪，不固定 +5h；
6. multi-window blocking 使用 `max(blocking reset)` 判断 executable；
7. Prime 若启用，必须经过 Phase0 证明；
8. Prime 绝不计入 task quota-window budget；
9. bridge arm/fire 不再消费 quota window；
10. quota window 只在新 executable epoch 的 authorized resume 实际开始时消费；
11. Persistent Recurring Bridge 继续是 stable activation transport；
12. Session wake 不创建 nested Scheduled Task；
13. recurring bridge 不被错误描述为 precise boundary wake；
14. watcher failure 有 degraded/fallback；
15. real dogfood 跨至少两个 quota epochs；
16. full suite green。

以下不作为 stable release blocker：

```text
probe_then_hold
session_injector
self_retiming
```

它们属于 precision enhancement。

---

# 30. 对旧 v2.2 文档的具体修正关系

## 旧 §3.1 Quota Heartbeat

原：

```text
local runtime
no daemon
```

修正：

- Session-local `observer.py` 仍然 no daemon；
- 新增独立 Quota Watcher 可以是本地长运行进程；
- 禁止的是 background **model loop**，不是 provider-only control-plane watcher。

---

## 旧 §3.3 “provider natural reset”

修正为：

```text
provider boundary observed
→ if necessary Window Primer
→ quota refresh
→ new epoch confirmed
```

不得提前假设 reset 一定会自然 materialize 新 `reset_at`。

---

## 旧 §6 Adaptive Quota Heartbeat

保留作为 observer math。

新增：

```text
Watcher = actual timed execution of the observer policy
```

---

## 旧 §7 / §9 Wake Bridge

保留 Persistent Bridge identity。

修正：

```text
Bridge = Activation Transport
NOT Quota Clock
```

---

## 旧 §11 Resume

增加：

```text
new executable quota epoch
```

作为 resume 的硬前置条件。

---

## 旧 §16 Observer

完全保留 pure contract。

新 Watcher 不替代 Observer。

---

## 旧 §22 wake-plan / wake-status

`wake-plan` 在 recurring 模式下不得把 logical `reset_at + grace` 描述为宿主真实 next run。

输出建议改为：

```json
{
  "transport": "recurring_bridge",
  "host_interval_minutes": 30,
  "host_next_run_at": "...",
  "desired_activation_at": "...",
  "logical_boundary_id": "..."
}
```

---

## 旧 §31 “不做 daemon”

修改为：

```text
不做 background model heartbeat daemon
```

允许：

```text
provider-only Quota Watcher local service
```

---

# 31. 最终状态对象建议

```json
{
  "quota_control_plane": {
    "watcher_status": "running",
    "mode": "ACTIVE",
    "epoch": 18,
    "epoch_id": "glm:...",
    "provider_status": "AVAILABLE",
    "executable": true,
    "probe_boundary_at": "...",
    "desired_activation_at": "..."
  },

  "continuation": {
    "obligation": "armed",
    "wake_bridge": {
      "status": "armed",
      "mode": "recurring",
      "automation_id": "automation-...",
      "host_next_run_at": "...",
      "bridge_interval_minutes": 60,
      "logical_boundary_id": "..."
    }
  },

  "quota_subscription": {
    "enabled": true,
    "registered_epoch": 17,
    "last_activation_epoch": 17,
    "minimum_state": "AVAILABLE"
  }
}
```

三组状态必须区分：

```text
Quota Control Plane truth
Activation Transport truth
Task continuity truth
```

---

# 32. 最终流程

```text
Task starts
    ↓
user authorizes until_done
    ↓
eager-arm ONE Persistent Recurring Bridge
    ↓
register quota subscription
    ↓
start / activate Quota Watcher
    ↓
NORMAL execution
    ↓
PRESSURE
    ↓
DRAINING
    ↓
mechanical checkpoint + manifest + handoff
    ↓
waiting_quota
    ↓

[Session can be dormant]

Quota Watcher keeps polling
    ↓
blocking reset boundary approaches
    ↓
if required: minimal Window Prime
    ↓
force provider quota refresh
    ↓
new executable quota epoch confirmed
    ↓
ActivationReady persisted
    ↓

Stable:
next Persistent Recurring Bridge trigger
    ↓
same ZCode session
    ↓
read ActivationReady
    ↓
authorized?
budget?
task active?
    ↓
reconcile
    ↓
TASK_RESUME
    ↓
record task_id + epoch_id consumption
    ↓
continue task
```

Experimental precision path：

```text
Recurring Bridge
→ quota-wait pending
→ Watcher epoch event
→ same turn continues
```

或未来：

```text
Watcher
→ session injector
→ same session prompt
```

---

# 33. 对 Agent 的实施纪律

执行本计划的 Agent 必须遵守：

1. **只在 `v2-dev` 上实施。**
2. 每个 C milestone 独立 commit，不做大爆炸重构。
3. 不破坏当前 1665-test baseline。
4. 先切断旧记账（C1a），再建 epoch 模型（C2），再落新消费 API（C1b），最后
   Watcher；不能在错误的 quota-window 记账语义上继续扩展。C1a→C2→C1b 顺序
   不得倒置。
5. 不把 `observer.py` 改成有 I/O 的 service。
6. 不复制 provider parser / scheduler / resolver。
7. Primer 未经过 P0-QP-00 / 00A / 01..08 真实验证前必须默认关闭。
8. 不把 recurring bridge 描述为精确 quota boundary scheduler。
9. 不重试 CronUpdate / CronDelete glitch。
10. scheduled-owned Session 绝不 CronCreate。
11. experimental transport 全部 feature flag + stable fallback。
12. 所有 quota event 必须幂等。
13. 所有 model prime 必须单独审计，不能隐藏成普通 refresh。
14. repo / state / manifest 的 truth hierarchy 继续遵守现有 v2.1/v2.2 规则。
15. 每个 milestone 完成后先 full test，再进入下一阶段。
16. Watcher subprocess 一律使用 `sys.executable`（本机 plain `python` / `py`
    启动器损坏）；用户 CLI 文档仍写 `python3`；第一版不引入 Windows service。
17. 存量记账迁移不得自动退款（§22.6：`new_consumed >= legacy_consumed`）；
    消费点不得偏离 §15.1 的 Resume Controller commit point。
18. journal / history 单独存在不得制造 active bridge（§22.5）；C1 对账 helper
    不调用 CronList，host adapter 归 C6。

---

# 34. 推荐 Commit / Milestone 命名

```text
docs(v2.2): adopt quota control plane architecture correction

fix(v2.2): C1a cut legacy arm-time window consumption on persistent path

feat(v2.2): add quota epoch and executable-boundary model

feat(v2.2): C1b resume-time boundary consumption + conservative accounting migration

feat(v2.2): add realtime quota watcher skeleton (P0-WATCH-00 gated)

test(v2.2): verify window-primer provider semantics (P0-QP-00/00A first)

feat(v2.2): add guarded quota window primer

feat(v2.2): add quota subscription and epoch-driven resume eligibility

feat(v2.2): introduce activation transport abstraction

feat(v2.2): update resume controller for epoch-based consumption

feat(v2.2): quota control plane dogfood and hardening
```

---

# 35. 最终架构结论

v2.1：

> **中断后仍有状态和原语可以恢复。**

原 v2.2：

> **提前建立 Persistent Wake Bridge，使未来额度窗口仍有 Session 入口。**

本修正后的 v2.2：

> **Provider quota 由独立 Quota Control Plane 连续维护；任务执行由 GLM-Conductor Execution Plane 机械交接；ZCode Scheduled Task 被重新定位为 Activation Transport，而不是 quota scheduler。**

最重要的四条边界：

```text
Provider Clock != Host Clock
Quota Watcher != Task Scheduler
Automation Fire != Quota Epoch
Quota Epoch != Session Activation
```

Stable 主路径：

```text
Real-Time Quota Watcher
+
Optional Window Primer
+
Quota Epoch
+
Persistent Recurring Bridge
+
Mechanical Handoff / Resume
```

目标能力：

> **在不依赖用户再次输入“继续”的前提下，让长程任务跨真实动态 quota windows 持续推进，并对“额度何时真正恢复”与“Session 何时获得执行机会”分别建立可验证、可降级、可审计的控制面。**

Precision enhancement：

```text
probe_then_hold
session_injector
self_retiming
```

只有在对应 Phase0 实证通过后，才允许把系统能力从：

```text
bounded-latency same-session activation
```

提升为：

```text
near-real-time / precise quota-driven same-session activation
```
