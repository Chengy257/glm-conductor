#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""dispatch wave 批量事务测试（v2.1 M4，wu-21-08）。

仅 Python 3 标准库（unittest + tempfile + subprocess + unittest.mock
故障注入），零第三方依赖；state.json / leases.json / events.jsonl /
permits/ 全部落盘在 tempfile.TemporaryDirectory 提供的临时目录（真实
runtime 构造与真实 I/O，非 mock state），绝不触碰 .glm-conductor/
真实账本。租约中途冲突用 mock 包装 acquire_lease 定向注入
LeaseConflictError（plan 租约闸在单写者前提下拦截不了 prepare 与
acquire 之间的竞态，批量事务的安全降级路径只能故障注入触达）。

覆盖（规格测试清单 10 项 + agent_launched wave_id 接线）：
    1. 双单元 happy path：wave 记录 active、双 permit 带 wave_id、双
       租约持有、journal 单条 dispatch_wave_prepared（无逐单元
       dispatch_prepared）、批准集按 state.work_units 原序（夹具故意
       逆 id 序）、worker_budget = min(max_workers, len(approved))；
    2. 租约中途冲突 → 安全降级：冲突单元剔除、本轮已获取租约释放、
       wave 只含成功成员、无冲突单元租约残留；
    3. 全部冲突 / 批准集空 → TaskManagerError（零租约、零 wave 记录、
       零事件、state.json 字节不变）；
    4. ownership 冲突单元进 deferred（plan_dispatch 既有行为透传）；
    5. quota EXHAUSTED → TaskManagerError waiting_quota 口径；
    6. validate_wave_membership：active 成员 ok / closed 拒 / 未知
       wave_id 拒 / wave_id None 恒 ok / state 缺失拒 / 成员不符拒；
       new_wave_id 形状与唯一性；
    7. pre_tool_use hook：wave permit + active wave → allow；closed
       wave → deny（报文含 wave_id 与 re-prepare 指引）；
    8. finish_unit 末成员终态 → wave closed + wave_closed 事件；单成员
       wave 立即关；无 waves 键零行为；
    9. state._validate_dispatch：waves 形状错误中文报错（非 list /
       缺键 / wave_id 重复 / status 非法 / closed_at 与 status 矛盾 /
       worker_budget / quota_status / units）+ 合法 wave 落盘读回；
    10. CLI wave-prepare/wave-show：0/2/1 退出码、JSON 输出含 markers。

运行：
    cd <repo_root> && python3 -m unittest tests.test_wave_dispatch -v
