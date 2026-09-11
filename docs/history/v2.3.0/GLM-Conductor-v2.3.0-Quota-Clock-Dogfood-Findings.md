# GLM-Conductor v2.3.0 Global Quota Clock 首次真实部署发现记录（Dogfood Findings）

> **性质**：独立新增记录文档，未改动任何既有文档。记录 2026-09-09 在真实用户环境首次部署 Global Quota Clock（Scheduled Clock Tick 路径）与 Quota Watcher 过程中发现的问题，供后续开发改进。
>
> **环境**：Windows 10 (19042) / Git Bash / ZCode CLI；装机插件缓存 `glm-conductor` **2.3.0**；dev 树 `C:\Users\user\ZCodeProject\glm-conductor`（含 **2.3.1-dev** 增量）。执行者为主会话（builtin:bigmodel-coding-plan / GLM-5.3-Flash）。**本文逐处标注结论出自装机版实测还是 dev 树源码判读，两者不混淆。**

## 0. 发现总览

| # | 级别 | 发现 | 2.3.0 装机 | 2.3.1-dev |
|---|------|------|-----------|-----------|
| F-1 | **P1** | 死绑定 replace 路径结构性死锁：`quota-clock-bind` 拒绝改绑且无任何解绑原语，恢复指引指向前必败命令 | 存在 | **原样存在**（`clock_store.py:215-217` 未变；新增 recovery_guidance 仍指向同一死路） |
| F-2 | P1→P2 | Clock 链死亡无告警面；SessionStart advisory（w1-session-advisory）补齐 tick_stale 启发式但有结构性盲区 | 完全没有 | 部分补齐，盲区见 §3.3 |
| F-3 | P2 | 首次 bind 后即报 `unhealthy`（tick_stale）——"新装即不健康"的自相矛盾呈现 | 存在 | 已收敛（`quota_clock.py:371-375`），待回归验证 |
| F-4 | P2 | `quota-clock-plan` 的 `cli_command` / `suggested_prompt` 硬编码 `python3`，Windows 首跳必失败 | 存在（`cli.py:1607`） | **原样存在**（`commands/quota_clock.py:140`） |
| F-5 | P3 | 观察项四条（runtime_path 自愈覆盖面 / owner_repository 迁移语义 / 存储层级不对称 / prompt 内容漂移不可校验） | — | — |

---

## 1. 部署结果与验证证据

部署动作（全部成功）：

1. 宿主 automation `automation-f089c3e7-04b8-499d-a3f9-f390577bde88`（标题「额度窗口定时刷新激活（Quota Clock tick，每60分钟watchdog）」，recurring 每小时，绑定交互会话）
2. `quota-clock-bind` → `{"status": "bound", "first_target_epoch_ms": 1788900647000, "retimed": true}`——DB 层 `next_run_at` 成功锚定到 reset_at+120s
3. `quota-clock-status`（绑后）→ `db_next_run_at: 1788900647000`，`target_matches_db: true`（首次 tick 前的 `unhealthy/tick_stale` 见 F-3）
4. `quota-watcher start` → pid 196188，PASSIVE，heartbeat 正常；首次观察 `AVAILABLE`，five_hour reset `2026-09-08T20:48:47Z`，epoch `glm:82861f88f62a9a11`

本次部署全程未做任何 primer 类 control-plane 模型调用，窗口物化完全交给 Scheduled Clock Tick 路径——与「primer 默认结构性关闭、生产物化路径 = Clock tick」的 v2.3 定位一致，未构成对 P0-QP 系列结论的挑战。

---

## 2. F-1：死绑定 replace 路径结构性死锁（P1）

### 2.1 现象与证据（装机 2.3.0 实测时间线）

1. 部署前 `quota-clock-status`：clock state 绑定到旧 automation `automation-ebb3f72d-9689-4731-afa9-59c475cc7617`，但 `db_next_run_at: null`、reasons 含 **`db_row_missing`**——宿主 DB 中该 automation 行已不存在（ZCode 宿主 CronList 独立交叉确认：列表中无此 id）
2. 按状态机自带建议执行 `quota-clock-bind <new_automation_id>`（suggestion 原文：`"replace: run quota-clock-bind <new_automation_id> from a fresh interactive session"`）：
   ```
   ClockStateConflictError: bind_clock_state：provider c2f1cd0f6fcca2fe 的 clock
   已绑定到 automation 'automation-ebb3f72d-...'，拒绝改绑
   'automation-f089c3e7-...'（原 state 未动）
   ```
3. 全 CLI 子命令清单中**不存在任何 unbind / replace / force 原语**

