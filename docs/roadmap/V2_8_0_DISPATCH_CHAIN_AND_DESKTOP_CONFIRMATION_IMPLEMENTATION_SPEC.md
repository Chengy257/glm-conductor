# GLM Conductor v2.8.0 实施规格：直派链路由 + Desktop 免确认诊断修复

> **Status:** LOCKED — 等待 owner 实施授权（决策五项已于 2026-10-24 锁定，讨论档
> `POST_V2_7_0_MAIN_SESSION_DELEGATION_AND_WORKFLOW_UX_DISCUSSION.md` §4）。
> **基线：** main=0b10449（v2.7.0）。
> **目标版本：** v2.8.0（两项全收：Part A 免确认诊断修复 + Part B 直派链教义）。
> **本文即实施权威**——授权后实施者按此执行；规格外发现按流程回报，不擅改。

---

## 0. 锁定决策（owner 2026-10-24）

| 编号 | 决策 |
| --- | --- |
| D-A | 问题一走 **O1 诊断先行**。H1-H4 根因修复**随诊断预授权**；**H5 确证则停下回报 owner 裁定 O2/O3**（O2 与零持久化纪律冲突，不预授权）。 |
| D-B | 小任务判据 = **判断式红绿灯**（W4 风格，无机械代理指标）。 |
| D-C | **链块入账 journal**：新事件 `chain_block_delegated`（词汇恰十名→恰十一名）。scout/runner/单发机械 offload 仍零事件。 |
| D-E | **链内开放并行**：前提 = ownership 严格不重叠 + 零依赖 + 同批上限保守值；无 plan_stages 编译期检查，划分责任上移主会话。 |
| D-F | **两项全收 v2.8.0**。 |

### D-C 与词汇冻结面的对撞化解（实施前必读）

`runtime/task.py:232-234` 冻结注释明文「无宿主子代理生命周期词汇
（implementation_started / dispatch_* / lease_* / reviewer_invoked 等一律不入表）」，
`tests/test_task_lifecycle.py:765-773` 锚定恰十名 + 禁词。D-C 落地是**显式推翻其中一类**，
化解论证（写入修订后的冻结注释）：

- 禁令的类别是**宿主子代理生命周期镜像**（"Conductor never mirrors host execution state"）。
- 链块委派记录的**不是子代理状态，而是主会话的语义性分解决策**——它是
  `workflow_started`（delegate/full 实施基质启动记录，已在词汇表）的**直派对位物**：
  链就是低 delegability 长任务的实施基质，块委派 = 该基质的进度语义。
- 因此：`chain_block_delegated` 入表不违 "never mirrors host execution state" 原则；
  宿主生命周期词（implementation_started / lease_* / reviewer_invoked）**仍禁**；
  事件名不带 `dispatch_` 前缀（该前缀词仍在禁词表）。

## 1. 目标与非目标

**目标**

1. Desktop 会话首个（及后续）授权 path 型 CreateWorkflow 提交不再弹确认卡——钩子真实应答，
   或以活体证据确证 H5 并交 owner 裁定 O2/O3。
2. 低 delegability 长任务不再由主会话全程实施：直派链（dispatch chain）成为第三种实施形态
   （主会话直接实施仅限小任务），链块入账 journal，块间可并行。
3. v2.8.0 全发布链（版本面/CHANGELOG/README/缓存/快照/登记/tag/Release）。

**非目标**

- 不改 v24-compile / plan_stages / workflow 编译器任何行为。
- 不改 interview 四问结构（问题数、顺序、Q4 语义均不动）。
- 不动 mode 枚举与 state.json 既有键（state schema 零变更；唯一 schema 面变更 = journal 词汇 +11）。
- 不预授权 O2（session 级 addRules）；不做任何 OS 电源类操作（常设边界）。
- 不为 solo 短任务引入新流程（guard-free 直做路径不变）。

---

## 2. Part A：Desktop 免确认诊断与修复（W-A 系列）

### W-A1 钩子诊断装备化（代码，先于一切活体动作）

`hooks/permission_request_workflow.py` 弃权与放行路径加 **stderr 原因码**（行为零变更，
fail-open 不变，纯本地红线不变——仍不写文件不联网）：

- 每个弃权分支一行：`PERMISSION_HOOK abstain: reason=<code> detail=<…>`；reason 码集：
  `bad_payload / bad_event / bad_tool / no_path / path_outside / no_authorized_task`。
