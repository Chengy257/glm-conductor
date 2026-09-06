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
    ⑤ C5b 订阅接线（TransitionRegistrationTest +
        ResumeSubscriptionGateTest + CrashReconciliationTest +
        TransitionMarkOrderTest + CliSubscriptionFaceTest）：EXHAUSTED
        转态点零网络注册（含折算失败不注册 / 幂等 / 失败不阻断）；
        resume 订阅任务全矩阵（同 epoch 不恢复 / 新 epoch+executable
        恢复+mark 恰一次 / 不可执行不恢复 / minimum_state 不满足 /
        EXHAUSTED 面照附零转态 / legacy 输出零变化）；崩溃窗口对账
        （journal 证据 state 落后 → 不 eligible + best-effort 自愈，
        自愈失败仍保守拦截）；转态-mark 顺序（evaluate → 转态 →
        mark；失败路径零 mark）；CLI quota-resume additive 键透传

运行：
    cd <repo_root> && python3 -m unittest tests.test_quota_subscription -v
"""

import inspect
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "glm-conductor"))
from runtime import cli, journal, reconcile as reconcile_mod, state, task_manager
from runtime.quota import epoch as quota_epoch, primer
from runtime.quota import resolver as quota_resolver

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

    def test_rewrite_preserves_unknown_keys_merge_not_replace(self):
        # v2.2 C6 reviewer 留账吸收：非幂等重写路径按 merge 落盘——
        # 既有块的未知键原样保留（不整块替换），五规范键被新值覆盖
        self.put_task()
        self.register()
        st = state.load_state(self.repo, TID)
        st["quota_subscription"]["future_key"] = {"v": 1}
        save_task(self.repo, st)
        self.register(epoch_id=EPOCH_B)
        block = self.subscription()
        self.assertEqual(block["future_key"], {"v": 1})  # 未知键保留
        self.assertEqual(block["registered_epoch_id"], EPOCH_B)  # 五键更新
        self.assertIs(block["enabled"], True)

    def test_idempotent_comparison_ignores_unknown_keys(self):
        # 幂等比较仍只比五规范键：块内多出未知键 + 同参重注册 → 幂等
        # 零写（未知键不放大为「变化」）
        self.put_task()
        self.register()
        st = state.load_state(self.repo, TID)
        st["quota_subscription"]["future_key"] = 1
        save_task(self.repo, st)
        bytes_before = self.state_bytes()
        again = self.register()
        self.assertTrue(again["idempotent"])
        self.assertEqual(self.state_bytes(), bytes_before)

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
        return primer._prime_once_unchecked(
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


# —— ⑤ C5b 订阅接线（wu-22-C5b：转态点注册 + resume 资格门 + 对账） ——

# 真实窗口夹具（§27 形状）：epoch 身份由 quota_epoch 从窗口折算，保证
# 测试断言的 epoch_id 与 runtime 折算同源（绝不手写指纹）。
#   WINDOWS_A = 耗尽时刻的 epoch（reset 12:00Z，窗 EXHAUSTED）
#   WINDOWS_B = 窗口滚动后的新 epoch（reset 17:00Z，窗已恢复 AVAILABLE
#               ——executable=True 的恢复场景）
#   WINDOWS_BLOCKED = 另一个新 epoch（reset 19:00Z，窗仍 EXHAUSTED——
#               不可执行 / 四态不放行场景）
WINDOWS_A = [{"kind": "five_hour", "status": "EXHAUSTED",
              "used_percent": 100.0, "remaining_percent": 0.0,
              "reset_at": "2026-09-01T12:00:00Z"}]
WINDOWS_B = [{"kind": "five_hour", "status": "AVAILABLE",
              "used_percent": 10.0, "remaining_percent": 90.0,
              "reset_at": "2026-09-01T17:00:00Z"}]
WINDOWS_BLOCKED = [{"kind": "five_hour", "status": "EXHAUSTED",
                    "used_percent": 100.0, "remaining_percent": 0.0,
                    "reset_at": "2026-09-01T19:00:00Z"}]
EPOCH_OF_A = quota_epoch.epoch_id(WINDOWS_A)
EPOCH_OF_B = quota_epoch.epoch_id(WINDOWS_B)
EPOCH_OF_BLOCKED = quota_epoch.epoch_id(WINDOWS_BLOCKED)

# resume_from_quota 冻结五键（legacy 输出零变化的硬锚）
RESUME_FROZEN_KEYS = ["recommended_resume_at", "resumed", "status",
                      "unit_recovery", "wake_budget_remaining"]
# handle_quota_exhausted 冻结七键（C5b 不改返回形状的硬锚）
EXHAUSTED_FROZEN_KEYS = ["auto_resume", "reason", "recommended_resume_at",
                         "remaining_quota_windows", "task_status",
                         "waiting_units", "wake"]


def fake_resolved(status):
    """resolve_quota_status 冻结四键的测试桩。"""
    return {"status": status, "source": "provider",
            "evaluated_at": "2026-09-01T12:00:00.000Z",
            "reason": "fixture"}


def fake_detail(status, windows, snapshot="auto"):
    """resolve_quota_detail 冻结四键的测试桩（snapshot="auto" 带窗口，
    None 模拟 none 层无明细）。"""
    if snapshot == "auto":
        snapshot = {"provider": "fixture",
                    "fetched_at": "2026-09-01T12:00:00.000Z",
                    "status": status, "windows": windows}
    return {"source": "provider", "status": status,
            "snapshot": snapshot,
            "fetched_at": "2026-09-01T12:00:00.000Z"}


def write_cache(repo, status, windows):
    """按 resolver 缓存原语落一份缓存（转态点零网络折算的输入）。"""
    quota_resolver._save_cache(quota_resolver._cache_path(repo), {
        "provider": "fixture",
        "fetched_at": "2026-09-01T12:00:00.000Z",
        "status": status,
        "snapshot": {"provider": "fixture",
                     "fetched_at": "2026-09-01T12:00:00.000Z",
                     "status": status, "windows": windows}})


VERIFY_CMD = "python3 -m unittest tests.test_quota_subscription"


def make_unit(uid, status="waiting_quota", runtime_meta=None):
    """构造通过 §61 契约校验的最小 work unit dict。"""
    unit = {"id": uid, "objective": "C5b wiring unit %s" % uid,
            "status": status, "depends_on": [],
            "executor": "flash-implementer",
            "ownership": ["src/%s.py" % uid],
            "verification": [VERIFY_CMD]}
    if runtime_meta is not None:
        unit["runtime"] = runtime_meta
    return unit


class WiringCase(SubscriptionCase):
    """C5b 接线公共装置：waiting 态任务 + 订阅注册 + resolver 桩。"""

    def make_task(self, status="waiting_quota", units=None):
        st = make_state(status=status)
        st["work_units"] = list(units) if units is not None \
            else [make_unit("wu-a")]
        save_task(self.repo, st)
        return st

    def subscribe(self, epoch_id=EPOCH_OF_A, **kwargs):
        return task_manager.register_quota_subscription(
            self.repo, TID, epoch_id=epoch_id, **kwargs)

    def advanced_events(self):
        return [e for e in self.control_events()
                if e.get("event") == "quota_epoch_advanced"]

    def quota_resumed_events(self):
        return [e for e in self.task_events()
                if e.get("event") == "quota_resumed"]


class TransitionRegistrationTest(WiringCase):
    """C5b ①：EXHAUSTED 转态点注册订阅（零网络折算 + 容错降级）。"""

    def test_transition_registers_subscription_at_cached_epoch(self):
        self.make_task(status="executing", units=[make_unit("wu-a", "ready")])
        write_cache(self.repo, "EXHAUSTED", WINDOWS_A)
        result = task_manager.handle_quota_exhausted(self.repo, TID)
        # 返回键冻结（C5b 只加订阅事实，不改转态返回形状）
        self.assertEqual(sorted(result.keys()), EXHAUSTED_FROZEN_KEYS)
        self.assertEqual(result["task_status"], "waiting_quota")
        block = self.subscription()
        self.assertTrue(block["enabled"])
        self.assertEqual(block["registered_epoch_id"], EPOCH_OF_A)
        registered = [e for e in self.task_events()
                      if e.get("event") == "quota_subscription_registered"]
        self.assertEqual(len(registered), 1)
        self.assertEqual(registered[0]["registered_epoch_id"], EPOCH_OF_A)
        self.assertEqual(
            state.load_state(self.repo, TID)["status"], "waiting_quota")

    def test_second_cycle_same_epoch_idempotent(self):
        # 已注册同参 → register 自身幂等（C5a 保证）：第二次 EXHAUSTED
        # 转态零写零事件（quota_waiting 照常，订阅事件不重复）
        self.make_task(status="executing", units=[make_unit("wu-a", "ready")])
        write_cache(self.repo, "EXHAUSTED", WINDOWS_A)
        task_manager.handle_quota_exhausted(self.repo, TID)
        st = state.load_state(self.repo, TID)
        st["status"] = "executing"
        save_task(self.repo, st)
        task_manager.handle_quota_exhausted(self.repo, TID)
        registered = [e for e in self.task_events()
                      if e.get("event") == "quota_subscription_registered"]
        waiting = [e for e in self.task_events()
                   if e.get("event") == "quota_waiting"]
        self.assertEqual(len(registered), 1)
        self.assertEqual(len(waiting), 2)
        self.assertEqual(self.subscription()["registered_epoch_id"],
                         EPOCH_OF_A)

    def test_new_epoch_updates_registration(self):
        self.make_task(status="executing", units=[make_unit("wu-a", "ready")])
        write_cache(self.repo, "EXHAUSTED", WINDOWS_A)
        task_manager.handle_quota_exhausted(self.repo, TID)
        st = state.load_state(self.repo, TID)
        st["status"] = "executing"
        save_task(self.repo, st)
        write_cache(self.repo, "EXHAUSTED", WINDOWS_B)
        task_manager.handle_quota_exhausted(self.repo, TID)
        registered = [e for e in self.task_events()
                      if e.get("event") == "quota_subscription_registered"]
        self.assertEqual(len(registered), 2)
        self.assertEqual(self.subscription()["registered_epoch_id"],
                         EPOCH_OF_B)

    def test_no_cache_no_registration_legacy_unchanged(self):
        # 折算失败（无缓存 → 无 windows）→ 不注册零副作用：legacy 路径
        # 逐字不变（默认块原样、无订阅事件、转态照常）
        self.make_task(status="executing", units=[make_unit("wu-a", "ready")])
        result = task_manager.handle_quota_exhausted(self.repo, TID)
        self.assertEqual(result["task_status"], "waiting_quota")
        self.assertEqual(self.subscription(),
                         FROZEN_DEFAULT_QUOTA_SUBSCRIPTION)
        self.assertEqual(
            [e for e in self.task_events()
             if e.get("event") == "quota_subscription_registered"], [])

    def test_bad_cache_status_no_registration(self):
        # 缓存 status 词汇外 → evaluate_epoch ValueError → 折算失败 →
        # 不注册零副作用
        self.make_task(status="executing", units=[make_unit("wu-a", "ready")])
        write_cache(self.repo, "MEGA", WINDOWS_A)
        result = task_manager.handle_quota_exhausted(self.repo, TID)
        self.assertEqual(result["task_status"], "waiting_quota")
        self.assertEqual(self.subscription(),
                         FROZEN_DEFAULT_QUOTA_SUBSCRIPTION)

    def test_registration_failure_does_not_block_transition(self):
        # 订阅是增强面：注册失败（预期外异常）不阻断 / 不回滚已完成的
        # 转态——任务照常 waiting_quota
        self.make_task(status="executing", units=[make_unit("wu-a", "ready")])
        write_cache(self.repo, "EXHAUSTED", WINDOWS_A)
        with mock.patch.object(task_manager, "register_quota_subscription",
                               side_effect=RuntimeError("boom")):
            result = task_manager.handle_quota_exhausted(self.repo, TID)
        self.assertEqual(result["task_status"], "waiting_quota")
        self.assertEqual(
            state.load_state(self.repo, TID)["status"], "waiting_quota")

    def test_transition_derivation_is_zero_network(self):
        # 转态点折算刻意走缓存原语（零网络纪律）：resolve_quota_detail
        # 恒不被调用——缓存缺位时它会发生 provider 抓取，把网络引入
        # 转态事务面
        self.make_task(status="executing", units=[make_unit("wu-a", "ready")])
        write_cache(self.repo, "EXHAUSTED", WINDOWS_A)
        with mock.patch.object(quota_resolver, "resolve_quota_detail") as d:
            task_manager.handle_quota_exhausted(self.repo, TID)
        d.assert_not_called()
        self.assertEqual(self.subscription()["registered_epoch_id"],
                         EPOCH_OF_A)


class ResumeSubscriptionGateTest(WiringCase):
    """C5b ②：resume 订阅任务全矩阵（未注册任务输出零变化为硬锚）。"""

    def resume(self, status=None, resolved_status="AVAILABLE",
               windows=WINDOWS_B, detail=None):
        with mock.patch.object(
                quota_resolver, "resolve_quota_status",
                return_value=fake_resolved(resolved_status)), \
             mock.patch.object(
                 quota_resolver, "resolve_quota_detail",
                 return_value=detail if detail is not None
                 else fake_detail(resolved_status, windows)):
            return task_manager.resume_from_quota(self.repo, TID,
                                                  status=status)

    def test_same_epoch_already_activated_not_resumed(self):
        # QC-07：last_activation == 当前 epoch → 无资格，不恢复
        self.make_task()
        self.subscribe(EPOCH_OF_B)
        task_manager.mark_activation_epoch(self.repo, TID,
                                           epoch_id=EPOCH_OF_B)
        advanced_before = len(self.advanced_events())
        result = self.resume(windows=WINDOWS_B)
        self.assertFalse(result["resumed"])
        face = result["subscription"]
        self.assertTrue(face["registered"])
        self.assertFalse(face["eligible"])
        self.assertEqual(face["epoch_id"], EPOCH_OF_B)
        self.assertTrue(any("已激活" in r for r in face["reasons"]))
        on_disk = state.load_state(self.repo, TID)
        self.assertEqual(on_disk["status"], "waiting_quota")
        self.assertEqual(on_disk["work_units"][0]["status"],
                         "waiting_quota")
        self.assertEqual(len(self.advanced_events()), advanced_before)
        self.assertEqual(self.quota_resumed_events(), [])

    def test_new_epoch_executable_resumes_and_marks_exactly_once(self):
        self.make_task()
        self.subscribe(EPOCH_OF_A)
        task_manager.mark_activation_epoch(self.repo, TID,
                                           epoch_id=EPOCH_OF_A)
        result = self.resume(windows=WINDOWS_B)
        self.assertTrue(result["resumed"])
        self.assertEqual(sorted(result.keys()),
                         sorted(RESUME_FROZEN_KEYS
                                + ["subscription", "consumption"]))
        face = result["subscription"]
        self.assertTrue(face["registered"])
        self.assertTrue(face["eligible"])
        self.assertEqual(face["reasons"], [])
        self.assertEqual(face["epoch_id"], EPOCH_OF_B)
        self.assertEqual(face["activated_epoch_id"], EPOCH_OF_B)
        self.assertEqual(sorted(face["mark"].keys()),
                         sorted(MARK_RESULT_KEYS))
        self.assertTrue(face["mark"]["marked"])
        self.assertFalse(face["mark"]["idempotent"])
        on_disk = state.load_state(self.repo, TID)
        self.assertEqual(on_disk["status"], "executing")
        self.assertEqual(on_disk["work_units"][0]["status"], "ready")
        self.assertEqual(on_disk["quota_subscription"]
                         ["last_activation_epoch_id"], EPOCH_OF_B)
        advanced = self.advanced_events()
        self.assertEqual(len(advanced), 2)  # A 一次 + B 一次（每 epoch 恰一次）
        self.assertEqual(advanced[-1]["epoch_id"], EPOCH_OF_B)
        self.assertEqual(advanced[-1]["task_id"], TID)
        self.assertEqual(len(self.quota_resumed_events()), 1)

    def test_new_epoch_not_executable_not_resumed(self):
        # 新 epoch 但窗仍 EXHAUSTED（provider EXHAUSTED）：executable=
        # False → 无资格，零转态；subscription 面照附（四态不放行分支）
        self.make_task()
        self.subscribe(EPOCH_OF_A)
        result = self.resume(resolved_status="EXHAUSTED",
                             windows=WINDOWS_BLOCKED)
        self.assertFalse(result["resumed"])
        face = result["subscription"]
        self.assertFalse(face["eligible"])
        self.assertEqual(face["epoch_id"], EPOCH_OF_BLOCKED)
        self.assertTrue(any("不可执行" in r for r in face["reasons"]))
        self.assertNotIn("mark", face)
        self.assertNotIn("activated_epoch_id", face)
        on_disk = state.load_state(self.repo, TID)
        self.assertEqual(on_disk["status"], "waiting_quota")
        self.assertEqual(on_disk["work_units"][0]["status"],
                         "waiting_quota")
        self.assertEqual(self.advanced_events(), [])

    def test_minimum_state_unmet_not_resumed(self):
        # PRESSURE 未达缺省 minimum_state=AVAILABLE 档位 → 无资格
        self.make_task()
        self.subscribe(EPOCH_OF_A)
        result = self.resume(resolved_status="PRESSURE", windows=WINDOWS_B)
        self.assertFalse(result["resumed"])
        face = result["subscription"]
        self.assertFalse(face["eligible"])
        self.assertTrue(any("未达到" in r for r in face["reasons"]))
        self.assertEqual(state.load_state(self.repo, TID)["status"],
                         "waiting_quota")
        self.assertEqual(self.advanced_events(), [])

    def test_epoch_derivation_failure_blocks_conservatively(self):
        # 折算失败（none 层无 snapshot）→ 无法确认新 epoch → 保守不恢复
        self.make_task()
        self.subscribe(EPOCH_OF_A)
        result = self.resume(resolved_status="UNKNOWN",
                             detail=fake_detail("UNKNOWN", WINDOWS_B,
                                                snapshot=None))
        self.assertFalse(result["resumed"])
        face = result["subscription"]
        self.assertFalse(face["eligible"])
        self.assertIsNone(face["epoch_id"])
        self.assertTrue(any("无法折算" in r for r in face["reasons"]))
        on_disk = state.load_state(self.repo, TID)
        self.assertEqual(on_disk["status"], "waiting_quota")
        self.assertEqual(on_disk["work_units"][0]["status"],
                         "waiting_quota")

    def test_legacy_task_output_verbatim_and_detail_never_read(self):
        # 未注册（默认块）→ 既有行为逐字不变：冻结五键、无 subscription
        # 键、明细解析零调用（网络面零扩大）
        self.make_task()
        with mock.patch.object(
                quota_resolver, "resolve_quota_detail") as detail_mock:
            result = task_manager.resume_from_quota(self.repo, TID,
                                                    status="AVAILABLE")
        detail_mock.assert_not_called()
        self.assertEqual(sorted(result.keys()), RESUME_FROZEN_KEYS)
        self.assertTrue(result["resumed"])
        self.assertEqual(state.load_state(self.repo, TID)["status"],
                         "executing")

    def test_repeated_wake_after_resume_has_no_subscription_key(self):
        # 任务已不在等待态（重复唤醒）：零转态幂等，订阅面不附（门只在
        # waiting 态介入）
        self.make_task()
        self.subscribe(EPOCH_OF_A)
        self.resume(windows=WINDOWS_B)  # 第一次：恢复 + mark
        result = self.resume(windows=WINDOWS_B)  # 第二次：重复唤醒
        self.assertFalse(result["resumed"])
        self.assertNotIn("subscription", result)

    def test_manual_ruling_blocks_activation_with_zero_mark(self):
        # 资格过但单元对账 manual_ruling → 既有行为（不转态）；订阅面
        # 照附（eligible=True）但零 mark（未实际转态不记账）
        self.make_task(units=[make_unit(
            "wu-r", runtime_meta={"quota_interrupted_from": "running"})])
        self.subscribe(EPOCH_OF_A)
        with mock.patch.object(
                reconcile_mod, "reconcile_agent_run",
                return_value={"classification": "manual_ruling",
                              "rationale": ["fixture"]}) as rec:
            result = self.resume(windows=WINDOWS_B)
        rec.assert_called_once()
        self.assertFalse(result["resumed"])
        face = result["subscription"]
        self.assertTrue(face["eligible"])
        self.assertNotIn("mark", face)
        self.assertNotIn("activated_epoch_id", face)
        self.assertEqual(state.load_state(self.repo, TID)["status"],
                         "waiting_quota")
        self.assertEqual(self.advanced_events(), [])


class CrashReconciliationTest(WiringCase):
    """C5b ③：崩溃窗口对账（journal 证据 vs state 落后，保守方向）。"""

    def _task_with_stale_state(self):
        self.make_task()
        self.subscribe(EPOCH_OF_A)
        # 控制面 journal 有本任务 B epoch 的推进证据，state 落后（未标）
        journal.append_control_plane_event(self.repo, {
            "event": "quota_epoch_advanced", "task_id": TID,
            "epoch_id": EPOCH_OF_B, "previous_epoch_id": None})

    def test_journal_evidence_state_behind_blocks_and_self_heals(self):
        self._task_with_stale_state()
        with mock.patch.object(
                quota_resolver, "resolve_quota_status",
                return_value=fake_resolved("AVAILABLE")), \
             mock.patch.object(
                 quota_resolver, "resolve_quota_detail",
                 return_value=fake_detail("AVAILABLE", WINDOWS_B)):
            result = task_manager.resume_from_quota(self.repo, TID)
        self.assertFalse(result["resumed"])
        face = result["subscription"]
        self.assertFalse(face["eligible"])
        self.assertTrue(any("journal 证据对账" in r for r in face["reasons"]))
        self.assertTrue(any("已激活" in r for r in face["reasons"]))
        on_disk = state.load_state(self.repo, TID)
        # best-effort 自愈已落盘（state 与 journal 重新一致）
        self.assertEqual(on_disk["quota_subscription"]
                         ["last_activation_epoch_id"], EPOCH_OF_B)
        self.assertEqual(on_disk["status"], "waiting_quota")  # 零转态
        # 自愈绝不追加控制面事件（QC-07 每 epoch 恰一次，mark 才是写点）
        self.assertEqual(len(self.advanced_events()), 1)

    def test_self_heal_failure_still_blocks(self):
        # 自愈写失败（如盘满）不阻塞判定：仍保守判不 eligible，只记 reason
        self._task_with_stale_state()
        with mock.patch.object(
                quota_resolver, "resolve_quota_status",
                return_value=fake_resolved("AVAILABLE")), \
             mock.patch.object(
                 quota_resolver, "resolve_quota_detail",
                 return_value=fake_detail("AVAILABLE", WINDOWS_B)), \
             mock.patch.object(state, "save_state",
                               side_effect=OSError("locked")):
            result = task_manager.resume_from_quota(self.repo, TID)
        self.assertFalse(result["resumed"])
        face = result["subscription"]
        self.assertFalse(face["eligible"])
        self.assertTrue(any("自愈重写失败" in r for r in face["reasons"]))
        on_disk = state.load_state(self.repo, TID)
        self.assertIsNone(on_disk["quota_subscription"]
                          ["last_activation_epoch_id"])  # 未被改写
        self.assertEqual(on_disk["status"], "waiting_quota")

    def test_unrelated_journal_evidence_resumes_normally(self):
        # 他任务 / 他 epoch 的推进证据不拦截本任务本 epoch 的恢复
        self.make_task()
        self.subscribe(EPOCH_OF_A)
        journal.append_control_plane_event(self.repo, {
            "event": "quota_epoch_advanced", "task_id": "other-task",
            "epoch_id": EPOCH_OF_B, "previous_epoch_id": None})
        journal.append_control_plane_event(self.repo, {
            "event": "quota_epoch_advanced", "task_id": TID,
            "epoch_id": EPOCH_OF_A, "previous_epoch_id": None})
        with mock.patch.object(
                quota_resolver, "resolve_quota_status",
                return_value=fake_resolved("AVAILABLE")), \
             mock.patch.object(
                 quota_resolver, "resolve_quota_detail",
                 return_value=fake_detail("AVAILABLE", WINDOWS_B)):
            result = task_manager.resume_from_quota(self.repo, TID)
        self.assertTrue(result["resumed"])
        self.assertTrue(result["subscription"]["eligible"])
        self.assertEqual(
            state.load_state(self.repo, TID)["status"], "executing")
        advanced = self.advanced_events()
        self.assertEqual(len(advanced), 3)  # 两条夹具 + B 恰一条 mark


class TransitionMarkOrderTest(WiringCase):
    """C5b 约束：转态-记账顺序 = evaluate → 转态 → mark。"""

    def _prepared_task(self):
        self.make_task()
        self.subscribe(EPOCH_OF_A)
        task_manager.mark_activation_epoch(self.repo, TID,
                                           epoch_id=EPOCH_OF_A)

    def test_mark_runs_after_transition_is_durable(self):
        # mark 被调用时盘上任务已是 executing（转态先落盘——先 mark 后
        # 转态的崩溃窗口会卡死同 epoch 恢复）
        self._prepared_task()
        observed = []
        original = task_manager.mark_activation_epoch

        def spy(repo_root, task_id, *, epoch_id):
            observed.append(
                state.load_state(repo_root, task_id)["status"])
            return original(repo_root, task_id, epoch_id=epoch_id)

        with mock.patch.object(
                quota_resolver, "resolve_quota_status",
                return_value=fake_resolved("AVAILABLE")), \
             mock.patch.object(
                 quota_resolver, "resolve_quota_detail",
                 return_value=fake_detail("AVAILABLE", WINDOWS_B)), \
             mock.patch.object(task_manager, "mark_activation_epoch",
                               side_effect=spy):
            result = task_manager.resume_from_quota(self.repo, TID)
        self.assertEqual(observed, ["executing"])  # 恰一次且在转态之后
        self.assertTrue(result["resumed"])
        self.assertEqual(len(self.advanced_events()), 2)

    def test_ineligible_path_zero_marks(self):
        # 恢复失败路径零 mark：同 epoch 无资格 → 零转态零控制面事件
        self._prepared_task()
        with mock.patch.object(
                quota_resolver, "resolve_quota_status",
                return_value=fake_resolved("AVAILABLE")), \
             mock.patch.object(
                 quota_resolver, "resolve_quota_detail",
                 return_value=fake_detail("AVAILABLE", WINDOWS_A)):
            result = task_manager.resume_from_quota(self.repo, TID)
        self.assertFalse(result["resumed"])
        self.assertEqual(len(self.advanced_events()), 1)  # 只有夹具那一条


class CliSubscriptionFaceTest(WiringCase):
    """C5b ④：quota-resume CLI 输出透传 subscription 面（additive 键，
    既有键与退出码契约不动）。"""

    def run_cli(self, *args):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = cli.main(list(args))
        return code, json.loads(buffer.getvalue())

    def test_subscription_payload_carries_additive_key(self):
        self.make_task()
        self.subscribe(EPOCH_OF_A)
        task_manager.mark_activation_epoch(self.repo, TID,
                                           epoch_id=EPOCH_OF_A)
        with mock.patch.object(
                quota_resolver, "resolve_quota_status",
                return_value=fake_resolved("AVAILABLE")), \
             mock.patch.object(
                 quota_resolver, "resolve_quota_detail",
                 return_value=fake_detail("AVAILABLE", WINDOWS_B)):
            code, payload = self.run_cli("quota-resume", self.repo, TID)
        self.assertEqual(code, 0)
        self.assertTrue(payload["resumed"])
        self.assertEqual(sorted(payload.keys()),
                         sorted(RESUME_FROZEN_KEYS
                                + ["subscription", "consumption"]))
        self.assertTrue(payload["subscription"]["eligible"])
        self.assertEqual(payload["subscription"]["activated_epoch_id"],
                         EPOCH_OF_B)

    def test_legacy_payload_has_no_subscription_key(self):
        self.make_task()
        with mock.patch.object(
                quota_resolver, "resolve_quota_status",
                return_value=fake_resolved("AVAILABLE")), \
             mock.patch.object(
                 quota_resolver, "resolve_quota_detail") as detail_mock:
            code, payload = self.run_cli("quota-resume", self.repo, TID)
        self.assertEqual(code, 0)
        self.assertNotIn("subscription", payload)  # legacy 输出零变化
        detail_mock.assert_not_called()

    def test_exhausted_payload_carries_ineligible_face_exit_0(self):
        # EXHAUSTED 保守等待仍是合法结果（退出码 0），订阅面照附
        self.make_task()
        self.subscribe(EPOCH_OF_A)
        with mock.patch.object(
                quota_resolver, "resolve_quota_status",
                return_value=fake_resolved("EXHAUSTED")), \
             mock.patch.object(
                 quota_resolver, "resolve_quota_detail",
                 return_value=fake_detail("EXHAUSTED", WINDOWS_BLOCKED)):
            code, payload = self.run_cli("quota-resume", self.repo, TID)
        self.assertEqual(code, 0)
        self.assertFalse(payload["resumed"])
        self.assertFalse(payload["subscription"]["eligible"])


if __name__ == "__main__":
    unittest.main()
