# GLM Conductor v2.1 实施前缺口探查与设计决策记录

- **日期**：2026-08-30
- **性质**：分析记录 + 决策锁定登记（本轮未实施任何功能代码；遵循仓库「分析文档入库、改动走实施计划」惯例）
- **关联文档**：`GLM-Conductor-v2.1-Architecture-Agent-Implementation-Plan.md`（本记录结论已按节修订入该计划，修订处标注 ※DR）、`GLM-Conductor-v2.0.1-子智能体派发恢复与并行执行诊断.md`、`zcode-fr-subagent-hook-inheritance.md`、`docs/glm-conductor-v2-phase0-runtime-verification.md`
- **结论一句话**：计划引用的宿主能力面经三重证据（官方文档、仓库实证、活体实测）核对后基本成立；四个原设计缺口（D1/D4/D6/R3）已锁定方案；另发现三个计划未覆盖的宿主硬约束（H1/H2/H3）与一个交付缺口（CLI 入口），连同 wake 形态与分批交付，共七项决策已由用户于 2026-08-30 确认「按推荐」，全部修订入计划。

---

## 一、证据来源

| 证据层 | 内容 |
| --- | --- |
| 官方文档 | hooks（七事件清单；PreToolUse stdin 含 `tool_use_id`；`permissionDecision` + `updatedInput` 全量替换并重校验；PostToolUse 含完整 `tool_response`；SessionStart 可注入 `additionalContext`；非 0 退出=可恢复失败放行、exit 2=阻断；默认 timeout 60s / stdout 32KB）、subagents（前台同发即并行、后台不阻塞、嵌套禁令、后台 Explore 只读）、automations（总量 20 槽上限、app 关闭触发=跳过不补、会话内创建回原会话） |
| 仓库实证 | Phase 0 运行时验证（19 顶层键载荷、snake+camel 双命名、顶层 `agent_type` 主会话省略、子代理 8/8 零触发钩子、python3 在钩子 PATH、Stop 每 turn 最多 3 次续行）；子智能体派发诊断（`~/.zcode/cli/agents/<主会话id>/<agentId>/metadata.json` 档案含 childSessionId/parentToolUseId/完整 prompt；SendMessage 同会话续接已完成 agent；跨会话不可进程级恢复；僵尸 status；真实任务全程绕过 dispatcher 实录）；ms_new dogfood 教训（CronDelete/Update glitch 绝不重试、heredoc/`python3 -c` 陷阱、实施者报告必须独立复现） |
| 活体实测（2026-08-30 本会话） | 后台 Agent 派发：launch 即返 `agentId` + 档案路径 `~/.zcode/cli/agents/<主会话id>/<agentId>/output.txt`；完成通知自带 (agentId, tool_use_id, result, usage)——ledger 绑定三元组齐全；SessionStart additionalContext 注入真实生效；Agent tool schema 实锤后台字段名 `run_in_background` |
| runtime 源码核对 | `validate_state` 未知顶层键忽略（向前兼容）；`leases.json` tmp+os.replace 原子写 + 单写者前提（permit 存储的现成模式）；`record_unit_verification` 现为「主会话亲跑后自报」——M6 缺口确认；`quota/provider.fetch` + `scheduler.evaluate/plan_resume`（reset+grace 计算）已存在——M5 底座大半现成 |

## 二、宿主能力实锤（原风险项关闭）

1. **R1 PreToolUse 改写入参**：`updatedInput`（全量替换、按 schema 重校验）与 `permissionDecision` 双重证实（官方文档 + Phase 0 活体捕获）；后台字段名 `run_in_background`。
2. **R2 句柄可得性**：PreToolUse stdin 含 `tool_use_id`；后台 launch 结果含 `agentId` + 档案路径；完成通知含三元组；档案 metadata.json 含 `childSessionId` / `parentToolUseId`。ledger 绑定链数据齐全，且不依赖 SQLite（仅文件系统档案，符合 §22.1 红线）。
3. **R7 legacy 兼容**：`validate_state` 明示未知顶层键忽略、可选块缺键合法——`execution_policy` 照 `repository` 块模式加入即可，旧 state 不判 corrupt。低风险。
4. **M5 quota 底座**：provider fetch（含缓存）+ scheduler evaluate + plan_resume 已在；M5 只需把 `prepare_dispatch` 默认值从 `"AVAILABLE"` 改为 `None → resolver` 并实现 UNKNOWN 降级。quota 解析在主会话 runtime API 内执行，不受 hook timeoutMs 约束。

## 三、宿主硬约束（计划原未覆盖）

### H1 — hooks 在子代理内不触发

实证（Phase 0 B0.1 复测 8/8 + 代码级：子会话构造不传 hookRunner，发射函数检空跳过；待提交 FR 见 `zcode-fr-subagent-hook-inheritance.md`）。后果：PreToolUse Bash policy 只覆盖主会话；子代理内执行的验证命令对 PostToolUse 观察不可见——D4 被动观察路线不完整的直接依据。

