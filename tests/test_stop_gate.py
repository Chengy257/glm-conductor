#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hooks.stop_gate 子进程冒烟测试（v2 强制层 Stop 完成门，§15 四重检查）。

以子进程方式运行 plugins/glm-conductor/hooks/stop_gate.py，验证其
完成门与 fail-open 契约的可观测行为：
  - 无活动任务：静默放行（退出码 0，无任何输出）；
  - 活动任务无越界（或未声明 ownership）：静默放行（骨架观测报文已废止）；
  - 声明了 ownership 的任务存在越界改动：stdout 单行
    {"decision":"block",...} 拦截 + journal 记 gate_blocked；
  - B4.1 四重检查（§15 顺序 ownership → verification → review → visual）：
    verification_missing / verification_stale / review_missing /
    review_rejected / review_stale / visual_stale 六种新 check 的 block
    报文与 journal 事件字段；指纹用 fingerprint.task_fingerprint 真算
    （与门同一入口）；全部新鲜 → 静默放行 + gate_passed 记账；
  - journal 尾部连续 gate_blocked 达上限：放行 + stderr 报
    ENFORCEMENT GATE EXHAUSTED + journal 记 gate_exhausted；
    链中出现其他事件即断链重新计数（上限对新 check 同样生效）；
  - git 失败 / 非 git 仓库：降级放行（stderr ENFORCEMENT DEGRADED）
    + journal 记 gate_degraded；evaluate 阶段结构性错误同走降级
    （reason=evaluation_error）；
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

