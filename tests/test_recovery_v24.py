#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""runtime.recovery v2.4 恢复简化测试（Phase 2 P2-E / 单元 W5）。

覆盖（W5 单元规格的四类最低集 + 补充）：

recovery.build_recovery_summary（v2.4 摘要冻结形状）：
  - active / waiting_quota / waiting_user 三态摘要形态：条目键恰为
    task_id / goal / repository / status / route / workflow_run_id /
    next_action 七键 + 条件第八键 quota_wait（仅额度等待态携带，值为
    status / mode / resume_count / max_resumes 四键）；goal 截断 120；
    repository 经绑定解析；多任务按 task_id 排序；
  - v2.3 遗留任务一行式：落 summary["legacy"]（恰 task_id / guidance /
    next_action 三键，guidance 为 state.LEGACY_STATE_GUIDANCE 逐字），
    绝不混入 summary["tasks"]；
  - 空 / 全终态仓库 → 三空列表；corrupt state.json → summary["corrupt"]。

recovery.next_safe_action（下一步安全动作映射）：
  - 词汇恰为七值（NEXT_ACTIONS 逐字冻结）；
  - 优先级映射逐格覆盖：遗留 → retire；waiting_* → wait；blocked →
    reassess；high 保障 + 审查未 ship → run required review；
    validating 相未验证 → run main validation；验证已过 → inspect
    workflow（有 run）/ reassess（无 run）；执行相有 run → resume
    workflow；有 run 无标注 → inspect；无 run → reassess。

recovery.render_resume_context（v2.4 渲染形态）：
  - 头部逐字 "GLM CONDUCTOR RESUME CONTEXT"；任务段恰八行（Task /
    Repository / Goal / Status / Route / Workflow run / [Quota wait] /
    Next action）；legacy 恰一行；任务超 3 只列前 3 + omitted 行；
    corrupt 单行 WARNING；无内容 → ""；
  - 无任何单元级字段出现（渲染文本与摘要条目双向断言：禁词表 + 精确
    键集）；recovery 导入面零残留（ast 解析导入语句：仅 runtime.state，
    无 resume_manifest / agent_run / journal / execution_policy /
    lease / reconcile 等 legacy 模块）。

hooks.session_start（子进程直调，同 tests/test_recovery.py 先例——钩子
快照不热加载）：
  - 有活动任务 → exit 0、stderr 空、stdout 单行 JSON
    hookSpecificOutput，additionalContext 与纯 recovery 渲染逐字节
    一致（test_session_start_advisory 共存契约的零噪音回归面）；
  - 空仓库 / 仅终态任务 → 恒静默。

仅 Python 3 标准库（unittest + subprocess + tempfile + ast），零第三方
依赖；被检仓库目录由 tempfile 提供，不污染真实工作区；状态 fixture 经
sys.path 接插件根后用 v2.4 runtime.state 正门构造（遗留 / 损坏盘面
绕过 save_state 直写原始 JSON，同 tests/test_state_v24.py 先例）。

运行：
    cd <repo_root> && python3 -X utf8 -m unittest tests.test_recovery_v24 -v
