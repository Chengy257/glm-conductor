#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""runtime.cli task-record-workflow-run 规范 run 注册 + _finish 重放定向测试
（v2.6.0 Phase C，WP-R4 / P2-1 / P2-2）。

运行：
    cd <repo_root> && PYTHONUTF8=1 python3 -m unittest tests.test_workflow_run_registration -v

覆盖（docs/roadmap/V2_6_0_IMPLEMENTATION_PLAN.md §3 Phase C 验收门 R4）：
    - 任意 CWD 注册（规范路径锚定 repo_root）：os.chdir 到无关目录后
      进程内调 cli.main，以及真实子进程以无关 CWD 调 cli.py——单据
      一律落在账本根 repo_root 的 .glm-conductor/workflow-runs/，与
      进程 CWD 无关；CWD 侧零残留；state.workflow_run_id、journal
      workflow_started 事件、写者守卫 run id 幂等补挂同批成立；
    - AF-04 守卫前置经 CLI 的拒绝面（退出码 1）：无 reservation（未
      acquire）拒绝——错误报文含 missing writer reservation，state 不
      落 workflow_run_id、无 workflow_started 事件、adapter 无单据、
      守卫仍缺失（绝不落假成功）；失配 reservation（他任务持有）拒绝
      ——报文逐字点名持有者 task_id / workflow_run_id，他人守卫原样
      保留；run-id 结构非法（空串）CLI 先闸退出码 2，参数个数错误
      退出码 2；
    - v24-record-run legacy 行为回归不变：真实子进程以无关 CWD 调用
      仍成功（无需任何任务 / 守卫前置），单据落 CWD 相对
      .glm-conductor/workflow-runs/（CWD-only 口径逐字保留），不写
      任何任务 state / journal，repo 账本侧零污染；
    - _finish 同终态重放经事务判定后事件 / 释放仍幂等：complete ×2
      （×3，中途重取守卫验证释放幂等重做）零重复落盘（state.json
      逐字节恒等）、事件幂等重追加、守卫保持释放；fail / cancel ×2
      同语义；跨终态迁移拒绝且零副作用（防御闸 mock 直测：事务返回
      非终态盘面 → ValueError，绝不追加事件、绝不释放他人可见守卫
      ——事件不落在 active 任务）；
    - 并发 reopen 交错（确定性）：同终态重放在事务入口等锁期间，
      reopen 在锁内提交（completed → active，复刻 reopen_task 锁内
      步骤）——重放判定收在锁内零变更 mutate 上，对新鲜 active 盘面
      重新判定并合法迁移，终局仍是终态 completed（终态不被复活、
      不被陈旧读数盲写），task_completed 事件 from=active to=completed
      证伪「锁外判重放」（事件不落在 active 任务），task_reopened
      事件恰一条且按真实顺序夹在两次完成事件之间，守卫保持释放。

