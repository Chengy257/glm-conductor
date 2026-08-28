# GLM Conductor v2 — 实施计划与工作块拆解

> 日期：2026-08-28（Phase 0 完成后）
> 依据：`glm-conductor-v2-upgrade-guide-final.md`（含 Phase 0 修订）、`glm-conductor-v2-phase0-runtime-verification.md`
> 性质：待用户确认后生效的主计划。每轮实施前主会话按本计划出块规格。

---

## 1. 已锁定决策（不再重议）

| 决策 | 内容 |
|---|---|
| **分支策略（2026-08-28 用户确认）** | v2 全部开发在 `v2-dev` 分支进行（含 2.0.0-alpha1 版本线）；`main` 保持在 v1.1.0 发布态（8dbe9a5），里程碑完成后再合入——避免市场用户收到 alpha 半成品升级 |
| Ownership | Layer A（Stop 完成门校验 touched⊆owned，确定性）+ Layer B（PreToolUse(Agent\|Task) 派发注入，提示级）；Layer C（子会话钩子继承）为可选跟踪项——FR 文稿存 `docs/zcode-fr-subagent-hook-inheritance.md`，**用户决定暂不做 issue 提交** |
| 标识 | TASK_ID 全面取代 CONTINUITY_ID；legacy checkpoint 字段在恢复时归一化读取（已在 v2-dev 完成：e8c79e2） |
| Quota | provider-api 完整实现：环境变量 `GLM_CONDUCTOR_QUOTA_API_KEY` 优先，ZCode provider 配置 apiKey 为文档化回退；周窗可选；fail-open |
| 钩子 | python3（node 不可用）；随插件 `hooks/hooks.json` 分发（自动启用 runner）；文档注明"仅安装/更新后的新会话生效" |
| 并行 | run_in_background 已实证；Phase 10 前必须先有租约 |
| 编排 | 每块：主会话写五段式规格 → flash-implementer 实施 → 主会话验证（diff+校验器+测试）→ assurance:high 块加 glm-reviewer 终审；修复后裁决失效重审 |

## 2. 工作块总表

依赖方向：B0 → B1 → B2 → (B3 → B4) → B5 → (B6 ∥ B7) → B8 → B9 → B10；X 类横切块随里程碑穿插。

