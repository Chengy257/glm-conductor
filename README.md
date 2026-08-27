# glm-advisor

![License](https://img.shields.io/badge/license-MIT-blue.svg)
![ZCode Plugin](https://img.shields.io/badge/ZCode-plugin-green.svg)
![Models](https://img.shields.io/badge/models-GLM--5.3%20%2F%20GLM--5.3--Flash-orange.svg)

> ZCode 的 GLM 双模型风险分级编排插件 —— GLM-5.3 指挥，GLM-5.3-Flash 实施，独立只读终审。

## 功能特性

- **风险分级选择性路由**：主会话（GLM-5.3 架构师）在任何任务委派之前声明机器可审计的 `SELECTIVE ROUTE`（solo / delegate / audit / full）与风险理由
- **成本优化分工**：判断密集工作（规划、验证、验收、高风险实施）留在 GLM-5.3 主会话；边界清晰的高吞吐实施委派给价格约为旗舰 1/10 的 GLM-5.3-Flash
- **独立只读终审**：audit / full 路由下，由全新上下文的 GLM-5.3 只读审查者给出 `ship` / `fix-first` / `rethink` 裁决，任何修复使裁决失效
- **fail-closed 纪律**：所需子智能体缺失时停止通道并提示检查安装，绝不静默降级或替换角色
- **结构化契约**：五段式实施规格（OBJECTIVE / FILES AND OWNERSHIP / INTERFACES / CONSTRAINTS / VERIFICATION）+ IMPLEMENTATION REPORT 返回模板——无证据的完成声明无效

## 为什么

GLM-5.3 与 GLM-5.3-Flash 的智力差距很小（AA 智能指数 60 vs 57），但 API 价格相差 10-20 倍，GLM Coding Plan 中 Flash 的额度消耗约为旗舰的 1/3。与其所有工作都用旗舰模型，不如按风险分级路由：让旗舰负责规划、验证与验收，让 Flash 承担高吞吐的常规实施，再用全新上下文的只读审查者为高风险交付加一道独立终审。glm-advisor 把这套选择性路由固化为 ZCode 插件，开箱即用。

## 前置要求

- [ZCode](https://zcode.z.ai) 客户端
- GLM Coding Plan（或 Z.ai 账号），已连接 GLM-5.3 与 GLM-5.3-Flash

## 安装

以下三种方式任选其一。安装完成后**必须新建会话**，子智能体与技能才会被发现和加载。

### 方式一：从 GitHub 仓库安装（推荐）

1. 打开 ZCode：Settings → Plugin Management → Discover
2. 点击 `+`，选择 GitHub 仓库，输入本仓库地址：

   ```
   https://github.com/<OWNER>/glm-advisor
   ```

3. 点击安装 glm-advisor

### 方式二：从本地目录安装

1. 克隆本仓库：

   ```bash
   git clone https://github.com/<OWNER>/glm-advisor.git
   ```

2. 打开 Settings → Plugin Management → Discover → `+`，选择克隆得到的 `glm-advisor` 目录
3. 点击安装

### 方式三：从 marketplace 清单文件安装

1. 按方式二克隆本仓库
2. 打开 Settings → Plugin Management → Discover → `+`，选择仓库内的 `.agents/plugins/marketplace.json`
3. 按提示完成安装

### 验证安装

新建会话后确认三处：

- Settings → Subagents 中出现 `flash-implementer` 与 `glm-reviewer`
- `/orchestration` 技能可用（`/` 菜单中可见）
- 提示词中提及编排时，主会话会先输出 `SELECTIVE ROUTE` 声明

## 快速开始

新建会话后，在输入框输入：

```
用 glm-advisor:orchestration 规划并实现这个功能，声明路由并完成验证
```

或直接输入 `/orchestration`。

主会话会先输出 `SELECTIVE ROUTE` 声明（模式 + 风险理由），再按所选路由执行。

## 四种路由

| 模式 | 场景 | 执行方式 |
| --- | --- | --- |
| solo（默认） | 风险可控的常规工作；判断密集、高风险或影响面广的实施工作 | 主会话自行规划、实施、测试并自审，不生成辅助代理 |
| delegate | 边界清晰、规格完备的有界工作 | flash-implementer 执行五段式规格，主会话检查完整 diff 并重跑验证 |
| audit | 主会话自行实施且需独立终审 | 主会话实施 + 验证后，全新 glm-reviewer 只读审查累积变更集 |
| full（例外） | 明确的宽泛/高风险场景 | flash-implementer 实施，主会话验证，再由全新 glm-reviewer 终审 |

注意：solo 吸收了高风险实施（与 sol-advisor 的差异）——GLM 体系没有 Terra 高风险通道，判断密集或高风险的实施工作由旗舰亲自完成。

## 角色

| 角色 | 模型 | 思考档位 | 工具 | 职责 |
| --- | --- | --- | --- | --- |
| 主会话（架构师） | GLM-5.3 | — | 全部 | 需求歧义解决、架构与路由、任务分解、五段式规格、diff 检查与验证重跑、升级决策、验收 |
| flash-implementer | GLM-5.3-Flash | high | 读写全套 | 执行边界清晰的五段式实施规格，返回 IMPLEMENTATION REPORT |
| glm-reviewer | GLM-5.3 | max | 只读白名单 | 独立终审，输出 ship / fix-first / rethink 裁决与证据 |

## 工作流

```
用户目标
   |
   v
主会话规划（GLM-5.3 架构师）
   |
   v
SELECTIVE ROUTE 声明（mode + risk）── 必须在首次 Agent 调用之前
   |
   +-- solo -----→ 主会话实施 + 自审 + 验证
   |
   +-- delegate -→ flash 实施 → 主会话验证
   |
   +-- audit ----→ 主会话实施 → 主会话验证 → glm-reviewer 只读终审
   |
   +-- full -----→ flash 实施 → 主会话验证 → glm-reviewer 只读终审
   |
   v
主会话验收 → ship
```

## 与 sol-advisor 的关系

本项目受 [DannyMac180/sol-advisor](https://github.com/DannyMac180/sol-advisor)（MIT）启发，将其中针对 Codex 生态的选择性路由编排移植到 ZCode 与 GLM 双模型体系。三处主要适配：

1. **Terra 高风险通道并入 solo**：GLM 体系没有 Terra 高风险通道，判断密集、高风险的实施工作直接由 GLM-5.3 主会话亲自完成
2. **TOML + 安装脚本改为 Markdown 目录约定**：子智能体与技能均以带 YAML frontmatter 的 Markdown 文件定义，由 ZCode 按目录约定自动发现，无需安装脚本
3. **子智能体天然新上下文**：ZCode 子智能体每次调用都是全新上下文，无需原项目的 fork 参数即可保证"新鲜审查者"语义

## 贡献

欢迎通过 Issue 与 Pull Request 参与。开发时注意：修改插件文件（子智能体定义、技能、清单）后需新建会话才能看到效果；提交前请确认 `plugin.json` 与 `marketplace.json` 仍为合法 JSON、agents 的 frontmatter 字段完整。

## 许可

[MIT](./LICENSE) © 2026 glm-advisor contributors

本项目的设计受 [sol-advisor](https://github.com/DannyMac180/sol-advisor)（MIT）启发，特此致谢。
