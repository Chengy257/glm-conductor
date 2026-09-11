# GLM Conductor 核心概念

> **GLM Conductor 是面向 ZCode GLM Coding Agent 的确定性编排、执行保障与额度感知长任务连续性层。**
>
> 本文描述系统**现在是什么**：三个支柱与一条稳定边界，不叙述演化过程。每个机制陈述都可在 [architecture.md](architecture.md)（唯一架构真相源）与插件技能文档（`plugins/glm-conductor/skills/`）中核对。

GLM Conductor 是 ZCode 插件，为 GLM 双模型体系（GLM-5.3 主会话 + GLM-5.3-Flash 子智能体）提供编排运行时。三个支柱相互独立、组合使用：

| 支柱 | 回答的问题 |
| --- | --- |
| 选择性路由编排（Selective Orchestration） | 谁实施、如何验证、是否需要独立终审 |
| 运行时强制层（Mechanical Execution Assurance） | 哪些契约由运行时确定性保证，而非依赖模型自觉 |
| 额度感知连续性（Durable Quota-Aware Continuity） | 长任务跨会话、跨额度窗口中断后如何安全恢复 |

---

## 1. 选择性路由编排（Selective Orchestration）

主会话（GLM-5.3）始终担任唯一架构师：需求与歧义解决、架构与路由判断、任务分解、实施规格编写、完整 diff 检查与验证重跑、路由重估决策、最终验收。子智能体只能实施或审查，不得成为新的编排者——子智能体内不能再派生子智能体，结构天然扁平。

### 双轴路由

路由由两个独立轴共同决定，先分别回答、再查矩阵——不存在按风险单向递进的模型：

- **轴 A — Delegability（可委派性）**：剩余实施是否足够有界、规格足够完备，可以委派？目标 / 文件边界 / 接口 / 约束 / 验证均明确且架构已定 → high；架构未定、root cause 未知、实质歧义、判断密集 → low
- **轴 B — Assurance（保障等级）**：实施通过主会话验证后，一次全新上下文的独立终审是否有实质价值？影响面有限、回归风险可控 → standard；宽影响面、高回归风险、破坏性行为、大规模用户可见变更 → high

| Delegability | Assurance | Route | 实施 | 独立审查 |
| --- | --- | --- | --- | --- |
| low | standard | `solo` | GLM-5.3 主会话 | 否 |
| high | standard | `delegate` | 实施者子智能体 | 否 |
| low | high | `audit` | GLM-5.3 主会话 | 是 |
| high | high | `full` | 实施者子智能体 | 是 |

语义要点：`delegate` 不是比 `solo` 更高一级的路线——两者只是实施者不同（主会话实施 vs Flash 实施）；`audit` = 主会话实施 + 独立终审；`full` = 委派实施 + 独立终审。

### SELECTIVE ROUTE 声明

在任何 Agent 工具调用之前，主会话必须输出一次五字段声明（外加基于证据的 reason）：

```
SELECTIVE ROUTE
mode: solo | delegate | audit | full
delegability: low | high
assurance: standard | high
executor: main | flash-implementer | visual-implementer
continuity: foreground | resumable | idle
reason: <简明的、基于证据的理由>
```

- **executor 是独立的能力维度，不是第五种 route**：标准编码任务 → `flash-implementer`；视觉 / 交互任务 → `visual-implementer`（亲自读截图判定）。
- **assurance: high 时按任务模态引入全新上下文的只读审查者**：文本任务 → `glm-reviewer`（`GLM REVIEW`）；视觉任务 → `visual-reviewer`（`VISUAL REVIEW`）。裁决只有三种：`ship` / `fix-first` / `rethink`；任何修复使先前裁决失效，复审必须换全新审查者。
- **路由可双向重估（ROUTE REASSESSMENT）**：路由变化必须来自新观察到的证据，可上调也可下调；实施者的重估信号、审查者的 rethink 均为有效证据。无新证据不得变更路由。

### 委派契约与工作单元

- 委派使用**五段式实施规格**：OBJECTIVE / FILES AND OWNERSHIP / INTERFACES / CONSTRAINTS / VERIFICATION。实施者返回 IMPLEMENTATION REPORT——报告只是声明（implementation claim），验证证据（verification evidence）只存在于主会话亲自检查的 diff 与亲自重跑的命令输出中。
- 大任务分解为 **Work Unit**：每个单元必须有界到能接收一份完整规格，且必填 ownership 与 verification（无文件范围或无验证的单元不可派发）；单元间依赖用 `depends_on` 表达（同任务内引用、禁止环）。
- 派发准入由运行时纯决策器（`plan_dispatch`）把关：quota 四态闸 → ownership 不相交 → 租约闸（防并发写冲突）→ worker 预算。默认串行；有界并行上限 4（experimental），并行资格 = ownership 声明可并行 **且** 无外来活跃租约冲突。
- 派发生命周期走事务边界：`prepare_dispatch`（决策 + 获取租约 + 签发 dispatch permit）→ `commit_dispatch`（单元转 running）→ `finish_unit`（终态 + 释放租约）；崩溃窗口有确定性恢复对账（`reconcile`：按仓库证据分类，绝不盲目重放——仓库状态始终权威于运行时记录）。
- 路由矩阵一致性、executor 绑定与审查义务由运行时在 state 保存时机械校验（`validate_route_invariants`）——手写 state 漏写审查标志也无法绕过独立终审。

