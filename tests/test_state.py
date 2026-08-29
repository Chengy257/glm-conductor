#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""runtime.state 单元测试（v2 工作块 B1.2）。

仅 Python 3 标准库（unittest + tempfile），零第三方依赖；
所有文件操作在 tempfile.TemporaryDirectory 内进行，不污染真实目录。

运行：
    cd <repo_root> && python3 -m unittest tests.test_state -v
"""

import sys, unittest
import json, tempfile
from unittest import mock
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "glm-conductor"))
from runtime import journal, state

# —— 测试夹具 ——

TID = "demo-task-1a2b3c"
# H2 夹具迁移：full 路由在路由矩阵下必须 assurance=high
# （high+standard 应为 delegate）；规则 R3 由此要求 review.required=true，
# new_task_state 缺省派生正好满足
FULL_ROUTE = {
    "mode": "full",
    "delegability": "high",
    "assurance": "high",
    "executor": "flash-implementer",
    "continuity": "foreground",
}


def make_state(task_id=TID, goal="重构认证中间件", route=None, status="created",
               **kwargs):
    """构造一个默认合法的完整状态 dict（route 缺省用 FULL_ROUTE）。

    H2 规则 R4 要求 delegate/full 任务声明非空 ownership.files 与
    verification.required：夹具缺省补占位声明，保证默认构造可过
    validate_state；各用例关注的字段不受影响。
    """
    if route is None:
        route = dict(FULL_ROUTE)
    kwargs.setdefault("ownership_files", ("src/auth.ts",))
    kwargs.setdefault(
        "verification_required", ("python3 -m unittest tests.test_state",))
    return state.new_task_state(task_id, goal, route, status=status, **kwargs)


def write_raw_state(repo_root, task_id, text):
    """在临时仓库内直接落一个 state.json 原始文本（构造损坏/legacy 用例）。"""
    directory = state.task_dir(repo_root, task_id)
    directory.mkdir(parents=True)
    path = directory / state.STATE_FILENAME
    path.write_text(text, encoding="utf-8")
    return path


# —— 路径常量与定位 ——

class PathHelpersTest(unittest.TestCase):

    def test_path_constants(self):
        self.assertEqual(state.TASKS_DIRNAME, ".glm-conductor")
        self.assertEqual(state.STATE_FILENAME, "state.json")

    def test_tasks_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(
                state.tasks_root(tmp),
                Path(tmp) / ".glm-conductor" / "tasks")

    def test_task_dir_and_state_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            expected_dir = Path(tmp) / ".glm-conductor" / "tasks" / "t-1"
            self.assertEqual(state.task_dir(tmp, "t-1"), expected_dir)
            self.assertEqual(
                state.state_path(tmp, "t-1"), expected_dir / "state.json")


# —— normalize_task_id ——

class NormalizeTaskIdTest(unittest.TestCase):

    def test_each_of_four_keys(self):
        # 四种键各自单独存在时都能取值
        for key in state.TASK_ID_KEYS:
            self.assertEqual(state.normalize_task_id({key: "x-1"}), "x-1", key)

    def test_priority_takes_first_key(self):
        # 多键同值：取 TASK_ID_KEYS 顺序中第一个键的值
        raw = {"task_id": "a-1", "TASK_ID": "a-1",
               "CONTINUITY_ID": "a-1", "continuity_id": "a-1"}
        self.assertEqual(state.normalize_task_id(raw), "a-1")

    def test_conflicting_values_raise(self):
        with self.assertRaises(ValueError):
            state.normalize_task_id({"task_id": "a-1", "CONTINUITY_ID": "b-2"})

    def test_all_keys_missing_raises(self):
        with self.assertRaises(ValueError):
            state.normalize_task_id({"goal": "没有标识"})

    def test_empty_value_raises(self):
        with self.assertRaises(ValueError):
            state.normalize_task_id({"task_id": ""})

    def test_non_str_value_raises(self):
        with self.assertRaises(ValueError):
            state.normalize_task_id({"task_id": 123})

    def test_non_dict_raises(self):
        with self.assertRaises(ValueError):
            state.normalize_task_id(["a-1"])


# —— validate_state ——

class ValidateStateTest(unittest.TestCase):

    def test_valid_full_state(self):
        self.assertEqual(state.validate_state(make_state()), [])

    def test_valid_with_minimal_route(self):
        # route 只给 mode，其余键缺省（new_task_state 填 None）依然合法
        st = state.new_task_state("t-1", "目标", {"mode": "solo"})
        self.assertEqual(state.validate_state(st), [])

    def test_non_dict_input(self):
        for bad in (None, [], "x", 42):
            self.assertEqual(
                state.validate_state(bad), ["state 必须是 JSON 对象"], bad)

    def test_missing_task_id(self):
        st = make_state()
        del st["task_id"]
        self.assertTrue(
            any("task_id" in e for e in state.validate_state(st)))

    def test_bad_task_id_format(self):
        for bad in ("", "-abc", "a b", "任务一", "a_b"):
            st = make_state(task_id=bad)
            errors = state.validate_state(st)
            self.assertTrue(any("task_id" in e for e in errors), bad)

    def test_missing_goal(self):
        st = make_state()
        del st["goal"]
        self.assertTrue(any("goal" in e for e in state.validate_state(st)))

    def test_empty_goal(self):
        st = make_state(goal="")
        self.assertTrue(any("goal" in e for e in state.validate_state(st)))

    def test_missing_route(self):
        st = make_state()
        del st["route"]
        self.assertTrue(any("route" in e for e in state.validate_state(st)))

    def test_route_not_dict(self):
        st = make_state(route=None)
        st["route"] = ["full"]
        self.assertTrue(
            any(e == "route 必须是 JSON 对象" for e in state.validate_state(st)))

    def test_bad_route_mode(self):
        st = make_state(route={"mode": "wizard"})
        self.assertTrue(
            any("route.mode" in e for e in state.validate_state(st)))

    def test_missing_route_mode(self):
        st = make_state(route={})
        self.assertTrue(
            any("route.mode" in e for e in state.validate_state(st)))

    def test_bad_route_optional_keys(self):
        for key, value in (("delegability", "medium"),
                           ("assurance", "ultra"),
                           ("executor", "wizard-agent"),
                           ("continuity", "background")):
            route = {"mode": "solo", key: value}
            st = make_state(route=route)
            errors = state.validate_state(st)
            self.assertTrue(any("route.%s" % key in e for e in errors),
                            "%s=%s" % (key, value))

    def test_route_optional_none_is_allowed(self):
        route = {"mode": "delegate", "delegability": None, "assurance": None,
                 "executor": None, "continuity": None}
        st = make_state(route=route)
        self.assertEqual(state.validate_state(st), [])

    def test_missing_status(self):
        st = make_state()
        del st["status"]
        self.assertTrue(any("status" in e for e in state.validate_state(st)))

    def test_bad_status(self):
        st = make_state(status="active")  # v1.x 旧词不在 v2 词汇表
        self.assertTrue(
            any(e.startswith("status") for e in state.validate_state(st)))

    def test_bad_review_required_type(self):
        st = make_state(review_required="yes")
        self.assertTrue(
            any("review.required" in e for e in state.validate_state(st)))

    def test_bad_review_verdict(self):
        st = state.new_task_state(
            TID, "目标", dict(FULL_ROUTE), review_required=True,
            reviewer="glm-reviewer")
        st["review"]["verdict"] = "looks-good"
        self.assertTrue(
            any("review.verdict" in e for e in state.validate_state(st)))

    def test_review_verdict_none_allowed(self):
        st = make_state(review_required=True, reviewer="glm-reviewer")
        self.assertEqual(state.validate_state(st), [])

    def test_bad_reviewer_type(self):
        st = make_state(review_required=True, reviewer=42)
        self.assertTrue(
            any("review.reviewer" in e for e in state.validate_state(st)))

    def test_review_fingerprint_valid_str(self):
        st = make_state()
        st["review"]["fingerprint"] = "sha256:" + "a" * 64
        self.assertEqual(state.validate_state(st), [])

    def test_review_fingerprint_null_and_missing_allowed(self):
        st = make_state()
        st["review"]["fingerprint"] = None
        self.assertEqual(state.validate_state(st), [])
        # 键整体缺失同样合法（schema 权威含 fingerprint: null，但校验宽松）
        del st["review"]["fingerprint"]
        self.assertEqual(state.validate_state(st), [])

    def test_review_fingerprint_bad_type(self):
        for bad in (12345, ["sha256:" + "a" * 64], {"hex": "aa"}, True):
            st = make_state()
            st["review"]["fingerprint"] = bad
            errors = state.validate_state(st)
            self.assertTrue(
                any("review.fingerprint" in e for e in errors), repr(bad))

    def test_ownership_not_dict(self):
        st = make_state()
        st["ownership"] = ["src/auth.ts"]
        self.assertTrue(
            any(e == "ownership 必须是 JSON 对象"
                for e in state.validate_state(st)))

    def test_ownership_files_not_list(self):
        st = make_state()
        st["ownership"]["files"] = "src/auth.ts"
        self.assertTrue(
            any("ownership.files" in e for e in state.validate_state(st)))

    def test_ownership_files_bad_item(self):
        st = make_state(ownership_files=("src/auth.ts", ""))
        errors = state.validate_state(st)
        self.assertTrue(any("ownership.files[1]" in e for e in errors))

    def test_verification_required_not_list(self):
        st = make_state()
        st["verification"]["required"] = "python3 -m pytest"
        self.assertTrue(any(
            "verification.required" in e for e in state.validate_state(st)))

    def test_verification_completed_bad_item(self):
        st = make_state()
        st["verification"]["completed"] = ["python3 -m pytest", 3]
        self.assertTrue(any(
            "verification.completed[1]" in e for e in state.validate_state(st)))

    def test_verification_fingerprint_bad_type(self):
        st = make_state()
        st["verification"]["fingerprint"] = 12345
        self.assertTrue(any(
            "verification.fingerprint" in e for e in state.validate_state(st)))

    def test_visual_evidence_valid(self):
        st = make_state()
        st["visual_evidence"] = [
            {"path": "docs/shot.png", "sha256": "ab" * 32, "extra": 1}]
        # 其他键忽略（向前兼容）；空数组同样合法
        self.assertEqual(state.validate_state(st), [])
        st["visual_evidence"] = []
        self.assertEqual(state.validate_state(st), [])

    def test_visual_evidence_not_list(self):
        st = make_state()
        st["visual_evidence"] = {"path": "docs/shot.png", "sha256": "ab"}
        errors = state.validate_state(st)
        self.assertTrue(
            any(e == "visual_evidence 必须是数组" for e in errors))

    def test_visual_evidence_item_not_dict(self):
        st = make_state()
        st["visual_evidence"] = ["docs/shot.png"]
        errors = state.validate_state(st)
        self.assertTrue(any(
            e == "visual_evidence[0] 必须是 JSON 对象" for e in errors))

    def test_visual_evidence_missing_path(self):
        st = make_state()
        st["visual_evidence"] = [{"sha256": "ab" * 32}]
        errors = state.validate_state(st)
        self.assertTrue(any(
            e == "visual_evidence[0].path 必须是非空字符串" for e in errors))

    def test_visual_evidence_bad_sha256(self):
        for bad in ("", 12345, None, ["ab"]):
            st = make_state()
            st["visual_evidence"] = [{"path": "docs/shot.png", "sha256": bad}]
            errors = state.validate_state(st)
            self.assertTrue(any(
                e == "visual_evidence[0].sha256 必须是非空字符串"
                for e in errors), repr(bad))

    def test_visual_evidence_non_str_path(self):
        st = make_state()
        st["visual_evidence"] = [{"path": 42, "sha256": "ab" * 32}]
        errors = state.validate_state(st)
        self.assertTrue(any(
            "visual_evidence[0].path" in e for e in errors))

    def test_work_units_not_list(self):
        st = make_state()
        st["work_units"] = {"id": "wu-1"}
        self.assertTrue(
            any(e == "work_units 必须是数组" for e in state.validate_state(st)))

    def test_dispatch_not_dict(self):
        st = make_state()
        st["dispatch"] = [1]
        self.assertTrue(
            any(e == "dispatch 必须是 JSON 对象"
                for e in state.validate_state(st)))

    def test_dispatch_max_workers_below_one(self):
        for bad in (0, -1):
            st = make_state()
            st["dispatch"]["max_workers"] = bad
            self.assertTrue(any(
                "dispatch.max_workers" in e for e in state.validate_state(st)),
                bad)

    def test_dispatch_max_workers_bad_type(self):
        for bad in ("2", 1.5, True):
            st = make_state()
            st["dispatch"]["max_workers"] = bad
            self.assertTrue(any(
                "dispatch.max_workers" in e for e in state.validate_state(st)),
                repr(bad))

    def test_dispatch_active_not_list(self):
        st = make_state()
        st["dispatch"]["active"] = "wu-1"
        self.assertTrue(
            any("dispatch.active" in e for e in state.validate_state(st)))

    def test_unknown_top_key_ignored(self):
        st = make_state()
        st["future_field"] = {"whatever": [1, 2, 3]}
        self.assertEqual(state.validate_state(st), [])


# —— new_task_state ——

class NewTaskStateTest(unittest.TestCase):

    def test_defaults(self):
        st = state.new_task_state("t-1", "目标", {"mode": "solo"})
        self.assertEqual(st["task_id"], "t-1")
        self.assertEqual(st["goal"], "目标")
        self.assertEqual(st["status"], "created")
        self.assertEqual(
            st["route"],
            {"mode": "solo", "delegability": None, "assurance": None,
             "executor": None, "continuity": None})
        self.assertEqual(st["ownership"], {"files": []})
        self.assertEqual(
            st["verification"],
            {"required": [], "completed": [], "fingerprint": None})
        self.assertEqual(
            st["review"],
            {"required": False, "reviewer": None, "verdict": None,
             "fingerprint": None})
        self.assertEqual(st["visual_evidence"], [])
        self.assertEqual(st["work_units"], [])
        self.assertEqual(st["dispatch"], {"max_workers": 1, "active": []})

    def test_visual_evidence_key_position(self):
        # 顶层键顺序契约：visual_evidence 在 review 之后、work_units 之前
        st = state.new_task_state("t-1", "目标", {"mode": "solo"})
        keys = list(st.keys())
        self.assertLess(keys.index("review"), keys.index("visual_evidence"))
        self.assertLess(keys.index("visual_evidence"), keys.index("work_units"))

    def test_route_missing_keys_become_none(self):
        st = state.new_task_state("t-1", "目标", {})
        self.assertIsNone(st["route"]["mode"])
        self.assertIsNone(st["route"]["continuity"])
        # mode 为 None 时校验必须报 route.mode（构造不校验，校验交给调用方）
        self.assertTrue(
            any("route.mode" in e for e in state.validate_state(st)))

    def test_route_non_dict_raises_type_error(self):
        # route 非 dict：直接 TypeError（含实际类型名），不再是裸 AttributeError
        for bad in (None, "solo", ["mode"], 42):
            with self.assertRaises(TypeError, msg=repr(bad)) as ctx:
                state.new_task_state("t-1", "目标", bad)
            self.assertIn(type(bad).__name__, str(ctx.exception), repr(bad))

    def test_full_params_and_valid(self):
        st = state.new_task_state(
            "t-2", "目标二", {"mode": "audit"},
            ownership_files=("src/a.py",),
            verification_required=("python3 -m pytest",),
            review_required=True, reviewer="glm-reviewer",
            status="preflight")
        self.assertEqual(st["ownership"]["files"], ["src/a.py"])
        self.assertEqual(st["verification"]["required"], ["python3 -m pytest"])
        self.assertEqual(
            st["review"],
            {"required": True, "reviewer": "glm-reviewer", "verdict": None,
             "fingerprint": None})
        self.assertEqual(st["status"], "preflight")
        self.assertEqual(state.validate_state(st), [])

    def test_input_containers_copied(self):
        files = ("a.py",)
        required = ("python3 a.py",)
        st = state.new_task_state(
            "t-1", "目标", {"mode": "solo"},
            ownership_files=files, verification_required=required)
        st["ownership"]["files"].append("b.py")
        st["verification"]["required"].append("python3 b.py")
        # 元组入参不被后续可变操作波及
        self.assertEqual(files, ("a.py",))
        self.assertEqual(required, ("python3 a.py",))


# —— save / load 往返 ——

class SaveLoadRoundtripTest(unittest.TestCase):

    def test_roundtrip_identical(self):
        with tempfile.TemporaryDirectory() as tmp:
            st = make_state(review_required=True, reviewer="glm-reviewer")
            path = state.save_state(tmp, st)
            self.assertEqual(path, state.state_path(tmp, TID))
            self.assertTrue(path.is_file())
            loaded = state.load_state(tmp, TID)
            self.assertEqual(loaded, st)

    def test_utf8_chinese_content_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            st = make_state(goal="重构认证中间件：阶段一（含中文与「引号」）")
            state.save_state(tmp, st)
            text = state.state_path(tmp, TID).read_text(encoding="utf-8")
            # ensure_ascii=False：中文以原样字符写入而非 \uXXXX 转义
            self.assertIn("重构认证中间件", text)
            self.assertNotIn("\\u", text)
            self.assertEqual(state.load_state(tmp, TID), st)

    def test_no_tmp_leftover_after_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            state.save_state(tmp, make_state())
            leftovers = list(state.task_dir(tmp, TID).glob("*.tmp"))
            self.assertEqual(leftovers, [])

    def test_task_id_param_must_match_payload(self):
        """显式 task_id 与内容 task_id 一致 → 正常写盘（P1-9 起
        不一致组合被拒绝，见
        test_state_directory_id_must_match_payload_task_id）。"""
        with tempfile.TemporaryDirectory() as tmp:
            st = make_state()
            path = state.save_state(tmp, st, task_id=TID)
            self.assertEqual(path, state.state_path(tmp, TID))
            self.assertTrue(path.is_file())
            self.assertEqual(state.load_state(tmp, TID), st)

    def test_state_directory_id_must_match_payload_task_id(self):
        """P1-9：save_state 显式 task_id 与 state.task_id 不一致 →
        ValueError 且不产生任何目录；一致时正常；缺省 task_id（None）
        行为不变。"""
        with tempfile.TemporaryDirectory() as tmp:
            st = make_state()
            other = "demo-task-9f8e7d"
            with self.assertRaises(ValueError) as ctx:
                state.save_state(tmp, st, task_id=other)
            self.assertIn("不一致", str(ctx.exception))
            self.assertIn("P1-9", str(ctx.exception))
            # 拒绝写入：目录 id 与内容 id 对应路径都不存在
            self.assertFalse(state.state_path(tmp, other).exists())
            self.assertFalse(state.state_path(tmp, TID).exists())
            self.assertFalse(state.tasks_root(tmp).exists())
            # 显式 task_id 与内容一致 → 正常保存
            path = state.save_state(tmp, st, task_id=TID)
            self.assertEqual(path, state.state_path(tmp, TID))
            self.assertTrue(path.is_file())
            # 缺省 task_id（None）行为不变：目录名取 state["task_id"]
            st2 = make_state(status="preflight")
            path2 = state.save_state(tmp, st2)
            self.assertEqual(path2, state.state_path(tmp, TID))
            self.assertEqual(state.load_state(tmp, TID), st2)

    def test_save_creates_missing_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(state.tasks_root(tmp).exists())
            state.save_state(tmp, make_state())
            self.assertTrue(state.state_path(tmp, TID).is_file())

    def test_save_rejects_invalid_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            st = make_state(status="not-a-status")
            with self.assertRaises(ValueError) as ctx:
                state.save_state(tmp, st)
            # 错误消息拼接校验错误清单
            self.assertIn("status", str(ctx.exception))
            # 校验先于落盘：非法状态不得写出任何文件
            self.assertFalse(state.state_path(tmp, TID).exists())


# —— record_* 写入助手（纯 dict 变换，不触碰磁盘） ——

class RecordHelpersTest(unittest.TestCase):

    FP_A = "sha256:" + "a" * 64
    FP_B = "sha256:" + "b" * 64

    # record_verification

    def test_record_verification_appends_and_dedups(self):
        st = make_state()
        result = state.record_verification(st, "python3 -m pytest")
        self.assertIs(result, st)  # 返回同一 dict（就地修改）
        self.assertEqual(st["verification"]["completed"], ["python3 -m pytest"])
        state.record_verification(st, "python3 -m pytest")  # 已存在不重复追加
        state.record_verification(st, "python3 -m ruff check .")
        self.assertEqual(
            st["verification"]["completed"],
            ["python3 -m pytest", "python3 -m ruff check ."])

    def test_record_verification_fingerprint_updated_only_when_not_none(self):
        st = make_state()
        self.assertIsNone(st["verification"]["fingerprint"])
        state.record_verification(st, "python3 -m pytest", fingerprint=self.FP_A)
        self.assertEqual(st["verification"]["fingerprint"], self.FP_A)
        # fingerprint=None 表示「本次不更新指纹」，保留旧值
        state.record_verification(st, "python3 -m ruff check .")
        self.assertEqual(st["verification"]["fingerprint"], self.FP_A)
        state.record_verification(st, "python3 -m pytest", fingerprint=self.FP_B)
        self.assertEqual(st["verification"]["fingerprint"], self.FP_B)

    def test_record_verification_creates_missing_structures(self):
        # verification 子 dict 整体缺失
        st = {"task_id": TID}
        state.record_verification(st, "python3 -m pytest", fingerprint=self.FP_A)
        self.assertEqual(st["verification"]["completed"], ["python3 -m pytest"])
        self.assertEqual(st["verification"]["fingerprint"], self.FP_A)
        # completed list 缺失但 verification 存在
        st2 = make_state()
        del st2["verification"]["completed"]
        state.record_verification(st2, "cmd-2")
        self.assertEqual(st2["verification"]["completed"], ["cmd-2"])

    def test_record_verification_value_errors_no_side_effect(self):
        st = make_state()
        for bad_command in ("", None, 42, ["python3 -m pytest"]):
            with self.subTest(command=bad_command):
                with self.assertRaises(ValueError):
                    state.record_verification(st, bad_command)
        for bad_fp in ("", 42, ["sha256:x"], dict(hex="aa")):
            with self.subTest(fingerprint=bad_fp):
                with self.assertRaises(ValueError):
                    state.record_verification(
                        st, "python3 -m pytest", fingerprint=bad_fp)
        # 校验先于修改：失败不产生任何副作用
        self.assertEqual(st["verification"]["completed"], [])
        self.assertIsNone(st["verification"]["fingerprint"])

    # record_review

    def test_record_review_updates_verdict_and_fingerprint(self):
        st = make_state(review_required=True, reviewer="glm-reviewer")
        result = state.record_review(st, "ship", fingerprint=self.FP_A)
        self.assertIs(result, st)
        self.assertEqual(st["review"]["verdict"], "ship")
        self.assertEqual(st["review"]["fingerprint"], self.FP_A)

    def test_record_review_accepts_all_verdicts(self):
        for verdict in state.REVIEW_VERDICTS:
            st = make_state()
            state.record_review(st, verdict)
            self.assertEqual(st["review"]["verdict"], verdict, verdict)

    def test_record_review_none_fingerprint_keeps_old(self):
        st = make_state()
        state.record_review(st, "missing")
        self.assertIsNone(st["review"]["fingerprint"])
        state.record_review(st, "ship", fingerprint=self.FP_A)
        state.record_review(st, "fix-first")  # None → 不更新指纹
        self.assertEqual(st["review"]["fingerprint"], self.FP_A)

    def test_record_review_creates_missing_review_dict(self):
        st = {"task_id": TID}
        state.record_review(st, "ship", fingerprint=self.FP_A)
        # 重建时带 required=False / reviewer=None 默认值（required 语义
        # 由调用方后续负责）
        self.assertEqual(
            st["review"],
            {"required": False, "reviewer": None, "verdict": "ship",
             "fingerprint": self.FP_A})

    def test_record_review_bad_verdict_raises_no_side_effect(self):
        st = make_state()
        for bad in ("looks-good", "", None, 42, True):
            with self.subTest(verdict=bad):
                with self.assertRaises(ValueError):
                    state.record_review(st, bad)
        self.assertIsNone(st["review"]["verdict"])

    def test_record_review_bad_fingerprint_raises(self):
        st = make_state()
        for bad_fp in ("", 42):
            with self.subTest(fingerprint=bad_fp):
                with self.assertRaises(ValueError):
                    state.record_review(st, "ship", fingerprint=bad_fp)
        self.assertIsNone(st["review"]["verdict"])

    # record_visual_evidence

    def test_record_visual_evidence_append_and_replace(self):
        st = make_state()
        result = state.record_visual_evidence(st, "docs/a.png", "aa" * 32)
        self.assertIs(result, st)
        self.assertEqual(
            st["visual_evidence"],
            [{"path": "docs/a.png", "sha256": "aa" * 32}])
        state.record_visual_evidence(st, "docs/b.png", "bb" * 32)
        self.assertEqual(len(st["visual_evidence"]), 2)
        # 同 path：就地替换该项 sha256，不追加第二条，顺序保持
        state.record_visual_evidence(st, "docs/a.png", "cc" * 32)
        self.assertEqual(
            st["visual_evidence"],
            [{"path": "docs/a.png", "sha256": "cc" * 32},
             {"path": "docs/b.png", "sha256": "bb" * 32}])

    def test_record_visual_evidence_creates_missing_key(self):
        st = {"task_id": TID}
        state.record_visual_evidence(st, "x.png", "ab" * 32)
        self.assertEqual(
            st["visual_evidence"], [{"path": "x.png", "sha256": "ab" * 32}])
        # 顶层键形状异常（非 list）：重建为 list 后正常追加
        st2 = make_state()
        st2["visual_evidence"] = "corrupt"
        state.record_visual_evidence(st2, "x.png", "ab" * 32)
        self.assertEqual(
            st2["visual_evidence"], [{"path": "x.png", "sha256": "ab" * 32}])

    def test_record_visual_evidence_value_errors_no_side_effect(self):
        st = make_state()
        for bad_path in ("", None, 42, ["x.png"]):
            with self.subTest(path=bad_path):
                with self.assertRaises(ValueError):
                    state.record_visual_evidence(st, bad_path, "ab" * 32)
        for bad_sha in ("", None, 42, ["ab"]):
            with self.subTest(sha256=bad_sha):
                with self.assertRaises(ValueError):
                    state.record_visual_evidence(st, "x.png", bad_sha)
        self.assertEqual(st["visual_evidence"], [])

    # 三个助手均不触碰磁盘 + save/load 往返字段保留

    def test_record_results_survive_save_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            st = make_state(review_required=True, reviewer="glm-reviewer")
            state.record_verification(
                st, "python3 -m pytest", fingerprint=self.FP_A)
            state.record_review(st, "ship", fingerprint=self.FP_B)
            state.record_visual_evidence(st, "docs/shot.png", "ab" * 32)
            state.save_state(tmp, st)
            loaded = state.load_state(tmp, TID)
            self.assertEqual(loaded, st)
            self.assertEqual(loaded["verification"]["fingerprint"], self.FP_A)
            self.assertEqual(loaded["review"]["fingerprint"], self.FP_B)
            self.assertEqual(
                loaded["visual_evidence"],
                [{"path": "docs/shot.png", "sha256": "ab" * 32}])
            # 加载回的状态再次通过校验（含新 schema 字段）
            self.assertEqual(state.validate_state(loaded), [])


# —— load_state ——

class LoadStateTest(unittest.TestCase):

    def test_missing_file_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(state.load_state(tmp, "no-such-task-000000"))

    def test_corrupt_json_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_raw_state(tmp, TID, "{not json")
            with self.assertRaises(ValueError):
                state.load_state(tmp, TID)

    def test_legacy_upper_continuity_id_normalized(self):
        with tempfile.TemporaryDirectory() as tmp:
            legacy = {"CONTINUITY_ID": TID, "goal": "legacy 目标",
                      "route": {"mode": "delegate"}, "status": "executing"}
            write_raw_state(
                tmp, TID, json.dumps(legacy, ensure_ascii=False))
            loaded = state.load_state(tmp, TID)
            self.assertEqual(loaded["task_id"], TID)
            self.assertNotIn("CONTINUITY_ID", loaded)
            self.assertEqual(loaded["status"], "executing")

    def test_legacy_lower_continuity_id_normalized(self):
        with tempfile.TemporaryDirectory() as tmp:
            legacy = {"continuity_id": TID, "goal": "legacy 目标"}
            write_raw_state(
                tmp, TID, json.dumps(legacy, ensure_ascii=False))
            loaded = state.load_state(tmp, TID)
            self.assertEqual(loaded["task_id"], TID)
            self.assertNotIn("continuity_id", loaded)

    def test_legacy_upper_task_id_normalized(self):
        with tempfile.TemporaryDirectory() as tmp:
            legacy = {"TASK_ID": TID, "goal": "legacy 目标"}
            write_raw_state(
                tmp, TID, json.dumps(legacy, ensure_ascii=False))
            loaded = state.load_state(tmp, TID)
            self.assertEqual(loaded["task_id"], TID)
            self.assertNotIn("TASK_ID", loaded)

    def test_legacy_conflicting_ids_raise(self):
        with tempfile.TemporaryDirectory() as tmp:
            legacy = {"task_id": "a-1", "CONTINUITY_ID": "b-2", "goal": "冲突"}
            write_raw_state(
                tmp, "a-1", json.dumps(legacy, ensure_ascii=False))
            with self.assertRaises(ValueError):
                state.load_state(tmp, "a-1")


# —— find_active_tasks ——

class FindActiveTasksTest(unittest.TestCase):

    def save(self, repo_root, task_id, status):
        # save_state 自 H1 起带状态转换门：completed 不能经公共 API 直达
        # （首存同样被拒）——completed 夹具走合法迁移链
        # created→executing→finalizing→完成门内部提交，被测语义不变
        # （盘上存在相应终态任务）；其余状态无盘上旧状态时首存不受
        # 转换表约束，直接落盘即可
        st = make_state(task_id=task_id, status=status)
        if status == "completed":
            st["status"] = "executing"
            state.save_state(repo_root, st)
            st["status"] = "finalizing"
            state.save_state(repo_root, st)
            state.commit_completion(repo_root, task_id)
            return
        state.save_state(repo_root, st)

    def test_mixed_active_terminal_and_broken_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.save(tmp, "a-active-111111", "executing")
            self.save(tmp, "b-completed-222222", "completed")
            self.save(tmp, "c-cancelled-333333", "cancelled")
            self.save(tmp, "d-failed-444444", "failed")
            # JSON 损坏：load_state 抛 ValueError，必须被跳过
            write_raw_state(tmp, "e-corrupt-555555", "{broken")
            # JSON 合法但缺任务标识：normalize 抛 ValueError，必须被跳过
            write_raw_state(
                tmp, "f-no-id-666666", json.dumps({"goal": "缺标识"}))
            # 目录存在但无 state.json：load 返回 None，必须被跳过
            state.task_dir(tmp, "g-empty-777777").mkdir(parents=True)
            self.assertEqual(state.find_active_tasks(tmp), ["a-active-111111"])

    def test_multiple_active_sorted_by_dir_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.save(tmp, "z-later-aaaaaa", "executing")
            self.save(tmp, "a-earlier-bbbbbb", "blocked")
            self.assertEqual(
                state.find_active_tasks(tmp),
                ["a-earlier-bbbbbb", "z-later-aaaaaa"])

    def test_no_tasks_dir_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(state.find_active_tasks(tmp), [])

    def test_plain_file_under_tasks_root_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.save(tmp, "a-active-111111", "routed")
            (state.tasks_root(tmp) / "not-a-dir.txt").write_text(
                "杂项文件", encoding="utf-8")
            self.assertEqual(state.find_active_tasks(tmp), ["a-active-111111"])

    def test_unreadable_dir_skipped_scan_continues(self):
        # 单目录 load_state 抛 OSError（如权限不足）：跳过该目录，
        # 其余活动任务仍被完整返回（不中断扫描）
        with tempfile.TemporaryDirectory() as tmp:
            self.save(tmp, "a-active-111111", "executing")
            self.save(tmp, "b-no-access-222222", "executing")
            self.save(tmp, "z-later-333333", "blocked")
            original_load = state.load_state

            def load_with_denied_dir(repo_root, task_id):
                if task_id == "b-no-access-222222":
                    raise OSError(13, "权限不足（模拟）")
                return original_load(repo_root, task_id)

            with mock.patch.object(state, "load_state",
                                   side_effect=load_with_denied_dir):
                self.assertEqual(
                    state.find_active_tasks(tmp),
                    ["a-active-111111", "z-later-333333"])


# —— discover_tasks 四分类（H3/P0-3：发现完整性） ——

class DiscoverTasksTest(unittest.TestCase):
    """discover_tasks 四分类 + find_active_tasks 兼容等价（H3/P0-3）。

    损坏 / 缺标识 / 读取异常的 state.json 不再从发现阶段静默消失：
    归入 corrupt 桶并携带简短中文原因；无 state.json 的目录归入
    orphaned 桶。
    """

    def save(self, repo_root, task_id, status):
        # 与 FindActiveTasksTest.save 同链路：completed 走合法迁移链构造
        st = make_state(task_id=task_id, status=status)
        if status == "completed":
            st["status"] = "executing"
            state.save_state(repo_root, st)
            st["status"] = "finalizing"
            state.save_state(repo_root, st)
            state.commit_completion(repo_root, task_id)
            return
        state.save_state(repo_root, st)

    def test_four_classes_in_one_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.save(tmp, "a-active-111111", "executing")
            self.save(tmp, "b-completed-222222", "completed")
            self.save(tmp, "c-cancelled-333333", "cancelled")
            self.save(tmp, "d-failed-444444", "failed")
            # JSON 损坏 → corrupt（不再被跳过）
            write_raw_state(tmp, "e-corrupt-555555", "{broken")
            # JSON 合法但缺任务标识（normalize 抛 ValueError）→ corrupt
            write_raw_state(
                tmp, "f-no-id-666666", json.dumps({"goal": "缺标识"}))
            # 目录存在但无 state.json → orphaned
            state.task_dir(tmp, "g-empty-777777").mkdir(parents=True)
            found = state.discover_tasks(tmp)
            self.assertEqual(
                list(found.keys()),
                ["active", "terminal", "corrupt", "orphaned"])
            # active / terminal 的 reason 为 status 字符串
            self.assertEqual(found["active"], [("a-active-111111", "executing")])
            self.assertEqual(
                found["terminal"],
                [("b-completed-222222", "completed"),
                 ("c-cancelled-333333", "cancelled"),
                 ("d-failed-444444", "failed")])
            # corrupt / orphaned 的 reason 为简短中文原因
            self.assertEqual(
                [name for name, _ in found["corrupt"]],
                ["e-corrupt-555555", "f-no-id-666666"])
            for _name, reason in found["corrupt"]:
                self.assertTrue(reason.startswith("state.json 损坏："), reason)
            self.assertEqual(
                found["orphaned"], [("g-empty-777777", "无 state.json")])

    def test_tasks_root_missing_returns_all_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            found = state.discover_tasks(tmp)
            self.assertEqual(
                found,
                {"active": [], "terminal": [], "corrupt": [], "orphaned": []})

    def test_plain_file_under_tasks_root_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.save(tmp, "a-active-111111", "routed")
            (state.tasks_root(tmp) / "not-a-dir.txt").write_text(
                "杂项文件", encoding="utf-8")
            found = state.discover_tasks(tmp)
            self.assertEqual(
                [name for name, _ in found["active"]], ["a-active-111111"])
            self.assertEqual(found["corrupt"], [])
            self.assertEqual(found["orphaned"], [])
            self.assertEqual(found["terminal"], [])

    def test_corrupt_reason_truncated_within_80_chars(self):
        with tempfile.TemporaryDirectory() as tmp:
            state.task_dir(tmp, "x-corrupt-000000").mkdir(parents=True)
            bloated = ValueError("异常" * 200)
            with mock.patch.object(state, "load_state", side_effect=bloated):
                found = state.discover_tasks(tmp)
            self.assertEqual(
                [name for name, _ in found["corrupt"]], ["x-corrupt-000000"])
            reason = found["corrupt"][0][1]
            self.assertLessEqual(len(reason), 80)
            self.assertTrue(reason.startswith("state.json 损坏："), reason)

    def test_oserror_reason_says_unreadable(self):
        with tempfile.TemporaryDirectory() as tmp:
            state.task_dir(tmp, "y-denied-000000").mkdir(parents=True)
            with mock.patch.object(
                    state, "load_state",
                    side_effect=OSError(13, "权限不足（模拟）")):
                found = state.discover_tasks(tmp)
            self.assertEqual(
                [name for name, _ in found["corrupt"]], ["y-denied-000000"])
            self.assertTrue(
                found["corrupt"][0][1].startswith("state.json 不可读："),
                found["corrupt"][0][1])

    def test_find_active_tasks_equivalent_to_discover_active(self):
        # 兼容等价：混合目录（active/terminal/corrupt/orphaned/非目录）
        # 下 find_active_tasks 输出恒等于 discover_tasks 的 active 桶
        with tempfile.TemporaryDirectory() as tmp:
            self.save(tmp, "a-active-111111", "executing")
            self.save(tmp, "b-completed-222222", "completed")
            self.save(tmp, "z-later-333333", "blocked")
            write_raw_state(tmp, "m-corrupt-444444", "{broken")
            state.task_dir(tmp, "n-empty-555555").mkdir(parents=True)
            (state.tasks_root(tmp) / "stray.txt").write_text("x", encoding="utf-8")
            self.assertEqual(
                state.find_active_tasks(tmp),
                ["a-active-111111", "z-later-333333"])
            self.assertEqual(
                state.find_active_tasks(tmp),
                [name for name, _status in state.discover_tasks(tmp)["active"]])

    def test_find_active_tasks_equivalent_under_load_failure(self):
        # OSError 目录在两套发现口径下同样一致：不进 active、不中断扫描
        with tempfile.TemporaryDirectory() as tmp:
            self.save(tmp, "a-active-111111", "executing")
            self.save(tmp, "b-no-access-222222", "executing")
            original_load = state.load_state

            def load_with_denied_dir(repo_root, task_id):
                if task_id == "b-no-access-222222":
                    raise OSError(13, "权限不足（模拟）")
                return original_load(repo_root, task_id)

            with mock.patch.object(state, "load_state",
                                   side_effect=load_with_denied_dir):
                self.assertEqual(
                    state.find_active_tasks(tmp),
                    ["a-active-111111"])
                self.assertEqual(
                    state.find_active_tasks(tmp),
                    [name for name, _status in
                     state.discover_tasks(tmp)["active"]])
                # OSError 目录被 discover_tasks 显式归入 corrupt 桶
                self.assertEqual(
                    [name for name, _ in state.discover_tasks(tmp)["corrupt"]],
                    ["b-no-access-222222"])



class DispatchMaxWorkersBoundTest(unittest.TestCase):
    """dispatch.max_workers 上界校验（§82 并行上限，R9 终审 P2 修复）。"""

    def _dispatch_errors(self, max_workers):
        st = {"task_id": "t-1", "goal": "g", "route": {"mode": "solo"},
              "dispatch": {"max_workers": max_workers, "active": []},
              "status": "created"}
        return state.validate_state(st)

    def test_bound_1_and_4_pass(self):
        for n in (1, 4):
            self.assertEqual([e for e in self._dispatch_errors(n)
                              if "max_workers" in e], [])

    def test_bound_over_4_rejected(self):
        errors = self._dispatch_errors(5)
        self.assertTrue(any("超过并行上限" in e for e in errors), errors)

    def test_bool_and_zero_still_rejected(self):
        for bad in (True, 0, -1):
            errors = self._dispatch_errors(bad)
            self.assertTrue(any("max_workers" in e for e in errors), errors)


# —— 状态转换门（H1 生命周期封口，P0-1 修复） ——

class TaskTransitionGateTest(unittest.TestCase):
    """TASK_TRANSITIONS 转换表 + save_state / transition_task_status /
    commit_completion 的生命周期封口契约：completed 只能由完成门提交，
    finalizing 是唯一完成请求态。"""

    def _save_as(self, repo_root, status, task_id=TID):
        st = make_state(task_id=task_id, status=status)
        state.save_state(repo_root, st)
        return st

    # —— 转换表闭包 ——

    def test_transitions_table_closure(self):
        # 每个状态要么有表项要么是终态（终态无表项 = 不接受任何转换）
        for status in state.TASK_STATUSES:
            self.assertTrue(
                status in state.TASK_TRANSITIONS
                or status in state.TERMINAL_STATUSES, status)
        # 全部转换目标都是合法状态词汇
        for source, targets in state.TASK_TRANSITIONS.items():
            self.assertIn(source, state.TASK_STATUSES, source)
            for target in targets:
                self.assertIn(target, state.TASK_STATUSES, (source, target))

    def test_finalizing_in_vocabulary_nonterminal_and_reachable(self):
        self.assertIn("finalizing", state.TASK_STATUSES)
        self.assertNotIn("finalizing", state.TERMINAL_STATUSES)
        # 从全部执行/收尾态可达（blocked 解阻后同样可直达）
        for source in ("executing", "joining", "verifying", "reviewing",
                       "blocked"):
            self.assertIn("finalizing", state.TASK_TRANSITIONS[source], source)

    def test_completed_only_entry_edge_is_from_finalizing(self):
        # completed 的唯一入边是 finalizing → completed
        for source, targets in state.TASK_TRANSITIONS.items():
            if source == "finalizing":
                self.assertEqual(targets, ("completed", "failed", "cancelled"))
            else:
                self.assertNotIn("completed", targets, source)

    # —— save_state 转换门 ——

    def test_save_rejects_executing_to_completed(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._save_as(tmp, "executing")
            st = state.load_state(tmp, TID)
            st["status"] = "completed"
            with self.assertRaises(ValueError) as ctx:
                state.save_state(tmp, st)
            # 消息说明 completed 只能由完成门提交、应改走 finalizing
            self.assertIn("completed", str(ctx.exception))
            self.assertIn("finalizing", str(ctx.exception))
            # 盘上状态不变
            self.assertEqual(
                state.load_state(tmp, TID)["status"], "executing")

    def test_save_rejects_finalizing_to_completed_without_gate_channel(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._save_as(tmp, "finalizing")
            st = state.load_state(tmp, TID)
            st["status"] = "completed"
            with self.assertRaises(ValueError):
                state.save_state(tmp, st)  # 缺 _gate_commit（默认 False）
            self.assertEqual(
                state.load_state(tmp, TID)["status"], "finalizing")

    def test_save_gate_commit_requires_finalizing_on_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._save_as(tmp, "executing")
            st = state.load_state(tmp, TID)
            st["status"] = "completed"
            with self.assertRaises(ValueError):
                state.save_state(tmp, st, _gate_commit=True)
            self.assertEqual(
                state.load_state(tmp, TID)["status"], "executing")

    def test_first_save_completed_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                self._save_as(tmp, "completed")
            self.assertFalse(state.state_path(tmp, TID).exists())
            # 首存其他状态不受转换表约束（无盘上旧状态，无转换可言）
            self._save_as(tmp, "finalizing")
            self.assertEqual(
                state.load_state(tmp, TID)["status"], "finalizing")

    def test_same_status_resave_passes(self):
        # 旧 == 新（无转换）放行：record_* 之后重存状态的常规路径
        with tempfile.TemporaryDirectory() as tmp:
            self._save_as(tmp, "executing")
            st = state.load_state(tmp, TID)
            st["goal"] = "改目标不改状态"
            state.save_state(tmp, st)
            self.assertEqual(
                state.load_state(tmp, TID)["goal"], "改目标不改状态")

    def test_terminal_status_accepts_no_transition(self):
        with tempfile.TemporaryDirectory() as tmp:
            for terminal in state.TERMINAL_STATUSES:
                tid = "term-%s-000000" % terminal[:4]
                if terminal == "completed":
                    # completed 不能经公共 API 首存——走合法迁移链构造
                    # （executing→finalizing→完成门内部提交）
                    self._save_as(tmp, "executing", task_id=tid)
                    state.transition_task_status(tmp, tid, "finalizing")
                    state.commit_completion(tmp, tid)
                else:
                    self._save_as(tmp, terminal, task_id=tid)
                st = state.load_state(tmp, tid)
                st["status"] = "executing"
                with self.assertRaises(ValueError):
                    state.save_state(tmp, st)
                self.assertEqual(
                    state.load_state(tmp, tid)["status"], terminal, tid)

    def test_save_over_corrupt_file_propagates_value_error(self):
        # 盘上文件损坏：load_state 的 ValueError 向上传播，不覆盖损坏文件
        with tempfile.TemporaryDirectory() as tmp:
            self._save_as(tmp, "executing")
            path = state.state_path(tmp, TID)
            path.write_text("{corrupt", encoding="utf-8")
            with self.assertRaises(ValueError) as ctx:
                state.save_state(tmp, make_state(status="executing"))
            self.assertIn("损坏", str(ctx.exception))
            self.assertEqual(path.read_text(encoding="utf-8"), "{corrupt")

    # —— commit_completion（完成门专用提交通道） ——

    def test_commit_completion_commits_finalizing_to_completed(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._save_as(tmp, "finalizing")
            committed = state.commit_completion(tmp, TID)
            self.assertEqual(committed["status"], "completed")
            self.assertEqual(
                state.load_state(tmp, TID)["status"], "completed")
            # 本函数不写 journal（completed 事件由钩子记）
            self.assertEqual(journal.read_events(tmp, TID), [])

    def test_commit_completion_missing_task_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                state.commit_completion(tmp, "no-such-task-000000")

    def test_commit_completion_non_finalizing_raises_no_side_effect(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._save_as(tmp, "executing")
            with self.assertRaises(ValueError):
                state.commit_completion(tmp, TID)
            self.assertEqual(
                state.load_state(tmp, TID)["status"], "executing")

    # —— transition_task_status（公共迁移入口） ——

    def test_transition_legal_records_status_changed_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._save_as(tmp, "created")
            st = state.transition_task_status(tmp, TID, "executing")
            self.assertEqual(st["status"], "executing")
            self.assertEqual(
                state.load_state(tmp, TID)["status"], "executing")
            events = journal.read_events(tmp, TID)
            self.assertEqual(
                [e["event"] for e in events], ["status_changed"])
            self.assertEqual(events[0]["from"], "created")
            self.assertEqual(events[0]["to"], "executing")

    def test_transition_to_finalizing_requests_completion(self):
        # 进入 finalizing = 请求完成（created→…→reviewing→finalizing 合法）
        with tempfile.TemporaryDirectory() as tmp:
            self._save_as(tmp, "reviewing")
            state.transition_task_status(tmp, TID, "finalizing")
            self.assertEqual(
                state.load_state(tmp, TID)["status"], "finalizing")

    def test_transition_illegal_no_disk_change_no_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._save_as(tmp, "executing")
            with self.assertRaises(ValueError):
                state.transition_task_status(tmp, TID, "completed")
            with self.assertRaises(ValueError):
                state.transition_task_status(tmp, TID, "preflight")  # 逆向
            self.assertEqual(
                state.load_state(tmp, TID)["status"], "executing")
            self.assertEqual(journal.read_events(tmp, TID), [])

    def test_transition_missing_task_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                state.transition_task_status(tmp, TID, "executing")


# —— route 不变量（H2：跨字段一致性，P0-2 修复） ——

class RouteInvariantTest(unittest.TestCase):
    """validate_route_invariants 四条规则 + derive_review_required /
    new_task_state 的审查义务推导（非法组合在 save_state 即被拒，
    完成门不再依赖「模型记得把 review.required 写对」）。"""

    SOLO_ROUTE = {"mode": "solo", "delegability": "low",
                  "assurance": "standard", "executor": "main",
                  "continuity": "foreground"}
    DELEGATE_ROUTE = {"mode": "delegate", "delegability": "high",
                      "assurance": "standard", "executor": "flash-implementer",
                      "continuity": "foreground"}
    AUDIT_ROUTE = {"mode": "audit", "delegability": "low",
                   "assurance": "high", "executor": "main",
                   "continuity": "foreground"}

    def _substantive(self, **kwargs):
        """delegate/full 用例所需的实质性声明（R4）。"""
        kwargs.setdefault("ownership_files", ("src/auth.ts",))
        kwargs.setdefault("verification_required", ("python3 -m unittest",))
        return kwargs

    # —— R3 review 绑定（含保存被拒） ——

    def test_full_route_missing_review_invariant_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            st = state.new_task_state(TID, "目标", dict(FULL_ROUTE))
            st["review"]["required"] = False  # full 路由漏写审查义务
            errors = state.validate_state(st)
            self.assertTrue(
                any("review.required" in e for e in errors), errors)
            # save_state 拒绝非法组合且不落盘
            with self.assertRaises(ValueError) as ctx:
                state.save_state(tmp, st)
            self.assertIn("review.required", str(ctx.exception))
            self.assertFalse(state.state_path(tmp, TID).exists())

    def test_high_assurance_requires_review_required_true(self):
        # audit 路由（low+high）review.required=false 同样被拒
        st = state.new_task_state(TID, "目标", dict(self.AUDIT_ROUTE))
        st["review"]["required"] = False
        errors = state.validate_state(st)
        self.assertTrue(any("review.required" in e for e in errors), errors)

    # —— R1 矩阵一致性 ——

    def test_delegate_with_high_assurance_matrix_rejected(self):
        st = state.new_task_state(
            TID, "目标", dict(self.DELEGATE_ROUTE, assurance="high"),
            **self._substantive())
        errors = state.validate_state(st)
        self.assertTrue(
            any("矩阵组合不一致" in e and "应为 'full'" in e
                for e in errors), errors)

    def test_all_matrix_combinations_validate_clean(self):
        # 四个矩阵组合全部合法（validate_state 零错误）
        cases = (
            (self.SOLO_ROUTE, {}),
            (self.DELEGATE_ROUTE, self._substantive()),
            (self.AUDIT_ROUTE, {}),
            (dict(FULL_ROUTE, executor="visual-implementer"),
             self._substantive()),
        )
        for route, kwargs in cases:
            st = state.new_task_state("t-1", "目标", route, **kwargs)
            self.assertEqual(state.validate_state(st), [], route["mode"])

    # —— R2 executor 绑定 ——

    def test_solo_with_implementer_executor_rejected(self):
        st = state.new_task_state(
            TID, "目标", dict(self.SOLO_ROUTE, executor="flash-implementer"))
        errors = state.validate_state(st)
        self.assertTrue(any("route.executor" in e for e in errors), errors)

    def test_full_with_main_executor_rejected(self):
        st = state.new_task_state(
            TID, "目标", dict(FULL_ROUTE, executor="main"),
            **self._substantive())
        errors = state.validate_state(st)
        self.assertTrue(any("route.executor" in e for e in errors), errors)

    # —— R4 delegate 实质性 ——

    def test_delegate_requires_nonempty_ownership_and_verification(self):
        st = state.new_task_state(TID, "目标", dict(self.DELEGATE_ROUTE))
        errors = state.validate_state(st)
        self.assertTrue(any("ownership.files" in e for e in errors), errors)
        self.assertTrue(
            any("verification.required" in e for e in errors), errors)
        # ownership 块整体缺失同样按缺声明拒绝
        del st["ownership"]
        self.assertTrue(any(
            "ownership.files" in e for e in state.validate_state(st)))
        # 只补 ownership 仍缺 verification
        st2 = state.new_task_state(
            TID, "目标", dict(self.DELEGATE_ROUTE),
            ownership_files=("src/auth.ts",))
        self.assertTrue(any(
            "verification.required" in e
            for e in state.validate_state(st2)))

    # —— new_task_state 派生审查义务 ——

    def test_new_task_state_derives_review_required_default(self):
        # full / audit → True（不显式传 review_required）
        for route in (dict(FULL_ROUTE), dict(self.AUDIT_ROUTE)):
            st = state.new_task_state("t-1", "目标", route)
            self.assertIs(st["review"]["required"], True, route["mode"])
        # delegate + standard 与 solo（信息不足）→ False
        st = state.new_task_state("t-1", "目标", dict(self.DELEGATE_ROUTE))
        self.assertIs(st["review"]["required"], False)
        st = state.new_task_state("t-1", "目标", {"mode": "solo"})
        self.assertIs(st["review"]["required"], False)

    def test_explicit_review_required_true_legal_under_delegate_standard(self):
        # 显式 True 恒合法——比推导更严
        st = state.new_task_state(
            TID, "目标", dict(self.DELEGATE_ROUTE),
            ownership_files=("src/auth.ts",),
            verification_required=("python3 -m unittest",),
            review_required=True, reviewer="glm-reviewer")
        self.assertEqual(state.validate_state(st), [])

    # —— derive_review_required 直测 ——

    def test_derive_review_required_truth_table(self):
        self.assertIs(state.derive_review_required({"mode": "audit"}), True)
        self.assertIs(state.derive_review_required({"mode": "full"}), True)
        self.assertIs(state.derive_review_required(
            {"mode": "solo", "assurance": "high"}), True)
        self.assertIs(state.derive_review_required(
            {"mode": "delegate", "assurance": "standard"}), False)
        self.assertIs(state.derive_review_required(
            {"mode": "solo", "assurance": "standard"}), False)
        # 信息不足 / route 非 dict → None
        for info_poor in ({"mode": "solo"}, {"mode": "delegate"},
                          {"mode": "solo", "assurance": None}, {},
                          None, "solo", 42):
            self.assertIsNone(
                state.derive_review_required(info_poor), repr(info_poor))


if __name__ == "__main__":
    unittest.main()
