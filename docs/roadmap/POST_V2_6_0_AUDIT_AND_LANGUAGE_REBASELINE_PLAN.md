# Post-v2.6.0 审查收口 + 语言与发布面重基线计划（v2.6.1 单一合并版本）

- **状态**：IN PROGRESS —— 2026-10-15 owner 下令开工（full 路由 / assurance high / manual 连续性 / worker pin = account:bigmodel-individual-coding-plan/GLM-5.3-Flash）；feature 分支 `v2.6.1-language-rebaseline`，逐批本地 commit，收尾统一 merge + push + tag v2.6.1 + Release + 缓存刷新（既有流程）
- **日期**：2026-10-15
- **基线**：main @ 7eb40e3（v2.6.0 已发布：tag + Release Latest + 缓存/快照/登记全链）
- **输入**：2026-10-15 主会话全仓审查（基准：ZCode plugin-creator 0.1.1 插件/marketplace 规范 + skill-creator 0.1.0 SKILL.md 编写规范；范围 = 111 个跟踪文件 + 本地未跟踪 tests/ 40 文件）+ owner 两轮讨论定案（语言政策：模型消费面 = 精简英文）
- **发布载体**：**单一 v2.6.1**（Phase 0 + W1–W5 + D1 全部同车；v2.6.1 不再拆分两车，v2.7.0 取消）
- **纪律**：每批书面计划（本档）→ 实施 → 定向测试 + 本机全量回归（tests/ 在盘）→ 语义等价独立审查（glm-reviewer，清单制，§4）→ 下一批；**翻译按行为变更对待，不是文档美化**

## 0. Owner 裁决清单（2026-10-15 讨论锁定）

1. **语言政策总纲**：模型消费的执行面 = 精简英文；人消费的维护面 = 中文照旧；对外人类文档 = 英文。SKILL.md 正文、references、agents 正文、commands 与 frontmatter description 同属**模型执行面**，全部英文化（本轮讨论修正了初版审查把正文归入「维持中文」的分类——正文是模型触发后逐行执行的契约，且插件公开发布、v2.6.0 起模型无关，非中文宿主模型是真实用户）。
2. **正文精简 = 结构性精简**：同义去重（A2 双源 runbook 单一事实源化）、理据/事故史外移 references、版本史引用（「v2.5.2 D3 强制化」类）退 CHANGELOG；**每条规范性约束（绝不/必须清单）1:1 存活，一条不丢、一条不弱化**；不做有损压缩——宁可少删，不可删漏。
3. **翻译按行为变更对待**：语义等价独立审查（CLI 名/flag/退出码/约束清单逐条核对）+ 真实 dogfood 验证 + 专门版本发布，不与功能改动混车。
4. **不维护双语镜像**：中文原文以 git 历史为存档；唯一例外是 README 双语对（v2.2.1 既有形态，保留不动）。
5. **模板关键字保持英文**（SELECTIVE ROUTE / mode: / VERDICT: 等已是英文）；自由文本字段（reason: 等）自然跟随会话语言，不做规定。
6. 审查建议全部采纳：A1/A2、B1–B4、C1–C3、Tier 2 三篇对外文档英文化、Tier 3 runtime 字符串两步走；D1 与 report.py 语言两项已裁定（§0.5）。
7. B5（manifest 只声明 skills、hooks/commands/agents 靠目录约定自动发现）为观察项不动作——生产实证有效，显式声明仅为可选自文档化。
8. **D1 测试纳入跟踪**（不建 CI，owner 既定「绝不触发 Actions」不变）：git add tests/ + .gitignore 摘除 /tests/ + README 增「本地运行测试」一节（PYTHONUTF8=1）。
9. **report.py 人读输出保留中文**（/quota 契约 = 原样呈现给用户本人；W3 不翻译 report.py 文案，仅其输出若被英文文档引用时如实标注语言）。
10. **发布全部合并为 v2.6.1 单一版本号**：Phase 0 不再单独先行发布，与 W1–W5、D1 同车。
11. **W5（docs 三件英文化）随车同发**。

