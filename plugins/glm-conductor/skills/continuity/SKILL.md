---
name: continuity
description: GLM 长任务连续性：原生 Workflow 恢复（同 run id resume）、waiting_quota 额度等待、manual/auto 有界恢复（authorize 授权 + max_resumes 预算 + confirm 确认才计数）、auto 连续性先武装后开工（arm-before-work：授权 → 创建原生未来 Scheduled Task → bind → preflight PASS 才算 armed，才许 CreateWorkflow 与自动 resume）、原生 Scheduled Task 唤醒（recurring 优先、one-shot 后备先创建后继再 resume；幂等决策契约；唤醒轮次是宿主会话中途续入）与终态清理（确认删除才清绑定）。适用于长任务跨会话或跨额度窗口中断后的恢复、额度耗尽后的有界续跑、无人值守的定时唤醒。
---

# GLM 连续性：Workflow 恢复与有界续跑

## Purpose

一次任务的路由（solo / delegate / audit / full）由 orchestration 技能决定；continuity 只回答一个问题——未完成的工作如何恢复。两者组合而非替代。

v2.4 的连续性由四个机制构成，全部围绕任务状态（state.json）展开：

1. **Workflow 恢复**——中断的委派运行按同一 run id 续跑；
2. **waiting_quota**——额度耗尽时的确定性停泊点；
3. **manual / auto 有界恢复**——恢复是用户授权下的预算行为，不是模型的即兴决定；
4. **arm-before-work 原生调度武装**——auto 连续性先武装后开工：授权 → 创建原生未来 Scheduled Task → bind → preflight PASS 才算 armed，才许 CreateWorkflow 启动与任何自动 resume。

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
3. **续跑**：任务有 `workflow_run_id` 且未到终态 → 经宿主以**同一 run id** resume 该 Workflow（宿主持有执行状态，恢复的是它自己的运行）；恢复后照常等结构化结果 → 主会话验证 → 收尾。自动唤醒路径（mode=auto）多两道硬闸：决策 `resume-authorized` **且** `quota-continuity-preflight` ready 才许 resume（见「原生 Scheduled Task 唤醒」）；写者守卫仍是本任务持有（无过期逻辑）——这是同一 run 续跑的凭证，不需要重新 acquire
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

### arm-before-work：先武装后开工（INV-CONT-01）

授权与武装是两个独立事实：**authorize 是用户许可；bind 是未来激活见证；preflight 是两者齐备的机械确认**。mode=auto 时，`quota_resume.automation_id` 必须在 CreateWorkflow 启动之前、以及任何自动 ResumeWorkflowRun 之前非空——auto 连续性在 automation_id 为空时绝不是 armed（unarmed 即阻断）。武装不是模式的推断结果：bind 绝不推断 mode=auto，授权也绝不虚构 automation id。

auto/until_done 启动序列（与 orchestration §6.2 一致；调度创建、绑定与预检一律置于 CreateWorkflow 之前）：

```
create task
-> quota-resume-authorize（显式有界 max_resumes）
-> 创建原生未来 Scheduled Task（宿主动作，主会话执行）
-> quota-automation-bind（只绑真实创建返回的 automation id）
-> quota-continuity-preflight 必须 PASS
-> v24-compile
-> 显式 Flash 模型选型（orchestration §6.1 硬闸）
-> writer-acquire
-> CreateWorkflow（subagent_model=精确 Flash id）
-> 记录 run 关联
```

Scheduled Task 创建失败三禁：**绝不谎称已启用**（向用户如实报告 auto 连续性未武装）、**绝不绑假 id**、**绝不按 auto 承诺继续**（本轮降级为仅手动连续性，或交运营者处理）。

武装操作面三命令（v2.4.1）：

- `quota-automation-bind <repo_root> <task_id> <automation_id>`（底层 `task.bind_quota_automation`）：把未来 Scheduled Task 见证 id 绑入 quota_resume.automation_id；同 id 幂等成功、异 id 拒绝——绝不静默替换，替换必须是宿主侧生命周期对账后的显式 clear → bind 序列；只允许 active / waiting_quota / waiting_user 三态
- `quota-automation-clear <repo_root> <task_id> [automation_id]`（底层 `task.clear_quota_automation`）：预期在宿主侧删除确认或运营者显式对账**之后**使用；给定 id 与存量不一致拒绝、存量 None 幂等成功；只清 automation_id，绝不改 mode / max_resumes / resume_count；终态任务允许清理
- `quota-continuity-preflight <repo_root> <task_id>`（底层 `task.quota_continuity_preflight`，纯读不改状态）：manual → ready=true；auto + 非空绑定 → ready=true（armed）；auto + 空 → ready=false（退出码 2，主会话机械视作 launch/resume 阻断）。它只证明持久化绑定存在，**绝不证明宿主侧激活仍存活**——唤醒边界仍须宿主侧核查

