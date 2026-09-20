# 角色契约

本文件是 glm-conductor 编排体系的完整角色契约，SKILL.md 引用本文件获取判据与模板细节。

## 双轴路由契约

### 轴 A — Delegability（可委派性）

判断问题：剩余的实施工作是否足够有界、规格足够完备，可以委派出去？

**delegability: high** —— 通常满足：

- objective 与 observable outcome 明确
- 目标文件与 ownership 边界明确
- interface 明确
- constraints 明确
- verification 明确
- 架构已决定，剩余任务主要是实施
- 实施过程中不需要频繁重新设计

典型任务：已确定接口的函数实现、测试/fixture 补充、重复性重构、CLI 接线、配置管道、序列化、已知 root cause 的 bug fix、明确规则的大量代码迁移。

**delegability: low** —— 出现以下情况时优先判断为 low：

- 架构尚未确定
- root cause 未知
- 需求存在实质歧义
- 大量跨模块判断
- 实施本身就是主要的推理问题
- 并发/分布式状态等隐藏复杂度
- 敏感决策尚未解决
- 实施过程中可能频繁改变设计

delegability: low 时由 GLM-5.3 主会话实施。

### 轴 B — Assurance（保障等级）

判断问题：实施完成并通过主会话验证后，一次全新上下文的独立终审是否有实质价值？

**assurance: standard** —— 适用于：影响面有限、验证足够明确、回归风险可控的普通实施。

**assurance: high** —— 以下情况优先使用：

- 宽影响面（wide blast radius）
- 高回归风险
- 认证 / 权限
- 破坏性行为
- 数据迁移
- 跨模块语义变更
- 验证难以覆盖所有重要失败模式
- 大规模用户可见变更
- 用户明确要求严格审查

### 路由矩阵

| Delegability | Assurance | Route | 实施 | 独立审查 |
| --- | --- | --- | --- | --- |
| low | standard | solo | GLM-5.3 主会话 | 否 |
| high | standard | delegate | 原生 Workflow（文本工人） | 否 |
| low | high | audit | GLM-5.3 主会话 | 是 |
| high | high | full | 原生 Workflow（文本工人） | 是 |

语义要点：

- delegate 不是 solo 的风险升级：solo = GLM-5.3 实施，delegate = 原生 Workflow 文本工人实施，delegate 只表达"该实施已足够有界"
- audit = 主会话实施 + 审查者终审（适合判断密集、敏感、架构重的工作）
- full = 委派实施 + 审查者终审（适合大规模但有界：机械 API 迁移、清晰规格的 UI 改版、重复性多文件转换）
- 视觉/交互任务是实施模态的一种能力选择（走 visual-implementer 子智能体通道），不是第五种 route

### SELECTIVE ROUTE 声明

主会话在任何实施委派之前输出一次（进入 state 的只有 mode 与 assurance 两键）：

```
SELECTIVE ROUTE
mode: solo | delegate | audit | full
delegability: low | high
assurance: standard | high
reason: <简明的、基于证据的理由>
```

### ROUTE REASSESSMENT

路由变化必须来自新观察到的证据，可双向重估，不得凭直觉或为省事变更：

```
ROUTE REASSESSMENT

delegability: <old> -> <new>
assurance: <old> -> <new>
mode: <old> -> <new>

evidence:
<新观察到的证据>
```

- 下调示例：delegate 执行中节点结果频繁返回 reassessment、暴露隐藏的跨模块状态耦合、架构歧义或范围超出 ownership → delegability high→low、assurance standard→high、mode delegate→audit，主会话接管实施
- 上调示例：solo 调查后 root cause、架构、文件与验证均已确定且剩余工作完全机械 → delegability low→high、mode solo→delegate（前提：主会话尚未重复完成同一实施）
- 实施结果里的 reassessment 重估请求属于有效证据
- 审查者给出 rethink 或多项 fix-first 属于有效证据

## 五段式实施规格（节点声明的语义框架）

五段式规格是实施声明的思维框架：静态 DAG 节点的必填/可选字段即它的落点（objective ↔ OBJECTIVE、ownership ↔ FILES AND OWNERSHIP、interfaces ↔ INTERFACES、constraints ↔ CONSTRAINTS、local_check ↔ VERIFICATION）；视觉通道的子智能体交付仍直接使用本模板原文。模板可直接复制：

