#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""runtime.quota.clock / clock_store 单元测试（v2.3.0 计划 §7 + §4
决策表，unit v23-w2a）。

锚定对象：Global Quota Clock 决策纯函数层 + 用户级 state 存储。
测试类映射：

    ClockConstantsTest       缺省常量与冻结词汇表（grace 120 / retry
                             300 / 三元决策 / 决策键集 / state 键集）
    SelectFiveHourWindowTest five_hour 窗口选取（首个命中 / 缺失 /
                             形状容错）
    FreshFutureResetTest     决策 6：fresh future reset →
                             reset_target，target=B+120000ms
    ReconfirmTest            决策 6：B>now 且 B==last_reset_at →
                             reconfirm（int 与 Z 形串两种形态）
    WindowAdvancedTest       决策 6：last_reset_at 非 None 且不等 →
                             window_advanced
    PastResetTest            决策 5：reset 已过期 → short_retry，
                             target=now+300000ms
    FiveHourMissingTest      决策 3：five_hour 缺失 → short_retry
    WeeklyParkTest           决策 2：weekly EXHAUSTED → weekly_park
                             （优先于有效的 five_hour reset）
    WeeklyUnparsableTest     决策 2：weekly EXHAUSTED 但 reset 全不
                             可解析 → short_retry
    SnapshotUnavailableTest  决策 1：snapshot None / 非 dict /
                             windows 非列表 → provider_unavailable
    FiveHourUnparsableTest   决策 4：reset_at 非法串 →
                             five_hour_unparsable
    CustomDelaysTest        自定义 grace / retry 生效（三条决策路径）
    DecisionValidationTest   now_ms / grace / retry 参数闸
    DecisionContractTest     决策值恒 ∈ CLOCK_DECISIONS、键集 ==
                             CLOCK_TARGET_KEYS（全场景扫描）
    ClockStatePathTest       路径解析三级优先 + 文件名净化单射
    BindLoadRoundtripTest    bind→load 往返全键相等 + 无 budget 字段
                             （架构裁决锚定）
    BindConflictTest         bound 态异 automation 冲突拒绝且原 state
                             不动；非 bound 态可改绑
    BindDeadBindingSelfHealTest
                             F-1（w35-dogfood-fix）死绑定自愈存储层
                             TOCTOU 守卫：verified_dead_automation_id
                             锁内相符放行干净覆盖 / 锁内漂移冲突拒绝
    BindIdempotentTest       同 automation 幂等重绑（保留既有合法
                             status）
    MalformedStateTest       malformed / 未知 schema → load None 且
                             bind 重写干净
    UpdateClockStateTest     RMW 成功路径 + status / 键集越界拒绝 +
                             缺文件拒绝

全部离线：决策测试零 I/O（snapshot 照 tests/test_quota_epoch.py 的
win() 构造法包进 {"windows": [...]}，保证与生产 §27 形态一致）；
存储测试经 GLM_CONDUCTOR_HOME 环境变量注入 tempfile 临时目录，绝不
触碰真实 ~/.glm-conductor；期望毫秒值用独立的 strptime 折算（不复用
被测模块的解析）。unittest + unittest.mock，零 pytest。

运行：
    cd <repo_root> && python3 -S -m unittest tests.test_quota_clock -v
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "glm-conductor"))
from runtime.quota import clock, clock_store

# —— 常量与装置（Z 形 §27 ISO 串；照抄 test_quota_epoch.py 的 win 法） ——


def ms_of(iso_z):
    """独立的期望值计算（绝不复用被测模块解析）：整秒 Z 形串 → epoch 毫秒。"""
    moment = datetime.strptime(iso_z, "%Y-%m-%dT%H:%M:%SZ")
    return int(moment.replace(tzinfo=timezone.utc).timestamp() * 1000)


def win(kind, status=None, reset_at=None):
    """§27 形状单窗口 dict（percent 字段与决策无关，省略）。"""
    return {"kind": kind, "status": status, "reset_at": reset_at}


def snap(*windows):
    """生产 snapshot 形态包装（clock 只读 windows 键）。"""
    return {"windows": list(windows)}


