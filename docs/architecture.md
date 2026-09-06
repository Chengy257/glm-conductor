# GLM Conductor 架构（权威文档）

> 本文档是 GLM Conductor 的**唯一架构真相源**，描述 v2.2 stable contract（v2-dev 开发线）的实际运行时行为。
> 契约细节以插件目录为准（`plugins/glm-conductor/` 下的 agents 与 skills）；本文档与其保持一致，冲突时以修复到一致为准，不得偏离开源文档单独演化。
> 历史提案存于 `docs/history/`，仅作参考，不构成当前实现依据。

## 1. 定位

```
GLM Conductor

Selective orchestration and durable quota-aware continuity for GLM coding agents in ZCode.
```

GLM Conductor 是 ZCode 插件，为 GLM 双模型体系（GLM-5.3 主会话 + GLM-5.3-Flash 子智能体）提供选择性编排：主会话任架构师并判断密集的工作，有界、规格完备的实施委派给 Flash 执行者，assurance:high 的交付物由全新上下文的只读审查者独立终审，长任务由与路由正交的连续性层安全续跑。v2 起，关键运行时契约（v1 为提示词契约）由**强制层**（插件钩子 + 运行时状态）确定性执行——"从提示词契约到强制执行契约"。

## 2. 架构总览

```
Delegability × Assurance
        ↓
solo / delegate / audit / full

Executor capability（实施者能力，独立维度）
        ↓
main / flash-implementer / visual-implementer

Reviewer capability（审查者，按任务模态选择）
        ↓
glm-reviewer（文本）/ visual-reviewer（视觉）

Continuity lifecycle（生命周期，与路由正交）
        ↓
foreground / resumable / idle

Enforcement（v2 强制层，确定性）
        ↓
Layer A 完成门（Stop 钩子）+ Layer B 派发注入（PreToolUse Agent|Task）
+ Bash 策略门控（PreToolUse Bash，allow/ask/deny）
+ 运行时状态层（state.json / events.jsonl）
```

五个维度相互独立：路由回答"谁实施、是否独立终审"；executor 回答"用哪种实施能力"；reviewer 回答"终审用哪种模态"；continuity 回答"任务很长时如何恢复"；enforcement 回答"哪些契约由运行时确定性保证而非依赖模型自觉"。不存在按风险单向递进的模型——路由由两个独立轴共同决定，且可基于新证据双向重估。

## 3. 角色

| 角色 | 模型 | 思考档位 | 工具 | 职责 |
| --- | --- | --- | --- | --- |
| 主会话（架构师） | GLM-5.3 | — | 全部 | 需求歧义解决、架构与路由判断、五段式规格编写、diff 检查与验证重跑、路由重估、验收 |
| flash-implementer | GLM-5.3-Flash | high | 读写全套 | 标准（非视觉）有界实施 |
| visual-implementer | GLM-5.3-Flash（多模态） | high | 读写全套 + 读图 | 视觉/交互有界实施，亲自读截图判定 |
| glm-reviewer | GLM-5.3 | max | 只读白名单 | 文本任务独立终审（`GLM REVIEW`） |
| visual-reviewer | GLM-5.3-Flash（多模态） | max | 只读白名单 + 读图 | 视觉任务独立视觉终审（`VISUAL REVIEW`） |

关键约束：

- 子智能体内不能再派生子智能体——结构天然扁平，主会话是唯一编排者
- 子智能体每次调用都是全新上下文——"新鲜审查者"语义天然成立，无需额外机制
- Browser Use 与 Computer Use 为 ZCode 主会话专用（策略层禁止子代理使用）——视觉证据由主会话采集、Flash 系角色读图判定
- GLM-5.3 是纯文本模型——主会话在视觉链路中只做驱动与采集，不得声称做了视觉判定

## 4. 双轴选择性路由

### 4.1 两个独立轴

- **轴 A — Delegability（可委派性）**：剩余实施是否足够有界、规格足够完备，可以委派？objective/文件边界/接口/约束/验证均明确且架构已定 → high；架构未定、root cause 未知、实质歧义、判断密集 → low
- **轴 B — Assurance（保障等级）**：主会话验证之后，一次全新上下文的独立终审是否有实质价值？影响面有限、回归风险可控 → standard；宽影响面、高回归风险、破坏性行为、大规模用户可见变更 → high

### 4.2 路由矩阵

| Delegability | Assurance | Route | 实施 | 独立审查 |
| --- | --- | --- | --- | --- |
| low | standard | solo | GLM-5.3 主会话 | 否 |
| high | standard | delegate | 实施者子智能体 | 否 |
| low | high | audit | GLM-5.3 主会话 | 是 |
| high | high | full | 实施者子智能体 | 是 |

### 4.3 SELECTIVE ROUTE 声明

主会话在任何 Agent 工具调用之前输出一次：

```
SELECTIVE ROUTE
mode: solo | delegate | audit | full
delegability: low | high
assurance: standard | high
executor: main | flash-implementer | visual-implementer
continuity: foreground | resumable | idle
reason: <简明的、基于证据的理由>
```

### 4.4 ROUTE REASSESSMENT（双向重估）

路由变化必须来自新观察到的证据，可双向重估，不得凭直觉或为省事变更：

```
ROUTE REASSESSMENT

delegability: <old> -> <new>
assurance: <old> -> <new>
mode: <old> -> <new>

evidence:
<新观察到的证据>
```

实施者返回的重估信号（ROUTE REASSESSMENT 请求）、审查者给出 rethink 或多项 fix-first，均构成有效重估证据。

## 5. 委派契约

委派（delegate/full）使用五段式实施规格：OBJECTIVE / FILES AND OWNERSHIP / INTERFACES / CONSTRAINTS / VERIFICATION（视觉任务追加 VISUAL ACCEPTANCE 节）。工作者返回 IMPLEMENTATION REPORT——报告只是声明，证据只存在于主会话亲自观察到的 diff 与命令输出中；无证据的完成声明无效。

主会话的验证义务不可由工作者报告替代：亲自检查完整 diff、重跑全部验证命令、将命令映射到实际输出。fix-first 之后旧裁决作废，复审必须换全新审查者。

## 6. 视觉执行拓扑

### 6.1 规范流程（跨多次调用）

```
visual-implementer 调用
        ↓
实施代码改动
        ↓
需要视觉证据 → 返回 VISUAL_CAPTURE_REQUEST，本次调用结束
        ↓
主会话采集截图落盘（Browser/Computer Use 为主会话专用）
        ↓
新的 visual-implementer 调用（携带完整状态）
        ↓
实施者亲自 Read 截图、对照 VISUAL ACCEPTANCE 判定
        ↓
通过 → FUNCTIONAL VERIFICATION → 报告
不符合 → 修正代码 → 再次返回 VISUAL_CAPTURE_REQUEST
```

**规范路径是"新的 visual-implementer 调用"**：每次视觉轮次自包含，由主会话携带完整状态发起——五段式规格（含 VISUAL ACCEPTANCE）、当前 diff / 改动状态、上一轮的 VISUAL_CAPTURE_REQUEST、截图文件路径清单、VISUAL_ROUND 轮次号。协议正确性**不依赖**子代理上下文保留。

子代理 resume 仅作为可选优化：仅当 ZCode 运行时实际暴露并验证了稳定的子代理恢复机制时，主会话可以 resume 原实施者；任何情况下正确性都不得以 resume 为前提。

约束：

- 每个验收点以 VISUAL_ROUND 跟踪（1 | 2 | 3），最多 3 轮采集-判定循环，超出即 blocked 交回主会话做 ROUTE REASSESSMENT
- 不存在子代理在单次调用内等待父会话采集的通道——实施者不得等待、不得空转、不得虚构截图结果
- 截图不可得（主会话无法采集）时 fail-closed：视觉通道停止，不得改为纯文本验证交付
- 视觉证据目录按任务隔离：`.glm-conductor/tasks/<task-id>/visual-evidence/`

### 6.2 视觉终审

assurance:high 的视觉任务在主会话验证之后，由全新上下文的 visual-reviewer 独立终审：亲自读取每一张截图，对照 VISUAL ACCEPTANCE 判定实施者视觉结论是否成立，检查用户可见回归，输出 `VISUAL REVIEW` 裁决（ship / fix-first / rethink，含 RESIDUAL VISUAL RISK）。代码 diff 对审查者是上下文，不替代主会话的代码审查。

