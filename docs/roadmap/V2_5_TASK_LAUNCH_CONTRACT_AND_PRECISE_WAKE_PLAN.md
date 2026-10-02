# v2.5 Task-Launch Contract & Precise Wake Plan

> **Status:** PLANNED — 用户四项裁决已锁定（2026-10-02/03 讨论），待过目后实施
> **Baseline:** `2522dce`（origin/main HEAD，2026-10-03）
> **讨论裁决记录：** 模型选择=任务内询问一次授权后任务级默认；编排粒度=保持现状轻量收敛；跨额度唤醒=并存方案+任务开始强制问答；GQC=独立计划文档（见 `GLOBAL_QUOTA_CLOCK_V0_1_MINIMAL_IMPLEMENTATION_PLAN.md`）

## 1. Why v2.5 exists

v2.4.1 关闭了两个失败模式（无意外模型继承、无未武装 auto 任务），但留有四个体验缺口，均为 2026-10-02/03 讨论中用户点名：

1. **模型选型歧义的重复成本**——本宿主恒有两个 `account:` Flash 候选（individual / offpeak-idle），每次 CreateWorkflow 前都要人工带 `--model`；且候选集会漂移（plan 过期消失 / 新 plan 加入），不能静态写死。
2. **auto 连续性依赖用户主动声明**——用户忘说 until_done，长任务额度耗尽即裸停在 `waiting_quota`，无未来唤醒能力；入口是「被动等声明」而非「主动确认」。
3. **唤醒精度浪费**——`plan_resume` 已算出精确 `resume_at = max(reset_at) + grace`，但载体是固定周期 recurring cron：知道几点该醒，却用固定闹钟反复醒来试。
4. **编排粒度判据缺失**（轻量处理）——编制纪律只有定性上限，主会话易把「实施步骤」当「DAG 节点」编出碎图。

## 2. Design decisions（四条不变量）

### I1 — 任务级 worker 模型 pin（授权一次，任务内默认，失效即拒）

- **pin 是用户授权的任务级偏好，不是任务身份**：顶层键 `worker_model`，授权问答时写入，任务全生命周期默认使用（含唤醒恢复后的新 run 提交）。
- **写入不查宿主面，消费必查宿主面**：pin 落盘只做形状校验（复用 submission 形态约定）；每次 `workflow-model-select --task-ref` 消费时对账当次宿主列表——pin 不在列表即 **exit 2 专门拒绝**（"pin 已失效，请重新问答授权"），绝不回退自动选型、绝不静默换模型。候选漂移由此天然安全：新候选不自动采用，旧 pin 过期不自动替换。
- **submission.py 零改动**（公共面冻结维持）：pin 由 CLI 层注入既有 `explicit_model_id` 参数。
- **优先级固定**：`--model` 显式值 > 任务 pin > 自动选型（恰一候选自动选定不询问）。
- **唤醒轮 fail closed**：无人值守回合遇 pin 失效 → 阻断提交、转等待用户，绝不弹询问（死锁禁令）。

### I2 — 任务启动问答（launch interview，强制一次、两问合并）

- **触发点**：`create_task` 之前——凡建任务状态的任务（delegate/full/audit/跨回合长任务）必问；纯会话内小任务（无 state）不问（噪声控制）。
- **一次问答两题**（AskUserQuestion 单轮）：
  1. 跨额度自动唤醒：启用（含 `max_resumes` 预算选项）/ 不启用（manual，额度耗尽即停）；
  2. worker 模型：仅当选型歧义（多候选）时出现，列全部候选 id 与计划语义提示；恰一候选时自动选定、此题不出现。
- **答后落盘**：① 启用 → `quota-resume-authorize` + arm-before-work 武装序列；② 选定 → `worker-model-pin`。
- **问答只发生在交互回合**：唤醒轮/恢复轮绝不弹问。

### I3 — 武装建立顺序绝对优先（arm-before-work 升格为总原则）

