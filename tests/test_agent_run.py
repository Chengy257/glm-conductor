#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""runtime.agent_run 单元测试（v2.1 M2 wu-21-03 + M3 前半账本面）。

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

M3 前半读取面（list_agent_runs / native_agent_metadata /
run_lifecycle）与 CLI agent-runs：
  - journal 聚合的事件归类（launched→run、failed→run、
    replay_skipped→无 run）与缺字段容错、run_id 形状；
  - 原生档案只读 adapter：tempfile 伪造 agents_root（绝不读写真实
    ~/.zcode）——白名单归一 / 未知字段丢弃 / 缺档案 / 坏 JSON /
    非 dict JSON / 多会话目录定位 / 路径逃逸防护 / 缺省根 home 注入；
  - run_lifecycle 计数、unit 过滤、last_agent_id 与 §7.3 僵尸语义
    （status=="running" → possibly_zombie=True、archived_terminal=
    False；档案缺失 → 二者皆 False）；
  - CLI agent-runs 两形态（数组 / lifecycle dict）与用法错误退出码。

RB-21-02 reviewer invocation 账本（ReviewerInvocationTest）：
  - reviewer_invoked 事件字段冻结（tool_use_id/reviewer/task_id/
    agent_id/execution_mode）与载荷形状容错、执行模式推导；
  - reviewer_invocation_skipped 警告事件（reason 摘要口径）；
  - review marker（GLM_CONDUCTOR_REVIEW=<task_id>）构造/解析与
    REVIEWER_PROFILES 白名单词汇冻结。

