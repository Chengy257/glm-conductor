# GLM Conductor 故障排查（Troubleshooting）

> 面向用户的排障手册：按「现象 → 判定 → 处置」组织，只写结论性行为事实；每条事实标注实现模块路径供核对。架构全貌见 [architecture.md](architecture.md)，概念入门见 [core-concepts.md](core-concepts.md)。

运行时 CLI 的统一入口是 `runtime/cli.py`（本文以 `<cli>` 代指 `<插件目录>/plugins/glm-conductor/runtime/cli.py`，安装后以实际插件缓存路径为准）；用可用的 Python 3 解释器执行（Windows 无 `python3` 启动器时用 `python`）。

## 1. host 兼容性：`host-check` 判定与处置

**何时用**：ZCode 升级之后，或怀疑调度类功能（定时唤醒 / Quota Clock tick）异常时——当前适配器依赖的宿主库结构是观察到的宿主行为而非契约 API，宿主升级可能使其漂移。

**怎么跑**：

```
python <cli> host-check
```

全程只读（四类纯读语句，绝不写宿主库、绝不以写试写——`runtime/host/zcode_schedule.py` 的 `probe_host_compatibility`），恒输出单行 JSON 并返回 0。

**输出解读**（键集冻结，全键恒在）：

| 键 | 含义 | 排障关注点 |
| --- | --- | --- |
| `support_status` | 总判定：`supported` / `unsupported` | `unsupported` 才需要行动 |
| `database_found` | 宿主库文件存在（默认 `~/.zcode/v2/tasks-index.sqlite`） | false → ZCode 未在此机器使用过 / 路径非默认（可用 `--db <path>` 或环境变量 `GLM_CONDUCTOR_ZCODE_DB` 显式指向） |
| `database_readable` | 只读方式打开成功 | false 且文件存在 → 库损坏或权限问题，`issues` 有明细 |
| `schema` | `compatible` / `incompatible` | incompatible → automations 表或必需列缺失 |
| `missing_columns` | 缺失的必需列名清单 | 适配器必需列集 = `automation_id` / `recurring` / `enabled` / `lifecycle_status` / `next_run_at` |
| `next_run_at_type_ok` | `next_run_at` 存储类型抽样是否为 integer/null | false → 宿主改了存储类型 |
| `runs_table_ok` | automation_runs 表可达性（咨询性，不影响 support 判定） | 缺失仅损失「最近 runs」诊断信息 |
| `issues` | 人类可读问题明细 | 逐条对应上列判定 |
| `write_capability_required` | 恒 `next_run_at only`——适配器唯一生产写能力 | 佐证写边界（见下） |

**处置**：

- `supported` → 无需任何动作。
- `unsupported` → 插件的调度写入会在前置闸上安全失败并报错（绝不带病写——`ZcodeScheduleSchemaError`，`runtime/host/zcode_schedule.py`）；请勿手工修改宿主库，等待适配新宿主版本的插件更新；在修复前，连续性恢复始终有 SessionStart 注入兜底（自动化是加速器，不是正确性前提）。

## 2. Quota Clock 失联：诊断与恢复路径

**现象**：额度窗口不再被自动观察/物化、周期 tick 不再出现（此前设置过 Quota Clock）。

**第一步——权威诊断**：

```
python <cli> quota-clock-status <repo_root>
```

只读汇总 clock 绑定健康（`runtime/commands/quota_clock.py`），重点看 `health` / `reasons` / `needs_replacement` / `suggestion`。

**判定（needs_replacement 两极，`runtime/quota/clock.py` 的 `needs_replacement_from_reasons`）**：

- `needs_replacement: true`（reasons 含 `db_row_missing`——宿主 automation 行已消失，或 `db_inspect_failed`——宿主库不可查）→ clock 已无法继续服务，走下方恢复路径。
- `needs_replacement: false`（仅有 `target_mismatch` / `tick_stale` / `runtime_path_missing`）→ 这些只是 advisory：按 `suggestion`「await next tick」等待；`runtime_path_missing`（插件升级后旧路径失真）会在下次 tick 自动回写自愈。

**恢复路径（needs_replacement=true 时，`recovery_guidance` 同文）**：

1. 新建一个独立的专用 ZCode 交互会话，选用低成本 Flash 类模型（automation 的 model 即发起会话的模型；clock automation 建议放在专用会话，避免周期 tick 打断主编码会话）。
2. 在该会话内为 clock 创建一个新的 recurring ZCode automation。
3. 显式执行替换绑定：

```
python <cli> quota-clock-bind <repo_root> <new_automation_id>
```

**死绑定自动替换说明**：bind 遇到「已绑定其它 automation」的冲突时，会只读复核旧 automation 的宿主库行；确认该行已消失（verified dead binding）才自动放行改绑，成功输出的 JSON 会多出 `replaced_dead_binding: <旧 automation_id>` 键（仅此路径出现）。旧绑定行仍存活时照样拒绝改绑（`ClockStateConflictError`，保护活绑定）；本插件**不做隐式迁移或自动 rebind**——新 automation_id 始终由你显式提供（`runtime/commands/quota_clock.py` + `runtime/quota/clock_store.py` 的锁内 TOCTOU 复核）。

