# GLM Conductor 架构（权威文档）

> 本文档是 GLM Conductor 的**唯一架构真相源**，描述 v2.0.0（v2-dev 开发线）的实际运行时行为。
> 契约细节以插件目录为准（`plugins/glm-conductor/` 下的 agents 与 skills）；本文档与其保持一致，冲突时以修复到一致为准，不得偏离开源文档单独演化。
> 历史提案存于 `docs/history/`，仅作参考，不构成当前实现依据。

## 1. 定位

```
GLM Conductor

Selective orchestration for GLM coding agents in ZCode.
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
| foreground（默认） | 当前会话内可完成 | 正常执行，不创建任何 continuation |
| resumable | 可能跨会话/跨可用性窗口中断的长任务 | 里程碑后写 checkpoint + 定时唤醒，唤醒后先检查再恢复 |
| idle | 非紧急、可无人值守 | 优先交给 ZCode 原生闲时任务 |

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

- 定时唤醒用安全周期性再激活（检查-恢复或等待），不 sleep 到固定时间；**调度触发本身就是存活探针**——额度感知调度（§7 quota 节）在 reset 已知时精确规划唤醒，不可用时回退本探针，不引入常驻额度轮询器
- 同会话投递：结果要回到当前会话，必须从当前聊天创建绑定本会话的定时续作
- 原生插件级 quota API 仍不存在（getQuotaRemaining 等均属虚构）、不硬编码 5 小时重置；额度查询走 provider-api 监控端点（§7 Quota-Aware Continuity），凭证不可得时回退周期性探针——额度观察只作为证据使用，不作为路由轴
- 完成清理只作用于本任务：删除 `.glm-conductor/tasks/<task-id>/` 单个目录、停止关联的定时任务、终止闲时排队，避免幽灵唤醒
- `.glm-conductor/` 是本地运行时状态：优先写入 `.git/info/exclude` 本地排除，不自动修改 tracked `.gitignore`

### 7.4 Quota-Aware Continuity（alpha3）

额度感知是连续性层的增强（`runtime/quota/`），**不是路由轴**——额度决定"何时能继续工作"，不决定"谁来做"，不据此自动换模型：

- **provider 抽象与解析**（§24-§27）：`provider.py` 定抽象边界（QuotaProvider / QuotaProviderError 五类错误），`parser.py` 把监控端点响应解析为标准化快照——按语义字段判窗（unit=3/number=5 → five_hour；unit=6/number=1 → weekly；与 type 无关），周窗可选（lite 套餐实测无周窗），容忍加性未知字段；`zai.py` / `bigmodel.py` 两个适配器走 `_http.py` 共享硬化层
- **安全条款**（§37 全部代码级落地并有测试锚定）：HTTPS only、严格 host allowlist（api.z.ai / open.bigmodel.cn，构造期拦截、绝不向清单外转发凭证）、短超时（5s）、限长响应（64KiB）、重定向禁用、原始响应不落盘、凭证零落盘（Authorization 头只存在于请求构造处，错误消息模板不含 key）
- **凭证链**（§36 provider-api 模式）：环境变量 `GLM_CONDUCTOR_QUOTA_API_KEY` 优先，已登录 ZCode 的 `~/.zcode/v2/config.json` provider 配置为文档化回退；两者皆不可得 → unavailable → 周期性探针回退
- **评估与规划**（§28-§33）：`scheduler.py` 纯函数四态评估（AVAILABLE / PRESSURE / EXHAUSTED / UNKNOWN，fail-open）与恢复规划——PRESSURE 在安全里程碑落 checkpoint；EXHAUSTED 取全部阻塞窗 max(reset)+grace 精确唤醒且唤醒后强制刷新；reset 未知或数据不可得 → 周期性回退，绝不虚构 reset 时间
- **诊断面**（§43）：`/glm-conductor:quota` 命令驱动 `report.py`（文本 + `--json` 双输出，zai→bigmodel 有界探测，退出码恒 0，零凭证输出）
- **查询时机**（§34）：只在刷新点查询（任务开始 / 路由选定后 / 大段派发前 / 里程碑后 / 调度恢复前后）+ 实例内短 TTL 缓存，不做常驻轮询

## 8. 任务与工作单元管理（v2 beta2）

Work Unit 是可独立派发的最小有界实施单元（§60-§73，`runtime/work_unit.py` / `dependency.py` / `dispatcher.py` / `reconcile.py`）——不是工作流 DSL：

- **状态模型**：十词状态词汇 + 26 条合法转换边（主链 pending→…→completed；quota/block 回退边；§69 恢复对账边；终态封锁）；单元必填 ownership 与 verification（无范围或无验证不可派发）；`attempt` 记录重试史，新调用不抹除失败史（§73 有界重试）
- **依赖图**：`depends_on` 同任务内引用、环拒绝（报全部环成员）；就绪 = status ready 且依赖全部 completed；确定性拓扑序驱动派发顺序
- **派发准入**（主会话仍是唯一编排者）：`plan_dispatch` 五道闸——quota 四态（EXHAUSTED→waiting_quota、UNKNOWN/PRESSURE 保守抑制，§67）→ ownership 不相交（保守近似：字面前缀相交即冲突，宁少并行不越界，§66）→ **租约闸（`runtime/lease.py`：任务专属 leases.json owner map、派发前全有或全无获取、同 owner 幂等、异 owner 冲突拒绝、写相结束释放，§78-§79）→ max_workers 预算（1-4，§82）→ 派发；默认串行，有界并行（上限 4，experimental）已启用——并行资格 = ownership 声明判定可并行 **且** 无外来活跃租约冲突（两道闸门均须通过，§66/§78；租约不豁免 ownership 闸）
- **事务边界（`runtime/task_manager.py`，v2.0.1 H4）**：派发生命周期的手工拼接（plan→租约→状态转换→`dispatch.active` 记账→save→事件）固化为四个高层 API——`prepare_dispatch`（决策 + 获取租约 + `dispatch_prepared` 事件；不动单元状态、不落盘，决策未批准时零副作用）→ `commit_dispatch`（租约在位校验 → ready→running → active 记账（幂等防御）→ 单次 save → `implementation_started`）→ `finish_unit`（终态转换——running+completed 双跳经 verifying 编码 §70 父验证语义——→ 释放租约 → active 移除 → 单次 save → `unit_finished`）；`abort_dispatch` 回退未提交的准备（running 已提交不得静默回退）。崩溃窗口映射（确定性恢复路径）：prepare 后中断 → 单元仍 ready + 租约在位（同 owner 租约不挡后续 plan）→ 可安全 commit 或 abort；commit 后中断 → running 且无验证证据 → 由既有 reconcile 按 §69 证据三分恢复。分层纪律：dispatcher 保持纯决策器（零 I/O），task_manager 是唯一做派发 I/O 编排的事务层
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

**corrupt 的 fail-closed 证据规则**：读该任务目录 journal（容错读，坏行跳过），任一 `route_selected` 事件 mode ∈ (audit, full) 或 assurance=high，或任一 `status_changed` 事件 to=finalizing → 判定该任务此前处于需要完成门强制的高保障 / 完成请求路径，状态损坏不得成为静默放行通道——并入违规清单（check=`corrupt_state`，为第八种 check）走统一 block / gate_exhausted 机器，报文含可行动恢复指引（从 events.jsonl / checkpoint 证据重建 state.json——仓库状态权威；或经用户确认后归档任务目录）。无此证据 → 降级放行：stderr 报警 + journal 记 `gate_degraded`（reason=corrupt_state）。corrupt 的证据判定只依赖 journal，不依赖 git——active 为空时 git touched 清单不再被取（两条既有降级路径 git_unavailable / evaluation_error 因此仅在 active 非空时可达）。由此状态损坏被结构化记录并可区分：no task（静默零干预）≠ unreadable task（报警，高保障时拦截）。

### 9.2 PreToolUse 双面：Layer B 注入（提示级）+ Bash 策略门控（决策级，beta1）

同一钩子脚本 `hooks/pre_tool_use.py` 按载荷 tool_name 分流：

- **Layer B（matcher `Agent|Task`，advisory）**：每次子代理派发前注入 ownership 契约提醒（声明清单 + 越界将拦完成门）。提高合规但不构成强制。
- **Bash 策略门控（matcher `Bash`，决策级）**：`runtime/policy.py` 表驱动规则（§57-§59，禁 DSL）把主会话 Bash 命令分类为 allow/ask/deny，经 `permissionDecision` 返回运行时——deny：rm -r/-f、git reset --hard、git clean -f、force push（--force-with-lease 归 ask）；ask（仅活动任务 assurance:high 时）：任何 push、模式迁移、发布操作、权限变更。门控顺序：非 Bash 不管 → 无活动任务零干预 → deny 无视保障级 → ask 仅 high → 其余默认放行。只覆盖主会话调用（子代理工具调用不触发钩子，角色级 deny 由 agent 工具白名单负责）；字符串中引用的破坏性文本与 `git rm -r --cached`（仅动索引）会被保守误拒（beta1 已登记取舍：误拒方向保守安全，特判排除违反 §59 简单可检视原则）。

### 9.3 失败处理与循环安全

- **fail-open 降级可见**：三种降级路径全部放行并在 stderr 报 `ENFORCEMENT DEGRADED`，但记账不同——前两类（git 失败 / 求值阶段结构性错误）会向参与任务 journal 记 `gate_degraded`，进程级崩溃兜底仅 stderr 可见、不写 journal（崩溃可能正是 journal 故障所致）——钩子起不来时 fail-closed 会卡死所有会话，降级可见优于假强制
- **续行有界**：运行时对 Stop block 的续行内建上限（每 turn 最多 3 次）；钩子侧连续两次 block 后第三次放行（stderr 报 `ENFORCEMENT GATE EXHAUSTED`、journal 记 `gate_exhausted`）——**此时模型必须向用户报告 blocked，不得声称完成**；两次 block 间出现真实工作事件即重置计数（活体实测：四重检查全路径单次 Stop 约 0.2s，正常仓库远低于 5s 钩子预算）
- **强制面（alpha2）**：ownership 越界 + 验证完成 + 审查有效 + 证据新鲜度（含视觉证据），四者同门按序检查

强制层的用户可见解释（自检、报文含义、被拦截恢复方法）见 `skills/enforcement`。

## 10. 运行时边界（ZCode 约束）

- 连续性编排基于 ZCode 原生的本地会话生命周期机制，不是独立的云调度器或后台守护进程；桌面客户端需保持运行、机器需保持唤醒
- 强制层钩子依赖 `python3` 在 PATH（安装自检见 README / enforcement 技能）；钩子随插件分发、仅安装/更新后的新会话生效；子代理会话不触发钩子（Layer A/B 设计的由来）
- 定时任务数量与频率受 ZCode automation 机制约束；闲时任务可用性取决于版本与账号能力
- 子智能体以前台调用受支持为前提，不假定后台子智能体可用
- 子智能体只能看到会话启动时已连接的 MCP 服务，跨会话恢复后需重新确认
- fail-closed 纪律：所需角色缺失、证据路径不可得时停止通道并告知用户，绝不静默降级或替换角色（强制层自身的 fail-open 降级是显式可见的例外，见 §9.3）

## 11. 静态校验与发布

- `scripts/validate_plugin.py`（纯标准库，14 项检查）+ CI（`.github/workflows/validate.yml`，静态校验 + 单元测试）维护契约一致性：扫描 `plugins/`、`README.md`、`marketplace.json` 与本文档，`docs/history/` 不参与当前契约校验
- 检查覆盖：旧名清理、禁词、quota 否定式声明、任务专属 checkpoint 路径、视觉协议标记、TASK_ID 必含、视觉新调用规范措辞、`plugin.json` 与 CHANGELOG 的版本一致性、钩子清单完整性（含脚本存在性）、runtime 状态层与技能契约标记
- 运行时模块（`runtime/`）、钩子（`hooks/`）与 quota 子系统各配单元测试与子进程冒烟（`tests/`，635 用例：状态层 90、日志 26、ownership 38、指纹 59、stop_gate 32、pre_tool_use 18、policy 20、work_unit 57、dependency 52、dispatcher 55、reconcile 21、lease 19、quota 解析 29/抽象 12/适配器 28/调度器 37/凭证 25/诊断 17，含 §98 集成冒烟、§45 调度场景、§68-§69 恢复对账三场景与策略/传输端到端），随 CI 执行
- 版本策略：`plugin.json` 版本、CHANGELOG 最新条目、git tag / GitHub Release 三者保持一致

## 12. 演化边界

v2 开发在 `v2-dev` 分支进行（`main` 保持在 v1.1.0 发布态，里程碑完成后再合入）。当前处于 **2.0.0 stable**——强制层（状态/四重完成门/指纹/策略门控）+ 额度感知连续性 + 任务与工作单元管理 + 租约保护的有界并行（上限 4，experimental——§101 允许 stable 保留 experimental 标记）全部落地；§103 Definition of Done 全条目达成。除非实际使用暴露出具体能力缺口，不新增路由维度或角色；强制层只针对高置信不变量（越界、缺失证据、过期证据），不做语义解释型拦截。