"""

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "glm-conductor"))
from runtime import agent_run, cli, dispatch_wave, journal, lease, state
from runtime import task_manager, work_unit

TID = "wave-task-1a2b3c"
VERIFY_CMD = "python3 -m unittest tests.test_wave_dispatch"
# delegate 路由（矩阵合法：delegability high + assurance standard）
ROUTE = {"mode": "delegate", "delegability": "high",
         "assurance": "standard", "executor": "flash-implementer",
         "continuity": "foreground"}

PRE_TOOL_USE = (Path(__file__).resolve().parents[1]
                / "plugins/glm-conductor/hooks/pre_tool_use.py")
POST_TOOL_USE = (Path(__file__).resolve().parents[1]
                 / "plugins/glm-conductor/hooks/post_tool_use.py")
CLI_PATH = (Path(__file__).resolve().parents[1] / "plugins" /
            "glm-conductor" / "runtime" / "cli.py")


def wu(uid, owned, status="ready"):
    """构造 §61 形状的 ready 单元（真实 new_work_unit）。"""
    return work_unit.new_work_unit(
        uid, "wave 目标 %s" % uid, executor="flash-implementer",
        ownership=list(owned), verification=[VERIFY_CMD], status=status)


def make_task(root, units, *, max_workers=2, active=(), status="executing",
              task_id=TID):
    """在临时目录真实落盘一个带 work units 的任务 state。"""
    st = state.new_task_state(
        task_id, "wave 批量事务测试目标", dict(ROUTE),
        ownership_files=("src/**",),
        verification_required=(VERIFY_CMD,), status=status)
    st["work_units"] = list(units)
    st["dispatch"] = {"max_workers": max_workers, "active": list(active)}
    state.save_state(root, st)
    return st


def waves_of(root, task_id=TID):
    """容错读取盘上 dispatch.waves。"""
    st = state.load_state(root, task_id)
    dispatch_block = st.get("dispatch")
    waves = (dispatch_block.get("waves")
             if isinstance(dispatch_block, dict) else None)
    return waves if isinstance(waves, list) else []


def state_bytes(root, task_id=TID):
    """读取 state.json 原始字节（零写入断言用）。"""
    with open(state.state_path(root, task_id), "rb") as fh:
        return fh.read()


def events(root, name, task_id=TID):
    """读取真实 journal 并过滤事件名。"""
    return [e for e in journal.read_events(root, task_id)
            if e.get("event") == name]


def run_cli(*args):
    """调用 cli.main 并捕获 stdout，返回 (退出码, 解析后 JSON)。"""
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        code = cli.main(list(args))
    return code, json.loads(buffer.getvalue())


def run_hook(script, stdin_text, project_dir):
    """以子进程运行钩子脚本（显式 UTF-8，CI Windows 教训）。"""
    return subprocess.run(
        [sys.executable, str(script)],
        input=stdin_text, text=True, capture_output=True,
        encoding="utf-8", errors="replace",
        env=dict(os.environ, ZCODE_PROJECT_DIR=str(project_dir)))


def hook_decision(result):
    """解析钩子 stdout 的单行 JSON hookSpecificOutput。"""
    return json.loads(result.stdout.strip())["hookSpecificOutput"]


def wave_record(wave_id, units, *, status="active", closed_at=None,
                worker_budget=1):
    """构造键名冻结的 wave 记录（校验测试用）。"""
    return {
        "wave_id": wave_id,
        "units": list(units),
        "worker_budget": worker_budget,
        "quota_status": "AVAILABLE",
        "created_at": "2026-08-31T00:00:00.000Z",
        "status": status,
        "closed_at": ("2026-08-31T01:00:00.000Z" if closed_at is None
                      and status == "closed" else closed_at),
    }


# —— 1. 双单元 happy path ——

class WaveHappyPathTest(unittest.TestCase):
    """一次调用完成决策 → 全量租约 → wave 记录 → 批量 permit → journal。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        # 故意逆 id 序：批准集必须按 state.work_units 原序（非 topo 序）
        make_task(self.root, [wu("u2", ("src/b/**",)),
                              wu("u1", ("src/a/**",))])

    def tearDown(self):
        self._tmp.cleanup()

    def test_wave_prepared_end_to_end(self):
        result = task_manager.prepare_dispatch_wave(self.root, TID, quota_status="AVAILABLE")
        # wave_id 形状："wave-" + 12 hex
        wave_id = result["wave_id"]
        self.assertIsInstance(wave_id, str)
        self.assertTrue(wave_id.startswith("wave-"))
        self.assertEqual(len(wave_id), 17)
        int(wave_id[len("wave-"):], 16)  # 12 位 hex
        # 批准集按 work_units 原序（u2 在前）
        self.assertEqual(result["units"], ["u2", "u1"])
        self.assertEqual(result["worker_budget"], 2)
        self.assertEqual(result["deferred"], [])
        self.assertEqual(result["waiting_quota"], [])
        # 双 permit：均带 wave_id，unit 覆盖两个成员，文件真实落盘
        permits = result["permits"]
        self.assertEqual(len(permits), 2)
        self.assertEqual({p["unit_id"] for p in permits}, {"u1", "u2"})
        self.assertEqual(len({p["permit_id"] for p in permits}), 2)
        for permit in permits:
            self.assertEqual(permit["wave_id"], wave_id)
            loaded = dispatch_wave.load_permit(self.root, TID,
                                               permit["permit_id"])
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded["wave_id"], wave_id)
        # 双租约持有
        leases = lease.lease_state(self.root, TID)
        self.assertEqual(leases["src/a/**"]["owner"], "u1")
        self.assertEqual(leases["src/b/**"]["owner"], "u2")
        # wave 记录 active 落盘
        waves = waves_of(self.root)
        self.assertEqual(len(waves), 1)
        wave = waves[0]
        self.assertEqual(wave["wave_id"], wave_id)
        self.assertEqual(wave["units"], ["u2", "u1"])
        self.assertEqual(wave["worker_budget"], 2)
        self.assertEqual(wave["quota_status"], "AVAILABLE")
        self.assertEqual(wave["status"], "active")
        self.assertIsNone(wave["closed_at"])
        self.assertIsInstance(wave["created_at"], str)
        self.assertNotEqual(wave["created_at"], "")
        # journal 单条 dispatch_wave_prepared，无逐单元重复事件
        prepared = events(self.root, "dispatch_wave_prepared")
        self.assertEqual(len(prepared), 1)
        self.assertEqual(prepared[0]["wave_id"], wave_id)
        self.assertEqual(prepared[0]["units"], ["u2", "u1"])
        self.assertEqual(prepared[0]["worker_budget"], 2)
        self.assertEqual(prepared[0]["permits"],
                         [p["permit_id"] for p in permits])
        self.assertEqual(events(self.root, "dispatch_prepared"), [])
        self.assertEqual(events(self.root, "dispatch_permit_created"), [])

    def test_worker_budget_capped_by_max_workers(self):
        # 2 单元 ready、max_workers=1：只批 1 个，worker_budget = min(1, 1)
        result = task_manager.prepare_dispatch_wave(self.root, TID,
                                                    max_workers=1, quota_status="AVAILABLE")
        self.assertEqual(len(result["units"]), 1)
        self.assertEqual(result["worker_budget"], 1)
        self.assertEqual(len(result["permits"]), 1)
        self.assertEqual(waves_of(self.root)[0]["worker_budget"], 1)

    def test_single_unit_wave_is_legal(self):
        # 单元 wave：plan 只批 1 个时照常成 wave
        make_task(self.root, [wu("solo", ("src/a/**",))], max_workers=2)
        result = task_manager.prepare_dispatch_wave(self.root, TID, quota_status="AVAILABLE")
        self.assertEqual(result["units"], ["solo"])
        self.assertEqual(waves_of(self.root)[0]["units"], ["solo"])

    def test_new_wave_id_shape_and_uniqueness(self):
        first = dispatch_wave.new_wave_id()
        second = dispatch_wave.new_wave_id()
        self.assertTrue(first.startswith("wave-"))
        self.assertEqual(len(first), 17)
        self.assertNotEqual(first, second)


# —— 2/3. 租约冲突安全降级与批准集空 ——

