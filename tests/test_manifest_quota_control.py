#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v2.2 M9 恢复面三件套测试（wu-22-09，manifest 投影 + SessionStart
桥接对账展示；WU-22-09 账本合并单元的 ledger 必绿口径）。

覆盖四面：

runtime.resume_manifest（manifest 增量投影）：
  - 顶层新键 "continuity" 五键恒存在（obligation / execution_phase /
    auto_resume / consumed_quota_windows / max_quota_windows），默认
    任务按默认口径（none / None / manual / 0 / 0）；
  - 控制回路事实投影：quota.execution_phase（M4 D13 回填）、
    continuation.obligation、auto_resume=until_done + 窗口 1/2；
  - legacy v2.1 state（无 continuation / quota / execution_policy 块）
    五键全默认；半块 / 形状异常容错（直接喂 hand-built dict，绝不抛）；
  - 既有键零扰：manifest 既有十一顶层键齐全，quota_control 冻结九键
    与 resume_authorization 在事实注入后逐字不变（D15-e 冻结形状）；
  - 落盘回读一致。

runtime.recovery（桥接对账 + 四行展示块）：
  - reconcile_wake_bridge 纯函数：stale 判定边界（恰好 2×interval
    不算、超过即算；「已过 next_wake_at 且 >2×interval」）、间隔
    缺省回退（wake_bridge 记账 → execution_policy 默认 60）、
    next_wake_at 缺失 / 不可解析 → 不判 stale（不虚构）、非
    armed/fired 状态不参与时间数学、legacy 空 dict 全默认；
  - fired-but-unresolved：fired + obligation ∈ {wake_required,
    degraded} 判真；fired + 其他义务 / 非 fired 桥判假；
  - 摘要条目条件第十键 "continuity"（十键形状）：携带事实的任务有、
    legacy / 无事实任务无（既有八字段冻结形状不变）；
  - 渲染四行展示块逐字键（WAKE BRIDGE / QUOTA PHASE / AUTO RESUME /
    NEXT SAFE ACTION，值各一行中文短语）+ stale / fired-unresolved
    两判定分支 + legacy 零增行；
  - build_recovery_summary 的 now 注入贯通 stale 判定；
  - 词汇同步锚定：本模块字面量词汇 ⊆ runtime.state / quota.control
    冻结枚举（防漂移）。

hooks.session_start（子进程直调，同 test_recovery 模式）：
  - armed 桥任务 → additionalContext 透出四行块（管道原样透传）；
  - 无事实任务 → 不透出块（零噪音纪律）。

fixture：tempfile 仓库 + runtime.state 构造；桥接对账时间全部 now
注入（零墙钟依赖、零网络）。仅 Python 3 标准库。

运行：
    cd <repo_root> && python3 -m unittest tests.test_manifest_quota_control -v
