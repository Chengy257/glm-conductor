#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""runtime.quota.watcher_store 真实多进程测试（v2.2.1 hardening，
WU-221-A2 "New tests" 逐字要求：a1..a4 落地的并发原语既有验证全部是
进程内线程测试，本模块补上**真实多进程**面——Windows spawn / POSIX
fork 双形态下进程边界的机械互斥与陈旧恢复）。

场景映射（需求逐条 → 测试类）：
    SimultaneousAcquireTest         N=2..4 个进程 barrier 同步同时
                                    acquire_watcher_lock（同一 tempdir
                                    仓库 + 同一 provider_identity_hash）：
                                    恰一个 acquired=True（O_EXCL 机械
                                    原子保证，非时序）、其余全部确定性
                                    冲突、watcher.json 全程合法 JSON、
                                    锁 generation 全局唯一（败者零写盘）、
                                    无接管即零 claim 标记残留
    StaleRecoveryAcrossProcessesTest
                                    跨进程陈旧恢复两形态：
                                    (a) 死 pid（真实 subprocess 派生并
                                    wait 的已退出进程）+ 超龄 created_at
                                    → 子进程接管（generation = 栽种+1、
                                    锁重写为子进程 pid）；
                                    (b) 活 pid（测试进程自身）但 heartbeat
                                    超阈值（3600 秒前 >> 默认 180 秒）→
                                    子进程仍必须接管——heartbeat 过期
                                    优先于 pid 存活，时间戳远超默认阈值
                                    即注入、运行时有界
    DiesAfterLockTest               子进程 acquire 成功后 os._exit 不释放
                                    → 父进程经 pid 死亡判定接管成功
                                    （generation+1）；子进程经退出码 70
                                    + tempdir 结果文件双通道上报，绝无
                                    静默挂起
    StopStartRaceTest               stop/start 竞态：持有者 A 在场时父
                                    进程 request_stop（RMW 旗标落盘）→
                                    父进程作为第二 acquire 者对活锁确定
                                    性冲突 → A 释放 → 父进程干净重启
                                    （generation 延续 = 2、新纪元旗标
                                    复位）
    Windows 安全性 = 本套件自身：全套在 Windows 本机真实 spawn 下运行，
    零 POSIX-only API（死 pid 用 subprocess+wait，双平台可用）。

spawn 纪律（硬要求）：
    - 全部 Process target 为模块级函数（无闭包 / lambda / 实例方法），
      参数仅 pickleable 原语与路径 str；
    - 并发起点用 multiprocessing.Barrier / Event 同步，全部等待有界
      （barrier 10 秒、join 30 秒、队列收集 30 秒）；
    - worker 任何意外异常写入 multiprocessing.Queue（或 die-worker 的
      tempdir 结果文件）——绝不静默挂起；
    - 全部工作在 tempfile.TemporaryDirectory 仓库内：零真实网络、零模
      型调用、绝不触碰仓库内 .glm-conductor/ 真实账本；
    - finally 统一 terminate/join 全部进程（容忍已退出）；
    - 每用例秒级完成（新鲜锁冲突是 bounded single check；接管 settle
      ~0.05 秒）。

运行：
    cd <repo_root> && PYTHONUTF8=1 python3 -m unittest tests.test_multiprocess_runtime -v