class WaveLeaseConflictDegradationTest(unittest.TestCase):
    """租约中途冲突 → 释放本轮租约、剔除冲突单元、重 plan。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        make_task(self.root, [wu("u1", ("src/a/**",)),
                              wu("u2", ("src/b/**",))])

    def tearDown(self):
        self._tmp.cleanup()

    def test_midway_conflict_degrades_to_successful_members(self):
        real_acquire = task_manager.lease.acquire_lease
        real_release = task_manager.lease.release_lease
        acquires = []
        released = []

        def flaky_acquire(repo, tid, owner, paths, **kwargs):
            acquires.append(owner)
            if owner == "u2":
                # 模拟 plan 之后、acquire 之前的竞态占用
                raise lease.LeaseConflictError(
                    "租约冲突：src/b/**（持有者 ghost-worker）",
                    [{"path": "src/b/**", "owner": "ghost-worker"}])
            return real_acquire(repo, tid, owner, paths, **kwargs)

        def recording_release(repo, tid, owner, *args, **kwargs):
            released.append(owner)
            return real_release(repo, tid, owner, *args, **kwargs)

        with mock.patch.object(task_manager.lease, "acquire_lease",
                               side_effect=flaky_acquire), \
                mock.patch.object(task_manager.lease, "release_lease",
                                  side_effect=recording_release):
            result = task_manager.prepare_dispatch_wave(self.root, TID, quota_status="AVAILABLE")
        # wave 只含成功成员；冲突单元被剔除（重 plan 后不再批准）
        self.assertEqual(result["units"], ["u1"])
        self.assertEqual(waves_of(self.root)[0]["units"], ["u1"])
        self.assertEqual(len(result["permits"]), 1)
        self.assertEqual(result["permits"][0]["unit_id"], "u1")
        # 冲突轮次已获取的 u1 租约被释放（安全降级），随后重试重取
        self.assertIn("u1", released)
        # 无冲突单元租约残留；成功成员租约在位
        leases = lease.lease_state(self.root, TID)
        self.assertEqual(set(leases), {"src/a/**"})
        self.assertEqual(leases["src/a/**"]["owner"], "u1")
        # journal 仅成功轮一条
        prepared = events(self.root, "dispatch_wave_prepared")
        self.assertEqual(len(prepared), 1)
        self.assertEqual(prepared[0]["units"], ["u1"])


class WaveApprovedEmptyTest(unittest.TestCase):
    """全部冲突 / 批准集空 → TaskManagerError（零租约零 wave 零事件）。

    wu-21-09 起 PRESSURE 不再属于本拒绝矩阵（整批抑制分支删除）——
    其预算收缩为 1 的 wave 形态保留在本类末尾作对照锚。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_all_conflicts_raise_with_zero_residue(self):
        make_task(self.root, [wu("u1", ("src/a/**",)),
                              wu("u2", ("src/b/**",))])
        before = state_bytes(self.root)

        def always_conflict(repo, tid, owner, paths, **kwargs):
            raise lease.LeaseConflictError(
                "租约冲突（注入）", [{"path": "src/**", "owner": "ghost"}])

        with mock.patch.object(task_manager.lease, "acquire_lease",
                               side_effect=always_conflict):
            with self.assertRaises(task_manager.TaskManagerError) as ctx:
                task_manager.prepare_dispatch_wave(self.root, TID, quota_status="AVAILABLE")
        # 零租约残留、无 wave 记录、无事件、state.json 字节不变
        self.assertEqual(lease.lease_state(self.root, TID), {})
        self.assertEqual(waves_of(self.root), [])
        self.assertEqual(journal.read_events(self.root, TID), [])
        self.assertEqual(state_bytes(self.root), before)
        self.assertIn("未批准任何单元", str(ctx.exception))

    def test_pressure_budget_one_batches_single_unit(self):
        # wu-21-09：PRESSURE 不再整批抑制（pressure_suppressed 分支删除）
        # ——预算按 §12 收缩为 1：双就绪单元只批 1 个成 wave，另一个以
        # concurrency 落在 deferred（不是 quota 挂起），租约只落 1 张
        make_task(self.root, [wu("u1", ("src/a/**",)),
                              wu("u2", ("src/b/**",))])
        result = task_manager.prepare_dispatch_wave(self.root, TID,
                                                    quota_status="PRESSURE")
        self.assertEqual(result["units"], ["u1"])
        self.assertEqual(result["worker_budget"], 1)
        self.assertEqual(result["deferred"],
                         [{"id": "u2", "reason": "concurrency"}])
        self.assertEqual(result["waiting_quota"], [])
        wave = waves_of(self.root)[0]
        self.assertEqual(wave["worker_budget"], 1)
        self.assertEqual(wave["quota_status"], "PRESSURE")
        self.assertEqual(set(lease.lease_state(self.root, TID)),
                         {"src/a/**"})

    def test_quota_exhausted_waiting_quota_wording(self):
        make_task(self.root, [wu("u1", ("src/a/**",))])
        with self.assertRaises(task_manager.TaskManagerError) as ctx:
            task_manager.prepare_dispatch_wave(self.root, TID,
                                               quota_status="EXHAUSTED")
        self.assertIn("quota EXHAUSTED", str(ctx.exception))
        self.assertEqual(lease.lease_state(self.root, TID), {})
        self.assertEqual(waves_of(self.root), [])
        self.assertEqual(journal.read_events(self.root, TID), [])


# —— RB-21-03：wave / 单单元 prepare 事务补偿 ——

