# GLM Conductor v2.2.1 执行计划（Execution Plan）

> **需求源**：`docs/GLM-Conductor-v2.2.1-Hardening-and-Repository-Release-Cleanup-Implementation-Plan.md`（下称"需求计划"，WU-221-* / ST-00..08 / §5 兼容冻结 / §15 回归矩阵 / §16 DoD 均以该文为准）
> **本文职责**：裁决记录 + 自举方法论 + 工作单元分解 + 阶段门 + 连续性设计。与需求计划冲突时以本文的裁决记录为准（裁决均源自用户 2026-09-06 讨论）。
> **基线事实**：`origin/main = b4f4d3f`（v2.2.0 合并点）；`v2-dev = 173fed4`（领先合并点 1 个 docs-only 提交）；本地 `main = 0cddbf3`（v2.1.0 时代，待 fast-forward）；测试基线 2113 / validator 15/15；CI = 单工作流 `validate-plugin`（Linux+Windows × Py3.8/3.13 四路，`on: push` 无分支过滤）。
> **性质**：维护版，零功能扩张。

---

## 1. 锁定裁决记录（用户裁决，2026-09-06）

| # | 裁决 |
| --- | --- |
| D1 | **A**：先本地 merge `v2-dev`(173fed4, docs-only) → `main` 并 push，再从 main 开维护分支（沿 v2.1.0 收官"本地 merge 直推"先例） |
| D2 | 分支名 **`v2.2.1-hardening`** |
| D3 | C1 分解**只抽三域**（quota accounting / wake bridge / subscription-resume）；dispatch_service/completion_service 留 v2.3 |
| D4 | CI：**ruff（最小规则集 `F`+`E9`）+ plugin-load smoke** 采纳；coverage 仅零负担时 report-only（默认不做）；CodeQL 暂缓 |
| D5 | **全版本 history 分类**（v1/ v2.0/ v2.1/ v2.2/ + 现有平铺 2 文件归位），独立 commit、独立校验；zcode-fr 文档单独归位（reference 类） |
| D6 | CHANGELOG：2.2.0 浓缩后全文存档 `docs/history/v2.2/CHANGELOG-2.2.0-full.md`；旧条目中指向被搬文档的链接**机械改指** history/ 新路径 |
| D7 | GitHub description **单语英文**（需求 §3 stable product statement）；topics 九个照单 |
| D8 | 分支保护（F3）：**默认仅交付配置方案**；应用须 ST-08 前用户当次明确批准 |
| D9 | v2.2.1 发布验证完成后**删除 `v2-dev`**（届时已完全合并，无损） |
| D10 | core-concepts.md **中文主**、术语保留英文，不做双语 |

**补充裁决（语言/授权政策）**：

- README 双语共存、**英文为首选项**：`README.md` = 英文（GitHub 默认渲染面），中文版改名 `README.zh-CN.md`，两份顶部互设语言切换链接；`README.en.md` 文件名退役。
- 插件发布面元数据**一致英文**：`plugins/glm-conductor/.zcode-plugin/plugin.json` 的 `description` + `marketplace.json` 两级 `description`（keywords 已英文不动）。
- skills/ 与 commands/ 的 frontmatter description **保持中文不动**（运行面、模型读取触发，不属发布页元数据）。
- 归档历史文档**中文原样不改**（只 `git mv`；仅 history 索引 README 按新面撰写）。
- **git 常设授权**：后续所有无风险 git 操作（含 push）无需逐次请示；推送节奏仍按计划节点，中途需远端 CI 验证时直接 push。

---

## 2. 自举方法论（本机 glm-conductor 插件 2.2.0）

本项目用自身插件的最新安装版（缓存 2.2.0）执行自身的维护版——延续 dogfood 纪律。

**强制面解耦**：实施期间 hooks / PreToolUse 派发门 / Stop 完成门**逐事件从缓存 2.2.0 执行**，被修改的是仓库 `plugins/` 工作副本——二者解耦，实施期不受自身改动影响。缓存仅在 ST-08 发布后 robocopy `//MIR` 就地刷新（matcher 面=新会话生效）。

**伞任务**：全程一个 conductor 任务，TASK_ID = `v221-hardening-<6hex>`（创建时定随机后缀）。基线路由（首单元派发前原样输出声明）：

```
SELECTIVE ROUTE
mode: full
delegability: high
assurance: high
executor: flash-implementer
continuity: resumable
reason: every work unit is bounded by a five-part spec with explicit ownership and deterministic verification; project discipline mandates independent review; the arc spans quota windows
```

