#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Task Wake Bridge 精确 wake 锚点测试（v2.3.0 §10 / W3，unit v23-w3）。

锚定对象：
    - runtime/cli.py 的 wake-record（arm 时锚点：登记落地即经 W1
      adapter retime 到记录的 wake 时刻 + zcode_db_path 持久化 +
      fail-open retime_applied/retime_warning 输出）、wake-arm（生产
      arm 入口：arm_transport 稳定通道真实记账 + armed 记录 wake_at
      锚定 + arm 失败零 retime）、wake-retime
      （fire 后锚点：executable_park_bridge / retime_boundary /
      retime_retry / retime_failed / no_bridge 五态决策）、wake-status
      （db_next_run_at / wake_at_matches_db / db_check_note 一致性
      观测）、wake-plan（additive advisory 字段）；
    - runtime/continuity/wake_bridge.py 的 universal_wake_prompt（新
      wake-retime 编号步骤 + 既有红线原文保留）；
    - runtime/activation_transport.py 的词汇表回归（TRANSPORT_KINDS
      不变，recurring_bridge 唯一 STABLE——零新增 transport 词汇）。

铁律对齐（v2.3 W3）：authorization / accounting / epoch / resume 语义
零改动（本文件全部走 mock 的 adapter/resolver，绝不触碰真实
~/.zcode DB 与网络；GLM_CONDUCTOR_HOME 不涉及）；一切 retime 失败
fail-open（登记照常成功、wake-retime 退出 0）。

夹具：tempfile 仓库 + runtime.state 真实构造（test_wake_planner.py
同款 make_task / arm_bridge 装置）+ unittest.mock.patch（resolver /
discover / inspect / retime 全 mock）。CLI 调用照 test_cli_extensions
先例：cli.main(list) + contextlib.redirect_stdout 捕获单行 JSON。
unittest + unittest.mock，零 pytest。

运行：
    cd <repo_root> && python3 -S -m unittest tests.test_wake_retime -v
