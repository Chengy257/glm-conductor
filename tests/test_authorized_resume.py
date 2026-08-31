#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""runtime 授权续跑测试（v2.1 §14/§22.7，wu-21-11 Authorized Quota Resume）。

覆盖（实施规格测试清单 9 项 + RB-21-01 恢复对账增补）：
  1. handle_quota_exhausted happy：executing 任务 + ready/running/
     waiting_quota 单元 + evaluation 带 reset_at → 单元与任务转
     waiting_quota、dispatch.active 清空、manifest 刷新、journal
     quota_waiting、recommended_resume_at = 最晚 EXHAUSTED 窗 reset +
     300 秒（RB-21-01 起 scheduler.plan_resume 统一口径）、返回键冻结；
     中断来源 runtime.quota_interrupted_from 写入（ready/running）且
     已是 waiting_quota 的单元不覆盖既有标记；
  2. 四态授权矩阵：manual / notify → wake.required=False（notify 仍
     返回提醒 prompt）；auto_once + user 授权 + 预算 1 → required=
     True 且 prompt 非空含 task_id 与红线；until_done 同；auto_once
     无 user 授权 → required=False（授权矩阵纯决策纵深防御分支——
     该组合被 execution_policy §5.4 不变量挡在合法 state 之外）；
  3. 预算耗尽：consumed >= max → budget_exhausted=True + until_done
     任务转 waiting_user + journal auto_resume_authorization_exhausted；
  4. record_quota_wake：consumed 递增、journal quota_wake_recorded、
     与 automation 存活解耦（无回滚 API，二次调用纯递增）；
  5. quota_wake_prompt：含 task_id / 仓库根 / resume_from_quota /
     RB-1 指纹 / 预算状态 / 红线（CronDelete 不重试）与 maxRuns=1
     一次性语义；quota-resolve 指令带 --force-refresh；
  6. resume_from_quota：monkeypatch resolver（force_refresh=True 强制
     刷新）→ 任务回 executing、单元按中断来源恢复、journal
     quota_resumed（source 透传 + unit_recovery 增量）；EXHAUSTED →
     零转态 resumed=False 且经强刷缓存 snapshot 按 plan_resume 口径给
     建议恢复时刻（最晚 reset + 宽限）；UNKNOWN → 零转态（保守）；
     显式 status 直通零解析（resolver 不被调用）；
  7. waiting_user 状态机：waiting_user→executing 合法（用户重新授权
     后）；waiting_user→completed 非法（须经完成门）；
  8. execution_policy：consumed_quota_windows 缺省 0（缺键合法）、
     负数 / bool / 非整数校验拒绝、容错读与 state 级错误路径前缀；
  9. recovery 渲染：waiting_quota 任务出现在 recovery summary 且含
     recommended_resume_at 与剩余窗口预算，渲染含 resume_from_quota
     指引；非等待态任务 quota_wait 为 None（八字段冻结形状不变）。

RB-21-01 恢复对账测试（§10.1 命名）：wake 强刷绕过新鲜缓存、双窗
EXHAUSTED 取最晚 reset、auto_once 不在首个局部 reset 唤醒、running
中断单元禁止盲目 ready、reuse_result 不重派（直达 verifying）、
resume_with_progress 需进度包续作、manual_ruling 不派发、对账异常
fail-closed、中断来源生命周期（写入 / 不覆盖 / 恢复清除 / legacy
缺字段合法）。

fixture：tempfile 仓库 + runtime.state 构造（scratch 任务目录，不碰
真实账本）；全部离线（resolver 路径 monkeypatch，零网络）。仅
Python 3 标准库（unittest + tempfile + mock），零第三方依赖。

运行：
    cd <repo_root> && python3 -m unittest tests.test_authorized_resume -v
