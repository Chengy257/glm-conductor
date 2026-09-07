# GLM Conductor v2.0.1 Release Hardening Patch & Release Plan

> **用途**：直接交付编码 Agent 执行  
> **目标分支**：`v2-dev`  
> **目标版本**：`v2.0.1`  
> **发布目标**：完成两个 release blocker 后，将 `v2-dev` 合入 `main`，发布 `v2.0.1`  
> **基线状态**：v2.0.1 H1–H8 已完成；现有测试约 775 cases，`validate_plugin` 14/14；CI 已覆盖 Ubuntu/Windows × Python 3.8/3.13  
> **原则**：本补丁只做发布前正确性收口，不提前实现 v2.1 Continuity Enforcement / Evidence Provenance 大改。

---

## 0. Agent 执行指令

本任务是 **v2.0.1 release hardening**，不是 v2.1 功能开发。

必须遵守以下边界：

1. 仅修复两个 release blocker：
   - **RB-1：Work Unit 不得在缺少新鲜验证证据时进入 `completed`**；
   - **RB-2：Completion Gate 必须支持 task-specific repository root，不能因多仓 workspace 根目录不是 Git repo 而整体降级**。
2. 不实现以下 v2.1 项：
   - SessionStart 恢复钩子；
   - 自动 scheduled wake / `/resume`；
   - `prepare_dispatch` 内置 quota provider 获取；
   - runtime `verify_unit()` / `verify_task()` subprocess wrapper；
   - review invocation provenance；
   - 多 orchestrator / 多会话同 task 并发；
   - 无界并行或扩大 `max_workers`。
3. 保留当前架构原则：
   - 主会话仍是唯一 orchestrator；
   - `dispatcher` 保持纯决策器；
   - `task_manager` 负责 I/O 生命周期编排；
   - `reconcile` 保持恢复建议 / 证据判断层；
   - Stop Gate 是 task-level `completed` 的唯一提交入口；
   - 默认串行、并行上限仍为 4。
4. 所有修改必须带测试；不得仅靠文档约定修复。
5. 不允许通过削弱 gate、扩大 fail-open 范围或移除现有 invariant 来“解决”测试失败。
6. 每个工作包完成后先运行定点测试，再运行全量测试。
7. 不直接在 `main` 开发；全部修改先落 `v2-dev`。

---

# 1. 发布前结论

v2.0.1 H1–H8 已经关闭上一轮三个 P0：

- `finalizing → completion gate → completed` 已形成状态机约束；
- route matrix / executor / review / ownership / verification 已形成跨字段 invariant；
- corrupt task 已通过 `discover_tasks()` 四分类进入可见、可 fail-closed 的强制路径。

同时，`task_manager`、lease TTL/recovery、unit-bound verification、quota deepcopy、task_id consistency、CI matrix 等 P1 也已基本完成。

本轮复审发现两个仍不适合带入 stable release 的问题：

| ID | 级别 | 问题 | 是否阻断发布 |
|---|---|---|---|
| RB-1 | P0 / Release Blocker | `finish_unit(outcome="completed")` 可以在没有新鲜 unit verification evidence 时机械进入 completed | **是** |
| RB-2 | P0 / Release Blocker | Stop Gate 使用单一 `ZCODE_PROJECT_DIR` / cwd 作为 Git root，多仓 workspace 下整个完成门走 `git_unavailable` fail-open | **是** |

完成 RB-1、RB-2 后即可发布 v2.0.1。其余 continuity/provenance 议题留 v2.1。

---

# 2. RB-1：Work Unit Completed 必须绑定新鲜验证证据

## 2.1 当前问题

当前 `runtime/task_manager.py` 的 `finish_unit()` 在：

```text
current == "running" and outcome == "completed"
```

时做：

```text
running → verifying → completed
```

随后释放 lease、移除 `dispatch.active`、保存 state、记录 `unit_finished`。

这只证明“走过了状态转换表”，**没有证明 parent-observed verification 实际存在**。

当前合法但错误的调用序列：

