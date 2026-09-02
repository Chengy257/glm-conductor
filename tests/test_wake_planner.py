#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Persistent Wake Bridge 行为测试（v2.2 M5，wu-22-05；决策记录 D15-a/b/c/d）。

仅 Python 3 标准库（unittest + tempfile），零第三方依赖、零网络、零
宿主调用（runtime 绝不 CronCreate/CronDelete——M5 是纯规划器 + 记账器
+ prompt 生成器）。state.json / events.jsonl / quota-cache.json 全部
落盘在 tempfile.TemporaryDirectory 提供的临时目录（真实 new_task_state
构造，非 mock state），不污染真实工作区（夹具风格对齐
tests/test_task_manager_draining.py）。

覆盖（规格验证清单）：
    - D15-c 四档 arm 裁决全分支：until_done+user → MUST eager-arm
      （不等 PRESSURE，NORMAL 相也 arm）；auto_once 风险触发
      （PRESSURE 相 / long-horizon / 跨窗预测 / 会话已关联 Scheduled
      Task）→ SHOULD；auto_once DRAINING+create=allowed → MUST；
      manual/notify PRESSURE → SHOULD、DRAINING → MUST/SHOULD、NORMAL
      → 不建桥；BLOCKED → 不现场建桥；未授权（source != user）→ 不建；
    - D10 boundary 数学：多窗取最早 reset、boundary_id = "<kind>:
      <reset>"（Z 形式归一）、wake_at = reset + DEFAULT_GRACE_SECONDS
      （300）、无窗/坏 reset 不虚构；
    - WB-04 幂等：同 boundary 已 armed → required=False（复用同桥）；
      boundary 变更 → required=False + retarget 记账语义（不新建）；
    - prompt 生成：三分支决策 + 硬红线关键句（严禁创建任何新的
      Scheduled Task / DO NOT CREATE A NEW SCHEDULED TASK）+ 零凭证 +
      单次清理绝不重试 + 同桥复用语义；
    - 生命周期记账：arm_wake_bridge（requested→armed + journal
      wake_bridge_armed + obligation=armed + 第二 automation 身份拒绝
      + 不同 boundary 拒绝）、record_bridge_fired（armed→fired +
      recurring 重复触发合法）、retarget_wake_bridge（current_boundary_
      id 更新 + generation 恒 0 + journal wake_bridge_retargeted）、
      write_completion_tombstone（D15-d 冻结四键 + 幂等）、
      degrade_continuity（obligation=degraded + journal
      continuity_degraded + reason=scheduler_create_forbidden + 幂等）；
    - CLI：wake-plan happy（§22.2 冻结九键）/ 用法错 exit 2 / 任务缺失
      exit 1；wake-status happy（十值 status + scheduler_context +
      mode/generation + tombstone）/ 用法错 exit 2 / 任务缺失 exit 1；
    - scheduler_context 缺省容错：legacy 无 continuation 块的 state →
      plan 按 default_continuation 解释（create=unknown fail-open）。

运行：
    cd <repo_root> && python3 -m unittest tests.test_wake_planner -v
"""

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "glm-conductor"))
from runtime import cli, journal, state, task_manager
from runtime.quota import resolver

TID = "wake-plan-bridge-1a2b3c"
RESET_AT = "2026-09-02T12:00:00Z"
WEEKLY_RESET = "2026-09-05T18:00:00Z"
GRACE = 300
AUTOMATION_ID = "automation-dc0cc8d8-5c86-43af-93b1-81b664382e0d"
BOUNDARY = "five_hour:%s" % RESET_AT
# solo 路由（矩阵合法：delegability low + assurance standard → main）
ROUTE = {"mode": "solo", "delegability": "low", "assurance": "standard",
         "executor": "main", "continuity": "resumable"}


# —— 测试夹具 ——

def make_task(root, *, status="executing", auto_resume="manual",
              source="default", max_windows=0, consumed=0,
              interval_minutes=None, continuation=None):
    """真实落盘一个任务 state（new_task_state 构造 + save），并按参数
    授予 execution_policy.continuity / authorization / quota_control。"""
    st = state.new_task_state(TID, "Persistent Wake Bridge 测试目标",
                              dict(ROUTE), status=status)
    policy = st["execution_policy"]
    policy["continuity"]["auto_resume"] = auto_resume
    policy["continuity"]["max_quota_windows"] = max_windows
    policy["continuity"]["consumed_quota_windows"] = consumed
    policy["authorization"]["source"] = source
    if source == "user":
        policy["authorization"]["confirmed_at"] = "2026-09-01T00:00:00Z"
    if interval_minutes is not None:
        policy["quota_control"]["bridge_interval_minutes"] = interval_minutes
    if continuation is not None:
        st["continuation"] = continuation
    state.save_state(root, st)
    return st


def make_legacy_task(root, *, status="executing"):
    """落盘一个 legacy 形态任务（删除 continuation 块——§23.1 缺键
    合法，消费方按 default_continuation 解释）。"""
    st = state.new_task_state(TID, "legacy 形态目标", dict(ROUTE),
                              status=status)
    st.pop("continuation", None)
    st.pop("execution_policy", None)  # 同为 legacy 缺块
    state.save_state(root, st)
    return st


def window(kind="five_hour", remaining=63.0, reset_at=RESET_AT):
    """§27 snapshot 的单窗形状。"""
    return {"kind": kind, "remaining_percent": remaining,
            "reset_at": reset_at}


def arm_bridge(root, *, boundary_id=BOUNDARY, automation_id=AUTOMATION_ID,
               status="armed"):
    """把任务的 wake_bridge 直接置为活桥（记账路径经 arm_wake_bridge
    的用例自行走 API；本装置供 plan 幂等 / retarget / fired 用例）。"""
    st = state.load_state(root, TID)
    bridge = st["continuation"]["wake_bridge"]
    bridge["status"] = status
    bridge["boundary_id"] = boundary_id
    bridge["current_boundary_id"] = boundary_id
    bridge["automation_id"] = automation_id
    bridge["reset_at"] = RESET_AT
    bridge["wake_at"] = "2026-09-02T12:05:00Z"
    bridge["armed_at"] = "2026-09-01T18:19:27.900Z"
    bridge["mode"] = "recurring"
    bridge["generation"] = 0
    bridge["bridge_interval_minutes"] = 60
    state.save_state(root, st)


def scheduler_context(create=None, origin="interactive",
                      parent_automation_id=None):
    """scheduler_context 覆写块（None 键不写——保持默认 unknown）。"""
    block = {}
    if origin is not None:
        block["origin"] = origin
    if create is not None:
        block["create"] = create
    if parent_automation_id is not None:
        block["parent_automation_id"] = parent_automation_id
    return block


def write_raw_state(root, st):
    """把内存 state dict 原样写盘（绕过 save_state 校验闸）——仅供
    纵深防御分支用例：auto 家族 + source != user 的组合被 save_state
    的授权耦合拒绝（§5.4），而盘上半定义 / 手改形态正是 plan 防御
    分支的存在理由，须以原样落盘构造。"""
    path = state.state_path(root, TID)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(st, fh, ensure_ascii=False, indent=2)
    return st


def events(root, name):
    """读取真实 journal 并过滤事件名。"""
    return [e for e in journal.read_events(root, TID)
            if e.get("event") == name]


def run_cli(*args):
    """调用 cli.main 并捕获 stdout，返回 (退出码, 解析后 JSON)。"""
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        code = cli.main(list(args))
    return code, json.loads(buffer.getvalue())


class WakePlannerTestBase(unittest.TestCase):
    """临时目录夹具：每个用例独立的 repo_root（无 git 需求）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = self._tmp.name

    def plan(self, *, provider_status="AVAILABLE", windows=None, **kwargs):
        """plan_wake_bridge 薄包装（windows 缺省给健康双窗）。"""
        return task_manager.plan_wake_bridge(
            self.root, TID, provider_status=provider_status,
            windows=[window(), window("weekly", 80.0, WEEKLY_RESET)]
            if windows is None else windows, **kwargs)