RESET_FIVE = "2026-09-02T21:59:00Z"
FIVE_MS = ms_of(RESET_FIVE)
RESET_WEEKLY = "2026-09-07T21:59:00Z"
WEEKLY_MS = ms_of(RESET_WEEKLY)
FUTURE_NOW = FIVE_MS - 3600000   # reset 前一小时
PAST_NOW = FIVE_MS + 60000       # reset 后一分钟（传播延迟场景）
GRACE_MS = 120000                # DEFAULT_GRACE_SECONDS=120 → 毫秒
RETRY_MS = 300000                # DEFAULT_RETRY_DELAY_SECONDS=300 → 毫秒

HASH_A = "a1b2c3d4e5f60718"
HASH_B = "b2c3d4e5f6071807"
AID_1 = "auto-clock-1"
AID_2 = "auto-clock-2"
REPO = "C:/work/repo"
DB_PATH = "C:/zcode/v2/tasks-index.sqlite"
RUNTIME_PATH = "C:/zcode/v2"
NOW_MS = 1750000000000
TICK_MS = 1750000600000
TARGET_MS = 1750003600000


def bind_default(hash_value=HASH_A, automation_id=AID_1, **overrides):
    """存储测试共用的 bind 调用（参数可覆盖）。"""
    params = {
        "zcode_db_path": DB_PATH,
        "runtime_path": RUNTIME_PATH,
        "now_ms": NOW_MS,
    }
    params.update(overrides)
    return clock_store.bind_clock_state(REPO, hash_value, automation_id,
                                        **params)


# —— 决策层：常量锚定 ——

class ClockConstantsTest(unittest.TestCase):

    def test_frozen_defaults_and_vocabularies(self):
        """缺省常量与词汇表逐字冻结。"""
        self.assertEqual(clock.DEFAULT_GRACE_SECONDS, 120)
        self.assertEqual(clock.DEFAULT_RETRY_DELAY_SECONDS, 300)
        self.assertEqual(
            clock.CLOCK_DECISIONS,
            ("reset_target", "short_retry", "weekly_park"))
        self.assertEqual(
            clock.CLOCK_TARGET_KEYS,
            ("decision", "target_epoch_ms", "reason", "reset_at",
             "reset_at_epoch_ms", "window_kind"))

    def test_state_keys_frozen_without_budget_fields(self):
        """state 键集冻结十五键，且绝无 budget / consumed 语义字段
        （2026-09-08 架构裁决：clock 无窗口预算）。"""
        self.assertEqual(
            clock_store.CLOCK_STATE_KEYS,
            ("schema_version", "provider_identity_hash", "automation_id",
             "owner_repository", "zcode_db_path", "runtime_path",
             "automation_model", "fallback_interval_minutes",
             "grace_seconds", "retry_delay_seconds", "last_tick_at",
             "last_reset_at", "next_target_at", "last_retime_at",
             "status"))
        for key in clock_store.CLOCK_STATE_KEYS:
            self.assertNotIn("budget", key.lower())
            self.assertNotIn("consumed", key.lower())
        self.assertEqual(clock_store.CLOCK_STATUSES,
                         ("bound", "parked_weekly", "rebound", "stale"))
        self.assertEqual(clock_store.CLOCK_STATE_SCHEMA_VERSION, 1)


# —— 决策层：five_hour 窗口选取 ——

class SelectFiveHourWindowTest(unittest.TestCase):

    FIVE = win("five_hour", "EXHAUSTED", RESET_FIVE)

    def test_returns_first_five_hour_window(self):
        """首个 kind=="five_hour" 的窗口原样返回（同一对象）。"""
        snapshot = snap(win("weekly", "AVAILABLE", RESET_WEEKLY), self.FIVE)
        self.assertIs(clock.select_five_hour_window(snapshot), self.FIVE)

    def test_none_when_absent_or_shape_invalid(self):
        """缺失 / snapshot 非 dict / windows 非列表 → None。"""
        self.assertIsNone(
            clock.select_five_hour_window(
                snap(win("weekly", "AVAILABLE", RESET_WEEKLY))))
        for snapshot in (None, 42, "junk", {"windows": None},
                         {"windows": "junk"}):
            self.assertIsNone(clock.select_five_hour_window(snapshot))

    def test_non_dict_entries_skipped(self):
        """非 dict 条目跳过，不影响命中（epoch 输入容错同口径）。"""
        snapshot = snap(None, 42, "window", self.FIVE)
        self.assertIs(clock.select_five_hour_window(snapshot), self.FIVE)


# —— 决策层：决策表逐行（每行一方法） ——

