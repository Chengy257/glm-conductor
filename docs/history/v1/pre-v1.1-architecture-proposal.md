# GLM Conductor — v2 Architecture & Implementation Specification

> SUPERSEDED
>
> This document describes the pre-v1.1 architecture proposal.
> It is retained only for historical reference.
>
> Do NOT use this document as the current implementation source of truth.
> The authoritative architecture is `docs/architecture.md`.
>
> ----
>
> **Proposed project name:** `glm-conductor`  
> **Current project:** `glm-advisor`  
> **Target environment:** ZCode + GLM-5.3 + GLM-5.3-Flash  
> **Document purpose:** 作为下一版本的架构与实施规范，可直接交给 coding agent 执行。

---

## 1. Revision Scope

本次版本只增加三个核心能力：

1. **Two-axis Selective Routing**
   - 从单一“风险升级”改为：
   - **Delegability × Assurance**

2. **Visual Implementer**
   - 新增基于 GLM-5.3-Flash 的视觉/交互实施者
   - 面向 frontend、browser、GUI、dashboard 等任务

3. **Long-Horizon Continuity**
   - 支持长时间 Coding 任务跨 session interruption / Coding Plan window 持续执行
   - 优先利用 ZCode 原生 Goal、Scheduled Task、Idle-time Task
   - 不自行实现 quota daemon

### Explicitly Out of Scope

本版本**不增加**：

- benchmark framework
- cost telemetry
- cost-aware routing
- token accounting
- model performance benchmarking
- learned / adaptive routing
- 自动根据价格选择模型

本版本重点是改善 **任务编排语义、执行能力和长任务连续性**。

---

# 2. Core Design Principles

## 2.1 GLM-5.3 remains the architect

主会话 GLM-5.3 始终保留：

- requirement interpretation
- ambiguity resolution
- architecture
- task decomposition
- route selection
- executor selection
- implementation specification
- complete diff inspection
- verification rerun
- route reassessment
- final acceptance

Subagent 只能：

- 实施；
- 或审查。

不得成为新的 orchestrator。

---

## 2.2 Delegation substitutes implementation

当任务已经委派给 implementer：

主会话不得再次重复实现相同工作。

主会话只负责：

```text
inspect diff
    ↓
check scope
    ↓
rerun verification
    ↓
accept / repair / reassess / audit
```

---

## 2.3 Completion claim ≠ evidence

Implementer 返回的：

```text
IMPLEMENTATION REPORT
STATUS: complete
```

只能视为：

> implementation claim

不能直接视为完成证据。

只有主会话亲自：

- 检查完整 diff；
- 查看真实文件状态；
- 重跑 verification；
- 检查 runtime / visual result；

之后才能形成：

> verification evidence

---

# 3. New Conceptual Architecture

下一版本不要再把所有状态压缩成一个 route。

一次任务由四个相互独立的维度描述：

```yaml
routing:
  delegability: low | high
  assurance: standard | high

route:
  mode: solo | delegate | audit | full

execution:
  executor: main | flash-implementer | visual-implementer

continuity:
  mode: foreground | resumable | idle
```

分别回答：

```text
Delegability
    ↓
能不能把 implementation 委派出去？

Assurance
    ↓
完成后需不需要 fresh independent-context review？

Executor
    ↓
如果委派，谁最适合执行？

Continuity
    ↓
任务如果很长，如何持续执行？
```

---

# 4. Two-Axis Selective Routing

## 4.1 Axis A — Delegability

判断问题：

> Is the remaining implementation sufficiently bounded and specified to delegate?

### `delegability: high`

通常满足：

- objective 明确；
- observable outcome 明确；
- target files / ownership 边界明确；
- interface 明确；
- constraints 明确；
- verification 明确；
- architecture 已经决定；
- 剩余任务主要是 implementation；
- implementation 过程中不需要频繁重新设计。

典型任务：

- 已确定接口的函数实现；
- test / fixture 补充；
- repetitive refactor；
- CLI wiring；
- config plumbing；
- serialization；
- 已知 root cause 的 bug fix；
- 明确规则的大量代码迁移。

