# Changelog

## 2.0.1

v2.0.1 — runtime integrity / correctness hardening wave, driven by the v2.0.0 comprehensive review (`docs/GLM-Conductor-v2.0.0-全面审查与v2.0.1加固建议.md`, work packages H1-H8 plus release closeout). No new routing dimensions or roles; enforcement only tightens machine-checkable invariants:

- **P0 — completion lifecycle**: completion is requested by entering `finalizing`; `save_state` enforces the `TASK_TRANSITIONS` migration table; `completed` is only committable through the Stop gate's internal channel (`state.commit_completion`, `_gate_commit`)
- **P0 — route invariants**: `validate_route_invariants` enforces cross-field consistency at save time — route matrix (delegability × assurance → mode), executor binding, `review.required` derivation, and delegate/full substantive ownership + verification
- **P0 — discovery integrity**: `discover_tasks` classifies every task directory four ways (active / terminal / corrupt / orphaned) — a corrupt `state.json` is no longer silently treated as "no task"; the Stop gate handles unreadable tasks fail-closed with journal evidence
- **P1 — task_manager transaction boundary**: `prepare_dispatch` / `commit_dispatch` / `abort_dispatch` / `finish_unit` fuse plan → lease → status transition → `dispatch.active` bookkeeping → save → journal into fixed sequences, with documented crash-window recovery paths (prepare-interrupted → commit or abort safely; commit-interrupted → §69 reconciliation); plus `record_unit_verification` (the recommended entry point for unit-level verification evidence) and `refresh_readiness` (promotes dependency-satisfied pending / waiting_dependency units to ready — a dogfood gap: this promotion previously relied on manual transitions)
- **P1 — lease crash recovery**: lease records gain `session_id` / `generation` / `heartbeat_at` / `expires_at` — conservative default TTL of 1800s on the dispatch path, `renew_lease` heartbeats, generation bump on expired same-owner reacquisition (2.0.0-format records without `expires_at` stay readable and renewable); `reconcile_leases` triages leases into stale / active / expired_running and `recover_leases` releases only the stale set with a `lease_recovered` journal event — no manual `leases.json` deletion after a crash
- **P1 — unit-bound verification evidence**: journal verification events carry an explicit `unit` (work unit id) field; resume reconciliation matches evidence by exact unit id — identical commands / overlapping ownership between units can no longer cross-reuse evidence; unit-level evidence is written via `task_manager.record_unit_verification`, while task-level (completion-gate) evidence still goes through `state.record_verification`
- **P1 — misc hardening**: quota cache deep-copy isolation; `save_state` rejects directory/content `task_id` mismatch; CI matrix ubuntu + windows × Python 3.8/3.13 (README declares Python 3.8+); parallel-eligibility wording unified to AND semantics — the historical v2.0.0 wording "ownership 不相交或有效租约保护" is superseded by "ownership 不相交 **且** 无外来活跃租约冲突（两道闸门均须通过）"; old entries are left as written per changelog discipline
- **BREAKING**: resume reconciliation no longer trusts legacy verification journal events that lack a `unit` field — evidence that cannot prove ownership is conservatively treated as no evidence and the main session must re-verify. This is an intentional v2.0.1 change (documented in the architecture doc and the continuity skill)
- **Testing**: 635 → 775 tests, all green; `validate_plugin` 14/14. New/extended suites include task_manager (53, new module), lease (19 → 37: TTL / generation / renew / expired + crash recovery), reconcile (21 → 33: `reconcile_leases` + unit-bound evidence), state (90 → 125: transition gate / route invariants / four-way discovery), stop_gate (32 → 53: gate-only completion commit + discovery fail-closed)
- **Release hardening RB-1 — work-unit completion evidence gate**: `finish_unit(outcome="completed")` now requires fresh unit-bound verification evidence **before any transition** — via the shared `reconcile.fresh_unit_verification` all-match predicate (every required command needs at least one `verification` event with exact unit id, current `task_fingerprint`, and `pass` status; missing / stale / partial / wrong-unit / legacy no-`unit` evidence is rejected with **zero side effects** — no journal events, no state writes on rejection); git / fingerprint read failures during the check fail closed (`TaskManagerError` — freshness that cannot be determined means completion is refused); `failed` / `cancelled` outcomes need no evidence; the empty-required fast path stays zero-git (production units always carry ≥1 command per state schema validation). Predicate-level shape defense: events without a unit id can never match via `None` equality. Resume reconciliation now delegates to the same predicate, tightening suggestions from any-match to **all-match** (D2): multi-command units with partial evidence are no longer suggested `completed` (suggested `verifying` instead); the degenerate case — an interrupted unit with residual changes and empty/missing `verification` — now yields a `completed` suggestion via the fast path (unreachable in persisted state, defensive semantics for direct predicate callers; recorded per review checkpoint #1)
- **Release hardening RB-2 — task repository binding + per-task stop-gate evaluation**: `state.json` gains an optional top-level `repository.root` binding (`bind_repository_root` / `bound_repository_root` / `resolve_repository_root`; `new_task_state(repository_root=...)`; absent key = legacy, fully legal). The Stop gate resolves each participating task's **own** repository root (bound root first; legacy tasks fall back to the ledger root — single-repo behavior unchanged) with a per-repo touched/base snapshot cache (at most 1 `status` + 1 `rev-parse` per distinct root per Stop); repository failures no longer abort the whole Stop — degradation is **isolated per task** with structured reasons (`repository_unavailable` / `git_unavailable` (legacy transient) / `repository_root_missing` / `repository_ambiguous` / `evaluation_error`); nested-repo ambiguity is never guessed (candidates are reported, explicit binding required); ledger root and git root are separated (state / journal / lease always live on the ledger root); `finish_unit`'s RB-1 evidence gate splits its roots the same way (git root by binding, verification events injected from the ledger root) — multi-repo completion works
- **Testing**: 775 → 828 tests, all green; `validate_plugin` 14/14. New/extended suites: reconcile (33 → 46: shared all-match predicate + shape defense), task_manager (53 → 72: RB-1 rejection/completion matrix on a real-git fixture), state (125 → 133: repository.root binding), stop_gate (53 → 66: split-ledger per-task evaluation + two-repo isolation)

## 2.0.0

v2 正式版——「从提示词契约到强制执行契约」全量达成（§103 Definition of Done 全条目；alpha1→rc1 六个里程碑的完整能力清单见各条目，本条目记录 stable 收口）：

- **验收对照（§103）**：越界改动无法静默通过完成门（Layer A）；验证缺失/过期、审查过期自动拦截；高保障任务无新鲜 ship 裁决不能完成；额度凭证零落盘（专项安全审查锚定）；Work Unit 依赖确定性控制就绪、completed 不盲重放；quota 压力/耗尽抑制新派发；join 后任务级全局验证；并行有界（1-4）且租约保护（experimental 标记保留，§101 允许）；无通用工作流引擎引入（scope 审查全程把关）
- **校验器终版**：orchestration 租约标记（并行须租约文档化的机械锚点）；检查 1-14 全绿
- **测试**：635 用例全量绿（18 个测试文件：状态 90/日志 26/ownership 38/指纹 59/完成门 32/派发注入 18/策略 20/工作单元 57/依赖 52/调度 55/对账 21/租约 19/quota 148）
- **发版动作**：v2-dev 合入 main（main 此前保持 v1.1.0 发布态）、tag v2.0.0、GitHub Release 与市场更新

## 2.0.0-rc1

v2 第六里程碑——**租约与有界并行**（升级指南 §78-§83，实施计划轮 9 块 B9/B10）：

- **文件租约（`runtime/lease.py`，§78-§79）**：任务专属 `leases.json` owner map（原子写）——派发前全有或全无获取（同 owner 幂等、异 owner 精确冲突拒绝且零部分写入）、写相结束释放（只释放自持项）；安全并行派发的前提层
- **有界并行启用（`dispatcher.py` 集成，§80-§83，experimental）**：准入第六闸 `lease_conflict`（候选 ownership vs 他人租约，复用模式冲突混判）；max_workers 限定 1-4（§82 推荐上限，禁无界扇出）；并行资格 = ownership 不相交或有效租约保护（§81：可独立规格化/接口已固定/验证可分离/无顺序依赖）；并行不是新路由（内部 max_workers 状态）；默认仍串行
- **技能契约**：orchestration §10 派发段更新（租约闸、并行资格、获取/释放时点、experimental 标记）
- **测试**：635 用例（新增 33：lease 19 / dispatcher 11 / state 上界 3——含三轮端到端并发冒烟：不相交双派发 → 租约挡重叠 → 释放后重派，active 全程对账）

## 2.0.0-beta2

v2 第五里程碑——**任务与工作单元管理**（P1.5，升级指南 §60-§73，实施计划轮 7-8 块 B8）：

- **工作单元状态模型（`runtime/work_unit.py`）**：十词状态 + 26 条合法转换边（主链 / quota·block 回退 / §69 恢复对账边 / 终态封锁）；§61 契约校验（ownership 与 verification 必填——无文件范围或无验证的单元不可派发）；`attempt` 重试记账不抹除失败史（§73）；state.json 的 work_units 逐项校验接入
- **依赖图（`runtime/dependency.py`）**：DAG 校验（重复 id / 未知引用 / 自依赖 / 环——报全部环成员）；就绪推导（依赖全部 completed 才 ready）；传递下游闭包；确定性拓扑序（同层 id 排序）
- **派发准入（`runtime/dispatcher.py`，主会话仍是唯一编排者）**：`plan_dispatch` 四道闸——quota 四态（EXHAUSTED→waiting_quota、UNKNOWN/PRESSURE 保守抑制，§67）→ ownership 不相交（保守近似：字面前缀相交即冲突，宁少并行不越界，§66）→ max_workers 预算 → 派发；**默认串行（max_workers=1）**，有界并行待租约层
- **恢复对账（`runtime/reconcile.py`，§68-§69）**：中断恢复绝不盲目重放——completed 不重跑；`running` 单元按证据三分（无残留→干净重派 / 残留+绑定当前指纹的新鲜验证事件→completed / 残留无新鲜证据→verifying 主会话亲验）；`verifying` 单元仅新鲜证据时建议 completed，其余出 advisory（保持现状 / 主会话裁决）不做转换；纯建议零落盘
- **技能契约（orchestration 新 §10）**：分解原则（单元必须仍能接收完整五段式规格）、派发契约（plan_dispatch 准入 + dispatch.active 记账）、单元验证仍为 parent-observed、显式 Join（任务级全局验证永不自动替代）、中断恢复对账；continuity 恢复读取顺序接入
- **测试**：602 用例（新增 174：work_unit 57 / dependency 52 / dispatcher 44 / reconcile 21——含串行多单元、依赖图派发、中断恢复三场景端到端与转换表逐边锚定；终审修复批次 +6）

## 2.0.0-beta1

v2 第四里程碑——**路由上下文契约 + 权限策略层**（依据升级指南 §48-§53 ROUTING PREFLIGHT / TASK CONTEXT PACK、§57-§59 Route-aware Permission Policy，实施计划轮 6 块 B6/B7；另清偿轮 5 终审全部 P2 遗留）：

- **ROUTING PREFLIGHT（orchestration 技能新 §3）**：双轴判断前的证据准备——根因/边界/ownership/接口/验证/耦合任一未知即触发只读侦察（优先 ZCode 内置 Explore，不新增自定义代理）；报告模板逐字锁定（ROOT CAUSE / RELEVANT FILES / INTERFACES / TEST ENTRYPOINTS / HIDDEN COUPLING / OPEN AMBIGUITY），条目全部证据化；任务已显式时跳过
- **TASK CONTEXT PACK（role-contracts.md 五段式规格前置节）**：主会话压缩的有界上下文包（TASK / ROOT CAUSE / RELEVANT FILES / KEY SYMBOLS / CALL·DATA FLOW / INTERFACES / OWNERSHIP / TEST ENTRYPOINTS / KNOWN RISKS / EXCLUDED AREAS 十节模板）——「GLM-5.3 压缩、Flash 执行」异构分工的落地形态；条目优先复用 PREFLIGHT REPORT；15-40 行有界、禁整库倾倒/大文件复制/推测性事实；简单任务可省略
- **Bash 策略门控（`runtime/policy.py` + PreToolUse(Bash) 接线）**：表驱动 allow/ask/deny（禁 DSL）——deny 恒拒：rm -r/-f、git reset --hard、git clean -f、force push；ask（仅活动任务 assurance:high）：任何 push、模式迁移（alembic/prisma/manage.py/knex）、发布操作（npm/cargo publish、push --tags、gh release）、权限变更（chmod/chown/icacls/attrib）；门控顺序：非 Bash 不管 → 无活动任务零干预 → deny 无视保障级 → ask 仅 high → 默认放行；决策经 `permissionDecision` 返回运行时（Phase 0 实证能力）；force-with-lease 归 ask 组（可控变体不硬拒）；`--force\b(?!-)` 正则同时满足 force 拒止与 lease 放行
- **策略边界**：只覆盖主会话 Bash 调用（子代理工具调用不触发钩子——角色级 deny 由 agent 工具白名单负责）；保守误拒两类已登记取舍：字符串中引用的破坏性文本（echo 'rm -rf'）与 `git rm -r --cached`（仅动索引不删工作区文件，被 rm 规则连坐）
- **轮 5 P2 清偿**：适配器 docstring 措辞自洽（"代码路径不出现 host 字符串"）；_http.py 不可达 https 检查注记 belt-and-suspenders；默认传输本地回环端到端测试（限长读取恰 max_bytes+1、302 真实拒绝跟随）
- **校验器**：检查 14 纳入 policy.py 与测试文件、pre_tool_use permissionDecision 标记、技能 ROUTING PREFLIGHT / TASK CONTEXT PACK 标记
- **测试**：428 用例（新增 34：policy 20、pre_tool_use 11、quota 适配器端到端 3）；B7.2 活体实测 8/8（deny/ask/allow/零干预/force-with-lease 归组/Agent 注入回归）

## 2.0.0-alpha3

v2 第三里程碑——**额度感知连续性**（Quota-Aware Continuity，依据升级指南 §23-§45，实施计划轮 5 块 B5；另清偿轮 4 终审全部 P2 遗留）：

- **quota 子系统（`runtime/quota/`）**：`provider.py` 抽象边界（QuotaProvider / 五类错误词汇 / host allowlist）+ `parser.py` 标准化解析（语义字段判窗 unit/number 而非 type、周窗可选——lite 套餐实测无周窗、容忍加性未知字段、epoch ms → ISO reset）+ `scheduler.py` 纯函数四态评估（AVAILABLE/PRESSURE/EXHAUSTED/UNKNOWN，fail-open）与恢复规划（EXHAUSTED → 全部阻塞窗 max(reset)+grace 精确唤醒且唤醒后强制刷新；reset 未知 → 周期性回退，绝不虚构 reset）
- **HTTP 适配器（zai / bigmodel）安全硬化（§37 全条款测试锚定）**：HTTPS only、严格 host allowlist（构造期拦截，绝不向清单外转发凭证）、短超时 5s、限长响应 64KiB、重定向禁用、原始响应不落盘、**凭证零落盘**（Authorization 头只存在于请求构造处，错误消息零 key 拼接；鉴权格式 `Authorization: <API Key>` 无 Bearer 前缀，与官方插件一致的实测格式）；实例内 TTL 缓存 + force 刷新
- **凭证链（§36 provider-api 模式）**：环境变量 `GLM_CONDUCTOR_QUOTA_API_KEY` 优先，已登录 ZCode 的 `~/.zcode/v2/config.json` provider 配置回退；两者皆无 → unavailable → 周期性存活探针（调度触发本身即探针的既有机制保持不变）
- **诊断命令 `/glm-conductor:quota`**：`report.py` 文本 + `--json` 双输出（三态：unavailable 含配置指引 / 每 provider 失败 kind / 窗口用量 + scheduler 评估与规划）；zai→bigmodel 有界探测；退出码恒 0；零凭证输出
- **措辞全面反转（B5.6）**：v1.1「无 quota API」绝对化声明（当时防虚构接口）在 README / continuity 技能 / long-horizon / architecture 九处反转为 provider-api 实态；不变边界保留（原生插件级接口仍不存在、不硬编码 5h、额度不作路由轴）
- **轮 4 终审 P2 清偿**：词汇外 review verdict 不再 default-allow（按 review_missing）；visual 证据缺 recorded 恒 stale；每次 Stop git 子调用 1+2N → 恒 2（touched/base 跨任务复用）；补 exhausted 后新周期重 block 单测；commit 改基线 → 证据 stale 的红线入契约
- **校验器**：7c 禁止「无 quota API」绝对化措辞复现；检查 14 纳入 quota 八模块、五测试文件、诊断命令文件与 scheduler/http/credentials/report + 技能标记；agent 引用正则不再误报斜杠命令
- **测试**：394 用例（新增 145：quota 解析 29 / 抽象 12 / 适配器 25 / 调度器 37 / 凭证 25 / 诊断 17；指纹 +7 / stop_gate +3 为 P2 回归）

## 2.0.0-alpha2

v2 第二里程碑——**证据完整性**：「任何修复使先前验证/审查失效」从提示词契约升级为完成门的自动强制（依据 docs/glm-conductor-v2-upgrade-guide-final.md §17-§22，实施计划轮 4 块 B3/B4）：

- **证据指纹层（`runtime/fingerprint.py`）**：`task_fingerprint` = sha256(基线修订 + 相关文件集归一化内容状态)——换行归一（CRLF/LF 逻辑内容不变则指纹不变）、路径归一、unborn 仓库基线回退；范围规则：声明了 ownership 取「当前改动 ∩ 声明范围」，未声明取全部改动；主会话记录证据与 Stop 完成门比对共用同一入口
- **完成门四重检查（`hooks/stop_gate.py`）**：Layer A 从单一 ownership 校验升级为 §15 顺序流水线——① ownership（touched ⊆ owned）② 验证（required 命令全部由主会话完成 + 指纹新鲜）③ 审查（required 时 verdict=ship + 指纹新鲜）④ 视觉证据（截图字节 sha256 一致）；七种失败形态（ownership / verification_missing / verification_stale / review_missing / review_rejected / review_stale / visual_stale）各有可行动 block 报文与 journal `check` 字段；参与判定：四项声明任一非空即受门跟踪
- **指纹状态写入（`runtime/state.py`）**：`record_verification` / `record_review` / `record_visual_evidence` 纯 dict 助手（去重追加、fingerprint=None 保留旧值、同 path 就地替换 sha256）；schema 增补 `review.fingerprint` 与 `visual_evidence` 数组校验
- **stale 自动检测**：记录证据后任何文件编辑 → 指纹不一致 → verification_stale / review_stale 拦截完成；恢复路径唯一：重跑验证 / 重新审查并记录新指纹（禁止旧指纹续命）
- **技能契约**：continuity 新增「证据指纹的记录时机」节（三类证据的记录时机表 + 同一入口 + 记录后不改 owned 文件红线）；orchestration 状态同步义务接入指纹落账；enforcement 技能重写为四重检查契约（检查表、参与判定、按 check 分类的恢复方法、evaluation_error 降级行）
- **校验器标记扩展（§97 对应项）**：指纹层文件与标记、stop_gate 四重检查标记、状态层写入助手标记、测试文件清单补全、技能 stale 语义标记
- **测试**：239 个单元/集成用例（新增 93：指纹层 52、状态层 +24、stop_gate 四重检查 13 + §98 集成冒烟场景 3-6 端到端流 4）；B4.2 Stop 循环安全活体实测 14/14（续行上限 2 block + 1 exhausted、exhausted 后新周期重新计数、链断重置、四重检查全路径单次 ~0.2s）
- 已知限制：完成门每次 Stop 执行 1 次 git status + 每个需指纹比对的参与任务 ≤2 次短 git 子调用（各 3s 超时上限，钩子总预算 5s）——正常仓库毫秒级，git 病态缓慢时按降级处理

## 2.0.0-alpha1

v2「从提示词契约到强制执行契约」——alpha1 强制基座达成（运行时状态层 + Ownership Layer A/B + 执行日志；依据 docs/glm-conductor-v2-upgrade-guide-final.md 与实施计划，Phase 0 运行时验证先行）：

- **任务标识更名**：`CONTINUITY_ID` → `TASK_ID`（v1.x 遗留 checkpoint 读取时归一化，无需重写）；路径占位统一为 `<task-id>`
- **运行时状态层（`runtime/state.py`）**：`state.json` 作为强制状态源——schema 校验（路由五字段 / ownership.files / verification / review / 13 态生命周期词汇）、原子保存、`CONTINUITY_ID` legacy 归一、活动任务发现；创建 state.json 即受完成门跟踪，foreground 普通短任务零干预
- **执行日志（`runtime/journal.py`）**：任务专属 `events.jsonl`，append-only（追加唯一写入口、时间戳由模块管理）、坏行容错读取（撕裂 UTF-8 尾部 / U+2028 行分隔符不丢事件）、尾部查询；无秘密值、无完整 prompt
- **Ownership Gate Layer A（完成门，`hooks/stop_gate.py` + `runtime/ownership.py`）**：Stop 时对声明了 ownership 的活动任务校验「git 改动文件 ⊆ 声明范围」，越界即 block（报文列出精确 out-of-scope 路径与两条出路）；声明形式支持精确文件 / 目录前缀（段级匹配）/ glob（`**` 跨段）；`.glm-conductor/` 运行时目录豁免（防自指拦截）；续行有界——连续两次 block 后放行并报 `ENFORCEMENT GATE EXHAUSTED`（模型义务：向用户报告 blocked，不得声称完成）
- **Ownership Gate Layer B（派发注入，`hooks/pre_tool_use.py`）**：PreToolUse（Agent|Task）在每次子代理派发前注入 ownership 契约提醒（提示级，确定性强制在 Layer A）
- **fail-open 降级可见**：钩子崩溃 / git 不可用时放行 + stderr 报 `ENFORCEMENT DEGRADED` + journal 记 `gate_degraded`——强制层故障不卡死会话，降级绝不静默
- **技能契约增补**：continuity（state.json / events.jsonl 创建时机、事件时点表、恢复读取顺序）、orchestration（route 落盘、ownership.files 同步义务）、新增 `enforcement` 技能（强制层用户侧解释：环境自检 / 报文含义 / 被拦截恢复方法）
- **静态校验器扩展至 14 项**：新增钩子清单完整性（事件合法 / python3 入口 / `${ZCODE_PLUGIN_ROOT}` 脚本存在 / Stop 必声明）与 runtime 状态层标记检查；CI 增加单元测试步骤
- **测试**：146 个单元/集成用例（状态层 63、日志 26、ownership 38、stop_gate 12、pre_tool_use 7，含真实 git fixture 五状态、子进程冒烟与多任务记账回归）
- Ownership 设计依据 Phase 0 修订：子代理工具调用不触发钩子（实测+代码级确证），故强制位于完成边界与派发时点（主会话上下文），写前拦截留待 ZCode 运行时演进（feature request 跟踪）

## 1.1.0

v1.1.0 — v1.0 发布后的审计整改与运行时加固（release hardening）：

- **视觉拓扑修正（P0）**：视觉反馈环改为跨多次调用的显式协议——实施者需要截图时返回 `VISUAL_CAPTURE_REQUEST` 并结束本次调用，主会话采集后恢复实施者读图判定，不再假设父子代理存在调用中握手机制（每验收点最多 3 轮）；子代理浏览器/桌面通道经实测确认被 ZCode 策略层禁止（Browser Use / Computer Use 为主会话专用），"主会话采集 + Flash 读图"是唯一受支持的视觉拓扑
- **Continuity 任务隔离（P0）**：checkpoint 从工作区全局单文件改为任务专属 `.glm-conductor/tasks/<continuity-id>/checkpoint.md`；引入稳定的 CONTINUITY_ID（并行长任务互不覆盖、互不删除，"最新 checkpoint"不再是查找策略）；结构化 resume prompt 携带精确 ID 与路径；完成清理只作用于本任务目录与其关联 automation；视觉证据目录同样按任务隔离
- **运行时契约加固（P1）**：同会话投递要求从当前聊天创建绑定会话的定时续作；调度触发本身即存活探针（不引入独立额度检查器）；README 新增"运行时限制"章节；`.glm-conductor/` Git 非侵入政策（优先 `.git/info/exclude`，不自动修改 tracked `.gitignore`）
- **术语一致（P1）**：残留的 v1「升级信号 / 上报升级」措辞统一替换为「重估信号 / ROUTE REASSESSMENT 请求」，闪现实施者 description 同步修正
- **视觉审查者收紧（P1）**：visual-reviewer 定位为以独立视觉验收为主职（代码 diff 仅作上下文，不作为更强的通用代码审查者）；输出格式 `GLM REVIEW` → `VISUAL REVIEW`（含 `RESIDUAL VISUAL RISK` 字段）；文本审查者 glm-reviewer 保持 `GLM REVIEW`
- **市场描述更新（P1）**：marketplace 插件条目描述补全视觉通道与长任务连续性
- **静态校验器与 CI（P2）**：新增 `scripts/validate_plugin.py`（纯标准库 8 项检查：JSON 合法性、agent/skill frontmatter 必填字段、引用 agent 存在、命名一致、v1 禁词、continuity 安全、视觉协议名一致）与 `.github/workflows/validate.yml`
- **发布质量（P2）**：README 声明 Tested with ZCode 3.9.2；「成本优化分工」重定位为「分层执行能力」，成本优势作为次级收益呈现
- **架构真相源（P0/P1）**：新增权威架构文档 `docs/architecture.md`（与 v1.1 运行时契约一致）；pre-v1.1 架构提案移入 `docs/history/` 并标注 SUPERSEDED，不再作为实现依据
- **CONTINUITY_ID 机械唯一（P1）**：ID 格式改为「语义前缀 + 6~8 位随机十六进制后缀」——并行同时创建的任务不再依赖命名约定防碰撞；生成、持久化、恢复、清理全程使用同一精确 ID
- **视觉新调用规范化（P1）**：视觉反馈环的续作以「携带完整状态的新调用」为规范路径（规格、当前 diff、上一轮 VISUAL_CAPTURE_REQUEST、截图路径、VISUAL_ROUND 轮次号）；子代理 resume 仅为可选优化，协议正确性不依赖 resume
- **校验器扩展（P2）**：权威架构文档纳入静态校验扫描（`docs/history/` 排除）；新增 CONTINUITY_ID 与视觉规范措辞必含检查、plugin.json 与 CHANGELOG 版本一致性检查

## 1.0.0

- 项目更名：glm-advisor → **glm-conductor**（系统角色已从"给主模型建议"演化为完整的编排层：route → assign → execute → verify → review → resume）
- Tagline：Selective orchestration for GLM coding agents in ZCode.
- 本地数据目录同步更名：`.glm-conductor/`（checkpoint 与视觉证据）

## 0.3.0

- 新增长任务连续性技能（continuity）：foreground / resumable / idle 三种模式，与路由正交
- CONTINUITY CHECKPOINT 结构化检查点；恢复时 repository 优先于 checkpoint
- 八步恢复流程 + 自包含结构化 resume prompt，映射 ZCode 定时任务与闲时任务
- 显式的额度 API 边界：不虚构 quota 接口、不硬编码 5 小时重置
- 完成后清理机制，防止幽灵唤醒

## 0.2.0

- 新增视觉通道：visual-implementer（GLM-5.3-Flash 多模态实施者）与 visual-reviewer（GLM-5.3-Flash 只读审查者）
- 视觉分工：主会话（纯文本）负责驱动采集截图，Flash 多模态角色负责视觉判定
- 五段式规格新增 VISUAL ACCEPTANCE 扩展；每个验收点最多 3 轮修正，截图不可得即 fail-closed
- 审查者按任务模态选择：文本任务 → glm-reviewer，视觉任务 → visual-reviewer

## 0.1.0

- 初始发布：双轴选择性路由（Delegability × Assurance → solo / delegate / audit / full）
- GLM-5.3 主会话任架构师；flash-implementer（GLM-5.3-Flash）执行有界实施
- 六字段 SELECTIVE ROUTE 声明；基于证据的双向 ROUTE REASSESSMENT
- 五段式实施规格 + IMPLEMENTATION REPORT；无证据的完成声明无效
- 受 DannyMac180/sol-advisor 启发
