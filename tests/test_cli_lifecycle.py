#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI v2.5.2 Phase 1 单元测试（W1 reset_at 观测口径 + W2 任务生命周期
子命令面）。

覆盖：
  1. _quota_observation_view / quota-wait / quota-resume-decision 的
     reset_at 观测口径（W1，v2.6.0 WP-R2 起为单一观测口径，规格
     §2.2）：snapshot 先经 parser.evaluate 求逐窗四态，reset_at 取
     已识别窗口中最早的有效未来观测 reset（UTC instant 显式解析，
     与窗口 AVAILABLE/PRESSURE/EXHAUSTED/UNKNOWN 状态无关）——
     无有效未来 reset / snapshot None → None（绝不虚构）；decision
     对所有 action 无条件透传该值（含 no-op）；
  2. task-create（W2）：词汇先闸（goal / mode / assurance → 2）；
     --dag 双形态（节点数组 / {nodes: [...]} 对象）读入 + 节点形状与
     图校验聚合（→ 2）；runtime.task 拒绝（任务已存在 → 1）；成功
     落 route_selected 事件并输出 {task_id, status, route}；
  3. record-validation（W2）：status 词汇 / 空串先闸（→ 2）；--summary
     至多一次、--commands 可重复逐条透传；change_id 现算；任务缺失
     → 1；成功落 validation_recorded 事件；
  4. task-complete / task-fail / task-cancel（W2）：终态迁移 + 专属
     事件；同终态重放放行（0）；跨终态拒绝（1）；任务缺失（1）；
  5. exit-waiting（W2）：「→ active」合法来源（waiting_quota /
     waiting_user / blocked，v2.6.0 WP-C 矩阵事实钉子）→ active 落
     exit 事件；active 幂等；终态拒绝（1）。

零网络纪律：凡涉 resolver 的用例一律 monkeypatch（fake detail 注入）；
scratch 目录全部 tempfile.TemporaryDirectory，绝不触碰真实账本。仅
Python 3 标准库。

运行：
    cd <repo_root> && python3 -m unittest tests.test_cli_lifecycle -v
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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "glm-conductor"))
from runtime import cli, journal, messages, state, task, work_unit
from runtime.quota import resolver as quota_resolver

TID = "clilife-task-1a2b3c"

CLI_PATH = (Path(__file__).resolve().parents[1] / "plugins" /
            "glm-conductor" / "runtime" / "cli.py")


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
        run_git(self.repo, "config", "user.email", "clilife@example.com")
        run_git(self.repo, "config", "user.name", "CLI Lifecycle")
        # 固定换行行为，避免全局 autocrlf 干扰 change_id 的换行归一口径
        run_git(self.repo, "config", "core.autocrlf", "false")
        # 运行时目录（state.json / events.jsonl）不入库
        self.dirty_file(".gitignore", b".glm-conductor/\n")
        self.dirty_file("base.txt", b"v1\n")
        run_git(self.repo, "add", ".")
        run_git(self.repo, "commit", "-m", "init")

    def make_v24_task(self, task_id=TID):
        """经 runtime.task 构造 v2.4 任务。"""
        return task.create_task(str(self.repo), task_id, "生命周期 CLI 测试",
                                {"mode": "solo"})


# —— 1. 单一观测 reset_at 口径（v2.6.0 WP-R2，规格 §2.2/§2.6） ——

# 窗口时间锚（相对当前 UTC 动态生成，保证「有效未来」不随日期漂移）：
# T1 = five_hour 窗 reset（更早、未来）；T2 = weekly 窗 reset（更晚、未来）
_NOW = datetime.now(timezone.utc)


def _iso_ms_z(moment):
    """aware datetime → 毫秒精度 Z 形式串（§27/provider reset_at 形态）。"""
    return moment.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


_T1 = _iso_ms_z(_NOW + timedelta(hours=5))
_T2 = _iso_ms_z(_NOW + timedelta(days=4))


def _make_window(kind, used, reset_at):
    """构造 §27 形状窗口（used=None 即数据不完整 → UNKNOWN）。"""
    remaining = None if used is None else round(100 - used, 2)
    return {"kind": kind, "used_percent": used,
            "remaining_percent": remaining, "reset_at": reset_at}


def _fake_detail(windows, status="EXHAUSTED"):
    """构造 resolve_quota_detail 的假返回（零网络注入）。"""
    return {
        "status": status,
        "source": "provider",
        "snapshot": {
            "provider": "zai", "plan_generation": None, "plan_level": None,
            "fetched_at": "2026-10-11T04:00:00.000Z", "status": None,
            "windows": windows,
        },
        "fetched_at": "2026-10-11T04:00:00.000Z",
    }


