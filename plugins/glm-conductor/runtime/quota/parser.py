#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GLM Conductor v2 quota 标准化解析层（纯函数，v2 工作块 B5.1）。

职责：
    把 provider 监控端点的原始响应体（fixture 驱动）解析为标准化
    snapshot dict（升级指南 §27 形状），供调度器（B5.3）消费：
      - parse_quota_body()：envelope → snapshot；容忍加性未知字段，
        容忍 weekly 缺席（§26：lite 套餐实测只有 five_hour 窗）；
      - coerce_percent()：percentage / 剩余比例 → 0..100 的两位小数
        float，任何不可信输入一律 None（不静默造数）；
      - epoch_ms_to_iso()：nextResetTime（epoch 毫秒）→ UTC
        ISO8601 秒精度字符串（"2026-08-28T06:45:02Z" 形式）。

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
    snapshot 的 status 恒为 None——AVAILABLE / PRESSURE / EXHAUSTED /
    UNKNOWN 的评估属于 B5.3 调度器（§28），解析层不判断状态、不猜测。

不透明纪律：
    snapshot 不透出任何原始响应字段（usageDetails / currentValue /
    usage 等一律不透出），不含任何秘密；plan_generation 由条目
    type 词汇推断（任一 CREDIT_LIMIT → "v3"，否则有 TOKENS_LIMIT →
    "v2"，否则 None）——这是套餐代际标签，与判窗无关。

依赖：
    仅 Python 3 标准库（datetime / math），零第三方依赖，
    `python3 -S` 可运行。风格对齐 runtime/state.py /
    runtime/fingerprint.py。

来源：
    docs/glm-conductor-v2-upgrade-guide-final.md §26（V2/V3 解析兼容）、
    §27（snapshot 形状）、§28（状态词汇）、§44（fixture 清单）
    + v2 升级计划工作块 B5.1；实测知识见
    docs/glm-conductor-v2-phase0-runtime-verification.md §2.4。
"""

import math
from datetime import datetime, timedelta, timezone

from runtime.quota.provider import QuotaProviderError

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
        "status": None,  # 恒 None：状态评估属 B5.3 调度器（§28）
        "windows": windows,
    }
