# GLM-Conductor v2.2 Phase0 Primer 实验记录（P0-QP 系列）

> 对应修正计划 `docs/GLM-Conductor-v2.2-Quota-Control-Plane-Architecture-Correction-and-Agent-Implementation-Plan.md` §8.3（Feature-Gate + 三重授权）与 §9（P0-QP-00/00A → P0-QP-01..08）。工作单元 `wu-22-P0QP`（主会话主导，C4 Window Primer 的硬前置）。
>
> **纪律**：任何一条硬前置不成立 → C4 停在实验阶段，绝不伪造 epoch / 伪造实验结果。全部通过前 `primer.enabled` 恒 false（当前状态：**primer 特性代码缺席 = 结构性关闭**，运行时 grep 零 primer 实现面，唯一一处提及是 task_manager docstring 的不消费清单）。

## 门总览

| # | 门 | 状态 | 证据 |
|---|----|------|------|
| P0-QP-00 | 凭证与执行路径可达 | **PASS**（2026-09-03 探针实测；含粒度告警） | §1 |
| P0-QP-00A | Primer 授权三条件 | **PASS**（Phase0 期间按「特性缺席=关闭」口径） | §2 |
| P0-QP-01 | 纯 polling 是否自然产生新 reset_at | **CLOSED（负向）**——2026-09-03 用户机制裁决 + 02:14Z 首触锚定佐证 | §3 |
| P0-QP-02 | 最小模型 call 是否物化新 boundary | **CLOSED（确认）**——同上；C4 走全量分支 B | §4 |
| P0-QP-03 | Prime 与任务同一 Coding Plan 池 | **PASS（弱证据）**（同凭证同端点；百分比粒度限制见 §1.4） | §1 |
| P0-QP-04 | Prime 成本记录 | **首笔已录**（19+38 tokens / 6.15s / 0 个可观测百分点） | §1.3 |
| P0-QP-05 | Rolling-window anchor 行为 | **PASS（实证确认）**——reset_at 锚定过期后首触物化时刻，恒不得从旧边界外推 | §5 |
| P0-QP-06 | Weekly / multi-window blocking | **机制确认（2026-09-03 用户裁决：weekly 优先判定、不得归 0）**——control.py 阻塞窗语义已机械对齐（QC-05）；真实 weekly 耗尽样本转 C11 机会性佐证 | §6 |
| P0-QP-07 | Prime 幂等（同旧 boundary 不连发） | **PENDING（设计门）**（C4 primer.py 实现时落地+测试） | §7 |
| P0-QP-08 | 无 demand 时禁止 Prime（PASSIVE 零 control-plane model call） | **PASS（结构审计）** | §8 |

---

## 1. P0-QP-00：凭证与执行路径可达（PASS，2026-09-03 实测）

探针脚本 `.glm-conductor/tmp/p0qp00_prime_probe.py`（不入库；可复跑，形态见 §1.5）。凭证只经 `runtime/quota/credentials.resolve_credential()` 进内存，**任何产物不含 key 材料**。

### 1.1 quota 查询凭证可用 — PASS

`quota-resolve --force-refresh` 持续可用（本任务全程依赖；resolver 四级层级，provider=zai 抓取成功）。凭证来源 `zcode-provider`（`~/.zcode/v2/config.json` provider `builtin:bigmodel-coding-plan`，options 含 `apiKey` + `baseURL`）。

### 1.2 model prime 调用凭证也可用 + 真实 provider path — PASS

单次最小 Messages 调用（2026-09-03 05:14Z，北京）：

```text
POST https://open.bigmodel.cn/api/anthropic/v1/messages
鉴权：x-api-key 头（Anthropic 原生格式；首发即 200，无需 Bearer 回退）
anthropic-version: 2023-06-01
model: GLM-5.3-Flash（coding plan 家族最便宜档）  max_tokens: 64
body: [{"role":"user","content":"Reply with a single word: pong"}]

HTTP 200（latency 6150ms）
id=msg_2026090305142158c7e524f74647be  model=glm-5.3-flash
stop_reason=end_turn  service_tier=standard
usage: input_tokens=19  output_tokens=38（含 thinking 块——GLM-5.3-Flash
默认开思考，最小 prompt 也有思考开销；content 首块为 thinking）
```

端点即 ZCode 宿主本会话模型调用所用的同一 Anthropic 兼容路径（provider
kind=anthropic，baseURL 同源）——「primer 请求能通过实际 provider path
发出」成立。

### 1.3 P0-QP-04 成本首笔

```text
request tokens: 19（input）+ 38（output，含 thinking）
quota percentage delta: 0（36.0% → 36.0%，整百分点监控粒度不可见）
latency: 6150ms
```

### 1.4 P0-QP-03 同池判定（弱证据 + 告警）

