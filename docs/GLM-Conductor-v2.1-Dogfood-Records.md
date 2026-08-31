# GLM Conductor v2.1 第二批 Dogfood 实录（M4-M6）

> 任务 `v21-m4m6-b2f94c`（v2.1 第二批收口：wave 并行 / 额度授权续跑 / 验证与审查溯源），2026-08-31，主会话亲历实录。
> 本批延续第一批传统——**用本批正在实施的新机制编排本批自己的实施**。以下五场景（A-E）全部来自真实账本（`.glm-conductor/tasks/v21-m4m6-b2f94c/`）与宿主运行时观察，非构造数据；每场景按 **时间线 / 观察 / 机制对应 / 结论** 四段记录。
> 时间均为 UTC（`Z`）；「窗口 1308」为当日 Coding Plan 额度窗口标识。

---

## Dogfood A — 长任务 / 后台 worker：wave 全生命周期

### 时间线

1. 单元派发：wu-21-08 经单单元 `prepare_dispatch` 派出（wave 形态之外的首个实施单元）。
2. 第一波：`wave-18467eb95c0a` 双工（wu-21-09 + wu-21-12）并发派出。
3. 第二波：`wave-a6d0d1a3b146` 双工（wu-21-10 + wu-21-13）并发派出——后阵亡于窗口 1308（见 Dogfood B）。
4. 第三波：`wave-25919b70126d` 双工续作（wu-21-10 + wu-21-13 重派，见 Dogfood B）。
5. 三波全部随成员完成而收口。

### 观察

- wave launch contract 实际遵守：每波两个单元在**同一回合并发派出**，没有出现「等第一个返回再派下一个」；主会话在并行写相期间只做验证规划与结果收集。
- PostToolUse 自动记账实证：每次 launch 成功后 journal 出现 `agent_launched`（tool_use_id / permit_id / agent_id 绑定），permit 文件被原子 rename 消费——**主会话未手写任何一条 launch 事件**。
- 三波全部由 `finish_unit` **自动关闭**（`wave_closed`）：没有任何一次手工 wave 清理。

### 机制对应

- `task_manager.prepare_dispatch_wave`（CLI `wave-prepare`）：一次调用签发整批 permit + marker（M4，wu-21-08）。
- wave launch contract（§11.5）：wave.units > 1 同回合并发派出。
- PostToolUse runtime-observed 生命周期（M2）：`agent_launched` 自动记账 + permit 原子消费防重放。
- `_close_finished_waves`：成员全部终态/verifying 时 `finish_unit` 自动关 wave。

### 结论

wave 事务层在真实三波九单元次派发中零人工干预闭合——批量签发、并发合同、自动记账、自动关 wave 四环节全部按设计工作；「等第一个返回再派下一个」的旧习惯没有被触发过，说明合同已内化为编排纪律。

---

## Dogfood B — agent run 崩溃恢复：窗口 1308 中断与人工裁决

### 时间线

1. 窗口 1308 内，第二波（wu-21-10 + wu-21-13）两 worker **运行中死亡**（额度烧穿导致的会话终止）。
2. 账本对账：每个 worker 只有 `agent_launched` 一条记录——运行中死亡，结果回收通道不存在。
3. `reconcile_agent_run` 对两个单元判 `manual_ruling`（兄弟单元残留混在同一工作树，残留无法唯一归属）。
4. 主会话逐文件归属裁决：13 个文件 100% 归属到两个单元的 ownership 声明内，无越界残留。
5. 两单元 running → ready 重派；残留改动作为进度包随新规格携带（SendMessage 对失败 agent 不可用——宿主实测，进度包只能走规格文本）。
6. 续作 worker（第三波 `wave-25919b70126d`）核对并采纳残留，同时修复前人 2 处实质缺陷：resolver 异常 cause 硬编码、stop_gate 测试断言漂移。

### 观察

- **H2 实证**：后台 worker 运行中死亡时，账本只见 launch 确认——PostToolUse 只见 launch ack（第一批 §2.3 宿主硬约束），结果与部分产出全部不在账本里。
- reconcile 没有瞎猜：兄弟残留混树（两个单元的半成品在同一工作树）时拒绝自动分类，交主会话裁决——`manual_ruling` 是四分中唯一的人工出口，且这次用对了。
- 残留不是垃圾：续作 worker 把前人残留当作进度包核对采纳，且发现并修复了前人实现中的 2 处实质缺陷——**恢复重派没有丢失已完成的工作，反而获得了二次审查机会**。
- SendMessage 对已失败的 agent 不可用（宿主实测）：崩溃续作不能依赖原会话续接，进度包必须落在规格文本里。