import hashlib
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
from runtime import fingerprint as fingerprint_mod
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
                        task_id=TID, **state_kwargs):
        """写入一个活动任务 state.json，返回 task_id。

        state_kwargs 直接透传 state.new_task_state（B4.1 起
        verification_required / review_required / reviewer 等按名透传）。
        """
        state.save_state(self.repo, state.new_task_state(
            task_id, "完成门 Layer A 冒烟测试目标", dict(ROUTE),
            ownership_files=ownership_files, status=status, **state_kwargs))
        return task_id

    def set_state(self, st):
        """保存（已深改的）任务 state dict（落点取 st["task_id"]）。"""
        state.save_state(self.repo, st)
        return st

    def patch_state_file_raw(self, task_id, mutate):
        """绕过 save_state 校验直接改写已落盘的 state.json（模拟手写
        state.json 的形状异常）。

        save_state 会按 validate_state 拒绝词汇外 review.verdict、缺
        sha256 的 visual_evidence 项等形状；本助手先落合法状态，再对
        JSON 文件就地打补丁，精确复现「手写偏差」场景。mutate 就地修改
        load 出的 dict，无返回值。
        """
        path = state.state_path(self.repo, task_id)
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        mutate(raw)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(raw, fh, ensure_ascii=False, indent=2)

    def task_fingerprint(self, st):
        """按门同一入口真算任务证据指纹（fingerprint.task_fingerprint），
        保证测试记录值与完成门比对值可比。"""
        return fingerprint_mod.task_fingerprint(str(self.repo), st)

    def file_sha256(self, rel):
        """仓库内文件原始字节的 sha256（视觉证据记录值口径，与门一致）。"""
        with open(self.repo / rel, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()

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
        # journal：1 条 gate_blocked，check=ownership，
        # out_of_scope 精确等于越界清单，patterns 为声明清单（B4.1 结构）
        events = self.journal_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event"], "gate_blocked")
        self.assertEqual(events[0]["check"], "ownership")
        self.assertEqual(events[0]["out_of_scope"], ["src/other/rogue.ts"])
        self.assertEqual(events[0]["patterns"], ["src/owned/**"])
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

    # —— 用例 c2：exhausted 后第 4 次 Stop → 重新 block（新周期额度） ——
    def test_fourth_stop_blocks_again_after_gate_exhausted(self):
        # gate_exhausted 事件断开 journal 尾部的 gate_blocked 连续链：
        # 第 4 次 Stop 从尾部数到的连续 gate_blocked 为 0 → 重新计数，
        # 新周期重新获得 2 次 block 额度（无此机制则同一违规永远放行）
        self.add_gate_task()
        self.make_violating_diff()
        first = run_gate("{}", self.repo)
        second = run_gate("{}", self.repo)
        third = run_gate("{}", self.repo)
        self.assertEqual(json.loads(first.stdout)["decision"], "block")
        self.assertEqual(json.loads(second.stdout)["decision"], "block")
        self.assertEqual(third.returncode, 0)
        self.assertEqual(third.stdout, "")
        self.assertIn("ENFORCEMENT GATE EXHAUSTED", third.stderr)
        # 第 4 次：尾部最新事件是 gate_exhausted（非 gate_blocked）→
        # 连续计数为 0 → 再次 block，且无 EXHAUSTED 报警
        fourth = run_gate("{}", self.repo)
        self.assertEqual(fourth.returncode, 0)
        self.assertEqual(json.loads(fourth.stdout)["decision"], "block")
        self.assertNotIn("ENFORCEMENT GATE EXHAUSTED", fourth.stderr)
        self.assertEqual(
            [e["event"] for e in self.journal_events()],
            ["gate_blocked", "gate_blocked", "gate_exhausted",
             "gate_blocked"])
        # 新周期语义：第 5 次 Stop（尾部 1 条 gate_blocked < 2）仍 block，
        # 第 6 次才再次 exhausted——额度确实重新计满
        fifth = run_gate("{}", self.repo)
        self.assertEqual(json.loads(fifth.stdout)["decision"], "block")
        sixth = run_gate("{}", self.repo)
        self.assertEqual(sixth.stdout, "")
        self.assertIn("ENFORCEMENT GATE EXHAUSTED", sixth.stderr)

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
        # 其余违规任务以 "Also failing in task <id>: <check>" 附带列出
        # （B4.1 起附带行只列 check，不再逐路径列出其他任务的越界清单）
        self.assertIn("Also failing in task %s: ownership" % second, reason)
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


# —— B4.1 四重检查：verification（missing / stale） ——

@unittest.skipUnless(shutil.which("git"), "环境无 git 可执行，跳过 git fixture 测试")
class StopGateVerificationCheckTest(GitRepoFixture):
    """verification.required 检查：命令缺失 → verification_missing，
    指纹过期 → verification_stale。"""

    CMD = "pytest tests/a.py"

    def test_verification_missing_blocks_with_command_list(self):
        self.add_active_task(verification_required=[self.CMD])
        result = run_gate("{}", self.repo)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.count("\n"), 1)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["decision"], "block")
        reason = payload["reason"]
        self.assertIn("required parent verification is incomplete", reason)
        self.assertIn("Missing:", reason)
        self.assertIn("- %s" % self.CMD, reason)
        self.assertIn("runtime.state.record_verification", reason)
        events = self.journal_events()
        self.assertEqual([e["event"] for e in events], ["gate_blocked"])
        self.assertEqual(events[0]["check"], "verification_missing")
        self.assertEqual(events[0]["task_id"], TID)
        self.assertEqual(events[0]["missing"], [self.CMD])

    def test_verification_stale_with_null_fingerprint_blocks(self):
        self.add_active_task(verification_required=[self.CMD])
        st = state.load_state(self.repo, TID)
        state.record_verification(st, self.CMD)  # fingerprint=None → 保留 None
        self.set_state(st)
        result = run_gate("{}", self.repo)
        self.assertEqual(result.returncode, 0)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["decision"], "block")
        reason = payload["reason"]
        self.assertIn("verification evidence is stale", reason)
        self.assertIn("recorded none", reason)
        self.assertIn("current fingerprint sha256:", reason)
        self.assertIn("Re-run the required verification commands", reason)
        events = self.journal_events()
        self.assertEqual(events[0]["check"], "verification_stale")
        self.assertIsNone(events[0]["recorded"])
        self.assertTrue(events[0]["current"].startswith("sha256:"))

    def test_verification_stale_after_later_change_blocks(self):
        self.write("src/a.py", b"v1\n")
        self.add_active_task(verification_required=[self.CMD])
        st = state.load_state(self.repo, TID)
        state.record_verification(st, self.CMD, self.task_fingerprint(st))
        self.set_state(st)
        # 记录后立即检查：指纹新鲜 → 静默放行 + gate_passed 记到该任务
        fresh = run_gate("{}", self.repo)
        self.assertEqual(fresh.returncode, 0)
        self.assertEqual(fresh.stdout, "")
        self.assertEqual(fresh.stderr, "")
        self.assertEqual([e["event"] for e in self.journal_events()],
                         ["gate_passed"])
        # 之后改动文件 → 旧指纹失效 → block，报文含新旧两个指纹串
        self.write("src/a.py", b"v2\n")
        result = run_gate("{}", self.repo)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["decision"], "block")
        reason = payload["reason"]
        self.assertIn("verification evidence is stale", reason)
        events = self.journal_events()
        self.assertEqual(events[-1]["check"], "verification_stale")
        self.assertNotEqual(events[-1]["recorded"], events[-1]["current"])
        self.assertIn(events[-1]["recorded"], reason)
        self.assertIn(events[-1]["current"], reason)


