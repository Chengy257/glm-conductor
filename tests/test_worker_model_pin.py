#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""任务级 worker 模型 pin 测试（v2.5 W1：state 新键 + pin API + CLI 面
；v2.6.0 WP-M1 用户授权显式选型重基线同步）。

锚定 docs/roadmap/V2_5_TASK_LAUNCH_CONTRACT_AND_PRECISE_WAKE_PLAN.md
§2 I1（写入不查宿主面 / 消费必查宿主面 / 失效即拒）与 §4 W1，叠加
docs/roadmap/POST_V2_5_2_RELIABILITY_AND_MODEL_SELECTION_IMPLEMENTATION_
SPEC.md §5.3（v2.6.0：角色中性形状校验、有 pin 任务普通 --model 不得
绕过）：

  1. state 新键 worker_model 校验（存在才校验）：缺省不写键合法；
     null / 非空 str 合法；空串 / 非字符串拒绝；save_state 校验闸
     拒绝非法 pin；save/load 往返保真；未知键忽略的向前兼容不变。
  2. task.pin_worker_model：首写 prev=None 且落盘；同值幂等零写盘；
     异值显式覆盖（返回值带 prev 与 new）；终态冻结拒绝；形状预检
     （角色中性：provider 全限定两段 <provider>/<model>——account:
     命名空间与非 account provider（2026-10-19 owner 裁定）同合法
     / 无空白——非 Flash 已配置精确 id 合法，不含 '/' 的裸别名 / 空
     值 / 缺段仍拒）拒绝且盘面零改动；
     缺失任务 / v2.3 遗留任务拒绝；journal 十名冻结零新增事件名。
  3. CLI worker-model-pin：成功 / 幂等 / 覆盖（stdout 单行 JSON
     {task_id, prev, new}）；id 形状非法 → 退出码 2；任务缺失 /
     终态冻结 → 退出码 1；用法错 → 退出码 2。
  4. CLI workflow-model-select --task-ref（v2.6.0 规格 §5.3 第 4 条）：
     任务有 pin 时 --model 显式值必须与 pin 一致，不一致即拒 exit 2
     （「不得绕过任务 pin」文案提示交互问答重新授权 / worker-model-pin
     更新）；一致（或无 --model）时 pin 采纳（多候选歧义被 pin 打破）；
     pin 失效专门拒绝 exit 2（「任务 pin 已失效，请重新问答授权」，
     绝不回退自动选型）；pin 形状非法 exit 2；无 pin 时 --model 任意
     configured 精确 id 合法（不限 Flash 后缀）；无 pin 且无 --model 且带
     --task-ref → exit 2 转问答（严口径，恰一候选也拒；裸调用才走自动选型）；任务缺失 → 1（含带 --model 形态——pin 消费
     面一致性对账必须读到任务，fail closed）；用法错 → 2。

调用方式：CLI 面模仿 tests/test_cli_v24.py 的真实子进程冒烟
（subprocess.run，cwd 钉在 tempfile 临时仓库根——--task-ref 的任务
账本按当前工作目录解析，v24-record-run 同款约定）；runtime 面进程
内直接调用。仅 Python 3.7 标准库。

运行：
    cd <repo_root> && python3 -X utf8 -m unittest tests.test_worker_model_pin -v