# —— D15-c 分层：until_done MUST eager-arm ——

class UntilDoneEagerArmTest(WakePlannerTestBase):

    def setUp(self):
        super().setUp()
        make_task(self.root, auto_resume="until_done", source="user",
                  max_windows=2)

    def test_until_done_user_arms_eagerly_even_in_normal_phase(self):
        plan = self.plan()  # 63% / 80% → NORMAL
        self.assertTrue(plan["required"])
        self.assertTrue(plan["eager"])
        self.assertIn("MUST eager-arm", plan["reason"])
        self.assertIn("until_done", plan["reason"])
        self.assertIsNotNone(plan["prompt"])

    def test_until_done_user_arms_eagerly_in_pressure_phase(self):
        plan = self.plan(windows=[window(remaining=30.0)])  # PRESSURE 带
        self.assertTrue(plan["required"])
        self.assertTrue(plan["eager"])
        self.assertIn("不等 PRESSURE", plan["reason"])

    def test_until_done_without_user_authorization_does_not_arm(self):
        # save_state 拒绝 until_done + source != user（§5.4 授权耦合）
        # ——纵深防御分支以盘上半定义形态构造（write_raw_state，全程
        # 不经 save_state 校验闸）
        st = state.new_task_state(TID, "纵深防御目标", dict(ROUTE),
                                  status="executing")
        st["execution_policy"]["continuity"]["auto_resume"] = "until_done"
        st["execution_policy"]["continuity"]["max_quota_windows"] = 2
        st["execution_policy"]["authorization"]["source"] = "default"
        write_raw_state(self.root, st)
        plan = self.plan()
        self.assertFalse(plan["required"])
        self.assertFalse(plan["eager"])
        self.assertIsNone(plan["prompt"])
        self.assertIn("未获得用户授权", plan["reason"])

    def test_until_done_budget_exhausted_does_not_arm(self):
        make_task(self.root, auto_resume="until_done", source="user",
                  max_windows=2, consumed=2)
        plan = self.plan()
        self.assertFalse(plan["required"])
        self.assertIn("预算已耗尽", plan["reason"])
        self.assertIn("§14.5", plan["reason"])

    def test_until_done_create_forbidden_does_not_arm(self):
        st = state.load_state(self.root, TID)
        st["continuation"]["scheduler_context"] = dict(
            st["continuation"]["scheduler_context"], create="forbidden")
        state.save_state(self.root, st)
        plan = self.plan()
        self.assertFalse(plan["required"])
        self.assertIn("create=forbidden", plan["reason"])
        self.assertIn("degrade_continuity", plan["reason"])


# —— D15-c 分层：auto_once 风险触发 ——

