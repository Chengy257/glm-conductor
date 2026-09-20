---
name: orchestration
description: GLM 双轴选择性路由 + 原生 Workflow 编排。主会话任架构师，按 Delegability × Assurance 两个独立维度在首次任务委派前声明 SELECTIVE ROUTE（solo/delegate/audit/full）；delegate/full 经 v24-compile 把静态节点 DAG 确定性编译为宿主原生 Workflow 源码，由主会话 CreateWorkflow 提交执行（writer-acquire 取仓库写预约 → 记录 run 关联 → 等待结构化结果）；实施完成后主会话亲自验证并记录 validation，assurance:high 时引入全新上下文的只读审查者并经 review-record 申报裁决；视觉任务走子代理例外通道。适用于构建并验证功能、多步骤交付、前端/视觉任务、需要委派实施或独立代码审查的任务。
---

# GLM 编排：双轴选择性路由 + 原生 Workflow

## 1. 角色与前置

本技能要求主会话为 GLM-5.3，担任架构师。以下职责保留在主会话，不外派：

- 需求与歧义解决
- 架构与路由判断
- 任务分解（静态 DAG 编制）
- 实施规格与节点声明编写
- 完整 diff 检查与验证重跑（主验证）
- 路由重估决策
- 最终验收与完成申报

子智能体只能实施或审查，不得成为新的编排者。若当前主会话模型不是 GLM-5.3，告知用户切换后再继续。

分工边界（v2.4 冻结）：GLM Conductor 拥有语义编排与验收（路由、DAG、验证、审查、完成守卫）；ZCode 拥有执行编排（Workflow 运行、子代理生命周期、调度）。Conductor 不镜像宿主执行状态。

## 2. 双轴路由判断

不要把所有状态压缩成一个风险等级。先独立回答两个问题，再查路由矩阵：

**轴 A — Delegability（可委派性）**：剩余的实施工作是否足够有界、规格足够完备，可以委派出去？

**轴 B — Assurance（保障等级）**：实施完成并通过主会话验证后，是否需要一次全新上下文的独立终审？

两轴独立判断，互不推导。判据全表见 references/role-contracts.md。

## 3. ROUTING PREFLIGHT（路由前置侦察）

PREFLIGHT 不是路由，是路由的证据准备：双轴判断前若关键事实未知，先用只读侦察补齐，不用弱证据强行提前定路由。

**触发条件**（以下任一未知即触发）：根因 / 实施边界 / ownership 集合 / 接口契约 / 验证方案 / 隐藏耦合风险。任务已经显式（上述全部已知）时跳过——不为侦察而侦察。

**执行方式**：优先用 ZCode 内置只读 Explore 能力，必要时主会话亲自读码；不新增自定义侦察代理。侦察只读，不改动仓库。

**产出**（侦察完成后、SELECTIVE ROUTE 声明前输出；模板逐字，条目全部来自实际观察——文件/符号/调用链证据，不得包含推测性事实；OPEN AMBIGUITY 留空表示无未决歧义）：

```
ROUTING PREFLIGHT REPORT

ROOT CAUSE:
...

RELEVANT FILES:
- ...

INTERFACES:
- ...

TEST ENTRYPOINTS:
- ...

HIDDEN COUPLING:
- ...

OPEN AMBIGUITY:
- ...
```

该报告直接喂给双轴判断（§2）、TASK CONTEXT PACK 与静态节点声明（§5 / references/role-contracts.md）。

## 4. SELECTIVE ROUTE 声明

在任何实施委派（CreateWorkflow 提交或子智能体调用）之前必须输出一次（原样保留代码块）：

```
SELECTIVE ROUTE
mode: solo | delegate | audit | full
delegability: low | high
assurance: standard | high
reason: <简明的、基于证据的理由>
```

示例：

```
SELECTIVE ROUTE
mode: delegate
delegability: high
assurance: standard
reason: implementation is bounded by explicit interfaces, owned files, and deterministic verification
```

声明之前不得委派任何实施。delegability 是轴 A 的判断记录；进入任务状态（state.json）的只有 mode 与 assurance 两键（`runtime.task.create_task` 摘取口径，v2.4 schema 不持久化其余轴）。

## 5. 任务状态与静态 DAG 编制

### 5.1 任务创建

需要完成守卫保护的任务（delegate/full、audit，以及任何跨回合的长任务）在声明 SELECTIVE ROUTE 后创建任务状态：

- 入口：`runtime.task.create_task(repo_root, task_id, goal, route, dag_nodes=...)`（校验后落 `.glm-conductor/tasks/<task-id>/state.json` + journal `route_selected` 事件；route 只存 mode 与 assurance 两键）
- TASK_ID 用「语义前缀 + 随机十六进制后缀」机械唯一格式（规则见 continuity 技能）
- 纯会话内可完成、不需要守卫保护的一次性小任务可不创建 state.json——无状态文件时完成守卫零干预

