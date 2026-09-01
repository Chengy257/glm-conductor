#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hooks.post_tool_use 子进程测试（v2.1 M2 wu-21-03）。

以子进程方式运行 plugins/glm-conductor/hooks/post_tool_use.py，验证
PostToolUse / PostToolUseFailure 两条路径的可观测行为：

PostToolUse（成功）：
  - 有效 permit（fixture 任务经真实 prepare_dispatch 签发）+ marker
    携带于 prompt → permit 原子消费（<id>.consumed.json 存在、原文件
    不在）+ journal agent_launched 绑 tool_use_id/permit_id/unit；
  - marker 在 description 同样定位；
  - 已消费 permit（重放）→ agent_launch_replay_skipped，不报错；
  - 无 marker / 无归属活动任务 → 静默（exit 0、stdout 空、零事件）。

PostToolUseFailure：
  - 有效 permit → .invalidated.json + agent_dispatch_failed（error 自
    载荷提取）；
  - permit 已不在 → 仅记失败事件。

契约：
  - hook_event_name 缺失/非本钩子事件 → 静默；
  - stdin 空/非 JSON → 容错静默 exit 0（fail-open 前提）；
  - stdout 恒空（记账钩子零输出纪律）。

fixture：tempfile 仓库 + runtime.state 构造带 work unit 的任务 +
task_manager.prepare_dispatch 真实签发 permit（ownership/verification
非空满足 §61）。仅 Python 3 标准库。
"""

import sys, unittest
import json, os, subprocess, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "glm-conductor"))
from runtime import agent_run, dispatch_wave, journal, state, task_manager

POST_TOOL_USE = (Path(__file__).resolve().parents[1]
                 / "plugins/glm-conductor/hooks/post_tool_use.py")

TID = "post-tool-test-1a2b3c"
ROUTE = {"mode": "delegate", "delegability": "high",
         "assurance": "standard", "executor": "flash-implementer",
         "continuity": "foreground"}
UNIT = {"id": "wu-1", "objective": "post tool use fixture unit",
        "status": "pending", "depends_on": [],
        "executor": "flash-implementer",
        "ownership": ["src/a.py"],
        "verification": ["python3 -m unittest -h"]}


def run_hook(stdin_text, project_dir):
    """以子进程运行 post_tool_use.py，返回 CompletedProcess（显式 UTF-8）。"""
    return subprocess.run(
        [sys.executable, str(POST_TOOL_USE)],
        input=stdin_text, text=True, capture_output=True,
        encoding="utf-8", errors="replace",
        env=dict(os.environ, ZCODE_PROJECT_DIR=str(project_dir)))


def save_task_with_permit(repo, mode=None):
    """构造带 work unit 的活动任务并 prepare_dispatch 签发 permit。

    返回 (permit_id, permit)。mode 给定时直接用 dispatch_wave.
    create_permit 覆盖（foreground 场景需要 reason）。
    """
    st = state.new_task_state(
        TID, "post tool use fixture", dict(ROUTE),
        ownership_files=["src/a.py"],
        verification_required=["python3 -m unittest -h"], status="executing")
    st["work_units"] = [dict(UNIT)]
    state.save_state(repo, st)
    task_manager.refresh_readiness(repo, TID)
    result = task_manager.prepare_dispatch(repo, TID, "wu-1",
                                           quota_status="AVAILABLE")
    permit = result["permit"]
    if mode is not None:
        permit = dispatch_wave.create_permit(
            repo, TID, "wu-1", mode=mode,
            reason="synchronous_dependency" if mode == "foreground"
            else None)
    return permit["permit_id"], permit


def stdin_payload(permit_id, event="PostToolUse", where="prompt",
                  tool_use_id="call_1", extra_response=None):
    """构造钩子事件 stdin（marker 在 prompt 或 description）。"""
    marker = dispatch_wave.marker_for(permit_id)
    tool_input = {"subagent_type": "glm-conductor:flash-implementer",
                  "description": "fixture dispatch"}
    if where == "prompt":
        tool_input["prompt"] = "do work %s" % marker
    else:
        tool_input["description"] = "fixture %s" % marker
    response = {"agentId": "agent_fixture"}
    if isinstance(extra_response, dict):
        response.update(extra_response)
    return json.dumps({
        "hook_event_name": event, "tool_name": "Agent",
        "tool_input": tool_input, "tool_use_id": tool_use_id,
        "tool_response": response})


class PostToolUseSuccessTest(unittest.TestCase):
    """成功路径：消费 permit + agent_launched 记账。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = self._tmp.name
        self.permit_id, self.permit = save_task_with_permit(self.repo)

    def tearDown(self):
        self._tmp.cleanup()

    def _events(self):
        return journal.read_events(self.repo, TID)

    def test_consumes_permit_and_records_launch(self):
        result = run_hook(stdin_payload(self.permit_id), self.repo)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        # 原子消费：.consumed.json 存在，原文件不在
        consumed = (Path(self.repo) / ".glm-conductor/tasks" / TID
                    / "permits" / (self.permit_id + ".consumed.json"))
        self.assertTrue(consumed.is_file())
        live = consumed.parent / (self.permit_id + ".json")
        self.assertFalse(live.exists())
        # agent_launched 绑 tool_use_id / permit_id / unit
        launched = [e for e in self._events()
                    if e["event"] == "agent_launched"]
        self.assertEqual(len(launched), 1)
        self.assertEqual(launched[0]["tool_use_id"], "call_1")
        self.assertEqual(launched[0]["permit_id"], self.permit_id)
        self.assertEqual(launched[0]["unit"], "wu-1")
        self.assertEqual(launched[0]["agent_id"], "agent_fixture")

    def test_marker_in_description(self):
        result = run_hook(
            stdin_payload(self.permit_id, where="description"), self.repo)
        self.assertEqual(result.returncode, 0)
        launched = [e for e in self._events()
                    if e["event"] == "agent_launched"]
        self.assertEqual(len(launched), 1)

    def test_replayed_permit_records_skip(self):
        run_hook(stdin_payload(self.permit_id), self.repo)
        result = run_hook(stdin_payload(self.permit_id,
                                        tool_use_id="call_2"), self.repo)
        self.assertEqual(result.returncode, 0, result.stderr)
        events = self._events()
        skipped = [e for e in events
                   if e["event"] == "agent_launch_replay_skipped"]
        self.assertEqual(len(skipped), 1)
        self.assertEqual(skipped[0]["tool_use_id"], "call_2")
        self.assertEqual(len([e for e in events
                              if e["event"] == "agent_launched"]), 1)

    def test_no_marker_silent(self):
        payload = json.dumps({
            "hook_event_name": "PostToolUse", "tool_name": "Agent",
            "tool_input": {"subagent_type": "Explore",
                           "prompt": "read only probe"},
            "tool_use_id": "c", "tool_response": {}})
        result = run_hook(payload, self.repo)
        self.assertEqual((result.returncode, result.stdout), (0, ""))
        self.assertNotIn("agent_launched",
                         [e["event"] for e in self._events()])

    def test_foreign_permit_silent(self):
        # permit 属于别的任务（伪造 id）：无归属活动任务 → 静默零事件
        result = run_hook(stdin_payload("dp-000000000000"), self.repo)
        self.assertEqual((result.returncode, result.stdout), (0, ""))
        self.assertNotIn("agent_launched",
                         [e["event"] for e in self._events()])


