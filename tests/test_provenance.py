#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""runtime.provenance 验证溯源测试（v2.1 M5/M6，wu-21-12，§16.3/§16.4）。

十个规格用例 + 错误路径补充（scratch 任务目录全部真实 git init——RB-1
证据门与证据指纹都依赖 git）：
  1. verify_unit happy path：required=["git status --porcelain"]（allow
     类）→ exit 0 → receipt 文件 + journal verification_receipt +
     record_unit_verification 证据事件；随后 fresh_unit_verification
     ok=True 且 fingerprint 与 receipt 一致；finish_unit 可通过（端到端：
     verify_unit → finish_unit(completed) 成功）；
  2. command 白名单：显式传不在 required 的命令 → ProvenanceError，
     零副作用（无 receipt、无事件、state.json 字节不变）；
  3. policy 闸：required 里塞 "rm -rf /tmp/x"（deny 类）→
     ProvenanceError 拒绝执行，无 receipt（command=None 与显式传入
     两条路径都闸）；
  4. ask 类命令（"git push"）同样拒绝；
  5. exit != 0：receipt 记 exit_code=3、all_passed=False、无 verification
     证据事件；
  6. 超时：timeout_seconds=1 → receipt timeout=true、exit_code None、
     无证据事件；
  7. command=None 多命令：两条 required（一过一败）→ 两条 receipt、
     all_passed=False、仅过的一条有证据事件；
  8. verify_task：任务级 required → receipt scope="task" +
     state.record_verification 落账（verification.completed 含该命令，
     fingerprint 与完成门同一入口复算一致）；
  9. stdout/stderr excerpt 截断（4096 字符）与 utf-8（命令输出含中文；
     durable 文件 ensure_ascii=False 明文可读）；
  10. receipts 目录原子写：执行后无 .tmp 残留；
  补充：任务缺失 / 单元缺失 → ProvenanceError；verify_task 白名单闸。

git fixture 做法（git init + config + commit、Windows 下 .git 只读位
清理）对齐 tests/test_reconcile.py / tests/test_task_manager.py 的
GitRepoFixture；环境无 git 可执行时整类自动 skipTest。被检仓库目录由
tempfile.TemporaryDirectory 提供，绝不读写真实工作区与真实账本。

运行：
    cd <repo_root> && python3 -m unittest tests.test_provenance -v
"""

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "glm-conductor"))
from runtime import fingerprint as fingerprint_mod
from runtime import journal as journal_mod
from runtime import provenance
from runtime import reconcile
from runtime import state
from runtime import task_manager
from runtime import work_unit

TID = "prov-task-1a2b3c"
ROUTE = {"mode": "delegate", "delegability": "high", "assurance": "standard",
         "executor": "flash-implementer", "continuity": "foreground"}

# allow 类命令（classify_bash 无规则命中 → None）：scratch git 仓库内恒 exit 0
VERIFY_CMD = "git status --porcelain"
# exit 3 的失败命令（cmd.exe shell=True 下 python3 已实测可解析）
FAIL_CMD = 'python3 -c "import sys;sys.exit(3)"'
# 超时用命令（配合 timeout_seconds=1）
SLEEP_CMD = 'python3 -c "import time;time.sleep(5)"'


def run_git(repo, *args):
    """在 fixture 仓库里执行 git 子命令（测试装置专用，失败即断言错误）。"""
    proc = subprocess.run(
        ["git"] + list(args), cwd=str(repo),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        raise AssertionError(
            "测试装置 git %s 失败（returncode=%d）：%s"
            % (" ".join(args), proc.returncode,
               proc.stderr.decode("utf-8", errors="replace")))
    return proc.stdout.decode("utf-8", errors="replace")


def make_unit(uid, owned, verification, status="running"):
    """构造 §61 形状的 work unit（new_work_unit 真实构造；默认 running
    ——verify_unit → finish_unit 端到端链的合法起点）。"""
    return work_unit.new_work_unit(
        uid, "验证溯源场景单元 %s" % uid, executor="flash-implementer",
        ownership=list(owned), verification=list(verification),
        status=status)


class TempDirFixture(unittest.TestCase):
    """tempdir 基座：目录/状态文件写入 + Windows .git 只读位清理。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._force_cleanup)
        self.repo = Path(self._tmp.name)

    def _force_cleanup(self):
        """解除 git 只读对象后清理临时目录（模式复用 test_reconcile）。"""
        git_dir = os.path.join(self._tmp.name, ".git")
        if os.path.isdir(git_dir):
            for dirpath, _dirnames, filenames in os.walk(git_dir):
                for name in filenames:
                    try:
                        os.chmod(os.path.join(dirpath, name), stat.S_IWRITE)
                    except OSError:
                        pass
        self._tmp.cleanup()

    def dirty_file(self, rel, data):
        """在 fixture 仓库内写一个（未跟踪/已改动）文件（自动建父目录）。"""
        target = self.repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


