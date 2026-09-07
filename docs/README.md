# GLM Conductor 文档索引

本目录是 GLM Conductor 文档的入口。当前真相源 = 仓库代码 + `architecture.md`；其余内容按下列三组定位。

## 从这里开始（Start here）

- [README.md（仓库根）](../README.md) — 项目入口：项目定位、三支柱概览、安装与快速上手。
- [core-concepts.md](core-concepts.md) — 核心概念：三个支柱（选择性路由编排 / 运行时强制层 / 额度感知连续性）与稳定边界的概念性说明。

## 技术参考（Technical Reference）

- [architecture.md](architecture.md) — **唯一架构真相源**：当前运行时行为的权威技术描述（状态模型、路由、派发事务、强制层、额度连续性、恢复、稳定边界）。
- [CHANGELOG.md（仓库根）](../CHANGELOG.md) — 发布级变更记录（用户可见与维护者相关的变更，不含实施过程细节）。
- 技能文档 — 运行面操作契约的权威来源（各技能的 `references/` 内含模板与判据细节），位于 `plugins/glm-conductor/skills/`：
  - [orchestration/SKILL.md](../plugins/glm-conductor/skills/orchestration/SKILL.md) — 双轴选择性路由与委派契约；
  - [enforcement/SKILL.md](../plugins/glm-conductor/skills/enforcement/SKILL.md) — 强制层运行时契约（完成门、permit 门、指纹与 receipt、被拦截时的恢复方法）；
  - [continuity/SKILL.md](../plugins/glm-conductor/skills/continuity/SKILL.md) — 长任务生命周期（checkpoint、恢复、额度感知调度与授权续跑）。

## 历史（History）——非权威存档

> **[history/](history/) 是存档，不是现行文档。** 其中内容是开发过程与发布证据（历史实施计划、实验与验证记录、架构校正讨论、发布门清单等），仅供追溯；其中的术语可能描述已被后续设计取代的中间方案，**不构成当前实现的依据**。当前真相 = 仓库代码 + [architecture.md](architecture.md) + [core-concepts.md](core-concepts.md)。

归档按版本分类，每个子目录内有独立的中文索引：

| 子目录 | 内容 |
| --- | --- |
| [history/v1/](history/v1/) | v1 时代的架构提案（[索引](history/v1/README.md)） |
| [history/v2.0/](history/v2.0/) | v2 实施计划、运行时验证、升级指南与 v2.0.x 加固 / 诊断记录（[索引](history/v2.0/README.md)） |
| [history/v2.1/](history/v2.1/) | v2.1 架构计划、运行完整性收口、实测记录与设计决策记录（[索引](history/v2.1/README.md)） |
| [history/v2.2/](history/v2.2/) | v2.2 控制环 / 控制面计划与校正、实验与运行验证、发布加固、稳定发布计划，以及 2.2.0 CHANGELOG 全文存档（[索引](history/v2.2/README.md)） |
| `history/v2.2.1/` | v2.2.1 需求计划与执行计划（发布收官时移入） |
| [history/reference/](history/reference/) | 宿主（ZCode）行为研究等参考资料——非本项目开发史（[索引](history/reference/README.md)） |