"""

import sys, unittest
import datetime
import json, os, subprocess, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "glm-conductor"))
from runtime import journal, recovery, resume_manifest, state

# 被测钩子脚本（管道透出验证用）
SESSION_START = (Path(__file__).resolve().parents[1]
                 / "plugins/glm-conductor/hooks/session_start.py")

TID = "m9-quota-1a2b3c"
ROUTE = {"mode": "solo", "delegability": "low", "assurance": "standard",
         "executor": "main", "continuity": "resumable"}
VERIFY_CMD = "python3 -m unittest -h"

# stale 时间锚（next_wake_at 10:00Z，间隔 30 分钟 → 2×间隔阈值 11:00Z）
WAKE_AT = "2026-09-01T10:00:00Z"
INTERVAL = 30
UTC = datetime.timezone.utc
NOW_JUST_BEFORE = datetime.datetime(2026, 9, 1, 10, 59, 59, tzinfo=UTC)
NOW_AT_THRESHOLD = datetime.datetime(2026, 9, 1, 11, 0, 0, tzinfo=UTC)
NOW_PAST_THRESHOLD = datetime.datetime(2026, 9, 1, 11, 0, 1, tzinfo=UTC)


def make_unit(uid, status="pending"):
    """构造一个通过 §61 契约校验的最小 work unit dict。"""
    return {"id": uid, "objective": "fixture unit %s" % uid,
            "status": status, "depends_on": [], "executor": "main",
            "ownership": ["src/a.py"],
            "verification": [VERIFY_CMD]}


def save_task(repo, task_id=TID, status="executing", units=()):
    """构造带默认块的任务状态（v2.2 形态：continuation / policy 齐全）。"""
    st = state.new_task_state(
        task_id, "M9 恢复面冒烟目标", dict(ROUTE),
        ownership_files=["src/a.py"], verification_required=[VERIFY_CMD],
        status=status)
    st["work_units"] = list(units)
    state.save_state(repo, st)
    return st


def arm_bridge(repo, task_id=TID, *, status="armed", obligation="armed",
               next_wake_at=WAKE_AT, interval=INTERVAL,
               automation_id="cron-bridge-1"):
    """给任务布防/改写 wake bridge 并写盘（validate 口径内的合法形状）。"""
    st = state.load_state(repo, task_id)
    st["continuation"]["obligation"] = obligation
    st["continuation"]["wake_bridge"] = {
        "status": status,
        "boundary_id": "five_hour:2026-09-01T12:00:00+00:00",
        "automation_id": automation_id,
        "reset_at": "2026-09-01T12:00:00+00:00",
        "armed_at": "2026-09-01T09:30:00+00:00",
        "mode": "recurring",
        "generation": 1,
        "current_boundary_id": "five_hour:2026-09-01T12:00:00+00:00",
        "next_wake_at": next_wake_at,
        "bridge_interval_minutes": interval,
    }
    state.save_state(repo, st)
    return st


def run_hook(stdin_text, project_dir):
    """以子进程运行 session_start.py（UTF-8 解码，同 test_recovery）。"""
    return subprocess.run(
        [sys.executable, str(SESSION_START)],
        input=stdin_text, text=True, capture_output=True,
        encoding="utf-8", errors="replace",
        env=dict(os.environ, ZCODE_PROJECT_DIR=str(project_dir)))


# —— manifest 增量投影 ——

class ManifestContinuitySnapshotTest(unittest.TestCase):
    """manifest 顶层 "continuity" 增量键：默认形状 / 事实投影 / legacy。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = self._tmp.name
        save_task(self.repo, units=[make_unit("wu-1")])

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self):
        return resume_manifest.write_resume_manifest(self.repo, TID)

    def test_default_shape_five_keys(self):
        # 新任务（默认块）：五键恒存在，全默认口径
        manifest = self._write()
        self.assertEqual(
            manifest["continuity"],
            {"obligation": "none",
             "execution_phase": None,
             "auto_resume": "manual",
             "consumed_quota_windows": 0,
             "max_quota_windows": 0})
        # 落盘回读一致
        reread = resume_manifest.read_resume_manifest(self.repo, TID)
        self.assertEqual(reread["continuity"],
                         manifest["continuity"])

    def test_facts_projected(self):
        # execution_phase 回填 + until_done 授权 + 窗口记账 1/2 + 义务态
        st = state.load_state(self.repo, TID)
        st["quota"] = {"execution_phase": "DRAINING"}
        st["continuation"]["obligation"] = "wake_required"
        from runtime.execution_policy import set_resume_authorization
        st["execution_policy"] = set_resume_authorization(
            st["execution_policy"], auto_resume="until_done",
            max_quota_windows=2, source="user",
            confirmed_at="2026-09-01T00:00:00Z")
        st["execution_policy"]["continuity"]["consumed_quota_windows"] = 1
        state.save_state(self.repo, st)
        self.assertEqual(
            self._write()["continuity"],
            {"obligation": "wake_required",
             "execution_phase": "DRAINING",
             "auto_resume": "until_done",
             "consumed_quota_windows": 1,
             "max_quota_windows": 2})

    def test_legacy_state_defaults(self):
        # legacy v2.1 形态（三块全无）：五键全默认，绝不抛
        st = state.load_state(self.repo, TID)
        for key in ("continuation", "execution_policy", "quota"):
            st.pop(key, None)
        state.save_state(self.repo, st)
        self.assertEqual(
            self._write()["continuity"],
            {"obligation": "none", "execution_phase": None,
             "auto_resume": "manual", "consumed_quota_windows": 0,
             "max_quota_windows": 0})

    def test_half_block_and_garbage_shapes_tolerated(self):
        # 半块 / 形状异常（hand-built dict 直喂纯函数，绕过保存闸）：
        # 缺块按默认、垃圾形状按 0 保守折算，绝不抛
        snapshot = resume_manifest._continuity_snapshot({
            "continuation": {"obligation": "armed"},
            "quota": {"execution_phase": "PRESSURE"},
            "execution_policy": "garbage",
        })
        self.assertEqual(snapshot, {
            "obligation": "armed", "execution_phase": "PRESSURE",
            "auto_resume": "manual", "consumed_quota_windows": 0,
            "max_quota_windows": 0})
        self.assertEqual(
            resume_manifest._continuity_snapshot({}),
            {"obligation": "none", "execution_phase": None,
             "auto_resume": "manual", "consumed_quota_windows": 0,
             "max_quota_windows": 0})
        # bool / 负数 / 非整数窗口数 → 0（bool 是 int 子类，拒绝）
        for bad in (True, -1, "2", 1.5, None):
            st = {"execution_policy": {"continuity": {
                "auto_resume": "notify", "max_quota_windows": bad}}}
            self.assertEqual(
                resume_manifest._continuity_snapshot(st)
                ["max_quota_windows"], 0, bad)

    def test_existing_keys_untouched_by_continuity_key(self):
        # 只增不改不删：既有十一顶层键齐全；quota_control 冻结九键与
        # resume_authorization 在事实注入后逐字不变（D15-e 冻结形状）
        st = state.load_state(self.repo, TID)
        st["quota"] = {"execution_phase": "NORMAL"}
        st["continuation"]["obligation"] = "armed"
        st["continuation"]["wake_bridge"] = {
            "status": "armed", "automation_id": "cron-x",
            "mode": "recurring", "generation": 3,
            "current_boundary_id": "weekly:b2",
            "next_wake_at": WAKE_AT, "bridge_interval_minutes": 60}
        state.save_state(self.repo, st)
        manifest = self._write()
        for key in ("task_id", "written_at", "task_status",
                    "last_event_seq", "active_units", "verification_due",
                    "agent_runs", "agent_runs_truncated",
                    "next_ready_candidates", "quota_snapshot",
                    "resume_authorization", "quota_control", "continuity"):
            self.assertIn(key, manifest)
        self.assertEqual(
            manifest["quota_control"],
            {"scheduler_origin": "unknown",
             "scheduler_create_capability": "unknown",
             "wake_bridge_mode": "recurring",
             "wake_bridge_status": "armed",
             "automation_id": "cron-x",
             "generation": 3,
             "boundary_id": "weekly:b2",
             "next_wake_at": WAKE_AT,
             "bridge_interval_minutes": 60})
        self.assertEqual(manifest["resume_authorization"],
                         {"mode": "resumable", "remaining_windows": 0})


