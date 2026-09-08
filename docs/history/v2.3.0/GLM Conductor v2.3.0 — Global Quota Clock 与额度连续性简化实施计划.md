# GLM Conductor v2.3.0  
## Global Quota Clock 与额度连续性简化实施计划

**状态：Implementation-ready（2026-09-07 讨论裁决回写 + 2026-09-08 预算归属修正）**  
**目标版本：v2.3.0**  
**基线版本：v2.2.1 / current main**  
**核心原则：窗口连续性与任务额度调度完全解耦——clock 常驻无预算，窗口预算 N 属于 task 侧**

---

# 1. 背景与问题

v2.2 已经建立较完整的 quota control plane，包括：

- provider quota resolver；
- quota epoch；
- NORMAL / PRESSURE / DRAINING / BLOCKED；
- Quota Watcher；
- Window Primer；
- Persistent Wake Bridge；
- Activation Transport；
- quota subscription / resume accounting。

这些组件分别解决了局部问题，但额度窗口连续性的实际运行路径仍然过于间接：

```text
quota observation
→ watcher polling
→ boundary
→ recurring wake bridge
→ scheduled wake
→ refresh
→ resume
```

当前 Stable Wake Bridge 默认使用约 60 分钟 recurring Scheduled Task，因此额度实际恢复与会话重新获得 execution opportunity 之间可能存在接近一个完整 interval 的空闲时间。

同时，ZCode 实测确认：

1. Scheduled Task 实际绑定创建它的原会话；
2. 一个会话只能绑定一条 automation；
3. 触发回合内无法可靠调用 CronUpdate/CronDelete；
4. ZCode interval 最大 200 分钟，无法直接配置约 5 小时周期；
5. 直接修改 `tasks-index.sqlite` 中指定 automation 的 `next_run_at` 会立即生效；
6. `next_run_at` 可以使用任意 off-grid 时间；
7. run 完成阶段不会覆盖触发回合写入的 `next_run_at`；
8. 该值只决定下一次触发；下一次派发时 ZCode 会重新按 recurring 网格生成 fallback `next_run_at`。

第 1–8 点的完整凭据链（2026-09-07 本机实测，会话 sess_86d76e30）：
`C:/Users/user/ZCodeProject/cron-selfupdate-test/`（result.md / exp-c-timeline.md /
automation-backup.json = automations 表 34 列实测快照，含 next_run_at epoch ms、
model/provider/mode/thought_level 等字段；DB 路径 `~/.zcode/v2/tasks-index.sqlite`，
WAL 模式外部写实测成功）。

调度器写模型（三轮独立观测自洽）：

```text
触发时：读 DB 任意 next_run_at 值，精确执行（off-grid 合法）
派发时：按 cron/interval 网格从当前时间重算下一槽并写回（外部值 one-shot）
run 完成/回合结束：不写、不覆盖
```

附带实测：非触发回合内同会话 CronUpdate 自己的 automation 可行且即时重算（正样本×1）；
completed 行被宿主自动清理，parked 的 active 行不会被清理——park 是稳定的"停止"手段。

第 5–8 点构成了一个非常适合 GLM Conductor 的机制：

> **永久 recurring automation + 每轮动态重写 next_run_at。**

---

# 2. v2.3 的核心架构决策

## D1. 增加 Account-level Global Quota Clock

每个 provider identity 最多存在一个 Global Quota Clock。

它只负责：

```text
5h window reset
→ 及时产生一次真实 model turn
→ materialize 下一窗口
→ force-refresh
→ 获取新的 reset_at
→ 安排下一次 tick
```

它不负责：

- task dispatch；
- worker 数量；
- PRESSURE / DRAINING；
- task resume authorization；
- task checkpoint；
- task completion；
- quota-window budget。

即：

```text
Provider Account
      │
      ▼
Global Quota Clock
      │
      ▼
持续存在的真实 5h quota windows
```

---

## D2. Global Clock 与 Task Execution 完全分离

任务侧继续消费当前 quota 状态：

```text
Current Quota
    │
    ├── NORMAL
    ├── PRESSURE
    ├── DRAINING
    └── BLOCKED
```

v2.3.0 **不修改**现有：

```text
pressure_percent = 35%
draining_percent = 20%
```

也不在本轮加入：

- burn-rate prediction；
- unit cost estimation；
- 新的 quota phase；
- 动态 reserve floor。

这些属于以后独立的 Task Quota Policy 工作，不与 Global Clock 一起实施。

