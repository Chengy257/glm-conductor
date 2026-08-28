#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hooks.stop_gate 子进程冒烟测试（v2 强制层 Stop 完成门 Layer A）。

以子进程方式运行 plugins/glm-conductor/hooks/stop_gate.py，验证其
Layer A 完成门与 fail-open 契约的可观测行为：
  - 无活动任务：静默放行（退出码 0，无任何输出）；
  - 活动任务无越界（或未声明 ownership）：静默放行（骨架观测报文已废止）；
  - 声明了 ownership 的任务存在越界改动：stdout 单行
    {"decision":"block",...} 拦截 + journal 记 gate_blocked；
  - journal 尾部连续 gate_blocked 达上限：放行 + stderr 报
    ENFORCEMENT GATE EXHAUSTED + journal 记 gate_exhausted；
    链中出现其他事件即断链重新计数；
  - git 失败 / 非 git 仓库：降级放行（stderr ENFORCEMENT DEGRADED）
    + journal 记 gate_degraded；
  - stop_hook_active=true（续行循环中）：照常校验（续行额度靠每次
    Stop 都校验来消费；行为与普通 Stop 相同）；
  - stdin 为空串 / 非法 JSON：按空对象容错，照常退出 0。

仅 Python 3 标准库（unittest + subprocess + tempfile），零第三方依赖；
git fixture 做法（git init + config + commit、Windows 下 .git 只读位
清理）对齐 tests/test_ownership.py；环境无 git 可执行时 git 场景自动
skipTest。被检仓库目录由 tempfile.TemporaryDirectory 提供，不污染真实
工作区。

运行：
    cd <repo_root> && python3 -m unittest tests.test_stop_gate -v
"""

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "glm-conductor"))
from runtime import journal as journal_mod
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
    原样继承。断言只用 ASCII 子串，规避 locale 解码差异。
    """
    return subprocess.run(
        [sys.executable, str(STOP_GATE)],
        input=stdin_text, text=True, capture_output=True,
        env=dict(os.environ, ZCODE_PROJECT_DIR=str(project_dir)))


