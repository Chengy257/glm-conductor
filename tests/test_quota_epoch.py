#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""runtime.quota.epoch 纯模型层单元测试（v2.2 修正计划 C2，wu-22-C2）。

锚定对象：修正计划 §10（窗口语义核心：automation fire != quota
epoch）/ §10.1（epoch_id 形状）/ §10.2（probe vs executable 双
boundary 分工，2026-09-02 冻结）/ §30（max(reset)+grace）/ §31
（绝不虚构）；epoch 身份冻结口径：排序后 (kind, reset_at|"unknown")
多重集，不含 status / percent。

运行：
    cd <repo_root> && python3 -m unittest tests.test_quota_epoch -v

全部离线：windows 以手写 §27 形状 dict 为主；零 I/O、零网络、
零时钟依赖（被测函数为纯函数，不调用 now()）。

覆盖映射（修正计划 §C2 测试清单逐条对应）：
    a  single 5h：单 EXHAUSTED 窗——epoch_id 前缀、probe=21:59+300s、
       executable 同值（唯一阻塞窗 max=min）、provider EXHAUSTED →
       executable=False；evaluate_epoch 冻结 6 键形状
    b  5h + weekly：指纹 ≠ 单 5h；probe=min（weekly 不阻塞但参与）；
       executable=唯一 EXHAUSTED 窗 reset+grace；双 EXHAUSTED →
       max(reset)+grace（min ≠ max，锚定 §10.2 拆分）
    c  reset drift：同 kind reset_at 21:59:00Z → 03:19:22Z（真实漂移
       样本）→ epoch_id 改变
    d  repeated same snapshot：同 windows 两次调用同 id；输入顺序
       打乱同 id（顺序无关）
    e  new boundary：five_hour 不变、weekly 滚动（reset_at 变）→
       epoch_id 改变（epoch 由全部窗身份构成）
    f  unknown reset：reset_at=None 或乱串 → 两 boundary 均 None；
       id 稳定；双 unknown 同 kind 同 id；unknown vs 已知 reset 异 id
    g  provider status flip：窗 status EXHAUSTED↔AVAILABLE（reset_at
       不变）→ id 不变；provider_status AVAILABLE↔EXHAUSTED →
       executable 翻转、id 不变
    h  输入容错：非 dict 窗跳过；windows=None/非 list → 空集
       （空集指纹锚定）；grace 0 / 600 生效；grace -1/True/nan →
       ValueError；provider_status="BOGUS" → ValueError
    i  归一化："+00:00 后缀形式" 与 "Z 形式" 同一时刻 → 归一后同指纹
