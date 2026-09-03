# -*- coding: utf-8 -*-
"""hooks Cron* 观测路径子进程测试（v2.2 C6，wu-22-C6 ③）。

以子进程方式运行 plugins/glm-conductor/hooks/pre_tool_use.py 与
post_tool_use.py，注入 Claude 兼容 JSON 载荷（零真实 Cron 工具、零
网络），验证 C6 scheduler 能力观测主战场的可观测行为：

hooks.json：+3 条目（PreToolUse "CronCreate"、PostToolUse /
PostToolUseFailure "CronCreate|CronUpdate|CronDelete"）与既有条目
并存、既有 matcher 序不变。

PreToolUse CronCreate 嵌套创建 fail-fast 门（职责四）：
  - 会话事实 create=forbidden / origin=scheduled_task 任一在案 →
    deny（单行 JSON permissionDecision，可行动英文报文）；
  - 无 session_id / 无记录 / 记录无禁止事实（create=allowed +
    origin=interactive）→ 静默放行（单探针纪律：无证据不预判）。

PostToolUse Cron* 观测（职责二）：
  - CronCreate 成功：tool_response 深度有界容错提取 automationId /
    automation_id → session facts（create=allowed + origin=interactive +
    automation_ids 追加）+ 每个活动任务经 observe_scheduler_context
    镜像（恰一条 scheduler_capability_observed 事件；wake_bridge.
    status 恒不动——§22.5）；重复观测零新增事件（变更闸）；
  - CronUpdate / CronDelete 成功：update / delete=allowed，origin
    不动；无活动任务 → 只记 facts 零 journal；
  - PostToolUseFailure：仅 CronCreate 且错误文本命中冻结分类
    （NESTED_REJECTION_MARKERS）才记 origin=scheduled_task +
    create=forbidden；瞬时错误 / CronUpdate / CronDelete 失败零写。

fixture：tempfile 仓库 + runtime.state 构造活动任务；session facts 经
runtime.scheduler_facts 直接播种。仅 Python 3 标准库。

运行：
    cd <repo_root> && python3 -m unittest tests.test_cron_hooks -v
"""

import sys, unittest
import json, os, subprocess, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "glm-conductor"))
from runtime import journal, scheduler_facts, state, task_manager

PRE_TOOL_USE = (Path(__file__).resolve().parents[1]
                / "plugins/glm-conductor/hooks/pre_tool_use.py")
POST_TOOL_USE = (Path(__file__).resolve().parents[1]
                 / "plugins/glm-conductor/hooks/post_tool_use.py")
HOOKS_JSON = (Path(__file__).resolve().parents[1]
              / "plugins/glm-conductor/hooks/hooks.json")

TID = "cron-hook-test-1a2b3c"
SESSION = "sess-cron-fixture-1"
ROUTE = {"mode": "delegate", "delegability": "high",
         "assurance": "standard", "executor": "flash-implementer",
         "continuity": "foreground"}
# D15-e 单向升级夹具：CronCreate 成功 → origin=interactive 只在
# unknown 起点落定
ARM_ISO = "2026-09-03T00:00:00Z"


def run_hook(script, stdin_text, project_dir):
    """以子进程运行钩子脚本，返回 CompletedProcess（显式 UTF-8，
    与 test_pre_tool_use / test_post_tool_use 同口径）。"""
    return subprocess.run(
        [sys.executable, str(script)],
        input=stdin_text, text=True, capture_output=True,
        encoding="utf-8", errors="replace",
        env=dict(os.environ, ZCODE_PROJECT_DIR=str(project_dir)))


def save_active_task(repo, task_id=TID, status="executing"):
    """构造一个活动任务（delegate 路由矩阵合法；无 work unit——
    观测镜像不依赖单元）。"""
    state.save_state(repo, state.new_task_state(
        task_id, "cron hooks fixture", dict(ROUTE),
        ownership_files=["src/a.py"],
        verification_required=["python3 -m unittest -h"],
        status=status))


def cron_stdin(tool_name, event="PostToolUse", session_id=SESSION,
               tool_response=None, error=None):
    """构造 Cron* 钩子事件 stdin（Claude 兼容 JSON）。"""
    payload = {"hook_event_name": event, "tool_name": tool_name,
               "tool_input": {"command": "fixture"},
               "tool_use_id": "call_cron_1"}
    if session_id is not None:
        payload["session_id"] = session_id
    if tool_response is not None:
        payload["tool_response"] = tool_response
    if error is not None:
        payload["error"] = error
    return json.dumps(payload)


