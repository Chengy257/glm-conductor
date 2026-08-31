#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v2.1 §11.5/§12 有界并行激活测试（wu-21-09）。

仅 Python 3 标准库（unittest + tempfile + subprocess git fixture），
零第三方依赖；state.json / leases.json / events.jsonl 全部落盘在
tempfile 提供并 git init 的 scratch 任务目录（纪律：RB-1 与指纹依赖
git），绝不触碰仓库内 .glm-conductor/ 真实账本。本文件只走
prepare / wave 面的 I/O（无 finish 完成门），git 仅作 scratch 目录
合规初始化。

覆盖（wu-21-09 规格测试清单 7 项）：
    1. §12 预算矩阵（policy max_workers=4 任务）：AVAILABLE→预算 4 /
       PRESSURE→1 / UNKNOWN→1 / EXHAUSTED→0 且单元转 waiting_quota
       口径拒绝（dispatcher 不再整批挂起 UNKNOWN/PRESSURE，预算收缩
       全部由 task_manager._effective_worker_cap 折算）；
    2. 并发默认 2：new_task_state 的 dispatch.max_workers==2；
       plan_dispatch 不传 max_workers 时 DEFAULT_MAX_WORKERS==2 生效；
    3. 显式 max_workers 的 min 语义：显式 1 收紧；显式 3 在 policy
       max=2 下被压回 2；
    4. UNKNOWN 下双就绪 disjoint 单元：只批 1 个（预算 1），另一个
       仍在候选（deferred 记 concurrency）——不再 unknown_suppressed
       挂起，waiting_quota 清单为空；
    5. PRESSURE 下同上（预算 1，不再 pressure_suppressed）；
    6. prepare_dispatch_wave 在 UNKNOWN 下：wave 只含 1 单元、
       worker_budget=1（wave 记录与 journal 事件同步）；
    7. dispatch_prepared 事件含 effective_max_workers（wave 事件已有
       worker_budget，不加重复键）。

运行：
    cd <repo_root> && python3 -m unittest tests.test_parallel_activation -v
"""

import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "glm-conductor"))
from runtime import dispatcher, execution_policy, journal, lease, state
from runtime import task_manager, work_unit

TID = "par-task-1a2b3c"
VERIFY_CMD = "python3 -m unittest tests.test_parallel_activation"
ROUTE = {"mode": "delegate", "delegability": "high",
         "assurance": "standard", "executor": "flash-implementer",
         "continuity": "foreground"}
CONFIRMED_AT = "2026-08-31T00:00:00.000Z"


def run_git(repo, *args):
    """在 scratch 目录里执行 git 子命令（测试装置专用，失败即断言错误）。"""
    proc = subprocess.run(
        ["git"] + list(args), cwd=str(repo),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        raise AssertionError(
            "测试装置 git %s 失败（returncode=%d）：%s"
            % (" ".join(args), proc.returncode,
               proc.stderr.decode("utf-8", errors="replace")))
    return proc.stdout.decode("utf-8", errors="replace")


class GitTaskFixture(unittest.TestCase):
    """git init 的 tempfile scratch 任务目录（每用例独立 repo_root）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        # 注册在基类 cleanup 之后（LIFO：先解除 .git 只读位再删目录）
        self.addCleanup(self._force_cleanup)
        self.root = self._tmp.name
        run_git(self.root, "init")
        run_git(self.root, "config", "user.email", "par@example.com")
        run_git(self.root, "config", "user.name", "Parallel Activation")
        run_git(self.root, "config", "core.autocrlf", "false")

    def _force_cleanup(self):
        """解除 git 只读对象后清理临时目录（Windows 上 git 松散对象
        文件带只读属性，TemporaryDirectory.cleanup 会 PermissionError；
        模式对齐 tests/test_task_manager.py 的 GitRepoFixture）。"""
        git_dir = os.path.join(self.root, ".git")
        if os.path.isdir(git_dir):
            for dirpath, _dirnames, filenames in os.walk(git_dir):
                for name in filenames:
                    try:
                        os.chmod(os.path.join(dirpath, name), stat.S_IWRITE)
                    except OSError:
                        pass
        self._tmp.cleanup()