### 5.2 静态节点（Work Unit = 编译期声明）

delegate/full 需要多段实施时，把任务分解为静态节点 DAG。节点只声明编译期事实，零运行时状态（`runtime/work_unit.py` 为权威形状）：

| 节点字段 | 必填 | 语义（五段语义的落点） |
| --- | --- | --- |
| `id` | 必填 | 节点标识，单 DAG 内唯一 |
| `objective` | 必填 | 有界实施目标（对应五段规格 OBJECTIVE） |
| `depends_on` | 必填 | 前置节点 id 数组——纯 DAG：禁止环、自依赖、缺失引用 |
| `ownership` | 必填 | 仓库相对路径 scope 数组，非空（对应 FILES AND OWNERSHIP） |
| `interfaces` | 可选 | 须保持兼容的接口面 |
| `constraints` | 可选 | 额外约束（仓库规范、安全边界、排除范围） |
| `local_check` | 可选 | 本地检查命令（worker 诚实执行，为空不虚构） |

编制纪律：

- **每个节点必须仍然小到能接收一个完整的有界 objective**——需要再拆说明分解不足；无文件范围（ownership 空）的节点不可编译
- 依赖只表达编译期顺序事实；编译器把无依赖冲突的节点并入同一阶段并行、其余串行（`ownership.plan_stages`：scope 明确重叠的候选确定性串行，歧义冲突直接拒绝）
- **两个并发实施的节点 ownership 不得重叠**——这是唯一需要的并行安全声明；写相互斥由仓库写者守卫（§6）兜底
- 五段式实施规格模板（OBJECTIVE / FILES AND OWNERSHIP / INTERFACES / CONSTRAINTS / VERIFICATION）仍是节点声明的思维框架与视觉通道的交付格式，见 references/role-contracts.md

## 6. delegate/full 的执行程序（原生 Workflow）

delegate/full 的唯一实施基底是 ZCode 原生 Workflow。主会话按以下固定顺序执行（运行时调用一律走 CLI：`python3 plugins/glm-conductor/runtime/cli.py <子命令>`，退出码 0=成功 / 2=校验拒绝 / 1=运行期拒绝；禁止 `python3 -c` 拼复杂内联脚本）：

1. **写 DAG JSON**：`{"task_context": {"task_ref": <task-id>, "goal": ..., "repository": ...}, "nodes": [...]}`（节点即 §5.2 形状）
2. **编译**：`cli.py v24-compile <dag-json> [--task-ref <id>] [--out <path>]`——逐节点形状校验 + 全图校验 + ownership 阶段规划 + 确定性 TypeScript 源。校验失败退出码 2 并一次报出全部错误；**生成源绝不自动执行**
3. **取仓库写预约**：`cli.py writer-acquire <repo_root> <task_id>`——一个 Git 仓库至多一个活跃 Conductor 写 Workflow；冲突时退出码 1 并报当前持有者（先对账再重试，绝不强行并行写）
4. **CreateWorkflow 提交**：主会话把编译产物经宿主 CreateWorkflow 提交（ZCode 拥有执行编排；模型与推理档位策略由主会话按仓库惯例指定，Conductor 不镜像）
5. **记录 run 关联**：拿到 run id 后立即 `runtime.task.record_workflow_run(repo_root, task_id, workflow_run_id)`（一条调用完成 state.workflow_run_id + `.glm-conductor/workflow-runs/` 关联单据 + journal `workflow_started` 事件）；只落关联单据可用 `cli.py v24-record-run <task_ref> <run-id>`
6. **等待结构化结果**：完成通知经宿主送达（GetWorkflowRun 可查进度）。每个节点返回一份 NodeResult：`node_id / status（complete | partial | blocked）/ changes / local_checks / reassessment / gaps`——全部结果按节点 id 聚合返回
7. **收尾**：验收通过后 `runtime.task.complete(...)`（终态迁移 + `task_completed` 事件 + 自动释放写者守卫）；失败/取消走 `task.fail` / `task.cancel`（同样释放守卫）

中断与恢复（Workflow 停摆、额度等待）见 `skills/continuity`；完成守卫语义见 `skills/enforcement`。

## 7. 主会话验证（main validation）

- Workflow 结果与工作者报告都只是**声明**（implementation claim）：主会话必须亲自检查完整 diff、核对改动范围是否 ⊆ DAG ownership 并集、重跑验证命令，才能形成验证证据
- 验证完成后落任务级验证记录：`runtime.task.record_validation(repo_root, task_id, status, summary=None, commands=None)`——status ∈ passed/failed；change_id 现算并存（相关路径集 = DAG 全节点 ownership 并集）
- **change_id 新鲜度**：任何相关文件在此之后的再变化都会使验证（与后续审查）记录过期——修复后必须重验重录；禁止回写旧值续命
- 多节点结果聚合后主会话仍须做任务级全局验证（跨节点集成/构建/lint）——节点级 local_check 永不自动替代全局验证

