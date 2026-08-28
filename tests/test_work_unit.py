#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""runtime.work_unit 单元测试（v2 工作块 B8.1）。

仅 Python 3 标准库（unittest + json），零第三方依赖，零 I/O
（JSON 往返只走内存 dump/load，不落盘）。

覆盖：
    - 构造（默认 / 全参 / 列表副本 / 形状校验报错分支）；
    - validate_work_unit 全错误分支（缺键逐个、空 ownership /
      verification、词汇外 status / executor、attempt 负数 / bool、
      未知键忽略、多错误聚合）；
    - 转换表逐边锚定（表内容逐字锁定 + 26 条合法边逐一断言 +
      每个非终态至少一条非法边 + 终态全封锁 + 错误消息含
      from/to 与合法目标清单）；
    - record_attempt 递增与 outcome 累积（含旧 result 非 dict 与
      dict 内 attempt_outcomes 非 list 的历史保留起始路径）；
    - JSON dump/load 往返后仍通过校验。

运行：
    cd <repo_root> && python3 -m unittest tests.test_work_unit -v
"""

import sys, unittest
import json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "glm-conductor"))
from runtime import work_unit
from runtime import state


# —— 测试夹具 ——

def make_wu(uid="wu-auth-tests", objective="补齐认证中间件回归测试",
            **overrides):
    """构造一个默认合法的 work unit dict（依赖 wu-auth-core 仅为占位）。"""
    kwargs = dict(
        executor="flash-implementer",
        ownership=("tests/auth/**",),
        verification=("python3 -m unittest tests.test_auth",),
        depends_on=("wu-auth-core",),
    )
    kwargs.update(overrides)
    return work_unit.new_work_unit(uid, objective, **kwargs)


# —— 词汇表常量 ——

class ConstantsTest(unittest.TestCase):

    def test_status_vocabulary_verbatim(self):
        self.assertEqual(work_unit.WORK_UNIT_STATUSES, (
            "pending", "waiting_dependency", "ready", "running",
            "waiting_quota", "blocked", "verifying",
            "completed", "failed", "cancelled"))

    def test_terminal_statuses(self):
        self.assertEqual(work_unit.WU_TERMINAL_STATUSES,
                         ("completed", "failed", "cancelled"))
        for status in work_unit.WU_TERMINAL_STATUSES:
            self.assertIn(status, work_unit.WORK_UNIT_STATUSES)

    def test_required_and_optional_keys(self):
        self.assertEqual(work_unit.WU_REQUIRED_KEYS, (
            "id", "objective", "status", "depends_on", "executor",
            "ownership", "verification"))
        self.assertEqual(work_unit.WU_OPTIONAL_KEYS, (
            "interfaces", "constraints", "attempt", "result"))

    def test_executors_vocabulary_matches_state_layer(self):
        self.assertEqual(work_unit.WU_EXECUTORS,
                         ("main", "flash-implementer", "visual-implementer"))
        # 与状态层 EXECUTORS 取值一致（各自独立声明，避免反向依赖）
        self.assertEqual(work_unit.WU_EXECUTORS, state.EXECUTORS)


# —— new_work_unit ——

class NewWorkUnitTest(unittest.TestCase):

    def test_defaults(self):
        w = make_wu()
        self.assertEqual(w["id"], "wu-auth-tests")
        self.assertEqual(w["objective"], "补齐认证中间件回归测试")
        self.assertEqual(w["status"], "pending")
        self.assertEqual(w["depends_on"], ["wu-auth-core"])
        self.assertEqual(w["executor"], "flash-implementer")
        self.assertEqual(w["ownership"], ["tests/auth/**"])
        self.assertEqual(w["interfaces"], [])
        self.assertEqual(w["constraints"], [])
        self.assertEqual(w["verification"],
                         ["python3 -m unittest tests.test_auth"])
        self.assertEqual(w["attempt"], 0)
        self.assertIsNone(w["result"])
        self.assertEqual(work_unit.validate_work_unit(w), [])

    def test_full_params(self):
        w = work_unit.new_work_unit(
            "wu-docs", "重写部署文档", executor="main",
            ownership=("docs/deploy.md",),
            verification=("python3 -m mkdocs build",),
            depends_on=(), status="ready",
            interfaces=("CLI 入口名不变",), constraints=("零新依赖",),
            attempt=2, result={"attempt_outcomes": ["首次草稿被驳回"]})
        self.assertEqual(w["status"], "ready")
        self.assertEqual(w["interfaces"], ["CLI 入口名不变"])
        self.assertEqual(w["constraints"], ["零新依赖"])
        self.assertEqual(w["attempt"], 2)
        self.assertEqual(w["result"], {"attempt_outcomes": ["首次草稿被驳回"]})
        self.assertEqual(work_unit.validate_work_unit(w), [])

    def test_input_lists_copied(self):
        depends_on = ["wu-a"]
        ownership = ["src/a.py"]
        verification = ["python3 -m pytest"]
        w = work_unit.new_work_unit(
            "wu-1", "目标", executor="main", ownership=ownership,
            verification=verification, depends_on=depends_on)
        w["depends_on"].append("wu-b")
        w["ownership"].append("src/b.py")
        w["verification"].append("python3 -m ruff check .")
        # 列表入参不被返回 dict 的可变操作波及
        self.assertEqual(depends_on, ["wu-a"])
        self.assertEqual(ownership, ["src/a.py"])
        self.assertEqual(verification, ["python3 -m pytest"])

    def test_uid_type_and_empty(self):
        for bad in (None, 42, ["wu-1"], {"id": "wu-1"}):
            with self.subTest(uid=bad):
                with self.assertRaises(TypeError):
                    work_unit.new_work_unit(
                        bad, "目标", executor="main",
                        ownership=(), verification=())
        with self.assertRaises(ValueError):
            work_unit.new_work_unit(
                "", "目标", executor="main", ownership=(), verification=())

    def test_executor_type_empty_and_vocabulary(self):
        for bad_type in (None, 42, ["main"]):
            with self.subTest(executor=bad_type):
                with self.assertRaises(TypeError):
                    work_unit.new_work_unit(
                        "wu-1", "目标", executor=bad_type,
                        ownership=(), verification=())
        with self.assertRaises(ValueError):
            work_unit.new_work_unit(
                "wu-1", "目标", executor="", ownership=(), verification=())
        # 词汇外 executor：ValueError 且消息含合法取值清单
        with self.assertRaises(ValueError) as ctx:
            work_unit.new_work_unit(
                "wu-1", "目标", executor="wizard-agent",
                ownership=(), verification=())
        for allowed in work_unit.WU_EXECUTORS:
            self.assertIn(allowed, str(ctx.exception))

    def test_list_args_type_and_items(self):
        with self.assertRaises(TypeError):
            make_wu(ownership="src/a.py")
        with self.assertRaises(TypeError):
            make_wu(verification=None)
        for bad_lists in ({"depends_on": ("",)},
                          {"ownership": ("src/a.py", 42)},
                          {"verification": (None,)},
                          {"interfaces": [""]},
                          {"constraints": [["x"]]}):
            (key, value), = bad_lists.items()
            with self.subTest(key=key):
                with self.assertRaises(ValueError):
                    make_wu(**{key: value})

    def test_empty_ownership_allowed_at_construction_flagged_by_validation(self):
        # 构造从宽：空 ownership 能构造；「必填非空数组」由校验从严落地
        w = make_wu(ownership=())
        self.assertEqual(w["ownership"], [])
        self.assertEqual(work_unit.validate_work_unit(w),
                         ["ownership 不能为空数组（无文件范围的单元不可派发）"])


# —— validate_work_unit ——

class ValidateWorkUnitTest(unittest.TestCase):

    def test_valid_default(self):
        self.assertEqual(work_unit.validate_work_unit(make_wu()), [])

    def test_non_dict(self):
        for bad in (None, [], "wu", 42):
            self.assertEqual(work_unit.validate_work_unit(bad),
                             ["work_unit 必须是 JSON 对象"], bad)

    def test_missing_each_required_key(self):
        for key in work_unit.WU_REQUIRED_KEYS:
            w = make_wu()
            del w[key]
            errors = work_unit.validate_work_unit(w)
            self.assertTrue(
                any("缺少必填键 %s" % key in e for e in errors), key)

    def test_empty_id_and_objective(self):
        # 构造器会拒绝空 uid，这里直接变异 dict 走校验分支
        w = make_wu()
        w["id"] = ""
        self.assertTrue(any("id 必须是非空字符串" in e
                            for e in work_unit.validate_work_unit(w)))
        w = make_wu(objective="")
        self.assertTrue(any("objective 必须是非空字符串" in e
                            for e in work_unit.validate_work_unit(w)))

    def test_non_str_id_and_objective(self):
        w = make_wu()
        w["id"] = 42
        w["objective"] = ["目标"]
        errors = work_unit.validate_work_unit(w)
        self.assertTrue(any(e == "id 必须是非空字符串" for e in errors))
        self.assertTrue(any(e == "objective 必须是非空字符串" for e in errors))

    def test_status_out_of_vocabulary(self):
        for bad in ("executing", "done", "", 42, None):
            w = make_wu()
            w["status"] = bad
            errors = work_unit.validate_work_unit(w)
            self.assertTrue(any(e.startswith("status") for e in errors), bad)
            # 错误消息含合法取值清单
            for allowed in work_unit.WORK_UNIT_STATUSES:
                self.assertTrue(
                    any(allowed in e for e in errors
                        if e.startswith("status")), (bad, allowed))

    def test_executor_out_of_vocabulary(self):
        w = make_wu()
        w["executor"] = "wizard-agent"
        errors = work_unit.validate_work_unit(w)
        self.assertTrue(any("executor" in e and "wizard-agent" in e
                            for e in errors))

    def test_executor_non_str(self):
        w = make_wu()
        w["executor"] = 42
        self.assertTrue(any(e == "executor 必须是非空字符串"
                            for e in work_unit.validate_work_unit(w)))

    def test_depends_on_may_be_empty(self):
        w = make_wu(depends_on=())
        self.assertEqual(work_unit.validate_work_unit(w), [])

    def test_depends_on_bad_items(self):
        w = make_wu()
        w["depends_on"] = ["wu-a", "", 42, None]
        errors = work_unit.validate_work_unit(w)
        self.assertTrue(any("depends_on[1]" in e for e in errors))
        self.assertTrue(any("depends_on[2]" in e for e in errors))
        self.assertTrue(any("depends_on[3]" in e for e in errors))

    def test_depends_on_not_list(self):
        w = make_wu()
        w["depends_on"] = "wu-a"
        self.assertTrue(any(e == "depends_on 必须是数组"
                            for e in work_unit.validate_work_unit(w)))

    def test_empty_ownership_and_verification_rejected(self):
        w = make_wu(ownership=())
        self.assertTrue(any("ownership 不能为空数组" in e
                            for e in work_unit.validate_work_unit(w)))
        w = make_wu(verification=())
        self.assertTrue(any("verification 不能为空数组" in e
                            for e in work_unit.validate_work_unit(w)))

    def test_ownership_verification_not_list_and_bad_items(self):
        w = make_wu()
        w["ownership"] = "src/a.py"
        w["verification"] = ["ok", ""]
        errors = work_unit.validate_work_unit(w)
        self.assertTrue(any(e == "ownership 必须是数组" for e in errors))
        self.assertTrue(any("verification[1]" in e for e in errors))

    def test_attempt_negative_bool_and_garbage(self):
        for bad in (-1, True, False, "1", 1.5, None):
            w = make_wu()
            w["attempt"] = bad
            errors = work_unit.validate_work_unit(w)
            self.assertTrue(
                any(e == "attempt 必须是 >= 0 的整数" for e in errors), bad)

    def test_attempt_zero_and_positive_ok(self):
        for good in (0, 1, 99):
            w = make_wu(attempt=good)
            self.assertEqual(work_unit.validate_work_unit(w), [], good)

    def test_result_any_json_value_or_none(self):
        for value in (None, "文本", 0, 3.14, True, [1, 2],
                      {"attempt_outcomes": ["x"]}, {"任意": {"嵌套": None}}):
            w = make_wu(result=value)
            self.assertEqual(work_unit.validate_work_unit(w), [], value)

    def test_unknown_keys_ignored(self):
        w = make_wu()
        w["lease"] = {"holder": "main"}
        w["future_field"] = {"whatever": [1, 2, 3]}
        self.assertEqual(work_unit.validate_work_unit(w), [])

    def test_multiple_errors_aggregated_not_short_circuit(self):
        # 构造合法单元后逐字段变异，确保校验层聚合全部错误
        w = make_wu()
        w["id"] = ""
        w["executor"] = "wizard-agent"
        w["ownership"] = []
        del w["verification"]
        w["attempt"] = -1
        w["status"] = "done"
        errors = work_unit.validate_work_unit(w)
        # id + executor + ownership + 缺 verification + attempt + status
        self.assertGreaterEqual(len(errors), 6)


# —— transition_work_unit：转换表锚定与逐边覆盖 ——

class TransitionTableTest(unittest.TestCase):

    def test_table_locked_verbatim(self):
        self.assertEqual(work_unit.WU_TRANSITIONS, {
            "pending": ("waiting_dependency", "ready", "cancelled"),
            "waiting_dependency": ("ready", "blocked", "cancelled"),
            "ready": ("running", "waiting_quota", "blocked", "cancelled"),
            "waiting_quota": ("ready", "blocked", "cancelled"),
            "blocked": ("ready", "failed", "cancelled"),
            "running": ("verifying", "ready", "blocked", "waiting_quota",
                        "failed", "completed", "cancelled"),
            "verifying": ("completed", "failed", "cancelled"),
        })

    def test_every_status_has_disposition(self):
        # 词汇表中的每个状态要么有转换表项，要么是终态，无遗漏
        covered = set(work_unit.WU_TRANSITIONS) \
            | set(work_unit.WU_TERMINAL_STATUSES)
        self.assertEqual(covered, set(work_unit.WORK_UNIT_STATUSES))

    def test_terminal_states_never_start_transitions(self):
        # 终态不是任何转换的起点（无表项）；cancelled / completed /
        # failed 可以作为非终态的合法目标（§62：any nonterminal →
        # cancelled；running → completed 与 blocked/running → failed）
        for terminal in work_unit.WU_TERMINAL_STATUSES:
            self.assertNotIn(terminal, work_unit.WU_TRANSITIONS)
        self.assertIn("cancelled", work_unit.WU_TRANSITIONS["verifying"])


class TransitionLegalEdgesTest(unittest.TestCase):

    def test_every_legal_edge_one_by_one(self):
        total = 0
        for from_status, targets in work_unit.WU_TRANSITIONS.items():
            for target in targets:
                total += 1
                with self.subTest(edge="%s -> %s" % (from_status, target)):
                    w = make_wu()
                    w["status"] = from_status
                    result = work_unit.transition_work_unit(w, target)
                    self.assertIs(result, w)  # 就地更新并返回同一 dict
                    self.assertEqual(w["status"], target)
                    self.assertEqual(w["id"], "wu-auth-tests")  # 其余键不动
        # §62 转换表共 26 条合法边
        self.assertEqual(total, 26)

    def test_recommended_main_chain_end_to_end(self):
        # §62 推荐主链：pending → waiting_dependency → ready → running
        # → verifying → completed
        w = make_wu()
        for step in ("waiting_dependency", "ready", "running",
                     "verifying", "completed"):
            work_unit.transition_work_unit(w, step)
        self.assertEqual(w["status"], "completed")

    def test_quota_bypass_chain(self):
        # ready → waiting_quota → ready → running → failed
        w = make_wu()
        work_unit.transition_work_unit(w, "ready")
        work_unit.transition_work_unit(w, "waiting_quota")
        work_unit.transition_work_unit(w, "ready")
        work_unit.transition_work_unit(w, "running")
        work_unit.transition_work_unit(w, "failed")
        self.assertEqual(w["status"], "failed")

    def test_blocked_chain_from_running(self):
        # running → blocked → failed（§62 回退/判死边）
        w = make_wu()
        work_unit.transition_work_unit(w, "ready")
        work_unit.transition_work_unit(w, "running")
        work_unit.transition_work_unit(w, "blocked")
        work_unit.transition_work_unit(w, "failed")
        self.assertEqual(w["status"], "failed")


class TransitionIllegalEdgesTest(unittest.TestCase):

    def test_each_nonterminal_state_has_illegal_edge(self):
        illegal_pick = {
            "pending": "running",
            "waiting_dependency": "running",
            "ready": "completed",
            "waiting_quota": "running",
            "blocked": "running",
            "running": "pending",
            "verifying": "running",
        }
        for from_status, target in illegal_pick.items():
            with self.subTest(edge="%s -> %s" % (from_status, target)):
                w = make_wu()
                w["status"] = from_status
                with self.assertRaises(ValueError) as ctx:
                    work_unit.transition_work_unit(w, target)
                message = str(ctx.exception)
                self.assertIn(from_status, message)   # 消息含 from
                self.assertIn(target, message)        # 消息含 to
                for legal in work_unit.WU_TRANSITIONS[from_status]:
                    self.assertIn(legal, message)     # 消息含合法目标清单
                self.assertEqual(w["status"], from_status)  # 失败无副作用

    def test_terminal_states_accept_no_transition(self):
        for terminal in work_unit.WU_TERMINAL_STATUSES:
            for target in work_unit.WORK_UNIT_STATUSES:
                with self.subTest(edge="%s -> %s" % (terminal, target)):
                    w = make_wu()
                    w["status"] = terminal
                    with self.assertRaises(ValueError) as ctx:
                        work_unit.transition_work_unit(w, target)
                    message = str(ctx.exception)
                    self.assertIn(terminal, message)
                    self.assertIn(target, message)
                    self.assertIn("终态", message)
                    self.assertEqual(w["status"], terminal)

    def test_unknown_target_status_rejected(self):
        w = make_wu()
        work_unit.transition_work_unit(w, "ready")
        with self.assertRaises(ValueError):
            work_unit.transition_work_unit(w, "executing")

    def test_missing_current_status_rejected(self):
        w = make_wu()
        del w["status"]
        with self.assertRaises(ValueError):
            work_unit.transition_work_unit(w, "ready")

    def test_garbage_current_status_rejected(self):
        w = make_wu()
        w["status"] = "flying"
        with self.assertRaises(ValueError):
            work_unit.transition_work_unit(w, "ready")

    def test_non_dict_input_rejected(self):
        for bad in (None, [], "wu", 42):
            with self.subTest(wu=bad):
                with self.assertRaises(ValueError):
                    work_unit.transition_work_unit(bad, "ready")


# —— record_attempt ——

class RecordAttemptTest(unittest.TestCase):

    def test_increment_only_keeps_result_none(self):
        w = make_wu()
        result = work_unit.record_attempt(w)
        self.assertIs(result, w)
        self.assertEqual(w["attempt"], 1)
        self.assertIsNone(w["result"])

    def test_attempt_increments_from_existing_value(self):
        w = make_wu(attempt=4)
        work_unit.record_attempt(w)
        self.assertEqual(w["attempt"], 5)

    def test_outcome_starts_result_dict(self):
        w = make_wu()
        work_unit.record_attempt(w, outcome={"error": "工具超时"})
        self.assertEqual(w["attempt"], 1)
        self.assertEqual(w["result"],
                         {"attempt_outcomes": [{"error": "工具超时"}]})

    def test_outcomes_accumulate_across_attempts(self):
        w = make_wu()
        work_unit.record_attempt(w, outcome="第一次失败")
        work_unit.record_attempt(w, outcome={"error": "第二次失败"})
        self.assertEqual(w["attempt"], 2)
        self.assertEqual(
            w["result"],
            {"attempt_outcomes": ["第一次失败", {"error": "第二次失败"}]})

    def test_old_non_dict_result_preserved_as_history(self):
        # §73：新 worker 调用不抹除失败历史——旧自由 JSON 值保留为首条
        for old in ("自由文本报告", 42, ["旧结果"], True):
            w = make_wu()
            w["result"] = old
            work_unit.record_attempt(w, outcome={"stage": "retry"})
            with self.subTest(old=old):
                self.assertEqual(
                    w["result"],
                    {"attempt_outcomes": [old, {"stage": "retry"}]})

    def test_old_dict_result_keeps_other_keys(self):
        w = make_wu()
        w["result"] = {"summary": "部分完成"}
        work_unit.record_attempt(w, outcome="补做")
        self.assertEqual(w["result"],
                         {"summary": "部分完成", "attempt_outcomes": ["补做"]})

    def test_old_dict_result_existing_outcomes_appended(self):
        w = make_wu()
        w["result"] = {"attempt_outcomes": ["旧失败"]}
        work_unit.record_attempt(w, outcome="新失败")
        self.assertEqual(w["result"]["attempt_outcomes"], ["旧失败", "新失败"])

    def test_old_dict_non_list_outcomes_preserved_not_dropped(self):
        # P2#4：result 为 dict 但 attempt_outcomes 为非 list 值时，
        # 旧值不静默丢弃——保留为失败历史首条再追加（与「旧 result
        # 非 dict」的起始路径同构），其他键原样保留
        for old in ("自由文本报告", 42, {"旧": "形状"}, True):
            w = make_wu()
            w["result"] = {"attempt_outcomes": old, "summary": "历史"}
            work_unit.record_attempt(w, outcome="新结果")
            with self.subTest(old=old):
                self.assertEqual(w["attempt"], 1)
                self.assertEqual(
                    w["result"],
                    {"attempt_outcomes": [old, "新结果"], "summary": "历史"})

    def test_outcomes_accumulate_after_non_list_value_repair(self):
        # 非 list 旧值保留为首条后，后续 outcome 在 repaired 列表上正常累积
        w = make_wu()
        w["result"] = {"attempt_outcomes": "首次的字符串报告"}
        work_unit.record_attempt(w, outcome="第二次失败")
        work_unit.record_attempt(w, outcome="第三次失败")
        self.assertEqual(
            w["result"]["attempt_outcomes"],
            ["首次的字符串报告", "第二次失败", "第三次失败"])
        self.assertEqual(w["attempt"], 2)

    def test_outcome_none_does_not_touch_result(self):
        w = make_wu()
        w["result"] = {"summary": "保持原样"}
        work_unit.record_attempt(w)
        self.assertEqual(w["result"], {"summary": "保持原样"})

    def test_bad_attempt_type_raises_no_side_effect(self):
        for bad in (-1, True, "1", 1.5, None):
            w = make_wu()
            w["attempt"] = bad
            with self.subTest(attempt=bad):
                with self.assertRaises(ValueError):
                    work_unit.record_attempt(w)
        self.assertEqual(w["attempt"], bad)  # 最后一次失败也无副作用

    def test_non_dict_input_rejected(self):
        with self.assertRaises(ValueError):
            work_unit.record_attempt(None)


# —— JSON 往返 ——

class JsonRoundtripTest(unittest.TestCase):

    def test_default_roundtrip_validates(self):
        w = make_wu()
        loaded = json.loads(json.dumps(w, ensure_ascii=False))
        self.assertEqual(loaded, w)
        self.assertEqual(work_unit.validate_work_unit(loaded), [])

    def test_full_roundtrip_validates(self):
        w = make_wu(
            status="waiting_dependency",
            interfaces=("公共 auth 中间件行为不变",),
            constraints=("不得改动 src/auth/**",),
            attempt=2, result={"attempt_outcomes": ["首次失败"]})
        loaded = json.loads(json.dumps(w, ensure_ascii=False))
        self.assertEqual(loaded, w)
        self.assertEqual(work_unit.validate_work_unit(loaded), [])

    def test_running_unit_with_history_roundtrip_validates(self):
        w = make_wu(status="running", attempt=1,
                    result={"attempt_outcomes": ["首次被阻断"]})
        work_unit.record_attempt(w, outcome={"error": "重试仍失败"})
        loaded = json.loads(json.dumps(w, ensure_ascii=False))
        self.assertEqual(work_unit.validate_work_unit(loaded), [])
        self.assertEqual(loaded["attempt"], 2)


if __name__ == "__main__":
    unittest.main()