---

## 2. 运行时强制层（Mechanical Execution Assurance）

关键运行时契约由插件钩子（`hooks/`）与运行时模块（`runtime/`，纯标准库 python3）确定性执行——状态在文件里，证据绑定指纹，完成必须过门。

### 状态层

active task 在专属目录维护机器可读状态：

```
.glm-conductor/tasks/<task-id>/
├── checkpoint.md     # 导航状态（叙述性恢复依据，不是真相源）
├── state.json        # 强制状态源（goal、route 五字段、ownership、verification、review、status 等）
├── events.jsonl      # 执行溯源（append-only，一行一事件）
└── visual-evidence/
```

- **创建 state.json 即受完成门跟踪**（status 非终态即 active）；普通短任务不创建状态文件，零干预。
- 状态迁移按生命周期转换表逐次校验：进入 `finalizing` 即请求完成；`completed` 只能由完成门在全绿路径上原子提交——任何公共写入路径都无法直接把任务写成 completed。
- `.glm-conductor/` 是本地运行时账本，不算仓库改动（完成门对其豁免）；在 Git 仓库中优先写入本地排除文件 `.git/info/exclude`，绝不静默污染 `git diff`。

### Stop 完成门（四重检查）

活动任务在主会话 turn 结束时（Stop 钩子）按固定顺序接受四重检查，任一失败即 block（报文自带可行动的恢复指引）：

1. **ownership**：git 实际改动 ⊆ 声明的 `ownership.files`——越界改动不被阻止发生（子会话不触发钩子），但不可能静默通过完成门
2. **验证**：`verification.required` 全部完成，且证据指纹与当前指纹一致（否则 `verification_stale`）
3. **审查**：最新 review receipt 的 verdict = `ship` 且指纹新鲜（`review_missing` / `review_rejected` / `review_stale`）——**fresh ship review receipt 是审查证据的唯一权威**，state 手写字段不被采信
4. **视觉证据**：每张截图的字节 sha256 与记录一致（否则 `visual_stale`）

配套机制：

- **证据指纹（fingerprint）**：验证 / 审查证据经 `task_fingerprint`（基线修订 + 相关文件集归一化内容状态的 sha256）绑定到记录时的仓库状态；完成门用**同一入口**重算当前指纹并比对。任何记录之后的编辑都会使证据判 stale——「任何修复使先前验证 / 审查失效」由此自动强制。stale 的唯一恢复路径是重跑验证 / 重新审查并记录新指纹，禁止回写旧指纹。
- **durable receipts**：`verify-unit` / `verify-task` / `review-record`（runtime CLI）让 runtime 亲自执行验证命令、绑定同刻指纹落盘 receipt（runner = `glm-conductor-runtime`），把「跑过」从声明固化为机械事实；手工伪造的 receipt 文件不被完成门采信。
- **续行有界（gate_exhausted）**：连续 block 达上限后放行是循环安全机制，不是完成许可——任务不进入 completed，模型必须向用户报告 blocked 状态。

### 派发面强制

- **dispatch permit 门**：存在活动任务时，实施者类型子智能体（`flash-implementer` / `visual-implementer`）的派发必须携带有效 permit marker（`GLM_CONDUCTOR_DISPATCH=<permit_id>`）——无 marker / 伪造 / 过期 / 已消费（重放）一律 deny。permit 一次性，launch 成功后由钩子自动消费并记 `agent_launched`（runtime-observed 生命周期）。
- **Layer B 派发注入**：每次子代理派发前向主会话注入 ownership 契约提醒（提示级，提高合规但不构成强制；确定性强制只在完成门）。
- **Bash 策略门控**：表驱动规则把主会话命令分类 allow / ask / deny——破坏性命令（`rm` 带 r/f 标志、`git reset --hard`、`git clean -f`、force push）在活动任务期间恒拒；`git push` / 模式迁移 / 发布操作 / 权限变更在 assurance:high 时需向用户确认。

### 失败语义

