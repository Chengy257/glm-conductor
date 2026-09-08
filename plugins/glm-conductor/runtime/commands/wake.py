#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""runtime.commands.wake：Persistent Wake Bridge 八子命令（v2.3.1
Wave 3，unit w3-wake-split）。

wake-record / wake-arm / wake-prompt / wake-plan / wake-status /
wake-retime / wake-reconcile / transport-status 的处理函数自 runtime/
cli.py 原样迁入（纯搬运、零行为变化：stdout 字节与退出码逐路径等价；
argv 参数校验与 USAGE 仍归 cli._dispatch，依赖方向恒为 cli →
commands → 域模块，本模块不 import runtime.cli）。输出三形态与 cli
同款契约：_emit 单行 JSON（ensure_ascii=True）、_emit_utf8 单行 JSON
（ensure_ascii=False + 显式 UTF-8 落 stdout）、_emit_text 纯文本
（显式 UTF-8；wake-prompt 成功路径专用）。

函数名与 cli.py 时代保持一致（同名迁移）：
    _persist_bridge_zcode_db_path   zcode_db_path 持久化进 bridge 记
                                    账（additive 键，幂等零写）。
    _anchor_bridge_wake_at          wake-record / wake-arm 落地即
                                    retime 锚定（fail-open）。
    _wake_record                    wake-record：唤醒窗口扣减记账
                                    （v2.1 legacy 兼容入口）。
    _wake_arm                       wake-arm：生产 arm 入口 + arm 时
                                    retime 锚定（v2.3.0 W3 补口）。
    _wake_prompt                    wake-prompt：一次性唤醒 prompt
                                    生成（唯一纯文本 stdout 子命令）。
    _wake_plan                      wake-plan：arm 裁决纯计算
                                    （§22.2 冻结九键 + advisory）。
    _wake_status                    wake-status：bridge 状态只读查询
                                    （§22.3 + W3 宿主一致性观测）。
    _wake_retime                    wake-retime：fire 后「判定 +
                                    retime」一步完成（v23-w3）。
    _wake_reconcile                 wake-reconcile：宿主事实对账
                                    （v2.2 C6，C1 裁决 #9）。
    _transport_status               transport-status：Activation
                                    Transport 事实面只读查询（冻结
                                    十二键）。

专属私有辅助/常量随迁：_wake_cached_quota_inputs（wake-plan / wake-arm
共用的本地额度输入装配）、WAKE_PLAN_ARM_ADVISORY（wake-plan 输出的
advisory 词汇；无外部锚点，原件随函数整体迁出）。WAKE_RETIME_
PARK_OFFSET_MS / WAKE_RETIME_RETRY_OFFSET_MS / _clock_now_ms 为琐碎
共用件：按 W3 迁移规格原件留在 cli.py（tests 以 cli.WAKE_RETIME_* 锚
定原件取值），本模块保留同实现副本——两处同值同实现，commands 模块
间互不 import。

异常词汇（结构例外项，同实现副本）：_TaskMissing / _QuotaFlowRejected
的原件是 cli 侧多命令组共用（policy / permit / wave / quota-exhausted
/ quota-resume 等），依模板不得回 import cli——本模块保留同实现副本，
cli.main 的退出码映射（非 ValueError 的任务缺失 / 事务被拒 →
{"error": str(exc)} + 退出码 1）同时捕获两处同名词，stdout 字节与退
出码逐路径等价；副本绝不子类化 ValueError（避免被校验拒绝类条款截
获而翻转为退出码 2）。