"""

import json
import multiprocessing
import os
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from queue import Empty

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "glm-conductor"))
from runtime.quota import watcher_store

IDENTITY = "a1b2c3d4e5f60718"

BARRIER_TIMEOUT = 10.0     # barrier 等待上界（秒）
JOIN_TIMEOUT = 30.0        # 子进程 join 上界（秒）
DRAIN_TIMEOUT = 30.0       # 结果队列收集上界（秒）
EVENT_TIMEOUT = 15.0       # worker 内事件等待上界（秒）
DIE_EXIT_OK = 70           # die-worker「已 acquire」哨兵退出码（刻意避开
DIE_EXIT_NO = 71           #   STILL_ACTIVE=259，Windows pid 判活不误判）


def iso_seconds_ago(seconds):
    """距此刻 seconds 秒前的 ISO8601 UTC Z 串（构造陈旧锁/记录夹具）。"""
    moment = datetime.now(timezone.utc) - timedelta(seconds=seconds)
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


# —— 模块级 worker（spawn 纪律：target 必须可按 名字 pickle） ——

def _mp_acquire_barrier_worker(repo, identity, barrier, release_event, out_q):
    """场景 1 worker：barrier 同步同时 acquire；结果入队。胜者持锁等待
    release_event 再退出——父进程断言「恰一胜者」期间胜者必然存活，
    败者绝无「误判陈旧锁而接管」的时序窗口（exactly-one 是机械保证
    而非时序运气）。"""
    try:
        barrier.wait(timeout=BARRIER_TIMEOUT)
        result = watcher_store.acquire_watcher_lock(
            repo, provider_identity_hash=identity, mode="ACTIVE")
        out_q.put({"pid": os.getpid(),
                   "acquired": result["acquired"],
                   "takeover": result["takeover"],
                   "generation": result["generation"],
                   "conflict": result["conflict"]})
        if result["acquired"]:
            release_event.wait(timeout=EVENT_TIMEOUT)
    except BaseException as exc:  # 意外异常必须可见，绝不静默挂起
        out_q.put({"pid": os.getpid(), "error": repr(exc)})


def _mp_acquire_report_worker(repo, identity, out_q):
    """场景 2/3 worker：单发 acquire → 结果入队 → 正常退出。"""
    try:
        result = watcher_store.acquire_watcher_lock(
            repo, provider_identity_hash=identity, mode="ACTIVE")
        out_q.put({"pid": os.getpid(),
                   "acquired": result["acquired"],
                   "takeover": result["takeover"],
                   "generation": result["generation"]})
    except BaseException as exc:
        out_q.put({"pid": os.getpid(), "error": repr(exc)})


def _mp_acquire_die_worker(repo, identity, result_path):
    """场景 4 worker：acquire 成功后 os._exit 不释放（os._exit 不冲刷
    Queue feeder 线程——故走 tempdir 结果文件 + 退出码双通道上报；
    退出码刻意避开 259/STILL_ACTIVE）。"""
    outcome = {"pid": os.getpid()}
    try:
        result = watcher_store.acquire_watcher_lock(
            repo, provider_identity_hash=identity, mode="ACTIVE")
        outcome["acquired"] = result["acquired"]
        outcome["generation"] = result["generation"]
    except BaseException as exc:
        outcome["error"] = repr(exc)
    with open(result_path, "w", encoding="utf-8") as handle:
        json.dump(outcome, handle)
    os._exit(DIE_EXIT_OK if outcome.get("acquired") else DIE_EXIT_NO)


def _mp_holder_worker(repo, identity, acquired_event, release_event, out_q):
    """场景 5 worker：acquire 并持有 → acquired_event → 等 release_event
    → 按释放身份纪律释放 → released 结果入队。"""
    try:
        result = watcher_store.acquire_watcher_lock(
            repo, provider_identity_hash=identity, mode="ACTIVE")
        out_q.put({"pid": os.getpid(),
                   "acquired": result["acquired"],
                   "generation": result["generation"]})
        if not result["acquired"]:
            return
        acquired_event.set()
        if not release_event.wait(timeout=EVENT_TIMEOUT):
            out_q.put({"pid": os.getpid(),
                       "error": "release_event wait timeout"})
            return
        out_q.put({"pid": os.getpid(),
                   "released": watcher_store.release_watcher_lock(
                       repo, pid=os.getpid(),
                       generation=result["generation"])})
    except BaseException as exc:
        out_q.put({"pid": os.getpid(), "error": repr(exc)})


def _drain(out_q, count, timeout=DRAIN_TIMEOUT):
    """有界收集 count 个结果；超时返回已收到的部分（调用方断言数量）。"""
    results = []
    deadline = time.monotonic() + timeout
    while len(results) < count and time.monotonic() < deadline:
        try:
            results.append(out_q.get(timeout=0.5))
        except Empty:
            continue
    return results


def _terminate_all(processes):
    """清理纪律：terminate 所有仍存活进程并 join（容忍已退出）。"""
    for proc in processes:
        if proc.is_alive():
            proc.terminate()
    for proc in processes:
        proc.join(timeout=JOIN_TIMEOUT)


class MultiprocessFixture(unittest.TestCase):
    """tempdir 仓库基座（每用例独立 scratch 仓库 + 通用夹具助手）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = self._tmp.name
        self.state_path = watcher_store.watcher_state_path(self.repo)
        self.lock_path = watcher_store.watcher_lock_path(self.repo)

    def dead_pid(self):
        """真实派生一个立即退出的子进程并 wait → 保证已死的 pid
        （OS 原语零 mock；Windows/POSIX 双平台可用）。"""
        helper = os.path.join(self._tmp.name, "dead_pid_helper.py")
        with open(helper, "w", encoding="utf-8") as handle:
            handle.write("pass\n")
        process = subprocess.Popen([sys.executable, helper],
                                   stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL)
        process.wait(timeout=JOIN_TIMEOUT)
        return process.pid

    def write_lock(self, *, pid, generation=1, created_at=None):
        """手写 watcher.lock 夹具（构造陈旧锁仲裁输入）。"""
        os.makedirs(os.path.dirname(self.lock_path), exist_ok=True)
        payload = {"pid": pid, "generation": generation,
                   "provider_identity_hash": IDENTITY,
                   "created_at": created_at or iso_seconds_ago(0)}
        with open(self.lock_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True)

    def raw_lock(self):
        with open(self.lock_path, "r", encoding="utf-8") as handle:
            return json.load(handle)

    def raw_state(self):
        """直读 watcher.json 原文 json.load（任何撕裂/半份 JSON 在此即炸
        ——「文件全程合法 JSON」断言）。"""
        with open(self.state_path, "r", encoding="utf-8") as handle:
            return json.load(handle)

    def claim_residue(self):
        """目录内现存 claim 标记文件名列表（零残留断言用）。"""
        return [name for name in
                os.listdir(os.path.dirname(self.lock_path))
                if name.startswith(os.path.basename(self.lock_path)
                                   + watcher_store.LOCK_CLAIM_MARKER)]