class PostToolUseFailurePathTest(unittest.TestCase):
    """失败路径：作废 permit + agent_dispatch_failed。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = self._tmp.name
        self.permit_id, self.permit = save_task_with_permit(self.repo)

    def tearDown(self):
        self._tmp.cleanup()

    def _events(self):
        return journal.read_events(self.repo, TID)

    def test_invalidates_and_records_failure(self):
        payload = stdin_payload(self.permit_id,
                                event="PostToolUseFailure")
        payload = json.loads(payload)
        payload["error"] = "gateway quota exceeded"
        result = run_hook(json.dumps(payload), self.repo)
        self.assertEqual(result.returncode, 0, result.stderr)
        invalidated = (Path(self.repo) / ".glm-conductor/tasks" / TID
                       / "permits" / (self.permit_id + ".invalidated.json"))
        self.assertTrue(invalidated.is_file())
        failed = [e for e in self._events()
                  if e["event"] == "agent_dispatch_failed"]
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0]["permit_id"], self.permit_id)
        self.assertEqual(failed[0]["unit"], "wu-1")
        self.assertEqual(failed[0]["error"], "gateway quota exceeded")

    def test_failure_without_permit_still_records(self):
        dispatch_wave.consume_permit(self.repo, TID, self.permit_id)
        result = run_hook(
            stdin_payload(self.permit_id, event="PostToolUseFailure"),
            self.repo)
        self.assertEqual(result.returncode, 0)
        failed = [e for e in self._events()
                  if e["event"] == "agent_dispatch_failed"]
        self.assertEqual(len(failed), 1)
        self.assertIsNone(failed[0]["permit_id"])


class ContractTest(unittest.TestCase):
    """静默纪律与容错契约。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_unserved_event_silent(self):
        payload = json.dumps({"hook_event_name": "Stop",
                              "tool_name": "Agent",
                              "tool_input": {"prompt":
                                             dispatch_wave.marker_for("dp-x")}})
        result = run_hook(payload, self.repo)
        self.assertEqual((result.returncode, result.stdout, result.stderr),
                         (0, "", ""))

    def test_empty_stdin_tolerated(self):
        result = run_hook("", self.repo)
        self.assertEqual(result.returncode, 0)
        result = run_hook("not json", self.repo)
        self.assertEqual(result.returncode, 0)


