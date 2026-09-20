# GLM Conductor 架构（权威文档，v2.4）

> 本文档是 GLM Conductor 的**唯一架构真相源**，描述插件**当前**（v2.4.0）的实际运行时行为——每一节回答"系统现在是什么"，不叙述开发历程；已删除的模块不写成"deprecated 运行路径"——它们不存在了（退役对照见 §21）。
> 一句话定位：**GLM Conductor owns semantic orchestration and acceptance; ZCode owns execution orchestration.**
> 契约细节以插件目录为准（`plugins/glm-conductor/` 下的 agents 与 skills）；本文档与其保持一致，冲突时以修复到一致为准。概念入门见 [core-concepts.md](core-concepts.md)，排障指引见 [troubleshooting.md](troubleshooting.md)。

## 1. 定位与职责分割

GLM Conductor 是 ZCode 插件，为 GLM 双模型体系（GLM-5.3 主会话 + GLM-5.3-Flash 子智能体）提供轻量语义编排层。v2.4 按一条重基线规则划界：

> **如果 ZCode 已拥有某个执行事实，Conductor 不得再对同一事实建模。Conductor 只持久化宿主不知道的语义。**

| Conductor 拥有 | ZCode 拥有 |
| --- | --- |
| 路由策略 | Workflow 执行生命周期 |
| 规范实施 DAG（静态节点） | 子 actor 生命周期 |
| ownership 语义 | 依赖执行机制 |
| 任务目标 / 约束 | 并行调度 |
| 任务级验收 | 重试 / 停止 / 恢复机制 |
| 高保障独立评审 | Workflow run 可观测性 |
| 终局变更新鲜度（change_id） | 后台执行 |
| 配额自动恢复授权（有界） | Scheduled Task 激活基底 |

v2.4 明确**不是**：第二套任务运行时、Workflow 引擎、权限引擎、溯源系统、调度平台或额度控制平面。主会话（GLM-5.3）始终是唯一编排者：需求歧义解决、路由判断、DAG 规格编写、验证与验收、评审派发；委派实施走 ZCode Native Workflow，主会话不重复实现任何执行机制。

## 2. 路由矩阵

路由保持两个独立轴，先分别回答、再查矩阵；`executor` 不再是普通文本工作的独立路由维度——`solo/audit` 即主会话实施，`delegate/full` 即 Native Workflow 实施（视觉例外见 §13）。

- **Delegability（可委派性）**：剩余实施是否足够有界、规格足够完备？objective / 文件边界 / 接口 / 约束 / 验证均明确且架构已定 → high；架构未定、root cause 未知、实质歧义、判断密集 → low
- **Assurance（保障等级）**：主会话验证之后，一次全新上下文的独立终审是否有实质价值？影响面有限、回归风险可控 → standard；宽影响面、高回归风险、破坏性行为、大规模用户可见变更 → high

| Delegability | Assurance | Route | 实施 | 独立评审 |
| --- | --- | --- | --- | --- |
| low | standard | `solo` | GLM-5.3 主会话 | 否 |
| high | standard | `delegate` | Native Workflow | 否 |
| low | high | `audit` | GLM-5.3 主会话 | 是 |
| high | high | `full` | Native Workflow | 是 |

主会话在编排动作之前输出一次 SELECTIVE ROUTE 声明（四字段 + 基于证据的理由）：

```
SELECTIVE ROUTE
mode: solo | delegate | audit | full
delegability: low | high
assurance: standard | high
reason: <简明的、基于证据的理由>
```

路由变化必须来自新观察到的证据，可双向重估（ROUTE REASSESSMENT）——实施者在 NodeResult.reassessment 返回的重估请求、评审者的 rethink 裁决均为有效重估证据；无新证据不得变更路由，不得为省事升 / 降档：

```
ROUTE REASSESSMENT
delegability: <old> -> <new>
assurance: <old> -> <new>
mode: <old> -> <new>
evidence: <新观察到的证据>
```

## 3. 静态 DAG

Work Unit 在 v2.4 退化为**静态 DAG 节点声明**（`runtime/work_unit.py` 纯数据层 + `runtime/dependency.py` 纯图校验层）：节点描述"要做什么"，不携带任何"执行到哪了"。单元运行时语义——status / attempt / 结果历史 / 单元验证 / 单元 receipts / 单元租约 / dispatch permits / 单元生命周期事件——全部不存在。

节点形状（必填：`id` / `objective` / `depends_on` / `ownership`；可选：`interfaces` / `constraints` / `local_check`）：

```yaml
id: parser
objective: implement parser behavior
depends_on: []
ownership:
  - src/parser/**
interfaces:
  - public parse() signature unchanged
constraints:
  - no schema migration
local_check:
  - pytest tests/parser -q   # 可选
```

- **依赖图**：`depends_on` 同 DAG 内引用；`graph_errors` 聚合报重复 id / 缺失依赖 / 自依赖 / 环（报全部环成员）；`topo_order` / `stage_levels` 给出确定性拓扑序与层级（同输入同输出，零运行时状态读取）
- **local_check 可选**：只是下游依赖不安全时的廉价节点健康闸，不是持久溯源；纯文档或无依赖节点可以没有。worker 本地检查只确立"本节点健康到足以让依赖方继续实施"，永不替代主会话任务级验收
- 委派执行的默认单位是**一个完整可委派 DAG 对应一次 Native Workflow run**（典型投影：stage 1: A‖B → stage 2: C‖D → stage 3: E），不是按"波"反复零散启动：

```
（DAG）A ─┬─→ C ─┐             （生成 Workflow 阶段投影）
        B ─┴─→ D ─┴─→ E        阶段 1: A、B（并行）
                                阶段 2: C、D（并行）
                                阶段 3: E
```

