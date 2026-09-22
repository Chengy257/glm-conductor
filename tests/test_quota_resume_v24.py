#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""runtime.task 配额恢复授权与等待/恢复生命周期单元测试（Phase 3 单元 Q1，P3-B/C）。

运行：
    cd <repo_root> && python3 -X utf8 -m unittest tests.test_quota_resume_v24 -v

覆盖（Q1 单元规格最低集 + 补充）：
    - manual 默认永不授权：quota_resume 默认恰为 manual/0/0/null；
      manual 任务可用观测的决策 action=waiting-user，状态与计数
      原地不动；
    - authorize_quota_resume：显式授权置 mode=auto + 预算；仅
      waiting_quota / active 可授权；非法 max（负数 / bool / 非 int）
      与「max 小于已用计数」拒绝；授权不重置已用计数、不落 journal；
    - enter_waiting_quota 观测记录：实际进入等待时把最近一次观测
      （status/reset_at/observed_at，source 不落盘）记入
      quota_resume.last_observation 供诊断；无观测进入不写该键；
      观测形状非法拒绝且零副作用；重复进入不刷新观测、不重复落
      事件；
    - scheduled_activation_decision 四 action 封闭词汇：非
      waiting_quota（含 active / waiting_user / 终态）→ no-op 重复
      唤醒安全；EXHAUSTED/UNKNOWN → remain-waiting 绝不虚构可用性；
      manual 未授权 → waiting-user 不转态；预算耗尽 → waiting-user
      且恰一次转 waiting_user（再次唤醒 no-op）；可用 + 已授权 +
      预算有余 → resume-authorized 带 workflow_run_id 与暂存
      resume_count_after，磁盘计数不动，确认前重复决策恒同值；
      无关联 run / 观测形状非法 / 任务缺失 → ValueError；
    - confirm_resume_started 暂存落账：确认后磁盘计数 +1、任务转回
      active（phase=workflow）、落 quota_resume_confirmed 事件；
      重复确认 / manual / 计数形状异常 / 预算已耗尽一律拒绝（重复
      确认绝不重复消耗预算）；第二窗口恢复后预算耗尽的完整闭环；
    - bind/clear/preflight 三公共 API（v2.4.1 H2，规格 §4.5 最低
      14 用例）：manual 预检 ready；auto 仅授权未武装 → 预检不
      ready；bind 后 ready；同 id 重绑幂等；异 id 重绑拒绝（绝不
      静默替换）；clear 精确 id → 解除武装；clear None 幂等；
      clear 错误期望 id 拒绝；终态清理允许；非法 automation id
      拒绝；bind 不改 mode 与预算（绝不推断 mode=auto）；clear
      不改 mode 与预算；auto 无武装决策绝不走向宿主 resume 路径
      （remain-waiting + continuity-unarmed）；无武装唤醒零状态
      写盘零预算消耗；
    - v2.4.1 契约强化（INV-CONT-01）：标准前置 prepare_waiting 先
      bind 武装再进入等待——既有 resume-authorized / confirm 用例
      在已武装任务上断言（先武装后开工；契约加强而非测试弱化），
      scheduled_activation_decision 在预算检查之后、resume-
      authorized 之前插入 continuity-unarmed 闸；
    - state 层校验收口：mode 恰为 manual/auto（auto 合法，v2.3 四
      模式旧词拒绝）；max_resumes/resume_count 为 >= 0 整数（bool
      拒绝）；预算不变量 resume_count <= max_resumes（save_state
      同闸）。

全部离线：任务账本在 tempfile.TemporaryDirectory 内，绝不触碰仓库
内 .glm-conductor/ 真实账本；adapter 的 RUNS_DIR 每测试注入临时
目录（模块文档化的注入点）。仅 Python 3 标准库（unittest +
tempfile），零第三方依赖。
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "glm-conductor"))

from runtime import journal, state, task, writer_guard  # noqa: E402
from runtime.workflow import adapter as workflow_adapter  # noqa: E402


# —— 测试夹具 ——

def quota_view(status, reset_at=None, observed_at="2026-09-21T12:00:00.000Z",
               source="provider"):
    """构造归一化配额观测 dict（与 resolver/report 的归一化答案同形）。"""
    return {"status": status, "reset_at": reset_at, "source": source,
            "observed_at": observed_at}