### `delegability: low`

出现以下情况时优先判断为 low：

- architecture 尚未确定；
- root cause 未知；
- requirement 有实质歧义；
- 大量跨模块判断；
- implementation 本身就是主要 reasoning problem；
- concurrency / distributed state 等隐藏复杂度；
- sensitive decision 尚未解决；
- 实施过程中可能频繁改变设计。

此时应由 GLM-5.3 主会话实施。

---

# 5. Axis B — Assurance

判断问题：

> After implementation and parent verification, is a fresh final review materially valuable?

### `assurance: standard`

适用于：

- blast radius 有限；
- verification 足够明确；
- regression risk 可控；
- 普通常规 implementation。

### `assurance: high`

以下情况优先使用：

- wide blast radius；
- high regression potential；
- authentication / permissions；
- destructive behavior；
- 数据迁移；
- cross-module semantic change；
- verification 难以覆盖所有重要 failure mode；
- 大规模 user-facing change；
- 用户明确要求严格审查。

---

# 6. Route Matrix

新的 route 定义：

| Delegability | Assurance | Route | Implementation | Reviewer |
|---|---|---|---|---|
| low | standard | `solo` | GLM-5.3 | No |
| high | standard | `delegate` | Implementer | No |
| low | high | `audit` | GLM-5.3 | Yes |
| high | high | `full` | Implementer | Yes |

---

# 7. Important Semantic Changes

## 7.1 `delegate` is NOT an escalation from `solo`

旧模型中的：

```text
solo
  ↓
delegate
  ↓
full
```

不再视为一个风险等级 ladder。

因为：

```text
solo
= GLM-5.3 implementation

delegate
= GLM-5.3-Flash implementation
```

`delegate` 并不天然比 `solo` 更安全。

它表达的是：

> 该 implementation 已经足够 bounded，可以由执行模型完成。

---

## 7.2 `audit`

新的准确语义：

```text
delegability: low
assurance: high
```

流程：

```text
GLM-5.3 implementation
        ↓
parent verification
        ↓
fresh GLM-5.3 reviewer
```

特别适合：

- judgment-heavy；
- sensitive；
- architecture-heavy；
- high-risk implementation。

---

## 7.3 `full`

不再定义为：

> generic high-risk route

而定义为：

```text
delegability: high
assurance: high
```

流程：

```text
bounded implementation
        ↓
implementer
        ↓
GLM-5.3 verification
        ↓
fresh reviewer
```

适合：

- 大规模但机械的 API migration；
- 清晰 specification 下的 UI overhaul；
- repetitive multi-file transformation；
- 明确规则的大规模 refactor；
- 高 user-facing impact 但 implementation 本身 bounded。

---

# 8. New SELECTIVE ROUTE Declaration

建议替换现在的：

```text
SELECTIVE ROUTE
mode: ...
risk: ...
```

为：

```text
SELECTIVE ROUTE
mode: solo | delegate | audit | full
delegability: low | high
assurance: standard | high
executor: main | flash-implementer | visual-implementer
continuity: foreground | resumable | idle
reason: <concise evidence-based rationale>
```

例如：

```text
SELECTIVE ROUTE
mode: delegate
delegability: high
assurance: standard
executor: flash-implementer
continuity: foreground
reason: implementation is bounded by explicit interfaces, owned files, and deterministic verification
```

视觉任务：

```text
SELECTIVE ROUTE
mode: full
delegability: high
assurance: high
executor: visual-implementer
continuity: resumable
reason: bounded UI implementation requires visual interaction and has broad user-facing impact
```

---

# 9. Route Reassessment

删除：

> route can only upgrade

改为：

> **ROUTE REASSESSMENT**

路由变化必须来自**新观察到的证据**。

---

## Example A

最初：

```text
delegate
```

Flash 发现：

- shared state coupling；
- architecture ambiguity；
- scope 超过 FILES AND OWNERSHIP。

则：

