# GLM Conductor 文档索引

本目录是 GLM Conductor 文档的入口。当前真相源 = 仓库代码 + `architecture.md`；其余内容按下列两组定位。

## 从这里开始（Start here）

- [README.md（仓库根）](../README.md) — 项目入口：项目定位、三支柱概览、安装与快速上手。
- [core-concepts.md](core-concepts.md) — 核心概念：三个支柱（选择性路由编排 / 运行时强制层 / 额度感知连续性）与稳定边界的概念性说明。

## Active review / implementation handoff（非架构真相源）

以下文档用于正在进行的宿主兼容性实验与下一版本 re-baseline 审查。实验完成并作出架构裁决前，它们**不修改** `architecture.md` 的当前冻结结论。

- [ZCODE_3_14_NATIVE_WORKFLOW_REBASELINE_PLAN.md](reviews/ZCODE_3_14_NATIVE_WORKFLOW_REBASELINE_PLAN.md) — ZCode 3.14 native workflow compatibility spike + re-baseline review 的可交付实施计划；定义 WF-00–WF-25、决策闸、最终 capability/module-disposition 产物与最小测试纪律。
- [ZCODE_3_14_NATIVE_WORKFLOW_EVIDENCE_TEMPLATE.md](reviews/ZCODE_3_14_NATIVE_WORKFLOW_EVIDENCE_TEMPLATE.md) — 实施端证据记录模板：host/run/model/hook/并行写入/cancel-resume/quota 观测字段，以及最终返回格式。

## 技术参考（Technical Reference）

- [architecture.md](architecture.md) — **唯一架构真相源**：当前运行时行为的权威技术描述（状态模型、路由、派发事务、强制层、额度连续性、恢复、稳定边界）。
- [troubleshooting.md](troubleshooting.md) — 面向用户的排障手册：按「现象 → 判定 → 处置」组织的结论性行为事实，每条标注实现模块路径供核对。
- [CHANGELOG.md（仓库根）](../CHANGELOG.md) — 发布级变更记录（用户可见与维护者相关的变更，不含实施过程细节）。
- 技能文档 — 运行面操作契约的权威来源（各技能的 `references/` 内含模板与判据细节），位于 `plugins/glm-conductor/skills/`：
  - [orchestration/SKILL.md](../plugins/glm-conductor/skills/orchestration/SKILL.md) — 双轴选择性路由与委派契约；
  - [enforcement/SKILL.md](../plugins/glm-conductor/skills/enforcement/SKILL.md) — 强制层运行时契约（完成门、permit 门、指纹与 receipt、被拦截时的恢复方法）；
  - [continuity/SKILL.md](../plugins/glm-conductor/skills/continuity/SKILL.md) — 长任务生命周期（checkpoint、恢复、额度感知调度与授权续跑）。