class FreshFutureResetTest(unittest.TestCase):

    def test_reset_target_with_default_grace(self):
        """决策 6：fresh future reset → reset_target，target=B+120000ms，
        reason=reconfirm（last_reset_at 未提供）。"""
        result = clock.next_clock_target(
            snap(win("five_hour", "EXHAUSTED", RESET_FIVE)),
            now_ms=FUTURE_NOW)
        self.assertEqual(result["decision"], "reset_target")
        self.assertEqual(result["target_epoch_ms"], FIVE_MS + GRACE_MS)
        self.assertEqual(result["reason"], "reconfirm")
        self.assertEqual(result["reset_at"], RESET_FIVE)
        self.assertEqual(result["reset_at_epoch_ms"], FIVE_MS)
        self.assertEqual(result["window_kind"], "five_hour")


class ReconfirmTest(unittest.TestCase):

    def test_same_last_reset_at_reconfirms(self):
        """决策 6：B>now 且 B==last_reset_at → reset_target/reconfirm
        （last_reset_at 支持 epoch 毫秒 int 与同刻 Z 形串两种形态）。"""
        snapshot = snap(win("five_hour", "AVAILABLE", RESET_FIVE))
        for last_reset in (FIVE_MS, RESET_FIVE):
            result = clock.next_clock_target(
                snapshot, now_ms=FUTURE_NOW, last_reset_at=last_reset)
            self.assertEqual(result["decision"], "reset_target")
            self.assertEqual(result["reason"], "reconfirm")
            self.assertEqual(result["target_epoch_ms"], FIVE_MS + GRACE_MS)


class WindowAdvancedTest(unittest.TestCase):

    def test_changed_last_reset_at_reports_window_advanced(self):
        """决策 6：last_reset_at 非 None 且 B != last_reset_at →
        window_advanced（窗口已推进；int 与异刻 Z 形串皆触发）。"""
        snapshot = snap(win("five_hour", "AVAILABLE", RESET_FIVE))
        for last_reset in (FIVE_MS - 1000, "2026-09-02T15:59:00Z"):
            result = clock.next_clock_target(
                snapshot, now_ms=FUTURE_NOW, last_reset_at=last_reset)
            self.assertEqual(result["decision"], "reset_target")
            self.assertEqual(result["reason"], "window_advanced")
            self.assertEqual(result["target_epoch_ms"], FIVE_MS + GRACE_MS)


class PastResetTest(unittest.TestCase):

    def test_elapsed_reset_short_retries_with_fixed_delay(self):
        """决策 5：B <= now（含 reset 未推进传播延迟）→ short_retry，
        target=now+300000ms，reset 字段仍填当前 five_hour 窗。"""
        result = clock.next_clock_target(
            snap(win("five_hour", "AVAILABLE", RESET_FIVE)),
            now_ms=PAST_NOW)
        self.assertEqual(result["decision"], "short_retry")
        self.assertEqual(result["target_epoch_ms"], PAST_NOW + RETRY_MS)
        self.assertEqual(result["reason"], "reset_elapsed")
        self.assertEqual(result["reset_at"], RESET_FIVE)
        self.assertEqual(result["reset_at_epoch_ms"], FIVE_MS)
        self.assertEqual(result["window_kind"], "five_hour")


class FiveHourMissingTest(unittest.TestCase):

    def test_missing_five_hour_short_retries(self):
        """决策 3：five_hour 缺失 → short_retry/five_hour_missing。"""
        result = clock.next_clock_target(
            snap(win("weekly", "AVAILABLE", RESET_WEEKLY)),
            now_ms=FUTURE_NOW)
        self.assertEqual(result["decision"], "short_retry")
        self.assertEqual(result["reason"], "five_hour_missing")
        self.assertEqual(result["target_epoch_ms"], FUTURE_NOW + RETRY_MS)
        self.assertIsNone(result["window_kind"])
        self.assertIsNone(result["reset_at"])
        self.assertIsNone(result["reset_at_epoch_ms"])


