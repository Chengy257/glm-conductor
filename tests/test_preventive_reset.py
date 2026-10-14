#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v2.6.0 Phase B（WP-R2）单一 reset_at 无条件透传 + 预防性双通道武装
回归测试——规格 §2.6 十条逐条落位。

规格权威：docs/roadmap/POST_V2_5_2_RELIABILITY_AND_MODEL_SELECTION_
IMPLEMENTATION_SPEC.md §2.6（Required precision regression tests）；
实现面：runtime/cli.py（_quota_observation_view / _quota_resume_
decision）、runtime/quota/parser.py（earliest_future_reset_at）、
runtime/task.py（scheduled_activation_decision，语义不动）。

条目 → 测试类映射：
  ① AVAILABLE + 有效 five_hour reset → view 与 no-op decision 均透传
     reset_at                    TestObservationViewPassthrough /
                                 TestDecisionUnconditionalPassthrough
  ② PRESSURE 同透传              同上两类
  ③ EXHAUSTED waiting 同透传且 remain-waiting 决策语义不变
                                 同上两类（resume_count / 状态零副作用）
  ④ UNKNOWN + 有效观测 reset 仍可见但 UNKNOWN 不授权 resume
                                 同上两类（remain-waiting 而非 resume）
  ⑤ weekly 晚于 five_hour 取更早 five_hour、weekly 更早取 weekly、
     重复唤醒按新快照重选（非硬编码 +5h）
                                 TestEarliestFutureSelection（纯函数，
                                 now 注入）+ TestRepeatedWakeReselects
  ⑥ 全部过期/无效 → 刷新一次、不虚构、floor-only 如实披露、无负睡眠/
     自旋                        TestStaleObservationRefreshOnce（runtime
                                 刷新语义）+ TestRunbookContract（睡眠
                                 模板护栏的文档结构断言）
  ⑦ 静默耗尽无停靠调用 → 首个睡眠已在臂（对技能 runbook 用文档结构
     断言，锚点与 scripts/validate_plugin.py 16k 检查同源）
                                 TestRunbookContract
  ⑧ 重启丢睡眠 → 地板恢复重臂；地板+睡眠双触发不双 confirm（幂等
     决策）                      TestRunbookContract + TestDualWakeIdempotent
  ⑨ 新鲜唤醒 AVAILABLE/no-op 仍透传下一 reset 可重臂；weekly 仍阻塞
     时不假恢复                  TestDecisionUnconditionalPassthrough +
                                 TestWeeklyStillBlockingNoFakeResume
  ⑩ 无新 CLI reset 字段 / 无第二调度器 / 无新额度状态机（结构断言）
                                 TestStructuralNoSecondScheduler

零网络纪律：凡涉 resolver 的用例一律 mock.patch 注入假 detail（键冻结
四键：source/status/snapshot/fetched_at）；scratch 目录全部 tempfile.
TemporaryDirectory，绝不触碰真实账本；文档断言只读插件 SKILL/参考文
件。仅 Python 3.7 标准库。

运行：
    cd <repo_root> && PYTHONUTF8=1 python3 -m unittest tests.test_preventive_reset -v