- `path_outside` 与放行路径的 detail 必含：**归一前 path、归一后 path、命中的 repo_root 及其来源**
  （`ZCODE_PROJECT_DIR` / `cwd` / 新增探测源），直接服务 H2/H3 判别。
- 放行路径一行：`PERMISSION_HOOK allow: task=<task_id> path=<归一后>`。
- 文件头 docstring 同步更新；对应单测（原因码矩阵）。

### W-A2 双臂活体诊断协议（Desktop，H1 排除法先行）

**前提自检**（书面留痕）：确认插件缓存已刷 2.7.0 且**诊断会话启动于刷新之后**
（会话钩子面 = 启动时快照，宿主事实）——这是 H1 的先验排除；不满足则先补环境再测。

- **臂 1（授权臂）**：新建 scratch 任务走完整 launch 序列（interview Q4 enable +
  `task-create --auto-approve-workflow` + v24-compile 1 节点最小 DAG + `writer-acquire` +
  path 型 CreateWorkflow 提交）。观察：卡是否出现、是否需要人工应答。
- **臂 2（对照臂）**：未授权任务（不带 `--auto-approve-workflow`）同型提交。预期：卡正常出现。
- **执行者与死锁护栏**：实施会话自身即 Desktop 会话，可亲自跑臂 1；**若卡出现且无人应答会阻塞
  实施回合本身**——协议规定臂 1 提交前先声明"本轮若卡死 30 分钟无自动应答即判 hook-fail 数据点"，
  由 owner 在场解卡（持续推进纪律：需用户配合面收敛到单点，不扩为管线停摆）。owner 不在场时
  臂 1 顺延，Part B 不受阻塞。
- **stderr 可观测性**：若宿主 hook 日志面不可读，允许 spike 分支临时 debug 落盘变体
  （落 `.glm-conductor/tmp/` 下）——**不进发布面**。
- **产出**：`docs/reviews/V280_W21_DESKTOP_CONFIRMATION_DIAGNOSIS.md`——判定链每步实测值、
  根因结论（H1-H5 之一或多）、证据（时间线/输出摘录）。

### W-A3 根因修复（按 W-A2 分流，预授权范围内）

| 根因 | 修复 | 面 |
| --- | --- | --- |
| H1 会话快照早于缓存刷新 | 环境面指引（troubleshooting 条目复核/补写「会话钩子面=启动快照，刷新后须新会话」），零代码 | docs |
| H2 repo_root 口径失配 | `repo_root()` 多源探测序：`ZCODE_PROJECT_DIR` → cwd → 从归一 path 自身向上探测（找 `.glm-conductor/tasks` 或 `.git` 标记，只读、层数封顶）；stderr 记录命中源 | hook 代码 + 单测 |
| H3 相对 path 归一基错 | 双侧：①教义——launch details 的 path-only 规则补「**必须绝对路径**」；②钩子——相对 path 按探测到的 repo_root 归一后再判 | SKILL.md + hook 代码 |
| H4 flag 漏带 / state 未落 | interview 段强化 Q4 答案与 `--auto-approve-workflow` 的绑定句（checklist 化措辞）；不改代码 | SKILL.md |
| H5 Desktop 壳层不走 ask 竞速 | **停**：证据落 W-A2 文档，回报 owner 裁定 O2/O3；Part A 以裁定结果收尾 | 流程 |

### W-A4 Part A 验收

双臂复测：授权臂无卡直跑、对照臂卡正常出现（H1-H4 修复后复测通过）；或 H5 证据文档 +
owner 裁定记录落档。钩子单测（原因码矩阵 / 多源 repo_root / 相对 path 归一 / 既有行为回归）全绿。

---

## 3. Part B：直派链教义与事件面（W-B 系列）

### W-B1 orchestration/SKILL.md 教义落地

1. **'Two-axis routing'** 补 Axis A 语义修正句：low = **不能一次性预先委派（无静态 DAG）**，
   ≠ 不可委派任何实施；第三判据（任务长度/上下文足迹）进入路由叙事。
