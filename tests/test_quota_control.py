#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""runtime.quota.control 纯决策器单元测试（v2.2 M2，wu-22-02）。

锚定对象：主计划 §4.2（execution phase 四态）/ §5.1（固定阈值）/
§8.3（obligation 规则）/ §17.1（返回 dict 形状）/ §18/§18.1（预算与
白名单）；决策记录 D7（双层映射冻结，provider_status ≠ execution_phase）、
D5（阈值默认单一真相源 DEFAULT_QUOTA_CONTROL）。

运行：
    cd <repo_root> && python3 -m unittest tests.test_quota_control -v

全部离线：windows 以手写 §27 形状 dict 为主；零 I/O、零网络
（被测函数为纯决策器）。

覆盖映射：
    1  D7 五条映射各至少一例（含边界等号：==20→DRAINING、==35→
       PRESSURE、==0 且 provider AVAILABLE→BLOCKED、provider
       EXHAUSTED 但 remaining>0→BLOCKED 双入口）
    2  fail-open：provider UNKNOWN → PRESSURE 预算 1；windows 空/全坏
       → PRESSURE、wake_at=None、reason 含 fail-open
    3  双层命名区分：provider_status=AVAILABLE 与 execution_phase=
       PRESSURE 同 dict 共存
    4  预算/白名单四相（NORMAL 用 max_workers、PRESSURE 1、
       DRAINING 0/False/True、BLOCKED 0/False/False）
    5  obligation 全分支（NORMAL none；PRESSURE armed/wake_required/
       none；DRAINING armed/wake_required；BLOCKED waiting_quota/
       degraded）
    6  wake_at 数学（reset+默认 300s、自定义 grace、无 reset→None、
       不可解析 reset→None、多窗最早 reset 选择）
    7  参数校验 ValueError（非法 provider_status、违例阈值
       draining>=pressure、非法 wake_bridge_status、max_workers<=0
       等）
    8  两窗 severity 混合（18%+50%→DRAINING）与坏窗容忍跳过
    +  §17.1 冻结 8 键形状、quota_control 键级合并、词汇与
       runtime.state 枚举同步
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "glm-conductor"))
from runtime import execution_policy, state
from runtime.quota import control

# —— 常量与装置 ——

# §27 形状的标准化 ISO 串（Z 形式；与 scheduler 测试同源）
RESET_FIVE = "2026-08-28T06:45:02Z"
RESET_WEEKLY = "2026-09-01T12:20:00Z"
FIVE_PLUS_GRACE = "2026-08-28T06:50:02Z"    # RESET_FIVE + 默认 300s
WEEKLY_PLUS_GRACE = "2026-09-01T12:25:00Z"  # RESET_WEEKLY + 默认 300s

# §17.1 冻结 8 键（键名逐字）
FROZEN_KEYS = {
    "provider_status", "execution_phase", "dispatch_budget",
    "allow_new_wave", "allow_finish_current", "continuation_obligation",
    "wake_at", "reason",
}


def win(kind, remaining=None, reset_at=None):
    """构造 §27 形状的单窗口 dict（used_percent 对决策层无意义，仅保形）。"""
    return {"kind": kind, "used_percent": None,
            "remaining_percent": remaining, "reset_at": reset_at}


def decide(windows, provider_status="AVAILABLE", **overrides):
    """evaluate_task_quota_phase 的短封装（provider 默认 AVAILABLE）。"""
    return control.evaluate_task_quota_phase(
        provider_status=provider_status, windows=windows, **overrides)


# —— 1/8：D7 冻结映射（含边界等号与多窗 severity 混合） ——

