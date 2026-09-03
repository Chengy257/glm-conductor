#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""runtime.recovery + hooks.session_start 测试（v2.1 M3 wu-21-05）。

覆盖三面（计划 §8 SessionStart Recovery）：

recovery.build_recovery_summary（摘要冻结形状）：
  - 空仓库 → {"tasks": [], "corrupt": []}；
  - 单活动任务条目八字段齐全（task_id / goal 截断 120 / status /
    repo_root / completed_units / incomplete_units / interrupted_units /
    auto_resume），repo_root 无绑定为 None、绑定为归一根；
  - 多任务按 task_id 排序；legacy 状态（无 execution_policy 块）→
    auto_resume 兜底 "manual"；execution_policy.continuity.auto_resume
    非默认值（notify）透传；
  - 混合单元状态：completed_units 只收 completed；incomplete 收全部
    非 completed；interrupted 恰为 running/waiting_quota/blocked，且
    无 agent run 记录时 run_lifecycle 为 None、有记录时为 lifecycle
    dict（launch_count 计数）；
  - corrupt 任务（state.json 损坏）列入 summary["corrupt"]。

recovery.render_resume_context（英文模板）：
  - 无任务 → ""；头部逐字 "GLM CONDUCTOR RESUME CONTEXT"；
  - 逐任务段（Task / Repository / Goal / Status / Completed units /
    Interrupted units / Waiting / Quota-resume authorization）与尾部
    Recommended 编号 1-4 逐字短语；
  - possibly_zombie=True 的 interrupted 单元带逐字僵尸标注；
  - 任务超 3 只列前 3 + omitted 行；单元列表超 8 截断 + omitted 计数；
  - corrupt 非空 → 末尾单行 WARNING。

hooks.session_start（子进程直调，同 test_pre_tool_use 模式——钩子
快照不热加载）：
  - 有活动任务 → exit 0、stderr 空、stdout 单行 JSON
    hookSpecificOutput（hookEventName=="SessionStart"，
    additionalContext 含 RESUME 头与 task_id）；
  - 无活动任务（空仓库 / 仅终态任务）→ 恒静默（exit 0，stdout/stderr
    均空）；
  - stdin 空串 / 非法 JSON → 容错照常注入。

hooks.json：SessionStart 条目（matcher "startup|clear|compact"、
process / python3 / timeoutMs 5000 / session_start.py）与既有条目并存。

仅 Python 3 标准库（unittest + subprocess + tempfile），零第三方依赖；
被检仓库目录由 tempfile.TemporaryDirectory 提供，不污染真实工作区；
状态 fixture 经 sys.path 接插件根后用 runtime.state 构造并原子保存。

运行：
    cd <repo_root> && python3 -m unittest tests.test_recovery -v
