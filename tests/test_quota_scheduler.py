#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""runtime.quota.scheduler 单元测试（v2 工作块 B5.3，升级指南 §45）。

运行：
    python3 -m unittest tests.test_quota_scheduler -v

全部离线：snapshot 以手写 dict 为主（§27 形状，与
parser.parse_quota_body 产出同形），并取一个 fixture
（both_exhausted.json）经 parse_quota_body 转换锚定解析层到调度层
的衔接；另含 runtime.state 可选顶层 quota 块校验（§39）用例。

覆盖映射（§45 十场景中可在本层测的部分 + 补充）：
    1  AVAILABLE → continue
    2  PRESSURE → checkpoint
    3  five_hour EXHAUSTED → resume_at = five reset + grace
    4  weekly EXHAUSTED → resume_at = weekly reset + grace
    5  双 EXHAUSTED → resume_at = 较晚 reset + grace（§30 max）
    6  EXHAUSTED 且 reset 未知 → periodic_fallback（不虚构）
    7  空窗口 / 缺 used → UNKNOWN → periodic_fallback
    8  force_refresh 标志只在 resume_at 路径为 True
    9  阈值边界（==threshold / >threshold / used=100）
    10 grace / now 注入（grace=0 时 resume_at 精确等于 max(reset)）
    11 evaluate 逐窗状态与 reason
    12 非法输入 ValueError
    13 state.py quota 块校验（§39）