class D7MappingTest(unittest.TestCase):

    def test_frozen_shape_eight_keys(self):
        """返回 dict 恰为 §17.1 冻结 8 键，键名逐字。"""
        result = decide([win("five_hour", 50.0, RESET_FIVE)])
        self.assertEqual(set(result), FROZEN_KEYS)
        self.assertEqual(result["provider_status"], "AVAILABLE")
        self.assertIsInstance(result["dispatch_budget"], int)
        self.assertIsInstance(result["allow_new_wave"], bool)
        self.assertIsInstance(result["allow_finish_current"], bool)

    def test_rule1_zero_remaining_blocks_even_provider_available(self):
        """D7-1：任一窗 remaining==0 → BLOCKED（provider 仍报 AVAILABLE）。"""
        result = decide([win("five_hour", 0.0, RESET_FIVE)])
        self.assertEqual(result["execution_phase"], "BLOCKED")
        self.assertEqual(result["provider_status"], "AVAILABLE")
        self.assertFalse(result["allow_new_wave"])
        self.assertFalse(result["allow_finish_current"])
        self.assertEqual(result["wake_at"], FIVE_PLUS_GRACE)

    def test_rule1_provider_exhausted_with_positive_remaining(self):
        """D7-1 双入口：provider EXHAUSTED 但窗 remaining>0 → BLOCKED。"""
        result = decide([win("five_hour", 40.0, RESET_FIVE)],
                        provider_status="EXHAUSTED")
        self.assertEqual(result["execution_phase"], "BLOCKED")
        self.assertEqual(result["dispatch_budget"], 0)
        self.assertEqual(result["wake_at"], FIVE_PLUS_GRACE)

    def test_rule2_draining_at_boundary_20(self):
        """D7-2：remaining==draining_percent(20)（边界等号）→ DRAINING。"""
        threshold = execution_policy.DEFAULT_QUOTA_CONTROL["draining_percent"]
        result = decide([win("five_hour", threshold, RESET_FIVE)])
        self.assertEqual(result["execution_phase"], "DRAINING")

    def test_rule2_draining_beats_provider_available(self):
        """D7-2：provider 报 AVAILABLE 但剩余 18% ≤ 20 → DRAINING
        （v2.1 的 24% 误判类场景由此封堵）。"""
        result = decide([win("five_hour", 18.0, RESET_FIVE)])
        self.assertEqual(result["execution_phase"], "DRAINING")

    def test_above_draining_boundary_is_not_draining(self):
        """边界另侧：remaining 略高于排水阈值 → 不再是 DRAINING。"""
        result = decide([win("five_hour", 20.5, RESET_FIVE)])
        self.assertEqual(result["execution_phase"], "PRESSURE")

    def test_rule3_pressure_at_boundary_35(self):
        """D7-3：remaining==pressure_percent(35)（边界等号）→ PRESSURE。"""
        threshold = execution_policy.DEFAULT_QUOTA_CONTROL["pressure_percent"]
        result = decide([win("five_hour", threshold, RESET_FIVE)])
        self.assertEqual(result["execution_phase"], "PRESSURE")

    def test_rule5_normal_above_all_thresholds(self):
        """D7-5：remaining 高于全部阈值 → NORMAL。"""
        result = decide([win("five_hour", 35.5, RESET_FIVE)])
        self.assertEqual(result["execution_phase"], "NORMAL")

    def test_two_windows_mixed_severity_takes_worst(self):
        """D7 混合窗：five_hour 18% + weekly 50% → 取最严重 DRAINING。"""
        result = decide([
            win("five_hour", 18.0, RESET_FIVE),
            win("weekly", 50.0, RESET_WEEKLY)])
        self.assertEqual(result["execution_phase"], "DRAINING")
        self.assertEqual(result["dispatch_budget"], 0)
        self.assertTrue(result["allow_finish_current"])


# —— 2：fail-open ——