class AutoOnceRiskTriggerTest(WakePlannerTestBase):

    def setUp(self):
        super().setUp()
        make_task(self.root, auto_resume="auto_once", source="user",
                  max_windows=1)

    def test_pressure_phase_triggers_should_arm(self):
        plan = self.plan(windows=[window(remaining=30.0)])  # (20, 35] 带
        self.assertTrue(plan["required"])
        self.assertFalse(plan["eager"])
        self.assertIn("SHOULD arm", plan["reason"])
        self.assertIn("PRESSURE", plan["reason"])

    def test_draining_with_create_allowed_is_must(self):
        st = state.load_state(self.root, TID)
        st["continuation"]["scheduler_context"] = scheduler_context(
            create="allowed")
        state.save_state(self.root, st)
        plan = self.plan(windows=[window(remaining=15.0)])  # DRAINING 带
        self.assertTrue(plan["required"])
        self.assertFalse(plan["eager"])
        self.assertIn("MUST arm", plan["reason"])

    def test_normal_without_triggers_does_not_arm(self):
        plan = self.plan()
        self.assertFalse(plan["required"])
        self.assertIn("无风险触发", plan["reason"])

    def test_long_horizon_flag_triggers_should_arm(self):
        plan = self.plan(long_horizon=True)
        self.assertTrue(plan["required"])
        self.assertFalse(plan["eager"])
        self.assertIn("long-horizon", plan["reason"])

    def test_cross_window_prediction_triggers_should_arm(self):
        plan = self.plan(cross_window_predicted=True)
        self.assertTrue(plan["required"])
        self.assertIn("跨窗", plan["reason"])

    def test_parent_automation_in_place_triggers_should_arm(self):
        st = state.load_state(self.root, TID)
        st["continuation"]["scheduler_context"] = scheduler_context(
            parent_automation_id="automation-parent-0001")
        state.save_state(self.root, st)
        plan = self.plan()
        self.assertTrue(plan["required"])
        self.assertIn("会话已关联 Scheduled Task", plan["reason"])

    def test_auto_once_without_user_authorization_does_not_arm(self):
        # 同上：§5.4 授权耦合被 save_state 拒绝——纵深防御分支以盘上
        # 半定义形态构造（write_raw_state，全程不经 save_state 校验闸）
        st = state.new_task_state(TID, "纵深防御目标", dict(ROUTE),
                                  status="executing")
        st["execution_policy"]["continuity"]["auto_resume"] = "auto_once"
        st["execution_policy"]["continuity"]["max_quota_windows"] = 1
        st["execution_policy"]["authorization"]["source"] = "default"
        write_raw_state(self.root, st)
        plan = self.plan()
        self.assertFalse(plan["required"])
        self.assertIn("未获得用户授权", plan["reason"])


# —— D15-c 分层：manual / notify ——

class ManualNotifyLayeringTest(WakePlannerTestBase):

    def test_manual_pressure_should_arm(self):
        make_task(self.root, auto_resume="manual")
        plan = self.plan(windows=[window(remaining=30.0)])
        self.assertTrue(plan["required"])
        self.assertFalse(plan["eager"])
        self.assertIn("SHOULD arm", plan["reason"])

    def test_manual_draining_with_create_allowed_is_must(self):
        make_task(self.root, auto_resume="manual")
        st = state.load_state(self.root, TID)
        st["continuation"]["scheduler_context"] = scheduler_context(
            create="allowed")
        state.save_state(self.root, st)
        plan = self.plan(windows=[window(remaining=15.0)])
        self.assertTrue(plan["required"])
        self.assertIn("MUST arm", plan["reason"])

    def test_manual_draining_with_unknown_create_is_should(self):
        make_task(self.root, auto_resume="manual")  # create 缺省 unknown
        plan = self.plan(windows=[window(remaining=15.0)])
        self.assertTrue(plan["required"])
        self.assertFalse(plan["eager"])
        self.assertIn("SHOULD", plan["reason"])
        self.assertNotIn("MUST arm", plan["reason"])

    def test_notify_pressure_should_arm_with_warm_only_note(self):
        make_task(self.root, auto_resume="notify")
        plan = self.plan(windows=[window(remaining=30.0)])
        self.assertTrue(plan["required"])
        self.assertIn("SHOULD arm", plan["reason"])
        self.assertIn("warm-only", plan["reason"])

    def test_manual_normal_does_not_arm(self):
        make_task(self.root, auto_resume="manual")
        plan = self.plan()
        self.assertFalse(plan["required"])
        self.assertIn("暂不建桥", plan["reason"])

    def test_blocked_phase_does_not_build_on_the_spot(self):
        make_task(self.root, auto_resume="manual")
        plan = self.plan(provider_status="EXHAUSTED")
        self.assertFalse(plan["required"])
        self.assertIn("BLOCKED", plan["reason"])
        self.assertIn("早已 armed", plan["reason"])


# —— D10 boundary 数学 ——

class BoundaryMathTest(WakePlannerTestBase):

    def setUp(self):
        super().setUp()
        make_task(self.root, auto_resume="until_done", source="user",
                  max_windows=2)

    def test_multi_window_takes_earliest_reset(self):
        plan = self.plan()
        self.assertEqual(plan["boundary_id"], BOUNDARY)
        self.assertEqual(plan["wake_at"], "2026-09-02T12:05:00Z")

    def test_wake_at_adds_default_grace_seconds(self):
        from runtime.quota.scheduler import DEFAULT_GRACE_SECONDS
        self.assertEqual(DEFAULT_GRACE_SECONDS, 300)
        plan = self.plan(windows=[window(reset_at="2026-09-02T00:00:00Z")])
        self.assertEqual(plan["wake_at"], "2026-09-02T00:05:00Z")

    def test_boundary_id_normalizes_offset_spelling(self):
        # 同一时刻的不同拼写 → 归一为同一 Z 形式（WB-04 幂等比较稳定）
        plan = self.plan(windows=[
            window(reset_at="2026-09-02T12:00:00+00:00")])
        self.assertEqual(plan["boundary_id"], BOUNDARY)

    def test_unknown_boundary_when_no_parseable_reset(self):
        plan = self.plan(windows=[
            window(reset_at=None), {"kind": "five_hour"}])
        self.assertIsNone(plan["boundary_id"])
        self.assertIsNone(plan["wake_at"])

    def test_interval_minutes_from_policy_key_level_merge(self):
        make_task(self.root, auto_resume="until_done", source="user",
                  max_windows=2, interval_minutes=45)
        plan = self.plan()
        self.assertEqual(plan["bridge_interval_minutes"], 45)

    def test_interval_minutes_default_60(self):
        plan = self.plan()
        self.assertEqual(plan["bridge_interval_minutes"], 60)

    def test_mode_is_constant_recurring(self):
        plan = self.plan()
        self.assertEqual(plan["mode"], "recurring")

    def test_frozen_nine_keys(self):
        plan = self.plan()
        self.assertEqual(
            sorted(plan.keys()),
            ["boundary_id", "bridge_interval_minutes", "current_boundary_id",
             "eager", "mode", "prompt", "reason", "required", "wake_at"])


