#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GLM Conductor v2.3.0 Global Quota Clock 决策纯函数层（v2.3 计划 §7 +
§4 决策表，unit v23-w2a）。

职责（account 级 Global Quota Clock 的「纯决策」半边）：
    消费 §27 规范形 snapshot（resolve_quota_detail 透出的 windows
    列表），按冻结决策表折算出 clock automation 的下一个目标时刻
    （epoch 毫秒）与决策标签，供接线层经 W1 host adapter
    （runtime.host.zcode_schedule.retime_automation）把 next_run_at
    动态改写为目标值。本模块零 I/O、零时钟依赖、零副作用——不
    import adapter、不读盘、不调用 now()。

冻结决策表（严格序，next_clock_target）：
    1. snapshot 为 None / 非 dict / windows 非列表
           → short_retry（provider_unavailable）
    2. weekly 窗口存在且按 epoch 的 blocked-EXHAUSTED 语义判定为
       耗尽压制 → weekly_park，target=weekly.reset_at+grace
       （weekly_blocked；weekly reset 全不可解析 → 退化 short_retry，
       weekly_reset_unparsable）
    3. five_hour 窗口缺失 → short_retry（five_hour_missing）
    4. B = parse(five_hour.reset_at) 失败
           → short_retry（five_hour_unparsable）
    5. B <= now_ms（reset 已过期：含 §4.1 的「reset 未推进」传播
       延迟场景）→ short_retry（reset_elapsed），target=now+retry_delay
    6. B > now_ms → reset_target，target=B+grace
       （last_reset_at 非 None 且 B != last_reset_at → window_advanced；
       其余 → reconfirm）

架构裁决（2026-09-08，不可违背）：
    clock 是极低成本后台常驻机制——无窗口预算、无限期；额度窗口
    预算 N 是 task 侧概念（v2.2 continuity.max_quota_windows），与本
    决策层零关系：本模块的任何输入 / 输出都不得出现 budget /
    consumed 语义字段。

v2.3.1 增补（unit w1-clock-ux）：Placement UX advisory 纯函数层
    （placement_guidance_for_plan / bind_session_placement /
    cost_advisory_for_model / status_placement_block /
    status_host_block / needs_replacement_from_reasons /
    recovery_guidance_for_replacement / model_is_flash）——plan /
    bind / status 三命令呈现层共用的会话放置引导、成本提示与
    needs_replacement 判定。全部纯文案 / 纯判定（零 I/O、零时钟、
    零阻断）：ZCode 不向插件开放会话探测 / 迁移能力，故
    dedicated_session 恒为 DEDICATED_SESSION_UNVERIFIABLE 常量串
    （绝不伪造 true/false 布尔）、恢复指引恒声明「未执行自动会话
    迁移」；needs_replacement 两极判定（锁定决策 c）：True ⇔ reasons
    含 db_row_missing / db_inspect_failed（automation 缺失 / 不可
    查），target_mismatch / tick_stale / runtime_path_missing 仅
    advisory 不触发；model_is_flash 仅 advisory 用途——绝不作为
    绑定阻断条件，也绝不当作「专用会话」的证据。

weekly blocked 判定的单一真相源：
    复用 runtime.quota.epoch 的阻塞窗语义（status == "EXHAUSTED"，
    canonical_windows 归一；blocking_windows）与 executable boundary
    数学（executable_boundary_at：max(阻塞窗可解析 reset_at)+grace，
    §30），限定在 weekly 窗口子集上调用——绝不重新发明判定。

时间转换：
    epoch / time_utils 现有原语止于 aware datetime 与 Z 形串（无
    ISO→epoch 毫秒助手），故毫秒折算在本模块 _parse_reset_at_ms 内
    实现：解析复用 time_utils._parse_iso_utc（epoch.canonical_windows
    的同一解析器，归一口径一致），误差毫秒级。