### H2 — 后台 Agent 的 PostToolUse 只见 launch 确认

Agent tool 调用在 launch 成功即「完成」，最终结果经异步通知送达，runtime 无法 hook 通知。后果：PostToolUse(Agent) 承担「绑定」，不承担后台「结果回收」；结果回收两路——①模型转述进 finish_unit/结果记录 API，②reconcile 时读原生档案（metadata.json status + output.txt，文件级）。前台 Agent 的 PostToolUse 可直接见最终报告。

### H3 — automation 槽位与可靠性

总量 20 槽上限（含暂停/完成/失败，删除才释放）；app 关闭期间触发=跳过不补；会话内创建的任务触发时回原会话（触发时不重放 SessionStart → wake prompt 必须自足）；本机实战存在 CronDelete/Update glitch（绝不重试）。

### H4 — hook 失败三层语义

| 失败形态 | 宿主行为 | 证据 |
| --- | --- | --- |
| hook 无法启动（脚本/解释器缺失） | 阻断所有匹配调用 | 2026-08-29 插件卸载事故实测（所有 Bash/Agent 只返回钩子报错、不执行） |
| hook 运行但崩溃（非 0 退出 ≠2） | fail-open 放行 | 官方文档 |
| 超时 | 未写明 | 残余探针 |

设计含义：permit enforcement 唯一静默 bypass 窗口是「脚本运行中崩溃」——permit 检查路径必须极简（只读 permit 文件 + 状态查询，无网络无 quota）并充分测试。

### 附：钩子快照与生效协议

钩子配置按会话启动快照解析、不热加载；插件缓存更新、子代理定义变更均需新会话生效——dogfood 每轮迭代必须「更新插件缓存（市场源重装）→ 新建会话」。

## 四、已锁定决策（用户 2026-08-30「按推荐」确认）

| # | 决策项 | 锁定方案 | 主要理由 |
| --- | --- | --- | --- |
| D1 | permit 适用范围 | 按 `tool_input.subagent_type` 分级：插件自有实施者类型（flash-implementer / visual-implementer / glm-reviewer / visual-reviewer 及别名）必须 permit；只读类豁免；「包装成只读」由 Layer A Stop Gate ownership 校验兜底 | 全量一刀切令日常只读委派全部撞 deny，摩擦不可接受；判别字段必须用 tool_input.subagent_type（顶层 agent_type 主会话省略，Phase 0 实测） |
| D4 | verify 执行主体 | runtime **受限主动执行**：只执行 state 声明的 required verification command、先过 runtime.policy、执行同刻捕获 exit code + fingerprint（零 TOCTOU）；被动观察降级为非关键增强 | H1 判死被动观察完整性；Bash tool_response 的 exit code 字段未证实；主动执行才满足 DoD「runtime 实际观察到」；与用户既有「实施者报告必须独立复现」纪律同构。Windows subprocess 输出显式 utf-8 解码 |
| D6 | 失败降级与 DoD 措辞 | 派发面 fail-open + 完成面 fail-closed 双层（v2.0.1 B/A 模式延伸）；permit 检查极简；DoD 第 1 条校准为「健康时默认拒绝；运行中崩溃（唯一静默窗口）fail-open 但无法产生合法生命周期证据、完成门仍拒；hook 无法启动时宿主自身阻断」 | H4 实证：hook 坏 ≠ 静默放行（无法启动=全阻断），真实风险是可用性而非 bypass |
| R3 | permit 存储 | 照抄 leases.json 模式：任务目录内独立文件、tmp+os.replace 原子写、单写者前提；**每 permit 一文件、消费 = 原子 rename 为 consumed** 防重放，规避 hook 进程与主会话双写者竞态 | 与既有控制记录（租约）完全同构；rename 原子性使重放拒绝无需文件锁 |
| W | M5 wake 形态 | 一次性 automation（recurring=false + maxRuns=1，自完成）；until_done 惰性逐窗创建（任一时刻 ≤1 活跃 wake）；绝不重试 CronDelete/Update；容忍清理失败（20 槽耗尽 → 降级 SessionStart 恢复）；window 扣减由 runtime 写 state、与 automation 状态解耦；wake prompt 自足 | 本机 CronDelete/Update 已知 glitch；automation 定位本就是 best-effort bridge |
| C | CLI 入口 | 新增 WU-21-15：正式 CLI 入口替代技能层 `python3 -c "..."` 内联调用，随各里程碑增量交付，技能示例全迁移 | ms_new 实战：内联 -c 的引号/换行陷阱多次炸；v2.1 调用面暴涨 |
| B | 分批交付与分支 | 两批：M1–M3（控制面闭合+恢复）先行 → M4–M6（并行+续跑+溯源）+ Docs/Release；每批新会话推进；实施分支沿用 v2 既定策略（v2-dev 开发、里程碑后合 main） | 14 WU 单批回滚成本高；开新会话续作省 30–50%（成本锚点）；v2-dev 为既定分支策略 |