"""

import ast
import inspect
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "glm-conductor"))

from runtime import recovery, state  # noqa: E402

# 被测钩子脚本：仓库根 plugins/glm-conductor/hooks/session_start.py
SESSION_START = (Path(__file__).resolve().parents[1]
                 / "plugins/glm-conductor/hooks/session_start.py")

LONG_GOAL = "g" * 200
CHANGE_ID = "sha256:" + "0" * 16

# 单元级禁词（v2.1-v2.3 恢复面概念，v2.4 渲染文本与摘要条目一律不得
# 出现——大小写不敏感匹配）
UNIT_LEVEL_FORBIDDEN = (
    "unit", "interrupted", "zombie", "wake bridge", "quota phase",
    "auto resume", "next safe action", "quota-resume authorization",
    "reconcile", "lease", "receipt", "permit", "agent run", "manifest",
    "recommended", "completed_units", "incomplete_units",
    "interrupted_units", "auto_resume", "continuity", "execution_policy",
    "run_lifecycle", "work_units",
)

# legacy 恢复导入禁词（W5 规格点名 resume_manifest；其余为 v2.3 执行
# 运行时模块——recovery 的导入面必须零残留）
BANNED_IMPORTS = (
    "resume_manifest", "agent_run", "journal", "execution_policy",
    "lease", "reconcile", "provenance", "fingerprint", "task_manager",
    "dispatcher", "dispatch_wave", "policy", "legacy_unit",
)


# —— 测试夹具 ——

def v24_task(repo, task_id, *, status="active", goal="v2.4 恢复冒烟目标",
             mode="solo", assurance=None, run_id=None, phase=None,
             validation_status=None, review_verdict=None,
             max_resumes=None, resume_count=None):
    """经 v2.4 状态层正门构造并保存一个活动任务（校验由 save_state 把关）。"""
    route = {"mode": mode}
    if assurance is not None:
        route["assurance"] = assurance
    st = state.new_task_state(
        task_id, goal, route, repository_root=repo,
        workflow_run_id=run_id, status=status, phase=phase)
    if validation_status is not None:
        st["validation"] = {
            "status": validation_status,
            "change_id": CHANGE_ID,
            "summary": None,
            "commands": None,
        }
    if review_verdict is not None:
        st["review"] = {
            "reviewer": "glm-reviewer",
            "verdict": review_verdict,
            "change_id": CHANGE_ID,
            "findings": None,
        }
    if max_resumes is not None:
        st["quota_resume"]["max_resumes"] = max_resumes
    if resume_count is not None:
        st["quota_resume"]["resume_count"] = resume_count
    state.save_state(repo, st)
    return st


def write_raw_state(repo_root, task_id, payload):
    """绕过 save_state 直接落盘原始 JSON（构造遗留 / 损坏盘面用）。"""
    path = state.state_path(repo_root, task_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(path), "w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    return path


def legacy_v23_payload(task_id="legacy-23-task"):
    """构造带 v2.3 标记的最小遗留 state（work_units 键 + 阶段态）。"""
    return {
        "task_id": task_id,
        "goal": "v2.3 遗留任务",
        "route": {"mode": "delegate", "delegability": "high",
                  "assurance": "standard", "executor": "flash-implementer",
                  "continuity": "foreground"},
        "status": "executing",
        "work_units": [{"id": "u1", "status": "executing",
                        "lease": {"holder": "main"}}],
    }


def run_hook(stdin_text, project_dir):
    """以子进程运行 session_start.py，返回 CompletedProcess。

    显式 UTF-8 解码 + errors="replace"（test_recovery 先例）；ZCODE_
    PROJECT_DIR 指向被检仓库；GLM_CONDUCTOR_HOME 注入一次性空目录
    （隔离本机用户级状态，保证「完全静默」断言不依赖运行机器的
    用户目录——v2.4 P3-D 起钩子已无额度时钟读取面，本注入纯为
    隔离惯例保留）。
    """
    with tempfile.TemporaryDirectory(prefix="gc-recovery-v24-home-") as home:
        return subprocess.run(
            [sys.executable, str(SESSION_START)],
            input=stdin_text, text=True, capture_output=True,
            encoding="utf-8", errors="replace",
            env=dict(os.environ, ZCODE_PROJECT_DIR=str(project_dir),
                     **{"GLM_CONDUCTOR_HOME": home}))


def parse_single_line_json(text):
    """把 hook stdout 按单行 JSON 解析并返回 dict（test_recovery 同款）。"""
    stripped = text.strip()
    if "\n" in stripped:
        raise AssertionError("stdout 不是单行 JSON: %r" % text)
    return json.loads(stripped)


class TempRepoTest(unittest.TestCase):
    """提供每测试隔离的临时仓库根。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = self._tmp.name


