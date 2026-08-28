#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GLM Conductor v2 quota 调度决策层（纯函数，v2 工作块 B5.3）。

职责：
    消费 runtime.quota.parser 产出的标准化 snapshot（§27 形状），
    产出两类纯决策 dict，供任务编排层（长任务连续性 / 完成门等）消费：
      - evaluate(snapshot)：§28 四态评估。逐窗口给出
        AVAILABLE / PRESSURE / EXHAUSTED / UNKNOWN，总体取最严重，
        并附中文 reason；
      - plan_resume(evaluation, snapshot=None, ...)：§29-§33 恢复
        规划。返回 {"action", "resume_at", "force_refresh_before_resume",
        "reason"}，action ∈ RESUME_ACTIONS。

纪律（本模块的硬边界）：
    - 纯函数、零网络、零文件 I/O；plan_resume 仅在 now 缺省时读取
      系统时钟（datetime.now），除此之外无任何外部状态依赖；
    - 绝不虚构数据（§31）：EXHAUSTED 阻塞窗的 reset 时间未知时
      不虚构调度时刻，回退 periodic_fallback；
    - fail-open（§28/§33）：窗口数据缺失 / 不完整 → UNKNOWN，
      由上层回退周期存活探针，绝不虚构、绝不无限阻塞；
    - 唤醒强制刷新（§32）：resume_at 路径恒带
      force_refresh_before_resume=True——休眠期间缓存的额度快照
      不可信，唤醒后必须强制刷新再重新决策。

语义锚点（与升级指南逐条对应）：
    - §28：used_percent 未知 → 该窗 UNKNOWN；used >= 100 或
      remaining == 0 → EXHAUSTED；remaining <= threshold（含
      0 < remaining）→ PRESSURE；否则 AVAILABLE。总体 = 最严重
      者；规则锁定：任一窗 UNKNOWN 且其余窗无 EXHAUSTED/PRESSURE
      → 总体 UNKNOWN（坏窗明细仍在 windows 列表可见）；
    - §29：PRESSURE → checkpoint——当前原子步骤收尾后在安全
      里程碑落 checkpoint，不开新的大实施阶段；
    - §30：EXHAUSTED → resume_at = max(各阻塞窗 reset_at) +
      grace_seconds（取最晚；Z 形式 ISO8601 可直接比较）；
    - §31：任一阻塞窗 reset_at 为 None（或不可解析）→
      periodic_fallback，绝不虚构 reset；
    - §32：resume_at 路径唤醒后必须强制刷新（force 标志恒 True）；
    - §33：UNKNOWN → periodic_fallback 周期存活探针回退。

