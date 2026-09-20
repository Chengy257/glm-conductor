# -*- coding: utf-8 -*-
"""Activation Transport / Scheduler Facts / CLI C6 面 + reviewer 留账
吸收项单元测试（v2.2 C6，wu-22-C6 ①②⑤⑥⑧）。

锚定对象：修正计划 §16（Activation Transport 抽象与四概念映射）/
§17（Transport A stable）/ §C6（四词汇：recurring_bridge stable +
probe_then_hold / self_retiming / session_injector 实验预留）/
§22.5（对账与观测绝不制造 armed）/ §14（subscription 幂等）。

覆盖映射：
    ① transport 抽象（TransportVocabularyTest + StatusTest +
       ArmTransportTest）：四词汇冻结（与 execution_policy.
       ACTIVATION_TRANSPORTS 对齐锚定）；activation_transport_status
       冻结十二键 + armed=bridge_status∈REUSABLE + policy 容错缺省；
       arm_transport param-before-IO（实验 kind → TransportReservedError
       零 I/O；stable 委托 arm_wake_bridge 零改写、冲突语义透传）
    ② scheduler_facts（SchedulerFactsTest）：路径布局 / 读 fail-open /
       FACT_FIELDS 冻结 / None 不覆盖 / origin 单向升级 / automation_id
       追加去重 / 无新证据零写幂等 / MAX_SESSIONS=32 按 updated_at 淘汰 /
       PermissionError 有界重试耗尽抛 SchedulerFactsError / 参数闸
       先于 I/O
    ⑤ CLI（CliTransportTest）：wake-reconcile passthrough（host_status
       闸 → 退出码 2；任务缺失 → 退出码 1；observed_at 透传）+
       transport-status（冻结键、任务缺失退出码 1、usage 同步）
    ⑥ execution_policy 可选顶层键（ExecutionPolicyTransportKeyTest）：
       DEFAULT_EXECUTION_POLICY 冻结块不含该键（测试锚定）+ validator
       词汇闸 + 容错 reader 缺省 recurring_bridge + setter 保留
    ⑧ reviewer 留账吸收（ResidualFixesTest + JournalDocstringContractTest）：
       _resume_subscription_gate 死参数移除（签名 + resume 链冒烟）；
       register_quota_subscription 非幂等重写保留未知键（幂等比较仍只比
       五键）；watcher.run_once 写回前同形 _merge_stop_flag（stop 旗标
       存活断言归位 tests.test_watcher.RunOnceTest）；read_control_plane_
       events docstring 按实际容错契约修正（不再宣称「绝不抛」）

运行：
    cd <repo_root> && python3 -m unittest tests.test_activation_transport -v
"""

import inspect
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "glm-conductor"))
from runtime import activation_transport, cli, execution_policy, journal, scheduler_facts, state
from runtime.continuity import resume as _cr
from runtime.continuity import subscription as _cs
from runtime.quota import accounting as _qa
from runtime.continuity import subscription as _cs
from runtime.quota import accounting as _qa

TID = "transport-test-1a2b3c"
SESSION = "sess-transport-1"
ARM_ISO = "2026-09-03T00:00:00Z"
EPOCH_A = "glm:0123456789abcdef"

# activation_transport_status 冻结十二键（C8 接口面，形状即契约）
TRANSPORT_STATUS_KEYS = ["task_id", "transport", "stable", "armed",
                         "bridge_status", "next_wake_at",
                         "current_boundary_id", "automation_id",
                         "bridge_interval_minutes", "scheduler_origin",
                         "scheduler_create", "reasons"]

FACT_FIELDS = ["origin", "create", "update", "pause", "delete",
               "automation_ids", "updated_at"]


def make_state(repo, **kwargs):
    """构造默认合法的完整状态 dict（solo 路由；v2.4 必填仓库根绑定）。"""
    task_id = kwargs.pop("task_id", TID)
    return state.new_task_state(
        task_id, "v2.2 C6 transport 夹具任务", {"mode": "solo"},
        repository_root=repo, **kwargs)


