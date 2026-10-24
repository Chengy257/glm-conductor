# POST v2.7.0 优化轮讨论：主会话委派语义 + Workflow 确认卡合并（2026-10-24）

> **Status:** 已锁定（LOCKED 2026-10-24，§4 五项全裁定）——实施权威移交
> `V2_8_0_DISPATCH_CHAIN_AND_DESKTOP_CONFIRMATION_IMPLEMENTATION_SPEC.md`，本文转历史档。
> **基线：** main=0b10449（v2.7.0 已发布）。
> **性质：** 问题二为教义（doctrine）级变更，问题一含一个未完成的宿主行为验证的失败数据点。

---

## 0. Owner 输入（原话摘要）

1. 会话首个 workflow 的确认提问仍没有成功合并到统一的用户提问回答环节，之后仍需用户回答且阻塞任务实施。
2. 主会话任务职责仍不够明确，任务选择路由有可优化部分：现行「不可委派的任务由主会话实施」需要更改——
   只有整体任务较小时才由主会话直接实施；任务很长但不能拆分工作流时，应按需直接派生子智能体实施
   （按复杂度选择继承主会话模型或 Flash 类实施子代理）。现行设计存在主会话全程完成复杂任务的风险，
   过长的复杂任务会污染主会话上下文——这正是插件功能要避免的。

---

## 1. 问题一：首个 CreateWorkflow 确认卡未并入统一问答环节

### 1.1 设计意图回顾（应然）

统一问答环节 = 启动面谈（Task launch interview）单轮四问（SKILL.md 'Task launch interview'，
orchestration/SKILL.md:145-157）。问题 4（workflow submission auto-approval）授权后：
`task-create --auto-approve-workflow` → state 顶层 `auto_approve_workflow: true` →
PermissionRequest decision-only 钩子（hooks/permission_request_workflow.py）在宿主 ask 层
竞速应答 → 确认卡作废，用户自面谈之后不再有任何工作流确认提问。**Q4 本身就是「确认已并入统一环节」的载体。**

### 1.2 实测报告与验证边界（实然）

- Owner 报告：首个 workflow 确认卡仍出现、仍需回答、仍阻塞。
- B0 spike 边界（docs/reviews/V270_B0_SPIKE_RESULTS.md:34-38）：机制二是**源码推演成立 + headless
  不可判**（headless 两臂均自动放行）；**Desktop 双臂活体验证归 B2 验收，发布时未做**——正是
  v2.7.0 收官记录里的遗留项。Owner 本次报告 = 该遗留验证的首个数据点，**结果为 FAIL**
  （至少首次提交未被钩子应答）。
- 措辞推断（待 Owner 确认）：报告说「会话**首个**」，隐含后续提交无卡。若属实，后续无卡的来源
  大概率是 owner 应答首卡时点了**会话级授权按钮**（B0 旁路矩阵：`rule.session.allow` 旁路
  alwaysAsk），而非钩子生效——即钩子在该会话**全程未生效**，不只是首卡输掉竞速。

### 1.3 候选根因（诊断假设，按先验排序）

| 编号 | 假设 | 机制锚点 | 可修性 |
| --- | --- | --- | --- |
| H1 | 会话启动早于 2.7.0 缓存刷新 → PermissionRequest matcher 未进该会话钩子面快照（宿主事实：matcher=启动时快照） | reference-zcode-plugin-cache-refresh | 环境性，换新会话即消 |
| H2 | `repo_root()` 口径失配：ZCODE_PROJECT_DIR 未注入且钩子进程 cwd ≠ 仓库根 → 判定链 (4) path 检查失败 → 静默弃权 | permission_request_workflow.py:95-114 | 可修（口径加固/双源探测） |
| H3 | 提交 path 为相对路径，钩子按自身 cwd 归一后落到仓库外 → 弃权 | 同上（os.path.abspath 相对基） | 可修（与主会话约定绝对路径提交，或钩子多根探测） |
| H4 | 实跑时 `--auto-approve-workflow` 漏带 / state 未落 → 判定链 (5) 不过 | permission_request_workflow.py:117-142 | 可修（提交流程 checklist 化） |
| H5 | Desktop 壳层确认 UI 不走 `permissionBroker.requestPermission` 的 ask 层竞速（源码分析覆盖主链，Desktop 壳层路径未证）→ 机制二在 Desktop 结构性不成立 | V270_B0_SPIKE_RESULTS.md:13-18 | 不可插件侧修 → 转 O2/O3 |

