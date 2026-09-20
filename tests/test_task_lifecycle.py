#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""runtime.task 任务生命周期层单元测试（Phase 2 工作包 P2-A/C / 单元 W3）。

运行：
    cd <repo_root> && python3 -X utf8 -m unittest tests.test_task_lifecycle -v

覆盖（W3 单元规格六类最低集 + 补充）：
    - create/load 往返：全字段往返全等（route 恰存 mode/assurance、
      validation/review 全 null 初值、quota_resume 占位块）；重复创建
      拒绝；route / dag / task_id 非法拒绝；缺失读取 None；
    - record_validation 存 change_id：与 change_id.compute_change_id
      独立重算全等且确定性重放全等；passed/failed 词汇强制；
      summary / commands 形状强制；
    - record_review 顺序规则强制：先 validation 后 review（无验证记录
      / 手工 validation 缺 change_id 均拒绝）；verdict / reviewer /
      findings 形状强制；
    - 合法状态转换集合：TASK_TRANSITIONS 矩阵驱动（合法链逐站断言；
      矩阵外迁移 / 词汇外状态 / 终态任何变化拒绝）；phase 标注与
      清除；终态冻结（记录 / 标注 / run 关联一律拒绝）；
    - complete 释放写者守卫：complete / fail / cancel 三终态均释放
      （writer_guard.inspect 归 None）；他任务持有的守卫绝不误删；
      无守卫幂等；同终态重放安全；
    - journal 词汇精确：TASK_JOURNAL_EVENTS 恰十名（Phase 3 Q1 恰增
      quota_resume_confirmed）；全生命周期事件名
      逐条不多不少落在词汇内，无宿主子代理生命周期词汇；
    - record_workflow_run：state + adapter.record_run 双镜像 +
      workflow_started 事件；非法 id / 终态拒绝；
    - v2.3 遗留任务：load_task 与全部变更入口拒绝并携带处置指引。

全部离线：git fixture 在 tempfile.TemporaryDirectory 内（仅 git init，
unborn 基线 "-"，绝不提交、绝不触碰仓库内 .glm-conductor/ 真实账本）；
环境无 git 可执行时依赖 change_id 的用例自动 skipTest。adapter 的
RUNS_DIR 每测试注入临时目录（adapter 模块文档化的注入点），绝不污染
真实仓库工作目录。仅 Python 3 标准库（unittest + tempfile），零第三方
依赖。
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "glm-conductor"))

from runtime import change_id, journal, state, task, work_unit, writer_guard  # noqa: E402
from runtime.workflow import adapter as workflow_adapter  # noqa: E402


# 变更标识合法格式："sha256:" + 64 位小写十六进制
_CHANGE_ID_RE = re.compile("^sha256:[0-9a-f]{64}$")

# 宿主子代理生命周期词汇黑名单（v2.4 任务级账本绝不镜像的 v2.3 遗留事件）
_HOST_CHILD_TERMS = (
    "implementation_started", "dispatch_prepared", "dispatch_aborted",
    "lease_recovered", "unit_finished", "reviewer_invoked",
    "status_changed", "task_created")


# —— 测试夹具 ——

class LifecycleCase(unittest.TestCase):
    """提供每测试隔离的临时仓库根 + adapter RUNS_DIR 注入。"""

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

    def node(self, node_id="build", deps=()):
        """构造合法静态节点（经 work_unit.new_node 正门）。"""
        return work_unit.new_node(
            node_id, "实现 %s 节点" % node_id,
            depends_on=deps, ownership=("src/%s.py" % node_id,))

    def make_task(self, task_id="life-cycle-a", route=None, dag=None):
        """在临时仓库创建缺省活动任务，返回构造出的状态 dict。"""
        return task.create_task(
            self.repo, task_id, "验证 v2.4 任务生命周期层",
            route if route is not None else {"mode": "delegate",
                                             "assurance": "high"},
            dag if dag is not None else [self.node("build")])

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

    def load(self, task_id="life-cycle-a"):
        return state.load_state(self.repo, task_id)

    def events(self, task_id="life-cycle-a"):
        """按文件顺序读取临时仓库里该任务的全部 journal 事件。"""
        return journal.read_events(self.repo, task_id)

    def names(self, task_id="life-cycle-a"):
        return [item.get("event") for item in self.events(task_id)]


