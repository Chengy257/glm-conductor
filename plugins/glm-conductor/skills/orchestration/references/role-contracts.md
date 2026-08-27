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
| high | standard | delegate | 实施者子智能体 | 否 |
| low | high | audit | GLM-5.3 主会话 | 是 |
| high | high | full | 实施者子智能体 | 是 |

语义要点：

- delegate 不是 solo 的风险升级：solo = GLM-5.3 实施，delegate = GLM-5.3-Flash 实施，delegate 只表达"该实施已足够有界"
- audit = 主会话实施 + 审查者终审（适合判断密集、敏感、架构重的工作）
- full = 委派实施 + 审查者终审（适合大规模但有界：机械 API 迁移、清晰规格的 UI 改版、重复性多文件转换）

### SELECTIVE ROUTE 声明

主会话在任何 Agent 工具调用之前输出一次：

```
SELECTIVE ROUTE
mode: solo | delegate | audit | full
delegability: low | high
assurance: standard | high
executor: main | flash-implementer | visual-implementer
continuity: foreground | resumable | idle
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

- 下调示例：delegate 执行中暴露隐藏的跨模块状态耦合、架构歧义或范围超出 FILES AND OWNERSHIP → delegability high→low、assurance standard→high、mode delegate→audit，主会话接管实施
- 上调示例：solo 调查后 root cause、架构、文件与验证均已确定且剩余工作完全机械 → delegability low→high、mode solo→delegate（前提：主会话尚未重复完成同一实施）
- 实施者返回重估信号属于有效证据
- 审查者给出 rethink 或多项 fix-first 属于有效证据

## 五段式实施规格模板

委派（delegate/full）给实施者时必须使用以下模板，可直接复制：

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
- **VERIFICATION**——精确命令与期望的具体结果/证据
- **RETURN**——附在规格末尾的返回要求："A completion claim without evidence is invalid / 无证据的完成声明无效"

## 视觉任务与 VISUAL ACCEPTANCE 扩展

视觉任务在五段式规格之上追加 VISUAL ACCEPTANCE 节（附在 VERIFICATION 之后，其余五节保持不变）。

**适用视觉通道的任务**：

- React/Vue/Svelte 前端、CSS/布局、响应式设计
- dashboard、浏览器工作流、表单交互
- GUI、Canvas/WebGL、游戏 UI
- 视觉回归、基于截图的验收、用户可见页面改版

**不适用（走 flash-implementer）**：

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

视觉任务是 executor 维度的能力选择，不是第五种 route。

## IMPLEMENTATION REPORT 模板

工作者返回时必须遵循：

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

## 主会话（架构师）契约

主会话（GLM-5.3）保留以下职责，不外派：

- 需求与歧义解决
- 架构与路由判断
- 五段式规格编写
- 亲自检查完整 diff 并重跑验证命令
- 路由重估判断
- 验收

辅助工作是替代而非重复主会话的工作：委派实施后主会话不再重复写实现，只做验证与验收。

**能力边界**：GLM-5.3 是纯文本模型。在视觉链路中主会话只能承担驱动与采集（启动应用、驱动浏览器/桌面、截图落盘），不得声称自己做了视觉判定；视觉判定由 Flash 系多模态角色（visual-implementer / visual-reviewer）完成。

## flash-implementer 契约

- **定位**：标准的有界实施执行者（standard bounded implementation executor）
- **模型**：GLM-5.3-Flash，思考档位 high（已固定，不附加覆盖）
- **用途**：仅限声明为 delegate/full 的有界、规格完备工作——有界代码实现、测试、fixture、重构、配置、CLI、确定性转换、已知 bug 修复、机械迁移
- **行为约束**：在既有架构内实施；歧义浮出上报而非自行重构；遵守并行编辑纪律（只在自有文件集内改动，不回退他人无关改动）
- **重估信号**：结果显示任务判断密集、高风险或被误分类时，立即停止并返回明确的重估信号（ROUTE REASSESSMENT 请求），无需先重试；规格有误时指出精确修正项，允许一次修正后重试，且该重试不是重估的前提
- **生成方式**：`subagent_type: glm-conductor:flash-implementer`

## visual-implementer 契约（视觉任务实施者）

- **定位**：视觉/交互任务的有界实施执行者（visual bounded implementation executor）
- **模型**：GLM-5.3-Flash（多模态），思考档位 high（已固定，不附加覆盖）
- **用途**：仅限声明为 delegate/full 的视觉/交互任务实施——执行含 VISUAL ACCEPTANCE 扩展的五段式规格
- **视觉反馈环（跨调用拓扑）**：实施者需要证据时返回 `VISUAL_CAPTURE_REQUEST` 并结束本次调用（不等待、不虚构）→ 主会话用其专属 Browser/Computer Use 采集截图落盘（主会话专用，子代理在 ZCode 策略层被禁止使用）→ 主会话恢复实施者（resume 保留上下文）或发起携带规格、当前 diff 与截图路径的新调用 → 实施者用 Read 亲自读取截图判定 → 不符合则修正并再次返回 `VISUAL_CAPTURE_REQUEST` → 每个 VISUAL ACCEPTANCE 验收点最多 3 轮采集-判定循环，禁止无限视觉打磨。协议格式见 agents/visual-implementer.md 的 VISUAL_CAPTURE_REQUEST 节
- **重估信号**：验收标准不清、规格有歧义或视觉证据不可得时返回 blocked，交回主会话处理；不得以文字推测替代视觉验证
- **报告**：使用含 FUNCTIONAL VERIFIED 与 VISUAL VERIFIED 两节的 IMPLEMENTATION REPORT 模板（模板见 agents/visual-implementer.md）
- **生成方式**：`subagent_type: glm-conductor:visual-implementer`

## visual-reviewer 契约（视觉任务审查者）

- **定位**：全新上下文、与实施隔离的只读审查者（fresh-context, implementation-isolated reviewer）；与实施者同为 GLM-5.3-Flash，独立性来自干净上下文与只读工具白名单，不宣称跨模型独立
- **模型**：GLM-5.3-Flash（多模态），思考档位 max（已固定）
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

- **裁决失效规则**：任何修复之后原裁决作废，必须换全新审查者复审
- **生成方式**：`subagent_type: glm-conductor:visual-reviewer`

## glm-reviewer 契约（文本任务审查者）

审查者按任务模态选择——文本任务用本角色，视觉任务用 visual-reviewer（见上文）。

- **定位**：全新上下文、与实施隔离的只读审查者（fresh-context, implementation-isolated reviewer）——不宣称跨模型独立
- **模型**：GLM-5.3，思考档位 max（已固定）
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

- **裁决失效规则**：任何修复之后原裁决作废，必须换全新审查者复审
- **隔离判定按观察而非按请求**：审查者工具为只读白名单即视为隔离已强制；若观察到任何写入尝试或越权行为，终止该通道
