# 长周期任务运行机制

本文件是 continuity 技能的运行机制文档，SKILL.md 引用本文件获取生命周期、checkpoint 模板与恢复流程细节。

## Lifecycle

```
任务开始（orchestration 声明 continuity 字段）
   |
   v
[foreground] 正常执行 ---------> 完成 -> 清理
   |
   v（任务变长/可能中断 -> 改 resumable/idle，需证据）
[checkpoint 循环]
   执行到实质性里程碑 -> 写 CONTINUITY CHECKPOINT -> 继续执行
   |
   v（中断：会话结束/额度窗口耗尽/用户暂停）
[再激活]（定时唤醒 / 闲时任务 / 用户手动恢复）
   |
   v
八步恢复检查（repository > checkpoint）
   |
   +-- 目标已完成 -> 清理（删 checkpoint、停任务）
   +-- 执行可用 -> 重新声明路由 -> 从 NEXT ACTION 继续
   +-- 执行不可用 -> 不触碰仓库，等待下次触发
```

## CONTINUITY CHECKPOINT 模板

resumable / idle 任务在每个实质性里程碑后（不是每次工具调用后）写入用户工作区根的 `.glm-conductor/checkpoint.md`；同一文件覆盖写，保证任意时刻只有一个最新 checkpoint。模板可直接复制：

```
CONTINUITY CHECKPOINT

GOAL:
<原始目标>

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

checkpoint 原则清单：

- **导航状态非真相源**：checkpoint 只记录"做到哪、下一步做什么"，不替代仓库
- **不复制完整 diff、不复制大量代码**：diff 与代码的真相比对对象是仓库本身
- **不声称未验证内容**：VERIFICATION.completed 之外的项不得写成已完成
- **repository 优先**：checkpoint 与仓库状态冲突时，以仓库为准
- **每个实质性里程碑更新一次即可**：不是每次工具调用后都写

## Resume Procedure（八步）

每次重新激活后必须依次执行：

1. **检查当前 Goal**：确认当前会话目标是否仍是同一目标
2. **读最新 checkpoint**：读取 `.glm-conductor/checkpoint.md`；不存在时按无 checkpoint 处理，只能从目标与仓库现状重建认知
3. **检查仓库状态**：仓库真实状态优先于 checkpoint（repository > checkpoint）
4. **检查当前 diff**：确认工作区当前实际有哪些改动
5. **判断先前变更是否仍在**：checkpoint 声称已完成的改动，是否能在 diff 与仓库中观察到
6. **判断目标是否已完成**：已完成则停止实施，进入 Completion Cleanup
7. **检查最新验证状态**：VERIFICATION.completed 是否仍可复现、pending 还剩哪些
8. **从 NEXT ACTION 恢复**：重新输出 SELECTIVE ROUTE 声明后，从 NEXT ACTION 继续执行

两条禁令：

- 禁止唤醒后盲目重播旧指令
- 禁止为恢复 checkpoint 回滚仓库新改动——checkpoint 与仓库不一致时，分析变化来源、更新认知、以仓库为准

## 结构化 Resume Prompt（定时任务用）

以下 prompt 用作 ZCode 定时任务/闲时任务的触发内容。它必须自包含：不依赖任何会话上下文，唤醒后的会话（可能是全新上下文）凭此 prompt + checkpoint + 仓库状态即可恢复。

```
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

## Scheduled Trigger

映射到 ZCode 定时任务：

- **触发内容** = 上述结构化 resume prompt
- **触发节奏** = 安全周期性再激活，例如每 30-60 分钟检查一次，而非 sleep 5 小时

不依赖固定重置时间：额度政策可能变化，硬编码任何重置周期都会在政策变化后静默失效；周期性再激活只依赖"唤醒时重新检查"这一动作，与具体政策解耦。

每次唤醒的任务量应小——做一轮检查，然后恢复或继续一段工作——避免单次唤醒塞满全部剩余工作。

## Idle Task

ZCode 闲时任务支持配置了自定义模型的子智能体。无人值守的段落工作可直接指派：

- 标准有界实施 → `flash-implementer`
- 视觉/交互实施 → `visual-implementer`

主会话负责在闲时段落结束后做父级验证——闲时段落的 IMPLEMENTATION REPORT 仍只是声明，证据只来自主会话亲自观察的 diff 与命令输出。同目标的多段闲时执行之间仍靠 checkpoint 衔接。

## Route Recovery

恢复的会话必须重新输出 SELECTIVE ROUTE 声明后再执行（详细规则见 orchestration 技能的"恢复会话的路由恢复"章节）；若证据显示双轴判断（Delegability × Assurance）已变化，先做 ROUTE REASSESSMENT。

## Completion Cleanup

目标完成并验收后的动作清单：

1. 删除 `.glm-conductor/checkpoint.md`
2. 移除相关定时任务
3. 终止闲时任务排队
4. 向用户报告最终状态

## Failure Cases

- **automation 不可用**（定时/闲时能力均不可用）：不得声称已启用连续性；保留 checkpoint，向用户报告手动可恢复，并附恢复方法——新建会话输入"读取 .glm-conductor/checkpoint.md 并按八步恢复流程继续"
- **executor 缺失**（如 visual-implementer）：fail-closed，不自动换成其他执行者，交回用户处理
- **唤醒后 checkpoint 与仓库严重不一致**：以仓库为准，向用户报告差异后继续；不得回滚仓库新改动

## ZCode Limitations

- **无 quota API**：getQuotaRemaining / getQuotaResetTime / onQuotaReset 均不存在，不得虚构任何额度查询接口
- **额度观察只能来自用户或 UI**：只作为证据使用，不作为 API
- **子智能体内不能再派生子智能体**：continuity 的执行者只能是主会话直接调用的子智能体
- **子智能体只能看到会话启动时已连接的 MCP 服务**：跨会话恢复后需重新确认所需服务可用
