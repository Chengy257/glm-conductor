# v2.7.0 B0 Spikes 结论（2026-10-24）

任务：v270-launch-2e38b8b9（main=40b7de9 基线，零代码变更，纯验证）
宿主：ZCode Desktop + zcode.cjs 0.17.0（2026-10-19 构建），源码考古 + headless 活体双路取证。

## P-1 CreateWorkflow 免确认机制 —— 部分推翻前提，机制切换

**机制一（PreToolUse permissionDecision=allow）：死路（源码铁证）。**
`applyPreToolPermissionDecision`（zcode.cjs cyo）将 ask 升级为 allow 的条件含
`!e.alwaysAsk`；CreateWorkflow 工具定义 `permission.alwaysAsk:true` —— PreToolUse allow
被该闸显式免疫，无任何输入形态可以绕过。

**机制二（PermissionRequest decision=allow）：成立（架构位 + 权威 schema）。**
- 权限主链（resolveToolPermission）：checkPermission →（CreateWorkflow 走
  checkAlwaysAsk）ask → `kyo` 赛跑结构 `racePermissionResponders`：UI 授权卡
  （permissionBroker.requestPermission）与 **PermissionRequest 钩子链并发竞速，先到先得**；
  钩子返回 `decision:{behavior:"allow"}` 直接应答，卡作废。alwaysAsk 只挡 PreToolUse
  升级层，不挡请求层应答。
- checkAlwaysAsk 旁路矩阵（优先序）：deny 规则 → **sessionRules allow
  （rule.session.allow）** → workflowOwner（AmendWorkflow 本会话 run）→ ask。
  注意：project allow 规则与 config.allowedTools **均不**旁路 alwaysAsk——只认会话级
  授权（卡片 session 按钮或钩子 addRules 授予）。
- 钩子 stdout 权威 Zod（OPr/P0s/A0s）：顶层键白名单
  `{additionalContext?, continue?, decision?("approve"|"block" 旧式), hookSpecificOutput?,
  reason?, stopReason?, suppressOutput?, systemMessage?}`；PermissionRequest 分支
  `decision:{behavior:"allow", permissionUpdates?:[{type:"addRules",
  behavior:"allow"|"deny"|"ask", rules:[{toolName, ruleContent?}]}], updatedInput?}`。
  **设计裁定：B2 钩子只返回 decision.allow、不带 permissionUpdates**——钩子给的
  permissionUpdates 会持久化到 session/project store（跨会话存活），会造成无任务授权
  状态下的历史性过度授权；逐请求现查任务态 + path 限制，零持久化。
- `approvalMode:"user-once"`（跳过钩子竞速的分支）仅产生于 guarded 模式危险命令路径，
  CreateWorkflow 正常 build/edit 模式不经过它，钩子竞速恒可用。

**活体差分：不可判（headless 非 Desktop 等效测试场）——本身是新宿主事实。**
headless `-p`（--mode build）下：带无条件 allow 钩子与无钩子对照两臂提交**均成功**
（run dwfrun-d0a8b9c9… 对照组照常起跑）→ headless 的 ask 层自动放行，钩子与否不构成
差分变量。结论：免确认的最终活体验证只能在 Desktop 会话做，归入 B2 实施验收
（规格 §4.7 原有安排：授权任务 path 提交无卡 / inline 仍有卡 / 未授权任务仍有卡）。

## P-2 CLI --resume 与 Desktop 并发 —— PASS

Desktop 本会话全程存活期间：headless 新建会话（sess_49f67d8d…，response OK）→
两次 `--resume` 同 id 续跑（response RESUMED / RESUMED2，EXIT=0，eventCount
136→349→…）。共享 db.sqlite 无互锁。L1 唤醒轮的并发前提成立。

## P-3 SessionStart resume 源 —— PASS（文档实证）

zcode-guide diagnosing-hooks（官方插件文档）§1：SessionStart matcher 源值域 =
`startup | resume | clear | compact`。`resume` 为官方合法源；插件 matcher
`startup|clear|compact` 漏配属实。补 resume 为纯放宽（加法），安全。
Desktop 重开的端到端活证留待发布后用户侧观察（troubleshooting 已排条目）。

## P-4 opencode 桥接 id 全链路 —— PASS

ListModels 列出 `opencode-go-responses/deepseek-v4.1-flash` → scratch 任务
`worker-model-pin` 接受（prev=null→new）→ `workflow-model-select --task-ref` 消费返回
`{subagent_model:"opencode-go-responses/deepseek-v4.1-flash",
model_policy:"user-authorized-explicit-selection"}`，exit 0。
W7 结论：main（v2.6.2）执行面无残留，owner 所引错误为 2.6.1 及更早行为/环境面；
B1 只做诊断指引与环境同步，零代码移除项。

## 附带新宿主事实（W1/W2 设计输入）

1. **headless -p 工作流工具注册**：`--workflow-mode onDemand` **不**注册
   CreateWorkflow（三次实测：裸 onDemand / +skill 加载 / +--surface desktop 均报
   工具不存在）；`--workflow-mode alwaysOn` 注册且可完成真实提交（p1spike run
   {ok:true}）。**W1-L1 唤醒命令形态据此修正为 alwaysOn**。
2. **headless 权限面零摩擦**：ask 层自动放行（P-1 对照组实证）→ L1 唤醒轮无需任何
   权限护航即可执行 ResumeWorkflowRun / 重建提交；wake 协议提示词的紧约束是唯一护栏。
3. **工作区 config.json hooks 形状**与插件 hooks.json 不同：工作区面嵌套于
   `hooks.events.<Event>` 且需 `hooks.enabled:true`（NZc Zod）；插件面为
   `hooks.<Event>` 数组（本插件 hooks.json 现行格式）。B2 钩子落插件面。
4. 插件 hooks.json 支持 `args` 数组 + `${ZCODE_PLUGIN_ROOT}` 模板（现行
   session_start.py 即此形态），新钩子沿用。

## 规格改案（B0 出垒门执行，先行改案后动代码）

- §4.3：钩子事件 PreToolUse → **PermissionRequest**（matcher CreateWorkflow；判定
  逻辑与 path 限制、fail-open 纪律全部不变；输出换 decision 形态，见 P-1 schema）。
- §4.4：spike 记录即本文档；Desktop 双臂活体验证归 B2 验收（不变）。
- §3.3：L1 唤醒命令 `--workflow-mode` 值 onDemand → **alwaysOn**；补记 headless
  零权限摩擦事实。
- R1 风险关闭形态：机制二成立，interview Q4 与 state 授权面保留；R2 部分关闭
  （并发 PASS + alwaysOn 修正后 L1 通道完整成立）。