def parse_single_line_json(text):
    stripped = text.strip()
    if "\n" in stripped:
        raise AssertionError("stdout 不是单行 JSON: %r" % text)
    return json.loads(stripped)


class CronFixtureCase(unittest.TestCase):
    """公共装置：tempfile scratch 仓库 + 活动任务。"""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = tmp.name
        save_active_task(self.repo)

    def facts_record(self, session_id=SESSION):
        return scheduler_facts.read_session_record(self.repo, session_id)

    def task_state(self):
        return state.load_state(self.repo, TID)

    def task_events(self):
        return journal.read_events(self.repo, TID)

    def observed_events(self):
        return [e for e in self.task_events()
                if e["event"] == "scheduler_capability_observed"]

    def scheduler_context(self):
        return self.task_state()["continuation"]["scheduler_context"]


# —— hooks.json：+3 条目与既有条目并存 ——

class CronHooksManifestTest(unittest.TestCase):
    """C6 三条新 matcher 条目：既有条目零改动前提下的 additive 并存。"""

    def setUp(self):
        with open(str(HOOKS_JSON), "r", encoding="utf-8") as fh:
            self.manifest = json.load(fh)
        self.hooks = self.manifest["hooks"]

    def _entries(self, event):
        return [entry for entry in self.hooks[event]
                if isinstance(entry, dict)]

    def test_pretooluse_matchers_additive(self):
        matchers = [entry.get("matcher")
                    for entry in self._entries("PreToolUse")]
        # 既有两条序不变，CronCreate 追加在后（既有条目零改动的佐证）
        self.assertEqual(matchers, ["Agent|Task", "Bash", "CronCreate"])

    def test_posttooluse_failure_matchers_additive(self):
        for event in ("PostToolUse", "PostToolUseFailure"):
            matchers = [entry.get("matcher")
                        for entry in self._entries(event)]
            self.assertEqual(
                matchers, ["Agent|Task", "CronCreate|CronUpdate|CronDelete"],
                event)

    def test_cron_entries_shape(self):
        for event, script in (("PreToolUse", "pre_tool_use.py"),
                              ("PostToolUse", "post_tool_use.py"),
                              ("PostToolUseFailure", "post_tool_use.py")):
            entry = self._entries(event)[-1]
            self.assertEqual(entry["matcher"],
                             "CronCreate" if event == "PreToolUse"
                             else "CronCreate|CronUpdate|CronDelete")
            hook = entry["hooks"][0]
            self.assertEqual(hook["type"], "process")
            self.assertEqual(hook["command"], "python3")
            # 既有 3s timeout 纪律不动
            self.assertEqual(hook["timeoutMs"], 3000)
            self.assertEqual(
                hook["args"],
                ["${ZCODE_PLUGIN_ROOT}/hooks/%s" % script])

    def test_existing_entries_untouched(self):
        # Stop / SessionStart 条目仍在且指向原脚本（零改动佐证）
        self.assertIn("stop_gate.py", json.dumps(self.hooks["Stop"]))
        self.assertIn("session_start.py",
                      json.dumps(self.hooks["SessionStart"]))


# —— PreToolUse CronCreate 嵌套创建 fail-fast 门 ——