并发用例全部离线：真实线程 + durable_io 文件锁（tests/test_state_
transactions.py 同款确定性交错手法）；CLI 用例进程内调 cli.main +
redirect_stdout 与真实子进程两种形态并用。仅 Python 3 标准库
（unittest + threading + tempfile），零第三方依赖，3.7 兼容语法。
"""

import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "glm-conductor"))

from runtime import cli, journal, state, task, writer_guard  # noqa: E402
from runtime.durable_io import (  # noqa: E402
    LOCK_SUFFIX,
    exclusive_lock,
)
from runtime.workflow import adapter as workflow_adapter  # noqa: E402

CLI_PATH = (Path(__file__).resolve().parents[1] / "plugins" /
            "glm-conductor" / "runtime" / "cli.py")
THREAD_JOIN_TIMEOUT = 30.0
EVENT_TIMEOUT = 10.0


# —— 测试夹具 ——

class RunRegistrationCase(unittest.TestCase):
    """每测试隔离的临时账本根 + 无关 CWD 目录 + 进程内 CLI 入口。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = os.path.join(self._tmp.name, "ledger")
        self.foreign = os.path.join(self._tmp.name, "elsewhere")
        os.makedirs(self.repo)
        os.makedirs(self.foreign)
        self.tid = "runreg-task-a1b2c"
        self._original_cwd = os.getcwd()

    def tearDown(self):
        os.chdir(self._original_cwd)

    # —— 装置助手 ——

    def make_task(self, task_id=None, mode="delegate"):
        """经 task.create_task 正门创建活动任务，返回状态 dict。"""
        return task.create_task(
            self.repo, task_id or self.tid, "规范 run 注册定向测试",
            {"mode": mode, "assurance": "standard"})

    def run_cli(self, *args):
        """进程内调用 cli.main，返回 (退出码, stdout 原文)。"""
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = cli.main(list(args))
        return code, buffer.getvalue()

    def run_cli_json(self, *args):
        """进程内调用 cli.main 并把 stdout 单行 JSON 解析为 dict。"""
        code, stdout = self.run_cli(*args)
        return code, json.loads(stdout)

    def run_cli_subprocess(self, *args, **kwargs):
        """真实子进程调用 cli.py，返回 (退出码, stdout 原文)。

        cwd 由调用方以关键字注入（默认无关目录 foreign）——CLI 的
        repo_root 参数决定账本落点，进程 CWD 决定 legacy 落点。"""
        proc = subprocess.run(
            [sys.executable, str(CLI_PATH)] + list(args),
            cwd=kwargs.pop("cwd", self.foreign), text=True,
            capture_output=True, encoding="utf-8", errors="replace")
        return proc.returncode, proc.stdout

    def runs_dir(self, root=None):
        """某根下的 run 关联记录目录（repo_root 锚定口径）。"""
        return os.path.join(root or self.repo, ".glm-conductor",
                            "workflow-runs")

    def load(self, task_id=None):
        return state.load_state(self.repo, task_id or self.tid)

    def names(self, task_id=None):
        """任务 journal 全部事件名的有序清单。"""
        return [item.get("event")
                for item in journal.read_events(self.repo, task_id or self.tid)]

    def canonical_record(self, task_id=None, run_id=None):
        """经 adapter 正门读取 repo_root 上的关联单据（断言用同一口径）。"""
        return workflow_adapter.load_run(task_id or self.tid,
                                         repo_root=self.repo)

    def assert_canonical_registered(self, run_id):
        """断言一次成功注册的全部位点：state / 单据 / 事件 / 守卫补挂。"""
        self.assertEqual(self.load()["workflow_run_id"], run_id)
        record = self.canonical_record()
        self.assertIsNotNone(record)
        self.assertEqual(record["task_ref"], self.tid)
        self.assertEqual(record["workflow_run_id"], run_id)
        self.assertTrue(record["created_at"].endswith("Z"))
        self.assertEqual(self.names()[-1], "workflow_started")
        self.assertEqual(self.names().count("workflow_started"), 1)
        holder = writer_guard.inspect(self.repo)
        self.assertIsNotNone(holder)
        self.assertEqual(holder["task_id"], self.tid)
        self.assertEqual(holder["workflow_run_id"], run_id)


# —— 1. 任意 CWD 注册：单据落账本根 repo_root ——

