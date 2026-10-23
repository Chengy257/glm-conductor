# v2.6.3 计划：唤醒载体降级矩阵 + CreateWorkflow 免确认 + 宿主兼容核查

状态：SUPERSEDED（2026-10-24 并入 V2_7_0_IMPLEMENTATION_SPEC.md —— 裁决 D1/D12 更新 + W4-W6/W7 扩充；本文 §2 宿主事实与 §3 设计细节被 2.7.0 规格引用为附录）
日期：2026-10-23
基线：main = 40b7de9（v2.6.2），982 tests，validator 16/16
触发：owner 提出三项小版本诉求 —— (1) 无原生定时工具会话（典型：SSH 远端工作区）的唤醒兜底；(2) 工作流每次会话首跑必须人工确认，要求可跳过；(3) 新版 ZCode 兼容面核查。

---

## 0. 结论速览

| 议题 | 结论 | 一句话方案 |
| --- | --- | --- |
| 1. Cron 缺失兜底 | 可做，且现有设计的盲点比想象大：auto 连续性被"原生 Cron 见证"单点绑死，连**不依赖 Cron 的 precision sleep 通道也被连坐关闭** | 引入四级唤醒载体矩阵（native / os_timer / sleep_only / unarmed），`quota_resume` 增加 `automation_kind` 见证类型；OS 级载体经 **zcode CLI headless resume**（`--resume sess_xxx -p`，实测存在且 headless 默认 yolo）真正唤起会话 |
| 2. 首跑授权阻塞 | 可做，宿主有现成旁路：PreToolUse 钩子可返回 `permissionDecision: "allow"`（运行时 schema 实测确认），权限规则链也有 `rule.project.allow` / `allowedTools` 放行位 | 插件新增 PreToolUse 钩子（matcher `CreateWorkflow`），**仓库级 opt-in** 且仅放行 `path` 型（文件态、v24-compile 产物）提交；顺带修掉 unattended 重建路径的授权死锁 |
| 3. 兼容核查 | 一处需动（SessionStart matcher 缺 `resume`），其余核查项均无需变更（含 v2.6.2 已收口的模型 id 前缀） | matcher 改 `startup|resume|clear|compact`；其余记录为"已核查、维持现状" |

版本形态：单版本 **2.6.3**，分 B1–B5 五批实施，每批独立出垒门（沿用 v2.6.x 惯例）；不设 feature 分支外的拓扑变化。

---

## 1. 议题与现状差距

### 1.1 议题一：无原生定时工具会话的唤醒兜底

现状（continuity SKILL.md 'Failure Handling'）：宿主调度能力不可用时 auto 连续性整体 fail-closed —— `quota-continuity-preflight` 要求 `quota_resume.automation_id` 非空（原生 Scheduled Task 见证），CronCreate 缺失的会话里：

- floor carrier 无法建立 → preflight ready=false → **auto 分支整体阻断，本轮降级 manual**；
- 连带后果：**precision sleep（后台 Bash sleep，零 Cron 依赖）也不会被武装** —— 因为整个 auto 分支在 preflight 处就断了。这是设计耦合造成的连坐，不是宿主限制。

受影响场景（本机实测存在）：SSH 远端工作区（`remote:ssh:202.116.90.48:...`，见 `~/.zcode/v2/setting.json` lastWorkspaceSession）。宿主工具注入逐会话可变，远端会话缺 CronCreate/CronDelete/CronList（既有 probe 事实，`docs/reviews/WAKE_TOPOLOGY_PROBE_RESULTS.md`）。

owner 提议方向：Linux `nohup` 终端后台定时 / Windows 等价物。**关键澄清（物理约束）**：会话（模型、auth、session db）永远活在装 ZCode 的本机；远端 box 上的任何 nohup 都无法注入本机会话。因此 OS 兜底载体的宿主只能是**会话所在机器**（本机 Windows，或 ZCode 装在 Linux 时的本机 Linux）；远端工作区的任务执行仍经 SSH 由同一会话驱动，不受影响。