"""

import io
import json
import os
import sys
import tempfile
import time
import unittest
from contextlib import contextmanager, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "glm-conductor"))
from runtime import cli, execution_policy, journal, state, task_manager
from runtime.activation_transport import (  # 重导断言用（词汇表回归）
    EXPERIMENTAL_TRANSPORTS, STABLE_TRANSPORT, TRANSPORT_KINDS)
from runtime.continuity import wake_bridge
from runtime.host import zcode_schedule
from runtime.quota import resolver

TID = "wake-retime-1a2b3c"
AID = "automation-wake-3f2a"
DB = "X:/mock/tasks-index.sqlite"
OTHER_DB = "X:/mock/other.sqlite"
RESET_AT = "2026-09-02T12:00:00Z"
WEEKLY_RESET = "2026-09-05T18:00:00Z"
WAKE_AT = "2026-09-02T12:05:00Z"  # RESET_AT + 300s（D10 数学，装置记忆值）
GRACE_MS = 300 * 1000             # plan_resume 缺省宽限（DEFAULT_GRACE_SECONDS）
PARK_MS = 365 * 86400 * 1000      # executable → 停摆一年
RETRY_MS = 300 * 1000             # 无已知边界 → 5 分钟短重试
ROUTE = {"mode": "solo", "delegability": "low", "assurance": "standard",
         "executor": "main", "continuity": "resumable"}


def ms_of(iso_z):
    """独立的期望值折算（绝不复用被测模块解析）：Z 形串 → epoch 毫秒。"""
    from datetime import datetime, timezone
    moment = datetime.strptime(iso_z, "%Y-%m-%dT%H:%M:%SZ")
    return int(moment.replace(tzinfo=timezone.utc).timestamp() * 1000)


# —— 测试夹具（构造法照抄 tests/test_wake_planner.py） ——

def make_task(root, *, status="executing", auto_resume="manual",
              source="default", max_windows=0, consumed=0):
    """真实落盘一个任务 state（new_task_state 构造 + save）。"""
    st = state.new_task_state(TID, "wake retime 测试目标", dict(ROUTE),
                              status=status)
    policy = st["execution_policy"]
    policy["continuity"]["auto_resume"] = auto_resume
    policy["continuity"]["max_quota_windows"] = max_windows
    policy["continuity"]["consumed_quota_windows"] = consumed
    policy["authorization"]["source"] = source
    if source == "user":
        policy["authorization"]["confirmed_at"] = "2026-09-01T00:00:00Z"
    state.save_state(root, st)
    return st


def arm_bridge(root, *, automation_id=AID, status="armed",
               wake_at=WAKE_AT, db_path=DB):
    """把任务的 wake_bridge 直接置为活桥（含 v2.3 W3 持久化的
    zcode_db_path；direct 装置——记账路径用例自行走 arm_wake_bridge）。"""
    st = state.load_state(root, TID)
    bridge = st["continuation"]["wake_bridge"]
    bridge["status"] = status
    bridge["boundary_id"] = "five_hour:%s" % RESET_AT
    bridge["current_boundary_id"] = "five_hour:%s" % RESET_AT
    bridge["automation_id"] = automation_id
    bridge["reset_at"] = RESET_AT
    bridge["wake_at"] = wake_at
    bridge["armed_at"] = "2026-09-01T18:19:27.900Z"
    bridge["mode"] = "recurring"
    bridge["generation"] = 0
    bridge["bridge_interval_minutes"] = 60
    bridge["zcode_db_path"] = db_path
    state.save_state(root, st)


def exhausted_window(kind="five_hour", reset_at=RESET_AT, used=100.0):
    """§27 snapshot 单窗（scheduler.evaluate 自行折算 status——
    used=100 / remaining=0 → EXHAUSTED）。"""
    return {"kind": kind, "used_percent": used,
            "remaining_percent": 0.0 if used >= 100 else 100.0 - used,
            "reset_at": reset_at}


def detail_of(status, *windows):
    """resolve_quota_detail 明细形状（键冻结四键，snapshot 透出）。"""
    return {"source": "provider", "status": status,
            "snapshot": {"windows": list(windows)} if windows else None,
            "fetched_at": "2026-09-02T10:59:00.000Z"}


def run_cli(*args):
    """调用 cli.main 并捕获 stdout，返回 (退出码, 解析后 JSON)。"""
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        code = cli.main(list(args))
    return code, json.loads(buffer.getvalue())


def state_bytes(root):
    """state.json 原始字节（零写断言用）。"""
    with open(state.state_path(root, TID), "rb") as fh:
        return fh.read()


def write_cache(root, *, status="AVAILABLE", windows=None):
    """按 resolver 缓存布局落盘 quota-cache.json（legacy 无指纹形态，
    身份闸保守信任——零凭证读取；test_wake_planner 同款装置）。"""
    directory = os.path.join(root, ".glm-conductor")
    os.makedirs(directory, exist_ok=True)
    payload = {
        "provider": "zai",
        "fetched_at": "2026-09-02T10:00:00.000Z",
        "status": status,
        "snapshot": {"windows": list(
            windows if windows is not None
            else [{"kind": "five_hour", "used_percent": 63.0,
                   "remaining_percent": 37.0, "reset_at": RESET_AT},
                  {"kind": "weekly", "used_percent": 20.0,
                   "remaining_percent": 80.0, "reset_at": WEEKLY_RESET}])},
    }
    with open(os.path.join(directory, resolver.CACHE_FILE_NAME), "w",
              encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, ensure_ascii=False)


class WakeRetimeCase(unittest.TestCase):
    """临时目录夹具：每个用例独立 repo_root（无 git 需求）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = self._tmp.name

    def bridge_block(self):
        st = state.load_state(self.root, TID)
        return st["continuation"]["wake_bridge"]

    def events(self, name):
        return [e for e in journal.read_events(self.root, TID)
                if e.get("event") == name]


