#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""runtime.writer_guard 单元测试（v2.4 Phase 1，单元 U5 / 工作包 P1-E）。

锚定对象：仓库级写预约不变式——一个 Git 仓库至多一个活跃 Conductor 写
Workflow；恢复只经终态或显式释放。规格红线：无 TTL / generation /
heartbeat / 任何按时间过期的逻辑——手工写入超陈旧 created_at（甚至不可
解析）的残留记录后 acquire 仍冲突，以此机械锚定「绝不自动过期」。
测试类映射：

    FirstAcquireTest          首次 acquire 成功（ok/conflict 形态 + 记录
                              恰四字段落盘 + repo_identity 为绝对仓库根）
    ConflictReportTest        异 task 二次 acquire 报完整冲突信息
                              （task_id / workflow_run_id / created_at），
                              记录零改动
    SameTaskIdempotentTest    同 task 重复 acquire 幂等成功；非 None run
                              id 更新记录；None 保留原值；created_at 不变
    OwnerReleaseTest          owner 释放成功且幂等；释放后异 task 可获取
    NonOwnerReleaseTest       非 owner 释放被拒且报当前持有者，记录零改动
    InspectStatesTest         inspect 空态 None / 占用态四字段 / 释放后 None
    ResidualRecordTest        模拟崩溃残留（手工写记录文件再 acquire）：
                              仍冲突、绝不自动过期（陈旧 / 不可解析
                              created_at、字段残缺残留一概照常冲突）
    CorruptRecordTest         记录损坏 → fail-open 视同无预约（不可读容
                              错，非按时间过期；规格红线不受影响）
    ConcurrencyTest           并发 acquire 互斥（独占锁下恰一方持有）

全部离线：文件操作均在 tempfile.TemporaryDirectory 内（绝不触碰仓库内
.glm-conductor/ 真实账本）；OS / 文件原语零 mock——并发用真实 threading；
时间仅以字符串写入残留记录（守卫判定本就不读时间）。

运行：
    cd <repo_root> && python3 -X utf8 -m unittest tests.test_writer_guard -v
"""

import json
import os
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "glm-conductor"))
from runtime import durable_io, writer_guard


def iso_seconds_ago(seconds):
    """距此刻 seconds 秒前的 ISO8601 UTC Z 串（构造陈旧残留记录用）。"""
    moment = datetime.now(timezone.utc) - timedelta(seconds=seconds)
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_iso_utc(value):
    """ISO8601 Z 串 → aware datetime（3.7 fromisoformat 不认 Z 后缀）。"""
    probe = value[:-1] + "+00:00" if value.endswith(("Z", "z")) else value
    moment = datetime.fromisoformat(probe)
    assert moment.tzinfo is not None
    return moment


def read_raw(path):
    """读记录文件原始字节（零改动断言 / 归零形态断言用）。"""
    with open(path, "rb") as handle:
        return handle.read()


class WriterGuardFixture(unittest.TestCase):
    """公共夹具：临时目录即仓库根，全程不触碰真实工作区。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo_root = self._tmp.name
        self.record_path = writer_guard.record_path(self.repo_root)
        self.identity = os.path.abspath(self.repo_root)

    def write_residue(self, residue):
        """手工写残留记录文件（模拟持有者进程崩溃后遗留的预约）。"""
        durable_io.atomic_write_json(self.record_path, residue)


