#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""runtime.journal 单元测试（v2 工作块 B1.3）。

运行：
    python3 -m unittest tests.test_journal -v
"""

import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "glm-conductor"))
from runtime import journal

import json
import re
import tempfile
from datetime import datetime

TASK_ID = "task-test-7f3a2c"

# 自动生成 ts 的形态：UTC ISO8601，毫秒精度，偏移 "+00:00"
TS_AUTO_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}\+00:00$")


class JournalTestCase(unittest.TestCase):
    """公共临时仓库根目录基类。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = self._tmp.name

    # —— 辅助 ——

    def raw_lines(self, task_id=TASK_ID):
        """按原始文本行读取 journal 文件（不经过 journal 模块解析）。

        与 read_events 一致按 "\n" 切分（不用 splitlines，避免在
        U+0085/U+2028/U+2029 处错误断行）；文件以 "\n" 结尾时去掉末尾
        的空串元素，保持「一条事件一个元素」的语义。
        """
        path = journal.journal_path(self.root, task_id)
        with path.open("r", encoding="utf-8") as fh:
            lines = fh.read().split("\n")
        if lines and lines[-1] == "":
            lines.pop()
        return lines

    def write_raw(self, text, task_id=TASK_ID):
        """绕过 append_event 手工写 journal 文件（构造坏行/空白行场景）。"""
        path = journal.journal_path(self.root, task_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)


class TestJournalPath(JournalTestCase):

    def test_journal_path_layout(self):
        expected = (Path(self.root) / ".glm-conductor" / "tasks" / TASK_ID
                    / "events.jsonl")
        self.assertEqual(journal.journal_path(self.root, TASK_ID), expected)


class TestAppendEvent(JournalTestCase):

    def test_append_returns_event_with_ts(self):
        written = journal.append_event(
            self.root, TASK_ID,
            {"event": "verification", "command": "pytest", "status": "pass"})
        self.assertIsInstance(written, dict)
        self.assertEqual(written["event"], "verification")
        self.assertEqual(written["command"], "pytest")
        self.assertIn("ts", written)

    def test_ts_auto_is_iso8601_utc(self):
        written = journal.append_event(self.root, TASK_ID, {"event": "task_created"})
        ts = written["ts"]
        self.assertRegex(ts, TS_AUTO_RE)
        # 可被 fromisoformat 解析且确为 UTC
        parsed = datetime.fromisoformat(ts)
        self.assertEqual(parsed.utcoffset().total_seconds(), 0.0)

    def test_ts_explicit_adopted(self):
        explicit = "2026-08-28T00:00:00.000+00:00"
        written = journal.append_event(
            self.root, TASK_ID, {"event": "review", "verdict": "ship"},
            ts=explicit)
        self.assertEqual(written["ts"], explicit)
        events = journal.read_events(self.root, TASK_ID)
        self.assertEqual(events[0]["ts"], explicit)

    def test_event_with_ts_key_rejected(self):
        with self.assertRaises(journal.JournalError):
            journal.append_event(
                self.root, TASK_ID,
                {"event": "task_created", "ts": "2026-01-01T00:00:00+00:00"})
        self.assertFalse(journal.journal_path(self.root, TASK_ID).exists())

    def test_event_non_dict_rejected(self):
        for bad in ("verification", 42, None, ["event"], ("event",)):
            with self.assertRaises(journal.JournalError, msg=repr(bad)):
                journal.append_event(self.root, TASK_ID, bad)
        self.assertFalse(journal.journal_path(self.root, TASK_ID).exists())

    def test_event_missing_name_rejected(self):
        for bad in ({}, {"mode": "full"}, {"event": 123}, {"event": None}):
            with self.assertRaises(journal.JournalError, msg=repr(bad)):
                journal.append_event(self.root, TASK_ID, bad)
        self.assertFalse(journal.journal_path(self.root, TASK_ID).exists())

    def test_event_empty_name_rejected(self):
        with self.assertRaises(journal.JournalError):
            journal.append_event(self.root, TASK_ID, {"event": ""})
        self.assertFalse(journal.journal_path(self.root, TASK_ID).exists())

    def test_append_creates_missing_dirs(self):
        path = journal.journal_path(self.root, "deep/nested-task-9f8e7d")
        self.assertFalse(path.exists())
        journal.append_event(self.root, "deep/nested-task-9f8e7d",
                             {"event": "task_created"})
        self.assertTrue(path.is_file())

    def test_two_appends_keep_existing_content(self):
        journal.append_event(self.root, TASK_ID, {"event": "task_created"})
        journal.append_event(self.root, TASK_ID, {"event": "route_selected",
                                                  "mode": "delegate"})
        events = journal.read_events(self.root, TASK_ID)
        self.assertEqual(len(events), 2)
        self.assertEqual([e["event"] for e in events],
                         ["task_created", "route_selected"])

    def test_line_format_utf8_and_key_order(self):
        journal.append_event(
            self.root, TASK_ID,
            {"event": "verification", "command": "python3 -m unittest",
             "note": "中文备注"})
        lines = self.raw_lines()
        self.assertEqual(len(lines), 1)
        line = lines[0]
        # UTF-8 原样写入（ensure_ascii=False），中文不转义
        self.assertIn("中文备注", line)
        # 单行 JSON：ts 由模块管理置首，调用方键保持插入序
        self.assertTrue(line.startswith('{"ts":'))
        record = json.loads(line)
        self.assertEqual(
            list(record.keys()),
            ["ts", "event", "command", "note"])

    def test_input_dict_not_mutated(self):
        event = {"event": "review"}
        journal.append_event(self.root, TASK_ID, event)
        self.assertEqual(event, {"event": "review"})


