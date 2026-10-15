#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Phase D WP-M1 用户授权模型选型重基线——定向测试（规格 §5.5 全套）。

锚定 docs/roadmap/POST_V2_5_2_RELIABILITY_AND_MODEL_SELECTION_
IMPLEMENTATION_SPEC.md §5.2/§5.3/§5.5 与
docs/roadmap/V2_6_0_IMPLEMENTATION_PLAN.md §4（WP-M1 测试面），
按九条验收口径组织（owner ask ①–⑨ 逐条对应）：

  ① 混合宿主清单（旧 Flash + 新代际 Flash + 非 Flash 文本模型 +
     跨 provider 非 Flash）→ 软推荐不硬拒：恰多 Flash 候选时歧义拒绝
     且消息可见全部 Flash 档候选（绝不静默取第一个）；owner 选非
     Flash（GLM-5.3 / 跨 provider）被接受并出
     user-authorized-explicit-selection 提交契约；选非 Flash 后 pin
     写入成功且 --task-ref 消费放行。
  ② 未配置 id / 裸别名（缺 provider 前缀）/ 空串 / 纯空白 → 一律拒绝
     （CLI --model 与 worker-model-pin 两侧；API 侧非 Flash 未配置 id
     同拒——拒绝面只认形状 + configured 精确成员，绝不认档位）。
  ③ 未来陌生名（GLM-9-Turbo，已配置精确 id）→ 接受：API 形状校验、
     显式选型、pin 写入、消费放行全链合法（角色中性，绝不按名字/后缀
     拒绝——no-go 红线）。
  ④ 任务有 pin 时 --model 与 pin 一致 → 放行；--model ≠ pin → 拒
     exit 2（普通 --model 不得绕过任务 pin；换授权必经交互重授权）。
  ⑤ pin 跨唤醒/恢复存活（complete → state.reopen_task 重开 → 新
     子进程 --task-ref 消费仍命中）；pin 不在当次 configured 集合 =
     失效即拒 exit 2（fail-closed，绝不回退自动选型）。
  ⑥ 无 pin 时 --model 任意 configured 精确 id 合法（含非 Flash /
     跨 provider / 未来陌生名；带与不带 --task-ref 两形态）。
  ⑦ workflow-model-select 成功输出 model_policy=
     "user-authorized-explicit-selection"（恰两键提交契约）。
  ⑧ 全仓无残留硬依赖：插件树（runtime/skills/agents/hooks/commands）
     零 "explicit-flash-required" 残留；runtime 与 orchestration/
     continuity 技能面零「必须 GLM-5.3」类硬门措辞；静态 agent 模型
     绑定恰限于视觉双角色（frontmatter provider 全限定，owner 裁决
     维持），glm-reviewer 无绑定。清单展示面的文案锚（max 推理档提示
     / 单一候选也确认）归 scripts/validate_plugin.py 锚组，不在本套
     重复。
  ⑨ resume 遇 legacy 任务无 pin 不隐式授权（runtime 层）：v2.3 遗留
     任务 --task-ref 消费 fail closed（exit 1，绝不静默当作「无 pin」
     走自动选型）；重开/生命周期动作绝不写 pin（无 pin 任务重开后仍
     无 pin，不隐式授权）；runtime 全树 worker_model 写入点恰唯一
     （task.pin_worker_model——无人值守唤醒轮绝不自动重 pin 的结构性
     事实）；选型消费全程零写盘。

运行：
    cd <repo_root> && PYTHONUTF8=1 python3 -m unittest \\
        tests.test_model_selection_rebaseline -v

全量：
    cd <repo_root> && PYTHONUTF8=1 python3 -m unittest discover -s tests -t .

CLI 面模仿 tests/test_cli_v24.py / tests/test_worker_model_pin.py 的
真实子进程冒烟（subprocess.run，cwd 钉 tempfile 临时仓库根——
--task-ref 的任务账本按当前工作目录解析，v24-record-run 同款约定）；
runtime 面进程内直接调用。仅 Python 3.7 标准库。
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PLUGIN_ROOT = (Path(__file__).resolve().parents[1] / "plugins" /
               "glm-conductor")