```
OBJECTIVE:
<可观察的结果及其意义>

FILES AND OWNERSHIP:
- <仅列实施者自有文件>
（注明：代码库可能被并发编辑，不得回退他人改动或越界修改）

INTERFACES:
<须保持兼容的签名、类型、模式、命令>

CONSTRAINTS:
<仓库规范、安全边界、排除范围、已定决策>

VERIFICATION:
<精确命令与期望的具体结果/证据>

RETURN:
A completion claim without evidence is invalid / 无证据的完成声明无效
```

各节用途：

- **OBJECTIVE**——可观察的结果及其意义
- **FILES AND OWNERSHIP**——仅列实施者自有文件；注明代码库可能被并发编辑、不得回退他人改动或越界修改
- **INTERFACES**——须保持兼容的签名、类型、模式、命令
- **CONSTRAINTS**——仓库规范、安全边界、排除范围、已定决策
- **VERIFICATION**——精确命令与期望的具体结果/证据（节点形态下即 local_check）
- **RETURN**——附在规格末尾的返回要求："A completion claim without evidence is invalid / 无证据的完成声明无效"

节点编制纪律（DAG 侧约束见 SKILL.md §5）：每个节点必须仍然小到能接收一个完整的有界 objective——需要再拆说明分解不足。

## TASK CONTEXT PACK（实施声明的前置上下文包）

复杂或陌生代码域的委派，在实施声明**之前**准备一个上下文包——主会话用昂贵的推理与侦察压缩出的有界上下文，交给实施方做有界执行（这正是异构分工的价值点：GLM-5.3 压缩、执行方执行）。做过 ROUTING PREFLIGHT 时，包内条目直接取自 PREFLIGHT REPORT，不重复侦察。

模板（附在实施声明最前，标题行逐字）：

```
TASK CONTEXT PACK

TASK:
<一句话任务定位>

ROOT CAUSE:
<为什么要改——实际观察到的根因，非猜测>

RELEVANT FILES:
- <文件路径（+ 关键行号/符号）>

KEY SYMBOLS:
- <函数/类/常量及其职责>

CALL / DATA FLOW:
<改动的数据或调用如何流动，两三句>

INTERFACES:
<须保持兼容的契约>

OWNERSHIP:
<本包授权的实施范围（与 FILES AND OWNERSHIP 一致）>

TEST ENTRYPOINTS:
- <验证入口命令>

KNOWN RISKS:
- <已识别的坑（并发编辑点/平台差异/隐式依赖）>

EXCLUDED AREAS:
- <明确不碰的区域>
```

规则：

- **证据化、紧凑**：条目全部来自主会话实际观察（文件/符号/调用链），不把推测性事实当已定约束写入；不倾倒整个仓库、不整段复制大文件、不重复粘贴完整 diff——引用路径+行号胜过贴代码
- 有界：通常 15-40 行；超过说明任务分解不足，先拆节点再委派
- 简单任务（边界清晰、上下文自明）可省略整个包，直接实施声明

## 视觉任务与 VISUAL ACCEPTANCE 扩展

视觉任务在五段式规格之上追加 VISUAL ACCEPTANCE 节（附在 VERIFICATION 之后，其余五节保持不变）。

**适用视觉通道的任务**：

- React/Vue/Svelte 前端、CSS/布局、响应式设计
- dashboard、浏览器工作流、表单交互
- GUI、Canvas/WebGL、游戏 UI
- 视觉回归、基于截图的验收、用户可见页面改版

**不适用（走原生 Workflow 文本通道）**：

- 普通后端函数、CLI、解析器、纯数据转换、无视觉验收的服务端代码

VISUAL ACCEPTANCE 模板（可直接复制）：

```
VISUAL ACCEPTANCE:
- target page / component: <目标页面或组件>
- viewport: <桌面 1440×900 / 移动 390×844 等>
- required interaction: <须执行的交互>
- expected visible state: <期望的可见状态>
- responsive behavior: <响应式要求>
- clipping / overflow constraints: <裁切与溢出约束>
- important user-visible content: <重要用户可见内容>
```

示例：

```
VISUAL ACCEPTANCE:
- target page / component: 设置页的 API Key 表单
- viewport: 桌面 1440×900
- required interaction: 输入非法 Key 并点击保存
- expected visible state: 输入框下方出现红色错误提示，页面不跳动
- responsive behavior: 390×844 下表单单列堆叠，按钮保持可点击
- clipping / overflow constraints: 错误提示完整可见，无横向滚动条
- important user-visible content: 错误文案说明原因与修复方式
```

## IMPLEMENTATION REPORT 模板（子智能体通道）

视觉通道（visual-implementer）返回时必须遵循；普通实施结果的结构化契约见下节 NodeResult：

