---
name: continuity
description: GLM 长任务连续性：原生 Workflow 恢复（同 run id resume）、waiting_quota 额度等待、manual/auto 有界恢复（authorize 授权 + max_resumes 预算 + confirm 确认才计数）与原生 Scheduled Task 唤醒（幂等决策契约；唤醒轮次是宿主会话中途续入）。适用于长任务跨会话或跨额度窗口中断后的恢复、额度耗尽后的有界续跑、无人值守的一次性定时唤醒。
---

# GLM 连续性：Workflow 恢复与有界续跑

## Purpose

一次任务的路由（solo / delegate / audit / full）由 orchestration 技能决定；continuity 只回答一个问题——未完成的工作如何恢复。两者组合而非替代。

v2.4 的连续性由三个机制构成，全部围绕任务状态（state.json）展开：

1. **Workflow 恢复**——中断的委派运行按同一 run id 续跑；
2. **waiting_quota**——额度耗尽时的确定性停泊点；
3. **manual / auto 有界恢复**——恢复是用户授权下的预算行为，不是模型的即兴决定。

## 任务状态与运行时文件

active task（delegate/full、audit，以及任何需要完成守卫保护的长任务）在专属目录内维护机器可读状态：

```
.glm-conductor/tasks/<task-id>/
├── state.json        # 确定性任务状态（完成守卫按它校验完成条件）
├── events.jsonl      # 执行溯源（append-only，一行一事件）
└── checkpoint.md     # 导航状态（模型可读的恢复叙述，长任务适用）
```

**state.json**（schema 权威：`runtime/state.py`）顶层概念：`task_id / goal / repository / route（mode + assurance）/ dag / status / phase（可选描述性标注）/ workflow_run_id / validation / review / quota_resume`。

- **status 恰为七态**：`active / waiting_quota / waiting_user / blocked / completed / cancelled / failed`；终态 = completed / cancelled / failed，终态不得静默重开（显式重开只能走 `state.reopen_task`，落 journal 记录）
- 状态迁移严格按 `state.TASK_TRANSITIONS` 矩阵（如 waiting_quota → active / waiting_user / blocked / failed / cancelled）；`completed` 只由完成守卫在四查全过后经 `task.complete` 提交
- repository 文件仍是代码状态真相源，state.json 只是运行时任务状态
- 原子写；写入经 `runtime.task` 的生命周期 API（create_task / record_workflow_run / record_validation / record_review / set_status / enter_waiting_quota / ...），不要手拼 JSON 回写

**events.jsonl**：append-only 执行日志。任务级事件词汇恰十个（`runtime.task.TASK_JOURNAL_EVENTS`）：

| 事件 | 时机 |
| --- | --- |
| route_selected | create_task 创建任务时（含 mode / assurance） |
| workflow_started | record_workflow_run 记录委派 run 关联时 |
| workflow_reassessed | 路由重估后（主会话声明 ROUTE REASSESSMENT 时记录） |
| validation_recorded | record_validation 落主验证记录时 |
| review_recorded | review-record 落审查裁决时 |
| waiting_quota | enter/exit_waiting_quota 进出额度等待时（direction 字段） |
| quota_resume_confirmed | confirm_resume_started 暂存恢复计数落账时 |
| task_completed / task_failed / task_cancelled | 终态收尾时（含 from/to） |

约束：不写入任何秘密值（密钥、Authorization 头）、不写入完整 prompt 或完整源码；它不是遥测。

**TASK_ID 规则**：`<语义前缀>-<随机十六进制后缀>`（如 `redesign-settings-page-7f3a2c`），生成即机械唯一、跨恢复保持稳定；任务目录、checkpoint、定时唤醒 prompt、automation 关联全部使用同一精确 ID。完整契约见 references/long-horizon.md。

## Workflow 恢复（同 run id resume）

委派任务（delegate/full）启动时主会话已把 run id 落进 `state.workflow_run_id`（`record_workflow_run`）。Conductor **绝不镜像宿主执行状态**——Workflow 的运行进度是宿主自有事实（GetWorkflowRun 可查），`.glm-conductor/workflow-runs/` 下的记录只是 id 关联单据。

恢复程序（新会话或唤醒后）：

