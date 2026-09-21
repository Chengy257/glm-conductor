# Global Quota Clock 拆离清单（P3-F 交接文档）

> **Status:** v2.4 Phase 3 unit Q2 产物（2026-09-21）。本清单在**任何 Clock 相关删除发生之前**写成——文中所引代码均摘自当时的 LIVE 树（分支 `review/zcode-3.14-native-workflow`）；Q3/Q4 删除落地后，仓库内这些文件即不复存在，本清单是它们唯一（或最后）的仓库内文字记录。
> **Authority:** `docs/reviews/V2_4_NATIVE_WORKFLOW_REBASELINE_SPEC.md` → `docs/roadmap/V2_4_PHASE_3_QUOTA_RESUME_EXTRACTION_SPEC.md` §7（P3-F）→ `docs/roadmap/V2_4_PHASE_3_WORKFLOW_EXECUTION_PLAN.md` unit Q2。
> **性质：** 交接（handover），不是实施。本文档只描述「未来独立 Global Quota Clock 项目」可参考/必须避开的材料；不在 Phase 3 创建新仓库，不承诺任何代码可直接搬用。
> **读者：** 未来独立 Global Quota Clock 项目的实施者；以及 Phase 3 Q3/Q4 删除单元的执行者（删除前请先读完本清单）。
> **Standalone project follow-up (2026-09-21):** standalone implementation is **not yet created**. The approved new-project handoff is now frozen in `docs/roadmap/GLOBAL_QUOTA_CLOCK_STANDALONE_PROJECT_PLAN.md` and `docs/roadmap/GLOBAL_QUOTA_CLOCK_V0_1_IMPLEMENTATION_SPEC.md`. Proposed repository: `Chengy257/GlobalQuotaClock`; working CLI/package name: `gqclock`.

GLM Conductor v2.3 的 Global Quota Clock 是 account 级常驻机制：一个 ZCode recurring automation 周期注入 tick 回合，每个 tick 消费 provider quota 快照、按冻结决策表算出下一个目标时刻、再经宿主 adapter 把 automation 的 `next_run_at` 动态改写为目标值。v2.4 Phase 3 把它从 Conductor 拆离（`V2_4_PHASE_3_QUOTA_RESUME_EXTRACTION_SPEC.md` §1：Conductor 不再保留 provider/account 级连续 clock）。本清单把拆离前的材料分三类：概念上可迁移/复用（第一节）、禁止迁移（第二节）、必须警告（第三节）。

---

## 一、概念上可迁移 / 复用

以下各项按「行为/算法/观察」摘录，供新项目参考设计；标注了 v2.3 源文件与关键行号。注意 Q3/Q4 删除后行号指向的文件不再存在，引用时以本节文字为准。

### 1.1 Provider 配额适配行为（`runtime/quota/provider.py`、`zai.py`、`bigmodel.py`）

这是 Clock（以及一切 quota 消费者）与世界接触的最底层，行为面完整、与 Conductor 域零耦合，是最可整体复用的部分：