依赖：
    仅 Python 3.7 标准库（math / datetime），零第三方依赖，
    `python3 -S` 可运行。状态词汇 QUOTA_STATUSES 复用
    runtime.quota.parser 的词汇表（quota/* 不 import
    runtime.state，无循环导入）。风格对齐 runtime/quota/parser.py。

来源：
    docs/glm-conductor-v2-upgrade-guide-final.md §27（snapshot）、
    §28（四态评估）、§29（PRESSURE→checkpoint）、§30
    （max(reset)+grace）、§31（绝不虚构 reset）、§32（唤醒强制
    刷新）、§33（UNKNOWN 周期存活探针回退）、§45（冒烟场景）
    + v2 升级计划工作块 B5.3。
"""

import math
from datetime import datetime, timedelta, timezone

from runtime.quota.parser import QUOTA_STATUSES

# —— 词汇表常量 ——

# PRESSURE 判定阈值缺省值（§28：remaining <= 阈值即接近耗尽）
DEFAULT_PRESSURE_THRESHOLD_PERCENT = 10.0
# resume_at 宽限秒数缺省值（§30：reset 之后再等一等，防唤醒过早）
DEFAULT_GRACE_SECONDS = 300

# 恢复规划 action 词汇（§29-§33）
RESUME_ACTIONS = ("continue", "checkpoint", "resume_at", "periodic_fallback")

# §28 四态严重度：数值越大越严重，总体 = 最严重者
_SEVERITY = {"AVAILABLE": 0, "UNKNOWN": 1, "PRESSURE": 2, "EXHAUSTED": 3}


# —— 内部小助手（纯函数） ——

def _is_number(value):
    """值是否为可信数值：int/float（bool 除外）且有限。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(value)


def _fmt_number(value):
    """把数值格式化为紧凑文本（300.0 → "300"，12.34 → "12.34"），
    用于 reason 中的剩余百分比与宽限秒数。"""
    value = round(float(value), 2)
    if value == int(value):
        return str(int(value))
    return str(value)


def _parse_iso_utc(text):
    """把 ISO8601 时刻文本解析为 aware datetime（UTC）；失败 → None。

    统一 Z 形式（"2026-08-28T06:45:02Z"，§27 reset_at 的输出形式）；
    Python 3.7 的 fromisoformat 不认 Z 后缀，先改写为 +00:00。
    非 str / 空 / 不可解析 → None（调用方一律按「未知」fail-open）。
    """
    if not isinstance(text, str) or text == "":
        return None
    raw = text.strip()
    if raw.endswith("Z") or raw.endswith("z"):
        raw = raw[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


def _format_iso_z(moment):
    """aware datetime → UTC ISO8601 秒精度 Z 形式字符串（§27 输出纪律）。"""
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _normalize_now(now):
    """归一 now 注入参数：None → 当前 UTC 时刻；datetime / ISO 串 → 时刻。

    now 仅供测试注入当前时刻（或调用方传入参考时刻）使用；§30 规定
    resume_at 只由 reset + grace 决定，本函数的返回值不参与任何
    action / resume_at 的计算（避免测试与运行环境时钟耦合）。
    非法输入 → ValueError（中文）。
    """
    if now is None:
        return datetime.now(timezone.utc)
    if isinstance(now, datetime):
        if now.tzinfo is None:
            return now.replace(tzinfo=timezone.utc)
        return now
    moment = _parse_iso_utc(now)
    if moment is None:
        raise ValueError(
            "plan_resume：now 必须是 datetime 或 ISO8601 字符串，得到 %r"
            % (now,))
    return moment


def _kind_names(windows):
    """把窗口列表的 kind 拼为中文顿号清单（如 "five_hour、weekly"）。"""
    names = [str(w.get("kind")) for w in windows
             if isinstance(w, dict) and w.get("kind")]
    return "、".join(names) if names else "窗口"


# —— §28 四态评估 ——

def _evaluate_window(item, threshold):
    """评估单个窗口 → {"kind","status","used_percent",
    "remaining_percent","reset_at"}；原始字段原样透传，仅新增 status。

    - item 非 dict → 整窗 UNKNOWN（fail-open，明细保留）；
    - used_percent 未知（None / 非数值）→ 该窗 UNKNOWN（§28 锁定
      规则第一优先；显式未知优于猜测）；
    - used >= 100 或 remaining == 0 → EXHAUSTED；
    - remaining <= threshold（含 0 < remaining）→ PRESSURE；
    - 否则 AVAILABLE。remaining 取 snapshot 的 remaining_percent，
      缺失时用 100 - used 推导；两者都不可得已并入 UNKNOWN 分支。
    """
    if not isinstance(item, dict):
        return {"kind": None, "status": "UNKNOWN", "used_percent": None,
                "remaining_percent": None, "reset_at": None}
    used = item.get("used_percent")
    has_used = _is_number(used)
    remaining = item.get("remaining_percent")
    if _is_number(remaining):
        remaining = float(remaining)
    elif has_used:
        remaining = 100.0 - float(used)  # 快照缺 remaining 时推导
    else:
        remaining = None
    if not has_used or remaining is None:
        status = "UNKNOWN"
    elif float(used) >= 100 or remaining == 0:
        status = "EXHAUSTED"
    elif remaining <= threshold:
        status = "PRESSURE"
    else:
        status = "AVAILABLE"
    return {
        "kind": item.get("kind"),
        "status": status,
        "used_percent": used,
        "remaining_percent": item.get("remaining_percent"),
        "reset_at": item.get("reset_at"),
    }


def _overall_reason(status, windows):
    """按总体状态构造中文一句话 reason（§28 风格示例）。"""
    exhausted = [w for w in windows if w["status"] == "EXHAUSTED"]
    pressure = [w for w in windows if w["status"] == "PRESSURE"]
    unknown = [w for w in windows if w["status"] == "UNKNOWN"]
    parts = []
    if exhausted:
        parts.append("%s 窗口已耗尽" % _kind_names(exhausted))
    if pressure:
        segments = []
        for w in pressure:
            segment = "%s 接近阈值" % (
                w["kind"] if w.get("kind") else "窗口")
            if _is_number(w["remaining_percent"]):
                segment += "（剩余 %s%%）" % _fmt_number(
                    w["remaining_percent"])
            segments.append(segment)
        parts.append("，".join(segments))
    if status == "EXHAUSTED":
        return "，".join(parts)
    if status == "PRESSURE":
        return parts[0] if parts else "额度接近阈值"
    if status == "UNKNOWN":
        if unknown:
            return "窗口数据不完整（%s 已用/剩余数据缺失）" % _kind_names(
                unknown)
        return "窗口数据不完整"
    return "窗口余量充足"


def evaluate(snapshot, *, pressure_threshold_percent=
             DEFAULT_PRESSURE_THRESHOLD_PERCENT):
    """按 §28 对标准化 snapshot 做四态评估（纯函数，零 I/O）。

    参数：
      - snapshot：parser.parse_quota_body 产出的 §27 形状 dict
        （也接受同形状的手写 dict）；非 dict / windows 非 list /
        windows 为空 → 总体 UNKNOWN（windows 输出 []，reason 注明
        无可用窗口数据）；
      - pressure_threshold_percent：PRESSURE 阈值，缺省 10.0；非
        有限数值（bool / str / nan / inf / None）→ ValueError。

    返回：
      {"status": <四态之一>,
       "windows": [{"kind", "status", "used_percent",
                    "remaining_percent", "reset_at"}, ...],
       "reason": <中文一句话>}
    windows 与输入逐窗一一对应（原始字段原样透传，仅新增 status），
    坏窗明细保持可见；总体 = 最严重窗口的状态，但任一窗 UNKNOWN 且
    其余窗无 EXHAUSTED/PRESSURE 时总体 UNKNOWN（§28 fail-open）。
    """
    if not _is_number(pressure_threshold_percent):
        raise ValueError(
            "evaluate：pressure_threshold_percent 必须是有限数值，得到 %r"
            % (pressure_threshold_percent,))
    threshold = float(pressure_threshold_percent)

    no_window_result = {
        "status": "UNKNOWN",
        "windows": [],
        "reason": "无可用窗口数据，状态未知",
    }
    if not isinstance(snapshot, dict):
        return no_window_result
    raw_windows = snapshot.get("windows")
    if not isinstance(raw_windows, list) or not raw_windows:
        return no_window_result

    windows = [_evaluate_window(item, threshold) for item in raw_windows]
    overall = max((w["status"] for w in windows),
                  key=lambda name: _SEVERITY[name])
    return {
        "status": overall,
        "windows": windows,
        "reason": _overall_reason(overall, windows),
    }


# —— §29-§33 恢复规划 ——

def _periodic_fallback(reason):
    """统一构造 periodic_fallback 决策（§31/§33：不虚构、不阻塞）。"""
    return {"action": "periodic_fallback", "resume_at": None,
            "force_refresh_before_resume": False, "reason": reason}


def _blocked_resets(evaluation, snapshot):
    """收集 EXHAUSTED 阻塞窗的 (kind, reset_at) 清单。

    优先取 evaluation["windows"] 中 status == "EXHAUSTED" 的窗口
    （evaluate() 输出的逐窗明细）；评估里缺少逐窗信息时（如手工
    构造的最小 evaluation），若提供 snapshot 则保守取其全部窗口的
    reset（等最晚 reset 必然覆盖所有阻塞窗），否则返回空清单。
    """
    windows = evaluation.get("windows")
    blocked = []
    if isinstance(windows, list):
        blocked = [w for w in windows
                   if isinstance(w, dict) and w.get("status") == "EXHAUSTED"]
    if not blocked and isinstance(snapshot, dict):
        snap_windows = snapshot.get("windows")
        if isinstance(snap_windows, list):
            blocked = [w for w in snap_windows if isinstance(w, dict)]
    pairs = []
    for window in blocked:
        pairs.append((window.get("kind"), window.get("reset_at")))
    return pairs


def plan_resume(evaluation, snapshot=None, *, now=None,
                grace_seconds=DEFAULT_GRACE_SECONDS):
    """按 §29-§33 把评估结果翻译为恢复决策（纯函数，除时钟外零 I/O）。

    参数：
      - evaluation：evaluate() 的输出 dict（status 必填）；也接受
        手工构造的最小 {"status": ...} dict；
      - snapshot：可选，§27 形状 snapshot；仅在 evaluation 缺少
        逐窗明细时用于保守收集 reset 时刻；
      - now：仅供测试注入当前时刻（datetime 或 ISO8601 字符串），
        缺省读系统时钟；§30 规定 resume_at 只由 reset + grace 决定，
        本参数不改变任何 action / resume_at 计算；非法 → ValueError；
      - grace_seconds：resume_at 宽限秒数，缺省 300；负数 / 非有限
        数值 → ValueError。

    参数校验（先全量校验再决策，中文 ValueError）：
      - evaluation 非 dict、或 status 不在 QUOTA_STATUSES → ValueError。

    决策规则：
      - AVAILABLE  → continue（不暂停，resume_at=None，不强制刷新）；
      - PRESSURE   → checkpoint（§29：当前原子步骤收尾后在安全
        里程碑落 checkpoint，不开新的大实施阶段；不强制刷新）；
      - EXHAUSTED  → 阻塞窗 = 全部 EXHAUSTED 窗口：任一阻塞窗
        reset_at 未知 → periodic_fallback（§31 绝不虚构 reset）；
        否则 resume_at = max(各阻塞窗 reset_at) + grace_seconds
        （§30 取最晚），action=resume_at，且
        force_refresh_before_resume=True（§32 唤醒后必须强制刷新）；
      - UNKNOWN    → periodic_fallback（§33 周期存活探针回退，
        fail-open，不强制刷新）。

    返回：
      {"action": <RESUME_ACTIONS 之一>, "resume_at": <Z 形式串或 None>,
       "force_refresh_before_resume": <bool>, "reason": <中文一句话>}
    """
    if not isinstance(evaluation, dict):
        raise ValueError(
            "plan_resume：evaluation 必须是 JSON 对象，得到 %s"
            % type(evaluation).__name__)
    status = evaluation.get("status")
    if status not in QUOTA_STATUSES:
        raise ValueError(
            "plan_resume：evaluation.status %r 不在合法取值内（%s）"
            % (status, ", ".join(QUOTA_STATUSES)))
    if not _is_number(grace_seconds) or grace_seconds < 0:
        raise ValueError(
            "plan_resume：grace_seconds 必须是 >= 0 的有限数值，得到 %r"
            % (grace_seconds,))
    grace = timedelta(seconds=float(grace_seconds))
    _normalize_now(now)  # 仅校验注入参数的合法性（见 _normalize_now docstring）

    if status == "AVAILABLE":
        return {"action": "continue", "resume_at": None,
                "force_refresh_before_resume": False,
                "reason": "额度余量充足，继续当前工作"}

    if status == "PRESSURE":
        return {"action": "checkpoint", "resume_at": None,
                "force_refresh_before_resume": False,
                "reason": "额度接近阈值：当前原子步骤收尾后在安全里程碑"
                          "落 checkpoint，不开新的大实施阶段"}

    if status == "UNKNOWN":
        return _periodic_fallback(
            "额度状态未知（fail-open）：回退周期存活探针，不阻塞任务")

    # —— EXHAUSTED（§30/§31/§32） ——
    pairs = _blocked_resets(evaluation, snapshot)
    if not pairs:
        return _periodic_fallback(
            "窗口已耗尽但无法确定任何阻塞窗口的 reset 时间，"
            "不虚构调度：回退周期存活探针")
    moments = []
    for kind, reset_at in pairs:
        moment = _parse_iso_utc(reset_at)
        if moment is None:
            label = kind if kind else "阻塞窗口"
            return _periodic_fallback(
                "%s reset 时间未知，不虚构调度：回退周期存活探针" % label)
        moments.append(moment)
    resume_at = _format_iso_z(max(moments) + grace)
    grace_text = _fmt_number(grace_seconds)
    return {"action": "resume_at", "resume_at": resume_at,
            "force_refresh_before_resume": True,
            "reason": "窗口已耗尽：等到 %s 之后再恢复（最晚 reset 加 %s 秒"
                      "宽限），唤醒后必须强制刷新额度" % (resume_at, grace_text)}
