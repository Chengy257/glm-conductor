#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""chain-block-record 直派链块委派记录单元测试（v2.8.0 W-B3）。

覆盖（规格 §3 W-B3/W-B5 五态 + 取值边界）：
    1. 正常记录：active 任务追加恰一条 chain_block_delegated 事件
       （payload 恒含 block_id / role；--ownership 缺省时无该键、
       给出时携带）；journal-only——state.json 零写盘（前后全等）；
    2. 同 block_id 重复记录 → 拒绝（重复 = 调度错误信号；换 role
       同 block_id 仍拒），journal 仍恰一条事件；
    3. 任务缺失 → 拒绝（exit 1 / TASK_MISSING）；
    4. 终态任务（completed）→ 拒绝（终态冻结，
       TASK_TERMINAL_FROZEN 带 op 名 record_chain_block）；
    5. ownership 可选形态 + 取值边界：--ownership 携带进 payload；
       role 词汇外拒绝；空 block_id 拒绝；空 --ownership 拒绝；
       用法错误（参数个数 / 未知旗标）拒绝。

CLI 经 cli.main 直调（run_cli 捕获单行 JSON，断言退出码 0/1/2 与
错误消息）；任务层直调 record_chain_block 断言 ValueError 消息恰为
messages 单源新常量。全部离线：scratch 目录
tempfile.TemporaryDirectory，绝不触碰真实账本（本面无 change_id
git 依赖，无需 git fixture）。仅 Python 3 标准库（unittest +
tempfile），零第三方依赖。

运行：
    cd <repo_root> && python3 -X utf8 -m unittest tests.test_chain_block_record -v
"""

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "glm-conductor"))

from runtime import cli, journal, messages, state, task  # noqa: E402

TID = "chainblk-task-1a2b3c"


def run_cli(*args):
    """调用 cli.main 并捕获 stdout，返回 (退出码, 解析后 JSON)。"""
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        code = cli.main(list(args))
    return code, json.loads(buffer.getvalue())


class ChainBlockRecordCliTest(unittest.TestCase):
    """chain-block-record CLI 五态 + 取值边界（退出码 0/1/2）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = str(self._tmp.name)

    def make_task(self, task_id=TID):
        """经 runtime.task 构造缺省 active v2.4 任务。"""
        return task.create_task(
            self.repo, task_id, "直派链块委派记录测试",
            {"mode": "delegate", "assurance": "high"})

    def events(self, name, task_id=TID):
        """读取真实 journal 并过滤事件名。"""
        return [e for e in journal.read_events(self.repo, task_id)
                if e.get("event") == name]

    def test_normal_record_exit_0_journal_only(self):
        """正常记录：exit 0；恰一条事件；payload 恰 block_id/role；
        journal-only——state.json 前后全等（零写盘）。"""
        self.make_task()
        before = state.load_state(self.repo, TID)
        code, payload = run_cli("chain-block-record", self.repo, TID,
                                "block-1", "impl-flash")
        self.assertEqual(code, 0)
        self.assertEqual(payload, {"task_id": TID, "block_id": "block-1",
                                   "role": "impl-flash",
                                   "status": "active"})
        recorded = self.events("chain_block_delegated")
        self.assertEqual(len(recorded), 1)
        self.assertEqual(recorded[0]["block_id"], "block-1")
        self.assertEqual(recorded[0]["role"], "impl-flash")
        self.assertNotIn("ownership", recorded[0])  # 缺省无 ownership 键
        self.assertEqual(state.load_state(self.repo, TID), before)

    def test_duplicate_block_id_refused_exit_1(self):
        """同 block_id 重复记录 → exit 1（换 role 同 id 仍拒——块 id
        唯一，重复 = 调度错误信号）；journal 仍恰一条事件。"""
        self.make_task()
        code, _payload = run_cli("chain-block-record", self.repo, TID,
                                 "block-1", "impl")
        self.assertEqual(code, 0)
        code, payload = run_cli("chain-block-record", self.repo, TID,
                                "block-1", "impl-flash")
        self.assertEqual(code, 1)
        self.assertIn(
            messages.TASK_CHAIN_BLOCK_DUPLICATE % (TID, "block-1"),
            payload["error"])
        self.assertEqual(len(self.events("chain_block_delegated")), 1)

    def test_missing_task_refused_exit_1(self):
        """任务缺失 → exit 1，错误含 TASK_MISSING。"""
        code, payload = run_cli("chain-block-record", self.repo,
                                "ghost-task", "block-1", "impl")
        self.assertEqual(code, 1)
        self.assertIn(messages.TASK_MISSING % "ghost-task",
                      payload["error"])

    def test_terminal_task_refused_exit_1(self):
        """终态任务（completed）→ exit 1，终态冻结（op 名
        record_chain_block）。"""
        self.make_task()
        task.complete(self.repo, TID)
        code, payload = run_cli("chain-block-record", self.repo, TID,
                                "block-1", "impl")
        self.assertEqual(code, 1)
        self.assertIn(
            messages.TASK_TERMINAL_FROZEN
            % ("record_chain_block", TID, "completed"),
            payload["error"])
        self.assertEqual(self.events("chain_block_delegated"), [])

    def test_ownership_carried_into_payload(self):
        """--ownership 给出 → payload 携带 ownership 摘要键。"""
        self.make_task()
        code, payload = run_cli("chain-block-record", self.repo, TID,
                                "block-2", "impl", "--ownership",
                                "plugins/glm-conductor/runtime/**")
        self.assertEqual(code, 0)
        self.assertEqual(payload["block_id"], "block-2")
        recorded = self.events("chain_block_delegated")
        self.assertEqual(len(recorded), 1)
        self.assertEqual(recorded[0]["ownership"],
                         "plugins/glm-conductor/runtime/**")

    def test_value_and_usage_gates_exit_2(self):
        """role 词汇外 / 空 block_id / 空 --ownership / 参数个数 /
        未知旗标 → exit 2，零事件副作用。"""
        self.make_task()
        for args in (
                ("block-a", "reviewer"),            # role 词汇外
                ("", "impl"),                        # 空 block_id
                ("block-b", "impl", "--ownership", ""),  # 空 ownership
                ("block-c",),                        # 参数个数不足
                ("block-c", "impl", "extra"),        # 参数个数过多
                ("block-c", "impl", "--bogus", "x"),  # 未知旗标
                ("block-c", "impl", "--ownership")):  # 旗标缺取值
            code, payload = run_cli("chain-block-record", self.repo,
                                    TID, *args)
            self.assertEqual(code, 2, "args=%r 应退出码 2" % (args,))
            self.assertIn("error", payload)
        self.assertEqual(self.events("chain_block_delegated"), [])


