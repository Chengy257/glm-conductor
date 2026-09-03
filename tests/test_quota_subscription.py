# -*- coding: utf-8 -*-
"""Quota Subscription 状态面 + 纯资格判定 + 控制面 journal 单元测试
（v2.2 C5a，wu-22-C5a）。

锚定对象：修正计划 §14（Task 只订阅 quota，不再拥有 quota clock——
规格 adapted：§14 草图的 registered_epoch:17 是 int 序数示意，实现以
§10.1 epoch_id 字符串等值身份为准）/ §10.1（epoch_id = "glm:"+16hex）/
§13（控制面事件词汇）/ §QC-07（epoch 推进 → 恰一条 quota_epoch_advanced）
+ C4 reviewer P2 正解（primer 的 window_primed 从伪任务目录迁到控制面
journal .glm-conductor/quota/events.jsonl）。

覆盖映射（规格报告要求逐条对应）：
    ① state 面（DefaultBlockTest + ValidatorMatrixTest）：
        DEFAULT_QUOTA_SUBSCRIPTION 五键冻结形状 + 全新拷贝隔离；
        new_task_state 恒含默认块；validate_state 规则 8.9 全矩阵
        （legacy 无块合法 / 缺键按默认 / enabled 非 bool / epoch_id
        非 null 非 ^glm:[0-9a-f]{16}$ / minimum_state 四档词汇外 /
        continuation_mode 四枚举外 / 未知键忽略 / 错误路径前缀 /
        非法块 save_state 拒绝）
    ② 三 API（RegisterTest + EvaluateEligibilityTest +
        MarkActivationTest + SignatureFreezeTest）：
        register 幂等（同参重注册零写零事件）+ continuation_mode
        镜像 execution_policy.continuity.auto_resume + 任务 journal
        quota_subscription_registered；evaluate 纯判定全矩阵（未注册
        / 未启用 / 同 epoch / 不可执行 / minimum_state 不满足 / 全过；
        零副作用；不做 authorization/budget/reduce——归 C7）；
        mark 幂等（同 epoch 零事件零写）与推进（恰一次事件，
        previous_epoch_id 记账）
    ③ 控制面 journal（ControlPlaneJournalTest）：落点
        .glm-conductor/quota/events.jsonl、格式与 append_event 同构、
        读写容错（缺文件 [] / 坏行跳过 / ts 闸）、quota_epoch_advanced
        入 RECOMMENDED_EVENTS
    ④ primer 落点迁移（PrimerMigrationEndToEndTest）：window_primed
        落控制面 journal、事件字段零变化、伪任务目录不复存在、不产生
        tasks/quota-control-plane 目录

运行：
    cd <repo_root> && python3 -m unittest tests.test_quota_subscription -v
"""

import inspect
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "glm-conductor"))
from runtime import journal, state, task_manager
from runtime.quota import primer

TID = "sub-task-1a2b3c"
# §10.1 形状夹具（"glm:" + 16 位十六进制）
EPOCH_A = "glm:0123456789abcdef"
EPOCH_B = "glm:fedcba9876543210"

# ① 冻结默认块原文（§14 adapted 五键，逐字段不得增删改名）
FROZEN_DEFAULT_QUOTA_SUBSCRIPTION = {
    "enabled": False,
    "registered_epoch_id": None,
    "last_activation_epoch_id": None,
    "minimum_state": "AVAILABLE",
    "continuation_mode": "manual",
}

# task_manager 三 API 冻结返回键
REGISTER_RESULT_KEYS = ["enabled", "registered_epoch_id",
                        "last_activation_epoch_id", "minimum_state",
                        "continuation_mode", "idempotent"]
EVALUATE_RESULT_KEYS = ["eligible", "reasons"]
MARK_RESULT_KEYS = ["marked", "last_activation_epoch_id", "idempotent"]

PRIMER_EVENT_KEYS = ["ts", "event", "boundary_id", "provider_identity_hash",
                     "materialized", "executable", "tokens", "latency_ms",
                     "idempotent"]


def make_state(**kwargs):
    """构造默认合法的完整状态 dict（solo 路由过全部不变量）。"""
    task_id = kwargs.pop("task_id", TID)
    goal = kwargs.pop("goal", "v2.2 C5a quota subscription 夹具任务")
    return state.new_task_state(task_id, goal, {"mode": "solo"}, **kwargs)


