# GLM Conductor 核心概念（v2.4）

> **GLM Conductor 是面向 ZCode GLM Coding Agent 的轻量语义编排层：GLM Conductor owns semantic orchestration and acceptance; ZCode owns execution orchestration.**
>
> 本文是概念词典：逐条定义 v2.4 的核心概念——是什么、长什么样、权威落点在哪。每个词条都可在 [architecture.md](architecture.md)（唯一架构真相源）与 `plugins/glm-conductor/runtime/` 代码中核对；运行面操作契约见插件技能文档。不叙述演化过程，不收录已删除的 v2.3 概念（退役对照见 architecture.md §21）。

两个真相域是全部概念的坐标系：

- **静态语义真相**（Conductor 持有）：任务 / DAG / ownership / change_id / 授权——本词典的七条主词条；
- **执行运行时真相**（ZCode 持有）：Workflow run 的生命周期、并行、重试、恢复——Conductor 绝不镜像，只保存 run id 关联。

---

## 1. 任务（Task）

**是什么**：一次有目标、有验收的编排单元，是 Conductor 唯一的有状态概念。

- **标识**：`task_id`（TASK_ID）机械唯一——语义前缀 + 随机十六进制后缀（如 `redesign-settings-page-7f3a2c`），格式约束 `^[A-Za-z0-9][A-Za-z0-9-]*$`；生成即唯一，恢复期间绝不重新生成
- **形态**：`<账本根>/.glm-conductor/tasks/<task-id>/` 下恰两个文件——`state.json`（状态真相源）+ `events.jsonl`（append-only 溯源）
- **状态（七态）**：`active / waiting_quota / waiting_user / blocked / completed / cancelled / failed`；后三者是终态，**终态不得静默重开**（显式重开只能走 `reopen_task`，落 journal 记录）；状态迁移按 `TASK_TRANSITIONS` 矩阵逐次校验，矩阵外迁移被拒
- **phase**（可选）：`planning / workflow / validating / reviewing`——纯描述性标注，无转换矩阵，不参与任何强制
- **route**：`mode`（四路线）必填 + `assurance`（standard / high）可选——assurance=high 使完成守卫追加独立评审义务
- **权威落点**：`runtime/state.py`（schema / 转换表 / 发现四分类 / 遗留检测）；`runtime/task.py`（生命周期 API）

**相邻**：任务发现按 active / terminal / corrupt / orphaned 四分类（损坏 state.json 不会被解释成"没有任务"）。v2.3 遗留任务只检测、不迁移——按 `LEGACY_STATE_GUIDANCE` 报告后由用户在 2.3.x 下收尾或显式放弃。

路由四路线（`route.mode` 词汇，Delegability × Assurance 查表）：

| Delegability | Assurance | mode | 实施 | 独立评审 |
| --- | --- | --- | --- | --- |
| low | standard | `solo` | GLM-5.3 主会话 | 否 |
| high | standard | `delegate` | Native Workflow | 否 |
| low | high | `audit` | GLM-5.3 主会话 | 是 |
| high | high | `full` | Native Workflow | 是 |

events.jsonl 示意（一行一事件，任务级词汇恰十名）：

```
{"event": "route_selected", "mode": "full", "assurance": "high"}
{"event": "workflow_started", "workflow_run_id": "<run id>"}
{"event": "validation_recorded", "status": "passed", "change_id": "sha256:…"}
```

## 2. DAG（规范实施 DAG）

**是什么**：委派任务的实施蓝图——把"剩余实施"分解为一张静态有向无环图，一次 Native Workflow run 执行整张图。

- 形态：state 顶层 `dag` 数组（节点列表，可为空）；依赖用 `depends_on` 表达，只引用同 DAG 内的节点
- 校验：重复 id / 缺失依赖 / 自依赖 / 环（报全部环成员）在保存与编译前聚合拒绝
- 排序：确定性拓扑序与层级（第 i 层 = 依赖全部落在更早层级的节点）；同一图任何输入顺序给出相同输出
- 执行单位：**一个可委派 DAG ↔ 一次 Workflow run**（典型投影：阶段 1: A‖B → 阶段 2: C‖D → 阶段 3: E）；图在运行中不可变——要改蓝图就重估路由、开新 run
- **权威落点**：`runtime/dependency.py`（纯图校验与排序，零 I/O）

示例：三个节点、两层的 DAG（`parser` 与 `types` 无相互依赖 → 同层可并行；`ui` 依赖两者 → 下一层）：

```yaml
dag:
  - { id: parser, depends_on: [],              ownership: ["src/parser/**"] }
  - { id: types,  depends_on: [],              ownership: ["src/types/**"] }
  - { id: ui,     depends_on: [parser, types], ownership: ["src/ui/**"] }
# 层级投影：阶段 1: parser, types → 阶段 2: ui
```