仅 Python 3 标准库；临时目录 fixture，不污染真实工作区。
"""

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "glm-conductor"))
from runtime import agent_run, cli, journal

TID = "agent-run-test-1a2b3c"
PERMIT = {"permit_id": "dp-abc123def456", "task_id": TID, "unit_id": "wu-1",
          "wave_id": None, "mode": "background", "reason": None,
          "created_at": "2026-08-30T00:00:00.000+00:00",
          "expires_at": "2026-08-30T00:30:00.000+00:00", "consumed": False}


def _write_metadata(agents_root, session_id, agent_id, **overrides):
    """在伪造档案树落一份 metadata.json（camelCase 真实形状），返回路径。

    默认字段按 dogfood 实测形状（agentId/childSessionId/parentSessionId/
    parentToolUseId/status/usage + 一个表外字段验证丢弃）；overrides
    覆盖任意键。绝不写真实 ~/.zcode——agents_root 恒为测试临时目录。
    """
    metadata = {
        "agentId": agent_id,
        "childSessionId": "sess_subagent_" + agent_id,
        "parentSessionId": session_id,
        "parentToolUseId": "call_parent_0001",
        "status": "completed",
        "usage": {"inputTokens": 100, "outputTokens": 20},
        "transcriptFile": "SHOULD-NOT-LEAK",  # 表外字段：必须被丢弃
    }
    metadata.update(overrides)
    path = Path(agents_root) / session_id / agent_id / "metadata.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(path), "w", encoding="utf-8", newline="\n") as fh:
        json.dump(metadata, fh, ensure_ascii=False)
    return path


def _run_cli(*args):
    """调用 cli.main 并捕获 stdout，返回 (退出码, 解析后 JSON)。"""
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        code = cli.main(list(args))
    return code, json.loads(buffer.getvalue())


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


class ReviewerInvocationTest(unittest.TestCase):
    """RB-21-02：reviewer_invoked / reviewer_invocation_skipped 记账 +
    review marker 构造/解析 + REVIEWER_PROFILES 白名单词汇。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def _last(self):
        return journal.read_events(self.repo, TID)[-1]

    def test_reviewer_profiles_vocabulary(self):
        # 白名单词汇冻结：bare 与 "glm-conductor:" 前缀双形态
        self.assertEqual(
            agent_run.REVIEWER_PROFILES,
            frozenset(("glm-reviewer", "visual-reviewer",
                       "glm-conductor:glm-reviewer",
                       "glm-conductor:visual-reviewer")))

    def test_review_marker_round_trip(self):
        marker = agent_run.review_marker_for("t-abc")
        self.assertEqual(marker, "GLM_CONDUCTOR_REVIEW=t-abc")
        # 多行文本按「prefix 后首个空白 token」定位（与 dispatch marker
        # 的 parse 同构）
        text = "请审查该 diff\n%s\n谢谢" % marker
        self.assertEqual(agent_run.parse_review_marker(text), "t-abc")
        # 行内尾随文本不吞并
        self.assertEqual(
            agent_run.parse_review_marker(
                "GLM_CONDUCTOR_REVIEW=t-1 extra"),
            "t-1")

    def test_parse_review_marker_negative(self):
        self.assertIsNone(agent_run.parse_review_marker("no marker here"))
        self.assertIsNone(agent_run.parse_review_marker(None))
        self.assertIsNone(agent_run.parse_review_marker(
            "GLM_CONDUCTOR_REVIEW="))  # 空 token → None
        self.assertIsNone(agent_run.parse_review_marker(
            "GLM_CONDUCTOR_REVIEW=   "))  # 纯空白 token → None
        # 不与 dispatch marker 混淆（独立前缀）
        self.assertIsNone(agent_run.parse_review_marker(
            "GLM_CONDUCTOR_DISPATCH=dp-1"))

    def test_review_marker_for_rejects_bad_task_id(self):
        for bad in ("", None, 123):
            with self.assertRaises(ValueError):
                agent_run.review_marker_for(bad)

    def test_invocation_fields_frozen(self):
        payload = {
            "tool_use_id": "call_r1",
            "tool_input": {"subagent_type": "glm-conductor:visual-reviewer",
                           "prompt": "review GLM_CONDUCTOR_REVIEW=other"},
            "tool_response": {"agentId": "agent_r1"},
        }
        event = agent_run.record_reviewer_invocation(self.repo, TID, payload)
        # 字段冻结：{"event","tool_use_id","reviewer","task_id","agent_id",
        # "execution_mode"}（+ journal 统一管理的 ts）
        self.assertEqual(
            set(event.keys()),
            {"ts", "event", "tool_use_id", "reviewer", "task_id",
             "agent_id", "execution_mode"})
        self.assertEqual(event["event"], "reviewer_invoked")
        self.assertEqual(event["tool_use_id"], "call_r1")
        self.assertEqual(event["reviewer"], "glm-conductor:visual-reviewer")
        self.assertEqual(event["task_id"], TID)
        self.assertEqual(event["agent_id"], "agent_r1")
        self.assertEqual(event["execution_mode"], "foreground")

    def test_invocation_execution_mode_and_shape_tolerated(self):
        # run_in_background=True → "background"；载荷形状异常容错为
        # 缺省（reviewer None / agent_id None / foreground）
        event = agent_run.record_reviewer_invocation(
            self.repo, TID,
            {"tool_input": {"subagent_type": "glm-reviewer",
                            "run_in_background": True}})
        self.assertEqual(event["execution_mode"], "background")
        event = agent_run.record_reviewer_invocation(self.repo, TID, None)
        self.assertIsNone(event["reviewer"])
        self.assertIsNone(event["agent_id"])
        self.assertIsNone(event["tool_use_id"])
        self.assertEqual(event["execution_mode"], "foreground")
        self.assertEqual(event["task_id"], TID)

    def test_invocation_agent_id_snake_and_nested_result(self):
        event = agent_run.record_reviewer_invocation(
            self.repo, TID,
            {"tool_response": {"agent_id": "snake_1"}})
        self.assertEqual(event["agent_id"], "snake_1")
        event = agent_run.record_reviewer_invocation(
            self.repo, TID,
            {"tool_response": {"result": {"agentId": "nested_1"}}})
        self.assertEqual(event["agent_id"], "nested_1")

    def test_invocation_skipped_fields(self):
        event = agent_run.record_reviewer_invocation_skipped(
            self.repo, TID,
            {"tool_use_id": "call_r9",
             "tool_input": {"subagent_type": "glm-reviewer"}},
            reason="task_missing")
        self.assertEqual(event["event"], "reviewer_invocation_skipped")
        self.assertEqual(event["reason"], "task_missing")
        self.assertEqual(event["task_id"], TID)
        self.assertEqual(event["tool_use_id"], "call_r9")
        self.assertEqual(event["reviewer"], "glm-reviewer")
        # reason 非 str 压字符串并截断 200（同 error 摘要口径）
        event = agent_run.record_reviewer_invocation_skipped(
            self.repo, TID, {}, reason=None)
        self.assertEqual(event["reason"], "unknown")
        event = agent_run.record_reviewer_invocation_skipped(
            self.repo, TID, {}, reason="x" * 500)
        self.assertEqual(len(event["reason"]), 200)