**职责边界澄清（2026-09-08 裁决，取代 09-07"D2 修正案"表述）**：
额度监测刷新（Global Quota Clock）是极低成本的后台常驻机制，**无限期运行、
不设窗口预算**；额度窗口预算 N 是**任务侧**概念——长程任务持续推进时的
可使用窗口上限（v2.2 的 `continuity.max_quota_windows` / consumed / remaining
记账，accounting.py），避免任务无休止消耗额度。两者独立，v2.3 对 task 侧
机制零改动（即 D2 原文语义）。

---

## D3. 不采用 per-window one-shot automation chain

明确禁止把主路径设计成：

```text
Q1 → create Q2 → create Q3 → ...
```

原因：

- trigger round 内 host mutation 不可靠；
- successor 创建失败会永久断链；
- 会造成 automation identity 与清理复杂度增长；
- 与“一 provider 一个永久 clock”的设计原则冲突。

Global Clock 始终保持同一个 automation identity。

---

## D4. recurring schedule 只作为 watchdog

Clock automation 仍通过官方 ZCode Scheduled Task 创建，并保持：

```text
recurring = true
fallback interval = 60 min
```

但 60 分钟不再表示正常执行周期。

正常情况下：

```text
ZCode dispatch
    ↓
ZCode 自动写入下一 recurring grid
    ↓
model turn
    ↓
GLM Conductor 得到 new reset_at
    ↓
覆盖 next_run_at = reset_at + grace
```

因此中间的 60 分钟网格不会触发。

只有 retime 失败时：

```text
SQLite update failure
provider failure
runtime crash
unexpected exception
```

ZCode 自己写入的 recurring grid 才会成为下一次恢复机会。

定义：

```text
dynamic next_run_at = primary timing
recurring interval  = watchdog timing
```

---

# 3. Global Quota Clock 正常算法

建议默认参数：

```text
grace_seconds       = 120
retry_delay_seconds = 300
fallback_interval   = 60 min
```

## 3.1 初始化

普通交互回合执行：

```text
force-refresh quota
      ↓
解析 provider identity
      ↓
取得 five_hour.reset_at = A
      ↓
first_target = A + 120s
      ↓
创建唯一 recurring Scheduled Task（Flash + 最低思考强度，见 §3.3）
      ↓
获得 automation_id
      ↓
bind clock（快照 runtime_path 入 state；clock 常驻无窗口预算，见 §3.2）
      ↓
next_run_at = first_target
```

Scheduled Task 本身必须使用官方 ZCode 创建能力。

禁止通过 SQLite：

- INSERT automation；
- DELETE automation；
- 修改 schedule rule；
- 创建 successor。

SQLite adapter 在正常运行中只允许修改已经绑定 automation 的：

```text
next_run_at
```

## 3.2 职责边界：clock 常驻无预算，窗口预算 N 属于 task 侧（2026-09-08 裁决）

**额度监测刷新（Global Quota Clock）** = 极低成本的后台常驻机制（实现形态 =
永久 recurring automation，每 5h 窗口边界一 tick），无限期运行、不设任何
窗口预算，是账户级基础设施。

**额度窗口预算 N** = 任务侧概念：长程任务（auto_resume 家族）持续推进时的
可使用窗口上限，避免任务无休止消耗额度。v2.2 已有完整实现，v2.3 原样保持：

```text
continuity.max_quota_windows       授权窗口数上限（task state 内）
continuity.consumed_quota_windows  resume 时逐窗扣账（accounting.py
                                   record_quota_boundary_consumed；
                                   manual/notify 不得制造消耗）
remaining = max(0, max - consumed) 耗尽即停止 auto_resume
```

两者独立：clock 只负责窗口持续滚动与状态新鲜，不读不写 task 的窗口账；
task 消耗到 N 上限后停止自动续跑，与 clock 是否继续滚动无关。

## 3.3 Tick automation 成本配置（2026-09-07 裁决）

clock 常驻无限期运行（§3.2），token 成本必须压到最低。automation 行自带
`model / mode / thought_level` 字段（实测快照证实，且随创建会话继承）。
bind/replace 时必须：

```text
model         = GLM-5.3-Flash
thought_level = 最低档
prompt        = §9 极简版（数十 token 量级）
```

