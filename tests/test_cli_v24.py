#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v2.4 Phase 1 新路径 CLI 子命令测试（P1-F，单元 U6）。

锚定对象：cli.py 纯追加的五个子命令（v24-compile / writer-acquire /
writer-release / writer-show / v24-record-run）——规格 U6 测试清单：

  1. v24-compile：固定小 DAG 黄金输出（phase 名含节点 id、
     await Promise.all、NodeResult 接口）；--out 落盘 + stdout 只打
     一行 JSON 摘要（ok / stages / nodes / out）且落盘内容与 stdout
     直出形态字节全等（确定性红线在 CLI 面复验）；--task-ref 覆盖；
     编译面零写副作用（除 --out 外零新文件——生成源绝不自动执行的
     机械锚）；非法 DAG——缺失依赖 / 环 / ownership 歧义冲突——
     退出码 2 且错误信息含节点 id；节点形状 / task_context 缺键错误
     聚合报出；DAG JSON 缺失 / 坏 JSON → 2；用法错 → 2。
  2. writer-acquire / writer-release / writer-show：acquire → show
     往返（holder 四字段 + 记录真实落盘）；异 task 二次 acquire 冲突
     上报（conflict 三键 + 退出码 1 + 记录零改动）；owner release
     成功 → show 归空；无预约 release 幂等（退出码 0）；非 owner
     release 拒绝（ok=false + 当前持有者 + 退出码 1）；--force 显式
     清除路径（输出被清除的持有者四字段信息；无预约幂等 ok）；
     空 task_id（结构非法）→ 2；用法错 → 2。
  3. v24-record-run：落盘往返（stdout 记录与盘上
     .glm-conductor/workflow-runs/ 记录逐字段一致；--artifact 可选
     字段）；同 task_ref 重复调用整条替换；空 run-id → 2；用法错 → 2。

调用方式：模仿 tests/test_cli_extensions.py 的真实子进程冒烟
（subprocess.run([sys.executable, CLI_PATH, ...], encoding="utf-8")）——
全部用例经子进程调 cli.py；cwd 一律钉在 tempfile 临时仓库根
（v24-record-run 的记录目录相对 cwd 落盘、writer 守卫记录落
<repo_root>/.glm-conductor/，绝不触碰本仓库真实账本）。仅 Python 3.7
标准库。

运行：
    cd <repo_root> && python3 -X utf8 -m unittest tests.test_cli_v24 -v
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

CLI_PATH = (Path(__file__).resolve().parents[1] / "plugins" /
            "glm-conductor" / "runtime" / "cli.py")

TASK_CONTEXT = {
    "task_ref": "T-U6-001",
    "goal": "为认证模块交付实现与测试",
    "repository": "glm-conductor",
}

# 固定小 DAG（黄金输出夹具）：impl-auth / impl-docs 并行（scope 不相交，
# 同阶段），impl-tests 依赖 impl-auth（第二阶段）——预期恰两个阶段
DAG_JSON = {
    "task_context": dict(TASK_CONTEXT),
    "nodes": [
        {"id": "impl-auth", "objective": "实现认证模块的签名校验",
         "depends_on": [], "ownership": ["src/auth/**"]},
        {"id": "impl-docs", "objective": "补齐认证模块的用法文档",
         "depends_on": [], "ownership": ["docs/auth/**"]},
        {"id": "impl-tests", "objective": "补齐认证模块的回归测试",
         "depends_on": ["impl-auth"], "ownership": ["tests/auth/**"]},
    ],
}


def dag_with_nodes(nodes):
    """同 task_context、换节点数组的 DAG dict（非法 DAG 用例构造）。"""
    return {"task_context": dict(TASK_CONTEXT), "nodes": nodes}


