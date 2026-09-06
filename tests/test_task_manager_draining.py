#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""task_manager DRAINING 派发闸测试（v2.2 M4，wu-22-04；决策记录 D6/D7/D13）。

仅 Python 3 标准库（unittest + tempfile），零第三方依赖、零网络；
state.json / events.jsonl / quota-cache.json 全部落盘在
tempfile.TemporaryDirectory 提供的临时目录（真实 new_task_state +
new_work_unit 构造，非 mock state），不污染真实工作区。所有用例显式传
quota_status="AVAILABLE"（provider 维度直通，零 resolver 零网络），
execution phase 闸只读本地 quota-cache.json——正是 v2.1 误判场景的
复现面：provider 报 AVAILABLE 而窗口剩余已跌入排水带。

覆盖（规格验证清单）：
    - DRAINING 缓存硬拒新实施单元（prepare_dispatch）：TaskManagerError
      消息以 "execution phase" 开头、含 DRAINING/驱动窗口剩余/收尾
      白名单/reset-wake 建议；零租约、零 permit、零 dispatch_prepared；
      单元保持 ready；state.quota.execution_phase 回填 DRAINING（拒绝
      路径先落回填再 raise，D13）；
    - DRAINING 缓存硬拒新 wave（prepare_dispatch_wave）：零 wave 记录、
      零 permit、零租约、零 dispatch_wave_prepared + D13 回填；
    - BLOCKED 缓存（remaining==0）双入口硬拒；
    - PRESSURE 相预算收缩：wave worker_budget == 1（与既有 §12 折算
      合流为单一预算源）、单单元 plan max_workers == 1、放行 + 回填
      PRESSURE；
    - NORMAL 相照旧：单单元 max_workers == 2（policy 默认）、wave
      worker_budget == 2、放行 + 回填 NORMAL；
    - fail-open 先于闸门：无缓存 / 坏 JSON / status 词汇陈旧（不在
      QUOTA_STATUSES）/ fetched_at 缺失 → 闸不激活，放行且 state.json
      字节零改动（绝不因缺缓存阻塞或降级既有派发）；
    - 白名单不加闸（D6）：DRAINING 缓存下 running 单元不强杀——
      commit_dispatch / finish_unit 零接触照常收尾。

运行：
    cd <repo_root> && python3 -m unittest tests.test_task_manager_draining -v
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "glm-conductor"))
from runtime import dispatch_wave
from runtime import journal
from runtime import lease
from runtime import state
from runtime import task_manager
from runtime import work_unit
from runtime.quota import resolver

TID = "draining-task-1a2b3c"
VERIFY_CMD = "python3 -m unittest tests.test_task_manager_draining"
RESET_AT = "2026-09-02T12:00:00Z"
FETCHED_AT = "2026-09-02T10:00:00.000Z"
# delegate 路由（矩阵合法：delegability high + assurance standard）
ROUTE = {"mode": "delegate", "delegability": "high",
         "assurance": "standard", "executor": "flash-implementer",
         "continuity": "foreground"}


# —— 测试夹具 ——

def wu(uid, owned=("src/a/**",), deps=(), status="ready"):
    """构造 §61 形状的 work unit dict（new_work_unit 真实构造，带验证
    命令——validate_state 拒绝「无验证命令的单元」）。"""
    return work_unit.new_work_unit(
        uid, "目标 %s" % uid, executor="flash-implementer",
        ownership=list(owned), verification=[VERIFY_CMD],
        depends_on=list(deps), status=status)


def make_task(root, units, *, status="decomposed", max_workers=2,
              active=()):
    """在临时目录真实落盘一个任务 state（new_task_state 构造 + save；
    state 恒含 execution_policy 默认块 parallelism.max_workers=2）。"""
    st = state.new_task_state(
        TID, "DRAINING 派发闸测试目标", dict(ROUTE),
        ownership_files=("src/**",),
        verification_required=(VERIFY_CMD,),
        status=status)
    st["work_units"] = list(units)
    st["dispatch"] = {"max_workers": max_workers, "active": list(active)}
    state.save_state(root, st)
    return st


