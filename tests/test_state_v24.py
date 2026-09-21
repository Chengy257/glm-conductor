#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""runtime.state v2.4 任务状态层单元测试（Phase 2 工作包 P2-A / W1）。

运行：
    cd <repo_root> && python3 -X utf8 -m unittest tests.test_state_v24 -v

覆盖（W1 单元规格的七类最低集 + 补充）：
    - 合法全字段样例：new_task_state 全字段构造 → validate_state 零
      错误；七态逐个合法；route 四模式逐个合法；空 dag 合法；
    - 非法 status 拒绝：v2.3 阶段态逐个拒绝（不网开一面）、垃圾值 /
      非 str 拒绝；
    - route 词汇强制：mode 恰为 solo/delegate/audit/full，词汇外
      拒绝；assurance 可选两级，词汇外拒绝；
    - dag 内嵌节点校验错误带节点路径前缀：节点级错误带 dag[i]. 前缀，
      整图错误（重复 id / 缺失依赖 / 自依赖 / 环）的 nodes[i] 路径
      改写为 dag[i]；
    - 终态重开拒绝：save_state / transition_task_status 对终态迁移
      一律拒绝；reopen_task 显式重开放行且落 task_reopened 事件；
    - legacy 检测正反例：work_units 键 / permit/lease/receipt 字段 /
      v2.3 status 词汇 → True；v2.4 全字段 state → False；
      detect_legacy_task 只读（缺失 None / 遗留带处置指引 / v2.4 带
      legacy=False），不写盘；
    - save/load 往返原子性：复用 durable_io 约定（唯一临时名 +
      os.replace），往返全等、无 .tmp 残留；损坏 state.json 拒绝
      读取且不被失败写覆盖；目录/内容 task_id 一致性（P1-9）。

仅 Python 3 标准库（unittest + tempfile），零第三方依赖。
"""

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "glm-conductor"))

from runtime import journal, state, work_unit  # noqa: E402


# —— 测试夹具 ——

REPO_ROOT = "C:/glm-conductor-under-test"

V23_STATUSES = (
    "created", "preflight", "routed", "decomposed", "executing",
    "joining", "verifying", "reviewing", "finalizing")


def demo_node(node_id="build", deps=()):
    """构造合法静态节点（经 work_unit.new_node 正门）。"""
    return work_unit.new_node(
        node_id, "实现 %s 节点" % node_id,
        depends_on=deps, ownership=("src/%s.py" % node_id,))


def full_state(**overrides):
    """构造合法全字段 v2.4 state（含可选 phase 与已填 validation）。"""
    st = state.new_task_state(
        "w1-demo-task", "验证 v2.4 状态层", {"mode": "solo"},
        repository_root=REPO_ROOT,
        dag_nodes=[demo_node("build"), demo_node("check", ("build",))],
        phase="validating")
    st["validation"] = {
        "status": "passed",
        "change_id": "sha256:" + "0" * 16,
        "summary": "主验证通过",
        "commands": ["python3 -m unittest tests.test_state_v24"],
    }
    dag_nodes = overrides.pop("dag_nodes", None)
    if dag_nodes is not None:
        st["dag"] = dag_nodes
    for key, value in overrides.items():
        st[key] = value
    return st


def legacy_v23_state():
    """构造带 v2.3 标记的最小遗留 state（work_units 键 + 阶段态）。"""
    return {
        "task_id": "legacy-23-task",
        "goal": "v2.3 遗留任务",
        "route": {"mode": "delegate", "delegability": "high",
                  "assurance": "standard", "executor": "flash-implementer",
                  "continuity": "foreground"},
        "status": "executing",
        "work_units": [{"id": "u1", "status": "executing",
                        "lease": {"holder": "main"}}],
    }


def write_raw_state(repo_root, task_id, payload):
    """绕过 save_state 直接落盘原始 JSON（构造遗留 / 损坏盘面用）。"""
    path = state.state_path(repo_root, task_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    return path


class TempRepoTest(unittest.TestCase):
    """提供每测试隔离的临时仓库根。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = self._tmp.name


# —— 1. 合法全字段样例 ——

