---
name: enforcement
description: GLM Conductor v2.4 完成守卫契约（completion guard）。解释 Stop 完成守卫四查（仓库写者守卫 + ownership 越界 + 主会话验证 + 独立审查）、change_id 新鲜度（任何任务相关仓库变化使验证/审查记录过期）、守卫无法评估时的可见降级行为（fail-open + ENFORCEMENT DEGRADED）与被拦截时的恢复方法；含环境自检步骤。用户询问完成守卫拦截原因、change_id 过期含义、降级报文含义或守卫是否生效时使用。
---

# GLM 完成守卫：确定性验收契约

本技能只**解释**守卫的运行时契约，不实现任何强制逻辑——强制由插件钩子（`hooks/`）与运行时模块（`runtime/`）确定性地执行。

v2.4 分工（冻结）：GLM Conductor 拥有语义编排与验收；ZCode 拥有执行编排。Conductor 侧的确定性强制点只剩两个：

1. **Stop 完成守卫**（钩子 `hooks/stop_gate.py`）——任务完成的唯一合法通道；
2. **仓库写者守卫**（`runtime/writer_guard.py` 的仓库级写预约，经 CLI `writer-acquire/release/show` 操作）——一个 Git 仓库至多一个活跃 Conductor 写 Workflow。

派发面钩子（实施者派发拦截、Bash 命令分类、派发时注入）已随 v2.3 执行面整体退役：委派实施不再有钩子前置拦截，完成守卫是唯一确定性防线。绕过过程的任务（越界改动、未验证、未审查）会在收尾时被守卫拦下。

## 环境自检（首先执行）

回答"守卫是否生效"之前先自检：

1. `python3 --version` 有输出（钩子解释器在 PATH）
2. `echo '{}' | python3 ~/.zcode/cli/plugins/cache/glm-conductor/glm-conductor/<version>/hooks/stop_gate.py` 退出码为 0（钩子可独立运行；无活动任务时无任何输出）
3. 钩子**仅在安装/更新插件后的新会话中生效**——当前会话若早于插件更新，回答"本会话未挂载，新会话起生效"

## 哪些钩子执行

`hooks/hooks.json`（随插件分发，安装后自动启用）：

| 事件 | 脚本 | 超时 | 性质 |
| --- | --- | --- | --- |
| SessionStart（matcher `startup\|clear\|compact`） | `hooks/session_start.py` | 5s | 恢复上下文注入（fail-open，见 continuity 技能） |
| Stop | `hooks/stop_gate.py` | 5s | 完成守卫四查（block 决策） |

两者均为 `python3` 进程入口。

## 完成守卫的触发域

Stop 触发时，守卫只对**触发域内**的任务求值；域外零介入：

- **参与**：任务账本根（`.glm-conductor/tasks/`）discover 出的 active 任务中 `status == "active"` 的 v2.4 任务
- **停泊态放行**：`waiting_quota` / `waiting_user` / `blocked` 是有意的停止点，收尾不发生在本回合，一律放行
- **终态任务**：completed / cancelled / failed 无可评估，放行
- **v2.3 遗留任务**：放行并附一行处置指引（`LEGACY_STATE_GUIDANCE`："v2.3 遗留任务：请在 2.3.x 下收尾或显式放弃（v2.4 不自动迁移、不伪造 v2.4 完成证据）"）——绝不假装能判完成
- **corrupt / orphaned 任务目录**：不参与拦截，仅 stderr 一行提示
- **无活跃任务**：静默放行，普通会话零干预

## 四查完成守卫（按序，首败即返）

守卫按固定顺序做四查，全部通过才提交完成；任一失败则以单条最严重、可行动的中文理由 block（四查顺序即严重序）：

| # | 检查 | 通过条件 | 失败形态 |
| --- | --- | --- | --- |
| 1 | 仓库写者守卫 | 仓库写预约不存在，或持有者就是本任务（本任务 Workflow 的预约要到终态收尾才释放——完成守卫自身就是释放点） | 其他任务持有：拦截并逐字报出持有者 task_id 与 workflow_run_id |
| 2 | ownership | 实际改动路径（git touched）全部落在 DAG 全节点 ownership scope 并集内 | 越界路径逐条列出，并附参与比对的 DAG 节点 id |
| 3 | 主会话验证 | `validation.status == "passed"` 且 `validation.change_id` 等于当前 change_id | 未验证 / 验证记录过期 |
| 4 | 独立审查 | 仅 `route.assurance == "high"` 时受查：`review.verdict == "ship"` 且 `review.change_id` 等于当前 change_id | 未审查 / 裁决非 ship / 裁决过期；standard 保障任务跳过本查 |

- **四查全过 → 守卫就地收尾**：执行 `runtime.task.complete`（status=completed + journal `task_completed` 事件 + 自动释放仓库写者守卫）并放行
- **任一失败 → block**：stdout 单行决策 JSON 请求模型续跑；任务状态不变，修复后重新 Stop
- **fix-first / rethink 永不满足完成**；`standard` 保障任务无独立审查义务
- 同一判定逻辑经 `hooks.stop_gate.evaluate_completion(repo_root, task_id)` 暴露为可导入函数——生命周期 API 侧（主会话）可在 Stop 之前显式自检