## 0.5 待裁决项 → 已全部裁定（2026-10-15 owner 拍板）

| # | 事项 | 裁决 |
| --- | --- | --- |
| 1 | D1 测试发布 | **纳入跟踪**（裁决 8；不建 CI） |
| 2 | report.py 人读输出语言 | **保留中文**（裁决 9） |
| 3 | 发布载体 | **单一 v2.6.1 合并发布**（裁决 10） |
| 4 | W5 排期 | **随车同发**（裁决 11） |

## 1. 语言政策权威边界图

**模型执行面（英文 + 精简）**：

| 面 | 位置 | 现状 | 目标 | 批次 |
| --- | --- | --- | --- | --- |
| frontmatter description ×7 | skills ×3 / agents ×3 / commands ×1 | 中文；orchestration 868 字符、continuity 671（enforcement 245 尚可） | 英文，收敛 150–250 字符「做什么+何时用」；机制细节归位正文 | W1 / W2 |
| SKILL.md 正文 ×3 | orchestration 309 行 / continuity 261 / enforcement 101 | 中文 | 英文 + 结构性精简（裁决 2） | W1（enforcement）/ W2 |
| references ×3 | long-horizon 326 / role-contracts 339 / operations 82 | 中文 | 英文 + 去重；含唤醒 prompt 模板与编译期契约 | W2 |
| agents 正文 ×3 | glm-reviewer 46 / visual-implementer 105 / visual-reviewer 70 | 中文 | 英文（子代理的系统级指令；绑定维护说明保留） | W1 |
| commands/quota.md | 56 行 | 中文 | 英文 | W1 |
| worker persona | runtime/workflow/persona.py（TEXT_WORKER_PERSONA，78 行含中文） | 中文 | 英文——编译器内嵌进每个生成 Workflow，每次 run 被 worker 模型读取，最易漏的模型面 | W3b |
| SessionStart 恢复注入 | hooks/session_start.py（56）+ runtime/recovery.py 注入文案（134） | 中文 | 英文——每个会话启动即注入模型 | W3b |
| Stop block 理由与降级报文 | hooks/stop_gate.py（251；LEGACY_STATE_GUIDANCE 亦中文） | 中文（ENFORCEMENT DEGRADED 前缀已英文） | 英文 | W3b |
| CLI USAGE 与用户可见错误 | runtime/cli.py 字符串面（818 行含中文中的字符串部分） | 中文 | 英文 | W3b |
| 编译器生成注释模板 | runtime/workflow/compiler.py（94 行含中文，以 docstring 为主） | 实施时盘点 | 生成源注释模板若含中文一并英文化；编译器自身 docstring/注释不动 | W3 盘点 |

**人消费维护面（中文照旧，不动）**：runtime 各模块 docstring 与代码注释；docs/roadmap 全部；docs/reviews 全部；CHANGELOG ≤2.5.0 历史条目（断层记录见 §2.3-D2）；scripts/ 内部脚本注释；report.py 人读输出（裁决 9）。

**对外人类文档（英文）**：README.md（已英文，仅 W5 修锚点 + W4 版本面）；README.zh-CN.md（保留，双语对例外）；docs/architecture.md（497 行）/ core-concepts.md（210）/ troubleshooting.md（225）→ W5 英文化。

## 2. 审查发现清单（含证据锚点与处置批次）

### 2.1 P1

