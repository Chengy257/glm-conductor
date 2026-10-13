---
name: continuity
description: GLM 长任务连续性：原生 Workflow 恢复（同 run id resume）、waiting_quota 额度等待、manual/auto 有界恢复（authorize 授权 + max_resumes 预算 + confirm 确认才计数）、auto 连续性先武装后开工（arm-before-work：授权 → 创建原生未来 Scheduled Task → bind → preflight PASS 才算 armed，才许 CreateWorkflow 与自动 resume；唤醒机制的建立/续臂动作永远先于工作推进）、双通道唤醒（v2.6.0：启动即建 recurring 地板载体——间隔经启动问答确认、默认 1h、终身不变；精度层后台睡眠同批启动即武装到观测 reset_at+300s——reset_at 为最早的有效未来观测复位时刻、任何额度状态无条件透传（含 no-op），无有效未来观测时如实陈述 floor-only 降级、绝不谎称已武装，每次唤醒轮收尾必核查在臂并滚动续臂；两种触发源同一唤醒协议——醒来首动作 quota-resume-decision --force-refresh、动作优先、绝不枚举工具清单；active 分支含 run 健康检查自愈；终态删除按宿主三态规律预期失败、如实上报 automation id 交用户手动删除；唤醒轮次是宿主会话中途续入、唤醒轮绝不弹问）与终态清理（确认删除才清绑定）。适用于长任务跨会话或跨额度窗口中断后的恢复、额度耗尽后的有界续跑、无人值守的定时唤醒。
---

# GLM 连续性：Workflow 恢复与有界续跑

## Purpose

一次任务的路由（solo / delegate / audit / full）由 orchestration 技能决定；continuity 只回答一个问题——未完成的工作如何恢复。两者组合而非替代。

v2.4 的连续性由四个机制构成，全部围绕任务状态（state.json）展开：

1. **Workflow 恢复**——中断的委派运行按同一 run id 续跑；
2. **waiting_quota**——额度耗尽时的确定性停泊点；
3. **manual / auto 有界恢复**——恢复是用户授权下的预算行为，不是模型的即兴决定；
4. **arm-before-work 原生调度武装**——auto 连续性先武装后开工：授权 → 创建原生未来 Scheduled Task → bind → preflight PASS 才算 armed，才许 CreateWorkflow 启动与任何自动 resume。

v2.5.2 在四机制之上并入两条扩展：

5. **任务启动问答授权落点**——建状态任务启动前的强制一次问答（单轮三问，契约见 orchestration §6.0）把「是否跨额度自动唤醒 + 有界 max_resumes 预算」「worker 模型（仅选型歧义时）」「地板载体间隔（默认 1h）」落进任务状态与武装参数；问答只发生在交互回合，唤醒轮 / 恢复轮绝不弹问；
6. **双通道唤醒（启动即武装 + 滚动续臂）**——地板（recurring 载体，活性兜底，抗应用重启，创建后终身不再触碰）与精度层（后台睡眠到观测 reset_at+300s，秒级精确——reset_at 为最早的有效未来观测复位时刻，与额度状态无关；无有效未来观测时精度层缺席、floor-only 降级如实陈述）同批建立；唤醒机制的建立/续臂动作永远先于工作推进，每次唤醒轮收尾必核查在臂睡眠并续臂滚动到当前观测的下一未来窗口边界（见「双通道唤醒」节）。

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
- 原子写；写入经 runtime.task 的生命周期 API，技能层一律走 runtime CLI 具名子命令（`task-create` / `record-validation` / `review-record` / `quota-*` / `exit-waiting` / `task-complete` / `task-fail` / `task-cancel`——权威命令清单见 `runtime/cli.py` USAGE），不要手拼 JSON 回写、不要 `python3 -c` 内联

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
- **exit**：手动恢复走 CLI `exit-waiting <repo_root> <task_id>`（底层 `task.exit_waiting_quota`）迁回 active（direction=exit）；自动恢复路径由 `quota-resume-confirm` 落账时直接转 active（`quota_resume_confirmed` 事件）

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

