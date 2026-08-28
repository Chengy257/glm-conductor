#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""runtime.reconcile 集成冒烟测试（v2 工作块 B8.4，§68-§69 恢复对账）。

三个端到端场景（每个都是：真 git 仓库 + state.json + work_units +
journal + 驱动 reconcile_interrupted）+ 纯函数容错用例：
  - 场景 A 串行多单元：B 依赖 A；A completed（owned 文件已提交）零建议
    （§68 completed 不重跑锚定）；B running 有残留、无验证事件 →
    verifying；主会话补新鲜验证事件（指纹对 B 的 owned_hits 真算）→
    再次对账建议 completed（running→completed 仅恢复对账可用）；
  - 场景 B 依赖图：C 依赖 A、B；A/B 均 completed → ready_units 含 C、
    plan_dispatch（AVAILABLE, max_workers=1）派发 C；中断 C（running）
    且清空其残留 → 建议 ready（干净重派），应用后重新可派发；
  - 场景 C 中断恢复：A verifying（残留 + 新鲜验证事件 → completed）；
    B running（残留 + 过期指纹事件——先记事件再改文件 → verifying）；
    C running 无残留 → ready；建议经 transition_work_unit 逐个应用全部
    合法（转换表闭环），应用后 ready_units 恰为重派集合；
  - 纯函数用例：非 running/verifying 单元零建议（全状态词汇穷举）；
    events 注入空 / 非 list 容错；建议键序按 units 出现序；§69 证据
    匹配四条件逐项锚定（event 名 / 指纹 / status=pass / command ∈
    verification）；纯建议纪律（零落盘、不改 units）；结构性错误
    （git 失败 / 非法 ownership 模式 / 指纹目标为目录）自然上抛。

git fixture 做法（git init + config + commit、Windows 下 .git 只读位
清理）对齐 tests/test_stop_gate.py 的 GitRepoFixture；环境无 git 可执行
时 git 场景自动 skipTest。被检仓库目录由 tempfile.TemporaryDirectory
提供，不污染真实工作区。

运行：
    cd <repo_root> && python3 -m unittest tests.test_reconcile -v
"""

import copy
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "glm-conductor"))
from runtime import dependency
from runtime import dispatcher
from runtime import fingerprint as fingerprint_mod
from runtime import journal as journal_mod
from runtime import ownership
from runtime import reconcile
from runtime import state
from runtime import work_unit

TID = "recon-task-1a2b3c"
ROUTE = {"mode": "delegate", "delegability": "high", "assurance": "standard",
         "executor": "flash-implementer", "continuity": "foreground"}

CMD_A = "python3 -m unittest tests.test_feature_a"
CMD_B = "python3 -m unittest tests.test_feature_b"
CMD_C = "python3 -m unittest tests.test_feature_c"


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


def make_unit(uid, owned, *, deps=(), status="pending", verification=()):
    """构造 §61 形状的 work unit（经 work_unit.new_work_unit 构造）。"""
    return work_unit.new_work_unit(
        uid, "恢复对账场景单元 %s" % uid, executor="flash-implementer",
        ownership=list(owned), verification=list(verification),
        depends_on=list(deps), status=status)


class TempDirFixture(unittest.TestCase):
    """tempdir 基座：目录/状态文件写入 + Windows .git 只读位清理。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._force_cleanup)
        self.repo = Path(self._tmp.name)

    def _force_cleanup(self):
        """解除 git 只读对象后清理临时目录（模式复用 test_stop_gate）。"""
        git_dir = os.path.join(self._tmp.name, ".git")
        if os.path.isdir(git_dir):
            for dirpath, _dirnames, filenames in os.walk(git_dir):
                for name in filenames:
                    try:
                        os.chmod(os.path.join(dirpath, name), stat.S_IWRITE)
                    except OSError:
                        pass
        self._tmp.cleanup()

    def write(self, rel, data):
        """在 fixture 仓库内写一个文件（自动建父目录）。"""
        target = self.repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