纪律：
    - 纯函数：无副作用、无 I/O、不 import runtime.host（adapter）；
    - 输出键集冻结为 CLOCK_TARGET_KEYS（decision 必须 ∈
      CLOCK_DECISIONS）；short_retry / weekly_park 按当前相关窗口
      填 reset_at / reset_at_epoch_ms / window_kind，无关键填 None
      （reset 不可解析时 reset_at / reset_at_epoch_ms 恒 None，不
      透传不可解析原文）；
    - 多个同 kind 窗口（退化输入）取可解析 reset 的最大者——与
      epoch §30 executable boundary 的 max 语义一致：对「下一个目标
      时刻」而言最晚 reset 即相关者；
    - stdlib only；Python 3.7 兼容语法。

来源：v2.3.0 计划 §7（Global Quota Clock）+ §4（决策表），unit
v23-w2a；v2.3.1 Placement UX advisory 增补，unit w1-clock-ux。
"""

import math

from runtime.quota.epoch import (
    blocking_windows,
    canonical_windows,
    executable_boundary_at,
)
from runtime.quota.time_utils import _parse_iso_utc

# —— 词汇表常量 ——

# reset 后的宽限秒数缺省（clock 专属缺省，与 task 侧 v2.2 的 300 秒
# grace 无关——clock 是常驻机制，宽限只防 reset 边界抖动）
DEFAULT_GRACE_SECONDS = 120

# short_retry 的重试间隔缺省（秒）：provider 暂不可用 / reset 已过期
# 等「无确定性目标」场景的固定小步
DEFAULT_RETRY_DELAY_SECONDS = 300

# 决策词汇（冻结三元组）
CLOCK_DECISIONS = ("reset_target", "short_retry", "weekly_park")

# 决策输出 dict 的冻结键集（键名逐字）
CLOCK_TARGET_KEYS = ("decision", "target_epoch_ms", "reason",
                     "reset_at", "reset_at_epoch_ms", "window_kind")

# §27 规范形的窗口 kind 字面量
WINDOW_FIVE_HOUR = "five_hour"
WINDOW_WEEKLY = "weekly"

# 决策 reason 词汇（冻结）。注：决策表第 5 行（B <= now_ms）规格未给
# reason 字面量，"reset_elapsed" 为本单元命名（对齐其余 snake_case
# 词汇），供主会话审查冻结。
REASON_PROVIDER_UNAVAILABLE = "provider_unavailable"
REASON_WEEKLY_BLOCKED = "weekly_blocked"
REASON_WEEKLY_RESET_UNPARSABLE = "weekly_reset_unparsable"
REASON_FIVE_HOUR_MISSING = "five_hour_missing"
REASON_FIVE_HOUR_UNPARSABLE = "five_hour_unparsable"
REASON_RESET_ELAPSED = "reset_elapsed"
REASON_WINDOW_ADVANCED = "window_advanced"
REASON_RECONFIRM = "reconfirm"

# —— v2.3.1 Placement UX advisory 词汇（plan / bind / status 呈现层
# 共用；纯文案与判定，键集冻结，只增不删）——

# plan 输出 placement_guidance 的冻结键集
PLACEMENT_GUIDANCE_KEYS = ("automation_required", "recommended_steps",
                           "suggested_name", "warning")

# bind 输出 session_placement 的冻结键集
SESSION_PLACEMENT_KEYS = ("attachment", "tick_effect", "recommendation",
                          "avoid")

# status 输出 placement 块的冻结键集
STATUS_PLACEMENT_KEYS = ("session_binding", "preferred_model",
                         "dedicated_session")

# status host 块的冻结键集与唯一键值
STATUS_HOST_KEYS = ("adapter",)
HOST_ADAPTER = "zcode_sqlite"

# dedicated_session 的唯一合法值（机械不可验证的显式声明——插件无
# 会话探测能力，恒为字符串，绝不输出布尔）
DEDICATED_SESSION_UNVERIFIABLE = "not_mechanically_verifiable"

# cost_advisory 的 preferred 固定值（advisory 提示，非阻断）
PREFERRED_MODEL_ADVICE = "low-cost Flash-class model"

# 建议的 automation 命名（plan 引导口径）
SUGGESTED_AUTOMATION_NAME = "GLM Conductor · Quota Clock"

# needs_replacement 恢复指引中的无自动迁移声明（逐字冻结）
NO_AUTO_MIGRATION_DECLARATION = "未执行自动会话迁移"

_MS_PER_SECOND = 1000


# —— 参数校验与时间小助手（纯函数） ——

def _validated_seconds(value, arg_name):
    """grace / retry 类秒参数校验：有限数值且 >= 0（bool 拒绝）。

    语义镜像 epoch._validated_grace（合法原样返回，否则中文
    ValueError）；本地实现以免决策层依赖 epoch 的私有助手。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)) \
            or not math.isfinite(value) or value < 0:
        raise ValueError(
            "next_clock_target：%s 必须是 >= 0 的有限数值，得到 %r"
            % (arg_name, value))
    return value


