#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Phase E（WP-C）发布面卫生定向测试（v2.6.0，规格 §6 P3 卫生项）。

覆盖三条卫生项的静态 / 完整性面（行为面归各域测试文件）：
  1. quota/window_math.py 死代码删除后的引用完整性：
     - 模块文件已删、import 级不可达（ModuleNotFoundError）；
     - 生产源零 import 残留（grep 级：import / from-import 两形态；
       time_utils.py docstring 中的历史名词提及——非 import——不在
       契约内）；
     - runtime 包全部子模块可独立 import（删除未留下悬空引用）。
  2. hooks.json 自述版本无关：description 不再携带本插件版本号
     （「GLM Conductor hooks」，非历史形态「GLM Conductor v2.4
     hooks」——自述随发布版本漂移会让每次发版产生无意义 diff），
     且 SessionStart + Stop 结构 / matcher / 脚本指向 / 超时预算
     完好（版本无关不等于结构松绑）。
  3. quota/_http.py 异常分类源面：传输层 catch 元组含
     http.client.HTTPException（IncompleteRead / BadStatusLine 等
     响应层异常归 network），模块零裸 except:（「无裸异常泄漏」的
     静态锚；异常分类行为面归
     tests.test_quota_adapters.TestErrorClassification）。

零网络纪律：纯文件 / AST / importlib 断言，零网络、零盘面写入。
仅 Python 3 标准库。

运行：
    cd <repo_root> && python3 -m unittest tests.test_release_hygiene -v