### D1 分批裁定补记（2026-08-31，第一批 M1-M3 终审第 1 项）

D1 锁定文本将 glm-reviewer / visual-reviewer 列入「必须 permit」的实施者集合；第一批实施（wu-21-03，`hooks/pre_tool_use.py` 的 `IMPLEMENTATION_EXECUTORS`）**暂将 reviewer 类型留在豁免集**（走只读 advisory 注入路径），理由：第一批无 review receipt 绑定机制，permit 而无 receipt 不能提升审查溯源强度，反而增加无收益摩擦。reviewer 类型的 permit 义务最终裁定（纳入义务集、或以 WU-21-13 review receipt 绑定替代）**推迟至 WU-21-13 Review Provenance 一并落定**。裁定落定前，规范口径以本补记为准——计划 §6.4 D1 文本中的实施者集合应读作「flash-implementer / visual-implementer 及别名（reviewer 类型待 WU-21-13 裁定）」。**裁定落定（wu-21-13，2026-08-31）：reviewer 类型（glm-reviewer / visual-reviewer）最终维持 permit 豁免**（`IMPLEMENTATION_EXECUTORS` 集合不变），审查溯源由 receipt 绑定承担（`runtime.provenance.run_review` durable review receipt + Stop 完成门「只有 fresh ship receipt 才能通过」）——permit 证明的是「派发被授权」，receipt 证明的是「审查被实际执行且绑定终态指纹」，后者才是 M6 要的增量。

## 五、计划修订对照（已执行，计划内标注 ※DR）

| 计划位置 | 修订 |
| --- | --- |
| 文档头 | 决策记录引用 + 实施分支声明 |
| §2.3（新增） | H1/H2/H3/H4 宿主硬约束 + 钩子快照协议 |
| §5.4 | execution_policy 不变量补充：新增 waiting 状态不得破坏既有状态机（finalizing 只能 completed/failed/cancelled、completed 仅完成门可写、wu ready→verifying 须经 running） |
| §6.3 | R3 permit 存储锁定（leases 模式 + rename 消费） |
| §6.4 | D1 分级：校验链插入 subagent_type 门 + 判别字段警告 |
| §6.5 | R1 证实补记（updatedInput 双重证实、字段名、全量替换要求、残余探针） |
| §7.4 | H2：后台 tool_response = launch 确认，结果回收两路 |
| §9 | reconcile 拆两层：runtime 分类建议+证据句柄；模型侧 ReadSessionContext 组进度包；SendMessage 轻量选项 |
| §14.4 | W：wake 形态五条约束 |
| §16.4 | D4：受限主动执行锁定，被动降级 |
| §17 | PostToolUse(Agent) 注：仅绑定，结果两路 |
| §21 | dogfood 协议前提（更新缓存→新会话） |
| §19 WU-21-11 | wake 卫生目标并入 |
| §19 WU-21-15（新增） | CLI 入口横切工作包 |
| §22.7（新增） | 不得把 wake 正确性建立在 CronUpdate/Delete 上 |
| §23 DoD 第 1 条 | D6 校准语义 |
| §24 | 分批交付结构 |

## 六、残余探针（实施首周，半天级）

1. PostToolUse 实际 stdin 的 `tool_response` 结构（Agent launch ack 与 Bash 的 JSON 形态、exit code 字段有无）——临时探针钩子捕获一次；
2. `updatedInput` 改写 `run_in_background` 后宿主是否实际按改写值执行（机制已证实，Agent tool 特殊性待实测）；
3. SessionStart matcher（`startup|clear|compact`）各值触发面与 additionalContext 体积上限（stdout cap 32KB 相关）；
4. Stop 事件在后台 worker 未 join 时对主会话的拦截面（Dogfood A 顺带覆盖）;
5. hook timeout 的 block/proceed 语义（文档未写；permit 路径按 fail-open 设计则不阻塞，但需知道）；
6. 僵尸判据可机械化程度：metadata.json 字段能否近似「最后消息 token 全零」形状（会话库形状判据属 SQLite 依赖，禁入核心）。

## 七、不变量与边界提醒（实施期红线）

- v2.0.1 已完成边界（§2.1 清单）不得回退；所有修订不削弱 fresh verification / review / finalizing / Stop Gate / completed 链；
- `~/.zcode/cli/db/db.sqlite` 仅可诊断，核心恢复不得依赖（§22.1）；原生档案目录（agents/<sid>/<agentId>/）为可容忍的文件级 adapter 依赖，须隔离封装、缺失时优雅降级；
- 无 daemon（§22.2）、无默认无限跨窗口（§22.3）、无默认大量并发（§22.4）维持不变；
- 本机环境：Python 一律 `python3`（plain python/py 损坏）；本地全量测试以 CI 为权威门（Git Bash GBK 编码故障）；钩子/子代理定义变更需新会话生效。