## 3. 节点（静态节点 / NodeResult）

**是什么**：DAG 的顶点——一份**静态节点声明**：描述"要做什么"，不携带任何"执行到哪了"。v2.3 时代 Work Unit 的运行时面（status / attempt / 重试史 / 单元验证 / receipts / 租约）在 v2.4 不存在。

- **必填四键**：`id`（DAG 内唯一）、`objective`（有界目标）、`depends_on`（依赖）、`ownership`（非空的仓库相对 scope 列表——无文件范围的节点不可编译）
- **可选三键**：`interfaces`（须保持兼容的接口面）、`constraints`（额外约束）、`local_check`（可选本地检查——廉价的节点健康闸：只确立"本节点健康到足以让依赖方继续"，永不替代主会话任务级验收；纯文档节点可以没有）
- **结果契约（NodeResult）**：worker 对每个节点返回恰六字段；`reassessment` 非空即路由重估请求，交回主会话裁决
- **权威落点**：`runtime/work_unit.py`（构造 + 校验，纯数据层零导入）；结果接口由 `runtime/workflow/compiler.py` 生成进 Workflow 源码

```json
// NodeResult：worker 返回的结构化实施结果（恰六字段）
{ "node_id": "parser",
  "status": "complete",          // "complete" | "partial" | "blocked"
  "changes": ["src/parser/lexer.ts"],
  "local_checks": ["pytest tests/parser -q — 12 passed"],
  "reassessment": "",            // 非空 = 路由重估请求
  "gaps": [] }
```

## 4. Ownership（路径所有权）

**是什么**：谁能改哪些文件——v2.4 并发安全的唯一机制（宿主对同一文件的并发写是 silent last-writer-wins，编译期检查是唯一冲突屏障；无运行时锁）。

- **scope 语法**：精确文件（`src/auth.ts`）；目录前缀（裸路径 `src/auth` 等价 `src/auth/**`）；段级 glob（`**` 跨段，`*` / `?` 不跨段）。拒绝隐式扩张：`src/auth` 不覆盖 `src/authentication.ts`
- **编译期（Workflow 启动前）**：`plan_stages` 把节点规划为"组内 scope 两两不相交"的并行阶段——层内贪心装箱；scope **明确重叠**的候选被**确定性串行**（排进同层级后续组），不是错误；模式非法或**歧义**（覆盖仓库根 / 一切路径，如 `**`）以 `OwnershipConflictError` **编译期拒绝**——冲突即拒，绝不猜测
- **任务级终局（验收前）**：`实际改动路径 ⊆ union(DAG ownership)`——由完成守卫查 2 强制；越界路径逐条点名
- **仓库级（粗粒度兜底）**：**一仓库至多一个活跃 Conductor 写 Workflow**——`writer_guard` 单条持久预约（`.glm-conductor/writer_guard.json` 四字段），永不自动过期（无 TTL / 心跳）；释放只有两条路：任务终态自动释放，或 `writer-show` inspect 后 `writer-release --force` 显式清除
- **权威落点**：`runtime/ownership.py`（匹配 / `git_touched_files` / `plan_stages`）；`runtime/writer_guard.py`

```json
// .glm-conductor/writer_guard.json：仓库级写预约（恰四字段）
{ "repo_identity": "C:\\work\\demo",
  "task_id": "redesign-settings-page-7f3a2c",
  "workflow_run_id": "<run id>",
  "created_at": "2026-09-21T08:30:00.000Z" }
```

## 5. change_id（任务级变更标识）

**是什么**：v2.4 唯一的新鲜度原语——「基线修订 + 相关路径集的归一化内容状态」的确定性摘要（`"sha256:" + 64 位十六进制`）。

- **语义**：任何相关文件增 / 删 / 内容变化 / 相关文件集变化 / 基线变化都会改变标识；CRLF→LF 归一；`.glm-conductor/` 记账路径剔除（编排器写自己的账本不使自己过时）；与输入顺序无关
- **相关路径集**：`task.relevant_changed_files` = dag 全节点 ownership scope 并集（`task.ownership_scopes`）∩ git 工作区实际改动（`ownership.git_touched_files` + `classify_paths`）——记录侧与守卫侧**同调同一派生、同一实现**，标识才可比（单一实现红线，禁止旁路重算）；owned 文件增 / 删 / 改 / 改名 / 文件集变化都改变标识，scope 外改动仍由 ownership 完成检查拦截、与新鲜度无关，`.glm-conductor/` 记账在两层各自剔除
- **三处共用**：验证记录（`validation.change_id`）、评审记录（`review.change_id`）、完成守卫新鲜度比对。"任何修复使先前验证 / 评审失效"由此自动成立；唯一恢复路径是重做并记录新值，禁止回写旧值"续命"
- **权威落点**：`runtime/change_id.py`（`compute_change_id`）

