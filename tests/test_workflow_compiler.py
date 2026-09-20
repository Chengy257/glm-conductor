#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""runtime.workflow（v2.4 原生 Workflow 编译器包）单元测试（工作包 P1-D，单元 U4）。

运行：
    cd <repo_root> && python3 -X utf8 -m unittest tests.test_workflow_compiler -v

覆盖（规格 U4 最低集 + 补充）：
    - compiler.compile_workflow：同输入两次编译字节全等（确定性红线，
      含输入顺序无关的更强形态）；全部节点 id 出现在 actor 标签
      （node-<id>）与结果映射（allResults["<id>"]）；阶段结构——
      await Promise.all 次数 == ownership.plan_stages 阶段组数、组间
      顺序 await（phase 与汇合点的位置交错）、phase 名称形如
      「阶段 i: 节点 id 列表」；NodeResult 接口恰六字段 + status 三值；
      最终 return 按节点 id 升序聚合；ask 以 actor 方法调用形态生成
      （含 .ask<NodeResult>(，且零自由函数 ask<NodeResult>(agent 前缀）；
      ask 指令内嵌 objective /
      ownership / interfaces / constraints / local_check / 任务背景 /
      结果契约；生成源禁词扫描（permit / lease / credential / api_key /
      secret）零命中；无时间戳 / 无随机面；形状 / 图 / task_context
      校验失败抛 WorkflowCompileError；ownership 歧义模式抛
      OwnershipConflictError（冲突即抛）；scope 明确重叠被确定性串行
      （不是错误）；空 DAG 允许；
    - persona：TEXT_WORKER_PERSONA 迁移语义锚点 + 零 permit / receipt
      词；build_ask 含 objective、ownership、interfaces、constraints、
      local_check、任务背景与结果契约六字段，可选字段缺省占位；
    - adapter：record_run / load_run 往返；artifact_path / node_map
      可选字段；原子写行为（durable_io 唯一临时名，写后无
      .durable-tmp. 残留、重复写整条替换、单文件）；缺失 / 损坏 /
      task_ref 错配 → None（fail-open）；记录字段白名单——零子代理
      状态镜像（running / retry / token / health 零命中）；特殊字符与
      超长 task_ref 不逃逸记录目录、不互相碰撞；非法输入抛
      WorkflowRunError；
    - 导入冒烟：runtime.workflow 及三个子模块可独立导入，包级重导出
      与子模块同源。

全部离线：adapter 测试经整替换模块级 RUNS_DIR 注入临时目录（绝不触碰
仓库内真实 .glm-conductor/ 账本）；生成源只做静态断言、绝不执行。
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "glm-conductor"))
from runtime import durable_io, ownership, work_unit
from runtime.workflow import adapter, compiler, persona


# —— 测试夹具 ——

TASK_CONTEXT = {
    "task_ref": "T-U4-001",
    "goal": "为认证模块交付实现、文档与测试",
    "repository": "glm-conductor",
}

# 禁词（生成源与人设文本零命中；均为子串级扫描）
FORBIDDEN_SOURCE_TOKENS = ("permit", "lease", "credential", "api_key", "secret")
FORBIDDEN_PERSONA_TOKENS = ("permit", "receipt")


def sample_nodes():
    """三节点样例 DAG：impl-auth / impl-docs 并行（scope 不相交），
    impl-tests 依赖 impl-auth——预期两个阶段。"""
    return [
        work_unit.new_node(
            "impl-auth", "实现认证模块的签名校验",
            ownership=("src/auth/**",),
            interfaces=("IAuth",),
            constraints=("禁改数据库 schema",),
            local_check=("python3 -m compileall src/auth",)),
        work_unit.new_node(
            "impl-docs", "补齐认证模块的用法文档",
            ownership=("docs/auth/**",)),
        work_unit.new_node(
            "impl-tests", "补齐认证模块的回归测试",
            depends_on=("impl-auth",),
            ownership=("tests/auth/**",)),
    ]


SAMPLE_NODE_IDS = ("impl-auth", "impl-docs", "impl-tests")


def compile_sample():
    """样例 DAG 的编译产物（多处断言共用）。"""
    return compiler.compile_workflow(sample_nodes(), TASK_CONTEXT)