# —— 1. 三态摘要形态 ——

class SummaryShapeTest(TempRepoTest):
    """active / waiting_quota / waiting_user 三态的摘要冻结形状。"""

    V24_ENTRY_KEYS = {"task_id", "goal", "repository", "status", "route",
                      "workflow_run_id", "next_action"}

    def _single_entry(self):
        summary = recovery.build_recovery_summary(self.repo)
        self.assertEqual(summary["legacy"], [])
        self.assertEqual(summary["corrupt"], [])
        self.assertEqual(len(summary["tasks"]), 1)
        return summary["tasks"][0]

    def test_active_task_entry_shape(self):
        """active 任务：恰七键，无 quota_wait，route 两键视图。"""
        v24_task(self.repo, "rec-active-1a", run_id="run-xyz")
        entry = self._single_entry()
        self.assertEqual(set(entry.keys()), self.V24_ENTRY_KEYS)
        self.assertEqual(entry["task_id"], "rec-active-1a")
        self.assertEqual(entry["goal"], "v2.4 恢复冒烟目标")
        self.assertEqual(entry["status"], "active")
        self.assertEqual(entry["route"], {"mode": "solo", "assurance": None})
        self.assertEqual(entry["workflow_run_id"], "run-xyz")
        # repository 经绑定解析（与状态层同一读取面）
        loaded = state.load_state(self.repo, "rec-active-1a")
        self.assertEqual(entry["repository"],
                         state.bound_repository_root(loaded))
        self.assertNotIn("quota_wait", entry)

    def test_active_task_default_next_action_reassess(self):
        """active 无 run 未验证 → 下一步动作 reassess repository。"""
        v24_task(self.repo, "rec-active-2b")
        entry = self._single_entry()
        self.assertEqual(entry["next_action"], "reassess repository")

    def test_waiting_quota_entry_shape(self):
        """waiting_quota 任务：七键 + 条件第八键 quota_wait 四键。"""
        v24_task(self.repo, "rec-waitq-3c", status="waiting_quota",
                 max_resumes=2, resume_count=1)
        entry = self._single_entry()
        self.assertEqual(set(entry.keys()),
                         self.V24_ENTRY_KEYS | {"quota_wait"})
        self.assertEqual(entry["status"], "waiting_quota")
        self.assertEqual(
            entry["quota_wait"],
            {"status": "waiting_quota", "mode": "manual",
             "resume_count": 1, "max_resumes": 2})
        self.assertEqual(entry["next_action"], "wait for quota or user")

    def test_waiting_user_entry_shape(self):
        """waiting_user 任务：同款条件键，动作 wait for quota or user。"""
        v24_task(self.repo, "rec-waitu-4d", status="waiting_user")
        entry = self._single_entry()
        self.assertEqual(set(entry.keys()),
                         self.V24_ENTRY_KEYS | {"quota_wait"})
        self.assertEqual(
            entry["quota_wait"],
            {"status": "waiting_user", "mode": "manual",
             "resume_count": 0, "max_resumes": 0})
        self.assertEqual(entry["next_action"], "wait for quota or user")

    def test_goal_truncated_to_120(self):
        """goal 超 120 截断（冻结体积上限）。"""
        v24_task(self.repo, "rec-goal-5e", goal=LONG_GOAL)
        entry = self._single_entry()
        self.assertEqual(entry["goal"], "g" * 120)

    def test_tasks_sorted_by_task_id(self):
        """多任务按 task_id 排序。"""
        v24_task(self.repo, "rec-b-1")
        v24_task(self.repo, "rec-a-2")
        v24_task(self.repo, "rec-c-3")
        summary = recovery.build_recovery_summary(self.repo)
        self.assertEqual([entry["task_id"] for entry in summary["tasks"]],
                         ["rec-a-2", "rec-b-1", "rec-c-3"])

    def test_empty_and_terminal_repo(self):
        """空仓库 / 仅终态任务 → 三空列表（render 层据此静默）。"""
        self.assertEqual(recovery.build_recovery_summary(self.repo),
                         {"tasks": [], "legacy": [], "corrupt": []})
        v24_task(self.repo, "rec-done-6f", status="completed")
        self.assertEqual(recovery.build_recovery_summary(self.repo),
                         {"tasks": [], "legacy": [], "corrupt": []})

    def test_corrupt_state_listed(self):
        """损坏 state.json → corrupt 桶显式报告（目录名排序）。"""
        write_raw_state(self.repo, "rec-corrupt-7g", "{not-json")
        summary = recovery.build_recovery_summary(self.repo)
        self.assertEqual(summary["corrupt"], ["rec-corrupt-7g"])
        self.assertEqual(summary["tasks"], [])