class GitRepoFixture(TempDirFixture):
    """tempdir 内真实 git 仓库基座（对齐 test_stop_gate.py）。"""

    def setUp(self):
        super().setUp()
        run_git(self.repo, "init")
        run_git(self.repo, "config", "user.email", "reconcile@example.com")
        run_git(self.repo, "config", "user.name", "Reconcile")
        # 固定换行行为，避免全局 autocrlf 干扰状态判定
        run_git(self.repo, "config", "core.autocrlf", "false")
        # 运行时目录（state.json / events.jsonl）不入库：否则它们作为
        # 未跟踪文件混入 touched 清单，污染对账证据
        self.write(".gitignore", b".glm-conductor/\n")
        self.write("base.txt", b"v1\n")
        run_git(self.repo, "add", ".")
        run_git(self.repo, "commit", "-m", "init")


class ReconcileFixture(GitRepoFixture):
    """git 仓库 + 任务 state.json（含 work_units）+ journal 事件基座。"""

    def save_units(self, units, task_id=TID):
        """写入任务 state.json（work_units 经 validate_state 全量校验）。"""
        st = state.new_task_state(task_id, "恢复对账冒烟目标", dict(ROUTE))
        st["work_units"] = units
        state.save_state(self.repo, st)
        return st

    def record_event(self, payload, task_id=TID):
        """向任务 journal 追加一条事件（缺省通道的真实落盘）。"""
        return journal_mod.append_event(self.repo, task_id, payload)

    def current_fingerprint(self, unit, touched=None):
        """按 reconcile 同一口径真算单元当前证据指纹（对 owned_hits）。"""
        if touched is None:
            touched = ownership.git_touched_files(str(self.repo))
        owned_hits, _ = ownership.classify_paths(touched, unit["ownership"])
        return fingerprint_mod.compute_fingerprint(str(self.repo), owned_hits)

    def verification_event(self, unit, fp):
        """构造绑定指定指纹的 pass 验证事件（journal verification 形态）。"""
        return {"event": "verification",
                "command": unit["verification"][0],
                "status": "pass",
                "fingerprint": fp}


# —— 场景 A：串行多单元（completed 不重跑 + 残留无证据 + 补证后完成） ——

@unittest.skipUnless(shutil.which("git"), "环境无 git 可执行，跳过 git fixture 测试")
class ScenarioASerialUnitsTest(ReconcileFixture):
    """§69 场景 A：B 依赖 A；A completed 零建议；B running 有残留。"""

    def test_serial_units_completed_untouched_running_reverified(self):
        # A 的 owned 文件已提交（completed 的成果已入库，touched 不再含它）
        self.write("src/feature-a/a.py", b"a-v1\n")
        run_git(self.repo, "add", "src/feature-a")
        run_git(self.repo, "commit", "-m", "feature-a done")
        unit_a = make_unit("feat-a", ("src/feature-a/**",),
                           status="completed", verification=(CMD_A,))
        unit_b = make_unit("feat-b", ("src/feature-b/**",),
                           deps=("feat-a",), status="running",
                           verification=(CMD_B,))
        units = [unit_a, unit_b]
        self.save_units(units)
        # B running 且 ownership 内有残留改动（未跟踪文件）；无任何验证事件
        self.write("src/feature-b/b.py", b"b-v1\n")
        # 驱动对账：touched / events 全走缺省通道（真 git status + 真 journal）
        report = reconcile.reconcile_interrupted(str(self.repo), TID, units)
        # A completed 零建议（§68：completed 不重跑）；B → verifying
        self.assertEqual(report["suggestions"], {
            "feat-b": {"to": "verifying",
                       "reason": "残留改动无新鲜验证证据：主会话必须"
                                 "亲自检查 diff 并运行单元验证"}})
        self.assertEqual(report["reconciled"], ["feat-b"])
        self.assertEqual(report["touched"], ["src/feature-b/b.py"])
        # 应用建议：running → verifying 合法（转换表主验证边）
        work_unit.transition_work_unit(
            unit_b, report["suggestions"]["feat-b"]["to"])
        self.assertEqual(unit_b["status"], "verifying")
        # 模拟主会话亲自验证：追加绑定 B 当前 owned_hits 的新鲜验证事件
        touched = ownership.git_touched_files(str(self.repo))
        fp = self.current_fingerprint(unit_b, touched)
        self.record_event(self.verification_event(unit_b, fp))
        report2 = reconcile.reconcile_interrupted(str(self.repo), TID, units)
        # 再次对账：存在绑定当前改动的新鲜证据 → 建议 completed
        self.assertEqual(report2["suggestions"], {
            "feat-b": {"to": "completed",
                       "reason": "存在绑定当前改动的新鲜验证证据"
                                 "（parent-observed），待主会话确认"}})
        self.assertEqual(report2["reconciled"], ["feat-b"])
        # 应用：running → completed 仅恢复对账可用（B8.1 锁定的恢复边）
        work_unit.transition_work_unit(
            unit_b, report2["suggestions"]["feat-b"]["to"])
        self.assertEqual(unit_b["status"], "completed")
        # A、B 均 completed → 无重派单元
        self.assertEqual(dependency.ready_units(units), [])