```text
ROUTE REASSESSMENT

delegability: high -> low
assurance: standard -> high
mode: delegate -> audit

evidence:
implementation exposed hidden cross-module state coupling
```

然后：

```text
GLM-5.3 takes over implementation
        ↓
verification
        ↓
reviewer
```

---

## Example B

开始为：

```text
solo
```

GLM-5.3 调查后已经确定：

- root cause；
- architecture；
- exact files；
- verification。

剩余部分完全机械。

可以重新判断：

```text
delegability: low -> high
mode: solo -> delegate
```

前提：

> 主会话尚未重复完成同一 implementation。

---

# 10. Executor Selection

Route 和 Executor 必须分开。

```text
route
    ↓
whether delegation/review is needed

executor
    ↓
what implementation capability is appropriate
```

---

# 11. Standard Flash Implementer

保留：

```text
flash-implementer
```

模型：

```yaml
model: GLM-5.3-Flash
thoughtLevel: high
```

主要用于：

- bounded code implementation；
- tests；
- fixtures；
- refactor；
- config；
- CLI；
- deterministic transformations；
- known bug fixes；
- mechanical migration。

现有核心纪律继续保留：

```text
execute specification
do not redesign architecture
stay inside owned files
preserve interfaces
report ambiguity
run verification
return evidence-bearing report
```

---

# 12. New Visual Implementer

新增：

```text
agents/visual-implementer.md
```

模型：

```yaml
model: GLM-5.3-Flash
thoughtLevel: high
```

---

# 13. Why Visual Implementer Is Separate

不要直接把 `flash-implementer` 变成“什么都做”。

Visual task 有完全不同的验证闭环：

普通 coding：

```text
implement
   ↓
run tests
```

视觉任务：

```text
implement
   ↓
launch
   ↓
observe
   ↓
interact
   ↓
visual verify
   ↓
refine
   ↓
functional verify
```

因此需要独立 role contract。

---

# 14. Visual Task Examples

使用 `visual-implementer`：

- React/Vue/Svelte frontend
- CSS / layout
- responsive design
- dashboard
- browser workflow
- form interaction
- GUI
- Canvas / WebGL
- game UI
- visual regression
- screenshot-based acceptance
- user-facing page redesign

不要用于：

- 普通 backend function；
- CLI；
- parser；
- pure data transformation；
- 无视觉 acceptance 的 server code。

---

# 15. Visual Implementer Contract

Visual implementer 必须：

1. 遵守五段式 implementation specification；
2. 只改 ownership 文件；
3. 不重新设计 architecture；
4. 使用 Browser / Computer capability 检查实际界面；
5. 必要时执行真实 interaction；
6. 在有限次数内修正明显视觉问题；
7. 最后执行 functional verification；
8. 返回视觉证据；
9. 发现实质歧义则停止并请求 route reassessment。

---

# 16. Visual Feedback Loop

标准流程：

```text
IMPLEMENT
    ↓
LAUNCH / PREVIEW
    ↓
OBSERVE
    ↓
INTERACT
    ↓
VISUAL VERIFY
    ↓
REFINE
    ↓
FUNCTIONAL VERIFY
```

禁止：

> 无限 visual polish loop

如果 acceptance criteria 不清晰，应：

```text
blocked / reassessment
```

而不是自行不断改变设计。

---

# 17. Extend Implementation Specification

对于 Visual Task，可以在现有五段式 contract 后增加：

```text
VISUAL ACCEPTANCE:
- target page / component
- viewport
- required interaction
- expected visible state
- responsive behavior
- clipping / overflow constraints
- important user-visible content
```

例如：

```text
VISUAL ACCEPTANCE:
- desktop 1440×900
- mobile 390×844
- navigation remains visible
- no horizontal overflow
- submit button remains reachable
- result panel updates after form submission
```

---

# 18. Visual Implementation Report

Visual agent 返回：

