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
  - H3/P0-3 发现完整性（discover_tasks 四分类）：state.json 损坏 +
    journal 高保障证据（route_selected 的 mode 为 audit/full 或
    assurance=high，或 status_changed 的 to=finalizing）→ fail-closed
    block（check=corrupt_state，统一 exhaustion 机器同样生效）；损坏
    无高保障证据 → 降级放行（stderr 报警 + gate_degraded
    reason=corrupt_state）；orphaned（目录有 events.jsonl 但无
    state.json）→ 永不拦截（stderr 报警 + gate_degraded
    reason=orphaned_task）——损坏的 active state.json 不再静默消失；
  - RB-2 per-task 仓库求值（workspace=非 git 账本根 + 嵌套真实 git
    仓库夹具）：任务绑定 repository.root 后 ownership / 指纹 / 完成提交
    全部在任务仓库根求值（repo root ≠ ZCODE_PROJECT_DIR 仍走真实门，
    dogfood 场景 finalizing 照常提交 completed）；双仓互不越界；仓库
    故障只降级该任务（journal gate_degraded 的结构化 reason 精确断言：
    repository_unavailable / repository_root_missing /
    repository_ambiguous），legacy 无绑定 + 账本根非 git 时按一级子
    目录 .git 扫描区分 missing / ambiguous，歧义不猜、不自动绑定；
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
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "glm-conductor"))
from runtime import fingerprint as fingerprint_mod
from runtime import journal as journal_mod
from runtime import state
# H1/H3：钩子模块直接导入——commit_finalizing_completions 的完成提交
# 通道与失败隔离（H1）、_corrupt_requires_fail_closed 证据规则与
# build_block_reason 的 corrupt_state 报文模板（H3）的模块级直测
# （子进程冒烟之外的快速反馈）
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "glm-conductor" / "hooks"))
import stop_gate
from stop_gate import _corrupt_requires_fail_closed, build_block_reason

# 被测钩子脚本：仓库根 plugins/glm-conductor/hooks/stop_gate.py
STOP_GATE = (Path(__file__).resolve().parents[1]
             / "plugins/glm-conductor/hooks/stop_gate.py")

TID = "demo-task-1a2b3c"
# H2 夹具迁移：delegate 路由在规则 R4 下要求非空 ownership/verification，
# 而本文件的门行为用例大量依赖「空声明」基座（被测语义与路由无关）——
# 基座改用矩阵合法的 solo 路由（solo+standard 推导无审查义务）；需要
# delegate/audit/full 语义的场景各自显式构造 route
ROUTE = {"mode": "solo", "delegability": "low", "assurance": "standard",
         "executor": "main", "continuity": "foreground"}


