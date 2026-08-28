#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""runtime.lease 单元测试（v2 工作块 B9.1，§78 文件租约）。

仅 Python 3 标准库（unittest + tempfile），零第三方依赖、零 git 需求；
租约文件写入 tempfile.TemporaryDirectory 提供的临时目录，不污染真实
工作区。

覆盖（§78 语义逐条锚定）：
    - 路径：lease_path 与 state.json 同目录；lease_state 文件不存在
      → {}；
    - 获取/读回：acquire → 落盘 → lease_state 读回 owner/acquired_at
      （UTC ISO 毫秒格式）；
    - 幂等重入：同 owner 重复获取不报错、不更新 acquired_at、不重复记
      （文件字节不变）；
    - 异 owner 冲突：LeaseConflictError（消息含申请方与持有者双方、
      conflicts 属性逐项精确），且零写入（文件字节不变）；
    - 全有或全无：多 path 申请其一冲突 → 全部不写入（含可得者）；
    - 释放：release 自身持有的项 / paths=None 全释放 / 他人持有的项
      不被释放（静默跳过）/ 文件不存在 → 返回 []；
    - held_by：排序列出 owner 持有的全部 path；
    - key 归一：反斜杠输入与正斜杠 key 归一为同一条目（幂等）；
    - 结构性错误：owner 非空 str 校验、非法路径（非字符串）
      OwnershipError；
    - 损坏 JSON / 顶层非对象 → ValueError（中文消息，不静默）；
    - 原子写：落盘后无 .tmp 残留；
    - 并发冒烟（轻量）：两次连续 acquire→release 往返后 state 一致。

运行：
    cd <repo_root> && python3 -m unittest tests.test_lease -v
"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "glm-conductor"))
from runtime import lease
from runtime import ownership

TID = "lease-task-1a2b3c"


