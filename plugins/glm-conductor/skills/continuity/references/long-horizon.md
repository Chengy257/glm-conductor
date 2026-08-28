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
   +-- 目标已完成 -> 清理（删本任务目录、停任务）
   +-- 执行可用 -> 重新声明路由 -> 从 NEXT ACTION 继续
   +-- 执行不可用 -> 不触碰仓库，等待下次触发
```

## CONTINUITY CHECKPOINT 模板

resumable / idle 任务在每个实质性里程碑后（不是每次工具调用后）写入任务专属路径 `.glm-conductor/tasks/<task-id>/checkpoint.md`。同一任务的多次写入覆盖本任务自己的文件；不同任务各用各的目录，互不覆盖、互不删除。模板可直接复制：

```
CONTINUITY CHECKPOINT

TASK_ID:
<语义前缀>-<随机十六进制后缀>（如 redesign-settings-page-7f3a2c；生成契约见下）

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

若 ZCode 运行时实际暴露了以下标识，可选择性附加记录；不暴露就不写，**不得虚构**：

```
SESSION_ID:
<会话标识>

AUTOMATION_ID:
<定时任务标识>

IDLE_TASK_ID:
<闲时任务标识>
```

### TASK_ID 规则

格式——生成即机械唯一，不依赖命名约定：

```text
<语义前缀>-<6~8 位随机十六进制后缀>
```

示例：`redesign-settings-page-7f3a2c`、`parser-refactor-a92c1d`、`migrate-auth-20260828-231502`（时间戳后缀可接受，随机后缀优先——并行同时创建的任务也不碰撞）。

生成契约：

1. 任务首次进入 resumable / idle 时生成一次
2. 随 checkpoint 持久化
3. 恢复期间绝不重新生成
4. checkpoint 路径、视觉证据路径、定时 resume prompt、automation 关联、完成清理，全部使用同一精确 ID
5. 在当前工作区内必须唯一——语义前缀人类可读是可选收益，唯一性是强制要求
6. 生成后跨恢复保持稳定（写入 checkpoint 后不得再变）

查找与隔离纪律：

- 定时/闲时的 resume 指令必须携带精确 ID
- 恢复与清理都按它定位 `.glm-conductor/tasks/<id>/` 目录
- 视觉证据目录也按它隔离（见 orchestration 技能的视觉通道运行细节）
- 不以"最新 checkpoint"作为查找策略——并行任务下"最新"是歧义的
- 读取 v1.x 遗留 checkpoint 时，旧字段名 `CONTINUITY_ID` 视同 `TASK_ID`（读取时归一化，不要求重写已存在的 checkpoint 文件）

checkpoint 原则清单：

- **导航状态非真相源**：checkpoint 只记录"做到哪、下一步做什么"，不替代仓库
- **不复制完整 diff、不复制大量代码**：diff 与代码的真相比对对象是仓库本身
- **不声称未验证内容**：VERIFICATION.completed 之外的项不得写成已完成
- **repository 优先**：checkpoint 与仓库状态冲突时，以仓库为准
- **每个实质性里程碑更新一次即可**：不是每次工具调用后都写

## Resume Procedure（八步）

每次重新激活后必须依次执行：

1. **检查当前 Goal**：确认当前会话目标是否仍是同一目标
2. **读取本任务 checkpoint**：读取 resume 指令携带的 `.glm-conductor/tasks/<task-id>/checkpoint.md`；指令未携带 ID 时，按目标语义在 `.glm-conductor/tasks/` 下定位匹配目录；不存在时按无 checkpoint 处理，只能从目标与仓库现状重建认知
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

以下 prompt 用作 ZCode 定时任务/闲时任务的触发内容。它必须自包含：不依赖任何会话上下文，唤醒后的会话（可能是全新上下文）凭此 prompt + 指定 checkpoint + 仓库状态即可恢复。**占位符必须在创建定时任务时替换为真实值**：