def _patch_detail(windows, status="EXHAUSTED"):
    return mock.patch.object(
        quota_resolver, "resolve_quota_detail",
        return_value=_fake_detail(windows, status=status))


class QuotaObservationSingleResetTest(TempDirFixture):
    """reset_at = 已识别窗口中最早的有效未来观测 reset（v2.6.0 单一
    观测口径，规格 §2.2）：与窗口状态无关、只认有效未来、取更早。"""

    def test_earliest_future_wins_over_later_weekly(self):
        # five_hour 耗尽（T1 更早）+ weekly 健康（T2 更晚）→ 取 T1
        # （最早未来当选；EXHAUSTED 不再独占透传资格）
        windows = [_make_window("five_hour", 100.0, _T1),
                   _make_window("weekly", 10.0, _T2)]
        with _patch_detail(windows):
            view = cli._quota_observation_view(str(self.repo))
        self.assertEqual(view["reset_at"], _T1)

    def test_available_still_passes_earliest_future(self):
        # 全 AVAILABLE 也无条件透传（v2.5.2 旧口径为 None——已退役）
        windows = [_make_window("five_hour", 10.0, _T1),
                   _make_window("weekly", 40.0, _T2)]
        with _patch_detail(windows, status="AVAILABLE"):
            view = cli._quota_observation_view(str(self.repo))
        self.assertEqual(view["reset_at"], _T1)
        self.assertEqual(view["status"], "AVAILABLE")

    def test_multi_window_takes_earliest_not_max(self):
        # 双窗均耗尽 → 取更早的 T1（旧阻塞窗口径取字典序最大值 T2——退役）
        windows = [_make_window("five_hour", 100.0, _T1),
                   _make_window("weekly", 100.0, _T2)]
        with _patch_detail(windows):
            view = cli._quota_observation_view(str(self.repo))
        self.assertEqual(view["reset_at"], _T1)

    def test_pressure_window_passes_reset_through(self):
        # 剩余 5% → PRESSURE：观测 reset 照常透传（状态不参与选择）
        windows = [_make_window("five_hour", 95.0, _T1)]
        with _patch_detail(windows, status="PRESSURE"):
            view = cli._quota_observation_view(str(self.repo))
        self.assertEqual(view["reset_at"], _T1)

    def test_unknown_window_with_valid_reset_visible(self):
        # 数据不完整 → UNKNOWN：只要观测 reset 有效且在未来仍可见
        # （规格 §2.6 第 4 条：UNKNOWN 不授权 resume，但不遮蔽时间）
        windows = [_make_window("five_hour", None, _T1)]
        with _patch_detail(windows, status="UNKNOWN"):
            view = cli._quota_observation_view(str(self.repo))
        self.assertEqual(view["reset_at"], _T1)

    def test_snapshot_none_reset_none(self):
        with mock.patch.object(
                quota_resolver, "resolve_quota_detail",
                return_value={"status": "UNKNOWN", "source": "none",
                              "snapshot": None, "fetched_at": None}):
            view = cli._quota_observation_view(str(self.repo))
        self.assertIsNone(view["reset_at"])
        self.assertEqual(view["status"], "UNKNOWN")


class QuotaWaitResetPassthroughCliTest(GitCliFixture):
    """quota-wait 落盘的 last_observation.reset_at 与 decision 透传值
    均为最早的有效未来观测 reset（view / decision 同源同值）。"""

    def test_quota_wait_stores_observed_reset(self):
        self.make_v24_task()
        windows = [_make_window("five_hour", 100.0, _T1),
                   _make_window("weekly", 10.0, _T2)]
        with _patch_detail(windows):
            code, payload = run_cli("quota-wait", str(self.repo), TID)
        self.assertEqual(code, 0)
        self.assertEqual(payload["quota_observation"]["reset_at"], _T1)
        st = state.load_state(self.repo, TID)
        self.assertEqual(
            st["quota_resume"]["last_observation"]["reset_at"], _T1)

    def test_quota_resume_decision_passthrough_reset(self):
        self.make_v24_task()
        task.enter_waiting_quota(str(self.repo), TID)
        windows = [_make_window("five_hour", 100.0, _T1),
                   _make_window("weekly", 10.0, _T2)]
        with _patch_detail(windows):
            code, payload = run_cli("quota-resume-decision", str(self.repo),
                                    TID)
        self.assertEqual(code, 0)
        self.assertEqual(payload["decision"]["action"], "remain-waiting")
        self.assertEqual(payload["decision"]["reset_at"], _T1)
        self.assertEqual(payload["quota_observation"]["reset_at"], _T1)