## 7. 连续性生命周期

continuity 是与路由正交的生命周期维度，不是第五种 route。

| 模式 | 适用 | 行为 |
| --- | --- | --- |
| foreground（交互默认） | 当前会话内可完成 | 正常执行，不创建任何 continuation |
| resumable | 可能跨会话/跨可用性窗口中断的长任务 | 里程碑后写 checkpoint + 定时唤醒，唤醒后先检查再恢复 |
| idle | 非紧急、可无人值守 | 优先交给 ZCode 原生闲时任务 |

**两层默认语义（v2.2 冻结解释）**：foreground 是普通、未创建 durable task 的短任务的**产品/交互默认**（不创建 continuation state，零干预）；一旦创建 durable task / `execution_policy`，runtime 保守默认为 `continuity.mode=resumable` + `auto_resume=manual`（`max_quota_windows=0`）——**可恢复，但没有自动跨窗授权**。`resumable != automatic resume`：自动跨窗续跑（auto_once / until_done）须显式授权升档，不得理解为「安装后所有任务默认自动续跑」。

### 7.1 任务标识与状态布局

TASK_ID 是唯一的运行时任务标识，**生成即机械唯一**：

```
格式：<语义前缀>-<6~8 位随机十六进制后缀>
示例：redesign-settings-page-7f3a2c / parser-refactor-a92c1d
      （时间戳后缀可接受，随机后缀优先——并行同时建任务不碰撞）
```

v2 起 TASK_ID 取代 v1.x 的 CONTINUITY_ID（遗留 checkpoint 读取时归一化）。

生成契约：任务首次进入 resumable/idle 时生成一次；写入 checkpoint 持久化；恢复期间绝不重新生成；checkpoint 路径、视觉证据路径、定时 resume prompt、automation 关联、清理全部使用同一精确 ID。foreground 视觉任务使用同格式的临时 ID（TASK_ID 即通用运行时任务标识，不引入第二个标识概念）。

```
.glm-conductor/
└── tasks/
    └── <task-id>/
        ├── checkpoint.md        # 导航状态（叙述性恢复依据）
        ├── state.json           # 强制状态源（完成门按它校验）
        ├── events.jsonl         # 执行溯源（append-only）
        └── visual-evidence/
```

state.json 是 active task 的确定性状态（goal、route 五字段、ownership.files、verification、review、status 等；schema 与词汇表以插件 `runtime/state.py` 为准，legacy `CONTINUITY_ID` 读取时归一）。**创建 state.json 即受完成门跟踪**：status 非终态（completed / cancelled / failed）的任务在 Stop 完成门接受 ownership 校验；foreground 普通短任务不创建状态文件，零干预。events.jsonl 只追加（事件时点表见 continuity 技能），无秘密值、无完整 prompt。恢复与清理都按精确 TASK_ID 定位目录；不以"最新 checkpoint"作为查找策略——并行任务下"最新"是歧义的。并行任务互不覆盖、互不删除。

### 7.2 Checkpoint 与恢复

checkpoint 是导航状态，不是仓库真相源：不复制完整 diff、不声称未验证内容。repository > checkpoint——冲突时以仓库为准，禁止为恢复 checkpoint 回滚仓库新改动。

每次重新激活后执行八步恢复：检查目标 → 按 TASK_ID 读取 checkpoint → 检查仓库状态 → 检查当前 diff → 判断先前变更是否仍在 → 判断目标是否已完成 → 检查验证状态 → 从 NEXT ACTION 恢复。恢复执行前必须重新输出 SELECTIVE ROUTE 声明。

### 7.3 调度与边界

- 定时唤醒用安全周期性再激活（检查-恢复或等待），不 sleep 到固定时间；**调度触发本身就是存活探针**——v2.2 correctness path（§7.5）：provider quota truth 由 resolver / 可选 resident Watcher 观察维护；恢复资格由 Quota Epoch + 额度订阅（subscription eligibility）判定；同会话激活机会由 Persistent Recurring Bridge 提供并保持 armed。bridge fire 只是 probe / execution opportunity——fire ≠ 新 epoch、fire ≠ 额度消费；`reset_at` 绝不从旧 boundary 按固定 +5h 外推（锚定物化时刻，见 §7.5 实测口径）；`CronUpdate` / `CronDelete` 成功与否不属于 correctness 前提；宿主不可用导致 wake 漏发时 durable ledger 依然有效，由下次 SessionStart 兜底恢复
- 观察与调度分工（v2.2 冻结解释）：`observer.py` 保持纯决策层（零 I/O、零 daemon、零 timer、零网络）；可选 resident Watcher（§7.5 C3）是独立的进程级常驻 I/O 控制循环——poll-only、零模型调用，负责 provider quota truth 的持续观察，不负责 task dispatch、resume 授权、Scheduled Task 创建或 Session 注入
- 同会话投递：结果要回到当前会话，必须从当前聊天创建绑定本会话的定时续作
- 原生插件级 quota API 仍不存在（getQuotaRemaining 等均属虚构）、不硬编码 5 小时重置；额度查询走 provider-api 监控端点（§7 Quota-Aware Continuity），凭证不可得时回退周期性探针——额度观察只作为证据使用，不作为路由轴
- 完成清理只作用于本任务：删除 `.glm-conductor/tasks/<task-id>/` 单个目录、停止关联的定时任务、终止闲时排队，避免幽灵唤醒；`CronUpdate` / `CronDelete` 成功与否不属于 correctness 依赖——清理失败不破坏任务正确性，tombstone / no-task 状态下的后续激活是安全 no-op
- `.glm-conductor/` 是本地运行时状态：优先写入 `.git/info/exclude` 本地排除，不自动修改 tracked `.gitignore`

### 7.4 Quota-Aware Continuity（alpha3）

额度感知是连续性层的增强（`runtime/quota/`），**不是路由轴**——额度决定"何时能继续工作"，不决定"谁来做"，不据此自动换模型：

- **provider 抽象与解析**（§24-§27）：`provider.py` 定抽象边界（QuotaProvider / QuotaProviderError 五类错误），`parser.py` 把监控端点响应解析为标准化快照——按语义字段判窗（unit=3/number=5 → five_hour；unit=6/number=1 → weekly；与 type 无关），周窗可选（lite 套餐实测无周窗），容忍加性未知字段；`zai.py` / `bigmodel.py` 两个适配器走 `_http.py` 共享硬化层
- **安全条款**（§37 全部代码级落地并有测试锚定）：HTTPS only、严格 host allowlist（api.z.ai / open.bigmodel.cn，构造期拦截、绝不向清单外转发凭证）、短超时（5s）、限长响应（64KiB）、重定向禁用、原始响应不落盘、凭证零落盘（Authorization 头只存在于请求构造处，错误消息模板不含 key）
- **凭证链**（§36 provider-api 模式）：环境变量 `GLM_CONDUCTOR_QUOTA_API_KEY` 优先，已登录 ZCode 的 `~/.zcode/v2/config.json` provider 配置为文档化回退；两者皆不可得 → unavailable → 周期性探针回退
- **评估与规划**（§28-§33）：`scheduler.py` 纯函数四态评估（AVAILABLE / PRESSURE / EXHAUSTED / UNKNOWN，fail-open）与恢复规划——PRESSURE 在安全里程碑落 checkpoint；EXHAUSTED 取全部阻塞窗 max(reset)+grace 精确唤醒且唤醒后强制刷新；reset 未知或数据不可得 → 周期性回退，绝不虚构 reset 时间（v2.2 起 EXHAUSTED 唤醒的 correctness path 由 §7.5 承接：epoch / subscription 判恢复资格、recurring bridge 提供同会话激活机会；本条 alpha3 精确唤醒规划存活于 `quota-resume` 的 `scheduler.plan_resume`，不再作为唯一唤醒依赖）
- **诊断面**（§43）：`/glm-conductor:quota` 命令驱动 `report.py`（文本 + `--json` 双输出，zai→bigmodel 有界探测，退出码恒 0，零凭证输出）
- **查询时机**（§34）：只在刷新点查询（任务开始 / 路由选定后 / 大段派发前 / 里程碑后 / 调度恢复前后）+ 实例内短 TTL 缓存，不做常驻轮询（指会话内观察面；v2.2 起可选 resident Watcher（§7.5 C3）是独立的进程级常驻观察，零模型调用，与会话观察面互不替代）