class QuotaResumeCase(unittest.TestCase):
    """提供每测试隔离的临时仓库根 + adapter RUNS_DIR 注入。"""

    TID = "quota-resume-a"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = self._tmp.name
        # adapter 的 run 关联记录相对 CWD 落盘（模块级常量 RUNS_DIR 是
        # 文档化的测试注入点）——注入临时目录，绝不污染真实仓库
        patcher = mock.patch.object(
            workflow_adapter, "RUNS_DIR",
            os.path.join(self._tmp.name, ".glm-conductor", "workflow-runs"))
        patcher.start()
        self.addCleanup(patcher.stop)

    # —— 装置助手 ——

    def make_task(self, task_id=TID):
        """在临时仓库创建缺省活动任务（manual / 零预算，未授权）。"""
        return task.create_task(
            self.repo, task_id, "验证 v2.4 配额恢复授权与等待/恢复生命周期",
            {"mode": "delegate"})

    def prepare_waiting(self, max_resumes=3, run_id="run-77", view=None,
                        task_id=TID, automation_id="sched-witness-1"):
        """授权 + 武装 + 关联 run + 进入等待（标准前置；view 缺省不带观测）。

        v2.4.1 契约（INV-CONT-01 先武装后开工）：auto 授权后先 bind
        未来定时激活见证再进入等待——既有 resume-authorized / confirm
        用例在已武装任务上断言；automation_id=None 可构造「已授权但
        未武装」的 auto 任务，供 continuity-unarmed 闸用例使用。

        delegate 任务按协议先取本仓库写者守卫（AF-04：record_workflow_run
        前置要求当前任务持有写预约），再注册委派 run。
        """
        self.make_task(task_id)
        if run_id is not None:
            writer_guard.acquire(self.repo, task_id, run_id)
            task.record_workflow_run(self.repo, task_id, run_id)
        task.authorize_quota_resume(self.repo, task_id, max_resumes)
        if automation_id is not None:
            task.bind_quota_automation(self.repo, task_id, automation_id)
        task.enter_waiting_quota(self.repo, task_id, view)
        return self.load(task_id)

    def authorize(self, max_resumes, task_id=TID):
        return task.authorize_quota_resume(self.repo, task_id, max_resumes)

    def decide(self, view, task_id=TID):
        return task.scheduled_activation_decision(self.repo, task_id, view)

    def confirm(self, task_id=TID):
        return task.confirm_resume_started(self.repo, task_id)

    def load(self, task_id=TID):
        return state.load_state(self.repo, task_id)

    def events(self, task_id=TID):
        """按文件顺序读取临时仓库里该任务的全部 journal 事件。"""
        return journal.read_events(self.repo, task_id)

    def names(self, task_id=TID):
        return [item.get("event") for item in self.events(task_id)]


# —— 1. manual 默认永不授权 + authorize 授权语义 ——

class TestAuthorize(QuotaResumeCase):

    def test_default_manual_never_authorizes(self):
        """默认 manual/0/0/null：可用观测决策 waiting-user，零转态零计数。"""
        st = self.make_task()
        self.assertEqual(st["quota_resume"],
                         {"mode": "manual", "max_resumes": 0,
                          "resume_count": 0, "automation_id": None})
        task.enter_waiting_quota(self.repo, self.TID)
        decision = self.decide(quota_view("AVAILABLE"))
        self.assertEqual(decision["action"], "waiting-user")
        loaded = self.load()
        self.assertEqual(loaded["status"], "waiting_quota")
        self.assertEqual(loaded["quota_resume"]["mode"], "manual")
        self.assertEqual(loaded["quota_resume"]["resume_count"], 0)

    def test_authorize_sets_auto_and_budget(self):
        """显式授权：mode=auto + 预算落盘；active 态即可授权。"""
        self.make_task()
        st = self.authorize(3)
        self.assertEqual(st["quota_resume"]["mode"], "auto")
        self.assertEqual(st["quota_resume"]["max_resumes"], 3)
        self.assertEqual(st["quota_resume"]["resume_count"], 0)
        self.assertIsNone(st["quota_resume"]["automation_id"])
        self.assertEqual(state.validate_state(st), [])
        self.assertEqual(self.load()["quota_resume"]["mode"], "auto")
        # 授权不落 journal 事件（授权事实由 state 落盘记录）
        self.assertEqual(self.names(), ["route_selected"])

    def test_authorize_in_waiting_quota_allowed(self):
        """waiting_quota 态同样可授权（等待中的显式授权通道）。"""
        self.make_task()
        task.enter_waiting_quota(self.repo, self.TID)
        st = self.authorize(2)
        self.assertEqual(st["quota_resume"]["mode"], "auto")
        self.assertEqual(st["status"], "waiting_quota")

    def test_authorize_requires_waiting_or_active(self):
        """blocked / waiting_user / 终态 → 授权拒绝。"""
        self.make_task()
        task.set_status(self.repo, self.TID, "blocked")
        with self.assertRaises(ValueError):
            self.authorize(2)
        task.set_status(self.repo, self.TID, "active")
        task.set_status(self.repo, self.TID, "waiting_user")
        with self.assertRaises(ValueError):
            self.authorize(2)
        task.set_status(self.repo, self.TID, "active")
        task.complete(self.repo, self.TID)
        with self.assertRaises(ValueError):
            self.authorize(2)
        self.assertEqual(self.load()["quota_resume"]["mode"], "manual")

    def test_authorize_invalid_max_refused(self):
        """负数 / bool / 非整数 max → 拒绝，授权块零改动。"""
        self.make_task()
        for bad in (-1, True, False, "3", 1.5, None):
            with self.assertRaises(ValueError):
                self.authorize(bad)
        self.assertEqual(self.load()["quota_resume"]["mode"], "manual")
        self.assertEqual(self.load()["quota_resume"]["max_resumes"], 0)

    def test_authorize_max_below_used_count_refused(self):
        """max 小于已用 resume_count → 拒绝（预算不变量前置守卫）。"""
        self.prepare_waiting(max_resumes=2)
        self.decide(quota_view("AVAILABLE"))
        self.confirm()
        self.assertEqual(self.load()["quota_resume"]["resume_count"], 1)
        with self.assertRaises(ValueError):
            self.authorize(0)
        self.assertEqual(self.load()["quota_resume"]["max_resumes"], 2)

    def test_authorize_missing_task_refused(self):
        """任务不存在 → 拒绝。"""
        with self.assertRaises(ValueError):
            self.authorize(2, task_id="ghost-task")


