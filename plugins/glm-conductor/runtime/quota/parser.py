#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GLM Conductor v2 quota 标准化解析与评估层（纯函数，v2 工作块 B5.1）。

职责：
    把 provider 监控端点的原始响应体（fixture 驱动）解析为标准化
    snapshot dict（升级指南 §27 形状），并在此之上做四态评估与
    恢复规划（v2.4 Phase 3 P3-A 观测核收缩：原 B5.3 scheduler 的
    纯函数决策核心行为保持移入本模块，scheduler 已删除）：
      - parse_quota_body()：envelope → snapshot；容忍加性未知字段，
        容忍 weekly 缺席（§26：lite 套餐实测只有 five_hour 窗）；
      - coerce_percent()：percentage / 剩余比例 → 0..100 的两位小数
        float，任何不可信输入一律 None（不静默造数）；
      - epoch_ms_to_iso()：nextResetTime（epoch 毫秒）→ UTC
        ISO8601 秒精度字符串（"2026-08-28T06:45:02Z" 形式）；
      - evaluate()：§28 四态评估（snapshot → 逐窗 AVAILABLE /
        PRESSURE / EXHAUSTED / UNKNOWN + 总体 status + 中文 reason）；
      - plan_resume()：§29-§33 恢复规划（评估 → {action, resume_at,
        force_refresh_before_resume, reason}，action ∈ RESUME_ACTIONS）。

判窗纪律（§26，本模块的核心规则）：
    绝不按 type 字段或数组位置判窗（TOKENS_LIMIT / CREDIT_LIMIT /
    TIME_LIMIT 都可能出现语义窗），只用语义字段：
        unit == 3 且 number == 5 → five_hour
        unit == 6 且 number == 1 → weekly
    其他 unit/number 组合（含缺语义字段的任何条目）→ 跳过，容忍
    不报错；同一 kind 重复出现时保留第一条，后续同 kind 跳过。
    snapshot 的 windows 按 five_hour 在前、weekly 在后排序；
    weekly 是可选的——解析层绝不假设双窗必然存在。

status 纪律：
    snapshot 的 status 恒为 None——解析（parse_quota_body）不判断
    状态、不猜测；AVAILABLE / PRESSURE / EXHAUSTED / UNKNOWN 的
    四态评估由本模块 evaluate()（§28）承担（v2.4 P3-A 起评估与
    恢复规划的纯函数落点即本模块）。

不透明纪律：
    snapshot 不透出任何原始响应字段（usageDetails / currentValue /
    usage 等一律不透出），不含任何秘密；plan_generation 由条目
    type 词汇推断（任一 CREDIT_LIMIT → "v3"，否则有 TOKENS_LIMIT →
    "v2"，否则 None）——这是套餐代际标签，与判窗无关。

依赖：
    仅 Python 3 标准库（datetime / math）+ runtime.quota.time_utils
    （ISO-8601 UTC 时间原语，导入方向 parser → time_utils，本模块
    绝不被其反向依赖），零第三方依赖，`python3 -S` 可运行。风格
    对齐 runtime/state.py。

来源：
    v2.0 设计（已蒸馏入 docs/architecture.md：V2/V3 解析兼容 /
    snapshot 形状 / 状态词汇 / fixture 清单）+ v2 升级计划工作块 B5.1。
"""

import math
from datetime import datetime, timedelta, timezone

# v2.4 Phase 3 P3-A（行为保持移入）：evaluate / plan_resume 所需的
# ISO-8601 UTC 时间原语，规范落点为 time_utils（WU-221-C2），此处经
# import 复用（reset 时间数学的唯一规范来源，不复制第二份）。
from runtime.quota.provider import QuotaProviderError
from runtime.quota.time_utils import _format_iso_z, _normalize_now, _parse_iso_utc

# —— 词汇表常量 ——

# 标准化窗口种类（§27：five_hour 在前 weekly 在后）
WINDOW_KINDS = ("five_hour", "weekly")
# quota 状态词汇（§28；解析层不判断，恒留 None 给调度器）
QUOTA_STATUSES = ("AVAILABLE", "PRESSURE", "EXHAUSTED", "UNKNOWN")

# 判窗语义字段映射（§26 实测映射；与 type 无关）
_FIVE_HOUR_UNIT = 3
_FIVE_HOUR_NUMBER = 5
_WEEKLY_UNIT = 6
_WEEKLY_NUMBER = 1

# epoch 毫秒起点（UTC）
_EPOCH_UTC = datetime(1970, 1, 1, tzinfo=timezone.utc)


# —— 数值归一助手（纯函数） ——

def coerce_percent(value):
    """把 percentage 类输入归一为 0..100 的 float（四舍五入到 2 位小数）。

    - int / float（bool 除外）且 0 <= value <= 100 → float(value)；
    - 其余一律 None：None / str / 越界 / nan / inf / 其他类型。
    数据不完整时宁可 None 也不造数（§31 纪律在数值域的对应）。
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value):
        return None
    if value < 0 or value > 100:
        return None
    return round(float(value), 2)


