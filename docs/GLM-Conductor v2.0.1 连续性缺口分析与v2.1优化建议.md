# GLM Conductor v2.0.1 连续性缺口分析与 v2.1 优化建议

| 项目 | 内容 |
|------|------|
| **问题来源** | v2.0.1 Runtime Hardening 实施会话的活体观察（dogfood） |
| **发现日期** | 2026-08-29 |
| **触发事件** | 实施会话因 Coding Plan 5 小时额度窗口耗尽而中断；恢复依赖用户手动发起，GLM Conductor 的任务持续性（continuity）机制全程未自动生效 |
| **文档定位** | v2.1 演进的问题记录与优化依据（本轮仅记录分析，暂不实施） |
| **关联文档** | `docs/GLM-Conductor-v2.0.0-全面审查与v2.0.1加固建议.md`（§5 Quota-Aware Continuity 审查结论、§9 v2.1 演进建议） |

## 1. 问题描述

v2.0.1 实施会话（active task `v201-hardening-a3f8d2`，route 声明 `continuity: resumable`）在全部 9 个工作单元完成、代码已提交后的收尾阶段遭遇额度窗口耗尽，会话中断。窗口重置后，会话由用户手动恢复。在此期间：

- 没有任何自动唤醒发生（无 ZCode 定时任务被创建）；
- 新会话启动时没有任何机制提示「存在未收尾的活动任务」；
- 任务的剩余收尾动作（推送 v2-dev、CI 确认、发布准备）在用户回来之前保持停滞。

**核心观察：v2/v2.0.1 把完成契约（completion）从 Prompt 约定升级成了运行时强制，但连续性契约（continuity）仍停留在 Prompt 层——全部机制都依赖「模型在场且记得」，而中断恰恰发生在模型不在场的时候。**

## 2. 事实与证据

以下事实全部来自实施会话的第一手观察（journal / state.json / 终端输出）：

1. **额度观测是时点性的**。会话中每次派发前主会话手动执行 quota 诊断（`runtime.quota.report`），读数始终 AVAILABLE（最后一次实测余量 97%）。窗口耗尽发生在两次操作之间的数小时里，无任何组件在 turn 之外重新检查。`plan_resume()` 具备按 reset 时刻规划唤醒的能力，但它的触发前提是「有人在场调用它」。
2. **无新会话恢复注入**。窗口重置后的新会话启动时，superpowers 插件的 SessionStart 钩子注入了开场上下文（证明 SessionStart 注入机制在 ZCode 真实可用），而 glm-conductor 没有对应钩子——v2.0.1 新建的恢复语义（`reconcile_interrupted` 三分对账、`recover_leases` 租约回收、`discover_tasks` 四分类）全部依赖下一会话**主动想起**去调用。
3. **resumable 协议未被执行**。continuity 技能要求 resumable 任务写 CONTINUITY CHECKPOINT、在 EXHAUSTED 时安排唤醒；实施会话（由插件作者本人驱动）全程未写 checkpoint、未建定时唤醒，实际依靠的是 ZCode 主会话的跨会话 auto-memory——普通用户会话没有这层保险。**作者本人都会漏，是「协议靠模型记得」不可靠的最直接证据。**
4. **幸运边界**。中断点恰好落在全部单元 completed、代码已提交的干净边界。若中断发生在某单元 running 中，恢复语义虽在（对账建议 + 租约 TTL 过期回收），入口仍然是缺失的第 2 条——nothing would have invoked them。
5. **多仓工作区使完成门全程降级**。实施会话的工作区根（`C:\Users\user\ZCodeProject`）不是 git 仓库，Stop 完成门按 `ZCODE_PROJECT_DIR` 定位被检仓库，全程走 `git_unavailable` 降级（stderr 可见但不拦截），完成门协议由主会话手动履行；收官阶段不得不把任务账本移入插件仓库目录内，才完成了 finalizing → gate → completed 的真实链路（该链路本身验证成功，journal 留有完整四事件审计）。**一个工作区含多个 git 仓库是常见形态，该形态下强制层整体降级是结构性缺口。**
6. **派发额度闸靠手传参数**。`plan_dispatch` / `task_manager.prepare_dispatch` 的 `quota_status` 由调用方手传（本会话每次手传 AVAILABLE），运行时不自行取数——又一个模型纪律依赖点。

