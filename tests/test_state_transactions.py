#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""runtime.state / runtime.task v2.6.0 WP-R1 状态事务定向测试（Phase A，P1-1）。

运行：
    cd <repo_root> && PYTHONUTF8=1 python3 -m unittest tests.test_state_transactions -v

覆盖（docs/roadmap/V2_6_0_IMPLEMENTATION_PLAN.md §1 Phase A 验收门矩阵）：
    - 并发写者更新独立字段双方保留：确定性交错注入（测试线程手动持有
      <state.json>.lock 期间第二写者入队等锁 → 持锁方提交独立字段 →
      第二写者锁内新鲜重读后提交，两字段并存；陈旧整态写入会丢失
      对方字段）+ 真实锁争用（barrier 齐发四个任务层变更入口，独立
      字段全部存活）+ 双 update_task_state 串行证据（互斥锁内恰一个
      mutate 观察到对方字段——双方都不看 / 都看即互斥或新鲜读破损）；
    - 并发 complete vs validation 不复活终态：validation 锁外预检
      （存在性 / 终态冻结 / change_id）全部通过后被确定性压到 complete
      提交之后，锁内对新鲜盘面的终态冻结复核拒绝——validation 块零
      写入、state.json 逐字节不被触碰、journal 无 validation_recorded、
      状态保持 completed；另真实并发多轮，completed 恒为终局、complete
      恒成功、终态绝不重开；
    - 并发 resume confirm 不丢预算增量：双 confirm_resume_started 齐发
      恰一成功一拒绝（确认后任务已 active，重复确认无暂存决策可落账）、
      resume_count 恰 +1、恰一条 quota_resume_confirmed 事件；六个纯
      计数事务齐发并发 +1，resume_count 恰为 6（丢失更新即 < 6）；
    - 损坏 state.json 被拒不被覆盖：JSON 撕裂 / 缺必填键的 schema 残缺
      / 目录与内容任务标识错位三种盘面 × save_state / update_task_
      state（含 allow_missing=True——缺失放行不等于损坏放行）/
      transition_task_status / reopen_task / create_task /
      record_validation / complete 全部 ValueError 且 state.json 逐字节
      不被触碰（绝不以默认值 / 过时整态顶替损坏盘面）；
    - 同态重放幂等：终态同态重放（complete ×2 零重复落盘、journal
      事件与写者守卫释放按 _finish 文档化行为幂等重做）、active→active
      迁移重放、save_state 同态整放行、update_task_state 零变更 mutate
      内容恒等、bind 同 id / pin 同值 / clear 未武装的取锁前零写盘幂等。