| 块 | 内容 | 路由 | 依赖 | 里程碑 |
|---|---|---|---|---|
| B0.1 | 子会话钩子继承专项复测（插件式钩子 + 各类子代理） | 主会话设计+执行，flash 探针 | — | 前置门 |
| B0.2 | ZCode feature request 文稿（Layer C） | 主会话撰写，用户提交 | — | 跟踪 |
| B1.1 | TASK_ID 命名迁移（v1.1 六契约文件+校验器+README+architecture.md） | delegate（精确编辑规格） | B0.1 | alpha1 |
| B1.2 | runtime/state.py（schema/读写/legacy 归一化）+ 单测 | delegate | B1.1 | alpha1 |
| B1.3 | runtime/journal.py（events.jsonl 追加/查询）+ 单测 | delegate | B1.2 | alpha1 |
| B1.4 | orchestration/continuity 技能契约增补（TASK_ID/state/journal 用法） | **主会话 solo** | B1.2 | alpha1 |
| B2.1 | runtime/ownership.py（exact/prefix/glob 匹配）+ 单测 | delegate | B1.2 | alpha1 |
| B2.2 | hooks/stop_gate.py 骨架 + Layer A + hooks.json（python3、超时、降级策略） | 主会话定设计，delegate 实现 | B2.1 | alpha1 |
| B2.3 | hooks/pre_tool_use.py Layer B（Agent\|Task 注入） | delegate | B2.1 | alpha1 |
| B2.4 | fixture 仓库 + ownership 冒烟（§80-81 场景） | delegate 建 fixture，主会话执行 | B2.2 | alpha1 |
| B3.1 | runtime/fingerprint.py（normalized diff sha256，换行/路径归一）+ 单测 | delegate | B1.2 | alpha2 |
| B3.2 | 指纹状态写入（verification/review/visual-evidence）+ stale 检测 | delegate | B3.1 | alpha2 |
| B3.3 | 主会话记录指纹的时机契约（技能文档节） | **主会话 solo** | B3.2 | alpha2 |
| B4.1 | stop_gate 完整逻辑（ownership+验证+审查+stale 四重检查、可行动 block 报文） | delegate | B2.2/B3.2 | alpha2 |
| B4.2 | Stop 循环安全活体实测（3 次上限行为） | **主会话 solo** | B4.1 | alpha2 |
| B4.3 | 集成冒烟场景 3-6（§98） | flash fixture，主会话执行 | B4.1 | alpha2 |
| B5.1 | quota/parser.py + provider.py 抽象 + 11 类离线 fixtures + 单测 | delegate | — | alpha3 |
| B5.2 | quota/zai.py + bigmodel.py 适配器（allowlist/https/超时/限长/无凭证落盘） | delegate，**安全重点审查** | B5.1 | alpha3 |
| B5.3 | quota/scheduler.py（PRESSURE 检查点/EXHAUSTED max(reset)+grace/UNKNOWN 回退）+ 10 冒烟单测 | delegate | B5.1 | alpha3 |
| B5.4 | 凭证获取链（env 优先 + ZCode provider config 回退） | 主会话设计，delegate 实现 | B5.2 | alpha3 |
| B5.5 | `quota` 诊断命令（skill/command，文本+JSON 双输出） | delegate | B5.4 | alpha3 |
| B5.6 | 「无 quota API」措辞全面反转（六处文档 + 校验器检查 7a 规则重设计） | 主会话撰写，flash 机械执行 | B5.1 | alpha3 |
| B6.1 | ROUTING PREFLIGHT（报告模板 + orchestration 契约） | **主会话 solo** | B4 | beta1 |
| B6.2 | TASK CONTEXT PACK（五段式规格补充节） | **主会话 solo** | B4 | beta1 |
| B7.1 | runtime/policy.py（action×resource→allow/ask/deny）+ 单测 | delegate | B2 | beta1 |
| B7.2 | 主会话 Bash 写模式 + 高保障 ask 规则接线 + 实测 | delegate + 主会话 | B7.1 | beta1 |
| B8.1 | work_unit.py（schema/九状态/转换）+ 单测 | delegate | B1/B2 | beta2 |
| B8.2 | dependency.py（DAG 校验/环拒绝/就绪推导）+ 单测 | delegate | B8.1 | beta2 |
| B8.3 | dispatcher.py（max_workers/quota 准入接口）+ 单测 | delegate | B8.2/B5.3 | beta2 |
| B8.4 | 任务图恢复对账辅助 + 冒烟（串行多单元/依赖图/中断恢复） | delegate + 主会话 | B8.3 | beta2 |
| B8.5 | orchestration 技能增补（分解/dispatch/join 契约） | **主会话 solo** | B8.3 | beta2 |
| B9.1 | lease.py（租约/冲突拒绝/生命周期）+ 单测 | delegate | B8 | rc1 |
| B10.1 | 有界并行启用（max_workers/压制/join 集成）+ 并发冒烟 | delegate + 主会话实测 | B9.1 | rc1（可标 experimental） |
| X1 | 校验器 v2 分批扩展（每阶段对应新检查；§97 全清单） | delegate，主会话验收 | 各阶段 | 持续 |
| X2 | enforcement 技能（建立+随阶段扩充） | **主会话 solo** | B2 后 | 持续 |
| X3 | 文档波：architecture.md/README/CHANGELOG 按里程碑更新 | 主会话+flash 机械 | 各里程碑 | 持续 |
| X4 | 插件清单 hooks 声明 + 安装说明（新会话生效提示） | delegate | B2.2 | alpha1 |

## 3. 关键设计预置（实施前锁定，避免块内自行发挥）

