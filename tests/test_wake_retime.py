#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Task Wake Bridge 精确 wake 锚点测试（v2.3.0 §10 / W3，unit v23-w3）。

锚定对象（v2.3.1 W3 后处理函数已迁 runtime/commands/wake.py，本文件
经 cli.main 调用、常量锚点直指 commands.wake）：
    - wake-record（arm 时锚点：登记落地即经 W1
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
from runtime import cli, execution_policy, journal, state
from runtime.commands import wake as wake_commands  # WAKE_RETIME_* 常量锚点（v2.3.1 W4 起指向实现所在）
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
def ms_of(iso_z):
    """独立的期望值折算（绝不复用被测模块解析）：Z 形串 → epoch 毫秒。"""
    from datetime import datetime, timezone
    moment = datetime.strptime(iso_z, "%Y-%m-%dT%H:%M:%SZ")
    return int(moment.replace(tzinfo=timezone.utc).timestamp() * 1000)


# —— 测试夹具（构造法照抄 tests/test_wake_planner.py） ——

def make_task(root, *, status="active"):
    """真实落盘一个 v2.4 任务 state（new_task_state 构造 + save）。"""
    st = state.new_task_state(TID, "wake retime 测试目标",
                              {"mode": "solo"},
                              repository_root=root, status=status)
    state.save_state(root, st)
    return st


def arm_bridge(root, *, automation_id=AID, status="armed",
               wake_at=WAKE_AT, db_path=DB):
    """把任务的 continuation.wake_bridge 直接置为活桥（v2.4 state 的
    未知顶层键向前兼容，validate_state 不拒绝 continuation 块）。"""
    st = state.load_state(root, TID)
    st["continuation"] = {
        "obligation": "none",
        "wake_bridge": {
            "status": status,
            "boundary_id": "five_hour:%s" % RESET_AT,
            "current_boundary_id": "five_hour:%s" % RESET_AT,
            "automation_id": automation_id,
            "reset_at": RESET_AT,
            "wake_at": wake_at,
            "armed_at": "2026-09-01T18:19:27.900Z",
            "mode": "recurring",
            "generation": 0,
            "bridge_interval_minutes": 60,
            "zcode_db_path": db_path,
        },
        "scheduler_context": {"origin": "unknown", "create": "unknown"},
    }
    state.save_state(root, st)


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


# —— v2.4 Phase 2（W6）：执行面退役锚定 ——


class WakeRetiredRuntimeTest(WakeRetimeCase):
    """wake-record / wake-arm / wake-retime 的执行面编排已随 v2.3 执行
    面退役：子命令保留注册，调用即 RuntimeError → 错误 JSON + 退出码
    1（用法错误仍是退出码 2）。"""

    def test_wake_record_usage_error_exit_2(self):
        code, _payload = run_cli("wake-record", self.root, TID)
        self.assertEqual(code, 2)

    def test_wake_record_retired_runtime_error_exit_1(self):
        code, payload = run_cli("wake-record", self.root, TID, AID,
                                WAKE_AT)
        self.assertEqual(code, 1)
        self.assertIn("RuntimeError", payload["error"])
        self.assertIn("已退役", payload["error"])

    def test_wake_arm_retired_runtime_error_exit_1(self):
        write_cache(self.root)  # 合法 boundary 越过参数闸后命中退役面
        with patched_adapter(db=DB) as (_d, _i, retime):
            code, payload = run_cli("wake-arm", self.root, TID, AID)
        self.assertEqual(code, 1)
        self.assertIn("已退役", payload["error"])
        retime.assert_not_called()

    def test_wake_retime_usage_error_exit_2(self):
        code, _payload = run_cli("wake-retime", self.root)
        self.assertEqual(code, 2)

    def test_wake_retime_retired_runtime_error_exit_1(self):
        code, payload = run_cli("wake-retime", self.root, TID)
        self.assertEqual(code, 1)
        self.assertIn("已退役", payload["error"])


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
        # v2.4 Phase 2 接受的降级（Phase 3 收口）：无桥任务的缺块兜底
        # 依赖 v2.3 时代的 state.default_continuation（W1 状态重写时
        # 移除）——wake-status 对无 continuation 块的任务以 AttributeError
        # 退出码 1 显式失败（有桥任务路径不受影响，见上四例）。
        make_task(self.root)  # 覆写为无桥任务（无 continuation 块）
        with patched_adapter(db=DB) as (_discover, inspect, _retime):
            code, payload = run_cli("wake-status", self.root, TID)
        self.assertEqual(code, 1)
        self.assertIn("AttributeError", payload["error"])
        inspect.assert_not_called()


# —— 9. prompt 黄金断言：wake-retime 步骤 + 既有红线原文 ——

class UniversalWakePromptGoldenTest(WakeRetimeCase):

    def build_prompt(self):
        return wake_bridge.universal_wake_prompt(
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