class TestHappyPath(TempRepoTest):

    def test_full_schema_valid(self):
        """全字段构造 → 零错误；含可选 phase 与已填 validation/review。"""
        st = full_state()
        self.assertEqual(state.validate_state(st), [])

    def test_validation_and_review_records_fillable(self):
        """validation/review 任务级记录填充后仍合法（P2-C 形状）。"""
        st = full_state()
        st["review"] = {
            "reviewer": "glm-reviewer",
            "verdict": "ship",
            "change_id": "sha256:" + "0" * 16,
            "findings": "无阻塞发现",
        }
        self.assertEqual(state.validate_state(st), [])

    def test_all_seven_statuses_valid(self):
        """durable status 恰为七态：逐个写入 → 零错误。"""
        for status in state.TASK_STATUSES:
            st = full_state(status=status)
            self.assertEqual(state.validate_state(st), [],
                             "status=%r 应合法" % status)

    def test_all_four_route_modes_valid(self):
        """route 词汇恰为四模式：逐个构造 → 零错误。"""
        for mode in state.ROUTE_MODES:
            st = full_state(route={"mode": mode})
            self.assertEqual(state.validate_state(st), [],
                             "mode=%r 应合法" % mode)

    def test_workflow_run_id_nullable_and_fillable(self):
        """workflow_run_id 可空（缺省 None），非空 str 亦合法。"""
        self.assertIsNone(full_state()["workflow_run_id"])
        st = full_state(workflow_run_id="run-abc-123")
        self.assertEqual(state.validate_state(st), [])

    def test_empty_dag_valid(self):
        """空 dag 合法（solo 任务可暂无节点；可编译性归编译器判定）。"""
        st = state.new_task_state(
            "solo-empty", "无节点任务", {"mode": "solo"},
            repository_root=REPO_ROOT)
        self.assertEqual(state.validate_state(st), [])


# —— 2. 非法 status 拒绝 ——

class TestInvalidStatus(TempRepoTest):

    def test_v23_statuses_rejected(self):
        """v2.3 阶段态逐个拒绝——重基线不网开一面。"""
        for old in V23_STATUSES:
            st = full_state(status=old)
            errors = state.validate_state(st)
            self.assertEqual(len(errors), 1, "status=%r 应恰好报一处" % old)
            self.assertTrue(errors[0].startswith("status"),
                            "错误应指向 status 字段：%r" % errors[0])

    def test_garbage_and_non_str_status_rejected(self):
        """垃圾值 / 非 str 一律拒绝。"""
        for bad in ("", "ACTIVE", "activ ", None, 123, True, ["active"]):
            st = full_state(status=bad)
            errors = state.validate_state(st)
            self.assertTrue(
                any(item.startswith("status") for item in errors),
                "status=%r 应报 status 错误，得到 %r" % (bad, errors))


# —— 3. route 词汇强制 ——

class TestRouteVocabulary(TempRepoTest):

    def test_mode_outside_vocabulary_rejected(self):
        """mode 词汇外（含大小写变体与 None）拒绝。"""
        for bad in ("pair", "SOLO", "", None, 1):
            st = full_state(route={"mode": bad})
            errors = state.validate_state(st)
            self.assertTrue(
                any(item.startswith("route.mode") for item in errors),
                "mode=%r 应报 route.mode 错误，得到 %r" % (bad, errors))

    def test_assurance_optional_two_levels(self):
        """assurance 可选：缺省 / standard / high 合法，词汇外拒绝。"""
        for value in (None, "standard", "high"):
            st = full_state(route={"mode": "audit", "assurance": value})
            self.assertEqual(state.validate_state(st), [],
                             "assurance=%r 应合法" % value)
        st = full_state(route={"mode": "audit", "assurance": "critical"})
        errors = state.validate_state(st)
        self.assertTrue(any(item.startswith("route.assurance")
                            for item in errors), errors)

    def test_route_must_be_object(self):
        """route 非 dict 拒绝。"""
        errors = state.validate_state(full_state(route="solo"))
        self.assertEqual(errors, ["route 必须是 JSON 对象"])


# —— 4. 必填顶层键与可选 phase ——