# —— B4.1 四重检查：review（missing / rejected / stale） ——

@unittest.skipUnless(shutil.which("git"), "环境无 git 可执行，跳过 git fixture 测试")
class StopGateReviewCheckTest(GitRepoFixture):
    """review.required 检查：三类拦截 + reviewer / verdict / 指纹字段。"""

    def _review_task(self, verdict, fingerprint=None):
        """建一个 review.required 任务并记录指定裁决（指纹可选绑定）。"""
        self.add_active_task(review_required=True, reviewer="reviewer-a")
        st = state.load_state(self.repo, TID)
        state.record_review(st, verdict, fingerprint)
        self.set_state(st)

    def test_review_missing_blocks_with_reviewer(self):
        self.add_active_task(review_required=True, reviewer="reviewer-a")
        result = run_gate("{}", self.repo)
        self.assertEqual(result.returncode, 0)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["decision"], "block")
        reason = payload["reason"]
        self.assertIn("review required but not completed", reason)
        self.assertIn("reviewer-a", reason)
        self.assertIn("runtime.state.record_review", reason)
        events = self.journal_events()
        self.assertEqual(events[0]["check"], "review_missing")
        self.assertEqual(events[0]["task_id"], TID)
        self.assertEqual(events[0]["reviewer"], "reviewer-a")

    def test_review_missing_unassigned_reviewer(self):
        self.add_active_task(review_required=True)  # reviewer 缺省 None
        result = run_gate("{}", self.repo)
        reason = json.loads(result.stdout)["reason"]
        self.assertIn("Reviewer: unassigned", reason)
        self.assertIsNone(self.journal_events()[0]["reviewer"])

    def test_review_rejected_blocks_with_verdict(self):
        self._review_task("fix-first")
        result = run_gate("{}", self.repo)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["decision"], "block")
        reason = payload["reason"]
        self.assertIn("review verdict is 'fix-first'", reason)
        self.assertIn("runtime.state.record_review", reason)
        events = self.journal_events()
        self.assertEqual(events[0]["check"], "review_rejected")
        self.assertEqual(events[0]["verdict"], "fix-first")

    def test_review_stale_with_null_fingerprint_blocks(self):
        self._review_task("ship")  # ship 但未绑定指纹
        result = run_gate("{}", self.repo)
        reason = json.loads(result.stdout)["reason"]
        self.assertIn("review evidence is stale", reason)
        self.assertIn("review fingerprint none", reason)
        self.assertIn("Re-review the current change set", reason)
        events = self.journal_events()
        self.assertEqual(events[0]["check"], "review_stale")
        self.assertIsNone(events[0]["recorded"])
        self.assertTrue(events[0]["current"].startswith("sha256:"))

    def test_review_stale_after_later_change_blocks(self):
        self.write("src/a.py", b"v1\n")
        self.add_active_task(review_required=True, reviewer="reviewer-a")
        st = state.load_state(self.repo, TID)
        state.record_review(st, "ship", self.task_fingerprint(st))
        self.set_state(st)
        # 审查后改动文件 → 审查证据失效 → block（recorded 为旧指纹）
        self.write("src/a.py", b"v2\n")
        result = run_gate("{}", self.repo)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["decision"], "block")
        reason = payload["reason"]
        self.assertIn("review evidence is stale", reason)
        self.assertIn("Files changed after review", reason)
        events = self.journal_events()
        self.assertEqual(events[0]["check"], "review_stale")
        self.assertNotEqual(events[0]["recorded"], events[0]["current"])

    def test_out_of_vocabulary_verdict_blocks_as_review_missing(self):
        # 回归（终审 P2）：verdict 词汇外（手写 state.json 拼写偏差，如
        # 大写 "Ship"）不得沿 ship 路径仅做指纹比对后放行——按
        # review_missing 处理，detail 携带 verdict 原值，报文模板不变
        self.write("src/a.py", b"v1\n")
        self.add_active_task(review_required=True, reviewer="reviewer-a")
        st = state.load_state(self.repo, TID)
        current = self.task_fingerprint(st)
        # 指纹绑定当前状态（若被误当 ship 处理则此处会静默放行）
        def mutate(raw):
            raw["review"]["verdict"] = "Ship"
            raw["review"]["fingerprint"] = current
        self.patch_state_file_raw(TID, mutate)
        result = run_gate("{}", self.repo)
        self.assertEqual(result.returncode, 0)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["decision"], "block")
        reason = payload["reason"]
        self.assertIn("review required but not completed", reason)
        self.assertIn("reviewer-a", reason)
        self.assertIn("runtime.state.record_review", reason)
        events = self.journal_events()
        self.assertEqual(events[0]["check"], "review_missing")
        self.assertEqual(events[0]["task_id"], TID)
        self.assertEqual(events[0]["reviewer"], "reviewer-a")
        self.assertEqual(events[0]["verdict"], "Ship")