**建立动作优先（v2.5.2 总原则，v2.6.0 预防性武装）**：一旦启动问答（orchestration §6.0）确认启用，唤醒机制的建立 / 续臂永远先于当下工作推进：授权确认后立即观测 reset_at（`quota-resume-decision --force-refresh`，任何额度状态），地板载体在 CreateWorkflow 之前同批建立；存在有效未来观测 reset 时首个精度睡眠同批发出（无有效未来观测 → 不发、如实陈述 floor-only 降级，绝不谎称精度层已武装）（完整 runbook 见「双通道唤醒」节启动序列）；武装 PASS 前不开工。预算仍有限：`max_resumes` 有界语义不变，预算耗尽转 `waiting_user` 等用户重新授权，绝不无限自动复活。

**唤醒发现 unarmed**（state 里 automation_id 为空）：单次重建尝试（宿主 CronCreate 地板 → `quota-automation-bind` → preflight PASS）。两种结局都如实处理：创建被会话归属拒绝（会话名下其实仍有存活载体、只是账本见证缺失）→ 按现状报告并继续依赖宿主节奏，绝不重试风暴；创建成功 → bind + preflight 后继续。重建失败按 remain-waiting 报告并收轮，绝不弹问、绝不无武装 resume。

武装操作面三命令（v2.4.1）：

- `quota-automation-bind <repo_root> <task_id> <automation_id>`（底层 `task.bind_quota_automation`）：把未来 Scheduled Task 见证 id 绑入 quota_resume.automation_id；同 id 幂等成功、异 id 拒绝——绝不静默替换，替换必须是宿主侧生命周期对账后的显式 clear → bind 序列；只允许 active / waiting_quota / waiting_user 三态
- `quota-automation-clear <repo_root> <task_id> [automation_id]`（底层 `task.clear_quota_automation`）：预期在宿主侧删除确认或运营者显式对账**之后**使用；给定 id 与存量不一致拒绝、存量 None 幂等成功；只清 automation_id，绝不改 mode / max_resumes / resume_count；终态任务允许清理
- `quota-continuity-preflight <repo_root> <task_id>`（底层 `task.quota_continuity_preflight`，纯读不改状态）：manual → ready=true；auto + 非空绑定 → ready=true（armed）；auto + 空 → ready=false（退出码 2，主会话机械视作 launch/resume 阻断）。它只证明持久化绑定存在，**绝不证明宿主侧激活仍存活**——唤醒边界仍须宿主侧核查

决策层的机械镜像（纵深防御）：`scheduled_activation_decision` 在 auto + automation_id 为空时拒绝给出 resume-authorized，返回 remain-waiting 且 reason 含 `continuity-unarmed`——零状态写盘、零预算消耗（武装缺失是运维活性故障，不是授权耗尽）。

## 双通道唤醒：地板载体 + 精度层睡眠（启动即武装 + 滚动续臂）

无人值守恢复由**两条正交通道**承载（v2.5.2 终态；宿主事实与活体证据见 `docs/reviews/WAKE_TOPOLOGY_PROBE_RESULTS.md`）：

- **地板（floor）**：启动即建的 recurring 原生 Scheduled Task——**间隔经启动问答第三问确认（默认 1h）**，创建后**终身不再触碰**（不删、不改、不重建，INV-CONT-03 原样成立）。职责是活性兜底：抗应用重启（宿主承诺 automation 存活跨重启）、在精度层失联时最迟一个间隔内接住恢复。
- **精度层（precision sleep）**：后台 Bash 睡眠任务——睡到**观测 reset_at + 300s 宽限**（reset_at = 最早的有效未来观测复位时刻，与额度状态无关；无有效未来观测 → 精度层缺席，floor-only 降级如实陈述），退出即自动唤起会话（实测秒级精确 + 8-11s 唤起延迟）。与 automation 状态机完全正交（零 Cron 交互）；不抗应用重启（由地板兜底）。**存在有效未来 reset 时授权确认即武装首个睡眠，先于 CreateWorkflow**——静默耗尽（额度死在回合之间）由在臂睡眠天然覆盖。

设计依据（宿主事实，全部活体实测）：一会话一 automation；载体首触发后该会话一切 Cron 变更（建/改/删）静默失效（三态规律）；**额度耗尽时模型轮不可执行**——等待期连续性只能由宿主侧节奏（automation 触发或后台进程）承担，任何依赖「醒来后做调度动作」的机制在最需要处不可执行；reset_at 由观测面**无条件透传**（v2.6.0 单一观测口径：任何额度状态——AVAILABLE/PRESSURE/EXHAUSTED/UNKNOWN——都透传最早的有效未来观测 reset；额度状态只管可否继续/恢复，不管可否透传时间），额度耗尽只决定「醒来有无活干」，不决定「该几点醒」。