@contextmanager
def patched_adapter(db=DB, *, retime_error=None, inspect_return=None,
                    inspect_error=None):
    """一次叠齐 W1 adapter 三函数 patch；yield (discover, inspect,
    retime) 三个 mock。discover 以 side_effect 复刻真实优先级
    （explicit_path > 缺省 db——mock 固定 return_value 会掩盖显式路径
    透传）。"""
    inspect_kwargs = ({"side_effect": inspect_error}
                      if inspect_error is not None
                      else {"return_value": inspect_return})
    with mock.patch(
            "runtime.host.zcode_schedule.discover_zcode_tasks_db",
            side_effect=lambda explicit=None: (
                os.fspath(explicit) if explicit else db)) as discover_mock, \
            mock.patch(
                "runtime.host.zcode_schedule.inspect_automation",
                **inspect_kwargs) as inspect_mock, \
            mock.patch(
                "runtime.host.zcode_schedule.retime_automation",
                side_effect=retime_error) as retime_mock:
        yield discover_mock, inspect_mock, retime_mock


# —— 1/2. wake-record：arm 时锚点（落地即 retime，fail-open） ——

class WakeRecordAnchorTest(WakeRetimeCase):
    """wake-record 登记后立即 retime（§10 arm 时锚点）。"""

    def setUp(self):
        super().setUp()
        # SH-21-01 写入前授权三查：授予 until_done / 2 窗用户授权
        make_task(self.root, auto_resume="until_done", source="user",
                  max_windows=2)

    def test_wake_record_retimes_to_recorded_wake_at(self):
        with patched_adapter(db=DB) as (discover, _inspect, retime):
            code, payload = run_cli("wake-record", self.root, TID, AID,
                                    WAKE_AT)
        self.assertEqual(code, 0)
        # 登记五键原样 + retime_applied: true（无 warning 键）
        self.assertEqual(payload["automation_id"], AID)
        self.assertEqual(payload["fires_at"], WAKE_AT)
        self.assertEqual(payload["consumed_quota_windows"], 1)
        self.assertIs(payload["retime_applied"], True)
        self.assertNotIn("retime_warning", payload)
        # retime 收到的参数 = 记录的 wake 时刻（D10 数学不变，只锚定）
        self.assertEqual(retime.call_count, 1)
        self.assertEqual(retime.call_args,
                         mock.call(DB, AID, ms_of(WAKE_AT)))
        discover.assert_called_once_with(None)
        # zcode_db_path 已持久化进 bridge 记账（additive 键）
        self.assertEqual(self.bridge_block().get("zcode_db_path"), DB)

    def test_wake_record_explicit_db_flag_overrides(self):
        with patched_adapter(db=DB) as (discover, _inspect, retime):
            code, payload = run_cli("wake-record", self.root, TID, AID,
                                    WAKE_AT, "--db", OTHER_DB)
        self.assertEqual(code, 0)
        self.assertIs(payload["retime_applied"], True)
        discover.assert_called_once_with(OTHER_DB)
        self.assertEqual(retime.call_args,
                         mock.call(OTHER_DB, AID, ms_of(WAKE_AT)))
        self.assertEqual(self.bridge_block().get("zcode_db_path"),
                         OTHER_DB)

    def test_wake_record_retime_failure_fails_open(self):
        error = zcode_schedule.ZcodeScheduleBusyError("db locked")
        with patched_adapter(db=DB, retime_error=error):
            code, payload = run_cli("wake-record", self.root, TID, AID,
                                    WAKE_AT)
        self.assertEqual(code, 0)  # fail-open：登记照常成功
        # 登记本身完整：五键原样 + journal 事件在案
        self.assertEqual(payload["automation_id"], AID)
        self.assertEqual(payload["consumed_quota_windows"], 1)
        self.assertEqual(len(self.events("quota_wake_recorded")), 1)
        # retime 失败显式上报：retime_applied: false + 异常摘要
        self.assertIs(payload["retime_applied"], False)
        self.assertIn("retime_warning", payload)
        self.assertIn("ZcodeScheduleBusyError", payload["retime_warning"])
        # bridge 记账完整（zcode_db_path 已持久化，状态语义零变化）
        self.assertEqual(self.bridge_block().get("zcode_db_path"), DB)
        self.assertEqual(self.bridge_block().get("status"), "none")

    def test_wake_record_unparsable_fires_at_skips_retime(self):
        with patched_adapter(db=DB) as (_discover, _inspect, retime):
            code, payload = run_cli("wake-record", self.root, TID, AID,
                                    "not-a-timestamp")
        self.assertEqual(code, 0)
        self.assertIs(payload["retime_applied"], False)
        self.assertIn("fires_at", payload["retime_warning"])
        retime.assert_not_called()
        self.assertEqual(len(self.events("quota_wake_recorded")), 1)


