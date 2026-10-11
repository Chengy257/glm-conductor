# Wake-Topology Probe Battery — 宿主调度行为实测报告(v2.5.2 设计取证)

- 日期:2026-10-11(本机,ZCode 3.14.0.7681)
- 会话:glm-conductor 主仓库工作会话(探针创建会话 = 本会话)
- 目的:为 v2.5.2「跨额度窗口精确唤醒」修复方案决定宿主行为事实
- 探针:`wake-topology-probe-A`(id `automation-25bf63ab-c727-4e2a-8e10-dacb5f31ba4d`)

## 时间线

| 本地时刻 | 动作 | 结果 |
| --- | --- | --- |
| ~01:26 | CronList | 工作区干净(0 automation) |
| ~01:28 | CronCreate one-shot(delayMinutes=2, recurring=false) | ✅ 接受;`nextRunAt = anchorAt + 120000ms`;scheduleRule 形态 `{unit:minute, interval:2, hour, minute, anchorAt}` |
| ~01:29 | CronCreate #2(同会话,P-C 复测) | ❌ 逐字拒绝:`Cannot create a scheduled task inside a session that already belongs to a scheduled task. Ask the user to start a new chat to create another scheduled task.` |
| ~01:30 | CronUpdate(title,P-B 前半) | ✅ 接受,调度未动 |
| ~01:31 | CronUpdate(cron 字段→`41 1 11 10 *`) | ✅ nextRunAt 精确移到新槽位 01:41:00.000(整秒对齐) |
| ~01:32 | CronUpdate(intervalUnit=minute, interval=2,P-B 主测) | ✅ 接受;**anchorAt 重置为更新时刻,nextRunAt = 新 anchor + 120000ms(ms 精度)**;recurring 隐式翻 true |
| 01:39:08 | probe-A 首次触发(runCount=1) | 以**会话中途续入**形式注入运行中回合 |
| 01:39 后 | 多次 CronCreate / CronUpdate / CronDelete 发射尝试 | **全部退化为重复 CronList**(本回合内 8 次;跨回合续测再 2 次+) |
| ~01:41 / ~01:43 / … | 后续触发(runCount=2,3,4,…) | 锚定 2 分钟周期准时,分发抖动 ~10-20s;部分以中途续入注入 |
| ~01:52 | 派发 general-purpose 子代理执行 CronDelete | 子代理工具集**不含任何 Cron 工具**(主会话专属),0 次调用即返 |

## 裁决

| 事实 | 裁决 | 证据 |
| --- | --- | --- |
| **P-C 同会话二建拒绝** | ✅ 复现(2026-10-03 结论回归验证) | 逐字报文;宿主出路 = 新会话 |
| **P-B 活跃 automation 重定** | ✅ 实锤 | cron 槽位与 interval 双形态均重锚;interval 形态 = 更新时刻 + interval(ms 级),隐式 recurring=true |
| **recurring 活性** | ✅ 实锤 | 连续按锚定周期触发(runCount 1→4+),触发后恒有 nextRunAt |
| **P-D 触发后续入/新回合的变更发射退化** | ✅ 新发现(与 W0 尖刺 "CronList tool-call loop" 同款,第二次独立复现) | 首次触发前 CronUpdate×2 成功;首次触发后 CronCreate/Update/Delete 发射全部退化为 CronList,跨回合持续;非 Cron 工具(Read/Write/Edit/Bash/TodoWrite/Agent)全部正常 |
| **子代理 Cron 能力** | ❌ 无 | 子代理无任何 Cron 工具 |

## 开放项

- **P-A(已触发 one-shot 是否释放会话归属)**:未测得——P-D 退化使创建尝试无法发射。推断(据 P-C 报文"归属绑定 automation 存在性、删除才释放"):触发不释放。仅影响 T1(one-shot 链)回退路径,需未来在**全新会话**中探针验证。

## 设计含义(v2.5.2)

1. **首触发前,归属会话对载体拥有全部变更能力;首触发后,变更能力实测不可用**(机制未定:宿主侧工具回收 vs 发射退化——两种解释在所有观测上等价,设计按保守事实处理)。
2. 由此,**精确重定(CronUpdate interval)的可靠窗口 = 载体创建后、首次触发前**。T2 可重定载体设计据此修正为「**延期武装**:载体在 enter_waiting_quota(观测 EXHAUSTED + reset_at)时以 interval=precise_wake.delay 创建——该创建必然处于首触发前,重定精度 100% 可达」;启动时不再预建心跳载体(arm-before-work 不变式重述为"无人值守恢复开始前必武装")。
3. 首次精确唤醒后,载体处于触发后态:不再尝试重定(避免必然失败的调用);其间隔继承上次精确值(≈ 一个额度窗长度),后续窗口以该节奏探测——远优于现状的任意发明周期,且零失败调用。
4. 续入形态注入本身无害于 no-op 分支(active→no-op 零 Cron 调用);变更只在空闲会话的唤醒轮/停靠轮里发生。
5. 每次触发 = 一次模型轮 = 额度消耗:无人值守探针的周期选择要把"触发成本"计入契约默认值。

## 清理终态

- probe-A 为 2 分钟 recurring,**主会话与子代理均无法删除**(P-D + 子代理无工具)。待用户自 Automations 页面手动删除(id 见上)。
- 2026-10-11 12:24 更新:用户已手动删除 probe-A;**删除后本会话 Cron 变更发射立即恢复**(后续 CronCreate O1 成功)。

## 补充取证:PA-PROBE O1(2026-10-11 12:21-12:25)