def wu(uid, owned):
    """构造 §61 形状的 ready 单元（真实 new_work_unit；默认带验证命令
    ——validate_state 拒绝无验证命令的单元，空壳落不了盘）。"""
    return work_unit.new_work_unit(
        uid, "并行激活目标 %s" % uid, executor="flash-implementer",
        ownership=list(owned), verification=[VERIFY_CMD], status="ready")


def make_task(root, units, *, policy_max_workers=None, active=()):
    """落盘一个 scratch 任务（new_task_state 真实构造 + save）。

    policy_max_workers 给定时经 set_parallel_authorization 抬升
    parallelism.max_workers（>2 须 source="user"，validate_state
    授权不变量才放行）；缺省保持默认块（max_workers=2）。"""
    st = state.new_task_state(
        TID, "有界并行激活测试目标", dict(ROUTE),
        ownership_files=("src/**",),
        verification_required=(VERIFY_CMD,), status="executing")
    st["work_units"] = list(units)
    st["dispatch"] = {"max_workers": 2, "active": list(active)}
    if policy_max_workers is not None:
        st["execution_policy"] = execution_policy.set_parallel_authorization(
            st["execution_policy"], max_workers=policy_max_workers,
            source="user", confirmed_at=CONFIRMED_AT)
    state.save_state(root, st)
    return st


def events(root, name):
    """读取真实 journal 并过滤事件名。"""
    return [e for e in journal.read_events(root, TID)
            if e.get("event") == name]


def deferred_ids(plan):
    """决策 dict 的 (id, reason) 对列表（断言辅助）。"""
    return [(entry["id"], entry["reason"]) for entry in plan["deferred"]]


# —— 1. §12 预算矩阵（policy max_workers=4 任务） ——

class QuotaBudgetMatrixTest(GitTaskFixture):
    """quota 四态 → worker 预算的 §12 表端到端：AVAILABLE→task cap /
    PRESSURE→1 / UNKNOWN→1 / EXHAUSTED→0+waiting_quota。

    dispatcher 自身不再解释 quota（unknown_suppressed /
    pressure_suppressed / small_only 分支已删除）——矩阵由
    task_manager._effective_worker_cap 经
    execution_policy.effective_worker_budget 折算后传入 max_workers。
    """

    def _policy4_task(self):
        return make_task(self.root, [wu("u1", ("src/a/**",)),
                                     wu("u2", ("src/b/**",))],
                         policy_max_workers=4)

    def test_available_budget_equals_task_cap_four(self):
        self._policy4_task()
        plan = task_manager.prepare_dispatch(self.root, TID, "u1", quota_status="AVAILABLE")
        self.assertEqual(plan["max_workers"], 4)
        self.assertEqual(sorted(plan["dispatch"]), ["u1", "u2"])
        self.assertEqual(plan["deferred"], [])
        prepared = events(self.root, "dispatch_prepared")[0]
        self.assertEqual(prepared["effective_max_workers"], 4)

    def test_pressure_budget_one(self):
        self._policy4_task()
        plan = task_manager.prepare_dispatch(self.root, TID, "u1",
                                             quota_status="PRESSURE")
        self.assertEqual(plan["max_workers"], 1)
        self.assertEqual(plan["dispatch"], ["u1"])
        self.assertNotIn("pressure_suppressed",
                         [r for _, r in deferred_ids(plan)])
        prepared = events(self.root, "dispatch_prepared")[0]
        self.assertEqual(prepared["effective_max_workers"], 1)

    def test_unknown_budget_one(self):
        self._policy4_task()
        plan = task_manager.prepare_dispatch(self.root, TID, "u1",
                                             quota_status="UNKNOWN")
        self.assertEqual(plan["max_workers"], 1)
        self.assertEqual(plan["dispatch"], ["u1"])
        self.assertNotIn("unknown_suppressed",
                         [r for _, r in deferred_ids(plan)])
        prepared = events(self.root, "dispatch_prepared")[0]
        self.assertEqual(prepared["effective_max_workers"], 1)

    def test_exhausted_budget_zero_waits_quota(self):
        self._policy4_task()
        # 预算 0 的可观察面：nothing dispatchable，单元转 waiting_quota
        # 口径拒绝（§12 表 EXHAUSTED→0，事实源 effective_worker_budget）
        policy = state.load_state(self.root, TID)["execution_policy"]
        self.assertEqual(
            execution_policy.effective_worker_budget(policy, "EXHAUSTED"),
            0)
        with self.assertRaises(task_manager.TaskManagerError) as ctx:
            task_manager.prepare_dispatch(self.root, TID, "u1",
                                          quota_status="EXHAUSTED")
        self.assertIn("quota EXHAUSTED", str(ctx.exception))
        self.assertEqual(lease.lease_state(self.root, TID), {})
        self.assertEqual(events(self.root, "dispatch_prepared"), [])


