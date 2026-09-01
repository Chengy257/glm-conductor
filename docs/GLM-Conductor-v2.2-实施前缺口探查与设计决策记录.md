# GLM Conductor v2.2 实施前缺口探查与设计决策记录

> **文档类型**：Pre-Implementation Gap Analysis & Design Decision Record
> **对应主计划**：`docs/GLM-Conductor-v2.2-Quota-Continuity-Control-Loop-Implementation-Plan.md`（Implementation Ready）
> **基线**：v2.1.0 stable（`857f55a`）
> **状态**：决策已冻结（用户 2026-09-01「按推荐」确认全部 14 项）
> **任务账本**：`.glm-conductor/tasks/v22-loop-857f55a/`（route: full / high / high / flash-implementer / resumable）

---

## 一、证据来源

主会话（GLM-5.3）对 v2.1 基线的全量精读（2026-09-01），全部结论有代码行号锚点：

- `runtime/quota/scheduler.py`（383 行）——四态评估 + plan_resume 纯决策
- `runtime/quota/resolver.py`（333 行）——四级层级 + quota-cache.json 结构
- `runtime/quota/credentials.py`（135 行）——固定单键 provider 识别
- `runtime/execution_policy.py`（568 行）——授权事实源 + effective_worker_budget
- `runtime/state.py`（1130 行）——TASK_TRANSITIONS 状态机 + validate_state
- `runtime/task_manager.py`（2035 行）——quota 连续性链路全量（prepare 预算折算、
  handle_quota_exhausted:1544、quota_wake_prompt:1656、record_quota_wake:1730、
  resume_from_quota:1928）
- `hooks/stop_gate.py`（1004 行）——四重检查 + gate_exhausted 统一机器
- `hooks/{pre_tool_use,post_tool_use,session_start}.py` + `hooks/hooks.json`
- `runtime/{resume_manifest,recovery,journal,cli}.py`
- ZCode 官方 hook 文档（zcode-guide 插件 diagnosing-hooks）
- 项目记忆：CronDelete/Update glitch 教训、宿主 cron 20 槽位上限、
  2026-08-31 wake 入账本实测（同会话 user-turn 注入）

## 二、宿主能力实锤（风险项关闭）

| 能力 | 证据 | 结论 |
|---|---|---|
| 宿主有原生 scheduled automation | 本会话暴露 CronCreate/CronList/CronUpdate/CronDelete 工具，语义含 recurring=false + maxRuns=1 | 文档 §26 猜测的 tool 名中 `CronCreate` 与本环境吻合，但仍需 M0 实测 payload |
| hook matcher 匹配 tool name | 官方文档：matcher 为大小写敏感正则，测试对象是 tool name（Bash/Read/Write/Edit/Agent 等） | Cron 工具名理论上可匹配；是否走标准 PreToolUse 管道待 M0 实测 |
| PreToolUse 可 deny | 官方文档：可返回 allow/ask/deny；exit 2 blocks | wake permit 校验可实现 |
| Stop 续行上限 3 次 | 官方文档 + stop_gate GATE_BLOCK_LIMIT=2 实现 | continuation gate 复用统一机器可行 |
| UserPromptSubmit 事件存在 | 官方文档七事件之一 | 可能是 wake fire 机械记账挂点，M0 实测 automation 注入 turn 是否触发 |
| unittest 本地可跑 | v2.1 dogfood 任务 verification.required 惯例 `python3 -m unittest discover -s tests` | 本机验证口径确定（pytest 收集崩，CI 为权威门） |

## 三、宿主硬约束（主计划未覆盖，本轮新发现）

### H-A — Stop hook 超时与 provider 超时撞满

`hooks.json` Stop `timeoutMs=5000`；provider 抓取默认 timeout 5.0s。
Stop Gate 的 continuation check 若走网络必然超时 → 整个 Stop hook fail-open
降级放行 → 义务门形同虚设。**决策 D2 因此冻结为只读缓存零网络。**

### H-B — warm-only bridge 与 v2.1 授权记账体系冲突

`record_quota_wake`（task_manager.py:1803）对 auto_resume ∉ {auto_once,
until_done} 直接抛 TaskManagerError；v2.1 授权不变量冻结 manual/notify →
`max_quota_windows == 0`。主计划 §7.3 要求 manual/notify 也 arm Wake Bridge
（warm-only）。**解法：新增 `record_wake_bridge`（记 continuation.wake_bridge
全字段 + journal），与窗口预算扣减完全解耦；授权族 bridge 两个记账都调，
warm bridge 只调前者。**