class ListAgentRunsTest(unittest.TestCase):
    """list_agent_runs：journal 聚合的事件归类与缺字段容错。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_empty_journal_returns_empty_list(self):
        self.assertEqual(agent_run.list_agent_runs(self.repo, TID), [])

    def test_launch_event_produces_run_not_failed(self):
        agent_run.record_agent_launch(
            self.repo, TID, {"tool_use_id": "call_1",
                             "tool_response": {"agentId": "agent_9"}},
            permit=PERMIT)
        events = journal.read_events(self.repo, TID)
        runs = agent_run.list_agent_runs(self.repo, TID)
        self.assertEqual(len(runs), 1)
        run = runs[0]
        # 冻结八键形状（§7.2 handle）
        self.assertEqual(sorted(run),
                         ["agent_id", "execution_mode", "failed",
                          "permit_id", "run_id", "tool_use_id", "ts",
                          "unit"])
        self.assertEqual(run["run_id"], "call_1")
        self.assertEqual(run["unit"], "wu-1")
        self.assertEqual(run["permit_id"], "dp-abc123def456")
        self.assertEqual(run["tool_use_id"], "call_1")
        self.assertEqual(run["agent_id"], "agent_9")
        self.assertEqual(run["execution_mode"], "background")
        self.assertIs(run["failed"], False)
        self.assertEqual(run["ts"], events[-1]["ts"])

    def test_failure_event_produces_run_failed(self):
        agent_run.record_agent_failure(self.repo, TID,
                                       {"tool_use_id": "call_x"},
                                       permit=PERMIT, error="boom")
        runs = agent_run.list_agent_runs(self.repo, TID)
        self.assertEqual(len(runs), 1)
        run = runs[0]
        self.assertIs(run["failed"], True)
        self.assertEqual(run["unit"], "wu-1")
        self.assertEqual(run["permit_id"], "dp-abc123def456")
        self.assertEqual(run["tool_use_id"], "call_x")
        # dispatch_failed 事件本无这两个字段 → None（缺字段容错）
        self.assertIsNone(run["agent_id"])
        self.assertIsNone(run["execution_mode"])

    def test_replay_skipped_and_foreign_events_produce_no_run(self):
        agent_run.record_launch_replay_skipped(
            self.repo, TID, {"tool_use_id": "call_r"},
            permit_id="dp-abc123def456")
        journal.append_event(self.repo, TID,
                             {"event": "implementation_started",
                              "unit": "wu-1"})
        journal.append_event(self.repo, TID, {"event": "totally_unknown"})
        self.assertEqual(agent_run.list_agent_runs(self.repo, TID), [])

    def test_mixed_sequence_preserves_file_order(self):
        agent_run.record_agent_launch(
            self.repo, TID, {"tool_use_id": "c1",
                             "tool_response": {"agentId": "a1"}},
            permit=PERMIT)
        agent_run.record_agent_failure(self.repo, TID,
                                       {"tool_use_id": "c2"}, permit=PERMIT)
        agent_run.record_launch_replay_skipped(
            self.repo, TID, {"tool_use_id": "c3"}, permit_id="dp-x")
        agent_run.record_agent_launch(
            self.repo, TID, {"tool_use_id": "c4"}, permit=None)
        runs = agent_run.list_agent_runs(self.repo, TID)
        self.assertEqual(len(runs), 3)  # replay_skipped 不产生 run
        self.assertEqual([r["tool_use_id"] for r in runs],
                         ["c1", "c2", "c4"])
        self.assertEqual([r["failed"] for r in runs],
                         [False, True, False])
        self.assertEqual([r["unit"] for r in runs],
                         ["wu-1", "wu-1", None])

    def test_run_id_tool_use_id_or_sequence_number(self):
        # 有 tool_use_id → run_id 即 tool_use_id；缺失 → 1 起始递增序号
        agent_run.record_agent_launch(
            self.repo, TID, {"tool_use_id": "call_a"}, permit=PERMIT)
        agent_run.record_agent_failure(self.repo, TID, {}, permit=PERMIT)
        agent_run.record_agent_launch(self.repo, TID, {}, permit=None)
        runs = agent_run.list_agent_runs(self.repo, TID)
        self.assertEqual([r["run_id"] for r in runs],
                         ["call_a", 2, 3])


class NativeAgentMetadataTest(unittest.TestCase):
    """native_agent_metadata：伪造档案树上的只读 adapter（绝不读写
    真实 ~/.zcode——agents_root 恒为 tempfile 注入或 home mock）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.agents_root = self._tmp.name
        self.session_id = "sess_parent_1111aaaa"

    def tearDown(self):
        self._tmp.cleanup()

    def test_normal_archive_whitelist_normalized(self):
        _write_metadata(self.agents_root, self.session_id, "agent_a1")
        result = agent_run.native_agent_metadata(
            "agent_a1", agents_root=self.agents_root)
        self.assertEqual(sorted(result),
                         ["agent_id", "child_session_id",
                          "observed_status", "parent_session_id",
                          "parent_tool_use_id", "tokens"])
        self.assertEqual(result["agent_id"], "agent_a1")
        self.assertEqual(result["child_session_id"],
                         "sess_subagent_agent_a1")
        self.assertEqual(result["parent_session_id"], self.session_id)
        self.assertEqual(result["parent_tool_use_id"], "call_parent_0001")
        # observed_status 是档案 status 原值（不解读、不改名值）
        self.assertEqual(result["observed_status"], "completed")
        # tokens：usage 用量块原样透传
        self.assertEqual(result["tokens"],
                         {"inputTokens": 100, "outputTokens": 20})
        # 表外字段（transcriptFile 等）绝不透出（§7.2 不复制 transcript）
        self.assertNotIn("transcript_file", result)
        self.assertNotIn("transcriptFile", result)

    def test_missing_archive_returns_none(self):
        self.assertIsNone(agent_run.native_agent_metadata(
            "agent_ghost", agents_root=self.agents_root))

    def test_bad_agent_id_shapes_return_none(self):
        for bad in (None, "", 42, ".", "..", "../escape", r"a\b",
                    "a/b", "a\x00b"):
            self.assertIsNone(agent_run.native_agent_metadata(
                bad, agents_root=self.agents_root),
                "agent_id %r 应被拒绝" % (bad,))

    def test_corrupt_and_non_dict_json_return_none(self):
        agent_dir = Path(self.agents_root) / self.session_id / "agent_bad"
        agent_dir.mkdir(parents=True)
        (agent_dir / "metadata.json").write_text("{not-json", encoding="utf-8")
        self.assertIsNone(agent_run.native_agent_metadata(
            "agent_bad", agents_root=self.agents_root))
        for name, junk in (("agent_junk_list", "[1, 2, 3]"),
                           ("agent_junk_str", '"just-a-string"'),
                           ("agent_junk_null", "null")):
            agent_dir = (Path(self.agents_root) / self.session_id / name)
            agent_dir.mkdir(parents=True)
            (agent_dir / "metadata.json").write_text(junk, encoding="utf-8")
            self.assertIsNone(agent_run.native_agent_metadata(
                name, agents_root=self.agents_root))

    def test_multi_session_dirs_first_sorted_hit(self):
        # 档案在字典序更晚的会话目录下也能定位（不依赖主会话 id）
        _write_metadata(self.agents_root, "sess_aaa_1", "agent_m1")
        _write_metadata(self.agents_root, "sess_zzz_9", "agent_m2")
        hit = agent_run.native_agent_metadata(
            "agent_m2", agents_root=self.agents_root)
        self.assertIsNotNone(hit)
        self.assertEqual(hit["parent_session_id"], "sess_zzz_9")

    def test_missing_status_and_non_dict_usage_degrade(self):
        _write_metadata(self.agents_root, self.session_id, "agent_n1",
                        status=None, usage="not-a-dict")
        result = agent_run.native_agent_metadata(
            "agent_n1", agents_root=self.agents_root)
        self.assertIsNone(result["observed_status"])
        self.assertIsNone(result["tokens"])

    def test_default_root_derived_from_injected_home(self):
        # 缺省 agents_root = Path.home()/.zcode/cli/agents——把 home
        # mock 到临时目录，验证缺省推导且不触碰真实 ~/.zcode
        fake_home = Path(self.agents_root) / "home"
        planted = fake_home / ".zcode" / "cli" / "agents"
        _write_metadata(str(planted), self.session_id, "agent_h1")
        with mock.patch.object(Path, "home", return_value=fake_home):
            hit = agent_run.native_agent_metadata("agent_h1")
            miss = agent_run.native_agent_metadata("agent_ghost")
        self.assertEqual(hit["agent_id"], "agent_h1")
        self.assertIsNone(miss)