```text
IMPLEMENTATION REPORT
STATUS: complete | partial | blocked

OBJECTIVE:
<goal>

CHANGES:
- <file>: <change>

FUNCTIONAL VERIFIED:
- <command / interaction>: <result>

VISUAL VERIFIED:
- <page / viewport / flow>: <observed result>

JUDGMENT CALLS:
- <decision or none>

GAPS:
- <gap or none>
```

主会话仍不得直接相信报告。

需要：

```text
inspect diff
rerun available verification
inspect visual acceptance where practical
```

---

# 19. Tool Policy for Visual Implementer

不要机械复制目前 `flash-implementer` 的静态工具白名单。

原因：

Visual Agent 必须能使用：

- Browser Use
- Computer Use
- screenshot / browser interaction
- 当前 session 中实际可用的相关 MCP/tool

Implementation agent 应优先：

> 继承当前 session 合适的工具能力

如果 ZCode 当前版本需要显式工具名：

> agent 在实施前必须核实当前 ZCode 文档/运行时实际 tool identifiers。

不得：

> 声称进行了 visual verification，但实际上没有视觉工具。

如果视觉能力不可用：

```text
STATUS: blocked
```

并交回主会话。

---

# 20. Long-Horizon Continuity

新增：

```text
skills/continuity/
```

建议目录：

```text
skills/
├── orchestration/
│   └── ...
│
└── continuity/
    ├── SKILL.md
    └── references/
        └── long-horizon.md
```

---

# 21. Continuity Is NOT a Fifth Route

不要增加：

```text
solo
delegate
audit
full
resume   # WRONG
```

Continuity 是独立维度：

```text
route:
  full

executor:
  visual-implementer

continuity:
  resumable
```

---

# 22. Continuity Modes

定义三种：

```text
foreground
resumable
idle
```

---

## 22.1 Foreground

默认。

适合：

- 普通任务；
- 当前 session 内预期完成；
- 需要用户实时互动。

行为：

```text
current session
     ↓
execute normally
```

不创建额外 continuation。

---

## 22.2 Resumable

适合：

- 长时间 foreground task；
- 可能跨 Coding Plan window；
- 可能因 availability 中断；
- 仍希望继续当前 session；
- 不适合完全无人值守。

核心：

```text
current task
    ↓
checkpoint
    ↓
scheduled same-session wakeup
    ↓
inspect state
    ↓
resume safely
```

---

## 22.3 Idle

适合：

- 非紧急长任务；
- 可以无人值守；
- validation 可以自动完成；
- ZCode idle-time task 可用。

原则：

> 优先使用 ZCode 原生 idle-time execution。

不要自己实现：

- daemon；
- while-loop supervisor；
- OS cron wrapper；
- quota polling server。

---

# 23. Do Not Invent a Quota API

Continuity 第一版严禁假设存在：

```text
getQuotaRemaining()
getQuotaResetTime()
onQuotaReset()
```

除非 implementation 时 ZCode 已经公开并正式支持。

项目不得宣传：

> 能实时读取当前 Coding Plan 5h quota

如果实际上只能从 UI 观察。

---

# 24. Resume Watchdog

对于 `resumable`：

不要实现：

```text
sleep until current_time + 5h
```

而应采用：

> safe periodic reactivation

概念：

```text
unfinished goal
      ↓
save checkpoint
      ↓
scheduled wakeup
      ↓
inspect current state
      ↓
┌───────────────────────┐
│ goal already complete │──→ stop
└───────────────────────┘

otherwise
      ↓
is execution available?
      ↓
 yes ──→ resume

 no
      ↓
leave repository untouched
      ↓
try again on later trigger
```

这样不会依赖：

- 固定 5h reset 时间；
- weekly quota；
- reset card；
- 未来政策变化。

---

# 25. Continuity Checkpoint

对于 resumable / idle task：

在 meaningful milestone 后写入：

```text
CONTINUITY CHECKPOINT

GOAL:
<original objective>

ROUTE:
<solo | delegate | audit | full>

DELEGABILITY:
<low | high>

ASSURANCE:
<standard | high>

EXECUTOR:
<main | flash-implementer | visual-implementer>

COMPLETED:
- ...

CURRENT STATE:
- ...

NEXT ACTION:
- ...

VERIFICATION:
- completed:
  - ...
- pending:
  - ...

OPEN RISKS:
- ...
```