"""

import ast
import importlib
import json
import pathlib
import re
import sys
import unittest

PLUGIN_ROOT = (pathlib.Path(__file__).resolve().parents[1]
               / "plugins" / "glm-conductor")
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


def production_sources():
    """生产源清单：插件树全部 .py + 两份插件清单（marketplace /
    plugin.json——manifest 内嵌脚本路径若有 import 残留同样违规）。"""
    paths = list(PLUGIN_ROOT.rglob("*.py"))
    for manifest in (REPO_ROOT / "marketplace.json",
                     PLUGIN_ROOT / ".zcode-plugin" / "plugin.json"):
        if manifest.exists():
            paths.append(manifest)
    return [p for p in paths if "__pycache__" not in p.parts]


# —— ④ window_math.py 删除后的引用完整性 ——

class WindowMathReferenceIntegrityTest(unittest.TestCase):
    """window_math.py（v2.1 沉淀的死代码：docstring 声称的存活消费方
    不存在、_earliest_reset_plus min 口径与现行 plan_resume max 口径
    语义相反）删除后：零文件、零可达、零 import 残留、包完整性不破。"""

    def test_module_file_deleted(self):
        self.assertFalse(
            (PLUGIN_ROOT / "runtime" / "quota" / "window_math.py").exists(),
            "runtime/quota/window_math.py 应已删除（死代码）")

    def test_import_level_unreachable(self):
        try:
            importlib.import_module("runtime.quota.window_math")
        except ModuleNotFoundError:
            pass  # 预期：模块已删，import 级不可达
        else:
            self.fail("runtime.quota.window_math 仍可 import——删除不完整")

    def test_no_import_residue_in_production_source(self):
        # grep 级断言：生产源零 window_math import 残留（三种形态：
        # import x.window_math / from…window_math…import / from…import
        # …window_math）。docstring / 注释里的历史名词提及不是 import，
        # 不在本断言契约内。
        import_pattern = re.compile(
            r"^\s*(?:import\s+\S*window_math"
            r"|from\s+[\w.]*window_math[\w.]*\s+import"
            r"|from\s+[\w.]+\s+import\s+[^#\n]*\bwindow_math\b)",
            re.MULTILINE)
        offenders = []
        for path in production_sources():
            text = path.read_text(encoding="utf-8")
            if import_pattern.search(text):
                offenders.append(str(path))
        self.assertEqual(offenders, [],
                         "生产源存在 window_math import 残留：%r" % offenders)

    def test_runtime_package_fully_importable(self):
        # 完整性面：runtime 包全部子模块（含 quota 适配器 / workflow
        # 编译器 / commands）可独立 import——删除未留下悬空引用
        package_root = PLUGIN_ROOT / "runtime"
        checked = 0
        for path in sorted(package_root.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            rel = path.relative_to(PLUGIN_ROOT).with_suffix("")
            module = ".".join(rel.parts)
            with self.subTest(module=module):
                importlib.import_module(module)
            checked += 1
        self.assertGreaterEqual(checked, 20,
                                "runtime 包模块数异常（解析锚失效）")


# —— ⑤ hooks.json 版本无关 ——

class HooksJsonVersionAgnosticTest(unittest.TestCase):
    """hooks.json description 去自版本表述（「GLM Conductor v2.4
    hooks」→「GLM Conductor hooks」），且钩子结构 / 脚本指向完好。"""

    @classmethod
    def setUpClass(cls):
        cls.hooks_path = PLUGIN_ROOT / "hooks" / "hooks.json"
        with open(str(cls.hooks_path), encoding="utf-8") as fh:
            cls.config = json.load(fh)

    def test_description_is_version_agnostic(self):
        description = self.config.get("description", "")
        self.assertTrue(description.startswith("GLM Conductor hooks"),
                        "description 应回到版本无关自述：%r"
                        % description[:60])
        # 自述头部不得钉本插件版本（历史形态 "GLM Conductor v2.4 hooks"
        # 会让每次发版都产生 description diff）；description 后段曾有的
        # 历史退役注（v2.3 enforcement hooks retired）已在 v2.6.1 B4
        # 配置清理中随英文重写移除，头部前缀检查保持不变
        self.assertNotRegex(description, r"^GLM Conductor v[\d.]+")

    def test_hook_event_surface_intact(self):
        hooks = self.config.get("hooks")
        self.assertIsInstance(hooks, dict)
        self.assertEqual(
            set(hooks), {"SessionStart", "Stop", "PermissionRequest"})
        for event in ("SessionStart", "Stop", "PermissionRequest"):
            entries = hooks[event]
            self.assertIsInstance(entries, list)
            self.assertTrue(entries, "%s 为空数组" % event)
        self.assertEqual(hooks["SessionStart"][0].get("matcher"),
                         "startup|clear|compact")

    def test_hook_scripts_referenced_and_present(self):
        scripts = []
        for event in ("SessionStart", "Stop", "PermissionRequest"):
            for entry in self.config["hooks"][event]:
                for hook in entry["hooks"]:
                    self.assertEqual(hook["type"], "process")
                    self.assertEqual(hook["command"], "python3")
                    self.assertLessEqual(int(hook["timeoutMs"]), 5000)
                    self.assertEqual(len(hook["args"]), 1)
                    scripts.append(hook["args"][0])
        self.assertEqual(
            scripts,
            ["${ZCODE_PLUGIN_ROOT}/hooks/session_start.py",
             "${ZCODE_PLUGIN_ROOT}/hooks/stop_gate.py",
             "${ZCODE_PLUGIN_ROOT}/hooks/permission_request_workflow.py"])
        for name in ("session_start.py", "stop_gate.py",
                     "permission_request_workflow.py"):
            self.assertTrue((PLUGIN_ROOT / "hooks" / name).exists(),
                            "钩子脚本缺失：%s" % name)


# —— ③ _http.py 异常分类源面 ——

class HttpExceptionSourceContractTest(unittest.TestCase):
    """_http.py 传输层异常分类的源面锚（行为面归
    tests.test_quota_adapters.TestErrorClassification：HTTPException
    家族 → network kind 的端到端断言已在彼处）。"""

    @classmethod
    def setUpClass(cls):
        path = PLUGIN_ROOT / "runtime" / "quota" / "_http.py"
        cls.source = path.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def test_transport_catch_tuple_includes_http_client_exceptions(self):
        # 响应层异常（IncompleteRead / BadStatusLine 等 HTTPException
        # 子类）与 URLError 家族同归 network——catch 元组与 import
        # 必须在源面在场
        self.assertIn("import http.client", self.source)
        self.assertIn("http.client.HTTPException", self.source)

    def test_no_bare_except_clauses(self):
        # 「无裸异常泄漏」静态面：全模块零裸 except:（无绑定类型的
        # ExceptHandler）——传输层任何异常都必须经分类元组归入
        # QuotaProviderError 的明确 kind
        bare = [node.lineno for node in ast.walk(self.tree)
                if isinstance(node, ast.ExceptHandler)
                and node.type is None]
        self.assertEqual(bare, [],
                         "_http.py 出现裸 except:（行 %r）" % bare)


if __name__ == "__main__":
    unittest.main()
