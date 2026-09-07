#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""runtime.quota.parser 单元测试（v2 工作块 B5.1，升级指南 §44）。

运行：
    python3 -m unittest tests.test_quota_parser -v

全部离线：11 个 §44 fixture（tests/fixtures/quota/）逐一断言 +
语义字段判窗 / 回退 / malformed / 助手函数边界用例，不发任何网络请求。
"""

import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "glm-conductor"))
from runtime.quota import parser
from runtime.quota.provider import QuotaProviderError

# —— fixture 装置 ——

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "quota"

# 测试用统一 provider / fetched_at（原样进入 snapshot，断言透传）
PROVIDER = "bigmodel"
FETCHED_AT = "2026-08-28T05:00:00Z"

# fixture nextResetTime（epoch ms）对应的标准化 ISO 串
RESET_FIVE = "2026-08-28T06:45:02Z"    # 1787899502000
RESET_WEEKLY = "2026-09-01T12:20:00Z"  # 1788265200000
RESET_MCP = "2026-08-28T12:20:00Z"     # 1787919600000（TIME_LIMIT MCP 通道）


def load_fixture(name):
    """读取 tests/fixtures/quota/<name> 并 json.load。"""
    with open(str(FIXTURE_DIR / name), "r", encoding="utf-8") as fh:
        return json.load(fh)


def parse_fixture(name, **kwargs):
    """加载 fixture 并解析为标准化 snapshot。"""
    return parser.parse_quota_body(
        load_fixture(name),
        provider=kwargs.pop("provider", PROVIDER),
        fetched_at=kwargs.pop("fetched_at", FETCHED_AT))


def snapshot_common_asserts(test, snapshot, plan_generation, plan_level,
                            window_kinds, provider=PROVIDER,
                            fetched_at=FETCHED_AT):
    """所有成功解析共有的断言：顶层形状 / status 恒 None / 透传字段。"""
    test.assertEqual(
        set(snapshot.keys()),
        {"provider", "plan_generation", "plan_level", "fetched_at",
         "status", "windows"})
    test.assertEqual(snapshot["status"], None)  # 解析层恒 None（§28 属调度器）
    test.assertEqual(snapshot["provider"], provider)
    test.assertEqual(snapshot["fetched_at"], fetched_at)
    test.assertEqual(snapshot["plan_generation"], plan_generation)
    test.assertEqual(snapshot["plan_level"], plan_level)
    test.assertEqual([w["kind"] for w in snapshot["windows"]], window_kinds)
    for window in snapshot["windows"]:
        test.assertEqual(
            set(window.keys()),
            {"kind", "used_percent", "remaining_percent", "reset_at"})
        # 不透出任何原始响应字段
        test.assertNotIn("usageDetails", window)
        test.assertNotIn("currentValue", window)
        test.assertNotIn("usage", window)
        test.assertNotIn("percentage", window)
        test.assertNotIn("nextResetTime", window)


# —— §44 fixture 逐一断言 ——

class TestParseFixtures(unittest.TestCase):

    def test_legacy_tokens_limit(self):
        """TOKENS_LIMIT 双窗 v2 形态：双窗 + percentage + nextResetTime。"""
        snapshot = parse_fixture("legacy_tokens_limit.json")
        snapshot_common_asserts(
            self, snapshot, "v2", "pro", ["five_hour", "weekly"])
        five, weekly = snapshot["windows"]
        self.assertEqual(five["used_percent"], 82.5)
        self.assertEqual(five["remaining_percent"], 17.5)
        self.assertEqual(five["reset_at"], RESET_FIVE)
        self.assertEqual(weekly["used_percent"], 45.0)
        self.assertEqual(weekly["remaining_percent"], 55.0)
        self.assertEqual(weekly["reset_at"], RESET_WEEKLY)

    def test_v3_credit_limit(self):
        """CREDIT_LIMIT 双窗：plan_generation=v3（type 只影响代际标签）。"""
        snapshot = parse_fixture("v3_credit_limit.json")
        snapshot_common_asserts(
            self, snapshot, "v3", "max", ["five_hour", "weekly"])
        five, weekly = snapshot["windows"]
        self.assertEqual(five["used_percent"], 66.0)
        self.assertEqual(five["reset_at"], RESET_FIVE)
        self.assertEqual(weekly["used_percent"], 10.5)
        self.assertEqual(weekly["reset_at"], RESET_WEEKLY)

    def test_time_limit_entries_no_window(self):
        """TIME_LIMIT 条目（unit=4/number=1）不映射任何窗口。"""
        snapshot = parse_fixture("time_limit_entries.json")
        snapshot_common_asserts(self, snapshot, "v2", "pro", ["five_hour"])
        five = snapshot["windows"][0]
        self.assertEqual(five["used_percent"], 20.0)
        self.assertEqual(five["reset_at"], RESET_FIVE)

    def test_five_hour_only_weekly_optional(self):
        """lite 实测形态：仅 five_hour + TIME_LIMIT，无 weekly 不报错。"""
        snapshot = parse_fixture("five_hour_only.json")
        snapshot_common_asserts(self, snapshot, "v2", "lite", ["five_hour"])
        five = snapshot["windows"][0]
        self.assertEqual(five["used_percent"], 12.5)
        self.assertEqual(five["remaining_percent"], 87.5)
        self.assertEqual(five["reset_at"], RESET_FIVE)

    def test_weekly_only(self):
        """只有 weekly 窗：单窗形态合法。"""
        snapshot = parse_fixture("weekly_only.json")
        snapshot_common_asserts(self, snapshot, "v2", "pro", ["weekly"])
        weekly = snapshot["windows"][0]
        self.assertEqual(weekly["used_percent"], 33.33)
        self.assertEqual(weekly["remaining_percent"], 66.67)
        self.assertEqual(weekly["reset_at"], RESET_WEEKLY)

    def test_both_exhausted(self):
        """双窗 percentage=100：used=100.0 / remaining=0.0。"""
        snapshot = parse_fixture("both_exhausted.json")
        snapshot_common_asserts(
            self, snapshot, "v2", "max", ["five_hour", "weekly"])
        for window in snapshot["windows"]:
            self.assertEqual(window["used_percent"], 100.0)
            self.assertEqual(window["remaining_percent"], 0.0)
        self.assertEqual(snapshot["windows"][0]["reset_at"], RESET_FIVE)
        self.assertEqual(snapshot["windows"][1]["reset_at"], RESET_WEEKLY)

    def test_missing_next_reset(self):
        """five_hour 条目无 nextResetTime：窗口保留但 reset_at=None。"""
        snapshot = parse_fixture("missing_next_reset.json")
        snapshot_common_asserts(self, snapshot, "v2", "lite", ["five_hour"])
        five = snapshot["windows"][0]
        self.assertEqual(five["used_percent"], 8.0)
        self.assertEqual(five["remaining_percent"], 92.0)
        self.assertEqual(five["reset_at"], None)

    def test_auth_failure_is_malformed(self):
        """401 错误体（无 data/limits）：解析层分类 malformed。"""
        with self.assertRaises(QuotaProviderError) as ctx:
            parse_fixture("auth_failure.json")
        self.assertEqual(ctx.exception.kind, "malformed")

    def test_endpoint_unavailable_is_malformed(self):
        """5xx 错误体（无 data/limits）：解析层分类 malformed
        （HTTP 状态 → unavailable 的映射属适配器，不在解析层）。"""
        with self.assertRaises(QuotaProviderError) as ctx:
            parse_fixture("endpoint_unavailable.json")
        self.assertEqual(ctx.exception.kind, "malformed")

    def test_malformed_structure(self):
        """合法 JSON 但 data 无 limits：malformed。"""
        with self.assertRaises(QuotaProviderError) as ctx:
            parse_fixture("malformed.json")
        self.assertEqual(ctx.exception.kind, "malformed")

    def test_unknown_additive_fields_ignored(self):
        """大量未知加性字段（顶层/中间层/条目级）不影响解析结果。"""
        snapshot = parse_fixture("unknown_additive_fields.json")
        baseline = parse_fixture("legacy_tokens_limit.json")
        snapshot_common_asserts(
            self, snapshot, "v2", "pro", ["five_hour", "weekly"])
        self.assertEqual(snapshot["windows"], baseline["windows"])


# —— 判窗语义（§26：语义字段，与 type / 位置无关） ——

class TestWindowSemantics(unittest.TestCase):

    @staticmethod
    def _limits_body(limits, level="pro"):
        return {"success": True, "data": {"level": level, "limits": limits}}

    def test_semantic_fields_regardless_of_type(self):
        """unit=3/number=5 判 five_hour 与 type 无关（TIME/CREDIT 同样判窗）。"""
        for item_type in ("TOKENS_LIMIT", "CREDIT_LIMIT", "TIME_LIMIT",
                          "SOMETHING_NEW"):
            body = self._limits_body([{
                "type": item_type, "unit": 3, "number": 5,
                "percentage": 25, "nextResetTime": 1787899502000}])
            snapshot = parser.parse_quota_body(
                body, provider=PROVIDER, fetched_at=FETCHED_AT)
            with self.subTest(item_type=item_type):
                self.assertEqual(
                    [w["kind"] for w in snapshot["windows"]], ["five_hour"])

    def test_duplicate_kind_first_kept(self):
        """同 kind 重复条目：保留第一条，后续跳过。"""
        body = self._limits_body([
            {"type": "TOKENS_LIMIT", "unit": 3, "number": 5,
             "percentage": 10, "nextResetTime": 1787899502000},
            {"type": "TOKENS_LIMIT", "unit": 3, "number": 5,
             "percentage": 99, "nextResetTime": 1788265200000},
        ])
        snapshot = parser.parse_quota_body(
            body, provider=PROVIDER, fetched_at=FETCHED_AT)
        self.assertEqual([w["kind"] for w in snapshot["windows"]],
                         ["five_hour"])
        self.assertEqual(snapshot["windows"][0]["used_percent"], 10.0)

    def test_unknown_unit_number_combos_skipped(self):
        """未知 unit/number 组合与缺语义字段条目：跳过，容忍不报错。"""
        body = self._limits_body([
            {"type": "TOKENS_LIMIT", "unit": 7, "number": 2, "percentage": 1},
            {"type": "TOKENS_LIMIT", "percentage": 2},                 # 缺语义字段
            {"type": "TOKENS_LIMIT", "unit": "3", "number": 5},        # 字符串 unit
            {"type": "TOKENS_LIMIT", "unit": 3, "number": True},       # bool number
            {"type": "TOKENS_LIMIT", "unit": None, "number": 5},       # None unit
            "not-a-dict",                                              # 非 dict 条目
            {"type": "TOKENS_LIMIT", "unit": 3, "number": 5,
             "percentage": 40, "nextResetTime": 1787899502000},
        ])
        snapshot = parser.parse_quota_body(
            body, provider=PROVIDER, fetched_at=FETCHED_AT)
        self.assertEqual([w["kind"] for w in snapshot["windows"]],
                         ["five_hour"])
        self.assertEqual(snapshot["windows"][0]["used_percent"], 40.0)

    def test_window_order_five_hour_first(self):
        """windows 恒 five_hour 在前 weekly 在后（与条目数组顺序无关）。"""
        body = self._limits_body([
            {"type": "TOKENS_LIMIT", "unit": 6, "number": 1,
             "percentage": 30, "nextResetTime": 1788265200000},
            {"type": "TOKENS_LIMIT", "unit": 3, "number": 5,
             "percentage": 10, "nextResetTime": 1787899502000},
        ])
        snapshot = parser.parse_quota_body(
            body, provider=PROVIDER, fetched_at=FETCHED_AT)
        self.assertEqual([w["kind"] for w in snapshot["windows"]],
                         ["five_hour", "weekly"])

    def test_percentage_missing_remaining_fallback(self):
        """percentage 缺失走 remaining 回退：remaining=30.5 → used=69.5。"""
        body = self._limits_body([{
            "type": "TOKENS_LIMIT", "unit": 3, "number": 5,
            "remaining": 30.5, "nextResetTime": 1787899502000}])
        snapshot = parser.parse_quota_body(
            body, provider=PROVIDER, fetched_at=FETCHED_AT)
        window = snapshot["windows"][0]
        self.assertEqual(window["used_percent"], 69.5)
        self.assertEqual(window["remaining_percent"], 30.5)

    def test_remaining_out_of_range_used_none(self):
        """remaining 越界（>100）：回退失败 → used/remaining 为 None，窗口保留。"""
        body = self._limits_body([{
            "type": "TOKENS_LIMIT", "unit": 3, "number": 5,
            "remaining": 920000, "nextResetTime": 1787899502000}])
        snapshot = parser.parse_quota_body(
            body, provider=PROVIDER, fetched_at=FETCHED_AT)
        window = snapshot["windows"][0]
        self.assertEqual(window["kind"], "five_hour")
        self.assertEqual(window["used_percent"], None)
        self.assertEqual(window["remaining_percent"], None)
        self.assertEqual(window["reset_at"], RESET_FIVE)

    def test_no_semantic_match_yields_empty_windows(self):
        """limits 非空但无任何条目映射窗口：snapshot 合法，windows=[]。"""
        body = self._limits_body([
            {"type": "TIME_LIMIT", "unit": 4, "number": 1, "percentage": 40}])
        snapshot = parser.parse_quota_body(
            body, provider=PROVIDER, fetched_at=FETCHED_AT)
        self.assertEqual(snapshot["windows"], [])
        self.assertEqual(snapshot["plan_generation"], None)

    def test_provider_and_fetched_at_verbatim(self):
        """provider / fetched_at 原样进入 snapshot，不做任何变换。"""
        body = self._limits_body([{
            "type": "TOKENS_LIMIT", "unit": 3, "number": 5, "percentage": 1}])
        snapshot = parser.parse_quota_body(
            body, provider="prov_underscore-1", fetched_at="fetch-t0-marker")
        self.assertEqual(snapshot["provider"], "prov_underscore-1")
        self.assertEqual(snapshot["fetched_at"], "fetch-t0-marker")

    def test_snapshot_shape_has_no_raw_fields(self):
        """snapshot 顶层与窗口键集固定，不透出原始响应字段与秘密。"""
        body = self._limits_body([{
            "type": "TOKENS_LIMIT", "unit": 3, "number": 5,
            "percentage": 25, "usageDetails": [{"x": 1}],
            "apiKey": "should-not-leak", "nextResetTime": 1787899502000}])
        snapshot = parser.parse_quota_body(
            body, provider=PROVIDER, fetched_at=FETCHED_AT)
        serialized = json.dumps(snapshot, ensure_ascii=False)
        self.assertNotIn("usageDetails", serialized)
        self.assertNotIn("should-not-leak", serialized)
        snapshot_common_asserts(self, snapshot, "v2", "pro", ["five_hour"])


# —— malformed 输入路径 ——

class TestMalformedInput(unittest.TestCase):

    def test_body_not_dict_is_malformed(self):
        """body 非 dict（list/str/int/None/bool）→ malformed。"""
        for bad in ([1, 2], "data", 42, None, True):
            with self.subTest(body=bad):
                with self.assertRaises(QuotaProviderError) as ctx:
                    parser.parse_quota_body(
                        bad, provider=PROVIDER, fetched_at=FETCHED_AT)
                self.assertEqual(ctx.exception.kind, "malformed")

    def test_data_not_dict_and_body_without_limits(self):
        """data 非 dict 且 body 无 limits 键 → malformed。"""
        for body in ({"data": 5}, {"data": "x"}, {"data": None}, {}):
            with self.subTest(body=body):
                with self.assertRaises(QuotaProviderError) as ctx:
                    parser.parse_quota_body(
                        body, provider=PROVIDER, fetched_at=FETCHED_AT)
                self.assertEqual(ctx.exception.kind, "malformed")

    def test_bare_limits_body_without_data_envelope(self):
        """body 自身含 limits（无 data envelope）→ 直接用 body 解析。"""
        body = {"limits": [{
            "type": "TOKENS_LIMIT", "unit": 3, "number": 5,
            "percentage": 15, "nextResetTime": 1787899502000}]}
        snapshot = parser.parse_quota_body(
            body, provider=PROVIDER, fetched_at=FETCHED_AT)
        self.assertEqual([w["kind"] for w in snapshot["windows"]],
                         ["five_hour"])
        self.assertEqual(snapshot["plan_level"], None)

    def test_limits_not_list_or_empty_is_malformed(self):
        """limits 非 list / 空数组 → malformed。"""
        for bad_limits in ({"a": 1}, "x", 5, None, []):
            body = {"data": {"level": "pro", "limits": bad_limits}}
            with self.subTest(limits=bad_limits):
                with self.assertRaises(QuotaProviderError) as ctx:
                    parser.parse_quota_body(
                        body, provider=PROVIDER, fetched_at=FETCHED_AT)
                self.assertEqual(ctx.exception.kind, "malformed")


# —— coerce_percent 边界 ——

class TestCoercePercent(unittest.TestCase):

    def test_valid_values(self):
        cases = ((0, 0.0), (100, 100.0), (45, 45.0), (12.5, 12.5),
                 (82.456, 82.46), (0.004, 0.0), (99.999, 100.0))
        for value, expected in cases:
            with self.subTest(value=value):
                result = parser.coerce_percent(value)
                self.assertIsInstance(result, float)
                self.assertEqual(result, expected)

    def test_invalid_values_return_none(self):
        for bad in (None, "50", "", True, False, -1, -0.001, 100.5,
                    10 ** 9, float("nan"), float("inf"), float("-inf"),
                    [50], {"p": 50}):
            with self.subTest(value=bad):
                self.assertEqual(parser.coerce_percent(bad), None)


# —— epoch_ms_to_iso 边界 ——

class TestEpochMsToIso(unittest.TestCase):

    def test_valid_values(self):
        self.assertEqual(parser.epoch_ms_to_iso(1787899502000), RESET_FIVE)
        self.assertEqual(parser.epoch_ms_to_iso(1787899502000.0), RESET_FIVE)
        # 毫秒截断到秒精度
        self.assertEqual(parser.epoch_ms_to_iso(1787899502999), RESET_FIVE)
        self.assertEqual(parser.epoch_ms_to_iso(1788265200000), RESET_WEEKLY)

    def test_roundtrip_parseable(self):
        """合法值输出可被 ISO 解析回同一 UTC 时刻。"""
        raw = 1787899502000
        text = parser.epoch_ms_to_iso(raw)
        parsed = datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc)
        self.assertEqual(int(parsed.timestamp() * 1000), raw)

    def test_invalid_values_return_none(self):
        for bad in (None, "1787899502000", "", True, False, 0, -1,
                    -1787899502000, float("nan"), float("inf"),
                    10 ** 15):  # 超出 datetime 表示范围
            with self.subTest(value=bad):
                self.assertEqual(parser.epoch_ms_to_iso(bad), None)


if __name__ == "__main__":
    unittest.main()