**总原则（建立动作优先）**：唤醒机制的建立 / 续臂动作**永远先于工作推进**——授权确认即观测 reset_at 并同批建立地板（存在有效未来 reset 时同批发首个精度睡眠，先于 CreateWorkflow）；每次唤醒轮收尾必核查在臂并续臂；停靠轮只做核查、补发与纠偏，**绝不承担建立首个睡眠**。

### 启动序列（runbook，arm-before-work）

```
问答（orchestration §6.0，单轮三问：①跨额度唤醒+预算 ②worker 模型（仅歧义） ③地板间隔，默认 1h）
-> task-create <repo_root> <task_id> <goal> <mode> <assurance> [--dag <path>]
-> quota-resume-authorize <repo_root> <task_id> <max_resumes>（问答第一题确认的预算）
-> 立即 quota-resume-decision <repo_root> <task_id> --force-refresh 取观测 reset_at
   （任何额度状态都可观测——单一观测口径：最早的有效未来观测 reset，与额度状态
   无关；active/no-op 分支同样透传 reset_at，不要求任务在 waiting_quota）
-> 宿主 CronCreate 地板载体：intervalUnit=hourly, interval=<问答第三问确认值，默认 1>,
   recurring=true, prompt=统一唤醒协议（见下；动作优先，绝不枚举工具清单）
-> quota-automation-bind <repo_root> <task_id> <真实返回的 automation id>
-> quota-continuity-preflight <repo_root> <task_id> 必须 PASS（ready=false 即阻断启动）
-> 发首个精度睡眠：后台 sleep 到（观测 reset_at + 300s）——仅当本次观测含有效
   未来 reset 时发出；reset_at 为 null 时**不发**、如实陈述 floor-only 降级
   （只有地板在臂），绝不谎称精度层已武装——命令模板逐字抄写自
   references/long-horizon.md「精度层睡眠模板」（bash 动态计算秒数，模型零算术；
   宿主接受后台进程即记录在案）
-> 应用问答第二题的 worker 模型 pin：worker-model-pin <repo_root> <task_id> <精确 id>
   （第二题出现过时；消费语义「失效即拒」见 orchestration §6.1）
-> v24-compile -> 显式 worker 模型选型（orchestration §6.1 硬闸） -> writer-acquire
-> CreateWorkflow（subagent_model=选型返回值） -> v24-record-run <task_ref> <run-id>
```

**成功陈述三分（v2.6.0）**：auto 启动成功的汇报必须区分三个独立事实——(a) **auto 授权已落**（mode=auto + 有界预算）；(b) **地板载体已绑定**（真实 automation id 入 state + preflight PASS）；(c) **精度睡眠已实际发出**（宿主接受后台进程）。三者缺一如实陈述缺项：缺 (c) 即 **floor-only 可见降级**（只有地板兜底在臂），绝不是「精度层已武装」；缺 (b) 即 auto 阻断（preflight ready=false 不开工）。

地板载体创建失败三禁不变：**绝不谎称已启用**、**绝不绑假 id**、**绝不按 auto 承诺继续**（本轮降级为仅手动连续性，或交运营者处理）。

### 统一唤醒协议（睡眠退出 / 地板触发同协议；收尾必续臂）

唤醒轮第一动作永远是幂等决策，分支树固定（逐字执行；**动作优先，绝不枚举工具清单**——见「唤醒 prompt 设计规则」）：

```
quota-resume-decision <repo_root> <task_id> --force-refresh（醒来首动作；
│ v2.6.0 起任何 action 的 decision 都无条件透传本次观测 reset_at，含 no-op）
├ resume-authorized 且 preflight PASS（quota-continuity-preflight）：
│     ResumeWorkflowRun（同一 run id）→ 宿主接受后
│     quota-resume-confirm <repo_root> <task_id> → 继续工作
├ active 且 run 健康（GetWorkflowRun 可查、在推进）：no-op 收轮
├ active 且 run 停摆（静默耗尽自愈路径）：按恢复处理——run 可 resume
│     则同上 resume；否则以仓库现状重建认知后重派发（绝不重放已完成节点）
├ remain-waiting（EXHAUSTED/UNKNOWN）：等待收轮（reset_at 照常透传，
│     供续臂目标换算）
├ waiting-user（未授权/预算耗尽）：no-op（等用户，绝不弹问）
└ terminal：no-op 实施，走终态清理
收尾动作（各分支处理完，凡非终态且仍授权 auto 的任务）：
    核查在臂睡眠：已有指向未来窗口的在臂睡眠即不重发（避免重复睡眠）；
    无在臂、目标已过或陈旧时，发下一个精度睡眠到（本次观测 reset_at + 300s
    ——当前观测的下一未来窗口边界）——滚动续臂；reset_at 为 null 或已过去
    → 不发（无负值 sleep、无忙旋），如实报告 floor-only 状态，等待地板节奏
```

