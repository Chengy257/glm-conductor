# GLM Conductor v2.2 — Phase 0 宿主能力实测报告（Scheduled Wake / Cron 工具链）

> **对应计划**：主计划 §26 + 《实施前缺口探查与设计决策记录》§六（残余探针）
> **实测环境**：本机 ZCode（win32），2026-09-01，glm-conductor 2.1.0 插件运行中
> **实测方式**：主会话直接调用宿主 Cron 工具 + ZCode 日志分析 + 缓存 hook 脚本手动执行
> **状态**：第一轮（会话内可测项已确证；hook 拦截项待 M6 部署 probe 后新会话补测）

---

## 1. 结论总表

| # | 探针项 | 结论 | 置信 | 证据 |
|---|---|---|---|---|
| 1 | Scheduled Task create 真实 tool_name | **`CronCreate`**（同族 `CronList` / `CronUpdate` / `CronDelete`） | 确证 | 主会话直接调用成功，工具为宿主原生（无 mcp__ 前缀） |
| 2 | automation id 的字段路径 | **`tool_result.automation.automationId`**（本轮实测返回 `automation-cf2017e9-…`；同步返回 `nextRunAt`（epoch ms）、`cronExpr`、`recurring`、`runCount`、`lifecycleStatus`） | 确证（PostToolUse 透传形态待 M6 复核） | CronCreate 返回 JSON 全文 |
| 3 | 相对延迟 → 绝对调度的换算 | `delayMinutes=3` → `cronExpr="48 23 1 9 *"` + `nextRunAt = anchorAt + 180000ms`。**wake-plan 必须用绝对 cron 5 字段**（reset_at 已知时直接换算），不用 delayMinutes（相对锚点不适合跨窗口唤醒） | 确证 | CronCreate 返回的 scheduleRule |
| 4 | 一次性语义 | `recurring=false` 不传 maxRuns 默认跑一次；`runCount` / `lifecycleStatus` 可观察进度 | 确证（自完成行为待触发后复核） | 工具 schema + 创建返回 |
| 5 | 槽位现状 | `CronList` → `{"automations":[...]}`，本轮实测时可新建（≥1 空槽；记忆值上限 20 未复核到边界） | 观察 | CronList 两次调用 |
| 6 | hook 配置热加载 | **不热加载**（会话启动快照，InMemoryHookRunner 静态数组）——本会话内部署 probe 无效，hook 拦截实测必须「部署 → 新会话」 | 确证（v2.1 Phase 0 已证，本轮沿用） | v2.1 Phase 0 文档 + 官方文档 |
| 7 | ZCode 日志作为 hook 观察面 | **只记失败**（`hook.run.failed`，warn 级，含 hookEventName/matcher/source/durationMs/sessionId）；成功执行不记日志。M6 验证应以「无 failed 记录 + probe 自身落盘副作用」双证据 | 确证 | 今日日志 444 条 failed 全量分析 |
| 8 | glm-conductor hook 健康度 | **正常**。今日全部 failed 记录属 `example-plugin@zcode-plugins-official`（其 SessionStart `startup\|clear\|compact` 与 PreToolUse `Bash\|Write\|Edit` 在失败，与本插件无关）。缓存 2.1.0 的 `session_start.py` 手动执行：exit 0 且正确产出 v22-loop-857f55a 的 RESUME CONTEXT（12 waiting 单元 + 授权面）；`pre_tool_use.py`（Bash payload）：exit 0 静默放行 | 确证 | 手动执行 + 日志 source 字段 |
| 9 | SessionStart 恢复注入 | 下一个新会话将注入 v22-loop-857f55a resume context（任务账本已被恢复发现捕获）——v2.2 任务自身的 resumable 闭环可用 | 确证 | 手动执行 session_start.py 输出 |
| 10 | wake 注入形态（同会话 user-turn） | **确证（2026-09-01 15:49Z 触发实测）**：探针在上一 turn 结束、会话空闲后以**用户 turn 形式注入本会话**，prompt 全文原样到达、marker 完整可读；完整对话历史可读（v2.1「同会话续行」再确证）；注入不触发任何可见 hook 通知（SessionStart 不重放）；turn 进行中不注入（触发时刻与注入时刻间隔 = 上一 turn 剩余时长）。附带确证 **one-shot 自完成**：触发后 `runCount=1` + `enabled=false` + `lifecycleStatus="completed"`，**无需 CronDelete 清理（glitch 红线天然规避）**；`lastRunAt` 较计划时刻晚 ~20 秒（调度精度可接受） | 确证 | 主会话第一手观察 + CronList 触发后状态 |
| 11 | PreToolUse/PostToolUse 对 Cron 工具的拦截 | **待 M6 部署后新会话补测**：matcher 候选 `CronCreate\|CronDelete\|CronUpdate`（官方语义为正则匹配 tool name；Cron 系是原生工具，理论可拦） | 待测 | 官方文档 |
| 12 | UserPromptSubmit 对 wake 注入 turn 的触发 | **待补测**（Stop/UserPromptSubmit 忽略 matcher——v2.1 已证；wake fire 机械记账挂点候选） | 待测 | v2.1 Phase 0 + 官方文档 |
| 13 | **会话级 automation 创建限制（2026-09-01T16:16Z 发现，实验性 wake 建立受阻）** | `Cannot create a scheduled task inside a session that already belongs to a scheduled task`——**被 scheduled wake 触发过的会话被宿主标记"belongs to scheduled task"，即使该 automation 已 completed，本会话内也不能再创建任何新 cron**。含义：① until_done 多窗逐窗建 wake 在同一会话内**不可行**（第一窗 wake 触发后的会话建不了第二窗）；② bridge 必须在"干净会话"（从未被 wake 过）中创建；③ 账本在仓库不在会话——跨会话恢复不受此限（新会话建 wake / SessionStart 恢复均有效）。**wu-22-05/06 设计修正项：wake planner 的建立时机与"宿主关闭后 wake 去向"（RG-22-05）因此升为必测** | 确证（错误信息原文） | CronCreate 被宿主拒绝（本会话探针 15:48Z 触发后） |

