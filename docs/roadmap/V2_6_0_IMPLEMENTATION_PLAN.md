# v2.6.0 实施计划（Reliability Fix & Model Selection Rebaseline）

- **状态**：IMPLEMENTED — Phase A-E 已全部实施（Phase A `c299425` / Phase B `6751a7a` / Phase C `5925488` / Phase D `ee5d841` / Phase E 为 v2.6.0 发布提交（本分支最后一个 feat 提交））；统一 push + tag + Release 由主会话收尾（owner 裁决：不补历史 tags）
- **日期**：2026-10-14
- **基线**：main @ 7c95ba0（v2.5.2 + 规格文档）
- **规格权威**：docs/roadmap/POST_V2_5_2_RELIABILITY_AND_MODEL_SELECTION_IMPLEMENTATION_SPEC.md（owner 对齐设计）；本计划为其执行细化，含 owner 讨论裁决（2026-10-13/14）与本机宿主实测事实
- **输入**：.local-dev/review-2026-10-13-post-v2.5.2.md（审查报告，P1-1/P1-2/P1-3/P2-1/P2-2/P3×7）
- **实施者**：ZCode 主会话（规格 §8 的 Codex 假设按 owner 指示忽略）；宿主操作归主会话，纯 runtime 不碰宿主工具

## 0. Owner 裁决清单（全部已锁定）

1. **单 reset_at 无条件透传**：对外只有一个 reset_at（provider 观测的有效下一窗口复位时刻），AVAILABLE/PRESSURE/EXHAUSTED/UNKNOWN 任何状态只要观测有效就透传；额度状态只管可否继续/恢复，不管可否透传时间。启动授权成立即同批建双通道（recurring 地板默认 1h + 后台 sleep 到 reset_at+300s），先武装后执行；绝不依赖额度耗尽后模型轮建立首个 sleep（规格 §0 决策 1-3）。
2. **窗口选择规则**：精度睡眠锚点 = 已识别窗口中**最早的有效未来观测 reset_at**（UTC instant 显式解析比较，不靠字典序巧合），与窗口状态无关；醒来重观测重续臂，迭代收敛；plan_resume 的 EXHAUSTED 保守口径保留、不再控制透传/武装（规格 §2.2）。
3. **视觉角色维持静态绑定** GLM-5.3-Flash（`account:bigmodel-individual-coding-plan/GLM-5.3-Flash`）；在 agents 说明文档增加绑定维护声明（含"改 frontmatter 新会话生效"的宿主事实与新增可选子智能体的扩展点）。不做动态绑定探针、不做运行时改 frontmatter。
4. **per-role `agent_models` map 裁掉**（owner 2026-10-14 A 项裁决）：worker 用现有 worker_model pin，视觉静态绑定，glm-reviewer 无绑定（AF-02 契约不变）。
5. **候选范围**：全部宿主可用模型、不限于 bigmodel（owner B 项裁决）。**ListModels 的 `levels` 是思考档位（disabled/low/high/max），disabled 档=关闭推理选项，不影响可用性**（owner 确认 + 2026-10-14 CreateWorkflow subagent_model 实测：opencode-go-responses/deepseek-v4.1-flash 于 $max 档正常回答）。候选判定=ListModels 全行入候选；仅出现真正的行级禁用标注才排除并与用户确认。本机当前 12 行全可用。
6. **问询轮必须提示**：选用的子代理（工作流）模型**默认以 max 推理档运行**（宿主 default max）；提示文案进 orchestration §6.0 第二问展示与 role-contracts（级别不进 pin，pin 只存模型 id，默认 max）。
7. **发布形态**：v2.6.0（minor）；**不补历史 tags/Releases**；实施期 feature 分支、逐 Phase 本地 commit 不 push、全部完成统一 push + tag + Release + 缓存刷新。
8. **流程纪律**：每 Phase 书面计划已备（本档）→ 实施 → 定向测试 + 本机全量（tests/ 在盘，827 为历史基线）→ 独立审查（glm-reviewer）→ 下一 Phase；未跑的宿主探针如实标 UNVERIFIED，绝不虚报。

## 1. Phase A — WP-R1 状态事务化（P1-1）