分层设计：**派发面 fail-open（降级必须可见：stderr `ENFORCEMENT DEGRADED` + journal 记账）+ 完成面 fail-closed**——绕过派发门的任务最终无法合法 completed。状态损坏不会被解释成「没有任务」：任务发现按 active / terminal / orphaned / corrupt 四分类，高保障任务的 state 损坏会 fail-closed 拦截并给出重建指引（从 events.jsonl 与 checkpoint 证据重建，仓库状态权威）。

---

## 3. 额度感知连续性（Durable Quota-Aware Continuity）

连续性是与路由正交的生命周期维度，不是第五种 route。

### 生命周期模式与两层默认

| 模式 | 适用 | 行为 |
| --- | --- | --- |
| `foreground` | 当前会话内可完成的普通任务 | 交互默认，不创建任何 continuation |
| `resumable` | 可能跨会话 / 跨额度窗口中断的长任务 | 里程碑后写 checkpoint + 安排唤醒，唤醒后先检查再恢复 |
| `idle` | 非紧急、可无人值守、验证可自动完成 | 交给 ZCode 原生闲时任务 |

两层默认语义：`foreground` 是普通短任务（未创建 durable task）的交互默认；一旦创建 durable task，runtime 保守默认 `resumable + manual + max_quota_windows=0`——**可恢复，但没有自动跨窗授权**。`resumable` 不等于 automatic resume：自动续跑必须显式授权（见「授权续跑」）。

### 状态与恢复基元

- **CONTINUITY CHECKPOINT**：导航状态，不是仓库真相源——**repository > checkpoint**，冲突时以仓库为准，绝不为恢复 checkpoint 回滚仓库新改动。每次重新激活后执行固定恢复步骤（检查目标 → 读 checkpoint → 检查仓库状态与 diff → 判断先前变更与目标完成度 → 从 NEXT ACTION 恢复），恢复执行前必须重新输出 SELECTIVE ROUTE 声明。
- **SessionStart 恢复注入**：新会话自动发现未完成任务并注入恢复上下文——纯本地、零网络、零 quota 消耗；无论自动化配置如何，它始终是兜底恢复语义。

### 额度观察：两个四态

- **provider 四态**（额度状态）：`AVAILABLE / PRESSURE / EXHAUSTED / UNKNOWN`——经 provider 监控端点查询（凭证零落盘），解析层级为 新鲜缓存 → provider → 陈旧缓存 → UNKNOWN，**绝不默认 AVAILABLE、绝不重试网络**；凭证不可得时回退周期性存活探针。
- **execution phase 四态**（任务执行相位）：`NORMAL / PRESSURE / DRAINING / BLOCKED`——与 provider 状态是两个维度（最小编余 ≤ 20% 即 DRAINING，即使 provider 报 AVAILABLE）。DRAINING 禁开新实施波次，只许收尾白名单动作（join / verify / review / checkpoint / wake）；BLOCKED 连收尾一并冻结。查询：`quota-phase` / `quota-observe`。

### Quota Epoch 与订阅

- **epoch 身份** = 窗口多重集 `(kind, reset_at)` 的确定性指纹，`epoch_id = "glm:" + 前 16 位十六进制`——不含状态与百分比（provider 状态翻转是消费状态变化，不是新 epoch；窗口滚动 = reset_at 变化 = 新 epoch）。有意义的恢复触发是「新的可执行 epoch 出现」，不是定时器触发。
- **窗口物化**：下一个 reset_at 只在新窗口内发生真实模型调用时才物化——纯查询绝不推进它。
- **额度订阅（quota subscription）**：任务订阅 quota 而非拥有额度时钟；同一 epoch 的激活恰记一次，恢复门顺序冻结为 evaluate → 转态 → mark（同 epoch 重复唤醒零转态零重复激活）。
- **窗口预算消费**：唯一合法消费点 = resume commit point（转态落盘之后）；arm / fire / create automation 一律不消费。预算（`consumed_quota_windows ≥ max_quota_windows`）耗尽后任务转 `waiting_user`，此后不得再创建任何自动化唤醒。

### 授权续跑（authorized resume）

额度 EXHAUSTED 走确定性转态链（CLI `quota-exhausted`，执行态任务与单元转 `waiting_quota`），按 `execution_policy.continuity.auto_resume` 四态裁决：

| auto_resume | 授权要求 | EXHAUSTED 行为 |
| --- | --- | --- |
| `manual`（默认） | — | 不建自动化；SessionStart 兜底提示 |
| `notify` | — | 只产出提醒文本，不自动恢复 |
| `auto_once` | `authorization.source = user` | 一次性唤醒，烧穿 1 个窗口预算后停 |
| `until_done` | `authorization.source = user` | 逐窗续跑，直至完成或预算耗尽 |