# —— compiler：确定性 ——

class CompileDeterminismTest(unittest.TestCase):
    """确定性红线：同输入两次编译字节全等；无时间戳 / 随机面。"""

    def test_double_compile_byte_identical(self):
        first = compile_sample()
        second = compiler.compile_workflow(sample_nodes(), TASK_CONTEXT)
        self.assertEqual(first, second)
        self.assertEqual(first.encode("utf-8"), second.encode("utf-8"))

    def test_input_order_independent(self):
        # 更强形态：节点数组顺序不同，编译产物仍逐字节相同
        # （plan_stages 文档契约：同一图不同输入顺序 → 相同输出）
        ordered = compile_sample()
        shuffled = compiler.compile_workflow(
            list(reversed(sample_nodes())), TASK_CONTEXT)
        self.assertEqual(ordered, shuffled)

    def test_no_timestamp_or_random_surface(self):
        source = compile_sample()
        for banned in ("new Date", "Date.now", "Date(", "Math.random",
                       "created_at", "timestamp"):
            self.assertNotIn(banned, source,
                             "生成源出现时间戳 / 随机面：%r" % banned)


# —— compiler：结构 ——

class CompileStructureTest(unittest.TestCase):
    """阶段结构、actor 标签、结果映射、NodeResult 接口与最终聚合。"""

    def setUp(self):
        self.source = compile_sample()
        self.stages = ownership.plan_stages(sample_nodes())

    def test_all_node_ids_in_actor_labels(self):
        for node_id in SAMPLE_NODE_IDS:
            self.assertIn('"node-%s"' % node_id, self.source,
                          "节点 id 未出现在 actor 标签：%s" % node_id)

    def test_all_node_ids_in_result_mapping(self):
        for node_id in SAMPLE_NODE_IDS:
            key = 'allResults["%s"]' % node_id
            self.assertEqual(self.source.count(key), 2,
                             "结果映射键应恰出现两次（写 + 返回）：%s" % key)

    def test_promise_all_count_equals_stage_count(self):
        self.assertEqual(self.stages,
                         [["impl-auth", "impl-docs"], ["impl-tests"]])
        self.assertEqual(self.source.count("await Promise.all(["),
                         len(self.stages))

    def test_stages_sequential_between_groups(self):
        # 组间顺序 await：每组 phase 标记之后才有本组汇合点，
        # 且下一组 phase 在本组汇合点之后（位置严格交错）
        positions = []
        stage_number = 0
        for node_ids in self.stages:
            stage_number += 1
            phase_at = self.source.index(
                'phase("阶段 %d: %s")'
                % (stage_number, ", ".join(sorted(node_ids))))
            join_at = self.source.index("await Promise.all([", phase_at)
            positions.append((phase_at, join_at))
        for index in range(len(positions) - 1):
            self.assertLess(positions[index][1], positions[index + 1][0],
                            "阶段组间未保持顺序 await：%r" % (positions,))

    def test_phase_names_list_node_ids(self):
        self.assertIn('phase("阶段 1: impl-auth, impl-docs")', self.source)
        self.assertIn('phase("阶段 2: impl-tests")', self.source)

    def test_node_result_interface_exact_fields(self):
        self.assertIn("interface NodeResult {", self.source)
        for field in compiler.NODE_RESULT_FIELDS:
            self.assertIn("  %s:" % field, self.source)
        self.assertIn('  status: %s;' % compiler.STATUS_UNION, self.source)
        self.assertEqual(
            compiler.NODE_RESULT_FIELDS,
            ("node_id", "status", "changes", "local_checks",
             "reassessment", "gaps"))

    def test_final_return_sorted_by_node_id(self):
        tail = self.source[self.source.index("最终聚合返回"):]
        self.assertIn("return [", tail)
        offsets = [tail.index('allResults["%s"]' % node_id)
                   for node_id in sorted(SAMPLE_NODE_IDS)]
        self.assertEqual(offsets, sorted(offsets),
                         "最终 return 未按节点 id 升序聚合")

    def test_ask_embeds_node_context(self):
        # 节点 id 原样出现在 ask 指令（标题点名）与全部声明细节
        self.assertIn("【节点 impl-auth】", self.source)
        self.assertIn("实现认证模块的签名校验", self.source)
        self.assertIn("src/auth/**", self.source)
        self.assertIn("IAuth", self.source)
        self.assertIn("禁改数据库 schema", self.source)
        self.assertIn("python3 -m compileall src/auth", self.source)
        self.assertIn("T-U4-001", self.source)
        self.assertIn("为认证模块交付实现、文档与测试", self.source)
        self.assertIn("glm-conductor", self.source)

    def test_ask_is_actor_method_call(self):
        # ask 是 agent 返回的 actor 的方法（facade 契约），不是自由函数：
        # 每节点恰一处 agent("node-<id>", persona).ask<NodeResult>(指令)
        self.assertEqual(self.source.count("agent(\"node-"),
                         len(SAMPLE_NODE_IDS))
        self.assertEqual(self.source.count(".ask<NodeResult>("),
                         len(SAMPLE_NODE_IDS))
        self.assertNotIn("ask<NodeResult>(agent", self.source,
                         "生成源出现自由函数 ask 形态（应为 actor 方法）")

    def test_persona_embedded_from_single_source(self):
        self.assertIn("const persona: string =", self.source)
        embedded = json.dumps(persona.TEXT_WORKER_PERSONA,
                              ensure_ascii=False)
        self.assertIn(embedded, self.source)

    def test_empty_dag_allowed(self):
        source = compiler.compile_workflow([], TASK_CONTEXT)
        self.assertNotIn("await Promise.all([", source)
        self.assertIn("return [];", source)


