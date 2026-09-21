# v2.4 Phase 4 P4-A — 全仓退役面审计（Retired-Surface Audit）

> **执行单元：** R1（Phase 4 workflow 单元规格见 `docs/roadmap/V2_4_PHASE_4_WORKFLOW_EXECUTION_PLAN.md` §2-R1）
> **权威链：** `docs/reviews/V2_4_NATIVE_WORKFLOW_REBASELINE_SPEC.md` → `docs/roadmap/V2_4_PHASE_4_INTEGRATION_RELEASE_SPEC.md` §2（P4-A）
> **日期：** 2026-09-21（分支 `review/zcode-3.14-native-workflow`，基于 `bff4d21`）
> **方法：** `git grep -i -E`（仅 tracked 文件，排除 `__pycache__`/`*.pyc`）对 22 个退役概念逐词全文扫描；命中按六大面分组统计（PROD=runtime+hooks+门脚本+CI、TEST=tests/、SKILL=skills+commands+agents、USRDOC=README×2+marketplace.json+plugin.json+docs/README.md、TECHDOC=architecture+core-concepts+troubleshooting、HIST=CHANGELOG+docs/reviews+docs/roadmap+scripts/spikes）；每条命中逐条分类（见下）。表内命中数为 **R1 修复后** 的存量计数（修复前差异见 §3）。

## 概念计数说明（规格偏差记录）

任务书言「21 个退役概念」，但其枚举清单（与 P4-A 规格 §2 一致）实际列出 **22** 个。本审计以枚举清单为准，22 个概念全部建档（C01–C22）；§6 门断言按「小节标题 ≥ 21」执行，22 个小节自然覆盖。

## 分类图例

- **类 1（活性生产引用）**：生产代码 / 测试中仍把退役概念当作现役事实的引用 → R1 当场修复（仅限代码/测试；docs/skills 不属本单元）。
- **类 2（历史变更/评审证据）**：CHANGELOG、docs/reviews、docs/roadmap、scripts/spikes 中的历史材料 → 保留并在本表引用。
- **类 3（迁移/拆离说明或检测面）**：显式的退役记录、拆除清单、legacy 检测常量、禁词扫描锚、删除回归锚定测试、出处注（“自 X 逐字移入，X 已删除”）→ 保留。
- **误报**：关键词撞名但语义无关（如 CLI argv 分发 `_dispatch`、Unix epoch 毫秒、HTTP transport 注入点、发布波次名 "Wave 2"、v2.4 任务七态中的 `blocked`）→ 保留，注明。

---

## 1. 总表（22 概念 × 面命中数 × 分类汇总）