## change_id 新鲜度（stale 检测的核心）

验证与审查记录都绑定记录时刻的仓库变更状态，锚点是任务级 `change_id`（`runtime/change_id.py`）：

- **计算口径**：`compute_change_id` =「基线修订 + 相关改动文件集的归一化内容状态」的 sha256。相关改动文件集 = `runtime.task.relevant_changed_files`（DAG 全节点 ownership scope 并集 ∩ git 工作区实际改动）——三处（record_validation / record_review / 守卫比对）共用同一派生与同一实现，**禁止旁路重算**：两侧口径不一致的值永远无法通过比对；owned 文件增 / 删 / 改 / 改名 / 文件集变化都改变标识，scope 外改动由 ownership 检查拦截、与新鲜度无关，`.glm-conductor/` 记账在两层各自剔除
- **过期语义**：任何相关文件的增 / 删 / 内容变化 / 基线变化都会改变当前 change_id——既有验证或审查记录随即过期。这是「任何修复使先前验证/审查失效」的自动强制：fix-first / rethink 修复后必须重验（重录 validation）， assurance: high 还须换全新审查者重审（重录 review）
- **恢复路径只有一条**：重跑验证 / 重新审查并落新记录；禁止回写旧 change_id "续命"
- Conductor 自身记账目录（`.glm-conductor/`）不参与变更标识——写账本不会使自己过期

## 守卫无法评估时的可见降级（fail-open）

守卫自身的故障**不阻断会话**（钩子起不来时 fail-closed 会卡死所有会话），但降级必须可见：

| 情形 | 行为 | 报文 |
| --- | --- | --- |
| 钩子进程崩溃 / import 失败 | 放行 + 报警 | stderr `ENFORCEMENT DEGRADED: stop_gate failed: ...` |
| 单任务求值结构性错误（非 git 仓库 / git 故障的 ChangeIdError、非法 ownership scope 模式等） | 只降级该任务：stderr 一行报告后放行该任务，其余任务照常求值 | stderr `ENFORCEMENT DEGRADED: ...` |
| state.json 损坏 / 任务目录残缺 | 不拦截，仅 stderr 提示（corrupt / orphaned 不参与触发域） | stderr 一行提示 |
| python3 不在 PATH | 钩子无法启动（宿主侧报 hook 运行失败） | 见环境自检；文档前置要求已声明 |

**模型侧义务**：观察到任何 `ENFORCEMENT DEGRADED` 时，必须如实向用户报告"守卫处于降级态"，不得默认强制仍在生效。**降级放行不是完成**：守卫只在四查全过路径上执行 complete——被降级任务保持 active，不产生任何完成事实；基础设施故障不能当作完成证据。

## 被拦时的恢复方法

按 block 报文行动（报文自带首败检查与恢复指引）：

1. **ownership 越界是有意的** → 修订 DAG/任务声明的 ownership scope（经 `runtime.task` API 或重写 DAG 后重建声明），再重新收尾
2. **ownership 越界是误伤** → 回退越界改动后重新收尾
3. **验证缺失 / 过期** → 主会话亲自重跑验证命令，`runtime.task.record_validation(...)` 落新记录（change_id 现算）后重新收尾
4. **审查缺失 / 非 ship / 过期** → 按 fix-first / rethink 裁决完成修复 → 主会话重验（重录 validation）→ 换全新审查者复审 → CLI 落新裁决：`python3 plugins/glm-conductor/runtime/cli.py review-record <repo_root> <task_id> <reviewer> <verdict> [note]`
5. **写者守卫冲突** → `cli.py writer-show <repo_root>` 查当前持有者：持有者任务正常收尾时自动释放；确认关联任务/Workflow 已死无收尾路径时，经 `writer-release --force`（先 inspect 取持有者再显式清除）做运维释放
6. **反复被拦且无法修复** → 向用户报告完整 block 报文，等待人工决策

## 边界（当前版本不强制的事项）

- **审查者只读**：由 agent 定义的只读工具白名单保证（确定性），非钩子强制；reviewer 的越界写入最终会被完成守卫的 ownership 查捕获（纵深防御）
- **子会话内写操作**：ZCode 运行时子代理不触发钩子，写前拦截不可实现——这正是"完成守卫作为最后防线"的设计由来；原生 Workflow 的文本工人受 persona 纪律与 ownership 声明约束，越界最终仍由四查兜底
- **视觉证据不做机械比对**：完成守卫四查不含视觉证据检查——视觉验收由视觉通道（VISUAL ACCEPTANCE + visual-reviewer，见 orchestration 技能）承担，主会话验收是视觉事实的把关人
- **无派发面拦截**：实施者派发没有钩子前置检查；语义编排质量（规格是否完备、范围是否有界）由路由纪律与审查承担，守卫只保证收尾事实的完整与新鲜