**单元微循环**（每个运行时 WU）：

1. 主会话写五段式规格（OBJECTIVE / FILES AND OWNERSHIP / INTERFACES / CONSTRAINTS / VERIFICATION；复杂域附 TASK CONTEXT PACK）；
2. `prepare_dispatch`（签发 permit，ownership 同步 state）→ Agent 派发，prompt 携带 `GLM_CONDUCTOR_DISPATCH=<permit_id>` marker（flash-implementer 类型无 permit 即被 PreToolUse 门 deny）；
3. 实施者返回 IMPLEMENTATION REPORT（仅视为声明）→ 主会话亲自检查完整 diff + 亲自跑单元验证，经 CLI `verify-unit` 落单元级证据（**指纹一律取 `reconcile.fresh_unit_verification(...)` 返回的单元作用域值**——v2.1 实战教训锁定，任务作用域指纹在含其他改动的工作树上必被 RB-1 拒）；
4. `finish_unit`（终态 + 租约释放 + wave 关闭）；**record → finish 之间零 git 提交**；
5. assurance 单元：派 `glm-reviewer`（全新上下文、只读），prompt 携带 `GLM_CONDUCTOR_REVIEW=<task_id>` marker（无 marker 零记账、裁决拿不到 receipt）→ 裁决后 CLI `review-record` 落 durable receipt；fix-first → 原实施者修复 → 主会话重验 → **换新审查者**复审；任何修复使先前证据失效，重走链；
6. git commit（沿仓库既有提交信息风格；commit 后证据自然 stale 属设计内，下单元重走）。

**审查分级**：运行时 WU（ST-01..04）逐 WU 终审；文档 WU（ST-05）主会话验证 + ST 门处 delta 审查；ST-07 全新上下文独立发布审查是唯一放行裁决。文档 WU 委派同样走 permit 门（executor 类型一致）。

**派发纪律**：默认串行（max_workers=1）。本计划运行时 WU 间存在 import/迁移依赖，**全程默认串行**；不满足"ownership 不相交 + 接口固定 + 验证可分离"三条件绝不并行。

**单元注册**：每 ST 开工时注册该阶段单元（沿既有引导脚本实践：`Write` 独立脚本到 `.glm-conductor/tmp/` 调 runtime API 执行——**禁止 `python3 -c` 内联**，heredoc/引号陷阱实战已炸）。

**runtime 调用入口**：一律 `python3 plugins/glm-conductor/runtime/cli.py <子命令>`。

---

## 3. 连续性设计（长程持续推进）

- **mode = resumable；auto_resume = until_done**（开工确认项 A：授权 `authorization.source = "user"`）+ **max_quota_windows = 10**（开工确认项 B，可调）。
- **Persistent Wake Bridge**（唯一 stable transport）：ST-00e 经 `wake-plan` 裁决 → 宿主 CronCreate（recurring）→ `arm` 记账。一任务一桥；arm/fire/create 一律不消费窗口；消费唯一合法点 = §15.1 resume commit point。
- **checkpoint 节奏**：每 ST 门通过后写 CONTINUITY CHECKPOINT 到 `.glm-conductor/tasks/v221-*/checkpoint.md`（导航状态，不复制 diff）；休眠前三件套（durable checkpoint + 当期 epoch 订阅注册 + armed 桥）按 Stop 门第 0 位义务补齐，不硬闯门。
- **唤醒/恢复**：恢复首步恒为 `quota-resume --force-refresh`；running 单元先 `reconcile_agent_run` 四分对账，completed 不重跑；**repository > checkpoint**。
- **相位纪律**：DRAINING 只做收尾白名单（join/verify/review/checkpoint/wake），不开新波次；额度四态经 `quota-resolve` 在刷新点观察。
- **桥与宿主会话**：桥绑定本会话；宿主会话死亡 → 桥 active≠有效，新会话由 SessionStart 恢复注入兜底 + 单次尝试清理旧桥重建；**绝不重试 CronDelete/CronUpdate**（glitch 教训，墓碑兜底路径已实战验证）。
- **watcher**：不强制启动（可选观察加速面，非正确性前提）；由用户/主会话按需决定。
- **完成清理**：ST-08 验证后——伞任务 Stop 门四重检查 → completed → 删除本任务目录 + 桥单次尝试停止。

---

## 4. 阶段计划与工作单元

### ST-00 基线与分支整合（solo / foreground——伞任务建立前，无 Agent 派发）

