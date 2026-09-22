#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v2.4 Phase 1 新路径 CLI 子命令测试（P1-F，单元 U6）+ v2.4.1 continuity hotfix CLI 面。

锚定对象：cli.py 纯追加的五个子命令（v24-compile / writer-acquire /
writer-release / writer-show / v24-record-run）与 v2.4.1 continuity
hotfix 追加的四子命令（workflow-model-select / quota-automation-bind /
quota-automation-clear / quota-continuity-preflight）——规格 U6 测试
清单 + 规格 §3.4（workflow-model-select CLI 测试）/ §4.5（H2 CLI 测试）：

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
  4. quota 生命周期四命令（v2.4 audit-fix AF-03；quota-wait /
     quota-resume-authorize / quota-resume-decision /
     quota-resume-confirm）：manual 缺省 quota-wait 进 waiting_quota
     （journal enter 事件）；authorize 落 mode=auto + max_resumes；
     EXHAUSTED/UNKNOWN → remain-waiting；AVAILABLE + 预算有余 + 已武装
     → resume-authorized（含 workflow_run_id / resume_count_after；
     v2.4.1 起决策的 resume-authorized 路径要求 auto 连续性已武装，
     prepare_waiting 缺省绑定见证 id）；confirm 恰消耗一预算；
     confirm 前重复 decision 不消耗预算；非 waiting 任务 confirm →
     1；预算耗尽 → waiting-user 且任务转 waiting_user；max_resumes
     非法（"abc" / "-1"）→ 2。
  5. workflow-model-select（v2.4.1 H1，submission 薄壳）：恰一 Flash
     自动选中（stdout 恰 {subagent_model, model_policy=
     "explicit-flash-required"}）；内联数组与文件两形态等价；零候选
     exit 2；多候选歧义 exit 2 且错误列出全部候选 id；显式 --model
     精确选中；显式未配置 / 显式主会话文本模型 exit 2；清单文件缺失 /
     坏 JSON / 顶层非数组 exit 2；用法错（零参数 / 未知旗标 / 旗标缺
     取值）exit 2。
  6. auto 连续性武装三命令（v2.4.1 H2；quota-automation-bind /
     quota-automation-clear / quota-continuity-preflight）：bind →
     armed 往返（quota_resume.automation_id 落盘、同 id 幂等、
     journal 零新增）；异 id 冲突 exit 1 且盘上零改动；clear 解除
     （幂等；给定 id 与存量不一致 exit 1）；preflight 三态 manual →
     0 / auto+armed → 0 / auto+unarmed → 2（主会话机械视作
     launch/resume 阻断）；任务缺失 exit 1；空 automation_id exit 1；
     auto 已授权未武装的 decision 经 CLI 仍 remain-waiting
     （continuity-unarmed，任务保持 waiting_quota、零预算消耗）。

调用方式：模仿 tests/test_cli_extensions.py 的真实子进程冒烟
（subprocess.run([sys.executable, CLI_PATH, ...], encoding="utf-8")）——
v24-compile / writer-* / v24-record-run 全部用例经子进程调 cli.py；
quota 生命周期四命令沿用同文件 CLI 测试的进程内调用 + monkeypatch
风格（cli.main + redirect_stdout + mock.patch.object 注入假
resolve_quota_detail 观测，零真实网络；同 tests/test_cli_extensions.
QuotaResolveCliTest），adapter RUNS_DIR 注入临时目录。两种调用 cwd /
账本一律钉在 tempfile 临时仓库根（writer 守卫记录、任务 state 与
journal 全落临时目录，绝不触碰本仓库真实账本）。仅 Python 3.7
标准库。

运行：
    cd <repo_root> && python3 -X utf8 -m unittest tests.test_cli_v24 -v