CLI_PATH = PLUGIN_ROOT / "runtime" / "cli.py"

# runtime import（tests/test_cli_v24.py 同款 sys.path 引导）
sys.path.insert(0, str(PLUGIN_ROOT))
from runtime import messages, state, task  # noqa: E402
from runtime.workflow import submission  # noqa: E402

# —— 测试夹具：混合宿主清单（中性账户段，绝不指向真实账户） ——

FLASH_OLD = "account:team-a/GLM-5.3-Flash"        # 旧 Flash 档
FLASH_NEW = "account:team-a/GLM-6-Flash"          # 新代际 Flash 档
TEXT_MAIN = "account:team-a/GLM-5.3"              # 非 Flash 文本模型
CROSS_PROVIDER = "account:other-cloud/deepseek-v4.1"  # 跨 provider 非 Flash
FUTURE = "account:team-a/GLM-9-Turbo"             # 未来陌生名（非 Flash）
MIXED = [FLASH_OLD, FLASH_NEW, TEXT_MAIN, CROSS_PROVIDER, FUTURE]

TID = "m1-ut-9d41c2"

# ⑧/⑨ 静态扫描用的锚与禁词（v2.6.1 W2：orchestration/continuity
# 英文化后补英文等价硬门措辞；中文原锚保留给仍中文的 enforcement 面）
RETIRED_POLICY_TOKEN = "explicit-flash-required"
HARD_GATE_PHRASES = (
    "必须 GLM-5.3",
    "必须为 GLM-5.3",
    "主会话必须是 GLM",
    "主会话必须为 GLM",
    "main session must be GLM-5.3",
    "main-session model must be GLM-5.3",
)
WORKER_MODEL_WRITE_PATTERN = '["worker_model"] ='
VISUAL_AGENTS = ("visual-implementer.md", "visual-reviewer.md")


def iter_plugin_texts():
    """遍历插件树文本文件（.py/.md/.json，跳过 __pycache__）。"""
    for path in sorted(PLUGIN_ROOT.rglob("*")):
        if not path.is_file() or path.suffix not in (".py", ".md", ".json"):
            continue
        if "__pycache__" in path.parts:
            continue
        yield path


class M1CliFixture(unittest.TestCase):
    """公共夹具：临时目录即仓库根，子进程调用 cwd 钉在临时目录。"""

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

    def make_task(self, task_id=TID):
        return task.create_task(self.repo, task_id, "M1 定向测试目标",
                                {"mode": "solo"})

    def write_models(self, models, name="models.json"):
        """模型 id 数组写入临时仓库根 JSON 文件（子进程 cwd 即临时目录）。"""
        path = Path(self.repo) / name
        path.write_bytes(json.dumps(models, ensure_ascii=False).encode("utf-8"))
        return name

    def raw_state_bytes(self, task_id=TID):
        with open(state.state_path(self.repo, task_id), "rb") as fh:
            return fh.read()


# —— ① 混合宿主清单：软推荐不硬拒 + owner 选非 Flash 全链 ——