决策层的机械镜像（纵深防御）：`scheduled_activation_decision` 在 auto + automation_id 为空时拒绝给出 resume-authorized，返回 remain-waiting 且 reason 含 `continuity-unarmed`——零状态写盘、零预算消耗（武装缺失是运维活性故障，不是授权耗尽）。

## 原生 Scheduled Task 唤醒（recurring 优先）

无人值守恢复的**首选载体是 recurring 原生 Scheduled Task**：一次创建持续唤醒，保持武装到终态清理、显式取消或运营者批准的策略变更——recurring 激活成功唤醒后**绝不删除、绝不重建**（INV-CONT-03），重复唤醒天然安全（决策幂等）。仅当 recurring 调度不可用或刻意不用时，才退到 one-shot 后备（见下节）。

**唤醒 prompt 必带**（自足稳定标识，凭仓库真相即可恢复；不嵌完整 workflow 源码、不嵌秘密 / 凭证、不嵌过期额度假设——模板见 references/long-horizon.md）：

- 精确 task_id 与仓库根（repo root）
- 先载任务态（state.json + checkpoint）再行动
- 唤醒时刷新额度（quota-resume-decision 自带新鲜观测）
- preflight 先行：任何自动 resume 前必跑 `quota-continuity-preflight`
- 查 workflow_run_id：从任务状态取精确 run id，按同一 run id resume
- decision=resume-authorized **且** preflight ready 才 ResumeWorkflowRun
- 宿主接受后 `quota-resume-confirm` 落账
- 终态清理指令

**唤醒行为四分支**（固定次序，逐字执行）：

```
load task（先载任务态）
if terminal（completed / cancelled / failed）:
    对本任务绑定的 automation 做单次删除
    确认删除后才 quota-automation-clear 清绑
    停止（不再实施）
if active:
    no-op（运行中的任务不需要唤醒干预）
if waiting_user:
    不自动恢复（等用户重新授权或显式收尾）
if waiting_quota:
    刷新额度（quota-resume-decision 新鲜观测）
    决策（no-op / remain-waiting / waiting-user / resume-authorized）
    验 preflight（quota-continuity-preflight）
    resume-authorized 且 preflight ready：
        ResumeWorkflowRun（同一 run id）
        宿主接受后 quota-resume-confirm 落账
```

**幂等决策契约**（每次唤醒固定执行）：

1. 带新鲜额度观测调 `quota-resume-decision <repo_root> <task_id> [--force-refresh]`（底层 `task.scheduled_activation_decision`）——重复唤醒安全：任务不在 waiting_quota（已完成/已取消/已转 waiting_user）一律 no-op，零副作用；auto + 未武装拒绝给出 resume-authorized（`continuity-unarmed`）
2. `resume-authorized` 且 preflight ready 时由主会话以同一 run id resume Workflow，宿主接受后立即 `quota-resume-confirm <repo_root> <task_id>` 落账（底层 `task.confirm_resume_started`）
3. 唤醒轮次的工作量要小——做一轮检查、恢复一段工作，不把剩余工作塞进单次唤醒
4. 任务到达终态后，只对本任务绑定的 automation 做**单次**清理尝试（删除即止，绝不重试）；确认删除后才清除绑定 id，删除失败如实上报 stale 关联（详见「终态清理」）

**唤醒轮次是宿主会话中途续入**——事实与含义：

- 触发时宿主把 prompt 作为新的 turn 注入**既有会话**（不是全新上下文的独立代理）：唤醒轮次能看到会话既有上下文，但这不构成恢复依据——恢复仍必须走仓库事实与 state.json（repository > checkpoint > 会话记忆），防止用陈旧会话记忆跳过对账
- runtime 自身零宿主调度调用：Scheduled Task 的创建 / 删除等宿主动作由主会话执行；Conductor 只提供幂等决策与武装预检原语

### one-shot 后备（仅 recurring 不可用或刻意不用时）

one-shot 激活触发即失效——唤醒时必须**先创建后继、先武装后恢复**（INV-CONT-02：liveness 续期先于 resume）：

```
load state
刷新额度 / 复位证据
任务可自动继续时：
    创建后继未来激活
    quota-automation-bind 后继 id
    quota-continuity-preflight PASS
然后才：
    ResumeWorkflowRun（同一 run id）
    宿主接受后 quota-resume-confirm 落账
```

替换已触发 one-shot id 的安全序列（宿主不支持先建后删时，按序执行）：对账已触发 / 现存 automation → `quota-automation-clear` 清旧绑定 → 创建后继 → `quota-automation-bind` 绑后继 → preflight PASS → resume。宿主支持先建后删时，先创建后继再经显式对账序列原子迁移绑定——绝不让长自动续跑出现「无后继且无力创建后继」的裸奔窗口。两条硬禁令：

- **绝不以「加 5 小时」推导下一窗口**——下一窗口只来自 provider 复位证据（观测 reset_at）或受支持的 recurring / retry 计划，不硬编码任何周期
- **绝不在无后继时 clear**——清掉唯一唤醒能力而任务仍需自动续跑，等于静默拆掉连续性