# —— 场景 B：依赖图（派发准入联动 + 干净重派） ——

@unittest.skipUnless(shutil.which("git"), "环境无 git 可执行，跳过 git fixture 测试")
class ScenarioBDependencyGraphTest(ReconcileFixture):
    """§69 场景 B：A/B completed，C ready 依赖满足被派发，中断后干净重派。"""

    def test_dependency_graph_dispatch_interrupted_clean_redispatch(self):
        # A、B 的成果已提交（completed）
        self.write("src/base-a/x.py", b"x\n")
        self.write("src/base-b/y.py", b"y\n")
        run_git(self.repo, "add", ".")
        run_git(self.repo, "commit", "-m", "base a+b done")
        unit_a = make_unit("base-a", ("src/base-a/**",),
                           status="completed", verification=(CMD_A,))
        unit_b = make_unit("base-b", ("src/base-b/**",),
                           status="completed", verification=(CMD_B,))
        unit_c = make_unit("feat-c", ("src/feature-c/**",),
                           deps=("base-a", "base-b"), status="ready",
                           verification=(CMD_C,))
        units = [unit_a, unit_b, unit_c]
        self.save_units(units)
        # 依赖满足 → ready_units 含 C；plan_dispatch（AVAILABLE）派发 C
        self.assertEqual(dependency.ready_units(units), ["feat-c"])
        decision = dispatcher.plan_dispatch(units, max_workers=1)
        self.assertEqual(decision["dispatch"], ["feat-c"])
        self.assertEqual(decision["deferred"], [])
        # 主会话按决策派发：ready → running
        work_unit.transition_work_unit(unit_c, "running")
        # 中断 C：曾写出的残留被清空（工作区回到干净，等价 git checkout）
        self.write("src/feature-c/c.py", b"partial\n")
        os.remove(self.repo / "src/feature-c" / "c.py")
        # 对账：无 ownership 内残留 → 干净重派 ready
        report = reconcile.reconcile_interrupted(str(self.repo), TID, units)
        self.assertEqual(report["suggestions"], {
            "feat-c": {"to": "ready",
                       "reason": "无 ownership 内残留改动，干净重派"}})
        self.assertEqual(report["reconciled"], ["feat-c"])
        # 应用：running → ready（§69 恢复边）→ 重新可派发
        work_unit.transition_work_unit(
            unit_c, report["suggestions"]["feat-c"]["to"])
        self.assertEqual(dependency.ready_units(units), ["feat-c"])
        self.assertEqual(
            dispatcher.plan_dispatch(units, max_workers=1)["dispatch"],
            ["feat-c"])