并发用例全部离线：真实线程 + durable_io 文件锁（同进程多线程
O_CREAT|O_EXCL 争用，跨线程 owner 键互异）；record_validation 的
change_id git 现算以 mock 注入固定值（被测不变式是状态事务，与
change_id 派生无关）。仅 Python 3 标准库（unittest + threading +
tempfile），零第三方依赖，3.7 兼容语法。
"""

import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "glm-conductor"))

from runtime import journal, messages, state, task, writer_guard  # noqa: E402

# v2.6.1 W3b 英文化：终态冻结拒绝的辨识锚（messages.TASK_TERMINAL_FROZEN
# 占位符间的稳定段，单源派生、不在测试内复刻文案）。
TERMINAL_FROZEN_MARKER = (
    messages.TASK_TERMINAL_FROZEN.split("%r")[0].split("%s", 2)[-1].strip())
from runtime.durable_io import (  # noqa: E402
    LOCK_SUFFIX,
    atomic_write_json,
    exclusive_lock,
)

FLASH_A = "account:team-a/GLM-5.3-Flash"
FIXED_CHANGE_ID = "sha256:" + "0" * 64
THREAD_JOIN_TIMEOUT = 30.0
EVENT_TIMEOUT = 10.0


# —— 测试夹具 ——

class TransactionCase(unittest.TestCase):
    """每测试隔离的临时仓库根 + 线程 / 断言小助手。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = self._tmp.name

    # —— 夹具助手 ——

    def make_task(self, task_id):
        """经 task.create_task 正门创建一个 solo 活动任务。"""
        return task.create_task(self.repo, task_id, "状态事务定向测试任务",
                                {"mode": "solo"})

    def state_bytes(self, task_id):
        """读取任务 state.json 当前原始字节。"""
        return state.state_path(self.repo, task_id).read_bytes()

    def write_state_bytes(self, payload, task_id):
        """绕过全部写入口直接落盘原始字节（构造损坏 / 错位盘面用）。"""
        path = state.state_path(self.repo, task_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        return path

    def lock_path(self, task_id):
        """任务 state.json 的 durable_io 独占锁路径（<state.json>.lock）。"""
        return Path(str(state.state_path(self.repo, task_id)) + LOCK_SUFFIX)

    def journal_names(self, task_id):
        """任务 journal 全部事件名的有序清单。"""
        return [event.get("event")
                for event in journal.read_events(self.repo, task_id)]

    def start_and_join(self, threads):
        """启动并限时 join 全部线程；任一线程超时未结束即失败（锁死锁）。"""
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(THREAD_JOIN_TIMEOUT)
        for thread in threads:
            self.assertFalse(
                thread.is_alive(),
                "工作线程在 %.0f 秒内未结束（疑似状态锁死锁）"
                % THREAD_JOIN_TIMEOUT)


# —— 1. 并发写者更新独立字段双方保留 ——

class TestConcurrentIndependentWriters(TransactionCase):
    """验收门①：独立字段的并发写者在事务序列化后双方保留。"""

    def test_deterministic_interleave_second_writer_reads_fresh(self):
        """确定性交错：写者 B 等锁期间写者 A 提交独立字段——B 锁内
        新鲜重读后提交，两字段并存（陈旧整态写入必丢 worker_model）。"""
        tid = "txn-interleave"
        self.make_task(tid)
        orig_update = state.update_task_state
        entered = threading.Event()

        def spying_update(*args, **kwargs):
            # 写者 B 已通过全部锁外预检、即将进入状态事务等锁
            entered.set()
            return orig_update(*args, **kwargs)

        writer_b_errors = []

        def writer_b():
            try:
                task.set_phase(self.repo, tid, "validating")
            except Exception as exc:  # noqa: B902 - 线程内异常带回主线程
                writer_b_errors.append(exc)

        thread = threading.Thread(target=writer_b)
        with mock.patch.object(state, "update_task_state", spying_update):
            with exclusive_lock(self.lock_path(tid)):
                thread.start()
                self.assertTrue(
                    entered.wait(EVENT_TIMEOUT),
                    "写者 B 未在时限内到达状态事务入口")
                # 写者 A（持锁方）提交独立字段 worker_model：
                # 落盘先于放锁，B 的锁内重读必然见到
                current = state.load_state(self.repo, tid)
                self.assertIsNotNone(current)
                current["worker_model"] = FLASH_A
                atomic_write_json(
                    state.state_path(self.repo, tid), current,
                    sort_keys=False)
            # 放锁在先、join 在后（join 绝不入锁——B 要等本测试放锁）
        thread.join(THREAD_JOIN_TIMEOUT)
        self.assertFalse(
            thread.is_alive(),
            "写者 B 在 %.0f 秒内未结束（疑似状态锁死锁）"
            % THREAD_JOIN_TIMEOUT)
        self.assertEqual(writer_b_errors, [])
        final = state.load_state(self.repo, tid)
        # A 的独立字段未被 B 的提交覆盖（过时整态 dict 丢失即失败）
        self.assertEqual(final.get("worker_model"), FLASH_A)
        # B 的独立字段落盘
        self.assertEqual(final.get("phase"), "validating")
        self.assertEqual(final.get("status"), "active")

    def test_real_contention_four_task_layer_writers(self):
        """真实锁争用：四个任务层入口 barrier 齐发，全部成功且四个
        独立字段（phase / worker_model / automation_id / 授权预算）
        全部落盘互不覆盖。"""
        tid = "txn-barrier"
        self.make_task(tid)
        barrier = threading.Barrier(4)
        errors = {}

        def _worker(name, fn):
            try:
                barrier.wait(EVENT_TIMEOUT)
                fn()
            except Exception as exc:
                errors[name] = exc

        workers = {
            "phase": lambda: task.set_phase(self.repo, tid, "workflow"),
            "pin": lambda: task.pin_worker_model(self.repo, tid, FLASH_A),
            "bind": lambda: task.bind_quota_automation(
                self.repo, tid, "auto-txn-001"),
            "authorize": lambda: task.authorize_quota_resume(
                self.repo, tid, 3),
        }
        threads = [
            threading.Thread(target=_worker, args=(name, fn))
            for name, fn in sorted(workers.items())
        ]
        self.start_and_join(threads)
        self.assertEqual(errors, {})
        final = state.load_state(self.repo, tid)
        self.assertEqual(final.get("phase"), "workflow")
        self.assertEqual(final.get("worker_model"), FLASH_A)
        block = final["quota_resume"]
        self.assertEqual(block["automation_id"], "auto-txn-001")
        self.assertEqual(block["mode"], "auto")
        self.assertEqual(block["max_resumes"], 3)
        self.assertEqual(final.get("status"), "active")

    def test_paired_update_transactions_serialize_and_both_survive(self):
        """双 update_task_state 齐发：文件锁强制串行 + 锁内新鲜读——
        恰一个 mutate 观察到对方字段（两个都看不到 = 互斥破损，
        两个都看到 = 不可能），最终两字段并存。"""
        tid = "txn-pair"
        self.make_task(tid)
        barrier = threading.Barrier(2)
        observed = {}
        errors = []

        def writer(field, value, other_field):
            def _mutate(current):
                # 观察对方字段是否已在盘上（事务串行 + 新鲜读的证据）
                observed[field] = other_field in current
                current[field] = value
                return current

            try:
                barrier.wait(EVENT_TIMEOUT)
                state.update_task_state(self.repo, tid, _mutate)
            except Exception as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=writer, args=(
                "phase", "reviewing", "worker_model")),
            threading.Thread(target=writer, args=(
                "worker_model", FLASH_A, "phase")),
        ]
        self.start_and_join(threads)
        self.assertEqual(errors, [])
        # 恰一个写者后进锁、看到先进锁写者已提交的字段
        self.assertEqual(
            sorted(observed.values()), [False, True],
            "事务互斥 / 锁内新鲜读破损：观察记录 %r" % (observed,))
        final = state.load_state(self.repo, tid)
        self.assertEqual(final.get("phase"), "reviewing")
        self.assertEqual(final.get("worker_model"), FLASH_A)