def save_task(repo, st):
    """落盘任务状态（返回路径）。"""
    return state.save_state(repo, st)


class SubscriptionCase(unittest.TestCase):
    """公共装置：tempfile scratch 仓库（绝不触碰仓库内真实账本）。"""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = tmp.name

    # —— 断言助手 ——

    def put_task(self, st=None):
        """落盘一个默认合法任务（可传入改写后的 state）。"""
        st = st if st is not None else make_state()
        save_task(self.repo, st)
        return st

    def task_events(self):
        return journal.read_events(self.repo, TID)

    def control_events(self):
        return journal.read_control_plane_events(self.repo)

    def state_bytes(self):
        path = state.state_path(self.repo, TID)
        with open(path, "rb") as handle:
            return handle.read()

    def subscription(self):
        st = state.load_state(self.repo, TID)
        return st.get("quota_subscription")


# —— ① state 面：默认块与冻结形状 ——

class DefaultBlockTest(SubscriptionCase):

    def test_frozen_schema_exact(self):
        # §14 adapted 五键逐字段相等（enabled=False 未订阅初始态；epoch
        # 身份键为 null；阈值 AVAILABLE；续跑模式 manual——与
        # default_execution_policy 的 continuity.auto_resume 同源）
        self.assertEqual(state.DEFAULT_QUOTA_SUBSCRIPTION,
                         FROZEN_DEFAULT_QUOTA_SUBSCRIPTION)
        self.assertEqual(state.default_quota_subscription(),
                         FROZEN_DEFAULT_QUOTA_SUBSCRIPTION)

    def test_fresh_copy_each_call(self):
        first = state.default_quota_subscription()
        first["enabled"] = True
        first["minimum_state"] = "EXHAUSTED"
        self.assertEqual(state.default_quota_subscription(),
                         FROZEN_DEFAULT_QUOTA_SUBSCRIPTION)

    def test_new_task_state_contains_default_block_and_valid(self):
        st = make_state()
        self.assertEqual(st["quota_subscription"],
                         FROZEN_DEFAULT_QUOTA_SUBSCRIPTION)
        self.assertEqual(state.validate_state(st), [])
        # save/load 往返保形
        save_task(self.repo, st)
        loaded = state.load_state(self.repo, TID)
        self.assertEqual(loaded["quota_subscription"],
                         FROZEN_DEFAULT_QUOTA_SUBSCRIPTION)

    def test_frozen_vocabularies(self):
        # 四档阈值词汇（供比较的词汇序本地冻结：索引越小档位越高）与
        # auto_resume 四枚举（镜像 execution_policy 词汇）
        self.assertEqual(state.QUOTA_SUBSCRIPTION_MINIMUM_STATES,
                         ("AVAILABLE", "PRESSURE", "DRAINING", "EXHAUSTED"))
        self.assertEqual(state.QUOTA_SUBSCRIPTION_CONTINUATION_MODES,
                         ("manual", "notify", "auto_once", "until_done"))

    def test_is_quota_epoch_id_shape_gate(self):
        # §10.1 唯一判定入口：^glm:[0-9a-f]{16}$
        for good in (EPOCH_A, EPOCH_B, "glm:ffffffffffffffff"):
            self.assertTrue(state.is_quota_epoch_id(good), good)
        for bad in (None, 17, "", "17", "glm:", "glm:0123456789abcdef0",
                    "glm:0123456789abcde", "GLM:0123456789ABCDEF",
                    "glm:0123456789abcdeg", "0123456789abcdef"):
            self.assertFalse(state.is_quota_epoch_id(bad), repr(bad))


# —— ① state 面：validator 全矩阵（规则 8.9） ——