---

# 26. Checkpoint Principles

Checkpoint 是：

> navigation state

不是：

> repository source of truth

因此：

- 不复制完整 diff；
- 不复制大量代码；
- 不声称未验证内容；
- repository state 优先于 checkpoint；
- 每个 substantial milestone 更新一次即可；
- 不要每次 tool call 都写 checkpoint。

---

# 27. Resume Procedure

每次重新激活后必须依次：

```text
1. inspect current Goal
2. read latest checkpoint
3. inspect repository status
4. inspect current diff
5. determine whether previous changes still exist
6. check whether Goal is already complete
7. inspect latest verification state
8. resume from NEXT ACTION
```

禁止：

```text
wake up
   ↓
blindly execute previous instruction again
```

---

# 28. Resume Safety Rule

如果 checkpoint 与 repository 不一致：

```text
repository > checkpoint
```

必须：

1. 检查当前真实文件；
2. 分析变化来源；
3. 更新 current state；
4. 重新判断下一步。

不得：

> 为了恢复 checkpoint 而回滚 repository 中的新改动。

---

# 29. Goal Mode Integration

Continuity 不重新实现 Goal Mode。

职责分工：

```text
Goal Mode
    ↓
Is the overall user goal complete?

glm-conductor orchestration
    ↓
Who implements?
How is it verified?
Does it need review?

continuity
    ↓
How does unfinished work resume?
```

三者应组合，而不是相互替代。

---

# 30. Scheduled Resume Instruction

不要安排：

```text
continue
```

应使用结构化 resume prompt：

```text
Inspect the current session goal, the latest GLM Conductor continuity
checkpoint, and the current repository state.

If the original goal is already satisfied, perform no further
implementation and end the continuation.

If the goal remains incomplete, resume from the last verified
checkpoint.

Do not redo completed work.

Inspect the current diff and outstanding verification evidence before
performing additional implementation.

Continue under the existing route, executor, ownership, verification,
and review contracts.

Perform ROUTE REASSESSMENT only if newly observed evidence changes
delegability or assurance.
```

---

# 31. Idle-Time Integration

如果 ZCode 当前运行环境支持 native idle task：

优先：

```text
long unattended task
        ↓
idle-time task
        ↓
native execution segment
        ↓
native requeue
        ↓
same task/session continuation
```

而不是：

```text
custom watchdog daemon
```

---

# 32. Continuity Failure Modes

## Automation unavailable

如果 Scheduled / Idle capability 不存在：

```text
do not claim continuity enabled
```

而应该：

```text
preserve checkpoint
report manually resumable
```

---

## Executor missing

保持 fail-closed：

如果：

```text
visual-implementer
```

不存在：

> 不得自动换成 generic implementer。

---

## Visual tools unavailable

如果：

```text
visual-implementer
```

没有 Browser / Computer capability：

> 停止 Visual lane。

不得：

> 用文字推测页面看起来正常。

---

## Reviewer unavailable

`audit/full`：

Reviewer 缺失：

> 不得降级成无 review 的交付。

---

# 33. Target Repository Structure

建议：

```text
glm-conductor/
├── README.md
├── marketplace.json
│
└── plugins/
    └── glm-conductor/
        │
        ├── .zcode-plugin/
        │   └── plugin.json
        │
        ├── agents/
        │   ├── flash-implementer.md
        │   ├── visual-implementer.md
        │   └── glm-reviewer.md
        │
        └── skills/
            │
            ├── orchestration/
            │   ├── SKILL.md
            │   └── references/
            │       ├── operations.md
            │       └── role-contracts.md
            │
            └── continuity/
                ├── SKILL.md
                └── references/
                    └── long-horizon.md
```

如果当前阶段暂时不改 plugin ID：

可以先继续：

```text
plugins/glm-advisor/
```

等功能稳定后再统一 rename。

