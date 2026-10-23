# v2.7.0 统一实施规格：唤醒载体降级 + 免确认直通 + 编排粒度/并行 + 直派角色 + 模型准入复核

状态：IMPLEMENTING（B0 spikes 已收官，结论 `docs/reviews/V270_B0_SPIKE_RESULTS.md`；两处改案已并入 §3.3/§4.3）
日期：2026-10-24（B0 改案 2026-10-24 同日）
取代：`V2_6_3_WAKE_CARRIER_FALLBACK_AND_SESSION_COMPAT_PLAN.md`（其全部内容并入本规格并按终裁更新）
基线：main = 40b7de9（v2.6.2），982 tests，validator 16/16
版本目标：**2.7.0**（单版本承载全部，owner D12 终裁）

---

## 1. 裁决快照（D1–D13 全锁定 + W7 指令）

| # | 裁决 | 值 |
| --- | --- | --- |
| D1 | CreateWorkflow 免确认授权面 | **并入 launch interview 单轮问询**（见 §4）：问一次全事项，任务级授权落 state，后续自主无阻塞 |
| D2 | carrier 见证形态 | `quota_resume` 第五键 `automation_kind`，缺省 native 向后兼容 |
| D3 | sleep_only 层 | 允许武装 auto，诚实降级标签 |
| D4 | OS 载体范围 | 只做 floor 节拍；精度层沿用后台 Bash sleep；headless 一次性定时不做 |
| D5 | SessionStart matcher | 补 `resume` 源 |
| D6 | OS 载体命令落点 | 入 runtime CLI（`os-carrier-install/remove`，subprocess 精确 argv） |
| D7 | 红线修订 | "零宿主工具调用；OS 级计划任务经显式 opt-in 子命令例外" |
| D8 | 拆解判据落点 | 纯 skill 指南（compile advisory 后置观察） |
| D9 | 侦察代理裁决 | 推翻"不新增自定义侦察代理"，引入 conductor-scout（成本动机） |
| D10 | 实施载体裁决 | 修订"唯一载体"：开放 delegate 单节点直派 impl 角色，契约显式写明代价与恢复语义 |
| D11 | 角色清单 | 四角色全上（scout / runner / impl-flash / impl） |
| D12 | 版本编排 | 单版本 **2.7.0** 承载全部 |
| D13 | 事件契约 | TASK_JOURNAL_EVENTS 十名不动，直派不新增事件 |
| W7 | 模型准入（owner 2026-10-24 指令） | "可见列表模型都可选、不得只接受 account:" ——**经代码核验 main 已满足**（v2.6.2 `_reject_reason` 接受任意 `<provider>/<model>`，2026-10-19 裁定在案）；owner 所引错误文案为 2.6.1 及更早行为 → W7 定为复核收口 + 环境同步（§9），非代码移除项 |

---

## 2. 范围总览

| 工作流 | 主题 | 类型 | 批次 |
| --- | --- | --- | --- |
| W1 | 唤醒载体四级降级矩阵（native/os_timer/sleep_only/unarmed） | runtime + skill | B4 / B7 |
| W2 | CreateWorkflow 免确认（interview 授权直通 + PreToolUse 钩子） | hooks + runtime + skill | B2 |
| W3 | SessionStart matcher 补 resume | hooks 一行 | B3 |
| W4 | 拆解粒度判据（防巨石/防碎片双侧红旗） | skill 指南 | B6 |
| W5 | 并行优先 authoring 三原则 | skill 指南 | B6 |
| W6 | 直派角色四件套 + 直派通道 | agents + skill | B5 |
| W7 | 模型准入复核收口 + 环境同步 | 验证 + 文档 | B1 |

宿主事实依据（2026-10-23 实测 + 2026-10-24 B0 spikes，全文见 `docs/reviews/V270_B0_SPIKE_RESULTS.md`）：PermissionRequest 钩子 `decision.allow` 在 ask 请求层竞速应答（UI 卡 vs 钩子先到先得）；PreToolUse allow 被 CreateWorkflow `alwaysAsk:true` 显式免疫（机制一死路）；zcode CLI 0.17.0 `--resume sess_xxx -p` headless 且默认 yolo、ask 层自动放行；**`--workflow-mode` headless 下必须 alwaysOn（onDemand 不注册工作流工具）**；会话库单一 `~/.zcode/cli/db.sqlite`，CLI 与 Desktop 并发读写无互锁（P-2 实证）；`${CLAUDE_SESSION_ID}` 模板变量可用。

