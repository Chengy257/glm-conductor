# GLM Conductor

[English](./README.md) | **简体中文**

![Version](https://img.shields.io/badge/version-2.5.1-blue.svg)
![License](https://img.shields.io/badge/license-MIT-blue.svg)
![ZCode Plugin](https://img.shields.io/badge/ZCode-plugin-green.svg)
![Python](https://img.shields.io/badge/python-3.8%2B-blue.svg)
![Models](https://img.shields.io/badge/models-GLM--5.3%20%2F%20GLM--5.3--Flash-orange.svg)
![CI](https://github.com/Chengy257/glm-conductor/actions/workflows/validate.yml/badge.svg)

**面向 ZCode 中 GLM 编码智能体的选择性路由、原生 Workflow 执行、最小确定性保障与可选有界额度恢复。**

GLM Conductor 让强模型专注于架构规划、全局判断与最终验收，把边界清晰的实施任务交由 ZCode 原生 Workflow 高效执行，并在代码改动值得时引入独立、全新上下文的只读审查。Conductor 专注语义编排与验收；执行编排归 ZCode 原生所有。

> **设计目标：** 为个人与团队编码工作流提供极轻量的语义编排——决定工作由谁执行、把有界工作编译为规范 DAG，且只保留提示词本身无法保证的确定性检查。Conductor 避免重复建模宿主已拥有的执行事实：它不是第二套工作流引擎、任务调度器或额度控制平面。

---

## 核心特性亮点

- **最大化强模型杠杆率：** 将昂贵的 GLM-5.3 推理算力聚焦于需求澄清、架构设计、路由仲裁、集成验证与最终验收；将边界明确的代码实现委托给经济快速的 Workflow 工作者。
- **基于显式契约的委派：** 委派实施前先编译规范 DAG，在开工前明确定义节点目标、上下游依赖、文件所有权边界、接口规范与环境约束。
- **确定性 Native Workflow 执行：** 静态 DAG 纯代码编译为确定性 TypeScript 原生工作流，由 ZCode 原生调度运行，零多余的在途运行状态镜像。
- **工作者模型选型硬闸：** 强制要求工作者必须解析并绑定 provider 限定的 `/GLM-5.3-Flash`（`workflow-model-select`），从协议层根除意外继承高成本主会话模型的隐患。
- **最小确定性质量保障：**
  - **`change_id` 唯一新鲜度原语：** 基于 `git rev-parse HEAD` 与任务所有权范围内的实际改动文件生成 SHA-256 摘要（换行符 CRLF 规范化）；后续任何文件改动必使前置验证或审查自动失效。
  - **粗粒度仓库写者守卫：** 保证一个 Git 仓库同一时刻至多一个活跃 Conductor 写工作流，无脆弱的按时间自动过期（无 TTL），仅在收尾或人工运维时释放。
  - **Stop 钩子完成守卫四查：** 严密拦截越界改动、无写预约、验证缺失/过期或高保障审查未达标等完成尝试。
- **有界可恢复的额度连续性：** 任务遭遇额度耗尽时安全停靠在 `waiting_quota`，通过原生 Scheduled Task 在有界授权预算（`max_resumes`）内自动恢复。
- **零外部三方依赖：** 100% 纯 Python 3.8+ 标准库实现，深度适配 Windows 与 POSIX 文件系统与进程特性。

---

## 架构与工作流程

```text
                     主会话 — GLM-5.3
                (规划 · 路由 · 验证 · 验收)
                         /              \
                        /                \
          solo / audit (主会话亲自实施)  delegate / full (原生 Workflow)
                    ↓                            ↓
            主会话代码编写               v24-compile (规范 DAG 编译)
                                                 ↓
                                         workflow-model-select (Flash 选型硬闸)
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
| **Low** | Standard | `solo` | GLM-5.3 主会话 | 无 |
| **High** | Standard | `delegate` | ZCode 原生 Workflow | 无 |
| **Low** | High | `audit` | GLM-5.3 主会话 | 只读审查者（`glm-reviewer` / `visual-reviewer`） |
| **High** | High | `full` | ZCode 原生 Workflow | 只读审查者（`glm-reviewer` / `visual-reviewer`） |

主会话在动作前输出规范的 `SELECTIVE ROUTE` 声明块；实施过程中若出现新证据改变了任务边界，可双向进行 `ROUTE REASSESSMENT` 重估。

---

## 模型角色分工与选型治理

| 角色 | 默认模型绑定 | 核心职责 |
| --- | --- | --- |
| **主会话** | **GLM-5.3** | 架构决策、路由选择、DAG 编制、代码验证、最终验收 |
| **Workflow 工作者** | **GLM-5.3-Flash（显式指定）** | 原生 Workflow 内的有界文本代码实施 |
| **视觉实施者** | **GLM-5.3-Flash（多模态）** | 有界前端/UI 代码实施（Custom Subagent 能力例外通道） |
| **文本审查者** | **宿主/当前会话模型** | 全新上下文、只读终审（`glm-reviewer`） |
| **视觉审查者** | **GLM-5.3-Flash（多模态）** | 全新上下文视觉读图终审（`visual-reviewer`，不可用即 fail closed） |

### 工作者模型 Pin 与提交硬闸
Workflow 工作者绝不继承会话模型。提交必须经 `workflow-model-select` 选型硬闸：
1. 宿主配置中恰有一个 provider 限定的 Flash 模型（`account:.../GLM-5.3-Flash`）时自动选定；
2. 存在多候选歧义时，在启动问答中由用户一次性选定，并作为任务级 `worker_model` pin 记录；
3. 后续提交携带 `--task-ref` 自动消费该 pin；若 pin 的模型在当前宿主列表已失效，消费点直接拒绝（退出码 2），绝不静默回退或自动切模。

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
- **周期定时载体与精确唤醒并存：** Recurring 定时任务作为武装载体与保底，同时支持按观测到的 `reset_at` 加发零状态 one-shot 精确唤醒。

---

## 环境要求与安装配置

### 环境要求

- **[ZCode](https://zcode.z.ai) 3.14+**（已在 Native Workflow 与 Scheduled Task 上深度实测）
- GLM Coding Plan 或 Z.ai 账号，可访问 GLM-5.3 与 GLM-5.3-Flash
- **Python 3.8+**，用于运行时钩子与 CLI 工具
  - *Windows 提示：* 请确保 PATH 中可直接执行 `python3.exe`。若环境仅有 `python.exe`，请参见 [Windows 排障指引](./docs/troubleshooting.md#8-windows-环境下-python3-钩子命令排查)。

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
- 在模型候选项歧义时指定工作者模型。

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
| `workflow-model-select <models-json> [--model <id>] [--task-ref <id>]` | 工作者模型选型硬闸，自动筛选 Flash 模型或消费任务 pin。 |
| `worker-model-pin <repo> <task_id> <exact-id>` | 写入用户授权的任务级 worker 模型 pin。 |
| `writer-acquire <repo> <task_id> [--run-id <id>]` | 获取仓库级写预约（独占写入保护）。 |
| `writer-release <repo> <task_id> [--force]` | 释放仓库写预约（`--force` 供运维强制清理残留）。 |
| `writer-show <repo>` | 查询当前仓库写预约持有者信息。 |
| `v24-record-run <task_ref> <run-id>` | 记录任务与 Native Workflow run-id 的关联单据。 |
| `review-record <repo> <task_id> <reviewer> <verdict>` | 记录任务级审查裁决（`ship` / `fix-first` / `rethink`）。 |
| `quota-resolve <repo> [--force-refresh]` | 额度四态只读解析（缓存 → provider → 陈旧缓存 → UNKNOWN）。 |
| `quota-wait <repo> <task_id>` | 停泊至 `waiting_quota` 并记录归一化观测快照。 |
| `quota-resume-authorize <repo> <task_id> <max_resumes>` | 授权有界自动恢复预算。 |
| `quota-resume-decision <repo> <task_id>` | 定时唤醒时的幂等决策（无副作用判断）。 |
| `quota-resume-confirm <repo> <task_id>` | 在宿主成功接收 resume 调用后确认落账恢复计数。 |
| `quota-automation-bind / clear / preflight` | 管理 Scheduled Task 连续性武装生命周期。 |

### 仓库运维工具

- `scripts/archive_legacy_tasks.py` —— 识别并将 v2.3 遗留任务安全归档至 `.glm-conductor/archived-tasks/`（默认 dry-run 预览，`--confirm` 正式执行）。
- `scripts/validate_plugin.py` —— 纯静态契约校验器，机械校验 16/16 契约锚点。
- `scripts/smoke_plugin_load.py` —— 插件 manifest、agents、skills、hooks 与运行时装载冒烟测试。

---

## 代码仓库布局

```text
glm-conductor/
├── marketplace.json                # 插件市场 catalog 描述清单
├── .zcode-plugin/                  # 插件 manifest 与元数据配置
├── plugins/glm-conductor/
│   ├── agents/                     # 原生 Subagent 定义 (glm-reviewer, visual-*)
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
├── tests/                          # 完备的单元测试套件 (790+ 项测试)
└── docs/                           # 架构规格文档、核心概念、排障手册与路线图
```

---

## 文档索引

- [核心概念](./docs/core-concepts.md) —— 深入了解选择性路由、工作流执行、保障机制与额度恢复。
- [权威架构规格](./docs/architecture.md) —— v2.5 运行时设计的唯一架构真相源。
- [故障排查手册](./docs/troubleshooting.md) —— 额度诊断、Windows 环境配置、超大仓 Git 超时调优与写者守卫运维。
- [Global Quota Clock 拆离清单](./docs/roadmap/GLOBAL_QUOTA_CLOCK_EXTRACTION_INVENTORY.md) —— 独立伴随项目的边界定义。
- [更新日志](./CHANGELOG.md) —— 详尽的版本演进历史与发版说明。

---

## 质量验证与贡献指引

欢迎提交 Issue 与 Pull Request！在提交代码前，请确保通过全套质量门禁：

```bash
# 1. 运行全部单元测试 (790+ 测试)
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