### 7.5 Quota Control Plane / Activation Transport / Continuity Health（v2.2）

v2.2 把恢复链从 Agent 纪律升级为机械保证。计划：`docs/GLM-Conductor-v2.2-Quota-Continuity-Control-Loop-Implementation-Plan.md`（M 系列）+ `docs/GLM-Conductor-v2.2-Quota-Control-Plane-Architecture-Correction-and-Agent-Implementation-Plan.md`（C 系列，按其 C0 冲突裁决取代旧 M6-M10 规格）。窗口机制实测口径（2026-09-03 用户裁决，证据文档 `docs/GLM-Conductor-v2.2-Phase0-Primer-Experiments.md`）：**reset_at 时刻窗口恢复 100%（周窗优先）；下一个 reset_at 只在新窗口内发生模型调用时才物化——纯查询绝不推进它；reset_at 锚定物化时刻 +5h00m01s；会话空闲推迟窗口起点**。

- **双层额度模型（M1-M4）**：provider 四态（AVAILABLE / PRESSURE / EXHAUSTED / UNKNOWN，`scheduler.evaluate`）与 execution phase 四态（NORMAL / PRESSURE / DRAINING / BLOCKED）是两个维度（D7 冻结映射，`runtime/quota/control.py` 的 `evaluate_task_quota_phase` 冻结 8 键）——provider EXHAUSTED 或任一窗余 0 → BLOCKED；最小编余 ≤ draining_percent（默认 20）→ DRAINING（即使 provider 报 AVAILABLE，v2.1 24% 误判由此条封堵）；draining < 编余 ≤ pressure_percent（35）→ PRESSURE；provider UNKNOWN / 无可数值窗 → PRESSURE（fail-open，预算 1 不阻塞不虚构唤醒时刻）；其余 NORMAL。派发预算：NORMAL → max_workers 可开新波次；PRESSURE → 1；DRAINING → 0 且禁新波次仅许收尾白名单（join/verify/review/checkpoint/wake 等）；BLOCKED → 0 且新波次与收尾一并冻结。`runtime/quota/observer.py` 叠加观测层（纯决策零 I/O 零 daemon）：自适应间隔表（NORMAL 1800s / PRESSURE 600s / DRAINING 300s / BLOCKED 300s，下限 60s）+ lazy heartbeat `should_refresh` + §16.1 八键观测 dict；M4 在 prepare 层硬拒绝 DRAINING 的新单元/新 wave 并按 execution phase 折算 worker cap
- **Quota Epoch（C2，`runtime/quota/epoch.py`）**：有意义的事件不再是 "automation fired"，而是「新的可执行 quota epoch 出现」——定时器触发只是观察机会，本身不构成新 epoch。epoch 身份 = 排序后 `(kind, reset_at|"unknown")` 窗口多重集的 sha256 确定性指纹，**不含 status / 百分比**（provider 状态翻转是消费状态变化，不是新 epoch；窗口滚动 = reset_at 变化 = 新 epoch），`epoch_id = "glm:" + 指纹前 16 位`。双 boundary 冻结分离：probe boundary = min(全部可解析 reset_at)+grace（只服务观察收紧，Resume Controller 绝不当 executable 消费）；executable boundary = max(阻塞 EXHAUSTED 窗可解析 reset_at)+grace（§30 最晚多窗语义）；无阻塞窗或 reset 全不可解析 → None（§31 绝不虚构时刻）
- **Real-Time Quota Watcher（C3，`runtime/quota/watcher.py` + `watcher_store.py`）**：独立本地进程循环（poll-only、零模型调用、可注入时钟/fetch/sleep），证明 §6.1 命题「Session dormant 时运行时可独立维护真实 provider 额度时钟」。单实例锁即状态文件本身（`.glm-conductor/quota/watcher.json`：存活 pid + 新鲜 heartbeat → 拒绝；死/过期 → 接管 generation+1）；ACTIVE/PASSIVE 每 tick 重判（连续激活是需求耦合不是永久状态）：waiting_quota 或 executing 族 + auto_resume ∈ {auto_once, until_done} → ACTIVE，manual/notify 恒 PASSIVE（可观察但不 prime、不发激活）；休眠按 60s 步长分片（< 180s heartbeat 新鲜阈值），锁绝不在长间隔睡眠中被误接管；抓取异常容错不崩（只取类型名进 `last_observation.error`）。CLI：`quota-watcher start|status|stop|once`
- **Window Primer（C4，`runtime/quota/primer.py`）**：runtime 唯一的 control-plane 模型调用面——用一次最小、可审计的模型调用使新窗口物化，随后强制 quota refresh 二次确认。三重授权闸机械 fail-closed：`primer_enabled == true`（**默认恒 false**，词汇落点 `execution_policy.quota_control.primer_enabled`）AND `auto_resume ∈ {auto_once, until_done}` AND `authorization.source == "user"`——manual/notify 永不 prime；单飞幂等键 `(provider_identity_hash, boundary_id)`，**失败尝试同样落账**（超时后 provider 侧可能已物化，重发即同旧 boundary 连发多个 prime）；物化确认红线：唯一证据 = prime 前后两次强制 refresh 的窗口身份变化（`epoch.same_epoch` 判定）——HTTP 200 不是证据、百分比下降不是证据（整百分点粒度对小调用不可见）；授权方向绝不 fail-open（误拒只损失一次物化时机，误放行是无授权模型调用）
- **Quota Subscription 与激活记账（C5a/C5b）**：任务订阅 quota 而不拥有额度时钟——state 顶层 `quota_subscription` 块记录注册 epoch（`epoch_id` 字符串等值比较）；三 API：`register_quota_subscription`（幂等；`handle_quota_exhausted` 转态成功后 best-effort 自动注册，失败不阻断转态）、`evaluate_subscription_eligibility`（纯判定零写零事件）、`mark_activation_epoch`（**QC-07 每 epoch 恰一次**：同 epoch 重标零写零事件；控制面 `quota_epoch_advanced` 事件经 `journal.append_control_plane_event` 落 `.glm-conductor/quota/events.jsonl`）。恢复门顺序冻结 **evaluate → 转态 → mark**：先 mark 后转态的崩溃窗口会把同 epoch 下次恢复卡死（last_activation 已等于当前 epoch、资格门恒判同 epoch）；后转态顺序至多损失一次记账（QC-07 方向：宁可少记账不卡死恢复）。未注册/未启用任务输出零变化（legacy 契约）
- **§15.1 commit-point 消费（C1b/C7）**：窗口预算消费的唯一合法位置 = resume commit point（转态 durable + mark 之后的提交点），入口 `record_quota_boundary_consumed`（幂等 key = `task_id + epoch_id`；授权预闸三查镜像 `auto_resume ∈ {auto_once, until_done}` AND `source == "user"` AND `remaining > 0`——manual/notify 永不消费；boundary 证据派生 = 当前 epoch 快照最早可解析窗口的 D10 形态 `"kind:reset_at"`（RH-04 起该辅助证据在事件中记作 `representative_boundary_id`——epoch 中用于人类审计的代表窗口身份，并非 C2 executable boundary；RH-04 前旧事件为 legacy 键 `executable_boundary_id`，读侧双键兼容），无可解析窗跳过消费不虚构）。事务序冻结：授权预闸 → boundary 派生 → `migrate_quota_window_accounting`（幂等 one-shot 先行；双键去重使 legacy boundary 证据与新 epoch 证据不双计）→ record（三查竞态 TaskManagerError → face 降级不抛——转态已 durable 不回滚；OSError 自然上抛——QC-07 证据丢失必须可见）。CLI `quota-resume` 退出码 0/1/2/3：**3 = durable-but-degraded**（转态可能已落盘、幂等重跑安全），区别于 1 拒绝 / 2 参数错误
- **Quota 消费/迁移崩溃一致性（RH-03，write-ahead pending marker）**：`record_quota_boundary_consumed` 与 `migrate_quota_window_accounting` 在任何可变投影（state save）之前先向任务 journal 追加 pending 事件冻结证据——`quota_consumption_pending` {epoch_id, representative_boundary_id, target_consumed, resume_started_at} 与 `quota_accounting_migration_pending` {legacy_consumed, verified_boundary_ids, attributed_consumed, legacy_unattributed_consumed, new_consumed}（epoch 身份先于投影存在；RH-04 前旧 pending 为 legacy 键 `executable_boundary_id`，读侧双键兼容）。重入事务序：先扫 committed（同 epoch 已消费 → 幂等原样返回，语义不变），再扫 pending 做恢复闭合（reconcile，**先于授权三查**——pending 是上一进程已过授权的持久证据，闭合不重查预算，否则 state 已投影 + 预算恰好耗尽时永远无法闭合）：state 投影已达冻结 target → 只补 committed/marker 事件；差一窗未落 → 补一次投影再落事件；state 与 pending 冻结值矛盾（领先/差距>1/多条 pending 不一致/形状损坏）→ TaskManagerError 零写、pending 保留待人工裁决。迁移闭合按 pending 冻结五数直接补 marker，绝不从已更新 state 重算 legacy（legacy_consumed/归属拆分不失真）。两 API 公开签名与返回键零变化，正常路径外部可观测行为逐键不变
- **消费证据字段语义清理（RH-04，representative_boundary_id 双键兼容）**：消费事件辅助证据字段按方案 A 改名 `executable_boundary_id` → `representative_boundary_id`（语义对齐：该值由 `_consumption_boundary_id` 派生 = epoch 快照最早可解析窗口的 D10 形态 `"kind:reset_at"`，是「epoch 中用于人类审计的代表窗口身份」，并非 C2 executable boundary（max(阻塞 EXHAUSTED reset)+grace）——旧名与来源不符，改名不改派生数学；幂等/归属权威键仍是 `task_id + epoch_id`）。新 pending / committed 事件只写新键；RH-04 前旧事件（legacy 键）**不重写**，全部读者双键兼容（先 new 后 legacy）：record 幂等命中与 pending 恢复闭合读冻结值双键兼容（闭合补的 committed 以新键落盘）；`migrate_quota_window_accounting` 的身份键去重把 `representative_boundary_id` 与 legacy `executable_boundary_id` 同 `epoch_id` / `boundary_id` 一并登记 seen（新旧键名混用的同窗证据不双计，distinct 窗口照常各计一次）；`record_quota_boundary_consumed` 的 kwargs 参数名与返回键名 `executable_boundary_id` 冻结不变（调用面最小扰动，键值承载代表窗口身份）
- **Persistent Wake Bridge 与 Activation Transport（M5 + C6，`runtime/activation_transport.py`）**：`plan_wake_bridge`（CLI `wake-plan`，纯计算零 automation 创建，只读本地 quota-cache 绝不发网络）裁决 → 主会话宿主 CronCreate（**宿主动作，runtime 绝不调用宿主 `Cron*`——宿主事实由会话侧供给**，如 `wake-reconcile <repo> <task> <host_status>` 显式传入 CronList 观测结论）→ `arm_wake_bridge` 记账即止。**arm/fire/create 一律不消费窗口预算**（automation lifecycle ≠ quota epoch consumption）；`wake-record` / `record_quota_wake` 是 v2.1 legacy 兼容入口（deprecated），persistent path 禁止调用。传输四词汇冻结：`recurring_bridge` 唯一 STABLE（一任务 ↔ 一常驻循环 automation）；`probe_then_hold` / `self_retiming` / `session_injector` 预留——`arm_transport` 对实验 kind 一律 `TransportReservedError`（预留即预留，绝不半实现）。`activation_transport_status`（CLI `transport-status`）输出冻结 12 键事实面（transport / stable / armed / bridge_status / reasons 等），是 C8 Stop 门 armed 检查的接口面；armed 只认显式 arm 路径落下的记账（对账降级 cancelled/stale 即 missing），观测面绝不制造 armed（§22.5）。`runtime/scheduler_facts.py` 以 session_id 缓存宿主 scheduler 能力已证事实（`.glm-conductor/scheduler/session_facts.json`）：证据只进不退——四能力 allowed/forbidden 落定即冻结、origin 只允许 unknown → interactive / scheduled_task 单向升级（D15-e：被 Scheduled Task 触发过的会话禁止再创建 automation），单探针纪律的数据基础。完成时桥接清理是**会话侧单次尝试**动作（pause/delete 绝不重试，tombstone 快速 no-op）
- **Stop 门第 0 位 continuity health（C8/C8a，`hooks/stop_gate.py`）**：四重检查之前对「触发域内」任务做连续性健康检查——触发域冻结（域外零介入，Stop 每回合都触发、trio 是 dormant 交接要求不是日常要求）：任务活动 且（status ∈ {waiting_quota, waiting_user} 或 quota.execution_phase ∈ {PRESSURE, DRAINING}）且 route.continuity == "resumable" 且 auto_resume ∈ {auto_once, until_done}。域内按依赖序三检查（trio）：handoff durable（checkpoint.md + resume manifest 存在）→ quota subscription（enabled 且已注册当期 epoch）→ transport armed（经 `activation_transport_status`）；未 armed 且 create==forbidden 或 origin==scheduled_task → **降级放行不无限拦截**（INV-22-PB-07），双未知 → block + 复用 gate_exhausted 释放阀防死锁；watcher 缺席/过期只是降级注记，绝不独立 block；obligation==degraded 短路放行。C8a 修复：reason 以 `continuity_` 为前缀的 gate_degraded 是门侧注记（非模型工作），跳过不破 gate_exhausted trailing 链——否则持续降级任务的计数永不达上限、释放阀永不触发（无限 block 取代 3-Stop 耗尽语义）。本检查纯读零写零网络零模型调用
- **runtime CLI 额度/连续性面**：`quota-resolve` / `quota-observe` / `quota-phase` / `quota-exhausted` / `quota-resume`（退出码 0/1/2/3）/ `wake-record`（legacy）/ `wake-prompt` / `wake-plan` / `wake-status` / `wake-reconcile` / `transport-status` / `quota-watcher start|status|stop|once`