---

## 3. W1 —— 唤醒载体四级矩阵

（设计全文沿用被取代计划 §3，要点与增量如下；D2/D3/D4/D6/D7 已按裁决固化）

3.1 矩阵：

| 级 | automation_kind | floor | precision | 存活 | 降级标签 |
| --- | --- | --- | --- | --- | --- |
| L0 | native（现状） | CronCreate（INV-CONT-03 不变） | 后台 sleep | 跨应用重启 | 无 |
| L1 | os_timer | schtasks/crontab → headless CLI resume 唤醒轮 | 后台 sleep | 跨重启 | floor-on-OS |
| L2 | sleep_only | 无 | 后台 sleep | 不跨应用重启 | sleep-only |
| L3 | unarmed | 无 | 无 | —— | 现状 fail-closed |

判定顺序固化进 runbook（模型零判断）：有 CronCreate→L0；否则 `wake-carrier-probe` → CLI+调度器可用→L1；仅后台 Bash→L2；否则 L3。

3.2 state 变更：`quota_resume` 四键 → 五键（`automation_kind`，缺省 native）；bind 增可选 `kind`（kind 不一致同拒）；preflight 返回值增 kind（ready 语义不变）；clear 同步清 kind；`continuity-unarmed` 镜像闸不变。L2 见证 id = `sleep-only`；L1 = `os:schtasks:glm-conductor-<task-id>` 形态。

3.3 OS 载体（L1）：唤醒命令 `node <zcode.cjs> --resume <sess_id> --cwd <repo> --workflow-mode alwaysOn -p "<统一唤醒协议模板>"`（B0 P-1 实测：headless 下 onDemand 不注册工作流工具，必须 alwaysOn；headless ask 层自动放行，唤醒轮零权限摩擦——护栏=唤醒提示词紧约束）；session id 由 SessionStart 钩子落 `.glm-conductor/session-witness.json`（`${CLAUDE_SESSION_ID}`，fail-open 同口径）；floor 创建/删除走 `cli.py os-carrier-install / os-carrier-remove`（`runtime/carrier.py`，精确 argv，不拼 shell）；间隔沿用 interview Q3 值、终身不动；终局清理 L1 任务以 `os-carrier-remove` 替代原生删除尝试（OS 删除真实有效，无三态劣化）。

3.4 探针：`cli.py wake-carrier-probe <repo_root>`（纯本地零写）返回 `{platform, zcode_cli, scheduler, recommendation}`；zcode CLI 探测序 = PATH `zcode` → Windows 默认装位 `zcode.cjs` → `node`+cjs 组合；调度器只探测可执行性不建探针任务。

3.5 文档同步面（B7 一次收口）：continuity SKILL（dual-channel 段+launch runbook+unarmed 重建按 kind+terminal cleanup L1 分支+Failure Handling 首条改降级矩阵）、long-horizon.md（headless 命令形态+生命周期）、orchestration SKILL（Q3 文案+auto 序列图）、三外档+README 双语、validator check_7/16 锚点。

3.6 测试：state 五键/缺省兼容/bind kind 幂等与冲突/preflight 返回 kind/clear 清 kind；carrier argv 纯构造测试（不真建）；probe 假 PATH 阶梯测试；重点定向回归 test_quota_resume_v24 / test_guard_budget / test_cli_v24。

---

## 4. W2 —— CreateWorkflow 免确认（D1 终裁形态）

4.1 授权面 = launch interview 单轮第 4 问（AskUserQuestion 单轮上限恰 4 问）：

- Q1 跨额度自动唤醒（enable + 预算 / not）——不变；
- Q2 worker 模型——不变；
- Q3 floor 间隔（Q1 enable 时）——不变；
- **Q4（新增，仅 delegate/full 有 CreateWorkflow 的任务问）："本任务工作流提交免确认（自动批准 path 型提交）：enable / not"**，软推荐 enable。
- 纪律不变：单轮一次问全（4 问合一），wake/resume 轮永不问。