| 单元 | 内容 | 验证 |
| --- | --- | --- |
| v221-00a | 本地 main fast-forward → b4f4d3f；本地 merge `v2-dev`(173fed4) → main（merge commit，信息沿 v2.1.0 先例）；push origin main | main CI 四路绿 |
| v221-00b | 自 main 创建 `v2.2.1-hardening` 并 push（`on: push` 全分支触发 CI） | 分支 CI 触发确认 |
| v221-00c | 提交需求计划（untracked）+ 本执行计划入库；本地清理杂散目录 `./--force-refresh/`（gitignored 残骸，非 git 变更） | git status 干净 |
| v221-00d | 基线记录：`PYTHONUTF8=1 python3 -m unittest` 全量（预期 2113）+ `python scripts/validate_plugin.py`（15/15）→ 填入 §9 基线块 | 基线绿 |
| v221-00e | 创建伞任务（state.json + route 五字段 + events.jsonl `task_created`/`route_selected`）；确认项 A/B 通过后 arm 桥 | state/桥/事件在位 |

**门**：分支干净、基线绿、伞任务 + 桥在位。

### ST-01 持久 I/O 原语（需求 WU-221-A1；full）

| 单元 | 内容 | 验证 |
| --- | --- | --- |
| v221-a1 | 新建 `runtime/durable_io.py` + `tests/test_durable_io.py`：`atomic_write_json`（同目录唯一临时文件 = tempfile PID+随机后缀、flush+close 后 replace 前可行处 `fsync`、`os.replace`、Windows PermissionError 有界重试**保留**、遗弃临时文件 best-effort 清理、**绝不固定 `.tmp` 名**）；`atomic_read_json`（容错）；`atomic_update_json`（独占锁下 read-modify-write）；`exclusive_lock`（`O_CREAT\|O_EXCL` + PID/generation/created_at + 陈旧校验 + claim-marker 竞态安全 takeover）。stdlib-only、Py3.8 兼容 | 新测试 + 全量绿 |
| v221-a2 | 迁移共享写者（quota/resolver 缓存、watcher_store、primer 存储、scheduler_facts）→ durable_io；A3 四类分类表（single-writer / multi-replace / multi-RMW / append-only）逐文件标注进 architecture.md；grep 断言仓库内无固定 `.tmp` 写者 | 既有模块套件 + 新并发写者回归 |

**门**：迁移者全绿、唯一临时文件证明、零行为漂移。

### ST-02 watcher 锁硬化（需求 WU-221-A2/A3；full）

| 单元 | 内容 | 验证 |
| --- | --- | --- |
| v221-a3 | `.glm-conductor/quota/watcher.lock` 与 `watcher.json` 分离：O_EXCL 原子获取（锁内容 PID/generation/provider_identity_hash/created_at）；陈旧恢复（PID 死 / 心跳陈旧校验后才 takeover，takeover 本身竞态安全）；败者得确定性 conflict；`watcher.json` 降为纯观察状态；优雅停机保留；无无限等待 | 单元测试 |
| v221-a4 | stop 标志 / 心跳 / scheduler facts 的 RMW 路径改 `atomic_update_json` 独占锁语义；回归证明并发 stop 请求不被心跳回写静默丢失 | 竞态回归 |
| v221-a5 | `tests/test_multiprocess_runtime.py` 场景组 1：N=2..4 同时 acquire → 恰一胜、其余 conflict、watcher.json 不损坏、无重复 active generation；陈旧 PID / 陈旧心跳恢复；取锁即死；stop/start 竞态；Windows spawn 安全（模块级 target、无闭包、barrier/event 同步、有界超时、零真实网络） | 多进程套件 |

**门**：多进程测试连续 5 轮通过；stop/心跳竞态回归绿。

### ST-03 provider 身份硬化（需求 WU-221-B1/B2；full）

| 单元 | 内容 | 验证 |
| --- | --- | --- |
| v221-b1 | `compute_provider_identity_hash` 抽取 → `runtime/quota/identity.py`（现居 `quota/watcher.py:142`）；watcher/primer 改引共享模块；零行为变化（B1 与 C2 的顺序耦合在此闭合） | 全量绿 |
| v221-b2 | resolver 缓存绑定身份：新条目写 `provider_identity_hash`；复用仅当 hash 相等；不等 → 不视为新鲜、不作新身份的权威陈旧回退、按既有 fail-open 取 fresh 或 UNKNOWN；legacy 无 hash 缓存保守降级；秘密材料零落日志审计（raw key / Authorization 头 / 完整凭证源 / 失败响应体） | 跨账缓存矩阵测试 |
| v221-b3 | `QuotaIdentity = (provider_identity_hash, epoch_id)` 落账面：审计订阅 / 激活标记 / 窗口消费 / primer 幂等 / watcher 观察 / 恢复对账六面；新记录加可选 `provider_identity_hash`（写者发新形、读者双形兼容、旧记录绝不重写、迁移幂等）；`epoch_id` 格式不动 | 跨账回归：同文本 epoch_id 不同身份不互相授权/消费；账户切换后恢复正确 |

