**GLM Conductor v2.0.0**

**项目全面审查与 v2.0.1 Runtime Hardening 实施建议**

可交付技术审查文档

| **审查对象**     | Chengy257/glm-conductor                         |
|------------------|-------------------------------------------------|
| **审查基线**     | main @ ab825b1b582b57045bd5197c107cdeb108eae39f |
| **版本**         | v2.0.0                                          |
| **审查日期**     | 2026-08-29                                      |
| **最新 CI 基线** | 14/14 静态校验通过；635 个 unittest 全部通过    |
| **文档定位**     | v2.0.1 加固与后续 v2.1 演进的实施依据           |

仓库：[https://github.com/Chengy257/glm-conductor](https://github.com/Chengy257/glm-conductor)

# 1. 文档目的与审查范围

本文档将对 GLM Conductor v2.0.0 当前主分支的架构、运行时强制层、长任务连续性、Quota-Aware Continuity、Work Unit/DAG 调度、有界并行与租约、恢复对账、测试/CI 与文档契约进行系统审查，并将发现转化为可直接进入 v2.0.1 milestone 的整改工作项。

> [!IMPORTANT]
> **审查结论：** v2 主体功能已经真实落地，项目已从“Prompt/Agent 约定”演进为带 durable state、completion enforcement、DAG scheduling、quota-aware continuity 与 bounded concurrency 的轻量 agent orchestration runtime。下一阶段应冻结功能扩张，优先做 runtime integrity hardening。

本轮重点检查以下问题：

- 关键契约是否真正由 runtime 强制，而非仍依赖模型正确写状态；
- 长程任务在额度耗尽、会话中断、进程崩溃后是否可安全恢复；
- 多 Work Unit 并发、ownership、lease 与 dispatch 状态是否形成一致的生命周期闭环；
- quota 查询、凭证与恢复规划是否具备安全边界与可靠降级；
- 当前测试/CI 是否覆盖项目公开承诺的运行环境与核心不变量。

# 2. 执行摘要

> [!IMPORTANT]
> **总体判断：** v2.0.0 已达到“功能完整、可实际使用”的水平，但尚未达到“所有核心契约均无法通过状态顺序、漏字段或损坏状态绕过”的程度。建议 v2.0.1 作为 correctness / integrity hardening release，而不是继续叠加新功能。

| **维度**               | **评价** | **结论**                                                        |
|------------------------|----------|-----------------------------------------------------------------|
| 架构设计               | 优秀     | Prompt / state / enforcement / quota / DAG / lease 模块边界清晰 |
| 模块完整度             | 优秀     | v2 关键计划基本已实现，不再停留在文档层                         |
| 测试体系               | 优秀-    | 635 tests + 14/14 静态校验；但偏模块契约测试                    |
| 完成门可靠性           | 中上     | 正常路径扎实，但存在关键 invariant bypass                       |
| Quota-Aware Continuity | 良好     | 观察、四态评估、恢复规划已内化；唤醒执行仍依赖 ZCode            |
| 多 Agent / Work Unit   | 良好     | DAG、dispatcher、reconcile 已形成完整骨架                       |
| 并行安全               | 中上     | bounded parallel + lease 已实现，crash recovery 仍需补强        |
| 跨会话恢复             | 中上     | repository-first 思路正确，顶层 lifecycle/lease 尚未完全事务化  |
| 安全设计               | 优秀-    | 凭证零落盘、allowlist、禁 redirect、限长响应等设计成熟          |
| Production hardening   | 中等偏上 | 当前最值得投入的工程方向                                        |

**必须优先处理的 P0：**

- P0-1：顶层 task 提前写入 completed 可绕过 Stop Completion Gate；
- P0-2：route.assurance=high 并不会自动强制 review.required=true；
- P0-3：损坏的 active state.json 会在任务发现阶段被静默跳过，从而表现成“无任务需要 enforcement”。

# 3. P0：必须在 v2.0.1 优先修复的问题

## **P0-1 顶层 completed 状态可绕过 Stop Completion Gate**

> [!WARNING]
> **P0 风险：** 当前 active task 定义排除了 completed / cancelled / failed；Stop Gate 又只扫描 active task，因此主会话在 Stop 前先写 completed，会使任务直接退出完成门检查集合。

**触发路径：**任务完成流程中，主会话先将 state.status 更新为 completed，再触发 Stop；find_active_tasks() 不返回该任务，stop_gate.py 因 active 为空直接放行。

**影响：**完成门不再是“完成状态的唯一提交点”。ownership、verification、review、visual freshness 等检查都可以被生命周期顺序绕过，直接削弱 v2 的核心定位。

**建议修改：**

- 新增顶层任务状态 finalizing（或等价阶段），普通运行时只能进入 finalizing，不能直接进入 completed。
- 将 completed 设为 Completion Gate 的唯一 commit point：gate_passed 后由 runtime 原子写入 completed 并追加 completed journal event。
- 新增顶层 task transition table，禁止任意状态直接写 completed；必要时提供受控迁移函数。
- 文档同步改为“进入 finalizing 即请求完成；只有 completion gate 通过后才进入 completed”。

**验收标准：**

- 构造一个 verification_missing / review_missing 的 finalizing task，Stop 必须 block。
- 直接尝试通过公共 API 从运行态写 completed 必须失败。
- 只有 gate_passed 路径可以生成 completed 状态与 completed journal event。
- 回归测试覆盖 gate_exhausted、normal pass、corrupt state、multiple active task。

**主要证据路径：**plugins/glm-conductor/runtime/state.py；plugins/glm-conductor/hooks/stop_gate.py；plugins/glm-conductor/skills/continuity/SKILL.md；docs/architecture.md

## **P0-2 assurance: high 与 review.required 缺乏运行时不变量绑定**

> [!WARNING]
> **P0 风险：** route.assurance 与 review.required 目前是两个独立字段；new_task_state() 默认 review_required=False，validate_state() 没有推导两者关系，Stop Gate 只在 review.required is True 时检查审查证据。

**触发路径：**可构造 mode=full / assurance=high 但 review.required=false 的合法 state；Stop Gate 不进入 review 检查分支。

**影响：**“high-assurance 任务必须有独立 fresh ship 裁决”仍依赖主会话正确写状态，而不是 deterministic enforcement。

**建议修改：**

- 新增 validate_route_invariants()，由 mode / delegability / assurance / executor 推导强约束。
- audit/full 或 assurance=high 时强制 review.required=true；standard 时允许 false。
- delegate/full 强制 ownership.files 与 verification.required 非空；solo/audit 与 executor=main 保持一致。
- 必要时不再让调用方直接传 review_required，而由 route 自动派生默认值，显式 override 仅用于迁移。

**验收标准：**

- 非法组合在 state.save_state 前即被拒绝。
- full + review.required=false、audit + review.required=false 等组合有专门单测。
- Stop Gate 不依赖“模型记得把 review.required 写对”即可满足 high-assurance 约束。
- README / architecture / skill 中的路由承诺与 validator 一致。

**主要证据路径：**plugins/glm-conductor/runtime/state.py；plugins/glm-conductor/hooks/stop_gate.py；plugins/glm-conductor/skills/orchestration/SKILL.md；docs/architecture.md

## **P0-3 损坏的 active state.json 会从 enforcement 发现阶段静默消失**

> [!WARNING]
> **P0 风险：** find_active_tasks() 为保证扫描健壮性会跳过 JSON 损坏、缺 task_id、读取异常等目录；但 Completion Gate 使用同一结果作为唯一强制集合。

**触发路径：**active task 的 state.json 半写、损坏或权限异常 → find_active_tasks() skip → 若没有其他活动任务，Stop Gate 直接放行。

**影响：**state corruption 被解释为“没有任务需要强制”，无法产生 gate_degraded / blocked 的明确运行痕迹，可能造成错误完成声明。

**建议修改：**

- 把任务发现改为 discover_tasks()，至少区分 active / terminal / corrupt / orphaned。
- Completion Gate 对 corrupt/orphaned 不能静默视为不存在；高保障或 finalizing 状态优先 fail-closed，普通任务至少必须 degraded + user-visible warning。
- 保留 find_active_tasks() 作为兼容 helper，但 Stop Gate 使用 richer discovery result。
- 增加损坏 state 的恢复指引：保留目录、读取 journal/checkpoint、repository-first 重建或人工裁决。

**验收标准：**

- 损坏 state.json 时 Stop 不再静默通过。
- corrupt 状态有结构化 journal / stderr 记录，并能明确区分 no task 与 unreadable task。
- 恢复后可重建合法状态而不回滚仓库实际改动。

**主要证据路径：**plugins/glm-conductor/runtime/state.py；plugins/glm-conductor/hooks/stop_gate.py；plugins/glm-conductor/runtime/journal.py

# 4. P1：v2.0.1 / v2.1 应继续加固的问题

| **编号** | **问题**                                | **当前风险**                                                                 | **建议归属** |
|----------|-----------------------------------------|------------------------------------------------------------------------------|--------------|
| P1-1     | Verification provenance 不够强          | completed+fingerprint 可由模型直接落账，runtime 未亲眼观察命令 exit code     | v2.1         |
| P1-2     | Review provenance 不够强                | ship+fingerprint 未绑定 reviewer invocation/context                          | v2.1         |
| P1-3     | 缺少事务化 Task Manager                 | plan/lease/state/dispatch 由主会话分步执行，崩溃可留下半状态                 | v2.0.1       |
| P1-4     | Lease 无 TTL/generation/heartbeat       | 崩溃后 stale lease 可能永久残留                                              | v2.0.1       |
| P1-5     | Reconcile 未闭环清理 lease              | 恢复只关注 running/verifying unit，不完整覆盖租约生命周期                    | v2.0.1       |
| P1-6     | 多顶层 active task 的 diff 作用域不清   | 全仓 touched 分别与每个 task ownership 比较，合法 disjoint task 可能互相误伤 | v2.1         |
| P1-7     | Work Unit 验证事件未强绑定 unit id      | 相同 command / 重叠 ownership 场景存在错误复用证据的理论空间                 | v2.0.1       |
| P1-8     | Quota cache 仅浅拷贝                    | windows\[\] 嵌套对象可被调用方修改并污染 TTL cache                           | v2.0.1       |
| P1-9     | 目录 task_id 与 state.task_id 可错位    | save_state(task_id=...) 允许写入与内容 ID 不一致的目录                       | v2.0.1       |
| P1-10    | 并行文档 OR/AND 语义不一致              | 文档写 ownership 不相交“或”lease；实际 dispatcher 两道闸均须通过             | v2.0.1       |
| P1-11    | CI 平台覆盖不足                         | 公开支持 Windows / 较老 Python，但 CI 主要 Ubuntu + latest Python            | v2.0.1       |
| P1-12    | gate_exhausted 与“绝对无法通过”表述冲突 | 连续 block 达上限后允许 Stop，文档承诺应更精确                               | v2.0.1       |

## 4.1 建议新增 runtime/task_manager.py

当前 dispatcher 明确是纯决策器；真实生命周期仍要求主会话按顺序完成 plan_dispatch → acquire_lease → transition ready→running → dispatch.active 记账 → save_state → Agent 调用。中间任一点中断，都可能形成“计划已批准但未加锁”“已加锁但未记 running”“running 已落盘但 Agent 未真正启动”等半状态。

> plan_dispatch()
>
> ↓
>
> acquire_lease()
>
> ↓
>
> transition ready → running
>
> ↓
>
> dispatch.active += unit_id
>
> ↓
>
> save_state()
>
> ↓
>
> invoke Agent

建议新增事务边界层，提供 prepare_dispatch / commit_dispatch / abort_dispatch / finish_unit 等高层 API，并把 journal、lease、state 更新的顺序固定下来。这样主会话仍是唯一 orchestrator，但不再承担细粒度状态一致性的手工拼接。

## 4.2 Lease 需要 crash recovery 语义

现有 lease 已具备 all-or-nothing、同 owner 幂等、异 owner 拒绝与原子写，是正确基础。但 acquired_at 尚未参与失效判断，崩溃后 release 未执行会永久占用。建议 lease record 增加 session_id / generation / heartbeat_at / expires_at（至少 generation + recovery epoch），恢复时与 work unit / repository evidence 对账。

> {
>
> "owner": "WU-3",
>
> "task_id": "...",
>
> "session_id": "...",
>
> "generation": 7,
>
> "acquired_at": "...",
>
> "heartbeat_at": "...",
>
> "expires_at": "..."
>
> }

## 4.3 Quota provider cache 应改为 deep-copy / immutable snapshot

HttpQuotaProvider.fetch() 目前通过 dict(snapshot) 返回浅拷贝，只保护顶层字段。调用方若修改 windows\[0\] 等嵌套对象，可反向污染实例 TTL cache。建议使用 copy.deepcopy()，或将 snapshot 固化为不可变 value object；补充 nested mutation regression test。

## 4.4 Evidence provenance 的长期升级方向

当前完成门已经很好地实现“freshness enforcement”：required 是否完成、fingerprint 是否新鲜、review verdict 是否 ship。但 record_verification / record_review 仍允许主会话直接写状态，runtime 不能证明命令真的执行过、reviewer 真的被调用过。长期建议引入统一 evidence wrapper。

> verification evidence:
>
> command
>
> exit_code
>
> started_at / finished_at
>
> fingerprint
>
> executor = parent
>
> review evidence:
>
> reviewer
>
> invocation_id
>
> verdict
>
> review_artifact_hash
>
> fingerprint

这样可将项目从“模型告诉 runtime 已验证”升级到“runtime 亲眼观察验证”。该项适合 v2.1，不建议阻塞 v2.0.1。

# 5. Quota-Aware Continuity 审查结论

该部分已经从设计真正进入实现层，当前链路包含 credential resolver、Z.ai / BigModel provider、标准化 snapshot、四态评估（AVAILABLE / PRESSURE / EXHAUSTED / UNKNOWN）与 plan_resume。EXHAUSTED 取所有阻塞窗口最晚 reset + grace；reset 不可得则 periodic_fallback，不硬编码 5 小时，也不虚构重置时刻。

> credential resolver
>
> ↓
>
> Z.ai / BigModel provider
>
> ↓
>
> normalized quota snapshot
>
> ↓
>
> AVAILABLE / PRESSURE / EXHAUSTED / UNKNOWN
>
> ↓
>
> plan_resume()

> [!NOTE]
> **能力边界：** 当前真正 runtime 化的是“Quota observation + decision + resume planning”；真正的未来唤醒与执行仍依赖 ZCode 原生 scheduled / idle mechanism。这是合理边界，不建议自行引入 daemon、while-loop supervisor 或 cron wrapper。

建议后续把 plan → ZCode automation → resume → force refresh quota → reconcile → dispatch 进一步封装为 Task Manager 的高层操作，减少主会话手工拼接。

# 6. Work Unit / 多子智能体 / 有界并行审查结论

Work Unit、DAG、ready derivation、deterministic topo order、quota gate、ownership gate、lease gate、max_workers 与 reconcile 已形成完整骨架。尤其是“completed 不盲重跑、running 按 repository + journal evidence 三分恢复”的方向正确，已经明显超出纯 Prompt harness。

> Work Unit schema
>
> ↓
>
> DAG validation
>
> ↓
>
> ready_units + topo_order
>
> ↓
>
> quota gate
>
> ↓
>
> ownership gate
>
> ↓
>
> lease gate
>
> ↓
>
> max_workers budget
>
> ↓
>
> dispatch decision

当前最需要补的不是更多 Agent 类型，而是执行生命周期的事务边界与 lease crash recovery。并行本身仍应保持 experimental、默认 max_workers=1。

## 6.1 文档与实现的 OR / AND 不一致

部分文档将并行资格描述为“ownership 不相交 或 有效租约保护”，但实际 dispatcher 是先做 ownership_conflict，再做 lease_conflict：二者均通过才可派发。当前实现更保守、更合理，建议保留代码并修改文档。

> 推荐文档语义：
>
> ownership declarations 必须判定为可并行
>
> AND
>
> 不存在 foreign active lease conflict

# 7. v2.0.1 Runtime Hardening 实施计划

| **工作包** | **内容**                                                                                 | **优先级** | **主要模块**               | **完成标志**                                |
|------------|------------------------------------------------------------------------------------------|------------|----------------------------|---------------------------------------------|
| H1         | Completion lifecycle：finalizing → gate → completed，completed 只能由 gate commit        | P0         | state.py / stop_gate.py    | 不存在 terminal bypass                      |
| H2         | Route invariants：route/mode/assurance/executor/review/verification/ownership 一致性校验 | P0         | state.py                   | high assurance 不再依赖手工 review.required |
| H3         | Task discovery integrity：active / terminal / corrupt / orphaned 分类                    | P0         | state.py / stop_gate.py    | 坏 state 不再等价于 no task                 |
| H4         | Task Manager 事务边界：plan/lease/transition/journal/dispatch bookkeeping                | P1         | runtime/task_manager.py    | 派发半状态可恢复                            |
| H5         | Lease recovery：generation/TTL or epoch + reconcile                                      | P1         | lease.py / reconcile.py    | crash 后 stale lease 可判定与清理           |
| H6         | Evidence/unit binding：verification event 强绑定 work_unit_id                            | P1         | journal.py / reconcile.py  | unit 证据不可误复用                         |
| H7         | Quota deep copy + state ID consistency                                                   | P1         | quota/\_http.py / state.py | nested cache 不污染；目录 ID 与内容 ID 一致 |
| H8         | CI / docs alignment：Windows + Python matrix；修正 OR/AND 与 gate_exhausted 表述         | P1         | Actions / README / docs    | 公开承诺与实现一致                          |

**推荐实施顺序：**

1.  H1 → H2 → H3：先封住 deterministic enforcement 的三个核心缺口。

2.  H7 + H8：低风险快速修复，尽早清掉文档/缓存/CI 不一致。

3.  H4 → H5 → H6：形成可恢复、可事务化的 Work Unit/parallel lifecycle。

4.  v2.0.1 发布后再进入 v2.1 的 provenance wrapper 与更完整自动闭环。

# 8. v2.0.1 Definition of Done / 验收标准

- 任何 task 不能通过公共状态 API 在完成门前直接进入 completed；completed 仅由 gate commit。
- assurance:high / audit / full 无法构造 review.required=false 的合法状态。
- delegate/full 任务缺 ownership 或 verification 时无法进入可派发/可完成状态。
- 损坏或不可读 state.json 不再被 Stop Gate 当作“不存在任务”静默放行。
- plan_dispatch → lease → running → dispatch bookkeeping 的中断场景有确定性恢复策略。
- crash 后 orphan/stale lease 可以识别、保守处理并恢复，不要求人工删除 leases.json。
- Work Unit verification journal 明确带 work_unit_id，并由 reconcile 精确匹配。
- quota provider 返回嵌套对象被调用方修改后，不会污染 provider cache。
- state 文件目录 task-id 与 state.task_id 不允许不一致。
- 并行文档统一为 ownership gate AND lease gate 的实际实现语义。
- CI 至少覆盖 Linux + Windows 与明确的 Python minimum / latest 组合。
- 新增回归测试后，现有 635 tests 全部保持通过，新增 hardening 测试全部通过。

# 9. v2.1 后续演进建议

v2.0.1 完成后，项目可再进入能力扩张阶段。推荐优先级不是增加更多子智能体，而是把 evidence provenance 与 quota-resume-dispatch 闭环进一步 runtime 化。
- 统一 verify wrapper：runtime 真实执行验证命令并记录 exit_code、时间与 fingerprint；
- review invocation provenance：绑定 reviewer / invocation_id / artifact digest / verdict / fingerprint；
- Quota → scheduled wake → force refresh → reconcile → dispatch 的高层 Task Manager API；
- 多顶层 task 的 workspace diff 分区策略，避免 disjoint active tasks 互相 ownership 误伤；
- 更完整的 observability：结构化 task summary / recovery diagnosis，而不是增加遥测；
- 必要时再探索更高并发，但继续保持上限小、默认串行、repository-first。

# 10. 最终结论

GLM Conductor v2.0.0 已经从“教模型如何委派”的提示词插件，演进为以 ZCode 原生 Agent 为执行后端、带本地 durable state、completion enforcement、DAG scheduling、quota-aware continuity 与 bounded concurrency 的轻量 orchestration runtime。该方向具有清晰的独立价值。

当前最值得投入的不是继续叠加 route、agent 或 workflow feature，而是把已有能力收敛成一个无法被状态顺序、漏字段、损坏 state、进程崩溃或 stale lease 破坏的 orchestration kernel。建议立即将 v2.0.1 定位为 Runtime Integrity / Correctness Hardening Release，并以上述 H1-H8 作为主实施清单。

> [!IMPORTANT]
> **发布建议：** 先完成 P0-1 / P0-2 / P0-3，再发布 v2.0.1；此后再推进 Task Manager、Lease Recovery 与 Evidence Provenance。这样可最大化 v2 已有架构投入的可靠性收益，而不是继续增加表面功能。

# 附录 A：重点审查证据路径

- plugins/glm-conductor/runtime/state.py — task state schema、active discovery、verification/review 记录；
- plugins/glm-conductor/hooks/stop_gate.py — ownership / verification / review / visual completion gate；
- plugins/glm-conductor/hooks/pre_tool_use.py — ownership injection 与 Bash policy gate；
- plugins/glm-conductor/runtime/work_unit.py — Work Unit schema 与转换表；
- plugins/glm-conductor/runtime/dependency.py — DAG / ready / topo；
- plugins/glm-conductor/runtime/dispatcher.py — quota / ownership / lease / concurrency 准入；
- plugins/glm-conductor/runtime/lease.py — durable lease owner map；
- plugins/glm-conductor/runtime/reconcile.py — running / verifying 中断恢复对账；
- plugins/glm-conductor/runtime/quota/\* — provider / parser / scheduler / credentials / report；
- plugins/glm-conductor/skills/orchestration/SKILL.md — 路由、Work Unit、并行与审查契约；
- plugins/glm-conductor/skills/continuity/SKILL.md — long-horizon、state/journal、quota-aware scheduling；
- docs/architecture.md — 当前权威架构描述；
- CHANGELOG.md — v2.0.0 Definition of Done 与版本能力声明；
- tests/\* + GitHub Actions validate workflow — 当前 635 tests 与 14/14 静态校验基线。

# 附录 B：建议新增的关键回归测试

- finalizing_verification_missing_blocks_and_cannot_precomplete

- high_assurance_requires_review_required_true

- full_route_missing_review_invariant_rejected

- corrupt_active_state_does_not_silently_disappear

- gate_pass_is_only_path_to_completed

- quota_cache_nested_mutation_isolated

- state_directory_id_must_match_payload_task_id

- work_unit_verification_evidence_requires_matching_unit_id

- orphan_lease_reconciled_after_interrupted_running_unit

- dispatch_prepare_crash_recovery_before_agent_invocation

- dispatch_commit_crash_recovery_after_agent_invocation

- windows_hook_smoke_and_python_minimum_matrix
