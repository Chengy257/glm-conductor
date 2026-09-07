# ZCode Scheduled Task Retime Experiments（v2.3.0 W0）

**日期：2026-09-07 ~ 2026-09-08**  
**目的：冻结 v2.3.0 Global Quota Clock 所依赖的 ZCode 宿主调度事实（计划 §1 事实 5–8 的复验 + ZC-01..04 补做）**  
**状态：✅ 完成（ZC-02 挂起为发布前补做项；其余全部成立）**  
**实验工具：`C:/Users/user/ZCodeProject/zc-w0/observe.py`（id 定向、单列 UPDATE、read-back 校验）**

---

## 1. 既往凭据（2026-09-07，会话 sess_86d76e30）

归档：`C:/Users/user/ZCodeProject/cron-selfupdate-test/`（result.md / exp-c-timeline.md /
automation-backup.json）；memory：zcodeproject 项目 `reference-zcode-cron-automation-facts`。

已冻结事实（不重复实验，本轮仅在活体上抽验关键两条）：

1. 触发 = prompt 投递回创建 automation 的绑定会话（target_task_id / automation_runs.session_id）；
2. 一个会话只能绑定一个 automation（completed 行被宿主清理后绑定释放）；
3. 触发回合内 CronUpdate/CronDelete 9/9 退化为 CronList（不可用）；
4. interval 上限 200 分钟；
5. 直改 `next_run_at` 立即生效（18:21:00 精确触发，原网格点取消）；
6. off-grid 值合法且被精确执行（18:49:42.180）；
7. run 完成阶段不覆盖触发回合写入的值（19:01:37.826 精确触发，网格点静默跳过）；
8. 派发时按 recurring 网格从当前时间重算下一槽写回（外部值 one-shot）；
9. 非触发回合内同会话 CronUpdate 自己的 automation 可行且即时重算（18:05 正样本）；
10. completed 行被宿主自动清理；parked 的 active 行不被清理。

## 2. 本轮实验环境（2026-09-08 00:16 起）

- 实验体：`automation-ebb3f72d-9689-4731-afa9-59c475cc7617`（title "v2.3-W0-metronome"），
  recurring=1、intervalUnit=minute、interval=5、anchor=1788797808176（2026-09-08T00:16:48.176+08:00），
  网格点 = anchor+300k ms；model 继承创建会话 = GLM-5.3（§3.3 已知边界，dogfood 接受）。
- DB：`~/.zcode/v2/tasks-index.sqlite`，`PRAGMA journal_mode = wal`（实测）。
- schema 冻结：`automations` 33 列（automation_id PK / title / cron_expr / prompt / model /
  provider / mode / thought_level / workspace_key / workspace_path / workspace_identity /
  target_task_id / location_kind / recurring / max_runs / end_at / schedule_rule / run_count /
  enabled / lifecycle_status / **next_run_at INTEGER epoch ms** / last_run_at / running /
  claimed_at / dispatch_status / dispatch_attempts / retry_at / last_error / created_at /
  updated_at / bot_delivery_target / schedule_edited_by_user / scheduled_run_count）；
  `automation_runs` 12 列（run_id PK / automation_id / workspace_key / scheduled_at /
  trigger / dispatch_status / outcome / session_id / error / attempts / created_at / updated_at）。
  与 09-07 automation-backup.json 快照零漂移。
- 表内另有两条用户 paused 任务（额度刷新-5：00 / v2.2.1 持续推进桥），enabled=0，全程不受影响。

## 3. A 系列：外部 retime 语义活体复验

### A1 off-grid 精确触发

- 00:16:48 创建；首个网格点应为 anchor+300s = 00:21:48.176。
- 00:16:56 外部写入 `next_run_at = 1788797996363`（00:19:56.363，T1，早于首个网格点 → 非歧义）。
- read-back 校验 ok=True；随后被锁实验短暂改写后于 00:17:24 恢复为同一绝对值。
- **结果（✅ 成立）：`scheduled_at = 2026-09-08T00:19:56.363+08:00`，与外部写入值精确一致**
  ——事实 5（直改立即生效）/ 6（off-grid 合法）在本轮宿主版本复验成立。
  附注：注入于触发后约 34s 到达本会话（触发时会话处于活动 turn，排队后投递——与
  19 连拍记忆"排队注入"一致）；派发后宿主将 next_run_at 重算为 anchor+600s 网格
  （00:21:48.176）而非 T1+300s——事实 8（派发网格重算）复验成立。

### A2 ZC-03 SQLite busy/lock

方法：后台进程 `BEGIN EXCLUSIVE` 持锁 6 秒；1 秒后并发尝试 `BEGIN IMMEDIATE + UPDATE next_run_at`。

| 场景 | busy_timeout | 结果 |
|---|---|---|
| 短超时写 | 200 ms | 失败 `database is locked`（elapsed 0.30s，即时报错） |
| 长超时写 | 10000 ms | **成功**（elapsed 4.63s ≈ 持锁剩余时长，等待后提交） |

结论：WAL 下外部写与宿主共存的正确配方 = `BEGIN IMMEDIATE + busy_timeout`；
无超时即失败 → adapter 必须设置 busy_timeout（计划 §6.2 配方实证）。锁实验期间宿主无异常。