## 4. Workflow 编译器边界

主实施路径：规范 DAG → Workflow 编译器（`runtime/workflow/compiler.py`）→ 生成的 ZCode Native Workflow 源码 → 宿主原生执行（并行 / 后台 / 重试 / 恢复全部归宿主）。

编译器 `compile_workflow(nodes, task_context)` 的固定管线：

1. 校验：逐节点 `work_unit.validate_node` + 全图 `dependency.graph_errors` + `task_context`（至少 `task_ref` / `goal` / `repository` 三个非空字符串），错误聚合抛 `WorkflowCompileError`；
2. 阶段规划：`ownership.plan_stages`（§6）——ownership 歧义 / 非法模式以 `OwnershipConflictError` 原样上抛（冲突即拒，绝不猜测）；scope 明确重叠的候选被确定性串行排进后续组；
3. 逐组生成：每组一个 `phase(name)` 标记 + 一个 `await Promise.all`（组内并行，每节点恰一个 `agent("node-<id>", persona).ask<NodeResult>(指令)`）；组间是顺序的顶层 await；
4. 末尾 `return` 按节点 id 升序聚合全部节点结果。

生成源契约：确定性（无时间戳、无随机数，同一输入两次编译字节全等）；节点 id 原文出现在 actor 标签、ask 指令与结果映射中；persona 唯一权威来源是 `runtime/workflow/persona.py`（宿主 runtime 不消费插件 agent 定义，角色仿真必须把 persona 文本内嵌进脚本）；结果接口 `NodeResult` 恰六字段 `node_id / status("complete"|"partial"|"blocked") / changes / local_checks / reassessment / gaps`；生成源零 permit / lease 词汇（禁词扫描锚定）。

**编译器不做**（这些是宿主 Native Workflow 的职责，Conductor 不再造）：就绪队列、dispatch wave、并发调度器、后台管理器、重试管理器、子 actor 生命周期账本、ask 回放、Workflow resume 引擎。编译器只生成源码、只做静态断言——**绝不启动任何 Workflow**；执行是主会话经宿主 CreateWorkflow 发起的事（CLI 入口 `v24-compile`，只产码或落盘）。

生成源形态（节选示意，实际产物以编译器输出为准）：

```typescript
interface NodeResult { node_id: string; status: "complete" | "partial" | "blocked";
  changes: string[]; local_checks: string[]; reassessment: string; gaps: string[]; }
const persona: string = /* runtime/workflow/persona.py 的 TEXT_WORKER_PERSONA 原文 */;
const allResults: { [nodeId: string]: NodeResult } = {};

// —— 阶段 1: parser, types ——（plan_stages 判定 scope 不相交，可并行）
phase("阶段 1: parser, types");
await Promise.all([
  agent("node-parser", persona).ask<NodeResult>(/* build_ask 指令 */)
    .then((result: NodeResult): void => { allResults["parser"] = result; }),
  agent("node-types", persona).ask<NodeResult>(/* ... */)
    .then((result: NodeResult): void => { allResults["types"] = result; }),
]);
// ……后续阶段为顺序的顶层 await 语句……
return [allResults["parser"], allResults["types"] /* 按节点 id 升序 */];
```

## 5. 宿主拥有的执行状态

v2.4 有两个真相域：**静态语义真相**（Conductor 任务/DAG state）与**执行运行时真相**（ZCode Workflow run）。Conductor 不持久化任何 per-node 运行状态（running / completed 等）——那是宿主 `GetWorkflowRun` 的自有事实。

唯一的持久关联是 `runtime/workflow/adapter.py` 的 run-id 单据：`record_run(task_ref, workflow_run_id)` 把「任务 ↔ Workflow run」写为 `.glm-conductor/workflow-runs/` 下单一 JSON 记录（同任务重写整条替换——一任务一活跃 run；可选 `artifact_path` / `node_map`），`load_run` 读回。**零状态镜像红线**：子 actor running/完成状态、重试计数、token 账目、后台任务状态、宿主健康度一律不落盘。任务侧对应物是 `state.workflow_run_id`（委派启动前为 null），由 `runtime.task.record_workflow_run` 落盘 + 镜像 adapter + `workflow_started` 事件。

```json
// .glm-conductor/workflow-runs/run-<task_ref 百分号编码>.json
{ "task_ref": "redesign-settings-page-7f3a2c",
  "workflow_run_id": "<宿主 CreateWorkflow 返回的 run id>",
  "created_at": "2026-09-21T08:30:00.000Z" }
```

主会话的委派启动顺序固化为：`v24-compile` 产码 → `writer-acquire` 取仓库写预约 → 宿主 CreateWorkflow 启动 run → `record_workflow_run`（state.workflow_run_id + adapter 单据 + journal 事件）。run 的中途状态（进行中 / 卡在 ask / 已完成）一律经宿主 ListWorkflowRuns / GetWorkflowRun 观察，不进 Conductor 账本。

## 6. ownership 与编译期冲突处理

宿主对同一文件的并发写是 silent last-writer-wins（W0 实测），编译期检查是唯一冲突屏障；v2.4 用两级 ownership 纪律，无任何运行时锁。

**路径匹配语义**（`runtime/ownership.py`，完成守卫与编译期共用）：三种声明形式——精确文件（`src/auth.ts`）、目录前缀（裸路径 `src/auth` 等价 `src/auth/**`，按路径段匹配）、段级 glob（`**` 跨段、`*` / `?` 不跨段）。拒绝隐式扩张：`src/auth` 不覆盖 `src/authentication.ts`，`*.py` 不匹配 `sub/x.py`。