2. **新节 'Direct-dispatch chain (low-delegability long tasks)'**（置于 'Direct dispatch roles'
   之后、'Main-session validation' 之前），内容七块：
   - **触发**：低 delegability × 长任务（红灯命中）。
   - **小任务红绿灯（D-B，判断式）**——绿灯（主会话直接实施）：预计 ≤1-2 文件、验证单命令、
     无需长程探索、数轮工具调用内可完成，或改动微小到派发开销（spec+report+验收）大于实施本身；
     红灯（必须走链）：预计多文件/多步、需大段自读文件内容、验证为长测试大输出（runner 可代跑）、
     预计工具轮数显著超小任务形态。**中途绊线**：直做中实际形态超预测（连续自读大文件、轮数
     超限）→ 触发 ROUTE REASSESSMENT（证据=观察到的生长）转链。
   - **块判据**：逐字引用 W4（'Task state and static DAG authoring' 的粒度判据）——**单源引用，
     不复制第二份判据文本**，防漂移。
   - **模型选择**：机械块 → conductor-impl-flash（成本+速度）；判断密集块 → conductor-impl
     （继承主会话模型；**诚实句**：同价，赢在上下文隔离非省钱）。两角色 frontmatter 静态绑定，
     不进 workflow-model-select。
   - **并行规则（D-E）**：前提 = 同批块 ownership **严格不重叠** + 相互零依赖；无 plan_stages
     编译期检查，**划分责任在主会话**；同批并行上限保守值 **3**（教义默认，ROUTE REASSESSMENT
     可依证据调）；观察到写冲突立即回退串行并记录。
   - **链事件**：每块派发即经 CLI 记 `chain_block_delegated`（W-B3）；scout/runner/无任务单发
     机械 offload 仍零事件（D13 修订版语义）。
   - **恢复**：唤醒轮 = journal 尾读定位链位 → 仓库事实复核 → 下一块重派（块恢复语义 = D10
     同款：从仓库事实重派，无子代理进程级续接）。
3. **'Direct dispatch roles' 节改写**：solo 辅助定位句替换为链主定位——"the main session is
   the **orchestrator of record**; it implements directly only when the whole task is small
   (green light), and chains bounded blocks otherwise"；D10 单节点条款保留；D13 句修订（链块入账、
   其余直派零事件）。
4. **'Task launch interview'** 补一句：链式任务写块角色为 frontmatter 静态绑定（impl/impl-flash），
   不进入 worker-model 问答与 pin 面（现状行为，明确成文）。

### W-B2 references/role-contracts.md 同步

- Axis A 'delegability: low' 段 + line 34 单句改写（"At delegability: low the main session
  implements" → 链主句 + 小任务例外）。
- 路由矩阵 Implementation 列：solo / audit 两行 → "main session directly (small) / direct-dispatch
  chain (long)"；语义点两行（solo=…/audit=…）同步。
- impl / impl-flash Trigger 增链触发描述（链是 impl 的主要触发场景之一）。
- **Main-session (architect) contract** 增 'Chain master duties'：逐块 spec 撰写、逐块验收
  （diff 范围 ⊆ 块 ownership、local_check 复核）、并行划分责任、链事件记录、链恢复重建。
  "never delegated" 六职责清单**不动**（链化强化而非弱化该分工）。

### W-B3 runtime 事件面（D-C 落地）

- `runtime/task.py`：`TASK_JOURNAL_EVENTS` 十一名（+`chain_block_delegated`，序位在
  `workflow_started` 之后）；冻结注释按 §0 D-C 化解论证修订；`TASK_JOURNAL_EVENTS` 消费面
  （journal 词汇校验、恢复读面）同步核对。
- **新 CLI**：`chain-block-record <repo_root> <task_id> <block_id> <impl|impl-flash>
  [--ownership <s>]` —— 追加 `chain_block_delegated` 事件（payload：block_id / role /
  ownership 摘要）；**同 block_id 重复记录 = exit 1 拒绝**（块 id 唯一，重复=调度错误信号）；
  任务不存在或终态 = exit 1；messages.py 单源补 USAGE 常量（v2.6.1 W3a 单源纪律）；
  cli.py USAGE 面与既有子命令同型。
- 附带核对：writer guard 语义零改动（guard 是任务级单持有，链内并行块同守卫之下，无新面）。

### W-B4 continuity/SKILL.md 链恢复节

- 恢复流程新增链位重建步：journal 尾读 `chain_block_delegated` → state（mode/assurance/
  非终态）→ git 事实复核（已落地块）→ 下一块重派；与 waiting_quota / quota_resume_confirmed
  既有决策门不冲突（链任务跨窗仍走同一 resume 决策序）。
- 语言边界纪律：模型执行面新增文本全英文。

### W-B5 测试与 validator 同步

- `tests/test_task_lifecycle.py` 词汇锚重写：恰十一名 + **禁词表保留**（dispatch_* /
  implementation_started / lease_* / reviewer_invoked 仍禁——新名不含禁前缀）。
