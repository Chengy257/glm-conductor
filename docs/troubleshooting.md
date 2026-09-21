# GLM Conductor 故障排查（v2.4）

> 面向用户的排障手册：按「现象 → 判定 → 处置」组织，只写结论性行为事实；每条事实标注实现模块路径供核对。架构全貌见 [architecture.md](architecture.md)，概念入门见 [core-concepts.md](core-concepts.md)。
>
> runtime CLI 统一入口是 `plugins/glm-conductor/runtime/cli.py`（本文以 `<cli>` 代指；安装后以实际插件缓存路径为准）。用可用的 Python 3 解释器执行（Windows 无 `python3` 启动器时用 `python`）。v2.4 的 CLI 子命令仅：`quota-resolve` / `quota-wait` / `quota-resume-authorize` / `quota-resume-decision` / `quota-resume-confirm` / `v24-compile` / `writer-acquire` / `writer-release` / `writer-show` / `v24-record-run` / `review-record`；额度诊断另有独立脚本 `runtime/quota/report.py`。查询本文未列出的子命令（permit / lease / wave / clock / bridge / policy / verify-* / host-check 等）没有意义——那些面连同后端已不存在。

## 1. 额度诊断：report.py 双模式与套餐口径

**何时用**：任务停在 `waiting_quota` / `waiting_user` 不动；恢复决策反复 `remain-waiting`；想确认当前额度窗口与 reset 时间。

**怎么跑**（只读诊断，退出码恒 0——不是闸门，不要据退出码拦截）：

```
python <插件根>/runtime/quota/report.py            # 文本：人读
python <插件根>/runtime/quota/report.py --json     # 机器可读（供编排决策）
```

**输出解读**：各窗口（`5-hour` / `weekly`）的 used / remaining / reset、`status:`（`AVAILABLE` / `PRESSURE` / `EXHAUSTED` / `UNKNOWN`）与 `plan:`（continue / checkpoint / resume_at / periodic_fallback）恢复建议。三种诊断态：

文本输出示意（正常态）：

```
GLM Coding Plan Quota Report  (2026-09-21 08:30 UTC)
provider: bigmodel
  5-hour : used 62%  remaining 38%  resets 2026-09-21 15:04 (+6h34m)
  status : AVAILABLE
  plan   : continue
```

1. **正常报告**——窗口明细 + status + plan；
2. **探测失败**——每 provider 一行错误分类（auth / unavailable / malformed / network / unknown，不含 key 与响应体）；
3. **unavailable**——未找到凭证，按输出中的配置指引处理（环境变量 `GLM_CONDUCTOR_QUOTA_API_KEY` 优先；已登录 ZCode 的 `~/.zcode/v2/config.json` provider 配置为文档化回退）。

**lite 套餐口径**：周窗（weekly）是**可选窗**——lite 套餐实测只有 5 小时窗，解析层容忍 weekly 缺席且绝不假设双窗必然存在（`runtime/quota/parser.py`；按语义字段判窗：unit=3/number=5 → five_hour，unit=6/number=1 → weekly）。lite 用户看到"只有 5-hour 一行"是正常形态，不是故障。

**运行时解析**：`python <cli> quota-resolve <repo_root> [--force-refresh]` 输出四级层级解析结果（新鲜缓存 ≤300s → provider → 陈旧缓存 → `UNKNOWN`，绝不默认 AVAILABLE；`--force-refresh` 跳过缓存强制走 provider）——`runtime/quota/resolver.py`。四态的恢复语义（完成守卫不消费额度；四态只喂给 quota_resume 决策链）：

| status | 含义 | `scheduled_activation_decision` 行为 |
| --- | --- | --- |
| `AVAILABLE` / `PRESSURE` | 可用（PRESSURE 是余量压力，仍可恢复） | 已授权且有预算 → `resume-authorized` |
| `EXHAUSTED` | 耗尽 | `remain-waiting` |
| `UNKNOWN` | 数据不可得（无凭证 / 网络失败 / 无缓存） | `remain-waiting`——绝不虚构可用性 |

**恢复操作入口**（AF-03 起）：等待 / 授权 / 决策 / 确认四步的稳定入口是 CLI `quota-wait` / `quota-resume-authorize` / `quota-resume-decision` / `quota-resume-confirm`（单行 JSON 输出，见 `runtime/cli.py`）——额度恢复的运行时操作走这四个子命令，不用 `python3 -c` 直调 runtime API（授权本身仍由用户在技能 / 策略层把关，CLI 不自助放行）。

**窗口机制口径**：reset_at 时刻窗口恢复 100%（周窗优先）；下一个 reset_at 只在新窗口内发生模型调用时才物化——纯查询绝不推进它（`runtime/quota/window_math.py` / `time_utils.py`）。

## 2. v2.3 遗留任务：检测与处置