- **抽象契约**（`provider.py`）：`QuotaProvider` 基类声明 `fetch(force=False)` 返回标准化 snapshot dict（§27 形状：`provider / plan_generation / plan_level / fetched_at / status / windows`，每窗含 `kind / used_percent / remaining_percent / reset_at`）；失败一律抛 `QuotaProviderError`，不返回 None、不抛裸异常（`provider.py:86-125`）。
- **错误分类**（`provider.py:58`）：`ERROR_KINDS = ("auth", "unavailable", "malformed", "network", "unknown")`，供上层 fail-open 决策使用。
- **host allowlist**（`provider.py:54`）：`ALLOWED_HOSTS = ("api.z.ai", "open.bigmodel.cn")`；适配器只引用常量，不自行散布 host 字符串（§25 纪律）。
- **九条实现契约**（`provider.py:17-36` docstring 逐条）：仅 HTTPS；严格 host allowlist；短超时；限长响应（防超大响应体）；不向 allowlist 外 host 转发凭证；重定向禁用或重定向目标重新过闸；原始响应默认不持久化；鉴权头遵循实测格式（`Authorization: <API Key>`，无 Bearer 前缀——与官方 glm-plan-usage 插件一致的已验证格式）；凭证零落盘（绝不写入账本目录、state、events、日志、stdout/stderr）。
- **错误 str 纪律**（`provider.py:38-42, 61-83`）：`QuotaProviderError.__str__` 只含 message 与 kind，本类自身绝不附加任何凭证或 Authorization 值——凭证安全由「异常类不持有」与「调用方不拼入」两侧共同保证。新项目请原样继承这条纪律。
- **两个薄适配器**（`zai.py:34-49`、`bigmodel.py:36-51`）：只绑定 provider 标识 `"zai"` / `"bigmodel"`，HTTP 传输/安全条款/缓存/错误分类全部在共享基类 `runtime.quota._http.HttpQuotaProvider`（八条安全条款在其 docstring 逐条声明，被 `tests/test_quota_adapters.py` 锚定）。实测端点（仅记于 docstring，代码路径不出现）：`GET https://api.z.ai/api/monitor/usage/quota/limit` 与 `GET https://open.bigmodel.cn/api/monitor/usage/quota/limit`。
- **可迁移要点**：`_http.py`（共享传输基类）与 `credentials.py`（凭证解析，只进内存）虽不在本清单点名清单内，但同属该适配行为域，Phase 3 KEEP 侧保留；新项目直接以它们为起点，不需要重写传输与凭证面。

### 1.2 五小时 reset 计算（`runtime/quota/window_math.py`、`time_utils.py`）

先记一个设计决策：**「五小时」从不被本地计算**——本地绝不以 `now + 5h` 推算窗口，五小时窗口的边界永远是 provider 快照里 `kind == "five_hour"` 窗口自带的 `reset_at`，本地只做解析、归一与 grace 相加。这个「锚定 provider 报告值、不自行推算」的原则本身值得迁移。

- **时间原语**（`time_utils.py`，quota 依赖图最底层，零 I/O 零时钟）：
  - `_parse_iso_utc(text)`（`time_utils.py:42-60`）：ISO8601 时刻文本 → aware datetime（UTC）；失败 → None，调用方一律按「未知」fail-open。Python 3.7 兼容细节：`fromisoformat` 不认 `Z` 后缀，先改写为 `+00:00`；naive 结果补 UTC。
  - `_format_iso_z(moment)`（`time_utils.py:63-65`）：aware datetime → UTC 秒精度 Z 形式串（§27 输出纪律）。
  - `_normalize_now(now)`（`time_utils.py:68-87`）：now 注入参数归一（None → 当前 UTC；datetime / ISO 串 → 时刻；非法 → 中文 ValueError）——仅供测试注入，返回值不参与任何目标时刻计算，避免测试与运行环境时钟耦合。
- **窗口边界数学**（`window_math.py`）：`_earliest_reset_plus(windows, grace_delta)`（`window_math.py:36-49`）——相关窗中**最早**可解析 `reset_at` 加 grace 的 Z 形串；不可解析逐窗跳过，全不可解析 → None（**不虚构唤醒时刻**，§31 纪律）。
- **同一族的两个 max 语义**（迁移动向提示）：对「窗口身份/多窗可执行边界」而言相关的是**最晚** reset——`epoch.executable_boundary_at` 取 `max(阻塞 EXHAUSTED 窗可解析 reset_at) + grace`（`epoch.py:293-294`），Clock 决策层与 degenerate 输入的 `_max_parseable_reset`（`clock.py:240-252`）同口径；对「最早可恢复时刻」而言相关的是**最早** reset（`window_math._earliest_reset_plus`）。min/max 语义随用途而分，迁移时勿混同。
- **依赖方向**：`window_math → time_utils`，`time_utils` 绝不反向——时间原语是依赖图最底层。Phase 3 把其中 minimal reset-time 纯助手收进保留核（`time_utils.py` 在 KEEP 清单），新项目可整段沿用。