| 声明 | 命中 | 不命中 |
| --- | --- | --- |
| `src/auth.ts` | `src/auth.ts` | `src/auth.ts.bak` |
| `src/auth`（= `src/auth/**`） | `src/auth/a.ts`、`src/auth/x/y.ts` | `src/authentication.ts` |
| `*.py` | `a.py` | `sub/x.py`（`*` 不跨段） |
| `a/**/b.ts` | `a/b.ts`、`a/x/b.ts`、`a/x/y/b.ts` | — |

**编译期（Workflow 启动前）**：`plan_stages(nodes)` 确定性规划"组内 scope 两两不相交"的并行阶段——先静态校验，取 `dependency.stage_levels` 层级（跨层依赖保持权威），层内按 id 升序贪心装箱进第一个无重叠组，放不进则新开一组（即确定性串行）。两类失败以 `OwnershipConflictError` 编译期拒绝：模式非法；模式歧义（语义上匹配仓库根或一切路径，如 `**` / `*`——所有权声明覆盖一切即失去判定意义）。scope 明确重叠不是错误——被串行化，绝不猜测。无锁、无租约、无运行时预约。

```
（同层两个候选 scope 重叠 → 确定性串行，重叠判定是完备的路径语言相交判定）
节点 authz（ownership: src/auth/**）  ┐  scopes_overlap("src/auth/**",
节点 login（ownership: src/auth/login/**）┘   "src/auth/login/**") == True
→ 同层规划为两组：阶段 i: authz → 阶段 i+1: login（组序即执行序）
```

**任务级终局（验收前）**：`实际改动路径 ⊆ union(DAG ownership)`——由完成守卫查 2 执行（§12），是任务级检查而非 per-node 完成闸。

## 7. 一仓库一活跃写 Workflow

v2.4 有意选择粗粒度仓库级写不变量（取代 v2.3 的 per-unit 租约系统）：

> **一个 Git 仓库同一时刻至多一个活跃 Conductor 写 Workflow。**

实现是 `runtime/writer_guard.py`：仓库内单条持久预约记录 `.glm-conductor/writer_guard.json`（恰四字段 `repo_identity / task_id / workflow_run_id / created_at`），公共面仅 `acquire` / `release` / `inspect` 三函数。

- **永不自动过期**：全代码无 TTL / generation / heartbeat / 按时间过期的逻辑。`created_at` 仅为诊断信息，守卫判定绝不读取它——残留记录无论多旧一律冲突。恢复只有两条路：关联任务到达终态（`task.complete` / `fail` / `cancel` 自动释放）或操作者在 `writer-show` inspect 之后显式 `writer-release --force`
- **并发安全**：读-改-写全程走 `durable_io.atomic_update_json`（独占锁 + 原子替换），两个并发 acquire 必有一方看到对方落盘的持有者；同 task 重复 acquire 幂等成功；冲突零写入并报出持有者 `task_id` 与 `workflow_run_id`
- 释放权威键是 `task_id`（`workflow_run_id` 仅诊断）；非持有者释放被拒绝且绝不代删他人预约；Workflow 内部并行在编译期 ownership 验证之后是允许的——守卫约束的是**仓库之间**的第二条写 Workflow

## 8. 任务状态（七态）

任务状态文件是 `<账本根>/.glm-conductor/tasks/<task-id>/state.json`（`runtime/state.py`；同目录 `events.jsonl` 为 append-only journal）。`task_id` 是机械唯一标识：语义前缀 + 随机十六进制后缀（格式约束 `^[A-Za-z0-9][A-Za-z0-9-]*$`）。

durable status 恰为七态：`active / waiting_quota / waiting_user / blocked / completed / cancelled / failed`。终态 = 后三者，**终态不得静默重开**（`TASK_TRANSITIONS` 无终态表项；显式重开只能走 `reopen_task`，落 `task_reopened` 事件）。合法迁移：

```
active          → waiting_quota | waiting_user | blocked | completed | cancelled | failed
waiting_quota   → active | waiting_user | blocked | failed | cancelled
waiting_user    → active | blocked | failed | cancelled
blocked         → active | waiting_quota | waiting_user | failed | cancelled
```

顶层 schema（`validate_state` 强制）：`task_id / goal / repository{root 必填} / route{mode 必填, assurance 可选} / dag / status / workflow_run_id / validation / review / quota_resume`；`phase`（`planning / workflow / validating / reviewing`）是唯一可选键——纯描述性标注，无转换矩阵。`validation` / `review` 各只有一条任务级记录（§9 / §11），无逐单元证据。

```json
// state.json 示意（委派 high 保障任务，委派运行已启动）
{ "task_id": "redesign-settings-page-7f3a2c",
  "goal": "…",
  "repository": { "root": "C:\\work\\demo" },
  "route": { "mode": "full", "assurance": "high" },
  "dag": [ { "id": "parser", "objective": "…", "depends_on": [],
             "ownership": ["src/parser/**"], "interfaces": [],
             "constraints": [], "local_check": [] } ],
  "status": "active", "phase": "workflow",
  "workflow_run_id": "<run id>",
  "validation": { "status": null, "change_id": null,
                  "summary": null, "commands": null },
  "review": { "reviewer": null, "verdict": null,
              "change_id": null, "findings": null },
  "quota_resume": { "mode": "manual", "max_resumes": 0,
                    "resume_count": 0, "automation_id": null } }
```