class ValidatorMatrixTest(SubscriptionCase):

    def validate_block(self, block):
        st = make_state()
        st["quota_subscription"] = block
        return state.validate_state(st)

    def block_errors(self, block):
        return [e for e in self.validate_block(block)
                if e.startswith("quota_subscription")]

    def test_legacy_without_block_legal(self):
        st = make_state()
        del st["quota_subscription"]
        self.assertEqual(state.validate_state(st), [])
        save_task(self.repo, st)
        self.assertIsNone(state.load_state(self.repo, TID)
                          .get("quota_subscription"))

    def test_missing_keys_legal_per_default(self):
        # 缺键按默认解释（存在才校验）——单键块全部合法
        for block in ({}, {"enabled": True},
                      {"registered_epoch_id": EPOCH_A},
                      {"last_activation_epoch_id": EPOCH_B},
                      {"minimum_state": "DRAINING"},
                      {"continuation_mode": "until_done"}):
            self.assertEqual(self.block_errors(block), [], repr(block))

    def test_enabled_must_be_bool(self):
        for bad in ("true", 1, 0, None, [True], {"on": True}):
            errors = self.block_errors({"enabled": bad})
            self.assertEqual(len(errors), 1, repr(bad))
            self.assertIn("quota_subscription.enabled", errors[0])
            self.assertIn("布尔", errors[0])
        for good in (True, False):
            self.assertEqual(self.block_errors({"enabled": good}), [])

    def test_epoch_id_keys_shape_gate(self):
        for key in ("registered_epoch_id", "last_activation_epoch_id"):
            self.assertEqual(self.block_errors({key: None}), [])
            self.assertEqual(self.block_errors({key: EPOCH_A}), [])
            for bad in (17, "17", "glm:XYZ", "glm:0123456789abcdef0",
                        "GLM:0123456789abcdef", "epoch-17", ""):
                errors = self.block_errors({key: bad})
                self.assertEqual(len(errors), 1, repr(bad))
                self.assertIn("quota_subscription.%s" % key, errors[0])

    def test_minimum_state_vocabulary_gate(self):
        for good in state.QUOTA_SUBSCRIPTION_MINIMUM_STATES:
            self.assertEqual(
                self.block_errors({"minimum_state": good}), [])
        # 词汇外值（含观测面 UNKNOWN 与小写变体）→ 错误；None 亦拒
        # （枚举无 null 空档，缺省语义靠缺键表达）
        for bad in ("UNKNOWN", "available", "BLOCKED", None, 42):
            errors = self.block_errors({"minimum_state": bad})
            self.assertEqual(len(errors), 1, repr(bad))
            self.assertIn("quota_subscription.minimum_state", errors[0])

    def test_continuation_mode_vocabulary_gate(self):
        for good in state.QUOTA_SUBSCRIPTION_CONTINUATION_MODES:
            self.assertEqual(
                self.block_errors({"continuation_mode": good}), [])
        for bad in ("always", "auto", "AUTO_ONCE", None, 7):
            errors = self.block_errors({"continuation_mode": bad})
            self.assertEqual(len(errors), 1, repr(bad))
            self.assertIn("quota_subscription.continuation_mode", errors[0])

    def test_unknown_keys_ignored(self):
        block = dict(FROZEN_DEFAULT_QUOTA_SUBSCRIPTION)
        block["future_key"] = {"anything": 1}
        self.assertEqual(self.block_errors(block), [])

    def test_non_dict_block_rejected(self):
        for bad in (None, "sub", 17, []):
            st = make_state()
            st["quota_subscription"] = bad
            errors = state.validate_state(st)
            self.assertIn("quota_subscription 必须是 JSON 对象", errors)

    def test_errors_aggregate_not_short_circuit(self):
        # 多键同坏 → 聚合全部错误不短路
        errors = self.block_errors({"enabled": "yes",
                                    "minimum_state": "UNKNOWN",
                                    "continuation_mode": "always",
                                    "registered_epoch_id": "17"})
        self.assertEqual(len(errors), 4)

    def test_save_state_rejects_illegal_block(self):
        st = make_state()
        st["quota_subscription"]["enabled"] = "true"
        with self.assertRaises(ValueError) as ctx:
            save_task(self.repo, st)
        self.assertIn("quota_subscription.enabled", str(ctx.exception))
        # 非法块未落盘（先校验后写盘）
        self.assertIsNone(state.load_state(self.repo, TID))


# —— ② register_quota_subscription ——