- **「永远优先」指建立顺序，不指授权深度**：一旦问答确认启用，未来唤醒能力的建立/修复永远先于当下工作推进——武装 PASS 前不开工；唤醒轮发现 unarmed，第一动作是自动重建（CronCreate → bind → preflight PASS）而非任何恢复动作，重建失败按 remain-waiting 报告（不弹问）。
- **预算仍有限**：`max_resumes` 有界语义不变；预算耗尽转 `waiting_user` 等用户重新授权，绝不无限自动复活。
- 该原则同时收编 INV-CONT-02 推广：每次 `remain-waiting` 的第一动作是后继唤醒先行（见 I4），然后才是本轮收尾。

### I4 — 精确唤醒并存方案（recurring 兜底不动 + one-shot 加发）

- **recurring 是武装载体与兜底，INV-CONT-03 不变**：一次武装、绝不删除重建；one-shot 是无见证的辅助精确唤醒。
- **后继 one-shot 先行**：`remain-waiting`（EXHAUSTED 且已观测 reset_at）时，用最新观测计算 `delay = (reset_at + grace) - now`（分钟向上取整），`CronCreate` one-shot `delayMinutes`；`delay <= 0` 不调度（本轮即按决策处理）。换算是确定算术（reset_at 是 provider 观测的绝对时刻），不踩「相对推测推窗」禁令。
- **one-shot 零状态**：不 bind、不落 state、不清理——触发即自失效；重复加发由决策幂等兜底（早醒无害）。
- **绝不用 CronUpdate**（间歇故障史 + 改 cron 丢原周期的回落复杂度）。
- **窗口变动天然免疫**：每次 remain-waiting 都强制刷新重新观测，绝不硬信旧 reset_at（「绝不以加 5 小时推导下一窗口」禁令不变）；one-shot 醒早 → 新观测 → 再加发，醒晚 → 额度已恢复 → resume。
- **终态清理次序不变**：task.complete → CronDelete(recurring) → clear 绑定；one-shot 已自失效无需处理。

## 3. Intended implementation surface

| 层 | 文件 | 变更 |
| --- | --- | --- |
| runtime | `runtime/state.py` | 顶层新键 `worker_model`（可选；存在才校验：null 或非空 str；未知键忽略的向前兼容已具备） |
| runtime | `runtime/task.py` | 新 API `pin_worker_model`（非终态写入 / 同值幂等 / 异值显式覆盖，返回 prev+new；**不新增 journal 事件——十名冻结维持**，审计由 CLI 输出与 state 承载） |
| runtime | `runtime/cli.py` | 新子命令 `worker-model-pin`；`workflow-model-select` 增 `--task-ref`（优先级 --model > pin > 自动；pin 失效专门拒绝 exit 2）；`quota-resume-decision` 的 EXHAUSTED 分支输出只读透传观测 `reset_at`（向后兼容输出扩展，供换算 delayMinutes；UNKNOWN 分支无 reset 保持不透传） |
| runtime | `runtime/workflow/submission.py` | **零改动** |
| 技能 | `skills/orchestration/SKILL.md` | 新 §6.0 任务启动问答（I2 全契约）；§6.1 歧义分支改接问答→pin→`--task-ref`；§5.2 编制纪律加一句反模式（W4） |
| 技能 | `skills/continuity/SKILL.md` | 唤醒协议扩并存方案（I4）；唤醒轮 unarmed 自动重建优先（I3）；无人值守禁弹问措辞 |
| 技能 | `skills/continuity/references/long-horizon.md` | 唤醒 prompt 模板补后继先行要素；问答要素 |
| 校验 | `scripts/validate_plugin.py` | check_16 扩展锚：问答节存在+禁弹问措辞、pin 失效即拒措辞、后继先行并存段落 |
| 文档 | README 双语 / architecture / core-concepts / troubleshooting / CHANGELOG / plugin.json + marketplace.json | 2.5.0 真相同步（含版本一致性门） |

## 4. Work packages

### W1 — 任务级 worker 模型 pin