# —— 1. create/load 往返 ——

class TestCreateLoadRoundtrip(LifecycleCase):

    def test_roundtrip_full(self):
        """创建 → 落盘 → 读取全等；route 恰存 mode/assurance；初值块齐全。"""
        dag = [self.node("build"), self.node("check", ("build",))]
        st = self.make_task(
            route={"mode": "delegate", "assurance": "high"}, dag=dag)
        loaded = self.load()
        self.assertEqual(loaded, st)
        # route 块恰两键：mode 必存，assurance 词汇内一并存入
        self.assertEqual(loaded["route"],
                         {"mode": "delegate", "assurance": "high"})
        # 七态起点与可空 run 关联
        self.assertEqual(loaded["status"], "active")
        self.assertIsNone(loaded["workflow_run_id"])
        # validation / review 全 null 未记录初值（P2-C 四键形状）
        self.assertEqual(loaded["validation"],
                         {"status": None, "change_id": None,
                          "summary": None, "commands": None})
        self.assertEqual(loaded["review"],
                         {"reviewer": None, "verdict": None,
                          "change_id": None, "findings": None})
        # quota_resume 占位块（Phase 3 定稿前的冻结初始形状）
        self.assertEqual(loaded["quota_resume"], state.default_quota_resume())
        # dag 原样持久化；仓库根绑定（RB-2）为归一绝对路径
        self.assertEqual(loaded["dag"], dag)
        self.assertEqual(loaded["repository"]["root"],
                         str(Path(os.path.abspath(self.repo)).resolve()))
        # 恰一条 route_selected 事件（含 mode / assurance）
        self.assertEqual(self.names(), ["route_selected"])
        event = self.events()[0]
        self.assertEqual(event["mode"], "delegate")
        self.assertEqual(event["assurance"], "high")

    def test_duplicate_create_refused(self):
        """任务已存在（盘上有 state.json）→ 拒绝重建，原账本不被覆盖。"""
        self.make_task()
        with self.assertRaises(ValueError):
            self.make_task(route={"mode": "solo"})
        loaded = self.load()
        self.assertEqual(loaded["route"]["mode"], "delegate")
        self.assertEqual(self.names(), ["route_selected"])

    def test_invalid_route_refused(self):
        """route.mode 词汇外 / route.assurance 词汇外 → 拒绝且零落盘。"""
        with self.assertRaises(ValueError):
            self.make_task(route={"mode": "pair"})
        with self.assertRaises(ValueError):
            self.make_task(route={"mode": "solo", "assurance": "extreme"})
        self.assertIsNone(self.load())

    def test_invalid_dag_refused(self):
        """重复节点 id（整图错误）→ create 拒绝且错误可读。"""
        with self.assertRaises(ValueError) as ctx:
            self.make_task(dag=[self.node("build"), self.node("build")])
        self.assertIn("dag", str(ctx.exception))
        self.assertIsNone(self.load())

    def test_invalid_task_id_refused(self):
        """task_id 不匹配格式（下划线开头）→ 拒绝。"""
        with self.assertRaises(ValueError):
            self.make_task(task_id="_bad-id")
        self.assertIsNone(self.load("_bad-id"))

    def test_load_missing_none(self):
        """无 state.json → load_task 返回 None（不抛）。"""
        self.assertIsNone(task.load_task(self.repo, "no-such-task"))


# —— 2. 委派 run 关联（state + adapter 双镜像） ——