**门**：跨账矩阵绿；v2.2 遗留 state/journal 可读；零破坏性重写。

### ST-04 运行时分解（需求 WU-221-C1/C2；full；小提交串行）

| 单元 | 内容 | 验证 |
| --- | --- | --- |
| v221-c1 | 抽配额记账/消费（`record_quota_boundary_consumed`、`migrate_quota_window_accounting`、pending-marker 链）→ `runtime/quota/accounting.py`；task_manager facade 再导出 | 全量绿 |
| v221-c2 | 抽唤醒桥规划/记账 → `runtime/continuity/wake_bridge.py` | 全量绿 |
| v221-c3 | 抽订阅/恢复 → `runtime/continuity/subscription.py` + `resume.py` | 全量绿 |
| v221-c4 | C2 共享助手 → `runtime/quota/time_utils.py` + `window_math.py`（仅多模块共用且语义稳定者：ISO UTC 解析/格式化、reset 窗数学、边界助手）；公共面 import 兼容契约测试（`runtime.task_manager` 既有公共符号集不变） | import 契约 + 全量绿 |

**门**：每次抽取后全量绿；公共 API 导入面不变；无捆绑行为变化。dispatch/completion 门面不动（D3）。

### ST-05 仓库/文档清理（需求 E1–E7 + G1–G2；delegate + 主会话验证 + ST 门 delta 审查）

| 单元 | 内容 | 验证 |
| --- | --- | --- |
| v221-e1 | 新建 `docs/core-concepts.md`（中文主、术语英文；四节 = 三支柱 + 稳定边界；零开发编年）+ `docs/README.md` 索引（Start here / Technical Reference / History 三组；history 显式标注非权威） | 内容审查 + 链接有效 |
| v221-e2 | v2.2 九份 `git mv` → `docs/history/v2.2/` + `history/v2.2/README.md`；**独立 commit** 做全版本分类（映射见 §5）；修 4 个 runtime 源文件注释路径（watcher / scheduler_facts / execution_policy / state）+ CHANGELOG 旧条目链接机械改指；G1 根目录复核（现状已符目标面；`README.en.md` 退役改名在 e4） | git mv 零删除、全量可寻、validator 15/15 |
| v221-e3 | architecture.md 现状化：保留设计原则/状态模型/路由/派发事务/完成证据/额度连续性/持久存储/恢复/稳定边界/安全模型；开发编年与长校正记录移 history；仅兼容需要处留 "Since v2.x" 短注；新增 A3 分类表（a2 落）核对 | 每节回答"系统现在是什么" |
| v221-e4 | README 重构：新英文 `README.md`（默认渲染）+ 中文 `README.zh-CN.md`；顶部语言切换互链；结构按需求 §E1 模板；移除 M/C/RH/Phase0/dogfood 编年、wu-* 名、内部事件枚举；保留路由模式/完成门/收据指纹/额度相位/epoch+订阅+桥/watcher+primer 边界/单控+工上限/SessionStart 回退/recurring_bridge 唯一稳定/primer 默认关；validator README 耦合检查逐项调整（每处注明合法理由，限需求 D2 五类） | CN/EN 标题级 parity 校验；validator 绿 |
| v221-e5 | CHANGELOG：2.2.0 全文存档 → `docs/history/v2.2/CHANGELOG-2.2.0-full.md`；2.2.0 条目按分组模板浓缩（Added/Changed/Hardened/Known boundaries）；2.2.1 条目草稿（终稿 ST-08） | 存档完整 + 浓缩形合规 |
| v221-e6 | 发布面元数据英文化：`.zcode-plugin/plugin.json` description + `marketplace.json` 两级 description → §3 stable product statement 口径；skills/commands 描述不动（裁决）；版本面不在此触（ST-08 原子 bump 保留） | validator 绿；语义无损 |

**门**：README 不依赖开发文档即可理解项目；CN/EN 对等；历史证据零丢失；版本面除 e6 元数据外未动。