class MixedHostListTest(M1CliFixture):
    """① 旧/新 Flash + 非 Flash 全在清单：候选可见、软推荐、owner 选
    非 Flash 被接受并 pin 成功（⑦ 契约锚同行覆盖）。"""

    def test_soft_candidates_visible_ambiguity_never_silent_pick(self):
        """无显式值：多 Flash 候选歧义拒绝 exit 2，消息可见全部 Flash
        档候选（旧 + 新代际）与「软推荐」措辞——绝不静默取第一个。"""
        name = self.write_models(MIXED)
        code, stdout, _ = self.run_cli("workflow-model-select", name)
        self.assertEqual(code, 2)
        text = json.loads(stdout)["error"]
        self.assertIn("歧义", text)
        self.assertIn("软推荐", text)
        self.assertIn(FLASH_OLD, text)
        self.assertIn(FLASH_NEW, text)

    def test_owner_selects_non_flash_accepted_with_contract(self):
        """owner 选非 Flash（GLM-5.3 文本模型）→ 放行并出⑦提交契约。"""
        name = self.write_models(MIXED)
        code, stdout, stderr = self.run_cli(
            "workflow-model-select", name, "--model", TEXT_MAIN)
        self.assertEqual((code, stderr), (0, ""))
        self.assertEqual(len(stdout.strip().splitlines()), 1)
        self.assertEqual(json.loads(stdout), {
            "subagent_model": TEXT_MAIN,
            "model_policy": "user-authorized-explicit-selection"})

    def test_owner_selects_cross_provider_accepted(self):
        """owner 选跨 provider 非 Flash（deepseek 系精确 id）→ 放行。"""
        name = self.write_models(MIXED)
        code, stdout, _ = self.run_cli(
            "workflow-model-select", name, "--model", CROSS_PROVIDER)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout)["subagent_model"],
                         CROSS_PROVIDER)

    def test_owner_non_flash_pin_succeeds_and_consumed(self):
        """owner 选非 Flash 后 pin 写入成功；--task-ref 消费放行同一 id
        （授权一次、任务内默认的全链）。"""
        self.make_task()
        code, payload, stderr = self.run_cli_json(
            "worker-model-pin", self.repo, TID, TEXT_MAIN)
        self.assertEqual((code, stderr), (0, ""))
        self.assertEqual(
            payload, {"task_id": TID, "prev": None, "new": TEXT_MAIN})
        name = self.write_models(MIXED)
        code, stdout, _ = self.run_cli(
            "workflow-model-select", name, "--task-ref", TID)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout), {
            "subagent_model": TEXT_MAIN,
            "model_policy": "user-authorized-explicit-selection"})

    def test_api_soft_ranking_not_admission_gate(self):
        """API 锚：档位只是软推荐候选分类，绝不参与准入——非 Flash
        已配置 id 经 validate / 显式 select 全部放行；零 Flash 候选
        集合 + 显式非 Flash 同样放行。"""
        self.assertEqual(submission.validate_worker_model(TEXT_MAIN),
                         TEXT_MAIN)
        self.assertEqual(
            submission.select_worker_model(MIXED,
                                           explicit_model_id=TEXT_MAIN),
            TEXT_MAIN)
        self.assertEqual(
            submission.select_worker_model(
                [TEXT_MAIN, CROSS_PROVIDER, FUTURE],
                explicit_model_id=FUTURE),
            FUTURE)


# —— ② 未配置 / 裸别名 / 空串 / 纯空白 → 拒绝 ——

class RejectionMatrixTest(M1CliFixture):
    """② 拒绝面只认形状 + configured 精确成员，绝不认档位。"""

    def test_select_model_rejections_exit_2(self):
        """--model 给未配置 id / 裸别名（缺 provider 前缀，Flash 与
        非 Flash 同算）/ 空串 / 纯空白 → 全部 exit 2。"""
        name = self.write_models(MIXED)
        bad_models = (
            "account:team-a/GLM-99-Imaginary",  # 形状合法但未配置
            "GLM-5.3-Flash",                    # 裸别名（Flash 档）
            "GLM-5.3",                          # 裸别名（非 Flash 档）
            "",                                 # 空串
            "   ",                              # 纯空白
        )
        for bad in bad_models:
            code, stdout, _ = self.run_cli(
                "workflow-model-select", name, "--model", bad)
            self.assertEqual(code, 2, "--model=%r 应拒绝 exit 2" % (bad,))
            self.assertIn("error", json.loads(stdout))

    def test_pin_shape_rejections_exit_2_no_disk_change(self):
        """worker-model-pin 给裸别名 / 空串 / 纯空白 → exit 2 且盘面
        零改动（写入只收形状，绝不查宿主面）。"""
        self.make_task()
        before = self.raw_state_bytes()
        for bad in ("GLM-5.3-Flash", "GLM-5.3", "", "   "):
            code, stdout, _ = self.run_cli(
                "worker-model-pin", self.repo, TID, bad)
            self.assertEqual(code, 2, "pin=%r 应拒绝 exit 2" % (bad,))
            self.assertIn("error", json.loads(stdout))
        self.assertEqual(self.raw_state_bytes(), before)

    def test_api_unconfigured_non_flash_rejected(self):
        """API 锚：非 Flash 未配置 id 同样被精确成员闸拒绝——拒绝面
        与档位无关（ configured 对账认精确字符串相等）。"""
        with self.assertRaises(submission.WorkflowSubmissionError) as ctx:
            submission.validate_worker_model(
                FUTURE, [FLASH_OLD, TEXT_MAIN])
        self.assertIn(FUTURE, str(ctx.exception))
        with self.assertRaises(submission.WorkflowSubmissionError):
            submission.select_worker_model(
                [FLASH_OLD, TEXT_MAIN], explicit_model_id=FUTURE)