class WeeklyParkTest(unittest.TestCase):

    def test_exhausted_weekly_parks_until_reset_plus_grace(self):
        """决策 2：weekly EXHAUSTED（epoch 阻塞窗语义）→ weekly_park，
        target=weekly.reset_at+grace；优先级高于有效的 five_hour
        reset（决策表严格序）。"""
        result = clock.next_clock_target(
            snap(win("five_hour", "AVAILABLE", RESET_FIVE),
                 win("weekly", "EXHAUSTED", RESET_WEEKLY)),
            now_ms=FUTURE_NOW)
        self.assertEqual(result["decision"], "weekly_park")
        self.assertEqual(result["target_epoch_ms"], WEEKLY_MS + GRACE_MS)
        self.assertEqual(result["reason"], "weekly_blocked")
        self.assertEqual(result["reset_at"], RESET_WEEKLY)
        self.assertEqual(result["reset_at_epoch_ms"], WEEKLY_MS)
        self.assertEqual(result["window_kind"], "weekly")

    def test_available_weekly_does_not_park(self):
        """weekly 存在但不 EXHAUSTED → 不触发 park（blocked-EXHAUSTED
        才算耗尽压制），照常走 five_hour 决策。"""
        result = clock.next_clock_target(
            snap(win("five_hour", "AVAILABLE", RESET_FIVE),
                 win("weekly", "AVAILABLE", RESET_WEEKLY)),
            now_ms=FUTURE_NOW)
        self.assertEqual(result["decision"], "reset_target")


class WeeklyUnparsableTest(unittest.TestCase):

    def test_exhausted_weekly_with_unparsable_reset_short_retries(self):
        """决策 2 退化：weekly EXHAUSTED 但 reset 全不可解析（乱串 /
        缺失，§31 不虚构）→ short_retry/weekly_reset_unparsable。"""
        for reset in ("garbage", None):
            result = clock.next_clock_target(
                snap(win("five_hour", "AVAILABLE", RESET_FIVE),
                     win("weekly", "EXHAUSTED", reset)),
                now_ms=FUTURE_NOW)
            self.assertEqual(result["decision"], "short_retry")
            self.assertEqual(result["reason"], "weekly_reset_unparsable")
            self.assertEqual(result["target_epoch_ms"],
                             FUTURE_NOW + RETRY_MS)
            self.assertEqual(result["window_kind"], "weekly")
            self.assertIsNone(result["reset_at"])
            self.assertIsNone(result["reset_at_epoch_ms"])


class SnapshotUnavailableTest(unittest.TestCase):

    def test_invalid_snapshot_short_retries_as_provider_unavailable(self):
        """决策 1：snapshot None / 非 dict / windows 非列表 →
        short_retry/provider_unavailable（target=now+retry_delay）。"""
        for snapshot in (None, 42, "junk", {"windows": None},
                         {"windows": "junk"}, {"windows": {"a": 1}}):
            result = clock.next_clock_target(snapshot, now_ms=FUTURE_NOW)
            self.assertEqual(result["decision"], "short_retry",
                             repr(snapshot))
            self.assertEqual(result["reason"], "provider_unavailable",
                             repr(snapshot))
            self.assertEqual(result["target_epoch_ms"],
                             FUTURE_NOW + RETRY_MS)
            self.assertIsNone(result["window_kind"])
            self.assertIsNone(result["reset_at"])


class FiveHourUnparsableTest(unittest.TestCase):

    def test_unparsable_five_hour_reset_short_retries(self):
        """决策 4：reset_at 非法串 / 缺失 → short_retry/
        five_hour_unparsable（reset 字段不透传不可解析原文）。"""
        for reset in ("not-a-timestamp", None):
            result = clock.next_clock_target(
                snap(win("five_hour", "EXHAUSTED", reset),
                     win("weekly", "AVAILABLE", RESET_WEEKLY)),
                now_ms=FUTURE_NOW)
            self.assertEqual(result["decision"], "short_retry")
            self.assertEqual(result["reason"], "five_hour_unparsable")
            self.assertEqual(result["target_epoch_ms"],
                             FUTURE_NOW + RETRY_MS)
            self.assertEqual(result["window_kind"], "five_hour")
            self.assertIsNone(result["reset_at"])
            self.assertIsNone(result["reset_at_epoch_ms"])


