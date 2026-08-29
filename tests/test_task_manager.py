#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""runtime.task_manager 单元测试（v2.0.1 加固工作包 H4，审查项 P1-3）。

仅 Python 3 标准库（unittest + tempfile），零第三方依赖、零 git 需求；
state.json / leases.json / events.jsonl 全部落盘在
tempfile.TemporaryDirectory 提供的临时目录（真实 new_task_state +
new_work_unit 构造，非 mock state），不污染真实工作区。

覆盖：
    - prepare_dispatch happy path：决策快照返回（dispatch 组含 uid）/
      租约落盘（owner==uid）/ dispatch_prepared 事件（leased=归一路径
      排序）/ 单元状态与 state.json 字节不被改动（无 state 副作用）/
      反斜杠 ownership 归一 / 同 owner 重复 prepare 幂等 /
      max_workers 缺省取 dispatch.max_workers（显式传参覆盖）；
    - prepare 失败：单元缺失 / 单元非 ready（消息含当前状态）/ plan
      落选 quota EXHAUSTED（消息含 quota EXHAUSTED）/ plan 落选
      deferred（消息含 reason）/ 依赖未满足未进候选（fallback 理由）/
      落选零副作用（不写租约不写事件）/ 非法 quota_status ValueError
      透传；
    - commit happy：ready→running / active 记账 / 任务级 created/
      decomposed→executing 直达边（executing、joining 不被翻动）/
      落盘读回一致 / implementation_started 事件（含 executor）/
      active 追加幂等防御；
    - commit 失败：未 prepare 无租约（消息提示 prepare_dispatch）/
      租约丢失（消息提示 abort_dispatch 重备）/ 异主租约 / 重复
      commit（消息含 running）；
    - abort：running 拒绝（不得静默回退，租约未被动）/ happy（释放
      租约 + dispatch_aborted 事件 + state.json 零写入）/ active 残留
      清理（移除 + save）；
    - finish：四条转换边（running→completed 双跳经 verifying——以
      transition spy 断言恰好两次表内转换、running→failed、
      verifying→completed、verifying→cancelled）+ 租约释放 + active
      移除 + unit_finished 事件（含 outcome）/ 非法 outcome
      ValueError（校验先于 I/O）/ 状态不符 TaskManagerError / 单元
      缺失；
    - 崩溃窗口模拟两条：prepare 后直接 commit 成功（事件序
      dispatch_prepared→implementation_started、running + active +
      租约在位）；prepare 后 abort 释放（租约清空、单元仍 ready、
      可重新 prepare）；
    - 事件词汇：RECOMMENDED_EVENTS 三条新增事件名 + 插入位置
      （implementation_started 之后 / checkpoint_written 之前），
      真实 journal 行由各 API 用例断言；
    - H5（租约崩溃恢复）：prepare 默认 TTL 落盘（expires_at/generation/
      heartbeat_at/session_id + dispatch_prepared 的 ttl_seconds 字段）/
      recover_leases（释放 stale + lease_recovered 事件、active 保留、
      expired_running 不动仅上报、零释放不落事件、缺 state 报错）/
      端到端崩溃场景 orphan_lease_reconciled_after_interrupted_running_unit
      （running 单元 + 孤儿租约 → reconcile_interrupted 建议 ready →
      应用转换 → recover_leases 闭环清理）。

运行：
    cd <repo_root> && python3 -m unittest tests.test_task_manager -v
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "glm-conductor"))
from runtime import journal
from runtime import lease
from runtime import reconcile
from runtime import state
from runtime import task_manager
from runtime import work_unit

TID = "tm-task-1a2b3c"
VERIFY_CMD = "python3 -m unittest tests.test_task_manager"

LEASE_T0 = "2026-01-01T00:00:00.000Z"
LEASE_NOW = "2026-06-01T00:00:00.000Z"
LEASE_FUTURE = "2099-01-01T00:00:00.000Z"
LEASE_PAST = "2020-01-01T00:00:00.000Z"


# —— 测试夹具 ——

