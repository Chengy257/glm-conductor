# v2.4.1 Continuity Hotfix — Implementation Report

> **Verdict: READY**（终裁，依据见 §10；独立审查裁决见 §11）
> **Spec:** `docs/roadmap/V2_4_1_CONTINUITY_HOTFIX_IMPLEMENTATION_SPEC.md`（父计划 `V2_4_1_CONTINUITY_HOTFIX_PLAN.md`）
> **Task:** `v241-continuity-7e4a2c`（route `full` / assurance `high`；Workflow run `dwfrun-03d5ac62-f844-4266-9103-58e7bd5d25b8`）
> **实施宿主:** ZCode 3.14 native Workflow；主会话 GLM-5.3；worker 显式 `account:bigmodel-individual-coding-plan/GLM-5.3-Flash`

## 1. Baseline 与分支

- 规格锚定 baseline：`550df5c45b13425bde6ffe704c02725e6d6c4b26`
- 实际实施 baseline：`e77b957`（同步后的 origin/main HEAD；相对规格锚的 Δ 仅为 v2.4.1 交接/规格文档与 Global Quota Clock 独立项目文档本身，零代码差异）
- 分支：`main`（线性提交，无 merge commit）

## 2. 提交清单（按工作包）

| 工作包 | Commit | 内容 |
| --- | --- | --- |
| H1 | `f356469` | `feat(v2.4.1): workflow submission model preflight module (H1)` — submission.py + 35 例测试 |
| H2 | `aa48fea` | `feat(v2.4.1): automation binding and continuity preflight (H2)` — task.py 三 API + decision 强化 + state.py 语义澄清 + 测试 |
| CLI 收口 | `9248e94` | `feat(v2.4.1): CLI surface for model selection and continuity arming (H1+H2 收口)` — cli.py 四子命令 + 测试 |
| H3 | `ffd4563` | `feat(v2.4.1): arm-before-work protocol and static contract checks (H3)` — 三技能文档 + validator check_16 |
| H4 | 本提交 | 文档真相同步（README 双语 / architecture / core-concepts / troubleshooting / CHANGELOG 2.4.1 / plugin.json + marketplace.json 2.4.1）+ 本报告 |

## 3. 变更文件

生产代码（4）：

- `plugins/glm-conductor/runtime/workflow/submission.py`（新建，纯 stdlib 零 I/O）
- `plugins/glm-conductor/runtime/task.py`（+bind/clear/preflight 三 API；scheduled_activation_decision 插入 continuity-unarmed 闸）
- `plugins/glm-conductor/runtime/state.py`（automation_id 语义澄清注释；形状零变更）
- `plugins/glm-conductor/runtime/cli.py`（+4 子命令 + `_AutomationRejected` + USAGE）

技能/校验（4）：

- `plugins/glm-conductor/skills/orchestration/SKILL.md`（§6 重构：§6.1 模型选型硬闸 / §6.2 双分支启动序列）
- `plugins/glm-conductor/skills/continuity/SKILL.md`（arm-before-work / 武装三命令 / recurring 优先 / one-shot 后继 / 终态清理）
- `plugins/glm-conductor/skills/continuity/references/long-horizon.md`（结构化 resume prompt 重写 + Scheduled Trigger 契约）
- `scripts/validate_plugin.py`（+check_16：§5.6 六静态契约）

测试（4）：`tests/test_workflow_submission.py`（新建 35 例）、`tests/test_quota_resume_v24.py`（+15 例 + 既有用例按 v2.4.1 契约更新）、`tests/test_cli_v24.py`（+四命令用例）、`tests/test_task_lifecycle.py`（+1 断言）。

文档/清单（8）：`README.md`、`README.zh-CN.md`、`docs/architecture.md`、`docs/core-concepts.md`、`docs/troubleshooting.md`、`CHANGELOG.md`、`plugins/glm-conductor/.zcode-plugin/plugin.json`（2.4.1）、`marketplace.json`（2.4.1）、本报告。

编译器（compiler.py / persona.py / adapter.py）、change_id、writer guard、完成守卫、reviewer 语义、Global Quota Clock 拆离——零改动（规格 §10 非目标保持）。

## 4. 定向测试与结果（逐门原样命令）

| 门 | 命令 | 结果 |
| --- | --- | --- |
| H1 门 | `python3 -X utf8 -m unittest tests.test_workflow_submission tests.test_cli_v24 tests.test_workflow_compiler -v` | **137 tests OK**（本机 Python 3.7.9——3.7 兼容语法同时得证） |
| H1 门 | `python3 scripts/validate_plugin.py` | **16/16 通过** |
| H2 门 | `python3 -X utf8 -m unittest tests.test_quota_resume_v24 tests.test_task_lifecycle tests.test_state_v24 tests.test_cli_v24 -v` | **208 tests OK** |
| H3 门 | `python3 scripts/validate_plugin.py` + `python3 scripts/smoke_plugin_load.py` | **16/16** + **0 项失败** |
| H3 门 | `python3 -X utf8 -m unittest tests.test_workflow_submission tests.test_quota_resume_v24 tests.test_cli_v24 tests.test_recovery_v24 -v` | **170 tests OK** |
| §6.1 集成束 | 十模块定向束（submission/compiler/cli/quota/task/state/recovery/writer/completion/agent_model_binding）+ validator + smoke | **385 tests OK** + **16/16** + **0 失败** |
| 变更文件 ruff | `ruff check <12 个变更 .py>` | **All checks passed** |

