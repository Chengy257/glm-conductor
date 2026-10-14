#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI 错误分类单元测试（v2.6.0 WP-C P3 卫生第 1 项，规格 §6.1）。

已发布契约（runtime/cli.py 模块头「输出与退出码契约」）：
  - 盘上 state.json 结构性拒绝（JSON 损坏解析 / schema 残缺 /
    任务标识错位 / save_state 校验闸）= 校验拒绝 → 退出码 2；
  - 任务生命周期拒绝（任务缺失 / v2.3 遗留 / 终态冻结 / 状态迁移
    矩阵外 / AF-04 守卫前置失配）= 异常 → 退出码 1；
  两档互斥。历史缺陷：_TaskRejected / _QuotaRejected 各薄壳把
  load_state 的损坏 ValueError 一律包成 Rejected（退出码 1），与
  模块头声称的 2 矛盾——本文件钉住分类收口后的映射，并断言损坏
  盘面绝不被任何拒绝路径触碰（「损坏不被覆盖」不变式的 CLI 侧）。

覆盖：
  1. 损坏 JSON × 任务层薄壳（record-validation / task-complete
     / task-fail / task-cancel / exit-waiting / task-create
     / task-record-workflow-run / quota-wait / quota-resume-decision
     / quota-resume-authorize / quota-resume-confirm + automation 三壳
     bind/clear/preflight——str(exc) 历史缺陷的行为级回归锚）→ 退出码 2、
     错误消息含「损坏」、盘面逐字节不变；
  2. schema 残缺（合法 JSON 缺必填键）与账本错位（归一任务标识与
     目录不一致）→ 退出码 2；
  3. 生命周期拒绝对照组：任务缺失 / 终态冻结 → 退出码 1（与结构
     性 2 档在同一命令上互斥可见）；
  4. _is_structural_state_rejection 谓词真值表（前缀锚点，无盘面
     依赖）；
  5. USAGE / docstring / 实现三方一致（无盘面依赖）：USAGE 子命令
     集合 == _dispatch 分发集合；模块 docstring 退出码契约如实
     记载结构性（2）/ 生命周期（1）两档及其互斥；main docstring
     记载分类收口路径；14 个薄壳（五类 Rejected 包装面）在 Rejected
     包装前先经分类放行（模块级调用点恰 def + 14）。

零网络纪律：quota 观测一律 mock.patch 注入 fake detail；scratch
目录全部 tempfile.TemporaryDirectory，绝不触碰真实账本。仅 Python
3 标准库。

运行：
    cd <repo_root> && python3 -m unittest tests.test_cli_error_classification -v