# —— recovery 桥接对账（纯函数） ——

class ReconcileWakeBridgeTest(unittest.TestCase):
    """reconcile_wake_bridge：stale 边界 / fired-unresolved / legacy。"""

    def _task(self, *, status="armed", obligation="armed",
              next_wake_at=WAKE_AT, interval=INTERVAL, **bridge_extra):
        bridge = {"status": status, "automation_id": "cron-1",
                  "mode": "recurring", "generation": 1,
                  "next_wake_at": next_wake_at,
                  "bridge_interval_minutes": interval}
        bridge.update(bridge_extra)
        return {"continuation": {"obligation": obligation,
                                 "wake_bridge": bridge}}

    def test_stale_threshold_boundary(self):
        task = self._task()
        # 2×interval 内（哪怕已过 next_wake_at）→ 不判 stale
        self.assertFalse(recovery.reconcile_wake_bridge(
            task, now=NOW_JUST_BEFORE)["stale"])
        # 恰好在阈值上（> 为严格大于）→ 不判 stale
        self.assertFalse(recovery.reconcile_wake_bridge(
            task, now=NOW_AT_THRESHOLD)["stale"])
        # 超过 2×interval → stale
        verdict = recovery.reconcile_wake_bridge(task, now=NOW_PAST_THRESHOLD)
        self.assertTrue(verdict["stale"])
        self.assertTrue(verdict["fired_unresolved"] is False)

    def test_stale_interval_fallback_to_policy_default(self):
        # wake_bridge 记账缺 interval → 回退 execution_policy 默认 60
        # （阈值 = 10:00Z + 120min = 12:00Z；now 11:01Z 在 30 分钟口径
        # 已 stale，60 分钟口径未到 → 未 stale 证明回退生效）
        task = self._task(interval=None)
        self.assertFalse(recovery.reconcile_wake_bridge(
            task, now=datetime.datetime(2026, 9, 1, 11, 1, tzinfo=UTC))
            ["stale"])
        self.assertTrue(recovery.reconcile_wake_bridge(
            task, now=datetime.datetime(2026, 9, 1, 12, 0, 1, tzinfo=UTC))
            ["stale"])
        # policy 显式配置的间隔也参与回退
        task_with_policy = dict(self._task(interval=None))
        task_with_policy["execution_policy"] = {"quota_control": {
            "bridge_interval_minutes": 15}}
        self.assertTrue(recovery.reconcile_wake_bridge(
            task_with_policy, now=datetime.datetime(2026, 9, 1, 10, 31,
                                                    tzinfo=UTC))["stale"])

    def test_stale_requires_parseable_next_wake_at(self):
        # next_wake_at 缺失 / 不可解析 → 不判 stale（不虚构时刻）
        for bad in (None, "", "not-a-time"):
            task = self._task(next_wake_at=bad)
            self.assertFalse(recovery.reconcile_wake_bridge(
                task, now=NOW_PAST_THRESHOLD)["stale"], bad)

    def test_stale_only_for_armed_or_fired(self):
        # 非 armed/fired 桥不参与时间数学（词汇 ⊆ 冻结十值，见同步锚定）
        task = self._task(next_wake_at="2000-01-01T00:00:00Z",
                          bridge_interval_minutes=5)
        for status in ("none", "requested", "retarget_required", "degraded",
                       "paused", "cancelled", "stale", "failed"):
            task["continuation"]["wake_bridge"]["status"] = status
            verdict = recovery.reconcile_wake_bridge(
                task, now=datetime.datetime(2026, 9, 1, tzinfo=UTC))
            self.assertFalse(verdict["stale"], status)
            self.assertEqual(verdict["bridge_status"], status)

    def test_fired_unresolved_branches(self):
        # fired + wake_required / degraded → 判真
        for obligation in ("wake_required", "degraded"):
            task = self._task(status="fired", obligation=obligation,
                              next_wake_at=None)
            verdict = recovery.reconcile_wake_bridge(task, now=NOW_JUST_BEFORE)
            self.assertTrue(verdict["fired_unresolved"], obligation)
            self.assertFalse(verdict["stale"])
        # fired + 其他义务 → 假；非 fired 桥 + wake_required → 假
        self.assertFalse(recovery.reconcile_wake_bridge(
            self._task(status="fired", obligation="none",
                       next_wake_at=None),
            now=NOW_JUST_BEFORE)["fired_unresolved"])
        self.assertFalse(recovery.reconcile_wake_bridge(
            self._task(status="armed", obligation="wake_required"),
            now=NOW_JUST_BEFORE)["fired_unresolved"])

    def test_legacy_empty_state_defaults(self):
        # legacy 空 dict：七键恒存在全默认，绝不抛；now 缺省走墙钟也安全
        verdict = recovery.reconcile_wake_bridge({})
        self.assertEqual(verdict, {
            "bridge_status": "none", "obligation": "none",
            "automation_id": None, "next_wake_at": None,
            "interval_minutes": 60, "stale": False,
            "fired_unresolved": False})
        # now 支持 ISO 串注入（与 datetime 等价）
        self.assertTrue(recovery.reconcile_wake_bridge(
            self._task(), now="2026-09-01T11:00:01Z")["stale"])

    def test_fired_stale_simultaneous(self):
        # fired 桥也可同时判 stale（两判定独立：armed/fired 均参与时间
        # 数学；fired-unresolved 只看义务）
        task = self._task(status="fired", obligation="wake_required")
        verdict = recovery.reconcile_wake_bridge(task, now=NOW_PAST_THRESHOLD)
        self.assertTrue(verdict["stale"])
        self.assertTrue(verdict["fired_unresolved"])


