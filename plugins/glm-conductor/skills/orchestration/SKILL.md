---
name: orchestration
description: GLM 双轴选择性路由 + 原生 Workflow 编排。主会话任架构师，按 Delegability × Assurance 两个独立维度在首次任务委派前声明 SELECTIVE ROUTE（solo/delegate/audit/full）；任何实施任务启动必经一次强制任务启动问答（单轮三问、零模型判断：跨额度自动唤醒启用与否 + 有界 max_resumes 预算；worker 模型仅选型歧义时列全部候选；地板载体间隔默认 1h 可调）——答「启用」落 quota-resume-authorize 与双通道武装序列（地板 CronCreate + 首个精度睡眠，先于 CreateWorkflow），答出模型落 worker-model-pin 任务级 pin；delegate/full 经 v24-compile 把静态节点 DAG 确定性编译为宿主原生 Workflow 源码，由主会话以显式 Flash 档 subagent_model 提交执行——提交前必经宿主模型列表 → workflow-model-select 选型硬闸（Flash 档判据版本无关，--task-ref 自动取任务级 pin，pin 失效专门拒绝即停），选型失败绝不 CreateWorkflow、绝不省略 subagent_model、绝不继承主会话 GLM-5.3（writer-acquire 取仓库写预约 → 记录 run 关联 → 等待结构化结果）；实施完成后主会话亲自验证并经 record-validation 记录，assurance:high 时引入全新上下文的只读审查者并经 review-record 申报裁决；视觉任务走子代理例外通道。适用于构建并验证功能、多步骤交付、前端/视觉任务、需要委派实施或独立代码审查的任务。
---

# GLM 编排：双轴选择性路由 + 原生 Workflow

## 1. 角色与前置

本技能要求主会话为 GLM-5.3，担任架构师。以下职责保留在主会话，不外派：

- 需求与歧义解决
- 架构与路由判断
- 任务分解（静态 DAG 编制）
- 实施规格与节点声明编写
- 完整 diff 检查与验证重跑（主验证）
- 路由重估决策
- 最终验收与完成申报

子智能体只能实施或审查，不得成为新的编排者。若当前主会话模型不是 GLM-5.3，告知用户切换后再继续。

分工边界（v2.4 冻结）：GLM Conductor 拥有语义编排与验收（路由、DAG、验证、审查、完成守卫）；ZCode 拥有执行编排（Workflow 运行、子代理生命周期、调度）。Conductor 不镜像宿主执行状态。

## 2. 双轴路由判断

不要把所有状态压缩成一个风险等级。先独立回答两个问题，再查路由矩阵：

**轴 A — Delegability（可委派性）**：剩余的实施工作是否足够有界、规格足够完备，可以委派出去？

**轴 B — Assurance（保障等级）**：实施完成并通过主会话验证后，是否需要一次全新上下文的独立终审？

两轴独立判断，互不推导。判据全表见 references/role-contracts.md。

## 3. ROUTING PREFLIGHT（路由前置侦察）

PREFLIGHT 不是路由，是路由的证据准备：双轴判断前若关键事实未知，先用只读侦察补齐，不用弱证据强行提前定路由。

**触发条件**（以下任一未知即触发）：根因 / 实施边界 / ownership 集合 / 接口契约 / 验证方案 / 隐藏耦合风险。任务已经显式（上述全部已知）时跳过——不为侦察而侦察。

**执行方式**：优先用 ZCode 内置只读 Explore 能力，必要时主会话亲自读码；不新增自定义侦察代理。侦察只读，不改动仓库。

**产出**（侦察完成后、SELECTIVE ROUTE 声明前输出；模板逐字，条目全部来自实际观察——文件/符号/调用链证据，不得包含推测性事实；OPEN AMBIGUITY 留空表示无未决歧义）：

```
ROUTING PREFLIGHT REPORT

ROOT CAUSE:
...

RELEVANT FILES:
- ...

INTERFACES:
- ...

TEST ENTRYPOINTS:
- ...

HIDDEN COUPLING:
- ...

OPEN AMBIGUITY:
- ...
```

