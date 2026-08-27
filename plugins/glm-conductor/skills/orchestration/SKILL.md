---
name: orchestration
description: GLM 双轴选择性路由编排。GLM-5.3 主会话任架构师，按 Delegability × Assurance 两个独立维度在首次任务委派前声明 SELECTIVE ROUTE（solo/delegate/audit/full）；executor 独立选择实施者（flash-implementer / visual-implementer），assurance:high 时按任务模态引入全新上下文的只读审查者；路由可基于新证据双向重估。适用于构建并验证功能、多步骤交付、前端/视觉任务、需要委派实施或独立代码审查的任务。
---

# GLM 编排：双轴选择性路由

## 1. 角色与前置

本技能要求主会话为 GLM-5.3，担任架构师。以下职责保留在主会话，不外派：

- 需求与歧义解决
- 架构与路由判断
- 任务分解
- 实施规格编写
- 完整 diff 检查与验证重跑
- 路由重估决策
- 最终验收

子智能体只能实施或审查，不得成为新的编排者。若当前主会话模型不是 GLM-5.3，告知用户切换后再继续。

## 2. 双轴路由判断

不要把所有状态压缩成一个风险等级。先独立回答两个问题，再查路由矩阵：

**轴 A — Delegability（可委派性）**：剩余的实施工作是否足够有界、规格足够完备，可以委派出去？

**轴 B — Assurance（保障等级）**：实施完成并通过主会话验证后，是否需要一次全新上下文的独立终审？

两轴独立判断，互不推导。判据全表见 references/role-contracts.md。

## 3. SELECTIVE ROUTE 声明

在任何 Agent（任务）工具调用之前必须输出一次（原样保留代码块）：

```
SELECTIVE ROUTE
mode: solo | delegate | audit | full
delegability: low | high
assurance: standard | high
executor: main | flash-implementer | visual-implementer
continuity: foreground | resumable | idle
reason: <简明的、基于证据的理由>
```

示例：

```
SELECTIVE ROUTE
mode: delegate
delegability: high
assurance: standard
executor: flash-implementer
continuity: foreground
reason: implementation is bounded by explicit interfaces, owned files, and deterministic verification
```

声明之前不得调用任何任务工具。

## 4. 路由矩阵

| Delegability | Assurance | Route | 实施 | 独立审查 |
| --- | --- | --- | --- | --- |
| low | standard | `solo` | GLM-5.3 主会话 | 否 |
| high | standard | `delegate` | 实施者子智能体 | 否 |
| low | high | `audit` | GLM-5.3 主会话 | 是 |
| high | high | `full` | 实施者子智能体 | 是 |

语义要点：

- **delegate 不是 solo 的"升级"**。solo = 旗舰实施，delegate = Flash 实施，两者只是实施者不同；delegate 表达的是"该实施已足够有界，可由执行模型完成"，并不天然更安全或更高级。
- **audit = 主会话实施 + 独立终审**，特别适合判断密集、敏感、架构重的实施。
- **full = 委派实施 + 独立终审**，适合大规模但有界的工作（明确规则的迁移、清晰规格的 UI 改版等）。

## 5. Executor 选择

route 决定"是否委派、是否审查"；executor 决定"谁来实施、需要什么能力"。两者分开：

- 标准编码任务（后端函数、测试、重构、CLI、配置、确定性转换）→ `flash-implementer`
- 视觉/交互任务（前端界面、布局、dashboard、表单交互、视觉回归）→ `visual-implementer`

视觉任务是 executor 维度的一种能力选择，**不是第五种 route**。判据与不适用清单见 role-contracts.md。

## 6. 审查者选择（assurance: high 时）

按任务模态选择全新上下文的只读审查者：

- 文本任务 → `glm-reviewer`（GLM-5.3，审代码 diff）
- 视觉任务 → `visual-reviewer`（GLM-5.3-Flash 多模态，同时审 diff 与截图证据）

GLM-5.3 主会话与 glm-reviewer 均为纯文本模型：**主会话在视觉链路中只能驱动采集（截图落盘），不得声称自己做了视觉判定**；视觉判定由 Flash 系角色完成。

## 7. 预检（fail-closed）

- 按声明的 executor 与审查者，核对所需子智能体是否在 Agent 工具的可用类型列表中
- solo（executor: main）无需实施者预检；assurance: standard 无需审查者预检
- 模型与思考档位已固定在子智能体定义中，调用时不得附加模型覆盖
- 视觉通道额外要求：确认截图证据可以落盘并由实施者读取；不可得即停止视觉通道
- 任何所需角色缺失、不可用或名称不符时：停止该通道，告知用户检查插件安装（Settings → Plugin Management），不得静默替换为其他子智能体类型

## 8. 规格与报告

- 委派必须使用五段式实施规格（OBJECTIVE / FILES AND OWNERSHIP / INTERFACES / CONSTRAINTS / VERIFICATION），返回后按 IMPLEMENTATION REPORT 接收；完整模板见 references/role-contracts.md（首次委派前必须阅读）
- 工作者的报告仅视为声明（implementation claim）：主会话必须亲自检查完整 diff、核对改动范围、重跑验证命令，才能形成验证证据（verification evidence）

## 9. 评审与裁决（仅 assurance: high）

- 审查者保持只读，返回 `ship` / `fix-first` / `rethink` 三种裁决之一（文本任务用 GLM REVIEW 格式，视觉任务用 VISUAL REVIEW 格式，见 role-contracts.md）
- fix-first：audit 路由由主会话修正，full 路由由原实施者修正；修正后主会话重验，再换用全新审查者
- rethink：修订架构，不得报告完成
- 任何修复使先前裁决失效；审查者与实施者/主会话同模型家族，独立性来自全新上下文与只读隔离，不宣称跨模型独立

## 10. ROUTE REASSESSMENT

路由不是单向阶梯。路由变化必须来自**新观察到的证据**，可以双向重估：

```
ROUTE REASSESSMENT

delegability: <old> -> <new>
assurance: <old> -> <new>
mode: <old> -> <new>

evidence:
<新观察到的证据>
```

示例 A（下调可委派性）：delegate 执行中暴露隐藏的跨模块状态耦合 →

```
delegability: high -> low
assurance: standard -> high
mode: delegate -> audit
evidence: implementation exposed hidden cross-module state coupling
```

主会话接管实施 → 验证 → 审查者终审。

示例 B（上调可委派性）：solo 调查后 root cause、架构、文件、验证均已确定，剩余工作完全机械 →

```
delegability: low -> high
mode: solo -> delegate
evidence: root cause, architecture, owned files and verification are now fully determined; remaining work is mechanical
```

前提：主会话尚未重复完成同一实施。

无新证据时不得变更路由；不得为了省事或直觉变更。

## 11. Continuity（独立维度）

`continuity` 是与 route 正交的生命周期维度（foreground / resumable / idle），不是第五种 route。长任务的检查点、恢复与闲时执行机制见 `skills/continuity`；本技能只在路由声明中携带 continuity 字段，不在此重复实现。