class TestCanonicalRegistrationAnyCwd(RunRegistrationCase):
    """task-record-workflow-run 与进程 CWD 无关（WP-R4 主验收）。"""

    def test_registration_from_chdir_elsewhere(self):
        """os.chdir 到无关目录后进程内注册：单据仍落 repo_root 账本，
        CWD 侧零残留。"""
        self.make_task()
        self.assertTrue(
            writer_guard.acquire(self.repo, self.tid, "run-pre")["ok"])
        os.chdir(self.foreign)
        code, payload = self.run_cli_json(
            "task-record-workflow-run", self.repo, self.tid, "run-123")
        self.assertEqual(code, 0)
        self.assertEqual(payload, {"task_id": self.tid,
                                   "workflow_run_id": "run-123",
                                   "status": "active"})
        self.assert_canonical_registered("run-123")
        # CWD 侧零残留：无关目录不得出现任何 Conductor 记账目录
        self.assertFalse(os.path.exists(
            os.path.join(self.foreign, ".glm-conductor")))

    def test_registration_from_foreign_cwd_subprocess(self):
        """真实子进程以无关 CWD 调 cli.py：退出码 0、单行 JSON 契约、
        单据落在 repo_root（进程 CWD 与账本根分离的端到端锚）。"""
        self.make_task()
        self.assertTrue(
            writer_guard.acquire(self.repo, self.tid, "run-pre")["ok"])
        code, stdout = self.run_cli_subprocess(
            "task-record-workflow-run", self.repo, self.tid, "run-e2e",
            cwd=self.foreign)
        self.assertEqual(code, 0)
        lines = stdout.splitlines()
        self.assertEqual(len(lines), 1, "CLI 成功输出必须恰单行 JSON")
        payload = json.loads(lines[0])
        self.assertEqual(payload["task_id"], self.tid)
        self.assertEqual(payload["workflow_run_id"], "run-e2e")
        self.assertEqual(payload["status"], "active")
        self.assert_canonical_registered("run-e2e")
        self.assertFalse(os.path.exists(
            os.path.join(self.foreign, ".glm-conductor")))

    def test_registration_record_path_is_repo_root_anchored(self):
        """单据文件名与路径：record_path(task, repo_root) 落在
        repo_root/.glm-conductor/workflow-runs/（结构性路径锚）。"""
        self.make_task()
        writer_guard.acquire(self.repo, self.tid, "run-pre")
        self.run_cli("task-record-workflow-run", self.repo, self.tid,
                     "run-path")
        expected = os.path.join(self.runs_dir(self.repo),
                                "run-%s.json" % self.tid)
        self.assertTrue(os.path.isfile(expected))
        self.assertEqual(
            workflow_adapter.record_path(self.tid, repo_root=self.repo),
            expected)


# —— 2. AF-04 守卫前置经 CLI 的拒绝面（退出码 1） ——

class TestCanonicalRegistrationGuardPrecondition(RunRegistrationCase):
    """无 reservation / 失配 reservation 拒绝且零副作用（绝不假成功）。"""

    def assert_registration_refused(self):
        """断言拒绝后的全部零副作用位点。"""
        self.assertIsNone(self.load()["workflow_run_id"])
        self.assertNotIn("workflow_started", self.names())
        self.assertIsNone(self.canonical_record())
        # 守卫前置失败的回合不得触碰 repo 账本根以外的任何目录
        self.assertFalse(os.path.exists(
            os.path.join(self.foreign, ".glm-conductor")))

    def test_missing_reservation_refused_exit_1(self):
        """未 acquire（无 reservation）→ exit 1 + missing writer
        reservation 报文 + 全部位点零副作用。"""
        self.make_task()
        code, payload = self.run_cli_json(
            "task-record-workflow-run", self.repo, self.tid, "run-1")
        self.assertEqual(code, 1)
        self.assertIn("missing writer reservation", payload["error"])
        self.assertIn(self.tid, payload["error"])
        self.assert_registration_refused()
        self.assertIsNone(writer_guard.inspect(self.repo))

    def test_mismatched_reservation_refused_exit_1(self):
        """守卫被他任务持有 → exit 1 + 逐字点名持有者 task_id 与
        workflow_run_id；他人守卫原样保留。"""
        self.make_task()
        writer_guard.acquire(self.repo, "holder-task-9", "run-held-0")
        code, payload = self.run_cli_json(
            "task-record-workflow-run", self.repo, self.tid, "run-1")
        self.assertEqual(code, 1)
        self.assertIn("holder-task-9", payload["error"])
        self.assertIn("run-held-0", payload["error"])
        self.assert_registration_refused()
        holder = writer_guard.inspect(self.repo)
        self.assertEqual(holder["task_id"], "holder-task-9")
        self.assertEqual(holder["workflow_run_id"], "run-held-0")

    def test_empty_run_id_exit_2_before_task_layer(self):
        """run-id 空串在 CLI 侧先闸（参数值非法口径）→ 退出码 2，
        任务层零接触（state / 事件 / 守卫均不动）。"""
        self.make_task()
        writer_guard.acquire(self.repo, self.tid, "run-pre")
        code, payload = self.run_cli_json(
            "task-record-workflow-run", self.repo, self.tid, "")
        self.assertEqual(code, 2)
        self.assertIn("run-id", payload["error"])
        # 任务层零接触：state 不落 run 关联，既有守卫预约原样保留
        self.assertIsNone(self.load()["workflow_run_id"])
        holder = writer_guard.inspect(self.repo)
        self.assertEqual(holder["task_id"], self.tid)
        self.assertEqual(holder["workflow_run_id"], "run-pre")

    def test_usage_errors_exit_2(self):
        """参数个数错误 → 退出码 2（_UsageError）。"""
        code, _stdout = self.run_cli("task-record-workflow-run")
        self.assertEqual(code, 2)
        code, _stdout = self.run_cli(
            "task-record-workflow-run", self.repo, self.tid)
        self.assertEqual(code, 2)
        code, _stdout = self.run_cli(
            "task-record-workflow-run", self.repo, self.tid, "run", "extra")
        self.assertEqual(code, 2)

    def test_solo_mode_registers_without_guard_from_cli(self):
        """solo 任务无守卫前置（AF-04 只约束 delegate/full）：CLI 注册
        成功且不触碰守卫。"""
        self.make_task(mode="solo")
        code, payload = self.run_cli_json(
            "task-record-workflow-run", self.repo, self.tid, "run-solo")
        self.assertEqual(code, 0)
        self.assertEqual(payload["workflow_run_id"], "run-solo")
        self.assertEqual(self.load()["workflow_run_id"], "run-solo")
        self.assertIsNotNone(self.canonical_record())
        self.assertIsNone(writer_guard.inspect(self.repo))