# —— RB-21-02：reviewer invocation 记账分支（PostToolUse） ——

# reviewer 分支夹具路由（与文件既有 ROUTE 同款矩阵合法组合——分支
# 行为与 route 无关，不需要 audit/high；save_state 的 H2 校验必须过）
REVIEW_ROUTE = {"mode": "delegate", "delegability": "high",
                "assurance": "standard", "executor": "flash-implementer",
                "continuity": "foreground"}


def save_plain_task(repo, task_id=TID):
    """构造 reviewer 分支用的活动任务（无需 permit；R4 实质性：delegate
    路由须带非空 ownership + verification）。"""
    state.save_state(repo, state.new_task_state(
        task_id, "reviewer branch fixture", dict(REVIEW_ROUTE),
        ownership_files=["src/a.py"],
        verification_required=["python3 -m unittest -h"],
        status="reviewing"))


def review_payload(task_id=TID, subagent_type="glm-conductor:glm-reviewer",
                   with_marker=True, run_in_background=None,
                   tool_use_id="call_rev_1", event="PostToolUse"):
    """构造 reviewer 派发的钩子事件 stdin（marker 携带 GLM_CONDUCTOR_
    REVIEW=<task_id>；run_in_background 可选注入 tool_input）。"""
    tool_input = {"subagent_type": subagent_type}
    if with_marker:
        tool_input["prompt"] = ("review the diff %s"
                                % agent_run.review_marker_for(task_id))
    if run_in_background is not None:
        tool_input["run_in_background"] = run_in_background
    return json.dumps({
        "hook_event_name": event, "tool_name": "Agent",
        "tool_input": tool_input, "tool_use_id": tool_use_id,
        "tool_response": {"agentId": "agent_review_1"}})


