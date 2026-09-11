#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GLM Conductor v2.2 adaptive quota observer 纯决策层（v2.2 M3，wu-22-03）。

职责（主计划 §16 / §6）：
    在 M2 纯决策器 control.evaluate_task_quota_phase（§17.1 冻结 8 键，
    D7 冻结映射）之上叠观测编排，机械回答控制回路的两个问题：
      1. 什么时候该再查额度？——§6.2 自适应间隔 + 已知 reset 收紧 +
         §6.4 lazy heartbeat 判定（next_check_at 按需纯推导，不落任何
         observer 状态文件——避免第二 truth source）；
      2. 当前 execution phase 是什么、要不要唤醒？——§16.1 观测 dict。
    供派发闸（M4 / wu-22-04）、桥接生命周期（M5）与 Stop gate（M7）
    经 CLI（quota-observe / quota-phase）消费。

三个公开函数（全 keyword-only、零 I/O）：
    next_check_interval(...) → int 秒（自适应间隔；now 必填）
    should_refresh(...)      → bool（lazy heartbeat 判定，§6.4）
    observe(...)             → §16.1 观测 dict（八键冻结，键名逐字）

纪律（本模块的硬边界）：
    - 纯决策（§16.2 Observer 不做什么）：零网络、零文件 I/O、不写
      state、不建 automation、无 model call；
    - 无时间墙钟依赖：now 一律入参——next_check_interval 的 now 必填
      （None → ValueError）；observe 的 now 缺省 None 且 None 时
      next_check_at=None，绝不偷偷读时钟（该选择已在 docstring 写明
      并由测试锚定）；
    - 无 daemon（WU-22-03 禁止项）：无定时器 / 无 sleep loop / 无后台
      轮询——任何 hook / CLI / agent action 进入 runtime 时以
      should_refresh(now, next_check_at) 即得「是否该刷新」；
    - 不重复实现映射：execution phase / continuation obligation 全部
      复用 control.evaluate_task_quota_phase（D7 映射单一真相源），
      本模块只叠加观测层（间隔表 + 观测 dict 组装）；
    - fail-open：reset_at 缺失 / 不可解析 → 按「未知」处理（间隔不
      收紧、透传 None、不虚构时刻，§31 同源纪律）；next_check_at
      不可解析 → should_refresh 按「到期」处理（绝不因坏值跳过刷新）；
    - 时间数学与格式工具复用 runtime.quota.scheduler 的私有实现
      （_parse_iso_utc / _format_iso_z / _is_number），不改 scheduler。

