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
| 1 | 2026-09-02T03:19:22Z | journal wake_bridge_fired #18 跨界恢复 |
| 2 | 2026-09-02T08:35:48Z | journal wake_bridge_fired #25/#27/#28 |
| 3 | 2026-09-02T23:40:18Z | 同上 + 实验文档 §5 |
| 4 | 2026-09-03T07:14:27Z | recorder CSV（空闲型穿越全程） |
| 5 | 2026-09-03T12:28:09Z | recorder CSV（活跃型穿越全程） |

（当前窗口 reset 2026-09-03T17:28:12Z 进行中，第三轮穿越采集由
watcher/recorder 自动续录。）

**≥2 quota epochs 真实长任务：成立**（跨 5 个已穿越窗口边界；epoch_id 机制 C2 于 09-02 落地后覆盖后两窗，CSV 留有 4 个 distinct epoch_id 记录）

## 1. RG-22-01：低余量长任务（部分验证——门机械全验证，全自动恢复环未整环跑）

- **PRESSURE/DRAINING 机械门生产实战**：2026-09-02T02:19Z 额度 9% 时
  quota-observe 机器自判 DRAINING、M4 闸机械停派（journal 在案；更早 02:13Z 13% 快照为 memory 级记录，journalled 02:19Z 独立支持同一结论）（"主会话自觉 hold"退役）；09-03 05:3xZ 22% PRESSURE 预算收缩
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

2026-09-02T08:3xZ 用户取消会话：桥被删（单次 CronDelete 成功——**首个
正样本**，此前 Phase 0 尝试全败；第三正样本是 09-02T19:56:08Z 对重建
桥的对账删除）→ **零 ghost**（宿主 automation 列表空，journal
bridge_deleted 在案）→ 同日新会话（journal 13:23Z / 北京 21:23）经
SessionStart + checkpoint 序列完整恢复（repo 零错误修改）。correctness 不依赖 wake 成功：实测成立。

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
| Update | ⚠️ 已知 CronUpdate glitch（v2.1 时代实测）；SCHED-01 复验未执行（并入 P0-HOLD/QC 矩阵覆盖）；retarget 保留为死路径备份 | 未测 |
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

> [补录 2026-09-05] 第 16 条已闭合：RG-22-06 双窗活跑于 2026-09-04/05 通过（任务 v22-rh06-be1301，见 §11 补录），stable 升格条件满足。

## 10. 仪器与数据资产

- 常驻 watcher pid 181160（C3 交付，ACTIVE 自适应）+ recorder CSV
  （50+ 行，两轮边界穿越全程）——持续采集，第三轮（17:28:12Z）自动入账。
- 实验文档 §5.1 双轮穿越佐证 + §5 六行证据表 = 机制裁决的实测背书。

## 11. Stable 收口补录：RH-06 双窗活跑（2026-09-04/05，PASS 14/14）

（本节为 stable 计划 ST-02b 补录；勾选依据 = stable 收口会话对原始账本的独立重演——events.jsonl 301 事件 + 控制面 quota/events.jsonl 2 事件 + state.json/session_facts.json，不采信执行侧自述。时间戳 UTC。）

**任务与载体**：v22-rh06-be1301 @ D:\BioWorkflows（干净项目会话，v2-dev b8f15aa hooks 面）；载体 = seclip-srna-bs-seq v0.1 计划（三子流程 24 单元，feature/seclip-srna-bs-seq-v0.1 分支仅本地提交）；执行侧证据表 `.glm-conductor/rh06/evidence_table.md`（对账件）。

**三 epoch 主表**（§5.12）：