"""

import sys, unittest
import tempfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "glm-conductor"))
from runtime import (execution_policy, journal, reconcile as reconcile_mod,
                     recovery, resume_manifest, state, task_manager)
from runtime.quota import resolver as quota_resolver

TID = "authorized-resume-1a2b3c"
ROUTE = {"mode": "delegate", "delegability": "high",
         "assurance": "standard", "executor": "flash-implementer",
         "continuity": "resumable"}
CONFIRMED_AT = "2026-08-31T00:00:00+00:00"
VERIFY_CMD = "python3 -m unittest tests.test_authorized_resume"


def make_unit(uid, status="ready"):
    """构造一个通过 §61 契约校验的最小 work unit dict。"""
    return {"id": uid, "objective": "fixture unit %s" % uid,
            "status": status, "depends_on": [],
            "executor": "flash-implementer",
            "ownership": ["src/%s.py" % uid],
            "verification": [VERIFY_CMD]}


def make_interrupted_unit(uid, origin):
    """构造带额度中断来源标记的 waiting_quota 单元（RB-21-01）。"""
    unit = make_unit(uid, "waiting_quota")
    unit["runtime"] = {"quota_interrupted_from": origin}
    return unit


def authorize(st, auto_resume, max_quota_windows, source="user",
              confirmed_at=CONFIRMED_AT):
    """以 set_resume_authorization 写入续跑授权（返回全新 policy dict）。"""
    return execution_policy.set_resume_authorization(
        st["execution_policy"], auto_resume=auto_resume,
        max_quota_windows=max_quota_windows, source=source,
        confirmed_at=confirmed_at)


def save_task(repo, *, status="executing", units=(), policy=None,
              task_id=TID):
    """在临时仓库构造任务（构造默认块后按需整体替换 execution_policy）。"""
    st = state.new_task_state(
        task_id, "authorized resume fixture", dict(ROUTE),
        ownership_files=["src"], verification_required=[VERIFY_CMD],
        status=status)
    st["work_units"] = list(units)
    if policy is not None:
        st["execution_policy"] = policy
    state.save_state(repo, st)
    return st


EXHAUSTED_EVALUATION = {
    "status": "EXHAUSTED",
    "windows": [
        {"kind": "weekly", "status": "EXHAUSTED",
         "used_percent": 100.0, "remaining_percent": 0.0,
         "reset_at": "2026-08-31T13:00:00Z"},
        {"kind": "five_hour", "status": "EXHAUSTED",
         "used_percent": 100.0, "remaining_percent": 0.0,
         "reset_at": "2026-08-31T12:00:00Z"},
    ],
}


class HandleQuotaExhaustedTest(unittest.TestCase):
    """清单 1：EXHAUSTED 转态链 happy path 与入口约束。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_happy_units_task_manifest_journal_and_frozen_keys(self):
        policy = authorize(save_task(
            self.repo,
            units=[make_unit("wu-a", "ready"), make_unit("wu-b", "running"),
                   make_unit("wu-c", "waiting_quota")],
            policy=None), "until_done", 3)
        st = state.load_state(self.repo, TID)
        st["execution_policy"] = policy
        st["dispatch"]["active"] = ["wu-b"]
        state.save_state(self.repo, st)

        result = task_manager.handle_quota_exhausted(
            self.repo, TID, evaluation=EXHAUSTED_EVALUATION)

        # 返回键冻结（含 wake 子 dict 三键）
        self.assertEqual(sorted(result.keys()), [
            "auto_resume", "reason", "recommended_resume_at",
            "remaining_quota_windows", "task_status", "waiting_units",
            "wake"])
        self.assertEqual(sorted(result["wake"].keys()),
                         ["budget_exhausted", "prompt", "required"])
        self.assertEqual(result["task_status"], "waiting_quota")
        self.assertEqual(result["waiting_units"], ["wu-a", "wu-b", "wu-c"])
        # 最晚 EXHAUSTED 窗 reset（weekly 13:00Z）+ 300 秒宽限
        # （RB-21-01：scheduler.plan_resume 统一口径，多窗取 max 不取 min）
        self.assertEqual(result["recommended_resume_at"],
                         "2026-08-31T13:05:00Z")
        self.assertEqual(result["auto_resume"], "until_done")
        self.assertEqual(result["remaining_quota_windows"], 3)
        self.assertTrue(result["wake"]["required"])
        self.assertFalse(result["wake"]["budget_exhausted"])
        self.assertTrue(result["wake"]["prompt"])

        # 盘上：任务 waiting_quota、三单元全 waiting_quota、active 清空
        on_disk = state.load_state(self.repo, TID)
        self.assertEqual(on_disk["status"], "waiting_quota")
        self.assertEqual([(u["id"], u["status"]) for u in
                          on_disk["work_units"]],
                         [("wu-a", "waiting_quota"),
                          ("wu-b", "waiting_quota"),
                          ("wu-c", "waiting_quota")])
        self.assertEqual(on_disk["dispatch"]["active"], [])
        # 中断来源标记（RB-21-01）：ready/running 首次转换时写入转换前
        # 状态；wu-c 已是 waiting_quota——原地保持，无 runtime 键
        self.assertEqual(on_disk["work_units"][0]["runtime"],
                         {"quota_interrupted_from": "ready"})
        self.assertEqual(on_disk["work_units"][1]["runtime"],
                         {"quota_interrupted_from": "running"})
        self.assertNotIn("runtime", on_disk["work_units"][2])

        # journal quota_waiting（units + recommended_resume_at）
        events = [e for e in journal.read_events(self.repo, TID)
                  if e.get("event") == "quota_waiting"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["units"], ["wu-a", "wu-b", "wu-c"])
        self.assertEqual(events[0]["recommended_resume_at"],
                         "2026-08-31T13:05:00Z")

        # manifest 刷新且反映最终任务状态
        manifest = resume_manifest.read_resume_manifest(self.repo, TID)
        self.assertIsNotNone(manifest)
        self.assertEqual(manifest["task_status"], "waiting_quota")

    def test_evaluation_none_recommends_none(self):
        # evaluation=None（reset 未知）→ None 不虚构（§31）
        save_task(self.repo, units=[make_unit("wu-a")])
        result = task_manager.handle_quota_exhausted(self.repo, TID)
        self.assertIsNone(result["recommended_resume_at"])
        # manual（默认块）→ wake.required=False，reason 说明 SessionStart
        self.assertFalse(result["wake"]["required"])
        self.assertIsNone(result["wake"]["prompt"])
        self.assertIn("SessionStart", result["reason"])

    def test_preexisting_waiting_quota_origin_not_overwritten(self):
        # 已是 waiting_quota 的单元原地保持：既有中断来源标记不覆盖
        # （首次转换才写；同时验证 runtime dict 既有内容原样保留）
        pre = make_unit("wu-c", "waiting_quota")
        pre["runtime"] = {"quota_interrupted_from": "running",
                          "note": "keep me"}
        save_task(self.repo, units=[make_unit("wu-a", "ready"), pre])
        task_manager.handle_quota_exhausted(self.repo, TID)
        on_disk = state.load_state(self.repo, TID)
        self.assertEqual(on_disk["work_units"][0]["runtime"],
                         {"quota_interrupted_from": "ready"})
        self.assertEqual(on_disk["work_units"][1]["runtime"],
                         {"quota_interrupted_from": "running",
                          "note": "keep me"})

    def test_joining_entry_two_step_transition(self):
        # joining 入口：先经表内边回 executing，再转 waiting_quota
        save_task(self.repo, status="joining", units=[make_unit("wu-a")])
        result = task_manager.handle_quota_exhausted(self.repo, TID)
        self.assertEqual(result["task_status"], "waiting_quota")
        self.assertEqual(state.load_state(self.repo, TID)["status"],
                         "waiting_quota")

    def test_non_executing_family_rejected(self):
        # 前置态（created）不存在「额度耗尽挂起」语义 → TaskManagerError
        save_task(self.repo, status="created", units=[make_unit("wu-a")])
        with self.assertRaises(task_manager.TaskManagerError) as ctx:
            task_manager.handle_quota_exhausted(self.repo, TID)
        self.assertIn("created", str(ctx.exception))
        # 盘上状态不动
        self.assertEqual(state.load_state(self.repo, TID)["status"],
                         "created")

    def test_missing_task_rejected(self):
        with self.assertRaises(task_manager.TaskManagerError):
            task_manager.handle_quota_exhausted(self.repo, TID)