# —— wake-arm：生产 arm 入口（arm_transport 稳定通道）+ retime 锚定 ——

class WakeArmTest(WakeRetimeCase):
    """wake-arm（v2.3 W3 补口）：记账经 activation_transport.arm_transport
    稳定通道（内部委托 arm_wake_bridge，真实记账不 mock）；成功后执行
    与 wake-record 相同的锚定（fail-open）；arm 失败非零退出且零
    retime。boundary/wake_at 与 wake-plan 同源同法（本地 cache + D10
    数学 → five_hour 最早 reset + 300s = WAKE_AT）。"""

    def setUp(self):
        super().setUp()
        make_task(self.root)
        write_cache(self.root)  # → boundary five_hour:RESET_AT / WAKE_AT

    def test_wake_arm_books_via_stable_channel_and_retimes(self):
        with patched_adapter(db=DB) as (_discover, _inspect, retime):
            code, payload = run_cli("wake-arm", self.root, TID, AID)
        self.assertEqual(code, 0)
        # arm 记账字段（arm_wake_bridge 实际返回键原样直出）
        self.assertEqual(payload["status"], "armed")
        self.assertEqual(payload["automation_id"], AID)
        self.assertEqual(payload["boundary_id"],
                         "five_hour:%s" % RESET_AT)
        self.assertEqual(payload["current_boundary_id"],
                         "five_hour:%s" % RESET_AT)
        self.assertEqual(payload["reset_at"], RESET_AT)
        self.assertEqual(payload["wake_at"], WAKE_AT)  # reset + 300s
        self.assertEqual(payload["mode"], "recurring")
        self.assertEqual(payload["generation"], 0)
        self.assertEqual(payload["bridge_interval_minutes"], 60)
        self.assertTrue(payload["armed_at"])
        # retime 锚定：目标 = armed 记录的 wake_at（D10 数学不改）
        self.assertIs(payload["retime_applied"], True)
        self.assertEqual(retime.call_args,
                         mock.call(DB, AID, ms_of(WAKE_AT)))
        # 真实记账落盘：bridge armed + zcode_db_path 持久化 + journal
        bridge = self.bridge_block()
        self.assertEqual(bridge["status"], "armed")
        self.assertEqual(bridge["zcode_db_path"], DB)
        self.assertEqual(len(self.events("wake_bridge_armed")), 1)

    def test_wake_arm_idempotent_replay_still_retimes(self):
        with patched_adapter(db=DB):
            run_cli("wake-arm", self.root, TID, AID)
        with patched_adapter(db=DB) as (_discover, _inspect, retime):
            code, payload = run_cli("wake-arm", self.root, TID, AID)
        self.assertEqual(code, 0)
        self.assertTrue(payload["idempotent"])  # 同 automation 同 boundary
        self.assertIs(payload["retime_applied"], True)
        self.assertEqual(retime.call_args,
                         mock.call(DB, AID, ms_of(WAKE_AT)))
        self.assertEqual(len(self.events("wake_bridge_armed")), 1)

    def test_wake_arm_retime_failure_fails_open_but_books(self):
        error = zcode_schedule.ZcodeScheduleBusyError("db locked")
        with patched_adapter(db=DB, retime_error=error):
            code, payload = run_cli("wake-arm", self.root, TID, AID)
        self.assertEqual(code, 0)  # fail-open：arm 记账不受影响
        self.assertEqual(payload["status"], "armed")
        self.assertIs(payload["retime_applied"], False)
        self.assertIn("ZcodeScheduleBusyError", payload["retime_warning"])
        # arm 记账照常落盘（journal + 状态）
        self.assertEqual(self.bridge_block()["status"], "armed")
        self.assertEqual(len(self.events("wake_bridge_armed")), 1)

    def test_wake_arm_transport_reserved_error_exit_nonzero_no_retime(self):
        from runtime.activation_transport import TransportReservedError
        with patched_adapter(db=DB) as (_discover, _inspect, retime), \
                mock.patch("runtime.activation_transport.arm_transport",
                           side_effect=TransportReservedError("reserved")):
            code, payload = run_cli("wake-arm", self.root, TID, AID)
        self.assertEqual(code, 1)  # 原样透传 → 非 0（意外异常兜底）
        self.assertIn("TransportReservedError", payload["error"])
        retime.assert_not_called()
        self.assertEqual(self.bridge_block()["status"], "none")

    def test_wake_arm_missing_task_exit_1_no_retime(self):
        # cache 提供合法 boundary → 越过参数闸后命中任务缺失（TaskManagerError）
        with patched_adapter(db=DB) as (_discover, _inspect, retime):
            code, payload = run_cli("wake-arm", self.root,
                                    "ghost-task-0001", AID)
        self.assertEqual(code, 1)
        self.assertIn("error", payload)
        retime.assert_not_called()

    def test_wake_arm_no_cache_no_boundary_rejected_exit_2_no_retime(self):
        # 无 cache → boundary/wake_at 双 None → arm 既有参数闸拒绝
        # （绝不虚构边界；ValueError = 校验拒绝类，退出码 2）
        os.remove(os.path.join(self.root, ".glm-conductor",
                               resolver.CACHE_FILE_NAME))
        with patched_adapter(db=DB) as (_discover, _inspect, retime):
            code, payload = run_cli("wake-arm", self.root, TID, AID)
        self.assertEqual(code, 2)
        self.assertIn("boundary_id", payload["error"])
        retime.assert_not_called()
        self.assertEqual(self.bridge_block()["status"], "none")