### 7.6 运行时写者持久化分类（v2.2.1 WU-221-A3）

v2.2 起多个合法进程（前台主会话 + 常驻 quota watcher 等）写共享 JSON 状态——既有写方各自使用固定 `<path>.tmp` 临时名，两个进程会在同一临时路径相撞。v2.2.1 hardening 按共享原语层（WU-221-A1 交付 `runtime/durable_io.py`：唯一同目录临时名 + `os.replace` 原子写 + PermissionError 有界重试；WU-221-A2 完成多进程 capable 写方的迁移）对「写者 × 目标文件」逐项分类如下，杜绝相撞面；回归由 `tests/test_durable_io.py::FixedTmpBanTest` 机械锚定（多进程 capable 写者中不得再出现固定 `.tmp` 写法）：

| 写者模块 | 目标文件 | 分类 | 机制 |
| --- | --- | --- | --- |
| `runtime/quota/resolver.py` | `.glm-conductor/quota-cache.json` | multi-replace（主会话 + watcher 均可 resolve） | `durable_io.atomic_write_json`（唯一临时名） |
| `runtime/quota/watcher_store.py` | `.glm-conductor/quota/watcher.json` | multi-replace（watcher 进程 + 主会话 stop 路径） | `durable_io.atomic_write_json` |
| `runtime/scheduler_facts.py` | `.glm-conductor/scheduler/session_facts.json` | multi-replace（多宿主会话 hook/观察路径可并发） | `durable_io.atomic_write_json` |
| `runtime/quota/primer.py` | `.glm-conductor/quota/primer.json` | single-writer（primer 仅主会话运行，watcher 永不 prime；按 WU-221-A1 计划枚举统一迁移） | `durable_io.atomic_write_json` |
| `runtime/state.py` | `tasks/*/state.json` | single-writer（仅主会话/会话内 hook 写） | 固定 `.tmp` 保留（记录在案） |
| `runtime/dispatch_wave.py` | permits | single-writer（主会话派发事务） | 固定 `.tmp` 保留 |
| `runtime/lease.py` | leases | single-writer（主会话） | 固定 `.tmp` 保留 |
| `runtime/provenance.py` | receipts | single-writer（runtime CLI 由主会话调用） | 固定 `.tmp` 保留 |
| `runtime/resume_manifest.py` | `tasks/*/manifest.json` | single-writer（主会话事务刷新） | 固定 `.tmp` 保留 |
| `runtime/journal.py`（及 task_manager/control 的 append 路径） | `events.jsonl`（任务级 + 控制面） | append-only | 追加语义，非本原语迁移面（A1 明示） |
| stop 标志 / 心跳 / scheduler facts merge 的 RMW 路径 | watcher.json/stop 标志 + session_facts.json | multi-RMW | `durable_io.atomic_update_json`（v221-a4 已落地：`<目标>.lock` 独占锁下的读-改-写，临界区只含读+合并+写；RMW 锁毫秒级短临界区，与 watcher.lock 所有权域长期持有相互独立、互不替代） |

