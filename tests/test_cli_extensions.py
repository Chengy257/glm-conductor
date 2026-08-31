#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI 扩展子命令测试（v2.1 M5/M6，wu-21-15 横切收口）。

覆盖 wu-21-15 规格测试清单：每个新子命令至少 3 例（happy + 用法错
exit 2 + 失败路径 exit 1）——

  1. quota-resolve：monkeypatch 假 resolver（零网络）→ JSON 四键原样
     直出；--force-refresh 透传；未知旗标 / 参数个数 → 2；resolver
     契约外异常 → 兜底 1；
  2. verify-unit / verify-task：tempfile scratch 任务目录真实 git init
     （证据指纹依赖 git，装置做法对齐 tests/test_provenance.py）——
     happy path all_passed=true 退出码 0；exit_code 非 0 → all_passed
     =false 且 CLI 退出码 1（证据没过就是失败口径）；任务/单元缺失、
     白名单拒绝 → 1；参数个数错误 → 2；
  3. review-record：receipt JSON 直出（route/note 缺省 None）；route /
     note 透传；verdict / route 词汇非法 → 2；任务缺失 → 1；
  4. quota-exhausted：executing 任务 → waiting_quota + wake 裁决
     JSON；任务缺失 / 非执行态族 → 1；参数个数 → 2；
  5. quota-resume：显式 AVAILABLE 直通（source=explicit 零网络）→
     resumed=true；显式 EXHAUSTED 保守等待 resumed=false 仍退出码 0
     （零转态是合法结果）；status 缺省走 resolver（monkeypatch 零网
     络）；非法词汇 → 2；任务缺失 → 1；
  6. wake-record：consumed 递增 + journal quota_wake_recorded；空
     automation_id → 2；任务缺失 → 1；参数个数 → 2；
  7. wake-prompt：**stdout 纯文本**（不裹 JSON——json.loads 必炸的
     反向锚）；任务缺失 → 错误 JSON + 1；参数个数 → 2；真实子进程
     UTF-8 冒烟（Windows 显式 utf-8 纪律）；
  8. wave-prepare 缺省值改 None 的行为锚定（wu-21-15 设计决策）：
     monkeypatch resolver 返回 AVAILABLE → exit 0 且 journal 落
     quota_resolved；返回 EXHAUSTED → exit 1（证明缺省真的走 resolver
     而非「CLI 永远 AVAILABLE」的乐观兜底）。

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
from runtime import agent_run, cli, dispatch_wave, journal, provenance, state
from runtime import task_manager, work_unit
from runtime.quota import resolver as quota_resolver

TID = "cliext-task-1a2b3c"
ROUTE = {"mode": "delegate", "delegability": "high", "assurance": "standard",
         "executor": "flash-implementer", "continuity": "foreground"}

# allow 类命令（classify_bash 无规则命中 → None）：scratch git 仓库内恒 exit 0
VERIFY_CMD = "git status --porcelain"
# exit 3 的失败命令（cmd.exe shell=True 下 python3 已实测可解析）
FAIL_CMD = 'python3 -c "import sys;sys.exit(3)"'

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


def run_cli_text(*args):
    """调用 cli.main 捕获 stdout 原文（wake-prompt 纯文本路径专用），
    返回 (退出码, 文本)。"""
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        code = cli.main(list(args))
    return code, buffer.getvalue()


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


def make_unit(uid, owned, verification, status="ready"):
    """构造 §61 形状的 work unit（new_work_unit 真实构造）。"""
    return work_unit.new_work_unit(
        uid, "CLI 扩展场景单元 %s" % uid, executor="flash-implementer",
        ownership=list(owned), verification=list(verification),
        status=status)