任务发现完整性：`discover_tasks` 对 tasks_root 全部子目录四分类（active / terminal / corrupt / orphaned）——损坏的 state.json 不会被解释成"没有任务"，corrupt 与 orphaned 被显式报告（完成守卫 stderr 提示后跳过，不参与拦截）。v2.3 遗留 state 只识别不兼容：`is_legacy_state` / `detect_legacy_task` 检测 `work_units` / `permits` / `leases` / `receipts` 集合键与 v2.3 阶段态 status 等标记，命中即按 `LEGACY_STATE_GUIDANCE` 报告（§18）。

`repository.root` 是必填仓库根绑定（`bind_repository_root` 归一为绝对路径）：任务全部 git 求值（touched 清单、change_id）在生效根上做——绑定优先、账本根回退（`resolve_repository_root`）；账本恒在账本根。`.glm-conductor/` 是本地运行时账本：不算仓库改动（touched 求值与 change_id 均豁免该目录），优先写入 `.git/info/exclude` 本地排除。

## 9. change_id：唯一新鲜度原语

`runtime/change_id.py` 的 `compute_change_id(repo_root, relevant_paths)` 把「基线修订 + 相关路径集的归一化内容状态」确定性压缩为 `"sha256:" + 64 位十六进制`。语义：任何相关文件增 / 删 / 内容变化 / 相关文件集变化 / 基线变化都会改变标识；CRLF→LF 归一（同一逻辑内容不因换行风格改变标识）；`.glm-conductor/` 记账路径剔除（编排器写自己的账本不使自己过时）；归一去重排序（与输入顺序无关）；空相关集同样给出稳定标识。

**单一实现红线**：同一函数被三处共用（禁止旁路重算）——主会话记录验证（`validation.change_id`）、记录评审（`review.change_id`）、完成守卫新鲜度比对（§12）。相关路径集的派生同被两侧共用：`task.relevant_paths(task_state)` = dag 全节点 ownership scope 并集（去重排序）——记录侧与守卫侧同调此派生，标识才可比。任何任务相关仓库变化都使既有验证 / 评审记录过时：「任何修复使先前验证 / 评审失效」由此自动成立；唯一恢复路径是重新验证 / 重新评审并记录新值，禁止回写旧值"续命"。

## 10. 主会话验证

Workflow 结束后，GLM-5.3 主会话必须亲自完成主会话验证（main validation）：

- 检查累积 diff 的任务范围与接口保留；
- 跑**有限的、针对全任务的**集成 / 构建 / 测试命令——不机械重跑每个 worker 的 local_check（worker 本地检查只是节点健康闸，§3）；
- 判定用户目标是否达成，并经 `runtime.task.record_validation` 记录 `passed | failed`——记录内嵌现算 change_id（§9），可选 `summary` / `commands` 人读摘要。

验证记录是声明 + 新鲜度锚定，不要求 stdout 捕获、runner 身份或任何逐命令证据（v2.3 的 durable verification receipt 体系已不存在）。报告只是声明：无主会话亲自观察到的 diff 与命令输出，完成声明无效。`record_review` 要求先有完整 validation 记录（先验证后评审，§11）。

## 11. 评审者路径

`assurance=high`（路由 `audit` / `full`）在**主会话验证之后**引入一次全新上下文的独立评审（fresh-context read-only review）。评审者是原生 Custom Subagent——`glm-reviewer`（文本，`GLM REVIEW` 裁决）与 `visual-reviewer`（视觉，`VISUAL REVIEW` 裁决）——全新上下文 + 宿主强制的只读工具白名单（Read / Glob / Grep / LS / NotebookRead / WebFetch / WebSearch）构成独立性；评审者不实施任何修复。

- **输入五要素**：ROLE / STATED GOAL / ACCUMULATED CHANGE SET / INTERFACES AND CONSTRAINTS / VERIFICATION EVIDENCE——缺任何一项应要求补齐而不是猜测
- **裁决词汇**：`ship / fix-first / rethink`；fix-first / rethink 修复后主验证必须先刷新（diff 变了 change_id 必变），复审必须换**全新评审者**——旧裁决不因修复而复活
- **持久化最小化**：仅一条任务级 review 记录（`runtime.task.record_review`；CLI `review-record`）：`{reviewer, verdict, change_id, findings?}` + `review_recorded` 事件。v2.3 的评审调用真实性链、tool-use ID 证明、receipts 目录、runner 身份、journal/state 双镜像全部不存在
- **模型绑定**：评审者 agent frontmatter **无 `model` 字段**——继承宿主/会话模型（W0 发现：单模型宿主上不可解析的固定 model id 会让评审者确定性无法启动）；宿主可移植性由此成立

## 12. 完成守卫（四查）

完成是显式受守卫的任务级操作。`hooks/stop_gate.py` 挂在 Stop 事件上，对触发域内任务做**四查**（`evaluate_completion(repo_root, task_id)` 同一套判定亦可导入调用）；任一失败输出一行 block JSON（恰好一条最严重、可操作的中文理由），全部通过时守卫就地执行 `runtime.task.complete`（`completed` + 释放写者守卫 + `task_completed` 事件）并放行。

触发域：账本根 `discover_tasks` 的 active 桶中 `status == "active"` 的 v2.4 任务。停泊态（waiting_quota / waiting_user / blocked）是有意的停止点，一律放行；终态任务无可评估；v2.3 遗留任务放行并附一行处置指引（绝不假装能判完成）；corrupt / orphaned 目录不参与拦截，仅 stderr 一行提示；无活跃任务静默放行。v2.3 的 `finalizing` 完成请求态不存在——收尾发生在守卫全绿路径上，不存在"请求完成"的中间状态。

四查（按序，首败即返；求值根 = `resolve_repository_root` 生效仓库根）：

