#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v2.2 C7 resume 消费接线单元测试（wu-22-C7 ①②；修正计划 §C7 / §15.1
消费事务点冻结 / §22.1 / §22.6 / QC-07）。

锚定对象：
  - §15.1：quota_boundary_consumed 唯一合法写入点 = Resume Controller
    的 resume commit point（转态 durable + mark 之后）；同一 epoch 绝
    不二次消费（幂等证据可修正投影）；
  - §22.1：manual / notify 永不消费；授权预闸（auto_resume ∈
    {auto_once, until_done} AND source=="user" AND remaining>0）不过 →
    零调用零副作用；
  - §C7 / 修正计划 C7：消费事件辅助证据只从当前 epoch 快照真实窗口
    派生（D10 形态 "kind:reset_at"，最早可解析窗口，无宽限；无可解析
    窗口跳过消费不虚构 §31）；RH-04 起事件字段记作
    representative_boundary_id（代表窗口身份；RH-04 前旧事件为 legacy
    键 executable_boundary_id，读侧双键兼容；consumption face 返回键
    名 executable_boundary_id 冻结不变）；epoch_id 恒为幂等 / 归属
    权威键；
  - §22.6：迁移先于新形态事件（先 migrate one-shot 再 record）；
  - QC-07 / 退出码契约（v2.2 C7 ②）：消费记账 OSError 自然上抛（证据
    丢失必须可见）→ CLI quota-resume 退出码 3 + {error, guidance}；
    三查竞态 TaskManagerError → consumption face 降级不抛（转态已
    durable）；legacy 未注册任务零消费键。

覆盖映射（规格报告要求逐条对应）：
    ① happy path 恰一次消费 + 事件冻结字段（HappyPathConsumptionTest）
    ② 迁移事件先于首条消费事件的 journal 顺序（同上 + 幂等命中恢复）
    ③ boundary 派生 D10 最早可解析窗 + 不可解析跳过（MultiWindow /
       UnparseableWindow）
    ④ 授权预闸矩阵 manual / notify / 非 user / 预算耗尽 → 零调用零副
       作用 + 中文 reason（PreGateMatrixTest）
    ⑤ 同 epoch 重放（任务已 executing）零二次消费（SameEpochReplayTest）
    ⑥ journal 证据在案的幂等命中 face（idempotent=True 零新建事件）
    ⑦ 三查竞态 TaskManagerError face 降级 / record 与 migrate 的
       OSError 上抛（RaceAndEvidenceLossTest）
    ⑧ legacy 未注册任务无 consumption 键（LegacyUnchangedTest）
    ⑨ CLI：退出码 3 分类 + guidance 面 / TaskManagerError 仍 1 /
       consumption 面透传（CliExitContractTest）
    ⑩ RH-03 write-ahead pending journal 序 + 幸存 pending 经 resume 链
       恢复闭合不双消费（WriteAheadJournalOrderTest，wu-rh03）

fixture：tempfile 仓库 + runtime.state 构造（scratch 任务目录，不碰
真实账本）；resolver / epoch / 记账全注入——零网络零宿主调用。仅
Python 3 标准库（unittest + tempfile），零第三方依赖。

运行：
    cd <repo_root> && python3 -m unittest tests.test_resume_consumption -v