依赖：
    仅 Python 3.7 标准库 + runtime.quota.control / runtime.quota.scheduler；
    quota/* 包纪律：不 import runtime.state / runtime.task_manager。

来源：
    v2.2 设计（已蒸馏入 docs/architecture.md：Adaptive Quota Heartbeat /
    推荐心跳周期 / 事件触发刷新——查询侧对应：进 runtime 先判
    should_refresh / lazy heartbeat：无 daemon、避免多余 truth source /
    Quota Observer 设计与输出 dict 形状 / Observer 不做什么 / CLI 输出）
    + WU-22-03（adaptive interval / event-triggered refresh /
    lazy heartbeat / next_check_at / 禁 daemon）。
"""

from datetime import datetime, timedelta, timezone

from runtime.quota.control import EXECUTION_PHASES, evaluate_task_quota_phase
from runtime.quota.scheduler import (
    DEFAULT_GRACE_SECONDS,
    _format_iso_z,
    _is_number,
    _parse_iso_utc,
)

# —— §6.2 自适应间隔表（WU-22-03 冻结：单位秒，按 execution phase 取值） ——
# 消费的是 M2 的 execution 相决策（§4.2）而非 provider 维度四态；键集
# 与 control.EXECUTION_PHASES 一一对应（一致性由测试锚定）。
ADAPTIVE_INTERVAL_SECONDS = {
    "NORMAL": 1800,
    "PRESSURE": 600,
    "DRAINING": 300,
    "BLOCKED": 300,
}

# 间隔下限（秒）：任何相 / 任何收紧都不低于 60 秒（§6.2「不得低于
# provider 安全频率」的保守下界；reset 边界已过时 lazy heartbeat 的
# 最小节奏）
MIN_CHECK_INTERVAL_SECONDS = 60

# wake_recommended 认可的 execution phase 子集（观测层「建议唤醒」的
# 相：PRESSURE 收缩期 / DRAINING 排水期；NORMAL 不需要、BLOCKED 归
# §8.3 的 waiting_quota/degraded 义务管）
_WAKE_RECOMMENDED_PHASES = ("PRESSURE", "DRAINING")


# —— 内部小助手（纯函数） ——

def _normalize_moment(now):
    """归一 now / 时刻入参：datetime（naive 按 UTC）或 ISO8601 串。

    None 原样返回（None 的语义由调用方定义：next_check_interval 的
    now 必填 → ValueError；observe 的 now=None → next_check_at=None）；
    非法输入 → ValueError（中文，对齐 scheduler._normalize_now 口径）。
    """
    if now is None:
        return None
    if isinstance(now, datetime):
        if now.tzinfo is None:
            return now.replace(tzinfo=timezone.utc)
        return now
    moment = _parse_iso_utc(now)
    if moment is None:
        raise ValueError(
            "observer：now 必须是 datetime 或 ISO8601 字符串，得到 %r"
            % (now,))
    return moment


def _numeric_windows(windows):
    """§27 窗口清单中的「可数值评估窗」（与 control 同一口径：dict 且
    remaining_percent 为有限数值；windows 非 list 按空处理）。"""
    return [item for item in (windows if isinstance(windows, list) else [])
            if isinstance(item, dict) and _is_number(item.get("remaining_percent"))]


# —— §6.2 自适应间隔 ——

def next_check_interval(*, execution_phase, reset_at=None, now,
                        grace_seconds=DEFAULT_GRACE_SECONDS):
    """自适应查询间隔（§6.2，纯函数）：下次查询距 now 的秒数（int）。

    参数（全 keyword-only）：
      - execution_phase：M2 的 execution 相（control.EXECUTION_PHASES
        四值）；词汇外 → ValueError；
      - reset_at：已知重置时刻（§27 窗口字段，ISO8601 串）；None /
        不可解析 → 按「未知」处理（不收紧）；
      - now：参考时刻（datetime / ISO8601 串），**必填**（None →
        ValueError——本模块无时间墙钟依赖的硬边界）；
      - grace_seconds：边界前宽限秒数，缺省 DEFAULT_GRACE_SECONDS
        （300，单一真相源 scheduler 常量）；负数 / 非有限数值 →
        ValueError。

    规则（按序）：
      1. 基础间隔 = ADAPTIVE_INTERVAL_SECONDS[execution_phase]
         （NORMAL 1800 / PRESSURE 600 / DRAINING 300 / BLOCKED 300）；
      2. reset_at 已知：与「距 reset_at - grace_seconds 的秒数」取
         min——已知 reset 时不得晚于边界前 grace 秒才查；
      3. 下限 MIN_CHECK_INTERVAL_SECONDS（60 秒）——reset 边界已过 /
         紧贴时也至少间隔 60 秒（lazy heartbeat 最小节奏）。

    返回：int 秒。
    """
    if execution_phase not in EXECUTION_PHASES:
        raise ValueError(
            "next_check_interval：execution_phase %r 不在合法取值内"
            "（%s）" % (execution_phase, ", ".join(EXECUTION_PHASES)))
    moment = _normalize_moment(now)
    if moment is None:
        raise ValueError(
            "next_check_interval：now 必填（datetime 或 ISO8601 字符串）"
            "——observer 无时间墙钟依赖，不接受 None")
    if not _is_number(grace_seconds) or grace_seconds < 0:
        raise ValueError(
            "next_check_interval：grace_seconds 必须是 >= 0 的有限数值，"
            "得到 %r" % (grace_seconds,))
    interval = ADAPTIVE_INTERVAL_SECONDS[execution_phase]
    reset = _parse_iso_utc(reset_at)
    if reset is not None:
        deadline = reset - timedelta(seconds=float(grace_seconds))
        bounded = int((deadline - moment).total_seconds())
        if bounded < interval:
            interval = bounded
    if interval < MIN_CHECK_INTERVAL_SECONDS:
        interval = MIN_CHECK_INTERVAL_SECONDS
    return interval


# —— §6.4 lazy heartbeat 判定 ——

def should_refresh(*, now, next_check_at):
    """lazy heartbeat 判定（§6.4，纯函数）：now >= next_check_at → True。

    参数（全 keyword-only）：
      - now：参考时刻（datetime / ISO8601 串）；None / 非法 →
        ValueError（无时间墙钟依赖，消费方自带 now）；
      - next_check_at：observe 产出的下次查询时刻（Z 形式 ISO 串）。

    规则：
      - next_check_at 为 None（从未推导过——observe 的 now=None 产物）
        → True：没有可信的「下次」时刻即立即刷新（fail-open 保守）；
      - next_check_at 不可解析（非 None 非 ISO）→ True：按到期处理，
        绝不因坏值跳过刷新；
      - 否则 now >= next_check_at → True（边界含等号）。

    纯比较：不读时钟、零 I/O——任何 hook / CLI / agent action 进入
    runtime 时调用即得「是否该刷新」（§6.4 lazy heartbeat 全部形态）。
    """
    moment = _normalize_moment(now)
    if moment is None:
        raise ValueError(
            "should_refresh：now 必填（datetime 或 ISO8601 字符串）——"
            "observer 无时间墙钟依赖，不接受 None")
    if next_check_at is None:
        return True
    due = _parse_iso_utc(next_check_at)
    if due is None:
        return True
    return moment >= due


# —— §16.1 观测 dict ——

def observe(*, provider_status, windows, quota_control=None,
            max_workers=None, task_active=True,
            wake_bridge_status="none", grace_seconds=DEFAULT_GRACE_SECONDS,
            now=None):
    """§16.1 观测 dict（纯函数；内部复用 M2 决策器，不重复实现映射）。

    参数：
      - provider_status / windows / quota_control / max_workers /
        task_active / wake_bridge_status / grace_seconds：与
        control.evaluate_task_quota_phase 同名同义（原样透传，校验
        与中文 ValueError 均由决策器负责）；
      - now：观测参考时刻（datetime / ISO8601 串）。**缺省 None，且
        None 时绝不读墙钟**：next_check_at=None，其余七键照常产出
        （决策、唤醒判定与透传键都不依赖 now；只有 next_check_at 的
        推导需要 now——需要时必须显式传入，CLI 层负责注入墙钟）。
        非法（非 datetime / 非 ISO 串）→ ValueError。

    返回（§16.1 冻结八键，键名逐字）：
      {"provider_status":  入参回显（= 决策器同键），
       "execution_phase":  决策器 execution_phase（D7 冻结映射），
       "remaining_percent": 数值评估窗中最小剩余窗的 remaining_percent
                            原样透传（并列取先出现者）；无可数值评估
                            的窗 → None，
       "reset_at":          同上最小剩余窗的 reset_at 原样透传；无 →
                            None，
       "next_check_at":     min(now + next_check_interval(...),
                            reset_at - grace_seconds) 的 Z 形式 ISO——
                            已知 reset 时不得晚于边界前 grace 秒
                            （§6.2）；reset_at 未知 / 不可解析 → 纯
                            now + interval；now=None → None，
       "reason":            决策器中文 reason 原样透传，
       "wake_recommended":  execution_phase ∈ {PRESSURE, DRAINING} 且
                            任一数值窗 reset_at 已知 且 task_active，
       "wake_required":     continuation_obligation == "wake_required"
                            （§8.3 义务词汇的观测面投影）}
    """
    decision = evaluate_task_quota_phase(
        provider_status=provider_status, windows=windows,
        quota_control=quota_control, max_workers=max_workers,
        task_active=task_active, wake_bridge_status=wake_bridge_status,
        grace_seconds=grace_seconds)
    moment = _normalize_moment(now)
    numeric = _numeric_windows(windows)
    min_window = (min(numeric,
                      key=lambda item: float(item["remaining_percent"]))
                  if numeric else None)
    remaining = min_window.get("remaining_percent") if min_window else None
    reset_at = min_window.get("reset_at") if min_window else None
    phase = decision["execution_phase"]
    # 「reset_at 已知」与决策器 §8.3 的 reset_known 同口径：任一数值窗
    # 有真值 reset_at（观测面与义务判定不得各说各话）
    reset_known = any(item.get("reset_at") for item in numeric)
    wake_recommended = (phase in _WAKE_RECOMMENDED_PHASES
                        and reset_known and bool(task_active))
    next_check_at = None
    if moment is not None:
        interval = next_check_interval(
            execution_phase=phase, reset_at=reset_at, now=moment,
            grace_seconds=grace_seconds)
        next_moment = moment + timedelta(seconds=interval)
        deadline = _parse_iso_utc(reset_at)
        if deadline is not None:
            bounded = deadline - timedelta(seconds=float(grace_seconds))
            if bounded < next_moment:  # 取早：不晚于边界前 grace 秒
                next_moment = bounded
        next_check_at = _format_iso_z(next_moment)
    return {
        "provider_status": decision["provider_status"],
        "execution_phase": phase,
        "remaining_percent": remaining,
        "reset_at": reset_at,
        "next_check_at": next_check_at,
        "reason": decision["reason"],
        "wake_recommended": wake_recommended,
        "wake_required": (decision["continuation_obligation"]
                          == "wake_required"),
    }