```text
prepare_dispatch
→ commit_dispatch
→ Agent 修改代码
→ finish_unit(outcome="completed")
```

可以完全遗漏：

```text
record_unit_verification(...)
```

但单元仍会成为 `completed`。

这会进一步影响 DAG：

```text
unit A 未真正验证
    ↓
A.status = completed
    ↓
dependency.deps_satisfied(...)
    ↓
refresh_readiness()
    ↓
下游 unit B 被提升为 ready
```

因此该问题会把“验证缺失”传播为“错误解锁下游任务图”，必须在 v2.0.1 发布前修复。

---

## 2.2 修复目标

建立如下 invariant：

> **Work Unit 只有在其全部 required verification command 都存在 unit-bound、status=pass、fingerprint=current 的新鲜证据时，才能进入 `completed`。**

最低证据条件：

```text
event == "verification"
AND event.unit == unit.id
AND event.command ∈ unit.verification
AND event.status == "pass"
AND event.fingerprint == current_unit_fingerprint
```

并且：

```text
for every required command in unit.verification:
    至少存在一条满足上述条件的 evidence
```

只满足一部分 required command 不得完成。

---

## 2.3 推荐实现

### 2.3.1 抽出共享 evidence helper

优先避免在 `task_manager` 和 `reconcile` 中复制两套证据匹配逻辑。

建议在：

```text
plugins/glm-conductor/runtime/reconcile.py
```

新增一个可复用的纯判断 helper，例如：

```python
def fresh_unit_verification(
    repo_root,
    task_id,
    unit,
    *,
    touched=None,
    events=None,
) -> dict:
    ...
```

推荐返回：

```python
{
    "ok": True | False,
    "fingerprint": "...",
    "required": ["cmd1", "cmd2"],
    "matched": ["cmd1", "cmd2"],
    "missing": [],
}
```

或等价结构。

必须满足：

- 不写 state；
- 不写 journal；
- 不修改传入 unit；
- 证据匹配规则与 `reconcile_interrupted()` 保持同一口径；
- `reconcile_interrupted()` 改为复用该 helper，而不是继续维护独立匹配逻辑。

### 2.3.2 fingerprint 口径

必须沿用现有 reconcile 语义，不在 v2.0.1 新造第二套 fingerprint 规则。

建议：

```text
touched
  ↓
ownership.classify_paths(touched, unit.ownership)
  ↓
owned_hits
  ↓
fingerprint.compute_fingerprint(repo_root, owned_hits)
```

如果现有 reconcile 对“无 owned_hits”有特殊恢复语义，则 completion helper 必须显式区分：

- recovery decision；
- normal completion evidence。

**正常 `finish_unit(completed)` 不允许因为 owned_hits 为空就跳过验证。**

如果 unit required verification 非空，则仍必须存在有效验证证据。

### 2.3.3 修改 `finish_unit()`

目标文件：

```text
plugins/glm-conductor/runtime/task_manager.py
```

当前：

```text
running + completed
→ verifying
→ completed
```

修改为：

```text
running/verifying + outcome=completed
    ↓
先检查 fresh verification evidence
    ↓
evidence 不满足
    → TaskManagerError
    → zero state mutation
    → lease 保留
    → dispatch.active 保留
    → 不写 unit_finished
    ↓
evidence 满足
    → 按合法 transition 完成
    → release lease
    → remove active
    → save
    → unit_finished
```

### 2.3.4 失败必须零副作用

这是本补丁的重要验收点。

例如：

```python
finish_unit(..., outcome="completed")
```

因 missing / stale verification 被拒绝时：

```text
unit.status 不变
dispatch.active 不变
leases.json 不变
state.json 不写
不产生 unit_finished
```

允许额外写一个明确的拒绝事件吗？

**建议不写。**

原因：`finish_unit()` 当前错误路径基本保持异常 + 零副作用语义；release patch 不应扩大 journal 事件面。

---

## 2.4 不要在本补丁实现的内容

不要把 RB-1 扩展成 v2.1 Evidence Provenance。

本轮仍允许：