"""

import hashlib
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "glm-conductor"))
from runtime.quota import epoch, parser, scheduler

# —— 常量与装置（Z 形式 §27 ISO 串） ——

RESET_FIVE = "2026-09-02T21:59:00Z"
FIVE_PLUS_GRACE = "2026-09-02T22:04:00Z"     # RESET_FIVE + 默认 300s
FIVE_PLUS_600 = "2026-09-02T22:09:00Z"       # RESET_FIVE + 600s
RESET_WEEKLY = "2026-09-07T21:59:00Z"
WEEKLY_PLUS_GRACE = "2026-09-07T22:04:00Z"   # RESET_WEEKLY + 默认 300s
RESET_WEEKLY_ROLLED = "2026-09-14T21:59:00Z"  # 场景 e：weekly 滚动后的新 reset
RESET_DRIFT = "2026-09-03T03:19:22Z"          # 场景 c：真实漂移样本

# evaluate_epoch 冻结 6 键（键名逐字）
FROZEN_KEYS = {
    "epoch_id", "provider_status", "executable",
    "probe_boundary_at", "executable_boundary_at", "windows",
}


def win(kind, status=None, reset_at=None):
    """构造 epoch 视角的 §27 形状单窗口 dict（percent 字段与身份无关，省略）。"""
    return {"kind": kind, "status": status, "reset_at": reset_at}


# —— a：single 5h ——

class SingleFiveHourEpochTest(unittest.TestCase):

    def test_epoch_id_nonempty_glm_prefixed(self):
        """a：单 5h EXHAUSTED 窗——epoch_id 非空且以 "glm:" 开头。"""
        windows = [win("five_hour", "EXHAUSTED", RESET_FIVE)]
        epoch_id = epoch.epoch_id(windows)
        self.assertTrue(epoch_id)
        self.assertTrue(epoch_id.startswith("glm:"))
        self.assertEqual(epoch_id, "glm:" + epoch.epoch_fingerprint(windows)[:16])

    def test_probe_boundary_is_reset_plus_default_grace(self):
        """a：probe = 21:59 + 300s = 22:04:00Z（DEFAULT_GRACE_SECONDS）。"""
        windows = [win("five_hour", "EXHAUSTED", RESET_FIVE)]
        self.assertEqual(
            epoch.probe_boundary_at(windows), FIVE_PLUS_GRACE)
        self.assertEqual(scheduler.DEFAULT_GRACE_SECONDS, 300)

    def test_executable_boundary_same_as_probe_for_single_blocking(self):
        """a：executable boundary 同值（唯一阻塞窗 max=min）。"""
        windows = [win("five_hour", "EXHAUSTED", RESET_FIVE)]
        self.assertEqual(
            epoch.executable_boundary_at(windows), FIVE_PLUS_GRACE)

    def test_evaluate_provider_exhausted_not_executable(self):
        """a：evaluate_epoch(provider_status="EXHAUSTED") → executable=False。"""
        windows = [win("five_hour", "EXHAUSTED", RESET_FIVE)]
        result = epoch.evaluate_epoch(
            provider_status="EXHAUSTED", windows=windows)
        self.assertEqual(set(result), FROZEN_KEYS)  # 冻结 6 键，键名逐字
        self.assertFalse(result["executable"])
        self.assertEqual(result["provider_status"], "EXHAUSTED")
        self.assertEqual(result["epoch_id"], epoch.epoch_id(windows))
        self.assertEqual(result["probe_boundary_at"], FIVE_PLUS_GRACE)
        self.assertEqual(result["executable_boundary_at"], FIVE_PLUS_GRACE)
        self.assertEqual(result["windows"], epoch.canonical_windows(windows))


# —— b：5h + weekly ——

class MixedWindowsBoundaryTest(unittest.TestCase):

    MIXED = [
        win("five_hour", "EXHAUSTED", RESET_FIVE),
        win("weekly", "AVAILABLE", RESET_WEEKLY),
    ]

    def test_fingerprint_differs_from_single_five(self):
        """b：双窗指纹 ≠ 单 5h 场景（身份是全部窗的多重集）。"""
        self.assertNotEqual(
            epoch.epoch_fingerprint(self.MIXED),
            epoch.epoch_fingerprint(
                [win("five_hour", "EXHAUSTED", RESET_FIVE)]))

    def test_probe_takes_min_over_all_statuses(self):
        """b：probe = min = 21:59 + grace（weekly 不阻塞但参与 probe）。"""
        self.assertEqual(epoch.probe_boundary_at(self.MIXED), FIVE_PLUS_GRACE)

    def test_executable_boundary_uses_only_blocking_window(self):
        """b：executable = 唯一 EXHAUSTED 窗（five_hour）的 reset + grace。"""
        self.assertEqual(
            epoch.executable_boundary_at(self.MIXED), FIVE_PLUS_GRACE)

    def test_both_exhausted_takes_max_reset(self):
        """b：双窗全 EXHAUSTED → executable = max(reset)+grace（§30 最晚），
        且与 probe（min）分离——锚定 §10.2 双 boundary 拆分。"""
        both = [
            win("five_hour", "EXHAUSTED", RESET_FIVE),
            win("weekly", "EXHAUSTED", RESET_WEEKLY),
        ]
        self.assertEqual(
            epoch.executable_boundary_at(both), WEEKLY_PLUS_GRACE)
        self.assertEqual(epoch.probe_boundary_at(both), FIVE_PLUS_GRACE)
        self.assertNotEqual(
            epoch.executable_boundary_at(both),
            epoch.probe_boundary_at(both))


# —— c：reset drift ——

class ResetDriftTest(unittest.TestCase):

    def test_drifted_reset_changes_epoch_id(self):
        """c：同 kind（five_hour EXHAUSTED）reset_at 21:59:00Z → 03:19:22Z
        （真实漂移样本）→ epoch_id 改变。"""
        before = [win("five_hour", "EXHAUSTED", RESET_FIVE)]
        after = [win("five_hour", "EXHAUSTED", RESET_DRIFT)]
        self.assertNotEqual(epoch.epoch_id(before), epoch.epoch_id(after))
        self.assertFalse(epoch.same_epoch(before, after))


# —— d：repeated same snapshot ——

class SnapshotStabilityTest(unittest.TestCase):

    def test_repeated_snapshot_same_epoch_id(self):
        """d：同 windows 两次调用 → 同 epoch_id。"""
        windows = [win("five_hour", "EXHAUSTED", RESET_FIVE),
                   win("weekly", "AVAILABLE", RESET_WEEKLY)]
        self.assertEqual(epoch.epoch_id(windows), epoch.epoch_id(windows))

    def test_shuffled_input_order_same_epoch_id(self):
        """d：输入列表顺序打乱 → 同 epoch_id（多重集身份与顺序无关）。"""
        windows = [win("five_hour", "EXHAUSTED", RESET_FIVE),
                   win("weekly", "AVAILABLE", RESET_WEEKLY)]
        shuffled = list(reversed(windows))
        self.assertEqual(epoch.epoch_id(windows), epoch.epoch_id(shuffled))
        self.assertTrue(epoch.same_epoch(windows, shuffled))
        # canonical 排序结果本身确定（five_hour 在 weekly 之前）
        self.assertEqual(
            epoch.canonical_windows(windows),
            epoch.canonical_windows(shuffled))


# —— e：new boundary（weekly 滚动） ——

class WeeklyRollOverTest(unittest.TestCase):

    def test_weekly_rollover_changes_epoch_id(self):
        """e：five_hour 不变、weekly 滚动（reset_at 变）→ epoch_id 改变
        （epoch 由全部窗身份构成，任一窗滚动即新 epoch）。"""
        before = [win("five_hour", "EXHAUSTED", RESET_FIVE),
                  win("weekly", "AVAILABLE", RESET_WEEKLY)]
        after = [win("five_hour", "EXHAUSTED", RESET_FIVE),
                 win("weekly", "AVAILABLE", RESET_WEEKLY_ROLLED)]
        self.assertNotEqual(epoch.epoch_id(before), epoch.epoch_id(after))


# —— f：unknown reset ——

class UnknownResetTest(unittest.TestCase):

    def test_none_and_garbage_reset_both_boundaries_none(self):
        """f：reset_at=None 或乱串 → 两个 boundary 均 None（§31 不虚构）。"""
        for reset in (None, "not-a-timestamp", ""):
            windows = [win("five_hour", "EXHAUSTED", reset)]
            self.assertIsNone(epoch.probe_boundary_at(windows), repr(reset))
            self.assertIsNone(
                epoch.executable_boundary_at(windows), repr(reset))

    def test_epoch_id_stable_for_unknown_reset(self):
        """f：unknown reset 下 epoch_id 仍可计算且稳定。"""
        windows = [win("five_hour", "EXHAUSTED", None)]
        first = epoch.epoch_id(windows)
        self.assertTrue(first.startswith("glm:"))
        self.assertEqual(first, epoch.epoch_id(windows))

    def test_double_unknown_same_kind_same_epoch_id(self):
        """f：双 unknown 快照同 kind → 同 id（status 差异与乱串/None 均
        不进入身份——均归一为 "unknown"）。"""
        none_form = [win("five_hour", "EXHAUSTED", None)]
        garbage_form = [win("five_hour", "AVAILABLE", "garbage")]
        self.assertEqual(epoch.epoch_id(none_form), epoch.epoch_id(garbage_form))

    def test_unknown_vs_known_reset_differs(self):
        """f：unknown vs 已知 reset → 异 id。"""
        unknown = [win("five_hour", "EXHAUSTED", None)]
        known = [win("five_hour", "EXHAUSTED", RESET_FIVE)]
        self.assertNotEqual(epoch.epoch_id(unknown), epoch.epoch_id(known))


# —— g：provider status flip ——

class ProviderStatusFlipTest(unittest.TestCase):

    def test_window_status_flip_keeps_epoch_id(self):
        """g：窗 status EXHAUSTED↔AVAILABLE（reset_at 不变）→ epoch_id 不变
        （身份不含 status：耗尽/恢复是消费状态变化，不是新 epoch）。"""
        exhausted = [win("five_hour", "EXHAUSTED", RESET_FIVE)]
        available = [win("five_hour", "AVAILABLE", RESET_FIVE)]
        self.assertEqual(epoch.epoch_id(exhausted), epoch.epoch_id(available))
        self.assertTrue(epoch.same_epoch(exhausted, available))

    def test_provider_status_flip_flips_executable_keeps_epoch_id(self):
        """g：provider_status AVAILABLE↔EXHAUSTED → executable 翻转、
        epoch_id 不变（identity 只由 windows 决定）。"""
        windows = [win("five_hour", "AVAILABLE", RESET_FIVE)]
        ok = epoch.evaluate_epoch(provider_status="AVAILABLE", windows=windows)
        blocked = epoch.evaluate_epoch(
            provider_status="EXHAUSTED", windows=windows)
        self.assertTrue(ok["executable"])
        self.assertFalse(blocked["executable"])
        self.assertEqual(ok["epoch_id"], blocked["epoch_id"])

    def test_available_provider_still_gated_by_blocking_window(self):
        """g（补）：provider AVAILABLE 但存在 EXHAUSTED 阻塞窗 →
        executable=False（executable 公式的第二项独立生效）。"""
        windows = [win("five_hour", "EXHAUSTED", RESET_FIVE)]
        result = epoch.evaluate_epoch(
            provider_status="AVAILABLE", windows=windows)
        self.assertFalse(result["executable"])
        self.assertEqual(
            result["executable_boundary_at"], FIVE_PLUS_GRACE)


# —— h：输入容错 ——

class InputToleranceTest(unittest.TestCase):

    def test_non_dict_windows_skipped(self):
        """h：非 dict 窗逐项跳过，不影响其余条目的身份。"""
        noisy = [None, 42, "window", ["x"], (7.5,),
                 win("five_hour", "EXHAUSTED", RESET_FIVE)]
        clean = [win("five_hour", "EXHAUSTED", RESET_FIVE)]
        self.assertEqual(epoch.epoch_id(noisy), epoch.epoch_id(clean))
        self.assertEqual(len(epoch.canonical_windows(noisy)), 1)

    def test_windows_none_or_non_list_treated_as_empty(self):
        """h：windows=None / 非 list → 空列表处理（fail-open，与 control
        一致）；boundary 均 None。"""
        for windows in (None, "windows", {"windows": []}, 7):
            self.assertEqual(epoch.canonical_windows(windows), [])
            self.assertIsNone(epoch.probe_boundary_at(windows))
            self.assertIsNone(epoch.executable_boundary_at(windows))
        self.assertEqual(epoch.epoch_id(None), epoch.epoch_id([]))

    def test_empty_windows_epoch_id_is_empty_set_fingerprint(self):
        """h：空集 epoch_id = "glm:" + sha256(b"")[:16]（独立锚定）。"""
        self.assertEqual(
            epoch.epoch_id([]),
            "glm:" + hashlib.sha256(b"").hexdigest()[:16])
        result = epoch.evaluate_epoch(provider_status="AVAILABLE", windows=None)
        self.assertEqual(result["windows"], [])
        self.assertEqual(result["epoch_id"], epoch.epoch_id([]))

    def test_grace_zero_probe_is_bare_reset(self):
        """h：grace_seconds=0 → probe = 裸 reset 时刻。"""
        windows = [win("five_hour", "EXHAUSTED", RESET_FIVE)]
        self.assertEqual(
            epoch.probe_boundary_at(windows, grace_seconds=0), RESET_FIVE)
        self.assertEqual(
            epoch.executable_boundary_at(windows, grace_seconds=0),
            RESET_FIVE)

    def test_custom_grace_600(self):
        """h：grace_seconds=600（自定义）生效。"""
        windows = [win("five_hour", "EXHAUSTED", RESET_FIVE)]
        self.assertEqual(
            epoch.probe_boundary_at(windows, grace_seconds=600),
            FIVE_PLUS_600)
        result = epoch.evaluate_epoch(
            provider_status="EXHAUSTED", windows=windows, grace_seconds=600)
        self.assertEqual(result["probe_boundary_at"], FIVE_PLUS_600)

    def test_negative_and_bool_and_nonfinite_grace_raise(self):
        """h：grace_seconds=-1 / True / nan → ValueError（bool 拒绝）。"""
        windows = [win("five_hour", "EXHAUSTED", RESET_FIVE)]
        for bad in (-1, True, float("nan"), float("inf"), "300", None):
            with self.assertRaises(ValueError):
                epoch.probe_boundary_at(windows, grace_seconds=bad)
            with self.assertRaises(ValueError):
                epoch.executable_boundary_at(windows, grace_seconds=bad)
            with self.assertRaises(ValueError):
                epoch.evaluate_epoch(
                    provider_status="AVAILABLE", windows=windows,
                    grace_seconds=bad)

    def test_bogus_provider_status_raises_with_vocabulary(self):
        """h：evaluate_epoch provider_status="BOGUS" → ValueError（中文，
        消息含取值清单）。"""
        with self.assertRaises(ValueError) as ctx:
            epoch.evaluate_epoch(provider_status="BOGUS", windows=[])
        message = str(ctx.exception)
        for status in parser.QUOTA_STATUSES:
            self.assertIn(status, message)
        # 全部合法四态均可通过校验（executable 公式其余分支另行覆盖）
        for status in parser.QUOTA_STATUSES:
            result = epoch.evaluate_epoch(provider_status=status, windows=[])
            self.assertEqual(result["provider_status"], status)


# —— i：归一化 ——

class ResetNormalizationTest(unittest.TestCase):

    def test_plus00_00_form_and_z_form_same_fingerprint(self):
        """i："+00:00 后缀形式" 与 "Z 形式" 同一时刻 → 归一后同指纹
        （canonical 经 scheduler._parse_iso_utc 统一）。"""
        z_form = [win("five_hour", "EXHAUSTED", RESET_FIVE)]
        offset_form = [win("five_hour", "EXHAUSTED",
                           "2026-09-02T21:59:00+00:00")]
        self.assertEqual(
            epoch.epoch_fingerprint(z_form), epoch.epoch_fingerprint(offset_form))
        self.assertEqual(epoch.epoch_id(z_form), epoch.epoch_id(offset_form))

    def test_canonical_reset_is_z_form(self):
        """i：canonical 输出的 reset_at 恒为 Z 形式串（非 Z 输入被归一）。"""
        windows = [win("five_hour", "EXHAUSTED",
                       "2026-09-02T21:59:00+00:00")]
        self.assertEqual(
            epoch.canonical_windows(windows)[0]["reset_at"], RESET_FIVE)
        self.assertEqual(
            epoch.probe_boundary_at(windows), FIVE_PLUS_GRACE)


# —— canonical 形状与排序（身份冻结口径的机械锚定） ——

class CanonicalWindowsShapeTest(unittest.TestCase):

    def test_canonical_shape_three_keys_and_sort(self):
        """canonical 每窗恰为 kind/status/reset_at 三键；输出按
        (kind, reset_at) 确定性排序（reset 未知者排同 kind 最前）。"""
        windows = [
            win("weekly", "AVAILABLE", RESET_WEEKLY),
            win("five_hour", "EXHAUSTED", RESET_FIVE),
            win("five_hour", "AVAILABLE", None),
        ]
        canonical = epoch.canonical_windows(windows)
        self.assertEqual([w["kind"] for w in canonical],
                         ["five_hour", "five_hour", "weekly"])
        self.assertEqual([w["reset_at"] for w in canonical],
                         [None, RESET_FIVE, RESET_WEEKLY])
        for window in canonical:
            self.assertEqual(set(window), {"kind", "status", "reset_at"})

    def test_non_str_kind_and_status_become_none(self):
        """kind / status 非 str → None（原样透传仅对 str 生效）。"""
        windows = [{"kind": 123, "status": 4.5, "reset_at": RESET_FIVE}]
        canonical = epoch.canonical_windows(windows)[0]
        self.assertIsNone(canonical["kind"])
        self.assertIsNone(canonical["status"])
        self.assertEqual(canonical["reset_at"], RESET_FIVE)

    def test_canonical_is_idempotent(self):
        """canonical 幂等：canonical_windows(canonical_windows(w)) ==
        canonical_windows(w)。"""
        windows = [win("weekly", "AVAILABLE", RESET_WEEKLY),
                   win("five_hour", "EXHAUSTED", RESET_FIVE)]
        once = epoch.canonical_windows(windows)
        self.assertEqual(epoch.canonical_windows(once), once)


if __name__ == "__main__":
    unittest.main()