# —— 3. wake-retime：可执行 → park 本桥（365d 数学 + 零状态写） ——

class WakeRetimeExecutableParkTest(WakeRetimeCase):

    def setUp(self):
        super().setUp()
        make_task(self.root)
        arm_bridge(self.root)

    def test_executable_parks_bridge_for_365_days(self):
        before_ms = time.time() * 1000.0
        with patched_adapter(db=OTHER_DB) as (_discover, _inspect, retime), \
                mock.patch("runtime.quota.resolver.resolve_quota_detail",
                           return_value=detail_of("AVAILABLE")):
            code, payload = run_cli("wake-retime", self.root, TID)
        after_ms = time.time() * 1000.0
        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "executable_park_bridge")
        self.assertEqual(payload["automation_id"], AID)
        self.assertEqual(payload["quota_status"], "AVAILABLE")
        # 365d 数学：now + 365*86400*1000（常量锚定 + 时间窗界内）
        self.assertEqual(cli.WAKE_RETIME_PARK_OFFSET_MS, PARK_MS)
        target = retime.call_args[0][2]
        self.assertGreaterEqual(target, before_ms + PARK_MS)
        self.assertLessEqual(target, after_ms + PARK_MS)
        # 记账里的 zcode_db_path 优先（discover 缺省解析不被使用）
        _discover.assert_not_called()
        self.assertEqual(retime.call_args[0][0], DB)
        self.assertEqual(retime.call_args[0][1], AID)

    def test_executable_park_writes_no_task_state_no_journal(self):
        before_bytes = state_bytes(self.root)
        before_events = journal.read_events(self.root, TID)
        with patched_adapter(db=DB) as (_discover, _inspect, _retime), \
                mock.patch("runtime.quota.resolver.resolve_quota_detail",
                           return_value=detail_of("PRESSURE")):
            code, _payload = run_cli("wake-retime", self.root, TID)
        self.assertEqual(code, 0)
        # 纯节拍职责：零 task state 写、零 journal 写（PRESSURE 同样可执行）
        self.assertEqual(state_bytes(self.root), before_bytes)
        self.assertEqual(journal.read_events(self.root, TID),
                         before_events)