## 3. 「clock 已停摆但 status 报 healthy」——awaiting_first_tick 语义注记

`last_tick_at` 缺失或非数值会被判为**首绑未 tick**（`awaiting_first_tick: true`，healthy、只提示等待下一次 tick，不触发 replace 建议）——这是为修复「新绑定立刻报 unhealthy」的自相矛盾而定的语义（`runtime/commands/quota_clock.py` 与 `hooks/session_start.py` 同口径镜像）。

**已知误判形态**：曾正常 tick 过的 clock，若 state 文件中的 `last_tick_at` 单键损坏或丢失（手工编辑、磁盘故障等），status 会误判为「首绑未 tick」而报 healthy——停摆被健康标签掩盖。

**处置**：怀疑停摆但 status 报 healthy 时，先打开 state 文件核对：

```
~/.glm-conductor/quota-clocks/<provider_identity_hash>.json
```

检查 `last_tick_at` 是否为合理的 epoch 毫秒数值（对比当前时间与 clock 周期）。数值明显过旧或键缺失而 clock 实际长期未 tick → 按 §2 的恢复路径替换绑定。

## 4. SessionStart advisory：触发条件与语义

**触发条件**（启发式，只读 state 文件、绝不打开任何数据库——`hooks/session_start.py` 的 `render_clock_advisory`）：Quota Clock 已绑定（用户级 state 文件存在且可识别），且 `last_tick_at` 落后当前时刻超过 **2 × fallback_interval_minutes**（缺省 60 分钟，即阈值 2 小时）。首绑未 tick 不触发；state 缺失 / tick 新鲜 / 任何内部异常一律静默（fail-open，绝不阻塞会话启动、零噪音）。

**语义**：advisory 只是提示，不执行任何 bind / replace / 迁移、零写入；文本含恢复指引、会话专用性声明（专用会话放置 `not_mechanically_verifiable`——插件无会话探测能力）与权威诊断指路。

**收到 advisory 后**：运行 `quota-clock-status <repo_root>` 做权威 DB 级诊断（`needs_replacement` 只在那里判定），再按 §2 处置。

## 5. 状态存储位置对照（排障先找对文件）

| 状态 | 位置 | 说明 |
| --- | --- | --- |
| Quota Watcher 观察状态 | 仓库级 `<repo>/.glm-conductor/quota/watcher.json`（另有 `watcher.log`、互斥凭据 `watcher.lock`） | 观察面按仓库隔离（`runtime/quota/watcher_store.py`） |
| provider 额度缓存 | 仓库级 `<repo>/.glm-conductor/quota-cache.json` | 标准化快照缓存，不含凭证（`runtime/quota/resolver.py`） |
| 控制面事件 | 仓库级 `<repo>/.glm-conductor/quota/events.jsonl` | append-only（`runtime/journal.py`） |
| Global Quota Clock state | **用户级** `~/.glm-conductor/quota-clocks/<provider_identity_hash>.json` | clock 是 provider 身份（账号）级，一个身份一个 state，多仓库共享（`runtime/quota/clock_store.py`） |
| 宿主 automation 库 | 用户级 `~/.zcode/v2/tasks-index.sqlite` | ZCode 所有物；只经适配器访问，**勿手工编辑**（`runtime/host/zcode_schedule.py`） |

排障时最容易找错的是最后两行：watcher / 缓存 / 事件在**仓库级** `.glm-conductor/` 下，clock state 与宿主库在**用户级** home 目录下。

## 6. 告警盲区（advisory 的结构性边界）

SessionStart advisory 是 state 文件启发式：宿主库中的 automation 行丢失（`db_row_missing`）对它**结构性不可感知**（advisory 按设计不打开数据库），只能靠「tick 停止刷新 last_tick_at → 超过 2×fallback 阈值」间接收敛——因此从行丢失到告警存在**最长 2×fallback 间隔的延迟**，且 advisory 本身无法区分「停摆」与「宿主行丢失」这两种处置紧迫度不同的形态。

DB 级诊断（`needs_replacement` / `placement` / `recovery_guidance`）权威归属 `quota-clock-status`——advisory 文本中的指路即此意。对告警时效有要求的场景，可让常驻 Quota Watcher 保持运行（它是观察面，不是正确性前提）。

## 7. 安全边界速查

- **插件不做 OS 电源操作**：睡眠/唤醒/关机等电源操作是用户本人权限，插件与代理严禁代为执行（适用于 runtime、技能/代理行为与一切附属脚本）。
- **睡眠/唤醒跨窗行为未验证**：连续性设计不依赖它——时钟靠 recurring watchdog 网格自愈、恢复兜底归 SessionStart 注入；机器长期睡眠时定时触发不可依赖，这是如实记录的边界，不是已修复的缺陷。
- **一会话一 automation**：同一会话已有 scheduled task 时，宿主会直接拒绝再创建——这是宿主硬规则（创建时拒绝），不是插件缺陷。
- **写边界**：适配器对宿主库的唯一写操作是改写单个 automation 的 `next_run_at` 单列（事务化 + 写后读回验证），其余一切宿主变更被机械禁止（`tests/test_zcode_schedule.py` 的 ForbiddenSqlTest 锚定）。