# —— 2. 并发默认 2（三处口径一致） ——

class DefaultTwoWorkersTest(GitTaskFixture):

    def test_new_task_state_defaults_dispatch_max_workers_two(self):
        st = state.new_task_state(TID, "目标", dict(ROUTE))
        self.assertEqual(st["dispatch"]["max_workers"], 2)
        self.assertEqual(st["execution_policy"]["parallelism"]
                         ["max_workers"], 2)
        self.assertEqual(dispatcher.DEFAULT_MAX_WORKERS, 2)

    def test_plan_dispatch_without_param_uses_default_two(self):
        # 双就绪 disjoint 单元：缺省预算 2 → 双派发（v2.0.1 时代缺省 1
        # 只能派 1 个）
        units = [wu("u1", ("src/a/**",)), wu("u2", ("src/b/**",))]
        plan = dispatcher.plan_dispatch(units)
        self.assertEqual(plan["max_workers"], 2)
        self.assertEqual(plan["dispatch"], ["u1", "u2"])
        self.assertEqual(plan["deferred"], [])


# —— 3. 显式 max_workers 的 min 语义 ——

class ExplicitCapMinSemanticsTest(GitTaskFixture):

    def test_explicit_one_tightens_below_policy_cap(self):
        make_task(self.root, [wu("u1", ("src/a/**",)),
                              wu("u2", ("src/b/**",))],
                  policy_max_workers=4)
        plan = task_manager.prepare_dispatch(self.root, TID, "u1",
                                             max_workers=1, quota_status="AVAILABLE")
        self.assertEqual(plan["max_workers"], 1)
        self.assertEqual(plan["dispatch"], ["u1"])
        self.assertEqual(deferred_ids(plan),
                         [("u2", "concurrency")])

    def test_explicit_three_capped_by_policy_two(self):
        # policy 默认块 max=2：显式 3 被压回 2（min 语义）
        make_task(self.root, [wu("u1", ("src/a/**",)),
                              wu("u2", ("src/b/**",))])
        plan = task_manager.prepare_dispatch(self.root, TID, "u1",
                                             max_workers=3, quota_status="AVAILABLE")
        self.assertEqual(plan["max_workers"], 2)
        self.assertEqual(sorted(plan["dispatch"]), ["u1", "u2"])


# —— 4/5. UNKNOWN / PRESSURE：预算 1，挂起清单为空 ——