### 1.2 议题二：CreateWorkflow 每会话首跑授权

现状：CreateWorkflow 工具契约要求"脚本 typecheck 通过后用户确认才启动"。交互轮是摩擦（owner 诉求），**unattended 轮是死锁**：wake turn 走"run 已死不可恢复 → 重建重交"路径时调用 CreateWorkflow 会弹授权卡（既有宿主事实：无人值守回合弹授权 = 死锁，bridge 全白名单教训同源）。v2.4.x 以来的 runbook 没有显式覆盖这个洞，属于被 resume 主路径（ResumeWorkflowRun 无确认）掩盖的边角。

### 1.3 议题三：新版 ZCode 兼容面核查

见 §5；需动一处，其余维持现状。

---

## 2. 本轮新实测宿主事实（2026-10-23，全部只读探针）

以下均为本轮在本机直接验证（zcode.cjs 运行时 schema 解包 + CLI help 实跑 + 官方指南交叉），实施期 spike 只需复验不复探：

1. **PreToolUse 钩子可返回权限决定**。官方 diagnosing-hooks 明文：`PreToolUse` 可返回 `allow/ask/deny`。运行时 stdout 严格 schema（Zod）实测还原：

   ```json
   {"hookSpecificOutput": {"hookEventName": "PreToolUse",
     "permissionDecision": "allow", "permissionDecisionReason": "..."},
    "suppressOutput": true}
   ```

   顶层多余键校验即失败，钩子必须精确按此形态输出。
2. **PermissionRequest 钩子可批准并可授持久规则**。决策 schema：`{"decision": {"behavior": "allow", "permissionUpdates": [{"type": "addRules", "behavior": "allow", "rules": [{"toolName": "CreateWorkflow"}]}]}}` —— 会话级 grant（`grantSessionPermission`）。W2 的备选机制。
3. **权限解析链完整还原**（PermissionService.checkPermission 顺序）：project-rules deny → ask → **project-rules allow** → workflow-draft 预批（仅 Edit/Write 草稿文件，**不含** CreateWorkflow 提交）→ `config.allowedTools` → build/edit 模式闸。即：**存在 allow 规则时根本不会走到 ask**，CreateWorkflow 的确认卡（`prepareCreateWorkflowApproval` 的 `gate:"ask"`）应在 ask 分支侧，预期能被 allow 规则/钩子放行短路 —— 留 spike P-1 实证。
4. **zcode CLI 存在且可 headless**：`node "C:\Program Files\ZCode\resources\glm\zcode.cjs" --version` → `0.17.0`。关键旗标：`-p/--prompt`（单轮 headless）、`--resume <sessionId>`（续既有会话，sess_* 形态）、`--cwd <path>`、`--mode`（**`--prompt` 默认 yolo**——headless 唤醒轮不会弹授权）、`--json`、`--workflow-mode disabled|onDemand|alwaysOn`（**默认 disabled**——唤醒轮需要 ResumeWorkflowRun/CreateWorkflow 时必须显式 `onDemand`）。
5. **会话存储单一**：`~/.zcode/cli/db.sqlite` 为唯一会话库（Desktop 与 CLI 同库，`~/.zcode/v2/` 无独立会话库）——CLI `--resume` 理论可达 Desktop 会话。留 spike P-2 实证（并发互锁/Desktop 可见性）。
6. **钩子模板变量含 `${CLAUDE_SESSION_ID}`**（官方 diagnosing-hooks）——插件可经 SessionStart 钩子落盘 session id 见证，供 OS 载体命令使用（现插件未留存任何 session id）。
7. **SessionStart matcher 合法值**：`startup|resume|clear|compact` 四值；现插件 matcher 为 `startup|clear|compact`（v2.1 起沿用），**缺 `resume`** —— 会话重开/宿主恢复后无恢复上下文注入（§5.1）。
8. Python 前置不变：插件整体（hooks + runtime CLI）本就以 `python3` 在 PATH 为硬前置；hooks 未新增额外前置。

---

