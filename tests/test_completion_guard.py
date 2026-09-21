#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hooks.stop_gate 完成守卫单元测试（v2.4 Phase 2 工作包 P2-D，单元 W4）。

运行：
    cd <repo_root> && python3 -X utf8 -m unittest tests.test_completion_guard -v

覆盖（W4 单元规格）：
    - evaluate_completion 单查失败各出对应理由（恰好一条中文、可操作）：
      writer_guard（他人持有 → 逐字报持有者 task_id / workflow_run_id）、
      ownership（越界路径逐条列出 + dag 节点 id）、validation（无通过
      记录 / failed / 编辑后 change_id 过期）、review（high 无 ship /
      fix-first / ship 但过期）——四查按序首败即返（顺序锚定）；
    - 全过 → allow + task.complete（status=completed + 写者守卫释放 +
      journal task_completed 事件）；自持有守卫不拦查 1；
    - 新鲜度绑定 owned touched 文件（AF-01）：目录前缀 / src/** /
      嵌套 glob 内编辑或新增文件 → validation stale；high 评审后
      owned-glob 内编辑、重验不重审 → review stale；scope 外改动拦
      查 2（ownership 失败面，非新鲜度面）；仅 .glm-conductor/ 记账
      改动不过期、照常收尾；
    - 拒绝面：任务缺失 / state.json 损坏 / v2.3 遗留（携带处置指引）
      → ValueError；
    - hook 外壳（子进程）：无活跃任务静默放行；v2.3 遗留任务放行 +
      stderr 一行迁移指引；失败任务 stdout 恰单行 block JSON（状态不
      变）；全过任务经钩子完成 + 守卫释放；停泊态（waiting_quota）
      放行；corrupt / orphaned 仅 stderr 提示不拦；空串 / 非法 JSON
      stdin 容错。

全部离线：git fixture 在 tempfile.TemporaryDirectory 内（仅 git init，
unborn 基线，绝不提交、绝不触碰仓库内 .glm-conductor/ 真实账本）；
环境无 git 可执行时依赖门判定的用例自动 skipTest。仅 Python 3 标准
库（unittest + subprocess + tempfile），零第三方依赖。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "glm-conductor"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "glm-conductor" / "hooks"))

from runtime import journal, state, task, work_unit, writer_guard  # noqa: E402
import stop_gate  # noqa: E402

# 被测钩子脚本：仓库根 plugins/glm-conductor/hooks/stop_gate.py
STOP_GATE = (Path(__file__).resolve().parents[1]
             / "plugins/glm-conductor/hooks/stop_gate.py")


def run_gate(stdin_text, project_dir):
    """以子进程运行 stop_gate.py，返回 CompletedProcess。

    text=True + 显式 encoding="utf-8"：钩子按运行时契约恒以 UTF-8 字节
    写 stdout / stderr，父进程必须按 UTF-8 解码（对齐 tests/test_stop_
    gate.py 的既有做法）；ZCODE_PROJECT_DIR 指向被检仓库（钩子据此发现
    任务），其余环境变量原样继承。
    """
    return subprocess.run(
        [sys.executable, str(STOP_GATE)],
        input=stdin_text, text=True, capture_output=True,
        encoding="utf-8", errors="replace",
        env=dict(os.environ, ZCODE_PROJECT_DIR=str(project_dir)))


# —— 测试夹具 ——

class CompletionGuardCase(unittest.TestCase):
    """提供每测试隔离的临时仓库根与 v2.4 任务装置。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = self._tmp.name
        self.tid = "guard-task-a1b2c"

    # —— 装置助手 ——

    def init_git(self):
        """在临时仓库 git init（unborn 基线）；无 git 可执行时 skipTest。"""
        try:
            proc = subprocess.run(
                ["git", "init", "-q"], cwd=self.repo,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except FileNotFoundError:
            self.skipTest("环境无 git 可执行")
        if proc.returncode != 0:
            raise AssertionError(
                "测试装置 git init 失败（returncode=%d）：%s"
                % (proc.returncode,
                   proc.stderr.decode("utf-8", errors="replace")))

    def write_file(self, rel, text="内容\n"):
        """写入仓库相对路径文本文件，父目录自动创建。"""
        target = Path(self.repo) / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        return target

    def node(self, node_id="build", deps=()):
        """构造合法静态节点（经 work_unit.new_node 正门）。"""
        return work_unit.new_node(
            node_id, "实现 %s 节点" % node_id,
            depends_on=deps, ownership=("src/%s.py" % node_id,))

    def make_task(self, task_id=None, route=None, dag=None):
        """在临时仓库创建缺省活动任务，返回构造出的状态 dict。"""
        return task.create_task(
            self.repo, task_id or self.tid, "验证 v2.4 完成守卫",
            route if route is not None else {"mode": "delegate",
                                             "assurance": "standard"},
            dag if dag is not None else [self.node("build")])

    def gate_ready(self):
        """标准可过门基座：git init + 在 dag ownership 范围内落一个文件。"""
        self.init_git()
        self.write_file("src/build.py")

    def load(self, task_id=None):
        return state.load_state(self.repo, task_id or self.tid)

    def bound_root(self, task_id=None):
        """任务的生效仓库根（守卫 acquire / inspect 用同一形态）。"""
        return state.resolve_repository_root(
            self.load(task_id), self.repo)

    def events(self, task_id=None):
        """按文件顺序读取临时仓库里该任务的全部 journal 事件。"""
        return journal.read_events(self.repo, task_id or self.tid)

    def acquire_guard(self, holder, run_id="run-9", task_id=None):
        """为 holder 取得本仓库写者守卫（取任务绑定根，形态一致）。"""
        outcome = writer_guard.acquire(
            self.bound_root(task_id), holder, run_id)
        self.assertTrue(outcome["ok"])
        return outcome

    def evaluate(self, task_id=None):
        """驱动被测入口 evaluate_completion（缺省任务）。"""
        return stop_gate.evaluate_completion(
            self.repo, task_id or self.tid)


# —— 1. 查 1：writer_guard ——

class TestGuardCheck(CompletionGuardCase):

    def test_guard_held_by_other_blocks_with_holder_ids(self):
        """守卫被他人持有 → 阻断并逐字报持有者 task_id / workflow_run_id。"""
        self.gate_ready()
        self.make_task()
        self.acquire_guard("other-task", "run-9")
        result = self.evaluate()
        self.assertEqual(result["decision"], "block")
        self.assertEqual(result["check"], "writer_guard")
        self.assertEqual(result["detail"]["holder_task_id"], "other-task")
        self.assertEqual(
            result["detail"]["holder_workflow_run_id"], "run-9")
        self.assertIn("other-task", result["reason"])
        self.assertIn("run-9", result["reason"])
        # 失败路径零副作用：状态不变、守卫不被误动
        self.assertEqual(self.load()["status"], "active")
        self.assertEqual(
            writer_guard.inspect(self.bound_root())["task_id"],
            "other-task")

    def test_self_held_guard_passes_check1(self):
        """守卫被本任务持有 → 查 1 放行（完成门就是释放点），继续后续查。"""
        self.gate_ready()
        self.make_task()
        self.acquire_guard(self.tid, "run-self")
        result = self.evaluate()
        # 本任务无验证记录 → 拦在查 3（而非查 1 的持有者理由）
        self.assertEqual(result["decision"], "block")
        self.assertEqual(result["check"], "validation")
        self.assertNotIn("writer_guard", result["check"])


# —— 2. 查 2：ownership ——

class TestOwnershipCheck(CompletionGuardCase):

    def test_out_of_scope_blocks_with_node_ids(self):
        """越界路径逐条列出并带节点 id；查 2 先于查 3（顺序锚定）。"""
        self.gate_ready()
        self.write_file("stray.txt")
        self.make_task()
        result = self.evaluate()
        self.assertEqual(result["decision"], "block")
        self.assertEqual(result["check"], "ownership")
        self.assertEqual(result["detail"]["out_of_scope"], ["stray.txt"])
        self.assertEqual(result["detail"]["node_ids"], ["build"])
        self.assertIn("- stray.txt", result["reason"])
        self.assertIn("build", result["reason"])
        self.assertEqual(self.load()["status"], "active")

    def test_out_of_scope_edit_is_ownership_block_not_stale(self):
        """scope 外改动属 ownership 失败面：即使记录仍新鲜也先拦查 2。"""
        self.gate_ready()
        self.make_task()
        task.record_validation(self.repo, self.tid, "passed")
        self.write_file("stray.txt", "越界改动\n")
        result = self.evaluate()
        self.assertEqual(result["decision"], "block")
        self.assertEqual(result["check"], "ownership")
        self.assertEqual(result["detail"]["out_of_scope"], ["stray.txt"])
        # 不是查 3 的新鲜度面：不带过期分支的「验证记录已过期」话术
        self.assertNotIn("验证记录已过期", result["reason"])


# —— 3. 查 3：validation ——

class TestValidationCheck(CompletionGuardCase):

    def test_validation_missing_blocks(self):
        """无验证记录（validation.status=None）→ 阻断且理由点名验证。"""
        self.gate_ready()
        self.make_task()
        result = self.evaluate()
        self.assertEqual(result["decision"], "block")
        self.assertEqual(result["check"], "validation")
        self.assertIn("验证", result["reason"])
        self.assertIn("record_validation", result["reason"])

    def test_validation_failed_blocks(self):
        """validation.status=failed 同样不满足「passed」。"""
        self.gate_ready()
        self.make_task()
        task.record_validation(self.repo, self.tid, "failed")
        result = self.evaluate()
        self.assertEqual(result["decision"], "block")
        self.assertEqual(result["check"], "validation")
        self.assertIn("failed", result["reason"])

    def test_stale_change_id_after_edit_blocks(self):
        """编辑后 change_id 过期 → 阻断（记录与当前 change_id 都在理由里）。"""
        self.gate_ready()
        self.make_task()
        task.record_validation(self.repo, self.tid, "passed")
        self.write_file("src/build.py", "编辑后的内容\n")
        result = self.evaluate()
        self.assertEqual(result["decision"], "block")
        self.assertEqual(result["check"], "validation")
        self.assertIn("过期", result["reason"])
        self.assertIsNotNone(result["detail"]["recorded_change_id"])
        self.assertNotEqual(
            result["detail"]["recorded_change_id"],
            result["detail"]["current_change_id"])

    def gate_with_scope(self, ownership, rel, text="初版内容\n"):
        """指定 scope 形状的可过门基座：git init + owned 文件 + passed 验证。"""
        self.init_git()
        self.write_file(rel, text)
        node = work_unit.new_node(
            "build", "实现 build 节点", ownership=ownership)
        self.make_task(dag=[node])
        task.record_validation(self.repo, self.tid, "passed")

    def assert_validation_stale_block(self):
        """断言当前求值拦在查 3 的过期分支（decision/check/理由/两侧 id）。"""
        result = self.evaluate()
        self.assertEqual(result["decision"], "block")
        self.assertEqual(result["check"], "validation")
        self.assertIn("过期", result["reason"])
        self.assertNotEqual(
            result["detail"]["recorded_change_id"],
            result["detail"]["current_change_id"])

    def test_stale_after_directory_prefix_edit_blocks(self):
        """目录前缀 scope（src/auth）内编辑 → validation stale 阻断。"""
        self.gate_with_scope(("src/auth",), "src/auth/login.py")
        self.write_file("src/auth/login.py", "编辑后的内容\n")
        self.assert_validation_stale_block()

    def test_stale_after_owned_glob_edit_blocks(self):
        """glob scope（src/**）内编辑 → stale 阻断（scope 不再被当字面路径哈希）。"""
        self.gate_with_scope(("src/**",), "src/parser/core.py")
        self.write_file("src/parser/core.py", "编辑后的内容\n")
        self.assert_validation_stale_block()

    def test_stale_after_owned_glob_new_file_blocks(self):
        """glob（src/**）下新增 owned 文件（文件集变化）→ validation stale 阻断。"""
        self.gate_with_scope(("src/**",), "src/parser/core.py")
        self.write_file("src/parser/extra.py", "新增文件\n")
        self.assert_validation_stale_block()

    def test_stale_after_nested_glob_edit_blocks(self):
        """嵌套 glob（a/**/b）内文件编辑 → validation stale 阻断。"""
        self.gate_with_scope(("a/**/b",), "a/x/y/b")
        self.write_file("a/x/y/b", "编辑后的内容\n")
        self.assert_validation_stale_block()


# —— 4. 查 4：review（仅 high 保障） ——

class TestReviewCheck(CompletionGuardCase):

    def high_ready(self):
        """high 保障 + 通过且新鲜的验证记录（查 4 的前置基座）。"""
        self.gate_ready()
        self.make_task(route={"mode": "full", "assurance": "high"})
        task.record_validation(self.repo, self.tid, "passed")

    def test_high_without_review_blocks(self):
        """high 无 ship 评审（review.verdict=None）→ 阻断且理由点名 ship。"""
        self.high_ready()
        result = self.evaluate()
        self.assertEqual(result["decision"], "block")
        self.assertEqual(result["check"], "review")
        self.assertIn("ship", result["reason"])
        self.assertIn("评审", result["reason"])

    def test_high_with_fix_first_blocks(self):
        """fix-first 永不满足完成（词汇内但非 ship）。"""
        self.high_ready()
        task.record_review(self.repo, self.tid, "glm-reviewer", "fix-first")
        result = self.evaluate()
        self.assertEqual(result["decision"], "block")
        self.assertEqual(result["check"], "review")
        self.assertIn("fix-first", result["reason"])

    def test_high_review_stale_blocks(self):
        """评审后相关文件又有变化（验证已刷新、评审未刷新）→ 评审过期阻断。"""
        self.high_ready()
        task.record_review(self.repo, self.tid, "glm-reviewer", "ship")
        self.write_file("src/build.py", "评审后编辑\n")
        # 刷新验证（查 3 重新新鲜），评审未刷新 → 拦在查 4 的过期分支
        task.record_validation(self.repo, self.tid, "passed")
        result = self.evaluate()
        self.assertEqual(result["decision"], "block")
        self.assertEqual(result["check"], "review")
        self.assertIn("过期", result["reason"])

    def test_high_review_stale_blocks_after_owned_glob_edit(self):
        """high：owned glob（src/**）内编辑后重验不重审 → review stale 阻断。

        重录 validation 后查 3 重新新鲜（记录侧与守卫侧同调
        relevant_changed_files 的直接证明），拦在查 4 的过期分支。
        """
        self.init_git()
        self.write_file("src/parser/core.py")
        node = work_unit.new_node(
            "build", "实现 build 节点", ownership=("src/**",))
        self.make_task(route={"mode": "full", "assurance": "high"},
                       dag=[node])
        task.record_validation(self.repo, self.tid, "passed")
        task.record_review(self.repo, self.tid, "glm-reviewer", "ship")
        self.write_file("src/parser/core.py", "评审后编辑\n")
        task.record_validation(self.repo, self.tid, "passed")
        result = self.evaluate()
        self.assertEqual(result["decision"], "block")
        self.assertEqual(result["check"], "review")
        self.assertIn("过期", result["reason"])

    def test_standard_assurance_skips_review(self):
        """standard 保障无独立审查义务：无 review 记录也不拦查 4。"""
        self.gate_ready()
        self.make_task(route={"mode": "delegate", "assurance": "standard"})
        task.record_validation(self.repo, self.tid, "passed")
        result = self.evaluate()
        self.assertEqual(result["decision"], "allow")


# —— 5. 全过 → completed + 守卫释放 ——

class TestCompletionSuccess(CompletionGuardCase):

    def test_all_pass_completes_and_releases_guard(self):
        """四查全过 → allow + completed + 守卫释放 + task_completed 事件。"""
        self.gate_ready()
        self.make_task()
        self.acquire_guard(self.tid, "run-self")
        task.record_validation(self.repo, self.tid, "passed")
        result = self.evaluate()
        self.assertEqual(
            result, {"decision": "allow", "check": None, "detail": None,
                     "reason": None})
        self.assertEqual(self.load()["status"], "completed")
        self.assertIsNone(writer_guard.inspect(self.bound_root()))
        self.assertEqual(
            [item.get("event") for item in self.events()],
            ["route_selected", "validation_recorded", "task_completed"])
        self.assertEqual(self.events()[-1]["to"], "completed")

    def test_all_pass_high_with_ship_review_completes(self):
        """high 保障 + 新鲜 ship 评审 → 同样放行收尾。"""
        self.gate_ready()
        self.make_task(route={"mode": "full", "assurance": "high"})
        task.record_validation(self.repo, self.tid, "passed")
        task.record_review(self.repo, self.tid, "glm-reviewer", "ship")
        result = self.evaluate()
        self.assertEqual(result["decision"], "allow")
        self.assertEqual(self.load()["status"], "completed")
        self.assertEqual(self.events()[-1]["event"], "task_completed")

    def test_bookkeeping_only_change_still_completes(self):
        """仅 .glm-conductor/ 记账改动 → 不入相关文件集、记录不过期 → 照常收尾。"""
        self.gate_ready()
        self.make_task()
        self.acquire_guard(self.tid, "run-self")
        task.record_validation(self.repo, self.tid, "passed")
        self.write_file(".glm-conductor/notes/cache.json", "{}\n")
        result = self.evaluate()
        self.assertEqual(
            result, {"decision": "allow", "check": None, "detail": None,
                     "reason": None})
        self.assertEqual(self.load()["status"], "completed")
        self.assertIsNone(writer_guard.inspect(self.bound_root()))


# —— 6. 拒绝面 ——

class TestEvaluateRefusals(CompletionGuardCase):

    def test_missing_task_raises(self):
        """任务不存在 → ValueError（完成门无法评估）。"""
        with self.assertRaises(ValueError):
            stop_gate.evaluate_completion(self.repo, "no-such-task")

    def test_corrupt_state_raises(self):
        """state.json JSON 损坏 → ValueError（load_state 不静默）。"""
        path = state.state_path(self.repo, "corrupt-task")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{不是 JSON", encoding="utf-8")
        with self.assertRaises(ValueError):
            stop_gate.evaluate_completion(self.repo, "corrupt-task")

    def test_legacy_task_raises_with_guidance(self):
        """v2.3 遗留任务 → ValueError 携带一行处置指引（不假装能判完成）。"""
        payload = {
            "task_id": "legacy-23-task",
            "goal": "v2.3 遗留任务",
            "route": {"mode": "delegate", "delegability": "high"},
            "status": "executing",
            "work_units": [{"id": "u1", "status": "executing",
                            "lease": {"holder": "main"}}],
        }
        path = state.state_path(self.repo, "legacy-23-task")
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        with self.assertRaises(ValueError) as ctx:
            stop_gate.evaluate_completion(self.repo, "legacy-23-task")
        self.assertIn("2.3", str(ctx.exception))
        self.assertIn("收尾", str(ctx.exception))


# —— 7. hook 外壳（子进程冒烟） ——

class TestHookShell(CompletionGuardCase):
    """驱动 hooks/stop_gate.py 子进程（ZCODE_PROJECT_DIR 指向临时仓库）。"""

    def test_no_active_task_silent_allow(self):
        """无活跃 conductor 任务 → 静默放行（退出码 0，stdout 全空）。"""
        self.write_file("src/build.py")  # 仓库有文件但无任务
        proc = run_gate("{}", self.repo)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, "")

    def test_legacy_task_allowed_with_guidance(self):
        """v2.3 遗留任务 → 放行 + stderr 一行迁移指引（stdout 无决策）。"""
        payload = {
            "task_id": "legacy-23-task",
            "goal": "v2.3 遗留任务",
            "route": {"mode": "delegate", "delegability": "high"},
            "status": "executing",
            "work_units": [{"id": "u1", "status": "executing",
                            "lease": {"holder": "main"}}],
        }
        path = state.state_path(self.repo, "legacy-23-task")
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        proc = run_gate("{}", self.repo)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, "")
        self.assertIn("2.3", proc.stderr)
        self.assertIn("收尾", proc.stderr)

    def test_corrupt_task_stderr_only(self):
        """corrupt state.json → 仅 stderr 提示（完成门跳过），不拦会话。"""
        path = state.state_path(self.repo, "corrupt-task")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{不是 JSON", encoding="utf-8")
        proc = run_gate("{}", self.repo)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, "")
        self.assertIn("完成门跳过", proc.stderr)

    def test_parked_task_allowed_without_validation(self):
        """停泊态（waiting_quota）是有意停止点 → 放行，不要求验证。"""
        self.gate_ready()
        self.make_task()
        task.enter_waiting_quota(self.repo, self.tid)
        proc = run_gate("{}", self.repo)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, "")
        self.assertEqual(self.load()["status"], "waiting_quota")

    def test_failing_task_blocks_single_line_json(self):
        """失败任务 → stdout 恰单行 block JSON（中文理由），状态不变。"""
        self.gate_ready()
        self.make_task()  # 无验证记录 → 拦在查 3
        proc = run_gate("", self.repo)
        self.assertEqual(proc.returncode, 0)
        lines = proc.stdout.splitlines()
        self.assertEqual(len(lines), 1)
        decision = json.loads(lines[0])
        self.assertEqual(decision["decision"], "block")
        self.assertIn("验证", decision["reason"])
        self.assertIn("record_validation", decision["reason"])
        self.assertEqual(self.load()["status"], "active")

    def test_all_pass_completes_via_hook(self):
        """全过任务经钩子就地完成：stdout 全空 + completed + 守卫释放。"""
        self.gate_ready()
        self.make_task()
        self.acquire_guard(self.tid, "run-self")
        task.record_validation(self.repo, self.tid, "passed")
        proc = run_gate("{}", self.repo)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, "")
        self.assertEqual(self.load()["status"], "completed")
        self.assertIsNone(writer_guard.inspect(self.bound_root()))
        self.assertEqual(self.events()[-1]["event"], "task_completed")

    def test_stdin_tolerance(self):
        """stdin 空串 / 非法 JSON → 按空对象容错，照常求值与拦截。"""
        self.gate_ready()
        self.make_task()
        for stdin_text in ("", "不是 JSON"):
            proc = run_gate(stdin_text, self.repo)
            self.assertEqual(proc.returncode, 0)
            decision = json.loads(proc.stdout.splitlines()[0])
            self.assertEqual(decision["decision"], "block")


if __name__ == "__main__":
    unittest.main()