```python
record_unit_verification(
    uid,
    command,
    fingerprint,
    status="pass",
)
```

由调用方传入结果。

v2.1 再做：

```text
runtime 实际执行 command
→ 捕获 exit_code
→ 自动计算 fingerprint
→ 签发 verification receipt
```

v2.0.1 只负责：

> **没有证据不能 completed。**

而不是：

> **runtime 已经能证明这个证据不是伪造的。**

---

## 2.5 RB-1 必须新增的测试

目标文件建议：

```text
tests/test_task_manager.py
tests/test_reconcile.py
```

至少新增以下用例。

### A. 完成门拒绝

1. `running + completed + no verification event` → `TaskManagerError`
2. `verifying + completed + no verification event` → `TaskManagerError`
3. evidence `status=fail` → reject
4. evidence `unit=other-unit` → reject
5. evidence command 不属于 `unit.verification` → reject
6. required 有 2 条命令，只验证 1 条 → reject
7. fingerprint stale → reject
8. legacy verification event 无 `unit` → reject
9. reject 时 state bytes 不变
10. reject 时 lease 不变
11. reject 时 `dispatch.active` 不变
12. reject 时无 `unit_finished`

### B. 正常完成

13. 单 required command + 新鲜 pass evidence → completed
14. 多 required commands 全部有新鲜 pass evidence → completed
15. running 状态有完整证据 → 可按现有合法 transition 路径完成
16. verifying 状态有完整证据 → completed
17. completed 后 lease 释放
18. completed 后 active 移除
19. completed 后 `unit_finished` 正确落盘

### C. DAG 集成

20. upstream 无 verification evidence → 不能 completed → downstream 不得被 `refresh_readiness()` 提升
21. upstream 完整验证并 completed → downstream 可提升 ready

### D. reconcile 共享 helper 回归

22. `reconcile_interrupted()` 与 `finish_unit()` 对同一 evidence 的 fresh/stale 判断一致
23. 相同 command、不同 unit 的 evidence 仍不能交叉复用

---

# 3. RB-2：Task-Specific Repository Identity / Root Resolution

## 3.1 当前问题

当前 Stop Gate 的 repository root 主要来自：

```text
ZCODE_PROJECT_DIR
or
cwd
```

随后一次性：

```text
git_touched_files(repo)
```

如果 ZCode 打开的 workspace 是：

```text
C:\Users\user\ZCodeProject
```

而真实 Git repo 是：

```text
C:\Users\user\ZCodeProject\glm-conductor
```

则 workspace 根本身不是 Git repo：

```text
git_touched_files(workspace_root)
→ OwnershipError
→ ENFORCEMENT DEGRADED
→ gate fail-open
```

这已经在 v2.0.1 dogfood 中真实发生。

因此必须把：

```text
workspace root == repository root
```

这一隐含假设移除。

---

## 3.2 修复目标

建立如下模型：

```text
Workspace
├── repo-A
│   └── .git
├── repo-B
│   └── .git
└── .glm-conductor / or task ledger
```

每个 task 必须能够知道：

```text
task.repository.root
```

Stop Gate 必须按 task 分别检查：

```text
Task A → repo-A → touched/fingerprint A
Task B → repo-B → touched/fingerprint B
```

不能再用一份 workspace-global touched 清单套所有 task。

---

## 3.3 State schema 扩展

建议在 task state 中新增：

```json
{
  "repository": {
    "root": "C:/Users/user/ZCodeProject/glm-conductor"
  }
}
```

### 字段要求

`repository`：

- optional，用于兼容已有 v2.0.0 / v2.0.1 pre-patch state；
- 新建任务推荐写入；
- `root` 为非空字符串；
- 建议落盘前规范化：
  - absolute；
  - `Path.resolve()`；
  - Windows 分隔符允许原样或统一 `/`，但读取时必须可恢复为本地 `Path`。

### 推荐 API

在 `runtime/state.py` 新增：

```python
def bind_repository_root(st, repo_root) -> dict:
    ...
```

或扩展：

```python
new_task_state(..., repository_root=None)
```

