# V2_5_2 精确唤醒与模型适配硬化计划（PRECISE WAKE & MODEL ADAPTATION）— v4.1 终态

- 状态：**已实施完成（2026-10-11）**——两 Phase 全部门绿（pytest 827+151 子测试 / validator 16/16 / smoke 0 失败 / ruff）；独立终审（audit/full，fix-first 裁决）P2/P3 事项全部闭合后复门全绿。下文 §2.4/§2.5/§8 的「待放行」措辞为计划冻结时的快照，实际实施已按本计划完成
- 演进：v1 延期武装（废弃）→ v2 窗口锚定链（受三态规律阻断）→ v3 启动即建+自锁 6h（精度不足）→ v3.1 地板+删旧建新升级（作废）→ v4 双通道（1h 地板 + 停靠发睡眠）→ **v4.1 睡眠启动即武装+滚动续臂（本文）**
- 前置证据：`docs/reviews/WAKE_TOPOLOGY_PROBE_RESULTS.md`（automation 状态机、三态发射规律、额度无响应事实、后台睡眠唤起 P-B1/P-B2）
- 版本落点：plugin.json / marketplace.json / CHANGELOG 同步 2.5.2

## 0. 问题陈述

v2.5.1 两个实证缺陷：(a) 跨额度精确唤醒在默认拓扑下**永不触发**（同会话归属约束使 one-shot 加发全灭，模型实际只会用发明周期的固定定时任务）；(b) 契约面存在硬性矛盾与模糊点（CLI 生命周期子命令缺失、问答触发靠模型判断、命令语法散落多处），导致模型"思考查找很多"。

## 1. 决策记录（用户裁决，冻结）

| # | 裁决 |
| --- | --- |
| D0 | 唤醒架构 = **双通道终态**（§2）：启动即建 recurring 载体（活性地板，**默认 1h**，抗应用重启，终身不动；**间隔在启动问答中一并询问确认**）+ **精度层 sleep 同批启动即武装**（授权确认即发首个后台睡眠到当前窗口 reset_at+grace；每次唤醒轮收尾必续臂滚动到下一窗口边界——建立/续臂动作永远先于工作推进，静默耗尽由待命 sleep 覆盖） |
| D1 | CLI：**完善命令与文档，模型不需要自己找，不允许冲突命令**（§3） |
| D2 | workflow-model-select：文档化 models-json 文件形态 + 显式 repo-root 解析（§4） |
| D3 | 启动问答：**改为强制项，零模型判断**——删除"纯会话内小任务不问"豁免（§5） |
| D4 | 启动序列/唤醒协议 runbook 化：伪代码内联完整命令行（§5） |
| D5 | 选型规则：**解绑 GLM-5.3-Flash 字面后缀**；存在选择时**必须经启动问答由用户判断**（§6） |
| D6 | 视觉审查者绑定：仅文档写实，零插件代码（§7） |
| D7 | 终态清理写实：载体删除**预期失败**→上报 automation id→用户手动删除为**预期运营动作**（§2.2） |
| D8 | 边界写实：**一会话一 automation ⇒ 主会话同时至多一个 auto 任务**（§2.4） |

已向用户标注的解释点：D3 意味着极小任务也会被问一次（零判断的代价，已接受）；D5 下恰一候选仍自动选定（无选择可判断）。

## 2. W1：双通道唤醒（1h 地板 + 后台睡眠精度层）

### 2.1 依赖的宿主事实（全部活体实测，证据见探针报告）

- **F1（P-C）** 一会话一 automation：会话名下有 automation（响没响都算）即拒绝创建第二个；
- **F2/F3（三态规律）** 载体首触发前变更可靠；响过之后该会话一切 Cron 变更（改/删/建）失效，删除（实测为用户手动）后恢复；
- **F4（额度无响应）** 额度耗尽时模型轮无法执行——任何依赖"醒来后做调度动作"的机制在最需要处不可执行；等待期连续性只能由宿主侧节奏（automation 触发或后台进程）承担；
- **F5** 子代理工具集不含 Cron 工具；
- **F7（后台睡眠通道，P-B1/P-B2 已实锤）** 后台任务退出**自动唤起**会话模型（空闲会话实测，延迟 8-11s）；前台 600s timeout **不约束**后台任务（660s 完整睡满）；时长上界不另验证（用户裁决：detached 进程不被 kill 即成立）；后台任务**不抗应用关闭/重启**——由地板兜底；
- **F8（窗口锚定）** reset_at 全程可知（窗口内首次调用+5h 锚定即固定）；额度耗尽只决定"醒来有无活干"，不决定"该几点醒"。

### 2.2 架构与生命周期

**总原则（用户裁决）：唤醒机制的建立/续臂动作永远先于工作推进**——授权确认即武装（地板 + 首个精度 sleep 同批建立，先于 CreateWorkflow）；每次唤醒轮收尾必续臂；停靠轮只做核查与补发。

