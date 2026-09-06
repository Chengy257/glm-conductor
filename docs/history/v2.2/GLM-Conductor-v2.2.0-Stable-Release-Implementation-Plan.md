# GLM Conductor v2.2.0 Stable Release：最终收口与 Agent 实施计划

> **文档类型**：Stable Release Finalization / Agent Implementation Plan  
> **目标版本**：v2.2.0 stable  
> **目标分支**：`v2-dev` → `main`  
> **仓库**：`Chengy257/glm-conductor`  
> **审查基线**：`v2-dev@b149705ec5969de73c83ae17588537f503c4f816`  
> **main 基线**：`main@0cddbf3a68b49d2c73c298f5945cc9e42ad77f6a`（v2.1.0 stable）  
> **状态**：Release Candidate / stable 前最终收口  
> **核心目标**：不再扩展架构；完成真实双 quota epoch 闭环、权威文档收口、版本发布面同步、main 集成与 stable release。  
> **最高原则**：**RH-06 真实双 epoch 自动续跑未通过前，不得标记 v2.2.0 stable。**

---

## 0. 文档目的

当前 `v2-dev` 已完成 v2.2 的主体架构与 release-hardening：

- v2.1 的 dispatch permit / wave / lease / provenance / review receipt / crash recovery；
- v2.2 双层 quota 模型；
- Quota Epoch；
- Real-Time Quota Watcher；
- Window Primer；
- quota subscription；
- resume commit-point quota consumption；
- Activation Transport；
- Persistent Recurring Bridge；
- Stop Gate continuity health；
- RH-01 Primer single-attempt；
- RH-02 Primer mechanical authorization；
- RH-03 quota consumption crash consistency；
- RH-04 boundary evidence cleanup；
- RH-05 CI closure。

因此，本文件**不是新的架构升级计划**，也不是 v2.3 设计。

本文件只回答一个问题：

> **从当前 `v2-dev@b149705` 出发，还需要完成哪些最少且必要的工作，才能可信地发布 `v2.2.0 stable`？**

最终路径冻结为：

```text
ST-00 Release baseline freeze
        ↓
ST-01 RH-06 real dual-epoch dogfood
        ↓
ST-02 Architecture / evidence truth-source closure
        ↓
ST-03 v2.2.0 release metadata atomic bump
        ↓
ST-04 Final verification + independent stable review
        ↓
ST-05 v2-dev → main integration
        ↓
ST-06 post-merge CI + v2.2.0 GitHub Release
```

除非 ST-01 暴露真实 runtime 缺陷，否则**不得再新增新的 control-plane mechanism**。

---

# 1. 当前仓库事实基线

## 1.1 当前分支状态

当前：

```text
v2-dev = b149705ec5969de73c83ae17588537f503c4f816
main   = 0cddbf3a68b49d2c73c298f5945cc9e42ad77f6a
```

`v2-dev` 最新提交是 RH-05 post-push CI closure；仓库中尚无 RH-06 / RG-22-06 的完成提交。

GitHub compare 当前为：

```text
status    = diverged
ahead_by  = 40
behind_by = 5
merge_base = 857f55a1c9041c7303d26fb2454cb2f1aae432d9
```

这里的 divergence 主要来自 v2.1 stable 的 release/merge 历史拓扑；`main` 的 v2.1 stable tree 与 `857f55a` release tree 一致，因此 stable 集成应走 **PR / merge**，不得 force-update `main`。

---

## 1.2 当前 CI

当前 `v2-dev@b149705` 的最新 GitHub Actions 已通过：

```text
ubuntu-latest  × Python 3.8   PASS
ubuntu-latest  × Python 3.13  PASS
windows-latest × Python 3.8   PASS
windows-latest × Python 3.13  PASS
```

每路执行：

```text
python scripts/validate_plugin.py
python -m unittest discover -s tests -v
```

当前 release-hardening 本地记录：

```text
2113 tests green
validator 15/15
```

因此：

> **测试和 CI 当前不是 stable blocker。**

---

## 1.3 RH-01～RH-05 状态

以下工作已完成，不得在 stable 实施中重新设计：

| 项目 | 状态 | stable 阶段处置 |
|---|---|---|
| RH-01 Primer single-attempt | 已完成 | 冻结 |
| RH-02 Primer mechanical authorization | 已完成 | 冻结 |
| RH-03 quota consumption crash consistency | 已完成 | 冻结 |
| RH-04 boundary evidence cleanup | 已完成 | 冻结 |
| RH-05 CI closure | 已完成 | 冻结 |
| RH-06 real dual-epoch dogfood | **未完成** | **唯一 runtime release blocker** |
| RH-07 stable release decision | 未完成 | 本文件 ST-04～ST-06 |

---

# 2. Stable 前问题重新核查

## 2.1 P1 — 唯一 runtime blocker：RH-06 尚未真实闭环

现有 dogfood 已证明：

- 多个真实 provider reset boundary；
- Watcher 可以常驻观测；
- DRAINING / PRESSURE 真实触发；
- recurring Scheduled Task 可重复进入同一 Session；
- v2.1 时代 persistent bridge 曾跨窗口继续；
- checkpoint / SessionStart 跨会话恢复可用；
- C-series 的 subscription / activation / consumption / idempotence 有完整单测。

但是尚未证明以下新链条在**同一真实任务**中连续跨两个新 executable epoch：