# —— compiler：禁词 ——

class CompileForbiddenTokenTest(unittest.TestCase):
    """生成源禁词扫描（子串级、大小写不敏感）。"""

    def test_forbidden_tokens_absent(self):
        source = compile_sample().lower()
        for token in FORBIDDEN_SOURCE_TOKENS:
            self.assertNotIn(token, source, "生成源出现禁词：%r" % token)

    def test_forbidden_tokens_absent_for_overlapping_stages(self):
        # 重叠 scope 串行化路径的产物同样过禁词扫描
        nodes = [
            work_unit.new_node("impl-one", "同文件改动一",
                               ownership=("src/same.ts",)),
            work_unit.new_node("impl-two", "同文件改动二",
                               ownership=("src/same.ts",)),
        ]
        source = compiler.compile_workflow(nodes, TASK_CONTEXT).lower()
        for token in FORBIDDEN_SOURCE_TOKENS:
            self.assertNotIn(token, source)


# —— compiler：校验与冲突 ——

class CompileValidationTest(unittest.TestCase):
    """形状 / 图 / task_context 校验失败 → WorkflowCompileError；
    ownership 歧义 → OwnershipConflictError（冲突即抛）。"""

    def test_missing_required_key(self):
        bad = {"id": "impl-x", "depends_on": [], "ownership": ["src/x/**"]}
        with self.assertRaises(compiler.WorkflowCompileError) as caught:
            compiler.compile_workflow([bad], TASK_CONTEXT)
        self.assertIn("node 缺少必填键 objective", str(caught.exception))

    def test_duplicate_ids_rejected(self):
        nodes = [
            work_unit.new_node("impl-a", "目标", ownership=("src/a/**",)),
            work_unit.new_node("impl-a", "目标", ownership=("src/b/**",)),
        ]
        with self.assertRaises(compiler.WorkflowCompileError) as caught:
            compiler.compile_workflow(nodes, TASK_CONTEXT)
        self.assertIn("重复 id", str(caught.exception))

    def test_missing_dependency_rejected(self):
        nodes = [
            work_unit.new_node("impl-a", "目标", depends_on=("ghost",),
                               ownership=("src/a/**",)),
        ]
        with self.assertRaises(compiler.WorkflowCompileError) as caught:
            compiler.compile_workflow(nodes, TASK_CONTEXT)
        self.assertIn("引用不存在的 id", str(caught.exception))

    def test_cycle_rejected(self):
        nodes = [
            work_unit.new_node("impl-a", "目标", depends_on=("impl-b",),
                               ownership=("src/a/**",)),
            work_unit.new_node("impl-b", "目标", depends_on=("impl-a",),
                               ownership=("src/b/**",)),
        ]
        with self.assertRaises(compiler.WorkflowCompileError) as caught:
            compiler.compile_workflow(nodes, TASK_CONTEXT)
        self.assertIn("环", str(caught.exception))

    def test_nodes_must_be_list(self):
        with self.assertRaises(compiler.WorkflowCompileError) as caught:
            compiler.compile_workflow(tuple(sample_nodes()), TASK_CONTEXT)
        self.assertIn("nodes 必须是数组", str(caught.exception))

    def test_task_context_required_keys(self):
        with self.assertRaises(compiler.WorkflowCompileError) as caught:
            compiler.compile_workflow(sample_nodes(),
                                      {"task_ref": "T-1"})
        self.assertIn("task_context.goal", str(caught.exception))
        self.assertIn("task_context.repository", str(caught.exception))
        with self.assertRaises(compiler.WorkflowCompileError):
            compiler.compile_workflow(sample_nodes(), None)

    def test_ambiguous_ownership_conflict_raises(self):
        # 冲突即抛：歧义模式（匹配一切路径）在 plan_stages 编译期拒绝
        nodes = [
            work_unit.new_node("impl-wide", "范围歧义的目标",
                               ownership=("**",)),
        ]
        with self.assertRaises(ownership.OwnershipConflictError):
            compiler.compile_workflow(nodes, TASK_CONTEXT)

    def test_overlapping_scopes_serialized_not_error(self):
        # scope 明确重叠不是错误：被 plan_stages 确定性串行进相邻两组
        nodes = [
            work_unit.new_node("impl-one", "同文件改动一",
                               ownership=("src/same.ts",)),
            work_unit.new_node("impl-two", "同文件改动二",
                               ownership=("src/same.ts",)),
        ]
        source = compiler.compile_workflow(nodes, TASK_CONTEXT)
        self.assertEqual(source.count("await Promise.all(["), 2)
        self.assertIn('phase("阶段 1: impl-one")', source)
        self.assertIn('phase("阶段 2: impl-two")', source)