```
IMPLEMENTATION REPORT
STATUS: complete | partial | blocked
OBJECTIVE: <一句话复述目标>
CHANGES:
- <逐文件列出改动>
VERIFIED:
- <命令>: <实际输出>
JUDGMENT CALLS:
- <实施中的判断决策>（无则写"无"）
GAPS:
- <未覆盖项>（无则写"无"）
```

- STATUS 取值：complete / partial / blocked
- VERIFIED 必须是命令与实际输出的配对，不接受空泛描述
- 报告只是声明（implementation claim）；证据只存在于主会话亲自观察到的 diff 与命令输出中

## 文本工人契约（原生 Workflow 实施）

delegate/full 的文本实施由原生 Workflow 运行的文本工人（text worker）执行：

- **来源**：persona 由 `runtime/workflow/persona.py` 唯一定义，编译器（v24-compile）把它逐字内嵌进生成的 Workflow 源码——宿主 runtime 不消费插件 agent 定义，角色仿真靠脚本内嵌 persona 文本
- **定位**：有界 objective 的精确执行者——只执行所交付的 objective，不重新设计架构；objective 之外的架构决策、范围扩张与顺手重构一律不做
- **范围纪律**：只改动节点 ownership 覆盖的仓库相对路径；遵守声明的 interfaces 与 constraints；同一仓库可能有其他代理并行编辑——不回退他人的无关改动
- **歧义处理**：遇到实质性歧义、范围冲突或规格缺漏时不擅自决策——在结果的 `reassessment` 字段返回明确的重估请求，交回主会话裁决
- **local_check 诚实执行**：非空时逐项执行并如实记录结果与失败；为空时不虚构任何检查；不得伪造输出，不得省略失败
- **结果契约（NodeResult，恰六字段）**：`node_id` / `status`（complete | partial | blocked）/ `changes` / `local_checks` / `reassessment` / `gaps`——无证据的完成声明无效；status 与实际证据不符的结果在主会话验证阶段被拒
- **主会话侧含义**：文本工人没有可调用的子智能体定义，也没有任何派发凭据或标记机制——对它的全部约束都来自节点声明（内嵌进 ask 指令）与完成守卫的事后校验

## visual-implementer 契约（视觉任务实施者）

- **定位**：视觉/交互任务的有界实施执行者（visual bounded implementation executor）
- **模型**：GLM-5.3-Flash（多模态），思考档位 high（已固定，不附加覆盖）
- **用途**：仅限视觉/交互任务实施——执行含 VISUAL ACCEPTANCE 扩展的五段式规格
- **视觉反馈环（跨调用拓扑）**：实施者需要证据时返回 `VISUAL_CAPTURE_REQUEST` 并结束本次调用（不等待、不虚构）→ 主会话用其专属 Browser/Computer Use 采集截图落盘（主会话专用，子代理在 ZCode 策略层被禁止使用）→ 主会话发起携带完整状态的新调用（规范路径：五段式规格含 VISUAL ACCEPTANCE、当前 diff / 改动状态、上一轮 VISUAL_CAPTURE_REQUEST、截图路径、VISUAL_ROUND 轮次号；仅当运行时已验证稳定 resume 机制时才可作为可选优化，协议正确性不依赖 resume） → 实施者用 Read 亲自读取截图判定 → 不符合则修正并再次返回 `VISUAL_CAPTURE_REQUEST` → 每个 VISUAL ACCEPTANCE 验收点以 VISUAL_ROUND（1|2|3）跟踪，最多 3 轮采集-判定循环，禁止无限视觉打磨。协议格式见 agents/visual-implementer.md 的 VISUAL_CAPTURE_REQUEST 节
- **重估信号**：验收标准不清、规格有歧义或视觉证据不可得时返回 blocked，交回主会话处理；不得以文字推测替代视觉验证
- **报告**：使用含 FUNCTIONAL VERIFIED 与 VISUAL VERIFIED 两节的 IMPLEMENTATION REPORT 模板（模板见 agents/visual-implementer.md）
- **生成方式**：`subagent_type: glm-conductor:visual-implementer`

## visual-reviewer 契约（视觉任务审查者）