# —— WB-04 幂等：活桥复用 / retarget 记账语义 ——

class IdempotenceTest(WakePlannerTestBase):

    def setUp(self):
        super().setUp()
        make_task(self.root, auto_resume="until_done", source="user",
                  max_windows=2)

    def test_armed_same_boundary_reuses_bridge(self):
        arm_bridge(self.root)
        plan = self.plan()
        self.assertFalse(plan["required"])
        self.assertEqual(plan["current_boundary_id"], BOUNDARY)
        self.assertEqual(plan["boundary_id"], BOUNDARY)
        self.assertIsNone(plan["prompt"])
        self.assertIn("复用同桥", plan["reason"])
        self.assertIn("不新建", plan["reason"])

    def test_armed_boundary_advanced_notes_retarget(self):
        arm_bridge(self.root, boundary_id="five_hour:2026-09-01T12:00:00Z")
        plan = self.plan()  # 新窗口 reset 09-02 → boundary 推进
        self.assertFalse(plan["required"])
        self.assertEqual(plan["current_boundary_id"],
                         "five_hour:2026-09-01T12:00:00Z")
        self.assertEqual(plan["boundary_id"], BOUNDARY)
        self.assertIn("retarget", plan["reason"])
        self.assertIn("不新建", plan["reason"])

    def test_fired_bridge_is_still_reusable(self):
        arm_bridge(self.root, status="fired")
        plan = self.plan()
        self.assertFalse(plan["required"])
        self.assertIn("复用同桥", plan["reason"])

    def test_cancelled_bridge_is_not_reusable(self):
        arm_bridge(self.root, status="cancelled")
        plan = self.plan()
        # cancelled 不在 REUSABLE_BRIDGE_STATUSES——重新按 D15-c 裁决
        self.assertTrue(plan["required"])
        self.assertTrue(plan["eager"])


# —— Universal Wake prompt（§10 三分支 + 硬红线 + 零凭证） ——

class UniversalWakePromptTest(WakePlannerTestBase):

    def build_prompt(self, **kwargs):
        params = dict(
            task_id=TID, ledger_root=self.root, repository_root=self.root,
            boundary_id=BOUNDARY, reset_at=RESET_AT,
            wake_at="2026-09-02T12:05:00Z", mode="recurring",
            bridge_interval_minutes=60, automation_id=AUTOMATION_ID)
        params.update(kwargs)
        return task_manager.universal_wake_prompt(**params)

    def test_three_decision_branches_present(self):
        prompt = self.build_prompt()
        # 分支一：goal 已达成 → tombstone + 单次清理
        self.assertIn("任务 goal 已达成", prompt)
        self.assertIn("continuation.tombstone", prompt)
        self.assertIn("bridge_should_noop=true", prompt)
        # 分支二：manual/notify → warm-only
        self.assertIn("warm-only", prompt)
        self.assertIn("绝不实施新工作", prompt)
        # 分支三：auto 家族 → quota-resume + 对账续作
        self.assertIn("quota-resume", prompt)
        self.assertIn("reconcile", prompt)
        self.assertIn("不重放已完成工作", prompt)
        # 额度不可执行 → no-op 不消费窗口预算（D15-g）
        self.assertIn("不消费窗口预算", prompt)

    def test_hard_red_lines_verbatim(self):
        prompt = self.build_prompt()
        self.assertIn("严禁创建任何新的 Scheduled Task", prompt)
        self.assertIn("DO NOT CREATE A NEW SCHEDULED TASK", prompt)
        self.assertIn("本会话属于 scheduled task", prompt)
        self.assertIn("绝不重试", prompt)
        self.assertIn("失败即容忍", prompt)

    def test_zero_credential_assertion(self):
        prompt = self.build_prompt()
        self.assertIn("不含任何凭证", prompt)
        for secret_marker in ("apiKey", "api_key", "Authorization",
                              "Bearer ", "sk-", "password", "token="):
            self.assertNotIn(secret_marker, prompt)

    def test_reuse_same_bridge_semantics(self):
        prompt = self.build_prompt()
        self.assertIn("复用同一 automation 身份", prompt)
        self.assertIn("绝不新建", prompt)
        self.assertIn("reuse or retarget the existing bridge only", prompt)
        self.assertIn("第二座 wake", prompt)

    def test_payload_fields_present(self):
        prompt = self.build_prompt()
        self.assertIn("TASK_ID: %s" % TID, prompt)
        self.assertIn("LEDGER_ROOT: %s" % self.root, prompt)
        self.assertIn("BOUNDARY_ID: %s" % BOUNDARY, prompt)
        self.assertIn("EXPECTED_RESET_AT: %s" % RESET_AT, prompt)
        self.assertIn("WAKE_AT: 2026-09-02T12:05:00Z", prompt)
        self.assertIn("recurring", prompt)
        self.assertIn("间隔 60 分钟", prompt)

    def test_optional_fields_fall_back_to_unknown(self):
        prompt = task_manager.universal_wake_prompt(TID,
                                                    ledger_root=self.root)
        self.assertIn("BOUNDARY_ID: unknown", prompt)
        self.assertIn("EXPECTED_RESET_AT: unknown", prompt)
        self.assertIn("WAKE_AT: 按固定间隔触发", prompt)