# —— 2. enter_waiting_quota 观测记录（P3-C 进入等待诊断面） ——

class TestEnterObservation(QuotaResumeCase):

    def test_enter_records_observation(self):
        """进入等待时把 status/reset_at/observed_at 记入 last_observation。"""
        self.make_task()
        writer_guard.acquire(self.repo, self.TID, "run-1")
        task.record_workflow_run(self.repo, self.TID, "run-1")
        view = quota_view("EXHAUSTED",
                          reset_at="2026-09-21T17:00:00.000Z",
                          observed_at="2026-09-21T12:00:00.000Z")
        task.enter_waiting_quota(self.repo, self.TID, view)
        block = self.load()["quota_resume"]
        self.assertEqual(
            block["last_observation"],
            {"status": "EXHAUSTED",
             "reset_at": "2026-09-21T17:00:00.000Z",
             "observed_at": "2026-09-21T12:00:00.000Z"})
        # source 不落盘（单元规格只要求三键）；诊断键不破 schema
        self.assertNotIn("source", block["last_observation"])
        self.assertEqual(state.validate_state(self.load()), [])
        # enter 事件仍是恰一条 waiting_quota（direction=enter），无附加字段
        self.assertEqual(
            self.names(),
            ["route_selected", "workflow_started", "waiting_quota"])
        self.assertEqual(self.events()[-1],
                         {"ts": self.events()[-1]["ts"],
                          "event": "waiting_quota", "direction": "enter"})

    def test_enter_without_view_no_observation(self):
        """不带观测进入（既有调用面）→ 不写 last_observation 键。"""
        self.make_task()
        task.enter_waiting_quota(self.repo, self.TID)
        self.assertNotIn("last_observation", self.load()["quota_resume"])

    def test_enter_bad_view_refused_zero_side_effect(self):
        """观测非 dict / status 词汇外 → 拒绝，状态与观测零副作用。"""
        self.make_task()
        for bad in ("EXHAUSTED", 123,
                    quota_view("DRAINING"),  # v2.3 执行阶段旧词，词汇外
                    {"reset_at": None}):  # 缺 status 键
            with self.assertRaises(ValueError):
                task.enter_waiting_quota(self.repo, self.TID, bad)
        self.assertEqual(self.load()["status"], "active")
        self.assertNotIn("last_observation", self.load()["quota_resume"])
        self.assertEqual(self.names(), ["route_selected"])

    def test_enter_repeat_keeps_first_observation(self):
        """重复进入不刷新观测、不重复落 enter 事件（重入安全）。"""
        self.make_task()
        task.enter_waiting_quota(
            self.repo, self.TID,
            quota_view("EXHAUSTED", reset_at="2026-09-21T15:00:00.000Z"))
        task.enter_waiting_quota(
            self.repo, self.TID,
            quota_view("AVAILABLE", reset_at="2026-09-21T18:00:00.000Z"))
        self.assertEqual(
            self.load()["quota_resume"]["last_observation"]["status"],
            "EXHAUSTED")
        self.assertEqual(self.names().count("waiting_quota"), 1)


# —— 3. scheduled_activation_decision 四 action ——