```
启动轮（arm-before-work 保留，建立动作优先）:
    问答(强制,单轮三问:①跨额度唤醒+预算 ②worker 模型(仅歧义时) ③地板间隔(默认 1h,确认或调整))
    → quota-resume-authorize(若启用)
    → CronCreate 载体: intervalUnit=hourly, interval=<问答确认值(默认 1)>, recurring=true,
       prompt=统一唤醒协议(去 priming:动作优先,不列工具清单)
    → quota-automation-bind → quota-continuity-preflight PASS
    → 发首个精度 sleep: 后台 sleep 到 (当前窗口 reset_at + 300s)，退出即唤起（模板见 runbook）
    → v24-compile → 显式选型 → writer-acquire → CreateWorkflow → 记录 run → 开工

停靠轮（额度耗尽被观测、回合仍在飞行）:
    quota-wait → quota-resume-decision --force-refresh
    → 核查精度层在臂情况（已有待命 sleep 即不重发；reset_at 明显漂移则补发新 sleep）
    → 收轮
    【边界:耗尽若发生在回合之间(静默耗尽)——启动/上次唤醒已武装的待命 sleep 恰好覆盖,
     无需任何新动作;这是 sleep 启动即武装的根本理由】

唤醒轮（sleep 退出 或 地板触发,同一协议;收尾必续臂）:
    quota-resume-decision --force-refresh
    ├ resume-authorized 且 preflight → ResumeWorkflowRun(同一 run id) → quota-resume-confirm → 继续工作
    ├ active 且 run 健康 → no-op
    ├ active 且 run 停摆(静默耗尽自愈路径) → 按恢复处理：run 可 resume 则 resume，
    │   否则以仓库现状重建认知后重派发（绝不重放已完成节点）
    ├ remain-waiting 且带 reset_at → 等待
    └ waiting_user / terminal → no-op（terminal 走终态清理）
    收尾动作（各分支处理完，凡任务未终态）: 发下一个精度 sleep 到 (本次观测 reset_at + 300s)

终态:
    task-complete → 对载体试删一次（按三态规律预期失败）→ 如实上报 automation id
    → 用户手动删除（预期运营动作）→ 确认后 quota-automation-clear → 删任务目录
```

精度特征（诚实账本）：**精度层在臂期间，窗口边界唤醒秒级精确**（实测零误差 + 8-11s 唤起延迟）；睡眠链的工作期续臂成本 ≈ 每窗口边界（~5h）一次小唤醒（no-op+续臂），等待期触发免费；仅地板承载时（应用重启杀掉全部 sleep）恢复迟到 0-1 个间隔（默认 1h 间隔下均值 ~30 分钟），恢复后首个地板唤醒即重臂精度 sleep（自愈闭环）；重复 sleep（启动臂 + 停靠补发并存）由决策幂等自然吸收。

### 2.3 地板间隔的权衡逻辑（为什么默认 1h、为什么放问答里）

终态设计里地板是**纯兜底**（精度由睡眠层承担），间隔因此只是"兜底响应速度 vs 工作期监测成本"的权衡，不再是自锁参数：

- 更短 ⇒ 兜底恢复更快（迟到上限 = 一个间隔），但工作期 no-op 唤醒更密（每次一个小模型轮）；
- **不得等于窗口长度（5h 整）**：会与 reset 秒级竞速（唤醒调用锚定下一窗 → 下次触发恒早几秒 → 无响应 → 错过整窗）——间隔显著偏离窗口长度即可（1h 远离 5h，无竞速）；
- 默认 1h（用户裁决）：兜底迟到均值 ~30 分钟，工作期每小时一次 no-op——比此前 6h 自锁方案的稳定 +60 分钟**更快**，代价仅监测密度；
- 间隔因任务而异（重要任务 1h、经济任务 4h 皆合理）——故作为**启动问答第三问**由用户逐任务确认，默认值 1h 写进契约常量。

### 2.4 契约修订清单（Phase 2 文本手术，实施待放行）

- continuity/SKILL.md：「原生 Scheduled Task 唤醒」节重写为「双通道唤醒（地板+精度层，启动即武装+滚动续臂）」；唤醒协议统一（两种触发源同一分支树，**active 分支细化 run 健康检查**——run 停摆按恢复处理，覆盖静默耗尽自愈）；**删除** one-shot 加发节与「绝不用 CronUpdate」相关论述（不再有任何载体变更动作，禁令自然满足）；终态清理按 D7 写实；**唤醒 prompt 设计规则：动作优先，绝不枚举工具清单**（去 priming）；
- orchestration §6.0：问答扩为单轮三问（含地板间隔确认，默认 1h）；§6.2 启动序列改为"建立动作优先"块（载体 + 首个精度 sleep 同批、先于 CreateWorkflow），参数显式（hourly/<间隔>/recurring=true + 睡眠模板，runbook 化）；
- long-horizon.md：唤醒模板改为抄写式——含后台睡眠命令模板（bash 动态计算目标秒数，模型零算术）；
- commands/quota.md：决策输出 reset_at 语义说明（阻塞窗）；
- validator：16h 锚随契约重写；新增 CLI 契约锚、睡眠模板锚、问答第三问锚。