class TestReadEvents(JournalTestCase):

    def test_read_missing_file_returns_empty(self):
        self.assertEqual(journal.read_events(self.root, TASK_ID), [])

    def test_roundtrip_multiple_order_content(self):
        first = {"event": "task_created", "task_id": TASK_ID}
        second = {"event": "route_selected", "mode": "delegate",
                  "assurance": "high"}
        third = {"event": "verification", "command": "python3 -m unittest",
                 "status": "pass"}
        journal.append_event(self.root, TASK_ID, first)
        journal.append_event(self.root, TASK_ID, second)
        journal.append_event(self.root, TASK_ID, third)

        events = journal.read_events(self.root, TASK_ID)
        self.assertEqual(len(events), 3)
        # 顺序与文件一致；除 ts 外内容逐字段一致
        for written, expected in zip(events, (first, second, third)):
            for key, value in expected.items():
                self.assertEqual(written[key], value)
        for item in events:
            self.assertRegex(item["ts"], TS_AUTO_RE)

    def test_bad_line_tolerated_by_default(self):
        self.write_raw(
            '{"ts":"2026-08-28T00:00:00.000+00:00","event":"task_created"}\n'
            '{broken-json\n')
        events = journal.read_events(self.root, TASK_ID)
        self.assertEqual([e["event"] for e in events], ["task_created"])

    def test_truncated_tail_line_tolerated(self):
        # 写入中断常见的截断尾行：最后一行没有换行且 JSON 不完整
        self.write_raw(
            '{"ts":"2026-08-28T00:00:00.000+00:00","event":"completed"}\n'
            '{"ts":"2026-08-28T00:00:01.000+0')
        events = journal.read_events(self.root, TASK_ID)
        self.assertEqual([e["event"] for e in events], ["completed"])

    def test_bad_line_non_dict_tolerated_by_default(self):
        # 合法 JSON 但非 dict 的行同样按坏行处理
        self.write_raw(
            '{"ts":"2026-08-28T00:00:00.000+00:00","event":"completed"}\n'
            '[1, 2, 3]\n'
            '"just-a-string"\n')
        events = journal.read_events(self.root, TASK_ID)
        self.assertEqual([e["event"] for e in events], ["completed"])

    def test_strict_raises_with_line_number(self):
        # 坏行在第 2 行
        self.write_raw(
            '{"ts":"2026-08-28T00:00:00.000+00:00","event":"a"}\n'
            '{broken-json\n'
            '{"ts":"2026-08-28T00:00:02.000+00:00","event":"c"}\n')
        with self.assertRaises(journal.JournalError) as cm:
            journal.read_events(self.root, TASK_ID, strict=True)
        match = re.search(r"第 (\d+) 行", str(cm.exception))
        self.assertIsNotNone(match, str(cm.exception))
        self.assertEqual(int(match.group(1)), 2)

        # 坏行在第 1 行（1 起始行号）
        self.write_raw("not-json-at-all\n", task_id="task-strict-1")
        with self.assertRaises(journal.JournalError) as cm:
            journal.read_events(self.root, "task-strict-1", strict=True)
        match = re.search(r"第 (\d+) 行", str(cm.exception))
        self.assertIsNotNone(match, str(cm.exception))
        self.assertEqual(int(match.group(1)), 1)

    def test_blank_lines_skipped(self):
        self.write_raw(
            "\n"
            '{"ts":"2026-08-28T00:00:00.000+00:00","event":"task_created"}\n'
            "   \n"
            '{"ts":"2026-08-28T00:00:01.000+00:00","event":"completed"}\n'
            "\n")
        events = journal.read_events(self.root, TASK_ID)
        self.assertEqual([e["event"] for e in events],
                         ["task_created", "completed"])

    def test_roundtrip_values_with_line_separator_chars(self):
        # U+2028 / U+0085 经 ensure_ascii=False 原样写入（合法 JSON 单行
        # 内容），按行读取不得在这些字符处断行导致事件被拆丢
        first = {"event": "verification",
                 "note": "含 U+2028 行分隔符：\u2028之后仍是同一行"}
        second = {"event": "review",
                  "note": "含 U+0085 NEL：\u0085之后仍是同一行"}
        journal.append_event(self.root, TASK_ID, first)
        journal.append_event(self.root, TASK_ID, second)
        events = journal.read_events(self.root, TASK_ID)
        self.assertEqual([e["event"] for e in events],
                         ["verification", "review"])
        self.assertEqual(events[0]["note"], first["note"])
        self.assertEqual(events[1]["note"], second["note"])

    def test_torn_trailing_utf8_bytes_tolerated(self):
        # 尾部撕裂的多字节 UTF-8 字符（写入中断部分落盘）：解码按替换符
        # 容错，不得抛 UnicodeDecodeError 让整个日志不可读；撕裂行按坏行
        # 逻辑处理（默认跳过 / strict 报错）
        journal.append_event(self.root, TASK_ID,
                             {"event": "completed", "note": "中文完成"})
        with journal.journal_path(self.root, TASK_ID).open("ab") as fh:
            # 二进制追加："中文" 的 UTF-8 编码去掉最后一字节，制造撕裂尾行
            fh.write("中文".encode("utf-8")[:-1])
        events = journal.read_events(self.root, TASK_ID)
        self.assertEqual([e["event"] for e in events], ["completed"])
        self.assertEqual(events[0]["note"], "中文完成")
        # strict=True：撕裂字符所在行解析失败，按坏行报 JournalError
        with self.assertRaises(journal.JournalError):
            journal.read_events(self.root, TASK_ID, strict=True)


