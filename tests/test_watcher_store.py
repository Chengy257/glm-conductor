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

v2.2.1 WU-221-A2（锁所有权与观察状态分离）→ 新增
WatcherLockOwnershipTest：互斥凭据 = .glm-conductor/quota/watcher.lock
（O_CREAT|O_EXCL 机械原子）；watcher.json 降级为纯观察面（advisory
heartbeat 证据）；门 1 / 门 5 的既有用例夹具同步落锁文件（语义正当地
由「锁即状态文件」迁移为两文件现实），其余断言原样保留。

v2.2.1 WU-221-A3（RMW 收口）→ 新增 ConcurrentStopVsHeartbeatRmwTest：
stop 旗标合并（request_stop）与心跳写回（update_watcher_state）收口
为 <watcher.json>.lock 独占锁下的读-改-写（durable_io.
atomic_update_json）——并发丢失更新回归（stop 绝不被心跳写回静默抹
掉）+ RMW 锁忙 / PermissionError 降级与既有写失败同族的锚定。

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
from runtime import durable_io
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
        self.lock_path = watcher_store.watcher_lock_path(self.repo)

    def raw_state(self):
        """绕过读 API 直读磁盘原文（断言落盘形状用）。"""
        with open(self.state_path, "r", encoding="utf-8") as handle:
            return json.load(handle)

    def raw_lock(self):
        """绕过内容约定直读 watcher.lock JSON（断言锁形状用）。"""
        with open(self.lock_path, "r", encoding="utf-8") as handle:
            return json.load(handle)

    def raw_lock_bytes(self):
        """读 watcher.lock 原始字节（零写盘断言用）。"""
        with open(self.lock_path, "rb") as handle:
            return handle.read()

    def write_lock(self, *, pid, generation=1, created_at=NOW_ISO,
                   identity=IDENTITY):
        """手写 watcher.lock 夹具（构造「活锁 / 陈旧锁」仲裁输入）。"""
        os.makedirs(os.path.dirname(self.lock_path), exist_ok=True)
        payload = {"pid": pid, "generation": generation,
                   "provider_identity_hash": identity,
                   "created_at": created_at}
        with open(self.lock_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
        return self.lock_path

    def corrupt_lock(self, text='{"pid": '):
        """手写损坏（半份 JSON）的 watcher.lock 夹具。"""
        os.makedirs(os.path.dirname(self.lock_path), exist_ok=True)
        with open(self.lock_path, "w", encoding="utf-8") as handle:
            handle.write(text)

    def backdate(self, path, seconds=3600.0):
        """把文件 mtime 回拨 seconds 秒（跨平台；损坏锁宽限闸 / 孤儿认
        领标记 TTL 用真实墙钟判定，经 os.utime 控制）。"""
        stamp = time.time() - seconds
        os.utime(path, (stamp, stamp))

    def dead_pid(self):
        """真实派生一个立即退出的子进程并 wait → 返回保证已死的 pid。"""
        helper = Path(self._tmp.name) / "dead_pid_helper.py"
        helper.write_text("pass\n", encoding="utf-8")
        process = subprocess.Popen([sys.executable, str(helper)],
                                   stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL)
        process.wait(timeout=30)
        return process.pid

    def claim_residue(self):
        """目录内现存的认领标记文件名列表（零残留断言用）。"""
        return [name for name in
                os.listdir(os.path.dirname(self.lock_path))
                if name.startswith(os.path.basename(self.lock_path)
                                   + watcher_store.LOCK_CLAIM_MARKER)]


# —— 门 1：single-instance lock（活锁拒绝第二 acquire） ——

class SingleInstanceLockTest(StoreFixture):
    """§6 冻结：一个 provider identity 同时最多一个 active watcher
    （v2.2.1 WU-221-A2 起锁本体 = watcher.lock；watcher.json 为观察面，
    本类断言的冲突形状与零改写行为保持不变）。"""

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
    （Windows 原语零 mock）。v2.2.1 WU-221-A2：锁本体是 watcher.lock，
    夹具同步落陈旧锁 + 观察记录（语义正当迁移——仲裁凭据从 watcher.json
    换为锁文件，断言本身原样保留）。"""

    def test_takeover_when_pid_dead(self):
        """陈旧锁 pid = 已退出子进程 → _pid_alive False → 接管：
        takeover=True、generation=2、started_at 推进到接管时刻；锁本体
        同步换手（generation+1、pid=接管者）。"""
        dead_pid = self.dead_pid()
        self.write_lock(pid=dead_pid, generation=1)
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
        lock = self.raw_lock()  # 锁本体同步换手（WU-221-A2）
        self.assertEqual(lock["generation"], 2)
        self.assertEqual(lock["pid"], os.getpid())
        self.assertEqual(lock["provider_identity_hash"], IDENTITY)

    def test_takeover_when_heartbeat_expired(self):
        """pid 活着（测试进程自身）但 heartbeat 过期（> 阈值）→ 接管
        generation+1（过期即 stale lock，活 pid 不构成永久占用）。"""
        self.write_lock(pid=os.getpid(), generation=4)
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
        self.assertEqual(self.raw_lock()["generation"], 5)

    def test_takeover_preserves_last_observation(self):
        """接管保留现存 last_observation（观察连续性参考）；锁 generation
        不可解析（非整数）→ 按 1 兜底再 +1 不炸（WU-221-A2：代次权威
        在锁，垃圾值不放大）。"""
        self.write_lock(pid=0, generation="not-an-int")
        watcher_store.write_watcher_state(
            self.repo, base_record(pid=0, generation="not-an-int",
                                   last_observation={"epoch_id": "glm:x"}))
        result = watcher_store.acquire_watcher_lock(
            self.repo, provider_identity_hash=IDENTITY, mode="PASSIVE",
            now=NOW)
        self.assertTrue(result["acquired"])
        self.assertTrue(result["takeover"])
        self.assertEqual(result["generation"], 2)  # 兜底 1 + 1（不放大）
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


# —— v2.2.1 WU-221-A2：watcher.lock 机械原子单实例锁 ——

class WatcherLockOwnershipTest(StoreFixture):
    """锁所有权与观察状态分离（v2.2.1 WU-221-A2）：互斥凭据 =
    .glm-conductor/quota/watcher.lock（O_CREAT|O_EXCL 机械原子创建）；
    watcher.json 降级为纯观察面。锚定：全新 acquire 落锁、新鲜锁冲突
    零写盘且有界（bounded single check）、损坏锁宽限闸两翼、created_at
    兜底窗口、释放身份纪律、孤儿认领标记、双线程认领仲裁恰一胜出。"""

    def test_fresh_acquire_creates_lock_file_with_generation_1(self):
        """全新仓库 acquire：watcher.lock 被创建，内容键恰为冻结四键，
        generation=1、pid=持有者、created_at=参考时刻（注入一致）。"""
        result = watcher_store.acquire_watcher_lock(
            self.repo, provider_identity_hash=IDENTITY, mode="PASSIVE",
            now=NOW)
        self.assertTrue(result["acquired"])
        self.assertEqual(result["lock_generation"], 1)
        self.assertTrue(os.path.isfile(self.lock_path))
        lock = self.raw_lock()
        self.assertEqual(sorted(lock),
                         ["created_at", "generation", "pid",
                          "provider_identity_hash"])
        self.assertEqual(lock["generation"], 1)
        self.assertEqual(lock["pid"], os.getpid())
        self.assertEqual(lock["provider_identity_hash"], IDENTITY)
        self.assertEqual(lock["created_at"], NOW_ISO)

    def test_second_acquire_conflict_writes_nothing_and_bounded(self):
        """新鲜锁在位：第二 acquire → 确定型冲突；锁字节与观察记录逐
        字节不变（零写盘）；冲突路径零 sleep 注入调用（bounded single
        check，无等待循环——sleep 计数桩机械锚定）。"""
        first = watcher_store.acquire_watcher_lock(
            self.repo, provider_identity_hash=IDENTITY, mode="ACTIVE",
            now=NOW)
        self.assertTrue(first["acquired"])
        lock_before = self.raw_lock_bytes()
        with open(self.state_path, "rb") as handle:
            record_before = handle.read()
        sleeps = []
        second = watcher_store.acquire_watcher_lock(
            self.repo, provider_identity_hash=IDENTITY, mode="ACTIVE",
            now=NOW + timedelta(seconds=1), sleep=sleeps.append)
        self.assertFalse(second["acquired"])
        self.assertFalse(second["takeover"])
        self.assertIsNone(second["generation"])
        self.assertEqual(second["conflict"]["pid"], os.getpid())
        self.assertEqual(second["conflict"]["created_at"], NOW_ISO)
        self.assertEqual(second["conflict"]["lock_generation"], 1)
        self.assertEqual(sleeps, [])  # 判定即返回：零等待
        self.assertEqual(self.raw_lock_bytes(), lock_before)
        with open(self.state_path, "rb") as handle:
            self.assertEqual(handle.read(), record_before)

    def test_conflict_shape_keeps_legacy_keys(self):
        """冲突形状向后兼容：既有键（pid / heartbeat_at /
        heartbeat_age_seconds / provider_identity_hash）逐一在位——
        CLI（runtime/cli.py）消费面零改动的前提。"""
        watcher_store.acquire_watcher_lock(
            self.repo, provider_identity_hash=IDENTITY, mode="PASSIVE",
            now=NOW)
        second = watcher_store.acquire_watcher_lock(
            self.repo, provider_identity_hash=IDENTITY, mode="PASSIVE",
            now=NOW + timedelta(seconds=2))
        for key in ("pid", "heartbeat_at", "heartbeat_age_seconds",
                    "provider_identity_hash"):
            self.assertIn(key, second["conflict"])
        self.assertEqual(second["conflict"]["heartbeat_at"], NOW_ISO)

    def test_corrupt_lock_within_grace_conflicts_without_writes(self):
        """锁内容损坏但 mtime 龄期未超宽限（持有者写一半的微秒级窗口）
        → 视同持有中：确定型冲突（证据不可得 → None 不虚构），零写盘
        （观察记录不落、锁字节不动）。"""
        self.corrupt_lock()
        lock_before = self.raw_lock_bytes()
        result = watcher_store.acquire_watcher_lock(
            self.repo, provider_identity_hash=IDENTITY, mode="PASSIVE",
            now=NOW)
        self.assertFalse(result["acquired"])
        self.assertIsNone(result["conflict"]["pid"])
        self.assertFalse(os.path.exists(self.state_path))  # 零写盘
        self.assertEqual(self.raw_lock_bytes(), lock_before)

    def test_corrupt_lock_beyond_grace_takeover(self):
        """锁内容损坏且 mtime 龄期超宽限（崩溃遗留）→ 接管：generation
        不可解析按 1 兜底 +1 = 2，锁内容重写为合法 JSON、pid=接管者。"""
        self.corrupt_lock()
        self.backdate(self.lock_path, seconds=3600.0)
        result = watcher_store.acquire_watcher_lock(
            self.repo, provider_identity_hash=IDENTITY, mode="PASSIVE",
            now=NOW)
        self.assertTrue(result["acquired"])
        self.assertTrue(result["takeover"])
        self.assertEqual(result["generation"], 2)
        self.assertEqual(self.raw_lock()["generation"], 2)
        self.assertEqual(self.raw_lock()["pid"], os.getpid())
        self.assertEqual(self.claim_residue(), [])  # 自己的认领标记已清

    def test_heartbeat_evidence_unavailable_falls_back_to_created_at(self):
        """heartbeat 证据不可得（无观察记录）→ 锁 created_at 兜底：
        距今 ≤ lock_stale_seconds（默认 300）→ 冲突（覆盖「锁刚创建、
        观察记录尚未落盘」的启动窗口不被误接管）；超龄 → 接管。"""
        self.write_lock(pid=os.getpid(), generation=1)
        early = watcher_store.acquire_watcher_lock(
            self.repo, provider_identity_hash=IDENTITY, mode="PASSIVE",
            now=NOW + timedelta(seconds=1))
        self.assertFalse(early["acquired"])
        self.assertIsNone(early["conflict"]["heartbeat_at"])  # 证据缺失不虚构
        self.assertEqual(early["conflict"]["pid"], os.getpid())
        late = watcher_store.acquire_watcher_lock(
            self.repo, provider_identity_hash=IDENTITY, mode="PASSIVE",
            now=NOW + timedelta(seconds=301))
        self.assertTrue(late["acquired"])
        self.assertTrue(late["takeover"])
        self.assertEqual(late["generation"], 2)

    def test_release_identity_discipline(self):
        """释放身份纪律：仅当锁内容仍是 (pid, generation) 自己才删；
        generation 或 pid 不匹配（后继已接管 / 他者锁）→ 保留并返回
        False——绝不误删后继接管者的锁。"""
        self.write_lock(pid=os.getpid(), generation=1)
        self.assertFalse(watcher_store.release_watcher_lock(
            self.repo, pid=os.getpid(), generation=2))  # 代次不符不删
        self.assertTrue(os.path.isfile(self.lock_path))
        self.assertTrue(watcher_store.release_watcher_lock(
            self.repo, pid=os.getpid(), generation=1))  # 身份相符才删
        self.assertFalse(os.path.exists(self.lock_path))
        # 后继锁（generation 2）不被前任 (pid, generation 1) 删除
        self.write_lock(pid=os.getpid(), generation=2)
        self.assertFalse(watcher_store.release_watcher_lock(
            self.repo, pid=os.getpid(), generation=1))
        self.assertTrue(os.path.isfile(self.lock_path))
        # 锁内容 pid 不符（他者持有）→ 不删
        self.assertFalse(watcher_store.release_watcher_lock(
            self.repo, pid=0, generation=2))
        self.assertTrue(os.path.isfile(self.lock_path))

    def test_orphan_claim_marker_ignored_not_deleted(self):
        """孤儿认领标记（龄期超 TTL）：只忽略、绝不代删——接管照常
        进行，事后目录里恰好只剩孤儿标记（自己的认领标记已清理）。
        孤儿名取全零 hex（字典序最小）：若被当作存活会冤枉赢仲裁，
        本用例恰证明 TTL 闸生效。"""
        dead = self.dead_pid()
        self.write_lock(pid=dead, generation=1)
        orphan = self.lock_path + watcher_store.LOCK_CLAIM_MARKER + "0000"
        with open(orphan, "w", encoding="utf-8") as handle:
            handle.write("{}")
        self.backdate(orphan, seconds=3600.0)  # 超 LOCK_CLAIM_TTL_SECONDS
        result = watcher_store.acquire_watcher_lock(
            self.repo, provider_identity_hash=IDENTITY, mode="PASSIVE",
            now=NOW)
        self.assertTrue(result["acquired"])
        self.assertTrue(result["takeover"])
        self.assertEqual(self.claim_residue(),
                         [os.path.basename(orphan)])  # 孤儿保留、自己的已清

    def test_claim_race_two_threads_exactly_one_winner(self):
        """两线程同抢同一陈旧锁（死 pid，真实 subprocess）：claim-marker
        仲裁恰一方接管成功（generation=2），另一方确定型冲突；零认领
        标记残留（各自 finally 清理自己的标记）。settle / 重试全部注入
        零等待。"""
        dead = self.dead_pid()
        self.write_lock(pid=dead, generation=1)
        watcher_store.write_watcher_state(self.repo,
                                          base_record(pid=dead))
        results = []
        barrier = threading.Barrier(2)

        def contender():
            barrier.wait()
            results.append(watcher_store.acquire_watcher_lock(
                self.repo, provider_identity_hash=IDENTITY, mode="PASSIVE",
                now=NOW, settle_poll_seconds=0.0, sleep=lambda _s: None))

        threads = [threading.Thread(target=contender) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)
        self.assertEqual(len(results), 2)
        winners = [r for r in results if r["acquired"]]
        losers = [r for r in results if not r["acquired"]]
        self.assertEqual(len(winners), 1)  # 恰一胜出：机械互斥成立
        self.assertEqual(len(losers), 1)
        self.assertTrue(winners[0]["takeover"])
        self.assertEqual(winners[0]["generation"], 2)
        self.assertFalse(losers[0]["takeover"])
        self.assertIsNone(losers[0]["generation"])
        self.assertEqual(self.raw_lock()["generation"], 2)
        self.assertEqual(self.raw_lock()["pid"], os.getpid())
        self.assertEqual(self.claim_residue(), [])  # 零认领标记残留

    def test_acquire_parameter_validation_covers_new_thresholds(self):
        """新增阈值参数校验先于 I/O：lock_stale_seconds /
        claim_ttl_seconds / settle_poll_seconds 非数值或负 → ValueError
        且零落盘（无锁、无观察记录）。"""
        for kwargs in (
                {"lock_stale_seconds": -1},
                {"lock_stale_seconds": "300"},
                {"claim_ttl_seconds": -0.5},
                {"settle_poll_seconds": -1}):
            with self.assertRaises(ValueError):
                watcher_store.acquire_watcher_lock(
                    self.repo, provider_identity_hash=IDENTITY,
                    mode="PASSIVE", now=NOW, **kwargs)
        self.assertFalse(os.path.exists(self.lock_path))
        self.assertFalse(os.path.exists(self.state_path))


# —— v2.2.1 WU-221-A3：stop 旗标 / 心跳写回的独占锁 RMW ——

def _hb_value(thread_index, iteration):
    """心跳线程 (thread_index, iteration) 的确定性 heartbeat_at 值
    （ISO 形态、两两互异——「最终值必属写过值集合」断言的值域）。"""
    return "2026-09-02T12:%02d:%02d.%dZ" % (
        10 + iteration // 60, iteration % 60, thread_index)


class ConcurrentStopVsHeartbeatRmwTest(StoreFixture):
    """WU-221-A3 收口回归（验收主轴）：并发 stop 旗标合并 vs 心跳写回。

    旧实现（无锁 RMW：读 → 内存改 → 整体写回）下，心跳写回若以陈旧
    读取整体覆盖写回，stop 旗标即被静默抹掉（丢更新——CLI stop 失效，
    watcher 永不退出）。现两条路径同处 <watcher.json>.lock 独占锁下的
    读-改-写（durable_io.atomic_update_json）：谁后写都建立在对方落盘
    结果之上。多线程（进程内线程；多进程归 a5）交错 M 轮后终态必须：
    stop_requested 闩锁为 True（绝不静默丢失）、heartbeat_at 恰为心跳
    方写过的某个值（心跳写回同样不丢）、文件全程合法 JSON 且冻结字段
    齐备、RMW 锁/临时文件零残留。"""

    STOPPERS = 2
    BEATERS = 2
    ROUNDS = 20

    def test_concurrent_stop_and_heartbeat_merges_never_lose_update(self):
        """N 线程交错 M 轮 stop 合并 vs 心跳合并：stop 旗标不丢、
        heartbeat_at ∈ 写过值集合、文件全程合法、零锁残留。"""
        watcher_store.write_watcher_state(
            self.repo, base_record(generation=2))
        barrier = threading.Barrier(self.STOPPERS + self.BEATERS + 1)
        errors = []

        def stopper():
            barrier.wait()
            for _round in range(self.ROUNDS):
                try:
                    watcher_store.request_stop(self.repo)
                except Exception as exc:  # noqa: BLE001 —— 捕获即 FAIL 证据
                    errors.append(exc)
                    return

        def beater(thread_index):
            barrier.wait()
            for iteration in range(self.ROUNDS):
                try:
                    watcher_store.update_watcher_state(
                        self.repo,
                        lambda payload, value=_hb_value(thread_index,
                                                        iteration):
                        {**payload, "heartbeat_at": value})
                except Exception as exc:  # noqa: BLE001
                    errors.append(exc)
                    return

        def observer():
            # 全程并发读者：任何一次成功读入都必须是冻结字段齐备的
            # 完整记录（撕裂 / 半份 JSON 在 json.load 即炸）
            barrier.wait()
            for _round in range(self.ROUNDS * 4):
                try:
                    record = watcher_store.read_watcher_state(self.repo)
                    if record is None:
                        continue  # 极端瞬态（读重试耗尽）不算撕裂
                    for field in watcher_store.WATCHER_STATE_FIELDS:
                        if field not in record:
                            raise AssertionError("缺冻结字段 %s" % field)
                except Exception as exc:  # noqa: BLE001
                    errors.append(exc)
                    return
                time.sleep(0.001)

        threads = ([threading.Thread(target=stopper)
                    for _ in range(self.STOPPERS)]
                   + [threading.Thread(target=beater, args=(index,))
                      for index in range(self.BEATERS)]
                   + [threading.Thread(target=observer)])
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=120)
        self.assertEqual(errors, [])
        final = self.raw_state()  # json.load 即「文件合法 JSON」断言
        self.assertTrue(final["stop_requested"])  # 修复前：可被心跳写回抹掉
        written = {_hb_value(t, i)
                   for t in range(self.BEATERS)
                   for i in range(self.ROUNDS)}
        self.assertIn(final["heartbeat_at"], written)  # 心跳不丢、不被编造
        self.assertEqual(final["generation"], 2)  # RMW 两方都不触碰代次
        self.assertEqual(sorted(final), sorted(base_record()))
        # RMW 锁（<watcher.json>.lock）与临时文件零残留
        self.assertEqual(os.listdir(os.path.dirname(self.state_path)),
                         ["watcher.json"])

    def test_rmw_lock_busy_degrades_to_watcher_store_error(self):
        """RMW 锁忙等超时（LockBusyError）→ 折算 WatcherStoreError
        （__cause__ = LockBusyError）——与 write_watcher_state 的写失败
        降级同族同向，绝不静默吞。夹具：手写新鲜 RMW 锁（pid=本进程
        存活、created_at=当前）→ 有界忙等耗尽。"""
        watcher_store.write_watcher_state(
            self.repo, base_record(generation=2))
        lock_path = self.state_path + ".lock"
        with open(lock_path, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(
                {"pid": os.getpid(), "generation": 1,
                 "created_at": _format_iso_z(datetime.now(timezone.utc)),
                 "owner": "fixture"}, sort_keys=True))
        with self.assertRaises(watcher_store.WatcherStoreError) as ctx:
            watcher_store.update_watcher_state(
                self.repo, lambda payload: payload,
                timeout_seconds=0.05, poll_interval=0.0,
                sleep=lambda _seconds: None)
        self.assertIsInstance(ctx.exception.__cause__,
                              durable_io.LockBusyError)
        self.assertEqual(self.raw_state()["generation"], 2)  # 目标未动

    def test_request_stop_permission_error_degrades_to_watcher_store_error(
            self):
        """request_stop 写侧 PermissionError 有界重试耗尽 →
        WatcherStoreError（__cause__ = PermissionError）——与迁移前
        write_watcher_state 的降级逐形一致（约束：LockBusyError /
        PermissionError 降级绝不偏离既有写失败行为）。"""
        watcher_store.write_watcher_state(
            self.repo, base_record(generation=2))
        calls = {"n": 0}

        def always_denied(src, dst):
            calls["n"] += 1
            raise PermissionError("[WinError 5] 模拟目录锁")

        with mock.patch("os.replace", side_effect=always_denied):
            with self.assertRaises(watcher_store.WatcherStoreError) as ctx:
                watcher_store.request_stop(self.repo, retry_attempts=3,
                                           retry_interval=0.0,
                                           sleep=lambda _s: None)
        self.assertEqual(calls["n"], 3)  # 有界：恰好 3 次
        self.assertIsInstance(ctx.exception.__cause__, PermissionError)
        self.assertEqual(self.raw_state()["generation"], 2)  # 旧值未破坏
        self.assertEqual(os.listdir(os.path.dirname(self.state_path)),
                         ["watcher.json"])  # RMW 锁与 tmp 零残留


if __name__ == "__main__":
    unittest.main()
