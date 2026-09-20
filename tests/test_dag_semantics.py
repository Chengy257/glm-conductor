#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""runtime.dependency（v2.4 纯 DAG 语义）单元测试（Phase 1 工作包 P1-B）。

仅 Python 3 标准库（unittest），零第三方依赖、零磁盘 I/O。

覆盖：
    - graph_errors 四类：缺失依赖 / 重复 id / 自依赖 / 环，聚合不短路；
    - topo_order 确定性：同层（就绪集内）按 id 排序、输入顺序无关；
    - stage_levels：层级正确（第 0 层无依赖、第 i 层依赖全在前层）、
      层内有序、输入顺序无关；
    - 环检测：graph_errors 报全部环成员，topo_order / stage_levels /
      downstream 抛 ValueError；
    - downstream：传递闭包、拓扑序输出、不含自身、未知 id 空表；
    - 无运行时面：模块不提供 ready_units / deps_satisfied 等
      基于 status 的就绪函数。

运行：
    cd <repo_root> && python3 -m unittest tests.test_dag_semantics -v
"""

import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "glm-conductor"))
from runtime import dependency


# —— 测试夹具 ——

def n(node_id, deps=()):
    """构造最小静态节点 dict（dependency 层只依赖 id / depends_on）。"""
    return {"id": node_id, "depends_on": list(deps)}


def guide_graph():
    """例图：a、b 无依赖；c → a,b；d → c；e → c,d（最长链 4 层）。"""
    return [n("d", ("c",)),
            n("b"),
            n("e", ("c", "d")),
            n("a"),
            n("c", ("a", "b"))]


# —— graph_errors ——

class GraphErrorsTest(unittest.TestCase):
    """graph_errors：四类错误与聚合。"""

    def test_non_list_input(self):
        """非数组输入单条错误。"""
        self.assertEqual(dependency.graph_errors("nope"),
                         ["nodes 必须是数组"])

    def test_missing_dependency(self):
        """缺失依赖：depends_on 引用不存在的 id。"""
        errors = dependency.graph_errors([n("a"), n("b", ("ghost",))])
        self.assertEqual(
            [e for e in errors if "ghost" in e],
            ["nodes[1]（id='b'）depends_on 引用不存在的 id：'ghost'"])

    def test_duplicate_id(self):
        """重复 id：报告两次出现的下标。"""
        errors = dependency.graph_errors([n("a"), n("b"), n("a")])
        self.assertIn("重复 id：'a'（nodes[0] 与 nodes[2]）", errors)

    def test_self_dependency(self):
        """自依赖：id 出现在自身 depends_on 中。"""
        errors = dependency.graph_errors([n("a", ("a",))])
        self.assertIn("nodes[0]（id='a'）自依赖", errors)

    def test_cycle_reports_all_members(self):
        """环检测：A→B→C→A 报全部三个 id（按 id 排序）。"""
        graph = [n("a", ("c",)), n("b", ("a",)), n("c", ("b",))]
        errors = dependency.graph_errors(graph)
        self.assertTrue(any("依赖图存在环" in e and
                            "a, b, c" in e for e in errors), errors)

    def test_errors_aggregated_no_short_circuit(self):
        """多类错误同时存在时聚合全部，不短路。"""
        # 自依赖 + 缺失依赖 + 重复 id 同图聚合（此图无环）
        graph = [n("a", ("a", "ghost")), n("a"), n("b", ("a",)),
                 n("c", ("b",)), n("d", ("c",))]
        errors = dependency.graph_errors(graph)
        self.assertTrue(any("自依赖" in e for e in errors), errors)
        self.assertTrue(any("ghost" in e for e in errors), errors)
        self.assertTrue(any("重复 id" in e for e in errors), errors)
        # 环与重复 id 同图聚合
        graph2 = [n("x", ("y",)), n("y", ("x",)), n("x")]
        errors2 = dependency.graph_errors(graph2)
        self.assertTrue(any("依赖图存在环" in e for e in errors2), errors2)
        self.assertTrue(any("重复 id" in e for e in errors2), errors2)

    def test_shape_errors_reported(self):
        """形状级错误（非 dict 项 / 缺 id / depends_on 项非字符串）。"""
        graph = ["not-a-dict", {"depends_on": [1]}, n("ok", (7,))]
        errors = dependency.graph_errors(graph)
        self.assertIn("nodes[0] 必须是 JSON 对象", errors)
        # id 非法的节点只报 id：节点无法定位，其 depends_on 不再展开
        # （与 legacy_dependency 同语义）
        self.assertIn("nodes[1].id 必须是非空字符串", errors)
        self.assertNotIn("nodes[1].depends_on[0] 必须是非空字符串", errors)
        self.assertIn("nodes[2].depends_on[0] 必须是非空字符串", errors)

    def test_valid_graph_zero_errors(self):
        """合法图（含例图）零错误。"""
        self.assertEqual(dependency.graph_errors(guide_graph()), [])
        self.assertEqual(dependency.graph_errors([]), [])
        self.assertEqual(dependency.graph_errors([n("solo")]), [])


# —— topo_order ——

class TopoOrderTest(unittest.TestCase):
    """topo_order：确定性拓扑序。"""

    def test_dependencies_precede_dependents(self):
        """依赖先于被依赖者（例图全序）。"""
        order = dependency.topo_order(guide_graph())
        self.assertLess(order.index("a"), order.index("c"))
        self.assertLess(order.index("b"), order.index("c"))
        self.assertLess(order.index("c"), order.index("d"))
        self.assertLess(order.index("d"), order.index("e"))

    def test_deterministic_across_input_orders(self):
        """同一图不同输入顺序 → 相同输出（含并列 id 升序）。"""
        graph = guide_graph()
        baseline = dependency.topo_order(graph)
        self.assertEqual(baseline, ["a", "b", "c", "d", "e"])
        self.assertEqual(dependency.topo_order(list(reversed(graph))),
                         baseline)
        self.assertEqual(dependency.topo_order([graph[3], graph[0],
                                                graph[4], graph[2], graph[1]]),
                         baseline)

    def test_ties_sorted_by_id(self):
        """就绪集内并列节点按 id 升序（无依赖层全排序）。"""
        graph = [n("z"), n("m"), n("a"), n("q")]
        self.assertEqual(dependency.topo_order(graph),
                         ["a", "m", "q", "z"])

    def test_missing_dep_ignored_in_order(self):
        """缺失依赖不计入入度（排序不抛错，裁决归 graph_errors）。"""
        self.assertEqual(dependency.topo_order([n("a", ("ghost",))]),
                         ["a"])

    def test_cycle_raises_value_error(self):
        """图有环 → ValueError，消息含全部环成员。"""
        graph = [n("a", ("c",)), n("b", ("a",)), n("c", ("b",)),
                 n("free")]
        with self.assertRaises(ValueError) as ctx:
            dependency.topo_order(graph)
        self.assertIn("a, b, c", str(ctx.exception))


# —— stage_levels ——

class StageLevelsTest(unittest.TestCase):
    """stage_levels：确定性层级（供代码生成）。"""

    def test_levels_correct(self):
        """第 0 层无依赖，第 i 层依赖全在前层（例图 4 层）。"""
        levels = dependency.stage_levels(guide_graph())
        self.assertEqual(levels,
                         [["a", "b"], ["c"], ["d"], ["e"]])

    def test_deterministic_across_input_orders(self):
        """同一图不同输入顺序 → 相同层级输出。"""
        graph = guide_graph()
        baseline = dependency.stage_levels(graph)
        self.assertEqual(dependency.stage_levels(list(reversed(graph))),
                         baseline)

    def test_layer_count_is_longest_chain(self):
        """层数 = 最长依赖链（菱形：a→b,c→d 为 3 层）。"""
        diamond = [n("d", ("b", "c")), n("b", ("a",)),
                   n("c", ("a",)), n("a")]
        self.assertEqual(dependency.stage_levels(diamond),
                         [["a"], ["b", "c"], ["d"]])

    def test_cycle_raises_value_error(self):
        """图有环 → ValueError（环上节点永远无法入层）。"""
        with self.assertRaises(ValueError):
            dependency.stage_levels([n("x", ("y",)), n("y", ("x",))])


# —— downstream ——

class DownstreamTest(unittest.TestCase):
    """downstream：传递闭包。"""

    def test_transitive_closure_in_topo_order(self):
        """直接 + 间接依赖者，按拓扑序输出（例图中 c 的下游）。"""
        self.assertEqual(dependency.downstream("c", guide_graph()),
                         ["d", "e"])

    def test_leaf_has_no_downstream(self):
        """末端节点无下游；不含自身。"""
        self.assertEqual(dependency.downstream("e", guide_graph()), [])

    def test_unknown_id_empty(self):
        """id 不存在 → 空列表。"""
        self.assertEqual(dependency.downstream("ghost", guide_graph()), [])

    def test_branch_merge_closure(self):
        """分叉汇聚：a 被 b、c 依赖，d 汇聚 → 全部下游。"""
        graph = [n("d", ("b", "c")), n("b", ("a",)),
                 n("c", ("a",)), n("a")]
        self.assertEqual(dependency.downstream("a", graph),
                         ["b", "c", "d"])


# —— 面契约 ——

class SurfaceContract(unittest.TestCase):
    """模块不提供运行时就绪函数（纯 DAG，无 status 语义）。"""

    def test_no_runtime_readiness_api(self):
        for banned in ("ready_units", "deps_satisfied"):
            self.assertFalse(hasattr(dependency, banned),
                             "不应提供运行时就绪 API：%s" % banned)


if __name__ == "__main__":
    unittest.main()