"""

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "glm-conductor"))

from runtime import cli, journal, state, task  # noqa: E402
from runtime.quota import parser as quota_parser  # noqa: E402
from runtime.quota import resolver as quota_resolver  # noqa: E402
from runtime.workflow import adapter as workflow_adapter  # noqa: E402

PLUGIN_ROOT = (Path(__file__).resolve().parents[1] / "plugins" /
               "glm-conductor")
CONTINUITY_SKILL = PLUGIN_ROOT / "skills" / "continuity" / "SKILL.md"
LONG_HORIZON = (PLUGIN_ROOT / "skills" / "continuity" / "references" /
                "long-horizon.md")
ORCHESTRATION_SKILL = PLUGIN_ROOT / "skills" / "orchestration" / "SKILL.md"
QUOTA_COMMAND = PLUGIN_ROOT / "commands" / "quota.md"
RUNTIME_DIR = PLUGIN_ROOT / "runtime"

TID = "preventive-reset-4e2a9c"


# —— 公共夹具 ——

def iso_ms_z(moment):
    """aware datetime → 毫秒精度 Z 形式串（§27/provider reset_at 形态）。"""
    return moment.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


NOW = datetime.now(timezone.utc)
"""模块级当前 UTC 锚（窗口锚相对它生成——reset_at 只认「有效未来」，
硬编码日期会随时间漂移成过去）。"""


def future_reset(hours):
    """相对模块加载时刻的未来 reset 锚（毫秒精度 Z 串）。"""
    return iso_ms_z(NOW + timedelta(hours=hours))


def make_window(kind, used, reset_at):
    """构造 §27 形状窗口（used=None 即数据不完整 → UNKNOWN 窗）。"""
    remaining = None if used is None else round(100 - used, 2)
    return {"kind": kind, "used_percent": used,
            "remaining_percent": remaining, "reset_at": reset_at}


def fake_detail(status, windows=None, fetched_at=None, source="provider"):
    """构造 resolve_quota_detail 形状的假观测（键冻结四键，零网络）。"""
    snapshot = None if windows is None else {"windows": windows}
    return {"source": source, "status": status,
            "snapshot": snapshot, "fetched_at": fetched_at}


def evaluation(windows):
    """snapshot → parser.evaluate 输出（喂 earliest_future_reset_at）。"""
    return quota_parser.evaluate({"windows": windows})


class PreventiveResetCase(unittest.TestCase):
    """临时仓库根 + adapter RUNS_DIR 注入 + 进程内 cli.main 调用。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = self._tmp.name
        patcher = mock.patch.object(
            workflow_adapter, "RUNS_DIR",
            os.path.join(self._tmp.name, ".glm-conductor", "workflow-runs"))
        patcher.start()
        self.addCleanup(patcher.stop)

    # —— 装置助手 ——

    def run_cli(self, *args):
        """进程内调用 cli.main，返回 (退出码, stdout 原文)。"""
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = cli.main(list(args))
        return code, buffer.getvalue()

    def run_cli_json(self, *args):
        """进程内调用 cli.main 并把 stdout 单行 JSON 解析为 dict。"""
        code, stdout = self.run_cli(*args)
        return code, json.loads(stdout)

    def install_detail(self, detail):
        """注入恒定假 resolve_quota_detail，返回 mock（可断言调用）。"""
        return mock.patch.object(quota_resolver, "resolve_quota_detail",
                                 return_value=detail)

    def install_detail_sequence(self, details):
        """注入按调用次序出队的假 detail 序列，返回 force_refresh 现场记录。"""
        calls = []

        def fake(repo_root, force_refresh=False):
            calls.append(force_refresh)
            return details[min(len(calls), len(details)) - 1]

        patcher = mock.patch.object(quota_resolver, "resolve_quota_detail",
                                    new=fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        return calls

    def make_task(self, task_id=TID):
        """临时仓库创建缺省 solo / manual 任务。"""
        return task.create_task(self.repo, task_id,
                                "单一 reset_at 透传与预防性武装回归",
                                {"mode": "solo"})

    def prepare_waiting(self, task_id=TID, max_resumes=3,
                        run_id="run-preventive-1",
                        automation_id="sched-witness-preventive"):
        """auto 授权 + 武装 + 关联 run + 进入 waiting_quota（决策面前置）。"""
        self.make_task(task_id)
        task.record_workflow_run(self.repo, task_id, run_id)
        task.authorize_quota_resume(self.repo, task_id, max_resumes)
        if automation_id is not None:
            task.bind_quota_automation(self.repo, task_id, automation_id)
        task.enter_waiting_quota(self.repo, task_id)

    def load(self, task_id=TID):
        return state.load_state(self.repo, task_id)

    def names(self, task_id=TID):
        return [item.get("event")
                for item in journal.read_events(self.repo, task_id)]


# —— §2.6 ⑤（纯函数层）：确定性窗口选择 ——

class TestEarliestFutureSelection(unittest.TestCase):
    """parser.earliest_future_reset_at：最早的有效未来观测 reset。

    规格锚（§2.2）：UTC instant 显式解析比较（不靠字典序巧合）；只认
    已识别、可解析、严格的未来时刻；取更早；无候选 → None 绝不虚构。
    """

    BASE = datetime(2026, 10, 14, 8, 0, tzinfo=timezone.utc)

    def select(self, windows, now=None):
        return quota_parser.earliest_future_reset_at(
            evaluation(windows), now=now or self.BASE)

    def test_weekly_later_selects_earlier_five_hour(self):
        weekly_late = "2026-10-19T00:00:00Z"
        five_hour = "2026-10-14T13:00:00Z"
        windows = [make_window("five_hour", 100.0, five_hour),
                   make_window("weekly", 10.0, weekly_late)]
        self.assertEqual(self.select(windows), five_hour)

    def test_weekly_earlier_selects_weekly(self):
        five_hour_late = "2026-10-14T17:00:00Z"
        weekly_early = "2026-10-14T13:00:00Z"
        windows = [make_window("five_hour", 100.0, five_hour_late),
                   make_window("weekly", 100.0, weekly_early)]
        self.assertEqual(self.select(windows), weekly_early)

    def test_expired_and_future_pair_picks_future(self):
        expired = "2026-10-14T07:00:00Z"  # 早于 BASE 08:00Z
        future = "2026-10-14T13:00:00Z"
        windows = [make_window("five_hour", 100.0, expired),
                   make_window("weekly", 10.0, future)]
        self.assertEqual(self.select(windows), future)

    def test_utc_instant_parse_not_lexical(self):
        # A 字典序更大（"1..." > "0..."）但时刻更早（09:30Z < 09:45Z）
        offset_form = "2026-10-14T10:30:00+01:00"  # = 09:30Z
        z_form = "2026-10-14T09:45:00Z"
        windows = [make_window("five_hour", 50.0, offset_form),
                   make_window("weekly", 50.0, z_form)]
        self.assertEqual(self.select(windows), offset_form)

    def test_boundary_reset_at_now_excluded(self):
        # 严格晚于 now：恰好等于 now 的 reset 不参与（不产生零/负睡眠）
        boundary = "2026-10-14T08:00:00Z"
        windows = [make_window("five_hour", 50.0, boundary)]
        self.assertIsNone(self.select(windows))

    def test_invalid_and_unrecognized_excluded(self):
        windows = [
            {"kind": "five_hour", "reset_at": None},           # 无观测
            {"kind": "weekly", "reset_at": "not-a-time"},      # 不可解析
            {"kind": "daily", "reset_at": "2026-10-14T09:00:00Z"},  # 未识别窗
            make_window("five_hour", 50.0, "2026-10-14T13:00:00Z"),
        ]
        self.assertEqual(self.select(windows), "2026-10-14T13:00:00Z")

    def test_malformed_evaluation_none_without_raise(self):
        for bad in (None, {}, {"windows": None}, {"windows": "x"},
                    {"windows": [None, "x", 42]}):
            self.assertIsNone(
                quota_parser.earliest_future_reset_at(bad, now=self.BASE))

    def test_no_candidate_returns_none(self):
        windows = [make_window("five_hour", 100.0, "2026-10-14T07:00:00Z")]
        self.assertIsNone(self.select(windows))

    def test_invalid_now_injection_refused(self):
        for bad in (1742774400, "yesterday", object()):
            with self.assertRaises(ValueError) as ctx:
                quota_parser.earliest_future_reset_at(
                    evaluation([]), now=bad)
            self.assertIn("earliest_future_reset_at", str(ctx.exception))


# —— §2.6 ①②③④（观测 view 层）：四态一律无条件透传 ——

class TestObservationViewPassthrough(PreventiveResetCase):
    """_quota_observation_view：reset_at 与窗口状态无关（规格 §2.2：
    额度状态只管可否继续/恢复，不管可否透传时间）。"""

    def view_for(self, status, windows):
        detail = fake_detail(status, windows=windows,
                             fetched_at="2026-10-14T08:00:00.000Z")
        with self.install_detail(detail):
            return cli._quota_observation_view(self.repo)

    def test_available_passes_reset(self):
        reset = future_reset(5)
        view = self.view_for("AVAILABLE",
                             [make_window("five_hour", 10.0, reset)])
        self.assertEqual(view["reset_at"], reset)
        self.assertEqual(view["status"], "AVAILABLE")

    def test_pressure_passes_reset(self):
        reset = future_reset(5)
        view = self.view_for("PRESSURE",
                             [make_window("five_hour", 95.0, reset)])
        self.assertEqual(view["reset_at"], reset)

    def test_exhausted_passes_reset(self):
        reset = future_reset(5)
        view = self.view_for("EXHAUSTED",
                             [make_window("five_hour", 100.0, reset)])
        self.assertEqual(view["reset_at"], reset)

    def test_unknown_passes_valid_observed_reset(self):
        reset = future_reset(5)
        view = self.view_for("UNKNOWN",
                             [make_window("five_hour", None, reset)])
        self.assertEqual(view["reset_at"], reset)

    def test_view_is_exactly_four_keys(self):
        # 单一公开 reset 面：view 恰四键（status/source/observed_at/
        # reset_at），绝不出现第二个 reset 字段（规格 §2.6 第 10 条）
        reset = future_reset(5)
        view = self.view_for("AVAILABLE",
                             [make_window("five_hour", 10.0, reset)])
        self.assertEqual(set(view),
                         {"status", "source", "observed_at", "reset_at"})
        self.assertEqual(view["observed_at"], "2026-10-14T08:00:00.000Z")
        self.assertEqual(view["source"], "provider")


# —— §2.6 ⑤（观测层）：重复唤醒按新快照重选 ——

class TestRepeatedWakeReselects(PreventiveResetCase):
    """连续两次唤醒各取当次快照的最早有效未来 reset——绝不硬编码
    「加 5 小时」推算下一窗口（规格 §2.6 第 5 条）。"""

    def test_second_wake_uses_fresh_snapshot(self):
        first_reset = future_reset(2)     # 首次唤醒观测：five_hour +2h
        second_reset = future_reset(4.5)  # 二次唤醒新快照：滚动到 +4.5h
        calls = self.install_detail_sequence([
            fake_detail("EXHAUSTED",
                        windows=[make_window("five_hour", 100.0,
                                             first_reset),
                                 make_window("weekly", 10.0,
                                             future_reset(96))]),
            fake_detail("EXHAUSTED",
                        windows=[make_window("five_hour", 100.0,
                                             second_reset),
                                 make_window("weekly", 10.0,
                                             future_reset(96))]),
        ])
        view1 = cli._quota_observation_view(self.repo)
        view2 = cli._quota_observation_view(self.repo)
        self.assertEqual(view1["reset_at"], first_reset)
        # 第二次唤醒按新快照重选：锚点 = 新观测的最早未来（+4.5h），
        # 既非旧锚（陈旧目标不发睡眠），也非旧锚 +5h 的硬编码推算
        self.assertEqual(view2["reset_at"], second_reset)
        self.assertNotEqual(view2["reset_at"], view1["reset_at"])
        self.assertEqual(calls, [False, False])


# —— §2.6 ⑥（runtime 刷新语义）：刷新恰一次、不虚构 ——

class TestStaleObservationRefreshOnce(PreventiveResetCase):
    """已过期/无效/陈旧观测 → force refresh 恰一次；仍不可得 →
    reset_at=null 如实披露（floor-only 降级），绝不虚构（规格 §2.2）；
    provider/none 来源不二次刷新；显式 --force-refresh 不叠加。"""

    PAST = iso_ms_z(NOW - timedelta(hours=2))  # 相对模块加载时刻必为过去

    def test_stale_cache_refreshed_once_recovers_future_reset(self):
        future = future_reset(5)
        calls = self.install_detail_sequence([
            fake_detail("EXHAUSTED", source="cache_stale",
                        fetched_at="2026-10-14T06:00:00.000Z",
                        windows=[make_window("five_hour", 100.0,
                                             self.PAST)]),
            fake_detail("EXHAUSTED", source="provider",
                        fetched_at="2026-10-14T08:00:00.000Z",
                        windows=[make_window("five_hour", 100.0, future)]),
        ])
        view = cli._quota_observation_view(self.repo)
        # 恰两次调用：首次不强制（缓存层），缺有效未来 reset → 补一次
        # force_refresh；view 与最终一次观测同源（provider 三键全换新）
        self.assertEqual(calls, [False, True])
        self.assertEqual(view["reset_at"], future)
        self.assertEqual(view["source"], "provider")
        self.assertEqual(view["observed_at"], "2026-10-14T08:00:00.000Z")

    def test_refresh_still_empty_reports_null_no_fabrication(self):
        calls = self.install_detail_sequence([
            fake_detail("EXHAUSTED", source="cache_stale",
                        fetched_at="2026-10-14T06:00:00.000Z",
                        windows=[make_window("five_hour", 100.0,
                                             self.PAST)]),
            fake_detail("EXHAUSTED", source="provider",
                        fetched_at="2026-10-14T08:00:00.000Z",
                        windows=[make_window("five_hour", 100.0,
                                             self.PAST)]),
        ])
        view = cli._quota_observation_view(self.repo)
        self.assertEqual(calls, [False, True])
        # 仍不可得 → null 如实披露（floor-only），绝不虚构时刻
        self.assertIsNone(view["reset_at"])
        self.assertEqual(view["source"], "provider")
        self.assertEqual(view["observed_at"], "2026-10-14T08:00:00.000Z")

    def test_provider_source_never_refreshed_twice(self):
        # provider 是刚取到的当前事实：全过期也不二次刷新
        calls = self.install_detail_sequence([
            fake_detail("EXHAUSTED",
                        fetched_at="2026-10-14T08:00:00.000Z",
                        windows=[make_window("five_hour", 100.0,
                                             self.PAST)]),
        ])
        view = cli._quota_observation_view(self.repo)
        self.assertEqual(calls, [False])
        self.assertIsNone(view["reset_at"])

    def test_none_source_never_refreshed(self):
        # none 层 provider 已在本轮尝试过（resolver 纪律绝不重试网络）
        calls = self.install_detail_sequence([
            fake_detail("UNKNOWN", source="none", fetched_at=None,
                        windows=None),
        ])
        view = cli._quota_observation_view(self.repo)
        self.assertEqual(calls, [False])
        self.assertIsNone(view["reset_at"])

    def test_valid_future_cache_not_refreshed(self):
        # 有效未来观测在场：不触发补刷（恰一次调用）
        future = future_reset(5)
        calls = self.install_detail_sequence([
            fake_detail("EXHAUSTED", source="cache_fresh",
                        fetched_at="2026-10-14T07:59:00.000Z",
                        windows=[make_window("five_hour", 100.0, future)]),
        ])
        view = cli._quota_observation_view(self.repo)
        self.assertEqual(calls, [False])
        self.assertEqual(view["reset_at"], future)

    def test_explicit_force_refresh_not_stacked(self):
        # 调用方已显式 --force-refresh：仍只恰一次（不叠加第二次）
        calls = self.install_detail_sequence([
            fake_detail("EXHAUSTED", source="cache_stale",
                        fetched_at="2026-10-14T06:00:00.000Z",
                        windows=[make_window("five_hour", 100.0,
                                             self.PAST)]),
        ])
        view = cli._quota_observation_view(self.repo, force_refresh=True)
        self.assertEqual(calls, [True])
        self.assertIsNone(view["reset_at"])


# —— §2.6 ①②③④⑨（决策层）：所有 action 无条件透传 + 语义不动 ——

class TestDecisionUnconditionalPassthrough(PreventiveResetCase):
    """quota-resume-decision：decision.reset_at 对四种 action 全透传；
    既有 action/reason/workflow_run_id/resume_count_after 与任务层副
    作用逐字不动（规格 §2.3 / §2.6 第 1-4 条）。"""

    #: 观测五态场景的窗口数据（UNKNOWN=数据不完整窗，EXHAUSTED=耗尽
    #: 窗，其余=健康窗；reset 统一取 +5h 有效未来锚）
    USED_BY_STATUS = {"AVAILABLE": 10.0, "PRESSURE": 95.0,
                      "EXHAUSTED": 100.0, "UNKNOWN": None}

    def scenario(self, task_id, status):
        """给定任务 id + 指定状态观测 → (code, payload)。"""
        detail = fake_detail(
            status, fetched_at="2026-10-14T08:00:00.000Z",
            windows=[make_window("five_hour", self.USED_BY_STATUS[status],
                                 future_reset(5))])
        with self.install_detail(detail):
            return self.run_cli_json("quota-resume-decision", self.repo,
                                     task_id)

    def test_all_actions_carry_reset_at(self):
        cases = [
            # (状态, 任务前置形态, 期望 action)
            ("AVAILABLE", "active", "no-op"),                  # ① no-op 透传
            ("PRESSURE", "active", "no-op"),                   # ② no-op 透传
            ("EXHAUSTED", "active", "no-op"),
            ("UNKNOWN", "active", "no-op"),                    # ④ no-op 透传
            ("AVAILABLE", "waiting-auto", "resume-authorized"),
            ("PRESSURE", "waiting-auto", "resume-authorized"),
            ("EXHAUSTED", "waiting-auto", "remain-waiting"),   # ③
            ("UNKNOWN", "waiting-auto", "remain-waiting"),     # ④
        ]
        for status, shape, action in cases:
            task_id = "pr-decision-%s-%s" % (status.lower(),
                                             shape.replace("-", ""))
            if shape == "active":
                self.make_task(task_id)
            else:
                self.prepare_waiting(task_id=task_id)
            code, payload = self.scenario(task_id, status)
            self.assertEqual(code, 0, "status=%s" % status)
            decision = payload["decision"]
            self.assertEqual(decision["action"], action,
                             "status=%s" % status)
            # 无条件透传：ISO 串在场且与观测 view 同源同值
            self.assertIn("reset_at", decision, "status=%s" % status)
            self.assertEqual(decision["reset_at"], future_reset(5),
                             "status=%s" % status)
            self.assertEqual(payload["quota_observation"]["reset_at"],
                             decision["reset_at"])
            # 决策键 = 任务层既有键 ∪ 恰一个附加键 reset_at（无删改）
            primitive = task.scheduled_activation_decision(
                self.repo, task_id, {"status": status})
            self.assertTrue(set(primitive) <= set(decision),
                            "status=%s" % status)
            self.assertEqual(set(decision) - set(primitive), {"reset_at"},
                             "status=%s" % status)

    def test_exhausted_waiting_semantics_unchanged(self):
        # ③ remain-waiting 决策语义不变：状态原地、计数零消耗
        self.prepare_waiting()
        detail = fake_detail("EXHAUSTED", fetched_at="2026-10-14T08:00:00.000Z",
                             windows=[make_window("five_hour", 100.0,
                                                  future_reset(5))])
        with self.install_detail(detail):
            code, payload = self.run_cli_json("quota-resume-decision",
                                              self.repo, TID)
        self.assertEqual(code, 0)
        self.assertEqual(payload["decision"]["action"], "remain-waiting")
        loaded = self.load()
        self.assertEqual(loaded["status"], "waiting_quota")
        self.assertEqual(loaded["quota_resume"]["resume_count"], 0)
        self.assertNotIn("quota_resume_confirmed", self.names())

    def test_unknown_does_not_authorize_resume(self):
        # ④ UNKNOWN 观测 reset 可见但绝不授权 resume
        self.prepare_waiting()
        detail = fake_detail("UNKNOWN", fetched_at="2026-10-14T08:00:00.000Z",
                             windows=[make_window("five_hour", None,
                                                  future_reset(5))])
        with self.install_detail(detail):
            code, payload = self.run_cli_json("quota-resume-decision",
                                              self.repo, TID)
        self.assertEqual(code, 0)
        decision = payload["decision"]
        self.assertEqual(decision["action"], "remain-waiting")
        self.assertNotIn("workflow_run_id", decision)
        self.assertNotIn("resume_count_after", decision)
        self.assertEqual(decision["reset_at"], future_reset(5))
        self.assertEqual(self.load()["status"], "waiting_quota")

    def test_fresh_wake_noop_exposes_next_reset_for_rearm(self):
        # ⑨ 新鲜唤醒（active / no-op）仍透传下一 reset——主会话可凭该
        # 值为仍授权 auto 的任务重臂精度睡眠
        self.make_task()
        detail = fake_detail("AVAILABLE", fetched_at="2026-10-14T08:00:00.000Z",
                             windows=[make_window("five_hour", 10.0,
                                                  future_reset(3))])
        with self.install_detail(detail):
            code, payload = self.run_cli_json("quota-resume-decision",
                                              self.repo, TID)
        self.assertEqual(code, 0)
        self.assertEqual(payload["decision"]["action"], "no-op")
        self.assertEqual(payload["decision"]["reset_at"], future_reset(3))


# —— §2.6 ⑨：weekly 仍阻塞时不假恢复 ——

class TestWeeklyStillBlockingNoFakeResume(PreventiveResetCase):
    """five_hour 已翻绿但 weekly 仍 EXHAUSTED → 总体 EXHAUSTED →
    remain-waiting（绝不假恢复）；透传 reset = 最早未来（five_hour）。"""

    def test_weekly_blocking_keeps_waiting_with_visible_reset(self):
        self.prepare_waiting()
        five_hour = future_reset(1)
        weekly = future_reset(90)
        detail = fake_detail("EXHAUSTED", fetched_at="2026-10-14T08:00:00.000Z",
                             windows=[make_window("five_hour", 10.0,
                                                  five_hour),
                                      make_window("weekly", 100.0,
                                                  weekly)])
        with self.install_detail(detail):
            code, payload = self.run_cli_json("quota-resume-decision",
                                              self.repo, TID)
        self.assertEqual(code, 0)
        decision = payload["decision"]
        self.assertEqual(decision["action"], "remain-waiting")
        self.assertNotIn("workflow_run_id", decision)
        self.assertNotIn("resume_count_after", decision)
        # 透传值 = 最早的有效未来观测（five_hour），供收尾续臂换算
        self.assertEqual(decision["reset_at"], five_hour)
        self.assertEqual(payload["quota_observation"]["status"], "EXHAUSTED")
        loaded = self.load()
        self.assertEqual(loaded["status"], "waiting_quota")
        self.assertEqual(loaded["quota_resume"]["resume_count"], 0)


# —— §2.6 ⑧（runtime 幂等半边）：双通道双触发不双 confirm ——

class TestDualWakeIdempotent(PreventiveResetCase):
    """地板与精度睡眠先后触发同一次额度等待：第一唤醒 resume-authorized
    + confirm 恰消耗一预算；第二唤醒 no-op、重复 confirm 拒绝、计数
    仍为 1（幂等决策契约，规格 §2.6 第 8 条）。"""

    def test_duplicate_triggers_confirm_exactly_once(self):
        self.prepare_waiting(max_resumes=3)
        detail = fake_detail("AVAILABLE", fetched_at="2026-10-14T08:00:00.000Z",
                             windows=[make_window("five_hour", 10.0,
                                                  future_reset(5))])
        # 触发 1（地板先到）：resume-authorized → 宿主 resume 接受 → confirm
        with self.install_detail(detail):
            code1, payload1 = self.run_cli_json("quota-resume-decision",
                                                self.repo, TID)
        self.assertEqual(code1, 0)
        self.assertEqual(payload1["decision"]["action"], "resume-authorized")
        self.assertEqual(payload1["decision"]["resume_count_after"], 1)
        code, payload = self.run_cli_json("quota-resume-confirm",
                                          self.repo, TID)
        self.assertEqual(code, 0)
        self.assertEqual(payload["quota_resume"]["resume_count"], 1)
        # 触发 2（精度睡眠随后到）：任务已 active → no-op（仍照常透传
        # reset_at，供收尾续臂），零预算消耗
        with self.install_detail(detail):
            code2, payload2 = self.run_cli_json("quota-resume-decision",
                                                self.repo, TID)
        self.assertEqual(code2, 0)
        self.assertEqual(payload2["decision"]["action"], "no-op")
        self.assertEqual(payload2["decision"]["reset_at"], future_reset(5))
        # 重复 confirm → 拒绝（exit 1），计数不被二次消耗
        code3, payload3 = self.run_cli_json("quota-resume-confirm",
                                            self.repo, TID)
        self.assertEqual(code3, 1)
        self.assertIn("error", payload3)
        self.assertEqual(self.load()["quota_resume"]["resume_count"], 1)
        self.assertEqual(
            self.names().count("quota_resume_confirmed"), 1)


# —— §2.6 ⑦⑧（runbook 半边）+ ⑤⑥ 文档护栏：结构断言 ——

class TestRunbookContract(unittest.TestCase):
    """技能 runbook 文档结构断言（锚点与 scripts/validate_plugin.py
    16k 单一观测口径检查同源）：

      - ⑦ 启动序列在 CreateWorkflow 之前建立地板与首个精度睡眠——
        静默耗尽（无停靠轮）由启动即武装的在臂睡眠覆盖；
      - ⑧ 重启丢睡眠由 recurring 地板自愈重臂；
      - ⑥ 睡眠模板护栏：null/过去目标不发、无负值 sleep、无忙旋、
        绝不虚构、floor-only 如实披露；
      - ⑤ 下一窗口绝不硬编码「加 5 小时」推导；
      - v2.5.2 阻塞窗（EXHAUSTED-only）提取口径措辞四文档零容忍。
    """

    def read(self, path):
        with open(str(path), "r", encoding="utf-8") as handle:
            return handle.read()

    def setUp(self):
        self.continuity = self.read(CONTINUITY_SKILL)
        self.long_horizon = self.read(LONG_HORIZON)
        self.orchestration = self.read(ORCHESTRATION_SKILL)
        self.quota_command = self.read(QUOTA_COMMAND)

    def test_single_observation_anchors_present(self):
        # 与 validator 16k 同源的四文档语义锚
        for text, markers in (
                (self.continuity,
                 ("最早的有效未来观测", "与额度状态无关", "无条件透传",
                  "floor-only",
                  "quota-resume-decision <repo_root> <task_id> "
                  "--force-refresh")),
                (self.long_horizon,
                 ("最早的有效未来观测", "与额度状态无关", "floor-only")),
                (self.orchestration,
                 ("最早的有效未来观测", "与额度状态无关", "floor-only")),
                (self.quota_command,
                 ("earliest valid future observed reset_at",
                  "regardless of quota status", "unconditionally",
                  "floor-only"))):
            for marker in markers:
                self.assertIn(marker, text)

    def test_runbook_arms_sleep_before_createworkflow(self):
        # ⑦ arm-before-work：auto 启动序列里地板绑定 / preflight / 首个
        # 精度睡眠全部先于 CreateWorkflow（两处 runbook 同断言；切片锚
        # 定 auto 分支代码块首行——manual 分支无武装直达 CreateWorkflow
        # 是合法形态，不参与本断言）
        continuity_runbook = self.continuity[
            self.continuity.index("### 启动序列（runbook，arm-before-work）"):]
        orchestration_runbook = self.orchestration[
            self.orchestration.index(
                "问答（§6.0 单轮三问：第一题「启用」+预算"):]
        for runbook in (continuity_runbook, orchestration_runbook):
            workflow_at = runbook.index("CreateWorkflow")
            self.assertLess(runbook.index("quota-automation-bind"),
                            workflow_at)
            self.assertLess(runbook.index("quota-continuity-preflight"),
                            workflow_at)
            self.assertLess(runbook.index("发首个精度睡眠"), workflow_at)

    def test_silent_exhaustion_covered_by_launch_armed_sleep(self):
        # ⑦ 静默耗尽（额度死在回合之间、无停靠轮）→ 首个睡眠已在臂；
        # 停靠轮绝不承担建立首个睡眠
        self.assertIn("额度若死在回合之间（无停靠轮发生）", self.continuity)
        self.assertIn("已武装的待命睡眠恰好覆盖下一个窗口边界",
                      self.continuity)
        self.assertIn("绝不承担建立首个睡眠", self.continuity)

    def test_restart_recovery_via_recurring_floor(self):
        # ⑧ 重启丢睡眠 → 地板自愈重臂（且不把地板触发谎报为精度唤醒）
        self.assertIn("重启杀掉睡眠后由地板兜底", self.long_horizon)
        self.assertIn("自愈", self.long_horizon)
        self.assertIn("self-healing", self.long_horizon)

    def test_sleep_template_guards_no_negative_no_fabrication(self):
        # ⑥ 睡眠模板护栏：bash 动态计算 + null/过去不发 + 无负值睡眠
        # 无忙旋 + 绝不虚构 + floor-only 如实披露
        for marker in ("精度层睡眠模板", "run_in_background", "date -u -d",
                       "差值 <= 0", "无负值 sleep", "无忙旋", "绝不虚构目标",
                       "floor-only"):
            self.assertIn(marker, self.long_horizon)

    def test_next_window_never_hardcoded_plus_five_hours(self):
        # ⑤ 文档护栏：续臂只依据 provider 观测证据，绝不「加 5 小时」
        self.assertIn("不以「加 5 小时」推算", self.long_horizon)

    def test_three_part_success_statement(self):
        # 成功陈述三分：授权 / 地板绑定 / 睡眠实际发出——floor-only 是
        # 可见降级而非「已武装」
        self.assertIn("成功陈述三分", self.continuity)
        self.assertIn("成功陈述三分", self.orchestration)

    def test_blocked_window_semantics_retired_from_docs(self):
        # v2.5.2 EXHAUSTED-only 提取口径措辞退役（零容忍，与 validator
        # 16k 负向检查同源）——防止旧口径回潮
        retired = ("阻塞窗口径", "EXHAUSTED 阻塞窗", "字典序最大值")
        for name, text in (("continuity/SKILL.md", self.continuity),
                           ("long-horizon.md", self.long_horizon),
                           ("commands/quota.md", self.quota_command)):
            for token in retired:
                self.assertNotIn(token, text,
                                 "%s 不应再出现退役口径 %r" % (name, token))


# —— §2.6 ⑩：无新字段 / 无第二调度器 / 无新额度状态机 ——

class TestStructuralNoSecondScheduler(unittest.TestCase):
    """结构断言（规格 §2.6 第 10 条 / §7.2 no-go）：

      - 无第二个公开 precision reset 字段（next_window_reset_at /
        blocking_resume_at / reset_time_v2 等命名在 runtime 零命中）；
      - 无第二调度器（runtime 下无 scheduler / watcher / accounting
        控制面模块——v2.4 Phase 3 已删，防回潮）；
      - 无新额度状态机：决策 action 封闭四词、观测 status/source 词汇
        均保持原集。
    """

    BANNED_RESET_FIELDS = ("next_window_reset_at", "blocking_resume_at",
                           "reset_time_v2")

    def runtime_sources(self):
        for dirpath, dirnames, filenames in os.walk(str(RUNTIME_DIR)):
            dirnames[:] = [d for d in dirnames if d != "__pycache__"]
            for name in filenames:
                if name.endswith(".py"):
                    path = os.path.join(dirpath, name)
                    with open(path, "r", encoding="utf-8") as handle:
                        yield path, handle.read()

    def test_no_second_public_reset_field(self):
        for path, source in self.runtime_sources():
            for token in self.BANNED_RESET_FIELDS:
                self.assertNotIn(
                    token, source,
                    "%s 出现第二 reset 字段命名 %r" % (path, token))

    def test_no_scheduler_module_in_runtime(self):
        for dirpath, dirnames, filenames in os.walk(str(RUNTIME_DIR)):
            dirnames[:] = [d for d in dirnames if d != "__pycache__"]
            for name in filenames:
                lowered = name.lower()
                for marker in ("scheduler", "watcher", "accounting"):
                    self.assertNotIn(
                        marker, lowered,
                        "%s 疑似第二调度器/控制面模块" % os.path.join(
                            dirpath, name))

    def test_decision_vocabulary_closed(self):
        # 决策 action 封闭四词（无新额度状态机的外观面）
        self.assertEqual(
            task.SCHEDULED_ACTIVATION_ACTIONS,
            ("no-op", "remain-waiting", "waiting-user",
             "resume-authorized"))

    def test_status_and_source_vocabularies_unchanged(self):
        # 观测四态 / 四来源词汇保持原集（不新增状态机词汇）
        self.assertEqual(task.QUOTA_VIEW_STATUSES,
                         ("AVAILABLE", "PRESSURE", "EXHAUSTED", "UNKNOWN"))
        self.assertEqual(quota_resolver.QUOTA_SOURCES,
                         ("cache_fresh", "provider", "cache_stale", "none"))


class TestDecisionCliSurface(PreventiveResetCase):
    """CLI 参数面结构断言：quota-resume-decision 可选参数仍只接受
    --force-refresh——CLI 面没有新增 reset 字段入口（§2.6 第 10 条）。"""

    def test_decision_cli_accepts_no_new_reset_flag(self):
        code, payload = self.run_cli_json(
            "quota-resume-decision", self.repo, TID, "--reset-at", "x")
        self.assertEqual(code, 2)
        self.assertIn("--force-refresh", payload["error"])


if __name__ == "__main__":
    unittest.main()