# —— 2. task-create（W2） ——

class TaskCreateCliTest(GitCliFixture):
    """task-create：词汇先闸 2 / --dag 双形态与校验 2 / 已存在 1 / 成功 0。"""

    def test_happy_defaults(self):
        code, payload = run_cli("task-create", str(self.repo), TID,
                                "修复跨窗口唤醒", "delegate", "high")
        self.assertEqual(code, 0)
        self.assertEqual(payload, {"task_id": TID, "status": "active",
                                   "route": {"mode": "delegate",
                                             "assurance": "high"}})
        st = state.load_state(self.repo, TID)
        self.assertEqual(st["status"], "active")
        self.assertEqual(len(self.events("route_selected")), 1)

    def test_dag_object_form_persists_nodes(self):
        nodes = [work_unit.new_node("n1", "实现睡眠模板",
                                    ownership=["plugins/**"]),
                 work_unit.new_node("n2", "更新契约文档", depends_on=["n1"],
                                    ownership=["docs/**"])]
        dag_path = self.repo / "dag.json"
        dag_path.write_text(json.dumps({"nodes": nodes}), encoding="utf-8")
        code, payload = run_cli("task-create", str(self.repo), TID, "目标",
                                "delegate", "standard",
                                "--dag", str(dag_path))
        self.assertEqual(code, 0)
        st = state.load_state(self.repo, TID)
        self.assertEqual([n["id"] for n in st["dag"]], ["n1", "n2"])

    def test_dag_bare_array_form(self):
        nodes = [work_unit.new_node("n1", "目标一", ownership=["src/**"])]
        dag_path = self.repo / "dag.json"
        dag_path.write_text(json.dumps(nodes), encoding="utf-8")
        code, _payload = run_cli("task-create", str(self.repo), TID, "目标",
                                 "solo", "standard", "--dag", str(dag_path))
        self.assertEqual(code, 0)
        self.assertEqual(len(state.load_state(self.repo, TID)["dag"]), 1)

    def test_dag_file_errors_exit_2(self):
        # 文件不存在 / 非法 JSON / nodes 非 array / 节点缺必填键 → 2
        missing = self.repo / "missing.json"
        code, payload = run_cli("task-create", str(self.repo), TID, "目标",
                                "solo", "standard", "--dag", str(missing))
        self.assertEqual(code, 2)
        self.assertIn(
            messages.CLI_TASK_CREATE_DAG_READ_ERROR.split("%r")[0],
            payload["error"])
        bad = self.repo / "bad.json"
        bad.write_text("{not-json", encoding="utf-8")
        code, payload = run_cli("task-create", str(self.repo), TID, "目标",
                                "solo", "standard", "--dag", str(bad))
        self.assertEqual(code, 2)
        self.assertIn(
            messages.CLI_TASK_CREATE_DAG_PARSE_ERROR.split("(%s)")[0],
            payload["error"])
        shape = self.repo / "shape.json"
        shape.write_text(json.dumps({"nodes": "x"}), encoding="utf-8")
        code, _payload = run_cli("task-create", str(self.repo), TID, "目标",
                                 "solo", "standard", "--dag", str(shape))
        self.assertEqual(code, 2)
        badnode = self.repo / "badnode.json"
        badnode.write_text(json.dumps({"nodes": [{"id": "n1"}]}),
                           encoding="utf-8")
        code, payload = run_cli("task-create", str(self.repo), TID, "目标",
                                "solo", "standard", "--dag", str(badnode))
        self.assertEqual(code, 2)
        self.assertIn("missing required key", payload["error"])

    def test_vocabulary_and_goal_gates_exit_2(self):
        for args in (("delegate", "ultra"),      # assurance 词汇外
                     ("pair", "standard"),       # mode 词汇外
                     ("solo", "")):              # 空取值
            code, payload = run_cli("task-create", str(self.repo), TID,
                                    "目标", *args)
            self.assertEqual(code, 2, "args=%r 应退出码 2" % (args,))
            self.assertIn("error", payload)
        code, _payload = run_cli("task-create", str(self.repo), TID,
                                 "", "solo", "standard")
        self.assertEqual(code, 2)  # 空 goal
        self.assertEqual(state.load_state(self.repo, TID), None)  # 零落盘

    def test_duplicate_task_exit_1(self):
        self.make_v24_task()
        code, payload = run_cli("task-create", str(self.repo), TID, "目标",
                                "solo", "standard")
        self.assertEqual(code, 1)
        self.assertIn(messages.TASK_CREATE_ALREADY_EXISTS % TID,
                      payload["error"])

    def test_usage_errors_exit_2(self):
        code, _payload = run_cli("task-create", str(self.repo), TID, "目标",
                                 "solo")  # 少 assurance
        self.assertEqual(code, 2)
        code, _payload = run_cli("task-create", str(self.repo), TID, "目标",
                                 "solo", "standard", "--dag")
        self.assertEqual(code, 2)
        code, _payload = run_cli("task-create", str(self.repo), TID, "目标",
                                 "solo", "standard", "--bogus", "x")
        self.assertEqual(code, 2)