# —— 场景 1：N=2..4 进程同时 acquire，恰一胜者 ——

class SimultaneousAcquireTest(MultiprocessFixture):
    """WU-221-A2 "New tests" 逐字场景：N 个 watcher-acquire 进程同时启动
    → 恰一个 acquired=True → 其余全部确定性冲突 → watcher.json 不损坏 →
    无重复 active generation。exactly-one-winner 由 O_EXCL 机械原子保证，
    非时序：胜者持锁存活到父进程断言结束（release_event 门控），败者
    绝无把新鲜锁误判陈旧而接管的窗口。"""

    def _assert_simultaneous_acquire(self, n):
        barrier = multiprocessing.Barrier(n)
        release_event = multiprocessing.Event()
        out_q = multiprocessing.Queue()
        processes = [
            multiprocessing.Process(
                target=_mp_acquire_barrier_worker,
                args=(self.repo, IDENTITY, barrier, release_event, out_q))
            for _ in range(n)]
        try:
            for proc in processes:
                proc.start()
            results = _drain(out_q, n)
            self.assertEqual(
                len(results), n,
                "workers did not all report (bounded collect %ss); got %r"
                % (DRAIN_TIMEOUT, results))
            errors = [r for r in results if r.get("error")]
            self.assertEqual(errors, [], "worker unexpected exception")
            winners = [r for r in results if r["acquired"]]
            losers = [r for r in results if not r["acquired"]]
            # 恰一胜者（机械互斥；不断言胜者身份——断言不变式）
            self.assertEqual(len(winners), 1,
                             "exactly one winner expected; got %r" % (results,))
            winner = winners[0]
            self.assertFalse(winner["takeover"])   # 无前锁：直取非接管
            self.assertEqual(winner["generation"], 1)
            # 其余全部确定性冲突（零写盘：generation/锁代次双 None + 带
            # 冲突证据 dict）
            for loser in losers:
                self.assertIsNone(loser["generation"])
                self.assertFalse(loser["takeover"])
                self.assertIsNotNone(
                    loser["conflict"],
                    "loser without conflict info: %r" % (loser,))
            # 锁本体：恰为胜者 pid + 唯一 generation（无重复 active
            # generation；败者从未写锁）
            lock = self.raw_lock()
            self.assertEqual(lock["pid"], winner["pid"])
            self.assertEqual(lock["generation"], 1)
            self.assertEqual(lock["provider_identity_hash"], IDENTITY)
            # watcher.json 全程合法 JSON（json.load 即断言）且与锁一致
            state = self.raw_state()
            self.assertEqual(state["pid"], winner["pid"])
            self.assertEqual(state["generation"], 1)
            self.assertEqual(state["provider_identity_hash"], IDENTITY)
            # 无接管即零 claim 标记残留
            self.assertEqual(self.claim_residue(), [])
        finally:
            release_event.set()  # 胜者放行退出（无论如何先放行再清场）
            _terminate_all(processes)

    def test_two_processes_exactly_one_winner(self):
        self._assert_simultaneous_acquire(2)

    def test_three_processes_exactly_one_winner(self):
        self._assert_simultaneous_acquire(3)

    def test_four_processes_exactly_one_winner(self):
        self._assert_simultaneous_acquire(4)