class TestScheduledDecision(QuotaResumeCase):

    def test_not_waiting_quota_noop(self):
        """非 waiting_quota（active / waiting_user / 终态）→ no-op。"""
        self.make_task()
        self.assertEqual(
            self.decide(quota_view("AVAILABLE"))["action"], "no-op")
        self.assertEqual(self.load()["status"], "active")
        # 终态任务被陈旧定时唤醒 → 同样 no-op（重复唤醒安全）
        task.complete(self.repo, self.TID)
        self.assertEqual(
            self.decide(quota_view("AVAILABLE"))["action"], "no-op")
        self.assertEqual(self.load()["status"], "completed")

    def test_exhausted_and_unknown_remain_waiting(self):
        """EXHAUSTED / UNKNOWN → remain-waiting（绝不虚构可用性）。"""
        self.prepare_waiting(max_resumes=3)
        for status in ("EXHAUSTED", "UNKNOWN"):
            decision = self.decide(quota_view(status))
            self.assertEqual(decision["action"], "remain-waiting")
            loaded = self.load()
            self.assertEqual(loaded["status"], "waiting_quota")
            self.assertEqual(loaded["quota_resume"]["resume_count"], 0)

    def test_manual_waiting_user_no_transition(self):
        """manual 未授权 → waiting-user，状态与计数原地不动。"""
        self.make_task()
        writer_guard.acquire(self.repo, self.TID, "run-1")
        task.record_workflow_run(self.repo, self.TID, "run-1")
        task.enter_waiting_quota(self.repo, self.TID)
        decision = self.decide(quota_view("PRESSURE"))
        self.assertEqual(decision["action"], "waiting-user")
        loaded = self.load()
        self.assertEqual(loaded["status"], "waiting_quota")
        self.assertEqual(loaded["quota_resume"]["resume_count"], 0)

    def test_budget_exhausted_moves_waiting_user_once(self):
        """预算耗尽 → waiting-user 且恰一次转 waiting_user；再次唤醒 no-op。"""
        self.prepare_waiting(max_resumes=1)
        self.assertEqual(
            self.decide(quota_view("AVAILABLE"))["action"],
            "resume-authorized")
        self.confirm()  # count=1，预算用尽
        task.enter_waiting_quota(self.repo, self.TID)
        decision = self.decide(quota_view("AVAILABLE"))
        self.assertEqual(decision["action"], "waiting-user")
        self.assertIn("预算已耗尽", decision["reason"])
        self.assertEqual(self.load()["status"], "waiting_user")
        # 幂等：再次唤醒任务已非 waiting_quota → no-op（恰一次转态）
        self.assertEqual(
            self.decide(quota_view("AVAILABLE"))["action"], "no-op")
        self.assertEqual(self.load()["status"], "waiting_user")
        self.assertEqual(self.load()["quota_resume"]["resume_count"], 1)

    def test_zero_budget_authorize_immediately_waiting_user(self):
        """授权零预算（max=0）：可用观测直接 waiting-user + 转 waiting_user。"""
        self.prepare_waiting(max_resumes=0)
        decision = self.decide(quota_view("AVAILABLE"))
        self.assertEqual(decision["action"], "waiting-user")
        self.assertEqual(self.load()["status"], "waiting_user")

    def test_resume_authorized_stages_count(self):
        """可用 + 已授权 + 预算有余 → resume-authorized 暂存计数正确。"""
        self.prepare_waiting(max_resumes=3)
        decision = self.decide(quota_view("AVAILABLE"))
        self.assertEqual(decision["action"], "resume-authorized")
        self.assertEqual(decision["workflow_run_id"], "run-77")
        self.assertEqual(decision["resume_count_after"], 1)
        # 暂存不落盘：磁盘计数仍为 0
        self.assertEqual(self.load()["quota_resume"]["resume_count"], 0)
        self.assertEqual(self.load()["status"], "waiting_quota")
        # PRESSURE 同样视为可用
        decision = self.decide(quota_view("PRESSURE"))
        self.assertEqual(decision["action"], "resume-authorized")
        self.assertEqual(decision["resume_count_after"], 1)

    def test_three_unconfirmed_decisions_consume_nothing(self):
        """确认前重复决策幂等：连续三次决策后磁盘计数仍为零。"""
        self.prepare_waiting(max_resumes=3)
        for _ in range(3):
            decision = self.decide(quota_view("AVAILABLE"))
            self.assertEqual(decision["action"], "resume-authorized")
            self.assertEqual(decision["resume_count_after"], 1)
        self.assertEqual(self.load()["quota_resume"]["resume_count"], 0)
        self.assertEqual(self.load()["status"], "waiting_quota")

    def test_confirm_commits_count_and_reactivates(self):
        """确认 → 计数落账 1、转回 active（workflow 阶段）、确认事件落账。"""
        self.prepare_waiting(max_resumes=3)
        self.decide(quota_view("AVAILABLE"))
        st = self.confirm()
        self.assertEqual(st["quota_resume"]["resume_count"], 1)
        self.assertEqual(st["status"], "active")
        self.assertEqual(st["phase"], "workflow")
        loaded = self.load()
        self.assertEqual(loaded["quota_resume"]["resume_count"], 1)
        self.assertEqual(loaded["status"], "active")
        self.assertEqual(loaded["phase"], "workflow")
        # quota_resume_confirmed 事件：任务级词汇内、携带计数与 run
        self.assertEqual(
            self.names(),
            ["route_selected", "workflow_started", "waiting_quota",
             "quota_resume_confirmed"])
        event = self.events()[-1]
        self.assertEqual(event["resume_count"], 1)
        self.assertEqual(event["max_resumes"], 3)
        self.assertEqual(event["workflow_run_id"], "run-77")
        self.assertIn(event["event"], task.TASK_JOURNAL_EVENTS)

    def test_double_confirm_refused_count_still_one(self):
        """重复确认 → 拒绝（无暂存决策），计数不再消耗。"""
        self.prepare_waiting(max_resumes=3)
        self.decide(quota_view("AVAILABLE"))
        self.confirm()
        with self.assertRaises(ValueError):
            self.confirm()
        self.assertEqual(self.load()["quota_resume"]["resume_count"], 1)
        self.assertEqual(self.load()["status"], "active")

    def test_confirm_manual_refused(self):
        """manual 任务（未授权）→ 确认拒绝。"""
        self.make_task()
        task.enter_waiting_quota(self.repo, self.TID)
        with self.assertRaises(ValueError):
            self.confirm()
        self.assertEqual(self.load()["quota_resume"]["resume_count"], 0)

    def test_confirm_exhausted_budget_refused(self):
        """预算已耗尽（count >= max）→ 确认拒绝（守卫预算不变量）。"""
        self.prepare_waiting(max_resumes=1)
        st = self.load()
        st["quota_resume"]["resume_count"] = 1
        state.save_state(self.repo, st)
        self.assertEqual(st["status"], "waiting_quota")
        with self.assertRaises(ValueError):
            self.confirm()

    def test_second_window_completes_then_exhausts(self):
        """完整闭环：第一窗口恢复确认 → 第二窗口可用恢复 → 预算尽转用户。"""
        self.prepare_waiting(max_resumes=2)
        self.assertEqual(
            self.decide(quota_view("AVAILABLE"))["resume_count_after"], 1)
        self.confirm()
        # 第二次额度等待：再决策得暂存 2，确认后计数 2
        task.enter_waiting_quota(self.repo, self.TID)
        self.assertEqual(
            self.decide(quota_view("AVAILABLE"))["resume_count_after"], 2)
        self.confirm()
        self.assertEqual(self.load()["quota_resume"]["resume_count"], 2)
        # 第三次等待：预算耗尽 → waiting_user（新窗口绝不自动重置预算）
        task.enter_waiting_quota(self.repo, self.TID)
        self.assertEqual(
            self.decide(quota_view("AVAILABLE"))["action"], "waiting-user")
        self.assertEqual(self.load()["status"], "waiting_user")
        for name in self.names():
            self.assertIn(name, task.TASK_JOURNAL_EVENTS)

    def test_missing_run_id_refused(self):
        """已授权但无关联 workflow_run_id → 拒绝（无 run 可恢复）。"""
        self.prepare_waiting(max_resumes=3, run_id=None)
        with self.assertRaises(ValueError):
            self.decide(quota_view("AVAILABLE"))

    def test_bad_quota_view_refused(self):
        """观测非 dict / status 词汇外 → ValueError（零副作用）。"""
        self.prepare_waiting(max_resumes=3)
        for bad in (None, "AVAILABLE", 123,
                    {"reset_at": None, "source": "provider"},
                    quota_view("DRAINING"), quota_view("available")):
            with self.assertRaises(ValueError):
                self.decide(bad)
        self.assertEqual(self.load()["status"], "waiting_quota")

    def test_missing_task_refused(self):
        """任务不存在 → ValueError。"""
        with self.assertRaises(ValueError):
            self.decide(quota_view("AVAILABLE"), task_id="ghost-task")


