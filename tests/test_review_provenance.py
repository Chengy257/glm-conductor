#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""runtime.provenance.run_review 审查溯源测试（v2.1 M6，wu-21-13，§16.5）。

规格六用例 + 补充（scratch 任务目录真实 git init——终指纹与完成门同一
入口 fingerprint.task_fingerprint，依赖 git）：
  1. run_review happy：ship → receipt 文件（冻结键）+ journal
     review_receipt（同字段）+ state.review 同步（verdict / fingerprint
     与 receipt 恒一致——完成门快速路径字段由同一次调用维护）；
  2. verdict 非法（计划侧泛称 "changes_requested" / 手写拼写偏差
     "Ship"）→ ProvenanceError，零副作用（无 receipt、无事件、
     state.json 字节不变）；
  3. route 非法 → ProvenanceError；route=None 恒法（receipt["route"]
     为 None）；
  4. note 透传（中文、durable 文件 ensure_ascii=False 明文可读）与
     null 默认（note 恒入键，未传为 None）；
  5. 多张 receipt：fix-first 后 ship → 最新一张为准（observed_at 排序，
     经 stop_gate.latest_review_receipt 同一扫描函数验证）；
  6. tool_use_id / reviewer 空串拒绝；
  补充：任务缺失 → ProvenanceError；durable 原子写无 .tmp 残留。

RB-21-02 调用真实性回验（receipt 从申报制升级为 runtime-observed
invocation + runtime-bound verdict；规格命名 §10.2）：
  7. 白名单：reviewer ∉ agent_run.REVIEWER_PROFILES → 拒；
  8. invocation 存在：无 reviewer_invoked 事件（伪造 tool_use_id）→ 拒；
  9. reviewer/task 匹配：身份不匹配 / 跨任务申报 → 拒；
  10. replay 闸：同 tool_use_id + 同 verdict → 幂等返回既有 receipt
      （不新建第二份、不重复记事件）；verdict 矛盾 → 拒；
  11. 真实链路：invocation + ship → receipt 可过完成门扫描；审查后
      仓库再变动 → receipt 指纹过期（stale 语义）。

git fixture 做法（git init + config + commit、Windows 下 .git 只读位
清理）对齐 tests/test_provenance.py 的 ProvenanceFixture；环境无 git
可执行时整类自动 skipTest。被检仓库目录由 tempfile.TemporaryDirectory
提供，绝不读写真实工作区与真实账本。

运行：
    cd <repo_root> && python3 -m unittest tests.test_review_provenance -v