注：钩子弃权路径完全静默（stdout 空、无 stderr），H2/H3/H4 在用户端不可见——这本身是诊断成本放大器。

### 1.4 严重性再评估（影响选项取舍的两个宿主事实）

1. **CLI 侧无人值守零摩擦**：L1/L2 唤醒轮 = headless CLI resume turn
   （continuity/SKILL.md:169-174，`--workflow-mode alwaysOn -p`），headless ask 层自动放行
   （B0 P-1 附带事实 2）→ 无人值守再提交在 CLI 侧**不依赖钩子**也零摩擦。
2. **L0 唤醒轮是真实暴露面**：L0 floor（Desktop 主会话内 CronCreate）与 precision sleep
   唤醒的都是**创建它们的 Desktop 主会话**——唤醒轮是 Desktop 内无人值守轮，此时若需
   CreateWorkflow 再提交（run 已终态/需重建），确认卡无人应答 = 死锁。**W2 钩子对这条路径有
   真实防死锁价值，不能因为交互面失败就退役。**（缓解事实：恢复首选 ResumeWorkflowRun 同 id
   续跑，不触发 CreateWorkflow 确认；只有 run 终态后的重建提交才撞卡。）

结论：问题一在「用户在场」场景是 **UX 承诺落空**（Q4 答了还要再答一次），在「L0 唤醒轮重建提交」
场景是**潜在死锁面**。后者决定必须修到「钩子或等价机制在 Desktop 真实生效」，不能只做诚实化声明。

### 1.5 优化选项

- **O1 诊断先行（推荐第一步，W2.1 spike）**：缓存刷新后的新 Desktop 会话双臂复现
  （授权任务 path 提交 vs 未授权任务提交），钩子加临时诊断（stderr 输出判定链每步输入与判定
  结果 + 弃权原因码），核对 rollout jsonl 钩子注册、ZCODE_PROJECT_DIR 注入实况、提交 path
  形态、state flag 落盘。按根因分流：H1=环境面指导；H2/H3/H4=代码/教义小修；H5=转 O2/O3。
  顺手产出（无论根因）：钩子弃权路径加一行 stderr 原因码（不改行为，fail-open 不变），
  把下次诊断成本降为零。
- **O2 session 级 addRules 变体（H5 成立时的机制切换）**：B0 旁路矩阵证实 `sessionRules allow`
  旁路 alwaysAsk（V270_B0_SPIKE_RESULTS.md:19-22）。钩子在首个满足判定链的请求返回
  `decision.allow + permissionUpdates addRules`（session 作用域、CreateWorkflow + path 前缀
  规则），后续提交走旁路、不再依赖竞速。**代价（需 owner 裁定）**：与零持久化纪律冲突——
  授权判定从「逐请求现查任务态」退化为「会话期规则」，任务终态后会话内存在残余授权窗口；
  session 规则随会话死，比 project 级持久化轻，但仍是放宽。
- **O3 接受首卡 + 诚实化（Desktop 卡结构性不可抑制时的兜底）**：把首卡定义为「运行确认门」
  （用户在场，答一次）；Q4 语义收窄为只服务无人值守再提交（state flag 供钩子在 L0 唤醒轮用）；
  唤醒协议优先 ResumeWorkflowRun、把「终态后重建提交」在 Desktop 无人值守轮列为待用户处理
  而非自动重派。交互总量 = 面谈一轮 + 首卡一次。**不满足 owner「合并到统一环节」的诉求，
  仅作为 O2 也不可行时的诚实降级。**
- **O4 宿主面 feature request（并行提，不指望短期）**：向 ZCode 提插件声明式
  CreateWorkflow path 级权限规则 / run 预确认 API。机制二的 ask 层竞速是插件侧唯一杠杆，
  宿主原生支持才是终态。

**推荐：O1 先行（含弃权原因码）；按根因分流 O1-fix / O2 / O3；O4 并行提。**

---

## 2. 问题二：主会话职责与路由 —— solo/audit 长任务的直派链化