def window(kind="five_hour", remaining=18.0, reset_at=RESET_AT):
    """§27 snapshot 的单窗形状（control 消费 kind / remaining_percent /
    reset_at 三键）。"""
    return {"kind": kind, "remaining_percent": remaining,
            "reset_at": reset_at}


def write_cache(root, *, status="AVAILABLE", windows=None,
                fetched_at=FETCHED_AT, raw=None):
    """按 resolver 缓存布局落盘 quota-cache.json（{"provider",
    "fetched_at", "snapshot", "status"}）；raw 非 None 时直接写原始
    字节（坏 JSON 用例）。返回缓存路径。"""
    directory = os.path.join(root, ".glm-conductor")
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, resolver.CACHE_FILE_NAME)
    if raw is not None:
        with open(path, "wb") as fh:
            fh.write(raw)
        return path
    payload = {
        "provider": "zai",
        "fetched_at": fetched_at,
        "status": status,
        "snapshot": {"windows": list(windows or [])},
    }
    # UTF-8 无 BOM、固定 \n 换行（Windows 纪律同 resolver._save_cache）
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=True)
    return path


def state_bytes(root):
    """读取 state.json 原始字节（零写入断言用）。"""
    with open(state.state_path(root, TID), "rb") as fh:
        return fh.read()


def events(root, name):
    """读取真实 journal 并过滤事件名。"""
    return [e for e in journal.read_events(root, TID)
            if e.get("event") == name]


def waves_of(root):
    """容错读取盘上 dispatch.waves。"""
    st = state.load_state(root, TID)
    dispatch_block = st.get("dispatch")
    waves = (dispatch_block.get("waves")
             if isinstance(dispatch_block, dict) else None)
    return waves if isinstance(waves, list) else []


def st_quota_phase(root):
    """容错读取盘上 state.quota.execution_phase（缺块返回 None）。"""
    st = state.load_state(root, TID)
    quota_block = st.get("quota")
    if not isinstance(quota_block, dict):
        return None
    return quota_block.get("execution_phase")