已知边界：宿主 CronCreate/CronUpdate 均无 model 参数，模型继承创建会话——
因此推荐在 Flash + 最低思考强度的会话中执行 bind；CLI 在 bind/replace 后
从 DB 读回 automation.model 写入 state，非 Flash 时在输出中报告 warning。
replace 若发生在主力模型会话，clock 会绑定该模型直至下次 replace（已知限制，文档化）。

---

# 4. Clock Tick 算法

Scheduled Task prompt 最终只负责调用：

```text
quota-clock-tick
```

不在 prompt 中复制复杂 quota orchestration 逻辑。

一次 tick：

```text
1. Scheduled Task 到点
2. ZCode 派发 model turn
3. 本次真实 model turn 本身完成 window touch/materialization
4. quota-clock-tick --force-refresh
5. 获取最新完整 snapshot（含 weekly，非仅 five_hour）
6. 计算 target（分类见下）
7. 修改自己的 next_run_at
8. read-back 验证
9. 记录 clock state
10. return
```

### 正常情况

若：

```text
reset_at = B
B > now
provider fact fresh
```

则：

```text
next_run_at = B + grace_seconds
```

### 4.0 weekly blocked（2026-09-07 裁决）

weekly 窗口 EXHAUSTED 且优先级压制 five_hour（weekly 优先不归零）时，
不做 5 分钟空转；决策输入为完整 snapshot，复用 epoch 既有 boundary 语义
（epoch.py 的 executable_boundary = max blocked EXHAUSTED resets + grace）：

```text
next_run_at = weekly reset_at + grace（长 park）
```

物理边界：weekly 耗尽期 tick turn 的模型调用可能被 API 拒绝、turn 无法执行 CLI——
此时 retime 不发生，native 60m watchdog 自然接管（每小时一次失败尝试直至恢复），
属预期降级而非故障，须文档化；W5 dogfood 的 quota-exhausted 场景顺带观测该形态。

---

## 4.1 reset 没有推进

如果触发发生以后：

```text
reset_at 仍然是旧值
且 reset_at + grace <= now
```

说明：

- provider 状态传播尚未完成；
- materialization 尚未反映；
- 或 refresh 得到异常状态。

不要重新引入复杂 phase。

直接：

```text
next_run_at = now + retry_delay_seconds
```

默认五分钟后再确认。

---

## 4.2 provider refresh 失败

不得根据 stale quota 强行计算长期下一窗口。

执行：

```text
next_run_at = now + retry_delay_seconds
```

如果连这次 retime 都失败：

```text
什么都不做
```

ZCode dispatch 阶段已经生成的 recurring fallback 自然接管。

故障链变为：

```text
reset_at + 2m
      ↓
正常路径失败
      ↓
now + 5m retry
      ↓
retime 本身也失败
      ↓
native 60m watchdog
```

不需要额外 daemon。

---

# 5. Provider / Account 全局状态

Global Clock 不是 task state。

不要放进：

```text
.glm-conductor/tasks/<task_id>/
```

建议新增用户级状态目录：

```text
~/.glm-conductor/
└── quota-clocks/
    └── <provider_identity_hash>.json
```

可通过：

```text
GLM_CONDUCTOR_HOME
```

覆盖默认根目录，便于测试和特殊安装。

建议状态：

```json
{
  "schema_version": 1,
  "provider_identity_hash": "...",
  "automation_id": "...",
  "owner_repository": "...",
  "zcode_db_path": "...",
  "runtime_path": "...",
  "automation_model": "...",
  "fallback_interval_minutes": 60,
  "grace_seconds": 120,
  "retry_delay_seconds": 300,
  "last_tick_at": "...",
  "last_reset_at": "...",
  "next_target_at": "...",
  "last_retime_at": "...",
  "status": "bound | parked_weekly | rebound | stale"
}
```

字段说明（2026-09-07 增补，09-08 修正）：

```text
runtime_path      bind 时运行时路径快照（升级悬空检测 + tick 自愈回写，见 §10.5）
automation_model  bind/replace 时从 DB 读回的 automation.model 快照（Flash 校验）
```

不保存：

- provider credential；
- prompt 内容；
- model response；
- quota token；
- account secret。

一个 provider identity 已有 bound clock 时：

```text
第二次 setup → fail
```

除非显式执行 replace/rebind。

这样机械保证：

> one provider identity → one Global Quota Clock。

---

# 6. ZCode SQLite Host Adapter

## 6.1 新模块

建议新增：

```text
plugins/glm-conductor/runtime/host/
├── __init__.py
└── zcode_schedule.py
```

