#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""runtime.task_manager 单元测试（v2.0.1 加固工作包 H4，审查项 P1-3）。

仅 Python 3 标准库（unittest + tempfile + subprocess git fixture），
零第三方依赖；state.json / leases.json / events.jsonl 全部落盘在
tempfile.TemporaryDirectory 提供的临时目录（真实 new_task_state +
new_work_unit 构造，非 mock state），不污染真实工作区。

git fixture 分层：基础夹具（TaskManagerTestBase）保持零 git 特质——
转换语义 / 租约 / 事件等测试不依赖 git；RB-1 完成证据门（WU-P2）的
证据路径需要真实 git 仓库与真实指纹，归入 GitRepoFixture 系列类
（做法对齐 tests/test_reconcile.py / tests/test_stop_gate.py，环境无
git 可执行时自动 skipTest）。空 verification 单元不能作为夹具兜底：
validate_state 拒绝「无验证命令的单元」（不可派发），因此完成路径
必须走真实证据，这是门语义本身的要求而非夹具妥协。

覆盖：
    - prepare_dispatch happy path：决策快照返回（dispatch 组含 uid）/
      租约落盘（owner==uid）/ dispatch_prepared 事件（leased=归一路径
      排序）/ 单元状态与 state.json 字节不被改动（无 state 副作用）/
      反斜杠 ownership 归一 / 同 owner 重复 prepare 幂等 /
      max_workers 缺省取 dispatch.max_workers（显式传参覆盖）；
    - prepare 失败：单元缺失 / 单元非 ready（消息含当前状态）/ plan
      落选 quota EXHAUSTED（消息含 quota EXHAUSTED）/ plan 落选
      deferred（消息含 reason）/ 依赖未满足未进候选（fallback 理由）/
      落选零副作用（不写租约不写事件）/ 非法 quota_status ValueError
      透传；
    - commit happy：ready→running / active 记账 / 任务级 created/
      decomposed→executing 直达边（executing、joining 不被翻动）/
      落盘读回一致 / implementation_started 事件（含 executor）/
      active 追加幂等防御；
    - commit 失败：未 prepare 无租约（消息提示 prepare_dispatch）/
      租约丢失（消息提示 abort_dispatch 重备）/ 异主租约 / 重复
      commit（消息含 running）；
    - abort：running 拒绝（不得静默回退，租约未被动）/ happy（释放
      租约 + dispatch_aborted 事件 + state.json 零写入）/ active 残留
      清理（移除 + save）；
    - finish：failed / cancelled / 非法 outcome ValueError（校验先于
      I/O）/ 状态不符 TaskManagerError / 单元缺失（零 git 夹具）；
    - RB-1 完成证据门拒绝（git fixture，计划 §2.5 A1-A12）：running/
      verifying 无证据 / fail 事件 / wrong-unit / 非 required command /
      partial（missing 恰为未验证那条）/ stale 指纹（record 后改动
      owned 文件）/ legacy 无 unit 字段事件 → 一律 TaskManagerError；
      拒绝零副作用四连断言（state.json 字节 / 租约 / dispatch.active /
      journal 不变，无 unit_finished 也无拒绝事件）；非 git 目录 +
      required 单元 fail-closed（零 git 夹具）；
    - RB-1 完成门通过（git fixture，计划 §2.5 B13-B19）：真实指纹
      （runtime.fingerprint.compute_fingerprint 对 owned_hits 真算）+
      新鲜 pass 证据 → completed；单/双 required 全证据 / running 态
      仍走双跳（transition spy 恰好两次表内转换）/ verifying 态完成 /
      租约释放 / active 移除 / unit_finished 落盘（unit + outcome）/
      全生命周期事件序（dispatch_prepared→implementation_started→
      verification→unit_finished）；
    - RB-1 × DAG 集成（计划 §2.5 C20-C21）：上游无证据被拒 → 单元仍
      running → refresh_readiness 不提升下游（不得 ready）；上游完整
      证据完成 → refresh_readiness 提升下游 ready；
    - 崩溃窗口模拟两条：prepare 后直接 commit 成功（事件序
      dispatch_prepared→implementation_started、running + active +
      租约在位）；prepare 后 abort 释放（租约清空、单元仍 ready、
      可重新 prepare）；
    - 事件词汇：RECOMMENDED_EVENTS 三条新增事件名 + 插入位置
      （implementation_started 之后 / checkpoint_written 之前），
      真实 journal 行由各 API 用例断言；
    - H5（租约崩溃恢复）：prepare 默认 TTL 落盘（expires_at/generation/
      heartbeat_at/session_id + dispatch_prepared 的 ttl_seconds 字段）/
      recover_leases（释放 stale + lease_recovered 事件、active 保留、
      expired_running 不动仅上报、零释放不落事件、缺 state 报错）/
      端到端崩溃场景 orphan_lease_reconciled_after_interrupted_running_unit
      （running 单元 + 孤儿租约 → reconcile_interrupted 建议 ready →
      应用转换 → recover_leases 闭环清理）；
    - H6（验证证据归属绑定）：record_unit_verification 正常写入逐字段
      锚定 / fail 合法词汇 / 空 uid-command-fingerprint 与非法 status
      各抛 ValueError（消息含字段名）；
    - RELEASE（refresh_readiness）：pending+依赖满足提升并返回 /
      waiting_dependency 提升 / 依赖未满足不提升零写入 / 全 ready 与
      空图零写入零返回零 journal / 提升序按 units 出现序 / 提升后
      prepare_dispatch 直接准入（集成）。

运行：
    cd <repo_root> && python3 -m unittest tests.test_task_manager -v
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
from runtime import journal
from runtime import lease
from runtime import ownership
from runtime import reconcile
from runtime import state
from runtime import task_manager
from runtime import work_unit

TID = "tm-task-1a2b3c"
VERIFY_CMD = "python3 -m unittest tests.test_task_manager"

LEASE_T0 = "2026-01-01T00:00:00.000Z"
LEASE_NOW = "2026-06-01T00:00:00.000Z"
LEASE_FUTURE = "2099-01-01T00:00:00.000Z"
LEASE_PAST = "2020-01-01T00:00:00.000Z"


# —— 测试夹具 ——

def wu(uid, owned=("src/a/**",), deps=(), status="ready",
       executor="flash-implementer", verification=None):
    """构造 §61 形状的 work unit dict（new_work_unit 真实构造）。

    默认带 VERIFY_CMD 验证命令——不能用空 verification 兜底：validate_state
    拒绝「无验证命令的单元」（不可派发），空 required 单元根本落不了盘。
    无需证据的收尾路径用 outcome != "completed"（failed / cancelled 不过
    RB-1 完成门）；完成路径的用例进 GitRepoFixture 系列类配真实证据。
    """
    return work_unit.new_work_unit(
        uid, "目标 %s" % uid, executor=executor,
        ownership=list(owned),
        verification=([VERIFY_CMD] if verification is None
                      else list(verification)),
        depends_on=list(deps), status=status)


