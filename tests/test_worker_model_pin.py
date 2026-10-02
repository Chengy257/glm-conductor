#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""任务级 worker 模型 pin 测试（v2.5 W1：state 新键 + pin API + CLI 面）。

锚定 docs/roadmap/V2_5_TASK_LAUNCH_CONTRACT_AND_PRECISE_WAKE_PLAN.md
§2 I1（写入不查宿主面 / 消费必查宿主面 / 失效即拒）与 §4 W1：

  1. state 新键 worker_model 校验（存在才校验）：缺省不写键合法；
     null / 非空 str 合法；空串 / 非字符串拒绝；save_state 校验闸
     拒绝非法 pin；save/load 往返保真；未知键忽略的向前兼容不变。
  2. task.pin_worker_model：首写 prev=None 且落盘；同值幂等零写盘；
     异值显式覆盖（返回值带 prev 与 new）；终态冻结拒绝；形状预检
     （account: 前缀 / Flash 后缀 / 文本模型后缀专门拒绝）拒绝且盘面
     零改动；缺失任务 / v2.3 遗留任务拒绝；journal 十名冻结零新增
     事件名。
  3. CLI worker-model-pin：成功 / 幂等 / 覆盖（stdout 单行 JSON
     {task_id, prev, new}）；id 形状非法 → 退出码 2；任务缺失 /
     终态冻结 → 退出码 1；用法错 → 退出码 2。
  4. CLI workflow-model-select --task-ref 三来源优先级：--model 显式
     覆盖 pin（任务 pin 不参与也不对账）；pin 采纳（多候选歧义被
     pin 打破）；pin 失效专门拒绝 exit 2（「任务 pin 已失效，请重新
     问答授权」，绝不回退自动选型）；pin 形状非法 exit 2；无 pin
     走既有路径（与不带 --task-ref 输出逐字一致）；任务缺失 → 1；
     用法错 → 2。

调用方式：CLI 面模仿 tests/test_cli_v24.py 的真实子进程冒烟
（subprocess.run，cwd 钉在 tempfile 临时仓库根——--task-ref 的任务
账本按当前工作目录解析，v24-record-run 同款约定）；runtime 面进程
内直接调用。仅 Python 3.7 标准库。

运行：
    cd <repo_root> && python3 -X utf8 -m unittest tests.test_worker_model_pin -v
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

CLI_PATH = (Path(__file__).resolve().parents[1] / "plugins" /
            "glm-conductor" / "runtime" / "cli.py")

# runtime import（tests/test_cli_v24.py 同款 sys.path 引导）
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "glm-conductor"))
from runtime import journal, state, task  # noqa: E402

# 测试用模型 id（假账户段，绝不指向任何真实账户；形态约定同
# tests/test_cli_v24.py 的 FLASH_A / FLASH_B / TEXT_MODEL_ID）
FLASH_A = "account:team-a/GLM-5.3-Flash"
FLASH_B = "account:team-b/GLM-5.3-Flash"
TEXT_MODEL_ID = "account:team-a/GLM-5.3"
OTHER_MODEL_ID = "account:team-a/GLM-4-Air"

TID = "pin-ut-7c4e2a"


def full_state(**overrides):
    """构造合法 v2.4 state（solo 缺省形态，供纯校验用例改键）。"""
    st = state.new_task_state(
        TID, "任务级 worker 模型 pin 测试", {"mode": "solo"},
        repository_root="C:/glm-conductor-under-test")
    for key, value in overrides.items():
        st[key] = value
    return st


# —— 1. state 新键 worker_model 校验（存在才校验） ——