推荐两者至少有一个明确入口。

不要让调用方直接手拼 schema 成为唯一方式。

---

## 3.4 Repository root 解析优先级

Stop Gate 对每个 task 采用以下顺序：

```text
1. state.repository.root 存在
   ↓
   使用该 root

2. repository.root 缺失
   ↓
   旧兼容：尝试当前 ledger/workspace root

3. 当前 root 不是 git repo
   ↓
   不得自动随意选择多个 nested repo 中的任意一个
```

对于 legacy task：

- 如果 ledger root 本身就是 Git repo → 保持旧行为；
- 如果不是 Git repo → 可 degraded，但必须给出明确原因：
  - `repository_root_missing`
  - 或 `repository_ambiguous`
- 提示用户 / 主会话重新绑定 repository root。

**不要在存在多个 nested Git repo 时自动“猜一个”。**

---

## 3.5 Stop Gate 必须改为 per-task repo evaluation

目标文件：

```text
plugins/glm-conductor/hooks/stop_gate.py
```

当前倾向：

```text
active tasks
    ↓
global repo
    ↓
global touched
    ↓
evaluate every task
```

修改为：

```text
active tasks
    ↓
resolve repository root per task
    ↓
cache by repository root
    ↓
git_touched_files(task_repo)
    ↓
task fingerprint(task_repo)
    ↓
evaluate task
```

建议：

```python
repo_cache = {
    resolved_repo_root: {
        "touched": [...],
        "error": None,
    }
}
```

这样同一 repo 的多个 task 不需要重复调用 Git。

---

## 3.6 单任务降级必须隔离

这是 RB-2 的关键验收条件。

假设：

```text
Task A → valid repo
Task B → invalid/missing repo
```

不得：

```text
Task B git failure
→ entire Stop Gate return early
→ Task A 也被跳过
```

必须：

```text
Task A → 正常 enforce
Task B → gate_degraded(reason=repository_unavailable)
```

也就是说：

> **repository failure 的 blast radius 必须从“整次 Stop”缩小为“单 task”。**

仅钩子本身的致命异常仍保留现有全局 fail-open 兜底。

---

## 3.7 fingerprint 必须跟随 task repo

涉及：

```text
runtime.fingerprint.task_fingerprint(...)
```

或 Stop Gate 内当前指纹调用。

确保：

```text
Task A fingerprint
```

基于：

```text
Task A.repository.root
```

而不是 `ZCODE_PROJECT_DIR`。

否则 ownership 已修复但 verification/review stale 判断仍会基于错误 repo。

---

## 3.8 多顶层 task 本轮不扩展 attribution 能力

RB-2 解决的是：

> 不同 task 可以绑定不同 repository root。

本轮**不解决**同一个 repo 中多个 top-level active task 的 diff attribution。

v2.0.1 发布文档中建议明确：

```text
一个 repository 同时只支持一个 active top-level task；
该 task 内允许多个 Work Unit bounded parallel。
```

如果实现当前没有硬限制，可先作为 documented limitation，不要求本补丁增加全新锁。

v2.1 再考虑：

```text
task baseline
task-specific diff attribution
cross-task ownership
```

---

## 3.9 RB-2 必须新增的测试

目标建议：

```text
tests/test_state.py
tests/test_stop_gate.py
```

至少新增：

### State

1. `repository.root` 正常保存/加载
2. 非 string root → validate error
3. 空 root → validate error
4. legacy state 无 repository → 仍可读取
5. `bind_repository_root()` 正常规范化
6. state save 不因 Windows path 分隔符破坏

### Stop Gate 单仓

7. `repository.root` 指向真实 repo → ownership 正常检查
8. fingerprint 使用 repository.root
9. repo root 不等于 `ZCODE_PROJECT_DIR` 时仍可通过真实 gate
10. workspace root 非 Git，但 task repo 是 Git → 不得 `git_unavailable` 全局降级

### Stop Gate 多仓