同凭证（同一 coding plan key）+ 同端点域（open.bigmodel.cn coding plan
Anthropic 路径，即宿主会话计费路径）+ 响应 `service_tier=standard`。
**告警**：监控端点为整百分点粒度，57-token 调用不可见 delta——「同池」
无法用百分比 delta 直接证明，当前证据为凭证与端点同源推断。C4 设计
含义：**primer 不得以「百分比下降」自证生效**；判断物化只能看
reset_at/boundary 变化（这也正是 §10 epoch 模型的方向）。

### 1.5 无 secret 落盘 / 日志 — PASS（含一笔过程教训）

- 探针输出白名单字段（id/model/usage/stop_reason/120 字符 content 预览），
  原始响应不落盘，key 不进 stdout/stderr/异常文本（§37 条款沿用）。
- **过程教训（如实记录）**：2026-09-03 检查 provider 配置结构时，一次
  中间调试输出的脱敏函数按「值内容含 key/token 字样」掩码而非按键名
  掩码，导致 `apiKey` 值打进了 ZCode 会话记录（本地 session 存储层）。
  未落任何仓库文件 / git 提交 / journal / 本文档。纠正：后续一切结构
  检查按键名脱敏；C4 实现时 primer 凭证路径只走 `resolve_credential()`。

### 1.6 复现形态（无 secret）

请求形态见 §1.2（端点/头/体逐字）；凭证解析走
`runtime/quota/credentials.resolve_credential()`；quota 前后读数走
`resolver.resolve_quota_detail(force_refresh=True)`。探针脚本留档于
`.glm-conductor/tmp/p0qp00_prime_probe.py`（账本目录，不入 git）。

---

## 2. P0-QP-00A：Primer 授权三条件（PASS）

```text
primer.enabled == true        → Phase0 期间为 false（特性代码缺席=结构性
                                关闭；C0 已把文档示例钉为 false）
auto_resume ∈ {auto_once, until_done}
                              → 任务账本 execution_policy.continuity.
                                auto_resume = "until_done" ✓
authorization.source == user  → 任务账本 authorization.source = "user"
                                （confirmed_at 2026-09-01T16:15:14+00:00）✓
```

Phase0 实验本身（§1 探针）是操作者显式执行的被批准实验（修正计划 §9
明文要求的实验），不是 primer 特性路径；primer 特性启用（enabled=true）
只可能在全部 P0-QP 门通过之后由 C4 落地、且恒受本三重门约束。
`manual / notify` 不得 prime——C4 实现时必须机械强制（非文档约定）。

---

## 3. P0-QP-01：纯 polling 是否自然产生新 reset_at（CLOSED——负向）

**2026-09-03 用户机制裁决（最高权威）**：provider 窗口机制确认——到达
reset_at 后窗口恢复 100%（weekly 优先判定、weekly 不得归 0）；**下一轮
reset_at 必须在新窗口中产生模型调用才会物化刷新，纯 quota 查询永远
不推进**。此即修正计划 §8.1 的"若"字假设，由设计者本人实证确认，
与本文件 02:14Z 首触锚定数据（新 reset_at=物化时刻+5h00m01s）完全
吻合。**01 负向关闭**。

**佐证采集（非阻塞）**：C3 watcher（pid 181160）+ boundary recorder
（pid 183340，被动读 watcher.json 每 60s 落 CSV）持续常驻——夜间
reset_at 冻结、晨间首次会话调用后翻转的完整时间线将作为 C11 dogfood
佐证回填本节；不再构成任何门的阻塞项。

## 4. P0-QP-02：最小模型 call 是否物化新 boundary（CLOSED——确认）

**同上裁决：确认**。§1 的探针已给出首笔可重复证据形态（最小 Messages
调用 → 刷新 → 观察边界变化；HTTP 200 本身不构成证据，§C4 红线）。
**C4 走全量分支 B**：primer.py 按已冻结设计实施（§8.3 三重授权闸
机械强制、幂等键=(provider_identity, 旧 boundary/epoch_id) 单飞 +
超时单次有界重试、物化确认只看二次 refresh 后 reset_at/boundary
变化、绝不用百分比下降自证、weekly 阻塞 → 无 ActivationReady）；
primer.enabled 恒 false 缺省直至用户显式启用。

## 5. P0-QP-05：Rolling-window anchor 行为（PASS——实证确认）

**问题**：新窗口 next reset_at 是否受 prime/首调用时刻影响。

**结论（2026-09-03T02:14Z 钉死）**：**是——reset_at 锚定「过期后首次
触碰」的物化时刻 + 5h，绝不能从旧 boundary 线性外推。**

证据链（全部本机实测）：