| # | 概念 | PROD | TEST | SKILL | USRDOC | TECHDOC | HIST | PROD/TEST 分类汇总 | 处置 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |
| C01 | flash-implementer 作为文本执行基底 | 5 | 4 | 13 | 2 | 7 | 32 | 全部类 3（validator 退役豁免 + legacy 负例） | skills→R4；README→R2；architecture→R3 |
| C02 | dispatcher | 4 | 3 | 13 | 0 | 16 | 137 | 2 误报（`_dispatch`）+ 2 类 3 | skills→R4；architecture→R3 |
| C03 | dispatch wave | 3 | 3 | 5 | 0 | 5 | 52 | 1 误报（"Wave 2"）+ 2 类 3 | skills→R4；architecture→R3 |
| C04 | permit | 14 | 10 | 8 | 1 | 8 | 81 | 全部类 3（legacy 检测 / 禁词锚 / 退役注） | enforcement→R4；core-concepts→R3；plugin.json→R2 |
| C05 | per-unit lease | 19 | 14 | 4 | 0 | 14 | 78 | 全部类 3 + 1 耦合锚（validator orchestration "租约" 标记→R4） | 同上；architecture→R3 |
| C06 | Work Unit 运行时状态 | 14 | 14 | 3 | 0 | 0 | 2 | 全部类 3（work_units legacy 检测面；`runtime/work_unit.py` 静态节点模块是 v2.4 活模块） | skills→R4 |
| C07 | 单元验证 receipt | 10 | 24 | 64 | 11 | 27 | 100 | 全部类 3 + 1 耦合锚（enforcement `verification_stale` 标记→R4） | enforcement→R4；core-concepts/architecture→R3；plugin.json→R2 |
| C08 | 评审 receipt 权威 | 20 | 8 | 10 | 3 | 8 | 65 | **类 1 ×1 已修（validator check_15）**；余类 3 | enforcement→R4；architecture→R3 |
| C09 | agent-run ledger | 2 | 4 | 7 | 0 | 4 | 33 | 全部类 3 | skills→R4；architecture→R3 |
| C10 | reconcile 小任务状态 | 0 | 3 | 14 | 0 | 12 | 55 | PROD 零命中 ✓ | skills→R4；docs→R3 |
| C11 | resume manifest | 1 | 4 | 6 | 0 | 5 | 20 | 类 3（退役注；writer-show 对比注已修） | skills→R4 |
| C12 | Bash policy | 3 | 0 | 2 | 0 | 1 | 12 | 全部类 3（退役子命令注） | skills→R4 |
| C13 | finalizing | 3 | 2 | 5 | 0 | 5 | 14 | 全部类 3（`_gate_commit` 形参已修删） | architecture→R3；skills→R4 |
| C14 | 执行相位 DRAINING/BLOCKED | 0 | 2 | 2 | 0 | 4 | 10 | **类 1 ×1 已修（`QUOTA_SUBSCRIPTION_MINIMUM_STATES` 删除）**；余类 3（v2.4 任务态 `blocked` 同名不同义） | core-concepts→R3；continuity→R4 |
| C15 | quota watcher | 3 | 0 | 8 | 0 | 18 | 24 | 全部类 3（journal/resolver/durable_io 活性注已修） | continuity→R4；architecture/troubleshooting→R3 |
| C16 | quota epoch | 1 | 0 | 5 | 0 | 5 | 17 | 类 3 + 误报（parser 的 epoch=Unix 纪元） | continuity→R4 |
| C17 | subscription/accounting | 6 | 0 | 4 | 0 | 13 | 51 | **类 1 ×1 已修（subscription 词汇常量删除）**；余类 3 | architecture→R3 |
| C18 | Window Primer | 1 | 0 | 6 | 0 | 10 | 26 | 类 3（journal 引用已随控制面删除） | continuity→R4；architecture→R3 |
| C19 | Conductor 内 Global Quota Clock | 4 | 4 | 9 | 13 | 24 | 95 | 全部类 3（journal 控制面 API 已修删；advisory 删除回归锚） | README→R2；architecture/troubleshooting→R3；continuity→R4 |
| C20 | Activation Transport | 1 | 0 | 2 | 0 | 5 | 17 | 类 3 + 误报（quota/_http 的 HTTP transport） | architecture→R3；continuity→R4 |
| C21 | Task Wake Bridge | 3 | 2 | 19 | 6 | 13 | 75 | 全部类 3（退役注；hooks 已只剩 SessionStart+Stop） | continuity/long-horizon→R4；README→R2；architecture→R3 |
| C22 | 直写 scheduler SQLite | 15 | 3 | 3 | 2 | 14 | 57 | **sqlite 命中 = 0**；scheduler 15 处全为出处/删除注（类 3） | 保持零写入；troubleshooting→R3 |

**结论：** R1 修复后，PROD 与 TEST 面不再存在任何类 1 未处置命中；存量命中均为类 3（迁移注 / legacy 检测 / 禁词锚 / 删除回归锚）或已注明的误报。SKILL/USRDOC/TECHDOC 面的存量命中全部按归属指派（§4），HIST 面全部保留（类 2/3）。

---

## 2. 逐概念小节

### C01 — flash-implementer 作为文本执行基底

- **PROD（5，全部类 3）**：`scripts/validate_plugin.py:21,169,175,177`（check 4 的 `RETIRED_AGENTS = ("flash-implementer",)` 退役豁免常量与 `TODO(Phase 4)` 注释——技能全面重写（R4）后应移除豁免）、`:492`（check 4 豁免判定逻辑）。
- **TEST（4，类 3）**：`tests/test_agent_model_binding.py:18`（W6 退役说明注）；`tests/test_state_v24.py:86`、`tests/test_recovery_v24.py:143`、`tests/test_task_lifecycle.py:580`——三处均为 **legacy v2.3 state 负例 fixture** 中的 `route.executor` 值（构造遗留任务以验证 v2.4 拒绝/检测，必须保真 v2.3 形态）。
- **SKILL（13，指派 R4）**：`skills/orchestration/SKILL.md:75,87,117`（executor 词汇与示例）、`skills/orchestration/references/operations.md:8`、`role-contracts.md:78,194,263`（整节 "flash-implementer 契约"）、`skills/continuity/references/long-horizon.md:51,224`。
- **USRDOC（2，指派 R2）**：`README.md:143`、`README.zh-CN.md:143`。
- **TECHDOC（7，指派 R3）**：`docs/architecture.md:35,84,121,341` 等。
- **HIST（32，类 2/3）**：CHANGELOG、reviews/roadmap 材料保留。
- **处置**：代码/测试无活性引用；R4 重写技能后须同步移除 validator 豁免（见 §4 约束 1）。

### C02 — dispatcher

- **PROD（4）**：`runtime/cli.py:387,515` `_dispatch()`——**误报**（CLI 自身 argv 分发器，非 v2.3 dispatch 运行时）；`runtime/journal.py:47`、`runtime/task.py:120`——退役词排除注（类 3）。
- **TEST（3，类 3）**：`tests/test_recovery_v24.py:89`（legacy 模块导入禁词表含 "dispatcher"）、`tests/test_task_lifecycle.py:60`（`dispatch_prepared/dispatch_aborted` 排除词锚）、`tests/test_quota_resolver.py:14`（历史注）。
- **SKILL（13）→R4；TECHDOC（16）→R3**（`docs/architecture.md:279,292,366,378-392` 仍把 dispatch wave/permit 门当现役架构）；HIST 137（类 2）。
- **处置**：无活性引用，无需修复。

