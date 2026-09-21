# 长周期任务运行机制

本文件是 continuity 技能的运行机制文档，SKILL.md 引用本文件获取生命周期、checkpoint 模板与恢复流程细节。

## Lifecycle

```
任务开始（orchestration 声明 SELECTIVE ROUTE，create_task 落 state.json）
   |
   v
[执行] delegate/full → 原生 Workflow 运行（record_workflow_run 绑定 run id）
   |
   v（实质性里程碑 -> 写 CONTINUITY CHECKPOINT）
   |
   v（中断：会话结束 / 额度耗尽 -> waiting_quota / Workflow 停摆）
[再激活]（SessionStart 恢复注入 / 原生 Scheduled Task 唤醒 / 用户手动恢复）
   |
   v
八步恢复检查（repository > checkpoint > 会话记忆）
   |
   +-- 目标已完成 -> 清理（删本任务目录、停关联 automation）
   +-- 可恢复 -> 同 run id resume（额度可用 + 授权 + 预算有余时）
   +-- 不可恢复（额度耗尽 / run 已死）-> 停泊等待或按仓库现状重建
```

## CONTINUITY CHECKPOINT 模板

长任务在每个实质性里程碑后（不是每次工具调用后）写入任务专属路径 `.glm-conductor/tasks/<task-id>/checkpoint.md`。同一任务的多次写入覆盖本任务自己的文件；不同任务各用各的目录，互不覆盖、互不删除。模板可直接复制：

```
CONTINUITY CHECKPOINT

TASK_ID:
<语义前缀>-<随机十六进制后缀>（如 redesign-settings-page-7f3a2c；生成契约见下）

GOAL:
<原始目标>

ROUTE:
<solo | delegate | audit | full>

ASSURANCE:
<standard | high>

WORKFLOW RUN ID:
<委派任务的 run id，无则省略本节>

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
```

### TASK_ID 规则

格式——生成即机械唯一，不依赖命名约定：

```text
<语义前缀>-<6~8 位随机十六进制后缀>
```

示例：`redesign-settings-page-7f3a2c`、`parser-refactor-a92c1d`、`migrate-auth-20260828-231502`（时间戳后缀可接受，随机后缀优先——并行同时创建的任务也不碰撞）。

生成契约：

1. 任务创建时生成一次（create_task 的 task_id 即它）
2. 随 checkpoint 持久化
3. 恢复期间绝不重新生成
4. checkpoint 路径、视觉证据路径、定时 resume prompt、automation 关联、完成清理，全部使用同一精确 ID
5. 在当前工作区内必须唯一——语义前缀人类可读是可选收益，唯一性是强制要求
6. 生成后跨恢复保持稳定（写入 checkpoint 后不得再变）
7. 合法字符约束（`runtime/state.py` 校验）：字母数字开头，仅含字母数字与连字符

查找与隔离纪律：

- 定时的 resume 指令必须携带精确 ID
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

## Runtime State（state.json）与执行日志（events.jsonl）

active task 在 checkpoint 之外维护两个机器可读文件（同目录、同 TASK_ID），三者分工：

| 文件 | 性质 | 读者 |
| --- | --- | --- |
| checkpoint.md | 导航状态（叙述性恢复依据） | 模型 |
| state.json | 确定性任务状态（完成守卫按它校验完成条件） | 守卫 / 模型 |
| events.jsonl | 执行溯源（append-only，一行一事件） | 模型 / 审计 |

state.json 字段概览（权威 schema 见插件 `runtime/state.py`）：

| 字段 | 说明 |
| --- | --- |
| task_id / goal | 标识与目标 |
| repository | 任务绑定的仓库根（`repository.root`，求值与求值根解析的唯一绑定） |
| route | mode / assurance 两键（v2.4 摘取口径） |
| dag | 静态节点数组（形状见 `runtime/work_unit.py`；完成守卫按全节点 ownership 并集做越界判定） |
| status | 七态：active / waiting_quota / waiting_user / blocked / completed / cancelled / failed |
| phase | 可选描述性标注（planning / workflow / validating / reviewing），无转换矩阵 |
| workflow_run_id | 委派 run 关联（启动时由 record_workflow_run 落盘；恢复按同 run id resume） |
| validation | 任务级验证记录（status passed/failed + change_id） |
| review | 任务级审查记录（reviewer + verdict + change_id） |
| quota_resume | 恢复授权块（mode / max_resumes / resume_count / automation_id [+ 诊断性 last_observation]） |