## 3. W1 —— 唤醒载体降级矩阵（议题一）

### 3.1 目标拓扑：四级矩阵

启动面（launch interview Q1 确认 enable 后）按能力探测结果落级，**降级永远诚实标注，绝不虚报 armed 层级**：

| 级 | carrier kind | floor 载体 | precision 层 | 存活语义 | 降级标签 |
| --- | --- | --- | --- | --- | --- |
| L0 | `native` | 宿主 CronCreate（现状不变，INV-CONT-03 不动） | 后台 Bash sleep（现状不变） | 跨应用重启 | 无（全武装） |
| L1 | `os_timer` | OS 计划任务（Win: `schtasks`；Linux: `crontab`/`at`）到点触发 **headless CLI resume 唤醒轮** | 后台 Bash sleep（不变） | 跨应用重启、跨机器重启 | floor-only-on-OS（报告 OS 载体名） |
| L2 | `sleep_only` | 无 | 后台 Bash sleep（不变） | **不跨应用重启**（重启后靠用户回来 / L2' 注入） | sleep-only 降级（显式声明） |
| L3 | （unarmed） | 无 | 无 | —— | 现状 fail-closed 语义不变 |

判定顺序（runbook 固化，模型零判断）：会话工具面有 CronCreate → L0；否则运行 `wake-carrier-probe`（§3.4）——`zcode` CLI 可用 + 平台调度器可写 → L1；仅后台 Bash 可用 → L2；后台 Bash 也不可用 → L3。**L2 是本轮的核心增量**：把"auto 被 Cron 单点绑死"解开，让不依赖 Cron 的 precision 通道独立成军。

### 3.2 carrier 见证 schema（state 变更）

`quota_resume` 从四键扩为五键（schema authority `runtime/state.py`）：

```
quota_resume: {mode, max_resumes, resume_count, automation_id, automation_kind}
automation_kind ∈ {"native" (缺省，向后兼容), "os_timer", "sleep_only"}
```

- **缺省即 native**：存量 state.json 零迁移；`bind_quota_automation` 增可选参 `kind`，缺省 native，同 id 幂等语义不变，**kind 不一致同样拒绝**（对齐"绝不静默替换"）。
- `preflight`：auto + 非空 automation_id → ready=true（不变）；返回 dict 增 `automation_kind` 键供报告层区分降级标签。**preflight 语义不因 kind 变宽**——ready 只证明"持久化见证存在"。
- L2 的 automation_id 取合成见证 `sleep-only`（kind=sleep_only）；L1 取 OS 载体名（如 `os:schtasks:glm-conductor-<task-id>`，kind=os_timer）。
- `clear_quota_automation` 同步清 kind；决策层 `continuity-unarmed` 镜像闸不变。

### 3.3 OS 载体设计（L1）

**唤醒动作（一次性）**：到点执行

```
node "<zcode.cjs>" --resume <sess_id> --cwd <repo_root> --workflow-mode onDemand \
  -p "<统一唤醒协议提示词——与原生 floor 的 CronCreate prompt 同文，references/long-horizon.md 模板>"
```

