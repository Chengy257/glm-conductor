#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""runtime.resume_manifest 单元测试（v2.1 M3 wu-21-07）。

覆盖：
  - build/write/read 形状逐键（含 legacy 无 execution_policy / 无
    quota 块的兜底）；
  - quota_control 快照（v2.2 M1a D15-e）：legacy 缺 continuation 块
    按默认解释 / continuation 事实容错派生 / current_boundary_id 优先
    于 boundary_id / 半块逐键回退；
  - 三挂点触发（commit_dispatch / abort_dispatch / finish_unit 后
    manifest 刷新——active_units / next_ready_candidates 随事务变化）；
  - §10.3 失败语义：manifest 写失败绝不影响主事务（mock 抛异常 →
    四 API 照常成功 + manifest_write_failed 警告事件）；
  - agent_runs 截断 20 + truncated 标注；
  - read 缺失 / 损坏 JSON / 非 dict → None；
  - 原子写（无 .tmp 残留）；
  - CLI manifest-show 两态与退出码。

fixture：tempfile 仓库 + runtime.state 构造带 work unit 的任务 +
task_manager 真实事务。仅 Python 3 标准库。
"""

import sys, unittest
import json, tempfile
from pathlib import Path
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "glm-conductor"))
from runtime import (journal, resume_manifest, state, task_manager)

TID = "manifest-test-1a2b3c"
ROUTE = {"mode": "delegate", "delegability": "high",
         "assurance": "standard", "executor": "flash-implementer",
         "continuity": "resumable"}


def make_unit(uid, deps=()):
    return {"id": uid, "objective": "o " + uid, "status": "pending",
            "depends_on": list(deps), "executor": "flash-implementer",
            "ownership": ["src/%s.py" % uid],
            "verification": ["python3 -m unittest -h"]}


def save_task(repo, units):
    st = state.new_task_state(
        TID, "manifest fixture", dict(ROUTE),
        ownership_files=["src"],
        verification_required=["python3 -m unittest -h"], status="executing")
    st["work_units"] = units
    state.save_state(repo, st)
    return st


class ManifestShapeTest(unittest.TestCase):
    """build/write/read 形状与 legacy 兜底。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = self._tmp.name
        save_task(self.repo, [make_unit("wu-1")])

    def tearDown(self):
        self._tmp.cleanup()

    def test_shape_frozen(self):
        manifest = resume_manifest.write_resume_manifest(self.repo, TID)
        for key in ("task_id", "written_at", "task_status",
                    "last_event_seq", "active_units", "verification_due",
                    "agent_runs", "next_ready_candidates",
                    "quota_snapshot", "resume_authorization",
                    "quota_control"):
            self.assertIn(key, manifest)
        self.assertEqual(manifest["task_id"], TID)
        self.assertEqual(manifest["task_status"], "executing")
        self.assertEqual(manifest["next_ready_candidates"], ["wu-1"])
        self.assertEqual(manifest["verification_due"], ["wu-1"])
        # legacy：本 fixture 无 quota 块 → UNKNOWN；无 execution_policy
        # → 默认块（manual / 0）
        self.assertEqual(manifest["quota_snapshot"],
                         {"status": "UNKNOWN", "observed_at": None})
        self.assertEqual(manifest["resume_authorization"],
                         {"mode": "resumable", "remaining_windows": 0})
        # v2.2 M1a：新任务含 continuation 默认块 → quota_control 快照
        # 逐键等于默认口径（unknown/unknown/recurring/none/0）
        self.assertEqual(
            manifest["quota_control"],
            {"scheduler_origin": "unknown",
             "scheduler_create_capability": "unknown",
             "wake_bridge_mode": "recurring",
             "wake_bridge_status": "none",
             "automation_id": None,
             "generation": 0,
             "boundary_id": None,
             "next_wake_at": None,
             "bridge_interval_minutes": None})
        # 回读一致
        reread = resume_manifest.read_resume_manifest(self.repo, TID)
        self.assertEqual(reread["task_id"], TID)
        # 原子写：无 tmp 残留
        leftovers = list((Path(self.repo) / ".glm-conductor/tasks" / TID)
                         .glob("*.tmp"))
        self.assertEqual(leftovers, [])

    def test_read_missing_and_corrupt(self):
        self.assertIsNone(
            resume_manifest.read_resume_manifest(self.repo, TID))
        path = resume_manifest.resume_manifest_path(self.repo, TID)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json", encoding="utf-8")
        self.assertIsNone(
            resume_manifest.read_resume_manifest(self.repo, TID))
        path.write_text('["list"]', encoding="utf-8")
        self.assertIsNone(
            resume_manifest.read_resume_manifest(self.repo, TID))

    def test_agent_runs_truncation(self):
        from runtime import agent_run, dispatch_wave
        permit = dispatch_wave.create_permit(self.repo, TID, "wu-1")
        for i in range(25):
            agent_run.record_agent_launch(
                self.repo, TID,
                {"tool_use_id": "call-%d" % i,
                 "tool_response": {"agentId": "a-%d" % i}},
                permit=permit)
        manifest = resume_manifest.write_resume_manifest(self.repo, TID)
        self.assertEqual(len(manifest["agent_runs"]), 20)
        self.assertTrue(manifest["agent_runs_truncated"])