# —— 2. legacy 一行式 ——

class LegacyOneLinerTest(TempRepoTest):
    """v2.3 遗留任务：独立 legacy 桶 + 渲染恰一行。"""

    def test_legacy_entry_shape(self):
        """遗留条目恰三键：task_id / guidance 逐字 / retire 动作。"""
        write_raw_state(self.repo, "legacy-23-task",
                        legacy_v23_payload("legacy-23-task"))
        summary = recovery.build_recovery_summary(self.repo)
        self.assertEqual(summary["tasks"], [])
        self.assertEqual(len(summary["legacy"]), 1)
        entry = summary["legacy"][0]
        self.assertEqual(set(entry.keys()),
                         {"task_id", "guidance", "next_action"})
        self.assertEqual(entry["task_id"], "legacy-23-task")
        self.assertEqual(entry["guidance"], state.LEGACY_STATE_GUIDANCE)
        self.assertEqual(entry["next_action"],
                         "explicitly retire legacy task")

    def test_legacy_by_status_vocabulary_alone(self):
        """仅 v2.3 阶段态 status（无 work_units 键）同样落 legacy 桶。"""
        write_raw_state(self.repo, "legacy-stage-8h", {
            "task_id": "legacy-stage-8h",
            "goal": "阶段态遗留",
            "route": {"mode": "solo"},
            "status": "verifying",
        })
        summary = recovery.build_recovery_summary(self.repo)
        self.assertEqual(summary["tasks"], [])
        self.assertEqual(
            [entry["task_id"] for entry in summary["legacy"]],
            ["legacy-stage-8h"])

    def test_legacy_renders_single_line(self):
        """渲染恰一行：检测到 v2.3 任务 + 处置指引 + retire 动作。"""
        write_raw_state(self.repo, "legacy-23-task",
                        legacy_v23_payload("legacy-23-task"))
        text = recovery.render_resume_context(
            recovery.build_recovery_summary(self.repo))
        self.assertTrue(text.startswith(recovery.RESUME_HEADER))
        lines = [line for line in text.split("\n")
                 if "legacy-23-task" in line]
        self.assertEqual(len(lines), 1,
                         "遗留任务必须渲染为一行：%r" % text)
        line = lines[0]
        self.assertIn("v2.3 task detected", line)
        self.assertIn(state.LEGACY_STATE_GUIDANCE, line)
        self.assertIn("explicitly retire legacy task", line)
        # 遗留任务不展开任何字段行
        self.assertNotIn("Goal:", text)
        self.assertNotIn("Status:", text)
        self.assertNotIn("Route:", text)

    def test_mixed_v24_and_legacy_separated(self):
        """v2.4 与 legacy 混仓：分桶呈现互不污染。"""
        v24_task(self.repo, "rec-live-9i")
        write_raw_state(self.repo, "legacy-23-task",
                        legacy_v23_payload("legacy-23-task"))
        summary = recovery.build_recovery_summary(self.repo)
        self.assertEqual([entry["task_id"] for entry in summary["tasks"]],
                         ["rec-live-9i"])
        self.assertEqual([entry["task_id"] for entry in summary["legacy"]],
                         ["legacy-23-task"])
        text = recovery.render_resume_context(summary)
        self.assertIn("Task: rec-live-9i", text)
        self.assertIn("Legacy task legacy-23-task:", text)


