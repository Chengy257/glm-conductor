# GLM Conductor

[English](./README.en.md) | **简体中文**

![Version](https://img.shields.io/badge/version-2.0.0-blue.svg)
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

- **完成门四重检查**——① ownership：实际改动 ⊆ 声明范围；② 验证：required 命令全部由主会话亲自跑过；③ 审查：high-assurance 须有新鲜 ship 裁决；④ 证据新鲜度：验证/审查证据经 `task_fingerprint` 绑定到记录时的仓库状态，后续任何编辑使其过期并拦截完成
- **Bash 策略门控**——表驱动 allow/ask/deny：破坏性命令（rm -rf、git reset --hard、force push）恒拒；高保障任务的推送/迁移/发布/权限变更升级为 ask
- **降级可见**——强制层故障永不阻断会话（fail-open），stderr 报 `ENFORCEMENT DEGRADED`

**长任务与额度**

- **任务与工作单元管理**——Work Unit 依赖图（十状态生命周期、环校验、确定性拓扑序）、五道闸派发准入、证据对账式中断恢复（completed 不重跑）、显式 Join 强制任务级全局验证
- **文件租约与有界并行**（experimental）——全有或全无获取、异 owner 冲突拒绝；并行上限 4，默认串行
- **额度感知连续性**——查询 Coding Plan 用量（凭证零落盘），四态评估，EXHAUSTED 按最晚窗口 reset 精确规划唤醒；不可用时 fail-open 回退周期性探针；`/glm-conductor:quota` 随时诊断
- **跨会话恢复**——任务专属 checkpoint + 八步恢复检查，仓库真实状态始终优先于记录

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
- **额度感知**：走已验证的 provider-api 监控端点（凭证零落盘）；不虚构原生 quota 接口、不硬编码 5 小时重置；端点失败时回退周期性探针
- **视觉拓扑**：Browser/Computer Use 为主会话专用——截图由主会话采集、Flash 系角色读图判定
- **子智能体**：不能再派生子智能体；只能看到会话启动时已连接的 MCP 服务
- **定时/闲时任务**：受 ZCode automation 机制与账号能力约束

完整限制清单与设计取舍见 [架构文档](./docs/architecture.md)。

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