class RegisterTest(SubscriptionCase):

    def register(self, **kwargs):
        kwargs.setdefault("epoch_id", EPOCH_A)
        return task_manager.register_quota_subscription(self.repo, TID,
                                                        **kwargs)

    def test_first_register_writes_block_and_task_journal(self):
        self.put_task()
        result = self.register(minimum_state="PRESSURE",
                               continuation_mode="until_done")
        self.assertEqual(sorted(result.keys()), sorted(REGISTER_RESULT_KEYS))
        self.assertFalse(result["idempotent"])
        self.assertTrue(result["enabled"])
        self.assertEqual(result["registered_epoch_id"], EPOCH_A)
        self.assertEqual(result["minimum_state"], "PRESSURE")
        self.assertEqual(result["continuation_mode"], "until_done")
        block = self.subscription()
        self.assertEqual(block["enabled"], True)
        self.assertEqual(block["registered_epoch_id"], EPOCH_A)
        self.assertEqual(block["minimum_state"], "PRESSURE")
        # last_activation_epoch_id 不由 register 触碰
        self.assertIsNone(block["last_activation_epoch_id"])
        registered = [e for e in self.task_events()
                      if e.get("event") == "quota_subscription_registered"]
        self.assertEqual(len(registered), 1)
        self.assertEqual(registered[0]["registered_epoch_id"], EPOCH_A)
        self.assertEqual(registered[0]["minimum_state"], "PRESSURE")
        self.assertEqual(registered[0]["continuation_mode"], "until_done")
        # 正常任务事件落在任务 journal，不进控制面 journal
        self.assertEqual(self.control_events(), [])

    def test_same_params_reregister_zero_write_zero_event(self):
        self.put_task()
        self.register()
        bytes_before = self.state_bytes()
        events_before = len(self.task_events())
        again = self.register()
        self.assertTrue(again["idempotent"])
        self.assertEqual(self.state_bytes(), bytes_before)  # 零写
        self.assertEqual(len(self.task_events()), events_before)  # 零事件

    def test_changed_params_update_and_journal(self):
        self.put_task()
        self.register()
        updated = self.register(epoch_id=EPOCH_B, minimum_state="DRAINING")
        self.assertFalse(updated["idempotent"])
        self.assertEqual(updated["registered_epoch_id"], EPOCH_B)
        self.assertEqual(updated["minimum_state"], "DRAINING")
        registered = [e for e in self.task_events()
                      if e.get("event") == "quota_subscription_registered"]
        self.assertEqual(len(registered), 2)
        self.assertEqual(registered[-1]["registered_epoch_id"], EPOCH_B)
        # 更新注册不触碰激活记录
        self.assertIsNone(self.subscription()["last_activation_epoch_id"])

    def test_continuation_mode_mirrors_execution_policy(self):
        # 缺省镜像 execution_policy.continuity.auto_resume（§14 同一
        # 事实源）：until_done + user source 的合法授权块
        st = make_state()
        st["execution_policy"]["continuity"]["auto_resume"] = "until_done"
        st["execution_policy"]["continuity"]["max_quota_windows"] = 2
        st["execution_policy"]["authorization"]["source"] = "user"
        st["execution_policy"]["authorization"]["confirmed_at"] = \
            "2026-09-01T00:00:00+00:00"
        self.put_task(st)
        result = self.register()
        self.assertEqual(result["continuation_mode"], "until_done")

    def test_continuation_mode_default_manual_for_legacy_policy(self):
        # 默认 policy（manual）→ 镜像落 manual
        self.put_task()
        self.assertEqual(self.register()["continuation_mode"], "manual")

    def test_legacy_task_without_block_gets_block(self):
        st = make_state()
        del st["quota_subscription"]
        self.put_task(st)
        result = self.register()
        self.assertFalse(result["idempotent"])
        block = self.subscription()
        self.assertEqual(block["enabled"], True)
        self.assertEqual(block["registered_epoch_id"], EPOCH_A)

    def test_param_validation_before_io(self):
        # 参数校验先于 I/O：非法参数 → ValueError，且零落盘零事件
        with self.assertRaises(ValueError):
            self.register(epoch_id="17")
        with self.assertRaises(ValueError):
            self.register(epoch_id="glm:NOPE")
        with self.assertRaises(ValueError):
            self.register(minimum_state="UNKNOWN")
        with self.assertRaises(ValueError):
            self.register(continuation_mode="always")
        self.assertFalse(os.path.exists(
            state.state_path(self.repo, TID).parent))

    def test_missing_task_raises_task_manager_error(self):
        with self.assertRaises(task_manager.TaskManagerError):
            self.register()


# —— ② evaluate_subscription_eligibility ——

def evaluate(case, **kwargs):
    kwargs.setdefault("current_epoch_id", EPOCH_B)
    kwargs.setdefault("current_executable", True)
    kwargs.setdefault("provider_status", "AVAILABLE")
    return task_manager.evaluate_subscription_eligibility(
        case.repo, TID, **kwargs)