- **A1 continuity 面仍指向 legacy `v24-record-run`，与 orchestration 规范命令口径分裂**
  - 证据：skills/continuity/SKILL.md:143（启动 runbook 以 `v24-record-run <task_ref> <run-id>` 收尾）；skills/continuity/references/long-horizon.md:26（生命周期图 `CreateWorkflow(...) -> v24-record-run 绑 run id`）
  - 规范面：orchestration §6.2 双分支 + §6.4 已用 `task-record-workflow-run`（AF-04 守卫前置 + state.workflow_run_id 落盘 + workflow_started 事件），并明确 v24-record-run 为「legacy 低层薄壳：不写 state、不落事件、不动守卫」
  - 风险：照 continuity runbook 执行 → resume 链条（continuity/SKILL.md:61/67 自身依赖 record_workflow_run 落盘）与 AF-04 前置全部失效；continuity 文件内部自相矛盾
  - 顺带：long-horizon.md:25「显式 Flash 档模型选型」措辞停留在 v2.4.1 硬闸口径，应对齐 v2.6.0「用户授权显式选型、Flash 软推荐」
  - **处置：Phase 0（v2.6.1 第一刀）**
- **D1 测试套件未随仓库发布**：外部克隆零测试且 validator 缺件（scripts/validate_plugin.py:330 TESTS_DIR = REPO_ROOT/tests）；commit/CHANGELOG 引用的「975 OK / validator 16/16」对外不可复现；单机丢失风险。**处置：已裁决纳入跟踪（裁决 8），W3a 前执行**

### 2.2 P2

- **A2 启动 runbook 双源维护**：auto 启动序列 + 「成功陈述三分」在 orchestration §6.2 与 continuity「启动序列」节近乎逐字重复——v2.6.0 每次口径变更需双边同步，A1 即漏同步产物。**处置：W2 与翻译同刀**：完整启动/唤醒 runbook 归 continuity（单一事实源），orchestration §6.2 收敛为分支概览 + 指针（实施细化可调，原则 = 每个口径只有一个权威落点）
- **B1 marketplace.json 缺展示字段**：无 displayName / displayName_i18n（zh-CN）/ description_i18n / category（规范支持且与双语 README 定位契合）。**处置：W1**
- **C1 frontmatter description 超载**：orchestration 868 / continuity 671 字符——元数据常驻插件安装的每个会话上下文（skill-creator：「keep it short」）；§6.0/§6.1 门闸机制细节（flag 语义/退出码/优先级链）属执行面内容误入触发面。**处置：W1/W2 随各文件英文化同刀**；样例（orchestration 方向稿，最终以实施为准）：
  > GLM dual-axis selective routing + Native Workflow orchestration. The main session acts as architect: declare solo/delegate/audit/full routes on Delegability × Assurance, run the mandatory launch interview (wake authorization, worker model, floor interval), compile delegated work from a canonical DAG into ZCode Native Workflows, validate results itself, and route high-assurance work to fresh-context reviewers. Use for any implementation task, multi-step delivery, frontend/visual work, or independent code review.

### 2.3 P3 / 卫生项

- **B2** plugin.json 缺 `author` 字段（规范示例字段；owner 定名，如 Chengy257）。**处置：W1**
- **B3** `keywords` 钉 `glm-5.3`/`glm-5.3-flash`——描述已按 v2.6.0 去版本化，keywords 未跟；建议去版本化为 `glm`（可加 `quota-continuity` 等能力词）。**处置：W1**
- **B4** hooks/hooks.json:2 描述仍带版本史句（「v2.3 enforcement hooks … retired in Phase 2 W6」）；改为只描述现状（SessionStart 恢复注入 + Stop 完成守卫）。**处置：W1**
- **C2** enforcement description 版本钉住（「GLM Conductor v2.4 完成守卫契约」）——去版本号，与 B4 同理。**处置：W1 随英文化**
- **C3** 绝对化用语密度高：runbook 行内同一约束重复 3–4 次（「绝不」×N）；skill-creator 立场 = 解释 why 优于更大声重复（本项目理据节质量好，问题仅在重复）。**处置：W2 结构性精简内吸收（每条约束陈述一次，理据归 references）**
- **D2** CHANGELOG 语言断层：2.5.1 起英文、2.5.0 及以前中文——符合「历史原样」政策，**仅记录不动作**
- **审查通过项（无动作）**：三 SKILL.md 正文均 <500 行、references 渐进披露规范；README 结构/徽章/双语切换到位；plugin/marketplace 描述已全英文且版本一致；hooks 用 `${ZCODE_PLUGIN_ROOT}` 令牌、超时与 statusMessage 齐备；agents model 绑定 provider 全限定 + 绑定维护说明文档；/quota 退出码语义与只读边界完整