def make_task(root, units, *, status="decomposed", max_workers=1,
              active=()):
    """在临时目录真实落盘一个任务 state（new_task_state 构造 + save）。"""
    st = state.new_task_state(
        TID, "事务边界测试目标",
        {"mode": "delegate", "delegability": "high",
         "assurance": "standard", "executor": "flash-implementer",
         "continuity": "foreground"},
        ownership_files=("src/**",),
        verification_required=(VERIFY_CMD,),
        status=status)
    st["work_units"] = list(units)
    st["dispatch"] = {"max_workers": max_workers, "active": list(active)}
    state.save_state(root, st)
    return st


def state_bytes(root):
    """读取 state.json 原始字节（零写入断言用）。"""
    with open(state.state_path(root, TID), "rb") as fh:
        return fh.read()


def events(root, name):
    """读取真实 journal 并过滤事件名。"""
    return [e for e in journal.read_events(root, TID)
            if e.get("event") == name]


def unit_of(st, uid):
    """从 state dict 取单元 dict。"""
    return next(u for u in st["work_units"] if u["id"] == uid)


class TaskManagerTestBase(unittest.TestCase):
    """临时目录夹具：每个用例独立的 repo_root（无 git 需求）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = self._tmp.name


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


class GitRepoFixture(TaskManagerTestBase):
    """在 tempdir 基座上初始化真实 git 仓库（RB-1 完成证据门专用；
    模式对齐 tests/test_reconcile.py / tests/test_stop_gate.py）。

    无 git 可执行的环境请给派生类标注
    @unittest.skipUnless(shutil.which("git"), …)（本文件的 git fixture
    测试类均显式标注）。
    """

    def setUp(self):
        super().setUp()
        # 注册在基类 cleanup 之后（LIFO：先解除 .git 只读位再删目录）
        self.addCleanup(self._force_cleanup)
        self.repo = Path(self.root)
        run_git(self.repo, "init")
        run_git(self.repo, "config", "user.email", "taskmgr@example.com")
        run_git(self.repo, "config", "user.name", "Task Manager")
        # 固定换行行为，避免全局 autocrlf 干扰指纹的换行归一口径
        run_git(self.repo, "config", "core.autocrlf", "false")
        # 运行时目录（state.json / events.jsonl / leases.json）不入库：
        # 否则它们作为未跟踪文件混入 touched 清单，污染证据指纹
        self.dirty_file(".gitignore", b".glm-conductor/\n")
        self.dirty_file("base.txt", b"v1\n")
        run_git(self.repo, "add", ".")
        run_git(self.repo, "commit", "-m", "init")

    def _force_cleanup(self):
        """解除 git 只读对象后清理临时目录（Windows 上 git 松散对象文件
        带只读属性，TemporaryDirectory.cleanup 的 rmtree 会
        PermissionError；先遍历 .git 清掉只读位再删除，模式复用
        test_reconcile / test_stop_gate）。"""
        git_dir = os.path.join(self.root, ".git")
        if os.path.isdir(git_dir):
            for dirpath, _dirnames, filenames in os.walk(git_dir):
                for name in filenames:
                    try:
                        os.chmod(os.path.join(dirpath, name), stat.S_IWRITE)
                    except OSError:
                        pass
        self._tmp.cleanup()

    def dirty_file(self, rel, data):
        """在 fixture 仓库内写 / 改一个文件（自动建父目录）——制造工作区
        改动（touched），即验证证据指纹的绑定对象。"""
        target = self.repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return rel

    def unit_fingerprint(self, unit, *, touched=None):
        """按完成门同一口径真算单元当前证据指纹（对 owned_hits）：
        git_touched_files → classify_paths → compute_fingerprint。"""
        if touched is None:
            touched = ownership.git_touched_files(self.root)
        owned_hits, _ = ownership.classify_paths(touched, unit["ownership"])
        return fingerprint_mod.compute_fingerprint(self.root, owned_hits)

    def record_evidence(self, unit, command=None, *, fp=None, status="pass",
                        uid=None):
        """为单元写一条单元级验证证据（record_unit_verification 缺省
        通道）。测试只经本助手造证据，保证指纹口径与完成门一致：
        指纹缺省按当前工作区真算（unit_fingerprint）。"""
        if command is None:
            command = unit["verification"][0]
        if fp is None:
            fp = self.unit_fingerprint(unit)
        return task_manager.record_unit_verification(
            self.root, TID, unit["id"] if uid is None else uid,
            command, fp, status=status)


# —— prepare happy path ——

class PrepareHappyTest(TaskManagerTestBase):

    def setUp(self):
        super().setUp()
        make_task(self.root, [wu("u1", ("src/a/**",))])

    def test_prepare_returns_plan_and_acquires_lease(self):
        plan = task_manager.prepare_dispatch(self.root, TID, "u1")
        self.assertEqual(plan["dispatch"], ["u1"])
        self.assertEqual(plan["max_workers"], 1)
        self.assertEqual(plan["quota_status"], "AVAILABLE")
        leases = lease.lease_state(self.root, TID)
        self.assertEqual(set(leases), {"src/a/**"})
        self.assertEqual(leases["src/a/**"]["owner"], "u1")

    def test_prepare_journals_dispatch_prepared_with_normalized_paths(self):
        task_manager.prepare_dispatch(self.root, TID, "u1")
        records = events(self.root, "dispatch_prepared")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["unit"], "u1")
        self.assertEqual(records[0]["leased"], ["src/a/**"])

    def test_prepare_leaves_unit_status_and_state_bytes_untouched(self):
        before = state_bytes(self.root)
        task_manager.prepare_dispatch(self.root, TID, "u1")
        # prepare 对 state.json 零副作用（不落盘、不改单元状态）
        self.assertEqual(state_bytes(self.root), before)
        st = state.load_state(self.root, TID)
        self.assertEqual(unit_of(st, "u1")["status"], "ready")

    def test_prepare_normalizes_backslash_ownership(self):
        make_task(self.root, [wu("u2", ("src\\b\\main.ts",))])
        task_manager.prepare_dispatch(self.root, TID, "u2")
        record = events(self.root, "dispatch_prepared")[0]
        self.assertEqual(record["leased"], ["src/b/main.ts"])
        self.assertEqual(set(lease.lease_state(self.root, TID)),
                         {"src/b/main.ts"})

    def test_repeated_prepare_is_idempotent_on_leases(self):
        # §78 同 owner 幂等：崩溃后重备安全，不产生重复租约条目
        task_manager.prepare_dispatch(self.root, TID, "u1")
        task_manager.prepare_dispatch(self.root, TID, "u1")
        self.assertEqual(set(lease.lease_state(self.root, TID)),
                         {"src/a/**"})
        self.assertEqual(len(events(self.root, "dispatch_prepared")), 2)

    def test_max_workers_defaults_from_state_and_param_overrides(self):
        make_task(self.root, [wu("u1", ("src/a/**",)),
                              wu("u2", ("src/b/**",))],
                  max_workers=2)
        plan = task_manager.prepare_dispatch(self.root, TID, "u1")
        self.assertEqual(plan["max_workers"], 2)
        self.assertEqual(sorted(plan["dispatch"]), ["u1", "u2"])
        plan = task_manager.prepare_dispatch(self.root, TID, "u1",
                                             max_workers=1)
        self.assertEqual(plan["max_workers"], 1)
        self.assertEqual(plan["dispatch"], ["u1"])


# —— prepare 失败（消息断言 + 失败零副作用） ——

class PrepareFailureTest(TaskManagerTestBase):

    def test_unit_not_found_raises(self):
        make_task(self.root, [wu("u1")])
        with self.assertRaises(task_manager.TaskManagerError) as ctx:
            task_manager.prepare_dispatch(self.root, TID, "ghost")
        self.assertIn("ghost", str(ctx.exception))

    def test_unit_not_ready_raises_with_current_status(self):
        make_task(self.root, [wu("u1", status="running")])
        with self.assertRaises(task_manager.TaskManagerError) as ctx:
            task_manager.prepare_dispatch(self.root, TID, "u1")
        self.assertIn("u1", str(ctx.exception))
        self.assertIn("running", str(ctx.exception))
        # 失败零副作用：不写租约、不写事件
        self.assertEqual(lease.lease_state(self.root, TID), {})
        self.assertEqual(journal.read_events(self.root, TID), [])

    def test_quota_exhausted_rejected_with_reason(self):
        make_task(self.root, [wu("u1")])
        with self.assertRaises(task_manager.TaskManagerError) as ctx:
            task_manager.prepare_dispatch(self.root, TID, "u1",
                                          quota_status="EXHAUSTED")
        self.assertIn("u1", str(ctx.exception))
        self.assertIn("quota EXHAUSTED", str(ctx.exception))
        # 决策未批准发生在 acquire 之前 → 零租约零事件
        self.assertEqual(lease.lease_state(self.root, TID), {})
        self.assertEqual(journal.read_events(self.root, TID), [])

    def test_deferred_reason_surfaced(self):
        make_task(self.root, [wu("u1")])
        # PRESSURE 默认整批抑制 → deferred("pressure_suppressed")
        with self.assertRaises(task_manager.TaskManagerError) as ctx:
            task_manager.prepare_dispatch(self.root, TID, "u1",
                                          quota_status="PRESSURE")
        self.assertIn("pressure_suppressed", str(ctx.exception))
        self.assertEqual(lease.lease_state(self.root, TID), {})

    def test_unmet_dependencies_not_candidate(self):
        make_task(self.root, [wu("dep", status="pending"),
                              wu("u1", ("src/a/**",), deps=("dep",))])
        # u1 status ready 但依赖未 completed → 不进任何决策组 → fallback 理由
        with self.assertRaises(task_manager.TaskManagerError) as ctx:
            task_manager.prepare_dispatch(self.root, TID, "u1")
        self.assertIn("候选", str(ctx.exception))
        self.assertEqual(lease.lease_state(self.root, TID), {})

    def test_invalid_quota_status_value_error_propagates(self):
        # plan_dispatch 的参数校验 ValueError 不被吞
        make_task(self.root, [wu("u1")])
        with self.assertRaises(ValueError):
            task_manager.prepare_dispatch(self.root, TID, "u1",
                                          quota_status="MEGA")


# —— commit happy path ——

class CommitHappyTest(TaskManagerTestBase):

    def test_commit_transitions_books_active_and_flips_task_status(self):
        make_task(self.root, [wu("u1")], status="decomposed")
        task_manager.prepare_dispatch(self.root, TID, "u1")
        st = task_manager.commit_dispatch(self.root, TID, "u1")
        self.assertEqual(unit_of(st, "u1")["status"], "running")
        self.assertEqual(st["dispatch"]["active"], ["u1"])
        self.assertEqual(st["status"], "executing")
        # 落盘读回一致（单次 save 已持久化全部三项）
        reloaded = state.load_state(self.root, TID)
        self.assertEqual(unit_of(reloaded, "u1")["status"], "running")
        self.assertEqual(reloaded["dispatch"]["active"], ["u1"])
        self.assertEqual(reloaded["status"], "executing")

    def test_commit_journals_implementation_started_with_executor(self):
        make_task(self.root, [wu("u1")])
        task_manager.prepare_dispatch(self.root, TID, "u1")
        task_manager.commit_dispatch(self.root, TID, "u1")
        records = events(self.root, "implementation_started")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["unit"], "u1")
        self.assertEqual(records[0]["executor"], "flash-implementer")

    def test_commit_from_created_status_flips_to_executing(self):
        make_task(self.root, [wu("u1")], status="created")
        task_manager.prepare_dispatch(self.root, TID, "u1")
        st = task_manager.commit_dispatch(self.root, TID, "u1")
        self.assertEqual(st["status"], "executing")

    def test_commit_keeps_executing_status(self):
        make_task(self.root, [wu("u1")], status="executing")
        task_manager.prepare_dispatch(self.root, TID, "u1")
        st = task_manager.commit_dispatch(self.root, TID, "u1")
        self.assertEqual(st["status"], "executing")

    def test_commit_keeps_joining_status(self):
        # joining 已是执行态：不翻动（只处理四种非执行态）
        make_task(self.root, [wu("u1")], status="joining")
        task_manager.prepare_dispatch(self.root, TID, "u1")
        st = task_manager.commit_dispatch(self.root, TID, "u1")
        self.assertEqual(st["status"], "joining")

    def test_commit_active_append_is_idempotent(self):
        make_task(self.root, [wu("u1")])
        task_manager.prepare_dispatch(self.root, TID, "u1")
        # 手工制造「已在 active」的中间态（异常残留）→ commit 不重复追加
        st = state.load_state(self.root, TID)
        st["dispatch"]["active"] = ["u1", "ghost-x"]
        state.save_state(self.root, st)
        st = task_manager.commit_dispatch(self.root, TID, "u1")
        self.assertEqual(st["dispatch"]["active"], ["u1", "ghost-x"])


# —— commit 失败 ——

class CommitFailureTest(TaskManagerTestBase):

    def test_commit_without_prepare_reports_missing_lease(self):
        make_task(self.root, [wu("u1")])
        with self.assertRaises(task_manager.TaskManagerError) as ctx:
            task_manager.commit_dispatch(self.root, TID, "u1")
        self.assertIn("u1", str(ctx.exception))
        self.assertIn("prepare_dispatch", str(ctx.exception))
        # 单元保持 ready，state 未被写脏
        st = state.load_state(self.root, TID)
        self.assertEqual(unit_of(st, "u1")["status"], "ready")
        self.assertEqual(st["dispatch"]["active"], [])
        self.assertEqual(journal.read_events(self.root, TID), [])

    def test_commit_with_lost_lease_suggests_abort(self):
        make_task(self.root, [wu("u1")])
        task_manager.prepare_dispatch(self.root, TID, "u1")
        lease.release_lease(self.root, TID, "u1")  # 模拟租约外部丢失
        with self.assertRaises(task_manager.TaskManagerError) as ctx:
            task_manager.commit_dispatch(self.root, TID, "u1")
        self.assertIn("abort_dispatch", str(ctx.exception))

    def test_commit_with_foreign_lease_owner_rejected(self):
        make_task(self.root, [wu("u1")])
        lease.acquire_lease(self.root, TID, "someone-else", ["src/a/**"])
        with self.assertRaises(task_manager.TaskManagerError):
            task_manager.commit_dispatch(self.root, TID, "u1")

    def test_duplicate_commit_rejected(self):
        make_task(self.root, [wu("u1")])
        task_manager.prepare_dispatch(self.root, TID, "u1")
        task_manager.commit_dispatch(self.root, TID, "u1")
        with self.assertRaises(task_manager.TaskManagerError) as ctx:
            task_manager.commit_dispatch(self.root, TID, "u1")
        self.assertIn("running", str(ctx.exception))


# —— abort ——

class AbortTest(TaskManagerTestBase):

    def test_running_unit_cannot_abort(self):
        make_task(self.root, [wu("u1")])
        task_manager.prepare_dispatch(self.root, TID, "u1")
        task_manager.commit_dispatch(self.root, TID, "u1")
        with self.assertRaises(task_manager.TaskManagerError) as ctx:
            task_manager.abort_dispatch(self.root, TID, "u1")
        self.assertIn("running", str(ctx.exception))
        # 已提交单元的租约未被动
        self.assertEqual(lease.held_by(self.root, TID, "u1"), ["src/a/**"])

    def test_abort_releases_lease_journals_and_spares_state(self):
        make_task(self.root, [wu("u1")])
        task_manager.prepare_dispatch(self.root, TID, "u1")
        before = state_bytes(self.root)
        st = task_manager.abort_dispatch(self.root, TID, "u1")
        self.assertEqual(lease.lease_state(self.root, TID), {})
        records = events(self.root, "dispatch_aborted")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["unit"], "u1")
        # 单元保持 ready；无 active 残留 → state.json 零写入（常态）
        self.assertEqual(state_bytes(self.root), before)
        self.assertEqual(unit_of(st, "u1")["status"], "ready")

    def test_abort_cleans_active_residue_and_saves(self):
        make_task(self.root, [wu("u1")])
        task_manager.prepare_dispatch(self.root, TID, "u1")
        # 手工制造异常残留：active 里出现 u1（正常流程 commit 才记账）
        st = state.load_state(self.root, TID)
        st["dispatch"]["active"] = ["u1"]
        state.save_state(self.root, st)
        task_manager.abort_dispatch(self.root, TID, "u1")
        reloaded = state.load_state(self.root, TID)
        self.assertEqual(reloaded["dispatch"]["active"], [])
        self.assertEqual(lease.lease_state(self.root, TID), {})
        self.assertEqual(len(events(self.root, "dispatch_aborted")), 1)


# —— finish（failed / cancelled / 校验序；零 git 夹具） ——

class FinishTest(TaskManagerTestBase):
    """finish_unit 的零 git 子集：completed 收尾被 RB-1 完成证据门要求
    真实证据（git fixture），因此 completed 路径用例全部归入
    FinishGateCompletionTest / FinishGateRejectionTest；本类只覆盖
    failed / cancelled 收尾与参数 / 状态校验序（均不过完成门）。"""

    def _run_to_running(self):
        """prepare + commit 到 running（带租约与 active 记账）。"""
        make_task(self.root, [wu("u1")])
        task_manager.prepare_dispatch(self.root, TID, "u1")
        task_manager.commit_dispatch(self.root, TID, "u1")

    def test_running_to_failed_single_step(self):
        self._run_to_running()
        st = task_manager.finish_unit(self.root, TID, "u1",
                                      outcome="failed")
        self.assertEqual(unit_of(st, "u1")["status"], "failed")
        reloaded = state.load_state(self.root, TID)
        self.assertEqual(reloaded["dispatch"]["active"], [])
        self.assertEqual(lease.lease_state(self.root, TID), {})
        self.assertEqual(events(self.root, "unit_finished")[0]["outcome"],
                         "failed")

    def test_verifying_to_cancelled(self):
        make_task(self.root, [wu("u1", status="verifying")], active=["u1"])
        st = task_manager.finish_unit(self.root, TID, "u1",
                                      outcome="cancelled")
        self.assertEqual(unit_of(st, "u1")["status"], "cancelled")
        self.assertEqual(st["dispatch"]["active"], [])
        self.assertEqual(events(self.root, "unit_finished")[0]["outcome"],
                         "cancelled")

    def test_invalid_outcome_raises_value_error_before_io(self):
        make_task(self.root, [wu("u1", status="running")])
        for bad in ("shipped", "VERIFIED", None):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    task_manager.finish_unit(self.root, TID, "u1",
                                             outcome=bad)
        # 校验先于任何 I/O：零事件、状态未动
        self.assertEqual(journal.read_events(self.root, TID), [])
        st = state.load_state(self.root, TID)
        self.assertEqual(unit_of(st, "u1")["status"], "running")

    def test_finish_requires_running_or_verifying(self):
        make_task(self.root, [wu("u1", status="ready")])
        with self.assertRaises(task_manager.TaskManagerError) as ctx:
            task_manager.finish_unit(self.root, TID, "u1")
        self.assertIn("ready", str(ctx.exception))

    def test_finish_unit_not_found(self):
        make_task(self.root, [wu("u1", status="running")])
        with self.assertRaises(task_manager.TaskManagerError):
            task_manager.finish_unit(self.root, TID, "ghost")


# —— 崩溃窗口模拟（文档化恢复映射两条） ——

class CrashWindowTest(TaskManagerTestBase):

    def test_crash_after_prepare_then_commit_succeeds(self):
        make_task(self.root, [wu("u1")])
        task_manager.prepare_dispatch(self.root, TID, "u1")
        # —— 崩溃点：单元仍 ready + 租约在位 + 无 running/active ——
        st = state.load_state(self.root, TID)
        self.assertEqual(unit_of(st, "u1")["status"], "ready")
        self.assertEqual(st["dispatch"]["active"], [])
        self.assertEqual(lease.held_by(self.root, TID, "u1"), ["src/a/**"])
        # 恢复路径一：直接 commit 完成提交（自有租约不挡重派）
        st = task_manager.commit_dispatch(self.root, TID, "u1")
        self.assertEqual(unit_of(st, "u1")["status"], "running")
        self.assertEqual(st["dispatch"]["active"], ["u1"])
        names = [e["event"] for e in journal.read_events(self.root, TID)]
        self.assertEqual(names, ["dispatch_prepared",
                                 "implementation_started"])

    def test_crash_after_prepare_then_abort_releases(self):
        make_task(self.root, [wu("u1")])
        task_manager.prepare_dispatch(self.root, TID, "u1")
        # 恢复路径二：abort 回退——租约清空、单元仍 ready、事件在案
        task_manager.abort_dispatch(self.root, TID, "u1")
        self.assertEqual(lease.lease_state(self.root, TID), {})
        st = state.load_state(self.root, TID)
        self.assertEqual(unit_of(st, "u1")["status"], "ready")
        self.assertEqual(len(events(self.root, "dispatch_aborted")), 1)
        # 回退后可重新 prepare（全有或全无重来一遍）
        task_manager.prepare_dispatch(self.root, TID, "u1")
        self.assertEqual(lease.held_by(self.root, TID, "u1"), ["src/a/**"])


# —— 事件词汇（RECOMMENDED_EVENTS 增补三条 + 位置锁定） ——

class JournalVocabularyTest(unittest.TestCase):

    def test_new_events_in_recommended_vocabulary(self):
        names = journal.RECOMMENDED_EVENTS
        for name in ("dispatch_prepared", "dispatch_aborted",
                     "unit_finished"):
            self.assertIn(name, names)
        # 位置：prepared / aborted 紧随 implementation_started；
        # unit_finished 在 checkpoint_written 附近（之前）
        self.assertLess(names.index("implementation_started"),
                        names.index("dispatch_prepared"))
        self.assertLess(names.index("dispatch_prepared"),
                        names.index("dispatch_aborted"))
        self.assertLess(names.index("unit_finished"),
                        names.index("checkpoint_written"))


# —— H5：prepare 默认 TTL / recover_leases / 端到端崩溃场景 ——

def _write_lease_map(root, mapping):
    """按落盘形态手工改写 leases.json（构造过期 / 指定 TTL 记录用）。"""
    path = lease.lease_path(root, TID)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(mapping, ensure_ascii=False, indent=2,
                            sort_keys=True))


def _read_lease_map(root):
    with open(lease.lease_path(root, TID), "r", encoding="utf-8") as fh:
        return json.load(fh)


class PrepareTtlTest(TaskManagerTestBase):
    """prepare_dispatch 的保守 TTL 默认（H5）。"""

    def test_prepare_acquires_with_default_ttl_and_journals_it(self):
        make_task(self.root, [wu("u1")])
        task_manager.prepare_dispatch(self.root, TID, "u1")
        record = lease.lease_state(self.root, TID)["src/a/**"]
        # H5 record 形状：TTL + generation + 心跳 + 会话标识
        self.assertIn("expires_at", record)
        self.assertEqual(record["generation"], 1)
        self.assertIsNone(record["session_id"])
        self.assertEqual(record["heartbeat_at"], record["acquired_at"])
        span = (lease._parse_iso(record["expires_at"])
                - lease._parse_iso(record["acquired_at"])).total_seconds()
        self.assertEqual(span, float(lease.LEASE_DEFAULT_TTL_SECONDS))
        # dispatch_prepared 事件补 ttl_seconds 字段
        prepared = events(self.root, "dispatch_prepared")[0]
        self.assertEqual(prepared["ttl_seconds"],
                         lease.LEASE_DEFAULT_TTL_SECONDS)

    def test_reprepare_keeps_unexpired_record(self):
        # 同 owner 未过期幂等跳过：generation 与 acquired_at 不动
        make_task(self.root, [wu("u1")])
        task_manager.prepare_dispatch(self.root, TID, "u1")
        before = _read_lease_map(self.root)["src/a/**"]
        task_manager.prepare_dispatch(self.root, TID, "u1")
        self.assertEqual(_read_lease_map(self.root)["src/a/**"], before)
        self.assertEqual(before["generation"], 1)


class RecoverLeasesTest(TaskManagerTestBase):

    def _expire_paths(self, paths):
        """把指定路径的租约记录拨到已过期（其余保持未过期）。"""
        mapping = _read_lease_map(self.root)
        for path in paths:
            mapping[path]["expires_at"] = LEASE_PAST
        _write_lease_map(self.root, mapping)

    def test_recover_releases_stale_and_journals(self):
        # 单元 completed（非活跃写相）仍持租约 → stale → 自动释放
        make_task(self.root, [wu("u1", status="completed")])
        lease.acquire_lease(self.root, TID, "u1", ["src/a/**"])
        report = task_manager.recover_leases(self.root, TID)
        self.assertEqual(report["released"], ["src/a/**"])
        self.assertEqual([item["path"] for item in report["stale"]],
                         ["src/a/**"])
        self.assertEqual(report["expired_running"], [])
        self.assertEqual(lease.lease_state(self.root, TID), {})
        record = events(self.root, "lease_recovered")[0]
        self.assertEqual(record["released"], ["src/a/**"])
        self.assertEqual(record["kept_expired_running"], [])

    def test_recover_keeps_active_and_expired_running(self):
        make_task(self.root, [wu("u1", ("src/a/**",), status="running"),
                              wu("u2", ("src/b/**",), status="running")])
        lease.acquire_lease(self.root, TID, "u1", ["src/a/**"],
                            ttl_seconds=3600)
        lease.acquire_lease(self.root, TID, "u2", ["src/b/**"],
                            ttl_seconds=3600)
        self._expire_paths(["src/b/**"])
        report = task_manager.recover_leases(self.root, TID)
        # active 保留；expired_running（worker 可能仍在写）不动、仅上报
        self.assertEqual([item["path"] for item in report["active"]],
                         ["src/a/**"])
        self.assertEqual([item["path"] for item in
                          report["expired_running"]], ["src/b/**"])
        # released 为空 → 不落 journal（只读对账不留噪声行）
        self.assertEqual(report["released"], [])
        self.assertEqual(journal.read_events(self.root, TID), [])
        # 两条租约都未被 recover 动过
        self.assertEqual(set(lease.lease_state(self.root, TID)),
                         {"src/a/**", "src/b/**"})

    def test_recover_mixed_releases_stale_keeps_expired_running(self):
        make_task(self.root, [wu("u1", ("src/a/**",), status="failed"),
                              wu("u2", ("src/b/**",), status="running")])
        lease.acquire_lease(self.root, TID, "u1", ["src/a/**"],
                            ttl_seconds=3600)
        lease.acquire_lease(self.root, TID, "u2", ["src/b/**"],
                            ttl_seconds=3600)
        self._expire_paths(["src/a/**", "src/b/**"])
        report = task_manager.recover_leases(self.root, TID)
        self.assertEqual(report["released"], ["src/a/**"])
        self.assertEqual(lease.lease_state(self.root, TID),
                         {"src/b/**": lease.lease_state(
                             self.root, TID)["src/b/**"]})
        record = events(self.root, "lease_recovered")[0]
        self.assertEqual(record["released"], ["src/a/**"])
        self.assertEqual(record["kept_expired_running"], ["src/b/**"])

    def test_recover_requires_task_state(self):
        with self.assertRaises(task_manager.TaskManagerError):
            task_manager.recover_leases(self.root, TID)


class OrphanLeaseRecoveryTest(TaskManagerTestBase):
    """端到端崩溃场景（appendix B：
    orphan_lease_reconciled_after_interrupted_running_unit）。"""

    def test_orphan_lease_reconciled_after_interrupted_running_unit(self):
        make_task(self.root, [wu("u1")])
        task_manager.prepare_dispatch(self.root, TID, "u1")
        task_manager.commit_dispatch(self.root, TID, "u1")
        # —— 崩溃：单元 running + 租约在位；无验证证据、工作区干净 ——
        st = state.load_state(self.root, TID)
        self.assertEqual(unit_of(st, "u1")["status"], "running")
        self.assertEqual(lease.held_by(self.root, TID, "u1"),
                         ["src/a/**"])
        # §69 对账（注入 touched=[] 干净工作区 / events=[] 无验证证据）
        report = reconcile.reconcile_interrupted(
            self.root, TID, st["work_units"], touched=[], events=[])
        self.assertEqual(report["suggestions"]["u1"]["to"], "ready")
        # 应用建议（主会话侧：只对 suggestions 应用 transition）
        work_unit.transition_work_unit(unit_of(st, "u1"), "ready")
        state.save_state(self.root, st)
        # 单元已回 ready（非活跃写相）→ 租约成孤儿 → recover 闭环清理
        recovered = task_manager.recover_leases(self.root, TID)
        self.assertEqual(recovered["released"], ["src/a/**"])
        self.assertEqual(lease.lease_state(self.root, TID), {})
        record = events(self.root, "lease_recovered")[0]
        self.assertEqual(record["released"], ["src/a/**"])
        self.assertEqual(record["kept_expired_running"], [])


# —— H6：单元级验证证据写入口（record_unit_verification） ——

class RecordUnitVerificationTest(TaskManagerTestBase):

    def test_writes_fully_bound_verification_event(self):
        event = task_manager.record_unit_verification(
            self.root, TID, "u1", VERIFY_CMD, "deadbeef", status="pass")
        # 事件形状逐字段锚定（ts 由 append_event 管理）
        self.assertEqual(event["event"], "verification")
        self.assertEqual(event["unit"], "u1")
        self.assertEqual(event["command"], VERIFY_CMD)
        self.assertEqual(event["status"], "pass")
        self.assertEqual(event["fingerprint"], "deadbeef")
        self.assertIn("ts", event)
        # 落盘读回一致
        self.assertEqual(events(self.root, "verification"), [event])

    def test_fail_status_is_legal_vocabulary(self):
        # "fail" 允许写入留痕（但不构成完成证据——reconcile 只认 pass）
        event = task_manager.record_unit_verification(
            self.root, TID, "u1", VERIFY_CMD, "deadbeef", status="fail")
        self.assertEqual(event["status"], "fail")

    def test_empty_or_illegal_arguments_raise_value_error_with_field(self):
        for field, args in (
                ("uid", ("", VERIFY_CMD, "fp")),
                ("uid", (None, VERIFY_CMD, "fp")),
                ("command", ("u1", "", "fp")),
                ("command", ("u1", 42, "fp")),
                ("fingerprint", ("u1", VERIFY_CMD, "")),
                ("fingerprint", ("u1", VERIFY_CMD, None))):
            with self.subTest(field=field):
                with self.assertRaises(ValueError) as ctx:
                    task_manager.record_unit_verification(
                        self.root, TID, *args)
                self.assertIn(field, str(ctx.exception))

    def test_illegal_status_raises_value_error(self):
        for bad in ("PASS", "verified", "", None, 1):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError) as ctx:
                    task_manager.record_unit_verification(
                        self.root, TID, "u1", VERIFY_CMD, "fp",
                        status=bad)
                self.assertIn("status", str(ctx.exception))

    # prepare→commit→record_unit_verification→finish_unit 全流程用例
    # （原 :729）已迁入 FinishGateCompletionTest：RB-1 完成证据门起
    # completed 收尾要求绑定当前指纹的新鲜证据，假指纹（"cafebabe"）
    # 与零 git 夹具不再构成合法完成路径——改用 git fixture + 真实指纹。


# —— RB-1 完成证据门：拒绝路径（计划 §2.5 A1-A12，git fixture） ——

@unittest.skipUnless(shutil.which("git"),
                     "环境无 git 可执行，跳过 git fixture 测试")
class FinishGateRejectionTest(GitRepoFixture):
    """finish_unit(completed) 的 RB-1 完成门拒绝矩阵：missing / fail /
    wrong-unit / 非 required command / partial / stale / legacy 证据一律
    TaskManagerError；拒绝零副作用（state.json 字节 / 租约 /
    dispatch.active / journal 全部不变，也不写拒绝事件——计划 §2.3.4）。"""

    def _run_to_running(self, **wu_kwargs):
        """make_task + prepare + commit 到 running，返回构造的 unit dict。"""
        unit = wu("u1", **wu_kwargs)
        make_task(self.root, [unit])
        task_manager.prepare_dispatch(self.root, TID, "u1")
        task_manager.commit_dispatch(self.root, TID, "u1")
        return unit

    def test_running_completed_without_evidence_rejected(self):
        # A1：有 required 命令、无任何 verification 事件 → 拒绝
        self._run_to_running()
        with self.assertRaises(task_manager.TaskManagerError) as ctx:
            task_manager.finish_unit(self.root, TID, "u1")
        message = str(ctx.exception)
        self.assertIn("finish_unit", message)
        self.assertIn("u1", message)
        self.assertIn(VERIFY_CMD, message)  # missing 清单逐条可见
        self.assertIn("record_unit_verification", message)  # 补证指引
        self.assertIn("record → finish_unit → commit", message)

    def test_verifying_completed_without_evidence_rejected(self):
        # A2：verifying 态同样要过证据门
        make_task(self.root, [wu("u1", status="verifying")], active=["u1"])
        with self.assertRaises(task_manager.TaskManagerError) as ctx:
            task_manager.finish_unit(self.root, TID, "u1")
        self.assertIn(VERIFY_CMD, str(ctx.exception))

    def test_fail_status_evidence_rejected(self):
        # A3：status="fail" 允许留痕但不构成完成证据
        unit = self._run_to_running()
        self.dirty_file("src/a/feature.ts", b"feat-v1\n")
        self.record_evidence(unit, status="fail")
        with self.assertRaises(task_manager.TaskManagerError) as ctx:
            task_manager.finish_unit(self.root, TID, "u1")
        self.assertIn(VERIFY_CMD, str(ctx.exception))

    def test_other_unit_evidence_rejected(self):
        # A4：事件 unit="other-unit" → 归属绑定不匹配 → 按无证据处理
        unit = self._run_to_running()
        self.dirty_file("src/a/feature.ts", b"feat-v1\n")
        self.record_evidence(unit, uid="other-unit")
        with self.assertRaises(task_manager.TaskManagerError) as ctx:
            task_manager.finish_unit(self.root, TID, "u1")
        self.assertIn(VERIFY_CMD, str(ctx.exception))

    def test_non_required_command_evidence_rejected(self):
        # A5：command 不在 unit.verification 内 → 不算证据
        unit = self._run_to_running()
        self.dirty_file("src/a/feature.ts", b"feat-v1\n")
        self.record_evidence(unit, command="echo not-a-required-command")
        with self.assertRaises(task_manager.TaskManagerError) as ctx:
            task_manager.finish_unit(self.root, TID, "u1")
        self.assertIn(VERIFY_CMD, str(ctx.exception))

    def test_partial_evidence_rejected_with_exact_missing(self):
        # A6：两条 required 只验证一条 → missing 恰为未验证那条（all-match）
        cmd_b = "python3 -m unittest tests.test_other"
        unit = self._run_to_running(verification=(VERIFY_CMD, cmd_b))
        self.dirty_file("src/a/feature.ts", b"feat-v1\n")
        self.record_evidence(unit, command=VERIFY_CMD)  # 只验证第一条
        with self.assertRaises(task_manager.TaskManagerError) as ctx:
            task_manager.finish_unit(self.root, TID, "u1")
        message = str(ctx.exception)
        self.assertIn("missing：%s）" % cmd_b, message)
        self.assertNotIn(VERIFY_CMD, message)  # 已验证那条不在 missing 内

    def test_stale_fingerprint_evidence_rejected(self):
        # A7：record 后再改 owned 文件 → 指纹漂移 → 证据 stale → 拒绝
        unit = self._run_to_running()
        self.dirty_file("src/a/feature.ts", b"feat-v1\n")
        self.record_evidence(unit)
        self.dirty_file("src/a/feature.ts", b"feat-v2\n")
        with self.assertRaises(task_manager.TaskManagerError) as ctx:
            task_manager.finish_unit(self.root, TID, "u1")
        self.assertIn(VERIFY_CMD, str(ctx.exception))

    def test_legacy_event_without_unit_field_rejected(self):
        # A8：legacy 无 unit 字段事件（H6 起不再采信）→ 按无证据处理
        unit = self._run_to_running()
        self.dirty_file("src/a/feature.ts", b"feat-v1\n")
        journal.append_event(self.root, TID, {
            "event": "verification", "command": VERIFY_CMD,
            "status": "pass",
            "fingerprint": self.unit_fingerprint(unit)})
        with self.assertRaises(task_manager.TaskManagerError) as ctx:
            task_manager.finish_unit(self.root, TID, "u1")
        self.assertIn(VERIFY_CMD, str(ctx.exception))

    def test_rejection_leaves_state_lease_active_journal_untouched(self):
        # A9-A12 合并：拒绝零副作用四连断言 + 单元状态不变
        self._run_to_running()
        self.dirty_file("src/a/feature.ts", b"feat-v1\n")  # 有残留无证据
        bytes_before = state_bytes(self.root)
        lease_before = lease.lease_state(self.root, TID)
        active_before = state.load_state(
            self.root, TID)["dispatch"]["active"]
        journal_before = journal.read_events(self.root, TID)
        with self.assertRaises(task_manager.TaskManagerError):
            task_manager.finish_unit(self.root, TID, "u1")
        self.assertEqual(state_bytes(self.root), bytes_before)
        self.assertEqual(lease.lease_state(self.root, TID), lease_before)
        self.assertEqual(state.load_state(
            self.root, TID)["dispatch"]["active"], active_before)
        self.assertEqual(journal.read_events(self.root, TID),
                         journal_before)
        self.assertEqual(events(self.root, "unit_finished"), [])
        st = state.load_state(self.root, TID)
        self.assertEqual(unit_of(st, "u1")["status"], "running")


# —— RB-1 完成证据门：fail-closed（非 git 目录，零 git 夹具） ——

class FinishGateFailClosedTest(TaskManagerTestBase):
    """非 git 目录 + 带 required 验证命令的单元：git / 指纹失败使证据
    新鲜性不可判定 → 包装为 TaskManagerError fail-closed 拒绝完成。"""

    def test_non_git_repo_with_required_commands_fails_closed(self):
        # A12b：零 git 夹具即可——tempdir 未 git init
        make_task(self.root, [wu("u1")])
        task_manager.prepare_dispatch(self.root, TID, "u1")
        task_manager.commit_dispatch(self.root, TID, "u1")
        with self.assertRaises(task_manager.TaskManagerError) as ctx:
            task_manager.finish_unit(self.root, TID, "u1")
        message = str(ctx.exception)
        self.assertIn("fail-closed", message)
        self.assertIn("git", message)
        # 零副作用：无 unit_finished，单元仍 running
        self.assertEqual(events(self.root, "unit_finished"), [])
        st = state.load_state(self.root, TID)
        self.assertEqual(unit_of(st, "u1")["status"], "running")


# —— RB-1 完成证据门：通过路径（计划 §2.5 B13-B19，git fixture） ——

@unittest.skipUnless(shutil.which("git"),
                     "环境无 git 可执行，跳过 git fixture 测试")
class FinishGateCompletionTest(GitRepoFixture):
    """RB-1 完成门通过路径：真实指纹（compute_fingerprint 对 owned_hits
    真算）+ 新鲜 pass 证据 → 正常收尾（转换 / 释放 / 记账 / 事件照旧）。"""

    def _prepared(self, unit=None):
        """make_task + prepare + commit 到 running，返回 unit dict。"""
        unit = unit or wu("u1")
        make_task(self.root, [unit])
        task_manager.prepare_dispatch(self.root, TID, "u1")
        task_manager.commit_dispatch(self.root, TID, "u1")
        return unit

    def _verifying_with_lease(self):
        """verifying 态 + 租约 + active 记账 + 当前改动 + 新鲜证据。"""
        unit = wu("u1", status="verifying")
        make_task(self.root, [unit], active=["u1"])
        lease.acquire_lease(self.root, TID, "u1", ["src/a/**"])
        self.dirty_file("src/a/feature.ts", b"feat-v1\n")
        self.record_evidence(unit)
        return unit

    def test_single_required_fresh_evidence_completes_with_sequence(self):
        # B13 + D23（原 :729 迁移）：真实指纹证据 → completed +
        # 全生命周期事件序（verification 夹在派发与收尾之间）
        unit = self._prepared()
        self.dirty_file("src/a/feature.ts", b"feat-v1\n")
        fp = self.unit_fingerprint(unit)
        task_manager.record_unit_verification(
            self.root, TID, "u1", VERIFY_CMD, fp)
        st = task_manager.finish_unit(self.root, TID, "u1")
        self.assertEqual(unit_of(st, "u1")["status"], "completed")
        record = events(self.root, "verification")[0]
        self.assertEqual(record["unit"], "u1")
        self.assertEqual(record["command"], VERIFY_CMD)
        self.assertEqual(record["status"], "pass")
        self.assertEqual(record["fingerprint"], fp)
        names = [e["event"] for e in journal.read_events(self.root, TID)]
        self.assertEqual(names, ["dispatch_prepared",
                                 "implementation_started",
                                 "verification", "unit_finished"])

    def test_all_required_commands_evidenced_completes(self):
        # B14：多 required 全部有新鲜证据 → completed（all-match 满足）
        cmd_b = "python3 -m unittest tests.test_other"
        unit = self._prepared(
            wu("u1", verification=(VERIFY_CMD, cmd_b)))
        self.dirty_file("src/a/feature.ts", b"feat-v1\n")
        self.record_evidence(unit, command=VERIFY_CMD)
        self.record_evidence(unit, command=cmd_b)
        st = task_manager.finish_unit(self.root, TID, "u1")
        self.assertEqual(unit_of(st, "u1")["status"], "completed")

    def test_running_with_full_evidence_still_goes_through_verifying(self):
        # B15（原 FinishTest spy 用例迁移）：证据门通过不改变 §70 双跳
        unit = self._prepared()
        self.dirty_file("src/a/feature.ts", b"feat-v1\n")
        self.record_evidence(unit)
        calls = []
        original = work_unit.transition_work_unit

        def spy(w, new_status):
            calls.append((w.get("status"), new_status))
            return original(w, new_status)

        work_unit.transition_work_unit = spy
        try:
            task_manager.finish_unit(self.root, TID, "u1")
        finally:
            work_unit.transition_work_unit = original
        # §70 父验证语义：恰好两次表内转换，不越表直跳
        self.assertEqual(calls, [("running", "verifying"),
                                 ("verifying", "completed")])

    def test_verifying_with_full_evidence_completes(self):
        # B16（原 FinishTest verifying→completed 用例迁移）
        self._verifying_with_lease()
        st = task_manager.finish_unit(self.root, TID, "u1")
        self.assertEqual(unit_of(st, "u1")["status"], "completed")

    def test_completed_unit_releases_lease(self):
        # B17：completed 后租约已释放
        self._verifying_with_lease()
        task_manager.finish_unit(self.root, TID, "u1")
        self.assertEqual(lease.lease_state(self.root, TID), {})

    def test_completed_unit_removed_from_active(self):
        # B18：completed 后 dispatch.active 已移除（返回值与落盘一致）
        self._verifying_with_lease()
        st = task_manager.finish_unit(self.root, TID, "u1")
        self.assertEqual(st["dispatch"]["active"], [])
        self.assertEqual(
            state.load_state(self.root, TID)["dispatch"]["active"], [])

    def test_unit_finished_event_persisted_with_outcome(self):
        # B19：completed 后 unit_finished 落盘（unit + outcome）
        self._verifying_with_lease()
        task_manager.finish_unit(self.root, TID, "u1")
        finished = events(self.root, "unit_finished")
        self.assertEqual(len(finished), 1)
        self.assertEqual(finished[0]["unit"], "u1")
        self.assertEqual(finished[0]["outcome"], "completed")


# —— RB-1 完成证据门 × DAG 集成（计划 §2.5 C20-C21，git fixture） ——

@unittest.skipUnless(shutil.which("git"),
                     "环境无 git 可执行，跳过 git fixture 测试")
class FinishGateDagIntegrationTest(GitRepoFixture):
    """验证缺失不得经 refresh_readiness 传播为错误解锁下游（RB-1 动机
    本身）：上游完成门被拒 → 下游不得 ready；完整证据完成 → 照常提升。"""

    def _task_with_downstream(self):
        """u1（上游）prepare + commit 到 running；u2 pending 依赖 u1。"""
        upstream = wu("u1")
        make_task(self.root, [upstream,
                              wu("u2", ("src/b/**",), deps=("u1",),
                                 status="pending")])
        task_manager.prepare_dispatch(self.root, TID, "u1")
        task_manager.commit_dispatch(self.root, TID, "u1")
        return upstream

    def test_upstream_rejected_keeps_downstream_locked(self):
        # C20：上游无证据 finish 被拒 → 仍 running → 下游不得 ready
        self._task_with_downstream()
        with self.assertRaises(task_manager.TaskManagerError):
            task_manager.finish_unit(self.root, TID, "u1")
        st = state.load_state(self.root, TID)
        self.assertEqual(unit_of(st, "u1")["status"], "running")
        self.assertEqual(task_manager.refresh_readiness(self.root, TID), [])
        st = state.load_state(self.root, TID)
        self.assertEqual(unit_of(st, "u2")["status"], "pending")
        self.assertEqual(events(self.root, "unit_finished"), [])

    def test_upstream_completed_with_evidence_promotes_downstream(self):
        # C21：上游完整证据完成 → refresh_readiness 提升下游 ready
        upstream = self._task_with_downstream()
        self.dirty_file("src/a/feature.ts", b"feat-v1\n")
        self.record_evidence(upstream)
        st = task_manager.finish_unit(self.root, TID, "u1")
        self.assertEqual(unit_of(st, "u1")["status"], "completed")
        self.assertEqual(task_manager.refresh_readiness(self.root, TID),
                         ["u2"])
        st = state.load_state(self.root, TID)
        self.assertEqual(unit_of(st, "u2")["status"], "ready")


# —— RELEASE：就绪推导态提升（refresh_readiness，dogfood 缺口补齐） ——

class RefreshReadinessTest(TaskManagerTestBase):

    def test_pending_unit_with_satisfied_deps_is_promoted(self):
        make_task(self.root, [wu("u0", status="completed"),
                              wu("u1", ("src/b/**",), deps=("u0",),
                                 status="pending")])
        promoted = task_manager.refresh_readiness(self.root, TID)
        self.assertEqual(promoted, ["u1"])
        st = state.load_state(self.root, TID)
        self.assertEqual(unit_of(st, "u1")["status"], "ready")

    def test_waiting_dependency_unit_is_promoted(self):
        make_task(self.root, [wu("u0", status="completed"),
                              wu("u1", ("src/b/**",), deps=("u0",),
                                 status="waiting_dependency")])
        promoted = task_manager.refresh_readiness(self.root, TID)
        self.assertEqual(promoted, ["u1"])
        st = state.load_state(self.root, TID)
        self.assertEqual(unit_of(st, "u1")["status"], "ready")

    def test_unmet_dependency_blocks_promotion(self):
        # 一个 dep completed、另一个 running → 不满足 → 不提升、零写入
        make_task(self.root, [wu("u0", status="completed"),
                              wu("u9", ("src/z/**",), status="running"),
                              wu("u1", ("src/b/**",), deps=("u0", "u9"),
                                 status="pending")])
        before = state_bytes(self.root)
        self.assertEqual(task_manager.refresh_readiness(self.root, TID), [])
        self.assertEqual(state_bytes(self.root), before)
        st = state.load_state(self.root, TID)
        self.assertEqual(unit_of(st, "u1")["status"], "pending")

    def test_no_promotion_means_zero_writes_and_no_journal(self):
        # 全部已 ready → 零返回零写入；journal 不落任何事件
        make_task(self.root, [wu("u1")])
        before = state_bytes(self.root)
        self.assertEqual(task_manager.refresh_readiness(self.root, TID), [])
        self.assertEqual(state_bytes(self.root), before)
        self.assertEqual(journal.read_events(self.root, TID), [])

    def test_promotion_order_follows_units_appearance_order(self):
        make_task(self.root, [wu("u0", status="completed"),
                              wu("u2", ("src/c/**",), deps=("u0",),
                                 status="pending"),
                              wu("u1", ("src/b/**",), deps=("u0",),
                                 status="pending")])
        promoted = task_manager.refresh_readiness(self.root, TID)
        self.assertEqual(promoted, ["u2", "u1"])

    def test_promoted_unit_passes_prepare_dispatch_directly(self):
        # 集成：提升后 prepare_dispatch 无需任何手工转态直接准入
        make_task(self.root, [wu("u0", status="completed"),
                              wu("u1", ("src/b/**",), deps=("u0",),
                                 status="pending")])
        self.assertEqual(task_manager.refresh_readiness(self.root, TID),
                         ["u1"])
        plan = task_manager.prepare_dispatch(self.root, TID, "u1")
        self.assertEqual(plan["dispatch"], ["u1"])


if __name__ == "__main__":
    unittest.main()