### 2.1 现状证据（设计漏洞确认）

- 路由矩阵（references/role-contracts.md:54-61）：`low|standard → solo → main session (host model)`、
  `low|high → audit → main session (host model)`；Axis A 明文「At delegability: low the main
  session implements」（role-contracts.md:34）。
- delegability: low 的典型判定（role-contracts.md:23-32）：架构未定 / 根因未知 / 重跨模块判断 /
  实施本身就是主要推理问题 / 设计可能频繁变化——**这些恰是「长而复杂」任务的典型特征**。
- solo 辅助定位（orchestration/SKILL.md:213）：「main session remains the implementer of
  record; mechanical blocks may be offloaded」——offload 存在但处于辅助位，默认是主会话自己写。
- 推论：**低 delegability × 长任务 = 主会话全程实施 → 主上下文被文件内容 / diff / 测试输出灌满**。
  Owner 判断准确：这是设计层漏洞（路由语义错位），不是执行层走样——教义照做也会得病。

### 2.2 语义修正的核心一句话

Axis A 问的其实是「**能否一次性预先给出全部有界 spec（静态 DAG）**」。low ≠ 不可委派任何实施，
= **不能一次性预先委派**。缺的是第三种形态：

> **直派链（dispatch chain）**：低 delegability 的长任务，主会话当**链主**——动态逐块委派。
> 每块 = 一份有界五段 spec；块间由主会话验收衔接；主上下文增长 ~O(块数 ×（spec+report）)，
> 不再 ~O(任务规模)。

路由三分（修正后）：

| 场景 | 实施形态 | 主会话角色 |
| --- | --- | --- |
| 整体任务小（任何 delegability） | 主会话直接实施（现 solo 短任务形态） | 实施者 + 验收者 |
| 长 × 不能预先 DAG 化（low） | **直派链**：逐块派 conductor-impl / conductor-impl-flash | 架构师 + 链主 + 逐块验收 |
| 长 × 能预先 DAG 化（high） | Native Workflow（现 delegate/full，不变） | 架构师 + 任务级验收 |

### 2.3 设计要点

1. **块判据 = W4 逐字复用**：一完整交付物 / 太大红牌必拆 / 太碎红牌必并 / 路由层先消解
   （orchestration/SKILL.md:124-129）。块 spec = 五段式（role-contracts.md:102-135）+
   TASK CONTEXT PACK（15-40 行，role-contracts.md:139-183）。
2. **模型选择（按复杂度，与 interview Q2 软推荐口径对齐）**：机械块 → conductor-impl-flash
   （成本+速度）；判断密集块 → conductor-impl（继承主会话模型——同价，赢在上下文隔离不省钱，
   教义里诚实写明）。两角色契约现成（role-contracts.md:353-368），无新角色、无新 frontmatter。
3. **小任务判据（主会话直接实施的准入）**：建议 W4 风格判断式红绿灯——
   绿灯（可直接做）：预计 ≤1-2 文件、验证单命令、无需长程探索、几轮工具调用内可完成，
   或改动微小到派发开销（spec+report+验收）大于实施本身；
   红灯（必须走链）：预计多文件/多步、需要大段自读文件内容、验证是长测试大输出
   （runner 可代跑）、预计工具轮数超阈值。
   **中途绊线（观察哨）**：主会话直接实施中若实际形态超预测（如连续自读大文件、轮数超限），
   触发 ROUTE REASSESSMENT（证据=观察到的生长），转链——防「以为小实际大」的漏洞。
4. **写型纪律不变**：链内写块仍须活动任务态 + writer guard（现教义，SKILL.md:211）。
   solo 短任务本来就 guard-free 直做；长任务建态 + 完成守卫本来就该有——语义自洽。
5. **恢复语义**：块失败/部分 → 主会话裁决、修 spec 重派（恢复=仓库事实，D10 同款，
   references 无 NodeResult 的轻通道语义）。跨额度窗链恢复：唤醒轮从任务 state + 仓库事实重建
   「当前块」。**可选项（Q3）**：每块一条 journal 事件（如 `dispatch_block`）让唤醒轮 O(1)
   定位链位置——与 D13（直派零事件，SKILL.md:215）冲突，需 owner 裁定。