既有用例更新说明（规格 §7 契约变化口径）：`test_cli_v24.py` 与 `test_quota_resume_v24.py` 中原「auto 未 bind 即断言 resume-authorized」的用例按 v2.4.1 契约改为先 bind（夹具 `prepare_waiting` 缺省绑定 automation id），并新增 unarmed → `remain-waiting`（`continuity-unarmed`）锚定用例——这是契约加强的测试同步，非弱化。

## 5. 唯一一次全量回归（终门）

| 命令 | 结果 |
| --- | --- |
| `python3 -X utf8 -m unittest discover -s tests` | **Ran 749 tests — OK**（0 failures / 0 errors；v2.4.0 收口 682 → 净增 67） |
| `ruff check plugins/ scripts/ tests/`（CI 同口径，固定 0.4.4） | **All checks passed** |

全量回归在整个 v2.4.1 实施期**仅运行此一次**（H1-H3 期间零全量，测试经济纪律遵守）。

## 6. 模型选型活体证据（§6.2 / smoke A）

宿主 ListModels 实测配置面（7 id）含**两个** `account:` 前缀 Flash 候选（`bigmodel-individual-coding-plan` 与 `bigmodel-offpeak-idle-plan`）——按 INV-MODEL-01 属歧义局面：

1. **负面预检（真实宿主模型面，零额度消耗）**：
   - 仅文本模型 + 非 account 前缀 Flash → `workflow-model-select` **exit 2**（零候选，fail closed）；
   - 全量 7 id 不带 `--model` → **exit 2**，错误消息列出全部候选：「Flash 候选有 2 个，选择歧义——必须显式给出 exact id…」；
   - `--model account:bigmodel-individual-coding-plan/GLM-5.3` → **exit 2**（文本模型后缀专门拒绝原因）；
   - `--model account:bigmodel-individual-coding-plan/GLM-5.3-Flash` → **exit 0**，输出 `{"subagent_model": "account:bigmodel-individual-coding-plan/GLM-5.3-Flash", "model_policy": "explicit-flash-required"}`。
2. **正向提交**：实施 Workflow（`dwfrun-03d5ac62-…`）与冒烟 Workflow（`dwfrun-f2894b36-…`）均以显式 `subagent_model=account:bigmodel-individual-coding-plan/GLM-5.3-Flash` 提交；宿主 CreateWorkflow 回执两度确认「Subagents run on account:bigmodel-individual-coding-plan/GLM-5.3-Flash$max」，GetWorkflowRun 元数据 `subagent_model` 字段同值。
3. **worker 自报佐证**：冒烟 no-op worker 返回 `self_reported_model: "account:bigmodel-individual-coding-plan/GLM-5.3-Flash"`（主会话 GLM-5.3 纯文本，无法伪装多模态自报）。

结论：**主会话 GLM-5.3 上提交、worker 实际运行显式 GLM-5.3-Flash** 的目标行为在提交回执、run 元数据、worker 自报三层一致成立；省略/GLM-5.3 两类错误在提交前即被预检拒绝。

## 7. auto 连续性先武装后启动活体证据（§6.3 / smoke B）

隔离账本根（`%TEMP%\v241-smoke-u6zd8mh9`）no-op 任务 `v241-smoke-b`（route delegate）：

| 步 | 时刻（UTC） | 证据 |
| --- | --- | --- |
| create task（active，quota_resume=manual/0/0/null） | 04:44 | 任务落盘 JSON |
| authorize auto `max_resumes=2` | 04:44-45 | CLI exit 0，mode=auto |
| **preflight 未武装 → 阻断** | 04:45:01 前 | `{"ready": false, … "auto continuity is not armed"}`，**exit 2** |
| CronCreate recurring（`41 5 * * *`） | 04:45:01Z 标记之后 | `automation-547e8645-e0d9-46a5-930d-6c8d88c30eb1` 创建成功（recurring=true, active） |
| bind automation id | 04:45:14 前后 | CLI exit 0，automation_id 落盘 |
| **preflight 已武装 → 放行** | 04:45:14 前 | `{"ready": true, … "auto continuity armed"}`，**exit 0** |
| writer-acquire | 04:45:14 前 | `{"ok": true}` |
| CreateWorkflow（显式 Flash） | **04:45:14Z 标记之后** | run `dwfrun-f2894b36-4e4e-4cfa-93c8-d92945cd0995` 启动 |
| record run | 04:45:2x | state.workflow_run_id + 守卫补挂 |

**激活创建时刻先于 Workflow 启动时刻**（时间线标记 `before CronCreate 04:45:01Z` / `before CreateWorkflow 04:45:14Z` 与宿主 run created_at `04:45:2x` 交叉印证）——arm-before-work 顺序活体成立。

