#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI 扩展子命令测试（v2.4 Phase 3 P3-D/E 收敛版）。

v2.3 执行面（permits / permit-show / permit-consume / agent-runs /
wave-prepare / wave-show / manifest-show / verify-unit / verify-task）
的用例已随对应子命令退役删除（v2.4 W6）；v2.4 P3-D/E 控制面收口：
quota-exhausted / quota-resume / wake-record / wake-prompt 等子命令
连同其后端整体移除，其退役降级锚定用例一并删除。存活命令：

  1. quota-resolve：monkeypatch 假 resolver（零网络）→ JSON 四键原样
     直出；--force-refresh 透传；未知旗标 / 参数个数 → 2；resolver
     契约外异常 → 兜底 1；
  2. review-record：审查块 JSON 直出（note→findings 透传）；verdict
     词汇非法 → 2；先验证后评审顺序闸 / 任务缺失 → 1。

零网络纪律：凡涉 resolver 的用例一律 monkeypatch；scratch 目录全部
tempfile.TemporaryDirectory，绝不触碰真实账本。仅 Python 3.7 标准库。

运行：
    cd <repo_root> && python3 -m unittest tests.test_cli_extensions -v
"""

import io
import json
import os
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
from runtime import cli, journal, state, task
from runtime.quota import resolver as quota_resolver

TID = "cliext-task-1a2b3c"

CLI_PATH = (Path(__file__).resolve().parents[1] / "plugins" /
            "glm-conductor" / "runtime" / "cli.py")

# 假 resolver 返回（键冻结四键，§37 绝不含凭证）
FAKE_RESOLVED = {
    "status": "AVAILABLE",
    "source": "provider",
    "evaluated_at": "2026-08-31T00:00:00.000Z",
    "reason": "假 resolver（测试注入，零网络）",
}


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

    def dirty_file(self, rel, data):
        """在 fixture 仓库内写一个文件（自动建父目录）。"""
        target = self.repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    def events(self, name, task_id=TID):
        """读取真实 journal 并过滤事件名。"""
        return [e for e in journal.read_events(self.repo, task_id)
                if e.get("event") == name]


@unittest.skipUnless(shutil.which("git"),
                     "需要 git 可执行文件（scratch 仓库必须 git init）")
class GitCliFixture(TempDirFixture):
    """真实 git 仓库基座（v2.4 change_id 现算依赖 git）。"""

    def setUp(self):
        super().setUp()
        run_git(self.repo, "init")
        run_git(self.repo, "config", "user.email", "cliext@example.com")
        run_git(self.repo, "config", "user.name", "CLI Extensions")
        # 固定换行行为，避免全局 autocrlf 干扰 change_id 的换行归一口径
        run_git(self.repo, "config", "core.autocrlf", "false")
        # 运行时目录（state.json / events.jsonl）不入库
        self.dirty_file(".gitignore", b".glm-conductor/\n")
        self.dirty_file("base.txt", b"v1\n")
        run_git(self.repo, "add", ".")
        run_git(self.repo, "commit", "-m", "init")

    def make_v24_task(self, with_validation=True):
        """经 runtime.task 构造 v2.4 任务（可选先记录 passed 验证）。"""
        task.create_task(str(self.repo), TID, "CLI 扩展测试目标",
                         {"mode": "solo"})
        if with_validation:
            task.record_validation(str(self.repo), TID, "passed")


# —— 1. quota-resolve ——

class QuotaResolveCliTest(TempDirFixture):
    """quota-resolve：假 resolver 注入（零网络）+ 旗标透传 + 0/2/1。"""

    def test_quota_resolve_success_passthrough(self):
        with mock.patch.object(quota_resolver, "resolve_quota_status",
                               return_value=dict(FAKE_RESOLVED)) as fake:
            code, payload = run_cli("quota-resolve", str(self.repo))
        self.assertEqual(code, 0)
        self.assertEqual(payload, FAKE_RESOLVED)  # 四键原样直出
        fake.assert_called_once_with(str(self.repo), force_refresh=False)

    def test_quota_resolve_force_refresh_passthrough(self):
        with mock.patch.object(quota_resolver, "resolve_quota_status",
                               return_value=dict(FAKE_RESOLVED)) as fake:
            code, payload = run_cli("quota-resolve", str(self.repo),
                                    "--force-refresh")
        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "AVAILABLE")
        fake.assert_called_once_with(str(self.repo), force_refresh=True)

    def test_quota_resolve_usage_errors_exit_2(self):
        # 参数个数错误 → 2
        code, _payload = run_cli("quota-resolve")
        self.assertEqual(code, 2)
        # 未知旗标 → 2
        code, payload = run_cli("quota-resolve", str(self.repo), "--bogus")
        self.assertEqual(code, 2)
        self.assertIn("--force-refresh", payload["error"])

    def test_quota_resolve_unexpected_failure_exit_1(self):
        # resolver 契约外异常 → 兜底 1（错误 JSON 含异常类型名）
        with mock.patch.object(quota_resolver, "resolve_quota_status",
                               side_effect=RuntimeError("boom")):
            code, payload = run_cli("quota-resolve", str(self.repo))
        self.assertEqual(code, 1)
        self.assertIn("RuntimeError", payload["error"])


# —— 2. review-record（v2.4 改接 runtime.task.record_review） ——

class ReviewRecordCliTest(GitCliFixture):
    """review-record：审查块直出 + note 透传 + verdict 闸 2 /
    顺序与缺失闸 1。"""

    def test_review_record_happy_defaults(self):
        self.make_v24_task()
        code, payload = run_cli("review-record", str(self.repo), TID,
                                "glm-reviewer", "ship")
        self.assertEqual(code, 0)
        self.assertEqual(payload["task_id"], TID)
        self.assertEqual(payload["review"]["reviewer"], "glm-reviewer")
        self.assertEqual(payload["review"]["verdict"], "ship")
        self.assertIsNone(payload["review"]["findings"])   # 缺省 None
        self.assertTrue(payload["review"]["change_id"])    # change_id 现算
        # state 落盘 + journal 任务级事件同源
        st = state.load_state(self.repo, TID)
        self.assertEqual(st["review"]["verdict"], "ship")
        recorded = self.events("review_recorded")
        self.assertEqual(len(recorded), 1)
        self.assertEqual(recorded[0]["verdict"], "ship")

    def test_review_record_note_passthrough(self):
        self.make_v24_task()
        code, payload = run_cli("review-record", str(self.repo), TID,
                                "glm-reviewer", "fix-first",
                                "权限矩阵缺一节")
        self.assertEqual(code, 0)
        self.assertEqual(payload["review"]["verdict"], "fix-first")
        self.assertEqual(payload["review"]["findings"], "权限矩阵缺一节")

    def test_review_record_invalid_verdict_exit_2(self):
        self.make_v24_task()
        code, payload = run_cli("review-record", str(self.repo), TID,
                                "glm-reviewer", "changes_requested")
        self.assertEqual(code, 2)  # 计划侧泛称不引入（词汇闸在 CLI 侧）
        self.assertIn("verdict", payload["error"])
        self.assertEqual(self.events("review_recorded"), [])  # 零副作用

    def test_review_record_requires_validation_first_exit_1(self):
        # 先验证后评审：无 validation 记录 → runtime.task 拒绝 → 1
        self.make_v24_task(with_validation=False)
        code, payload = run_cli("review-record", str(self.repo), TID,
                                "glm-reviewer", "ship")
        self.assertEqual(code, 1)
        self.assertIn("validation", payload["error"])

    def test_review_record_missing_task_exit_1(self):
        code, payload = run_cli("review-record", str(self.repo),
                                "ghost-task", "glm-reviewer", "ship")
        self.assertEqual(code, 1)
        self.assertIn("不存在", payload["error"])

    def test_review_record_usage_errors_exit_2(self):
        # 3 个参数（缺 verdict）→ 2
        code, _payload = run_cli("review-record", str(self.repo), TID,
                                 "glm-reviewer")
        self.assertEqual(code, 2)
        # 6 个参数（超长）→ 2
        code, _payload = run_cli("review-record", str(self.repo), TID,
                                 "glm-reviewer", "ship", "note", "extra")
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