# —— 2. 并发 complete vs validation 不复活终态 ——

class TestCompleteVersusValidation(TransactionCase):
    """验收门②：validation 与 complete 竞争时终态不被复活。"""

    def test_validation_blocked_behind_complete_rejected_on_fresh_state(self):
        """确定性排序：validation 锁外预检全过（盘面仍 active）后被压
        到 complete 提交之后——锁内新鲜盘面终态冻结拒绝，零写入、
        零 journal 事件、状态保持 completed。"""
        tid = "txn-complete-val"
        self.make_task(tid)
        at_change_id = threading.Event()
        release_change_id = threading.Event()

        def fake_change_id(st, repo_root):
            # validation 已通过存在性 / 终态冻结 / 词汇全部锁外预检，
            # 在 change_id 闸处悬停，等主线程先把 complete 提交落盘
            at_change_id.set()
            release_change_id.wait(EVENT_TIMEOUT)
            return FIXED_CHANGE_ID

        writer_b_errors = []

        def writer_b():
            try:
                task.record_validation(
                    self.repo, tid, "passed",
                    summary="并发验证", commands=["echo ok"])
            except Exception as exc:
                writer_b_errors.append(exc)

        with mock.patch.object(task, "_compute_change_id", fake_change_id):
            thread = threading.Thread(target=writer_b)
            thread.start()
            try:
                self.assertTrue(
                    at_change_id.wait(EVENT_TIMEOUT),
                    "validation 未在时限内到达 change_id 闸")
                # validation 预检已过但尚未取锁；complete 此刻自由取锁
                # 提交终态
                task.complete(self.repo, tid)
                after_complete = self.state_bytes(tid)
            finally:
                release_change_id.set()
                thread.join(THREAD_JOIN_TIMEOUT)
        # validation 被锁内新鲜盘面的终态冻结复核拒绝
        self.assertEqual(len(writer_b_errors), 1,
                         "validation 应恰被拒绝一次：%r" % (writer_b_errors,))
        self.assertIsInstance(writer_b_errors[0], ValueError)
        self.assertIn(TERMINAL_FROZEN_MARKER, str(writer_b_errors[0]))
        # 终态保持，validation 块零写入，state.json 逐字节不被触碰
        final = state.load_state(self.repo, tid)
        self.assertEqual(final["status"], "completed")
        self.assertIsNone(final["validation"]["status"])
        self.assertEqual(self.state_bytes(tid), after_complete)
        # journal 只有 task_completed，无 validation_recorded
        names = self.journal_names(tid)
        self.assertIn("task_completed", names)
        self.assertNotIn("validation_recorded", names)

    def test_racy_complete_and_validation_terminal_always_wins(self):
        """真实并发多轮：complete 恒成功、终局恒为 completed；
        validation 要么先于 complete 落盘（记录保留），要么被终态
        冻结拒绝（ValueError）——绝不出现终态重开或拒绝后残留写入。"""
        for round_index in range(8):
            tid = "txn-race-cv-%d" % round_index
            self.make_task(tid)
            barrier = threading.Barrier(2)
            outcomes = {}

            def _attempt(name, fn):
                try:
                    barrier.wait(EVENT_TIMEOUT)
                    fn()
                    outcomes[name] = None
                except Exception as exc:
                    outcomes[name] = exc

            with mock.patch.object(
                    task, "_compute_change_id",
                    lambda st, root: FIXED_CHANGE_ID):
                threads = [
                    threading.Thread(target=_attempt, args=(
                        "complete",
                        lambda: task.complete(self.repo, tid))),
                    threading.Thread(target=_attempt, args=(
                        "validation",
                        lambda: task.record_validation(
                            self.repo, tid, "passed", summary="竞态验证"))),
                ]
                self.start_and_join(threads)
            with self.subTest(round=round_index):
                self.assertIsNone(
                    outcomes["complete"],
                    "complete 必须恒成功：%r" % (outcomes,))
                final = state.load_state(self.repo, tid)
                self.assertEqual(final["status"], "completed")
                validation_error = outcomes["validation"]
                if validation_error is None:
                    # validation 先提交、complete 随后收尾：记录合法保留
                    self.assertEqual(final["validation"]["status"], "passed")
                else:
                    # validation 后到：被锁内新鲜盘面终态冻结拒绝
                    self.assertIsInstance(validation_error, ValueError)
                    self.assertIn(TERMINAL_FROZEN_MARKER, str(validation_error))
                    self.assertIsNone(final["validation"]["status"])
                self.assertNotIn(
                    "task_reopened", self.journal_names(tid),
                    "终态被重开：validation 复活了 completed 终态")