### 1.3 Global Clock 目标时刻决策逻辑（`runtime/quota/clock.py`）——决策算法全文摘录

`clock.py` 是「纯决策」半边：零 I/O、零时钟、零副作用，不 import adapter、不读盘、不调 now()（`clock.py:6-12`）。它消费 §27 规范形 snapshot（`resolver.resolve_quota_detail` 透出的 `{"windows": [...]}`），按**冻结决策表**折算下一个目标时刻。Q4 删除后以下摘录即唯一文字记录：

**输入**：`next_clock_target(snapshot, *, now_ms, last_reset_at=None, grace_seconds=120, retry_delay_seconds=300)`（`clock.py:272-362`）。
- `snapshot`：§27 规范形 dict；None / 非 dict / `windows` 非列表 → 决策 1；
- `now_ms`：当前 epoch 毫秒（有限数值，bool 拒绝，非法先于任何决策抛中文 ValueError）；
- `last_reset_at`：上次 tick 观测到的 five_hour reset（epoch 毫秒 int，或同刻 Z 形串——容错归一；None/无法归一 → 视为未提供）；
- grace 缺省 120 秒（clock 专属缺省，只防 reset 边界抖动）；retry_delay 缺省 300 秒。

**冻结决策表**（严格序；`clock.py:14-29` docstring 与 `clock.py:307-362` 实现）：

1. `snapshot` 为 None / 非 dict / `windows` 非列表 → **short_retry**，reason=`provider_unavailable`，target = now + retry_delay。
2. weekly 窗口存在且按 epoch 的阻塞语义（canonical 归一后 `status == "EXHAUSTED"`，`epoch.blocking_windows`）判定为耗尽压制 → **weekly_park**，target = `epoch.executable_boundary_at(weekly 窗, grace)` = max(阻塞 weekly 窗可解析 reset_at) + grace；有阻塞 weekly 窗但 reset 全不可解析（§31 不虚构）→ 退化 **short_retry**，reason=`weekly_reset_unparsable`。weekly 存在但不 EXHAUSTED 不触发 park。
3. five_hour 窗口缺失 → **short_retry**，reason=`five_hour_missing`。
4. `B = parse(five_hour.reset_at)` 失败（多个同 kind 窗取可解析 reset 最大者；全不可解析）→ **short_retry**，reason=`five_hour_unparsable`。
5. `B <= now_ms`（reset 已过期——含「reset 未推进」的 provider 快照传播延迟场景）→ **short_retry**，reason=`reset_elapsed`，target = now + retry_delay（固定小步等 provider 刷新）。
6. `B > now_ms` → **reset_target**，target = `B + grace`；reason：`last_reset_at` 已提供且 ≠ B → `window_advanced`（窗口确实推进了）；相等或未提供 → `reconfirm`。

**输出**：键集冻结为 `CLOCK_TARGET_KEYS = ("decision", "target_epoch_ms", "reason", "reset_at", "reset_at_epoch_ms", "window_kind")`，`decision ∈ CLOCK_DECISIONS = ("reset_target", "short_retry", "weekly_park")`（`clock.py:100-104`）；`reset_at` / `reset_at_epoch_ms` 按当前相关窗口填写，无关键填 None（reset 不可解析时恒 None，不透传不可解析原文）。

**架构裁决（2026-09-08，`clock.py:30-34`，不可违背）**：clock 是极低成本后台常驻机制——无窗口预算、无限期；额度窗口预算 N 是 task 侧概念，与本决策层零关系：**本模块的任何输入/输出都不得出现 budget / consumed 语义字段**。新项目请把这条裁决作为设计宪法继承。