# —— 3. record-validation（W2） ——

class RecordValidationCliTest(GitCliFixture):
    """record-validation：词汇 / 空串先闸 2；透传；事件；任务缺失 1。"""

    def test_happy_defaults(self):
        self.make_v24_task()
        code, payload = run_cli("record-validation", str(self.repo), TID,
                                "passed")
        self.assertEqual(code, 0)
        self.assertEqual(payload["task_id"], TID)
        validation = payload["validation"]
        self.assertEqual(validation["status"], "passed")
        self.assertTrue(validation["change_id"])  # 现算非空
        self.assertIsNone(validation["summary"])
        self.assertIsNone(validation["commands"])
        self.assertEqual(len(self.events("validation_recorded")), 1)

    def test_summary_and_repeated_commands_passthrough(self):
        self.make_v24_task()
        code, payload = run_cli(
            "record-validation", str(self.repo), TID, "failed",
            "--summary", "定向束两处红",
            "--commands", "python3 -m unittest tests.test_cli_lifecycle",
            "--commands", "ruff check plugins")
        self.assertEqual(code, 0)
        self.assertEqual(payload["validation"]["summary"], "定向束两处红")
        self.assertEqual(payload["validation"]["commands"],
                         ["python3 -m unittest tests.test_cli_lifecycle",
                          "ruff check plugins"])

    def test_vocabulary_and_empty_gates_exit_2(self):
        self.make_v24_task()
        code, _payload = run_cli("record-validation", str(self.repo), TID,
                                 "green")
        self.assertEqual(code, 2)
        code, _payload = run_cli("record-validation", str(self.repo), TID,
                                 "passed", "--summary", "")
        self.assertEqual(code, 2)
        code, _payload = run_cli("record-validation", str(self.repo), TID,
                                 "passed", "--commands", "")
        self.assertEqual(code, 2)
        self.assertEqual(self.events("validation_recorded"), [])  # 零副作用

    def test_missing_task_exit_1(self):
        code, payload = run_cli("record-validation", str(self.repo),
                                "ghost-task", "passed")
        self.assertEqual(code, 1)
        self.assertIn(messages.TASK_MISSING % "ghost-task",
                      payload["error"])

    def test_usage_errors_exit_2(self):
        self.make_v24_task()
        # 位置参数不足
        code, _payload = run_cli("record-validation", str(self.repo), TID)
        self.assertEqual(code, 2)
        # 旗标缺取值（不成对）
        code, _payload = run_cli("record-validation", str(self.repo), TID,
                                 "passed", "--summary")
        self.assertEqual(code, 2)
        # --summary 重复
        code, _payload = run_cli("record-validation", str(self.repo), TID,
                                 "passed", "--summary", "a",
                                 "--summary", "b")
        self.assertEqual(code, 2)
        # 未知旗标
        code, _payload = run_cli("record-validation", str(self.repo), TID,
                                 "passed", "--bogus", "x")
        self.assertEqual(code, 2)


# —— 4. task-complete / task-fail / task-cancel（W2） ——

