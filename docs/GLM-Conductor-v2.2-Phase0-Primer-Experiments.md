# GLM-Conductor v2.2 Phase0 Primer 实验记录（P0-QP 系列）

> 对应修正计划 `docs/GLM-Conductor-v2.2-Quota-Control-Plane-Architecture-Correction-and-Agent-Implementation-Plan.md` §8.3（Feature-Gate + 三重授权）与 §9（P0-QP-00/00A → P0-QP-01..08）。工作单元 `wu-22-P0QP`（主会话主导，C4 Window Primer 的硬前置）。
>
> **纪律**：任何一条硬前置不成立 → C4 停在实验阶段，绝不伪造 epoch / 伪造实验结果。全部通过前 `primer.enabled` 恒 false（当前状态：**primer 特性代码缺席 = 结构性关闭**，运行时 grep 零 primer 实现面，唯一一处提及是 task_manager docstring 的不消费清单）。

## 门总览

| # | 门 | 状态 | 证据 |
|---|----|------|------|
| P0-QP-00 | 凭证与执行路径可达 | **PASS**（2026-09-03 探针实测；含粒度告警） | §1 |
| P0-QP-00A | Primer 授权三条件 | **PASS**（Phase0 期间按「特性缺席=关闭」口径） | §2 |
| P0-QP-01 | 纯 polling 是否自然产生新 reset_at | **PENDING**（run condition 见 §3） | — |
| P0-QP-02 | 最小模型 call 是否物化新 boundary | **PENDING**（依赖 01 结果；run condition 见 §4） | — |
| P0-QP-03 | Prime 与任务同一 Coding Plan 池 | **PASS（弱证据）**（同凭证同端点；百分比粒度限制见 §1.4） | §1 |
| P0-QP-04 | Prime 成本记录 | **首笔已录**（19+38 tokens / 6.15s / 0 个可观测百分点） | §1.3 |
| P0-QP-05 | Rolling-window anchor 行为 | **PENDING**（需跨 ≥2 个窗口观察 reset_at 锚定） | §5 |
| P0-QP-06 | Weekly / multi-window blocking | **PENDING**（需 weekly 耗尽态样本） | §6 |
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

## 3. P0-QP-01：纯 polling 是否自然产生新 reset_at（PENDING）

**问题**：旧 window 到期后，只做 quota 查询（零模型调用），reset_at
是否会自然改变。

**Run condition / 协议**（跨会话执行，二选一）：

- **强隔离版（首选）**：边界时刻（当前窗口 reset_at
  `2026-09-02T23:40:18Z`，其后每个 5h 窗口同样适用）之后，在**普通
  终端**（Git Bash 直跑，无任何 ZCode/agent 会话活跃——避免会话自身
  模型调用污染）执行：
  `python3 plugins/glm-conductor/runtime/cli.py quota-resolve . --force-refresh`
  每 5 分钟一次 × 6 次，记录 reset_at 是否从旧值推进。
- **会话内版（带污染告警）**：wake/恢复会话首个工具动作即
  quota-resolve --force-refresh——但会话首回合模型调用先于任何工具
  执行，观察结果只能作弱证据（reset_at 已推进 ≠ polling 自然产生，
  可能被本回合调用物化）。

**判定**：强隔离版下 reset_at 推进 → 01 正向（polling 自然产生，primer
的物化假设被削弱）；不推进 → 进入 02 实验。

## 4. P0-QP-02：最小模型 call 是否物化新 boundary（PENDING，依赖 01）

**问题**：`old boundary Bn → minimal call → quota refresh → boundary
Bn+1` 是否成立，需可重复证据。

**Run condition**：仅当 01 强隔离版显示「polling 不产生新 reset_at」后，
在同一普通终端（保持零其他调用）执行一次最小 prime（§1.5 形态）→
立即 quota-resolve --force-refresh → 记录 reset_at。重复 ≥2 个边界周期
取证。注意 P0-QP-07：同旧 boundary 绝不连发第二次 prime。

## 5. P0-QP-05：Rolling-window anchor 行为（PENDING）

**问题**：新窗口 next reset_at 是否受 prime/首调用时刻影响。
**Run condition**：跨 ≥2 个窗口记录（新窗口 reset_at − 首次观测到它的
时刻）与（新窗口 reset_at − 旧窗口 reset_at）的关系；若 anchor 随调用
时刻漂移 → C4/C5 必须把 reset_at 定义为 provider-observed dynamic
boundary（不得从旧 boundary 线性外推——与 §10 epoch 模型一致）。

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
- 记录人：主会话（wu-22-P0QP，executor=main）；journal 事件随各门
  取证追加。