- headless 默认 yolo：无授权弹窗（唤醒轮第一动作固定 `quota-resume-decision`，提示词本就自包含）；`--workflow-mode onDemand` 保证 ResumeWorkflowRun/CreateWorkflow 面可用。
- **session id 来源**：SessionStart 钩子（matcher 含 startup/resume/clear/compact 全源）把 `${CLAUDE_SESSION_ID}` 写入 `.glm-conductor/session-witness.json`（纯本地、单行 JSON、fail-open 与现钩子同口径）。OS 载体创建时读取。
- **floor（常驻节拍）**：Win `schtasks /create /tn glm-conductor-<task-id> /sc hourly /tr "<上述命令>"`（用户级任务免管理员）；Linux `crontab` 追加行（`flock` 防重叠）。间隔沿用 interview Q3 确认值，创建后同样**终身不动**（INV-CONT-03 语义延伸到 OS 载体）。
- **创建/删除一律走 runtime CLI 新子命令**（`os-carrier-install` / `os-carrier-remove`，新模块 `runtime/carrier.py`，subprocess 精确 argv，绝不拼 shell 字符串——schtasks `/tr` 嵌套引号是知名雷区，模型手打 Bash 模板不可接受）。这是对"runtime 零宿主调度调用"红线的一次**显式例外**：OS schtasks/crontab 是本机系统命令而非宿主工具，且落在独立 opt-in 子命令后（决策点 D7）。
- **精度层不换**：L1 下 precision 仍用后台 Bash sleep（应用存活时更便宜、零额外进程）；headless 一次性定时（`/sc once` / `at`）列为**不做**（§8），避免双精度源。
- **终局清理**：OS 载体删除**真实有效**（无三态劣化），`os-carrier-remove` 在 Terminal cleanup 第 1 步替换原生删除尝试（L1 任务）；清理顺序与"先删后清"惯例不变。

### 3.4 探针子命令

`wake-carrier-probe <repo_root>`（纯本地、零模型、零写入）返回单行 JSON：

```
{platform, zcode_cli: {path, version} | null, scheduler: "schtasks"|"crontab"|"at"|null,
 background_bash: true|false(unverified 时给 unknown), recommendation: "os_timer"|"sleep_only"|"unarmed"}
```

实现要点：zcode CLI 探测顺序 = PATH `zcode` → `C:\Program Files\ZCode\resources\glm\zcode.cjs`（Windows 默认装位）→ `node` + zcode.cjs 组合；调度器探测 = 只查可执行存在与 `schtasks /query` 无参自检，**不创建任何探针任务**；background_bash 探测给出保守 unknown（真实可用性由 L2 武装动作本身实证，失败即降 L3 并诚实报告）。

### 3.5 skill / 文档同步面（文档工作量与代码同量级，提前列全）

- continuity SKILL.md：'Dual-channel wake' 增四级矩阵段；'Launch sequence' auto 分支在 CronCreate 步前插探测分支；'Wake finds unarmed' 重建序列改为按 kind 重建（os_timer 走 os-carrier-install）；'Terminal cleanup' 增 L1 分支；'Failure Handling' 第一条改写（能力不可用 → 按矩阵降级而非整体 fail-closed）。
- references/long-horizon.md：唤醒协议模板增 headless 命令形态；载体生命周期段同步。
- orchestration SKILL.md：interview Q3 文案（"floor 载体"从原生限定改为"按探测结果落级"）；auto launch 序列图同步。
- enforcement SKILL.md：无 contract 变化（guard 不读载体），仅若措辞提及"原生 Scheduled Task"处同步。
- 架构/排障三文档 + README 双语四份同步；validator check_7/check_16 锚点随文同步。

### 3.6 测试

- 单元：state 五键 schema / kind 缺省 native 兼容 / bind 幂等与 kind 冲突拒绝 / preflight 返回 kind / clear 清 kind。
- carrier.py：schtasks/crontab 命令 argv 组装的纯构造测试（不真建任务；真实安装冒烟留实施期本机一次手工验证并记录）。
- probe：假 PATH 环境下的推荐阶梯测试。
- 既有 982 套定向回归（test_quota_resume_v24.py / test_guard_budget.py / test_cli_v24.py 为重点）。

---

## 4. W2 —— CreateWorkflow 免确认（议题二）

### 4.1 机制选择（spike P-1 验证顺序）

1. **PreToolUse 钩子 `permissionDecision:"allow"`**（首选）：链路最靠前，提示卡根本不出现。
2. PermissionRequest 钩子 `decision.allow` + addRules：ask 已发生后批准并授会话级规则（备选，若 1 不覆盖 workflow 专用 ask 闸）。
3. 用户在确认卡点 "always allow"（`allowedTools` 路径）：作为文档兜底写进 troubleshooting（若 1/2 均被 workflow 专用闸绕过，则此项为唯一解，W2 缩围为文档 + 死锁修复另议）。

