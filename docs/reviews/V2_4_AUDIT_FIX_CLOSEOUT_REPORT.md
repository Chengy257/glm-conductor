# GLM Conductor v2.4 Audit-Fix Closeout Report

> **FINAL RELEASE CLOSEOUT OVERRIDE — RESOLVED:** A subsequent repository audit found one additional merge-blocking correctness bug: terminal lifecycle cleanup releases the repository writer guard using the ledger root instead of the task's effective bound `repository.root` when those roots differ. The same audit also confirmed that the literal fresh-session reviewer proof required by the original gate was not yet performed, and found release-document status drift. The Final Release Closeout Addendum (§A1–§A5, 2026-09-21) closes FR-01–FR-04 and issues the final verdict `MERGE_READY`, which supersedes this temporary override and re-affirms §9. §1–§9 remain the AF-01–AF-06 evidence record, unmodified.


> **Branch:** `review/zcode-3.14-native-workflow`
> **报告落盘时分支头：** `8330e15`（本报告与 troubleshooting F3 修正随下一提交入列）
> **日期：** 2026-09-21（UTC+8）
> **实施载体：** 单一原生 Workflow `dwfrun-87d28514`（全部实施子代理 GLM-5.3-Flash）+ 主会话（GLM-5.3）分析/验收/宿主活证
> **权威规格：** `docs/roadmap/V2_4_AUDIT_FIX_CLOSEOUT_PLAN.md` + `V2_4_AUDIT_FIX_CLOSEOUT_IMPLEMENTATION_SPEC.md`

## 1. 分支与提交

| 提交 | 内容 |
| --- | --- |
| `b61fad9` | fix(v2.4): bind change freshness to owned touched files（AF-01） |
| `61fd0f6` | fix(v2.4): restore multimodal visual reviewer binding（AF-02） |
| `d71b052` | fix(v2.4): expose bounded quota resume cli（AF-03） |
| `b60f488` | fix(v2.4): enforce writer guard in delegated lifecycle（AF-04） |
| `b698797` | docs(v2.4): audit-fix wording consistency sweep（D1） |
| `8330e15` | fix(v2.4): complete audit-fix sweep leftovers（grep-0 连带 + change_id 参数更名 + 一致性尾巴） |

全部为本地提交；push 在本报告落盘并随终提交后一次执行。

## 2. AF-01 – AF-06 处置

### AF-01 change_id 作用域新鲜度 — **修复（merge blocker 关闭）**

`task.relevant_paths` 拆分为 `ownership_scopes`（DAG 声明并集）与 `relevant_changed_files`（scope 并集 ∩ `git_touched_files` 经 `classify_paths`）两函数；`record_validation` / `record_review` / 完成门查 3 三处同调后者喂 `compute_change_id`。`change_id.py` 责任保持纯字面文件路径（参数行为中性更名 `relevant_files` 并在 docstring 写明 AF-01 责任分离）。glob/目录/精确/嵌套四种 scope 下 owned 文件增、删、改、改名、集合变化均使标识变化；scope 外改动仍归 ownership 查；`.glm-conductor/` 记账两层剔除。旧名 `relevant_paths` 全仓零命中（`git grep` 退出码 1）。

**活证**：C1 dogfood 中 scope `audit-fix-probe/**` 内文件 `probe.txt` v1→v2 编辑后，Stop 门以 validation stale 拦截（记录 `sha256:1043e45f…` ≠ 当前 `sha256:530fc96d…`）。

### AF-02 visual-reviewer 多模态绑定 — **修复（merge blocker 关闭）**

frontmatter 恢复显式绑定 `model: "account:bigmodel-individual-coding-plan/GLM-5.3-Flash"`（宿主 ListModels 验证可用，与 visual-implementer 同款）；定位段改写为 fail-closed 契约。validator 检查 2 拆分：glm-reviewer 禁 model 字段（继承）/ visual-reviewer 必须 provider 全限定且以 `/GLM-5.3-Flash` 结尾。`test_agent_model_binding.py` 重写为能力意图断言（4 测试）。orchestration SKILL / role-contracts / operations 同步"视觉高保障绑定不可用即 fail closed，绝不退回文本模型"。