class WakeAuthorizationMatrixTest(unittest.TestCase):
    """清单 2：manual / notify / auto_once / until_done 四态授权矩阵。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def _run(self, auto_resume, max_windows, source="user"):
        policy = authorize(save_task(self.repo, units=[make_unit("wu-a")]),
                           auto_resume, max_windows, source=source)
        st = state.load_state(self.repo, TID)
        st["execution_policy"] = policy
        state.save_state(self.repo, st)
        return task_manager.handle_quota_exhausted(
            self.repo, TID, evaluation=EXHAUSTED_EVALUATION)

    def test_manual_never_requires_wake(self):
        result = self._run("manual", 0)
        self.assertFalse(result["wake"]["required"])
        self.assertIsNone(result["wake"]["prompt"])
        self.assertFalse(result["wake"]["budget_exhausted"])
        self.assertIn("SessionStart", result["reason"])

    def test_notify_reminds_but_never_requires_wake(self):
        # notify：required 语义是「授权允许自动恢复执行」——notify 不允许；
        # prompt 仍返回（提醒模板）
        result = self._run("notify", 0)
        self.assertFalse(result["wake"]["required"])
        self.assertTrue(result["wake"]["prompt"])
        self.assertIn("maxRuns=1", result["wake"]["prompt"])
        self.assertEqual(result["task_status"], "waiting_quota")

    def test_auto_once_authorized_with_budget_requires_wake(self):
        result = self._run("auto_once", 1)
        self.assertTrue(result["wake"]["required"])
        prompt = result["wake"]["prompt"]
        self.assertTrue(prompt)
        self.assertIn(TID, prompt)          # 自足：task_id 内置
        self.assertIn("CronDelete", prompt)  # 红线内置
        self.assertEqual(result["remaining_quota_windows"], 1)
        self.assertFalse(result["wake"]["budget_exhausted"])

    def test_until_done_authorized_with_budget_requires_wake(self):
        result = self._run("until_done", 3)
        self.assertTrue(result["wake"]["required"])
        self.assertIn(TID, result["wake"]["prompt"])
        self.assertEqual(result["remaining_quota_windows"], 3)

    def test_auto_once_without_user_source_never_requires_wake(self):
        # 纵深防御分支：auto_once + source=default 被 execution_policy
        # §5.4 不变量挡在合法 state 之外，这里直接测授权矩阵纯决策——
        # 即使手写 state 绕过了闸，runtime 也绝不产出 wake 指令
        decision = task_manager._quota_wake_decision({
            "auto_resume": "auto_once", "source": "default",
            "max_quota_windows": 1, "consumed_quota_windows": 0,
            "remaining": 1})
        self.assertFalse(decision["required"])
        self.assertIsNone(decision["prompt_mode"])
        self.assertFalse(decision["transition_waiting_user"])
        self.assertIn("user", decision["reason"])
        # until_done 同款
        decision = task_manager._quota_wake_decision({
            "auto_resume": "until_done", "source": "default",
            "max_quota_windows": 2, "consumed_quota_windows": 0,
            "remaining": 2})
        self.assertFalse(decision["required"])

    def test_continuity_view_legacy_defaults_to_manual(self):
        # legacy（无 execution_policy 块）→ manual / default / 0 / 0
        st = save_task(self.repo, units=[make_unit("wu-a")])
        del st["execution_policy"]
        state.save_state(self.repo, st)
        reloaded = state.load_state(self.repo, TID)
        view = task_manager._continuity_view(reloaded)
        self.assertEqual(view["auto_resume"], "manual")
        self.assertEqual(view["source"], "default")
        self.assertEqual(view["max_quota_windows"], 0)
        self.assertEqual(view["consumed_quota_windows"], 0)
        self.assertEqual(view["remaining"], 0)


class BudgetExhaustedTest(unittest.TestCase):
    """清单 3：consumed >= max → waiting_user + 授权耗尽事件。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_exhausted_until_done_transitions_to_waiting_user(self):
        policy = authorize(save_task(self.repo, units=[make_unit("wu-a")]),
                           "until_done", 1)
        st = state.load_state(self.repo, TID)
        st["execution_policy"] = policy
        state.save_state(self.repo, st)
        # 先消耗掉唯一的窗口预算（CronCreate 成功后记账）
        task_manager.record_quota_wake(self.repo, TID,
                                       automation_id="cron-once",
                                       fires_at="2026-08-31T12:05:00Z")

        result = task_manager.handle_quota_exhausted(
            self.repo, TID, evaluation=EXHAUSTED_EVALUATION)

        self.assertEqual(result["task_status"], "waiting_user")
        self.assertTrue(result["wake"]["budget_exhausted"])
        self.assertFalse(result["wake"]["required"])
        self.assertIsNone(result["wake"]["prompt"])
        self.assertEqual(result["remaining_quota_windows"], 0)
        self.assertEqual(state.load_state(self.repo, TID)["status"],
                         "waiting_user")
        events = [e for e in journal.read_events(self.repo, TID)
                  if e.get("event") == "auto_resume_authorization_exhausted"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["auto_resume"], "until_done")
        self.assertEqual(events[0]["consumed_quota_windows"], 1)
        self.assertEqual(events[0]["max_quota_windows"], 1)
        # 单元转态不受影响（waiting_quota 照常），manifest 反映最终态
        self.assertEqual(state.load_state(self.repo, TID)["work_units"][0]
                         ["status"], "waiting_quota")
        self.assertEqual(
            resume_manifest.read_resume_manifest(self.repo, TID)
            ["task_status"], "waiting_user")