# —— arm_wake_bridge（requested→armed 记账） ——

class ArmWakeBridgeTest(WakePlannerTestBase):

    def setUp(self):
        super().setUp()
        make_task(self.root, auto_resume="until_done", source="user",
                  max_windows=2)

    def test_arm_writes_bookkeeping_and_journal(self):
        result = task_manager.arm_wake_bridge(
            self.root, TID, automation_id=AUTOMATION_ID,
            boundary_id=BOUNDARY, reset_at=RESET_AT,
            wake_at="2026-09-02T12:05:00Z", next_wake_at=RESET_AT,
            bridge_interval_minutes=60)
        self.assertEqual(result["status"], "armed")
        self.assertEqual(result["automation_id"], AUTOMATION_ID)
        self.assertEqual(result["current_boundary_id"], BOUNDARY)
        self.assertEqual(result["mode"], "recurring")
        self.assertEqual(result["generation"], 0)
        st = state.load_state(self.root, TID)
        bridge = st["continuation"]["wake_bridge"]
        self.assertEqual(bridge["status"], "armed")
        self.assertEqual(bridge["automation_id"], AUTOMATION_ID)
        self.assertEqual(bridge["boundary_id"], BOUNDARY)
        self.assertIsNotNone(bridge["armed_at"])
        self.assertEqual(st["continuation"]["obligation"], "armed")
        armed = events(self.root, "wake_bridge_armed")
        self.assertEqual(len(armed), 1)
        self.assertEqual(armed[0]["automation_id"], AUTOMATION_ID)
        self.assertTrue(armed[0]["recurring"])
        self.assertEqual(armed[0]["bridge_interval_minutes"], 60)

    def test_arm_is_idempotent_for_same_identity_and_boundary(self):
        first = task_manager.arm_wake_bridge(
            self.root, TID, automation_id=AUTOMATION_ID,
            boundary_id=BOUNDARY, reset_at=RESET_AT,
            wake_at="2026-09-02T12:05:00Z", bridge_interval_minutes=60)
        with open(state.state_path(self.root, TID), "rb") as fh:
            before = fh.read()
        second = task_manager.arm_wake_bridge(
            self.root, TID, automation_id=AUTOMATION_ID,
            boundary_id=BOUNDARY, reset_at=RESET_AT,
            wake_at="2026-09-02T12:05:00Z", bridge_interval_minutes=60)
        self.assertTrue(second["idempotent"])
        self.assertEqual(second["automation_id"], first["automation_id"])
        self.assertEqual(len(events(self.root, "wake_bridge_armed")), 1)
        with open(state.state_path(self.root, TID), "rb") as fh:
            self.assertEqual(fh.read(), before)  # 零写字节

    def test_arm_rejects_second_automation_identity(self):
        task_manager.arm_wake_bridge(
            self.root, TID, automation_id=AUTOMATION_ID,
            boundary_id=BOUNDARY, reset_at=RESET_AT,
            wake_at="2026-09-02T12:05:00Z")
        with self.assertRaises(task_manager.TaskManagerError) as ctx:
            task_manager.arm_wake_bridge(
                self.root, TID, automation_id="automation-second-0002",
                boundary_id=BOUNDARY, reset_at=RESET_AT,
                wake_at="2026-09-02T12:05:00Z")
        self.assertIn("persistent automation identity", str(ctx.exception))

    def test_arm_rejects_different_boundary_while_armed(self):
        task_manager.arm_wake_bridge(
            self.root, TID, automation_id=AUTOMATION_ID,
            boundary_id=BOUNDARY, reset_at=RESET_AT,
            wake_at="2026-09-02T12:05:00Z")
        with self.assertRaises(task_manager.TaskManagerError) as ctx:
            task_manager.arm_wake_bridge(
                self.root, TID, automation_id=AUTOMATION_ID,
                boundary_id="five_hour:2026-09-06T00:00:00Z",
                reset_at="2026-09-06T00:00:00Z",
                wake_at="2026-09-06T00:05:00Z")
        self.assertIn("retarget_wake_bridge", str(ctx.exception))

    def test_arm_validates_parameters_before_io(self):
        for kwargs in (
                {"automation_id": ""},
                {"boundary_id": None},
                {"reset_at": "not-a-time"},
                {"wake_at": 123},
                {"bridge_interval_minutes": 4},
                {"bridge_interval_minutes": True},
                {"mode": "chain"}):
            params = dict(automation_id=AUTOMATION_ID, boundary_id=BOUNDARY,
                          reset_at=RESET_AT, wake_at="2026-09-02T12:05:00Z")
            params.update(kwargs)
            with self.assertRaises(ValueError):
                task_manager.arm_wake_bridge(self.root, TID, **params)
        self.assertEqual(events(self.root, "wake_bridge_armed"), [])
        st = state.load_state(self.root, TID)
        self.assertEqual(st["continuation"]["wake_bridge"]["status"],
                         "none")  # 零副作用

    def test_arm_missing_task_raises(self):
        with self.assertRaises(task_manager.TaskManagerError):
            task_manager.arm_wake_bridge(
                self.root, "missing-task-0001",
                automation_id=AUTOMATION_ID, boundary_id=BOUNDARY,
                reset_at=RESET_AT, wake_at="2026-09-02T12:05:00Z")


# —— record_bridge_fired / retarget_wake_bridge ——