### H-C — 宿主 wake 是同会话 user-turn 注入、SessionStart 不重放

2026-08-31 实测（quota_wake_prompt docstring）。Universal Wake prompt 必须
自足（现有模板已满足，v2.2 扩展字段即可）；SessionStart 只是 correctness
fallback 不是 wake 路径。

### H-D — 运行中插件在缓存目录，仓库源码不直接影响本会话 hook

运行中的插件位于 `~/.zcode/cli/plugins/cache/glm-conductor/glm-conductor/
2.1.0/`；仓库 `plugins/glm-conductor/` 是源码。**M0 探针与 M6 之后的自举
dogfood 必须考虑此分离**（probe 需进 config-file hooks 或插件缓存，正式
能力随 v2.2.0 发布进缓存后生效）。

既有教训继续生效（项目记忆）：**CronDelete/CronUpdate 有已知 glitch 绝不
重试**；宿主 cron **20 槽位上限**——清理失败必须容忍（wake fires → 任务
已完成 → no-op 退出，即主计划 §15 ghost wake 安全设计）。

## 四、已锁定决策（用户 2026-09-01「按推荐」确认）

### D1 — DRAINING 强制 arm 范围【分层】

- 授权族（auto_once / until_done，authorization.source == user）：DRAINING
  下无 armed bridge → obligation=wake_required → **Stop block**（硬）；
- manual / notify：DRAINING 下 SHOULD arm（**advisory，不 block**），Stop
  报文提示可建 warm bridge；不建则退化为既有 SessionStart fallback。
- 理由：不替保守授权用户自动创建 automation；§0 的 24% 核心场景（用户已
  授权 until_done）获得完整硬保证。
- 实现注记：`_quota_wake_decision` 的授权矩阵语义不变；continuation
  obligation 的 block 判定在 control.py 中按 auto_resume 分层。

### D2 — Stop Gate continuation check 数据口径【只读缓存零网络】

- 读 resolver 缓存（quota-cache.json 的 snapshot），remaining ≤ draining
  阈值即按 DRAINING 判定（保守方向：误 block 一次的代价只是带行动指引的
  block 报文；漏检的代价是跨窗口断链）；
- 无缓存 / snapshot 无 remaining → 不 block（fail-open，H-A 红线）；
- provider EXHAUSTED（缓存 status）→ 不需要 continuation block（任务应已
  走 handle_quota_exhausted 的 waiting_quota 链）。

### D3 — observer 明细入口【新增 resolve_quota_detail】

- `resolve_quota_status` 四键冻结不动；新增 `resolve_quota_detail()`
  （同四级层级，返回含 snapshot 明细 / remaining / reset_at）；
- observer 与 CLI quota-observe 共用 detail 入口。

### D4 — dogfood 与发布策略【功能模拟 + 发布真实】

- 功能验证允许临时调高 draining 阈值触发完整真实链路（bridge 创建、等
  reset、wake、resume 全真）；
- 发布验收第 16 条（至少一轮真实跨 5h 窗口 resume）必须在真实耗尽-重置
  上发生，不可模拟替代；
- 发布节奏沿 v2.1 惯例：alpha → rc → stable + CI 权威门。

### D5 — 阈值配置落点【execution_policy.quota_control 子块】

```json
{"quota_control": {"pressure_percent": 35, "draining_percent": 20}}
```
- 归 execution_policy（授权事实源同放置）；legacy 缺块按默认 35/20 解释；
  validate 聚合校验（0 ≤ draining < pressure ≤ 100）。

### D6 — DRAINING 派发闸层次【task_manager prepare 层硬拒绝】

- `prepare_dispatch` / `prepare_dispatch_wave` 检查 execution phase：
  DRAINING 拒新 implementation 单元与新 wave（新 TaskManagerError 口径）；
- `plan_dispatch` 底层只认 provider 四态，不动；
- running 单元不强杀；finish/verify/review/checkpoint/wake 动作不受限。

### D7 — 双层映射冻结【provider_status ≠ execution_phase】

```text
provider EXHAUSTED 或 remaining == 0        → BLOCKED
remaining ≤ draining_percent（默认 20）      → DRAINING（即使 provider 报 AVAILABLE）
draining < remaining ≤ pressure_percent(35) → PRESSURE（execution 语义，预算 1）
provider UNKNOWN / remaining 不可得          → PRESSURE（fail-open 预算 1，不阻塞）
其余                                          → NORMAL
```
- 两个 PRESSURE 在代码中用命名空间区分（provider_status / execution_phase），
  文档与测试不混用简称；