## 8. 高保证评审（仅 assurance: high）

按任务模态选择全新上下文的只读审查者：

- 文本任务 → `glm-reviewer`（只读白名单，模型继承宿主/会话——不宣称跨模型独立，独立性来自全新上下文与只读隔离）
- 视觉任务 → `visual-reviewer`（多模态只读，主职独立视觉验收：亲自读截图对照 VISUAL ACCEPTANCE、检查用户可见回归；diff 仅作上下文）

纪律：

- 审查必须在主会话验证之后调用；输入要素（STATED GOAL / ACCUMULATED CHANGE SET / INTERFACES AND CONSTRAINTS / VERIFICATION EVIDENCE，视觉任务另加 VISUAL EVIDENCE）由主会话提供，缺项要求补齐而非猜测
- 裁决只有三个：`ship` / `fix-first` / `rethink`。fix-first：audit 路由主会话修正、full 路由由修复实施方修正，修正后主会话重验，再换**全新**审查者复审；rethink：修订架构，不得报告完成。fix-first / rethink 永不满足完成守卫
- 裁决落账走 CLI：`cli.py review-record <repo_root> <task_id> <reviewer> <verdict> [note]`——**先验证后评审**（无 validation 记录即拒绝），change_id 现算绑定；任何相关文件再变化都会使裁决过期，须重新审查重录
- 完成守卫（assurance: high 时）只认 verdict=ship 且 change_id 新鲜的审查记录

## 9. 视觉例外路径（子代理通道，照旧）

视觉/交互任务（前端界面、布局、dashboard、表单交互、视觉回归）不走原生 Workflow 文本通道，直接派发 `visual-implementer` 子智能体实施：

- 主会话用其专属 Browser/Computer Use 能力采集截图落盘（子代理无采集权）；实施者以 `VISUAL_CAPTURE_REQUEST` 结束调用索取证据，主会话发起携带完整状态的新调用续作（VISUAL_ROUND 1-3，最多 3 轮）
- 视觉判定由多模态角色完成（visual-implementer / visual-reviewer 亲自 Read 截图）；GLM-5.3 主会话是纯文本模型，只能驱动采集，不得声称自己做了视觉判定
- 视觉任务 assurance: high 时用 `visual-reviewer` 独立终审（裁决与 review-record 流程同 §8）

判据、模板与协议细节见 references/role-contracts.md 与 references/operations.md。

## 10. 审查者与实施者预检（fail-closed）

- 视觉通道需要 `glm-conductor:visual-implementer` 在 Agent 工具的可用类型列表中；assurance: high 按模态核对 `glm-conductor:glm-reviewer` / `glm-conductor:visual-reviewer`
- delegate/full 的文本实施经原生 Workflow 执行（worker persona 由编译器内嵌生成源），无需子智能体预检；solo 与 assurance: standard 无需任何预检
- 模型与思考档位已固定在子智能体定义中，调用时不得附加模型覆盖
- 视觉通道额外要求：确认截图证据可以落盘并由实施者读取；不可得即停止视觉通道
- 任何所需角色缺失、不可用或名称不符时：停止该通道，告知用户检查插件安装（Settings → Plugin Management），不得静默替换为其他子智能体类型

## 11. ROUTE REASSESSMENT

路由不是单向阶梯。路由变化必须来自**新观察到的证据**，可以双向重估：

```
ROUTE REASSESSMENT

delegability: <old> -> <new>
assurance: <old> -> <new>
mode: <old> -> <new>

evidence:
<新观察到的证据>
```

示例 A（下调可委派性）：delegate 执行中节点结果频繁返回 `reassessment` 重估请求、暴露隐藏的跨模块耦合 →

```
delegability: high -> low
assurance: standard -> high
mode: delegate -> audit
evidence: node results kept returning reassessment requests exposing hidden cross-module coupling
```

主会话接管实施 → 验证 → 审查者终审。

示例 B（上调可委派性）：solo 调查后 root cause、架构、文件、验证均已确定，剩余工作完全机械 →

```
delegability: low -> high
mode: solo -> delegate
evidence: root cause, architecture, owned files and verification are now fully determined; remaining work is mechanical
```

前提：主会话尚未重复完成同一实施。无新证据时不得变更路由；不得为了省事或直觉变更。重估后把 `workflow_reassessed` 事件追加到任务 journal。

## 12. 完成衔接

任务收尾（complete/fail/cancel）与完成判定由 Stop 完成守卫把关：仓库写者守卫、ownership 越界、主会话验证、独立审查四查全过才提交 completed。守卫语义、change_id 新鲜度与降级行为见 `skills/enforcement`。