class TestRequiredKeys(unittest.TestCase):

    def test_each_required_key_missing_flagged(self):
        """逐个删除必填顶层键 → 恰好一条「缺少必填顶层键」。"""
        for key in state._REQUIRED_TOP_KEYS:
            st = full_state()
            del st[key]
            errors = state.validate_state(st)
            self.assertIn("缺少必填顶层键 %s" % key, errors,
                          "缺 %s 应被点名" % key)

    def test_phase_optional_no_null_slot(self):
        """phase 可选：缺键合法（含构造期 None 省略）；写入词汇外拒绝。"""
        st = full_state()
        del st["phase"]
        self.assertEqual(state.validate_state(st), [])
        self.assertIsNone(full_state(phase=None).get("phase"),
                          "phase=None 构造期省略键，无 null 空档")
        for bad in ("executing", "review"):
            errors = state.validate_state(full_state(phase=bad))
            self.assertTrue(any(item.startswith("phase") for item in errors),
                            "phase=%r 应拒绝" % bad)

    def test_unknown_top_keys_ignored(self):
        """未知顶层键忽略（向前兼容）。"""
        st = full_state(future_block={"anything": True})
        self.assertEqual(state.validate_state(st), [])

    def test_workflow_run_id_shape(self):
        """workflow_run_id：null / 非空 str 合法；空串 / 非 str 拒绝。"""
        for bad in ("", 42, ["run"]):
            errors = state.validate_state(full_state(workflow_run_id=bad))
            self.assertTrue(
                any(item.startswith("workflow_run_id") for item in errors),
                "workflow_run_id=%r 应拒绝" % bad)


# —— 5. dag 内嵌节点校验错误带节点路径前缀 ——

class TestDagValidation(unittest.TestCase):

    def test_node_errors_carry_dag_index_prefix(self):
        """节点级错误带 dag[i]. 前缀（内嵌 work_unit.validate_node）。"""
        st = full_state(dag_nodes=[{"id": "a"}])  # 缺 objective/depends_on/ownership
        errors = state.validate_state(st)
        self.assertTrue(errors, "坏节点应产生错误")
        for item in errors:
            self.assertTrue(item.startswith("dag[0]."),
                            "错误应带 dag[0]. 前缀：%r" % item)

    def test_duplicate_id_graph_error_rewritten(self):
        """整图重复 id：nodes[i] 路径改写为 dag[i]。"""
        st = full_state(dag_nodes=[demo_node("dup"), demo_node("dup")])
        errors = state.validate_state(st)
        dup = [item for item in errors if "重复 id" in item]
        self.assertTrue(dup, errors)
        self.assertIn("dag[0]", dup[0])
        self.assertIn("dag[1]", dup[0])
        self.assertNotIn("nodes[", "".join(errors))

    def test_missing_dependency_and_self_dependency(self):
        """缺失依赖 / 自依赖：路径均以 dag[i] 报告。"""
        st = full_state(dag_nodes=[demo_node("a", ("ghost",)),
                                   demo_node("b", ("b",))])
        text = "\n".join(state.validate_state(st))
        self.assertIn("dag[0]", text)
        self.assertIn("dag[1]", text)
        self.assertIn("自依赖", text)
        self.assertNotIn("nodes[", text)

    def test_cycle_reported_with_dag_prefix(self):
        """依赖环：整图消息带 dag. 前缀且列出环成员。"""
        st = full_state(dag_nodes=[demo_node("a", ("b",)),
                                   demo_node("b", ("a",))])
        errors = state.validate_state(st)
        cycle = [item for item in errors if "环" in item]
        self.assertTrue(cycle, errors)
        self.assertTrue(cycle[0].startswith("dag."),
                        "环消息应带 dag. 前缀：%r" % cycle[0])

    def test_dag_must_be_list(self):
        """dag 非 list 拒绝。"""
        st = full_state()
        st["dag"] = None
        self.assertIn("dag 必须是数组", state.validate_state(st))
        st = full_state()
        st["dag"] = "build"
        self.assertIn("dag 必须是数组", state.validate_state(st))


# —— 6. validation / review / quota_resume 记录形状 ——