# —— 3. next-action 映射 ——

class NextActionMappingTest(unittest.TestCase):
    """next_safe_action 纯函数的七词词汇与优先级映射。"""

    def test_vocabulary_frozen(self):
        """词汇恰为七值，逐字冻结、无重复。"""
        self.assertEqual(
            set(recovery.NEXT_ACTIONS),
            {"inspect workflow", "resume workflow", "reassess repository",
             "run main validation", "run required review",
             "wait for quota or user", "explicitly retire legacy task"})
        self.assertEqual(len(recovery.NEXT_ACTIONS), 7)

    def test_legacy_maps_to_retire(self):
        self.assertEqual(
            recovery.next_safe_action(legacy_v23_payload()),
            "explicitly retire legacy task")

    def test_waiting_states_map_to_wait(self):
        self.assertEqual(
            recovery.next_safe_action({"status": "waiting_quota"}),
            "wait for quota or user")
        self.assertEqual(
            recovery.next_safe_action({"status": "waiting_user"}),
            "wait for quota or user")

    def test_blocked_maps_to_reassess(self):
        self.assertEqual(
            recovery.next_safe_action({"status": "blocked"}),
            "reassess repository")

    def test_high_assurance_without_ship_review(self):
        """验证已过 + high 保障 + 审查未 ship → run required review。"""
        base = {"status": "active", "phase": "reviewing",
                "validation": {"status": "passed"},
                "route": {"assurance": "high"}}
        for verdict in (None, "fix-first", "rethink"):
            state_dict = dict(base)
            state_dict["review"] = {"verdict": verdict}
            self.assertEqual(
                recovery.next_safe_action(state_dict),
                "run required review", "verdict=%r" % verdict)

    def test_validating_phase_without_validation(self):
        """验证相未验证（未记录 / failed）→ run main validation。"""
        for validation_status in (None, "failed"):
            state_dict = {"status": "active", "phase": "validating",
                          "workflow_run_id": "run-1"}
            if validation_status is not None:
                state_dict["validation"] = {"status": validation_status}
            self.assertEqual(
                recovery.next_safe_action(state_dict),
                "run main validation", "validation=%r" % validation_status)

    def test_validation_passed_with_run_inspects(self):
        """验证已过（审查义务已了）+ 有 run → inspect workflow。"""
        self.assertEqual(
            recovery.next_safe_action(
                {"status": "active", "validation": {"status": "passed"},
                 "review": {"verdict": "ship"},
                 "route": {"assurance": "high"},
                 "workflow_run_id": "run-1"}),
            "inspect workflow")
        self.assertEqual(
            recovery.next_safe_action(
                {"status": "active", "validation": {"status": "passed"}}),
            "reassess repository")

    def test_mid_workflow_run_maps_to_resume(self):
        """执行相标注 + 有 run + 未验证 → resume workflow。"""
        self.assertEqual(
            recovery.next_safe_action(
                {"status": "active", "phase": "workflow",
                 "workflow_run_id": "run-1"}),
            "resume workflow")

    def test_run_without_phase_maps_to_inspect(self):
        """有 run、无 phase 标注、未验证 → 先核对（inspect workflow）。"""
        self.assertEqual(
            recovery.next_safe_action(
                {"status": "active", "workflow_run_id": "run-1"}),
            "inspect workflow")

    def test_no_run_defaults_to_reassess(self):
        """无 run、未验证、无标注 → reassess repository（含空 dict /
        非 dict 兜底）。"""
        self.assertEqual(recovery.next_safe_action({}),
                         "reassess repository")
        self.assertEqual(recovery.next_safe_action({"status": "active"}),
                         "reassess repository")
        self.assertEqual(recovery.next_safe_action(None),
                         "reassess repository")