class RecordQuotaWakeTest(unittest.TestCase):
    """清单 4：window 扣减记账——递增、事件、与 automation 存活解耦。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def _waiting_task(self):
        policy = authorize(save_task(self.repo, units=[make_unit("wu-a")]),
                           "until_done", 2)
        st = state.load_state(self.repo, TID)
        st["execution_policy"] = policy
        st["status"] = "waiting_quota"
        state.save_state(self.repo, st)

    def test_increments_journals_and_never_rolls_back(self):
        self._waiting_task()
        first = task_manager.record_quota_wake(
            self.repo, TID, automation_id="cron-1",
            fires_at="2026-08-31T12:05:00Z")
        self.assertEqual(first["consumed_quota_windows"], 1)
        self.assertEqual(first["remaining_quota_windows"], 1)
        self.assertEqual(first["max_quota_windows"], 2)
        self.assertEqual(
            state.load_state(self.repo, TID)["execution_policy"]
            ["continuity"]["consumed_quota_windows"], 1)
        events = [e for e in journal.read_events(self.repo, TID)
                  if e.get("event") == "quota_wake_recorded"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["automation_id"], "cron-1")
        self.assertEqual(events[0]["fires_at"], "2026-08-31T12:05:00Z")
        self.assertEqual(events[0]["consumed"], 1)
        self.assertEqual(events[0]["remaining"], 1)

        # 二次调用纯递增（无回滚 API——wake 没触发也不回滚，与
        # automation 存活解耦）
        second = task_manager.record_quota_wake(
            self.repo, TID, automation_id="cron-2",
            fires_at="2026-08-31T17:05:00Z")
        self.assertEqual(second["consumed_quota_windows"], 2)
        self.assertEqual(second["remaining_quota_windows"], 0)
        self.assertEqual(
            state.load_state(self.repo, TID)["execution_policy"]
            ["continuity"]["consumed_quota_windows"], 2)
        events = [e for e in journal.read_events(self.repo, TID)
                  if e.get("event") == "quota_wake_recorded"]
        self.assertEqual(len(events), 2)

    def test_blank_automation_id_or_fires_at_rejected(self):
        self._waiting_task()
        for kwargs in ({"automation_id": "", "fires_at": "x"},
                       {"automation_id": "cron-1", "fires_at": ""},
                       {"automation_id": None, "fires_at": "x"}):
            with self.assertRaises(ValueError):
                task_manager.record_quota_wake(self.repo, TID, **kwargs)
        # 失败零副作用：无事件、无记账
        events = [e for e in journal.read_events(self.repo, TID)
                  if e.get("event") == "quota_wake_recorded"]
        self.assertEqual(events, [])
        self.assertNotIn("consumed_quota_windows",
                         state.load_state(self.repo, TID)
                         ["execution_policy"]["continuity"])


class WakePromptTest(unittest.TestCase):
    """清单 5：自足 wake prompt 的内容契约。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_prompt_contains_task_repo_resume_budget_and_redlines(self):
        policy = authorize(save_task(
            self.repo,
            units=[make_unit("wu-a", "waiting_quota"),
                   make_unit("wu-b", "waiting_quota")],
            policy=None), "until_done", 2)
        st = state.load_state(self.repo, TID)
        st["execution_policy"] = policy
        st["status"] = "waiting_quota"
        state.save_state(self.repo, st)
        task_manager.record_quota_wake(self.repo, TID,
                                       automation_id="cron-1",
                                       fires_at="2026-08-31T12:05:00Z")

        prompt = task_manager.quota_wake_prompt(self.repo, TID)

        self.assertIn(TID, prompt)                       # task_id
        self.assertIn(self.repo, prompt)                  # 账本根（仓库根）
        self.assertIn("resume_from_quota", prompt)        # 恢复首步
        # RB-21-01：quota-resolve 指令带 --force-refresh（文本与行为一致）
        self.assertIn("quota-resolve --force-refresh", prompt)
        self.assertIn("RB-1", prompt)                     # 指纹口径
        self.assertIn("record_unit_verification", prompt)
        self.assertIn("已消耗 1 / 共 2 窗", prompt)        # 预算状态
        self.assertIn("剩余 1 窗", prompt)
        self.assertIn("CronDelete", prompt)               # 红线：不重试
        self.assertIn("CronUpdate", prompt)
        self.assertIn("maxRuns=1", prompt)                # 一次性语义
        self.assertIn("wu-a", prompt)                     # 等待单元清单

    def test_prompt_missing_task_rejected(self):
        with self.assertRaises(task_manager.TaskManagerError):
            task_manager.quota_wake_prompt(self.repo, TID)


