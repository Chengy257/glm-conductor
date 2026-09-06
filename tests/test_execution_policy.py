#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""runtime.execution_policy 单元测试（v2.1 M1，WU-21-01）。

仅 Python 3 标准库（unittest + tempfile + subprocess），零第三方依赖；
CLI 用例全部经 cli.main 列表注入 + redirect_stdout 捕获，另设一条
真实子进程路径（显式 encoding="utf-8"，CI 教训），零真实写入固定目录。

运行：
    cd <repo_root> && python3 -m pytest tests/test_execution_policy.py -q
    （或 python3 -m unittest tests.test_execution_policy -v）
"""

import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "glm-conductor"))
from runtime import cli, execution_policy, state

TID = "policy-task-1a2b3c"
TS = "2026-08-30T12:00:00+00:00"
TS2 = "2026-08-30T13:30:00Z"
CLI_PATH = (Path(__file__).resolve().parents[1] / "plugins" /
            "glm-conductor" / "runtime" / "cli.py")

# 冻结 schema 原文（逐字段锚定，default_execution_policy 必须等于它）。
# v2.2 M1（wu-22-01）：§3 四必填子块不变，默认块新增可选子块
# quota_control（决策记录 D5，pressure/draining 阈值，默认 35/20）；
# v2.2 M1a（WU-22-01a）：quota_control 增补 bridge_interval_minutes
# （决策记录 D15-d，Persistent Wake Bridge 固定间隔，保守默认 60——
# 详细契约锚定见 tests/test_execution_policy_quota_control.py）
FROZEN_DEFAULT = {
    "worker_execution": {"default_mode": "background"},
    "parallelism": {"mode": "standard", "default_workers": 2,
                    "max_workers": 2, "hard_limit": 4},
    "continuity": {"mode": "resumable", "auto_resume": "manual",
                   "max_quota_windows": 0},
    "authorization": {"source": "default", "confirmed_at": None,
                      "scope": "task"},
    "quota_control": {"pressure_percent": 35.0, "draining_percent": 20.0,
                      "bridge_interval_minutes": 60},
}


def make_policy(**overrides) -> dict:
    """默认合法 policy 的可定制拷贝（overrides 按子块名 patch 叶子）。"""
    policy = execution_policy.default_execution_policy()
    for name, patch in overrides.items():
        policy[name].update(patch)
    return policy


def run_cli(*args):
    """调用 cli.main 并捕获 stdout，返回 (退出码, 解析后 JSON dict)。"""
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        code = cli.main(list(args))
    return code, json.loads(buffer.getvalue())


def user_confirm(policy, **kwargs):
    """带用户授权元数据的 set_resume 便捷变换（测试辅助）。"""
    return execution_policy.set_resume_authorization(
        policy, source="user", confirmed_at=TS, **kwargs)


# —— default_execution_policy ——

class DefaultExecutionPolicyTest(unittest.TestCase):

    def test_frozen_schema_exact(self):
        # §3 冻结 schema 逐字段相等（默认即保守：background/2/2/4/
        # resumable/manual/0/default/null/task）
        self.assertEqual(execution_policy.default_execution_policy(),
                         FROZEN_DEFAULT)

    def test_fresh_copy_each_call(self):
        # 每次返回全新拷贝：改写返回值不波及模块常量与其他调用方
        first = execution_policy.default_execution_policy()
        first["parallelism"]["max_workers"] = 9
        first["authorization"]["source"] = "user"
        self.assertEqual(execution_policy.default_execution_policy(),
                         FROZEN_DEFAULT)

    def test_default_passes_validation(self):
        self.assertEqual(
            execution_policy.validate_execution_policy(
                execution_policy.default_execution_policy()), [])


# —— validate_execution_policy ——

class ValidateExecutionPolicyTest(unittest.TestCase):

    def test_non_dict_rejected(self):
        for bad in (None, [], "x", 42, True):
            self.assertEqual(
                execution_policy.validate_execution_policy(bad),
                ["execution_policy 必须是 JSON 对象"], bad)

    def test_missing_sub_block_rejected(self):
        for name in execution_policy.POLICY_SUB_BLOCKS:
            policy = make_policy()
            del policy[name]
            errors = execution_policy.validate_execution_policy(policy)
            self.assertTrue(
                any(e == "缺少必填子块 %s" % name for e in errors), name)

    def test_sub_block_not_dict_rejected(self):
        for name in execution_policy.POLICY_SUB_BLOCKS:
            policy = make_policy()
            policy[name] = "oops"
            errors = execution_policy.validate_execution_policy(policy)
            self.assertTrue(
                any(e == "%s 必须是 JSON 对象" % name for e in errors), name)

    def test_worker_mode_valid_and_invalid(self):
        for good in ("background", "foreground"):
            self.assertEqual(
                execution_policy.validate_execution_policy(
                    make_policy(worker_execution={"default_mode": good})),
                [], good)
        for bad in ("inline", "", None, 42):
            policy = make_policy(worker_execution={"default_mode": bad})
            errors = execution_policy.validate_execution_policy(policy)
            self.assertTrue(any(
                e.startswith("worker_execution.default_mode") for e in errors),
                repr(bad))

    def test_worker_mode_missing_rejected(self):
        policy = make_policy()
        del policy["worker_execution"]["default_mode"]
        errors = execution_policy.validate_execution_policy(policy)
        self.assertTrue(any(
            e == "缺少必填键 worker_execution.default_mode" for e in errors))

    def test_serial_mode_with_one_worker_valid(self):
        policy = make_policy(
            parallelism={"mode": "serial", "default_workers": 1,
                         "max_workers": 1})
        self.assertEqual(execution_policy.validate_execution_policy(policy), [])

    def test_serial_mode_with_two_workers_rejected(self):
        policy = make_policy(parallelism={"mode": "serial"})
        errors = execution_policy.validate_execution_policy(policy)
        self.assertTrue(any(
            "parallelism.mode=serial" in e and "max_workers" in e
            for e in errors))

    def test_standard_mode_range(self):
        for workers in (2, 3, 4):
            policy = make_policy(
                parallelism={"mode": "standard", "max_workers": workers},
                authorization={"source": "user", "confirmed_at": TS})
            # max_workers > 2 需 user 授权；2 与 4 均在 standard 域内
            self.assertEqual(
                execution_policy.validate_execution_policy(policy),
                [], workers)
        # standard + 1：mode↔max_workers 耦合错误
        policy = make_policy(parallelism={"mode": "standard",
                                          "max_workers": 1})
        errors = execution_policy.validate_execution_policy(policy)
        self.assertTrue(any(
            "parallelism.mode=standard" in e for e in errors))
        # standard + 5：越界已有 max_workers 基线范围错误（耦合不再重复报）
        policy = make_policy(parallelism={"mode": "standard",
                                          "max_workers": 5})
        errors = execution_policy.validate_execution_policy(policy)
        self.assertTrue(any(
            e.startswith("parallelism.max_workers") for e in errors))

    def test_max_workers_range_and_type(self):
        for bad in (0, 5, -1, "2", True, 2.5, None):
            policy = make_policy(parallelism={"max_workers": bad})
            errors = execution_policy.validate_execution_policy(policy)
            self.assertTrue(any(
                e.startswith("parallelism.max_workers") for e in errors),
                repr(bad))

    def test_default_workers_range_and_cap(self):
        # 1..4 合法；== max_workers 合法（默认块 2/2）
        for good in (1, 2, 3, 4):
            policy = make_policy(
                parallelism={"default_workers": good, "max_workers": 4},
                authorization={"source": "user", "confirmed_at": TS})
            self.assertEqual(
                execution_policy.validate_execution_policy(policy), [], good)
        for bad in (0, 5, True, "3"):
            policy = make_policy(parallelism={"default_workers": bad})
            self.assertTrue(any(
                e.startswith("parallelism.default_workers")
                for e in execution_policy.validate_execution_policy(policy)),
                repr(bad))
        # default_workers > max_workers 拒绝
        policy = make_policy(parallelism={"default_workers": 3,
                                          "max_workers": 2})
        errors = execution_policy.validate_execution_policy(policy)
        self.assertTrue(any(
            "parallelism.default_workers=3" in e and "max_workers=2" in e
            for e in errors))

    def test_hard_limit_frozen_at_four(self):
        self.assertEqual(
            execution_policy.validate_execution_policy(make_policy()), [])
        for bad in (2, 8, "4", True, None):
            policy = make_policy(parallelism={"hard_limit": bad})
            errors = execution_policy.validate_execution_policy(policy)
            self.assertTrue(any(
                e.startswith("parallelism.hard_limit") for e in errors),
                repr(bad))

    def test_continuity_mode_vocab(self):
        for good in ("foreground", "resumable", "idle"):
            self.assertEqual(
                execution_policy.validate_execution_policy(
                    make_policy(continuity={"mode": good})), [], good)
        policy = make_policy(continuity={"mode": "background"})
        self.assertTrue(any(
            e.startswith("continuity.mode")
            for e in execution_policy.validate_execution_policy(policy)))

    def test_auto_resume_vocab(self):
        # 词汇遍历：auto_once/until_done 需配套窗口与 user 授权才是
        # 完整合法形状（词汇本身在下方 bad 段单独验）
        plan = {"manual": 0, "notify": 0, "auto_once": 1, "until_done": 1}
        for good, windows in plan.items():
            policy = make_policy(
                continuity={"auto_resume": good,
                            "max_quota_windows": windows},
                authorization={"source": "user", "confirmed_at": TS})
            self.assertEqual(
                execution_policy.validate_execution_policy(policy), [], good)
        for bad in ("unlimited", "always", "", None, 3):
            policy = make_policy(continuity={"auto_resume": bad})
            errors = execution_policy.validate_execution_policy(policy)
            self.assertTrue(any(
                e.startswith("continuity.auto_resume") for e in errors),
                repr(bad))

    def test_windows_couplings(self):
        # §5.4 全表：manual/notify → 0；auto_once → 1；until_done >= 1
        plan = (
            ("manual", (0,), (1, 2)),
            ("notify", (0,), (1,)),
            ("auto_once", (1,), (0, 2)),
            ("until_done", (1, 3), (0,)),
        )
        for auto_resume, goods, bads in plan:
            for windows in goods:
                policy = make_policy(
                    continuity={"auto_resume": auto_resume,
                                "max_quota_windows": windows},
                    authorization={"source": "user", "confirmed_at": TS})
                self.assertEqual(
                    execution_policy.validate_execution_policy(policy), [],
                    (auto_resume, windows))
            for windows in bads:
                policy = make_policy(
                    continuity={"auto_resume": auto_resume,
                                "max_quota_windows": windows},
                    authorization={"source": "user", "confirmed_at": TS})
                errors = execution_policy.validate_execution_policy(policy)
                self.assertTrue(any(
                    "max_quota_windows" in e and "auto_resume" in e
                    for e in errors), (auto_resume, windows, errors))

    def test_windows_type_and_negative(self):
        for bad in (-1, "1", True, 1.5, None):
            policy = make_policy(continuity={"max_quota_windows": bad})
            errors = execution_policy.validate_execution_policy(policy)
            self.assertTrue(any(
                e.startswith("continuity.max_quota_windows") for e in errors),
                repr(bad))

    def test_authorization_source_vocab(self):
        for good in ("default", "user"):
            source_policy = make_policy(
                authorization={"source": good,
                               "confirmed_at": TS if good == "user" else None})
            self.assertEqual(
                execution_policy.validate_execution_policy(source_policy),
                [], good)
        for bad in ("admin", "model", "", None, 1):
            policy = make_policy(authorization={"source": bad})
            errors = execution_policy.validate_execution_policy(policy)
            self.assertTrue(any(
                e.startswith("authorization.source") for e in errors),
                repr(bad))

    def test_scope_frozen_to_task(self):
        for bad in ("session", "global", None, 42):
            policy = make_policy(authorization={"scope": bad})
            errors = execution_policy.validate_execution_policy(policy)
            self.assertTrue(any(
                e.startswith("authorization.scope") for e in errors),
                repr(bad))

    def test_confirmed_at_iso8601_or_null(self):
        for good in (None, TS, TS2, "2026-08-30T12:00:00"):
            policy = make_policy(
                authorization={"confirmed_at": good})
            self.assertEqual(
                execution_policy.validate_execution_policy(policy), [],
                repr(good))
        for bad in ("yesterday", "2026-13-40T00:00:00", 12345, ["x"], ""):
            policy = make_policy(authorization={"confirmed_at": bad})
            errors = execution_policy.validate_execution_policy(policy)
            self.assertTrue(any(
                e.startswith("authorization.confirmed_at") for e in errors),
                repr(bad))

    def test_user_source_requires_confirmed_at(self):
        policy = make_policy(authorization={"source": "user",
                                            "confirmed_at": None})
        errors = execution_policy.validate_execution_policy(policy)
        self.assertTrue(any(
            "confirmed_at" in e and "user" in e for e in errors))

    def test_cross_window_resume_requires_user(self):
        # auto_once / until_done 必须 source=user（一正一反）
        for auto_resume, windows in (("auto_once", 1), ("until_done", 2)):
            denied = make_policy(
                continuity={"auto_resume": auto_resume,
                            "max_quota_windows": windows})
            errors = execution_policy.validate_execution_policy(denied)
            self.assertTrue(any(
                "authorization.source" in e and "user" in e for e in errors),
                auto_resume)
            granted = make_policy(
                continuity={"auto_resume": auto_resume,
                            "max_quota_windows": windows},
                authorization={"source": "user", "confirmed_at": TS})
            self.assertEqual(
                execution_policy.validate_execution_policy(granted), [],
                auto_resume)

    def test_escalated_parallel_requires_user(self):
        # max_workers > 2 必须 source=user（一正一反）
        denied = make_policy(parallelism={"max_workers": 3})
        errors = execution_policy.validate_execution_policy(denied)
        self.assertTrue(any(
            "parallelism.max_workers=3" in e and "user" in e for e in errors))
        granted = make_policy(parallelism={"max_workers": 3},
                              authorization={"source": "user",
                                             "confirmed_at": TS})
        self.assertEqual(
            execution_policy.validate_execution_policy(granted), [])

    def test_aggregates_errors_not_shortcircuit(self):
        # 多处非法聚合全部错误，不短路
        policy = make_policy(
            worker_execution={"default_mode": "inline"},
            parallelism={"max_workers": 9},
            authorization={"source": "admin", "scope": "session"})
        errors = execution_policy.validate_execution_policy(policy)
        self.assertTrue(any("worker_execution.default_mode" in e
                            for e in errors))
        self.assertTrue(any("parallelism.max_workers" in e for e in errors))
        self.assertTrue(any("authorization.source" in e for e in errors))
        self.assertTrue(any("authorization.scope" in e for e in errors))

    def test_unknown_keys_ignored(self):
        policy = make_policy()
        policy["future_block"] = {"whatever": 1}
        policy["parallelism"]["future_field"] = 1
        self.assertEqual(execution_policy.validate_execution_policy(policy), [])


# —— set_parallel_authorization ——

class SetParallelAuthorizationTest(unittest.TestCase):

    def test_returns_new_dict_input_unchanged(self):
        policy = make_policy()
        snapshot = json.dumps(policy, sort_keys=True)
        updated = execution_policy.set_parallel_authorization(
            policy, max_workers=4, source="user", confirmed_at=TS)
        self.assertIsNot(updated, policy)
        self.assertEqual(json.dumps(policy, sort_keys=True), snapshot)
        self.assertEqual(updated["parallelism"]["max_workers"], 4)

    def test_two_workers_keeps_standard_and_records_user(self):
        updated = execution_policy.set_parallel_authorization(
            execution_policy.default_execution_policy(),
            max_workers=2, source="user", confirmed_at=TS)
        self.assertEqual(
            execution_policy.validate_execution_policy(updated), [])
        self.assertEqual(updated["parallelism"]["mode"], "standard")
        self.assertEqual(updated["parallelism"]["max_workers"], 2)
        self.assertEqual(updated["authorization"],
                         {"source": "user", "confirmed_at": TS,
                          "scope": "task"})

    def test_one_worker_derives_serial_and_clamps_default(self):
        updated = execution_policy.set_parallel_authorization(
            execution_policy.default_execution_policy(),
            max_workers=1, source="user", confirmed_at=TS)
        self.assertEqual(
            execution_policy.validate_execution_policy(updated), [])
        self.assertEqual(updated["parallelism"]["mode"], "serial")
        self.assertEqual(updated["parallelism"]["default_workers"], 1)
        self.assertEqual(updated["parallelism"]["hard_limit"], 4)

    def test_four_workers_valid_and_hard_limit_untouched(self):
        updated = execution_policy.set_parallel_authorization(
            make_policy(), max_workers=4, source="user", confirmed_at=TS)
        self.assertEqual(
            execution_policy.validate_execution_policy(updated), [])
        self.assertEqual(
            execution_policy.effective_worker_budget(updated, "AVAILABLE"), 4)
        self.assertEqual(updated["parallelism"]["hard_limit"], 4)

    def test_rejects_out_of_range_workers(self):
        policy = make_policy()
        snapshot = json.dumps(policy, sort_keys=True)
        for bad in (0, 5, -1, "3", True, 2.5, None):
            with self.assertRaises(ValueError, msg=repr(bad)) as ctx:
                execution_policy.set_parallel_authorization(
                    policy, max_workers=bad, source="user",
                    confirmed_at=TS)
            self.assertIn("max_workers", str(ctx.exception))
        # 失败零副作用
        self.assertEqual(json.dumps(policy, sort_keys=True), snapshot)

    def test_rejects_bad_source(self):
        for bad in ("admin", "", None, 1):
            with self.assertRaises(ValueError, msg=repr(bad)) as ctx:
                execution_policy.set_parallel_authorization(
                    make_policy(), max_workers=2, source=bad,
                    confirmed_at=TS)
            self.assertIn("source", str(ctx.exception))

    def test_rejects_bad_confirmed_at(self):
        for bad in ("yesterday", 12345, ["x"]):
            with self.assertRaises(ValueError, msg=repr(bad)) as ctx:
                execution_policy.set_parallel_authorization(
                    make_policy(), max_workers=2, source="user",
                    confirmed_at=bad)
            self.assertIn("confirmed_at", str(ctx.exception))

    def test_rejects_user_without_confirmed_at(self):
        with self.assertRaises(ValueError) as ctx:
            execution_policy.set_parallel_authorization(
                make_policy(), max_workers=2, source="user",
                confirmed_at=None)
        self.assertIn("confirmed_at", str(ctx.exception))

    def test_rejects_escalation_without_user(self):
        with self.assertRaises(ValueError) as ctx:
            execution_policy.set_parallel_authorization(
                make_policy(), max_workers=3, source="default",
                confirmed_at=TS)
        self.assertIn("max_workers", str(ctx.exception))
        self.assertIn("user", str(ctx.exception))

    def test_rejects_non_dict_policy(self):
        for bad in (None, 42, "x", []):
            with self.assertRaises(ValueError, msg=repr(bad)):
                execution_policy.set_parallel_authorization(
                    bad, max_workers=2, source="user", confirmed_at=TS)


# —— set_resume_authorization ——

class SetResumeAuthorizationTest(unittest.TestCase):

    def test_manual_zero_records_user(self):
        updated = execution_policy.set_resume_authorization(
            make_policy(), auto_resume="manual", max_quota_windows=0,
            source="user", confirmed_at=TS)
        self.assertEqual(
            execution_policy.validate_execution_policy(updated), [])
        self.assertEqual(updated["continuity"]["auto_resume"], "manual")
        self.assertEqual(updated["continuity"]["max_quota_windows"], 0)
        self.assertEqual(updated["authorization"]["source"], "user")

    def test_until_done_with_budget_ok(self):
        updated = execution_policy.set_resume_authorization(
            make_policy(), auto_resume="until_done", max_quota_windows=3,
            source="user", confirmed_at=TS)
        self.assertEqual(
            execution_policy.validate_execution_policy(updated), [])
        self.assertEqual(updated["continuity"]["max_quota_windows"], 3)

    def test_input_unchanged_and_continuity_mode_preserved(self):
        policy = make_policy(continuity={"mode": "idle"})
        snapshot = json.dumps(policy, sort_keys=True)
        updated = execution_policy.set_resume_authorization(
            policy, auto_resume="auto_once", max_quota_windows=1,
            source="user", confirmed_at=TS)
        self.assertIsNot(updated, policy)
        self.assertEqual(json.dumps(policy, sort_keys=True), snapshot)
        self.assertEqual(updated["continuity"]["mode"], "idle")
        self.assertEqual(updated["continuity"]["auto_resume"], "auto_once")

    def test_rejects_bad_auto_resume(self):
        for bad in ("unlimited", "always", "", None, 3):
            with self.assertRaises(ValueError, msg=repr(bad)) as ctx:
                execution_policy.set_resume_authorization(
                    make_policy(), auto_resume=bad, max_quota_windows=0,
                    source="user", confirmed_at=TS)
            self.assertIn("auto_resume", str(ctx.exception))

    def test_rejects_bad_windows(self):
        for bad in (-1, "1", True, 1.5, None):
            with self.assertRaises(ValueError, msg=repr(bad)) as ctx:
                execution_policy.set_resume_authorization(
                    make_policy(), auto_resume="until_done",
                    max_quota_windows=bad, source="user", confirmed_at=TS)
            self.assertIn("max_quota_windows", str(ctx.exception))

    def test_rejects_manual_or_notify_with_windows(self):
        for auto_resume in ("manual", "notify"):
            with self.assertRaises(ValueError, msg=auto_resume) as ctx:
                execution_policy.set_resume_authorization(
                    make_policy(), auto_resume=auto_resume,
                    max_quota_windows=1, source="user", confirmed_at=TS)
            self.assertIn("max_quota_windows", str(ctx.exception))

    def test_rejects_auto_once_with_wrong_window(self):
        for bad in (0, 2):
            with self.assertRaises(ValueError, msg=bad) as ctx:
                execution_policy.set_resume_authorization(
                    make_policy(), auto_resume="auto_once",
                    max_quota_windows=bad, source="user", confirmed_at=TS)
            self.assertIn("max_quota_windows", str(ctx.exception))

    def test_rejects_until_done_with_zero_window(self):
        with self.assertRaises(ValueError) as ctx:
            execution_policy.set_resume_authorization(
                make_policy(), auto_resume="until_done",
                max_quota_windows=0, source="user", confirmed_at=TS)
        self.assertIn("max_quota_windows", str(ctx.exception))

    def test_rejects_cross_window_without_user(self):
        for auto_resume, windows in (("auto_once", 1), ("until_done", 2)):
            with self.assertRaises(ValueError, msg=auto_resume) as ctx:
                execution_policy.set_resume_authorization(
                    make_policy(), auto_resume=auto_resume,
                    max_quota_windows=windows, source="default",
                    confirmed_at=TS)
            self.assertIn("source", str(ctx.exception))

    def test_rejects_user_without_confirmed_at(self):
        with self.assertRaises(ValueError) as ctx:
            execution_policy.set_resume_authorization(
                make_policy(), auto_resume="until_done",
                max_quota_windows=1, source="user", confirmed_at=None)
        self.assertIn("confirmed_at", str(ctx.exception))

    def test_rejects_non_dict_policy(self):
        for bad in (None, 42, "x"):
            with self.assertRaises(ValueError, msg=repr(bad)):
                execution_policy.set_resume_authorization(
                    bad, auto_resume="manual", max_quota_windows=0,
                    source="user", confirmed_at=TS)


# —— effective_worker_budget ——

class EffectiveWorkerBudgetTest(unittest.TestCase):

    def test_quota_status_table(self):
        # §12 表（默认策略 max_workers=2）：AVAILABLE→2 / PRESSURE→1 /
        # UNKNOWN→1 / EXHAUSTED→0
        plan = (("AVAILABLE", 2), ("PRESSURE", 1), ("UNKNOWN", 1),
                ("EXHAUSTED", 0))
        for quota_status, expected in plan:
            self.assertEqual(
                execution_policy.effective_worker_budget(
                    execution_policy.default_execution_policy(),
                    quota_status),
                expected, quota_status)

    def test_available_uses_policy_max_workers(self):
        policy = execution_policy.set_parallel_authorization(
            execution_policy.default_execution_policy(),
            max_workers=4, source="user", confirmed_at=TS)
        self.assertEqual(
            execution_policy.effective_worker_budget(policy, "AVAILABLE"), 4)
        serial = execution_policy.set_parallel_authorization(
            execution_policy.default_execution_policy(),
            max_workers=1, source="user", confirmed_at=TS)
        self.assertEqual(
            execution_policy.effective_worker_budget(serial, "PRESSURE"), 1)

    def test_illegal_status_conservative_zero(self):
        for bad in ("available", "pressur", "FOO", "", None, 42, True):
            self.assertEqual(
                execution_policy.effective_worker_budget(
                    execution_policy.default_execution_policy(), bad),
                0, repr(bad))

    def test_legacy_or_garbage_policy_falls_back_to_default(self):
        # legacy 缺块（None）/ 形状异常 → 按默认块 max_workers=2 解释
        for policy in (None, {}, {"parallelism": "x"},
                       {"parallelism": {"max_workers": "2"}}):
            self.assertEqual(
                execution_policy.effective_worker_budget(policy, "AVAILABLE"),
                2, policy)
            self.assertEqual(
                execution_policy.effective_worker_budget(policy, "EXHAUSTED"),
                0, policy)

    def test_oversized_hand_written_workers_clamped(self):
        # 手写越界 max_workers 不炸消费方：截断到 hard_limit=4
        policy = {"parallelism": {"max_workers": 99}}
        self.assertEqual(
            execution_policy.effective_worker_budget(policy, "AVAILABLE"), 4)


# —— runtime/cli.py 三个 policy 子命令 ——

class CliPolicyTest(unittest.TestCase):
    """CLI 骨架契约：成功/拒绝路径与退出码（0/2/1）。

    夹具故意以「构造后删除 execution_policy 键」落一个 legacy state
    （缺键合法，R7），验证 show 展示默认块不写盘、set-* 以默认块为底
    创建完整块。
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = self._tmp.name
        st = state.new_task_state(TID, "策略 CLI 夹具任务", {"mode": "solo"})
        del st["execution_policy"]  # legacy 形态（缺键合法）
        state.save_state(self.repo, st)

    def test_show_legacy_returns_default_block_without_writing(self):
        code, payload = run_cli("policy-show", self.repo, TID)
        self.assertEqual(code, 0)
        self.assertEqual(payload["policy_source"], "default")
        self.assertEqual(payload["execution_policy"], FROZEN_DEFAULT)
        self.assertEqual(payload["task_id"], TID)
        # 只读：盘上仍无该键
        loaded = state.load_state(self.repo, TID)
        self.assertNotIn("execution_policy", loaded)

    def test_show_state_block(self):
        st = state.load_state(self.repo, TID)
        st["execution_policy"] = FROZEN_DEFAULT
        state.save_state(self.repo, st)
        code, payload = run_cli("policy-show", self.repo, TID)
        self.assertEqual(code, 0)
        self.assertEqual(payload["policy_source"], "state")
        self.assertEqual(payload["execution_policy"], FROZEN_DEFAULT)

    def test_show_missing_task_exit_one(self):
        code, payload = run_cli("policy-show", self.repo, "ghost-task-1")
        self.assertEqual(code, 1)
        self.assertIn("error", payload)

    def test_set_parallel_success_and_persisted(self):
        code, payload = run_cli("policy-set-parallel", self.repo, TID, "4")
        self.assertEqual(code, 0)
        self.assertEqual(payload["policy_source"], "state")
        loaded = state.load_state(self.repo, TID)
        block = loaded["execution_policy"]
        self.assertEqual(block["parallelism"]["max_workers"], 4)
        self.assertEqual(block["parallelism"]["mode"], "standard")
        self.assertEqual(block["authorization"]["source"], "user")
        self.assertTrue(block["authorization"]["confirmed_at"])
        self.assertEqual(state.validate_state(loaded), [])
        self.assertEqual(payload["execution_policy"], block)

    def test_set_parallel_one_derives_serial(self):
        code, _payload = run_cli("policy-set-parallel", self.repo, TID, "1")
        self.assertEqual(code, 0)
        loaded = state.load_state(self.repo, TID)
        block = loaded["execution_policy"]
        self.assertEqual(block["parallelism"]["mode"], "serial")
        self.assertEqual(block["parallelism"]["max_workers"], 1)
        self.assertEqual(block["parallelism"]["default_workers"], 1)
        self.assertEqual(state.validate_state(loaded), [])

    def test_set_parallel_rejects_out_of_range_exit_two(self):
        for bad in ("0", "5", "-1", "abc", "2.5"):
            code, payload = run_cli(
                "policy-set-parallel", self.repo, TID, bad)
            self.assertEqual(code, 2, bad)
            self.assertIn("error", payload, bad)
        # 拒绝路径零落盘：state 仍是 legacy 缺键形态
        loaded = state.load_state(self.repo, TID)
        self.assertNotIn("execution_policy", loaded)

    def test_set_parallel_missing_task_exit_one(self):
        code, payload = run_cli(
            "policy-set-parallel", self.repo, "ghost-task-1", "2")
        self.assertEqual(code, 1)
        self.assertIn("error", payload)

    def test_set_resume_success_with_explicit_windows(self):
        code, _payload = run_cli(
            "policy-set-resume", self.repo, TID, "until_done", "2")
        self.assertEqual(code, 0)
        loaded = state.load_state(self.repo, TID)
        block = loaded["execution_policy"]
        self.assertEqual(block["continuity"]["auto_resume"], "until_done")
        self.assertEqual(block["continuity"]["max_quota_windows"], 2)
        self.assertEqual(block["authorization"]["source"], "user")
        self.assertEqual(state.validate_state(loaded), [])

    def test_set_resume_default_windows_derivation(self):
        plan = (("manual", 0), ("notify", 0), ("auto_once", 1),
                ("until_done", 1))
        for auto_resume, expected in plan:
            with tempfile.TemporaryDirectory() as tmp:
                st = state.new_task_state(
                    TID, "策略 CLI 缺省窗口", {"mode": "solo"})
                del st["execution_policy"]
                state.save_state(tmp, st)
                code, _payload = run_cli(
                    "policy-set-resume", tmp, TID, auto_resume)
                self.assertEqual(code, 0, auto_resume)
                loaded = state.load_state(tmp, TID)
                self.assertEqual(
                    loaded["execution_policy"]["continuity"]["auto_resume"],
                    auto_resume)
                self.assertEqual(
                    loaded["execution_policy"]["continuity"][
                        "max_quota_windows"],
                    expected, auto_resume)
                self.assertEqual(state.validate_state(loaded), [])

    def test_set_resume_rejects_coupling_violation_exit_two(self):
        code, payload = run_cli(
            "policy-set-resume", self.repo, TID, "manual", "1")
        self.assertEqual(code, 2)
        self.assertIn("error", payload)

    def test_set_resume_rejects_bad_word_exit_two(self):
        code, payload = run_cli(
            "policy-set-resume", self.repo, TID, "always")
        self.assertEqual(code, 2)
        self.assertIn("error", payload)

    def test_usage_errors_exit_two(self):
        for args in ([], ["no-such-command", "x"],
                     ["policy-show", self.repo],
                     ["policy-set-parallel", self.repo, TID],
                     ["policy-set-resume", self.repo, TID, "manual", "1",
                      "extra"]):
            code, payload = run_cli(*args)
            self.assertEqual(code, 2, args)
            self.assertIn("error", payload, args)

    def test_output_is_single_line_ascii_json(self):
        # stdout 契约：单行 + ensure_ascii=True（中文转义 \uXXXX）
        code, _payload = run_cli("policy-set-resume", self.repo, TID,
                                 "until_done", "1")
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            cli.main(["policy-show", self.repo, TID])
        text = buffer.getvalue()
        self.assertTrue(text.endswith("\n"))
        self.assertEqual(text.count("\n"), 1)
        self.assertTrue(text.strip().isascii())

    def test_subprocess_real_cli_utf8(self):
        # 真实子进程路径（显式 encoding="utf-8"，CI 教训）
        proc = subprocess.run(
            [sys.executable, str(CLI_PATH),
             "policy-show", self.repo, TID],
            capture_output=True, encoding="utf-8", check=False)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.count("\n"), 1, proc.stdout)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["policy_source"], "default")
        self.assertIn("execution_policy", payload)

    def test_subprocess_set_parallel_rejects(self):
        proc = subprocess.run(
            [sys.executable, str(CLI_PATH),
             "policy-set-parallel", self.repo, TID, "9"],
            capture_output=True, encoding="utf-8", check=False)
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("error", json.loads(proc.stdout))


if __name__ == "__main__":
    unittest.main()
