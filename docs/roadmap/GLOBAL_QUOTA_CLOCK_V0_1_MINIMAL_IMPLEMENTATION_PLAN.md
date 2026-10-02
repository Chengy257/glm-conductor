# GlobalQuotaClock v0.1 — Minimal Implementation Plan

> **Status:** PLANNED — 2026-10-03 讨论锁定：GQC 独立立项，保持简单
> **实施仓库:** `Chengy257/ZcodeGlobalQuotaClock`（已建于 GitHub，2026-09-21 交接；本地未克隆，实施从 G1 交接核对开始）
> **父文档:** `GLOBAL_QUOTA_CLOCK_STANDALONE_PROJECT_PLAN.md`（总设计）/ `GLOBAL_QUOTA_CLOCK_EXTRACTION_INVENTORY.md`（可迁移资产）/ `GLOBAL_QUOTA_CLOCK_V0_1_IMPLEMENTATION_SPEC.md`（v2.3 时代规格，本计划按 2026-10 讨论收敛后覆盖其范围）
> **与 glm-conductor 的关系:** 完全独立实施；Conductor 不依赖 gqclock（任务侧唤醒协议独立成立），集成点是 v0.2 议题

## 1. Why（2026-10-03 讨论锁定的存在意义）

GQC 的意义不是「检测窗口时间变动」这一件事——任务侧唤醒轮每次 remain-waiting 强制刷新，对窗口变动已天然免疫。它的充分理由是**窗口真相的单一事实源**，具体由三层构成：

1. **锚点链的维持者**：`reset_at` 只在新窗口发生首次真实模型调用后物化——纯查询永远不推进它。任务不跑，窗口锚点链就断；下次任务启动拿到陈旧锚点。GQC 的 tick 把「窗口切换时物化新锚点」从任务生命周期里抽出，变成与任何任务无关的常驻轻量职能（锚定 = 物化时刻 + 5h）。
2. **观测与物化的分工载体**：查询（免费、不污染窗口状态）回答「现在有没有额度」；一次最小真实调用（有成本）才回答「下一轮什么时候 reset」。两者都由 tick 统一承担。
3. **规模价值**：N 个任务 / N 个项目共享 1 次探测与物化，而不是各自唤醒各自查询。

**保持简单**是硬约束：v0.1 只做三命令 + 驱动，明确不做的事见 §6。

## 2. Design invariants

- **INV-GQC-01（tick 幂等单发）**：`gqclock tick` 单次执行完成「观测 → 必要时物化 → 落账」并单行 JSON 汇报后结束；重复调用安全。「观测照常落账、失败不补偿」——retime/物化失败只更新 last_tick_at、输出失败标志、退出码 0，native watchdog 兜底（可迁移自 inventory 点名的旧 tick 语义）。
- **INV-GQC-02（禁直写宿主 SQLite）**：绝不编辑 ZCode 内部库（`tasks-index.sqlite` 等）、不依赖未公开 schema——v2.4 规格「Conductor must not directly edit ZCode internal SQLite scheduler state」边界原样继承到独立项目。旧 `retime_automation` 直写 adapter 仅作历史证据，采用前须逐项重新实测评估。
- **INV-GQC-03（账户级单一 state）**：锚点 state 落用户级目录（单账户单文件），形状向前兼容（未知键忽略）；无多任务注册表、无 per-task 状态。
- **INV-GQC-04（驱动是外部事实）**：tick 由一个 recurring ZCode Scheduled Task 周期调用（专用小会话，遵守宿主会话卫生惯例）；**不承诺精确 reset 时激活**、不承诺离线正确性——调度精度归宿主，gqclock 只保证被调用时的幂等正确。
- **INV-GQC-05（观测不虚构）**：reset_at 未知/不可解析 → 如实落 unknown，绝不外推窗口周期（与 glm-conductor「绝不以加 5 小时推导下一窗口」同源纪律）。

## 3. v0.1 surface（全部产出）