- effective_worker_budget 保留（provider 维度），新增 phase 维度折算入口
  （NORMAL→策略值 / PRESSURE→1 / DRAINING→0 新实施、允许收尾 / BLOCKED→0）。

### D8 — Cron matcher 设计【Phase 0 冻结名，候选 CronCreate|CronDelete|CronUpdate】

- hooks.json 新增 Pre/Post/Failure 三条 matcher（候选正则见左），脚本内
  按 tool_name 细分；
- M0 必测三项：① Cron 工具是否走 PreToolUse 管道；② tool_result 中
  automation_id 的字段路径；③ automation wake 注入 turn 是否触发
  UserPromptSubmit。

### D9 — warm-only 实现【resume_from_quota 加 mode="warm"】

- 单一入口原则；warm 路径：强刷 quota + 确认 active + 刷新 manifest +
  **零转态**（不转 executing、不派发）退出。

### D10 — boundary_id 与 wake_at【DRAINING 窗口 kind:reset_at】

- `boundary_id = "<kind>:<reset_at>"`；多窗同时 DRAINING 取最早 reset；
- `wake_at = reset_at + grace`，grace 复用 `QUOTA_RESUME_GRACE_SECONDS=300`
  与 scheduler §30 数学；
- 同 task_id + boundary_id 至多一个 active bridge（幂等，WB-04）。

### D11 — Universal Wake prompt 语言【中文】

- 对齐现有 quota_wake_prompt 模板与同会话注入语义；字段清单按主计划 §10
  对齐（TASK_ID / LEDGER_ROOT / REPOSITORY_ROOT / EXPECTED_RESET_AT /
  WAKE_AT / 三分支决策 / 保留义务 / 红线）。

### D12 — gate_exhausted 交互【复用统一机器 + degraded 记账】

- continuation 违规 check 名 `continuation_unresolved`，走现有 block /
  gate_exhausted 机器（连续 block ≥2 第 3 次放行）；
- 放行时若任务仍 DRAINING 无 bridge → 追加 `continuity_degraded`（reason=
  stop_gate_exhausted）journal 事件。

### D13 — state.quota 回填【可选 execution_phase 键】

- `state.quota.execution_phase`（validator 允缺），prepare 时点写入，供
  manifest 与 SessionStart 展示。

### D14 — wake bridge 记账路径【新旧并存】

- 新增 `record_wake_bridge`（continuation.wake_bridge 全字段 + journal
  `wake_bridge_armed` 等事件）——warm 与授权族通用；
- `record_quota_wake`（窗口预算扣减）语义不变，仍限授权族——授权族 bridge
  先 record_wake_bridge 再 record_quota_wake；幂等锚均含 automation_id。

## 五、计划修订对照（对主计划的增量）

| 主计划条目 | 修订 | 依据 |
|---|---|---|
| §7.2/§12.2（DRAINING 一律 MUST arm） | 按 D1 分层：授权族 MUST block，manual/notify advisory | v2.1 授权语义冲突（H-B） |
| §12.1（Stop 检查顺序 0 位） | 实现确认：复用 collect_violations 前置检查 + 统一 block 机器；数据口径按 D2 | H-A 超时 |
| §13.4（PostToolUse 自动 record_quota_wake） | 授权族记预算扣减；warm bridge 记 record_wake_bridge（不扣预算） | H-B |
| §22（wake-failed CLI） | 保留，补充为 PostToolUseFailure 的手动兜底入口 | — |
| §26（Phase 0 清单 10 项） | 增补：UserPromptSubmit 触发、tool_result 字段路径、运行插件缓存分离（H-D） | 本轮发现 |
| §34（Phase 0→M1..M10） | 顺序确认；M5 依赖 M4（task_manager.py 串行）；M6 规格 matcher 名以 M0 结论冻结 | 文件重叠串行化 |
| §28.1 QO-04（remaining 0% → BLOCKED） | 冻结为双入口：provider EXHAUSTED 或 remaining == 0 | D7 |

## 六、残余探针（M0 执行清单，产出 Phase 0 报告）

1. Scheduled Task create 的真实 tool_name（候选 CronCreate）；
2. PreToolUse payload 形态（tool_input 字段：prompt/title/cron/delayMinutes/
   recurring/maxRuns）；