# —— 场景 2/3：跨进程陈旧恢复（死 pid / 活 pid+过期 heartbeat） ——

class StaleRecoveryAcrossProcessesTest(MultiprocessFixture):
    """父进程栽种陈旧锁 → 独立子进程 acquire 必须接管成功。"""

    def test_dead_pid_planted_lock_taken_over_by_child_process(self):
        """死 pid（真实已退出 subprocess）+ 超龄 created_at：子进程接管，
        generation = 栽种+1，锁/观察记录均重写为子进程 pid；零 claim
        残留。"""
        planted_generation = 4
        self.write_lock(pid=self.dead_pid(),
                        generation=planted_generation,
                        created_at=iso_seconds_ago(3600))
        out_q = multiprocessing.Queue()
        child = multiprocessing.Process(
            target=_mp_acquire_report_worker,
            args=(self.repo, IDENTITY, out_q))
        try:
            child.start()
            results = _drain(out_q, 1)
            self.assertEqual(len(results), 1,
                             "child did not report: %r" % (results,))
            result = results[0]
            self.assertNotIn("error", result,
                             "child unexpected exception: %r" % (result,))
            self.assertTrue(result["acquired"])
            self.assertTrue(result["takeover"])
            self.assertEqual(result["generation"], planted_generation + 1)
            child.join(timeout=JOIN_TIMEOUT)
            # 锁与观察记录都换成子进程 pid + 新 generation
            self.assertEqual(self.raw_lock()["pid"], result["pid"])
            self.assertEqual(self.raw_lock()["generation"],
                             planted_generation + 1)
            self.assertEqual(self.raw_state()["pid"], result["pid"])
            self.assertEqual(self.raw_state()["generation"],
                             planted_generation + 1)
            self.assertEqual(self.claim_residue(), [])
        finally:
            _terminate_all([child])

    def test_live_pid_stale_heartbeat_taken_over_by_child_process(self):
        """活 pid（测试进程自身全程存活）但 heartbeat 超阈值（3600 秒前
        >> 默认 180 秒阈值，时间戳远超默认即注入、运行时有界）：子进程
        仍必须接管——heartbeat 过期优先于 pid 存活。"""
        planted_generation = 5
        stale_iso = iso_seconds_ago(3600)
        self.write_lock(pid=os.getpid(),
                        generation=planted_generation,
                        created_at=stale_iso)
        watcher_store.write_watcher_state(self.repo, {
            "schema_version": 1,
            "provider_identity_hash": IDENTITY,
            "mode": "PASSIVE",
            "pid": os.getpid(),               # 活 pid
            "started_at": stale_iso,
            "heartbeat_at": stale_iso,        # 但 heartbeat 远超阈值
            "generation": planted_generation,
            "stop_requested": False,
            "last_observation": None,
        })
        out_q = multiprocessing.Queue()
        child = multiprocessing.Process(
            target=_mp_acquire_report_worker,
            args=(self.repo, IDENTITY, out_q))
        try:
            child.start()
            results = _drain(out_q, 1)
            self.assertEqual(len(results), 1,
                             "child did not report: %r" % (results,))
            result = results[0]
            self.assertNotIn("error", result,
                             "child unexpected exception: %r" % (result,))
            self.assertTrue(result["acquired"])
            self.assertTrue(result["takeover"])
            self.assertEqual(result["generation"], planted_generation + 1)
            child.join(timeout=JOIN_TIMEOUT)
            self.assertEqual(self.raw_lock()["pid"], result["pid"])
            self.assertEqual(self.raw_lock()["generation"],
                             planted_generation + 1)
            self.assertEqual(self.raw_state()["pid"], result["pid"])
            self.assertEqual(self.claim_residue(), [])
        finally:
            _terminate_all([child])


# —— 场景 4：acquire 后立刻死亡（不释放） → pid 死亡接管 ——