class ResumeFromQuotaTest(unittest.TestCase):
    """清单 6：恢复入口——resolver 缺省解析与显式 status 直通。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = self._tmp.name
        policy = authorize(save_task(
            self.repo, units=[make_unit("wu-a", "waiting_quota")],
            policy=None), "until_done", 2)
        st = state.load_state(self.repo, TID)
        st["execution_policy"] = policy
        st["status"] = "waiting_quota"
        state.save_state(self.repo, st)

    def tearDown(self):
        self._tmp.cleanup()

    def _resolved(self, status, source="cache_fresh"):
        return {"status": status, "source": source,
                "evaluated_at": "2026-08-31T12:00:00.000Z",
                "reason": "fixture"}

    def test_available_via_resolver_resumes(self):
        with mock.patch.object(
                quota_resolver, "resolve_quota_status",
                return_value=self._resolved("AVAILABLE")) as resolver_mock:
            result = task_manager.resume_from_quota(self.repo, TID)
        # RB-21-01：唤醒后强制刷新（force_refresh=True，绕过新鲜缓存）
        resolver_mock.assert_called_once_with(self.repo, force_refresh=True)
        self.assertEqual(sorted(result.keys()),
                         ["recommended_resume_at", "resumed", "status",
                          "unit_recovery", "wake_budget_remaining"])
        self.assertTrue(result["resumed"])
        self.assertEqual(result["status"], "AVAILABLE")
        self.assertIsNone(result["recommended_resume_at"])  # 不虚构
        self.assertEqual(result["unit_recovery"], {})  # legacy 直回 ready
        on_disk = state.load_state(self.repo, TID)
        self.assertEqual(on_disk["status"], "executing")
        self.assertEqual(on_disk["work_units"][0]["status"], "ready")
        events = [e for e in journal.read_events(self.repo, TID)
                  if e.get("event") == "quota_resumed"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["status"], "AVAILABLE")
        self.assertEqual(events[0]["source"], "cache_fresh")
        self.assertEqual(events[0]["unit_recovery"], {})

    def test_exhausted_zero_transitions(self):
        with mock.patch.object(
                quota_resolver, "resolve_quota_status",
                return_value=self._resolved("EXHAUSTED", "provider")):
            result = task_manager.resume_from_quota(self.repo, TID)
        self.assertFalse(result["resumed"])
        self.assertEqual(result["status"], "EXHAUSTED")
        on_disk = state.load_state(self.repo, TID)
        self.assertEqual(on_disk["status"], "waiting_quota")
        self.assertEqual(on_disk["work_units"][0]["status"],
                         "waiting_quota")
        self.assertNotIn("quota_resumed",
                         [e.get("event")
                          for e in journal.read_events(self.repo, TID)])

    def test_unknown_conservative_wait_zero_transitions(self):
        with mock.patch.object(
                quota_resolver, "resolve_quota_status",
                return_value=self._resolved("UNKNOWN", "none")):
            result = task_manager.resume_from_quota(self.repo, TID)
        self.assertFalse(result["resumed"])
        self.assertEqual(state.load_state(self.repo, TID)["status"],
                         "waiting_quota")

    def test_explicit_status_skips_resolver(self):
        # 显式 status 直通：零解析（resolver 不被调用）、source 记 explicit
        with mock.patch.object(quota_resolver, "resolve_quota_status") \
                as resolver_mock:
            result = task_manager.resume_from_quota(self.repo, TID,
                                                    status="PRESSURE")
        resolver_mock.assert_not_called()
        self.assertTrue(result["resumed"])
        events = [e for e in journal.read_events(self.repo, TID)
                  if e.get("event") == "quota_resumed"]
        self.assertEqual(events[0]["source"], "explicit")

    def test_pressure_resumes_from_waiting_user(self):
        # waiting_user（授权耗尽）→ 用户重新授权场景：PRESSURE 也恢复
        st = state.load_state(self.repo, TID)
        st["status"] = "waiting_user"
        state.save_state(self.repo, st)
        result = task_manager.resume_from_quota(self.repo, TID,
                                                status="PRESSURE")
        self.assertTrue(result["resumed"])
        self.assertEqual(state.load_state(self.repo, TID)["status"],
                         "executing")

    def test_already_resumed_is_idempotent_zero_transitions(self):
        # 重复唤醒（任务已 executing）：零转态幂等 resumed=False
        state.transition_task_status(self.repo, TID, "executing")
        result = task_manager.resume_from_quota(self.repo, TID,
                                                status="AVAILABLE")
        self.assertFalse(result["resumed"])
        self.assertEqual(state.load_state(self.repo, TID)["status"],
                         "executing")


class WaitingUserStateMachineTest(unittest.TestCase):
    """清单 7：waiting_user 词汇与转换边。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def _waiting_user_task(self):
        save_task(self.repo, status="waiting_quota",
                  units=[make_unit("wu-a", "waiting_quota")])
        state.transition_task_status(self.repo, TID, "waiting_user")

    def test_vocabulary_and_edges(self):
        self.assertIn("waiting_user", state.TASK_STATUSES)
        self.assertNotIn("waiting_user", state.TERMINAL_STATUSES)
        self.assertIn("waiting_user", state.TASK_TRANSITIONS["waiting_quota"])
        self.assertEqual(
            state.TASK_TRANSITIONS["waiting_user"],
            ("executing", "blocked", "failed", "cancelled"))

    def test_waiting_user_to_executing_legal(self):
        self._waiting_user_task()
        migrated = state.transition_task_status(self.repo, TID, "executing")
        self.assertEqual(migrated["status"], "executing")
        self.assertEqual(state.load_state(self.repo, TID)["status"],
                         "executing")

    def test_waiting_user_to_completed_illegal(self):
        # waiting_user → completed 非法：必须先回 executing 走完成门
        # （完成门闸优先拦下 completed 的任何公共写入，消息指向
        # finalizing；转换表层面 waiting_user 亦无 completed 入边）
        self._waiting_user_task()
        st = state.load_state(self.repo, TID)
        st["status"] = "completed"
        with self.assertRaises(ValueError) as ctx:
            state.save_state(self.repo, st)
        self.assertIn("completed", str(ctx.exception))
        self.assertIn("finalizing", str(ctx.exception))
        self.assertEqual(state.load_state(self.repo, TID)["status"],
                         "waiting_user")
        # 转换表层面对照：waiting_user 的合法目标不含 completed
        self.assertNotIn(
            "completed", state.TASK_TRANSITIONS["waiting_user"])