# —— recovery 摘要 / 渲染 ——

class SummaryContinuityKeyTest(unittest.TestCase):
    """摘要条目条件第十键 "continuity"（十键形状 / 触发条件 / legacy）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def _entry(self):
        return recovery.build_recovery_summary(
            self.repo, now=NOW_PAST_THRESHOLD)["tasks"][0]

    def test_continuity_key_shape_ten_keys(self):
        save_task(self.repo, units=[make_unit("wu-1")])
        arm_bridge(self.repo)
        view = self._entry()["continuity"]
        self.assertEqual(sorted(view.keys()), [
            "automation_id", "bridge_status", "consumed_quota_windows",
            "execution_phase", "fired_unresolved", "interval_minutes",
            "max_quota_windows", "next_wake_at", "obligation", "stale"])
        self.assertEqual(view["bridge_status"], "armed")
        self.assertEqual(view["automation_id"], "cron-bridge-1")
        self.assertEqual(view["interval_minutes"], INTERVAL)
        self.assertTrue(view["stale"])  # now 已注入 11:00:01Z
        self.assertEqual(view["execution_phase"], None)
        self.assertEqual(view["consumed_quota_windows"], 0)
        self.assertEqual(view["max_quota_windows"], 0)

    def test_no_facts_task_omits_key_frozen_shape(self):
        # 无事实任务（默认块 / executing）：整键省略，八字段冻结形状不变
        save_task(self.repo, units=[make_unit("wu-1", "running")])
        entry = self._entry()
        self.assertNotIn("continuity", entry)
        self.assertEqual(sorted(entry.keys()), [
            "auto_resume", "completed_units", "goal", "incomplete_units",
            "interrupted_units", "repo_root", "status", "task_id"])

    def test_legacy_state_omits_key(self):
        # legacy v2.1 任务：零增键（优雅降级为安静）
        save_task(self.repo)
        st = state.load_state(self.repo, TID)
        for key in ("continuation", "execution_policy", "quota"):
            st.pop(key, None)
        state.save_state(self.repo, st)
        self.assertNotIn("continuity", self._entry())

    def test_execution_phase_backfill_triggers_key(self):
        # 仅 execution_phase 回填（无桥 / 无义务）也触发增补
        save_task(self.repo)
        st = state.load_state(self.repo, TID)
        st["quota"] = {"execution_phase": "PRESSURE"}
        state.save_state(self.repo, st)
        view = self._entry()["continuity"]
        self.assertEqual(view["bridge_status"], "none")
        self.assertEqual(view["execution_phase"], "PRESSURE")
        self.assertFalse(view["stale"])

    def test_waiting_task_gets_defaults(self):
        # waiting_quota 无桥任务：键存在且桥/义务全默认；consumed 记账
        # 独立合法（consumed>=0 不受 max 约束），max 缺省 0
        save_task(self.repo, status="waiting_quota",
                  units=[make_unit("wu-1", "waiting_quota")])
        st = state.load_state(self.repo, TID)
        for key in ("continuation", "quota"):
            st.pop(key, None)
        st["execution_policy"]["continuity"]["consumed_quota_windows"] = 1
        state.save_state(self.repo, st)
        view = self._entry()["continuity"]
        self.assertEqual(view["bridge_status"], "none")
        self.assertEqual(view["obligation"], "none")
        self.assertEqual(view["consumed_quota_windows"], 1)
        self.assertEqual(view["max_quota_windows"], 0)


class RenderContinuityBlockTest(unittest.TestCase):
    """四行展示块：逐字键 / stale 与 fired-unresolved 分支 / legacy。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def _render(self, now=NOW_JUST_BEFORE):
        # 缺省 now 在 2×间隔阈值之前（armed 桥健康）；stale 分支显式
        # 注入 NOW_PAST_THRESHOLD
        return recovery.render_resume_context(
            recovery.build_recovery_summary(self.repo, now=now))

    def test_four_verbatim_lines_armed_bridge(self):
        save_task(self.repo, units=[make_unit("wu-1")])
        arm_bridge(self.repo)
        text = self._render()
        for marker in recovery.CONTINUITY_BLOCK_KEYS:
            self.assertIn("%s: " % marker, text)
        self.assertIn("WAKE BRIDGE: 已布防（armed），"
                      "automation=cron-bridge-1，下次唤醒 %s" % WAKE_AT,
                      text)
        self.assertIn("QUOTA PHASE: 未知（prepare 时点回填，尚无记录）",
                      text)
        self.assertIn("AUTO RESUME: manual（不自动跨额度窗口续跑）", text)
        # armed 健康（本 fixture 未 stale）→ 常规指引
        self.assertIn("NEXT SAFE ACTION: 唤醒桥已布防：等待下次唤醒",
                      text)

    def test_stale_branch_annotates_bridge_and_action(self):
        save_task(self.repo, units=[make_unit("wu-1")])
        arm_bridge(self.repo)  # now=11:00:01Z > 2×30min 阈值 → stale
        text = self._render(now=NOW_PAST_THRESHOLD)
        self.assertIn("WAKE BRIDGE: 已布防（armed），"
                      "automation=cron-bridge-1，下次唤醒 %s"
                      "——疑似失联：已过下次唤醒超过 %d×间隔（%d 分钟）"
                      "仍未触发"
                      % (WAKE_AT,
                         recovery.STALE_BRIDGE_INTERVAL_MULTIPLIER,
                         INTERVAL), text)
        self.assertIn(
            "NEXT SAFE ACTION: 唤醒桥疑似失联：先核验 automation 存活"
            "（CronList）", text)
        # stale 优先于 armed 常规指引
        self.assertNotIn("唤醒桥已布防：等待下次唤醒", text)

    def test_fired_unresolved_branch(self):
        # next_wake_at=None（无唤醒时刻 → 不参与 stale 时间数学），
        # 隔离 fired-unresolved 判定单独验证
        save_task(self.repo, units=[make_unit("wu-1")])
        arm_bridge(self.repo, status="fired", obligation="wake_required",
                   automation_id="cron-fired-9", next_wake_at=None)
        text = self._render()
        self.assertIn("WAKE BRIDGE: 已触发（fired），"
                      "automation=cron-fired-9"
                      "——已触发但续跑义务未消（obligation=wake_required），"
                      "唤醒未被对账消费", text)
        self.assertIn(
            "NEXT SAFE ACTION: 唤醒已触发但续跑义务未消：先强刷额度"
            "（quota-resolve --force-refresh）并 reconcile 中断单元",
            text)

    def test_phase_lines_for_backfilled_phases(self):
        # DRAINING / BLOCKED 回填相驱动 NEXT SAFE ACTION 排水/冻结指引
        save_task(self.repo)
        st = state.load_state(self.repo, TID)
        st["quota"] = {"execution_phase": "DRAINING"}
        state.save_state(self.repo, st)
        text = self._render()
        self.assertIn("QUOTA PHASE: DRAINING", text)
        self.assertIn("NEXT SAFE ACTION: 额度排水中（DRAINING）：勿派新"
                      "实施单元", text)
        st = state.load_state(self.repo, TID)
        st["quota"] = {"execution_phase": "BLOCKED"}
        state.save_state(self.repo, st)
        text = self._render()
        self.assertIn("NEXT SAFE ACTION: 额度已冻结（BLOCKED）：派发与"
                      "收尾一律暂停", text)

    def test_waiting_task_action_and_budget_line(self):
        # waiting_quota：等额度指引 + AUTO RESUME 行带窗口预算
        save_task(self.repo, status="waiting_quota",
                  units=[make_unit("wu-1", "waiting_quota")])
        st = state.load_state(self.repo, TID)
        from runtime.execution_policy import set_resume_authorization
        st["execution_policy"] = set_resume_authorization(
            st["execution_policy"], auto_resume="until_done",
            max_quota_windows=2, source="user",
            confirmed_at="2026-09-01T00:00:00Z")
        st["execution_policy"]["continuity"]["consumed_quota_windows"] = 1
        state.save_state(self.repo, st)
        text = self._render()
        self.assertIn("AUTO RESUME: until_done（窗口预算已用 1/2）", text)
        self.assertIn("NEXT SAFE ACTION: 任务在等额度/等授权", text)

    def test_legacy_task_zero_extra_lines(self):
        # legacy / 无事实任务：零增行（既有渲染逐字不变）
        save_task(self.repo)
        text = self._render()
        for marker in recovery.CONTINUITY_BLOCK_KEYS:
            self.assertNotIn(marker, text)