class TempDirFixture(unittest.TestCase):
    """tempdir 基座：目录/状态文件写入 + Windows .git 只读位清理。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._force_cleanup)
        self.repo = Path(self._tmp.name)

    def _force_cleanup(self):
        """解除 git 只读对象后清理临时目录（模式复用 test_provenance）。"""
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

    def make_task(self, units=(), *, ownership_files=("src/**",),
                  verification_required=(VERIFY_CMD,), status="executing",
                  max_workers=2):
        """写入任务 state.json（new_task_state 构造 + validate 全量校验）。"""
        st = state.new_task_state(
            TID, "CLI 扩展测试目标", dict(ROUTE),
            ownership_files=list(ownership_files),
            verification_required=list(verification_required),
            status=status)
        st["work_units"] = list(units)
        st["dispatch"] = {"max_workers": max_workers, "active": []}
        state.save_state(self.repo, st)
        return st

    def events(self, name, task_id=TID):
        """读取真实 journal 并过滤事件名。"""
        return [e for e in journal.read_events(self.repo, task_id)
                if e.get("event") == name]


@unittest.skipUnless(shutil.which("git"),
                     "需要 git 可执行文件（scratch 仓库必须 git init）")
class GitCliFixture(TempDirFixture):
    """真实 git 仓库基座（verify/review 证据指纹依赖 git）。"""

    def setUp(self):
        super().setUp()
        run_git(self.repo, "init")
        run_git(self.repo, "config", "user.email", "cliext@example.com")
        run_git(self.repo, "config", "user.name", "CLI Extensions")
        # 固定换行行为，避免全局 autocrlf 干扰指纹的换行归一口径
        run_git(self.repo, "config", "core.autocrlf", "false")
        # 运行时目录（state.json / events.jsonl / receipts）不入库
        self.dirty_file(".gitignore", b".glm-conductor/\n")
        self.dirty_file("base.txt", b"v1\n")
        run_git(self.repo, "add", ".")
        run_git(self.repo, "commit", "-m", "init")

    def record_invocation(self, tool_use_id, reviewer="glm-reviewer",
                          task_id=TID):
        """RB-21-02 真实链路 setup：经 agent_run.record_reviewer_invocation
        直落一条 reviewer_invoked 事件（review-record 回验链的前置账本）。

        本文件不在 RB-21-02 声明的自有文件集内——此处为全量测试保持
        全绿的最小夹具增补（既有断言零改动/只朝更严方向）。"""
        payload = {
            "hook_event_name": "PostToolUse", "tool_name": "Agent",
            "tool_input": {"subagent_type": reviewer,
                           "prompt": "review %s"
                                     % agent_run.review_marker_for(task_id)},
            "tool_use_id": tool_use_id,
            "tool_response": {"agentId": "agent-review-fixture"}}
        return agent_run.record_reviewer_invocation(
            str(self.repo), task_id, payload)


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


# —— 2. verify-unit ——

class VerifyUnitCliTest(GitCliFixture):
    """verify-unit：happy / 失败口径 exit 1 / 白名单与缺失 exit 1 / 用法 2。"""

    def test_verify_unit_happy_path_exit_0(self):
        self.make_task([make_unit("u1", ("src/a/**",), (VERIFY_CMD,))])
        code, payload = run_cli("verify-unit", str(self.repo), TID, "u1")
        self.assertEqual(code, 0)
        self.assertEqual(payload["unit"], "u1")
        self.assertTrue(payload["all_passed"])
        self.assertEqual(payload["exit_codes"], {VERIFY_CMD: 0})
        self.assertEqual(len(payload["receipts"]), 1)
        receipt = payload["receipts"][0]
        self.assertEqual(receipt["scope"], "unit")
        self.assertEqual(receipt["exit_code"], 0)
        # 成功路径写单元级证据事件（RB-1 完成证据门消费面）
        verification_events = [e for e in self.events("verification")
                               if e.get("unit") == "u1"]
        self.assertEqual(len(verification_events), 1)

    def test_verify_unit_nonzero_exit_all_passed_false_exit_1(self):
        # 证据没过就是失败口径：all_passed=False 且 CLI 退出码 1
        self.make_task([make_unit("u1", ("src/a/**",), (FAIL_CMD,))])
        code, payload = run_cli("verify-unit", str(self.repo), TID, "u1")
        self.assertEqual(code, 1)
        self.assertFalse(payload["all_passed"])
        self.assertEqual(payload["exit_codes"], {FAIL_CMD: 3})
        self.assertEqual(payload["receipts"][0]["exit_code"], 3)

    def test_verify_unit_missing_task_exit_1(self):
        code, payload = run_cli("verify-unit", str(self.repo),
                                "ghost-task", "u1")
        self.assertEqual(code, 1)
        self.assertIn("不存在", payload["error"])

    def test_verify_unit_missing_unit_exit_1(self):
        self.make_task([make_unit("u1", ("src/a/**",), (VERIFY_CMD,))])
        code, payload = run_cli("verify-unit", str(self.repo), TID, "ghost")
        self.assertEqual(code, 1)
        self.assertIn("ghost", payload["error"])

    def test_verify_unit_whitelist_reject_exit_1(self):
        # 显式 command 不在 required 清单 → D4 白名单闸 → 1
        self.make_task([make_unit("u1", ("src/a/**",), (VERIFY_CMD,))])
        code, payload = run_cli("verify-unit", str(self.repo), TID, "u1",
                                "git diff --stat")
        self.assertEqual(code, 1)
        self.assertIn("git diff --stat", payload["error"])

    def test_verify_unit_usage_error_exit_2(self):
        code, _payload = run_cli("verify-unit", str(self.repo), TID)
        self.assertEqual(code, 2)


# —— 3. verify-task ——

class VerifyTaskCliTest(GitCliFixture):
    """verify-task：与 verify-unit 同构（task 作用域）。"""

    def test_verify_task_happy_path_exit_0(self):
        self.make_task([make_unit("u1", ("src/a/**",), (VERIFY_CMD,))])
        code, payload = run_cli("verify-task", str(self.repo), TID)
        self.assertEqual(code, 0)
        self.assertEqual(payload["task"], TID)
        self.assertTrue(payload["all_passed"])
        self.assertEqual(payload["exit_codes"], {VERIFY_CMD: 0})
        self.assertEqual(payload["receipts"][0]["scope"], "task")
        # 成功路径同步任务级完成门证据（record_verification 后落盘）
        st = state.load_state(self.repo, TID)
        completed = st["verification"]["completed"]
        self.assertIn(VERIFY_CMD, [c for c in completed])

    def test_verify_task_missing_task_exit_1(self):
        code, payload = run_cli("verify-task", str(self.repo), "ghost-task")
        self.assertEqual(code, 1)
        self.assertIn("不存在", payload["error"])

    def test_verify_task_whitelist_reject_exit_1(self):
        # 任务级白名单 = state.verification.required；未声明命令恒拒
        self.make_task()
        code, payload = run_cli("verify-task", str(self.repo), TID,
                                "git diff --stat")
        self.assertEqual(code, 1)
        self.assertIn("git diff --stat", payload["error"])

    def test_verify_task_usage_error_exit_2(self):
        code, _payload = run_cli("verify-task", str(self.repo))
        self.assertEqual(code, 2)


# —— 4. review-record ——

class ReviewRecordCliTest(GitCliFixture):
    """review-record：receipt 直出 + route/note 透传 + 词汇闸 2 / 缺失 1。"""

    def test_review_record_happy_defaults(self):
        self.make_task()
        # RB-21-02：receipt 前置 runtime 观察到的 invocation 账本
        self.record_invocation("call_review_1")
        code, payload = run_cli("review-record", str(self.repo), TID,
                                "glm-reviewer", "ship", "call_review_1")
        self.assertEqual(code, 0)
        self.assertEqual(payload["kind"], "review")
        self.assertEqual(payload["reviewer"], "glm-reviewer")
        self.assertEqual(payload["verdict"], "ship")
        self.assertIsNone(payload["route"])       # 缺省 None
        self.assertIsNone(payload["note"])        # 缺省 None
        self.assertEqual(payload["tool_use_id"], "call_review_1")
        # durable receipt 落盘 + journal 同字段事件 + state 快速路径同步
        receipts = [e for e in self.events("review_receipt")]
        self.assertEqual(len(receipts), 1)
        self.assertEqual(receipts[0]["verdict"], "ship")
        st = state.load_state(self.repo, TID)
        self.assertEqual(st["review"]["verdict"], "ship")

    def test_review_record_route_and_note_passthrough(self):
        self.make_task()
        self.record_invocation("call_r2")  # RB-21-02 前置 invocation 账本
        code, payload = run_cli("review-record", str(self.repo), TID,
                                "glm-reviewer", "fix-first", "call_r2",
                                "delegate", "权限矩阵缺一节")
        self.assertEqual(code, 0)
        self.assertEqual(payload["route"], "delegate")
        self.assertEqual(payload["note"], "权限矩阵缺一节")

    def test_review_record_invalid_verdict_exit_2(self):
        self.make_task()
        code, payload = run_cli("review-record", str(self.repo), TID,
                                "glm-reviewer", "changes_requested",
                                "call_r3")
        self.assertEqual(code, 2)  # 计划侧泛称不引入（wu-21-13 锁定）
        self.assertIn("verdict", payload["error"])
        self.assertEqual(self.events("review_receipt"), [])  # 零副作用

    def test_review_record_invalid_route_exit_2(self):
        self.make_task()
        code, payload = run_cli("review-record", str(self.repo), TID,
                                "glm-reviewer", "ship", "call_r4", "sneaky")
        self.assertEqual(code, 2)
        self.assertIn("route", payload["error"])

    def test_review_record_missing_task_exit_1(self):
        code, payload = run_cli("review-record", str(self.repo),
                                "ghost-task", "glm-reviewer", "ship",
                                "call_r5")
        self.assertEqual(code, 1)
        self.assertIn("不存在", payload["error"])

    def test_review_record_usage_errors_exit_2(self):
        # 4 个参数（缺 tool_use_id）→ 2
        code, _payload = run_cli("review-record", str(self.repo), TID,
                                 "glm-reviewer", "ship")
        self.assertEqual(code, 2)
        # 8 个参数（超长）→ 2
        code, _payload = run_cli("review-record", str(self.repo), TID,
                                 "glm-reviewer", "ship", "call_r6", "solo",
                                 "note", "extra")
        self.assertEqual(code, 2)


# —— 5. quota-exhausted ——

class QuotaExhaustedCliTest(TempDirFixture):
    """quota-exhausted：EXHAUSTED 转态链 JSON + 状态族闸 1 / 用法 2。"""

    def test_quota_exhausted_happy_transitions_to_waiting_quota(self):
        self.make_task([make_unit("u1", ("src/a/**",), (VERIFY_CMD,))])
        code, payload = run_cli("quota-exhausted", str(self.repo), TID)
        self.assertEqual(code, 0)
        # 默认 manual 授权：不自动恢复、不产 prompt、不转 waiting_user
        self.assertEqual(payload["task_status"], "waiting_quota")
        self.assertEqual(payload["waiting_units"], ["u1"])
        self.assertIsNone(payload["recommended_resume_at"])  # None 不虚构
        self.assertFalse(payload["wake"]["required"])
        self.assertIsNone(payload["wake"]["prompt"])
        self.assertFalse(payload["wake"]["budget_exhausted"])
        # 盘上转态 + journal 记账真实发生
        st = state.load_state(self.repo, TID)
        self.assertEqual(st["status"], "waiting_quota")
        self.assertEqual(len(self.events("quota_waiting")), 1)

    def test_quota_exhausted_missing_task_exit_1(self):
        code, payload = run_cli("quota-exhausted", str(self.repo),
                                "ghost-task")
        self.assertEqual(code, 1)
        self.assertIn("不存在", payload["error"])

    def test_quota_exhausted_non_execution_status_exit_1(self):
        # decomposed 不在执行态族（QUOTA_WAIT_TASK_STATUSES）→ 1
        self.make_task(status="decomposed")
        code, payload = run_cli("quota-exhausted", str(self.repo), TID)
        self.assertEqual(code, 1)
        self.assertIn("执行态", payload["error"])

    def test_quota_exhausted_usage_error_exit_2(self):
        code, _payload = run_cli("quota-exhausted", str(self.repo))
        self.assertEqual(code, 2)


# —— 6. quota-resume ——

class QuotaResumeCliTest(TempDirFixture):
    """quota-resume：显式四态直通 / 缺省走 resolver（monkeypatch）/
    保守等待仍 0 / 词汇闸 2 / 缺失 1。"""

    def _waiting_task(self):
        """构造 waiting_quota 任务（真实转态链：executing → 挂起）。"""
        self.make_task([make_unit("u1", ("src/a/**",), (VERIFY_CMD,))])
        task_manager.handle_quota_exhausted(self.repo, TID)
        st = state.load_state(self.repo, TID)
        self.assertEqual(st["status"], "waiting_quota")  # 前置自检

    def test_quota_resume_explicit_available_resumes(self):
        self._waiting_task()
        code, payload = run_cli("quota-resume", str(self.repo), TID,
                                "AVAILABLE")
        self.assertEqual(code, 0)
        self.assertTrue(payload["resumed"])
        self.assertEqual(payload["status"], "AVAILABLE")
        self.assertIsNone(payload["recommended_resume_at"])
        st = state.load_state(self.repo, TID)
        self.assertEqual(st["status"], "executing")
        unit = next(u for u in st["work_units"] if u["id"] == "u1")
        self.assertEqual(unit["status"], "ready")
        resumed = self.events("quota_resumed")
        self.assertEqual(len(resumed), 1)
        self.assertEqual(resumed[0]["source"], "explicit")  # 直通零解析

    def test_quota_resume_default_goes_through_resolver(self):
        # status 缺省 → resolver 解析（monkeypatch 零网络）；source 记
        # resolver 层级来源而非 explicit；RB-21-01 起强制刷新
        # （force_refresh=True——wake 后不得信任新鲜缓存）
        self._waiting_task()
        with mock.patch.object(quota_resolver, "resolve_quota_status",
                               return_value=dict(FAKE_RESOLVED)) as fake:
            code, payload = run_cli("quota-resume", str(self.repo), TID)
        self.assertEqual(code, 0)
        self.assertTrue(payload["resumed"])
        fake.assert_called_once_with(str(self.repo), force_refresh=True)
        resumed = self.events("quota_resumed")
        self.assertEqual(resumed[0]["source"], "provider")

    def test_quota_resume_exhausted_conservative_wait_exit_0(self):
        # EXHAUSTED 保守等待：resumed=false、零转态——合法结果，退出码 0
        self._waiting_task()
        code, payload = run_cli("quota-resume", str(self.repo), TID,
                                "EXHAUSTED")
        self.assertEqual(code, 0)
        self.assertFalse(payload["resumed"])
        st = state.load_state(self.repo, TID)
        self.assertEqual(st["status"], "waiting_quota")  # 零转态

    def test_quota_resume_invalid_status_exit_2(self):
        code, payload = run_cli("quota-resume", str(self.repo), TID, "MEGA")
        self.assertEqual(code, 2)
        self.assertIn("quota-resume", payload["error"])

    def test_quota_resume_missing_task_exit_1(self):
        code, payload = run_cli("quota-resume", str(self.repo), "ghost-task")
        self.assertEqual(code, 1)
        self.assertIn("不存在", payload["error"])

    def test_quota_resume_usage_error_exit_2(self):
        code, _payload = run_cli("quota-resume", str(self.repo))
        self.assertEqual(code, 2)


# —— 7. wake-record ——

class WakeRecordCliTest(TempDirFixture):
    """wake-record：窗口扣减记账 + 空串闸 2 / 缺失 1 / 用法 2。"""

    def test_wake_record_increments_consumed(self):
        self.make_task()
        code, payload = run_cli("wake-record", str(self.repo), TID,
                                "aut-wake-0001", "2026-09-01T00:00:00Z")
        self.assertEqual(code, 0)
        self.assertEqual(payload["consumed_quota_windows"], 1)
        self.assertEqual(payload["remaining_quota_windows"], 0)
        self.assertEqual(payload["automation_id"], "aut-wake-0001")
        self.assertEqual(payload["fires_at"], "2026-09-01T00:00:00Z")
        recorded = self.events("quota_wake_recorded")
        self.assertEqual(len(recorded), 1)
        self.assertEqual(recorded[0]["consumed"], 1)
        # 二次调用纯递增（与 automation 存活解耦，无回滚）
        code, payload = run_cli("wake-record", str(self.repo), TID,
                                "aut-wake-0002", "2026-09-02T00:00:00Z")
        self.assertEqual((code, payload["consumed_quota_windows"]), (0, 2))

    def test_wake_record_empty_automation_id_exit_2(self):
        self.make_task()
        code, payload = run_cli("wake-record", str(self.repo), TID,
                                "", "2026-09-01T00:00:00Z")
        self.assertEqual(code, 2)
        self.assertIn("automation_id", payload["error"])
        self.assertEqual(self.events("quota_wake_recorded"), [])  # 零副作用

    def test_wake_record_missing_task_exit_1(self):
        code, payload = run_cli("wake-record", str(self.repo), "ghost-task",
                                "aut-wake-x", "2026-09-01T00:00:00Z")
        self.assertEqual(code, 1)
        self.assertIn("不存在", payload["error"])

    def test_wake_record_usage_error_exit_2(self):
        code, _payload = run_cli("wake-record", str(self.repo), TID,
                                 "aut-wake-x")
        self.assertEqual(code, 2)


# —— 8. wake-prompt（纯文本输出） ——

class WakePromptCliTest(TempDirFixture):
    """wake-prompt：纯文本（不裹 JSON）+ 缺失 1 / 用法 2 / 子进程 UTF-8。"""

    def test_wake_prompt_plain_text_not_json(self):
        self.make_task([make_unit("u1", ("src/a/**",), (VERIFY_CMD,))])
        # 真实转态链先把 u1 挂到 waiting_quota（prompt 只列等待单元）
        task_manager.handle_quota_exhausted(self.repo, TID)
        code, text = run_cli_text("wake-prompt", str(self.repo), TID)
        self.assertEqual(code, 0)
        # 自足中文模板：含唤醒头、task_id 与等待单元
        self.assertIn("GLM CONDUCTOR 额度唤醒", text)
        self.assertIn(TID, text)
        self.assertIn("u1", text)
        # 反向锚：不裹 JSON——json.loads 必炸
        with self.assertRaises(ValueError):
            json.loads(text)

    def test_wake_prompt_missing_task_error_json_exit_1(self):
        code, payload = run_cli("wake-prompt", str(self.repo), "ghost-task")
        self.assertEqual(code, 1)
        self.assertIn("不存在", payload["error"])

    def test_wake_prompt_usage_error_exit_2(self):
        code, _payload = run_cli("wake-prompt", str(self.repo))
        self.assertEqual(code, 2)

    def test_wake_prompt_real_subprocess_utf8_smoke(self):
        # 真实子进程（显式 encoding="utf-8"）：Windows 管道下纯文本零乱码
        self.make_task([make_unit("u1", ("src/a/**",), (VERIFY_CMD,))])
        proc = subprocess.run(
            [sys.executable, str(CLI_PATH), "wake-prompt",
             str(self.repo), TID],
            text=True, capture_output=True, encoding="utf-8",
            errors="replace")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stderr, "")
        self.assertIn("GLM CONDUCTOR 额度唤醒", proc.stdout)


# —— 9. wave-prepare 缺省值改 None（wu-21-15 设计决策锚定） ——

class WavePrepareDefaultCliTest(TempDirFixture):
    """wave-prepare 无 quota_status → resolver（monkeypatch 零网络），
    消除「CLI 永远 AVAILABLE」的乐观兜底。"""

    def _task(self):
        self.make_task([make_unit("u1", ("src/a/**",), (VERIFY_CMD,)),
                        make_unit("u2", ("src/b/**",), (VERIFY_CMD,))])

    def test_default_resolves_via_resolver_available_exit_0(self):
        self._task()
        with mock.patch.object(quota_resolver, "resolve_quota_status",
                               return_value=dict(FAKE_RESOLVED)) as fake:
            code, payload = run_cli("wave-prepare", str(self.repo), TID)
        self.assertEqual(code, 0)
        self.assertEqual(payload["units"], ["u1", "u2"])
        self.assertEqual(payload["worker_budget"], 2)
        self.assertEqual(len(payload["permits"]), 2)
        fake.assert_called_once_with(str(self.repo))
        # 解析事实入账：quota_resolved 事件 + wave 记录记实际四态
        resolved = self.events("quota_resolved")
        self.assertEqual(len(resolved), 1)
        self.assertEqual(resolved[0]["status"], "AVAILABLE")
        wave = state.load_state(self.repo, TID)["dispatch"]["waves"][0]
        self.assertEqual(wave["quota_status"], "AVAILABLE")

    def test_default_resolves_via_resolver_exhausted_exit_1(self):
        # resolver 判 EXHAUSTED → 决策未批准（waiting_quota 口径）→ 1：
        # 证明缺省真的走 resolver，而非隐式 AVAILABLE 乐观兜底
        self._task()
        exhausted = dict(FAKE_RESOLVED, status="EXHAUSTED",
                         source="provider")
        with mock.patch.object(quota_resolver, "resolve_quota_status",
                               return_value=exhausted):
            code, payload = run_cli("wave-prepare", str(self.repo), TID)
        self.assertEqual(code, 1)
        self.assertIn("quota EXHAUSTED", payload["error"])

    def test_explicit_available_still_passes_through(self):
        # 显式四态直通（零解析零事件）——既有行为不回退
        self._task()
        with mock.patch.object(quota_resolver, "resolve_quota_status") as fake:
            code, payload = run_cli("wave-prepare", str(self.repo), TID,
                                    "AVAILABLE")
        self.assertEqual(code, 0)
        self.assertEqual(payload["units"], ["u1", "u2"])
        fake.assert_not_called()  # 直通：resolver 未被触达
        self.assertEqual(self.events("quota_resolved"), [])

    def test_default_missing_task_exit_1(self):
        # 任务缺失先于 resolver（_require_state 在解析之前）
        with mock.patch.object(quota_resolver, "resolve_quota_status") as fake:
            code, payload = run_cli("wave-prepare", str(self.repo),
                                    "ghost-task")
        self.assertEqual(code, 1)
        self.assertIn("不存在", payload["error"])
        fake.assert_not_called()


if __name__ == "__main__":
    unittest.main()