class ConsumedQuotaWindowsPolicyTest(unittest.TestCase):
    """清单 8：execution_policy.consumed_quota_windows 可选键契约。"""

    _ABSENT = object()

    def _policy_with_consumed(self, value):
        policy = execution_policy.default_execution_policy()
        if value is not self._ABSENT:
            policy["continuity"]["consumed_quota_windows"] = value
        return policy

    def test_absent_key_valid_and_defaults_to_zero(self):
        # 默认块不含该键：validate 容错缺省 0，冻结 schema 不变
        self.assertNotIn("consumed_quota_windows",
                         execution_policy.default_execution_policy()
                         ["continuity"])
        self.assertEqual(
            execution_policy.validate_execution_policy(
                execution_policy.default_execution_policy()), [])
        self.assertEqual(
            execution_policy.consumed_quota_windows(
                execution_policy.default_execution_policy()), 0)

    def test_zero_and_positive_valid(self):
        for value in (0, 3):
            self.assertEqual(
                execution_policy.validate_execution_policy(
                    self._policy_with_consumed(value)), [])
        self.assertEqual(
            execution_policy.consumed_quota_windows(
                self._policy_with_consumed(3)), 3)

    def test_negative_bool_non_int_rejected(self):
        for bad in (-1, True, False, "2", 2.5, None):
            errors = execution_policy.validate_execution_policy(
                self._policy_with_consumed(bad))
            self.assertTrue(
                any(e.startswith("continuity.consumed_quota_windows")
                    for e in errors), (bad, errors))
        # 键缺失（哨兵不写入）完全合法
        self.assertEqual(
            execution_policy.validate_execution_policy(
                self._policy_with_consumed(self._ABSENT)), [])

    def test_tolerant_reader_on_broken_shapes(self):
        for broken in (None, {}, {"continuity": None},
                       {"continuity": {"consumed_quota_windows": -1}},
                       {"continuity": {"consumed_quota_windows": True}}):
            self.assertEqual(
                execution_policy.consumed_quota_windows(broken), 0, broken)

    def test_state_level_error_path_prefix(self):
        st = state.new_task_state(
            TID, "policy fixture", dict(ROUTE),
            ownership_files=["src"], verification_required=[VERIFY_CMD])
        st["execution_policy"]["continuity"]["consumed_quota_windows"] = -1
        errors = state.validate_state(st)
        self.assertTrue(
            any(e.startswith(
                "execution_policy.continuity.consumed_quota_windows")
                for e in errors), errors)


class RecoveryQuotaWaitTest(unittest.TestCase):
    """清单 9：recovery 对 waiting_quota / waiting_user 任务的渲染增补。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def _waiting_task_with_event(self):
        policy = authorize(save_task(
            self.repo, status="waiting_quota",
            units=[make_unit("wu-a", "waiting_quota")],
            policy=None), "until_done", 2)
        st = state.load_state(self.repo, TID)
        st["execution_policy"] = policy
        state.save_state(self.repo, st)
        journal.append_event(self.repo, TID, {
            "event": "quota_waiting", "units": ["wu-a"],
            "recommended_resume_at": "2026-08-31T12:05:00Z"})

    def test_waiting_task_summary_carries_quota_wait(self):
        self._waiting_task_with_event()
        summary = recovery.build_recovery_summary(self.repo)
        self.assertEqual(len(summary["tasks"]), 1)
        entry = summary["tasks"][0]
        self.assertEqual(entry["status"], "waiting_quota")
        self.assertEqual(entry["quota_wait"], {
            "recommended_resume_at": "2026-08-31T12:05:00Z",
            "remaining_windows": 2,  # max 2 - consumed 0（未记账）
        })

    def test_waiting_task_render_includes_resume_guidance(self):
        self._waiting_task_with_event()
        text = recovery.render_resume_context(
            recovery.build_recovery_summary(self.repo))
        self.assertIn("recommended_resume_at=2026-08-31T12:05:00Z", text)
        self.assertIn("remaining wake budget: 2 window(s)", text)
        self.assertIn("resume_from_quota", text)

    def test_non_waiting_task_quota_wait_omitted_shape_frozen(self):
        # 非等待态任务：quota_wait 整键省略，既有八字段冻结形状逐字不变
        save_task(self.repo, status="executing",
                  units=[make_unit("wu-a", "running")])
        entry = recovery.build_recovery_summary(self.repo)["tasks"][0]
        self.assertNotIn("quota_wait", entry)
        self.assertEqual(sorted(entry.keys()), [
            "auto_resume", "completed_units", "goal", "incomplete_units",
            "interrupted_units", "repo_root", "status",
            "task_id"])
        text = recovery.render_resume_context(
            recovery.build_recovery_summary(self.repo))
        self.assertNotIn("Quota wait:", text)

    def test_missing_event_recommends_none_without_fabrication(self):
        # 无 quota_waiting 事件 → None（不虚构），渲染为 unknown
        save_task(self.repo, status="waiting_quota",
                  units=[make_unit("wu-a", "waiting_quota")])
        entry = recovery.build_recovery_summary(self.repo)["tasks"][0]
        self.assertEqual(entry["quota_wait"], {
            "recommended_resume_at": None,
            "remaining_windows": 0,  # 默认块 manual/0
        })
        text = recovery.render_resume_context(
            recovery.build_recovery_summary(self.repo))
        self.assertIn("recommended_resume_at=unknown", text)


class WakeResumePlanningTest(unittest.TestCase):
    """RB-21-01 §10.1 A/B：wake 强制刷新与 scheduler 统一恢复规划。

    resume_from_quota 的 status=None 分支以 force_refresh=True 强刷；
    EXHAUSTED 时从强刷后缓存 snapshot 读回 evaluation，交
    scheduler.plan_resume 统一口径（max(reset)+grace）——本模块历史
    的「最早窗」min 第二套算法已删除。
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def _waiting_task(self, auto_resume="until_done", max_windows=3):
        policy = authorize(save_task(
            self.repo, status="waiting_quota",
            units=[make_unit("wu-a", "waiting_quota")],
            policy=None), auto_resume, max_windows)
        st = state.load_state(self.repo, TID)
        st["execution_policy"] = policy
        state.save_state(self.repo, st)

    def _write_exhausted_cache(self):
        """模拟 wake 强刷后 provider 落盘的缓存（完整 snapshot 形状，
        resolver.py 缓存原语原样复用——测试不另写第二套缓存格式）。"""
        quota_resolver._save_cache(
            quota_resolver._cache_path(self.repo), {
                "provider": "fixture",
                "fetched_at": "2026-08-31T12:00:00.000Z",
                "status": "EXHAUSTED",
                "snapshot": {"windows": [
                    {"kind": "five_hour", "used_percent": 100.0,
                     "remaining_percent": 0.0,
                     "reset_at": "2026-08-31T12:00:00Z"},
                    {"kind": "weekly", "used_percent": 100.0,
                     "remaining_percent": 0.0,
                     "reset_at": "2026-08-31T13:00:00Z"},
                ]}})

    @staticmethod
    def _resolved_exhausted():
        return {"status": "EXHAUSTED", "source": "provider",
                "evaluated_at": "2026-08-31T12:00:00.000Z",
                "reason": "fixture"}

    def test_wake_force_refresh_bypasses_fresh_cache(self):
        # 预置一份「新鲜」缓存：resume 入口仍必须强制走 provider 强刷
        #（休眠期间的新鲜缓存可能早已失效，§32 不得据此跳过刷新）
        self._waiting_task()
        self._write_exhausted_cache()
        with mock.patch.object(
                quota_resolver, "resolve_quota_status",
                return_value=self._resolved_exhausted()) as resolver_mock:
            result = task_manager.resume_from_quota(self.repo, TID)
        resolver_mock.assert_called_once_with(self.repo, force_refresh=True)
        self.assertFalse(result["resumed"])

    def test_two_exhausted_windows_use_latest_reset(self):
        self._waiting_task()
        self._write_exhausted_cache()
        with mock.patch.object(
                quota_resolver, "resolve_quota_status",
                return_value=self._resolved_exhausted()):
            result = task_manager.resume_from_quota(self.repo, TID)
        # plan_resume 统一口径：max(12:00Z, 13:00Z) + 300 秒宽限
        self.assertEqual(result["recommended_resume_at"],
                         "2026-08-31T13:05:00Z")
        # EXHAUSTED 非恢复分支：零转态、unit_recovery 为空 dict
        self.assertFalse(result["resumed"])
        self.assertEqual(result["unit_recovery"], {})
        self.assertEqual(state.load_state(self.repo, TID)["status"],
                         "waiting_quota")

    def test_auto_once_not_scheduled_at_first_partial_reset(self):
        # auto_once 只有 1 窗预算：若按历史 min 口径在首个局部 reset
        # （five_hour 12:00Z）唤醒，weekly 仍 EXHAUSTED——单窗预算被
        # 浪费；统一为 plan_resume 最晚口径后建议时刻 = 13:05Z
        self._waiting_task(auto_resume="auto_once", max_windows=1)
        self._write_exhausted_cache()
        with mock.patch.object(
                quota_resolver, "resolve_quota_status",
                return_value=self._resolved_exhausted()):
            result = task_manager.resume_from_quota(self.repo, TID)
        self.assertEqual(result["recommended_resume_at"],
                         "2026-08-31T13:05:00Z")
        self.assertNotEqual(result["recommended_resume_at"],
                            "2026-08-31T12:05:00Z")
        self.assertEqual(result["wake_budget_remaining"], 1)

    def test_explicit_exhausted_does_not_read_cache_or_fabricate(self):
        # 显式 status：零解析、零缓存读——不虚构调度时刻（恒 None）
        self._waiting_task()
        self._write_exhausted_cache()
        with mock.patch.object(
                quota_resolver, "resolve_quota_status") as resolver_mock:
            result = task_manager.resume_from_quota(self.repo, TID,
                                                    status="EXHAUSTED")
        resolver_mock.assert_not_called()
        self.assertFalse(result["resumed"])
        self.assertIsNone(result["recommended_resume_at"])
        self.assertEqual(result["unit_recovery"], {})