# —— 3. 并发 resume confirm 不丢预算增量 ——

class TestConcurrentResumeConfirm(TransactionCase):
    """验收门③：恢复确认的预算增量在并发下不丢、不重。"""

    def _prepare_auto_waiting(self, task_id, max_resumes):
        """auto 授权 + 进入 waiting_quota（confirm 的合法前置态）。"""
        self.make_task(task_id)
        task.authorize_quota_resume(self.repo, task_id, max_resumes)
        task.set_status(self.repo, task_id, "waiting_quota")

    def test_double_confirm_exactly_one_increment(self):
        """双 confirm 齐发：恰一成功一拒绝，resume_count 恰 +1、
        恰一条 quota_resume_confirmed 事件、终态 active/workflow。"""
        tid = "txn-confirm"
        self._prepare_auto_waiting(tid, max_resumes=2)
        barrier = threading.Barrier(2)
        outcomes = {}

        def _confirm(name):
            try:
                barrier.wait(EVENT_TIMEOUT)
                task.confirm_resume_started(self.repo, tid)
                outcomes[name] = None
            except Exception as exc:
                outcomes[name] = exc

        threads = [
            threading.Thread(target=_confirm, args=(name,))
            for name in ("confirm-1", "confirm-2")
        ]
        self.start_and_join(threads)
        succeeded = [name for name, err in outcomes.items() if err is None]
        self.assertEqual(len(succeeded), 1,
                         "双 confirm 应恰一成功：%r" % (outcomes,))
        for name, err in outcomes.items():
            if err is not None:
                self.assertIsInstance(err, ValueError)
                self.assertIn("waiting_quota", str(err))
        final = state.load_state(self.repo, tid)
        block = final["quota_resume"]
        self.assertEqual(block["resume_count"], 1)  # 不丢也不重
        self.assertEqual(block["max_resumes"], 2)
        self.assertEqual(final["status"], "active")
        self.assertEqual(final.get("phase"), "workflow")
        names = self.journal_names(tid)
        self.assertEqual(names.count("quota_resume_confirmed"), 1)

    def test_parallel_increment_transactions_no_lost_update(self):
        """六写者齐发对 resume_count 各 +1（纯计数状态事务）：
        最终恰为 6——丢失更新（读-改-写不串行）即 < 6。"""
        tid = "txn-increment"
        self.make_task(tid)
        task.authorize_quota_resume(self.repo, tid, 8)
        workers = 6
        barrier = threading.Barrier(workers)
        errors = []

        def _increment():
            def _mutate(current):
                block = current["quota_resume"]
                block["resume_count"] = block["resume_count"] + 1
                return current

            try:
                barrier.wait(EVENT_TIMEOUT)
                state.update_task_state(self.repo, tid, _mutate)
            except Exception as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=_increment) for _ in range(workers)
        ]
        self.start_and_join(threads)
        self.assertEqual(errors, [])
        final = state.load_state(self.repo, tid)
        block = final["quota_resume"]
        self.assertEqual(block["resume_count"], workers)
        self.assertEqual(block["max_resumes"], 8)  # 预算不变量仍成立
        self.assertEqual(final["status"], "active")