```
1. writer_guard ：仓库无其他任务的活跃写预约（持有者是自己放行——完成门即释放点）
                  否 → block：逐字报出持有者 task_id 与 workflow_run_id
2. ownership    ：git 实际改动路径 ⊆ dag 全节点 ownership 并集
                  否 → block：越界路径逐条列出 + 参与比对的节点 id
3. validation   ：validation.status == "passed" 且 validation.change_id == 当前 change_id
                  否 → block：无通过记录，或记录已过期（两侧 change_id 都报）
4. review       ：route.assurance == "high" 时 review.verdict == "ship"
                  且 review.change_id == 当前 change_id
                  否 → block：无 ship 裁决（fix-first / rethink 永不满足完成），或已过期
```

配套语义：求值期结构性错误（非 git 仓库的 `ChangeIdError`、非法 scope 模式的 `OwnershipError` 等）按任务隔离降级放行（stderr 报告——基础设施故障不是完成证据）；钩子自身崩溃 fail-open（绝不阻断会话）；本钩子不向 journal 写任何事件（唯一的写入来自 `task.complete`）。越界改动不被阻止发生（Workflow 子 actor 不触发钩子，§20），但不可能静默通过完成守卫。

block 决策的宿主契约是 stdout 单行 JSON（ZCode 据此请求模型续跑，报文即续跑指令）：

```json
{"decision": "block", "reason": "完成被阻断：验证记录已过期——验证后任务相关文件又有变化（任务 redesign-settings-page-7f3a2c）。\n- 记录 change_id：sha256:…\n- 当前 change_id：sha256:…\n请重新验证并经 runtime.task.record_validation 刷新记录，再请求完成。"}
```

journal 任务级事件词汇（恰十名，`TASK_JOURNAL_EVENTS` / `journal.RECOMMENDED_EVENTS` 对齐）：`route_selected`（create_task）/ `workflow_started`（record_workflow_run）/ `workflow_reassessed`（声明的词汇名，主会话记录路由重估）/ `validation_recorded` / `review_recorded` / `waiting_quota`（enter / exit）/ `quota_resume_confirmed`（恢复计数落账）/ `task_completed` / `task_failed` / `task_cancelled`。词汇外事件名不存在。

## 13. 视觉例外

`visual-implementer` 是**能力例外**，不走 Native Workflow 工人通道，继续走 Custom Subagent 通道（保留宿主强制的多模态模型绑定与读写工具面），直到 Native Workflow 被单独证明能保持多模态 / 截图反馈契约。此例外不得成为第二条通用文本实施路径——普通文本 `delegate/full` 只有 Native Workflow 一种实施基底（v2.3 的 flash-implementer 文本执行者已删除，§21）。

视觉反馈环跨多次调用完成（Browser Use / Computer Use 为主会话专用，子代理无法驱动浏览器 / 桌面）：

```
visual-implementer 调用（实施代码改动）
        ↓ 需要视觉证据 → 返回 VISUAL_CAPTURE_REQUEST，本次调用结束
主会话采集截图落盘
        ↓
新的 visual-implementer 调用（携带完整状态：规格、diff、上轮请求、截图路径、VISUAL_ROUND）
        ↓ 实施者亲自 Read 截图、对照 VISUAL ACCEPTANCE 判定
通过 → FUNCTIONAL VERIFICATION → 报告；不符合 → 修正后再发 VISUAL_CAPTURE_REQUEST
```

规范路径是"新的调用"：每次视觉轮次自包含，正确性不依赖子代理上下文保留（resume 仅为可选优化）。每个验收点以 `VISUAL_ROUND`（1|2|3）跟踪，最多 3 轮采集-判定循环，超出即 blocked 交回主会话重估；实施者不等待、不空转、不虚构截图结果；截图不可得时 fail-closed（视觉通道停止，不得改为纯文本验证交付）。`assurance=high` 的视觉任务由全新上下文的 `visual-reviewer` 做 `VISUAL REVIEW` 视觉终审（§11）。

## 14. 最小配额恢复

额度连续性收敛为 Workflow 本身不提供的最小任务级能力：**有界、须授权的配额恢复（quota_resume）**。无 epoch、无订阅、无激活记账、无边界消费证明、无常驻观测进程——v2.3 的这些概念在 Conductor 中不存在。

state 顶层 `quota_resume` 块（冻结初始形状 `{mode: "manual", max_resumes: 0, resume_count: 0, automation_id: null}`）：

- **mode**：`manual`（默认）/ `auto`。auto 不需要附加字段——授权由显式 `authorize_quota_resume(repo_root, task_id, max_resumes)` 调用落盘记录（要求任务处于 waiting_quota 或 active；`max_resumes >= resume_count`），绝不推断。`max_resumes=1` 即一次性恢复；`max_resumes=N` 提供有界多窗续跑
- **预算不变量**：`resume_count <= max_resumes`（`validate_state` 保存时强制）

等待与恢复路径（`runtime/task.py` 生命周期 API）：

```
额度耗尽 → enter_waiting_quota（status=waiting_quota；workflow_run_id 保留；
           可选把最近一次归一化观测 status/reset_at/observed_at 记入 last_observation 供诊断）
        → 原生 ZCode Scheduled Task 提供未来执行机会（宿主动作）
定时轮次 → scheduled_activation_decision(task_id, quota_view) 幂等决策，判定顺序固定：
  1. 非 waiting_quota → no-op（重复唤醒安全，零改写）
  2. 观测 EXHAUSTED / UNKNOWN → remain-waiting（绝不虚构可用性）
  3. mode 非 auto → waiting-user（等用户显式授权）
  4. 预算耗尽 → waiting-user，且恰一次转 waiting_user（幂等：再唤醒即 no-op）
  5. 可用（AVAILABLE/PRESSURE）+ 已授权 + 预算有余 → resume-authorized
     （附 workflow_run_id；resume_count_after 仅暂存在返回值里——绝不写盘计数）
        → 宿主 resume 调用真正被接受后 → confirm_resume_started：
           resume_count +1（唯一落账点）+ 任务转回 active（phase=workflow）
           + quota_resume_confirmed 事件——未确认的决策绝不消耗预算
预算耗尽后任务停在 waiting_user 等用户；不再有自动动作
```