# —— 3. v24-record-run legacy 行为回归不变 ——

class TestLegacyRecordRunRegression(RunRegistrationCase):
    """legacy 低层薄壳：CWD-only 口径、无任务 / 守卫前置。"""

    def test_legacy_record_run_cwd_anchored_without_task(self):
        """无关 CWD 子进程调用 legacy v24-record-run：仍成功（无任务、
        无守卫要求），单据落 CWD 相对 .glm-conductor/workflow-runs/，
        repo 账本侧零污染、零任务 state / journal。"""
        code, stdout = self.run_cli_subprocess(
            "v24-record-run", "T-legacy-cwd", "run-l1", cwd=self.foreign)
        self.assertEqual(code, 0)
        lines = stdout.splitlines()
        self.assertEqual(len(lines), 1)
        payload = json.loads(lines[0])
        self.assertEqual(payload["task_ref"], "T-legacy-cwd")
        self.assertEqual(payload["workflow_run_id"], "run-l1")
        self.assertTrue(payload["created_at"].endswith("Z"))
        # 单据落 CWD（legacy 口径逐字保留）
        legacy_path = os.path.join(
            self.runs_dir(self.foreign), "run-T-legacy-cwd.json")
        self.assertTrue(os.path.isfile(legacy_path))
        with open(legacy_path, "r", encoding="utf-8") as handle:
            self.assertEqual(json.load(handle), payload)
        # repo 账本侧零污染：无同名单据、无任务目录、无任何 state
        self.assertFalse(os.path.isfile(os.path.join(
            self.runs_dir(self.repo), "run-T-legacy-cwd.json")))
        self.assertFalse(os.path.exists(os.path.join(
            self.repo, ".glm-conductor", "tasks", "T-legacy-cwd")))

    def test_legacy_and_canonical_records_do_not_collide(self):
        """同 task_ref 双口径并存：canonical 落 repo_root、legacy 落
        CWD，两份单据互不覆盖（不同目录各自成立）。"""
        self.make_task()
        writer_guard.acquire(self.repo, self.tid, "run-pre")
        os.chdir(self.foreign)
        self.run_cli("task-record-workflow-run", self.repo, self.tid,
                     "run-canonical")
        code, _stdout = self.run_cli("v24-record-run", self.tid, "run-legacy")
        self.assertEqual(code, 0)
        self.assertEqual(
            self.canonical_record()["workflow_run_id"], "run-canonical")
        legacy_path = os.path.join(self.runs_dir(self.foreign),
                                   "run-%s.json" % self.tid)
        with open(legacy_path, "r", encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["workflow_run_id"],
                             "run-legacy")


# —— 4. _finish 同终态重放：事件 / 释放幂等 ——

class FinishReplayCase(RunRegistrationCase):
    """终态重放用例的公共助手（状态事务 + 守卫位点断言）。"""

    def state_bytes(self, task_id):
        return state.state_path(self.repo, task_id).read_bytes()

    def lock_path(self, task_id):
        return Path(str(state.state_path(self.repo, task_id)) + LOCK_SUFFIX)