class RecordChainBlockUnitTest(unittest.TestCase):
    """task.record_chain_block 任务层直调：返回值与 ValueError 消息
    恰为 messages 单源新常量。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = str(self._tmp.name)

    def make_task(self, task_id=TID):
        return task.create_task(
            self.repo, task_id, "直派链块委派记录测试",
            {"mode": "delegate", "assurance": "high"})

    def names(self, task_id=TID):
        return [e.get("event")
                for e in journal.read_events(self.repo, task_id)]

    def test_returns_loaded_state_and_appends_event(self):
        """返回载入的现有状态 dict（journal-only 不迁移状态）；
        ownership=None 时 payload 恰 block_id/role 两业务键。"""
        st = self.make_task()
        returned = task.record_chain_block(self.repo, TID, "block-1",
                                           "impl")
        self.assertEqual(returned, st)
        self.assertEqual(self.names(),
                         ["route_selected", "chain_block_delegated"])
        event = journal.read_events(self.repo, TID)[-1]
        self.assertEqual(event["block_id"], "block-1")
        self.assertEqual(event["role"], "impl")
        self.assertNotIn("ownership", event)

    def test_ownership_none_vs_given_payload_shape(self):
        """ownership 给出 → payload 附 ownership 键；None → 无该键。"""
        self.make_task()
        task.record_chain_block(self.repo, TID, "block-1", "impl-flash",
                                ownership="src/**")
        event = journal.read_events(self.repo, TID)[-1]
        self.assertEqual(event["ownership"], "src/**")

    def test_shape_rejections_match_single_source_messages(self):
        """block_id 空 / role 词汇外 / ownership 空 → ValueError 且
        消息恰为 messages 单源常量（先于任何读盘写盘）。"""
        self.make_task()
        cases = (
            (("", "impl"),
             messages.TASK_CHAIN_BLOCK_ID_EMPTY % ("",)),
            (("block-1", "reviewer"),
             messages.TASK_CHAIN_BLOCK_ROLE_INVALID
             % ("reviewer", ", ".join(task.CHAIN_BLOCK_ROLES))),
            (("block-1", "impl-flash", ""),
             messages.TASK_CHAIN_BLOCK_OWNERSHIP_INVALID % ("",)),
        )
        for args, expected in cases:
            with self.assertRaises(ValueError) as ctx:
                task.record_chain_block(self.repo, TID, *args)
            self.assertEqual(str(ctx.exception), expected,
                             "args=%r" % (args,))
        self.assertEqual(self.names(), ["route_selected"])  # 零副作用

    def test_duplicate_and_missing_and_terminal(self):
        """重复 block_id / 任务缺失 / 终态冻结 → ValueError 携带
        单源消息。"""
        self.make_task()
        task.record_chain_block(self.repo, TID, "block-1", "impl")
        with self.assertRaises(ValueError) as ctx:
            task.record_chain_block(self.repo, TID, "block-1",
                                    "impl-flash")
        self.assertIn(
            messages.TASK_CHAIN_BLOCK_DUPLICATE % (TID, "block-1"),
            str(ctx.exception))
        self.assertEqual(self.names().count("chain_block_delegated"), 1)
        with self.assertRaises(ValueError) as ctx:
            task.record_chain_block(self.repo, "ghost-task", "block-2",
                                    "impl")
        self.assertIn(messages.TASK_MISSING % "ghost-task",
                      str(ctx.exception))
        task.complete(self.repo, TID)
        with self.assertRaises(ValueError) as ctx:
            task.record_chain_block(self.repo, TID, "block-3", "impl")
        self.assertIn(
            messages.TASK_TERMINAL_FROZEN
            % ("record_chain_block", TID, "completed"),
            str(ctx.exception))
        self.assertEqual(self.names().count("chain_block_delegated"), 1)


if __name__ == "__main__":
    unittest.main()