观测词汇（`QUOTA_VIEW_STATUSES`）恰为 `AVAILABLE / PRESSURE / EXHAUSTED / UNKNOWN` 四态；UNKNOWN 绝不当作可用。配额恢复不改变路由（额度决定"何时"，不决定"谁做"）。

典型时间线（auto 授权 + `max_resumes=1`）：

```
额度耗尽 → waiting_quota（last_observation 记入）
定时唤醒 #1 → 观测 EXHAUSTED → remain-waiting（零改写）
定时唤醒 #2 → 观测 AVAILABLE、mode=manual → waiting-user（未授权）
用户 authorize_quota_resume(max_resumes=1) → mode=auto
定时唤醒 #3 → resume-authorized（暂存 resume_count_after=1）
宿主 resume 被接受 → confirm_resume_started → resume_count=1、active
定时唤醒 #4 → 观测 EXHAUSTED → 预算 1/1 耗尽 → waiting-user（恰一次转态）
```

## 15. Global Quota Clock 拆离

Global Quota Clock 是**独立的未来伴随项目**，不在 Conductor 责任边界内（v2.4 Phase 3 已整体拆出；可复用件清单见 `docs/roadmap/GLOBAL_QUOTA_CLOCK_EXTRACTION_INVENTORY.md`）。其 provider/账号级目标——按真实 reset 信息观察五小时额度窗口并在窗口边界后做低成本物化——属于 Clock 项目自己。

硬分离（双向）：

- Clock 项目可拥有：provider 身份、快照解析、reset 观察、时钟状态、周期低成本物化、自己的调度集成；
- Clock 必须不知道：Conductor task_id、Work Unit、路由模式、实施 DAG、ownership、评审策略、任务级恢复记账；
- **Conductor v2.4 的正确性绝不依赖 Global Quota Clock**：等待额度的任务凭自己的原生 Scheduled Task 路径保持可恢复——没装 Clock 一切照常工作。

Conductor 生产图中不存在 Clock 代码（无 clock / clock_store / clock automation 面，无宿主调度 SQLite 写路径）；Conductor 只保留只读的额度**观测**面（§17），它回答"现在能不能恢复"，不维护任何时钟。

## 16. 运行时模块布局与 CLI 面

```
plugins/glm-conductor/
├── agents/        # visual-implementer / glm-reviewer / visual-reviewer（无 flash-implementer）
├── commands/      # quota.md（/glm-conductor:quota 驱动 report.py 只读诊断）
├── hooks/         # hooks.json：SessionStart（恢复注入）+ Stop（完成守卫）——仅此两个钩子
├── skills/        # orchestration / enforcement / continuity 三技能
└── runtime/       # 纯标准库 Python（python3，零第三方依赖，3.7 兼容）
    ├── state.py        # 七态任务状态层（schema / 转换表 / 发现四分类 / 遗留检测 / 仓库根绑定）
    ├── change_id.py    # 任务级变更标识（§9 唯一新鲜度原语）
    ├── task.py         # 任务生命周期 API（创建/记录/等待恢复/终态收尾）
    ├── ownership.py    # 路径匹配 + git_touched_files + plan_stages 编译期规划
    ├── work_unit.py    # 静态节点（构造 + 校验，零运行时字段）
    ├── dependency.py   # 纯 DAG 校验与确定性排序
    ├── workflow/       # persona + compiler + adapter（§4 / §5）
    ├── writer_guard.py # 仓库写者守卫（§7）
    ├── journal.py      # events.jsonl append-only（任务级十事件词汇）
    ├── durable_io.py   # 共享原子写 / 独占锁原语（不 import runtime 包内任何模块）
    ├── recovery.py     # SessionStart 恢复摘要（纯本地，§18）
    ├── cli.py          # 运行时 CLI 唯一入口（下表）
    └── quota/          # 11 个只读观测模块（§17）
```

runtime CLI 子命令（退出码 0 成功 / 2 校验拒绝 / 1 运行期拒绝；技能层 runtime 调用一律走本 CLI）：

| 子命令 | 作用 |
| --- | --- |
| `v24-compile <dag-json> [--task-ref] [--out]` | 静态 DAG → TS Workflow 源（只产码，绝不执行） |
| `writer-acquire / writer-release [--force] / writer-show` | 仓库写者守卫操作面（§7） |
| `v24-record-run <task_ref> <run-id>` | 任务 ↔ Workflow run 关联单据（§5） |
| `review-record <repo> <task> <reviewer> <verdict> [note]` | 任务级评审记录（先验证后评审） |
| `quota-resolve <repo> [--force-refresh]` | 额度四态解析（§17） |

额度诊断另有独立脚本 `runtime/quota/report.py`（`/glm-conductor:quota` 命令驱动，文本 + `--json` 双输出）。v2.3 的 permits / wave / verify-unit / verify-task / policy-* / wake-* / quota-clock-* / host-check 等子命令连同其后端已全部删除。

## 17. 额度观测面（只读）