**建议不要把“架构修改”和“全仓库 rename”放在同一个 commit。**

---

# 34. File-by-File Implementation Guide

## `skills/orchestration/SKILL.md`

必须修改：

- 删除一维 risk ladder；
- 定义 Delegability；
- 定义 Assurance；
- 添加 2×2 route matrix；
- 修改 SELECTIVE ROUTE；
- 添加 executor selection；
- 加入 `visual-implementer`；
- 将 escalation 改为 reassessment；
- continuity 只作为一个字段引用；
- 不在 orchestration skill 内重复实现 continuity。

---

## `references/role-contracts.md`

必须修改：

- 新 route declaration；
- Delegability contract；
- Assurance contract；
- route matrix；
- route reassessment；
- visual implementer contract；
- 明确：
  - audit = main + reviewer
  - full = delegated + reviewer

保留：

- five-section implementation spec；
- implementation report；
- parent verification；
- reviewer verdict contract。

---

## `references/operations.md`

修改：

- preflight 根据 `executor` 检查 agent；
- Visual lane 检查视觉工具；
- 删除 monotonic escalation；
- 加入 reassessment；
- 描述 resumed session 如何恢复 route；
- cross-reference continuity；
- 保留 fresh-reviewer semantics。

---

## `agents/flash-implementer.md`

只做小改。

保留：

- GLM-5.3-Flash/high；
- bounded implementation；
- ownership；
- interfaces；
- verification；
- ambiguity escalation。

修改描述为：

> standard bounded implementation executor

不要再描述为：

> low-risk lane

---

## `agents/visual-implementer.md`

新增。

必须包含：

```text
ROLE
SCOPE DISCIPLINE
VISUAL FEEDBACK LOOP
FUNCTIONAL VERIFICATION
VISUAL VERIFICATION
AMBIGUITY HANDLING
RETURN CONTRACT
```

---

## `agents/glm-reviewer.md`

主体保持不变。

优化术语：

不要强调：

> independent model reviewer

改为：

> fresh-context, implementation-isolated reviewer

因为主会话和 reviewer 都是 GLM-5.3。

---

## `skills/continuity/SKILL.md`

新增。

至少包含：

```text
Purpose
Modes
Selection
Checkpoint
Resume Procedure
Scheduled Resume
Idle Execution
Goal Mode Integration
Failure Handling
Quota API Boundary
Completion Cleanup
```

---

## `continuity/references/long-horizon.md`

记录详细运行机制：

- lifecycle；
- checkpoint template；
- resume prompt；
- scheduled trigger；
- idle task；
- route recovery；
- completion cleanup；
- failure cases；
- ZCode limitations。

---

# 35. Updated High-Level Architecture

```text
                         USER GOAL
                             │
                             ▼
                    GLM-5.3 ARCHITECT
                             │
                 ┌───────────┴───────────┐
                 │                       │
          DELEGABILITY               ASSURANCE
             low/high              standard/high
                 │                       │
                 └───────────┬───────────┘
                             │
                             ▼
                    SELECTIVE ROUTE
          solo / delegate / audit / full
                             │
                             ▼
                    EXECUTOR SELECTION
                   ┌─────────┴─────────┐
                   │                   │
              standard task        visual task
                   │                   │
          flash-implementer    visual-implementer
                   │                   │
                   └─────────┬─────────┘
                             │
                             ▼
                    PARENT VERIFICATION
                             │
                     Assurance high?
                       │           │
                      No          Yes
                       │           │
                       │       GLM Reviewer
                       │           │
                       └─────┬─────┘
                             │
                             ▼
                     FINAL ACCEPTANCE

                      CONTINUITY LAYER
                             │
            ┌────────────────┼────────────────┐
            │                │                │
       foreground        resumable           idle
```

---

# 36. Acceptance Criteria

## Routing

- [ ] Delegability 和 Assurance 为两个独立判断。
- [ ] `delegate` 不再被描述为 `solo` 的风险升级。
- [ ] `audit = main implementation + review`。
- [ ] `full = delegated implementation + review`。
- [ ] Route 可以基于新证据 reassess。
- [ ] Route change 必须给出新 evidence。

