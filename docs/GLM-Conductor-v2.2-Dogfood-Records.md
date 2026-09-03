# GLM-Conductor v2.2 Dogfood 实录（RG-22-01..08 证据映射）

> 工作单元 wu-22-12（executor=main）。本记录只登记**真实发生过**的事件
> 与机器证据（journal / CSV / commit / reviewer 收据），不虚构未运行的
> 场景。映射基准：实施计划 §RG-22-01..08 + §30 发布验收 16 条。

## 0. 任务与窗口总览

载体任务 `v22-loop-857f55a`（v2.2 Quota Continuity Control Loop Closure，
分支 v2-dev）：2026-09-01T15:52Z 创建 → 2026-09-03 C 系列实施面收官。
跨过的真实 quota 窗口边界（UTC，均有机器证据）：

| # | 窗口 reset_at | 证据源 |
|---|---|---|
| 1 | 2026-09-02T03:19:22Z | journal quota_resolved 序列 |
| 2 | 2026-09-02T08:35:48Z | 同上 |
| 3 | 2026-09-02T23:40:18Z | 同上 + 实验文档 §5 |
| 4 | 2026-09-03T07:14:27Z | recorder CSV（空闲型穿越全程） |
| 5 | 2026-09-03T12:28:09Z | recorder CSV（活跃型穿越全程） |

（当前窗口 reset 2026-09-03T17:28:12Z 进行中，第三轮穿越采集由
watcher/recorder 自动续录。）

**≥2 quota epochs 真实长任务：成立**（跨 5 个已穿越窗口边界、5 个以上
epoch 身份，C2 epoch_id 逐窗变化有 journal/CSV 记录）。

## 1. RG-22-01：低余量长任务（部分验证——门机械全验证，全自动恢复环未整环跑）

- **PRESSURE/DRAINING 机械门生产实战**：2026-09-02T02:13Z 额度 13% 时
  quota-observe 机器自判 DRAINING + wake_required=true，M4 闸机械硬拒
  新波次（"主会话自觉 hold"退役）；09-03 05:2xZ 22% PRESSURE 预算收缩
  1（D7 收口纪律执行）。
- **DRAINING 机械交接协议**：每窗口收口均走 checkpoint.md §0 重写 +
  manifest 刷新 + 账本对账（恢复三会话实测 repository>checkpoint 修复
  了 checkpoint 滞后于账本的场景）。
- **未整环验证的部分**：bridge armed → EXHAUSTED → waiting_quota →
  wake 注入 → quota-resume → 无用户输入续跑。C 系列期间桥未重建
  （本会话工具集无 CronCreate，obligation=degraded 落账），各次跨窗
  恢复由用户开会话 + checkpoint 序列完成。**留待 RG-22-06 活跑**。

## 2. RG-22-02：manual/notify warm-only（测试验证 + 纪律在案）

未安排 manual foreground 任务实测；机械保证在案：C7 授权预闸对
manual/notify 零调用零副作用（test_resume_consumption 预闸矩阵），
§22.1 不消费清单 docstring + reviewer 逐环验证。

## 3. RG-22-03：完成前收尾（部分——tombstone 机制在案，completion 活跑未发生）

任务尚未 completed（发布面收尾中）。机制在案：write_completion_tombstone
（M5）+ C8 stop 门 handoff-durable 检查（checkpoint+manifest 存在性）。
completion 时的会话侧单次 CronDelete 纪律已文档化（wu-22-10 封存裁决：
运行时零宿主调用是设计边界）。

## 4. RG-22-04：Create 失败分类（机械就绪 + v2.1 时代实证）

- C6 交付机械分类：PostToolUseFailure CronCreate × NESTED_REJECTION_
  MARKERS → scheduler_context.create=forbidden + origin=scheduled_task；
  PreToolUse 嵌套 fail-fast deny（单探针纪律，测试矩阵全绿）。
- v2.1 时代实证：scheduled-owned 会话曾观测嵌套创建拒绝（Phase 0 #13）。
- C8：forbidden/无桥 → continuity_degraded 不无限 block（INV-22-PB-07，
  3-Stop 红绿测试 + 对照锁定）。

## 5. RG-22-05：宿主关闭/任务跳过（实证成立）

2026-09-02T08:2xZ 用户取消会话：桥被删（单次 CronDelete 成功，第三
正样本）→ **零 ghost**（宿主 automation 列表空，journal bridge_deleted
在案）→ 次日新会话经 SessionStart + checkpoint 序列完整恢复（repo 零
错误修改）。correctness 不依赖 wake 成功：实测成立。

## 6. RG-22-06：双窗 until-done（部分实证 + 机械保证，活跑待办）

- **v2.1 M5 时代 28 连拍实证**（旧记账口径）：recurring 桥跨窗口 #2→#3
  边界存活、同会话注入、#18 跨边界恢复、retarget 从未需要 create——
  "第二窗恢复不依赖 create"的方向性证据。
- **C 系列机械保证**（测试锚定）：REUSE SAME BRIDGE 冲突语义（同
  boundary 异 automation 拒绝）、嵌套创建 hook 拒绝、§15.1 消费幂等
  （同 epoch 二次恢复零消费）。
- **修正记账口径下的活跑未发生**（桥未重建）。这是 stable 发布验收
  第 16 条唯一实质待办。

## 7. RG-22-07：能力矩阵（按 P0-SCHED 实测回填）

| 能力 | fresh interactive | scheduled-owned |
|---|---|---|
| Create | ✅ 3 次成功（含重建） | ❌ 嵌套禁止（Phase 0 #13） |
| Update | ⚠️ glitch（SCHED-01 失败，死路径备份 retarget） | 未测 |
| Pause | 未测 | 未测 |
| Delete | ✅ 3 正样本（含 1 次 scheduled-owned 自删） | ✅ |
| Recurring 连发 | ✅ 28 拍无故障（≤5.2s 偏差三连拍 SCHED-04） | 同会话注入 |
| Overlap | SCHED-06 未跑 | — |
| Offline | SCHED-10 部分（耗尽期排队批量注入 #8） | — |

C6 起 Create/Update/Delete 能力经 hooks 自动观测落账
（scheduler_capability_observed）。

## 8. RG-22-08：离线恢复（实证成立）

宿主 automation 长时间未触发的场景由 SessionStart + 账本恢复兜底：
三会话交接（09-02 两会话 + 09-03 恢复会话）均经
repository > checkpoint > memory 真相层级完成，零任务状态丢失。

## 9. 发布验收 16 条对照（摘要）

1-15 机械/测试/生产验证在案（全量 2076 绿、validator 15/15、各单元
reviewer ship 收据链完整）；**第 16 条（修正记账口径的桥活跑双窗
dogfood）为唯一实质待办**——建议 v2.2.0 以此记录发布 alpha/beta，
stable 等 RG-22-06 活跑通过后升格。

## 10. 仪器与数据资产

- 常驻 watcher pid 181160（C3 交付，ACTIVE 自适应）+ recorder CSV
  （50+ 行，两轮边界穿越全程）——持续采集，第三轮（17:28:12Z）自动入账。
- 实验文档 §5.1 双轮穿越佐证 + §5 六行证据表 = 机制裁决的实测背书。