class EvaluateEligibilityTest(SubscriptionCase):

    def evaluate(self, **kwargs):
        return evaluate(self, **kwargs)

    def test_unregistered_default_block_ineligible(self):
        # legacy 无块 → 按默认块解释：未注册 + 未启用
        self.put_task()
        result = self.evaluate()
        self.assertEqual(sorted(result.keys()), sorted(EVALUATE_RESULT_KEYS))
        self.assertFalse(result["eligible"])
        text = "；".join(result["reasons"])
        self.assertIn("未注册", text)
        self.assertIn("未启用", text)

    def test_disabled_ineligible(self):
        self.put_task()
        task_manager.register_quota_subscription(self.repo, TID,
                                                 epoch_id=EPOCH_A)
        st = state.load_state(self.repo, TID)
        st["quota_subscription"]["enabled"] = False
        save_task(self.repo, st)
        result = self.evaluate()
        self.assertFalse(result["eligible"])
        self.assertTrue(any("未启用" in r for r in result["reasons"]))

    def test_same_epoch_ineligible_qc07(self):
        # 同 epoch（last_activation == current）→ 无资格（QC-07 每
        # epoch 恰一次；字符串等值比较，无 int 序数）
        self.put_task()
        task_manager.register_quota_subscription(self.repo, TID,
                                                 epoch_id=EPOCH_A)
        task_manager.mark_activation_epoch(self.repo, TID, epoch_id=EPOCH_B)
        result = self.evaluate(current_epoch_id=EPOCH_B)
        self.assertFalse(result["eligible"])
        self.assertTrue(any("已激活" in r for r in result["reasons"]))
        # 新 epoch → 该原因消失
        result = self.evaluate(current_epoch_id=EPOCH_A)
        self.assertTrue(all("已激活" not in r for r in result["reasons"]))

    def test_not_executable_ineligible(self):
        self.put_task()
        task_manager.register_quota_subscription(self.repo, TID,
                                                 epoch_id=EPOCH_A)
        result = self.evaluate(current_epoch_id=EPOCH_A,
                               current_executable=False)
        self.assertFalse(result["eligible"])
        self.assertTrue(any("不可执行" in r for r in result["reasons"]))

    def test_minimum_state_not_met_ineligible(self):
        self.put_task()
        task_manager.register_quota_subscription(self.repo, TID,
                                                 epoch_id=EPOCH_A,
                                                 minimum_state="AVAILABLE")
        result = self.evaluate(current_epoch_id=EPOCH_A,
                               provider_status="PRESSURE")
        self.assertFalse(result["eligible"])
        self.assertTrue(any("未达到" in r for r in result["reasons"]))
        # 观测面 UNKNOWN 不在订阅阈值词汇内 → 保守不满足（不猜测折算）
        result = self.evaluate(current_epoch_id=EPOCH_A,
                               provider_status="UNKNOWN")
        self.assertFalse(result["eligible"])

    def test_tier_order_frozen(self):
        # 四档序 AVAILABLE > PRESSURE > DRAINING > EXHAUSTED：达到档位
        # 及以上（更优或相等）即满足
        self.assertEqual(
            task_manager._QUOTA_SUBSCRIPTION_STATE_RANK,
            {"AVAILABLE": 0, "PRESSURE": 1, "DRAINING": 2, "EXHAUSTED": 3})
        satisfied = task_manager._subscription_state_satisfied
        self.assertTrue(satisfied("AVAILABLE", "AVAILABLE"))
        self.assertTrue(satisfied("AVAILABLE", "DRAINING"))
        self.assertTrue(satisfied("PRESSURE", "EXHAUSTED"))
        self.assertTrue(satisfied("EXHAUSTED", "EXHAUSTED"))
        self.assertFalse(satisfied("DRAINING", "PRESSURE"))
        self.assertFalse(satisfied("EXHAUSTED", "AVAILABLE"))
        self.assertFalse(satisfied("UNKNOWN", "AVAILABLE"))
        self.assertFalse(satisfied("AVAILABLE", None))

    def test_all_pass_eligible_with_no_reasons(self):
        self.put_task()
        task_manager.register_quota_subscription(self.repo, TID,
                                                 epoch_id=EPOCH_A,
                                                 minimum_state="PRESSURE")
        task_manager.mark_activation_epoch(self.repo, TID, epoch_id=EPOCH_B)
        result = self.evaluate(current_epoch_id=EPOCH_A,
                               current_executable=True,
                               provider_status="PRESSURE")
        self.assertTrue(result["eligible"])
        self.assertEqual(result["reasons"], [])

    def test_pure_decision_zero_side_effects(self):
        # 纯判定：零转态零写零事件（state 字节与两侧 journal 均不变）
        self.put_task()
        task_manager.register_quota_subscription(self.repo, TID,
                                                 epoch_id=EPOCH_A)
        bytes_before = self.state_bytes()
        task_events_before = len(self.task_events())
        control_events_before = len(self.control_events())
        self.evaluate(current_epoch_id=EPOCH_A)  # 不满足也零副作用
        self.evaluate(current_epoch_id=EPOCH_A)  # 满足也零副作用
        self.assertEqual(self.state_bytes(), bytes_before)
        self.assertEqual(len(self.task_events()), task_events_before)
        self.assertEqual(len(self.control_events()),
                         control_events_before)

    def test_missing_task_raises_task_manager_error(self):
        with self.assertRaises(task_manager.TaskManagerError):
            evaluate(self)

    def test_bad_current_epoch_id_valueerror_before_io(self):
        self.put_task()
        for bad in ("17", "glm:XYZ", "", None):
            with self.assertRaises(ValueError):
                self.evaluate(current_epoch_id=bad)