职责必须严格限制。

公开 API 建议：

```python
discover_zcode_tasks_db(...)
inspect_automation(...)
retime_automation(...)
```

不要构造一个通用 ZCode automation framework。

---

## 6.2 retime_automation()

唯一生产写操作：

```text
UPDATE automations
SET next_run_at = ?
WHERE automation_id = ?
```

具体实际 column name 必须以当前 ZCode DB 实测 schema 为准，不允许凭计划猜测。

2026-09-07 已有一手材料：`cron-selfupdate-test/automation-backup.json` 为 automations
表 34 列完整实测快照（`next_run_at` epoch ms / `recurring` / `enabled` /
`lifecycle_status` / `target_task_id` / `schedule_rule` / `model` 等），
DB 位于 `~/.zcode/v2/tasks-index.sqlite`，WAL 模式外部写实测成功；
W1 开工前仅需 PRAGMA 复核无版本漂移。inspect 侧可读同库 `automation_runs` 表
（scheduled_at / session_id / run_count）作为 fire 的被动健康证据
（宿主 jsonl 日志中无独立 automation fire 事件，DB 表是唯一被动证据源）。

写入前必须：

1. DB 文件存在；
2. schema 存在；
3. `automations` 表存在；
4. required columns 存在；
5. automation_id 恰好匹配一行；
6. automation 仍为预期 recurring task；
7. 当前 provider clock binding 与 automation_id 一致。

执行：

```text
busy_timeout
BEGIN IMMEDIATE
SELECT current row
UPDATE one column
SELECT read-back
COMMIT
```

若 read-back 与 target 不一致：

```text
ROLLBACK / error
```

---

## 6.3 硬安全边界

v2.3 adapter 禁止：

```text
INSERT
DELETE
ALTER TABLE
DROP
修改 prompt
修改 target_task_id
修改 recurring
修改 max_runs
修改 scheduleRule
```

Production adapter 只有：

```text
READ
UPDATE next_run_at
```

这点需要单元测试机械锚定。

---

## 6.4 ZCode 内部接口兼容风险

`tasks-index.sqlite` 属于 ZCode 内部实现，不是稳定公开 API。

因此：

```text
ZCodeScheduleAdapter
```

必须与 quota/domain logic 隔离。

未来如果 ZCode 支持：

```text
CronUpdate in trigger round
或
setNextRunAt API
```

只替换 adapter：

```text
SQLite adapter
     ↓
Official API adapter
```

Global Clock 算法完全不变。

---

# 7. Runtime 新模块

建议新增：

```text
runtime/
├── host/
│   └── zcode_schedule.py
└── quota/
    ├── clock.py
    └── clock_store.py
```

## quota/clock.py

只负责纯决策：

```text
select five_hour window
calculate target
classify target:
    reset_target
    short_retry
    watchdog_fallback
```

尽量不直接处理 SQLite。

建议纯函数：

```python
select_five_hour_window(...)
next_clock_target(...)
```

便于完整单元测试。

## quota/clock_store.py

负责：

- global state path；
- schema；
- load/save；
- provider identity 单例；
- atomic write。

复用现有：

```text
durable_io
```

不要再实现一份 tempfile/os.replace。

---

# 8. CLI

建议增加四个核心子命令。

## quota-clock-plan

```text
quota-clock-plan <repo_root>
```

功能：

- force-refresh；
- provider identity；
- five_hour reset_at；
- first_target；
- fallback interval；
- 输出 Scheduled Task prompt。

纯规划，不写 ZCode DB。

---

## quota-clock-bind

```text
quota-clock-bind <repo_root> <automation_id>
```

作用：

1. 检查 automation（含从 DB 读回 model/mode/thought_level 写入 state，非 Flash 输出 warning）；
2. 建立 global clock state（含 runtime_path 快照；clock 常驻无窗口预算，见 §3.2）；
3. 首次设置：

```text
next_run_at = reset_at + grace
```

bind 必须从普通交互回合执行（推荐在 Flash + 最低思考强度会话中执行，见 §3.3）。

---

## quota-clock-tick

```text
quota-clock-tick <repo_root>
```

Scheduled Task 的唯一主要入口。

执行：

```text
force refresh
→ calculate
→ retime
→ verify
→ persist
```

stdout 返回固定 JSON，至少包含：

```text
status
provider_identity_hash
reset_at
next_target_at
automation_id
retimed
reason
```

---