class WaveTransactionCompensationTest(unittest.TestCase):
    """permit 创建 / state 保存失败 → 整笔补偿（all-or-safe-degrade）：
    失败时无 wave、无本轮租约、无活跃 permit、无 prepared 事件；补偿
    自身失败 fail-closed（transaction_aborted.compensation_error 记账 +
    原始异常上抛）。注入点与生产同形：create_permit / save_state 抛
    OSError；磁盘终态逐项断言（waves / leases.json / permits 目录 /
    journal），不是只看返回值。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        make_task(self.root, [wu("u1", ("src/a/**",)),
                              wu("u2", ("src/b/**",))])
        self.created = []  # 补偿断言用：真实落盘成功的 permit

    def tearDown(self):
        self._tmp.cleanup()

    def _permit_patch(self, fail_unit=None):
        """create_permit 注入：捕获全部成功创建的 permit；fail_unit
        非 None 时该单元在真实落盘前抛 OSError（第 N 张失败场景）。"""
        real_create = dispatch_wave.create_permit
        created = self.created

        def patched_create(repo, tid, unit_id, **kwargs):
            if unit_id == fail_unit:
                raise OSError("注入：unit %s 的 permit 落盘失败" % unit_id)
            permit = real_create(repo, tid, unit_id, **kwargs)
            created.append(permit)
            return permit

        return mock.patch.object(task_manager.dispatch_wave,
                                 "create_permit", side_effect=patched_create)

    def _retired_path(self, permit):
        return (dispatch_wave.permits_dir(self.root, TID)
                / (permit["permit_id"]
                   + dispatch_wave.INVALIDATED_SUFFIX))

    def test_second_permit_create_failure_rolls_back_wave(self):
        # 第 2 张 permit（u2）落盘失败：wave 不落盘、租约全释放、
        # u1 的 permit 作废，异常原样上抛
        with self._permit_patch(fail_unit="u2"):
            with self.assertRaises(OSError) as ctx:
                task_manager.prepare_dispatch_wave(self.root, TID,
                                                   quota_status="AVAILABLE")
        self.assertIn("u2", str(ctx.exception))
        # 磁盘终态：无 wave、无租约、无活跃 permit
        self.assertEqual(waves_of(self.root), [])
        self.assertEqual(lease.lease_state(self.root, TID), {})
        self.assertEqual(dispatch_wave.list_permits(self.root, TID), [])
        # u1 permit 的审计轨迹以 .invalidated.json 保留
        self.assertTrue(self._retired_path(self.created[0]).is_file())
        # 补偿记账：transaction_aborted（api + 失败阶段 + wave_id）
        aborted = events(self.root, "transaction_aborted")
        self.assertEqual(len(aborted), 1)
        self.assertEqual(aborted[0]["api"], "prepare_dispatch_wave")
        self.assertTrue(aborted[0]["reason"].startswith(
            "create_permit_failed"), aborted[0]["reason"])
        self.assertTrue(aborted[0]["wave_id"].startswith("wave-"))
        self.assertNotIn("compensation_error", aborted[0])

    def test_permit_failure_releases_all_wave_leases(self):
        # 本轮批准集（u1、u2）租约全部 acquire 在先 → 补偿全部释放
        released = []
        real_release = task_manager.lease.release_lease

        def recording_release(repo, tid, owner, *args, **kwargs):
            released.append(owner)
            return real_release(repo, tid, owner, *args, **kwargs)

        with self._permit_patch(fail_unit="u2"), \
                mock.patch.object(task_manager.lease, "release_lease",
                                  side_effect=recording_release):
            with self.assertRaises(OSError):
                task_manager.prepare_dispatch_wave(self.root, TID,
                                                   quota_status="AVAILABLE")
        self.assertEqual(sorted(released), ["u1", "u2"])
        self.assertEqual(lease.lease_state(self.root, TID), {})

    def test_permit_failure_invalidates_partial_permits(self):
        # 前 N-1 张已创建 permit 全部作废：活跃面 list 为空、load 视同
        # 不存在（rename 防重放推论）、审计文件保留
        with self._permit_patch(fail_unit="u2"):
            with self.assertRaises(OSError):
                task_manager.prepare_dispatch_wave(self.root, TID,
                                                   quota_status="AVAILABLE")
        self.assertEqual(len(self.created), 1)
        first = self.created[0]
        self.assertEqual(first["unit_id"], "u1")
        self.assertIsNone(dispatch_wave.load_permit(self.root, TID,
                                                    first["permit_id"]))
        self.assertTrue(self._retired_path(first).is_file())
        self.assertEqual(dispatch_wave.list_permits(self.root, TID), [])

    def test_wave_state_save_failure_rolls_back_permits(self):
        # save_state 原子写失败：两张 permit 已创建 → 全部作废、租约
        # 全释放；wave 未落盘（tmp+os.replace 失败即未写）

        def broken_save(repo, st, **kwargs):
            raise OSError("注入：state 落盘失败")

        with self._permit_patch(), \
                mock.patch.object(task_manager.state, "save_state",
                                  side_effect=broken_save):
            with self.assertRaises(OSError) as ctx:
                task_manager.prepare_dispatch_wave(self.root, TID,
                                                   quota_status="AVAILABLE")
        self.assertIn("state 落盘失败", str(ctx.exception))
        self.assertEqual(waves_of(self.root), [])
        self.assertEqual(lease.lease_state(self.root, TID), {})
        self.assertEqual(dispatch_wave.list_permits(self.root, TID), [])
        self.assertEqual(len(self.created), 2)
        for permit in self.created:
            self.assertTrue(self._retired_path(permit).is_file(),
                            permit["permit_id"])
        aborted = events(self.root, "transaction_aborted")
        self.assertEqual(len(aborted), 1)
        self.assertEqual(aborted[0]["api"], "prepare_dispatch_wave")
        self.assertTrue(aborted[0]["reason"].startswith(
            "save_state_failed"), aborted[0]["reason"])

    def test_wave_prepare_failure_has_no_prepared_event(self):
        # 失败路径 journal 只有 transaction_aborted（AVAILABLE 直通，
        # 无 quota_resolved）——dispatch_wave_prepared 绝不出现
        with self._permit_patch(fail_unit="u2"):
            with self.assertRaises(OSError):
                task_manager.prepare_dispatch_wave(self.root, TID,
                                                   quota_status="AVAILABLE")
        all_events = journal.read_events(self.root, TID)
        self.assertEqual([e["event"] for e in all_events],
                         ["transaction_aborted"])
        self.assertEqual(events(self.root, "dispatch_wave_prepared"), [])

    def test_compensation_failure_fails_closed_with_journal_warning(self):
        # 补偿中 release_lease 也炸 → fail-closed：警告事件仍落
        # （compensation_error 记账）+ 原始异常上抛（非补偿异常掩盖）
        real_create_patch = self._permit_patch(fail_unit="u2")

        def exploding_release(repo, tid, owner, *args, **kwargs):
            raise OSError("注入：补偿中 release_lease 也失败")

        with real_create_patch, \
                mock.patch.object(task_manager.lease, "release_lease",
                                  side_effect=exploding_release):
            with self.assertRaises(OSError) as ctx:
                task_manager.prepare_dispatch_wave(self.root, TID,
                                                   quota_status="AVAILABLE")
        self.assertIn("u2", str(ctx.exception))
        self.assertNotIn("release_lease 也失败", str(ctx.exception))
        # permit 补偿先于租约释放完成：无活跃 permit 残留
        self.assertEqual(dispatch_wave.list_permits(self.root, TID), [])
        aborted = events(self.root, "transaction_aborted")
        self.assertEqual(len(aborted), 1)
        self.assertTrue(aborted[0]["reason"].startswith(
            "create_permit_failed"), aborted[0]["reason"])
        comp_errors = aborted[0]["compensation_error"]
        self.assertEqual(len(comp_errors), 2)
        self.assertTrue(all(e.startswith("release_lease(u")
                            for e in comp_errors), comp_errors)
        # 释放失败的租约残留盘上（绝不静默）——警告事件即其账面
        self.assertEqual(set(lease.lease_state(self.root, TID)),
                         {"src/a/**", "src/b/**"})


class SingleUnitPermitFailureCompensationTest(unittest.TestCase):
    """单单元 prepare_dispatch：create_permit 失败 → 自动释放该租约再
    上抛（RB-21-03：消除「人工 abort_dispatch 回退」缺口）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        make_task(self.root, [wu("u1", ("src/a/**",))])

    def tearDown(self):
        self._tmp.cleanup()

    def test_single_permit_failure_releases_lease(self):
        def exploding_create(repo, tid, unit_id, **kwargs):
            raise OSError("注入：单单元 permit 落盘失败")

        with mock.patch.object(task_manager.dispatch_wave, "create_permit",
                               side_effect=exploding_create):
            with self.assertRaises(OSError):
                task_manager.prepare_dispatch(self.root, TID, "u1",
                                              quota_status="AVAILABLE")
        # 失败零残留：租约已释放、无活跃 permit、成功路径事件未落
        self.assertEqual(lease.lease_state(self.root, TID), {})
        self.assertEqual(dispatch_wave.list_permits(self.root, TID), [])
        self.assertEqual(events(self.root, "dispatch_prepared"), [])
        self.assertEqual(events(self.root, "dispatch_permit_created"), [])
        aborted = events(self.root, "transaction_aborted")
        self.assertEqual(len(aborted), 1)
        self.assertEqual(aborted[0]["api"], "prepare_dispatch")
        self.assertTrue(aborted[0]["reason"].startswith(
            "create_permit_failed"), aborted[0]["reason"])
        self.assertNotIn("wave_id", aborted[0])  # 单单元无 wave 语义