reviewer 契约（WU-221-A1）：`exclusive_lock` 临界区必须保持短（毫秒级 JSON RMW；不得在锁内做长计算/网络调用），否则超过 `stale_seconds`（300 秒）会被合法接管。

## 8. 任务与工作单元管理（v2 beta2）

Work Unit 是可独立派发的最小有界实施单元（§60-§73，`runtime/work_unit.py` / `dependency.py` / `dispatcher.py` / `reconcile.py`）——不是工作流 DSL：

- **状态模型**：十词状态词汇 + 26 条合法转换边（主链 pending→…→completed；quota/block 回退边；§69 恢复对账边；终态封锁）；单元必填 ownership 与 verification（无范围或无验证不可派发）；`attempt` 记录重试史，新调用不抹除失败史（§73 有界重试）
- **依赖图**：`depends_on` 同任务内引用、环拒绝（报全部环成员）；就绪 = status ready 且依赖全部 completed；确定性拓扑序驱动派发顺序
- **派发准入**（主会话仍是唯一编排者）：`plan_dispatch` 五道闸——quota 四态（EXHAUSTED→waiting_quota、UNKNOWN/PRESSURE 保守抑制，§67）→ ownership 不相交（保守近似：字面前缀相交即冲突，宁少并行不越界，§66）→ **租约闸（`runtime/lease.py`：任务专属 leases.json owner map、派发前全有或全无获取、同 owner 幂等、异 owner 冲突拒绝、写相结束释放，§78-§79）→ max_workers 预算（1-4，§82）→ 派发；默认串行，有界并行（上限 4，experimental）已启用——并行资格 = ownership 声明判定可并行 **且** 无外来活跃租约冲突（两道闸门均须通过，§66/§78；租约不豁免 ownership 闸）
- **事务边界（`runtime/task_manager.py`，v2.0.1 H4）**：派发生命周期的手工拼接（plan→租约→状态转换→`dispatch.active` 记账→save→事件）固化为四个高层 API——`prepare_dispatch`（决策 + 获取租约 + `dispatch_prepared` 事件；不动单元状态、不落盘，决策未批准时零副作用）→ `commit_dispatch`（租约在位校验 → ready→running → active 记账（幂等防御）→ 单次 save → `implementation_started`）→ `finish_unit`（终态转换——running+completed 双跳经 verifying 编码 §70 父验证语义——→ 释放租约 → active 移除 → 单次 save → `unit_finished`）；`abort_dispatch` 回退未提交的准备（running 已提交不得静默回退）。崩溃窗口映射（确定性恢复路径）：prepare 后中断 → 单元仍 ready + 租约在位（同 owner 租约不挡后续 plan）→ 可安全 commit 或 abort；commit 后中断 → running 且无验证证据 → 由既有 reconcile 按 §69 证据三分恢复。分层纪律：dispatcher 保持纯决策器（零 I/O），task_manager 是唯一做派发 I/O 编排的事务层
- **单元完成证据门（v2.0.1 release hardening RB-1）**：`finish_unit(outcome="completed")` 在任何状态转换**之前**先过 `reconcile.fresh_unit_verification` 共享证据谓词（与恢复对账同一口径，all-match）——`verification` 内每个 required command 各需至少一条五条件全满足的事件：`event == "verification"`、`unit` 与单元 id 逐字精确、`fingerprint` 等于当前指纹、`status == "pass"`、`command` 等于该命令；missing / stale / partial / wrong-unit / 无 `unit` 字段的 legacy 证据一律拒绝，且拒绝**零副作用**（不写任何 journal 事件、不落盘）；检查过程中 git / 指纹读取失败 fail-closed（`TaskManagerError`——新鲜度无法判定即拒绝完成）；`failed` / `cancelled` 出口不需要证据；空 required 走零 git 快路径（生产单元经 state 校验必带 ≥1 命令，该形状仅为谓词直调方的防御语义）。谓词侧形状防御：id 非非空字符串的单元任何事件都不匹配（`None` 不构成 legacy 无 unit 事件的匹配通道）。恢复对账（上条）同步收紧 all-match——多 command 单元部分证据不再建议 `completed`（建议 `verifying`）；证据写入口为 `task_manager.record_unit_verification`（单元级），与任务级 `state.record_verification` 分工
- **租约崩溃恢复（v2.0.1 H5，P1-4/P1-5）**：lease record 扩展 session_id / generation / heartbeat_at / expires_at——`prepare_dispatch` 按 `runtime/lease.py` 的保守默认 TTL（`LEASE_DEFAULT_TTL_SECONDS = 1800` 秒：覆盖单个 work unit 一次有界实施的正常写相并留足余量；`ttl_seconds=None` = 永久）落盘 expires_at，长期实施由 `renew_lease` 心跳续约，同 owner 重取已过期记录 generation + 1；2.0.0 旧格式记录（无 expires_at）永不过期、可读可续，靠 stale 判定兜底。恢复对账闭环：`reconcile.reconcile_leases` 纯建议三分——stale（owner 不是图中 running/verifying 单元：单元已完成/失败或图中无此单元）、active（活跃写相且未过期）、expired_running（活跃写相但已过期——worker 可能仍在写，**不建议自动释放**，归主会话裁决）；`task_manager.recover_leases` 是清理入口：仅释放 stale 组 + journal `lease_recovered` 事件（零释放不落事件），expired_running 仅上报。崩溃后无需人工删除 leases.json
- **恢复对账**（§68-§69）：恢复绝不盲目重放——completed 不重跑；`running` 单元按证据三分为 ready（无残留）/ completed（残留 + 绑定当前指纹的新鲜验证事件）/ verifying（残留无新鲜证据，主会话必须亲自验证）；`verifying` 单元仅在存在新鲜验证证据时建议 completed，其余情形出 advisory（保持现状由主会话直接验证 / 无残留由主会话裁决），不做状态转换；仓库状态权威于运行时记录。**验证证据归属绑定（v2.0.1 H6）**：journal verification 事件显式携带 `unit`（work unit id）字段，reconcile 按单元逐字精确匹配——相同 command / 重叠 ownership 的单元之间不存在错误复用证据的空间；单元级证据经 `task_manager.record_unit_verification` 写入，无 `unit` 字段的旧格式事件不再匹配（保守按无证据处理，主会话重新验证）
- **Join**（§71-§72）：全部单元 completed 后主会话显式 join——聚合 diff → 任务级全局验证（跨单元集成/构建/lint，局部验证永不自动替代）→ 终指纹 → 审查 → 完成门

## 9. 强制层（alpha2 起，四重检查）

v2 把关键运行时契约从提示词升级为确定性强制。强制层由插件钩子（`hooks/hooks.json` 声明，安装后自动启用、仅新会话生效）与运行时模块（`runtime/`，纯标准库 python3）组成。

### 9.1 Stop 完成门 — 四重检查（Layer A，确定性）

主会话 turn 结束时（Stop 钩子 `hooks/stop_gate.py`），对每个**参与任务**（ownership 声明非空 / verification.required 非空 / review.required 为 true / visual_evidence 非空，四者任一）按固定顺序校验：