class FirstAcquireTest(WriterGuardFixture):
    """首次 acquire 成功：结果形态与持久记录四字段。"""

    def test_first_acquire_ok(self):
        result = writer_guard.acquire(self.repo_root, "task-a", "run-1")
        self.assertEqual(result, {"ok": True, "conflict": None})

    def test_first_acquire_record_four_fields(self):
        writer_guard.acquire(self.repo_root, "task-a", "run-1")
        with open(self.record_path, "r", encoding="utf-8") as handle:
            record = json.load(handle)
        self.assertEqual(
            sorted(record.keys()), sorted(writer_guard.RECORD_FIELDS))
        self.assertEqual(record["repo_identity"], self.identity)
        self.assertEqual(record["task_id"], "task-a")
        self.assertEqual(record["workflow_run_id"], "run-1")
        # created_at 是 ISO8601 UTC Z 串且指向现在（纯诊断，不做任何判定）
        self.assertTrue(record["created_at"].endswith("Z"))
        self.assertLess(
            abs((datetime.now(timezone.utc)
                 - parse_iso_utc(record["created_at"])).total_seconds()), 60)

    def test_first_acquire_without_run_id(self):
        result = writer_guard.acquire(self.repo_root, "task-a")
        self.assertEqual(result, {"ok": True, "conflict": None})
        record = writer_guard.inspect(self.repo_root)
        self.assertIsNone(record["workflow_run_id"])

    def test_invalid_inputs_rejected(self):
        with self.assertRaises(writer_guard.WriterGuardError):
            writer_guard.acquire(self.repo_root, "")
        with self.assertRaises(writer_guard.WriterGuardError):
            writer_guard.acquire(self.repo_root, None)
        with self.assertRaises(writer_guard.WriterGuardError):
            writer_guard.acquire(self.repo_root, "task-a", "")
        with self.assertRaises(writer_guard.WriterGuardError):
            writer_guard.release(self.repo_root, 42)


class ConflictReportTest(WriterGuardFixture):
    """异 task 二次 acquire：报完整冲突信息且记录零改动。"""

    def test_conflict_reports_holder_ids(self):
        writer_guard.acquire(self.repo_root, "task-a", "run-1")
        created_at = writer_guard.inspect(self.repo_root)["created_at"]
        result = writer_guard.acquire(self.repo_root, "task-b", "run-2")
        self.assertFalse(result["ok"])
        self.assertEqual(
            result["conflict"],
            {"task_id": "task-a", "workflow_run_id": "run-1",
             "created_at": created_at})
        self.assertEqual(
            sorted(result["conflict"].keys()),
            sorted(writer_guard.CONFLICT_FIELDS))

    def test_conflict_zero_write(self):
        writer_guard.acquire(self.repo_root, "task-a", "run-1")
        before = read_raw(self.record_path)
        writer_guard.acquire(self.repo_root, "task-b")
        self.assertEqual(read_raw(self.record_path), before)
        self.assertEqual(
            writer_guard.inspect(self.repo_root)["task_id"], "task-a")

    def test_conflict_with_default_run_id(self):
        writer_guard.acquire(self.repo_root, "task-a")
        result = writer_guard.acquire(self.repo_root, "task-b")
        self.assertEqual(
            result,
            {"ok": False,
             "conflict": {"task_id": "task-a", "workflow_run_id": None,
                          "created_at": writer_guard.inspect(
                              self.repo_root)["created_at"]}})


class SameTaskIdempotentTest(WriterGuardFixture):
    """同 task 重复 acquire：幂等成功；run id 更新 / 保留语义。"""

    def test_same_task_reacquire_ok(self):
        writer_guard.acquire(self.repo_root, "task-a", "run-1")
        result = writer_guard.acquire(self.repo_root, "task-a", "run-1")
        self.assertEqual(result, {"ok": True, "conflict": None})
        record = writer_guard.inspect(self.repo_root)
        self.assertEqual(record["task_id"], "task-a")
        self.assertEqual(record["workflow_run_id"], "run-1")

    def test_same_task_updates_run_id(self):
        writer_guard.acquire(self.repo_root, "task-a", "run-1")
        created_at = writer_guard.inspect(self.repo_root)["created_at"]
        result = writer_guard.acquire(self.repo_root, "task-a", "run-2")
        self.assertEqual(result, {"ok": True, "conflict": None})
        record = writer_guard.inspect(self.repo_root)
        self.assertEqual(record["workflow_run_id"], "run-2")
        # created_at 保持预约创建时刻，重入不改写
        self.assertEqual(record["created_at"], created_at)

    def test_same_task_none_preserves_run_id(self):
        writer_guard.acquire(self.repo_root, "task-a", "run-1")
        result = writer_guard.acquire(self.repo_root, "task-a")
        self.assertEqual(result, {"ok": True, "conflict": None})
        record = writer_guard.inspect(self.repo_root)
        self.assertEqual(record["workflow_run_id"], "run-1")


