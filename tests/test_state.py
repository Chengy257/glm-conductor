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
from runtime import state

# —— 测试夹具 ——

TID = "demo-task-1a2b3c"
FULL_ROUTE = {
    "mode": "full",
    "delegability": "high",
    "assurance": "standard",
    "executor": "flash-implementer",
    "continuity": "foreground",
}


def make_state(task_id=TID, goal="重构认证中间件", route=None, status="created",
               **kwargs):
    """构造一个默认合法的完整状态 dict（route 缺省用 FULL_ROUTE）。"""
    if route is None:
        route = dict(FULL_ROUTE)
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

    def test_task_id_param_overrides(self):
        with tempfile.TemporaryDirectory() as tmp:
            st = make_state()
            path = state.save_state(tmp, st, task_id="demo-task-9f8e7d")
            self.assertEqual(path, state.state_path(tmp, "demo-task-9f8e7d"))
            self.assertTrue(path.is_file())
            self.assertFalse(state.state_path(tmp, TID).exists())
            self.assertEqual(state.load_state(tmp, "demo-task-9f8e7d"), st)

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
        state.save_state(repo_root, make_state(task_id=task_id, status=status))

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


if __name__ == "__main__":
    unittest.main()