# —— ② mark_activation_epoch ——

class MarkActivationTest(SubscriptionCase):

    def mark(self, epoch_id=EPOCH_A):
        return task_manager.mark_activation_epoch(self.repo, TID,
                                                  epoch_id=epoch_id)

    def advanced_events(self):
        return [e for e in self.control_events()
                if e.get("event") == "quota_epoch_advanced"]

    def test_first_mark_writes_state_and_control_plane_event(self):
        self.put_task()
        result = self.mark(epoch_id=EPOCH_A)
        self.assertEqual(sorted(result.keys()), sorted(MARK_RESULT_KEYS))
        self.assertTrue(result["marked"])
        self.assertFalse(result["idempotent"])
        self.assertEqual(result["last_activation_epoch_id"], EPOCH_A)
        self.assertEqual(self.subscription()["last_activation_epoch_id"],
                         EPOCH_A)
        events = self.advanced_events()
        self.assertEqual(len(events), 1)  # QC-07：恰一条
        self.assertEqual(events[0]["task_id"], TID)
        self.assertEqual(events[0]["epoch_id"], EPOCH_A)
        self.assertIsNone(events[0]["previous_epoch_id"])
        # 控制面事件不进任务 journal
        self.assertEqual(
            [e for e in self.task_events()
             if e.get("event") == "quota_epoch_advanced"], [])

    def test_same_epoch_remark_zero_write_zero_event(self):
        # QC-07 幂等闸：同 epoch_id 重标 → 零写零事件
        self.put_task()
        self.mark(epoch_id=EPOCH_A)
        bytes_before = self.state_bytes()
        events_before = len(self.control_events())
        again = self.mark(epoch_id=EPOCH_A)
        self.assertFalse(again["marked"])
        self.assertTrue(again["idempotent"])
        self.assertEqual(self.state_bytes(), bytes_before)
        self.assertEqual(len(self.control_events()), events_before)

    def test_advance_writes_exactly_one_new_event(self):
        self.put_task()
        self.mark(epoch_id=EPOCH_A)
        again = self.mark(epoch_id=EPOCH_B)
        self.assertTrue(again["marked"])
        self.assertFalse(again["idempotent"])
        events = self.advanced_events()
        self.assertEqual(len(events), 2)  # 每 epoch 恰一次
        self.assertEqual(events[-1]["epoch_id"], EPOCH_B)
        self.assertEqual(events[-1]["previous_epoch_id"], EPOCH_A)
        self.assertEqual(self.subscription()["last_activation_epoch_id"],
                         EPOCH_B)

    def test_legacy_task_without_block_gets_activation_only(self):
        # legacy 缺块：按默认块落位再记账；enabled / registered 不由
        # mark 触碰（订阅与否归 register）
        st = make_state()
        del st["quota_subscription"]
        self.put_task(st)
        self.mark(epoch_id=EPOCH_A)
        block = self.subscription()
        self.assertEqual(block["last_activation_epoch_id"], EPOCH_A)
        self.assertEqual(block["enabled"],
                         FROZEN_DEFAULT_QUOTA_SUBSCRIPTION["enabled"])
        self.assertIsNone(block["registered_epoch_id"])

    def test_param_validation_and_missing_task(self):
        with self.assertRaises(ValueError):
            self.mark(epoch_id="epoch-17")
        with self.assertRaises(task_manager.TaskManagerError):
            self.mark()