"""

import json
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
from runtime import journal, messages, state, task  # noqa: E402

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
                [messages.STATE_WORKER_MODEL_INVALID],
                "worker_model=%r 应被拒绝" % (value,))

    def test_save_state_gate_rejects_invalid_pin(self):
        """save_state 校验闸拒绝非法 pin（ValueError，零落盘）。"""
        st = full_state(worker_model="")
        with self.assertRaises(ValueError) as ctx:
            state.save_state("C:/glm-conductor-under-test", st)
        self.assertIn(messages.STATE_WORKER_MODEL_INVALID,
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
        path.write_bytes(
            json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"))
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
        self.assertIn(messages.TASK_TERMINAL_FROZEN
                      % ("pin_worker_model", TID, "completed"),
                      str(ctx.exception))
        self.assertNotIn("worker_model",
                         state.load_state(self.repo, TID))

    def test_shape_rejections_no_disk_change(self):
        """形状预检拒绝且盘面零改动（写入不查宿主面，只收形状）。

        v2.6.0 WP-M1 角色中性 + 2026-10-19 放宽：拒绝面收窄为纯形状
        ——裸别名（无 '/'）/ 缺 provider 段或缺模型段 / 含空白 / 空串
        / 非字符串；非 Flash 与非 account provider 的已配置精确
        id 合法（另测）。"""
        self.make_task()
        task.pin_worker_model(self.repo, TID, FLASH_A)
        before = self.raw_state_bytes()
        bad_ids = (
            "GLM-5.3-Flash",                    # 裸别名（无 '/' provider 段）
            "account:team-a/",                  # 模型段为空
            "account:team-a",                   # 无 '/' 分隔
            "opencode-x",                       # 非 account 形态同样无 '/'
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
        self.assertEqual(state.load_state(self.repo, TID)["worker_model"],
                         FLASH_A)

    def test_non_flash_pin_accepted_and_persisted(self):
        """v2.6.0 WP-M1 角色中性：非 Flash 已配置精确 id 合法 pin
        （未来陌生名字的已配置 id 同形态合法；宿主对账留给消费点）。"""
        self.make_task()
        outcome = task.pin_worker_model(self.repo, TID, TEXT_MODEL_ID)
        self.assertEqual(
            outcome, {"task_id": TID, "prev": None, "new": TEXT_MODEL_ID})
        loaded = state.load_state(self.repo, TID)
        self.assertEqual(loaded.get("worker_model"), TEXT_MODEL_ID)
        self.assertEqual(state.validate_state(loaded), [])
        # 非Flash → Flash 显式覆盖（换授权的规范落盘动作）同样合法
        outcome = task.pin_worker_model(self.repo, TID, FLASH_B)
        self.assertEqual(
            outcome, {"task_id": TID, "prev": TEXT_MODEL_ID, "new": FLASH_B})

    def test_non_account_provider_pin_accepted(self):
        """2026-10-19 owner 裁定解除 account: 前缀唯一准入：宿主桥接
        provider 的 providerId/modelId 复合 id 同样合法 pin（此前被
        误判为裸别名拒绝；宿主对账留给消费点）。"""
        bridge = "opencode-go-responses/deepseek-v4.1-flash"
        self.make_task()
        outcome = task.pin_worker_model(self.repo, TID, bridge)
        self.assertEqual(
            outcome, {"task_id": TID, "prev": None, "new": bridge})
        loaded = state.load_state(self.repo, TID)
        self.assertEqual(loaded.get("worker_model"), bridge)
        self.assertEqual(state.validate_state(loaded), [])

    def test_missing_task_rejected(self):
        """任务缺失（无 state.json）→ ValueError 拒绝。"""
        with self.assertRaises(ValueError) as ctx:
            task.pin_worker_model(self.repo, "no-such-task", FLASH_A)
        self.assertIn(messages.TASK_MISSING % "no-such-task",
                      str(ctx.exception))

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
        path.write_bytes(json.dumps(raw, ensure_ascii=False, indent=2)
                         .encode("utf-8"))

    def write_models(self, models, name="models.json"):
        """模型 id 数组写入临时仓库根 JSON 文件（子进程 cwd 即临时目录）。"""
        path = Path(self.repo) / name
        path.write_bytes(json.dumps(models, ensure_ascii=False).encode("utf-8"))
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
        """id 形状非法 → 参数值非法口径，退出码 2（盘面零改动）。

        v2.6.0 WP-M1：非 Flash 形状合法 id 不再在此拒绝面（角色中性
        形状校验）；裸别名 / 空串仍拒。"""
        self.make_task()
        for bad in ("GLM-5.3-Flash", ""):
            code, stdout, _ = self.run_cli(
                "worker-model-pin", self.repo, TID, bad)
            self.assertEqual(code, 2, "model_id=%r 应退出码 2" % (bad,))
            self.assertIn("error", json.loads(stdout))
        self.assertNotIn("worker_model", self.load())

    def test_non_flash_pin_cli_accepted(self):
        """非 Flash 已配置精确 id 经 CLI 合法 pin（角色中性形状校验）。"""
        self.make_task()
        code, payload, stderr = self.run_cli_json(
            "worker-model-pin", self.repo, TID, TEXT_MODEL_ID)
        self.assertEqual((code, stderr), (0, ""))
        self.assertEqual(
            payload, {"task_id": TID, "prev": None, "new": TEXT_MODEL_ID})
        self.assertEqual(self.load()["worker_model"], TEXT_MODEL_ID)

    def test_missing_task_exit_1(self):
        """任务缺失 → 运行期异常口径，退出码 1。"""
        code, stdout, _ = self.run_cli(
            "worker-model-pin", self.repo, "no-such-task", FLASH_A)
        self.assertEqual(code, 1)
        self.assertIn(messages.CLI_TASK_MISSING_WORKER_MODEL_PIN
                      % ("no-such-task", self.repo),
                      json.loads(stdout)["error"])

    def test_terminal_task_exit_1(self):
        """终态冻结 → 任务层拒绝口径，退出码 1（盘面零改动）。"""
        self.make_task()
        task.complete(self.repo, TID)
        code, stdout, _ = self.run_cli(
            "worker-model-pin", self.repo, TID, FLASH_A)
        self.assertEqual(code, 1)
        self.assertIn(messages.TASK_TERMINAL_FROZEN
                      % ("pin_worker_model", TID, "completed"),
                      json.loads(stdout)["error"])
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
    """--task-ref pin 消费面（v2.6.0 规格 §5.3 第 4 条：--model 不得
    绕过任务 pin——显式值必须与 pin 一致）。"""

    def test_model_mismatch_with_pin_exit_2(self):
        """任务有 pin 时 --model 显式值必须与 pin 一致：不一致即拒
        exit 2（普通 --model 不再能绕过 pin），文案提示交互问答重新
        授权 / worker-model-pin 更新。"""
        self.make_task()
        task.pin_worker_model(self.repo, TID, FLASH_A)
        name = self.write_models([FLASH_A, FLASH_B])
        code, stdout, _ = self.run_cli(
            "workflow-model-select", name, "--task-ref", TID,
            "--model", FLASH_B)
        self.assertEqual(code, 2)
        text = json.loads(stdout)["error"]
        self.assertIn(messages.CLI_WORKFLOW_MODEL_SELECT_MODEL_PIN_MISMATCH
                      % (FLASH_B, TID, FLASH_A), text)
        self.assertIn("must not bypass the task pin", text)
        self.assertIn("re-authorize through the interactive interview",
                      text)
        self.assertIn("worker-model-pin", text)
        self.assertIn(FLASH_A, text)
        self.assertIn(FLASH_B, text)
        # 对照：pin 缺失的任务同 --model 显式值合法（无 pin 不受此闸）
        clean_id = "pin-ut-clean-4e02a"
        self.make_task(clean_id)
        code, stdout, _ = self.run_cli(
            "workflow-model-select", name, "--task-ref", clean_id,
            "--model", FLASH_B)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout)["subagent_model"], FLASH_B)

    def test_model_equal_to_pin_exit_0(self):
        """--model 显式值与 pin 一致 → 放行（显式确认既有授权）。"""
        self.make_task()
        task.pin_worker_model(self.repo, TID, FLASH_B)
        name = self.write_models([FLASH_A, FLASH_B])
        code, stdout, _ = self.run_cli(
            "workflow-model-select", name, "--task-ref", TID,
            "--model", FLASH_B)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout), {
            "subagent_model": FLASH_B,
            "model_policy": "user-authorized-explicit-selection"})

    def test_task_missing_with_model_exit_1(self):
        """--task-ref 指向缺失任务时即使带 --model 也 fail closed：
        pin 消费面一致性对账必须读到任务 → 退出码 1。"""
        name = self.write_models([FLASH_A])
        code, stdout, _ = self.run_cli(
            "workflow-model-select", name, "--model", FLASH_A,
            "--task-ref", "no-such-task")
        self.assertEqual(code, 1)
        self.assertIn(messages.CLI_WORKFLOW_MODEL_SELECT_TASK_MISSING
                      % ("no-such-task", self.repo),
                      json.loads(stdout)["error"])

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
            "model_policy": "user-authorized-explicit-selection"})

    def test_non_flash_pin_consumed_from_configured(self):
        """v2.6.0 WP-M1：非 Flash pin 形状合法且在 configured 集合 →
        消费放行（角色中性消费面；不受档位判据约束）。"""
        self.make_task()
        task.pin_worker_model(self.repo, TID, TEXT_MODEL_ID)
        name = self.write_models([FLASH_A, TEXT_MODEL_ID])
        code, stdout, _ = self.run_cli(
            "workflow-model-select", name, "--task-ref", TID)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout)["subagent_model"],
                         TEXT_MODEL_ID)

    def test_stale_pin_dedicated_rejection_exit_2(self):
        """pin 不在 configured 集合 → 专门拒绝 exit 2，绝不回退自动选型。"""
        self.make_task()
        task.pin_worker_model(self.repo, TID, FLASH_A)
        name = self.write_models([FLASH_B])  # FLASH_B 恰一候选本可自动选定
        code, stdout, _ = self.run_cli(
            "workflow-model-select", name, "--task-ref", TID)
        self.assertEqual(code, 2)
        text = json.loads(stdout)["error"]
        self.assertIn(messages.CLI_WORKFLOW_MODEL_SELECT_PIN_NOT_CONFIGURED
                      % (TID, FLASH_A, 1), text)
        self.assertIn("never fall back to automatic selection", text)
        self.assertIn(FLASH_A, text)
        # 对照：无 pin 时**裸调用**同 configured 走既有自动选型 → 0
        # （证明上面的拒绝是 pin 失效闸而非选型面回归；--task-ref 无 pin
        #  的严口径拒绝由 no_pin 测试类锚定，规格 §5.3 resume 不隐式授权）
        clean_id = "pin-ut-clean-3d91b"
        self.make_task(clean_id)
        code, stdout, _ = self.run_cli(
            "workflow-model-select", name)
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
        self.assertIn("has an invalid shape", text)
        self.assertIn("the task pin is void", text)
        self.assertIn("re-run the launch interview for authorization",
                      text)

    def test_drifted_pin_shapes_rejected_not_treated_as_no_pin(self):
        """盘面漂移（外部直写非 str / 空串）→ 专门失效拒绝 exit 2，
        绝不静默当作「无 pin」（与 str 形状非法同口径 fail-closed）。"""
        self.make_task()
        name = self.write_models([FLASH_A, FLASH_B])
        for drift in ("", 123):
            self.set_raw_pin(drift)
            code, stdout, _ = self.run_cli(
                "workflow-model-select", name, "--task-ref", TID)
            self.assertEqual(code, 2, "漂移 %r 应拒" % (drift,))
            text = json.loads(stdout)["error"]
            self.assertIn("has an invalid shape", text)
            self.assertIn("ledger drift", text)
            self.assertIn("the task pin is void", text)

    def test_no_pin_follows_existing_path_verbatim(self):
        """无 pin 分径契约（规格 §5.3 resume 严口径）：--task-ref 无 pin
        且无 --model → exit 2 转问答（绝不隐式授权单候选）；裸调用照旧
        恰一候选自动选定；--model 显式值照旧放行。"""
        self.make_task()
        single = self.write_models([FLASH_A, TEXT_MODEL_ID])
        # --task-ref 无 pin 无 --model：拒绝转问答（新严口径）
        code_with, stdout_with, _ = self.run_cli(
            "workflow-model-select", single, "--task-ref", TID)
        self.assertEqual(code_with, 2)
        self.assertIn(
            messages.CLI_WORKFLOW_MODEL_SELECT_NO_PIN_NO_MODEL % TID,
            json.loads(stdout_with)["error"])
        self.assertIn("launch interview",
                      json.loads(stdout_with)["error"])
        # 裸调用（无任务上下文）：恰一 Flash 软推荐照旧自动选定
        code_without, stdout_without, _ = self.run_cli(
            "workflow-model-select", single)
        self.assertEqual(code_without, 0)
        self.assertEqual(json.loads(stdout_without)["subagent_model"],
                         FLASH_A)
        # --model 显式值：无 pin 任务照旧任意 configured id 放行
        code, stdout, _ = self.run_cli(
            "workflow-model-select", single, "--task-ref", TID,
            "--model", TEXT_MODEL_ID)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout)["subagent_model"],
                         TEXT_MODEL_ID)
        # 多候选歧义照旧（裸调用拒绝面不变）
        ambiguous = self.write_models([FLASH_A, FLASH_B], name="multi.json")
        code, stdout, _ = self.run_cli(
            "workflow-model-select", ambiguous)
        self.assertEqual(code, 2)
        self.assertIn("ambiguous", json.loads(stdout)["error"])

    def test_null_pin_treated_as_no_pin(self):
        """worker_model=null（无 pin 占位）→ 视同无 pin：--task-ref 严
        口径拒绝转问答；裸调用对照照旧自动选型。"""
        self.make_task()
        self.set_raw_pin(None)  # 显式落盘 null 占位
        name = self.write_models([FLASH_A])
        code, stdout, _ = self.run_cli(
            "workflow-model-select", name, "--task-ref", TID)
        self.assertEqual(code, 2)
        self.assertIn(
            messages.CLI_WORKFLOW_MODEL_SELECT_NO_PIN_NO_MODEL % TID,
            json.loads(stdout)["error"])
        code, stdout, _ = self.run_cli("workflow-model-select", name)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout)["subagent_model"], FLASH_A)

    def test_missing_task_exit_1(self):
        """--task-ref 指向不存在任务 → 运行期异常口径，退出码 1。"""
        name = self.write_models([FLASH_A])
        code, stdout, _ = self.run_cli(
            "workflow-model-select", name, "--task-ref", "no-such-task")
        self.assertEqual(code, 1)
        self.assertIn(messages.CLI_WORKFLOW_MODEL_SELECT_TASK_MISSING
                      % ("no-such-task", self.repo),
                      json.loads(stdout)["error"])

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
