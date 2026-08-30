# GLM Conductor v2.0.1 子智能体派发恢复与并行执行诊断报告

- **日期**：2026-08-30
- **性质**：分析记录（仅记录，未实施任何修复；遵循仓库"分析文档入库、改动走实施计划"的惯例）
- **诊断环境**：本机（Windows 10 / Git Bash / ZCode CLI），插件缓存 glm-conductor 2.0.1，实证数据来自本机 ZCode 会话库与用户真实项目 `F:\Lab\课题\植物小肽数据库\ribo_workflow\MS_new`
- **关联文档**：`GLM-Conductor v2.0.1 连续性缺口分析与v2.1优化建议.md`（R1-R5 / A1-C1）、`architecture.md` §8（强制层）
- **结论一句话**：ZCode 原生已把派发记录完整落盘、同会话内子智能体可续接，但跨会话/崩溃后无进程级恢复路径；并行派发机制在本插件中完整存在却从未被启用，且实际执行绕过了整台决策机器——两个问题同根：**runtime 机制齐备，advisory 文本约束不住主会话的最简路径**。

---

## 一、问题一：子智能体派发中断能否恢复？（2026-08-30 实测）

### 1.1 用户问题

> 派发子智能体前记录，后续异常中断的情况下能否直接恢复原来的，而不是重新派发？

### 1.2 结论矩阵

| 场景 | 能否恢复 | 证据 |
| --- | --- | --- |
| 同一活跃主会话内，已完成的子智能体 | ✅ SendMessage 给 agentId 即可带原上下文续接 | 实测：派 flash-impl 测试子智能体记录暗号 → 完成后 SendMessage 追问 → 0 次工具调用、约 11K token（大部分缓存读）准确答出 `checkpoint-alpha-7734` |
| 跨主会话 / 主进程崩溃后 | ❌ SendMessage 报 `No active local_agent task found` | 实测：给已关闭会话 sess_a7125e2a 中的 wu4 子智能体（agent_c3412137）发消息失败；该会话的续作会话实际把 wu1–wu4 全部重派 |
| 兜底：捞回旧子智能体的完整进度 | ✅ ReadSessionContext 直接读子智能体持久化会话 | 实测：完整重建出中断 wu4 死前已完成的 6 项决策输入（XSD 字节比对、许可证证据、idx schema 判定等） |

### 1.3 原生持久化机制（"记录"一半原生已做，无需自建）

每次派发 ZCode 自动落盘三层记录：

| 层 | 位置 | 内容 |
| --- | --- | --- |
| 磁盘档案 | `~/.zcode/cli/agents/<主会话id>/<agentId>/metadata.json` | agentId、childSessionId（`sess_subagent_agent_<uuid>`）、parentSessionId、parentToolUseId、**完整派发 prompt**、派发时子智能体定义快照（模型/系统提示）、status、token 用量 |
| 完整对话 | `~/.zcode/cli/db/db.sqlite` 的 message/part 表 | 子智能体是 `session.task_type='subagent_child'`、带 `parent_id` 的正式子会话（本机 222 个）；ReadSessionContext 按 `sess_subagent_agent_<uuid>` 可读 |
| 模型 I/O 日志 | `~/.zcode/cli/rollout/model-io-sess_subagent_agent_*.jsonl` | 原始请求/响应流 |

### 1.4 关键坑：僵尸 status

主会话异常结束时，子智能体的 metadata status **永远停在 `running`**（实证：agent_c3412137），其会话库最后一条 assistant 消息 tokens 全零（模型调用被杀于出 token 之前）。本机 218 个档案分布：completed 203 / failed 13 / stopped 1 / **running 1（僵尸）**。

**推论**：status 字段不可作为完成/中断判据；恢复对账必须以 transcript 实际内容 + 仓库真实状态为准——与 continuity 既有原则 repository > checkpoint 完全一致。

### 1.5 未尽事项（诚实边界）

- 主会话崩溃后 `--resume` 恢复同一会话、能否 SendMessage 拉起它**自己的**旧子智能体——未直接测试（需重启会话）；从报错语义（"No **active** local_agent task"）与续作会话全部重派的实证看，大概率不可。
- `failed` 状态的子智能体能否在同会话内 SendMessage 续接——未测试（仅验证 completed）。

