# GLM Conductor v2 — Phase 0 运行时验证报告

> 日期：2026-08-28
> 范围：v2 升级指南（final 版）Phase 0 spike——在承诺实施前验证 ZCode 运行时的五个关键未知项
> 方法：本机实测（钩子日志取证 + 活体探针 + 差分实验）+ 静态代码提取（zcode.cjs 反汇编级 grep，证据存 `TEMP/gc-phase0-static-findings.md`）
> 状态：**全部未知项已关闭**；含一项对 v2 P0 设计的重大修正建议

---

## 1. 结论总表

| # | 未知项 | 结论 | 置信度 | 证据类型 |
|---|---|---|---|---|
| 1 | 配置文件钩子是否热生效 | **不热加载**——会话/进程启动时一次性读取并快照，运行期零文件 IO；改动需新会话 | 确证 | 实测 + 代码（无 watcher，`InMemoryHookRunner` 静态数组） |
| 2 | 子代理工具调用是否触发 PreToolUse | **不触发**——子代理子会话构造时不传 `hookRunner`，发射函数 `runPreToolUseHooks` 检测到空即静默跳过 | 确证 | 实测（~80 次子代理调用 0 触发）+ 代码级 |
| 3 | 钩子输入的身份字段 | **有 `agent_type`**（Claude 兼容 stdin：`hook_event_name`/`session_id`/`permission_mode`/`agent_type`/`tool_name`/`tool_input`/`tool_use_id`/`transcript_path`/`stop_hook_active` 等）——但仅在钩子实际触发时可见（即主会话调用） | 确证 | 代码（`createClaudeCompatibleHookStdin`） |
| 4 | Windows 钩子进程解释器 | **python3 在 PATH，node 不在**——官方 example-plugin 的 `command: "node"` 在本机全天 350+ 次 `hook.run.failed`；python3 从子进程环境成功执行 | 确证 | 实测 |
| 5 | 多子代理并发 | **可用**——两个 `run_in_background` flash 子代理实测并行（探针 B 的 72 秒飞行窗口完全嵌在提取代理 A 的 18.6 分钟运行期内） | 确证 | 实测（时间戳重叠） |
| （前轮） | Quota 监控端点 | **官方支持 API Key 直查**，本机已配 Key 并端到端实测全通 | 确证 | 官方插件源码 + 本机实测 |

## 2. 关键证据摘要

### 2.1 子代理不触发钩子（最重要）

- **实测**：flash 子代理飞行窗口内（契约修改代理 29 次工具调用、校验器代理 18 次、提取代理 66 次、探针代理 9 次），钩子日志（`~/.zcode/cli/log/zcode-<date>.jsonl`，模块 `core.hooks`）中归属本会话的事件全部可与主会话自身的调用一一对应；子代理调用触发数为 **0**（窗口内仅 1 条无法归属的孤立条目，见 §5 附注）。
- **代码**（zcode.cjs）：`runPreToolUseHooks` 的第一行即 `e.hookRunner ? e.hookRunner.run(...) : {additionalContexts:[]}`；子代理执行器（Agent/Task 工具 → `runExploreAgent` 构造子会话 `new Ah(...)`）的 config **不含 `hooks` 键**、`taskType:"subagent_child"` → 子会话 `hookRunner = undefined` → 全部工具事件（含 Edit/Write/Bash）空转。
- **推论**：PreToolUse/PermissionRequest/PostToolUse 对子代理内部的写操作**无强制能力**。

### 2.2 Stop 完成门完全可行

- Stop 在主会话 turn 结束后发射（主会话 hookRunner 存在）；`{"decision":"block"}` 或 `{"continue":true}` 或退出码 2 请求续跑；**每 turn 最多 3 次续行**（常量 `Dui=3`，`stop_hook_active` 字段防死循环）——v2 指南 §16 的循环安全由运行时内建。
- 输出协议（Zod 严格校验，stdout 必须以 `{` 开头）：PreToolUse 可返回 `permissionDecision: allow|ask|deny` + `permissionDecisionReason` + **`updatedInput`（可改写工具输入）**；退出码 2 = deny/block；其他非零 = 报错。

### 2.3 钩子运行时细节（实现必读）

- **分发**：插件 `hooks/hooks.json` 自动启用 runner；config 钩子默认禁用需 `enabled:true`；两者都在会话启动时装载快照
- **matcher**：纯 `[a-zA-Z0-9_|]` 字符串按字面量多值精确匹配；否则按 JS 正则（大小写敏感）；别名 `Agent↔Task`、`ApplyPatch→{Write,Edit}` 全事件生效；Stop/UserPromptSubmit 忽略 matcher
- **spawn 环境**：cwd=会话工作目录；env 含 `ZCODE_SESSION_ID`/`ZCODE_PROJECT_DIR`，插件钩子另注入 `ZCODE_PLUGIN_ROOT`/`ZCODE_PLUGIN_DATA`/`ZCODE_PLUGIN_ID`/`ZCODE_PLUGIN_NAME`（及 CLAUDE_* 兼容名）；支持 `${VAR}` 模板展开
- **type 语义**：`command` → shell 模式；`process` → argv 模式（不经 shell）
- **Windows 坑**：git-bash 的 `TEMP=/tmp` 与 Windows Python 的路径解析不一致（钩子内必须用绝对路径）；日志写入有缓冲延迟（分析工具须容忍秒级滞后）