## 3. 实施分批（全部同车 v2.6.1）

### Phase 0 —— P1 修补（v2.6.1 第一刀）

- 改动：skills/continuity/SKILL.md:143 与 references/long-horizon.md:26 的 `v24-record-run` 改为 `task-record-workflow-run <repo_root> <task_id> <run-id>`；long-horizon.md:25 措辞对齐 v2.6.0 选型口径。文档级，零 runtime 改动
- 验收门：全插件面 grep `v24-record-run` 仅剩 orchestration §6.4 的 legacy 标注一处；validator 过；中文原文不动（本批不做翻译）

### Phase W1 —— 小体积模型面 + 发布面卫生（第一批，~430 行）

- 范围：agents ×3（正文+description 英文化）、enforcement SKILL.md（英文化 + C2 去版本）、commands/quota.md、C1 中本批文件的 description 精简、B1/B2/B3/B4
- 目的：以低风险小批次验证翻译流程与语义等价审查清单，再上大块
- 验收门：语义等价审查通过（§4 清单）；插件加载冒烟（scripts/smoke_plugin_load.py）+ /quota 面可用；ruff + validator（锚同步）零告警；plugin.json/marketplace.json 版本一致性检查

### Phase W2 —— 大块：orchestration + continuity + references（~1700 行，与 A2 去重同刀）

- 范围：orchestration/continuity SKILL.md 正文与 description、long-horizon / role-contracts / operations 全部英文化 + 结构性精简 + A2 单一事实源化 + C3 吸收 + 版本史引用退 CHANGELOG
- 方向性行数目标（非硬指标，规则零丢失前提下能省则省）：orchestration 309→~220、continuity 261→~200、role-contracts 339→~280、long-horizon 326→~280、operations 82 基本持平
- 验收门：语义等价审查（重点：§6.0 问答三问、§6.1 选型硬闸 8 条、唤醒协议分支树、四查守卫表、终态清理次序）；**真实 dogfood：delegate 全链（问答→pin→compile→select→acquire→CreateWorkflow→record-run→验证→收尾）+ 可行时一次唤醒轮**；本机全量回归 + validator 锚同步 + ruff
- 注意：正文引用的 CLI 输出若仍为中文（W3 之前），英文正文引用中文输出是预期过渡态，不阻塞
- dogfood 说明：本战役自身即 delegate 全链活体（本任务经问答→pin→compile→select→acquire→CreateWorkflow→record-run 执行）；唤醒轮不适用（manual 连续性，owner 裁决①），如实记录

### Phase W3 —— runtime 模型面字符串两步走

- **W3a 集中（行为零变）**：把模型可见字符串（USAGE、用户可见错误、block 理由、persona、恢复注入文案）抽到集中位置（如 runtime/messages.py 或就近常量模块）；本地测试断言改为引用同一常量；生成注释模板盘点（compiler.py）
- **W3b 英文化**：W3a 基础上换英文文案——persona.py、recovery.py/session_start.py 注入、stop_gate.py（block 理由 / LEGACY_STATE_GUIDANCE / 降级报文正文，ENFORCEMENT DEGRADED 前缀已英文）、cli.py USAGE+错误；report.py 保留中文（裁决 9）
- 验收门：W3a 后全量回归全绿（零行为变的证明）；W3b 后定向测试 + validator 锚 + 钩子冒烟（`echo '{}' | python3 hooks/stop_gate.py` 退出码 0）+ 编译产物抽查（persona 内嵌形态）

### Phase W4 —— 发布收口（v2.6.1 出垒）

- plugin.json / marketplace.json = 2.6.1；CHANGELOG（英文）；README 双语对齐（版本徽章 + D1 本地测试节；命令面无变化，CLI 表预期不动）；缓存/快照/登记全链（既有流程）；终门独立审查 ship；统一 merge + push + tag v2.6.1 + Release

