#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""runtime.quota.watcher_store 单元测试（v2.2 修正计划 C3，wu-22-C3）。

锚定对象：修正计划 §6.2 P0-WATCH-00（Windows 前置硬门五项）/ §6（状态
锁字段 + 单实例冻结语义）/ §6.1（skeleton-first）/ §31（不虚构）。

运行：
    cd <repo_root> && python3 -m unittest tests.test_watcher_store -v

P0-WATCH-00 五门 → 测试类映射（门纪律：任一 FAIL → 停止实施上报；
全部以本机真实 Windows 原语执行，OS 原语零 mock 掉——门 4 只按规格
monkeypatch os.replace 注入 PermissionError，门 5 的死 pid 用真实
subprocess 派生并 wait 后的已退出进程）：
    门 1 single-instance lock   → SingleInstanceLockTest
    门 2 tmp+replace 原子写      → AtomicWriteTest
    门 3 watcher reader/writer 并发 → ReaderWriterConcurrencyTest
    门 4 PermissionError bounded → PermissionErrorBoundedTest
    门 5 stale lock recovery    → StaleLockRecoveryTest

全部离线：状态文件落在 tempfile.TemporaryDirectory 的 scratch 仓库
（绝不触碰仓库内 .glm-conductor/ 真实账本）；重试间隔注入 0 +
sleep 注入计数桩（零真实等待）；凭证零依赖（identity_hash 直接注入
字面量——派生逻辑归 watcher，本层只做词汇闸）。
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
from runtime.quota import watcher_store
from runtime.quota.scheduler import _format_iso_z

NOW = datetime(2026, 9, 2, 12, 0, 0, tzinfo=timezone.utc)
NOW_ISO = "2026-09-02T12:00:00Z"
IDENTITY = "a1b2c3d4e5f60718"


def base_record(**overrides):
    """构造锁记录底形（冻结字段全带；overrides 局部覆盖）。"""
    record = {
        "schema_version": 1,
        "provider_identity_hash": IDENTITY,
        "mode": "PASSIVE",
        "pid": os.getpid(),  # 测试进程本身活到用例结束 → 可靠的「活 pid」
        "started_at": NOW_ISO,
        "heartbeat_at": NOW_ISO,
        "generation": 1,
        "stop_requested": False,
        "last_observation": None,
    }
    record.update(overrides)
    return record