"""

import sys, unittest
import json, os, subprocess, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "glm-conductor"))
from runtime import journal, recovery, state

# 被测钩子脚本：仓库根 plugins/glm-conductor/hooks/session_start.py
SESSION_START = (Path(__file__).resolve().parents[1]
                 / "plugins/glm-conductor/hooks/session_start.py")

# 被测钩子清单：plugins/glm-conductor/hooks/hooks.json
HOOKS_JSON = (Path(__file__).resolve().parents[1]
              / "plugins/glm-conductor/hooks/hooks.json")

TID = "recovery-demo-1a2b3c"
# 矩阵合法的 solo 路由基座（与 test_pre_tool_use 同款）
ROUTE = {"mode": "solo", "delegability": "low", "assurance": "standard",
         "executor": "main", "continuity": "foreground"}
LONG_GOAL = "g" * 200
ZOMBIE_NOTE = recovery.ZOMBIE_NOTE


def make_unit(uid, status):
    """构造一个通过 §61 契约校验的最小 work unit dict。"""
    return {"id": uid, "objective": "fixture unit %s" % uid,
            "status": status, "depends_on": [], "executor": "main",
            "ownership": ["src/a.py"],
            "verification": ["python3 -m unittest -h"]}


def save_task(repo, task_id=TID, status="executing", units=(), goal="恢复冒烟测试目标",
              execution_policy=None, repository_root=None):
    """在临时仓库构造一个任务状态文件（runtime.state 构造 + 原子保存）。

    execution_policy 显式给定整体替换（构造默认块之后的自定义，如
    auto_resume=notify）；repository_root 非 None 时构造期绑定任务
    专属仓库根（RB-2）。
    """
    active = state.new_task_state(
        task_id, goal, dict(ROUTE), ownership_files=["src/a.py"],
        verification_required=["python3 -m unittest -h"], status=status,
        repository_root=repository_root)
    if execution_policy is not None:
        active["execution_policy"] = execution_policy
    active["work_units"] = list(units)
    state.save_state(repo, active)
    return active


def run_hook(stdin_text, project_dir):
    """以子进程运行 session_start.py，返回 CompletedProcess。

    text=True + 显式 encoding="utf-8"：钩子按运行时契约恒以 UTF-8 字节
    写 stdout/stderr，父进程必须按 UTF-8 解码（不指定 encoding 时按父
    进程 locale 严格解码，中文/非 ASCII 字节会 UnicodeDecodeError，
    CI Windows 矩阵实测）；errors="replace" 兜底异常字节。
    ZCODE_PROJECT_DIR 指向被检仓库（钩子据此发现活动任务）。
    """
    return subprocess.run(
        [sys.executable, str(SESSION_START)],
        input=stdin_text, text=True, capture_output=True,
        encoding="utf-8", errors="replace",
        env=dict(os.environ, ZCODE_PROJECT_DIR=str(project_dir)))


def parse_single_line_json(text):
    """把 hook stdout 按单行 JSON 解析并返回 dict（多行 / 非法即断言失败）。"""
    stripped = text.strip()
    if "\n" in stripped:
        raise AssertionError("stdout 不是单行 JSON: %r" % text)
    return json.loads(stripped)


class BuildSummaryEmptyTest(unittest.TestCase):
    """空仓库 / 无活动任务：摘要两键全空。"""

    def test_empty_repo_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(recovery.build_recovery_summary(tmp),
                             {"tasks": [], "corrupt": []})

    def test_terminal_only_repo_no_active_tasks(self):
        # 全终态（completed）任务不算未完成任务 → tasks 空
        with tempfile.TemporaryDirectory() as tmp:
            st = save_task(tmp, status="finalizing")
            st["status"] = "finalizing"
            state.save_state(tmp, st)
            state.commit_completion(tmp, TID)
            summary = recovery.build_recovery_summary(tmp)
            self.assertEqual(summary["tasks"], [])
            self.assertEqual(summary["corrupt"], [])


class BuildSummaryShapeTest(unittest.TestCase):
    """摘要条目冻结形状（八字段）与 goal / repo_root / 排序语义。"""

    def test_task_entry_frozen_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            save_task(tmp)
            summary = recovery.build_recovery_summary(tmp)
            self.assertEqual(sorted(summary.keys()), ["corrupt", "tasks"])
            self.assertEqual(len(summary["tasks"]), 1)
            entry = summary["tasks"][0]
            self.assertEqual(sorted(entry.keys()), [
                "auto_resume", "completed_units", "goal",
                "incomplete_units", "interrupted_units", "repo_root",
                "status", "task_id"])
            self.assertEqual(entry["task_id"], TID)
            self.assertEqual(entry["status"], "executing")
            # 无 repository 绑定（legacy 形态）→ repo_root 为 None
            self.assertIsNone(entry["repo_root"])
            # 构造默认块的 continuity.auto_resume == "manual"
            self.assertEqual(entry["auto_resume"], "manual")

    def test_goal_truncated_to_120(self):
        with tempfile.TemporaryDirectory() as tmp:
            save_task(tmp, goal=LONG_GOAL)
            entry = recovery.build_recovery_summary(tmp)["tasks"][0]
            self.assertEqual(entry["goal"], "g" * 120)

    def test_multi_task_sorted_by_task_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            save_task(tmp, task_id="recovery-b-2")
            save_task(tmp, task_id="recovery-a-1")
            summary = recovery.build_recovery_summary(tmp)
            self.assertEqual([entry["task_id"] for entry in summary["tasks"]],
                             ["recovery-a-1", "recovery-b-2"])

    def test_legacy_state_without_execution_policy_manual(self):
        # legacy 状态（无 execution_policy 块）完全合法 → auto_resume 兜底 manual
        with tempfile.TemporaryDirectory() as tmp:
            save_task(tmp)
            st = state.load_state(tmp, TID)
            del st["execution_policy"]
            state.save_state(tmp, st)
            entry = recovery.build_recovery_summary(tmp)["tasks"][0]
            self.assertEqual(entry["auto_resume"], "manual")

    def test_execution_policy_auto_resume_propagated(self):
        # 授权事实源读取：notify + max_quota_windows=0 为合法块
        with tempfile.TemporaryDirectory() as tmp:
            save_task(tmp)
            st = state.load_state(tmp, TID)
            st["execution_policy"]["continuity"]["auto_resume"] = "notify"
            st["execution_policy"]["continuity"]["max_quota_windows"] = 0
            state.save_state(tmp, st)
            entry = recovery.build_recovery_summary(tmp)["tasks"][0]
            self.assertEqual(entry["auto_resume"], "notify")

    def test_bound_repository_root_resolved(self):
        with tempfile.TemporaryDirectory() as tmp:
            save_task(tmp, repository_root=tmp)
            entry = recovery.build_recovery_summary(tmp)["tasks"][0]
            self.assertEqual(entry["repo_root"], str(Path(tmp).resolve()))


class BuildSummaryUnitsTest(unittest.TestCase):
    """单元三分（completed / incomplete / interrupted）与僵尸账本接线。"""

    def test_mixed_unit_statuses_classification(self):
        units = [make_unit("wu-c1", "completed"), make_unit("wu-c2", "completed"),
                 make_unit("wu-r", "running"), make_unit("wu-p", "pending"),
                 make_unit("wu-q", "waiting_quota"), make_unit("wu-b", "blocked"),
                 make_unit("wu-f", "failed")]
        with tempfile.TemporaryDirectory() as tmp:
            save_task(tmp, units=units)
            entry = recovery.build_recovery_summary(tmp)["tasks"][0]
            self.assertEqual(entry["completed_units"], ["wu-c1", "wu-c2"])
            self.assertEqual(
                entry["incomplete_units"],
                ["wu-r", "wu-p", "wu-q", "wu-b", "wu-f"])
            # interrupted 恰为 running/waiting_quota/blocked 三类
            self.assertEqual([item["id"] for item in entry["interrupted_units"]],
                             ["wu-r", "wu-q", "wu-b"])
            for item in entry["interrupted_units"]:
                self.assertEqual(sorted(item.keys()), ["id", "run_lifecycle"])

    def test_interrupted_without_runs_lifecycle_none(self):
        # 被中断单元无任何 agent run 记录 → run_lifecycle 为 None
        with tempfile.TemporaryDirectory() as tmp:
            save_task(tmp, units=[make_unit("wu-r", "running")])
            entry = recovery.build_recovery_summary(tmp)["tasks"][0]
            self.assertIsNone(entry["interrupted_units"][0]["run_lifecycle"])

    def test_interrupted_with_runs_lifecycle_dict(self):
        # journal 有 agent_launched 记录 → run_lifecycle 为 dict（launch 计数）
        with tempfile.TemporaryDirectory() as tmp:
            save_task(tmp, units=[make_unit("wu-r", "running")])
            journal.append_event(tmp, TID, {
                "event": "agent_launched", "unit": "wu-r",
                "permit_id": "dp-1", "tool_use_id": "tu-1",
                "agent_id": "agent-x", "execution_mode": "background"})
            entry = recovery.build_recovery_summary(tmp)["tasks"][0]
            lifecycle = entry["interrupted_units"][0]["run_lifecycle"]
            self.assertIsInstance(lifecycle, dict)
            self.assertEqual(lifecycle["unit"], "wu-r")
            self.assertEqual(lifecycle["launch_count"], 1)
            self.assertIn("possibly_zombie", lifecycle)


class BuildSummaryCorruptTest(unittest.TestCase):
    """corrupt 任务显式列入 summary["corrupt"]（不静默消失）。"""

    def test_corrupt_task_listed(self):
        with tempfile.TemporaryDirectory() as tmp:
            save_task(tmp)
            bad_dir = state.task_dir(tmp, "corrupt-task-9z8y7x")
            bad_dir.mkdir(parents=True)
            with open(str(bad_dir / "state.json"), "w",
                      encoding="utf-8") as fh:
                fh.write("{not-json")
            summary = recovery.build_recovery_summary(tmp)
            self.assertEqual(summary["corrupt"], ["corrupt-task-9z8y7x"])
            self.assertEqual([entry["task_id"] for entry in summary["tasks"]],
                             [TID])


class RenderResumeContextTest(unittest.TestCase):
    """英文模板渲染：头部逐字、逐段形态、截断、僵尸标注、空返回。"""

    def _summary(self, tmp, **kwargs):
        save_task(tmp, **kwargs)
        return recovery.build_recovery_summary(tmp)

    def test_empty_summary_renders_empty_string(self):
        with tempfile.TemporaryDirectory() as tmp:
            summary = recovery.build_recovery_summary(tmp)
            self.assertEqual(recovery.render_resume_context(summary), "")
        # 空形状容错（非 dict / 缺键）
        self.assertEqual(recovery.render_resume_context({}), "")
        self.assertEqual(recovery.render_resume_context(None), "")

    def test_header_verbatim_and_sections(self):
        with tempfile.TemporaryDirectory() as tmp:
            text = recovery.render_resume_context(self._summary(tmp))
        self.assertTrue(text.startswith("GLM CONDUCTOR RESUME CONTEXT"))
        for marker in ("Task: %s" % TID, "Repository: not bound",
                       "Status: executing", "Completed units:",
                       "Interrupted units:", "Waiting:",
                       "Quota-resume authorization: manual"):
            self.assertIn(marker, text)
        # Recommended 编号 1-4 逐字冻结
        for item in ("1. reconcile interrupted agent runs",
                     "2. inspect repository residue",
                     "3. retrieve prior progress if needed",
                     "4. continue via prepare_dispatch"):
            self.assertIn(item, text)

    def test_interrupted_unit_listed_and_waiting_excludes_it(self):
        units = [make_unit("wu-done", "completed"),
                 make_unit("wu-run", "running"),
                 make_unit("wu-wait", "pending")]
        with tempfile.TemporaryDirectory() as tmp:
            text = recovery.render_resume_context(
                self._summary(tmp, units=units))
        self.assertIn("  wu-done", text)
        self.assertIn("  wu-run", text)
        self.assertIn("  wu-wait", text)
        # interrupted 段只含 wu-run；Waiting 段含 wu-wait 不含 wu-run
        interrupted_block = text.split("Interrupted units:")[1]
        interrupted_block = interrupted_block.split("Waiting:")[0]
        self.assertIn("wu-run", interrupted_block)
        self.assertNotIn("wu-wait", interrupted_block)
        waiting_block = text.split("Waiting:")[1]
        waiting_block = waiting_block.split("Quota-resume")[0]
        self.assertIn("wu-wait", waiting_block)
        self.assertNotIn("wu-run", waiting_block)

    def test_zombie_annotation_rendered(self):
        # render 是纯函数：手工构造 possibly_zombie=True 的摘要验证标注
        summary = {"tasks": [{
            "task_id": "zombie-task-1a2b3c", "goal": "g", "status": "executing",
            "repo_root": None, "completed_units": [],
            "incomplete_units": ["wu-z"],
            "interrupted_units": [{"id": "wu-z", "run_lifecycle": {
                "unit": "wu-z", "runs": [], "launch_count": 1,
                "failure_count": 0, "last_agent_id": "a-1",
                "native": {"observed_status": "running"},
                "possibly_zombie": True, "archived_terminal": False}}],
            "auto_resume": "manual"}], "corrupt": []}
        text = recovery.render_resume_context(summary)
        self.assertIn(ZOMBIE_NOTE, text)
        self.assertIn("wu-z", text)

    def test_more_than_three_tasks_only_first_three(self):
        ids = ["recovery-task-%d" % i for i in range(5)]
        with tempfile.TemporaryDirectory() as tmp:
            for tid in ids:
                save_task(tmp, task_id=tid)
            text = recovery.render_resume_context(
                recovery.build_recovery_summary(tmp))
        for tid in ids[:3]:
            self.assertIn("Task: %s" % tid, text)
        for tid in ids[3:]:
            self.assertNotIn(tid, text)
        self.assertIn("(2 more task(s) omitted)", text)

    def test_unit_list_truncated_to_eight_with_omitted_count(self):
        units = [make_unit("wu-c-%02d" % i, "completed") for i in range(10)]
        with tempfile.TemporaryDirectory() as tmp:
            text = recovery.render_resume_context(
                self._summary(tmp, units=units))
        for i in range(8):
            self.assertIn("wu-c-%02d" % i, text)
        for i in range(8, 10):
            self.assertNotIn("wu-c-%02d" % i, text)
        self.assertIn("(2 more completed units omitted)", text)

    def test_corrupt_warning_single_line_at_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            save_task(tmp)
            bad_dir = state.task_dir(tmp, "corrupt-task-9z8y7x")
            bad_dir.mkdir(parents=True)
            with open(str(bad_dir / "state.json"), "w",
                      encoding="utf-8") as fh:
                fh.write("not json")
            text = recovery.render_resume_context(
                recovery.build_recovery_summary(tmp))
        lines = text.split("\n")
        warning_lines = [line for line in lines if "corrupt task state" in line]
        self.assertEqual(len(warning_lines), 1)
        self.assertTrue(lines[-1].startswith("WARNING:"))
        self.assertIn("corrupt-task-9z8y7x", lines[-1])


class SessionStartHookTest(unittest.TestCase):
    """session_start.py 子进程契约（fail-open + 单行 JSON + 恒静默）。"""

    def test_active_task_emits_resume_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            save_task(tmp, units=[make_unit("wu-run", "running")])
            result = run_hook(
                json.dumps({"session_id": "s-1", "source": "startup"}), tmp)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "")
            payload = parse_single_line_json(result.stdout)
            # Zod 严格校验形态：顶层仅 hookSpecificOutput，内层仅两键
            self.assertEqual(sorted(payload.keys()), ["hookSpecificOutput"])
            block = payload["hookSpecificOutput"]
            self.assertEqual(sorted(block.keys()),
                             ["additionalContext", "hookEventName"])
            self.assertEqual(block["hookEventName"], "SessionStart")
            ctx = block["additionalContext"]
            self.assertIn("GLM CONDUCTOR RESUME CONTEXT", ctx)
            self.assertIn(TID, ctx)
            self.assertIn("Recommended:", ctx)

    def test_no_active_task_completely_silent(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = run_hook("{}", tmp)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "")
            self.assertEqual(result.stderr, "")

    def test_terminal_task_completely_silent(self):
        with tempfile.TemporaryDirectory() as tmp:
            st = save_task(tmp, status="finalizing")
            state.commit_completion(tmp, TID)
            del st
            result = run_hook("{}", tmp)
            self.assertEqual((result.returncode, result.stdout, result.stderr),
                             (0, "", ""))

    def test_empty_and_invalid_stdin_tolerated(self):
        # stdin 空串 / 非法 JSON 不影响恢复注入（事件形态兼容）
        with tempfile.TemporaryDirectory() as tmp:
            save_task(tmp)
            for raw in ("", "not-json"):
                with self.subTest(stdin=repr(raw)):
                    result = run_hook(raw, tmp)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    block = parse_single_line_json(
                        result.stdout)["hookSpecificOutput"]
                    self.assertEqual(block["hookEventName"], "SessionStart")
                    self.assertIn("RESUME CONTEXT",
                                  block["additionalContext"])


class HooksManifestTest(unittest.TestCase):
    """hooks.json：SessionStart 条目与既有条目并存、matcher 与脚本正确。"""

    def test_session_start_entry(self):
        with open(str(HOOKS_JSON), "r", encoding="utf-8") as fh:
            manifest = json.load(fh)
        hooks = manifest["hooks"]
        self.assertIn("SessionStart", hooks)
        entries = hooks["SessionStart"]
        self.assertIsInstance(entries, list)
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry.get("matcher"), "startup|clear|compact")
        hook = entry["hooks"][0]
        self.assertEqual(hook["type"], "process")
        self.assertEqual(hook["command"], "python3")
        self.assertEqual(hook["timeoutMs"], 5000)
        self.assertEqual(
            hook["args"], ["${ZCODE_PLUGIN_ROOT}/hooks/session_start.py"])

    def test_existing_entries_coexist(self):
        # wu-21-03 的既有条目不回退（Stop / PreToolUse×3 / PostToolUse /
        # PostToolUseFailure 并存）；v2.2 C6（wu-22-C6）PreToolUse 追加
        # CronCreate 第三项——既有两条的相对序与内容零改动（additive，
        # hooks.json +3 条目之一）
        with open(str(HOOKS_JSON), "r", encoding="utf-8") as fh:
            hooks = json.load(fh)["hooks"]
        for event in ("Stop", "PreToolUse", "PostToolUse",
                      "PostToolUseFailure"):
            self.assertIn(event, hooks)
        matchers = [entry.get("matcher") for entry in hooks["PreToolUse"]]
        self.assertEqual(matchers, ["Agent|Task", "Bash", "CronCreate"])


if __name__ == "__main__":
    unittest.main()