class PreToolUseCronGateTest(CronFixtureCase):
    """事实在案 → deny；无证据 → 静默放行（单探针纪律）。"""

    def test_create_forbidden_fact_denies(self):
        scheduler_facts.record_observation(
            self.repo, SESSION, create="forbidden")
        result = run_hook(
            PRE_TOOL_USE,
            json.dumps({"tool_name": "CronCreate", "session_id": SESSION,
                        "tool_input": {"command": "cron create"}}),
            self.repo)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        payload = parse_single_line_json(result.stdout)
        self.assertEqual(sorted(payload.keys()), ["hookSpecificOutput"])
        out = payload["hookSpecificOutput"]
        self.assertEqual(sorted(out.keys()),
                         ["hookEventName", "permissionDecision",
                          "permissionDecisionReason"])
        self.assertEqual(out["hookEventName"], "PreToolUse")
        self.assertEqual(out["permissionDecision"], "deny")
        self.assertIn("create=forbidden", out["permissionDecisionReason"])
        self.assertIn("arm_wake_bridge", out["permissionDecisionReason"])

    def test_scheduled_origin_fact_denies(self):
        scheduler_facts.record_observation(
            self.repo, SESSION, origin="scheduled_task")
        result = run_hook(
            PRE_TOOL_USE,
            json.dumps({"tool_name": "CronCreate", "session_id": SESSION,
                        "tool_input": {}}),
            self.repo)
        self.assertEqual(result.returncode, 0, result.stderr)
        out = parse_single_line_json(result.stdout)["hookSpecificOutput"]
        self.assertEqual(out["permissionDecision"], "deny")
        self.assertIn("origin=scheduled_task",
                      out["permissionDecisionReason"])

    def test_no_evidence_silent_pass(self):
        # 无 facts 文件（单探针第一次尝试必须被放行）
        result = run_hook(
            PRE_TOOL_USE,
            json.dumps({"tool_name": "CronCreate", "session_id": SESSION,
                        "tool_input": {}}),
            self.repo)
        self.assertEqual((result.returncode, result.stdout, result.stderr),
                         (0, "", ""))

    def test_allowed_facts_silent_pass(self):
        # 已证 allowed + interactive（无禁止事实）→ 放行
        scheduler_facts.record_observation(
            self.repo, SESSION, origin="interactive", create="allowed",
            automation_id="auto-xyz")
        result = run_hook(
            PRE_TOOL_USE,
            json.dumps({"tool_name": "CronCreate", "session_id": SESSION,
                        "tool_input": {}}),
            self.repo)
        self.assertEqual((result.returncode, result.stdout, result.stderr),
                         (0, "", ""))

    def test_missing_session_id_silent_pass(self):
        # 载荷无 session_id → 无检索键 → 放行（即使盘上有其他会话事实）
        scheduler_facts.record_observation(
            self.repo, "other-session-9", create="forbidden")
        result = run_hook(
            PRE_TOOL_USE,
            json.dumps({"tool_name": "CronCreate", "tool_input": {}}),
            self.repo)
        self.assertEqual((result.returncode, result.stdout, result.stderr),
                         (0, "", ""))

    def test_foreign_session_record_not_consulted(self):
        scheduler_facts.record_observation(
            self.repo, "other-session-9", create="forbidden")
        result = run_hook(
            PRE_TOOL_USE,
            json.dumps({"tool_name": "CronCreate", "session_id": SESSION,
                        "tool_input": {}}),
            self.repo)
        self.assertEqual((result.returncode, result.stdout), (0, ""))

    def test_deny_does_not_mutate_facts(self):
        # 门是只读面：deny 路径零写（单探针事实不被门改写）
        scheduler_facts.record_observation(
            self.repo, SESSION, create="forbidden")
        before = self.facts_record()
        run_hook(
            PRE_TOOL_USE,
            json.dumps({"tool_name": "CronCreate", "session_id": SESSION,
                        "tool_input": {}}),
            self.repo)
        self.assertEqual(self.facts_record(), before)


# —— PostToolUse Cron* 成功观测 ——