class CustomDelaysTest(unittest.TestCase):

    def test_custom_grace_applies_to_all_targets(self):
        """自定义 grace 生效：reset_target 与 weekly_park 的 target 都
        随 grace 平移。"""
        result = clock.next_clock_target(
            snap(win("five_hour", "AVAILABLE", RESET_FIVE)),
            now_ms=FUTURE_NOW, grace_seconds=45)
        self.assertEqual(result["target_epoch_ms"], FIVE_MS + 45000)
        parked = clock.next_clock_target(
            snap(win("five_hour", "AVAILABLE", RESET_FIVE),
                 win("weekly", "EXHAUSTED", RESET_WEEKLY)),
            now_ms=FUTURE_NOW, grace_seconds=45)
        self.assertEqual(parked["decision"], "weekly_park")
        self.assertEqual(parked["target_epoch_ms"], WEEKLY_MS + 45000)

    def test_custom_retry_delay_applies_to_short_retry(self):
        """自定义 retry_delay 生效：reset 已过期 → target=now+7000ms。"""
        result = clock.next_clock_target(
            snap(win("five_hour", "AVAILABLE", RESET_FIVE)),
            now_ms=PAST_NOW, retry_delay_seconds=7)
        self.assertEqual(result["decision"], "short_retry")
        self.assertEqual(result["target_epoch_ms"], PAST_NOW + 7000)


class DecisionValidationTest(unittest.TestCase):

    def test_invalid_now_ms_grace_retry_raise(self):
        """参数闸：now_ms bool/非数值、grace/retry 负数/bool/非有限
        → ValueError（先于任何决策，镜像 epoch._validated_grace）。"""
        snapshot = snap(win("five_hour", "AVAILABLE", RESET_FIVE))
        for now_ms in (None, "123", True):
            with self.assertRaises(ValueError):
                clock.next_clock_target(snapshot, now_ms=now_ms)
        for bad in (-1, True, float("nan"), float("inf"), "120", None):
            with self.assertRaises(ValueError):
                clock.next_clock_target(snapshot, now_ms=FUTURE_NOW,
                                        grace_seconds=bad)
            with self.assertRaises(ValueError):
                clock.next_clock_target(snapshot, now_ms=FUTURE_NOW,
                                        retry_delay_seconds=bad)


class DecisionContractTest(unittest.TestCase):

    def _cases(self):
        """全决策分支的 snapshot 清单（含退化与容错输入）。"""
        return [
            None,
            "junk",
            {"windows": "junk"},
            snap(win("weekly", "AVAILABLE", RESET_WEEKLY)),
            snap(win("five_hour", "EXHAUSTED", "garbage")),
            snap(win("five_hour", "AVAILABLE", RESET_FIVE)),
            snap(win("five_hour", "AVAILABLE", RESET_FIVE),
                 win("weekly", "AVAILABLE", RESET_WEEKLY)),
            snap(win("five_hour", "AVAILABLE", RESET_FIVE),
                 win("weekly", "EXHAUSTED", RESET_WEEKLY)),
            snap(win("five_hour", "AVAILABLE", RESET_FIVE),
                 win("weekly", "EXHAUSTED", "garbage")),
            snap(None, 42, win("five_hour", "AVAILABLE", RESET_FIVE)),
        ]

    def test_decisions_in_vocabulary_and_keys_frozen(self):
        """全场景扫描：decision 恒 ∈ CLOCK_DECISIONS，键集恒 ==
        CLOCK_TARGET_KEYS，target 恒为 int。"""
        for snapshot in self._cases():
            for now_ms in (FUTURE_NOW, PAST_NOW):
                result = clock.next_clock_target(snapshot, now_ms=now_ms)
                self.assertIn(result["decision"], clock.CLOCK_DECISIONS,
                              repr(snapshot))
                self.assertEqual(set(result), set(clock.CLOCK_TARGET_KEYS))
                self.assertIsInstance(result["target_epoch_ms"], int)
                self.assertIsInstance(result["reason"], str)


# —— 存储层：路径解析与文件名净化 ——