- `state.py`：`worker_model` 键校验（存在才校验，风格随既有 `_validate_*`）。
- `task.py`：`pin_worker_model(repo_root, task_id, model_id)`——形状预检（account: 前缀 + Flash 后缀，复用 submission 形态约定但不查宿主面）、非终态写入、同值幂等、异值显式覆盖；终态任务拒绝。
- `cli.py`：`worker-model-pin <repo_root> <task_id> <exact-id>`（exit 0/2/1 既有约定）；`workflow-model-select <models-json> [--model <id>] [--task-ref <task_id>]` 三来源优先级与 pin 失效专门拒绝。
- 测试：state 新键校验、pin API（幂等/覆盖/终态拒/形状拒）、CLI 优先级三态与失效拒绝（exit 2）。

### W2 — 任务启动问答契约

- `orchestration/SKILL.md` §6.0（新节，置于 §6.2 启动序列之前）：触发条件（create_task 前）、两问题形、答后落盘动作、交互回合边界。
- `continuity/SKILL.md`：唤醒四分支补「unarmed → 自动重建优先，重建失败 remain-waiting 报告，绝不弹问」。
- 纯文档契约 + validator 锚定；runtime 零改动。

### W3 — 精确唤醒 one-shot 加发协议

- `continuity/SKILL.md` 并存方案全契约（I4 五条）；`long-horizon.md` 模板要素。
- `cli.py`：`quota-resume-decision` EXHAUSTED 分支输出附 `reset_at`（只读透传；实施时核实输出组装点，保持四键决策形状之外的附加键向后兼容）。
- 测试：decision 透传用例（EXHAUSTED 带 reset_at / UNKNOWN 不带 / no-op 不带）。

### W4 — 编排粒度反模式提示（一句话）

- `orchestration/SKILL.md` §5.2 编制纪律追加：「节点对应交付物，不对应实施步骤——objective 只能用『交付了什么』表述；写不出交付物、只列得出步骤序（先/再/然后）的是步骤型节点，应向上合并；节点内实施顺序由 worker 自主决定。」
- 零 runtime、零 validator 改动。

### W5 — validator 锚定扩展 + 文档真相同步 + 版本面

- check_16 扩展锚（见 §3 表）；README 双语 / architecture / core-concepts / troubleshooting / CHANGELOG / plugin.json + marketplace.json → 2.5.0。

## 5. Test economy

- 定向为主：新测试束（pin / decision 透传）+ 既有束（submission / cli_v24 / quota_resume_v24 / task_lifecycle / state_v24 / recovery_v24）。
- 实施期**唯一一次全量回归**（`unittest discover`），ruff 全仓（CI 同口径 0.4.4）。
- validator 全过 + `smoke_plugin_load.py` 0 失败。

## 6. Release gates

1. 定向束全绿（各工作包门命令原样记录于实施报告）；
2. 唯一一次全量回归全绿（749 + 净增）+ ruff 全仓通过；
3. validator 16/16（含扩展锚）+ smoke 0 失败；
4. **活体证据**（隔离账本 + 真实宿主，三层）：
   - 问答→pin→`--task-ref` 提交（歧义宿主面上 pin 采纳、篡改 pin 后失效拒绝）；
   - auto 问答确认→武装 PASS→CreateWorkflow（启动序活证）；
   - EXHAUSTED→decision 带 reset_at→one-shot 加发（CronCreate delayMinutes 回执）→精确恢复或早醒再加发（至少走通加发与幂等分支）；
5. glm-reviewer 独立终审 ship（assurance: high 路径）；
6. **tag / GitHub Release 时机由用户另定**（「暂不发布」裁决维持；本计划只备好发布面）。

## 7. Non-goals

- 不改静态 DAG / 编译器 / ownership 语义（v2.4 根基不动）；
- 不加编排粒度机械判据契约（仅 W4 一句话提示）；
- 不用 CronUpdate、不做 recurring retime 复活；
- 不做全局（账本级）模型配置文件；
- 不做窗口感知自动换 pin（offpeak/inividual 切换留后续）；
- journal 十名冻结维持，pin 不新增事件名；
- 不做 GQC 集成（独立项目 v0.2 议题）。

## 8. Handoff

- 实施按 W1→W2→W3→W4→W5 顺序（W1/W2 可并行）；每个 W 一个 commit，遵循 v2.4.1 提交信息规范。
- 本计划文档随首个实施 commit 一并入库（计划先行过目）。
