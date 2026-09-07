#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""runtime.quota.observer 单元测试（v2.2 M3，wu-22-03）。

锚定对象：主计划 §6.2（自适应心跳周期）/ §6.4（lazy heartbeat：无
daemon、next_check_at 按需推导）/ §16.1（观测 dict 形状）/ §16.2
（Observer 不做什么）/ §22.1（CLI 输出）；WU-22-03（adaptive
interval / event-triggered refresh / lazy heartbeat / next_check_at /
禁 daemon）。

运行：
    cd <repo_root> && python3 -m unittest tests.test_quota_observer -v

全部离线：observer 三函数为纯决策（零 I/O、零墙钟——now 一律入参）；
resolver 明细与 CLI 用例的缓存文件落在 tempfile.TemporaryDirectory
的 scratch 目录（绝不触碰仓库内 .glm-conductor/ 真实账本），凭证 /
provider 经 monkeypatch 注入（零真实网络，注入面与
tests/test_quota_resolver 同款）。

覆盖映射：
    1  next_check_interval：自适应表四相（1800/600/300/300）+
       reset_at-grace 收紧（取 min）+ 边界已过 / 紧贴的 60 秒下限 +
       未知 / 不可解析 reset 不收紧 + 参数校验（now 必填、非法相、
       负 grace）+ datetime / ISO 串 / naive 时刻口径
    2  should_refresh：边界含等号（now == next_check_at → True）/
       之前 False / 之后 True / None → True / 不可解析 → True /
       now 非法 ValueError
    3  observe：§16.1 冻结八键形状；决策键（provider_status /
       execution_phase / reason）与 control 直调逐字一致（不重复
       实现映射的锚）；最小剩余窗透传（含并列取先 / 类型保持）与
       缺失 None；wake_recommended / wake_required 全分支；next_
       check_at 推进（now=None → None 的语义选择、reset-grace 取早、
       now+interval）；参数校验透传
    4  resolve_quota_detail：四 source 路径（cache_fresh / provider /
       cache_stale / none）+ snapshot 形状 + fetched_at 语义 + 四键
       冻结 + 与 resolve_quota_status 逐用例同源一致 + 损坏缓存 none
    5  CLI quota-observe / quota-phase：happy path（无任务保守默认 /
       真实任务 state 装配：policy max_workers、continuation bridge、
       执行态族 task_active）+ 任务缺失 1 + 用法错 2 + 真实 resolver
       接线（无缓存无凭证 → UNKNOWN fail-open）+ ensure_ascii=False
       原文输出