**活证**：见 §6 视觉审查者盲测。

### AF-03 quota-resume CLI — **修复**

`runtime/cli.py` 纯追加四子命令薄壳：`quota-wait`（resolver detail 观测 → `enter_waiting_quota`，view 含 status/source/observed_at/reset_at=max 窗口 reset_at）、`quota-resume-authorize`、`quota-resume-decision`（绝不调宿主 Resume）、`quota-resume-confirm`；任务层拒绝映射退出码 1（`_QuotaRejected`），argv/参数值错误退出码 2。continuity 技能两文档改以 CLI 为规范入口。九例 CLI 测试全绿（monkeypatch 零网络）。

### AF-04 写者守卫生命周期 — **修复**

`record_workflow_run` 对 delegate/full：inspect 无持有者 → `ValueError`（missing writer reservation）；他人持有 → 拒绝报持有者；落盘三件事成功后幂等补挂 run id（acquire 竞态冲突 → 拒绝注册）。完成门查 1 新增：`workflow_run_id` 非空且 holder 缺失 → block。solo/audit 零变化（测试锚定）。保证措辞三处收敛为生命周期级（不宣称拦截裸宿主 CreateWorkflow）。

**独立审查披露的已接受边界**：补挂冲突路径存在规格明文接受的 TOCTOU 部分写入窗口（三件已落盘、acquire 冲突抛错），闭环 fail-closed（后续完成门拦截并给出 `writer-acquire` 补回指引）。

### AF-05 钩子启动器 — **验证免改（release blocker 关闭）**

探针（2026-09-21 目标宿主）：
- `python3` → `C:\Users\user\AppData\Local\Microsoft\WindowsApps\python3.exe`，真实 CPython **3.7.9**（非 Store stub；`python3 -c` 正常执行）；`python` → 3.10.11；
- 本会话 SessionStart 钩子（刷新前 2.3.2 缓存脚本）有真实输出（额度 advisory）——启动器在本机钩子进程 PATH 下可执行；
- **决定：`hooks.json` 启动器保持 `python3` 不变**（代码本就 3.7 兼容；validator 锚点 `HOOK_COMMAND="python3"` 不变；未建任何启动器框架）。

**新缓存活证**：见 §5。

### AF-06 reviewer 活证 — **完成（release blocker 关闭）**

文本与视觉两条 reviewer 通道以 Agent 工具真实启动、全新上下文、只读、产出有效裁决；视觉含盲测读图（§6）。严格字面意义的"缓存刷新后新开会话"复验见 §8 已知限制第 1 条。

## 3. 代码变更清单

- `plugins/glm-conductor/runtime/task.py`（AF-01 派生拆分 + AF-04 前置/补挂）
- `plugins/glm-conductor/runtime/change_id.py`（参数更名 + docstring 澄清，行为中性）
- `plugins/glm-conductor/hooks/stop_gate.py`（查 3 新鲜度派生 + 查 1 缺失持有拦截）
- `plugins/glm-conductor/runtime/cli.py`（四条 quota 生命周期子命令）
- `plugins/glm-conductor/agents/visual-reviewer.md`（多模态绑定）
- `scripts/validate_plugin.py`（检查 2 文本/视觉分治锚点）
- `plugins/glm-conductor/skills/`（orchestration SKILL + role-contracts + operations；enforcement SKILL；continuity SKILL + long-horizon）
- `scripts/smoke_plugin_load.py`（锚点随 AF-01 更名）
- 测试：`test_task_lifecycle.py` / `test_completion_guard.py` / `test_change_id.py` / `test_agent_model_binding.py` / `test_writer_guard.py` / `test_cli_v24.py` / `test_quota_resume_v24.py`
- 文档：`architecture.md` / `core-concepts.md` / `troubleshooting.md` / `README.md` / `README.zh-CN.md` / `CHANGELOG.md`（2.4.0 追加审计修复条目）/ `docs/reviews/V2_4_CLOSEOUT_REPORT.md`（确认带 superseded 注记无需再改）/ 四份 roadmap 文档保留原义措辞改写