"""

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "glm-conductor"))
from runtime import agent_run
from runtime import fingerprint as fingerprint_mod
from runtime import journal as journal_mod
from runtime import provenance
from runtime import state

# hooks 侧的 receipt 扫描函数（用例 5「最新一张为准」的同一消费入口）
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "glm-conductor" / "hooks"))
from stop_gate import latest_review_receipt

TID = "rev-prov-task-1a2b"
ROUTE = {"mode": "solo", "delegability": "low", "assurance": "standard",
         "executor": "main", "continuity": "foreground"}

# 冻结 receipt 键（wu-21-13 设计决策；note 恒入键）
REVIEW_RECEIPT_KEYS = {
    "kind", "reviewer", "route", "verdict", "fingerprint", "tool_use_id",
    "observed_at", "runner", "note"}


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


class TempDirFixture(unittest.TestCase):
    """tempdir 基座：目录/状态文件写入 + Windows .git 只读位清理。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._force_cleanup)
        self.repo = Path(self._tmp.name)

    def _force_cleanup(self):
        """解除 git 只读对象后清理临时目录（模式复用 test_provenance）。"""
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
class ReviewProvenanceFixture(TempDirFixture):
    """真实 git 仓库 + 任务 state.json 基座（终指纹依赖 git）。"""

    def setUp(self):
        super().setUp()
        run_git(self.repo, "init")
        run_git(self.repo, "config", "user.email", "review@example.com")
        run_git(self.repo, "config", "user.name", "Review Provenance")
        # 固定换行行为，避免全局 autocrlf 干扰指纹的换行归一口径
        run_git(self.repo, "config", "core.autocrlf", "false")
        # 运行时目录（state.json / events.jsonl / receipts）不入库
        self.dirty_file(".gitignore", b".glm-conductor/\n")
        self.dirty_file("base.txt", b"v1\n")
        run_git(self.repo, "add", ".")
        run_git(self.repo, "commit", "-m", "init")

    def make_review_task(self, task_id=TID, reviewer="glm-reviewer"):
        """写入一个 review.required 任务（含 ownership 声明，指纹有界）。"""
        self.dirty_file("src/main.py", b"reviewed\n")
        st = state.new_task_state(
            task_id, "审查溯源测试目标", dict(ROUTE),
            ownership_files=["src/**"], review_required=True,
            reviewer=reviewer, status="reviewing")
        state.save_state(self.repo, st)
        return st

    def record_invocation(self, tool_use_id, reviewer="glm-reviewer",
                          task_id=TID, run_in_background=None):
        """RB-21-02 真实链路前置：经 agent_run.record_reviewer_invocation
        真实入口直落一条 reviewer_invoked 事件（hook PostToolUse 分支的
        等价调用——receipt 申报必须先有 runtime 观察到的调用账本）。"""
        tool_input = {"subagent_type": reviewer,
                      "prompt": "review it %s"
                                % agent_run.review_marker_for(task_id)}
        if run_in_background is not None:
            tool_input["run_in_background"] = run_in_background
        payload = {
            "hook_event_name": "PostToolUse", "tool_name": "Agent",
            "tool_input": tool_input, "tool_use_id": tool_use_id,
            "tool_response": {"agentId": "agent-review-fixture"}}
        return agent_run.record_reviewer_invocation(
            str(self.repo), task_id, payload)

    def events(self, name, task_id=TID):
        """读取真实 journal 并过滤事件名。"""
        return [e for e in journal_mod.read_events(self.repo, task_id)
                if e.get("event") == name]

    def state_bytes(self, task_id=TID):
        """读取 state.json 原始字节（零写入断言用）。"""
        with open(state.state_path(self.repo, task_id), "rb") as fh:
            return fh.read()

    def receipt_dir(self, task_id=TID):
        """任务 receipts 目录（可能不存在）。"""
        return state.task_dir(self.repo, task_id) / "receipts"

    def receipt_names(self, task_id=TID):
        """列出 receipts 目录文件名（目录不存在按空处理），排序确定。"""
        d = self.receipt_dir(task_id)
        if not d.is_dir():
            return []
        return sorted(entry.name for entry in d.iterdir())

    def read_receipt(self, name, task_id=TID):
        """读取 receipts 目录内一份凭证 JSON。"""
        with open(str(self.receipt_dir(task_id) / name), "r",
                  encoding="utf-8") as fh:
            return json.load(fh)