### 1.6 对 v2.1 恢复设计的落点

现实可行的"记录 + 恢复"形态（进程级恢复不可得，工作级恢复可得）：

1. **记录**：无需新增——原生档案按 agentId 组织，可直接枚举对账。建议 journal 的 `implementation_started` 事件补记 agentId 映射（更稳，免于按标题前缀猜）。
2. **中断后 reconcile**（三分）：
   - **已完成但结果未回主会话** → ReadSessionContext 捞最终报告，**不重派**；
   - **部分完成** → 旧 transcript 摘要 + 当前 diff 打成"先前进度包"喂给新派发（重派的是进程，不是工作）；
   - **未开始** → 全新派发。
3. 崩溃判据不用 status 字段，用"parent 会话存活性 + 最后消息 token 形状 + transcript 实际内容"。

---

## 二、问题二：可并行单元为何实际顺序执行？（2026-08-30 实测）

### 2.1 用户问题

> 拆解的任务可并行时，技能是否有尽可能并发的设计？我实际观察到大多仍是顺序执行。

### 2.2 结论

**并行机制完整存在，但从未被启用，且实际执行完全绕过了它。**观察到的顺序执行是三层原因叠加，非 bug 而是设计使然 + 行为绕过。

### 2.3 三层原因（逐层实证）

**第一层：默认并发预算就是 1，真实任务全部如此。**

- `runtime/dispatcher.py:87`：`DEFAULT_MAX_WORKERS = 1`（注释自述"保守：无显式配置时只跑一个 worker"）；
- `runtime/task_manager.py`：max_workers 缺省取 `state["dispatch"]["max_workers"]`（缺失按 1）；
- 实查 MS_new 三个真实任务（revfix-v2-6 / v2-7 / v2-remaining-20260828）的 state.json：`dispatch.max_workers` **全部 = 1**；
- orchestration SKILL §64-67 将有界并行（上限 4）标注 **experimental**，并写"不确定即不并行（串行永远合法）"——主会话读到此句的理性选择恒为串行。

**第二层：六道闸全部保守取向（设计如此）。**

`plan_dispatch` 准入链：quota → 压力 → ownership 冲突 → 租约 → 并发余量。其中：

- quota `UNKNOWN` → 整批 deferred（`unknown_suppressed`）；`PRESSURE` → 默认整批抑制；
- ownership 冲突判定（`patterns_conflict`）是字面前缀保守近似，docstring 自述"宁可误伤并行度，绝不放行可能同时写同一文件的两组 worker"；
- 技能层并行资格还要求"接口已固定、验证可分离、无顺序依赖"在拆解时被显式确认——单元不声明这些即"不确定"。

这是 rc1 并行安全里程碑（B9.1+B10.1）按"安全优先"设计的闸门，不是吞吐优先——**设计目标是"有界保守"，不是"尽可能并发"**。

**第三层（最关键）：实际执行绕过了整台决策机器。**

v2-7 任务 events.jsonl 实录：

```
implementation_started wu1-preflight-runner       06:23:16
unit_completed         wu1                        07:12:43   (49 min)
implementation_started wu2-resource-model         07:12:44   ← 前单元完成后 1 秒
unit_completed         wu2                        08:34:32   (82 min)
implementation_started wu3-runtime-matrix-freeze  08:34:40   ← 8 秒后
unit_completed         wu3                        09:18:18
implementation_started wu4-xsd-decision           09:18:18   ← 同秒；此后主会话中断，wu4 成僵尸
```

严格串行流水线；且**全程没有一条 `dispatch_prepared` / `dispatch_committed` 事件**，说明：

- `plan_dispatch` / `prepare_dispatch`（本支持一次决策多单元、批量取租约）**根本没被调用**；
- 主会话走了"派发 → 等待 → 验证 → finish_unit → 派下一个"的手工循环，然后按 continuity 事件表手动记 `implementation_started`——**该事件是手工事件，等于给最简路径留了后门**；
- Layer B 钩子（pre_tool_use.py）只在每次派发前注入 ownership 提醒（advisory），无任何强制并行动作。