**审查者披露的完整 AF-04 变更集**（含 sweep commit 内配套项）：上表 task.py / stop_gate.py 两测试文件之外，尚有 `test_quota_resume_v24.py`（delegate 前置先 acquire 适配）、`troubleshooting.md`（拦截现象文案）、`CHANGELOG.md`（AF-04 条目）。

## 4. 定向测试证据

Workflow 内逐单元门（GLM-5.3-Flash 实施 + 门裁决；A1/B2 另经全新上下文只读审查者复审）：

- A1：`tests.test_change_id + test_task_lifecycle + test_completion_guard` 全绿（121 例）；
- A2：`tests.test_agent_model_binding` 全绿 + validator 15/15；
- B1：`tests.test_cli_v24 + test_quota_resume_v24` 全绿（70 例）+ validator 15/15；
- B2：`tests.test_task_lifecycle + test_completion_guard + test_writer_guard` 全绿 + validator 15/15；
- D1：validator 15/15 + smoke 0 失败 + `git grep relevant_paths` 零命中。

**主会话独立复现**（不采信实施者自述）：

- D2 束重跑：`python3 -X utf8 -m unittest`（十个定向模块）→ **Ran 315 tests — OK**；
- `validate_plugin.py` → **15/15**；`smoke_plugin_load.py` → 0 失败；`ruff check plugins/ scripts/ tests/` → All checks passed；`compileall` → 干净；
- A1 审查回归（fixture 的 RUNS_DIR patcher 误入清理方法 → 测试静默污染真实仓库 `.glm-conductor/workflow-runs/`）：修复后主会话以快照对比独立证实——测试运行前后该目录**字节一致**；污染窗口遗留的 fixture 记录 `run-life-cycle-a.json` 已清除。

## 5. 宿主钩子活证（AF-05/AF-06 前半）

**缓存刷新**：`robocopy //MIR`（排除 `__pycache__`）将 `plugins/glm-conductor` 镜像入用户级缓存 `~/.zcode/cli/plugins/cache/glm-conductor/glm-conductor/2.3.2/`（目录名保持 2.3.2、内含 plugin.json version 2.4.0——就地刷新既定程序）；刷新树外备份 `~/.zcode/rh24-cache-backup-2.3.2`；`diff -r` 证缓存与仓库载荷**字节一致**（模两个过渡性空操作桩，见 §8-2）。

**SessionStart**（宿主同形精确调用：`ZCODE_PROJECT_DIR` + 事件 JSON on stdin，执行缓存脚本）：受控未完成任务 `af-c1-live-proof` 下输出单行 `hookSpecificOutput` JSON，`additionalContext` 头部逐字 `GLM CONDUCTOR RESUME CONTEXT`（含 task/goal/status/route/下一安全动作），exit 0。

**Stop 完成门（真实宿主事件，两回合闭环）**：

1. 受控任务 active + 过期验证（probe v1 验证后文件改 v2）→ 主会话结束回合 → **宿主触发 v2.4 stop_gate.py（自刷新缓存）输出 block**：验证过期，记录 `sha256:1043e45f…` ≠ 当前 `sha256:530fc96d…`，理由与账本完全一致；
2. `record_validation` 刷新后再次结束回合 → 四查全过 → **就地完成**：state `status=completed`、journal `task_completed`（from active）、写者守卫归零 `{}`；
3. teardown 后 `git status --porcelain` 干净。

**附加宿主行为证据**：刷新删除了旧缓存 `pre_tool_use.py` 后，本（旧快照）会话的 PreToolUse 钩子立即以 `python3: can't open file …` 失败并阻断 Bash——实证"钩子逐事件从磁盘以 python3 执行"；以空操作桩恢复后 Bash 复通。

