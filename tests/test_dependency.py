#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""runtime.dependency 单元测试（v2 工作块 B8.2）。

仅 Python 3 标准库（unittest + tempfile），零第三方依赖；
 tempfile 只用于 state 集成的 save/load 往返，不污染真实目录。

覆盖：
    - graph_errors：空图 / 单单元 / 重复 id / 未知引用 / 自依赖 /
      环（A→B→C→A 报全部三个 id）/ depends_on 项非法 / 非 dict 项；
    - deps_satisfied：completed 才算满足，failed / blocked / pending /
      引用缺失 / 非 dict 均不满足；
    - ready_units：status == ready 且依赖满足的过滤与输入顺序；
    - downstream：传递闭包（含分叉汇聚，§63 例图）与拓扑序输出；
    - topo_order：确定性（同层按 id 排序、输入顺序无关）与环抛错；
    - state.validate_state 集成：含合法 work_units 的 state 通过、
      含非法项的报 work_units[i] 前缀错误并聚合不短路、save/load
      往返后仍合法。
      （规格允许放进 test_state.py 或本文件二选一：本工作块约束
      只允许新建 test_work_unit.py / test_dependency.py、不允许改
      test_state.py，故集成用例落在本文件。）

运行：
    cd <repo_root> && python3 -m unittest tests.test_dependency -v