# —— persona ——

class PersonaTest(unittest.TestCase):
    """TEXT_WORKER_PERSONA 语义锚点与禁词；build_ask 小节齐全。"""

    def test_persona_no_permit_or_receipt(self):
        text = persona.TEXT_WORKER_PERSONA.lower()
        for token in FORBIDDEN_PERSONA_TOKENS:
            self.assertNotIn(token, text, "persona 出现禁词：%r" % token)

    def test_persona_migrated_semantics(self):
        text = persona.TEXT_WORKER_PERSONA
        for anchor in ("objective", "ownership", "interfaces", "constraints",
                       "reassessment", "local_check", "重新设计", "重估",
                       "结构化", "伪造"):
            self.assertIn(anchor, text, "persona 缺语义锚点：%r" % anchor)

    def test_persona_forbidden_tokens_safe_for_embedding(self):
        # persona 会被原样内嵌进生成源，须同时满足生成源禁词集
        text = persona.TEXT_WORKER_PERSONA.lower()
        for token in FORBIDDEN_SOURCE_TOKENS:
            self.assertNotIn(token, text)

    def test_build_ask_sections_complete(self):
        node = sample_nodes()[0]
        ask = persona.build_ask(node, TASK_CONTEXT)
        for section in ("【节点 impl-auth】", "objective", "ownership",
                        "interfaces", "constraints", "local_check",
                        "任务背景", "结果契约", "node_id", "status",
                        "changes", "local_checks", "reassessment", "gaps",
                        "complete", "partial", "blocked",
                        "T-U4-001", "glm-conductor"):
            self.assertIn(section, ask, "ask 缺小节 / 字段：%r" % section)
        for word in FORBIDDEN_PERSONA_TOKENS + FORBIDDEN_SOURCE_TOKENS:
            self.assertNotIn(word, ask.lower())

    def test_build_ask_empty_optional_placeholders(self):
        bare = work_unit.new_node("impl-bare", "只有必填的目标",
                                  ownership=("src/bare/**",))
        ask = persona.build_ask(bare, TASK_CONTEXT)
        self.assertIn("（本节点未声明接口面）", ask)
        self.assertIn("（本节点未声明额外约束）", ask)
        self.assertIn("（本节点未声明本地检查）", ask)

    def test_build_ask_requires_dict_node(self):
        with self.assertRaises(TypeError):
            persona.build_ask("not-a-dict", TASK_CONTEXT)