class TestWorkflowRunMirror(LifecycleCase):

    def test_record_run_mirrors_state_and_adapter(self):
        """record_workflow_run：state 落盘 + adapter 单据 + workflow_started 事件。"""
        self.make_task()
        st = task.record_workflow_run(self.repo, "life-cycle-a", "run-123")
        self.assertEqual(st["workflow_run_id"], "run-123")
        self.assertEqual(self.load()["workflow_run_id"], "run-123")
        record = workflow_adapter.load_run("life-cycle-a")
        self.assertIsNotNone(record)
        self.assertEqual(record["task_ref"], "life-cycle-a")
        self.assertEqual(record["workflow_run_id"], "run-123")
        self.assertEqual(self.names(),
                         ["route_selected", "workflow_started"])
        self.assertEqual(
            self.events()[-1]["workflow_run_id"], "run-123")

    def test_record_run_reruns_replace(self):
        """重复记录 → adapter 关联整条替换（一任务一活跃 run 口径）。"""
        self.make_task()
        task.record_workflow_run(self.repo, "life-cycle-a", "run-1")
        task.record_workflow_run(self.repo, "life-cycle-a", "run-2")
        self.assertEqual(self.load()["workflow_run_id"], "run-2")
        self.assertEqual(
            workflow_adapter.load_run("life-cycle-a")["workflow_run_id"],
            "run-2")

    def test_record_run_invalid_refused(self):
        """workflow_run_id 空串 / 非 str → 拒绝且 state 零改动。"""
        self.make_task()
        for bad in ("", None, 123):
            with self.assertRaises(ValueError):
                task.record_workflow_run(self.repo, "life-cycle-a", bad)
        self.assertIsNone(self.load()["workflow_run_id"])

    def test_record_run_terminal_refused(self):
        """终态任务冻结：completed 后拒绝关联新 run。"""
        self.make_task()
        task.complete(self.repo, "life-cycle-a")
        with self.assertRaises(ValueError):
            task.record_workflow_run(self.repo, "life-cycle-a", "run-9")
        self.assertIsNone(self.load()["workflow_run_id"])


# —— 3. 任务级验证记录（change_id） ——

class TestValidationRecords(LifecycleCase):

    def test_validation_stores_change_id(self):
        """record_validation 存 change_id，且与独立重算全等。"""
        self.init_git()
        self.write_file("src/build.py")
        self.make_task(dag=[self.node("build")])
        st = task.record_validation(
            self.repo, "life-cycle-a", "passed",
            summary="主验证通过",
            commands=["python3 -X utf8 -m unittest tests.test_task_lifecycle"])
        validation = st["validation"]
        self.assertEqual(validation["status"], "passed")
        self.assertRegex(validation["change_id"], _CHANGE_ID_RE)
        # 与 change_id.compute_change_id 独立重算全等（同一相关路径派生）
        self.assertEqual(
            validation["change_id"],
            change_id.compute_change_id(self.repo, ["src/build.py"]))
        self.assertEqual(validation["summary"], "主验证通过")
        self.assertEqual(
            validation["commands"],
            ["python3 -X utf8 -m unittest tests.test_task_lifecycle"])
        self.assertEqual(self.load()["validation"], validation)
        # validation_recorded 事件携带 status 与 change_id
        self.assertEqual(self.names(),
                         ["route_selected", "validation_recorded"])
        self.assertEqual(self.events()[-1]["change_id"],
                         validation["change_id"])

    def test_validation_deterministic_replay(self):
        """同树重复记录 → change_id 全等（确定性）。"""
        self.init_git()
        self.write_file("src/build.py")
        self.make_task(dag=[self.node("build")])
        first = task.record_validation(self.repo, "life-cycle-a", "failed")
        second = task.record_validation(self.repo, "life-cycle-a", "passed")
        self.assertEqual(first["validation"]["change_id"],
                         second["validation"]["change_id"])
        self.assertEqual(second["validation"]["status"], "passed")

    def test_validation_vocabulary_and_shapes(self):
        """status 词汇强制；summary / commands 形状强制；空 commands 合法。"""
        self.init_git()
        self.make_task()
        with self.assertRaises(ValueError):
            task.record_validation(self.repo, "life-cycle-a", "ok")
        with self.assertRaises(ValueError):
            task.record_validation(self.repo, "life-cycle-a", "passed",
                                   summary=123)
        with self.assertRaises(ValueError):
            task.record_validation(self.repo, "life-cycle-a", "passed",
                                   commands="python3 -m unittest")
        with self.assertRaises(ValueError):
            task.record_validation(self.repo, "life-cycle-a", "passed",
                                   commands=["python3", 1])
        st = task.record_validation(self.repo, "life-cycle-a", "failed",
                                    commands=())
        self.assertEqual(st["validation"]["commands"], [])
        self.assertEqual(st["validation"]["status"], "failed")

    def test_validation_missing_task_refused(self):
        """任务不存在 → 拒绝。"""
        with self.assertRaises(ValueError):
            task.record_validation(self.repo, "ghost-task", "passed")