**改动**：`runtime/state.py`（核心）、`runtime/task.py`（迁移全部变更入口）、必要时 `runtime/durable_io.py`（只加严格 opt-in 助手，不改既有语义）。

**方案**（规格 §1 + 实施者补充）：
- 新增 per-task 事务原语：对目标 `state.json` 先取 durable_io 独占锁 → 锁内严格重读当前字节（missing 仅在显式允许处、malformed/schema 错即拒）→ 任务身份+终态/转换校验 → 对**新鲜 state** 施加操作专属变更 → 校验终态 → atomic_write_json 原子替换 → 释放。
- **锁内零 I/O 原则（实施者补充，规格未明写）**：git 观察等慢操作全部移到锁外（锁外取快照 → 锁内基于新鲜 state 应用）；锁内只做纯内存校验+变更+写，避免数秒级锁持有串行阻塞全部写者。
- 不裸用 `atomic_update_json`（其 atomic_read_json 把损坏当默认，会破坏"损坏不被覆盖"不变式）——按规格警告执行。
- 迁移全部变更入口：create/transition/reopen/phase/validation/review/run 注册/quota authorize·wait·resume confirm·计数/automation bind·clear/worker pin/终态三命令。过时整态 dict 绝不覆盖无关新字段。
- 保留：同终态幂等、completed/cancelled/failed 不静默重开（重开必经 reopen_task 且走同一锁、发 task_reopened）、journal 仅在状态提交成功后追加、事件名不变；诚实文档化"状态-账本跨文件非 ACID"。

**验收门**：并发写者更新独立字段双方保留；并发 complete vs validation 不复活终态；并发 resume confirm 不丢预算增量；损坏被拒不被覆盖；同态重放幂等。新增定向测试（并发/损坏/幂等矩阵），ruff + 3.7 兼容扫描过。

## 2. Phase B — WP-R2 预防性武装（P1-3）

**改动**：`runtime/cli.py`（`_quota_observation_view`/`_quota_resume_decision`/quota-wait view）、必要时 `runtime/quota/parser.py` 小助手；`skills/continuity/SKILL.md` + `references/long-horizon.md`、`skills/orchestration/SKILL.md`（§6.0/§6.2 runbook）、`commands/quota.md`；validator 锚点同步。

**行为**：
- view 去 EXHAUSTED 过滤与 max-of-blocking 计算：全量 snapshot 评估（保留 status/source/observed_at 四键），按裁决 2 提取单一**最早未来观测 reset_at**（显式 UTC instant 解析；已过期/无效/陈旧 → 强制刷新一次 → 仍无则 reset_at=null 并如实披露 floor-only 降级，绝不虚构）。
- `_quota_resume_decision` 对**所有 action**（no-op/remain-waiting/waiting-user/resume-authorized）无条件附加 `decision["reset_at"]`；既有键与副作用逐字不动。启动序列复用本命令（--force-refresh）取观测——已活体验证 active+无 run 任务走 no-op 返回（2026-10-14 临时 repo 探针）。
- 不动 task.py 可用性/恢复状态机；不动 parser.plan_resume 的 EXHAUSTED 保守 resume_at 规则。
- 技能面：§2.4 启动 runbook（授权→task-create→authorize→观测 reset_at→CronCreate 地板→bind→preflight→发首个精度睡眠→pin→compile/select→writer-acquire→CreateWorkflow→canonical run 注册）与 §2.5 唤醒/停靠/恢复协议（两通道同协议、收尾必续臂、停靠轮只核查补发、重启由地板自愈、无人值守绝不弹问）；成功陈述三分（授权/地板绑定/睡眠实际发出），floor-only 是可见降级态非"已武装"。

**验收门**：规格 §2.6 十条回归测试全绿（AVAILABLE/PRESSURE/EXHAUSTED/UNKNOWN 透传、双窗选择、全过期兜底、静默耗尽、重启恢复、重复唤醒不双 confirm、无新字段/无第二调度器）；validator 锚更新后全过。

## 3. Phase C — WP-R3+R4 守卫与 run 注册（P1-2/P2-1/P2-2）

