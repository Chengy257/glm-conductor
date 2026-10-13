# 长周期任务运行机制

本文件是 continuity 技能的运行机制文档，SKILL.md 引用本文件获取生命周期、checkpoint 模板与恢复流程细节。

## Lifecycle

```
任务开始（orchestration 声明 SELECTIVE ROUTE，§6.0 单轮三问问答，
   task-create 落 state.json）
   |
   v
[武装]（仅 auto/until_done；arm-before-work + 建立动作优先：问答确认
   即同批建立，全部先于 CreateWorkflow——quota-resume-authorize
   -> 立即 quota-resume-decision --force-refresh 取观测 reset_at（任何
   额度状态都可观测；单一观测口径：最早的有效未来观测 reset，与额度
   状态无关，active/no-op 分支同样透传）-> 宿主 CronCreate 地板载体
   （intervalUnit=hourly, interval=问答第三问确认值（默认 1）,
   recurring=true，终身不变）-> quota-automation-bind
   -> quota-continuity-preflight PASS -> 存在有效未来 reset 时发首个
   精度睡眠到观测 reset_at+300s（睡眠模板见下「精度层睡眠模板」；
   reset_at 为 null → 不发、如实陈述 floor-only 降级，绝不谎称已武装；
   创建失败绝不谎称已启用）
   |
   v
[执行] delegate/full → 原生 Workflow 运行（显式 Flash 档模型选型 ->
   CreateWorkflow(subagent_model=选型返回值) -> v24-record-run 绑 run id）
   |
   v（实质性里程碑 -> 写 CONTINUITY CHECKPOINT）
   |
   v（中断：会话结束 / 额度耗尽 -> waiting_quota / Workflow 停摆）
[再激活]（SessionStart 恢复注入 / 双通道唤醒——精度睡眠退出或地板触发，
   同一唤醒协议 / 用户手动恢复）
   |
   v
八步恢复检查（repository > checkpoint > 会话记忆）
   |
   +-- 目标已完成 -> 终态清理（删除尝试按三态规律预期失败 -> 如实上报
   |     automation id -> 用户手动删除 -> 确认后才清绑定，再删本任务目录）
   +-- 可恢复 -> 同 run id resume（额度可用 + 授权 + 预算有余 + auto 已武装/preflight ready 时）
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

若 ZCode 运行时实际暴露了以下标识，可选择性附加记录；不暴露就不写，**不得虚构**。automation 关联的权威落点是 state.json 的 `quota_resume.automation_id`（auto 连续性武装见证，bind/clear 经 CLI 操作）；checkpoint 里的 AUTOMATION_ID 只是导航提示，两者不一致时以 state.json 为准：

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
| quota_resume | 恢复授权块（mode / max_resumes / resume_count / automation_id [+ 诊断性 last_observation]）；automation_id 是当前关联的未来原生 Scheduled Task 见证——mode=auto 时它非空才算 armed（preflight 的必要条件），其存在不独立证明宿主侧激活仍存活 |

status 只前进不回退（`state.TASK_TRANSITIONS` 矩阵校验）；终态不得静默重开（显式重开走 `state.reopen_task`）；repository 仍是代码状态真相源，state.json 只是运行时任务状态。

events.jsonl 任务级事件词汇恰十个（`runtime.task.TASK_JOURNAL_EVENTS`）：route_selected / workflow_started / workflow_reassessed / validation_recorded / review_recorded / waiting_quota / quota_resume_confirmed / task_completed / task_failed / task_cancelled——逐事件时机见 SKILL.md「任务状态与运行时文件」节；追加一律通过 `runtime/journal.py`（时间戳由模块管理，调用方不得自带）。

### 恢复时的读取顺序

八步恢复中，第 2 步读取 checkpoint 的同时读取 state.json、并查看 events.jsonl 尾部：

1. **state.json**（机器可读）：恢复 status、route、workflow_run_id、validation / review 认知——若存在且非终态，本任务仍是 active task，恢复后仍受完成守卫跟踪；quota_resume 块显示授权、预算与武装现状（mode=auto 时 automation_id 为空即未武装：先重建激活并 bind + preflight PASS 才许自动 resume）
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
8. **从 NEXT ACTION 恢复**：重新输出 SELECTIVE ROUTE 声明后，从 NEXT ACTION 继续执行（委派任务先走「同 run id resume」，见 SKILL.md；auto 唤醒路径必经幂等决策 `resume-authorized` **且** `quota-continuity-preflight` ready 两道硬闸才许 ResumeWorkflowRun；收尾必核查在臂睡眠并滚动续臂——对非终态仍授权 auto 的任务，发/续下一个精度睡眠到本次观测 reset_at+300s——当前观测的下一未来窗口边界；陈旧/过去目标不发睡眠、不自旋）

两条禁令：

- 禁止唤醒后盲目重播旧指令
- 禁止为恢复 checkpoint 回滚仓库新改动——checkpoint 与仓库不一致时，分析变化来源、更新认知、以仓库为准

## 结构化 Resume Prompt（地板载体触发内容）

以下 prompt 用作地板 recurring Scheduled Task 的触发内容。它必须自包含：不依赖任何会话上下文，凭此 prompt + 指定任务状态 + 仓库状态即可恢复。**占位符必须在创建载体时替换为真实值**。prompt 只携带稳定标识与恢复指令——绝不嵌入完整 workflow 源码、秘密 / 凭证、过期额度假设（额度在唤醒时现测）、**绝不枚举工具清单**（动作优先，去 priming）：

```
Resume GLM Conductor task. First action: run the idempotent wake decision.