# —— 4. 渲染形态 + 无单元级字段 ——

class RenderShapeTest(TempRepoTest):
    """v2.4 渲染形态：段序、Quota wait 行、截断、corrupt、禁词。"""

    def test_empty_summary_renders_empty(self):
        self.assertEqual(
            recovery.render_resume_context(
                {"tasks": [], "legacy": [], "corrupt": []}), "")
        self.assertEqual(recovery.render_resume_context({}), "")
        self.assertEqual(recovery.render_resume_context(None), "")

    def test_task_block_frozen_line_order(self):
        """任务段恰八行：空行 + Task/Repository/Goal/Status/Route/
        Workflow run/Next action（无 quota_wait 时恰七行内容）。"""
        v24_task(self.repo, "rec-active-1a", mode="delegate",
                 assurance="high", run_id="run-xyz")
        text = recovery.render_resume_context(
            recovery.build_recovery_summary(self.repo))
        lines = text.split("\n")
        self.assertEqual(lines[0], recovery.RESUME_HEADER)
        expected_labels = [
            "",
            "Task: rec-active-1a",
            "Repository: ",  # 前缀断言：值为绑定根归一绝对路径
            "Goal: v2.4 恢复冒烟目标",
            "Status: active",
            "Route: delegate (assurance: high)",
            "Workflow run: run-xyz",
            "Next action: inspect workflow",  # 有 run 无 phase → 先核对
        ]
        segment = lines[1:1 + len(expected_labels)]
        self.assertEqual(len(segment), len(expected_labels))
        self.assertEqual(segment[0], expected_labels[0])
        self.assertEqual(segment[1], expected_labels[1])
        self.assertTrue(segment[2].startswith(expected_labels[2]),
                        segment[2])
        for actual, expected in zip(segment[3:], expected_labels[3:]):
            self.assertEqual(actual, expected)
        self.assertNotIn("Quota wait:", text)

    def test_quota_wait_line_renders(self):
        """额度等待任务渲染 Quota wait 一行（status + 占位块记账）。"""
        v24_task(self.repo, "rec-waitq-3c", status="waiting_quota",
                 max_resumes=2, resume_count=1)
        text = recovery.render_resume_context(
            recovery.build_recovery_summary(self.repo))
        self.assertIn("Quota wait: waiting_quota (resume mode: manual; "
                      "resumes used 1/2)", text)
        self.assertIn("Next action: wait for quota or user", text)

    def test_more_than_three_tasks_omitted(self):
        """任务超 3 只列前 3 + omitted 计数行。"""
        for index in range(5):
            v24_task(self.repo, "rec-v24-%02d" % index)
        text = recovery.render_resume_context(
            recovery.build_recovery_summary(self.repo))
        self.assertEqual(text.count("Task: rec-v24-"), 3)
        self.assertIn("(2 more task(s) omitted)", text)

    def test_corrupt_warning_single_line(self):
        """corrupt 非空 → 末尾单行 WARNING（仅 corrupt 也输出）。"""
        write_raw_state(self.repo, "rec-corrupt-7g", "{not-json")
        text = recovery.render_resume_context(
            recovery.build_recovery_summary(self.repo))
        lines = text.split("\n")
        self.assertEqual(lines[0], recovery.RESUME_HEADER)
        warning_lines = [line for line in lines if line.startswith("WARNING")]
        self.assertEqual(len(warning_lines), 1)
        self.assertIn("rec-corrupt-7g", warning_lines[0])

    def test_no_unit_level_fields_anywhere(self):
        """全场景渲染：无任何单元级字段出现（文本 + 条目双向断言）。"""
        v24_task(self.repo, "rec-live-9i", run_id="run-1")
        v24_task(self.repo, "rec-waitq-3c", status="waiting_quota")
        write_raw_state(self.repo, "legacy-23-task",
                        legacy_v23_payload("legacy-23-task"))
        write_raw_state(self.repo, "rec-corrupt-7g", "{not-json")
        summary = recovery.build_recovery_summary(self.repo)
        text = recovery.render_resume_context(summary)
        lowered = text.lower()
        for word in UNIT_LEVEL_FORBIDDEN:
            self.assertNotIn(word, lowered,
                             "渲染文本出现单元级禁词 %r" % word)
        for entry in summary["tasks"] + summary["legacy"]:
            for word in UNIT_LEVEL_FORBIDDEN:
                self.assertNotIn(word, str(entry).lower(),
                                 "摘要条目出现单元级禁词 %r" % word)
        # 摘要条目精确键集（形状层面无单元级字段）
        for entry in summary["tasks"]:
            self.assertTrue(set(entry.keys()) <= {
                "task_id", "goal", "repository", "status", "route",
                "workflow_run_id", "quota_wait", "next_action"})
        for entry in summary["legacy"]:
            self.assertEqual(set(entry.keys()),
                             {"task_id", "guidance", "next_action"})