- O1 = 一次性探针(`automation-459ea629-673d-45b4-8792-564bc0f03b18`,delayMinutes=2),创建成功(此时会话名下无 automation——发射正常再次确认)。
- 12:23:40 按计划触发(抖动 15 秒):`lifecycleStatus: completed`、`runCount: 1`、`enabled: false`——one-shot 触发后进入完成态且不再触发。
- **P-A 主测结构性自阻**:P-A 的问题("会话名下有已触发 one-shot 时,创建第二个是否被接受")的待测状态恰是变更发射退化状态——两次刻意 CronCreate 尝试(含一次排除 prompt-priming 的重试)全部退化为 CronList。测试被自己的前提挡死,P-A 判定为**结构性不可测**,答案不再作为设计依赖。
- **三态发射规律(决定性事实,13+ 次实测零例外)**:

| 会话状态 | Cron 变更发射 |
| --- | --- |
| 名下无 automation / 有但从未触发 | ✅ 全部正常 |
| 名下 automation 已触发(recurring 触发后 / one-shot completed) | ❌ 全部退化为 CronList |
| automation 被删除后 | ✅ 立即恢复 |

- 机制开放:宿主侧回收 vs 模型侧发射退化(探针 prompt 恒以工具清单开头且 CronList 居首,存在 priming 嫌疑;生产唤醒 prompt 不必如此写)。**设计按保守规律处理,同时加"唤醒 prompt 动作优先、绝不枚举工具清单"规则作逃生门。**
- O1 残留为完成态/已禁用/不再触发(零循环风险),待用户顺手删除。

## 补充取证:后台睡眠唤起通道(P-B1/P-B2,2026-10-11 09:01-09:12Z)

绕开 automation 状态机的第二条通道——Bash `run_in_background` 任务的"退出即唤起"机制:

- **P-B1(90s 机制验证)**:后台 `sleep 90` 精确睡满 90.000s(09:01:01Z→09:02:31Z,exit 0);任务退出在**空闲会话**中自动唤起模型轮,退出→新回合开跑延迟 **11 秒**(09:02:31Z→09:02:42Z)。✅ 机制成立。
- **P-B2(660s 时长验证)**:660s 完整睡满(09:01:01Z→09:12:01Z,exit 0),**未被前台 600s timeout 上限截杀**(该上限只约束前台调用的 timeout 参数,不绑定后台任务);唤起延迟 **8 秒**。✅
- **时长上界**:不另做长时间探针(用户裁决:后台命令不被 kill 即成立,11min→5h 外推接受);生产等待 ≤5h06m(窗口+宽限)。
- **已知弱点**:后台任务无 "survives app restarts" 承诺(Cron automation 有)——应用关闭/重启预计杀死睡眠进程 → 由 6h recurring 地板兜底(最迟一个周期内接住)。
- **设计落点(终态)**:双通道唤醒 = 启动即建 6h recurring 载体(地板,抗重启,终身不变)+ 停靠轮发后台 sleep 到 reset_at+300s(精度层,秒级,零 automation 交互);唤醒协议统一(睡眠退出与地板触发同分支树);三态规律/一会话一归属/删旧建新全部不再触碰。详见 `docs/roadmap/V2_5_2_PRECISE_WAKE_AND_MODEL_ADAPTATION_PLAN.md` §2。(注:地板间隔后经用户裁决改为默认 1h 并入启动问答确认;精度层进一步升级为「启动即武装 + 每轮滚动续臂」——以 roadmap v4.1 为准。)

## 补充取证:端到端彩排——真实窗口睡眠唤醒(P-R1,2026-10-11 09:13-09:39Z)

v4.1 精度层在真实额度窗口上的全链彩排(单窗账户,five_hour 窗):

- **武装**:09:13:01Z 发起后台 `sleep` 1539s,目标 09:38:40Z = 当时观测窗口 reset 09:33:40Z + 300s 宽限(睡眠秒数由 bash 动态计算,模型零算术——即契约睡眠模板的实弹形态)。
- **唤醒精确性**:**目标 09:38:40Z,实测唤醒 09:38:40Z——零误差**(秒级对齐;P-B1/P-B2 的短时长结论在 25.7 分钟时长上保持;唤醒→新回合开跑延迟 ~9s,与 P-B1 的 8-11s 一致)。
- **额度恢复签名**:唤醒轮 provider 查询 → `AVAILABLE,used 5.0%`——reset 边界后额度如约恢复,「醒来有活干」。
- **新窗锚定签名(F8 活体实证)**:新窗口 `reset_at = 2026-10-11T14:33:42Z` = 锚定调用 09:33:42Z + 5h(**精确到秒**)。锚点归因:旧窗 reset(09:33:40Z)后 2 秒即有新窗首次消耗——与本彩排所在会话的当轮起始(用户放行消息触发的主会话调用)吻合。「窗口由首次调用锚定 +5h、reset_at 全程可知」第一次在真实窗口上对上账;这正是自锁/滚动续臂设计的物理基础。
- **等待期连续性**:睡眠的 25.7 分钟内会话完全空闲、零模型轮——连续性完全由宿主后台进程承担,F4(额度耗尽时模型无响应)场景下的承载方式得到真实验证。
- **结论**:精度层端到端成立:武装 → 零误差唤醒 → 恢复可见 → 新窗 reset 可知,四环节无一落空。彩排期间唯一在场的 automation(pa-probe-O1)为完成态未参与,证明睡眠通道与 automation 状态机完全正交。