class RunReviewTest(ReviewProvenanceFixture):
    """run_review：happy path / 参数闸 / note / 多 receipt / 错误路径。"""

    def test_run_review_ship_receipt_journal_and_state_sync(self):
        # 用例 1：ship → receipt 文件（冻结键）+ journal review_receipt
        # （同字段）+ state.review 同步（verdict / fingerprint 恒一致）
        # RB-21-02：先落 runtime 观察到的 reviewer_invoked（真实链路）
        self.make_review_task()
        self.record_invocation("toolu-run-001")
        receipt = provenance.run_review(
            str(self.repo), TID, reviewer="glm-reviewer", verdict="ship",
            tool_use_id="toolu-run-001", route="delegate", note="looks good")
        # 冻结键名契约（note 恒入键）
        self.assertEqual(set(receipt.keys()), REVIEW_RECEIPT_KEYS)
        self.assertEqual(receipt["kind"], "review")
        self.assertEqual(receipt["reviewer"], "glm-reviewer")
        self.assertEqual(receipt["route"], "delegate")
        self.assertEqual(receipt["verdict"], "ship")
        self.assertEqual(receipt["tool_use_id"], "toolu-run-001")
        self.assertEqual(receipt["note"], "looks good")
        self.assertEqual(receipt["runner"], "glm-conductor-runtime")
        self.assertTrue(receipt["observed_at"].endswith("Z"))
        # 指纹 = 完成门同一入口复算值（终指纹绑定，可直接与门对账）
        st = state.load_state(self.repo, TID)
        self.assertEqual(receipt["fingerprint"],
                         fingerprint_mod.task_fingerprint(str(self.repo), st))
        # durable 落盘：文件名前缀 review- + 内容与返回值逐字段一致
        names = self.receipt_names()
        self.assertEqual(len(names), 1)
        self.assertTrue(names[0].startswith("review-"))
        self.assertTrue(names[0].endswith(".json"))
        self.assertEqual(self.read_receipt(names[0]), receipt)
        # journal review_receipt（同字段）
        vr = self.events("review_receipt")
        self.assertEqual(len(vr), 1)
        for key, value in receipt.items():
            self.assertEqual(vr[0][key], value)
        # state.review 同步：完成门快速路径字段与 receipt 恒一致
        self.assertEqual(st["review"]["verdict"], "ship")
        self.assertEqual(st["review"]["fingerprint"], receipt["fingerprint"])
        self.assertTrue(st["review"]["required"])  # 同步不翻转 required
        self.assertEqual(st["review"]["reviewer"], "glm-reviewer")

    def test_run_review_invalid_verdict_zero_side_effects(self):
        # 用例 2：verdict 非法（计划侧泛称 + 手写拼写偏差）→
        # ProvenanceError，零副作用（无 receipt、无事件、state 字节不变）
        self.make_review_task()
        before_state = self.state_bytes()
        before_events = journal_mod.read_events(self.repo, TID)
        for bad in ("changes_requested", "Ship", "approve", ""):
            with self.subTest(verdict=bad), \
                    self.assertRaises(provenance.ProvenanceError) as ctx:
                provenance.run_review(
                    str(self.repo), TID, reviewer="glm-reviewer",
                    verdict=bad, tool_use_id="toolu-run-002")
            self.assertIn("verdict", str(ctx.exception))
        self.assertEqual(self.state_bytes(), before_state)
        self.assertEqual(journal_mod.read_events(self.repo, TID),
                         before_events)
        self.assertEqual(self.receipt_names(), [])

    def test_run_review_route_invalid_rejected_none_always_legal(self):
        # 用例 3：route 非法 → ProvenanceError；route=None 恒法（receipt
        # 键仍在、取值 None）
        self.make_review_task()
        before_state = self.state_bytes()
        with self.assertRaises(provenance.ProvenanceError) as ctx:
            provenance.run_review(
                str(self.repo), TID, reviewer="glm-reviewer", verdict="ship",
                tool_use_id="toolu-run-003", route="parallel")
        self.assertIn("route", str(ctx.exception))
        self.assertEqual(self.state_bytes(), before_state)
        self.assertEqual(self.receipt_names(), [])
        # route=None 恒法（RB-21-02：两次申报各配一条 invocation 账本）
        self.record_invocation("toolu-run-003")
        receipt = provenance.run_review(
            str(self.repo), TID, reviewer="glm-reviewer", verdict="ship",
            tool_use_id="toolu-run-003")
        self.assertIn("route", receipt)
        self.assertIsNone(receipt["route"])
        # 合法 mode 值透传
        self.record_invocation("toolu-run-003b")
        routed = provenance.run_review(
            str(self.repo), TID, reviewer="glm-reviewer", verdict="ship",
            tool_use_id="toolu-run-003b", route="audit")
        self.assertEqual(routed["route"], "audit")

    def test_run_review_note_passthrough_and_null_default(self):
        # 用例 4：note 透传（中文、durable 文件 ensure_ascii=False 明文
        # 可读）与 null 默认（note 恒入键，未传为 None）
        self.make_review_task()
        self.record_invocation("toolu-run-004a")
        plain = provenance.run_review(
            str(self.repo), TID, reviewer="glm-reviewer", verdict="ship",
            tool_use_id="toolu-run-004a")
        self.assertIn("note", plain)
        self.assertIsNone(plain["note"])
        self.record_invocation("toolu-run-004b")
        noted = provenance.run_review(
            str(self.repo), TID, reviewer="glm-reviewer", verdict="ship",
            tool_use_id="toolu-run-004b", note="中文审查备注：通过")
        self.assertEqual(noted["note"], "中文审查备注：通过")
        # durable 文件里中文明文可读（ensure_ascii=False）
        names = self.receipt_names()
        self.assertEqual(len(names), 2)
        with open(str(self.receipt_dir() / names[-1]), "r",
                  encoding="utf-8") as fh:
            raw = fh.read()
        self.assertIn("中文审查备注：通过", raw)

    def test_run_review_latest_receipt_wins_after_rereview(self):
        # 用例 5：多张 receipt——fix-first 后重审 ship → 最新一张为准
        # （observed_at 排序；扫描用完成门同一函数 latest_review_receipt）
        self.make_review_task()
        self.record_invocation("toolu-run-005a")
        first = provenance.run_review(
            str(self.repo), TID, reviewer="glm-reviewer",
            verdict="fix-first", tool_use_id="toolu-run-005a",
            note="round 1")
        time.sleep(0.01)  # observed_at 毫秒精度：留出间隔保证严格递增
        self.record_invocation("toolu-run-005b")
        second = provenance.run_review(
            str(self.repo), TID, reviewer="glm-reviewer", verdict="ship",
            tool_use_id="toolu-run-005b", note="round 2")
        self.assertGreater(second["observed_at"], first["observed_at"])
        names = self.receipt_names()
        self.assertEqual(len(names), 2)
        self.assertTrue(all(name.startswith("review-")
                            and name.endswith(".json") for name in names))
        # 完成门同一扫描函数：取 observed_at 最新一张（ship）
        latest = latest_review_receipt(str(self.repo), TID)
        self.assertEqual(latest["verdict"], "ship")
        self.assertEqual(latest["observed_at"], second["observed_at"])
        self.assertEqual(latest["tool_use_id"], "toolu-run-005b")

    def test_run_review_empty_reviewer_and_tool_use_id_rejected(self):
        # 用例 6：reviewer / tool_use_id 空串拒绝（先全量校验后副作用）
        self.make_review_task()
        before_state = self.state_bytes()
        before_events = journal_mod.read_events(self.repo, TID)
        with self.assertRaises(provenance.ProvenanceError):
            provenance.run_review(
                str(self.repo), TID, reviewer="", verdict="ship",
                tool_use_id="toolu-run-006")
        with self.assertRaises(provenance.ProvenanceError):
            provenance.run_review(
                str(self.repo), TID, reviewer="glm-reviewer", verdict="ship",
                tool_use_id="")
        with self.assertRaises(provenance.ProvenanceError):
            provenance.run_review(
                str(self.repo), TID, reviewer=None, verdict="ship",
                tool_use_id="toolu-run-006")
        self.assertEqual(self.state_bytes(), before_state)
        self.assertEqual(journal_mod.read_events(self.repo, TID),
                         before_events)
        self.assertEqual(self.receipt_names(), [])

    def test_run_review_missing_task_raises_no_receipt(self):
        # 补充：任务缺失 → ProvenanceError（同款错误口径），零 receipt
        self.make_review_task()
        with self.assertRaises(provenance.ProvenanceError) as ctx:
            provenance.run_review(
                str(self.repo), "ghost-task", reviewer="glm-reviewer",
                verdict="ship", tool_use_id="toolu-run-007")
        self.assertIn("ghost-task", str(ctx.exception))
        self.assertEqual(self.receipt_names(), [])

    def test_run_review_durable_atomic_write_no_tmp_residue(self):
        # 补充：durable 原子写 → 落盘后无 .tmp 残留
        self.make_review_task()
        for index in range(2):
            self.record_invocation("toolu-run-008-%d" % index)
            provenance.run_review(
                str(self.repo), TID, reviewer="glm-reviewer",
                verdict="ship" if index else "fix-first",
                tool_use_id="toolu-run-008-%d" % index)
            time.sleep(0.01)
        names = self.receipt_names()
        self.assertEqual(len(names), 2)
        for name in names:
            self.assertTrue(name.endswith(".json"))
            self.assertFalse(name.endswith(".tmp"))