class LeaseTestBase(unittest.TestCase):
    """临时目录夹具：每个用例独立的 repo_root（无 git 需求）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = self._tmp.name

    def _raw_bytes(self):
        """读取租约文件原始字节（零写入断言用）。"""
        with open(lease.lease_path(self.root, TID), "rb") as fh:
            return fh.read()


# —— 路径定位 / 空态 ——

class LeasePathTest(LeaseTestBase):

    def test_lease_path_sits_beside_state_json(self):
        expected = (Path(self.root) / ".glm-conductor" / "tasks" / TID
                    / "leases.json")
        self.assertEqual(lease.lease_path(self.root, TID), expected)

    def test_lease_state_missing_file_returns_empty_dict(self):
        self.assertEqual(lease.lease_state(self.root, TID), {})


# —— 获取 / 读回 / 幂等重入（§78 同 owner 放行） ——

class AcquireTest(LeaseTestBase):

    def test_acquire_then_read_back(self):
        result = lease.acquire_lease(self.root, TID, "unit-a",
                                     ["src/a/**", "src/a/main.ts"])
        self.assertEqual(set(result), {"src/a/**", "src/a/main.ts"})
        self.assertEqual(result["src/a/**"]["owner"], "unit-a")
        self.assertEqual(result["src/a/main.ts"]["owner"], "unit-a")
        # acquired_at：UTC ISO 毫秒（2026-08-28T12:34:56.789Z 形态）
        self.assertRegex(result["src/a/**"]["acquired_at"],
                         r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")
        # 落盘读回一致
        self.assertEqual(lease.lease_state(self.root, TID), result)

    def test_reacquire_same_owner_is_idempotent(self):
        # §78 同 owner 放行：不报错、不更新 acquired_at、不重复记
        lease.acquire_lease(self.root, TID, "unit-a", ["src/a/**"])
        raw_before = self._raw_bytes()
        original_now = lease._utc_now_iso
        lease._utc_now_iso = lambda: "2099-01-01T00:00:00.000Z"
        try:
            again = lease.acquire_lease(self.root, TID, "unit-a",
                                        ["src/a/**"])
        finally:
            lease._utc_now_iso = original_now
        # acquired_at 未被重写（时间戳标记未混入）
        self.assertNotEqual(again["src/a/**"]["acquired_at"],
                            "2099-01-01T00:00:00.000Z")
        # 幂等：文件字节不变（零写入）
        self.assertEqual(self._raw_bytes(), raw_before)
        self.assertEqual(lease.held_by(self.root, TID, "unit-a"),
                         ["src/a/**"])


# —— 异 owner 冲突 / 全有或全无 / 零写入（§78 异 owner 拒绝） ——

class ConflictTest(LeaseTestBase):

    def test_conflicting_owner_raises_with_exact_conflicts(self):
        lease.acquire_lease(self.root, TID, "unit-a", ["src/a/**"])
        raw_before = self._raw_bytes()
        with self.assertRaises(lease.LeaseConflictError) as ctx:
            lease.acquire_lease(self.root, TID, "unit-b", ["src/a/**"])
        exc = ctx.exception
        # conflicts 属性逐项精确
        self.assertEqual(exc.conflicts,
                         [{"path": "src/a/**", "owner": "unit-a"}])
        # 消息含双方（申请方与持有者）
        self.assertIn("unit-b", str(exc))
        self.assertIn("unit-a", str(exc))
        # 零写入：文件字节不变
        self.assertEqual(self._raw_bytes(), raw_before)

    def test_all_conflicts_listed_not_just_first(self):
        # 租约层冲突判定是精确 key 匹配（模式重叠判定属 dispatcher 层），
        # 两个精确 key 同时被他人持有 → 冲突清单列出全部而非只报第一个
        lease.acquire_lease(self.root, TID, "unit-a",
                            ["src/a/**", "src/shared.ts"])
        with self.assertRaises(lease.LeaseConflictError) as ctx:
            lease.acquire_lease(self.root, TID, "unit-b",
                                ["src/shared.ts", "src/a/**"])
        self.assertEqual(ctx.exception.conflicts,
                         [{"path": "src/shared.ts", "owner": "unit-a"},
                          {"path": "src/a/**", "owner": "unit-a"}])

    def test_all_or_nothing_on_partial_conflict(self):
        # 两 path 其一冲突 → 另一（可得者）也未写入
        lease.acquire_lease(self.root, TID, "unit-a", ["src/a/**"])
        with self.assertRaises(lease.LeaseConflictError):
            lease.acquire_lease(self.root, TID, "unit-b",
                                ["src/b/free.ts", "src/a/**"])
        state_now = lease.lease_state(self.root, TID)
        self.assertEqual(set(state_now), {"src/a/**"})
        self.assertEqual(state_now["src/a/**"]["owner"], "unit-a")


# —— 释放 / held_by（§78 释放时点原语） ——

class ReleaseTest(LeaseTestBase):

    def test_release_own_path(self):
        lease.acquire_lease(self.root, TID, "unit-a",
                            ["src/a/**", "src/a/main.ts"])
        released = lease.release_lease(self.root, TID, "unit-a",
                                       ["src/a/main.ts"])
        self.assertEqual(released, ["src/a/main.ts"])
        self.assertEqual(set(lease.lease_state(self.root, TID)),
                         {"src/a/**"})

    def test_release_none_releases_all_of_owner(self):
        lease.acquire_lease(self.root, TID, "unit-a",
                            ["src/z.ts", "src/a/**"])
        lease.acquire_lease(self.root, TID, "unit-b", ["src/c/**"])
        released = lease.release_lease(self.root, TID, "unit-a")
        # 返回排序、只含该 owner 的项
        self.assertEqual(released, ["src/a/**", "src/z.ts"])
        self.assertEqual(set(lease.lease_state(self.root, TID)),
                         {"src/c/**"})

    def test_release_skips_paths_held_by_others(self):
        lease.acquire_lease(self.root, TID, "unit-a", ["src/a/**"])
        lease.acquire_lease(self.root, TID, "unit-b", ["src/b/**"])
        released = lease.release_lease(self.root, TID, "unit-a",
                                       ["src/a/**", "src/b/**"])
        # 只释放自己持有的；他人持有的静默跳过（不报错、不释放）
        self.assertEqual(released, ["src/a/**"])
        self.assertEqual(set(lease.lease_state(self.root, TID)),
                         {"src/b/**"})

    def test_release_missing_file_returns_empty(self):
        self.assertEqual(lease.release_lease(self.root, TID, "unit-a"), [])
        self.assertEqual(
            lease.release_lease(self.root, TID, "unit-a", ["src/a/**"]), [])

    def test_held_by_lists_sorted_paths(self):
        lease.acquire_lease(self.root, TID, "unit-a",
                            ["src/z.ts", "src/a/**"])
        lease.acquire_lease(self.root, TID, "unit-b", ["src/other.ts"])
        self.assertEqual(lease.held_by(self.root, TID, "unit-a"),
                         ["src/a/**", "src/z.ts"])
        self.assertEqual(lease.held_by(self.root, TID, "unit-b"),
                         ["src/other.ts"])
        self.assertEqual(lease.held_by(self.root, TID, "unit-c"), [])


# —— key 归一 / 结构性错误 ——

class NormalizationTest(LeaseTestBase):

    def test_backslash_keys_normalized(self):
        # 反斜杠输入与正斜杠 key 归一为同一条目（§78 key 归一）
        lease.acquire_lease(self.root, TID, "unit-a", ["src\\a\\main.ts"])
        state_now = lease.lease_state(self.root, TID)
        self.assertEqual(set(state_now), {"src/a/main.ts"})
        # 正斜杠写法命中同一条目 → 幂等，不产生第二条
        again = lease.acquire_lease(self.root, TID, "unit-a",
                                    ["src/a/main.ts"])
        self.assertEqual(set(again), {"src/a/main.ts"})

    def test_illegal_path_raises_ownership_error(self):
        # 非字符串路径：normalize_path 的 OwnershipError 向上抛
        for bad in (42, None, 1.5):
            with self.subTest(bad=bad):
                with self.assertRaises(ownership.OwnershipError):
                    lease.acquire_lease(self.root, TID, "unit-a", [bad])


class OwnerValidationTest(LeaseTestBase):

    def test_owner_must_be_non_empty_string(self):
        for bad in ("", None, 42, b"unit-a"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    lease.acquire_lease(self.root, TID, bad, ["src/a/**"])
                with self.assertRaises(ValueError):
                    lease.release_lease(self.root, TID, bad)
                with self.assertRaises(ValueError):
                    lease.held_by(self.root, TID, bad)


# —— 损坏 JSON / 原子写 / 轻量并发冒烟 ——

class CorruptionTest(LeaseTestBase):

    def _write_raw(self, text):
        path = lease.lease_path(self.root, TID)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def test_corrupt_json_raises_value_error(self):
        self._write_raw("{not json")
        with self.assertRaises(ValueError) as ctx:
            lease.lease_state(self.root, TID)
        self.assertIn("损坏", str(ctx.exception))

    def test_non_object_top_level_raises_value_error(self):
        self._write_raw("[]")
        with self.assertRaises(ValueError):
            lease.lease_state(self.root, TID)


class AtomicWriteTest(LeaseTestBase):

    def test_no_tmp_residue_after_write(self):
        # 原子写（tmp + os.replace）：成功后无 .tmp 残留
        lease.acquire_lease(self.root, TID, "unit-a", ["src/a/**"])
        lease.release_lease(self.root, TID, "unit-a")
        task_dir = lease.lease_path(self.root, TID).parent
        leftovers = [entry.name for entry in task_dir.iterdir()
                     if entry.name.endswith(".tmp")]
        self.assertEqual(leftovers, [])

    def test_acquire_release_roundtrips_keep_state_consistent(self):
        # 轻量并发冒烟：两次连续 acquire→release 往返后 state 一致
        for round_index in range(2):
            holder = "unit-%d" % round_index
            pattern = "src/r%d/**" % round_index
            lease.acquire_lease(self.root, TID, holder, [pattern])
            self.assertEqual(lease.held_by(self.root, TID, holder),
                             [pattern])
            lease.release_lease(self.root, TID, holder)
            self.assertEqual(lease.lease_state(self.root, TID), {})


if __name__ == "__main__":
    unittest.main()