# —— 4. 审查记录与顺序规则 ——

class TestReviewOrdering(LifecycleCase):

    def test_review_before_validation_refused(self):
        """顺序规则：无 validation 记录 → 拒绝（先验证后评审）。"""
        self.make_task()
        with self.assertRaises(ValueError) as ctx:
            task.record_review(self.repo, "life-cycle-a", "glm-reviewer",
                               "ship")
        self.assertIn("validation", str(ctx.exception))
        self.assertNotIn("review_recorded", self.names())
        self.assertIsNone(self.load()["review"]["verdict"])

    def test_review_handmade_validation_without_change_id_refused(self):
        """手工 validation（有 status 无 change_id）≠ 完整记录 → 仍拒绝。"""
        self.make_task()
        st = self.load()
        st["validation"]["status"] = "passed"
        st["validation"]["change_id"] = None
        state.save_state(self.repo, st)
        with self.assertRaises(ValueError):
            task.record_review(self.repo, "life-cycle-a", "glm-reviewer",
                               "ship")

    def test_review_after_validation_ok(self):
        """先 validation 后 review：记录落盘 + change_id 现算 + 事件。"""
        self.init_git()
        self.write_file("src/build.py")
        self.make_task(dag=[self.node("build")])
        task.record_validation(self.repo, "life-cycle-a", "passed")
        st = task.record_review(self.repo, "life-cycle-a", "glm-reviewer",
                                "ship", findings="无阻塞发现")
        review = st["review"]
        self.assertEqual(review, {
            "reviewer": "glm-reviewer", "verdict": "ship",
            "change_id": review["change_id"], "findings": "无阻塞发现"})
        self.assertRegex(review["change_id"], _CHANGE_ID_RE)
        self.assertEqual(
            review["change_id"],
            change_id.compute_change_id(self.repo, ["src/build.py"]))
        self.assertEqual(self.load()["review"], review)
        self.assertEqual(
            self.names(),
            ["route_selected", "validation_recorded", "review_recorded"])
        self.assertEqual(self.events()[-1]["verdict"], "ship")

    def test_review_shapes_refused(self):
        """verdict 词汇 / reviewer 空串 / findings 形状 → 逐个拒绝。"""
        self.init_git()
        self.make_task()
        task.record_validation(self.repo, "life-cycle-a", "passed")
        with self.assertRaises(ValueError):
            task.record_review(self.repo, "life-cycle-a", "glm-reviewer",
                               "not-required")
        with self.assertRaises(ValueError):
            task.record_review(self.repo, "life-cycle-a", "", "ship")
        with self.assertRaises(ValueError):
            task.record_review(self.repo, "life-cycle-a", "glm-reviewer",
                               "ship", findings=["a"])
        self.assertIsNone(self.load()["review"]["verdict"])


# —— 5. 合法状态转换集合 ——