## Executor

- [ ] 保留 `flash-implementer`。
- [ ] 新增 `visual-implementer`。
- [ ] Visual 使用 GLM-5.3-Flash。
- [ ] Visual 不是第五种 route。
- [ ] Visual agent 能进行 Browser/Computer verification。
- [ ] 无视觉能力时 fail closed。

## Reviewer

- [ ] read-only。
- [ ] fresh context。
- [ ] repair 后旧 verdict 失效。
- [ ] 不暗示跨模型独立性。

## Continuity

- [ ] foreground / resumable / idle 三种模式存在。
- [ ] Continuity 与 route 独立。
- [ ] 有 CONTINUITY CHECKPOINT。
- [ ] Resume 首先检查 repository 当前状态。
- [ ] 不重复已完成工作。
- [ ] 不 hardcode 5h reset。
- [ ] 不虚构 quota API。
- [ ] 支持 same-session scheduled resume 的设计。
- [ ] unattended long work 优先使用 idle-time。
- [ ] automation 不可用时安全失败。
- [ ] 完成后停止 continuation。

## Scope

- [ ] 不增加 benchmark。
- [ ] 不增加 cost telemetry。
- [ ] 不增加 cost-aware routing。
- [ ] 不增加 learned router。

---

# 37. Recommended Implementation Order

## Phase 1 — Routing Refactor

先改：

```text
orchestration/SKILL.md
role-contracts.md
operations.md
README.md
```

目标：

> 完成 Delegability × Assurance 语义重构。

不要同时做 Visual / Continuity。

---

## Phase 2 — Visual Implementer

新增：

```text
visual-implementer.md
```

然后更新：

- orchestration；
- role contract；
- operations；
- README；
- plugin manifest。

---

## Phase 3 — Continuity

新增：

```text
skills/continuity/
```

实现：

- foreground；
- resumable；
- idle；
- checkpoint；
- resume procedure。

---

## Phase 4 — Consistency Pass

全仓库检查：

```text
risk ladder
upgrade only
high-risk = full
delegate = safer
```

等旧语义是否残留。

检查：

- JSON；
- YAML frontmatter；
- agent names；
- examples；
- installation；
- README。

---

# 38. Git Commit Strategy

推荐拆成：

```text
refactor: introduce two-axis selective routing

feat: add GLM Flash visual implementer

feat: add long-horizon continuity orchestration

docs: update architecture and usage examples

chore: rename glm-advisor to glm-conductor
```

项目 rename 最后进行。

不要一开始就 rename。

否则：

> architecture diff + directory rename + manifest rename

会混在一起，非常难 review。

---

# 39. Direct Coding-Agent Instruction

可以直接把下面这段连同本文档交给 agent：