### A3 ZC-04 park 静默观察（进行中）

- 00:21:19.332 park（网格点 00:21:48.176 前 29 秒），parked `next_run_at = 2027-09-08T00:21:19.298`。
- 观察窗口 00:21:20 → 00:33:27（>12 分钟），跨虚拟网格点 00:21:48 / 00:26:48 / 00:31:48。
- **结果（✅ 成立）：全程 run_count=1 恒定、`next_run_at` 保持 2027-09-08T00:21:19.298
  原封不动、enabled=1——park 后 recurring 网格完全不触发，外部值不被矫正。**

## 4. B 系列：ZC-01 ZCode restart（✅ 成立）

- 00:33:46 武装 T2 = `1788799426221`（00:43:46.221，now+10min）。
- ~00:35–00:40 用户完全退出并重启 ZCode 应用，重开本会话。
- 00:41:17 会话恢复后立即读 DB：**`next_run_at` 仍为 1788799426221 原封不动**——
  行完好（enabled=1 / active / run_count=1），无网格矫正、无后台覆写、last_error 空。
- 00:43:46.221 触发：`scheduled_at = 00:43:46.221` **与外部写入值精确一致**，run_count=2；
  派发后 next_run_at 重算为 00:46:48.176（anchor+1800s 网格对齐）——事实 8 不受重启影响。
- 注入在触发后约 1–2 分钟排队送达**重开后的同一会话**（target_task_id 绑定跨重启保持）。

**ZC-01 结论：外部写入的动态 next_run_at 跨 ZCode 应用重启完整存活，重启后仍被精确执行，
注入仍投递至绑定会话。Global Clock 的 D4 watchdog 模式在重启场景下成立。**

## 5. C 系列：ZC-02 Windows sleep（⛔ 本轮挂起）

- 原计划：T3=00:52:19.916 武装后用户令机器睡眠跨过触发点、唤醒后观察补发/跳网格/丢弃行为。
- **2026-09-08 00:46 用户告知本机当前不能进行睡眠/关机操作——ZC-02 挂起为开放项。**
- 处置：不阻塞 W1/W2 实施（代码不依赖睡眠语义）；**保持 release-gate 地位**，
  v2.3.0 发布前在可睡眠环境补做；T3 已被 park 覆盖（不再触发）。

## 6. D 系列：ZC-04 park 跨 restart（待用户重启 #2）

- 00:46:59 park：`next_run_at = 1820335619273`（2027-09-08T00:46:59.273，+365 天）。
- ~00:47–00:49 用户完全退出并重启 ZCode 应用，重开本会话。
- 00:49:16 读 DB：**parked 值原封不动（1820335619273）、run_count=2 恒定、enabled=1、
  active——park 跨 App 重启存活，重启不产生任何补发或网格矫正。**

**ZC-04 结论（✅ 成立）：远期 park 值跨重启保持 + 静默期零触发 + 重启零副作用。
§10.5 replace 的 park 旧 automation 与 §11 completed bridge 的 park 路径物理基础成立。**

## 7. 结论汇总（2026-09-08 00:50）

| 宿主事实 / 实验 | 判定 | 关键证据 |
|---|---|---|
| 事实 5：直改 next_run_at 立即生效 | ✅（09-07 + 本轮复验） | T1=00:19:56.363 精确触发；原网格点取消 |
| 事实 6：off-grid 值合法 | ✅（同上） | T1/T2 均非网格点，scheduled_at 与写入值毫秒一致 |
| 事实 7：run 完成不覆盖触发回合写入 | ✅（09-07 凭据） | 19:01:37.826 精确触发、网格点静默跳过 |
| 事实 8：派发时网格重算写回 | ✅（本轮 ×2） | T1 派发后 →00:21:48.176（anchor+600s）；T2 派发后 →00:46:48.176（anchor+1800s） |
| ZC-01：restart 后动态值 | ✅ | T2 值跨重启存活 + 精确触发 + 注入回绑定会话（target_task_id 跨重启保持） |
| ZC-02：sleep 跨 next_run_at | ⛔ 挂起 | 本机当前不能睡眠；**release-gate 保持，发布前补做** |
| ZC-03：SQLite busy/lock | ✅ | 无超时 0.30s 即败；busy_timeout=10s 等待 4.63s 后提交成功 |
| ZC-04：远期 park | ✅ | 12 分钟跨 3 网格点零触发；跨重启值不变零副作用 |

**对计划的影响**：
1. §6.2 retime 事务配方（busy_timeout + BEGIN IMMEDIATE + read-back）实证成立；
2. schema 33 列/WAL 与 09-07 快照零漂移，W1 可直接锚定；
3. D4 watchdog 循环的每个环节（外部 retime 精确执行、派发网格接管、park 静默、重启存活）均有活体证据；
4. 唯一开放项 ZC-02（睡眠语义）不阻塞 W1/W2 实施，发布前补做。

**遗留物**：实验体 `automation-ebb3f72d`（5 分钟网格 recurring）保留不删，
转正为 W2 dogfood 的 clock 种子（automation identity 连续跨实验→dogfood 窗口）。