即：**状态机自己建议的 replace 命令，在唯一需要它的场景（db_row_missing）里必然失败。** 恢复指引是一条自指死路。

### 2.2 根因（三个事实叠加）

1. **冲突检查过窄**：`bind_clock_state` 的 updater 在 `current.status == "bound"` 且 automation_id 不同时无条件抛错（cache `cli.py` 内联实现与 dev 树 `runtime/quota/clock_store.py:215-217` 同款，**2.3.1-dev 未改动**）。冲突守卫的合理意图是防止抢走活绑定，但它不区分「活绑定」与「宿主行已消失的死绑定」。
2. **`stale` / `rebound` 是死词汇**：`CLOCK_STATUSES = ("bound", "parked_weekly", "rebound", "stale")`（`clock_store.py:69`）中，`parked_weekly` 由 tick 写入，而 **`stale` 与 `rebound` 在 cache 与 dev 树全部 runtime 源码中均无任何写入点**（grep 级核实）。词汇表预留了正确的出口状态，却没有通往它的路。
3. **指引链自指**：2.3.0 status 的 suggestion、2.3.1-dev 新增的 `recovery_guidance`（`commands/quota_clock.py:360-361, 475-476`）与 SessionStart advisory（`hooks/session_start.py:174-176`）三者给出的恢复指令都是 `quota-clock-bind <new_automation_id>` replace——全部撞上第 1 条的同一堵墙。

### 2.3 影响

- Clock 链一旦死亡（宿主 automation 被清理/会话销毁/升级换绑），**没有一条文档化、可机械执行的恢复路径**；唯一出口是调用方对用户级 state 文件做运行时外科手术（见 2.4），这违背「runtime 调用入口一律走 CLI」的项目自身纪律
- 对普通用户而言，该状态等于 Clock 永久失联

### 2.4 本次实际采用的绕过（供复现对照，勿视为正式路径）

临时脚本经 runtime 自身 API（`clock_store.update_clock_state`，独占锁 + 原子写）把死绑定转为 `stale` 后重新 bind 即成功（bind 冲突检查只拦 `status=="bound"`）。脚本带严格守卫：

```python
if st.get("automation_id") == <已确认 DB 行缺失的 id> and st.get("status") == "bound":
    # 仅此精确匹配才置 status = "stale"，其余一律 no-op
```

### 2.5 改进建议（按优先序）

1. **bind 内建死绑定自愈（首选）**：bind 遇 `status=="bound"` 且 id 不同时，先对现存绑定的 automation_id 做 DB 行检查；确认 `db_row_missing` → 在同一独占锁事务内先把旧 state 转 `stale` 再写新绑定；DB 检查失败/不确定 → 维持现有 ClockStateConflictError（fail-closed）。冲突守卫的初心（保护活绑定）完整保留
2. 独立 `quota-clock-unbind`（或 `--replace` 旗标）子命令——显式原语，便于审计与测试锚定
3. 最低限度：修正 status `suggestion`、`recovery_guidance` 与 advisory 文本，使其指向真实可执行路径（1 或 2 落地前的过渡文案必须包含 stale 转换步骤）

---

## 3. F-2：Clock 链死亡的告警面（P1→P2）

### 3.1 装机 2.3.0：完全静默

旧链 `last_tick_at = 2026-09-08T05:49:47Z`，至当日晚（≈17:14Z）已停摆 ≥11.5 小时，期间**无任何主动告警**；本次是用户主动要求重建额度基础设施才顺带发现。链死亡的原因无法事后定位（宿主行消失可能源于会话/automation 清理或 2.2→2.3.0 缓存升级窗口），这本身佐证了告警面的必要性。

### 3.2 2.3.1-dev advisory 评述（源码判读，`hooks/session_start.py:91-187`）

w1-session-advisory 的设计干净：state-file-only 单次容错 JSON 读（绝不打开 SQLite）、fail-open 静默降级、零噪音红线、tick_stale 口径与 quota-clock-status 逐条镜像。**实证回放**：本次会话启动时刻 last_tick 停摆已 ≈11.5h > 2×fallback(60min) 阈值——若 2.3.1 已装机，advisory 本应注入。装机版缺它正是 2.3.0 静默的直接原因。

### 3.3 残余盲区（2.3.1 落地后仍需处理）

