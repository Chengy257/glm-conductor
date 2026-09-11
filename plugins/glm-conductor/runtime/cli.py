#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GLM Conductor v2.1 runtime CLI（M1 policy + M2 permit + M3 agent-runs
+ M4 dispatch wave + M5 quota 连续性 + M6 溯源/审查）。

职责：
    以单行 JSON stdout 提供 execution_policy 三个子命令（M1）、
    dispatch permit 三个子命令（M2 前半，runtime.dispatch_wave 的人工
    操作面）与 agent run 账本只读查询（M3 前半，runtime.agent_run 纯读
    账本面）。M1 只落数据层与 CLI 骨架——policy 部分不接线任何 hook /
    task_manager 消费方（那是 M2+ 的事）；permit 部分是原语层的薄壳
    （签发仍归 task_manager.prepare_dispatch，这里只做 list / show /
    consume）；agent-runs 是账本/档案的纯读薄壳（零写副作用，任务不
    存在时账本为空数组——journal 是唯一真相源，不做任务存在闸）。
    v2.1 后半程（wu-21-15 横切收口）补齐：M4 wave 事务两子命令、
    M5 quota 连续性五子命令（resolve / exhausted / resume / wake-record /
    wake-prompt）、M6 溯源三子命令（verify-unit / verify-task /
    review-record）与 manifest 只读查询——全部为对应 runtime API 的
    薄壳，技能层 runtime 调用一律走本 CLI（禁止 python3 -c 内联）。
    v2.3.0 W3 补齐 wake-arm（persistent arm 生产入口：arm_transport
    稳定通道记账 + next_run_at=wake_at 锚定，fail-open）与 wake-retime
    （fire 后「判定 + retime」）——同一薄壳纪律。

子命令：
    policy-show <repo_root> <task_id>
        展示任务 execution_policy。state 存在 execution_policy 块 →
        原样展示（policy_source="state"）；缺该键（legacy 形态）→
        展示默认块（policy_source="default"），不写盘。只读，不改任何
        文件。
    policy-set-parallel <repo_root> <task_id> <max_workers>
        以用户身份写入并发授权：authorization.source="user"、
        confirmed_at=当前 UTC 时刻（本命令即用户确认动作的落笔）；
        max_workers=1 → serial，2-4 → standard；default_workers 超过
        新上限时由 setter 同步下调。写盘经 state.save_state（全量
        校验闸，含状态转换门），不绕过校验。
    policy-set-resume <repo_root> <task_id> <auto_resume>
                      [max_quota_windows]
        以用户身份写入续跑授权（同上 source/confirmed_at 口径）；
        max_quota_windows 缺省按 auto_resume 推导（manual/notify → 0，
        auto_once → 1，until_done → 1），显式给出时照传。
    permits <repo_root> <task_id>
        列出任务全部活跃 dispatch permit（v2.1 M2，runtime.
        dispatch_wave.list_permits——按 permit_id 排序；无 permit 输出
        空列表形态）。只读。
    permit-show <repo_root> <task_id> <permit_id>
        展示单张 permit（load_permit 原样 dict）；不存在 / 已消费 /
        已失效 → 退出码 1。
    permit-consume <repo_root> <task_id> <permit_id>
        消费 permit（原子 rename 防重放）；成功输出 consumed=true，
        无活跃 permit 可消费 → 退出码 1（fail-closed，不做静默空操作）。
    agent-runs <repo_root> <task_id> [unit]
        agent run 账本只读查询（v2.1 M3 前半，runtime.agent_run）。
        缺省 unit → 输出 list_agent_runs 的 run 记录数组（journal 聚合，
        文件时间序；无 agent_launched/agent_dispatch_failed 事件输出
        空数组）；给定 unit → 输出 run_lifecycle 单元视角汇总 dict
        （launch/失败计数 + 最后已知 agent_id 的原生档案观察 +
        possibly_zombie/archived_terminal 僵尸语义二标注，§7.3：
        status=="running" 绝不解读为存活）。纯读：不写 journal /
        state / 档案；任务不存在同空账本（退出码仍 0）。
    wave-prepare <repo_root> <task_id> [quota_status] [max_workers]
        批量派发准备（v2.1 M4 wu-21-08，task_manager.
        prepare_dispatch_wave 薄壳）：一次调用完成「决策 → 全量租约 →
        wave 记录 → 批量 permit → journal」。成功输出 wave_id / units /
        worker_budget / permits（permit dict 列表）/ markers
        （GLM_CONDUCTOR_DISPATCH=<permit_id>，可直接放进 Agent prompt）/
        deferred / waiting_quota；任务缺失或决策未批准（TaskManagerError）
        → 退出码 1；quota_status / max_workers 非法 → 退出码 2。
    wave-show <repo_root> <task_id> [wave_id]
        wave 记录只读查询（v2.1 M4 wu-21-08）。缺省 wave_id → 输出
        {"task_id", "waves": [...]}（无 waves 键输出空数组）；给定
        wave_id → 输出该 wave 记录原样 dict；wave 不存在或任务不存在
        → 退出码 1。
    manifest-show <repo_root> <task_id>
        Resume Manifest 只读查询（v2.1 M3，runtime.resume_manifest）。
        输出 {"manifest": <dict|null>}——manifest 是派生压缩层，缺失 /
        损坏输出 null 不是错误（read 侧永不抛）；任务存在与否不设闸。
    quota-resolve <repo_root> [--force-refresh]
        额度四态解析（v2.1 M5 wu-21-10，runtime.quota.resolver.
        resolve_quota_status 薄壳）：四级层级（新鲜缓存 → provider →
        陈旧缓存 → UNKNOWN）解析当前额度状态，输出键冻结 dict
        {status, source, evaluated_at, reason}（绝不含凭证材料）。
        --force-refresh 跳过层级 1 强制走 provider（§32 唤醒强制刷新
        语义）。观测面：provider 成功时写额度缓存，零任务转态。
    quota-observe <repo_root> [task_id]
        自适应额度观测（v2.2 M3 wu-22-03，runtime.quota.observer.
        observe 薄壳）：resolver.resolve_quota_detail（四级层级，
        lazy heartbeat 的「进 runtime 先刷新」入口）+ state.json
        只读装配 task 侧输入 → §16.1 观测 dict 八键
        {provider_status, execution_phase, remaining_percent,
        reset_at, next_check_at, reason, wake_recommended,
        wake_required}。task_id 缺省按无任务保守默认（task_active=
        True、bridge=none、阈值/预算全默认）；给定 task_id 时任务
        缺失 → 退出码 1。零 state 写、零 automation。
    quota-phase <repo_root> <task_id>
        执行相决策（v2.2 M3 wu-22-03，runtime.quota.control.
        evaluate_task_quota_phase 薄壳）：与 quota-observe 同源输入
        → §17.1 冻结 8 键决策 dict 原样直出；任务缺失 → 退出码 1。
    verify-unit <repo_root> <task_id> <uid> [command]
        受限执行单元已声明验证命令并产出溯源凭证（v2.1 M6 wu-21-12，
        runtime.provenance.verify_unit 薄壳）：command 缺省执行该单元
        verification 全部 required 命令；显式 command 须逐字 ∈ required
        清单（D4 白名单闸），全部命令先过 policy 闸（deny/ask 即拒）。
        输出 {unit, receipts, exit_codes, all_passed}；all_passed=False
        （exit_code 非 0 或超时）→ 退出码 1——证据没过就是失败口径；
        任务/单元缺失、白名单或 policy 拒绝（ProvenanceError）→ 退出
        码 1。
    verify-task <repo_root> <task_id> [command]
        任务作用域同构薄壳（runtime.provenance.verify_task）：白名单 =
        state.verification.required（Stop 完成门 §19 同一清单）；成功
        路径同步任务级完成门证据。输出形状与 verify-unit 同构（task 键
        替代 unit 键），退出码口径相同。
    review-record <repo_root> <task_id> <reviewer> <verdict>
                  <tool_use_id> [route] [note]
        申报一次已发生的审查并落 durable review receipt（v2.1 M6
        wu-21-13，runtime.provenance.run_review 薄壳）：verdict 须 ∈
        state.REVIEW_VERDICTS；route ∈ solo/delegate/audit/full（缺省
        None）；note 缺省 None。RB-21-02 起落证前机械回验调用真实性
        （reviewer ∈ agent_run.REVIEWER_PROFILES 白名单 + 任务 journal
        内 tool_use_id 绑定的 reviewer_invoked 事件 + reviewer/task
        匹配 + 不可 replay——审查派发 prompt 必须携带
        GLM_CONDUCTOR_REVIEW=<task_id> marker）。输出 receipt dict（与
        落盘文件、journal review_receipt 事件逐字段一致——Stop 完成门
        只认 fresh ship review receipt，本子命令是该 receipt 的唯一
        CLI 产出点）。verdict / route 非法 → 退出码 2；任务缺失或回验
        被拒（ProvenanceError）→ 退出码 1。
    quota-exhausted <repo_root> <task_id>
        EXHAUSTED 转态链 + 授权矩阵裁决（v2.1 M5 §14.1，
        task_manager.handle_quota_exhausted 薄壳；evaluation 不经 CLI
        传——None 不虚构 recommended_resume_at）。输出冻结键 dict
        {task_status, waiting_units, recommended_resume_at, auto_resume,
        remaining_quota_windows, wake, reason}。任务缺失或不在执行态族
        （TaskManagerError）→ 退出码 1。
    quota-resume <repo_root> <task_id> [status]
        额度唤醒 / SessionStart 的恢复首步（v2.1 M5 §14，
        task_manager.resume_from_quota 薄壳）：status 缺省经 resolver
        四级层级解析（四级来源记入 journal quota_resolved）；显式四态
        （QUOTA_STATUSES）直通（source="explicit"，零解析零网络）。
        输出 {resumed, status, recommended_resume_at,
        wake_budget_remaining}；已注册且启用订阅的任务另含 additive 键
        "subscription"（资格面，v2.2 C5b）与转态恢复时的 "consumption"
        （消费记账面，v2.2 C7 §15.1 消费事务点——consumed=False 附中文
        reason / error 降级形态）。status 非法 → 退出码 2；任务缺失 →
        退出码 1；恢复链 OSError（证据写失败）→ 退出码 3 + {error,
        guidance}（durable 转态可能已落，幂等重跑安全）；EXHAUSTED /
        UNKNOWN 保守等待（resumed=false，零转态）是合法结果——退出码
        仍 0。
    wake-record / wake-arm / wake-prompt / wake-plan / wake-status /
            wake-retime / wake-reconcile / transport-status
            <repo_root> <task_id> [...]
        Persistent Wake Bridge / Activation Transport 八子命令（v2.1
        M5 §14.4 起分批建设：wake-record / wake-prompt；v2.2 M5
        wu-22-05：wake-plan / wake-status；v2.2 C6：wake-reconcile /
        transport-status；v2.3.0 W3：wake-arm / wake-retime 与
        arm/fire 时 retime 锚点）。实现已迁 runtime/commands/wake.py
        （v2.3.1 Wave 3，unit w3-wake-split）——cli 仅存参数校验分支 +
        函数内 import 调用；stdout 形态（单行 JSON；wake-prompt 成功
        路径为纯文本；wake-plan / wake-status / transport-status 走
        ensure_ascii=False 观测面）、失败 JSON 面 {"error": ...} 与退
        出码（0 成功 / 1 任务缺失·事务被拒 / 2 用法或参数值非法；
        wake-retime 无桥与决策异常 fail-open 恒 0）逐字节不变，键集与
        语义明细见该模块 docstring。
    quota-watcher <repo_root> start|status|stop|once
        Real-Time Quota Watcher 操作面（v2.2 修正计划 C3 wu-22-C3，
        §6/§6.1，runtime.quota.watcher / watcher_store 薄壳）：
        start 以 **sys.executable** 分离派生子进程运行本 CLI 的内部
        serve 形态 `quota-watcher <repo_root> --serve`（隐藏形态，仅
        由 start 派生使用；stdout/stderr 落
        .glm-conductor/quota/watcher.log），派生成功即返回 pid（子
        进程内的单实例锁冲突只落日志，不在 start 同步上报）；status
        读 watcher.json 输出摘要（mode/pid/generation/heartbeat/
        staleness/last_observation）；stop 置 stop_requested 旗标
        （原子写回，单次操作绝不轮询等待退出）；once 前台单次抓取
        （测试/诊断用；锁被活进程新鲜持有时报冲突退出码 1）。watcher
        只写自身状态文件 .glm-conductor/quota/watcher.json，绝不写
        任务 state/journal；第一阶段零模型调用、不 prime、不发
        activation（§6.1 skeleton-first）。
    quota-clock-plan / quota-clock-bind / quota-clock-tick /
            quota-clock-status <repo_root> [...]
        Global Quota Clock 四子命令（v2.3.0 §8，unit v23-w2b）：
        纯规划（零写盘、零 DB、零 state）/ 绑定 automation +
        用户级 state + 首次 retime / Scheduled Task 唯一主入口 /
        绑定健康只读汇总（判定落 clock 纯函数层）。实现已迁
        runtime/commands/quota_clock.py（v2.3.1 Wave 3，unit
        w3-quota-clock-split）——cli 仅存参数校验分支 + 函数内
        import 调用；输出冻结键集、失败 JSON 面 {"status":
        "error" | "not_plannable", ...} 与退出码逐字节不变，
        键集与语义明细见该模块 docstring。

