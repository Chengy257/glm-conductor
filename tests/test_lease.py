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
    - 并发冒烟（轻量）：两次连续 acquire→release 往返后 state 一致；
    - H5（TTL/generation/崩溃恢复）：新记录 TTL 字段形状与 expires_at
      推算 / 永久边界 / 非法 ttl 拒绝 / 过期不豁免异 owner 冲突 /
      expired_leases（只含已过期、排序、now 接受 datetime 与 ISO 串、
      边界相等不过期、解析失败保守按未过期）/ generation（新获取=1、
      过期重取=2、未过期幂等不变、刷新无 ttl 转永久）/ renew_lease
      （显式 ttl、原窗宽重开、永久记录只更新心跳、他人持有跳过、
      无续约不落盘、旧格式可续不虚构 expires_at）/ 旧格式重取幂等。

运行：
    cd <repo_root> && python3 -m unittest tests.test_lease -v
"""

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "glm-conductor"))
from runtime import lease
from runtime import ownership

TID = "lease-task-1a2b3c"

T0 = "2026-01-01T00:00:00.000Z"
T1 = "2026-01-01T01:00:00.000Z"


def write_lease_map(root, mapping):
    """按落盘形态手工写入 leases.json（构造旧格式 / 过期记录用）。"""
    path = lease.lease_path(root, TID)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(mapping, ensure_ascii=False, indent=2,
                            sort_keys=True))


def read_lease_map(root):
    """直读 leases.json 的原始 JSON（断言落盘形状用）。"""
    with open(lease.lease_path(root, TID), "r", encoding="utf-8") as fh:
        return json.load(fh)


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


# —— H5：TTL 语义 / generation / 心跳续约 / 过期判定 / 向后兼容 ——

def _span_seconds(record):
    """record 的 (expires_at - acquired_at) 秒数（TTL 落盘断言用）。"""
    return (lease._parse_iso(record["expires_at"])
            - lease._parse_iso(record["acquired_at"])).total_seconds()


class TtlAcquireTest(LeaseTestBase):

    def test_new_record_carries_ttl_fields(self):
        result = lease.acquire_lease(
            self.root, TID, "unit-a", ["src/a/**"],
            session_id="session-1", ttl_seconds=60)
        record = result["src/a/**"]
        self.assertEqual(
            set(record),
            {"owner", "acquired_at", "session_id", "generation",
             "heartbeat_at", "expires_at"})
        self.assertEqual(record["owner"], "unit-a")
        self.assertEqual(record["session_id"], "session-1")
        self.assertEqual(record["generation"], 1)
        self.assertEqual(record["heartbeat_at"], record["acquired_at"])
        self.assertEqual(_span_seconds(record), 60.0)

    def test_no_ttl_is_permanent(self):
        # ttl_seconds=None → 不写 expires_at = 永久，expired_leases 永不入选
        result = lease.acquire_lease(self.root, TID, "unit-a", ["src/a/**"])
        self.assertNotIn("expires_at", result["src/a/**"])
        self.assertEqual(lease.expired_leases(self.root, TID), [])

    def test_ttl_must_be_positive_or_none(self):
        for bad in (0, -5, "60", True, False, 0.0):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    lease.acquire_lease(self.root, TID, "unit-a",
                                        ["src/a/**"], ttl_seconds=bad)
        # 非法 ttl 零副作用
        self.assertEqual(lease.lease_state(self.root, TID), {})

    def test_foreign_expired_lease_still_conflicts(self):
        # 过期不豁免冲突闸：释放归 recover_leases / 主会话，获取端保守
        lease.acquire_lease(self.root, TID, "unit-a", ["src/a/**"],
                            ttl_seconds=60)
        write_lease_map(self.root, {
            "src/a/**": {"owner": "unit-a", "acquired_at": T0,
                         "expires_at": "2020-01-01T00:00:00.000Z"}})
        with self.assertRaises(lease.LeaseConflictError):
            lease.acquire_lease(self.root, TID, "unit-b", ["src/a/**"])


class ExpiredLeasesTest(LeaseTestBase):

    def test_reports_only_expired_sorted(self):
        write_lease_map(self.root, {
            "src/z.ts": {"owner": "u9", "acquired_at": T0,
                         "expires_at": "2025-06-01T00:00:00.000Z"},
            "src/a.ts": {"owner": "u1", "acquired_at": T0,
                         "expires_at": "2026-01-01T00:00:00.000Z"},
            "src/b.ts": {"owner": "u2", "acquired_at": T0,
                         "expires_at": "2099-01-01T00:00:00.000Z"},
            "src/c.ts": {"owner": "u3", "acquired_at": T0},
        })
        expired = lease.expired_leases(self.root, TID,
                                       now="2026-06-01T00:00:00.000Z")
        # 只含已过期者、按 path 排序、字段精确（永久记录永不入选）
        self.assertEqual(expired,
                         [{"path": "src/a.ts", "owner": "u1",
                           "expires_at": "2026-01-01T00:00:00.000Z"},
                          {"path": "src/z.ts", "owner": "u9",
                           "expires_at": "2025-06-01T00:00:00.000Z"}])

    def test_now_accepts_datetime_and_iso_string(self):
        write_lease_map(self.root, {
            "src/a.ts": {"owner": "u1", "acquired_at": T0,
                         "expires_at": "2026-01-01T00:00:00.000Z"}})
        as_string = lease.expired_leases(
            self.root, TID, now="2026-01-01T00:00:01.000Z")
        as_datetime = lease.expired_leases(
            self.root, TID,
            now=datetime(2026, 1, 1, 0, 0, 1, tzinfo=timezone.utc))
        self.assertEqual(len(as_string), 1)
        self.assertEqual(as_string, as_datetime)
        # naive datetime 视为 UTC
        naive = lease.expired_leases(
            self.root, TID, now=datetime(2026, 1, 1, 0, 0, 2))
        self.assertEqual(len(naive), 1)

    def test_boundary_expires_equal_now_not_expired(self):
        # expires_at < now 严格小于：恰好相等不算过期
        write_lease_map(self.root, {
            "src/a.ts": {"owner": "u1", "acquired_at": T0,
                         "expires_at": "2026-06-01T00:00:00.000Z"}})
        self.assertEqual(
            lease.expired_leases(self.root, TID,
                                 now="2026-06-01T00:00:00.000Z"), [])

    def test_unparseable_expires_treated_permanent(self):
        # 解析失败保守按未过期（容错不抛，绝不误清）
        write_lease_map(self.root, {
            "src/a.ts": {"owner": "u1", "acquired_at": T0,
                         "expires_at": "not-a-date"}})
        self.assertEqual(lease.expired_leases(self.root, TID), [])
        # 同 owner 重取也因「未过期」幂等跳过（不触发刷新）
        raw_before = self._raw_bytes()
        lease.acquire_lease(self.root, TID, "u1", ["src/a.ts"])
        self.assertEqual(self._raw_bytes(), raw_before)


class GenerationTest(LeaseTestBase):

    def test_generation_increments_on_expired_reacquire(self):
        write_lease_map(self.root, {
            "src/a/**": {"owner": "unit-a", "acquired_at": T0,
                         "session_id": "s-old", "generation": 1,
                         "heartbeat_at": T0,
                         "expires_at": "2026-01-01T00:30:00.000Z"}})
        result = lease.acquire_lease(self.root, TID, "unit-a",
                                     ["src/a/**"],
                                     session_id="s-new", ttl_seconds=60)
        record = result["src/a/**"]
        self.assertEqual(record["generation"], 2)
        self.assertEqual(record["session_id"], "s-new")
        self.assertEqual(record["heartbeat_at"], record["acquired_at"])
        self.assertEqual(_span_seconds(record), 60.0)
        self.assertGreater(record["acquired_at"], T0)  # ISO 串比较成立
        # 落盘读回一致
        self.assertEqual(read_lease_map(self.root)[ "src/a/**"], record)

    def test_refresh_without_ttl_turns_permanent(self):
        # 刷新按本次调用语义重写 expires_at（None → 移除，转永久）
        write_lease_map(self.root, {
            "src/a/**": {"owner": "unit-a", "acquired_at": T0,
                         "session_id": "s-old", "generation": 3,
                         "heartbeat_at": T0,
                         "expires_at": "2020-01-01T00:00:00.000Z"}})
        result = lease.acquire_lease(self.root, TID, "unit-a",
                                     ["src/a/**"])
        record = result["src/a/**"]
        self.assertNotIn("expires_at", record)
        self.assertEqual(record["generation"], 4)
        self.assertEqual(record["session_id"], "s-old")  # None 保留旧值

    def test_unexpired_reacquire_is_fully_idempotent(self):
        lease.acquire_lease(self.root, TID, "unit-a", ["src/a/**"],
                            session_id="s-1", ttl_seconds=3600)
        raw_before = self._raw_bytes()
        again = lease.acquire_lease(self.root, TID, "unit-a",
                                    ["src/a/**"],
                                    session_id="s-2", ttl_seconds=1)
        # 未过期幂等跳过：不换 session、不重置 ttl、不 bump generation
        self.assertEqual(self._raw_bytes(), raw_before)
        self.assertEqual(again["src/a/**"]["generation"], 1)
        self.assertEqual(again["src/a/**"]["session_id"], "s-1")

    def test_legacy_record_reacquire_stays_idempotent(self):
        # 2.0.0 旧格式（无 expires_at）永不过期：同 owner 重取一字不动
        write_lease_map(self.root,
                        {"src/a/**": {"owner": "unit-a",
                                      "acquired_at": T0}})
        raw_before = self._raw_bytes()
        lease.acquire_lease(self.root, TID, "unit-a", ["src/a/**"])
        self.assertEqual(self._raw_bytes(), raw_before)


class RenewTest(LeaseTestBase):

    def test_renew_extends_with_explicit_ttl(self):
        lease.acquire_lease(self.root, TID, "unit-a",
                            ["src/z.ts", "src/a/**"], ttl_seconds=60)
        original_now = lease._utc_now_iso
        lease._utc_now_iso = lambda: T1
        try:
            renewed = lease.renew_lease(self.root, TID, "unit-a",
                                        ttl_seconds=120)
        finally:
            lease._utc_now_iso = original_now
        # 返回排序；heartbeat/expires 全部按续约时刻重开
        self.assertEqual(renewed, ["src/a/**", "src/z.ts"])
        state_now = lease.lease_state(self.root, TID)
        for path in ("src/a/**", "src/z.ts"):
            self.assertEqual(state_now[path]["heartbeat_at"], T1)
            self.assertEqual(
                (lease._parse_iso(state_now[path]["expires_at"])
                 - lease._parse_iso(T1)).total_seconds(), 120.0)

    def test_renew_without_ttl_reuses_original_window(self):
        lease.acquire_lease(self.root, TID, "unit-a", ["src/a/**"],
                            ttl_seconds=60)
        original_now = lease._utc_now_iso
        lease._utc_now_iso = lambda: T1
        try:
            renewed = lease.renew_lease(self.root, TID, "unit-a")
        finally:
            lease._utc_now_iso = original_now
        self.assertEqual(renewed, ["src/a/**"])
        record = lease.lease_state(self.root, TID)["src/a/**"]
        # 原有 TTL 窗宽（60s）从当前时刻重开
        self.assertEqual(
            (lease._parse_iso(record["expires_at"])
             - lease._parse_iso(T1)).total_seconds(), 60.0)

    def test_renew_permanent_record_updates_heartbeat_only(self):
        lease.acquire_lease(self.root, TID, "unit-a", ["src/a/**"])
        original_now = lease._utc_now_iso
        lease._utc_now_iso = lambda: T1
        try:
            renewed = lease.renew_lease(self.root, TID, "unit-a")
        finally:
            lease._utc_now_iso = original_now
        self.assertEqual(renewed, ["src/a/**"])
        record = lease.lease_state(self.root, TID)["src/a/**"]
        self.assertEqual(record["heartbeat_at"], T1)
        self.assertNotIn("expires_at", record)  # 无原 TTL 且未给 → 不产生

    def test_renew_skips_unheld_and_foreign_paths(self):
        lease.acquire_lease(self.root, TID, "unit-a", ["src/a/**"])
        lease.acquire_lease(self.root, TID, "unit-b", ["src/b/**"])
        foreign_before = lease.lease_state(self.root, TID)["src/b/**"]
        renewed = lease.renew_lease(self.root, TID, "unit-a",
                                    ["src/a/**", "src/b/**"])
        # 只续自己持有的；他人持有静默跳过（§78 异 owner 不可代动）
        self.assertEqual(renewed, ["src/a/**"])
        self.assertEqual(lease.lease_state(self.root, TID)["src/b/**"],
                         foreign_before)  # 他人记录一字不动

    def test_renew_nothing_held_writes_nothing(self):
        renewed = lease.renew_lease(self.root, TID, "unit-a")
        self.assertEqual(renewed, [])
        self.assertFalse(lease.lease_path(self.root, TID).exists())
        # 文件已存在但无续约 → 字节不变
        lease.acquire_lease(self.root, TID, "unit-b", ["src/b/**"])
        raw_before = self._raw_bytes()
        self.assertEqual(lease.renew_lease(self.root, TID, "unit-a"), [])
        self.assertEqual(self._raw_bytes(), raw_before)

    def test_renew_legacy_record_adds_heartbeat_without_expiry(self):
        # 旧格式可读可续：心跳写入，但不虚构 expires_at
        write_lease_map(self.root,
                        {"src/a/**": {"owner": "unit-a",
                                      "acquired_at": T0}})
        renewed = lease.renew_lease(self.root, TID, "unit-a")
        self.assertEqual(renewed, ["src/a/**"])
        record = lease.lease_state(self.root, TID)["src/a/**"]
        self.assertIn("heartbeat_at", record)
        self.assertNotIn("expires_at", record)
        self.assertEqual(record["acquired_at"], T0)  # 旧字段不动


if __name__ == "__main__":
    unittest.main()