## 2. 关键证据摘录

### 2.1 CronCreate 返回结构（automation id 字段路径锚点）

```json
{"automation": {
   "automationId": "automation-cf2017e9-70f7-496e-96e6-10928fc1c5a5",
   "title": "…", "cronExpr": "48 23 1 9 *", "prompt": "…",
   "enabled": true, "lifecycleStatus": "active",
   "nextRunAt": 1788277735588, "runCount": 0, "recurring": false,
   "scheduleRule": {"unit": "minute", "interval": 3, "hour": 23,
                    "minute": 48, "anchorAt": 1788277555588}},
 "message": "Created automation automation-cf2017e9-…. "}
```

M6 的 PostToolUse 解析锚点：`automation.automationId`（str）+ `nextRunAt`（epoch
ms → ISO 换算后即 wake_at 事实）。**注意**：PostToolUse payload 中 tool_result
是否原样透传此 JSON 待 M6 部署后实测（若为摘要文本，需正则提取
`automation-<uuid>` 形态作降级解析）。

### 2.2 日志观察面（M6 验证协议）

- `~/.zcode/cli/log/zcode-YYYY-MM-DD.jsonl`：`event=hook.run.failed` 含
  `context.{hookEventName, matcher, source, durationMs}` 与 `sessionId/turnId`；
- 成功执行零记录——M6 的验证证据链 = ①日志无 failed + ②probe 脚本自身的
  落盘副作用（payload dump 文件存在且形态正确）；
- 今日 444 条 failed 全属 example-plugin（其失败不阻塞、不波及本插件——
  hook runner 按插件隔离执行）。

### 2.3 红线复核（本轮实测未违反）

- 未调用 CronDelete / CronUpdate（glitch 红线）；
- 探针为一次性（recurring=false）自完成，无需清理；若因故未触发，容忍
  残留（与 §15 ghost wake 安全设计一致——wake fires → 无任务 → no-op）。

## 3. 对 v2.2 实施的直接输入

1. **wake-plan（wu-22-05）**：`wake_at` → cron 表达式换算逻辑确定（5 字段
   本地时区分:时:日:月:周；`reset_at + grace` 的 UTC 时刻换算到本地时区后
   填充）。CLI `wake-plan` 输出直接携带可复制的 CronCreate 参数组。
2. **PostToolUse（wu-22-06）**：解析锚点 `automation.automationId`；同步
   捕获 `nextRunAt` / `cronExpr` 入 `continuation.wake_bridge`。
3. **M6 验证协议**：部署 probe（插件 hooks.json 随 wu-22-06 实施）→ 更新
   插件缓存（市场源重装，dogfood 惯例）→ 新会话创建一次性 cron → 证据链
   = 日志无 failed + probe dump 副作用 + PostToolUse 记账 journal 事件。
4. **UserPromptSubmit**（候选增强）：wake fire 机械记账挂点——M6 部署后
   顺手实测，若触发则 wake_bridge_fired 事件可机械落账，否则退化为
   wake prompt 内指示模型调用 CLI 记账（wake-fired 子命令）。
5. **本会话 hook 全部在快照内运行**：v2.2 的 hook 改动（wu-22-06/07/09）
   在本会话内不生效——dogfood 与 Stop Gate 新检查的验证一律走
   「实施 → 更新缓存 → 新会话」，与 v2.1 惯例一致。

## 4. 残余不确定项（转 M6/M10 处理）

| 项 | 处置 |
|---|---|
| PostToolUse tool_result 透传形态 | M6 probe 实测后冻结解析器（JSON 路径优先、正则降级） |
| UserPromptSubmit 是否对 wake 注入触发 | M6 部署后实测；不影响 correctness（wake prompt 内置 CLI 记账指示兜底） |
| Cron 工具是否被 PreToolUse 管道覆盖 | M6 probe 实测；失败则走降级路径（主计划 §26 已预留） |
| 槽位上限 20 的精确值 | 不强求；cleanup 设计已按「容忍删除失败」 |
| 休眠 / ZCode 关闭后的 missed-wake 行为 | RG-22-05 真实 dogfood 覆盖 |