恢复首步恒为 `quota-resume`（内部强制刷新额度；AVAILABLE / PRESSURE → 任务转回 executing、按账本就绪续作；EXHAUSTED / UNKNOWN → 零转态保守等待，不派发、不重建唤醒）。CLI 退出码 0 / 1 / 2 / 3——3 = durable-but-degraded（转态可能已落盘，幂等重跑安全）。

### 激活传输与观察面

- **Persistent Wake Bridge**：`wake-plan` 纯计算裁决 → 主会话执行宿主 CronCreate → `arm` 记账（runtime 自身绝不调用宿主 `Cron*`，宿主事实由会话侧供给，如 `wake-reconcile` 显式传入观测结论）。一任务 ↔ 一常驻桥。**`recurring_bridge` 是唯一 stable 传输**；其余传输词仅预留，arm 一律被拒绝。完成时桥接清理是会话侧单次尝试动作（绝不重试）。
- **Quota Watcher（可选，默认不运行）**：`quota-watcher start|status|stop|once`——本地 poll-only 常驻进程，零模型调用。v2.3 起它是纯观察/诊断加速面（**不**维护额度时钟，窗口延续职责已移交 Global Quota Clock）；不是正确性前提，不运行时刷新点查询照常工作。
- **Global Quota Clock（额度连续性主路径）**：每个 provider 身份一个持久时钟——周期做低成本模型调用以观察/物化下个额度窗口并向真实 reset 自校时（每小时 recurring 网格仅 watchdog 兜底）；建议放置在专用低成本 Flash 会话（插件只能建议与诊断，不验证会话专用性）。窗口物化的生产路径是 Scheduled Clock Tick，不是 primer。
- **Window Primer（默认关闭）**：新窗口需一次最小模型调用才物化；primer 是 runtime 唯一的 control-plane 模型调用面，三重授权闸 fail-closed（`primer_enabled` 默认 false + 自动续跑档位 + 用户授权来源）。授权闸之外绝不自行预调用——那是一次真实消耗额度的模型调用。
- **Stop 门 continuity health**：休眠交接域的任务（`waiting_quota` / `waiting_user`，或 PRESSURE / DRAINING 相位下的 resumable 自动续跑任务）在四重检查之前先核查三件套——handoff durable（checkpoint + resume manifest）、当期 epoch 订阅已注册、激活传输已 armed。

---

## 4. 稳定边界（Stable Boundaries）

以下契约冻结。除非出现需要窄兼容扩展的可证明正确性缺陷，发布不改变它们：

- **插件身份**：插件名与安装路径不变。
- **公开 CLI**：runtime CLI 子命令名（`python3 plugins/glm-conductor/runtime/cli.py <subcommand>`）与文档化退出码语义不变（通用 0 = 成功 / 1 = 运行期拒绝 / 2 = 用法错；`quota-resume` 另有 3 = durable-but-degraded）。
- **状态与日志可读性**：既有任务 state 文件、journal 与既有 quota 事件保持可读；旧记录绝不破坏性重写，迁移幂等，缺失新元数据时保守降级而非崩溃。
- **epoch_id 格式**：`glm:<16 位十六进制>` 不变；更高层的身份概念只以可选字段扩展，读者兼容新旧记录。
- **激活传输**：`recurring_bridge` 是唯一 stable transport；预留传输保持预留。
- **并发语义**：默认 worker 限额与并发语义不变（默认串行，有界并行硬上限 4）。
- **primer 默认关**：Window Primer 默认结构性关闭。
- **单一控制器**：主会话是唯一编排者；不引入多控制器、跨机分布式锁或第二套调度实现。
- **SessionStart 回退**：automation 不可用时恢复语义始终回退 SessionStart 注入——自动化永远是加速器，不是正确性前提。

同样固定的诚实边界（系统**不做**什么）：

- 原生插件级 quota API（`getQuotaRemaining` 等）不存在，绝不虚构；不硬编码 5 小时重置；额度观察只作为证据使用，不作为路由轴、不据此自动换模型。
- 子会话不触发钩子：写前拦截不可实现——越界改动不被阻止发生，但不可能静默通过完成门。
- 审查独立性来自全新上下文与只读隔离，不宣称跨模型独立。
- 连续性编排基于 ZCode 本地会话生命周期机制，不是独立的云调度器或后台守护进程（桌面客户端需保持运行、机器需保持唤醒）。

---

## 延伸阅读

- [architecture.md](architecture.md)——唯一架构真相源：运行时行为、状态模型、强制层与额度连续性的权威技术描述。
- 技能文档（运行面操作契约，含 `references/` 模板与判据）：`plugins/glm-conductor/skills/orchestration/SKILL.md`、`plugins/glm-conductor/skills/enforcement/SKILL.md`、`plugins/glm-conductor/skills/continuity/SKILL.md`。
- [仓库根 README](../README.md)——项目入口与安装使用。
- 文档索引：[docs/README.md](README.md)。