def _validated_now_ms(now_ms):
    """now_ms 校验：有限数值 epoch 毫秒（bool 拒绝），否则中文
    ValueError（风格对齐 zcode_schedule 的 next_run_at 参数闸）。"""
    if isinstance(now_ms, bool) or not isinstance(now_ms, (int, float)) \
            or not math.isfinite(now_ms):
        raise ValueError(
            "next_clock_target：now_ms 必须是有限数值 epoch 毫秒，"
            "得到 %r" % (now_ms,))
    return now_ms


def _parse_reset_at_ms(text):
    """Z 形 ISO 时刻串 → epoch 毫秒（int）；缺失 / 不可解析 → None。

    来源判断：epoch / time_utils 的现有原语止于 aware datetime 与
    Z 形串（无 ISO→epoch 毫秒助手），毫秒折算在本模块实现；解析
    复用 time_utils._parse_iso_utc（epoch.canonical_windows 的同一
    解析器，归一口径一致）；int(round(...)) 抵御浮点亚毫秒误差。"""
    moment = _parse_iso_utc(text)
    if moment is None:
        return None
    return int(round(moment.timestamp() * _MS_PER_SECOND))


def _coerce_epoch_ms(value):
    """last_reset_at 容错归一为 epoch 毫秒；无法归一 → None。

    int / float 原样采信；str 按 _parse_reset_at_ms 解析（兼容接线
    层存 Z 形串的形态）；其余（含 bool / None）→ None——decision
    落 reconfirm 分支（last_reset_at 未提供的既定语义）。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        return _parse_reset_at_ms(value)
    return None


def _windows_of(snapshot):
    """snapshot → windows 列表；snapshot 非 dict 或 windows 非列表 →
    None（决策表第 1 行 provider_unavailable 的信号）。"""
    if not isinstance(snapshot, dict):
        return None
    windows = snapshot.get("windows")
    return windows if isinstance(windows, list) else None


def _target(decision, target_epoch_ms, reason, reset_at=None,
            reset_at_epoch_ms=None, window_kind=None):
    """装配 CLOCK_TARGET_KEYS 冻结键集的决策 dict（纯装配）。"""
    return {
        "decision": decision,
        "target_epoch_ms": target_epoch_ms,
        "reason": reason,
        "reset_at": reset_at,
        "reset_at_epoch_ms": reset_at_epoch_ms,
        "window_kind": window_kind,
    }


def _short_retry(now_ms, retry_delay_ms, reason, window_kind=None):
    """short_retry 统一形态：target = now + retry_delay（无确定性
    目标时刻的固定小步）。"""
    return _target("short_retry", int(now_ms + retry_delay_ms), reason,
                   window_kind=window_kind)


def _max_parseable_reset(canonical_subset):
    """canonical 窗口子集 → (datetime, Z 形串)：可解析 reset 的最大者。

    与 epoch §30 executable boundary 同一 max 口径（退化输入的多个
    同 kind 窗口取最晚 reset）；全不可解析 → None。"""
    parseable = []
    for window in canonical_subset:
        moment = _parse_iso_utc(window["reset_at"])
        if moment is not None:
            parseable.append((moment, window["reset_at"]))
    if not parseable:
        return None
    return max(parseable, key=lambda pair: pair[0])


# —— 公开 API（纯决策） ——

def select_five_hour_window(snapshot):
    """取 snapshot windows 中首个 kind=="five_hour" 的窗口 dict。

    无 five_hour 窗口 / snapshot 非 dict / windows 非列表 → None。
    顺序敏感取首个（§27 规范形每快照恰一个 five_hour 窗；退化输入
    下首个即确定性结果）。返回原始窗口 dict（不拷贝、不归一）。"""
    windows = _windows_of(snapshot)
    if windows is None:
        return None
    for item in windows:
        if isinstance(item, dict) and item.get("kind") == WINDOW_FIVE_HOUR:
            return item
    return None


def next_clock_target(snapshot, *, now_ms, last_reset_at=None,
                      grace_seconds=DEFAULT_GRACE_SECONDS,
                      retry_delay_seconds=DEFAULT_RETRY_DELAY_SECONDS):
    """冻结决策表：snapshot → clock 下一个目标时刻（模块 docstring
    有决策表全文与编号，以下仅列参数 / 输出契约）。

    参数：
      - snapshot：§27 规范形 dict（resolver.resolve_quota_detail 透出
        的 snapshot；{"windows": [...]}）；None / 非 dict / windows
        非列表 → 决策 1（provider_unavailable）；
      - now_ms：当前时刻 epoch 毫秒（有限数值，bool 拒绝，非法
        ValueError——先于任何决策）；
      - last_reset_at：上次 tick 观测到的 five_hour reset（epoch 毫秒
        int，或同刻 Z 形串——容错归一；None / 无法归一 → 视为未
        提供）：reset_target 时与本次 B 比较，不等 → window_advanced，
        相等或未提供 → reconfirm；
      - grace_seconds：目标时刻 = reset + grace（秒，缺省 120）；
      - retry_delay_seconds：short_retry 的固定小步（秒，缺省 300）。
      grace_seconds / retry_delay_seconds 非有限数值或负数 →
      ValueError（中文，镜像 epoch._validated_grace）。

    返回（键集冻结为 CLOCK_TARGET_KEYS）：
      {"decision": <CLOCK_DECISIONS 之一>,
       "target_epoch_ms": <int epoch 毫秒>,
       "reason": <reason 词汇>,
       "reset_at": <当前相关窗口的 Z 形 reset 串或 None>,
       "reset_at_epoch_ms": <同上的 epoch 毫秒或 None>,
       "window_kind": <"five_hour" / "weekly" 或 None>}
    """
    _validated_now_ms(now_ms)
    _validated_seconds(grace_seconds, "grace_seconds")
    _validated_seconds(retry_delay_seconds, "retry_delay_seconds")
    grace_ms = int(round(grace_seconds * _MS_PER_SECOND))
    retry_delay_ms = int(round(retry_delay_seconds * _MS_PER_SECOND))

    # —— 决策 1：snapshot / windows 形状闸 ——
    windows = _windows_of(snapshot)
    if windows is None:
        return _short_retry(now_ms, retry_delay_ms,
                            REASON_PROVIDER_UNAVAILABLE)
    canonical = canonical_windows(windows)

    # —— 决策 2：weekly blocked-EXHAUSTED（epoch 语义复用） ——
    # blocking_windows / executable_boundary_at 即 epoch 的阻塞窗判定
    # 与 executable boundary（max(阻塞窗可解析 reset)+grace，§30），
    # 限定 weekly 子集调用；weekly 存在但不 EXHAUSTED 不触发 park。
    weekly = [window for window in canonical
              if window["kind"] == WINDOW_WEEKLY]
    if weekly:
        if blocking_windows(weekly):
            boundary = executable_boundary_at(
                weekly, grace_seconds=grace_seconds)
            if boundary is None:
                # 有阻塞 weekly 窗但 reset 全不可解析（§31 不虚构）：
                # 退化 short_retry
                return _short_retry(now_ms, retry_delay_ms,
                                    REASON_WEEKLY_RESET_UNPARSABLE,
                                    window_kind=WINDOW_WEEKLY)
            # boundary 恒为 epoch._format_iso_z 产物，解析不会失败
            target_ms = _parse_reset_at_ms(boundary)
            chosen = _max_parseable_reset(blocking_windows(weekly))
            return _target(
                "weekly_park", int(target_ms), REASON_WEEKLY_BLOCKED,
                chosen[1], _parse_reset_at_ms(chosen[1]), WINDOW_WEEKLY)

    # —— 决策 3 / 4：five_hour 缺失 / reset 不可解析 ——
    five_hour = [window for window in canonical
                 if window["kind"] == WINDOW_FIVE_HOUR]
    if not five_hour:
        return _short_retry(now_ms, retry_delay_ms,
                            REASON_FIVE_HOUR_MISSING)
    chosen = _max_parseable_reset(five_hour)
    if chosen is None:
        return _short_retry(now_ms, retry_delay_ms,
                            REASON_FIVE_HOUR_UNPARSABLE,
                            window_kind=WINDOW_FIVE_HOUR)

    # —— 决策 5 / 6：reset 已过期 vs reset_target ——
    reset_at, reset_ms = chosen[1], _parse_reset_at_ms(chosen[1])
    if reset_ms <= now_ms:
        # 含 §4.1 的「reset 未推进」传播延迟：provider 尚未滚动快照，
        # 固定小步重试等刷新
        return _target("short_retry", int(now_ms + retry_delay_ms),
                       REASON_RESET_ELAPSED, reset_at, reset_ms,
                       WINDOW_FIVE_HOUR)
    previous_ms = _coerce_epoch_ms(last_reset_at)
    reason = (REASON_WINDOW_ADVANCED
              if previous_ms is not None and previous_ms != reset_ms
              else REASON_RECONFIRM)
    return _target("reset_target", int(reset_ms + grace_ms), reason,
                   reset_at, reset_ms, WINDOW_FIVE_HOUR)


# —— v2.3.1 公开 API（Placement UX advisory，纯文案与判定） ——

def model_is_flash(model):
    """automation 模型名是否 Flash 类（判定口径与 bind 输出一致：
    模型名含 "Flash"；非 str 含 None → False）。

    仅 advisory 用途：绝不作为绑定阻断条件，也绝不当作「专用会话」
    的证据（ZCode 不向插件开放会话探测能力）。"""
    return isinstance(model, str) and "Flash" in model


def placement_guidance_for_plan():
    """quota-clock-plan 的会话放置引导（键集冻结为
    PLACEMENT_GUIDANCE_KEYS）。

    语义冻结：需先创建一个持久 recurring ZCode automation 作为 tick
    载体；建议 1) 新建独立 ZCode 会话 2) 用低成本 Flash 模型
    3) automation 可命名为 SUGGESTED_AUTOMATION_NAME 4) 在该会话内
    执行 clock 设置；警示：除非有意如此，不要在主编码会话创建
    Global Quota Clock（周期 tick 会注入该会话）。纯 advisory 文案，
    不阻断、不探测会话。"""
    return {
        "automation_required": (
            "quota-clock 需要先创建一个持久的 recurring ZCode "
            "automation 作为周期 tick 载体（创建前请先决定放置会话）"),
        "recommended_steps": [
            "新建一个独立的 ZCode 交互会话（勿用主编码会话）",
            "在该会话选用低成本 Flash 类模型"
            "（automation 的 model 即发起会话的模型）",
            "automation 建议命名为 \"%s\"" % SUGGESTED_AUTOMATION_NAME,
            "在该专用会话内执行 quota-clock-bind 完成 clock 设置",
        ],
        "suggested_name": SUGGESTED_AUTOMATION_NAME,
        "warning": (
            "除非有意如此，不要在主编码会话创建 Global Quota "
            "Clock——绑定后周期 quota-clock tick 会注入该会话"),
    }


def bind_session_placement():
    """quota-clock-bind 的 SESSION PLACEMENT 说明（键集冻结为
    SESSION_PLACEMENT_KEYS）。

    语义冻结：本 automation 保持附着在当前 ZCode 会话（插件不具备
    会话迁移能力）；周期 quota-clock tick 将出现在本对话中；建议
    专用低成本 Flash 会话；避免绑定到主编排 / 编码会话。"""
    return {
        "attachment": (
            "本 automation 保持附着在当前 ZCode 会话（插件不具备会话"
            "迁移能力）"),
        "tick_effect": "周期 quota-clock tick 将出现在本对话中",
        "recommendation": "建议使用专用低成本 Flash 会话执行绑定",
        "avoid": "避免绑定到主编排 / 编码会话（tick 会周期性注入打断）",
    }


def cost_advisory_for_model(model):
    """automation 模型非 Flash 类时的成本提示；Flash 类 → None。

    输出键冻结为 {current_model, preferred}（preferred 恒为
    PREFERRED_MODEL_ADVICE）。advisory 性质：仅提示模型档位偏好，
    绝不阻断绑定（非 Flash 照常 bind 成功）。"""
    if model_is_flash(model):
        return None
    return {"current_model": model, "preferred": PREFERRED_MODEL_ADVICE}


def status_placement_block(automation_model, *, automation_row_available):
    """quota-clock-status 的 placement 诊断块（键集冻结为
    STATUS_PLACEMENT_KEYS）。

    session_binding：automation 行可查（inspect 成功且行存在）→
    "active"；行缺失 / 不可查 → "unavailable"。preferred_model：模型
    名含 "Flash"（advisory 口径，绝不是「专用会话」的证据）。
    dedicated_session：恒为 DEDICATED_SESSION_UNVERIFIABLE 常量串——
    插件无法机械验证会话专用性，绝不伪造 true/false 布尔。"""
    return {
        "session_binding": ("active" if automation_row_available
                            else "unavailable"),
        "preferred_model": model_is_flash(automation_model),
        "dedicated_session": DEDICATED_SESSION_UNVERIFIABLE,
    }


def status_host_block():
    """quota-clock-status 的 host 标识块（键集冻结为
    STATUS_HOST_KEYS：adapter=zcode_sqlite）。"""
    return {"adapter": HOST_ADAPTER}


def needs_replacement_from_reasons(reasons):
    """status reasons → needs_replacement 判定（锁定决策 c 两极）。

    True ⇔ reasons 含 db_row_missing 或 db_inspect_failed 前缀 token
    （automation 缺失 / 不可查——clock 无法继续服务的硬故障）；
    target_mismatch / tick_stale / runtime_path_missing 仅 advisory，
    绝不触发。reasons 非 list / 元素非 str 一律按不触发处理（诊断面
    容错，绝不抛）。"""
    if not isinstance(reasons, list):
        return False
    for item in reasons:
        if isinstance(item, str) and (
                item == "db_row_missing"
                or item.startswith("db_inspect_failed")):
            return True
    return False


def recovery_guidance_for_replacement():
    """needs_replacement=true 的恢复指引（advisory 文案）。

    语义冻结：在专用低成本 Flash 交互会话中显式执行 replace/rebind
    （quota-clock-bind <new_automation_id>——w35-dogfood-fix 起 bind
    对已确认失效的死绑定自动检测并替换：宿主 DB 行缺失时放行改绑，
    用户仍显式提供新 automation_id）+ 「未执行自动会话迁移」声明
    ——ZCode 不向插件开放会话迁移能力，本插件永远不做隐式迁移
    / 自动 rebind。"""
    return ("replace: run quota-clock-bind <new_automation_id> from a "
            "fresh dedicated low-cost Flash interactive session；"
            "bind auto-detects a verified dead binding (missing host "
            "DB row) and replaces it, while you still provide the new "
            "automation_id；"
            + NO_AUTO_MIGRATION_DECLARATION
            + "（本插件不做隐式迁移或自动 rebind）")
