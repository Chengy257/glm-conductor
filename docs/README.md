# GLM Conductor 文档索引

本目录是 GLM Conductor 文档的入口。v2.4 起当前真相源 = 仓库代码 + `architecture.md`（v2.4 口径）；其余内容按下列两组定位。

## 从这里开始（Start here）

- [README.md（仓库根）](../README.md) — 项目入口：v2.4 四支柱概览（选择性路由 / 原生 Workflow 执行 / 最小确定性保障 / 可选有界额度恢复）、安装与快速上手。中文镜像 [README.zh-CN.md](../README.zh-CN.md) 结构对齐。
- [core-concepts.md](core-concepts.md) — 核心概念：v2.4 语义编排层（路由矩阵 / 规范 DAG / Workflow 编译边界 / change_id 与完成守卫 / 有界额度恢复）的概念性说明。

## 技术参考（Technical Reference）

- [architecture.md](architecture.md) — **唯一架构真相源**：v2.4 运行时行为的权威技术描述（路由矩阵、静态 DAG、Workflow 编译器边界、宿主拥有的执行状态、ownership 冲突处理、单仓库写者守卫、任务状态、change_id、主会话验证、审查路径、完成守卫、视觉例外、最小额度恢复、Global Clock 分离）。
- [troubleshooting.md](troubleshooting.md) — 面向用户的排障手册：按「现象 → 判定 → 处置」组织（额度诊断、v2.3 遗留任务检测、写者守卫陈旧释放等），每条标注实现模块路径供核对。
- [CHANGELOG.md（仓库根）](../CHANGELOG.md) — 发布级变更记录（用户可见与维护者相关的变更，不含实施过程细节；2.4.0 条目含重基线概述、删除面统计、保留语义与迁移说明）。
- [GLOBAL_QUOTA_CLOCK_EXTRACTION_INVENTORY.md](roadmap/GLOBAL_QUOTA_CLOCK_EXTRACTION_INVENTORY.md) — Global Quota Clock 拆离清单：独立未来伴生项目的可复用能力目录与 must-not-migrate 边界。
- 技能文档 — 运行面操作契约的权威来源（各技能的 `references/` 内含模板与判据细节），位于 `plugins/glm-conductor/skills/`：
  - [orchestration/SKILL.md](../plugins/glm-conductor/skills/orchestration/SKILL.md) — 选择性路由、规范 DAG 构建与 delegate/full 的 Workflow 启动流程；
  - [enforcement/SKILL.md](../plugins/glm-conductor/skills/enforcement/SKILL.md) — 确定性保障运行时契约（写者守卫、change_id 新鲜度、四查完成守卫、被拦截时的恢复方法）；
  - [continuity/SKILL.md](../plugins/glm-conductor/skills/continuity/SKILL.md) — Workflow 恢复、waiting_quota、手动/自动有界恢复与原生 Scheduled Task 唤醒。

## Active review / implementation handoff（非架构真相源）

以下文档记录 v2.4 重基线、audit-fix 与最终发布收口。Phase 0–4、audit-fix（AF-01–AF-06）与 Final Release Closeout（FR-01–FR-04）均已完成，最终裁决 `MERGE_READY`（见审计报告 Final Release Closeout Addendum）；分支可合并 main。

- [V2_4_NATIVE_WORKFLOW_REBASELINE_SPEC.md](reviews/V2_4_NATIVE_WORKFLOW_REBASELINE_SPEC.md) — **v2.4 冻结规格**（含 W0 复验收口记录 §13.1）：职责切分、路由矩阵、静态 DAG、编译器边界、最小保障模型、legacy 兼容政策、模块处置表。
- 路线图四计划（`docs/roadmap/`，各 Phase 出垒记录已附于对应执行计划文末，出垒门均 PASSED）：
  - [Phase 1 — Native Semantic Core](roadmap/V2_4_PHASE_1_NATIVE_SEMANTIC_CORE_SPEC.md) + [Phase 1 Workflow Execution Plan](roadmap/V2_4_PHASE_1_WORKFLOW_EXECUTION_PLAN.md)（含 Phase 1 出垒记录）
  - [Phase 2 — State & Assurance Retirement](roadmap/V2_4_PHASE_2_STATE_ASSURANCE_RETIREMENT_SPEC.md) + [Phase 2 Workflow Execution Plan](roadmap/V2_4_PHASE_2_WORKFLOW_EXECUTION_PLAN.md)（含 Phase 2 出垒记录）
  - [Phase 3 — Quota Resume & Clock Extraction](roadmap/V2_4_PHASE_3_QUOTA_RESUME_EXTRACTION_SPEC.md) + [Phase 3 Workflow Execution Plan](roadmap/V2_4_PHASE_3_WORKFLOW_EXECUTION_PLAN.md)（含 Phase 3 出垒记录）
  - [Phase 4 — Integration & Release](roadmap/V2_4_PHASE_4_INTEGRATION_RELEASE_SPEC.md) + [Phase 4 Workflow Execution Plan](roadmap/V2_4_PHASE_4_WORKFLOW_EXECUTION_PLAN.md)（收口阶段：退役面审计、文档/技能重写、测试重置、唯一一次全量回归）