# —— 4. ownership 冲突进 deferred ——

class WaveDeferredPassthroughTest(unittest.TestCase):
    """ownership 冲突单元不进 wave，落在返回值 deferred（透传）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        # 两个单元 ownership 重叠：plan 的 ownership 闸只批第一个
        make_task(self.root, [wu("u1", ("src/same/**",)),
                              wu("u2", ("src/same/**",))])

    def tearDown(self):
        self._tmp.cleanup()

    def test_ownership_conflicted_unit_deferred(self):
        result = task_manager.prepare_dispatch_wave(self.root, TID, quota_status="AVAILABLE")
        self.assertEqual(result["units"], ["u1"])
        self.assertEqual(result["deferred"],
                         [{"id": "u2", "reason": "ownership_conflict"}])
        self.assertEqual(waves_of(self.root)[0]["units"], ["u1"])
        self.assertEqual({p["unit_id"] for p in result["permits"]}, {"u1"})


# —— mode/reason 共享校验（_validate_permit_mode 口径） ——

class WaveModeValidationTest(unittest.TestCase):
    """prepare_dispatch_wave 复用 prepare_dispatch 的 mode/reason 校验。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        make_task(self.root, [wu("u1", ("src/a/**",))])

    def tearDown(self):
        self._tmp.cleanup()

    def test_foreground_without_reason_rejected_before_io(self):
        with self.assertRaises(task_manager.TaskManagerError) as ctx:
            task_manager.prepare_dispatch_wave(self.root, TID,
                                               mode="foreground", quota_status="AVAILABLE")
        self.assertIn("foreground", str(ctx.exception))
        # 校验先于任何写副作用
        self.assertEqual(lease.lease_state(self.root, TID), {})
        self.assertEqual(waves_of(self.root), [])

    def test_foreground_with_valid_reason_succeeds(self):
        result = task_manager.prepare_dispatch_wave(
            self.root, TID, mode="foreground",
            reason="synchronous_dependency", quota_status="AVAILABLE")
        self.assertEqual(result["permits"][0]["mode"], "foreground")
        self.assertEqual(result["permits"][0]["reason"],
                         "synchronous_dependency")

    def test_background_drops_explicit_reason(self):
        # background 恒 reason null：显式传入的 reason 静默归 null
        # （原 prepare_dispatch 口径逐字保持，create_permit 端才严格拒绝）
        result = task_manager.prepare_dispatch_wave(
            self.root, TID, reason="synchronous_dependency", quota_status="AVAILABLE")
        self.assertIsNone(result["permits"][0]["reason"])


# —— 6. validate_wave_membership 数据层 ——

class WaveMembershipTest(unittest.TestCase):
    """validate_wave_membership：成员资格校验链与英文 reason。"""

    WAVE_ID = "wave-111111111111"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        make_task(self.root, [wu("u1", ("src/a/**",)),
                              wu("u2", ("src/b/**",))])
        st = state.load_state(self.root, TID)
        st["dispatch"]["waves"] = [wave_record(self.WAVE_ID, ["u1", "u2"])]
        state.save_state(self.root, st)

    def tearDown(self):
        self._tmp.cleanup()

    def _permit(self, wave_id=WAVE_ID, unit_id="u1"):
        return {"permit_id": "dp-000000000000", "task_id": TID,
                "unit_id": unit_id, "wave_id": wave_id}

    def test_active_member_ok(self):
        ok, reason = dispatch_wave.validate_wave_membership(
            self.root, TID, self._permit())
        self.assertTrue(ok)
        self.assertIsNone(reason)

    def test_closed_wave_rejected(self):
        st = state.load_state(self.root, TID)
        st["dispatch"]["waves"][0]["status"] = "closed"
        st["dispatch"]["waves"][0]["closed_at"] = "2026-08-31T02:00:00.000Z"
        state.save_state(self.root, st)
        ok, reason = dispatch_wave.validate_wave_membership(
            self.root, TID, self._permit())
        self.assertFalse(ok)
        self.assertEqual(reason, "wave not active")

    def test_unknown_wave_id_rejected(self):
        ok, reason = dispatch_wave.validate_wave_membership(
            self.root, TID, self._permit(wave_id="wave-999999999999"))
        self.assertFalse(ok)
        self.assertEqual(reason, "wave not found")

    def test_wave_id_none_always_ok(self):
        # 单单元 permit 恒放行——连任务目录都不存在也放行
        with tempfile.TemporaryDirectory() as empty:
            ok, reason = dispatch_wave.validate_wave_membership(
                empty, TID, self._permit(wave_id=None))
        self.assertTrue(ok)
        self.assertIsNone(reason)
        ok, reason = dispatch_wave.validate_wave_membership(
            self.root, TID, self._permit(wave_id=None))
        self.assertTrue(ok)
        self.assertIsNone(reason)

    def test_missing_state_rejected(self):
        with tempfile.TemporaryDirectory() as empty:
            ok, reason = dispatch_wave.validate_wave_membership(
                empty, TID, self._permit())
        self.assertFalse(ok)
        self.assertEqual(reason, "wave state unavailable")

    def test_member_mismatch_rejected(self):
        ok, reason = dispatch_wave.validate_wave_membership(
            self.root, TID, self._permit(unit_id="u3"))
        self.assertFalse(ok)
        self.assertEqual(reason, "wave member mismatch")