status 只前进不回退（`state.TASK_TRANSITIONS` 矩阵校验）；终态不得静默重开（显式重开走 `state.reopen_task`）；repository 仍是代码状态真相源，state.json 只是运行时任务状态。

events.jsonl 任务级事件词汇恰十个（`runtime.task.TASK_JOURNAL_EVENTS`）：route_selected / workflow_started / workflow_reassessed / validation_recorded / review_recorded / waiting_quota / quota_resume_confirmed / task_completed / task_failed / task_cancelled——逐事件时机见 SKILL.md「任务状态与运行时文件」节；追加一律通过 `runtime/journal.py`（时间戳由模块管理，调用方不得自带）。

### 恢复时的读取顺序

八步恢复中，第 2 步读取 checkpoint 的同时读取 state.json、并查看 events.jsonl 尾部：

1. **state.json**（机器可读）：恢复 status、route、workflow_run_id、validation / review 认知——若存在且非终态，本任务仍是 active task，恢复后仍受完成守卫跟踪；quota_resume 块显示授权与预算现状
2. **checkpoint.md**（叙述性）：恢复 NEXT ACTION 与上下文
3. **events.jsonl 尾部**（最近若干条）：了解中断前最后发生了什么（最后一条事件往往就是中断点）

三者冲突时仍以 repository 为准；state.json 与 checkpoint.md 的叙述冲突以仓库实际 diff 裁决并修正两者。

## Resume Procedure（八步）

每次重新激活后必须依次执行：

1. **检查当前 Goal**：确认当前会话目标是否仍是同一目标
2. **读取本任务 checkpoint**：读取 resume 指令携带的 `.glm-conductor/tasks/<task-id>/checkpoint.md`；指令未携带 ID 时，按目标语义在 `.glm-conductor/tasks/` 下定位匹配目录；不存在时按无 checkpoint 处理，只能从目标与仓库现状重建认知
3. **检查仓库状态**：仓库真实状态优先于 checkpoint（repository > checkpoint）
4. **检查当前 diff**：确认工作区当前实际有哪些改动
5. **判断先前变更是否仍在**：checkpoint 声称已完成的改动，是否能在 diff 与仓库中观察到
6. **判断目标是否已完成**：已完成则停止实施，进入 Completion Cleanup
7. **检查最新验证状态**：validation 记录是否仍新鲜（change_id 与当前一致）、还剩哪些验证未做
8. **从 NEXT ACTION 恢复**：重新输出 SELECTIVE ROUTE 声明后，从 NEXT ACTION 继续执行（委派任务先走「同 run id resume」，见 SKILL.md）

两条禁令：

- 禁止唤醒后盲目重播旧指令
- 禁止为恢复 checkpoint 回滚仓库新改动——checkpoint 与仓库不一致时，分析变化来源、更新认知、以仓库为准

## 结构化 Resume Prompt（定时任务用）

以下 prompt 用作宿主原生 Scheduled Task 的触发内容。它必须自包含：不依赖任何会话上下文，凭此 prompt + 指定 checkpoint + 仓库状态即可恢复。**占位符必须在创建定时任务时替换为真实值**：

```
Resume GLM Conductor task:

TASK_ID: <id>
TASK DIR: .glm-conductor/tasks/<id>/
CHECKPOINT: .glm-conductor/tasks/<id>/checkpoint.md

Inspect the current goal, the task state.json, the specified checkpoint,
and the current repository state.

If the task is already terminal or its goal is satisfied, perform no
further implementation and clean up only the runtime state associated
with this TASK_ID (single cleanup attempt for any associated automation).

If the task is waiting on quota, obtain a fresh quota observation and
follow the idempotent decision contract (quota-resume-decision);
confirm via quota-resume-confirm only after the host resume call is
actually accepted.

If a workflow_run_id is recorded and resumable, resume the same run.
Otherwise rebuild from repository reality; never replay finished work.

Repository state is authoritative over checkpoint state.

Continue under the stored route, ownership, validation, and review
contracts; re-declare SELECTIVE ROUTE before resuming execution.

Perform ROUTE REASSESSMENT only when newly observed evidence changes
delegability or assurance.
```