class TestStatusTransitions(LifecycleCase):

    def test_legal_walk(self):
        """合法链逐站断言：active→waiting_quota→active→blocked→active→waiting_user→failed。"""
        self.make_task()
        self.assertEqual(self.load()["status"], "active")
        task.enter_waiting_quota(self.repo, "life-cycle-a")
        self.assertEqual(self.load()["status"], "waiting_quota")
        task.exit_waiting_quota(self.repo, "life-cycle-a")
        self.assertEqual(self.load()["status"], "active")
        task.set_status(self.repo, "life-cycle-a", "blocked")
        self.assertEqual(self.load()["status"], "blocked")
        task.set_status(self.repo, "life-cycle-a", "active")
        task.set_status(self.repo, "life-cycle-a", "waiting_user")
        self.assertEqual(self.load()["status"], "waiting_user")
        task.fail(self.repo, "life-cycle-a")
        self.assertEqual(self.load()["status"], "failed")
        self.assertEqual(
            self.names(),
            ["route_selected", "waiting_quota", "waiting_quota",
             "task_failed"])
        self.assertEqual(self.events()[1]["direction"], "enter")
        self.assertEqual(self.events()[2]["direction"], "exit")
        self.assertEqual(self.events()[3]["from"], "waiting_user")

    def test_matrix_external_transitions_refused(self):
        """矩阵外迁移逐个拒绝：waiting_user→waiting_quota 等。"""
        self.make_task()
        task.set_status(self.repo, "life-cycle-a", "waiting_user")
        with self.assertRaises(ValueError):
            task.set_status(self.repo, "life-cycle-a", "waiting_quota")
        with self.assertRaises(ValueError):
            task.complete(self.repo, "life-cycle-a")  # waiting_user→completed 矩阵外
        self.assertEqual(self.load()["status"], "waiting_user")

    def test_terminal_transitions_refused(self):
        """终态不得静默重开：completed 后任何状态变化拒绝。"""
        self.make_task()
        task.complete(self.repo, "life-cycle-a")
        with self.assertRaises(ValueError):
            task.set_status(self.repo, "life-cycle-a", "active")
        with self.assertRaises(ValueError):
            task.fail(self.repo, "life-cycle-a")
        with self.assertRaises(ValueError):
            task.cancel(self.repo, "life-cycle-a")
        with self.assertRaises(ValueError):
            task.enter_waiting_quota(self.repo, "life-cycle-a")
        self.assertEqual(self.load()["status"], "completed")

    def test_status_vocabulary_refused(self):
        """词汇外 status（v2.3 阶段态）→ 拒绝。"""
        self.make_task()
        with self.assertRaises(ValueError):
            task.set_status(self.repo, "life-cycle-a", "executing")
        self.assertEqual(self.load()["status"], "active")

    def test_set_phase(self):
        """phase 标注设置 / 清除 / 词汇外拒绝；无 journal 事件。"""
        self.make_task()
        task.set_phase(self.repo, "life-cycle-a", "validating")
        self.assertEqual(self.load()["phase"], "validating")
        task.set_phase(self.repo, "life-cycle-a", None)
        self.assertNotIn("phase", self.load())
        with self.assertRaises(ValueError):
            task.set_phase(self.repo, "life-cycle-a", "shipping")
        self.assertEqual(self.names(), ["route_selected"])

    def test_terminal_freeze_on_records(self):
        """终态冻结：completed 后记录 / 标注 / run 关联一律拒绝。"""
        self.init_git()
        self.make_task()
        task.complete(self.repo, "life-cycle-a")
        with self.assertRaises(ValueError):
            task.record_validation(self.repo, "life-cycle-a", "passed")
        with self.assertRaises(ValueError):
            task.record_review(self.repo, "life-cycle-a", "glm-reviewer",
                               "ship")
        with self.assertRaises(ValueError):
            task.set_phase(self.repo, "life-cycle-a", "reviewing")
        with self.assertRaises(ValueError):
            task.record_workflow_run(self.repo, "life-cycle-a", "run-x")


# —— 6. 终态收尾与写者守卫释放 ——