class QuotaControlSnapshotTest(unittest.TestCase):
    """quota_control 快照（v2.2 M1a D15-e）：legacy 缺块按默认解释，
    continuation 事实按容错读派生，绝不抛。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = self._tmp.name
        save_task(self.repo, [make_unit("wu-1")])

    def tearDown(self):
        self._tmp.cleanup()

    def _manifest_quota_control(self):
        return resume_manifest.write_resume_manifest(
            self.repo, TID)["quota_control"]

    def test_legacy_state_without_continuation_gets_defaults(self):
        # legacy v2.1 形态（无 continuation 键）：快照恒九键、按默认块解释
        st = state.load_state(self.repo, TID)
        del st["continuation"]
        state.save_state(self.repo, st)
        self.assertEqual(
            self._manifest_quota_control(),
            {"scheduler_origin": "unknown",
             "scheduler_create_capability": "unknown",
             "wake_bridge_mode": "recurring",
             "wake_bridge_status": "none",
             "automation_id": None,
             "generation": 0,
             "boundary_id": None,
             "next_wake_at": None,
             "bridge_interval_minutes": None})

    def test_continuation_facts_reflected_and_current_boundary_wins(self):
        # 有 continuation 时正确反映 mode / status / automation_id；
        # boundary_id 取 current_boundary_id 优先，缺省回退 boundary_id
        st = state.load_state(self.repo, TID)
        st["continuation"]["scheduler_context"] = {
            "origin": "scheduled_task", "create": "forbidden",
            "update": "unknown", "pause": "allowed", "delete": "allowed",
            "parent_automation_id": "cron-parent-1"}
        st["continuation"]["wake_bridge"] = {
            "status": "armed",
            "boundary_id": "five_hour:2026-09-01T12:00:00+00:00",
            "automation_id": "cron-bridge-1",
            "reset_at": "2026-09-01T12:00:00+00:00",
            "wake_at": "2026-09-01T11:55:00+00:00",
            "armed_at": "2026-09-01T11:00:00+00:00",
            "fired_at": None,
            "mode": "self_retiming",
            "generation": 2,
            "current_boundary_id": "weekly:2026-09-07T00:00:00+00:00",
            "next_wake_at": "2026-09-01T11:55:00+00:00",
            "bridge_interval_minutes": 60}
        state.save_state(self.repo, st)
        self.assertEqual(
            self._manifest_quota_control(),
            {"scheduler_origin": "scheduled_task",
             "scheduler_create_capability": "forbidden",
             "wake_bridge_mode": "self_retiming",
             "wake_bridge_status": "armed",
             "automation_id": "cron-bridge-1",
             "generation": 2,
             "boundary_id": "weekly:2026-09-07T00:00:00+00:00",
             "next_wake_at": "2026-09-01T11:55:00+00:00",
             "bridge_interval_minutes": 60})
        # wu-22-01 旧形态（无 current_boundary_id）：回退 legacy
        # boundary_id 键
        st = state.load_state(self.repo, TID)
        del st["continuation"]["wake_bridge"]["current_boundary_id"]
        state.save_state(self.repo, st)
        self.assertEqual(
            self._manifest_quota_control()["boundary_id"],
            "five_hour:2026-09-01T12:00:00+00:00")

    def test_half_block_falls_back_per_key(self):
        # 半块 / 缺键逐键按默认解释（形状确定性，恢复入口零猜测）
        st = state.load_state(self.repo, TID)
        st["continuation"]["wake_bridge"] = {
            "status": "paused", "automation_id": "cron-bridge-2"}
        state.save_state(self.repo, st)
        snapshot = self._manifest_quota_control()
        self.assertEqual(snapshot["wake_bridge_status"], "paused")
        self.assertEqual(snapshot["automation_id"], "cron-bridge-2")
        self.assertEqual(snapshot["wake_bridge_mode"], "recurring")
        self.assertEqual(snapshot["generation"], 0)
        self.assertIsNone(snapshot["boundary_id"])
        self.assertIsNone(snapshot["bridge_interval_minutes"])


class HookPointsTest(unittest.TestCase):
    """三挂点触发与事务后快照刷新。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = self._tmp.name
        # finish 路径的 RB-1 证据门要算 git 指纹——fixture 必须是 git 仓库
        import subprocess
        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=True)
        save_task(self.repo, [make_unit("wu-1"), make_unit("wu-2",
                                                           deps=["wu-1"])])
        task_manager.refresh_readiness(self.repo, TID)

    def tearDown(self):
        self._tmp.cleanup()

    def _manifest(self):
        return resume_manifest.read_resume_manifest(self.repo, TID)

    def test_commit_refreshes_manifest(self):
        task_manager.prepare_dispatch(self.repo, TID, "wu-1",
                                      quota_status="AVAILABLE")
        task_manager.commit_dispatch(self.repo, TID, "wu-1")
        manifest = self._manifest()
        self.assertIsNotNone(manifest)
        self.assertIn("wu-1", manifest["active_units"])
        self.assertEqual(manifest["next_ready_candidates"], [])

    def test_finish_recomputes_next_ready(self):
        task_manager.prepare_dispatch(self.repo, TID, "wu-1",
                                      quota_status="AVAILABLE")
        task_manager.commit_dispatch(self.repo, TID, "wu-1")
        # 单元验证证据（RB-1 门要求）：伪造新鲜 pass 事件
        from runtime import fingerprint
        unit = [w for w in state.load_state(self.repo, TID)["work_units"]
                if w["id"] == "wu-1"][0]
        st = state.load_state(self.repo, TID)
        fp = fingerprint.task_fingerprint(self.repo, st)
        task_manager.record_unit_verification(
            self.repo, TID, "wu-1", unit["verification"][0], fp)
        task_manager.finish_unit(self.repo, TID, "wu-1")
        manifest = self._manifest()
        self.assertEqual(manifest["active_units"], [])
        self.assertEqual(manifest["next_ready_candidates"], ["wu-2"])

    def test_abort_refreshes_manifest(self):
        task_manager.prepare_dispatch(self.repo, TID, "wu-1",
                                      quota_status="AVAILABLE")
        task_manager.abort_dispatch(self.repo, TID, "wu-1")
        manifest = self._manifest()
        self.assertIsNotNone(manifest)
        self.assertEqual(manifest["active_units"], [])

    def test_write_failure_does_not_break_transactions(self):
        task_manager.prepare_dispatch(self.repo, TID, "wu-1",
                                      quota_status="AVAILABLE")
        with mock.patch.object(resume_manifest, "write_resume_manifest",
                               side_effect=OSError("disk full")):
            st = task_manager.commit_dispatch(self.repo, TID, "wu-1")
        # 主事务照常成功
        self.assertIn("wu-1", st["dispatch"]["active"])
        # 警告事件在 journal，事务事件在前
        events = journal.read_events(self.repo, TID)
        names = [e["event"] for e in events]
        self.assertIn("manifest_write_failed", names)
        self.assertLess(names.index("implementation_started"),
                        names.index("manifest_write_failed"))
        warning = [e for e in events
                   if e["event"] == "manifest_write_failed"][0]
        self.assertIn("disk full", warning["error"])


class ManifestCliTest(unittest.TestCase):
    """manifest-show 两态与退出码。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = self._tmp.name
        save_task(self.repo, [make_unit("wu-1")])

    def tearDown(self):
        self._tmp.cleanup()

    def _cli(self, *argv):
        from runtime import cli
        stdout = []
        with mock.patch.object(cli, "_emit",
                               lambda payload: stdout.append(payload)):
            code = cli.main(list(argv))
        return code, stdout[0] if stdout else None

    def test_show_null_then_dict(self):
        code, payload = self._cli("manifest-show", self.repo, TID)
        self.assertEqual(code, 0)
        self.assertEqual(payload, {"manifest": None})
        resume_manifest.write_resume_manifest(self.repo, TID)
        code, payload = self._cli("manifest-show", self.repo, TID)
        self.assertEqual(code, 0)
        self.assertEqual(payload["manifest"]["task_id"], TID)

    def test_usage_error_exit_two(self):
        code, _ = self._cli("manifest-show", self.repo)
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