class ClockStatePathTest(unittest.TestCase):

    def test_home_param_beats_env(self):
        """home 参数 > 环境变量 GLM_CONDUCTOR_HOME。"""
        with mock.patch.dict(os.environ,
                             {clock_store.GLM_CONDUCTOR_HOME_ENV: "E2"}):
            path = clock_store.clock_state_path(HASH_A, home="H1")
        self.assertEqual(path, os.path.join("H1", "quota-clocks",
                                            HASH_A + ".json"))

    def test_env_beats_default(self):
        """环境变量 > 默认 <~>/.glm-conductor。"""
        with mock.patch.dict(os.environ,
                             {clock_store.GLM_CONDUCTOR_HOME_ENV: "E2"}):
            path = clock_store.clock_state_path(HASH_A)
        self.assertEqual(path, os.path.join("E2", "quota-clocks",
                                            HASH_A + ".json"))

    def test_default_under_user_home(self):
        """默认：<~>/.glm-conductor/quota-clocks/<hash>.json（空串环境
        变量视为未设）。"""
        with mock.patch.dict(os.environ,
                             {clock_store.GLM_CONDUCTOR_HOME_ENV: ""}):
            path = clock_store.clock_state_path(HASH_A)
        self.assertEqual(
            path,
            os.path.join(os.path.expanduser("~"), ".glm-conductor",
                         "quota-clocks", HASH_A + ".json"))

    def test_path_attack_hash_sanitized_and_distinct(self):
        """路径攻击串净化为安全文件名（无分隔符、只含安全字符），且
        定长转义保证不同 hash 净化后互不相同（绝不互踩）。"""
        safe_chars = set(
            "abcdefghijklmnopqrstuvwxyz"
            "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
            "0123456789._-")
        attack = ".." + os.sep + ".." + os.sep + "evil"
        paths = [clock_store.clock_state_path(value, home="H")
                 for value in ("../x", ".._x", "x", attack)]
        for path in paths:
            name = os.path.basename(path)
            self.assertTrue(name.endswith(".json"))
            self.assertEqual(os.path.dirname(path),
                             os.path.join("H", "quota-clocks"))
            for char in name[:-len(".json")]:
                self.assertIn(char, safe_chars)
        self.assertEqual(len(set(paths)), 4)

    def test_empty_or_non_str_hash_rejected(self):
        """hash 空 / 非 str → ClockStateError（先于任何 I/O）。"""
        for bad in ("", None, 42):
            with self.assertRaises(clock_store.ClockStateError):
                clock_store.clock_state_path(bad)


# —— 存储层：bind / load / update ——