class TestTerminalAndGuard(LifecycleCase):

    def test_complete_releases_writer_guard(self):
        """complete：终态 + task_completed 事件 + 写者守卫释放。"""
        self.make_task()
        outcome = writer_guard.acquire(self.repo, "life-cycle-a", "run-1")
        self.assertTrue(outcome["ok"])
        self.assertIsNotNone(writer_guard.inspect(self.repo))
        st = task.complete(self.repo, "life-cycle-a")
        self.assertEqual(st["status"], "completed")
        self.assertEqual(self.load()["status"], "completed")
        self.assertIsNone(writer_guard.inspect(self.repo))
        self.assertEqual(self.names(),
                         ["route_selected", "task_completed"])
        self.assertEqual(self.events()[-1]["from"], "active")
        self.assertEqual(self.events()[-1]["to"], "completed")

    def test_fail_and_cancel_release_writer_guard(self):
        """fail / cancel：各自终态与事件，同样释放写者守卫。"""
        self.make_task(task_id="life-fail-b")
        writer_guard.acquire(self.repo, "life-fail-b", "run-2")
        task.fail(self.repo, "life-fail-b")
        self.assertEqual(self.load("life-fail-b")["status"], "failed")
        self.assertIsNone(writer_guard.inspect(self.repo))
        self.make_task(task_id="life-cancel-c")
        writer_guard.acquire(self.repo, "life-cancel-c", "run-3")
        task.cancel(self.repo, "life-cancel-c")
        self.assertEqual(self.load("life-cancel-c")["status"], "cancelled")
        self.assertIsNone(writer_guard.inspect(self.repo))
        self.assertIn("task_failed", self.names("life-fail-b"))
        self.assertIn("task_cancelled", self.names("life-cancel-c"))

    def test_guard_of_other_task_untouched(self):
        """守卫属其它任务 → release 拒绝且不误删（零写入）。"""
        writer_guard.acquire(self.repo, "guard-holder", "run-0")
        self.make_task()
        task.complete(self.repo, "life-cycle-a")
        holder = writer_guard.inspect(self.repo)
        self.assertIsNotNone(holder)
        self.assertEqual(holder["task_id"], "guard-holder")

    def test_complete_without_guard_idempotent(self):
        """无守卫（缺失 / 已归零）→ complete 幂等成功。"""
        self.make_task()
        st = task.complete(self.repo, "life-cycle-a")
        self.assertEqual(st["status"], "completed")
        self.assertIsNone(writer_guard.inspect(self.repo))

    def test_complete_same_terminal_replay(self):
        """同终态重放放行：事件幂等重做，状态不再变化；跨终态拒绝。"""
        self.make_task()
        task.complete(self.repo, "life-cycle-a")
        task.complete(self.repo, "life-cycle-a")
        self.assertEqual(self.load()["status"], "completed")
        self.assertEqual(self.names().count("task_completed"), 2)
        with self.assertRaises(ValueError):
            task.fail(self.repo, "life-cycle-a")


# —— 7. journal 词汇精确 ——

class TestJournalVocabulary(LifecycleCase):

    def test_vocabulary_constant_exact(self):
        """TASK_JOURNAL_EVENTS 恰十名，不多不少、无宿主子代理词汇。"""
        self.assertEqual(task.TASK_JOURNAL_EVENTS, (
            "route_selected", "workflow_started", "workflow_reassessed",
            "validation_recorded", "review_recorded", "waiting_quota",
            "quota_resume_confirmed",
            "task_completed", "task_failed", "task_cancelled"))
        self.assertEqual(len(set(task.TASK_JOURNAL_EVENTS)), 10)
        for term in _HOST_CHILD_TERMS:
            self.assertNotIn(term, task.TASK_JOURNAL_EVENTS)

    def test_full_lifecycle_events_exact(self):
        """全生命周期事件名逐条不多不少落在词汇内（顺序确定）。"""
        self.init_git()
        self.write_file("src/build.py")
        self.make_task(dag=[self.node("build")])
        task.record_workflow_run(self.repo, "life-cycle-a", "run-42")
        task.enter_waiting_quota(self.repo, "life-cycle-a")
        task.exit_waiting_quota(self.repo, "life-cycle-a")
        task.record_validation(self.repo, "life-cycle-a", "passed")
        task.record_review(self.repo, "life-cycle-a", "glm-reviewer", "ship")
        task.complete(self.repo, "life-cycle-a")
        expected = ["route_selected", "workflow_started", "waiting_quota",
                    "waiting_quota", "validation_recorded", "review_recorded",
                    "task_completed"]
        self.assertEqual(self.names(), expected)
        vocabulary = set(task.TASK_JOURNAL_EVENTS)
        for name in self.names():
            self.assertIn(name, vocabulary)
        for term in _HOST_CHILD_TERMS:
            self.assertNotIn(term, self.names())

    def test_events_without_git_stay_in_vocabulary(self):
        """无 git 环境（跳过 change_id 用例）下事件词汇同样不出表。"""
        self.make_task()
        task.enter_waiting_quota(self.repo, "life-cycle-a")
        task.cancel(self.repo, "life-cycle-a")
        for name in self.names():
            self.assertIn(name, set(task.TASK_JOURNAL_EVENTS))