三者任一生效即达成目标；spike 用最小工作流各试一次，结论记入 `docs/reviews/`。

### 4.2 推荐设计（按机制 1）

新钩子 `hooks/pre_tool_use_workflow.py`（hooks.json 增 `PreToolUse`，matcher `CreateWorkflow`）：

- **仓库级 opt-in**：读 `<repo_root>/.glm-conductor/settings.json` 的 `auto_approve_workflow: true` 才生效（缺省不动作——保持人工确认缺省安全语义；决策点 D1）。文件不存在/解析失败 → 不放行（fail-closed）。
- **仅放行 path 型提交**：钩子输入里 `input.path` 存在且文件位于本仓库 `gen/` 或 `.zcode/workflow-drafts/` 下才 allow；inline `script:` 提交一律不干预（仍走确认）。理由：path 型是 v24-compile 落盘产物，内容已纳入计划/验证链；inline 型是即席脚本，保留人工闸。
- 输出严格按 §2-1 schema，附 `permissionDecisionReason`（审计可见）；未命中策略时 stdout 空（透传默认行为）。
- **附带收益**：unattended 重建路径（run 死 → 重编译 → 重交）在 opt-in 仓库不再授权死锁——这是比"少点一次确认"更硬的价值，写进 CHANGELOG 卖点。

### 4.3 path 型提交标准化（顺车收口）

runbook 增一句硬约束：**CreateWorkflow 提交一律 `path:` 引用 v24-compile 落盘文件，禁 inline `script:`**（visual/saved 例外照旧）。收口三件事：heredoc 转义历史雷区（v2.4/v2.6 教训）、钩子放行的判据单一、typecheck 失败重试不需重贴脚本。orchestration SKILL.md 'Launch details' 与 long-horizon.md 同步一句。

### 4.4 测试

- 钩子单测：opt-in 缺省拒 / path 命中放行 / inline 不放行 / 越界路径不放行 / settings 损坏 fail-closed / 输出严格 schema（多余键即拒的宿主契约对齐）。
- hooks.json 变更入 validator check_13 面。
- 本机活体验证一次（实施期）：opt-in 后首跑无确认卡、inline 仍有卡。

---

## 5. W3 —— 兼容/优化核查清单（议题三）

### 5.1 需动：SessionStart matcher 补 `resume`

`hooks.json` matcher `startup|clear|compact` → `startup|resume|clear|compact`。动因：应用重启/会话恢复后正是恢复注入最该出现的时刻（floor-only 恢复路径依赖"用户回来开新会话拿注入"，而"重开同一会话"现拿不到注入）；钩子本身 fail-open + 幂等，补上无副作用风险。spike P-3 实证 Desktop 会话重开确实以 `resume` 源触发（若是其它源值再对齐）。session_start.py 无需改逻辑（stdin 源值本就不消费）。

### 5.2 已核查、维持现状（记录在案，防止重复审查）

| 项 | 结论 |
| --- | --- |
| 模型 id 前缀规则 | v2.6.2 已按 owner 裁定放宽（provider/model 合法），无遗留 |
| Python 3.7 ast.Str 兼容 | v2.6.1 已修；3.7/3.10 双 pycache 正常共存 |
| hooks 面结构（七事件/process 型/模板变量） | 与现插件声明完全兼容，无 deprecated 项 |
| stop_gate timeoutMs 5000 | 纯本地 state 读，无 git/网络，预算充足 |
| `.zcode/`（workflow-drafts 等）与 `gen/` | 均在 .gitignore，无泄漏 |
| `python3` 前置 | 全插件既有硬前置，本轮不新增前置（carrier/probe 同口径） |
| 工具描述漂移（CronCreate/ListModels/CreateWorkflow 参数面） | 与本会话宿主面一致，无需改 |
| 环境噪声（example-plugin 每工具调用一行注入） | 非本插件问题；建议 owner 在插件管理里停用 example-plugin（用户体验项，不入本计划） |

---

## 6. 实施批次与出垒门