1. **db_row_missing 结构性不可感知**：advisory 按设计不打开 DB，只能靠 tick_stale 作收敛代理（行死后不再有 tick 刷新 last_tick_at，经 2×fallback 阈值后必然转陈旧）。代理最终会收敛，但存在最长 2×fallback 的告警延迟，且无法区分「停摆」与「宿主行丢失」这两种处置紧迫度不同的形态
2. **指引与 F-1 复合**：advisory 与 recovery_guidance 都把用户引向 `quota-clock-bind` replace——在死绑定场景即 F-1 的死路。**F-1 不解，F-2 的告警只是把用户领到墙下**
3. （机会性）watcher 是常驻 poll-only 观察面，其 tick 间隙顺带对 clock state 做只读诊断读数是否越权（W4 职责边界），值得设计讨论——若允许，可将死链发现延迟从「下次会话启动」压缩到「下个 watcher tick」

---

## 4. F-3：首次 bind 后即报 unhealthy（P2）

装机实测：bind 成功且 `target_matches_db: true` 的当下，`quota-clock-status` 返回 `health: "unhealthy"`、reasons `[tick_stale]`（last_tick_at=null）、suggestion `"await next tick"`——「不健康」标签与「等待即可」建议同框自相矛盾，用户无从判断是否需要行动。

dev 树已收敛（`commands/quota_clock.py:371-375`）：首次 tick 前不判 target_mismatch、只给「await next tick」级提示、不触发 replace 建议。建议回归验证时追加一条断言：**首绑未 tick 场景 health 不得为 unhealthy**（或引入 `provisioning` / `awaiting_first_tick` 独立呈现值）。

---

## 5. F-4：plan 产物硬编码 `python3`（P2）

- 位置双确认：cache `cli.py:1607`（`tick_command = "python3 %s quota-clock-tick %s"`）与 dev 树 `commands/quota_clock.py:140`，**2.3.1-dev 未修**
- 本机 Windows 无 `python3` 启动器（launcher 只注册 `python`）。suggested_prompt 内置了「路径缺失时自行定位 cli.py」的兜底措辞，但模型照词执行首跳必失败一次，靠兜底措辞补救属于把可消除的错误留给运行时
- 建议：plan 时用 `sys.executable` 生成 tick_command，或安装期探测可用解释器
- 部署事实记录：实际创建的 automation prompt 已由部署会话人工将 `python3` 改为 `python`——**runtime 无法校验宿主 automation 的 prompt 内容**，state 与 prompt 之间存在不可检漂移（本次漂移无碍 retime 锚定，但校验缺位值得知晓）

---

## 6. F-5：观察项（P3）

1. **runtime_path 升级自愈覆盖面**：旧 state 的 `runtime_path` 指向 dev checkout（`ZCodeProject\glm-conductor\...`），§10.5 自愈只挂在 tick 路径；本次经 bind 重写为 cache 路径后消除。若链死在升级窗口且用户走 F-1 绕过路径（不经 tick），失真 runtime_path 会一直留存——自愈逻辑是否应前移到 bind/status 写路径，可一并考虑
2. **owner_repository 随改绑迁移**：clock state 按 provider identity 寻址（account 级），改绑后 owner 从 dev 仓变为 ZCodeProject，「最后 bind 者持有」符合设计但应在用户文档写明（多仓库用户的排障入口）
3. **存储层级不对称**：watcher 状态在 repo 级 `<repo>/.glm-conductor/quota/`，clock state 在用户级 `~/.glm-conductor/quota-clocks/`——语义自洽（观察面 per-repo、时钟 account 级），但排障时容易找错位置，文档值得给一张「哪个状态在哪」的对照表
4. **双真相源窗口**：dev 树 2.3.1-dev 与装机 2.3.0 并存期间，实测结论与源码判读可能分叉（本文已逐处标注）；建议 release 流程把「装机缓存版本」纳入 dogfood 记录模板

---

## 7. 部署遗留状态快照（供回归对照）

| 项 | 值 |
|---|---|
| Clock automation | `automation-f089c3e7-04b8-499d-a3f9-f390577bde88`（recurring 每小时 watchdog；实际触发由 DB `next_run_at` 精确锚定） |
| 首次 tick 目标 | `1788900647000`（reset `2026-09-08T20:48:47Z` + 120s） |
| Clock state | `~/.glm-conductor/quota-clocks/c2f1cd0f6fcca2fe.json`（provider hash `c2f1cd0f6fcca2fe`，宿主 DB `~/.zcode/v2/tasks-index.sqlite`） |
| Watcher | pid 196188（start 时刻），PASSIVE；状态/日志 `<repo>/.glm-conductor/quota/watcher.{json,log}` |
| 部署时已被清理的死绑定 | `automation-ebb3f72d-9689-4731-afa9-59c475cc7617`（已转 `stale` 后被新 bind 覆盖） |
| 用户自有无关任务 | 「额度刷新-5：00」（cron `0 5 * * *`，paused，prompt 为 "test"）——部署会话未触碰 |
