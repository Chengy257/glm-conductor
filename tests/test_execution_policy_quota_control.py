#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""execution_policy.quota_control 可选子块单元测试（v2.2 M1，wu-22-01）。

锚定对象：主计划 §5.1（quota_control 阈值配置）/ §23（向后兼容：缺块
合法）；决策记录 D5（阈值配置落点 execution_policy.quota_control 子块，
0 <= draining < pressure <= 100）。v2.2 M1a（WU-22-01a，决策记录 D15-d）
增补 bridge_interval_minutes（Persistent Wake Bridge 固定间隔，默认
60，非 bool int 且 5-1440）。覆盖：
  - DEFAULT_QUOTA_CONTROL / 默认块内 quota_control 逐字段断言；
  - default_execution_policy 深隔离（改写返回值不影响模块常量）；
  - POLICY_SUB_BLOCKS 保持四必填不变（quota_control 不在其中）；
  - validate_execution_policy：缺块合法 / 自定义合法 / 阈值约束 /
    负数·bool·非数值·非有限值拒绝 / bridge 间隔越界·bool·非整数拒绝 /
    缺键按默认解释 / 未知键忽略；
  - default_quota_control 容错读（缺块/形状非法/坏键按默认，合法值
    透传，全新拷贝）；
  - setter（set_parallel/set_resume）不重置 quota_control；
  - 经 state.validate_state 规则 8.7 聚合（execution_policy.quota_control
    前缀）+ new_task_state 默认块。

仅 Python 3 标准库（unittest），零第三方依赖，零 I/O（纯函数为主）。

运行：
    cd <repo_root> && python3 -m unittest tests.test_execution_policy_quota_control -v