class OwnerReleaseTest(WriterGuardFixture):
    """owner 释放：成功、幂等、释放后可被异 task 获取。"""

    def test_owner_release_ok(self):
        writer_guard.acquire(self.repo_root, "task-a", "run-1")
        result = writer_guard.release(self.repo_root, "task-a")
        self.assertEqual(
            result, {"ok": True, "released": True, "conflict": None})
        self.assertIsNone(writer_guard.inspect(self.repo_root))

    def test_owner_release_idempotent(self):
        writer_guard.acquire(self.repo_root, "task-a")
        self.assertEqual(
            writer_guard.release(self.repo_root, "task-a"),
            {"ok": True, "released": True, "conflict": None})
        self.assertEqual(
            writer_guard.release(self.repo_root, "task-a"),
            {"ok": True, "released": False, "conflict": None})
        self.assertIsNone(writer_guard.inspect(self.repo_root))

    def test_release_keyed_on_task_not_run_id(self):
        # 释放权威键是 task_id：run id 不匹配也照常释放（诊断信息不参与比对）
        writer_guard.acquire(self.repo_root, "task-a", "run-1")
        result = writer_guard.release(self.repo_root, "task-a", "other-run")
        self.assertEqual(
            result, {"ok": True, "released": True, "conflict": None})

    def test_other_task_can_acquire_after_release(self):
        writer_guard.acquire(self.repo_root, "task-a")
        writer_guard.release(self.repo_root, "task-a")
        self.assertEqual(
            writer_guard.acquire(self.repo_root, "task-b", "run-9"),
            {"ok": True, "conflict": None})


class NonOwnerReleaseTest(WriterGuardFixture):
    """非 owner 释放：拒绝、报当前持有者、记录零改动。"""

    def test_non_owner_release_refused(self):
        writer_guard.acquire(self.repo_root, "task-a", "run-1")
        created_at = writer_guard.inspect(self.repo_root)["created_at"]
        result = writer_guard.release(self.repo_root, "task-b")
        self.assertEqual(
            result,
            {"ok": False, "released": False,
             "conflict": {"task_id": "task-a", "workflow_run_id": "run-1",
                          "created_at": created_at}})

    def test_non_owner_release_zero_write(self):
        writer_guard.acquire(self.repo_root, "task-a", "run-1")
        before = read_raw(self.record_path)
        writer_guard.release(self.repo_root, "task-b")
        self.assertEqual(read_raw(self.record_path), before)
        self.assertEqual(
            writer_guard.inspect(self.repo_root)["task_id"], "task-a")


class InspectStatesTest(WriterGuardFixture):
    """inspect 两态：空 None / 占用四字段；释放后回到 None。"""

    def test_inspect_empty(self):
        self.assertIsNone(writer_guard.inspect(self.repo_root))

    def test_inspect_occupied(self):
        writer_guard.acquire(self.repo_root, "task-a", "run-1")
        snapshot = writer_guard.inspect(self.repo_root)
        self.assertEqual(
            sorted(snapshot.keys()), sorted(writer_guard.RECORD_FIELDS))
        self.assertEqual(snapshot["repo_identity"], self.identity)
        self.assertEqual(snapshot["task_id"], "task-a")
        self.assertEqual(snapshot["workflow_run_id"], "run-1")
        self.assertTrue(snapshot["created_at"].endswith("Z"))

    def test_inspect_after_release(self):
        writer_guard.acquire(self.repo_root, "task-a")
        writer_guard.release(self.repo_root, "task-a")
        self.assertIsNone(writer_guard.inspect(self.repo_root))

    def test_inspect_returns_detached_copy(self):
        writer_guard.acquire(self.repo_root, "task-a")
        snapshot = writer_guard.inspect(self.repo_root)
        snapshot["task_id"] = "tampered"
        self.assertEqual(
            writer_guard.inspect(self.repo_root)["task_id"], "task-a")


