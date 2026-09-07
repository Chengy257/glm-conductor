# GLM Conductor

[English](./README.md) | **简体中文**

![Version](https://img.shields.io/badge/version-2.2.1-blue.svg)
![License](https://img.shields.io/badge/license-MIT-blue.svg)
![ZCode Plugin](https://img.shields.io/badge/ZCode-plugin-green.svg)
![Models](https://img.shields.io/badge/models-GLM--5.3%20%2F%20GLM--5.3--Flash-orange.svg)
![CI](https://github.com/Chengy257/glm-conductor/actions/workflows/validate.yml/badge.svg)

> GLM Conductor 是面向 ZCode 中 GLM 编码智能体的确定性编排 / 执行保障 / 额度感知连续性运行时。让委派安全的关键契约由运行时钩子确定性强制，而非依赖模型自觉。

## 项目简介

GLM Conductor 是一个 ZCode 编排插件，服务于使用 GLM Coding Plan 的编码智能体。GLM-5.3 主会话担任唯一架构师——负责规划、路由、验证与验收；高性价比的 GLM-5.3-Flash 子智能体执行有界的实施规格；全新上下文的只读审查者提供独立终审。

它的决定性特征：关键契约（ownership 范围、验证证据、审查裁决、额度感知续跑）由随插件分发的钩子与纯标准库 Python 运行时**机械地**强制执行——越界改动无法静默通过完成门，无证据的完成声明会被拦截，过期证据自动失效，额度耗尽的任务在有界延迟内于同一会话再激活。

## 设计动机：三大支柱

**选择性路由编排。** 首次委派前，主会话先声明机器可审计的 `SELECTIVE ROUTE`（五个字段：mode / delegability / assurance / executor / continuity），按两个独立轴分级——**Delegability**（剩余实施是否足够有界可委派）与 **Assurance**（完成后是否需要独立终审）——映射到四条路线之一：`solo` / `delegate` / `audit` / `full`。每次委派都使用五段式实施规格（目标 / 文件与归属 / 接口 / 约束 / 验证）并按 IMPLEMENTATION REPORT 接收；路由可凭新证据双向重估。

**机械的执行保障。** Stop 完成门做四重检查：ownership（实际改动 ⊆ 声明范围）、验证（required 命令真的跑过并落证据）、审查（高保障任务须有新鲜的独立 ship 裁决）、证据新鲜度（验证/审查 receipt 经指纹绑定到记录时的仓库状态）。派发许可（permit）在实施者动手前把关，表驱动的 Bash 策略恒拒破坏性命令。强制层 fail-open 且可见（stderr 报 `ENFORCEMENT DEGRADED`）——强制层故障永不阻断会话。

**额度感知连续性。** 长任务跨额度窗口安全续跑：执行相（NORMAL / PRESSURE / DRAINING / BLOCKED）机械决定当前允许做什么；额度耗尽的任务走授权续跑链，经持久唤醒桥在同一会话内再激活，宿主不可用时由 durable SessionStart 恢复兜底。任务订阅 quota epoch 而非自持额度时钟；恢复时仓库真实状态始终优先于记录。

## 安装

前提条件：

- [ZCode](https://zcode.z.ai) 客户端（Tested with 3.9.2）
- GLM Coding Plan（或 Z.ai 账号），已连接 GLM-5.3 与 GLM-5.3-Flash——**编排主会话必须是 GLM-5.3**；Flash 承担实施与审查角色
- `python3`（Python 3.8+）在 PATH 中——强制层钩子的运行时

通过 ZCode 插件市场安装：

1. 在 ZCode 中打开一个工作区（插件管理页需要工作区处于打开状态）。
2. **Settings → 插件 → 创建 → 添加插件市场**，选择 GitHub 仓库来源，输入 `https://github.com/Chengy257/glm-conductor`，校验通过后在 **个人** 分段安装 glm-conductor。（本地安装：克隆仓库后，把仓库根目录——`marketplace.json` 所在处——添加为本地市场。）
3. 安装或更新后**新建会话**，子智能体、技能与钩子才会加载。

> 请选择市场清单（仓库根目录），不要选 `plugins/glm-conductor/.zcode-plugin/plugin.json`——那是插件清单而非市场清单。

## 快速开始

新建会话后，在首次委派前先要求编排：

```
用 glm-conductor:orchestration 规划并实现这个功能：先声明路由，再完成验证。
```

主会话会先输出路由声明，再按所选路由执行：

```
SELECTIVE ROUTE
mode: delegate
delegability: high
assurance: standard
executor: flash-implementer
continuity: foreground
reason: implementation is bounded by explicit interfaces, owned files, and deterministic verification
```

| Delegability | Assurance | 路由 | 实施 | 独立审查 |
| --- | --- | --- | --- | --- |
| low | standard | `solo` | GLM-5.3 主会话 | 否 |
| high | standard | `delegate` | 实施者子智能体 | 否 |
| low | high | `audit` | GLM-5.3 主会话 | 是 |
| high | high | `full` | 实施者子智能体 | 是 |

`executor`（标准 / 视觉）与 `continuity`（foreground / resumable / idle）作为独立维度与路线一并声明。

三个技能承载工作流：

- `/orchestration` — 路由声明、委派契约、工作单元管理、审查流程
- `/continuity` — 长任务连续性：任务专属 checkpoint、恢复、额度感知调度
- `enforcement` — 强制层用户侧参考：环境自检、钩子报文含义、被拦截时的恢复方法

额度诊断随时可用：`/glm-conductor:quota`（文本报告，`--json` 为机器可读输出）。

runtime CLI 是额度/连续性子命令的唯一入口：

```
python3 plugins/glm-conductor/runtime/cli.py <subcommand>
```

例如 `quota-resolve`、`quota-phase`、`quota-resume`。

## 关键运行时保证

只声明运行时真正支持的行为——每一条都由钩子与纯标准库运行时强制：

- **ownership 范围内的强制。** 四重 Stop 完成门逐任务、按任务绑定的 Git 仓库根求值：实际改动 ⊆ 声明文件范围、required 验证命令真的跑过、高保障任务另有新鲜的独立 ship 审查。越界改动与无证据的完成声明无法静默通过。
- **durable 验证 / 审查 receipt。** `verify-unit` / `verify-task` 由 runtime 亲自在白名单与策略限制下执行已声明命令并落 durable receipt；审查裁决以指纹绑定 receipt 的形式记录。完成门只认 receipt——state 手写字段不再作为通过依据——且 `task_fingerprint` 把每条 receipt 绑定到记录时刻的仓库状态，任何后续编辑都会使证据过期并拦截完成。
- **DRAINING 只许收尾。** 额度感知是双层的：provider 报告四态（AVAILABLE / PRESSURE / EXHAUSTED / UNKNOWN），执行相（NORMAL / PRESSURE / DRAINING / BLOCKED）独立推导——最小编余窗口降到 20% 及以下即判 DRAINING，即使 provider 仍报 AVAILABLE。DRAINING 禁开新实施波次、只许收尾动作；BLOCKED 两者一并冻结。
- **epoch 幂等消费。** 任务订阅 quota epoch（epoch 身份是当前窗口集合的确定性指纹）而非自持额度时钟；激活每 epoch 至多记账一次，窗口预算只在 resume 提交点消费一次——同 epoch 幂等，崩溃窗口重跑绝不重复消费。
- **recurring bridge 是唯一 stable 传输。** 休眠任务的同会话再激活走持久唤醒桥：`recurring_bridge` 是唯一 stable 状态的激活传输，预留的替代传输被拒绝（`TransportReservedError`）而非半实现。可选常驻额度 watcher 只做观察——绝不向休眠会话注入回合；宿主不可用时，durable SessionStart 恢复自动把续跑上下文注入下一个会话。
- **primer 默认关闭。** 新额度窗口物化需要一次最小模型调用；window primer 是 runtime 唯一的 control-plane 模型调用面，默认结构性关闭（`primer_enabled=false` 加授权闸——manual/notify 永不 prime）。

额度事实来自经验证的 provider 监控端点（凭证自动解析、零落盘）——绝不虚构原生 quota 接口、不硬编码重置时间表；端点不可用时回退周期性探针。

## 文档

- [docs/core-concepts.md](./docs/core-concepts.md) — 三大支柱的概念性说明
- [docs/architecture.md](./docs/architecture.md) — 唯一架构真相源：状态模型、路由、派发事务、强制层、额度连续性、恢复
- [docs/README.md](./docs/README.md) — 文档索引
- [CHANGELOG.md](./CHANGELOG.md) — 发布级变更记录

历史存档：开发过程记录（实施计划、实验、发布证据）位于 [docs/history/](./docs/history/)——非权威存档；当前真相 = 仓库代码 + 架构文档。

## 兼容性与稳定边界

稳定边界已冻结：路由双轴与四条路线、Stop 门检查顺序与记账词汇、TASK_ID 格式与 `.glm-conductor/tasks/<task-id>/` 状态布局、传输词汇（`recurring_bridge` 唯一 stable）、runtime CLI 公共子命令面——变更须经显式设计裁决（见[架构文档 §14](./docs/architecture.md)）。执行模型是单控制器：单一编排主会话，实施 worker 上限 4（有界并行，experimental）。

## 许可

[MIT](./LICENSE) © 2026 glm-conductor contributors