## 6. reviewer 活证（AF-06）

**文本审查者（glm-reviewer）**：Agent 工具真实启动（subagent_type `glm-conductor:glm-reviewer`），全新上下文；对 AF-04 提交 `b60f488` 464 行 diff + 六文件全文 + 关联调用方审查（15 次工具调用，纯只读零写入），产出有效 `GLM REVIEW`，**VERDICT: ship**，FINDINGS 精确到文件:行号。三项非阻断发现中 F3（`troubleshooting.md:72` 把守卫强制点误标为 CLI `v24-record-run`，实际是 `runtime.task.record_workflow_run`）已随本报告修正；F1（变更集声明完整性）以 §3 完整清单回应；F2（TOCTOU 部分写入窗口）为规格明文接受边界，已记录于 §2-AF-04。

**视觉审查者（visual-reviewer）**：Agent 工具真实启动（subagent_type `glm-conductor:visual-reviewer`），运行于 `account:bigmodel-individual-coding-plan/GLM-5.3-Flash` 显式绑定：

1. 常规轮：Read 实读 fixture PNG（480×240，纯标准库生成：白底 + 顶行 3 红方块 + 底行左蓝右绿），逐项核对 5 条验收点全过，`VISUAL REVIEW` VERDICT: ship；
2. **盲测轮（决定性）**：新调用不含任何预期答案，仅要求"裸描述你看到的画面"——回报：白背景、**5** 个有色方块（上排左/中/右三红、下排左蓝右绿）、无文字线条、横向 ~2:1。与图像真实内容 100% 吻合（480×240 即 2:1）。纯文本复述提示词不可能给出该描述——多模态实读能力成立，AF-02 能力契约在最终绑定上验证通过。

## 7. 最终全量回归

- Workflow D3（一次）：`python3 -X utf8 -m unittest discover -s tests -q` → **OK**；
- 主会话独立复现（同命令重跑一次）：**Ran 677 tests — OK**（v2.4 战役基线 630 例 + 审计修复新增 47 例）；
- validator 15/15、smoke 0 失败、ruff 全过、compileall 干净（主会话复跑）。

## 8. 剩余已知宿主限制

1. **字面意义的新会话复验**：reviewer 活证在编排会话（缓存刷新前启动）内执行。该会话快照中的 visual-reviewer 定义与修复后定义**绑定字节相同**（均为 `account:bigmodel-individual-coding-plan/GLM-5.3-Flash`），且缓存已与修复后载荷字节一致——新会话必然取得修复后定义；建议合并后由用户开一个新会话做一次 30 秒确认（reviewer 出现在可用类型列表并可启动）。
2. **过渡性空操作桩**：本（旧快照）会话期间，缓存 `hooks/pre_tool_use.py` / `post_tool_use.py` 为空操作桩（v2.3 快照钩子脚本被 v2.4 载荷删除会阻断旧会话工具调用）；仅存在于缓存目录、不入仓库，新会话不引用。
3. **CI 远端门**：push 后由 GitHub Actions 复核（ruff + validator + smoke + unittest，Py3.8/3.13 × ubuntu/windows）；本机已全绿预检。
4. **缓存目录名**：仍为 `2.3.2` 承载 2.4.0 载荷（就地刷新既定程序，目录名只是键）；正式发布面版本推进时按常规流程处理。

## 9. 最终裁决

```
MERGE_READY
```

依据：AF-01–AF-04 全部落地且经定向门 + 独立审查 + 主会话独立复现三重验证；AF-05 启动器探针 + 新缓存真实宿主钩子事件双证关闭（免改结论）；AF-06 两条 reviewer 通道真实启动、只读、有效裁决 + 视觉盲测读图决定性证据；定向束 315 例、全量回归 677 例、validator/smoke/ruff/compileall 全绿；文档保证措辞与实际能力对齐。`review/zcode-3.14-native-workflow` 可作为 v2.4.0 合并候选进入 `main`。

---

# Final Release Closeout Addendum（2026-09-21）