输出与退出码契约：
    stdout 恒为单行 JSON（json.dumps(..., ensure_ascii=True)，中文以
    \\uXXXX 转义——管道 / Windows 控制台零编码依赖；例外：wake-prompt
    的成功路径为纯文本 prompt；v2.2 M3 起 quota-observe / quota-phase
    为 ensure_ascii=False（wu-22-03 规格冻结：中文 reason 面向主会话
    直接阅读，显式 UTF-8 落 stdout）；v2.2 M5 起 wake-plan / wake-
    status 同走 ensure_ascii=False（§22.2/§22.3 中文 reason / prompt
    面向主会话直接阅读，同一观测面口径））；stderr 不承载结构化输出。
    v2.3 quota-clock 四子命令的失败路径为自绘 JSON 面 {"status":
    "error" | "not_plannable", "error", ...}（status 键恒在，退出码
    1；参数个数错误仍走 _UsageError → {"error": ...} / 退出码 2 的
    既有惯例）。
    退出码：
      0 = 成功；
      2 = 校验拒绝（用法错误 / 参数值非法 / setter 抛 ValueError /
          save_state 校验闸或状态转换门拒绝——含盘上 state.json 损坏
          的解析拒绝）；
      1 = 异常（任务不存在 / 溯源执行被拒 / 事务被拒 / 意外错误；
          错误 JSON 只含异常类型名与消息，供操作者排查）；
      3 = durable-but-degraded（v2.2 C7，仅 quota-resume：恢复链
          OSError——state 落盘 / journal / mark / consumption 证据写
          失败；错误 JSON 面 {error, guidance}：durable 转态可能已落
          盘、quota-resume 幂等重跑安全、检查任务 journal 核对 mark /
          consumption 记账）。

依赖方向：
    本模块是薄壳：校验与变换都在 runtime.execution_policy /
    runtime.state 内，这里只做 argv 解析、JSON 输出与退出码映射。
    插件根经 sys.path 引导（runtime/quota/report.py 同款），因此
    `python3 plugins/glm-conductor/runtime/cli.py ...` 可在仓库根
    直接运行。

依赖：
    仅 Python 3 标准库（datetime / json / pathlib / sys），零第三方
    依赖。
