# glm-conductor

![License](https://img.shields.io/badge/license-MIT-blue.svg)
![ZCode Plugin](https://img.shields.io/badge/ZCode-plugin-green.svg)
![Models](https://img.shields.io/badge/models-GLM--5.3%20%2F%20GLM--5.3--Flash-orange.svg)

> **Selective orchestration for GLM coding agents in ZCode.** —— GLM-5.3 指挥，GLM-5.3-Flash 实施，独立只读终审。

## 功能特性

- **双轴选择性路由**：主会话（GLM-5.3 架构师）按 Delegability（可委派性）× Assurance（保障等级）两个独立维度判断，在首次任务委派前声明机器可审计的 `SELECTIVE ROUTE`（solo / delegate / audit / full）
- **分层执行能力**：判断密集工作（规划、验证、验收、架构未定的实施）留在 GLM-5.3 主会话；有界、规格完备的高吞吐实施委派给 GLM-5.3-Flash（标准 / 视觉两通道）——这一异构分工同时带来额度与成本优势：Flash 的 API 价格约为旗舰 1/10，GLM Coding Plan 中额度消耗约为旗舰 1/3
- **独立只读终审**：assurance:high 的任务由全新上下文的只读审查者给出 `ship` / `fix-first` / `rethink` 裁决，任何修复使裁决失效
- **视觉通道**：前端/界面任务由 visual-implementer（GLM-5.3-Flash 多模态）实施，主会话负责驱动采集截图、Flash 角色负责视觉判定——GLM-5.3 纯文本的边界被显式分工补齐
- **长任务连续性**：continuity 作为与路由正交的生命周期层（foreground / resumable / idle），checkpoint 检查点 + 结构化恢复流程，让长任务可以跨会话中断、跨额度窗口安全续跑（见下文[长任务连续性](#长任务连续性)）
- **额度感知连续性（v2 alpha3）**：resumable 任务可查询 Coding Plan 用量（已验证的 provider-api 监控端点，凭证零落盘）——四态评估（充足/高压/耗尽/未知）、EXHAUSTED 时按最晚窗口 reset 时间精确规划唤醒、PRESSURE 提前 checkpoint；凭证不可得或端点失败时 fail-open 回退周期性存活探针；`/glm-conductor:quota` 诊断命令随时查看用量与评估
- **强制层（v2 alpha2）**：完成契约由插件钩子确定性执行——Stop 完成门四重检查（① ownership：实际改动文件 ⊆ 声明范围，越界改动无法静默通过；② 验证：required 命令全部由主会话亲自跑过；③ 审查：high-assurance 任务须有 ship 裁决；④ 证据新鲜度：验证/审查证据经 `task_fingerprint`（基线修订 + 相关文件集归一化 sha256）绑定到记录时的仓库状态，任何后续编辑使证据过期并拦截完成）+ Layer B 派发注入（每次子代理派发前注入 ownership 提醒）+ Bash 策略门控（v2 beta1：表驱动 allow/ask/deny——破坏性命令恒拒，高保障任务的推送/迁移/发布/权限变更升级为 ask）；任务状态层（`state.json`）与 append-only 执行日志（`events.jsonl`）本地落盘；钩子失效按降级可见处理（`ENFORCEMENT DEGRADED`），绝不因强制层故障卡死会话（详见 [架构文档](./docs/architecture.md) §9）
- **基于证据的路由重估**：路由可双向变化（ROUTE REASSESSMENT），但必须附新观察到的证据
- **fail-closed 纪律**：所需子智能体或证据路径缺失时停止通道并提示，绝不静默降级或替换角色
- **结构化契约**：五段式实施规格 + IMPLEMENTATION REPORT——无证据的完成声明无效

## 为什么

GLM-5.3 与 GLM-5.3-Flash 的智力差距很小（AA 智能指数 60 vs 57），但 API 价格相差 10-20 倍，GLM Coding Plan 中 Flash 的额度消耗约为旗舰的 1/3。与其所有工作都用旗舰模型，不如按两个独立维度分级路由：

- **Delegability**：剩余实施是否足够有界、规格完备，可以委派给 Flash？
- **Assurance**：完成后是否需要一次全新上下文的独立终审？

两个维度交叉出四种路由，executor（标准实施 / 视觉实施）与 continuity（前台 / 可恢复 / 闲时）作为独立维度另行选择。

## 前置要求

- [ZCode](https://zcode.z.ai) 客户端。Tested with ZCode 3.9.2——更早版本可能缺少多模态、定时任务、闲时执行或自定义子智能体所需的行为
- GLM Coding Plan（或 Z.ai 账号），已连接 GLM-5.3 与 GLM-5.3-Flash
- **Python 3**（`python3` 可在 PATH 中调用）：v2 强制层钩子（Stop 完成门）的运行时。Windows 官方 Python 安装器默认勾选加入 PATH；自检：新终端执行 `python3 --version` 有输出即满足

## 安装

安装前先确认已在 ZCode 中打开一个工作区（插件管理页需要工作区处于打开状态）。以下方式任选其一，安装完成后**必须新建会话**，子智能体与技能才会被加载。

### 方式一：从 GitHub 仓库安装（推荐）

1. 打开 ZCode：**Settings → 插件**（Plugin Management）
2. 点右上角 **创建 → 添加插件市场**
3. 选择 GitHub 仓库来源，输入本仓库地址：

   ```
   https://github.com/Chengy257/glm-conductor
   ```

4. 校验通过后，插件会以市场名分组出现在 **个人** 分段，点击安装 glm-conductor

### 方式二：从本地目录安装（开发/测试）

1. 克隆本仓库：

   ```bash
   git clone https://github.com/Chengy257/glm-conductor.git
   ```

2. 打开 **Settings → 插件**，点右上角 **创建 → 添加插件市场**
3. 选择"本地清单文件或目录"，指向克隆得到的 `glm-conductor` 仓库根目录（根目录下的 `marketplace.json` 是市场清单；直接选择该文件也可以），或直接把文件夹拖入
4. 在 **个人** 分段中找到 glm-conductor，点击安装

> ⚠️ 常见错误：不要选择 `plugins/glm-conductor/.zcode-plugin/plugin.json`——那是**插件清单**，不是**市场清单**。把它当市场添加会得到一个收录 0 个插件的空市场，Discover 中不会出现可安装的卡片。

### 验证安装

1. 添加市场成功后，**市场源**面板（插件页搜索框上方齿轮图标）中 glm-conductor 应显示收录 1 个插件；若显示 0 个，说明选错了清单文件，移除该市场后重新添加
2. 安装并新建会话后确认：
   - Settings → Subagents 中出现各角色子智能体
   - `/orchestration` 与 `/continuity` 技能可用（`/` 菜单中可见）
   - 提示词中提及编排时，主会话会先输出 `SELECTIVE ROUTE` 声明

钩子（v2 强制层）说明：

- 插件钩子随会话启动装载，**仅在安装或更新插件后的新会话中生效**；当前会话不会获得新钩子
- 钩子脚本依赖 `python3`（见前置要求）。自检：执行 `echo '{}' | python3 ~/.zcode/cli/plugins/cache/glm-conductor/glm-conductor/<version>/hooks/stop_gate.py`（`<version>` 换成已安装版本号，目录不存在时用 `ls ~/.zcode/cli/plugins/cache/glm-conductor/glm-conductor/` 查看），退出码为 0 即正常（无活动任务时无任何输出）
- 钩子失效（如 python3 不在 PATH）时强制层按降级处理并在 stderr 报告 `ENFORCEMENT DEGRADED`，不会阻断普通会话

### 更新与卸载

- 更新：在 **市场源** 面板中单独刷新 glm-conductor 市场，再更新插件；更新后需新建会话生效
- 卸载：插件详情页卸载；如需移除整个市场，在 **市场源** 面板中移除

## 快速开始

新建会话后，在输入框输入：

```
用 glm-conductor:orchestration 规划并实现这个功能，声明路由并完成验证
```

或直接输入 `/orchestration`。

主会话会先输出 `SELECTIVE ROUTE` 声明（含双轴判断与理由），再按所选路由执行。示例：

```
SELECTIVE ROUTE
mode: delegate
delegability: high
assurance: standard
executor: flash-implementer
continuity: foreground
reason: implementation is bounded by explicit interfaces, owned files, and deterministic verification
```

视觉任务（前端/界面改版）的声明示例：

```
SELECTIVE ROUTE
mode: full
delegability: high
assurance: high
executor: visual-implementer
continuity: foreground
reason: bounded UI implementation with broad user-facing impact requires independent visual review
```

预计跨额度窗口的长任务声明示例：

```
SELECTIVE ROUTE
mode: delegate
delegability: high
assurance: standard
executor: flash-implementer
continuity: resumable
reason: multi-hour migration may be interrupted by availability windows; checkpoint enables safe resume
```

## 路由矩阵

| Delegability | Assurance | 路由 | 实施 | 独立审查 |
| --- | --- | --- | --- | --- |
| low | standard | `solo` | GLM-5.3 主会话 | 否 |
| high | standard | `delegate` | 实施者子智能体 | 否 |
| low | high | `audit` | GLM-5.3 主会话 | 是 |
| high | high | `full` | 实施者子智能体 | 是 |

语义要点：

- `delegate` 不是 `solo` 的"升级"——两者只是实施者不同（旗舰 vs Flash），delegate 表达"该实施已足够有界"
- `audit` = 主会话实施 + 独立终审，适合判断密集、敏感、架构重的工作
- `full` = 委派实施 + 独立终审，适合大规模但有界的工作（机械迁移、清晰规格的改版）
- 路由可基于新证据双向重估（ROUTE REASSESSMENT），不设单向阶梯

## 长任务连续性

continuity 是与路由**正交**的生命周期维度——回答"任务如果很长，如何持续执行"，不是第五种 route。由 `/continuity` 技能实现：

| 模式 | 适用 | 行为 |
| --- | --- | --- |
| `foreground`（默认） | 当前会话内可完成、需实时互动 | 正常执行，不创建任何 continuation |
| `resumable` | 长任务、可能跨额度窗口或因可用性中断 | 里程碑后写 checkpoint + 定时唤醒；唤醒后先检查再恢复 |
| `idle` | 非紧急、可无人值守 | 优先交给 ZCode 原生闲时任务执行（支持自定义模型的子智能体） |

核心机制：

- **CONTINUITY CHECKPOINT**：实质性里程碑后写入任务专属路径 `.glm-conductor/tasks/<task-id>/checkpoint.md`——每个长任务一个机械唯一的 TASK_ID（语义前缀+随机后缀，如 `redesign-settings-page-7f3a2c`），并行任务互不覆盖、互不删除（建议将 `.glm-conductor/` 加入 `.git/info/exclude` 本地排除，而非修改 tracked `.gitignore`）；checkpoint 记录目标、路由、已完成项、下一步与验证状态，是导航状态，不是仓库真相源
- **repository > checkpoint**：恢复时八步检查（目标 → checkpoint → 仓库状态 → diff → 变更是否仍在 → 目标是否已完成 → 验证状态 → 从 NEXT ACTION 继续），仓库真实状态始终优先；禁止盲目重播旧指令，禁止为恢复 checkpoint 回滚仓库新改动
- **安全周期性再激活**：定时唤醒做"检查-恢复或等待"，而非 sleep 到固定时间；恢复前重新声明 SELECTIVE ROUTE
- **明确的边界**：不虚构原生 quota 接口、不硬编码 5 小时重置；额度感知走已验证的 provider-api 监控端点（凭证零落盘，不可用时回退周期性探针），定时/闲时能力不可用时如实报告"手动可恢复"
- **完成即清理**：目标验收后只删除本任务目录（`.glm-conductor/tasks/<id>/`）与其关联的定时任务，避免幽灵唤醒，也不影响并行任务

## 角色

| 角色 | 模型 | 思考档位 | 工具 | 职责 |
| --- | --- | --- | --- | --- |
| 主会话（架构师） | GLM-5.3 | — | 全部 | 需求歧义解决、架构与路由、任务分解、五段式规格、diff 检查与验证重跑、路由重估、验收 |
| flash-implementer | GLM-5.3-Flash | high | 读写全套 | 执行有界的五段式实施规格，返回 IMPLEMENTATION REPORT |
| visual-implementer | GLM-5.3-Flash | high | 读写全套 + 读图 | 视觉任务实施：含 VISUAL ACCEPTANCE 的规格执行；需截图时返回 `VISUAL_CAPTURE_REQUEST` 结束本次调用，主会话采集后以携带完整状态的新调用开启下一轮，读图判定并有限次修正 |
| visual-reviewer | GLM-5.3-Flash | max | 只读白名单 + 读图 | 视觉任务独立视觉终审（`VISUAL REVIEW`）：亲自读截图对照验收标准，检查用户可见回归 |
| glm-reviewer | GLM-5.3 | max | 只读白名单 | 文本任务独立终审，输出 ship / fix-first / rethink 裁决与证据 |

> 视觉任务的分工：主会话（纯文本）只做驱动与采集，视觉判定全部由 Flash 多模态角色完成；长任务连续性（continuity）见 `/continuity` 技能。

## 工作流

```
用户目标
   |
   v
主会话规划（GLM-5.3 架构师）
   |
   v
双轴判断：Delegability × Assurance ── 必须在首次 Agent 调用之前
   |
   +-- solo (low+standard) ----→ 主会话实施 + 自审 + 验证
   |
   +-- delegate (high+standard) → 实施者执行规格 → 主会话验证
   |
   +-- audit (low+high) ------→ 主会话实施 → 主会话验证 → 只读审查者终审
   |
   +-- full (high+high) ------> 实施者执行规格 → 主会话验证 → 只读审查者终审
   |
   v
ROUTE REASSESSMENT（任何阶段，凭新证据双向重估）
   |
   v
主会话验收 → ship

──── CONTINUITY LAYER（与路由正交，贯穿全程）────
  foreground：会话内完成（默认）
  resumable：checkpoint + 定时唤醒 → 八步恢复检查 → 续跑
  idle：ZCode 原生闲时任务无人值守执行
```

## 与 sol-advisor 的关系

本项目受 [DannyMac180/sol-advisor](https://github.com/DannyMac180/sol-advisor)（MIT）启发，将其中针对 Codex 生态的选择性路由编排移植到 ZCode 与 GLM 双模型体系。主要适配：

1. **双轴路由替代一维风险阶梯**：GLM 体系只有两档模型，v1 的"高风险通道并入 solo"在 v2 中被形式化为 Delegability × Assurance 双轴——判断密集/高风险实施即 delegability:low，由旗舰亲自完成（详见 [架构文档](./docs/architecture.md)）
2. **TOML + 安装脚本改为 Markdown 目录约定**：子智能体与技能均以带 YAML frontmatter 的 Markdown 文件定义，由 ZCode 按目录约定自动发现，无需安装脚本
3. **子智能体天然新上下文**：ZCode 子智能体每次调用都是全新上下文，无需原项目的 fork 参数即可保证"新鲜审查者"语义

## 运行时限制

GLM Conductor 的连续性编排基于 ZCode 原生的本地会话生命周期机制，**不是独立的云调度器或后台守护进程**。已知限制：

- **依赖本地桌面环境**：面向 ZCode 本地桌面与本地项目工作区设计；桌面客户端需保持运行，机器需保持唤醒；远程/无头工作区不在支持范围
- **视觉拓扑**：Browser Use 与 Computer Use 为 ZCode 主会话专用（策略层禁止子代理使用）——视觉证据由主会话采集、Flash 系角色读图判定，反馈环跨多次调用完成（`VISUAL_CAPTURE_REQUEST`）
- **定时任务**：数量与频率受 ZCode automation 机制约束；只有从当前聊天创建的续作才能把结果送回当前会话，通用入口创建的分离 automation 不等价
- **闲时任务**：可用性与创建上限取决于 ZCode 版本与账号能力
- **子智能体**：不能再派生子智能体（结构天然扁平）；只能看到会话启动时已连接的 MCP 服务，跨会话恢复后需重新确认所需服务可用
- **子智能体运行方式**：前台调用受支持；后台子智能体不应假定可用（编排不依赖 run_in_background 语义）
- **额度感知（v2 alpha3）**：resumable 任务可经 `/glm-conductor:quota` 诊断与 provider-api 监控端点查询 Coding Plan 用量（5h/周窗、四态评估、reset 感知唤醒规划）；凭证不可得或端点失败时 fail-open 回退周期性存活探针——调度触发本身即探针，额度观察不作为路由轴
- **强制层（v2 beta1 边界）**：四重检查（ownership / 验证 / 审查 / 证据新鲜度）已接入完成门；Bash 策略门控只覆盖主会话调用（角色级 deny 由子智能体工具白名单负责）；引用破坏性文本的字符串会被保守误拒（登记取舍）；work-unit 任务图与有界并行属后续里程碑。钩子不作用于子代理内部（ZCode 子会话不触发钩子——故强制在完成边界而非写前拦截）；`python3` 须在 PATH（见前置要求），缺失时强制层降级（stderr 报 `ENFORCEMENT DEGRADED`，不阻断会话）；强制层随插件分发，仅安装/更新后的新会话生效

## 贡献

欢迎通过 Issue 与 Pull Request 参与。开发时注意：修改插件文件（子智能体定义、技能、清单）后需新建会话才能看到效果；提交前请确认 `plugin.json` 与 `marketplace.json` 仍为合法 JSON、agents 的 frontmatter 字段完整。

## 许可

[MIT](./LICENSE) © 2026 glm-conductor contributors

本项目的设计受 [sol-advisor](https://github.com/DannyMac180/sol-advisor)（MIT）启发，特此致谢。