11. workspace 下 repo-A / repo-B 两个真实 Git repo
12. Task A 绑定 repo-A，Task B 绑定 repo-B
13. A 的 touched 不应被 B 当作 out-of-scope
14. B 的 touched 不应被 A 当作 out-of-scope
15. 一个 repo 出错时另一个 repo 仍执行 gate
16. repo failure 只给对应 task 写 `gate_degraded`

### Legacy / Ambiguity

17. legacy task 无 repository，但 ledger root 为 Git repo → fallback 正常
18. legacy task 无 repository，workspace 非 Git → degraded 可见
19. 多 nested repo 且 task 无 repository → 不猜 repo
20. degraded reason 必须结构化且可测试

---

# 4. 建议工作包拆分

本补丁建议拆成 4 个 Work Unit。

---

## WU-P1：共享 Unit Verification Evidence Predicate

**目标**

把当前 reconcile 的 unit verification evidence 规则抽成共享、纯判断 helper。

**Ownership**

```text
plugins/glm-conductor/runtime/reconcile.py
tests/test_reconcile.py
```

**实施内容**

- 新增 shared fresh-evidence helper；
- required commands 必须全部满足；
- `reconcile_interrupted()` 改为复用 helper；
- 保留现有 recovery semantics；
- 不写 I/O。

**验收**

```text
python -m unittest tests.test_reconcile -v
```

---

## WU-P2：Enforce Evidence Before Unit Completion

**依赖**

```text
WU-P1
```

**Ownership**

```text
plugins/glm-conductor/runtime/task_manager.py
tests/test_task_manager.py
```

**实施内容**

- `finish_unit(outcome="completed")` 调用 fresh evidence helper；
- missing/stale/partial/wrong-unit evidence 一律 reject；
- reject zero mutation；
- success 保留当前 lease / active / journal 正常收尾；
- 增 DAG integration test。

**验收**

```text
python -m unittest tests.test_task_manager -v
```

---

## WU-P3：Repository Root State + Per-Task Stop Gate

**Ownership**

```text
plugins/glm-conductor/runtime/state.py
plugins/glm-conductor/hooks/stop_gate.py
tests/test_state.py
tests/test_stop_gate.py
```

**实施内容**

- state 增 optional `repository.root`；
- 提供明确 bind/create API；
- Stop Gate per-task root；
- touched / fingerprint 按 task repo；
- per-repo cache；
- 单 task repository failure isolation；
- legacy fallback；
- ambiguity 不猜。

**验收**

```text
python -m unittest tests.test_state tests.test_stop_gate -v
```

---

## WU-P4：Docs / Changelog / Release Closeout

**依赖**

```text
WU-P2
WU-P3
```

**Ownership**

```text
README.md
README.en.md
CHANGELOG.md
docs/architecture.md
docs/GLM-Conductor v2.0.1 连续性缺口分析与v2.1优化建议.md
plugins/glm-conductor/skills/orchestration/SKILL.md
plugins/glm-conductor/skills/enforcement/SKILL.md
scripts/validate_plugin.py   # 仅在需要新增机械 marker 时修改
```

**实施内容**

1. CHANGELOG v2.0.1 增加 release-hardening 条目：
   - unit completed 需要 fresh unit evidence；
   - per-task repository root；
   - multi-repo workspace completion gate 修复。
2. README：
   - 修正“主会话亲自验证”的 runtime 边界表述；
   - 明确 multi-repo workspace 已支持 task-specific repo root；
   - 明确同 repo 多 top-level active task 仍非正式支持面。
3. architecture：
   - 更新 `finish_unit` lifecycle；
   - 更新 Stop Gate root resolution；
   - 更新 degraded scope。
4. continuity gap dogfood 文档：
   - R4 标记为 **v2.0.1 release hardening 已修复**；
   - R1/R2/R3/R5 保留 v2.1。
5. 如果 validator 依赖文档 marker：
   - 增加最小必要 marker；
   - 不为了 validator 重写运行时代码。

---

# 5. 推荐状态流

## 5.1 Work Unit 正常路径

v2.0.1 patch 后建议实际流程：