# —— 4. 损坏 state.json 被拒不被覆盖 ——

class TestCorruptStateRejected(TransactionCase):
    """验收门④：三种损坏盘面 × 全部变更入口一律 ValueError 且逐字节
    不被触碰。"""

    def _rejected_operations(self, tid):
        """对损坏盘面的全部状态变更入口（均为期望 ValueError 的操作）。"""
        valid = state.new_task_state(
            tid, "状态事务定向测试", {"mode": "solo"},
            repository_root=self.repo)
        return [
            ("save_state",
             lambda: state.save_state(self.repo, valid, task_id=tid)),
            ("update_task_state",
             lambda: state.update_task_state(self.repo, tid,
                                             lambda cur: cur)),
            ("update_task_state(allow_missing)",
             lambda: state.update_task_state(self.repo, tid,
                                             lambda cur: cur,
                                             allow_missing=True)),
            ("transition_task_status",
             lambda: state.transition_task_status(self.repo, tid, "blocked")),
            ("reopen_task",
             lambda: state.reopen_task(self.repo, tid)),
            ("create_task",
             lambda: task.create_task(self.repo, tid, "重建",
                                      {"mode": "solo"})),
            ("record_validation",
             lambda: task.record_validation(self.repo, tid, "passed")),
            ("complete",
             lambda: task.complete(self.repo, tid)),
        ]

    def _assert_all_rejected_and_intact(self, tid):
        """逐操作断言 ValueError 且 state.json 逐字节不被触碰。"""
        before = self.state_bytes(tid)
        with mock.patch.object(task, "_compute_change_id",
                               lambda st, root: FIXED_CHANGE_ID):
            for name, operation in self._rejected_operations(tid):
                with self.subTest(operation=name):
                    with self.assertRaises(ValueError):
                        operation()
                    self.assertEqual(
                        self.state_bytes(tid), before,
                        "%s 触碰了损坏盘面" % name)

    def test_torn_json_rejected_not_overwritten(self):
        """JSON 撕裂盘面：解析即 ValueError，写入方绝不以默认值顶替。"""
        tid = "txn-corrupt-torn"
        self.make_task(tid)
        self.write_state_bytes(
            b'{"task_id": "' + tid.encode("ascii")
            + b'", "status": "acti', tid)
        self._assert_all_rejected_and_intact(tid)

    def test_schema_incomplete_json_rejected_not_overwritten(self):
        """schema 残缺盘面（合法 JSON 但缺必填键）：事务基底校验拒绝，
        绝不被整体态默认值覆盖。"""
        tid = "txn-corrupt-schema"
        self.make_task(tid)
        self.write_state_bytes(
            json.dumps({"task_id": tid},
                       ensure_ascii=False).encode("utf-8"), tid)
        self._assert_all_rejected_and_intact(tid)

    def test_identity_mismatch_rejected_not_overwritten(self):
        """目录与内容任务标识错位盘面（内容是另一任务的合法 state）：
        错位账本拒绝作为事务基底，绝不在其上写入。"""
        tid = "txn-corrupt-mismatch"
        self.make_task(tid)
        other = state.new_task_state(
            "txn-mismatch-other", "别的任务", {"mode": "solo"},
            repository_root=self.repo)
        self.write_state_bytes(
            json.dumps(other, ensure_ascii=False,
                       indent=2).encode("utf-8"), tid)
        self._assert_all_rejected_and_intact(tid)