## 3. 根因分析

| # | 根因 | 层次 | 本会话证据 |
|---|------|------|-----------|
| R1 | quota 观测只在模型在场的时点发生，无 turn 之外的连续观测 | 运行时 | 证据 1 |
| R2 | 缺少新会话的恢复入口注入，恢复语义只有 API 没有触发面 | 运行时 | 证据 2、4 |
| R3 | continuity 协议（checkpoint / 唤醒）是提示层约定，无运行时落点 | 协议 | 证据 3 |
| R4 | 完成门按单一根目录定位，多仓工作区整体降级。**已在 v2.0.1 release hardening 修复**（804e3f8：repository.root 绑定 + per-task 求值） | 运行时 | 证据 5 |
| R5 | 派发准入的额度判断依赖调用方手传 | 运行时 | 证据 6 |

共性主题：**连续性能力全部是 advisory（建议 / 约定 / 手动调用），没有一个进入 v2 已建立的 enforcement 通道（钩子 / 事务边界 / 状态机）。**

## 4. 优化建议

### A. 运行时强制层（把 v2 的「Prompt → Enforced」哲学延伸到 continuity）

**A1. SessionStart 恢复钩子（最高优先级）**
新会话启动时：`discover_tasks()` 四分类扫描 → 读中断任务 journal 尾部 → （可选，见 A4）缓存 quota 快照 → 以 additionalContext 注入「你有 N 个活动/中断任务 + 建议动作（`reconcile_interrupted` + `recover_leases` + quota 诊断）」。将「恢复靠记得」变成「恢复建议自动出现」，同时覆盖额度耗尽、进程崩溃、用户直接关闭会话三类中断成因。纯本地、零网络、直接复用 v2.0.1 既有模块，成本约一个工作包。

**A2. 事务边界自动检查点**
`finish_unit` / `commit_dispatch` 在事务提交时顺手落一份结构化 checkpoint（下一就绪单元、待验证命令、quota 摘要）。state.json 已是事实源，checkpoint 只补「下一步叙事」，使任何新会话或定时任务零上下文可续。消除 R3 的纪律依赖。

**A3. prepare_dispatch 内置 quota 获取**
`quota_status` 从必传参数改为可选自动取数（复用 provider 的 TTL 缓存，fail-open 回退 UNKNOWN 由调用方裁决），消除 R5。

**A4. Stop 时点额度探针（谨慎项）**
Stop 钩子在 5s 预算内做一次「缓存优先、带超时」的 quota 检查，EXHAUSTED 时在放行报文附带「建议转 waiting_quota + plan_resume 唤醒时刻」。把观测时点从「派发前」扩展到「完成声明时」，收窄 R1 的盲区。约束：探针失败绝不降级完成门本身；网络调用必须可跳过（缓存缺失时只记 UNKNOWN 不阻塞）。

### B. 桥接层（守住「不自建 daemon」的既定边界）

**B1. `/glm-conductor:resume` 命令**
读 checkpoint + `plan_resume()` 输出，产出两件事：恢复动作清单（对账 / 租约回收 / 待跑验证命令）+ 一个建议用户确认的 ZCode 定时唤醒（按 reset+grace 计算时刻）。插件不能在会话死亡后自行行动，但可以把「ZCode 原生定时 + 新会话」两侧需要的信息与触发机械地准备好。

**B2. 任务级 repo 指针 / 根发现改进**
state.json 增可选 repo 路径字段，或钩子根发现改为「自 `ZCODE_PROJECT_DIR` 起向上/向下查找含 `.glm-conductor` 的 git 根」，消除 R4——本会话收官被迫移动账本即其直接代价。

> **已在 v2.0.1 release hardening 修复**（804e3f8）：state.json 增可选顶层 `repository.root` 绑定（`bind_repository_root` / `bound_repository_root` / `resolve_repository_root`），Stop 完成门按任务逐个解析其绑定仓库根求值（legacy 无绑定回退账本根，行为不变）；账本根与 git 根分离，账本（state/journal/lease）恒在账本根，多仓工作区不再需要移动账本、也不再整体降级（单任务仓库故障按任务隔离降级）。