# —— 场景 C：中断恢复（三分支齐全 + 转换表应用闭环） ——

@unittest.skipUnless(shutil.which("git"), "环境无 git 可执行，跳过 git fixture 测试")
class ScenarioCInterruptRecoveryTest(ReconcileFixture):
    """§69 场景 C：新鲜证据完成 / 过期指纹待验 / 无残留重派，三建议应用闭环。"""

    def test_mixed_recovery_suggestions_applied_via_transition_table(self):
        unit_a = make_unit("mod-a", ("src/mod-a/**",), status="verifying",
                           verification=(CMD_A,))
        unit_b = make_unit("mod-b", ("src/mod-b/**",), status="running",
                           verification=(CMD_B,))
        unit_c = make_unit("mod-c", ("src/mod-c/**",), status="running",
                           verification=(CMD_C,))
        units = [unit_a, unit_b, unit_c]
        self.save_units(units)
        # A（verifying）：残留 + 新鲜验证事件（先写文件、指纹对当前真算）
        self.write("src/mod-a/a.py", b"a-v1\n")
        fp_a = self.current_fingerprint(unit_a)
        self.record_event(self.verification_event(unit_a, fp_a))
        # B（running）：先记事件（绑定 v1），再改文件 → 指纹过期
        self.write("src/mod-b/b.py", b"b-v1\n")
        fp_b_stale = self.current_fingerprint(unit_b)
        self.record_event(self.verification_event(unit_b, fp_b_stale))
        self.write("src/mod-b/b.py", b"b-v2\n")
        # C（running）：无残留（不写文件）
        report = reconcile.reconcile_interrupted(str(self.repo), TID, units)
        # 三分支建议齐全，键序按 units 出现序
        self.assertEqual(list(report["suggestions"]),
                         ["mod-a", "mod-b", "mod-c"])
        self.assertEqual(report["suggestions"]["mod-a"]["to"], "completed")
        self.assertEqual(report["suggestions"]["mod-b"]["to"], "verifying")
        self.assertEqual(report["suggestions"]["mod-c"]["to"], "ready")
        self.assertEqual(report["reconciled"], ["mod-a", "mod-b", "mod-c"])
        # 应用：verifying→completed / running→verifying / running→ready
        # 全部在 §62 转换表内（转换闭环，无 ValueError）
        for uid, unit in (("mod-a", unit_a), ("mod-b", unit_b),
                          ("mod-c", unit_c)):
            work_unit.transition_work_unit(
                unit, report["suggestions"][uid]["to"])
        self.assertEqual([u["status"] for u in units],
                         ["completed", "verifying", "ready"])
        # 应用后重派集合恰为 mod-c（A completed 不重跑、B verifying 不重派）
        self.assertEqual(dependency.ready_units(units), ["mod-c"])


# —— 纯函数：非中断状态零建议（§68 锚定） ——

@unittest.skipUnless(shutil.which("git"), "环境无 git 可执行，跳过 git fixture 测试")
class ReconcileNonInterruptedTest(GitRepoFixture):
    """running / verifying 之外的全部状态词汇零建议（含 completed 不重跑）。"""

    def test_all_non_interrupted_statuses_zero_suggestions(self):
        units = []
        for status in work_unit.WORK_UNIT_STATUSES:
            if status in reconcile.INTERRUPTED_STATUSES:
                continue
            units.append(make_unit("u-%s" % status,
                                   ("src/%s/**" % status,), status=status))
        # 即便工作区有改动，非中断单元也零建议
        touched = ["src/completed/x.py", "src/failed/y.py"]
        report = reconcile.reconcile_interrupted(
            str(self.repo), TID, units, touched=touched, events=[])
        self.assertEqual(report["suggestions"], {})
        self.assertEqual(report["reconciled"], [])
        self.assertEqual(report["touched"], touched)

    def test_units_non_list_tolerated_as_empty(self):
        report = reconcile.reconcile_interrupted(
            str(self.repo), TID, None, touched=[], events=[])
        self.assertEqual(report,
                         {"suggestions": {}, "reconciled": [], "touched": []})