该报告直接喂给双轴判断（§2）、TASK CONTEXT PACK 与静态节点声明（§5 / references/role-contracts.md）。

## 4. SELECTIVE ROUTE 声明

在任何实施委派（CreateWorkflow 提交或子智能体调用）之前必须输出一次（原样保留代码块）：

```
SELECTIVE ROUTE
mode: solo | delegate | audit | full
delegability: low | high
assurance: standard | high
reason: <简明的、基于证据的理由>
```

示例：

```
SELECTIVE ROUTE
mode: delegate
delegability: high
assurance: standard
reason: implementation is bounded by explicit interfaces, owned files, and deterministic verification
```

声明之前不得委派任何实施。delegability 是轴 A 的判断记录；进入任务状态（state.json）的只有 mode 与 assurance 两键（`runtime.task.create_task` 摘取口径，v2.4 schema 不持久化其余轴）。

## 5. 任务状态与静态 DAG 编制

### 5.1 任务创建

需要完成守卫保护的任务（delegate/full、audit，以及任何跨回合的长任务）在声明 SELECTIVE ROUTE 后、经 §6.0 启动问答创建任务状态：

- 入口（CLI 具名命令）：`cli.py task-create <repo_root> <task_id> <goal> <mode> <assurance> [--dag <path>]`（校验后落 `.glm-conductor/tasks/<task-id>/state.json` + journal `route_selected` 事件；route 只存 mode 与 assurance 两键；--dag 读 DAG JSON 文件，兼容 v24-compile 同款格式）
- TASK_ID 用「语义前缀 + 随机十六进制后缀」机械唯一格式（规则见 continuity 技能）
- 启动问答（§6.0）对任何实施任务强制——纯会话内小任务同样被问（零判断的已接受代价）；其中问答确认无需守卫的任务可不建 state.json（问答答「不启用」且路由为 solo 时自然如此）——无状态文件时完成守卫零干预

### 5.2 静态节点（Work Unit = 编译期声明）

delegate/full 需要多段实施时，把任务分解为静态节点 DAG。节点只声明编译期事实，零运行时状态（`runtime/work_unit.py` 为权威形状）：

| 节点字段 | 必填 | 语义（五段语义的落点） |
| --- | --- | --- |
| `id` | 必填 | 节点标识，单 DAG 内唯一 |
| `objective` | 必填 | 有界实施目标（对应五段规格 OBJECTIVE） |
| `depends_on` | 必填 | 前置节点 id 数组——纯 DAG：禁止环、自依赖、缺失引用 |
| `ownership` | 必填 | 仓库相对路径 scope 数组，非空（对应 FILES AND OWNERSHIP） |
| `interfaces` | 可选 | 须保持兼容的接口面 |
| `constraints` | 可选 | 额外约束（仓库规范、安全边界、排除范围） |
| `local_check` | 可选 | 本地检查命令（worker 诚实执行，为空不虚构） |

编制纪律：

- **每个节点必须仍然小到能接收一个完整的有界 objective**——需要再拆说明分解不足；无文件范围（ownership 空）的节点不可编译
- 依赖只表达编译期顺序事实；编译器把无依赖冲突的节点并入同一阶段并行、其余串行（`ownership.plan_stages`：scope 明确重叠的候选确定性串行，歧义冲突直接拒绝）
- **两个并发实施的节点 ownership 不得重叠**——这是唯一需要的并行安全声明；写相互斥由仓库写者守卫（§6）兜底
- 五段式实施规格模板（OBJECTIVE / FILES AND OWNERSHIP / INTERFACES / CONSTRAINTS / VERIFICATION）仍是节点声明的思维框架与视觉通道的交付格式，见 references/role-contracts.md
- **节点对应交付物，不对应实施步骤（编制反模式）**：objective 只能用「交付了什么」表述；写不出交付物、只列得出步骤序（先 / 再 / 然后）的是步骤型节点，应向上合并；节点内的实施顺序由 worker 自主决定

## 6. delegate/full 的执行程序（原生 Workflow）