另：wu1/wu2/wu3（preflight-runner / resource-model / runtime-matrix-freeze）作用域彼此分离，本是有界并行的典型候选——串行不是任务性质决定的，是预算与行为决定的。

### 2.4 并发上限事实（上一问题附带结论）

- ZCode 官方文档无子智能体硬并发上限（"同时启动多个时并行跑"），本地配置无相关项；
- 本机实证：17 个主会话出现过子智能体运行时间重叠（最多一个会话 11 对重叠）；
- 真实瓶颈：API 速率/额度窗口、机器资源、主会话上下文窗口；固定限制：子智能体不可嵌套派发、后台 Explore 只读。

---

## 三、根因归纳

两个问题同构，指向 v2.0.1 的同一个主题（与连续性缺口报告 R1-R5 一致）：

> **runtime 层机制齐备（档案落盘、批量决策器、租约、六道闸、finish_unit 证据门），但主会话是否走这些机制全靠技能文本约束；文本没有写成硬性动作指令的部分，主会话会选最简路径绕过。**

- 恢复问题：原生记录齐全，但没有 reconcile 流程去消费它 → 中断后重派；
- 并行问题：决策器齐全，但没有指令强制"批量决策后必须并发派发" → 逐个派发。

---

## 四、v2.1 建议（未实施，供规划引用）

底座大多现成：journal 的 unit 键、reconcile 按 unit 精确匹配（v2.0.1 H6）、任务级 join（全局验证+终指纹）均兼容乱序完成。

| # | 建议 | 说明 |
| --- | --- | --- |
| P1 | 拆解契约强化：单元 `ownership` / 依赖边 / `small` 变必填 | 现状"无声明=不确定=串行"，是并行打不开的根源 |
| P2 | orchestration 增加动作级指令："dispatch 组含多单元时必须同一回合以多个 Agent tool-use 并发派出（或 run_in_background），禁止逐个等待" | 现文本教算决策，未规定拿到决策后必须并发派 |
| P3 | 默认 `max_workers` 提到 2，或"不相交 ownership + 无依赖"单元自动获得并行预算 | 把"要不要并行"从主会话即兴判断变为确定性规则；experimental 标签待 dogfood 后摘除 |
| P4 | 允许并行单元全部完成后统一批量"单元验证 + finish_unit" | 与 RB-1 finish_unit 证据门不冲突，仅改时机；逐单元验证-收尾节奏是串行惯性的来源之一 |
| P5 | journal `implementation_started` 补记 agentId 映射 | 使中断 reconcile 可直接定位子会话，免按标题前缀猜 |
| P6 | 中断 reconcile 三分流程（见 §1.6） | 已完成→捞报告不重派；部分完成→进度包喂新派发；未开始→全新派发；判据用 transcript 内容而非 status 字段 |
| P7 | （可选）quota UNKNOWN 闸门分级 | 现状 UNKNOWN 整批 deferred；可改为"UNKNOWN 阻塞 >1 worker 而非阻塞一切"，与周期探针回退语义对齐 |

### 优先级建议

P5/P6 与连续性缺口报告的 A1（SessionStart 恢复注入）、A2（自动检查点）同线，合入"连续性 enforcement"主线；P1-P4 为"并行启用"独立线，建议 P2（纯文本改动、零代码）先行验证行为改变，再推 P3 预算调整。

---

## 五、验证方法附录（复现本报告结论）

```bash
# 子智能体档案状态分布
python3 -c "import json,glob,collections; print(collections.Counter(json.load(open(f,encoding='utf-8')).get('status') for f in glob.glob(r'C:/Users/user/.zcode/cli/agents/*/*/metadata.json')))"

# 会话库中父子挂载与并发重叠
sqlite3 ~/.zcode/cli/db/db.sqlite "SELECT id,parent_id,title FROM session WHERE task_type='subagent_child' LIMIT 5"

# 真实任务派发节奏
python3 -c "import json;[print(json.loads(l).get('event'),json.loads(l).get('ts','')) for l in open(r'<task-dir>/events.jsonl',encoding='utf-8')]"
```

（本机 plain `python`/`py` 启动器损坏，须用 `python3`。）
