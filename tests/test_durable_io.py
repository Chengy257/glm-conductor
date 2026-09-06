#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""runtime.durable_io 单元测试（v2.2.1 hardening，WU-221-A1）。

锚定对象：v2.2 根因（多进程写共享 JSON 时固定 <path>.tmp 临时名相撞）
→ 共享原语层四公共名：唯一临时名原子写 / 容错读 / 独占锁 RMW /
O_CREAT|O_EXCL 独占锁（claim-marker race-safe 陈旧接管）。测试类映射：

    WriteReadRoundtripTest        写→读往返（非 ASCII 载荷 + 落盘形态）
    UniqueTempTest                两次连续写临时名绝不相同（spy os.replace）
    ConcurrentWritersTest         4 线程 × 20 并发写：终值恒为某一完整载荷
    PermissionErrorRetryTest      os.replace 注入 PermissionError：有界重试
    AbandonedTempCleanupTest      同前缀废弃临时清理 / 非匹配文件不碰
    ReadFaultToleranceTest        缺失 → default；损坏 → default
    ExclusiveLockTest             基本获取/释放；第二获取忙错（有界）；释放
                                  不误删后继锁
    StaleTakeoverTest             死 pid + 超龄 created_at → 认领接管
                                  （generation+1、锁内容换新）
    ClaimRaceTest                 2 线程同时抢陈旧锁：恰一人胜，另一人
                                  确定型 LockBusyError；胜者锁内容正确
    UpdateJsonTest                RMW 互斥计数 4×50=200；updater 抛错目标
                                  不被触碰；缺失文件以空 dict 起底

全部离线：文件操作均在 tempfile.TemporaryDirectory 内（绝不触碰仓库内
.glm-conductor/ 真实账本）；PermissionError 只按规格 monkeypatch
os.replace 注入、重试间隔注入 0（零真实等待）；OS 原语零 mock——死 pid
用真实 subprocess 派生并 wait 后的已退出进程；跨平台：不用 POSIX-only
API（threading / subprocess / os.utime 双平台可用）。

运行：
    cd <repo_root> && PYTHONUTF8=1 python3 -m unittest tests.test_durable_io -v
"""

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "glm-conductor"))
from runtime import durable_io


def iso_seconds_ago(seconds):
    """距此刻 seconds 秒前的 ISO8601 UTC Z 串（构造陈旧/新鲜锁内容用）。"""
    moment = datetime.now(timezone.utc) - timedelta(seconds=seconds)
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def noop_sleep(_seconds):
    """零等待休眠桩（注入重试路径，测试零真实等待）。"""


class DurableIOFixture(unittest.TestCase):
    """tempdir 基座：每用例独立 scratch 目录 + 目标路径 + 锁路径。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.scratch = self._tmp.name
        self.target = os.path.join(self.scratch, "state.json")
        self.lock_path = self.target + durable_io.LOCK_SUFFIX

    # —— 夹具助手 ——

    def dead_pid(self):
        """真实已退出进程的 pid（subprocess 派生 + wait；OS 原语零 mock）。"""
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        proc.wait()
        return proc.pid

    def raw_write(self, path, text):
        """绕过被测 API 直写磁盘原文（构造夹具 / 断言落盘形态用）。"""
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)

    def raw_read(self, path):
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read()

    def backdate(self, path, seconds=3600.0):
        """把文件 mtime 回拨 seconds 秒（跨平台，龄期闸测试用）。"""
        stamp = time.time() - seconds
        os.utime(path, (stamp, stamp))

    def plant_lock(self, pid, generation, created_at):
        """绕过被测 API 直接落一个锁文件（内容 JSON）。"""
        self.raw_write(self.lock_path, json.dumps(
            {"pid": pid, "generation": generation, "created_at": created_at},
            ensure_ascii=False))

    def temp_names(self):
        """scratch 目录内本模块前缀临时文件的文件名列表。"""
        prefix = "state.json" + durable_io.TEMP_MARKER
        return sorted(n for n in os.listdir(self.scratch)
                      if n.startswith(prefix))


# —— 写→读往返 ——

class WriteReadRoundtripTest(DurableIOFixture):

    def test_roundtrip_unicode_payload(self):
        """非 ASCII 载荷写→读往返一致；ensure_ascii=False 直落 UTF-8 原文
        （无 \\u 转义残影），换行固定 \\n（无 Windows \\r\\n）。"""
        payload = {"标题": "会话状态", "条目": ["甲", "乙", "café"],
                   "数量": 7, "nested": {"键": None}}
        returned = durable_io.atomic_write_json(self.target, payload)
        self.assertEqual(returned, self.target)
        self.assertEqual(durable_io.atomic_read_json(self.target), payload)
        raw = self.raw_read(self.target)
        self.assertIn("会话状态", raw)      # 直落 UTF-8 原文
        self.assertNotIn("\\u", raw)        # 无 ASCII 转义
        self.assertNotIn("\r\n", raw)       # 固定 \n 换行