4.2 持久化：`task-create ... [--auto-approve-workflow]` → state 顶层 `auto_approve_workflow: true`（缺省无此键=false；schema authority `runtime/state.py`；task 层透传，"问后三独立写"扩为四写）。任务终态后钩子自然失权（只扫非终态任务）。

4.3 钩子：`hooks/permission_request_workflow.py` + hooks.json 增 `PermissionRequest`（matcher `CreateWorkflow`，process 型 python3，timeoutMs 5000）。**机制（B0 P-1 改案：PreToolUse 被 alwaysAsk 免疫，改用 PermissionRequest 请求层应答）**。逻辑（全路径 fail-open，只放行或弃权、绝不阻断）：

1. stdin 解析失败/形状不符 → stdout 空（弃权，默认确认行为）；
2. `input.path` 不存在（inline script 提交）→ 弃权（人工确认保留）；
3. path 解析不在 `<repo_root>/gen/` 或 `<repo_root>/.zcode/workflow-drafts/` 下 → 弃权；
4. 扫 `.glm-conductor/tasks/*/state.json`：存在**非终态**且 `auto_approve_workflow: true` 的任务 → 输出严格 schema（**只答本请求，不带 permissionUpdates**——钩子给的 updates 会持久化 session/project store，造成跨会话无授权状态的过度授权；逐请求现查任务态为零持久化设计）：
   `{"hookSpecificOutput":{"hookEventName":"PermissionRequest","decision":{"behavior":"allow","message":"glm-conductor task-level auto-approve (interview-authorized, path-type submission)"}},"suppressOutput":true}`；
5. 无授权任务/任何内部异常 → stdout 空 + stderr 一行诊断，exit 0。

repo_root 口径：`ZCODE_PROJECT_DIR` 优先，回退 cwd（与 session_start/stop_gate 同源）。多任务仓库语义诚实写文档：任一非终态已授权任务存活期间，该仓库 path 型提交均免确认（任务级授权、仓库级生效窗口）。

4.4 spike P-1 已收官（结论 `docs/reviews/V270_B0_SPIKE_RESULTS.md`）：机制一（PreToolUse allow）被 `alwaysAsk:true` 显式免疫（源码铁证）；机制二（PermissionRequest decision.allow）架构位成立（ask 层竞速应答）；headless 活体差分不可判（headless ask 自动放行，两臂均过）→ Desktop 双臂活体验证归 B2 实施验收（授权任务 path 提交无卡 / inline 仍有卡 / 未授权任务仍有卡）。

4.5 附带修复（写进 CHANGELOG 卖点）：unattended 唤醒轮"run 已死→重建→重交"路径的授权死锁在已授权任务上消除。

4.6 runbook 同步：CreateWorkflow 提交一律 `path:` 引用 v24-compile 落盘文件，禁 inline `script:`（收口 heredoc 转义雷区；钩子放行判据单一；typecheck 重试免重贴）。orchestration 'Launch details' + long-horizon.md 各一句。

4.7 测试：钩子单测（缺省弃权/path 命中放行/inline 弃权/越界路径弃权/settings 损坏 fail-open/输出严格 schema 无多余键/decision-only 无 permissionUpdates）；state 键单测；task-create 旗标 CLI 测试；validator check_13 面（新钩子入 manifest 校验）；实施期 Desktop 双臂活体验证一次（授权后首跑无卡、inline 仍有卡、未授权任务仍有卡）。

---

## 5. W3 —— SessionStart matcher 补 resume

`hooks.json` matcher `startup|clear|compact` → `startup|resume|clear|compact`；session_start.py 逻辑零改动（stdin 源值本不消费），模块注释同步一句；troubleshooting 增一条（应用重启/会话恢复后恢复注入到位）。spike P-3 实证 Desktop 重开确实以 resume 源触发。

---

## 6. W4 —— 拆解粒度判据（skill 指南，D8）

落点：orchestration SKILL 'Static nodes' authoring 纪律段增"粒度判据"小节：