> 本 addendum 按页首 OVERRIDE 注记要求追加，处置 `docs/roadmap/V2_4_FINAL_RELEASE_CLOSEOUT_PLAN.md` 的 FR-01–FR-04，并**取代 §8 第 1 条**（字面新会话复验待补）——该缺口已由下方 §A3 的真新鲜会话双 smoke 关闭。§1–§9 的 AF-01–AF-06 历史证据原样保留。

## A1. FR-01 — 终态收尾在生效仓库根释放写者守卫（merge blocker 关闭）

**发现**：`runtime/task.py::_finish` 以账本根 `repo_root` 调 `writer_guard.release`，而 touched-file 发现、change_id 求值、委派 run 守卫检查、完成门查 1 均在 `state.resolve_repository_root` 生效根上求值——账本根 ≠ 绑定 `repository.root` 时，complete/fail/cancel 会在账本根下释放不存在的守卫，真守卫滞留绑定仓库根（违反单活跃写者不变量）。

**修复**（commit `28fe26c`）：`_finish` 载入状态后经 `state.resolve_repository_root(st, repo_root)` 解析生效根再释放；state/journal 恒留账本根；不加账本根兜底第二次释放；`_finish`/模块 docstring 双根分工同步更正。三条终态路径（complete/fail/cancel）共享 `_finish`，一处修复全覆盖。

**回归**：新增双物理目录夹具（`<tmp>/ledger` 账本根 + `<tmp>/repo` 绑定仓库根，经正门 `create_task` + `bind_repository_root` 构造，绝不 mock release）：

- complete / fail / cancel 三终态：守卫从绑定根消失、账本根零守卫文件、state/journal 留账本根（`tests/test_task_lifecycle.py::TestTerminalTwoRootRelease`）；
- 非属主持有：终态照常到达、他人守卫绝不误删；
- 同终态重放（双根）：释放仍指向绑定根；
- 完成门决定性集成：账本根 A / 绑定根 B 分离下四查全过 → allow + completed + 守卫从 B 释放 + A 零守卫（`tests/test_completion_guard.py::TestTwoRootCompletion`）。

**红绿验证**：`git stash` 临时还原修复 → 5 个新用例全红（守卫滞留绑定根，复现病灶）；恢复修复 → 定向门三模块（task_lifecycle + completion_guard + writer_guard）**121 例全绿** + ruff 全过。

## A2. FR-03 — 发布面真相同步

六面统一于本 addendum 定稿时点的最终状态：README.md / README.zh-CN.md 的 reviewer 证明措辞由"待补"改为"已实证"（保留 App 关闭定时触发未实证与 fail-closed 语义两条真实限制）；CHANGELOG 2.4.0 关闭 AF-05/AF-06、新增 FR-01 条目、迁移注撤回 pending 措辞；`docs/README.md` 与主实施计划状态随 F3 终门联动收口（见 §A4）。

## A3. FR-02 — 真新鲜会话 reviewer 双 smoke（release gate 关闭）

**缓存与宿主记录**（F2.1）：

- 插件安装记录 `glm-conductor@glm-conductor` → `installedPath` = `C:\Users\user\.zcode\cli\plugins\cache\glm-conductor\glm-conductor\2.3.2`（目录名 2.3.2 承载 2.4.0 载荷，就地刷新既定程序）；`plugin.json` version = **2.4.0**；
- F1 落地后无清除复制刷新：1 文件更新（`runtime/task.py`）、42 文件一致、2 个过渡空操作桩按规格"验证不引用"分支保留（缓存 `hooks.json` 已为 v2.4 面，仅 SessionStart+Stop，新会话快照不引用桩）；`task.py` 仓库↔缓存 sha256 一致（`5940d738…`）；
- 被测仓库提交：`28fe26c`；宿主 ZCode 3.14.x（updater 显示 3.14.1 待装）；agents 声明绑定：`visual-reviewer` = `account:bigmodel-individual-coding-plan/GLM-5.3-Flash`（thoughtLevel max、只读工具面）、`glm-reviewer` 无固定 model 字段（继承宿主/会话模型）。