- **定位**：全新上下文、与实施隔离的只读审查者（fresh-context, implementation-isolated reviewer）；独立性来自干净上下文与只读工具白名单，不宣称跨模型独立（模型继承宿主/会话，诊断时可记录实际运行模型，但不是持久任务状态轴）
- **思考档位**：max（已固定）
- **用途**：仅限视觉任务的 audit/full 路由，且必须在主会话验证之后调用；主职是独立视觉验收（截图证据 + VISUAL ACCEPTANCE + 用户可见回归），代码 diff 作为上下文读取，不替代主会话的代码审查
- **职责分工**：主会话负责完整 diff、代码正确性、功能验证与范围/接口核验；visual-reviewer 负责视觉验收、交互证据、截图证据与用户可见回归
- **输入六要素**（由主会话提供，缺项要求补齐而非猜测）：
  - ROLE：声明本次为只读审查
  - STATED GOAL：用户的原始目标原文
  - ACCUMULATED CHANGE SET：允许文件清单 + 完整 diff，或基准/目标修订
  - INTERFACES AND CONSTRAINTS：须保持兼容的接口与约束
  - VERIFICATION EVIDENCE：验证命令映射到主会话实际输出的证据
  - VISUAL EVIDENCE：截图文件路径清单 + 对应的 VISUAL ACCEPTANCE 标准
- **审查范围**：以视觉符合度为主线（必须亲自 Read 每一张截图，对照 VISUAL ACCEPTANCE 判定实施者的视觉结论是否成立），外加用户可见回归与证据链完整性；正确性、完整性、回归风险、范围纪律、接口保留、测试充分性为附带核对（可报告，不替代主会话代码审查）
- **VISUAL REVIEW 输出格式**：

  ```
  VISUAL REVIEW
  VERDICT: ship | fix-first | rethink
  REASON: <基于视觉证据的决定性理由>
  FINDINGS:
  - <截图路径 / VISUAL ACCEPTANCE 验收项>: <发现>（无则写"无"）
  RESIDUAL VISUAL RISK: <最重要的剩余视觉风险>（无则写"无"）
  ```

- **裁决失效规则**：任何修复之后原裁决作废，必须换全新审查者复审；裁决经 CLI `review-record` 落账（change_id 绑定，先验证后评审）
- **生成方式**：`subagent_type: glm-conductor:visual-reviewer`

## glm-reviewer 契约（文本任务审查者）

审查者按任务模态选择——文本任务用本角色，视觉任务用 visual-reviewer（见上文）。

- **定位**：全新上下文、与实施隔离的只读审查者（fresh-context, implementation-isolated reviewer）——不宣称跨模型独立；模型继承宿主/会话（frontmatter 无 model 字段），思考档位 max（已固定）
- **用途**：仅限文本任务的 audit/full 路由，且必须在主会话验证之后调用；全新上下文 = 新鲜审查者
- **输入五要素**（由主会话提供）：
  - ROLE：声明本次为只读审查
  - STATED GOAL：用户的原始目标原文，不得转写篡改
  - ACCUMULATED CHANGE SET：允许文件清单 + 完整 diff，或基准/目标修订
  - INTERFACES AND CONSTRAINTS：须保持兼容的签名、类型、模式、命令与约束
  - VERIFICATION EVIDENCE：验证命令映射到主会话实际输出的证据
- **审查范围七项**：正确性、完整性、回归风险、范围纪律（是否越界改动）、接口保留、测试充分性、实质风险
- **GLM REVIEW 输出格式**：

  ```
  GLM REVIEW
  VERDICT: ship | fix-first | rethink
  REASON: <基于证据的决定性理由>
  FINDINGS:
  - <文件:行号> <发现>（无则写"无"）
  RESIDUAL RISK: <最重要的剩余风险>（无则写"无"）
  ```

- **裁决失效规则**：任何修复之后原裁决作废，必须换全新审查者复审；裁决经 CLI `review-record` 落账（change_id 绑定，先验证后评审）
- **隔离判定按观察而非按请求**：审查者工具为只读白名单即视为隔离已强制；若观察到任何写入尝试或越权行为，终止该通道

## 主会话（架构师）契约

主会话（GLM-5.3）保留以下职责，不外派：

- 需求与歧义解决
- 架构与路由判断
- 静态 DAG 编制与实施声明编写
- 亲自检查完整 diff 并重跑验证命令
- 路由重估判断
- 验收与完成申报

辅助工作是替代而非重复主会话的工作：委派实施后主会话不再重复写实现，只做验证与验收。

**能力边界**：GLM-5.3 是纯文本模型。在视觉链路中主会话只能承担驱动与采集（启动应用、驱动浏览器/桌面、截图落盘），不得声称自己做了视觉判定；视觉判定由 Flash 系多模态角色（visual-implementer / visual-reviewer）完成。