域模块经函数内 import（monkeypatch 友好先例，迁移后同形态保留）：
state / execution_policy / task_manager / activation_transport /
host.zcode_schedule / quota.clock / quota.resolver / quota.scheduler /
quota.identity / continuity.wake_bridge。
"""

import json
import sys
import time


def _emit(payload):
    """向 stdout 写单行 JSON（ensure_ascii=True，与 cli._emit 同款契
    约锚点；本模块自含，绝不回 import cli）。"""
    sys.stdout.write(json.dumps(payload, ensure_ascii=True) + "\n")


def _force_utf8_stdout():
    """把 stdout 调到 UTF-8（wake-prompt 纯文本 / wake-plan、
    wake-status、transport-status 观测面输出用；cli 同名 helper 同款
    ——非 TTY / 测试捕获（StringIO）容错跳过）。"""
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError, OSError):
        pass  # 测试捕获（StringIO）或已被重定向：保持原样


def _emit_text(text):
    """向 stdout 写纯文本（wake-prompt 专用；显式 UTF-8，Windows 管道
    / 控制台零乱码）。"""
    _force_utf8_stdout()
    sys.stdout.write(text + "\n")


def _emit_utf8(payload):
    """向 stdout 写单行 JSON（ensure_ascii=False；与 cli._emit_utf8
    同款契约——v2.2 M5 起 wake-plan / wake-status、v2.2 C6 起
    transport-status 的中文 reason / prompt 面向主会话直接阅读，显式
    UTF-8 落 stdout，Windows 管道 / 控制台零乱码）。"""
    _force_utf8_stdout()
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")


class _TaskMissing(Exception):
    """任务不存在（无 state.json）——cli 同名词同实现副本（原件为
    cli 多命令组共用；cli.main 退出码映射两处同名词同捕获，退出码 1）。"""


class _QuotaFlowRejected(Exception):
    """quota 连续性事务被 task_manager 拒绝——cli 同名词同实现副本
    （原件为 cli 多命令组共用；cli.main 退出码映射两处同名词同捕获，
    退出码 1）。"""


def _clock_now_ms():
    """当前 epoch 毫秒（wake-retime 接线层取 now 点；原件按 W3 迁移
    规格留在 cli.py，本模块为同实现副本，两处互不 import）。"""
    return int(time.time() * 1000)


def _persist_bridge_zcode_db_path(repo_root, task_id, db_path) -> None:
    """把解析出的 zcode_db_path 持久化进 continuation.wake_bridge
    （v2.3.0 W3 additive 键；validate_state 对 wake_bridge 未知键忽略
    ——向前兼容）。零 wake_bridge 状态语义变化（status / boundary /
    automation_id 全不动）；已一致时零写零副作用。"""
    from runtime import state, task_manager
    st = state.load_state(repo_root, task_id)
    if st is None:
        raise _TaskMissing(
            "任务 %s 不存在（%s 下无 state.json），无法持久化 "
            "wake_bridge.zcode_db_path" % (task_id, repo_root))
    continuation = task_manager._ensure_continuation(st)
    bridge = continuation.get("wake_bridge")
    if not isinstance(bridge, dict):
        bridge = state.default_continuation()["wake_bridge"]
        continuation["wake_bridge"] = bridge
    if bridge.get("zcode_db_path") == db_path:
        return  # 已一致：零写（幂等）
    bridge["zcode_db_path"] = db_path
    state.save_state(repo_root, st)


def _anchor_bridge_wake_at(repo_root, task_id, automation_id, fires_at,
                           db_path=None):
    """wake-record 落地即 retime（v2.3.0 W3 §10 arm 时锚点；fail-open）。

    流程：解析 DB 路径（--db > discover_zcode_tasks_db(None)）→ 持久化
    continuation.wake_bridge.zcode_db_path（additive 键，供 fire 后
    wake-retime 复用）→ fires_at 折算 epoch 毫秒（复用
    clock._parse_reset_at_ms 的统一 ISO 解析口径；wake 时刻的 D10 数学
    reset+300s 不变，本层只是把宿主 next_run_at 落地锚定到已定时刻）→
    retime_automation 单列改写。任何一步失败都不抛：返回 (False, 警告
    摘要)，登记结果与 task/bridge 状态语义零影响（D4：native recurring
    网格仍是兜底节拍）；全部成功 → (True, None)。"""
    from runtime.host import zcode_schedule  # 函数内 import：monkeypatch 友好
    try:
        db = zcode_schedule.discover_zcode_tasks_db(db_path)
    except Exception as exc:  # 纯路径解析——防御保留位
        return False, ("zcode_db_path 解析失败 %s: %s"
                       % (type(exc).__name__, exc))
    warnings = []
    try:
        _persist_bridge_zcode_db_path(repo_root, task_id, db)
    except Exception as exc:
        warnings.append("zcode_db_path 持久化失败 %s: %s"
                        % (type(exc).__name__, exc))
    from runtime.quota import clock  # 函数内 import：monkeypatch 友好
    target_ms = clock._parse_reset_at_ms(fires_at)
    if target_ms is None:
        warnings.append("fires_at %r 不可解析为 ISO8601——未 retime"
                        % (fires_at,))
        return False, "; ".join(warnings)
    try:
        zcode_schedule.retime_automation(db, automation_id, target_ms)
    except Exception as exc:
        warnings.append("retime 失败 %s: %s" % (type(exc).__name__, exc))
        return False, "; ".join(warnings)
    return True, ("; ".join(warnings) if warnings else None)


def _wake_record(repo_root, task_id, automation_id, fires_at,
                 db_path=None) -> int:
    """wake-record：唤醒窗口扣减记账（task_manager.record_quota_wake
    薄壳；v2.1 legacy 兼容入口——v2.2 persistent path 禁止调用，C1a：
    arm/fire/create 不消费窗口预算）。automation_id / fires_at 空串由
    API 的 ValueError 闸拒绝（退出码 2）；任务缺失（TaskManagerError）
    → _QuotaFlowRejected（退出码 1）。

    v2.3.0 W3（§10 arm 时锚点）：登记成功后立即经 W1 adapter 把该
    automation 的 next_run_at retime 为记录的 wake 时刻（fires_at），
    zcode_db_path（--db > discover 缺省解析）随之持久化进 bridge 记账。
    一切 retime 失败 fail-open：输出加 "retime_applied": false +
    "retime_warning"（异常摘要），登记照常成功（退出码仍 0，不得因
    retime 失败改变任何 task/bridge 状态语义）；成功 →
    "retime_applied": true。"""
    from runtime import task_manager
    try:
        result = task_manager.record_quota_wake(
            repo_root, task_id, automation_id=automation_id,
            fires_at=fires_at)
    except task_manager.TaskManagerError as exc:
        raise _QuotaFlowRejected(str(exc)) from exc
    retime_applied, retime_warning = _anchor_bridge_wake_at(
        repo_root, task_id, automation_id, fires_at, db_path=db_path)
    result["retime_applied"] = retime_applied
    if retime_warning is not None:
        result["retime_warning"] = retime_warning
    _emit(result)
    return 0


def _wake_arm(repo_root, task_id, automation_id, db_path=None) -> int:
    """wake-arm：Persistent Wake Bridge 的生产 arm 入口 + arm 时 retime
    锚定（v2.3.0 W3 补口；v2.2 既存缺口——SKILL 的 arm_wake_bridge
    记账步骤此前无 CLI 调用面）。

    记账走稳定通道 runtime.activation_transport.arm_transport
    （transport 恒 "recurring_bridge"——唯一 STABLE，词汇不扩；内部
    零改写委托 task_manager.arm_wake_bridge，参数校验顺序 / 冲突语义 /
    幂等语义 / journal 事件全由其承担；TransportReservedError 语义原样
    透传——本命令恒传 stable，实验传输的预留闸保持在 adapter 层）。
    boundary / reset_at / wake_at 与 wake-plan 同源同法：只读本地
    quota-cache.json（_wake_cached_quota_inputs，身份闸，绝不触发
    provider 抓取）→ _bridge_boundary 的 D10 数学（多窗取最早可解析
    reset，wake_at = reset + DEFAULT_GRACE_SECONDS=300，不改数学）；
    bridge_interval_minutes 取 execution_policy.quota_control 键级合并
    （legacy 缺块按默认 60）。无可解析窗口 → boundary_id/wake_at 双
    None → arm_wake_bridge 既有参数闸拒绝（ValueError，退出码 2，
    绝不虚构边界）。arm 失败（TaskManagerError → _QuotaFlowRejected
    退出码 1；ValueError → 退出码 2；TransportReservedError 原样透传
    退出码 1）→ 非零退出且绝不做任何 retime。

    记账成功（含幂等命中）后执行与 wake-record 完全相同的锚定：
    zcode_db_path（--db > discover_zcode_tasks_db(None)）持久化进
    bridge 记账（additive 键）+ retime_automation 把宿主 next_run_at
    锚定到 armed 记录的 wake_at；retime 失败 fail-open（输出
    "retime_applied": false + "retime_warning"，退出码仍 0，arm 记账
    不受影响——native recurring 网格仍是兜底节拍，D4 watchdog）。

    输出 = arm 记账返回字段（automation_id / boundary_id /
    current_boundary_id / reset_at / wake_at / next_wake_at / mode /
    generation / bridge_interval_minutes / status / armed_at
    [, idempotent]）+ retime_applied[, retime_warning]。"""
    from runtime import (activation_transport, execution_policy, state,
                         task_manager)
    from runtime.continuity.wake_bridge import _bridge_boundary
    from runtime.quota import scheduler  # 函数内 import：monkeypatch 友好
    _provider_status, windows = _wake_cached_quota_inputs(repo_root)
    st = state.load_state(repo_root, task_id)
    policy = st.get("execution_policy") if isinstance(st, dict) else None
    interval = execution_policy.default_quota_control(policy)[
        "bridge_interval_minutes"]
    boundary_id, reset_z, wake_at = _bridge_boundary(
        windows, scheduler.DEFAULT_GRACE_SECONDS)
    try:
        result = activation_transport.arm_transport(
            repo_root, task_id, transport="recurring_bridge",
            automation_id=automation_id, boundary_id=boundary_id,
            reset_at=reset_z, wake_at=wake_at, next_wake_at=None,
            bridge_interval_minutes=interval, mode="recurring")
    except task_manager.TaskManagerError as exc:
        raise _QuotaFlowRejected(str(exc)) from exc
    retime_applied, retime_warning = _anchor_bridge_wake_at(
        repo_root, task_id, automation_id, result.get("wake_at"),
        db_path=db_path)
    result["retime_applied"] = retime_applied
    if retime_warning is not None:
        result["retime_warning"] = retime_warning
    _emit(result)
    return 0


def _wake_prompt(repo_root, task_id) -> int:
    """wake-prompt：一次性额度唤醒 prompt 生成（task_manager.
    quota_wake_prompt 薄壳）。成功路径 stdout 纯文本（不裹 JSON，
    主会话直接复制进 automation）；任务缺失（TaskManagerError）→
    _QuotaFlowRejected（错误 JSON + 退出码 1，stdout 契约不破）。"""
    from runtime import task_manager
    try:
        prompt = task_manager.quota_wake_prompt(repo_root, task_id)
    except task_manager.TaskManagerError as exc:
        raise _QuotaFlowRejected(str(exc)) from exc
    _emit_text(prompt)
    return 0


# v2.3.0 W3（§10.5 宿主代价）：wake-plan 输出的 arm 前 advisory（一行
# 字符串字段）——一个宿主会话只能被一条 automation 占用（one-automation-
# per-session）：承载 Global Quota Clock automation 的会话不得再 arm
# task bridge，须另换交互会话执行。
WAKE_PLAN_ARM_ADVISORY = (
    "本会话若已承载 Global Quota Clock automation（quota-clock-bind "
    "绑定的 clock 会话），须另换交互会话执行本桥 arm——"
    "one-automation-per-session（§10.5 宿主代价：clock 占用的宿主会话"
    "不能再 arm task bridge）")


def _wake_cached_quota_inputs(repo_root):
    """wake-plan / wake-arm 共用的本地额度输入装配（零网络，v2.3 W3
    抽取自 _wake_plan 原文，行为逐字不变）：只读本地 quota-cache.json
    （resolver._load_cache / _cache_path 容错原语：缺失 / 坏 JSON /
    status 词汇陈旧 / fetched_at 不可解析一律视为无缓存）+ v2.2.1
    WU-221-B2（QuotaIdentity）直接读者身份闸（异身份缓存视同无缓存；
    legacy 无指纹缓存保守信任；当前指纹经 runtime.quota.identity 派生
    恰一次）→ (provider_status, windows)。无缓存 → (None, None)，
    上层按 UNKNOWN / 无边界保守 fail-open，绝不触发 provider 抓取。"""
    from runtime.quota import resolver  # 函数内 import：monkeypatch 友好
    from runtime.quota.identity import (  # 函数内 import：monkeypatch 友好
        compute_provider_identity_hash)
    provider_status = None
    windows = None
    cache = resolver._load_cache(resolver._cache_path(repo_root))
    if isinstance(cache, dict):
        # v2.2.1 WU-221-B2：异身份缓存视同无缓存（指纹非秘密 16-hex，
        # 派生只读本地凭证，零网络；legacy 无指纹缓存保守信任）
        cached_identity = cache.get("provider_identity_hash")
        if cached_identity is not None \
                and cached_identity != compute_provider_identity_hash():
            cache = None
    if isinstance(cache, dict):
        provider_status = cache.get("status")
        snapshot = cache.get("snapshot")
        if isinstance(snapshot, dict) \
                and isinstance(snapshot.get("windows"), list):
            windows = snapshot["windows"]
    return provider_status, windows


def _wake_plan(repo_root, task_id) -> int:
    """wake-plan：Persistent Wake Bridge 的 arm 裁决（§22.2，v2.2 M5
    wu-22-05，task_manager.plan_wake_bridge 薄壳）。纯计算不创建
    automation；额度输入与 M4 闸同源——只读本地 quota-cache.json
    （resolver._load_cache / _cache_path 容错原语：缺失 / 坏 JSON /
    status 词汇陈旧 / fetched_at 不可解析一律视为无缓存 → 传 None，
    plan 内按 UNKNOWN 保守 fail-open），绝不触发 provider 抓取、绝不
    重试网络。v2.2.1 WU-221-B2（QuotaIdentity）直接读者身份闸：缓存
    绑定异身份（provider_identity_hash 与当前指纹不一致）→ 视同无
    缓存（走既有 no-cache 路径，plan 按 UNKNOWN fail-open）；legacy
    无指纹缓存保守信任（行为逐字不变）；当前指纹经共享模块
    （runtime.quota.identity）派生恰一次。任务缺失（TaskManagerError）
    → _QuotaFlowRejected（退出码 1）。输出 §22.2 冻结九键 + v2.3 W3
    additive 字符串字段 advisory（WAKE_PLAN_ARM_ADVISORY，§10.5 宿主
    代价）（ensure_ascii=False，同 M3 观测面）。"""
    from runtime import task_manager
    provider_status, windows = _wake_cached_quota_inputs(repo_root)
    try:
        plan = task_manager.plan_wake_bridge(
            repo_root, task_id, provider_status=provider_status,
            windows=windows)
    except task_manager.TaskManagerError as exc:
        raise _QuotaFlowRejected(str(exc)) from exc
    plan["advisory"] = WAKE_PLAN_ARM_ADVISORY  # v2.3 W3 additive 字段
    _emit_utf8(plan)
    return 0


def _wake_status(repo_root, task_id) -> int:
    """wake-status：wake bridge 状态只读查询（§22.3，v2.2 M5
    wu-22-05）。展示 wake_bridge.status 十值词汇 + scheduler_context
    （origin + capability 缓存）+ mode/generation 与墓碑。continuation
    缺块按 default_continuation 兜底（§23.1 legacy 兼容，不写盘）；
    零写副作用。任务缺失 → _TaskMissing（退出码 1）。

    v2.3.0 W3 输出增补（additive 三键，全部 fail-open 只读）：
    db_next_run_at（经 W1 adapter inspect_automation 读回宿主行的
    next_run_at 原始值；读不到行 / inspect 失败 → None）、
    wake_at_matches_db（db_next_run_at 与 bridge.wake_at 折算 epoch
    毫秒的一致性布尔；无法判定 → None）、db_check_note（读不到行 /
    无法判定时明示原因：db_row_missing / db_inspect_failed: ... /
    no_automation_id / wake_at_unparsable / db_next_run_at_null）。
    DB 路径取 bridge 记账持久化的 zcode_db_path（v2.3 W3 wake-record
    登记时写入），缺失时按 discover_zcode_tasks_db(None) 缺省解析。"""
    from runtime import state
    from runtime.host import zcode_schedule  # 函数内 import：monkeypatch 友好
    st = state.load_state(repo_root, task_id)
    if st is None:
        raise _TaskMissing(
            "任务 %s 不存在（%s 下无 state.json），无法查询 wake bridge "
            "状态" % (task_id, repo_root))
    continuation = st.get("continuation")
    block = (continuation if isinstance(continuation, dict)
             else state.default_continuation())
    bridge = block.get("wake_bridge")
    bridge = (bridge if isinstance(bridge, dict)
              else state.default_continuation()["wake_bridge"])
    scheduler_context = block.get("scheduler_context")
    scheduler_context = (scheduler_context
                         if isinstance(scheduler_context, dict) else
                         state.default_continuation()["scheduler_context"])
    # —— v2.3.0 W3：宿主 DB next_run_at 与 bridge wake_at 一致性观测 ——
    automation_id = bridge.get("automation_id")
    db_next_run_at = None
    wake_at_matches_db = None
    db_check_note = None
    if isinstance(automation_id, str) and automation_id != "":
        recorded_db = bridge.get("zcode_db_path")
        db = (recorded_db if isinstance(recorded_db, str)
              and recorded_db != ""
              else zcode_schedule.discover_zcode_tasks_db(None))
        try:
            row = zcode_schedule.inspect_automation(db, automation_id)
        except Exception as exc:
            row = None
            db_check_note = "db_inspect_failed: %s: %s" % (
                type(exc).__name__, exc)
        if row is None:
            if db_check_note is None:
                db_check_note = "db_row_missing"
        else:
            db_next_run_at = row.get("next_run_at")
            if db_next_run_at is None:
                db_check_note = "db_next_run_at_null"
    else:
        db_check_note = "no_automation_id"
    if db_next_run_at is not None:
        from runtime.quota import clock  # 函数内 import：monkeypatch 友好
        wake_at = bridge.get("wake_at")
        wake_at_ms = (clock._parse_reset_at_ms(wake_at)
                      if isinstance(wake_at, str) else None)
        if wake_at_ms is None:
            db_check_note = "wake_at_unparsable"
        else:
            wake_at_matches_db = db_next_run_at == wake_at_ms
    _emit_utf8({
        "task_id": st.get("task_id", task_id),
        "status": bridge.get("status", "none"),
        "obligation": block.get("obligation", "none"),
        "mode": bridge.get("mode", "recurring"),
        "generation": bridge.get("generation", 0),
        "automation_id": bridge.get("automation_id"),
        "boundary_id": bridge.get("boundary_id"),
        "current_boundary_id": bridge.get("current_boundary_id"),
        "next_wake_at": bridge.get("next_wake_at"),
        "bridge_interval_minutes": bridge.get("bridge_interval_minutes"),
        "scheduler_context": scheduler_context,
        "wake_bridge": bridge,
        "tombstone": block.get("tombstone"),
        "db_next_run_at": db_next_run_at,
        "wake_at_matches_db": wake_at_matches_db,
        "db_check_note": db_check_note,
    })
    return 0


# —— v2.3.0（v23-w3）：Task Wake Bridge fire 后锚点（wake-retime） ——

# park 偏移（毫秒）：quota 可执行 → 本桥停摆一年（任务醒了不再等待；
# 任务下次再睡会走既有 arm 流程重新建置，automation 仍按 native
# recurring 服役但等价 no-op）。同实现副本：原件留在 cli.py（tests 以
# cli.WAKE_RETIME_PARK_OFFSET_MS 锚定），两处同值互不 import。
WAKE_RETIME_PARK_OFFSET_MS = 365 * 86400 * 1000
# 无已知边界重试偏移（毫秒）：§10.4「保持简单优先」的 5 分钟短重试
# （同实现副本：原件留在 cli.py，tests 以
# cli.WAKE_RETIME_RETRY_OFFSET_MS 锚定）。
WAKE_RETIME_RETRY_OFFSET_MS = 300 * 1000


def _wake_retime(repo_root, task_id) -> int:
    """wake-retime：fire 后锚点「判定 + retime」一步完成（v2.3.0 §10 /
    W3，unit v23-w3）。wake turn 在 quota-resolve --force-refresh 之后
    调用本命令：

      1. 读 task state + bridge 记账（automation_id / zcode_db_path）；
         无 armed bridge（status ∉ armed/fired 或 automation_id 缺失，
         任务缺失同口径）→ {"status": "no_bridge"} 退出 0（不算错）；
      2. force-refresh quota（resolver.resolve_quota_detail——与现有
         wake 路径同法的四级层级强刷入口）；
      3. 判定（复用既有语义，不新发明）：
         - quota 可执行（status ∈ task_manager.QUOTA_RESUME_STATUSES
           ——resume 面既有恢复四态词汇）→ park 本桥：
           next_run_at = now + 365 天，
           {"status": "executable_park_bridge", "next_target_at": ...}
           ——注意本命令不等于执行 resume：resume 仍由 prompt 既有
           quota-resume 步骤按授权进行，本命令只管节拍；
         - 不可执行且存在已知未来边界（scheduler.plan_resume 的
           max(EXHAUSTED resets)+300s 宽限口径，D10 数学直接复用）→
           retime 该值，{"status": "retime_boundary",
           "next_target_at": ...}；
         - 不可执行且无已知边界（periodic_fallback / reset 不可解析）
           → retime now + 5 分钟，{"status": "retime_retry",
           "next_target_at": ...}；
      4. 决策或 adapter 任何异常 → {"status": "retime_failed",
         "warning": <异常摘要>} 仍退出 0（fail-open，native recurring
         watchdog 接管，§4.2 同款）。

    零 task state / journal / accounting 写副作用（纯节拍职责）。
    zcode_db_path 取 bridge 记账持久化值（wake-record 登记时写入），
    缺失时按 discover_zcode_tasks_db(None) 缺省解析。next_target_at
    恒为 epoch 毫秒（与宿主 DB next_run_at 同口径，可直接比对）。"""
    from runtime import state, task_manager
    from runtime.host import zcode_schedule  # 函数内 import：monkeypatch 友好
    from runtime.quota import clock, resolver, scheduler  # monkeypatch 友好
    st = state.load_state(repo_root, task_id)
    if st is None:
        _emit({"status": "no_bridge", "task_id": task_id,
               "reason": "任务不存在（无 state.json）——无桥可重定时"})
        return 0
    continuation = st.get("continuation")
    block = (continuation if isinstance(continuation, dict)
             else state.default_continuation())
    bridge = block.get("wake_bridge")
    bridge = (bridge if isinstance(bridge, dict)
              else state.default_continuation()["wake_bridge"])
    automation_id = bridge.get("automation_id")
    if bridge.get("status") not in task_manager.REUSABLE_BRIDGE_STATUSES \
            or not isinstance(automation_id, str) or automation_id == "":
        _emit({"status": "no_bridge", "task_id": task_id,
               "bridge_status": bridge.get("status")})
        return 0
    try:
        detail = resolver.resolve_quota_detail(repo_root,
                                               force_refresh=True)
        quota_status = (detail.get("status") if isinstance(detail, dict)
                        else None)
        now_ms = _clock_now_ms()
        if quota_status in task_manager.QUOTA_RESUME_STATUSES:
            decision = "executable_park_bridge"
            target_ms = now_ms + WAKE_RETIME_PARK_OFFSET_MS
        else:
            evaluation = scheduler.evaluate(
                detail.get("snapshot") if isinstance(detail, dict)
                else None)
            plan = scheduler.plan_resume(evaluation)
            boundary_ms = (clock._parse_reset_at_ms(plan.get("resume_at"))
                           if plan.get("action") == "resume_at" else None)
            if boundary_ms is not None:
                decision = "retime_boundary"
                target_ms = boundary_ms
            else:
                decision = "retime_retry"
                target_ms = now_ms + WAKE_RETIME_RETRY_OFFSET_MS
        recorded_db = bridge.get("zcode_db_path")
        db = (recorded_db if isinstance(recorded_db, str)
              and recorded_db != ""
              else zcode_schedule.discover_zcode_tasks_db(None))
        zcode_schedule.retime_automation(db, automation_id, int(target_ms))
    except Exception as exc:
        _emit({"status": "retime_failed", "task_id": task_id,
               "automation_id": automation_id,
               "warning": "%s: %s" % (type(exc).__name__, exc)})
        return 0
    _emit({"status": decision, "task_id": task_id,
           "automation_id": automation_id, "quota_status": quota_status,
           "next_target_at": int(target_ms)})
    return 0


# —— v2.2 修正计划 C6（wu-22-C6）：wake-reconcile / transport-status ——

def _wake_reconcile(repo_root, task_id, host_status, observed_at=None) -> int:
    """wake-reconcile：宿主事实会话侧对账（task_manager.
    reconcile_wake_bridge_from_host 薄壳，v2.2 C1 裁决 #9）。

    C6 面向：宿主事实由调用方（主会话 / dogfood 操作者）显式供给
    （CronList 观测结论），runtime 零宿主探针——本子命令是 C1b 纯账本
    对账 helper 的显式输入口。host_status 词汇闸在 API 内（先于 I/O，
    中文 ValueError → 退出码 2）；任务缺失（TaskManagerError）→
    _QuotaFlowRejected（退出码 1）。§22.5：对账只能降级 / 确认，
    绝不制造 armed。"""
    from runtime import task_manager
    try:
        result = task_manager.reconcile_wake_bridge_from_host(
            repo_root, task_id, host_status=host_status,
            observed_at=observed_at)
    except task_manager.TaskManagerError as exc:
        raise _QuotaFlowRejected(str(exc)) from exc
    _emit(result)
    return 0


def _transport_status(repo_root, task_id) -> int:
    """transport-status：Activation Transport 事实面只读查询
    （runtime.activation_transport.activation_transport_status 薄壳，
    v2.2 C6 / C8 / dogfood 面）。

    输出冻结十二键（transport / stable / armed / bridge_status /
    reasons ...；ensure_ascii=False——中文 reasons 面向主会话直接阅读，
    同 wake-plan / wake-status 观测面口径）。零写副作用；任务缺失
    （TaskManagerError）→ _QuotaFlowRejected（退出码 1）。"""
    from runtime import activation_transport, task_manager
    try:
        result = activation_transport.activation_transport_status(
            repo_root, task_id)
    except task_manager.TaskManagerError as exc:
        raise _QuotaFlowRejected(str(exc)) from exc
    _emit_utf8(result)
    return 0