class TaskTerminalCliTest(GitCliFixture):
    """三个终态命令：迁移 + 事件；同终态重放放行；跨终态 / 缺失 → 1。"""

    def test_complete_transitions_and_event(self):
        self.make_v24_task()
        code, payload = run_cli("task-complete", str(self.repo), TID)
        self.assertEqual(code, 0)
        self.assertEqual(payload, {"task_id": TID, "status": "completed"})
        self.assertEqual(state.load_state(self.repo, TID)["status"],
                         "completed")
        recorded = self.events("task_completed")
        self.assertEqual(len(recorded), 1)
        self.assertEqual(recorded[0]["from"], "active")

    def test_same_terminal_replay_allowed_exit_0(self):
        self.make_v24_task()
        run_cli("task-complete", str(self.repo), TID)
        code, _payload = run_cli("task-complete", str(self.repo), TID)
        self.assertEqual(code, 0)  # 同终态重放放行（幂等）

    def test_cross_terminal_refused_exit_1(self):
        self.make_v24_task()
        run_cli("task-complete", str(self.repo), TID)
        code, payload = run_cli("task-fail", str(self.repo), TID)
        self.assertEqual(code, 1)
        self.assertIn(messages.TASK_TRANSITION_NO_TARGETS,
                      payload["error"])

    def test_fail_and_cancel_terminal_statuses(self):
        self.make_v24_task("clilife-fail-1")
        code, payload = run_cli("task-fail", str(self.repo), "clilife-fail-1")
        self.assertEqual((code, payload["status"]), (0, "failed"))
        self.assertEqual(
            len(self.events("task_failed", "clilife-fail-1")), 1)
        self.make_v24_task("clilife-cancel-1")
        code, payload = run_cli("task-cancel", str(self.repo),
                                "clilife-cancel-1")
        self.assertEqual((code, payload["status"]), (0, "cancelled"))
        self.assertEqual(
            len(self.events("task_cancelled", "clilife-cancel-1")), 1)

    def test_missing_task_exit_1(self):
        for cmd in ("task-complete", "task-fail", "task-cancel"):
            code, payload = run_cli(cmd, str(self.repo), "ghost-task")
            self.assertEqual(code, 1, "cmd=%r 应退出码 1" % cmd)
            self.assertIn(messages.TASK_MISSING % "ghost-task",
                      payload["error"])

    def test_usage_errors_exit_2(self):
        self.make_v24_task()
        for cmd in ("task-complete", "task-fail", "task-cancel"):
            code, _payload = run_cli(cmd, str(self.repo))
            self.assertEqual(code, 2)
            code, _payload = run_cli(cmd, str(self.repo), TID, "extra")
            self.assertEqual(code, 2)


# —— 5. exit-waiting（W2） ——

class ExitWaitingCliTest(GitCliFixture):
    """exit-waiting：「→ active」合法来源（waiting_quota /
    waiting_user / blocked）均放行落 exit 事件；active 幂等；终态 1。"""

    def test_exit_from_waiting_quota(self):
        self.make_v24_task()
        task.enter_waiting_quota(str(self.repo), TID)
        code, payload = run_cli("exit-waiting", str(self.repo), TID)
        self.assertEqual(code, 0)
        self.assertEqual(payload, {"task_id": TID, "status": "active"})
        directions = [e.get("direction")
                      for e in self.events("waiting_quota")]
        self.assertEqual(directions, ["enter", "exit"])

    def test_idempotent_from_active(self):
        self.make_v24_task()
        code, payload = run_cli("exit-waiting", str(self.repo), TID)
        self.assertEqual((code, payload["status"]), (0, "active"))
        self.assertEqual(self.events("waiting_quota"), [])  # 无迁移零事件

    def test_exit_from_waiting_user_and_blocked(self):
        # 矩阵事实钉子（WP-C P3：TASK_TRANSITIONS["waiting_user"] 与
        # ["blocked"] 均含 active）——exit-waiting 对两来源放行并落
        # exit 事件；文档不再误称 waiting_user 为「矩阵外来源」
        for index, source_status in enumerate(("waiting_user", "blocked")):
            task_id = "clilife-exit-%d" % index
            with self.subTest(source=source_status):
                self.make_v24_task(task_id)
                task.set_status(str(self.repo), task_id, source_status)
                code, payload = run_cli("exit-waiting", str(self.repo),
                                        task_id)
                self.assertEqual((code, payload["status"]), (0, "active"))
                directions = [e.get("direction") for e
                              in self.events("waiting_quota", task_id)]
                self.assertEqual(directions, ["exit"])

    def test_terminal_task_refused_exit_1(self):
        self.make_v24_task()
        task.complete(str(self.repo), TID)
        code, payload = run_cli("exit-waiting", str(self.repo), TID)
        self.assertEqual(code, 1)
        self.assertIn(messages.TASK_TRANSITION_NO_TARGETS,
                      payload["error"])

    def test_missing_task_exit_1(self):
        code, payload = run_cli("exit-waiting", str(self.repo), "ghost-task")
        self.assertEqual(code, 1)
        self.assertIn(messages.TASK_MISSING % "ghost-task",
                      payload["error"])

    def test_usage_errors_exit_2(self):
        self.make_v24_task()
        code, _payload = run_cli("exit-waiting", str(self.repo))
        self.assertEqual(code, 2)
        code, _payload = run_cli("exit-waiting", str(self.repo), TID, "x")
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