class TestTailEvents(JournalTestCase):

    def setUp(self):
        super().setUp()
        # 5 条事件，其中 3 条 verification 交错分布
        self.verification_ts = []
        for i, event in enumerate(["verification", "review", "verification",
                                   "review", "verification"]):
            ts = "2026-08-28T00:00:0%d.000+00:00" % i
            if event == "verification":
                self.verification_ts.append(ts)
            journal.append_event(
                self.root, TASK_ID, {"event": event, "seq": i}, ts=ts)

    def test_tail_n_subsets_in_file_order(self):
        tail = journal.tail_events(self.root, TASK_ID, 3)
        self.assertEqual([e["seq"] for e in tail], [2, 3, 4])
        # 超过总数时返回全部，不反转
        tail = journal.tail_events(self.root, TASK_ID, 99)
        self.assertEqual([e["seq"] for e in tail], [0, 1, 2, 3, 4])

    def test_tail_event_filter(self):
        tail = journal.tail_events(self.root, TASK_ID, event="verification")
        self.assertEqual(len(tail), 3)
        self.assertEqual([e["event"] for e in tail],
                         ["verification", "verification", "verification"])
        # 过滤后再取尾部：最近 2 条 verification
        tail = journal.tail_events(self.root, TASK_ID, 2, event="verification")
        self.assertEqual([e["ts"] for e in tail],
                         self.verification_ts[1:])

    def test_tail_default_n_is_20(self):
        for i in range(25):
            journal.append_event(
                self.root, "task-tail-20", {"event": "review", "seq": i})
        tail = journal.tail_events(self.root, "task-tail-20")
        self.assertEqual(len(tail), 20)
        self.assertEqual([e["seq"] for e in tail], list(range(5, 25)))

    def test_tail_n_zero_and_negative(self):
        self.assertEqual(journal.tail_events(self.root, TASK_ID, 0), [])
        self.assertEqual(journal.tail_events(self.root, TASK_ID, -1), [])

    def test_tail_missing_file(self):
        self.assertEqual(journal.tail_events(self.root, "task-absent-000"), [])


if __name__ == "__main__":
    unittest.main()