class RunLifecycleTest(unittest.TestCase):
    """run_lifecycle：单元视角计数、unit 过滤与 §7.3 僵尸语义标注。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = self._tmp.name
        self.agents_root = self._tmp.name  # 同一临时目录：伪造 home 树
        self.session_id = "sess_parent_2222bbbb"
        # 类级缺省：home 指向空临时目录——本类所有 run_lifecycle 调用
        # 的缺省档案根都不落在真实 ~/.zcode；需要档案的用例经
        # _with_home_archive 在其上再叠一层 patch（嵌套恢复安全）
        self._home_patch = mock.patch.object(
            Path, "home",
            return_value=Path(self.agents_root) / "empty_home")
        self._home_patch.start()
        self.addCleanup(self._home_patch.stop)

    def tearDown(self):
        self._tmp.cleanup()

    def _launch(self, tool_use_id, agent_id, unit="wu-1"):
        permit = dict(PERMIT)
        permit["unit_id"] = unit
        response = {"agentId": agent_id} if agent_id else {"no": "handle"}
        agent_run.record_agent_launch(
            self.repo, TID,
            {"tool_use_id": tool_use_id, "tool_response": response},
            permit=permit)

    def _with_home_archive(self, agent_id, status):
        """在 mock home 派生树下种一份档案，返回已激活的 patch 上下文。

        run_lifecycle → native_agent_metadata 走缺省根
        Path.home()/.zcode/cli/agents——把 home mock 到临时目录派生树，
        覆盖真实档案（本测试绝不读写真实 ~/.zcode）。
        """
        fake_home = Path(self.agents_root) / "home"
        planted = fake_home / ".zcode" / "cli" / "agents"
        _write_metadata(str(planted), self.session_id, agent_id,
                        status=status)
        return mock.patch.object(Path, "home", return_value=fake_home)

    def test_counts_unit_filter_and_frozen_shape(self):
        self._launch("c1", "agent_a1", unit="wu-1")
        agent_run.record_agent_failure(
            self.repo, TID, {"tool_use_id": "c2"},
            permit=dict(PERMIT, unit_id="wu-1"))
        self._launch("c3", "agent_a3", unit="wu-1")
        self._launch("c4", "agent_b1", unit="wu-2")  # 其他单元，不计入
        result = agent_run.run_lifecycle(self.repo, TID, "wu-1")
        self.assertEqual(sorted(result),
                         ["archived_terminal", "failure_count",
                          "last_agent_id", "launch_count", "native",
                          "possibly_zombie", "runs", "unit"])
        self.assertEqual(result["unit"], "wu-1")
        self.assertEqual(result["launch_count"], 2)
        self.assertEqual(result["failure_count"], 1)
        self.assertEqual([r["tool_use_id"] for r in result["runs"]],
                         ["c1", "c2", "c3"])
        # 倒序扫描：最后已知 agent_id 是 agent_a3
        self.assertEqual(result["last_agent_id"], "agent_a3")
        # 真实机器无该档案（缺省根下不存在）→ native None、二标注皆 False
        self.assertIsNone(result["native"])
        self.assertIs(result["possibly_zombie"], False)
        self.assertIs(result["archived_terminal"], False)

    def test_last_agent_id_skips_unextractable_launch(self):
        self._launch("c1", "agent_known")
        self._launch("c2", None)  # 末次派发 agent_id 提取失败
        result = agent_run.run_lifecycle(self.repo, TID, "wu-1")
        self.assertEqual(result["last_agent_id"], "agent_known")

    def test_unknown_unit_all_zero(self):
        result = agent_run.run_lifecycle(self.repo, TID, "wu-ghost")
        self.assertEqual(result["runs"], [])
        self.assertEqual(result["launch_count"], 0)
        self.assertEqual(result["failure_count"], 0)
        self.assertIsNone(result["last_agent_id"])
        self.assertIsNone(result["native"])
        self.assertIs(result["possibly_zombie"], False)
        self.assertIs(result["archived_terminal"], False)

    def test_zombie_running_marked_not_alive(self):
        # §7.3 冻结：档案 status=="running" 绝不解读为存活
        self._launch("c1", "agent_z1")
        with self._with_home_archive("agent_z1", "running"):
            result = agent_run.run_lifecycle(self.repo, TID, "wu-1")
        self.assertEqual(result["last_agent_id"], "agent_z1")
        self.assertIsNotNone(result["native"])
        self.assertEqual(result["native"]["observed_status"], "running")
        self.assertIs(result["possibly_zombie"], True)
        self.assertIs(result["archived_terminal"], False)

    def test_archived_terminal_completed(self):
        self._launch("c1", "agent_t1")
        with self._with_home_archive("agent_t1", "completed"):
            result = agent_run.run_lifecycle(self.repo, TID, "wu-1")
        self.assertIs(result["archived_terminal"], True)
        self.assertIs(result["possibly_zombie"], False)

    def test_failed_and_stopped_also_terminal(self):
        for status in ("failed", "stopped"):
            agent_id = "agent_term_" + status
            self._launch("c_" + status, agent_id)
            with self._with_home_archive(agent_id, status):
                result = agent_run.run_lifecycle(self.repo, TID, "wu-1")
            self.assertIs(result["archived_terminal"], True, status)
            self.assertIs(result["possibly_zombie"], False, status)


class AgentRunsCliTest(unittest.TestCase):
    """CLI agent-runs：两形态输出与用法错误退出码。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = self._tmp.name
        # home 指向空临时目录：unit 形态经 run_lifecycle 触达缺省档案
        # 根时绝不读写真实 ~/.zcode
        self._home_patch = mock.patch.object(
            Path, "home", return_value=Path(self._tmp.name) / "empty_home")
        self._home_patch.start()
        self.addCleanup(self._home_patch.stop)

    def tearDown(self):
        self._tmp.cleanup()

    def test_list_form_outputs_run_array(self):
        agent_run.record_agent_launch(
            self.repo, TID, {"tool_use_id": "c1",
                             "tool_response": {"agentId": "a1"}},
            permit=PERMIT)
        agent_run.record_launch_replay_skipped(
            self.repo, TID, {"tool_use_id": "c2"}, permit_id="dp-x")
        code, payload = _run_cli("agent-runs", self.repo, TID)
        self.assertEqual(code, 0)
        self.assertIsInstance(payload, list)
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["run_id"], "c1")
        self.assertIs(payload[0]["failed"], False)

    def test_list_form_empty_journal_outputs_empty_array(self):
        code, payload = _run_cli("agent-runs", self.repo, "no-such-task")
        self.assertEqual(code, 0)
        self.assertEqual(payload, [])

    def test_unit_form_outputs_lifecycle_dict(self):
        agent_run.record_agent_launch(
            self.repo, TID, {"tool_use_id": "c1",
                             "tool_response": {"agentId": "a1"}},
            permit=PERMIT)
        code, payload = _run_cli("agent-runs", self.repo, TID, "wu-1")
        self.assertEqual(code, 0)
        self.assertIsInstance(payload, dict)
        self.assertEqual(payload["unit"], "wu-1")
        self.assertEqual(payload["launch_count"], 1)
        self.assertEqual(payload["last_agent_id"], "a1")
        self.assertIs(payload["possibly_zombie"], False)
        self.assertIs(payload["archived_terminal"], False)

    def test_usage_error_wrong_arg_count_exit_2(self):
        for args in (("agent-runs", self.repo),
                     ("agent-runs", self.repo, TID, "wu-1", "extra")):
            code, payload = _run_cli(*args)
            self.assertEqual(code, 2, args)
            self.assertIn("error", payload)


if __name__ == "__main__":
    unittest.main()
