# GLM Conductor

[English](./README.md) | **简体中文**

![Version](https://img.shields.io/badge/version-2.3.1-blue.svg)
![License](https://img.shields.io/badge/license-MIT-blue.svg)
![ZCode Plugin](https://img.shields.io/badge/ZCode-plugin-green.svg)
![Models](https://img.shields.io/badge/models-GLM--5.3%20%2F%20GLM--5.3--Flash-orange.svg)
![CI](https://github.com/Chengy257/glm-conductor/actions/workflows/validate.yml/badge.svg)

**面向 ZCode 中 GLM 编码智能体的选择性编排、执行保障与额度感知连续性插件。**

GLM Conductor 让强模型专注于规划、判断与最终验收，把边界清晰的实施任务交给低成本工作者，并在改动值得时引入独立审查。对于长时间任务，它可以跨额度窗口保存并恢复工作，同时保持“额度何时可用”与“任务应该由谁执行”彼此独立。

> **设计目标：** 为个人编码工作流提供轻量编排——强推理用在真正需要判断的地方，规格明确的实施交给低成本模型，而不能只依赖提示词保证的关键边界则由运行时确定性检查。

## 为什么使用 GLM Conductor

- **把强模型额度花在最有价值的地方。** GLM-5.3 负责解决歧义、架构设计、路由、验证与最终验收；边界明确的实施可以交给 GLM-5.3-Flash。
- **以明确契约进行委派。** 每个委派单元都定义目标、文件归属、接口、约束和验证方式；运行时钩子检查关键边界，并要求完成声明具备实际证据。
- **把实施与独立审查分开。** 对高保障任务，在主会话完成验证后，可以再由全新上下文、只读的审查者进行最终审查。
- **让长任务始终可恢复。** 连续性与路由相互独立：任务可以 checkpoint、等待额度并恢复，而不会因为额度变化自动改变执行角色。

## 工作方式

```text
                         主会话 — GLM-5.3
                    规划 · 路由 · 验证 · 验收
                         /                 \
                        /                   \
                  有界实施任务             高保障任务
                      ↓                       ↓
            GLM-5.3-Flash 实施者       全新上下文独立审查者

                         运行时执行保障
                 范围 · 证据 · 完成门 · 恢复

                         长任务连续性
                 全局额度时钟 + 任务唤醒桥
```

ZCode 仍然是底层编码 harness；GLM Conductor 在其上增加编排契约、确定性强制层，以及可选的长任务连续性能力。

## 选择性路由

首次委派前，主会话分别判断两个独立问题：

- **可委派性（Delegability）** —— 剩余实施是否已经足够有界、规格足够完整，可以安全交给工作者？
- **保障等级（Assurance）** —— 主会话验证之后，再增加一次全新上下文的独立审查，是否能实质降低风险？

| 可委派性 | 保障等级 | 路由 | 实施 | 独立审查 |
| --- | --- | --- | --- | --- |
| low | standard | `solo` | 主会话 | 否 |
| high | standard | `delegate` | 实施者 | 否 |
| low | high | `audit` | 主会话 | 是 |
| high | high | `full` | 实施者 | 是 |

路由由证据驱动，而不是固定的风险升级阶梯。当新的证据改变任务边界或风险时，可以向任意方向重新评估。

## 模型角色

当前推荐组合保持简单：

| 角色 | 默认模型 | 主要职责 |
| --- | --- | --- |
| 主会话 | **GLM-5.3** | 规划、架构、路由、验证、验收 |
| 实施者 | **GLM-5.3-Flash** | 有界的标准实施 |
| 视觉实施者 | **GLM-5.3-Flash** | 有界的多模态实施 |
| 文本审查者 | **GLM-5.3** | 全新上下文、只读最终审查 |
| 视觉审查者 | **GLM-5.3-Flash** | 全新上下文视觉审查 |

这些只是当前推荐的模型分配，不是永久架构身份。系统契约围绕角色设计，为未来的 `planner_model` / `executor_model` / `reviewer_model` 配置保留空间。

## 确定性的执行保障

委派结果不会仅凭模型的“已完成”声明被接受。GLM Conductor 将提示词层面的任务契约与运行时检查结合，用于约束任务归属、完成证据、过期验证状态等关键不变量。

最终验收仍由主会话负责：工作者报告只是声明，仓库实际状态、diff 和主会话重新运行的验证才是证据。如果强制层自身无法正常工作，它会明确降级，而不是静默阻断会话。

## 跨额度窗口的长任务连续性

连续性是生命周期层，不是路由轴；日常编排完全不依赖它。

### 全局额度时钟（Global Quota Clock）

持久时钟绑定 provider 身份，而不是某一个具体任务。它观察 provider 的真实额度窗口，并根据实际 reset 时间重新安排下一次唤醒，而不是假设固定的五小时重置周期。常规 recurring schedule 只承担 watchdog 兜底作用。

### 任务唤醒桥（Task Wake Bridge）

任务唤醒桥是临时、任务专属的。只有任务真正等待额度时才存在，并尽量在任务可以恢复执行的时间点附近唤醒。全局时钟服务 provider 身份，唤醒桥只服务一个等待中的任务。

### 推荐放置方式

建议把全局额度时钟放在一个使用低成本 Flash 模型的小型 ZCode 专用会话中，避免周期 tick 打断主编码会话。ZCode 当前没有向插件开放会话创建能力，因此 GLM Conductor 可以提供放置建议和错置诊断，但不能自动创建或机械保证专用会话。

## 宿主兼容性与安全边界

额度调度适配的是**实际观察到的 ZCode 宿主行为，而不是正式的调度契约 API**。宿主相关实现被隔离在可替换 adapter 后面。插件对 ZCode task store 的唯一生产写入，是重新设置既有 automation 的 `next_run_at`；其他宿主存储变更均不属于插件边界。

ZCode 升级后，建议先运行只读兼容性检查：

```bash
python <plugin-root>/runtime/cli.py host-check
```

根据系统使用可用的 Python 3 启动命令（`python` 或 `python3`）。如果宿主兼容性发生变化，调度写入会安全失败，持久任务仍可通过会话启动恢复路径继续。具体诊断与恢复见 [故障排查](./docs/troubleshooting.md)。

## 安装

### 环境要求

- [ZCode](https://zcode.z.ai) —— 当前已在 3.9.2 测试
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
executor: flash-implementer
continuity: foreground
reason: implementation is bounded by explicit interfaces, owned files, and deterministic verification
```

常用入口：

- `glm-conductor:orchestration` —— 路由、委派契约与审查流程
- `glm-conductor:continuity` —— 长任务 checkpoint 与恢复
- `glm-conductor:enforcement` —— 运行时强制、诊断与被拦截任务的恢复
- `/glm-conductor:quota` —— 随时进行额度诊断（`--json` 输出机器可读结果）

### 可选：额度连续性

只有希望任务跨额度窗口自动延续时才需要配置这一部分。在推荐的专用会话中规划并绑定全局额度时钟，之后通过统一 runtime CLI 进行诊断和恢复：

```bash
python <plugin-root>/runtime/cli.py <subcommand>
```

主要命令包括 `quota-clock-plan`、`quota-clock-bind`、`quota-clock-status`、`quota-resume`、`quota-phase` 和 `host-check`。

## 文档

- [核心概念](./docs/core-concepts.md) —— 编排、执行保障与连续性的概念说明
- [架构](./docs/architecture.md) —— 当前运行时设计的权威技术参考
- [故障排查](./docs/troubleshooting.md) —— 宿主兼容性、时钟健康、状态位置与恢复
- [更新日志](./CHANGELOG.md) —— 发布级变更记录

## 项目状态

**v2.3.1 是当前 stable 基线。** 额度连续性子系统已经进入维护模式：后续优先处理 bug、ZCode 宿主兼容性和明确的用户体验改进，而不是继续增加新的 scheduler 架构层。

## 许可

[MIT](./LICENSE) © 2026 glm-conductor contributors