# —— ③ 未来陌生名（已配置精确 id）→ 接受 ——

class FutureUnknownIdTest(M1CliFixture):
    """③ GLM-9-Turbo：陌生名字但已配置的精确 id 全链合法（绝不按
    名字/后缀拒绝——no-go 红线）。"""

    def test_future_id_end_to_end_accepted(self):
        """API 形状校验 → 显式选型 → pin 写入 → --task-ref 消费。"""
        self.assertEqual(submission.validate_worker_model(FUTURE), FUTURE)
        self.make_task()
        code, payload, stderr = self.run_cli_json(
            "worker-model-pin", self.repo, TID, FUTURE)
        self.assertEqual((code, stderr), (0, ""))
        self.assertEqual(payload["new"], FUTURE)
        name = self.write_models(MIXED)
        code, stdout, _ = self.run_cli(
            "workflow-model-select", name, "--task-ref", TID)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout)["subagent_model"], FUTURE)
        code, stdout, _ = self.run_cli(
            "workflow-model-select", name, "--model", FUTURE)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout)["subagent_model"], FUTURE)


# —— ④/⑤ pin 完整性：--model 不绕过 + 跨唤醒/恢复存活 + 失效即拒 ——

class PinIntegrityTest(M1CliFixture):
    """④ --model 与 pin 一致放行 / 不一致拒 exit 2；⑤ pin 跨重开与
    进程边界存活、失效 fail-closed。"""

    def test_model_equal_to_pin_passes(self):
        """④ --model 显式值与 pin 一致 → 放行（显式确认既有授权）。"""
        self.make_task()
        self.assertEqual(
            task.pin_worker_model(self.repo, TID, FLASH_NEW)["new"],
            FLASH_NEW)
        name = self.write_models(MIXED)
        code, stdout, _ = self.run_cli(
            "workflow-model-select", name, "--task-ref", TID,
            "--model", FLASH_NEW)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout)["subagent_model"], FLASH_NEW)

    def test_model_differs_from_pin_exit_2_no_bypass(self):
        """④ --model ≠ pin → exit 2（不得绕过任务 pin；换授权必经
        交互重授权 + worker-model-pin）。"""
        self.make_task()
        task.pin_worker_model(self.repo, TID, FLASH_OLD)
        name = self.write_models(MIXED)
        code, stdout, _ = self.run_cli(
            "workflow-model-select", name, "--task-ref", TID,
            "--model", TEXT_MAIN)
        self.assertEqual(code, 2)
        text = json.loads(stdout)["error"]
        self.assertIn(messages.CLI_WORKFLOW_MODEL_SELECT_MODEL_PIN_MISMATCH
                      % (TEXT_MAIN, TID, FLASH_OLD), text)
        self.assertIn("must not bypass the task pin", text)
        self.assertIn("re-authorize through the interactive interview",
                      text)
        self.assertIn(FLASH_OLD, text)
        self.assertIn(TEXT_MAIN, text)

    def test_pin_survives_reopen_and_process_boundary(self):
        """⑤ pin 跨唤醒/恢复存活：pin → 新子进程消费命中 → complete
        → state.reopen_task 显式重开 → pin 原值仍在 → 再一个全新子
        进程 --task-ref 消费仍命中同一 id。"""
        self.make_task()
        task.pin_worker_model(self.repo, TID, FUTURE)
        name = self.write_models(MIXED)
        code, stdout, _ = self.run_cli(
            "workflow-model-select", name, "--task-ref", TID)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout)["subagent_model"], FUTURE)
        task.complete(self.repo, TID)
        state.reopen_task(self.repo, TID, reason="M1 恢复存活测试")
        self.assertEqual(
            state.load_state(self.repo, TID).get("worker_model"), FUTURE)
        code, stdout, _ = self.run_cli(
            "workflow-model-select", name, "--task-ref", TID)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout)["subagent_model"], FUTURE)

    def test_stale_pin_fail_closed_never_auto_select(self):
        """⑤ pin 不在当次 configured 集合 = 失效即拒 exit 2：清单上
        恰一 Flash 候选本可自动选定，也绝不回退（fail-closed）。"""
        self.make_task()
        task.pin_worker_model(self.repo, TID, FLASH_OLD)
        name = self.write_models([FLASH_NEW])  # 恰一候选本可自动选定
        code, stdout, _ = self.run_cli(
            "workflow-model-select", name, "--task-ref", TID)
        self.assertEqual(code, 2)
        text = json.loads(stdout)["error"]
        self.assertIn(messages.CLI_WORKFLOW_MODEL_SELECT_PIN_NOT_CONFIGURED
                      % (TID, FLASH_OLD, 1), text)
        self.assertIn("never fall back to automatic selection", text)
        self.assertIn(FLASH_OLD, text)