## quota-clock-status

```text
quota-clock-status <repo_root>
```

纯读：

- bound automation；
- provider identity；
- automation model（是否 Flash）；
- last reset；
- next target；
- DB 当前 next_run_at；
- 是否一致；
- last tick；
- runtime_path 是否存在（升级悬空检测）；
- automation_runs 最近 fire 证据；
- health（判据：last_tick_at 陈旧超 2×预期 / DB 行缺失 / runtime_path 悬空，
  任一命中 → unhealthy，并给出 replace 建议）。

---

# 9. Global Clock Scheduled Prompt

Prompt 必须压到数十 token 量级（2026-09-07 裁决：token 成本最小化）：

```text
GLM CONDUCTOR QUOTA CLOCK TICK
Run: python3 <runtime_path>/runtime/cli.py quota-clock-tick <repo_root>
If that path is missing, locate runtime/cli.py under the current
glm-conductor plugin cache and run it there.
Reply with the tick JSON in one line, then end the turn.
Never call Cron tools. Never do anything else.
```

tick automation 的三个成本杠杆（2026-09-07 裁决）：

```text
model         = GLM-5.3-Flash（单次调用最小化）
thought_level = 最低档（思考 token 最小化）
prompt        = 上述极简版（注入体积最小化）
```

历史累积项由 auto-compact 天然封顶：tick prompt 自包含无状态（每轮全文重发、
不依赖对话历史），压缩对 tick 无害；空闲期消耗由 §3.2 窗口预算封顶。

目标是：

> model 负责产生真实 turn，确定性 runtime 负责其他一切。

---

# 10. Task Wake Bridge 改造

Global Clock **不能替代 task wake bridge**。

原因：

```text
global clock automation
→ 只能向 quota-clock session 投递 prompt
```

它无法把 turn 投递给另一个 project/task session。

因此仍然需要：

```text
Task Wake Bridge
```

但职责变得非常简单：

> 只负责在额度恢复后重新激活该 task session。

---

## 10.1 保留 recurring_bridge 名称

暂时不要增加：

```text
retimed_bridge
dynamic_bridge
```

也不要把 `self_retiming` 提升成新的用户可见 transport。

现有 automation 实际仍然是：

```text
recurring Scheduled Task
```

只是 normal timing 使用动态 `next_run_at`。

因此继续：

```text
activation_transport = recurring_bridge
```

最少破坏现有 state/API/test。

---

## 10.2 bridge_interval_minutes 重新定义

保留现有字段：

```text
bridge_interval_minutes = 60
```

但改变其文档语义：

**旧：**

```text
正常 scheduled wake interval
```

**新：**

```text
native recurring watchdog interval
```

正常 wake 时间来源改为：

```text
next_wake_at
```

---

## 10.3 waiting_quota 时

task 进入 waiting quota：

```text
当前 five_hour reset_at = A
Global Clock grace = 120s
Task resume lag = 60s
```

建议：

```text
task_wake_at = A + 180s
```

即：

```text
reset
  ↓
+120s
Global Clock tick
  ↓
窗口确认/物化
  ↓
+60s
Task Wake
  ↓
force-refresh
  ↓
quota-resume
```

task bridge 使用同一个 ZCode adapter：

```text
next_run_at = task_wake_at
```

不再依赖“等下一次 60 分钟 recurring”。

---

## 10.4 wake 后仍不可执行

如果 task session 醒来后：

```text
quota still unavailable
```

则：

```text
next_run_at = now + 5min
```

或者如果 provider 给出了新的 blocking reset：

```text
next_run_at = blocking reset + appropriate grace
```

保持简单优先。

## 10.5 Clock 失联自动 replace（2026-09-07 裁决：零手动）

触发面（全自动，用户唯一动作 = 照常使用 ZCode）：

```text
插件 SessionStart hook（hooks/session_start.py，槽位已存在）：
新会话启动时检查 clock 健康（读 state + DB 行 + runtime_path），
unhealthy 则向该会话注入"clock 失联，请执行 quota-clock-bind --replace"上下文；
任何 conductor CLI 调用顺带同一健康检查。
```

replace 流程（turn 内自动完成）：

```text
1. CronCreate 新 automation（Flash + 最低思考强度 + §9 极简 prompt）
2. adapter 将旧 automation 行 park：next_run_at = now + 365 days
   （不执行 DELETE——§6.3 禁列；parked active 行惰性残留无害，
    宿主自动清理仅覆盖 completed 行）
3. clock state 迁移至新 automation_id（status = rebound）
```