# —— 4. wake-retime：不可执行 + 已知边界 → retime = plan_resume 值 ——

class WakeRetimeBoundaryTest(WakeRetimeCase):

    def setUp(self):
        super().setUp()
        make_task(self.root)
        arm_bridge(self.root)

    def test_not_executable_retimes_to_latest_boundary_plus_grace(self):
        # 双窗同时 EXHAUSTED → plan_resume §30 口径 max(reset) + 300s
        detail = detail_of(
            "EXHAUSTED",
            exhausted_window("five_hour", RESET_AT),
            exhausted_window("weekly", WEEKLY_RESET))
        with patched_adapter(db=DB) as (_discover, _inspect, retime), \
                mock.patch("runtime.quota.resolver.resolve_quota_detail",
                           return_value=detail):
            code, payload = run_cli("wake-retime", self.root, TID)
        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "retime_boundary")
        self.assertEqual(payload["quota_status"], "EXHAUSTED")
        expected = ms_of(WEEKLY_RESET) + GRACE_MS  # 最晚 reset + 300s 宽限
        self.assertEqual(payload["next_target_at"], expected)
        self.assertEqual(retime.call_args,
                         mock.call(DB, AID, expected))


# —— 5. wake-retime：不可执行 + 无已知边界 → now + 5 分钟 ——

class WakeRetimeRetryTest(WakeRetimeCase):

    def setUp(self):
        super().setUp()
        make_task(self.root)
        arm_bridge(self.root)

    def test_not_executable_without_boundary_retries_in_5_minutes(self):
        # EXHAUSTED 但 reset 不可解析 → periodic_fallback（§31 不虚构）
        detail = detail_of("EXHAUSTED",
                           exhausted_window("five_hour", "garbage"))
        before_ms = time.time() * 1000.0
        with patched_adapter(db=DB) as (_discover, _inspect, retime), \
                mock.patch("runtime.quota.resolver.resolve_quota_detail",
                           return_value=detail):
            code, payload = run_cli("wake-retime", self.root, TID)
        after_ms = time.time() * 1000.0
        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "retime_retry")
        self.assertEqual(cli.WAKE_RETIME_RETRY_OFFSET_MS, RETRY_MS)
        target = retime.call_args[0][2]
        self.assertGreaterEqual(target, before_ms + RETRY_MS)
        self.assertLessEqual(target, after_ms + RETRY_MS)


# —— 6/7. wake-retime：adapter 异常 fail-open / 无桥 no-op ——

class WakeRetimeFailOpenTest(WakeRetimeCase):

    def setUp(self):
        super().setUp()
        make_task(self.root)
        arm_bridge(self.root)

    def test_adapter_error_reports_retime_failed_exit_0(self):
        detail = detail_of("EXHAUSTED", exhausted_window())
        with patched_adapter(db=DB, retime_error=RuntimeError("boom")) as \
                (_discover, _inspect, _retime), \
                mock.patch("runtime.quota.resolver.resolve_quota_detail",
                           return_value=detail):
            code, payload = run_cli("wake-retime", self.root, TID)
        self.assertEqual(code, 0)  # fail-open：watchdog 接管，不算错
        self.assertEqual(payload["status"], "retime_failed")
        self.assertIn("RuntimeError: boom", payload["warning"])
        self.assertEqual(payload["automation_id"], AID)