### C03 — dispatch wave

- **PROD（3）**：`runtime/cli.py:14-15`（`wave-prepare/wave-show` 退役注，类 3）；`runtime/commands/__init__.py:4`（"v2.3.1 Wave 2" 发布波次名，**误报**）。
- **TEST（3，类 3）**：`tests/test_cli_extensions.py:6`、`tests/test_recovery_v24.py:89`、`tests/test_quota_resolver.py:14`。
- **SKILL（5）→R4；TECHDOC（5）→R3**；HIST 52（类 2）。
- **处置**：无需修复。

### C04 — permit

- **PROD（14，全部类 3）**：`runtime/state.py:48,154-158,242-244,276`（v2.3 legacy 检测常量 `_LEGACY_COLLECTION_KEYS/_LEGACY_UNIT_FIELD_KEYS` 及文档——规格明令保留的检测面）；`runtime/workflow/compiler.py:42`、`runtime/workflow/persona.py:21`（U4 生成源**禁词扫描锚**，防回潮机制）；`runtime/cli.py:12-14`（退役子命令注）；`runtime/recovery.py:145`、`runtime/task.py:288`、`hooks/stop_gate.py:55`（拆除/排除清单）。
- **TEST（10，类 3）**：legacy 负例 + `tests/test_workflow_compiler.py:62` `FORBIDDEN_SOURCE_TOKENS` 禁词锚。
- **SKILL（8）→R4**（`skills/enforcement/SKILL.md:57` dispatch permit 闸教程）；**TECHDOC（8）→R3**（`docs/core-concepts.md:60,102`）；**USRDOC（1）→R2**（plugin.json）；HIST 81（类 2）。
- **处置**：无需修复。

### C05 — per-unit lease

- **PROD（19，全部类 3 + 1 耦合锚）**：`runtime/state.py` legacy 检测常量/注释（7 处）；`runtime/ownership.py:302`（否定句“不引入任何锁、租约”）；`runtime/recovery.py:28,145`（删除面）；`runtime/journal.py:47`、`runtime/task.py:121,288`、`hooks/stop_gate.py:55`、`runtime/cli.py:12`（退役/排除注）；`scripts/validate_plugin.py:306`——**耦合锚**：check 14 要求 orchestration 技能含 “租约” 标记（技能重写时必须同步改锚，见 §4 约束 2）。
- **TEST（14，类 3）**：legacy fixture（`work_units[].lease`）与禁词表。
- **SKILL（4）→R4；TECHDOC（14）→R3**（`docs/core-concepts.md:59-60` 租约闸/租约生命周期、`docs/architecture.md:295,405` 租约崩溃恢复）；HIST 78（类 2）。
- **处置**：无需修复。

### C06 — Work Unit 运行时状态

- **PROD（14，全部类 3）**：`runtime/state.py` 的 `work_units` legacy 检测（`state.py:48,154-158,242-244,258-265,276`）+ `runtime/recovery.py:271`、`runtime/task.py:287`（遗留标记注）。注意：`runtime/work_unit.py` 是 v2.4 **活模块**（静态节点 `validate_node`）——“Work Unit 运行时状态” 退役的是运行时账本/状态面，模块名保留是 v2.4 设计，非残留。
- **TEST（14，类 3）**：legacy 负例 fixture。
- **SKILL（3）→R4**（orchestration “工作单元与任务图” 标记）；HIST（2，类 2）。
- **处置**：无需修复。

### C07 — 单元验证 receipt（verify-unit / verify-task / durable verification receipt）

- **PROD（10，全部类 3 + 1 耦合锚）**：`hooks/stop_gate.py:55-56`（拆除清单：逐命令 verification 证据与 `verification_stale/verification_missing` 词汇）；`runtime/state.py:49,588`（迁移注）；`runtime/cli.py:15`（`verify-unit/verify-task` 退役注）；`runtime/recovery.py:28,145`；`runtime/work_unit.py:24`（否定句）；`scripts/validate_plugin.py:84`（本次改锚说明注）、`:310`——**耦合锚**：check 14 要求 enforcement 技能含 `verification_stale/review_stale`（§4 约束 2）。
- **TEST（24，类 3）**：禁词表、legacy 负例、退役锚定。
- **SKILL（64，重灾区）→R4**：`skills/enforcement/SKILL.md` 全篇以 verify-unit/verify-task/receipt 为教程主线（:32,64,136 等）；**TECHDOC（27）→R3**（`docs/core-concepts.md:97` durable receipts、`docs/architecture.md:392`）；**USRDOC（11）→R2**（plugin.json description "fingerprint-bound receipts" 等）；HIST 100（类 2）。
- **处置**：无代码活性引用。