已知宿主代价（文档化）：新宿主会话被 clock 占用（one-automation-per-session），
此后该会话不能再 arm task bridge；task 侧跨会话恢复不受影响（账本在仓库）。
W3 在 arm 时增加"本会话已被 clock 占用"早报错检查。

升级自愈（三层，2026-09-07 裁决全上）：

```text
(a) state 快照 runtime_path，status 检测悬空
(b) tick 发现实际运行路径 ≠ 记录时自愈回写 state（升级后首次 fire 即自动修复）
(c) prompt 内置路径失效恢复指令（见 §9）+ upgrade runbook 文档
```

净效果：常规插件升级与 clock 会话死亡均零人工干预。

---

# 11. completed bridge 处理

当前触发回合内 CronDelete 已经被实测证明不可靠。

因此 v2.3 不应该继续把：

```text
trigger → CronDelete self
```

描述成可靠路径。

实施前增加一个宿主实验：

### ZC-PARK-01

验证：

```text
trigger round
UPDATE next_run_at = now + 365 days
```

是否：

- 精确保留；
- 不触发 recurring grid；
- App restart 后仍保持。

若验证通过：

completed task：

```text
tombstone
→ park bridge far future
```

以后用户在普通交互回合/UI 可以真正删除。

若 PARK 实验不通过：

v2.3 保留当前 cleanup 行为，但必须在文档中明确列为 host limitation，不允许假装 CronDelete reliable。

---

# 12. Watcher 的处理

不删除 `watcher.py`。

但从架构上正式降级：

### Watcher 以后只负责

```text
task execution quota observation
diagnostics
optional active-work observation
```

### Watcher 不再负责

```text
maintaining quota window continuity
activation timing
window materialization
```

因此：

```text
Quota Watcher ≠ Quota Clock
```

v2.3 不需要为了 Global Clock 启动 watcher。

这应成为明确的 public documentation。

---

# 13. Window Primer 的处理

v2.3 不删除 `primer.py`，避免一次性大范围破坏测试和历史 API。

但正式定义：

```text
Scheduled Clock Tick
= production window materialization path
```

Primer：

```text
default disabled
manual / experimental fallback only
```

本轮：

- 不接 watcher；
- 不接 clock；
- 不接 resume；
- 不扩大授权。

稳定运行一个版本后，可以在后续版本决定是否删除 Primer。

---

# 14. Epoch 的处理

保留 quota epoch。

但它的职责收缩为：

```text
window identity
idempotent quota-window consumption
before/after window verification
```

不再把 epoch boundary 当作复杂 scheduling subsystem 的中心。

一句话：

> **Epoch 是窗口身份证，不是闹钟。**

---

# 15. 现有文件级改动

## 新增

```text
plugins/glm-conductor/runtime/host/__init__.py
plugins/glm-conductor/runtime/host/zcode_schedule.py
plugins/glm-conductor/runtime/quota/clock.py
plugins/glm-conductor/runtime/quota/clock_store.py

tests/test_zcode_schedule.py
tests/test_quota_clock.py
tests/test_quota_clock_cli.py
```

---

## 重点修改

```text
runtime/cli.py
runtime/continuity/wake_bridge.py
runtime/activation_transport.py
runtime/execution_policy.py
runtime/quota/watcher.py
runtime/quota/primer.py
hooks/session_start.py（clock 健康检查与 replace 注入，2026-09-07 增）
skills/continuity/SKILL.md
```

其中：

### activation_transport.py

不重构接口。

只更新稳定 transport 语义：

```text
recurring_bridge:
persistent recurring automation
+
dynamic next_run_at retiming
+
native recurrence watchdog
```

`self_retiming` 暂继续保留 reserved，避免 vocabulary migration。

### execution_policy.py

保留：

```text
bridge_interval_minutes
```

默认仍为：

```text
60
```

但改成：

```text
fallback/watchdog interval
```

而不是 normal wake interval。

### wake_bridge.py

重点删除/弱化：

```text
固定 recurring interval = normal timing
```

改为：

```text
wake_at = exact target
bridge_interval = fallback
```

Universal Wake Prompt 同时大幅缩短。

---

# 16. 暂时明确不删除的组件

v2.3.0 不做“代码洁癖式删除”。

以下先保留：

```text
observer.py
watcher.py
primer.py
epoch.py
scheduler.py
subscription/accounting
legacy wake APIs
```