"""

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "glm-conductor"))
from runtime import cli, execution_policy, journal, state, task_manager
from runtime.quota import epoch as quota_epoch
from runtime.quota import identity as quota_identity
from runtime.quota import resolver as quota_resolver

TID = "resume-consume-1a2b3c"
CONFIRMED_AT = "2026-09-03T00:00:00+00:00"
VERIFY_CMD = "python3 -m unittest tests.test_resume_consumption"

# 真实窗口夹具（§27 形状；epoch 身份由 quota_epoch 从窗口折算——绝不
# 手写指纹，与 tests/test_quota_subscription 同纪律）：
#   WINDOWS_A = 耗尽时刻的 epoch（reset 12:00Z，窗 EXHAUSTED）
#   WINDOWS_B = 窗口滚动后的新 epoch（reset 17:00Z，AVAILABLE →
#               executable=True 的恢复场景；D10 boundary = 最早可解析
#               窗 five_hour:2026-09-01T17:00:00Z）
WINDOWS_A = [{"kind": "five_hour", "status": "EXHAUSTED",
              "used_percent": 100.0, "remaining_percent": 0.0,
              "reset_at": "2026-09-01T12:00:00Z"}]
WINDOWS_B = [{"kind": "five_hour", "status": "AVAILABLE",
              "used_percent": 10.0, "remaining_percent": 90.0,
              "reset_at": "2026-09-01T17:00:00Z"}]
# 多窗：weekly reset 更晚、five_hour 更早——D10 取最早可解析窗
WINDOWS_MULTI = [{"kind": "weekly", "status": "AVAILABLE",
                  "used_percent": 5.0, "remaining_percent": 95.0,
                  "reset_at": "2026-09-05T00:00:00Z"},
                 {"kind": "five_hour", "status": "AVAILABLE",
                  "used_percent": 10.0, "remaining_percent": 90.0,
                  "reset_at": "2026-09-01T17:00:00Z"}]
# 全不可解析：epoch 身份可折算（unknown 占位）但无 D10 boundary（§31）
WINDOWS_OPAQUE = [{"kind": "five_hour", "status": "AVAILABLE",
                   "used_percent": 1.0, "remaining_percent": 99.0,
                   "reset_at": "not-a-timestamp"}]
EPOCH_OF_A = quota_epoch.epoch_id(WINDOWS_A)
EPOCH_OF_B = quota_epoch.epoch_id(WINDOWS_B)
EPOCH_OF_MULTI = quota_epoch.epoch_id(WINDOWS_MULTI)
EPOCH_OF_OPAQUE = quota_epoch.epoch_id(WINDOWS_OPAQUE)
BOUNDARY_OF_B = "five_hour:2026-09-01T17:00:00Z"

# resume_from_quota 冻结五键（legacy 输出零变化硬锚，同 C5b 口径）
RESUME_FROZEN_KEYS = ["recommended_resume_at", "resumed", "status",
                      "unit_recovery", "wake_budget_remaining"]
# C7 consumption face 冻结键（reason|error 按形态二选一附加）
CONSUMPTION_BASE_KEYS = ["consumed", "consumed_quota_windows", "epoch_id",
                         "executable_boundary_id", "idempotent",
                         "remaining_quota_windows"]


def make_state(**kwargs):
    """构造默认合法的完整状态 dict（solo 路由过全部不变量）。"""
    task_id = kwargs.pop("task_id", TID)
    goal = kwargs.pop("goal", "v2.2 C7 resume consumption 夹具任务")
    return state.new_task_state(task_id, goal, {"mode": "solo"}, **kwargs)


def make_unit(uid, status="waiting_quota"):
    """构造通过 §61 契约校验的最小 work unit dict。"""
    return {"id": uid, "objective": "C7 consumption unit %s" % uid,
            "status": status, "depends_on": [],
            "executor": "flash-implementer",
            "ownership": ["src/%s.py" % uid],
            "verification": [VERIFY_CMD]}


def fake_resolved(status):
    """resolve_quota_status 冻结四键的测试桩。"""
    return {"status": status, "source": "provider",
            "evaluated_at": "2026-09-01T12:00:00.000Z",
            "reason": "fixture"}


def fake_detail(status, windows, snapshot="auto"):
    """resolve_quota_detail 冻结四键的测试桩。"""
    if snapshot == "auto":
        snapshot = {"provider": "fixture",
                    "fetched_at": "2026-09-01T12:00:00.000Z",
                    "status": status, "windows": windows}
    return {"source": "provider", "status": status,
            "snapshot": snapshot,
            "fetched_at": "2026-09-01T12:00:00.000Z"}


class ConsumptionWiringCase(unittest.TestCase):
    """C7 消费接线公共装置：waiting 任务 + 订阅注册 + resolver 桩。"""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = tmp.name

    # —— 装置 ——

    def authorized_state(self, auto_resume="until_done",
                         max_quota_windows=2, source="user"):
        """waiting 任务 + 用户续跑授权（预闸三查的通过形态）。"""
        st = make_state(status="waiting_quota")
        st["work_units"] = [make_unit("wu-a")]
        st["execution_policy"] = execution_policy.set_resume_authorization(
            st["execution_policy"], auto_resume=auto_resume,
            max_quota_windows=max_quota_windows, source=source,
            confirmed_at=CONFIRMED_AT)
        return st

    def put_task(self, st):
        state.save_state(self.repo, st)
        return st

    def subscribe(self, epoch_id=EPOCH_OF_A, **kwargs):
        return task_manager.register_quota_subscription(
            self.repo, TID, epoch_id=epoch_id, **kwargs)

    def mark(self, epoch_id=EPOCH_OF_A):
        return task_manager.mark_activation_epoch(self.repo, TID,
                                                  epoch_id=epoch_id)

    def resume(self, resolved_status="AVAILABLE", windows=WINDOWS_B,
               detail=None, status=None):
        with mock.patch.object(
                quota_resolver, "resolve_quota_status",
                return_value=fake_resolved(resolved_status)), \
             mock.patch.object(
                 quota_resolver, "resolve_quota_detail",
                 return_value=detail if detail is not None
                 else fake_detail(resolved_status, windows)):
            return task_manager.resume_from_quota(self.repo, TID,
                                                  status=status)

    # —— 断言助手 ——

    def task_events(self):
        return journal.read_events(self.repo, TID)

    def consumed_events(self):
        return [e for e in self.task_events()
                if e.get("event") == "quota_boundary_consumed"]

    def migrated_events(self):
        return [e for e in self.task_events()
                if e.get("event") == "quota_accounting_migrated"]

    def advanced_events(self):
        return [e for e in journal.read_control_plane_events(self.repo)
                if e.get("event") == "quota_epoch_advanced"]

    def on_disk(self):
        return state.load_state(self.repo, TID)

    def continuity_consumed(self):
        continuity = self.on_disk()["execution_policy"]["continuity"]
        return continuity.get("consumed_quota_windows", 0)


# —— ①② happy path：恰一次消费 + 冻结事件字段 + 迁移先行 ——

class HappyPathConsumptionTest(ConsumptionWiringCase):
    """§15.1 commit point 消费：预闸过 → boundary 派生 → 迁移 → 记账。"""

    def _prepared(self, max_quota_windows=2):
        self.put_task(self.authorized_state(
            max_quota_windows=max_quota_windows))
        self.subscribe()
        self.mark()

    def test_happy_path_consumes_once_with_frozen_event_fields(self):
        result = (self._prepared(), self.resume())[1]
        self.assertTrue(result["resumed"])
        self.assertIn("consumption", result)  # additive 顶层面
        face = result["consumption"]
        self.assertEqual(sorted(face.keys()), sorted(CONSUMPTION_BASE_KEYS))
        self.assertTrue(face["consumed"])
        self.assertFalse(face["idempotent"])
        self.assertEqual(face["epoch_id"], EPOCH_OF_B)  # 幂等权威键=当前 epoch
        self.assertEqual(face["executable_boundary_id"], BOUNDARY_OF_B)
        self.assertEqual(face["consumed_quota_windows"], 1)
        self.assertEqual(face["remaining_quota_windows"], 1)
        events = self.consumed_events()
        self.assertEqual(len(events), 1)  # 恰一次
        event = events[0]
        self.assertEqual(event["epoch_id"], EPOCH_OF_B)
        # RH-04：事件字段面改记 representative_boundary_id（face 返回
        # 键名 executable_boundary_id 冻结不变，见上方 face 断言）
        self.assertEqual(event["representative_boundary_id"], BOUNDARY_OF_B)
        self.assertNotIn("executable_boundary_id", event)
        self.assertIsInstance(event["resume_started_at"], str)
        self.assertTrue(event["resume_started_at"])
        self.assertEqual(event["consumed"], 1)
        self.assertEqual(event["remaining"], 1)
        self.assertEqual(self.continuity_consumed(), 1)
        self.assertEqual(self.on_disk()["status"], "executing")

    def test_multi_window_boundary_is_earliest_parseable_d10(self):
        """D10 派生：多窗取最早可解析 reset 的 "kind:reset_at"（无宽限
        ——boundary 是被消费窗口自身身份，不是 wake 时刻）。"""
        self.put_task(self.authorized_state())
        self.subscribe()
        self.mark()
        result = self.resume(windows=WINDOWS_MULTI)
        face = result["consumption"]
        self.assertTrue(face["consumed"])
        self.assertEqual(face["epoch_id"], EPOCH_OF_MULTI)
        self.assertEqual(face["executable_boundary_id"], BOUNDARY_OF_B)

    def test_migration_precedes_first_consumption_in_journal_order(self):
        """§22.6：迁移先于新形态事件——同一 resume 内
        quota_accounting_migrated 在 quota_boundary_consumed 之前。"""
        self.put_task(self.authorized_state())
        self.subscribe()
        self.mark()
        self.resume()
        events = self.task_events()
        migrated = [i for i, e in enumerate(events)
                    if e.get("event") == "quota_accounting_migrated"]
        consumed = [i for i, e in enumerate(events)
                    if e.get("event") == "quota_boundary_consumed"]
        self.assertEqual(len(migrated), 1)
        self.assertEqual(len(consumed), 1)
        self.assertLess(migrated[0], consumed[0])

    def test_journal_evidence_preexisting_is_idempotent_hit(self):
        """§15.1 幂等证据语义（resume 层锚定）：journal 已有同 epoch
        消费证据（mark 后 state 被回滚的崩溃窗口）→ record 幂等命中，
        face idempotent=True，不新建第二条消费事件。注入证据刻意用
        RH-04 前旧形态（legacy 键 executable_boundary_id）——锚定
        record 幂等命中路径的双键兼容读（legacy 值回填 face 返回键）。"""
        self.put_task(self.authorized_state())
        self.subscribe()
        self.mark()
        journal.append_event(self.repo, TID, {
            "event": "quota_boundary_consumed", "epoch_id": EPOCH_OF_B,
            "executable_boundary_id": BOUNDARY_OF_B,
            "resume_started_at": "2026-09-03T08:00:00.000Z",
            "consumed": 1, "remaining": 1})
        result = self.resume()
        face = result["consumption"]
        self.assertTrue(face["consumed"])
        self.assertTrue(face["idempotent"])
        self.assertEqual(face["consumed_quota_windows"], 1)
        self.assertEqual(face["remaining_quota_windows"], 1)
        self.assertEqual(face["executable_boundary_id"], BOUNDARY_OF_B)
        self.assertEqual(len(self.consumed_events()), 1)  # 零新建


# —— ③ boundary 不可解析：§31 不虚构，跳过消费 ——

class UnparseableWindowTest(ConsumptionWiringCase):

    def test_unparseable_windows_skip_consumption_with_reason(self):
        self.put_task(self.authorized_state())
        self.subscribe(EPOCH_OF_OPAQUE)
        self.mark()
        result = self.resume(windows=WINDOWS_OPAQUE)
        # epoch 身份可折算（unknown 占位）→ 资格过、转态照常
        self.assertTrue(result["resumed"])
        face = result["consumption"]
        self.assertFalse(face["consumed"])
        self.assertIsNone(face["executable_boundary_id"])
        self.assertIn("不虚构", face["reason"])
        self.assertNotIn("error", face)
        # 跳过消费 = 零调用零副作用（迁移也不触发——无新形态事件落地）
        self.assertEqual(self.consumed_events(), [])
        self.assertEqual(self.migrated_events(), [])
        self.assertEqual(self.continuity_consumed(), 0)


# —— ④ 授权预闸矩阵：manual / notify / 非 user / 预算耗尽 ——

class PreGateMatrixTest(ConsumptionWiringCase):
    """§22.1：manual / notify 永不消费；预闸不过 = 零调用零副作用。"""

    def _prepared(self, **kwargs):
        self.put_task(self.authorized_state(**kwargs))
        self.subscribe()
        self.mark()

    def assert_pre_gate_blocked(self, result, reason_fragment,
                                expected_consumed=0):
        self.assertTrue(result["resumed"])  # 恢复照常（消费被预闸拦下）
        face = result["consumption"]
        self.assertFalse(face["consumed"])
        self.assertIn(reason_fragment, face["reason"])
        self.assertNotIn("error", face)
        self.assertIsNone(face["executable_boundary_id"])
        # 零调用：迁移与消费记账均未发生
        self.assertEqual(self.consumed_events(), [])
        self.assertEqual(self.migrated_events(), [])
        self.assertEqual(self.continuity_consumed(), expected_consumed)
        # mark 照常（转态 durable + 记账与消费彼此独立）
        self.assertTrue(result["subscription"]["mark"]["marked"])

    def test_manual_policy_never_consumes(self):
        self._prepared(auto_resume="manual", max_quota_windows=0)
        result = self.resume()
        self.assert_pre_gate_blocked(result, "manual")

    def test_notify_policy_never_consumes(self):
        # notify 授权族外（setter 不变量：notify 必须 max=0）——剩余 0
        # 与 notify 双双越闸，但 auto_resume 检查先行 → reason 指名 notify
        self._prepared(auto_resume="notify", max_quota_windows=0)
        result = self.resume()
        self.assert_pre_gate_blocked(result, "notify")

    def test_non_user_source_never_consumes(self):
        """source != "user" → 拒绝消费。save_state 的 §5.4 不变量使
        until_done+非 user 组合无法经合法路径落盘（resume 转态保存即
        拒），故该预闸分支是纯纵深防御（镜像 record 三查）——按模块
        既有惯例（_subscription_state_satisfied 等私有助手直测）在
        _resume_consumption 入口直测分支判定。"""
        st = self.authorized_state(auto_resume="until_done",
                                   max_quota_windows=1)
        self.put_task(st)  # 盘上保持合法（source=user）
        st["execution_policy"]["authorization"] = {
            "source": "default", "confirmed_at": None, "scope": "task"}
        face = task_manager._resume_consumption(self.repo, TID, st,
                                                EPOCH_OF_B)
        self.assertFalse(face["consumed"])
        self.assertIn("user", face["reason"])
        self.assertNotIn("error", face)
        self.assertIsNone(face["executable_boundary_id"])
        # 零调用零副作用：迁移与消费事件均未发生
        self.assertEqual(self.consumed_events(), [])
        self.assertEqual(self.migrated_events(), [])
        self.assertEqual(self.continuity_consumed(), 0)

    def test_budget_exhausted_never_consumes(self):
        """剩余 0（consumed == max，经盘上手改预置存量）→ 预闸预算查
        拒绝——§14.5 授权耗尽语义在消费预闸的镜像。"""
        self.put_task(self.authorized_state(auto_resume="until_done",
                                            max_quota_windows=1))
        st = self.on_disk()
        st["execution_policy"]["continuity"]["consumed_quota_windows"] = 1
        state.save_state(self.repo, st)
        self.subscribe()
        self.mark()
        result = self.resume()
        self.assert_pre_gate_blocked(result, "耗尽", expected_consumed=1)


# —— ⑤ 同 epoch 重放：零二次消费 ——

class SameEpochReplayTest(ConsumptionWiringCase):

    def test_already_executing_replay_zero_consumption(self):
        self.put_task(self.authorized_state())
        self.subscribe()
        self.mark()
        first = self.resume()
        self.assertTrue(first["resumed"])
        second = self.resume()
        # 任务已 executing：重复唤醒零转态，走不到消费段
        self.assertFalse(second["resumed"])
        self.assertNotIn("subscription", second)
        self.assertNotIn("consumption", second)
        self.assertEqual(len(self.consumed_events()), 1)  # 无第二条
        self.assertEqual(len(self.migrated_events()), 1)  # 迁移 one-shot
        self.assertEqual(self.continuity_consumed(), 1)
        self.assertEqual(len(self.advanced_events()), 2)  # 每 epoch 恰一次


# —— ⑩ RH-03 write-ahead pending marker（wu-rh03）：journal 序 + 恢复闭合 ——

class WriteAheadJournalOrderTest(ConsumptionWiringCase):
    """RH-03（修正计划 §5）：迁移与消费各自 write-ahead pending 先于
    可变投影 / committed；崩溃幸存的 pending 经 resume 链恢复闭合，恰
    +1 不多消费。故障语义的定点注入用例（CONSUME-TXN-01..05 /
    MIGRATE-TXN-01）在 tests/test_boundary_consumption——本类锚定接线
    层的组合序。"""

    def test_full_resume_journal_order_pending_precedes_projection_and_committed(self):
        self.put_task(self.authorized_state())
        self.subscribe()
        self.mark()
        self.resume()
        names = [e.get("event") for e in self.task_events()]
        # 迁移：pending 先于 marker
        self.assertLess(names.index("quota_accounting_migration_pending"),
                        names.index("quota_accounting_migrated"))
        # 迁移整体（pending + marker）先于消费段
        self.assertLess(names.index("quota_accounting_migrated"),
                        names.index("quota_consumption_pending"))
        # 消费：pending 先于 committed
        self.assertLess(names.index("quota_consumption_pending"),
                        names.index("quota_boundary_consumed"))
        # RH-04：pending 事件字段面 = representative_boundary_id（新键，
        # legacy 键 executable_boundary_id 不再出现在新事件中）
        pending = [e for e in self.task_events()
                   if e.get("event") == "quota_consumption_pending"][0]
        self.assertEqual(pending["representative_boundary_id"],
                         BOUNDARY_OF_B)
        self.assertNotIn("executable_boundary_id", pending)
        self.assertEqual(self.continuity_consumed(), 1)

    def test_resume_reconciles_surviving_pending_without_double_consumption(self):
        """崩溃现场（上一进程已 append pending、投影未落）穿越进程边界
        → resume 链按 pending 冻结 target 恢复闭合：consumed 恰 +1、
        journal 恰一条 pending + 一条 committed、无第二条 pending。注入
        pending 用 RH-04 后新形态（representative_boundary_id）——模拟
        当前写方产物的恢复闭合；legacy 键形态的闭合兼容在
        tests/test_boundary_consumption 的 RH-04 组锚定。"""
        self.put_task(self.authorized_state(max_quota_windows=2))
        self.subscribe()
        self.mark()
        journal.append_event(self.repo, TID, {
            "event": "quota_consumption_pending", "epoch_id": EPOCH_OF_B,
            "representative_boundary_id": BOUNDARY_OF_B,
            "target_consumed": 1,
            "resume_started_at": "2026-09-03T08:00:00.000Z"})
        result = self.resume()
        face = result["consumption"]
        self.assertTrue(face["consumed"])
        self.assertFalse(face["idempotent"])
        self.assertEqual(face["consumed_quota_windows"], 1)
        self.assertEqual(face["remaining_quota_windows"], 1)
        self.assertEqual(self.continuity_consumed(), 1)  # 恰 +1
        pendings = [e for e in self.task_events()
                    if e.get("event") == "quota_consumption_pending"]
        self.assertEqual(len(pendings), 1)
        self.assertEqual(pendings[0]["target_consumed"], 1)
        self.assertEqual(len(self.consumed_events()), 1)


# —— ⑦ 三查竞态与证据丢失（QC-07） ——

class RaceAndEvidenceLossTest(ConsumptionWiringCase):

    def _prepared(self):
        self.put_task(self.authorized_state())
        self.subscribe()
        self.mark()

    def test_three_check_race_degrades_face_without_raising(self):
        """预闸后竞态（record 内部三查机械强制拒绝）→ face 降级不抛：
        转态已 durable，恢复不被炸掉。"""
        self._prepared()
        err = "record_quota_boundary_consumed：窗口预算已耗尽（竞态）"
        with mock.patch.object(task_manager,
                               "record_quota_boundary_consumed",
                               side_effect=task_manager.TaskManagerError(
                                   err)):
            result = self.resume()
        self.assertTrue(result["resumed"])  # 恢复完成
        face = result["consumption"]
        self.assertFalse(face["consumed"])
        self.assertEqual(face["error"], err)
        self.assertNotIn("reason", face)
        self.assertEqual(face["epoch_id"], EPOCH_OF_B)
        self.assertEqual(face["executable_boundary_id"], BOUNDARY_OF_B)
        self.assertEqual(self.on_disk()["status"], "executing")
        self.assertEqual(self.consumed_events(), [])

    def test_record_oserror_propagates(self):
        """QC-07：消费证据写失败（OSError）自然上抛——证据丢失必须
        可见（与 mark 同款），绝不静默吞掉。"""
        self._prepared()
        with mock.patch.object(task_manager,
                               "record_quota_boundary_consumed",
                               side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.resume()
        # 转态与 mark 已 durable（OSError 发生在 commit point 末段）
        self.assertEqual(self.on_disk()["status"], "executing")
        self.assertEqual(len(self.advanced_events()), 2)
        self.assertEqual(self.consumed_events(), [])

    def test_migrate_oserror_propagates_before_record(self):
        """§22.6 迁移失败（OSError）上抛可见；迁移在 record 之前——
        record 不被调用（新形态事件绝不先于迁移落地）。"""
        self._prepared()
        with mock.patch.object(task_manager,
                               "migrate_quota_window_accounting",
                               side_effect=OSError("locked")), \
             mock.patch.object(task_manager,
                               "record_quota_boundary_consumed") as rec:
            with self.assertRaises(OSError):
                self.resume()
        rec.assert_not_called()
        self.assertEqual(self.consumed_events(), [])


# —— ⑧ legacy 未注册任务：无 consumption 键 ——

class LegacyUnchangedTest(ConsumptionWiringCase):

    def test_legacy_unregistered_no_consumption_key(self):
        """未注册（默认块）任务：恢复路径逐字不变——冻结五键、无
        subscription / consumption 键、明细解析零调用、零消费事件。"""
        self.put_task(make_state(status="waiting_quota"))
        with mock.patch.object(
                quota_resolver, "resolve_quota_detail") as detail_mock:
            result = task_manager.resume_from_quota(self.repo, TID,
                                                    status="AVAILABLE")
        detail_mock.assert_not_called()
        self.assertEqual(sorted(result.keys()), RESUME_FROZEN_KEYS)
        self.assertTrue(result["resumed"])
        self.assertEqual(self.consumed_events(), [])
        self.assertEqual(self.migrated_events(), [])


# —— ⑨ CLI 退出码契约（v2.2 C7 ②） ——

class CliExitContractTest(ConsumptionWiringCase):

    def run_cli(self, *args):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = cli.main(list(args))
        return code, json.loads(buffer.getvalue())

    def test_oserror_maps_to_exit_3_with_guidance_face(self):
        """恢复链 OSError（消费证据写失败）→ 退出码 3 + {error,
        guidance}（durable 转态可能已落 / 幂等重跑安全 / 查 journal）。"""
        self.put_task(self.authorized_state())
        self.subscribe()
        self.mark()
        with mock.patch.object(
                quota_resolver, "resolve_quota_status",
                return_value=fake_resolved("AVAILABLE")), \
             mock.patch.object(
                 quota_resolver, "resolve_quota_detail",
                 return_value=fake_detail("AVAILABLE", WINDOWS_B)), \
             mock.patch.object(task_manager,
                               "record_quota_boundary_consumed",
                               side_effect=OSError("disk full")):
            code, payload = self.run_cli("quota-resume", self.repo, TID)
        self.assertEqual(code, 3)
        self.assertIn("error", payload)
        self.assertIn("guidance", payload)
        self.assertIn("幂等", payload["guidance"])
        self.assertIn("journal", payload["guidance"])

    def test_task_missing_still_maps_to_exit_1(self):
        """退出码阶梯回归：TaskManagerError（任务缺失）仍 → 1，不被
        OSError 分类吞并。"""
        code, payload = self.run_cli("quota-resume", self.repo, TID)
        self.assertEqual(code, 1)
        self.assertIn("error", payload)
        self.assertNotIn("guidance", payload)

    def test_payload_carries_consumption_face_additive(self):
        """quota-resume 输出透传 consumption 面（additive 顶层面，
        既有冻结五键 + subscription 面不动）。"""
        self.put_task(self.authorized_state())
        self.subscribe()
        self.mark()
        with mock.patch.object(
                quota_resolver, "resolve_quota_status",
                return_value=fake_resolved("AVAILABLE")), \
             mock.patch.object(
                 quota_resolver, "resolve_quota_detail",
                 return_value=fake_detail("AVAILABLE", WINDOWS_B)):
            code, payload = self.run_cli("quota-resume", self.repo, TID)
        self.assertEqual(code, 0)
        self.assertTrue(payload["resumed"])
        face = payload["consumption"]
        self.assertTrue(face["consumed"])
        self.assertEqual(face["epoch_id"], EPOCH_OF_B)
        self.assertEqual(face["executable_boundary_id"], BOUNDARY_OF_B)
        self.assertEqual(sorted(payload.keys()),
                         sorted(RESUME_FROZEN_KEYS
                                + ["subscription", "consumption"]))


# —— v2.2.1 WU-221-B2（QuotaIdentity）：跨账号恢复 + 直接读者身份闸 ——

HASH_A = "a1b2c3d4e5f60718"
HASH_B = "b2c3d4e5f6071882"


def write_identity_cache(repo, *, status="AVAILABLE", windows=None,
                         provider_identity_hash=None):
    """按 resolver 缓存布局落盘 quota-cache.json（可携带身份指纹；
    provider_identity_hash=None 模拟 v2.2 legacy 无指纹缓存）。"""
    payload = {
        "provider": "fixture",
        "fetched_at": "2026-09-01T12:00:00.000Z",
        "status": status,
        "snapshot": {"provider": "fixture",
                     "fetched_at": "2026-09-01T12:00:00.000Z",
                     "status": status, "windows": windows},
    }
    if provider_identity_hash is not None:
        payload["provider_identity_hash"] = provider_identity_hash
    quota_resolver._save_cache(quota_resolver._cache_path(repo), payload)


class DirectReaderIdentityGateTest(ConsumptionWiringCase):
    """B2 面 6（b2-review carryover 收口）：绕过 resolver 层级的
    _load_cache 直接读者补上身份闸——异身份缓存视同无缓存（各走既有
    no-cache 路径），legacy 无指纹缓存保守信任（行为逐字不变）。"""

    def under_identity(self, identity):
        return mock.patch.object(quota_identity,
                                 "compute_provider_identity_hash",
                                 return_value=identity)

    def run_cli(self, *args):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = cli.main(list(args))
        return code, json.loads(buffer.getvalue())

    def _state(self):
        st = make_state(status="waiting_quota")
        st["work_units"] = [make_unit("wu-a")]
        return st

    def test_execution_phase_gate_foreign_cache_fails_open(self):
        write_identity_cache(self.repo, status="PRESSURE",
                             windows=WINDOWS_B,
                             provider_identity_hash=HASH_A)
        with self.under_identity(HASH_B):
            decision = task_manager._execution_phase_decision(
                self.repo, self._state(), 2)
        self.assertIsNone(decision)  # 视同无缓存：闸不激活（fail-open）

    def test_execution_phase_gate_legacy_cache_unchanged(self):
        write_identity_cache(self.repo, status="PRESSURE",
                             windows=WINDOWS_B,
                             provider_identity_hash=None)
        with self.under_identity(HASH_B):
            decision = task_manager._execution_phase_decision(
                self.repo, self._state(), 2)
        self.assertIsNotNone(decision)  # legacy 无指纹：保守信任照旧
        self.assertEqual(decision["provider_status"], "PRESSURE")

    def test_execution_phase_gate_same_identity_active(self):
        write_identity_cache(self.repo, status="PRESSURE",
                             windows=WINDOWS_B,
                             provider_identity_hash=HASH_A)
        with self.under_identity(HASH_A):
            decision = task_manager._execution_phase_decision(
                self.repo, self._state(), 2)
        self.assertIsNotNone(decision)  # 同身份：缓存正常进决策器
        self.assertEqual(decision["provider_status"], "PRESSURE")

    def test_recommended_resume_foreign_cache_none(self):
        # resume 的 recommended_resume_at 直接读者：异身份缓存 → None
        # 不虚构建议时刻；legacy 缓存照常折算
        write_identity_cache(self.repo, status="EXHAUSTED",
                             windows=WINDOWS_A,
                             provider_identity_hash=HASH_A)
        with self.under_identity(HASH_B):
            self.assertIsNone(
                task_manager._evaluation_from_refreshed_cache(self.repo))
        with self.under_identity(HASH_A):
            evaluation = task_manager._evaluation_from_refreshed_cache(
                self.repo)
        self.assertIsNotNone(evaluation)
        self.assertEqual(evaluation["status"], "EXHAUSTED")

    def test_transition_epoch_context_foreign_cache_none(self):
        # 转态点订阅折算直接读者：异身份缓存 → None → 不注册零副作用
        write_identity_cache(self.repo, status="EXHAUSTED",
                             windows=WINDOWS_A,
                             provider_identity_hash=HASH_A)
        with self.under_identity(HASH_B):
            self.assertIsNone(
                task_manager._epoch_context_at_transition(self.repo))

    def test_transition_epoch_context_carries_same_identity(self):
        write_identity_cache(self.repo, status="EXHAUSTED",
                             windows=WINDOWS_A,
                             provider_identity_hash=HASH_A)
        with self.under_identity(HASH_A):
            context = task_manager._epoch_context_at_transition(self.repo)
        self.assertIsNotNone(context)
        self.assertEqual(context["epoch_id"], EPOCH_OF_A)
        self.assertEqual(context["provider_identity_hash"], HASH_A)

    def test_transition_epoch_context_legacy_cache_no_identity_key(self):
        write_identity_cache(self.repo, status="EXHAUSTED",
                             windows=WINDOWS_A,
                             provider_identity_hash=None)
        with self.under_identity(HASH_A):
            context = task_manager._epoch_context_at_transition(self.repo)
        self.assertIsNotNone(context)
        self.assertNotIn("provider_identity_hash", context)

    def _waiting_task(self):
        self.put_task(self._state())

    def test_wake_plan_foreign_cache_behaves_as_no_cache(self):
        # CLI wake-plan 直接读者：异身份缓存 → 视同无缓存（plan 按
        # UNKNOWN fail-open：boundary_id=None）；legacy 缓存 → 行为不变
        self._waiting_task()
        write_identity_cache(self.repo, status="AVAILABLE",
                             windows=WINDOWS_B,
                             provider_identity_hash=HASH_A)
        with self.under_identity(HASH_B):
            code, payload = self.run_cli("wake-plan", self.repo, TID)
        self.assertEqual(code, 0)
        self.assertIsNone(payload["boundary_id"])

    def test_wake_plan_legacy_cache_unchanged(self):
        self._waiting_task()
        write_identity_cache(self.repo, status="AVAILABLE",
                             windows=WINDOWS_B,
                             provider_identity_hash=None)
        with self.under_identity(HASH_B):
            code, payload = self.run_cli("wake-plan", self.repo, TID)
        self.assertEqual(code, 0)
        self.assertEqual(payload["boundary_id"], BOUNDARY_OF_B)

    def test_wake_plan_same_identity_uses_cache(self):
        self._waiting_task()
        write_identity_cache(self.repo, status="AVAILABLE",
                             windows=WINDOWS_B,
                             provider_identity_hash=HASH_A)
        with self.under_identity(HASH_A):
            code, payload = self.run_cli("wake-plan", self.repo, TID)
        self.assertEqual(code, 0)
        self.assertEqual(payload["boundary_id"], BOUNDARY_OF_B)


class ResumeAccountSwitchTest(ConsumptionWiringCase):
    """B2 面 5/6 集成（恢复对账）：waiting_quota 任务在 A 名下中断、
    B 的会话恢复——A 的 epoch 记录按 not-ours 处理（保守等待），绝不
    盲目复用；消费事件与激活记账随当前身份落指纹。"""

    def resume_under_identity(self, identity, **kwargs):
        kwargs.setdefault("resolved_status", "AVAILABLE")
        kwargs.setdefault("windows", WINDOWS_B)
        with mock.patch.object(
                quota_resolver, "resolve_quota_status",
                return_value=fake_resolved(kwargs["resolved_status"])), \
             mock.patch.object(
                 quota_resolver, "resolve_quota_detail",
                 return_value=fake_detail(kwargs["resolved_status"],
                                          kwargs["windows"])), \
             mock.patch.object(quota_identity,
                               "compute_provider_identity_hash",
                               return_value=identity):
            return task_manager.resume_from_quota(self.repo, TID)

    def test_interrupted_under_a_not_reused_under_b(self):
        # A 名下注册 + 激活 + 中断；B 恢复：订阅门按异身份拦截（A 的
        # epoch 记录不是 B 的），任务保持 waiting_quota 零转态
        self.put_task(self.authorized_state())
        self.subscribe(EPOCH_OF_A, provider_identity_hash=HASH_A)
        task_manager.mark_activation_epoch(
            self.repo, TID, epoch_id=EPOCH_OF_A,
            provider_identity_hash=HASH_A)
        result = self.resume_under_identity(HASH_B)
        self.assertFalse(result["resumed"])
        face = result["subscription"]
        self.assertFalse(face["eligible"])
        self.assertTrue(any("其他 provider 身份" in r
                            for r in face["reasons"]))
        self.assertEqual(self.on_disk()["status"], "waiting_quota")
        self.assertNotIn("consumption", result)
        self.assertEqual(self.consumed_events(), [])

    def test_legacy_records_resume_normally_under_any_identity(self):
        # v2.2 legacy 记录（无指纹）在新身份下照常恢复——兼容性裁决
        self.put_task(self.authorized_state())
        self.subscribe(EPOCH_OF_A)
        st = self.on_disk()
        st["quota_subscription"]["last_activation_epoch_id"] = EPOCH_OF_A
        state.save_state(self.repo, st)
        result = self.resume_under_identity(HASH_B)
        self.assertTrue(result["resumed"])
        self.assertTrue(result["subscription"]["eligible"])
        self.assertTrue(result["consumption"]["consumed"])
        # 新记账事件携带 B 的当前身份（derive-and-emit）
        self.assertEqual(self.consumed_events()[-1]
                         ["provider_identity_hash"], HASH_B)
        self.assertEqual(self.advanced_events()[-1]
                         ["provider_identity_hash"], HASH_B)


if __name__ == "__main__":
    unittest.main()
