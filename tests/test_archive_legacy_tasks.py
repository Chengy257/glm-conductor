#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scripts.archive_legacy_tasks 单元测试。"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(REPO_ROOT / "plugins" / "glm-conductor"))

import archive_legacy_tasks  # noqa: E402
from runtime import state  # noqa: E402


class TestArchiveLegacyTasks(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name)
        self.tasks_root = state.tasks_root(self.repo)

    def tearDown(self):
        self._tmp.cleanup()

    def _create_task(self, task_id, payload):
        tdir = self.tasks_root / task_id
        tdir.mkdir(parents=True, exist_ok=True)
        spath = tdir / "state.json"
        with open(spath, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
        return tdir

    def test_empty_repo_returns_clean_result(self):
        res = archive_legacy_tasks.scan_and_archive(self.repo, confirm=False)
        self.assertTrue(res["ok"])
        self.assertTrue(res["dry_run"])
        self.assertEqual(len(res["legacy_candidates"]), 0)
        self.assertEqual(len(res["archived"]), 0)

    def test_mixed_tasks_dry_run_and_confirm(self):
        # 1. 构造 v2.3 遗留任务（含 work_units 集合键与阶段状态 executing）
        self._create_task("legacy-task-1", {
            "task_id": "legacy-task-1",
            "status": "executing",
            "work_units": [{"id": "u1", "status": "pending"}],
        })

        # 2. 构造 v2.3 遗留任务（含 permits 键）
        self._create_task("legacy-task-2", {
            "task_id": "legacy-task-2",
            "status": "active",
            "permits": ["permit-1"],
        })

        # 3. 构造 v2.4 正常活跃任务（七态之一，无遗留标记）
        self._create_task("v24-active-task", {
            "task_id": "v24-active-task",
            "goal": "implement test feature",
            "repository": {"root": str(self.repo)},
            "route": {"mode": "delegate", "assurance": "standard"},
            "dag": [],
            "status": "active",
            "workflow_run_id": None,
            "validation": {"status": None, "change_id": None},
            "review": {"reviewer": None, "verdict": None},
            "quota_resume": {
                "mode": "manual", "max_resumes": 0,
                "resume_count": 0, "automation_id": None,
            },
        })

        # 4. 构造 v2.4 正常终态任务
        self._create_task("v24-completed-task", {
            "task_id": "v24-completed-task",
            "goal": "done goal",
            "repository": {"root": str(self.repo)},
            "route": {"mode": "solo"},
            "dag": [],
            "status": "completed",
            "workflow_run_id": None,
            "validation": {"status": "passed", "change_id": "sha256:abc"},
            "review": {"reviewer": None, "verdict": None},
            "quota_resume": {
                "mode": "manual", "max_resumes": 0,
                "resume_count": 0, "automation_id": None,
            },
        })

        # 5. 构造损坏目录与非任务目录
        corrupt_dir = self.tasks_root / "corrupt-task"
        corrupt_dir.mkdir(parents=True, exist_ok=True)
        with open(corrupt_dir / "state.json", "w", encoding="utf-8") as fh:
            fh.write("not json content")

        orphaned_dir = self.tasks_root / "orphaned-dir"
        orphaned_dir.mkdir(parents=True, exist_ok=True)

        # —— 第一步：测试 dry-run 模式 ——
        dry_res = archive_legacy_tasks.scan_and_archive(self.repo, confirm=False)
        self.assertTrue(dry_res["ok"])
        self.assertTrue(dry_res["dry_run"])
        cand_ids = [c["task_id"] for c in dry_res["legacy_candidates"]]
        self.assertEqual(sorted(cand_ids), ["legacy-task-1", "legacy-task-2"])
        self.assertEqual(len(dry_res["archived"]), 0)

        # 验证文件仍然在原始位置
        self.assertTrue((self.tasks_root / "legacy-task-1").is_dir())
        self.assertTrue((self.tasks_root / "legacy-task-2").is_dir())
        self.assertTrue((self.tasks_root / "v24-active-task").is_dir())

        # —— 第二步：测试 confirm 归档模式 ——
        confirm_res = archive_legacy_tasks.scan_and_archive(self.repo, confirm=True)
        self.assertTrue(confirm_res["ok"])
        self.assertFalse(confirm_res["dry_run"])
        archived_ids = [a["task_id"] for a in confirm_res["archived"]]
        self.assertEqual(sorted(archived_ids), ["legacy-task-1", "legacy-task-2"])

        # 验证遗留任务已被移动至归档目录
        archive_root = self.repo / ".glm-conductor" / "archived-tasks"
        self.assertTrue((archive_root / "legacy-task-1").is_dir())
        self.assertTrue((archive_root / "legacy-task-2").is_dir())
        self.assertFalse((self.tasks_root / "legacy-task-1").exists())
        self.assertFalse((self.tasks_root / "legacy-task-2").exists())

        # 验证 v2.4 正常任务和其它目录未受影响
        self.assertTrue((self.tasks_root / "v24-active-task").is_dir())
        self.assertTrue((self.tasks_root / "v24-completed-task").is_dir())
        self.assertTrue(corrupt_dir.is_dir())
        self.assertTrue(orphaned_dir.is_dir())

        # —— 第三步：重复运行归档 ——
        re_res = archive_legacy_tasks.scan_and_archive(self.repo, confirm=True)
        self.assertEqual(len(re_res["legacy_candidates"]), 0)
        self.assertEqual(len(re_res["archived"]), 0)


if __name__ == "__main__":
    unittest.main()
