#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GLM Conductor v2 授权续跑（RESUME）域 —— v2.2.1 WU-221-C1（行为保持抽取）。

职责：
    用户授权续跑域（v2.1 §14/§22.7，wu-21-11 Authorized Quota Resume）
    的规范定义落点：恢复词汇常量（QUOTA_WAIT_TASK_STATUSES /
    QUOTA_WAIT_UNIT_STATUSES / QUOTA_RESUME_GRACE_SECONDS /
    QUOTA_RESUME_STATUSES）、调度折算与缓存回读（_recommended_resume_at
    / _evaluation_from_refreshed_cache）、授权矩阵纯决策（_quota_wake_
    decision）、v2.1 one-shot wake 的 prompt 生成与 arm-time 窗口扣减
    记账（quota_wake_prompt / record_quota_wake，LEGACY / DEPRECATED
    兼容面）、唤醒预算读数与恢复对账（_wake_budget_remaining /
    _clear_quota_interrupt_origin / _reconcile_running_unit）、C5b
    resume 面订阅资格裁决（_resume_subscription_gate）与消费段
    representative_boundary_id 派生（_consumption_boundary_id）。
    v2.2.1 WU-221-C1（行为保持抽取）自 runtime.task_manager 原文抽取
    （行为保持：同 journal 事件形状与写序、同幂等语义、同错误口径，
    函数体逐字未改，仅 import 适配）。

    事务编排体留守 runtime.task_manager：resume_from_quota /
    _resume_consumption / handle_quota_exhausted 的函数体经 task_manager
    模块名字空间解析其协作调用（测试 monkeypatch 面契约：tests 对
    task_manager.mark_activation_epoch / register_quota_subscription /
    record_quota_boundary_consumed / migrate_quota_window_accounting 的
    patch 必须被这些编排体看到）；本模块只承载上述被编排体调用的
    规划 / 对账 / 裁决 / 派生层。

依赖方向（冻结，防循环）：
    task_manager → 本模块 → continuity.subscription，绝不反向——本
    模块禁止 import runtime.task_manager。与抽取域共用、原属
    task_manager 的 TaskManagerError / _require_state / _continuity_view
    / _current_provider_identity_hash 以 runtime.quota.accounting
    （v2.2.1 WU-221-C1 规范落点）为家；_cache_identity_usable /
    _epoch_context_after_refresh / _reconcile_activation_journal /
    evaluate_subscription_eligibility 以 runtime.continuity.subscription
    为家；_bridge_boundary 以 runtime.continuity.wake_bridge 为家——
    均 import 复用同一对象（单一规范落点，不复制）；上层经 re-import
    使既有 task_manager.<名字> 解析点（cli / hooks / tests）全部解析
    到同一对象。

    仅标准库依赖；其余为 runtime 一方模块（journal / state /
    execution_policy / continuity.subscription / continuity.wake_bridge
    / quota.accounting；quota.scheduler / quota.resolver / runtime.
    reconcile 保持函数内 import——monkeypatch 友好，与抽取前一致）。
    Python 3.7 兼容语法（仓库下限）。