class TestTaskRecords(unittest.TestCase):

    def test_validation_shape(self):
        """validation：status 两态枚举 / change_id 与 summary 可空串 /
        commands 可空字符串数组。"""
        for bad_status in ("ok", "PASSED", None, 1):
            st = full_state(validation={"status": bad_status})
            if bad_status is None:
                self.assertEqual(state.validate_state(st), [])
                continue
            errors = state.validate_state(st)
            self.assertTrue(
                any(item.startswith("validation.status") for item in errors),
                "validation.status=%r 应拒绝" % bad_status)
        st = full_state(validation={"status": "passed", "commands": ""})
        self.assertTrue(
            any(item.startswith("validation.commands") for item in
                state.validate_state(st)))

    def test_review_shape(self):
        """review：verdict 恰为 ship/fix-first/rethink；v2.3 裁决词
        拒绝；reviewer/change_id/findings 可空串。"""
        for verdict in state.REVIEW_VERDICTS:
            st = full_state(review={"verdict": verdict,
                                    "reviewer": "glm-reviewer"})
            self.assertEqual(state.validate_state(st), [],
                             "verdict=%r 应合法" % verdict)
        for bad in ("not-required", "missing", "stale", "SHIP"):
            st = full_state(review={"verdict": bad})
            errors = state.validate_state(st)
            self.assertTrue(
                any(item.startswith("review.verdict") for item in errors),
                "verdict=%r 应拒绝" % bad)

    def test_quota_resume_placeholder_shape(self):
        """quota_resume 占位块恰为四键冻结初始形状。"""
        self.assertEqual(
            state.default_quota_resume(),
            {"mode": "manual", "max_resumes": 0, "resume_count": 0,
             "automation_id": None})
        self.assertIsNot(state.default_quota_resume(),
                         state.DEFAULT_QUOTA_RESUME,
                         "必须返回全新拷贝，不暴露模块常量")
        self.assertEqual(state.validate_state(full_state()), [])
        st = full_state(quota_resume={"mode": "auto_once"})
        self.assertTrue(any(item.startswith("quota_resume.mode")
                            for item in state.validate_state(st)))
        for bad in (-1, True, "3"):
            st = full_state(quota_resume={"resume_count": bad})
            self.assertTrue(
                any(item.startswith("quota_resume.resume_count")
                    for item in state.validate_state(st)),
                "resume_count=%r 应拒绝" % bad)


# —— 7. legacy 检测正反例 ——

class TestLegacyDetection(TempRepoTest):

    def test_is_legacy_state_positive(self):
        """v2.3 标记正例：work_units 键 / permit·lease·receipt 字段 /
        v2.3 status 词汇 / 顶层三集合键。"""
        self.assertTrue(state.is_legacy_state(legacy_v23_state()))
        self.assertTrue(state.is_legacy_state({"work_units": []}))
        self.assertTrue(state.is_legacy_state({"permits": []}))
        self.assertTrue(state.is_legacy_state({"leases": []}))
        self.assertTrue(state.is_legacy_state({"receipts": []}))
        self.assertTrue(state.is_legacy_state(
            {"work_units": [{"id": "u1", "receipt": "r"}]}))
        self.assertTrue(state.is_legacy_state({"status": "finalizing"}))

    def test_is_legacy_state_negative(self):
        """反例：v2.4 全字段 state / 空 dict / status 缺失 → False。"""
        self.assertFalse(state.is_legacy_state(full_state()))
        self.assertFalse(state.is_legacy_state({}))
        self.assertFalse(state.is_legacy_state(
            {"task_id": "t", "goal": "g", "status": "active"}))

    def test_detect_legacy_task_missing_returns_none(self):
        """无 state.json → None（不抛）。"""
        self.assertIsNone(state.detect_legacy_task(self.repo, "ghost"))

    def test_detect_legacy_task_reports_guidance(self):
        """遗留任务 → legacy=True + 一行处置指引 + 标记清单。"""
        write_raw_state(self.repo, "legacy-23-task", legacy_v23_state())
        report = state.detect_legacy_task(self.repo, "legacy-23-task")
        self.assertTrue(report["legacy"])
        self.assertEqual(report["guidance"], state.LEGACY_STATE_GUIDANCE)
        self.assertIn("在 2.3.x 下收尾或显式放弃", report["guidance"])
        self.assertIn("key:work_units", report["markers"])
        self.assertIn("status:executing", report["markers"])
        self.assertEqual(report["status"], "executing")

    def test_detect_legacy_task_v24_negative_and_readonly(self):
        """v2.4 任务 → legacy=False、guidance=None；检测全程不写盘。"""
        state.save_state(self.repo, full_state())
        before = state.state_path(self.repo, "w1-demo-task").read_bytes()
        report = state.detect_legacy_task(self.repo, "w1-demo-task")
        self.assertFalse(report["legacy"])
        self.assertIsNone(report["guidance"])
        self.assertEqual(report["markers"], [])
        self.assertEqual(
            state.state_path(self.repo, "w1-demo-task").read_bytes(),
            before, "detect_legacy_task 必须只读")


# —— 8. 终态重开拒绝 ——

def _rmtree_quiet(path):
    shutil.rmtree(path, ignore_errors=True)


