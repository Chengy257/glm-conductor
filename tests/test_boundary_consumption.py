#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v2.2 C1b resume-time consumption 三 API 单元测试（wu-22-C1b）。

锚定对象（修正计划）：
  - §C1b / §22.1：record_quota_boundary_consumed——幂等 key =
    task_id + epoch_id（同 key 重放 no-op 并标注 idempotent）、授权
    三查镜像 record_quota_wake（auto_resume ∈ {auto_once, until_done}
    / authorization.source == "user" / remaining > 0，拒绝零副作用）、
    事件携带 epoch_id + executable_boundary_id + resume_started_at +
    consumed/remaining；
  - §22.6：migrate_quota_window_accounting——保守迁移 max 语义不退款
    （new_consumed >= legacy_consumed 恒成立）、数字相同仍写一次性
    quota_accounting_migrated 五字段事件、重复触发幂等 no-op；v2.2 C7
    双键联合去重（epoch_id / boundary_id / executable_boundary_id 全
    身份键登记 seen，同窗 legacy + 新形态证据不双计）；
  - §22.5：reconcile_wake_bridge_from_host——纯账本对账（host_status
    由调用方显式传入，零宿主调用 / 零 CronList 概念）、对账只能降级
    / 确认、journal 历史单独绝不制造 armed 桥；
  - §15.1：消费点唯一 = Resume Controller resume commit point——
    本文件同时锚定 resume_from_quota 一字不动（C1b 只提供 API，
    不接线）。

fixture：tempfile 仓库 + runtime.state 构造（scratch 任务目录，不碰
真实账本）；全部离线（零网络、零宿主调用）。仅 Python 3 标准库
（unittest + tempfile），零第三方依赖。

运行：
    cd <repo_root> && python3 -m unittest tests.test_boundary_consumption -v
"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "glm-conductor"))
from runtime import execution_policy, journal, state, task_manager

TID = "boundary-consume-1a2b3c"
ROUTE = {"mode": "delegate", "delegability": "high",
         "assurance": "standard", "executor": "flash-implementer",
         "continuity": "resumable"}
CONFIRMED_AT = "2026-09-03T00:00:00+00:00"
VERIFY_CMD = "python3 -m unittest tests.test_boundary_consumption"

EPOCH_A = "glm:aaaa1111aaaa1111"
EPOCH_B = "glm:bbbb2222bbbb2222"
BOUNDARY_A = "five_hour:2026-09-01T21:59:00Z"
BOUNDARY_B = "five_hour:2026-09-02T21:59:00Z"
RESUME_AT = "2026-09-03T08:00:00Z"


def make_unit(uid, status="ready"):
    """构造一个通过 §61 契约校验的最小 work unit dict。"""
    return {"id": uid, "objective": "fixture unit %s" % uid,
            "status": status, "depends_on": [],
            "executor": "flash-implementer",
            "ownership": ["src/%s.py" % uid],
            "verification": [VERIFY_CMD]}


def authorize(st, auto_resume, max_quota_windows, source="user",
              confirmed_at=CONFIRMED_AT):
    """以 set_resume_authorization 写入续跑授权（返回全新 policy dict）。"""
    return execution_policy.set_resume_authorization(
        st["execution_policy"], auto_resume=auto_resume,
        max_quota_windows=max_quota_windows, source=source,
        confirmed_at=confirmed_at)