"""

import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "glm-conductor"))
from runtime import execution_policy, state

TS = "2026-09-01T12:00:00+00:00"
# §5.1 / D5 + D15-d 冻结默认原文（逐字段锚定）
FROZEN_DEFAULT_QUOTA_CONTROL = {
    "pressure_percent": 35.0,
    "draining_percent": 20.0,
    "bridge_interval_minutes": 60,
}


def make_policy(**overrides) -> dict:
    """默认合法 policy 的可定制拷贝（overrides 按子块名 patch 叶子）。"""
    policy = execution_policy.default_execution_policy()
    for name, patch in overrides.items():
        policy[name].update(patch)
    return policy


def qc_errors(policy):
    """只取 quota_control 相关错误（聚焦本单元断言，噪音隔离）。"""
    return [e for e in execution_policy.validate_execution_policy(policy)
            if "quota_control" in e]


# —— 默认块与常量 ——

class DefaultQuotaControlBlockTest(unittest.TestCase):

    def test_frozen_default_exact(self):
        # 冻结默认逐字段相等（35/20 + D15-d 的 60；百分比数值语义由
        # default_quota_control 口径统一为浮点）
        self.assertEqual(execution_policy.DEFAULT_QUOTA_CONTROL,
                         FROZEN_DEFAULT_QUOTA_CONTROL)
        self.assertEqual(execution_policy.default_execution_policy()
                         ["quota_control"],
                         FROZEN_DEFAULT_QUOTA_CONTROL)

    def test_policy_sub_blocks_stay_four(self):
        # quota_control 是可选子块：绝不进入 POLICY_SUB_BLOCKS 四必填
        self.assertEqual(
            execution_policy.POLICY_SUB_BLOCKS,
            ("worker_execution", "parallelism", "continuity",
             "authorization"))
        self.assertIn("quota_control",
                      execution_policy.DEFAULT_EXECUTION_POLICY)

    def test_default_policy_passes_validation(self):
        self.assertEqual(
            execution_policy.validate_execution_policy(
                execution_policy.default_execution_policy()), [])

    def test_deep_isolation_of_returned_block(self):
        # 深隔离：改写返回值（含 quota_control 内层）不影响模块常量
        first = execution_policy.default_execution_policy()
        first["quota_control"]["pressure_percent"] = 99.0
        first["quota_control"]["future_nested"] = {"x": 1}
        first["parallelism"]["max_workers"] = 4
        self.assertIsNot(first["quota_control"],
                         execution_policy.DEFAULT_EXECUTION_POLICY[
                             "quota_control"])
        self.assertEqual(
            execution_policy.DEFAULT_EXECUTION_POLICY["quota_control"],
            FROZEN_DEFAULT_QUOTA_CONTROL)
        self.assertEqual(
            execution_policy.DEFAULT_QUOTA_CONTROL,
            FROZEN_DEFAULT_QUOTA_CONTROL)
        self.assertEqual(
            execution_policy.default_execution_policy(),
            execution_policy.default_execution_policy())

    def test_default_constants_not_aliased(self):
        # DEFAULT_EXECUTION_POLICY["quota_control"] 与 DEFAULT_QUOTA_CONTROL
        # 是解耦拷贝：改写前者不波及后者（构造期一次性拷贝）
        self.assertIsNot(
            execution_policy.DEFAULT_EXECUTION_POLICY["quota_control"],
            execution_policy.DEFAULT_QUOTA_CONTROL)

    def test_new_task_state_carries_default_block(self):
        st = state.new_task_state("qc-task-1a2b3c", "阈值默认",
                                  {"mode": "solo"})
        self.assertEqual(st["execution_policy"]["quota_control"],
                         FROZEN_DEFAULT_QUOTA_CONTROL)
        self.assertEqual(state.validate_state(st), [])


# —— validate_execution_policy：quota_control ——

class ValidateQuotaControlTest(unittest.TestCase):

    def test_missing_block_is_valid(self):
        # 缺块合法（legacy / §23.1 按默认 35/20 解释）
        policy = make_policy()
        del policy["quota_control"]
        self.assertEqual(execution_policy.validate_execution_policy(policy),
                         [])

    def test_default_and_custom_valid(self):
        self.assertEqual(qc_errors(make_policy()), [])
        for pressure, draining in ((40, 25), (50, 49), (100, 0),
                                   (35.5, 20.5), (36, 35.5)):
            policy = make_policy(quota_control={
                "pressure_percent": pressure, "draining_percent": draining})
            self.assertEqual(qc_errors(policy), [], (pressure, draining))

    def test_empty_block_is_valid(self):
        # 空块 = 全默认配置
        policy = make_policy(quota_control={})
        self.assertEqual(qc_errors(policy), [])

    def test_draining_not_below_pressure_rejected(self):
        # 冻结约束 draining < pressure（含相等）
        for pressure, draining in ((20, 20), (30, 40), (10, 11),
                                   (100, 100)):
            policy = make_policy(quota_control={
                "pressure_percent": pressure, "draining_percent": draining})
            errors = qc_errors(policy)
            self.assertTrue(any(
                "阈值约束被违反" in e and "pressure_percent" in e
                and "draining_percent" in e for e in errors),
                (pressure, draining, errors))

    def test_out_of_range_rejected(self):
        # 0 <= x <= 100 由合并判定覆盖：单侧越界同样落网
        for bad in (-1, -0.5, 101, 1000):
            policy = make_policy(quota_control={
                "pressure_percent": bad, "draining_percent": 20})
            self.assertTrue(qc_errors(policy), repr(bad))
            policy = make_policy(quota_control={
                "pressure_percent": 50, "draining_percent": bad})
            self.assertTrue(qc_errors(policy), repr(bad))

    def test_bool_rejected(self):
        # bool 是 int 子类，但 True/False 不得充当百分比阈值
        for bad in (True, False):
            policy = make_policy(quota_control={"pressure_percent": bad})
            errors = qc_errors(policy)
            self.assertTrue(any(
                e == "quota_control.pressure_percent 必须是有限数值"
                     "（bool 拒绝），得到 %r" % bad for e in errors),
                repr(bad))
            policy = make_policy(quota_control={"draining_percent": bad})
            self.assertTrue(any(
                e.startswith("quota_control.draining_percent")
                for e in qc_errors(policy)), repr(bad))

    def test_non_numeric_and_non_finite_rejected(self):
        for bad in ("35", None, [35], {"x": 1}, float("nan"),
                    float("inf"), float("-inf")):
            policy = make_policy(quota_control={"pressure_percent": bad})
            self.assertTrue(any(
                e.startswith("quota_control.pressure_percent")
                for e in qc_errors(policy)), repr(bad))
            policy = make_policy(quota_control={"draining_percent": bad})
            self.assertTrue(any(
                e.startswith("quota_control.draining_percent")
                for e in qc_errors(policy)), repr(bad))

    def test_non_dict_block_rejected(self):
        for bad in ("x", 42, True, [35]):
            policy = make_policy()
            policy["quota_control"] = bad  # 直接注入非 dict 块
            self.assertTrue(any(
                e == "quota_control 必须是 JSON 对象"
                for e in qc_errors(policy)), repr(bad))

    def test_partial_block_judged_against_defaults(self):
        # 缺键按默认解释（与 default_quota_control 消费口径一致）：
        # 合法半块放行；越界半块按生效配置落网
        good = make_policy(quota_control={"pressure_percent": 50})
        self.assertEqual(qc_errors(good), [])
        good = make_policy(quota_control={"draining_percent": 30})
        self.assertEqual(qc_errors(good), [])
        bad = make_policy(quota_control={"draining_percent": 50})
        self.assertTrue(any("阈值约束被违反" in e for e in qc_errors(bad)))
        bad = make_policy(quota_control={"pressure_percent": 15})
        self.assertTrue(any("阈值约束被违反" in e for e in qc_errors(bad)))

    def test_unknown_keys_ignored(self):
        policy = make_policy(quota_control={"future_field": 1})
        self.assertEqual(qc_errors(policy), [])

    def test_aggregates_errors_not_shortcircuit(self):
        policy = make_policy(quota_control={
            "pressure_percent": "x", "draining_percent": True})
        errors = qc_errors(policy)
        self.assertTrue(any(
            e.startswith("quota_control.pressure_percent") for e in errors))
        self.assertTrue(any(
            e.startswith("quota_control.draining_percent") for e in errors))


# —— bridge_interval_minutes（v2.2 M1a，D15-d Persistent Wake Bridge） ——

class BridgeIntervalMinutesTest(unittest.TestCase):

    def test_default_is_sixty(self):
        # D15-d 保守默认 60；默认块与缺块 policy 均按 60 解释
        self.assertEqual(
            execution_policy.DEFAULT_QUOTA_CONTROL[
                "bridge_interval_minutes"], 60)
        self.assertEqual(
            execution_policy.default_quota_control(None)[
                "bridge_interval_minutes"], 60)
        self.assertEqual(qc_errors(make_policy()), [])

    def test_custom_values_valid(self):
        # 自定义合法值（含下界/上界端点）放行，且经 default_quota_control
        # 原样透传
        for good in (5, 30, 60, 90, 1440):
            policy = make_policy(quota_control={
                "bridge_interval_minutes": good})
            self.assertEqual(qc_errors(policy), [], good)
            self.assertEqual(
                execution_policy.default_quota_control(policy)[
                    "bridge_interval_minutes"], good)

    def test_out_of_range_rejected(self):
        # 5-1440 闭区间：越界即拒；缺省按默认 60 合法
        for bad in (4, 1441, 0, -30, 2880):
            policy = make_policy(quota_control={
                "bridge_interval_minutes": bad})
            errors = qc_errors(policy)
            self.assertTrue(any(
                e == "quota_control.bridge_interval_minutes 必须是 "
                     "5-1440 的整数（bool 拒绝），得到 %r" % bad
                for e in errors), repr(bad))

    def test_bool_and_non_int_rejected(self):
        # bool 是 int 子类，True/False 不得充当分钟数；非整数一律拒
        for bad in (True, False, "60", 90.5, None, [60], {"m": 60}):
            policy = make_policy(quota_control={
                "bridge_interval_minutes": bad})
            errors = qc_errors(policy)
            self.assertTrue(any(
                e.startswith(
                    "quota_control.bridge_interval_minutes")
                for e in errors), repr(bad))

    def test_legacy_policy_without_key_is_valid(self):
        # legacy policy 无该键：缺块 / 缺键均合法，合并后按默认 60 解释
        policy = make_policy()
        del policy["quota_control"]
        self.assertEqual(execution_policy.validate_execution_policy(policy),
                         [])
        self.assertEqual(
            execution_policy.default_quota_control(policy),
            FROZEN_DEFAULT_QUOTA_CONTROL)
        policy = make_policy(quota_control={"pressure_percent": 40})
        self.assertEqual(qc_errors(policy), [])
        self.assertEqual(
            execution_policy.default_quota_control(policy)[
                "bridge_interval_minutes"], 60)

    def test_state_aggregation_reports_bad_interval_with_prefix(self):
        st = state.new_task_state("qc-task-1a2b3c", "间隔越界",
                                  {"mode": "solo"})
        st["execution_policy"]["quota_control"] = {
            "bridge_interval_minutes": 1441}
        errors = state.validate_state(st)
        self.assertTrue(any(
            e.startswith(
                "execution_policy.quota_control.bridge_interval_minutes")
            for e in errors))


# —— default_quota_control 容错读 ——

class DefaultQuotaControlReaderTest(unittest.TestCase):

    def test_missing_or_garbage_shapes_fall_back_to_default(self):
        default = dict(FROZEN_DEFAULT_QUOTA_CONTROL)
        for policy in (None, {}, 42, "x",
                       {},  # 无 quota_control 键
                       {"quota_control": None},
                       {"quota_control": "x"},
                       {"quota_control": 42},
                       {"quota_control": []}):
            self.assertEqual(execution_policy.default_quota_control(policy),
                             default, policy)

    def test_valid_values_pass_through(self):
        self.assertEqual(
            execution_policy.default_quota_control({"quota_control": {
                "pressure_percent": 40, "draining_percent": 25}}),
            {"pressure_percent": 40, "draining_percent": 25,
             "bridge_interval_minutes": 60})
        # int / float 均透传
        self.assertEqual(
            execution_policy.default_quota_control({"quota_control": {
                "pressure_percent": 40.5, "draining_percent": 0}}),
            {"pressure_percent": 40.5, "draining_percent": 0,
             "bridge_interval_minutes": 60})

    def test_per_key_fallback(self):
        # 单键缺失或形状非法 → 该键按默认，另一键照常透传
        self.assertEqual(
            execution_policy.default_quota_control(
                {"quota_control": {"pressure_percent": 50}}),
            {"pressure_percent": 50, "draining_percent": 20.0,
             "bridge_interval_minutes": 60})
        self.assertEqual(
            execution_policy.default_quota_control(
                {"quota_control": {"draining_percent": 10}}),
            {"pressure_percent": 35.0, "draining_percent": 10,
             "bridge_interval_minutes": 60})
        self.assertEqual(
            execution_policy.default_quota_control(
                {"quota_control": {"pressure_percent": "x",
                                   "draining_percent": 5}}),
            {"pressure_percent": 35.0, "draining_percent": 5,
             "bridge_interval_minutes": 60})

    def test_bool_and_non_finite_fall_back(self):
        for bad in (True, False, float("nan"), float("inf"), "35", None):
            merged = execution_policy.default_quota_control(
                {"quota_control": {"pressure_percent": bad,
                                   "draining_percent": bad}})
            self.assertEqual(merged, FROZEN_DEFAULT_QUOTA_CONTROL, repr(bad))

    def test_bridge_interval_falls_back_per_key(self):
        # bridge 间隔单键非法 → 按默认 60，百分比键照常透传
        for bad in (True, False, 4, 1441, "60", 90.5, None):
            merged = execution_policy.default_quota_control(
                {"quota_control": {"pressure_percent": 50,
                                   "draining_percent": 25,
                                   "bridge_interval_minutes": bad}})
            self.assertEqual(
                merged,
                {"pressure_percent": 50, "draining_percent": 25,
                 "bridge_interval_minutes": 60}, repr(bad))

    def test_fresh_copy_and_input_unchanged(self):
        import json
        policy = {"quota_control": {"pressure_percent": 40,
                                    "draining_percent": 25}}
        snapshot = json.dumps(policy, sort_keys=True)
        merged = execution_policy.default_quota_control(policy)
        self.assertIsNot(merged, policy["quota_control"])
        merged["pressure_percent"] = 99
        self.assertEqual(json.dumps(policy, sort_keys=True), snapshot)
        # 模块常量亦不被波及
        self.assertEqual(execution_policy.DEFAULT_QUOTA_CONTROL,
                         FROZEN_DEFAULT_QUOTA_CONTROL)


# —— setter 与 quota_control 的共存 ——

class SetterPreservesQuotaControlTest(unittest.TestCase):

    def test_parallel_setter_preserves_custom_thresholds(self):
        policy = make_policy(quota_control={"pressure_percent": 40,
                                            "draining_percent": 25,
                                            "bridge_interval_minutes": 30})
        updated = execution_policy.set_parallel_authorization(
            policy, max_workers=4, source="user", confirmed_at=TS)
        self.assertEqual(updated["quota_control"],
                         {"pressure_percent": 40, "draining_percent": 25,
                          "bridge_interval_minutes": 30})
        self.assertEqual(
            execution_policy.validate_execution_policy(updated), [])

    def test_resume_setter_preserves_custom_thresholds(self):
        policy = make_policy(quota_control={"pressure_percent": 60,
                                            "draining_percent": 30})
        updated = execution_policy.set_resume_authorization(
            policy, auto_resume="until_done", max_quota_windows=2,
            source="user", confirmed_at=TS)
        self.assertEqual(updated["quota_control"],
                         {"pressure_percent": 60, "draining_percent": 30,
                          "bridge_interval_minutes": 60})
        self.assertEqual(
            execution_policy.validate_execution_policy(updated), [])

    def test_setter_input_not_mutated(self):
        import json
        policy = make_policy(quota_control={"pressure_percent": 40})
        snapshot = json.dumps(policy, sort_keys=True)
        execution_policy.set_parallel_authorization(
            policy, max_workers=3, source="user", confirmed_at=TS)
        self.assertEqual(json.dumps(policy, sort_keys=True), snapshot)


# —— state.validate_state 规则 8.7 聚合（execution_policy. 前缀） ——

class StateAggregationTest(unittest.TestCase):

    def test_invalid_quota_control_reported_with_prefix(self):
        st = state.new_task_state("qc-task-1a2b3c", "聚合前缀",
                                  {"mode": "solo"})
        st["execution_policy"]["quota_control"] = {
            "pressure_percent": 20, "draining_percent": 20}
        errors = state.validate_state(st)
        self.assertTrue(any(
            e.startswith("execution_policy.quota_control") for e in errors))

    def test_state_without_quota_control_is_valid(self):
        # legacy / §23.1：execution_policy 存在但缺 quota_control 键合法
        st = state.new_task_state("qc-task-1a2b3c", "缺块合法",
                                  {"mode": "solo"})
        del st["execution_policy"]["quota_control"]
        self.assertEqual(state.validate_state(st), [])

    def test_custom_threshold_state_valid(self):
        st = state.new_task_state("qc-task-1a2b3c", "自定义阈值",
                                  {"mode": "solo"})
        st["execution_policy"]["quota_control"] = {
            "pressure_percent": 40.0, "draining_percent": 25.0}
        self.assertEqual(state.validate_state(st), [])


if __name__ == "__main__":
    unittest.main()
