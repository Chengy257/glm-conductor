#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GLM Conductor v2.2 execution quota phase 纯决策器（v2.2 M2，wu-22-02）。

职责（主计划 §17）：
    消费 provider 四态（runtime.quota.scheduler.evaluate 的输出 status）
    + §27 snapshot 的 windows 列表 + quota_control 阈值 + task 侧
    continuation 状态，机械折算出单条执行相决策：
        evaluate_task_quota_phase(...) →
          {"provider_status", "execution_phase", "dispatch_budget",
           "allow_new_wave", "allow_finish_current",
           "continuation_obligation", "wake_at", "reason"}
    （§17.1 冻结 8 键，键名逐字；reason 为中文一句话。）

双层模型（决策记录 D7 冻结映射，provider_status ≠ execution_phase；
边界含等号，规则按序短路）：
    1  provider EXHAUSTED 或任一窗 remaining == 0         → BLOCKED
    2  最小 remaining <= draining_percent（默认 20）       → DRAINING
       （即使 provider 报 AVAILABLE——v2.1 的 24% 误判即此条封堵）
    3  draining < 最小 remaining <= pressure_percent（35） → PRESSURE
       （execution 相语义，与 provider 维度的 PRESSURE 严格区分）
    4  provider UNKNOWN / 无可数值评估的窗（列表空/全坏）  → PRESSURE
       （fail-open：预算 1 不阻塞，wake_at=None，绝不虚构唤醒时刻）
    5  其余                                                → NORMAL
两个 PRESSURE 在命名上不混用简称：provider_status 的 PRESSURE 是
provider 维度词汇（QUOTA_STATUSES），execution_phase 的 PRESSURE 是
execution 维度词汇（EXECUTION_PHASES）。

派发预算与白名单（§18/§18.1）：
    NORMAL   → dispatch_budget = max_workers，可开新实施波次；
    PRESSURE → 预算 1，可开新波次；
    DRAINING → 预算 0，禁新实施波次，仅允许收尾（join/verify/review/
               checkpoint/wake 等 §18.1 白名单动作）；
    BLOCKED  → 预算 0，新波次与收尾一律冻结。

continuation_obligation（§8.3，只产出 runtime.state 词汇子集）：
    NORMAL   → none
    PRESSURE → bridge armed → armed；否则 task_active 且数值评估窗中
               存在非空 reset_at → wake_required；否则 none
    DRAINING → bridge armed → armed；否则 wake_required
    BLOCKED  → bridge ∈ {armed, fired} → waiting_quota（runtime.state
               TASK_STATUSES 的任务级「等待额度」态）；否则 degraded

纪律（本模块的硬边界）：
    - 纯决策：零网络、零文件 I/O、不写 state、不建 automation、
      无时间依赖（now 不入参；wake_at 只做 reset_at + grace 的时间
      数学，格式照 scheduler 的 Z 形式 ISO8601 纪律）；
    - fail-open（D7 第 4 条）：provider UNKNOWN / 窗口数据不可得 →
      收敛到 PRESSURE 且 wake_at=None，不阻塞、不虚构；
    - 阈值默认值单一真相源：从 runtime.execution_policy 导入
      DEFAULT_QUOTA_CONTROL（D5），不复制数值字面量；宽限秒数默认从
      runtime.quota.scheduler 导入 DEFAULT_GRACE_SECONDS，数值/时间
      工具复用其私有实现（_is_number / _parse_iso_utc / _format_iso_z
      / _fmt_number），不改 scheduler.py；
    - wake_bridge 词汇按 quota 包既有纪律不 import runtime.state，
      以字面量声明并在注释指向 runtime.state.WAKE_BRIDGE_STATUSES
      （§8.2 冻结十值），词汇一致性由 tests.test_quota_control 的
      同步测试锚定。

依赖：
    仅 Python 3.7 标准库 + 上述 runtime 常量/工具；导入方向
    runtime.quota.control → runtime.execution_policy →
    runtime.quota.parser（parser 不反向依赖，无循环导入）。

来源：
    docs/history/v2.2/GLM-Conductor-v2.2-Quota-Continuity-Control-Loop-Implementation-
    Plan.md §4.2（execution phase 四态）/ §5.1（固定阈值）/ §8.3
    （obligation 规则）/ §17.1（返回 dict 形状）/ §18/§18.1（预算与
    白名单）/ WU-22-02；
    docs/history/v2.2/GLM-Conductor-v2.2-实施前缺口探查与设计决策记录.md D5
    （阈值配置落点）/ D7（双层映射冻结）。