**唤醒 prompt 必带**（自足稳定标识，凭仓库真相即可恢复；模板见 references/long-horizon.md）：精确 task_id 与仓库根；先载任务态（state.json + checkpoint）再行动；唤醒时刷新额度（decision 自带新鲜观测）；preflight 先行；按任务状态取精确 run id 同 id resume；decision=resume-authorized **且** preflight ready 才 ResumeWorkflowRun；宿主接受后 quota-resume-confirm 落账；收尾必核查在臂睡眠并滚动续臂（凡非终态且仍授权 auto；decision 的 reset_at 含 no-op 分支照常透传；陈旧/过去目标不发睡眠、不自旋）；终态走清理。**绝不嵌入**完整 workflow 源码、秘密/凭证、过期额度假设、工具清单枚举。

**唤醒 prompt 设计规则（去 priming，动作优先）**：唤醒 prompt 恒以**第一个要执行的动作**开头（quota-resume-decision），**绝不以可用工具清单开场、绝不通篇罗列工具名**——宿主发射退化与工具清单 priming 存在相关嫌疑（探针实录），动作优先写法同时消除歧义与 priming 面。

### 唤醒轮次的边界事实

- **唤醒轮次是宿主会话中途续入**：触发时宿主把 prompt 作为新 turn 注入既有会话（不是全新上下文的独立代理）——唤醒轮次能看到会话既有上下文，但这不构成恢复依据：恢复仍必须走仓库事实与 state.json（repository > checkpoint > 会话记忆），防止用陈旧会话记忆跳过对账。
- **唤醒轮 / 恢复轮绝不弹问**：任务启动问答（orchestration §6.0）只发生在交互回合；无人值守回合遇到任何本应问答才能解除的阻塞（如 pin 失效、预算耗尽）一律 fail closed——按对应等待态停泊并转等待用户，绝不以弹问制造死锁。
- **幂等决策契约**：每次唤醒固定以 `quota-resume-decision <repo_root> <task_id> --force-refresh` 开场——重复唤醒安全（非 waiting_quota 一律 no-op 零副作用；auto 未武装拒绝给出 resume-authorized，`continuity-unarmed`）；**任何 action 的 decision 都无条件透传本次观测 reset_at（含 no-op）**；resume-authorized 且 preflight ready 才同 id resume，宿主接受后立即 `quota-resume-confirm` 落账；未确认的决策绝不消耗预算。
- **reset_at 不是恢复授权**：决策透传的 reset_at 只是当前观测的下一未来窗口边界（安全的唤醒 / 检查点时刻）——resume 仍只由 decision 封闭四词（`resume-authorized`）与 preflight 硬闸决定；waiting_user / 终态任务绝不因 reset 到点而开新工。
- **唤醒轮次的工作量要小**：做一轮检查、恢复一段工作，不把剩余工作塞进单次唤醒。
- runtime 自身零宿主调度调用：Scheduled Task / 后台睡眠的创建等宿主动作由主会话执行；Conductor 只提供幂等决策与武装预检原语。

### 停靠轮（额度耗尽被观测、回合仍在飞行）

```
quota-wait <repo_root> <task_id> [--force-refresh]
-> quota-resume-decision <repo_root> <task_id> --force-refresh
-> 核查精度层在臂情况：已有指向未来窗口的在臂睡眠即不重发；观测 reset_at
   漂移（在臂目标已过 / 陈旧）则补发新睡眠到当前观测 reset_at + 300s
-> 收轮（等待期连续性已由在臂睡眠 + 地板承载）
```

**停靠轮边界（v2.6.0）**：停靠轮只做核查、补发与纠偏，**绝不承担建立首个睡眠**——首个睡眠由启动轮武装（授权确认即同批建立）；停靠轮迟到或缺失时连续性由在臂睡眠 + 地板承载，不靠「来得及停靠」。