**辅助纪律**：纯函数（无副作用、无 I/O、不 import 宿主 adapter）；多重同 kind 窗口（退化输入）取可解析 reset 的最大者（对「下一个目标时刻」而言最晚 reset 即相关者）；ISO→epoch 毫秒折算在 `_parse_reset_at_ms` 内实现，解析复用 `time_utils._parse_iso_utc` 同一解析器（归一口径一致）；stdlib only、Python 3.7 兼容。

**依赖注记（删除后需转写）**：决策表第 2 行复用 `runtime.quota.epoch` 的三件纯函数（`canonical_windows` 归一 / `blocking_windows` 阻塞判定 / `executable_boundary_at` 边界数学，`epoch.py:165-294`）——单一真相源纪律是「绝不重新发明阻塞判定」。epoch 模块随 Phase 3 删除，新项目须把这三件语义（归一排序的窗口多重集身份、`status == "EXHAUSTED"` 即阻塞、max+grace 边界）自行转写进自己的代码。

**Placement UX advisory 纯函数层**（v2.3.1 增补，`clock.py:36-50, 365-488`；零 I/O 零阻断，呈现层共用）：
- `model_is_flash(model)`：模型名含 "Flash"——仅 advisory 用途，绝不作为绑定阻断条件，也绝不当作「专用会话」的证据。
- `placement_guidance_for_plan()` / `bind_session_placement()`：会话放置引导——clock automation 应建在独立、低成本 Flash 模型的专用会话；automation 保持附着当前会话（插件无会话迁移能力），周期 tick 会注入该对话；勿在主编码会话创建（tick 会周期性注入打断）。
- `status_placement_block(...)`：`dedicated_session` 恒为常量串 `"not_mechanically_verifiable"`（`DEDICATED_SESSION_UNVERIFIABLE`）——ZCode 不向插件开放会话探测能力，**绝不伪造 true/false 布尔**。
- `needs_replacement_from_reasons(reasons)`：两极判定（锁定决策 c）——True ⇔ reasons 含 `db_row_missing` / `db_inspect_failed`（automation 缺失/不可查，clock 无法继续服务的硬故障）；`target_mismatch` / `tick_stale` / `runtime_path_missing` 仅 advisory 不触发。
- `recovery_guidance_for_replacement()` / `NO_AUTO_MIGRATION_DECLARATION`：恢复指引恒声明「未执行自动会话迁移」——本插件永远不做隐式迁移/自动 rebind，替换须用户显式提供新 automation_id。

### 1.4 宿主调度观察与约束（`runtime/scheduler_facts.py`、`runtime/host/zcode_schedule.py`）

**W0 结论（出处：`docs/reviews/ZCODE_3_14_NATIVE_WORKFLOW_RESULTS.md` §13.2，2026-09-20 实测，ZCode 3.14.0 build 3.14.0.7681）**——新项目必须在设计里逐条吸收：

- **scheduled turn 是宿主会话的中途续入（mid-turn continuation），而非新会话/新进程**：automation prompt 到达时出现在 owning 会话的活回合内部；wake 回合共享主会话的环境与权限状态。设计绝不能假设「调度触发 = 干净的新会话」，prompt/技能契约必须幂等且环境无关（同文档 §13.2 "NEW constraint" 段；spec §4 "Do not assume the scheduled turn is a fresh session"）。
- **App 关闭时的触发未验证（UNVERIFIED）**：本轮全部实测都在 App 开启状态完成；无人值守（宿主关闭）能否触发是一个 open question（`ZCODE_3_14_NATIVE_WORKFLOW_RESULTS.md:513-519`）。新项目对「离线触发」不得做出任何未经验证的正确性宣称。
- 其余 W0 已证事实：recurring Scheduled Task 可经宿主能力创建并按 cron 触发；`ListWorkflowRuns` 项目域跨会话可见；`ResumeWorkflowRun` 无需确认即接受；对已完成 run 的重复激活得到确定性拒绝且零状态变化（§13.2；这是 Clock 之外 Conductor 侧的观察，但同为宿主调度面的实测证据）。