class FailOpenTest(unittest.TestCase):

    def test_provider_unknown_pressures_with_budget_1(self):
        """D7-4：provider UNKNOWN → PRESSURE、预算 1、不虚构唤醒。"""
        result = decide([win("five_hour", 50.0, RESET_FIVE)],
                        provider_status="UNKNOWN")
        self.assertEqual(result["execution_phase"], "PRESSURE")
        self.assertEqual(result["dispatch_budget"], 1)
        self.assertTrue(result["allow_new_wave"])
        self.assertIsNone(result["wake_at"])
        self.assertIn("fail-open", result["reason"])

    def test_empty_windows_fail_open(self):
        """D7-4：windows 为空列表 → PRESSURE、wake_at=None。"""
        result = decide([])
        self.assertEqual(result["execution_phase"], "PRESSURE")
        self.assertEqual(result["dispatch_budget"], 1)
        self.assertIsNone(result["wake_at"])
        self.assertIn("fail-open", result["reason"])

    def test_all_bad_windows_fail_open(self):
        """D7-4：全坏窗（非 dict / 缺数值剩余）→ PRESSURE，reason 注明跳过。"""
        result = decide(["not-a-dict", win("five_hour", None, RESET_FIVE)])
        self.assertEqual(result["execution_phase"], "PRESSURE")
        self.assertIsNone(result["wake_at"])
        self.assertIn("fail-open", result["reason"])
        self.assertIn("跳过 2 个", result["reason"])

    def test_windows_shape_invalid_treated_as_empty(self):
        """windows 非 list（形状非法）→ 按空窗口 fail-open，不炸消费方。"""
        result = decide(None)
        self.assertEqual(result["execution_phase"], "PRESSURE")
        self.assertIsNone(result["wake_at"])
        self.assertIn("fail-open", result["reason"])


# —— 3：双层命名区分 ——

class LayerNamingTest(unittest.TestCase):

    def test_provider_available_and_execution_pressure_coexist(self):
        """provider_status=AVAILABLE 与 execution_phase=PRESSURE 同 dict
        共存（D7：两个 PRESSURE 是不同维度词汇，不得混用）。"""
        result = decide([win("weekly", 30.0, RESET_WEEKLY)])
        self.assertEqual(result["provider_status"], "AVAILABLE")
        self.assertEqual(result["execution_phase"], "PRESSURE")
        self.assertIn("PRESSURE", result["reason"])

    def test_provider_available_and_execution_draining_coexist(self):
        """provider AVAILABLE + execution DRAINING 同样共存。"""
        result = decide([win("five_hour", 12.0, RESET_FIVE)])
        self.assertEqual(result["provider_status"], "AVAILABLE")
        self.assertEqual(result["execution_phase"], "DRAINING")

    def test_phase_vocabulary_is_execution_not_provider(self):
        """execution_phase 词汇来自 EXECUTION_PHASES（四相）。"""
        self.assertEqual(
            control.EXECUTION_PHASES,
            ("NORMAL", "PRESSURE", "DRAINING", "BLOCKED"))


# —— 4：§18 / §18.1 预算与白名单四相 ——

class BudgetWhitelistTest(unittest.TestCase):

    def test_normal_uses_max_workers(self):
        """NORMAL → dispatch_budget = max_workers，双向放行。"""
        result = decide([win("five_hour", 80.0, RESET_FIVE)],
                        max_workers=3)
        self.assertEqual(result["execution_phase"], "NORMAL")
        self.assertEqual(result["dispatch_budget"], 3)
        self.assertTrue(result["allow_new_wave"])
        self.assertTrue(result["allow_finish_current"])

    def test_normal_without_max_workers_is_conservative_one(self):
        """NORMAL 且 max_workers=None → 按 1 保守。"""
        result = decide([win("five_hour", 80.0, RESET_FIVE)])
        self.assertEqual(result["dispatch_budget"], 1)

    def test_pressure_budget_1(self):
        """PRESSURE → 预算 1，仍可开新波次。"""
        result = decide([win("five_hour", 30.0, RESET_FIVE)], max_workers=4)
        self.assertEqual(result["dispatch_budget"], 1)
        self.assertTrue(result["allow_new_wave"])
        self.assertTrue(result["allow_finish_current"])

    def test_draining_zero_budget_finish_only(self):
        """DRAINING → 预算 0、禁新波次、允许收尾（§18.1 白名单）。"""
        result = decide([win("five_hour", 15.0, RESET_FIVE)], max_workers=4)
        self.assertEqual(result["dispatch_budget"], 0)
        self.assertFalse(result["allow_new_wave"])
        self.assertTrue(result["allow_finish_current"])

    def test_blocked_freezes_everything(self):
        """BLOCKED → 预算 0、禁新波次、禁收尾。"""
        result = decide([win("five_hour", 0.0, RESET_FIVE)], max_workers=4)
        self.assertEqual(result["dispatch_budget"], 0)
        self.assertFalse(result["allow_new_wave"])
        self.assertFalse(result["allow_finish_current"])