"""

import sys, unittest
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "glm-conductor"))
from runtime import dependency
from runtime import state
from runtime import work_unit


# —— 测试夹具 ——

def u(uid, deps=(), status="pending"):
    """构造最小 work unit dict（dependency 层只依赖三个键）。"""
    return {"id": uid, "depends_on": list(deps), "status": status}


def by_id(units):
    """构造 id → unit 映射（deps_satisfied 的 units_by_id 入参）。"""
    return {unit["id"]: unit for unit in units}


def guide_graph():
    """§63 例图：A、B → C → E；D → E（键为单位 id，值为依赖列表）。"""
    return [u("c", ("a", "b")),
            u("e", ("c", "d")),
            u("a"),
            u("b"),
            u("d")]


def valid_wu(uid="wu-a", deps=(), status="pending"):
    """构造一个通过 validate_work_unit 的完整 work unit（state 集成用）。"""
    return work_unit.new_work_unit(
        uid, "目标 %s" % uid, executor="flash-implementer",
        ownership=("src/%s/**" % uid,), verification=("python3 -m unittest",),
        depends_on=deps, status=status)


# —— graph_errors ——

class GraphErrorsTest(unittest.TestCase):

    def test_empty_graph_valid(self):
        self.assertEqual(dependency.graph_errors([]), [])

    def test_single_unit_valid(self):
        self.assertEqual(dependency.graph_errors([u("a")]), [])

    def test_guide_example_graph_valid(self):
        self.assertEqual(dependency.graph_errors(guide_graph()), [])

    def test_diamond_graph_valid(self):
        units = [u("a"), u("b", ("a",)), u("c", ("a",)), u("d", ("b", "c"))]
        self.assertEqual(dependency.graph_errors(units), [])

    def test_non_list_input(self):
        self.assertEqual(dependency.graph_errors({"id": "a"}),
                         ["units 必须是数组"])

    def test_duplicate_id(self):
        errors = dependency.graph_errors([u("a"), u("b"), u("a")])
        self.assertTrue(any("重复 id" in e and "'a'" in e for e in errors))

    def test_unknown_reference(self):
        errors = dependency.graph_errors([u("a", ("ghost",))])
        self.assertTrue(any("引用不存在的 id" in e and "ghost" in e
                            for e in errors))

    def test_self_dependency(self):
        errors = dependency.graph_errors([u("a", ("a",))])
        self.assertTrue(any("自依赖" in e and "'a'" in e for e in errors))

    def test_cycle_reports_all_members_sorted(self):
        # A→B→C→A：报出全部三个环成员（按 id 排序），不遗漏
        units = [u("c", ("b",)), u("a", ("c",)), u("b", ("a",))]
        errors = dependency.graph_errors(units)
        cycle_errors = [e for e in errors if "环" in e]
        self.assertEqual(len(cycle_errors), 1)
        message = cycle_errors[0]
        for member in ("a", "b", "c"):
            self.assertIn(member, message, member)
        self.assertLess(message.index("a"),
                        min(message.index("b"), message.index("c")))

    def test_cycle_excludes_downstream_unit(self):
        # 下游 zz 依赖环成员 a，但 zz 自身不在环上：唯一错误是环错误，
        # 且环成员清单只含 a, b, c（"d" 与 "id" 子串碰撞，改用 zz）
        units = [u("a", ("c",)), u("b", ("a",)), u("c", ("b",)),
                 u("zz", ("a",))]
        errors = dependency.graph_errors(units)
        self.assertEqual(len(errors), 1)          # 全部引用存在，无其他错误
        self.assertIn("环", errors[0])
        self.assertIn("a, b, c", errors[0])       # 环成员按 id 排序
        self.assertNotIn("zz", errors[0])         # 下游单元不算环成员

    def test_depends_on_item_empty_and_non_str(self):
        errors = dependency.graph_errors([u("a", ("", 42, None))])
        self.assertTrue(any("depends_on[0]" in e for e in errors))
        self.assertTrue(any("depends_on[1]" in e for e in errors))
        self.assertTrue(any("depends_on[2]" in e for e in errors))

    def test_depends_on_not_list(self):
        errors = dependency.graph_errors([{"id": "a", "depends_on": "b"}])
        self.assertTrue(
            any(e == "units[0].depends_on 必须是数组" for e in errors))

    def test_non_dict_entry(self):
        errors = dependency.graph_errors(["junk", u("a")])
        self.assertTrue(
            any(e == "units[0] 必须是 JSON 对象" for e in errors))

    def test_missing_or_empty_id(self):
        errors = dependency.graph_errors([{"depends_on": []}, u("")])
        self.assertTrue(any("units[0].id" in e for e in errors))
        self.assertTrue(any("units[1].id" in e for e in errors))

    def test_errors_aggregated_not_short_circuit(self):
        units = [u(""), u("a", ("ghost",)), u("a", ())]
        errors = dependency.graph_errors(units)
        self.assertGreaterEqual(len(errors), 3)  # 空 id + 未知引用 + 重复 id


# —— deps_satisfied ——

class DepsSatisfiedTest(unittest.TestCase):

    def test_no_deps_satisfied(self):
        self.assertTrue(dependency.deps_satisfied(u("a"), by_id([])))

    def test_unit_without_depends_on_key(self):
        self.assertTrue(dependency.deps_satisfied(
            {"id": "a", "status": "ready"}, {}))

    def test_all_completed_satisfied(self):
        units = [u("a", status="completed"), u("b", status="completed"),
                 u("c", ("a", "b"), status="ready")]
        self.assertTrue(dependency.deps_satisfied(u("c", ("a", "b")),
                                                  by_id(units)))

    def test_non_completed_dependency_not_satisfied(self):
        for dep_status in ("pending", "waiting_dependency", "ready",
                           "running", "waiting_quota", "blocked",
                           "verifying", "failed", "cancelled"):
            units = [u("a", status=dep_status), u("b", ("a",))]
            with self.subTest(dep_status=dep_status):
                self.assertFalse(
                    dependency.deps_satisfied(u("b", ("a",)), by_id(units)))

    def test_missing_reference_not_satisfied(self):
        self.assertFalse(dependency.deps_satisfied(
            u("b", ("ghost",)), by_id([u("a", status="completed")])))

    def test_partial_satisfaction_not_satisfied(self):
        units = [u("a", status="completed"), u("b", status="failed")]
        self.assertFalse(dependency.deps_satisfied(
            u("c", ("a", "b")), by_id(units)))

    def test_non_dict_unit_not_satisfied(self):
        self.assertFalse(dependency.deps_satisfied(None, {}))


# —— ready_units ——

class ReadyUnitsTest(unittest.TestCase):

    def test_empty_units(self):
        self.assertEqual(dependency.ready_units([]), [])

    def test_ready_with_satisfied_deps_selected(self):
        units = [u("a", status="completed"),
                 u("b", ("a",), status="ready")]
        self.assertEqual(dependency.ready_units(units), ["b"])

    def test_waiting_dependency_excluded_even_if_deps_completed(self):
        units = [u("a", status="completed"),
                 u("b", ("a",), status="waiting_dependency")]
        self.assertEqual(dependency.ready_units(units), [])

    def test_ready_with_unmet_deps_excluded(self):
        units = [u("a", status="failed"),
                 u("b", ("a",), status="ready"),
                 u("c", status="ready")]
        # b 依赖失败单元被抑制；c 无依赖正常入列（§63 规则 4）
        self.assertEqual(dependency.ready_units(units), ["c"])

    def test_input_order_preserved(self):
        units = [u("z", status="ready"),
                 u("a", status="completed"),
                 u("m", ("a",), status="ready"),
                 u("b", status="ready")]
        self.assertEqual(dependency.ready_units(units), ["z", "m", "b"])

    def test_missing_reference_excludes_ready_unit(self):
        units = [u("b", ("ghost",), status="ready")]
        self.assertEqual(dependency.ready_units(units), [])


# —— downstream ——

class DownstreamTest(unittest.TestCase):

    def test_direct_and_indirect_closure(self):
        # §63 例图：downstream(a) = c, e（直接 c，间接 e）
        units = guide_graph()
        self.assertEqual(dependency.downstream("a", units), ["c", "e"])
        self.assertEqual(dependency.downstream("b", units), ["c", "e"])

    def test_leaf_unit_empty(self):
        units = guide_graph()
        self.assertEqual(dependency.downstream("e", units), [])

    def test_unknown_unit_empty(self):
        units = guide_graph()
        self.assertEqual(dependency.downstream("ghost", units), [])
        self.assertEqual(dependency.downstream("ghost", []), [])

    def test_fork_join_closure_in_topo_order(self):
        # 分叉汇聚：a → b, a → c；b, c → d。闭包 {b, c, d} 按拓扑序输出
        units = [u("d", ("b", "c")), u("c", ("a",)), u("b", ("a",)), u("a")]
        self.assertEqual(dependency.downstream("a", units), ["b", "c", "d"])

    def test_does_not_include_self(self):
        units = [u("a"), u("b", ("a",))]
        self.assertEqual(dependency.downstream("a", units), ["b"])

    def test_chain_full_closure(self):
        units = [u("d", ("c",)), u("c", ("b",)), u("b", ("a",)), u("a")]
        self.assertEqual(dependency.downstream("a", units), ["b", "c", "d"])


# —— topo_order ——

class TopoOrderTest(unittest.TestCase):

    def test_empty_graph(self):
        self.assertEqual(dependency.topo_order([]), [])

    def test_independent_units_sorted_by_id(self):
        self.assertEqual(dependency.topo_order(
            [u("z"), u("a"), u("m")]), ["a", "m", "z"])

    def test_guide_example_deterministic(self):
        # §63 例图：乱序输入 → 确定性输出 a, b, c, d, e（同层按 id 排序）
        self.assertEqual(dependency.topo_order(guide_graph()),
                         ["a", "b", "c", "d", "e"])

    def test_input_order_independence(self):
        units_a = [u("d", ("b", "c")), u("c", ("a",)), u("b", ("a",)), u("a")]
        units_b = [u("a"), u("b", ("a",)), u("c", ("a",)),
                   u("d", ("b", "c"))]
        order_a = dependency.topo_order(units_a)
        order_b = dependency.topo_order(units_b)
        self.assertEqual(order_a, ["a", "b", "c", "d"])
        self.assertEqual(order_a, order_b)

    def test_chain_order(self):
        units = [u("d", ("c",)), u("c", ("b",)), u("b", ("a",)), u("a")]
        self.assertEqual(dependency.topo_order(units), ["a", "b", "c", "d"])

    def test_missing_reference_ignored_by_order(self):
        # 未知引用由 graph_errors 报告；topo_order 按「不存在」处理不抛错
        self.assertEqual(dependency.topo_order([u("a", ("ghost",))]), ["a"])

    def test_cycle_raises_with_all_members(self):
        units = [u("c", ("b",)), u("a", ("c",)), u("b", ("a",)),
                 u("d", ("a",))]
        with self.assertRaises(ValueError) as ctx:
            dependency.topo_order(units)
        message = str(ctx.exception)
        self.assertIn("环", message)
        for member in ("a", "b", "c"):
            self.assertIn(member, message, member)

    def test_self_loop_raises(self):
        with self.assertRaises(ValueError):
            dependency.topo_order([u("a", ("a",))])

    def test_cycle_detection_shared_with_graph_errors(self):
        # topo_order 与 graph_errors 复用同一环检测：同图同结论
        units = [u("a", ("b",)), u("b", ("a",))]
        with self.assertRaises(ValueError):
            dependency.topo_order(units)
        self.assertTrue(any("环" in e for e in dependency.graph_errors(units)))


# —— state.validate_state 集成（B8.1 接线；说明见模块 docstring） ——

class StateIntegrationTest(unittest.TestCase):

    TASK_ID = "t-wu-1a2b3c"

    def make_state(self, work_units):
        # H2 规则 R4：delegate 路由要求非空 ownership/verification
        # （被测语义是 work_units 校验，路由仅作合法载体，行为不变）
        st = state.new_task_state(
            self.TASK_ID, "目标", {"mode": "delegate"},
            ownership_files=("src/wu.py",),
            verification_required=(
                "python3 -m unittest tests.test_dependency",))
        st["work_units"] = work_units
        return st

    def test_valid_work_units_pass(self):
        st = self.make_state([
            valid_wu("wu-a", status="ready"),
            valid_wu("wu-b", deps=("wu-a",), status="waiting_dependency"),
        ])
        self.assertEqual(state.validate_state(st), [])

    def test_empty_work_units_pass(self):
        # new_task_state 默认 work_units == []（逐项循环零次）
        st = state.new_task_state(self.TASK_ID, "目标", {"mode": "solo"})
        self.assertEqual(state.validate_state(st), [])

    def test_work_units_not_list_still_reported(self):
        st = self.make_state({"id": "wu-1"})
        self.assertTrue(
            any(e == "work_units 必须是数组" for e in state.validate_state(st)))

    def test_invalid_unit_errors_prefixed(self):
        bad = valid_wu("wu-a")
        bad["ownership"] = []          # 空 ownership → 不可派发
        bad["status"] = "done"          # 词汇外状态
        errors = state.validate_state(self.make_state([bad]))
        self.assertTrue(any(
            e.startswith("work_units[0].") and "ownership" in e
            for e in errors), errors)
        self.assertTrue(any(
            e.startswith("work_units[0].") and "status" in e
            for e in errors), errors)

    def test_missing_key_error_prefixed(self):
        bad = valid_wu("wu-a")
        del bad["verification"]
        errors = state.validate_state(self.make_state([bad]))
        self.assertTrue(any(
            e == "work_units[0].缺少必填键 verification" for e in errors))

    def test_multiple_invalid_units_aggregated(self):
        first = valid_wu("wu-a")
        first["id"] = ""               # 空 id
        second = valid_wu("wu-b")
        second["attempt"] = -1         # 负数 attempt
        errors = state.validate_state(self.make_state([first, second]))
        self.assertTrue(any(e.startswith("work_units[0].") for e in errors))
        self.assertTrue(any(e.startswith("work_units[1].") for e in errors))

    def test_non_dict_unit_prefixed(self):
        errors = state.validate_state(self.make_state(["junk"]))
        self.assertTrue(any(
            e == "work_units[0].work_unit 必须是 JSON 对象" for e in errors))

    def test_work_units_survive_save_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            st = self.make_state([
                valid_wu("wu-a", status="completed"),
                valid_wu("wu-b", deps=("wu-a",), status="ready"),
            ])
            state.save_state(tmp, st)
            loaded = state.load_state(tmp, self.TASK_ID)
            self.assertEqual(loaded["work_units"], st["work_units"])
            self.assertEqual(state.validate_state(loaded), [])

    def test_dependency_functions_over_state_work_units(self):
        # 两层拼装冒烟：state 里的 work_units 直接可做依赖图推导
        st = self.make_state([
            valid_wu("wu-a", status="completed"),
            valid_wu("wu-b", deps=("wu-a",), status="ready"),
            valid_wu("wu-c", deps=("wu-b",), status="waiting_dependency"),
        ])
        self.assertEqual(state.validate_state(st), [])
        self.assertEqual(dependency.ready_units(st["work_units"]), ["wu-b"])
        self.assertEqual(dependency.downstream("wu-a", st["work_units"]),
                         ["wu-b", "wu-c"])
        self.assertEqual(dependency.topo_order(st["work_units"]),
                         ["wu-a", "wu-b", "wu-c"])


if __name__ == "__main__":
    unittest.main()