class ConsumptionTestBase(unittest.TestCase):
    """临时目录夹具 + 常用装置（每个用例独立的 repo_root）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = self._tmp.name

    def save_task(self, *, status="waiting_quota", auto_resume="until_done",
                  max_quota_windows=2, source="user", units=None):
        st = state.new_task_state(
            TID, "boundary consumption fixture", dict(ROUTE),
            ownership_files=["src"], verification_required=[VERIFY_CMD],
            status=status)
        st["work_units"] = list(units) if units is not None else [
            make_unit("wu-a")]
        st["execution_policy"] = authorize(st, auto_resume,
                                           max_quota_windows, source)
        state.save_state(self.repo, st)
        return st

    def state_bytes(self):
        return state.state_path(self.repo, TID).read_bytes()

    def events(self, name):
        return [e for e in journal.read_events(self.repo, TID)
                if e.get("event") == name]

    def consumed(self):
        st = state.load_state(self.repo, TID)
        continuity = st["execution_policy"]["continuity"]
        return continuity.get("consumed_quota_windows", 0)

    def set_consumed(self, value):
        """直接预置 continuity.consumed_quota_windows（legacy 存量模拟）。"""
        st = state.load_state(self.repo, TID)
        st["execution_policy"]["continuity"]["consumed_quota_windows"] = value
        state.save_state(self.repo, st)

    def arm_bridge(self, automation_id="cron-persist",
                   boundary_id=BOUNDARY_A):
        return task_manager.arm_wake_bridge(
            self.repo, TID, automation_id=automation_id,
            boundary_id=boundary_id, reset_at="2026-09-01T21:59:00Z",
            wake_at="2026-09-01T22:04:00Z")

    def append_consumed_event(self, *, epoch_id=None, boundary_id=None,
                              executable_boundary_id=None):
        """直接向 journal 注入一条 quota_boundary_consumed 证据（迁移
        verified 口径的输入；模拟 §22.6 所引 2026-09-01 人工证据时用
        boundary_id alias 形态；v2.2 C7 双键去重用例另以
        executable_boundary_id 注入新形态 §C1b 事件）。"""
        event = {"event": "quota_boundary_consumed"}
        if epoch_id is not None:
            event["epoch_id"] = epoch_id
        if boundary_id is not None:
            event["boundary_id"] = boundary_id
        if executable_boundary_id is not None:
            event["executable_boundary_id"] = executable_boundary_id
        event["resume_started_at"] = RESUME_AT
        return journal.append_event(self.repo, TID, event)


# —— record_quota_boundary_consumed（§22.1 / §C1b / §15.1） ——

class RecordQuotaBoundaryConsumedTest(ConsumptionTestBase):
    """§22.1：resume-time 消费记账——递增、事件形状、幂等与三查。"""

    def test_consumes_once_journals_event_and_updates_state(self):
        self.save_task(max_quota_windows=2)
        result = task_manager.record_quota_boundary_consumed(
            self.repo, TID, epoch_id=EPOCH_A,
            executable_boundary_id=BOUNDARY_A, resume_started_at=RESUME_AT)
        self.assertEqual(result["consumed_quota_windows"], 1)
        self.assertEqual(result["remaining_quota_windows"], 1)
        self.assertEqual(result["max_quota_windows"], 2)
        self.assertEqual(result["epoch_id"], EPOCH_A)
        self.assertEqual(result["executable_boundary_id"], BOUNDARY_A)
        self.assertEqual(result["resume_started_at"], RESUME_AT)
        self.assertNotIn("idempotent", result)
        self.assertEqual(self.consumed(), 1)
        records = self.events("quota_boundary_consumed")
        self.assertEqual(len(records), 1)
        # §C1b：事件同时携带 epoch_id 与 executable_boundary_id
        self.assertEqual(records[0]["epoch_id"], EPOCH_A)
        self.assertEqual(records[0]["executable_boundary_id"], BOUNDARY_A)
        self.assertEqual(records[0]["resume_started_at"], RESUME_AT)
        self.assertEqual(records[0]["consumed"], 1)
        self.assertEqual(records[0]["remaining"], 1)

    def test_same_epoch_replay_is_idempotent_noop(self):
        """§22.1：幂等 key = task_id + epoch_id——重放不递增、不新建
        事件、零写副作用，返回既有结果 + idempotent 标注（§15.1：绝不
        对同一 epoch 二次消费）。"""
        self.save_task(max_quota_windows=2)
        first = task_manager.record_quota_boundary_consumed(
            self.repo, TID, epoch_id=EPOCH_A,
            executable_boundary_id=BOUNDARY_A, resume_started_at=RESUME_AT)
        before = self.state_bytes()
        replay = task_manager.record_quota_boundary_consumed(
            self.repo, TID, epoch_id=EPOCH_A,
            executable_boundary_id=BOUNDARY_B,  # boundary 不同也不影响幂等
            resume_started_at="2026-09-03T09:00:00Z")
        self.assertEqual(replay["consumed_quota_windows"],
                         first["consumed_quota_windows"])
        self.assertEqual(replay["remaining_quota_windows"],
                         first["remaining_quota_windows"])
        self.assertEqual(replay["executable_boundary_id"], BOUNDARY_A)
        self.assertEqual(replay["resume_started_at"], RESUME_AT)
        self.assertTrue(replay["idempotent"])
        self.assertEqual(self.events("quota_boundary_consumed"), [
            e for e in journal.read_events(self.repo, TID)
            if e.get("event") == "quota_boundary_consumed"])
        self.assertEqual(len(self.events("quota_boundary_consumed")), 1)
        self.assertEqual(self.consumed(), 1)
        self.assertEqual(self.state_bytes(), before)

    def test_different_epoch_consumes_again(self):
        """§15.1：epoch 推进（窗口身份推进）后新 epoch 照常消费一窗。"""
        self.save_task(max_quota_windows=3)
        task_manager.record_quota_boundary_consumed(
            self.repo, TID, epoch_id=EPOCH_A,
            executable_boundary_id=BOUNDARY_A, resume_started_at=RESUME_AT)
        second = task_manager.record_quota_boundary_consumed(
            self.repo, TID, epoch_id=EPOCH_B,
            executable_boundary_id=BOUNDARY_B,
            resume_started_at="2026-09-03T14:00:00Z")
        self.assertEqual(second["consumed_quota_windows"], 2)
        self.assertEqual(len(self.events("quota_boundary_consumed")), 2)
        self.assertEqual(self.consumed(), 2)

    def test_manual_mode_cannot_consume(self):
        """三查之一：manual（/notify）任务不得经本 API 制造 consumed
        window——TaskManagerError 指明 violated 条件，零副作用。"""
        self.save_task(auto_resume="manual", max_quota_windows=0)
        before = self.state_bytes()
        with self.assertRaises(task_manager.TaskManagerError) as ctx:
            task_manager.record_quota_boundary_consumed(
                self.repo, TID, epoch_id=EPOCH_A,
                executable_boundary_id=BOUNDARY_A, resume_started_at=RESUME_AT)
        self.assertIn("manual", str(ctx.exception))
        self.assertEqual(self.state_bytes(), before)
        self.assertEqual(self.events("quota_boundary_consumed"), [])
        self.assertEqual(self.consumed(), 0)

    def test_unauthorized_source_cannot_consume(self):
        """三查之二（纵深防御）：auto_once + source=default 被
        execution_policy §5.4 不变量挡在合法 state 之外——手写
        state.json 绕过保存闸（load_state 不复检），记账侧仍必须独立
        拒绝（与 record_quota_wake 三查口径一致）。"""
        import json
        policy = authorize(self.save_task(max_quota_windows=1),
                           "until_done", 1)
        policy["authorization"] = {"source": "default",
                                   "confirmed_at": None, "scope": "task"}
        st = state.load_state(self.repo, TID)
        st["execution_policy"] = policy
        with open(state.state_path(self.repo, TID), "w",
                  encoding="utf-8", newline="\n") as fh:
            json.dump(st, fh, ensure_ascii=False)
        before = self.state_bytes()
        with self.assertRaises(task_manager.TaskManagerError) as ctx:
            task_manager.record_quota_boundary_consumed(
                self.repo, TID, epoch_id=EPOCH_A,
                executable_boundary_id=BOUNDARY_A, resume_started_at=RESUME_AT)
        self.assertIn("user", str(ctx.exception))
        self.assertEqual(self.state_bytes(), before)
        self.assertEqual(self.events("quota_boundary_consumed"), [])

    def test_budget_exhausted_cannot_consume(self):
        """三查之三：剩余预算耗尽（remaining == 0）后不得再消费新
        epoch——§14.5 语义在消费侧的独立防线。"""
        self.save_task(max_quota_windows=1)
        task_manager.record_quota_boundary_consumed(
            self.repo, TID, epoch_id=EPOCH_A,
            executable_boundary_id=BOUNDARY_A, resume_started_at=RESUME_AT)
        before = self.state_bytes()
        with self.assertRaises(task_manager.TaskManagerError) as ctx:
            task_manager.record_quota_boundary_consumed(
                self.repo, TID, epoch_id=EPOCH_B,
                executable_boundary_id=BOUNDARY_B,
                resume_started_at="2026-09-03T14:00:00Z")
        self.assertIn("耗尽", str(ctx.exception))
        # 零副作用：事件仍 1 条、consumed 仍 1、字节不变
        self.assertEqual(len(self.events("quota_boundary_consumed")), 1)
        self.assertEqual(self.consumed(), 1)
        self.assertEqual(self.state_bytes(), before)

    def test_notify_mode_rejected_too(self):
        # notify ∈ 授权族之外（manual/notify 均不得经本 API 消费）
        self.save_task(auto_resume="notify", max_quota_windows=0)
        with self.assertRaises(task_manager.TaskManagerError):
            task_manager.record_quota_boundary_consumed(
                self.repo, TID, epoch_id=EPOCH_A,
                executable_boundary_id=BOUNDARY_A, resume_started_at=RESUME_AT)
        self.assertEqual(self.events("quota_boundary_consumed"), [])

    def test_param_validation_valueerror_zero_side_effects(self):
        """参数校验先于任何 I/O：空串 / 非 str → ValueError，零副作用。"""
        self.save_task()
        before = self.state_bytes()
        for kwargs in ({"epoch_id": "", "executable_boundary_id": "b",
                        "resume_started_at": "t"},
                       {"epoch_id": None, "executable_boundary_id": "b",
                        "resume_started_at": "t"},
                       {"epoch_id": EPOCH_A, "executable_boundary_id": "",
                        "resume_started_at": "t"},
                       {"epoch_id": EPOCH_A, "executable_boundary_id": "b",
                        "resume_started_at": None}):
            with self.subTest(**kwargs):
                with self.assertRaises(ValueError):
                    task_manager.record_quota_boundary_consumed(
                        self.repo, TID, **kwargs)
        self.assertEqual(self.state_bytes(), before)
        self.assertEqual(self.events("quota_boundary_consumed"), [])

    def test_missing_task_raises(self):
        with self.assertRaises(task_manager.TaskManagerError):
            task_manager.record_quota_boundary_consumed(
                self.repo, TID, epoch_id=EPOCH_A,
                executable_boundary_id=BOUNDARY_A, resume_started_at=RESUME_AT)

    def test_resume_from_quota_wired_to_consumption_at_commit_point(self):
        """§15.1 纪律锚定（v2.2 C7 翻转 C1b 的「不接线」锚——接线归
        C7 Resume Controller 单元，wu-22-C7 已落地）：resume_from_quota
        剥离 docstring 的函数体内消费段接线在案（_resume_consumption
        落在 mark 之后的 commit point），且消费段体内 record 调用唯一
        （resume 侧 §15.1 唯一消费点）并先 migrate 后 record（迁移先于
        新形态事件，防双记账）。"""
        import inspect
        resume_body = inspect.getsource(
            task_manager.resume_from_quota).split('"""', 2)[2]
        self.assertIn("_resume_consumption(", resume_body)
        self.assertIn("mark_activation_epoch(", resume_body)
        self.assertLess(resume_body.index("mark_activation_epoch("),
                        resume_body.index("_resume_consumption("))
        segment_body = inspect.getsource(
            task_manager._resume_consumption).split('"""', 2)[2]
        self.assertEqual(
            segment_body.count("record_quota_boundary_consumed("), 1)
        self.assertIn("migrate_quota_window_accounting(", segment_body)
        self.assertLess(
            segment_body.index("migrate_quota_window_accounting("),
            segment_body.index("record_quota_boundary_consumed("))