# —— ② 三 API 冻结签名 ——

class SignatureFreezeTest(unittest.TestCase):
    """wu-22-C5a 规格 INTERFACES 原文（本单元不越界改签名）。"""

    def test_register_signature_frozen(self):
        signature = inspect.signature(
            task_manager.register_quota_subscription)
        self.assertEqual(list(signature.parameters),
                         ["repo_root", "task_id", "epoch_id",
                          "minimum_state", "continuation_mode"])
        self.assertEqual(
            signature.parameters["minimum_state"].default, "AVAILABLE")
        self.assertEqual(
            signature.parameters["continuation_mode"].default, None)
        for name in ("epoch_id", "minimum_state", "continuation_mode"):
            self.assertEqual(signature.parameters[name].kind,
                             inspect.Parameter.KEYWORD_ONLY)

    def test_evaluate_signature_frozen(self):
        signature = inspect.signature(
            task_manager.evaluate_subscription_eligibility)
        self.assertEqual(list(signature.parameters),
                         ["repo_root", "task_id", "current_epoch_id",
                          "current_executable", "provider_status"])
        for name in ("current_epoch_id", "current_executable",
                     "provider_status"):
            self.assertEqual(signature.parameters[name].kind,
                             inspect.Parameter.KEYWORD_ONLY)

    def test_mark_signature_frozen(self):
        signature = inspect.signature(
            task_manager.mark_activation_epoch)
        self.assertEqual(list(signature.parameters),
                         ["repo_root", "task_id", "epoch_id"])
        self.assertEqual(signature.parameters["epoch_id"].kind,
                         inspect.Parameter.KEYWORD_ONLY)


# —— ③ 控制面 journal ——

class ControlPlaneJournalTest(SubscriptionCase):

    def test_path_layout(self):
        # 与 watcher.json / primer.json 同层（.glm-conductor/quota/）
        self.assertEqual(
            journal.control_plane_journal_path(self.repo),
            Path(self.repo) / ".glm-conductor" / "quota" / "events.jsonl")

    def test_read_missing_returns_empty(self):
        self.assertEqual(journal.read_control_plane_events(self.repo), [])

    def test_append_creates_dirs_and_returns_record_with_ts(self):
        record = journal.append_control_plane_event(
            self.repo, {"event": "quota_epoch_advanced", "task_id": TID,
                        "epoch_id": EPOCH_A})
        self.assertEqual(record["event"], "quota_epoch_advanced")
        self.assertIsInstance(record["ts"], str)
        self.assertTrue(
            journal.control_plane_journal_path(self.repo).is_file())
        self.assertEqual(journal.read_control_plane_events(self.repo),
                         [record])

    def test_format_isomorphic_to_append_event(self):
        # 单行 JSON、UTF-8、ts 首键、\n 结尾（与 append_event 逐字同构）
        journal.append_control_plane_event(
            self.repo, {"event": "quota_epoch_advanced", "z": 1, "a": 2},
            ts="2026-09-03T12:00:00.000+00:00")
        raw = journal.control_plane_journal_path(self.repo).read_bytes()
        self.assertTrue(raw.endswith(b"\n"))
        self.assertEqual(raw.count(b"\n"), 1)
        line = raw.decode("utf-8").strip()
        self.assertEqual(
            list(json.loads(line).keys()),
            ["ts", "event", "z", "a"])

    def test_append_validation_same_as_append_event(self):
        # 结构性错误在任何 I/O 之前抛 JournalError
        with self.assertRaises(journal.JournalError):
            journal.append_control_plane_event(self.repo, "not-a-dict")
        with self.assertRaises(journal.JournalError):
            journal.append_control_plane_event(self.repo, {})
        with self.assertRaises(journal.JournalError):
            journal.append_control_plane_event(self.repo, {"event": ""})
        with self.assertRaises(journal.JournalError):
            journal.append_control_plane_event(
                self.repo, {"event": "x", "ts": "伪造"})
        self.assertEqual(journal.read_control_plane_events(self.repo), [])

    def test_read_tolerates_corrupt_and_blank_lines(self):
        path = journal.control_plane_journal_path(self.repo)
        path.parent.mkdir(parents=True, exist_ok=True)
        good = json.dumps({"ts": "t1", "event": "quota_epoch_advanced",
                           "epoch_id": EPOCH_A}, ensure_ascii=False)
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write("{corrupted\n")
            handle.write("\n")
            handle.write(good + "\n")
            handle.write("[1, 2]\n")
        events = journal.read_control_plane_events(self.repo)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["epoch_id"], EPOCH_A)

    def test_task_journal_untouched_by_control_plane_writes(self):
        # 控制面写不触碰任何任务目录
        journal.append_control_plane_event(
            self.repo, {"event": "quota_epoch_advanced"})
        self.assertFalse(
            (Path(self.repo) / ".glm-conductor" / "tasks").exists())

    def test_quota_epoch_advanced_in_recommended_events(self):
        self.assertIn("quota_epoch_advanced", journal.RECOMMENDED_EVENTS)


