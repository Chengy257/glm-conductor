#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""runtime.state 顶层 continuation 块单元测试（v2.2 M1，wu-22-01）。

锚定对象：主计划 §8（obligation / wake_bridge 冻结 schema）/ §23（默认块
与向后兼容）；决策记录 D14（wake bridge 记账路径）。v2.2 M1a（WU-22-01a，
决策记录 D15-e）扩展 Persistent Bridge schema 覆盖：
  - default_continuation 默认块形状逐字段断言 + 全新拷贝隔离；
  - new_task_state 恒含 continuation 默认块（含保存往返）；
  - validate_state 规则 8.8：legacy 缺块合法 / 枚举 / reason / wake_bridge
    七字段（id 为 null 或非空 str，时值为 null 或 ISO8601）/ 未知键忽略 /
    错误路径前缀 continuation. / 非法块 save_state 拒绝；
  - M1a 新键：10 值 wake_bridge.status / mode / generation /
    current_boundary_id / next_wake_at / bridge_interval_minutes（5-1440
    边界，bool 拒绝）/ scheduler_context（origin + 四能力 +
    parent_automation_id）/ tombstone（四键必填形状）；
  - wu-22-01 已写形态（7 键 wake_bridge、无新键）仍然合法。

仅 Python 3 标准库（unittest + tempfile），零第三方依赖；所有文件操作在
tempfile.TemporaryDirectory 内进行，不污染真实目录。

运行：
    cd <repo_root> && python3 -m unittest tests.test_state_continuation -v