```text
pending / waiting_dependency
    ↓ refresh_readiness
ready
    ↓ prepare_dispatch
ready + lease
    ↓ commit_dispatch
running
    ↓ Agent implementation
running
    ↓ parent runs required verification
record_unit_verification(...)
    ↓
finish_unit(completed)
    ↓ fresh evidence check
completed
    ↓
refresh_readiness()
    ↓
unlock downstream
```

注意：

- v2.0.1 patch 不要求持久化 `verifying` 中间态才能完成；
- `verifying` 仍保留给恢复 / 显式流程；
- 关键 invariant 是 **completed 前 evidence 必须真实存在于 journal 且 fresh**。

如果实施 Agent 希望把 `running → verifying` 做成显式持久化新 API，请不要在本轮扩大范围；优先完成最小 release-safe patch。

---

## 5.2 Task Completion Gate

```text
task.status = finalizing
    ↓
Stop
    ↓
discover tasks
    ↓
for each task:
    resolve task.repository.root
    ↓
    touched(task_repo)
    fingerprint(task_repo)
    ↓
    ownership
    verification
    review
    visual
    ↓
all pass
    ↓
commit_completion()
    ↓
completed
```

---

# 6. 兼容性要求

## 6.1 Legacy task state

v2.0.1 patch 不得让旧 state 因缺 `repository` 立即成为 corrupt。

允许：

```text
repository absent
→ legacy fallback
```

但：

```text
workspace/ledger root 不是 Git repo
→ degraded with actionable reason
```

不要：

```text
repository absent
→ silently choose first nested .git
```

---

## 6.2 Legacy verification events

保持当前 v2.0.1 H6 决策：

```text
verification event 无 unit
→ 不构成 unit completion evidence
```

不回退兼容。

---

# 7. 全量回归要求

完成 WU-P1 ~ WU-P4 后，依次运行：

```bash
python scripts/validate_plugin.py
python -m unittest discover -s tests -v
```

必须满足：

```text
validate_plugin: 14/14
all unittest: PASS
```

测试数应高于当前约 775；不要为了维持旧数字删除测试。

随后推送 `v2-dev`，确认 GitHub Actions 四个 job 全绿：

```text
ubuntu-latest / Python 3.8
ubuntu-latest / Python 3.13
windows-latest / Python 3.8
windows-latest / Python 3.13
```

任何一组失败都不得发布。

---

# 8. Release Candidate 验收场景

除单元测试外，建议 Agent 做 4 个真实 smoke。

## Smoke 1：未验证单元不可完成

```text
create task
→ create ready WU
→ prepare
→ commit
→ 不记录 verification
→ finish_unit(completed)
```

预期：

```text
TaskManagerError
unit still running/verifying
lease still present
active still contains unit
```

---

## Smoke 2：验证后正常完成并解锁下游

```text
WU-A → WU-B dependency
```

流程：

```text
A running
→ record fresh verification
→ finish A
→ A completed
→ refresh_readiness
→ B ready
```

---

## Smoke 3：Workspace root 非 Git，task repo 是 Git

结构：

```text
workspace/
└── repo/
    └── .git/
```

task ledger 可位于 workspace 层，但：

```text
repository.root = workspace/repo
```

Stop Gate 必须：

```text
使用 repo 的 git touched/fingerprint
```

不得出现全局：

```text
ENFORCEMENT DEGRADED: git_unavailable
```

---

## Smoke 4：两个 nested repos 隔离

```text
workspace/
├── repo-A/
└── repo-B/
```

Task A / Task B 分别绑定自身 repo。

修改 A、B 各一个文件后：

```text
A 不应看到 B 的 touched
B 不应看到 A 的 touched
```

如果 repo-B 故意损坏：

```text
A 仍执行正常 gate
B 单独 degraded
```

---

# 9. Commit 建议

建议保持小提交，便于 review / revert。

```text
1. refactor(runtime): share fresh unit verification evidence predicate
2. fix(runtime): require fresh unit evidence before work-unit completion
3. feat(runtime): bind task repository root and isolate stop-gate repo checks
4. test(runtime): cover release hardening evidence and multi-repo cases
5. docs(release): close v2.0.1 release blockers and update limitations
```

