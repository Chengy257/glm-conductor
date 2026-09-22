# GLM Conductor

[English](./README.md) | **简体中文**

![Version](https://img.shields.io/badge/version-2.4.1-blue.svg)
![License](https://img.shields.io/badge/license-MIT-blue.svg)
![ZCode Plugin](https://img.shields.io/badge/ZCode-plugin-green.svg)
![Models](https://img.shields.io/badge/models-GLM--5.3%20%2F%20GLM--5.3--Flash-orange.svg)
![CI](https://github.com/Chengy257/glm-conductor/actions/workflows/validate.yml/badge.svg)

**面向 ZCode 中 GLM 编码智能体的选择性路由、原生 Workflow 执行、最小确定性保障与可选有界额度恢复。**

GLM Conductor 让强模型专注于规划、判断与最终验收，把边界清晰的实施任务交给 ZCode 原生 Workflow，并在改动值得时引入独立审查。Conductor 只拥有语义编排与验收；执行编排归 ZCode 所有。

> **设计目标：** 为个人编码工作流提供轻量的语义编排——决定工作由谁执行、把有界工作编译为规范 DAG，只保留提示词本身无法保证的确定性检查。v2.4 不是第二套任务运行时、workflow 引擎、权限引擎、溯源系统、调度平台或额度控制面。

## 为什么使用 GLM Conductor

- **把强模型推理花在最有价值的地方。** GLM-5.3 负责解决歧义、架构设计、路由、验证与最终验收；边界明确的实施交给 workflow 工作者。
- **以明确契约进行委派。** 委派工作从规范 DAG 编译而来：节点目标、依赖、文件归属、接口与约束在开工前全部显式定义。
- **把实施与独立审查分开。** 对高保障任务，在主会话完成验证后，可以再由全新上下文、只读的审查者进行最终审查。
- **让额度等待始终可恢复。** 任务可以等待 provider 额度，并通过 ZCode 原生 Scheduled Task 恢复——只有在你明确授权、且有界恢复预算之内才会发生。

## 工作方式

```text
                        主会话 — GLM-5.3
                  规划 · 路由 · 验证 · 验收
                     /                 \
                    /                   \
        solo / audit（主会话）      delegate / full
                ↓                          ↓
          主会话亲自实施          原生 Workflow 运行
                                 （规范 DAG 编译产物）

                  最小确定性保障
   change_id · ownership · 完成守卫 · 仓库写者守卫

                  可选的有界额度恢复
         waiting_quota → 原生 Scheduled Task 唤醒
```

ZCode 仍然是底层编码 harness，并拥有执行编排：workflow 运行生命周期、子 actor、并行调度、重试、后台执行、停止/恢复机制与运行可观测性。GLM Conductor 只持久化宿主不知道的语义。

## 选择性路由

首次委派前，主会话分别判断两个独立问题：

- **可委派性（Delegability）** —— 剩余实施是否已经足够有界、规格足够完整，可以安全交给工作者？
- **保障等级（Assurance）** —— 主会话验证之后，再增加一次全新上下文的独立审查，是否能实质降低风险？

| 可委派性 | 保障等级 | 路由 | 实施 | 独立审查 |
| --- | --- | --- | --- | --- |
| low | standard | `solo` | 主会话 | 否 |
| high | standard | `delegate` | 原生 Workflow | 否 |
| low | high | `audit` | 主会话 | 是 |
| high | high | `full` | 原生 Workflow | 是 |

普通文本工作没有独立的 executor 轴：`solo`/`audit` 即主会话实施，`delegate`/`full` 即原生 Workflow 实施。当新的证据改变任务边界或风险时，路由可以向任意方向重估。

## 模型角色

当前推荐组合保持简单：

| 角色 | 默认模型 | 主要职责 |
| --- | --- | --- |
| 主会话 | **GLM-5.3** | 规划、架构、路由、验证、验收 |
| Workflow 工作者 | **GLM-5.3-Flash（显式，必选）** | 原生 Workflow 内的有界文本实施——delegate/full 每次提交都必须携带经选型预检（`workflow-model-select`）选定的显式 provider 限定 `…/GLM-5.3-Flash` `subagent_model`；省略即静默继承主会话模型，是协议硬禁止（v2.4.0 缺陷 A） |
| 视觉实施者 | **GLM-5.3-Flash** | 有界的多模态实施（Custom Subagent 例外通道） |
| 文本审查者 | **宿主/会话模型** | 全新上下文、只读最终审查 |
| 视觉审查者 | **GLM-5.3-Flash** | 全新上下文视觉审查（固定多模态绑定，不可用即 fail closed） |

Workflow 工作者绝不继承会话模型：编译器 TypeScript 模型无关（宿主在 run 提交时绑定一个模型），Conductor 因此把显式 Flash 选型做成启动硬闸——恰一个已配置的 provider 限定 Flash id 自动选定、零候选 fail closed、多候选必须显式指定。文本审查者刻意不固定 model 字段，从而在不同宿主与套餐下都可启动；两个视觉角色固定为 provider 全限定的多模态 GLM-5.3-Flash 绑定，绑定不可用时视觉高保障路线 fail closed。这些只是当前推荐的模型分配，不是永久架构身份。

## 原生 Workflow 执行

`delegate` 与 `full` 路由共享唯一执行基底：

```text
规范 Conductor DAG → Workflow 编译器 → ZCode 原生 Workflow
```

- Work Unit 是**静态 DAG 节点**：`id`、`objective`、`depends_on`、`ownership`，外加可选的 `interfaces` / `constraints` / `local_check`。它描述"要做什么"，不描述"执行到了哪里"——Conductor 不持久化任何按节点运行时状态。
- 编译器校验 DAG，检测可能并发执行节点之间的 ownership 重叠（冲突在启动前串行化或拒绝），以单一规范 persona 生成工作者指令，并产出确定性的 TypeScript workflow。它绝不自动执行——启动运行是主会话的宿主侧动作。
- 任何 delegate/full 提交前，主会话先过硬闸完成 worker 模型选型（`workflow-model-select`）：宿主模型列表 → 配置精确 id → 显式选定 provider 限定 GLM-5.3-Flash → `CreateWorkflow(subagent_model=<精确 id>)`。选型失败即不启动；主会话 GLM-5.3 绝不作为工作者回退。
- 运行的一切归 ZCode 所有：并行、重试、停止/恢复、后台执行与可观测性。Conductor 只记录任务 ↔ run-id 关联。

## 最小确定性保障

委派结果不会仅凭模型的"已完成"声明被接受，而 v2.4 把强制面刻意收窄：

- **change_id** —— 单一确定性的任务级变更身份（基线修订 + 当前实际改动的 owned 文件内容/状态的哈希，由 ownership scope 解析而来——绝不哈希 scope 串本身）。验证与审查记录都绑定它；此后仓库一旦变化，验证或审查即过期，必须重做。
- **完成守卫（completion guard）** —— Stop 钩子在任务完成前只检查四项不变量：无仍在活跃的写 workflow、改动路径全部落在 DAG ownership 并集内、必需的主会话验证是新鲜的（change_id 匹配）、高保障路由还存在新鲜的 `ship` 审查。
- **一个仓库至多一个活跃写 workflow** —— 粗粒度的仓库写者守卫取代按单元租约：持有者释放之前，第二个 Conductor 写 workflow 无法取得该仓库；委派 run 的注册与完成都会拒绝跳过取守卫的任务（Conductor 生命周期不变式；协议之外的裸宿主 Workflow 调用不在此保证范围内）。编译期 ownership 校验通过后，workflow 内部并行不受影响。
- **小任务状态** —— 七个状态（`active` / `waiting_quota` / `waiting_user` / `blocked` / `completed` / `cancelled` / `failed`）。没有需要 reconciliation 的按单元运行时状态、dispatch permit、租约或验证 receipt。

最终验收仍由主会话负责：工作者报告只是声明，仓库实际状态、diff 和主会话重新运行的验证才是证据。如果守卫自身无法评估，它会明确降级，而不是静默阻断会话。

## 可选的有界额度恢复

额度处理不是路由轴。额度耗尽的委派任务可以进入 `waiting_quota`，由 ZCode 原生 Scheduled Task 唤醒：

- `manual`（默认）：由你选择何时恢复。`auto`：连续性**先武装后开工**——创建未来原生 Scheduled Task 并把 automation id 绑定到任务（`quota-automation-bind`），`quota-continuity-preflight` 必须通过（退出码 0）后才允许首个消耗额度的 Workflow 启动与每次自动恢复；未武装的 auto 任务预检以退出码 2 阻断，定时决策保持 `remain-waiting`（`continuity-unarmed`）。
- recurring Scheduled Task 优先（成功唤醒后保持武装不删不重建）；one-shot 后备必须先创建并绑定后继激活再恢复。每次真实恢复消耗一单位有界 `max_resumes` 预算；预算耗尽后任务转为 `waiting_user`。
- 不存在 quota epoch、订阅、常驻 watcher 进程或窗口记账。额度诊断是只读的（`/glm-conductor:quota` 或 `quota-resolve`）；恢复与武装生命周期经稳定 CLI 子命令执行（`quota-wait` / `quota-resume-authorize` / `quota-resume-decision` / `quota-resume-confirm` / `quota-automation-bind` / `quota-automation-clear` / `quota-continuity-preflight`）。

> 账号级 **Global Quota Clock** 已不属于 GLM Conductor。它计划作为独立的未来伴生项目；拆离清单见 [`docs/roadmap/GLOBAL_QUOTA_CLOCK_EXTRACTION_INVENTORY.md`](./docs/roadmap/GLOBAL_QUOTA_CLOCK_EXTRACTION_INVENTORY.md)。

## 宿主要求与限制

环境要求：

- **ZCode 3.14+** —— v2.4 依赖原生 Workflow 与原生 Scheduled Task 能力（已在 ZCode 3.14.0 验证）
- GLM Coding Plan 或 Z.ai 账号，可使用 GLM-5.3 与 GLM-5.3-Flash
- Python 3.8+，用于运行时钩子与 CLI

如实声明的已知限制：

- **关闭 ZCode 应用时的定时触发未经实证。** 已测宿主上观察到的定时回合表现为所属会话的回合中续跑；本插件不宣称、也不保证 App 关闭时的唤醒行为。
- **审查者双角色已在最终绑定上经真正新开会话实证。** 从 v2.4 树刷新插件缓存后，新开的 ZCode 会话成功启动两个审查者角色：文本审查者（不固定 model 字段，继承宿主/会话模型）以只读方式产出符合契约的 `GLM REVIEW` 裁决；视觉审查者（固定 provider 全限定的多模态 GLM-5.3-Flash 绑定）对一张提示词从未描述过内容的测试图完成真实读图并准确描述画面（盲测，`VISUAL REVIEW` 裁决）。绑定不可用时视觉高保障路线仍 fail closed。
- `visual-implementer` 仍是 Custom Subagent 能力例外，不是 Native Workflow 工作者；视觉反馈拓扑需要主会话采集截图。
- 钩子只剩 SessionStart（恢复上下文）与 Stop（完成守卫）。没有 dispatch permit、ownership 注入或 Bash 策略钩子；工具策略请使用 ZCode 原生权限设施加工作者约束。

## 安装

### 环境要求

- [ZCode](https://zcode.z.ai) 3.14 或更新
- GLM Coding Plan 或 Z.ai 账号，可使用 GLM-5.3 与 GLM-5.3-Flash
- Python 3.8+，用于运行时钩子

### 从 ZCode 插件市场安装

1. 在 ZCode 中打开一个工作区。
2. 进入 **Settings → Plugins → Create → Add plugin marketplace**。
3. 选择 GitHub 仓库来源，输入 `https://github.com/Chengy257/glm-conductor`。
4. 在 **Personal** 分区安装 **glm-conductor**。
5. 安装或更新完成后创建一个**新会话**，使 agents、skills 和 hooks 重新加载。

本地安装时，克隆仓库后，将包含 `marketplace.json` 的仓库根目录添加为本地 marketplace。

> 应选择仓库根目录的 marketplace manifest。不要选择 `plugins/glm-conductor/.zcode-plugin/plugin.json`；后者是插件自身 manifest，不是 marketplace manifest。

## 快速开始

日常编排不需要额外配置。新建会话后，可以直接要求 GLM Conductor 规划并执行任务：

```text
使用 glm-conductor:orchestration 规划并实现这个功能。先声明路由，再完成验证。
```

典型路由声明如下：

```text
SELECTIVE ROUTE
mode: delegate
delegability: high
assurance: standard
reason: implementation is bounded by explicit interfaces, owned files, and deterministic verification
```

`delegate`/`full` 路由下，主会话随后构建规范 DAG，用 `v24-compile` 编译，经 `workflow-model-select` 硬闸选定显式 GLM-5.3-Flash 工作者模型（选型失败不启动、绝不省略 `subagent_model`），取得仓库写者守卫，启动原生 Workflow；运行结束后记录运行关联并验证结果。授权无人值守续跑的任务在启动前先完成未来定时激活的武装（授权 → 建 Scheduled Task → bind → 预检通过 → 启动）。

常用入口：

- `glm-conductor:orchestration` —— 路由、DAG 契约与审查流程
- `glm-conductor:continuity` —— checkpoint、`waiting_quota` 与有界恢复
- `glm-conductor:enforcement` —— 完成守卫语义、诊断与被拦截任务的恢复
- `/glm-conductor:quota` —— 只读额度诊断（`--json` 输出机器可读结果）

## 运行时 CLI 命令面

所有运行时操作经统一 CLI（`python <plugin-root>/runtime/cli.py <子命令>`；单行 JSON 输出）：

| 命令 | 用途 |
| --- | --- |
| `quota-resolve <repo> [--force-refresh]` · `quota-wait` / `quota-resume-authorize` / `quota-resume-decision` / `quota-resume-confirm` | 额度四态解析（只读观测）与有界恢复生命周期（等待 / 授权 / 决策 / 确认） |
| `quota-automation-bind` / `quota-automation-clear` / `quota-continuity-preflight` | auto 连续性武装生命周期（绑定 / 清除 / 预检；`ready=false` → 退出码 2 即启动/恢复阻断信号） |
| `workflow-model-select <models-json> [--model <exact-id>]` | worker 模型选型硬闸（恰一 Flash 自动选定、零候选拒绝、多候选须显式指定；输出 `{subagent_model, model_policy}` 提交契约） |
| `v24-compile <dag.json> [--task-ref <id>] [--out <path>]` | 把规范 DAG 编译为原生 Workflow 源码（只生成，绝不自动执行） |
| `writer-acquire <repo> <task_id> [--run-id <id>]` | 取得仓库写预约 |
| `writer-release <repo> <task_id> [--force]` | 释放写预约（`--force` 供显式 inspect 之后的清除） |
| `writer-show <repo>` | 查看当前持有者 |
| `v24-record-run <task_ref> <run-id> [--artifact <path>]` | 记录任务 ↔ workflow run-id 关联 |
| `review-record <repo> <task_id> <reviewer> <verdict> [note]` | 记录任务级审查裁决（先验证后评审，change_id 新鲜度锚定） |

额度诊断另有 `runtime/quota/report.py`（文本与 `--json` 双输出），由 `/glm-conductor:quota` 呈现。

## 文档

- [核心概念](./docs/core-concepts.md) —— 路由、workflow 执行、保障与额度恢复的概念说明
- [架构](./docs/architecture.md) —— 当前运行时设计的权威技术参考
- [故障排查](./docs/troubleshooting.md) —— 额度诊断、v2.3 遗留任务检测、写者守卫陈旧释放与恢复
- [Global Quota Clock 拆离清单](./docs/roadmap/GLOBAL_QUOTA_CLOCK_EXTRACTION_INVENTORY.md) —— 未来伴生项目的边界
- [更新日志](./CHANGELOG.md) —— 发布级变更记录

## 项目状态与迁移

**v2.4.1 是当前基线** —— 这是 v2.4 重基线之上的一次窄幅连续性热修。它关闭了实际使用中发现的两个 v2.4.0 缺陷：省略 `subagent_model` 时 Workflow 工作者静默继承主会话模型（现为每次 delegate/full 启动前的显式 provider 限定 GLM-5.3-Flash 选型硬闸），以及 auto 连续性在无保证未来激活的情况下进入额度等待（现为先武装后开工：recurring Scheduled Task 优先、one-shot 后备先续后继再恢复、until_done 恒受 max_resumes 有界）。v2.4 的复杂度重基线本身不变：这是一次基于 ZCode 原生 Workflow 的重基线，不是 v2.3 运行时的增量版本。v2.3 执行运行时（dispatcher、dispatch permit、按单元租约、receipt、额度控制面、Global Quota Clock）已删除。**v2.3 任务态不迁移**：v2.4 会检测遗留任务目录并以恢复指引拒绝，而不是自动转换。升级前请先完成或显式退役活跃的 v2.3 任务；已发布的 v2.3.x 可通过 Git 历史与 tag 找回。legacy 检测指引见[故障排查](./docs/troubleshooting.md)。

## 许可

[MIT](./LICENSE) © 2026 glm-conductor contributors