# —— 纯函数：events 注入通道容错 ——

@unittest.skipUnless(shutil.which("git"), "环境无 git 可执行，跳过 git fixture 测试")
class ReconcileEventsToleranceTest(GitRepoFixture):
    """events 注入空 list / 非 list / 含非 dict 项：按无证据处理，不炸对账。"""

    def residual_running_unit(self):
        self.write("src/tol/t.py", b"v1\n")
        return make_unit("tol", ("src/tol/**",), status="running",
                         verification=(CMD_A,))

    def test_empty_and_non_list_events_tolerated(self):
        unit = self.residual_running_unit()
        for events in ([], "not-a-list", {"event": "verification"}, 42):
            with self.subTest(events=events):
                report = reconcile.reconcile_interrupted(
                    str(self.repo), TID, [unit], events=events)
                self.assertEqual(report["suggestions"]["tol"]["to"],
                                 "verifying")

    def test_non_dict_event_items_skipped(self):
        unit = self.residual_running_unit()
        report = reconcile.reconcile_interrupted(
            str(self.repo), TID, [unit], events=["junk", None, 7])
        self.assertEqual(report["suggestions"]["tol"]["to"], "verifying")


# —— 纯函数：§69 证据匹配四条件逐项锚定 ——

@unittest.skipUnless(shutil.which("git"), "环境无 git 可执行，跳过 git fixture 测试")
class ReconcileEvidenceMatchingTest(ReconcileFixture):
    """event 名 / 指纹相等 / status=pass / command ∈ verification 四条件。"""

    def residual_unit(self):
        self.write("src/ev/e.py", b"v1\n")
        return make_unit("ev", ("src/ev/**",), status="running",
                         verification=(CMD_A,))

    def suggest_to(self, unit, event):
        report = reconcile.reconcile_interrupted(
            str(self.repo), TID, [unit], events=[event])
        return report["suggestions"]["ev"]["to"]

    def test_matching_event_suggests_completed(self):
        unit = self.residual_unit()
        fp = self.current_fingerprint(unit)
        self.assertEqual(
            self.suggest_to(unit, self.verification_event(unit, fp)),
            "completed")

    def test_non_pass_status_does_not_match(self):
        unit = self.residual_unit()
        fp = self.current_fingerprint(unit)
        event = self.verification_event(unit, fp)
        event["status"] = "fail"
        self.assertEqual(self.suggest_to(unit, event), "verifying")

    def test_command_outside_verification_list_does_not_match(self):
        unit = self.residual_unit()
        fp = self.current_fingerprint(unit)
        event = self.verification_event(unit, fp)
        event["command"] = "python3 -m unittest tests.test_other"
        self.assertEqual(self.suggest_to(unit, event), "verifying")

    def test_non_verification_event_name_does_not_match(self):
        unit = self.residual_unit()
        fp = self.current_fingerprint(unit)
        event = self.verification_event(unit, fp)
        event["event"] = "review"
        self.assertEqual(self.suggest_to(unit, event), "verifying")

    def test_stale_fingerprint_does_not_match(self):
        # 先记事件再改文件 → 记录指纹 ≠ 当前指纹 → 待验证（场景 C 单元级锚）
        self.write("src/ev/e.py", b"v1\n")
        unit = make_unit("ev", ("src/ev/**",), status="running",
                         verification=(CMD_A,))
        fp_stale = self.current_fingerprint(unit)
        self.record_event(self.verification_event(unit, fp_stale))
        self.write("src/ev/e.py", b"v2\n")
        report = reconcile.reconcile_interrupted(
            str(self.repo), TID, [unit])  # events 走缺省通道（真 journal）
        self.assertEqual(report["suggestions"]["ev"]["to"], "verifying")