class CliV24Fixture(unittest.TestCase):
    """公共夹具：临时目录即仓库根，全部子进程调用 cwd 钉在临时目录。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = self._tmp.name

    def run_cli(self, *args):
        """真实子进程调用 cli.py，返回 (退出码, stdout 全文, stderr 全文)。"""
        proc = subprocess.run(
            [sys.executable, str(CLI_PATH)] + list(args),
            cwd=self.repo, text=True, capture_output=True,
            encoding="utf-8", errors="replace")
        return proc.returncode, proc.stdout, proc.stderr

    def run_cli_json(self, *args):
        """调用 cli.py 并把 stdout 单行 JSON 解析为 dict（单行契约锚）。"""
        code, stdout, stderr = self.run_cli(*args)
        return code, json.loads(stdout), stderr

    def write_dag(self, dag, name="dag.json"):
        """DAG dict 写入临时仓库根的 JSON 文件，返回文件名。"""
        path = os.path.join(self.repo, name)
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(dag, handle, ensure_ascii=False)
        return name

    def listdir(self):
        """临时仓库根当前条目（零写副作用断言用）。"""
        return sorted(os.listdir(self.repo))

    def decoded(self, stdout):
        """单行 JSON stdout 的解码文本（中文断言用——_emit 走
        ensure_ascii=True，原文以 \\uXXXX 转义）。"""
        return json.dumps(json.loads(stdout), ensure_ascii=False)


# —— 1. v24-compile：黄金输出 ——

class V24CompileGoldenTest(CliV24Fixture):
    """v24-compile 成功路径：黄金行 / --out 摘要 / 确定性 / 零副作用。"""

    def test_stdout_source_golden_lines(self):
        name = self.write_dag(DAG_JSON)
        code, stdout, stderr = self.run_cli("v24-compile", name)
        self.assertEqual(code, 0, stderr)
        self.assertEqual(stderr, "")
        # 黄金行：phase 名含节点 id（阶段序列来自 ownership 规划）、
        # 组内并行 Promise.all、NodeResult 接口
        self.assertIn('phase("阶段 1: impl-auth, impl-docs")', stdout)
        self.assertIn('phase("阶段 2: impl-tests")', stdout)
        self.assertEqual(stdout.count("await Promise.all(["), 2)
        self.assertIn("interface NodeResult {", stdout)
        # 节点 id 原样出现在 actor 标签与结果映射
        self.assertIn('"node-impl-auth"', stdout)
        self.assertIn('allResults["impl-tests"]', stdout)
        self.assertIn("return [", stdout)
        # 成功路径 stdout 是 TS 源而非 JSON（单行 JSON 契约的显式例外）
        with self.assertRaises(ValueError):
            json.loads(stdout)
        # 零写副作用：除 DAG JSON 外无任何新文件（生成源绝不自动执行、
        # 编译面不落任何状态）
        self.assertEqual(self.listdir(), [name])

    def test_out_file_and_single_line_json_summary(self):
        name = self.write_dag(DAG_JSON)
        code, stdout, stderr = self.run_cli(
            "v24-compile", name, "--out", "compiled.mjs")
        self.assertEqual(code, 0, stderr)
        # stdout 只打一行 JSON 摘要：ok / stages / nodes / out
        self.assertEqual(len(stdout.strip().splitlines()), 1)
        payload = json.loads(stdout)
        self.assertEqual(
            sorted(payload.keys()), ["nodes", "ok", "out", "stages"])
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["stages"], 2)
        self.assertEqual(payload["nodes"], 3)
        self.assertEqual(payload["out"], "compiled.mjs")
        self.assertTrue(os.path.isfile(os.path.join(self.repo,
                                                    "compiled.mjs")))
        # 落盘内容与 stdout 直出形态字节全等（同一编译器的确定性）
        with open(os.path.join(self.repo, "compiled.mjs"), "r",
                  encoding="utf-8", newline="") as handle:
            file_source = handle.read()
        code2, direct, _stderr2 = self.run_cli("v24-compile", name)
        self.assertEqual(code2, 0)
        self.assertEqual(file_source, direct)

    def test_task_ref_override(self):
        name = self.write_dag(DAG_JSON)
        code, stdout, _stderr = self.run_cli(
            "v24-compile", name, "--task-ref", "T-OVERRIDE-42")
        self.assertEqual(code, 0)
        # 覆盖值回显在头部注释与 ask 指令的任务背景里
        self.assertIn("T-OVERRIDE-42", stdout)
        self.assertNotIn(TASK_CONTEXT["task_ref"], stdout)


# —— 2. v24-compile：非法 DAG ——

class V24CompileInvalidTest(CliV24Fixture):
    """非法 DAG：退出码 2 且错误信息含涉事节点 id；错误聚合报出。"""

    def test_missing_dependency_exit_2_reports_node_id(self):
        name = self.write_dag(dag_with_nodes([
            {"id": "impl-a", "objective": "目标",
             "depends_on": ["ghost"], "ownership": ["src/a/**"]},
        ]))
        code, stdout, _stderr = self.run_cli("v24-compile", name)
        self.assertEqual(code, 2)
        payload = json.loads(stdout)
        self.assertFalse(payload["ok"])
        self.assertIn("impl-a", stdout)   # 错误信息含节点 id
        self.assertIn("ghost", stdout)    # 与缺失的依赖 id
        self.assertEqual(self.listdir(), [name])  # 失败路径零落盘

    def test_cycle_exit_2_reports_member_ids(self):
        name = self.write_dag(dag_with_nodes([
            {"id": "impl-a", "objective": "目标",
             "depends_on": ["impl-b"], "ownership": ["src/a/**"]},
            {"id": "impl-b", "objective": "目标",
             "depends_on": ["impl-a"], "ownership": ["src/b/**"]},
        ]))
        code, stdout, _stderr = self.run_cli("v24-compile", name)
        self.assertEqual(code, 2)
        text = self.decoded(stdout)
        self.assertIn("环", text)
        self.assertIn("impl-a", text)
        self.assertIn("impl-b", text)

    def test_ownership_conflict_exit_2_reports_node_id(self):
        # 歧义模式（"**" 覆盖一切路径）：ownership 阶段规划编译期拒绝
        name = self.write_dag(dag_with_nodes([
            {"id": "impl-wide", "objective": "范围歧义的目标",
             "depends_on": [], "ownership": ["**"]},
        ]))
        code, stdout, _stderr = self.run_cli("v24-compile", name)
        self.assertEqual(code, 2)
        text = self.decoded(stdout)
        self.assertIn("impl-wide", text)
        self.assertIn("歧义", text)

    def test_node_shape_errors_aggregated(self):
        # 缺 objective + 自依赖：节点级错误一次聚合报出（不短路）
        name = self.write_dag(dag_with_nodes([
            {"id": "impl-x", "depends_on": ["impl-x"],
             "ownership": ["src/x/**"]},
        ]))
        code, stdout, _stderr = self.run_cli("v24-compile", name)
        self.assertEqual(code, 2)
        text = self.decoded(stdout)
        self.assertIn("objective", text)
        self.assertIn("自依赖", text)
        self.assertIn("impl-x", text)

    def test_incomplete_task_context_exit_2(self):
        # task_context 缺 goal / repository：逐键错误聚合报出
        name = self.write_dag({"task_context": {"task_ref": "T-1"},
                               "nodes": [
            {"id": "impl-a", "objective": "目标",
             "depends_on": [], "ownership": ["src/a/**"]}]})
        code, stdout, _stderr = self.run_cli("v24-compile", name)
        self.assertEqual(code, 2)
        text = self.decoded(stdout)
        self.assertIn("task_context.goal", text)
        self.assertIn("task_context.repository", text)

    def test_missing_task_context_exit_2(self):
        # task_context 键整个缺失：编译器报「必须是 JSON 对象」
        name = self.write_dag({"nodes": [
            {"id": "impl-a", "objective": "目标",
             "depends_on": [], "ownership": ["src/a/**"]}]})
        code, stdout, _stderr = self.run_cli("v24-compile", name)
        self.assertEqual(code, 2)
        self.assertIn("task_context", self.decoded(stdout))

    def test_missing_dag_file_exit_2(self):
        code, stdout, _stderr = self.run_cli("v24-compile", "no-such.json")
        self.assertEqual(code, 2)
        payload = json.loads(stdout)
        self.assertFalse(payload["ok"])
        self.assertIn("no-such.json", stdout)

    def test_corrupt_dag_json_exit_2(self):
        with open(os.path.join(self.repo, "bad.json"), "w",
                  encoding="utf-8") as handle:
            handle.write("{not json")
        code, stdout, _stderr = self.run_cli("v24-compile", "bad.json")
        self.assertEqual(code, 2)
        self.assertFalse(json.loads(stdout)["ok"])

    def test_usage_errors_exit_2(self):
        # 零参数 / 未知旗标 / 旗标缺取值 → 用法错 2
        code, _stdout, _stderr = self.run_cli("v24-compile")
        self.assertEqual(code, 2)
        name = self.write_dag(DAG_JSON)
        code, stdout, _stderr = self.run_cli(
            "v24-compile", name, "--bogus", "x")
        self.assertEqual(code, 2)
        code, _stdout, _stderr = self.run_cli("v24-compile", name, "--out")
        self.assertEqual(code, 2)


# —— 3. writer-acquire / writer-release / writer-show ——

class WriterGuardCliTest(CliV24Fixture):
    """写者守卫操作面：acquire → show 往返、冲突上报、--force 清除。"""

    def test_acquire_show_roundtrip(self):
        code, payload, _stderr = self.run_cli_json(
            "writer-acquire", self.repo, "task-a", "--run-id", "run-1")
        self.assertEqual(code, 0)
        self.assertEqual(payload, {"ok": True, "conflict": None})
        # 持久记录真实落盘（.glm-conductor/writer_guard.json）
        self.assertTrue(os.path.isfile(os.path.join(
            self.repo, ".glm-conductor", "writer_guard.json")))
        code, payload, _stderr = self.run_cli_json("writer-show", self.repo)
        self.assertEqual(code, 0)
        holder = payload["holder"]
        self.assertEqual(sorted(holder.keys()),
                         ["created_at", "repo_identity", "task_id",
                          "workflow_run_id"])
        self.assertEqual(holder["task_id"], "task-a")
        self.assertEqual(holder["workflow_run_id"], "run-1")
        self.assertTrue(holder["created_at"].endswith("Z"))
        self.assertEqual(holder["repo_identity"],
                         os.path.abspath(self.repo))

    def test_second_acquire_conflict_reported_exit_1(self):
        self.run_cli("writer-acquire", self.repo, "task-a",
                     "--run-id", "run-1")
        code, payload, _stderr = self.run_cli_json(
            "writer-acquire", self.repo, "task-b", "--run-id", "run-2")
        self.assertEqual(code, 1)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["conflict"]["task_id"], "task-a")
        self.assertEqual(payload["conflict"]["workflow_run_id"], "run-1")
        self.assertIn("created_at", payload["conflict"])
        # 记录零改动：show 仍见原持有者
        _code, payload, _ = self.run_cli_json("writer-show", self.repo)
        self.assertEqual(payload["holder"]["task_id"], "task-a")

    def test_release_by_owner_then_show_empty(self):
        self.run_cli("writer-acquire", self.repo, "task-a")
        code, payload, _stderr = self.run_cli_json(
            "writer-release", self.repo, "task-a")
        self.assertEqual(code, 0)
        self.assertEqual(payload, {"ok": True, "released": True,
                                   "conflict": None})
        _code, payload, _ = self.run_cli_json("writer-show", self.repo)
        self.assertIsNone(payload["holder"])
        # 释放后异 task 可获取
        code, payload, _ = self.run_cli_json(
            "writer-acquire", self.repo, "task-b", "--run-id", "run-9")
        self.assertEqual((code, payload["ok"]), (0, True))

    def test_release_without_reservation_idempotent(self):
        code, payload, _stderr = self.run_cli_json(
            "writer-release", self.repo, "task-a")
        self.assertEqual(code, 0)
        self.assertEqual(payload, {"ok": True, "released": False,
                                   "conflict": None})

    def test_release_by_non_owner_refused_exit_1(self):
        self.run_cli("writer-acquire", self.repo, "task-a",
                     "--run-id", "run-1")
        code, payload, _stderr = self.run_cli_json(
            "writer-release", self.repo, "task-b")
        self.assertEqual(code, 1)
        self.assertFalse(payload["ok"])
        self.assertFalse(payload["released"])
        self.assertEqual(payload["conflict"]["task_id"], "task-a")
        # 记录零改动
        _code, payload, _ = self.run_cli_json("writer-show", self.repo)
        self.assertEqual(payload["holder"]["task_id"], "task-a")

    def test_force_clears_holder_and_reports_it(self):
        self.run_cli("writer-acquire", self.repo, "task-a",
                     "--run-id", "run-1")
        # --force：运维检查后的显式清除路径——传入的 task_id 不是持有者
        # 也清除，且输出被清除的持有者四字段信息
        code, payload, _stderr = self.run_cli_json(
            "writer-release", self.repo, "task-other", "--force")
        self.assertEqual(code, 0)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["forced"])
        self.assertTrue(payload["released"])
        self.assertEqual(payload["cleared_holder"]["task_id"], "task-a")
        self.assertEqual(payload["cleared_holder"]["workflow_run_id"],
                         "run-1")
        _code, payload, _ = self.run_cli_json("writer-show", self.repo)
        self.assertIsNone(payload["holder"])

    def test_force_without_reservation_idempotent(self):
        code, payload, _stderr = self.run_cli_json(
            "writer-release", self.repo, "task-a", "--force")
        self.assertEqual(code, 0)
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["released"])
        self.assertIsNone(payload["cleared_holder"])

    def test_writer_show_empty_repo(self):
        code, payload, _stderr = self.run_cli_json("writer-show", self.repo)
        self.assertEqual(code, 0)
        self.assertIsNone(payload["holder"])

    def test_empty_task_id_exit_2(self):
        # 结构非法 id（空串）：参数值非法口径 → 2
        code, stdout, _stderr = self.run_cli(
            "writer-acquire", self.repo, "")
        self.assertEqual(code, 2)
        code, stdout, _stderr = self.run_cli(
            "writer-release", self.repo, "")
        self.assertEqual(code, 2)

    def test_writer_usage_errors_exit_2(self):
        code, _stdout, _stderr = self.run_cli("writer-acquire", self.repo)
        self.assertEqual(code, 2)
        code, _stdout, _stderr = self.run_cli(
            "writer-acquire", self.repo, "t", "--bogus", "x")
        self.assertEqual(code, 2)
        code, _stdout, _stderr = self.run_cli("writer-show")
        self.assertEqual(code, 2)
        code, _stdout, _stderr = self.run_cli("writer-release", self.repo)
        self.assertEqual(code, 2)
        code, _stdout, _stderr = self.run_cli(
            "writer-release", self.repo, "t", "--bogus")
        self.assertEqual(code, 2)
        code, _stdout, _stderr = self.run_cli(
            "writer-release", self.repo, "t", "--run-id")
        self.assertEqual(code, 2)


# —— 4. v24-record-run ——

class V24RecordRunCliTest(CliV24Fixture):
    """v24-record-run：落盘往返（记录目录相对 cwd 落盘）。"""

    def test_record_run_roundtrip_with_artifact(self):
        code, payload, _stderr = self.run_cli_json(
            "v24-record-run", "T-U6-001", "wf-run-42",
            "--artifact", ".zcode/workflows/compiled.mjs")
        self.assertEqual(code, 0)
        self.assertEqual(payload["task_ref"], "T-U6-001")
        self.assertEqual(payload["workflow_run_id"], "wf-run-42")
        self.assertEqual(payload["artifact_path"],
                         ".zcode/workflows/compiled.mjs")
        self.assertTrue(payload["created_at"].endswith("Z"))
        # 盘上记录与 stdout 逐字段一致（落盘往返）
        record_path = os.path.join(self.repo, ".glm-conductor",
                                   "workflow-runs", "run-T-U6-001.json")
        self.assertTrue(os.path.isfile(record_path))
        with open(record_path, "r", encoding="utf-8") as handle:
            on_disk = json.load(handle)
        self.assertEqual(on_disk, payload)

    def test_record_run_replaces_previous(self):
        self.run_cli("v24-record-run", "T-1", "run-first")
        code, payload, _stderr = self.run_cli_json(
            "v24-record-run", "T-1", "run-second")
        self.assertEqual(code, 0)
        self.assertEqual(payload["workflow_run_id"], "run-second")
        runs_dir = os.path.join(self.repo, ".glm-conductor",
                                "workflow-runs")
        self.assertEqual(os.listdir(runs_dir), ["run-T-1.json"])
        with open(os.path.join(runs_dir, "run-T-1.json"), "r",
                  encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["workflow_run_id"],
                             "run-second")

    def test_empty_run_id_exit_2(self):
        code, stdout, _stderr = self.run_cli("v24-record-run", "T-1", "")
        self.assertEqual(code, 2)
        self.assertIn("error", json.loads(stdout))

    def test_usage_errors_exit_2(self):
        code, _stdout, _stderr = self.run_cli("v24-record-run", "T-1")
        self.assertEqual(code, 2)
        code, _stdout, _stderr = self.run_cli(
            "v24-record-run", "T-1", "run-1", "--bogus", "x")
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