# —— 5：§8.3 obligation 全分支 ——

class ObligationTest(unittest.TestCase):

    def test_normal_obligation_none_even_with_armed_bridge(self):
        """NORMAL → none（即使 bridge 已 armed）。"""
        result = decide([win("five_hour", 80.0, RESET_FIVE)],
                        wake_bridge_status="armed")
        self.assertEqual(result["continuation_obligation"], "none")

    def test_pressure_armed_bridge_stays_armed(self):
        """PRESSURE + bridge armed → armed（不降级为 wake_required）。"""
        result = decide([win("five_hour", 30.0, RESET_FIVE)],
                        wake_bridge_status="armed")
        self.assertEqual(result["continuation_obligation"], "armed")

    def test_pressure_active_task_with_known_reset_wakes(self):
        """PRESSURE + task_active + 已知 reset_at → wake_required。"""
        result = decide([win("five_hour", 30.0, RESET_FIVE)])
        self.assertEqual(result["continuation_obligation"], "wake_required")

    def test_pressure_inactive_task_is_none(self):
        """PRESSURE + task_active=False → none（无推进主体）。"""
        result = decide([win("five_hour", 30.0, RESET_FIVE)],
                        task_active=False)
        self.assertEqual(result["continuation_obligation"], "none")

    def test_pressure_without_known_reset_is_none(self):
        """PRESSURE + 无任何已知 reset_at → none。"""
        result = decide([win("five_hour", 30.0, None)])
        self.assertEqual(result["continuation_obligation"], "none")

    def test_draining_armed_bridge_stays_armed(self):
        """DRAINING + bridge armed → armed。"""
        result = decide([win("five_hour", 15.0, RESET_FIVE)],
                        wake_bridge_status="armed")
        self.assertEqual(result["continuation_obligation"], "armed")

    def test_draining_without_bridge_requires_wake(self):
        """DRAINING + bridge 未建置 → wake_required（§8.3 必须 armed）。"""
        result = decide([win("five_hour", 15.0, RESET_FIVE)])
        self.assertEqual(result["continuation_obligation"], "wake_required")

    def test_draining_requires_wake_even_if_task_inactive(self):
        """DRAINING 义务不因 task_active=False 豁免（§8.3 无该条件）。"""
        result = decide([win("five_hour", 15.0, RESET_FIVE)],
                        task_active=False)
        self.assertEqual(result["continuation_obligation"], "wake_required")

    def test_blocked_with_armed_bridge_waits_quota(self):
        """BLOCKED + bridge armed → waiting_quota（任务级等待额度态）。"""
        result = decide([win("five_hour", 0.0, RESET_FIVE)],
                        wake_bridge_status="armed")
        self.assertEqual(result["continuation_obligation"], "waiting_quota")

    def test_blocked_with_fired_bridge_waits_quota(self):
        """BLOCKED + bridge fired（唤醒已触发）→ waiting_quota。"""
        result = decide([win("five_hour", 0.0, RESET_FIVE)],
                        wake_bridge_status="fired")
        self.assertEqual(result["continuation_obligation"], "waiting_quota")

    def test_blocked_without_bridge_degrades(self):
        """BLOCKED + bridge 未建置 → degraded（不依赖模型补建）。"""
        result = decide([win("five_hour", 0.0, RESET_FIVE)])
        self.assertEqual(result["continuation_obligation"], "degraded")

    def test_blocked_with_requested_bridge_degrades(self):
        """BLOCKED + bridge requested（已请求未 armed）→ degraded。"""
        result = decide([win("five_hour", 0.0, RESET_FIVE)],
                        wake_bridge_status="requested")
        self.assertEqual(result["continuation_obligation"], "degraded")


# —— 6：wake_at 时间数学 ——