class GateTestBase(unittest.TestCase):
    """临时目录夹具：每个用例独立的 repo_root（无 git 需求）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = self._tmp.name


# —— DRAINING / BLOCKED 硬拒（D6）：两入口各一 + 消息口径 + 零残留 ——

class DrainRejectTest(GateTestBase):

    def test_draining_cache_rejects_prepare_dispatch(self):
        make_task(self.root, [wu("u1", ("src/a/**",))])
        write_cache(self.root, status="AVAILABLE",
                    windows=[window(remaining=18)])
        with self.assertRaises(task_manager.TaskManagerError) as ctx:
            task_manager.prepare_dispatch(self.root, TID, "u1",
                                          quota_status="AVAILABLE")
        msg = str(ctx.exception)
        # 新错误口径：以 "execution phase" 开头（供测试与 M5 区分）
        self.assertTrue(msg.startswith("execution phase"), msg)
        self.assertIn("DRAINING", msg)
        # 驱动窗口剩余与排水线（决策器 reason 内含）
        self.assertIn("剩余 18%", msg)
        self.assertIn("20%", msg)
        # §18.1 收尾白名单 + reset/wake 建议
        self.assertIn("join/verify/review/checkpoint/wake", msg)
        self.assertIn("wake bridge", msg)
        self.assertIn("重置", msg)
        # 零残留：无租约、无 permit、无 prepared 事件、单元保持 ready
        self.assertEqual(lease.lease_state(self.root, TID), {})
        self.assertEqual(dispatch_wave.list_permits(self.root, TID), [])
        self.assertEqual(events(self.root, "dispatch_prepared"), [])
        st = state.load_state(self.root, TID)
        self.assertEqual(
            next(u for u in st["work_units"] if u["id"] == "u1")["status"],
            "ready")
        # D13 回填：拒绝路径也先落回填再 raise（prepare 时点写入）
        self.assertEqual(st["quota"]["execution_phase"], "DRAINING")

    def test_draining_cache_rejects_prepare_dispatch_wave(self):
        make_task(self.root, [wu("u1", ("src/a/**",)),
                              wu("u2", ("src/b/**",))])
        write_cache(self.root, status="AVAILABLE",
                    windows=[window(remaining=12)])
        with self.assertRaises(task_manager.TaskManagerError) as ctx:
            task_manager.prepare_dispatch_wave(self.root, TID,
                                               quota_status="AVAILABLE")
        msg = str(ctx.exception)
        self.assertTrue(msg.startswith("execution phase"), msg)
        self.assertIn("DRAINING", msg)
        self.assertIn("剩余 12%", msg)
        # 零残留：无 wave 记录、无 permit、无租约、无 prepared 事件
        self.assertEqual(waves_of(self.root), [])
        self.assertEqual(dispatch_wave.list_permits(self.root, TID), [])
        self.assertEqual(lease.lease_state(self.root, TID), {})
        self.assertEqual(
            events(self.root, "dispatch_wave_prepared"), [])
        # D13 回填落盘
        st = state.load_state(self.root, TID)
        self.assertEqual(st["quota"]["execution_phase"], "DRAINING")

    def test_blocked_cache_rejects_prepare_dispatch(self):
        make_task(self.root, [wu("u1", ("src/a/**",))])
        # remaining == 0 → D7 第 1 条 BLOCKED（provider 仍报 AVAILABLE）
        write_cache(self.root, status="AVAILABLE",
                    windows=[window(remaining=0)])
        with self.assertRaises(task_manager.TaskManagerError) as ctx:
            task_manager.prepare_dispatch(self.root, TID, "u1",
                                          quota_status="AVAILABLE")
        msg = str(ctx.exception)
        self.assertTrue(msg.startswith("execution phase"), msg)
        self.assertIn("BLOCKED", msg)
        self.assertEqual(lease.lease_state(self.root, TID), {})
        self.assertEqual(st_quota_phase(self.root), "BLOCKED")

    def test_blocked_cache_rejects_prepare_dispatch_wave(self):
        make_task(self.root, [wu("u1", ("src/a/**",))])
        write_cache(self.root, status="EXHAUSTED",
                    windows=[window(remaining=5)])
        # provider EXHAUSTED → D7 第 1 条 BLOCKED（仅存低剩余窗）
        with self.assertRaises(task_manager.TaskManagerError) as ctx:
            task_manager.prepare_dispatch_wave(self.root, TID,
                                               quota_status="AVAILABLE")
        msg = str(ctx.exception)
        self.assertTrue(msg.startswith("execution phase"), msg)
        self.assertIn("BLOCKED", msg)
        self.assertEqual(waves_of(self.root), [])
        self.assertEqual(lease.lease_state(self.root, TID), {})
        self.assertEqual(st_quota_phase(self.root), "BLOCKED")


# —— 放行路径的 D13 回填（state.quota.execution_phase，prepare 时点） ——

class BackfillTest(GateTestBase):

    def test_normal_allow_path_backfills_execution_phase(self):
        make_task(self.root, [wu("u1", ("src/a/**",))])
        write_cache(self.root, status="AVAILABLE",
                    windows=[window(remaining=80)])
        task_manager.prepare_dispatch(self.root, TID, "u1",
                                      quota_status="AVAILABLE")
        # 放行路径同样回填（D13：prepare 时点写入，供 manifest 与
        # SessionStart 展示）
        self.assertEqual(st_quota_phase(self.root), "NORMAL")
        st = state.load_state(self.root, TID)
        self.assertEqual(
            next(u for u in st["work_units"] if u["id"] == "u1")["status"],
            "ready")

    def test_pressure_allow_path_backfills_execution_phase(self):
        make_task(self.root, [wu("u1", ("src/a/**",))])
        write_cache(self.root, status="AVAILABLE",
                    windows=[window(remaining=30)])
        task_manager.prepare_dispatch(self.root, TID, "u1",
                                      quota_status="AVAILABLE")
        self.assertEqual(st_quota_phase(self.root), "PRESSURE")

    def test_backfill_preserves_existing_quota_block_keys(self):
        make_task(self.root, [wu("u1", ("src/a/**",))])
        write_cache(self.root, status="AVAILABLE",
                    windows=[window(remaining=80)])
        # 预置 observer 形状的 quota 块：回填只增写 execution_phase，
        # 既有键原样保留（validator 对 quota 未知键宽松）
        st = state.load_state(self.root, TID)
        st["quota"] = {"status": "AVAILABLE", "provider": "zai",
                       "last_checked": FETCHED_AT}
        state.save_state(self.root, st)
        task_manager.prepare_dispatch(self.root, TID, "u1",
                                      quota_status="AVAILABLE")
        st = state.load_state(self.root, TID)
        self.assertEqual(st["quota"]["status"], "AVAILABLE")
        self.assertEqual(st["quota"]["provider"], "zai")
        self.assertEqual(st["quota"]["execution_phase"], "NORMAL")


# —— PRESSURE 预算收缩 1 / NORMAL 照旧（与既有 §12 折算合流） ——

class BudgetFoldTest(GateTestBase):

    def test_pressure_wave_worker_budget_shrinks_to_one(self):
        make_task(self.root, [wu("u1", ("src/a/**",)),
                              wu("u2", ("src/b/**",))])
        # 20 < 30 <= 35 → PRESSURE（execution 相语义，provider AVAILABLE）
        write_cache(self.root, status="AVAILABLE",
                    windows=[window(remaining=30)])
        result = task_manager.prepare_dispatch_wave(self.root, TID,
                                                    quota_status="AVAILABLE")
        # PRESSURE 不阻塞派发，只收缩并发预算：eff=1 时 plan 批准集为
        # 1 单元（与既有 §12 PRESSURE→1 折算的可观察面一致）
        self.assertEqual(result["units"], ["u1"])
        self.assertEqual(len(result["permits"]), 1)
        # worker_budget 收缩为 1（与既有 PRESSURE→1 折算路径合流，单一
        # 预算源：wave 记录与事件同值，不双写）
        self.assertEqual(result["worker_budget"], 1)
        self.assertEqual(waves_of(self.root)[0]["worker_budget"], 1)
        record = events(self.root, "dispatch_wave_prepared")[0]
        self.assertEqual(record["worker_budget"], 1)

    def test_pressure_single_unit_plan_budget_is_one(self):
        make_task(self.root, [wu("u1", ("src/a/**",))])
        write_cache(self.root, status="AVAILABLE",
                    windows=[window(remaining=30)])
        plan = task_manager.prepare_dispatch(self.root, TID, "u1",
                                             quota_status="AVAILABLE")
        self.assertEqual(plan["dispatch"], ["u1"])
        # phase 预算 1 并入单一 min 折算：eff = min(policy 2, provider 2,
        # phase 1) = 1
        self.assertEqual(plan["max_workers"], 1)

    def test_normal_keeps_policy_budget(self):
        make_task(self.root, [wu("u1", ("src/a/**",)),
                              wu("u2", ("src/b/**",))])
        write_cache(self.root, status="AVAILABLE",
                    windows=[window(remaining=80)])
        # NORMAL 相 dispatch_budget = cap_base，折算无变化
        plan = task_manager.prepare_dispatch(self.root, TID, "u1",
                                             quota_status="AVAILABLE")
        self.assertEqual(plan["max_workers"], 2)
        result = task_manager.prepare_dispatch_wave(self.root, TID,
                                                    quota_status="AVAILABLE")
        self.assertEqual(result["worker_budget"], 2)


# —— fail-open 先于闸门：无缓存 / 坏缓存绝不阻塞、绝不写 state ——

class FailOpenTest(GateTestBase):

    def test_missing_cache_fail_open_allows_and_leaves_state_untouched(self):
        make_task(self.root, [wu("u1", ("src/a/**",))])
        before = state_bytes(self.root)
        plan = task_manager.prepare_dispatch(self.root, TID, "u1",
                                             quota_status="AVAILABLE")
        self.assertEqual(plan["dispatch"], ["u1"])
        self.assertEqual(plan["max_workers"], 2)  # 既有 §12 折算不变
        # 闸未激活：state.json 字节零改动、无 quota.execution_phase 回填
        self.assertEqual(state_bytes(self.root), before)
        self.assertIsNone(st_quota_phase(self.root))
        # wave 入口同口径放行
        result = task_manager.prepare_dispatch_wave(self.root, TID,
                                                    quota_status="AVAILABLE")
        self.assertEqual(result["units"], ["u1"])
        self.assertEqual(result["worker_budget"], 1)  # min(policy 2, 1 单元)

    def test_corrupt_cache_json_fail_open(self):
        make_task(self.root, [wu("u1", ("src/a/**",))])
        write_cache(self.root, raw=b"{not-json!!")
        before = state_bytes(self.root)
        plan = task_manager.prepare_dispatch(self.root, TID, "u1",
                                             quota_status="AVAILABLE")
        self.assertEqual(plan["dispatch"], ["u1"])
        self.assertEqual(state_bytes(self.root), before)
        self.assertIsNone(st_quota_phase(self.root))

    def test_stale_status_vocabulary_tolerated_fail_open(self):
        make_task(self.root, [wu("u1", ("src/a/**",))])
        # status 词汇陈旧（不在 QUOTA_STATUSES 四态内）→ 视为无缓存
        write_cache(self.root, status="WARPED",
                    windows=[window(remaining=5)])
        before = state_bytes(self.root)
        plan = task_manager.prepare_dispatch(self.root, TID, "u1",
                                             quota_status="AVAILABLE")
        self.assertEqual(plan["dispatch"], ["u1"])
        self.assertEqual(state_bytes(self.root), before)
        self.assertIsNone(st_quota_phase(self.root))

    def test_missing_fetched_at_tolerated_fail_open(self):
        make_task(self.root, [wu("u1", ("src/a/**",))])
        # fetched_at 不可解析 → _load_cache 视为无缓存 → fail-open
        write_cache(self.root, status="DRAINING",
                    windows=[window(remaining=10)], fetched_at="not-a-time")
        plan = task_manager.prepare_dispatch(self.root, TID, "u1",
                                             quota_status="AVAILABLE")
        self.assertEqual(plan["dispatch"], ["u1"])
        self.assertIsNone(st_quota_phase(self.root))


# —— D6 白名单：running 单元不强杀，commit / finish 路径零接触 ——

class WhitelistTest(GateTestBase):

    def test_running_unit_can_commit_and_finish_under_draining_cache(self):
        make_task(self.root, [wu("u1", ("src/a/**",))])
        write_cache(self.root, status="AVAILABLE",
                    windows=[window(remaining=80)])
        task_manager.prepare_dispatch(self.root, TID, "u1",
                                      quota_status="AVAILABLE")
        # 单元已派发后再写入 DRAINING 缓存：running 单元不强杀
        write_cache(self.root, status="AVAILABLE",
                    windows=[window(remaining=5)])
        st = task_manager.commit_dispatch(self.root, TID, "u1")
        self.assertEqual(
            next(u for u in st["work_units"] if u["id"] == "u1")["status"],
            "running")
        # finish（failed 收尾不需要证据）不受闸限制
        st = task_manager.finish_unit(self.root, TID, "u1",
                                      outcome="failed")
        self.assertEqual(
            next(u for u in st["work_units"] if u["id"] == "u1")["status"],
            "failed")
        self.assertEqual(events(self.root, "unit_finished")[0]["outcome"],
                         "failed")


if __name__ == "__main__":
    unittest.main()