**改动**：`hooks/stop_gate.py`、`runtime/change_id.py`、`runtime/ownership.py`、`runtime/task.py`、`runtime/workflow/adapter.py`、`runtime/cli.py`、orchestration runbook。

- R3：stop_gate 两处降级 warn_stderr 统一加 `ENFORCEMENT DEGRADED` 前缀（与 session_start 一致、兑现 SKILL/troubleshooting 契约）；守卫整体 git 观察预算 = 共享单调截止期，总额 ≤ hooks.json timeoutMs=5000（含进程启动/读取/报错/余量）——安全处复用同一次 git status/base 结果，禁止 3s+3s+3s 串行叠加；env 单次 git 超时不得超剩余总预算；git 挂起时在宿主硬杀**之前**输出可见降级警告（受控假子进程测试验证）。
- R4：新增 `task-record-workflow-run <repo_root> <task_id> <run-id>` 为规范路径（写 state.workflow_run_id + AF-04 写者守卫前置检查 + workflow_started 事件 + run-id 联动）；adapter record/load 接受显式 repo_root/base_dir（缺省兼容旧行为），单据落账本 repo_root 而非 CWD；v24-record-run 降级为文档标注的 legacy 低层薄壳；守卫前置失败绝不落假 workflow_started。orchestration runbook 改调规范命令。

**验收门**：假慢 git 在预算内返回、exit 0、报文含正确降级短语、无误 block；正常 block 仍单行 JSON；任意 CWD 调规范命令成功、无/失配 reservation 拒绝且无假成功。定向测试过。

## 4. Phase D — WP-M1 模型选择重基线

**改动**：`runtime/workflow/submission.py`、`runtime/task.py`、`runtime/cli.py`（worker-model-pin/模型选型）、`skills/orchestration/SKILL.md` + `references/role-contracts.md`、`agents/visual-implementer.md`/`agents/visual-reviewer.md`（维护声明）、README 双语；测试+validator 锚同步。