```text
C-series corrected accounting
+
persistent recurring bridge
+
quota subscription
+
new epoch resume
+
corrected activation/consumption ledger
```

已有记录明确承认：

```text
修正记账口径下的活跑未发生
```

因此：

> **“已经观测过多个 quota epoch”不能替代“新 C-series 自动续跑闭环已经跨两个 epoch”。**

这是 stable 发布前必须补齐的唯一 P1 release gate。

---

## 2.2 P2 — `docs/architecture.md` 不是当前实现的可靠唯一真相源

当前文件顶部声明：

```text
本文档是 GLM Conductor 的唯一架构真相源
```

但同时仍写：

```text
描述 v2.0.1（v2-dev 开发线）的实际运行时行为
```

这与实际 v2.2 development line 不一致。

更重要的是 §7.3 仍保留：

```text
不引入常驻额度轮询器
```

而 v2.2 已正式实现：

```text
runtime/quota/watcher.py
runtime/quota/watcher_store.py
```

并在同一架构文档 §7.5 描述 Real-Time Quota Watcher。

这是 stable 前必须修复的**权威文档内部矛盾**。

### Stable 冻结解释

必须明确区分：

```text
observer.py
= pure decision layer
= 不做 daemon / network / timer

watcher.py
= resident I/O control loop
= provider polling / heartbeat / epoch observation
= zero model calls
```

不得再使用“不引入常驻额度轮询器”描述 v2.2。

---

## 2.3 P2 — continuity 默认语义需要澄清，但不应改 runtime 默认

`docs/architecture.md` 当前写：

```text
foreground（默认）
```

而 `DEFAULT_EXECUTION_POLICY` 当前是：

```text
continuity.mode = resumable
auto_resume = manual
max_quota_windows = 0
```

stable 阶段**不建议为了文档一致性修改 runtime 默认值**。

推荐冻结解释：

```text
普通、未创建 durable task 的短任务
→ foreground 是产品/交互默认
→ 不创建 continuation state

一旦创建 durable task / execution_policy
→ runtime 默认 continuity.mode=resumable
→ auto_resume=manual
→ 表示“可恢复，但没有自动跨窗授权”
```

也就是说：

```text
resumable != automatic resume
```

文档必须明确两个层次，避免用户理解成“安装后所有任务默认自动续跑”。

---

## 2.4 P2 — architecture §7.3 的旧调度描述应被 v2.2 正式语义替换

旧文字仍偏向：

```text
reset 已知 → 精确规划单次唤醒
不可用 → 周期性探针
```

v2.2 stable 的 correctness path 应改为：

```text
Provider quota truth
→ Watcher / resolver observation

Activation opportunity
→ Persistent Recurring Bridge

Resume qualification
→ Quota Epoch + Subscription + quota-resume
```

必须明确：

- `reset_at` 不按旧 boundary 固定 +5h 外推；
- recurring bridge 是当前唯一 stable Activation Transport；
- bridge fire 只是 probe / execution opportunity；
- bridge fire ≠ new epoch；
- bridge fire ≠ quota consumption；
- `CronUpdate` / `CronDelete` 不是 correctness 前提；
- host unavailable 时自动 wake 可能 missed，下一 SessionStart 仍须可恢复。

---

## 2.5 P2 — Phase0 Primer 历史文档包含已被 RH-01 淘汰的 timeout retry 描述

`docs/GLM-Conductor-v2.2-Phase0-Primer-Experiments.md` 是重要实验记录，应保留历史证据，不建议整体重写。

但其中 P0-QP-07 仍含类似：

```text
超时单次有界重试
```

当前 RH-01 已冻结为：

```text
同一 idempotency key：
provider-side model request 最多一次

ambiguous timeout
→ 不重发
→ post-refresh confirmation
```

stable 前必须至少增加明确 correction / superseded note，防止历史实验文档被误读成当前实现契约。

---

## 2.6 P2 — Dogfood 文档测试基线已落后

`docs/GLM-Conductor-v2.2-Dogfood-Records.md` 仍记录：

```text
full suite 2076
```

当前 release-hardening 已推进到：

```text
2113 tests
```

不应重写历史 dogfood 事件，但 stable 前应增加：

```text
Release-hardening addendum
```

记录：

- RH-01～RH-05 后最终测试数；
- 最新 CI run；
- RH-06 新实证；
- stable release gate 最终状态。

---

## 2.7 P2 — Release-Hardening 文档的 Code Correctness checklist 尚未同步已完成事实

当前 §9.1 的 RH-01～RH-04 相关项仍是：

```text
[ ]
```

但对应代码、测试、review、CI 已完成。

stable 收口时应按真实证据更新，而不是保留“实现已完成但 gate 仍未勾选”的矛盾状态。

---

## 2.8 P2 — 版本发布面仍是 2.1.0，这是当前阶段的正确状态

当前：

```text
plugin.json version = 2.1.0
README badge         = 2.1.0
CHANGELOG            = ### 2.2.0 (unreleased)
```

这是合理的 pre-release 状态。

`validate_plugin.py` check 12 明确要求：

```text
plugin.json version
==
CHANGELOG 第一个 "## " heading 的版本
```

因此 stable bump 必须原子进行：

```text
plugin.json → 2.2.0
CHANGELOG   → ## 2.2.0
README      → 2.2.0
README.en   → 2.2.0
```

不得分成会让 validator 中间失败的独立提交。

---