class ImportSurfaceTest(unittest.TestCase):
    """recovery 导入面零残留：仅 runtime.state，legacy 模块零导入。"""

    def test_imports_only_runtime_state(self):
        source = inspect.getsource(recovery)
        tree = ast.parse(source)
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                names.add(node.module or "")
        # `from runtime import state` 的 ImportFrom.module 是 "runtime"
        self.assertEqual(names, {"runtime"})
        # from 项层面锚定：只允许 import state（禁用模块零残留）
        imported_from_runtime = [
            alias.name for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module == "runtime"
            for alias in node.names]
        self.assertEqual(imported_from_runtime, ["state"])
        for name in names:
            for banned in BANNED_IMPORTS:
                self.assertNotIn(banned, name,
                                 "recovery 导入了禁用模块 %r" % name)


# —— 5. hooks.session_start 集成（子进程直调） ——

class SessionStartHookTest(TempRepoTest):
    """恢复渲染路径经钩子进程的端到端契约。"""

    def test_empty_repo_silent(self):
        result = run_hook("{}", self.repo)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")

    def test_terminal_only_silent(self):
        v24_task(self.repo, "rec-done-6f", status="completed")
        result = run_hook("{}", self.repo)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")

    def test_active_task_injects_single_line_json(self):
        v24_task(self.repo, "rec-active-1a", run_id="run-xyz")
        result = run_hook("{\"stub\": true}", self.repo)
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = parse_single_line_json(result.stdout)
        self.assertEqual(sorted(payload.keys()), ["hookSpecificOutput"])
        block = payload["hookSpecificOutput"]
        self.assertEqual(sorted(block.keys()),
                         ["additionalContext", "hookEventName"])
        self.assertEqual(block["hookEventName"], "SessionStart")
        context = block["additionalContext"]
        # 与纯 recovery 渲染逐字节一致（零噪音回归红线）
        self.assertEqual(
            context,
            recovery.render_resume_context(
                recovery.build_recovery_summary(self.repo)))
        self.assertTrue(context.startswith(recovery.RESUME_HEADER))
        self.assertIn("Task: rec-active-1a", context)
        self.assertIn("Next action: ", context)

    def test_legacy_task_visible_through_hook(self):
        write_raw_state(self.repo, "legacy-23-task",
                        legacy_v23_payload("legacy-23-task"))
        result = run_hook("{}", self.repo)
        self.assertEqual(result.returncode, 0, result.stderr)
        context = parse_single_line_json(result.stdout)[
            "hookSpecificOutput"]["additionalContext"]
        self.assertIn("Legacy task legacy-23-task:", context)
        self.assertIn("explicitly retire legacy task", context)


if __name__ == "__main__":
    unittest.main()