# —— B4.1 四重检查：visual evidence（stale） ——

@unittest.skipUnless(shutil.which("git"), "环境无 git 可执行，跳过 git fixture 测试")
class StopGateVisualEvidenceCheckTest(GitRepoFixture):
    """visual_evidence 检查：记录后截图字节变化 → visual_stale 拦截。"""

    SHOT = "shots/ui.png"

    def _record_visual(self):
        """写截图并记录视觉证据（sha256 为文件原始字节哈希）。"""
        self.write(self.SHOT, b"png-bytes-v1")
        self.add_active_task()
        st = state.load_state(self.repo, TID)
        state.record_visual_evidence(st, self.SHOT, self.file_sha256(self.SHOT))
        self.set_state(st)

    def test_visual_stale_blocks_after_capture_change(self):
        self._record_visual()
        # 记录后未变化 → 静默放行（visual_evidence 非空即参与，记账照常）
        fresh = run_gate("{}", self.repo)
        self.assertEqual(fresh.returncode, 0)
        self.assertEqual(fresh.stdout, "")
        self.assertEqual(fresh.stderr, "")
        self.assertEqual([e["event"] for e in self.journal_events()],
                         ["gate_passed"])
        # 截图字节变化 → stale → block，报文含路径与证据指纹前缀
        self.write(self.SHOT, b"png-bytes-v2")
        result = run_gate("{}", self.repo)
        self.assertEqual(result.returncode, 0)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["decision"], "block")
        reason = payload["reason"]
        self.assertIn("visual evidence changed after review", reason)
        self.assertIn(self.SHOT, reason)
        self.assertIn("runtime.state.record_visual_evidence", reason)
        events = self.journal_events()
        self.assertEqual(events[-1]["event"], "gate_blocked")
        self.assertEqual(events[-1]["check"], "visual_stale")
        stale = events[-1]["stale"]
        self.assertEqual(len(stale), 1)
        self.assertEqual(stale[0]["path"], self.SHOT)
        self.assertEqual(stale[0]["recorded"],
                         hashlib.sha256(b"png-bytes-v1").hexdigest())
        self.assertEqual(stale[0]["current"],
                         hashlib.sha256(b"png-bytes-v2").hexdigest())
        self.assertIn(hashlib.sha256(b"png-bytes-v1").hexdigest()[:12], reason)

    def test_visual_entry_without_sha256_is_stale(self):
        # 回归（终审 P2）：证据项只有 path、缺 sha256 时不得因
        # current==recorded==None 被误判新鲜——缺 recorded 视为 stale
        # （证据未绑定内容即不可信），文件存在与否均拦
        self.add_active_task()
        # 手写形状：record_visual_evidence 校验参数，缺 sha256 的证据项
        # 直接对已落盘 state.json 打补丁构造
        self.patch_state_file_raw(
            TID, lambda raw: raw.update(
                {"visual_evidence": [{"path": self.SHOT}]}))
        # 场景一：文件不存在 → current=None，同样 stale → block
        # （旧实现 current==recorded==None 会误判新鲜而静默放行）
        missing = run_gate("{}", self.repo)
        self.assertEqual(missing.returncode, 0)
        payload = json.loads(missing.stdout)
        self.assertEqual(payload["decision"], "block")
        reason = payload["reason"]
        self.assertIn("visual evidence changed after review", reason)
        self.assertIn(self.SHOT, reason)
        self.assertIn("recorded missing, current missing", reason)
        # 场景二：同 path 文件存在 → current 非 None，仍 stale → block
        self.write(self.SHOT, b"png-bytes-v1")
        second = run_gate("{}", self.repo)
        self.assertEqual(json.loads(second.stdout)["decision"], "block")
        events = self.journal_events()
        self.assertEqual([e["event"] for e in events],
                         ["gate_blocked", "gate_blocked"])
        self.assertEqual([e["check"] for e in events],
                         ["visual_stale", "visual_stale"])
        self.assertEqual(events[-1]["stale"],
                         [{"path": self.SHOT, "recorded": None,
                           "current": hashlib.sha256(
                               b"png-bytes-v1").hexdigest()}])