def epoch_ms_to_iso(value):
    """把 epoch 毫秒时间戳转为 UTC ISO8601 秒精度字符串。

    - 数值（bool 除外）且 > 0 → "YYYY-MM-DDTHH:MM:SSZ" 形式
      （如 "2026-08-28T06:45:02Z"，秒精度，毫秒截断）；
    - 其余一律 None：None / 非数值 / <= 0 / nan / inf / 超出
      datetime 可表示范围。
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value):
        return None
    if value <= 0:
        return None
    total_seconds = int(value) // 1000
    try:
        moment = _EPOCH_UTC + timedelta(seconds=total_seconds)
    except (OverflowError, ValueError, OSError):
        return None
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


# —— 判窗与条目解析（内部助手） ——

def _window_kind(item):
    """按语义字段判窗（§26：与 type 无关）；不匹配 → None。"""
    unit = item.get("unit")
    number = item.get("number")
    if isinstance(unit, bool) or isinstance(number, bool):
        return None
    if not isinstance(unit, (int, float)) or not isinstance(number, (int, float)):
        return None
    if unit == _FIVE_HOUR_UNIT and number == _FIVE_HOUR_NUMBER:
        return "five_hour"
    if unit == _WEEKLY_UNIT and number == _WEEKLY_NUMBER:
        return "weekly"
    return None


def _used_percent(item):
    """条目的已用百分比：percentage 优先，缺失时走 remaining 回退。

    - coerce_percent(percentage) 非 None → 直接用；
    - None 时回退：remaining 为数值且 0 <= r <= 100 → used =
      coerce_percent(100 - r)（remaining 语义是「剩余百分比」）；
    - 再无 → None（数据不完整，窗口保留但 used_percent=None）。
    """
    used = coerce_percent(item.get("percentage"))
    if used is not None:
        return used
    remaining = item.get("remaining")
    if isinstance(remaining, bool) or not isinstance(remaining, (int, float)):
        return None
    if not math.isfinite(remaining) or remaining < 0 or remaining > 100:
        return None
    return coerce_percent(100 - remaining)


def _parse_window(item):
    """把单个语义条目解析为标准化窗口 dict（§27 窗口形状）。"""
    used = _used_percent(item)
    return {
        "kind": _window_kind(item),
        "used_percent": used,
        "remaining_percent":
            coerce_percent(100 - used) if used is not None else None,
        "reset_at": epoch_ms_to_iso(item.get("nextResetTime")),
    }


def _plan_generation(limits):
    """由条目 type 词汇推断套餐代际：CREDIT_LIMIT → v3，
    否则有 TOKENS_LIMIT → v2，否则 None（与判窗无关的标签推断）。"""
    has_credit = False
    has_tokens = False
    for item in limits:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if item_type == "CREDIT_LIMIT":
            has_credit = True
        elif item_type == "TOKENS_LIMIT":
            has_tokens = True
    if has_credit:
        return "v3"
    if has_tokens:
        return "v2"
    return None


# —— 主入口 ——

def parse_quota_body(body, *, provider, fetched_at):
    """把监控端点原始响应体解析为标准化 snapshot dict（§27 形状）。

    参数：
      - body：json.load 后的响应体（任意类型，结构错误 → malformed）；
      - provider：provider 标识，原样进入 snapshot["provider"]；
      - fetched_at：抓取时刻标记，原样进入 snapshot["fetched_at"]。

    行为：
      - body 非 dict → QuotaProviderError(kind="malformed")；
      - data = body.get("data") 非 dict 时，若 body 自身含 limits
        键则直接用 body 当 data（裸 limits 形态），否则 malformed；
      - limits 非 list 或为空 → malformed；
      - 逐条目判窗（语义字段，§26；与 type 无关）：非 dict 条目
        跳过；unit/number 不映射任何 kind 的条目跳过；同 kind 重复
        保留第一条；windows 按 five_hour 在前 weekly 在后排序；
      - percentage 缺失走 remaining 回退（见 _used_percent），两者
        都不可信时窗口保留但 used_percent / remaining_percent 为
        None（§31：显式未知优于伪造数值）；
      - plan_generation / plan_level / status 规则见模块 docstring
        （status 恒 None——评估属 B5.3 调度器）。

    返回（不含任何原始响应字段与秘密）：
        {"provider": ..., "plan_generation": ..., "plan_level": ...,
         "fetched_at": ..., "status": None,
         "windows": [{"kind", "used_percent", "remaining_percent",
                      "reset_at"}, ...]}
    """
    if not isinstance(body, dict):
        raise QuotaProviderError(
            "quota 响应体必须是 JSON 对象，得到 %s" % type(body).__name__,
            "malformed")
    data = body.get("data")
    if not isinstance(data, dict):
        if "limits" in body:
            # 裸 limits 形态：无 data envelope，body 自身承载条目
            data = body
        else:
            raise QuotaProviderError(
                "quota 响应缺少 data.limits 结构（非成功响应形态）",
                "malformed")
    limits = data.get("limits")
    if not isinstance(limits, list) or not limits:
        raise QuotaProviderError(
            "quota 响应 data.limits 必须是非空数组", "malformed")

    windows = []
    seen_kinds = set()
    for item in limits:
        if not isinstance(item, dict):
            continue  # 条目级容错：非 dict 条目跳过，不影响其余条目
        kind = _window_kind(item)
        if kind is None or kind in seen_kinds:
            continue  # 不映射任何 kind / 重复 kind：跳过，容忍不报错
        seen_kinds.add(kind)
        window = _parse_window(item)
        window["kind"] = kind
        windows.append(window)
    windows.sort(key=lambda window: WINDOW_KINDS.index(window["kind"]))

    level = data.get("level")
    return {
        "provider": provider,
        "plan_generation": _plan_generation(limits),
        "plan_level": level if isinstance(level, str) else None,
        "fetched_at": fetched_at,
        "status": None,  # 恒 None：状态评估见本模块 evaluate()（§28）
        "windows": windows,
    }


# —— §28 四态评估与 §29-§33 恢复规划 ——
# （v2.4 Phase 3 P3-A 观测核收缩：以下纯函数自已删除的
# runtime/quota/scheduler.py 逐字移入，行为保持零变化。）

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