class TestTerminalReopen(TempRepoTest):

    def _complete_task(self):
        """落一个合法 completed 任务，返回其状态 dict。"""
        state.save_state(self.repo, full_state())
        return state.transition_task_status(self.repo, "w1-demo-task",
                                            "completed")

    def test_save_state_refuses_terminal_reopen(self):
        """save_state 对终态 → 活跃态的静默改写拒绝。"""
        self._complete_task()
        st = state.load_state(self.repo, "w1-demo-task")
        st["status"] = "active"
        with self.assertRaises(ValueError) as ctx:
            state.save_state(self.repo, st)
        self.assertIn("转换被拒绝", str(ctx.exception))
        # 盘上仍为 completed
        self.assertEqual(
            state.load_state(self.repo, "w1-demo-task")["status"],
            "completed")

    def test_transition_refuses_all_terminal_exits(self):
        """三个终态经 transition_task_status 一律拒绝。"""
        for terminal in state.TERMINAL_STATUSES:
            repo = tempfile.mkdtemp()
            self.addCleanup(_rmtree_quiet, repo)
            state.save_state(repo, full_state(status=terminal))
            with self.assertRaises(ValueError):
                state.transition_task_status(repo, "w1-demo-task", "active")

    def test_reopen_task_is_the_only_explicit_channel(self):
        """reopen_task 显式重开放行：状态归 active 且落 task_reopened 事件。"""
        self._complete_task()
        reopened = state.reopen_task(self.repo, "w1-demo-task",
                                     reason="返工：change_id 过期")
        self.assertEqual(reopened["status"], "active")
        self.assertEqual(state.validate_state(reopened), [])
        self.assertEqual(
            state.load_state(self.repo, "w1-demo-task")["status"], "active")
        events = [item for item in
                  journal.read_events(self.repo, "w1-demo-task")
                  if item["event"] == "task_reopened"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["from"], "completed")
        self.assertEqual(events[0]["to"], "active")
        self.assertEqual(events[0]["reason"], "返工：change_id 过期")

    def test_reopen_refuses_non_terminal_and_missing(self):
        """非终态 / 缺失任务 → ValueError。"""
        state.save_state(self.repo, full_state())
        with self.assertRaises(ValueError):
            state.reopen_task(self.repo, "w1-demo-task")
        with self.assertRaises(ValueError):
            state.reopen_task(self.repo, "ghost-task")

    def test_terminal_no_silent_transition_between_terminals(self):
        """终态之间（completed → failed）同样拒绝。"""
        self._complete_task()
        st = state.load_state(self.repo, "w1-demo-task")
        st["status"] = "failed"
        with self.assertRaises(ValueError):
            state.save_state(self.repo, st)


# —— 9. save/load 往返原子性（复用 durable_io 约定） ——