class QuotaResumeReconciliationTest(unittest.TestCase):
    """RB-21-01 §10.1 C/D：running 中断单元恢复对账四分落点。

    中断来源（runtime.quota_interrupted_from）由 handle_quota_exhausted
    在转 waiting_quota 时写入（写入 / 不覆盖见 HandleQuotaExhaustedTest）；
    本类锁定恢复侧：running 来源先 reconcile 四分对账再落点，legacy /
    ready 来源直回 ready 不对账，成功落点清除标记，journal
    quota_resumed 增量携带 unit_recovery。
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def _waiting_task(self, units):
        save_task(self.repo, status="waiting_quota", units=units)

    @staticmethod
    def _report(classification, rationale=("fixture rationale",),
                evidence=None):
        """构造 reconcile_agent_run 返回形状的桩（冻结三键）。"""
        payload = {"classification": classification,
                   "rationale": list(rationale)}
        if evidence is not None:
            payload["evidence"] = evidence
        return payload

    def test_running_quota_interruption_not_blindly_ready(self):
        self._waiting_task([make_interrupted_unit("wu-r", "running")])
        with mock.patch.object(
                reconcile_mod, "reconcile_agent_run",
                return_value=self._report("redispatch_clean")) as rec:
            result = task_manager.resume_from_quota(self.repo, TID,
                                                    status="AVAILABLE")
        # ready 是对账后的结论，不是盲目转态：reconcile 以账本根 /
        # 任务 / 单元逐字调用
        rec.assert_called_once_with(self.repo, TID, "wu-r")
        self.assertTrue(result["resumed"])
        self.assertEqual(result["unit_recovery"]["wu-r"],
                         {"classification": "redispatch_clean"})
        on_disk = state.load_state(self.repo, TID)
        self.assertEqual(on_disk["status"], "executing")
        self.assertEqual(on_disk["work_units"][0]["status"], "ready")

    def test_running_interrupted_reuse_result_not_redispatched(self):
        self._waiting_task([make_interrupted_unit("wu-r", "running")])
        evidence = {"runs": [{"unit": "wu-r", "agent_id": "a-1"}],
                    "owned_residue": ["src/wu_r.py"],
                    "unattributable_residue": [],
                    "verification": None, "native": None}
        with mock.patch.object(
                reconcile_mod, "reconcile_agent_run",
                return_value=self._report(
                    "reuse_result",
                    rationale=[
                        "agent archived terminal + fresh evidence"],
                    evidence=evidence)):
            result = task_manager.resume_from_quota(self.repo, TID,
                                                    status="AVAILABLE")
        # 成果可复用：经 §62 演进新边直达 verifying，不重派 implementation；
        # 证据字段原样透传 + action_required 指引主会话先捞成果
        self.assertEqual(result["unit_recovery"]["wu-r"], {
            "classification": "reuse_result",
            "action_required": "recover_agent_result",
            "evidence": evidence})
        on_disk = state.load_state(self.repo, TID)
        self.assertEqual(on_disk["work_units"][0]["status"], "verifying")
        self.assertEqual(on_disk["status"], "executing")
        self.assertTrue(result["resumed"])

    def test_running_interrupted_progress_requires_progress_resume(self):
        self._waiting_task([make_interrupted_unit("wu-r", "running")])
        evidence = {"runs": [{"unit": "wu-r"}],
                    "owned_residue": ["src/wu_r.py"],
                    "unattributable_residue": [],
                    "verification": None, "native": None}
        with mock.patch.object(
                reconcile_mod, "reconcile_agent_run",
                return_value=self._report(
                    "resume_with_progress",
                    rationale=[
                        "partial work present, no fresh pass evidence"],
                    evidence=evidence)):
            result = task_manager.resume_from_quota(self.repo, TID,
                                                    status="AVAILABLE")
        # 有进度回 ready：证据句柄透传——主会话必须组装 Previous
        # Progress Package 随新规格续派（编排纪律，runtime 只透传）
        self.assertEqual(result["unit_recovery"]["wu-r"], {
            "classification": "resume_with_progress",
            "evidence": evidence})
        on_disk = state.load_state(self.repo, TID)
        self.assertEqual(on_disk["work_units"][0]["status"], "ready")
        self.assertTrue(result["resumed"])

    def test_manual_ruling_does_not_dispatch(self):
        self._waiting_task([make_interrupted_unit("wu-r", "running")])
        with mock.patch.object(
                reconcile_mod, "reconcile_agent_run",
                return_value=self._report(
                    "manual_ruling",
                    rationale=["unattributable edits present"])):
            result = task_manager.resume_from_quota(self.repo, TID,
                                                    status="AVAILABLE")
        # 人工裁决未决：单元保持 waiting_quota（标记保留）、任务不转
        # executing、resumed=False、不落 quota_resumed
        self.assertFalse(result["resumed"])
        self.assertEqual(result["unit_recovery"]["wu-r"], {
            "classification": "manual_ruling",
            "rationale": ["unattributable edits present"]})
        on_disk = state.load_state(self.repo, TID)
        self.assertEqual(on_disk["status"], "waiting_quota")
        self.assertEqual(on_disk["work_units"][0]["status"],
                         "waiting_quota")
        self.assertEqual(on_disk["work_units"][0]["runtime"],
                         {"quota_interrupted_from": "running"})
        self.assertNotIn("quota_resumed",
                         [e.get("event")
                          for e in journal.read_events(self.repo, TID)])

    def test_reconcile_error_fails_closed_to_manual_ruling(self):
        self._waiting_task([make_interrupted_unit("wu-r", "running")])
        with mock.patch.object(
                reconcile_mod, "reconcile_agent_run",
                side_effect=RuntimeError("boom")):
            result = task_manager.resume_from_quota(self.repo, TID,
                                                    status="AVAILABLE")
        # fail-closed：对账异常按 manual_ruling 处理，绝不炸掉整个 resume
        self.assertFalse(result["resumed"])
        entry = result["unit_recovery"]["wu-r"]
        self.assertEqual(entry["classification"], "manual_ruling")
        self.assertEqual(entry["rationale"],
                         ["reconcile error: RuntimeError"])
        on_disk = state.load_state(self.repo, TID)
        self.assertEqual(on_disk["status"], "waiting_quota")
        self.assertEqual(on_disk["work_units"][0]["status"],
                         "waiting_quota")

    def test_legacy_and_ready_origin_units_skip_reconcile(self):
        # legacy（无 runtime 标记）与 ready 来源直回 ready，不经对账
        self._waiting_task([make_unit("wu-legacy", "waiting_quota"),
                            make_interrupted_unit("wu-a", "ready")])
        with mock.patch.object(
                reconcile_mod, "reconcile_agent_run") as rec:
            result = task_manager.resume_from_quota(self.repo, TID,
                                                    status="AVAILABLE")
        rec.assert_not_called()
        self.assertEqual(result["unit_recovery"], {})
        self.assertTrue(result["resumed"])
        on_disk = state.load_state(self.repo, TID)
        self.assertEqual([(u["id"], u["status"]) for u in
                          on_disk["work_units"]],
                         [("wu-legacy", "ready"), ("wu-a", "ready")])

    def test_resume_clears_origin_and_journal_carries_unit_recovery(self):
        self._waiting_task([make_interrupted_unit("wu-r", "running")])
        with mock.patch.object(
                reconcile_mod, "reconcile_agent_run",
                return_value=self._report("redispatch_clean")):
            result = task_manager.resume_from_quota(self.repo, TID,
                                                    status="AVAILABLE")
        # 成功恢复的单元清除中断来源标记（runtime dict 空则整键删除）
        on_disk = state.load_state(self.repo, TID)
        self.assertNotIn("runtime", on_disk["work_units"][0])
        # journal quota_resumed 增量携带 unit_recovery（现有键保留）
        events = [e for e in journal.read_events(self.repo, TID)
                  if e.get("event") == "quota_resumed"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["status"], "AVAILABLE")
        self.assertEqual(events[0]["source"], "explicit")
        self.assertEqual(events[0]["unit_recovery"],
                         {"wu-r": {"classification":
                                   "redispatch_clean"}})
        self.assertTrue(result["resumed"])


if __name__ == "__main__":
    unittest.main()