class WakeRetimeNoBridgeTest(WakeRetimeCase):

    def test_no_armed_bridge_short_circuits_before_refresh(self):
        make_task(self.root)  # bridge status = none
        with patched_adapter(db=DB) as (_discover, _inspect, retime), \
                mock.patch("runtime.quota.resolver.resolve_quota_detail",
                           return_value=detail_of("AVAILABLE")) as resolver_mock:
            code, payload = run_cli("wake-retime", self.root, TID)
        self.assertEqual(code, 0)  # 不算错
        self.assertEqual(payload["status"], "no_bridge")
        self.assertEqual(payload["bridge_status"], "none")
        # 无桥即短路：零 provider 解析、零 adapter 写
        resolver_mock.assert_not_called()
        retime.assert_not_called()

    def test_fired_bridge_is_still_retimed(self):
        make_task(self.root)
        arm_bridge(self.root, status="fired")
        with patched_adapter(db=DB) as (_discover, _inspect, _retime), \
                mock.patch("runtime.quota.resolver.resolve_quota_detail",
                           return_value=detail_of("AVAILABLE")):
            code, payload = run_cli("wake-retime", self.root, TID)
        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "executable_park_bridge")

    def test_missing_task_maps_to_no_bridge(self):
        with patched_adapter(db=DB) as (_discover, _inspect, _retime), \
                mock.patch("runtime.quota.resolver.resolve_quota_detail",
                           return_value=detail_of("AVAILABLE")):
            code, payload = run_cli("wake-retime", self.root,
                                    "ghost-task-0001")
        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "no_bridge")


# —— 8. wake-status：db_next_run_at 一致性观测（两态 + 明示） ——

class WakeStatusConsistencyTest(WakeRetimeCase):

    def setUp(self):
        super().setUp()
        make_task(self.root)
        arm_bridge(self.root)

    def test_db_next_run_at_matching_wake_at_is_true(self):
        row = {"automation_id": AID, "recurring": 1,
               "next_run_at": ms_of(WAKE_AT), "run_count": 1}
        with patched_adapter(db=DB, inspect_return=row):
            code, payload = run_cli("wake-status", self.root, TID)
        self.assertEqual(code, 0)
        self.assertEqual(payload["db_next_run_at"], ms_of(WAKE_AT))
        self.assertIs(payload["wake_at_matches_db"], True)
        self.assertIsNone(payload["db_check_note"])

    def test_db_next_run_at_mismatch_is_false(self):
        row = {"automation_id": AID, "recurring": 1,
               "next_run_at": ms_of(WAKE_AT) + 60000, "run_count": 1}
        with patched_adapter(db=DB, inspect_return=row):
            code, payload = run_cli("wake-status", self.root, TID)
        self.assertEqual(code, 0)
        self.assertIs(payload["wake_at_matches_db"], False)
        self.assertIsNone(payload["db_check_note"])

    def test_row_missing_is_explicit(self):
        with patched_adapter(db=DB, inspect_return=None):
            code, payload = run_cli("wake-status", self.root, TID)
        self.assertEqual(code, 0)
        self.assertIsNone(payload["db_next_run_at"])
        self.assertIsNone(payload["wake_at_matches_db"])
        self.assertEqual(payload["db_check_note"], "db_row_missing")

    def test_inspect_failure_is_explicit(self):
        with patched_adapter(db=DB,
                             inspect_error=zcode_schedule.ZcodeScheduleDbMissing(
                                 "gone")):
            code, payload = run_cli("wake-status", self.root, TID)
        self.assertEqual(code, 0)
        self.assertIsNone(payload["wake_at_matches_db"])
        self.assertTrue(payload["db_check_note"].startswith(
            "db_inspect_failed"))

    def test_no_automation_id_is_explicit(self):
        make_task(self.root)  # 覆写为无桥任务（automation_id=None）
        with patched_adapter(db=DB) as (_discover, inspect, _retime):
            code, payload = run_cli("wake-status", self.root, TID)
        self.assertEqual(code, 0)
        self.assertEqual(payload["db_check_note"], "no_automation_id")
        inspect.assert_not_called()


# —— 9. prompt 黄金断言：wake-retime 步骤 + 既有红线原文 ——