class PostToolUseCronSuccessTest(CronFixtureCase):
    """成功路径：facts + 任务镜像 + 恰一条观测事件；§22.5 桥状态不动。"""

    def test_cron_create_success_records_facts_and_mirror(self):
        result = run_hook(
            POST_TOOL_USE,
            cron_stdin("CronCreate",
                       tool_response={"automationId": "auto-123",
                                      "nextRunAt": ARM_ISO}),
            self.repo)
        self.assertEqual(result.returncode, 0, result.stderr)
        # hook 恒零输出（记账静默纪律）
        self.assertEqual(result.stdout, "")
        record = self.facts_record()
        self.assertEqual(record["create"], "allowed")
        self.assertEqual(record["origin"], "interactive")
        self.assertEqual(record["automation_ids"], ["auto-123"])
        # 任务镜像：单条观测事件 + scheduler_context 落定
        events = self.observed_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["create"], "allowed")
        self.assertEqual(events[0]["origin"], "interactive")
        self.assertEqual(events[0]["parent_automation_id"], "auto-123")
        self.assertEqual(events[0]["source_session"], SESSION)
        ctx = self.scheduler_context()
        self.assertEqual(ctx["origin"], "interactive")
        self.assertEqual(ctx["create"], "allowed")
        self.assertEqual(ctx["parent_automation_id"], "auto-123")
        # §22.5：观测不制造 armed
        self.assertEqual(
            self.task_state()["continuation"]["wake_bridge"]["status"],
            "none")

    def test_nested_response_extraction_and_cronupdate(self):
        # 深度有界容错：automation_id（snake 形态）嵌套在两层 dict 内
        result = run_hook(
            POST_TOOL_USE,
            cron_stdin("CronUpdate",
                       tool_response={"result": {"meta": {
                           "automation_id": "auto-456",
                           "next_run_at": ARM_ISO}}}),
            self.repo)
        self.assertEqual(result.returncode, 0, result.stderr)
        record = self.facts_record()
        self.assertEqual(record["update"], "allowed")
        self.assertIsNone(record["origin"])  # update 不证明会话来源
        self.assertEqual(record["automation_ids"], ["auto-456"])
        ctx = self.scheduler_context()
        self.assertEqual(ctx["update"], "allowed")
        self.assertEqual(ctx["origin"], "unknown")
        self.assertEqual(ctx["parent_automation_id"], "auto-456")

    def test_cron_delete_success_records_delete_allowed(self):
        result = run_hook(
            POST_TOOL_USE,
            cron_stdin("CronDelete", tool_response={"deleted": True}),
            self.repo)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.facts_record()["delete"], "allowed")
        ctx = self.scheduler_context()
        self.assertEqual(ctx["delete"], "allowed")
        self.assertEqual(len(self.observed_events()), 1)

    def test_repeat_observation_zero_new_event(self):
        # 变更闸：同证据重放 → observe 幂等零事件；facts 零空转写
        for _ in range(2):
            result = run_hook(
                POST_TOOL_USE,
                cron_stdin("CronCreate",
                           tool_response={"automationId": "auto-123"}),
                self.repo)
            self.assertEqual(result.returncode, 0)
        self.assertEqual(len(self.observed_events()), 1)
        record = self.facts_record()
        self.assertEqual(record["automation_ids"], ["auto-123"])

    def test_no_session_id_still_mirrors_tasks(self):
        # facts 按 session_id 键控：无键跳过 facts，但任务镜像不阻断
        result = run_hook(
            POST_TOOL_USE,
            cron_stdin("CronCreate", session_id=None,
                       tool_response={"automationId": "auto-789"}),
            self.repo)
        self.assertEqual(result.returncode, 0)
        self.assertIsNone(self.facts_record())
        ctx = self.scheduler_context()
        self.assertEqual(ctx["create"], "allowed")
        self.assertEqual(ctx["parent_automation_id"], "auto-789")
        self.assertEqual(len(self.observed_events()), 1)


class PostToolUseNoActiveTaskTest(unittest.TestCase):
    """无活动任务 → 只记 session facts，零 journal（C4 伪任务教训）。"""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = tmp.name  # 空仓库：无任务

    def test_facts_only_zero_journal_anywhere(self):
        result = run_hook(
            POST_TOOL_USE,
            cron_stdin("CronCreate",
                       tool_response={"automationId": "auto-solo"}),
            self.repo)
        self.assertEqual(result.returncode, 0, result.stderr)
        record = scheduler_facts.read_session_record(self.repo, SESSION)
        self.assertEqual(record["create"], "allowed")
        # 任务 journal：无任务目录；控制面 journal 也不落（观测无
        # 控制面事件语义）
        tasks_root = Path(self.repo) / ".glm-conductor" / "tasks"
        self.assertFalse(tasks_root.exists())
        self.assertEqual(journal.read_control_plane_events(self.repo), [])


# —— PostToolUseFailure：嵌套拒绝分类（仅 CronCreate） ——