### C08 — 评审 receipt 权威（fresh ship review receipt 唯一权威）

- **PROD（20）**：
  - **类 1 ×1（已修 ✅）**：`scripts/validate_plugin.py` check_15 原以 “enforcement 审查 receipt 权威标记（run_review / review-record）” 为题、要求技能含 `run_review` 或 `review-record`、正文教授 “fresh ship review receipt 唯一权威”（receipt 由 `runtime.provenance.run_review` 落盘——该模块已随 v2.3 执行面删除，`run_review` 现仅存在于陈旧技能文本）。已重写为 `check_15_enforcement_review_record`：只锚定 v2.4 活路径 `review-record`（runtime.task.record_review，新鲜度锚定 change_id），标题/文档串/PASS-FAIL 文案全部换为 v2.4 口径（validator 15/15 仍绿，当前技能文本含 `review-record` 故通过）。
  - 其余（类 3）：`runtime/cli.py:33,180`（“v2.3 的 durable review receipt 与调用真实性回验链已随执行面退役”）；`hooks/stop_gate.py:54`（拆除清单）；`runtime/change_id.py:13`（否定句“不提供任何 per-unit 指纹 / receipt API”）；`runtime/task.py:15`；`runtime/workflow/persona.py:21`（禁词锚）；`scripts/validate_plugin.py:109,1066,1068`（本次改锚说明）。
- **TEST（8，类 3）**：legacy `receipts` 键负例、禁词锚。
- **SKILL（10）→R4**（enforcement:32,64,136）、**TECHDOC（8）→R3**（`docs/architecture.md:313-315,393`）、**USRDOC（3）→R2**；HIST 65（类 2）。
- **处置**：唯一活性引用已修复。

### C09 — agent-run ledger

- **PROD（2，类 3）**：`runtime/cli.py:14`（`agent-runs` 退役注）、`runtime/recovery.py:27`（删除面清单）。
- **TEST（4，类 3）**：`tests/test_recovery_v24.py:33,78,87`（legacy 导入禁词）。
- **SKILL（7）→R4；TECHDOC（4）→R3**（architecture:378）；HIST 33（类 2）。
- **处置**：无需修复。

### C10 — reconcile 小任务状态

- **PROD（0）**：生产面零命中 ✅（`runtime/reconcile.py` 已随 v2.3 执行面删除）。
- **TEST（3，类 3）**：`tests/test_recovery_v24.py:34,78,88`（禁导入词锚）。
- **SKILL（14）→R4；TECHDOC（12）→R3**（`docs/core-concepts.md:60`、`docs/architecture.md:405`）；HIST 55（类 2）。
- **处置**：无需修复。

### C11 — resume manifest

- **PROD（1，类 3）**：`runtime/cli.py:15`（`manifest-show` 退役注）。**类 1 ×1（已修 ✅）**：`runtime/cli.py` `_writer_show` 文档串原以已退役的 `manifest-show` 输出形态作现役对比（“manifest-show 的 {"manifest": ...} 同款包裹形态”），已改为直接描述单键包裹形态。
- **TEST（4，类 3）**：`tests/test_recovery_v24.py:33,78,84,87`（W5 规格点名的 `resume_manifest` 导入禁词）。
- **SKILL（6）→R4；TECHDOC（5）→R3**；HIST 20（类 2）。
- **处置**：对比注已修。

### C12 — Bash policy（授权策略 policy-show / policy-set-*）

- **PROD（3，类 3）**：`runtime/cli.py:21-22`（三子命令退役注）、`:182`（与已退役 `policy-set-resume` 的 auto_resume 闸同口径的历史对比注）。
- **SKILL（2）→R4；TECHDOC（1）→R3**；HIST 12（类 2）。
- **处置**：后端已删，无需修复。

### C13 — finalizing（完成请求态）

- **PROD（3，全部类 3）**：`runtime/state.py:104`（否定句“不再需要 finalizing 完成请求态”）、`:748`（历史注：v2.3 曾有 finalizing→completed 门内通道）；`hooks/stop_gate.py:54`（拆除清单）。**类 1 ×1（已修 ✅）**：`runtime/state.py` `save_state` 的 `_gate_commit=False` 保留形参（v2.3 完成门内部通道的唯一代码残迹，全仓零调用方）已删除，签名收敛为 `save_state(repo_root, state, *, task_id=None)`，docstring 保留一行历史注。
- **TEST（2，类 3）**：`tests/test_state_v24.py:49,376`（v2.3 阶段态 legacy 负例词表含 `finalizing`——检测面）。
- **SKILL（5）→R4；TECHDOC（5）→R3**（`docs/architecture.md:331` 仍整段描述 finalizing 通道）；HIST 14（类 2）。
- **处置**：形参已删，历史注保留。

### C14 — 执行相位 DRAINING/BLOCKED