class TransportCase(unittest.TestCase):
    """公共装置：tempfile scratch 仓库 + 默认任务。"""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = tmp.name
        state.save_state(self.repo, make_state(self.repo))

    def arm(self, task_id=TID, automation_id="auto-t1"):
        return _wb_arm(
            self.repo, task_id, automation_id=automation_id,
            boundary_id="five_hour:%s" % ARM_ISO, reset_at=ARM_ISO,
            wake_at=ARM_ISO, next_wake_at=ARM_ISO,
            bridge_interval_minutes=60)

    def task_events(self, task_id=TID):
        return journal.read_events(self.repo, task_id)


# —— ① transport 抽象：词汇冻结 ——

class TransportVocabularyTest(unittest.TestCase):

    def test_frozen_four_kind_vocabulary(self):
        self.assertEqual(
            activation_transport.TRANSPORT_KINDS,
            ("recurring_bridge", "probe_then_hold", "self_retiming",
             "session_injector"))

    def test_stable_and_experimental_partition(self):
        self.assertEqual(activation_transport.STABLE_TRANSPORT,
                         "recurring_bridge")
        self.assertEqual(
            activation_transport.EXPERIMENTAL_TRANSPORTS,
            ("probe_then_hold", "self_retiming", "session_injector"))
        # 分区完备：stable + 实验预留 == 全词汇（无第三类）
        self.assertEqual(
            sorted(activation_transport.EXPERIMENTAL_TRANSPORTS
                   + (activation_transport.STABLE_TRANSPORT,)),
            sorted(activation_transport.TRANSPORT_KINDS))

    def test_execution_policy_vocabulary_aligned(self):
        # 独立声明（反向 import 会成环），对齐由本测试锚定
        self.assertEqual(execution_policy.ACTIVATION_TRANSPORTS,
                         activation_transport.TRANSPORT_KINDS)
        self.assertEqual(execution_policy.DEFAULT_ACTIVATION_TRANSPORT,
                         activation_transport.STABLE_TRANSPORT)


class ArmTransportTest(TransportCase):
    """arm_transport：param-before-IO + 实验预留 + stable 委托。"""

    def test_experimental_kinds_reserved_error_zero_io(self):
        state_path = (Path(self.repo) / ".glm-conductor" / "tasks" / TID
                      / "state.json")
        before = state_path.read_bytes()
        for kind in activation_transport.EXPERIMENTAL_TRANSPORTS:
            with self.subTest(transport=kind):
                with self.assertRaises(
                        activation_transport.TransportReservedError):
                    activation_transport.arm_transport(
                        self.repo, TID, transport=kind,
                        automation_id="auto-x",
                        boundary_id="five_hour:%s" % ARM_ISO,
                        reset_at=ARM_ISO, wake_at=ARM_ISO)
        # 绝不落 arm：state 零变化、零 journal 事件
        self.assertEqual(state_path.read_bytes(), before)
        self.assertEqual(
            [e for e in self.task_events()
             if e["event"] == "wake_bridge_armed"], [])

    def test_experimental_reservation_checked_before_task_io(self):
        # param-before-IO：缺任务 + 实验 kind → TransportReservedError
        # （而非 TaskManagerError）——词汇闸先于任何读盘
        with self.assertRaises(
                activation_transport.TransportReservedError):
            activation_transport.arm_transport(
                self.repo, "ghost-task-0000000",
                transport="session_injector", automation_id="auto-x",
                boundary_id="five_hour:%s" % ARM_ISO,
                reset_at=ARM_ISO, wake_at=ARM_ISO)

    def test_unknown_kind_valueerror_before_io(self):
        with self.assertRaises(ValueError):
            activation_transport.arm_transport(
                self.repo, "ghost-task-0000000", transport="carrier_pigeon",
                automation_id="auto-x",
                boundary_id="five_hour:%s" % ARM_ISO,
                reset_at=ARM_ISO, wake_at=ARM_ISO)



