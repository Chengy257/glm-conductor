#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hooks.pre_tool_use 子进程冒烟测试（v2 Ownership Gate Layer B 注入钩子）。

以子进程方式运行 plugins/glm-conductor/hooks/pre_tool_use.py，验证其
advisory fail-open 契约的可观测行为：
  - 存在声明 ownership.files（非空）的活动任务：stdout 输出单行 JSON
    hookSpecificOutput（hookEventName=="PreToolUse"，additionalContext
    含 task_id、ownership 路径与 Layer A 兜底警告），stderr 恒空，exit 0；
  - 无活动任务 / 活动任务未声明 ownership / 任务已终态：静默放行
    （exit 0，stdout 与 stderr 均空）；
  - stdin 空串 / 非法 JSON：按空对象容错，照常注入并 exit 0；
  - 多任务：逐任务一段，最多列 3 个并注明省略；
  - hooks.json：Stop 与 PreToolUse 条目并存，matcher 为 "Agent|Task"。

仅 Python 3 标准库（unittest + subprocess + tempfile），零第三方依赖；
被检仓库目录由 tempfile.TemporaryDirectory 提供，不污染真实工作区；
状态 fixture 经 sys.path 接插件根后用 runtime.state 构造并保存。

运行：
    cd <repo_root> && python3 -m unittest tests.test_pre_tool_use -v
