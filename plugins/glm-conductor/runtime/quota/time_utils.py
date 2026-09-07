#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GLM Conductor v2.2.1 quota 时间原语单一规范落点（v2.2.1 WU-221-C2）。

职责：
    承载 quota / continuity 域语义稳定的 ISO-8601 UTC 时间原语
    （解析 / 格式化 / 归一）的单一规范落点（canonical home）：
      - _parse_iso_utc：ISO8601 时刻文本 → aware datetime（UTC）；
      - _format_iso_z：aware datetime → UTC 秒精度 Z 形式串；
      - _normalize_now：now 注入参数归一（None → 当前 UTC；
        datetime / ISO 串 → 时刻；非法 → ValueError）；
      - _utc_now_iso：当前 UTC 时刻 → 毫秒精度 Z 形式串。

    v2.2.1 WU-221-C2 行为保持抽取：前三个函数体自
    runtime.quota.scheduler 逐字移入，_utc_now_iso 自
    runtime.quota.accounting 逐字移入（仅取时刻行按本模块的
    from-import 风格改写，语义逐字等价）；原模块经 re-import 保持
    全部既有解析点（runtime.quota.scheduler._parse_iso_utc /
    _format_iso_z / _normalize_now、runtime.quota.accounting.
    _utc_now_iso，及其余经 scheduler re-export 的消费方）零变化。

    同名近亲（本单元逐实现比对后判定为「非重复、不合并」，各自
    留驻原模块——微差即非重复，错误合并才是失败模式）：
      - runtime.quota.resolver._normalize_now 与
        runtime.quota.report._normalize_now：逻辑同构但 ValueError
        文案锚定各自 API 名（"resolve_quota_status：" /
        "format_duration_delta："），与 scheduler 变体
        （"plan_resume："）非逐字相同，不合并；
      - runtime.quota.resolver._format_iso_ms_z：毫秒精度 Z 形式，
        与秒精度 _format_iso_z 语义不同，不合并。

依赖：
    仅 Python 3.7 标准库（datetime），零第三方依赖，零 I/O；
    不 import 任何 runtime 模块——时间原语是 quota 依赖图的最底层
    （window_math / scheduler / accounting 均可向下依赖本模块，
    本模块绝不反向）。
"""

from datetime import datetime, timezone


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


def _utc_now_iso() -> str:
    """当前 UTC 时刻的 ISO-8601 字符串（毫秒精度 Z 形态，与 permit /
    租约层时间字段同格式）——wave 记录 created_at / closed_at 落盘口径。"""
    moment = datetime.now(timezone.utc)
    return (moment.strftime("%Y-%m-%dT%H:%M:%S")
            + ".%03dZ" % (moment.microsecond // 1000))