```
Resume GLM Conductor task:

TASK_ID: <id>
CHECKPOINT: .glm-conductor/tasks/<id>/checkpoint.md

Inspect the current goal, the specified checkpoint, and the current
repository state.

If the goal represented by this checkpoint is already satisfied,
perform no further implementation and clean up only the runtime state
associated with this TASK_ID.

If incomplete, inspect the current diff and verification state before
resuming from NEXT ACTION.

Do not redo completed work.

Repository state is authoritative over checkpoint state.

Continue under the stored route, executor, ownership, verification,
and review contracts.

Perform ROUTE REASSESSMENT only when newly observed evidence changes
delegability or assurance.
```

## Scheduled Trigger

映射到 ZCode 定时任务：

- **触发内容** = 上述结构化 resume prompt（已替换 TASK_ID 与 checkpoint 路径）
- **触发节奏** = 安全周期性再激活，例如每 30-60 分钟检查一次，而非 sleep 5 小时

**调度触发即存活探针**：唤醒成功启动即说明模型执行当前可用，直接进入恢复流程；唤醒失败或未启动则不会产生任何仓库改动，自然等待下次触发。不实现独立的额度检查器——调度机制本身完成了探测。

**同会话投递要求**：若希望结果回到当前会话，必须从当前聊天/会话内创建绑定本会话的定时续作；从通用入口创建的分离 automation 不会把结果送回当前会话，两者不等价。跨会话自包含恢复（全新上下文 + 指定 checkpoint）始终可用，适用于不需要同会话投递的场景。

不依赖固定重置时间：额度政策可能变化，硬编码任何重置周期都会在政策变化后静默失效；周期性再激活只依赖"唤醒时重新检查"这一动作，与具体政策解耦。

每次唤醒的任务量应小——做一轮检查，然后恢复或继续一段工作——避免单次唤醒塞满全部剩余工作。

## Idle Task

ZCode 闲时任务支持配置了自定义模型的子智能体。无人值守的段落工作可直接指派：

- 标准有界实施 → `flash-implementer`
- 视觉/交互实施 → `visual-implementer`

主会话负责在闲时段落结束后做父级验证——闲时段落的 IMPLEMENTATION REPORT 仍只是声明，证据只来自主会话亲自观察的 diff 与命令输出。同目标的多段闲时执行之间靠任务专属 checkpoint 衔接（同一 TASK_ID）。

## Route Recovery

恢复的会话必须重新输出 SELECTIVE ROUTE 声明后再执行（详细规则见 orchestration 技能的"恢复会话的路由恢复"章节）；若证据显示双轴判断（Delegability × Assurance）已变化，先做 ROUTE REASSESSMENT。

## Completion Cleanup

目标完成并验收后的动作清单（只作用于本任务）：

1. 删除本任务目录 `.glm-conductor/tasks/<task-id>/`（仅此目录，不得触碰其他任务的状态）
2. 移除与该 TASK_ID 关联的定时任务
3. 终止该任务的闲时任务排队
4. 向用户报告最终状态

## Failure Cases

- **automation 不可用**（定时/闲时能力均不可用）：不得声称已启用连续性；保留本任务 checkpoint 目录，向用户报告手动可恢复，并附恢复方法——新建会话输入"读取 .glm-conductor/tasks/<task-id>/checkpoint.md 并按八步恢复流程继续"
- **executor 缺失**（如 visual-implementer）：fail-closed，不自动换成其他执行者，交回用户处理
- **唤醒后 checkpoint 与仓库严重不一致**：以仓库为准，向用户报告差异后继续；不得回滚仓库新改动

## ZCode Limitations

- **无 quota API**：getQuotaRemaining / getQuotaResetTime / onQuotaReset 均不存在，不得虚构任何额度查询接口
- **额度观察只能来自用户或 UI**：只作为证据使用，不作为 API
- **子智能体内不能再派生子智能体**：continuity 的执行者只能是主会话直接调用的子智能体
- **子智能体只能看到会话启动时已连接的 MCP 服务**：跨会话恢复后需重新确认所需服务可用