| 旧 reset_at | 过期后首触 | 新 reset_at | 新−首触 | 新−旧边界 |
|---|---|---|---|---|
| 2026-09-02T21:59:00Z | ~03:19Z（活跃会话，延迟≈2m） | 03:19:22Z | ≈5h+秒级 | 5h20m22s |
| 2026-09-02T03:19:22Z | ~08:35Z（活跃） | 08:35:48Z | ≈5h+秒级 | 5h16m26s |
| 2026-09-02T18:36:24Z | ~21:2xZ→23:40Z 间（活跃） | 23:40:18Z | ≈5h+秒级 | 5h3m54s |
| 2026-09-02T23:40:18Z | **02:14:26.869Z（空闲 2h34m 后首触）** | **07:14:27Z** | **5h00m01s** | **7h34m09s** |

空闲 2.5 小时的首触样本把规则从"活跃期 >5h 间隙的模糊拟合"变成显式
判别：新边界 = 首触时刻 + 5h（误差 ≤1s），与旧边界毫无外推关系。

**C4/C5 设计含义（冻结级）**：
- reset_at 一律按 provider-observed dynamic boundary 消费（§10 epoch
  模型既有立场，此处实证背书）；任何"旧 boundary + 固定周期"的推算
  都是错的。
- 可执行边界（executable boundary）的等待时长天然不确定（物化延迟
  = 空闲时长），resume 决策必须以观察为准，不得以时间表为准。
- 该行为同时意味着：**长时间空闲会"推迟"下一窗口起点**——额度时钟
  只在触碰后走表，对 dogfood 排窗与成本规划是实打实的语义。

## 6. P0-QP-06：Weekly / multi-window blocking（PENDING）

**Run condition**：需要 `5h available + weekly exhausted` 真实样本。
当前窗口无 weekly 阻塞，机会性等待（watcher ACTIVE 观察或用户遇到时
手动记录）。C4 实现时无论如何必须机械保证：多窗任一阻塞 → 不得产生
ActivationReady（control.evaluate_task_quota_phase 已有 BLOCKED 语义，
primer 侧归 C4 对齐）。

## 7. P0-QP-07：Prime 幂等（PENDING，设计门）

网络超时 / watcher restart 时不得对同旧 boundary 连发多个 prime。
C4 `primer.py` 实现要求：同 boundary 幂等键（类比
record_quota_boundary_consumed 的 task_id+epoch_id 模式）+ 超时单次
重试上限 + 测试锚定。本门在 C4 实现时随代码取证关闭。

## 8. P0-QP-08：无 demand 时禁止 Prime（PASS，结构审计）

- C3 watcher（`runtime/quota/watcher.py`）全文件无任何模型调用面：
  fetch 只走 `resolver.resolve_quota_detail`（quota 监控端点，非模型
  端点）；PASSIVE 分支无 prime / 无 activation。
- 运行时全仓 grep：模型调用端点（`/api/anthropic/v1/messages` 等）零
  出现于 `plugins/glm-conductor/`（探针脚本在 `.glm-conductor/tmp/`，
  非运行时代码）。
- Phase0 之后 primer 调用点唯一 = C4 primer.py，且受 §8.3 三重门。

---

## 时间线与环境

- 实验环境：Windows 10 19042 / Python 3.7.9（sys.executable 商店版）；
  网络：Clash Verge 代理环境下直连 open.bigmodel.cn 成功。
- 探针时 quota 状态：AVAILABLE，five_hour 36.0%（used 64%），reset_at
  `2026-09-02T23:40:18Z`（探针自身消耗未在整百分点上可见）。
- **2026-09-03T02:14-02:15Z 增补**：跨旧边界空闲 2h34m 后首触物化
  （§3/§5 数据）；C3 watcher 常驻启动（pid 181160，ACTIVE，首观察
  epoch_id `glm:163e156bcbec8e7b` / probe 边界 07:19:27Z）——watcher
  自此作为 P0-QP-01 的 poll-only 仪器持续取证。
- **流程教训记录（用户指令 2026-09-03T02:1xZ"为什么没有持续推进，
  这个问题需要记录下，后续优化"）**：上轮收轮（09-02T21:45Z）时把
  时间窗实验登记为"需用户手工配合"，未把已建成的 C3 watcher 接入
  实验执行路径，导致旧边界（23:40:18Z）观测窗口完全空转 2.5 小时。
  优化动作（已执行）：watcher 常驻化承担时间窗取证；后续规则：
  **凡时间窗约束的实验/等待，优先部署常驻自动化载体（watcher /
  bridge），把"等用户"降级为最后手段**。结构性缺口（无 CronCreate
  会话无法建桥自唤醒）归 C6 activation transport 解决。
- 记录人：主会话（wu-22-P0QP，executor=main）；journal 事件随各门
  取证追加。