# —— 7. pre_tool_use hook：wave permit 门 ——

class PreToolUseWaveGateTest(unittest.TestCase):
    """wave permit + active wave → allow；closed wave → deny。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        make_task(self.root, [wu("u1", ("src/a/**",)),
                              wu("u2", ("src/b/**",))])
        result = task_manager.prepare_dispatch_wave(self.root, TID, quota_status="AVAILABLE")
        self.wave_id = result["wave_id"]
        self.permit_u1 = next(p for p in result["permits"]
                              if p["unit_id"] == "u1")

    def tearDown(self):
        self._tmp.cleanup()

    def _stdin(self, permit_id):
        marker = dispatch_wave.marker_for(permit_id)
        return json.dumps({
            "tool_name": "Agent",
            "tool_input": {"subagent_type": "flash-implementer",
                           "description": "fixture dispatch",
                           "prompt": "implement %s" % marker}})

    def test_active_wave_member_allowed(self):
        result = run_hook(PRE_TOOL_USE, self._stdin(self.permit_u1["permit_id"]),
                          self.root)
        self.assertEqual(result.returncode, 0, result.stderr)
        block = hook_decision(result)
        self.assertNotIn("permissionDecision", block)
        # background permit 经 updatedInput 强制后台（既有行为不回退）
        self.assertIs(block["updatedInput"]["run_in_background"], True)

    def test_closed_wave_denied_with_reprepare_guidance(self):
        st = state.load_state(self.root, TID)
        st["dispatch"]["waves"][0]["status"] = "closed"
        st["dispatch"]["waves"][0]["closed_at"] = "2026-08-31T02:00:00.000Z"
        state.save_state(self.root, st)
        result = run_hook(PRE_TOOL_USE, self._stdin(self.permit_u1["permit_id"]),
                          self.root)
        self.assertEqual(result.returncode, 0, result.stderr)
        block = hook_decision(result)
        self.assertEqual(block["permissionDecision"], "deny")
        reason = block["permissionDecisionReason"]
        # 报文 actionable：含 wave_id、拦截原因与 re-prepare wave 指引
        self.assertIn(self.wave_id, reason)
        self.assertIn("wave not active", reason)
        self.assertIn("re-prepare the wave", reason)
        self.assertIn("prepare_dispatch_wave", reason)
        self.assertIn("GLM_CONDUCTOR_DISPATCH=", reason)

    def test_unknown_wave_denied(self):
        # 伪造 wave_id 的 permit：validate_permit 过（自身字段自洽）但
        # wave 不存在 → wave not found deny
        permit = dispatch_wave.create_permit(
            self.root, TID, "u1", wave_id="wave-999999999999")
        result = run_hook(PRE_TOOL_USE, self._stdin(permit["permit_id"]),
                          self.root)
        block = hook_decision(result)
        self.assertEqual(block["permissionDecision"], "deny")
        self.assertIn("wave not found",
                      block["permissionDecisionReason"])


# —— 8. finish_unit wave 收口 ——

class FinishUnitWaveClosureTest(unittest.TestCase):
    """末成员终态 → wave closed + wave_closed 事件；单成员 wave 立即关。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def _commit_and_finish(self, uid, outcome="cancelled"):
        task_manager.commit_dispatch(self.root, TID, uid)
        return task_manager.finish_unit(self.root, TID, uid, outcome=outcome)

    def test_last_member_finish_closes_wave(self):
        make_task(self.root, [wu("u1", ("src/a/**",)),
                              wu("u2", ("src/b/**",))])
        result = task_manager.prepare_dispatch_wave(self.root, TID, quota_status="AVAILABLE")
        wave_id = result["wave_id"]
        # 首个成员收尾：wave 仍 active（还有 running 成员）
        self._commit_and_finish("u1")
        wave = waves_of(self.root)[0]
        self.assertEqual(wave["status"], "active")
        self.assertIsNone(wave["closed_at"])
        self.assertEqual(events(self.root, "wave_closed"), [])
        # 末成员收尾：wave 关闭 + closed_at + 单条 wave_closed
        self._commit_and_finish("u2")
        wave = waves_of(self.root)[0]
        self.assertEqual(wave["status"], "closed")
        self.assertIsInstance(wave["closed_at"], str)
        self.assertNotEqual(wave["closed_at"], "")
        closed = events(self.root, "wave_closed")
        self.assertEqual(len(closed), 1)
        self.assertEqual(closed[0]["wave_id"], wave_id)

    def test_single_member_wave_closes_immediately(self):
        make_task(self.root, [wu("solo", ("src/a/**",))])
        result = task_manager.prepare_dispatch_wave(self.root, TID, quota_status="AVAILABLE")
        self._commit_and_finish("solo")
        wave = waves_of(self.root)[0]
        self.assertEqual(wave["status"], "closed")
        self.assertEqual(
            [e["wave_id"] for e in events(self.root, "wave_closed")],
            [result["wave_id"]])

    def test_no_waves_key_zero_behavior(self):
        # legacy 任务（无 waves 键）：finish 正常，零 wave_closed 噪声
        make_task(self.root, [wu("u1", ("src/a/**",))])
        task_manager.prepare_dispatch(self.root, TID, "u1", quota_status="AVAILABLE")
        self._commit_and_finish("u1")
        self.assertEqual(waves_of(self.root), [])
        self.assertEqual(events(self.root, "wave_closed"), [])
        self.assertEqual(len(events(self.root, "unit_finished")), 1)

    def test_wave_open_while_member_not_terminal(self):
        # 非 verifying/终态成员（ready）阻挡关闭
        make_task(self.root, [wu("u1", ("src/a/**",)),
                              wu("u2", ("src/b/**",))])
        task_manager.prepare_dispatch_wave(self.root, TID, quota_status="AVAILABLE")
        self._commit_and_finish("u1")
        # u2 从未 commit（仍 ready）→ wave 不关闭
        self.assertEqual(waves_of(self.root)[0]["status"], "active")
        self.assertEqual(events(self.root, "wave_closed"), [])