- 新 CLI 单测：正常记录 / 同 block_id 重复拒绝 / 任务缺失拒绝 / 终态任务拒绝 / ownership
  可选参数形态。
- hook 单测随 W-A1/W-A3 扩容（见 W-A4）。
- `scripts/validate_plugin.py`：check_7（continuity/orchestration 锚）随新节名同步；
  check_12 版本一致性 → 2.8.0；check_14 若锚词汇计数同步；SKILL.md/role-contracts 文本锚
  随教义改写同步。
- 定向测试纪律：Phase 内定向，全量回归唯一一次在 Phase 3（历史纪律）。

### W-B6 README 双语 + docs 面

- README.md / README.zh-CN.md：路由三分形态 + 直派链一段（双语对齐，测试计数留 Phase 3 刷新）。
- docs/core-concepts.md / architecture.md 若含路由矩阵表述则同步；CHANGELOG(EN) 在 Phase 3。

---

## 4. 发布面（W-R，Phase 3）

plugin.json 2.8.0 → marketplace.json 2.8.0（**发布清扫必查**：v2.7.0 曾漏）→ CHANGELOG(EN) →
README 测试计数刷新（1090 → 实测数）→ **全量回归唯一一次**（本机 python3 -S 口径）→
validator 16/16 → glm-reviewer 独立终审（ship 门）→ 本地 commit 序列 → 统一 push（常设授权含
push；若弹窗阻塞无人值守流则按降级偏好本地滞留待统一 push）→ tag v2.8.0 + GitHub Release
Latest → 缓存 robocopy //MIR 镜像 → marketplace 快照刷新 → 登记。

---

## 5. 顺序、出垒门与裁定点

```
Phase 1 = Part A（W-A1 → W-A2 → W-A3 → W-A4）        出垒门: 诊断文档落档 + 双臂复测结果
Phase 2 = Part B（W-B1..B6，可与 Phase 1 并行开做；
         W-A2 活体臂若顺延不阻塞 Phase 2）            出垒门: 教义/事件/测试/validator 全绿
Phase 3 = W-R 发布收口                                 出垒门: §4 全链 + reviewer ship
```

- **owner 裁定点 1（条件触发）**：W-A2 确证 H5 → 停下回报，O2（session addRules，需放宽零持久化
  纪律）或 O3（接受首卡+唤醒协议只走 ResumeWorkflowRun）二选一裁定；裁定前 Phase 1 不收口、
  Phase 2/3 不受阻塞。
- **owner 裁定点 2（授权时即知）**：本规格等待实施授权——授权即 D-A 预授权范围生效。

## 6. 风险与回退

| 风险 | 缓解 |
| --- | --- |
| 臂 1 卡死阻塞实施回合 | 协议护栏（30 分钟判 fail 数据点 + owner 在场解卡）；owner 不在场则臂 1 顺延不阻塞 Part B |
| 链事件词汇扩容破坏旧读面 | 纯加法（+1 事件名），恢复读面按事件名过滤不受影响；测试锚同步重写 |
| 并行直派写冲突（无编译期检查） | 教义前提（ownership 严格不重叠+零依赖）+ 批上限 3 + 观察到冲突即回退串行 + ROUTE REASSESSMENT 记录 |
| 链被滥用到小任务 | 红绿灯判据 + 派发开销地板句（绿灯第 1 条即开销倒挂保护） |
| impl 大量使用推高成本 | 诚实句入教义（impl 同价赢在隔离）+ 机械块默认 impl-flash |
| H5 确证改变 Part A 收尾形态 | 裁定点 1 设计已在流程内，Phase 2/3 不受阻塞 |

## 7. 验收清单（owner 验收口径）

1. `docs/reviews/V280_W21_DESKTOP_CONFIRMATION_DIAGNOSIS.md` 落档，双臂复测：授权臂无卡、
   对照臂有卡（或 H5 证据 + 裁定记录）。
2. 教义面：SKILL.md 新节 + role-contracts 路由矩阵 + 链主契约全改写，锚定测试绿。
3. 事件面：词汇恰十一名测试绿；`chain-block-record` 五态单测绿；恢复流程文档含链位重建。
4. 版本面：plugin.json / marketplace.json / CHANGELOG / README 双语 = 2.8.0，测试计数实测值。
5. 质量门：全量回归唯一一次全绿 + validator 16/16 + glm-reviewer ship。
6. 发布链：tag v2.8.0 + Release Latest + 缓存镜像 + 快照 + 登记（清单核对，marketplace 版本面必查）。