class TestFinishReplayIdempotent(FinishReplayCase):

    def test_complete_replay_events_and_release_idempotent(self):
        """complete ×3（含守卫重取）：state.json 逐字节恒等（零重复
        落盘），task_completed 事件幂等重追加，守卫每次重放后保持
        释放——重放判定收在锁内零变更 mutate 上。"""
        self.make_task()
        writer_guard.acquire(self.repo, self.tid, "run-1")
        first = task.complete(self.repo, self.tid)
        self.assertEqual(first["status"], "completed")
        self.assertIsNone(writer_guard.inspect(self.repo))
        after_first = self.state_bytes(self.tid)
        for round_index in (2, 3):
            if round_index == 3:
                # 中途重取守卫：重放的释放仍要把它幂等清掉
                writer_guard.acquire(self.repo, self.tid, "run-2")
            replay = task.complete(self.repo, self.tid)
            self.assertEqual(replay["status"], "completed")
            self.assertEqual(
                self.state_bytes(self.tid), after_first,
                "第 %d 次同终态重放不得重复落盘" % round_index)
            self.assertIsNone(writer_guard.inspect(self.repo))
        self.assertEqual(self.names().count("task_completed"), 3)
        self.assertEqual(self.names()[-1], "task_completed")

    def test_fail_and_cancel_replay_same_semantics(self):
        """fail ×2 与 cancel ×2：状态字节恒等、事件各恰两条、状态保持
        各自终态。"""
        for finish, event, terminal in (
                (task.fail, "task_failed", "failed"),
                (task.cancel, "task_cancelled", "cancelled")):
            tid = "runreg-replay-%s" % terminal
            self.make_task(tid, mode="solo")
            first = finish(self.repo, tid)
            self.assertEqual(first["status"], terminal)
            after_first = self.state_bytes(tid)
            second = finish(self.repo, tid)
            self.assertEqual(second["status"], terminal)
            self.assertEqual(self.state_bytes(tid), after_first)
            self.assertEqual(self.names(tid).count(event), 2)
            self.assertEqual(self.load(tid)["status"], terminal)

    def test_cross_terminal_transition_refused_zero_side_effects(self):
        """completed → failed 跨终态迁移拒绝：ValueError、字节不变、
        无 task_failed 事件、状态保持 completed。"""
        self.make_task()
        task.complete(self.repo, self.tid)
        before = self.state_bytes(self.tid)
        with self.assertRaises(ValueError):
            task.fail(self.repo, self.tid)
        self.assertEqual(self.state_bytes(self.tid), before)
        self.assertNotIn("task_failed", self.names())
        self.assertEqual(self.load()["status"], "completed")

    def test_defense_gate_refuses_event_on_non_terminal_state(self):
        """防御闸直测（事件不落在 active 任务）：状态事务提交后盘面非
        目标终态 → ValueError，绝不追加事件、绝不释放既有守卫。"""
        self.make_task()
        writer_guard.acquire(self.repo, self.tid, "run-g")
        before = self.state_bytes(self.tid)

        def stale_non_terminal(repo_root, task_id, _mutate, **kwargs):
            # 模拟事务返回了未施加终态的盘面（防御闸唯一触发面）
            return state.load_state(repo_root, task_id)

        with mock.patch.object(state, "update_task_state",
                               stale_non_terminal):
            with self.assertRaises(ValueError) as ctx:
                task.complete(self.repo, self.tid)
        self.assertIn("非目标终态", str(ctx.exception))
        self.assertNotIn("task_completed", self.names())
        holder = writer_guard.inspect(self.repo)
        self.assertIsNotNone(holder)
        self.assertEqual(holder["task_id"], self.tid)
        self.assertEqual(self.state_bytes(self.tid), before)


# —— 5. 并发 reopen 交错：重放判定与 reopen 竞态 ——