**新鲜会话**（`sess_c0dbf41b`，2026-09-21 18:19 +0800 启动，晚于缓存刷新，非实施会话）：

- **Smoke A（glm-reviewer）**：真实启动、只读（仅 1 次指定 fixture 读取）、输出遵循 `GLM REVIEW` 契约，裁决 fix-first（对受控 diff 给出实质发现：新函数零测试覆盖、非 dict 记录路径可抛 AttributeError 等）——文本审查通道在新会话内有效。
- **Smoke B（visual-reviewer，盲测）**：真实启动于声明的多模态 Flash 绑定、只读（仅 1 次读图）、输出遵循 `VISUAL REVIEW` 契约，裁决 ship。派发指令仅含图片路径与"裸描述"要求，**零内容提示**；对图 `fr02_blind.png`（为本轮新造，构图与 AF-06 旧图不同，且新会话被禁读真值持有文件与 docs/reviews/**）回报：纯黑背景横幅、左侧偏中大实心黄色圆形且底部与横条相切、底部青色水平长条横贯大部分宽度、右上不贴边亮品红正方形、无文字/渐变/阴影——与程序化锁定的真值（360×180 黑底；黄圆心 (100,90) r=62、底缘 y=152 与条顶 y=150 相切；品红方块 60px 边长右缘距边 30px；青条 x∈[40,320) 跨 280/360）**逐项吻合**。纯文本复述提示词不可能产出该描述——多模态实读能力在真新鲜会话内成立。

**判定**：FR-02 原始门（刷新缓存 + 真新会话 + reviewer 启动，视觉须真实读图）以未弱化的字面形态关闭。

## A4. FR-04 — 终门证据与最终裁决

**定向束（F3.1）**：`tests.test_task_lifecycle` + `tests.test_completion_guard` + `tests.test_writer_guard` + `tests.test_agent_model_binding` → **125 例 OK**。

**静态门（F3.2）**：`scripts/validate_plugin.py` → **15/15 通过**；`scripts/smoke_plugin_load.py` → **0 失败**；`ruff check plugins scripts tests` → **全过**；`compileall`（runtime + hooks + scripts）→ **干净**。

**唯一一次全量回归（F3.3，commit `65f45ef` 树上）**：`python3 -X utf8 -m unittest discover -s tests -q` → **Ran 682 tests — OK**（审计修复基线 677 + FR-01 新增 5 例双根回归；本阶段零修复零重跑）。此后变更仅为本 addendum 与状态文档（纯文档，不触代码与测试）。

**最终分支头与远端**：本 addendum 定稿提交见 git log（FR 收口 commit 序列 `28fe26c` → `65f45ef` → 终稿）；push 后 GitHub Actions 按 §8 第 3 条既有矩阵（ruff + validator + smoke + unittest，Py3.8/3.13 × ubuntu/windows）复核，结果记录于本节末尾的远端补记。

## A5. 最终裁决

```
MERGE_READY
```

依据：FR-01 修复经红绿验证与双物理目录全终态回归（含完成门 A/B 两根决定性集成）；FR-02 以未弱化字面形态在真新鲜会话关闭（双角色启动、只读、契约遵循、视觉真实盲读图且与真值吻合）；FR-03 六面发布文档收敛一致；FR-04 定向束 125 + validator 15/15 + smoke + ruff + compileall + 唯一一次全量回归 682 全绿。本裁决**取代页首 OVERRIDE 的暂时覆盖**，恢复并重申 §9 的合并候选结论：`review/zcode-3.14-native-workflow` 可作为 v2.4.0 合并候选进入 `main`。

**远端 CI 补记**：push（`7b734b1..572735f`）触发 GitHub Actions run `35588939915`（validate-plugin，commit `572735f`）→ **success**：四路全绿——Static plugin validation × {ubuntu-latest, windows-latest} × {Python 3.8, Python 3.13}。FR-04 远端门关闭。