```text
Implement the architecture specified in this document in the current
glm-advisor repository.

Treat this document as the architectural source of truth.

Before editing:

1. Inspect the complete current repository.
2. Read orchestration/SKILL.md, role-contracts.md, operations.md,
   flash-implementer.md, glm-reviewer.md, plugin.json, marketplace.json,
   and README.md.
3. Verify the currently supported ZCode syntax/tool identifiers required
   for Browser/Computer access, scheduled same-session continuation,
   Goal integration, and idle-time execution.
4. Do not assume undocumented APIs.

Implementation constraints:

1. Preserve the existing route names:
   solo / delegate / audit / full.

2. Replace the current one-dimensional risk/escalation semantics with:
   Delegability × Assurance.

3. Define:
   solo     = low delegability + standard assurance
   delegate = high delegability + standard assurance
   audit    = low delegability + high assurance
   full     = high delegability + high assurance.

4. Replace monotonic route escalation with evidence-based
   ROUTE REASSESSMENT.

5. Keep GLM-5.3 as the sole architect and final acceptor.

6. Keep parent diff inspection and verification rerun mandatory.

7. Keep flash-implementer as the standard bounded implementation worker.

8. Add visual-implementer using GLM-5.3-Flash/high.

9. Visual implementer must use a visual/browser feedback loop and must
   fail closed when the required visual capability is unavailable.

10. Do not create a fifth visual route.

11. Add continuity as an orthogonal lifecycle layer:
    foreground / resumable / idle.

12. Add a structured CONTINUITY CHECKPOINT.

13. Resume logic must inspect the current Goal, repository state,
    current diff, and verification state before doing more work.

14. Do not hardcode a five-hour quota reset timestamp.

15. Do not invent quota_remaining, quota_reset, or quota event APIs.

16. Prefer documented ZCode Goal, same-session scheduled tasks,
    and idle-time execution mechanisms.

17. Keep glm-reviewer strictly read-only and fresh-context.

18. Any repair invalidates the previous reviewer verdict.

19. Do not add benchmark infrastructure, cost telemetry,
    cost-aware routing, or learned routing.

20. Make the smallest coherent changes needed to implement this design.

After implementation:

1. Inspect the complete diff.
2. Validate plugin.json and marketplace.json.
3. Validate all agent YAML frontmatter.
4. Search for stale one-dimensional risk/escalation wording.
5. Verify all examples follow the same route semantics.
6. Verify the visual agent references actually available ZCode capabilities.
7. Verify continuity documentation does not claim unsupported quota APIs.
8. Report every modified/new file.
9. Report the validation commands and actual results.
10. Explicitly list any remaining ZCode capability assumptions that could
    not be verified.
```

---

# 40. Project Naming

## Recommended: `glm-conductor`

这是当前最推荐的名称。

`advisor` 表示：

> 给主模型建议。

但当前系统实际负责：

```text
route
  ↓
assign
  ↓
execute
  ↓
verify
  ↓
review
  ↓
resume
```

更接近：

> conductor / orchestrator

而不是 advisor。

### Repository

```text
glm-conductor
```

### Product Name

**GLM Conductor**

### Tagline

> **Selective orchestration for GLM coding agents in ZCode.**

或者更完整：

> **Route, execute, review, and resume GLM coding tasks in ZCode.**

---

# 41. Alternative Names

## `glm-orchestrator`

最准确、最好理解。

优点：

- 一眼知道干什么；
- 搜索友好；
- 不需要解释 metaphor。

缺点：

- 比较普通；
- 品牌辨识度弱。

---

## `glm-relay`

强调：

```text
Architect
   ↓
Implementer
   ↓
Verifier
   ↓
Reviewer
   ↓
Resume
```

特别契合新增 Continuity。

优点：

- 短；
- 好记；
- handoff / continuation 意味很强。

缺点：

- Architecture/routing 含义不如 Conductor 明显。

---

## `zcode-conductor`

如果未来希望项目不绑定具体 GLM generation，这是很好的名称。

例如未来：

```text
GLM-5.x
GLM-Flash-x
new ZCode agents
```

都仍然可以叫：

> ZCode Conductor

但当前实现仍然高度绑定 GLM，所以现在直接叫这个稍微有些过度泛化。

---

## `glm-pilot`

强调：

> steering long-running coding work

简洁、产品化。

但 multi-agent coordination 的表达不如 Conductor。

---

## `glm-flow`

强调 workflow。

比较适合：

- routing；
- lifecycle；
- continuity。

但名字过于通用。

---

# 42. Naming Recommendation

当前阶段推荐：

```text
glm-advisor
      ↓
glm-conductor
```

而不是：

```text
glm-advisor-v2
```

因为这次变化已经不仅是：

> version upgrade

而是项目角色发生变化：

```text
Advisor
   ↓
Orchestration layer
```

同时暂时保留 `glm` 很重要，因为系统仍然明确依赖：

```text
GLM-5.3
+
GLM-5.3-Flash
+
ZCode
```

不应过早把它宣传成 model-agnostic orchestration framework。

如果未来模型版本完全解耦，可以自然演化成：

```text
glm-conductor
      ↓
zcode-conductor
```

而不需要现在就做这一步。