# —— 8. v2.3 遗留任务拒绝 ——

class TestLegacyRefusal(LifecycleCase):

    def setUp(self):
        super().setUp()
        # 绕过 save_state 直接落盘原始 v2.3 遗留 JSON（同 test_state_v24 手法）
        payload = {
            "task_id": "legacy-23-task",
            "goal": "v2.3 遗留任务",
            "route": {"mode": "delegate", "delegability": "high",
                      "executor": "flash-implementer"},
            "status": "executing",
            "work_units": [{"id": "u1", "status": "executing",
                            "lease": {"holder": "main"}}],
        }
        path = state.state_path(self.repo, "legacy-23-task")
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)

    def test_load_task_refuses_legacy_with_guidance(self):
        """load_task：遗留检测透传 → ValueError 携带处置指引，绝不当作 v2.4 返回。"""
        with self.assertRaises(ValueError) as ctx:
            task.load_task(self.repo, "legacy-23-task")
        self.assertIn("2.3", str(ctx.exception))
        self.assertIn("收尾", str(ctx.exception))

    def test_mutations_refuse_legacy(self):
        """全部变更入口对遗留任务拒绝（先于任何写盘）。"""
        for call in (
                lambda: task.record_validation(
                    self.repo, "legacy-23-task", "passed"),
                lambda: task.record_review(
                    self.repo, "legacy-23-task", "glm-reviewer", "ship"),
                lambda: task.record_workflow_run(
                    self.repo, "legacy-23-task", "run-1"),
                lambda: task.set_status(self.repo, "legacy-23-task", "active"),
                lambda: task.set_phase(self.repo, "legacy-23-task", "workflow"),
                lambda: task.enter_waiting_quota(self.repo, "legacy-23-task"),
                lambda: task.complete(self.repo, "legacy-23-task"),
                lambda: task.fail(self.repo, "legacy-23-task"),
                lambda: task.cancel(self.repo, "legacy-23-task")):
            with self.assertRaises(ValueError):
                call()
        with self.assertRaises(ValueError):
            task.create_task(self.repo, "legacy-23-task", "重建",
                             {"mode": "solo"})


# —— 9. 相关路径派生（W4 共用） ——

class TestRelevantPaths(LifecycleCase):

    def test_union_dedup_sorted(self):
        """dag 全节点 ownership 并集：去重 + 排序。"""
        node_a = work_unit.new_node("a", "A", ownership=("b.py", "a.py"))
        node_b = work_unit.new_node("b", "B", depends_on=("a",),
                                    ownership=("a.py", "c/x.py"))
        self.assertEqual(
            task.relevant_paths({"dag": [node_a, node_b]}),
            ["a.py", "b.py", "c/x.py"])

    def test_malformed_states_yield_empty(self):
        """非 dict / 缺 dag / dag 非数组 / 节点非 dict → 容错返回空列表。"""
        self.assertEqual(task.relevant_paths(None), [])
        self.assertEqual(task.relevant_paths({}), [])
        self.assertEqual(task.relevant_paths({"dag": "oops"}), [])
        self.assertEqual(task.relevant_paths({"dag": ["oops"]}), [])


if __name__ == "__main__":
    unittest.main()