def wu(uid, owned=("src/a/**",), deps=(), status="ready",
       executor="flash-implementer"):
    """构造 §61 形状的 work unit dict（new_work_unit 真实构造）。"""
    return work_unit.new_work_unit(
        uid, "目标 %s" % uid, executor=executor,
        ownership=list(owned), verification=[VERIFY_CMD],
        depends_on=list(deps), status=status)


def make_task(root, units, *, status="decomposed", max_workers=1,
              active=()):
    """在临时目录真实落盘一个任务 state（new_task_state 构造 + save）。"""
    st = state.new_task_state(
        TID, "事务边界测试目标",
        {"mode": "delegate", "delegability": "high",
         "assurance": "standard", "executor": "flash-implementer",
         "continuity": "foreground"},
        ownership_files=("src/**",),
        verification_required=(VERIFY_CMD,),
        status=status)
    st["work_units"] = list(units)
    st["dispatch"] = {"max_workers": max_workers, "active": list(active)}
    state.save_state(root, st)
    return st


def state_bytes(root):
    """读取 state.json 原始字节（零写入断言用）。"""
    with open(state.state_path(root, TID), "rb") as fh:
        return fh.read()


def events(root, name):
    """读取真实 journal 并过滤事件名。"""
    return [e for e in journal.read_events(root, TID)
            if e.get("event") == name]


def unit_of(st, uid):
    """从 state dict 取单元 dict。"""
    return next(u for u in st["work_units"] if u["id"] == uid)