## 2.9 P2 — `v2-dev` 与 `main` 是 diverged，stable 不应 force fast-forward

stable 集成必须：

```text
v2-dev
  ↓
PR to main
  ↓
CI
  ↓
merge
```

不得：

```text
git push --force main
```

也不建议为了追求线性历史而在 RH-06 后重新大规模 rebase。

真实 dogfood evidence、review receipt 与最终 candidate commit 应保持稳定可追踪。

---

## 2.10 P3 — 明确延后到 v2.3，不得阻塞 stable

以下问题真实存在，但不是 v2.2 stable blocker：

- `quota-watcher start` 返回的是 spawned success，不是 child confirmed active；
- CLI 原语过多，缺统一 `status`；
- 缺 `doctor`；
- 缺 events timeline renderer；
- validator 中文档 literal-marker 依赖较强；
- docs 历史计划数量很多；
- 没有 multi-controller / distributed scheduler；
- experimental transport 尚未实现。

全部进入 v2.3：

```text
Operability & Simplification
```

本轮不得借 stable 收口扩需求。

---

# 3. Stable 架构冻结清单

以下语义在 v2.2.0 stable 前后都不得重新修改。

## 3.1 Routing / Agent

```text
Delegability × Assurance
→ solo / delegate / audit / full
```

保持：

```text
default workers = 2
hard limit      = 4
single main-session orchestrator
subagents do not spawn subagents
```

---

## 3.2 Quota 双层模型

Provider：

```text
AVAILABLE
PRESSURE
EXHAUSTED
UNKNOWN
```

Execution phase：

```text
NORMAL
PRESSURE
DRAINING
BLOCKED
```

默认：

```text
pressure_percent = 35
draining_percent = 20
```

保持：

```text
DRAINING → no new implementation wave
BLOCKED  → no new wave, no finish-current work
UNKNOWN  → conservative budget 1, fail-open
```

---

## 3.3 Quota Epoch

保持：

```text
epoch identity
= deterministic fingerprint of sorted (kind, reset_at) multiset
```

不得把：

```text
status
percent
automation fire count
```

加入 epoch identity。

保持：

```text
probe boundary != executable boundary
```

---

## 3.4 Watcher

保持：

```text
resident
poll-only
zero model calls
provider truth only
```

Watcher 不负责：

```text
task dispatch
task resume authorization
Scheduled Task creation
Session injection
```

---

## 3.5 Primer

保持：

```text
primer_enabled default false
```

授权三重闸：

```text
primer_enabled == true
AND auto_resume in {auto_once, until_done}
AND authorization.source == user
```

保持：

```text
same provider_identity + boundary
→ at most one model request
```

timeout：

```text
ambiguous
→ no resend
→ post-refresh confirmation
```

---

## 3.6 Activation Transport

v2.2 stable 唯一正式 transport：

```text
recurring_bridge
```

继续 reserved：

```text
probe_then_hold
self_retiming
session_injector
```

reserved transport：

```text
arm → TransportReservedError
```

不得 silent fallback。

---

## 3.7 Consumption

保持：

```text
arm            → 0
CronCreate     → 0
bridge fire    → 0
primer         → 0 window accounting
probe          → 0

successful authorized cross-epoch resume commit
→ consumed_quota_windows +1
```

幂等权威键：

```text
task_id + epoch_id
```

---

## 3.8 Host 边界

不得宣称：

```text
ZCode 关闭后仍保证自动执行
```

稳定语义：

```text
host alive + recurring bridge active
→ bounded-latency same-session activation

host unavailable / fire missed
→ durable ledger remains valid
→ next SessionStart recovers
```

---

# 4. ST-00 — Release Baseline Freeze

## 4.1 目标

在任何 RH-06 操作之前冻结当前 candidate 基线。

记录：

```text
v2-dev HEAD
git status
plugin version
full-test baseline
validator baseline
latest CI run
```

期望起点：

```text
HEAD = b149705...
plugin = 2.1.0
full suite = 2113
validator = 15/15
CI = four-way green
```

---

## 4.2 禁止事项

RH-06 开跑前禁止：

- version bump；
- main merge；
- release tag；
- 新 transport；
- 新 watcher capability；
- 修改 quota epoch 规则；
- 提高并发；
- “顺手”重构 task_manager；
- 大范围格式化。

---

# 5. ST-01 — RH-06 Real Dual-Epoch Dogfood

这是 v2.2.0 stable 的核心工作单元。

建议任务名：

```text
wu-22-ST01-rh06-dual-epoch-dogfood
```

---

## 5.1 测试目标

证明下面的完整因果链在真实环境成立：

```text
Epoch N
↓
active implementation
↓
DRAINING
↓
durable handoff
↓
EXHAUSTED
↓
waiting_quota
↓
quota subscription registered
↓
persistent recurring bridge remains armed
↓
new executable epoch N+1
↓
same-session activation
↓
quota-resume
↓
subscription eligible
↓
waiting_quota → executing
↓
activation epoch mark
↓
quota consumption +1
↓
work continues
↓
next DRAINING / EXHAUSTED
↓
new executable epoch N+2
↓
same automation_id fires again
↓
quota-resume again
↓
second activation mark
↓
second quota consumption +1
```

---

## 5.2 环境硬前置

### A. 必须是 fresh interactive Session

原因：

```text
Scheduled-owned session
→ nested CronCreate forbidden
```

因此 initial recurring bridge 必须在 fresh interactive session 建立。