- **PROD（0 draining；已修 ✅）**：原 `runtime/state.py:193-198` `QUOTA_SUBSCRIPTION_MINIMUM_STATES = ("AVAILABLE", "PRESSURE", "DRAINING", "EXHAUSTED")`——v2.3 执行相位/订阅词汇常量，注释声称“仅为 runtime.continuity.subscription 的模块作用域消费保留”，但该消费模块已在 Phase 3 删除，常量零消费方（`git grep` 仅命中定义处与文档）→ 已连同过时注释整块删除，模块 docstring 同步改写（§3 修复 2）。
- **同名辨析**：v2.4 任务七态中的 `blocked`（`state.TASK_STATUSES`）与 quota 解析的 `_blocked_resets`（`runtime/quota/parser.py:431`，窗口数学）与退役的“执行相位 BLOCKED”同词不同义，非残留。
- **TEST（2，类 3）**：`tests/test_quota_resume_v24.py:239,431`——`quota_view("DRAINING")` 作**词汇外负例**（验证 v2.4 观测词汇恰为 AVAILABLE/PRESSURE/EXHAUSTED/UNKNOWN），检测面保留。
- **SKILL（2）→R4；TECHDOC（4）→R3**（`docs/core-concepts.md:134` 仍教授 NORMAL/PRESSURE/DRAINING/BLOCKED 四相位）；HIST 10（类 2）。
- **处置**：常量已删；负例测试保留。

### C15 — quota watcher

- **PROD（3，全部类 3；另有 4 处类 1 已修 ✅）**：`runtime/durable_io.py:5`（v2.2 根因历史注，保留）；`runtime/durable_io.py:38`、`:82`（原为“与 quota.watcher_store 同纪律/数值一致”的现役对齐声明——该模块已删，已改写为独立声明+历史注）；`runtime/quota/__init__.py:7`（删除清单）。已修：`runtime/journal.py` 3 处（随控制面 journal API 删除）、`runtime/quota/resolver.py:222`（“主会话 resolve + watcher 强制刷新”多进程写者描述，已改为“并行发起的 resolve / --force-refresh 调用”）。
- **TEST（0）**。
- **SKILL（8）→R4**（continuity 的 `quota-watcher start|status|stop|once` 教程，:182,226）；**TECHDOC（18）→R3**（`docs/architecture.md:256` 仍把 watcher/watcher_store 列为现役模块）；HIST 24（类 2）。
- **处置**：代码活性描述已修；常驻观测进程确无残留（无 watcher 模块、无进程面）。

### C16 — quota epoch

- **PROD（1，类 3）**：`runtime/journal.py:49`（退役词注：`quota_epoch_advanced` 词汇连同控制面 journal API 已在 Phase 3 删除）。**类 1 ×1（已修 ✅）**：控制面 journal API 整块（`CONTROL_PLANE_DIR_PARTS` / `control_plane_journal_path` / `append_control_plane_event` / `read_control_plane_events`，原 `journal.py:166-253`）——其为 Global Quota Clock 落 `quota_epoch_advanced`/`window_primed` 事件的唯一落盘面，全仓零调用方（含测试），已整块删除（§3 修复 1）。
- **误报**：`runtime/quota/parser.py:14,76` 的 `epoch_ms_to_iso`/“epoch 毫秒起点” 是 **Unix 时间戳纪元**，与 quota epoch 概念无关。
- **SKILL（5）→R4；TECHDOC（5）→R3**；HIST 17（类 2）。
- **处置**：控制面 journal API 已删。

### C17 — subscription/accounting

- **PROD（6；类 1 ×1 已修 ✅，余类 3）**：已修：`QUOTA_SUBSCRIPTION_MINIMUM_STATES` 常量及其“为 continuity.subscription 保留导入”的过时理由注释（见 C14；修复 2/3）。余（类 3）：`runtime/quota/__init__.py:8`（删除清单）、`runtime/quota/time_utils.py:16,18`（出处注“自 runtime.quota.accounting 逐字移入，accounting 已删除”）、`runtime/state.py:51`（legacy 处置面注 `quota_subscription` 授权块）、`runtime/state.py:56`（本次改写的历史注）、`runtime/task.py:621`（否定句“绝不引入 epoch/subscription/accounting 概念”）。
- **TEST（0）**。
- **SKILL（4）→R4；TECHDOC（13）→R3**（`docs/architecture.md:258`）；HIST 51（类 2）。
- **处置**：死常量已删。

### C18 — Window Primer

- **PROD（1，类 3）**：`runtime/quota/__init__.py:8`（删除清单：primer 控制面模块已删）。原 `runtime/journal.py` 的 2 处 `primer.json` 同层布局引用已随控制面 journal 删除（修复 1）。
- **SKILL（6）→R4**（continuity:228 “Window Primer（默认结构性关闭）”）；**TECHDOC（10）→R3**；HIST 26（类 2，含 `docs/roadmap/GLOBAL_QUOTA_CLOCK_EXTRACTION_INVENTORY.md` 的 must-not-migrate 清单——类 3 权威）。
- **处置**：无需进一步修复。