如果实现过程中测试/文档必须同步，可合并 1+2 或 3+4，但不要把全部补丁压成一个不可审查的大提交。

---

# 10. Review Checklist

代码完成后，独立 reviewer 必须逐项回答。

## RB-1

- [ ] 是否仍存在任何不经过 fresh unit evidence 就能写出 WU `completed` 的正常 API？
- [ ] required command 多于 1 条时是否要求全部 pass？
- [ ] stale fingerprint 是否拒绝？
- [ ] wrong-unit evidence 是否拒绝？
- [ ] legacy no-unit evidence 是否拒绝？
- [ ] reject 是否保持 state / lease / active 零副作用？
- [ ] downstream readiness 是否只信任真正完成的 upstream？

## RB-2

- [ ] Stop Gate 是否仍把 `ZCODE_PROJECT_DIR` 当成所有任务的唯一 Git root？
- [ ] task.repository.root 是否真正用于 touched + fingerprint？
- [ ] 同 workspace 不同 repo 的 touched 是否隔离？
- [ ] 一个 task 的 repo failure 是否会让其它 task 一起 fail-open？
- [ ] legacy state 是否仍可运行？
- [ ] 多 nested repo 且无绑定时是否拒绝猜测？

## Regression

- [ ] H1 finalizing gate-only completion 未被削弱
- [ ] H2 route invariants 未被削弱
- [ ] H3 corrupt fail-closed 未被削弱
- [ ] H4 task_manager transaction semantics 未被破坏
- [ ] H5 lease TTL/recovery 未回归
- [ ] H6 unit-bound evidence 未回退
- [ ] H7 quota cache/task_id consistency 未回归
- [ ] H8 Windows/Linux CI 全绿

---

# 11. v2.0.1 Definition of Done

只有同时满足以下条件，Agent 才可以宣布 v2.0.1 ready for release：

1. **WU completion invariant**
   - 所有 required unit verification 都存在 fresh unit-bound pass evidence；
   - 否则 `finish_unit(completed)` 必须拒绝；
   - 拒绝零副作用。

2. **Repository identity**
   - task 可绑定明确 repository root；
   - Completion Gate 按 task repo 获取 Git touched 与 fingerprint；
   - workspace root 不是 Git repo 时，只要 task repo 有效，gate 仍可正常强制。

3. **Failure isolation**
   - 单 task repository failure 不得让其它 task 一起跳过完成门。

4. **Backward compatibility**
   - legacy state 无 `repository` 仍可加载；
   - legacy unit verification 无 `unit` 仍按 v2.0.1 H6 规则不可信。

5. **Tests**
   - 所有新增测试通过；
   - 全量 unittest 通过；
   - validator 14/14。

6. **CI**
   - Ubuntu/Windows × Python 3.8/3.13 四组全绿。

7. **Docs**
   - README / architecture / CHANGELOG 与代码行为一致；
   - dogfood continuity 文档将 R4 更新为已修复；
   - 不把尚未实现的 SessionStart / automatic resume 写成已实现能力。

8. **Independent review**
   - reviewer verdict = `ship`；
   - review 后无代码改动，否则重新 review。

---

# 12. 发布步骤

当上述 DoD 全部满足后执行发布。

## 12.1 v2-dev 收口

```bash
git status
git log --oneline --decorate -n 20
```

确认：

- 工作区 clean；
- HEAD 包含 release hardening commits；
- version 仍为 `2.0.1`；
- README / CHANGELOG / plugin manifest 版本一致。

---

## 12.2 推送并确认 CI

```bash
git push origin v2-dev
```

等待四矩阵全部 success。

不得仅以本机测试代替 CI。

---

## 12.3 合入 main

推荐使用 PR：

```text
v2-dev → main
```

PR 描述至少包含：

