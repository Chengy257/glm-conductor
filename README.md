# glm-advisor

![License](https://img.shields.io/badge/license-MIT-blue.svg)
![ZCode Plugin](https://img.shields.io/badge/ZCode-plugin-green.svg)
![Models](https://img.shields.io/badge/models-GLM--5.3%20%2F%20GLM--5.3--Flash-orange.svg)

> ZCode 的 GLM 双模型编排插件 —— GLM-5.3 指挥，GLM-5.3-Flash 实施，独立只读终审。

## 功能特性

- **双轴选择性路由**：主会话（GLM-5.3 架构师）按 Delegability（可委派性）× Assurance（保障等级）两个独立维度判断，在首次任务委派前声明机器可审计的 `SELECTIVE ROUTE`（solo / delegate / audit / full）
- **成本优化分工**：判断密集工作（规划、验证、验收、架构未定的实施）留在 GLM-5.3 主会话；有界、规格完备的高吞吐实施委派给价格约为旗舰 1/10 的 GLM-5.3-Flash
- **独立只读终审**：assurance:high 的任务由全新上下文的只读审查者给出 `ship` / `fix-first` / `rethink` 裁决，任何修复使裁决失效
- **基于证据的路由重估**：路由可双向变化（ROUTE REASSESSMENT），但必须附新观察到的证据
- **fail-closed 纪律**：所需子智能体或证据路径缺失时停止通道并提示，绝不静默降级或替换角色
- **结构化契约**：五段式实施规格 + IMPLEMENTATION REPORT——无证据的完成声明无效

## 为什么

GLM-5.3 与 GLM-5.3-Flash 的智力差距很小（AA 智能指数 60 vs 57），但 API 价格相差 10-20 倍，GLM Coding Plan 中 Flash 的额度消耗约为旗舰的 1/3。与其所有工作都用旗舰模型，不如按两个独立维度分级路由：

- **Delegability**：剩余实施是否足够有界、规格完备，可以委派给 Flash？
- **Assurance**：完成后是否需要一次全新上下文的独立终审？

两个维度交叉出四种路由，executor（标准实施 / 视觉实施）与 continuity（前台 / 可恢复 / 闲时）作为独立维度另行选择。

## 前置要求

- [ZCode](https://zcode.z.ai) 客户端
- GLM Coding Plan（或 Z.ai 账号），已连接 GLM-5.3 与 GLM-5.3-Flash

## 安装

安装前先确认已在 ZCode 中打开一个工作区（插件管理页需要工作区处于打开状态）。以下方式任选其一，安装完成后**必须新建会话**，子智能体与技能才会被加载。

### 方式一：从 GitHub 仓库安装（推荐）

1. 打开 ZCode：**Settings → 插件**（Plugin Management）
2. 点右上角 **创建 → 添加插件市场**
3. 选择 GitHub 仓库来源，输入本仓库地址：

   ```
   https://github.com/Chengy257/glm-advisor
   ```

4. 校验通过后，插件会以市场名分组出现在 **个人** 分段，点击安装 glm-advisor

### 方式二：从本地目录安装（开发/测试）

1. 克隆本仓库：

   ```bash
   git clone https://github.com/Chengy257/glm-advisor.git
   ```

2. 打开 **Settings → 插件**，点右上角 **创建 → 添加插件市场**
3. 选择"本地清单文件或目录"，指向克隆得到的 `glm-advisor` 仓库根目录（根目录下的 `marketplace.json` 是市场清单；直接选择该文件也可以），或直接把文件夹拖入
4. 在 **个人** 分段中找到 glm-advisor，点击安装

> ⚠️ 常见错误：不要选择 `plugins/glm-advisor/.zcode-plugin/plugin.json`——那是**插件清单**，不是**市场清单**。把它当市场添加会得到一个收录 0 个插件的空市场，Discover 中不会出现可安装的卡片。

### 验证安装

1. 添加市场成功后，**市场源**面板（插件页搜索框上方齿轮图标）中 glm-advisor 应显示收录 1 个插件；若显示 0 个，说明选错了清单文件，移除该市场后重新添加
2. 安装并新建会话后确认：
   - Settings → Subagents 中出现各角色子智能体
   - `/orchestration` 技能可用（`/` 菜单中可见）
   - 提示词中提及编排时，主会话会先输出 `SELECTIVE ROUTE` 声明

### 更新与卸载

- 更新：在 **市场源** 面板中单独刷新 glm-advisor 市场，再更新插件；更新后需新建会话生效
- 卸载：插件详情页卸载；如需移除整个市场，在 **市场源** 面板中移除

## 快速开始

新建会话后，在输入框输入：

```
用 glm-advisor:orchestration 规划并实现这个功能，声明路由并完成验证
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

## 角色

| 角色 | 模型 | 思考档位 | 工具 | 职责 |
| --- | --- | --- | --- | --- |
| 主会话（架构师） | GLM-5.3 | — | 全部 | 需求歧义解决、架构与路由、任务分解、五段式规格、diff 检查与验证重跑、路由重估、验收 |
| flash-implementer | GLM-5.3-Flash | high | 读写全套 | 执行有界的五段式实施规格，返回 IMPLEMENTATION REPORT |
| glm-reviewer | GLM-5.3 | max | 只读白名单 | 文本任务独立终审，输出 ship / fix-first / rethink 裁决与证据 |

> v2 将增加视觉通道角色（visual-implementer / visual-reviewer，基于 GLM-5.3-Flash 的多模态能力）与长任务连续性技能（continuity），架构规范见 [docs/](./docs/)。

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
```

## 与 sol-advisor 的关系

本项目受 [DannyMac180/sol-advisor](https://github.com/DannyMac180/sol-advisor)（MIT）启发，将其中针对 Codex 生态的选择性路由编排移植到 ZCode 与 GLM 双模型体系。主要适配：

1. **双轴路由替代一维风险阶梯**：GLM 体系只有两档模型，v1 的"高风险通道并入 solo"在 v2 中被形式化为 Delegability × Assurance 双轴——判断密集/高风险实施即 delegability:low，由旗舰亲自完成（详见 [v2 架构规范](./docs/)）
2. **TOML + 安装脚本改为 Markdown 目录约定**：子智能体与技能均以带 YAML frontmatter 的 Markdown 文件定义，由 ZCode 按目录约定自动发现，无需安装脚本
3. **子智能体天然新上下文**：ZCode 子智能体每次调用都是全新上下文，无需原项目的 fork 参数即可保证"新鲜审查者"语义

## 贡献

欢迎通过 Issue 与 Pull Request 参与。开发时注意：修改插件文件（子智能体定义、技能、清单）后需新建会话才能看到效果；提交前请确认 `plugin.json` 与 `marketplace.json` 仍为合法 JSON、agents 的 frontmatter 字段完整。

## 许可

[MIT](./LICENSE) © 2026 glm-advisor contributors

本项目的设计受 [sol-advisor](https://github.com/DannyMac180/sol-advisor)（MIT）启发，特此致谢。