"""

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

CLI_PATH = (Path(__file__).resolve().parents[1] / "plugins" /
            "glm-conductor" / "runtime" / "cli.py")

# quota 生命周期 CLI 用例（进程内调用 + 假 resolver 注入）的模块引导
# 与 runtime import（tests/test_quota_resume_v24.py 同款 sys.path 引导）
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "glm-conductor"))
from runtime import cli, journal, state, task  # noqa: E402
from runtime.quota import resolver as quota_resolver  # noqa: E402
from runtime.workflow import adapter as workflow_adapter  # noqa: E402

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


# —— 5. quota 生命周期四命令（v2.4 audit-fix AF-03） ——

QUOTA_FETCHED_AT = "2026-09-21T12:00:00.000Z"


def fake_detail(status, windows=None, fetched_at=QUOTA_FETCHED_AT,
                source="provider"):
    """构造 resolve_quota_detail 形状的假观测（键冻结四键，零网络）。"""
    snapshot = None if windows is None else {"windows": windows}
    return {"source": source, "status": status,
            "snapshot": snapshot, "fetched_at": fetched_at}


class QuotaLifecycleCliFixture(unittest.TestCase):
    """quota 生命周期 CLI 夹具：进程内调 cli.main + 假 resolver 注入。

    resolver 侧 mock.patch.object(quota_resolver,
    "resolve_quota_detail") 注入 fake 观测（零真实网络）；任务账本在
    tempfile 临时仓库根；adapter RUNS_DIR 注入临时目录（record_
    workflow_run 的 run 关联记录默认相对 CWD 落盘，绝不污染本仓库）。
    """

    TID = "quota-cli-4f2a1b"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = self._tmp.name
        patcher = mock.patch.object(
            workflow_adapter, "RUNS_DIR",
            os.path.join(self._tmp.name, ".glm-conductor", "workflow-runs"))
        patcher.start()
        self.addCleanup(patcher.stop)

    # —— 装置助手 ——

    def run_cli(self, *args):
        """进程内调用 cli.main，返回 (退出码, stdout 原文)。"""
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = cli.main(list(args))
        return code, buffer.getvalue()

    def run_cli_json(self, *args):
        """进程内调用 cli.main 并把 stdout 单行 JSON 解析为 dict。"""
        code, stdout = self.run_cli(*args)
        return code, json.loads(stdout)

    def with_detail(self, detail):
        """注入假 resolve_quota_detail 的上下文管理器。"""
        return mock.patch.object(quota_resolver, "resolve_quota_detail",
                                 return_value=detail)

    def make_task(self, task_id=TID):
        """临时仓库创建缺省 manual 任务（quota_resume 默认 manual/0/0）。"""
        return task.create_task(self.repo, task_id, "额度 CLI 测试目标",
                                {"mode": "solo"})

    def prepare_waiting(self, max_resumes=3, run_id="run-cli-42",
                        automation_id="sched-witness-run-42"):
        """授权 + 关联 run + 进入 waiting_quota（决策面标准前置）。

        v2.4.1 起决策原语的 resume-authorized 路径要求 auto 连续性
        已武装（mode=auto 且 automation_id 为空 → remain-waiting 的
        continuity-unarmed 闸）——本助手缺省绑定见证 id 使 resume-
        authorized 用例走完整前置；automation_id=None 显式保留未武装
        构造（供 unarmed 闸用例）。"""
        self.make_task()
        task.record_workflow_run(self.repo, self.TID, run_id)
        task.authorize_quota_resume(self.repo, self.TID, max_resumes)
        if automation_id is not None:
            task.bind_quota_automation(self.repo, self.TID, automation_id)
        task.enter_waiting_quota(self.repo, self.TID)

    def load(self, task_id=TID):
        return state.load_state(self.repo, task_id)

    def names(self, task_id=TID):
        return [item.get("event")
                for item in journal.read_events(self.repo, task_id)]


class QuotaLifecycleCliTest(QuotaLifecycleCliFixture):
    """AF-03 四命令：规格测试清单九例 + 透传 / 用法 / 拒绝路径锚定。"""

    # 假 snapshot 窗口（five_hour 在前 weekly 在后，与 parser 排序同形）
    WINDOWS = [
        {"kind": "five_hour", "reset_at": "2026-09-21T17:00:00.000Z"},
        {"kind": "weekly", "reset_at": "2026-09-21T05:00:00.000Z"},
    ]

    def test_quota_wait_manual_default_enters_waiting_quota(self):
        """manual 缺省：quota-wait 进入 waiting_quota + enter 事件落账。"""
        self.make_task()
        with self.with_detail(
                fake_detail("EXHAUSTED", windows=self.WINDOWS)) as fake:
            code, payload = self.run_cli_json(
                "quota-wait", self.repo, self.TID)
        self.assertEqual(code, 0)
        fake.assert_called_once_with(self.repo, force_refresh=False)
        # 输出三键：观测 view 为归一化诊断四键（reset_at = 窗口字典序
        # 最大值；observed_at = detail.fetched_at）
        self.assertEqual(payload["task_id"], self.TID)
        self.assertEqual(payload["status"], "waiting_quota")
        self.assertEqual(payload["quota_observation"], {
            "status": "EXHAUSTED", "source": "provider",
            "observed_at": QUOTA_FETCHED_AT,
            "reset_at": "2026-09-21T17:00:00.000Z"})
        # 盘上状态迁移 + 观测三键记入 last_observation（source 不落盘）
        st = self.load()
        self.assertEqual(st["status"], "waiting_quota")
        self.assertEqual(st["quota_resume"]["last_observation"],
                         {"status": "EXHAUSTED",
                          "reset_at": "2026-09-21T17:00:00.000Z",
                          "observed_at": QUOTA_FETCHED_AT})
        # journal 落 waiting_quota enter 事件
        self.assertEqual(self.names(),
                         ["route_selected", "waiting_quota"])
        enter = journal.read_events(self.repo, self.TID)[-1]
        self.assertEqual({k: v for k, v in enter.items() if k != "ts"},
                         {"event": "waiting_quota", "direction": "enter"})

    def test_quota_wait_force_refresh_passthrough(self):
        """--force-refresh 透传 resolver（强制走 provider 语义）。"""
        self.make_task()
        with self.with_detail(fake_detail("EXHAUSTED")) as fake:
            code, _payload = self.run_cli_json(
                "quota-wait", self.repo, self.TID, "--force-refresh")
        self.assertEqual(code, 0)
        fake.assert_called_once_with(self.repo, force_refresh=True)

    def test_quota_wait_without_windows_reset_at_none(self):
        """snapshot 无窗口 → 观测 reset_at=None（纯诊断字段）。"""
        self.make_task()
        with self.with_detail(fake_detail("UNKNOWN", windows=None)):
            code, payload = self.run_cli_json(
                "quota-wait", self.repo, self.TID)
        self.assertEqual(code, 0)
        self.assertIsNone(payload["quota_observation"]["reset_at"])

    def test_authorize_sets_auto_and_budget(self):
        """显式授权：authorize 落 mode=auto + max_resumes（零网络调用）。"""
        self.make_task()
        code, payload = self.run_cli_json(
            "quota-resume-authorize", self.repo, self.TID, "3")
        self.assertEqual(code, 0)
        self.assertEqual(payload["task_id"], self.TID)
        self.assertEqual(payload["quota_resume"],
                         {"mode": "auto", "max_resumes": 3,
                          "resume_count": 0, "automation_id": None})
        st = self.load()
        self.assertEqual(st["quota_resume"]["mode"], "auto")
        self.assertEqual(st["quota_resume"]["max_resumes"], 3)

    def test_decision_exhausted_unknown_remain_waiting(self):
        """EXHAUSTED / UNKNOWN 观测 → decision 返回 remain-waiting。"""
        self.prepare_waiting(max_resumes=3)
        for status in ("EXHAUSTED", "UNKNOWN"):
            with self.with_detail(fake_detail(status)) as fake:
                code, payload = self.run_cli_json(
                    "quota-resume-decision", self.repo, self.TID)
            self.assertEqual(code, 0)
            fake.assert_called_once_with(self.repo, force_refresh=False)
            self.assertEqual(payload["task_id"], self.TID)
            self.assertEqual(payload["decision"]["action"],
                             "remain-waiting")
            self.assertEqual(payload["quota_observation"]["status"], status)
            self.assertEqual(self.load()["status"], "waiting_quota")
            self.assertEqual(
                self.load()["quota_resume"]["resume_count"], 0)

    def test_decision_available_authorized_resume_authorized(self):
        """AVAILABLE + 预算有余 → resume-authorized（含 run id / 暂存计数）。"""
        self.prepare_waiting(max_resumes=3)
        with self.with_detail(fake_detail("AVAILABLE")):
            code, payload = self.run_cli_json(
                "quota-resume-decision", self.repo, self.TID)
        self.assertEqual(code, 0)
        decision = payload["decision"]
        self.assertEqual(decision["action"], "resume-authorized")
        self.assertEqual(decision["workflow_run_id"], "run-cli-42")
        self.assertEqual(decision["resume_count_after"], 1)
        # 暂存不落盘：磁盘计数仍为 0
        self.assertEqual(self.load()["quota_resume"]["resume_count"], 0)

    def test_confirm_consumes_exactly_one_budget(self):
        """confirm 恰消耗一预算：计数 +1、任务回 active、确认事件落账。"""
        self.prepare_waiting(max_resumes=3)
        with self.with_detail(fake_detail("AVAILABLE")):
            self.run_cli("quota-resume-decision", self.repo, self.TID)
        code, payload = self.run_cli_json(
            "quota-resume-confirm", self.repo, self.TID)
        self.assertEqual(code, 0)
        self.assertEqual(payload["task_id"], self.TID)
        self.assertEqual(payload["status"], "active")
        self.assertEqual(payload["phase"], "workflow")
        self.assertEqual(payload["quota_resume"]["resume_count"], 1)
        st = self.load()
        self.assertEqual(st["status"], "active")
        self.assertEqual(st["quota_resume"]["resume_count"], 1)
        self.assertEqual(
            self.names(),
            ["route_selected", "workflow_started", "waiting_quota",
             "quota_resume_confirmed"])

    def test_repeated_decision_before_confirm_consumes_nothing(self):
        """confirm 前重复 decision：两次同暂存值、磁盘计数不变。"""
        self.prepare_waiting(max_resumes=3)
        staged = []
        for _ in range(2):
            with self.with_detail(fake_detail("AVAILABLE")):
                code, payload = self.run_cli_json(
                    "quota-resume-decision", self.repo, self.TID)
            self.assertEqual(code, 0)
            self.assertEqual(payload["decision"]["action"],
                             "resume-authorized")
            staged.append(payload["decision"]["resume_count_after"])
        self.assertEqual(staged, [1, 1])
        self.assertEqual(self.load()["quota_resume"]["resume_count"], 0)
        self.assertEqual(self.load()["status"], "waiting_quota")

    def test_confirm_non_waiting_task_refused_exit_1(self):
        """非 waiting 任务 confirm → 任务层拒绝（退出码 1）。"""
        self.make_task()  # active：无暂存决策可落账
        code, payload = self.run_cli_json(
            "quota-resume-confirm", self.repo, self.TID)
        self.assertEqual(code, 1)
        self.assertIn("error", payload)
        self.assertIn("waiting_quota", payload["error"])
        self.assertEqual(self.load()["status"], "active")
        self.assertEqual(self.load()["quota_resume"]["resume_count"], 0)

    def test_budget_exhausted_decision_moves_waiting_user(self):
        """预算耗尽 → decision 返回 waiting-user 且任务转 waiting_user。"""
        self.prepare_waiting(max_resumes=0)  # 授权零预算：可用即耗尽
        with self.with_detail(fake_detail("AVAILABLE")):
            code, payload = self.run_cli_json(
                "quota-resume-decision", self.repo, self.TID)
        self.assertEqual(code, 0)
        self.assertEqual(payload["decision"]["action"], "waiting-user")
        self.assertIn("预算已耗尽", payload["decision"]["reason"])
        self.assertEqual(self.load()["status"], "waiting_user")
        # 幂等：再次唤醒（任务已 waiting_user）→ no-op，恰一次转态
        with self.with_detail(fake_detail("AVAILABLE")):
            code, payload = self.run_cli_json(
                "quota-resume-decision", self.repo, self.TID)
        self.assertEqual(code, 0)
        self.assertEqual(payload["decision"]["action"], "no-op")
        self.assertEqual(self.load()["status"], "waiting_user")

    def test_invalid_max_resumes_exit_2(self):
        """max_resumes 非法（"abc" / "-1"）→ 参数值非法，退出码 2。"""
        self.make_task()
        for bad in ("abc", "-1"):
            code, payload = self.run_cli_json(
                "quota-resume-authorize", self.repo, self.TID, bad)
            self.assertEqual(code, 2)
            self.assertIn("max_resumes", payload["error"])
        # 授权块零副作用（仍为缺省 manual/0）
        self.assertEqual(self.load()["quota_resume"]["mode"], "manual")
        self.assertEqual(self.load()["quota_resume"]["max_resumes"], 0)

    def test_missing_task_refused_exit_1(self):
        """任务缺失：authorize / decision / confirm → 任务层拒绝（1）。"""
        code, payload = self.run_cli_json(
            "quota-resume-authorize", self.repo, "ghost-task", "2")
        self.assertEqual(code, 1)
        self.assertIn("不存在", payload["error"])
        with self.with_detail(fake_detail("AVAILABLE")):
            code, payload = self.run_cli_json(
                "quota-resume-decision", self.repo, "ghost-task")
        self.assertEqual(code, 1)
        self.assertIn("不存在", payload["error"])
        code, payload = self.run_cli_json(
            "quota-resume-confirm", self.repo, "ghost-task")
        self.assertEqual(code, 1)
        self.assertIn("不存在", payload["error"])

    def test_lifecycle_usage_errors_exit_2(self):
        """参数个数 / 未知旗标 → 用法错退出码 2。"""
        code, _stdout = self.run_cli("quota-wait", self.repo)
        self.assertEqual(code, 2)
        code, payload = self.run_cli_json(
            "quota-wait", self.repo, self.TID, "--bogus")
        self.assertEqual(code, 2)
        self.assertIn("--force-refresh", payload["error"])
        code, _stdout = self.run_cli(
            "quota-resume-authorize", self.repo, self.TID)
        self.assertEqual(code, 2)
        code, _stdout = self.run_cli("quota-resume-decision", self.repo)
        self.assertEqual(code, 2)
        code, _stdout = self.run_cli(
            "quota-resume-decision", self.repo, self.TID, "--bogus")
        self.assertEqual(code, 2)
        code, _stdout = self.run_cli("quota-resume-confirm", self.repo,
                                     self.TID, "extra")
        self.assertEqual(code, 2)


# —— 6. workflow-model-select（v2.4.1 H1，submission 薄壳） ——

FLASH_A = "account:team-a/GLM-5.3-Flash"
FLASH_B = "account:team-b/GLM-5.3-Flash"
TEXT_MODEL_ID = "account:team-a/GLM-5.3"
OTHER_MODEL_ID = "account:team-a/GLM-4-Air"


class WorkflowModelSelectCliTest(CliV24Fixture):
    """workflow-model-select：内联 / 文件两形态、显式与自动选择、拒绝面。

    真实子进程调用（选型链纯内存计算，零状态零网络）——stdout 恰单行
    JSON 提交契约 {subagent_model, model_policy}。"""

    def write_models(self, models, name="models.json"):
        """模型 id 数组写入临时仓库根 JSON 文件（子进程 cwd 即临时目录）。"""
        path = os.path.join(self.repo, name)
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(models, handle, ensure_ascii=False)
        return name

    def test_single_flash_auto_selected_contract_stdout(self):
        """恰一 Flash 候选：自动选定，stdout 恰单行两键提交契约。"""
        name = self.write_models([TEXT_MODEL_ID, FLASH_A, OTHER_MODEL_ID])
        code, stdout, stderr = self.run_cli("workflow-model-select", name)
        self.assertEqual(code, 0, stderr)
        self.assertEqual(stderr, "")
        self.assertEqual(len(stdout.strip().splitlines()), 1)
        self.assertEqual(json.loads(stdout), {
            "subagent_model": FLASH_A,
            "model_policy": "explicit-flash-required"})
        # 成功路径零落盘（选型是纯内存计算，绝不写宿主面）
        self.assertEqual(self.listdir(), [name])

    def test_inline_array_and_file_forms_equivalent(self):
        """内联数组与文件两形态对同一集合输出逐字等价。"""
        name = self.write_models([FLASH_A, TEXT_MODEL_ID])
        code_file, stdout_file, _ = self.run_cli(
            "workflow-model-select", name)
        code_inline, stdout_inline, _ = self.run_cli(
            "workflow-model-select", json.dumps([FLASH_A, TEXT_MODEL_ID]))
        self.assertEqual(code_file, 0)
        self.assertEqual(code_inline, 0)
        self.assertEqual(stdout_file, stdout_inline)

    def test_zero_flash_candidates_exit_2(self):
        """零 Flash 候选（含内联空数组）→ 校验拒绝，退出码 2。"""
        name = self.write_models([TEXT_MODEL_ID, OTHER_MODEL_ID])
        code, stdout, _ = self.run_cli("workflow-model-select", name)
        self.assertEqual(code, 2)
        self.assertIn("error", json.loads(stdout))
        code, stdout, _ = self.run_cli("workflow-model-select", "[]")
        self.assertEqual(code, 2)
        self.assertIn("error", json.loads(stdout))

    def test_multiple_flash_candidates_exit_2_listing_candidates(self):
        """多候选歧义 → 退出码 2 且错误列出全部候选 id（绝不静默取第一个）。"""
        name = self.write_models([FLASH_A, FLASH_B, OTHER_MODEL_ID])
        code, stdout, _ = self.run_cli("workflow-model-select", name)
        self.assertEqual(code, 2)
        text = self.decoded(stdout)
        self.assertIn("歧义", text)
        self.assertIn(FLASH_A, text)
        self.assertIn(FLASH_B, text)

    def test_explicit_model_exact_selection_wins(self):
        """显式 --model <exact-id>：精确选中（显式优先于自动选择）。"""
        name = self.write_models([FLASH_A, FLASH_B])
        code, stdout, _ = self.run_cli(
            "workflow-model-select", name, "--model", FLASH_B)
        self.assertEqual(code, 0)
        payload = json.loads(stdout)
        self.assertEqual(payload["subagent_model"], FLASH_B)
        self.assertEqual(payload["model_policy"], "explicit-flash-required")

    def test_explicit_unconfigured_model_exit_2(self):
        """显式给出未配置 id → 精确成员闸拒绝，退出码 2。"""
        name = self.write_models([FLASH_A])
        code, stdout, _ = self.run_cli(
            "workflow-model-select", name, "--model", FLASH_B)
        self.assertEqual(code, 2)
        self.assertIn(FLASH_B, self.decoded(stdout))

    def test_explicit_text_model_exit_2(self):
        """显式给主会话文本模型：缺陷 A 的误继承源，专门拒绝。"""
        name = self.write_models([FLASH_A, TEXT_MODEL_ID])
        code, stdout, _ = self.run_cli(
            "workflow-model-select", name, "--model", TEXT_MODEL_ID)
        self.assertEqual(code, 2)
        self.assertIn("文本模型", self.decoded(stdout))

    def test_models_input_rejections_exit_2(self):
        """清单不可读 / 坏 JSON / 顶层非数组 / 元素非字符串 → 退出码 2。"""
        # 文件缺失
        code, stdout, _ = self.run_cli(
            "workflow-model-select", "no-such.json")
        self.assertEqual(code, 2)
        self.assertIn("no-such.json", self.decoded(stdout))
        # 坏 JSON 文件
        with open(os.path.join(self.repo, "bad.json"), "w",
                  encoding="utf-8") as handle:
            handle.write("{not json")
        code, _stdout, _ = self.run_cli("workflow-model-select", "bad.json")
        self.assertEqual(code, 2)
        # 顶层非数组（JSON 对象文件）
        with open(os.path.join(self.repo, "obj.json"), "w",
                  encoding="utf-8") as handle:
            json.dump({"models": [FLASH_A]}, handle)
        code, stdout, _ = self.run_cli("workflow-model-select", "obj.json")
        self.assertEqual(code, 2)
        self.assertIn("数组", self.decoded(stdout))
        # 内联可解析但非数组 → 落到文件路径读取 → 不存在 → 2
        code, _stdout, _ = self.run_cli("workflow-model-select", '{"a": 1}')
        self.assertEqual(code, 2)
        # 数组元素非字符串
        name = self.write_models([FLASH_A, 42], name="nums.json")
        code, _stdout, _ = self.run_cli("workflow-model-select", name)
        self.assertEqual(code, 2)

    def test_usage_errors_exit_2(self):
        """零参数 / 未知旗标 / 旗标缺取值 / 参数个数错误 → 退出码 2。"""
        code, _stdout, _ = self.run_cli("workflow-model-select")
        self.assertEqual(code, 2)
        name = self.write_models([FLASH_A])
        code, _stdout, _ = self.run_cli(
            "workflow-model-select", name, "--bogus", "x")
        self.assertEqual(code, 2)
        code, _stdout, _ = self.run_cli(
            "workflow-model-select", name, "--model")
        self.assertEqual(code, 2)
        code, _stdout, _ = self.run_cli(
            "workflow-model-select", name, "--model", FLASH_A, "extra")
        self.assertEqual(code, 2)


# —— 7. auto 连续性武装三命令（v2.4.1 H2，task 薄壳） ——

class QuotaAutomationCliTest(QuotaLifecycleCliFixture):
    """quota-automation-bind / quota-automation-clear /
    quota-continuity-preflight：armed 往返、冲突拒绝、preflight 三态。"""

    AUTO_ID = "sched-witness-cli-1"

    def test_bind_clear_preflight_roundtrip(self):
        """bind → 盘上武装 → 同 id 幂等 → clear 解除 → 幂等清理。

        bind / clear 绝不改 mode 与预算、绝不追加 journal 事件。"""
        self.make_task()
        code, payload = self.run_cli_json(
            "quota-automation-bind", self.repo, self.TID, self.AUTO_ID)
        self.assertEqual(code, 0)
        self.assertEqual(payload["task_id"], self.TID)
        self.assertEqual(payload["quota_resume"]["automation_id"],
                         self.AUTO_ID)
        # 绑定绝不推断 mode=auto，绝不动预算
        self.assertEqual(payload["quota_resume"]["mode"], "manual")
        self.assertEqual(payload["quota_resume"]["max_resumes"], 0)
        self.assertEqual(payload["quota_resume"]["resume_count"], 0)
        # 盘上真实落盘
        self.assertEqual(
            self.load()["quota_resume"]["automation_id"], self.AUTO_ID)
        # 同 id 重绑幂等成功
        code, payload = self.run_cli_json(
            "quota-automation-bind", self.repo, self.TID, self.AUTO_ID)
        self.assertEqual((code, payload["quota_resume"]["automation_id"]),
                         (0, self.AUTO_ID))
        # clear 给定同 id：解除武装
        code, payload = self.run_cli_json(
            "quota-automation-clear", self.repo, self.TID, self.AUTO_ID)
        self.assertEqual(code, 0)
        self.assertIsNone(payload["quota_resume"]["automation_id"])
        self.assertIsNone(self.load()["quota_resume"]["automation_id"])
        # clear 不改 mode / 预算
        self.assertEqual(payload["quota_resume"]["mode"], "manual")
        self.assertEqual(payload["quota_resume"]["max_resumes"], 0)
        # 无给定 id 的 clear 幂等（存量已 None）
        code, payload = self.run_cli_json(
            "quota-automation-clear", self.repo, self.TID)
        self.assertEqual(code, 0)
        self.assertIsNone(payload["quota_resume"]["automation_id"])
        # bind / clear 对 journal 零新增（仍只有建任务的 route_selected）
        self.assertEqual(self.names(), ["route_selected"])

    def test_bind_conflicting_id_exit_1_no_silent_replace(self):
        """异 id 重绑冲突 → 退出码 1，绝不静默替换，盘上零改动。"""
        self.make_task()
        self.run_cli("quota-automation-bind", self.repo, self.TID,
                     self.AUTO_ID)
        code, payload = self.run_cli_json(
            "quota-automation-bind", self.repo, self.TID, "other-sched")
        self.assertEqual(code, 1)
        self.assertIn("error", payload)
        self.assertIn("other-sched", payload["error"])
        self.assertIn(self.AUTO_ID, payload["error"])
        self.assertEqual(
            self.load()["quota_resume"]["automation_id"], self.AUTO_ID)

    def test_clear_wrong_expected_id_exit_1(self):
        """clear 给定 id 与存量不一致 → 精确对账拒绝，退出码 1。"""
        self.make_task()
        self.run_cli("quota-automation-bind", self.repo, self.TID,
                     self.AUTO_ID)
        code, payload = self.run_cli_json(
            "quota-automation-clear", self.repo, self.TID, "mismatch-id")
        self.assertEqual(code, 1)
        self.assertIn("mismatch-id", payload["error"])
        self.assertEqual(
            self.load()["quota_resume"]["automation_id"], self.AUTO_ID)

    def test_bind_empty_automation_id_exit_1(self):
        """空串 automation_id（任务层结构拒绝）→ 退出码 1，盘上零改动。"""
        self.make_task()
        code, payload = self.run_cli_json(
            "quota-automation-bind", self.repo, self.TID, "")
        self.assertEqual(code, 1)
        self.assertIn("automation_id", payload["error"])
        self.assertIsNone(self.load()["quota_resume"]["automation_id"])

    def test_preflight_three_states_exit_0_0_2(self):
        """preflight 三态：manual → 0；auto 未武装 → 2（launch/resume
        阻断）；auto 已武装 → 0。纯读，绝不改状态。"""
        # 态一：manual 未授权 → ready=true，退出码 0
        self.make_task()
        code, payload = self.run_cli_json(
            "quota-continuity-preflight", self.repo, self.TID)
        self.assertEqual(code, 0)
        self.assertEqual(payload["task_id"], self.TID)
        self.assertEqual(payload["preflight"], {
            "ready": True, "mode": "manual", "automation_id": None,
            "reason": "manual mode does not require scheduled activation"})
        # 态二：auto 已授权未武装 → ready=false，退出码 2
        task.authorize_quota_resume(self.repo, self.TID, 3)
        code, payload = self.run_cli_json(
            "quota-continuity-preflight", self.repo, self.TID)
        self.assertEqual(code, 2)
        self.assertEqual(payload["preflight"], {
            "ready": False, "mode": "auto", "automation_id": None,
            "reason": "auto continuity is not armed"})
        # 纯读：预检绝不改状态（仍 active）
        self.assertEqual(self.load()["status"], "active")
        # 态三：bind 武装后 → ready=true，退出码 0
        self.run_cli("quota-automation-bind", self.repo, self.TID,
                     self.AUTO_ID)
        code, payload = self.run_cli_json(
            "quota-continuity-preflight", self.repo, self.TID)
        self.assertEqual(code, 0)
        self.assertEqual(payload["preflight"], {
            "ready": True, "mode": "auto",
            "automation_id": self.AUTO_ID,
            "reason": "auto continuity armed"})
        self.assertEqual(self.load()["status"], "active")

    def test_unarmed_auto_decision_remains_waiting_via_cli(self):
        """auto 已授权未武装：AVAILABLE 观测下 decision 仍
        remain-waiting（continuity-unarmed 闸经 CLI；任务保持
        waiting_quota、零预算消耗）。"""
        self.prepare_waiting(automation_id=None)
        with self.with_detail(fake_detail("AVAILABLE")):
            code, payload = self.run_cli_json(
                "quota-resume-decision", self.repo, self.TID)
        self.assertEqual(code, 0)
        self.assertEqual(payload["decision"]["action"], "remain-waiting")
        self.assertIn("continuity-unarmed", payload["decision"]["reason"])
        self.assertEqual(self.load()["status"], "waiting_quota")
        self.assertEqual(self.load()["quota_resume"]["resume_count"], 0)

    def test_automation_missing_task_exit_1(self):
        """任务缺失：三命令均任务层拒绝（退出码 1，错误含「不存在」）。"""
        for argv in (
            ("quota-automation-bind", self.repo, "ghost-task", "sched-x"),
            ("quota-automation-clear", self.repo, "ghost-task"),
            ("quota-continuity-preflight", self.repo, "ghost-task"),
        ):
            code, payload = self.run_cli_json(*argv)
            self.assertEqual(code, 1)
            self.assertIn("不存在", payload["error"])

    def test_automation_usage_errors_exit_2(self):
        """参数个数错误（不足 / 超出）→ 用法错，退出码 2。"""
        code, _stdout = self.run_cli("quota-automation-bind", self.repo)
        self.assertEqual(code, 2)
        code, _stdout = self.run_cli(
            "quota-automation-bind", self.repo, self.TID, "s", "extra")
        self.assertEqual(code, 2)
        code, _stdout = self.run_cli("quota-automation-clear", self.repo)
        self.assertEqual(code, 2)
        code, _stdout = self.run_cli(
            "quota-automation-clear", self.repo, self.TID, "s", "extra")
        self.assertEqual(code, 2)
        code, _stdout = self.run_cli(
            "quota-continuity-preflight", self.repo)
        self.assertEqual(code, 2)
        code, _stdout = self.run_cli(
            "quota-continuity-preflight", self.repo, self.TID, "extra")
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