`runtime/quota/` 恰 11 个观测模块，只回答三件事：当前 provider 额度状态、耗尽任务的相关 reset 时间、此刻尝试恢复是否合理。

- **provider 抽象**（`provider.py` / `parser.py`）：监控端点响应解析为标准化快照——按语义字段判窗（unit=3/number=5 → five_hour；unit=6/number=1 → weekly）；**周窗可选**（lite 套餐实测无周窗，解析层绝不假设双窗必然存在）；`evaluate` 四态评估（AVAILABLE / PRESSURE / EXHAUSTED / UNKNOWN，fail-open）与 `plan_resume` 恢复建议（continue / checkpoint / resume_at / periodic_fallback）
- **provider 适配与硬化**（`zai.py` / `bigmodel.py` / `_http.py`）：HTTPS only、严格 host allowlist（api.z.ai / open.bigmodel.cn）、短超时（5s）、限长响应（64KiB）、重定向禁用、原始响应不落盘
- **凭证链**（`credentials.py`）：环境变量 `GLM_CONDUCTOR_QUOTA_API_KEY` 优先，已登录 ZCode 的 `~/.zcode/v2/config.json` provider 配置为文档化回退；两者皆不可得 → unavailable；凭证只进内存、零落盘（错误消息不含 key，异常只透出分类词）
- **运行时解析**（`resolver.py`）：`resolve_quota_status()` 四级层级——新鲜缓存（≤300s，不发网络）→ provider 抓取（四态评估 + 原子写缓存）→ 陈旧缓存回退 → UNKNOWN（绝不默认 AVAILABLE）；缓存绑定 provider 身份指纹（16 位非秘密 sha256 前缀，异身份缓存视同无缓存）；绝不重试网络
- **诊断面**（`report.py`）：`python3 report.py [--json]`——zai → bigmodel 有界探测（任一成功即用），三种诊断态（正常报告 / 探测失败每 provider 一行错误分类 / unavailable 配置指引），退出码恒 0（诊断面不是闸门），stdout 零凭证输出
- **窗口数学**（`time_utils.py` / `window_math.py`）：reset 时刻计算与窗口边界的规范落点（自 v2.3 实测口径逐字移入）

额度观察只作为恢复决策的证据使用，不作为路由轴。原生插件级 quota API（getQuotaRemaining / getQuotaResetTime / onQuotaReset 等）不存在，绝不虚构；不硬编码 5 小时重置。

## 18. 恢复与遗留任务

恢复入口收敛为两个，全部纯本地：

| 入口 | 机制 | 语义 |
| --- | --- | --- |
| SessionStart 注入 | `hooks/session_start.py` + `runtime/recovery.py` | 新会话 / compact / clear 时对未完成任务注入 `GLM CONDUCTOR RESUME CONTEXT`（每任务仅 task id / goal / repository / status / route / workflow run id / quota 等待态 / 下一步安全动作；无任务时完全安静） |
| 原生 Scheduled Task 唤醒 | 宿主 automation → 定时轮次（§14 决策链） | 等待额度任务的未来执行机会；重复唤醒是安全 no-op |

下一安全动作词汇（`NEXT_ACTIONS`，恰七个）：inspect workflow / resume workflow / reassess repository / run main validation / run required review / wait for quota or user / explicitly retire legacy task。仓库 > 任何运行时记录：恢复以仓库真实状态为准。注入形态（无任务时 stdout 恒空、完全安静）：

```
GLM CONDUCTOR RESUME CONTEXT
[1] redesign-settings-page-7f3a2c
    Goal:    …
    Repo:    C:\work\demo
    Status:  waiting_quota   Quota wait: EXHAUSTED @ 2026-09-21T15:00:00Z
    Route:   full (assurance: high)   Run: <workflow_run_id>
    Next:    wait for quota or user
```

**v2.3 遗留任务**：v2.4 只检测、不迁移、不兼容运行。`detect_legacy_task` 给出 `legacy` 判定、标记清单与一行处置指引 `LEGACY_STATE_GUIDANCE`（"请在 2.3.x 下收尾或显式放弃"）；`load_task` / 生命周期 API / 完成守卫对遗留任务一律拒绝或放行并指引，绝不静默当作 v2.4 任务、绝不伪造 v2.4 完成证据。v2.3.x 可经 Git 历史 / tag 随时找回。

## 19. 安全模型

- **额度网络面**：HTTPS only + host allowlist + 短超时 + 限长响应 + 禁重定向（§17）；凭证零落盘、异常只透出分类词；provider 身份指纹是 sha256 前缀截断，非秘密、不可逆
- **授权方向绝不 fail-open**：quota_resume 的 auto 授权必须显式落盘；计数只在宿主接受 resume 后落账；预算不变量保存时强制——误拒只损失一次恢复时机，误放行是无授权的执行
- **状态面最小暴露**：state.json / events.jsonl 无秘密值、无完整 prompt；events.jsonl append-only 且按任务隔离；`.glm-conductor/` 优先写入 `.git/info/exclude` 本地排除，不自动修改 tracked `.gitignore`
- **生成源安全**：Workflow 编译产物不含时间戳 / 随机数 / 凭证；禁词扫描锚定 permit / lease 词汇零出现
- **静态校验作为契约闸**：`scripts/validate_plugin.py` 15 项检查机械防止契约回潮（§22）

## 20. 运行时边界（ZCode 宿主事实）

以下为 W0 宿主复验（2026-09-20 收口）与既有实测确立的宿主事实，v2.4 设计以此为约束：