"""

from datetime import timedelta

from runtime.execution_policy import DEFAULT_QUOTA_CONTROL
from runtime.quota.parser import QUOTA_STATUSES
from runtime.quota.scheduler import (
    DEFAULT_GRACE_SECONDS,
    _fmt_number,
    _format_iso_z,
    _is_number,
    _parse_iso_utc,
)
# v2.2.1 WU-221-C2（行为保持抽取）：_earliest_reset_plus 的规范定义
# 已移至 runtime.quota.window_math（probe 边界数学：min(可解析 reset)
# + grace，2+ 模块共用——本模块与 runtime.quota.epoch）；本行 re-import
# 保持既有解析点（含 epoch 的 `from runtime.quota.control import
# _earliest_reset_plus`）零变化。
from runtime.quota.window_math import _earliest_reset_plus

# —— 词汇表常量 ——

# §4.2 execution phase 四态（与 provider 维度 QUOTA_STATUSES 是不同
# 维度的词汇，命名与 reason 中不混用简称）
EXECUTION_PHASES = ("NORMAL", "PRESSURE", "DRAINING", "BLOCKED")

# §8.2 wake_bridge.status 冻结十值（词汇源：runtime.state.
# WAKE_BRIDGE_STATUSES；quota/* 包纪律不 import runtime.state，
# 字面量一致性由 tests.test_quota_control 的同步测试锚定）
_WAKE_BRIDGE_STATUSES = (
    "none", "requested", "armed", "fired", "retarget_required",
    "degraded", "paused", "cancelled", "stale", "failed")

# §8.3 BLOCKED 分支承认「bridge 已建置」的 wake_bridge.status 子集
_ARMED_LIKE_BRIDGES = ("armed", "fired")

# §8.3 BLOCKED 且 bridge 已建置时的义务词：runtime.state.TASK_STATUSES
# 的 waiting_quota（任务级「等待额度」态；跨词汇的机械映射，不在
# runtime.state.CONTINUATION_OBLIGATIONS 六值内）
_WAITING_QUOTA = "waiting_quota"


# —— 内部小助手（纯函数） ——

def _kind_text(windows):
    """把窗口清单的 kind 拼为中文顿号清单（如 "five_hour、weekly"）。"""
    names = []
    for window in windows:
        name = window.get("kind")
        names.append(str(name) if name else "窗口")
    return "、".join(names) if names else "窗口"


def _merge_quota_control(quota_control):
    """键级合并 quota_control 入参与冻结默认（单一真相源 DEFAULT_QUOTA_CONTROL）。

    None → 全默认；非 dict → ValueError；pressure_percent /
    draining_percent 缺键按默认，存在则必须是有限数值（bool 拒绝，
    风格照 scheduler.evaluate），否则 ValueError；未知键忽略。
    返回 {"pressure_percent", "draining_percent"} 全新 dict（不波及
    入参与 DEFAULT_QUOTA_CONTROL 常量）。
    """
    merged = {
        "pressure_percent": DEFAULT_QUOTA_CONTROL["pressure_percent"],
        "draining_percent": DEFAULT_QUOTA_CONTROL["draining_percent"],
    }
    if quota_control is None:
        return merged
    if not isinstance(quota_control, dict):
        raise ValueError(
            "evaluate_task_quota_phase：quota_control 必须是 JSON 对象或"
            " None，得到 %s" % type(quota_control).__name__)
    for key in ("pressure_percent", "draining_percent"):
        if key not in quota_control:
            continue
        value = quota_control[key]
        if not _is_number(value):
            raise ValueError(
                "evaluate_task_quota_phase：quota_control.%s 必须是有限"
                "数值，得到 %r" % (key, value))
        merged[key] = value
    return merged


# —— §17.1 核心 API ——

def evaluate_task_quota_phase(*, provider_status, windows,
                              quota_control=None, max_workers=None,
                              task_active=True, wake_bridge_status="none",
                              grace_seconds=DEFAULT_GRACE_SECONDS):
    """按 D7 冻结映射 + §8.3 义务规则产出执行相决策（纯函数，零 I/O）。

    参数（全 keyword-only）：
      - provider_status：provider 维度四态字符串（QUOTA_STATUSES：
        AVAILABLE / PRESSURE / EXHAUSTED / UNKNOWN，通常取
        scheduler.evaluate(snapshot)["status"]）；非法值 ValueError；
      - windows：§27 snapshot 的 windows 列表（逐窗 dict，消费
        kind / remaining_percent / reset_at）；坏窗（非 dict 或缺
        数值型 remaining_percent）跳过并在 reason 注明；非 list 按
        空窗口处理（fail-open）；
      - quota_control：可选 {"pressure_percent", "draining_percent"}，
        缺省 None → 全按 DEFAULT_QUOTA_CONTROL（D5：35 / 20）；键级
        缺省合并；合并后须满足 0 <= draining_percent < pressure_percent
        <= 100，违例 ValueError；
      - max_workers：NORMAL 相预算（int）；None → 按 1 保守；非 int
        或 < 1 → ValueError；
      - task_active：任务是否在执行态（§8.3 PRESSURE 义务判定输入）；
      - wake_bridge_status：wake_bridge.status 十值词汇（§8.2），
        缺省 none；非法 ValueError；
      - grace_seconds：wake_at 宽限秒数，缺省 DEFAULT_GRACE_SECONDS
        （300）；负数 / 非有限数值 → ValueError。

    决策规则（D7 冻结映射，边界含等号，按序短路——详见模块 docstring）：
      1 EXHAUSTED provider 或任一窗 remaining == 0 → BLOCKED；
      2 最小 remaining <= draining_percent → DRAINING（即使 provider
        报 AVAILABLE）；
      3 draining < 最小 remaining <= pressure_percent → PRESSURE；
      4 provider UNKNOWN 或无可数值评估的窗 → PRESSURE（fail-open，
        wake_at=None）；
      5 其余 → NORMAL。

    wake_at：phase != NORMAL 且未走 fail-open 时，取「达到该 phase
    判定标准的相关窗」（BLOCKED = 归零窗，provider 单独耗尽时回退
    全部数值窗；DRAINING / PRESSURE = 落入对应阈值带的窗）中最早可
    解析 reset_at + grace_seconds 的 Z 形式 ISO 串；无 → None。

    返回（§17.1 冻结 8 键，键名逐字）：
      {"provider_status": <入参回显>,
       "execution_phase": <EXECUTION_PHASES 之一>,
       "dispatch_budget": <int>,
       "allow_new_wave": <bool>,
       "allow_finish_current": <bool>,
       "continuation_obligation": <§8.3 词汇>,
       "wake_at": <Z 形式串或 None>,
       "reason": <中文一句话>}
    """
    # —— 参数校验（先全量校验再决策，中文 ValueError，风格照 scheduler） ——
    if provider_status not in QUOTA_STATUSES:
        raise ValueError(
            "evaluate_task_quota_phase：provider_status %r 不在合法取值内"
            "（%s）" % (provider_status, ", ".join(QUOTA_STATUSES)))
    control = _merge_quota_control(quota_control)
    draining = float(control["draining_percent"])
    pressure = float(control["pressure_percent"])
    if not (0 <= draining < pressure <= 100):
        raise ValueError(
            "evaluate_task_quota_phase：quota_control 必须满足"
            " 0 <= draining_percent < pressure_percent <= 100，得到"
            " draining_percent=%r、pressure_percent=%r"
            % (control["draining_percent"], control["pressure_percent"]))
    if max_workers is None:
        workers = 1  # 缺省按 1 保守（§18 预算缺省）
    elif (isinstance(max_workers, bool) or not isinstance(max_workers, int)
          or max_workers < 1):
        raise ValueError(
            "evaluate_task_quota_phase：max_workers 必须是 >= 1 的整数"
            "（None 表示按 1 保守），得到 %r" % (max_workers,))
    else:
        workers = max_workers
    if wake_bridge_status not in _WAKE_BRIDGE_STATUSES:
        raise ValueError(
            "evaluate_task_quota_phase：wake_bridge_status %r 不在合法"
            "取值内（%s）"
            % (wake_bridge_status, ", ".join(_WAKE_BRIDGE_STATUSES)))
    if not _is_number(grace_seconds) or grace_seconds < 0:
        raise ValueError(
            "evaluate_task_quota_phase：grace_seconds 必须是 >= 0 的有限"
            "数值，得到 %r" % (grace_seconds,))
    grace_delta = timedelta(seconds=float(grace_seconds))
    active = bool(task_active)

    # —— 窗口分析：只统计数值型 remaining_percent，坏窗跳过并记账 ——
    shape_invalid = not isinstance(windows, list)
    numeric = []
    bad_count = 0
    for item in (windows if isinstance(windows, list) else []):
        if (not isinstance(item, dict)
                or not _is_number(item.get("remaining_percent"))):
            bad_count += 1
            continue
        numeric.append({
            "kind": item.get("kind"),
            "remaining": float(item["remaining_percent"]),
            "reset_at": item.get("reset_at"),
        })
    min_remaining = min(
        (entry["remaining"] for entry in numeric), default=None)
    zero_windows = [e for e in numeric if e["remaining"] <= 0]
    draining_windows = [e for e in numeric if e["remaining"] <= draining]
    pressure_windows = [
        e for e in numeric if draining < e["remaining"] <= pressure]

    # —— D7 冻结映射（规则按序短路，边界含等号） ——
    failopen = False
    if provider_status == "EXHAUSTED" or zero_windows:
        phase = "BLOCKED"
    elif draining_windows:
        phase = "DRAINING"
    elif pressure_windows:
        phase = "PRESSURE"
    elif provider_status == "UNKNOWN" or not numeric:
        phase = "PRESSURE"
        failopen = True  # D7 第 4 条：fail-open，不阻塞、不虚构唤醒
    else:
        phase = "NORMAL"

    # —— §18 / §18.1 预算与白名单 ——
    if phase == "NORMAL":
        budget, allow_new_wave, allow_finish_current = workers, True, True
    elif phase == "PRESSURE":
        budget, allow_new_wave, allow_finish_current = 1, True, True
    elif phase == "DRAINING":
        budget, allow_new_wave, allow_finish_current = 0, False, True
    else:  # BLOCKED
        budget, allow_new_wave, allow_finish_current = 0, False, False

    # —— §8.3 continuation obligation（只产出 runtime.state 词汇子集） ——
    armed_bridge = wake_bridge_status == "armed"
    reset_known = any(entry["reset_at"] for entry in numeric)
    if phase == "NORMAL":
        obligation = "none"
    elif phase == "PRESSURE":
        if armed_bridge:
            obligation = "armed"
        elif active and reset_known:
            obligation = "wake_required"
        else:
            obligation = "none"
    elif phase == "DRAINING":
        obligation = "armed" if armed_bridge else "wake_required"
    elif wake_bridge_status in _ARMED_LIKE_BRIDGES:
        obligation = _WAITING_QUOTA
    else:
        obligation = "degraded"

    # —— wake_at：相关窗（达到该 phase 判定标准的窗）最早 reset + grace ——
    if phase == "BLOCKED":
        relevant = zero_windows or numeric  # provider 单独耗尽 → 全部数值窗
    elif phase == "DRAINING":
        relevant = draining_windows
    elif phase == "PRESSURE":
        relevant = [] if failopen else pressure_windows
    else:
        relevant = []
    wake_at = _earliest_reset_plus(relevant, grace_delta)

    # —— 中文一句话 reason ——
    if phase == "NORMAL":
        base = ("额度余量充足（最小剩余 %s%%）：NORMAL，按预算 %s 派发"
                "新实施波次"
                % (_fmt_number(min_remaining), _fmt_number(workers)))
    elif phase == "DRAINING":
        base = ("%s 剩余 %s%% 已跌至排水线（不高于排水阈值 %s%%）："
                "DRAINING，禁止新实施波次，仅允许收尾（join/verify/"
                "review/checkpoint/wake）"
                % (_kind_text(draining_windows), _fmt_number(min_remaining),
                   _fmt_number(draining)))
    elif phase == "PRESSURE" and not failopen:
        base = ("%s 剩余 %s%% 落入执行压力带（高于排水阈值 %s%% 且不高于"
                "压力阈值 %s%%）：PRESSURE（execution 相语义），预算收缩为 1"
                % (_kind_text(pressure_windows), _fmt_number(min_remaining),
                   _fmt_number(draining), _fmt_number(pressure)))
    elif phase == "PRESSURE":
        base = ("provider 状态未知或无可数值评估的窗口（fail-open）：按 "
                "PRESSURE 保守收敛，预算 1 不阻塞派发，不虚构唤醒时刻")
    elif zero_windows:
        base = ("%s 剩余已归零：BLOCKED，派发与收尾一律冻结，等待额度重置"
                % _kind_text(zero_windows))
    else:
        base = ("provider 报告额度已耗尽（EXHAUSTED）：BLOCKED，派发与"
                "收尾一律冻结，等待额度重置")
    reason = base
    if wake_at is not None:
        reason += "；最早重置 %s（reset_at 加 %s 秒宽限）" % (
            wake_at, _fmt_number(grace_seconds))
    if bad_count:
        reason += "；跳过 %d 个无法数值评估的坏窗" % bad_count
    if shape_invalid:
        reason += "；windows 形状非法按空窗口处理"

    return {
        "provider_status": provider_status,
        "execution_phase": phase,
        "dispatch_budget": budget,
        "allow_new_wave": allow_new_wave,
        "allow_finish_current": allow_finish_current,
        "continuation_obligation": obligation,
        "wake_at": wake_at,
        "reason": reason,
    }