class QuotaShrunkBudgetSingleSlotTest(GitTaskFixture):
    """双就绪 disjoint 单元 + 预算收缩为 1：只批 topo 头一个，另一个
    仍是候选（deferred 记 concurrency）——quota 状态不再产生挂起理由，
    waiting_quota 清单恒空（图词汇不腐化的旧目标由转 waiting_quota 的
    EXHAUSTED 边独享）。"""

    def _prepare_first(self, quota_status):
        make_task(self.root, [wu("u1", ("src/a/**",)),
                              wu("u2", ("src/b/**",))])
        return task_manager.prepare_dispatch(self.root, TID, "u1",
                                             quota_status=quota_status)

    def test_unknown_single_slot_other_stays_candidate(self):
        plan = self._prepare_first("UNKNOWN")
        self.assertEqual(plan["dispatch"], ["u1"])
        self.assertEqual(deferred_ids(plan), [("u2", "concurrency")])
        self.assertEqual(plan["waiting_quota"], [])

    def test_pressure_single_slot_other_stays_candidate(self):
        plan = self._prepare_first("PRESSURE")
        self.assertEqual(plan["dispatch"], ["u1"])
        self.assertEqual(deferred_ids(plan), [("u2", "concurrency")])
        self.assertEqual(plan["waiting_quota"], [])


# —— 6. wave 在 UNKNOWN 下：1 单元 + worker_budget 1 ——

class WaveUnderUnknownTest(GitTaskFixture):

    def test_wave_batches_one_unit_with_budget_one(self):
        make_task(self.root, [wu("u1", ("src/a/**",)),
                              wu("u2", ("src/b/**",))])
        result = task_manager.prepare_dispatch_wave(
            self.root, TID, quota_status="UNKNOWN")
        self.assertEqual(result["units"], ["u1"])
        self.assertEqual(result["worker_budget"], 1)
        self.assertEqual(result["deferred"],
                         [{"id": "u2", "reason": "concurrency"}])
        self.assertEqual(result["waiting_quota"], [])
        # wave 记录与 journal 事件同步（worker_budget 即 quota 调节后值）
        st = state.load_state(self.root, TID)
        wave = st["dispatch"]["waves"][0]
        self.assertEqual(wave["units"], ["u1"])
        self.assertEqual(wave["worker_budget"], 1)
        prepared = events(self.root, "dispatch_wave_prepared")[0]
        self.assertEqual(prepared["worker_budget"], 1)
        # 只有被批成员持有租约
        self.assertEqual(set(lease.lease_state(self.root, TID)),
                         {"src/a/**"})


# —— 7. dispatch_prepared 事件含 effective_max_workers ——

class EffectiveMaxWorkersEventTest(GitTaskFixture):
    """prepare_dispatch 的 dispatch_prepared 事件新增
    effective_max_workers（§12 折算后的有效预算）；wave 事件已有
    worker_budget，不加重复键。"""

    def test_prepared_event_carries_effective_budget(self):
        make_task(self.root, [wu("u1", ("src/a/**",))])
        task_manager.prepare_dispatch(self.root, TID, "u1", quota_status="AVAILABLE")
        prepared = events(self.root, "dispatch_prepared")[0]
        self.assertEqual(prepared["effective_max_workers"], 2)  # 默认块

    def test_prepared_event_carries_shrunk_budget_under_pressure(self):
        make_task(self.root, [wu("u1", ("src/a/**",))])
        task_manager.prepare_dispatch(self.root, TID, "u1",
                                      quota_status="PRESSURE")
        prepared = events(self.root, "dispatch_prepared")[0]
        self.assertEqual(prepared["effective_max_workers"], 1)

    def test_wave_prepared_event_has_no_duplicate_budget_key(self):
        make_task(self.root, [wu("u1", ("src/a/**",))])
        task_manager.prepare_dispatch_wave(self.root, TID, quota_status="AVAILABLE")
        prepared = events(self.root, "dispatch_wave_prepared")[0]
        self.assertEqual(prepared["worker_budget"], 1)
        self.assertNotIn("effective_max_workers", prepared)
        self.assertEqual(events(self.root, "dispatch_prepared"), [])


if __name__ == "__main__":
    unittest.main()