1. **发现**：SessionStart 钩子（matcher `startup|clear|compact`）自动注入 `GLM CONDUCTOR RESUME CONTEXT`——每个未完成任务一行最小摘要（task id / goal / repository / status / route / workflow run id / quota 等待态 / 下一步安全动作）。纯本地、零模型调用、无活动任务时完全安静；崩溃后不需要用户提醒模型"还有任务"
2. **对账**：先看仓库真实状态（git diff / 工作树），再看 state.json 与 checkpoint——**repository > checkpoint > 会话记忆**；v2.3 遗留任务按注入的一行处置指引报告（在 2.3.x 下收尾或显式放弃），v2.4 绝不迁移、绝不伪造完成证据
3. **续跑**：任务有 `workflow_run_id` 且未到终态 → 经宿主以**同一 run id** resume 该 Workflow（宿主持有执行状态，恢复的是它自己的运行）；恢复后照常等结构化结果 → 主会话验证 → 收尾。写者守卫仍是本任务持有（无过期逻辑）——这是同一 run 续跑的凭证，不需要重新 acquire
4. **Workflow 已死无法 resume**（宿主报告 run 不存在/不可恢复）：以仓库现状重建认知——把 DAG 中尚未实施的部分重新编译提交（写者守卫先对账释放，再走 orchestration §6 的启动程序），**绝不盲目重放已完成的节点结果**
5. **Route recovery**：恢复执行前必须重新输出 SELECTIVE ROUTE 声明（沿用或基于新证据重估）；证据显示双轴判断已变化时先做 ROUTE REASSESSMENT

## waiting_quota（额度等待）

额度四态观测（AVAILABLE / PRESSURE / EXHAUSTED / UNKNOWN）来自只读面 `quota-resolve` / `report.py`（见下「额度观测面」）。任务执行中观测到额度不可继续时：

- **enter**：`quota-wait <repo_root> <task_id> [--force-refresh]`（runtime CLI；底层 `task.enter_waiting_quota`）——CLI 自带一次额度观测（resolver.resolve_quota_detail）并把归一化观测传入任务层，任务迁移到 `waiting_quota`（journal `waiting_quota` 事件 direction=enter），最近一次观测（status / reset_at / observed_at）记入 `quota_resume.last_observation` 供诊断。语义是任务已遭遇额度相关执行停止后的**显式停靠命令**，不是自动额度失败探测器
- **停泊语义**：waiting_quota 是有意的停止点——完成守卫放行停泊态；该回合不收尾、不派发
- **绝不虚构可用性**：观测 EXHAUSTED 或 UNKNOWN 时一律继续等待，不据陈旧缓存或猜测恢复
- **exit**：手动恢复走底层 API `task.exit_waiting_quota(repo_root, task_id)` 迁回 active（direction=exit）；自动恢复路径由 `quota-resume-confirm` 落账时直接转 active（`quota_resume_confirmed` 事件）

## manual / auto 有界恢复（授权 + 预算 + 确认）

恢复授权活在 state 顶层 `quota_resume` 块（恰四键：`mode / max_resumes / resume_count / automation_id`；缺省 `manual / 0 / 0 / null`——未授权、零预算）：

| mode | 授权要求 | 行为 |
| --- | --- | --- |
| manual（默认） | — | 不建自动化：额度恢复后等用户回来，或 SessionStart 恢复注入提示 |
| auto | 用户显式 `quota-resume-authorize` | 允许自动恢复，严格受 max_resumes 预算约束 |

授权与计数的三条纪律（机械语义，运行时操作一律走 runtime CLI 四命令；`runtime/task.py` 是底层实现）：

1. **授权必须显式**：`quota-resume-authorize <repo_root> <task_id> <max_resumes>`（底层 `task.authorize_quota_resume`）置 mode=auto 并落预算——只应由用户决定后调用；要求任务处于 waiting_quota 或 active；max_resumes 不得小于已用 resume_count。授权不推断、不随新窗口自动重置
2. **决策幂等**：每次定时唤醒带新鲜额度观测调 `quota-resume-decision <repo_root> <task_id> [--force-refresh]`（底层 `task.scheduled_activation_decision`），返回封闭四词之一——`no-op`（非 waiting_quota，重复唤醒安全）/ `remain-waiting`（EXHAUSTED/UNKNOWN，继续等）/ `waiting-user`（未授权，或预算耗尽——后者恰一次把任务转 `waiting_user` 等用户重新授权）/ `resume-authorized`（可用 + 已授权 + 预算有余；返回 workflow_run_id 与暂存计数）。决策命令绝不调用宿主 ResumeWorkflowRun——resume 是主会话的宿主动作
3. **确认才计数**：`resume-authorized` 的计数只是**暂存**——宿主 resume 调用真正被接受后调用 `quota-resume-confirm <repo_root> <task_id>` 落账（底层 `task.confirm_resume_started`；resume_count +1、任务转回 active、journal `quota_resume_confirmed` 事件）。未确认的决策绝不消耗预算；确认前重复决策恒返回同一暂存值；预算耗尽（resume_count ≥ max_resumes）后任务停在 `waiting_user`，不再自动恢复

## 原生 Scheduled Task 唤醒

无人值守的一次性恢复用宿主原生定时任务（如 one-shot CronCreate）实现：主会话创建定时任务，触发内容是一段自足的 resume prompt（TASK_ID、任务目录路径、恢复首步、预算状态内置——模板见 references/long-horizon.md）。

