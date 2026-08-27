# 角色契约

本文件是 glm-advisor 编排体系的完整角色契约，SKILL.md 引用本文件获取模板与规则细节。

## 路由声明与总则

SELECTIVE ROUTE 块格式（主会话在任何 Agent 工具调用之前输出一次）：

```
SELECTIVE ROUTE
mode: solo | delegate | audit | full
risk: <简明的任务级风险理由>
```

- **默认上限**：最多一个辅助代理（一个辅助实现者；audit/full 时外加一个审查者）
- **只升不降**：仅允许沿 solo → delegate → full 或 solo → audit → full 方向升级；禁止静默降级
- **fail-closed**：所需角色缺失、不可用或名称不符时停止该通道并告知用户，不得静默替换为其他子智能体类型

## 五段式实施规格模板

委派（delegate/full）给 flash-implementer 时必须使用以下模板，可直接复制：

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

## flash-implementer 契约

- **用途**：仅限声明为 delegate/full 的有界、规格完备工作
- **行为约束**：在既有架构内实施；歧义浮出上报而非自行重构；遵守并行编辑纪律（只在自有文件集内改动，不回退他人无关改动）
- **升级规则**：结果显示任务判断密集、高风险或被误分类时，立即停止并返回升级信号，无需先重试；规格有误时，允许一次修正后重试，且该重试不是升级的前提
- **生成方式**：`subagent_type: glm-advisor:flash-implementer`；模型 GLM-5.3-Flash、思考档位 high 已在子智能体定义中固定，调用时不附加任何模型或思考档位覆盖

## glm-reviewer 契约

- **用途**：仅限 audit/full 路由，且必须在主会话验证之后调用；全新上下文 = 新鲜审查者
- **输入五要素**（由主会话提供，每项内容要求如下）：
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
- **独立性说明**：主会话 GLM-5.3 审查自己体系的产物属于上下文干净（context-clean），并非跨模型家族独立
- **隔离判定按观察而非按请求**：审查者工具为只读白名单即视为隔离已强制；若观察到任何写入尝试或越权行为，终止该通道

## 主会话（架构师）契约

主会话（GLM-5.3）保留以下职责，不外派：

- 需求与歧义解决
- 架构与路由选择
- 五段式规格编写
- 亲自检查完整 diff 并重跑验证命令
- 升级判断
- 验收

辅助工作是替代而非重复主会话的工作：委派实施后主会话不再重复写实现，只做验证与验收；但报告、推理与最终判断始终由主会话负责。