# —— ④ primer 落点迁移 end-to-end ——

def _win(kind, status, remaining, reset_at):
    return {"kind": kind, "status": status,
            "used_percent": round(100.0 - remaining, 1),
            "remaining_percent": remaining, "reset_at": reset_at}


def _detail(status, windows):
    return {"source": "provider", "status": status,
            "snapshot": {"provider": "bigmodel",
                         "fetched_at": "2026-09-02T21:00:00.000Z",
                         "status": status, "windows": windows},
            "fetched_at": "2026-09-02T21:00:00.000Z"}


class PrimerMigrationEndToEndTest(SubscriptionCase):
    """window_primed 迁到控制面 journal 后的端到端一例（C4 reviewer
    P2 正解）：事件字段零变化、落点 .glm-conductor/quota/events.jsonl、
    伪任务目录不复存在。传输 / 凭证全注入——零真实网络零真实调用。"""

    def setUp(self):
        super().setUp()
        # 隔离真实凭证与真实用户配置（与 tests/test_primer.py 同纪律）
        patcher = mock.patch.object(
            primer, "resolve_credential",
            return_value=("env", "test-key-abc"))
        patcher.start()
        self.addCleanup(patcher.stop)

    IDENTITY = "a1b2c3d4e5f60718"

    def prime(self, transport, fetch=None):
        return primer.prime_once(
            self.repo, boundary_id=EPOCH_A,
            provider_identity_hash=self.IDENTITY,
            transport=transport,
            fetch_refresh=fetch if fetch is not None
            else (lambda repo: None))

    def test_window_primed_lands_in_control_plane_journal(self):
        def transport(url, body, headers, timeout):
            return 200, json.dumps(
                {"usage": {"input_tokens": 19, "output_tokens": 38}}
            ).encode("utf-8")

        result = self.prime(transport)
        self.assertTrue(result["primed"])
        events = journal.read_control_plane_events(self.repo)
        self.assertEqual(len(events), 1)
        event = events[0]
        # 事件字段零变化（冻结九键）
        self.assertEqual(sorted(event.keys()), sorted(PRIMER_EVENT_KEYS))
        self.assertEqual(event["event"], "window_primed")
        self.assertEqual(event["boundary_id"], EPOCH_A)
        self.assertEqual(event["provider_identity_hash"], self.IDENTITY)
        self.assertFalse(event["materialized"])
        self.assertFalse(event["idempotent"])
        # 伪任务目录语义已删除：常量不复存在、目录零产生
        self.assertFalse(hasattr(primer, "PRIMER_JOURNAL_TASK_ID"))
        self.assertFalse(
            (Path(self.repo) / ".glm-conductor" / "tasks").exists())

    def test_prime_journal_failure_degrades_silently(self):
        # best-effort 语义保留：journal OSError 降级不抛，prime 结果
        # 不被遮蔽（durable primer.json 仍是幂等真相源）
        def transport(url, body, headers, timeout):
            return 200, json.dumps({"usage": {}}).encode("utf-8")

        with mock.patch.object(
                primer, "_append_control_plane_event",
                side_effect=OSError("locked")):
            result = self.prime(transport)
        self.assertTrue(result["primed"])
        self.assertEqual(self.control_events(), [])


if __name__ == "__main__":
    unittest.main()