"""

import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "glm-conductor"))
from runtime import state
from runtime.quota import parser, scheduler

# —— 常量与装置 ——

# 与 tests/fixtures/quota/ 中 nextResetTime 对应的标准化 ISO 串
RESET_FIVE = "2026-08-28T06:45:02Z"    # 1787899502000
RESET_WEEKLY = "2026-09-01T12:20:00Z"  # 1788265200000
FIVE_PLUS_GRACE = "2026-08-28T06:50:02Z"    # RESET_FIVE + 300s
WEEKLY_PLUS_GRACE = "2026-09-01T12:25:00Z"  # RESET_WEEKLY + 300s

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "quota"


def win(kind, used=None, remaining=None, reset_at=None):
    """构造 §27 形状的单窗口 dict。"""
    return {"kind": kind, "used_percent": used,
            "remaining_percent": remaining, "reset_at": reset_at}


def make_snapshot(windows, **overrides):
    """构造 §27 形状的 snapshot dict（status 恒 None，评估属调度器）。"""
    snapshot = {
        "provider": "bigmodel", "plan_generation": "v2",
        "plan_level": "pro", "fetched_at": "2026-08-28T05:00:00Z",
        "status": None, "windows": windows,
    }
    snapshot.update(overrides)
    return snapshot


def parse_fixture(name):
    """加载 tests/fixtures/quota/<name> 并解析为标准化 snapshot。"""
    with open(str(FIXTURE_DIR / name), "r", encoding="utf-8") as fh:
        body = json.load(fh)
    return parser.parse_quota_body(
        body, provider="bigmodel", fetched_at="2026-08-28T05:00:00Z")


# —— evaluate：§28 四态评估 ——

class TestEvaluate(unittest.TestCase):

    def test_available(self):
        """双窗余量充足 → AVAILABLE（场景 1 的评估侧）。"""
        snapshot = make_snapshot([
            win("five_hour", 20.0, 80.0, RESET_FIVE),
            win("weekly", 40.0, 60.0, RESET_WEEKLY)])
        result = scheduler.evaluate(snapshot)
        self.assertEqual(result["status"], "AVAILABLE")
        self.assertTrue(result["reason"])

    def test_pressure_at_8_percent(self):
        """剩余 8% ≤ 阈值 10 → PRESSURE（场景 2 的评估侧）。"""
        snapshot = make_snapshot([win("five_hour", 92.0, 8.0, RESET_FIVE)])
        result = scheduler.evaluate(snapshot)
        self.assertEqual(result["status"], "PRESSURE")
        self.assertIn("剩余 8%", result["reason"])

    def test_exhausted_single_window(self):
        """used=100 → EXHAUSTED，reason 指明窗口。"""
        snapshot = make_snapshot([win("five_hour", 100.0, 0.0, RESET_FIVE)])
        result = scheduler.evaluate(snapshot)
        self.assertEqual(result["status"], "EXHAUSTED")
        self.assertIn("five_hour", result["reason"])
        self.assertIn("耗尽", result["reason"])

    def test_unknown_when_no_usable_data(self):
        """缺 used（used/remaining 双 None）→ UNKNOWN（场景 7 的评估侧）。"""
        snapshot = make_snapshot([win("five_hour", None, None, RESET_FIVE)])
        result = scheduler.evaluate(snapshot)
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertIn("数据不完整", result["reason"])

    def test_unknown_fail_open_without_worse_windows(self):
        """规则锁定：一窗 UNKNOWN、其余 AVAILABLE → 总体 UNKNOWN。"""
        snapshot = make_snapshot([
            win("five_hour", None, None, RESET_FIVE),
            win("weekly", 10.0, 90.0, RESET_WEEKLY)])
        result = scheduler.evaluate(snapshot)
        self.assertEqual(result["status"], "UNKNOWN")

    def test_worse_status_wins_over_unknown(self):
        """有 EXHAUSTED/PRESSURE 时按最严重报告，坏窗明细仍可见。"""
        snapshot = make_snapshot([
            win("five_hour", None, None, RESET_FIVE),
            win("weekly", 100.0, 0.0, RESET_WEEKLY)])
        result = scheduler.evaluate(snapshot)
        self.assertEqual(result["status"], "EXHAUSTED")
        self.assertEqual(result["windows"][0]["status"], "UNKNOWN")
        self.assertEqual(result["windows"][1]["status"], "EXHAUSTED")

    def test_mixed_exhausted_and_pressure(self):
        """双窗混合：总体 EXHAUSTED，reason 同时给出两窗情况（场景 11）。"""
        snapshot = make_snapshot([
            win("five_hour", 100.0, 0.0, RESET_FIVE),
            win("weekly", 92.0, 8.0, RESET_WEEKLY)])
        result = scheduler.evaluate(snapshot)
        self.assertEqual(result["status"], "EXHAUSTED")
        self.assertIn("five_hour", result["reason"])
        self.assertIn("weekly 接近阈值（剩余 8%）", result["reason"])
        # 逐窗状态正确
        self.assertEqual(
            [w["status"] for w in result["windows"]],
            ["EXHAUSTED", "PRESSURE"])

    def test_windows_detail_shape_and_no_mutation(self):
        """windows 输出逐窗对应、键集固定；不改动输入 snapshot（纯函数）。"""
        snapshot = make_snapshot([
            win("five_hour", 100.0, 0.0, RESET_FIVE),
            win("weekly", 45.0, 55.0, RESET_WEEKLY)])
        before = json.dumps(snapshot, ensure_ascii=False)
        result = scheduler.evaluate(snapshot)
        self.assertEqual(json.dumps(snapshot, ensure_ascii=False), before)
        self.assertEqual(len(result["windows"]), 2)
        for window in result["windows"]:
            self.assertEqual(
                set(window.keys()),
                {"kind", "status", "used_percent",
                 "remaining_percent", "reset_at"})
        self.assertEqual(
            result["windows"][0],
            {"kind": "five_hour", "status": "EXHAUSTED",
             "used_percent": 100.0, "remaining_percent": 0.0,
             "reset_at": RESET_FIVE})
        self.assertEqual(result["windows"][1]["status"], "AVAILABLE")

    def test_remaining_derived_from_used_when_missing(self):
        """remaining_percent 缺失时用 100-used 推导参与判定（不透出推导值）。"""
        snapshot = make_snapshot([win("five_hour", 95.0, None, RESET_FIVE)])
        result = scheduler.evaluate(snapshot)
        self.assertEqual(result["status"], "PRESSURE")
        self.assertEqual(result["windows"][0]["remaining_percent"], None)

    def test_no_usable_remaining_is_unknown(self):
        """used / remaining 都不可得 → 该窗 UNKNOWN（不造数）。"""
        snapshot = make_snapshot([win("weekly", "n/a", None, RESET_WEEKLY)])
        result = scheduler.evaluate(snapshot)
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(result["windows"][0]["status"], "UNKNOWN")

    def test_bad_snapshot_shapes_are_unknown(self):
        """snapshot 非 dict / windows 非 list / windows 空 → UNKNOWN、[]。"""
        for bad in (None, 42, "x", [], {"windows": None},
                    {"windows": "x"}, {"windows": []}, {}):
            with self.subTest(snapshot=bad):
                result = scheduler.evaluate(bad)
                self.assertEqual(result["status"], "UNKNOWN")
                self.assertEqual(result["windows"], [])
                self.assertTrue(result["reason"])

    def test_non_dict_window_item_is_unknown(self):
        """windows 内非 dict 条目 → 整窗 UNKNOWN（fail-open，明细保留）。"""
        result = scheduler.evaluate(make_snapshot(["garbage"]))
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(result["windows"][0]["status"], "UNKNOWN")
        self.assertEqual(result["windows"][0]["kind"], None)

    def test_threshold_boundaries(self):
        """阈值边界：==threshold → PRESSURE；>threshold → AVAILABLE。"""
        at_threshold = make_snapshot(
            [win("five_hour", 90.0, 10.0, RESET_FIVE)])
        self.assertEqual(
            scheduler.evaluate(at_threshold)["status"], "PRESSURE")
        above_threshold = make_snapshot(
            [win("five_hour", 89.99, 10.01, RESET_FIVE)])
        self.assertEqual(
            scheduler.evaluate(above_threshold)["status"], "AVAILABLE")

    def test_custom_threshold(self):
        """自定义阈值参与判定：阈值 25 时剩余 20 → PRESSURE。"""
        snapshot = make_snapshot([win("five_hour", 80.0, 20.0, RESET_FIVE)])
        self.assertEqual(
            scheduler.evaluate(snapshot)["status"], "AVAILABLE")
        self.assertEqual(
            scheduler.evaluate(
                snapshot, pressure_threshold_percent=25.0)["status"],
            "PRESSURE")


# —— plan_resume：§29-§33 恢复规划 ——

class TestPlanResume(unittest.TestCase):

    def test_available_continues(self):
        """场景 1：AVAILABLE → continue，无 resume_at，不强制刷新。"""
        evaluation = scheduler.evaluate(make_snapshot([
            win("five_hour", 20.0, 80.0, RESET_FIVE),
            win("weekly", 40.0, 60.0, RESET_WEEKLY)]))
        result = scheduler.plan_resume(evaluation)
        self.assertEqual(result["action"], "continue")
        self.assertEqual(result["resume_at"], None)
        self.assertIs(result["force_refresh_before_resume"], False)
        self.assertTrue(result["reason"])

    def test_pressure_checkpoints(self):
        """场景 2：PRESSURE → checkpoint（§29 安全里程碑语义写入 reason）。"""
        evaluation = scheduler.evaluate(
            make_snapshot([win("five_hour", 92.0, 8.0, RESET_FIVE)]))
        result = scheduler.plan_resume(evaluation)
        self.assertEqual(result["action"], "checkpoint")
        self.assertEqual(result["resume_at"], None)
        self.assertIs(result["force_refresh_before_resume"], False)
        self.assertIn("checkpoint", result["reason"])
        self.assertIn("安全里程碑", result["reason"])
        self.assertIn("不开新的大实施阶段", result["reason"])

    def test_five_hour_exhausted_resumes_on_five_reset(self):
        """场景 3：five_hour EXHAUSTED + weekly AVAILABLE → five reset + grace。"""
        evaluation = scheduler.evaluate(make_snapshot([
            win("five_hour", 100.0, 0.0, RESET_FIVE),
            win("weekly", 40.0, 60.0, RESET_WEEKLY)]))
        result = scheduler.plan_resume(evaluation)
        self.assertEqual(result["action"], "resume_at")
        self.assertEqual(result["resume_at"], FIVE_PLUS_GRACE)
        self.assertIs(result["force_refresh_before_resume"], True)
        self.assertIn("强制刷新", result["reason"])

    def test_weekly_exhausted_resumes_on_weekly_reset(self):
        """场景 4：weekly EXHAUSTED + five_hour AVAILABLE → weekly reset + grace。"""
        evaluation = scheduler.evaluate(make_snapshot([
            win("five_hour", 20.0, 80.0, RESET_FIVE),
            win("weekly", 100.0, 0.0, RESET_WEEKLY)]))
        result = scheduler.plan_resume(evaluation)
        self.assertEqual(result["action"], "resume_at")
        self.assertEqual(result["resume_at"], WEEKLY_PLUS_GRACE)

    def test_both_exhausted_takes_latest_reset(self):
        """场景 5：双 EXHAUSTED → 较晚 reset + grace（§30 max 锚定）。

        手写 snapshot 里 five_hour 反而更晚：证明取的是 max 而非
        「weekly 恒最晚」的位置/名称约定；再以 both_exhausted.json
        fixture（weekly 更晚）锚定解析层→调度层衔接。
        """
        late_five = "2026-09-02T00:00:00Z"
        evaluation = scheduler.evaluate(make_snapshot([
            win("five_hour", 100.0, 0.0, late_five),
            win("weekly", 100.0, 0.0, RESET_WEEKLY)]))
        result = scheduler.plan_resume(evaluation)
        self.assertEqual(result["action"], "resume_at")
        self.assertEqual(result["resume_at"], "2026-09-02T00:05:00Z")
        # fixture 路径：both_exhausted.json，weekly reset 更晚
        snapshot = parse_fixture("both_exhausted.json")
        result = scheduler.plan_resume(scheduler.evaluate(snapshot))
        self.assertEqual(result["action"], "resume_at")
        self.assertEqual(result["resume_at"], WEEKLY_PLUS_GRACE)
        self.assertIs(result["force_refresh_before_resume"], True)

    def test_exhausted_without_reset_falls_back(self):
        """场景 6：EXHAUSTED 且 reset_at=None → periodic_fallback（不虚构）。"""
        evaluation = scheduler.evaluate(make_snapshot([
            win("five_hour", 100.0, 0.0, None),
            win("weekly", 40.0, 60.0, RESET_WEEKLY)]))
        result = scheduler.plan_resume(evaluation)
        self.assertEqual(result["action"], "periodic_fallback")
        self.assertEqual(result["resume_at"], None)
        self.assertIs(result["force_refresh_before_resume"], False)
        self.assertIn("不虚构", result["reason"])

    def test_unknown_falls_back(self):
        """场景 7：空窗口 / 缺 used → UNKNOWN → periodic_fallback。"""
        for snapshot in (make_snapshot([]),
                         {"windows": "not-a-list"},
                         make_snapshot([win("five_hour", None, None, None)]),
                         {}):
            with self.subTest(snapshot=snapshot):
                evaluation = scheduler.evaluate(snapshot)
                self.assertEqual(evaluation["status"], "UNKNOWN")
                result = scheduler.plan_resume(evaluation)
                self.assertEqual(result["action"], "periodic_fallback")
                self.assertEqual(result["resume_at"], None)
                self.assertIs(
                    result["force_refresh_before_resume"], False)

    def test_force_refresh_flag_only_on_resume_path(self):
        """场景 8：force_refresh_before_resume 仅 resume_at 路径为 True。"""
        available = scheduler.evaluate(make_snapshot(
            [win("five_hour", 20.0, 80.0, RESET_FIVE)]))
        pressure = scheduler.evaluate(make_snapshot(
            [win("five_hour", 92.0, 8.0, RESET_FIVE)]))
        exhausted = scheduler.evaluate(make_snapshot(
            [win("five_hour", 100.0, 0.0, RESET_FIVE)]))
        unknown = scheduler.evaluate(make_snapshot([]))
        self.assertIs(
            scheduler.plan_resume(available)["force_refresh_before_resume"],
            False)
        self.assertIs(
            scheduler.plan_resume(pressure)["force_refresh_before_resume"],
            False)
        self.assertIs(
            scheduler.plan_resume(exhausted)["force_refresh_before_resume"],
            True)
        self.assertIs(
            scheduler.plan_resume(unknown)["force_refresh_before_resume"],
            False)

    def test_grace_zero_and_now_injection(self):
        """场景 10：grace=0 + now 注入 → resume_at 精确等于 max(reset)。"""
        evaluation = scheduler.evaluate(make_snapshot([
            win("five_hour", 100.0, 0.0, RESET_FIVE),
            win("weekly", 100.0, 0.0, RESET_WEEKLY)]))
        fixed_now = datetime(2026, 8, 28, 5, 0, 0, tzinfo=timezone.utc)
        result = scheduler.plan_resume(
            evaluation, now=fixed_now, grace_seconds=0)
        self.assertEqual(result["resume_at"], RESET_WEEKLY)
        # now 也可以是 ISO 字符串；grace 可为非整数值
        result = scheduler.plan_resume(
            evaluation, now="2026-08-28T05:00:00Z", grace_seconds=30.5)
        self.assertEqual(result["resume_at"], "2026-09-01T12:20:30Z")
        # 缺省 now（真实时钟）不改变 resume_at 的确定值
        result = scheduler.plan_resume(evaluation)
        self.assertEqual(result["resume_at"], WEEKLY_PLUS_GRACE)

    def test_minimal_evaluation_with_snapshot_fallback(self):
        """手工最小 evaluation：无逐窗明细时用 snapshot 保守收集 reset。"""
        evaluation = {"status": "EXHAUSTED"}
        snapshot = make_snapshot([
            win("five_hour", 100.0, 0.0, RESET_FIVE),
            win("weekly", 100.0, 0.0, RESET_WEEKLY)])
        result = scheduler.plan_resume(evaluation, snapshot)
        self.assertEqual(result["action"], "resume_at")
        self.assertEqual(result["resume_at"], WEEKLY_PLUS_GRACE)
        # 无 snapshot 可用 → reset 未知，不虚构
        result = scheduler.plan_resume({"status": "EXHAUSTED"})
        self.assertEqual(result["action"], "periodic_fallback")
        self.assertIn("不虚构", result["reason"])

    def test_plan_resume_is_pure(self):
        """plan_resume 不改动 evaluation（纯函数）。"""
        evaluation = scheduler.evaluate(make_snapshot(
            [win("five_hour", 100.0, 0.0, RESET_FIVE)]))
        before = json.dumps(evaluation, ensure_ascii=False)
        scheduler.plan_resume(evaluation)
        self.assertEqual(json.dumps(evaluation, ensure_ascii=False), before)


# —— 参数校验（中文 ValueError） ——

class TestPlanResumeValidation(unittest.TestCase):

    def test_evaluation_must_be_dict(self):
        """evaluation 非 dict → ValueError（中文）。"""
        for bad in (None, [], "x", 42, ("AVAILABLE",)):
            with self.subTest(evaluation=bad):
                with self.assertRaises(ValueError) as ctx:
                    scheduler.plan_resume(bad)
                self.assertIn("JSON 对象", str(ctx.exception))

    def test_status_must_be_in_vocabulary(self):
        """status 缺失 / 不在四态 → ValueError（含合法取值清单）。"""
        for bad in ({}, {"status": None}, {"status": "BROKEN"},
                    {"status": "available"}):
            with self.subTest(evaluation=bad):
                with self.assertRaises(ValueError) as ctx:
                    scheduler.plan_resume(bad)
                message = str(ctx.exception)
                self.assertIn("status", message)
                for name in scheduler.QUOTA_STATUSES:
                    self.assertIn(name, message)

    def test_grace_seconds_must_be_non_negative_number(self):
        """grace_seconds 负数 / 非数值 / bool → ValueError。"""
        evaluation = {"status": "AVAILABLE"}
        for bad in (-1, -0.5, "300", None, True):
            with self.subTest(grace_seconds=bad):
                with self.assertRaises(ValueError):
                    scheduler.plan_resume(evaluation, grace_seconds=bad)

    def test_now_must_be_datetime_or_iso(self):
        """now 注入非法格式 → ValueError。"""
        evaluation = {"status": "AVAILABLE"}
        for bad in ("not-a-time", "2026-13-40T99:00:00Z", 12345):
            with self.subTest(now=bad):
                with self.assertRaises(ValueError):
                    scheduler.plan_resume(evaluation, now=bad)
        # 合法注入：datetime 与 ISO 字符串
        scheduler.plan_resume(
            evaluation, now=datetime.now(timezone.utc))
        scheduler.plan_resume(evaluation, now=RESET_FIVE)

    def test_evaluate_threshold_must_be_number(self):
        """evaluate 的阈值参数非有限数值 → ValueError。"""
        snapshot = make_snapshot([win("five_hour", 50.0, 50.0, RESET_FIVE)])
        for bad in ("10", None, True, float("nan"), float("inf")):
            with self.subTest(threshold=bad):
                with self.assertRaises(ValueError):
                    scheduler.evaluate(
                        snapshot, pressure_threshold_percent=bad)


# —— runtime.state：可选顶层 quota 块校验（§39，场景 13） ——

class TestStateQuotaBlock(unittest.TestCase):

    @staticmethod
    def _state_with_quota(quota):
        st = state.new_task_state("t-quota-1", "目标", {"mode": "solo"})
        st["quota"] = quota
        return st

    def test_valid_quota_blocks_pass(self):
        """合法 quota 块通过：四态 status / 可空字段 / 双窗 dict。"""
        for status in state.QUOTA_STATUSES:
            with self.subTest(status=status):
                st = self._state_with_quota({
                    "status": status, "source": "live",
                    "provider": "bigmodel",
                    "last_checked": "2026-08-28T05:00:00Z",
                    "five_hour": {"used_percent": 82.5},
                    "weekly": None})
                self.assertEqual(state.validate_state(st), [])

    def test_quota_absent_still_valid(self):
        """不写 quota 块依然合法（可选块，向前兼容）。"""
        self.assertEqual(state.validate_state(
            state.new_task_state("t-quota-1", "目标", {"mode": "solo"})), [])

    def test_quota_must_be_dict(self):
        """quota 非 dict（含 None）→ 报「quota 必须是 JSON 对象」。"""
        for bad in (None, [], "x", 42):
            with self.subTest(quota=bad):
                errors = state.validate_state(self._state_with_quota(bad))
                self.assertTrue(
                    any("quota 必须是 JSON 对象" in e for e in errors), bad)

    def test_quota_status_vocabulary(self):
        """status 在词汇表外 → 报 quota.status 枚举错；None 视为未评估。"""
        st = self._state_with_quota({"status": "BROKEN"})
        errors = state.validate_state(st)
        self.assertTrue(any("quota.status" in e for e in errors))
        st = self._state_with_quota({"status": None})
        self.assertEqual(state.validate_state(st), [])

    def test_quota_string_fields(self):
        """source / provider / last_checked：非 None 时必须是非空 str。"""
        base = {"status": "AVAILABLE"}
        st = self._state_with_quota(dict(base, source=""))
        self.assertTrue(
            any("quota.source" in e for e in state.validate_state(st)))
        st = self._state_with_quota(dict(base, last_checked=123))
        self.assertTrue(any(
            "quota.last_checked" in e for e in state.validate_state(st)))
        st = self._state_with_quota(dict(base, provider=None))
        self.assertEqual(state.validate_state(st), [])

    def test_quota_window_fields_must_be_dict(self):
        """five_hour / weekly 非 None 时必须是 dict。"""
        base = {"status": "AVAILABLE"}
        st = self._state_with_quota(dict(base, five_hour="x"))
        self.assertTrue(
            any("quota.five_hour" in e for e in state.validate_state(st)))
        st = self._state_with_quota(dict(base, weekly=[1, 2]))
        self.assertTrue(
            any("quota.weekly" in e for e in state.validate_state(st)))
        st = self._state_with_quota(dict(base, weekly={}))
        self.assertEqual(state.validate_state(st), [])

    def test_no_secret_field_expectations(self):
        """quota 块不要求任何凭证/秘密字段（契约：永不含凭证）。"""
        st = self._state_with_quota({"status": "UNKNOWN"})
        self.assertEqual(state.validate_state(st), [])


if __name__ == "__main__":
    unittest.main()
