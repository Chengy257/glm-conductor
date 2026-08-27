---
name: orchestration
description: GLM 风险分级选择性路由编排。GLM-5.3 主会话任架构师，在首次调用任务工具前声明 SELECTIVE ROUTE（solo/delegate/audit/full）并给出风险理由；delegate 路由将五段式规格委派给 GLM-5.3-Flash 实施者，audit/full 路由引入全新 GLM-5.3 只读审查者终审。适用于构建并验证功能、多步骤交付、需要委派实施或独立代码审查的任务。
---

# GLM 编排：风险分级选择性路由

## 1. 角色与前置

本技能要求主会话为 GLM-5.3，担任架构师。以下职责保留在主会话，不外派：

- 需求与歧义解决
- 架构与路由选择
- 任务分解
- 五段式规格编写
- diff 检查与验证重跑
- 升级决策
- 验收

若当前主会话模型不是 GLM-5.3，告知用户切换后再继续。

## 2. SELECTIVE ROUTE 声明

在任何 Agent（任务）工具调用之前必须输出一次，格式如下（原样保留代码块）：

```
SELECTIVE ROUTE
mode: solo | delegate | audit | full
risk: <简明的任务级风险理由>
```

- solo 为默认；无明确风险理由时选 solo
- 声明之前不得调用任何任务工具

## 3. 预检（fail-closed）

- 按声明路由核对所需子智能体是否在 Agent 工具的可用类型列表中：`glm-advisor:flash-implementer` / `glm-advisor:glm-reviewer`
- solo 无需预检
- 模型与思考档位已固定在子智能体定义中（GLM-5.3-Flash/high 与 GLM-5.3/max），调用时不得附加模型覆盖
- 任何所需角色缺失、不可用或名称不符时：停止该通道，告知用户检查插件安装（Settings → Plugin Management），不得静默替换为其他子智能体类型

## 4. 四种路由模式

| 模式 | 使用场景 | 执行方式 |
| --- | --- | --- |
| solo（默认） | 风险可控的常规工作；判断密集、高风险或影响面广的实施工作 | 主会话自行规划、实施、测试并自审；不生成辅助代理 |
| delegate | 边界清晰、规格完备的有界工作 | flash-implementer 执行五段式规格，主会话检查完整 diff 并重跑验证；不引入审查者 |
| audit | 主会话自行实施且需独立终审 | 主会话实施 + 验证后，由全新 glm-reviewer 只读审查累积变更集 |
| full（例外） | 明确的宽泛/高风险场景 | flash-implementer 实施，主会话验证，再由全新 glm-reviewer 终审 |

注意：高风险实施留在主会话（solo）是本插件相对 sol-advisor 的关键适配——GLM 体系没有 Terra 高风险通道，旗舰亲自做。

## 5. 规格与报告

- 委派必须使用五段式规格（OBJECTIVE / FILES AND OWNERSHIP / INTERFACES / CONSTRAINTS / VERIFICATION），返回后按 IMPLEMENTATION REPORT 接收
- 完整模板见 references/role-contracts.md（首次委派前必须阅读）
- 工作者的报告仅视为声明：主会话必须亲自检查完整 diff、核对改动范围、重跑验证命令

## 6. 评审与裁决（仅 audit/full）

- reviewer 保持只读，返回 ship / fix-first / rethink 三种裁决之一
- fix-first：按模式修正（audit：主会话修正；full：原实施者修正），重验后换用全新 reviewer
- rethink：修订架构，不得报告完成
- 任何修复使先前裁决失效

## 7. 升级规则

- 路由只能升级，不能静默降级
- 每次升级必须附新观察到的风险证据
- 默认最多一个辅助实现者 +（audit/full 时）一个审查者
- solo/delegate 不得静默添加审查者