delegate/full 的唯一实施基底是 ZCode 原生 Workflow。主会话按以下固定顺序执行（运行时调用一律走 CLI：`python3 plugins/glm-conductor/runtime/cli.py <子命令>`，退出码 0=成功 / 2=校验拒绝 / 1=运行期拒绝；禁止 `python3 -c` 拼复杂内联脚本）：

### 6.0 任务启动问答（launch interview，v2.5.2）

**触发点在 create_task 之前，触发零模型判断（v2.5.2 D3 强制化）**：凡将启动实施的任务**必问一次**——任何实施任务都问，**没有豁免条款**（v2.5 的「纯会话内小任务不问」豁免已删除：零判断的代价是极小任务也被问一次，已接受）。问答是实施启动的强制入口，不是可选礼貌；唤醒轮 / 恢复轮绝不弹问（见节尾）。

**单轮三问（AskUserQuestion 单轮三问合并，绝不拆成多轮）**：

1. **跨额度自动唤醒**：启用 / 不启用。启用时同轮选定**有界 max_resumes 预算选项**（预算必须显式有界，绝不无界授权）；不启用即 manual 连续性（额度耗尽即停，等用户回来）。
2. **worker 模型**：**仅当选型歧义**（宿主模型列表上有多个 provider 限定 Flash 档候选，跨版本代际同算歧义）时出现——列出全部候选精确 id 与各自的计划语义提示（该模型对应的套餐 / 窗口语义），由用户选定；恰一候选时自动选定、此题不出现。
3. **地板载体间隔**：跨额度唤醒启用时确认地板 recurring 载体的间隔——默认 **1h**（推荐；兜底迟到均值 ~30 分钟），可调（如重要任务 1h、经济任务 4h；**不得等于窗口长度 5h 整**，会与 reset 秒级竞速）。第一题答「不启用」时此题不出现。

**答后落盘（三题各自独立落）**：

- 第一题答「启用」→ `quota-resume-authorize <repo_root> <task_id> <max_resumes>`，随后立即走双通道武装序列（§6.2 auto 分支 + continuity「双通道唤醒」节；授权成立后立即 `quota-resume-decision --force-refresh` 取观测 reset_at——任何额度状态都可观测，单一观测口径：最早的有效未来观测 reset，与额度状态无关；地板载体同批建立，存在有效未来 reset 时首个精度睡眠同批发出，全部先于 CreateWorkflow，INV-CONT-01）
- 第二题选定模型 → `worker-model-pin <repo_root> <task_id> <exact-id>`——写入任务级 worker 模型 pin（state 顶层键 `worker_model`）。**pin 是用户授权的任务级偏好，不是任务身份**（与 §6.1 第 8 条的区分见该节），任务全生命周期默认使用（含唤醒恢复后的新 run 提交）；写入只做形状校验、不查宿主面，消费必查宿主面（§6.1）
- 第三题确认值 → 地板 CronCreate 的 `interval` 参数（小时整数；默认 1）——问答确认值即载体终身间隔，创建后不再变更

**交互回合边界**：问答只发生在交互回合；**唤醒轮 / 恢复轮绝不弹问**——无人值守回合遇到本应问答才能解除的阻塞（如 pin 失效）一律 fail closed：阻断提交、转等待用户，绝不以弹问制造死锁。

**与 §6.1 的衔接**：第二题只在选型歧义时出现；歧义分支的完整接续（问答 → pin → `workflow-model-select --task-ref` 自动取 pin）见 §6.1。

### 6.1 worker 模型选型硬闸（INV-MODEL-01）

每次 CreateWorkflow 提交前必须完成显式 worker 模型选型。这是强制序列而非可选建议——前一步失败即停，绝不带病进入下一步：