class FireAndRetargetTest(WakePlannerTestBase):

    def setUp(self):
        super().setUp()
        make_task(self.root, auto_resume="until_done", source="user",
                  max_windows=2)
        arm_bridge(self.root)

    def test_fired_updates_status_and_journal(self):
        result = task_manager.record_bridge_fired(
            self.root, TID, fired_at="2026-09-01T18:48:56Z")
        self.assertEqual(result["status"], "fired")
        self.assertEqual(result["fired_at"], "2026-09-01T18:48:56Z")
        st = state.load_state(self.root, TID)
        self.assertEqual(st["continuation"]["wake_bridge"]["status"],
                         "fired")
        fired = events(self.root, "wake_bridge_fired")
        self.assertEqual(len(fired), 1)
        self.assertEqual(fired[0]["automation_id"], AUTOMATION_ID)
        self.assertEqual(fired[0]["boundary_id"], BOUNDARY)

    def test_fired_default_moment_and_repeat_fires_allowed(self):
        task_manager.record_bridge_fired(self.root, TID)
        task_manager.record_bridge_fired(self.root, TID)
        self.assertEqual(len(events(self.root, "wake_bridge_fired")), 2)
        st = state.load_state(self.root, TID)
        self.assertEqual(st["continuation"]["wake_bridge"]["status"],
                         "fired")

    def test_fired_rejects_without_live_bridge(self):
        make_task(self.root, status="executing")  # 桥复位 none
        with self.assertRaises(task_manager.TaskManagerError):
            task_manager.record_bridge_fired(self.root, TID)

    def test_retarget_updates_current_boundary_only(self):
        new_boundary = "five_hour:2026-09-02T21:59:00Z"
        result = task_manager.retarget_wake_bridge(
            self.root, TID, boundary_id=new_boundary,
            reset_at="2026-09-02T21:59:00Z",
            wake_at="2026-09-02T22:04:00Z")
        self.assertEqual(result["current_boundary_id"], new_boundary)
        self.assertEqual(result["boundary_id"], BOUNDARY)  # 原始目标保留
        self.assertEqual(result["generation"], 0)  # recurring 恒 0
        st = state.load_state(self.root, TID)
        bridge = st["continuation"]["wake_bridge"]
        self.assertEqual(bridge["current_boundary_id"], new_boundary)
        self.assertEqual(bridge["boundary_id"], BOUNDARY)
        self.assertEqual(bridge["wake_at"], "2026-09-02T22:04:00Z")
        retargets = events(self.root, "wake_bridge_retargeted")
        self.assertEqual(len(retargets), 1)
        self.assertEqual(retargets[0]["from_boundary_id"], BOUNDARY)
        self.assertEqual(retargets[0]["to_boundary_id"], new_boundary)
        self.assertEqual(retargets[0]["generation"], 0)

    def test_retarget_idempotent_when_nothing_changes(self):
        first = task_manager.retarget_wake_bridge(self.root, TID,
                                                  boundary_id=BOUNDARY)
        self.assertTrue(first["idempotent"])
        self.assertEqual(events(self.root, "wake_bridge_retargeted"), [])

    def test_retarget_rejects_without_live_bridge(self):
        make_task(self.root, status="executing")
        with self.assertRaises(task_manager.TaskManagerError):
            task_manager.retarget_wake_bridge(self.root, TID,
                                              boundary_id=BOUNDARY)

    def test_retarget_validates_parameters(self):
        with self.assertRaises(ValueError):
            task_manager.retarget_wake_bridge(self.root, TID,
                                              boundary_id="")
        with self.assertRaises(ValueError):
            task_manager.retarget_wake_bridge(
                self.root, TID, boundary_id=BOUNDARY, wake_at="bad")


# —— write_completion_tombstone（D15-d 冻结四键） ——

class TombstoneTest(WakePlannerTestBase):

    def setUp(self):
        super().setUp()
        make_task(self.root, auto_resume="until_done", source="user",
                  max_windows=2)

    def test_tombstone_frozen_four_keys(self):
        tombstone = task_manager.write_completion_tombstone(
            self.root, TID, completed_at="2026-09-02T13:00:00Z")
        self.assertEqual(sorted(tombstone.keys()),
                         ["bridge_should_noop", "completed_at", "status",
                          "task_id"])
        self.assertEqual(tombstone["task_id"], TID)
        self.assertEqual(tombstone["status"], "completed")
        self.assertEqual(tombstone["completed_at"], "2026-09-02T13:00:00Z")
        self.assertIs(tombstone["bridge_should_noop"], True)
        st = state.load_state(self.root, TID)
        self.assertEqual(st["continuation"]["tombstone"]["task_id"], TID)

    def test_tombstone_default_moment_and_idempotent(self):
        first = task_manager.write_completion_tombstone(self.root, TID)
        self.assertIn("completed_at", first)
        second = task_manager.write_completion_tombstone(self.root, TID)
        self.assertTrue(second["idempotent"])
        self.assertEqual(second["completed_at"], first["completed_at"])

    def test_tombstone_validates_and_survives_save_gate(self):
        with self.assertRaises(ValueError):
            task_manager.write_completion_tombstone(self.root, TID,
                                                    completed_at="junk")
        # 落盘形状过 validate_state（save_state 全量校验闸背书）
        st = state.load_state(self.root, TID)
        self.assertIsNone(st["continuation"]["tombstone"])
        task_manager.write_completion_tombstone(self.root, TID)
        errors = state.validate_state(state.load_state(self.root, TID))
        self.assertEqual(errors, [])


# —— degrade_continuity ——