def run_git(repo, *args):
    """在 fixture 仓库里执行 git 子命令（测试装置专用，失败即断言错误）。"""
    proc = subprocess.run(
        ["git"] + list(args), cwd=str(repo),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        raise AssertionError(
            "测试装置 git %s 失败（returncode=%d）：%s"
            % (" ".join(args), proc.returncode,
               proc.stderr.decode("utf-8", errors="replace")))
    return proc.stdout.decode("utf-8", errors="replace")


class TempDirFixture(unittest.TestCase):
    """tempdir 基座：目录/状态文件写入 + Windows .git 只读位清理。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._force_cleanup)
        self.repo = Path(self._tmp.name)

    def _force_cleanup(self):
        """解除 git 只读对象后清理临时目录。

        Windows 上 git 松散对象文件带只读属性，TemporaryDirectory.cleanup()
        的 rmtree 会 PermissionError；先遍历 .git 清掉只读位再删除
        （模式复用 tests/test_ownership.py）。
        """
        git_dir = os.path.join(self._tmp.name, ".git")
        if os.path.isdir(git_dir):
            for dirpath, _dirnames, filenames in os.walk(git_dir):
                for name in filenames:
                    try:
                        os.chmod(os.path.join(dirpath, name), stat.S_IWRITE)
                    except OSError:
                        pass
        self._tmp.cleanup()

    def write(self, rel, data):
        """在 fixture 仓库内写一个文件（自动建父目录）。"""
        target = self.repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    def add_active_task(self, ownership_files=(), status="executing",
                        task_id=TID):
        """写入一个活动任务 state.json，返回 task_id。"""
        state.save_state(self.repo, state.new_task_state(
            task_id, "完成门 Layer A 冒烟测试目标", dict(ROUTE),
            ownership_files=ownership_files, status=status))
        return task_id

    def journal_events(self, task_id=TID):
        """读取任务 journal 全部事件（无文件返回 []）。"""
        return journal_mod.read_events(self.repo, task_id)


class GitRepoFixture(TempDirFixture):
    """tempdir 内真实 git 仓库基座（Layer A 越界判定场景）。"""

    def setUp(self):
        super().setUp()
        run_git(self.repo, "init")
        run_git(self.repo, "config", "user.email", "gate@example.com")
        run_git(self.repo, "config", "user.name", "Stop Gate")
        # 固定换行行为，避免全局 autocrlf 干扰状态判定
        run_git(self.repo, "config", "core.autocrlf", "false")
        # 运行时目录（state.json / events.jsonl）不入库：否则它们作为
        # 未跟踪文件混入 touched 清单，污染越界判定断言
        self.write(".gitignore", b".glm-conductor/\n")
        self.write("base.txt", b"v1\n")
        run_git(self.repo, "add", ".")
        run_git(self.repo, "commit", "-m", "init")


# —— 骨架契约保留（放行路径） ——

class StopGateSilentPassTest(TempDirFixture):
    """无活动任务：静默放行。"""

    def test_empty_repo_exit0_silent_stdout(self):
        result = run_gate("{}", self.repo)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")


class StopGateActiveTaskSilentPassTest(GitRepoFixture):
    """存在活动任务且无越界（未声明 ownership）：静默放行。

    v2 alpha1 骨架此时向 stderr 报 ENFORCEMENT SKELETON；Layer A 起该
    观测报文废止——通过时 stdout/stderr 全空。注意 fixture 须是 git 仓库：
    非 git 目录会在 touched 清单一步降级（stderr 非空），不构成本场景。
    """

    def test_active_task_passes_silently_without_ownership(self):
        self.add_active_task()  # 未声明 ownership（ownership.files 为空）
        result = run_gate("{}", self.repo)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")


class StopGateStdinToleranceTest(TempDirFixture):
    """stdin 容错：空串 / 非法 JSON 一律按空对象处理。"""

    def test_empty_and_invalid_json_stdin_exit0(self):
        for raw in ("", "not-json"):
            with self.subTest(stdin=repr(raw)):
                result = run_gate(raw, self.repo)
                self.assertEqual(result.returncode, 0)


# —— Layer A 强制（越界 block / 放行静默） ——

@unittest.skipUnless(shutil.which("git"), "环境无 git 可执行，跳过 git fixture 测试")
class StopGateLayerAEnforceTest(GitRepoFixture):
    """声明 ownership 的任务 vs 实际 diff：block / 静默 / 续行上限。"""

    def add_gate_task(self, task_id=TID):
        return self.add_active_task(
            ownership_files=["src/owned/**"], task_id=task_id)

    def make_violating_diff(self):
        """owned 内一处改动 + 越界一处改动（均未跟踪，git status 可见）。"""
        self.write("src/owned/a.ts", b"owned\n")
        self.write("src/other/rogue.ts", b"rogue\n")

    # —— 用例 a：越界改动 → block + journal 记 gate_blocked ——
    def test_out_of_scope_changes_block_completion(self):
        self.add_gate_task()
        self.make_violating_diff()
        result = run_gate("{}", self.repo)
        self.assertEqual(result.returncode, 0)
        # stdout 为合法单行 JSON（除该行外无其他输出）
        self.assertEqual(result.stdout.count("\n"), 1)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["decision"], "block")
        reason = payload["reason"]
        self.assertIn(TID, reason)
        self.assertIn("src/other/rogue.ts", reason)
        self.assertNotIn("src/owned/a.ts", reason)
        # journal：1 条 gate_blocked，out_of_scope 精确等于越界清单
        events = self.journal_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event"], "gate_blocked")
        self.assertEqual(events[0]["out_of_scope"], ["src/other/rogue.ts"])
        self.assertEqual(events[0]["task_id"], TID)

    # —— 用例 b：只有 owned 内改动 → 静默放行；账本仅记 gate_passed（断链） ——
    def test_owned_only_changes_pass_silently(self):
        self.add_gate_task()
        self.write("src/owned/a.ts", b"owned\n")
        result = run_gate("{}", self.repo)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")
        events = self.journal_events()
        self.assertEqual([e["event"] for e in events], ["gate_passed"])
        self.assertEqual(events[0]["tasks"], [TID])

    # —— 用例 c：连续 3 次 Stop → 2 次 block 后放行 + EXHAUSTED 报警 ——
    def test_gate_exhausted_after_two_blocks(self):
        self.add_gate_task()
        self.make_violating_diff()
        first = run_gate("{}", self.repo)
        self.assertEqual(first.returncode, 0)
        self.assertEqual(json.loads(first.stdout)["decision"], "block")
        second = run_gate("{}", self.repo)
        self.assertEqual(second.returncode, 0)
        self.assertEqual(json.loads(second.stdout)["decision"], "block")
        self.assertEqual(
            [e["event"] for e in self.journal_events()],
            ["gate_blocked", "gate_blocked"])
        # 第 3 次：journal 尾部连续 gate_blocked 已达 2 → 放行 + 报警
        third = run_gate("{}", self.repo)
        self.assertEqual(third.returncode, 0)
        self.assertEqual(third.stdout, "")
        self.assertIn("ENFORCEMENT GATE EXHAUSTED", third.stderr)
        events = self.journal_events()
        self.assertEqual(
            [e["event"] for e in events],
            ["gate_blocked", "gate_blocked", "gate_exhausted"])
        self.assertEqual(events[-1]["out_of_scope"], ["src/other/rogue.ts"])

    # —— 用例 d：链断重置——block 之间出现其他事件 → 重新计数 ——
    def test_journal_chain_break_resets_block_count(self):
        self.add_gate_task()
        self.make_violating_diff()
        first = run_gate("{}", self.repo)
        second = run_gate("{}", self.repo)
        self.assertEqual(json.loads(first.stdout)["decision"], "block")
        self.assertEqual(json.loads(second.stdout)["decision"], "block")
        # 模型在两次 block 之间完成真实工作：手工追加一条 verification
        journal_mod.append_event(
            self.repo, TID,
            {"event": "verification",
             "command": "python3 -m unittest tests.test_stop_gate",
             "status": "valid"})
        third = run_gate("{}", self.repo)
        self.assertEqual(third.returncode, 0)
        # 连续链已被 verification 断开 → 重新计数 → 第 3 次仍然 block
        self.assertEqual(json.loads(third.stdout)["decision"], "block")
        self.assertNotIn("ENFORCEMENT GATE EXHAUSTED", third.stderr)
        names = [e["event"] for e in self.journal_events()]
        self.assertEqual(names.count("gate_blocked"), 3)
        self.assertNotIn("gate_exhausted", names)

    # —— 用例 g：stop_hook_active=true（续行循环中）→ 照常校验 ——
    def test_stop_hook_active_still_verifies(self):
        # 续行循环中的 Stop（stop_hook_active=true）照常校验——运行时
        # 3 次续行额度正是靠每次 Stop 都校验来消费的；提前放行会让
        # 续行中的完成声明免检（骨架时代"防自锁提前返回"已废止）
        self.add_gate_task()
        self.make_violating_diff()
        result = run_gate('{"stop_hook_active": true}', self.repo)
        self.assertEqual(result.returncode, 0)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["decision"], "block")
        self.assertEqual(
            [e["event"] for e in self.journal_events()], ["gate_blocked"])

    # —— 约束补充：多违规任务时第一个为准，其余在 reason 附带列出 ——
    def test_multiple_violating_tasks_first_task_is_primary(self):
        first = self.add_gate_task(task_id="multi-task-aaa")
        second = self.add_active_task(
            ownership_files=["src/b/**"], task_id="multi-task-bbb")
        self.write("src/a/ok.ts", b"ok\n")
        self.write("src/b/ok.ts", b"ok\n")
        self.write("src/a/rogue1.ts", b"rogue\n")
        self.write("src/b/rogue2.ts", b"rogue\n")
        result = run_gate("{}", self.repo)
        self.assertEqual(result.returncode, 0)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["decision"], "block")
        reason = payload["reason"]
        # 主违规任务是第一个（find_active_tasks 按目录名排序）
        self.assertIn("ownership violation (task %s)." % first, reason)
        self.assertIn("- src/a/rogue1.ts", reason)
        # 其余违规任务以 "Also out of scope in task <id>: ..." 附带列出
        self.assertIn("Also out of scope in task %s: " % second, reason)
        self.assertIn("src/b/rogue2.ts", reason)
        # journal 记账只落在第一个违规任务
        self.assertEqual(
            [e["event"] for e in self.journal_events(first)],
            ["gate_blocked"])
        self.assertEqual(self.journal_events(second), [])


# —— 用例 e：未声明 ownership 的任务跳过 Layer A ——

@unittest.skipUnless(shutil.which("git"), "环境无 git 可执行，跳过 git fixture 测试")
class StopGateEmptyOwnershipTest(GitRepoFixture):
    """ownership.files 为空的任务：即使有越界改动也跳过校验、静默放行。"""

    def test_empty_ownership_declaration_skips_gate(self):
        self.add_active_task(ownership_files=())
        self.write("src/other/rogue.ts", b"rogue\n")
        result = run_gate("{}", self.repo)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")
        # 无人声明 ownership → 没有校验发生 → 不记账（gate_passed 只对
        # 参与校验的任务记录）
        self.assertEqual(self.journal_events(), [])


# —— 用例 f：git 失败 → fail-open 降级放行 + gate_degraded 记账 ——

class StopGateGitFailureDegradeTest(TempDirFixture):
    """ZCODE_PROJECT_DIR 指向非 git 目录：touched 清单不可得 → 降级放行。"""

    def test_git_unavailable_degrades_open(self):
        # 非 git 仓库 + 活动任务（state 建在 tempdir）
        self.add_active_task(ownership_files=["src/owned/**"])
        result = run_gate("{}", self.repo)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("ENFORCEMENT DEGRADED", result.stderr)
        self.assertIn("ownership gate skipped", result.stderr)
        events = self.journal_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event"], "gate_degraded")
        self.assertEqual(events[0]["reason"], "git_unavailable")


@unittest.skipUnless(shutil.which("git"), "环境无 git 可执行，跳过 git fixture 测试")
class StopGateMultiTaskAccountingTest(GitRepoFixture):
    """多活动任务下的记账对象：gate_passed 只记到声明了 ownership 的任务。

    回归用例（终审 P2-1）：活动任务含未声明者时，gate_passed 若记到
    active[0]（可能恰是未声明任务），真正的 gate_blocked 尾链任务不被
    断链，下一周期会提前进入 exhausted。
    """

    def test_gate_passed_recorded_only_for_declared_tasks(self):
        undeclared = self.add_active_task(
            ownership_files=(), task_id="multi-a-undeclared")
        declared = self.add_active_task(
            ownership_files=["src/owned/**"], task_id="multi-b-declared")
        # 目录名排序后 undeclared 在前（active[0] != declared）
        self.assertLess(undeclared, declared)
        self.write("src/owned/a.ts", b"owned\n")
        result = run_gate("{}", self.repo)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        # 未声明任务零记账；声明任务记 gate_passed（断链语义正确落位）
        self.assertEqual(self.journal_events(undeclared), [])
        events = self.journal_events(declared)
        self.assertEqual([e["event"] for e in events], ["gate_passed"])
        self.assertEqual(events[0]["tasks"], [declared])


if __name__ == "__main__":
    unittest.main()