### ST-06 CI / 系统保障（需求 D1–D3）

| 单元 | 内容 | 验证 |
| --- | --- | --- |
| v221-d1 | ruff 配置 `select = ["F", "E9"]` + CI 步骤；既有违例 = 0 或逐条记录豁免；**不做风格重排** | ruff 通过 + 全量绿 |
| v221-d2 | plugin-load smoke：CI 增一步——plugin.json / marketplace.json 可解析、skills/hooks 目录结构完整、runtime 包可导入（隔离环境、零网络零模型） | smoke 绿 |
| v221-d3 | （条件）coverage report-only 单腿，仅当零维护负担；默认不做 | — |

收尾全量：validator + 全量套件 + 多进程套件；push 分支取远端四路证据。

**门**：四路绿 × 全部新套件（对应需求 §16.3）。

### ST-07 独立发布审查（audit；全新上下文）

`glm-reviewer`（prompt 携带 `GLM_CONDUCTOR_REVIEW` marker）对 `v2.2.1-hardening` vs `main` 全量 diff；优先级 = 需求 §13 ST-07 八项（无公共行为回归 / 竞态真闭合 / 身份切换安全 / 历史文档不再当期真相 / README 主张有运行时支撑 / architecture 现状导向 / 历史证据无意外删除 / 稳定边界诚实）。裁决 ship / fix-first / rethink；**仅 ship 放行**；fix-first → 修复 → 重验 → 换新审查者。

**门**：ship receipt 落盘（review-record）。

### ST-08 发布（solo + 用户确认点）

| 单元 | 内容 | 验证 |
| --- | --- | --- |
| v221-f1 | 原子版本 bump 四触点：plugin.json `2.2.1` + README.md/README.zh-CN.md 徽章 + CHANGELOG 首标题 `## 2.2.1`（沿 0897411 四触点原子先例）；2.2.1 条目终稿 | validator 检查 12 同构通过 |
| v221-f2 | push 分支 → PR → main；PR CI 四路绿 → merge（不 force 不 rebase）→ post-merge CI 绿 | 两轮 CI 绿 |
| v221-f3 | tag `v2.2.1` + GitHub Release（英文 notes；description/topics 按单语英文裁决同步设置） | tag/Release 在位 |
| v221-f4 | F3 分支保护：交付精确配置方案文档；**仅在用户当次明确批准后** gh 应用（D8 默认仅方案） | 方案交付 |
| v221-f5 | D9：发布验证完成后删 `v2-dev`（届时已完全合并） | 分支删除 |
| v221-f6 | 收官：伞任务 Stop 门四重检查 → completed → 任务目录清理 + 桥单次尝试停；两份计划文档 `git mv` → `docs/history/v2.2.1/`（收官 commit）；本机缓存 robocopy `//MIR` 刷新 2.2.1（matcher 面 = 新会话生效） | 终门 + 清理完成 |

---

## 5. 文档归档映射（D5 全量分类，独立 commit 执行）

| 目标 | 源（均 `git mv`，内容零改动） |
| --- | --- |
| `history/v1/` | `glm-conductor-v1-release-remediation.md`；`glm-conductor-v1.1-final-release-cleanup.md`；`history/pre-v1.1-architecture-proposal.md`（现平铺，移入） |
| `history/v2.0/` | `glm-conductor-v2-implementation-plan.md`；`glm-conductor-v2-phase0-runtime-verification.md`；`glm-conductor-v2-upgrade-guide-final.md`；`history/glm-conductor-v2-upgrade-guide-quota-aware.md`（现平铺，移入）；`GLM-Conductor-v2.0.0-全面审查与v2.0.1加固建议.md`；`GLM-Conductor-v2.0.1-连续性缺口分析与v2.1优化建议.md`；`GLM-Conductor-v2.0.1-Release-Hardening-Patch-Agent-Implementation-Plan.md`；`GLM-Conductor-v2.0.1-子智能体派发恢复与并行执行诊断.md` |
| `history/v2.1/` | `GLM Conductor v2.1 Runtime Integrity Closure.md`；`GLM-Conductor-v2.1-Architecture-Agent-Implementation-Plan.md`；`GLM-Conductor-v2.1-Dogfood-Records.md`；`GLM-Conductor-v2.1-实施前缺口探查与设计决策记录.md` |
| `history/v2.2/` | 需求计划所列九份（Dogfood-Records / Persistent-Wake-Bridge-Correction / Phase0-Primer-Experiments / Phase0-Scheduled-Wake-Verification / Quota-Continuity-Control-Loop / Quota-Control-Plane-Correction / Release-Hardening / 实施前缺口探查(v2.2) / v2.2.0-Stable-Release）+ `CHANGELOG-2.2.0-full.md`（新建存档）+ `README.md`（新建索引） |
| `history/v2.2.1/` | 需求计划 + 本执行计划（ST-08 收官时移入）+ `README.md`（新建索引） |
| `history/reference/` | `zcode-fr-subagent-hook-inheritance.md`（宿主行为研究参考，非本项目开发史） |