| 组件 | 内容 |
| --- | --- |
| `gqclock status` | 只读：当前锚点、上次 tick 时刻与结果、下一轮 reset_at（未知则 unknown） |
| `gqclock plan` | 只读：根据当前观测计算「下次 tick 该做什么」（等待 / 接近窗口边界需物化 / 需重试） |
| `gqclock tick` | 幂等单发主入口：观测 → 窗口切换时一次最小真实调用物化新 reset_at → 落账 → 单行 JSON 汇报 |
| state 文件 | 用户级目录单 JSON（锚点链：历次物化时刻 + 观测到的 reset_at + last_tick_at） |
| 驱动 prompt 模板 | Scheduled Task prompt：自足（自带 python 解释器路径槽 `{python}`=sys.executable、路径缺失自愈指引）、单行 JSON 后立即结束回合、绝不嵌套创建调度 |
| 自测 | 单元测试（锚点计算 / tick 幂等 / 物化决策）+ 一次真实宿主 Scheduled Task 驱动活证 |

## 4. Migratable assets（从 inventory 继承，逐项重估后采用）

- tick 语义样板：「观测照常落账、失败不补偿」+ 调度回合自我限权措辞（"Reply with the tick JSON in one line, then end the turn. Never call Cron tools."）。
- `{python}` 槽以 `sys.executable` 代入（消除 Windows 无 `python3` 启动器首跳失败）。
- prompt 自愈指引（路径缺失时去缓存 runtime 目录找 CLI）。
- `ForbiddenSqlTest` 思路：源码文本级断言「变更语句不存在」，锚定 INV-GQC-02。
- 额度观测/解析：可参考 glm-conductor `runtime/quota/parser.py` 的归一化思路，但独立项目**自带实现、不 import 插件代码**（同源 MIT，复制需保留版权注记）。

## 5. Work packages

### G1 — 交接核对

clone `Chengy257/ZcodeGlobalQuotaClock`，盘点既有骨架（README/handoff 内容/分支），核对与父文档的差异；本计划的权威性以核对结果校准（若骨架已有实现，按 INV 逐项对齐后继续，不推倒）。

### G2 — 核心观测与锚点

- 额度端点观测（provider 查询 → 归一化 {status, reset_at, observed_at}）；
- 锚点链 state 读写（INV-GQC-03 形状）；
- 物化决策：窗口切换判定 + 一次最小真实调用的执行与失败语义（INV-GQC-01/05）。

### G3 — 三命令 CLI

`status` / `plan` / `tick`，退出码约定 0=成功 / 2=校验拒绝 / 1=运行期拒绝；单行 JSON 输出。

### G4 — 驱动配方

- Scheduled Task prompt 模板（§3 要求）；
- 专用小会话饲养配方（创建步骤 + 会话卫生说明，写入项目 README）。

### G5 — 自测与收口

- 单元测试 + 一次真实驱动活证（tick 被真实 Scheduled Task 调用 ≥2 轮，锚点链前进或如实 unknown）；
- 项目 README（为何存在 / 三命令 / 驱动 / 边界诚实声明）。

## 6. Non-goals（v0.1 明确不做）

- 多任务/多项目注册表、订阅、推送、webhook；
- 精确 reset 时激活承诺、离线正确性承诺；
- 直写宿主调度库（INV-GQC-02 永久边界）；
- 与 glm-conductor 的程序内集成（任务唤醒轮读 `gqclock status` 作为锚点来源——**v0.2 议题**，且以「可选增强、协议不变」为前提）；
- 多账户支持（单账户单 state）。

## 7. 与 glm-conductor v2.5 的关系

- v2.5（见 `V2_5_TASK_LAUNCH_CONTRACT_AND_PRECISE_WAKE_PLAN.md`）的精确唤醒（I4）**不依赖 gqclock**：任务侧每次 remain-waiting 自带强制刷新，one-shot 用当次观测 reset_at。
- gqclock 落地后的增益只是「观测质量与共享」：多任务读同一锚点链、任务不跑锚点也前进。
- 两者解耦推进，均不阻塞对方。

## 8. Handoff

- 实施授权：独立仓库、独立专用会话（遵守常驻 automation 会话卫生）；
- G1 先行，G1 结论若与父文档/骨架冲突，回本计划修订后再继续；
- 首个实施 commit 携带本计划文档副本入独立仓库。