**`host/zcode_schedule.py`——直写宿主 SQLite 的 adapter（第四节有专节警告，此处只记其形态）**：ZCode 内部库 `tasks-index.sqlite` 的唯一适配器，四个公开函数——`discover_zcode_tasks_db`（explicit_path > 环境变量 `GLM_CONDUCTOR_ZCODE_DB` > 默认 `~/.zcode/v2/tasks-index.sqlite`）、`inspect_automation`（mode=ro URI 只读单行视图 + 最近 20 条 runs）、`retime_automation`（全模块唯一生产写：BEGIN IMMEDIATE → 行数/状态校验 → 单列改写 → 读回验证 → COMMIT，任一失败必回滚，`zcode_schedule.py:213-314`）、`probe_host_compatibility`（绝对零写兼容探针，`zcode_schedule.py:317-436`）。宿主 schema 为 2026-09-08 实测冻结（`zcode_schedule.py:39-41`）：automations 表 33 列、automation_runs 表 12 列、`next_run_at` 为 INTEGER epoch 毫秒（可 NULL）。硬安全边界：生产写全模块只有 `_UPDATE_NEXT_RUN_AT_SQL` 一条单列 UPDATE（`zcode_schedule.py:98-99`），由 `ForbiddenSqlTest` 机械锚定（源码文本逐 token 断言其余变更语句不存在）。

**`scheduler_facts.py`——宿主调度能力事实缓存**（会话侧观察面，与 clock 解耦但同属宿主调度观察域）：
- 以 hook 载荷 session_id 为键，把宿主 scheduler 能力探针的**已证事实**缓存到 `<repo_root>/.glm-conductor/scheduler/session_facts.json`（七键冻结：`origin / create / update / pause / delete / automation_ids / updated_at`）。
- **证据只进不退 merge 语义**（`scheduler_facts.py:22-31, 253-293`）：None 不覆盖既有值；四能力一旦落为 allowed/forbidden 即冻结不翻转；origin 只允许 unknown → interactive/scheduled_task 单向升级——其中 **D15-e 裁决**：被 Scheduled Task 触发过的会话（origin=scheduled_task）禁止再创建 automation，该事实绝不被后续观察冲掉。
- 容量闸 MAX_SESSIONS=32 按 updated_at 淘汰最旧；写入走原子写 + 独占锁读-改-写（`runtime.durable_io`），PermissionError 有界重试（Windows AV 是本机已知现象）；读方 fail-open（坏文件 = 无证据 = 放行，绝不炸消费方）。

### 1.5 Window Primer 物化观察（`runtime/quota/primer.py`）

Primer 是 v2.2 的「用一次最小模型调用使新 quota window 物化」机制（v2.3 起 production 物化移交给 clock tick，本模块定位为 default disabled 的 manual/experimental fallback，`primer.py:5-12`；Phase 3 删除）。它的**物化确认方法论**是本清单最值得迁移的观察之一：

- **物化确认红线**（`primer.py:58-68`）：
  - HTTP 200 不是证据——不得因 prime call 成功返回就判定窗口 AVAILABLE；
  - 百分比下降不是证据——监控端点是整百分点粒度，小调用不可见 delta（「百分比自证」被红线封死）；
  - 唯一证据 = prime 前后**两次强制 refresh 的窗口身份变化**（epoch / reset_at 推进；`epoch.same_epoch` 判定）；
  - refresh 后仍有 EXHAUSTED 阻塞窗（weekly 优先语义）→ materialized 但 executable=False。