class ResidualRecordTest(WriterGuardFixture):
    """模拟崩溃残留：手工写记录文件后再 acquire 仍冲突、绝不自动过期。"""

    def test_stale_residue_still_conflicts(self):
        # 10 天前的残留：任何按时间过期的设计都会放行——规格红线锚定点
        stale_created_at = iso_seconds_ago(10 * 86400)
        self.write_residue({
            "repo_identity": self.identity,
            "task_id": "task-dead",
            "workflow_run_id": "run-old",
            "created_at": stale_created_at,
        })
        result = writer_guard.acquire(self.repo_root, "task-new", "run-new")
        self.assertEqual(
            result,
            {"ok": False,
             "conflict": {"task_id": "task-dead",
                          "workflow_run_id": "run-old",
                          "created_at": stale_created_at}})
        self.assertEqual(
            writer_guard.inspect(self.repo_root)["task_id"], "task-dead")

    def test_unparseable_created_at_still_conflicts(self):
        # created_at 不可解析也绝不放过：守卫判定根本不读时间
        self.write_residue({
            "repo_identity": self.identity,
            "task_id": "task-dead",
            "workflow_run_id": "run-old",
            "created_at": "not-a-timestamp",
        })
        result = writer_guard.acquire(self.repo_root, "task-new")
        self.assertEqual(
            result["conflict"]["created_at"], "not-a-timestamp")
        self.assertFalse(result["ok"])

    def test_minimal_residue_still_conflicts(self):
        # 字段残缺的手写残留（仅 task_id）：照常冲突，缺失字段以 None 补位
        self.write_residue({"task_id": "task-x"})
        result = writer_guard.acquire(self.repo_root, "task-new")
        self.assertEqual(
            result,
            {"ok": False,
             "conflict": {"task_id": "task-x", "workflow_run_id": None,
                          "created_at": None}})

    def test_residue_release_paths(self):
        # 非 owner 释放被拒；显式释放只属于持有者 task 本身
        self.write_residue({
            "repo_identity": self.identity, "task_id": "task-dead",
            "workflow_run_id": "run-old", "created_at": "not-a-timestamp"})
        refused = writer_guard.release(self.repo_root, "task-new")
        self.assertFalse(refused["ok"])
        self.assertEqual(refused["conflict"]["task_id"], "task-dead")
        self.assertEqual(
            writer_guard.release(self.repo_root, "task-dead"),
            {"ok": True, "released": True, "conflict": None})
        self.assertIsNone(writer_guard.inspect(self.repo_root))


class CorruptRecordTest(WriterGuardFixture):
    """记录损坏：fail-open 视同无预约（不可读容错，非按时间过期）。"""

    def test_corrupt_record_treated_as_absent(self):
        os.makedirs(os.path.dirname(self.record_path), exist_ok=True)
        with open(self.record_path, "wb") as handle:
            handle.write(b"this is not json")
        self.assertIsNone(writer_guard.inspect(self.repo_root))
        result = writer_guard.acquire(self.repo_root, "task-c", "run-3")
        self.assertEqual(result, {"ok": True, "conflict": None})
        self.assertEqual(
            writer_guard.inspect(self.repo_root)["task_id"], "task-c")


class ConcurrencyTest(WriterGuardFixture):
    """并发 acquire 互斥：独占锁保护下恰一方持有（不变式并发成立）。"""

    def test_concurrent_acquire_mutual_exclusion(self):
        rounds = 8
        tasks = ("task-a", "task-b")
        ok_counts = {task: 0 for task in tasks}
        errors = []
        start = threading.Barrier(len(tasks))

        def worker(task):
            start.wait()
            for _ in range(rounds):
                try:
                    result = writer_guard.acquire(self.repo_root, task)
                    if result["ok"]:
                        # 持有窗口内 inspect 只能看见自己（另一方必然冲突）
                        holder = writer_guard.inspect(self.repo_root)
                        if holder["task_id"] != task:
                            errors.append(
                                "%s 持有窗口内看到 %r" % (task, holder))
                        ok_counts[task] += 1
                        writer_guard.release(self.repo_root, task)
                except Exception as exc:  # pragma: no cover - 失败上报用
                    errors.append("%s 异常: %r" % (task, exc))

        threads = [
            threading.Thread(target=worker, args=(task,)) for task in tasks]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        # 每轮至少一方成功（记录非空时恰一方成功；归零窗口可双双成功）
        self.assertGreaterEqual(
            ok_counts["task-a"] + ok_counts["task-b"], rounds)
        self.assertEqual(writer_guard.inspect(self.repo_root), None)


if __name__ == "__main__":
    unittest.main()