class DiesAfterLockTest(MultiprocessFixture):

    def test_child_dies_holding_lock_parent_takes_over(self):
        """子进程 acquire 成功后 os._exit 不释放：锁留存为死 pid 的孤儿
        锁；父进程随后 acquire 经 pid 死亡判定接管成功（generation+1、
        锁换父 pid）。子进程以退出码 70 + tempdir 结果文件双通道上报
        acquire 事实（os._exit 不冲刷 Queue——绝不静默挂起）。"""
        result_path = os.path.join(self.repo, "die_worker_result.json")
        child = multiprocessing.Process(
            target=_mp_acquire_die_worker,
            args=(self.repo, IDENTITY, result_path))
        try:
            child.start()
            child.join(timeout=JOIN_TIMEOUT)
            self.assertEqual(
                child.exitcode, DIE_EXIT_OK,
                "die worker exit code %r (expected %d=acquired; "
                "1=crash, %d=conflict)" % (child.exitcode, DIE_EXIT_OK,
                                           DIE_EXIT_NO))
            with open(result_path, "r", encoding="utf-8") as handle:
                outcome = json.load(handle)
            self.assertTrue(outcome["acquired"])
            self.assertEqual(outcome["generation"], 1)
            # 孤儿锁在盘上、持有者已死：pid 恒不等于父进程
            lock = self.raw_lock()
            self.assertEqual(lock["pid"], outcome["pid"])
            self.assertEqual(lock["generation"], 1)
            # 父进程（本进程内联调用）接管成功
            takeover = watcher_store.acquire_watcher_lock(
                self.repo, provider_identity_hash=IDENTITY, mode="ACTIVE")
            self.assertTrue(takeover["acquired"])
            self.assertTrue(takeover["takeover"])
            self.assertEqual(takeover["generation"], 2)
            self.assertEqual(self.raw_lock()["pid"], os.getpid())
            self.assertEqual(self.raw_lock()["generation"], 2)
            self.assertEqual(self.claim_residue(), [])
        finally:
            _terminate_all([child])


# —— 场景 5：stop/start 竞态 ——

class StopStartRaceTest(MultiprocessFixture):
    """持有者 A（子进程）在场：request_stop 落旗标 → 第二 acquire 者
    （父进程）对活锁确定型冲突 → A 释放 → 干净重启（generation 延续、
    新纪元旗标复位）。"""

    def test_stop_flag_conflict_then_release_then_clean_restart(self):
        acquired_event = multiprocessing.Event()
        release_event = multiprocessing.Event()
        out_q = multiprocessing.Queue()
        holder = multiprocessing.Process(
            target=_mp_holder_worker,
            args=(self.repo, IDENTITY, acquired_event, release_event, out_q))
        try:
            holder.start()
            # A 获取并持有
            self.assertTrue(acquired_event.wait(timeout=BARRIER_TIMEOUT),
                            "holder never signaled acquired")
            first = _drain(out_q, 1)[0]
            self.assertEqual(first["acquired"], True)
            self.assertEqual(first["generation"], 1)
            holder_generation = first["generation"]
            # 父进程 request_stop：stop 旗标经 RMW 落入 watcher.json
            stopped = watcher_store.request_stop(self.repo)
            self.assertIsNotNone(stopped)
            self.assertTrue(stopped["stop_requested"])
            self.assertEqual(stopped["pid"], holder.pid)
            # A 仍持有且 pid 存活：第二 acquire 者确定型冲突
            # （冲突证据 pid 恒为持有者 pid——锁/记录早已落盘，无
            # corrupt 窗口，此断言确定性成立）
            blocked = watcher_store.acquire_watcher_lock(
                self.repo, provider_identity_hash=IDENTITY, mode="ACTIVE")
            self.assertFalse(blocked["acquired"])
            self.assertEqual(blocked["conflict"]["pid"], holder.pid)
            # A 释放（按释放身份纪律删除自己的锁）
            release_event.set()
            released = _drain(out_q, 1)
            self.assertEqual(len(released), 1,
                             "holder never reported release: %r"
                             % (released,))
            self.assertNotIn("error", released[0],
                             "holder unexpected exception: %r"
                             % (released[0],))
            self.assertTrue(released[0]["released"])
            holder.join(timeout=JOIN_TIMEOUT)
            # B（父进程）干净重启：generation 延续 = 2，新纪元旗标复位
            restart = watcher_store.acquire_watcher_lock(
                self.repo, provider_identity_hash=IDENTITY, mode="ACTIVE")
            self.assertTrue(restart["acquired"])
            self.assertFalse(restart["takeover"])  # 锁已被 A 正常释放
            self.assertEqual(restart["generation"], holder_generation + 1)
            self.assertEqual(self.raw_lock()["pid"], os.getpid())
            self.assertFalse(self.raw_state()["stop_requested"])
        finally:
            release_event.set()
            _terminate_all([holder])


if __name__ == "__main__":
    unittest.main()