# —— B4.1 四重检查：全新鲜通过 / 混合违规 / 新 check 续行上限 / 降级 ——

@unittest.skipUnless(shutil.which("git"), "环境无 git 可执行，跳过 git fixture 测试")
class StopGateAllFreshPassTest(GitRepoFixture):
    """verification 完成 + 指纹新鲜 + review ship + 指纹新鲜 → 静默放行，
    gate_passed 记到该任务（无 ownership 声明也记账——参与集合语义）。"""

    def test_all_fresh_passes_silently_with_accounting(self):
        self.write("src/a.py", b"v1\n")
        self.add_active_task(verification_required=["pytest tests/a.py"],
                             review_required=True, reviewer="reviewer-a")
        st = state.load_state(self.repo, TID)
        current = self.task_fingerprint(st)
        state.record_verification(st, "pytest tests/a.py", current)
        state.record_review(st, "ship", current)
        self.set_state(st)
        result = run_gate("{}", self.repo)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")
        events = self.journal_events()
        self.assertEqual([e["event"] for e in events], ["gate_passed"])
        self.assertEqual(events[0]["tasks"], [TID])


@unittest.skipUnless(shutil.which("git"), "环境无 git 可执行，跳过 git fixture 测试")
class StopGateMixedChecksTest(GitRepoFixture):
    """多任务多 check 混合：以目录名序第一个违规任务为主报文，
    其余违规任务以 "Also failing in task <id>: <check>" 附带列出。"""

    def test_first_violating_task_primary_other_checks_appended(self):
        self.add_active_task(verification_required=["pytest tests/a.py"],
                             task_id="mix-task-aaa")
        self.add_active_task(review_required=True, reviewer="rev-b",
                             task_id="mix-task-bbb")
        result = run_gate("{}", self.repo)
        self.assertEqual(result.returncode, 0)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["decision"], "block")
        reason = payload["reason"]
        # 主报文以第一个违规任务（目录名序）为准
        self.assertIn("required parent verification is incomplete"
                      " (task mix-task-aaa).", reason)
        # 任务 B 的 review_missing 以附带行列出（只列 check）
        self.assertIn("Also failing in task mix-task-bbb: review_missing",
                      reason)
        # journal 记账只落在第一个违规任务
        self.assertEqual(
            [e["event"] for e in self.journal_events("mix-task-aaa")],
            ["gate_blocked"])
        self.assertEqual(
            [e["check"] for e in self.journal_events("mix-task-aaa")],
            ["verification_missing"])
        self.assertEqual(self.journal_events("mix-task-bbb"), [])


@unittest.skipUnless(shutil.which("git"), "环境无 git 可执行，跳过 git fixture 测试")
class StopGateExhaustedOnVerificationTest(GitRepoFixture):
    """续行上限对新 check 同样生效：verification_missing 连打 3 次 Stop
    → 2 次 block + 第 3 次 gate_exhausted 放行。"""

    def test_gate_exhausted_on_verification_missing(self):
        self.add_active_task(verification_required=["pytest tests/a.py"])
        first = run_gate("{}", self.repo)
        second = run_gate("{}", self.repo)
        self.assertEqual(json.loads(first.stdout)["decision"], "block")
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
        self.assertEqual(events[-1]["check"], "verification_missing")
        self.assertEqual(events[-1]["missing"], ["pytest tests/a.py"])