class WakeAtMathTest(unittest.TestCase):

    def test_default_grace_300_seconds(self):
        """wake_at = reset_at + 默认 300s，Z 形式 ISO 输出。"""
        result = decide([win("five_hour", 15.0, RESET_FIVE)])
        self.assertEqual(result["wake_at"], FIVE_PLUS_GRACE)

    def test_zero_grace_returns_reset_exactly(self):
        """grace_seconds=0 → wake_at 精确等于 reset_at。"""
        result = decide([win("five_hour", 15.0, RESET_FIVE)],
                        grace_seconds=0)
        self.assertEqual(result["wake_at"], RESET_FIVE)

    def test_no_reset_at_gives_none(self):
        """相关窗无 reset_at → wake_at=None（不虚构）。"""
        result = decide([win("five_hour", 15.0, None)])
        self.assertEqual(result["execution_phase"], "DRAINING")
        self.assertIsNone(result["wake_at"])

    def test_unparseable_reset_at_gives_none(self):
        """reset_at 不可解析 → wake_at=None（不虚构，纪律同 §31）。"""
        result = decide([win("five_hour", 15.0, "not-a-time")])
        self.assertIsNone(result["wake_at"])

    def test_multi_window_picks_earliest_reset_not_list_order(self):
        """多相关窗取最早 reset（与列表顺序无关）。"""
        result = decide([
            win("five_hour", 15.0, RESET_WEEKLY),  # 排在前但 reset 更晚
            win("weekly", 10.0, RESET_FIVE),       # reset 更早
        ])
        self.assertEqual(result["execution_phase"], "DRAINING")
        self.assertEqual(result["wake_at"], FIVE_PLUS_GRACE)

    def test_normal_phase_has_no_wake(self):
        """NORMAL → wake_at 恒 None。"""
        result = decide([win("five_hour", 80.0, RESET_FIVE)])
        self.assertIsNone(result["wake_at"])

    def test_fail_open_has_no_wake_even_with_resets(self):
        """fail-open（provider UNKNOWN）→ wake_at=None，不虚构唤醒。"""
        result = decide([win("five_hour", 50.0, RESET_FIVE)],
                        provider_status="UNKNOWN")
        self.assertIsNone(result["wake_at"])


# —— 7：参数校验 ValueError ——

class ValidationTest(unittest.TestCase):

    def _assert_value_error(self, **kwargs):
        kwargs.setdefault("provider_status", "AVAILABLE")
        kwargs.setdefault("windows", [])
        with self.assertRaises(ValueError):
            control.evaluate_task_quota_phase(**kwargs)

    def test_invalid_provider_status_rejected(self):
        """provider_status 不在四态词汇 → ValueError。"""
        self._assert_value_error(provider_status="DISASTER",
                                 windows=[win("five_hour", 50.0)])

    def test_draining_equal_pressure_rejected(self):
        """draining_percent == pressure_percent → ValueError（须严格小于）。"""
        self._assert_value_error(
            quota_control={"draining_percent": 35, "pressure_percent": 35})

    def test_draining_above_pressure_rejected(self):
        """draining_percent > pressure_percent → ValueError。"""
        self._assert_value_error(
            quota_control={"draining_percent": 50, "pressure_percent": 35})

    def test_negative_draining_rejected(self):
        """draining_percent < 0 → ValueError。"""
        self._assert_value_error(quota_control={"draining_percent": -1})

    def test_pressure_above_100_rejected(self):
        """pressure_percent > 100 → ValueError。"""
        self._assert_value_error(quota_control={"pressure_percent": 120})

    def test_non_finite_threshold_rejected(self):
        """阈值为字符串 → ValueError（bool / NaN 同理被拒）。"""
        self._assert_value_error(quota_control={"pressure_percent": "35"})

    def test_non_dict_quota_control_rejected(self):
        """quota_control 非 dict 非 None → ValueError。"""
        self._assert_value_error(quota_control=[35, 20])

    def test_invalid_wake_bridge_status_rejected(self):
        """wake_bridge_status 不在十值词汇 → ValueError。"""
        self._assert_value_error(wake_bridge_status="exploded")

    def test_max_workers_zero_rejected(self):
        """max_workers=0 → ValueError（非正数）。"""
        self._assert_value_error(max_workers=0)

    def test_max_workers_negative_rejected(self):
        """max_workers<0 → ValueError。"""
        self._assert_value_error(max_workers=-2)

    def test_max_workers_bool_rejected(self):
        """max_workers=True（bool 冒充整数）→ ValueError。"""
        self._assert_value_error(max_workers=True)

    def test_negative_grace_rejected(self):
        """grace_seconds<0 → ValueError。"""
        self._assert_value_error(grace_seconds=-1)