- **Workflow 子 actor 不触发插件钩子**（结构性旁路，W0-A 在当前构建上复确认）：派发面钩子强制在主实施路径上不可实现——v2.4 因此把强制收敛到编译期（ownership 规划 + 写者守卫）与终局（完成守卫四查），并删除了只覆盖主会话 Bash 的策略钩子（留着会暗示主路径并不享有的覆盖面）。越界改动不被阻止发生，但不可能静默通过完成守卫
- **定时轮次是所属会话的 mid-turn continuation**（W0 发现）：唤醒轮共享主会话环境与权限状态，不是新会话 / 新进程；应用关闭时的触发行为未验证——连续性设计不依赖它，恢复兜底归 SessionStart 注入
- 一会话一 automation 是宿主硬规则（已有 scheduled task 的会话再创建被宿主直接拒绝）；fire 实际执行相对排定时刻有秒级延迟
- 强制层钩子依赖 `python3` 在 PATH；钩子随插件分发、仅安装 / 更新后的新会话生效；agent 定义在会话启动时快照，运行中会话不热加载
- 子智能体每次调用都是全新上下文、不能再派生子智能体（结构扁平）；只能看到会话启动时已连接的 MCP 服务；以前台调用受支持为前提
- 插件与代理严禁替用户执行睡眠 / 唤醒 / 关机等 OS 电源操作（电源操作是用户本人权限）

## 21. v2.3 → v2.4 退役对照

下表是显式退役记录：这些名字在 v2.4 生产代码与文档中**不是运行路径，而是不存在**。历史证据保留在 CHANGELOG 与 docs/reviews / docs/roadmap（其中 v2.4 之前的架构描述只对 2.3.x 成立）。

| v2.3 概念 | v2.4 处置 | 取代物 |
| --- | --- | --- |
| dispatcher / dispatch wave / dispatch permit | 删除（模块 + CLI + 钩子面） | Native Workflow 一次 run 执行整个 DAG |
| per-unit lease（TTL / generation / heartbeat） | 删除 | 编译期 ownership 规划 + 仓库写者守卫 |
| Work Unit 运行时状态（status / attempt / 结果史） | 删除 | 静态节点：只声明编译期事实 |
| 单元 / 任务级 durable receipt（verify-unit / verify-task / runner 身份链） | 删除 | 任务级 validation / review 记录，change_id 锚定新鲜度 |
| agent-run ledger / reconcile 对账 / resume manifest | 删除 | 宿主 GetWorkflowRun 观测 + 任务级最小恢复摘要 |
| Bash 策略门（PreToolUse Bash） | 删除 | ZCode 原生权限设施 + worker constraints |
| finalizing 完成请求态 / 完成门记账机 | 删除 | 完成守卫全绿路径直接收尾（四查） |
| 执行相位 NORMAL / PRESSURE / DRAINING / BLOCKED | 删除 | 额度观测四态只作恢复证据 |
| quota watcher / quota epoch / 订阅 / 激活记账 / 窗口消费证明 | 删除 | `quota_resume` 有界预算（resume_count / max_resumes） |
| Window Primer / Activation Transport / Task Wake Bridge | 删除 | 原生 Scheduled Task 唤醒（宿主能力） |
| Global Quota Clock（clock / clock_store / clock automation） | 拆离为独立伴随项目 | Conductor 仅保留只读额度观测（§17） |
| 宿主调度 SQLite 适配器（host/ 直写 next_run_at） | 删除 | 只用宿主支持的 Scheduled Task 能力 |
| flash-implementer（文本执行者 agent） | 删除 | Native Workflow 文本工人（persona 模板） |
| continuity 生命周期轴（foreground / resumable / idle） | 删除 | 七态任务 + quota_resume 授权 + 原生唤醒 |
| Layer A / Layer B 派发注入与 permit 门钩子 | 删除 | 钩子只剩 SessionStart + Stop 两个 |

## 22. 静态校验与测试

- `scripts/validate_plugin.py`（纯标准库，15 项检查）+ CI（静态校验 + 单元测试）维护契约一致性：扫描 `plugins/`、`README.md`、`marketplace.json` 与本文档；一律跳过 `__pycache__` 与 `*.pyc` / `*.pyo`
- 测试（`tests/`，纯标准库 unittest）覆盖：静态节点与 DAG 校验、ownership 阶段规划与重叠判定、Workflow 编译器（确定性 / 禁词）、写者守卫（含并发）、七态状态层与转换门、change_id、任务生命周期、quota_resume 授权与恢复决策、完成守卫四查、恢复摘要、额度观测面；已删组件的测试随组件删除，不以 skip 形态留存
- 版本策略：`plugin.json` 版本、CHANGELOG 最新条目保持一致（v2.4.0）

## 23. 稳定边界与演化纪律

冻结边界（变更须经显式设计裁决并同步全部契约面）：

- 路由双轴 + 四路线（`solo / delegate / audit / full`）与三 agent 清单（visual-implementer / glm-reviewer / visual-reviewer）
- TASK_ID 格式（语义前缀 + 随机十六进制后缀）与 `.glm-conductor/tasks/<task-id>/`（state.json + events.jsonl）状态布局
- 七态词汇与 `TASK_TRANSITIONS`；完成守卫四查顺序与 block 报文契约
- change_id 推导口径与"记录 / 守卫两侧同调同一实现"红线
- writer_guard 记录四字段与"永不自动过期"语义；quota_resume 四键形状与预算不变量
- runtime CLI 公共子命令面与退出码语义；journal 任务级十事件词汇

演化纪律：除非实际使用暴露出具体能力缺口，不新增路由维度或角色；运行时只针对高置信不变量（ownership 越界、证据过期、第二写者、无授权恢复）做确定性强制，不做语义解释型拦截；宿主已拥有的执行事实永远不再建模。