# —— 9. state._validate_dispatch waves 形状校验 ——

class DispatchWavesValidationTest(unittest.TestCase):
    """waves 可选键：缺键 legacy 合法；形状错误中文报错带前缀。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.st = make_task(self.root, [wu("u1", ("src/a/**",))])

    def tearDown(self):
        self._tmp.cleanup()

    def _errors(self, waves):
        self.st["dispatch"]["waves"] = waves
        return state.validate_state(self.st)

    def test_legacy_without_waves_key_is_legal(self):
        self.st["dispatch"].pop("waves", None)
        self.assertEqual(
            [e for e in state.validate_state(self.st)
             if "dispatch.waves" in e], [])

    def test_valid_wave_roundtrips(self):
        errors = self._errors([wave_record("wave-111111111111", ["u1"])])
        self.assertEqual(
            [e for e in errors if "dispatch.waves" in e], [])
        state.save_state(self.root, self.st)
        self.assertEqual(waves_of(self.root),
                         [wave_record("wave-111111111111", ["u1"])])

    def test_waves_not_list(self):
        errors = self._errors("nope")
        self.assertIn("dispatch.waves 必须是数组", errors)

    def test_entry_not_dict(self):
        errors = self._errors(["nope"])
        self.assertIn("dispatch.waves[0] 必须是 JSON 对象", errors)

    def test_missing_required_keys(self):
        errors = self._errors([{"wave_id": "wave-111111111111"}])
        for key in ("units", "worker_budget", "quota_status", "created_at",
                    "status", "closed_at"):
            self.assertIn("dispatch.waves[0] 缺少必填键 %s" % key, errors)

    def test_duplicate_wave_id(self):
        errors = self._errors([
            wave_record("wave-111111111111", ["u1"]),
            wave_record("wave-111111111111", ["u2"])])
        self.assertTrue(any("dispatch.waves[1].wave_id" in e
                            and "重复" in e for e in errors), errors)

    def test_worker_budget_bounds(self):
        for bad in (0, -1, True, "2", 1.5, None):
            errors = self._errors([
                wave_record("wave-111111111111", ["u1"],
                            worker_budget=bad)])
            self.assertIn("dispatch.waves[0].worker_budget 必须是 >= 1 的整数",
                          errors, "worker_budget=%r" % (bad,))

    def test_quota_status_enum(self):
        errors = self._errors([wave_record("wave-111111111111", ["u1"])])
        self.st["dispatch"]["waves"][0]["quota_status"] = "MEGA"
        errors = state.validate_state(self.st)
        self.assertTrue(any("dispatch.waves[0].quota_status" in e
                            and "MEGA" in e for e in errors), errors)

    def test_status_enum(self):
        record = wave_record("wave-111111111111", ["u1"])
        record["status"] = "paused"
        errors = self._errors([record])
        self.assertTrue(any("dispatch.waves[0].status" in e
                            for e in errors), errors)

    def test_closed_contradiction(self):
        record = wave_record("wave-111111111111", ["u1"], status="closed")
        record["closed_at"] = None  # closed 却无 closed_at → 矛盾
        errors = self._errors([record])
        self.assertTrue(any("dispatch.waves[0].closed_at" in e
                            and "矛盾" in e for e in errors), errors)

    def test_units_shape(self):
        record = wave_record("wave-111111111111", [])
        errors = self._errors([record])
        self.assertIn("dispatch.waves[0].units 必须是非空数组", errors)
        errors = self._errors([wave_record("wave-111111111111", ["u1", ""])])
        self.assertIn("dispatch.waves[0].units[1] 必须是非空字符串", errors)


# —— agent_launched 事件带 wave_id（wu-21-08 设计决策 6 接线） ——

class AgentLaunchWaveIdTest(unittest.TestCase):
    """record_agent_launch：permit 带 wave_id 时事件携带；否则 null。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def _last_event(self):
        return journal.read_events(self.root, TID)[-1]

    def test_wave_permit_carries_wave_id(self):
        agent_run.record_agent_launch(
            self.root, TID, {"tool_use_id": "call_1"},
            permit={"permit_id": "dp-000000000000", "unit_id": "u1",
                    "wave_id": "wave-111111111111", "mode": "background"})
        event = self._last_event()
        self.assertEqual(event["event"], "agent_launched")
        self.assertEqual(event["wave_id"], "wave-111111111111")

    def test_single_unit_permit_wave_id_null(self):
        agent_run.record_agent_launch(
            self.root, TID, {"tool_use_id": "call_2"},
            permit={"permit_id": "dp-000000000000", "unit_id": "u1",
                    "mode": "background"})
        self.assertIsNone(self._last_event()["wave_id"])

    def test_permit_none_wave_id_null(self):
        agent_run.record_agent_launch(self.root, TID, {}, permit=None)
        self.assertIsNone(self._last_event()["wave_id"])