```
1. ownership：git 工作区改动文件（touched） ⊆ 声明 ownership？
   否 → block（ownership）：报文列出 out-of-scope 路径 + 两条出路
2. 验证：required 命令全部 completed？
   否 → block（verification_missing）：报文列出缺失命令
        且 verification.fingerprint = 当前指纹？
   否 → block（verification_stale）
3. 审查（review.required=true 时）：verdict = ship？
   否 → block（review_missing / review_rejected）
        且 review.fingerprint = 当前指纹？
   否 → block（review_stale）
4. 视觉证据：visual_evidence 每项文件字节 sha256 与记录一致？
   否 → block（visual_stale）
全部通过 → 静默放行（journal 记 gate_passed）
```

- ownership 声明三种形式：精确文件、目录前缀（`src/auth` 等价 `src/auth/**`，按路径段匹配）、glob（`**` 跨段 / `*` 与 `?` 不跨段）；拒绝隐式扩张（`src/auth` 不覆盖 `src/authentication.ts`）
- **证据指纹**（`runtime/fingerprint.py`）：`task_fingerprint` = sha256(基线修订 + 相关文件集归一化内容状态)（CRLF/LF 归一、路径归一）；范围 = 声明 ownership 时的「改动 ∩ 声明」，未声明时 = 全部改动。主会话记录证据（`record_verification` / `record_review`，时机契约见 continuity 技能）与完成门比对用**同一入口**——任何记录后的文件编辑都使指纹不一致，证据判 stale，完成被拦（「任何修复使先前验证/审查失效」的自动强制）
- `.glm-conductor/` 运行时目录豁免——编排器自身账本不算用户仓库改动（否则创建 state.json 即自指拦截）
- **per-task 仓库求值（RB-2）**：账本根（`ZCODE_PROJECT_DIR` / cwd，state.json / events.jsonl / lease 所在地）不再同时充当 git 根——每个参与任务按 state 顶层可选 `repository.root` 解析其专属仓库根（`bind_repository_root` / `bound_repository_root` / `resolve_repository_root`；无绑定 = legacy，回退账本根，单仓行为不变），ownership / 指纹 / 视觉检查全部在该根上求值；同一仓库根的 touched + base 单次 Stop 内至多取一次（per-repo 快照缓存：每根 1 次 `status` + 1 次 `rev-parse`，同根多任务不重复 git）。workspace 根无需是 Git 仓库——任务绑定嵌套仓即可受门强制；多嵌套仓歧义不猜：报出候选目录并要求显式绑定，绝不自动猜根。账本恒在账本根，绝不换根
- **降级按任务隔离（RB-2）**：仓库解析 / git 失败只降级该任务（stderr 报警 + 该任务 journal 记 `gate_degraded`），其余任务照常求值——旧实现的全局 git 早退拆除。降级 reason 结构化词汇（`gate_degraded` 事件的 `reason` 字段）：`repository_unavailable`（任务绑定的 repository.root 上 git 操作失败）、`git_unavailable`（legacy 任务且账本根本身是 git 根、git 瞬时故障）、`repository_root_missing`（legacy 任务、账本根非 git 根且一级子目录无任何 `.git` 候选）、`repository_ambiguous`（legacy 任务、账本根非 git 根但存在候选——即使只有一个也不自动绑定）、`evaluation_error`（求值期结构性错误：指纹 rev-parse 失败 / ownership 声明模式非法等），另有发现完整性路径的 `orphaned_task` / `corrupt_state`。被降级任务不参与本轮 `gate_passed` 记账，其指纹取不到 → 天然保持 `finalizing`（`completed` 只能来自门内全绿提交）；corrupt 高保障任务的 fail-closed 证据只依赖 journal，不被任何任务的仓库故障牵连
- 子代理工具调用不触发钩子（Phase 0 实证：子会话不携带 hook runner），故写前拦截不可实现——**越界改动不被阻止发生，但不可能静默通过完成门**

### 9.1.1 完成生命周期（finalizing → [完成门] → completed）

状态词汇含完成请求态 `finalizing`（介于 reviewing 与 completed 之间，非终态）；`runtime/state.py` 的顶层转换表 `TASK_TRANSITIONS` 约束每一次 status 迁移——终态无表项（不接受任何转换），`completed` 的唯一入边是 `finalizing → completed`，且只对完成门内部通道（`state.commit_completion`，经 `save_state` 的 `_gate_commit` 私有参数）放行，`save_state` / `transition_task_status` 等公共写入路径一律拒绝。收尾流：模型把 status 推进为 `finalizing`（= 请求完成，`transition_task_status` 自动记 `status_changed` 事件）→ Stop 完成门四重检查：有违规照常 block（状态保持 finalizing，修复后重新 Stop）；全部通过时钩子在放行路径对**全部** `finalizing` 任务（不限于参与校验集合）原子提交 `completed` 并记 `completed` 事件（via=completion_gate，绑定当前证据指纹）——gate_exhausted 放行与任何降级路径都不提交。由此 `completed` 成为完成门的唯一提交点，主会话无法在完成门前经公共状态 API 把任务写成 completed 绕过四重检查（P0-1 修复）。

### 9.1.2 路由不变量（P0-2）

`route.assurance` 与 `review.required` 是两个独立字段——只靠「模型记得写对」不构成强制。v2.0.1 起 `runtime/state.py` 的 `validate_route_invariants` 在 `validate_state` 既有枚举校验之后追加四条跨字段规则，非法组合在 `save_state` 即被拒（错误消息中文、含字段路径；规则仅在涉及字段为合法枚举值时生效，route 非 dict 或 mode 非法交由基线枚举错误处理）：

```
R1 矩阵一致性：mode 必须等于路由矩阵 [delegability][assurance]
   （low/standard→solo，high/standard→delegate，low/high→audit，high/high→full）
R2 executor 绑定：solo/audit ↔ executor=main；
   delegate/full ↔ executor ∈ (flash-implementer, visual-implementer)
R3 review 绑定：mode ∈ (audit, full) 或 assurance=high（derive_review_required
   推导为 True）时 review.required 必须为 true（review 块缺失按非 true 处理）
R4 delegate/full 实质性：ownership.files 与 verification.required 必须非空数组
```

配套地，`new_task_state` 的 `review_required` 缺省值改为按 route 推导（audit/full 或 assurance:high → True；solo/delegate + standard → False；信息不足落 False；显式 True/False 照传——显式 False + 推导 True 的组合由 R3 在保存时拒绝）。门侧同步：`hooks/stop_gate.py` 的参与判定与检查 3 的条件从「只认 `review.required` 标志」改为「标志为 True 或 route 推导要求审查」——即使手写 state.json 绕过 `save_state` 校验漏写标志，high-assurance 任务也无法跳过独立 fresh ship 裁决的检查（不新增 check 词汇，仍按 review_missing / review_stale 拦截）。

### 9.1.3 任务发现完整性（P0-3）

完成门的强制对象先于四重检查——发现阶段若把「读不出的任务」当成「不存在的任务」，损坏即成为静默放行通道。v2.0.1 起 Stop 钩子改用 `runtime/state.py` 的 `discover_tasks` 对 `tasks_root` 下全部子目录做四分类（`find_active_tasks` 保留为兼容 helper，实现复用同一发现，输出不变）：

| 分类 | 判定 | Stop 完成门处置 |
| --- | --- | --- |
| active | state.json 可读且 status 非终态 | 照常参与四重检查 |
| terminal | status ∈ (completed, cancelled, failed) | 与完成门无关 |
| orphaned | 目录存在但无 state.json | 永不拦截：stderr 报警 + 该目录 journal 记 `gate_degraded`（reason=orphaned_task） |
| corrupt | state.json 存在但 JSON 损坏 / 任务标识归一失败（ValueError）或读取 OSError | 按 journal 证据二分（见下），不中断其余任务的扫描 |

**corrupt 的 fail-closed 证据规则**：读该任务目录 journal（容错读，坏行跳过），任一 `route_selected` 事件 mode ∈ (audit, full) 或 assurance=high，或任一 `status_changed` 事件 to=finalizing → 判定该任务此前处于需要完成门强制的高保障 / 完成请求路径，状态损坏不得成为静默放行通道——并入违规清单（check=`corrupt_state`，为第八种 check）走统一 block / gate_exhausted 机器，报文含可行动恢复指引（从 events.jsonl / checkpoint 证据重建 state.json——仓库状态权威；或经用户确认后归档任务目录）。无此证据 → 降级放行：stderr 报警 + journal 记 `gate_degraded`（reason=corrupt_state）。corrupt 的证据判定只依赖 journal，不依赖 git——active 为空时 git touched 清单不再被取；RB-2 起仓库求值按任务隔离（见 §9.1 per-task 仓库求值），corrupt 的 fail-closed 判定恒读 journal，不再被其他任务的仓库故障降级早退牵连。由此状态损坏被结构化记录并可区分：no task（静默零干预）≠ unreadable task（报警，高保障时拦截）。