**静默耗尽边界**：额度若死在回合之间（无停靠轮发生），启动 / 上次唤醒已武装的待命睡眠恰好覆盖下一个窗口边界——无需任何新动作。这是精度睡眠**启动即武装**的根本理由：建立动作不依赖「来得及停靠」。

### 地板间隔的权衡（为什么默认 1h、为什么进问答）

地板是纯兜底（精度由睡眠层承担），间隔只是「兜底响应速度 vs 工作期监测成本」的权衡：

- 更短 ⇒ 兜底恢复更快（迟到上限 = 一个间隔），工作期 no-op 唤醒更密（每次一个小模型轮）；
- **不得等于窗口长度（5h 整）**：会与 reset 秒级竞速（唤醒调用锚定下一窗 → 下次触发恒早几秒 → 无响应 → 错过整窗）——间隔显著偏离窗口长度即可（1h 远离 5h，无竞速）；
- 默认 1h：兜底迟到均值 ~30 分钟、工作期每小时一次 no-op——比长间隔方案的稳定迟到更早接住恢复；
- 间隔因任务而异（重要任务 1h、经济任务 4h 皆合理）——故作为**启动问答第三问**由用户逐任务确认。

### 精度特征与自愈（诚实账本）

- 精度层在臂期间，窗口边界唤醒**秒级精确**（端到端彩排实测零误差 + 8-11s 唤起延迟）；等待期触发零额度成本（睡眠期无模型轮）；
- 工作期续臂成本 ≈ 每窗口边界（~5h）一次小唤醒（no-op + 续臂）；
- 仅地板承载时（应用重启杀掉全部睡眠），恢复迟到 0-1 个间隔（默认 1h 间隔下均值 ~30 分钟）；恢复后**首个地板唤醒即重臂精度睡眠**（自愈闭环）；
- 重复睡眠（启动臂 + 停靠补发并存）由决策幂等自然吸收（早醒无害）。

### 边界写实（D8）

**一会话一 automation ⇒ 主会话同时至多一个 auto 任务**：宿主按会话归属拒绝第二个 automation（实测报文口径）。需要并行多个跨额度长任务时，把额外任务放独立会话（宿主自身的建议路径），或逐任务串行。

### 载体终身不变（INV-CONT-03）

载体创建后**不再有任何变更动作**：不 CronUpdate、不 CronDelete、不重建——地板按问答确认的间隔活到终态清理；精度全部由正交的睡眠通道承载（对 Cron 面零依赖）。三态规律（响后变更失效）因此**永远不会咬到**：设计里没有触发后变更。

## Checkpoint（导航状态）

长任务在实质性里程碑后（不是每次工具调用后）把 CONTINUITY CHECKPOINT 写入任务目录 `checkpoint.md`：记录 TASK_ID / GOAL / ROUTE / COMPLETED / CURRENT STATE / NEXT ACTION / VERIFICATION / OPEN RISKS。原则：

- checkpoint 是导航状态，不是仓库真相源——不复制完整 diff、不复制大量代码、不声称未验证内容
- repository 状态始终优先：checkpoint 与仓库冲突时以仓库为准
- 模板与八步恢复程序见 references/long-horizon.md

## 终态清理

任务到达终态（completed / cancelled / failed）并验收后的清理次序固定（只作用于本任务）：

1. **先删后清，次序固定；删除按三态规律预期失败（D7 写实）**：对本任务绑定的 automation（以 state 里记录的 automation_id 定位）做宿主侧**单次**删除尝试——**预期结果即失败**：载体一旦触发过，该会话的 Cron 删除发射会静默退化（三态规律，活体实测），模型侧无法完成删除。删除失败不是错误：**如实上报 automation id**（连同标题），**用户在 Automations 页面手动删除是预期运营动作**。
2. **用户确认手动删除后才清绑定**：确认后 `quota-automation-clear <repo_root> <task_id> [automation_id]` 清除绑定 id；未确认前绑定 id 保留待对账，绝不假装已清理干净。
3. 无重试风暴：删除尝试即止，绝不反复重试；重复唤醒 / 重复收尾遇到已清理状态一律幂等跳过。
4. **绝不删他人 automation**：只删本任务绑定的这一个；删除全局调度面或其他任务的 automation 是禁止操作。
5. 删除本任务目录 `.glm-conductor/tasks/<task-id>/`（仅此目录，不得触碰其他任务的状态或视觉证据）；既有清理流程删除任务目录时，次序绝不在宿主删除尝试之前抹掉 automation_id。
6. 向用户报告最终状态（含 auto 连续性武装的最终处置结果与待手动删除的 automation id）。