3. PostToolUse payload 与 tool_result 中 automation_id 字段路径；
4. scheduled wake 是否回到同会话（v2.1 实测已证同会话注入，复核）；
5. wake prompt 注入形态 + 是否触发 UserPromptSubmit hook；
6. task delete/update 的 tool 名（CronDelete/CronUpdate——仅观察可用性，
   **绝不重试**）；
7. one-shot task（recurring=false + maxRuns=1）skipped / 触发自完成行为；
8. ZCode 关闭 / 休眠后行为（观察，不强求）；
9. scheduled task 数量限制（记忆值 20，复核显示面）；
10. **probe 生效路径**：config-file hooks（`~/.zcode/cli/config.json` +
    hooks.enabled=true）vs 插件缓存（H-D）——探针不得污染插件缓存。

若 Cron 工具不可 hook：M6 转降级路径（Stop block → 主会话 CronCreate →
CLI wake-record/wake-failed 手动记账），M7 Stop 检查 armed 状态照常不减。

## 七、不变量与边界红线（实施期）

- 绝不重试 CronDelete / CronUpdate（本机已知 glitch）；
- Stop hook 零网络（H-A）；SessionStart 保持纯本地；
- manual / notify wake 绝不自动实施（32.2）；
- auto_once / until_done 恒要求 authorization.source == user（32.1）；
- wake prompt 不携带任何凭证材料（32.3）；
- repository > checkpoint；SessionStart 恒为 correctness fallback；
- completed 仅 Stop 完成门可写；旧任务 finalizing 终驻即开新任务；
- 状态机表内边（TASK_TRANSITIONS）不得新增表外转换；
- record 与 finish 之间不得产生 git 提交（证据指纹口径）；
- 本机验证：`python3 -m unittest …`（pytest 收集崩，CI 为权威门）。

## 八、任务账本映射（v22-loop-857f55a）

12 个 work unit 对应主计划 WU-22-01..12 与里程碑 M1-M10：

```text
wu-22-01 (M1 schema) ──┬─→ wu-22-02 (M2 phase) ─┬─→ wu-22-03 (M3 observer)
                       │                         ├─→ wu-22-04 (M4 gate) ─┐
                       │                         │                        ├─→ wu-22-05 (M5 planner)
                       │                         │                        │        │
                       │                         └─→ wu-22-09 (M9 集成)  │        ├─→ wu-22-06 (M6 hooks)
                       │                                                  │        ├─→ wu-22-07 (M7 stop gate)
                       │                                                  │        └─→ wu-22-08 (M8 resume)
                       │                                                  │                 │
                       └──────────────────────────────────────────────────┴─────────────────┴─→ wu-22-10 (M10a cleanup)
                                                                                            │
                                            wu-22-11 (M10b 文档) ←── 全部 ────────────────┘
                                            wu-22-12 (M10c dogfood+release, executor=main)
```

M0 Phase 0 为派发 wu-22-06 前的主会话宿主实测（不占 work unit），产出
`docs/GLM-Conductor-v2.2-Phase0-Scheduled-Wake-Runtime-Verification.md`。

---

## 九、D15：Persistent Wake Bridge 架构修正（用户 2026-09-01 批准，含修改项）

**触发**：Phase 0 #13 宿主硬约束——被 Scheduled Task 触发过的会话（即使该
automation 已 completed）永久不得再创建任何 Scheduled Task。该约束推翻
"逐窗链式建 wake"（wake N → resume → create wake N+1）拓扑，也推翻
v2.1 时代记忆处方"until_done 惰性逐窗创建"。证据：
`docs/GLM-Conductor-v2.2-Phase0-Scheduled-Wake-Runtime-Verification.md` #13、
`docs/GLM-Conductor-v2.2-Persistent-Wake-Bridge-Architecture-Correction.md`（架构
决策证据文档，非第二 truth）。

### D15-a — 批准：拓扑切换

WU-22-05～08 全部切换 Persistent Wake Bridge：一个 tracked task 恒指向**一条**
持久 automation 身份；废除"one quota boundary = one new automation"语义。

### D15-b — 批准：策略优先级（含本机修正）

主路径 = **Persistent Recurring Bridge**；Self-Retiming 仅为可选优化，且仅在
宿主明确验证"scheduled-owned session 可 Update 既有 automation"（P0-SCHED-01
通过）后启用。**不得把 Update capability 当 correctness 前提**。本机依据：
CronUpdate/CronDelete 有已知 glitch（§七红线），主路径必须绕开 Update。