### 2.5 运行时改动（极小，待放行后实施）

- `_quota_observation_view` 的 reset_at 语义修复：由"所有窗口 max"对齐为**阻塞窗（EXHAUSTED）max**（与 plan_resume 一致，避免未耗尽周窗把睡眠推迟数天）；
- 睡眠命令模板由契约给出（bash 动态计算，模型逐字抄写）——**无新增 CLI、无 schema 变更、无调度调用**；
- state schema / task.py / bind-clear-preflight / stop_gate / 决策幂等契约：零变更。

## 3. W2：CLI 生命周期面补齐（D1）

新子命令（薄壳，一一映射 `runtime/task.py` 公共 API，与既有 16 个子命令无名称冲突——前缀 `task-`/`record-`/`exit-` 与 `quota-`/`v24-`/`writer-`/`workflow-`/`worker-` 无交集）：

```
task-create <repo_root> <task_id> <goal> <mode> <assurance> [--dag <path>]
record-validation <repo_root> <task_id> <passed|failed> [--summary <s>] [--commands <s>]
task-complete | task-fail | task-cancel <repo_root> <task_id>
exit-waiting <repo_root> <task_id>        # 手动恢复路径；自动路径已有 quota-resume-confirm
```

- `set_status` / `set_phase` 保持内部原语（生命周期迁移一律走具名命令）；
- **权威命令面**：USAGE + 契约 + troubleshooting 三处同步完整命令清单，validator 锚防漂移——消除"禁止 python3 -c 却无 CLI 可用"的硬性矛盾。

## 4. W3：workflow-model-select 适配（D2）

- 契约写明推荐路径：ListModels → 写临时 JSON 文件 → `workflow-model-select <文件路径>`（内联数组仅短列表便捷形态）；
- 新增 `--repo-root <path>` 可选参数（缺省 cwd 保持兼容），消除与其它命令的接口不一致。

## 5. W4：启动问答强制化 + runbook 化（D3/D4）

- 触发零判断：任何实施任务启动必问一次，**单轮三问**（AskUserQuestion 合并）：①跨额度自动唤醒启用+有界预算（选项含默认）②worker 模型（仅选型歧义时出现，列全部候选由用户选定）③**地板间隔**（默认 1h，可调）；删除小任务豁免；唤醒轮/恢复轮绝不弹问；
- runbook 化：§6.2 双分支与统一唤醒协议伪代码内联完整命令行与参数形状（CronCreate 载体参数、后台睡眠模板、decision/resume/confirm 全链）。

## 6. W5：选型规则解绑（D5）

- 候选判定从精确后缀等值改为规则化（provider 全限定、非主会话文本模型、Flash 档机械判据，实施时定稿并配测试）；
- 歧义（≥2 候选）→ 必经问答由用户选定；恰一候选自动选定；pin 消费语义（失效即拒、绝不回退自动选型）不变。

## 7. W6：视觉绑定限制文档化（D6）

troubleshooting 增条目：本宿主 visual-reviewer 绑定启动即失败（account-connection-unavailable）→ 视觉高保障 fail closed 现状、人工验收替代、待宿主修复。零代码。

## 8. 实施分期与门（待用户放行后执行）

| Phase | 内容 | 门 |
| --- | --- | --- |
| 1 运行时（纯新增/极小修改） | W1 的 reset_at 阻塞窗语义修复（+定向单测）；W2 六个子命令（+单测）；W3 `--repo-root`（+单测） | 定向束 + 全量 pytest + validator 16/16（锚未动）+ smoke 0 失败 |
| 2 契约与发布 | W1 契约重写（双通道唤醒 + 三问问答 + runbook + 睡眠模板）；W4；W5；W6；CHANGELOG；版本 2.5.2；validator 锚更新 | validator（新锚数）/N + smoke + 全量 pytest + ruff |

每 Phase 一次提交（用户终端提交）。

## 9. 开放项（不阻塞主线）

- ~~端到端彩排~~ **已完成（P-R1，2026-10-11 09:38:40Z 零误差唤醒；恢复签名 + 新窗锚定 reset=锚点+5h 精确到秒，见探针报告 P-R1 节）**；
- 宿主上报建议：三态发射规律（响后 Cron 变更静默退化为 CronList 而非显式报错）、同会话归属约束、视觉绑定失败、后台任务不抗重启——建议反馈 ZCode 团队；
- 若宿主未来提供 onQuotaReset 类原生 API、修复响后变更、或让后台任务抗重启，双通道可进一步简化；
- 思路 A（工作流内等待）留作备选通道，未纳入本期。