### C19 — Conductor 内 Global Quota Clock

- **PROD（4，全部类 3）**：`runtime/journal.py:10`（改写后的历史注：控制面 journal 已随 Clock 在 P3-D 删除）、`runtime/quota/__init__.py:8-9`（clock/clock_store/identity 删除清单）、`runtime/cli.py:25`（时钟四子命令退役注）、`hooks/session_start.py:46`（Q4 收口注：时钟 advisory 渲染块已删）。
- **TEST（4，类 3）**：`tests/test_session_start_advisory.py` 整文件为 **advisory 删除后的回归锚定**（验证任何遗留时钟 state 形态下钩子恒静默）——保留。
- **SKILL（9）→R4**（continuity:232 “v2.3 Global Quota Clock 与职责边界” 节）；**USRDOC（13）→R2**（`README.md:39,84-94,157` + `README.zh-CN.md` 镜像 + plugin.json description "account-level global quota clock"）；**TECHDOC（24）→R3**（`docs/architecture.md:262`、`docs/troubleshooting.md:9,58-70` 整节 “Quota Clock 失联诊断”——引用已删除的 `quota-clock-status` CLI / `runtime/commands/quota_clock.py` / `clock.py`）；HIST（95，类 2/3：`docs/roadmap/GLOBAL_QUOTA_CLOCK_EXTRACTION_INVENTORY.md` 是拆离清单权威，保留）。
- **处置**：Conductor 生产图无 Clock 代码（P3-D 收口 + 本次 journal API 删除后无残迹）；文档面指派。

### C20 — Activation Transport

- **PROD（1，类 3）**：`runtime/cli.py:24`（`transport-status` 子命令退役注）。
- **误报**：`runtime/quota/_http.py:55,126,137` 等 `transport` 是 HTTP 传输注入点（urllib），与激活运输概念无关。
- **SKILL（2）→R4**（continuity:262）；**TECHDOC（5）→R3**（architecture:261）；HIST 17（类 2）。
- **处置**：无需修复。

### C21 — Task Wake Bridge

- **PROD（3，全部类 3）**：`runtime/cli.py:24`（Persistent Wake Bridge 八子命令退役注）、`runtime/commands/__init__.py:12-13`（Q4 收口注）、`runtime/recovery.py:30`（v2.2 M9 桥接对账删除面注）。hooks 面已确认只剩 SessionStart + Stop（`hooks/hooks.json`，且其 description 显式记录 W6 退役——类 3）。
- **TEST（2，类 3）**：删除回归锚定负例。
- **SKILL（19）→R4**（`skills/continuity/SKILL.md:85,181,240,262` wake_bridge_* 事件词与 wake-plan 教程、`long-horizon.md:238` 移除定时任务指引）；**USRDOC（6）→R2**（`README.md:39,88-90` + zh 镜像 + plugin.json "precise wake bridging"）；**TECHDOC（13）→R3**（`docs/architecture.md:67` 仍画 `wake_bridge.py` 模块）；HIST 75（类 2）。
- **辨析**：v2.4 保留语义是“原生 Scheduled Task wake”（宿主 CronCreate 一次性唤醒，`runtime/task.py` scheduled_activation_decision）——R4 重写 continuity 技能时须保留该表述、只删 Wake Bridge 控制面教程。

### C22 — 直写 ZCode scheduler SQLite

- **PROD（15；sqlite 命中 = 0）**：15 处全部为 `scheduler` 的**出处/删除注**（类 3）：`runtime/quota/parser.py:8-9,263`、`runtime/quota/report.py:57`、`runtime/quota/resolver.py:88`（及本次改写的 `:133`）、`runtime/quota/time_utils.py:15,18,27`、`runtime/quota/window_math.py:16,34`、`runtime/quota/__init__.py:8`、`runtime/writer_guard.py:55`（否定句）、`scripts/validate_plugin.py:101,1034`。生产代码对 `sqlite`（大小写不敏感）**零命中**——P3 收口后无任何调度库写入路径 ✅。
- **TEST（3，类 3）**：禁词锚。
- **SKILL（3）→R4；USRDOC（2）→R2；TECHDOC（14）→R3**（`docs/troubleshooting.md:9` 仍描述“宿主库结构”依赖）；HIST 57（类 2/3，含 capability matrix tsv）。
- **处置**：零写入维持；文档面指派。

---

## 3. R1 当场修复清单（代码/门脚本；无 docs/skills 改动）