class DegradeContinuityTest(WakePlannerTestBase):

    def test_degrade_writes_obligation_and_journal(self):
        make_task(self.root, auto_resume="manual")
        result = task_manager.degrade_continuity(self.root, TID)
        self.assertEqual(result["obligation"], "degraded")
        self.assertEqual(result["reason"], "scheduler_create_forbidden")
        st = state.load_state(self.root, TID)
        self.assertEqual(st["continuation"]["obligation"], "degraded")
        # wake_bridge.status 不动（降级的是连续性，不是桥本身）
        self.assertEqual(st["continuation"]["wake_bridge"]["status"],
                         "none")
        degraded = events(self.root, "continuity_degraded")
        self.assertEqual(len(degraded), 1)
        self.assertEqual(degraded[0]["reason"], "scheduler_create_forbidden")

    def test_degrade_idempotent(self):
        make_task(self.root, auto_resume="manual")
        task_manager.degrade_continuity(self.root, TID)
        with open(state.state_path(self.root, TID), "rb") as fh:
            before = fh.read()
        second = task_manager.degrade_continuity(self.root, TID)
        self.assertTrue(second["idempotent"])
        self.assertEqual(len(events(self.root, "continuity_degraded")), 1)
        with open(state.state_path(self.root, TID), "rb") as fh:
            self.assertEqual(fh.read(), before)

    def test_degrade_validates_reason(self):
        make_task(self.root, auto_resume="manual")
        with self.assertRaises(ValueError):
            task_manager.degrade_continuity(self.root, TID, reason="")


# —— scheduler_context 缺省容错（legacy 形态） ——

class LegacyStateToleranceTest(WakePlannerTestBase):

    def test_legacy_state_plans_with_defaults(self):
        make_legacy_task(self.root)  # 无 continuation / execution_policy
        plan = task_manager.plan_wake_bridge(
            self.root, TID, provider_status=None, windows=None)
        # legacy → auto_resume=manual + create=unknown + 无额度输入
        # （provider UNKNOWN → fail-open PRESSURE）→ SHOULD arm
        self.assertTrue(plan["required"])
        self.assertFalse(plan["eager"])
        self.assertIn("SHOULD", plan["reason"])
        self.assertIsNone(plan["boundary_id"])
        self.assertIsNone(plan["wake_at"])
        self.assertEqual(plan["bridge_interval_minutes"], 60)

    def test_legacy_manual_normal_without_quota_inputs_stays_conservative(
            self):
        make_legacy_task(self.root)
        # 显式 NORMAL 额度输入 → manual 不建桥
        plan = task_manager.plan_wake_bridge(
            self.root, TID, provider_status="AVAILABLE",
            windows=[window(remaining=90.0)])
        self.assertFalse(plan["required"])

    def test_provider_status_defaults_to_unknown_not_available(self):
        make_task(self.root, auto_resume="manual")
        # windows 无额度输入 + provider 缺省 → PRESSURE（fail-open）
        # → manual SHOULD arm（证明绝不默认 AVAILABLE）
        plan = task_manager.plan_wake_bridge(self.root, TID, windows=[])
        self.assertTrue(plan["required"])
        self.assertIn("SHOULD", plan["reason"])

    def test_task_status_gate(self):
        make_task(self.root, auto_resume="until_done", source="user",
                  max_windows=2, status="created")
        plan = task_manager.plan_wake_bridge(
            self.root, TID, provider_status="AVAILABLE",
            windows=[window()])
        self.assertFalse(plan["required"])
        self.assertIn("存活状态族", plan["reason"])

    def test_waiting_user_task_with_budget_left_can_rearm(self):
        # waiting_user 是 WAKE_PLAN_TASK_STATUSES 成员——重新授权后的
        # eager-arm 落点（预算有剩余时）
        make_task(self.root, auto_resume="until_done", source="user",
                  max_windows=2, consumed=1, status="waiting_user")
        plan = self.plan()
        self.assertTrue(plan["required"])
        self.assertTrue(plan["eager"])

    def test_missing_task_raises(self):
        with self.assertRaises(task_manager.TaskManagerError):
            task_manager.plan_wake_bridge(
                self.root, "missing-task-0001",
                provider_status="AVAILABLE", windows=[window()])


# —— CLI：wake-plan / wake-status ——

class WakePlanCliTest(WakePlannerTestBase):

    def setUp(self):
        super().setUp()
        make_task(self.root, auto_resume="until_done", source="user",
                  max_windows=2)

    def write_cache(self, *, status="AVAILABLE", windows=None):
        """按 resolver 缓存布局落盘 quota-cache.json（零网络）。"""
        directory = os.path.join(self.root, ".glm-conductor")
        os.makedirs(directory, exist_ok=True)
        payload = {
            "provider": "zai",
            "fetched_at": "2026-09-02T10:00:00.000Z",
            "status": status,
            "snapshot": {"windows": list(
                windows if windows is not None
                else [window(), window("weekly", 80.0, WEEKLY_RESET)])},
        }
        with open(os.path.join(directory, resolver.CACHE_FILE_NAME), "w",
                  encoding="utf-8", newline="\n") as fh:
            json.dump(payload, fh, ensure_ascii=False)

    def test_happy_path_outputs_frozen_nine_keys(self):
        self.write_cache()
        code, payload = run_cli("wake-plan", self.root, TID)
        self.assertEqual(code, 0)
        self.assertEqual(
            sorted(payload.keys()),
            ["boundary_id", "bridge_interval_minutes", "current_boundary_id",
             "eager", "mode", "prompt", "reason", "required", "wake_at"])
        self.assertTrue(payload["required"])
        self.assertTrue(payload["eager"])
        self.assertEqual(payload["boundary_id"], BOUNDARY)
        self.assertEqual(payload["bridge_interval_minutes"], 60)
        self.assertIn("DO NOT CREATE A NEW SCHEDULED TASK",
                      payload["prompt"])

    def test_without_cache_fails_open(self):
        # 无 quota-cache.json → provider UNKNOWN 保守（绝不默认 AVAILABLE）
        code, payload = run_cli("wake-plan", self.root, TID)
        self.assertEqual(code, 0)
        self.assertTrue(payload["required"])  # until_done eager 不看相位
        self.assertIsNone(payload["boundary_id"])

    def test_usage_error_exit_2(self):
        code, payload = run_cli("wake-plan", self.root)
        self.assertEqual(code, 2)
        self.assertIn("error", payload)
        code, _ = run_cli("wake-plan", self.root, TID, "extra")
        self.assertEqual(code, 2)

    def test_missing_task_exit_1(self):
        code, payload = run_cli("wake-plan", self.root, "missing-task-0001")
        self.assertEqual(code, 1)
        self.assertIn("error", payload)