"""

import datetime
import json
import pathlib
import sys

# 接线插件根以复用 runtime（CLI 脚本与 runtime/ 同插件；quota report 同款）
PLUGIN_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from runtime import agent_run, dispatch_wave, execution_policy, state  # noqa: E402
from runtime.quota.parser import QUOTA_STATUSES  # noqa: E402

USAGE = (
    "用法: python3 plugins/glm-conductor/runtime/cli.py "
    "policy-show <repo_root> <task_id> | "
    "policy-set-parallel <repo_root> <task_id> <max_workers> | "
    "policy-set-resume <repo_root> <task_id> <auto_resume> "
    "[max_quota_windows] | "
    "permits <repo_root> <task_id> | "
    "permit-show <repo_root> <task_id> <permit_id> | "
    "permit-consume <repo_root> <task_id> <permit_id> | "
    "agent-runs <repo_root> <task_id> [unit] | "
    "manifest-show <repo_root> <task_id> | "
    "wave-prepare <repo_root> <task_id> [quota_status] [max_workers] | "
    "wave-show <repo_root> <task_id> [wave_id] | "
    "quota-resolve <repo_root> [--force-refresh] | "
    "quota-observe <repo_root> [task_id] | "
    "quota-phase <repo_root> <task_id> | "
    "verify-unit <repo_root> <task_id> <uid> [command] | "
    "verify-task <repo_root> <task_id> [command] | "
    "review-record <repo_root> <task_id> <reviewer> <verdict> "
    "<tool_use_id> [route] [note] | "
    "quota-exhausted <repo_root> <task_id> | "
    "quota-resume <repo_root> <task_id> [status] | "
    "wake-record <repo_root> <task_id> <automation_id> <fires_at> "
    "[--db <path>] | "
    "wake-arm <repo_root> <task_id> <automation_id> [--db <path>] | "
    "wake-prompt <repo_root> <task_id> | "
    "wake-plan <repo_root> <task_id> | "
    "wake-status <repo_root> <task_id> | "
    "wake-retime <repo_root> <task_id> | "
    "wake-reconcile <repo_root> <task_id> <host_status> [observed_at] | "
    "transport-status <repo_root> <task_id> | "
    "quota-watcher <repo_root> start|status|stop|once | "
    "quota-clock-plan <repo_root> | "
    "quota-clock-bind <repo_root> <automation_id> [--db <path>] | "
    "quota-clock-tick <repo_root> | "
    "quota-clock-status <repo_root> | "
    "host-check [--db <path>]")

# policy-set-resume 的 max_quota_windows 缺省推导表（§5.4 耦合的
# 最小合法值：until_done 取下界 1，保守不放大）
AUTO_RESUME_DEFAULT_WINDOWS = {
    "manual": 0, "notify": 0, "auto_once": 1, "until_done": 1}


class _UsageError(ValueError):
    """用法 / 子命令 / 参数个数错误——校验拒绝类，退出码 2。"""


class _TaskMissing(Exception):
    """任务不存在（无 state.json）——运行期异常，退出码 1。"""


class _PermitMissing(Exception):
    """permit 不存在（或已消费 / 已失效，load 按不存在处理）——运行期
    异常，退出码 1（fail-closed：consume 空操作显式报错而非静默成功）。"""


class _WaveMissing(Exception):
    """wave 记录不存在——运行期异常，退出码 1。"""


class _WaveRejected(Exception):
    """wave 准备被派发事务层拒绝（任务缺失 / 决策未批准 /
    TaskManagerError）——运行期拒绝，退出码 1（区别于参数值非法的
    退出码 2）。"""


class _QuotaFlowRejected(Exception):
    """quota 连续性事务被 task_manager 拒绝（任务缺失 / 状态族不符 /
    TaskManagerError；quota-exhausted / quota-resume / wake-record /
    wake-prompt 共用）——运行期拒绝，退出码 1。"""


class _QuotaFlowDegraded(Exception):
    """quota-resume 恢复链遭遇 I/O 失败（OSError：state 落盘 / journal
    / mark / consumption 证据写；v2.2 C7 wu-22-C7 ②）——durable-but-
    degraded，退出码 3：durable 转态可能已落盘、quota-resume 幂等重跑
    安全（区别于 1 拒绝 / 2 参数；错误 JSON 面 {error, guidance}）。"""


class _VerifyRejected(Exception):
    """溯源执行 / 审查申报被 provenance 拒绝（任务或单元缺失、白名单
    闸 / policy 闸、ProvenanceError；verify-unit / verify-task /
    review-record 共用）——运行期拒绝，退出码 1。"""


class _QuotaWatcherConflict(Exception):
    """quota-watcher 单实例锁冲突（§6 冻结：现存记录 pid 活着且
    heartbeat 新鲜时，once / serve 拒绝执行）——运行期拒绝，退出码 1。"""


def _emit(payload):
    """向 stdout 写单行 JSON（ensure_ascii=True，契约锚点）。"""
    sys.stdout.write(json.dumps(payload, ensure_ascii=True) + "\n")


def _force_utf8_stdout():
    """把 stdout 调到 UTF-8（wake-prompt 纯文本输出用；quota/report.py
    同款——非 TTY / 测试捕获（StringIO）容错跳过）。"""
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError, OSError):
        pass  # 测试捕获（StringIO）或已被重定向：保持原样


def _emit_utf8(payload):
    """向 stdout 写单行 JSON（ensure_ascii=False；v2.2 M3 观测面
    quota-observe / quota-phase 专用——wu-22-03 规格冻结中文 reason
    不转义、面向主会话直接阅读；显式 UTF-8 落 stdout，Windows 管道
    / 控制台零乱码）。"""
    _force_utf8_stdout()
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _now_iso8601() -> str:
    """当前 UTC 时刻的 ISO8601 字符串（授权 confirmed_at 落笔口径）。"""
    return datetime.datetime.now(
        datetime.timezone.utc).isoformat(timespec="seconds")


def _require_task(repo_root, task_id) -> dict:
    """读取任务 state；不存在 → _TaskMissing（退出码 1）；
    JSON 损坏由 load_state 抛 ValueError（退出码 2：校验/状态层拒绝）。"""
    st = state.load_state(repo_root, task_id)
    if st is None:
        raise _TaskMissing(
            "任务 %s 不存在（%s 下无 state.json），无法操作 "
            "execution_policy" % (task_id, repo_root))
    return st


def _base_policy(task_state) -> dict:
    """取任务的 execution_policy 底块；缺键 / 形状异常（legacy）→
    保守默认块（setter 在完整底块上做局部更新，半块不被放大）。"""
    block = task_state.get("execution_policy")
    if isinstance(block, dict):
        return block
    return execution_policy.default_execution_policy()


def _parse_int(func_name, field, raw):
    """把 CLI 字符串参数解析为 int；失败抛 ValueError（退出码 2）。"""
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise ValueError(
            "%s：%s %r 不是整数" % (func_name, field, raw))


def _policy_show(repo_root, task_id) -> int:
    """policy-show：只读展示，legacy 缺键 → 默认块，不写盘。"""
    st = state.load_state(repo_root, task_id)
    if st is None:
        raise _TaskMissing(
            "任务 %s 不存在（%s 下无 state.json），无法展示 "
            "execution_policy" % (task_id, repo_root))
    block = st.get("execution_policy")
    if isinstance(block, dict):
        _emit({"task_id": st.get("task_id", task_id),
               "policy_source": "state", "execution_policy": block})
    else:
        _emit({"task_id": st.get("task_id", task_id),
               "policy_source": "default",
               "execution_policy":
                   execution_policy.default_execution_policy()})
    return 0


def _policy_set_parallel(repo_root, task_id, raw_max_workers) -> int:
    """policy-set-parallel：用户身份写入并发授权并经 save_state 落盘。"""
    max_workers = _parse_int(
        "policy-set-parallel", "max_workers", raw_max_workers)
    st = _require_task(repo_root, task_id)
    updated = execution_policy.set_parallel_authorization(
        _base_policy(st), max_workers=max_workers, source="user",
        confirmed_at=_now_iso8601())
    st["execution_policy"] = updated
    path = state.save_state(repo_root, st)
    _emit({"task_id": task_id, "policy_source": "state",
           "saved": str(path), "execution_policy": updated})
    return 0


def _policy_set_resume(repo_root, task_id, raw_auto_resume,
                       raw_windows=None) -> int:
    """policy-set-resume：用户身份写入续跑授权并经 save_state 落盘。"""
    func_name = "policy-set-resume"
    auto_resume = raw_auto_resume
    if auto_resume not in execution_policy.AUTO_RESUME_MODES:
        raise ValueError(
            "%s：auto_resume %r 不在合法取值内（%s）"
            % (func_name, auto_resume,
               ", ".join(execution_policy.AUTO_RESUME_MODES)))
    if raw_windows is None:
        max_quota_windows = AUTO_RESUME_DEFAULT_WINDOWS[auto_resume]
    else:
        max_quota_windows = _parse_int(
            func_name, "max_quota_windows", raw_windows)
    st = _require_task(repo_root, task_id)
    updated = execution_policy.set_resume_authorization(
        _base_policy(st), auto_resume=auto_resume,
        max_quota_windows=max_quota_windows, source="user",
        confirmed_at=_now_iso8601())
    st["execution_policy"] = updated
    path = state.save_state(repo_root, st)
    _emit({"task_id": task_id, "policy_source": "state",
           "saved": str(path), "execution_policy": updated})
    return 0


def _require_task_for_permit(repo_root, task_id) -> dict:
    """permit 子命令共用的任务存在闸：不存在 → _TaskMissing（退出码 1）；
    JSON 损坏由 load_state 抛 ValueError（退出码 2）。先于任何 permit
    I/O 检查——拼错 task_id 得到「任务不存在」而非空列表假象。"""
    st = state.load_state(repo_root, task_id)
    if st is None:
        raise _TaskMissing(
            "任务 %s 不存在（%s 下无 state.json），无法操作 dispatch "
            "permit" % (task_id, repo_root))
    return st


def _permits(repo_root, task_id) -> int:
    """permits：列出任务全部活跃 permit（list_permits 排序确定性）。"""
    _require_task_for_permit(repo_root, task_id)
    _emit({"task_id": task_id,
           "permits": dispatch_wave.list_permits(repo_root, task_id)})
    return 0


def _permit_show(repo_root, task_id, permit_id) -> int:
    """permit-show：展示单张活跃 permit（原样 dict）。"""
    _require_task_for_permit(repo_root, task_id)
    permit = dispatch_wave.load_permit(repo_root, task_id, permit_id)
    if permit is None:
        raise _PermitMissing(
            "permit %s 不存在（或已消费 / 已失效），无法展示" % (permit_id,))
    _emit(permit)
    return 0


def _permit_consume(repo_root, task_id, permit_id) -> int:
    """permit-consume：消费 permit（原子 rename 防重放）。"""
    _require_task_for_permit(repo_root, task_id)
    if not dispatch_wave.consume_permit(repo_root, task_id, permit_id):
        raise _PermitMissing(
            "permit %s 不存在（或已消费 / 已失效），无可消费的活跃 "
            "permit" % (permit_id,))
    _emit({"task_id": task_id, "permit_id": permit_id, "consumed": True})
    return 0


def _agent_runs(repo_root, task_id, unit=None) -> int:
    """agent-runs：agent run 账本只读查询（薄壳，零写副作用）。

    unit 缺省 → 输出 list_agent_runs 数组（journal 聚合，空账本即空
    数组——任务不存在不是错误，journal 是账本唯一真相源）；unit 给定
    → 输出 run_lifecycle 汇总 dict（含 §7.3 僵尸语义二标注）。
    """
    if unit is None:
        _emit(agent_run.list_agent_runs(repo_root, task_id))
    else:
        _emit(agent_run.run_lifecycle(repo_root, task_id, unit))
    return 0


# —— v2.1 M4（wu-21-08）：dispatch wave 批量事务与只读查询 ——

def _wave_prepare(repo_root, task_id, raw_quota_status=None,
                  raw_max_workers=None) -> int:
    """wave-prepare：批量派发准备（task_manager.prepare_dispatch_wave
    薄壳）。参数值非法 → ValueError（退出码 2）；任务缺失 / 决策未
    批准（TaskManagerError）→ _WaveRejected（退出码 1）；成功输出
    wave_id / units / worker_budget / permits / markers / deferred /
    waiting_quota——markers 为 GLM_CONDUCTOR_DISPATCH=<permit_id>
    文本，操作者可直接放进 Agent prompt。

    quota_status 缺省 None → 透传 API（wu-21-10：None 走 resolver
    四级层级并记 quota_resolved 事件，绝不默认 AVAILABLE）；显式
    四态直通（CLI 侧只做词汇闸，非法 → 退出码 2）。"""
    from runtime import task_manager
    quota_status = raw_quota_status
    if quota_status is not None and quota_status not in QUOTA_STATUSES:
        raise ValueError(
            "wave-prepare：quota_status %r 不在合法取值内（%s）"
            % (quota_status, ", ".join(QUOTA_STATUSES)))
    max_workers = None
    if raw_max_workers is not None:
        max_workers = _parse_int("wave-prepare", "max_workers",
                                 raw_max_workers)
    try:
        result = task_manager.prepare_dispatch_wave(
            repo_root, task_id, quota_status=quota_status,
            max_workers=max_workers)
    except task_manager.TaskManagerError as exc:
        raise _WaveRejected(str(exc)) from exc
    _emit({
        "task_id": task_id,
        "wave_id": result["wave_id"],
        "units": result["units"],
        "worker_budget": result["worker_budget"],
        "permits": result["permits"],
        "markers": [dispatch_wave.marker_for(permit["permit_id"])
                    for permit in result["permits"]],
        "deferred": result["deferred"],
        "waiting_quota": result["waiting_quota"],
    })
    return 0


def _waves_of(task_state) -> list:
    """容错读取 state dict 的 dispatch.waves（缺失/形状异常按空处理）。"""
    dispatch_block = (task_state.get("dispatch")
                      if isinstance(task_state, dict) else None)
    waves = (dispatch_block.get("waves")
             if isinstance(dispatch_block, dict) else None)
    return waves if isinstance(waves, list) else []


def _wave_show(repo_root, task_id, wave_id=None) -> int:
    """wave-show：wave 记录只读查询。缺省 wave_id → 全部 waves 数组；
    给定 wave_id → 该 wave 记录原样 dict（照 permit-show 直出风格）；
    wave 不存在 → _WaveMissing（退出码 1）。"""
    st = _require_task_for_permit(repo_root, task_id)
    waves = _waves_of(st)
    if wave_id is None:
        _emit({"task_id": task_id, "waves": waves})
        return 0
    for entry in waves:
        if isinstance(entry, dict) and entry.get("wave_id") == wave_id:
            _emit(entry)
            return 0
    raise _WaveMissing(
        "wave %s 不存在（任务 %s 无该 wave 记录）" % (wave_id, task_id))


# —— v2.1 M6（wu-21-12/13）：溯源凭证与审查 receipt ——

def _verify_unit(repo_root, task_id, uid, command=None) -> int:
    """verify-unit：受限执行单元验证命令并产出溯源凭证（provenance.
    verify_unit 薄壳）。任务/单元缺失、白名单或 policy 闸拒绝
    （ProvenanceError）→ _VerifyRejected（退出码 1）；exit_code 非 0
    或超时 → all_passed=False 且退出码 1（证据没过就是失败口径）。"""
    from runtime import provenance
    try:
        result = provenance.verify_unit(repo_root, task_id, uid,
                                        command=command)
    except provenance.ProvenanceError as exc:
        raise _VerifyRejected(str(exc)) from exc
    _emit(result)
    return 0 if result["all_passed"] else 1


def _verify_task(repo_root, task_id, command=None) -> int:
    """verify-task：任务级验证命令溯源（provenance.verify_task 薄壳，
    与 _verify_unit 同构：白名单 = state.verification.required）。"""
    from runtime import provenance
    try:
        result = provenance.verify_task(repo_root, task_id, command=command)
    except provenance.ProvenanceError as exc:
        raise _VerifyRejected(str(exc)) from exc
    _emit(result)
    return 0 if result["all_passed"] else 1


def _review_record(repo_root, task_id, reviewer, verdict, tool_use_id,
                   raw_route=None, raw_note=None) -> int:
    """review-record：申报已发生的审查并落 durable receipt（provenance.
    run_review 薄壳）。verdict / route 词汇在 CLI 侧先闸（参数值非法
    → 退出码 2，与 policy-set-resume 的 auto_resume 闸同口径）；任务
    缺失或调用真实性回验被拒（RB-21-02：reviewer 白名单 / journal 内
    tool_use_id 绑定的 reviewer_invoked 事件缺失 / reviewer-task 不
    匹配 / replay 矛盾——均为 ProvenanceError）→ _VerifyRejected
    （退出码 1）。note 缺省 None 原样透传。"""
    from runtime import provenance
    func_name = "review-record"
    if verdict not in state.REVIEW_VERDICTS:
        raise ValueError(
            "%s：verdict %r 不在 state.REVIEW_VERDICTS 内（%s）"
            % (func_name, verdict, ", ".join(state.REVIEW_VERDICTS)))
    route = None
    if raw_route is not None:
        if raw_route not in provenance.REVIEW_ROUTES:
            raise ValueError(
                "%s：route %r 不在合法取值内（%s）"
                % (func_name, raw_route,
                   ", ".join(str(item) for item in provenance.REVIEW_ROUTES
                             if item is not None)))
        route = raw_route
    try:
        receipt = provenance.run_review(
            repo_root, task_id, reviewer=reviewer, verdict=verdict,
            tool_use_id=tool_use_id, route=route, note=raw_note)
    except provenance.ProvenanceError as exc:
        raise _VerifyRejected(str(exc)) from exc
    _emit(receipt)
    return 0


# —— v2.1 M5（wu-21-10 / §14）：quota 连续性 ——

def _quota_resolve(repo_root, force_refresh=False) -> int:
    """quota-resolve：额度四态四级层级解析（quota.resolver.
    resolve_quota_status 薄壳，输出键冻结 dict 原样直出）。观测面
    零转态；--force-refresh 跳过新鲜缓存层强制走 provider。"""
    from runtime.quota import resolver  # 函数内 import：monkeypatch 友好
    resolved = resolver.resolve_quota_status(repo_root,
                                             force_refresh=force_refresh)
    _emit(resolved)
    return 0


def _quota_task_inputs(repo_root, task_id) -> dict:
    """quota-observe / quota-phase 共用的 task 侧输入装配（全部只读）。

    task_id 为 None（quota-observe 无任务形态）→ 保守默认：
    task_active=True、wake_bridge_status="none"、quota_control=None
    （决策层全默认阈值）、max_workers=None（决策层按 1 保守）。

    给定 task_id → state.json 只读装配：任务缺失 → _TaskMissing
    （退出码 1）；JSON 损坏由 load_state 抛 ValueError（退出码 2）；
    continuation 缺块按 default_continuation 兜底（obligation 消费
    口径，§23.1 legacy 兼容）；parallelism.max_workers 形状非法 →
    None（决策层按 1 保守，观测面不被半块 state 炸掉）。task_active
    取任务是否在执行态族（task_manager.QUOTA_WAIT_TASK_STATUSES：
    executing / joining / verifying / reviewing）。
    """
    from runtime import task_manager
    if task_id is None:
        return {"task_active": True, "wake_bridge_status": "none",
                "quota_control": None, "max_workers": None}
    st = state.load_state(repo_root, task_id)
    if st is None:
        raise _TaskMissing(
            "任务 %s 不存在（%s 下无 state.json），无法观测额度相位"
            % (task_id, repo_root))
    policy = st.get("execution_policy")
    quota_control = execution_policy.default_quota_control(policy)
    parallelism = (policy.get("parallelism")
                   if isinstance(policy, dict) else None)
    workers = (parallelism.get("max_workers")
               if isinstance(parallelism, dict) else None)
    if isinstance(workers, bool) or not isinstance(workers, int) \
            or workers < 1:
        workers = None  # 形状非法 → 决策层按 1 保守（观测面 fail-open）
    continuation = st.get("continuation")
    bridge = (continuation.get("wake_bridge")
              if isinstance(continuation, dict) else None)
    wake_bridge_status = (bridge.get("status")
                          if isinstance(bridge, dict)
                          and isinstance(bridge.get("status"), str)
                          and bridge.get("status") != ""
                          else "none")  # 缺块 / 空串按默认块兜底
    return {
        "task_active": (st.get("status")
                        in task_manager.QUOTA_WAIT_TASK_STATUSES),
        "wake_bridge_status": wake_bridge_status,
        "quota_control": quota_control,
        "max_workers": workers,
    }


def _snapshot_windows(detail) -> "list | None":
    """从 resolve_quota_detail 的明细 dict 容错取 §27 windows
    （snapshot 缺失 / 形状异常 → None，决策层按空窗口 fail-open）。"""
    snapshot = detail.get("snapshot") if isinstance(detail, dict) else None
    windows = (snapshot.get("windows")
               if isinstance(snapshot, dict) else None)
    return windows if isinstance(windows, list) else None


def _quota_observe(repo_root, task_id=None) -> int:
    """quota-observe：§16.1 观测 dict（v2.2 M3 wu-22-03，observer.
    observe 薄壳）。I/O 全在本层：resolve_quota_detail 读 quota-cache
    （四级层级，缓存过期即走 provider——§6.4 lazy heartbeat 的「进入
    runtime 先判是否刷新」入口）+ state.json 只读装配 task 侧输入 →
    纯决策 → 单行 JSON（ensure_ascii=False）。零 state 写、零
    automation、零 journal 事件；now 由本层注入当前 UTC（observer
    层自身无墙钟依赖）。"""
    from runtime.quota import observer, resolver  # 函数内 import：monkeypatch 友好
    inputs = _quota_task_inputs(repo_root, task_id)
    detail = resolver.resolve_quota_detail(repo_root)
    result = observer.observe(
        provider_status=detail["status"],
        windows=_snapshot_windows(detail),
        quota_control=inputs["quota_control"],
        max_workers=inputs["max_workers"],
        task_active=inputs["task_active"],
        wake_bridge_status=inputs["wake_bridge_status"],
        now=datetime.datetime.now(datetime.timezone.utc))
    _emit_utf8(result)
    return 0


def _quota_phase(repo_root, task_id) -> int:
    """quota-phase：执行相决策 JSON 原样直出（v2.2 M3 wu-22-03，
    control.evaluate_task_quota_phase 薄壳）。与 quota-observe 同源
    输入（resolve_quota_detail + state.json 只读），差异只在输出层
    （§17.1 决策 dict vs §16.1 观测 dict）——观测编排归 observer，
    执行相映射归 control，本层只做 I/O。"""
    from runtime.quota import control, resolver  # 函数内 import：monkeypatch 友好
    inputs = _quota_task_inputs(repo_root, task_id)
    detail = resolver.resolve_quota_detail(repo_root)
    decision = control.evaluate_task_quota_phase(
        provider_status=detail["status"],
        windows=_snapshot_windows(detail),
        quota_control=inputs["quota_control"],
        max_workers=inputs["max_workers"],
        task_active=inputs["task_active"],
        wake_bridge_status=inputs["wake_bridge_status"])
    _emit_utf8(decision)
    return 0


def _quota_exhausted(repo_root, task_id) -> int:
    """quota-exhausted：EXHAUSTED 转态链 + 授权矩阵裁决（task_manager.
    handle_quota_exhausted 薄壳；evaluation 不经 CLI 传——None 不虚构
    recommended_resume_at）。任务缺失 / 状态族不符（TaskManagerError）
    → _QuotaFlowRejected（退出码 1）。"""
    from runtime import task_manager
    try:
        result = task_manager.handle_quota_exhausted(repo_root, task_id)
    except task_manager.TaskManagerError as exc:
        raise _QuotaFlowRejected(str(exc)) from exc
    _emit(result)
    return 0


def _quota_resume(repo_root, task_id, raw_status=None) -> int:
    """quota-resume：额度唤醒 / SessionStart 的恢复首步（task_manager.
    resume_from_quota 薄壳）。status 缺省 None → API 内经 resolver 四级
    层级解析；显式四态直通（CLI 侧词汇闸，非法 → 退出码 2）。EXHAUSTED
    / UNKNOWN 保守等待（resumed=false）是合法结果——退出码 0；任务
    缺失（TaskManagerError）→ _QuotaFlowRejected（退出码 1）。
    v2.2 C5b：API 返回 dict 原样直出（零加工）——已注册且启用订阅的
    任务另含 additive 键 "subscription"（资格面），legacy 任务输出零
    变化；既有键与退出码契约不动。
    v2.2 C7：订阅路径转态恢复另含 additive 键 "consumption"（消费记账
    面，含 reason / error 降级形态）；恢复链 OSError（state 落盘 /
    journal / mark / consumption 证据写失败）→ _QuotaFlowDegraded
    （退出码 3 + {error, guidance} JSON 面——durable 转态可能已落、
    幂等重跑安全，QC-07 证据丢失必须可见）。"""
    from runtime import task_manager
    if raw_status is not None and raw_status not in QUOTA_STATUSES:
        raise ValueError(
            "quota-resume：status %r 不在合法取值内（%s）"
            % (raw_status, ", ".join(QUOTA_STATUSES)))
    try:
        result = task_manager.resume_from_quota(repo_root, task_id,
                                                status=raw_status)
    except task_manager.TaskManagerError as exc:
        raise _QuotaFlowRejected(str(exc)) from exc
    except OSError as exc:  # C7：durable-but-degraded → 退出码 3
        raise _QuotaFlowDegraded(str(exc)) from exc
    _emit(result)
    return 0


# —— v2.2 修正计划 C3（wu-22-C3）：Real-Time Quota Watcher ——

def _quota_watcher_start(repo_root) -> int:
    """quota-watcher start：以 sys.executable 分离派生 serve 子进程
    （§6.2 工程约束：subprocess 一律 sys.executable；用户文档仍写
    python3）。子进程运行本 CLI 的内部隐藏形态
    `quota-watcher <repo_root> --serve`，stdout/stderr 落
    .glm-conductor/quota/watcher.log；派生成功即返回 pid（子进程内的
    单实例锁冲突只落日志——start 是 fire-and-forget，状态由 status
    观测）。Windows 用 DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP，
    POSIX 用 start_new_session；不引入 Windows service / autostart
    （§6.1 冻结不做项）。"""
    import os
    import subprocess
    from runtime.quota import watcher_store
    cli_path = str(pathlib.Path(__file__).resolve())
    log_path = watcher_store.watcher_log_path(repo_root)
    state_path = watcher_store.watcher_state_path(repo_root)
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    argv = [sys.executable, cli_path, "quota-watcher", str(repo_root),
            "--serve"]
    with open(log_path, "ab") as log_handle:
        kwargs = {}
        if os.name == "nt":
            kwargs["creationflags"] = (subprocess.DETACHED_PROCESS
                                       | subprocess.CREATE_NEW_PROCESS_GROUP)
        else:
            kwargs["start_new_session"] = True
        process = subprocess.Popen(
            argv, stdin=subprocess.DEVNULL, stdout=log_handle,
            stderr=log_handle, close_fds=True, **kwargs)
    _emit({"started": True, "pid": process.pid, "log": log_path,
           "state": state_path,
           "serve": "quota-watcher <repo_root> --serve（内部隐藏形态，"
                    "由 start 派生；单实例锁冲突详情落 watcher.log）"})
    return 0


def _quota_watcher_status(repo_root) -> int:
    """quota-watcher status：读 watcher.json 输出摘要（零写副作用；
    无记录不是错误——active=false，退出码 0）。staleness =
    heartbeat_age_seconds 相对 watcher_store 默认新鲜阈值（180 秒）
    的新鲜性二标注。"""
    from runtime.quota import watcher_store
    record = watcher_store.read_watcher_state(repo_root)
    state_path = watcher_store.watcher_state_path(repo_root)
    if record is None:
        _emit_utf8({"active": False, "record": None, "state": state_path})
        return 0
    stale_seconds = watcher_store.DEFAULT_HEARTBEAT_STALE_SECONDS
    age = watcher_store.heartbeat_age_seconds(record)
    _emit_utf8({
        "active": record.get("pid") is not None,
        "mode": record.get("mode"),
        "pid": record.get("pid"),
        "generation": record.get("generation"),
        "provider_identity_hash": record.get("provider_identity_hash"),
        "started_at": record.get("started_at"),
        "heartbeat_at": record.get("heartbeat_at"),
        "heartbeat_age_seconds": age,
        "heartbeat_stale": (None if age is None
                            else age > stale_seconds),
        "stop_requested": record.get("stop_requested"),
        "next_poll_at": record.get("next_poll_at"),
        "last_observation": record.get("last_observation"),
        "state": state_path,
    })
    return 0


def _quota_watcher_stop(repo_root) -> int:
    """quota-watcher stop：置 stop_requested 旗标（原子写回）；单次
    操作，绝不轮询等待退出（消费由 watcher 循环在下一 wake 完成）。
    无记录 → stop_requested=false 幂等成功（退出码 0，非错误）。"""
    from runtime.quota import watcher_store
    record = watcher_store.request_stop(repo_root)
    if record is None:
        _emit_utf8({"stop_requested": False,
                    "reason": "无 watcher 状态记录（watcher.json 不存在），"
                              "无可停止对象",
                    "state": watcher_store.watcher_state_path(repo_root)})
        return 0
    _emit_utf8({"stop_requested": True, "pid": record.get("pid"),
                "generation": record.get("generation"),
                "note": "旗标已原子置位；watcher 将在下一 wake 优雅退出"
                        "（本命令不等待）",
                "state": watcher_store.watcher_state_path(repo_root)})
    return 0


def _quota_watcher_once(repo_root) -> int:
    """quota-watcher once：前台单次抓取（watcher.run_once 薄壳，测试/
    诊断用）。锁被活进程新鲜持有（§6）→ _QuotaWatcherConflict（退出
    码 1）；否则输出一次观察摘要（epoch_id / probe_boundary_at 来自
    epoch.evaluate_epoch 对 §27 windows 的折算）。"""
    from runtime.quota import watcher, watcher_store  # 函数内 import：monkeypatch 友好
    result = watcher.run_once(repo_root)
    if not result["ran"]:
        raise _QuotaWatcherConflict(
            "quota-watcher once：单实例锁被活进程持有（pid=%r，"
            "heartbeat_at=%r 仍新鲜），未执行抓取" % (
                (result["conflict"] or {}).get("pid"),
                (result["conflict"] or {}).get("heartbeat_at")))
    record = result["record"] or {}
    observation = record.get("last_observation") or {}
    _emit_utf8({"ran": True, "mode": record.get("mode"),
                "generation": record.get("generation"),
                "status": observation.get("status"),
                "source": observation.get("source"),
                "epoch_id": observation.get("epoch_id"),
                "probe_boundary_at": observation.get("probe_boundary_at"),
                "executable": observation.get("executable"),
                "observed_at": observation.get("observed_at"),
                "error": observation.get("error"),
                "state": watcher_store.watcher_state_path(repo_root)})
    return 0


def _quota_watcher_serve(repo_root) -> int:
    """quota-watcher <repo_root> --serve：内部隐藏形态，仅由 start 派生
    使用（文档注明；不由操作者直接调用）。acquire 冲突 → 退出码 1
    （详情落 stderr → watcher.log）；graceful stop → 退出码 0。"""
    from runtime.quota import watcher
    return watcher.serve(repo_root)


def _manifest_show(repo_root, task_id) -> int:
    """manifest-show：Resume Manifest 只读查询（薄壳）。

    输出 {"manifest": <dict|null>}——manifest 缺失 / 损坏均输出 null
    （read 侧永不抛）；任务是否存在不设闸（manifest 是派生物，缺失
    即无快照，不是错误）。
    """
    from runtime import resume_manifest
    _emit({"manifest": resume_manifest.read_resume_manifest(
        repo_root, task_id)})
    return 0


# —— v2.3.0（v23-w2b）：Global Quota Clock ——

# —— v2.3.0（v23-w3）：Task Wake Bridge fire 后锚点偏移常量 ——
# wake-retime 与 quota-clock 全部处理函数/常量已迁 runtime/commands/
# （v2.3.1 W3，unit w3-quota-clock-split / w3-wake-split；W4 测试收敛
# 将 WAKE_RETIME_* 常量锚点改指 commands.wake 后，cli.py 内的常量与
# _clock_now_ms 副本随之删除——依赖方向恒为 cli → commands）。
# 偏移常量语义（park=365 天 / retry=5 分钟）见 commands/wake.py 冻结注释。


def _dispatch(args) -> int:
    """argv 分发；子命令 / 参数个数错误抛 _UsageError（退出码 2）。"""
    if not args:
        raise _UsageError("缺少子命令。" + USAGE)
    cmd, rest = args[0], args[1:]
    if cmd == "policy-show":
        if len(rest) != 2:
            raise _UsageError(
                "policy-show 需要 <repo_root> <task_id> 两个参数。" + USAGE)
        return _policy_show(rest[0], rest[1])
    if cmd == "policy-set-parallel":
        if len(rest) != 3:
            raise _UsageError(
                "policy-set-parallel 需要 <repo_root> <task_id> "
                "<max_workers> 三个参数。" + USAGE)
        return _policy_set_parallel(rest[0], rest[1], rest[2])
    if cmd == "policy-set-resume":
        if len(rest) not in (3, 4):
            raise _UsageError(
                "policy-set-resume 需要 <repo_root> <task_id> "
                "<auto_resume> [max_quota_windows] 三或四个参数。" + USAGE)
        return _policy_set_resume(
            rest[0], rest[1], rest[2], rest[3] if len(rest) == 4 else None)
    if cmd == "permits":
        if len(rest) != 2:
            raise _UsageError(
                "permits 需要 <repo_root> <task_id> 两个参数。" + USAGE)
        return _permits(rest[0], rest[1])
    if cmd == "permit-show":
        if len(rest) != 3:
            raise _UsageError(
                "permit-show 需要 <repo_root> <task_id> <permit_id> "
                "三个参数。" + USAGE)
        return _permit_show(rest[0], rest[1], rest[2])
    if cmd == "permit-consume":
        if len(rest) != 3:
            raise _UsageError(
                "permit-consume 需要 <repo_root> <task_id> <permit_id> "
                "三个参数。" + USAGE)
        return _permit_consume(rest[0], rest[1], rest[2])
    if cmd == "agent-runs":
        if len(rest) not in (2, 3):
            raise _UsageError(
                "agent-runs 需要 <repo_root> <task_id> [unit] 两或三个"
                "参数。" + USAGE)
        return _agent_runs(
            rest[0], rest[1], rest[2] if len(rest) == 3 else None)
    if cmd == "wave-prepare":
        if len(rest) not in (2, 3, 4):
            raise _UsageError(
                "wave-prepare 需要 <repo_root> <task_id> [quota_status] "
                "[max_workers] 两到四个参数。" + USAGE)
        return _wave_prepare(
            rest[0], rest[1],
            rest[2] if len(rest) >= 3 else None,
            rest[3] if len(rest) >= 4 else None)
    if cmd == "wave-show":
        if len(rest) not in (2, 3):
            raise _UsageError(
                "wave-show 需要 <repo_root> <task_id> [wave_id] 两或三个"
                "参数。" + USAGE)
        return _wave_show(
            rest[0], rest[1], rest[2] if len(rest) == 3 else None)
    if cmd == "manifest-show":
        if len(rest) != 2:
            raise _UsageError(
                "manifest-show 需要 <repo_root> <task_id> 两个参数。"
                + USAGE)
        return _manifest_show(rest[0], rest[1])
    if cmd == "quota-resolve":
        if len(rest) not in (1, 2):
            raise _UsageError(
                "quota-resolve 需要 <repo_root> [--force-refresh] 一或"
                "两个参数。" + USAGE)
        force_refresh = False
        if len(rest) == 2:
            if rest[1] != "--force-refresh":
                raise _UsageError(
                    "quota-resolve 的可选参数只接受 --force-refresh。"
                    + USAGE)
            force_refresh = True
        return _quota_resolve(rest[0], force_refresh=force_refresh)
    if cmd == "quota-observe":
        if len(rest) not in (1, 2):
            raise _UsageError(
                "quota-observe 需要 <repo_root> [task_id] 一或两个参数。"
                + USAGE)
        return _quota_observe(rest[0], rest[1] if len(rest) == 2 else None)
    if cmd == "quota-phase":
        if len(rest) != 2:
            raise _UsageError(
                "quota-phase 需要 <repo_root> <task_id> 两个参数。" + USAGE)
        return _quota_phase(rest[0], rest[1])
    if cmd == "verify-unit":
        if len(rest) not in (3, 4):
            raise _UsageError(
                "verify-unit 需要 <repo_root> <task_id> <uid> [command] "
                "三或四个参数。" + USAGE)
        return _verify_unit(
            rest[0], rest[1], rest[2], rest[3] if len(rest) == 4 else None)
    if cmd == "verify-task":
        if len(rest) not in (2, 3):
            raise _UsageError(
                "verify-task 需要 <repo_root> <task_id> [command] 两或三"
                "个参数。" + USAGE)
        return _verify_task(
            rest[0], rest[1], rest[2] if len(rest) == 3 else None)
    if cmd == "review-record":
        if len(rest) not in (5, 6, 7):
            raise _UsageError(
                "review-record 需要 <repo_root> <task_id> <reviewer> "
                "<verdict> <tool_use_id> [route] [note] 五到七个参数。"
                + USAGE)
        return _review_record(
            rest[0], rest[1], rest[2], rest[3], rest[4],
            rest[5] if len(rest) >= 6 else None,
            rest[6] if len(rest) == 7 else None)
    if cmd == "quota-exhausted":
        if len(rest) != 2:
            raise _UsageError(
                "quota-exhausted 需要 <repo_root> <task_id> 两个参数。"
                + USAGE)
        return _quota_exhausted(rest[0], rest[1])
    if cmd == "quota-resume":
        if len(rest) not in (2, 3):
            raise _UsageError(
                "quota-resume 需要 <repo_root> <task_id> [status] 两或三"
                "个参数。" + USAGE)
        return _quota_resume(
            rest[0], rest[1], rest[2] if len(rest) == 3 else None)
    if cmd == "wake-record":
        if len(rest) not in (4, 6):
            raise _UsageError(
                "wake-record 需要 <repo_root> <task_id> <automation_id> "
                "<fires_at> [--db <path>] 四或六个参数。" + USAGE)
        db_path = None
        if len(rest) == 6:
            if rest[4] != "--db":
                raise _UsageError(
                    "wake-record 的可选参数只接受 --db <path>。" + USAGE)
            db_path = rest[5]
        from runtime.commands import wake  # 函数内 import：monkeypatch 友好
        return wake._wake_record(rest[0], rest[1], rest[2], rest[3],
                                 db_path=db_path)
    if cmd == "wake-arm":
        if len(rest) not in (3, 5):
            raise _UsageError(
                "wake-arm 需要 <repo_root> <task_id> <automation_id> "
                "[--db <path>] 三或五个参数。" + USAGE)
        db_path = None
        if len(rest) == 5:
            if rest[3] != "--db":
                raise _UsageError(
                    "wake-arm 的可选参数只接受 --db <path>。" + USAGE)
            db_path = rest[4]
        from runtime.commands import wake  # 函数内 import：monkeypatch 友好
        return wake._wake_arm(rest[0], rest[1], rest[2], db_path=db_path)
    if cmd == "wake-prompt":
        if len(rest) != 2:
            raise _UsageError(
                "wake-prompt 需要 <repo_root> <task_id> 两个参数。" + USAGE)
        from runtime.commands import wake  # 函数内 import：monkeypatch 友好
        return wake._wake_prompt(rest[0], rest[1])
    if cmd == "wake-plan":
        if len(rest) != 2:
            raise _UsageError(
                "wake-plan 需要 <repo_root> <task_id> 两个参数。" + USAGE)
        from runtime.commands import wake  # 函数内 import：monkeypatch 友好
        return wake._wake_plan(rest[0], rest[1])
    if cmd == "wake-status":
        if len(rest) != 2:
            raise _UsageError(
                "wake-status 需要 <repo_root> <task_id> 两个参数。" + USAGE)
        from runtime.commands import wake  # 函数内 import：monkeypatch 友好
        return wake._wake_status(rest[0], rest[1])
    if cmd == "wake-retime":
        if len(rest) != 2:
            raise _UsageError(
                "wake-retime 需要 <repo_root> <task_id> 两个参数。" + USAGE)
        from runtime.commands import wake  # 函数内 import：monkeypatch 友好
        return wake._wake_retime(rest[0], rest[1])
    if cmd == "wake-reconcile":
        if len(rest) not in (3, 4):
            raise _UsageError(
                "wake-reconcile 需要 <repo_root> <task_id> <host_status> "
                "[observed_at] 三或四个参数。" + USAGE)
        from runtime.commands import wake  # 函数内 import：monkeypatch 友好
        return wake._wake_reconcile(
            rest[0], rest[1], rest[2],
            rest[3] if len(rest) == 4 else None)
    if cmd == "transport-status":
        if len(rest) != 2:
            raise _UsageError(
                "transport-status 需要 <repo_root> <task_id> 两个参数。"
                + USAGE)
        from runtime.commands import wake  # 函数内 import：monkeypatch 友好
        return wake._transport_status(rest[0], rest[1])
    if cmd == "quota-watcher":
        if len(rest) != 2:
            raise _UsageError(
                "quota-watcher 需要 <repo_root> start|status|stop|once"
                " 两个参数（--serve 为 start 派生的内部隐藏形态）。"
                + USAGE)
        action = rest[1]
        if action == "start":
            return _quota_watcher_start(rest[0])
        if action == "status":
            return _quota_watcher_status(rest[0])
        if action == "stop":
            return _quota_watcher_stop(rest[0])
        if action == "once":
            return _quota_watcher_once(rest[0])
        if action == "--serve":  # 内部隐藏形态：仅由 start 派生使用
            return _quota_watcher_serve(rest[0])
        raise _UsageError(
            "quota-watcher 的动作只接受 start / status / stop / once"
            "（--serve 为内部隐藏形态），得到 %r。" % action + USAGE)
    if cmd == "quota-clock-plan":
        if len(rest) != 1:
            raise _UsageError(
                "quota-clock-plan 需要 <repo_root> 一个参数。" + USAGE)
        from runtime.commands import quota_clock  # 函数内 import：monkeypatch 友好
        return quota_clock._quota_clock_plan(rest[0])
    if cmd == "quota-clock-bind":
        if len(rest) not in (2, 4):
            raise _UsageError(
                "quota-clock-bind 需要 <repo_root> <automation_id> "
                "[--db <path>] 两或四个参数。" + USAGE)
        db_path = None
        if len(rest) == 4:
            if rest[2] != "--db":
                raise _UsageError(
                    "quota-clock-bind 的可选参数只接受 --db <path>。"
                    + USAGE)
            db_path = rest[3]
        from runtime.commands import quota_clock  # 函数内 import：monkeypatch 友好
        return quota_clock._quota_clock_bind(rest[0], rest[1],
                                             db_path=db_path)
    if cmd == "quota-clock-tick":
        if len(rest) != 1:
            raise _UsageError(
                "quota-clock-tick 需要 <repo_root> 一个参数。" + USAGE)
        from runtime.commands import quota_clock  # 函数内 import：monkeypatch 友好
        return quota_clock._quota_clock_tick(rest[0])
    if cmd == "quota-clock-status":
        if len(rest) != 1:
            raise _UsageError(
                "quota-clock-status 需要 <repo_root> 一个参数。" + USAGE)
        from runtime.commands import quota_clock  # 函数内 import：monkeypatch 友好
        return quota_clock._quota_clock_status(rest[0])
    if cmd == "host-check":
        if len(rest) not in (0, 2):
            raise _UsageError(
                "host-check 无位置参数，可选 [--db <path>]。" + USAGE)
        db_path = None
        if len(rest) == 2:
            if rest[0] != "--db":
                raise _UsageError(
                    "host-check 的可选参数只接受 --db <path>。" + USAGE)
            db_path = rest[1]
        from runtime.commands import host  # 函数内 import：monkeypatch 友好
        return host.host_check(db_path=db_path)
    raise _UsageError("未知子命令 %r。" % cmd + USAGE)


def main(argv=None) -> int:
    """CLI 入口：返回退出码（0 成功 / 2 校验拒绝 / 1 异常 / 3 durable-
    but-degraded）。

    argv 缺省取 sys.argv[1:]；测试可直接传列表调用。异常映射：
    ValueError（用法 / 参数值 / setter / save_state 校验栈）→ 2；
    _TaskMissing（任务不存在）/ _PermitMissing（permit 不存在或已
    消费 / 已失效）/ _WaveMissing（wave 记录不存在）/ _WaveRejected
    （wave 准备被派发事务层拒绝）/ _QuotaFlowRejected（quota 连续性
    事务被拒）/ _VerifyRejected（溯源执行或审查申报被拒）/
    _QuotaWatcherConflict（quota-watcher 单实例锁冲突）→ 1；
    commands.wake 的 _TaskMissing / _QuotaFlowRejected 同实现副本
    （v2.3.1 W3b wake 八子命令迁移：原件为多命令组共用留在本模块，
    commands.wake 依模板不回 import 本模块，故两处同名词一并捕获，
    输出与退出码等价）→ 1；
    _QuotaFlowDegraded（quota-resume 恢复链 OSError，v2.2 C7）→ 3
    （错误 JSON 面 {error, guidance}：durable 转态可能已落盘、
    quota-resume 幂等重跑安全、检查任务 journal 核对 mark /
    consumption 记账）；其余意外异常 → 1（错误 JSON 含异常类型名，
    stdout 契约不破）。
    """
    args = list(sys.argv[1:]) if argv is None else list(argv)
    from runtime.commands import wake as _wake_commands  # 函数内 import
    try:
        return _dispatch(args)
    except ValueError as exc:  # 含 _UsageError：校验拒绝类
        _emit({"error": str(exc)})
        return 2
    except (_TaskMissing, _PermitMissing, _WaveMissing, _WaveRejected,
            _QuotaFlowRejected, _VerifyRejected,
            _QuotaWatcherConflict,
            _wake_commands._TaskMissing,        # v2.3.1 W3b：commands.
            _wake_commands._QuotaFlowRejected) as exc:  # wake 同实现副本
        _emit({"error": str(exc)})
        return 1
    except _QuotaFlowDegraded as exc:  # C7：durable-but-degraded → 3
        _emit({"error": str(exc),
               "guidance": ("durable 转态可能已落盘（任务状态机可能已推"
                            "进）；quota-resume 幂等，可安全重跑；请检查"
                            "任务 journal 核对 mark / consumption 记账是"
                            "否在案")})
        return 3
    except Exception as exc:  # 意外异常兜底：stdout 契约不破
        _emit({"error": "%s: %s" % (type(exc).__name__, exc)})
        return 1


if __name__ == "__main__":
    sys.exit(main())