| # | 文件 | 修复内容 | 类别 | 性质 |
| --- | --- | --- | --- | --- |
| 1 | `plugins/glm-conductor/runtime/journal.py` | 删除控制面 journal API 整块（`CONTROL_PLANE_DIR_PARTS` / `control_plane_journal_path` / `append_control_plane_event` / `read_control_plane_events`，原 :166-253，约 90 行）——Global Quota Clock 的 `quota_epoch_advanced`/`window_primed` 事件落盘面，全仓零调用方；模块 docstring 的控制面职责段改写为历史注；`RECOMMENDED_EVENTS` 注释改为“控制面词汇连同 API 已删”；顺带把 `RECOMMENDED_EVENTS` 对齐 task.py 十名词汇（补 `quota_resume_confirmed`——P3-C 漏更新的文档性常量，原注释“恰九名一致”已失真，无测试锚定该常量内容） | 类 1 | 死代码删除 + 注释修 |
| 2 | `plugins/glm-conductor/runtime/state.py` | 删除 `QUOTA_SUBSCRIPTION_MINIMUM_STATES` 常量及其过时保留理由注释（原 :193-198）——声明消费者 `runtime.continuity.subscription` 已在 Phase 3 删除，常量零消费方；模块 docstring 对应段改写 | 类 1 | 死常量删除 + 注释修 |
| 3 | `plugins/glm-conductor/runtime/state.py` | `save_state` 删除 `_gate_commit=False` 保留形参（v2.3 finalizing→completed 门内通道唯一残迹，全仓零调用方、零测试引用）；docstring 保留历史注 | 类 1 | 死形参删除 |
| 4 | `plugins/glm-conductor/runtime/durable_io.py` | :38 与 :82 两处“与 quota.watcher_store 同纪律/数值一致”的现役对齐声明改为“原对齐模块已删、本模块独立声明”（:5 的 v2.2 根因历史注保留） | 类 1 | 注释修 |
| 5 | `plugins/glm-conductor/runtime/quota/resolver.py` | :157 `_format_iso_ms_z` 去掉“与 lease / permit 层时间字段同格式”死锚；:164-177 `_provider_identity_hash` docstring 去掉对已删 `runtime.quota.identity.compute_provider_identity_hash` 与“共享函数”的现役引用，改述 P3-D 后单一真相源即本函数（与 `tests/test_credentials_family.py` 实际锚定一致）；:133 时间助手注释去 `scheduler.py` 死锚；:222 多进程写者描述去“watcher 强制刷新” | 类 1 | 注释修 ×4 |
| 6 | `plugins/glm-conductor/runtime/quota/time_utils.py` | `_utc_now_iso` docstring 去掉“与 permit / 租约层时间字段同格式——wave 记录落盘口径”死锚，改为实际消费者（quota 缓存 fetched_at / 观测 evaluated_at） | 类 1 | 注释修 |
| 7 | `plugins/glm-conductor/runtime/writer_guard.py` | `acquire` docstring 去掉对已删 `runtime/lease.py` “重入不改 acquired_at” 先例的现役引用 | 类 1 | 注释修 |
| 8 | `plugins/glm-conductor/runtime/cli.py` | `_writer_show` docstring 去掉对已退役 `manifest-show` 输出形态的对比引用 | 类 1 | 注释修 |
| 9 | `scripts/validate_plugin.py` | check 14 的 `STOP_GATE_REQUIRED_MARKERS` 由 v2.3 完成门记账词（`gate_blocked/gate_passed/gate_exhausted/evaluate_task/verification_stale/review_stale`——现仅靠 stop_gate.py 拆除清单 docstring “意外满足”）改锚 v2.4 四查完成守卫活词汇（`COMPLETION_CHECKS/evaluate_completion/LEGACY_STATE_GUIDANCE/task_completed`）；`STATE_REQUIRED_MARKERS` 由已退役证据 API 词（`record_verification/record_review/visual_evidence`——仅靠 state.py 遗留注满足）改锚遗留检测与 quota_resume 活词汇（`detect_legacy_markers/LEGACY_STATE_GUIDANCE/DEFAULT_QUOTA_RESUME`）；check 15 重写为 `check_15_enforcement_review_record`（只锚 `review-record`，去 `run_review` 死词与 “receipt 权威” 口径）；模块 docstring 第 14/15 项同步 | 类 1 | 门脚本改锚 |

测试文件**零改动**：tests/ 中全部退役词命中均为类 3（legacy 负例 fixture、词汇外负例、禁词/禁导入锚、删除回归锚定），是规格明令保留的检测面。

---

## 4. 指派与移交（非 R1 ownership，未改动）

### R2（P4-B 用户文档 + 元数据）
`README.md` / `README.zh-CN.md`（Global Quota Clock + Task Wake Bridge 章节整章、executor: flash-implementer 示例）；`plugins/glm-conductor/.zcode-plugin/plugin.json`（description 仍含 "fingerprint-bound receipts / global quota clock / wake bridging / window consumption ledger"；version 仍 2.3.2）；`marketplace.json`（description 的 "long-task continuity" 表述需按新定位复核）；`docs/README.md`（索引指向 v2.4 文档）；`CHANGELOG.md` 增加 2.4.0 条目（历史条目原样保留）。