"""

import sys, unittest
import json, os, subprocess, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "glm-conductor"))
from runtime import state

# 被测钩子脚本：仓库根 plugins/glm-conductor/hooks/pre_tool_use.py
PRE_TOOL_USE = (Path(__file__).resolve().parents[1]
                / "plugins/glm-conductor/hooks/pre_tool_use.py")

# 被测钩子清单：plugins/glm-conductor/hooks/hooks.json（Stop + PreToolUse）
HOOKS_JSON = (Path(__file__).resolve().parents[1]
              / "plugins/glm-conductor/hooks/hooks.json")

TID = "demo-task-1a2b3c"
ROUTE = {"mode": "delegate", "delegability": "high", "assurance": "standard",
         "executor": "flash-implementer", "continuity": "foreground"}
OWNED = ["plugins/glm-conductor/hooks/pre_tool_use.py",
         "tests/test_pre_tool_use.py"]


def run_hook(stdin_text, project_dir):
    """以子进程运行 pre_tool_use.py，返回 CompletedProcess。

    text=True：Windows 下 stdin/stdout/stderr 按文本模式收发；
    ZCODE_PROJECT_DIR 指向被检仓库（钩子据此发现活动任务），其余环境变量
    原样继承。
    """
    return subprocess.run(
        [sys.executable, str(PRE_TOOL_USE)],
        input=stdin_text, text=True, capture_output=True,
        env=dict(os.environ, ZCODE_PROJECT_DIR=str(project_dir)))


def save_task(repo, task_id=TID, ownership_files=OWNED, status="executing"):
    """在临时仓库构造一个任务状态文件（runtime.state 构造 + 校验原子保存）。"""
    active = state.new_task_state(
        task_id, "Layer B 注入冒烟测试目标", dict(ROUTE),
        ownership_files=ownership_files, status=status)
    state.save_state(repo, active)


def parse_single_line_json(text):
    """把 hook stdout 按单行 JSON 解析并返回 dict（多行 / 非法即断言失败）。"""
    stripped = text.strip()
    if "\n" in stripped:
        raise AssertionError("stdout 不是单行 JSON: %r" % text)
    return json.loads(stripped)


class OwnershipInjectionTest(unittest.TestCase):
    """存在声明 ownership 的活动任务：注入 additionalContext 后放行。"""

    def test_active_task_with_ownership_injects_reminder(self):
        with tempfile.TemporaryDirectory() as tmp:
            save_task(tmp)
            result = run_hook(
                '{"tool_name":"Agent","tool_input":{"prompt":"do it"}}', tmp)
            self.assertEqual(result.returncode, 0)
            # advisory 层成功路径也不写 stderr
            self.assertEqual(result.stderr, "")
            payload = parse_single_line_json(result.stdout)
            # Zod 严格校验形态：顶层仅 hookSpecificOutput，内层仅两键
            self.assertEqual(sorted(payload.keys()), ["hookSpecificOutput"])
            out = payload["hookSpecificOutput"]
            self.assertEqual(sorted(out.keys()),
                             ["additionalContext", "hookEventName"])
            self.assertEqual(out["hookEventName"], "PreToolUse")
            ctx = out["additionalContext"]
            self.assertIn("OWNERSHIP REMINDER (Layer B)", ctx)
            self.assertIn(TID, ctx)
            for owned in OWNED:
                self.assertIn(owned, ctx)
            self.assertIn("Layer A", ctx)
            self.assertIn("Layer B", ctx)


class SilentPassTest(unittest.TestCase):
    """无注入对象：静默放行（exit 0，stdout 与 stderr 均空）。"""

    def test_empty_repo_exit0_silent(self):
        # 无活动任务（空 tempdir）：普通派发零干预
        with tempfile.TemporaryDirectory() as tmp:
            result = run_hook("{}", tmp)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "")
            self.assertEqual(result.stderr, "")

    def test_active_task_without_ownership_silent(self):
        # 活动任务存在但 ownership.files 为空 → 不注入
        with tempfile.TemporaryDirectory() as tmp:
            save_task(tmp, ownership_files=[])
            result = run_hook("{}", tmp)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "")
            self.assertEqual(result.stderr, "")

    def test_terminal_task_not_injected(self):
        # 终态任务（completed）不算活动任务 → 不注入
        with tempfile.TemporaryDirectory() as tmp:
            save_task(tmp, status="completed")
            result = run_hook("{}", tmp)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "")
            self.assertEqual(result.stderr, "")


class StdinToleranceTest(unittest.TestCase):
    """stdin 容错：空串 / 非法 JSON 一律按空对象处理，照常注入并 exit 0。"""

    def test_empty_and_invalid_json_stdin_exit0(self):
        with tempfile.TemporaryDirectory() as tmp:
            save_task(tmp)
            for raw in ("", "not-json"):
                with self.subTest(stdin=repr(raw)):
                    result = run_hook(raw, tmp)
                    self.assertEqual(result.returncode, 0)
                    payload = parse_single_line_json(result.stdout)
                    self.assertEqual(
                        payload["hookSpecificOutput"]["hookEventName"],
                        "PreToolUse")


class MaxInjectedTasksTest(unittest.TestCase):
    """多任务：逐任务一段，最多列 3 个并注明省略。"""

    def test_five_tasks_only_three_listed_with_omission_note(self):
        ids = ["task-a-%d" % i for i in range(5)]
        with tempfile.TemporaryDirectory() as tmp:
            for tid in ids:
                save_task(tmp, task_id=tid)
            result = run_hook("{}", tmp)
            self.assertEqual(result.returncode, 0)
            ctx = parse_single_line_json(result.stdout)[
                "hookSpecificOutput"]["additionalContext"]
            # 前 3 个（目录名序）逐任务一段列出
            self.assertEqual(ctx.count("declares ownership:"), 3)
            for tid in ids[:3]:
                self.assertIn(tid, ctx)
            # 其余不出现，且注明省略数量
            for tid in ids[3:]:
                self.assertNotIn(tid, ctx)
            self.assertIn("(2 more active task(s) with declared ownership "
                          "omitted)", ctx)


class HooksManifestTest(unittest.TestCase):
    """hooks.json：PreToolUse 条目增补后与 Stop 条目并存、matcher 正确。"""

    def test_stop_and_pretooluse_coexist(self):
        with open(str(HOOKS_JSON), "r", encoding="utf-8") as fh:
            manifest = json.load(fh)
        hooks = manifest["hooks"]
        # Stop 条目仍在，且仍指向 stop_gate.py（逐字节未动的行为佐证）
        self.assertIn("Stop", hooks)
        self.assertIn("stop_gate.py", json.dumps(hooks["Stop"]))
        # PreToolUse 条目存在且 matcher 精确为 Agent|Task
        self.assertIn("PreToolUse", hooks)
        entries = hooks["PreToolUse"]
        self.assertIsInstance(entries, list)
        matchers = [entry.get("matcher") for entry in entries
                    if isinstance(entry, dict)]
        self.assertIn("Agent|Task", matchers)
        # 内层 hook args 指向 pre_tool_use.py
        pre_args = [arg for entry in entries
                    for hook in entry.get("hooks", [])
                    for arg in hook.get("args", [])]
        self.assertTrue(
            any(arg.endswith("pre_tool_use.py") for arg in pre_args),
            "PreToolUse 条目应指向 pre_tool_use.py: %r" % pre_args)


if __name__ == "__main__":
    unittest.main()