### 2.4 Quota API（前轮已闭环，此处归档）

- `GET https://open.bigmodel.cn/api/monitor/usage/quota/limit`，`Authorization: <Coding Plan API Key>`（无 Bearer 前缀）
- 本机 Key 在 `~/.zcode/v2/config.json` → `provider/builtin:bigmodel-coding-plan/options/apiKey`；实测返回 5h 窗（unit=3+number=5，percentage、nextResetTime epoch ms）+ MCP 通道（TIME_LIMIT）；**lite 套餐无周窗条目——parser 必须视周窗为可选**
- `model-usage`/`tool-usage` 需 `startTime`/`endTime` 查询参数，返回逐时序列

## 3. 对 v2 设计的影响（需讨论决策）

### 3.1 Ownership Gate 必须降级重构（P0 最大修正）

原设计「PreToolUse 拦截子代理越界写入」**不可行**（钩子不在子代理上下文执行）。可行替代：

- **方案 A（推荐）——完成门侧校验**：把 ownership 从「写前拦截」移到 Stop 完成门与 PostToolUse(Agent) 校验：任务完成前，钩子/门逻辑核对 `git diff 涉及文件 ⊆ state.json ownership`，越界即 block。失去"阻止写入发生"，保留"越界不可能静默通过完成门"。与证据指纹天然协同（指纹本来就按 owned 文件计算）。
- **方案 B——主会话前置注入**：PreToolUse matcher 命中 `Agent|Task`，在派发前钩子校验规格中的 ownership 声明并注入提醒（additionalContext）。属提示增强，非强制。
- **方案 C——等待运行时演进**：向 ZCode 提 feature request（子会话继承 hookRunner），长期恢复原设计。
- A+B 可并行，C 作为跟踪项。

### 3.2 Stop 完成门成为 v2 强制层的第一支柱

主会话上下文中 hookRunner 存在，Stop 门 + 证据指纹 + 审查新鲜度校验全部可确定性执行。P0 实施顺序建议不变（Phase 1 状态层 → Phase 3 指纹 → Phase 4 完成门），Phase 2 按方案 A/B 重定义。

### 3.3 钩子实现语言与分发

- 钩子脚本用 **python3**（PATH 已验证；node 不可用），入口建议 `type: process` + python3 绝对路径（规避各机 PATH 差异）
- 强制钩子随**插件**分发（hooks/hooks.json 自动启用），不依赖用户改 config；文档必须说明「强制仅在安装/更新后的新会话生效」（本机实测：现装 example-plugin 的钩子每次主会话调用都在跑——分发模式已被生产验证）

### 3.4 其余不变项

- PermissionRequest 策略层：主会话动作可强制（子代理动作同 3.1 限制）
- 有界并行（Phase 10）：`run_in_background` 并发实证可用
- Quota（Phase 5）：API Key 直查已闭环，无阻塞

## 4. 复现实验记录（本机 2026-08-28）

1. 备份并临时向 `~/.zcode/cli/config.json` 注入 PreToolUse 日志钩子（python3）→ 补丁后主会话调用均未触发该钩子，而 example-plugin 钩子持续触发 → 不热加载（测后已还原配置，钩子与标记文件留在 TEMP：`gc-phase0-*`）
2. 差分实验：两个后台 flash 子代理飞行期间，窗口内钩子事件仅与主会话自身调用对应
3. 会话谱系澄清：`sess_123292f9`（谱系 ID，子代理档案归档于此）与 `sess_64983340`（当前会话实体 ID，钩子事件按此记账）——分析钩子日志时必须区分
4. 静态提取：`zcode.cjs`（12.5MB bundle）中定位 `runPreToolUseHooks`/`runStopHooks`/`shouldContinueAfterStopHooks`/`parseHookStdout`/`createClaudeCompatibleHookStdin`/`matchesHookMatcher` 等 10+ 关键函数，原始摘录存 `TEMP/gc-phase0-static-findings.md`（210 行）

## 5. 附注与残留不确定项

- 差分窗口中有 **1 条无法归属的钩子条目**（10:33:28，落点在探针代理飞行期内但与其任何调用开始时刻不吻合）。代码结论（子代理无 hookRunner）不因此动摇；但提取代理标注了"另两处 `new Ah` 子会话以 spread runtimeConfig 构造、是否继承 hooks 未完全追踪"——**Phase 2 设计时需一次专项动态复测**（新会话 + 插件钩子 + 各类子代理类型逐一验证）
- 钩子 stdout 为严格 Zod 校验（多余键报错但可恢复）；实现时输出只写白名单键
- 本轮全部结论基于 ZCode 3.9.2 本机实测；版本升级可能改变子代理 hookRunner 接线（跟踪项）

## 6. 建议的下一步（待用户确认后执行）

1. 采纳 3.1 方案 A+B：修订 v2 指南的 Ownership Gate 章节（写前拦截 → 完成门校验 + 派发前置注入）
2. Phase 1（状态层）按既定顺序开工，Phase 2 按新定义设计
3. 向 ZCode 官方提子会话钩子继承的 feature request（方案 C）