摘要输入的三行结构（域分隔，防跨域撞摘要）：

```
glm-conductor/change-id/1        ← 域分隔前缀
base <git rev-parse HEAD>        ← 基线修订（unborn 仓库为 "-"）
file <归一路径>\0<sha256:…|missing>   ← 每个相关路径一行（排序去重后）
```

## 6. 完成守卫（四查）

**是什么**：把"完成"变成显式受守卫的操作的 Stop 钩子（`hooks/stop_gate.py`）——同一套判定经 `evaluate_completion(repo_root, task_id)` 可导入调用。`finalizing` 完成请求态不存在：收尾发生在守卫全绿路径上。

- **触发域**：仅 `status == "active"` 的 v2.4 任务；停泊态（waiting_quota / waiting_user / blocked）是有意的停止点、一律放行；v2.3 遗留任务放行并附处置指引；corrupt / orphaned 仅 stderr 提示；无活跃任务静默放行
- **四查（按序，首败即返）**：
  1. **writer_guard**：仓库无其他任务的活跃写预约（持有者是自己放行——完成门即释放点）；
  2. **ownership**：git 实际改动 ⊆ dag ownership 并集；
  3. **validation**：有 `passed` 记录且 change_id 等于当前值（否则"缺失"或"过期"）；
  4. **review**：assurance=high 时须有 `ship` 裁决且 change_id 等于当前值（fix-first / rethink 永不满足完成）。
- **输出**：失败 → stdout 单行 block JSON（恰好一条最严重、可操作的中文理由）；全过 → 就地 `task.complete`（`completed` + 释放写者守卫 + `task_completed` 事件）并放行
- **失败语义**：求值期结构性错误（非 git 仓库等）按任务隔离降级放行（stderr 可见）；钩子自身崩溃 fail-open。越界改动不被阻止发生（Workflow 子 actor 不触发钩子——宿主事实），但不可能静默通过完成守卫
- **权威落点**：`hooks/stop_gate.py`

```json
// block 决策：stdout 单行 JSON，ZCode 据此请求模型续跑（reason 即续跑指令）
{"decision": "block", "reason": "完成被阻断：验证记录已过期——验证后任务相关文件又有变化（任务 …）。\n…\n请重新验证并经 runtime.task.record_validation 刷新记录，再请求完成。"}
```

## 7. quota_resume（有界配额恢复授权）

**是什么**：任务在 provider 额度耗尽后恢复执行的最小授权与记账块——有界、须授权、只数"真正开始的恢复"。无 epoch / 订阅 / 激活记账 / 边界消费证明（v2.3 概念已不存在）。

- **形状**：state 顶层恰四键 `{mode, max_resumes, resume_count, automation_id}`，初始 `{manual, 0, 0, null}`；预算不变量 `resume_count <= max_resumes` 保存时强制
- **mode**：`manual`（默认）/ `auto`——auto 只能由显式 `authorize_quota_resume(max_resumes)` 落盘授权，绝不推断；`max_resumes=1` 即一次性恢复，`N` 即有界多窗
- **等待**：额度耗尽 → `enter_waiting_quota`（七态中的 waiting_quota；`workflow_run_id` 保留；最近观测记入 `last_observation` 供诊断）
- **定时唤醒决策**（`scheduled_activation_decision`，幂等五步）：非 waiting_quota → `no-op`；观测 EXHAUSTED/UNKNOWN → `remain-waiting`（绝不虚构可用性）；未授权 → `waiting-user`；预算耗尽 → `waiting-user` 且恰一次转 `waiting_user`；可用 + 已授权 + 预算有余 → `resume-authorized`（计数仅暂存）
- **确认落账**：宿主 resume 调用**真正被接受后**才调 `confirm_resume_started`——`resume_count +1` + 转回 active + `quota_resume_confirmed` 事件；未确认的决策绝不消耗预算
- **激活基底**：原生 ZCode Scheduled Task（宿主能力）；重复唤醒是安全 no-op；没装任何外部时钟一切照常工作
- **权威落点**：`runtime/task.py`；额度观测面 `runtime/quota/`（只读：AVAILABLE / PRESSURE / EXHAUSTED / UNKNOWN 四态，lite 套餐无周窗是合法形态）