- **single-attempt 幂等**（`primer.py:78-105`）：幂等键 = `(provider_identity_hash, boundary_id)`；键命中 → 直接返回既有 durable 结果（零模型调用零事件）；**绝不自动重发**——歧义超时（请求可能已到达 provider、结果未知）以 prime 后强制 refresh 的窗口身份推进做事后确认代替重发，「timeout + materialized=True 是合法组合」；失败尝试同样落账（error_kind 非空），同键重入零网络零事件。这条「超时不重发、用观察收尾」的不对称设计对任何「触发真实世界副作用」的机制通用。
- **fail-closed 不对称**（`primer.py:70-76`）：quota 观察面缺数据按 UNKNOWN/PRESSURE fail-open 不阻塞；primer 是模型调用授权——一次调用消耗真实额度并改变 provider 侧窗口状态，policy 缺块/坏块一律保守拒绝。理由原文：误拒只损失一次物化时机（下个观察周期可再判），误放行则是无授权的模型调用。新项目若保留任何「主动触碰窗口」的能力，请继承这条授权方向不对称。
- **三重授权闸**（`authorize_prime`，`primer.py:400-455`）：`primer_enabled == true` AND `auto_resume ∈ {auto_once, until_done}` AND `authorization.source == "user"`，三条全过才 authorized；纯函数零副作用（连 repo_root 都不入参，结构上不可触盘）；manual / notify 永不 prime。
- **调用形态实测**（P0-QP-00，`primer.py:229-253`）：模型 `GLM-5.3-Flash`、`max_tokens=64`、消息 "Reply with a single word: pong"、Anthropic 兼容端点 `<baseURL>/v1/messages`、鉴权 `x-api-key` 头；最小调用延迟实测 6.15s（默认开思考）→ 超时取 30s 有界上限。异常只取类型名，绝不透传异常文本/URL/响应体（urllib 异常文本可能携带 URL）。

### 1.6 值得保留的 clock CLI UX（`runtime/commands/quota_clock.py`、`runtime/cli.py` 的 quota-clock-* 子命令）

四子命令的用法形态与输出契约（`cli.py:116-126, 239-242`；实现自 v2.3.1 Wave 3 迁至 `commands/quota_clock.py`）：

```
quota-clock-plan   <repo_root>
quota-clock-bind   <repo_root> <automation_id> [--db <path>]
quota-clock-tick   <repo_root>
quota-clock-status <repo_root>
```

- **plan——纯规划**（`quota_clock.py:115-169`）：零写盘、零 DB、零 state。force-refresh 解析 → identity → 取 five_hour reset B → first_target = B + 120s；输出冻结键集（`status:"planned"`, `provider_identity_hash`, `reset_at`, `reset_at_epoch_ms`, `first_target_epoch_ms`, `grace_seconds`, `retry_delay_seconds`, `fallback_interval_minutes:60`, `runtime_path`, `cli_command`, `suggested_prompt`, `placement_guidance`）。five_hour 缺失/不可解析 → `{"status": "not_plannable", "reason", "error"}` 退出码 1。新项目值得保留的 UX 精髓：**先 plan 后 bind 的两步引导**——用户先看到将要发生什么（含会话放置引导），再显式绑定。
- **bind——绑定 + 首次 retime**（`quota_clock.py:172-299`）：须在普通交互回合执行（推荐 Flash 会话——automation 的 model 即发起会话的模型）；流程 = inspect 行存在性 + recurring 校验 → 写用户级 state → retime 首个目标；retime 失败 → 非零退出并注明 state 已写但目标未生效。**死绑定自愈**（w35-dogfood-fix F-1，`quota_clock.py:187-263`）：目标 automation 已绑定时，只读复核旧 automation 的宿主 DB 行，行确认缺失才放行改绑（存储层锁内 TOCTOU 复核）；行存活则维持冲突拒绝——用户仍显式提供新 id，绝不自动迁移。
- **tick——调度回合唯一主入口**（`quota_clock.py:302-390`）：automation 的 prompt 即 plan 产出的 `suggested_prompt`，一个调度回合内完成「决策 → retime → state 观测更新」并单行 JSON 汇报后结束回合。tick prompt 模板（`quota_clock.py:59-65`）值得原文保留：约束「Reply with the tick JSON in one line, then end the turn. Never call Cron tools. Never do anything else.」——调度回合的自我限权措辞；`{python}` 槽以 `sys.executable` 代入（w35-dogfood-fix F-4：消除 Windows 无 python3 启动器的首跳必失败），prompt 自带「路径缺失时去插件缓存 runtime 目录找 cli.py」的自愈指引。**retime 失败 → 什么都不做（native watchdog 兜底），仅更新 last_tick_at，输出 retimed=false，退出码 0**（§4.2）——「观测照常落账、失败不补偿」的 tick 语义是可靠性设计的可迁移样板。
- **status——只读健康汇总**（`quota_clock.py:393-549`）：健康 reason 词汇（snake token）：`db_row_missing` / `target_mismatch`（`next_target_at` 为 None——bind 后首 tick 前——不判不一致，避免对刚绑定者误报）/ `runtime_path_missing`（tick 将自愈）/ `tick_stale`（now − last_tick_at > 2×fallback 60 分钟）/ `awaiting_first_tick`（last_tick_at 缺失 = 首绑未 tick 的健康态，w35-dogfood-fix F-3）/ `db_inspect_failed`；`needs_replacement` 两极判定与 `recovery_guidance` 全部落纯函数层（见 1.3）。附赠：`host-check` 子命令 → `probe_host_compatibility` 零写兼容探针（ZCode 升级后 schema 漂移的一次性自检，supported/unsupported + 缺失项明细）——新项目面对宿主升级时需要同款探针。
- **输出契约**：stdout 恒为单行 JSON（`json.dumps(..., ensure_ascii=True)` + "\n"，`quota_clock.py:40-43`）；失败路径统一 `{"status": "error"|"not_plannable", "error", ...}` 退出码 1（`quota_clock.py:85-91`）。绑定状态存于用户级 clock state（`runtime/quota/clock_store.py`，按 provider identity 寻址；随 Phase 3 删除）——新项目需要自己的等价 store，但「state 只承载观测（last_tick_at/last_reset_at/next_target_at）与绑定指针、不承载任何任务语义」的切分值得保留。