1. **调用宿主模型列表**：ListModels 等宿主模型发现面，取已配置模型全集（模型发现归宿主，runtime 绝不自建模型清单）；**推荐路径：把列表写成临时 JSON 文件再传文件路径**（内联数组仅短列表便捷形态）
2. **提取配置精确 id**：只保留 provider 全限定 `account:<账户>/<模型>` 形态的精确 id（裸别名不可选）
3. **跑 workflow-model-select**：`cli.py workflow-model-select <models-json> [--model <exact-id>] [--task-ref <task-id>] [--repo-root <path>]`（models-json 传文件路径或内联数组；--repo-root 显式解析任务账本根，缺省当前工作目录）——恰一 provider 限定 Flash 档候选时自动选定（**Flash 档判据版本无关**：模型段以 `-Flash` 结尾，GLM-6-Flash 等未来代际同档模型开箱命中）；零候选拒绝；多候选（选型歧义，跨版本代际同算）时按固定优先级取值：`--model` 显式值 > 任务 pin > 自动选型。任务 pin 由 `--task-ref` 从 §6.0 问答落盘的 `worker_model` 读取，命中即自动作选定 id；无显式值亦无任务 pin 时歧义即拒（绝不静默取第一个），交互回合转 §6.0 问答第二题接续：**问答 → `worker-model-pin` 落盘 → 后续提交带 `--task-ref` 自动取 pin**
4. **选型失败绝不 CreateWorkflow**：校验拒绝（退出码 2）是硬闸——报告原因并停止本次委派；绝不猜测回退、绝不静默改用其他模型
5. **以返回的精确 subagent_model 提交**：CreateWorkflow 的 `subagent_model` 逐字取选型返回值（provider 全限定、模型段以 `-Flash` 结尾的 Flash 档模型）
6. **绝不省略 subagent_model**——省略即继承主会话模型，是 v2.4.0 缺陷 A 的直接复现
7. **绝不替换主会话 GLM-5.3**——非 Flash 档模型（含 `GLM-5.3` 等主会话文本模型）绝不可用作 worker；绝不虚构模型 id
8. **诊断可报、身份不落**：诊断输出可报告实际选定的模型 id（选型成功返回 `{subagent_model, model_policy: "explicit-flash-required"}` 提交契约），但模型选型不是任务身份——绝不把选定模型写入 state.json / journal 当作任务标识

**pin 的消费语义（失效即拒）**：任务 pin 的写入不查宿主面，消费必查宿主面——每次 `workflow-model-select --task-ref` 对账当次宿主模型列表，pin 不在列表即**专门拒绝**（退出码 2：「pin 已失效，请重新问答授权」），绝不回退自动选型、绝不静默换模型。候选漂移由此天然安全：新候选不自动采用，旧 pin 过期不自动替换。pin 失效属选型失败，同受第 4 条硬闸约束；交互回合可重新问答授权，唤醒轮 / 恢复轮绝不弹问——阻断提交、转等待用户。

**第 8 条与任务 pin 的区分**：第 8 条禁止的是把模型选型结果当作**任务身份**（写入 state/journal 当任务标识）；`worker_model` pin 不是任务身份——它是用户在 §6.0 问答中显式授权的**任务级模型偏好**（任务全生命周期的 worker 模型默认值），任务的身份始终是 task_id。两者并行不悖：pin 恰是把选型决策从每次提交的临时动作升格为「一次授权、任务内默认」，第 8 条「诊断可报、身份不落」原样成立。

**编译器 TypeScript 模型无关（宿主绑定事实）**：v24-compile 生成的 TS 源不携带、也设置不了 worker 模型——`subagent_model` 是宿主在 run 提交（CreateWorkflow）时刻绑定的 run 级提交属性。因此模型选择只能发生在提交边界上：编译产物对任何 worker 模型成立，跑错模型的责任在提交动作，不在编译器。

### 6.2 启动序列（manual / auto 双分支，runbook 化）

运行时命令一律走 runtime CLI 具名子命令：`python3 plugins/glm-conductor/runtime/cli.py <子命令>`（退出码 0=成功 / 2=校验拒绝——含 preflight ready=false 阻断 / 1=运行期拒绝；**权威命令清单 = cli.py 顶部 USAGE**——模型不需要自己找命令，不允许发明 `python3 -c` 内联脚本，不允许使用 USAGE 之外的命令形态）。