1. **钩子失效降级**（B2.2 必须实现）：钩子进程启动失败/超时 → Layer A/B 按"非关键路径"处理（fail-open）+ stderr 明确 `ENFORCEMENT DEGRADED` 报告；enforcement 技能文档如实声明降级态。理由：钩子起不来时 fail-closed 会卡死所有会话；降级可见性优于假强制。
2. **python3 入口**：hooks.json `type: process, command: "python3"`；文档注明依赖 PATH 含 python3（Windows 官方安装器默认加入）；X4 安装说明含自检步骤（`/enforcement` 技能首条即环境自检）。跨机兼容性风险登记在案。
3. **TASK_ID 迁移兼容**：读 checkpoint 时 `TASK_ID ?? CONTINUITY_ID` 归一；校验器标记从 `CONTINUITY_ID`/`随机十六进制后缀` 换为 `TASK_ID` 同款格式标记。
4. **quota 凭证零落盘**：B5.2 审查重点——Authorization 头仅存在于请求构造处；日志/journal/异常栈一律脱敏；fixture 测试断言凭证不出现在任何产物。
5. **Stop 门幂等**：block 报文含缺失项精确清单；连续 block 至运行时 3 次上限后放行并在 journal 记 `gate_exhausted`（模型侧契约：此时必须向用户报告 blocked 而非声称完成）。

## 4. 会话节奏（预计 8-10 轮）

| 轮 | 内容 | 出口条件 |
|---|---|---|
| 1（下轮） | B0.1 复测 + B0.2 FR + B1.1 命名迁移 | 复测结论 + alpha1 第一块合入 |
| 2 | B1.2 + B1.3 + B1.4 + X1/X4 + glm-reviewer | 状态层就绪 |
| 3 | B2 全部 + B2.4 冒烟 + reviewer | **v2.0-alpha1** |
| 4 | B3 + B4 + B4.3 冒烟 + reviewer | **v2.0-alpha2** |
| 5 | B5 全部（quota 大块）+ reviewer | **v2.0-alpha3** |
| 6 | B6 + B7 + reviewer | **v2.0-beta1** |
| 7-8 | B8 全部 + reviewer | **v2.0-beta2** |
| 9 | B9 + B10 + 并发冒烟 | **v2.0-rc1** |
| 10 | stable 加固：校验器终版 + 文档终版 + 全量冒烟 + 发版 | **v2.0 stable** |

每轮通用协议：主会话按本计划出各块五段式规格 → 委派/自做 → 逐块验证（diff + `python3 scripts/validate_plugin.py` + 块单测）→ assurance:high 块 glm-reviewer 终审 → 逻辑分组提交 + CI 绿灯 → 轮末向用户汇报并确认下一轮。

## 5. 风险登记

| 风险 | 缓解 |
|---|---|
| python3 不在用户机 PATH（跨机） | 安装自检 + 文档声明 + 降级可见（§3.1/3.2） |
| 子会话钩子继承在 ZCode 版本更新后变化（两处未追踪构造点） | B0.1 专项复测；ZCode 升级后重跑；FR 跟踪 |
| Stop 门与用户交互习惯冲突（正常 solo 任务被误拦） | 仅"active task"（state.json 存在）才启用门；无状态文件零干预 |
| quota 端点改版 | 适配器隔离 + 显式 fallback 端点 + fixture 驱动 + fail-open |
| 钩子拖慢每次工具调用 | 轻量设计（§87）：毫秒级 JSON 读 + 单次 git 调用；超时 3-5s 上限 |
| scope 膨胀（P1.5 走向工作流引擎） | §75 边界清单为验收项；reviewer 对照检查 |

## 6. B0.2 feature request 文稿要点（主会话撰写后随轮 1 交付）

- 标题：Subagent tool calls should optionally fire plugin hooks (or expose a per-subagent hook runner)
- 依据：glm-conductor v2 Ownership/Permission 强制层需要；当前 `runExploreAgent` 构造子会话时不传 hookRunner（zcode.cjs 证据）
- 诉求：可选配置（如 agent 定义 frontmatter `hooks: inherit` 或 hooks.json 作用域声明）；保持默认关闭以兼容现有插件
- 附 Phase 0 报告链接

---

## 7. 验收对照

本计划执行完毕后应满足 v2 指南 §103 Definition of Done（已按 Phase 0 修订）全部条目，且每个里程碑对应 §101 里程碑清单。计划的任何偏离（块合并/拆分/顺序调整）需在轮末汇报中说明理由并获用户确认。