## 额度观测面（只读）

- `quota-resolve <repo_root> [--force-refresh]`（runtime CLI）：四态解析，输出冻结键 `{status, source, evaluated_at, reason}`；层级 = 新鲜缓存 → provider → 陈旧缓存 → UNKNOWN（绝不默认 AVAILABLE、绝不重试网络）；观测面零任务转态
- `quota-wait / quota-resume-authorize / quota-resume-decision / quota-resume-confirm`（runtime CLI）：额度等待 / 恢复生命周期操作面，规范用法见上「waiting_quota」与「manual / auto 有界恢复」两节；决策落盘只在任务侧，绝不调用宿主 ResumeWorkflowRun。**reset_at 语义（v2.6.0 单一观测口径）**：quota-wait / quota-resume-decision 观测 view 的 reset_at 与 decision 透传的 reset_at（**任何 action 无条件透传，含 no-op**）均为**已识别窗口中最早的有效未来观测 reset_at**（UTC instant 显式解析比较，与窗口 AVAILABLE/PRESSURE/EXHAUSTED/UNKNOWN 状态无关；已过期 / 无效 reset 不参与；无有效未来观测 → null，如实陈述 floor-only 降级，绝不虚构）——额度状态只管可否继续 / 恢复，不管可否透传时间；下一个观测到的窗口边界就是安全的唤醒 / 检查点，醒来重观测重续臂、迭代收敛。睡眠目标换算一律以此值为准（+300s 宽限）。`plan_resume` 的 EXHAUSTED 保守 resume_at 口径不变，属另一条独立决策路径，不参与 reset_at 透传与武装
- `quota-automation-bind / quota-automation-clear / quota-continuity-preflight`（runtime CLI）：auto 连续性武装操作面（bind / clear / preflight），规范用法见上「arm-before-work」节；preflight ready=false 以退出码 2 报告（launch/resume 阻断信号）
- `runtime/quota/report.py [--json]`：人读诊断（窗口明细 + status + 恢复建议），详见 `/glm-conductor:quota` 命令
- **凭证**：环境变量 `GLM_CONDUCTOR_QUOTA_API_KEY` 优先，已登录 ZCode 的 `~/.zcode/v2/config.json` provider 配置为文档化回退；凭证零落盘（不进 state.json / events.jsonl / checkpoint / 日志 / 任何输出）；凭证不可得 → UNKNOWN，不虚构
- **lite 套餐**：无周窗（仅 5 小时窗）——report 明细按实际存在窗口输出；解析与决策不依赖周窗存在
- **不变边界**：原生插件级 quota API（getQuotaRemaining / getQuotaResetTime / onQuotaReset）仍不存在，不得虚构；不硬编码重置周期；额度观测只作为证据使用，不作为路由轴；不实现常驻轮询——查询只发生在刷新点

## Failure Handling

- **定时能力不可用**：auto 连续性保持未武装（preflight ready=false）——不得声称已启用连续性、绝不绑假 id、绝不按 auto 承诺继续；保留 checkpoint 与 state，向用户报告"手动可恢复"及恢复方法（新建会话即得 SessionStart 恢复注入）
- **唤醒发现 auto 未武装**（绑定丢失 / 宿主激活已消失）：按「arm-before-work」节的单次重建序列处理（CronCreate → bind → preflight；会话归属拒绝时如实报告并依赖宿主既有节奏）；绝不虚构 activation、绝不无武装 resume
- **精度睡眠不在臂**（应用重启 / 会话重建后）：任何唤醒轮（含地板触发）收尾时按统一协议续臂即自愈——不视为故障
- **Workflow 不可 resume**：按「Workflow 恢复」第 4 步以仓库现状重建，不重放已完成工作
- **授权预算耗尽**：任务停在 waiting_user——等用户重新授权（`quota-resume-authorize`）或显式收尾；模型不得自行调高预算
- **v2.3 遗留任务**：按 `LEGACY_STATE_GUIDANCE` 报告处置指引，不迁移、不伪造完成证据