**现象**：恢复摘要 / 完成守卫报「v2.3 遗留任务：请在 2.3.x 下收尾或显式放弃（v2.4 不自动迁移、不伪造 v2.4 完成证据）」；或 `load_task` / 生命周期操作被拒绝并携带同一句指引。恢复注入中遗留任务渲染为一行式（`runtime/recovery.py`）：

```
[v2.3] refactor-auth-9c1d2e — v2.3 遗留任务：请在 2.3.x 下收尾或显式放弃
                            （v2.4 不自动迁移、不伪造 v2.4 完成证据）
    Next:    explicitly retire legacy task
```

**判定**：v2.4 只读检测、绝不迁移（`runtime/state.py` 的 `detect_legacy_task` / `is_legacy_state`）。命中以下任一标记即 v2.3 遗留：

- 顶层集合键 `work_units` / `permits` / `leases` / `receipts`；
- `work_units[i]` 内的 `permit` / `lease` / `receipt` 运行时字段；
- `status` 是非空字符串但不在 v2.4 七态词汇内（v2.3 阶段态，如 `finalizing`）。

**处置**：

1. 该任务若还想完成 → 切回 v2.3.x 插件版本收尾（v2.3.x 可经 Git 历史 / tag 随时找回）；
2. 不再需要 → 显式放弃：归档 / 删除 `.glm-conductor/tasks/<task-id>/` 任务目录（仓库真实改动以 git 为准，与任务账本无关）；
3. 之后照常在 v2.4 下重建任务——**不要**手工把遗留 state.json 改写成 v2.4 形态（缺 v2.4 必填键的文件 `validate_state` 照常报错，伪造完成证据不会被完成守卫采信）。

## 3. 写者守卫残留：`writer-show` 确认 + `--force` 清除

**现象**：完成守卫查 1 拦截：「仓库写者守卫正被其他任务持有」，但报出的持有任务其实早已消亡（会话崩溃、任务目录被手工删除等）；新委派 `writer-acquire` 冲突；或（AF-04 起）委派任务完成被拦：「完成被阻断：任务 <id> 已注册委派 run，但仓库写者守卫预约缺失（missing writer reservation…）」——有 run id 的委派任务在注册（`runtime.task.record_workflow_run`；CLI `v24-record-run` 是 adapter 单据薄壳，不写 state 也不带守卫前置）与完成两处都要求仍持有本仓库写预约，这是生命周期不变式，不覆盖 Conductor 协议之外的裸宿主 Workflow 调用。

**判定**：先看当前持有者：

```
python <cli> writer-show <repo_root>
```

输出 `{holder: {repo_identity, task_id, workflow_run_id, created_at} | null}`。`writer-acquire` 的冲突返回同口径的持有者信息（`{"ok": false, "conflict": {…持有者三键…}}`，零写入）。守卫**永不自动过期**——无 TTL / generation / heartbeat（`runtime/writer_guard.py` 规格），`created_at` 只是诊断信息，残留记录无论多旧都照样冲突。

**处置**（按优先级）：

1. 委派任务被「预约缺失」拦截（守卫记录被手工删除等，任务本身还在）→ 用本任务身份重新取预约补挂 run id：`writer-acquire <repo_root> <task_id> --run-id <run_id>`（同任务重复 acquire 幂等成功）；此时若预约已被其他任务持有，走下面 2 / 3；
2. 持有任务还活着 → 等它收尾（终态收尾 `task.complete` / `fail` / `cancel` 自动释放守卫），或在该任务内走完完成守卫；
3. 持有任务确已消亡（目录已删 / 用户确认放弃）→ 显式清除：

```
python <cli> writer-release <repo_root> <holder_task_id> --force
```

`--force` 是"inspect 之后由操作者显式清除"的运维通道：以**持有者自身的 task_id** 执行释放（守卫模块本身不设绕过持有者的旁路），stdout 报出被清除的持有者四字段信息。正常（非残留）释放不要带 `--force`。

## 4. reviewer 模型绑定分治：评审者跑在哪个模型上

**背景事实**（v2.4 AF-02 起文本/视觉分治）：`agents/glm-reviewer.md` 的 frontmatter **没有 `model` 字段**——宿主实测表明，单模型宿主上不可解析的固定 model id 会让评审者确定性无法启动（普通 Agent 面直接硬失败）；去掉 model 字段后文本评审者**继承宿主 / 当前会话的模型**，对文本审查契约而言继承即可满足（`scripts/validate_plugin.py` 检查 2 机械锚定：glm-reviewer 出现 model 字段即 FAIL）。`agents/visual-reviewer.md` 则相反：视觉审查契约要求直接读图，继承纯文本主会话模型会「启动成功但丧失契约能力」——其 frontmatter **必须**是 provider 全限定的多模态 Flash 绑定（`account:…/GLM-5.3-Flash`，与 visual-implementer 同款；validate 检查 2 同步锚定：model 缺失、裸 ID 或非 Flash 后缀即 FAIL）。