# —— 3b. auto 连续性武装：bind / clear / preflight（v2.4.1 H2，§4.5） ——

class TestAutomationBinding(QuotaResumeCase):
    """automation 绑定 / 清理 / 连续性预检 + 决策原语 unarmed 闸。

    规格 §4.5 最低 14 用例逐条落位（编号对齐规格清单）：
      1 manual 预检 ready；2 auto 仅授权未武装 → 预检不 ready；
      3 bind 后 ready；4 同 id 幂等；5 异 id 拒绝；6 clear 精确 id
      → 解除武装；7 clear None 幂等；8 clear 错误期望 id 拒绝；
      9 终态清理允许；10 非法 automation id 拒绝；11 bind 不改
      mode 与预算；12 clear 不改 mode 与预算；13 auto 无武装决策
      不得走向宿主 resume 路径；14 无武装唤醒零预算消耗。
    """

    AUTO_ID = "sched-witness-1"
    TID = QuotaResumeCase.TID

    def arm(self, task_id=TID, automation_id=AUTO_ID, max_resumes=3):
        """武装前置：建任务 → 授权 → 绑定（bind 不改 mode/状态）。"""
        self.make_task(task_id)
        task.authorize_quota_resume(self.repo, task_id, max_resumes)
        return task.bind_quota_automation(self.repo, task_id, automation_id)

    def state_bytes(self, task_id=TID):
        """读取 state.json 原始字节（零写盘断言用）。"""
        return state.state_path(self.repo, task_id).read_bytes()

    # 1. manual preflight ready without automation

    def test_manual_preflight_ready_without_automation(self):
        """manual 未授权未武装 → 预检 ready=true（manual 无需定时激活）。"""
        self.make_task()
        before = self.state_bytes()
        report = task.quota_continuity_preflight(self.repo, self.TID)
        self.assertEqual(report, {
            "ready": True, "mode": "manual", "automation_id": None,
            "reason": "manual mode does not require scheduled activation"})
        self.assertEqual(self.state_bytes(), before,
                         "preflight 纯读：绝不变更状态文件")

    # 2. auto authorization alone -> preflight not ready

    def test_auto_authorized_but_preflight_not_ready(self):
        """auto 已授权但未绑定 → 预检 ready=false（unarmed 即阻塞）。"""
        self.make_task()
        task.authorize_quota_resume(self.repo, self.TID, 3)
        report = task.quota_continuity_preflight(self.repo, self.TID)
        self.assertEqual(report, {
            "ready": False, "mode": "auto", "automation_id": None,
            "reason": "auto continuity is not armed"})

    # 3. bind -> ready

    def test_bind_then_preflight_ready(self):
        """绑定后预检 ready=true；绑定事实落盘；journal 零新增事件。"""
        self.arm()
        report = task.quota_continuity_preflight(self.repo, self.TID)
        self.assertEqual(report, {
            "ready": True, "mode": "auto",
            "automation_id": self.AUTO_ID,
            "reason": "auto continuity armed"})
        self.assertEqual(
            self.load()["quota_resume"]["automation_id"], self.AUTO_ID)
        # bind / preflight 对 journal 零新增（arm 仅落 route_selected）
        self.assertEqual(self.names(), ["route_selected"])
        for name in self.names():
            self.assertIn(name, task.TASK_JOURNAL_EVENTS)

    # 4. same-id rebind idempotent

    def test_bind_same_id_idempotent(self):
        """同 id 重绑 → 幂等成功，绑定与预算原样不动。"""
        self.arm()
        before = self.load()["quota_resume"]
        st = task.bind_quota_automation(self.repo, self.TID, self.AUTO_ID)
        self.assertEqual(st["quota_resume"]["automation_id"], self.AUTO_ID)
        self.assertEqual(st["quota_resume"], before)
        self.assertEqual(
            self.load()["quota_resume"]["automation_id"], self.AUTO_ID)

    # 5. different-id rebind rejected

    def test_bind_different_id_refused(self):
        """异 id 重绑 → 拒绝（绝不静默替换），存量绑定原样保留。"""
        self.arm()
        with self.assertRaises(ValueError) as ctx:
            task.bind_quota_automation(self.repo, self.TID, "other-sched")
        self.assertIn("静默替换", str(ctx.exception))
        self.assertEqual(
            self.load()["quota_resume"]["automation_id"], self.AUTO_ID)
        self.assertEqual(
            task.quota_continuity_preflight(self.repo, self.TID)
            ["automation_id"], self.AUTO_ID)

    # 6. clear exact id -> unarmed

    def test_clear_exact_id_unarms(self):
        """clear 精确 id → 绑定清为 None，预检转 unarmed。"""
        self.arm()
        st = task.clear_quota_automation(
            self.repo, self.TID, self.AUTO_ID)
        self.assertIsNone(st["quota_resume"]["automation_id"])
        self.assertIsNone(self.load()["quota_resume"]["automation_id"])
        report = task.quota_continuity_preflight(self.repo, self.TID)
        self.assertFalse(report["ready"])
        self.assertEqual(report["reason"], "auto continuity is not armed")

    # 7. clear None when already clear idempotent

    def test_clear_none_when_already_clear_idempotent(self):
        """存量 None（从未绑定 / 已清理）→ clear 幂等成功零写盘。"""
        self.make_task()
        before = self.state_bytes()
        st = task.clear_quota_automation(self.repo, self.TID)
        self.assertIsNone(st["quota_resume"]["automation_id"])
        self.assertEqual(self.state_bytes(), before, "幂等成功零写盘")
        # 已绑定任务 clear 后再次 clear → 同样幂等零写盘
        task.authorize_quota_resume(self.repo, self.TID, 3)
        task.bind_quota_automation(self.repo, self.TID, self.AUTO_ID)
        task.clear_quota_automation(self.repo, self.TID)
        after_first_clear = self.state_bytes()
        st = task.clear_quota_automation(self.repo, self.TID)
        self.assertIsNone(st["quota_resume"]["automation_id"])
        self.assertEqual(self.state_bytes(), after_first_clear)

    # 8. clear wrong expected id rejected

    def test_clear_wrong_expected_id_refused(self):
        """clear 期望 id 与存量不一致 → 拒绝，存量绑定原样保留。"""
        self.arm()
        with self.assertRaises(ValueError) as ctx:
            task.clear_quota_automation(
                self.repo, self.TID, "wrong-expected-id")
        self.assertIn("不一致", str(ctx.exception))
        self.assertEqual(
            self.load()["quota_resume"]["automation_id"], self.AUTO_ID)

    # 9. terminal task cleanup allowed

    def test_terminal_cleanup_allowed(self):
        """终态清理允许：completed 后 clear 放行（清后才能删绑定）。"""
        self.arm()
        self.assertEqual(
            self.load()["quota_resume"]["automation_id"], self.AUTO_ID)
        task.complete(self.repo, self.TID)
        st = task.clear_quota_automation(self.repo, self.TID)
        self.assertIsNone(st["quota_resume"]["automation_id"])
        self.assertIsNone(self.load()["quota_resume"]["automation_id"])
        # 清理不动 mode / 预算 / 状态
        loaded = self.load()
        self.assertEqual(loaded["status"], "completed")
        self.assertEqual(loaded["quota_resume"]["mode"], "auto")
        self.assertEqual(loaded["quota_resume"]["max_resumes"], 3)
        self.assertEqual(loaded["quota_resume"]["resume_count"], 0)

    # 10. invalid automation ids rejected

    def test_invalid_automation_ids_refused(self):
        """空串 / None / 非 str 的 automation id → 拒绝且零副作用。"""
        self.make_task()
        for bad in ("", None, 123, b"sched-id", ["sched-id"]):
            with self.assertRaises(ValueError):
                task.bind_quota_automation(self.repo, self.TID, bad)
        self.assertIsNone(self.load()["quota_resume"]["automation_id"])
        self.assertEqual(self.load()["quota_resume"]["mode"], "manual")
        # 任务不存在 / v2.3 遗留同样拒绝（结构性拒绝 ValueError）
        with self.assertRaises(ValueError):
            task.bind_quota_automation(self.repo, "ghost-task", self.AUTO_ID)
        with self.assertRaises(ValueError):
            task.quota_continuity_preflight(self.repo, "ghost-task")

    # 11. bind does not alter mode or budget

    def test_bind_does_not_alter_mode_or_budget(self):
        """bind 只写 automation_id：不改 mode / 预算 / 状态，绝不推断 auto。"""
        # manual 任务绑定 → mode 仍是 manual（绑定 ≠ 授权自动恢复）
        self.make_task()
        st = task.bind_quota_automation(self.repo, self.TID, self.AUTO_ID)
        self.assertEqual(st["quota_resume"], {
            "mode": "manual", "max_resumes": 0, "resume_count": 0,
            "automation_id": self.AUTO_ID})
        self.assertEqual(st["status"], "active")
        # manual + 已武装：预检仍按 manual 分支 ready（无需定时激活）
        report = task.quota_continuity_preflight(self.repo, self.TID)
        self.assertTrue(report["ready"])
        self.assertEqual(report["mode"], "manual")
        # auto 任务绑定 → 授权与预算逐键原样
        self.arm(task_id="bind-auto-a", max_resumes=5)
        loaded = self.load("bind-auto-a")
        self.assertEqual(loaded["quota_resume"]["mode"], "auto")
        self.assertEqual(loaded["quota_resume"]["max_resumes"], 5)
        self.assertEqual(loaded["quota_resume"]["resume_count"], 0)
        self.assertEqual(loaded["status"], "active")

    # 12. clear does not alter mode or budget

    def test_clear_does_not_alter_mode_or_budget(self):
        """clear 只清 automation_id：mode / max_resumes / resume_count 原样。"""
        self.prepare_waiting(max_resumes=3)
        self.decide(quota_view("AVAILABLE"))
        self.confirm()  # resume_count=1，预算已部分消耗
        task.enter_waiting_quota(self.repo, self.TID)
        before = self.load()["quota_resume"]
        self.assertEqual(before["resume_count"], 1)
        st = task.clear_quota_automation(self.repo, self.TID)
        self.assertEqual(st["quota_resume"], {
            "mode": "auto", "max_resumes": 3, "resume_count": 1,
            "automation_id": None})
        self.assertEqual(self.load()["status"], "waiting_quota")

    # 13. auto scheduled decision cannot lead to host resume path unarmed

    def test_unarmed_auto_decision_cannot_reach_resume(self):
        """auto 已授权未武装 → 决策 remain-waiting（continuity-unarmed），
        绝不给出 resume-authorized、绝不携带宿主 resume 所需 run id。"""
        self.prepare_waiting(automation_id=None)
        decision = self.decide(quota_view("AVAILABLE"))
        self.assertEqual(decision["action"], "remain-waiting")
        self.assertIn("continuity-unarmed", decision["reason"])
        self.assertNotIn("resume-authorized", decision["action"])
        self.assertNotIn("workflow_run_id", decision)
        self.assertNotIn("resume_count_after", decision)
        # 同一任务武装后同一观测 → resume-authorized（闸只拦未武装）
        task.bind_quota_automation(self.repo, self.TID, self.AUTO_ID)
        decision = self.decide(quota_view("AVAILABLE"))
        self.assertEqual(decision["action"], "resume-authorized")
        self.assertEqual(decision["workflow_run_id"], "run-77")

    # 14. no budget consumption on unarmed wake

    def test_unarmed_wake_consumes_no_budget(self):
        """无武装唤醒：零状态写盘、零预算消耗、状态原地 waiting_quota。"""
        self.prepare_waiting(automation_id=None)
        before = self.state_bytes()
        for _ in range(3):
            decision = self.decide(quota_view("AVAILABLE"))
            self.assertEqual(decision["action"], "remain-waiting")
            self.assertIn("continuity-unarmed", decision["reason"])
        self.assertEqual(self.state_bytes(), before,
                         "unarmed 决策零状态写盘")
        loaded = self.load()
        self.assertEqual(loaded["status"], "waiting_quota")
        self.assertEqual(loaded["quota_resume"]["resume_count"], 0)
        self.assertEqual(self.names().count("quota_resume_confirmed"), 0)
        # 武装后端到端闭环仍通：bind → 决策 → 确认 → 计数恰 +1
        task.bind_quota_automation(self.repo, self.TID, self.AUTO_ID)
        self.assertEqual(
            self.decide(quota_view("AVAILABLE"))["action"],
            "resume-authorized")
        st = self.confirm()
        self.assertEqual(st["quota_resume"]["resume_count"], 1)

    # —— 状态词汇补充（bind 状态限三非终态） ——

    def test_bind_status_restriction(self):
        """绑定限 active / waiting_quota / waiting_user：blocked 与终态拒绝。"""
        self.make_task()
        task.set_status(self.repo, self.TID, "blocked")
        with self.assertRaises(ValueError):
            task.bind_quota_automation(self.repo, self.TID, self.AUTO_ID)
        self.assertIsNone(self.load()["quota_resume"]["automation_id"])
        task.set_status(self.repo, self.TID, "active")
        task.set_status(self.repo, self.TID, "waiting_user")
        st = task.bind_quota_automation(self.repo, self.TID, self.AUTO_ID)
        self.assertEqual(st["quota_resume"]["automation_id"], self.AUTO_ID)
        task.set_status(self.repo, self.TID, "active")
        task.complete(self.repo, self.TID)
        with self.assertRaises(ValueError):
            task.bind_quota_automation(self.repo, self.TID, "late-bind")


