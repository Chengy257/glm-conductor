#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hooks.stop_gate 子进程冒烟测试（v2 强制层 Stop 完成门骨架）。

以子进程方式运行 plugins/glm-conductor/hooks/stop_gate.py，验证其
fail-open 契约的可观测行为：
  - 空仓库目录（无活动任务）：静默放行（退出码 0，无任何输出）；
  - 存在活动任务：stderr 输出 ENFORCEMENT SKELETON 观测报告（含任务 ID）
    后放行，stdout 恒为空；
  - stdin 为空串 / 非法 JSON：按空对象容错，照常退出 0。

仅 Python 3 标准库（unittest + subprocess + tempfile），零第三方依赖；
被检仓库目录由 tempfile.TemporaryDirectory 提供，不污染真实工作区。

运行：
    cd <repo_root> && python3 -m unittest tests.test_stop_gate -v
"""

import sys, unittest
import os, subprocess, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "glm-conductor"))
from runtime import state

# 被测钩子脚本：仓库根 plugins/glm-conductor/hooks/stop_gate.py
STOP_GATE = (Path(__file__).resolve().parents[1]
             / "plugins/glm-conductor/hooks/stop_gate.py")

TID = "demo-task-1a2b3c"
ROUTE = {"mode": "delegate", "delegability": "high", "assurance": "standard",
         "executor": "flash-implementer", "continuity": "foreground"}


def run_gate(stdin_text, project_dir):
    """以子进程运行 stop_gate.py，返回 CompletedProcess。

    text=True：Windows 下 stdin/stdout/stderr 按文本模式收发；
    ZCODE_PROJECT_DIR 指向被检仓库（钩子据此发现活动任务），其余环境变量
    原样继承。
    """
    return subprocess.run(
        [sys.executable, str(STOP_GATE)],
        input=stdin_text, text=True, capture_output=True,
        env=dict(os.environ, ZCODE_PROJECT_DIR=str(project_dir)))


class StopGateSilentPassTest(unittest.TestCase):
    """无活动任务：静默放行。"""

    def test_empty_repo_exit0_silent_stdout(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = run_gate("{}", tmp)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "")
            self.assertEqual(result.stderr, "")


class StopGateActiveTaskReportTest(unittest.TestCase):
    """存在活动任务：stderr 观测报告后仍放行。"""

    def test_active_task_reported_on_stderr_then_pass(self):
        # sys.path 已接插件根（文件头），用 runtime.state 构造一个活动任务
        with tempfile.TemporaryDirectory() as tmp:
            active = state.new_task_state(
                TID, "完成门冒烟测试目标", dict(ROUTE), status="executing")
            state.save_state(tmp, active)
            result = run_gate("{}", tmp)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "")
            self.assertIn("ENFORCEMENT SKELETON", result.stderr)
            self.assertIn(TID, result.stderr)


class StopGateStdinToleranceTest(unittest.TestCase):
    """stdin 容错：空串 / 非法 JSON 一律按空对象处理。"""

    def test_empty_and_invalid_json_stdin_exit0(self):
        with tempfile.TemporaryDirectory() as tmp:
            for raw in ("", "not-json"):
                with self.subTest(stdin=repr(raw)):
                    result = run_gate(raw, tmp)
                    self.assertEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