TASK_ID: <id>
REPOSITORY ROOT: <repo_root>
TASK DIR: .glm-conductor/tasks/<id>/
CHECKPOINT: .glm-conductor/tasks/<id>/checkpoint.md

First load the current task state (.glm-conductor/tasks/<id>/state.json),
the specified checkpoint, and the current repository state. Load task
state before taking any action.

Run the wake decision first, with a fresh quota observation:
cli.py quota-resume-decision <repo_root> <task_id> --force-refresh

Then follow the unified wake protocol by that decision:
- resume-authorized AND quota-continuity-preflight ready: resume the
  exact workflow_run_id from task state via the host resume action,
  then record it via cli.py quota-resume-confirm <repo_root> <task_id>
  once the host accepts.
- active with a healthy run: no-op.
- active with a stalled run: treat as recovery — resume if the run is
  resumable, otherwise rebuild from repository reality; never replay
  finished work.
- remain-waiting: wait (the decision output carries this observation's
  reset_at for the re-arm target — every action, including no-op,
  carries the currently observed earliest valid future reset).
- waiting-user or terminal: no-op (terminal proceeds to cleanup).

Unless the task is terminal (and only for a still-authorized auto
task), finish this wake turn by checking whether a suitable precision
sleep is already in flight; if none is, or its target is stale or
already past, re-arm one: copy the background sleep template verbatim
from the continuity long-horizon contract, targeting the reset_at
from this turn's decision output (the earliest valid future observed
reset, independent of quota status) plus 300 seconds. If reset_at is
null or already past, skip the sleep — never spin, never fabricate a
target — and disclose floor-only degraded precision honestly instead
of claiming the precision layer is armed. The first sleep is armed at
launch, before any work; a parking or wake turn only inspects, re-arms
or corrects it, and never establishes the first sleep. After a host or
app restart drops all background sleeps, the recurring floor wake
re-arms the precision sleep (self-healing) — never present a floor
trigger as a precision-time wake. Never compute the delay yourself —
the template computes it in bash.

The recurring floor automation bound to this task stays in place for
its whole lifetime — never update, delete, or recreate it from a wake
turn.

Authorizations recorded by the launch interview hold for the whole task
lifetime: auto continuity was enabled with a bounded max_resumes budget,
and the worker model pin (worker_model in task state) is the
user-authorized task-level default for any new workflow submission — a
preference, not the task identity. Consume it via workflow-model-select
--task-ref against the current host model list; if the pin is no longer
on the list, the selection is rejected: block the submission and wait
for the user. Never fall back to auto-selection, never switch models
silently, and NEVER ask questions on an unattended wake/recovery turn.

Repository state is authoritative over checkpoint state.

Continue under the stored route, ownership, validation, and review
contracts; re-declare SELECTIVE ROUTE before resuming execution.