### Phase W5 —— 三篇对外文档英文化（随车同发）

- docs/architecture.md（497）/ core-concepts.md（210）/ troubleshooting.md（225）英文化；**README.md:142 与 README.zh-CN.md 中指向 troubleshooting 中文标题的锚点链接同批修正**（翻译标题会断锚）；形态 = 直接英文化单篇（中文靠 git 历史，裁决 4）
- 验收门：三篇语义等价审查 + 两个 README 的全部外链可解析（锚点逐一验证）

### D1 —— tests 纳入跟踪（已裁决，W3a 前执行）

- 动作：.gitignore 摘除 `/tests/`；git add tests/（40 文件）；README 增「本地运行测试」一节（PYTHONUTF8=1 + 全量入口）；validate_plugin.py 无需改（TESTS_DIR 将真实存在）
- 不做：CI（owner 既定裁决）；测试内容本身不翻译（断言随 W3a 常量化自然迁移）

## 4. 验证纪律与语义等价审查清单（翻译专用，glm-reviewer 执行）

每批翻译类改动完成后，独立审查者持下清单逐条核对：

1. CLI 子命令名 / flag / 参数顺序 / 退出码（0/2/1）逐字一致
2. 规范性约束逐条存在且未弱化（绝不/必须清单——实施者需提交「约束迁移表」：旧文条目 → 新文位置）
3. 无新增规则、无删除规则、无语义改写（强度词不得升降级：绝不≠不得≠不应）
4. 结构化模板关键字不变（SELECTIVE ROUTE / ROUTE REASSESSMENT / GLM REVIEW / VISUAL REVIEW / CONTINUITY CHECKPOINT / 模板键名）
5. 机器可读面不变：JSON 键名、事件名（TASK_JOURNAL_EVENTS 十词）、状态七态、decision 封闭四词、verdict 三词
6. 交叉引用完整（章节名引用、references 相对路径、skills 互引）
7. 行数目标未以丢规则为代价达成（对照约束迁移表）

## 5. 非目标

- 不改任何运行时行为、状态机、命令面与 JSON 契约（W3a 是搬移不是改语义）
- 不建 CI、不恢复 Actions（owner 既定）
- 不翻译 docs/roadmap、docs/reviews、CHANGELOG ≤2.5.0 条目、runtime 模块 docstring/注释、report.py 人读输出（裁决 9）
- 不动 README 双语既有形态（除 W5 锚点修正 + W4 版本面）
- 不维护技能/文档双语镜像（README 例外，裁决 4）
- 不做 B5 manifest 显式声明化（观察项）

## 6. 附录：量化基线（2026-10-15 审查实测，实施后对照）

- description 字符数：orchestration 868 / continuity 671 / enforcement 245；agents：glm-reviewer 114 / visual-implementer 210 / visual-reviewer 190；quota command 221
- 行数：SKILL.md 309/261/101；references 326/82/339；agents 46/105/70；command 56；README 315/314；docs 三件 497/210/225
- 模型面 runtime 含中文行数（W3 盘点搜索空间，注释与字符串未分离）：cli.py 818 / task.py 615 / state.py 486 / stop_gate.py 251 / ownership.py 251 / resolver.py 227 / parser.py 227 / change_id.py 164 / durable_io.py 161 / _http.py 140 / recovery.py 134 / submission.py 107 / report.py 104 / credentials.py 101 / writer_guard.py 96 / compiler.py 94 / dependency.py 93 / adapter.py 88 / work_unit.py 82 / journal.py 79 / persona.py 78 / provider.py 74 / session_start.py 56 / time_utils.py 43
- CHANGELOG 中文断层：首个中文条目位于 2.5.0 节（L134）；2.5.1 起英文
- 仓库形态：111 跟踪文件；tests/ 40 文件未跟踪（.gitignore L10 `/tests/`）；validator TESTS_DIR 锚 scripts/validate_plugin.py:330