# —— 纯函数：建议键序按 units 出现序 ——

@unittest.skipUnless(shutil.which("git"), "环境无 git 可执行，跳过 git fixture 测试")
class ReconcileSuggestionOrderTest(GitRepoFixture):
    """suggestions / reconciled 键序按 units 出现序（非 id 排序序）。"""

    def test_suggestion_keys_follow_units_appearance_order(self):
        # 出现序 zulu → alpha ≠ 排序序（alpha < zulu），锚定「出现序」语义
        self.write("src/zulu/z.py", b"z\n")
        unit_z = make_unit("zulu", ("src/zulu/**",), status="running",
                           verification=(CMD_A,))
        unit_a = make_unit("alpha", ("src/alpha/**",), status="verifying",
                           verification=(CMD_B,))
        report = reconcile.reconcile_interrupted(
            str(self.repo), TID, [unit_z, unit_a],
            touched=["src/zulu/z.py"], events=[])
        self.assertEqual(list(report["suggestions"]), ["zulu", "alpha"])
        self.assertEqual(report["reconciled"], ["zulu", "alpha"])
        # zulu 有残留（无证据）→ verifying；alpha 无残留 → ready
        self.assertEqual(report["suggestions"]["zulu"]["to"], "verifying")
        self.assertEqual(report["suggestions"]["alpha"]["to"], "ready")


# —— 纯函数：纯建议纪律（零落盘、不改 units） ——

@unittest.skipUnless(shutil.which("git"), "环境无 git 可执行，跳过 git fixture 测试")
class ReconcilePurityTest(GitRepoFixture):
    """reconcile 纯建议：不写 state / journal，不修改传入 units。"""

    def test_no_disk_writes_and_units_unmutated(self):
        self.write("src/pure/p.py", b"v1\n")
        unit = make_unit("pure", ("src/pure/**",), status="running",
                         verification=(CMD_A,))
        snapshot = copy.deepcopy(unit)
        touched = ["src/pure/p.py"]
        report = reconcile.reconcile_interrupted(
            str(self.repo), TID, [unit], touched=touched, events=[])
        self.assertEqual(unit, snapshot)
        self.assertEqual(report["touched"], touched)
        # 零落盘：reconcile 不创建 .glm-conductor（落盘归调用方）
        self.assertFalse((self.repo / ".glm-conductor").exists())


# —— 结构性错误自然上抛（调用方处理） ——

class ReconcileErrorPropagationTest(TempDirFixture):
    """git 失败 / 非法 ownership 模式 / 指纹目标为目录 → 异常不静默吞。"""

    def test_git_failure_raises_ownership_error(self):
        # 非 git 仓库 + touched 缺省 → git_touched_files 抛 OwnershipError
        unit = make_unit("g", ("src/g/**",), status="running",
                         verification=(CMD_A,))
        with self.assertRaises(ownership.OwnershipError):
            reconcile.reconcile_interrupted(str(self.repo), TID, [unit])

    def test_illegal_ownership_pattern_raises_ownership_error(self):
        unit = make_unit("bad", ("src//bad/**",), status="running",
                         verification=(CMD_A,))
        with self.assertRaises(ownership.OwnershipError):
            reconcile.reconcile_interrupted(str(self.repo), TID, [unit],
                                            touched=["src/x.py"], events=[])

    def test_fingerprint_error_on_directory_target_propagates(self):
        # touched 注入命中一个目录路径 → compute_fingerprint 抛 FingerprintError
        os.makedirs(self.repo / "src" / "dir", exist_ok=True)
        unit = make_unit("d", ("src/dir",), status="running",
                         verification=(CMD_A,))
        with self.assertRaises(fingerprint_mod.FingerprintError):
            reconcile.reconcile_interrupted(str(self.repo), TID, [unit],
                                            touched=["src/dir"], events=[])


if __name__ == "__main__":
    unittest.main()