def run_gate(stdin_text, project_dir):
    """以子进程运行 stop_gate.py，返回 CompletedProcess。

    text=True + 显式 encoding="utf-8"：钩子按运行时契约恒以 UTF-8 字节
    写 stdout（emit_block_json，ensure_ascii=False）与 stderr
    （warn_stderr 直写 buffer），父进程必须按 UTF-8 解码——不指定
    encoding 时按父进程 locale（如 en-US runner 的 cp1252）严格解码，
    中文字节（corrupt reason「损坏」的 0x8D 在 cp1252 未定义）会
    UnicodeDecodeError（CI Windows 矩阵实测：8 个含中文流的用例全挂，
    ASCII 流用例全过；errors="replace" 兜底异常字节）。
    ZCODE_PROJECT_DIR 指向被检仓库（钩子据此发现活动任务），其余环境变量
    原样继承。断言只用 ASCII 子串，规避替换符干扰。
    """
    return subprocess.run(
        [sys.executable, str(STOP_GATE)],
        input=stdin_text, text=True, capture_output=True,
        encoding="utf-8", errors="replace",
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
        的 rmtree 会 PermissionError；先遍历临时树内全部 .git 目录（RB-2
        起夹具含嵌套真实 git 仓库）清掉只读位再删除（模式复用
        tests/test_ownership.py）。
        """
        git_dirs = []
        for dirpath, dirnames, _filenames in os.walk(self._tmp.name):
            if ".git" in dirnames:
                git_dirs.append(os.path.join(dirpath, ".git"))
        for git_dir in git_dirs:
            for sub_dirpath, _sub_dirnames, filenames in os.walk(git_dir):
                for name in filenames:
                    try:
                        os.chmod(os.path.join(sub_dirpath, name),
                                 stat.S_IWRITE)
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

    def add_corrupt_task(self, task_id, state_text="{broken"):
        """写入一个 state.json 损坏的任务目录（H3 四分类发现用）。"""
        directory = state.task_dir(self.repo, task_id)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / state.STATE_FILENAME).write_text(
            state_text, encoding="utf-8")
        return task_id

    def add_orphaned_task(self, task_id):
        """写入一个无 state.json 的孤儿任务目录（仅 events.jsonl）。

        journal 有内容才能证明降级记账确实写进该目录而非凭空造文件。
        """
        state.task_dir(self.repo, task_id).mkdir(parents=True, exist_ok=True)
        journal_mod.append_event(
            self.repo, task_id,
            {"event": "route_selected", "mode": "solo",
             "delegability": "low", "assurance": "standard"})
        return task_id


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


class SplitLedgerFixture(TempDirFixture):
    """RB-2 夹具基座：非 git 的账本根（workspace）+ 嵌套真实 git 仓库。

    self.repo 即账本根（ZCODE_PROJECT_DIR 指向它，自身无 .git）；
    make_inner_repo 在账本根下创建嵌套真实 git 仓库。任务经
    add_active_task(repository_root=...) 绑定后，门在该仓库根上求值
    git（touched / 指纹 / 完成提交），账本（state.json / events.jsonl）
    恒在 self.repo——精确复现 dogfood 场景（workspace 非 git，真仓库
    是其子目录）。
    """

    def make_inner_repo(self, name):
        """在账本根下创建嵌套真实 git 仓库（含基线提交），返回其 Path。"""
        inner = self.repo / name
        inner.mkdir()
        run_git(inner, "init")
        run_git(inner, "config", "user.email", "gate@example.com")
        run_git(inner, "config", "user.name", "Stop Gate")
        # 固定换行行为，避免全局 autocrlf 干扰指纹的换行归一口径
        run_git(inner, "config", "core.autocrlf", "false")
        (inner / "base.txt").write_bytes(b"v1\n")
        run_git(inner, "add", ".")
        run_git(inner, "commit", "-m", "init")
        return inner

    def inner_fingerprint(self, inner, st):
        """按门同一入口真算任务证据指纹（对指定任务仓库根）。"""
        return fingerprint_mod.task_fingerprint(str(inner), st)

    def write_in(self, root, rel, data):
        """在指定根（如嵌套任务仓库）内写一个文件（自动建父目录）。"""
        target = Path(root) / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return rel


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
    观测报文废止——通过时 stdout/stderr 全空。空声明任务不参与四重
    检查（RB-2 起参与判定先于任何 git 调用），无 git 仓库也不会降级；
    本夹具仍用 git 仓库以锚定「账本根=git 根」的 legacy 主路径。
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


# —— 用例 f（RB-2 改写）：legacy 无绑定 + 账本根非 git → 结构化降级 ——
#
# 语义改写说明（RB-2 / WU-P3，新语义即本单元目标）：本类原断言旧全局
# 路径——git 失败 → 全部参与任务记 gate_degraded(reason=git_unavailable)
# → 全局早退。RB-2 拆除全局早退：legacy 任务（无 repository 绑定）在
# 账本根非 git 根时按一级子目录 .git 扫描结果降级——无候选 →
# repository_root_missing（本类）；有候选 → repository_ambiguous
# （LegacyLedgerAmbiguousTest）；git_unavailable 词汇保留给「账本根
# 本身是 git 根但 git 操作瞬时失败」的 legacy 场景。

class LegacyLedgerMissingRepositoryTest(TempDirFixture):
    """legacy 无绑定 + 账本根非 git + 无嵌套候选 → repository_root_missing。"""

    def test_legacy_non_git_ledger_without_candidate_degrades_missing(self):
        # 非 git 账本根（无 .git、一级子目录无候选）+ 参与任务 → 降级放行
        self.add_active_task(ownership_files=["src/owned/**"])
        result = run_gate("{}", self.repo)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("ENFORCEMENT DEGRADED", result.stderr)
        self.assertIn("repository_root_missing", result.stderr)
        # 降级只记该任务一条，reason 为结构化词汇的精确字符串
        events = self.journal_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event"], "gate_degraded")
        self.assertEqual(events[0]["reason"], "repository_root_missing")


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
    """evaluate 阶段结构性错误按任务隔离（RB-2 改写，原全局
    evaluation_error 降级早退拆除）：ownership 声明模式非法的任务只
    降级自身（gate_degraded，reason=evaluation_error 精确字符串），
    其余任务照常求值—— healthy 任务有违规时 block 报文只含 healthy。"""

    def test_evaluation_error_isolated_to_offending_task(self):
        bad = self.add_active_task(
            ownership_files=["src//bad/**"], task_id="eval-err-aaa")  # 空路径段
        good = self.add_active_task(
            verification_required=["pytest tests/a.py"],
            task_id="eval-ok-bbb")
        self.write("src/x.ts", b"x\n")
        result = run_gate("{}", self.repo)
        self.assertEqual(result.returncode, 0)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["decision"], "block")
        reason = payload["reason"]
        # block 报文只含 healthy 任务的违规；被降级任务不进违规清单
        self.assertIn("required parent verification is incomplete"
                      " (task %s)." % good, reason)
        self.assertNotIn(bad, reason)
        self.assertIn("ENFORCEMENT DEGRADED", result.stderr)
        # 非法模式任务：恰好一条按任务降级记账（reason 精确断言）
        bad_events = self.journal_events(bad)
        self.assertEqual(
            [e["event"] for e in bad_events], ["gate_degraded"])
        self.assertEqual(bad_events[0]["reason"], "evaluation_error")
        # healthy 任务：照常 block 记账
        good_events = self.journal_events(good)
        self.assertEqual([e["event"] for e in good_events], ["gate_blocked"])
        self.assertEqual(good_events[0]["check"], "verification_missing")


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
        # （H2 起矩阵/不变量在保存时强制：solo+high 非法，改用矩阵合法的
        # audit 路由表达同一 high-assurance 语义；review_required 已显式
        # True，完成门行为不变）
        st = state.load_state(self.repo, TID)
        st["route"] = {"mode": "audit", "delegability": "low",
                       "assurance": "high", "executor": "main",
                       "continuity": "foreground"}
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


# —— H1 完成提交：finalizing → [完成门] → completed（P0-1 生命周期封口） ——

@unittest.skipUnless(shutil.which("git"), "环境无 git 可执行，跳过 git fixture 测试")
class StopGateCompletionCommitTest(GitRepoFixture):
    """放行路径的完成提交契约（completed 的唯一提交点在门内）：
      - finalizing + 全绿 → 放行且盘上提交 completed + journal 记 completed；
      - finalizing + 违规 → block 且盘上保持 finalizing；
      - finalizing + 达续行上限 → exhausted 放行但**不**提交 completed；
      - 无声明的 finalizing 任务（不参与四重检查）→ 放行且提交 completed；
      - 非 finalizing 活动任务全绿 → 放行且状态不被改写。
    """

    CMD = "pytest tests/a.py"

    def _fresh_evidence_task(self, status):
        """建一个 verification+review 全绿且证据新鲜的指定状态任务。"""
        self.write("src/a.py", b"v1\n")
        self.add_active_task(status=status, verification_required=[self.CMD],
                             review_required=True, reviewer="reviewer-a")
        st = state.load_state(self.repo, TID)
        current = self.task_fingerprint(st)
        state.record_verification(st, self.CMD, current)
        state.record_review(st, "ship", current)
        self.set_state(st)
        return current

    def test_all_green_finalizing_committed_to_completed(self):
        current = self._fresh_evidence_task("finalizing")
        result = run_gate("{}", self.repo)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")
        # 盘上状态被门提交为 completed（模型/公共 API 无法直达，钩子提交）
        self.assertEqual(
            state.load_state(self.repo, TID)["status"], "completed")
        events = self.journal_events()
        self.assertEqual(
            [e["event"] for e in events], ["gate_passed", "completed"])
        done = events[-1]
        self.assertEqual(done["task_id"], TID)
        self.assertEqual(done["via"], "completion_gate")
        # 审计事件绑定提交时的当前证据指纹（与门同一入口算出的值）
        self.assertEqual(done["fingerprint"], current)

    def test_verification_missing_finalizing_blocked_stays_finalizing(self):
        self.add_active_task(status="finalizing",
                             verification_required=[self.CMD])
        result = run_gate("{}", self.repo)
        self.assertEqual(result.returncode, 0)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["decision"], "block")
        self.assertIn("required parent verification is incomplete",
                      payload["reason"])
        # block 后盘上保持 finalizing（修复后重新 Stop），无 completed 事件
        self.assertEqual(
            state.load_state(self.repo, TID)["status"], "finalizing")
        events = self.journal_events()
        self.assertEqual([e["event"] for e in events], ["gate_blocked"])
        self.assertEqual(events[0]["check"], "verification_missing")

    def test_gate_exhausted_passes_without_committing_completion(self):
        self.add_active_task(status="finalizing",
                             verification_required=[self.CMD])
        first = run_gate("{}", self.repo)
        second = run_gate("{}", self.repo)
        self.assertEqual(json.loads(first.stdout)["decision"], "block")
        self.assertEqual(json.loads(second.stdout)["decision"], "block")
        third = run_gate("{}", self.repo)
        self.assertEqual(third.returncode, 0)
        self.assertEqual(third.stdout, "")
        self.assertIn("ENFORCEMENT GATE EXHAUSTED", third.stderr)
        # exhausted 放行是循环安全机制、不是完成许可：不提交 completed
        self.assertEqual(
            state.load_state(self.repo, TID)["status"], "finalizing")
        self.assertEqual(
            [e["event"] for e in self.journal_events()],
            ["gate_blocked", "gate_blocked", "gate_exhausted"])

    def test_undeclared_finalizing_task_committed_on_pass(self):
        # 四项声明全空 → 不参与四重检查；但 finalizing 本身就是完成请求，
        # 放行路径照样提交 completed（提交集合不限于参与校验集合）
        self.add_active_task(status="finalizing")
        result = run_gate("{}", self.repo)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")
        self.assertEqual(
            state.load_state(self.repo, TID)["status"], "completed")
        # 不参与 → 无 gate_passed 记账；completed 事件由提交步骤记录
        events = self.journal_events()
        self.assertEqual([e["event"] for e in events], ["completed"])
        self.assertEqual(events[0]["via"], "completion_gate")
        self.assertEqual(events[0]["task_id"], TID)

    def test_non_finalizing_all_green_task_not_rewritten(self):
        self._fresh_evidence_task("executing")
        result = run_gate("{}", self.repo)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")
        # 非 finalizing 活动任务：放行但状态不被改写、无 completed 事件
        self.assertEqual(
            state.load_state(self.repo, TID)["status"], "executing")
        self.assertEqual(
            [e["event"] for e in self.journal_events()], ["gate_passed"])


# —— H2 路由不变量：门侧按 route 推导审查义务（手写 state 也逃不过） ——

@unittest.skipUnless(shutil.which("git"), "环境无 git 可执行，跳过 git fixture 测试")
class StopGateRouteDerivedReviewTest(GitRepoFixture):
    """检查 3 的条件不依赖 review.required 标志（H2/P0-2）：手写 full 路由
    + review.required=false + verdict 缺失 → block review_missing；同状态
    补 ship 裁决 + 新鲜指纹 → 放行。漏写标志无法绕过 high-assurance 审查
    约束（state.json 直接打补丁构造，绕过 save_state 校验）。"""

    FULL_ROUTE = {"mode": "full", "delegability": "high", "assurance": "high",
                  "executor": "flash-implementer", "continuity": "foreground"}

    def _handwritten_full_route_task(self):
        # 先经 save_state 落合法 solo 基座，再绕过校验把 route 补丁为
        # full——精确复现「手写 state.json 绕过 save_state 校验」场景
        self.add_active_task()
        self.patch_state_file_raw(
            TID, lambda raw: raw.update({"route": dict(self.FULL_ROUTE)}))

    def test_full_route_derived_review_blocks_then_fresh_ship_passes(self):
        self._handwritten_full_route_task()
        # 第一次 Stop：review.required 仍为 false，但 full 路由推导要求
        # 审查且 verdict 缺失 → review_missing 拦截（参与判定同样按推导）
        first = run_gate("{}", self.repo)
        self.assertEqual(first.returncode, 0)
        payload = json.loads(first.stdout)
        self.assertEqual(payload["decision"], "block")
        reason = payload["reason"]
        self.assertIn("review required but not completed", reason)
        self.assertIn("Reviewer: unassigned", reason)
        self.assertIn("runtime.state.record_review", reason)
        events = self.journal_events()
        self.assertEqual([e["event"] for e in events], ["gate_blocked"])
        self.assertEqual(events[0]["check"], "review_missing")
        self.assertEqual(events[0]["task_id"], TID)
        self.assertIsNone(events[0]["reviewer"])
        # 同状态补 ship 裁决 + 新鲜指纹（仍绕过 save_state：required 保持
        # false——放行凭 route 推导 + 证据，而非凭标志）
        st = state.load_state(self.repo, TID)
        current = self.task_fingerprint(st)

        def mutate(raw):
            raw["review"]["verdict"] = "ship"
            raw["review"]["fingerprint"] = current
        self.patch_state_file_raw(TID, mutate)
        second = run_gate("{}", self.repo)
        self.assertEqual(second.returncode, 0)
        self.assertEqual(second.stdout, "")
        self.assertEqual(second.stderr, "")
        self.assertEqual(
            [e["event"] for e in self.journal_events()],
            ["gate_blocked", "gate_passed"])


# —— H3/P0-3 发现完整性：corrupt 证据规则与报文模板（单测） ——

class StopGateCorruptEvidenceUnitTest(TempDirFixture):
    """_corrupt_requires_fail_closed 证据规则 + corrupt_state 报文模板。"""

    def _write_journal(self, task_id, events):
        for item in events:
            journal_mod.append_event(self.repo, task_id, item)

    def test_missing_journal_returns_false(self):
        self.assertFalse(
            _corrupt_requires_fail_closed(str(self.repo), "no-such-task-00"))

    def test_route_selected_evidence_truth_table(self):
        tid = "ev-route-000001"
        self.add_corrupt_task(tid)
        # solo / delegate + standard 无高保障证据
        self._write_journal(tid, [
            {"event": "task_created", "task_id": tid},
            {"event": "route_selected", "mode": "solo",
             "delegability": "low", "assurance": "standard"},
        ])
        self.assertFalse(_corrupt_requires_fail_closed(str(self.repo), tid))
        # mode 为 audit / full，或 assurance=high → True
        for index, route in enumerate((
                {"mode": "audit", "assurance": "high"},
                {"mode": "full", "assurance": "high"},
                {"mode": "solo", "delegability": "low",
                 "assurance": "high"})):
            tid_case = "ev-route-%06d" % (index + 2)
            self.add_corrupt_task(tid_case)
            self._write_journal(
                tid_case, [{"event": "route_selected", **route}])
            self.assertTrue(
                _corrupt_requires_fail_closed(str(self.repo), tid_case),
                route)

    def test_status_changed_to_finalizing_is_evidence(self):
        tid = "ev-final-000001"
        self.add_corrupt_task(tid)
        self._write_journal(tid, [
            {"event": "status_changed", "from": "reviewing",
             "to": "finalizing"},
        ])
        self.assertTrue(_corrupt_requires_fail_closed(str(self.repo), tid))
        # 非 finalizing 的迁移不算证据
        tid2 = "ev-final-000002"
        self.add_corrupt_task(tid2)
        self._write_journal(tid2, [
            {"event": "status_changed", "from": "created", "to": "executing"},
        ])
        self.assertFalse(_corrupt_requires_fail_closed(str(self.repo), tid2))

    def test_corrupt_journal_lines_skipped_evidence_still_found(self):
        # read_events 容错读：坏行跳过，后续合法证据行仍被看见
        tid = "ev-raw-0000001"
        directory = state.task_dir(self.repo, tid)
        directory.mkdir(parents=True)
        path = directory / journal_mod.JOURNAL_FILENAME
        good = json.dumps({"ts": "2026-01-01T00:00:00.000Z",
                           "event": "route_selected", "mode": "full"},
                          ensure_ascii=False)
        path.write_text("{torn line\n" + good + "\n", encoding="utf-8")
        self.assertTrue(_corrupt_requires_fail_closed(str(self.repo), tid))

    def test_block_reason_corrupt_state_template(self):
        reason = build_block_reason(
            ("corrupt-task-999", "corrupt_state",
             {"reason": "state.json 损坏：Expecting value"}),
            [("other-task-1", "ownership", {})])
        lines = reason.split("\n")
        self.assertEqual(
            lines[0], "Completion blocked: unreadable task state"
                      " (task corrupt-task-999).")
        self.assertEqual(lines[1], "- state.json 损坏：Expecting value")
        self.assertIn("The task directory was kept. Recovery options:", lines)
        self.assertTrue(any(
            line.startswith("- rebuild state.json from events.jsonl /"
                            " checkpoint evidence")
            for line in lines), lines)
        self.assertTrue(any(
            "archive the task directory" in line for line in lines), lines)
        # 其余违规任务照旧以附带行列出
        self.assertIn("Also failing in task other-task-1: ownership", lines)


# —— H3/P0-3 发现完整性：corrupt / orphaned 子进程冒烟 ——

class StopGateCorruptStateDiscoveryTest(TempDirFixture):
    """损坏 state.json 不再静默消失（无需 git：纯 corrupt 场景的
    fail-closed 证据判定只依赖 journal，发现阶段不取 touched 清单）。"""

    CORRUPT = "corrupt-task-aaa"

    def test_corrupt_active_state_does_not_silently_disappear(self):
        # 损坏 state.json + journal route_selected(mode=full) → block
        #（旧实现：发现阶段跳过 → 无其他活动任务 → 静默放行）
        self.add_corrupt_task(self.CORRUPT)
        journal_mod.append_event(
            self.repo, self.CORRUPT,
            {"event": "route_selected", "mode": "full",
             "delegability": "high", "assurance": "high"})
        result = run_gate("{}", self.repo)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.count("\n"), 1)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["decision"], "block")
        reason = payload["reason"]
        self.assertIn(
            "unreadable task state (task %s)." % self.CORRUPT, reason)
        self.assertIn("state.json", reason)  # 损坏原因简写随报文可见
        self.assertIn("Recovery options:", reason)
        # journal 记 gate_blocked：check=corrupt_state + 损坏原因
        #（此前写入的 route_selected 证据事件仍在 journal 头部）
        events = self.journal_events(self.CORRUPT)
        self.assertEqual(
            [e["event"] for e in events], ["route_selected", "gate_blocked"])
        self.assertEqual(events[-1]["check"], "corrupt_state")
        self.assertEqual(events[-1]["task_id"], self.CORRUPT)
        self.assertTrue(events[-1]["reason"].startswith("state.json 损坏："))

    def test_corrupt_with_finalizing_evidence_blocks(self):
        self.add_corrupt_task(self.CORRUPT)
        journal_mod.append_event(
            self.repo, self.CORRUPT,
            {"event": "status_changed", "from": "reviewing",
             "to": "finalizing"})
        result = run_gate("{}", self.repo)
        self.assertEqual(result.returncode, 0)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["decision"], "block")
        self.assertIn(
            "unreadable task state (task %s)." % self.CORRUPT,
            payload["reason"])
        events = self.journal_events(self.CORRUPT)
        self.assertEqual(
            [e["event"] for e in events],
            ["status_changed", "gate_blocked"])
        self.assertEqual(events[-1]["check"], "corrupt_state")

    def test_corrupt_without_high_assurance_evidence_degrades_open(self):
        # 损坏 + 无高保障证据 → exit 0 + stderr 报警 + gate_degraded
        #（reason=corrupt_state），不拦截会话
        self.add_corrupt_task(self.CORRUPT)
        result = run_gate("{}", self.repo)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("ENFORCEMENT DEGRADED", result.stderr)
        self.assertIn(self.CORRUPT, result.stderr)
        events = self.journal_events(self.CORRUPT)
        self.assertEqual([e["event"] for e in events], ["gate_degraded"])
        self.assertEqual(events[0]["reason"], "corrupt_state")

    def test_orphaned_task_never_blocks_only_warns(self):
        # 目录有 events.jsonl 但无 state.json → 永不拦截：exit 0 + 报警
        # + 该目录 journal 记 gate_degraded（reason=orphaned_task）
        orphan = self.add_orphaned_task("orphan-task-bbb")
        result = run_gate("{}", self.repo)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("ENFORCEMENT DEGRADED", result.stderr)
        self.assertIn(orphan, result.stderr)
        events = self.journal_events(orphan)
        self.assertEqual(
            [e["event"] for e in events],
            ["route_selected", "gate_degraded"])
        self.assertEqual(events[-1]["reason"], "orphaned_task")

    def test_corrupt_high_assurance_exhausted_after_two_blocks(self):
        # exhausted 机器对新 check 同样生效：corrupt 连续两次 block 后
        # 第三次 Stop 放行 + EXHAUSTED 报警 + gate_exhausted 记账
        self.add_corrupt_task(self.CORRUPT)
        journal_mod.append_event(
            self.repo, self.CORRUPT,
            {"event": "route_selected", "mode": "full",
             "delegability": "high", "assurance": "high"})
        first = run_gate("{}", self.repo)
        second = run_gate("{}", self.repo)
        self.assertEqual(json.loads(first.stdout)["decision"], "block")
        self.assertEqual(json.loads(second.stdout)["decision"], "block")
        self.assertEqual(
            [e["event"] for e in self.journal_events(self.CORRUPT)],
            ["route_selected", "gate_blocked", "gate_blocked"])
        third = run_gate("{}", self.repo)
        self.assertEqual(third.returncode, 0)
        self.assertEqual(third.stdout, "")
        self.assertIn("ENFORCEMENT GATE EXHAUSTED", third.stderr)
        events = self.journal_events(self.CORRUPT)
        self.assertEqual(
            [e["event"] for e in events],
            ["route_selected", "gate_blocked", "gate_blocked",
             "gate_exhausted"])
        self.assertEqual(events[-1]["check"], "corrupt_state")


@unittest.skipUnless(shutil.which("git"), "环境无 git 可执行，跳过 git fixture 测试")
class StopGateCorruptWithActiveTaskTest(GitRepoFixture):
    """active 与 corrupt 混合：四重检查违规优先，corrupt 追加在违规清单。"""

    CORRUPT = "corrupt-task-aaa"

    def _add_high_assurance_corrupt(self):
        self.add_corrupt_task(self.CORRUPT)
        journal_mod.append_event(
            self.repo, self.CORRUPT,
            {"event": "route_selected", "mode": "full",
             "delegability": "high", "assurance": "high"})

    def test_all_green_active_plus_low_assurance_corrupt_passes(self):
        # active 全绿 + corrupt 无高保障证据 → 放行（有报警 + 降级记账）
        self.add_active_task()  # 空声明：不参与四重检查
        self.add_corrupt_task(self.CORRUPT)
        result = run_gate("{}", self.repo)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("ENFORCEMENT DEGRADED", result.stderr)
        self.assertIn(self.CORRUPT, result.stderr)
        # active 无声明不参与 → 无 gate_passed 记账；corrupt 记 gate_degraded
        self.assertEqual(self.journal_events(), [])
        corrupt_events = self.journal_events(self.CORRUPT)
        self.assertEqual(
            [e["event"] for e in corrupt_events], ["gate_degraded"])
        self.assertEqual(corrupt_events[0]["reason"], "corrupt_state")

    def test_all_green_active_plus_high_assurance_corrupt_blocks(self):
        # active 全绿 + corrupt 高保障 → block：corrupt 违规追加在
        # 四重检查违规（此处为空）之后，成为主报文
        self.add_active_task()
        self._add_high_assurance_corrupt()
        result = run_gate("{}", self.repo)
        self.assertEqual(result.returncode, 0)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["decision"], "block")
        self.assertIn(
            "unreadable task state (task %s)." % self.CORRUPT,
            payload["reason"])
        self.assertIn("Recovery options:", payload["reason"])
        # block 记账落在 corrupt 任务；active 任务零记账（无违规也无放行）
        self.assertEqual(
            [e["event"] for e in self.journal_events(self.CORRUPT)],
            ["route_selected", "gate_blocked"])
        self.assertEqual(self.journal_events(), [])

    def test_corrupt_appended_after_active_violations(self):
        # active 任务自身违规时为主报文，corrupt 以附带行列出且
        # 记账仍落在第一个违规任务（四重检查违规先收集，corrupt 追加尾部）
        active = self.add_active_task(
            verification_required=["pytest tests/a.py"])
        self._add_high_assurance_corrupt()
        result = run_gate("{}", self.repo)
        self.assertEqual(result.returncode, 0)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["decision"], "block")
        reason = payload["reason"]
        self.assertIn(
            "required parent verification is incomplete (task %s)." % active,
            reason)
        self.assertIn(
            "Also failing in task %s: corrupt_state" % self.CORRUPT, reason)
        events = self.journal_events(active)
        self.assertEqual([e["event"] for e in events], ["gate_blocked"])
        self.assertEqual(events[0]["check"], "verification_missing")
        # corrupt 任务仅保留证据事件本身，无 block 记账（非第一个违规）
        self.assertEqual(
            [e["event"] for e in self.journal_events(self.CORRUPT)],
            ["route_selected"])


# —— H1 补充：commit_finalizing_completions 单任务失败隔离（fail-open） ——

@unittest.skipUnless(shutil.which("git"), "环境无 git 可执行，跳过 git fixture 测试")
class StopGateCommitFailureIsolationTest(GitRepoFixture):
    """提交步骤单任务失败只降级该任务：stderr 报警 + 保持 finalizing，
    后续任务照常提交 completed + completed 事件（记账异常绝不崩放行路径）。"""

    def test_single_commit_failure_does_not_block_next_task(self):
        first = self.add_active_task(
            status="finalizing", task_id="commit-fail-aaa")
        second = self.add_active_task(
            status="finalizing", task_id="commit-ok-bbb")
        real_commit = state.commit_completion

        def flaky_commit(repo_root, task_id):
            if task_id == first:
                raise RuntimeError("模拟记账失败")
            return real_commit(repo_root, task_id)

        with mock.patch.object(state, "commit_completion",
                               side_effect=flaky_commit), \
                mock.patch.object(stop_gate, "warn_stderr") as warn_mock:
            stop_gate.commit_finalizing_completions(
                state, fingerprint_mod, journal_mod, str(self.repo),
                [first, second], {})
        # 第一个任务：恰好一次 DEGRADED 报警（含任务 ID）+ 状态保持
        # finalizing + 无 completed 事件
        self.assertEqual(warn_mock.call_count, 1)
        warn_message = warn_mock.call_args[0][0]
        self.assertIn("ENFORCEMENT DEGRADED", warn_message)
        self.assertIn(first, warn_message)
        self.assertIn("left in finalizing", warn_message)
        self.assertEqual(
            state.load_state(self.repo, first)["status"], "finalizing")
        self.assertEqual(self.journal_events(first), [])
        # 第二个任务：不受牵连，正常提交 completed + completed 事件
        self.assertEqual(
            state.load_state(self.repo, second)["status"], "completed")
        events = self.journal_events(second)
        self.assertEqual([e["event"] for e in events], ["completed"])
        self.assertEqual(events[0]["via"], "completion_gate")
        self.assertEqual(events[0]["task_id"], second)


# —— H3 补充：corrupt 高保障 × 任务仓库故障交叉路径（RB-2 改写） ——

class StopGateCorruptWithGitFailureTest(TempDirFixture):
    """任务仓库故障不再压制 corrupt 高保障 block（RB-2 改写：原全局
    git_unavailable 早退拆除）。active 任务的仓库故障只降级该任务
    （reason=repository_root_missing）；deferred 高保障 corrupt 的证据
    只依赖 journal、不依赖任何 git 根，恒进统一 block 机器。"""

    CORRUPT = "corrupt-task-aaa"

    def test_repo_failure_degrades_only_active_task_corrupt_still_blocks(self):
        self.add_corrupt_task(self.CORRUPT)
        journal_mod.append_event(
            self.repo, self.CORRUPT,
            {"event": "route_selected", "mode": "full",
             "delegability": "high", "assurance": "high"})
        # 非 git 账本根（TempDirFixture 无 git init）+ 声明 ownership 的
        # active 任务 → 该任务仓库不可解析 → 按任务降级（非全局早退）
        self.add_active_task(ownership_files=["src/owned/**"])
        result = run_gate("{}", self.repo)
        self.assertEqual(result.returncode, 0)
        # corrupt 高保障照常 block（不再被仓库故障牵连）
        self.assertEqual(result.stdout.count("\n"), 1)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["decision"], "block")
        self.assertIn(
            "unreadable task state (task %s)." % self.CORRUPT,
            payload["reason"])
        # stderr 同时含任务仓库降级报警与 deferred corrupt 报警
        self.assertIn("ENFORCEMENT DEGRADED", result.stderr)
        self.assertIn(
            "deferred for enforcement (task %s)" % self.CORRUPT,
            result.stderr)
        # journal：active 任务恰好一条按任务降级（结构化 reason），
        # corrupt 任务照常进统一 block 机器（gate_blocked）
        active_events = self.journal_events()
        self.assertEqual(
            [e["event"] for e in active_events], ["gate_degraded"])
        self.assertEqual(
            active_events[0]["reason"], "repository_root_missing")
        self.assertEqual(
            [e["event"] for e in self.journal_events(self.CORRUPT)],
            ["route_selected", "gate_blocked"])
        self.assertEqual(
            self.journal_events(self.CORRUPT)[-1]["check"], "corrupt_state")


# —— RB-2 per-task 仓库求值：绑定仓库根上的真实门（⑧⑨⑩） ——

@unittest.skipUnless(shutil.which("git"), "环境无 git 可执行，跳过 git fixture 测试")
class BoundRepositoryGateTest(SplitLedgerFixture):
    """任务绑定 repository.root 后，门在该仓库根上求值（账本根非 git
    也不降级）：ownership 越界 block、静默放行、指纹绑定任务仓库。"""

    CMD = "pytest tests/a.py"

    def test_out_of_scope_in_bound_repo_blocks(self):
        # ⑧ 越界判定按任务仓库：ZCODE_PROJECT_DIR（非 git workspace）
        # 不参与求值，block 报文列任务仓库内的越界文件
        inner = self.make_inner_repo("inner-repo")
        self.add_active_task(
            ownership_files=["src/owned/**"], repository_root=str(inner))
        self.write_in(inner, "src/owned/a.ts", b"owned\n")
        self.write_in(inner, "src/other/rogue.ts", b"rogue\n")
        result = run_gate("{}", self.repo)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.count("\n"), 1)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["decision"], "block")
        reason = payload["reason"]
        self.assertIn(TID, reason)
        self.assertIn("src/other/rogue.ts", reason)
        self.assertNotIn("src/owned/a.ts", reason)
        events = self.journal_events()
        self.assertEqual([e["event"] for e in events], ["gate_blocked"])
        self.assertEqual(events[0]["check"], "ownership")
        self.assertEqual(events[0]["out_of_scope"], ["src/other/rogue.ts"])

    def test_real_gate_silent_pass_despite_nongit_project_dir(self):
        # ⑩ repo root ≠ ZCODE_PROJECT_DIR 仍走真实门：workspace 非 git
        # 不产生任何降级（旧实现此处全局 git_unavailable 早退）
        inner = self.make_inner_repo("inner-repo")
        self.add_active_task(
            ownership_files=["src/owned/**"], repository_root=str(inner))
        self.write_in(inner, "src/owned/a.ts", b"owned\n")
        result = run_gate("{}", self.repo)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")
        events = self.journal_events()
        self.assertEqual([e["event"] for e in events], ["gate_passed"])
        self.assertEqual(events[0]["tasks"], [TID])

    def test_verification_fingerprint_binds_to_task_repo(self):
        # ⑨ 指纹按任务仓库计算：任务仓库内验证后改动 → verification_stale
        inner = self.make_inner_repo("inner-repo")
        self.write_in(inner, "src/a.py", b"v1\n")
        self.add_active_task(
            verification_required=[self.CMD], repository_root=str(inner))
        st = state.load_state(self.repo, TID)
        current = self.inner_fingerprint(inner, st)
        state.record_verification(st, self.CMD, current)
        self.set_state(st)
        fresh = run_gate("{}", self.repo)
        self.assertEqual(fresh.returncode, 0)
        self.assertEqual(fresh.stdout, "")
        self.assertEqual(fresh.stderr, "")
        self.assertEqual([e["event"] for e in self.journal_events()],
                         ["gate_passed"])
        # 任务仓库内文件变化 → 旧指纹失效（workspace 侧无 git，无法影响）
        self.write_in(inner, "src/a.py", b"v2\n")
        result = run_gate("{}", self.repo)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["decision"], "block")
        self.assertIn("verification evidence is stale", payload["reason"])
        events = self.journal_events()
        self.assertEqual(events[-1]["check"], "verification_stale")
        self.assertEqual(events[-1]["recorded"], current)
        self.assertNotEqual(events[-1]["current"], current)


# —— RB-2：dogfood 场景（⑪）——非 git workspace + 绑定仓库全绿提交 ——

@unittest.skipUnless(shutil.which("git"), "环境无 git 可执行，跳过 git fixture 测试")
class DogfoodCompletionTest(SplitLedgerFixture):
    """dogfood 实证场景回归：workspace 非 git、真仓库为嵌套目录、任务
    绑定后 finalizing 全绿 → 照常提交 completed（不得出现全局
    git_unavailable 降级）。"""

    CMD = "pytest tests/a.py"

    def test_finalizing_bound_task_completes_on_nongit_workspace(self):
        inner = self.make_inner_repo("inner-repo")
        self.write_in(inner, "src/a.py", b"v1\n")
        self.add_active_task(
            status="finalizing", verification_required=[self.CMD],
            review_required=True, reviewer="reviewer-a",
            repository_root=str(inner))
        st = state.load_state(self.repo, TID)
        current = self.inner_fingerprint(inner, st)
        state.record_verification(st, self.CMD, current)
        state.record_review(st, "ship", current)
        self.set_state(st)
        result = run_gate("{}", self.repo)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        # 无任何降级报文（旧实现：全局 git_unavailable → 整个门 fail-open）
        self.assertEqual(result.stderr, "")
        # 盘上提交 completed + journal 记 gate_passed 与 completed
        self.assertEqual(
            state.load_state(self.repo, TID)["status"], "completed")
        events = self.journal_events()
        self.assertEqual(
            [e["event"] for e in events], ["gate_passed", "completed"])
        done = events[-1]
        self.assertEqual(done["task_id"], TID)
        self.assertEqual(done["via"], "completion_gate")
        self.assertEqual(done["fingerprint"], current)


# —— RB-2：双仓隔离（⑫） ——

@unittest.skipUnless(shutil.which("git"), "环境无 git 可执行，跳过 git fixture 测试")
class DualRepoIsolationTest(SplitLedgerFixture):
    """双仓各绑各仓：A 仓 dirty 文件不被 B 判 out-of-scope，反之亦然
    （若任一任务被按他仓求值，他仓文件必成越界 → block；全绿即隔离证明）。"""

    def test_dirty_files_confined_to_own_repo(self):
        repo_a = self.make_inner_repo("repo-a")
        repo_b = self.make_inner_repo("repo-b")
        task_a = self.add_active_task(
            ownership_files=["src/a/**"], task_id="iso-task-aaa",
            repository_root=str(repo_a))
        task_b = self.add_active_task(
            ownership_files=["src/b/**"], task_id="iso-task-bbb",
            repository_root=str(repo_b))
        self.write_in(repo_a, "src/a/x.ts", b"a\n")
        self.write_in(repo_b, "src/b/y.ts", b"b\n")
        result = run_gate("{}", self.repo)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")
        # 两任务各自记 gate_passed，零降级、零越界
        self.assertEqual([e["event"] for e in self.journal_events(task_a)],
                         ["gate_passed"])
        self.assertEqual([e["event"] for e in self.journal_events(task_b)],
                         ["gate_passed"])


# —— RB-2：仓库故障按任务隔离（⑬⑭） ——

@unittest.skipUnless(shutil.which("git"), "环境无 git 可执行，跳过 git fixture 测试")
class RepositoryUnavailableIsolationTest(SplitLedgerFixture):
    """绑定根 git 操作失败只降级该任务（reason=repository_unavailable），
    其余任务照常：block 报文只含违规任务，journal 记账各归各。"""

    def _make_broken_bound_task(self, task_id):
        """绑定到非 git 目录的任务（绑定根存在但 git 求值必失败）。"""
        plain = self.repo / ("plain-" + task_id)
        plain.mkdir()
        return self.add_active_task(
            ownership_files=["src/**"], task_id=task_id,
            repository_root=str(plain))

    def test_block_report_contains_only_healthy_violating_task(self):
        # ⑬ B 仓库故障降级，A 照常违规 block——报文只含 A
        repo_a = self.make_inner_repo("repo-a")
        task_a = self.add_active_task(
            ownership_files=["src/owned/**"], task_id="fail-iso-aaa",
            repository_root=str(repo_a))
        task_b = self._make_broken_bound_task("fail-iso-bbb")
        self.write_in(repo_a, "src/other/rogue.ts", b"rogue\n")
        result = run_gate("{}", self.repo)
        self.assertEqual(result.returncode, 0)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["decision"], "block")
        reason = payload["reason"]
        self.assertIn("ownership violation (task %s)." % task_a, reason)
        self.assertIn("src/other/rogue.ts", reason)
        self.assertNotIn(task_b, reason)
        self.assertIn("ENFORCEMENT DEGRADED", result.stderr)
        self.assertIn("repository_unavailable", result.stderr)
        # journal：A 记 gate_blocked，B 记一条 gate_degraded
        self.assertEqual([e["event"] for e in self.journal_events(task_a)],
                         ["gate_blocked"])
        degraded = self.journal_events(task_b)
        self.assertEqual([e["event"] for e in degraded], ["gate_degraded"])
        self.assertEqual(degraded[0]["reason"], "repository_unavailable")

    def test_degradation_journal_recorded_only_for_failed_task(self):
        # ⑭ 降级记账各归各：B 恰一条 gate_degraded，A 零条（全绿记
        # gate_passed）
        repo_a = self.make_inner_repo("repo-a")
        task_a = self.add_active_task(
            ownership_files=["src/**"], task_id="jrnl-iso-aaa",
            repository_root=str(repo_a))
        task_b = self._make_broken_bound_task("jrnl-iso-bbb")
        self.write_in(repo_a, "src/owned.ts", b"owned\n")
        result = run_gate("{}", self.repo)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        events_a = self.journal_events(task_a)
        self.assertEqual([e["event"] for e in events_a], ["gate_passed"])
        self.assertNotIn("gate_degraded", [e["event"] for e in events_a])
        events_b = self.journal_events(task_b)
        self.assertEqual(len(events_b), 1)
        self.assertEqual(events_b[0]["event"], "gate_degraded")
        self.assertEqual(events_b[0]["reason"], "repository_unavailable")


# —— RB-2：legacy 锚定与歧义不猜（⑮⑰） ——

@unittest.skipUnless(shutil.which("git"), "环境无 git 可执行，跳过 git fixture 测试")
class LegacyLedgerGitRootAnchorTest(GitRepoFixture):
    """legacy 显式锚定（⑮）：无 repository 绑定 + 账本根本身是 git 仓库
    → 行为与单仓时代完全一致（RB-2 前的全部既有用例即回归证明，此处
    显式锚定一条）。"""

    def test_unbound_task_on_git_ledger_keeps_legacy_behavior(self):
        self.add_active_task(ownership_files=["src/owned/**"])
        st = state.load_state(self.repo, TID)
        self.assertNotIn("repository", st)  # legacy 形态：整键不存在
        self.assertIsNone(state.bound_repository_root(st))
        self.assertEqual(
            state.resolve_repository_root(st, str(self.repo)),
            str(self.repo))  # 回退账本根
        self.write("src/other/rogue.ts", b"rogue\n")
        result = run_gate("{}", self.repo)
        self.assertEqual(result.returncode, 0)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["decision"], "block")
        self.assertIn("src/other/rogue.ts", payload["reason"])
        events = self.journal_events()
        self.assertEqual([e["event"] for e in events], ["gate_blocked"])
        self.assertEqual(events[0]["check"], "ownership")

    def test_legacy_git_ledger_transient_git_failure_keeps_git_unavailable(self):
        # RB-2 词汇锚定：legacy + 账本根是 git 根 + git 操作瞬时失败
        # → 保留 git_unavailable 词汇（repository_unavailable 专属绑定根）。
        # 进程内直测 collect_violations（git 故障以 mock 注入，无法经
        # 子进程 fixture 稳定复现）
        task_id = self.add_active_task(ownership_files=["src/owned/**"])
        from runtime import ownership as ownership_mod
        with mock.patch.object(
                ownership_mod, "git_touched_files",
                side_effect=ownership_mod.OwnershipError(
                    "模拟 git 瞬时故障")), \
                mock.patch.object(stop_gate, "warn_stderr"):
            violations, declared = stop_gate.collect_violations(
                state, journal_mod, ownership_mod, fingerprint_mod,
                str(self.repo), [task_id], {}, {})
        self.assertEqual(violations, [])
        self.assertEqual(declared, [])  # 降级任务不进 declared
        events = self.journal_events()
        self.assertEqual([e["event"] for e in events], ["gate_degraded"])
        self.assertEqual(events[0]["reason"], "git_unavailable")


@unittest.skipUnless(shutil.which("git"), "环境无 git 可执行，跳过 git fixture 测试")
class LegacyLedgerAmbiguousTest(SplitLedgerFixture):
    """legacy 无绑定 + 账本根非 git + 一级子目录存在嵌套仓库 →
    repository_ambiguous：stderr 列候选目录名，不自动猜、不自动绑定
    （即使只有 1 个候选），也绝不对候选仓库执行 git 求值。"""

    def test_nested_candidate_not_evaluated_degrades_ambiguous(self):
        # 嵌套仓库内故意放置「若被求值必越界」的改动：保持降级即证明
        # 未对任何嵌套仓库执行 git 求值
        nested = self.make_inner_repo("some-repo")
        self.write_in(nested, "src/rogue.ts", b"rogue\n")
        self.add_active_task(ownership_files=["src/**"])
        result = run_gate("{}", self.repo)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")  # 降级放行（未 block）
        self.assertIn("ENFORCEMENT DEGRADED", result.stderr)
        self.assertIn("some-repo", result.stderr)  # stderr 列出候选
        self.assertIn("repository_ambiguous", result.stderr)
        events = self.journal_events()
        self.assertEqual([e["event"] for e in events], ["gate_degraded"])
        self.assertEqual(events[0]["reason"], "repository_ambiguous")


# —— RB-2：降级 reason 词汇结构化精确断言（⑱） ——

@unittest.skipUnless(shutil.which("git"), "环境无 git 可执行，跳过 git fixture 测试")
class DegradedReasonVocabularyTest(SplitLedgerFixture):
    """gate_degraded 的 reason 字段是结构化词汇：三仓库降级路径逐字
    锁定（门与运维 / 文档侧共用同一词汇；evaluation_error / git_unavailable
    的精确断言见 StopGateEvaluationErrorDegradeTest 与既有 legacy 用例）。"""

    def _reasons(self):
        return [e["reason"] for e in self.journal_events()]

    def test_reason_repository_unavailable_exact(self):
        plain = self.repo / "plain-dir"
        plain.mkdir()
        self.add_active_task(ownership_files=["src/**"],
                             repository_root=str(plain))
        result = run_gate("{}", self.repo)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self._reasons(), ["repository_unavailable"])

    def test_reason_repository_root_missing_exact(self):
        self.add_active_task(ownership_files=["src/**"])
        result = run_gate("{}", self.repo)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self._reasons(), ["repository_root_missing"])

    def test_reason_repository_ambiguous_exact(self):
        self.make_inner_repo("some-repo")
        self.add_active_task(ownership_files=["src/**"])
        result = run_gate("{}", self.repo)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self._reasons(), ["repository_ambiguous"])


if __name__ == "__main__":
    unittest.main()