**manual 连续性**（§6.0 问答第一题答「不启用」即落此分支——问答是两分支共同的强制入口，不是 auto 专属；第三题不出现）：

```
问答（§6.0 单轮三问，第一题答「不启用」）
-> task-create <repo_root> <task_id> <goal> <mode> <assurance> [--dag <path>]
-> v24-compile <dag-json> [--task-ref <id>] [--out <path>]
-> ListModels -> 写临时 JSON 文件
-> workflow-model-select <models文件> [--task-ref <task_id>] [--repo-root <repo_root>]
-> writer-acquire <repo_root> <task_id>
-> CreateWorkflow（subagent_model=选型返回值）
-> v24-record-run <task_ref> <run-id>
```

**auto 连续性（入口 = §6.0 问答确认启用后）**：第一题答「启用」即先落授权，预算在问答中一并选定；用户陈述只是授权意图，不是无界授权。**先武装后开工（arm-before-work，INV-CONT-01）+ 建立动作优先**：地板载体与首个精度睡眠在 CreateWorkflow 之前同批建立——完整唤醒契约（统一唤醒协议、停靠轮、续臂、终态清理）见 `skills/continuity`「双通道唤醒」节，本节是其编排侧入口：

```
问答（§6.0 单轮三问：第一题「启用」+预算；第三题确认地板间隔，默认 1h）
-> task-create <repo_root> <task_id> <goal> <mode> <assurance> [--dag <path>]
-> quota-resume-authorize <repo_root> <task_id> <max_resumes>
-> 立即 quota-resume-decision <repo_root> <task_id> --force-refresh 取观测
   reset_at（任何额度状态都可观测——单一观测口径：最早的有效未来观测 reset，
   与额度状态无关；active/no-op 分支同样透传，不要求任务在 waiting_quota）
-> 宿主 CronCreate 地板载体：intervalUnit=hourly, interval=<问答确认值，默认 1>,
   recurring=true, prompt=统一唤醒协议（动作优先，绝不枚举工具清单；
   模板见 continuity/references/long-horizon.md）
-> quota-automation-bind <repo_root> <task_id> <真实返回的 automation id>
-> quota-continuity-preflight <repo_root> <task_id> 必须 PASS（ready=false 即阻断启动）
-> 发首个精度睡眠：后台 sleep 到（观测 reset_at + 300s）——仅当本次观测含有效
   未来 reset 时发出；reset_at 为 null 时**不发**、如实陈述 floor-only 降级
   （只有地板在臂），绝不谎称精度层已武装——命令模板逐字抄写自
   long-horizon.md「精度层睡眠模板」（bash 动态计算秒数，模型零算术；
   宿主接受后台进程即记录在案）
-> worker-model-pin <repo_root> <task_id> <精确 id>（问答第二题选定过时；
   消费语义「失效即拒」见 §6.1）
-> v24-compile <dag-json> [--task-ref <id>] [--out <path>]
-> ListModels -> 写临时 JSON 文件
-> workflow-model-select <models文件> --task-ref <task_id> [--repo-root <repo_root>]
-> writer-acquire <repo_root> <task_id>
-> CreateWorkflow（subagent_model=选型返回值）
-> v24-record-run <task_ref> <run-id>
```

**成功陈述三分（v2.6.0）**：auto 启动成功的汇报必须区分三个独立事实——(a) **auto 授权已落**（mode=auto + 有界预算）；(b) **地板载体已绑定**（真实 automation id 入 state + preflight PASS）；(c) **精度睡眠已实际发出**（宿主接受后台进程）。三者缺一如实陈述缺项：缺 (c) 即 **floor-only 可见降级**（只有地板兜底在臂），绝不是「精度层已武装」；缺 (b) 即 auto 阻断（不开工、按降级报告）。

地板载体创建失败时三禁：**绝不谎称 auto 连续性已启用**；**绝不绑假 id**；**绝不按 auto/until_done 承诺继续**——按调用上下文报告降级（本轮仅手动连续性可用）或交运营者处理。武装、唤醒与终态清理契约见 `skills/continuity`。