### D15-c — 批准（含修改）：arm 时机分层

- `until_done`（task active + authorization.source=user）：**MUST eager-arm**，
  授权完成即建 bridge，不等 PRESSURE；
- `auto_once`：risk-triggered eager-arm——PRESSURE / 预测成本可能跨窗 /
  long-horizon / create capability 可能即将丢失 / 会话已关联其他 Scheduled
  Task / 运行时预测当前窗口无法安全完成任务，任一满足即 SHOULD arm；
  `DRAINING AND scheduler.create=allowed` 则 MUST arm；
- `manual/notify`：PRESSURE → SHOULD arm；DRAINING → mechanically possible
  则 MUST arm；create 不可用且无 reusable bridge → `continuity=degraded`，
  不得无限 Stop block。

### D15-d — 批准（含修改）：间隔与 ghost 缓解

`quota_control` 新增 `bridge_interval_minutes`；**保守默认 60 分钟**（recurring
overlap 行为经 P0-SCHED-10 验证前不冻结 30）。ghost bridge 四层缓解：
① completion 时单次 pause/delete 尝试（绝不重试）；② 明确 UI 手动清理路径；
③ future ghost wake 必须 cheap no-op；④ completed task tombstone
（`{task_id, status:"completed", completed_at, bridge_should_noop:true}`）。

### D15-e — 批准：WU-22-01a schema 前置增量

在 WU-22-05～08 行为实现前新增 **WU-22-01a**（只做 schema/validation/defaults/
serialization/manifest 字段/journal 词汇/迁移测试，不做行为）：
`scheduler_context.origin`（interactive/scheduled_task/unknown）与 capability
（allowed/forbidden/unknown × create/update/pause/delete +
parent_automation_id）；`wake_bridge.mode`（recurring/self_retiming）；
`wake_bridge.status` 扩为 10 值（+retarget_required/degraded/paused）；
新增字段 automation_id（已有）/generation/current_boundary_id/next_wake_at/
bridge_interval_minutes；tombstone 块。

### D15-f — 批准：P0-SCHED 实验矩阵（P0-SCHED-01..10）

见 Phase 0 报告 §5。所有 capability 实验区分 fresh interactive session 与
scheduled-owned session；每项记录 session origin / parent automation id /
previous trigger history / tested capability / result / host error；单探针纪律
（unknown → 一次受控探测 → allowed/forbidden → 本会话缓存；已知
create=forbidden 后不得重复真实 Create 探测）。**P0-SCHED-04（同一 recurring
task 能否稳定第二次/第三次触发）为 HARD GATE**——不通过则 Persistent
Recurring Bridge 策略失效，停止 WU-22-05 行为实现并重估架构。

### D15-g — 批准：记账与 auto_once 语义（本节为用户指令 §4/§5 的落账）

- automation lifecycle 与 quota-window consumption 拆分；冻结
  `automation fire count != quota window count`；
- 窗口预算按 `task_id + boundary_id`（`<window-kind>:<reset-at>`）幂等消费，
  同一 boundary 只能消费一次；
- `auto_once` = **一次成功的跨可执行新 quota boundary 的自动恢复**（不是一次
  automation 触发）。wake 触发但额度仍耗尽 / weekly cap 阻塞 / provider 不可用 /
  resume 未实际开始 → 不消费；仅在"新可执行 boundary 确认 AND 授权 resume 实际
  开始"时 `consumed_quota_windows += 1`。
- **本项取代主计划旧 §32.4"成功创建真实 future wake 后即消费 window budget"
  的建议语义**。

### D15 红线增量（并入 §七）

- 不得继续实现 per-window chained wake；
- 不得在 scheduled-owned session 中创建 next wake；
- 不得把 Update capability 当 correctness 前提；
- 不得把 automation trigger count 当 quota window count；
- overlap 未验证前不得把 30min 固化成硬默认；
- 已知 create=forbidden 后不得重复 probe；
- Stop Gate 不得无限要求机械上不可能的 scheduler 动作（INV-22-PB-07）。

### D15 账本映射变更

wu-22-01a 插入为 wu-22-01 后继（M1a）；wu-22-05（Persistent Wake Bridge）、
wu-22-06（Scheduler Capability & Bridge Lifecycle Adapter）、wu-22-07
（Continuation Stop Gate，联合判断 phase + bridge status + scheduler_context）、
wu-22-08（Resume Controller，严禁新建 Scheduled Task）按修正后规格重写。