class WakeStatusCliTest(WakePlannerTestBase):

    def setUp(self):
        super().setUp()
        make_task(self.root, auto_resume="until_done", source="user",
                  max_windows=2)

    def test_default_status_is_none_with_defaults(self):
        code, payload = run_cli("wake-status", self.root, TID)
        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "none")
        self.assertEqual(payload["mode"], "recurring")
        self.assertEqual(payload["generation"], 0)
        self.assertEqual(payload["obligation"], "none")
        self.assertEqual(payload["scheduler_context"]["origin"], "unknown")
        self.assertEqual(payload["scheduler_context"]["create"], "unknown")
        self.assertIsNone(payload["tombstone"])

    def test_armed_bridge_status_and_tombstone_visible(self):
        arm_bridge(self.root)
        task_manager.write_completion_tombstone(
            self.root, TID, completed_at="2026-09-02T13:00:00Z")
        code, payload = run_cli("wake-status", self.root, TID)
        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "armed")
        self.assertEqual(payload["automation_id"], AUTOMATION_ID)
        self.assertEqual(payload["current_boundary_id"], BOUNDARY)
        self.assertEqual(payload["bridge_interval_minutes"], 60)
        self.assertEqual(payload["wake_bridge"]["mode"], "recurring")
        self.assertEqual(payload["tombstone"]["bridge_should_noop"], True)

    def test_usage_error_exit_2(self):
        code, _ = run_cli("wake-status", self.root)
        self.assertEqual(code, 2)

    def test_missing_task_exit_1(self):
        code, payload = run_cli("wake-status", self.root,
                                "missing-task-0001")
        self.assertEqual(code, 1)
        self.assertIn("error", payload)


# —— C1a（wu-22-C1a）：persistent path 零窗口消费 ——

class C1aZeroConsumptionTests(WakePlannerTestBase):
    """C1a（wu-22-C1a，D15-g）：bridge 生命周期 arm/fire/retarget 全链
    不消费窗口预算——机械钉死 consumed_quota_windows 只能被 v2.1
    legacy 入口 record_quota_wake 改写，persistent path 上的任何
    bridge 记账都不碰它（消费点唯一合法位置 = resume commit point
    §15.1，C1b 落地新 API）。"""

    def test_bridge_lifecycle_never_consumes_windows(self):
        make_task(self.root, auto_resume="until_done", source="user",
                  max_windows=2, consumed=1)

        def consumed_value():
            st = state.load_state(self.root, TID)
            return (st["execution_policy"]["continuity"]
                    ["consumed_quota_windows"])

        self.assertEqual(consumed_value(), 1)  # 消费起点
        task_manager.arm_wake_bridge(
            self.root, TID, automation_id=AUTOMATION_ID,
            boundary_id=BOUNDARY, reset_at=RESET_AT,
            wake_at="2026-09-02T12:05:00Z")
        self.assertEqual(consumed_value(), 1)  # arm 不消费
        for hour in range(1, 11):  # fire 连续 10 次（每次不同 fired_at）
            task_manager.record_bridge_fired(
                self.root, TID, fired_at="2026-09-03T%02d:00:00Z" % hour)
            self.assertEqual(consumed_value(), 1)  # fire 不消费
        new_boundary = "five_hour:2026-09-02T21:59:00Z"
        task_manager.retarget_wake_bridge(
            self.root, TID, boundary_id=new_boundary,
            reset_at="2026-09-02T21:59:00Z",
            wake_at="2026-09-02T22:04:00Z")
        self.assertEqual(consumed_value(), 1)  # retarget 不消费
        rearm = task_manager.arm_wake_bridge(
            self.root, TID, automation_id=AUTOMATION_ID,
            boundary_id=new_boundary, reset_at="2026-09-02T21:59:00Z",
            wake_at="2026-09-02T22:04:00Z")
        self.assertTrue(rearm["idempotent"])  # 幂等 no-op 路径
        self.assertEqual(consumed_value(), 1)
        self.assertEqual(
            events(self.root, "quota_wake_recorded"), [])  # 零扣减事件
        self.assertEqual(len(events(self.root, "wake_bridge_fired")),
                         10)  # fire 真实记账（测试非空洞）

    def test_bridge_docstrings_no_legacy_discipline(self):
        """C1a：arm/plan docstring 不再串联「arm 后 wake-record 记账」
        旧纪律（legacy 入口标注只归 record_quota_wake 自身）。"""
        self.assertNotIn("wake-record", task_manager.arm_wake_bridge.__doc__)
        self.assertNotIn("record_quota_wake",
                         task_manager.arm_wake_bridge.__doc__)
        self.assertNotIn("wake-record", task_manager.plan_wake_bridge.__doc__)
        self.assertNotIn("record_quota_wake",
                         task_manager.plan_wake_bridge.__doc__)


if __name__ == "__main__":
    unittest.main()