class UniversalWakePromptGoldenTest(WakeRetimeCase):

    def build_prompt(self):
        return task_manager.universal_wake_prompt(
            TID, ledger_root=self.root, repository_root=self.root,
            boundary_id="five_hour:%s" % RESET_AT, reset_at=RESET_AT,
            wake_at=WAKE_AT, mode="recurring",
            bridge_interval_minutes=60, automation_id=AID)

    def test_prompt_contains_wake_retime_step_after_force_refresh(self):
        prompt = self.build_prompt()
        runtime_dir = os.path.dirname(os.path.abspath(
            wake_bridge.__file__))
        command = "python3 %s/cli.py wake-retime %s %s" % (
            runtime_dir, self.root, TID)
        self.assertIn(command, prompt)
        # 步骤语义：可执行 → 停摆本桥；不可执行 → 下一边界或 5 分钟
        self.assertIn("停摆本桥", prompt)
        self.assertIn("5 分钟后重试", prompt)
        # 编号位置：紧随 force-refresh（步骤 1）之后、路径分叉（步骤 3）之前
        # （以 "cli.py wake-retime" 定位命令本身——TID 恰含 wake-retime 字样）
        self.assertGreater(
            prompt.index("cli.py wake-retime"),
            prompt.index("quota-resolve . --force-refresh"))
        self.assertLess(
            prompt.index("cli.py wake-retime"),
            prompt.index("3. 读 .glm-conductor/tasks/%s" % TID))
        # 后续步骤顺次重编号（原文不动）
        self.assertIn("3. 读 .glm-conductor/tasks/%s/state.json" % TID,
                      prompt)
        self.assertIn("9. 本轮一切 git 提交遵守", prompt)

    def test_prompt_keeps_existing_red_lines_verbatim(self):
        prompt = self.build_prompt()
        # Cron 禁令原文
        self.assertIn("严禁创建任何新的 Scheduled Task", prompt)
        self.assertIn("DO NOT CREATE A NEW SCHEDULED TASK", prompt)
        self.assertIn("本会话属于 scheduled task", prompt)
        # CronDelete 单次尝试原文
        self.assertIn("单次尝试 CronDelete 本 automation（绝不重试，"
                      "失败即容忍）", prompt)
        # 墓碑步骤原文
        self.assertIn("continuation.tombstone", prompt)
        self.assertIn("bridge_should_noop=true", prompt)
        # 同桥复用语义原文
        self.assertIn("reuse or retarget the existing bridge only",
                      prompt)
        # 零凭证
        for secret_marker in ("apiKey", "api_key", "Authorization",
                              "Bearer ", "sk-", "password", "token="):
            self.assertNotIn(secret_marker, prompt)


# —— 10. 词汇表回归：TRANSPORT_KINDS 不变（重导断言） ——

class TransportVocabularyRegressionTest(unittest.TestCase):
    """零新增 transport 词汇：recurring_bridge 仍是唯一 STABLE
    （activation_transport 词汇表不扩、不换名——v2.3 §10.1）。"""

    def test_transport_kinds_unchanged(self):
        self.assertEqual(
            TRANSPORT_KINDS,
            ("recurring_bridge", "probe_then_hold", "self_retiming",
             "session_injector"))
        self.assertEqual(STABLE_TRANSPORT, "recurring_bridge")
        self.assertEqual(
            EXPERIMENTAL_TRANSPORTS,
            ("probe_then_hold", "self_retiming", "session_injector"))

    def test_execution_policy_mirror_still_aligned(self):
        self.assertEqual(execution_policy.ACTIVATION_TRANSPORTS,
                         TRANSPORT_KINDS)
        self.assertEqual(execution_policy.DEFAULT_ACTIVATION_TRANSPORT,
                         STABLE_TRANSPORT)

    def test_bridge_interval_value_and_validation_unchanged(self):
        # §10.2 语义重标不改值、不改校验：默认仍 60
        self.assertEqual(
            execution_policy.DEFAULT_QUOTA_CONTROL[
                "bridge_interval_minutes"], 60)
        self.assertEqual(
            execution_policy.default_quota_control(None)[
                "bridge_interval_minutes"], 60)


if __name__ == "__main__":
    unittest.main()