class TaskManagerTestBase(unittest.TestCase):
    """临时目录夹具：每个用例独立的 repo_root（无 git 需求）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = self._tmp.name


# —— prepare happy path ——

class PrepareHappyTest(TaskManagerTestBase):

    def setUp(self):
        super().setUp()
        make_task(self.root, [wu("u1", ("src/a/**",))])

    def test_prepare_returns_plan_and_acquires_lease(self):
        plan = task_manager.prepare_dispatch(self.root, TID, "u1")
        self.assertEqual(plan["dispatch"], ["u1"])
        self.assertEqual(plan["max_workers"], 1)
        self.assertEqual(plan["quota_status"], "AVAILABLE")
        leases = lease.lease_state(self.root, TID)
        self.assertEqual(set(leases), {"src/a/**"})
        self.assertEqual(leases["src/a/**"]["owner"], "u1")

    def test_prepare_journals_dispatch_prepared_with_normalized_paths(self):
        task_manager.prepare_dispatch(self.root, TID, "u1")
        records = events(self.root, "dispatch_prepared")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["unit"], "u1")
        self.assertEqual(records[0]["leased"], ["src/a/**"])

    def test_prepare_leaves_unit_status_and_state_bytes_untouched(self):
        before = state_bytes(self.root)
        task_manager.prepare_dispatch(self.root, TID, "u1")
        # prepare 对 state.json 零副作用（不落盘、不改单元状态）
        self.assertEqual(state_bytes(self.root), before)
        st = state.load_state(self.root, TID)
        self.assertEqual(unit_of(st, "u1")["status"], "ready")

    def test_prepare_normalizes_backslash_ownership(self):
        make_task(self.root, [wu("u2", ("src\\b\\main.ts",))])
        task_manager.prepare_dispatch(self.root, TID, "u2")
        record = events(self.root, "dispatch_prepared")[0]
        self.assertEqual(record["leased"], ["src/b/main.ts"])
        self.assertEqual(set(lease.lease_state(self.root, TID)),
                         {"src/b/main.ts"})

    def test_repeated_prepare_is_idempotent_on_leases(self):
        # §78 同 owner 幂等：崩溃后重备安全，不产生重复租约条目
        task_manager.prepare_dispatch(self.root, TID, "u1")
        task_manager.prepare_dispatch(self.root, TID, "u1")
        self.assertEqual(set(lease.lease_state(self.root, TID)),
                         {"src/a/**"})
        self.assertEqual(len(events(self.root, "dispatch_prepared")), 2)

    def test_max_workers_defaults_from_state_and_param_overrides(self):
        make_task(self.root, [wu("u1", ("src/a/**",)),
                              wu("u2", ("src/b/**",))],
                  max_workers=2)
        plan = task_manager.prepare_dispatch(self.root, TID, "u1")
        self.assertEqual(plan["max_workers"], 2)
        self.assertEqual(sorted(plan["dispatch"]), ["u1", "u2"])
        plan = task_manager.prepare_dispatch(self.root, TID, "u1",
                                             max_workers=1)
        self.assertEqual(plan["max_workers"], 1)
        self.assertEqual(plan["dispatch"], ["u1"])


# —— prepare 失败（消息断言 + 失败零副作用） ——

class PrepareFailureTest(TaskManagerTestBase):

    def test_unit_not_found_raises(self):
        make_task(self.root, [wu("u1")])
        with self.assertRaises(task_manager.TaskManagerError) as ctx:
            task_manager.prepare_dispatch(self.root, TID, "ghost")
        self.assertIn("ghost", str(ctx.exception))

    def test_unit_not_ready_raises_with_current_status(self):
        make_task(self.root, [wu("u1", status="running")])
        with self.assertRaises(task_manager.TaskManagerError) as ctx:
            task_manager.prepare_dispatch(self.root, TID, "u1")
        self.assertIn("u1", str(ctx.exception))
        self.assertIn("running", str(ctx.exception))
        # 失败零副作用：不写租约、不写事件
        self.assertEqual(lease.lease_state(self.root, TID), {})
        self.assertEqual(journal.read_events(self.root, TID), [])

    def test_quota_exhausted_rejected_with_reason(self):
        make_task(self.root, [wu("u1")])
        with self.assertRaises(task_manager.TaskManagerError) as ctx:
            task_manager.prepare_dispatch(self.root, TID, "u1",
                                          quota_status="EXHAUSTED")
        self.assertIn("u1", str(ctx.exception))
        self.assertIn("quota EXHAUSTED", str(ctx.exception))
        # 决策未批准发生在 acquire 之前 → 零租约零事件
        self.assertEqual(lease.lease_state(self.root, TID), {})
        self.assertEqual(journal.read_events(self.root, TID), [])

    def test_deferred_reason_surfaced(self):
        make_task(self.root, [wu("u1")])
        # PRESSURE 默认整批抑制 → deferred("pressure_suppressed")
        with self.assertRaises(task_manager.TaskManagerError) as ctx:
            task_manager.prepare_dispatch(self.root, TID, "u1",
                                          quota_status="PRESSURE")
        self.assertIn("pressure_suppressed", str(ctx.exception))
        self.assertEqual(lease.lease_state(self.root, TID), {})

    def test_unmet_dependencies_not_candidate(self):
        make_task(self.root, [wu("dep", status="pending"),
                              wu("u1", ("src/a/**",), deps=("dep",))])
        # u1 status ready 但依赖未 completed → 不进任何决策组 → fallback 理由
        with self.assertRaises(task_manager.TaskManagerError) as ctx:
            task_manager.prepare_dispatch(self.root, TID, "u1")
        self.assertIn("候选", str(ctx.exception))
        self.assertEqual(lease.lease_state(self.root, TID), {})

    def test_invalid_quota_status_value_error_propagates(self):
        # plan_dispatch 的参数校验 ValueError 不被吞
        make_task(self.root, [wu("u1")])
        with self.assertRaises(ValueError):
            task_manager.prepare_dispatch(self.root, TID, "u1",
                                          quota_status="MEGA")


# —— commit happy path ——

class CommitHappyTest(TaskManagerTestBase):

    def test_commit_transitions_books_active_and_flips_task_status(self):
        make_task(self.root, [wu("u1")], status="decomposed")
        task_manager.prepare_dispatch(self.root, TID, "u1")
        st = task_manager.commit_dispatch(self.root, TID, "u1")
        self.assertEqual(unit_of(st, "u1")["status"], "running")
        self.assertEqual(st["dispatch"]["active"], ["u1"])
        self.assertEqual(st["status"], "executing")
        # 落盘读回一致（单次 save 已持久化全部三项）
        reloaded = state.load_state(self.root, TID)
        self.assertEqual(unit_of(reloaded, "u1")["status"], "running")
        self.assertEqual(reloaded["dispatch"]["active"], ["u1"])
        self.assertEqual(reloaded["status"], "executing")

    def test_commit_journals_implementation_started_with_executor(self):
        make_task(self.root, [wu("u1")])
        task_manager.prepare_dispatch(self.root, TID, "u1")
        task_manager.commit_dispatch(self.root, TID, "u1")
        records = events(self.root, "implementation_started")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["unit"], "u1")
        self.assertEqual(records[0]["executor"], "flash-implementer")

    def test_commit_from_created_status_flips_to_executing(self):
        make_task(self.root, [wu("u1")], status="created")
        task_manager.prepare_dispatch(self.root, TID, "u1")
        st = task_manager.commit_dispatch(self.root, TID, "u1")
        self.assertEqual(st["status"], "executing")

    def test_commit_keeps_executing_status(self):
        make_task(self.root, [wu("u1")], status="executing")
        task_manager.prepare_dispatch(self.root, TID, "u1")
        st = task_manager.commit_dispatch(self.root, TID, "u1")
        self.assertEqual(st["status"], "executing")

    def test_commit_keeps_joining_status(self):
        # joining 已是执行态：不翻动（只处理四种非执行态）
        make_task(self.root, [wu("u1")], status="joining")
        task_manager.prepare_dispatch(self.root, TID, "u1")
        st = task_manager.commit_dispatch(self.root, TID, "u1")
        self.assertEqual(st["status"], "joining")

    def test_commit_active_append_is_idempotent(self):
        make_task(self.root, [wu("u1")])
        task_manager.prepare_dispatch(self.root, TID, "u1")
        # 手工制造「已在 active」的中间态（异常残留）→ commit 不重复追加
        st = state.load_state(self.root, TID)
        st["dispatch"]["active"] = ["u1", "ghost-x"]
        state.save_state(self.root, st)
        st = task_manager.commit_dispatch(self.root, TID, "u1")
        self.assertEqual(st["dispatch"]["active"], ["u1", "ghost-x"])


# —— commit 失败 ——

class CommitFailureTest(TaskManagerTestBase):

    def test_commit_without_prepare_reports_missing_lease(self):
        make_task(self.root, [wu("u1")])
        with self.assertRaises(task_manager.TaskManagerError) as ctx:
            task_manager.commit_dispatch(self.root, TID, "u1")
        self.assertIn("u1", str(ctx.exception))
        self.assertIn("prepare_dispatch", str(ctx.exception))
        # 单元保持 ready，state 未被写脏
        st = state.load_state(self.root, TID)
        self.assertEqual(unit_of(st, "u1")["status"], "ready")
        self.assertEqual(st["dispatch"]["active"], [])
        self.assertEqual(journal.read_events(self.root, TID), [])

    def test_commit_with_lost_lease_suggests_abort(self):
        make_task(self.root, [wu("u1")])
        task_manager.prepare_dispatch(self.root, TID, "u1")
        lease.release_lease(self.root, TID, "u1")  # 模拟租约外部丢失
        with self.assertRaises(task_manager.TaskManagerError) as ctx:
            task_manager.commit_dispatch(self.root, TID, "u1")
        self.assertIn("abort_dispatch", str(ctx.exception))

    def test_commit_with_foreign_lease_owner_rejected(self):
        make_task(self.root, [wu("u1")])
        lease.acquire_lease(self.root, TID, "someone-else", ["src/a/**"])
        with self.assertRaises(task_manager.TaskManagerError):
            task_manager.commit_dispatch(self.root, TID, "u1")

    def test_duplicate_commit_rejected(self):
        make_task(self.root, [wu("u1")])
        task_manager.prepare_dispatch(self.root, TID, "u1")
        task_manager.commit_dispatch(self.root, TID, "u1")
        with self.assertRaises(task_manager.TaskManagerError) as ctx:
            task_manager.commit_dispatch(self.root, TID, "u1")
        self.assertIn("running", str(ctx.exception))


# —— abort ——

class AbortTest(TaskManagerTestBase):

    def test_running_unit_cannot_abort(self):
        make_task(self.root, [wu("u1")])
        task_manager.prepare_dispatch(self.root, TID, "u1")
        task_manager.commit_dispatch(self.root, TID, "u1")
        with self.assertRaises(task_manager.TaskManagerError) as ctx:
            task_manager.abort_dispatch(self.root, TID, "u1")
        self.assertIn("running", str(ctx.exception))
        # 已提交单元的租约未被动
        self.assertEqual(lease.held_by(self.root, TID, "u1"), ["src/a/**"])

    def test_abort_releases_lease_journals_and_spares_state(self):
        make_task(self.root, [wu("u1")])
        task_manager.prepare_dispatch(self.root, TID, "u1")
        before = state_bytes(self.root)
        st = task_manager.abort_dispatch(self.root, TID, "u1")
        self.assertEqual(lease.lease_state(self.root, TID), {})
        records = events(self.root, "dispatch_aborted")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["unit"], "u1")
        # 单元保持 ready；无 active 残留 → state.json 零写入（常态）
        self.assertEqual(state_bytes(self.root), before)
        self.assertEqual(unit_of(st, "u1")["status"], "ready")

    def test_abort_cleans_active_residue_and_saves(self):
        make_task(self.root, [wu("u1")])
        task_manager.prepare_dispatch(self.root, TID, "u1")
        # 手工制造异常残留：active 里出现 u1（正常流程 commit 才记账）
        st = state.load_state(self.root, TID)
        st["dispatch"]["active"] = ["u1"]
        state.save_state(self.root, st)
        task_manager.abort_dispatch(self.root, TID, "u1")
        reloaded = state.load_state(self.root, TID)
        self.assertEqual(reloaded["dispatch"]["active"], [])
        self.assertEqual(lease.lease_state(self.root, TID), {})
        self.assertEqual(len(events(self.root, "dispatch_aborted")), 1)


# —— finish（四条转换边 + 租约释放 + active 移除 + 事件） ——

class FinishTest(TaskManagerTestBase):

    def _run_to_running(self):
        """prepare + commit 到 running（带租约与 active 记账）。"""
        make_task(self.root, [wu("u1")])
        task_manager.prepare_dispatch(self.root, TID, "u1")
        task_manager.commit_dispatch(self.root, TID, "u1")

    def test_running_to_completed_goes_through_verifying(self):
        self._run_to_running()
        calls = []
        original = work_unit.transition_work_unit

        def spy(w, new_status):
            calls.append((w.get("status"), new_status))
            return original(w, new_status)

        work_unit.transition_work_unit = spy
        try:
            st = task_manager.finish_unit(self.root, TID, "u1",
                                          outcome="completed")
        finally:
            work_unit.transition_work_unit = original
        # §70 父验证语义：恰好两次表内转换（running→verifying→completed），
        # 不越表直跳
        self.assertEqual(calls, [("running", "verifying"),
                                 ("verifying", "completed")])
        self.assertEqual(unit_of(st, "u1")["status"], "completed")
        # 租约释放 + active 移除 + 事件（落盘读回）
        reloaded = state.load_state(self.root, TID)
        self.assertEqual(reloaded["dispatch"]["active"], [])
        self.assertEqual(lease.lease_state(self.root, TID), {})
        records = events(self.root, "unit_finished")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["unit"], "u1")
        self.assertEqual(records[0]["outcome"], "completed")

    def test_running_to_failed_single_step(self):
        self._run_to_running()
        st = task_manager.finish_unit(self.root, TID, "u1",
                                      outcome="failed")
        self.assertEqual(unit_of(st, "u1")["status"], "failed")
        reloaded = state.load_state(self.root, TID)
        self.assertEqual(reloaded["dispatch"]["active"], [])
        self.assertEqual(lease.lease_state(self.root, TID), {})
        self.assertEqual(events(self.root, "unit_finished")[0]["outcome"],
                         "failed")

    def test_verifying_to_completed_releases_and_cleans(self):
        make_task(self.root, [wu("u1", status="verifying")], active=["u1"])
        lease.acquire_lease(self.root, TID, "u1", ["src/a/**"])
        st = task_manager.finish_unit(self.root, TID, "u1")
        self.assertEqual(unit_of(st, "u1")["status"], "completed")
        self.assertEqual(st["dispatch"]["active"], [])
        self.assertEqual(lease.lease_state(self.root, TID), {})
        self.assertEqual(events(self.root, "unit_finished")[0]["outcome"],
                         "completed")

    def test_verifying_to_cancelled(self):
        make_task(self.root, [wu("u1", status="verifying")], active=["u1"])
        st = task_manager.finish_unit(self.root, TID, "u1",
                                      outcome="cancelled")
        self.assertEqual(unit_of(st, "u1")["status"], "cancelled")
        self.assertEqual(st["dispatch"]["active"], [])
        self.assertEqual(events(self.root, "unit_finished")[0]["outcome"],
                         "cancelled")

    def test_invalid_outcome_raises_value_error_before_io(self):
        make_task(self.root, [wu("u1", status="running")])
        for bad in ("shipped", "VERIFIED", None):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    task_manager.finish_unit(self.root, TID, "u1",
                                             outcome=bad)
        # 校验先于任何 I/O：零事件、状态未动
        self.assertEqual(journal.read_events(self.root, TID), [])
        st = state.load_state(self.root, TID)
        self.assertEqual(unit_of(st, "u1")["status"], "running")

    def test_finish_requires_running_or_verifying(self):
        make_task(self.root, [wu("u1", status="ready")])
        with self.assertRaises(task_manager.TaskManagerError) as ctx:
            task_manager.finish_unit(self.root, TID, "u1")
        self.assertIn("ready", str(ctx.exception))

    def test_finish_unit_not_found(self):
        make_task(self.root, [wu("u1", status="running")])
        with self.assertRaises(task_manager.TaskManagerError):
            task_manager.finish_unit(self.root, TID, "ghost")


# —— 崩溃窗口模拟（文档化恢复映射两条） ——

class CrashWindowTest(TaskManagerTestBase):

    def test_crash_after_prepare_then_commit_succeeds(self):
        make_task(self.root, [wu("u1")])
        task_manager.prepare_dispatch(self.root, TID, "u1")
        # —— 崩溃点：单元仍 ready + 租约在位 + 无 running/active ——
        st = state.load_state(self.root, TID)
        self.assertEqual(unit_of(st, "u1")["status"], "ready")
        self.assertEqual(st["dispatch"]["active"], [])
        self.assertEqual(lease.held_by(self.root, TID, "u1"), ["src/a/**"])
        # 恢复路径一：直接 commit 完成提交（自有租约不挡重派）
        st = task_manager.commit_dispatch(self.root, TID, "u1")
        self.assertEqual(unit_of(st, "u1")["status"], "running")
        self.assertEqual(st["dispatch"]["active"], ["u1"])
        names = [e["event"] for e in journal.read_events(self.root, TID)]
        self.assertEqual(names, ["dispatch_prepared",
                                 "implementation_started"])

    def test_crash_after_prepare_then_abort_releases(self):
        make_task(self.root, [wu("u1")])
        task_manager.prepare_dispatch(self.root, TID, "u1")
        # 恢复路径二：abort 回退——租约清空、单元仍 ready、事件在案
        task_manager.abort_dispatch(self.root, TID, "u1")
        self.assertEqual(lease.lease_state(self.root, TID), {})
        st = state.load_state(self.root, TID)
        self.assertEqual(unit_of(st, "u1")["status"], "ready")
        self.assertEqual(len(events(self.root, "dispatch_aborted")), 1)
        # 回退后可重新 prepare（全有或全无重来一遍）
        task_manager.prepare_dispatch(self.root, TID, "u1")
        self.assertEqual(lease.held_by(self.root, TID, "u1"), ["src/a/**"])


# —— 事件词汇（RECOMMENDED_EVENTS 增补三条 + 位置锁定） ——

class JournalVocabularyTest(unittest.TestCase):

    def test_new_events_in_recommended_vocabulary(self):
        names = journal.RECOMMENDED_EVENTS
        for name in ("dispatch_prepared", "dispatch_aborted",
                     "unit_finished"):
            self.assertIn(name, names)
        # 位置：prepared / aborted 紧随 implementation_started；
        # unit_finished 在 checkpoint_written 附近（之前）
        self.assertLess(names.index("implementation_started"),
                        names.index("dispatch_prepared"))
        self.assertLess(names.index("dispatch_prepared"),
                        names.index("dispatch_aborted"))
        self.assertLess(names.index("unit_finished"),
                        names.index("checkpoint_written"))


# —— H5：prepare 默认 TTL / recover_leases / 端到端崩溃场景 ——

def _write_lease_map(root, mapping):
    """按落盘形态手工改写 leases.json（构造过期 / 指定 TTL 记录用）。"""
    path = lease.lease_path(root, TID)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(mapping, ensure_ascii=False, indent=2,
                            sort_keys=True))


def _read_lease_map(root):
    with open(lease.lease_path(root, TID), "r", encoding="utf-8") as fh:
        return json.load(fh)


class PrepareTtlTest(TaskManagerTestBase):
    """prepare_dispatch 的保守 TTL 默认（H5）。"""

    def test_prepare_acquires_with_default_ttl_and_journals_it(self):
        make_task(self.root, [wu("u1")])
        task_manager.prepare_dispatch(self.root, TID, "u1")
        record = lease.lease_state(self.root, TID)["src/a/**"]
        # H5 record 形状：TTL + generation + 心跳 + 会话标识
        self.assertIn("expires_at", record)
        self.assertEqual(record["generation"], 1)
        self.assertIsNone(record["session_id"])
        self.assertEqual(record["heartbeat_at"], record["acquired_at"])
        span = (lease._parse_iso(record["expires_at"])
                - lease._parse_iso(record["acquired_at"])).total_seconds()
        self.assertEqual(span, float(lease.LEASE_DEFAULT_TTL_SECONDS))
        # dispatch_prepared 事件补 ttl_seconds 字段
        prepared = events(self.root, "dispatch_prepared")[0]
        self.assertEqual(prepared["ttl_seconds"],
                         lease.LEASE_DEFAULT_TTL_SECONDS)

    def test_reprepare_keeps_unexpired_record(self):
        # 同 owner 未过期幂等跳过：generation 与 acquired_at 不动
        make_task(self.root, [wu("u1")])
        task_manager.prepare_dispatch(self.root, TID, "u1")
        before = _read_lease_map(self.root)["src/a/**"]
        task_manager.prepare_dispatch(self.root, TID, "u1")
        self.assertEqual(_read_lease_map(self.root)["src/a/**"], before)
        self.assertEqual(before["generation"], 1)


class RecoverLeasesTest(TaskManagerTestBase):

    def _expire_paths(self, paths):
        """把指定路径的租约记录拨到已过期（其余保持未过期）。"""
        mapping = _read_lease_map(self.root)
        for path in paths:
            mapping[path]["expires_at"] = LEASE_PAST
        _write_lease_map(self.root, mapping)

    def test_recover_releases_stale_and_journals(self):
        # 单元 completed（非活跃写相）仍持租约 → stale → 自动释放
        make_task(self.root, [wu("u1", status="completed")])
        lease.acquire_lease(self.root, TID, "u1", ["src/a/**"])
        report = task_manager.recover_leases(self.root, TID)
        self.assertEqual(report["released"], ["src/a/**"])
        self.assertEqual([item["path"] for item in report["stale"]],
                         ["src/a/**"])
        self.assertEqual(report["expired_running"], [])
        self.assertEqual(lease.lease_state(self.root, TID), {})
        record = events(self.root, "lease_recovered")[0]
        self.assertEqual(record["released"], ["src/a/**"])
        self.assertEqual(record["kept_expired_running"], [])

    def test_recover_keeps_active_and_expired_running(self):
        make_task(self.root, [wu("u1", ("src/a/**",), status="running"),
                              wu("u2", ("src/b/**",), status="running")])
        lease.acquire_lease(self.root, TID, "u1", ["src/a/**"],
                            ttl_seconds=3600)
        lease.acquire_lease(self.root, TID, "u2", ["src/b/**"],
                            ttl_seconds=3600)
        self._expire_paths(["src/b/**"])
        report = task_manager.recover_leases(self.root, TID)
        # active 保留；expired_running（worker 可能仍在写）不动、仅上报
        self.assertEqual([item["path"] for item in report["active"]],
                         ["src/a/**"])
        self.assertEqual([item["path"] for item in
                          report["expired_running"]], ["src/b/**"])
        # released 为空 → 不落 journal（只读对账不留噪声行）
        self.assertEqual(report["released"], [])
        self.assertEqual(journal.read_events(self.root, TID), [])
        # 两条租约都未被 recover 动过
        self.assertEqual(set(lease.lease_state(self.root, TID)),
                         {"src/a/**", "src/b/**"})

    def test_recover_mixed_releases_stale_keeps_expired_running(self):
        make_task(self.root, [wu("u1", ("src/a/**",), status="failed"),
                              wu("u2", ("src/b/**",), status="running")])
        lease.acquire_lease(self.root, TID, "u1", ["src/a/**"],
                            ttl_seconds=3600)
        lease.acquire_lease(self.root, TID, "u2", ["src/b/**"],
                            ttl_seconds=3600)
        self._expire_paths(["src/a/**", "src/b/**"])
        report = task_manager.recover_leases(self.root, TID)
        self.assertEqual(report["released"], ["src/a/**"])
        self.assertEqual(lease.lease_state(self.root, TID),
                         {"src/b/**": lease.lease_state(
                             self.root, TID)["src/b/**"]})
        record = events(self.root, "lease_recovered")[0]
        self.assertEqual(record["released"], ["src/a/**"])
        self.assertEqual(record["kept_expired_running"], ["src/b/**"])

    def test_recover_requires_task_state(self):
        with self.assertRaises(task_manager.TaskManagerError):
            task_manager.recover_leases(self.root, TID)


class OrphanLeaseRecoveryTest(TaskManagerTestBase):
    """端到端崩溃场景（appendix B：
    orphan_lease_reconciled_after_interrupted_running_unit）。"""

    def test_orphan_lease_reconciled_after_interrupted_running_unit(self):
        make_task(self.root, [wu("u1")])
        task_manager.prepare_dispatch(self.root, TID, "u1")
        task_manager.commit_dispatch(self.root, TID, "u1")
        # —— 崩溃：单元 running + 租约在位；无验证证据、工作区干净 ——
        st = state.load_state(self.root, TID)
        self.assertEqual(unit_of(st, "u1")["status"], "running")
        self.assertEqual(lease.held_by(self.root, TID, "u1"),
                         ["src/a/**"])
        # §69 对账（注入 touched=[] 干净工作区 / events=[] 无验证证据）
        report = reconcile.reconcile_interrupted(
            self.root, TID, st["work_units"], touched=[], events=[])
        self.assertEqual(report["suggestions"]["u1"]["to"], "ready")
        # 应用建议（主会话侧：只对 suggestions 应用 transition）
        work_unit.transition_work_unit(unit_of(st, "u1"), "ready")
        state.save_state(self.root, st)
        # 单元已回 ready（非活跃写相）→ 租约成孤儿 → recover 闭环清理
        recovered = task_manager.recover_leases(self.root, TID)
        self.assertEqual(recovered["released"], ["src/a/**"])
        self.assertEqual(lease.lease_state(self.root, TID), {})
        record = events(self.root, "lease_recovered")[0]
        self.assertEqual(record["released"], ["src/a/**"])
        self.assertEqual(record["kept_expired_running"], [])


if __name__ == "__main__":
    unittest.main()