"""


from runtime import journal
from runtime import state
from runtime.execution_policy import consumed_quota_windows
from runtime.execution_policy import default_execution_policy
from runtime.continuity.subscription import (
    _cache_identity_usable,
    _epoch_context_after_refresh,
    _reconcile_activation_journal,
    evaluate_subscription_eligibility,
)
from runtime.continuity.wake_bridge import _bridge_boundary
from runtime.quota.accounting import (
    TaskManagerError,
    _continuity_view,
    _current_provider_identity_hash,
    _require_state,
)


# —— 用户授权续跑（v2.1 §14/§22.7，wu-21-11 Authorized Quota Resume） ——

# handle_quota_exhausted 的合法入口任务状态（执行态族；任务不在此族
# → TaskManagerError——额度转态是执行期编排决策，created 等前置态
# 不存在「因额度耗尽而挂起」的语义）
QUOTA_WAIT_TASK_STATUSES = ("executing", "joining", "verifying", "reviewing")

# 额度耗尽时随任务一并转 waiting_quota 的单元状态（§62 表内边
# ready→waiting_quota、running→waiting_quota 均合法；waiting_quota
# 原地保持——已在等待态的单元不重复转、只计入 waiting_units 清单）
QUOTA_WAIT_UNIT_STATUSES = ("ready", "running", "waiting_quota")

# recommended_resume_at 的宽限秒数（§30 口径：reset 之后再等一等，
# 防唤醒过早；与 runtime.quota.scheduler.DEFAULT_GRACE_SECONDS 同源）
QUOTA_RESUME_GRACE_SECONDS = 300

# resume_from_quota 允许恢复执行的额度四态（AVAILABLE 直恢复；
# PRESSURE 也恢复——恢复后并发预算经既有 §12 折算自动收缩到 1；
# EXHAUSTED / UNKNOWN 保守等待，UNKNOWN 不虚构可用性）
QUOTA_RESUME_STATUSES = ("AVAILABLE", "PRESSURE")


def _recommended_resume_at(evaluation):
    """由 evaluation 计算建议恢复时刻（统一走 scheduler.plan_resume）。

    RB-21-01 起本函数降级为 runtime.quota.scheduler.plan_resume 的薄
    容错 wrapper——本模块不再维护第二套 reset 数学（历史的「最早
    EXHAUSTED 窗 reset + 宽限」min 口径已删除；统一为 §30 最晚多窗
    口径：resume_at = max(全部 EXHAUSTED 窗 reset) + 宽限，多窗同时
    EXHAUSTED 时不产生 premature wake 浪费 auto_once 的单窗预算）：
      - evaluation None / 非 dict / status 缺失或非法 / 规划异常 →
        None（不抛——调用方按「未知」处理，§31 不虚构）；
      - EXHAUSTED → plan["resume_at"]（任一阻塞窗 reset 不可解析 →
        periodic_fallback，plan["resume_at"] 为 None）；
      - 非 EXHAUSTED → None（AVAILABLE / PRESSURE 不需要调度时刻）。
    """
    if not isinstance(evaluation, dict):
        return None
    try:
        from runtime.quota import scheduler  # 函数内 import：monkeypatch 友好
        plan = scheduler.plan_resume(
            evaluation, grace_seconds=QUOTA_RESUME_GRACE_SECONDS)
    except Exception:
        return None
    if not isinstance(plan, dict):
        return None
    if evaluation.get("status") != "EXHAUSTED":
        return None
    return plan.get("resume_at")


def _evaluation_from_refreshed_cache(repo_root):
    """强刷后的额度缓存 snapshot → scheduler.evaluate 的 evaluation。

    resume_from_quota 的 EXHAUSTED 分支专用：wake 已以 force_refresh=
    True 强制刷新，provider 抓取成功时 resolver 缓存（
    <repo_root>/.glm-conductor/quota-cache.json）保存完整 snapshot
    （形状 {"provider", "fetched_at", "snapshot", "status"}）——复用
    resolver 的缓存原语（_cache_path / _load_cache，零 resolver 改动）
    读回 snapshot，经 scheduler.evaluate 产出 evaluation，交
    _recommended_resume_at（plan_resume 统一口径）计算建议恢复时刻。

    缓存缺失 / snapshot 非 dict / 求值异常 → None（§31 不虚构）。
    边界（mid-batch review P3）：provider 层在 wake 时抓取失败 →
    resolver 回退陈旧缓存 status，此处读到的 snapshot 是休眠前的
    旧账——recommended_resume_at 仍只是建议值（不虚构、主会话按
    四态重解析裁决），消费方不得将其当作已验证的可用性事实。
    """
    try:
        from runtime.quota import resolver, scheduler  # 函数内 import
        cache = resolver._load_cache(resolver._cache_path(repo_root))
        if not isinstance(cache, dict):
            return None
        # v2.2.1 WU-221-B2（QuotaIdentity）直接读者身份闸：异身份缓存
        # 视同无缓存（→ None 不虚构建议时刻）；legacy 无指纹缓存保守
        # 信任，行为逐字不变。
        if not _cache_identity_usable(
                cache, _current_provider_identity_hash()):
            return None
        snapshot = cache.get("snapshot")
        if not isinstance(snapshot, dict):
            return None
        return scheduler.evaluate(snapshot)
    except Exception:
        return None


def _quota_wake_decision(view) -> dict:
    """授权矩阵纯决策（§14.2-§14.5；输入 _continuity_view 的输出）。

    返回 {"required", "budget_exhausted", "prompt_mode", "reason",
    "transition_waiting_user"}：
      - required：「授权允许自动恢复执行」——manual / notify 恒 False；
      - prompt_mode："wake"（auto_once / until_done 且已授权且有预算
        ——自足唤醒 prompt）；"reminder"（notify——提醒模板，任务实际
        执行前仍需用户动作）；None（不产出 prompt）；
      - transition_waiting_user：auto_once / until_done 且已授权且预算
        耗尽（remaining <= 0）→ True（§14.5 授权耗尽语义）；source
        非 "user" 的未授权分支不转（合法 state 里 auto_once/until_done
        恒已授权——execution_policy §5.4 不变量保证，该分支是纵深防御）；
      - reason：中文一句话裁决理由。
    纯函数：零 I/O。
    """
    auto_resume = view["auto_resume"]
    remaining = view["remaining"]
    if auto_resume == "manual":
        return {
            "required": False, "budget_exhausted": False,
            "prompt_mode": None, "transition_waiting_user": False,
            "reason": "auto_resume=manual：不创建自动化唤醒，未来 "
                      "SessionStart 恢复注入会提示用户手动续跑",
        }
    if auto_resume == "notify":
        return {
            "required": False, "budget_exhausted": False,
            "prompt_mode": "reminder", "transition_waiting_user": False,
            "reason": "auto_resume=notify：授权允许提醒、不允许自动恢复"
                      "执行——可自建一次性提醒 automation（maxRuns=1，"
                      "prompt 文本已随返回给出），任务实际执行前仍需用户"
                      "动作",
        }
    # —— auto_once / until_done（跨窗口自动续跑授权族） ——
    if view["source"] != "user":
        return {
            "required": False, "budget_exhausted": remaining <= 0,
            "prompt_mode": None, "transition_waiting_user": False,
            "reason": "auto_resume=%r 未获得用户授权（authorization."
                      "source != \"user\"），不自动恢复执行" % auto_resume,
        }
    if remaining <= 0:
        return {
            "required": False, "budget_exhausted": True,
            "prompt_mode": None, "transition_waiting_user": True,
            "reason": "auto_resume=%r 的窗口预算已耗尽（consumed %d / "
                      "max %d），不得再创建任何自动化唤醒——转 waiting_user"
                      "等待用户重新授权（§14.5）"
                      % (auto_resume, view["consumed_quota_windows"],
                         view["max_quota_windows"]),
        }
    return {
        "required": True, "budget_exhausted": False,
        "prompt_mode": "wake", "transition_waiting_user": False,
        "reason": "auto_resume=%r 已获用户授权且窗口预算剩余 %d：创建"
                  "一次性自动化唤醒（maxRuns=1）恢复执行"
                  % (auto_resume, remaining),
    }


def quota_wake_prompt(repo_root, task_id) -> str:
    """生成自足的一次性额度唤醒 prompt（v2.1 §14.4 wake 形态锁定）。

    宿主实测（2026-08-31 wake 入账本）：ZCode automation wake 是同会话
    续行——prompt 以用户 turn 注入、SessionStart 不重放，因此 prompt
    必须自足：task_id、账本根 / 任务仓库根、恢复首步（resume_from_quota
    → 按账本就绪继续）、额度检查口径、预算状态（已消耗 / 共几窗 /
    剩余）、红线（绝不重试 CronDelete/CronUpdate、发布动作征询用户、
    RB-1 完成证据门指纹口径）与一次性（maxRuns=1）语义全部内置。

    纯函数：只读 state.json（预算读 execution_policy.continuity），
    零写副作用；任务缺失 TaskManagerError。中文模板，主会话把它放进
    automation 的 prompt 字段即可（automation 创建/删除本身归主会话
    的宿主工具，runtime 只产出指令与记账）。
    """
    api = "quota_wake_prompt"
    st = _require_state(repo_root, task_id, api)
    view = _continuity_view(st)
    waiting_ids = [
        unit.get("id") for unit in st.get("work_units") or []
        if isinstance(unit, dict) and unit.get("status") == "waiting_quota"]
    work_root = state.resolve_repository_root(st, str(repo_root))
    lines = [
        "GLM CONDUCTOR 额度唤醒（一次性 automation：recurring=false、"
        "maxRuns=1，本次触发即自完成）",
        "",
        "任务 task_id：%s" % task_id,
        "账本根：%s" % repo_root,
        "任务仓库根：%s" % work_root,
        "等待恢复的单元：%s" % ("、".join(waiting_ids) if waiting_ids
                               else "无（任务级挂起）"),
        "",
        "本唤醒是一次性自动化（maxRuns=1）：触发即终结，绝不依赖 "
        "CronUpdate 修改参数或改期。",
        "",
        "第一步（必须最先执行）——额度检查与恢复：",
        "1. 解析当前额度四态（绝不重试网络；--force-refresh 强制走 "
        "provider——唤醒后不得信任休眠期间的本地缓存；provider 不可用"
        "时按陈旧缓存 → UNKNOWN 层级回退）：",
        "   python3 plugins/glm-conductor/runtime/cli.py quota-resolve "
        "--force-refresh '%s'"
        % repo_root,
        "2. 调用 runtime.task_manager.resume_from_quota(repo_root=r'%s', "
        "task_id='%s')（内部同样强制刷新额度并对中断单元做恢复对账）："
        % (repo_root, task_id),
        "   - AVAILABLE / PRESSURE → 任务转回 executing、waiting_quota "
        "单元按中断来源恢复（ready 来源直回 ready；running 来源先 "
        "reconcile 四分：干净重派回 ready、成果可复用直达 verifying、"
        "有进度回 ready 待主会话组装进度包续作、人工裁决保持等待），"
        "按账本就绪顺序经 prepare_dispatch / "
        "prepare_dispatch_wave 继续（遵守 orchestration 纪律：SELECTIVE "
        "ROUTE、permit 门与租约时序不得绕过）；",
        "   - EXHAUSTED / UNKNOWN → 零转态保守等待：不得派发、不得再建"
        "唤醒，按 recovery 摘要重排或降级 SessionStart 恢复。",
        "",
        "预算状态：已消耗 %d / 共 %d 窗（剩余 %d 窗）。本唤醒本身不消耗"
        "窗口预算（automation arm/fire/create 一律不消费，D15-g："
        "automation lifecycle ≠ quota epoch consumption）；窗口预算仅在"
        "任务成功恢复执行（resume commit point）时消耗；预算耗尽后不得"
        "再创建任何自动化唤醒，一律降级 SessionStart 恢复。"
        % (view["consumed_quota_windows"], view["max_quota_windows"],
           view["remaining"]),
        "",
        "红线（违反即事故）：",
        "- 绝不重试 CronDelete / CronUpdate（本机已知 glitch）；清理"
        "失败容忍，降级 SessionStart 恢复；",
        "- 发布类动作（git push、对外发布、删除性操作）必须先征询用户；",
        "- 单元完成必须过 RB-1 完成证据门：finish_unit 前亲自运行该"
        "单元全部 required 验证命令，并用 record_unit_verification 记录"
        "绑定当前改动的 pass 指纹证据（record 与 finish 之间不得产生 "
        "git 提交，否则证据 stale 须重验）。",
    ]
    return "\n".join(lines)


def record_quota_wake(repo_root, task_id, *, automation_id, fires_at) -> dict:
    """window 扣减记账（v2.1 legacy arm-time 记账；v2.1 §14.4：主会话
    CronCreate 成功后调用）。

    LEGACY / DEPRECATED（v2.2 C1a）：本入口仅为 v2.1 one-shot 兼容
    保留；v2.2 persistent path 禁止调用（automation arm/fire/create
    一律不消费窗口预算，D15-g：automation lifecycle ≠ quota epoch
    consumption）——消费点唯一合法位置 = resume commit point（修正
    计划 §15.1），C1b 落地新记账 API 后本入口退役。

    - 幂等（SH-21-01）：journal 已有同 automation_id 的
      quota_wake_recorded 事件 → 直接返回既有消耗结果（不递增、
      不新建事件、零写副作用），返回 dict 既有键保留并增标
      "idempotent": True——cron glitch 重放 / 误重试不再重复消耗
      窗口预算（幂等只按 automation_id 判定，不同 automation_id
      照常各消耗一窗）；
    - 写入前授权复核（SH-21-01 三查，读取口径与 _continuity_view /
      _quota_wake_decision 一致——execution_policy 块缺/坏按默认块
      解释为 manual）：auto_resume ∈ {auto_once, until_done} /
      authorization.source == "user" / 剩余窗口 > 0，任一不满足 →
      TaskManagerError（中文消息指明 violated 条件，零副作用——
      无事件、state.json 字节不变；manual/notify 任务不得经本 API
      制造 consumed window）；
    - continuity.consumed_quota_windows += 1（缺键按 0 起算；legacy
      缺 execution_policy 块时以默认块补齐后写——半定义块过不了
      validate_state 的完整性闸）；
    - journal quota_wake_recorded {automation_id, fires_at, consumed,
      remaining}；
    - 与 automation 存活解耦：wake 未触发、automation 被清理或丢失
      都不回滚（正确性底线永远是未来 SessionStart 恢复注入，automation
      只是 best-effort bridge）；无回滚 API，不同 automation_id 的
      二次调用纯递增。

    参数校验（先于任何 I/O，失败零副作用）：automation_id / fires_at
    必须是非空 str，否则 ValueError（中文消息含字段名）。返回
    {"consumed_quota_windows", "remaining_quota_windows",
    "max_quota_windows", "automation_id", "fires_at"}；幂等命中路径
    既有键保留并增标 "idempotent": True。
    """
    api = "record_quota_wake"
    if not isinstance(automation_id, str) or automation_id == "":
        raise ValueError(
            "%s：automation_id 必须是非空字符串，得到 %r"
            % (api, automation_id))
    if not isinstance(fires_at, str) or fires_at == "":
        raise ValueError(
            "%s：fires_at 必须是非空字符串（ISO8601 口径），得到 %r"
            % (api, fires_at))
    st = _require_state(repo_root, task_id, api)
    view = _continuity_view(st)
    # SH-21-01 幂等：同 automation_id 已记过账 → 原样返回既有消耗结果
    # （取首条匹配——正确流程下至多一条；历史脏数据重复时首条是原始账）
    prior = None
    for event in journal.read_events(repo_root, task_id):
        if event.get("event") == "quota_wake_recorded" \
                and event.get("automation_id") == automation_id:
            prior = event
            break
    if prior is not None:
        consumed = prior.get("consumed")
        if isinstance(consumed, bool) or not isinstance(consumed, int) \
                or consumed < 0:
            consumed = view["consumed_quota_windows"]
        remaining = prior.get("remaining")
        if isinstance(remaining, bool) or not isinstance(remaining, int) \
                or remaining < 0:
            remaining = view["remaining"]
        recorded_fires_at = prior.get("fires_at")
        if not isinstance(recorded_fires_at, str) or recorded_fires_at == "":
            recorded_fires_at = fires_at
        return {
            "consumed_quota_windows": consumed,
            "remaining_quota_windows": remaining,
            "max_quota_windows": view["max_quota_windows"],
            "automation_id": automation_id,
            "fires_at": recorded_fires_at,
            "idempotent": True,
        }
    # SH-21-01 写入前授权复核（三查逐条指明 violated 条件；在任何
    # mutation / 落盘之前——拒绝路径零副作用）
    if view["auto_resume"] not in ("auto_once", "until_done"):
        raise TaskManagerError(
            "%s：auto_resume=%r 不在自动续跑授权族（auto_once / "
            "until_done）内——manual / notify 任务不得记账消耗窗口预算"
            "（额度恢复一律走 SessionStart 恢复注入 / 用户手动续跑）"
            % (api, view["auto_resume"]))
    if view["source"] != "user":
        raise TaskManagerError(
            "%s：authorization.source=%r 非 \"user\"——跨额度窗口自动"
            "续跑必须用户明确授权，拒绝记账窗口消耗（纵深防御，与 "
            "_quota_wake_decision 口径一致）" % (api, view["source"]))
    if view["remaining"] <= 0:
        raise TaskManagerError(
            "%s：窗口预算已耗尽（consumed %d / max %d，剩余 %d）——"
            "不得再记账新的自动化唤醒，转 waiting_user 等待用户重新"
            "授权（§14.5）" % (api, view["consumed_quota_windows"],
                               view["max_quota_windows"],
                               view["remaining"]))
    policy = st.get("execution_policy")
    if not isinstance(policy, dict):
        # legacy 任务无授权事实源块：以默认块补齐（完整四子块形状，
        # 否则 validate_state 拒绝落盘）——记账语义与默认 manual/0 授权
        # 解释一致，只是把「已消耗窗口」显式落盘
        policy = default_execution_policy()
        st["execution_policy"] = policy
    continuity = policy.get("continuity")
    if not isinstance(continuity, dict):
        continuity = default_execution_policy()["continuity"]
        policy["continuity"] = continuity
    consumed = consumed_quota_windows(policy) + 1
    continuity["consumed_quota_windows"] = consumed
    state.save_state(repo_root, st)
    view = _continuity_view(st)
    journal.append_event(repo_root, task_id, {
        "event": "quota_wake_recorded", "automation_id": automation_id,
        "fires_at": fires_at, "consumed": consumed,
        "remaining": view["remaining"]})
    return {
        "consumed_quota_windows": consumed,
        "remaining_quota_windows": view["remaining"],
        "max_quota_windows": view["max_quota_windows"],
        "automation_id": automation_id,
        "fires_at": fires_at,
    }


def _wake_budget_remaining(st) -> int:
    """任务 state 的剩余唤醒窗口预算（max(0, max - consumed)，容错读）。"""
    policy = st.get("execution_policy")
    continuity = (policy.get("continuity")
                  if isinstance(policy, dict) else None)
    max_windows = (continuity.get("max_quota_windows")
                   if isinstance(continuity, dict) else None)
    if isinstance(max_windows, bool) or not isinstance(max_windows, int) \
            or max_windows < 0:
        max_windows = 0
    return max(0, max_windows - consumed_quota_windows(policy))


def _clear_quota_interrupt_origin(unit) -> None:
    """清除单元的中断来源标记（RB-21-01 恢复落点收尾）。

    runtime.quota_interrupted_from 成功恢复后即失义（下一轮额度中断
    会重新写入），清除避免陈旧标记误导后续对账；runtime dict 因此变
    空则整键删除（保持单元形状最简）。runtime 缺失 / 非 dict 时零操作。
    """
    runtime_meta = unit.get("runtime")
    if not isinstance(runtime_meta, dict):
        return
    runtime_meta.pop("quota_interrupted_from", None)
    if not runtime_meta:
        unit.pop("runtime", None)


def _reconcile_running_unit(repo_root, task_id, uid):
    """对 running 中断单元做四分对账 → (落点状态, unit_recovery 条目)。

    调 runtime.reconcile.reconcile_agent_run（纯读对账，RB-21-01：
    resume 链禁止盲目 waiting_quota→ready——worker 现场可能有残留），
    按 classification 决定落点：
      - redispatch_clean → "ready"（无执行内容无残留，全新派发）；
      - reuse_result → "verifying"（agent 成果可复用，经 §62 演进的
        waiting_quota→verifying 新边直达验证；条目带
        action_required="recover_agent_result"，主会话须先捞取成果）；
      - resume_with_progress → "ready"（有进度无完整证据；条目透传
        evidence 证据句柄，主会话组装 Previous Progress Package 随新
        规格续派——编排纪律，runtime 不机械阻止）；
      - manual_ruling（及未知分类，防御）→ "waiting_quota"（保持等待，
        禁止自动猜测；条目透传 rationale）。

    fail-closed：reconcile 异常（求值失败 / 环境问题）→ 按 manual_ruling
    处理（rationale 注明 reconcile error 与异常类型名），绝不让异常炸掉
    整个 resume。
    """
    try:
        from runtime import reconcile  # 函数内 import：monkeypatch 友好
        report = reconcile.reconcile_agent_run(repo_root, task_id, uid)
    except Exception as exc:
        return "waiting_quota", {
            "classification": "manual_ruling",
            "rationale": ["reconcile error: %s" % type(exc).__name__]}
    classification = (report.get("classification")
                      if isinstance(report, dict) else None)
    if classification == "redispatch_clean":
        return "ready", {"classification": classification}
    if classification == "reuse_result":
        return "verifying", {
            "classification": classification,
            "action_required": "recover_agent_result",
            "evidence": report.get("evidence")}
    if classification == "resume_with_progress":
        return "ready", {
            "classification": classification,
            "evidence": report.get("evidence")}
    # manual_ruling 与未知分类（防御保留位）：保持 waiting_quota
    entry = {"classification":
             classification if isinstance(classification, str)
             and classification else "manual_ruling"}
    rationale = (report.get("rationale")
                 if isinstance(report, dict) else None)
    if rationale is not None:
        entry["rationale"] = rationale
    return "waiting_quota", entry


def _resume_subscription_gate(repo_root, task_id, st) -> dict:
    """resume 面订阅资格裁决（v2.2 C5b；返回 "subscription" 面 dict）。

    顺序即决策序（纯读 + 至多一次对账自愈写，零转态零派发）：
      1. 折算当前 epoch（_epoch_context_after_refresh：resolve_quota_
         detail + evaluate_epoch）；折算失败 → eligible=False——无法
         确认新 epoch，保守不恢复（reason 注明）；
      2. 崩溃窗口对账（evaluate 之前，_reconcile_activation_journal）；
      3. evaluate_subscription_eligibility 纯判定；对账证据在案时资格
         被保守推翻（eligible 强制 False + reason 点名 journal 证据）。

    v2.2 C6（wu-22-C6 reviewer 留账吸收）：签名移除死参数
    provider_status——资格判定消费的是 evaluate_subscription_
    eligibility 内部经 _epoch_context_after_refresh 折算出的
    provider_status（status 参数的解析结果从不进入本函数），参数自
    C5b 落地起即为死参数；移除零行为变化（reviewer 实测佐证）。

    v2.2.1 WU-221-B2（QuotaIdentity）：当前身份指纹在本函数经共享
    模块派生恰一次，同流程内供崩溃对账与纯判定共用——绝不二次解析
    凭证；异身份的既有记录按各面「无先前记录」语义处理（不授权、
    不幂等拦截）。

    返回 {"registered": True, "eligible": bool, "reasons": [...],
    "epoch_id": <§10.1 形状或 None（折算失败）>}；eligible=True 且调用
    方实际完成转态后再补 "activated_epoch_id" / "mark"（转态-记账顺序
    见 resume_from_quota docstring）。本函数不是消费点：C1b 的
    resume-time 窗口消费记账（§15.1 消费事务点）恒不在此发生——消费
    接线归 Resume Controller。
    """
    epoch_context = _epoch_context_after_refresh(repo_root)
    if epoch_context is None:
        return {"registered": True, "eligible": False,
                "reasons": ["无法折算当前 quota epoch（额度明细 snapshot "
                            "缺失、windows 缺失或折算异常）——无法确认新 "
                            "epoch，保守不恢复"],
                "epoch_id": None}
    epoch_id = epoch_context["epoch_id"]
    identity = _current_provider_identity_hash()
    journal_reason = _reconcile_activation_journal(
        repo_root, task_id, st, epoch_id,
        provider_identity_hash=identity)
    evaluation = evaluate_subscription_eligibility(
        repo_root, task_id, current_epoch_id=epoch_id,
        current_executable=epoch_context["executable"],
        provider_status=epoch_context["provider_status"],
        current_provider_identity_hash=identity)
    reasons = list(evaluation["reasons"])
    eligible = evaluation["eligible"]
    if journal_reason is not None:
        eligible = False
        reasons.append(journal_reason)
    return {"registered": True, "eligible": eligible,
            "reasons": reasons, "epoch_id": epoch_id}


def _consumption_boundary_id(repo_root):
    """消费段 representative_boundary_id 派生（v2.2 C7；D10 形态，无宽
    限；RH-04 改名——派生数学零变化）。

    读当前 epoch snapshot 的 windows（resolver.resolve_quota_detail——
    与 _epoch_context_after_refresh 同款入口，新鲜缓存命中零网络），
    复用 wake planner 的 D10 boundary 数学（_bridge_boundary，grace=0
    ——值只是「被消费的新窗口自身身份」，不是 wake 时刻）：多窗取最早
    可解析 reset，返回 "<kind>:<reset_at>"（Z 形式串）。

    RH-04 语义澄清（方案 A 改名不改数学）：本值 = epoch 中用于人类
    审计的代表窗口身份（representative），并非 C2 冻结的 executable
    boundary（= max(blocking EXHAUSTED reset_at) + grace）——旧字段名
    executable_boundary_id 名不符实，故新事件字段改记
    representative_boundary_id；RH-04 之前的旧事件（含 pending / 
    committed）以 legacy 键 executable_boundary_id 在案，读侧一律双键
    兼容（先 new 后 legacy），绝不重写旧 journal。

    detail 缺失 / windows 缺失 / 无可解析窗口 → None（§31 不虚构，调用
    方跳过消费）；epoch_id 恒为幂等 / 归属权威键，该值仅辅助证据。
    纯读零写。
    """
    windows = None
    try:
        from runtime.quota import resolver  # 函数内 import：monkeypatch 友好
        detail = resolver.resolve_quota_detail(repo_root)
    except Exception:
        return None
    if isinstance(detail, dict):
        snapshot = detail.get("snapshot")
        if isinstance(snapshot, dict) \
                and isinstance(snapshot.get("windows"), list):
            windows = snapshot["windows"]
    if windows is None:
        return None
    boundary, _reset_z, _wake_at = _bridge_boundary(windows, 0)
    return boundary