- **默认整块**：有界任务默认 1 节点；命中过大红旗才拆；拆后每个子节点独立通过"完整交付物"测试；
- **过大红旗（必拆）**：objective 含多个 and 连接独立交付物 / ownership 跨 ≥2 子系统边界 / local_check 需多套异质验证体系 / 五段规格单 prompt 装不下需摘要压缩；
- **过碎红旗（必并）**：两节点 ownership 高度重叠（plan_stages 必然串行化，杀死并行度）/ objective 只能陈述步骤 / 合并后仍是完整有界目标；
- **路由层先消解**：整体任务不大 → Axis A 判 low 或 solo（不进 DAG）；配合 W6 直派通道后"为进 workflow 而拆"的动机归零。

compile advisory（节点数/层深/串行组数非阻断警告）**后置观察项**，本版不做。

## 7. W5 —— 并行优先三原则（skill 指南）

落点：同节增"并行度"小节（plan_stages 算法零改动）：

1. **依赖最小化**：依赖只表达真实顺序事实（接口交接、消费产出物）；零依赖节点必须真的零依赖；
2. **ownership 划分即并行度旋钮**：想并行的两块工作 authoring 时划出不重叠 ownership（重叠=by design 串行化写互斥）；跨子系统大任务按接口边界拆，天然并行；
3. **stages 摘要当反馈循环**：v24-compile 输出 `{ok, stages, nodes, out}`，串行链长而依赖无事实依据 → 回炉重写 DAG 再编译。

validator：W4/W5 新锚点入 check_7/orchestration 锚点集（随批同步）。

---

## 8. W6 —— 直派角色四件套 + 直派通道

8.1 新 agents（`plugins/glm-conductor/agents/`，frontmatter 与现役三角色同规范）：

| 文件 | model | tools | 契约要点 |
| --- | --- | --- | --- |
| `conductor-scout.md` | `account:bigmodel-individual-coding-plan/GLM-5.3-Flash` | Read, Glob, Grep, LS, NotebookRead | 只读调查/事实收集；结论必带 file:line 证据引用；不臆造；范围有界作答 |
| `conductor-runner.md` | 同上 Flash pinned | Bash, Read, Glob, TaskOutput, TaskStop | 逐字执行受托命令；诚实回报 exit code+输出尾部；绝不改仓库文件；绝不重试破坏性命令；输出无秘密 |
| `conductor-impl-flash.md` | 同上 Flash pinned | Read, Write, Edit, Glob, Grep, Bash, NotebookRead, NotebookEdit, WebFetch, TodoWrite | 五段规格的有界机械实施；ownership 纪律同 workflow worker persona；local_check 诚实执行 |
| `conductor-impl.md` | **无 model 键（继承主会话模型，glm-reviewer 同款先例）** | 同 impl-flash | 复杂有界实施（需主模型强度、不需整 workflow 的单块工作） |

8.2 直派通道（orchestration SKILL 新节 'Direct dispatch roles' + role-contracts.md 全契约）：

- **任意路线可用（无任务前提）**：scout（调查）、runner（跑命令/测试）——只读/不改仓库面；
- **写型角色纪律**：impl-flash / impl 必须在**持有该仓库 writer guard 的活动任务**上下文中派发（guard 语义零改动）；主会话交五段规格（与节点声明同格式）；
- **delegate 单节点轻量通道（D10 修订条款）**：DAG 恰为单节点且 assurance standard 时，可直接派 impl 角色替代 1 节点 workflow——契约显式写明代价：无结构化 NodeResult、无 ResumeWorkflowRun 恢复（恢复语义=从仓库事实重派）、完成闸/验证/审查链路不变；多节点 delegate/full 仍唯一载体 = Native Workflow；
- **solo 路线辅助定位**：主会话仍是 implementer-of-record，offload 机械块给 impl 角色，路由矩阵四模式不变；
- **模型选型闸兼容**：直派角色模型策略由 frontmatter 公开声明（pinned/继承），不进 workflow-model-select 管线；interview Q2 语义不变（那是 run 级 subagent_model 的闸）；
- **事件**：直派不新增 journal 事件（D13；验证/审查/完成闸照常记账）。

8.3 配套：check_2（frontmatter）/check_4（agent refs）锚点扩展 + 新角色绑定单测（pin 存在性/继承缺省/tools 白名单）；README/架构文档角色表同步。