class WorkerModelStateKeyTest(unittest.TestCase):
    """worker_model 可选键：缺省 / null / 非空 str 合法，其余拒绝。"""

    def test_absent_key_valid_and_not_written_by_default(self):
        """缺省（键不写）合法：new_task_state 不写 worker_model 键。"""
        st = full_state()
        self.assertNotIn("worker_model", st)
        self.assertEqual(state.validate_state(st), [])

    def test_null_and_nonempty_str_valid(self):
        """null（无 pin 占位）与非空 str（pin id）均合法。"""
        for value in (None, FLASH_A):
            st = full_state(worker_model=value)
            self.assertEqual(state.validate_state(st), [],
                             "worker_model=%r 应合法" % (value,))

    def test_invalid_shapes_rejected(self):
        """空串 / 非字符串形状拒绝（存在才校验的拒绝面）。"""
        for value in ("", 42, ["account:team-a/GLM-5.3-Flash"], {}, True):
            st = full_state(worker_model=value)
            errors = state.validate_state(st)
            self.assertEqual(
                errors,
                ["worker_model 必须是非空字符串或 null"],
                "worker_model=%r 应被拒绝" % (value,))

    def test_save_state_gate_rejects_invalid_pin(self):
        """save_state 校验闸拒绝非法 pin（ValueError，零落盘）。"""
        st = full_state(worker_model="")
        with self.assertRaises(ValueError) as ctx:
            state.save_state("C:/glm-conductor-under-test", st)
        self.assertIn("worker_model 必须是非空字符串或 null",
                      str(ctx.exception))

    def test_save_load_roundtrip_preserves_pin(self):
        """save/load 往返保真：pin 原值往返（durable_io 原子写）。"""
        st = full_state(worker_model=FLASH_A)
        with tempfile.TemporaryDirectory() as repo:
            state.save_state(repo, st)
            loaded = state.load_state(repo, TID)
        self.assertEqual(loaded.get("worker_model"), FLASH_A)

    def test_unknown_top_keys_still_ignored(self):
        """未知顶层键忽略的向前兼容不变（v2.4 语义零回归）。"""
        st = full_state(some_future_key={"v2.6": True})
        self.assertEqual(state.validate_state(st), [])


# —— 2. task.pin_worker_model（写入 API） ——