### 机制对应

- H2（§2.3 宿主硬约束）：后台 PostToolUse 只见 launch 确认，结果回收 = 模型转述 + 原生档案对账。
- `reconcile_agent_run` 四分（M3）：证据优先级 repo 残留 > 新鲜验证 > 原生档案；混树场景正确落 `manual_ruling`。
- ownership 逐文件归属裁决：主会话按 state.json `ownership.files` 声明核对残留——声明先行的价值在崩溃恢复时兑现。
- running → ready 对账转换 + 进度包重派（§69 恢复对账的 wave 化续作）。

### 结论

崩溃恢复链（账本对账 → reconcile 分类 → 人工裁决 → 进度包重派 → 续作核对）全程可用且每一环都必要；「残留即进度包」让中断成本从重做降级为复核。修复前人 2 处实质缺陷是意外收获——独立的续作者以全新上下文审视残留，等价于一次被动交叉审查。

---

## Dogfood C — 额度耗尽 / 手动恢复：wu-21-11 的宿主行为实锚

### 时间线

1. 02:42Z：额度 PRESSURE（余 5%）→ 主会话落 checkpoint，向用户申请并获 `auto_once` 授权（`authorization.source == "user"`，窗口预算 1 窗），创建一次性 wake automation。
2. 04:25:41Z：wake 触发续作（见 Dogfood D）。
3. 06:01Z：双工 wave 在窗口 1308 烧穿额度，两 worker 阵亡（见 Dogfood B）。
4. 预算已耗（1/1 窗）：按策略**不再创建任何 automation**——用户手动刷新额度窗口 + 手动续会话恢复任务。
5. 当时 wu-21-11（授权续跑链）尚未落地——它正在被死掉的 worker 实施；主会话以手动流程续命整批任务。

### 观察

- PRESSURE 检查点纪律有效：5% 余量时落 checkpoint + 申请授权，为后续自动续作保留了启动条件。
- 授权与预算机制在 runtime 未落地时由主会话**手工执行了等价流程**：授权来源记录（用户）、预算（1 窗）、耗尽后停建 automation——这套手工流程随后被 wu-21-11 机制化。
- 预算耗尽后「不再建 automation」被严格遵守：宁可手动，不做无预算的自动唤醒。

### 机制对应

- `execution_policy.continuity`（M1 事实源）：auto_once 授权 + `consumed_quota_windows` 预算。
- 一次性 wake（recurring=false、maxRuns=1）：与 Dogfood D 同一 automation。
- 预算耗尽 → 停建 automation → 降级人工/SessionStart 恢复：正是 wu-21-11 的 `waiting_user` 耗尽语义。

### 结论

本场景是 wu-21-11 规格的**宿主行为实锚**：四态授权、窗口预算、耗尽停建、手动兜底——机制设计里的每个分支都先在这场实录中被主会话手工走过一遍。runtime 随后做的只是把这套被验证过的手工流程确定性化。

---

## Dogfood D — auto once：wake 桥首次全自动闭环

### 时间线

1. 04:17:05Z：额度窗口重置。
2. 04:25:41Z：一次性 wake 准点触发（窗口重置 + 8.5min 宽限——按 `max(reset)+grace` 规划）。
3. wake prompt 注入后：主会话从 prompt 内置信息恢复全部上下文（task_id、账本/仓库根、恢复首步、额度口径、预算状态、红线），无需用户补充任何信息。
4. `quota-resolve` 确认 AVAILABLE（99%）→ 自动续作成功。

### 观察

- **wake 桥首次全自动闭环实证**：耗尽 → 授权 → wake → 自足恢复 → 续作成功，全程无人工介入。
- 宿主实锚（三条，均已回写进 wu-21-11 与技能文档）：
  - wake = **同会话续行**——session id 不变，prompt 以用户 turn 注入；
  - **SessionStart 不重放**——恢复上下文必须全部内置在 wake prompt 里（自足性不是优化项，是正确性前提）；
  - 钩子快照在 **wake 边界刷新**到已装插件版本——wake turn 起 `agent_launched` 自动记账生效（第一批内钩子未挂载的会话，从 wake 起进入强制面）。