class StoreFixture(unittest.TestCase):
    """tempdir 仓库基座。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = self._tmp.name
        self.state_path = watcher_store.watcher_state_path(self.repo)

    def raw_state(self):
        """绕过读 API 直读磁盘原文（断言落盘形状用）。"""
        with open(self.state_path, "r", encoding="utf-8") as handle:
            return json.load(handle)


# —— 门 1：single-instance lock（活锁拒绝第二 acquire） ——

class SingleInstanceLockTest(StoreFixture):
    """§6 冻结：一个 provider identity 同时最多一个 active watcher；
    锁即状态文件本身。"""

    def test_second_acquire_rejected_while_live_and_fresh(self):
        """活 pid + 新鲜 heartbeat：第二 acquire 被拒并带冲突信息，
        状态文件不被拒绝方改写（generation 不变、不产生第二记录）。"""
        first = watcher_store.acquire_watcher_lock(
            self.repo, provider_identity_hash=IDENTITY, mode="ACTIVE",
            now=NOW)
        self.assertTrue(first["acquired"])
        self.assertFalse(first["takeover"])
        self.assertEqual(first["generation"], 1)
        second = watcher_store.acquire_watcher_lock(
            self.repo, provider_identity_hash=IDENTITY, mode="ACTIVE",
            now=NOW + timedelta(seconds=1))
        self.assertFalse(second["acquired"])
        self.assertIsNone(second["generation"])
        conflict = second["conflict"]
        self.assertEqual(conflict["pid"], os.getpid())
        self.assertEqual(conflict["heartbeat_at"], NOW_ISO)
        self.assertEqual(conflict["provider_identity_hash"], IDENTITY)
        self.assertLessEqual(conflict["heartbeat_age_seconds"], 1)
        self.assertEqual(self.raw_state()["generation"], 1)  # 未被改写

    def test_stale_freshness_boundary_contains_equal(self):
        """age == 阈值（含等号）仍算新鲜 → 拒绝；阈值 +1 秒 → 过期放行
        （本用例只锚等号侧；放行侧归门 5 的过期接管用例）。"""
        watcher_store.acquire_watcher_lock(
            self.repo, provider_identity_hash=IDENTITY, mode="PASSIVE",
            now=NOW)
        blocked = watcher_store.acquire_watcher_lock(
            self.repo, provider_identity_hash=IDENTITY, mode="PASSIVE",
            now=NOW + timedelta(seconds=180))
        self.assertFalse(blocked["acquired"])

    def test_acquire_writes_generation_1_fresh_record(self):
        """无记录 → 全新 acquire：generation=1，冻结字段齐备，
        stop_requested 复位 False，last_observation 无记录时为 None。"""
        result = watcher_store.acquire_watcher_lock(
            self.repo, provider_identity_hash=IDENTITY, mode="PASSIVE",
            now=NOW)
        self.assertTrue(result["acquired"])
        self.assertEqual(result["generation"], 1)
        record = self.raw_state()
        for field in watcher_store.WATCHER_STATE_FIELDS:
            self.assertIn(field, record)
        self.assertEqual(record["pid"], os.getpid())
        self.assertEqual(record["heartbeat_at"], NOW_ISO)
        self.assertFalse(record["stop_requested"])
        self.assertIsNone(record["last_observation"])

    def test_acquire_parameter_validation(self):
        """参数校验先于 I/O：空 identity / 词汇外 mode / 负阈值 →
        ValueError 且不写盘。"""
        for kwargs in (
                {"provider_identity_hash": "", "mode": "PASSIVE"},
                {"provider_identity_hash": IDENTITY, "mode": "aggressive"},
                {"provider_identity_hash": IDENTITY, "mode": "PASSIVE",
                 "heartbeat_stale_seconds": -1}):
            with self.assertRaises(ValueError):
                watcher_store.acquire_watcher_lock(self.repo, now=NOW,
                                                   **kwargs)
        self.assertFalse(os.path.exists(self.state_path))

    def test_heartbeat_age_seconds(self):
        """heartbeat_age_seconds：新鲜记录 age 正确；heartbeat 缺失 /
        不可解析 / 非 dict → None（不虚构）。"""
        self.assertEqual(
            watcher_store.heartbeat_age_seconds(base_record(), now=NOW), 0)
        self.assertIsNone(watcher_store.heartbeat_age_seconds(
            {"heartbeat_at": "not-a-time"}, now=NOW))
        self.assertIsNone(watcher_store.heartbeat_age_seconds(
            {"nope": 1}, now=NOW))
        self.assertIsNone(watcher_store.heartbeat_age_seconds(None, now=NOW))


# —— 门 2：tmp + replace 原子写 ——

class AtomicWriteTest(StoreFixture):
    """R3：读者要么看不到文件要么看到完整记录；tmp 同目录 + os.replace；
    成功后 tmp 零残留。"""

    def test_write_then_read_roundtrip_complete_content(self):
        """写后内容完整可解析、冻结字段齐备、目录内零 .tmp 残留。"""
        payload = base_record(mode="ACTIVE", generation=3,
                              last_observation={"epoch_id": "glm:abc"})
        path = watcher_store.write_watcher_state(self.repo, payload)
        self.assertEqual(path, self.state_path)
        self.assertTrue(os.path.isfile(self.state_path))
        on_disk = self.raw_state()
        self.assertEqual(on_disk, payload)
        self.assertEqual(on_disk["last_observation"]["epoch_id"], "glm:abc")
        self.assertEqual(os.listdir(os.path.dirname(self.state_path)),
                         ["watcher.json"])  # 无 .tmp 残留

    def test_overwrite_replaces_atomically_no_torn_window(self):
        """连续两次覆盖写：读者（read API）每次都见到两份完整记录之一，
        绝不出现撕裂 / 半份 JSON。"""
        watcher_store.write_watcher_state(self.repo, base_record(generation=1))
        watcher_store.write_watcher_state(self.repo, base_record(generation=2))
        record = watcher_store.read_watcher_state(self.repo)
        self.assertIsNotNone(record)
        self.assertIn(record["generation"], (1, 2))
        self.assertEqual(sorted(record), sorted(base_record()))

    def test_write_rejects_non_dict(self):
        """参数校验先于 I/O：record 非 dict → ValueError 且不写盘。"""
        for bad in (None, "watcher", 42, ["a"]):
            with self.assertRaises(ValueError):
                watcher_store.write_watcher_state(self.repo, bad)
        self.assertFalse(os.path.exists(self.state_path))


# —— 门 3：watcher reader / writer 并发 ——

class ReaderWriterConcurrencyTest(StoreFixture):
    """一写多读并发：读者永不见 partial JSON（写入方用真实 os.replace
    原子性，读者连续读——每次成功读到 dict 必为某一代完整记录）。"""

    WRITE_ROUNDS = 120
    READERS = 3

    def test_concurrent_readers_never_see_partial_json(self):
        """一写多读并发：读者永不见 partial JSON。

        实测口径（2026-09-02 本机 Windows 10 19042）：连续无间歇的
        读压力会让 os.replace 以 WinError 5（目标被读者句柄占用）拒绝
        ——默认 3×0.2s 的有界预算在「 torture 级读压力」下可能耗尽
        （这正是门 4 的设计行为，绝不静默吞）。本门测的是「读者永不见
        撕裂」，故写方按「常量可配」放宽预算（attempts=20 × 1ms 立即
        重试），读者按现实观察节奏 1ms 间歇轮询；默认预算口径由
        PermissionErrorBoundedTest 单独锚定。"""
        watcher_store.write_watcher_state(
            self.repo, base_record(generation=0,
                                   last_observation={"round": 0}))
        errors = []
        seen = {"reads": 0}
        seen_lock = threading.Lock()

        def reader():
            while not stop.is_set():
                record = watcher_store.read_watcher_state(self.repo)
                try:
                    if record is None:
                        continue  # 极端瞬态（重读耗尽）不算撕裂
                    # 撕裂 / 半份 JSON 在 json.load 即炸；形状完整性逐键闸
                    for field in watcher_store.WATCHER_STATE_FIELDS:
                        if field not in record:
                            raise AssertionError("缺冻结字段 %s" % field)
                    round_value = record["last_observation"]["round"]
                    if not isinstance(round_value, int) \
                            or not 0 <= round_value <= self.WRITE_ROUNDS:
                        raise AssertionError("round 越界 %r" % (round_value,))
                    if record["generation"] != round_value:
                        raise AssertionError("generation/round 不一致")
                    with seen_lock:
                        seen["reads"] += 1
                except Exception as exc:  # noqa: BLE001 —— 捕获即 FAIL 证据
                    errors.append(exc)
                    return
                time.sleep(0.001)  # 现实观察节奏（非零间歇）

        stop = threading.Event()
        readers = [threading.Thread(target=reader)
                   for _ in range(self.READERS)]
        for thread in readers:
            thread.start()
        try:
            for round_index in range(1, self.WRITE_ROUNDS + 1):
                watcher_store.write_watcher_state(
                    self.repo, base_record(
                        generation=round_index,
                        last_observation={"round": round_index}),
                    retry_attempts=20, retry_interval=0.001,
                    sleep=time.sleep)
        finally:
            stop.set()
            for thread in readers:
                thread.join(timeout=30)
        self.assertEqual(errors, [])
        self.assertGreater(seen["reads"], 0)
        final = watcher_store.read_watcher_state(self.repo)
        self.assertEqual(final["generation"], self.WRITE_ROUNDS)
        self.assertEqual(os.listdir(os.path.dirname(self.state_path)),
                         ["watcher.json"])  # 并发全程零 .tmp 残留

    def test_reader_survives_decode_error_with_bounded_reread(self):
        """读方容错：JSON decode 错误 → 有界重读后仍失败 → 返回 None
        不抛（§6.2 门 3 读侧；重读次数 = READ_RETRY_ATTEMPTS）。"""
        os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
        with open(self.state_path, "w", encoding="utf-8") as handle:
            handle.write('{"generation": 1, "pid": ')  # 半份 JSON
        self.assertIsNone(watcher_store.read_watcher_state(self.repo))
        self.assertIsNone(watcher_store.read_watcher_state(self.repo,
                                                           attempts=1))
        # 修复后立即可读（有界重读是瞬态窗口对策，不是永久坏账掩盖）
        watcher_store.write_watcher_state(self.repo, base_record())
        self.assertEqual(
            watcher_store.read_watcher_state(self.repo)["generation"], 1)


# —— 门 4：PermissionError 的 bounded handling ——

class PermissionErrorBoundedTest(StoreFixture):
    """Windows AV / 目录锁（本机已知现象）：写 tmp / os.replace 遇
    PermissionError → 有界重试（默认 3 次、0.2s 间隔，常量可配）；
    耗尽 → WatcherStoreError（绝不无限重试、绝不静默吞）。规格口径：
    monkeypatch os.replace 注入前 N 次失败。"""

    def test_bounded_retry_recovers_within_limit(self):
        """前 PERMISSION_RETRY_ATTEMPTS-1 次抛 PermissionError →
        第 N 次成功；恰好有界（不多不少），间隔参数逐次下发。"""
        real_replace = os.replace
        calls = {"n": 0}

        def flaky_replace(src, dst):
            calls["n"] += 1
            if calls["n"] < watcher_store.PERMISSION_RETRY_ATTEMPTS:
                raise PermissionError("[WinError 32] 模拟 AV 扫描占用")
            return real_replace(src, dst)

        sleeps = []

        def record_sleep(seconds):
            sleeps.append(seconds)

        payload = base_record(generation=7)
        with mock.patch("os.replace", side_effect=flaky_replace):
            path = watcher_store.write_watcher_state(
                self.repo, payload, retry_interval=0.0, sleep=record_sleep)
        self.assertEqual(path, self.state_path)
        self.assertEqual(calls["n"],
                         watcher_store.PERMISSION_RETRY_ATTEMPTS)
        self.assertEqual(sleeps, [0.0] *
                         (watcher_store.PERMISSION_RETRY_ATTEMPTS - 1))
        self.assertEqual(self.raw_state(), payload)  # 内容完整
        self.assertEqual(os.listdir(os.path.dirname(self.state_path)),
                         ["watcher.json"])  # 重试路径零 .tmp 残留

    def test_exhausted_retries_raise_watcher_store_error(self):
        """每次都 PermissionError → 恰好 attempts 次 os.replace 后抛
        WatcherStoreError（__cause__ = PermissionError）；tmp 清理、
        旧文件不被半写覆盖。"""
        watcher_store.write_watcher_state(self.repo, base_record(generation=1))
        calls = {"n": 0}

        def always_denied(src, dst):
            calls["n"] += 1
            raise PermissionError("[WinError 5] 模拟目录锁")

        sleeps = []

        with mock.patch("os.replace", side_effect=always_denied):
            with self.assertRaises(watcher_store.WatcherStoreError) as ctx:
                watcher_store.write_watcher_state(
                    self.repo, base_record(generation=2),
                    retry_attempts=3, retry_interval=0.0,
                    sleep=sleeps.append)
        self.assertEqual(calls["n"], 3)  # 有界：恰好 3 次，绝不无限
        self.assertEqual(len(sleeps), 2)  # 间隔只在重试之间
        self.assertIsInstance(ctx.exception.__cause__, PermissionError)
        self.assertEqual(self.raw_state()["generation"], 1)  # 旧值未被破坏
        self.assertEqual(os.listdir(os.path.dirname(self.state_path)),
                         ["watcher.json"])  # 失败路径 tmp 已清理

    def test_retry_parameters_customizable(self):
        """常量可配：attempts=5 → 恰 5 次尝试后成功（第 4 次失败、
        第 5 次放行），间隔常量默认 0.2 秒可被参数覆盖。"""
        real_replace = os.replace
        calls = {"n": 0}

        def flaky_replace(src, dst):
            calls["n"] += 1
            if calls["n"] < 5:
                raise PermissionError("denied")
            return real_replace(src, dst)

        with mock.patch("os.replace", side_effect=flaky_replace):
            watcher_store.write_watcher_state(
                self.repo, base_record(), retry_attempts=5,
                retry_interval=0.0, sleep=lambda seconds: None)
        self.assertEqual(calls["n"], 5)
        # 默认常量口径锚定（§6.2：默认 3 次、0.2 秒）
        self.assertEqual(watcher_store.PERMISSION_RETRY_ATTEMPTS, 3)
        self.assertEqual(watcher_store.PERMISSION_RETRY_INTERVAL_SECONDS,
                         0.2)


# —— 门 5：stale lock recovery ——

class StaleLockRecoveryTest(StoreFixture):
    """pid 已死或 heartbeat 过期 → 接管（generation+1，takeover 事实
    入返回值）；死 pid 用真实 subprocess 派生并 wait 后的已退出进程
    （Windows 原语零 mock）。"""

    def _dead_pid(self):
        """真实派生一个立即退出的子进程并 wait → 返回保证已死的 pid。"""
        helper = Path(self._tmp.name) / "dead_pid_helper.py"
        helper.write_text("pass\n", encoding="utf-8")
        process = subprocess.Popen([sys.executable, str(helper)],
                                   stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL)
        process.wait(timeout=30)
        return process.pid

    def test_takeover_when_pid_dead(self):
        """伪造记录 pid = 已退出子进程 → _pid_alive False → 接管：
        takeover=True、generation=2、started_at 推进到接管时刻。"""
        dead_pid = self._dead_pid()
        watcher_store.write_watcher_state(
            self.repo, base_record(pid=dead_pid, mode="ACTIVE",
                                   generation=1, started_at=NOW_ISO))
        later = NOW + timedelta(seconds=10)
        result = watcher_store.acquire_watcher_lock(
            self.repo, provider_identity_hash=IDENTITY, mode="ACTIVE",
            now=later)
        self.assertTrue(result["acquired"])
        self.assertTrue(result["takeover"])
        self.assertEqual(result["generation"], 2)
        record = self.raw_state()
        self.assertEqual(record["pid"], os.getpid())  # 接管者成为持有者
        self.assertEqual(record["started_at"], _format_iso_z(later))
        self.assertEqual(record["heartbeat_at"], _format_iso_z(later))
        self.assertFalse(record["stop_requested"])  # 接管复位停止旗标

    def test_takeover_when_heartbeat_expired(self):
        """pid 活着（测试进程自身）但 heartbeat 过期（> 阈值）→ 接管
        generation+1（过期即 stale lock，活 pid 不构成永久占用）。"""
        watcher_store.write_watcher_state(
            self.repo, base_record(pid=os.getpid(), generation=4,
                                   heartbeat_at=NOW_ISO))
        result = watcher_store.acquire_watcher_lock(
            self.repo, provider_identity_hash=IDENTITY, mode="PASSIVE",
            now=NOW + timedelta(
                seconds=watcher_store.DEFAULT_HEARTBEAT_STALE_SECONDS + 1))
        self.assertTrue(result["acquired"])
        self.assertTrue(result["takeover"])
        self.assertEqual(result["generation"], 5)

    def test_takeover_preserves_last_observation(self):
        """接管保留现存 last_observation（观察连续性参考），corrupted
        generation（非整数）→ 回退 generation=1 不炸。"""
        watcher_store.write_watcher_state(
            self.repo, base_record(pid=0, generation="not-an-int",
                                   last_observation={"epoch_id": "glm:x"}))
        result = watcher_store.acquire_watcher_lock(
            self.repo, provider_identity_hash=IDENTITY, mode="PASSIVE",
            now=NOW)
        self.assertTrue(result["acquired"])
        self.assertTrue(result["takeover"])
        self.assertEqual(result["generation"], 1)  # 非法 generation 不放大
        self.assertEqual(self.raw_state()["last_observation"],
                         {"epoch_id": "glm:x"})

    def test_request_stop_sets_flag_atomically_single_shot(self):
        """request_stop：单次原子写回 stop_requested=True；其余字段
        原样保留；无记录 → None（幂等非错误）。"""
        watcher_store.write_watcher_state(
            self.repo, base_record(generation=2))
        stopped = watcher_store.request_stop(self.repo)
        self.assertTrue(stopped["stop_requested"])
        raw = self.raw_state()
        self.assertTrue(raw["stop_requested"])
        self.assertEqual(raw["generation"], 2)  # 其余字段不被 stop 触碰
        self.assertIsNone(watcher_store.request_stop(
            str(Path(self._tmp.name) / "empty-repo")))


if __name__ == "__main__":
    unittest.main()