# —— 5. 同态重放幂等 ——

class TestSameStateReplay(TransactionCase):
    """验收门⑤：同态重放（含同终态重放）幂等。"""

    def test_terminal_replay_no_rewrite(self):
        """complete ×2：第二次同终态重放成功、零重复落盘（state.json
        逐字节恒等）；journal 事件与写者守卫释放按 _finish 文档化行为
        幂等重做（事件重追加、守卫保持无预约）。"""
        tid = "txn-replay-terminal"
        self.make_task(tid)
        first = task.complete(self.repo, tid)
        self.assertEqual(first["status"], "completed")
        after_first = self.state_bytes(tid)
        second = task.complete(self.repo, tid)
        self.assertEqual(second["status"], "completed")
        self.assertEqual(
            self.state_bytes(tid), after_first,
            "同终态重放不得重复落盘")
        names = self.journal_names(tid)
        self.assertEqual(names.count("task_completed"), 2)
        self.assertIsNone(
            writer_guard.inspect(self.repo),
            "重放后写者守卫应保持已释放（无预约）")

    def test_active_transition_and_save_replay(self):
        """active→active 迁移重放（transition_task_status 与任务层
        set_status）与 save_state 同态整放行：全部成功、状态不变。"""
        tid = "txn-replay-transition"
        self.make_task(tid)
        first = state.transition_task_status(self.repo, tid, "active")
        self.assertEqual(first["status"], "active")
        second = task.set_status(self.repo, tid, "active")
        self.assertEqual(second["status"], "active")
        replayed = state.load_state(self.repo, tid)
        state.save_state(self.repo, replayed)  # 同态整存放行
        final = state.load_state(self.repo, tid)
        self.assertEqual(final["status"], "active")
        self.assertEqual(final["task_id"], tid)

    def test_zero_change_mutate_replay(self):
        """update_task_state 零变更 mutate：提交成功且内容逐字节恒等。"""
        tid = "txn-replay-mutate"
        self.make_task(tid)
        before = self.state_bytes(tid)
        result = state.update_task_state(self.repo, tid, lambda cur: cur)
        self.assertEqual(result["task_id"], tid)
        self.assertEqual(
            self.state_bytes(tid), before,
            "零变更 mutate 的重放不得改变盘面内容")

    def test_zero_write_idempotent_entries(self):
        """取锁前零写盘的幂等入口：bind 同 id 重绑、pin 同值重 pin、
        clear 未武装清除——全部零写盘原样返回现状（字节恒等）。"""
        tid_bind = "txn-replay-bind"
        self.make_task(tid_bind)
        task.bind_quota_automation(self.repo, tid_bind, "auto-idem-1")
        after_bind = self.state_bytes(tid_bind)
        rebound = task.bind_quota_automation(
            self.repo, tid_bind, "auto-idem-1")
        self.assertEqual(rebound["quota_resume"]["automation_id"],
                         "auto-idem-1")
        self.assertEqual(self.state_bytes(tid_bind), after_bind)

        tid_pin = "txn-replay-pin"
        self.make_task(tid_pin)
        task.pin_worker_model(self.repo, tid_pin, FLASH_A)
        after_pin = self.state_bytes(tid_pin)
        repinned = task.pin_worker_model(self.repo, tid_pin, FLASH_A)
        self.assertEqual(repinned, {"task_id": tid_pin, "prev": FLASH_A,
                                    "new": FLASH_A})
        self.assertEqual(self.state_bytes(tid_pin), after_pin)

        tid_clear = "txn-replay-clear"
        self.make_task(tid_clear)
        after_create = self.state_bytes(tid_clear)
        cleared = task.clear_quota_automation(self.repo, tid_clear)
        self.assertIsNone(cleared["quota_resume"]["automation_id"])
        self.assertEqual(self.state_bytes(tid_clear), after_create)


if __name__ == "__main__":
    unittest.main()