class PostToolUseFailureClassificationTest(CronFixtureCase):
    """冻结关键词分类：命中才升格；瞬时错误零写。"""

    def test_nested_rejection_text_upgrades_facts(self):
        result = run_hook(
            POST_TOOL_USE,
            cron_stdin("CronCreate", event="PostToolUseFailure",
                       error="CronCreate failed: nested automation "
                             "creation is not allowed"),
            self.repo)
        self.assertEqual(result.returncode, 0, result.stderr)
        record = self.facts_record()
        self.assertEqual(record["create"], "forbidden")
        self.assertEqual(record["origin"], "scheduled_task")
        events = self.observed_events()
        self.assertEqual(len(events), 1)
        ctx = self.scheduler_context()
        self.assertEqual(ctx["create"], "forbidden")
        self.assertEqual(ctx["origin"], "scheduled_task")

    def test_schedul_plus_qualifier_hits_without_nested(self):
        # 主标记未中、宿主 + 限定词组合命中
        result = run_hook(
            POST_TOOL_USE,
            cron_stdin("CronCreate", event="PostToolUseFailure",
                       error="scheduled task creation denied by host"),
            self.repo)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.facts_record()["create"], "forbidden")

    def test_transient_error_zero_writes(self):
        for text in ("gateway quota exceeded", "network timeout",
                     "invalid arguments"):
            with self.subTest(error=text):
                result = run_hook(
                    POST_TOOL_USE,
                    cron_stdin("CronCreate", event="PostToolUseFailure",
                               error=text),
                    self.repo)
                self.assertEqual((result.returncode, result.stdout),
                                 (0, ""))
                self.assertIsNone(self.facts_record())
                self.assertEqual(self.observed_events(), [])

    def test_cronupdate_delete_failures_zero_writes(self):
        # 分类只对 CronCreate——update/delete 失败无嵌套语义，恒零写
        for tool in ("CronUpdate", "CronDelete"):
            with self.subTest(tool=tool):
                result = run_hook(
                    POST_TOOL_USE,
                    cron_stdin(tool, event="PostToolUseFailure",
                               error="nested creation forbidden"),
                    self.repo)
                self.assertEqual(result.returncode, 0)
                self.assertIsNone(self.facts_record())
                self.assertEqual(self.observed_events(), [])

    def test_structured_error_message_classified(self):
        # error 为 dict：error / message 键有界容错提取后分类
        result = run_hook(
            POST_TOOL_USE,
            cron_stdin("CronCreate", event="PostToolUseFailure",
                       error={"code": 400,
                              "message": "CronCreate rejected: nested "
                                         "scheduling is forbidden"}),
            self.repo)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.facts_record()["create"], "forbidden")

    def test_rejection_evidence_freezes_against_later_allowed(self):
        # 先 forbidden（拒绝证据）后 CronCreate 成功 allowed → 证据
        # 只进不退：facts 与 scheduler_context 恒 forbidden
        run_hook(
            POST_TOOL_USE,
            cron_stdin("CronCreate", event="PostToolUseFailure",
                       error="nested forbidden"),
            self.repo)
        run_hook(
            POST_TOOL_USE,
            cron_stdin("CronCreate", tool_response={"automationId": "a1"}),
            self.repo)
        record = self.facts_record()
        self.assertEqual(record["create"], "forbidden")
        # origin 同理单向：scheduled_task 不被 interactive 冲掉
        self.assertEqual(record["origin"], "scheduled_task")
        ctx = self.scheduler_context()
        self.assertEqual(ctx["create"], "forbidden")
        self.assertEqual(ctx["origin"], "scheduled_task")


class ObservationNeverTouchesBridgeTest(CronFixtureCase):
    """§22.5 全观测路径绝不碰 wake_bridge.status（已 armed 桥不被
    观测制造 / 改动 / 降级）。"""

    def setUp(self):
        super().setUp()
        task_manager.arm_wake_bridge(
            self.repo, TID, automation_id="auto-persist",
            boundary_id="five_hour:%s" % ARM_ISO, reset_at=ARM_ISO,
            wake_at=ARM_ISO, bridge_interval_minutes=60)

    def test_armed_bridge_untouched_by_success_observation(self):
        run_hook(
            POST_TOOL_USE,
            cron_stdin("CronCreate", tool_response={"automationId": "a2"}),
            self.repo)
        bridge = self.task_state()["continuation"]["wake_bridge"]
        self.assertEqual(bridge["status"], "armed")
        self.assertEqual(bridge["automation_id"], "auto-persist")

    def test_armed_bridge_untouched_by_rejection_observation(self):
        run_hook(
            POST_TOOL_USE,
            cron_stdin("CronCreate", event="PostToolUseFailure",
                       error="nested forbidden"),
            self.repo)
        bridge = self.task_state()["continuation"]["wake_bridge"]
        self.assertEqual(bridge["status"], "armed")


class AgentTaskPathsNotRegressedTest(CronFixtureCase):
    """Agent|Task 既有记账分支不受 Cron* 分支影响（tool_name 分发
    先行但只对 CRON_TOOLS 截流）。"""

    def test_agent_payload_still_routed_to_old_branches(self):
        # Agent 载荷无 dispatch marker → 旧分支静默（不进 Cron 路径、
        # 不落 scheduler 观测）
        result = run_hook(
            POST_TOOL_USE,
            json.dumps({"hook_event_name": "PostToolUse",
                        "tool_name": "Agent",
                        "tool_input": {"prompt": "read only"},
                        "tool_response": {}}),
            self.repo)
        self.assertEqual((result.returncode, result.stdout, result.stderr),
                         (0, "", ""))
        self.assertIsNone(self.facts_record())
        self.assertEqual(self.observed_events(), [])


if __name__ == "__main__":
    unittest.main()