先改变生产主路径。

运行一个稳定版本以后，再根据：

```text
实际调用图
测试覆盖
dogfood
```

决定 v2.4 是否删除 dead path。

这样风险远低于一次性重写整个 v2.2 quota subsystem。

---

# 17. 实施工作包

## W0 — 冻结 ZCode 宿主事实

产出一份历史实验记录：

```text
docs/history/v2.3.0/
ZCode-Scheduled-Task-Retime-Experiments.md
```

写入目前实测结果（凭据链 2026-09-07 已闭合：
`C:/Users/user/ZCodeProject/cron-selfupdate-test/`（result.md / exp-c-timeline.md /
automation-backup.json）+ memory reference-zcode-cron-automation-facts）：

- same-session；
- one automation/session；
- trigger CronUpdate suppressed（9/9）；
- 200min upper bound；
- off-grid next_run_at 精确执行；
- dispatch-time grid rewrite；
- completion no rewrite；
- dynamic value one-shot semantics；
- 非触发回合 CronUpdate 可行（正样本×1）；
- completed 行自动清理 / parked active 行不清理。

并补做（以下四项仍无数据，维持必做）：

### 必做宿主实验

```text
ZC-01  ZCode restart 后 dynamic next_run_at
ZC-02  Windows sleep 跨 next_run_at
ZC-03  SQLite busy/lock
ZC-04  long-future parking（同时服务 §10.5 park 语义与 §11 completed bridge）
```

这些是 release gate，不进入 CI。

---

## W1 — ZCode Schedule Adapter

完成：

```text
runtime/host/zcode_schedule.py
```

验收：

- temp sqlite unit tests；
- 只改一行一个字段；
- schema mismatch fail closed；
- id mismatch fail closed；
- read-back verification；
- no credential；
- Windows path + override；
- Linux CI 可用 temp fixture。

---

## W2 — Global Quota Clock

完成：

```text
clock.py
clock_store.py
quota-clock-plan
quota-clock-bind
quota-clock-tick
quota-clock-status
```

验收：

```text
同一 automation 连续跨至少 3 个真实 5h 窗口
automation_id 始终不变
每轮 next_run_at = 最新 reset_at + grace
无 per-window CronCreate
automation model = Flash（DB 读回校验）
weekly blocked → 长 park 到 weekly reset + grace，无 5 分钟空转
```

---

## W3 — Task Wake Bridge 精确化

将现有 Wake Bridge 从：

```text
60m polling
```

切成：

```text
exact wake_at + recurring watchdog
```

retime 双锚点（2026-09-07 补）：

```text
(1) arm 时：主会话创建 automation 后立即 next_run_at = task_wake_at
    （arm 流程内联调用 adapter）
(2) 每次 fire 后：wake turn 的 quota-resume / no-op / 短重试路径
    各自写入下一个 target——否则一次 fire 后即落回 60m 网格
```

验收：

```text
waiting_quota
→ exact boundary wake
→ force refresh
→ authorized resume
```

正常路径不得等待完整 watchdog interval；
arm 时对"本会话已被 clock automation 占用"给出早报错
（one-automation-per-session 冲突，见 §10.5 宿主代价）。

---

## W4 — 旧 quota continuity 职责收敛

更新：

```text
watcher
primer
activation_transport
execution_policy comments/docs
continuity skill
```

但不大规模删代码。

---

## W5 — Regression / Dogfood / Release

全测试：

```text
python -m unittest
ruff
plugin validation
```

保持当前 Ubuntu / Windows、Python 支持矩阵。

真实 ZCode dogfood 至少覆盖：

```text
3 个连续 5h window
一次 provider refresh failure
一次 SQLite retime failure
一次 ZCode restart/sleep
一次 task quota exhaustion → automatic wake
```

---

# 18. 测试矩阵

## Unit

必须覆盖：

### Adapter

```text
valid retime
unknown automation
duplicate/ambiguous match
bad schema
locked DB
read-back mismatch
invalid timestamp
only-next_run_at-mutated
```

### Clock decision

```text
fresh future reset
past reset
unchanged old reset
missing five_hour
weekly + five_hour（weekly blocked → 长 park target）
provider stale
provider error
identity mismatch
retry target
```

### Clock store

```text
atomic write
single provider identity
duplicate bind rejection
legacy/malformed state
```

### Wake Bridge