## 8. 唤醒/恢复活体证据（§6.4 / smoke C）

同一任务与 run（`dwfrun-f2894b36-…`）：

1. 主会话 TaskStop 安全停跑（stop_reason=model，宿主通知确认）；
2. `quota-wait`（`--force-refresh`）→ 任务 waiting_quota，真实 provider 观测 `AVAILABLE`（reset_at 09:00:20Z）；
3. 唤醒决策 `quota-resume-decision --force-refresh` → `resume-authorized`（附同一 `workflow_run_id`；`resume_count_after=1` 仅暂存，磁盘计数 0 不动）；
4. `quota-continuity-preflight` → `ready=true`（armed）——决策与预检**两道闸都过**才进入恢复；
5. `ResumeWorkflowRun`（**同一 run id**）→ 宿主接受，run 恢复运行；
6. `quota-resume-confirm` → **resume_count 恰 +1（0→1）**、任务转 active（phase=workflow）、`quota_resume_confirmed` 事件落账；
7. 重复唤醒决策 → `no-op`（"重复唤醒安全，不采取任何动作"）——重复唤醒安全性实证；
8. 恢复后的 run 完成剩余工作（45s 等待 leg `wait_exit_code=0`）并返回自报模型——同 run 恢复完成度闭环。

终态清理（§5.4 契约）：task.complete → CronDelete（`deleted:true`）→ CronList 确认为空 → **确认删除后** `quota-automation-clear`（精确 id）→ automation_id=null。全程单次尝试、零重试风暴、未触碰任何他人 automation。

## 9. recurring 与 one-shot 路径的证明口径

- **recurring：活体证明**（§7 的 `automation-547e8645` 即真实 recurring CronCreate；成功武装-启动-清理全链走通）。
- **one-shot：结构性测试**（规格 §6.5 允许的口径）——其约束（后继先创建、先绑定、preflight PASS 后才 resume；绝不在无后继时 clear；绝不硬编码周期推窗）由 validator check_16 静态锚定 + continuity/long-horizon 文档契约 + 单元测试（bind/clear/preflight 语义）承载；未做第二次昂贵宿主冒烟（recurring 是生产默认路径且已活体证明）。release 不因此阻塞。

## 10. 宿主限制与实施事件

- **App 关闭时的 Scheduled Task 触发仍未验证**（v2.4 已声明的诚实边界，v2.4.1 无新证据关闭它；文档如实保留）。
- 本宿主有两个 `account:` 前缀 Flash 候选——自动选型恒为歧义拒绝，须显式指定。这是 INV-MODEL-01 的设计行为而非缺陷，但意味着该宿主上每次提交都要带 `--model`/显式 `subagent_model`。
- H3 实施期间一次操作事故（worker 负向验证首轮误在真实仓库执行替换）：worker 自报已逐字节还原；主会话独立复核——全量 diff 审查零探针残留（生产 runtime 唯一 "Global Quota Clock" 命中为 journal.py:10 既有退役注记）、validator 16/16、定向束与全量回归全绿。事故关闭。
- H3 的 §5.5 裁决：恢复渲染（recovery.py）不改为**非必要**——INV-CONT-01 已由 runtime 决策闸 + CLI 退出码 2 + 唤醒协议文档三层机械承载；SessionStart 保持本地只读（主会话裁定接受）。
- CLI `quota-automation-clear` 在存量为 None 时对任意给定 id 幂等成功（task 层 None-幂等先于 mismatch 校验的读法）——已在 CLI 文档注明，属规格 §4.1 的合理解读而非偏离。

## 11. 独立审查与完成守卫

- 主会话验证：全量 diff 逐文件审查 + §4 全部门命令主会话亲自重跑 + 唯一一次全量回归（§5）——`record_validation(status=passed)` 已落账。
- 独立终审（assurance: high）：`glm-reviewer`（全新上下文只读）审查后经 `review-record` 记录裁决（见任务 journal `review_recorded` 事件；裁决须为 `ship` 才满足完成守卫第四查）。
- 完成守卫四查（写者守卫 / ownership 越界 / 主验证新鲜 / ship 审查新鲜）全过后经 `task.complete` 收尾。

## 12. 终裁

**READY**——规格 §12 Definition of Done 的两条失败模式均已机械关闭：

1. **无意外 GLM-5.3 worker 继承**：显式 provider 限定 Flash 选型是 delegate/full 的提交硬闸（CLI exit 2 阻断 + 技能强制序列 + validator check_16 静态锚定），且在本宿主活体三层证据一致。
2. **无「进入额度等待却无保证未来激活」的 auto 任务**：arm-before-work 由 bind/preflight 的持久化武装事实 + 决策层 continuity-unarmed 闸 + 唤醒协议（recurring 优先 / one-shot 后继先行）三层承载，先武装后启动与同 run 唤醒恢复全链活体证明。

发布顺序建议：推送 main → 打 tag `v2.4.1` → GitHub Release（操作者执行）。