@unittest.skipUnless(shutil.which("git"),
                     "需要 git 可执行文件（scratch 仓库必须 git init）")
class ProvenanceFixture(TempDirFixture):
    """真实 git 仓库 + 任务 state.json 基座（scratch 目录必须 git init：
    RB-1 证据门与证据指纹都依赖 git）。"""

    def setUp(self):
        super().setUp()
        run_git(self.repo, "init")
        run_git(self.repo, "config", "user.email", "provenance@example.com")
        run_git(self.repo, "config", "user.name", "Provenance")
        # 固定换行行为，避免全局 autocrlf 干扰指纹的换行归一口径
        run_git(self.repo, "config", "core.autocrlf", "false")
        # 运行时目录（state.json / events.jsonl / receipts）不入库
        self.dirty_file(".gitignore", b".glm-conductor/\n")
        self.dirty_file("base.txt", b"v1\n")
        run_git(self.repo, "add", ".")
        run_git(self.repo, "commit", "-m", "init")

    def make_task(self, units, *, ownership_files=("src/**",),
                  verification_required=(VERIFY_CMD,),
                  status="decomposed"):
        """写入任务 state.json（new_task_state 构造 + validate 全量校验）。"""
        st = state.new_task_state(
            TID, "验证溯源测试目标", dict(ROUTE),
            ownership_files=list(ownership_files),
            verification_required=list(verification_required),
            status=status)
        st["work_units"] = list(units)
        state.save_state(self.repo, st)
        return st

    def events(self, name):
        """读取真实 journal 并过滤事件名。"""
        return [e for e in journal_mod.read_events(self.repo, TID)
                if e.get("event") == name]

    def state_bytes(self):
        """读取 state.json 原始字节（零写入断言用）。"""
        with open(state.state_path(self.repo, TID), "rb") as fh:
            return fh.read()

    def receipt_names(self):
        """列出 receipts 目录文件名（目录不存在按空处理），排序确定。"""
        d = self.repo / ".glm-conductor" / "tasks" / TID / "receipts"
        if not d.is_dir():
            return []
        return sorted(entry.name for entry in d.iterdir())

    def receipt_path(self, name):
        return (self.repo / ".glm-conductor" / "tasks" / TID
                / "receipts" / name)

    def unit_status(self, st, uid):
        return next(u["status"] for u in st["work_units"] if u["id"] == uid)


# —— 冻结 receipt 键（wu-21-12 设计决策；超时键另行追加） ——

UNIT_RECEIPT_KEYS = {
    "kind", "scope", "unit", "command", "exit_code", "fingerprint",
    "observed_at", "runner", "duration_ms", "stdout_excerpt",
    "stderr_excerpt"}


