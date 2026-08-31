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
    C running 无残留 → ready；应用循环只对 suggestions 生效
    （advisories 断言为空），全部转换合法（转换表闭环），应用后
    ready_units 恰为重派集合；
  - verifying 态建议限定（P1#1）：新鲜证据 → suggestions completed；
    无残留无新鲜 / 有残留无新鲜两种表外情形 → advisories（无 to、
    不携带转换，裁决归主会话）；
    - 纯函数用例：非 running/verifying 单元零建议（全状态词汇穷举）；
    events 注入空 / 非 list 容错；suggestions / advisories /
    reconciled 键序均按 units 出现序；§69 证据匹配五条件逐项锚定
    （event 名 / unit==单元 id（H6 逐字精确，无 unit 旧格式不匹配）/ 指纹 /
    status=pass / command ∈ verification）；纯
    建议纪律（零落盘、不改 units）；结构性错误（git 失败 / 非法
    ownership 模式 / 指纹目标为目录）自然上抛；
  - H5 reconcile_leases 三分（无 git 需求，tempdir + 手工租约）：
    completed/ready owner → stale（reason 注明状态）、图中无 owner →
    stale（图中无此单元）、running/verifying 未过期 → active、running
    已过期 → expired_running（不入 stale，不自动清）、桶内按 path
    排序、空租约空报告、非 list units 按空图容错、纯建议纪律
    （零落盘、不改 units）；
  - H6 证据归属绑定（work_unit_verification_evidence_requires_matching_
    unit_id，git 基座真算指纹）：同 command / 重叠 ownership 的 A/B
    双 running 单元——unit=uA 的验证事件 → A 建议 completed、B 只得
    verifying（不得复用）；事件归属对调对称成立；无 unit 字段的旧
    格式事件不再匹配（双方 verifying，保守按无证据处理）；
  - WU-P1 共享证据谓词（fresh_unit_verification，all-match 口径）：
    直测——2 条 required 仅 1 条有证据 → ok=False 且 missing 恰为
    未验证那条（all-match，D2 收紧）；空 required 快路径（无 git 的
    普通 tempdir 可调用，零 git 零读盘；非 list/tuple required 同
    口径）；unit=other / status=fail / stale 指纹 / command 不在
    required 内 / 无 unit 字段旧格式五类事件均不算；events 注入非
    list 容错为 []；恢复集成（经 reconcile_interrupted）——多
    command 部分证据 + 残留改动 → running 得 verifying（不再
    completed）、verifying 得 stay advisory，全部证据才双双 completed。

git fixture 做法（git init + config + commit、Windows 下 .git 只读位
清理）对齐 tests/test_stop_gate.py 的 GitRepoFixture；环境无 git 可执行
时 git 场景自动 skipTest。被检仓库目录由 tempfile.TemporaryDirectory
提供，不污染真实工作区。

运行：
    cd <repo_root> && python3 -m unittest tests.test_reconcile -v