class PostToolUseWaveWiringTest(unittest.TestCase):
    """post_tool_use → agent_launched 端到端携带 wave_id（子进程）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        make_task(self.root, [wu("u1", ("src/a/**",))])
        result = task_manager.prepare_dispatch_wave(self.root, TID, quota_status="AVAILABLE")
        self.wave_id = result["wave_id"]
        self.permit_id = result["permits"][0]["permit_id"]

    def tearDown(self):
        self._tmp.cleanup()

    def test_agent_launched_event_has_wave_id(self):
        marker = dispatch_wave.marker_for(self.permit_id)
        payload = json.dumps({
            "hook_event_name": "PostToolUse", "tool_name": "Agent",
            "tool_input": {"subagent_type": "flash-implementer",
                           "prompt": "do %s" % marker},
            "tool_use_id": "call_1",
            "tool_response": {"agentId": "agent_fixture"}})
        result = run_hook(POST_TOOL_USE, payload, self.root)
        self.assertEqual(result.returncode, 0, result.stderr)
        launched = events(self.root, "agent_launched")
        self.assertEqual(len(launched), 1)
        self.assertEqual(launched[0]["wave_id"], self.wave_id)
        self.assertEqual(launched[0]["permit_id"], self.permit_id)


# —— 10. CLI wave-prepare / wave-show ——

class WaveCliTest(unittest.TestCase):
    """wave-prepare / wave-show：退出码 0/2/1 与 JSON markers 输出。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        make_task(self.root, [wu("u1", ("src/a/**",)),
                              wu("u2", ("src/b/**",))])

    def tearDown(self):
        self._tmp.cleanup()

    def test_wave_prepare_success_with_markers(self):
        # wu-21-15 起 CLI 缺省 quota_status=None 走 resolver（零网络纪律：
        # 进程内用例显式 AVAILABLE 直通；缺省→resolver 行为锚定在
        # tests/test_cli_extensions.py 的 monkeypatch 用例）
        code, payload = run_cli("wave-prepare", self.root, TID, "AVAILABLE")
        self.assertEqual(code, 0)
        self.assertTrue(payload["wave_id"].startswith("wave-"))
        self.assertEqual(payload["units"], ["u1", "u2"])
        self.assertEqual(payload["worker_budget"], 2)
        self.assertEqual(len(payload["permits"]), 2)
        # markers：与 permits 一一对应，冻结格式 GLM_CONDUCTOR_DISPATCH=
        self.assertEqual(len(payload["markers"]), 2)
        for permit, marker in zip(payload["permits"], payload["markers"]):
            self.assertEqual(marker,
                             dispatch_wave.marker_for(permit["permit_id"]))
        self.assertEqual(payload["deferred"], [])
        self.assertEqual(payload["waiting_quota"], [])

    def test_wave_prepare_exit_codes(self):
        # 任务不存在 → 1
        code, payload = run_cli("wave-prepare", self.root, "ghost-task")
        self.assertEqual(code, 1)
        self.assertIn("不存在", payload["error"])
        # 决策未批准（EXHAUSTED）→ 1，报文含 quota 口径
        code, payload = run_cli("wave-prepare", self.root, TID, "EXHAUSTED")
        self.assertEqual(code, 1)
        self.assertIn("quota EXHAUSTED", payload["error"])
        # 参数值非法 → 2
        code, payload = run_cli("wave-prepare", self.root, TID, "MEGA")
        self.assertEqual(code, 2)
        code, payload = run_cli("wave-prepare", self.root, TID,
                                "AVAILABLE", "abc")
        self.assertEqual(code, 2)
        # 参数个数错误 → 2
        code, payload = run_cli("wave-prepare", self.root)
        self.assertEqual(code, 2)

    def test_wave_show_all_and_specific(self):
        # 显式 AVAILABLE 直通（wu-21-15：缺省改走 resolver，零网络纪律）
        code, prepared = run_cli("wave-prepare", self.root, TID, "AVAILABLE")
        self.assertEqual(code, 0)
        wave_id = prepared["wave_id"]
        # 全量：waves 数组
        code, payload = run_cli("wave-show", self.root, TID)
        self.assertEqual(code, 0)
        self.assertEqual(payload["task_id"], TID)
        self.assertEqual(len(payload["waves"]), 1)
        self.assertEqual(payload["waves"][0]["wave_id"], wave_id)
        # 指定 wave_id：原样 wave 记录
        code, payload = run_cli("wave-show", self.root, TID, wave_id)
        self.assertEqual(code, 0)
        self.assertEqual(payload["wave_id"], wave_id)
        self.assertEqual(payload["units"], ["u1", "u2"])
        self.assertEqual(payload["status"], "active")

    def test_wave_show_exit_codes(self):
        # 任务不存在 → 1
        code, payload = run_cli("wave-show", self.root, "ghost-task")
        self.assertEqual(code, 1)
        # 未知 wave_id → 1
        code, payload = run_cli("wave-show", self.root, TID,
                                "wave-999999999999")
        self.assertEqual(code, 1)
        self.assertIn("wave-999999999999", payload["error"])
        # 无 waves 记录的任务 → 空数组仍 0
        code, payload = run_cli("wave-show", self.root, TID)
        self.assertEqual((code, payload["waves"]), (0, []))
        # 参数个数错误 → 2
        code, payload = run_cli("wave-show", self.root)
        self.assertEqual(code, 2)

    def test_real_subprocess_utf8_smoke(self):
        # 真实子进程路径（显式 encoding="utf-8"，CI Windows 教训）
        proc = subprocess.run(
            [sys.executable, str(CLI_PATH), "wave-show", self.root, TID],
            text=True, capture_output=True, encoding="utf-8",
            errors="replace")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout.strip())
        self.assertEqual(payload["waves"], [])


if __name__ == "__main__":
    unittest.main()