```text
v2.0.1 Runtime Integrity Hardening

- H1-H8
- release blocker RB-1:
  fresh unit verification required before completed
- release blocker RB-2:
  task-specific repository root / multi-repo gate isolation
- tests: <final count>
- validator: 14/14
- CI: Ubuntu/Windows × Python 3.8/3.13
```

如仓库流程允许 direct merge，也必须先确保 `main` 没有 v2-dev 之外的新提交。

---

## 12.4 main 再跑一次 CI

合并后确认 `main` HEAD 的 GitHub Actions 全绿。

---

## 12.5 Tag

建议 annotated tag：

```bash
git tag -a v2.0.1 -m "GLM Conductor v2.0.1"
git push origin v2.0.1
```

Tag 必须指向通过 CI 的 `main` commit。

---

## 12.6 GitHub Release

Release title：

```text
GLM Conductor v2.0.1
```

Release notes 推荐结构：

```markdown
## Highlights

v2.0.1 is a runtime integrity hardening release.

### Completion integrity
- finalizing → completion gate → completed
- route/review invariants
- corrupt-task fail-closed handling
- fresh unit verification evidence required before Work Unit completion

### Task runtime
- task_manager transaction boundary
- lease TTL/generation/heartbeat/recovery
- unit-bound verification evidence
- dependency readiness refresh

### Multi-repository workspaces
- task-specific repository root
- per-task Stop Gate repository evaluation
- repository failures isolated per task

### Reliability
- quota cache deep-copy isolation
- task_id path/payload consistency
- Linux + Windows CI
- Python 3.8 / 3.13 coverage

### Known boundaries
- continuity resume trigger remains advisory until v2.1
- no SessionStart recovery hook yet
- quota status is still caller-supplied on dispatch
- verification/review provenance wrappers are planned for v2.1
- bounded parallel remains experimental
```

---

# 13. 明确推迟到 v2.1 的事项

v2.0.1 发布后，以下事项进入 v2.1，不得继续塞回 patch：

### Continuity Enforcement

```text
SessionStart
→ discover active/interrupted tasks
→ reconcile
→ recover leases
→ quota refresh
→ inject resume context
```

### Automatic checkpoint

```text
commit_dispatch / finish_unit
→ structured checkpoint
```

### Quota integration

```text
prepare_dispatch(quota_status=None)
→ provider/cache
→ AVAILABLE/PRESSURE/EXHAUSTED/UNKNOWN
```

### Evidence Provenance

```text
verify_unit()
verify_task()
review receipt
```

由 runtime 亲自观察：

```text
exit_code
command
fingerprint
timestamp
executor/reviewer identity
```

### Cross-top-level-task attribution

同一个 Git repo 多 top-level active task 的：

```text
baseline
diff attribution
cross-task ownership
```

继续保持非正式支持面。

---

# 14. 最终交付要求

编码 Agent 最终报告必须按以下格式返回：

```text
V2.0.1 RELEASE HARDENING REPORT

Branch:
Base commit:
Final commit:

RB-1 Unit Completion Evidence:
- implementation:
- tests:
- result:

RB-2 Repository Identity:
- implementation:
- tests:
- result:

Regression:
- validate_plugin:
- unittest count:
- Ubuntu 3.8:
- Ubuntu 3.13:
- Windows 3.8:
- Windows 3.13:

Docs updated:
- README:
- README.en:
- CHANGELOG:
- architecture:
- continuity dogfood note:

Independent review:
- verdict:
- findings:

Release:
- merged to main: yes/no
- main commit:
- tag v2.0.1:
- GitHub Release:
```

如果任一 release blocker、CI job 或独立 review 未通过：

```text
不得标记 released / completed。
```

---

## 最终目标

v2.0.1 发布后应达到：

> **Task-level completion 不能绕过 Completion Gate；Work Unit-level completion 不能绕过 fresh verification evidence；Completion Gate 不再依赖“workspace root 恰好就是 Git repo”这一脆弱假设。**

完成这两点后，v2.0.1 才具备作为稳定 runtime integrity release 合入 `main` 的条件；后续 v2.1 再集中解决 Continuity Enforcement 与 Evidence Provenance。