### 6.3 启动细则（两分支共用）

- **写 DAG JSON**：`{"task_context": {"task_ref": <task-id>, "goal": ..., "repository": ...}, "nodes": [...]}`（节点即 §5.2 形状）
- **编译**：`cli.py v24-compile <dag-json> [--task-ref <id>] [--out <path>]`——逐节点形状校验 + 全图校验 + ownership 阶段规划 + 确定性 TypeScript 源。校验失败退出码 2 并一次报出全部错误；**生成源绝不自动执行**
- **取仓库写预约**：`cli.py writer-acquire <repo_root> <task_id>`——一个 Git 仓库至多一个活跃 Conductor 写 Workflow；冲突时退出码 1 并报当前持有者（先对账再重试，绝不强行并行写）

### 6.4 提交后程序

1. **记录 run 关联**：拿到 run id 后立即 `runtime.task.record_workflow_run(repo_root, task_id, workflow_run_id)`（一条调用完成 state.workflow_run_id + `.glm-conductor/workflow-runs/` 关联单据 + journal `workflow_started` 事件）；只落关联单据可用 `cli.py v24-record-run <task_ref> <run-id>`
2. **等待结构化结果**：完成通知经宿主送达（GetWorkflowRun 可查进度）。每个节点返回一份 NodeResult：`node_id / status（complete | partial | blocked）/ changes / local_checks / reassessment / gaps`——全部结果按节点 id 聚合返回
3. **收尾**：验收通过后 `cli.py task-complete <repo_root> <task_id>`（终态迁移 + `task_completed` 事件 + 自动释放写者守卫）；失败/取消走 `cli.py task-fail` / `cli.py task-cancel <repo_root> <task_id>`（同样释放守卫）

中断与恢复（Workflow 停摆、额度等待、auto 唤醒）见 `skills/continuity`；完成守卫语义见 `skills/enforcement`。

## 7. 主会话验证（main validation）

- Workflow 结果与工作者报告都只是**声明**（implementation claim）：主会话必须亲自检查完整 diff、核对改动范围是否 ⊆ DAG ownership 并集、重跑验证命令，才能形成验证证据
- 验证完成后落任务级验证记录：`cli.py record-validation <repo_root> <task_id> <passed|failed> [--summary <s>] [--commands <s>] ...`（--commands 可重复、每次恰一条命令；change_id 现算并存，相关路径集 = DAG 全节点 ownership 并集）
- **change_id 新鲜度**：任何相关文件在此之后的再变化都会使验证（与后续审查）记录过期——修复后必须重验重录；禁止回写旧值续命
- 多节点结果聚合后主会话仍须做任务级全局验证（跨节点集成/构建/lint）——节点级 local_check 永不自动替代全局验证

## 8. 高保证评审（仅 assurance: high）

按任务模态选择全新上下文的只读审查者：

- 文本任务 → `glm-reviewer`（只读白名单，模型继承宿主/会话——不宣称跨模型独立，独立性来自全新上下文与只读隔离）
- 视觉任务 → `visual-reviewer`（多模态只读，GLM-5.3-Flash frontmatter 显式绑定，主职独立视觉验收：亲自读截图对照 VISUAL ACCEPTANCE、检查用户可见回归；diff 仅作上下文）。宿主上该绑定不可用（启动报 account-connection-unavailable 等连接错误）时视觉高保障路线 fail closed：向用户报告绑定不可用，绝不退回文本模型（glm-reviewer 或主会话）充当视觉终审

纪律：

- 审查必须在主会话验证之后调用；输入要素（STATED GOAL / ACCUMULATED CHANGE SET / INTERFACES AND CONSTRAINTS / VERIFICATION EVIDENCE，视觉任务另加 VISUAL EVIDENCE）由主会话提供，缺项要求补齐而非猜测
- 裁决只有三个：`ship` / `fix-first` / `rethink`。fix-first：audit 路由主会话修正、full 路由由修复实施方修正，修正后主会话重验，再换**全新**审查者复审；rethink：修订架构，不得报告完成。fix-first / rethink 永不满足完成守卫
- 裁决落账走 CLI：`cli.py review-record <repo_root> <task_id> <reviewer> <verdict> [note]`——**先验证后评审**（无 validation 记录即拒绝），change_id 现算绑定；任何相关文件再变化都会使裁决过期，须重新审查重录
- 完成守卫（assurance: high 时）只认 verdict=ship 且 change_id 新鲜的审查记录