**含义与排查**：

- 文本评审者（`glm-reviewer`）与主会话同模型是**预期行为**，不是回退故障；独立性的来源是**全新上下文 + 只读工具白名单**，不是跨模型；
- 视觉评审（`visual-reviewer`）跑在已验证的多模态 GLM-5.3-Flash 绑定上；宿主上绑定不可用（启动报 account-connection-unavailable 等）时按视觉例外的 fail-closed 纪律停止视觉高保障路线并告知用户——不得退回文本模型（glm-reviewer 或主会话）充当视觉终审，也不得用文本推测界面正常；
- 对照：`visual-implementer`（实施者，非评审者）同样**保留** provider 全限定 `model: "account:…/GLM-5.3-Flash"` 字段——两个视觉角色同款绑定，文本评审者不共用该规则；
- agent 定义只在会话启动时快照：修改 agents 定义后须**新开会话**再派发才生效。

| agent | model 字段 | 实际模型 | 通道 |
| --- | --- | --- | --- |
| glm-reviewer | 无 | 继承宿主 / 会话模型 | 原生 Custom Subagent |
| visual-reviewer | `account:…/GLM-5.3-Flash`（全限定） | 固定 Flash 多模态（不可用即 fail closed） | 原生 Custom Subagent |
| visual-implementer | `account:…/GLM-5.3-Flash`（全限定） | 固定 Flash 多模态 | 原生 Custom Subagent（视觉例外） |
| 文本 worker（delegate/full） | 无 agent 定义 | persona 内嵌进生成源 | Native Workflow |

## 5. 「hook 不拦 Workflow 子代理」：宿主事实与含义

**宿主事实**（W0 复验，2026-09-20 在当前构建上确认）：ZCode Native Workflow 的子 actor **不触发插件钩子**——workflow 子代理在活动任务存在时零 hook 可见痕迹。这是宿主结构性事实，不是配置问题，插件侧不可修复。

**含义（v2.4 因此这样设计）**：

- **写前拦截在主实施路径上不可实现**——v2.3 式的派发 permit 门 / Agent 注入 / Bash 策略钩子已删除（只覆盖主会话 Bash 的策略钩子留着会暗示主路径并不享有的覆盖面）；
- 确定性强制收敛到**两端**：编译期（ownership 阶段规划 + 一仓库一写者守卫）与终局（完成守卫四查）。越界改动**会被发生**，但不可能静默通过完成守卫——查 2 逐条点名越界路径；
- 因此"worker 改了不该改的文件"的正确处置是：修复 / 撤销改动 → 重跑主验证（diff 变了 change_id 必变，旧验证自动过期）→ 重新过完成守卫；
- 钩子（SessionStart / Stop）只对**主会话**生效且依赖 `python3` 在 PATH；钩子自身故障 fail-open（stderr 报 `ENFORCEMENT DEGRADED` 后放行，绝不卡死会话）——看到该报文说明守卫本轮没干活，属降级可见，不是放行许可；
- 需要权限约束时，用宿主原生权限设施 + worker 约束（节点的 `interfaces` / `constraints` 声明与 persona 纪律），不要试图恢复派发面钩子。

## 6. 状态文件位置速查（排障先找对文件）

| 状态 | 位置 | 说明 |
| --- | --- | --- |
| 任务状态 | `<repo>/.glm-conductor/tasks/<task-id>/state.json` | 七态 schema 真相源（`runtime/state.py`） |
| 任务事件 | `<repo>/.glm-conductor/tasks/<task-id>/events.jsonl` | append-only，任务级十事件词汇（`runtime/journal.py`） |
| 写者守卫 | `<repo>/.glm-conductor/writer_guard.json` | 仓库级单条预约（§3） |
| run 关联 | `<repo>/.glm-conductor/workflow-runs/run-<task_ref 编码>.json` | 任务 ↔ Workflow run 的 id 关联（`runtime/workflow/adapter.py`） |
| 额度缓存 | `<repo>/.glm-conductor/quota-cache.json` | 标准化快照，绑定 provider 身份指纹，不含凭证（`runtime/quota/resolver.py`） |
| 钩子清单 | `plugins/glm-conductor/hooks/hooks.json` | 仅 SessionStart + Stop 两钩子（安装 / 更新后的新会话生效） |

全部在**仓库级** `.glm-conductor/` 下（建议写入 `.git/info/exclude` 本地排除）；该目录是编排器自身账本——不算仓库改动，也永不参与 change_id。账本根的定位口径：钩子进程优先 `ZCODE_PROJECT_DIR`，缺失回退当前工作目录——手工排障时以 `.glm-conductor/tasks/` 所在目录为准。Workflow run 的进行中状态不在上表：那是宿主自有事实，用 ZCode 的 run 观测面查询，Conductor 只存 run id。