### R3（P4-B 技术文档）
`docs/architecture.md`（仍文档化 `wake_bridge.py`/`clock.py`/`watcher.py`/`dispatch_wave.py`/`provenance.py`/`reconcile`/permit 门/租约闸/finalizing 通道/review receipt 权威/subscription/Layer A/B——按规格重写为 v2.4 十四主题，不得以 deprecated 名义保留）；`docs/core-concepts.md`（permit/租约/receipts/reconcile/执行相位四态教程）；`docs/troubleshooting.md`（Quota Clock 失联诊断整节引用已删 CLI 与模块，需改为 quota-report 诊断 + legacy 任务处置指引 + writer-guard --force）。

### R4（P4-C 技能 + 命令帮助）
`skills/continuity/SKILL.md` + `references/long-horizon.md`（wake bridge / quota watcher / Window Primer / Global Clock / flash-implementer 教程）；`skills/enforcement/SKILL.md`（verify-unit/verify-task/receipt 权威主线）；`skills/orchestration/SKILL.md` + `references/*`（flash-implementer 契约、dispatch CLI）。`commands/quota.md` 已在 P3 收口为 quota-resolve/report，无退役词命中 ✓；agents/ 三文件无退役词命中 ✓。

### Validator 耦合约束（R3/R4 必须同步，否则门崩）
1. `scripts/validate_plugin.py:177` `RETIRED_AGENTS` 豁免带 `TODO(Phase 4)`：R4 重写技能清掉 flash-implementer 历史引用后**必须移除该豁免**。
2. check 14 `SKILL_CONTRACT_MARKERS` 仍锚定陈旧技能词汇：continuity 需含 `task_created/task_fingerprint/Quota-Aware Scheduling`，orchestration 需含 `租约/工作单元与任务图/task_fingerprint`，enforcement 需含 `gate_exhausted/Layer A/Layer B/verification_stale/review_stale`——R4 改写技能时**必须在同一变更中改锚**这些标记（R1 未动它们：现在改会让 validator 立即 FAIL，破坏 R2-R4 的门）。
3. check 9/10/11 锚定 `docs/architecture.md` 与 continuity 技能的 v2.3 标记（`foreground / resumable / idle`、`最新 checkpoint` 等）——R3 重写 architecture.md、R4 重写 continuity 技能时同步改锚。
4. check 15（R1 已重锚）只要求 enforcement 技能含 `review-record`——R4 新技能文本必须保留该词（v2.4 活路径）。

---

## 5. 相邻观察（非 22 概念清单内，供 R5/R6 参考）

- **Layer A / Layer B 词汇**：`runtime/ownership.py:31`、`hooks/session_start.py:37`（ENFORCEMENT DEGRADED 前缀）、`hooks/stop_gate.py` 注释仍用 v2.3 “Layer A” 执法平台词汇——概念不在 22 清单内，未处置；R4 重构 enforcement 技能时一并定夺代码注释是否去 Layer 化。
- **陈旧 `__pycache__` 字节码**：`plugins/glm-conductor/runtime/__pycache__/`（dispatcher/lease/reconcile/resume_manifest/agent_run 等 cpython-37/310 残影）与 `tests/__pycache__/`（test_public_surface/test_quota_subscription）——gitignored 非源文件、Python 3 不会无源导入，不构成活性引用；属构建残留，建议 R5 全量回归前清理（R1 未动：非本单元 ownership）。
- **预存 ruff F401 ×3**（HEAD 上即存在，非本次引入）：`tests/test_change_id.py:31`（hashlib）、`tests/test_quota_resolver.py:47`（runtime.journal、runtime.state）——CI ruff 步骤会报；归 R5 测试重置时顺手清理。
- **`runtime/state.py:142` `TASK_ID_KEYS` 含 `CONTINUITY_ID`**：v1.x legacy 标识归一容错面，规格明示保留（类 3），validator check 14 仍锚定之。

---

## 6. 门与证据（R1 自跑，2026-09-21）

1. **审计文档断言**：`python3 -X utf8 -c`（open 本文件 + 断言总表标题与 C01–C22 小节标题、小节数 ≥ 21）→ **PASS**（详见执行记录）。
2. **幸存定向套件**：`python3 -X utf8 -m unittest tests.test_state_v24 tests.test_change_id tests.test_task_lifecycle tests.test_quota_resume_v24 tests.test_completion_guard tests.test_recovery_v24` → `Ran 207 tests ... OK`。
3. **插件校验器**：`python3 -X utf8 scripts/validate_plugin.py` → `15/15 项通过, 0 项失败`（含本次改锚后的 check 14/15）。
4. 附加回归（覆盖被改模块）：`python3 -X utf8 -m unittest tests.test_journal tests.test_writer_guard tests.test_durable_io tests.test_quota_resolver tests.test_quota_report tests.test_cli_extensions tests.test_cli_v24 tests.test_workflow_compiler tests.test_session_start_advisory` → `Ran 190 tests ... OK`。
5. ruff（信息性）：仅 HEAD 已存在的 3 条 F401（见 §5），本次改动零新增 lint 违规。

> 纪律遵守：本单元未执行任何 `git commit/add/push`；未跑全量回归（R5 专属）；未打印任何凭证。
