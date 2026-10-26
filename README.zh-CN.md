# GLM Conductor

[English](./README.md) | **简体中文**

![Version](https://img.shields.io/badge/version-2.7.0-blue.svg)
![License](https://img.shields.io/badge/license-MIT-blue.svg)
![ZCode Plugin](https://img.shields.io/badge/ZCode-plugin-green.svg)
![Python](https://img.shields.io/badge/python-3.8%2B-blue.svg)
![Models](https://img.shields.io/badge/models-GLM%20series-orange.svg)

**面向 ZCode 中 GLM 编码智能体的选择性路由、原生 Workflow 执行、最小确定性保障与可选有界额度恢复。**

GLM Conductor 让强模型专注于架构规划、全局判断与最终验收，把边界清晰的实施任务交由 ZCode 原生 Workflow 高效执行，并在代码改动值得时引入独立、全新上下文的只读审查。Conductor 专注语义编排与验收；执行编排归 ZCode 原生所有。

> **设计目标：** 为个人与团队编码工作流提供极轻量的语义编排——决定工作由谁执行、把有界工作编译为规范 DAG，且只保留提示词本身无法保证的确定性检查。Conductor 避免重复建模宿主已拥有的执行事实：它不是第二套工作流引擎、任务调度器或额度控制平面。

---

## 核心特性亮点

- **最大化强模型杠杆率：** 将主会话模型的推理算力聚焦于需求澄清、架构设计、路由仲裁、集成验证与最终验收（启动问答对这些环节软性建议强档模型，不阻塞其他已配置选择）；将边界明确的代码实现委托给经济快速的 Workflow 工作者。
- **基于显式契约的委派：** 委派实施前先编译规范 DAG，在开工前明确定义节点目标、上下游依赖、文件所有权边界、接口规范与环境约束。
- **确定性 Native Workflow 执行：** 静态 DAG 纯代码编译为确定性 TypeScript 原生工作流，由 ZCode 原生调度运行，零多余的在途运行状态镜像。
- **直派角色通道（v2.7.0）：** 编译 Workflow 之外，主会话可直接派发四个专项 subagent——`conductor-scout`（只读调查）与 `conductor-runner`（命令/测试执行）任意路线皆可、无任务前提；写型角色 `conductor-impl-flash` / `conductor-impl` 必须在持有仓库写者守卫的活动任务上下文中派发；单节点 standard 保障的 `delegate` 可直派一名实施者替代单节点 workflow（代价显式：无结构化 NodeResult、无运行级恢复；完成闸/验证/审查链路不变）。
- **低委派长任务的直派链（v2.8.0）：** `delegability: low` 指任务无法作为单一静态 DAG 一次性预先委派——并非不可委派任何实施。实施形态自此三分：主会话直接实施（小任务，绿灯）、**直派链**（低委派长任务：主会话仍是记录在案的编排者，将有界块逐块委派给 `conductor-impl-flash` / `conductor-impl`，每块经 `chain-block-record` 落一条 `chain_block_delegated` journal 事件，块间可在严格 ownership 划分下并行、同批上限 3）、原生 Workflow（delegate/full）。块恢复语义 = 从仓库事实重派。
- **用户授权显式选型：** 工作者必须是用户显式授权的 provider 全限定精确 id（展示宿主全部已配置候选；Flash 档为软推荐位，`workflow-model-select`，`model_policy: user-authorized-explicit-selection`）；`subagent_model` 绝不省略，从协议层根除静默继承主会话模型的隐患。
- **任务级工作流提交免确认（v2.7.0）：** 启动问答第 4 问可对本任务授权 CreateWorkflow 提交免确认（`task-create --auto-approve-workflow`，由 decision-only 的 PermissionRequest 钩子逐请求消费，仅限 **path 型提交**——inline `script:` 提交恒保留人工确认），无人值守唤醒/恢复轮的重提交不再卡在确认问答上。
- **最小确定性质量保障：**
  - **`change_id` 唯一新鲜度原语：** 基于 `git rev-parse HEAD` 与任务所有权范围内的实际改动文件生成 SHA-256 摘要（换行符 CRLF 规范化）；后续任何文件改动必使前置验证或审查自动失效。
  - **粗粒度仓库写者守卫：** 保证一个 Git 仓库同一时刻至多一个活跃 Conductor 写工作流，无脆弱的按时间自动过期（无 TTL），仅在收尾或人工运维时释放。
  - **Stop 钩子完成守卫四查：** 严密拦截越界改动、无写预约、验证缺失/过期或高保障审查未达标等完成尝试。
- **有界可恢复的额度连续性：** 任务遭遇额度耗尽时安全停靠在 `waiting_quota`，通过原生 Scheduled Task 在有界授权预算（`max_resumes`）内自动恢复。
- **四级唤醒载体降级（v2.7.0）：** 宿主 CronCreate 工具缺席时，自动连续性不再硬失败——地板载体按可用性矩阵逐级降级（L0 原生 CronCreate；否则 `wake-carrier-probe` 探测后 L1 OS 计划任务 `os-carrier-install` 兜底、跨应用重启；L2 sleep-only 绑定；L3 诚实 fail-closed），武装级别落盘为 `quota_resume.automation_kind` 见证。
- **零外部三方依赖：** 100% 纯 Python 3.8+ 标准库实现，深度适配 Windows 与 POSIX 文件系统与进程特性。

---

## 架构与工作流程

```text
                主会话（宿主模型）
                (规划 · 路由 · 验证 · 验收)
                         /              \
                        /                \
          solo / audit (主会话亲自实施)  delegate / full (原生 Workflow)
                    ↓                            ↓
            主会话代码编写               v24-compile (规范 DAG 编译)
                                                 ↓
                                         workflow-model-select (选型硬闸)
                                                 ↓
                                         writer-acquire (仓库写预约)
                                                 ↓
                                         ZCode 原生 Workflow 运行
                                                 ↓
                 最小确定性质量保障
           change_id · Ownership · 仓库写者守卫 · 完成守卫
                                                 ↓
                 主会话亲自验证 & [可选] 独立审查者审查
                                                 ↓
                 可选有界额度恢复 (原生 Scheduled Task 唤醒)
```

### 职责分工矩阵

| 领域 | GLM Conductor 拥有 | ZCode 宿主拥有 |
| --- | --- | --- |
| **编排层** | 路由策略、DAG 编译、代码审查策略 | Workflow 运行生命周期、子 actor、并行执行机制 |
| **执行层** | 静态 DAG 到 TypeScript 源码的确定性转换 | 任务调度、后台执行、重试、停止与恢复机制 |
| **状态层** | 语义任务状态（`state.json`）、溯源审计（`events.jsonl`） | 运行时执行状态、Token 账本、Actor 标准输出日志 |
| **保障层** | 变更标识（`change_id`）、仓库写者守卫、Stop 完成守卫 | 操作系统沙箱隔离、原生工具调用权限控制 |
| **连续性** | 恢复授权、有界预算跟踪 | 原生 Scheduled Task 定时触发与自动化调度 |

---

## 双轴选择性路由

在委派任何任务前，主会话先独立回答两个正交维度的问题：

- **可委派性（Delegability）：** 剩余实施是否已有界、规格是否完备、验证是否明确？
- **保障等级（Assurance）：** 主会话完成验证后，增加一次全新上下文的独立只读终审是否能实质降低交付风险？

| 可委派性 | 保障等级 | 路由模式 | 实施主体 | 独立审查 |
|:---:|:---:|:---:|:---:|:---:|
| **Low** | Standard | `solo` | 主会话直接实施（小任务）/ 直派链（长任务） | 无 |
| **High** | Standard | `delegate` | ZCode 原生 Workflow | 无 |
| **Low** | High | `audit` | 主会话直接实施（小任务）/ 直派链（长任务） | 只读审查者（`glm-reviewer` / `visual-reviewer`） |
| **High** | High | `full` | ZCode 原生 Workflow | 只读审查者（`glm-reviewer` / `visual-reviewer`） |

自 v2.8.0 起，Low 可委派性的实施主体按任务长度二分：小任务（绿灯）由主会话直接实施；长任务走有界块直派链，主会话仍是记录在案的编排者（见上方核心特性亮点）。

主会话在动作前输出规范的 `SELECTIVE ROUTE` 声明块；实施过程中若出现新证据改变了任务边界，可双向进行 `ROUTE REASSESSMENT` 重估。

---

## 模型角色分工与选型治理

| 角色 | 默认模型绑定 | 核心职责 |
| --- | --- | --- |
| **主会话** | **宿主/会话模型（继承，不可编程切换）** | 架构决策、路由选择、DAG 编制、代码验证、最终验收 |
| **Workflow 工作者** | **用户授权精确 id（Flash 软推荐）** | 原生 Workflow 内的有界文本代码实施 |
| **视觉实施者** | **GLM-5.3-Flash（多模态，frontmatter 静态绑定）** | 有界前端/UI 代码实施（Custom Subagent 能力例外通道） |
| **文本审查者** | **GLM-5.3（无 frontmatter pin——随会话模型，任意宿主可启动）** | 全新上下文、只读终审（`glm-reviewer`） |
| **视觉审查者** | **GLM-5.3-Flash（多模态，frontmatter 静态绑定）** | 全新上下文视觉读图终审（`visual-reviewer`，不可用即 fail closed） |
| **Scout（侦察）** | **GLM-5.3-Flash（只读工具面，frontmatter 静态绑定）** | 只读调查与事实收集，结论必带 `file:line` 证据引用（`conductor-scout`；直派通道，任意路线可用） |
| **Runner（执行）** | **GLM-5.3-Flash（frontmatter 静态绑定）** | 受托命令/测试逐字执行，诚实回报退出码与输出尾部，绝不改写仓库文件（`conductor-runner`；直派通道，任意路线可用） |
| **Impl-Flash（闪档实施）** | **GLM-5.3-Flash（frontmatter 静态绑定）** | 五段规格的有界机械实施，ownership 纪律同 workflow 工作者（`conductor-impl-flash`；直派通道，须在持有写者守卫的活动任务下派发） |
| **Impl（实施）** | **宿主/会话模型（继承——无 `model` 键）** | 需主模型强度、无需整 workflow 的复杂有界实施（`conductor-impl`；直派通道，须在持有写者守卫的活动任务下派发） |

### 工作者模型 Pin 与用户授权显式选型
Workflow 工作者绝不静默继承会话模型——`subagent_model` 始终是显式的 provider 全限定精确 id。宿主全部已配置模型入候选（不限 provider、不限 Flash）；启动问答列全清单，**单一候选也须用户显式确认**。选定模型**默认以 max 推理档运行（宿主默认思考档位）；档位不随 pin 存储**——pin 只存模型 id。Flash 档（版本无关 `-Flash` 后缀）是**软推荐位**（有界实施的成本/速度友好档）：
1. `--task-ref` 指向无 pin 任务且未给 `--model` 时直接拒绝并交回启动问答（恰一候选也绝不隐式授权）；不带 `--task-ref` 的裸调用恰一 Flash 档候选自动选定（软推荐便捷路径），零候选/多候选歧义一律拒绝交回问答。
2. 用户的选择经 `worker-model-pin` 一次性落为任务级 `worker_model` pin；任意已配置精确 id（含非 Flash）经用户授权均合法（`model_policy: user-authorized-explicit-selection`）。
3. 后续提交携带 `--task-ref` 自动消费该 pin；有 pin 任务的普通 `--model` 不得绕过（不一致即退出码 2）——更换授权必经交互问答重授权并更新 pin，无人值守唤醒轮绝不自动重 pin。若 pin 的模型在当前宿主列表已失效，消费点直接拒绝，绝不静默回退或自动切模。

---

## 最小确定性保障机制

1. **`change_id` 变更标识新鲜度：**
   通过 `git rev-parse HEAD`（unborn 仓库为 `-`）与声明 ownership 范围内实际变动文件的规范哈希计算得出。
   - 包含换行符（CRLF → LF）与路径正斜杠规范化；
   - 任何修复代码都会自动使先前的验证与审查记录过期，杜绝旧证明混充。
2. **粗粒度仓库写者守卫：**
   确保一个 Git 仓库同一时刻至多一个活跃 Conductor 写工作流。
   - 状态写入 `.glm-conductor/writer_guard.json`，并发读写受独占锁保护；
   - **永不按时间自动过期（无 TTL）：** 杜绝长任务锁被抢占。只有在任务终态收尾或操作者显式 `--force` 时方予释放。
3. **Stop 钩子完成守卫四查：**
   通过 ZCode 的 `Stop` 事件钩子（`hooks/stop_gate.py`）按严重级别顺序求值：
   - **查 1（写者守卫）：** 无其他任务的写预约；委派任务完成前必须仍持有该预约；
   - **查 2（所有权范围）：** 工作区实际变动路径全部落在 DAG 节点的 ownership 声明范围内；
   - **查 3（主会话验证）：** 主会话已记录新鲜的 `validation.status == "passed"`；
   - **查 4（独立审查）：** 高保障任务（`audit` / `full`）必须具有新鲜的 `review.verdict == "ship"`。

---

## 可选的有界额度恢复

遇到供应商配额耗尽的委派任务可进入 `waiting_quota` 状态，并经原生 Scheduled Task 唤醒：
- **先武装后开工（Arm-Before-Work）：** 启用自动恢复的任务必须在工作前创建并绑定原生 Scheduled Task（`quota-automation-bind`），并通过 `quota-continuity-preflight` 预检（退出码 0）。
- **有界预算保护：** 每次实际恢复消耗 1 次 `max_resumes` 预算；预算耗尽后任务转为 `waiting_user` 停靠，绝不无休止空转。
- **双通道额度唤醒（v2.5.2）：** 启动即建的 Recurring 定时任务作为终身地板载体（抗应用重启、创建后不再变更），阻塞窗 `reset_at` + grace 的后台睡眠提供秒级精确唤醒——两种触发源共用同一统一唤醒协议。

---

## 环境要求与安装配置

### 环境要求

- **[ZCode](https://zcode.z.ai) 3.14+**（已在 Native Workflow 与 Scheduled Task 上深度实测）
- GLM Coding Plan 或 Z.ai 账号，且已配置可用模型（主会话建议强档模型承担规划/架构/验证；Flash 档模型软推荐用作经济的工作流工作者——任何已配置模型经用户授权均可选）
- **Python 3.8+**，用于运行时钩子与 CLI 工具
  - *Windows 提示：* 请确保 PATH 中可直接执行 `python3.exe`。若环境仅有 `python.exe`，请参见 [Windows 排障指引](./docs/troubleshooting.md#8-troubleshooting-the-python3-hook-command-on-windows)。

### 从 ZCode 插件市场安装

1. 打开 ZCode 工作区；
2. 点击 **Settings → Plugins → Create → Add plugin marketplace**；
3. 输入 GitHub 仓库源地址：`https://github.com/Chengy257/glm-conductor`；
4. 在 **Personal** 分区中点击安装 **glm-conductor**；
5. 安装或更新后新建一个**新会话**以加载最新的 skills、agents 与 hooks。

### 本地克隆开发安装

克隆仓库后，将包含 `marketplace.json` 的项目根目录添加为本地 marketplace：

```bash
git clone https://github.com/Chengy257/glm-conductor.git
```

> **提示：** 请选择根目录的 `marketplace.json`，而非 `plugins/glm-conductor/.zcode-plugin/plugin.json`。

---

## 快速上手

### 1. 启动编排

在新建的 ZCode 会话中直接下达目标：

```text
使用 glm-conductor:orchestration 规划并实现这个功能。先声明路由，再完成验证。
```

### 2. 路由声明与启动问答

主会话声明路由：

```text
SELECTIVE ROUTE
mode: delegate
delegability: high
assurance: standard
reason: 需求边界清晰，具有明确接口与单元测试覆盖。
```

对于持久化任务，主会话在开工前自动进行单轮合并问答：
- 确认是否开启跨额度自动唤醒及 `max_resumes` 预算；
- 指定工作者模型：列出宿主全部已配置候选，单一候选也须显式确认。

### 3. 编译并提交工作流

```bash
# 将静态 DAG 编译为 Native Workflow 源码
python plugins/glm-conductor/runtime/cli.py v24-compile dag.json --out .glm-conductor/workflow.ts

# 取得仓库写预约
python plugins/glm-conductor/runtime/cli.py writer-acquire . <task_id>

# 主会话通过 ZCode CreateWorkflow(subagent_model=...) 提交执行
```

### 4. 验证与完成交付

工作流执行完毕后：
1. 主会话亲自检查代码 diff 并执行验证测试命令；
2. 记录验证通过结果：
   ```python
   runtime.task.record_validation(repo_root, task_id, status="passed", summary="集成测试全绿")
   ```
3. Stop 钩子触发完成守卫四查，全绿放行并自动完成任务与释放写预约。

---

## 参考与命令速查

### 专业技能库

- `glm-conductor:orchestration` —— 路由决策、静态 DAG 编制、启动问答、工作流提交与审查流程。
- `glm-conductor:continuity` —— 断点恢复、`waiting_quota` 停靠、先武装后开工调度与有界恢复。
- `glm-conductor:enforcement` —— Stop 完成守卫四查契约、诊断分析与拦截恢复。
- `/glm-conductor:quota` —— 只读额度余量与窗口重置时间诊断（支持 `--json`）。

### 运行时 CLI 子命令

所有底层操作统一经 `python <plugin-root>/runtime/cli.py <子命令>` 执行：

| 子命令 | 功能说明 |
| --- | --- |
| `v24-compile <dag-json> [--task-ref <id>] [--out <path>]` | 将规范静态 DAG 编译为确定性 TypeScript 原生工作流代码。 |
| `workflow-model-select <models-json> [--model <id>] [--task-ref <id>] [--repo-root <path>]` | 工作者模型选型硬闸：解析用户授权的 worker 模型（Flash 软推荐）或消费任务 pin（有 pin 时 `--model` 必须与 pin 一致）。 |
| `worker-model-pin <repo> <task_id> <exact-id>` | 写入用户授权的任务级 worker 模型 pin。 |
| `writer-acquire <repo> <task_id> [--run-id <id>]` | 获取仓库级写预约（独占写入保护）。 |
| `writer-release <repo> <task_id> [--force]` | 释放仓库写预约（`--force` 供运维强制清理残留）。 |
| `writer-show <repo>` | 查询当前仓库写预约持有者信息。 |
| `task-record-workflow-run <repo_root> <task_id> <run-id>` | 规范委派 run 注册：state + 写者守卫前置 + `workflow_started` 事件 + 账本根单据，任意 CWD 可用。 |
| `v24-record-run <task_ref> <run-id>` | legacy 低层 adapter 单据（兼容保留）。 |
| `review-record <repo> <task_id> <reviewer> <verdict>` | 记录任务级审查裁决（`ship` / `fix-first` / `rethink`）。 |
| `quota-resolve <repo> [--force-refresh]` | 额度四态只读解析（缓存 → provider → 陈旧缓存 → UNKNOWN）。 |
| `quota-wait <repo> <task_id>` | 停泊至 `waiting_quota` 并记录归一化观测快照。 |
| `quota-resume-authorize <repo> <task_id> <max_resumes>` | 授权有界自动恢复预算。 |
| `quota-resume-decision <repo> <task_id>` | 定时唤醒时的幂等决策（无副作用判断）。 |
| `quota-resume-confirm <repo> <task_id>` | 在宿主成功接收 resume 调用后确认落账恢复计数。 |
| `quota-automation-bind / clear / preflight` | 管理 Scheduled Task 连续性武装生命周期。 |
| `task-create <repo> <task_id> <goal> <mode> <assurance> [--dag <path>]` | 经生命周期 CLI 创建任务（`runtime.task` 薄壳映射）。 |
| `record-validation <repo> <task_id> <passed\|failed> [--summary <s>] [--commands <s>]` | 在任务台账上记录新鲜验证结果。 |
| `task-complete / task-fail / task-cancel <repo> <task_id>` | 终态生命周期迁移（成功 / 失败 / 用户取消）。 |
| `exit-waiting <repo> <task_id>` | 手动退出 `waiting_quota`（自动路径走 `quota-resume-confirm`）。 |

### 仓库运维工具

- `scripts/archive_legacy_tasks.py` —— 识别并将 v2.3 遗留任务安全归档至 `.glm-conductor/archived-tasks/`（默认 dry-run 预览，`--confirm` 正式执行）。
- `scripts/validate_plugin.py` —— 纯静态契约校验器，机械校验 16/16 契约锚点。
- `scripts/smoke_plugin_load.py` —— 插件 manifest、agents、skills、hooks 与运行时装载冒烟测试。

---

## 代码仓库布局

```text
glm-conductor/
├── marketplace.json                # 插件市场 catalog 描述清单
├── plugins/glm-conductor/
│   ├── .zcode-plugin/              # 插件 manifest（plugin.json）与元数据
│   ├── agents/                     # 原生 Subagent 定义 (视觉双角色 visual-* / glm-reviewer / 直派四件 conductor-*)
│   ├── commands/                   # 斜杠命令定义 (/glm-conductor:quota)
│   ├── hooks/                      # ZCode 钩子实现 (session_start.py, stop_gate.py)
│   ├── skills/                     # 领域技能 (orchestration, continuity, enforcement)
│   └── runtime/                    # 纯标准库 Python 运行时核心
│       ├── workflow/               # DAG 编译器、工作者 persona、提交选型闸
│       ├── quota/                  # 只读额度适配器、解析器与探测报告
│       ├── change_id.py            # 确定性变更标识哈希计算
│       ├── writer_guard.py         # 仓库写者守卫原子读写
│       ├── ownership.py            # 文件所有权匹配与阶段规划
│       ├── state.py                # 七态任务状态机与 schema 校验
│       └── cli.py                  # 运行时 CLI 统一操作入口
├── scripts/                        # 契约校验、冒烟测试与遗留任务归档脚本
└── docs/                           # 架构规格文档、核心概念、排障手册与路线图
```

---

## 文档索引

- [核心概念](./docs/core-concepts.md) —— 深入了解选择性路由、工作流执行、保障机制与额度恢复。
- [权威架构规格](./docs/architecture.md) —— v2.6 运行时设计的唯一架构真相源。
- [故障排查手册](./docs/troubleshooting.md) —— 额度诊断、Windows 环境配置、超大仓 Git 超时调优与写者守卫运维。
- [Global Quota Clock 拆离清单](./docs/roadmap/GLOBAL_QUOTA_CLOCK_EXTRACTION_INVENTORY.md) —— 独立伴随项目的边界定义。
- [更新日志](./CHANGELOG.md) —— 详尽的版本演进历史与发版说明。

---

## 质量验证与贡献指引

欢迎提交 Issue 与 Pull Request！在提交代码前，请确保通过全套质量门禁：

```bash
# 1. 运行全部单元测试 (1102 测试)
python -m unittest discover -s tests -p "test_*.py"

# 2. 运行静态契约校验 (16/16 检查)
python scripts/validate_plugin.py

# 3. 运行插件装载冒烟测试
python scripts/smoke_plugin_load.py

# 4. 运行代码规范检查
ruff check .
```

---

## 开源许可

[MIT](./LICENSE) © 2026 GLM Conductor Contributors