- **主模型**：删"主会话必须 GLM-5.3"硬门与版本能力断言；主模型继承宿主主会话、不可编程切换；问询轮软性建议强模型、不阻塞其他配置选择。
- **发现与展示**：问询前主会话活体调 ListModels（宿主动作，runtime 不碰）；全部行入候选（levels=思考档位原样展示、disabled 档不影响可用性——裁决 5）；跨 provider 候选合法（本机含 deepseek 系）；**展示文案必含"子代理（工作流）模型默认以 max 推理档运行"提示（裁决 6）**；零合适候选→解释并阻塞该角色，绝不静默继承主模型或挑别家。
- **单轮问答**（三问族保持）：①跨额度唤醒+预算（启用时）②主模型建议+全部子代理角色模型逐一显式确认（单一候选也确认；8+ 候选时文本列全清单 + AskUserQuestion 收敛选项+Other 自由输入精确 ID，不拆轮）③地板间隔（默认 1h）。solo 任务不问无关 worker 选择。
- **pin 语义**：worker-model-pin 放宽为角色中性精确 ID 校验（非 Flash 合法、未来陌生名字但已配置的 ID 合法；未配置/裸别名/空无效拒绝）；submission model_policy 从 explicit-flash-required 改 user-authorized-explicit-selection（CLI 契约/测试同改）；有 pin 任务普通 --model 不得绕过，换 pin 必经交互式重授权（新旧值可见）；无人值守唤醒轮绝不自动重 pin/静默换模型；resume 时 pin 缺失（legacy）不隐式授权单候选——需交互重授权；旧 v2.4/v2.5 状态可加载、在途工作不强迁。pin 是交互契约下的决策证据，非密码学证明（不过度声明）。
- **Workflow/Agent 边界**：CreateWorkflow 单 subagent_model/run（已实测跨 provider 路由生效）；生成 TS+persona 保持模型无关、不加节点级绑定；视觉角色按裁决 3 静态绑定+维护声明（frontmatter 全限定写法、裸 ID 静默回退坑、改绑新会话生效、新增 agents/*.md 即扩展）；读图不可得=该角色 fail-closed，绝不降级为文字猜测。
- **测试**（规格 §5.5 全套）：新旧 Flash+非 Flash 全展示、软推荐、owner 选非 Flash 被接受；单候选不静默指派；未配置/裸别名拒绝、陌生未来 ID 接受；pin 跨唤醒/恢复存活、失效 fail-closed、--model 不绕过；无 GLM-5.3 硬依赖残留于运行时政策/SKILL 硬门/静态 agent 选型；3.7 语法+单行 JSON 输出保持。

## 5. Phase E — WP-C P3 卫生 + 发布面

七项 P3（规格 §6 全清单）：①CLI 错误分类（损坏 state/schema=结构性 vs 生命周期拒绝，usage/docstring/实际映射三者一致并测试）②exit-waiting 文档 vs waiting_user 转换矩阵对齐（改文档或显式白名单，不静默改语义）③compiler node_id 经 `_comment_text` 折叠（注释注入面关闭、确定性保持）④window_math.py 死代码删除或标 retired（防 min 口径误接线）⑤_http 捕获元组补 `http.client.HTTPException` ⑥hooks.json description 与编译产物头注释去 "v2.4" 自版本（版本无关表述）⑦_USER_AGENT 版本同步或稳定版本无关标签。

发布面：CHANGELOG v2.6.0、README/README.zh-CN、plugin.json/marketplace.json=2.6.0、architecture/core-concepts/troubleshooting 同步双通道与模型政策新契约。

## 6. 测试与验收总门（每 Phase 必过 + 终门）

- 每 Phase：定向测试（新增/改造）+ ruff（改动文件）+ 3.7 兼容扫描 + 本机全量 unittest（`PYTHONUTF8=1 python3 -m unittest discover -s tests -t .`，当前基线 827 OK）+ validator（锚点随改动更新，不以 16/16 数字为教条）。
- 终门：全量回归 + validator 全过 + `scripts/smoke_plugin_load.py` + 独立审查（glm-reviewer，ship 裁决）+ diff 复查（秘密/身份泄漏/越权模型覆盖/静默状态回退/陈旧模型名/绕过路径）。
- **宿主探针台账**（已验证 ✓ / 待验 / UNVERIFIED 纪律）：
  - ✓ ListModels 形态与 12 行全可用（2026-10-14，disabled=思考档位确认）
  - ✓ CreateWorkflow subagent_model 跨 provider 真实路由（deepseek 探针 $max 正常回答）
  - ✓ quota-resume-decision 对 active+无 run 任务 no-op 返回（临时 repo 探针）
  - 待验（Phase B 后，专用小会话，不占本主会话 automation 名额）：Cron 地板创建+bind+preflight 全链、后台 sleep 唤醒精度
  - 视觉绑定现状即 frontmatter 静态事实（visual-reviewer 有使用史），不做动态绑定探针
- 发布链（全部 Phase 通过后）：缓存刷新（robocopy //MIR 镜像至 2.6.0）→ 统一 push（gh api 绕 SSH 断连）→ tag v2.6.0 → GitHub Release Latest → 登记更新。**不补历史 tags**。

## 7. 分支与提交纪律

- feature 分支 `v2.6.0-reliability-model-rebaseline`（自 main@7c95ba0）；本计划文档随首个提交进分支。
- 逐 Phase 小提交（含精确测试结果）；实施期不 push；全部完成+终门过→统一 push。
- 不覆盖历史 tag、不在起草期 bump 版本号（版本号变更只在 Phase E 发布面）。
- 每 Phase 完成即向 owner 报告：改动文件/提交/测试计数/宿主验证/已知 blocker。

## 8. No-go 红线（承规格 §7.2，逐字有效）

AVAILABLE 不透传 reset_at 或有有效新观测时首睡眠未先于实施建立；仅 EXHAUSTED 许透传或状态决定武装；引入第二个公开精度 reset 字段/状态机；并发保存覆盖 validation/额度/pin 或静默重开终态、损坏 JSON 被默认值替换；任何委托模型未经 owner 问询自动选定、非 Flash 已配置角色可用模型仅因名字/后缀被拒；pin 缺失被绕过或 resume 中已配置 ID 变化未经用户授权；未实际绑定读图能力就声称视觉验证；宿主 cron/恢复靠虚构命令/API、或依赖静默耗尽后的模型轮建首个 sleep。