---

### B. 必须确认当前 Session 实际加载的是 v2-dev hook/runtime 快照

ZCode hooks 是 session-start snapshot，不热加载。

RH-06 不允许出现：

```text
installed 2.1.0 hooks
+
手工调用 v2-dev CLI
```

然后把结果宣称为“v2.2 完整 E2E”。

必须采用以下之一：

#### 推荐路径

```text
刷新 / 重装本地插件缓存到当前 v2-dev checkout
→ 启动全新 Session
→ 核对 hooks.json 来源包含 v2.2 Cron matcher
```

至少确认当前 Session 生效 manifest 包含：

```text
PreToolUse:
  CronCreate

PostToolUse:
  CronCreate|CronUpdate|CronDelete

PostToolUseFailure:
  CronCreate|CronUpdate|CronDelete
```

以及：

```text
Stop
Agent|Task
Bash
```

均来自当前 v2-dev 插件内容。

无需提前把正式版本号改为 2.2.0；关键是**生效代码必须来自 release candidate**。

---

### C. 测试任务

创建新的真实长任务，不复用历史 `v22-loop-857f55a` 作为主要验收载体。

要求：

```text
continuity.mode = resumable
auto_resume = until_done
authorization.source = user
max_quota_windows >= 2
activation_transport = recurring_bridge
```

Primer：

```text
默认建议保持 false
```

RH-06 核心目标是验证 continuation，不是强制验证 Primer。

若真实窗口必须 prime 才能物化，可在用户已明确授权后单独启用，并记录：

```text
primer_enabled=true
```

不得为了加速实验绕过三重授权。

---

## 5.3 Initial bridge 建立

在 fresh interactive Session：

1. `wake-plan`
2. 创建一个 persistent recurring Scheduled Task
3. 记录 `automation_id`
4. runtime `arm`
5. `transport-status`
6. `wake-status`
7. `CronList` 宿主事实核对

冻结：

```text
AUTOMATION_ID_0
```

后续 N+1、N+2 恢复都必须使用它。

第二个窗口不得创建：

```text
AUTOMATION_ID_1
```

---

## 5.4 每个 quota handoff 必须机械完成

进入 DRAINING 时必须确认：

```text
allow_new_wave = false
```

并执行：

```text
join current work
verify / review if applicable
checkpoint refresh
resume manifest refresh
quota subscription registration
activation transport armed check
```

Stop continuity health 必须能够看到：

```text
handoff durable
subscription present
transport armed
```

若当前 scheduled-origin 导致 create forbidden：

```text
degraded allow
```

只能作为已有桥存在/宿主限制下的正常语义，不得用来替代 initial bridge 建立。

---

## 5.5 Epoch N → N+1 验收

必须记录：

```text
epoch_N
epoch_N_plus_1
```

且：

```text
epoch_N != epoch_N_plus_1
```

activation 后：

```text
quota-resume
```

必须得到：

```text
resumed = true
```

状态：

```text
waiting_quota → executing
```

必须有 journal / state 证据：

```text
quota subscription eligibility
quota_epoch_advanced / activation mark
quota_consumption_pending
quota_boundary_consumed
manifest refresh
```

消费变化：

```text
consumed_after_first_resume
=
consumed_before + 1
```

---

## 5.6 同 epoch duplicate fire 幂等验证

在 N+1 epoch 内至少观察一次额外 recurring fire，或用安全方式重复执行同 epoch resume evaluation。

必须证明：

```text
activation mark count unchanged
consumed_quota_windows unchanged
```

即：

```text
same epoch duplicate fire
→ no second activation
→ no second consumption
```

这项不能只靠单元测试替代。

---

## 5.7 Epoch N+1 → N+2 验收

重复真实跨窗流程。

必须记录第三个 epoch：

```text
epoch_N_plus_2
```

并满足：

```text
epoch_N
!= epoch_N_plus_1
!= epoch_N_plus_2
```

再次：

```text
waiting_quota → executing
```

消费最终：

```text
consumed_final
=
consumed_initial + 2
```

---

## 5.8 Bridge identity 验收

两个新 epoch 的恢复必须满足：

```text
automation_id N+1 == AUTOMATION_ID_0
automation_id N+2 == AUTOMATION_ID_0
```

并且：

```text
runCount monotonically increases
```

不得出现：

```text
nested CronCreate
```

验收口径：

```text
one task
→ one persistent recurring bridge
→ multiple fires
→ multiple quota epochs
```

---

## 5.9 Accounting 验收

最终必须同时记录：

```text
bridge_fire_count
successful_cross_epoch_resume_count
consumed_quota_windows delta
```

要求：

```text
successful_cross_epoch_resume_count = 2
consumed_quota_windows delta        = 2
```

同时明确：

```text
bridge_fire_count may be > 2
```

即证明：

```text
fire count != consumption count
```

---

## 5.10 Watcher degradation 验收

至少做一次受控 watcher 缺席/停止测试：

1. 保存任务 state / journal 摘要；
2. `quota-watcher stop`；
3. 确认 stop 只改变 watcher control state；
4. 确认 task state 未被 watcher stop 破坏；
5. 使用 `transport-status` / task ledger 核对 continuation 仍存在；
6. 再启动 watcher，确认可接管并继续观察。

本项验收的是：

```text
watcher absence does not corrupt task state
```

不是要求：

```text
watcher absence 时仍有精确实时 quota observation
```

---