"""

import inspect
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "glm-conductor"))
from runtime import cli, state, task  # noqa: E402

from runtime.quota import resolver as quota_resolver  # noqa: E402

TID = "errclass-task-1a2b3c"

CORRUPT_BYTES = b"this is not json"


def run_cli(*args):
    """调用 cli.main 并捕获 stdout，返回 (退出码, 解析后 JSON)。"""
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        code = cli.main(list(args))
    return code, json.loads(buffer.getvalue())


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
        """解除 git 只读对象后清理临时目录（v2.3 时代既定模式）。"""
        git_dir = os.path.join(self._tmp.name, ".git")
        if os.path.isdir(git_dir):
            for dirpath, _dirnames, filenames in os.walk(git_dir):
                for name in filenames:
                    try:
                        os.chmod(os.path.join(dirpath, name), stat.S_IWRITE)
                    except OSError:
                        pass
        self._tmp.cleanup()


@unittest.skipUnless(shutil.which("git"),
                     "需要 git 可执行文件（scratch 仓库必须 git init）")
class GitCliFixture(TempDirFixture):
    """真实 git 仓库基座（与 test_cli_lifecycle 同款装置）。"""

    def setUp(self):
        super().setUp()
        run_git(self.repo, "init")
        run_git(self.repo, "config", "user.email", "errclass@example.com")
        run_git(self.repo, "config", "user.name", "Error Classification")
        run_git(self.repo, "config", "core.autocrlf", "false")
        (self.repo / ".gitignore").write_bytes(b".glm-conductor/\n")
        (self.repo / "base.txt").write_bytes(b"v1\n")
        run_git(self.repo, "add", ".")
        run_git(self.repo, "commit", "-m", "init")

    def make_v24_task(self, task_id=TID):
        """经 runtime.task 构造 v2.4 任务。"""
        return task.create_task(str(self.repo), task_id, "错误分类测试任务",
                                {"mode": "solo"})

    def corrupt_task(self, payload=CORRUPT_BYTES, task_id=TID):
        """构造 v2.4 任务后绕过全部写入口落盘损坏字节，留存损坏字节
        本身作不变式锚（拒绝路径之后盘面须仍逐字节等于这份损坏盘面）。"""
        self.make_v24_task(task_id)
        path = state.state_path(str(self.repo), task_id)
        path.write_bytes(payload)
        self._disk_before = path.read_bytes()
        return path

    def assert_structural_exit_2(self, *args, task_id=TID):
        """断言：退出码 2 + 错误消息 + 损坏盘面逐字节不被触碰。"""
        code, payload = run_cli(*args)
        self.assertEqual(code, 2,
                         "结构性拒绝应退出码 2，得到 %r（%r）"
                         % (code, payload))
        self.assertIn("error", payload)
        path = state.state_path(str(self.repo), task_id)
        self.assertEqual(path.read_bytes(), self._disk_before,
                         "拒绝路径触碰了损坏盘面（损坏不被覆盖不变式）")
        return payload


class CorruptJsonClassificationCliTest(GitCliFixture):
    """损坏 JSON × 各 Rejected 薄壳（14 个分类收口面）→ 校验拒绝
    （退出码 2）。"""

    def test_record_validation_exit_2(self):
        self.corrupt_task()
        payload = self.assert_structural_exit_2(
            "record-validation", str(self.repo), TID, "passed")
        self.assertIn("损坏", payload["error"])

    def test_task_terminal_trio_exit_2(self):
        for index, cmd in enumerate(("task-complete", "task-fail",
                                     "task-cancel")):
            task_id = "errclass-term-%d" % index
            with self.subTest(cmd=cmd):
                self.corrupt_task(task_id=task_id)
                payload = self.assert_structural_exit_2(
                    cmd, str(self.repo), task_id, task_id=task_id)
                self.assertIn("损坏", payload["error"])

    def test_exit_waiting_exit_2(self):
        self.corrupt_task()
        payload = self.assert_structural_exit_2(
            "exit-waiting", str(self.repo), TID)
        self.assertIn("损坏", payload["error"])

    def test_task_create_existing_corrupt_exit_2(self):
        # 任务已存在 + 盘面损坏：损坏解析先于「已存在」拒绝——
        # 按已发布契约归结构性（退出码 2），绝不当作生命周期拒绝
        self.corrupt_task()
        payload = self.assert_structural_exit_2(
            "task-create", str(self.repo), TID, "目标", "solo", "standard")
        self.assertIn("损坏", payload["error"])

    def test_task_record_workflow_run_exit_2(self):
        # solo 任务无 AF-04 前置，损坏解析在守卫检查之前触发
        self.corrupt_task()
        payload = self.assert_structural_exit_2(
            "task-record-workflow-run", str(self.repo), TID, "run-x")
        self.assertIn("损坏", payload["error"])

    def test_quota_wait_exit_2(self):
        self.corrupt_task()
        with mock.patch.object(
                quota_resolver, "resolve_quota_detail",
                return_value={"status": "EXHAUSTED", "source": "provider",
                              "snapshot": None, "fetched_at": None}):
            payload = self.assert_structural_exit_2(
                "quota-wait", str(self.repo), TID)
        self.assertIn("损坏", payload["error"])

    def test_quota_resume_decision_exit_2(self):
        self.corrupt_task()
        with mock.patch.object(
                quota_resolver, "resolve_quota_detail",
                return_value={"status": "EXHAUSTED", "source": "provider",
                              "snapshot": None, "fetched_at": None}):
            payload = self.assert_structural_exit_2(
                "quota-resume-decision", str(self.repo), TID)
        self.assertIn("损坏", payload["error"])

    def test_quota_resume_authorize_exit_2(self):
        self.corrupt_task()
        payload = self.assert_structural_exit_2(
            "quota-resume-authorize", str(self.repo), TID, "2")
        self.assertIn("损坏", payload["error"])

    def test_quota_resume_confirm_exit_2(self):
        self.corrupt_task()
        payload = self.assert_structural_exit_2(
            "quota-resume-confirm", str(self.repo), TID)
        self.assertIn("损坏", payload["error"])


    def test_automation_trio_corrupt_state_exit_2(self):
        # str(exc) 历史缺陷的回归锚：automation 三壳的分类分支必须
        # 真实可达（损坏盘面穿透为退出码 2，而非包装成生命周期 1）。
        # 损坏落盘一次，三壳顺序执行（各自拒绝写盘、盘面保持逐字节不变）
        self.corrupt_task()
        for cmd_args in (
                ("quota-automation-bind", str(self.repo), TID, "an-id"),
                ("quota-automation-clear", str(self.repo), TID),
                ("quota-continuity-preflight", str(self.repo), TID),
        ):
            with self.subTest(cmd=cmd_args[0]):
                payload = self.assert_structural_exit_2(*cmd_args)
                self.assertIn("损坏", payload["error"])


class SchemaAndIdentityClassificationCliTest(GitCliFixture):
    """schema 残缺 / 账本错位（合法 JSON 的结构性问题）→ 退出码 2。"""

    def test_schema_amputation_exit_2(self):
        # 合法 JSON 但缺全部必填键：validate_state 非空 → schema 非法
        self.corrupt_task(payload=json.dumps({"task_id": TID})
                          .encode("utf-8"))
        code, payload = run_cli("record-validation", str(self.repo), TID,
                                "passed")
        self.assertEqual(code, 2)
        self.assertIn("schema 非法", payload["error"])

    def test_identity_mismatch_exit_2(self):
        # 盘上归一任务标识与目标目录不一致：错位账本拒绝作为事务基底
        self.make_v24_task()
        path = state.state_path(str(self.repo), TID)
        misplaced = state.load_state(str(self.repo), TID)
        misplaced["task_id"] = "errclass-other-9a9a"
        path.write_bytes(json.dumps(misplaced).encode("utf-8"))
        self._disk_before = path.read_bytes()
        code, payload = run_cli("task-complete", str(self.repo), TID)
        self.assertEqual(code, 2)
        self.assertIn("归一任务标识", payload["error"])
        self.assertEqual(path.read_bytes(), self._disk_before)


class LifecycleContrastCliTest(GitCliFixture):
    """对照组：生命周期拒绝恒归退出码 1，与结构性 2 档互斥。"""

    def test_missing_task_exit_1_not_2(self):
        code, payload = run_cli("record-validation", str(self.repo),
                                "errclass-ghost-1", "passed")
        self.assertEqual(code, 1)
        self.assertIn("不存在", payload["error"])
        code, payload = run_cli("quota-resume-authorize", str(self.repo),
                                "errclass-ghost-1", "2")
        self.assertEqual(code, 1)
        self.assertIn("不存在", payload["error"])

    def test_terminal_freeze_exit_1_not_2(self):
        self.make_v24_task()
        task.complete(str(self.repo), TID)
        code, payload = run_cli("task-complete", str(self.repo), TID)
        self.assertEqual(code, 0)  # 同终态重放放行（生命周期幂等面）
        # 跨终态迁移拒绝：终态冻结是生命周期拒绝（1），不是结构性（2）
        self.make_v24_task("errclass-cross-2")
        task.complete(str(self.repo), "errclass-cross-2")
        code, payload = run_cli("task-fail", str(self.repo),
                                "errclass-cross-2")
        self.assertEqual(code, 1)
        self.assertIn("终态", payload["error"])

    def test_same_command_corrupt_2_vs_missing_1(self):
        # 同一命令在两类盘面上的互斥分档
        self.corrupt_task()
        code, payload = run_cli("exit-waiting", str(self.repo), TID)
        self.assertEqual(code, 2)
        self.assertIn("损坏", payload["error"])
        code, payload = run_cli("exit-waiting", str(self.repo),
                                "errclass-ghost-3")
        self.assertEqual(code, 1)
        self.assertIn("不存在", payload["error"])


class StructuralClassifierPredicateTest(unittest.TestCase):
    """_is_structural_state_rejection 谓词真值表（无盘面依赖）。"""

    def test_structural_prefixes_match(self):
        for message in (
                "state.json 损坏，无法解析（x）：Expecting value",
                "state 事务：盘上 state.json 归一任务标识 'a' 与目标 "
                "'b' 不一致",
                "state 事务：盘上 state.json schema 非法，拒绝在其上"
                "写入（损坏不被覆盖）：缺 task_id",
                "state 非法，无法保存：缺 goal"):
            with self.subTest(message=message):
                self.assertTrue(cli._is_structural_state_rejection(
                    ValueError(message)))

    def test_lifecycle_and_foreign_values_do_not_match(self):
        for exc in (
                ValueError("create_task：任务 t 已存在（盘上已有 "
                           "state.json），拒绝重建"),
                ValueError("任务 t 不存在（无 state.json），无法执行"
                           "生命周期操作"),
                ValueError("task-complete：任务 t 已是终态 'completed'"),
                ValueError("state 事务：任务 t 不存在（无 state.json），"
                           "拒绝读写"),
                RuntimeError("state.json 损坏，无法解析"),  # 非 ValueError
                KeyError("state.json 损坏，无法解析")):
            with self.subTest(exc=type(exc).__name__):
                self.assertFalse(cli._is_structural_state_rejection(exc))


class UsageDocstringConsistencyTest(unittest.TestCase):
    """USAGE / docstring / 实现三方一致（v2.6.0 WP-C 第 1 项的文档面，
    规格「映射在 USAGE/docstring/实现三者一致并测试」）：
      - USAGE 列出的子命令集合与 _dispatch 实际分发集合完全一致
        （含 v24-record-run 的 legacy 标注解析）；
      - 模块 docstring 退出码契约如实记载结构性（退出码 2）与任务
        生命周期（退出码 1）两档及其互斥，并点名分类机制
        _is_structural_state_rejection；
      - main docstring 记载分类收口路径与两档互斥；
      - 14 个薄壳（五类 Rejected 包装面：_TaskRejected /
        _QuotaRejected / _AutomationRejected / _PinRejected /
        _ReviewRejected）在包装 Rejected 前先经分类放行；
        模块级调用点恰 def 签名 + 14。
    纯源面断言（inspect.getsource / 字符串扫描），零盘面依赖。
    """

    # 分类收口面：14 个薄壳 → 各自的 Rejected 包装类
    # （record-validation / task-complete / task-fail / task-cancel /
    #   exit-waiting / task-create / task-record-workflow-run /
    #   quota-wait / quota-resume-decision / quota-resume-authorize /
    #   quota-resume-confirm——终端三态共用 _task_terminal 一壳）
    SHELL_CLASSIFICATIONS = (
        ("_quota_wait", "_QuotaRejected"),
        ("_quota_resume_authorize", "_QuotaRejected"),
        ("_quota_resume_decision", "_QuotaRejected"),
        ("_quota_resume_confirm", "_QuotaRejected"),
        ("_task_create", "_TaskRejected"),
        ("_record_validation", "_TaskRejected"),
        ("_task_terminal", "_TaskRejected"),
        ("_exit_waiting", "_TaskRejected"),
        ("_task_record_workflow_run", "_TaskRejected"),
        ("_quota_automation_bind", "_AutomationRejected"),
        ("_quota_automation_clear", "_AutomationRejected"),
        ("_quota_continuity_preflight", "_AutomationRejected"),
        ("_review_record", "_ReviewRejected"),
        ("_worker_model_pin", "_PinRejected"),
    )

    @staticmethod
    def _normalize(text):
        """docstring 断言用：折叠全部空白（换行 + 缩进 → 单空格）。"""
        return " ".join(text.split())

    def usage_commands(self):
        """解析 USAGE 的子命令序列（竖线分隔段的首 token）。"""
        prefix = "用法: python3 plugins/glm-conductor/runtime/cli.py "
        self.assertTrue(cli.USAGE.startswith(prefix),
                        "USAGE 前缀漂移：%r" % cli.USAGE[:60])
        body = cli.USAGE[len(prefix):]
        # <passed|failed> 占位符内含竖线，先中性化再切分
        body = body.replace("<passed|failed>", "<passed-or-failed>")
        commands = []
        for segment in body.split("|"):
            segment = segment.strip()
            segment = re.sub(r"^\[legacy [^\]]*\]\s*", "", segment)
            commands.append(segment.split(" ", 1)[0])
        return commands

    def dispatch_commands(self):
        """从 _dispatch 源面提取实际分发的子命令列表。"""
        source = inspect.getsource(cli._dispatch)
        return re.findall(r'cmd == "([a-z0-9-]+)"', source)

    def test_usage_matches_dispatch_command_set(self):
        usage = self.usage_commands()
        dispatch = self.dispatch_commands()
        self.assertTrue(dispatch, "分发集合解析为空——解析锚失效")
        self.assertEqual(len(usage), len(set(usage)),
                         "USAGE 子命令重复：%r" % usage)
        self.assertEqual(sorted(usage), sorted(dispatch),
                         "USAGE 与 _dispatch 子命令集合漂移")

    def test_module_docstring_documents_two_tier_contract(self):
        doc = self._normalize(cli.__doc__ or "")
        for needle in (
                # 退出码 2 条目：盘上 state 结构性拒绝如实归档
                "盘上 state.json 结构性 拒绝——JSON 损坏解析 / schema "
                "残缺 / 任务标识错位 / save_state 校验闸",
                "也绝不作为任务 生命周期拒绝报出",
                "经各薄壳以 _is_structural_state_rejection 分类收口",
                # 退出码 1 条目：生命周期拒绝恒归本档、两档互斥
                "任务生命周期拒绝（含状态迁移矩阵外 来源 / 终态冻结）"
                "恒归本档，与退出码 2 的盘面结构性拒绝 互斥"):
            with self.subTest(needle=needle):
                self.assertIn(self._normalize(needle), doc,
                              "模块 docstring 缺少退出码契约锚")

    def test_main_docstring_documents_classification_route(self):
        doc = self._normalize(cli.main.__doc__ or "")
        for needle in (
                "经 _is_structural_state_rejection 在 _TaskRejected / "
                "_QuotaRejected / _AutomationRejected / _PinRejected / "
                "_ReviewRejected 各薄壳分类收口",
                "恒归 Rejected 类 → 1，两档互斥"):
            with self.subTest(needle=needle):
                self.assertIn(self._normalize(needle), doc,
                              "main docstring 缺少分类收口锚")

    def test_structural_classification_precedes_rejected_wrap(self):
        for name, wrap in self.SHELL_CLASSIFICATIONS:
            with self.subTest(shell=name):
                source = inspect.getsource(getattr(cli, name))
                self.assertIn("_is_structural_state_rejection(exc)", source,
                              "%s 未做结构性分类" % name)
                self.assertLess(
                    source.index("_is_structural_state_rejection(exc)"),
                    source.index("raise %s" % wrap),
                    "%s 的分类放行必须先于 %s 包装" % (name, wrap))
                self.assertIn("raise  # 盘上 state 结构性拒绝", source)

    def test_classification_call_sites_exactly_def_plus_shells(self):
        module_source = inspect.getsource(cli)
        # 调用点计数 = len(SHELL_CLASSIFICATIONS)；出现任何
        # _is_structural_state_rejection(<非 exc 形态>)（如 str(exc)——
        # 历史缺陷：谓词收异常实例，传 str 恒 False 分支死代码）即拒
        shells = len(self.SHELL_CLASSIFICATIONS)
        self.assertEqual(
            module_source.count("_is_structural_state_rejection(exc)"),
            shells + 1,
            "分类调用点（%d 薄壳 + def 1）面漂移" % shells)
        for bad in module_source.split("\n"):
            if "_is_structural_state_rejection(" in bad \
                    and "_is_structural_state_rejection(exc)" not in bad \
                    and not bad.lstrip().startswith("def ") \
                    and "docstring" not in bad:
                self.fail("非 exc 实参形态的分类调用：%r" % bad.strip())


if __name__ == "__main__":
    unittest.main()