class VerifyUnitTest(ProvenanceFixture):
    """verify_unit：happy path / 白名单闸 / policy 闸 / 失败 / 超时 /
    多命令 / excerpt / 原子写 / 错误路径。"""

    def test_verify_unit_happy_path_end_to_end_finish_unit(self):
        # 用例 1：exit 0 → receipt + verification_receipt + 证据事件；
        # fresh_unit_verification ok 且指纹一致；finish_unit 直接通过
        unit = make_unit("u1", ("src/**",), (VERIFY_CMD,))
        self.make_task([unit])
        self.dirty_file("src/main.py", b"hello\n")
        result = provenance.verify_unit(self.repo, TID, "u1")
        self.assertEqual(result["unit"], "u1")
        self.assertEqual(result["exit_codes"], {VERIFY_CMD: 0})
        self.assertTrue(result["all_passed"])
        self.assertEqual(len(result["receipts"]), 1)
        receipt = result["receipts"][0]
        # 冻结键名契约
        self.assertEqual(set(receipt.keys()), UNIT_RECEIPT_KEYS)
        self.assertNotIn("timeout", receipt)
        self.assertEqual(receipt["kind"], "verification")
        self.assertEqual(receipt["scope"], "unit")
        self.assertEqual(receipt["unit"], "u1")
        self.assertEqual(receipt["command"], VERIFY_CMD)
        self.assertEqual(receipt["exit_code"], 0)
        self.assertEqual(receipt["runner"], "glm-conductor-runtime")
        self.assertTrue(receipt["fingerprint"].startswith("sha256:"))
        self.assertIsInstance(receipt["duration_ms"], int)
        self.assertTrue(receipt["observed_at"].endswith("Z"))
        # durable 落盘：文件名前缀 + 内容与返回值逐字段一致
        names = self.receipt_names()
        self.assertEqual(len(names), 1)
        self.assertTrue(names[0].startswith("verification-"))
        self.assertTrue(names[0].endswith(".json"))
        with open(str(self.receipt_path(names[0])), "r",
                  encoding="utf-8") as fh:
            persisted = json.load(fh)
        self.assertEqual(persisted, receipt)
        # journal verification_receipt（同字段）
        vr = self.events("verification_receipt")
        self.assertEqual(len(vr), 1)
        for key, value in receipt.items():
            self.assertEqual(vr[0][key], value)
        # record_unit_verification 证据事件（H6 形状 + 同一指纹）
        evidences = self.events("verification")
        self.assertEqual(len(evidences), 1)
        self.assertEqual(evidences[0]["unit"], "u1")
        self.assertEqual(evidences[0]["command"], VERIFY_CMD)
        self.assertEqual(evidences[0]["status"], "pass")
        self.assertEqual(evidences[0]["fingerprint"],
                         receipt["fingerprint"])
        # RB-1 谓词：ok=True 且指纹与 receipt 一致
        evidence = reconcile.fresh_unit_verification(
            str(self.repo), TID, unit)
        self.assertTrue(evidence["ok"])
        self.assertEqual(evidence["fingerprint"], receipt["fingerprint"])
        # 端到端：verify_unit 之后 finish_unit 直接通过 RB-1 完成证据门
        st = task_manager.finish_unit(self.repo, TID, "u1")
        self.assertEqual(self.unit_status(st, "u1"), "completed")

    def test_verify_unit_whitelist_rejects_undeclared_zero_side_effects(self):
        # 用例 2：显式传不在 required 的命令 → ProvenanceError，零副作用
        unit = make_unit("u1", ("src/**",), (VERIFY_CMD,))
        self.make_task([unit])
        self.dirty_file("src/main.py", b"hello\n")
        before_state = self.state_bytes()
        before_events = journal_mod.read_events(self.repo, TID)
        with self.assertRaises(provenance.ProvenanceError) as ctx:
            provenance.verify_unit(self.repo, TID, "u1",
                                   command="git diff --stat")
        self.assertIn("白名单", str(ctx.exception))
        self.assertIn("git diff --stat", str(ctx.exception))
        self.assertEqual(self.state_bytes(), before_state)
        self.assertEqual(journal_mod.read_events(self.repo, TID),
                         before_events)
        self.assertEqual(self.receipt_names(), [])

    def test_verify_unit_policy_deny_rejected_no_receipt(self):
        # 用例 3：required 里塞 deny 类命令 → ProvenanceError，无 receipt
        # （command=None 与显式传入两条路径都闸）
        deny_cmd = "rm -rf /tmp/x"
        unit = make_unit("u1", ("src/**",), (deny_cmd,))
        self.make_task([unit])
        for kwargs in ({}, {"command": deny_cmd}):
            before_events = journal_mod.read_events(self.repo, TID)
            with self.assertRaises(provenance.ProvenanceError) as ctx:
                provenance.verify_unit(self.repo, TID, "u1", **kwargs)
            self.assertIn("deny", str(ctx.exception))
            self.assertEqual(journal_mod.read_events(self.repo, TID),
                             before_events)
            self.assertEqual(self.receipt_names(), [])

    def test_verify_unit_policy_ask_rejected_no_receipt(self):
        # 用例 4：ask 类命令（git push）同样拒绝
        unit = make_unit("u1", ("src/**",), ("git push",))
        self.make_task([unit])
        with self.assertRaises(provenance.ProvenanceError) as ctx:
            provenance.verify_unit(self.repo, TID, "u1")
        self.assertIn("ask", str(ctx.exception))
        self.assertEqual(self.receipt_names(), [])
        self.assertEqual(self.events("verification_receipt"), [])

    def test_verify_unit_nonzero_exit_records_receipt_not_evidence(self):
        # 用例 5：exit != 0 → receipt 记 exit_code=3、all_passed=False、
        # 无 verification 证据事件
        unit = make_unit("u1", ("src/**",), (FAIL_CMD,))
        self.make_task([unit])
        self.dirty_file("src/main.py", b"hello\n")
        result = provenance.verify_unit(self.repo, TID, "u1")
        self.assertFalse(result["all_passed"])
        self.assertEqual(result["exit_codes"], {FAIL_CMD: 3})
        receipt = result["receipts"][0]
        self.assertEqual(receipt["exit_code"], 3)
        self.assertEqual(len(self.receipt_names()), 1)
        self.assertEqual(len(self.events("verification_receipt")), 1)
        self.assertEqual(self.events("verification"), [])

    def test_verify_unit_timeout_marks_receipt_without_evidence(self):
        # 用例 6：超时 → receipt timeout=true、exit_code None
        unit = make_unit("u1", ("src/**",), (SLEEP_CMD,))
        self.make_task([unit])
        result = provenance.verify_unit(self.repo, TID, "u1",
                                        timeout_seconds=1)
        self.assertFalse(result["all_passed"])
        self.assertIsNone(result["exit_codes"][SLEEP_CMD])
        receipt = result["receipts"][0]
        self.assertIs(receipt.get("timeout"), True)
        self.assertIsNone(receipt["exit_code"])
        self.assertEqual(self.events("verification"), [])

    def test_verify_unit_multi_command_partial_pass(self):
        # 用例 7：command=None 两条 required（一过一败）→ 两条 receipt、
        # all_passed=False、仅过的一条有证据事件
        unit = make_unit("u1", ("src/**",), (VERIFY_CMD, FAIL_CMD))
        self.make_task([unit])
        self.dirty_file("src/main.py", b"hello\n")
        result = provenance.verify_unit(self.repo, TID, "u1")
        self.assertFalse(result["all_passed"])
        self.assertEqual(result["exit_codes"], {VERIFY_CMD: 0, FAIL_CMD: 3})
        self.assertEqual(len(result["receipts"]), 2)
        self.assertEqual(len(self.receipt_names()), 2)
        evidences = self.events("verification")
        self.assertEqual(len(evidences), 1)
        self.assertEqual(evidences[0]["command"], VERIFY_CMD)
        self.assertEqual(evidences[0]["status"], "pass")

    def test_excerpts_utf8_and_truncation(self):
        # 用例 9：stdout/stderr excerpt 截断（4096 字符）与 utf-8 中文；
        # durable 文件 ensure_ascii=False 明文可读
        code = ("print('中文验证输出ok'); import sys; "
                "sys.stdout.write('X' * 5000); "
                "sys.stderr.write('中文错误输出')")
        cmd = '"%s" -X utf8 -c "%s"' % (sys.executable, code)
        unit = make_unit("u1", ("src/**",), (cmd,))
        self.make_task([unit])
        result = provenance.verify_unit(self.repo, TID, "u1")
        self.assertTrue(result["all_passed"])
        receipt = result["receipts"][0]
        self.assertEqual(receipt["exit_code"], 0)
        self.assertEqual(len(receipt["stdout_excerpt"]), 4096)
        self.assertTrue(receipt["stdout_excerpt"].startswith("中文验证输出ok"))
        self.assertEqual(receipt["stderr_excerpt"], "中文错误输出")
        names = self.receipt_names()
        self.assertEqual(len(names), 1)
        with open(str(self.receipt_path(names[0])), "rb") as fh:
            raw = fh.read().decode("utf-8")
        self.assertIn("中文验证输出ok", raw)
        self.assertIn("中文错误输出", raw)

    def test_receipts_dir_atomic_write_no_tmp_residue(self):
        # 用例 10：receipts 目录原子写 → 执行后无 .tmp 残留
        unit = make_unit("u1", ("src/**",), (VERIFY_CMD, FAIL_CMD))
        self.make_task([unit])
        provenance.verify_unit(self.repo, TID, "u1")
        names = self.receipt_names()
        self.assertEqual(len(names), 2)
        for name in names:
            self.assertTrue(name.endswith(".json"))
            self.assertFalse(name.endswith(".tmp"))

    def test_missing_task_and_unit_raise_provenance_error(self):
        # 补充：任务缺失 / 单元缺失 → ProvenanceError（task_manager 同款
        # 错误口径的本模块变体），零 receipt
        unit = make_unit("u1", ("src/**",), (VERIFY_CMD,))
        self.make_task([unit])
        with self.assertRaises(provenance.ProvenanceError):
            provenance.verify_unit(self.repo, TID, "ghost")
        with self.assertRaises(provenance.ProvenanceError):
            provenance.verify_unit(self.repo, "no-such-task", "u1")
        self.assertEqual(self.receipt_names(), [])