## 5.11 Completion cleanup

最终完成时：

```text
verification
review if required
finalizing
Stop gate
completed
```

允许宿主侧：

```text
CronDelete / pause
```

仅做一次 best-effort。

无论成功/失败：

```text
correctness must not depend on cleanup success
```

若删除失败：

```text
tombstone / no-task future activation
→ safe no-op
```

---

## 5.12 RH-06 必须保存的证据

至少形成以下表格：

| Evidence | Epoch N | Epoch N+1 | Epoch N+2 |
|---|---|---|---|
| epoch_id | required | required | required |
| provider status | required | required | required |
| execution phase | required | required | required |
| automation_id | baseline | same | same |
| runCount | baseline | increased | increased |
| task status before activation | — | waiting_quota | waiting_quota |
| task status after resume | — | executing | executing |
| registered_epoch_id | required | required | required |
| last_activation_epoch_id | — | N+1 | N+2 |
| consumed_quota_windows | baseline | +1 | +2 |
| manifest refreshed | yes | yes | yes |
| nested CronCreate | none | none | none |

并保存 journal 事件顺序摘要。

---

## 5.13 RH-06 通过条件

以下全部满足才 PASS：

- [ ] 至少三个 distinct epoch：N / N+1 / N+2；
- [ ] 跨两个新的 executable epoch；
- [ ] 同一个 `automation_id`；
- [ ] `runCount` 增长；
- [ ] 无 nested CronCreate；
- [ ] N+1 `waiting_quota → executing`；
- [ ] N+2 `waiting_quota → executing`；
- [ ] 两次 activation mark；
- [ ] consumed delta 恰为 2；
- [ ] 同 epoch duplicate fire 零重复 activation；
- [ ] 同 epoch duplicate fire 零重复 consumption；
- [ ] handoff checkpoint / manifest 两轮均新鲜；
- [ ] watcher stop/absence 不破坏 task state；
- [ ] cleanup correctness 不依赖 CronDelete/CronUpdate。

任一失败：

```text
RH-06 = FAIL
stable release = STOP
```

---

# 6. RH-06 失败时的处理纪律

不得为了“按时 stable”降低 gate。

先分类。

## 6.1 Host capability failure

例如：

```text
CronCreate 当前 Session 不可用
host offline
Scheduled Task 未触发
session injection host bug
```

如果 runtime state / ledger 未损坏：

```text
记录 host limitation
换 fresh environment 重跑
```

不得立刻修改 runtime。

---

## 6.2 Runtime correctness failure

例如：

```text
duplicate consumption
wrong epoch activation
same bridge not reusable
subscription deadlock
pending transaction cannot reconcile
Stop gate incorrectly allows/bocks
```

则：

```text
新建单独 patch work unit
→ minimal fix
→ targeted tests
→ full suite
→ independent review
→ commit
→ RH-06 从干净任务重新开始
```

不得只从失败后的第二个窗口继续“拼接证据”。

---

## 6.3 Documentation-only mismatch

不影响 runtime 的证据描述错误：

```text
ST-02 修复
```

无需重新跑两完整 epoch，除非修改了 runtime semantics。

---

# 7. ST-02 — Architecture / Evidence Truth-Source Closure

建议工作单元：

```text
wu-22-ST02-doc-truth-closure
```

原则：

> 只修当前 stable truth；历史实验记录保留历史性质，不把旧计划重写成“从未发生”。

---

## 7.1 `docs/architecture.md`

必须修改：

### A. 顶部版本

从：

```text
描述 v2.0.1（v2-dev 开发线）
```

改为：

```text
描述 v2.2.0 stable 的实际运行时行为
```

或在正式 release commit 前写：

```text
描述 v2.2 release candidate / stable contract
```

最终 tag 前必须是 stable 表述。

---

### B. continuity 默认解释

明确：

```text
foreground
= 普通未跟踪短任务的交互默认

resumable/manual
= durable task execution_policy 的保守默认
= 可恢复，不代表自动续跑
```

---

### C. 删除/修正“不引入常驻额度轮询器”

替换为：

```text
observer 保持纯决策；
v2.2 可选 resident Watcher 负责 provider quota truth；
Watcher 零模型调用，不负责 task dispatch 或 session activation。
```

---

### D. 修正调度 correctness path

把 v2.1 风格：

```text
reset known → exact one-shot wake
```

收口为：

```text
Watcher/resolver → quota truth
Epoch/subscription → resume eligibility
Persistent Recurring Bridge → same-session activation opportunity
```

---

### E. 完成清理

明确：

```text
CronDelete / CronUpdate success is not a correctness dependency
```

---

## 7.2 `docs/GLM-Conductor-v2.2-Phase0-Primer-Experiments.md`

在文件顶部与 P0-QP-07 增加 correction：

```text
Release-hardening RH-01 supersedes timeout retry wording.
Current stable contract is single-attempt:
ambiguous timeout → no resend → post-refresh confirmation.
```

如果存在 RH-02 前的 caller-discipline 描述，同样标记：

```text
RH-02 supersedes:
public execution API now mechanically authorizes.
```

---

## 7.3 `docs/GLM-Conductor-v2.2-Dogfood-Records.md`

不要删除历史 2076 记录。

增加 stable addendum：

```text
RH-01..05 final baseline
RH-06 new live evidence
final full-suite count
final validator result
final CI run
stable gate result
```

将：

```text
RG-22-06 = partial / pending
```