class TestReplayVersusReopenInterleave(FinishReplayCase):
    """确定性交错：重放等锁期间 reopen 提交 → 锁内新鲜盘面重新判定。"""

    def test_parked_replay_rejudged_on_fresh_state_after_reopen(self):
        """同终态重放在事务入口等锁，reopen 锁内提交 completed→active：
        重放不得按锁外陈旧读数走零变更快路径——对新鲜 active 盘面
        重新判定并合法迁移，终局仍是终态 completed；task_completed
        事件 from=active（证伪「事件落在 active 任务」的陈旧重放），
        task_reopened 恰一条、按真实顺序夹在两次完成事件之间；
        守卫保持释放。"""
        tid = "runreg-race-reopen"
        self.make_task(tid, mode="solo")
        task.complete(self.repo, tid)
        self.assertEqual(self.names(tid),
                         ["route_selected", "task_completed"])
        orig_update = state.update_task_state
        entered = threading.Event()
        errors = []

        def spying_update(*args, **kwargs):
            # 重放写者已通过锁外预检（盘面 completed → 同终态预检放行）、
            # 即将进入状态事务等锁
            entered.set()
            return orig_update(*args, **kwargs)

        def replayer():
            try:
                task.complete(self.repo, tid)
            except Exception as exc:  # noqa: B902 - 线程内异常带回主线程
                errors.append(exc)

        thread = threading.Thread(target=replayer)
        with mock.patch.object(state, "update_task_state", spying_update):
            with exclusive_lock(self.lock_path(tid)):
                thread.start()
                self.assertTrue(
                    entered.wait(EVENT_TIMEOUT),
                    "重放写者未在时限内到达状态事务入口")
                # 交错：reopen 在重放等锁期间提交（reopen_task 锁内
                # 步骤的忠实复刻——本测试已持有同一把状态锁）
                current = state._transaction_state(self.repo, tid,
                                                   allow_missing=False)
                self.assertEqual(current["status"], "completed")
                reopened = dict(current)
                reopened["status"] = "active"
                state._transaction_commit(
                    state.state_path(self.repo, tid), tid, reopened)
                journal.append_event(
                    self.repo, tid,
                    {"event": "task_reopened", "from": "completed",
                     "to": "active", "reason": "replay-race-interleave"})
            # 放锁在先、join 在后（join 绝不入锁——重放要等本测试放锁）
        thread.join(THREAD_JOIN_TIMEOUT)
        self.assertFalse(thread.is_alive(), "重放写者未在时限内结束")
        self.assertEqual(errors, [], "重放写者不应因交错而失败：%r"
                         % (errors,))
        # 终局仍是终态：重放对新鲜 active 盘面重新判定（active→completed
        # 是合法迁移），陈旧 completed 整态没有被盲写回
        final = state.load_state(self.repo, tid)
        self.assertEqual(final["status"], "completed")
        # 事件序列按真实顺序：reopened 夹在两次 completed 之间
        events = journal.read_events(self.repo, tid)
        self.assertEqual(
            [item.get("event") for item in events],
            ["route_selected", "task_completed", "task_reopened",
             "task_completed"])
        reopened_event = events[2]
        self.assertEqual(reopened_event["from"], "completed")
        self.assertEqual(reopened_event["to"], "active")
        # 最后一条 task_completed 的 from=active：判定取锁内新鲜盘面，
        # 绝不是锁外陈旧 completed 读数的零变更重放（那样事件会落在
        # active 任务上、from 会是 completed）
        final_completed = events[-1]
        self.assertEqual(final_completed["event"], "task_completed")
        self.assertEqual(final_completed["from"], "active")
        self.assertEqual(final_completed["to"], "completed")
        # 终态不被复活：无第二次 reopen；守卫保持释放
        self.assertEqual(
            [item.get("event") for item in events].count("task_reopened"),
            1)
        self.assertIsNone(writer_guard.inspect(self.repo))

    def test_reopen_after_settled_replay_leaves_active_task(self):
        """次序相反的合法序列对照：重放先落定、reopen 随后全权生效——
        重放的事件留在历史里，任务停在 active（reopen 唯一放行通道）。"""
        tid = "runreg-race-reopen-after"
        self.make_task(tid, mode="solo")
        task.complete(self.repo, tid)
        task.complete(self.repo, tid)  # 同终态重放
        reopened = state.reopen_task(self.repo, tid, reason="对照重开")
        self.assertEqual(reopened["status"], "active")
        self.assertEqual(self.load(tid)["status"], "active")
        self.assertEqual(
            self.names(tid),
            ["route_selected", "task_completed", "task_completed",
             "task_reopened"])
        self.assertIsNone(writer_guard.inspect(self.repo))


if __name__ == "__main__":
    unittest.main()