```
（auto 授权 + max_resumes=1 的决策时间线）
额度耗尽 → waiting_quota
唤醒 → 观测 EXHAUSTED          → remain-waiting
唤醒 → 观测 AVAILABLE、manual  → waiting-user（未授权）
用户 authorize_quota_resume(1) → mode=auto
唤醒 → 可用、auto、预算有余     → resume-authorized（计数暂存 1）
宿主 resume 被接受 → confirm_resume_started → resume_count=1、active
唤醒 → 观测 EXHAUSTED          → 预算 1/1 耗尽 → waiting-user
```

---

## 支撑词条（速查）

| 词条 | 一句话定义 | 落点 |
| --- | --- | --- |
| Native Workflow | ZCode 原生工作流：并行 / 后台 / 重试 / 恢复的执行基底 | 宿主 CreateWorkflow / GetWorkflowRun |
| Workflow 编译器 | 静态 DAG → TS Workflow 源的确定性翻译；只产码绝不执行 | `runtime/workflow/compiler.py`（CLI `v24-compile`） |
| persona | 文本工人唯一权威人设；宿主不读插件 agent 定义，须内嵌进生成源 | `runtime/workflow/persona.py` |
| run 关联单据 | 任务 ↔ Workflow run 的 id 关联；零状态镜像 | `runtime/workflow/adapter.py` |
| 评审者（glm-reviewer / visual-reviewer） | 全新上下文只读 Custom Subagent，assurance=high 在主验证后独立终审；模型绑定文本/视觉分治——glm-reviewer 无 model 字段（继承宿主/会话模型），visual-reviewer 显式绑定多模态 GLM-5.3-Flash（绑定不可用即 fail closed） | `plugins/glm-conductor/agents/` |
| 视觉例外 | visual-implementer 保持 Custom Subagent 通道（多模态截图反馈契约），非第二文本实施路径 | agents/visual-implementer.md |
| 主会话验证 | Workflow 结束后主会话亲自查 diff、跑有限集成命令、记录 passed/failed | `runtime/task.record_validation` |
| journal 词汇 | 任务级恰十事件：route_selected / workflow_started / workflow_reassessed / validation_recorded / review_recorded / waiting_quota / quota_resume_confirmed / task_completed / task_failed / task_cancelled | `runtime/journal.py` |
| 遗留任务检测 | 只读识别 v2.3 state（work_units / permits / leases / receipts / 阶段态），绝不迁移 | `runtime/state.py` |
| SessionStart 注入 | 新会话发现未完成任务即注入最小恢复摘要（纯本地，无任务时安静） | `hooks/session_start.py` + `runtime/recovery.py` |

## 稳定边界（诚实边界）

- 主会话是唯一编排者；不引入第二套任务运行时 / Workflow 引擎 / 权限引擎 / 溯源系统 / 调度平台 / 额度控制平面
- 额度只决定"何时恢复"，不决定"谁做"，不是路由轴；原生插件级 quota API 不存在，绝不虚构
- Global Quota Clock 是独立伴随项目：Conductor 的正确性不依赖它，等待额度任务凭原生 Scheduled Task 路径自恢复
- Workflow 子 actor 不触发钩子（宿主事实）：写前拦截不可实现——确定性强制落在编译期与完成守卫两端
- `.glm-conductor/` 是本地运行时账本，不算仓库改动；优先写入 `.git/info/exclude`

## 常见误读（与七词条直接相关）

- "delegate = 自动派发多个子代理跑单元"——不是：`delegate/full` 是一次 Native Workflow run 执行整个静态 DAG，派发调度归宿主，Conductor 不发单元级派发指令；"completed 可以直接写进 state.json"——不能：公共写入路径过不了终态转换门，完成的唯一提交点是完成守卫全绿路径；
- "验证记录手工填一个 change_id 即可通过"——不行：守卫用同一实现现算比对，伪造值必然失配（过期报文给出两侧值）；"auto 授权后预算随新窗口刷新"——不刷新：`resume_count` 只增不减，提高预算须再次显式 `authorize_quota_resume` 且不得低于已用计数。

## 延伸阅读

- [architecture.md](architecture.md)——唯一架构真相源：十四个主题（路由矩阵、静态 DAG、编译器边界、宿主执行状态、ownership 冲突、写者守卫、七态、change_id、主验证、评审者路径、完成守卫、视觉例外、最小配额恢复、Global Clock 拆离）的权威技术描述。
- 技能文档（运行面操作契约）：`plugins/glm-conductor/skills/orchestration/SKILL.md`、`skills/enforcement/SKILL.md`、`skills/continuity/SKILL.md`。
- 排障手册：[troubleshooting.md](troubleshooting.md)；文档索引：[docs/README.md](README.md)；项目入口：[仓库根 README](../README.md)。