# —— migrate_quota_window_accounting（§22.6） ——

class MigrateQuotaWindowAccountingTest(ConsumptionTestBase):
    """§22.6：存量记账保守迁移——max 不退款、同数字仍写一次性事件。"""

    MIGRATED_EVENT_FIELDS = ("legacy_consumed", "verified_boundary_ids",
                             "attributed_consumed",
                             "legacy_unattributed_consumed", "migrated_at")

    def test_same_numbers_still_write_one_event(self):
        """数字相同（new == legacy）也要写事件——迁移事实本身是账本
        记录；state 零改写（consumed 不动）。"""
        self.save_task(max_quota_windows=2)
        self.set_consumed(1)
        before = self.state_bytes()
        result = task_manager.migrate_quota_window_accounting(
            self.repo, TID)
        self.assertEqual(result["legacy_consumed"], 1)
        self.assertEqual(result["verified_boundary_ids"], [])
        self.assertEqual(result["attributed_consumed"], 0)
        self.assertEqual(result["legacy_unattributed_consumed"], 1)
        self.assertEqual(result["new_consumed"], 1)
        self.assertTrue(result["new_consumed"] >= result["legacy_consumed"])
        self.assertTrue(result["migrated_at"])
        records = self.events("quota_accounting_migrated")
        self.assertEqual(len(records), 1)
        for field in self.MIGRATED_EVENT_FIELDS:
            self.assertIn(field, records[0])
        self.assertEqual(set(records[0]) - {"ts", "event"},
                         set(self.MIGRATED_EVENT_FIELDS))
        self.assertEqual(self.consumed(), 1)  # 不退款也不加账
        self.assertEqual(self.state_bytes(), before)  # 数字不变零 state 写

    def test_manual_legacy_evidence_attributed_no_double_count(self):
        """§22.6 末端锁定场景：一条 boundary_id alias 形态的人工
        quota_boundary_consumed 证据把 legacy consumed=1 正式归属到
        真实跨窗恢复——数字保持 1，不再重复 +1。"""
        self.save_task(max_quota_windows=2)
        self.set_consumed(1)
        self.append_consumed_event(boundary_id=BOUNDARY_A)
        result = task_manager.migrate_quota_window_accounting(
            self.repo, TID)
        self.assertEqual(result["verified_boundary_ids"], [BOUNDARY_A])
        self.assertEqual(result["attributed_consumed"], 1)
        self.assertEqual(result["legacy_unattributed_consumed"], 0)
        self.assertEqual(result["new_consumed"], 1)
        self.assertEqual(self.consumed(), 1)

    def test_verified_above_legacy_raises_new_consumed(self):
        """journal 可证明的消费数高于 legacy 存量 → new = max = verified
        （保守上调），state 投影跟进；不变量 new >= legacy 恒成立。"""
        self.save_task(max_quota_windows=3)
        self.set_consumed(1)
        self.append_consumed_event(epoch_id=EPOCH_A)
        self.append_consumed_event(epoch_id=EPOCH_B)
        result = task_manager.migrate_quota_window_accounting(
            self.repo, TID)
        self.assertEqual(result["legacy_consumed"], 1)
        self.assertEqual(result["new_consumed"], 2)
        self.assertEqual(result["attributed_consumed"], 1)  # min(1, 2)
        self.assertEqual(result["legacy_unattributed_consumed"], 0)
        self.assertEqual(self.consumed(), 2)
        self.assertEqual(
            result["new_consumed"] >= result["legacy_consumed"], True)

    def test_duplicate_epoch_events_counted_once(self):
        """同 epoch 重放事件只计一次（幂等 key 同构：身份去重）。"""
        self.save_task(max_quota_windows=3)
        self.set_consumed(1)
        self.append_consumed_event(epoch_id=EPOCH_A)
        self.append_consumed_event(epoch_id=EPOCH_A)  # 重放
        result = task_manager.migrate_quota_window_accounting(
            self.repo, TID)
        self.assertEqual(result["verified_boundary_ids"], [EPOCH_A])
        self.assertEqual(result["new_consumed"], 1)
        self.assertEqual(self.consumed(), 1)

    # —— v2.2 C7 双键联合去重（wu-22-C7 ③）：seen 登记全部身份键 ——

    def test_dual_key_legacy_boundary_and_new_epoch_same_window_count_once(self):
        """同窗 legacy boundary-only 人工证据 + 新形态 epoch 证据
        （executable_boundary_id 同窗同值）不再双计——verified 计一次，
        max 不退款语义不变（new >= legacy 恒成立）。"""
        self.save_task(max_quota_windows=3)
        self.set_consumed(1)
        self.append_consumed_event(boundary_id=BOUNDARY_A)
        self.append_consumed_event(epoch_id=EPOCH_A,
                                   executable_boundary_id=BOUNDARY_A)
        result = task_manager.migrate_quota_window_accounting(
            self.repo, TID)
        self.assertEqual(len(result["verified_boundary_ids"]), 1)
        self.assertEqual(result["new_consumed"], 1)
        self.assertEqual(result["new_consumed"] >= result["legacy_consumed"],
                         True)
        self.assertEqual(self.consumed(), 1)

    def test_dual_key_same_boundary_via_executable_field_dedup(self):
        """executable_boundary_id 在案身份值同样登记进 seen：不同 epoch
        但 executable_boundary_id 同窗的两条证据只计第一条。"""
        self.save_task(max_quota_windows=3)
        self.set_consumed(1)
        self.append_consumed_event(epoch_id=EPOCH_A,
                                   executable_boundary_id=BOUNDARY_A)
        self.append_consumed_event(epoch_id=EPOCH_B,
                                   executable_boundary_id=BOUNDARY_A)
        result = task_manager.migrate_quota_window_accounting(
            self.repo, TID)
        self.assertEqual(result["verified_boundary_ids"], [EPOCH_A])
        self.assertEqual(result["new_consumed"], 1)
        self.assertEqual(self.consumed(), 1)

    def test_dual_key_distinct_windows_count_separately(self):
        """去重面不放大：身份键全不同的 distinct 窗口照常各计一次。"""
        self.save_task(max_quota_windows=4)
        self.set_consumed(1)
        self.append_consumed_event(epoch_id=EPOCH_A,
                                   executable_boundary_id=BOUNDARY_A)
        self.append_consumed_event(epoch_id=EPOCH_B,
                                   executable_boundary_id=BOUNDARY_B)
        result = task_manager.migrate_quota_window_accounting(
            self.repo, TID)
        self.assertEqual(result["verified_boundary_ids"], [EPOCH_A, EPOCH_B])
        self.assertEqual(result["new_consumed"], 2)
        self.assertEqual(self.consumed(), 2)

    def test_identityless_events_not_verified(self):
        """epoch_id / boundary_id 皆缺的条目不可核验，不计入 verified。"""
        self.save_task(max_quota_windows=2)
        self.set_consumed(1)
        self.append_consumed_event()  # 无身份键
        result = task_manager.migrate_quota_window_accounting(
            self.repo, TID)
        self.assertEqual(result["verified_boundary_ids"], [])
        self.assertEqual(result["new_consumed"], 1)

    def test_one_shot_idempotent_noop_on_replay(self):
        """§22.6「迁移写一次」：重复触发幂等 no-op——零写零事件。"""
        self.save_task(max_quota_windows=3)
        self.set_consumed(1)
        self.append_consumed_event(epoch_id=EPOCH_A)
        first = task_manager.migrate_quota_window_accounting(
            self.repo, TID)
        self.assertNotIn("idempotent", first)
        before = self.state_bytes()
        replay = task_manager.migrate_quota_window_accounting(
            self.repo, TID)
        self.assertTrue(replay["idempotent"])
        self.assertEqual(replay["legacy_consumed"],
                         first["legacy_consumed"])
        self.assertEqual(len(self.events("quota_accounting_migrated")), 1)
        self.assertEqual(self.state_bytes(), before)

    def test_missing_task_raises(self):
        with self.assertRaises(task_manager.TaskManagerError):
            task_manager.migrate_quota_window_accounting(self.repo, TID)


