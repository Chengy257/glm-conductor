#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI workflow-model-select --repo-root 单元测试（v2.5.2 W3）。

--repo-root <path>：任务账本按显式根解析——调用 cwd 不再必须钉在
目标仓库根（与其它带 <repo_root> 位置参数的命令对齐）；缺省当前
工作目录保持既有兼容。

覆盖：
  1. cwd 在别处 + --repo-root 指向账本根 → 任务 pin 正常消费
     （多候选歧义被 pin 打破，exit 0）；
  2. 不带 --repo-root 时账本仍按 cwd 解析（兼容不变）：外乡
     cwd 下任务缺失 → exit 1；
  3. models 绝对路径 + --repo-root 绝对路径：完全 cwd 无关形态；
  4. 用法闸：--repo-root 缺取值 / 重复 / 位置参数超长 → exit 2。

零网络：models 清单与任务账本都在 tempfile 临时目录；真实子进程
调用（cwd 可钉任意目录），仅 Python 3 标准库。

运行：
    cd <repo_root> && python3 -m unittest tests.test_workflow_model_repo_root -v
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

CLI_PATH = (Path(__file__).resolve().parents[1] / "plugins" /
            "glm-conductor" / "runtime" / "cli.py")

# runtime import（tests/test_cli_v24.py 同款 sys.path 引导）
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "glm-conductor"))
from runtime import messages, task  # noqa: E402

# 测试用模型 id（假账户段，绝不指向任何真实账户；形态约定同
# tests/test_worker_model_pin.py 的 FLASH_A / FLASH_B）
FLASH_A = "account:team-a/GLM-5.3-Flash"
FLASH_B = "account:team-b/GLM-5.3-Flash"

TID = "reporoot-ut-9f3b1d"


class WorkflowModelSelectRepoRootCliTest(unittest.TestCase):
    """--repo-root：账本根显式解析，cwd 解耦。"""

    def setUp(self):
        # 账本根（任务 + pin）与调用 cwd 分居两个临时目录
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = self._tmp.name
        self._cwd_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._cwd_tmp.cleanup)
        self.foreign_cwd = self._cwd_tmp.name

    def run_cli_cwd(self, cwd, *args):
        """cwd 可钉任意目录的真实子进程调用，返回 (退出码, stdout)。"""
        proc = subprocess.run(
            [sys.executable, str(CLI_PATH)] + list(args),
            cwd=cwd, text=True, capture_output=True,
            encoding="utf-8", errors="replace")
        return proc.returncode, proc.stdout

    def write_models(self, models, directory, name="models.json"):
        """把模型 id 数组写到指定临时目录，返回文件绝对路径。"""
        path = Path(directory) / name
        path.write_text(json.dumps(models, ensure_ascii=False),
                        encoding="utf-8")
        return str(path)

    def make_task(self):
        return task.create_task(self.repo, TID, "--repo-root 解析测试",
                                {"mode": "solo"})

    def test_repo_root_resolves_pin_from_foreign_cwd(self):
        """cwd 在别处 + --repo-root 指向账本根 → 任务 pin 正常消费。"""
        self.make_task()
        task.pin_worker_model(self.repo, TID, FLASH_B)
        models_path = self.write_models([FLASH_A, FLASH_B],
                                        self.foreign_cwd)
        code, stdout = self.run_cli_cwd(
            self.foreign_cwd, "workflow-model-select", models_path,
            "--task-ref", TID, "--repo-root", self.repo)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout)["subagent_model"], FLASH_B)

    def test_without_repo_root_foreign_cwd_missing_task_exit_1(self):
        """不带 --repo-root 时账本仍按 cwd 解析（兼容不变）：外乡
        cwd 下任务缺失 → 退出码 1。"""
        self.make_task()
        task.pin_worker_model(self.repo, TID, FLASH_A)
        models_path = self.write_models([FLASH_A], self.foreign_cwd)
        code, stdout = self.run_cli_cwd(
            self.foreign_cwd, "workflow-model-select", models_path,
            "--task-ref", TID)
        self.assertEqual(code, 1)
        self.assertIn(messages.CLI_WORKFLOW_MODEL_SELECT_TASK_MISSING
                      % (TID, self.foreign_cwd),
                      json.loads(stdout)["error"])

    def test_absolute_paths_fully_cwd_independent(self):
        """models 绝对路径 + --repo-root 绝对路径：完全 cwd 无关形态。"""
        self.make_task()
        task.pin_worker_model(self.repo, TID, FLASH_A)
        models_path = self.write_models([FLASH_A], self.repo)
        code, stdout = self.run_cli_cwd(
            self.foreign_cwd, "workflow-model-select", models_path,
            "--task-ref", TID, "--repo-root", self.repo)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout)["subagent_model"], FLASH_A)

    def test_repo_root_flag_usage_gates_exit_2(self):
        """--repo-root 缺取值 / 重复 / 位置参数超长 → 用法口径 2。"""
        models_path = self.write_models([FLASH_A], self.repo)
        code, _stdout = self.run_cli_cwd(
            self.repo, "workflow-model-select", models_path, "--repo-root")
        self.assertEqual(code, 2)
        code, _stdout = self.run_cli_cwd(
            self.repo, "workflow-model-select", models_path,
            "--repo-root", self.repo, "--repo-root", self.repo)
        self.assertEqual(code, 2)
        code, _stdout = self.run_cli_cwd(
            self.repo, "workflow-model-select", models_path,
            "--model", FLASH_A, "--task-ref", TID,
            "--repo-root", self.repo, "extra")
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