class SchedulerFactsTest(unittest.TestCase):

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = tmp.name

    def record(self, **kwargs):
        kwargs.setdefault("session_id", SESSION)
        return scheduler_facts.record_observation(self.repo, **kwargs)

    def test_path_layout(self):
        self.assertEqual(
            scheduler_facts.session_facts_path(self.repo).replace("\\", "/"),
            str(Path(self.repo) / ".glm-conductor" / "scheduler"
                / "session_facts.json").replace("\\", "/"))

    def test_read_missing_and_corrupt_fail_open(self):
        self.assertIsNone(scheduler_facts.read_session_facts(self.repo))
        self.assertIsNone(scheduler_facts.read_session_record(
            self.repo, SESSION))
        path = Path(scheduler_facts.session_facts_path(self.repo))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("not json{", encoding="utf-8")
        self.assertIsNone(scheduler_facts.read_session_facts(self.repo))
        self.assertIsNone(scheduler_facts.read_session_record(
            self.repo, SESSION))

    def test_first_observation_freezes_field_shape(self):
        record = self.record(origin="interactive", create="allowed",
                             automation_id="auto-1")
        self.assertEqual(sorted(record.keys()), sorted(FACT_FIELDS))
        record = scheduler_facts.read_session_record(self.repo, SESSION)
        self.assertEqual(sorted(record.keys()), sorted(FACT_FIELDS))
        self.assertEqual(record["origin"], "interactive")
        self.assertEqual(record["create"], "allowed")
        self.assertEqual(record["update"], None)
        self.assertEqual(record["automation_ids"], ["auto-1"])

    def test_none_never_overwrites(self):
        self.record(create="allowed", origin="interactive")
        self.record()  # 全 None 观察
        record = scheduler_facts.read_session_record(self.repo, SESSION)
        self.assertEqual(record["create"], "allowed")
        self.assertEqual(record["origin"], "interactive")

    def test_origin_single_way_upgrade_only(self):
        # unknown → scheduled_task 单向升级；落定后 interactive 不冲掉
        self.record(origin="scheduled_task")
        self.record(origin="interactive")
        self.assertEqual(
            scheduler_facts.read_session_record(
                self.repo, SESSION)["origin"],
            "scheduled_task")

    def test_capability_evidence_frozen_no_flip(self):
        self.record(create="forbidden")
        self.record(create="allowed")
        self.assertEqual(
            scheduler_facts.read_session_record(
                self.repo, SESSION)["create"],
            "forbidden")

    def test_automation_ids_append_dedup(self):
        self.record(automation_id="auto-1")
        self.record(automation_id="auto-1")  # 重复：去重
        self.record(automation_id="auto-2")
        record = scheduler_facts.read_session_record(self.repo, SESSION)
        self.assertEqual(record["automation_ids"], ["auto-1", "auto-2"])

    def test_no_new_evidence_zero_write_idempotent(self):
        self.record(create="allowed")
        path = Path(scheduler_facts.session_facts_path(self.repo))
        before = path.read_bytes()
        result = self.record(create="allowed")
        self.assertTrue(result.get("idempotent"))
        self.assertEqual(path.read_bytes(), before)

    def test_max_sessions_prune_oldest(self):
        # 播种 32 条（updated_at 递增），再写一条新会话 → 最旧被淘汰
        for i in range(scheduler_facts.MAX_SESSIONS):
            scheduler_facts.record_observation(
                self.repo, "sess-%02d" % i, create="allowed",
                observed_at="2026-09-01T00:%02d:00Z" % i)
        self.record(origin="interactive",
                    observed_at="2026-09-09T00:00:00Z")
        data = scheduler_facts.read_session_facts(self.repo)
        self.assertLessEqual(len(data["sessions"]),
                             scheduler_facts.MAX_SESSIONS)
        self.assertIn(SESSION, data["sessions"])
        self.assertNotIn("sess-00", data["sessions"])  # updated_at 最旧
        self.assertIn("sess-%02d" % (scheduler_facts.MAX_SESSIONS - 1),
                      data["sessions"])

    def test_param_validation_before_io(self):
        for kwargs in ({"session_id": "", "create": "allowed"},
                       {"create": "maybe"},
                       {"origin": "unknown-ish"},
                       {"automation_id": 42},
                       {"observed_at": "yesterday"}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    self.record(**kwargs)
        self.assertIsNone(scheduler_facts.read_session_facts(self.repo))

    def test_permission_error_bounded_retry_raises(self):
        # 纪律复制自 watcher_store（常量独立）：耗尽 → SchedulerFactsError
        sleeps = []
        with mock.patch.object(scheduler_facts.os, "replace",
                               side_effect=PermissionError(13, "locked")):
            with self.assertRaises(scheduler_facts.SchedulerFactsError):
                self.record(create="allowed", retry_interval=0,
                            sleep=sleeps.append)
        self.assertEqual(len(sleeps),
                         scheduler_facts.FACT_PERMISSION_RETRY_ATTEMPTS - 1)

    def test_atomic_write_no_tmp_leftover(self):
        self.record(create="allowed")
        leftovers = [p for p in Path(self.repo).rglob("*.tmp")]
        self.assertEqual(leftovers, [])


# —— ⑥ execution_policy 可选顶层键 ——

class ExecutionPolicyTransportKeyTest(unittest.TestCase):

    def test_frozen_default_block_has_no_transport_key(self):
        # 默认块形状由既有冻结测试逐字段锚定——activation_transport 是
        # 可选键，绝不进 DEFAULT_EXECUTION_POLICY
        self.assertNotIn("activation_transport",
                         execution_policy.DEFAULT_EXECUTION_POLICY)
        self.assertNotIn("activation_transport",
                         execution_policy.default_execution_policy())

    def test_validator_gates_four_values(self):
        base = execution_policy.default_execution_policy()
        for kind in execution_policy.ACTIVATION_TRANSPORTS:
            with self.subTest(transport=kind):
                policy = dict(base)
                policy["activation_transport"] = kind
                self.assertEqual(
                    execution_policy.validate_execution_policy(policy), [])
        for bad in ("carrier_pigeon", "", 42, True, None):
            with self.subTest(transport=bad):
                policy = dict(base)
                policy["activation_transport"] = bad
                errors = execution_policy.validate_execution_policy(policy)
                self.assertEqual(len(errors), 1)
                self.assertIn("activation_transport", errors[0])

    def test_validator_missing_key_legal(self):
        self.assertEqual(
            execution_policy.validate_execution_policy(
                execution_policy.default_execution_policy()), [])

    def test_reader_fault_tolerant_default(self):
        # 缺 / 坏形状一律缺省 recurring_bridge，绝不抛（观测面 fail-open）
        self.assertEqual(
            execution_policy.activation_transport(
                execution_policy.default_execution_policy()),
            "recurring_bridge")
        self.assertEqual(
            execution_policy.activation_transport(None),
            "recurring_bridge")
        for bad in ({}, {"activation_transport": "junk"},
                    {"activation_transport": 7},
                    {"activation_transport": True}):
            with self.subTest(policy=bad):
                self.assertEqual(
                    execution_policy.activation_transport(bad),
                    "recurring_bridge")
        self.assertEqual(
            execution_policy.activation_transport(
                {"activation_transport": "self_retiming"}),
            "self_retiming")

    def test_state_roundtrip_with_transport_key(self):
        repo = self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(repo.cleanup)
        st = make_state(repo.name)
        st["execution_policy"] = {"activation_transport": "probe_then_hold"}
        state.save_state(repo.name, st)
        loaded = state.load_state(repo.name, TID)
        self.assertEqual(
            loaded["execution_policy"]["activation_transport"],
            "probe_then_hold")

    def test_authorization_setter_preserves_transport_key(self):
        policy = execution_policy.default_execution_policy()
        policy["activation_transport"] = "session_injector"
        updated = execution_policy.set_parallel_authorization(
            policy, max_workers=2, source="user",
            confirmed_at="2026-09-03T00:00:00Z")
        self.assertEqual(updated["activation_transport"],
                         "session_injector")


# —— ⑤ CLI：wake-reconcile / transport-status ——

def run_cli(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli.main(argv)
    return code, out.getvalue(), err.getvalue()


class CliTransportTest(TransportCase):

    def test_wake_reconcile_retired_error_face_exit_1(self):
        # v2.4 W6：对账编排退役——错误 JSON 面与退出码语义不变
        code, out, _err = run_cli(
            ["wake-reconcile", self.repo, "ghost-task-0000000", "active"])
        self.assertEqual(code, 1)
        self.assertIn("已退役", json.loads(out.strip())["error"])

    def test_wake_reconcile_usage_error_exit_2(self):
        code, out, _err = run_cli(["wake-reconcile", self.repo])
        self.assertEqual(code, 2)

    def test_usage_string_synced(self):
        self.assertIn("wake-reconcile <repo_root> <task_id> <host_status> "
                      "[observed_at]", cli.USAGE)
        self.assertIn("transport-status <repo_root> <task_id>", cli.USAGE)


# —— v2.4 Phase 2（W6）：执行面退役锚定 ——

def _wb_arm(repo_root, task_id, *, automation_id, boundary_id, reset_at,
            wake_at, next_wake_at, bridge_interval_minutes):
    """arm 装置改指 wake_bridge 规范落点（v2.4 W6 起该记账编排已随
    v2.3 执行面退役——对 v2.4 state 调用即 AttributeError/退化，供
    仍可运行的参数闸用例之外的退役锚定参考）。"""
    from runtime.continuity import wake_bridge as _wb
    return _wb.arm_wake_bridge(
        repo_root, task_id, automation_id=automation_id,
        boundary_id=boundary_id, reset_at=reset_at, wake_at=wake_at,
        next_wake_at=next_wake_at,
        bridge_interval_minutes=bridge_interval_minutes)


class RetiredRuntimeAnchorTest(TransportCase):
    """activation_transport 求值面与 wake-reconcile 编排已随 v2.3 执行
    面退役：调用即 RuntimeError（错误 JSON / 退出码语义不变）。"""

    def test_status_face_retired_runtime_error(self):
        code, out, _err = run_cli(["transport-status", self.repo, TID])
        self.assertEqual(code, 1)
        self.assertIn("已退役", json.loads(out.strip())["error"])

    def test_wake_reconcile_retired_runtime_error(self):
        code, out, _err = run_cli(
            ["wake-reconcile", self.repo, TID, "deleted"])
        self.assertEqual(code, 1)
        self.assertIn("已退役", json.loads(out.strip())["error"])


# —— ⑧ reviewer 留账吸收 ——

class ResidualFixesTest(TransportCase):

    def test_resume_subscription_gate_dead_param_removed(self):
        # 死参数 provider_status 移除：签名收敛为 (repo_root, task_id, st)
        params = list(inspect.signature(
            _cr._resume_subscription_gate).parameters)
        self.assertEqual(params, ["repo_root", "task_id", "st"])



# —— v2.4 Phase 2（W6）：执行面退役锚定 ——

def _wb_arm(repo_root, task_id, *, automation_id, boundary_id, reset_at,
            wake_at, next_wake_at, bridge_interval_minutes):
    """arm 装置改指 wake_bridge 规范落点（v2.4 W6 起该记账编排已随
    v2.3 执行面退役——对 v2.4 state 调用即 AttributeError/退化，供
    仍可运行的参数闸用例之外的退役锚定参考）。"""
    from runtime.continuity import wake_bridge as _wb
    return _wb.arm_wake_bridge(
        repo_root, task_id, automation_id=automation_id,
        boundary_id=boundary_id, reset_at=reset_at, wake_at=wake_at,
        next_wake_at=next_wake_at,
        bridge_interval_minutes=bridge_interval_minutes)


class RetiredRuntimeAnchorTest(TransportCase):
    """activation_transport 求值面与 wake-reconcile 编排已随 v2.3 执行
    面退役：调用即 RuntimeError（错误 JSON / 退出码语义不变）。"""

    def test_status_face_retired_runtime_error(self):
        code, out, _err = run_cli(["transport-status", self.repo, TID])
        self.assertEqual(code, 1)
        self.assertIn("已退役", json.loads(out.strip())["error"])

    def test_wake_reconcile_retired_runtime_error(self):
        code, out, _err = run_cli(
            ["wake-reconcile", self.repo, TID, "deleted"])
        self.assertEqual(code, 1)
        self.assertIn("已退役", json.loads(out.strip())["error"])


# —— ⑧ reviewer 留账吸收 ——

class ResidualFixesTest(TransportCase):

    def test_resume_subscription_gate_dead_param_removed(self):
        # 死参数 provider_status 移除：签名收敛为 (repo_root, task_id, st)
        params = list(inspect.signature(
            _cr._resume_subscription_gate).parameters)
        self.assertEqual(params, ["repo_root", "task_id", "st"])


if __name__ == "__main__":
    unittest.main()