- [V2_4_RETIRED_SURFACE_AUDIT.md](reviews/V2_4_RETIRED_SURFACE_AUDIT.md) — Phase 4 P4-A 全仓退役面审计：22 个退役概念 × 六面的逐条命中分类（活性引用当场修复 / 历史证据保留 / 迁移注保留）。

- [V2_4_AUDIT_FIX_CLOSEOUT_PLAN.md](roadmap/V2_4_AUDIT_FIX_CLOSEOUT_PLAN.md) + [Detailed Spec](roadmap/V2_4_AUDIT_FIX_CLOSEOUT_IMPLEMENTATION_SPEC.md) — 已实施的 post-implementation audit-fix（AF-01–AF-06），保留为修复依据与证据链。
- [V2_4_FINAL_RELEASE_CLOSEOUT_PLAN.md](roadmap/V2_4_FINAL_RELEASE_CLOSEOUT_PLAN.md) + [Detailed Spec](roadmap/V2_4_FINAL_RELEASE_CLOSEOUT_IMPLEMENTATION_SPEC.md) — 最终发布收口 merge gate（FR-01 effective-root writer release、FR-02 fresh-session reviewer proof、FR-03 release-doc truth sync、FR-04 final regression/CI），已全部完成。
- [V2_4_AUDIT_FIX_CLOSEOUT_REPORT.md](reviews/V2_4_AUDIT_FIX_CLOSEOUT_REPORT.md) — audit-fix 实施与宿主证据报告，文末 Final Release Closeout Addendum（§A1–§A5）记录 FR-01–FR-04 证据与最终裁决 `MERGE_READY`。
- [V2_4_CLOSEOUT_REPORT.md](reviews/V2_4_CLOSEOUT_REPORT.md) — Phase 1–4 implementation closeout 基线；后续结论由 audit-fix 与 Final Release Closeout evidence supersede。
- ZCode 3.14 spike 证据（W0 之前的探索，保留为证据链）：
  - [ZCODE_3_14_NATIVE_WORKFLOW_REBASELINE_PLAN.md](reviews/ZCODE_3_14_NATIVE_WORKFLOW_REBASELINE_PLAN.md) — spike + re-baseline review 的可交付实施计划（WF-00–WF-25、决策闸、capability/module-disposition 产物）。
  - [ZCODE_3_14_NATIVE_WORKFLOW_RESULTS.md](reviews/ZCODE_3_14_NATIVE_WORKFLOW_RESULTS.md) — spike 结果与 re-baseline 建议（宿主/运行/模型事实记录）。
  - [ZCODE_3_14_NATIVE_WORKFLOW_EVIDENCE_TEMPLATE.md](reviews/ZCODE_3_14_NATIVE_WORKFLOW_EVIDENCE_TEMPLATE.md) — 实施端证据记录模板。
  - capability 矩阵与模块处置表：[ZCODE_3_14_NATIVE_WORKFLOW_CAPABILITY_MATRIX.tsv](reviews/ZCODE_3_14_NATIVE_WORKFLOW_CAPABILITY_MATRIX.tsv) / [ZCODE_3_14_NATIVE_WORKFLOW_MODULE_DISPOSITION.tsv](reviews/ZCODE_3_14_NATIVE_WORKFLOW_MODULE_DISPOSITION.tsv)（处置表已被 REBASELINE_SPEC §15 取代，保留为历史证据）。