"""

import copy
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
from runtime import agent_run
from runtime import dependency
from runtime import dispatcher
from runtime import fingerprint as fingerprint_mod
from runtime import journal as journal_mod
from runtime import lease as lease_mod
from runtime import ownership
from runtime import reconcile
from runtime import state
from runtime import work_unit

TID = "recon-task-1a2b3c"
# H2 夹具迁移：delegate 路由在规则 R4 下要求非空 ownership/verification，
# 而本文件的指纹口径依赖「未声明 ownership = 全部改动」基座——改用矩阵
# 合法的 solo 路由（被测的对账行为与路由模式无关，ownership 保持未声明）
ROUTE = {"mode": "solo", "delegability": "low", "assurance": "standard",
         "executor": "main", "continuity": "foreground"}

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
        """构造绑定单元 id + 指纹的 pass 验证事件（H6 journal 形态）。"""
        return {"event": "verification",
                "unit": unit["id"],
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
        # 三分支建议齐全，键序按 units 出现序；A（verifying+新鲜证据）
        # 仍建议 completed 进 suggestions，advisories 为空
        self.assertEqual(list(report["suggestions"]),
                         ["mod-a", "mod-b", "mod-c"])
        self.assertEqual(report["suggestions"]["mod-a"]["to"], "completed")
        self.assertEqual(report["suggestions"]["mod-b"]["to"], "verifying")
        self.assertEqual(report["suggestions"]["mod-c"]["to"], "ready")
        self.assertEqual(report["advisories"], {})
        self.assertEqual(report["reconciled"], ["mod-a", "mod-b", "mod-c"])
        # 应用循环只对 suggestions 生效：verifying→completed /
        # running→verifying / running→ready 全部在 §62 转换表内
        # （转换闭环，无 ValueError）
        by_id = {u["id"]: u for u in units}
        for uid, advice in report["suggestions"].items():
            work_unit.transition_work_unit(by_id[uid], advice["to"])
        self.assertEqual([u["status"] for u in units],
                         ["completed", "verifying", "ready"])
        # 应用后重派集合恰为 mod-c（A completed 不重跑、B verifying 不重派）
        self.assertEqual(dependency.ready_units(units), ["mod-c"])


# —— 纯函数：verifying 态建议限定（P1#1：表外情形走 advisories） ——

@unittest.skipUnless(shutil.which("git"), "环境无 git 可执行，跳过 git fixture 测试")
class ReconcileVerifyingAdviceTest(ReconcileFixture):
    """verifying 态仅「新鲜证据 → completed」进 suggestions；无残留 /
    有残留无新鲜两种表外情形产出 no-op 裁决提示（advisories，无 to）。"""

    def verifying_unit(self, uid="ver"):
        return make_unit(uid, ("src/ver/**",), status="verifying",
                         verification=(CMD_A,))

    def test_fresh_evidence_suggests_completed_in_suggestions(self):
        self.write("src/ver/v.py", b"v1\n")
        unit = self.verifying_unit()
        fp = self.current_fingerprint(unit)
        report = reconcile.reconcile_interrupted(
            str(self.repo), TID, [unit],
            events=[self.verification_event(unit, fp)])
        # 新鲜验证证据 → suggestions completed（verifying→completed
        # 在 §62 转换表内），advisories 为空
        self.assertEqual(report["suggestions"], {
            "ver": {"to": "completed",
                    "reason": "存在绑定当前改动的新鲜验证证据"
                              "（parent-observed），待主会话确认"}})
        self.assertEqual(report["advisories"], {})
        self.assertEqual(report["reconciled"], ["ver"])
        # 应用合法：verifying → completed（转换表主验证边）
        work_unit.transition_work_unit(
            unit, report["suggestions"]["ver"]["to"])
        self.assertEqual(unit["status"], "completed")

    def test_no_residue_no_fresh_yields_ruling_advisory_without_to(self):
        # verifying + 无残留：verifying→ready 非法 → 只出裁决提示
        unit = self.verifying_unit()
        report = reconcile.reconcile_interrupted(
            str(self.repo), TID, [unit], touched=[], events=[])
        self.assertEqual(report["suggestions"], {})
        self.assertEqual(report["advisories"], {
            "ver": {"reason": "verifying 无残留改动且无新鲜验证证据："
                              "主会话裁决——重跑验证或按失败处理"}})
        self.assertNotIn("to", report["advisories"]["ver"])  # 无转换可应用
        self.assertEqual(report["reconciled"], ["ver"])
        # 纯建议纪律：建议层不改单元状态
        self.assertEqual(unit["status"], "verifying")

    def test_residue_without_fresh_yields_stay_advisory_without_to(self):
        # verifying + 有残留无新鲜证据：verifying→verifying 自转换非法
        self.write("src/ver/v.py", b"v1\n")
        unit = self.verifying_unit()
        report = reconcile.reconcile_interrupted(
            str(self.repo), TID, [unit], events=[])
        self.assertEqual(report["suggestions"], {})
        self.assertEqual(report["advisories"], {
            "ver": {"reason": "verifying 保持现状：主会话直接运行单元"
                              "验证后完成或失败"}})
        self.assertNotIn("to", report["advisories"]["ver"])
        self.assertEqual(report["reconciled"], ["ver"])

    def test_stale_evidence_still_yields_stay_advisory(self):
        # 先记事件再改文件 → 指纹过期 ≠ 新鲜证据 → 仍是保持现状提示
        self.write("src/ver/v.py", b"v1\n")
        unit = self.verifying_unit()
        fp_stale = self.current_fingerprint(unit)
        self.record_event(self.verification_event(unit, fp_stale))
        self.write("src/ver/v.py", b"v2\n")
        report = reconcile.reconcile_interrupted(str(self.repo), TID, [unit])
        self.assertEqual(report["suggestions"], {})
        self.assertEqual(report["advisories"], {
            "ver": {"reason": "verifying 保持现状：主会话直接运行单元"
                              "验证后完成或失败"}})


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
        self.assertEqual(
            report,
            {"suggestions": {}, "advisories": {}, "reconciled": [],
             "touched": []})


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


# —— 纯函数：§69 证据匹配五条件逐项锚定（H6 起含 unit 归属绑定） ——

@unittest.skipUnless(shutil.which("git"), "环境无 git 可执行，跳过 git fixture 测试")
class ReconcileEvidenceMatchingTest(ReconcileFixture):
    """event 名 / unit==单元 id / 指纹相等 / status=pass / command ∈ verification 五条件。"""

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


# —— WU-P1：fresh_unit_verification 共享证据谓词（all-match 口径直测） ——

@unittest.skipUnless(shutil.which("git"), "环境无 git 可执行，跳过 git fixture 测试")
class FreshUnitVerificationPredicateTest(ReconcileFixture):
    """fresh_unit_verification 直测：五条件逐项 + all-match 收紧 +
    events 注入容错（touched 显式注入，指纹与 reconcile 同口径真算）。"""

    TOUCHED = ["src/multi/m.py"]

    def multi_unit(self):
        """双 required command 的 running 单元（all-match 的最小载体）。"""
        self.write("src/multi/m.py", b"v1\n")
        return make_unit("multi", ("src/multi/**",), status="running",
                         verification=(CMD_A, CMD_B))

    def evidence(self, unit, fp, *, command=None, unit_id=None,
                 status="pass", event_name="verification"):
        """构造一条可逐字段变异的验证事件（H6 journal 形态）。"""
        return {"event": event_name,
                "unit": unit["id"] if unit_id is None else unit_id,
                "command": unit["verification"][0] if command is None
                else command,
                "status": status,
                "fingerprint": fp}

    def verdict(self, unit, events):
        return reconcile.fresh_unit_verification(
            str(self.repo), TID, unit, touched=self.TOUCHED, events=events)

    def test_partial_required_evidence_is_not_ok(self):
        # ① 2 条 required 仅 1 条有证据 → ok=False，missing 恰为未验证那条
        unit = self.multi_unit()
        fp = self.current_fingerprint(unit, self.TOUCHED)
        report = self.verdict(unit, [self.evidence(unit, fp)])  # 仅 CMD_A
        self.assertEqual(
            report,
            {"ok": False, "fingerprint": fp,
             "required": [CMD_A, CMD_B], "matched": [CMD_A],
             "missing": [CMD_B]})

    def test_other_unit_event_does_not_count(self):
        # ③ unit=other 的事件不算（H6 归属绑定）
        unit = self.multi_unit()
        fp = self.current_fingerprint(unit, self.TOUCHED)
        events = [self.evidence(unit, fp),
                  self.evidence(unit, fp, command=CMD_B, unit_id="other")]
        report = self.verdict(unit, events)
        self.assertFalse(report["ok"])
        self.assertEqual(report["matched"], [CMD_A])
        self.assertEqual(report["missing"], [CMD_B])

    def test_fail_status_does_not_count(self):
        # ④ status="fail" 不算 pass 证据
        unit = self.multi_unit()
        fp = self.current_fingerprint(unit, self.TOUCHED)
        events = [self.evidence(unit, fp),
                  self.evidence(unit, fp, command=CMD_B, status="fail")]
        report = self.verdict(unit, events)
        self.assertFalse(report["ok"])
        self.assertEqual(report["matched"], [CMD_A])
        self.assertEqual(report["missing"], [CMD_B])

    def test_stale_fingerprint_does_not_count(self):
        # ⑤ 指纹不匹配（stale）不算：事件先记、文件后改
        unit = self.multi_unit()
        fp_stale = self.current_fingerprint(unit, self.TOUCHED)
        self.write("src/multi/m.py", b"v2\n")
        events = [self.evidence(unit, fp_stale),
                  self.evidence(unit, fp_stale, command=CMD_B)]
        report = self.verdict(unit, events)
        self.assertFalse(report["ok"])
        self.assertEqual(report["matched"], [])
        self.assertEqual(report["missing"], [CMD_A, CMD_B])

    def test_command_outside_required_does_not_count(self):
        # ⑥ command 不在 required 内不算（喂饱 required 之外的命令无用）
        unit = self.multi_unit()
        fp = self.current_fingerprint(unit, self.TOUCHED)
        report = self.verdict(unit, [self.evidence(unit, fp, command=CMD_C)])
        self.assertFalse(report["ok"])
        self.assertEqual(report["matched"], [])
        self.assertEqual(report["missing"], [CMD_A, CMD_B])

    def test_legacy_event_without_unit_field_does_not_count(self):
        # ⑦ 无 unit 字段的旧格式事件不算（H6 破坏性决策不回退）
        unit = self.multi_unit()
        fp = self.current_fingerprint(unit, self.TOUCHED)
        events = [{"event": "verification", "command": CMD_A,
                   "status": "pass", "fingerprint": fp}]
        report = self.verdict(unit, events)
        self.assertFalse(report["ok"])
        self.assertEqual(report["matched"], [])

    def test_non_list_events_tolerated_as_empty(self):
        # ⑧ events 注入非 list → 按 [] 容错（全部 required 归 missing）
        unit = self.multi_unit()
        for events in ("not-a-list", {"event": "verification"}, 42):
            with self.subTest(events=events):
                report = self.verdict(unit, events)
                self.assertFalse(report["ok"])
                self.assertEqual(report["required"], [CMD_A, CMD_B])
                self.assertEqual(report["matched"], [])
                self.assertEqual(report["missing"], [CMD_A, CMD_B])

    def test_unit_without_valid_id_never_matches_legacy_events(self):
        # ⑧b 无合法 id 的单元（缺 id / None / 空串）：legacy 无 unit 字段
        # 事件的 event.get("unit")==None 不得经 None==None 匹配（审查
        # 检查点 #1 发现的形状防御缺口）——全部 required 归 missing
        unit = self.multi_unit()
        fp = self.current_fingerprint(unit, self.TOUCHED)
        for bad_unit in (dict(unit, id=None), dict(unit, id=""),
                         {"verification": unit["verification"],
                          "ownership": unit["ownership"]}):
            with self.subTest(unit=bad_unit):
                legacy = {"event": "verification", "command": CMD_A,
                          "status": "pass", "fingerprint": fp}
                report = self.verdict(bad_unit, [legacy])
                self.assertFalse(report["ok"])
                self.assertEqual(report["matched"], [])
                self.assertEqual(report["missing"], [CMD_A, CMD_B])


class FreshUnitVerificationFastPathTest(TempDirFixture):
    """空 required 快路径：无 git 的普通 tempdir 可调用（零 git 零读盘）。"""

    def test_empty_required_returns_ok_without_git(self):
        # ② verification 为空 → 平凡 ok；touched / events 缺省也不碰 git
        # （若误触缺省通道，git_touched_files 会在非 git tempdir 上抛
        # OwnershipError，本用例即失败——快路径性质的机械证明）
        unit = make_unit("nofix", ("src/nofix/**",), status="completed")
        result = reconcile.fresh_unit_verification(str(self.repo), TID, unit)
        self.assertEqual(
            result,
            {"ok": True, "fingerprint": None, "required": [],
             "matched": [], "missing": []})

    def test_non_list_required_tolerated_as_empty_fast_path(self):
        # required 非 list/tuple（缺字段 / 标量）同走快路径，不碰 git
        raw_unit = {"id": "raw-legacy", "ownership": ["src/raw/**"],
                    "status": "running"}
        result = reconcile.fresh_unit_verification(
            str(self.repo), TID, raw_unit, touched=["src/raw/x.py"],
            events=[{"event": "verification"}])
        self.assertTrue(result["ok"])
        self.assertEqual(result["required"], [])

    def test_non_empty_required_without_git_raises_ownership_error(self):
        # 对照：required 非空才走缺省 touched 通道 → 非 git 仓库上抛
        unit = make_unit("g", ("src/g/**",), status="running",
                         verification=(CMD_A,))
        with self.assertRaises(ownership.OwnershipError):
            reconcile.fresh_unit_verification(str(self.repo), TID, unit)


# —— WU-P1：恢复集成（D2 收紧经 reconcile_interrupted 生效） ——

@unittest.skipUnless(shutil.which("git"), "环境无 git 可执行，跳过 git fixture 测试")
class ReconcileAllMatchRecoveryTest(ReconcileFixture):
    """多 command 单元部分证据不得 completed（D2 收紧）：
    running → verifying（非 completed）、verifying → stay advisory；
    全部 required 各有新鲜证据才恢复 completed 建议。"""

    TOUCHED = ["src/duo/d.py"]

    def duo_units(self):
        """同 ownership 的 running / verifying 双单元（各双 required）。"""
        self.write("src/duo/d.py", b"v1\n")
        running = make_unit("duo-run", ("src/duo/**",), status="running",
                            verification=(CMD_A, CMD_B))
        verifying = make_unit("duo-ver", ("src/duo/**",), status="verifying",
                              verification=(CMD_A, CMD_B))
        return running, verifying

    def test_partial_evidence_no_longer_suggests_completed(self):
        # ⑨ 仅 CMD_A 有证据 → running 得 verifying、verifying 得 stay
        running, verifying = self.duo_units()
        fp = self.current_fingerprint(running, self.TOUCHED)
        events = [self.verification_event(running, fp)]  # 夹具默认首 command
        events.append({"event": "verification", "unit": "duo-ver",
                       "command": CMD_A, "status": "pass",
                       "fingerprint": fp})
        report = reconcile.reconcile_interrupted(
            str(self.repo), TID, [running, verifying],
            touched=self.TOUCHED, events=events)
        self.assertEqual(report["suggestions"], {
            "duo-run": {"to": "verifying",
                        "reason": "残留改动无新鲜验证证据：主会话必须"
                                  "亲自检查 diff 并运行单元验证"}})
        self.assertEqual(report["advisories"], {
            "duo-ver": {"reason": "verifying 保持现状：主会话直接运行"
                                  "单元验证后完成或失败"}})
        self.assertEqual(report["reconciled"], ["duo-run", "duo-ver"])

    def test_full_evidence_still_suggests_completed(self):
        # ⑩ 全部 required 各有新鲜证据 → 双双 completed（D2 全量口径）
        running, verifying = self.duo_units()
        fp = self.current_fingerprint(running, self.TOUCHED)
        events = []
        for unit in (running, verifying):
            for command in (CMD_A, CMD_B):
                events.append({"event": "verification",
                               "unit": unit["id"], "command": command,
                               "status": "pass", "fingerprint": fp})
        report = reconcile.reconcile_interrupted(
            str(self.repo), TID, [running, verifying],
            touched=self.TOUCHED, events=events)
        self.assertEqual(report["suggestions"]["duo-run"]["to"], "completed")
        self.assertEqual(report["suggestions"]["duo-ver"]["to"], "completed")
        self.assertEqual(report["advisories"], {})


# —— 纯函数：建议 / 咨询键序按 units 出现序 ——

@unittest.skipUnless(shutil.which("git"), "环境无 git 可执行，跳过 git fixture 测试")
class ReconcileSuggestionOrderTest(GitRepoFixture):
    """suggestions / advisories / reconciled 键序按 units 出现序（非 id 排序序）。"""

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
        # zulu（running 有残留无证据）→ suggestions；alpha（verifying
        # 无残留）→ advisories（verifying→ready 不在 §62 转换表内）；
        # reconciled 含全部被评估单元，键序均按出现序
        self.assertEqual(list(report["suggestions"]), ["zulu"])
        self.assertEqual(list(report["advisories"]), ["alpha"])
        self.assertEqual(report["reconciled"], ["zulu", "alpha"])
        self.assertEqual(report["suggestions"]["zulu"]["to"], "verifying")
        self.assertEqual(report["advisories"]["alpha"],
                         {"reason": "verifying 无残留改动且无新鲜验证"
                                    "证据：主会话裁决——重跑验证或按"
                                    "失败处理"})
        self.assertNotIn("to", report["advisories"]["alpha"])


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


# —— H5：reconcile_leases 租约三分对账（纯建议、零落盘、无 git 需求） ——

LEASE_T0 = "2026-01-01T00:00:00.000Z"
LEASE_NOW = "2026-06-01T00:00:00.000Z"
LEASE_FUTURE = "2099-01-01T00:00:00.000Z"
LEASE_PAST = "2020-01-01T00:00:00.000Z"


class ReconcileLeasesTest(TempDirFixture):
    """reconcile_leases 三分裁决：tempdir + 手工落盘租约，无 git 需求。"""

    def setUp(self):
        super().setUp()
        self.units = [
            make_unit("u1", ("src/a/**",), status="completed"),
            make_unit("u2", ("src/b/**",), status="running"),
            make_unit("u3", ("src/c/**",), status="verifying"),
            make_unit("u4", ("src/d/**",), status="ready"),
        ]

    def _write_lease_map(self, mapping):
        path = lease_mod.lease_path(self.repo, TID)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(mapping, ensure_ascii=False, indent=2,
                                sort_keys=True))

    def _record(self, owner, expires_at=None):
        record = {"owner": owner, "acquired_at": LEASE_T0}
        if expires_at is not None:
            record["expires_at"] = expires_at
        return record

    def test_completed_owner_is_stale(self):
        self._write_lease_map(
            {"src/a/**": self._record("u1", LEASE_FUTURE)})
        report = reconcile.reconcile_leases(self.repo, TID, self.units,
                                            now=LEASE_NOW)
        # 单元在非活跃写相（completed）→ stale，reason 注明归属状态
        self.assertEqual(report["stale"],
                         [{"path": "src/a/**", "owner": "u1",
                           "reason": "归属单元状态为 completed"
                                     "（非活跃写相）"}])
        self.assertEqual(report["active"], [])
        self.assertEqual(report["expired_running"], [])

    def test_owner_absent_from_graph_is_stale(self):
        self._write_lease_map({"src/g.ts": self._record("ghost")})
        report = reconcile.reconcile_leases(self.repo, TID, self.units,
                                            now=LEASE_NOW)
        self.assertEqual(len(report["stale"]), 1)
        self.assertEqual(report["stale"][0]["path"], "src/g.ts")
        self.assertEqual(report["stale"][0]["owner"], "ghost")
        self.assertEqual(report["stale"][0]["reason"], "图中无此单元")

    def test_ready_owner_is_stale(self):
        # ready 未进入活跃写相：孤儿租约可见（端到端崩溃场景的裁决依据）
        self._write_lease_map({"src/d/**": self._record("u4", LEASE_FUTURE)})
        report = reconcile.reconcile_leases(self.repo, TID, self.units,
                                            now=LEASE_NOW)
        self.assertEqual(len(report["stale"]), 1)
        self.assertIn("ready", report["stale"][0]["reason"])

    def test_running_and_verifying_unexpired_are_active(self):
        self._write_lease_map({
            "src/c/**": self._record("u3", LEASE_FUTURE),
            "src/b/**": self._record("u2", LEASE_FUTURE)})
        report = reconcile.reconcile_leases(self.repo, TID, self.units,
                                            now=LEASE_NOW)
        # 桶内按 path 排序（确定性）
        self.assertEqual(report["active"],
                         [{"path": "src/b/**", "owner": "u2"},
                          {"path": "src/c/**", "owner": "u3"}])
        self.assertEqual(report["stale"], [])
        self.assertEqual(report["expired_running"], [])

    def test_running_expired_goes_to_expired_running_not_stale(self):
        # running 但已过期：worker 可能仍在写 → 不入 stale（不自动清）
        self._write_lease_map(
            {"src/b/**": self._record("u2", LEASE_PAST)})
        report = reconcile.reconcile_leases(self.repo, TID, self.units,
                                            now=LEASE_NOW)
        self.assertEqual(report["stale"], [])
        self.assertEqual(report["active"], [])
        self.assertEqual(report["expired_running"],
                         [{"path": "src/b/**", "owner": "u2",
                           "expires_at": LEASE_PAST}])

    def test_buckets_sorted_and_report_shape(self):
        self._write_lease_map({
            "src/z.ts": self._record("ghost"),
            "src/a/**": self._record("u2", LEASE_PAST),
            "src/b/**": self._record("u2", LEASE_PAST),
            "src/c/**": self._record("u3", LEASE_FUTURE)})
        report = reconcile.reconcile_leases(self.repo, TID, self.units,
                                            now=LEASE_NOW)
        self.assertEqual(set(report),
                         {"stale", "active", "expired_running"})
        self.assertEqual([item["path"] for item in report["stale"]],
                         ["src/z.ts"])
        self.assertEqual([item["path"] for item in report["active"]],
                         ["src/c/**"])
        # expired_running 按桶内 path 排序
        self.assertEqual([item["path"] for item in
                          report["expired_running"]],
                         ["src/a/**", "src/b/**"])

    def test_no_leases_yields_empty_report(self):
        report = reconcile.reconcile_leases(self.repo, TID, self.units)
        self.assertEqual(report, {"stale": [], "active": [],
                                  "expired_running": []})

    def test_pure_advisory_no_disk_write_no_input_mutation(self):
        self._write_lease_map(
            {"src/b/**": self._record("u2", LEASE_FUTURE)})
        lease_path = lease_mod.lease_path(self.repo, TID)
        raw_before = lease_path.read_bytes()
        units_before = copy.deepcopy(self.units)
        journal_mod.append_event(self.repo, TID,
                                 {"event": "verification"})
        events_before = journal_mod.read_events(self.repo, TID)
        reconcile.reconcile_leases(self.repo, TID, self.units,
                                   now=LEASE_NOW)
        # 零落盘（租约与 journal 字节不变）、不修改传入 units
        self.assertEqual(lease_path.read_bytes(), raw_before)
        self.assertEqual(journal_mod.read_events(self.repo, TID),
                         events_before)
        self.assertEqual(self.units, units_before)

    def test_non_list_units_tolerated_as_empty_graph(self):
        self._write_lease_map(
            {"src/g.ts": self._record("u2", LEASE_FUTURE)})
        # units 非 list → 空图 → 全部 stale（图中无此单元）
        report = reconcile.reconcile_leases(self.repo, TID, None,
                                            now=LEASE_NOW)
        self.assertEqual(len(report["stale"]), 1)
        self.assertEqual(report["stale"][0]["reason"], "图中无此单元")


# —— H6：验证证据归属绑定（unit 字段逐字精确匹配；指纹真算需 git 基座） ——

@unittest.skipUnless(shutil.which("git"), "环境无 git 可执行，跳过 git fixture 测试")
class ReconcileUnitBindingTest(GitRepoFixture):
    """work_unit_verification_evidence_requires_matching_unit_id（appendix B）：
    相同 command / 重叠 ownership 的单元之间不得复用验证证据
    （fingerprint 以真实 git 仓库为基座、按 reconcile 同一口径真算）。"""

    def setUp(self):
        super().setUp()
        self.cmd = "python3 -m unittest tests.test_feature_shared"
        self.write("src/shared/a.py", b"unit implementation\n")
        self.touched = ["src/shared/a.py"]
        # A/B 同 command、ownership 完全重叠（都声明 src/**）、都 running
        self.units = [
            make_unit("uA", ("src/**",), status="running",
                      verification=(self.cmd,)),
            make_unit("uB", ("src/**",), status="running",
                      verification=(self.cmd,)),
        ]
        # 与 reconcile 同一口径对 owned_hits 真算当前指纹
        self.fp = fingerprint_mod.compute_fingerprint(str(self.repo),
                                                      self.touched)

    def _report(self, events):
        return reconcile.reconcile_interrupted(
            str(self.repo), TID, self.units, touched=self.touched,
            events=events)

    def test_work_unit_verification_evidence_requires_matching_unit_id(self):
        evidence = {"event": "verification", "unit": "uA",
                    "command": self.cmd, "status": "pass",
                    "fingerprint": self.fp}
        report = self._report([evidence])
        # A 的证据证明 A 的改动 → completed；B 不得复用 A 的证据 → verifying
        self.assertEqual(report["suggestions"]["uA"]["to"], "completed")
        self.assertEqual(report["suggestions"]["uB"]["to"], "verifying")

    def test_unit_binding_is_symmetric(self):
        evidence = {"event": "verification", "unit": "uB",
                    "command": self.cmd, "status": "pass",
                    "fingerprint": self.fp}
        report = self._report([evidence])
        # 事件归属谁，谁才可被建议 completed（对称锚定）
        self.assertEqual(report["suggestions"]["uA"]["to"], "verifying")
        self.assertEqual(report["suggestions"]["uB"]["to"], "completed")

    def test_legacy_event_without_unit_no_longer_matches(self):
        # 旧格式（无 unit 字段）不再匹配任何单元——保守按无证据处理
        legacy = {"event": "verification", "command": self.cmd,
                  "status": "pass", "fingerprint": self.fp}
        report = self._report([legacy])
        self.assertEqual(report["suggestions"]["uA"]["to"], "verifying")
        self.assertEqual(report["suggestions"]["uB"]["to"], "verifying")


# —— v2.1 M3：reconcile_agent_run 四分分类（§9 判定树 + §7.3 僵尸语义） ——

class ReconcileAgentRunFixture(ReconcileFixture):
    """reconcile_agent_run 基座：git 仓库 + state + journal + 伪造原生
    档案根（agents_root 用仓库外独立 tempdir——档案树放进仓库会混入
    touched 清单，污染 residue 证据）。"""

    def setUp(self):
        super().setUp()
        self._native_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._native_tmp.cleanup)
        self.agents_root = Path(self._native_tmp.name)

    def save_state_raw(self, units, task_id=TID):
        """绕过 validate_state 直写 state.json（空 verification 等校验外
        形状的边界用；文件布局与 state.save_state 一致）。"""
        st = state.new_task_state(task_id, "reconcile agent run 目标",
                                  dict(ROUTE))
        st["work_units"] = units
        path = (journal_mod.journal_path(self.repo, task_id).parent
                / "state.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(str(path), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(st, ensure_ascii=False))

    def write_native_archive(self, agent_id, status):
        """伪造原生档案 <agents_root>/<主会话id>/<agentId>/metadata.json
        （白名单形状对齐 agent_run.native_agent_metadata 的读取口径）。"""
        agent_dir = self.agents_root / "session-main" / agent_id
        agent_dir.mkdir(parents=True, exist_ok=True)
        payload = {"agentId": agent_id, "status": status,
                   "childSessionId": "child-%s" % agent_id,
                   "usage": {"inputTokens": 10, "outputTokens": 5}}
        (agent_dir / "metadata.json").write_text(
            json.dumps(payload), encoding="utf-8")

    def record_launch(self, uid, agent_id, *, tool_use_id="tu-1",
                      permit_id="dp-1"):
        """经 agent_run.record_agent_launch 落一条真实 agent_launched
        （agent_id 传 None 模拟 tool_response 形状异常的派发）。"""
        return agent_run.record_agent_launch(
            self.repo, TID,
            {"tool_use_id": tool_use_id,
             "tool_response": {"agentId": agent_id} if agent_id else {}},
            permit={"unit_id": uid, "permit_id": permit_id,
                    "mode": "background"})

    def reconcile(self, uid, **kwargs):
        """驱动 reconcile_agent_run（agents_root 缺省指向伪造档案根）。"""
        kwargs.setdefault("agents_root", self.agents_root)
        return reconcile.reconcile_agent_run(str(self.repo), TID, uid,
                                             **kwargs)

    def running_unit(self, uid="wu1", owned=("src/wu1/**",)):
        return make_unit(uid, owned, status="running",
                         verification=(CMD_A,))


@unittest.skipUnless(shutil.which("git"), "环境无 git 可执行，跳过 git fixture 测试")
class ReconcileAgentRunClassificationTest(ReconcileAgentRunFixture):
    """四分判定树逐分支正例（§9 冻结树，逐条短路）+ 冻结返回形状 +
    边界（无 required 命令 / 倒序 agent_id / events 注入 / 端到端烟雾）。"""

    def test_frozen_report_shape(self):
        unit = self.running_unit()
        self.save_units([unit])
        report = self.reconcile("wu1")
        self.assertEqual(set(report),
                         {"classification", "rationale", "evidence"})
        self.assertEqual(set(report["evidence"]),
                         {"runs", "owned_residue",
                          "unattributable_residue", "verification",
                          "native"})
        self.assertEqual(report["evidence"]["owned_residue"], [])
        self.assertEqual(report["evidence"]["unattributable_residue"], [])
        self.assertEqual(report["evidence"]["native"], None)

    # —— 分支 1：找不到单元（state 缺失 / 损坏 / 无此 uid） ——

    def test_unit_not_found_yields_manual_ruling(self):
        self.save_units([self.running_unit()])
        report = self.reconcile("ghost")
        self.assertEqual(report["classification"], "manual_ruling")
        self.assertEqual(report["rationale"],
                         ["unit ghost not found in task state"])
        self.assertEqual(report["evidence"]["runs"], [])
        self.assertEqual(report["evidence"]["verification"], None)

    def test_missing_state_yields_manual_ruling(self):
        report = self.reconcile("wu1")  # state.json 从未写入
        self.assertEqual(report["classification"], "manual_ruling")
        self.assertEqual(report["rationale"],
                         ["task state missing or unreadable",
                          "unit wu1 not found in task state"])

    def test_corrupt_state_yields_manual_ruling(self):
        path = (journal_mod.journal_path(self.repo, TID).parent
                / "state.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json", encoding="utf-8")
        report = self.reconcile("wu1")
        self.assertEqual(report["classification"], "manual_ruling")
        self.assertEqual(report["rationale"],
                         ["task state missing or unreadable",
                          "unit wu1 not found in task state"])

    # —— 分支 2 / 分支 3：residue 最先分流（§7.3 repository state） ——

    def test_no_runs_no_residue_yields_redispatch_clean(self):
        unit = self.running_unit()
        self.save_units([unit])
        report = self.reconcile("wu1")
        # 分支 2 先于分支 4 短路：工作区虽干净（verification 谓词仍被
        # 求值并进入 evidence），无执行内容即全新派发
        self.assertEqual(report["classification"], "redispatch_clean")
        self.assertEqual(report["rationale"], ["no runs, no residue"])
        self.assertEqual(report["evidence"]["runs"], [])
        self.assertEqual(report["evidence"]["verification"]["required"],
                         [CMD_A])

    def test_unattributable_edits_yield_manual_ruling(self):
        unit = self.running_unit()
        self.save_units([unit])
        self.write("src/rogue/x.py", b"rogue\n")  # ownership 声明之外
        report = self.reconcile("wu1")
        self.assertEqual(report["classification"], "manual_ruling")
        self.assertEqual(report["rationale"],
                         ["unattributable edits present"])
        self.assertEqual(report["evidence"]["unattributable_residue"],
                         ["src/rogue/x.py"])
        self.assertEqual(report["evidence"]["owned_residue"], [])

    # —— 分支 4：新鲜 pass 证据 + 档案终态 / 无档案 → reuse ——

    def _reuse_scenario(self, status):
        """reuse 前置：owned 残留 + launch 账本 + 档案 + 新鲜验证事件。"""
        unit = self.running_unit()
        self.save_units([unit])
        self.write("src/wu1/a.py", b"a-v1\n")
        self.record_launch("wu1", "agent-1")
        self.write_native_archive("agent-1", status)
        fp = self.current_fingerprint(unit)
        self.record_event(self.verification_event(unit, fp))
        return unit, fp

    def test_fresh_evidence_completed_native_yields_reuse_result(self):
        unit, fp = self._reuse_scenario("completed")
        report = self.reconcile("wu1")
        self.assertEqual(report["classification"], "reuse_result")
        self.assertEqual(report["rationale"],
                         ["agent archived terminal + fresh evidence"])
        self.assertEqual(report["evidence"]["verification"],
                         {"ok": True, "fingerprint": fp,
                          "required": [CMD_A], "matched": [CMD_A],
                          "missing": []})
        self.assertEqual(report["evidence"]["native"]["observed_status"],
                         "completed")
        self.assertEqual(report["evidence"]["owned_residue"],
                         ["src/wu1/a.py"])
        self.assertEqual(len(report["evidence"]["runs"]), 1)

    def test_fresh_evidence_stopped_native_yields_reuse_result(self):
        self._reuse_scenario("stopped")
        report = self.reconcile("wu1")
        self.assertEqual(report["classification"], "reuse_result")
        self.assertEqual(report["evidence"]["native"]["observed_status"],
                         "stopped")

    def test_fresh_evidence_without_archive_yields_reuse_result(self):
        unit = self.running_unit()
        self.save_units([unit])
        self.write("src/wu1/a.py", b"a-v1\n")
        self.record_launch("wu1", "agent-1")  # 档案缺失 → native None
        fp = self.current_fingerprint(unit)
        self.record_event(self.verification_event(unit, fp))
        report = self.reconcile("wu1")
        self.assertEqual(report["classification"], "reuse_result")
        self.assertEqual(report["evidence"]["native"], None)

    def test_fresh_evidence_failed_native_yields_manual_conflict(self):
        self._reuse_scenario("failed")
        report = self.reconcile("wu1")
        self.assertEqual(report["classification"], "manual_ruling")
        self.assertEqual(report["rationale"],
                         ["transcript/evidence conflict"])

    # —— 分支 5：有执行内容、无完整 pass 证据 → resume_with_progress ——

    def test_zombie_native_partial_work_yields_resume_with_zombie_note(self):
        unit = self.running_unit()
        self.save_units([unit])
        self.write("src/wu1/a.py", b"partial\n")
        self.record_launch("wu1", "agent-1")
        self.write_native_archive("agent-1", "running")  # 僵尸态档案
        report = self.reconcile("wu1")
        self.assertEqual(report["classification"],
                         "resume_with_progress")
        self.assertEqual(report["rationale"],
                         ["partial work present, no fresh pass evidence",
                          "metadata running is not truth (zombie-aware)"])
        self.assertFalse(report["evidence"]["verification"]["ok"])
        self.assertEqual(report["evidence"]["native"]["observed_status"],
                         "running")

    def test_partial_work_terminal_native_yields_resume_without_zombie(self):
        unit = self.running_unit()
        self.save_units([unit])
        self.write("src/wu1/a.py", b"partial\n")
        self.record_launch("wu1", "agent-1")
        self.write_native_archive("agent-1", "completed")
        report = self.reconcile("wu1")  # 无任何验证证据
        self.assertEqual(report["classification"], "resume_with_progress")
        # 档案 completed 是终态而非僵尸态：冻结树把僵尸句只绑在
        # observed running 上，此处 rationale 不含僵尸句
        self.assertEqual(report["rationale"],
                         ["partial work present, no fresh pass evidence"])

    def test_owned_residue_without_runs_yields_resume(self):
        unit = self.running_unit()
        self.save_units([unit])
        self.write("src/wu1/a.py", b"partial\n")
        report = self.reconcile("wu1")  # 无 launch 账本、无档案、无证据
        self.assertEqual(report["classification"], "resume_with_progress")
        self.assertEqual(report["evidence"]["runs"], [])
        self.assertEqual(report["evidence"]["native"], None)

    # —— 分支 6：证据与档案张力（verification ok + 档案非终态） →
    #     manual_ruling 保守兜底 ——

    def test_fresh_evidence_zombie_native_yields_conservative_manual(self):
        unit = self.running_unit()
        self.save_units([unit])
        self.write("src/wu1/a.py", b"a-v1\n")
        self.record_launch("wu1", "agent-1")
        self.write_native_archive("agent-1", "running")
        fp = self.current_fingerprint(unit)
        self.record_event(self.verification_event(unit, fp))
        report = self.reconcile("wu1")
        # 证据齐但档案 possibly running：不凭证据判 reuse、不凭档案判
        # 存活——禁止自动猜测（§9.4），裁决归主会话
        self.assertEqual(report["classification"], "manual_ruling")
        self.assertEqual(report["rationale"],
                         ["fresh pass evidence present",
                          "metadata running is not truth (zombie-aware)",
                          "unresolved evidence combination; manual ruling"])

    # —— 边界：无 required 命令 → verification None → 走 5 不误判 4 ——

    def test_unit_without_required_commands_verification_is_none_resume(self):
        unit = make_unit("nofix", ("src/nofix/**",), status="running",
                         verification=())
        self.save_state_raw([unit])  # 空 verification 绕过校验直写
        self.write("src/nofix/n.py", b"partial\n")
        report = self.reconcile("nofix")
        self.assertEqual(report["evidence"]["verification"], None)
        self.assertEqual(report["classification"], "resume_with_progress")
        self.assertEqual(report["rationale"],
                         ["partial work present, no fresh pass evidence"])

    # —— 边界：native 取最后一个非空 agent_id（倒序扫描） ——

    def test_native_uses_last_non_empty_agent_id(self):
        unit = self.running_unit()
        self.save_units([unit])
        self.record_launch("wu1", "agent-1", tool_use_id="tu-1")
        self.record_launch("wu1", None, tool_use_id="tu-2")
        self.write_native_archive("agent-1", "running")
        report = self.reconcile("wu1")
        # 末次派发的 agent_id 提取失败 → 倒序回退更早的已知档案句柄
        self.assertEqual(report["evidence"]["native"]["agent_id"],
                         "agent-1")
        self.assertEqual(report["evidence"]["native"]["observed_status"],
                         "running")

    def test_runs_filtered_to_requested_unit(self):
        unit_a = self.running_unit("wu1", ("src/wu1/**",))
        unit_b = self.running_unit("wu2", ("src/wu2/**",))
        self.save_units([unit_a, unit_b])
        self.record_launch("wu1", "agent-1", tool_use_id="tu-1")
        self.record_launch("wu2", "agent-2", tool_use_id="tu-2")
        self.write_native_archive("agent-1", "completed")
        self.write_native_archive("agent-2", "running")
        report = self.reconcile("wu1")
        self.assertEqual([run["unit"]
                          for run in report["evidence"]["runs"]], ["wu1"])
        # native 只对 wu1 的最后 agent_id 取档案，不串到 wu2 的 run
        self.assertEqual(report["evidence"]["native"]["agent_id"],
                         "agent-1")
        self.assertEqual(report["evidence"]["native"]["observed_status"],
                         "completed")

    # —— 边界：events 注入只作用于验证证据谓词 ——

    def test_events_injection_controls_verification_only(self):
        unit = self.running_unit()
        self.save_units([unit])
        self.write("src/wu1/a.py", b"a-v1\n")
        self.record_launch("wu1", "agent-1")
        self.write_native_archive("agent-1", "completed")
        fp = self.current_fingerprint(unit)
        # 注入新鲜证据（不落 journal）→ reuse_result
        report = self.reconcile(
            "wu1", events=[self.verification_event(unit, fp)])
        self.assertEqual(report["classification"], "reuse_result")
        # 注入坏形状（非 list）→ 谓词按 [] 容错 → 无证据 → resume
        report_bad = self.reconcile("wu1", events=42)
        self.assertEqual(report_bad["classification"],
                         "resume_with_progress")

    # —— 端到端烟雾（VERIFICATION 场景镜像） ——

    def test_smoke_launch_completed_archive_then_fresh_evidence(self):
        """running 单元 + record_agent_launch + completed 档案 + owned
        残留 + 无验证证据 → resume_with_progress（档案 completed 是
        终态、非 running，冻结树把僵尸句只绑在 observed running 上）；
        补新鲜 pass 证据 → reuse_result。"""
        unit = self.running_unit()
        self.save_units([unit])
        self.write("src/wu1/a.py", b"partial\n")
        self.record_launch("wu1", "agent-1")
        self.write_native_archive("agent-1", "completed")
        report = self.reconcile("wu1")
        self.assertEqual(report["classification"], "resume_with_progress")
        self.assertEqual(report["rationale"],
                         ["partial work present, no fresh pass evidence"])
        fp = self.current_fingerprint(unit)
        self.record_event(self.verification_event(unit, fp))
        report2 = self.reconcile("wu1")
        self.assertEqual(report2["classification"], "reuse_result")
        self.assertEqual(report2["rationale"],
                         ["agent archived terminal + fresh evidence"])


@unittest.skipUnless(shutil.which("git"), "环境无 git 可执行，跳过 git fixture 测试")
class ReconcileAgentRunPurityTest(ReconcileAgentRunFixture):
    """reconcile_agent_run 纯读：零落盘（任务目录文件集与 state /
    journal 字节均不变）。"""

    def test_no_disk_writes(self):
        unit = self.running_unit()
        self.save_units([unit])
        self.write("src/wu1/a.py", b"partial\n")
        self.record_launch("wu1", "agent-1")
        self.write_native_archive("agent-1", "running")
        task_dir = journal_mod.journal_path(self.repo, TID).parent
        state_path = task_dir / "state.json"
        journal_file = journal_mod.journal_path(self.repo, TID)
        files_before = sorted(entry.name for entry in task_dir.iterdir())
        state_before = state_path.read_bytes()
        journal_before = journal_file.read_bytes()
        self.reconcile("wu1")
        self.assertEqual(sorted(entry.name
                                for entry in task_dir.iterdir()),
                         files_before)
        self.assertEqual(state_path.read_bytes(), state_before)
        self.assertEqual(journal_file.read_bytes(), journal_before)


class ReconcileAgentRunNonGitTest(TempDirFixture):
    """非 git 目录容错（判定树分支 1b）：决策 API 不上抛求值异常——
    manual_ruling + rationale 注明求值失败，residue 按无 residue 处理，
    返回形状照旧。无 git 需求（tempdir 基座 + 原始写入 state.json）。"""

    def test_non_git_repo_yields_manual_ruling_with_evaluation_note(self):
        unit = make_unit("wu1", ("src/wu1/**",), status="running",
                         verification=(CMD_A,))
        st = state.new_task_state(TID, "非 git 容错", dict(ROUTE))
        st["work_units"] = [unit]
        path = (journal_mod.journal_path(self.repo, TID).parent
                / "state.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(str(path), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(st, ensure_ascii=False))
        report = reconcile.reconcile_agent_run(str(self.repo), TID, "wu1")
        self.assertEqual(report["classification"], "manual_ruling")
        self.assertEqual(report["rationale"][0],
                         "repository evaluation failed: OwnershipError")
        self.assertEqual(report["evidence"]["runs"], [])
        self.assertEqual(report["evidence"]["owned_residue"], [])
        self.assertEqual(report["evidence"]["unattributable_residue"], [])
        self.assertEqual(report["evidence"]["verification"], None)
        self.assertEqual(report["evidence"]["native"], None)


if __name__ == "__main__":
    unittest.main()