6. **mode/schema 零变更**：solo/audit 语义内部细化「直接实施 vs 直派链」，state.json 仍只存
   mode+assurance；「main session remains the implementer of record」措辞改为
   「orchestrator of record（小任务时兼任 implementer）」。
7. **并行多派（可选项，Q4）**：链内独立块理论上可同窗多派（ownership 不重叠前提，与 workflow
   节点并行同款安全模型）；但直派无 plan_stages 编译期保障，v1 保守串行更稳。
8. **PREFLIGHT 读卫生（次级同病，P2 项）**：侦察也是上下文大头。现教义「deep reconnaissance
   still prefers host Explore / main session reading」可加上下文预算：预计自读超阈值 → 改
   scout 链提问式侦察（bounded question + file:line 证据返回）。

### 2.4 反向风险（诚实列出）

- **派发开销地板**：一块 = spec + report + 验收的固定成本，块太小纯亏 → 「太小必并」判据必须同落。
- **链的串行性**：块间有依赖时无并行收益（对比 workflow stage 并行）；独立任务仍应优先 DAG 化。
- **极端耦合回退口**：每步都需读前步全部产物的任务，链化 handoff 成本可能超过收益 → 判据保留
  「极端耦合可回退主会话实施」出口，但须带观察哨与 reassessment 证据，防回退口滥用成默认。
- **subagent 自身上下文上限**：单块仍须有界（W4 天然覆盖）；链的价值恰是每块新鲜上下文。

### 2.5 实施面粗估（讨论阶段口径）

- 文档面：orchestration SKILL.md（Role and prerequisites / Two-axis routing / granularity /
  Direct dispatch roles）、references/role-contracts.md（Axis A 语义、路由矩阵 Implementation
  列、solo/audit 语义点、impl/impl-flash Trigger）、continuity（仅当采纳链事件）、README 双语、
  CHANGELOG。
- 代码面：无新 CLI、无 schema 变更（除非 Q3 采纳链事件 → runtime journal 词汇 + CLI 透出）。
- 测试面：角色契约锚定测试 + validator 锚点随教义同步（历史教训：validator 锚点随架构同步）。
- 总体：教义级变更，风险低，主要工作量在判据措辞与锚定。

---

## 3. 两问题协同

- 直派链**不经过 CreateWorkflow** → 链式任务天然免疫问题一的确认卡。问题二落地会显著缩小
  问题一的暴露面（长任务多走链/workflow，Desktop 首卡只剩 workflow 首提交一次交互）。
- 问题一的 O1 诊断结论（钩子在 Desktop 的真实行为）决定 O2/O3 取舍；两者无实施顺序硬依赖，
  可并行进同一轮。

---

## 4. 裁定（owner 2026-10-24 锁定）

| # | 问题 | 选项与推荐 | 裁定 |
| --- | --- | --- | --- |
| Q1 | 问题一路径 | O1 诊断先行（推荐）/ 直接 O2 session-addRules / O3 接受首卡诚实化 | **O1 诊断先行**（H1-H4 修复随诊断预授权；H5 确证则回报裁定 O2/O3——O2 与零持久化纪律冲突，不预授权） |
| Q2 | 小任务判据形态 | 判断式红绿灯（W4 风格，推荐）/ 加机械代理指标 | **判断式红绿灯** |
| Q3 | 链 per-block journal 事件 | 不加（守 D13）/ 加（唤醒轮 O(1) 恢复，推荐倾向） | **加链事件**（讨论后锁定为 `chain_block_delegated`，词汇 10→11；与 task.py 词汇冻结注释的对撞化解论证见规格 §3 D-C） |
| Q4 | 链内并行多派 | v1 串行（推荐）/ ownership 不重叠前提下开放并行 | **开放并行**（ownership 严格不重叠 + 零依赖前提；无 plan_stages 编译期检查，划分责任上移主会话，保守批上限入教义） |
| Q5 | 版本载体 | v2.7.1 / v2.8.0（推荐） | **两项全收 v2.8.0** |

**实施规格承接**：`docs/roadmap/V2_8_0_DISPATCH_CHAIN_AND_DESKTOP_CONFIRMATION_IMPLEMENTATION_SPEC.md`（本讨论文档随之进入历史档，后续以规格为权威）。
