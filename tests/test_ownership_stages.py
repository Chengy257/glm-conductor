#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""runtime.ownership 编译期阶段规划单元测试（v2.4 Phase 1 工作包 P1-C）。

运行：
    cd <repo_root> && python3 -m unittest tests.test_ownership_stages -v

覆盖（规格最低集 + 补充）：
    - scopes_overlap：精确同文件 / 文件对目录前缀 / 目录对目录前缀 /
      glob 重叠（两端都可 glob）/ 段边界反例 / 对称性；
      True 结论均附 match_path 见证路径（与 compile_pattern 语义互证）；
    - 歧义 pattern（"**" / "*" / "**/**"）→ OwnershipConflictError；
      非法 pattern → OwnershipError（结构性错误原样上抛）；
    - plan_stages：精确同文件同 level 串行；父子路径重叠串行；
      不相交目录同阶段并行；glob 重叠串行；歧义 / 非法 pattern 抛
      OwnershipConflictError；同输入两次调用输出全等（且输入顺序
      无关、不改输入）；跨层依赖权威（重叠 scope 分属两层不报错）；
      形状 / 图校验失败 → ValueError。
"""

import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "glm-conductor"))
from runtime import ownership, work_unit


# —— 测试夹具 ——

def node(node_id, scopes, deps=()):
    """构造合法静态节点（经 work_unit.new_node，走 U2 构造正门）。"""
    return work_unit.new_node(
        node_id, "目标 %s" % node_id, depends_on=deps, ownership=scopes)


def assert_witness(testcase, path, patterns):
    """True 结论的见证互证：path 必须同时被各 pattern（compile_pattern 侧）命中。"""
    for pattern in patterns:
        testcase.assertTrue(
            ownership.match_path(path, [pattern]),
            "见证路径 %r 未命中模式 %r（NFA 语义与 compile_pattern 脱节）"
            % (path, pattern))


# —— scopes_overlap：各声明形态 ——

class ScopesOverlapExactTest(unittest.TestCase):
    """精确文件与目录前缀形态。"""

    def test_exact_same_file_overlaps(self):
        self.assertTrue(ownership.scopes_overlap("src/auth.ts", "src/auth.ts"))

    def test_distinct_files_do_not_overlap(self):
        self.assertFalse(ownership.scopes_overlap("src/a.ts", "src/b.ts"))

    def test_file_under_directory_prefix(self):
        # 文件对目录前缀（两个方向都判定）
        self.assertTrue(ownership.scopes_overlap("src/auth/x.ts", "src/auth"))
        self.assertTrue(ownership.scopes_overlap("src/auth", "src/auth/x.ts"))
        assert_witness(self, "src/auth/x.ts", ["src/auth/x.ts", "src/auth"])

    def test_directory_prefix_chain(self):
        # 目录对目录前缀
        self.assertTrue(ownership.scopes_overlap("src/auth", "src"))
        self.assertTrue(ownership.scopes_overlap("src", "src/auth"))

    def test_segment_boundary_not_string_prefix(self):
        # 按路径段边界而非字符串前缀：兄弟文件 / 兄弟目录不命中
        self.assertFalse(
            ownership.scopes_overlap("src/authentication.ts", "src/auth"))
        self.assertFalse(
            ownership.scopes_overlap("src/auth.ts", "src/auth/**"))
        self.assertFalse(ownership.scopes_overlap("src/aut", "src/auth"))


class ScopesOverlapGlobTest(unittest.TestCase):
    """glob 形态（两端都可用 glob，复用 compile_pattern 语义）。"""

    def test_glob_against_literal_hit(self):
        self.assertTrue(ownership.scopes_overlap("src/*.ts", "src/auth.ts"))
        assert_witness(self, "src/auth.ts", ["src/*.ts", "src/auth.ts"])

    def test_glob_against_literal_miss(self):
        self.assertFalse(ownership.scopes_overlap("src/*.ts", "tests/a.ts"))

    def test_glob_against_glob_overlap(self):
        self.assertTrue(ownership.scopes_overlap("tests/*.py", "tests/**"))
        assert_witness(self, "tests/x.py", ["tests/*.py", "tests/**"])
        self.assertTrue(ownership.scopes_overlap("src/*.ts", "src/**"))
        assert_witness(self, "src/a.ts", ["src/*.ts", "src/**"])

    def test_glob_against_glob_disjoint(self):
        self.assertFalse(ownership.scopes_overlap("tests/**", "src/**"))
        # "*.py" 只命中单段路径，不与 "src/**"（"src" 及其后代）相交
        self.assertFalse(ownership.scopes_overlap("*.py", "src/**"))
        self.assertFalse(ownership.scopes_overlap("*.py", "tests"))

    def test_mid_globstar_against_literal(self):
        # 中段 "**"：零段 / 一段 / 多段见证均命中；字面量兼具目录前缀
        # 双语义（"a/x/y/c" 等价 "a/x/y/c/**"），故经后代 "a/x/y/c/b" 相交
        self.assertTrue(ownership.scopes_overlap("a/**/b", "a/b"))
        self.assertTrue(ownership.scopes_overlap("a/**/b", "a/x/b"))
        self.assertTrue(ownership.scopes_overlap("a/**/b", "a/x/y/b"))
        self.assertTrue(ownership.scopes_overlap("a/**/b", "a/x/y/c"))
        assert_witness(self, "a/x/y/c/b", ["a/**/b", "a/x/y/c"])
        # 末段被模式钉死为不同字面量（b ≠ c）→ 任何后代见证都无法同时满足
        self.assertFalse(ownership.scopes_overlap("a/**/b", "a/**/c"))

    def test_leading_globstar(self):
        self.assertTrue(ownership.scopes_overlap("**/b", "b"))
        self.assertTrue(ownership.scopes_overlap("**/b", "x/b"))
        # 反例不能选字面量目录（其目录前缀语义必含后代 "…/b"，必相交）；
        # 选末段钉死为 ".ts" 的单段 glob：交集需单段既是 "b" 又以 .ts 结尾
        self.assertFalse(ownership.scopes_overlap("**/b", "*.ts"))

    def test_question_mark_single_char(self):
        self.assertTrue(ownership.scopes_overlap("f?.py", "f1.py"))
        self.assertFalse(ownership.scopes_overlap("f?.py", "f12.py"))

    def test_symmetry_on_sample_matrix(self):
        pairs = [
            ("src/auth.ts", "src/auth.ts"),
            ("src/a.ts", "src/b.ts"),
            ("src/auth/x.ts", "src/auth"),
            ("src/auth.ts", "src/auth/**"),
            ("tests/*.py", "tests/**"),
            ("*.py", "src/**"),
            ("a/**/b", "a/x/y/c"),
            ("f?.py", "f1.py"),
        ]
        for scope_a, scope_b in pairs:
            with self.subTest(scope_a=scope_a, scope_b=scope_b):
                self.assertEqual(
                    ownership.scopes_overlap(scope_a, scope_b),
                    ownership.scopes_overlap(scope_b, scope_a))


class ScopesOverlapErrorsTest(unittest.TestCase):
    """歧义 / 非法模式的错误契约。"""

    def test_bare_globstar_is_ambiguous(self):
        for scope in ("**", "**/**"):
            with self.subTest(scope=scope):
                with self.assertRaises(ownership.OwnershipConflictError) as ctx:
                    ownership.scopes_overlap(scope, "src/**")
                self.assertIn(scope, str(ctx.exception))

    def test_single_star_is_ambiguous(self):
        # "*" 语义上命中仓库根本身（空相对路径），与 "**" 同判歧义
        with self.assertRaises(ownership.OwnershipConflictError):
            ownership.scopes_overlap("*", "src/**")
        with self.assertRaises(ownership.OwnershipConflictError):
            ownership.scopes_overlap("src/**", "*")

    def test_invalid_pattern_raises_ownership_error(self):
        # 结构性错误原样上抛（match_path / classify_paths 同一约定）；
        # plan_stages 负责包装为 OwnershipConflictError 并附节点 id
        with self.assertRaises(ownership.OwnershipError):
            ownership.scopes_overlap("a//b", "src/**")

    def test_non_string_scope_raises_ownership_error(self):
        with self.assertRaises(ownership.OwnershipError):
            ownership.scopes_overlap(123, "src/**")


# —— plan_stages：规格最低集 ——

class PlanStagesSpecCasesTest(unittest.TestCase):
    """规格点名的六类场景。"""

    def test_exact_same_file_serialized_within_level(self):
        stages = ownership.plan_stages([
            node("a", ("src/auth.ts",)),
            node("b", ("src/auth.ts",)),
        ])
        self.assertEqual(stages, [["a"], ["b"]])

    def test_parent_child_overlap_serialized(self):
        stages = ownership.plan_stages([
            node("a", ("src/auth",)),
            node("b", ("src/auth/x.ts",)),
        ])
        self.assertEqual(stages, [["a"], ["b"]])

    def test_disjoint_directories_share_stage(self):
        stages = ownership.plan_stages([
            node("a", ("src/a/**",)),
            node("b", ("src/b/**",)),
        ])
        self.assertEqual(stages, [["a", "b"]])

    def test_glob_overlap_serialized(self):
        stages = ownership.plan_stages([
            node("a", ("tests/*.py",)),
            node("b", ("tests/**",)),
        ])
        self.assertEqual(stages, [["a"], ["b"]])

    def test_glob_disjoint_share_stage(self):
        stages = ownership.plan_stages([
            node("a", ("src/*.ts",)),
            node("b", ("tests/*.py",)),
        ])
        self.assertEqual(stages, [["a", "b"]])

    def test_ambiguous_pattern_raises_with_node_id(self):
        # 单节点图同样拦截（预检不依赖配对），消息点名节点 id 与 scope
        for scopes in (("**",), ("*",)):
            with self.subTest(scopes=scopes):
                with self.assertRaises(
                        ownership.OwnershipConflictError) as ctx:
                    ownership.plan_stages([node("n1", scopes)])
                message = str(ctx.exception)
                self.assertIn("n1", message)
                self.assertIn(scopes[0], message)

    def test_invalid_pattern_raises_conflict_error_with_node_id(self):
        with self.assertRaises(ownership.OwnershipConflictError) as ctx:
            ownership.plan_stages([node("n1", ("src//broken",))])
        message = str(ctx.exception)
        self.assertIn("n1", message)
        self.assertIn("src//broken", message)

    def test_same_input_identical_output(self):
        graph = [
            node("worker-b", ("src/b/**",)),
            node("worker-a", ("src/a/**",)),
            node("gate", ("src/auth.ts",), deps=("worker-a", "worker-b")),
            node("docs", ("docs/**",)),
        ]
        first = ownership.plan_stages(graph)
        second = ownership.plan_stages(graph)
        self.assertEqual(first, second)
        # 输入顺序无关 + 输出覆盖每个节点恰好一次 + 不改输入
        self.assertEqual(
            first, ownership.plan_stages(list(reversed(graph))))
        flat = [node_id for group in first for node_id in group]
        self.assertEqual(sorted(flat),
                         ["docs", "gate", "worker-a", "worker-b"])
        self.assertEqual(len(flat), len(set(flat)))
        self.assertEqual(len(graph), 4)


# —— plan_stages：层级权威与贪心装箱 ——

class PlanStagesLevelsTest(unittest.TestCase):
    """跨层依赖权威、贪心组序、边界输入。"""

    def test_cross_level_overlap_is_not_an_error(self):
        # 重叠 scope 分属依赖链两层：层级权威，顺序串行即可，不报错
        stages = ownership.plan_stages([
            node("a", ("x/**",)),
            node("b", ("x/**",), deps=("a",)),
        ])
        self.assertEqual(stages, [["a"], ["b"]])

    def test_levels_authoritative_with_parallel_disjoint_root(self):
        # a(x/**) → b(x/**)；c(b/**) 无依赖：L0=[a, c] 并行，L1=[b]
        stages = ownership.plan_stages([
            node("b", ("x/**",), deps=("a",)),
            node("a", ("x/**",)),
            node("c", ("b/**",)),
        ])
        self.assertEqual(stages, [["a", "c"], ["b"]])

    def test_three_same_file_fully_serialized(self):
        stages = ownership.plan_stages([
            node("a", ("f.ts",)),
            node("b", ("f.ts",)),
            node("c", ("f.ts",)),
        ])
        self.assertEqual(stages, [["a"], ["b"], ["c"]])

    def test_greedy_grouping_keeps_group_order(self):
        # a、b 不相交同组；c 与 a 重叠 → 新组；组序列即层内串行顺序
        stages = ownership.plan_stages([
            node("c", ("x.ts",)),
            node("a", ("x.ts",)),
            node("b", ("y.ts",)),
        ])
        self.assertEqual(stages, [["a", "b"], ["c"]])

    def test_multi_scope_node_overlaps_by_any_scope(self):
        # 节点任一 scope 与组内成员重叠即不可同组
        stages = ownership.plan_stages([
            node("a", ("keep/**", "x.ts")),
            node("b", ("x.ts",)),
        ])
        self.assertEqual(stages, [["a"], ["b"]])

    def test_empty_graph_returns_empty_plan(self):
        self.assertEqual(ownership.plan_stages([]), [])

    def test_single_node_single_stage(self):
        self.assertEqual(
            ownership.plan_stages([node("solo", ("src/**",))]), [["solo"]])


# —— plan_stages：校验前置（ValueError） ——

class PlanStagesValidationTest(unittest.TestCase):
    """validate_node 与 graph_errors 前置，有错抛 ValueError。"""

    def test_shape_error_aggregates(self):
        bad = {"id": "n1", "objective": "", "depends_on": [],
               "ownership": []}
        with self.assertRaises(ValueError) as ctx:
            ownership.plan_stages([bad])
        self.assertIn("node.objective", str(ctx.exception))
        self.assertIn("node.ownership", str(ctx.exception))

    def test_duplicate_id_raises(self):
        with self.assertRaises(ValueError) as ctx:
            ownership.plan_stages([
                node("n1", ("a/**",)), node("n1", ("b/**",))])
        self.assertIn("重复 id", str(ctx.exception))

    def test_missing_dependency_raises(self):
        with self.assertRaises(ValueError):
            ownership.plan_stages([node("n1", ("a/**",), deps=("ghost",))])

    def test_cycle_raises(self):
        with self.assertRaises(ValueError) as ctx:
            ownership.plan_stages([
                node("n1", ("a/**",), deps=("n2",)),
                node("n2", ("b/**",), deps=("n1",)),
            ])
        self.assertIn("环", str(ctx.exception))

    def test_non_list_input_raises(self):
        with self.assertRaises(ValueError):
            ownership.plan_stages("not-a-list")


if __name__ == "__main__":
    unittest.main()