**唤醒轮次是宿主会话中途续入**——事实与含义：

- 触发时宿主把 prompt 作为新的 turn 注入**既有会话**（不是全新上下文的独立代理）：唤醒轮次能看到会话既有上下文，但这不构成恢复依据——恢复仍必须走仓库事实与 state.json（repository > checkpoint > 会话记忆），防止用陈旧会话记忆跳过对账
- runtime 自身零宿主调度调用：CronCreate / CronDelete 等宿主动作由主会话执行；Conductor 只提供幂等决策原语

**幂等决策契约**（每次唤醒固定执行）：

1. 带新鲜额度观测调 `quota-resume-decision <repo_root> <task_id> [--force-refresh]`（底层 `task.scheduled_activation_decision`）——重复唤醒安全：任务不在 waiting_quota（已完成/已取消/已转 waiting_user）一律 no-op，零副作用
2. `resume-authorized` 时由主会话以同一 run id resume Workflow，宿主接受后立即 `quota-resume-confirm <repo_root> <task_id>` 落账（底层 `task.confirm_resume_started`）
3. 唤醒轮次的工作量要小——做一轮检查、恢复一段工作，不把剩余工作塞进单次唤醒
4. 任务到达终态后，对该任务关联的一次性 automation 做**单次**清理尝试（删除即止，绝不重试）；删除失败按降级上报

无常驻观测进程、无额度轮询服务、无账户级时钟组件——额度观测只发生在刷新点（任务开始 / 恢复前后 / 大段派发前），原生 Scheduled Task 唤醒是唯一调度载体。

## Checkpoint（导航状态）

长任务在实质性里程碑后（不是每次工具调用后）把 CONTINUITY CHECKPOINT 写入任务目录 `checkpoint.md`：记录 TASK_ID / GOAL / ROUTE / COMPLETED / CURRENT STATE / NEXT ACTION / VERIFICATION / OPEN RISKS。原则：

- checkpoint 是导航状态，不是仓库真相源——不复制完整 diff、不复制大量代码、不声称未验证内容
- repository 状态始终优先：checkpoint 与仓库冲突时以仓库为准
- 模板与八步恢复程序见 references/long-horizon.md

## 完成清理

任务到达终态并验收后，只清理本任务的状态：

1. 删除本任务目录 `.glm-conductor/tasks/<task-id>/`（仅此目录，不得触碰其他任务的状态或视觉证据）
2. 移除与本 TASK_ID 关联的定时 automation（单次尝试）
3. 向用户报告最终状态

避免幽灵唤醒重复执行；并行任务下删除全局或他人状态是禁止操作。

## 额度观测面（只读）

- `quota-resolve <repo_root> [--force-refresh]`（runtime CLI）：四态解析，输出冻结键 `{status, source, evaluated_at, reason}`；层级 = 新鲜缓存 → provider → 陈旧缓存 → UNKNOWN（绝不默认 AVAILABLE、绝不重试网络）；观测面零任务转态
- `quota-wait / quota-resume-authorize / quota-resume-decision / quota-resume-confirm`（runtime CLI）：额度等待 / 恢复生命周期操作面，规范用法见上「waiting_quota」与「manual / auto 有界恢复」两节；决策落盘只在任务侧，绝不调用宿主 ResumeWorkflowRun
- `runtime/quota/report.py [--json]`：人读诊断（窗口明细 + status + 恢复建议），详见 `/glm-conductor:quota` 命令
- **凭证**：环境变量 `GLM_CONDUCTOR_QUOTA_API_KEY` 优先，已登录 ZCode 的 `~/.zcode/v2/config.json` provider 配置为文档化回退；凭证零落盘（不进 state.json / events.jsonl / checkpoint / 日志 / 任何输出）；凭证不可得 → UNKNOWN，不虚构
- **lite 套餐**：无周窗（仅 5 小时窗）——report 明细按实际存在窗口输出；解析与决策不依赖周窗存在
- **不变边界**：原生插件级 quota API（getQuotaRemaining / getQuotaResetTime / onQuotaReset）仍不存在，不得虚构；不硬编码重置周期；额度观测只作为证据使用，不作为路由轴；不实现常驻轮询——查询只发生在刷新点

## Failure Handling

- **定时能力不可用**：不得声称已启用连续性；保留 checkpoint 与 state，向用户报告"手动可恢复"及恢复方法（新建会话即得 SessionStart 恢复注入）
- **Workflow 不可 resume**：按「Workflow 恢复」第 4 步以仓库现状重建，不重放已完成工作
- **授权预算耗尽**：任务停在 waiting_user——等用户重新授权（`quota-resume-authorize`）或显式收尾；模型不得自行调高预算
- **v2.3 遗留任务**：按 `LEGACY_STATE_GUIDANCE` 报告处置指引，不迁移、不伪造完成证据