"""

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "glm-conductor"))
from runtime import cli, execution_policy, state
from runtime.quota import control, observer
from runtime.quota import resolver as quota_resolver

# —— 常量与装置 ——

NOW = datetime(2026, 8, 31, 5, 0, 0, tzinfo=timezone.utc)
NOW_ISO = "2026-08-31T05:00:00Z"

# §16.1 冻结八键（键名逐字；任务包记「十键」但 §16.1 文档与接口枚举
# 均为这八键——以枚举为准，见 test_frozen_eight_keys）
FROZEN_OBSERVER_KEYS = {
    "provider_status", "execution_phase", "remaining_percent",
    "reset_at", "next_check_at", "reason", "wake_recommended",
    "wake_required",
}

# §17.1 冻结 8 键（control 决策 dict，quota-phase 直出形状）
FROZEN_DECISION_KEYS = {
    "provider_status", "execution_phase", "dispatch_budget",
    "allow_new_wave", "allow_finish_current", "continuation_obligation",
    "wake_at", "reason",
}


def win(kind, remaining=None, reset_at=None):
    """构造 §27 形状的单窗口 dict（used_percent 对决策层无意义，仅保形）。"""
    return {"kind": kind, "used_percent": None,
            "remaining_percent": remaining, "reset_at": reset_at}


def observe(windows, provider_status="AVAILABLE", **overrides):
    """observe 的短封装（provider 默认 AVAILABLE）。"""
    return observer.observe(provider_status=provider_status,
                            windows=windows, **overrides)


# —— 1：next_check_interval（自适应表 + reset 收紧 + 下限） ——

class NextCheckIntervalTest(unittest.TestCase):

    def test_adaptive_table_four_phases(self):
        """§6.2 / WU-22-03 冻结表：NORMAL 1800 / PRESSURE 600 /
        DRAINING 300 / BLOCKED 300（reset 未知时）。"""
        for phase, seconds in (("NORMAL", 1800), ("PRESSURE", 600),
                               ("DRAINING", 300), ("BLOCKED", 300)):
            self.assertEqual(
                observer.next_check_interval(execution_phase=phase,
                                             now=NOW),
                seconds, phase)
        self.assertEqual(observer.ADAPTIVE_INTERVAL_SECONDS,
                         {"NORMAL": 1800, "PRESSURE": 600,
                          "DRAINING": 300, "BLOCKED": 300})

    def test_table_keys_match_execution_phases(self):
        """间隔表键集与 control.EXECUTION_PHASES 一一对应（无漏相）。"""
        self.assertEqual(sorted(observer.ADAPTIVE_INTERVAL_SECONDS),
                         sorted(control.EXECUTION_PHASES))

    def test_reset_tightening_caps_interval(self):
        """已知 reset：NORMAL 1800 但 reset-grace 只剩 600 秒 → 600
        （已知 reset 时不得晚于边界前 grace 秒）。"""
        result = observer.next_check_interval(
            execution_phase="NORMAL", reset_at="2026-08-31T05:15:00Z",
            now=NOW)  # deadline = 05:10:00 → 距 now 600s
        self.assertEqual(result, 600)

    def test_reset_tightening_beats_pressure_table(self):
        """PRESSURE 600 但 deadline 只剩 200 秒 → 200（取 min）。"""
        result = observer.next_check_interval(
            execution_phase="PRESSURE", reset_at="2026-08-31T05:08:20Z",
            now=NOW)  # deadline = 05:03:20 → 距 now 200s
        self.assertEqual(result, 200)

    def test_deadline_passed_floors_at_60(self):
        """reset-grace 边界已过 → 负收紧 → 下限 60（lazy heartbeat
        最小节奏，不因时刻已过而停摆）。"""
        result = observer.next_check_interval(
            execution_phase="DRAINING", reset_at="2026-08-31T05:00:10Z",
            now=NOW)  # deadline = 04:55:10 已过
        self.assertEqual(result, 60)

    def test_small_positive_bounded_floors_at_60(self):
        """收紧到 30 秒 → 下限 60。"""
        result = observer.next_check_interval(
            execution_phase="DRAINING", reset_at="2026-08-31T05:05:30Z",
            now=NOW)  # deadline = 05:00:30 → bounded 30 → 60
        self.assertEqual(result, 60)

    def test_bounded_exactly_60_is_kept(self):
        """收紧恰为 60 秒 → 60（边界含等号）。"""
        result = observer.next_check_interval(
            execution_phase="DRAINING", reset_at="2026-08-31T05:06:00Z",
            now=NOW)  # deadline = 05:01:00 → bounded 60
        self.assertEqual(result, 60)

    def test_unknown_or_unparseable_reset_no_tightening(self):
        """reset_at None / 不可解析 / 非 str → 按未知处理（表值）。"""
        for bad in (None, "not-a-time", 123, ""):
            self.assertEqual(
                observer.next_check_interval(
                    execution_phase="NORMAL", reset_at=bad, now=NOW),
                1800, repr(bad))

    def test_zero_grace_deadline_is_reset_itself(self):
        """grace_seconds=0 → 收紧目标即 reset 本身（reset 前 900 秒）。"""
        result = observer.next_check_interval(
            execution_phase="NORMAL", reset_at="2026-08-31T05:15:00Z",
            now=NOW, grace_seconds=0)
        self.assertEqual(result, 900)

    def test_custom_grace_shifts_deadline(self):
        """grace 60 → deadline = reset-60 → 距 now 840 秒。"""
        result = observer.next_check_interval(
            execution_phase="NORMAL", reset_at="2026-08-31T05:15:00Z",
            now=NOW, grace_seconds=60)
        self.assertEqual(result, 840)

    def test_now_required_none_rejected(self):
        """now=None → ValueError（无时间墙钟依赖的硬边界）。"""
        with self.assertRaises(ValueError):
            observer.next_check_interval(execution_phase="NORMAL",
                                         now=None)

    def test_invalid_now_rejected(self):
        with self.assertRaises(ValueError):
            observer.next_check_interval(execution_phase="NORMAL",
                                         now="yesterday")

    def test_invalid_phase_rejected(self):
        with self.assertRaises(ValueError):
            observer.next_check_interval(execution_phase="DISASTER",
                                         now=NOW)

    def test_negative_grace_rejected(self):
        with self.assertRaises(ValueError):
            observer.next_check_interval(execution_phase="NORMAL",
                                         now=NOW, grace_seconds=-1)

    def test_iso_string_and_naive_datetime_now(self):
        """now 接受 ISO 串 / naive datetime（按 UTC），结果一致。"""
        base = observer.next_check_interval(execution_phase="NORMAL",
                                            now=NOW)
        self.assertEqual(
            observer.next_check_interval(execution_phase="NORMAL",
                                         now=NOW_ISO), base)
        self.assertEqual(
            observer.next_check_interval(
                execution_phase="NORMAL",
                now=datetime(2026, 8, 31, 5, 0, 0)), base)


# —— 2：should_refresh（lazy heartbeat 判定） ——

class ShouldRefreshTest(unittest.TestCase):

    DUE = "2026-08-31T06:00:00Z"

    def test_boundary_inclusive_true(self):
        """now == next_check_at → True（边界含等号）。"""
        self.assertTrue(observer.should_refresh(now=NOW.replace(hour=6),
                                                next_check_at=self.DUE))

    def test_before_due_false(self):
        moment = datetime(2026, 8, 31, 5, 59, 59, tzinfo=timezone.utc)
        self.assertFalse(observer.should_refresh(now=moment,
                                                 next_check_at=self.DUE))

    def test_after_due_true(self):
        moment = datetime(2026, 8, 31, 6, 0, 1, tzinfo=timezone.utc)
        self.assertTrue(observer.should_refresh(now=moment,
                                                next_check_at=self.DUE))

    def test_none_next_check_at_true(self):
        """next_check_at=None（observe 的 now=None 产物）→ 立即刷新。"""
        self.assertTrue(observer.should_refresh(now=NOW,
                                                next_check_at=None))

    def test_unparseable_next_check_at_true(self):
        """坏值按到期处理：绝不因 next_check_at 不可解析而跳过刷新。"""
        self.assertTrue(observer.should_refresh(now=NOW,
                                                next_check_at="garbage"))

    def test_mixed_datetime_and_iso_accepted(self):
        """now datetime ↔ next_check_at ISO（及反向）均可比。"""
        self.assertTrue(observer.should_refresh(now=NOW.replace(hour=7),
                                                next_check_at=self.DUE))
        # 反向：now 为 ISO 串、next_check_at 为 datetime 派生串
        self.assertTrue(observer.should_refresh(
            now=self.DUE,
            next_check_at=NOW.replace(hour=5).isoformat()))
        self.assertFalse(observer.should_refresh(
            now=self.DUE,
            next_check_at=NOW.replace(hour=7).isoformat()))

    def test_invalid_now_rejected(self):
        for bad in (None, 123, "soon"):
            with self.assertRaises(ValueError):
                observer.should_refresh(now=bad, next_check_at=self.DUE)


# —— 3：observe（§16.1 观测 dict） ——

class ObserveShapeTest(unittest.TestCase):
    """形状冻结 + 决策键复用（不重复实现映射的锚）。"""

    def test_frozen_eight_keys(self):
        result = observe([win("five_hour", 50.0, "2026-09-01T00:00:00Z")],
                         now=NOW)
        self.assertEqual(set(result), FROZEN_OBSERVER_KEYS)

    def test_decision_keys_match_direct_control_call(self):
        """provider_status / execution_phase / reason 与 control 直调
        逐字一致（观测层只复用 D7 映射，不第二实现）。"""
        windows = [win("five_hour", 18.0, "2026-09-01T00:00:00Z")]
        expected = control.evaluate_task_quota_phase(
            provider_status="AVAILABLE", windows=windows)
        result = observe(windows, now=NOW)
        self.assertEqual(result["provider_status"],
                         expected["provider_status"])
        self.assertEqual(result["execution_phase"],
                         expected["execution_phase"])
        self.assertEqual(result["reason"], expected["reason"])

    def test_kwargs_passthrough_to_control(self):
        """quota_control / max_workers / wake_bridge_status 原样透传
        （透传效果经决策键可观测）。"""
        windows = [win("five_hour", 95.0, "2026-09-01T00:00:00Z")]
        result = observe(
            windows, max_workers=3, wake_bridge_status="armed",
            quota_control={"pressure_percent": 90, "draining_percent": 10},
            now=NOW)
        self.assertEqual(result["execution_phase"], "NORMAL")
        # 决策器行为对照：同参数直调一致
        direct = control.evaluate_task_quota_phase(
            provider_status="AVAILABLE", windows=windows, max_workers=3,
            wake_bridge_status="armed",
            quota_control={"pressure_percent": 90,
                           "draining_percent": 10})
        self.assertEqual(result["reason"], direct["reason"])

    def test_invalid_provider_status_rejected(self):
        with self.assertRaises(ValueError):
            observe([win("five_hour", 50.0)], provider_status="MEGA")

    def test_invalid_wake_bridge_status_rejected(self):
        with self.assertRaises(ValueError):
            observe([win("five_hour", 50.0)],
                    wake_bridge_status="exploded", now=NOW)

    def test_invalid_now_rejected(self):
        with self.assertRaises(ValueError):
            observe([win("five_hour", 50.0)], now="tomorrow")


class ObservePassthroughTest(unittest.TestCase):
    """remaining_percent / reset_at 最小剩余窗透传与缺失 None。"""

    def test_min_remaining_window_passthrough(self):
        """两窗取最小剩余窗透传；reset 取同窗；数值类型保持。"""
        result = observe([
            win("five_hour", 18, "2026-09-01T00:00:00Z"),
            win("weekly", 50, "2026-09-02T00:00:00Z")], now=NOW)
        self.assertEqual(result["remaining_percent"], 18)  # int 保持 int
        self.assertIsInstance(result["remaining_percent"], int)
        self.assertEqual(result["reset_at"], "2026-09-01T00:00:00Z")

    def test_tie_takes_first_window(self):
        """并列最小取先出现窗（确定性，与列表序耦合的规则写明）。"""
        result = observe([
            win("five_hour", 30, "2026-09-01T00:00:00Z"),
            win("weekly", 30, "2026-09-02T00:00:00Z")], now=NOW)
        self.assertEqual(result["reset_at"], "2026-09-01T00:00:00Z")

    def test_empty_windows_give_none(self):
        result = observe([], now=NOW)
        self.assertIsNone(result["remaining_percent"])
        self.assertIsNone(result["reset_at"])

    def test_windows_none_gives_none(self):
        """windows 非 list（形状非法）→ 透传 None，不炸消费方。"""
        result = observe(None, now=NOW)
        self.assertIsNone(result["remaining_percent"])
        self.assertIsNone(result["reset_at"])
        self.assertEqual(result["execution_phase"], "PRESSURE")  # fail-open

    def test_all_bad_windows_give_none(self):
        result = observe(["junk", win("five_hour", None, "2026-09-01T00:00:00Z")],
                         now=NOW)
        self.assertIsNone(result["remaining_percent"])
        self.assertIsNone(result["reset_at"])

    def test_reset_known_from_any_window_not_only_min(self):
        """「reset_at 已知」按任一数值窗判定（与决策器 §8.3 的
        reset_known 同口径）；透传仍取最小剩余窗。"""
        result = observe([
            win("five_hour", 30, None),
            win("weekly", 50, "2026-09-02T00:00:00Z")], now=NOW)
        self.assertEqual(result["remaining_percent"], 30)   # 最小剩余窗
        self.assertIsNone(result["reset_at"])               # 透传同窗
        self.assertTrue(result["wake_recommended"])         # 任一窗已知
        self.assertTrue(result["wake_required"])


class ObserveWakeFlagsTest(unittest.TestCase):
    """wake_recommended / wake_required 全分支。"""

    RESET = "2026-09-01T00:00:00Z"

    def test_pressure_active_with_reset_recommends_and_requires(self):
        result = observe([win("five_hour", 30, self.RESET)], now=NOW)
        self.assertEqual(result["execution_phase"], "PRESSURE")
        self.assertTrue(result["wake_recommended"])
        self.assertTrue(result["wake_required"])

    def test_pressure_inactive_recommends_nothing(self):
        """task_active=False → wake_recommended=False（且 PRESSURE
        义务 none → wake_required=False）。"""
        result = observe([win("five_hour", 30, self.RESET)],
                         task_active=False, now=NOW)
        self.assertFalse(result["wake_recommended"])
        self.assertFalse(result["wake_required"])

    def test_pressure_armed_bridge_requires_nothing(self):
        """bridge 已 armed → 义务 armed → wake_required=False，
        wake_recommended 不受桥影响。"""
        result = observe([win("five_hour", 30, self.RESET)],
                         wake_bridge_status="armed", now=NOW)
        self.assertTrue(result["wake_recommended"])
        self.assertFalse(result["wake_required"])

    def test_pressure_without_reset_neither(self):
        """reset 全未知 → wake_recommended=False（不虚构时刻）、
        义务 none → wake_required=False。"""
        result = observe([win("five_hour", 30, None)], now=NOW)
        self.assertFalse(result["wake_recommended"])
        self.assertFalse(result["wake_required"])

    def test_draining_active_recommends_and_requires(self):
        result = observe([win("five_hour", 15, self.RESET)], now=NOW)
        self.assertEqual(result["execution_phase"], "DRAINING")
        self.assertTrue(result["wake_recommended"])
        self.assertTrue(result["wake_required"])

    def test_draining_inactive_still_requires_but_not_recommends(self):
        """DRAINING 义务不因 task_active=False 豁免（§8.3）：
        wake_required=True 而 wake_recommended=False——两旗独立。"""
        result = observe([win("five_hour", 15, self.RESET)],
                         task_active=False, now=NOW)
        self.assertFalse(result["wake_recommended"])
        self.assertTrue(result["wake_required"])

    def test_normal_neither(self):
        result = observe([win("five_hour", 80, self.RESET)], now=NOW)
        self.assertEqual(result["execution_phase"], "NORMAL")
        self.assertFalse(result["wake_recommended"])
        self.assertFalse(result["wake_required"])

    def test_blocked_without_bridge_neither(self):
        """BLOCKED：相不在建议集（degraded 义务）→ 双 False。"""
        result = observe([win("five_hour", 0, self.RESET)], now=NOW)
        self.assertEqual(result["execution_phase"], "BLOCKED")
        self.assertFalse(result["wake_recommended"])
        self.assertFalse(result["wake_required"])

    def test_blocked_with_armed_bridge_waiting_quota_not_required(self):
        """BLOCKED + armed → waiting_quota（任务级等待态，非
        wake_required 词汇）→ wake_required=False。"""
        result = observe([win("five_hour", 0, self.RESET)],
                         wake_bridge_status="armed", now=NOW)
        self.assertFalse(result["wake_recommended"])
        self.assertFalse(result["wake_required"])

    def test_provider_unknown_fail_open_with_reset_recommends(self):
        """fail-open PRESSURE：规格公式按相判定——reset 已知 + active
        → wake_recommended=True（义务面同样 wake_required）。"""
        result = observe([win("five_hour", 50, self.RESET)],
                         provider_status="UNKNOWN", now=NOW)
        self.assertEqual(result["execution_phase"], "PRESSURE")
        self.assertTrue(result["wake_recommended"])
        self.assertTrue(result["wake_required"])


class ObserveNextCheckAtTest(unittest.TestCase):
    """next_check_at 推进（含 now=None 语义选择）。"""

    def test_now_none_gives_none_next_check_at(self):
        """**API 决策申报**：now 缺省 None 且 None 时绝不读墙钟 →
        next_check_at=None，其余七键照常产出（决策与唤醒判定不依赖
        now；需要 next_check_at 的消费方必须显式传 now）。"""
        result = observe([win("five_hour", 80, "2026-09-01T00:00:00Z")])
        self.assertIsNone(result["next_check_at"])
        self.assertEqual(set(result), FROZEN_OBSERVER_KEYS)  # 八键仍全
        self.assertEqual(result["execution_phase"], "NORMAL")

    def test_next_check_at_is_now_plus_interval(self):
        """NORMAL 无 reset → now + 1800s。"""
        result = observe([win("five_hour", 80, None)], now=NOW)
        self.assertEqual(result["next_check_at"], "2026-08-31T05:30:00Z")

    def test_next_check_at_pulled_earlier_to_reset_minus_grace(self):
        """已知 reset：next_check_at 取早到 reset - 300s（§6.2 不晚于
        边界前 grace 秒——尽管 NORMAL 表值 1800 更长）。"""
        result = observe([win("five_hour", 80, "2026-08-31T05:15:00Z")],
                         now=NOW)
        self.assertEqual(result["next_check_at"], "2026-08-31T05:10:00Z")

    def test_unparseable_reset_ignores_tightening(self):
        """reset 不可解析 → 纯 now + interval（不虚构收紧）。"""
        result = observe([win("five_hour", 80, "not-a-time")], now=NOW)
        self.assertEqual(result["next_check_at"], "2026-08-31T05:30:00Z")

    def test_draining_interval_300(self):
        result = observe([win("five_hour", 15, "2026-09-01T00:00:00Z")],
                         now=NOW)
        self.assertEqual(result["next_check_at"], "2026-08-31T05:05:00Z")

    def test_custom_grace_shifts_tightening(self):
        """grace 60 → 边界 = reset - 60 = 05:14:00。"""
        result = observe([win("five_hour", 80, "2026-08-31T05:15:00Z")],
                         now=NOW, grace_seconds=60)
        self.assertEqual(result["next_check_at"], "2026-08-31T05:14:00Z")

    def test_naive_datetime_now_treated_utc(self):
        result = observe([win("five_hour", 15, "2026-09-01T00:00:00Z")],
                         now=datetime(2026, 8, 31, 5, 0, 0))
        self.assertEqual(result["next_check_at"], "2026-08-31T05:05:00Z")


# —— 纯度锚：quota/* 包纪律（不 import runtime.state / task_manager） ——

class PurityTest(unittest.TestCase):

    def test_observer_module_has_no_state_or_task_manager_bindings(self):
        """observer 模块命名空间不含 state / task_manager 绑定
        （§16.2 纯决策；quota/* 包纪律的机械锚）。"""
        self.assertFalse(hasattr(observer, "state"))
        self.assertFalse(hasattr(observer, "task_manager"))


# —— 4：resolve_quota_detail（明细入口，四 source 路径） ——

NOW_R = datetime(2026, 8, 31, 5, 0, 0, tzinfo=timezone.utc)
NOW_ISO_MS = "2026-08-31T05:00:00.000Z"
OLD_ISO_MS = "2026-08-31T04:53:20.000Z"  # NOW - 400s（超出 300 秒新鲜期）

GOOD_SNAPSHOT = {
    "provider": "fake", "plan_generation": "v3", "plan_level": None,
    "fetched_at": "2026-08-31T04:59:59Z", "status": None,
    "windows": [{"kind": "five_hour", "used_percent": 50.0,
                 "remaining_percent": 50.0,
                 "reset_at": "2026-09-01T00:00:00Z"}],
}

DETAIL_KEYS = {"source", "status", "snapshot", "fetched_at"}


class FakeProvider(object):
    """fake quota provider：behavior 为 snapshot dict 或异常实例。"""

    def __init__(self, behavior):
        self.behavior = behavior
        self.calls = []

    def fetch(self, force=False):
        self.calls.append(force)
        if isinstance(self.behavior, Exception):
            raise self.behavior
        return dict(self.behavior)


def fake_factory(providers):
    """构造 _build_providers 替身（注入面与 tests/test_quota_resolver 同款）。"""
    def _factory(api_key, timeout_seconds=None):
        return list(providers)
    return _factory


class ResolveQuotaDetailTest(unittest.TestCase):
    """scratch 目录 + 凭证 / provider 注入（零真实网络）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = self._tmp.name

    def write_cache(self, status, fetched_at=OLD_ISO_MS, snapshot=None):
        # v2.2.1 WU-221-B1: planted cache now carries the identity hash (same-identity fixture; legacy-reuse assertions superseded by the binding ruling)
        payload = {"provider": "fake", "fetched_at": fetched_at,
                   "snapshot": (GOOD_SNAPSHOT if snapshot is None
                                else snapshot),
                   "status": status,
                   "provider_identity_hash":
                       quota_resolver._provider_identity_hash(
                           "test-key-value")}
        import os
        os.makedirs(os.path.dirname(quota_resolver._cache_path(self.root)),
                    exist_ok=True)
        with open(quota_resolver._cache_path(self.root), "w",
                  encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2,
                      sort_keys=True)

    def patch_credential(self, key="test-key-value"):
        if key is None:
            return mock.patch.object(quota_resolver, "resolve_credential",
                                     return_value=(None, None))
        return mock.patch.object(quota_resolver, "resolve_credential",
                                 return_value=("env", key))

    def resolve_detail(self, **kwargs):
        kwargs.setdefault("now", NOW_R)
        return quota_resolver.resolve_quota_detail(self.root, **kwargs)

    def resolve_status(self, **kwargs):
        kwargs.setdefault("now", NOW_R)
        return quota_resolver.resolve_quota_status(self.root, **kwargs)

    def test_fresh_cache_detail(self):
        """层级 1：cache_fresh + 缓存 snapshot/fetched_at 透出，零网络。"""
        self.write_cache("PRESSURE", fetched_at=NOW_ISO_MS)
        factory = fake_factory([("fake", FakeProvider(GOOD_SNAPSHOT))])
        with self.patch_credential(), \
                mock.patch.object(quota_resolver, "_build_providers",
                                  factory):
            detail = self.resolve_detail()
            status = self.resolve_status()
        self.assertEqual(set(detail), DETAIL_KEYS)  # 四键冻结
        self.assertEqual(detail["source"], "cache_fresh")
        self.assertEqual(detail["status"], "PRESSURE")
        self.assertEqual(detail["snapshot"], GOOD_SNAPSHOT)
        self.assertEqual(detail["fetched_at"], NOW_ISO_MS)
        # 与 resolve_quota_status 同源一致
        self.assertEqual((detail["source"], detail["status"]),
                         (status["source"], status["status"]))

    def test_provider_detail_snapshot_and_fetched_at(self):
        """层级 2：provider 成功 → snapshot 为本次抓取、fetched_at =
        本次 evaluated_at、缓存被刷新。"""
        self.write_cache("PRESSURE", fetched_at=OLD_ISO_MS)  # 过期
        factory = fake_factory([("fake", FakeProvider(GOOD_SNAPSHOT))])
        with self.patch_credential(), \
                mock.patch.object(quota_resolver, "_build_providers",
                                  factory):
            detail = self.resolve_detail()
        self.assertEqual(detail["source"], "provider")
        self.assertEqual(detail["status"], "AVAILABLE")
        self.assertEqual(detail["snapshot"], GOOD_SNAPSHOT)
        self.assertEqual(detail["fetched_at"], NOW_ISO_MS)

    def test_force_refresh_provider_path(self):
        """force_refresh=True：新鲜缓存也走 provider（明细入口同语义）。"""
        self.write_cache("PRESSURE", fetched_at=NOW_ISO_MS)  # 本来新鲜
        factory = fake_factory([("fake", FakeProvider(GOOD_SNAPSHOT))])
        with self.patch_credential(), \
                mock.patch.object(quota_resolver, "_build_providers",
                                  factory):
            detail = self.resolve_detail(force_refresh=True)
        self.assertEqual(detail["source"], "provider")
        self.assertEqual(detail["status"], "AVAILABLE")

    def test_stale_cache_detail(self):
        """层级 3：provider 失败 → cache_stale + 缓存 snapshot 与原始
        fetched_at（非本次评估时刻）。"""
        self.write_cache("PRESSURE", fetched_at=OLD_ISO_MS)
        factory = fake_factory([("fake", FakeProvider(None))])  # 抓取失败
        with self.patch_credential(), \
                mock.patch.object(quota_resolver, "_build_providers",
                                  factory):
            detail = self.resolve_detail()
        self.assertEqual(detail["source"], "cache_stale")
        self.assertEqual(detail["status"], "PRESSURE")
        self.assertEqual(detail["snapshot"], GOOD_SNAPSHOT)
        self.assertEqual(detail["fetched_at"], OLD_ISO_MS)

    def test_none_detail_gives_none_snapshot(self):
        """层级 4：无缓存无凭证 → none / UNKNOWN / snapshot=None /
        fetched_at=None（绝不虚构）。"""
        with self.patch_credential(key=None):
            detail = self.resolve_detail()
        self.assertEqual(detail["source"], "none")
        self.assertEqual(detail["status"], "UNKNOWN")
        self.assertIsNone(detail["snapshot"])
        self.assertIsNone(detail["fetched_at"])

    def test_corrupt_cache_none_path(self):
        """缓存损坏 JSON → 视为无缓存（none / UNKNOWN）。"""
        import os
        os.makedirs(os.path.dirname(quota_resolver._cache_path(self.root)),
                    exist_ok=True)
        with open(quota_resolver._cache_path(self.root), "w",
                  encoding="utf-8", newline="\n") as handle:
            handle.write("{broken json")
        with self.patch_credential(key=None):
            detail = self.resolve_detail()
        self.assertEqual(detail["source"], "none")
        self.assertEqual(detail["status"], "UNKNOWN")
        self.assertIsNone(detail["snapshot"])


# —— 5：CLI quota-observe / quota-phase ——

TID = "observer-task-1a2b3c"
ROUTE = {"mode": "delegate", "delegability": "high",
         "assurance": "standard", "executor": "flash-implementer",
         "continuity": "foreground"}
VERIFY_CMD = "python3 -m unittest tests.test_quota_observer"
RESET = "2026-09-01T00:00:00Z"


def run_cli(*args):
    """调用 cli.main 并捕获 stdout，返回 (退出码, 解析后 JSON)。"""
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        code = cli.main(list(args))
    return code, json.loads(buffer.getvalue())


def run_cli_text(*args):
    """调用 cli.main 捕获 stdout 原文（ensure_ascii=False 断言用）。"""
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        code = cli.main(list(args))
    return code, buffer.getvalue()


def fake_detail(status, windows):
    """构造 resolve_quota_detail 的注入返回（provider 层形态）。"""
    return {"source": "provider", "status": status,
            "snapshot": {"provider": "fake", "plan_generation": "v3",
                         "plan_level": None,
                         "fetched_at": "2026-08-31T04:59:59Z",
                         "status": None,
                         "windows": list(windows)},
            "fetched_at": "2026-08-31T04:59:59.000Z"}


class CliFixture(unittest.TestCase):
    """tempdir 基座 + 真实 state 任务写入（new_task_state + save）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = self._tmp.name

    def make_task(self, status="executing", max_workers=2):
        st = state.new_task_state(
            TID, "observer CLI 测试目标", dict(ROUTE),
            ownership_files=("src/**",),
            verification_required=(VERIFY_CMD,), status=status)
        st["work_units"] = []
        st["dispatch"] = {"max_workers": max_workers, "active": []}
        state.save_state(self.repo, st)
        return st


class QuotaObserveCliTest(CliFixture):
    """quota-observe：happy / 无任务默认 / state 装配 / 1 / 2 / 真实
    resolver 接线 / UTF-8 原文。"""

    def test_no_task_defaults_pressure_window(self):
        """无 task_id：task_active=True、bridge=none → PRESSURE 窗
        （30% + reset）→ wake_recommended/wake_required 双 True。"""
        windows = [win("five_hour", 30, RESET)]
        with mock.patch.object(
                quota_resolver, "resolve_quota_detail",
                return_value=fake_detail("AVAILABLE", windows)) as fake:
            code, payload = run_cli("quota-observe", str(self.repo))
        self.assertEqual(code, 0)
        fake.assert_called_once_with(str(self.repo))
        self.assertEqual(set(payload), FROZEN_OBSERVER_KEYS)
        self.assertEqual(payload["provider_status"], "AVAILABLE")
        self.assertEqual(payload["execution_phase"], "PRESSURE")
        self.assertEqual(payload["remaining_percent"], 30)
        self.assertEqual(payload["reset_at"], RESET)
        self.assertTrue(payload["wake_recommended"])
        self.assertTrue(payload["wake_required"])
        self.assertTrue(payload["next_check_at"].endswith("Z"))

    def test_task_bridge_armed_suppresses_wake_required(self):
        """真实 state 装配：continuation.wake_bridge.status=armed →
        wake_required=False（bridge 装配被观测到）。"""
        self.make_task()
        st = state.load_state(self.repo, TID)
        st["continuation"]["wake_bridge"]["status"] = "armed"
        state.save_state(self.repo, st)
        windows = [win("five_hour", 15, RESET)]  # DRAINING
        with mock.patch.object(quota_resolver, "resolve_quota_detail",
                               return_value=fake_detail("AVAILABLE",
                                                        windows)):
            code, payload = run_cli("quota-observe", str(self.repo), TID)
        self.assertEqual(code, 0)
        self.assertEqual(payload["execution_phase"], "DRAINING")
        self.assertTrue(payload["wake_recommended"])   # 相 + reset + active
        self.assertFalse(payload["wake_required"])     # bridge armed

    def test_waiting_task_not_active(self):
        """waiting_quota 任务不在执行态族 → task_active=False →
        wake_recommended=False（DRAINING 义务仍在 → required=True）。"""
        self.make_task(status="waiting_quota")
        windows = [win("five_hour", 15, RESET)]
        with mock.patch.object(quota_resolver, "resolve_quota_detail",
                               return_value=fake_detail("AVAILABLE",
                                                        windows)):
            code, payload = run_cli("quota-observe", str(self.repo), TID)
        self.assertEqual(code, 0)
        self.assertFalse(payload["wake_recommended"])
        self.assertTrue(payload["wake_required"])

    def test_missing_task_exit_1_state_checked_before_resolver(self):
        with mock.patch.object(quota_resolver, "resolve_quota_detail") as fake:
            code, payload = run_cli("quota-observe", str(self.repo),
                                    "ghost-task")
        self.assertEqual(code, 1)
        self.assertIn("不存在", payload["error"])
        fake.assert_not_called()  # 任务缺失先于 resolver（不发起查询）

    def test_usage_errors_exit_2(self):
        code, _ = run_cli("quota-observe")
        self.assertEqual(code, 2)
        code, _ = run_cli("quota-observe", str(self.repo), TID, "extra")
        self.assertEqual(code, 2)

    def test_real_resolver_no_cache_no_credential_fail_open(self):
        """真实 resolver 接线（非 mock 伪证）：无缓存 + 无凭证 →
        UNKNOWN → fail-open PRESSURE、wake 双 False、退出码 0。"""
        self.make_task()
        with mock.patch.object(quota_resolver, "resolve_credential",
                               return_value=(None, None)):
            code, payload = run_cli("quota-observe", str(self.repo), TID)
        self.assertEqual(code, 0)
        self.assertEqual(payload["provider_status"], "UNKNOWN")
        self.assertEqual(payload["execution_phase"], "PRESSURE")
        self.assertIsNone(payload["remaining_percent"])
        self.assertFalse(payload["wake_recommended"])
        self.assertFalse(payload["wake_required"])
        self.assertTrue(payload["next_check_at"].endswith("Z"))

    def test_utf8_raw_reason_not_escaped(self):
        """ensure_ascii=False：中文 reason 原文可读，无 \\uXXXX 转义。"""
        windows = [win("five_hour", 80, RESET)]  # NORMAL：reason 含「额度」
        with mock.patch.object(quota_resolver, "resolve_quota_detail",
                               return_value=fake_detail("AVAILABLE",
                                                        windows)):
            code, text = run_cli_text("quota-observe", str(self.repo))
        self.assertEqual(code, 0)
        self.assertIn("额度", text)
        self.assertNotIn("\\u", text)


class QuotaPhaseCliTest(CliFixture):
    """quota-phase：决策 dict 原样直出（与 control 直调全等）/ 1 / 2。"""

    def test_phase_matches_direct_evaluation(self):
        """输出 == 同输入直调 evaluate_task_quota_phase（薄壳锚：
        state 的 policy max_workers / 阈值 / bridge / 执行态全部装配）。"""
        st = self.make_task()  # executing + 默认 policy（max_workers=2）
        windows = [win("five_hour", 80, RESET)]
        with mock.patch.object(quota_resolver, "resolve_quota_detail",
                               return_value=fake_detail("AVAILABLE",
                                                        windows)):
            code, payload = run_cli("quota-phase", str(self.repo), TID)
        self.assertEqual(code, 0)
        expected = control.evaluate_task_quota_phase(
            provider_status="AVAILABLE", windows=windows,
            quota_control=execution_policy.default_quota_control(
                st["execution_policy"]),
            max_workers=st["execution_policy"]["parallelism"]["max_workers"],
            task_active=True, wake_bridge_status="none")
        self.assertEqual(payload, expected)  # 原样直出（含中文 reason）
        self.assertEqual(payload["dispatch_budget"], 2)  # policy 预算已装配

    def test_phase_empty_snapshot_fails_open(self):
        """snapshot 缺失（source=none 层）→ 空窗口 fail-open PRESSURE
        预算 1、不虚构 wake_at。"""
        self.make_task()
        detail = {"source": "none", "status": "UNKNOWN", "snapshot": None,
                  "fetched_at": None}
        with mock.patch.object(quota_resolver, "resolve_quota_detail",
                               return_value=detail):
            code, payload = run_cli("quota-phase", str(self.repo), TID)
        self.assertEqual(code, 0)
        self.assertEqual(set(payload), FROZEN_DECISION_KEYS)
        self.assertEqual(payload["execution_phase"], "PRESSURE")
        self.assertEqual(payload["dispatch_budget"], 1)
        self.assertIsNone(payload["wake_at"])
        self.assertIn("fail-open", payload["reason"])

    def test_phase_missing_task_exit_1(self):
        with mock.patch.object(quota_resolver, "resolve_quota_detail") as fake:
            code, payload = run_cli("quota-phase", str(self.repo),
                                    "ghost-task")
        self.assertEqual(code, 1)
        self.assertIn("不存在", payload["error"])
        fake.assert_not_called()

    def test_phase_usage_errors_exit_2(self):
        code, _ = run_cli("quota-phase", str(self.repo))
        self.assertEqual(code, 2)
        code, _ = run_cli("quota-phase", str(self.repo), TID, "extra")
        self.assertEqual(code, 2)

    def test_phase_utf8_raw_reason_not_escaped(self):
        self.make_task()
        windows = [win("five_hour", 80, RESET)]
        with mock.patch.object(quota_resolver, "resolve_quota_detail",
                               return_value=fake_detail("AVAILABLE",
                                                        windows)):
            code, text = run_cli_text("quota-phase", str(self.repo), TID)
        self.assertEqual(code, 0)
        self.assertIn("额度", text)
        self.assertNotIn("\\u", text)


if __name__ == "__main__":
    unittest.main()
