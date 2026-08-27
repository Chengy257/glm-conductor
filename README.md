# glm-advisor

ZCode 的 GLM 双模型风险分级编排插件——GLM-5.3 指挥，GLM-5.3-Flash 实施，独立只读终审。

## 为什么

GLM-5.3 与 GLM-5.3-Flash 的智力差距很小（AA 指数 60 vs 57），但价格相差 10-20 倍，Coding Plan 额度消耗约为旗舰的 1/3。合理的分工是：让旗舰（GLM-5.3）负责规划、验证与验收等判断密集工作，让 GLM-5.3-Flash 承担高吞吐的常规实施，再用全新上下文的只读审查者为高风险交付加一道独立终审。glm-advisor 把这套风险分级选择性路由固化为 ZCode 插件。

## 要求

- ZCode 客户端
- GLM Coding Plan（或 Z.ai 账号），已连接 GLM-5.3 与 GLM-5.3-Flash

## 安装

三种方式任选其一：

### 方式一：本地目录

1. 打开 ZCode 的 Settings → Plugin Management
2. 进入 Discover，点击 `+`
3. 选择本插件项目根目录（glm-advisor 目录）
4. 点击安装

### 方式二：marketplace 文件

1. 打开 Settings → Plugin Management → Discover，点击 `+`
2. 选择本仓库中的 `.agents/plugins/marketplace.json`
3. 按提示完成安装

### 方式三：GitHub

1. 将本仓库推送到 GitHub
2. 在 Settings → Plugin Management → Discover 中点击 `+`
3. 输入仓库地址完成添加与安装

安装后**必须新建会话**，子智能体与技能才会被发现和加载。

## 快速开始

新建会话后，在提示词中输入：

```
用 glm-advisor:orchestration 规划并实现这个功能，声明路由并完成验证
```

或直接输入 `/orchestration`。

主会话会先输出 SELECTIVE ROUTE 声明（模式 + 风险理由），再按所选路由执行。

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

## 致谢与许可

本项目以 [MIT](./LICENSE) 许可发布，致敬原项目 [sol-advisor](https://github.com/DannyMac180/sol-advisor) 的设计。