---

## 二、禁止迁移

以下材料属于 Conductor 的任务域/仓库域，与 account 级 clock 机制正交；新项目必须**不带一个字段地**离开它们：

- **Conductor 任务 id**：Conductor 内部标识（语义前缀+随机后缀，锚定 `.glm-conductor/tasks/<id>/` 账本布局），对独立 clock 项目无意义——clock 是 account 级机制，不按任务寻址。
- **Work Unit/DAG 状态**：v2.4 静态节点/DAG（`runtime/work_unit.py`、`runtime/dependency.py`）是 Conductor workflow 编译域的状态，描述「一个编码任务怎么拆」，与「quota 窗口何时翻转」零关系。
- **任务 subscription**：v2.2 continuity 的任务订阅把 clock tick 与具体任务续跑挂钩——正是 spec §1 要拆除的耦合；新项目必须任务无关（task-agnostic）。
- **任务 wake bridge**：`continuity/wake_bridge.py` 的 boundary-id（`"<kind>:<reset_at>"`）把宿主 automation 绑定到具体任务的恢复链——迁移它等于把 Conductor 任务语义偷运进 clock。
- **任务恢复预算（`quota_resume`）**：per-task `max_resumes / resume_count` 是 Conductor 任务生命周期的授权预算（`runtime/state.py` / `runtime/task.py` 域）；clock 架构裁决明文禁止任何 budget/consumed 语义进入其输入输出（见 1.3），两者不得混流。
- **校验/评审状态**：review-record / 校验门是 Conductor 的质量闸域，表达「编码产物是否合格」，与 clock 无关。
- **仓库 ownership**：`runtime/ownership.py` 的 stage 规划与 `runtime/writer_guard.py` 的仓库写预约是 per-repo 的 Conductor workflow 并发控制——clock 没有仓库概念。
- **任务 journal 语义**：任务级 journal 词汇表（`events.jsonl` 的任务生命周期事件）属于 Conductor 任务账本；clock 侧事件（如 `window_primed` 控制面事件）是另一套词汇，新项目应重新设计自己的观测/审计面，不继承任务 journal 的形状与语义。