# —— 4. state 层校验收口（mode 词汇 / 非负整数 / 预算不变量） ——

def base_state(repo_root):
    """构造合法全字段 v2.4 state（quota_resume 为默认块）。"""
    return state.new_task_state(
        "quota-valid-task", "验证 quota_resume 校验收口", {"mode": "solo"},
        repository_root=repo_root)


class TestQuotaResumeValidation(unittest.TestCase):

    def _state_with_block(self, block):
        st = base_state(tempfile.gettempdir())
        st["quota_resume"] = block
        return st

    def test_manual_and_auto_both_valid(self):
        """mode 恰为 manual / auto 两值合法（auto 不需要附加字段）。"""
        self.assertEqual(
            state.validate_state(self._state_with_block(
                {"mode": "manual", "max_resumes": 0, "resume_count": 0,
                 "automation_id": None})), [])
        self.assertEqual(
            state.validate_state(self._state_with_block(
                {"mode": "auto", "max_resumes": 5, "resume_count": 2,
                 "automation_id": None})), [])

    def test_bad_mode_refused(self):
        """v2.3 四模式旧词与垃圾值 → 拒绝（auto 大小写敏感）。"""
        for bad in ("notify", "auto_once", "until_done", "AUTO", "", None):
            st = self._state_with_block(
                {"mode": bad, "max_resumes": 0, "resume_count": 0,
                 "automation_id": None})
            errors = state.validate_state(st)
            self.assertTrue(
                any(item.startswith("quota_resume.mode") for item in errors),
                "mode=%r 应拒绝" % (bad,))

    def test_negative_and_bool_counts_refused(self):
        """负数 / bool 计数 → 拒绝（bool 是 int 子类，不得充当计数）。"""
        for key in ("max_resumes", "resume_count"):
            for bad in (-1, True, False, "3", 1.5):
                block = {"mode": "auto", "max_resumes": 0,
                         "resume_count": 0, "automation_id": None}
                block[key] = bad
                errors = state.validate_state(self._state_with_block(block))
                self.assertTrue(
                    any(item.startswith("quota_resume.%s" % key)
                        for item in errors),
                    "%s=%r 应拒绝" % (key, bad))

    def test_count_over_max_refused(self):
        """预算不变量：resume_count > max_resumes → 校验拒绝。"""
        block = {"mode": "auto", "max_resumes": 2, "resume_count": 3,
                 "automation_id": None}
        errors = state.validate_state(self._state_with_block(block))
        self.assertTrue(
            any(item.startswith("quota_resume.resume_count")
                and "max_resumes" in item for item in errors),
            errors)
        # 临界相等合法（resume_count == max_resumes）
        block["resume_count"] = 2
        self.assertEqual(
            state.validate_state(self._state_with_block(block)), [])

    def test_save_state_enforces_budget_invariant(self):
        """save_state 同闸：count > max 的状态拒绝落盘。"""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        st = base_state(tmp.name)
        st["quota_resume"] = {"mode": "auto", "max_resumes": 1,
                              "resume_count": 2, "automation_id": None}
        with self.assertRaises(ValueError) as ctx:
            state.save_state(tmp.name, st)
        self.assertIn("resume_count", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