class VocabularySyncTest(unittest.TestCase):
    """词汇同步锚定：本单元字面量词汇 ⊆ 各冻结枚举（防漂移）。"""

    def test_stale_candidates_within_state_vocabulary(self):
        self.assertEqual(recovery._STALE_CANDIDATE_BRIDGES,
                         ("armed", "fired"))
        for status in recovery._STALE_CANDIDATE_BRIDGES:
            self.assertIn(status, state.WAKE_BRIDGE_STATUSES)

    def test_fired_unresolved_within_obligation_vocabulary(self):
        for obligation in recovery._FIRED_UNRESOLVED_OBLIGATIONS:
            self.assertIn(obligation, state.CONTINUATION_OBLIGATIONS)

    def test_safe_action_phase_literals_within_execution_phases(self):
        from runtime.quota.control import EXECUTION_PHASES
        for phase in ("BLOCKED", "DRAINING"):
            self.assertIn(phase, EXECUTION_PHASES)

    def test_stale_multiplier_frozen(self):
        self.assertEqual(recovery.STALE_BRIDGE_INTERVAL_MULTIPLIER, 2)

    def test_block_keys_verbatim(self):
        self.assertEqual(
            recovery.CONTINUITY_BLOCK_KEYS,
            ("WAKE BRIDGE", "QUOTA PHASE", "AUTO RESUME",
             "NEXT SAFE ACTION"))


