#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""runtime.agent_run 单元测试（v2.1 M2 wu-21-03）。

覆盖 record_agent_launch / record_agent_failure /
record_launch_replay_skipped 的记账契约：
  - agent_launched 字段形状冻结（unit/permit_id/tool_use_id/agent_id/
    execution_mode），agent_id 提取形状自适应（驼峰/下划线/task_id/
    嵌套 result/缺失 None/非 dict 容错）；
  - agent_dispatch_failed 的 error 三来源（显式 > 载荷 error/message >
    unknown）与 200 字符截断、非 str 值压字符串；
  - replay_skipped 字段；
  - tool_use_id snake/camel 双命名提取；
  - 三事件与手写 implementation_started 是不同事件（互不替代）；
  - 事件经 journal.read_events 可回读（ts 由 journal 统一管理）。

仅 Python 3 标准库；临时目录 fixture，不污染真实工作区。
"""

import sys, unittest, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "glm-conductor"))
from runtime import agent_run, journal

TID = "agent-run-test-1a2b3c"
PERMIT = {"permit_id": "dp-abc123def456", "task_id": TID, "unit_id": "wu-1",
          "wave_id": None, "mode": "background", "reason": None,
          "created_at": "2026-08-30T00:00:00.000+00:00",
          "expires_at": "2026-08-30T00:30:00.000+00:00", "consumed": False}


class RecordAgentLaunchTest(unittest.TestCase):
    """agent_launched 事件字段形状与 agent_id 提取。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def _last(self):
        return journal.read_events(self.repo, TID)[-1]

    def test_fields_frozen(self):
        payload = {"tool_use_id": "call_1", "tool_response":
                   {"agentId": "agent_9"}}
        agent_run.record_agent_launch(self.repo, TID, payload, permit=PERMIT)
        event = self._last()
        self.assertEqual(event["event"], "agent_launched")
        self.assertEqual(event["unit"], "wu-1")
        self.assertEqual(event["permit_id"], "dp-abc123def456")
        self.assertEqual(event["tool_use_id"], "call_1")
        self.assertEqual(event["agent_id"], "agent_9")
        self.assertEqual(event["execution_mode"], "background")

    def test_agent_id_snake_and_task_fallback(self):
        for response, expect in (
                ({"agent_id": "agent_x"}, "agent_x"),
                ({"task_id": "agent_y"}, "agent_y"),
                ({"result": {"agentId": "agent_z"}}, "agent_z"),
                ({"unrelated": 1}, None),
                ("not-a-dict", None),
                ({"agentId": ""}, None)):
            payload = {"tool_use_id": "c", "tool_response": response}
            agent_run.record_agent_launch(self.repo, TID, payload,
                                          permit=PERMIT)
            self.assertEqual(self._last()["agent_id"], expect)

    def test_tool_use_id_camel_fallback_and_missing(self):
        for payload, expect in (({"toolUseId": "u1"}, "u1"),
                                ({"tool_use_id": "u2"}, "u2"),
                                ({}, None),
                                ("junk", None)):
            agent_run.record_agent_launch(self.repo, TID, payload,
                                          permit=PERMIT)
            self.assertEqual(self._last()["tool_use_id"], expect)

    def test_permit_none_tolerated(self):
        agent_run.record_agent_launch(self.repo, TID, {}, permit=None)
        event = self._last()
        self.assertIsNone(event["unit"])
        self.assertIsNone(event["permit_id"])

    def test_distinct_from_manual_implementation_started(self):
        journal.append_event(self.repo, TID, {"event": "implementation_started",
                                              "unit": "wu-1"})
        agent_run.record_agent_launch(
            self.repo, TID, {"tool_use_id": "c"},
            permit=PERMIT)
        events = journal.read_events(self.repo, TID)
        self.assertEqual([e["event"] for e in events],
                         ["implementation_started", "agent_launched"])


class RecordAgentFailureTest(unittest.TestCase):
    """agent_dispatch_failed 的 error 来源与截断。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def _last(self):
        return journal.read_events(self.repo, TID)[-1]

    def test_explicit_error_wins(self):
        agent_run.record_agent_failure(
            self.repo, TID, {"error": "payload-error"}, permit=PERMIT,
            error="explicit")
        self.assertEqual(self._last()["error"], "explicit")

    def test_payload_error_then_message_then_unknown(self):
        for payload, expect in (
                ({"error": "e1"}, "e1"),
                ({"message": "m1"}, "m1"),
                ({"error": 404}, "404"),
                ({}, "unknown"),
                ({"error": ""}, "unknown")):
            agent_run.record_agent_failure(self.repo, TID, payload,
                                           permit=PERMIT)
            self.assertEqual(self._last()["error"], expect)

    def test_truncated_to_200(self):
        agent_run.record_agent_failure(
            self.repo, TID, {}, permit=PERMIT, error="x" * 500)
        self.assertEqual(len(self._last()["error"]), 200)

    def test_permit_none_still_records(self):
        agent_run.record_agent_failure(self.repo, TID,
                                        {"tool_use_id": "c"}, permit=None)
        event = self._last()
        self.assertEqual(event["event"], "agent_dispatch_failed")
        self.assertIsNone(event["permit_id"])
        self.assertEqual(event["tool_use_id"], "c")


class RecordReplaySkippedTest(unittest.TestCase):
    """agent_launch_replay_skipped 幂等容错记账。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_fields(self):
        agent_run.record_launch_replay_skipped(
            self.repo, TID, {"tool_use_id": "c"},
            permit_id="dp-abc123def456")
        event = journal.read_events(self.repo, TID)[-1]
        self.assertEqual(event["event"], "agent_launch_replay_skipped")
        self.assertEqual(event["tool_use_id"], "c")
        self.assertEqual(event["permit_id"], "dp-abc123def456")


if __name__ == "__main__":
    unittest.main()