"""

import sys, unittest
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "glm-conductor"))
from runtime import state

TID = "cont-task-1a2b3c"
TS = "2026-09-01T12:00:00+00:00"
TS_Z = "2026-09-01T12:00:00Z"

# §23.2 + D15-e 冻结默认块原文（逐字段锚定，default_continuation 必须
# 等于它；v2.2 M1a WU-22-01a 扩展 scheduler_context / wake_bridge 五键 /
# tombstone）
FROZEN_DEFAULT_CONTINUATION = {
    "obligation": "none",
    "reason": None,
    "scheduler_context": {
        "origin": "unknown",
        "create": "unknown",
        "update": "unknown",
        "pause": "unknown",
        "delete": "unknown",
        "parent_automation_id": None,
    },
    "wake_bridge": {
        "status": "none",
        "boundary_id": None,
        "automation_id": None,
        "reset_at": None,
        "wake_at": None,
        "armed_at": None,
        "fired_at": None,
        "mode": "recurring",
        "generation": 0,
        "current_boundary_id": None,
        "next_wake_at": None,
        "bridge_interval_minutes": None,
    },
    "tombstone": None,
}


def make_state(route=None, **kwargs):
    """构造一个默认合法的完整状态 dict（solo 路由即可过全部不变量）。

    task_id / goal 可经 kwargs 覆盖，缺省用模块级夹具常量。
    """
    if route is None:
        route = {"mode": "solo"}
    kwargs.setdefault("task_id", TID)
    kwargs.setdefault("goal", "v2.2 continuation 夹具任务")
    return state.new_task_state(route=route, **kwargs)


# —— default_continuation / DEFAULT_CONTINUATION ——

class DefaultContinuationTest(unittest.TestCase):

    def test_frozen_schema_exact(self):
        # §23.2 + D15-e 冻结 schema 逐字段相等（obligation="none" +
        # 全 unknown scheduler_context + 无事实 wake_bridge 十二字段 +
        # 未退役 tombstone，不得增删改名）
        self.assertEqual(state.default_continuation(),
                         FROZEN_DEFAULT_CONTINUATION)
        self.assertEqual(state.DEFAULT_CONTINUATION,
                         FROZEN_DEFAULT_CONTINUATION)

    def test_fresh_copy_each_call(self):
        # 每次返回全新拷贝（嵌套 wake_bridge / scheduler_context 二层
        # 隔离）：改写返回值不波及模块常量与其他调用方
        first = state.default_continuation()
        first["obligation"] = "armed"
        first["reason"] = "x"
        first["wake_bridge"]["status"] = "armed"
        first["wake_bridge"]["automation_id"] = "cron-1"
        first["scheduler_context"]["origin"] = "scheduled_task"
        self.assertIsNot(first["wake_bridge"],
                         state.DEFAULT_CONTINUATION["wake_bridge"])
        self.assertIsNot(first["scheduler_context"],
                         state.DEFAULT_CONTINUATION["scheduler_context"])
        self.assertEqual(state.default_continuation(),
                         FROZEN_DEFAULT_CONTINUATION)

    def test_two_calls_do_not_share_wake_bridge(self):
        a = state.default_continuation()
        b = state.default_continuation()
        self.assertIsNot(a, b)
        self.assertIsNot(a["wake_bridge"], b["wake_bridge"])
        self.assertIsNot(a["scheduler_context"], b["scheduler_context"])

    def test_frozen_enums(self):
        # 冻结枚举原文（§8.1 六 obligation / §8.2 十 wake_bridge.status /
        # D15-e scheduler 两词汇 + bridge mode 两词汇）
        self.assertEqual(
            state.CONTINUATION_OBLIGATIONS,
            ("none", "checkpoint_required", "wake_required", "armed",
             "waiting_user", "degraded"))
        self.assertEqual(
            state.WAKE_BRIDGE_STATUSES,
            ("none", "requested", "armed", "fired", "retarget_required",
             "degraded", "paused", "cancelled", "stale", "failed"))
        self.assertEqual(
            state.SCHEDULER_ORIGINS,
            ("interactive", "scheduled_task", "unknown"))
        self.assertEqual(
            state.SCHEDULER_CAPABILITIES,
            ("allowed", "forbidden", "unknown"))
        self.assertEqual(
            state.WAKE_BRIDGE_MODES, ("recurring", "self_retiming"))


# —— new_task_state 构造 ——

class NewTaskStateContinuationTest(unittest.TestCase):

    def test_new_task_state_contains_default_block(self):
        st = make_state()
        self.assertEqual(st["continuation"],
                         state.default_continuation())
        # 构造隔离：两个任务的 wake_bridge 不共享同一 dict
        other = make_state(task_id="cont-task-9z8y7x")
        self.assertIsNot(st["continuation"]["wake_bridge"],
                         other["continuation"]["wake_bridge"])

    def test_new_task_state_carries_m1a_keys(self):
        # v2.2 M1a（D15-e）：构造结果恒含全部新键（scheduler_context 六键 /
        # wake_bridge 五扩展键 / tombstone），且默认值逐字段正确
        st = make_state()
        continuation = st["continuation"]
        self.assertEqual(
            continuation["scheduler_context"],
            {"origin": "unknown", "create": "unknown", "update": "unknown",
             "pause": "unknown", "delete": "unknown",
             "parent_automation_id": None})
        for key, value in (("mode", "recurring"), ("generation", 0),
                           ("current_boundary_id", None),
                           ("next_wake_at", None),
                           ("bridge_interval_minutes", None)):
            self.assertIn(key, continuation["wake_bridge"], key)
            self.assertEqual(continuation["wake_bridge"][key], value, key)
        self.assertIsNone(continuation["tombstone"])
        self.assertEqual(state.validate_state(st), [])

    def test_constructed_state_validates_and_roundtrips(self):
        st = make_state()
        self.assertEqual(state.validate_state(st), [])
        with tempfile.TemporaryDirectory() as tmp:
            state.save_state(tmp, st)
            loaded = state.load_state(tmp, TID)
            self.assertEqual(loaded["continuation"],
                             FROZEN_DEFAULT_CONTINUATION)
            self.assertEqual(state.validate_state(loaded), [])

    def test_armed_shape_roundtrips(self):
        # 控制回路推进后的典型形状（D14）：obligation/status=armed +
        # bridge 字段非空，整状态合法且保存往返保持
        st = make_state()
        st["continuation"]["obligation"] = "armed"
        st["continuation"]["reason"] = "quota draining, bridge armed"
        st["continuation"]["wake_bridge"] = {
            "status": "armed",
            "boundary_id": "five_hour:2026-09-01T18:00:00+00:00",
            "automation_id": "cron-wake-1",
            "reset_at": "2026-09-01T18:00:00+00:00",
            "wake_at": "2026-09-01T18:05:00+00:00",
            "armed_at": TS,
            "fired_at": None,
        }
        self.assertEqual(state.validate_state(st), [])
        with tempfile.TemporaryDirectory() as tmp:
            state.save_state(tmp, st)
            loaded = state.load_state(tmp, TID)
            self.assertEqual(loaded["continuation"],
                             st["continuation"])


# —— validate_state 规则 8.8 ——

class ValidateContinuationTest(unittest.TestCase):

    def test_legacy_missing_block_is_valid(self):
        # legacy v2.1 形态：无 continuation 键完全合法（构造后删除 +
        # 保存往返不出现该键）
        st = make_state()
        del st["continuation"]
        self.assertEqual(state.validate_state(st), [])
        with tempfile.TemporaryDirectory() as tmp:
            state.save_state(tmp, st)
            loaded = state.load_state(tmp, TID)
            self.assertNotIn("continuation", loaded)
            self.assertEqual(state.validate_state(loaded), [])

    def test_non_dict_rejected(self):
        for bad in (None, [], "x", 42, True):
            st = make_state()
            st["continuation"] = bad
            errors = state.validate_state(st)
            self.assertTrue(
                any(e == "continuation 必须是 JSON 对象" for e in errors),
                repr(bad))

    def test_obligation_enum(self):
        for good in state.CONTINUATION_OBLIGATIONS:
            st = make_state()
            st["continuation"]["obligation"] = good
            self.assertEqual(state.validate_state(st), [], good)
        for bad in ("bogus", "NONE", "", None, 3):
            st = make_state()
            st["continuation"]["obligation"] = bad
            errors = state.validate_state(st)
            self.assertTrue(any(
                e.startswith("continuation.obligation") for e in errors),
                repr(bad))

    def test_wake_bridge_status_enum(self):
        for good in state.WAKE_BRIDGE_STATUSES:
            st = make_state()
            st["continuation"]["wake_bridge"]["status"] = good
            self.assertEqual(state.validate_state(st), [], good)
        for bad in ("queued", "ARMED", "", None, 7):
            st = make_state()
            st["continuation"]["wake_bridge"]["status"] = bad
            errors = state.validate_state(st)
            self.assertTrue(any(
                e.startswith("continuation.wake_bridge.status")
                for e in errors), repr(bad))

    def test_reason_null_or_str(self):
        for good in (None, "", "quota draining"):
            st = make_state()
            st["continuation"]["reason"] = good
            self.assertEqual(state.validate_state(st), [], repr(good))
        for bad in (42, ["x"], {"r": 1}, True):
            st = make_state()
            st["continuation"]["reason"] = bad
            errors = state.validate_state(st)
            self.assertTrue(any(
                e == "continuation.reason 必须是字符串或 null"
                for e in errors), repr(bad))

    def test_wake_bridge_non_dict_rejected_and_null_tolerated(self):
        # 非 dict（非 None）→ 错误；显式 null 视同缺省 → 合法
        st = make_state()
        st["continuation"]["wake_bridge"] = "armed"
        self.assertTrue(any(
            e == "continuation.wake_bridge 必须是 JSON 对象或 null"
            for e in state.validate_state(st)))
        st = make_state()
        st["continuation"]["wake_bridge"] = None
        self.assertEqual(state.validate_state(st), [])

    def test_id_fields_null_or_nonempty_str(self):
        for key in ("boundary_id", "automation_id"):
            for good in (None, "five_hour:2026-09-01T18:00:00+00:00",
                         "cron-wake-1"):
                st = make_state()
                st["continuation"]["wake_bridge"][key] = good
                self.assertEqual(state.validate_state(st), [],
                                 (key, good))
            for bad in ("", 42, ["x"], True):
                st = make_state()
                st["continuation"]["wake_bridge"][key] = bad
                errors = state.validate_state(st)
                self.assertTrue(any(
                    e == "continuation.wake_bridge.%s 必须是非空字符串或 null"
                    % key for e in errors), (key, repr(bad)))

    def test_time_fields_null_or_iso8601(self):
        for key in ("reset_at", "wake_at", "armed_at", "fired_at"):
            for good in (None, TS, TS_Z, "2026-09-01T12:00:00"):
                st = make_state()
                st["continuation"]["wake_bridge"][key] = good
                self.assertEqual(state.validate_state(st), [],
                                 (key, good))
            for bad in ("yesterday", "2026-13-40T00:00:00", "", 12345,
                        ["x"]):
                st = make_state()
                st["continuation"]["wake_bridge"][key] = bad
                errors = state.validate_state(st)
                self.assertTrue(any(
                    e == "continuation.wake_bridge.%s 必须是 ISO8601 "
                    "字符串或 null" % key for e in errors),
                    (key, repr(bad)))

    def test_missing_leaf_keys_legal(self):
        # 存在才校验（缺键合法按默认解释）：半定义 wake_bridge 不报错
        st = make_state()
        st["continuation"] = {"obligation": "wake_required"}
        self.assertEqual(state.validate_state(st), [])
        st = make_state()
        st["continuation"] = {"wake_bridge": {"status": "fired"}}
        self.assertEqual(state.validate_state(st), [])
        st = make_state()
        st["continuation"] = {}
        self.assertEqual(state.validate_state(st), [])

    def test_unknown_keys_ignored(self):
        # 未知键忽略（向前兼容），块内外一致
        st = make_state()
        st["continuation"]["future_key"] = {"whatever": 1}
        st["continuation"]["wake_bridge"]["future_field"] = 1
        self.assertEqual(state.validate_state(st), [])

    def test_aggregates_errors_not_shortcircuit(self):
        # 多处非法聚合全部错误，不短路
        st = make_state()
        st["continuation"]["obligation"] = "bogus"
        st["continuation"]["reason"] = 42
        st["continuation"]["wake_bridge"]["status"] = "queued"
        st["continuation"]["wake_bridge"]["boundary_id"] = ""
        st["continuation"]["wake_bridge"]["wake_at"] = "yesterday"
        errors = state.validate_state(st)
        self.assertTrue(any(e.startswith("continuation.obligation")
                            for e in errors))
        self.assertTrue(any(e == "continuation.reason 必须是字符串或 null"
                            for e in errors))
        self.assertTrue(any(
            e.startswith("continuation.wake_bridge.status")
            for e in errors))
        self.assertTrue(any("continuation.wake_bridge.boundary_id" in e
                            for e in errors))
        self.assertTrue(any("continuation.wake_bridge.wake_at" in e
                            for e in errors))

    def test_invalid_continuation_not_saved(self):
        # 保存闸：非法 continuation 拒绝落盘且错误消息含路径
        st = make_state()
        st["continuation"]["obligation"] = "bogus"
        with tempfile.TemporaryDirectory() as tmp, \
                self.assertRaises(ValueError) as ctx:
            state.save_state(tmp, st)
        self.assertIn("continuation.obligation", str(ctx.exception))
        # 校验先于落盘：非法块不得写出任何文件
        self.assertFalse(state.state_path(tmp, TID).exists())


# —— validate_state 规则 8.8 增补（v2.2 M1a，D15-e Persistent Bridge） ——

class ValidateBridgeExtensionTest(unittest.TestCase):
    """wake_bridge 五扩展键 + 10 值 status 的形状校验。"""

    def test_new_status_values_all_valid(self):
        # 新三态（retarget_required / degraded / paused）全量合法，且
        # 原 7 值顺序不变（保持前缀）
        self.assertEqual(
            state.WAKE_BRIDGE_STATUSES[:4],
            ("none", "requested", "armed", "fired"))
        self.assertEqual(
            state.WAKE_BRIDGE_STATUSES[7:],
            ("cancelled", "stale", "failed"))
        for good in ("retarget_required", "degraded", "paused"):
            st = make_state()
            st["continuation"]["wake_bridge"]["status"] = good
            self.assertEqual(state.validate_state(st), [], good)

    def test_mode_enum(self):
        for good in state.WAKE_BRIDGE_MODES:
            st = make_state()
            st["continuation"]["wake_bridge"]["mode"] = good
            self.assertEqual(state.validate_state(st), [], good)
        for bad in ("interval", "RECURRING", "", None, 3, True):
            st = make_state()
            st["continuation"]["wake_bridge"]["mode"] = bad
            errors = state.validate_state(st)
            self.assertTrue(any(
                e.startswith("continuation.wake_bridge.mode")
                for e in errors), repr(bad))

    def test_generation_null_nonnegative_int(self):
        for good in (None, 0, 7):
            st = make_state()
            st["continuation"]["wake_bridge"]["generation"] = good
            self.assertEqual(state.validate_state(st), [], repr(good))
        for bad in (-1, True, False, 1.5, "0", [1]):
            st = make_state()
            st["continuation"]["wake_bridge"]["generation"] = bad
            errors = state.validate_state(st)
            self.assertTrue(any(
                e == "continuation.wake_bridge.generation 必须是 >= 0 "
                     "的整数或 null" for e in errors), repr(bad))

    def test_current_boundary_id_null_or_nonempty_str(self):
        for good in (None, "five_hour:2026-09-01T18:00:00+00:00"):
            st = make_state()
            st["continuation"]["wake_bridge"]["current_boundary_id"] = good
            self.assertEqual(state.validate_state(st), [], repr(good))
        for bad in ("", 42, ["x"], True):
            st = make_state()
            st["continuation"]["wake_bridge"]["current_boundary_id"] = bad
            errors = state.validate_state(st)
            self.assertTrue(any(
                e == "continuation.wake_bridge.current_boundary_id "
                     "必须是非空字符串或 null" for e in errors), repr(bad))

    def test_next_wake_at_null_or_iso8601(self):
        for good in (None, TS, TS_Z, "2026-09-01T12:00:00"):
            st = make_state()
            st["continuation"]["wake_bridge"]["next_wake_at"] = good
            self.assertEqual(state.validate_state(st), [], repr(good))
        for bad in ("tomorrow", "2026-13-40T00:00:00", "", 12345, ["x"]):
            st = make_state()
            st["continuation"]["wake_bridge"]["next_wake_at"] = bad
            errors = state.validate_state(st)
            self.assertTrue(any(
                e == "continuation.wake_bridge.next_wake_at 必须是 ISO8601 "
                     "字符串或 null" for e in errors), repr(bad))

    def test_bridge_interval_minutes_boundaries(self):
        # 5-1440 闭区间：两端点合法，越界一步即拒；null 合法（未定间隔）
        for good in (None, 5, 60, 1439, 1440):
            st = make_state()
            st["continuation"]["wake_bridge"]["bridge_interval_minutes"] = good
            self.assertEqual(state.validate_state(st), [], repr(good))
        for bad in (4, 1441, 0, -30, True, False, "60", 90.5, [60]):
            st = make_state()
            st["continuation"]["wake_bridge"]["bridge_interval_minutes"] = bad
            errors = state.validate_state(st)
            self.assertTrue(any(
                e == "continuation.wake_bridge.bridge_interval_minutes "
                     "必须是 5-1440 的整数或 null" for e in errors),
                repr(bad))

    def test_wu_22_01_shape_still_valid(self):
        # wu-22-01 已写形态：7 键 wake_bridge、无 scheduler_context /
        # tombstone——缺键按默认解释，仍然合法（迁移兼容锚点）
        st = make_state()
        st["continuation"] = {
            "obligation": "armed",
            "reason": "quota draining, bridge armed",
            "wake_bridge": {
                "status": "armed",
                "boundary_id": "five_hour:2026-09-01T18:00:00+00:00",
                "automation_id": "cron-wake-1",
                "reset_at": "2026-09-01T18:00:00+00:00",
                "wake_at": "2026-09-01T18:05:00+00:00",
                "armed_at": TS,
                "fired_at": None,
            },
        }
        self.assertEqual(state.validate_state(st), [])
        with tempfile.TemporaryDirectory() as tmp:
            state.save_state(tmp, st)
            loaded = state.load_state(tmp, TID)
            self.assertEqual(loaded["continuation"],
                             st["continuation"])


class ValidateSchedulerContextTest(unittest.TestCase):
    """scheduler_context 能力事实块的形状校验（D15-e）。"""

    def test_non_dict_rejected_and_null_tolerated(self):
        st = make_state()
        st["continuation"]["scheduler_context"] = "interactive"
        self.assertTrue(any(
            e == "continuation.scheduler_context 必须是 JSON 对象或 null"
            for e in state.validate_state(st)))
        # 显式 null 视同缺省 → 合法
        st = make_state()
        st["continuation"]["scheduler_context"] = None
        self.assertEqual(state.validate_state(st), [])

    def test_origin_enum(self):
        for good in state.SCHEDULER_ORIGINS:
            st = make_state()
            st["continuation"]["scheduler_context"]["origin"] = good
            self.assertEqual(state.validate_state(st), [], good)
        for bad in ("cron", "INTERACTIVE", "", None, 1):
            st = make_state()
            st["continuation"]["scheduler_context"]["origin"] = bad
            errors = state.validate_state(st)
            self.assertTrue(any(
                e.startswith("continuation.scheduler_context.origin")
                for e in errors), repr(bad))

    def test_capabilities_enum(self):
        for key in ("create", "update", "pause", "delete"):
            for good in state.SCHEDULER_CAPABILITIES:
                st = make_state()
                st["continuation"]["scheduler_context"][key] = good
                self.assertEqual(state.validate_state(st), [], (key, good))
            for bad in ("maybe", "ALLOWED", "", None, True):
                st = make_state()
                st["continuation"]["scheduler_context"][key] = bad
                errors = state.validate_state(st)
                self.assertTrue(any(
                    e.startswith(
                        "continuation.scheduler_context.%s" % key)
                    for e in errors), (key, repr(bad)))

    def test_parent_automation_id_null_or_nonempty_str(self):
        for good in (None, "cron-parent-1"):
            st = make_state()
            st["continuation"]["scheduler_context"][
                "parent_automation_id"] = good
            self.assertEqual(state.validate_state(st), [], repr(good))
        for bad in ("", 42, ["x"], True):
            st = make_state()
            st["continuation"]["scheduler_context"][
                "parent_automation_id"] = bad
            errors = state.validate_state(st)
            self.assertTrue(any(
                e == "continuation.scheduler_context.parent_automation_id "
                     "必须是非空字符串或 null" for e in errors), repr(bad))

    def test_unknown_keys_ignored(self):
        st = make_state()
        st["continuation"]["scheduler_context"]["future_probe"] = 1
        self.assertEqual(state.validate_state(st), [])


class ValidateTombstoneTest(unittest.TestCase):
    """tombstone（bridge 退役墓碑）形状校验（D15-e）。"""

    @staticmethod
    def valid_tombstone():
        return {"task_id": "cont-task-9z8y7x", "status": "completed",
                "completed_at": TS, "bridge_should_noop": True}

    def test_null_and_valid_dict_are_valid(self):
        for good in (None, self.valid_tombstone()):
            st = make_state()
            st["continuation"]["tombstone"] = good
            self.assertEqual(state.validate_state(st), [], repr(good))

    def test_non_dict_rejected(self):
        for bad in ("done", 42, True, ["x"]):
            st = make_state()
            st["continuation"]["tombstone"] = bad
            errors = state.validate_state(st)
            self.assertTrue(any(
                e == "continuation.tombstone 必须是 JSON 对象或 null"
                for e in errors), repr(bad))

    def test_missing_keys_rejected(self):
        for key in ("task_id", "status", "completed_at",
                    "bridge_should_noop"):
            tombstone = self.valid_tombstone()
            del tombstone[key]
            st = make_state()
            st["continuation"]["tombstone"] = tombstone
            errors = state.validate_state(st)
            self.assertTrue(any(
                e == "continuation.tombstone 缺少必填键 %s" % key
                for e in errors), key)

    def test_task_id_must_be_nonempty_str(self):
        for bad in ("", None, 42, True):
            tombstone = self.valid_tombstone()
            tombstone["task_id"] = bad
            st = make_state()
            st["continuation"]["tombstone"] = tombstone
            errors = state.validate_state(st)
            self.assertTrue(any(
                e == "continuation.tombstone.task_id 必须是非空字符串"
                for e in errors), repr(bad))

    def test_status_must_be_completed(self):
        for bad in ("failed", "cancelled", "COMPLETED", None, 3):
            tombstone = self.valid_tombstone()
            tombstone["status"] = bad
            st = make_state()
            st["continuation"]["tombstone"] = tombstone
            errors = state.validate_state(st)
            self.assertTrue(any(
                e.startswith("continuation.tombstone.status")
                for e in errors), repr(bad))

    def test_completed_at_must_be_iso8601(self):
        for bad in ("", "yesterday", "2026-13-40T00:00:00", None, 12345):
            tombstone = self.valid_tombstone()
            tombstone["completed_at"] = bad
            st = make_state()
            st["continuation"]["tombstone"] = tombstone
            errors = state.validate_state(st)
            self.assertTrue(any(
                e == "continuation.tombstone.completed_at 必须是 "
                     "ISO8601 字符串" for e in errors), repr(bad))

    def test_bridge_should_noop_must_be_true(self):
        for bad in (False, None, 1, "yes"):
            tombstone = self.valid_tombstone()
            tombstone["bridge_should_noop"] = bad
            st = make_state()
            st["continuation"]["tombstone"] = tombstone
            errors = state.validate_state(st)
            self.assertTrue(any(
                e == "continuation.tombstone.bridge_should_noop 必须为"
                     "布尔 true" for e in errors), repr(bad))

    def test_unknown_keys_ignored_and_errors_aggregated(self):
        # 未知键忽略；多处非法聚合全部错误不短路
        tombstone = self.valid_tombstone()
        tombstone["future_field"] = 1
        st = make_state()
        st["continuation"]["tombstone"] = tombstone
        self.assertEqual(state.validate_state(st), [])
        tombstone = self.valid_tombstone()
        tombstone["task_id"] = ""
        tombstone["status"] = "failed"
        tombstone["completed_at"] = "yesterday"
        tombstone["bridge_should_noop"] = False
        st = make_state()
        st["continuation"]["tombstone"] = tombstone
        errors = state.validate_state(st)
        self.assertTrue(any("continuation.tombstone.task_id" in e
                            for e in errors))
        self.assertTrue(any("continuation.tombstone.status" in e
                            for e in errors))
        self.assertTrue(any("continuation.tombstone.completed_at" in e
                            for e in errors))
        self.assertTrue(any("continuation.tombstone.bridge_should_noop" in e
                            for e in errors))

    def test_retired_shape_roundtrips(self):
        # 退役后的典型形状：整状态合法且保存往返保持
        st = make_state()
        st["continuation"]["scheduler_context"] = {
            "origin": "scheduled_task", "create": "forbidden",
            "update": "unknown", "pause": "allowed", "delete": "allowed",
            "parent_automation_id": "cron-parent-1"}
        st["continuation"]["wake_bridge"].update({
            "status": "paused", "mode": "self_retiming", "generation": 3,
            "current_boundary_id": "weekly:2026-09-07T00:00:00+00:00",
            "next_wake_at": "2026-09-01T18:05:00+00:00",
            "bridge_interval_minutes": 60})
        st["continuation"]["tombstone"] = self.valid_tombstone()
        self.assertEqual(state.validate_state(st), [])
        with tempfile.TemporaryDirectory() as tmp:
            state.save_state(tmp, st)
            loaded = state.load_state(tmp, TID)
            self.assertEqual(loaded["continuation"],
                             st["continuation"])


if __name__ == "__main__":
    unittest.main()