# —— hooks.session_start 管道透出 ——

class SessionStartPipeThroughTest(unittest.TestCase):
    """session_start 子进程：四行块原样透出（只增块，管道零改动）。"""

    def test_armed_bridge_task_emits_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            save_task(tmp, units=[make_unit("wu-1", "running")])
            arm_bridge(tmp)
            result = run_hook(
                json.dumps({"session_id": "s-1", "source": "startup"}), tmp)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "")
            payload = json.loads(result.stdout.strip())
            ctx = payload["hookSpecificOutput"]["additionalContext"]
            self.assertEqual(payload["hookSpecificOutput"]["hookEventName"],
                             "SessionStart")
            for marker in recovery.CONTINUITY_BLOCK_KEYS:
                self.assertIn("%s: " % marker, ctx)
            self.assertIn("GLM CONDUCTOR RESUME CONTEXT", ctx)
            self.assertIn("Recommended:", ctx)

    def test_no_fact_task_emits_no_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            save_task(tmp, units=[make_unit("wu-1")])
            result = run_hook("{}", tmp)
            self.assertEqual(result.returncode, 0, result.stderr)
            ctx = json.loads(result.stdout.strip())[
                "hookSpecificOutput"]["additionalContext"]
            for marker in recovery.CONTINUITY_BLOCK_KEYS:
                self.assertNotIn(marker, ctx)
            self.assertIn("Task: %s" % TID, ctx)


if __name__ == "__main__":
    unittest.main()