# —— ⑥/⑨ 无 pin：显式路径任意 id 合法；不隐式授权 ——

class NoPinExplicitAuthorizationTest(M1CliFixture):
    """⑥ 无 pin 时 --model 任意 configured 精确 id 合法；⑨ 无显式值
    且无 pin 时绝不静默指派（歧义交回问答，runtime 不隐式授权）。"""

    def test_no_pin_task_ref_any_configured_id_legal(self):
        """无 pin 任务：--model 遍历混合清单全部精确 id → 逐一放行。"""
        self.make_task()
        name = self.write_models(MIXED)
        for mid in MIXED:
            code, stdout, _ = self.run_cli(
                "workflow-model-select", name, "--task-ref", TID,
                "--model", mid)
            self.assertEqual(code, 0, "--model=%r 应放行" % (mid,))
            self.assertEqual(json.loads(stdout)["subagent_model"], mid)

    def test_no_pin_without_task_ref_any_configured_id_legal(self):
        """不带 --task-ref 的完全显式路径同此（行为与 v2.4.1 一致）。"""
        name = self.write_models(MIXED)
        for mid in MIXED:
            code, stdout, _ = self.run_cli(
                "workflow-model-select", name, "--model", mid)
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(stdout)["subagent_model"], mid)

    def test_no_pin_no_model_never_silently_assigns(self):
        """无 pin 且无显式值（--task-ref）：严口径拒绝转启动问答——
        恰一 Flash 候选也绝不隐式授权（规格 §5.2 单一候选也须确认 /
        §5.3 resume 不隐式授权）；裸调用对照照旧软推荐自动选定。"""
        self.make_task()
        # 多候选清单：拒绝且指向启动问答（不再是歧义消息）
        name = self.write_models(MIXED)
        code, stdout, _ = self.run_cli(
            "workflow-model-select", name, "--task-ref", TID)
        self.assertEqual(code, 2)
        error = json.loads(stdout)["error"]
        self.assertIn(messages.CLI_WORKFLOW_MODEL_SELECT_NO_PIN_NO_MODEL
                      % TID, error)
        self.assertIn("launch interview", error)
        # 恰一 Flash 候选：同样拒绝（关键差异点——旧便捷路径已收窄）
        single = self.write_models([FLASH_OLD, TEXT_MAIN],
                                   name="single.json")
        code, stdout, _ = self.run_cli(
            "workflow-model-select", single, "--task-ref", TID)
        self.assertEqual(code, 2)
        self.assertIn(
            messages.CLI_WORKFLOW_MODEL_SELECT_NO_PIN_NO_MODEL % TID,
            json.loads(stdout)["error"])
        # 裸调用对照（无任务上下文）：恰一 Flash 软推荐照旧自动选定
        code, stdout, _ = self.run_cli(
            "workflow-model-select", single)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout)["subagent_model"], FLASH_OLD)