更新为真实 PASS 或 FAIL。

只有 PASS 才进入 ST-03。

---

## 7.4 `docs/GLM-Conductor-v2.2-Release-Hardening-Implementation-Plan.md`

更新：

### §9.1

RH-01～RH-04 已证明项按证据勾选。

### §9.3

RH-06 成功后逐项勾选。

### §14.4

将：

```text
RH-06 / RH-07 remain
```

更新为：

```text
RH-06 closed
RH-07 scheduled / closed
```

并记录最终 dogfood evidence reference。

---

## 7.5 README / README.en 的 stable 能力边界

README 可以保留“选择性编排”主定位，但建议 stable tagline 收口为：

```text
Selective orchestration and durable quota-aware continuity
for GLM coding agents in ZCode.
```

必须避免：

```text
real-time quota event immediately wakes dormant session
```

推荐准确表述：

```text
resident quota observation
+
bounded-latency recurring same-session activation
+
durable SessionStart recovery fallback
```

---

# 8. ST-03 — v2.2.0 Release Metadata Atomic Bump

建议工作单元：

```text
wu-22-ST03-version-release-surface
```

必须是**单个原子 work unit**。

---

## 8.1 必改文件

### `plugins/glm-conductor/.zcode-plugin/plugin.json`

```json
"version": "2.2.0"
```

同时可收口 description，使其包含：

```text
selective orchestration
runtime enforcement
quota-aware continuity
```

但不要把 description 写成完整 release note。

---

### `CHANGELOG.md`

从：

```text
### 2.2.0 (unreleased)
```

改为符合仓库既有格式的：

```text
## 2.2.0
```

可按既有风格增加 release date。

删除顶部“为何临时使用 ###”的 unreleased note。

---

### `README.md`

```text
version-2.1.0
→ version-2.2.0
```

---

### `README.en.md`

同上。

---

### `marketplace.json`

当前没有 version 字段，因此不需要为了版本同步新增 schema。

可仅在需要时更新 description；不是硬要求。

---

## 8.2 为什么必须原子提交

validator check 12：

```text
plugin.json version
==
CHANGELOG 第一个 ## 标题版本
```

所以：

```text
先改 plugin.json
后改 CHANGELOG
```

作为两个独立可验证 commit 会制造人为红灯。

ST-03 必须一次完成。

---

# 9. ST-04 — Final Verification + RH-07 Independent Stable Review

建议工作单元：

```text
wu-22-ST04-stable-review
```

---

## 9.1 Targeted regression

至少覆盖：

```text
primer
quota epoch
quota watcher / store
quota subscription
resume consumption
boundary consumption
activation transport
Cron hooks
Stop gate continuity
execution policy
recovery / manifest
```

可按测试文件逐项跑定向测试。

---

## 9.2 Full validation

必须：

```bash
python scripts/validate_plugin.py
python -m unittest discover -s tests -v
```

要求：

```text
validator 15/15
full suite all green
```

测试总数以最终实际输出为准，不硬编码 2113；如果 ST-01 无代码新增，预计仍为 2113。

---

## 9.3 Independent stable review 输入

新的独立 reviewer 必须至少检查：

```text
1. v2-dev final diff
2. RH-06 dogfood evidence
3. release-hardening gate
4. architecture truth-source corrections
5. plugin/changelog/readme version consistency
6. latest full suite
7. latest CI
8. v2-dev vs main compare
```

---

## 9.4 Reviewer 关注重点

### Correctness

- quota epoch identity 未变；
- consumption commit point 未变；
- Primer 仍 single-attempt；
- Primer default off；
- Watcher 仍 zero-model-call；
- recurring_bridge 仍唯一 stable transport；
- reserved transport 没有半实现；
- no fixed +5h correctness assumption；
- no CronUpdate/CronDelete correctness dependency。

### Release integrity

- RH-06 不是“历史 evidence 拼接”；
- 真正有 N/N+1/N+2；
- same bridge；
- accounting delta = 2；
- same-epoch idempotence 有真实证据。

### Documentation

- architecture 无 v2.0.1 stale banner；
- architecture 无“不引入常驻 watcher”矛盾；
- Phase0 retry wording 已 correction；
- dogfood 状态真实；
- README 不过度宣传。

---

## 9.5 RH-07 verdict

只允许：

```text
ship
fix-first
rethink
```

只有：

```text
ship
```

才能进入 ST-05。

若 `fix-first`：

```text
修复
→ tests
→ full validation
→ old review invalid
→ fresh independent review
```

---

# 10. ST-05 — v2-dev → main Integration

当前无 open `v2-dev → main` PR。

建议：

```text
open PR
head = v2-dev
base = main
```

---

## 10.1 不允许 force main

禁止：

```text
git push --force origin v2-dev:main
```

---

## 10.2 不建议 stable 前大规模 rebase

理由：

- v2-dev 已有大量 dogfood / review / receipt / commit evidence；
- main divergence 主要是 v2.1 release topology；
- main stable tree 与 v2-dev 的 v2.1 release base 已对齐；
- merge 更容易保留证据链。

推荐：

```text
PR merge commit
```

或仓库当前稳定采用的等价非破坏性 merge 流程。

---

## 10.3 PR 必查