每批：定向测试绿 + ruff + validator 全绿后本地 commit（沿用不中断 push 纪律，收官统一 push）；唯一全量回归一次（B5）。

- **B0 spikes（半天）**：P-1 免确认机制三验（§4.1）；P-2 CLI `--resume` 与 Desktop 同库/并发行为（空闲会话 headless 一轮，观察 Desktop 侧表现与锁）；P-3 SessionStart `resume` 源实证。**任一 spike 推翻前提 → 回本计划改案再动代码**。
- **B1 W2 全量**：钩子 + settings 面 + runbook path 型约束 + 文档/validator/测试。（先做 W2：无 state 变更、独立可 ship、立刻消除 owner 摩擦。）
- **B2 W3 matcher + 已核查记录**：一行 hooks.json + session_start.py 注释同步 + troubleshooting 一条。
- **B3 W1 runtime**：state 五键 + bind/preflight/clear/decision 扩展 + carrier.py + probe 子命令 + 单测。
- **B4 W1 skill/文档**：四级矩阵全文档面（§3.5 清单）+ validator 锚点。
- **B5 发布收口**：版本 2.6.3 + CHANGELOG（EN）+ README 计数 + 全量回归 + glm-reviewer 终审 + tag/Release/缓存刷新/登记（2.6.3）。

---

## 7. 决策点（待 owner 拍板）

- **D1**：W2 opt-in 缺省。建议：缺省关（首次使用需在目标仓库放 `.glm-conductor/settings.json`），保人工确认缺省安全语义。备选：装即开（摩擦最小但静默削掉一道人工闸）。
- **D2**：carrier 见证形态。建议：第五键 `automation_kind`（机器可读、缺省向后兼容）；备选：类型前缀字符串（零 schema 变更但处处解析字符串）。
- **D3**：L2（sleep_only）是否允许武装 auto。建议：允许 + 诚实降级标签（解决远端会话"全有或全无"的主要痛点）；备选：L2 仍 fail-closed（保守但等于议题一无解）。
- **D4**：OS 载体实施范围。建议：只做 floor（常驻节拍）+ 沿用后台 sleep 精度层；headless 一次性精确定时列为不做。
- **D5**：SessionStart matcher 补 `resume`。建议：补（§5.1）。
- **D6**：`os-carrier-install/remove` 是否入 runtime CLI。建议：入（argv 精确、可测、模型零拼装）；备选：模型按模板手打 Bash（与现哲学一致但 Windows 引号雷区不可接受）。
- **D7**：红线修订措辞。"runtime 零宿主调度调用"改为"零**宿主工具**调用；OS 级计划任务经显式 opt-in 子命令例外"——是否接受此例外。

## 8. 明确不做（本轮边界）

- 远端 Linux box 上的 nohup/cron 唤醒（物理不可达本机会话；远端仅承载执行）。
- headless 一次性精确定时（`/sc once`/`at`）——双精度源，收益不抵复杂度。
- 常驻 wake daemon / app-server 形态。
- L3 场景（后台 Bash 也不可用）的任何"创造可用性"尝试——保持 fail-closed。
- 多任务并行 OS 载体命名冲突治理（仍守一仓库一写流 + 一会话一 auto 任务既有约束）。
- heredoc 型 inline 提交的转义修复（被 §4.3 path 化直接消灭）。

## 9. 风险

- **R1**：P-1 全部三机制都被 workflow 专用闸绕过（低概率，链路还原支持 allow 短路）→ W2 缩为文档项 + 死锁修复另案。
- **R2**：CLI `--resume` 与 Desktop 并发互锁或不可达（P-2 验证）→ L1 降为文档级"手工配方"，矩阵仍保 L2 增量。
- **R3**：schtasks 在受限环境（无用户计划任务权限）不可写 → probe 返回 sleep_only，矩阵自然降级，无硬失败。
- **R4**：文档面广（§3.5）漏同步 → 沿用 v2.6.1 锚点纪律（validator 正向锚点 + 双语反回归词表随批更新）。