Perform ROUTE REASSESSMENT only when newly observed evidence changes
delegability or assurance.
```

## 精度层睡眠模板（抄写式——bash 动态计算秒数，模型零算术）

启动轮武装与每次唤醒轮 / 停靠轮收尾（凡非终态且仍授权 auto）时，把 `<reset_at>` 代换为**本次观测 reset_at**（quota-resume-decision 输出的 `decision.reset_at`——v2.6.0 起任何 action 无条件透传、含 no-op，ISO 字符串，如 `2026-10-11T14:33:42Z`；单一观测口径：已识别窗口中最早的有效未来观测 reset，与额度状态无关），以 Bash 工具 **run_in_background 后台**逐字执行：

```
echo "precision-sleep until <reset_at>+300s, launched $(date -u +%Y-%m-%dT%H:%M:%SZ)"; sleep $(( $(date -u -d "<reset_at>" +%s) + 300 - $(date -u +%s) )); echo "precision-wake $(date -u +%Y-%m-%dT%H:%M:%SZ)"
```

模板纪律：

- **模型零算术**：目标秒数由 bash 现算（`date -u -d` 解析 ISO 时刻 + 300 宽限 − 当前时刻），模型绝不自己换算毫秒 / 分钟 / 时区
- **后台运行**：以 run_in_background 发起——任务退出自动唤起会话（实测秒级精确 + 8-11s 唤起延迟）；等待期零模型轮、零额度消耗
- **reset_at 为 null / 缺失**（无有效未来观测）→ 不发睡眠，如实陈述 floor-only 降级（只有地板在臂），等待地板节奏——绝不虚构目标
- **差值 <= 0**（目标已过）→ 不发睡眠（无负值 sleep、无忙旋——陈旧/过去目标不得自旋），本轮按决策处理（额度已恢复则走 resume 路径）
- **重复睡眠无害**：启动臂与停靠补发并存由决策幂等吸收（早醒 → 新观测 → 新决策）
- **不抗应用重启**：重启杀掉睡眠后由地板兜底，首个地板唤醒收尾续臂即自愈——不是故障

## Scheduled Trigger（双通道：地板载体 + 精度层睡眠）

无人值守恢复由两条正交通道承载（v2.5.2；宿主事实与活体实测证据见 `docs/reviews/WAKE_TOPOLOGY_PROBE_RESULTS.md`）：

- **地板载体**：启动即建的 recurring 原生 Scheduled Task——间隔经启动问答第三问确认（默认 1h，不得等于窗口长度 5h——会与 reset 秒级竞速），抗应用重启，**创建后终身不变**（不删、不改、不重建，INV-CONT-03）；工作期每次触发通常是一次 no-op 小唤醒（决策幂等吸收）
- **精度层睡眠**：上方模板的后台 sleep——睡到观测 reset_at + 300s（最早的有效未来观测复位时刻，与额度状态无关；无有效未来观测 → 精度层缺席，floor-only 降级如实披露），退出即唤起；与 automation 状态机完全正交（零 Cron 交互）；不抗重启（地板兜底）
- **触发内容** = 上方结构化 resume prompt（已替换 TASK_ID、REPOSITORY ROOT 与路径占位符；动作优先，绝不枚举工具清单）
- **滚动续臂**：每次唤醒轮收尾（凡非终态且仍授权 auto）必核查在臂睡眠——无在臂或目标已过/陈旧时发下一个精度睡眠到**当前观测** reset_at+300s（最早的有效未来观测；醒来重观测重续臂、迭代收敛）——唤醒机制的建立 / 续臂动作永远先于工作推进；额度若死在回合之间（静默耗尽），在臂睡眠恰好覆盖下一窗口边界
- **下一窗口绝不硬编码推导**：只依据 provider 复位证据（观测 reset_at，单一观测口径——最早的有效未来观测，与额度状态无关）——不以「加 5 小时」推算下一次唤醒
- **每次唤醒任务量要小**：做一轮检查，然后恢复一段工作——避免单次唤醒塞满全部剩余工作

**调度触发即存活探针**：唤醒成功启动即说明模型执行当前可用；唤醒失败或未启动则不会产生任何仓库改动，自然等待下次触发或用户恢复。

**同会话投递事实**：宿主把唤醒 prompt 作为新 turn 注入既有会话（中途续入）——唤醒轮次可见会话既有上下文，但恢复依据仍然是仓库与 state（repository > checkpoint > 会话记忆）；跨会话自包含恢复（同一 prompt 语义）不依赖会话记忆，两种形态下行为一致。

不硬编码重置周期：额度政策可能变化，任何固定周期都会在政策变化后静默失效。唤醒时机只依赖"唤醒时重新检查"这一动作（新鲜额度观测 + 幂等决策 + preflight 硬闸），与具体政策解耦；额度不可知时等待用户或下次触发，不虚构可用性。

## Route Recovery

恢复的会话必须重新输出 SELECTIVE ROUTE 声明后再执行（详细规则见 orchestration 技能的"恢复会话的路由恢复"章节）；若证据显示双轴判断（Delegability × Assurance）已变化，先做 ROUTE REASSESSMENT。

## Completion Cleanup

目标完成并验收后的动作清单（只作用于本任务，次序固定——**先删后清；删除按三态规律预期失败**）：

1. 对本任务绑定的 automation（按 state 里记录的 automation_id 定位）做宿主侧删除尝试——**会话侧单次尝试**（绝不重试，无重试风暴）；**预期结果即失败**：载体一旦触发过，该会话的 Cron 删除发射会静默退化（三态规律，活体实测），模型侧无法完成删除。删除失败不是错误：**如实上报 automation id 与标题**，用户在 Automations 页面手动删除是**预期运营动作**
2. **用户确认手动删除后才** `quota-automation-clear` 清除绑定 id；未确认前绑定 id 保留待对账，绝不假装已清理干净
3. **绝不删他人 automation**：只删本任务绑定的这一个；删除全局调度面或其他任务的 automation 是禁止操作
4. 删除本任务目录 `.glm-conductor/tasks/<task-id>/`（仅此目录，不得触碰其他任务的状态）；既有清理流程删除任务目录时，次序绝不在宿主删除尝试之前抹掉 automation_id
5. 向用户报告最终状态（含待手动删除的 automation id）

宿主调度动作（创建 / 删除定时任务、发起后台睡眠）由主会话执行；runtime 自身零宿主调度调用。窗口/预算语义与 automation 存活解耦：授权与计数只发生在 `authorize_quota_resume` / `confirm_resume_started` 两个落盘点，武装与解绑只发生在 `bind_quota_automation` / `clear_quota_automation` 两个绑定点。

## Failure Cases

- **定时能力不可用**：auto 连续性保持未武装（`quota-continuity-preflight` ready=false）——不得声称已启用连续性、绝不绑假 id、绝不按 auto/until_done 承诺继续；保留本任务 checkpoint 目录，向用户报告手动可恢复，并附恢复方法——新建会话输入"读取 .glm-conductor/tasks/<task-id>/checkpoint.md 并按八步恢复流程继续"（SessionStart 恢复注入会自动提示）
- **唤醒发现 auto 未武装**（绑定丢失 / 宿主激活已消失）：单次重建尝试（宿主 CronCreate 地板 → bind → preflight PASS）；创建被会话归属拒绝（会话名下其实仍有存活载体、只是账本见证缺失）→ 按现状报告并依赖宿主节奏，绝不重试风暴；绝不虚构 activation、绝不无武装 resume
- **精度睡眠不在臂**（应用重启 / 会话重建后）：任何唤醒轮收尾按模板续臂即自愈——不视为故障
- **唤醒发现任务已终态**：走终态清理（删除尝试预期失败 → 上报 automation id 交用户手动删除），不重开实施
- **实施通道缺失**（如 visual-implementer 不可用）：fail-closed，不自动换成其他执行者，交回用户处理
- **唤醒后 checkpoint 与仓库严重不一致**：以仓库为准，向用户报告差异后继续；不得回滚仓库新改动
- **Workflow run 不可 resume**：宿主报告 run 不存在或不可恢复时，按 SKILL.md「Workflow 恢复」第 4 步以仓库现状重建（未实施节点重新编译提交），绝不重放已完成工作

## ZCode Limitations

- **无原生 quota API**：插件级接口（getQuotaRemaining / getQuotaResetTime / onQuotaReset）均不存在，不得虚构；额度观测走只读解析面（SKILL.md「额度观测面」节），凭证不可得时按 UNKNOWN 处理
- **额度观察作为证据**：解析快照、用户告知或 UI 观察都只作为证据使用；不作为路由轴、不据此自动换模型或绕过授权
- **子智能体内不能再派生子智能体**：编排者只能是主会话
- **子智能体只能看到会话启动时已连接的 MCP 服务**：跨会话恢复后需重新确认所需服务可用
- **runtime 零宿主调度写入**：定时任务的创建 / 删除是主会话的宿主动作；Conductor 侧没有任何 scheduler 写入路径