class StoreTestBase(unittest.TestCase):
    """存储测试基类：tempfile 临时目录 + GLM_CONDUCTOR_HOME 注入
    （绝不触碰真实 ~/.glm-conductor）。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="glm-clock-test-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        patcher = mock.patch.dict(os.environ, {
            clock_store.GLM_CONDUCTOR_HOME_ENV: self.tmp})
        patcher.start()
        self.addCleanup(patcher.stop)

    def state_path(self, hash_value=HASH_A):
        return clock_store.clock_state_path(hash_value)

    def write_raw(self, hash_value, text):
        """绕过 API 直接落一个原始文件（构造 malformed / legacy 态）。"""
        path = self.state_path(hash_value)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return path


class BindLoadRoundtripTest(StoreTestBase):

    def test_bind_then_load_roundtrip_all_keys(self):
        """bind→load 往返：全键相等、值与参数一致、时间观测键初始
        None、status=bound、文件落在注入的 home 下。"""
        state = bind_default(
            grace_seconds=90, retry_delay_seconds=240,
            fallback_interval_minutes=45, automation_model="glm-5.3")
        self.assertEqual(set(state), set(clock_store.CLOCK_STATE_KEYS))
        self.assertEqual(state["schema_version"], 1)
        self.assertEqual(state["provider_identity_hash"], HASH_A)
        self.assertEqual(state["automation_id"], AID_1)
        self.assertEqual(state["owner_repository"], REPO)
        self.assertEqual(state["zcode_db_path"], DB_PATH)
        self.assertEqual(state["runtime_path"], RUNTIME_PATH)
        self.assertEqual(state["automation_model"], "glm-5.3")
        self.assertEqual(state["grace_seconds"], 90)
        self.assertEqual(state["retry_delay_seconds"], 240)
        self.assertEqual(state["fallback_interval_minutes"], 45)
        for key in ("last_tick_at", "last_reset_at", "next_target_at",
                    "last_retime_at"):
            self.assertIsNone(state[key])
        self.assertEqual(state["status"], "bound")
        loaded = clock_store.load_clock_state(HASH_A)
        self.assertEqual(loaded, state)
        self.assertTrue(os.path.isfile(
            os.path.join(self.tmp, "quota-clocks", HASH_A + ".json")))

    def test_written_state_never_carries_budget_fields(self):
        """架构裁决锚定：落盘 state 任何键都不得出现 budget / consumed
        语义字段（clock 无窗口预算，预算 N 是 task 侧概念）。"""
        state = bind_default()
        for key in state:
            self.assertNotIn("budget", key.lower())
            self.assertNotIn("consumed", key.lower())


class BindConflictTest(StoreTestBase):

    def test_bound_conflict_rejects_and_preserves_original(self):
        """已存在 status=="bound" 且 automation_id 不同 →
        ClockStateConflictError，原 state 原样不动。"""
        first = bind_default()
        with self.assertRaises(clock_store.ClockStateConflictError):
            bind_default(automation_id=AID_2)
        self.assertEqual(clock_store.load_clock_state(HASH_A), first)

    def test_non_bound_status_allows_rebind_to_new_automation(self):
        """非 bound 态（如 stale）改绑新 automation 不冲突 → 干净
        bound 态。"""
        bind_default()
        clock_store.update_clock_state(
            HASH_A, lambda state: dict(state, status="stale"))
        rebound = bind_default(automation_id=AID_2)
        self.assertEqual(rebound["automation_id"], AID_2)
        self.assertEqual(rebound["status"], "bound")


class BindDeadBindingSelfHealTest(StoreTestBase):
    """F-1（v2.3.1 w35-dogfood-fix）死绑定自愈的存储层 TOCTOU 守卫。

    分工：失效核实（宿主 DB 行缺失检查）在 CLI 编排层（本文件零
    DB 概念）；存储层只认锁内身份复核——verified_dead_automation_id
    与锁内当前 automation_id 逐一相符才放行覆盖，缺省 / 不符一律
    ClockStateConflictError（fail-closed）。"""

    def test_verified_dead_id_replaces_dead_binding_clean(self):
        """锁内身份相符 → 放行覆盖：state 指向新 id、干净 bound 态
        （观测键重置）、键集冻结完整、落盘往返一致。"""
        bind_default()
        replaced = bind_default(automation_id=AID_2,
                                verified_dead_automation_id=AID_1)
        self.assertEqual(replaced["automation_id"], AID_2)
        self.assertEqual(replaced["status"], "bound")
        self.assertEqual(set(replaced), set(clock_store.CLOCK_STATE_KEYS))
        for key in ("last_tick_at", "last_reset_at", "next_target_at",
                    "last_retime_at"):
            self.assertIsNone(replaced[key])
        self.assertEqual(clock_store.load_clock_state(HASH_A), replaced)

    def test_verified_dead_id_mismatch_in_lock_still_conflict(self):
        """锁内漂移（锁内当前 id ≠ verified_dead_automation_id）→
        ClockStateConflictError，原 state 原样不动（TOCTOU 守卫）。"""
        first = bind_default()
        with self.assertRaises(clock_store.ClockStateConflictError):
            bind_default(automation_id=AID_2,
                         verified_dead_automation_id="auto-drifted-id")
        self.assertEqual(clock_store.load_clock_state(HASH_A), first)

    def test_verified_dead_id_none_keeps_conflict(self):
        """verified_dead_automation_id 缺省（None）→ 既有冲突语义逐字
        不变（活绑定保护初心完整保留）。"""
        first = bind_default()
        with self.assertRaises(clock_store.ClockStateConflictError):
            bind_default(automation_id=AID_2,
                         verified_dead_automation_id=None)
        self.assertEqual(clock_store.load_clock_state(HASH_A), first)


class BindIdempotentTest(StoreTestBase):

    def test_same_automation_rebind_is_idempotent_overwrite(self):
        """同 automation_id 幂等覆盖：参数随新调用更新、键集完整、
        落盘态与返回值一致。"""
        bind_default(grace_seconds=120)
        second = bind_default(grace_seconds=90)
        self.assertEqual(second["automation_id"], AID_1)
        self.assertEqual(second["status"], "bound")
        self.assertEqual(second["grace_seconds"], 90)
        self.assertEqual(set(second), set(clock_store.CLOCK_STATE_KEYS))
        self.assertEqual(clock_store.load_clock_state(HASH_A), second)

    def test_rebind_same_automation_preserves_valid_status(self):
        """同 automation 重绑保留既有合法 status（不改写运行态标签，
        规格裁决项「保持/置 bound」的本单元取定：保持）。"""
        bind_default()
        clock_store.update_clock_state(
            HASH_A, lambda state: dict(state, status="parked_weekly"))
        rebound = bind_default()
        self.assertEqual(rebound["status"], "parked_weekly")
        for key in ("last_tick_at", "last_reset_at", "next_target_at",
                    "last_retime_at"):
            self.assertIsNone(rebound[key])


class MalformedStateTest(StoreTestBase):

    def test_malformed_load_none_and_bind_rewrites_clean(self):
        """malformed 文件 → load None（容忍为无状态），bind 重写干净
        全键态。"""
        self.write_raw(HASH_A, "{not json")
        self.assertIsNone(clock_store.load_clock_state(HASH_A))
        state = bind_default()
        self.assertEqual(clock_store.load_clock_state(HASH_A), state)
        self.assertEqual(state["status"], "bound")
        self.assertEqual(set(state), set(clock_store.CLOCK_STATE_KEYS))

    def test_unknown_schema_and_non_dict_load_none(self):
        """schema_version 不认识 / 顶层非 dict → load None。"""
        self.write_raw(HASH_A, json.dumps(
            {"schema_version": 999, "status": "bound"}))
        self.assertIsNone(clock_store.load_clock_state(HASH_A))
        self.write_raw(HASH_B, json.dumps([1, 2, 3]))
        self.assertIsNone(clock_store.load_clock_state(HASH_B))

    def test_bind_over_non_dict_file_rewrites_clean(self):
        """顶层非 dict 的既有文件（如 JSON 数组）→ 视为无状态，bind
        幂等重写干净 bound 态（RMW 入参非 dict 的容错路径）。"""
        self.write_raw(HASH_A, json.dumps([1, 2, 3]))
        state = bind_default()
        self.assertEqual(clock_store.load_clock_state(HASH_A), state)
        self.assertEqual(state["status"], "bound")


class UpdateClockStateTest(StoreTestBase):

    def test_update_rmw_changes_status_and_observation_fields(self):
        """RMW 成功路径：updater 收到当前 state，status /
        last_tick_at / next_target_at 更新并落盘。"""
        bind_default()
        seen = {}

        def updater(state):
            seen["automation_id"] = state["automation_id"]
            return dict(state, status="parked_weekly",
                        last_tick_at=TICK_MS, next_target_at=TARGET_MS,
                        last_reset_at=FIVE_MS)

        result = clock_store.update_clock_state(HASH_A, updater)
        self.assertEqual(seen["automation_id"], AID_1)
        self.assertEqual(result["status"], "parked_weekly")
        self.assertEqual(result["last_tick_at"], TICK_MS)
        self.assertEqual(result["next_target_at"], TARGET_MS)
        self.assertEqual(result["last_reset_at"], FIVE_MS)
        loaded = clock_store.load_clock_state(HASH_A)
        self.assertEqual(loaded["status"], "parked_weekly")
        self.assertEqual(loaded["last_tick_at"], TICK_MS)

    def test_update_invalid_status_raises_and_file_unchanged(self):
        """status 越界 → ClockStateError，文件保持更新前状态。"""
        bind_default()
        before = clock_store.load_clock_state(HASH_A)

        def updater(state):
            return dict(state, status="bogus")

        with self.assertRaises(clock_store.ClockStateError):
            clock_store.update_clock_state(HASH_A, updater)
        self.assertEqual(clock_store.load_clock_state(HASH_A), before)

    def test_update_missing_required_key_raises(self):
        """更新后键集不是 CLOCK_STATE_KEYS 超集 → ClockStateError。"""
        bind_default()
        before = clock_store.load_clock_state(HASH_A)

        def updater(state):
            broken = dict(state)
            broken.pop("status")
            return broken

        with self.assertRaises(clock_store.ClockStateError):
            clock_store.update_clock_state(HASH_A, updater)
        self.assertEqual(clock_store.load_clock_state(HASH_A), before)

    def test_update_missing_file_raises(self):
        """文件不存在 → ClockStateError（出路是 bind 重写）。"""
        with self.assertRaises(clock_store.ClockStateError):
            clock_store.update_clock_state(HASH_B, lambda state: state)

    def test_update_on_malformed_file_raises(self):
        """malformed 文件（不可识别 v1 state）→ ClockStateError。"""
        self.write_raw(HASH_A, "[]")
        with self.assertRaises(clock_store.ClockStateError):
            clock_store.update_clock_state(HASH_A, lambda state: state)

    def test_home_param_scopes_to_alternate_store(self):
        """home 参数指向另一存储根时互不可见（与 env 根隔离）。"""
        bind_default()
        other = tempfile.mkdtemp(prefix="glm-clock-other-")
        self.addCleanup(shutil.rmtree, other, True)
        with self.assertRaises(clock_store.ClockStateError):
            clock_store.update_clock_state(HASH_A, lambda state: state,
                                           home=other)
        self.assertIsNone(clock_store.load_clock_state(HASH_A, home=other))


if __name__ == "__main__":
    unittest.main()