class ResumeNeverAutoAuthorizesTest(M1CliFixture):
    """⑨ resume 遇 legacy 任务无 pin 不隐式授权（runtime 层）：
    v2.3 遗留 fail closed、重开绝不写 pin、worker_model 写入点全树
    恰唯一、选型消费零写盘。"""

    def write_raw_legacy(self, task_id="m1-legacy-task"):
        """绕过 save_state 直写 v2.3 遗留 state（work_units 即遗留
        授权/凭证集合标识）。"""
        path = state.state_path(self.repo, task_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "task_id": task_id,
            "goal": "v2.3 遗留任务",
            "status": "executing",
            "work_units": [{"id": "u1", "status": "executing"}],
        }
        path.write_bytes(
            json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"))
        return task_id

    def test_legacy_task_select_fail_closed_exit_1(self):
        """v2.3 遗留任务 --task-ref → exit 1：绝不静默当作「无 pin」
        回退自动选型（无 pin ≠ 遗留，两者消费面语义不同）。"""
        legacy_id = self.write_raw_legacy()
        name = self.write_models(MIXED)
        code, stdout, _ = self.run_cli(
            "workflow-model-select", name, "--task-ref", legacy_id)
        self.assertEqual(code, 1)
        text = json.loads(stdout)["error"]
        self.assertIn(
            messages.CLI_WORKFLOW_MODEL_SELECT_TASK_LEGACY.split(": %s")[0]
            % legacy_id, text)
        self.assertIn(state.LEGACY_STATE_GUIDANCE, text)
        self.assertNotIn("subagent_model", json.loads(stdout))

    def test_reopen_never_grants_pin(self):
        """无 pin 任务经 complete → reopen_task 重开：worker_model 仍
        缺省——恢复动作绝不隐式写入授权（需交互重授权）。"""
        self.make_task()
        task.complete(self.repo, TID)
        state.reopen_task(self.repo, TID, reason="M1 无隐式授权测试")
        loaded = state.load_state(self.repo, TID)
        self.assertNotIn("worker_model", loaded)

    def test_select_consumption_is_read_only(self):
        """pin 消费（--task-ref 选型）全程零写盘：select 前后任务
        state.json 字节逐字全等（消费不产生状态事实）。"""
        self.make_task()
        task.pin_worker_model(self.repo, TID, FLASH_OLD)
        before = self.raw_state_bytes()
        name = self.write_models(MIXED)
        code, _stdout, _ = self.run_cli(
            "workflow-model-select", name, "--task-ref", TID)
        self.assertEqual(code, 0)
        self.assertEqual(self.raw_state_bytes(), before)

    def test_runtime_has_exactly_one_pin_write_site(self):
        """结构性事实：插件树 Python 源码中 worker_model 的赋值写入点
        恰唯一（task.pin_worker_model）——无人值守唤醒轮绝不自动重
        pin 不可能被其他 runtime 路径绕过（⑨ 静态锚）。"""
        write_sites = []
        for path in sorted(PLUGIN_ROOT.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            with open(path, "r", encoding="utf-8") as fh:
                for lineno, line in enumerate(fh, start=1):
                    if WORKER_MODEL_WRITE_PATTERN in line:
                        write_sites.append((path, lineno, line.strip()))
        self.assertEqual(len(write_sites), 1,
                         "worker_model 写入点应恰唯一，实际：%r"
                         % (write_sites,))
        path, _lineno, line = write_sites[0]
        self.assertEqual(path, PLUGIN_ROOT / "runtime" / "task.py")
        self.assertTrue(line.startswith("current["),
                        "唯一写入点应位于 pin_worker_model 的锁内复核写：%r"
                        % (line,))


# —— ⑦/⑧ 政策值锚 + 全仓无残留硬依赖 ——

class PolicyAndNoHardGateScanTest(unittest.TestCase):
    """⑦ model_policy 值锚；⑧ 插件树零 explicit-flash-required 残留、
    runtime/技能面零「必须 GLM-5.3」类硬门措辞、静态 agent 绑定恰限
    视觉双角色（owner 裁决维持的例外）。"""

    def test_contract_policy_value_anchored(self):
        """提交契约恒为 user-authorized-explicit-selection（恰两键）；
        Flash 档常量仍是版本无关软推荐分类器（-Flash）。"""
        contract = submission.build_submission_contract(FLASH_OLD)
        self.assertEqual(list(contract),
                         ["subagent_model", "model_policy"])
        self.assertEqual(contract["model_policy"],
                         "user-authorized-explicit-selection")
        self.assertEqual(submission.FLASH_TIER_SUFFIX, "-Flash")

    def test_no_retired_policy_token_in_plugin_tree(self):
        """explicit-flash-required 在插件树（py/md/json）零残留
        （v2.6.0 WP-M1 换值后，旧政策值不得在任何活跃面出现）。"""
        hits = []
        for path in iter_plugin_texts():
            with open(path, "r", encoding="utf-8") as fh:
                if RETIRED_POLICY_TOKEN in fh.read():
                    hits.append(str(path))
        self.assertEqual(
            hits, [],
            "已退役政策值 %r 不应出现在插件树，实际命中：%r"
            % (RETIRED_POLICY_TOKEN, hits))

    def test_no_hard_gate_phrasing_in_active_policy_surfaces(self):
        """runtime 与技能面（orchestration/continuity/enforcement）零
        「必须 GLM-5.3」类主模型硬门措辞（主模型继承宿主、不可编程
        切换——Conductor 绝不检查主会话模型名）。"""
        surfaces = [PLUGIN_ROOT / "runtime"]
        for name in ("orchestration", "continuity", "enforcement"):
            skill_dir = PLUGIN_ROOT / "skills" / name
            if skill_dir.is_dir():
                surfaces.append(skill_dir)
        hits = []
        for surface in surfaces:
            paths = sorted(surface.rglob("*.py")) + sorted(surface.rglob("*.md"))
            for path in paths:
                with open(path, "r", encoding="utf-8") as fh:
                    text = fh.read()
                for phrase in HARD_GATE_PHRASES:
                    if phrase in text:
                        hits.append((str(path), phrase))
        self.assertEqual(
            hits, [],
            "活跃政策面出现主模型硬门措辞：%r" % (hits,))

    def test_static_agent_bindings_scoped_to_visual_roles(self):
        """静态 agent 模型绑定恰限于视觉双角色（frontmatter provider
        全限定 account: 前缀——裸 ID 静默回退坑）；glm-reviewer 无
        绑定（继承宿主/会话模型）。owner 裁决维持的例外恰此两个。"""
        agents_dir = PLUGIN_ROOT / "agents"
        bound = {}
        for path in sorted(agents_dir.glob("*.md")):
            with open(path, "r", encoding="utf-8") as fh:
                for line in fh:
                    stripped = line.strip()
                    if stripped.startswith("model:"):
                        bound[path.name] = stripped
                        break
        self.assertEqual(sorted(bound), sorted(VISUAL_AGENTS),
                         "静态模型绑定应恰限于视觉双角色，实际：%r"
                         % (sorted(bound),))
        for name in VISUAL_AGENTS:
            line = bound[name]
            value = line.partition(":")[2].strip().strip('"')
            self.assertTrue(
                value.startswith("account:"),
                "%s 的 frontmatter 绑定必须 provider 全限定（裸 ID 会"
                "静默回退宿主默认）：%r" % (name, value))


if __name__ == "__main__":
    unittest.main()