class PinWorkerModelApiTest(unittest.TestCase):
    """pin API：首写 / 幂等 / 覆盖 / 终态拒 / 形状拒 / 缺失与遗留拒。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = self._tmp.name

    # —— 装置助手 ——

    def make_task(self, task_id=TID):
        return task.create_task(self.repo, task_id, "pin API 测试目标",
                                {"mode": "solo"})

    def make_terminal_task(self, task_id=TID):
        """创建并完成任务（终态冻结用例前置；release 幂等零副作用）。"""
        self.make_task(task_id)
        return task.complete(self.repo, task_id)

    def write_raw_legacy(self, task_id="legacy-pin-task"):
        """绕过 save_state 直写 v2.3 遗留 state（遗留拒绝用例前置）。"""
        path = state.state_path(self.repo, task_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "task_id": task_id,
            "goal": "v2.3 遗留任务",
            "status": "executing",
            "work_units": [{"id": "u1", "status": "executing"}],
        }
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        return task_id

    def raw_state_bytes(self, task_id=TID):
        with open(state.state_path(self.repo, task_id), "rb") as fh:
            return fh.read()

    # —— 用例 ——

    def test_first_pin_prev_none_and_persisted(self):
        """首写：prev=None、new=id，盘上 worker_model 落盘且全量合法。"""
        self.make_task()
        outcome = task.pin_worker_model(self.repo, TID, FLASH_A)
        self.assertEqual(
            outcome, {"task_id": TID, "prev": None, "new": FLASH_A})
        loaded = state.load_state(self.repo, TID)
        self.assertEqual(loaded.get("worker_model"), FLASH_A)
        self.assertEqual(state.validate_state(loaded), [])

    def test_same_value_idempotent_zero_disk_write(self):
        """同值幂等：零写盘（save_state 未被调用）、原样返回现状。"""
        self.make_task()
        task.pin_worker_model(self.repo, TID, FLASH_A)
        before = self.raw_state_bytes()
        with mock.patch.object(state, "save_state") as fake_save:
            outcome = task.pin_worker_model(self.repo, TID, FLASH_A)
        fake_save.assert_not_called()
        self.assertEqual(
            outcome, {"task_id": TID, "prev": FLASH_A, "new": FLASH_A})
        self.assertEqual(self.raw_state_bytes(), before)

    def test_different_value_explicit_override(self):
        """异值显式覆盖：返回值带 prev 与 new，盘上更新为新 pin。"""
        self.make_task()
        task.pin_worker_model(self.repo, TID, FLASH_A)
        outcome = task.pin_worker_model(self.repo, TID, FLASH_B)
        self.assertEqual(
            outcome, {"task_id": TID, "prev": FLASH_A, "new": FLASH_B})
        loaded = state.load_state(self.repo, TID)
        self.assertEqual(loaded.get("worker_model"), FLASH_B)
        self.assertEqual(state.validate_state(loaded), [])

    def test_null_or_missing_pin_treated_as_first_write(self):
        """盘上 worker_model=null（或键缺失）按「无有效 pin」解释。"""
        self.make_task()
        st = state.load_state(self.repo, TID)
        st["worker_model"] = None
        state.save_state(self.repo, st)
        outcome = task.pin_worker_model(self.repo, TID, FLASH_A)
        self.assertEqual(outcome["prev"], None)
        self.assertEqual(outcome["new"], FLASH_A)

    def test_terminal_task_rejected(self):
        """终态冻结：completed 任务拒绝写入 pin（终态不得新增事实）。"""
        self.make_terminal_task()
        with self.assertRaises(ValueError) as ctx:
            task.pin_worker_model(self.repo, TID, FLASH_A)
        self.assertIn("终态", str(ctx.exception))
        self.assertNotIn("worker_model",
                         state.load_state(self.repo, TID))

    def test_shape_rejections_no_disk_change(self):
        """形状预检拒绝且盘面零改动（写入不查宿主面，只收形状）。

        覆盖 submission 形态约定全部拒绝面：裸别名 / 缺 Flash 后缀 /
        文本模型后缀专门拒绝 / 缺账户段或缺模型段 / 含空白 / 空串 /
        非字符串。"""
        self.make_task()
        task.pin_worker_model(self.repo, TID, FLASH_A)
        before = self.raw_state_bytes()
        bad_ids = (
            "GLM-5.3-Flash",                    # 裸别名（无 account: 前缀）
            "account:team-a/GLM-4-Air",         # 缺 Flash 后缀
            TEXT_MODEL_ID,                      # 文本模型后缀专门拒绝
            "account:/GLM-5.3-Flash",           # 账户段为空
            "account:team-a/",                  # 模型段为空
            "account:team-a",                   # 无 '/' 分隔
            "account:team-a /GLM-5.3-Flash",    # 含空白
            "account:team-a/GLM-5.3-Flash\n",   # 尾随空白
            "", None, 42,
        )
        for bad in bad_ids:
            with self.assertRaises(ValueError) as ctx:
                task.pin_worker_model(self.repo, TID, bad)
            self.assertIn("pin_worker_model", str(ctx.exception),
                          "model_id=%r 的拒绝消息应带入口名" % (bad,))
            self.assertEqual(self.raw_state_bytes(), before,
                             "model_id=%r 的拒绝应盘面零改动" % (bad,))
        # 文本模型后缀的专门拒绝原因（缺陷 A 语义）单独确认
        with self.assertRaises(ValueError) as ctx:
            task.pin_worker_model(self.repo, TID, TEXT_MODEL_ID)
        self.assertIn("文本模型", str(ctx.exception))
        self.assertEqual(state.load_state(self.repo, TID)["worker_model"],
                         FLASH_A)

    def test_missing_task_rejected(self):
        """任务缺失（无 state.json）→ ValueError 拒绝。"""
        with self.assertRaises(ValueError) as ctx:
            task.pin_worker_model(self.repo, "no-such-task", FLASH_A)
        self.assertIn("不存在", str(ctx.exception))

    def test_legacy_task_rejected(self):
        """v2.3 遗留任务拒绝（绝不静默当作 v2.4 任务写入）。"""
        legacy_id = self.write_raw_legacy()
        with self.assertRaises(ValueError) as ctx:
            task.pin_worker_model(self.repo, legacy_id, FLASH_A)
        self.assertIn(state.LEGACY_STATE_GUIDANCE, str(ctx.exception))

    def test_pin_never_adds_journal_events(self):
        """journal 十名冻结：pin 全程零新增事件名（state 落盘承载）。"""
        self.make_task()
        task.pin_worker_model(self.repo, TID, FLASH_A)
        task.pin_worker_model(self.repo, TID, FLASH_B)  # 覆盖路径同零事件
        names = [item.get("event")
                 for item in journal.read_events(self.repo, TID)]
        self.assertEqual(names, ["route_selected"])
        for name in names:
            self.assertIn(name, task.TASK_JOURNAL_EVENTS)


# —— 3/4. CLI 面（真实子进程，cwd 钉临时仓库根） ——

class WorkerModelPinCliFixture(unittest.TestCase):
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
        return task.create_task(self.repo, task_id, "pin CLI 测试目标",
                                {"mode": "solo"})

    def load(self, task_id=TID):
        return state.load_state(self.repo, task_id)

    def set_raw_pin(self, value, task_id=TID):
        """绕过 save_state 校验直写盘上 worker_model（构造盘面漂移）。"""
        path = state.state_path(self.repo, task_id)
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        if value is None:
            raw.pop("worker_model", None)
        else:
            raw["worker_model"] = value
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(raw, fh, ensure_ascii=False, indent=2)

    def write_models(self, models, name="models.json"):
        """模型 id 数组写入临时仓库根 JSON 文件（子进程 cwd 即临时目录）。"""
        path = os.path.join(self.repo, name)
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(models, handle, ensure_ascii=False)
        return name


class WorkerModelPinCliTest(WorkerModelPinCliFixture):
    """worker-model-pin：成功 / 幂等 / 覆盖 / 形状 2 / 任务层 1 / 用法 2。"""

    def test_pin_write_idempotent_and_override(self):
        """首写 / 同值幂等 / 异值覆盖：stdout 恰单行 {task_id, prev, new}。"""
        self.make_task()
        code, payload, stderr = self.run_cli_json(
            "worker-model-pin", self.repo, TID, FLASH_A)
        self.assertEqual((code, stderr), (0, ""))
        self.assertEqual(
            payload, {"task_id": TID, "prev": None, "new": FLASH_A})
        self.assertEqual(self.load()["worker_model"], FLASH_A)
        # 同值幂等成功
        code, payload, _ = self.run_cli_json(
            "worker-model-pin", self.repo, TID, FLASH_A)
        self.assertEqual(code, 0)
        self.assertEqual(payload["prev"], FLASH_A)
        # 异值显式覆盖：prev 报旧值
        code, payload, _ = self.run_cli_json(
            "worker-model-pin", self.repo, TID, FLASH_B)
        self.assertEqual(code, 0)
        self.assertEqual(
            payload, {"task_id": TID, "prev": FLASH_A, "new": FLASH_B})
        self.assertEqual(self.load()["worker_model"], FLASH_B)

    def test_shape_invalid_exit_2(self):
        """id 形状非法 → 参数值非法口径，退出码 2（盘面零改动）。"""
        self.make_task()
        for bad in (TEXT_MODEL_ID, "GLM-5.3-Flash", ""):
            code, stdout, _ = self.run_cli(
                "worker-model-pin", self.repo, TID, bad)
            self.assertEqual(code, 2, "model_id=%r 应退出码 2" % (bad,))
            self.assertIn("error", json.loads(stdout))
        self.assertNotIn("worker_model", self.load())

    def test_missing_task_exit_1(self):
        """任务缺失 → 运行期异常口径，退出码 1。"""
        code, stdout, _ = self.run_cli(
            "worker-model-pin", self.repo, "no-such-task", FLASH_A)
        self.assertEqual(code, 1)
        self.assertIn("不存在", json.loads(stdout)["error"])

    def test_terminal_task_exit_1(self):
        """终态冻结 → 任务层拒绝口径，退出码 1（盘面零改动）。"""
        self.make_task()
        task.complete(self.repo, TID)
        code, stdout, _ = self.run_cli(
            "worker-model-pin", self.repo, TID, FLASH_A)
        self.assertEqual(code, 1)
        self.assertIn("终态", json.loads(stdout)["error"])
        self.assertNotIn("worker_model", self.load())

    def test_usage_errors_exit_2(self):
        """参数个数错误 → 用法口径，退出码 2。"""
        code, _stdout, _ = self.run_cli("worker-model-pin", self.repo, TID)
        self.assertEqual(code, 2)
        code, _stdout, _ = self.run_cli(
            "worker-model-pin", self.repo, TID, FLASH_A, "extra")
        self.assertEqual(code, 2)
        code, _stdout, _ = self.run_cli("worker-model-pin")
        self.assertEqual(code, 2)


class WorkflowModelSelectTaskRefCliTest(WorkerModelPinCliFixture):
    """--task-ref 三来源优先级：--model > 任务 pin > 自动选型。"""

    def test_model_flag_overrides_task_pin(self):
        """--model 显式值 > 任务 pin：显式覆盖时 pin 不参与。"""
        self.make_task()
        task.pin_worker_model(self.repo, TID, FLASH_A)
        name = self.write_models([FLASH_A, FLASH_B])
        code, stdout, _ = self.run_cli(
            "workflow-model-select", name, "--task-ref", TID,
            "--model", FLASH_B)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout), {
            "subagent_model": FLASH_B,
            "model_policy": "explicit-flash-required"})

    def test_model_flag_wins_even_when_task_missing(self):
        """--model 为完全显式覆盖：任务缺失也不阻断（pin 不对账）。"""
        name = self.write_models([FLASH_A])
        code, stdout, _ = self.run_cli(
            "workflow-model-select", name, "--model", FLASH_A,
            "--task-ref", "no-such-task")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout)["subagent_model"], FLASH_A)

    def test_pin_adopted_breaks_ambiguity(self):
        """任务 pin > 自动选型：多候选歧义被 pin 打破（pin 恰被采纳）。"""
        self.make_task()
        task.pin_worker_model(self.repo, TID, FLASH_B)
        name = self.write_models([FLASH_A, FLASH_B])
        # 对照：无 pin 时同 configured 是多候选歧义 → 2
        code, stdout, _ = self.run_cli("workflow-model-select", name)
        self.assertEqual(code, 2)
        # 带 --task-ref：pin 被采纳，恰输出 pin 的提交契约
        code, stdout, _ = self.run_cli(
            "workflow-model-select", name, "--task-ref", TID)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout), {
            "subagent_model": FLASH_B,
            "model_policy": "explicit-flash-required"})

    def test_stale_pin_dedicated_rejection_exit_2(self):
        """pin 不在 configured 集合 → 专门拒绝 exit 2，绝不回退自动选型。"""
        self.make_task()
        task.pin_worker_model(self.repo, TID, FLASH_A)
        name = self.write_models([FLASH_B])  # FLASH_B 恰一候选本可自动选定
        code, stdout, _ = self.run_cli(
            "workflow-model-select", name, "--task-ref", TID)
        self.assertEqual(code, 2)
        text = json.loads(stdout)["error"]
        self.assertIn("任务 pin 已失效", text)
        self.assertIn("请重新问答授权", text)
        self.assertIn(FLASH_A, text)
        # 对照：无 pin 时同 configured 走既有自动选型 → 0（证明上面
        # 的拒绝是 pin 失效闸而非选型面回归）
        clean_id = "pin-ut-clean-3d91b"
        self.make_task(clean_id)
        code, stdout, _ = self.run_cli(
            "workflow-model-select", name, "--task-ref", clean_id)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout)["subagent_model"], FLASH_B)

    def test_invalid_shape_pin_exit_2(self):
        """盘面漂移出形状非法 pin → 专门拒绝 exit 2（形状 + 失效语义）。"""
        self.make_task()
        self.set_raw_pin("GLM-5.3-Flash")  # 裸别名：绕过写入口的盘面漂移
        name = self.write_models([FLASH_A, FLASH_B])
        code, stdout, _ = self.run_cli(
            "workflow-model-select", name, "--task-ref", TID)
        self.assertEqual(code, 2)
        text = json.loads(stdout)["error"]
        self.assertIn("形状非法", text)
        self.assertIn("任务 pin 已失效", text)
        self.assertIn("请重新问答授权", text)

    def test_no_pin_follows_existing_path_verbatim(self):
        """无 pin：--task-ref 与不带 --task-ref 输出逐字一致（既有路径
        回归锚）——恰一候选自动选定，多候选歧义照旧 exit 2。"""
        self.make_task()
        single = self.write_models([FLASH_A, TEXT_MODEL_ID])
        code_with, stdout_with, _ = self.run_cli(
            "workflow-model-select", single, "--task-ref", TID)
        code_without, stdout_without, _ = self.run_cli(
            "workflow-model-select", single)
        self.assertEqual((code_with, code_without), (0, 0))
        self.assertEqual(stdout_with, stdout_without)
        self.assertEqual(json.loads(stdout_with)["subagent_model"], FLASH_A)
        # 多候选歧义照旧（--task-ref 不改变自动选型拒绝面）
        ambiguous = self.write_models([FLASH_A, FLASH_B], name="multi.json")
        code, stdout, _ = self.run_cli(
            "workflow-model-select", ambiguous, "--task-ref", TID)
        self.assertEqual(code, 2)
        self.assertIn("歧义", json.loads(stdout)["error"])

    def test_null_pin_treated_as_no_pin(self):
        """worker_model=null（无 pin 占位）→ 走既有自动选型路径。"""
        self.make_task()
        self.set_raw_pin(None)  # 显式落盘 null 占位
        name = self.write_models([FLASH_A])
        code, stdout, _ = self.run_cli(
            "workflow-model-select", name, "--task-ref", TID)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout)["subagent_model"], FLASH_A)

    def test_missing_task_exit_1(self):
        """--task-ref 指向不存在任务 → 运行期异常口径，退出码 1。"""
        name = self.write_models([FLASH_A])
        code, stdout, _ = self.run_cli(
            "workflow-model-select", name, "--task-ref", "no-such-task")
        self.assertEqual(code, 1)
        self.assertIn("不存在", json.loads(stdout)["error"])

    def test_usage_errors_exit_2(self):
        """旗标缺取值 / 未知旗标 / 参数个数错误 → 用法口径，退出码 2。"""
        name = self.write_models([FLASH_A])
        code, _stdout, _ = self.run_cli(
            "workflow-model-select", name, "--task-ref")
        self.assertEqual(code, 2)
        code, _stdout, _ = self.run_cli(
            "workflow-model-select", name, "--bogus", "x")
        self.assertEqual(code, 2)
        code, _stdout, _ = self.run_cli(
            "workflow-model-select", name, "--task-ref", TID,
            "--model", FLASH_A, "extra")
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