class ReviewerInvocationBranchTest(unittest.TestCase):
    """RB-21-02：reviewer 类型派发 + GLM_CONDUCTOR_REVIEW marker →
    reviewer_invoked 账本（事件字段冻结）；正反用例 + permit 分支不回归。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def _events(self):
        return journal.read_events(self.repo, TID)

    def test_reviewer_with_marker_records_invocation(self):
        # 正用例：reviewer 类型（命名空间变体）+ marker → 事件字段冻结
        save_plain_task(self.repo)
        result = run_hook(review_payload(), self.repo)
        self.assertEqual((result.returncode, result.stdout), (0, ""))
        invoked = [e for e in self._events()
                   if e["event"] == "reviewer_invoked"]
        self.assertEqual(len(invoked), 1)
        event = invoked[0]
        self.assertEqual(
            set(event.keys()),
            {"ts", "event", "tool_use_id", "reviewer", "task_id",
             "agent_id", "execution_mode"})
        self.assertEqual(event["tool_use_id"], "call_rev_1")
        self.assertEqual(event["reviewer"], "glm-conductor:glm-reviewer")
        self.assertEqual(event["task_id"], TID)
        self.assertEqual(event["agent_id"], "agent_review_1")
        self.assertEqual(event["execution_mode"], "foreground")

    def test_reviewer_bare_profile_and_background_mode(self):
        # bare 形态白名单 + run_in_background=True → execution_mode=
        # "background"（执行模式自 tool_input 推导，缺省 foreground）
        save_plain_task(self.repo)
        result = run_hook(review_payload(subagent_type="visual-reviewer",
                                         run_in_background=True,
                                         tool_use_id="call_rev_bg"), self.repo)
        self.assertEqual(result.returncode, 0)
        invoked = [e for e in self._events()
                   if e["event"] == "reviewer_invoked"]
        self.assertEqual(len(invoked), 1)
        self.assertEqual(invoked[0]["reviewer"], "visual-reviewer")
        self.assertEqual(invoked[0]["execution_mode"], "background")

    def test_reviewer_without_marker_zero_accounting(self):
        # 反用例：reviewer 类型无 review marker → 零记账（也不消费任何
        # permit——reviewer 免 permit 的既有行为不回退）
        result = run_hook(review_payload(with_marker=False), self.repo)
        self.assertEqual((result.returncode, result.stdout, result.stderr),
                         (0, "", ""))
        self.assertEqual(self._events(), [])

    def test_reviewer_non_profile_type_still_silent(self):
        # 非 reviewer 类型（如 Explore）带 review marker → 不进 reviewer
        # 分支，无 dispatch marker → 静默（现行为）
        payload = review_payload(subagent_type="Explore")
        result = run_hook(payload, self.repo)
        self.assertEqual((result.returncode, result.stdout), (0, ""))
        self.assertEqual(self._events(), [])

    def test_reviewer_marker_missing_task_records_skipped(self):
        # marker 指向的任务不存在 → reviewer_invocation_skipped 警告记账
        # （fail-loud 不阻断：exit 0、stdout 空）；不落 reviewer_invoked
        ghost = "ghost-review-task-9a2b"
        result = run_hook(review_payload(task_id=ghost), self.repo)
        self.assertEqual((result.returncode, result.stdout), (0, ""))
        events = journal.read_events(self.repo, ghost)
        skipped = [e for e in events
                   if e["event"] == "reviewer_invocation_skipped"]
        self.assertEqual(len(skipped), 1)
        self.assertEqual(skipped[0]["reason"], "task_missing")
        self.assertEqual(skipped[0]["task_id"], ghost)
        self.assertEqual(skipped[0]["tool_use_id"], "call_rev_1")
        self.assertEqual(
            [e for e in events if e["event"] == "reviewer_invoked"], [])

    def test_reviewer_failure_path_records_nothing(self):
        # PostToolUseFailure 不做 reviewer 记账（reviewer_invoked 只证明
        # 「调用真实发生过」）：失败路径对 reviewer 类型静默
        result = run_hook(review_payload(event="PostToolUseFailure",
                                         tool_use_id="call_rev_fail"),
                          self.repo)
        self.assertEqual((result.returncode, result.stdout), (0, ""))
        self.assertEqual(self._events(), [])

    def test_implementer_permit_branch_not_regressed(self):
        # permit 分支回归锚：implementer 类型 + dispatch marker 照旧
        # （consume + agent_launched），reviewer 分支零影响
        permit_id, _permit = save_task_with_permit(self.repo)
        result = run_hook(stdin_payload(permit_id), self.repo)
        self.assertEqual(result.returncode, 0, result.stderr)
        events = journal.read_events(self.repo, TID)
        launched = [e for e in events if e["event"] == "agent_launched"]
        self.assertEqual(len(launched), 1)
        self.assertEqual(launched[0]["unit"], "wu-1")
        self.assertEqual(
            [e for e in events if e["event"] == "reviewer_invoked"], [])


if __name__ == "__main__":
    unittest.main()