无常驻观测进程、无额度轮询服务、无账户级时钟组件——额度观测只发生在刷新点（任务开始 / 恢复前后 / 大段派发前），原生 Scheduled Task 唤醒是唯一调度载体。

## Checkpoint（导航状态）

长任务在实质性里程碑后（不是每次工具调用后）把 CONTINUITY CHECKPOINT 写入任务目录 `checkpoint.md`：记录 TASK_ID / GOAL / ROUTE / COMPLETED / CURRENT STATE / NEXT ACTION / VERIFICATION / OPEN RISKS。原则：

- checkpoint 是导航状态，不是仓库真相源——不复制完整 diff、不复制大量代码、不声称未验证内容
- repository 状态始终优先：checkpoint 与仓库冲突时以仓库为准
- 模板与八步恢复程序见 references/long-horizon.md

## 终态清理

任务到达终态（completed / cancelled / failed）并验收后的清理次序固定（只作用于本任务）：

1. **先删后清，次序固定**：对本任务绑定的 automation（以 state 里记录的 automation_id 定位）做宿主侧**单次**删除尝试——确认删除成功后，才 `quota-automation-clear` 清除绑定 id；删除失败则如实上报 stale 关联（报告哪个 automation_id 仍悬挂），绝不假装已清理干净，绑定 id 保留待运营者对账
2. 无重试风暴：删除失败即止，绝不反复重试；重复唤醒 / 重复收尾遇到已清理状态一律幂等跳过
3. **绝不删他人 automation**：只删本任务绑定的这一个；删除全局调度面或其他任务的 automation 是禁止操作
4. 删除本任务目录 `.glm-conductor/tasks/<task-id>/`（仅此目录，不得触碰其他任务的状态或视觉证据）；既有清理流程删除任务目录时，次序绝不在宿主删除尝试之前抹掉 automation_id
5. 向用户报告最终状态（含 auto 连续性武装的最终处置结果）

## 额度观测面（只读）

- `quota-resolve <repo_root> [--force-refresh]`（runtime CLI）：四态解析，输出冻结键 `{status, source, evaluated_at, reason}`；层级 = 新鲜缓存 → provider → 陈旧缓存 → UNKNOWN（绝不默认 AVAILABLE、绝不重试网络）；观测面零任务转态
- `quota-wait / quota-resume-authorize / quota-resume-decision / quota-resume-confirm`（runtime CLI）：额度等待 / 恢复生命周期操作面，规范用法见上「waiting_quota」与「manual / auto 有界恢复」两节；决策落盘只在任务侧，绝不调用宿主 ResumeWorkflowRun
- `quota-automation-bind / quota-automation-clear / quota-continuity-preflight`（runtime CLI）：auto 连续性武装操作面（bind / clear / preflight），规范用法见上「arm-before-work」节；preflight ready=false 以退出码 2 报告（launch/resume 阻断信号）
- `runtime/quota/report.py [--json]`：人读诊断（窗口明细 + status + 恢复建议），详见 `/glm-conductor:quota` 命令
- **凭证**：环境变量 `GLM_CONDUCTOR_QUOTA_API_KEY` 优先，已登录 ZCode 的 `~/.zcode/v2/config.json` provider 配置为文档化回退；凭证零落盘（不进 state.json / events.jsonl / checkpoint / 日志 / 任何输出）；凭证不可得 → UNKNOWN，不虚构
- **lite 套餐**：无周窗（仅 5 小时窗）——report 明细按实际存在窗口输出；解析与决策不依赖周窗存在
- **不变边界**：原生插件级 quota API（getQuotaRemaining / getQuotaResetTime / onQuotaReset）仍不存在，不得虚构；不硬编码重置周期；额度观测只作为证据使用，不作为路由轴；不实现常驻轮询——查询只发生在刷新点

## Failure Handling

- **定时能力不可用**：auto 连续性保持未武装（preflight ready=false）——不得声称已启用连续性、绝不绑假 id、绝不按 auto 承诺继续；保留 checkpoint 与 state，向用户报告"手动可恢复"及恢复方法（新建会话即得 SessionStart 恢复注入）
- **唤醒发现 auto 未武装**（绑定丢失 / 宿主激活已消失）：按 remain-waiting（`continuity-unarmed`）处理——先经宿主重建激活并 bind + preflight PASS 才恢复；绝不虚构 activation、绝不无武装 resume
- **Workflow 不可 resume**：按「Workflow 恢复」第 4 步以仓库现状重建，不重放已完成工作
- **授权预算耗尽**：任务停在 waiting_user——等用户重新授权（`quota-resume-authorize`）或显式收尾；模型不得自行调高预算
- **v2.3 遗留任务**：按 `LEGACY_STATE_GUIDANCE` 报告处置指引，不迁移、不伪造完成证据
