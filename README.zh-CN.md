# GLM Conductor

[English](./README.md) | **简体中文**

![Version](https://img.shields.io/badge/version-2.3.1-blue.svg)
![License](https://img.shields.io/badge/license-MIT-blue.svg)
![ZCode Plugin](https://img.shields.io/badge/ZCode-plugin-green.svg)
![Models](https://img.shields.io/badge/models-GLM--5.3%20%2F%20GLM--5.3--Flash-orange.svg)
![CI](https://github.com/Chengy257/glm-conductor/actions/workflows/validate.yml/badge.svg)

GLM Conductor 是面向 ZCode 中 GLM 编码智能体的轻量编排扩展。强模型把规划与验收握在自己手里，把有界的实施交给低成本工作者，并在改动值得时引入独立审查。对长任务，它让工作在额度窗口之间安全延续，而不是在额度耗尽时停摆。

## 为什么选择 GLM Conductor

- **贵的推理用在刀刃上。** 主模型（GLM-5.3）把上下文花在真正值得的地方——解决歧义、设计、路由、检查 diff、验收结果——规格完备的实施则交给更便宜的执行者。
- **机械工作被委派出去，契约由运行时强制。** 每次委派都携带五段式规格（目标、文件与归属、接口、约束、验证），插件钩子对其做机械检查：越界改动与无证据的完成声明无法静默通过。强制层自身故障时可见降级——绝不静默阻断你的会话。
- **长任务跨额度窗口存活。** 任务中途额度耗尽时，在安全里程碑停泊，额度恢复后继续——见下文「长任务连续性」。

## 一图看懂工作方式

```
                     主模型 — GLM-5.3
             规划 · 路由 · 验证 · 验收
                  /                              \
    有界任务 ──▶ 实施者                 高保障任务 ──▶ 独立审查者
                （GLM-5.3-Flash，                   （全新上下文，
                  全新上下文）                         只读）

    长任务连续性
        ├─ 全局额度时钟     常驻 · 每 provider 身份一个
        └─ 任务唤醒桥      临时 · 每等待任务一个
```

ZCode 仍是底层 harness——插件在其内部编排，并在其上叠加运行时强制。

## 路由：谁来实施、谁来审查

首次委派前，主模型先回答两个独立问题：

- **可委派性（Delegability）——剩余实施是否足够有界、可以交出去？** 目标、文件范围、接口、约束、验证全部敲定即 high；架构未定或判断密集即 low。
- **保障等级（Assurance）——主模型验证之后，这次改动是否值得一次全新上下文的独立审查？** 影响面有限即 standard；影响面宽或用户可见风险高即 high。

两个答案共同选定四条路线之一：

| 可委派性 | 保障等级 | 路由 | 实施 | 独立审查 |
| --- | --- | --- | --- | --- |
| low | standard | `solo` | 主模型 | 否 |
| high | standard | `delegate` | 实施者 | 否 |
| low | high | `audit` | 主模型 | 是 |
| high | high | `full` | 实施者 | 是 |

路线在首次委派前声明一次，可凭新证据双向重估。视觉任务使用视觉实施者与视觉审查者。

## 模型角色

当前推荐的默认组合：**GLM-5.3** 担任主会话（规划、路由、验收），**GLM-5.3-Flash** 承担实施者（标准与视觉），独立审查由全新上下文的审查者完成——文本审查跑在 GLM-5.3 上，视觉审查跑在多模态 Flash 上。这些是「当前推荐的角色分配」，不是永久的架构身份：角色契约预期向语义化模型角色（`planner_model` / `executor_model` / `reviewer_model`）演化，未来可配置其他组合。

## 长任务连续性：跨额度窗口

连续性与路由正交——它是任何长任务都可使用的生命周期层。两个机制协作，且刻意是两个物种。默认行为：持久任务总是可恢复——下一次会话启动会接上它；跨额度窗口的自动唤醒则只发生在你显式授权的范围内。

### 全局额度时钟（Global Quota Clock）——常驻，每身份一个

- 每个 provider 身份（你的账号）一个持久时钟，不属于任何单个任务。
- 它周期性醒来，做一次低成本调用以观察 provider 的额度窗口并物化下一个窗口，随后向 provider 的真实 reset 时刻自校时——绝不假设固定重置时刻。
- 常规的周期调度（缺省每小时）只是 watchdog：正常路径会把下一次唤醒精确 retime 到真实 reset 之后。
- 额度事实始终来自 provider 自己的监控端点——插件绝不虚构额度接口、绝不硬编码重置时间。

### 任务唤醒桥（Task Wake Bridge）——临时，每等待任务一个

- 只在某个任务真的在等额度时创建，在临近可恢复执行的时刻唤醒该任务。
- 任务不再需要时随即清理。时钟服务账号，桥服务单个任务——两者从不混淆。

### 专用额度时钟会话（推荐放置）

建议把全局额度时钟放在一个运行低成本 Flash 模型的小型专用会话里，让它的周期唤醒永不打断你的主编码会话。ZCode 目前未向插件开放会话创建：插件只能建议放置方式、帮助诊断错置的时钟，不能创建会话、也无法机械保证放置。

## 宿主兼容与安全

调度类功能适配的是本地观察到的 ZCode 宿主行为——不是契约 API——因此 ZCode 升级可能使其漂移。适配被隔离在可替换的 adapter 之后，其唯一的生产写入是改写既有定时任务的单个调度字段（`next_run_at`）；宿主本地存储的其余一切不可触碰。ZCode 升级后，运行只读自检：

```
python3 <plugin-root>/runtime/cli.py host-check
```

兼容性被破坏时，调度功能安全失败，恢复回退到会话启动注入。诊断与恢复路径——宿主兼容性、时钟健康、状态存放位置——见 [docs/troubleshooting.md](./docs/troubleshooting.md)。

## 安装与首跑

前提条件：

- [ZCode](https://zcode.z.ai) 客户端（已在 3.9.2 版本测试）
- GLM Coding Plan（或 Z.ai 账号），已连接 GLM-5.3 与 GLM-5.3-Flash——**编排主会话必须是 GLM-5.3**；Flash 承担实施与审查角色
- `python3`（Python 3.8+）在 PATH 中——强制层钩子的运行时

通过 ZCode 插件市场安装：

1. 在 ZCode 中打开一个工作区（插件管理页需要工作区处于打开状态）。
2. **Settings → 插件 → 创建 → 添加插件市场**，选择 GitHub 仓库来源，输入 `https://github.com/Chengy257/glm-conductor`，校验通过后在 **个人** 分段安装 glm-conductor。（本地安装：克隆仓库后，把仓库根目录——`marketplace.json` 所在处——添加为本地市场。）
3. 安装或更新后**新建会话**，子智能体、技能与钩子才会加载。

> 请选择市场清单（仓库根目录），不要选 `plugins/glm-conductor/.zcode-plugin/plugin.json`——那是插件清单而非市场清单。

### 首跑：编排开箱即用（日常使用）

新建会话后，在首次委派前先要求编排：

```
用 glm-conductor:orchestration 规划并实现这个功能：先声明路由，再完成验证。
```

主模型会先输出路由声明，再按所选路由工作：

```
SELECTIVE ROUTE
mode: delegate
delegability: high
assurance: standard
executor: flash-implementer
continuity: foreground
reason: implementation is bounded by explicit interfaces, owned files, and deterministic verification
```

日常使用到此为止——全局额度时钟**不是**基本编排的必需品。常用入口：

- `glm-conductor:orchestration` — 路由声明、委派契约、审查流程
- `glm-conductor:continuity` — 长任务的 checkpoint 与恢复
- `glm-conductor:enforcement` — 强制层用户侧参考：环境自检、钩子报文含义、被拦截时的恢复方法
- `/glm-conductor:quota` — 随时可用的额度诊断（文本报告；`--json` 为机器可读输出）

### 可选：额度连续性设置

只在希望任务跨额度窗口继续时才需要：规划并绑定一个全局额度时钟（放在上文推荐的专用会话中），然后让它运行。runtime CLI 是额度与连续性子命令的唯一入口——`<plugin-root>` 为插件安装目录（仓库检出版本中即 `plugins/glm-conductor`）：

```
python3 <plugin-root>/runtime/cli.py <subcommand>
```

例如 `quota-clock-plan` / `quota-clock-bind` / `quota-clock-status`、`quota-resume`、`quota-phase`、`host-check`。

## 文档

- [docs/core-concepts.md](./docs/core-concepts.md) — 三大支柱的概念性说明
- [docs/architecture.md](./docs/architecture.md) — 唯一架构真相源：状态模型、路由、强制层、额度连续性、恢复
- [docs/troubleshooting.md](./docs/troubleshooting.md) — 按「现象 → 判定 → 处置」组织：宿主兼容性、时钟健康、状态存放位置
- [CHANGELOG.md](./CHANGELOG.md) — 发布级变更记录

## 许可

[MIT](./LICENSE) © 2026 glm-conductor contributors