- file diff 是否仅包含 v2.2 预期范围；
- main v2.1 stable 能力无意外删除；
- `.github/workflows/validate.yml` 保留；
- plugin metadata 是 2.2.0；
- changelog first `##` 是 2.2.0；
- README CN/EN 是 2.2.0；
- no secrets；
- no `.glm-conductor/` dogfood runtime ledger 进入 git；
- no primer probe credential material；
- no watcher log / CSV 临时资产误提交。

---

## 10.4 PR CI

必须等待：

```text
ubuntu 3.8
ubuntu 3.13
windows 3.8
windows 3.13
```

全部 green。

---

# 11. ST-06 — Post-Merge CI + GitHub Stable Release

合并 main 后，不要立即创建 Release。

先确认：

```text
main HEAD
→ GitHub Actions green
```

---

## 11.1 Release tag

沿用 v2.1 规范：

```text
v2.2.0
```

target：

```text
main
```

---

## 11.2 GitHub Release 名称

建议：

```text
GLM Conductor 2.2.0 — Stable
```

---

## 11.3 Release Note 必须包含

### What v2.2 delivers

- Dual-layer quota model；
- Quota Epoch；
- resident quota watcher；
- Persistent Recurring Bridge；
- Activation Transport abstraction；
- quota subscription；
- exactly-once activation / epoch-based consumption；
- mechanical Primer authorization；
- crash-safe quota accounting；
- Stop-gate continuity health。

### Runtime integrity retained from v2.1

- selective route；
- permit / wave / lease；
- runtime-observed Agent lifecycle；
- provenance verification；
- review receipt；
- multi-repo；
- completion gate。

### Stable evidence

写最终真实数字：

```text
full tests
validator 15/15
four-way CI
RH-06 two new executable epochs
same persistent bridge
consumption delta = 2
```

### Known boundaries

必须明确：

- recurring_bridge 是唯一 stable activation transport；
- watcher 不直接向 dormant Session 注入 turn；
- same-session activation 依赖 ZCode Scheduled Task host；
- host 关闭/休眠可能 missed，durable SessionStart recovery 兜底；
- Primer 默认关闭；
- CronUpdate/CronDelete 不属于 correctness dependency；
- single-controller per task/workspace remains the supported model；
- worker hard limit 4。

---

# 12. Stable Release Gate — 最终唯一清单

## 12.1 Runtime

> [勾选 2026-09-06] 依据 = RH-06 双窗活跑（v22-rh06-be1301）+ stable 会话独立重演 14/14（Dogfood-Records §11：三 epoch 584f/61ff/5335、automation-2d2c80aa 全程唯一、waiting_quota→executing ×2、激活标记恰 2、consumed 2/2、fire#2 同窗零增量、watcher 工件重演、清理链 completed→tombstone→单次 CronDelete 成功）。

- [x] RH-06 跨两个新 executable epoch；
- [x] three distinct epoch IDs；
- [x] same persistent automation ID；
- [x] no nested CronCreate；
- [x] two successful waiting_quota → executing transitions；
- [x] activation exactly once per epoch；
- [x] consumption delta exactly 2；
- [x] same-epoch replay zero duplicate consumption；
- [x] watcher absence does not corrupt task state；
- [x] completion correctness independent of host cleanup。

---

## 12.2 Existing hardening non-regression

> [勾选 2026-09-06] 依据 = Release-Hardening §9.1 十一项证据勾选（RH-01..05 ship 收据链 a57868f8/a7e62763/19251c2f/be6f4617/8dd36ff9 + 2113 + validator 15/15 + CI run 33828629148） + RH-07 终审九点 correctness 代码级抽查全过。

- [x] Primer request max once per idempotency key；
- [x] timeout no resend；
- [x] post-refresh ambiguity confirmation；
- [x] public Primer mechanical authorization；
- [x] manual/notify no Primer；
- [x] consumption pending transaction crash-safe；
- [x] migration pending transaction crash-safe；
- [x] arm/fire/create zero quota-window consumption；
- [x] observer pure；
- [x] watcher zero model call；
- [x] reserved transports remain reserved。

---

## 12.3 Documentation

> [勾选 2026-09-06] 依据 = ST-02a（b8f15aa）+ ST-02b（3dcb0ba/9c253de/66cbda8）+ ST-03（7047311/0897411/e6314ba）各轮 ship 审查；Dogfood §11 stable addendum；Release-Hardening §9.1/§9.3 按证据关闭。

- [x] architecture version updated；
- [x] foreground vs resumable/manual defaults clarified；
- [x] no stale “no resident watcher” statement；
- [x] stable scheduling path reflects Watcher/Epoch/Recurring Bridge；
- [x] Phase0 Primer retry wording corrected；
- [x] Dogfood RH-06 evidence appended；
- [x] Release-Hardening §9.1 / §9.3 closed accurately；
- [x] README CN/EN capability boundary accurate。

---

## 12.4 Version / Release

> [勾选 2026-09-06] 依据 = 版本面四触点原子（0897411）；全量 2113 OK + validator 15/15（本会话亲跑 + 定向 11 域 16 模块 OK）；统一 push b149705..e7a1f33 → CI run 34014359230 四路绿；RH-07 ship（P3 rider e7a1f33 确认 ship）；PR #2 CI 四路绿 MERGEABLE → merge b4f4d3f → main 合并后 CI run 34014682088 绿；tag v2.2.0 → GitHub Release 「GLM Conductor 2.2.0 — Stable」已发布。