### C. 协议层

**C1. continuity 技能检查单化**
「EXHAUSTED 时该做什么」从描述性文字改为检查单式必选步骤（写 checkpoint、建定时唤醒、转 waiting_quota）；「resumable 任务的会话开始协议」补「先跑恢复入口」步骤。依据即 R3：作者本人也会漏。

### 优先级与成本预估

| 优先级 | 项 | 成本 | 直接命中的根因 |
|--------|----|------|--------------|
| 1 | A1 SessionStart 恢复钩子 | 低（一个工作包，纯本地） | R2（兼收 R1/R3 的兜底） |
| 2 | A2 事务边界自动检查点 | 低 | R3 |
| 3 | B1 resume 命令桥 | 中 | R1/R3 |
| 4 | B2 repo 指针/根发现（已在 v2.0.1 release hardening 落地，见 B2 注） | 中 | R4 |
| 5 | A3 派发内置取数 | 低 | R5 |
| 6 | A4 Stop 探针 | 中（含超时纪律） | R1 |
| 7 | C1 技能检查单 | 低 | R3（与 A2 互补） |

## 5. 边界声明

- ZCode 插件无法在会话死亡后产生任何进程动作。「无 daemon」是 v2 审查文档（§5）确认的有意边界，本报告不挑战它。
- 因此「自动续跑」永远等于「ZCode 原生定时任务 + 新会话注入」的组合；插件侧的职责是把这两者所需的信息（checkpoint、唤醒时刻、恢复清单）与触发面（钩子、命令）机械备齐。
- A4 在钩子内做网络访问是全清单中唯一的例外项，若实测超时纪律不可靠应降级为纯缓存读取或移除。

## 6. 与 v2.1 既有方向的关系

v2.0.0 审查文档 §9 为 v2.1 规划的主线是 evidence provenance（统一 verify wrapper、review invocation 绑定）与 quota-resume-dispatch 闭环的高层 Task Manager API。本报告与其互补而非竞争：

- provenance 解决「完成后可信」（证据是谁产生的）；
- 本报告解决「中断后可续」（中断时谁知道、谁提醒、谁接手）；
- 两者在 v2.1 的交汇点是审查文档已建议的「Quota → scheduled wake → force refresh → reconcile → dispatch 高层 API」——A1/A2/B1 是它的入口与信息基座。

## 附录：本会话 dogfood 的其他工程发现（次要，随记）

1. **Layer B ownership 注入活体生效**：每次 Agent 派发时 PreToolUse 钩子按任务声明注入 ownership 提醒，行为与契约一致。
2. **`prepare_dispatch` 就绪提升缺口**：依赖满足后单元停留在 pending，task_manager 缺「就绪提升」入口，靠调用方手工 `transition_work_unit(pending→ready)`（dogfood 当场暴露，已在 v2.0.1 以 `refresh_readiness` 修复）。
3. **journal 事件 schema 早期漂移**：H4 之前主会话手写 verification 事件使用 `commands/passed` 字段，与 `reconcile` 证据扫描口径（`command/pass/unit`）不一致——单元级证据若无唯一写入口（`record_unit_verification`）极易再次漂移，验证了 H6 设计。
4. **本机（zh-CN locale）与 en-US CI 的编码差异曾致 CI Windows 矩阵失败**：钩子按契约恒写 UTF-8，测试父进程若不显式指定 `encoding="utf-8"` 会按父进程 locale 严格解码中文报文（cp1252 下 `损坏` 的 0x8D 未定义直接 UnicodeDecodeError）——已修复测试辅助函数，属测试基建教训：**凡捕获钩子输出的测试必须显式 UTF-8 解码**。
5. **RB-2 修复的收官活体验证**：本 release hardening 补丁任务自身即以拆分账本形态收官——workspace 非 git 仓库、任务经 `repository.root` 绑定嵌套的插件仓库，Stop 完成门按任务绑定仓库求值、账本留在 workspace 根，finalizing → gate → completed 全链路真实走通——即证据 5 所述结构性缺口修复后的形态。