# —— adapter ——

class AdapterFixture(unittest.TestCase):
    """公共夹具：临时目录 + 整替换 RUNS_DIR（绝不触碰真实账本）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.runs_dir = os.path.join(self._tmp.name, ".glm-conductor",
                                     "workflow-runs")
        self._original_runs_dir = adapter.RUNS_DIR
        adapter.RUNS_DIR = self.runs_dir
        self.addCleanup(setattr, adapter, "RUNS_DIR",
                        self._original_runs_dir)

    def record_names(self):
        """记录目录内的全部文件名（目录未创建 → 空表；残留断言用）。"""
        if not os.path.isdir(self.runs_dir):
            return []
        return sorted(os.listdir(self.runs_dir))


class AdapterRoundtripTest(AdapterFixture):
    """record / load 往返与可选字段。"""

    def test_roundtrip_minimal_record(self):
        stored = adapter.record_run("T-1", "run-1")
        self.assertEqual(sorted(stored.keys()),
                         sorted(adapter.RECORD_FIXED_FIELDS))
        self.assertEqual(stored["task_ref"], "T-1")
        self.assertEqual(stored["workflow_run_id"], "run-1")
        self.assertTrue(stored["created_at"].endswith("Z"))
        self.assertEqual(adapter.load_run("T-1"), stored)

    def test_optional_fields_persisted(self):
        stored = adapter.record_run(
            "T-2", "run-2", artifact_path=".zcode/workflow-runs/r2.mjs",
            node_map={"impl-auth": "node-impl-auth"})
        self.assertEqual(sorted(stored.keys()),
                         sorted(adapter.RECORD_FIXED_FIELDS
                                + adapter.RECORD_OPTIONAL_FIELDS))
        loaded = adapter.load_run("T-2")
        self.assertEqual(loaded["artifact_path"],
                         ".zcode/workflow-runs/r2.mjs")
        self.assertEqual(loaded["node_map"],
                         {"impl-auth": "node-impl-auth"})

    def test_optional_fields_omitted_when_none(self):
        stored = adapter.record_run("T-3", "run-3", artifact_path=None,
                                    node_map=None)
        self.assertNotIn("artifact_path", stored)
        self.assertNotIn("node_map", stored)

    def test_missing_record_returns_none(self):
        self.assertIsNone(adapter.load_run("never-recorded"))

    def test_corrupt_record_returns_none(self):
        adapter.record_run("T-bad", "run-bad")
        with open(adapter.record_path("T-bad"), "wb") as handle:
            handle.write(b"this is not json")
        self.assertIsNone(adapter.load_run("T-bad"))

    def test_task_ref_mismatch_returns_none(self):
        # 防手挪文件错配：记录内 task_ref 与请求不符 → None
        adapter.record_run("T-real", "run-real")
        path = adapter.record_path("T-real")
        with open(path, "r", encoding="utf-8") as handle:
            record = json.load(handle)
        record["task_ref"] = "T-other"
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(record, handle)
        self.assertIsNone(adapter.load_run("T-real"))


class AdapterAtomicWriteTest(AdapterFixture):
    """原子写行为：无临时残留、整条替换、单文件。"""

    def test_no_temp_residue_after_write(self):
        adapter.record_run("T-atom", "run-a")
        for name in self.record_names():
            self.assertNotIn(durable_io.TEMP_MARKER, name,
                             "写后残留临时文件：%s" % name)

    def test_rewrite_replaces_single_file(self):
        adapter.record_run("T-atom", "run-first")
        adapter.record_run("T-atom", "run-second")
        self.assertEqual(self.record_names(),
                         [os.path.basename(adapter.record_path("T-atom"))])
        self.assertEqual(adapter.load_run("T-atom")["workflow_run_id"],
                         "run-second")

    def test_directory_auto_created(self):
        self.assertFalse(os.path.isdir(self.runs_dir))
        adapter.record_run("T-atom", "run-a")
        self.assertTrue(os.path.isdir(self.runs_dir))


class AdapterNoStateMirroringTest(AdapterFixture):
    """白名单字段之外零落盘：零子代理状态镜像。"""

    def test_record_keys_within_whitelist(self):
        stored = adapter.record_run(
            "T-m", "run-m", artifact_path="a.mjs",
            node_map={"n": "node-n"})
        self.assertEqual(
            sorted(stored.keys()),
            sorted(adapter.RECORD_FIXED_FIELDS
                   + adapter.RECORD_OPTIONAL_FIELDS))

    def test_no_subagent_state_words(self):
        adapter.record_run("T-m", "run-m", node_map={"n": "node-n"})
        with open(adapter.record_path("T-m"), "r", encoding="utf-8") as handle:
            raw = handle.read()
        for banned in ("running", "retry", "token", "health",
                       "attempt", "quota"):
            self.assertNotIn(banned, raw.lower(),
                             "记录镜像了子代理 / 运行时状态：%r" % banned)


class AdapterNamingTest(AdapterFixture):
    """文件名编码：特殊字符不逃逸记录目录、不互相碰撞。"""

    def test_special_chars_stay_inside_runs_dir(self):
        stored = adapter.record_run("../..\\evil/t-ref", "run-x")
        self.assertEqual(self.record_names(),
                         [os.path.basename(adapter.record_path(
                             "../..\\evil/t-ref"))])
        self.assertEqual(adapter.load_run("../..\\evil/t-ref"), stored)

    def test_encoded_refs_do_not_collide(self):
        first = adapter.record_run("a/b", "run-ab")
        second = adapter.record_run("a%2Fb", "run-quoted")
        self.assertNotEqual(adapter.record_path("a/b"),
                            adapter.record_path("a%2Fb"))
        self.assertEqual(len(self.record_names()), 2)
        self.assertEqual(adapter.load_run("a/b"), first)
        self.assertEqual(adapter.load_run("a%2Fb"), second)

    def test_overlong_task_ref_roundtrips(self):
        long_ref = "T-" + "很长的任务引用" * 30
        stored = adapter.record_run(long_ref, "run-long")
        self.assertLessEqual(
            len(os.path.basename(adapter.record_path(long_ref))), 128)
        self.assertEqual(adapter.load_run(long_ref), stored)


class AdapterValidationTest(AdapterFixture):
    """非法输入 → WorkflowRunError，且不产生任何文件。"""

    def test_invalid_inputs_rejected(self):
        with self.assertRaises(adapter.WorkflowRunError):
            adapter.record_run("", "run-1")
        with self.assertRaises(adapter.WorkflowRunError):
            adapter.record_run(None, "run-1")
        with self.assertRaises(adapter.WorkflowRunError):
            adapter.record_run("T-1", "")
        with self.assertRaises(adapter.WorkflowRunError):
            adapter.record_run("T-1", None)
        with self.assertRaises(adapter.WorkflowRunError):
            adapter.record_run("T-1", "run-1", artifact_path="")
        with self.assertRaises(adapter.WorkflowRunError):
            adapter.record_run("T-1", "run-1", node_map=["x"])
        with self.assertRaises(adapter.WorkflowRunError):
            adapter.record_run("T-1", "run-1", node_map={1: "node-1"})
        with self.assertRaises(adapter.WorkflowRunError):
            adapter.load_run("")
        self.assertEqual(self.record_names(), [])


# —— 导入冒烟 ——

class ImportSmokeTest(unittest.TestCase):
    """包可独立导入；包级重导出与子模块同源。"""

    def test_package_and_submodules_importable(self):
        import runtime.workflow as package
        from runtime.workflow import adapter as adapter_module
        from runtime.workflow import compiler as compiler_module
        from runtime.workflow import persona as persona_module
        self.assertIs(package.compile_workflow,
                      compiler_module.compile_workflow)
        self.assertIs(package.WorkflowCompileError,
                      compiler_module.WorkflowCompileError)
        self.assertIs(package.TEXT_WORKER_PERSONA,
                      persona_module.TEXT_WORKER_PERSONA)
        self.assertIs(package.build_ask, persona_module.build_ask)
        self.assertIs(package.record_run, adapter_module.record_run)
        self.assertIs(package.load_run, adapter_module.load_run)
        self.assertIs(package.WorkflowRunError,
                      adapter_module.WorkflowRunError)


if __name__ == "__main__":
    unittest.main()
