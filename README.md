# GLM Conductor

[English](./README.en.md) | **简体中文**

![Version](https://img.shields.io/badge/version-2.1.0-blue.svg)
![License](https://img.shields.io/badge/license-MIT-blue.svg)
![ZCode Plugin](https://img.shields.io/badge/ZCode-plugin-green.svg)
![Models](https://img.shields.io/badge/models-GLM--5.3%20%2F%20GLM--5.3--Flash-orange.svg)
![CI](https://github.com/Chengy257/glm-conductor/actions/workflows/validate.yml/badge.svg)

> **Selective orchestration for GLM coding agents in ZCode.**
> GLM-5.3 指挥，GLM-5.3-Flash 实施，独立只读终审——关键契约由运行时钩子确定性强制，而非依赖模型自觉。

## 简介

GLM Conductor 是一个 ZCode 编排插件，为使用 GLM Coding Plan 的编码智能体提供**选择性路由**与**强制执行**能力。旗舰模型（GLM-5.3）担任架构师负责规划、验证与验收，高性价比的 GLM-5.3-Flash 负责有界实施，全新上下文的只读审查者提供独立终审。

设计动机很直接：GLM-5.3 与 GLM-5.3-Flash 智力差距很小，但 API 价格相差 10-20 倍、Coding Plan 额度消耗约为旗舰的 1/3。与其所有工作都用旗舰，不如按两个独立维度分级路由——**剩余实施是否足够有界可委派**（Delegability）与**完成后是否需要独立终审**（Assurance）。

v2 起，插件把原本写在提示词里的关键契约升级为**运行时强制**：越界改动无法静默通过完成门、无证据的完成声明会被拦截、过期的验证/审查证据自动失效、额度耗尽时按精确 reset 时间规划唤醒。这些由随插件分发的钩子与纯标准库运行时模块确定性执行。

## 核心特性

**路由与执行**

- **双轴选择性路由**——首次委派前输出机器可审计的 `SELECTIVE ROUTE` 声明（solo / delegate / audit / full 四模式），路由可凭新证据双向重估
- **分层执行**——判断密集工作留旗舰，规格完备的高吞吐实施委派 Flash（标准/视觉两通道），成本与额度双优化
- **路由前置侦察**——关键事实未知时先做只读 Explore 侦察（ROUTING PREFLIGHT），不用弱证据强定路由
- **任务上下文包**——主会话压缩的有界上下文（TASK CONTEXT PACK）交给实施者：旗舰压缩、Flash 执行

**质量与审查**

- **独立只读终审**——high-assurance 任务由全新上下文审查者给出 `ship` / `fix-first` / `rethink` 裁决；任何修复使先前裁决失效（v2 起由证据指纹自动强制）
- **视觉通道**——visual-implementer 多模态实施 + visual-reviewer 读图终审，补齐旗舰纯文本边界
- **结构化契约**——五段式实施规格 + IMPLEMENTATION REPORT，无证据的完成声明无效

**运行时强制（v2）**

- **完成门四重检查**——① ownership：实际改动 ⊆ 声明范围；② 验证：required 命令全部由主会话亲自跑过，并经 `record_unit_verification` / `record_verification` 记录新鲜证据，Work Unit 完成同样受 RB-1 证据门约束；③ 审查：high-assurance 须有新鲜 ship 裁决；④ 证据新鲜度：验证/审查证据经 `task_fingerprint` 绑定到记录时的仓库状态，后续任何编辑使其过期并拦截完成
- **多仓工作区支持**——任务可通过 state 的 `repository.root` 绑定专属 Git 仓库根；Stop 完成门按任务仓库求值，workspace 根无需是 Git 仓库；账本（state / journal / lease）位置不变
- **Bash 策略门控**——表驱动 allow/ask/deny：破坏性命令（rm -rf、git reset --hard、force push）恒拒；高保障任务的推送/迁移/发布/权限变更升级为 ask
- **验证 / 审查溯源（v2.1 M6）**——`verify-unit` / `verify-task` 由 runtime 受限主动执行验证命令（白名单 + 策略双闸、零 TOCTOU 同刻指纹）并落 durable receipt；审查裁决经 `review-record` 绑定终指纹落 fresh ship receipt——完成门审查检查只认 receipt，state 手写字段不再作为通过依据
- **降级可见**——强制层故障永不阻断会话（fail-open），stderr 报 `ENFORCEMENT DEGRADED`

**长任务与额度（v2.2 额度连续性控制环）**

- **任务与工作单元管理**——Work Unit 依赖图（十状态生命周期、环校验、确定性拓扑序）、五道闸派发准入、证据对账式中断恢复（completed 不重跑）、显式 Join 强制任务级全局验证
- **文件租约与有界并行**（experimental）——全有或全无获取、异 owner 冲突拒绝；**默认并发 2、上限 4**（v2.1 起），额度预算折算（AVAILABLE→策略值、PRESSURE/UNKNOWN→1、EXHAUSTED→0）；`prepare_dispatch_wave` 批量派发一次签发整批许可与 marker，wave 成员须同一回合并发派出
- **runtime 额度解析（v2.1 M5）**——派发前额度状态由四级层级解析（新鲜缓存 → provider → 陈旧缓存 → UNKNOWN），**绝不默认 AVAILABLE**；UNKNOWN fail-open 折算预算 1 不阻塞派发
- **双层额度模型（v2.2）**——provider 四态（AVAILABLE / PRESSURE / EXHAUSTED / UNKNOWN）与 execution phase 四态（NORMAL / PRESSURE / DRAINING / BLOCKED）是两个维度：最小编余 ≤20% 即判 DRAINING（即使 provider 报 AVAILABLE——v2.1 的 24% 误判被结构性封堵）；DRAINING 禁开新实施波次只许收尾，BLOCKED 新波次与收尾一并冻结；连续性义务（armed / wake_required / waiting_quota / degraded）由同一纯决策器机械折算
- **Quota Epoch 与窗口物化机制（v2.2）**——有意义的不再是"定时器触发了"，而是"新的可执行额度 epoch 出现了"：epoch 身份 = 窗口多重集 `(kind, reset_at)` 的确定性指纹（不含 status/百分比——provider 状态翻转不推进 epoch），`epoch_id = "glm:"+指纹前 16 位`；probe / executable 双 boundary 分离（观察收紧与恢复资格绝不混用）。**窗口机制实测口径**：reset_at 时刻窗口恢复 100%（周窗优先）；下一个 reset_at 只在新窗口内发生模型调用时才物化——纯查询绝不推进它；reset_at 锚定物化时刻 +5h00m01s；会话空闲会推迟窗口起点（证据见 `docs/GLM-Conductor-v2.2-Phase0-Primer-Experiments.md`）
- **额度 Watcher（v2.2，可选常驻）**——`quota-watcher start|status|stop|once` 启动本地常驻观察进程（poll-only、零模型调用、单实例锁即状态文件）：Session 休眠时独立维护真实 provider 额度时钟；ACTIVE/PASSIVE 逐 tick 重判（有 waiting_quota 或自动续跑任务才 ACTIVE；manual/notify 恒 PASSIVE——可观察但绝不 prime、绝不发激活）；观察间隔按 execution phase 自适应（NORMAL 1800s → DRAINING/BLOCKED 300s）
- **Window Primer（v2.2，默认关闭）**——新窗口需一次最小模型调用才物化：primer 是 runtime 唯一的 control-plane 模型调用面，三重授权闸 fail-closed（`primer_enabled` 默认 **false** + 自动续跑族 + 授权来源必须是用户——manual/notify 永不 prime）；单飞幂等（同 boundary 至多一次，失败尝试同样落账）；物化证据只有"两次强制刷新之间的窗口身份变化"——HTTP 200 与百分比都不是证据
- **额度订阅与恢复链（v2.2）**——任务订阅 quota 而非拥有额度时钟：state 的 `quota_subscription` 块记录注册 epoch；激活记账每 epoch 恰一次（QC-07，控制面 `quota_epoch_advanced` 事件落 `.glm-conductor/quota/events.jsonl`）；恢复门顺序冻结为 evaluate → 转态 → mark（崩溃窗口宁可少记账不卡死恢复）；**窗口预算只在 §15.1 resume commit point 消费一次**（`record_quota_boundary_consumed`，同 epoch 幂等；manual/notify 永不消费；arm/fire/create 一律不消费）；`quota-resume` 退出码 0/1/2/3——3 = durable-but-degraded（转态可能已落盘，幂等重跑安全）
- **授权续跑（v2.1 M5 起，v2.2 收口）**——额度 EXHAUSTED 走确定性转态链：四态授权（manual / notify / auto_once / until_done，升档须用户授权落盘）+ 自足 wake prompt（宿主实锚：wake=同会话续行）；Persistent Wake Bridge（`wake-plan` 裁决 → 主会话宿主 CronCreate → `arm` 记账）是 stable 主路径；预算耗尽转 `waiting_user`，恢复首步恒为 `quota-resume`
- **Activation Transport（v2.2）**——"额度可用"与"休眠会话拿到新回合"是两件事，后者单独抽象：`recurring_bridge` 是唯一 stable 传输（一个任务 ↔ 一个常驻循环 automation）；`probe_then_hold` / `self_retiming` / `session_injector` 为预留（`arm` 一律 `TransportReservedError` 拒绝，绝不半实现）；`transport-status` 输出冻结 12 键事实面
- **Stop 门 continuity health（v2.2）**——完成门第 0 位检查（只约束休眠交接域：waiting_quota/waiting_user 或 PRESSURE/DRAINING + resumable + 自动续跑）：三件套按依赖序核查——handoff durable（checkpoint + resume manifest）、当期 epoch 已注册订阅、激活传输已 armed；`create=forbidden` 或 scheduled-origin 一律降级放行不无限拦截（watcher 缺席/过期只是降级注记）
- **额度感知连续性**——查询 Coding Plan 用量（凭证零落盘），四态评估；不可用时 fail-open 回退周期性探针；`/glm-conductor:quota` 随时诊断
- **跨会话恢复**——任务专属 checkpoint + 八步恢复检查，仓库真实状态始终优先于记录

runtime 额度/连续性 CLI 面（`python3 plugins/glm-conductor/runtime/cli.py <子命令>`）：`quota-resolve` / `quota-observe` / `quota-phase` / `quota-exhausted` / `quota-resume`（退出码 0/1/2/3）/ `wake-record`（v2.1 legacy，deprecated）/ `wake-prompt` / `wake-plan` / `wake-status` / `wake-reconcile` / `transport-status` / `quota-watcher start|status|stop|once`。宿主事实由会话侧供给（如 `wake-reconcile` 显式传入 CronList 观测结论）——runtime 自身零宿主 `Cron*` 调用；完成时桥接清理是会话侧单次尝试动作（绝不重试）。

**v2.0.1 运行时完整性加固**——依据 v2.0.0 全面审查的 H1-H8 收口：完成生命周期门控（`finalizing` + 状态转换表，`completed` 仅完成门可提交）、路由跨字段不变量与任务发现四分类 fail-closed（损坏的 state.json 不再被当成"无任务"）。`task_manager` 事务边界四 API 固化派发生命周期（决策 → 租约 → 状态转换 → 记账 → 落盘 → 事件），崩溃窗口有确定性恢复路径。租约获得 TTL/generation/心跳续约，`recover_leases` 自动清理 stale 租约——崩溃后无需人工删 leases.json。验证证据显式绑定 work unit id（无 `unit` 字段的旧事件不再被恢复对账采信，属有意的破坏性变更；主会话需重新验证）。发布加固（RB-1/RB-2）补齐最后两块：Work Unit 完成（`completed`）前置单元绑定的新鲜验证证据门（all-match，拒绝零副作用，git/指纹读取失败 fail-closed）；任务可经 state 的 `repository.root` 绑定专属 Git 仓库根，Stop 完成门按任务逐个求值（同根快照缓存、单任务仓库故障只降级该任务），多仓工作区不再整体降级。CI 覆盖 ubuntu + windows × Python 3.8/3.13。

**v2.1-alpha1 控制平面收口（M1-M3）**——把"存在但可被绕过"的机制变成不可绕过的机器事实：派发许可（dispatch permit + PreToolUse 拒绝门，实施者类型无有效许可即 deny，重放/过期/伪造机械拒绝）；执行策略授权事实源（并发预算硬上限 4、跨额度窗口自动续跑默认关闭、升档须用户确认并落盘审计）；runtime 观察到的 Agent 生命周期（PostToolUse 自动记账，与手写事件互不替代）；崩溃恢复三件套——SessionStart 自动注入恢复上下文（新会话无需提醒即知未完成任务）、四分 reconcile（捞结果/进度包续作/干净重派/人工裁决，不再一律重派）、事务点自动刷新的 resume manifest；runtime CLI 入口取代内联调用。详见 CHANGELOG 2.1.0-alpha1。

**v2.1-alpha2 第二批收口（M4-M6）**——把有界并行、额度决策与证据溯源收进 runtime 确定性事实：dispatch wave 批量事务（`prepare_dispatch_wave` 一次签发整批许可与 marker，wave 成员同一回合并发派出——禁止"等第一个返回再派下一个"，默认并发 2 / 上限 4、额度预算折算，wave permit 成员资格环防旧许可重放）；运行时额度解析（`quota-resolve` 四级层级，绝不默认 AVAILABLE）与授权续跑链（`quota-exhausted` 四态授权 + 窗口预算 + 自足一次性 wake，预算耗尽转 `waiting_user`）；验证 / 审查溯源 receipts（`verify-unit` / `verify-task` runtime 亲测 + durable receipt，`review-record` 落 fresh ship receipt——完成门审查检查只认 receipt）。详见 CHANGELOG 2.1.0-alpha2。

**v2.1-alpha3 Runtime Integrity Closure**——依据审查 findings 逐条加固第二批机制：quota resume 控制面闭合（`quota-resume` 恢复时强制 provider 刷新、绝不采信睡眠期的新鲜缓存，EXHAUSTED 唤醒统一交 `scheduler.plan_resume` 按最晚 reset 规划，逐单元保留中断来源 `quota_interrupted_from`，reconcile 前置于重派——running-interrupted 单元不再被盲目置回 ready）；review 溯源绑定真实 reviewer invocation（PostToolUse marker 记账 `reviewer_invoked` 账本事实，回验链锚定 reviewer 白名单与 replay 幂等，完成门 receipt 来源闸只认追溯到真实调用的 ship receipt）；wave 事务补偿（permits 先于 wave 记录落盘，permit 签发或落盘失败全额回滚，wave 记录永不携带残缺租约）；permit 完整性 fail-closed（`validate_permit` 强制校验 id/mode/reason/时间戳/consumed 全链，含 `expires_at <= now` 过期边界）；multi-repo reconcile 双根分离（`reconcile_agent_run` 账本事实读 ledger 根、git 证据按任务绑定根求值）；另含 SH 三项加固——`wake-record` 幂等 + 写前授权复验、发布元数据纪律成文、Python 版本一致性核验。dogfood R1-R5 五场景实测（多窗口额度耗尽、worker 中途额度崩溃恢复、wave 残缺 permit 故障注入、伪 review 拒绝 + 真实 reviewer 链、账本根≠git 根 reconcile），全量 1328 测试绿、`validate_plugin` 15/15。详见 CHANGELOG 2.1.0-alpha3。

**v2.2 Quota Continuity Control Loop（当前开发线）**——把恢复链从 Agent 纪律升级为机械保证：双层额度模型（provider 四态 × execution phase，`quota-phase` 可查）+ Quota Epoch 身份与双 boundary（窗口滚动而非定时器触发构成新 epoch）+ 可选常驻额度 Watcher（`quota-watcher`，Session 休眠时独立维护真实额度时钟）+ Window Primer（新窗口物化用一次最小模型调用，默认结构性关闭）+ 额度订阅与每 epoch 恰一次的激活记账（QC-07）+ §15.1 resume commit point 唯一消费点 + Activation Transport 抽象（`recurring_bridge` stable）+ 完成门第 0 位 continuity health 三件套。窗口机制实测口径（2026-09-03 用户裁决，证据文档 `docs/GLM-Conductor-v2.2-Phase0-Primer-Experiments.md`）：reset_at 时刻窗口恢复 100%（周窗优先）；下一个 reset_at 只在新窗口内发生模型调用时才物化，纯查询绝不推进它；会话空闲推迟窗口起点。全量 2076 测试绿、`validate_plugin` 15/15。详见 CHANGELOG 2.2.0 (unreleased)。

## 路由矩阵

| Delegability | Assurance | 路由 | 实施 | 独立审查 |
| --- | --- | --- | --- | --- |
| low | standard | `solo` | GLM-5.3 主会话 | 否 |
| high | standard | `delegate` | 实施者子智能体 | 否 |
| low | high | `audit` | GLM-5.3 主会话 | 是 |
| high | high | `full` | 实施者子智能体 | 是 |

`executor`（标准/视觉实施）与 `continuity`（前台/可恢复/闲时）作为独立维度另行声明。工作流全景与架构细节见[架构文档](./docs/architecture.md)。

## 角色分工

| 角色 | 模型 | 工具 | 职责 |
| --- | --- | --- | --- |
| 主会话（架构师） | GLM-5.3 | 全部 | 需求澄清、架构与路由、任务分解、规格撰写、验证重跑、验收 |
| flash-implementer | GLM-5.3-Flash | 读写全套 | 执行有界的五段式实施规格 |
| visual-implementer | GLM-5.3-Flash | 读写 + 读图 | 视觉任务实施；需截图时返回 `VISUAL_CAPTURE_REQUEST`，主会话采集后续轮 |
| visual-reviewer | GLM-5.3-Flash | 只读 + 读图 | 视觉任务独立终审（`VISUAL REVIEW`） |
| glm-reviewer | GLM-5.3 | 只读白名单 | 文本任务独立终审（`GLM REVIEW`） |

## 环境要求

- [ZCode](https://zcode.z.ai) 客户端（Tested with 3.9.2；更早版本可能缺少多模态、定时任务或自定义子智能体能力）
- GLM Coding Plan（或 Z.ai 账号），已连接 GLM-5.3 与 GLM-5.3-Flash
- **Python 3.8+**（`python3` 在 PATH 中）——v2 强制层钩子的运行时。Windows 官方安装器默认加入 PATH；自检：新终端执行 `python3 --version` 有输出即满足

## 安装

安装前先在 ZCode 中打开一个工作区（插件管理页需要工作区处于打开状态）。安装/更新完成后**必须新建会话**，子智能体、技能与钩子才会加载。

### 从 GitHub 市场安装（推荐）

1. 打开 **Settings → 插件**，点右上角 **创建 → 添加插件市场**
2. 选择 GitHub 仓库来源，输入：

   ```
   https://github.com/Chengy257/glm-conductor
   ```

3. 校验通过后在 **个人** 分段安装 glm-conductor

### 从本地目录安装（开发/测试）

1. `git clone https://github.com/Chengy257/glm-conductor.git`
2. **Settings → 插件 → 创建 → 添加插件市场**，选择"本地清单文件或目录"，指向仓库根目录（`marketplace.json` 所在处），或直接拖入文件夹
3. 在 **个人** 分段安装

> ⚠️ 不要选择 `plugins/glm-conductor/.zcode-plugin/plugin.json`——那是插件清单而非市场清单，选错会得到收录 0 个插件的空市场。

### 验证安装

- **市场源**面板中 glm-conductor 显示收录 1 个插件（显示 0 个说明选错了清单文件）
- 新建会话后：Settings → Subagents 出现各角色子智能体；`/orchestration`、`/continuity`、`/glm-conductor:quota` 在 `/` 菜单可见
- 钩子自检：`echo '{}' | python3 ~/.zcode/cli/plugins/cache/glm-conductor/glm-conductor/<version>/hooks/stop_gate.py` 退出码为 0 即正常（版本目录用 `ls ~/.zcode/cli/plugins/cache/glm-conductor/glm-conductor/` 查看）

### 更新与卸载

- 更新：**市场源**面板单独刷新 glm-conductor 市场，再更新插件；新建会话生效
- 卸载：插件详情页卸载；移除整个市场在 **市场源** 面板操作

## 快速开始

新建会话后输入：

```
用 glm-conductor:orchestration 规划并实现这个功能，声明路由并完成验证
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

其他场景示例：

```
# 视觉任务（前端/界面改版，需独立视觉审查）
SELECTIVE ROUTE
mode: full / executor: visual-implementer / assurance: high
reason: bounded UI implementation with broad user-facing impact requires independent visual review

# 预计跨额度窗口的长任务
SELECTIVE ROUTE
mode: delegate / continuity: resumable
reason: multi-hour migration may be interrupted by availability windows; checkpoint enables safe resume
```

查看 Coding Plan 用量与四态评估：

```
/glm-conductor:quota          # 文本报告（窗口用量、reset 倒计时、调度建议）
/glm-conductor:quota --json   # 机器可读输出
```

## 命令与技能

| 命令 / 技能 | 用途 |
| --- | --- |
| `/orchestration` | 路由声明、委派契约、工作单元管理、审查流程 |
| `/continuity` | 长任务连续性：checkpoint、八步恢复、额度感知调度 |
| `/glm-conductor:quota` | 额度诊断（文本 + JSON；凭证自动解析，零落盘） |
| `enforcement` 技能 | 强制层用户侧解释：环境自检、报文含义、被拦截恢复方法 |

## 运行时限制

GLM Conductor 基于 ZCode 原生的本地会话生命周期机制，不是云调度器或后台守护进程。已登记的限制：

- **本地桌面环境**：客户端需保持运行、机器保持唤醒；远程/无头工作区不支持
- **强制层作用域**：钩子只在主会话触发（ZCode 子会话不触发钩子）——强制位于完成边界而非写前拦截；Bash 策略门控同样只覆盖主会话调用；`python3` 缺失时 fail-open 降级（stderr 报 `ENFORCEMENT DEGRADED`）
- **保守误拒（登记取舍）**：引用破坏性文本的字符串与 `git rm -r --cached` 会被 Bash 策略保守拒绝
- **有界并行 experimental**：上限 4；租约以主会话单编排者为前提，多会话并行操作同一任务目录不在支持面内
- **多仓工作区边界**：任务级 `repository.root` 绑定已支持（完成门按任务仓库求值）；同一 Git 仓库内多个 top-level active task 并存时的 diff attribution 仍非正式支持——保持"一仓一 active top-level task"（任务内多 Work Unit 有界并行照旧）
- **额度感知**：走已验证的 provider-api 监控端点（凭证零落盘）；不虚构原生 quota 接口、不硬编码 5 小时重置；端点失败时回退周期性探针。额度 Watcher 是手动启动的本地进程（`quota-watcher start`，不随机自启、不联网监听）；Window Primer 默认结构性关闭（`primer_enabled=false` + 三重授权闸，manual/notify 永不触发模型调用）
- **视觉拓扑**：Browser/Computer Use 为主会话专用——截图由主会话采集、Flash 系角色读图判定
- **子智能体**：不能再派生子智能体；只能看到会话启动时已连接的 MCP 服务
- **定时/闲时任务**：受 ZCode automation 机制与账号能力约束

完整限制清单与设计取舍见 [架构文档](./docs/architecture.md)。

## 发布纪律

GitHub Release 元数据与版本号语义绑定：`alpha` / `rc` 预发布版本必须勾选 **prerelease**（"Set as a pre-release"），仅 `stable` 正式版才以 latest 稳定版发布——市场源用户据此决定是否自动接收更新。

## 参考项目与致谢

本项目站在以下项目/生态的肩膀上（按参考深度排列）：

| 项目 | 参考点 |
| --- | --- |
| [sol-advisor](https://github.com/DannyMac180/sol-advisor)（MIT） | 本项目的起点灵感：选择性路由编排思想。glm-conductor 将其一维风险阶梯形式化为 Delegability × Assurance 双轴（GLM 两档模型体系下，"高风险通道"等价于 delegability:low 由旗舰亲自实施），并把子智能体天然新上下文用作"新鲜审查者"语义 |
| [zai-org/zai-coding-plugins](https://github.com/zai-org/zai-coding-plugins)（glm-plan-usage 插件） | quota 监控端点（`/api/monitor/usage/quota/limit`）与鉴权格式（`Authorization: <API Key>`，无 Bearer 前缀）的官方参照——v2 额度适配器的凭证纪律（§37）据此设计并经本机实测印证 |
| ZCode 官方插件生态（example-plugin、zcode-plugins-official） | 插件清单与 `hooks/hooks.json` 约定（`${ZCODE_PLUGIN_ROOT}`、process 型钩子）、Claude 兼容钩子载荷与 `permissionDecision` 输出契约、skills/agents 目录约定 |
| [Claude Code](https://claude.com/claude-code)（Anthropic） | hooks 事件模型（PreToolUse/Stop、`stop_hook_active` 续行上限语义）与 subagent 编排模式的契约参照 |
| CodexBar 等社区额度监控工具 | Coding Plan 用量端点行为的交叉印证来源之一 |

## 贡献

欢迎 Issue 与 Pull Request。开发注意：修改插件文件（子智能体定义、技能、清单）后需新建会话才能看到效果；提交前跑 `python3 scripts/validate_plugin.py` 与 `python3 -m unittest discover -s tests`，并确认 `plugin.json` / `marketplace.json` 为合法 JSON。

## 许可

[MIT](./LICENSE) © 2026 glm-conductor contributors