## Scheduled Trigger（原生 Scheduled Task）

无人值守恢复映射到宿主原生定时任务：

- **触发内容** = 上述结构化 resume prompt（已替换 TASK_ID 与路径占位符）
- **载体** = one-shot（一次性）定时任务——一次触发解决一轮恢复；任务到达终态后对它做单次清理尝试
- **每次唤醒任务量要小**：做一轮检查，然后恢复一段工作——避免单次唤醒塞满全部剩余工作

**调度触发即存活探针**：唤醒成功启动即说明模型执行当前可用；唤醒失败或未启动则不会产生任何仓库改动，自然等待下次触发或用户恢复。

**同会话投递事实**：宿主把唤醒 prompt 作为新 turn 注入既有会话（中途续入）——唤醒轮次可见会话既有上下文，但恢复依据仍然是仓库与 state（repository > checkpoint > 会话记忆）；跨会话自包含恢复（同一 prompt 语义）不依赖会话记忆，两种形态下行为一致。

不硬编码重置周期：额度政策可能变化，任何固定周期都会在政策变化后静默失效。唤醒时机只依赖"唤醒时重新检查"这一动作（新鲜额度观测 + 幂等决策），与具体政策解耦；额度不可知时等待用户或下次触发，不虚构可用性。

## Route Recovery

恢复的会话必须重新输出 SELECTIVE ROUTE 声明后再执行（详细规则见 orchestration 技能的"恢复会话的路由恢复"章节）；若证据显示双轴判断（Delegability × Assurance）已变化，先做 ROUTE REASSESSMENT。

## Completion Cleanup

目标完成并验收后的动作清单（只作用于本任务）：

1. 删除本任务目录 `.glm-conductor/tasks/<task-id>/`（仅此目录，不得触碰其他任务的状态）
2. 移除与该 TASK_ID 关联的定时 automation——**会话侧单次尝试**动作（绝不重试；删除失败按降级上报）
3. 向用户报告最终状态

宿主调度动作（创建 / 删除定时任务）由主会话执行；runtime 自身零宿主调度调用。窗口/预算语义与 automation 存活解耦：授权与计数只发生在 `authorize_quota_resume` / `confirm_resume_started` 两个落盘点。

## Failure Cases

- **定时能力不可用**：不得声称已启用连续性；保留本任务 checkpoint 目录，向用户报告手动可恢复，并附恢复方法——新建会话输入"读取 .glm-conductor/tasks/<task-id>/checkpoint.md 并按八步恢复流程继续"（SessionStart 恢复注入会自动提示）
- **实施通道缺失**（如 visual-implementer 不可用）：fail-closed，不自动换成其他执行者，交回用户处理
- **唤醒后 checkpoint 与仓库严重不一致**：以仓库为准，向用户报告差异后继续；不得回滚仓库新改动
- **Workflow run 不可 resume**：宿主报告 run 不存在或不可恢复时，按 SKILL.md「Workflow 恢复」第 4 步以仓库现状重建（未实施节点重新编译提交），绝不重放已完成工作

## ZCode Limitations

- **无原生 quota API**：插件级接口（getQuotaRemaining / getQuotaResetTime / onQuotaReset）均不存在，不得虚构；额度观测走只读解析面（SKILL.md「额度观测面」节），凭证不可得时按 UNKNOWN 处理
- **额度观察作为证据**：解析快照、用户告知或 UI 观察都只作为证据使用；不作为路由轴、不据此自动换模型或绕过授权
- **子智能体内不能再派生子智能体**：编排者只能是主会话
- **子智能体只能看到会话启动时已连接的 MCP 服务**：跨会话恢复后需重新确认所需服务可用
- **runtime 零宿主调度写入**：定时任务的创建 / 删除是主会话的宿主动作；Conductor 侧没有任何 scheduler 写入路径