# —— RH-03 崩溃一致性（wu-rh03；修正计划 §5 write-ahead pending marker） ——

def _flaky_append(real_append, target_event):
    """构造「指定事件名追加即抛 OSError、其余事件透传」的 append_event
    替身（故障注入全走 monkeypatch，绝不真实 kill 进程）。"""
    def flaky(repo_root, task_id, event, **kwargs):
        if event.get("event") == target_event:
            raise OSError("journal locked (%s)" % target_event)
        return real_append(repo_root, task_id, event, **kwargs)
    return flaky


class ConsumptionCrashConsistencyTest(ConsumptionTestBase):
    """RH-03：record_quota_boundary_consumed 的 write-ahead pending
    marker——崩溃窗口（pending 落/save 抛、save 落/committed 抛）重跑
    不得多消费；恢复闭合先于授权三查；异常形态零写待人工裁决。"""

    def _consume(self, epoch_id=EPOCH_A):
        return task_manager.record_quota_boundary_consumed(
            self.repo, TID, epoch_id=epoch_id,
            executable_boundary_id=BOUNDARY_A, resume_started_at=RESUME_AT)

    def _append_pending(self, epoch_id=EPOCH_A, target_consumed=1):
        return journal.append_event(self.repo, TID, {
            "event": "quota_consumption_pending", "epoch_id": epoch_id,
            "executable_boundary_id": BOUNDARY_A,
            "target_consumed": target_consumed,
            "resume_started_at": RESUME_AT})

    def test_pending_event_frozen_fields_and_write_ahead_order(self):
        """pending 五字段逐字 {event, epoch_id, executable_boundary_id,
        target_consumed, resume_started_at}；write-ahead 排序：pending
        先于 save_state、committed 最后闭合。"""
        self.save_task(max_quota_windows=2)
        self._consume()
        pendings = self.events("quota_consumption_pending")
        self.assertEqual(len(pendings), 1)
        self.assertEqual(
            set(pendings[0]) - {"ts"},
            {"event", "epoch_id", "executable_boundary_id",
             "target_consumed", "resume_started_at"})
        self.assertEqual(pendings[0]["epoch_id"], EPOCH_A)
        self.assertEqual(pendings[0]["executable_boundary_id"], BOUNDARY_A)
        self.assertEqual(pendings[0]["target_consumed"], 1)
        self.assertEqual(pendings[0]["resume_started_at"], RESUME_AT)
        names = [e.get("event") for e in journal.read_events(self.repo, TID)]
        self.assertLess(names.index("quota_consumption_pending"),
                        names.index("quota_boundary_consumed"))

    def test_consume_txn_01_pending_saved_state_save_failure_rerun_consumes_once(self):
        """CONSUME-TXN-01：pending 落 → save_state 抛 → 重跑（同 epoch）
        不得多消费——consumed 恰 +1 总量、journal 恰一条 pending +
        一条 committed。"""
        self.save_task(max_quota_windows=2)
        with mock.patch.object(state, "save_state",
                               side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self._consume()
        # pending 已落、投影未落
        self.assertEqual(len(self.events("quota_consumption_pending")), 1)
        self.assertEqual(self.events("quota_boundary_consumed"), [])
        self.assertEqual(self.consumed(), 0)
        # 重跑：恢复闭合（投影未落 → 补投影 + 补 committed）
        result = self._consume()
        self.assertNotIn("idempotent", result)
        self.assertEqual(result["consumed_quota_windows"], 1)
        self.assertEqual(self.consumed(), 1)  # 恰 +1 总量
        self.assertEqual(len(self.events("quota_consumption_pending")), 1)
        committed = self.events("quota_boundary_consumed")
        self.assertEqual(len(committed), 1)   # 恰一条 committed
        self.assertEqual(committed[0]["consumed"], 1)
        self.assertEqual(committed[0]["remaining"], 1)

    def test_consume_txn_02_state_saved_committed_failure_rerun_closes_only_committed(self):
        """CONSUME-TXN-02：save_state 成功 → committed 追加抛 → 重跑
        不得多消费——恢复闭合只补 committed 事件。"""
        self.save_task(max_quota_windows=2)
        with mock.patch.object(
                journal, "append_event",
                side_effect=_flaky_append(journal.append_event,
                                          "quota_boundary_consumed")):
            with self.assertRaises(OSError):
                self._consume()
        # 投影已落、committed 缺席
        self.assertEqual(self.consumed(), 1)
        self.assertEqual(len(self.events("quota_consumption_pending")), 1)
        self.assertEqual(self.events("quota_boundary_consumed"), [])
        # 重跑：state 已达 target → 只补 committed（不再 +1）
        result = self._consume()
        self.assertEqual(result["consumed_quota_windows"], 1)
        self.assertEqual(self.consumed(), 1)
        self.assertEqual(len(self.events("quota_consumption_pending")), 1)
        committed = self.events("quota_boundary_consumed")
        self.assertEqual(len(committed), 1)
        self.assertEqual(committed[0]["consumed"], 1)
        self.assertEqual(committed[0]["remaining"], 1)

    def test_consume_txn_03_same_epoch_normal_replay_idempotent(self):
        """CONSUME-TXN-03：同 epoch 正常重放 → consumed 不变 +
        idempotent:True（既有语义回归），pending / committed 各恰一条。"""
        self.save_task(max_quota_windows=2)
        first = self._consume()
        before = self.state_bytes()
        replay = self._consume()
        self.assertTrue(replay["idempotent"])
        self.assertEqual(replay["consumed_quota_windows"],
                         first["consumed_quota_windows"])
        self.assertEqual(self.consumed(), 1)
        self.assertEqual(len(self.events("quota_consumption_pending")), 1)
        self.assertEqual(len(self.events("quota_boundary_consumed")), 1)
        self.assertEqual(self.state_bytes(), before)  # 重放零写

    def test_consume_txn_04_different_epoch_consumes_again(self):
        """CONSUME-TXN-04：不同 epoch → consumed +1（pending / committed
        各 +1，各 epoch 一套）。"""
        self.save_task(max_quota_windows=3)
        self._consume(EPOCH_A)
        second = task_manager.record_quota_boundary_consumed(
            self.repo, TID, epoch_id=EPOCH_B,
            executable_boundary_id=BOUNDARY_B,
            resume_started_at="2026-09-03T14:00:00Z")
        self.assertEqual(second["consumed_quota_windows"], 2)
        self.assertEqual(self.consumed(), 2)
        self.assertEqual(len(self.events("quota_consumption_pending")), 2)
        self.assertEqual(len(self.events("quota_boundary_consumed")), 2)

    def test_consume_txn_05_pending_projected_exhausted_budget_still_closes(self):
        """CONSUME-TXN-05：pending 在案 + state 已投影 + 剩余预算恰为 0
        （target == max）→ 重跑仍闭合成功——恢复闭合先于授权三查的排序
        锚（pending 是上一进程已过授权的持久证据，闭合不重查预算；
        若闭合放在三查之后，该事务永远无法完成）。"""
        self.save_task(max_quota_windows=1)
        self._append_pending(target_consumed=1)
        self.set_consumed(1)  # 投影已落 + remaining 恰为 0
        before = self.state_bytes()
        result = self._consume()
        # 标准六键（返回键零变化，不加 idempotent 等新键）
        self.assertEqual(sorted(result), [
            "consumed_quota_windows", "epoch_id",
            "executable_boundary_id", "max_quota_windows",
            "remaining_quota_windows", "resume_started_at"])
        self.assertEqual(result["consumed_quota_windows"], 1)
        self.assertEqual(result["remaining_quota_windows"], 0)
        self.assertEqual(result["resume_started_at"], RESUME_AT)  # 冻结值
        # 只补 committed：零 state 写、pending 不新增
        self.assertEqual(self.state_bytes(), before)
        self.assertEqual(len(self.events("quota_consumption_pending")), 1)
        committed = self.events("quota_boundary_consumed")
        self.assertEqual(len(committed), 1)
        self.assertEqual(committed[0]["consumed"], 1)
        self.assertEqual(committed[0]["remaining"], 0)
        # 闭合后该 epoch 再重放 → 自然落入 committed 幂等分支
        again = self._consume()
        self.assertTrue(again["idempotent"])
        self.assertEqual(len(self.events("quota_boundary_consumed")), 1)

    def test_pending_projection_missing_closes_with_single_projection(self):
        """恢复闭合·投影未落分支：state == target - 1 → 补一次投影到
        pending 冻结 target 再补 committed（boundary / started_at 取
        pending 冻结值，不取重放调用的新值）。"""
        self.save_task(max_quota_windows=2)
        self._append_pending(epoch_id=EPOCH_A, target_consumed=1)
        result = task_manager.record_quota_boundary_consumed(
            self.repo, TID, epoch_id=EPOCH_A,
            executable_boundary_id=BOUNDARY_B,  # 与 pending 冻结值不同
            resume_started_at="2026-09-03T09:00:00Z")
        self.assertEqual(result["consumed_quota_windows"], 1)
        self.assertEqual(result["executable_boundary_id"], BOUNDARY_A)
        self.assertEqual(result["resume_started_at"], RESUME_AT)
        self.assertEqual(self.consumed(), 1)
        committed = self.events("quota_boundary_consumed")
        self.assertEqual(len(committed), 1)
        self.assertEqual(committed[0]["executable_boundary_id"], BOUNDARY_A)
        self.assertEqual(len(self.events("quota_consumption_pending")), 1)

    def test_pending_state_contradiction_raises_zero_writes(self):
        """异常分支·state 领先：pending 冻结 target=1 而 state 已消费 2
        → TaskManagerError（中文，指明 pending/state 不一致），零写，
        pending 保留待人工裁决。"""
        self.save_task(max_quota_windows=3)
        self._append_pending(target_consumed=1)
        self.set_consumed(2)
        before = self.state_bytes()
        with self.assertRaises(task_manager.TaskManagerError) as ctx:
            self._consume()
        self.assertIn("pending", str(ctx.exception))
        self.assertEqual(self.state_bytes(), before)   # 零写
        self.assertEqual(self.events("quota_boundary_consumed"), [])
        self.assertEqual(len(self.events("quota_consumption_pending")), 1)

    def test_pending_state_gap_over_one_raises_zero_writes(self):
        """异常分支·差距 > 1：pending 冻结 target=4 而 state=0（落后超一
        窗）→ 同一异常分支，零写。"""
        self.save_task(max_quota_windows=4)
        self._append_pending(epoch_id=EPOCH_B, target_consumed=4)
        before = self.state_bytes()
        with self.assertRaises(task_manager.TaskManagerError) as ctx:
            task_manager.record_quota_boundary_consumed(
                self.repo, TID, epoch_id=EPOCH_B,
                executable_boundary_id=BOUNDARY_B,
                resume_started_at=RESUME_AT)
        self.assertIn("不一致", str(ctx.exception))
        self.assertEqual(self.state_bytes(), before)
        self.assertEqual(self.events("quota_boundary_consumed"), [])

    def test_conflicting_pending_targets_raise_zero_writes(self):
        """异常分支·pending 证据自相矛盾：同 epoch 第二条 pending 不同
        target（不应发生的形态）→ TaskManagerError 零写。"""
        self.save_task(max_quota_windows=3)
        self._append_pending(target_consumed=1)
        self._append_pending(target_consumed=2)
        before = self.state_bytes()
        with self.assertRaises(task_manager.TaskManagerError):
            self._consume()
        self.assertEqual(self.state_bytes(), before)
        self.assertEqual(self.events("quota_boundary_consumed"), [])


class MigrationCrashConsistencyTest(ConsumptionTestBase):
    """RH-03：migrate_quota_window_accounting 同款 write-ahead pending
    marker——marker 缺失后重跑只按 pending 冻结值补 marker，绝不从已
    更新 state 重算 legacy（迁移记录不失真）；异常形态零写。"""

    def _prepare(self):
        self.save_task(max_quota_windows=3)
        self.set_consumed(1)
        self.append_consumed_event(epoch_id=EPOCH_A)
        self.append_consumed_event(epoch_id=EPOCH_B)

    def test_migrate_txn_01_marker_failure_rerun_completes_from_pending(self):
        """MIGRATE-TXN-01（§5.4 修正后期望）：state save 成功 + marker
        追加失败 → 重跑只按 pending 补 marker、不二次上调、
        legacy_consumed / attributed 拆分与首次计算一致（legacy=1 而
        非已更新 state 的 2——记录不失真）。"""
        self._prepare()
        with mock.patch.object(
                journal, "append_event",
                side_effect=_flaky_append(journal.append_event,
                                          "quota_accounting_migrated")):
            with self.assertRaises(OSError):
                task_manager.migrate_quota_window_accounting(self.repo, TID)
        # pending 已冻结、marker 缺席、投影已落（save 成功）
        self.assertEqual(
            len(self.events("quota_accounting_migration_pending")), 1)
        self.assertEqual(self.events("quota_accounting_migrated"), [])
        self.assertEqual(self.consumed(), 2)
        # 重跑：按 pending 冻结值补 marker
        result = task_manager.migrate_quota_window_accounting(self.repo, TID)
        self.assertNotIn("idempotent", result)
        self.assertEqual(result["legacy_consumed"], 1)
        self.assertEqual(result["verified_boundary_ids"], [EPOCH_A, EPOCH_B])
        self.assertEqual(result["attributed_consumed"], 1)
        self.assertEqual(result["legacy_unattributed_consumed"], 0)
        self.assertEqual(result["new_consumed"], 2)
        marker = self.events("quota_accounting_migrated")
        self.assertEqual(len(marker), 1)
        self.assertEqual(marker[0]["legacy_consumed"], 1)      # 冻结值
        self.assertEqual(marker[0]["verified_boundary_ids"],
                         [EPOCH_A, EPOCH_B])
        self.assertEqual(marker[0]["attributed_consumed"], 1)  # 拆分不失真
        self.assertEqual(marker[0]["legacy_unattributed_consumed"], 0)
        # 不二次上调：consumed 仍 2、pending 不新增
        self.assertEqual(self.consumed(), 2)
        self.assertEqual(
            len(self.events("quota_accounting_migration_pending")), 1)

    def test_pending_saved_projection_failure_rerun_applies_projection(self):
        """pending 落 → save_state 抛（投影未落）→ 重跑补一次投影到
        new_consumed 再补 marker（new > legacy 且 state 仍为 legacy）。"""
        self._prepare()
        with mock.patch.object(state, "save_state",
                               side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                task_manager.migrate_quota_window_accounting(self.repo, TID)
        self.assertEqual(self.consumed(), 1)  # 投影未落
        self.assertEqual(
            len(self.events("quota_accounting_migration_pending")), 1)
        result = task_manager.migrate_quota_window_accounting(self.repo, TID)
        self.assertEqual(result["new_consumed"], 2)
        self.assertEqual(result["legacy_consumed"], 1)  # 冻结值不重算
        self.assertEqual(self.consumed(), 2)            # 投影恰补一次
        self.assertEqual(len(self.events("quota_accounting_migrated")), 1)
        self.assertEqual(
            len(self.events("quota_accounting_migration_pending")), 1)

    def test_pending_event_frozen_fields_and_write_ahead_order(self):
        """pending 六字段逐字（五数在 state 动写之前冻结）；new ==
        legacy 也落 pending；write-ahead 排序：pending 先于 marker。"""
        self.save_task(max_quota_windows=3)
        self.set_consumed(1)
        self.append_consumed_event(epoch_id=EPOCH_A)
        before = self.state_bytes()
        task_manager.migrate_quota_window_accounting(self.repo, TID)
        pendings = self.events("quota_accounting_migration_pending")
        self.assertEqual(len(pendings), 1)
        self.assertEqual(
            set(pendings[0]) - {"ts"},
            {"event", "legacy_consumed", "verified_boundary_ids",
             "attributed_consumed", "legacy_unattributed_consumed",
             "new_consumed"})
        self.assertEqual(pendings[0]["legacy_consumed"], 1)
        self.assertEqual(pendings[0]["verified_boundary_ids"], [EPOCH_A])
        self.assertEqual(pendings[0]["attributed_consumed"], 1)
        self.assertEqual(pendings[0]["legacy_unattributed_consumed"], 0)
        self.assertEqual(pendings[0]["new_consumed"], 1)
        names = [e.get("event") for e in journal.read_events(self.repo, TID)]
        self.assertLess(names.index("quota_accounting_migration_pending"),
                        names.index("quota_accounting_migrated"))
        # new == legacy：零 state 写（数字不变语义回归）
        self.assertEqual(self.state_bytes(), before)

    def test_pending_state_contradiction_raises_zero_writes(self):
        """异常分支：state 与 pending 冻结值矛盾（state 既非 legacy 也
        非 new）→ TaskManagerError 零写，pending 保留待人工裁决。"""
        self.save_task(max_quota_windows=3)
        journal.append_event(self.repo, TID, {
            "event": "quota_accounting_migration_pending",
            "legacy_consumed": 2, "verified_boundary_ids": [],
            "attributed_consumed": 0,
            "legacy_unattributed_consumed": 2, "new_consumed": 2})
        self.set_consumed(0)  # state(0) ∉ {legacy=2, new=2}
        before = self.state_bytes()
        with self.assertRaises(task_manager.TaskManagerError) as ctx:
            task_manager.migrate_quota_window_accounting(self.repo, TID)
        self.assertIn("pending", str(ctx.exception))
        self.assertEqual(self.state_bytes(), before)
        self.assertEqual(self.events("quota_accounting_migrated"), [])
        self.assertEqual(
            len(self.events("quota_accounting_migration_pending")), 1)

    def test_malformed_pending_raises_zero_writes(self):
        """异常分支·pending 形状损坏（new < legacy 违反 max 不变量）→
        TaskManagerError 零写。"""
        self.save_task(max_quota_windows=3)
        journal.append_event(self.repo, TID, {
            "event": "quota_accounting_migration_pending",
            "legacy_consumed": 2, "verified_boundary_ids": [],
            "attributed_consumed": 0,
            "legacy_unattributed_consumed": 2, "new_consumed": 1})
        before = self.state_bytes()
        with self.assertRaises(task_manager.TaskManagerError):
            task_manager.migrate_quota_window_accounting(self.repo, TID)
        self.assertEqual(self.state_bytes(), before)
        self.assertEqual(self.events("quota_accounting_migrated"), [])


# —— reconcile_wake_bridge_from_host（§22.5） ——

class ReconcileWakeBridgeFromHostTest(ConsumptionTestBase):
    """§22.5：纯账本桥对账——host_status 显式传入、只能降级/确认、
    绝不制造 armed。"""

    def test_armed_confirmed_by_active_host_zero_writes(self):
        """host=active + 活桥 → 原地确认（零写零事件，不重复记账）。"""
        self.save_task()
        self.arm_bridge()
        before = self.state_bytes()
        result = task_manager.reconcile_wake_bridge_from_host(
            self.repo, TID, host_status="active")
        self.assertFalse(result["reconciled"])
        self.assertEqual(result["bridge_status_before"], "armed")
        self.assertEqual(result["bridge_status"], "armed")
        self.assertEqual(result["activation_transport"], "armed")
        self.assertEqual(result["automation_id"], "cron-persist")
        self.assertEqual(self.events("wake_bridge_reconciled"), [])
        self.assertEqual(self.state_bytes(), before)

    def test_deleted_host_downgrades_armed_to_cancelled(self):
        """host=deleted + armed → 降级 cancelled（§22.5：已知已删除记
        cancelled），automation_id / boundary 记账保留。"""
        self.save_task()
        self.arm_bridge()
        result = task_manager.reconcile_wake_bridge_from_host(
            self.repo, TID, host_status="deleted",
            observed_at="2026-09-03T09:30:00Z")
        self.assertTrue(result["reconciled"])
        self.assertEqual(result["bridge_status_before"], "armed")
        self.assertEqual(result["bridge_status"], "cancelled")
        self.assertEqual(result["activation_transport"], "missing")
        st = state.load_state(self.repo, TID)
        bridge = st["continuation"]["wake_bridge"]
        self.assertEqual(bridge["status"], "cancelled")
        self.assertEqual(bridge["automation_id"], "cron-persist")  # 保留
        self.assertEqual(bridge["boundary_id"], BOUNDARY_A)        # 保留
        records = self.events("wake_bridge_reconciled")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["host_status"], "deleted")
        self.assertEqual(records[0]["from_status"], "armed")
        self.assertEqual(records[0]["to_status"], "cancelled")
        self.assertEqual(records[0]["automation_id"], "cron-persist")
        self.assertEqual(records[0]["observed_at"], "2026-09-03T09:30:00Z")

    def test_completed_host_downgrades_fired_to_stale(self):
        """host=completed + fired → 降级 stale（§22.5：completed 记
        stale），fired_at 留痕不抹。"""
        self.save_task()
        self.arm_bridge()
        task_manager.record_bridge_fired(
            self.repo, TID, fired_at="2026-09-01T22:05:00Z")
        result = task_manager.reconcile_wake_bridge_from_host(
            self.repo, TID, host_status="completed")
        self.assertTrue(result["reconciled"])
        self.assertEqual(result["bridge_status"], "stale")
        st = state.load_state(self.repo, TID)
        bridge = st["continuation"]["wake_bridge"]
        self.assertEqual(bridge["status"], "stale")
        self.assertEqual(bridge["fired_at"], "2026-09-01T22:05:00Z")
        self.assertEqual(bridge["automation_id"], "cron-persist")

    def test_journal_history_never_manufactures_armed(self):
        """§22.5 核心红线：非 armed 记账（none / cancelled）即使报
        active 也绝不升级——对账只能降级/确认；journal 历史单独不得
        制造 armed 桥。"""
        self.save_task()
        # 默认 none 桥 + host active
        before = self.state_bytes()
        result = task_manager.reconcile_wake_bridge_from_host(
            self.repo, TID, host_status="active")
        self.assertEqual(result["bridge_status"], "none")
        self.assertEqual(result["activation_transport"], "missing")
        self.assertEqual(self.state_bytes(), before)
        # cancelled 桥 + host active → 仍 cancelled
        self.arm_bridge()
        task_manager.reconcile_wake_bridge_from_host(
            self.repo, TID, host_status="deleted")
        before = self.state_bytes()
        result = task_manager.reconcile_wake_bridge_from_host(
            self.repo, TID, host_status="active")
        self.assertEqual(result["bridge_status"], "cancelled")
        self.assertFalse(result["reconciled"])
        self.assertEqual(self.state_bytes(), before)

    def test_unknown_host_zero_writes_even_for_armed(self):
        """host=unknown：无宿主事实——不能确认也不能改动（零写）。"""
        self.save_task()
        self.arm_bridge()
        before = self.state_bytes()
        result = task_manager.reconcile_wake_bridge_from_host(
            self.repo, TID, host_status="unknown")
        self.assertFalse(result["reconciled"])
        self.assertEqual(result["bridge_status"], "armed")
        self.assertEqual(self.events("wake_bridge_reconciled"), [])
        self.assertEqual(self.state_bytes(), before)

    def test_repeat_downgrade_is_idempotent_noop(self):
        """降级幂等：已 cancelled 再次报 deleted → 零写 + idempotent。"""
        self.save_task()
        self.arm_bridge()
        task_manager.reconcile_wake_bridge_from_host(
            self.repo, TID, host_status="deleted")
        before = self.state_bytes()
        replay = task_manager.reconcile_wake_bridge_from_host(
            self.repo, TID, host_status="deleted")
        self.assertTrue(replay["idempotent"])
        self.assertFalse(replay["reconciled"])
        self.assertEqual(replay["bridge_status"], "cancelled")
        self.assertEqual(len(self.events("wake_bridge_reconciled")), 1)
        self.assertEqual(self.state_bytes(), before)

    def test_invalid_host_status_valueerror_before_io(self):
        """host_status 越词汇 → ValueError（先于任何 I/O——任务缺失也
        不掩盖参数错误）。"""
        with self.assertRaises(ValueError):
            task_manager.reconcile_wake_bridge_from_host(
                self.repo, TID, host_status="deleted-by-user")
        with self.assertRaises(ValueError):
            task_manager.reconcile_wake_bridge_from_host(
                self.repo, TID, host_status=None)

    def test_invalid_observed_at_valueerror(self):
        self.save_task()
        with self.assertRaises(ValueError):
            task_manager.reconcile_wake_bridge_from_host(
                self.repo, TID, host_status="deleted",
                observed_at="not-a-timestamp")

    def test_missing_task_raises(self):
        with self.assertRaises(task_manager.TaskManagerError):
            task_manager.reconcile_wake_bridge_from_host(
                self.repo, TID, host_status="deleted")

    def test_zero_host_calls_pure_ledger(self):
        """§22.5 硬边界：helper 代码体（剥离 docstring）不出现任何宿主
        / CronList 概念的调用——纯账本。"""
        import inspect
        source = inspect.getsource(task_manager.reconcile_wake_bridge_from_host)
        code_body = source.split('"""', 2)[2]  # 剥离 docstring（注释可提及）
        for forbidden in ("CronList", "cron_list", "subprocess"):
            self.assertNotIn(forbidden, code_body)


if __name__ == "__main__":
    unittest.main()