```text
exact wake target
fallback interval preserved
retime failure → watchdog
quota unavailable → short retry
no nested CronCreate
```

---

# 19. Release Acceptance Criteria

v2.3.0 发布必须同时满足：

### Clock

- [ ] 一个 provider identity 只有一个 Global Clock；
- [ ] 一个 automation identity 连续跨 ≥3 个窗口；
- [ ] 正常 next wake 使用真实 `reset_at + grace`；
- [ ] 无 `A + N×5h` 静态推算；
- [ ] 无 per-window CronCreate；
- [ ] 正常情况下无 60min quota-window polling；
- [ ] weekly blocked 时长 park 到 weekly reset + grace，无 5 分钟空转；
- [ ] tick automation = Flash + 最低思考强度 + 极简 prompt；
- [ ] retime 失败后 native recurring 能恢复；
- [ ] clock 会话失联后可由 SessionStart hook 触发全自动 replace（park 旧、不删除）；
- [ ] watcher 未运行时 clock 仍正常；
- [ ] primer disabled 时 clock 仍正常。

### Task continuity

- [ ] waiting task 不再随机等待最多 60 分钟；
- [ ] task wake 与 Global Clock 保持职责隔离；
- [ ] wake 后仍执行 force-refresh；
- [ ] auto_resume authorization 不变；
- [ ] quota-window accounting 不变（含 continuity.max_quota_windows 任务侧窗口
      预算上限语义，clock 不读不写，见 §3.2）；
- [ ] epoch 幂等语义不变。

### Safety

- [ ] 无凭证写入 Global Clock state；
- [ ] SQLite adapter 不支持 INSERT/DELETE；
- [ ] SQLite adapter 不支持 scheduleRule 修改；
- [ ] schema 不兼容时 fail closed；
- [ ] adapter 故障不阻塞普通 task execution。

---

# 20. 本版本明确不做

为了控制复杂度，以下全部排除在 v2.3.0：

```text
× burn-rate quota prediction
× dynamic task cost estimation
× 新 quota phase
× 修改 35/20 thresholds
× session injector
× event bus
× background daemon for clock
× watcher → model activation
× per-window automation chain
× generalized ZCode scheduler framework
× automatic DB schema migration
× 删除整个 primer subsystem
× 删除整个 watcher subsystem
```

---

# 21. 文档收敛

公开文档最终只表达下面这个模型：

```text
                      PROVIDER ACCOUNT
                            │
                            ▼
                 ┌────────────────────┐
                 │ GLOBAL QUOTA CLOCK │
                 │                    │
                 │ one automation     │
                 │ reset_at + grace   │
                 │ materialize window │
                 └─────────┬──────────┘
                           │
                    current quota facts
                           │
            ┌──────────────┴──────────────┐
            │                             │
            ▼                             ▼
     Task Execution                 Task Wake Bridge
     quota policy                   session activation
```

并明确：

```text
Quota Clock    = 窗口什么时候持续滚动
Task Policy    = 当前额度怎么花
Wake Bridge    = dormant task 什么时候重新获得 turn
Watcher        = 可选观察
Epoch          = 窗口身份
Primer         = 非主路径 fallback
```

这是 v2.3 最重要的概念收敛。

---

# 22. 推荐实施顺序

严格按：

```text
W0 host experiments
        ↓
W1 SQLite adapter
        ↓
W2 Global Quota Clock
        ↓
真实连续 3-window dogfood
        ↓
W3 Task Wake Bridge
        ↓
W4 old-path/documentation cleanup
        ↓
full regression
        ↓
v2.3.0 release
```

禁止一开始同时修改：

```text
watcher + primer + epoch + wake bridge + task quota policy
```

先证明 Global Clock 独立可靠，再动 task activation。

---

# 23. 最终成功标准

v2.3.0 完成以后，用户视角应当只剩两个简单问题：

### 额度窗口

> “5h 窗口会不会因为长期 idle 而停止连续刷新？”

由：

```text
Global Quota Clock
```

负责，答案应为：

```text
不会；每轮按真实 reset_at 自动 retime。
```

### 当前任务

> “这一窗口还有多少额度，我现在应该怎么执行？”

由：

```text
Task Execution Policy
```

负责。

两者之间不存在控制依赖。

Task Wake Bridge 仅承担一个技术职责：

> 当任务因为 quota dormant 时，在合适时间重新给原 session 一个 turn。

这就是 v2.3.0 应达到的最终架构边界。