class VerifyTaskTest(ProvenanceFixture):
    """verify_task：任务级白名单 + 完成门同入口指纹 + state 落账。"""

    def test_verify_task_records_state_verification(self):
        # 用例 8：任务级 required → receipt scope="task" +
        # state.record_verification 落账（completed 含该命令）
        self.make_task([make_unit("u1", ("src/**",), (VERIFY_CMD,))])
        self.dirty_file("src/main.py", b"hello\n")
        result = provenance.verify_task(self.repo, TID)
        self.assertEqual(result["task"], TID)
        self.assertTrue(result["all_passed"])
        self.assertEqual(result["exit_codes"], {VERIFY_CMD: 0})
        receipt = result["receipts"][0]
        self.assertEqual(receipt["kind"], "verification")
        self.assertEqual(receipt["scope"], "task")
        self.assertEqual(receipt["task"], TID)
        self.assertEqual(receipt["runner"], "glm-conductor-runtime")
        # state.record_verification 落账并已 save
        st = state.load_state(self.repo, TID)
        self.assertIn(VERIFY_CMD, st["verification"]["completed"])
        self.assertEqual(st["verification"]["fingerprint"],
                         receipt["fingerprint"])
        # 与完成门同一入口复算一致（receipt 指纹可对账）
        self.assertEqual(
            fingerprint_mod.task_fingerprint(str(self.repo), st),
            receipt["fingerprint"])
        vr = self.events("verification_receipt")
        self.assertEqual(len(vr), 1)
        self.assertEqual(vr[0]["scope"], "task")
        self.assertEqual(vr[0]["task"], TID)

    def test_verify_task_whitelist_rejects_undeclared_command(self):
        # 补充：verify_task 白名单 = state.verification.required
        self.make_task([make_unit("u1", ("src/**",), (VERIFY_CMD,))])
        with self.assertRaises(provenance.ProvenanceError) as ctx:
            provenance.verify_task(self.repo, TID, command="git diff --stat")
        self.assertIn("白名单", str(ctx.exception))
        self.assertEqual(self.receipt_names(), [])
        self.assertEqual(self.events("verification_receipt"), [])
        # 任务级 state 未被写入任何 completed
        st = state.load_state(self.repo, TID)
        self.assertEqual(st["verification"]["completed"], [])


if __name__ == "__main__":
    unittest.main()