## 9. 视觉例外路径（子代理通道，照旧）

视觉/交互任务（前端界面、布局、dashboard、表单交互、视觉回归）不走原生 Workflow 文本通道，直接派发 `visual-implementer` 子智能体实施：

- 主会话用其专属 Browser/Computer Use 能力采集截图落盘（子代理无采集权）；实施者以 `VISUAL_CAPTURE_REQUEST` 结束调用索取证据，主会话发起携带完整状态的新调用续作（VISUAL_ROUND 1-3，最多 3 轮）
- 视觉判定由多模态角色完成（visual-implementer / visual-reviewer 亲自 Read 截图）；GLM-5.3 主会话是纯文本模型，只能驱动采集，不得声称自己做了视觉判定
- 视觉任务 assurance: high 时用 `visual-reviewer` 独立终审（裁决与 review-record 流程同 §8）

判据、模板与协议细节见 references/role-contracts.md 与 references/operations.md。

## 10. 审查者与实施者预检（fail-closed）

- 视觉通道需要 `glm-conductor:visual-implementer` 在 Agent 工具的可用类型列表中；assurance: high 按模态核对 `glm-conductor:glm-reviewer` / `glm-conductor:visual-reviewer`
- delegate/full 的文本实施经原生 Workflow 执行（worker persona 由编译器内嵌生成源），无需子智能体预检；worker 模型选型硬闸（§6.1）同属 fail-closed，但不属本节子智能体预检；solo 与 assurance: standard 无需任何预检
- 模型与思考档位已固定在子智能体定义中，调用时不得附加模型覆盖
- 视觉高保障终审（assurance: high 视觉任务）必须使用多模态 `visual-reviewer`：其 GLM-5.3-Flash 绑定在宿主上不可用（启动报 account-connection-unavailable 等连接错误）时按 fail closed 处理——向用户报告绑定不可用并停止视觉高保障路线，绝不退回文本模型充当视觉终审
- 视觉通道额外要求：确认截图证据可以落盘并由实施者读取；不可得即停止视觉通道
- 任何所需角色缺失、不可用或名称不符时：停止该通道，告知用户检查插件安装（Settings → Plugin Management），不得静默替换为其他子智能体类型

## 11. ROUTE REASSESSMENT

路由不是单向阶梯。路由变化必须来自**新观察到的证据**，可以双向重估：

```
ROUTE REASSESSMENT

delegability: <old> -> <new>
assurance: <old> -> <new>
mode: <old> -> <new>

evidence:
<新观察到的证据>
```

示例 A（下调可委派性）：delegate 执行中节点结果频繁返回 `reassessment` 重估请求、暴露隐藏的跨模块耦合 →

```
delegability: high -> low
assurance: standard -> high
mode: delegate -> audit
evidence: node results kept returning reassessment requests exposing hidden cross-module coupling
```

主会话接管实施 → 验证 → 审查者终审。

示例 B（上调可委派性）：solo 调查后 root cause、架构、文件、验证均已确定，剩余工作完全机械 →

```
delegability: low -> high
mode: solo -> delegate
evidence: root cause, architecture, owned files and verification are now fully determined; remaining work is mechanical
```

前提：主会话尚未重复完成同一实施。无新证据时不得变更路由；不得为了省事或直觉变更。重估后把 `workflow_reassessed` 事件追加到任务 journal。

## 12. 完成衔接

任务收尾（complete/fail/cancel）与完成判定由 Stop 完成守卫把关：仓库写者守卫、ownership 越界、主会话验证、独立审查四查全过才提交 completed。守卫语义、change_id 新鲜度与降级行为见 `skills/enforcement`。