# —— 临时名唯一性 ——

class UniqueTempTest(DurableIOFixture):

    def test_two_quick_writes_never_share_temp_path(self):
        """spy os.replace 捕获两次连续写的临时名：绝不相同、永不复用固定
        .tmp 名（每次调用唯一是本单元的立项硬要求）。"""
        seen = []
        real_replace = os.replace

        def spy_replace(src, dst):
            seen.append(os.path.basename(src))
            return real_replace(src, dst)

        with mock.patch("os.replace", side_effect=spy_replace):
            durable_io.atomic_write_json(self.target, {"seq": 1})
            durable_io.atomic_write_json(self.target, {"seq": 2})
        self.assertEqual(len(seen), 2)
        self.assertNotEqual(seen[0], seen[1])
        prefix = "state.json" + durable_io.TEMP_MARKER
        for name in seen:
            self.assertTrue(name.startswith(prefix))
            self.assertTrue(name.endswith(".tmp"))
            self.assertNotEqual(name, "state.json.tmp")  # 固定名禁出现


# —— 并发写 ——

class ConcurrentWritersTest(DurableIOFixture):

    def test_four_writers_final_content_is_exactly_one_payload(self):
        """4 线程 × 20 次写互异载荷：全程无异常，终文件恒为合法 JSON 且
        等于恰好一个已写载荷（唯一临时名 + os.replace 原子性）。"""
        writers, iterations = 4, 20
        expected = [{"writer": w, "seq": s, "数据": "值-%d-%d" % (w, s)}
                    for w in range(writers) for s in range(iterations)]
        errors = []
        barrier = threading.Barrier(writers)

        def writer(w):
            try:
                barrier.wait(timeout=10)
                for s in range(iterations):
                    durable_io.atomic_write_json(
                        self.target, {"writer": w, "seq": s,
                                      "数据": "值-%d-%d" % (w, s)})
            except BaseException as exc:  # pragma: no cover - 失败诊断面
                errors.append(exc)

        threads = [threading.Thread(target=writer, args=(w,))
                   for w in range(writers)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
        self.assertEqual(errors, [])
        final = durable_io.atomic_read_json(self.target, default=None)
        self.assertIsNotNone(final)
        self.assertIn(final, expected)  # 恰为某一完整载荷，绝无撕裂混合


# —— PermissionError 有界重试 ——

class PermissionErrorRetryTest(DurableIOFixture):

    def test_two_transient_failures_then_success(self):
        """os.replace 前 2 次抛 PermissionError、第 3 次放行真实 replace：
        写成功且恰好观察到 3 次尝试（有界重试、间隔注入 0 零真实等待）。"""
        real_replace = os.replace
        calls = []

        def flaky_replace(src, dst):
            calls.append(1)
            if len(calls) <= 2:
                raise PermissionError(
                    "[injected] 瞬态拒绝 %d" % len(calls))
            return real_replace(src, dst)

        with mock.patch("os.replace", side_effect=flaky_replace):
            returned = durable_io.atomic_write_json(
                self.target, {"ok": True}, retry_interval=0,
                sleep=noop_sleep)
        self.assertEqual(returned, self.target)
        self.assertEqual(len(calls), 3)  # 2 次拒绝 + 1 次成功
        self.assertEqual(durable_io.atomic_read_json(self.target), {"ok": True})
        self.assertEqual(self.temp_names(), [])  # 成功路径零临时残留

    def test_exhaustion_raises_and_leaves_no_temp(self):
        """os.replace 恒拒绝：有界重试耗尽后原样上抛 PermissionError
        （不换皮不静默），目标不存在、临时文件零残留。"""
        def always_denied(src, dst):
            raise PermissionError("[injected] 恒定拒绝")

        with mock.patch("os.replace", side_effect=always_denied):
            with self.assertRaises(PermissionError):
                durable_io.atomic_write_json(self.target, {"x": 1},
                                             retry_attempts=3,
                                             retry_interval=0,
                                             sleep=noop_sleep)
        self.assertFalse(os.path.exists(self.target))
        self.assertEqual(self.temp_names(), [])


# —— 废弃临时清理 ——

class AbandonedTempCleanupTest(DurableIOFixture):

    def test_matching_abandoned_removed_others_untouched(self):
        """同前缀 + 超龄的废弃临时被顺带清理；龄期不足的同前缀临时与
        非匹配文件名一律不碰（清理失败也绝不传播）。"""
        abandoned = os.path.join(
            self.scratch, "state.json.durable-tmp.424242.%s.tmp" % ("ab" * 16))
        fresh_tmp = os.path.join(
            self.scratch, "state.json.durable-tmp.111111.%s.tmp" % ("cd" * 16))
        keeper = os.path.join(self.scratch, "unrelated.txt")
        for path, body in ((abandoned, "垃圾"), (fresh_tmp, "fresh"),
                           (keeper, "keep")):
            self.raw_write(path, body)
        self.backdate(abandoned, seconds=3600)
        self.backdate(keeper, seconds=3600)  # 非匹配名即便超龄也不碰

        durable_io.atomic_write_json(self.target, {"clean": True})

        self.assertFalse(os.path.exists(abandoned))   # 废弃临时被清
        self.assertTrue(os.path.exists(fresh_tmp))    # 在写龄期闸保护
        self.assertTrue(os.path.exists(keeper))       # 非匹配名不碰


# —— 容错读 ——

class ReadFaultToleranceTest(DurableIOFixture):

    def test_missing_file_returns_default(self):
        sentinel = {"absent": True}
        self.assertIs(
            durable_io.atomic_read_json(self.target, sentinel), sentinel)
        self.assertIsNone(durable_io.atomic_read_json(self.target))  # 缺省 None

    def test_corrupt_content_returns_default(self):
        self.raw_write(self.target, "{这 不是 JSON")
        sentinel = []
        self.assertIs(
            durable_io.atomic_read_json(self.target, sentinel), sentinel)

    def test_valid_content_beats_default(self):
        durable_io.atomic_write_json(self.target, {"v": 1})
        self.assertEqual(durable_io.atomic_read_json(self.target, "默认"),
                         {"v": 1})


# —— 独占锁 ——

class ExclusiveLockTest(DurableIOFixture):

    def test_acquire_release_basic(self):
        """基本获取/释放：锁内文件存在且内容含 pid/generation/created_at
        三键；with 退出后锁文件消失。"""
        with durable_io.exclusive_lock(self.lock_path) as generation:
            self.assertEqual(generation, 1)
            self.assertTrue(os.path.exists(self.lock_path))
            content = json.loads(self.raw_read(self.lock_path))
            for key in ("pid", "generation", "created_at", "owner"):
                self.assertIn(key, content)
            self.assertEqual(content["pid"], os.getpid())
            self.assertEqual(content["generation"], 1)
            self.assertIsInstance(content["created_at"], str)
            self.assertTrue(content["owner"])  # 线程级身份键（uuid4 hex）
        self.assertFalse(os.path.exists(self.lock_path))

    def test_second_acquire_busy_within_bounded_time(self):
        """新鲜持有者在场：第二获取在有界时间内以确定型 LockBusyError
        失败（绝不无限等待、绝不静默共享锁）。"""
        with durable_io.exclusive_lock(self.lock_path):
            started = time.monotonic()
            with self.assertRaises(durable_io.LockBusyError):
                with durable_io.exclusive_lock(self.lock_path,
                                               timeout_seconds=0.3,
                                               poll_interval=0.02):
                    pass  # pragma: no cover - 不可达
            elapsed = time.monotonic() - started
        self.assertLess(elapsed, 5.0)  # 有界（0.3 秒超时 + 机器余量）

    def test_release_does_not_delete_successor_lock(self):
        """持锁 A 期间锁内容被外部换成后继 (pid, generation=99)（模拟被
        接管）：释放 A 不得删锁——后继的锁原样保留。"""
        with durable_io.exclusive_lock(self.lock_path):
            successor = {"pid": os.getpid(), "generation": 99,
                         "created_at": iso_seconds_ago(0)}
            self.raw_write(self.lock_path, json.dumps(successor))
        self.assertTrue(os.path.exists(self.lock_path))  # 未被误删
        self.assertEqual(json.loads(self.raw_read(self.lock_path))["generation"],
                         99)


# —— 陈旧锁接管 ——

class StaleTakeoverTest(DurableIOFixture):

    def test_dead_pid_old_created_at_takeover(self):
        """死 pid（真实已退出进程）+ 超龄 created_at → 新 exclusive_lock
        经认领接管获取成功：generation +1、锁内容换成自己的 pid 与新
        created_at；释放后锁文件消失。"""
        dead = self.dead_pid()
        old_created = iso_seconds_ago(3600)
        self.plant_lock(dead, 3, old_created)

        with durable_io.exclusive_lock(self.lock_path) as generation:
            self.assertEqual(generation, 4)  # 陈旧 3 → 接管 +1
            content = json.loads(self.raw_read(self.lock_path))
            self.assertEqual(content["pid"], os.getpid())
            self.assertEqual(content["generation"], 4)
            self.assertNotEqual(content["created_at"], old_created)
        self.assertFalse(os.path.exists(self.lock_path))

    def test_old_created_at_alone_is_stale(self):
        """created_at 超龄单条件即判陈旧（pid 判定不可靠时的保守接管面）。"""
        self.plant_lock(os.getpid(), 2, iso_seconds_ago(3600))
        with durable_io.exclusive_lock(self.lock_path) as generation:
            self.assertEqual(generation, 3)
        self.assertFalse(os.path.exists(self.lock_path))


# —— 认领仲裁竞态 ——

class ClaimRaceTest(DurableIOFixture):

    def test_two_contenders_exactly_one_wins(self):
        """陈旧锁 + 2 线程同时抢：恰好一人经认领仲裁接管成功，另一人以
        确定型 LockBusyError 落败；胜者锁内容含本进程 pid 与 generation+1；
        释放后锁消失、零认领标记残留。"""
        dead = self.dead_pid()
        self.plant_lock(dead, 7, iso_seconds_ago(3600))
        acquired = threading.Event()
        release = threading.Event()
        outcomes = []
        barrier = threading.Barrier(2)

        def contender():
            barrier.wait(timeout=10)
            try:
                with durable_io.exclusive_lock(self.lock_path,
                                               timeout_seconds=2.0,
                                               poll_interval=0.01):
                    outcomes.append("acquired")
                    acquired.set()
                    release.wait(timeout=10)
            except durable_io.LockBusyError:
                outcomes.append("busy")

        threads = [threading.Thread(target=contender) for _ in range(2)]
        for t in threads:
            t.start()
        self.assertTrue(acquired.wait(15))  # 恰有一人拿到锁
        deadline = time.monotonic() + 8
        while len(outcomes) < 2 and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertEqual(sorted(outcomes), ["acquired", "busy"])
        content = json.loads(self.raw_read(self.lock_path))  # 胜者仍持锁
        self.assertEqual(content["pid"], os.getpid())
        self.assertEqual(content["generation"], 8)
        release.set()
        for t in threads:
            t.join(timeout=10)
        self.assertFalse(os.path.exists(self.lock_path))
        self.assertEqual(self.lock_claim_markers(), [])

    def lock_claim_markers(self):
        prefix = "state.json.lock" + durable_io.CLAIM_MARKER
        return [n for n in os.listdir(self.scratch) if n.startswith(prefix)]


# —— atomic_update_json ——

class UpdateJsonTest(DurableIOFixture):

    def test_counter_mutual_exclusion_exactly_200(self):
        """4 线程 × 各 50 次 RMW 自增：终值恰为 200（锁互斥 + 原子写，
        零丢失更新）；结束后锁文件消失。"""
        errors = []

        def bump(payload):
            return {"count": int(payload.get("count", 0)) + 1}

        def worker():
            try:
                for _ in range(50):
                    # 重试上界按本机 AV / 索引器实况放宽（重试注入参数的
                    # 既定用途）：200 次高频写放下 os.replace 瞬态拒绝窗口
                    # 会超过模块默认 5×0.1 秒。
                    durable_io.atomic_update_json(
                        self.target, bump, poll_interval=0.005,
                        retry_attempts=20, retry_interval=0.05)
            except BaseException as exc:  # pragma: no cover - 失败诊断面
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=120)
        self.assertEqual(errors, [])
        self.assertEqual(durable_io.atomic_read_json(self.target),
                         {"count": 200})
        self.assertFalse(os.path.exists(self.lock_path))

    def test_updater_exception_leaves_target_untouched(self):
        """updater 抛错：异常原样传播、目标文件保持原值、锁经 finally
        必释放。"""
        durable_io.atomic_write_json(self.target, {"v": 1})

        def boom(_payload):
            raise RuntimeError("updater 注入失败")

        with self.assertRaises(RuntimeError):
            durable_io.atomic_update_json(self.target, boom)
        self.assertEqual(durable_io.atomic_read_json(self.target), {"v": 1})
        self.assertFalse(os.path.exists(self.lock_path))

    def test_missing_file_starts_from_empty_dict(self):
        """文件缺失：updater 收到空 dict（default 未给）；返回值 = 新
        payload 且已落盘。"""
        seen = {}

        def record(payload):
            seen.update(payload)
            seen["init"] = True
            return {"seeded": True}

        result = durable_io.atomic_update_json(self.target, record)
        self.assertEqual(seen, {"init": True})
        self.assertEqual(result, {"seeded": True})
        self.assertEqual(durable_io.atomic_read_json(self.target),
                         {"seeded": True})

    def test_default_kwarg_overrides_missing_baseline(self):
        """文件缺失且显式给 default：updater 收到 default 而非空 dict。"""
        seen = {}

        def record(payload):
            seen.update(payload)
            return {"n": int(payload.get("n", 0)) + 1}

        durable_io.atomic_update_json(self.target, record,
                                      default={"n": 41})
        self.assertEqual(seen, {"n": 41})
        self.assertEqual(durable_io.atomic_read_json(self.target), {"n": 42})


if __name__ == "__main__":
    unittest.main()