# —— quota_control 键级合并（D5 单一真相源） ——

class QuotaControlMergeTest(unittest.TestCase):

    def test_custom_pressure_only_merges_with_default_draining(self):
        """只给 pressure_percent → draining 仍按默认 20 解释。"""
        result = decide([win("five_hour", 15.0, RESET_FIVE)],
                        quota_control={"pressure_percent": 30})
        self.assertEqual(result["execution_phase"], "DRAINING")

    def test_custom_thresholds_shift_band_boundaries(self):
        """自定义 10/30：12% 落入压力带、8% 落入排水线、40% 为 NORMAL。"""
        custom = {"draining_percent": 10, "pressure_percent": 30}
        self.assertEqual(decide([win("five_hour", 12.0, RESET_FIVE)],
                                quota_control=dict(custom))["execution_phase"],
                         "PRESSURE")
        self.assertEqual(decide([win("five_hour", 8.0, RESET_FIVE)],
                                quota_control=dict(custom))["execution_phase"],
                         "DRAINING")
        self.assertEqual(decide([win("five_hour", 40.0, RESET_FIVE)],
                                quota_control=dict(custom))["execution_phase"],
                         "NORMAL")

    def test_unknown_keys_ignored(self):
        """quota_control 未知键忽略（向前兼容）。"""
        result = decide([win("five_hour", 15.0, RESET_FIVE)],
                        quota_control={"bridge_interval_minutes": 60})
        self.assertEqual(result["execution_phase"], "DRAINING")

    def test_defaults_come_from_execution_policy_constant(self):
        """默认行为与 DEFAULT_QUOTA_CONTROL 数值一致（单一真相源）。"""
        defaults = execution_policy.DEFAULT_QUOTA_CONTROL
        draining = decide([win("five_hour",
                               defaults["draining_percent"], RESET_FIVE)])
        pressure = decide([win("five_hour",
                               defaults["pressure_percent"], RESET_FIVE)])
        self.assertEqual(draining["execution_phase"], "DRAINING")
        self.assertEqual(pressure["execution_phase"], "PRESSURE")


# —— 词汇一致性（control 字面量 ↔ runtime.state 枚举） ——

class VocabularySyncTest(unittest.TestCase):

    def test_wake_bridge_literal_matches_state_enum(self):
        """control 的 wake_bridge 十值字面量与 runtime.state 枚举一致。"""
        self.assertEqual(sorted(control._WAKE_BRIDGE_STATUSES),
                         sorted(state.WAKE_BRIDGE_STATUSES))

    def test_obligation_outputs_within_state_vocabularies(self):
        """义务输出 ⊆ CONTINUATION_OBLIGATIONS ∪ {waiting_quota}，
        waiting_quota 属 runtime.state.TASK_STATUSES（跨词汇机械映射）。"""
        produced = {"none", "armed", "wake_required", "waiting_quota",
                    "degraded"}
        for word in produced - {"waiting_quota"}:
            self.assertIn(word, state.CONTINUATION_OBLIGATIONS)
        self.assertIn("waiting_quota", state.TASK_STATUSES)

    def test_grace_default_matches_scheduler(self):
        """grace 默认复用 scheduler.DEFAULT_GRACE_SECONDS（无第二真相源）。"""
        from runtime.quota import scheduler
        self.assertEqual(scheduler.DEFAULT_GRACE_SECONDS, 300)


if __name__ == "__main__":
    unittest.main()