执行时逐份核对唯一信息；发现仍具现行价值的文档不改内容、由 `docs/README.md` 显式链入对应组。分类 commit 与 v2.2 九份搬迁 commit 分离（需求 §18 尾注纪律）。

---

## 6. push 节点与 git 纪律

- **计划节点**：ST-00（main reconcile + 分支建立）、ST-06（远端 CI 证据）、ST-08（发布链）。
- 中途任意时刻需远端验证可直接 push 分支（`on: push` 全分支触发四路矩阵；已获常设授权）。
- 其余时点本地 commit（WU 粒度，沿仓库提交信息风格）；`record_unit_verification` → `finish_unit` → git commit 时序纪律。
- 不 force-push、不 rebase 已发布历史；`.glm-conductor/` 永不入版本控制。

## 7. 风险与红线

- **兼容冻结**（需求 §5）与**无功能蔓延**（§17.2）：实施中发现的好点子记入 v2.3 备忘（收官时汇总），不在 v2.2.1 实施。
- **分解是最大回归面**：每 WU 小提交 + 全量绿 + import 契约测试；暴露隐藏耦合 → ROUTE REASSESSMENT 降 delegability（full → audit，主会话接管）。
- **多进程测试 Windows spawn 陷阱**：模块级 target / 无闭包 / barrier-event 同步 / 有界超时；本地全量用 `PYTHONUTF8=1 python3 -m unittest`（本机 pytest 编码故障），**远端 CI 为权威门**。
- **指纹口径**：单元证据一律 `reconcile.fresh_unit_verification` 返回值；任何修复使先前裁决失效。
- **桥 glitch**：绝不重试 CronDelete/CronUpdate；宿主会话死 → SessionStart 兜底 + 删旧桥重建。
- **自指风险**：实施期强制面 = 缓存 2.2.0 ≠ 工作树；发布后才刷新缓存，无自锁。
- **python 启动器**：本机 plain `python`/`py` 损坏，一律 `python3`。

## 8. 回归矩阵与 DoD 对应

需求 §15 各项映射到上述 WU 的验证列；§16.1（运行时）由 ST-01..04 门覆盖，§16.2（可维护性）由 ST-04 门覆盖，§16.3（测试）由 ST-06 门覆盖，§16.4（仓库）由 ST-05 门覆盖，§16.5（发布）由 ST-07/08 门覆盖。

## 9. 基线记录（ST-00d 填写）

```text
日期: 2026-09-06
分支/提交: v2.2.1-hardening @ 38089ef（基线树；main 同内容合并点 87325ed）
全量套件: 2113 OK / 99.5s（PYTHONUTF8=1 python3 -m unittest discover -s tests）
validator: 15/15（python3 scripts/validate_plugin.py）
CI run: main 87325ed → 34029197451 四路绿；v2.2.1-hardening 建分支首推 → 34029225268 四路绿
伞任务 ID: v221-hardening-2d7f86（route: full/high/high/flash-implementer/resumable；串行 max_workers=1；policy until_done + max_quota_windows=10，authorization.source=user）
桥 automation ID: automation-d5a396ef-d30c-4f6b-8bb2-1269dafa1f9b（recurring 60min，armed_at 2026-09-06T11:11:43Z，boundary five_hour:2026-09-06T15:21:43Z）
```

## 10. 开工确认清单（用户逐项确认后启动 ST-00）

- **A. 桥授权**：`auto_resume = until_done`，`authorization.source = user`（推荐：是——"持续推进"的最优匹配；否决则降 auto_once/manual，连续性相应弱化）
- **B. 窗口预算**：`max_quota_windows = 10`（约对应 10 个跨窗口恢复；可改任意值）
- **C. F3 分支保护**：维持"仅交付方案、ST-08 前单独批准"（D8 默认；如改为直接实施请注明）
- **D. 计划整体确认**：确认后立即执行 ST-00（含 main merge + push）