### 9.2 PreToolUse 双面：Layer B 注入（提示级）+ Bash 策略门控（决策级，beta1）

同一钩子脚本 `hooks/pre_tool_use.py` 按载荷 tool_name 分流：

- **Layer B（matcher `Agent|Task`，advisory）**：每次子代理派发前注入 ownership 契约提醒（声明清单 + 越界将拦完成门）。提高合规但不构成强制。
- **Bash 策略门控（matcher `Bash`，决策级）**：`runtime/policy.py` 表驱动规则（§57-§59，禁 DSL）把主会话 Bash 命令分类为 allow/ask/deny，经 `permissionDecision` 返回运行时——deny：rm -r/-f、git reset --hard、git clean -f、force push（--force-with-lease 归 ask）；ask（仅活动任务 assurance:high 时）：任何 push、模式迁移、发布操作、权限变更。门控顺序：非 Bash 不管 → 无活动任务零干预 → deny 无视保障级 → ask 仅 high → 其余默认放行。只覆盖主会话调用（子代理工具调用不触发钩子，角色级 deny 由 agent 工具白名单负责）；字符串中引用的破坏性文本与 `git rm -r --cached`（仅动索引）会被保守误拒（beta1 已登记取舍：误拒方向保守安全，特判排除违反 §59 简单可检视原则）。
- **Cron* 调度能力面（v2.2 C6，matcher `CronCreate` PreToolUse + `CronCreate|CronUpdate|CronDelete` PostToolUse/PostToolUseFailure）**：PreToolUse 对 `CronCreate` 的嵌套创建 fail-fast 门（scheduled-origin 会话不得再建 automation；裁决读 `scheduler_facts` 会话事实缓存，forbidden 在案后零探针重放）；PostToolUse 观察成功调用记能力证据（CronCreate → create=allowed + origin=interactive），PostToolUseFailure 仅对 CronCreate 且命中 `NESTED_REJECTION_MARKERS` 冻结分类时升格 create=forbidden（瞬时失败不是能力事实；CronUpdate/CronDelete 失败恒零写）。任务侧镜像经 `task_manager.observe_scheduler_context` 记 journal（**journal 唯一写点在该 API 内**，钩子绝不直接 append）；观测记账恒零输出，绝不阻断主流程

### 9.3 失败处理与循环安全

- **fail-open 降级可见**：降级路径全部放行并在 stderr 报 `ENFORCEMENT DEGRADED`，但记账不同——仓库降级（RB-2 起**按任务隔离**：只降级解析/git 失败的那个任务，其余任务照常求值，不再全局早退）与求值阶段结构性错误会向该任务 journal 记 `gate_degraded`（reason 词汇表见 §9.1），进程级崩溃兜底仅 stderr 可见、不写 journal（崩溃可能正是 journal 故障所致）——钩子起不来时 fail-closed 会卡死所有会话，降级可见优于假强制
- **续行有界**：运行时对 Stop block 的续行内建上限（每 turn 最多 3 次）；钩子侧连续两次 block 后第三次放行（stderr 报 `ENFORCEMENT GATE EXHAUSTED`、journal 记 `gate_exhausted`）——**此时模型必须向用户报告 blocked，不得声称完成**；两次 block 间出现真实工作事件即重置计数（活体实测：四重检查全路径单次 Stop 约 0.2s，正常仓库远低于 5s 钩子预算）
- **强制面（alpha2）**：ownership 越界 + 验证完成 + 审查有效 + 证据新鲜度（含视觉证据），四者同门按序检查

强制层的用户可见解释（自检、报文含义、被拦截恢复方法）见 `skills/enforcement`。

## 9.5 控制平面收口（v2.1 alpha1，M1-M3）

v2.1 第一批把「存在但可被 advisory 文本绕过」的能力收进机器可查的控制平面（计划与 ※DR 决策记录见 docs/GLM-Conductor-v2.1-Architecture-Agent-Implementation-Plan.md）：

- **execution_policy（M1）**：state 可选顶层块——自动化授权的事实源（worker 模式默认 background、并发默认 2 / 硬上限 4 冻结、auto_resume 默认 manual、升档须 authorization.source=user 且保存时校验）；legacy 缺块按保守默认解释。`runtime/execution_policy.py` 五个纯 API + CLI policy-* 子命令
- **dispatch permit（M2）**：`prepare_dispatch` 为单元签发持久 permit（`runtime/dispatch_wave.py`：每 permit 一文件、原子 rename 消费防重放、TTL 兜底、路径逃逸闸）；派发 prompt 必须携带 `GLM_CONDUCTOR_DISPATCH=<permit_id>` marker——新会话中 PreToolUse(Agent|Task) 对实施者类型无有效 permit 的派发直接 deny（D1 分级：只读类型豁免、Layer A 兜底）；background permit 由 hook 经 updatedInput 强制后台
- **runtime-observed 生命周期（M2）**：PostToolUse/PostToolUseFailure 自动消费/作废 permit 并 journal `agent_launched` / `agent_dispatch_failed`（tool_use_id ↔ permit ↔ unit ↔ agent_id 绑定）——与手写 implementation_started 互不替代；后台 Agent 结果回收 = 模型转述 + 原生档案对账两路（H2）
- **agent run 账本与档案 adapter（M3）**：`runtime/agent_run.py` 读取面（list_agent_runs / native_agent_metadata 白名单只读 adapter / run_lifecycle）——僵尸语义：原生档案 status=running 永不解读为存活（§7.3）
- **SessionStart 恢复注入（M3）**：`hooks/session_start.py` + `runtime/recovery.py`——新会话自动注入 RESUME CONTEXT（纯本地零 quota，无任务时安静），闭合连续性缺口 R2
- **四分 reconcile（M3）**：`reconcile_agent_run` 纯读分类（reuse_result / resume_with_progress / redispatch_clean / manual_ruling），证据优先级 repo 残留 > 新鲜验证 > 原生档案；进度包组装归模型侧（两层分工）
- **resume manifest（M3）**：commit/abort/finish 事务后自动刷新派生快照（写失败仅记警告、绝不阻断 state truth）
- **runtime CLI（WU-21-15）**：`runtime/cli.py` 取代 python3 -c 内联（policy/permits/agent-runs/manifest-show，退出码 0/2/1）

宿主硬约束（探查实测定型，计划 §2.3）：子代理内 hooks 不触发（H1）；后台 PostToolUse 只见 launch 确认（H2）；automation 20 槽上限（H3）；hook 失败三层语义（H4）——派发面 fail-open + 完成面 fail-closed 分层不变。第二批（M4-M6：wave 并行、quota 授权续跑、验证/审查溯源）见 §9.6。

## 9.6 第二批收口（v2.1 alpha2，M4-M6）

v2.1 第二批把并行派发、额度决策与证据溯源从模型纪律收进 runtime 确定性事实（计划 §21）：