### 机制对应

- `scheduler.py` 恢复规划（alpha3）：EXHAUSTED → `max(reset)+grace` 精确唤醒（本例 8.5min 宽限内准点）。
- `quota_wake_prompt`（wu-21-11 固化形态）：自足 prompt 的字段清单即本场景实际消费的信息集。
- 钩子快照边界刷新：宿主行为实锚，解释了「钩子仅新会话生效」与 wake 的交互——wake turn 等价于一次钩子重挂载点。

### 结论

一次性 wake automation 是「跨额度窗口续跑」的正确桥形态：maxRuns=1 消灭了自动化残留与幽灵唤醒风险，自足 prompt 消灭了对会话记忆的依赖。本场景与 Dogfood C 合起来构成完整证据链：**有预算时全自动、无预算时停建等人工**——两种路径都按设计收敛。

---

## Dogfood E — 并行授权：max_workers=2 的首次真实双工

### 时间线

1. 用户简报授权 `max_workers=2`（经 `policy-set-parallel` 落盘 `execution_policy.parallelism`）。
2. 双工 wave（Dogfood A 的三波）首次激活即成功——无回退串行事件。
3. ownership 两两相交的单元组合被正确 `deferred`（reason=`ownership_conflict`），未被强行并行。
4. 并行对窗口消耗 ~2×：99% 余量在 1.5h 内耗尽（双工烧穿窗口 1308，衔接 Dogfood B/C）。

### 观察

- 双工吞吐真实可见（两单元同时写相），代价同样是真实的：**并行额度消耗约 2 倍**——1.5h 烧穿一个满窗。
- ownership 相交单元的 deferred 判定零失误：宁少并行不越界的保守近似（字面前缀相交即冲突）在真实单元组合上表现正确。
- 教训（已写入编排纪律）：PRESSURE 检查应在**每次 wave prepare 前**跑——`quota-resolve`（CLI）现已可用，wave-prepare 缺省即触发 resolver，派发面不再依赖主会话记得先查额度。

### 机制对应

- `execution_policy.parallelism`（M1）+ `policy-set-parallel` CLI：授权事实源（max_workers>2 才须 source=user，2 在标准授权内但本批仍由用户简报显式给出）。
- `plan_dispatch` ownership 闸（§66 保守近似）：相交 → `ownership_conflict` deferred。
- 预算折算（wu-21-09）与 resolver 缺省解析（wu-21-10）：wave prepare 前的额度检查自动化。

### 结论

默认并发 2 的授权模型（用户简报 → 落盘 → 事务层接线）在真实负载下成立；2× 消耗数据为「PRESSURE 折半（budget 1）」的预算矩阵提供了经验锚点——并行不是免费的，预算矩阵必须随行。ownership 闸的保守方向（相交即 deferred）在双工场景下没有产生一次误并行。

---

## 汇总

| 场景 | 验证的机制 | 关键宿主实锚 |
| --- | --- | --- |
| A 长任务/后台 worker | wave 事务全生命周期、launch contract、自动记账、自动关 wave | PostToolUse 只见 launch ack（H2） |
| B 崩溃恢复 | reconcile 四分、manual_ruling、进度包重派 | SendMessage 对失败 agent 不可用；残留即进度包（修复前人 2 缺陷） |
| C 额度耗尽/手动 | 四态授权、窗口预算、耗尽停建 | wu-21-11 规格的宿主行为实锚（手工预演 → 机制化） |
| D auto once | 一次性 wake、自足 prompt | wake=同会话续行；SessionStart 不重放；钩子快照 wake 边界刷新 |
| E 并行授权 | max_workers=2、ownership deferred、预算折算 | 并行消耗 ~2×（1.5h 烧穿满窗）→ PRESSURE 检查前置于每次 wave prepare |

五场景共同指向一个结论：v2.1 第二批的三个里程碑（M4 wave / M5 额度决策 / M6 溯源）不是纸面设计——它们的每个分支都在编排本批自身的过程中被真实触发过，且触发方式与设计假设一致（两处宿主行为差异——SendMessage 不可用与钩子快照边界——已回写进机制文档）。