| 证据 | Epoch N | Epoch N+1 | Epoch N+2 |
|---|---|---|---|
| epoch_id | glm:584fb348c3b822c9 | glm:61ff429c539c55f3 | glm:53353d462111d193 |
| provider 状态 | PRESSURE（15%→7%） | AVAILABLE | AVAILABLE |
| execution phase | DRAINING | NORMAL | NORMAL |
| automation_id | automation-2d2c80aa-…（armed 09-04T19:39:54Z） | 同一 | 同一 |
| 任务态（激活前→后） | executing（DRAINING 先于开跑，睡眠整边界） | waiting_quota → executing | waiting_quota → executing |
| registered_epoch_id | 584f（09-04T19:44:30Z 注册） | 61ff（延迟转态时更新） | 5335（09-05T02:41:42Z） |
| last_activation_epoch_id | —（未激活） | 61ff（标记#1，prev=null） | 5335（标记#2，prev=61ff） |
| consumed_quota_windows | 0 | 1（remaining=1） | 2（remaining=0，恰耗尽） |
| checkpoint | 19:44:02Z | 21:49:09Z | 03:24:40Z |
| 嵌套 CronCreate | 无 | 无 | 无 |

（相对 §5.12 模板缺 runCount / manifest refreshed 两行：宿主 runCount 为执行侧 CronList 观察（自动化已删不可重查），以判定段 journal fires=10 为行为性证据；manifest 新鲜由两轮 checkpoint/manifest 链与 manifest.json 在位佐证——并入判定段。）

**§5.13 十四项判定**（独立重演，全 PASS；计划清单 14 项——N+1/N+2 转态与 dup-fire 零激活/零消费各为两项，下文合并列举）：三互异 epoch / 两新 executable epoch / 同一 automation 全程 / runCount 增长（journal fires 10 拍跨 24h02m，首拍 09-04T20:41:53Z → 末拍 09-05T20:44:21Z；宿主 runCount 0→1→7 为执行侧 CronList 观察，自动化已删不可重查——以 journal 行为性增长为准）/ 零嵌套 CronCreate / 两轮 waiting_quota→executing（C5 域事件链：quota_waiting → quota_resumed + 激活标记 + 消费）/ 两激活标记（控制面恰好 2 条 quota_epoch_advanced，QC-07 幂等闸机械保证）/ consumed 恰 2 / 同窗重复 fire 零重复激活、零重复消费（fire#2 至下一 epoch 事件间 journal 零增量）/ 两轮 handoff checkpoint+manifest 新鲜 / watcher stop/absence 零状态破坏（工件重演：watcher.json 原子 stop_requested=true 且观测/心跳/世代字段保全、watcher.log 单实例锁冲突干净退出、任务账本零破坏；"nothing to stop" 输出与重启接管为执行侧 §5.10 受控观察，工件佐证）/ 清理独立性（成功分支）。

**终账解耦**：fires(journal)=10；cross-epoch resumes=2；consumed=2/2 —— arm/fire/create 零消费，§15.1 commit-point 恰两笔（write-ahead pending 先行 = RH-03 形状；representative_boundary_id = RH-04 形状）。

**完成与清理时间线**：finalizing 09-05T20:49:07Z → completed 21:38:27Z（via completion_gate，指纹 5faa6166 = ship 终审）→ tombstone 21:47:49Z（manual per RH-05 precedent，bridge_should_noop=true）→ 单次 CronDelete 成功 21:48:07Z（first and only attempt，order_evidence 记录在案）。Post-completion 桥 prompt 以 no-op 回合消化（诚实合并注记在 tombstone 事件内）。

**载体收口**（不影响 RH-06 判定）：24 单元 + R1/R2 完成；D3/D4 用户裁决取消（scope_narrowed）；终审一轮 fix-first 按用户裁决归档为 bs-seq TODO §5（a859fd0）后二轮 ship；三条任务级 WSL 终验 exit-0。

**出处注记**（诚实记录）：宿主 runCount、§5.6 resumed:false、§5.10 受控 stop 的 CLI 输出（"nothing to stop" / 重启接管）为执行侧宿主观察/CLI 记录——runCount 与 §5.6 有 journal 零增量主证兜底，§5.10 有 watcher 工件（watcher.json/watcher.log）佐证；独立复核原文与重演脚本存 stable 会话 `.glm-conductor/tmp/`（rh06_independent_verification.md / rh06_rederive*.py）。

**与 §9 第 16 条的关系**：本节即第 16 条的闭合记录——修正记账口径的桥活跑双窗 dogfood 已过，v2.2.0 stable 升格的运行时证据条件全部满足（RH-07 终审另按 stable 计划 ST-04 执行）。