- **dispatch wave 批量事务（M4）**：`task_manager.prepare_dispatch_wave`（CLI `wave-prepare`）一次调用完成「额度解析 → plan_dispatch 全量决策 → 逐单元租约 → wave 记录 + 全员 permit 签发 → 单条 `dispatch_wave_prepared` 事件」，事务性 all-or-safe-degrade——决策-租约循环对租约冲突单元剔除重试（excluded 单调增长保证有界终止），wave 记录落盘时其全部成员租约已在位，绝不出现「wave 记录 2 单元但只有 1 张租约」；成员全部终态/verifying 时 `finish_unit` 自动关闭 wave（`wave_closed`）。**预算矩阵（wu-21-09）**：cap_base 优先级「显式参数 → execution_policy.parallelism.max_workers → dispatch.max_workers（legacy）→ 2（默认并行，硬上限 4 冻结）」，eff = min(cap_base, effective_worker_budget)——AVAILABLE→策略值、PRESSURE/UNKNOWN→1、EXHAUSTED→0；eff=0 时传 1 进 plan，由 quota 闸自然全转 waiting_quota（dispatcher 不再整批挂起 UNKNOWN/PRESSURE）。**wave launch contract（§11.5）**：wave.units > 1 时全部成员同一回合并发派出、禁止「等第一个返回再派下一个」，主会话并行做验证规划与结果收集；PreToolUse permit 门追加 wave 成员资格环（检查序 5.5：wave permit 须指向 active wave 且 unit 在成员清单，deny 报文含 re-prepare wave 指引）
- **运行时额度解析（M5，wu-21-10）**：`quota/resolver.py` 的 `resolve_quota_status()` 纯入口——prepare 链的 quota_status 缺省从显式字符串改为 None 触发运行时解析（**绝不默认 AVAILABLE**），四级层级：新鲜缓存（≤300s 直采不发网络）→ provider 抓取（解析凭证 + 四态评估 + 原子写缓存）→ 陈旧缓存回退 → UNKNOWN（fail-open，预算折 1 不阻塞派发）；纪律：绝不重试网络、异常不外泄（只取 kind / 类型名）、凭证零落盘、缓存只存标准化 snapshot。**授权续跑链（wu-21-11）**：`handle_quota_exhausted`（CLI `quota-exhausted`）确定性转态链——执行态任务/单元转 waiting_quota，按 `execution_policy.continuity.auto_resume` 四态授权矩阵裁决（manual 不建自动化 / notify 只允许提醒 / auto_once 一次性 / until_done 逐窗续跑，后两者须 `authorization.source == "user"` 保存时校验）；wake.required 时返回 `quota_wake_prompt` 自足文本（宿主实锚：wake=同会话续行、SessionStart 不重放，故 task_id / 账本与仓库根 / 恢复首步 / 额度口径 / 预算状态 / 红线全部内置；maxRuns=1 一次性语义），窗口扣减消费点唯一合法位置 = resume commit point（修正计划 §15.1，C1b 落新 API 记 `consumed_quota_windows`）——C1a 起 persistent path 的 arm/fire/create 一律不消费窗口预算（D15-g：automation lifecycle ≠ quota epoch consumption），v2.1 的 arm-time 扣减 `wake-record` 降为 legacy 兼容入口；预算耗尽任务转 `waiting_user`（新状态词：等待用户重新授权）且不得再建任何自动化唤醒；恢复首步恒为 `quota-resume`（AVAILABLE/PRESSURE → executing + waiting 单元回 ready；EXHAUSTED/UNKNOWN → 零转态保守等待）
- **验证 / 审查溯源（M6）**：`runtime/provenance.py` 把「evidence 是 runtime 实际观察到的」从声明固化为机械事实（D4：runtime 受限主动执行）——`verify_unit` / `verify_task`（CLI `verify-unit` / `verify-task`）只执行 state 已声明的 required 验证命令（白名单闸 + policy 闸双闸），进程退出后零 TOCTOU 计算同刻指纹（单元作用域与 RB-1 门同口径、任务作用域与完成门同入口），每次观察落 durable verification receipt（tmp + os.replace 原子写 + journal `verification_receipt` 事件，runner 恒 "glm-conductor-runtime"），exit 0 时自动接线单元级 / 任务级既有证据流（exit != 0 只落 receipt 不写证据——失败本身是有价值的观察）；`run_review`（CLI `review-record`）由主会话申报已发生审查的裁决（verdict + reviewer + tool_use_id），绑定终指纹落 review receipt + journal `review_receipt` + state.review 同步镜像。**门消费（wu-21-13）**：Stop 完成门审查检查改为「fresh ship review receipt 唯一权威」——扫描任务 `receipts/` 取 observed_at 最新一张（损坏 receipt 跳过取余下），无任何 receipt → `review_missing`、最新非 ship → `review_rejected`、指纹过期 → `review_stale`；state.review 手写字段不再作为通过依据

## 10. 运行时边界（ZCode 约束）

- 连续性编排基于 ZCode 原生的本地会话生命周期机制，不是独立的云调度器或后台守护进程；桌面客户端需保持运行、机器需保持唤醒
- 强制层钩子依赖 `python3` 在 PATH（安装自检见 README / enforcement 技能）；钩子随插件分发、仅安装/更新后的新会话生效；子代理会话不触发钩子（Layer A/B 设计的由来）
- 定时任务数量与频率受 ZCode automation 机制约束；闲时任务可用性取决于版本与账号能力
- 子智能体以前台调用受支持为前提，不假定后台子智能体可用
- 子智能体只能看到会话启动时已连接的 MCP 服务，跨会话恢复后需重新确认
- fail-closed 纪律：所需角色缺失、证据路径不可得时停止通道并告知用户，绝不静默降级或替换角色（强制层自身的 fail-open 降级是显式可见的例外，见 §9.3）

## 11. 静态校验与发布

- `scripts/validate_plugin.py`（纯标准库，15 项检查）+ CI（`.github/workflows/validate.yml`，静态校验 + 单元测试）维护契约一致性：扫描 `plugins/`、`README.md`、`marketplace.json` 与本文档，`docs/history/` 不参与当前契约校验；全部文件扫描一律跳过 `__pycache__` 目录与 `*.pyc` / `*.pyo` 字节码（v2.1 alpha2——编译缓存残影曾造成幻影名 FAIL，wu-21-08 实录根因）
- 检查覆盖：旧名清理、禁词、quota 否定式声明、任务专属 checkpoint 路径、视觉协议标记、TASK_ID 必含、视觉新调用规范措辞、`plugin.json` 与 CHANGELOG 的版本一致性、钩子清单完整性（含脚本存在性）、runtime 状态层与技能契约标记、enforcement 审查 receipt 权威标记（v2.1 alpha2 新增检查 15）
- 运行时模块（`runtime/`）、钩子（`hooks/`）与 quota 子系统各配单元测试与子进程冒烟（`tests/`，v2.2 基线 2113 用例；下列为 v2.1 时点 828 的分项构成——状态层 133、日志 26、ownership 38、指纹 59、stop_gate 66、pre_tool_use 18、policy 20、work_unit 57、dependency 52、dispatcher 55、task_manager 72、reconcile 46、lease 37、quota 解析 29/抽象 12/适配器 29/调度器 37/凭证 25/诊断 17，含 §98 集成冒烟、§45 调度场景、§68-§69 恢复对账三场景与策略/传输端到端），随 CI 执行
- 版本策略：`plugin.json` 版本、CHANGELOG 最新条目、git tag / GitHub Release 三者保持一致

## 12. 演化边界

v2 开发在 `v2-dev` 分支进行，里程碑完成后合入 `main`（`main` 当前为 **2.1.0 stable** 发布态）。`v2-dev` 线当前为 **2.2.0 stable 发布面**（RH-06 双窗 dogfood 证据链已于 2026-09-05（UTC）闭合、stable 会话独立复核 14/14；RH-07 稳定终审随 ST-04 收口）：在 2.1.0 已落地的强制层（状态/四重完成门/指纹/策略门控）+ 额度感知连续性 + 任务与工作单元管理 + 租约保护的有界并行（上限 4，experimental——§101 允许 stable 保留 experimental 标记）+ v2.0.1 运行时完整性加固（完成生命周期门控、路由不变量、发现四分类、task_manager 事务边界、租约崩溃恢复、证据归属绑定）+ release hardening（RB-1 单元完成证据门 all-match 收紧、RB-2 repository.root 绑定 + per-task 完成门求值）之上，v2.2 叠加持久唤醒桥与额度连续性控制面（persistent recurring bridge、quota subscription 与窗口消费账、DRAINING 相位闸、RH-01..05 收口加固，测试基线 2076→2113）；§103 Definition of Done 全条目达成（v2.0.1 口径），v2.2 稳定门 = RH-06 双窗 dogfood 证据链 + RH-07 终审（见《GLM-Conductor-v2.2.0-Stable-Release-Implementation-Plan》）。除非实际使用暴露出具体能力缺口，不新增路由维度或角色；强制层只针对高置信不变量（越界、缺失证据、过期证据），不做语义解释型拦截。