class ReviewerInvocationRecheckTest(ReviewProvenanceFixture):
    """RB-21-02 调用真实性回验链（规格 §10.2 命名）：
    reviewer 白名单 → reviewer_invoked 存在 → reviewer/task 匹配 →
    replay 闸 → 终指纹绑定。任一不满足 ProvenanceError 且零副作用。"""

    def test_non_reviewer_tool_use_id_rejected(self):
        # 白名单闸：reviewer 不在 REVIEWER_PROFILES → 拒（即使 invocation
        # 已落账也不行——receipt 只受理 runtime 已知审查者身份）
        self.make_review_task()
        self.record_invocation("toolu-nonprofile-001")
        before_state = self.state_bytes()
        before_events = journal_mod.read_events(self.repo, TID)
        for bad in ("random-reviewer", "glm-advisor", "claude-reviewer"):
            with self.subTest(reviewer=bad), \
                    self.assertRaises(provenance.ProvenanceError) as ctx:
                provenance.run_review(
                    str(self.repo), TID, reviewer=bad, verdict="ship",
                    tool_use_id="toolu-nonprofile-001")
            self.assertIn("REVIEWER_PROFILES", str(ctx.exception))
        self.assertEqual(self.state_bytes(), before_state)
        self.assertEqual(journal_mod.read_events(self.repo, TID),
                         before_events)
        self.assertEqual(self.receipt_names(), [])

    def test_fake_review_tool_use_id_rejected(self):
        # invocation 闸：伪造任意 tool_use_id（无 reviewer_invoked 账本）
        # → 拒，零副作用——receipt 不再受理口头申报（本 WU 目的性断言）
        self.make_review_task()
        before_state = self.state_bytes()
        before_events = journal_mod.read_events(self.repo, TID)
        with self.assertRaises(provenance.ProvenanceError) as ctx:
            provenance.run_review(
                str(self.repo), TID, reviewer="glm-reviewer", verdict="ship",
                tool_use_id="toolu-fake-no-invocation")
        self.assertIn("reviewer_invoked", str(ctx.exception))
        self.assertIn("toolu-fake-no-invocation", str(ctx.exception))
        self.assertEqual(self.state_bytes(), before_state)
        self.assertEqual(journal_mod.read_events(self.repo, TID),
                         before_events)
        self.assertEqual(self.receipt_names(), [])

    def test_reviewer_identity_mismatch_rejected(self):
        # 身份匹配闸：invocation 记录 glm-reviewer，申报 visual-reviewer
        # → 拒（receipt 只受理与真实调用一致的身份）
        self.make_review_task()
        self.record_invocation("toolu-identity-001", reviewer="glm-reviewer")
        before_events = journal_mod.read_events(self.repo, TID)
        with self.assertRaises(provenance.ProvenanceError) as ctx:
            provenance.run_review(
                str(self.repo), TID, reviewer="visual-reviewer",
                verdict="ship", tool_use_id="toolu-identity-001")
        self.assertIn("身份不匹配", str(ctx.exception))
        self.assertEqual(journal_mod.read_events(self.repo, TID),
                         before_events)
        self.assertEqual(self.receipt_names(), [])

    def test_review_tool_use_wrong_task_rejected(self):
        # task 匹配闸：invocation 绑定任务 A，申报任务 B（同 tool_use_id）
        # → 拒（一次审查调用不可跨任务申报；B 的 journal 无该 invocation）
        self.make_review_task()
        other_tid = "rev-prov-task-other9"
        self.make_review_task(task_id=other_tid)
        self.record_invocation("toolu-crosstask-001", task_id=TID)
        before_state = self.state_bytes(other_tid)
        with self.assertRaises(provenance.ProvenanceError) as ctx:
            provenance.run_review(
                str(self.repo), other_tid, reviewer="glm-reviewer",
                verdict="ship", tool_use_id="toolu-crosstask-001")
        self.assertIn("toolu-crosstask-001", str(ctx.exception))
        self.assertEqual(self.state_bytes(other_tid), before_state)
        self.assertEqual(self.receipt_names(other_tid), [])

    def test_review_tool_use_replay_idempotent(self):
        # replay 闸：同 tool_use_id + 同 task + 同 verdict 重复申报 →
        # 幂等（返回既有 receipt，不新建第二份、不重复记事件、state
        # 不重写）；同 tool_use_id 但 verdict 矛盾 → ProvenanceError
        self.make_review_task()
        self.record_invocation("toolu-replay-001")
        first = provenance.run_review(
            str(self.repo), TID, reviewer="glm-reviewer", verdict="ship",
            tool_use_id="toolu-replay-001", note="round 1")
        time.sleep(0.01)
        replayed = provenance.run_review(
            str(self.repo), TID, reviewer="glm-reviewer", verdict="ship",
            tool_use_id="toolu-replay-001", note="第二轮申报（应幂等）")
        # 返回与首次等价的结果（含首次 observed_at，note 不覆盖）
        self.assertEqual(replayed, first)
        self.assertEqual(replayed["observed_at"], first["observed_at"])
        self.assertEqual(replayed["note"], "round 1")
        # 不新建第二份 receipt、不重复记 review_receipt 事件
        self.assertEqual(len(self.receipt_names()), 1)
        self.assertEqual(len(self.events("review_receipt")), 1)
        # 同 tool_use_id 但 verdict 矛盾 → 拒（且零新副作用）
        with self.assertRaises(provenance.ProvenanceError) as ctx:
            provenance.run_review(
                str(self.repo), TID, reviewer="glm-reviewer",
                verdict="fix-first", tool_use_id="toolu-replay-001")
        self.assertIn("replay", str(ctx.exception))
        self.assertEqual(len(self.receipt_names()), 1)
        self.assertEqual(len(self.events("review_receipt")), 1)

    def test_real_reviewer_ship_receipt_passes(self):
        # 真实链路：invocation + ship 申报 → receipt（冻结键）可通过
        # 完成门同一扫描函数（runner/形状闸全过）且指纹与完成门一致
        self.make_review_task()
        invocation = self.record_invocation("toolu-real-001")
        # 事件字段冻结：六键 + event/ts
        self.assertEqual(
            set(invocation.keys()),
            {"ts", "event", "tool_use_id", "reviewer", "task_id",
             "agent_id", "execution_mode"})
        self.assertEqual(invocation["event"], "reviewer_invoked")
        self.assertEqual(invocation["tool_use_id"], "toolu-real-001")
        self.assertEqual(invocation["reviewer"], "glm-reviewer")
        self.assertEqual(invocation["task_id"], TID)
        self.assertEqual(invocation["agent_id"], "agent-review-fixture")
        self.assertEqual(invocation["execution_mode"], "foreground")
        receipt = provenance.run_review(
            str(self.repo), TID, reviewer="glm-reviewer", verdict="ship",
            tool_use_id="toolu-real-001", route="audit")
        st = state.load_state(self.repo, TID)
        self.assertEqual(
            receipt["fingerprint"],
            fingerprint_mod.task_fingerprint(str(self.repo), st))
        latest = latest_review_receipt(str(self.repo), TID)
        self.assertIsNotNone(latest)
        self.assertEqual(latest["verdict"], "ship")
        self.assertEqual(latest["runner"], "glm-conductor-runtime")
        self.assertEqual(latest, receipt)

    def test_review_receipt_becomes_stale_after_repo_change(self):
        # runtime-bound verdict：审查后仓库再变动 → receipt 指纹不再
        # 等于当前任务指纹（完成门 review_stale 的判定输入成立）
        self.make_review_task()
        self.record_invocation("toolu-stalerev-001")
        receipt = provenance.run_review(
            str(self.repo), TID, reviewer="glm-reviewer", verdict="ship",
            tool_use_id="toolu-stalerev-001")
        st = state.load_state(self.repo, TID)
        self.assertEqual(
            receipt["fingerprint"],
            fingerprint_mod.task_fingerprint(str(self.repo), st))
        self.dirty_file("src/main.py", b"reviewed-v2\n")
        st_after = state.load_state(self.repo, TID)
        self.assertNotEqual(
            receipt["fingerprint"],
            fingerprint_mod.task_fingerprint(str(self.repo), st_after))


if __name__ == "__main__":
    unittest.main()