- [x] plugin.json = 2.2.0；
- [x] CHANGELOG first `##` = 2.2.0；
- [x] README badge = 2.2.0；
- [x] README.en badge = 2.2.0；
- [x] validator 15/15；
- [x] full suite green；
- [x] v2-dev push CI green；
- [x] independent RH-07 reviewer = ship；
- [x] PR CI green；
- [x] main post-merge CI green；
- [x] GitHub Release `v2.2.0` published from main。

只有全部勾选：

```text
v2.2.0 = STABLE
```

---

# 13. Work Unit 拆分与 ownership

## `wu-22-ST00-baseline-freeze`

**修改文件**：建议无 tracked file 修改；只做证据记录。  
**输出**：baseline evidence。

---

## `wu-22-ST01-rh06-dual-epoch-dogfood`

**计划 tracked ownership**：

```text
docs/GLM-Conductor-v2.2-Dogfood-Records.md
docs/GLM-Conductor-v2.2-Release-Hardening-Implementation-Plan.md
```

真实 runtime ledger：

```text
.glm-conductor/
```

保持 untracked / local。

**默认不改 runtime code。**

若出现 runtime bug，停止本 WU，另开 patch WU。

---

## `wu-22-ST02-doc-truth-closure`

ownership：

```text
docs/architecture.md
docs/GLM-Conductor-v2.2-Phase0-Primer-Experiments.md
docs/GLM-Conductor-v2.2-Dogfood-Records.md
docs/GLM-Conductor-v2.2-Release-Hardening-Implementation-Plan.md
README.md
README.en.md
```

如 README 只在 ST-03 做版本面，可把内容修订与版本 bump 分离，但不得制造 changelog/plugin 版本不一致。

---

## `wu-22-ST03-version-release-surface`

ownership：

```text
plugins/glm-conductor/.zcode-plugin/plugin.json
CHANGELOG.md
README.md
README.en.md
marketplace.json   # only if description sync is chosen
```

必须原子完成 version consistency。

---

## `wu-22-ST04-stable-review`

只读 review + receipt / evidence。

原则：

```text
no code edits inside reviewer
```

---

## `wu-22-ST05-main-integration`

PR / merge 操作。

不做额外功能修改。

---

## `wu-22-ST06-github-release`

tag / release note / release publication。

不修改 runtime。

---

# 14. Agent 实施纪律

## 14.1 不扩需求

禁止：

```text
Session Injector
Probe-Then-Hold production
Self-Retiming production
CronUpdate-based correctness
new quota provider architecture
database backend
service manager
distributed scheduler
multi-controller
higher worker limit
v2.3 status/doctor/timeline implementation
```

---

## 14.2 不把 historical evidence 当 current implementation contract

优先级：

```text
current runtime code
>
current authoritative architecture
>
release-hardening correction
>
C-series correction
>
old M-series plan
>
historical Phase0 implementation wording
```

实验事实仍可保留，但实现语义以后续 hardening 为准。

---

## 14.3 不虚构 dogfood

没有真实发生：

```text
不得勾选
不得写 PASS
不得用单测替代
```

---

## 14.4 不把 host failure 写成 runtime failure

先分类：

```text
host capability
runtime correctness
configuration
documentation
```

再处理。

---

## 14.5 不把 warning 降级成 stable claim

例如：

```text
Watcher knows quota
```

不能写成：

```text
Watcher immediately wakes session
```

---

# 15. Stable 后明确延后到 v2.3

v2.3 主题建议冻结为：

```text
Operability & Simplification
```

优先级：

1. `glm-conductor status`
2. `glm-conductor doctor`
3. `events.jsonl` timeline
4. runtime API conceptual surface consolidation
5. watcher start/active status semantics cleanup
6. validator machine-contract vs docs-lint 分离
7. docs historical archive整理

不建议 v2.3 立即新增新的 quota/transport mechanism。

---

# 16. 最终发布判断

当前 `v2-dev@b149705` 可以视为：

```text
Architecture       = closed
Core mechanics     = closed
Release hardening  = RH-01..05 closed
CI                 = green
Stable evidence    = one critical gap
```

唯一需要真正证明的核心命题是：

> **在 v2.2 修正后的 C-series 记账与授权语义下，一个真实 `until_done` 任务能够复用同一个 Persistent Recurring Bridge，连续跨越两个新的 executable quota epoch，并且 activation/consumption 均保持 exactly-once。**

如果 RH-06 PASS，并完成本文 ST-02～ST-06：

```text
v2.2.0 stable
```

具备充分依据。

如果 RH-06 FAIL：

```text
不发布 stable
```

先修复真实根因，再重新跑完整 release gate。

---

# 17. Agent 最短执行清单

```text
[1] Freeze v2-dev@current HEAD evidence
[2] Refresh/load v2-dev plugin snapshot in a fresh ZCode session
[3] Run RH-06 real until_done task across Epoch N → N+1 → N+2
[4] Prove same automation_id, no nested create, consumed delta=2
[5] Prove same-epoch duplicate fire is idempotent
[6] Append RH-06 evidence and close release-hardening gates
[7] Repair architecture/document truth-source inconsistencies
[8] Atomically bump plugin/changelog/readme to 2.2.0
[9] Run validator + full tests
[10] Independent fresh-context RH-07 review → ship
[11] Open v2-dev → main PR; do not force main
[12] Require four-way PR CI green
[13] Merge to main
[14] Require main post-merge CI green
[15] Publish GitHub Release v2.2.0 — Stable
```

**完成。**