## 9. W7 —— 模型准入复核收口 + 环境同步

**事实（已核验）**：main=40b7de9 的 `_reject_reason`（`runtime/workflow/submission.py:89`）接受任意无空白两段 `<provider>/<model>`（account: 与非 account provider 同合法）；`_is_flash_tier_candidate` 只取首 '/' 后段判档；pin/select 两面 v2.6.2 已同步并有测试锚（982 含 `opencode-go-responses/deepseek-v4.1-flash` 正例）。owner 所引错误文案（"只接受 account:<账户段>/<模型段>"）是 **2.6.1 及更早的行为**。

W7 内容（复核收口，非代码移除）：

1. **B0 spike P-4**：本机 2.6.2 缓存面活体验证一次全链路——ListModels 列出 opencode 桥接 id → `worker-model-pin` → `workflow-model-select --task-ref` → 提交面接受（不出真 run，到 subagent_model 契约即止）；
2. **环境成因排查指引**（troubleshooting §5 增补）：三类成因与处方——旧插件缓存（缓存就地刷新 + 新会话拿新面；matcher 快照语义）、会话早于刷新启动（新会话即愈）、远端工作区装旧版本（远端同步/重装）；
3. 若 P-4 活体发现任何残留执行面（错误文案/未同步校验点）→ 补修 + 测试锚；预期为零；
4. 文档硬化：orchestration 'Worker model selection gate' 步骤 2 已是放宽后文案（2.6.2 收口），仅再核一处不漏。

---

## 10. 实施批次与出垒门

每批出垒门：定向测试绿 + ruff + validator 16/16（锚点随批同步）+ 本地 commit（统一 push 收官）；**唯一全量回归一次（B8）**。

| 批 | 内容 | 前置 |
| --- | --- | --- |
| B0 | spikes P-1（免确认机制）/P-2（CLI resume 与 Desktop 并发）/P-3（resume 源）/P-4（opencode id 全链路）；**任一推翻前提 → 回本规格改案再动代码** | —— |
| B1 | W7 收口（文档+诊断指引；残留则补修） | P-4 |
| B2 | W2 全量（interview Q4 + state 键 + task-create 旗标 + 钩子 + hooks.json + runbook path 化 + 测试/validator） | P-1 |
| B3 | W3 matcher 一行 + 注释 + troubleshooting 一条 | P-3 |
| B4 | W1 runtime（state 五键 + carrier.py + probe + CLI 子命令 + 单测） | P-2 |
| B5 | W6 四 agents + role-contracts + orchestration 直派节 + validator/测试 | —— |
| B6 | W4+W5 指南两小节 + 锚点 | —— |
| B7 | W1 skill/外档/README 全文档面 | B4 |
| B8 | 发布收口：版本 2.7.0 + CHANGELOG（EN）+ README 计数 + 全量回归 + glm-reviewer 终审 + tag v2.7.0 + Release Latest + 缓存/快照/登记（2.7.0） | 全部 |

## 11. 风险

- **R1** ~~P-1 三机制均被绕过~~ **已关闭（B0）**：机制二成立（PermissionRequest 请求层应答），机制切换已并入 §4.3；
- **R2** ~~CLI resume 与 Desktop 互锁/不可达~~ **已关闭（B0）**：并发实证 PASS；onDemand→alwaysOn 修正后 L1 通道完整；
- **R3** schtasks 受限环境不可写 → probe 自然降 L2，无硬失败；
- **R4** 直派 impl 角色被滥用绕 writer guard → 契约红线 + 文档显式；守卫机制层面无变化（任务级既有）；
- **R5** 文档面广漏同步 → validator 正向锚点 + 双语反回归词表随批更新（v2.6.1 纪律）。

## 12. 明确不做

远端 box nohup 唤醒本机会话（物理不可达）；headless 一次性精确定时（双精度源）；常驻 wake daemon / app-server 形态；L3 场景创造可用性尝试（保持 fail-closed）；多任务并行 OS 载体命名冲突治理（守一仓库一写流+一会话一 auto 既有约束）；inline script 转义修复（path 化直接消灭）；compile advisory（后置观察）；直派角色新增 journal 事件（D13）。