class TestSaveLoadRoundtrip(TempRepoTest):

    def test_roundtrip_equality(self):
        """save → load 全等（含嵌套块）；路径符合布局约定。"""
        st = full_state()
        path = state.save_state(self.repo, st)
        self.assertEqual(
            path, state.state_path(self.repo, "w1-demo-task"))
        self.assertTrue(path.is_file())
        self.assertEqual(state.load_state(self.repo, "w1-demo-task"), st)

    def test_no_tmp_residue_after_save(self):
        """durable_io 约定：唯一临时名 + os.replace → 无 .tmp 残留。"""
        state.save_state(self.repo, full_state())
        residue = [name for name in
                   state.task_dir(self.repo, "w1-demo-task").iterdir()
                   if name.suffix == ".tmp"]
        self.assertEqual(residue, [])

    def test_legal_transition_chain_persists(self):
        """合法迁移链 active → waiting_quota → active → completed 落盘。"""
        state.save_state(self.repo, full_state())
        state.transition_task_status(self.repo, "w1-demo-task",
                                     "waiting_quota")
        self.assertEqual(
            state.load_state(self.repo, "w1-demo-task")["status"],
            "waiting_quota")
        state.transition_task_status(self.repo, "w1-demo-task", "active")
        state.transition_task_status(self.repo, "w1-demo-task", "completed")
        self.assertEqual(
            state.load_state(self.repo, "w1-demo-task")["status"],
            "completed")
        changed = [item for item in
                   journal.read_events(self.repo, "w1-demo-task")
                   if item["event"] == "status_changed"]
        self.assertEqual(
            [(item["from"], item["to"]) for item in changed],
            [("active", "waiting_quota"), ("waiting_quota", "active"),
             ("active", "completed")])

    def test_corrupt_state_file_not_swallowed(self):
        """损坏 state.json：load 抛 ValueError（不静默）、save 拒绝覆盖。"""
        path = write_raw_state(self.repo, "w1-demo-task",
                               {"task_id": "w1-demo-task"})
        path.write_text('{"task_id": "w1-demo", "goal": ', encoding="utf-8")
        with self.assertRaises(ValueError):
            state.load_state(self.repo, "w1-demo-task")
        with self.assertRaises(ValueError):
            state.save_state(self.repo, full_state())
        self.assertIn("goal", path.read_text(encoding="utf-8"),
                      "损坏文件不得被失败写覆盖")

    def test_invalid_state_save_refused_and_disk_untouched(self):
        """非法 state 保存拒绝；盘上既有合法 state 原样保留。"""
        state.save_state(self.repo, full_state())
        before = state.state_path(self.repo, "w1-demo-task").read_bytes()
        bad = full_state(status="executing")  # v2.3 阶段态：validate 拒绝
        with self.assertRaises(ValueError):
            state.save_state(self.repo, bad)
        self.assertEqual(
            state.state_path(self.repo, "w1-demo-task").read_bytes(),
            before)

    def test_task_id_directory_content_mismatch_refused(self):
        """P1-9：显式 task_id 与内容不一致 → ValueError。"""
        with self.assertRaises(ValueError):
            state.save_state(self.repo, full_state(), task_id="other-task")

    def test_load_missing_returns_none(self):
        """无 state.json → None。"""
        self.assertIsNone(state.load_state(self.repo, "ghost"))


# —— 10. 路径助手与发现（冻结面抽查） ——

class TestPathHelpersAndDiscovery(TempRepoTest):

    def test_path_layout_unchanged(self):
        """路径助手布局与 v2.3 一致（导入安全边界）。"""
        self.assertEqual(
            state.tasks_root(REPO_ROOT),
            Path(REPO_ROOT) / ".glm-conductor" / "tasks")
        self.assertEqual(
            state.task_dir(REPO_ROOT, "t1"),
            state.tasks_root(REPO_ROOT) / "t1")
        self.assertEqual(
            state.state_path(REPO_ROOT, "t1"),
            state.tasks_root(REPO_ROOT) / "t1" / "state.json")

    def test_repository_binding_helpers(self):
        """RB-2 绑定 / 容错读 / 解析（绑定优先，缺省回退）。"""
        st = full_state()
        bound = state.bound_repository_root(st)
        self.assertTrue(bound and Path(bound).is_absolute())
        self.assertEqual(
            state.resolve_repository_root(st, "/fallback"),
            bound)
        unbound = {"task_id": "t"}
        self.assertIsNone(state.bound_repository_root(unbound))
        self.assertEqual(
            state.resolve_repository_root(unbound, "/fallback"), "/fallback")

    def test_discover_tasks_four_buckets(self):
        """四分类：active / terminal / corrupt / orphaned 各就各位。"""
        state.save_state(self.repo, full_state())  # active
        state.save_state(self.repo, full_state(task_id="done-task",
                                               status="completed"))
        (state.tasks_root(self.repo) / "orphan-task").mkdir(parents=True)
        write_raw_state(self.repo, "broken-task", {"task_id": "broken"})
        state.state_path(self.repo, "broken-task").write_text(
            "{broken json", encoding="utf-8")
        discovery = state.discover_tasks(self.repo)
        self.assertEqual([name for name, _ in discovery["active"]],
                         ["w1-demo-task"])
        self.assertEqual([name for name, _ in discovery["terminal"]],
                         ["done-task"])
        self.assertEqual([name for name, _ in discovery["corrupt"]],
                         ["broken-task"])
        self.assertEqual([name for name, _ in discovery["orphaned"]],
                         ["orphan-task"])
        self.assertEqual(state.find_active_tasks(self.repo),
                         ["w1-demo-task"])

    def test_legacy_task_falls_into_active_bucket(self):
        """v2.3 遗留任务（status 阶段态）落 active 桶，留给恢复层渲染。"""
        write_raw_state(self.repo, "legacy-23-task", legacy_v23_state())
        self.assertEqual(state.find_active_tasks(self.repo),
                         ["legacy-23-task"])


if __name__ == "__main__":
    unittest.main()