@unittest.skipUnless(shutil.which("git"), "环境无 git 可执行，跳过 git fixture 测试")
class StopGateEvaluationErrorDegradeTest(GitRepoFixture):
    """evaluate 阶段结构性错误（ownership 声明模式非法 → classify_paths
    抛 OwnershipError）→ 降级放行 + gate_degraded（reason=evaluation_error），
    与 git_unavailable 同走 fail-open，不卡会话。"""

    def test_illegal_pattern_degrades_with_evaluation_error(self):
        self.add_active_task(ownership_files=["src//bad/**"])  # 空路径段
        self.write("src/x.ts", b"x\n")
        result = run_gate("{}", self.repo)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("ENFORCEMENT DEGRADED", result.stderr)
        self.assertIn("gate skipped", result.stderr)
        events = self.journal_events()
        self.assertEqual([e["event"] for e in events], ["gate_degraded"])
        self.assertEqual(events[0]["reason"], "evaluation_error")


# —— B4.3 集成冒烟：§98 场景 3-6（多次 Stop 驱动的端到端流） ——

@unittest.skipUnless(shutil.which("git"), "环境无 git 可执行，跳过 git fixture 测试")
class StopGateIntegrationSmokeTest(GitRepoFixture):
    """§98 集成冒烟场景 3-6（B4.3）。

    区别于 B4.1 的单检查用例：每个场景是「多次 run_gate 的完整流」——
    第一次 Stop 拦截 → 模拟主会话修复（record_verification /
    record_review 落证）→ 第二次 Stop 放行。逐场景断言两次 Stop 的
    stdout/stderr 精确形态（block 次 = 单行 JSON；放行次 = 全空）、
    journal 事件序（含 check 字段）与 block reason 关键子串。
    """

    CMD = "python3 -m unittest tests.test_app"
    OWNED = "src/app.py"

    def _write_owned(self, data):
        """写/改一个 owned 文件（src/** 模式覆盖 src/app.py）。"""
        self.write(self.OWNED, data)

    # —— 场景 3：verification missing → block；补证 → 放行 ——
    def test_scenario3_verification_missing_then_supplemented_passes(self):
        self.add_active_task(
            ownership_files=["src/**"], verification_required=[self.CMD])
        self._write_owned(b"v1\n")
        # 第一次 Stop：required 命令未记录 → verification_missing 拦截
        first = run_gate("{}", self.repo)
        self.assertEqual(first.returncode, 0)
        self.assertEqual(first.stdout.count("\n"), 1)
        payload = json.loads(first.stdout)
        self.assertEqual(payload["decision"], "block")
        reason = payload["reason"]
        self.assertIn("required parent verification is incomplete", reason)
        self.assertIn("- %s" % self.CMD, reason)
        events = self.journal_events()
        self.assertEqual([e["event"] for e in events], ["gate_blocked"])
        self.assertEqual(events[0]["check"], "verification_missing")
        self.assertEqual(events[0]["task_id"], TID)
        self.assertEqual(events[0]["missing"], [self.CMD])
        # 模拟主会话补证：记录验证命令 + 当前证据指纹（与门同一入口真算）
        st = state.load_state(self.repo, TID)
        state.record_verification(st, self.CMD, self.task_fingerprint(st))
        self.set_state(st)
        # 第二次 Stop：证据新鲜 → 完全静默放行 + journal 尾部 gate_passed
        second = run_gate("{}", self.repo)
        self.assertEqual(second.returncode, 0)
        self.assertEqual(second.stdout, "")
        self.assertEqual(second.stderr, "")
        self.assertEqual(
            [e["event"] for e in self.journal_events()],
            ["gate_blocked", "gate_passed"])

    # —— 场景 4：review missing（high assurance）→ block；裁决 → 放行 ——
    def test_scenario4_review_missing_then_verdict_passes(self):
        self.add_active_task(
            ownership_files=["src/**"], verification_required=[self.CMD],
            review_required=True, reviewer="glm-reviewer")
        self._write_owned(b"v1\n")
        # verification 全备且指纹新鲜；§98 场景 4 名义为 high assurance
        # （完成门行为不依赖 assurance，提升只为场景语义忠实）
        st = state.load_state(self.repo, TID)
        st["route"]["assurance"] = "high"
        state.record_verification(st, self.CMD, self.task_fingerprint(st))
        self.set_state(st)
        # 第一次 Stop：verification 新鲜、仅 review 未完成 → review_missing
        first = run_gate("{}", self.repo)
        self.assertEqual(first.returncode, 0)
        self.assertEqual(first.stdout.count("\n"), 1)
        payload = json.loads(first.stdout)
        self.assertEqual(payload["decision"], "block")
        reason = payload["reason"]
        self.assertIn("review required but not completed", reason)
        self.assertIn("glm-reviewer", reason)
        events = self.journal_events()
        self.assertEqual([e["event"] for e in events], ["gate_blocked"])
        self.assertEqual(events[0]["check"], "review_missing")
        self.assertEqual(events[0]["task_id"], TID)
        self.assertEqual(events[0]["reviewer"], "glm-reviewer")
        # 模拟主会话裁决：ship + 当前指纹 → 全部新鲜
        st = state.load_state(self.repo, TID)
        state.record_review(st, "ship", self.task_fingerprint(st))
        self.set_state(st)
        # 第二次 Stop：完全静默放行 + journal 尾部 gate_passed
        second = run_gate("{}", self.repo)
        self.assertEqual(second.returncode, 0)
        self.assertEqual(second.stdout, "")
        self.assertEqual(second.stderr, "")
        self.assertEqual(
            [e["event"] for e in self.journal_events()],
            ["gate_blocked", "gate_passed"])

    # —— 场景 5：审查后编辑 → review stale 拦截 ——
    def test_scenario5_post_review_edit_review_stale(self):
        # review-only：verification_required 为空、review_required=True
        self.add_active_task(
            ownership_files=["src/**"], review_required=True,
            reviewer="glm-reviewer")
        self._write_owned(b"v1\n")
        # 先落一个新鲜裁决（ship + 当前证据指纹）
        st = state.load_state(self.repo, TID)
        state.record_review(st, "ship", self.task_fingerprint(st))
        self.set_state(st)
        # 审查后修复：编辑 owned 文件内容 → 审查证据失效
        self._write_owned(b"v2\n")
        result = run_gate("{}", self.repo)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.count("\n"), 1)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["decision"], "block")
        reason = payload["reason"]
        self.assertIn("review evidence is stale", reason)
        self.assertIn("Files changed after review", reason)
        events = self.journal_events()
        self.assertEqual([e["event"] for e in events], ["gate_blocked"])
        self.assertEqual(events[0]["check"], "review_stale")
        self.assertEqual(events[0]["task_id"], TID)
        # reason 同时含 current 与 review fingerprint 两枚指纹串
        self.assertNotEqual(events[0]["recorded"], events[0]["current"])
        self.assertIn(events[0]["recorded"], reason)
        self.assertIn(events[0]["current"], reason)

    # —— 场景 6：重验 + 新鲜审查 → 放行（§93 例） ——
    def test_scenario6_reverify_fresh_review_completion_allowed(self):
        self.add_active_task(
            ownership_files=["src/**"], verification_required=[self.CMD],
            review_required=True, reviewer="glm-reviewer")
        self._write_owned(b"v1\n")
        # 初始全部新鲜：verification 与 review 指纹都 = task_fingerprint
        st = state.load_state(self.repo, TID)
        fresh = self.task_fingerprint(st)
        state.record_verification(st, self.CMD, fresh)
        state.record_review(st, "ship", fresh)
        self.set_state(st)
        # 一次修复：两枚证据同时过期（§93 例）
        self._write_owned(b"v2\n")
        first = run_gate("{}", self.repo)
        self.assertEqual(first.returncode, 0)
        self.assertEqual(first.stdout.count("\n"), 1)
        payload = json.loads(first.stdout)
        self.assertEqual(payload["decision"], "block")
        # §15 顺序验证在前：两枚同坏时先报 verification_stale
        self.assertIn("verification evidence is stale", payload["reason"])
        events = self.journal_events()
        self.assertEqual([e["event"] for e in events], ["gate_blocked"])
        self.assertEqual(events[0]["check"], "verification_stale")
        self.assertEqual(events[0]["task_id"], TID)
        # 模拟重验 + 重审：两枚证据都重新绑定当前指纹
        st = state.load_state(self.repo, TID)
        current = self.task_fingerprint(st)
        state.record_verification(st, self.CMD, current)
        state.record_review(st, "ship", current)
        self.set_state(st)
        # 第二次 Stop：静默放行 + journal 尾部 [gate_blocked, gate_passed]
        second = run_gate("{}", self.repo)
        self.assertEqual(second.returncode, 0)
        self.assertEqual(second.stdout, "")
        self.assertEqual(second.stderr, "")
        self.assertEqual(
            [e["event"] for e in self.journal_events()],
            ["gate_blocked", "gate_passed"])


if __name__ == "__main__":
    unittest.main()