---

## 三、必须警告

### 3.1 直写 ZCode SQLite retiming 必须独立重估——绝不能因 v2.3 用过就继承

`runtime/host/zcode_schedule.py` 的 `retime_automation` 是对 ZCode **内部库** `tasks-index.sqlite` 的直接写（唯一生产写 = 单列 UPDATE `next_run_at`）。v2.3 时代它是唯一可行路径；v2.4 已把它定为要拆除的 Conductor 生产依赖（spec §5 明文：Conductor must not directly edit ZCode internal SQLite scheduler state）。新项目在采用同款实现前必须独立重估以下每一项，逐项拿到当时的实测证据：

- **宿主 schema 漂移**：`automations` 33 列 / `next_run_at` INTEGER epoch 毫秒是 2026-09-08 在 ZCode 3.14.0 实测冻结的快照（`zcode_schedule.py:39-41`）——任何 ZCode 升级都可能改变它；v2.3 为此写过零写探针（`probe_host_compatibility`），新项目需要同款自检并且每次宿主升级后重跑。
- **是否已有官方支持的等价能力**：若 ZCode 后续开放 automation 管理的受支持 API，直写内部库即失去全部正当性——「内部库无契约、随时可变」是直写方案的固有风险。
- **并发与锁风险**：WAL 模式下与宿主进程并发写、busy 超时、Windows 文件锁——v2.3 的 busy 包装与读回验证只是缓解，不是豁免。
- **未验证面**：W0 已证 scheduled turn 是中途续入、App 关闭触发 UNVERIFIED（见 1.4）——若新项目的核心宣称依赖离线触发，必须先补这项验证，否则设计应假设「宿主开启时才触发」。

结论重申：**直写 retiming 是一个当时权衡，不是一个可继承资产**。v2.3 用过这一事实本身不构成任何论据。

### 3.2 本清单是交接，不是实施

- 本文档只做材料分类与摘录；Phase 3 不创建新仓库、不迁移任何代码（spec §7 "This inventory is documentation only"）。
- 「概念上可迁移」不等于「可直接搬用」：所引代码按 Conductor 的 Python 3.7 兼容与 stdlib-only 纪律写成，新项目的技术选型（语言、宿主接口、存储）独立决策；本文摘录的算法与观察以**语义**为准而非字节。
- 所引文件将在 Phase 3 Q3/Q4 删除；删除后本文成为唯一记录，若发现摘录与历史 commit（删除前树）有出入，以历史 commit 为准。

---

## 四、v2.3 dogfood / re-home 记录检索结果

**不在仓库内。** 检索命令与结果（2026-09-21）：`grep -rn -i "dogfood\|re-home\|rehome\|w35" docs/reviews/` → 零命中（`docs/reviews/` 仅含 v2.4 ZCode 3.14 rebaseline 材料）；全 `docs/` 树对 "dogfood" 唯一命中是本 Phase 的执行计划文档自身（`docs/roadmap/V2_4_PHASE_3_WORKFLOW_EXECUTION_PLAN.md`）。

仓库内仅存的 v2.3 dogfood 痕迹是**代码内单元注记**（unit id `w35-dogfood-fix`，v2.3.1）：`runtime/commands/quota_clock.py`（F-1 死绑定自愈 / F-3 awaiting-first-tick / F-4 sys.executable 消除 Windows 首跳失败）、`runtime/quota/clock.py:477`（bind 对已确认失效的死绑定自动检测并替换）、`runtime/quota/clock_store.py:44`（verified_dead_automation_id 的 TOCTOU 复核）。这些注记记录了 dogfood 期间的真实故障形态（Windows 启动器、宿主行被外部删除、首绑未 tick 误报），其教训已并入 1.3/1.6 的摘录；完整的 v2.3 clock dogfood / re-home 过程记录不在本仓库，存于项目记忆（project memory），本清单如无法与其对账，以代码注记为准。
