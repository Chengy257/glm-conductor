#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""runtime.work_unit（v2.4 静态节点）单元测试（Phase 1 工作包 P1-B）。

仅 Python 3 标准库（unittest），零第三方依赖、零磁盘 I/O。

覆盖：
    - new_node：必填形状与可选字段缺省空列表、入参复制（不被调用方
      后续可变操作波及）、非法形状抛 TypeError / ValueError；
    - validate_node：各类错误聚合不短路、node.<字段名> 错误路径前缀、
      id / objective 非空字符串、depends_on 字符串数组 + 自依赖、
      ownership 非空仓库相对 scope、可选数组全部字符串；
    - 无运行时面：合法节点 dict 不含 status / attempt / result /
      verification 等任何运行时键，模块不提供 transition /
      record_attempt 等遗留 API。

运行：
    cd <repo_root> && python3 -m unittest tests.test_static_node -v
"""

import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "glm-conductor"))
from runtime import work_unit


# —— 测试夹具 ——

def valid_node(node_id="node-a"):
    """构造一个通过 validate_node 的完整静态节点。"""
    return work_unit.new_node(
        node_id, "目标 %s" % node_id,
        depends_on=("dep-1",), ownership=("src/auth/**",),
        interfaces=("IAuth",), constraints=("禁改 schema",))


# —— new_node 构造 ——

class NewNodeDefaults(unittest.TestCase):
    """new_node：缺省值与形状。"""

    def test_optional_fields_default_to_empty_lists(self):
        """可选字段缺省空列表：只传必填时 interfaces / constraints /
        local_check 均为空列表，dict 形状完整。"""
        node = work_unit.new_node("n1", "目标")
        self.assertEqual(
            node, {"id": "n1", "objective": "目标", "depends_on": [],
                   "ownership": [], "interfaces": [], "constraints": [],
                   "local_check": []})
        for key in ("interfaces", "constraints", "local_check"):
            self.assertIsInstance(node[key], list)

    def test_tuple_inputs_normalized_to_lists(self):
        """list / tuple 入参一律归一为 list。"""
        node = work_unit.new_node("n1", "目标", depends_on=("a",),
                                  ownership=["src/**"])
        self.assertEqual(node["depends_on"], ["a"])
        self.assertIsInstance(node["depends_on"], list)
        self.assertEqual(node["ownership"], ["src/**"])
        self.assertIsInstance(node["ownership"], list)

    def test_inputs_copied_not_aliased(self):
        """入参复制：调用方后续修改原列表不影响节点。"""
        deps = ["a"]
        owns = ["src/**"]
        node = work_unit.new_node("n1", "目标", depends_on=deps,
                                  ownership=owns)
        deps.append("b")
        owns.append("other/**")
        self.assertEqual(node["depends_on"], ["a"])
        self.assertEqual(node["ownership"], ["src/**"])

    def test_bad_shapes_raise(self):
        """非法形状：非 str id / 空 id / 非 list 依赖 / 非字符串项。"""
        with self.assertRaises(TypeError):
            work_unit.new_node(7, "目标")
        with self.assertRaises(ValueError):
            work_unit.new_node("", "目标")
        with self.assertRaises(TypeError):
            work_unit.new_node("n1", "目标", depends_on="a")
        with self.assertRaises(ValueError):
            work_unit.new_node("n1", "目标", ownership=("src/**", 7))
        with self.assertRaises(ValueError):
            work_unit.new_node("n1", "目标", interfaces=("",))


# —— validate_node 校验 ——

class ValidateNodeErrors(unittest.TestCase):
    """validate_node：错误分类与聚合。"""

    def test_non_dict(self):
        """非 dict 输入返回单条错误，不抛异常。"""
        self.assertEqual(work_unit.validate_node(None),
                         ["node 必须是 JSON 对象"])

    def test_missing_required_keys_aggregated(self):
        """缺必填键逐键聚合报错，不短路。"""
        errors = work_unit.validate_node({"interfaces": []})
        for key in work_unit.NODE_REQUIRED_KEYS:
            self.assertIn("node 缺少必填键 %s" % key, errors)

    def test_error_path_prefix(self):
        """错误路径前缀形如 node.<字段名>。"""
        node = {"id": "", "objective": 7, "depends_on": "x",
                "ownership": [], "interfaces": [1]}
        errors = work_unit.validate_node(node)
        for prefix in ("node.id", "node.objective", "node.depends_on",
                       "node.ownership", "node.interfaces"):
            self.assertTrue(any(e.startswith(prefix) for e in errors),
                            "%s 前缀缺失：%r" % (prefix, errors))

    def test_empty_id_and_objective(self):
        """id / objective 必须是非空字符串。"""
        node = valid_node()
        node["id"] = ""
        node["objective"] = ""
        errors = work_unit.validate_node(node)
        self.assertIn("node.id 必须是非空字符串", errors)
        self.assertIn("node.objective 必须是非空字符串", errors)

    def test_depends_on_must_be_string_list(self):
        """depends_on 非数组 / 项非非空字符串报错。"""
        node = valid_node()
        node["depends_on"] = "dep-1"
        self.assertIn("node.depends_on 必须是数组",
                      work_unit.validate_node(node))
        node["depends_on"] = ["dep-1", ""]
        self.assertIn("node.depends_on[1] 必须是非空字符串",
                      work_unit.validate_node(node))

    def test_self_dependency(self):
        """自依赖：id 出现在自身 depends_on 中报错。"""
        node = valid_node("node-a")
        node["depends_on"] = ["node-a"]
        errors = work_unit.validate_node(node)
        self.assertTrue(any("node.depends_on 自依赖" in e for e in errors),
                        "缺自依赖错误：%r" % errors)

    def test_empty_ownership_rejected(self):
        """空 ownership 数组报错（无文件范围的节点不可编译）。"""
        node = valid_node()
        node["ownership"] = []
        self.assertIn("node.ownership 不能为空数组（无文件范围的节点不可编译）",
                      work_unit.validate_node(node))

    def test_absolute_ownership_rejected(self):
        """绝对路径 ownership 报错（scope 必须仓库相对）。"""
        node = valid_node()
        node["ownership"] = ["/etc/passwd", "src/**"]
        errors = work_unit.validate_node(node)
        self.assertTrue(any("node.ownership[0] 必须是仓库相对路径" in e
                            for e in errors), errors)

    def test_optional_non_string_elements(self):
        """可选数组存在时元素必须全是字符串（local_check 无豁免）。"""
        for key in ("interfaces", "constraints", "local_check"):
            node = valid_node()
            node[key] = ["ok", 7]
            self.assertIn("node.%s[1] 必须是非空字符串" % key,
                          work_unit.validate_node(node))

    def test_optional_arrays_may_be_empty(self):
        """可选数组允许空列表（local_check 可为空，无必填非空语义）。"""
        node = valid_node()
        node["local_check"] = []
        self.assertEqual(work_unit.validate_node(node), [])

    def test_all_errors_aggregated_no_short_circuit(self):
        """多处非法聚合全部错误：缺键 + 坏 id + 坏依赖 + 空 ownership。"""
        errors = work_unit.validate_node({"id": "", "depends_on": [1]})
        self.assertGreaterEqual(len(errors), 4)
        self.assertTrue(any("node 缺少必填键 objective" in e for e in errors))
        self.assertTrue(any("node 缺少必填键 ownership" in e for e in errors))
        self.assertTrue(any("node.id 必须是非空字符串" in e for e in errors))

    def test_unknown_keys_ignored(self):
        """未知键忽略（向前兼容）。"""
        node = valid_node()
        node["future_field"] = {"any": "shape"}
        self.assertEqual(work_unit.validate_node(node), [])


# —— 合法样例 / 面契约 ——

class ValidNodeAndSurface(unittest.TestCase):
    """合法样例与「无运行时面」契约。"""

    def test_valid_sample_zero_errors(self):
        """合法样例零错误（必填 + 可选全齐）。"""
        self.assertEqual(work_unit.validate_node(valid_node()), [])
        bare = work_unit.new_node("n1", "目标", ownership=("src/**",))
        self.assertEqual(work_unit.validate_node(bare), [])

    def test_no_runtime_keys_in_shape(self):
        """构造产物不含任何运行时键（status / attempt / result /
        verification / runtime / executor）。"""
        node = valid_node()
        for banned in ("status", "attempt", "result", "verification",
                       "runtime", "executor"):
            self.assertNotIn(banned, node)

    def test_no_legacy_runtime_api(self):
        """模块不提供 v2.3 遗留运行时 API（静态节点无状态通道）。"""
        for banned in ("new_work_unit", "validate_work_unit",
                       "transition_work_unit", "record_attempt",
                       "WORK_UNIT_STATUSES", "WU_TRANSITIONS"):
            self.assertFalse(hasattr(work_unit, banned),
                             "不应提供遗留 API：%s" % banned)


if __name__ == "__main__":
    unittest.main()
