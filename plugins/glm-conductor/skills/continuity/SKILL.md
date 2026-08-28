---
name: continuity
description: GLM 长任务连续性编排。与路由正交的生命周期层：foreground / resumable / idle 三种模式；CONTINUITY CHECKPOINT 结构化检查点；恢复时先检查仓库真实状态（repository > checkpoint）；定时唤醒与闲时执行优先使用 ZCode 原生能力。适用于长时间编码任务、可能跨会话或跨额度窗口中断的工作、可无人值守的批量任务。
---

# GLM 连续性：长任务生命周期

## Purpose

一次任务的路由（solo / delegate / audit / full）由 orchestration 技能决定；continuity 只回答一个问题——任务如果很长，如何持续执行。两者组合而非替代：orchestration 决定"谁实施、如何验证、是否需要审查"，continuity 决定"未完成的工作如何恢复"。

continuity 是与路由正交的生命周期维度，不是第五种 route。

## Modes

| 模式 | 适用 | 行为 |
| --- | --- | --- |
| foreground（默认） | 普通任务、当前会话内可完成、需用户实时互动 | 正常执行，不创建任何 continuation |
| resumable | 长前台任务、可能跨额度窗口/因可用性中断、仍希望延续当前目标 | 里程碑后写 checkpoint + 安排同目标定时唤醒，唤醒后先检查再恢复 |
| idle | 非紧急长任务、可无人值守、验证可自动完成 | 优先交给 ZCode 原生闲时任务执行 |

- **foreground** 是默认值：不创建任何 continuation，任务随会话自然开始与结束
- **resumable** 仍在前台工作，但为中断做好准备：checkpoint 是恢复的依据，定时唤醒是恢复的触发器
- **idle** 把整段工作交给 ZCode 原生闲时任务，主会话不占用前台交互

## Selection

按以下判据选择模式：

- 预计当前会话内完成 → foreground
- 预计会中断但需要延续当前目标 → resumable
- 不紧急且无人值守可行 → idle

推荐优先级：普通任务用 foreground；长交互任务用 resumable（定位是安全的前台会话连续性，不是额度绕过）；无人值守可接受的长任务优先 idle。

continuity 字段随 SELECTIVE ROUTE 声明携带（见 orchestration 技能），可随任务进展变更；变更同样需要证据（例如观察到额度窗口耗尽），不得凭直觉切换。

## Checkpoint

resumable / idle 任务在实质性里程碑后（不是每次工具调用后）写入 CONTINUITY CHECKPOINT 到任务专属路径 `.glm-conductor/tasks/<continuity-id>/checkpoint.md`；CONTINUITY_ID 规则与完整模板见 references/long-horizon.md。

原则：

- checkpoint 是导航状态，不是仓库真相源——不复制完整 diff、不复制大量代码、不声称未验证内容
- repository 状态始终优先：checkpoint 与仓库冲突时以仓库为准
- 每个长任务一个机械唯一（语义前缀+随机后缀）的 CONTINUITY_ID 与专属目录；并行长任务互不覆盖、互不删除

## Runtime State 与 Git

`.glm-conductor/` 是本地运行时状态，不得侵入用户受版本控制的仓库内容：

1. 不为运行时状态自动修改 tracked `.gitignore`
2. 在 Git 仓库中优先将 `.glm-conductor/` 写入本地排除文件 `.git/info/exclude`
3. 无安全排除机制可用时（如非 Git 工作区），明确告知用户该目录的存在与位置
4. 运行时文件不得静默污染 `git diff`

## Resume Procedure

每次重新激活后必须依次执行八步：

检查目标 → 按 CONTINUITY_ID 读取 checkpoint → 检查仓库状态 → 检查当前 diff → 判断先前变更是否仍在 → 判断目标是否已完成 → 检查验证状态 → 从 NEXT ACTION 恢复

禁止盲目重播旧指令。若 checkpoint 与仓库不一致：repository > checkpoint——分析变化来源后更新认知，不得为恢复 checkpoint 而回滚仓库新改动。恢复执行前必须重新输出 SELECTIVE ROUTE 声明（沿用或基于新证据重估）。

详细步骤与禁令见 references/long-horizon.md。

## Scheduled Resume

resumable 模式的唤醒不用 sleep 直到固定时间，而用安全周期性再激活：

唤醒 → 检查目标是否已完成（完成即停止并清理）→ 检查执行是否可用（可用则恢复；不可用则不触碰仓库，等下次触发）

调度触发本身就是存活探针：唤醒成功启动即说明模型执行当前可用；唤醒失败或未启动则不会产生任何仓库改动，自然等待下次触发。不需要、也不引入独立的额度检查器。

安排定时任务时使用 references/long-horizon.md 中的结构化 resume prompt（自包含，不依赖会话上下文，且必须携带 CONTINUITY_ID 与精确 checkpoint 路径）。若希望结果回到当前会话，续作必须从当前聊天内创建绑定本会话的定时任务。

## Idle Execution

idle 模式优先使用 ZCode 原生闲时任务（支持配置了自定义模型的子智能体，可直接让 flash-implementer / visual-implementer 在闲时执行段落工作）。

不自行实现 daemon、while-loop 监督者、OS cron 包装器或额度轮询服务。

## Goal Integration

continuity 不重新实现 Goal 模式。职责分工：

- **Goal 模式**回答"总体目标是否完成"
- **orchestration** 回答"谁实施、如何验证、是否需要审查"
- **continuity** 回答"未完成的工作如何恢复"

三者组合使用。

## Failure Handling

- **定时/闲时能力不可用**：不得声称已启用连续性；保留 checkpoint 并向用户报告"手动可恢复"及恢复方法
- **所需 executor（如 visual-implementer）缺失**：fail-closed，不自动换成其他执行者
- **唤醒后发现目标已完成**：立即停止 continuation 并清理（见下）

## Quota API Boundary

本技能不假设存在任何 quota API——getQuotaRemaining / getQuotaResetTime / onQuotaReset 均为虚构。不硬编码 5 小时重置或周期额度假设，不宣传能实时读取额度。

额度相关的观察只能来自用户告知或 UI 截图，且只作为证据使用、不作为 API。

## Completion Cleanup

目标完成并验收后，只清理本任务的状态：

1. 删除本任务目录 `.glm-conductor/tasks/<continuity-id>/`（仅此目录，不得触碰其他任务的 checkpoint 或视觉证据）
2. 停止并移除与该 CONTINUITY_ID 关联的定时任务
3. 终止该任务的闲时任务排队

避免幽灵唤醒重复执行；并行任务下删除全局或他人状态是禁止操作。
