#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hooks.stop_gate 第 0 位 continuity health 检查测试（v2.2 C8，修正计划
§C8/§15/§22.5，工作单元 wu-22-C8）。

被测对象：plugins/glm-conductor/hooks/stop_gate.py 新增的
evaluate_continuity_health（纯读触发域检查）及其在 evaluate_task 的
第 0 位接线。两层验证：
  - 直测层（ContinuityHealthDirectTest，无需 git）：触发域冻结边界、
    三检查矩阵各分支、obligation 短路两向、watcher 健康注记 reason、
    检查自身异常 fail-open（continuity_evaluation_error）、docstring
    的 parent_automation_id 消费警示（C6 review P3-3 落地锚定）；
  - 门冒烟层（子进程运行 stop_gate.py，git 夹具对齐
    tests/test_stop_gate.py）：触发域边界零介入（NORMAL executing /
    manual / notify / foreground / BLOCKED 相）、waiting_quota /
    waiting_user / PRESSURE / DRAINING 触发、trio 各分支的 block /
    degraded 行为、journal gate_blocked / gate_degraded 事件的结构化
    词汇精确断言、gate_exhausted 机器复用防死锁（双 unknown 连续
    block 后降级放行，INV-22-PB-07）、watcher 注记永不独立 block。

零网络、零宿主 Cron* 调用；state / checkpoint.md / manifest.json /
watcher.json 全部由夹具注入。仅 Python 3 标准库（unittest +
subprocess + tempfile），环境无 git 可执行时门冒烟层自动 skipTest。

运行：
    cd <repo_root> && python3 -m unittest tests.test_stop_gate_continuation -v
"""

import datetime
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "glm-conductor"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "glm-conductor" / "hooks"))
from runtime import execution_policy as policy_mod
from runtime import journal as journal_mod
from runtime import resume_manifest
from runtime import state
from runtime.quota import watcher_store
import stop_gate
from stop_gate import evaluate_continuity_health

# 被测钩子脚本：仓库根 plugins/glm-conductor/hooks/stop_gate.py
STOP_GATE = (Path(__file__).resolve().parents[1]
             / "plugins/glm-conductor/hooks/stop_gate.py")

TID = "cont-task-1a2b3c"
# 触发域路由：resumable 连续性（域条件之一）
ROUTE_RESUMABLE = {"mode": "solo", "delegability": "low",
                   "assurance": "standard", "executor": "main",
                   "continuity": "resumable"}
# 域外对照路由：foreground（§32.2 warm-only 语义的路由面）
ROUTE_FOREGROUND = {"mode": "solo", "delegability": "low",
                    "assurance": "standard", "executor": "main",
                    "continuity": "foreground"}
# §10.1 形状的合法 epoch_id（"glm:"+16 位十六进制）
EPOCH_ID = "glm:0123456789abcdef"


def run_gate(stdin_text, project_dir):
    """以子进程运行 stop_gate.py，返回 CompletedProcess。

    text=True + 显式 encoding="utf-8"（errors="replace" 兜底）：钩子按
    运行时契约恒以 UTF-8 字节写 stdout / stderr，父进程必须按 UTF-8
    解码（与 tests/test_stop_gate.run_gate 同款）；ZCODE_PROJECT_DIR
    指向被检仓库。
    """
    return subprocess.run(
        [sys.executable, str(STOP_GATE)],
        input=stdin_text, text=True, capture_output=True,
        encoding="utf-8", errors="replace",
        env=dict(os.environ, ZCODE_PROJECT_DIR=str(project_dir)))


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


def iso_z(moment):
    """UTC 时刻 → ISO8601 毫秒 Z 形态（watcher heartbeat_at 注入口径）。"""
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % (
        moment.microsecond // 1000)


def make_continuation(obligation="none", bridge_status=None,
                      scheduler=None, automation_id="auto-bridge-001"):
    """构造合法 continuation 块（default_continuation 之上做定点覆盖，
    形状过 validate_state 规则 8.8 的词汇 / ISO 闸）。"""
    cont = state.default_continuation()
    cont["obligation"] = obligation
    if bridge_status is not None:
        cont["wake_bridge"]["status"] = bridge_status
        cont["wake_bridge"]["automation_id"] = automation_id
    if scheduler:
        cont["scheduler_context"].update(scheduler)
    return cont


def make_subscription(enabled=True, registered=EPOCH_ID):
    """构造 quota_subscription 块（default 之上做定点覆盖）。"""
    sub = state.default_quota_subscription()
    sub["enabled"] = enabled
    sub["registered_epoch_id"] = registered
    return sub


class TempDirFixture(unittest.TestCase):
    """tempdir 基座：目录 / 状态文件写入 + Windows .git 只读位清理。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._force_cleanup)
        self.repo = Path(self._tmp.name)

    def _force_cleanup(self):
        """解除 git 只读对象后清理临时目录（对齐 tests/test_stop_gate）。"""
        git_dirs = []
        for dirpath, dirnames, _filenames in os.walk(self._tmp.name):
            if ".git" in dirnames:
                git_dirs.append(os.path.join(dirpath, ".git"))
        for git_dir in git_dirs:
            for sub_dirpath, _sub_dirnames, filenames in os.walk(git_dir):
                for name in filenames:
                    try:
                        os.chmod(os.path.join(sub_dirpath, name),
                                 stat.S_IWRITE)
                    except OSError:
                        pass
        self._tmp.cleanup()

    def write(self, rel, data):
        """在 fixture 仓库内写一个文件（自动建父目录）。"""
        target = self.repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    def journal_events(self, task_id=TID):
        """读取任务 journal 全部事件（无文件返回 []）。"""
        return journal_mod.read_events(self.repo, task_id)

    # —— continuity 夹具文件写入（直测层 / 门冒烟层共用；纯文件注入，
    #    不依赖 git） ——

    def write_checkpoint(self, task_id=TID):
        """写任务 checkpoint.md（handoff durable 第一半）。"""
        path = state.task_dir(self.repo, task_id) / "checkpoint.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# CONTINUITY CHECKPOINT\n", encoding="utf-8")

    def write_manifest(self, task_id=TID):
        """写 resume manifest.json（handoff durable 第二半；只验存在性）。"""
        path = resume_manifest.resume_manifest_path(self.repo, task_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"task_id": task_id}), encoding="utf-8")

    def make_handoff_durable(self, task_id=TID):
        """checkpoint + manifest 双双就位。"""
        self.write_checkpoint(task_id)
        self.write_manifest(task_id)

    def write_watcher_record(self, heartbeat_at):
        """直写 watcher.json（heartbeat_at 为 ISO Z 串；pid=None 不参与
        健康判定——注记只看 heartbeat 新鲜度）。"""
        record = {
            "schema_version": 1,
            "provider_identity_hash": "0123456789abcdef",
            "mode": "ACTIVE",
            "pid": None,
            "started_at": heartbeat_at,
            "heartbeat_at": heartbeat_at,
            "generation": 1,
            "stop_requested": False,
            "last_observation": None,
        }
        watcher_store.write_watcher_state(str(self.repo), record)

    def write_fresh_watcher(self):
        """心跳 = 当前时刻（新鲜，180s 阈值内）。"""
        self.write_watcher_record(
            iso_z(datetime.datetime.now(datetime.timezone.utc)))

    def write_stale_watcher(self):
        """心跳 = 一小时前（超过 180s 阈值 → continuity_watcher_stale）。"""
        old = (datetime.datetime.now(datetime.timezone.utc)
               - datetime.timedelta(hours=1))
        self.write_watcher_record(iso_z(old))


class GitRepoFixture(TempDirFixture):
    """tempdir 内真实 git 仓库基座（门冒烟层的求值根；账本根 = git 根）。

    与 tests/test_stop_gate.GitRepoFixture 同构：.gitignore 排除
    .glm-conductor/，base.txt 基线提交，src/owned/a.ts 为 owned 内
    未跟踪改动（声明 src/owned/** 的任务 ownership 检查天然通过，
    保证门冒烟只观察 continuity 第 0 位检查的行为）。
    """

    def setUp(self):
        super().setUp()
        run_git(self.repo, "init")
        run_git(self.repo, "config", "user.email", "gate@example.com")
        run_git(self.repo, "config", "user.name", "Continuity Gate")
        run_git(self.repo, "config", "core.autocrlf", "false")
        self.write(".gitignore", b".glm-conductor/\n")
        self.write("base.txt", b"v1\n")
        self.write("src/owned/a.ts", b"owned\n")
        run_git(self.repo, "add", ".")
        run_git(self.repo, "commit", "-m", "init")

    # —— continuity 任务夹具 ——

    def add_continuity_task(self, task_id=TID, status="waiting_quota",
                            auto_resume="auto_once", route=None,
                            execution_phase=None, continuation=None,
                            subscription=None,
                            ownership_files=("src/owned/**",)):
        """写入一个可定制的任务 state.json，返回落盘 state dict。

        auto_resume=None 表示保持默认 policy（manual）；非 None 时经
        set_resume_authorization 写入（auto 家族要求 source="user"，
        窗口数按词汇耦合 auto_once=1 / until_done=2）。execution_phase
        非 None 时注入 quota.execution_phase（validate_state 对 quota
        块只做形状校验，未知子键合法）。
        """
        st = state.new_task_state(
            task_id, "continuity health 冒烟目标",
            dict(route or ROUTE_RESUMABLE), ownership_files=ownership_files,
            status=status)
        if auto_resume is not None:
            # §5.4 授权耦合：manual/notify → 0；auto_once → 1；until_done ≥ 1
            windows = {"manual": 0, "notify": 0,
                       "auto_once": 1, "until_done": 2}[auto_resume]
            st["execution_policy"] = policy_mod.set_resume_authorization(
                st["execution_policy"], auto_resume=auto_resume,
                max_quota_windows=windows,
                source="user", confirmed_at="2026-09-01T00:00:00Z")
        if execution_phase is not None:
            st["quota"] = {"execution_phase": execution_phase}
        if continuation is not None:
            st["continuation"] = continuation
        if subscription is not None:
            st["quota_subscription"] = subscription
        state.save_state(self.repo, st)
        return st


# —— 直测层：触发域 / trio 矩阵 / 短路 / watcher / fail-open / docstring ——

class ContinuityHealthDirectTest(TempDirFixture):
    """evaluate_continuity_health 直测（无需 git；transport 步骤按需把
    state 落盘——activation_transport_status 经账本根读 state.json）。"""

    def _resumable_state(self, status="waiting_quota", auto_resume="auto_once",
                         route=None, execution_phase=None,
                         continuation=None, subscription=None):
        st = state.new_task_state(
            TID, "direct", dict(route or ROUTE_RESUMABLE),
            ownership_files=["src/**"], status=status)
        if auto_resume is not None:
            # §5.4 授权耦合：manual/notify → 0；auto_once → 1；until_done ≥ 1
            windows = {"manual": 0, "notify": 0,
                       "auto_once": 1, "until_done": 2}[auto_resume]
            st["execution_policy"] = policy_mod.set_resume_authorization(
                st["execution_policy"], auto_resume=auto_resume,
                max_quota_windows=windows,
                source="user", confirmed_at="2026-09-01T00:00:00Z")
        if execution_phase is not None:
            st["quota"] = {"execution_phase": execution_phase}
        if continuation is not None:
            st["continuation"] = continuation
        if subscription is not None:
            st["quota_subscription"] = subscription
        return st

    # —— 触发域冻结边界（域外一律 None，零介入） ——
    def test_outside_domain_returns_none(self):
        cases = {
            "normal executing": self._resumable_state(status="executing"),
            "manual auto_resume": self._resumable_state(
                auto_resume="manual"),
            "notify auto_resume": self._resumable_state(
                auto_resume="notify"),
            "foreground route": self._resumable_state(
                route=ROUTE_FOREGROUND),
            "terminal completed": self._resumable_state(status="completed"),
            "terminal cancelled": self._resumable_state(status="cancelled"),
            "blocked phase": self._resumable_state(
                status="executing", execution_phase="BLOCKED"),
        }
        for name, st in cases.items():
            with self.subTest(case=name):
                self.assertIsNone(
                    evaluate_continuity_health(str(self.repo), TID, st))

    def test_waiting_statuses_trigger_handoff_block(self):
        for status in ("waiting_quota", "waiting_user"):
            with self.subTest(status=status):
                st = self._resumable_state(status=status)
                outcome = evaluate_continuity_health(
                    str(self.repo), TID, st)
                self.assertEqual(outcome["outcome"], "block")
                self.assertEqual(outcome["check"],
                                 "continuity_handoff_missing")
                self.assertEqual(outcome["detail"]["missing"],
                                 ["checkpoint.md", "manifest.json"])

    def test_pressure_and_draining_phases_trigger(self):
        for phase in ("PRESSURE", "DRAINING"):
            with self.subTest(phase=phase):
                st = self._resumable_state(status="executing",
                                           execution_phase=phase)
                outcome = evaluate_continuity_health(
                    str(self.repo), TID, st)
                self.assertEqual(outcome["outcome"], "block")
                self.assertEqual(outcome["check"],
                                 "continuity_handoff_missing")

    # —— trio 1/2：handoff 与 subscription 的 block 方向 ——
    def test_checkpoint_only_still_blocks_with_manifest_missing(self):
        self.write_checkpoint()
        st = self._resumable_state()
        outcome = evaluate_continuity_health(str(self.repo), TID, st)
        self.assertEqual(outcome["check"], "continuity_handoff_missing")
        self.assertEqual(outcome["detail"]["missing"], ["manifest.json"])

    def test_subscription_missing_blocks_after_handoff_ok(self):
        self.make_handoff_durable()
        st = self._resumable_state()  # 默认未订阅（enabled=False）
        outcome = evaluate_continuity_health(str(self.repo), TID, st)
        self.assertEqual(outcome["outcome"], "block")
        self.assertEqual(outcome["check"],
                         "continuity_subscription_missing")
        self.assertIsNone(outcome["detail"]["registered_epoch_id"])

    # —— trio 3：transport armed 矩阵（state 落盘 + §22.5 capability） ——
    def _saved_state(self, st):
        state.save_state(self.repo, st)
        return state.load_state(self.repo, TID)

    def test_interactive_disarmed_blocks_not_armed(self):
        self.make_handoff_durable()
        st = self._resumable_state()
        st["quota_subscription"] = make_subscription()
        st["continuation"] = make_continuation(
            scheduler={"origin": "interactive"})
        state.save_state(self.repo, st)
        outcome = evaluate_continuity_health(
            str(self.repo), TID, state.load_state(self.repo, TID))
        self.assertEqual(outcome["outcome"], "block")
        self.assertEqual(outcome["check"],
                         "continuity_transport_not_armed")
        self.assertEqual(outcome["detail"]["bridge_status"], "none")
        self.assertEqual(outcome["detail"]["scheduler_origin"],
                         "interactive")

    def test_create_allowed_disarmed_blocks_not_armed(self):
        self.make_handoff_durable()
        st = self._resumable_state()
        st["quota_subscription"] = make_subscription()
        st["continuation"] = make_continuation(
            scheduler={"create": "allowed"})
        state.save_state(self.repo, st)
        outcome = evaluate_continuity_health(
            str(self.repo), TID, state.load_state(self.repo, TID))
        self.assertEqual(outcome["check"], "continuity_transport_not_armed")

    def test_forbidden_disarmed_degrades_allow(self):
        # INV-22-PB-07：create forbidden → 降级放行，绝不 block
        self.make_handoff_durable()
        st = self._resumable_state()
        st["quota_subscription"] = make_subscription()
        st["continuation"] = make_continuation(
            scheduler={"create": "forbidden"})
        state.save_state(self.repo, st)
        outcome = evaluate_continuity_health(
            str(self.repo), TID, state.load_state(self.repo, TID))
        self.assertEqual(outcome["outcome"], "degraded")
        self.assertEqual(outcome["reason"],
                         "continuity_scheduler_create_forbidden")

    def test_scheduled_origin_disarmed_degrades_allow(self):
        self.make_handoff_durable()
        st = self._resumable_state()
        st["quota_subscription"] = make_subscription()
        st["continuation"] = make_continuation(
            scheduler={"origin": "scheduled_task"})
        state.save_state(self.repo, st)
        outcome = evaluate_continuity_health(
            str(self.repo), TID, state.load_state(self.repo, TID))
        self.assertEqual(outcome["outcome"], "degraded")
        self.assertEqual(outcome["reason"],
                         "continuity_scheduler_create_forbidden")

    def test_double_unknown_blocks_transport_unknown(self):
        self.make_handoff_durable()
        st = self._resumable_state()
        st["quota_subscription"] = make_subscription()
        st["continuation"] = make_continuation()  # origin/create 全 unknown
        state.save_state(self.repo, st)
        outcome = evaluate_continuity_health(
            str(self.repo), TID, state.load_state(self.repo, TID))
        self.assertEqual(outcome["outcome"], "block")
        self.assertEqual(outcome["check"], "continuity_transport_unknown")

    def test_armed_bridge_passes_trio(self):
        self.make_handoff_durable()
        st = self._resumable_state()
        st["quota_subscription"] = make_subscription()
        st["continuation"] = make_continuation(bridge_status="armed")
        state.save_state(self.repo, st)
        outcome = evaluate_continuity_health(
            str(self.repo), TID, state.load_state(self.repo, TID))
        self.assertEqual(outcome["outcome"], "ok")

    # —— obligation 短路两向 ——
    def test_obligation_degraded_short_circuits_before_trio(self):
        # 无任何交接工件也直接降级放行：既有 C1b 裁决不重审
        st = self._resumable_state(
            continuation=make_continuation(obligation="degraded"))
        outcome = evaluate_continuity_health(str(self.repo), TID, st)
        self.assertEqual(outcome["outcome"], "degraded")
        self.assertEqual(outcome["reason"],
                         "continuity_obligation_degraded")
        self.assertEqual(outcome["check"], None)

    def test_obligation_armed_with_cancelled_bridge_not_short_circuited(self):
        # §22.5：armed 记账遇 cancelled 桥 → 按未 armed 矩阵走
        self.make_handoff_durable()
        st = self._resumable_state()
        st["quota_subscription"] = make_subscription()
        st["continuation"] = make_continuation(
            obligation="armed", bridge_status="cancelled",
            scheduler={"origin": "interactive"})
        state.save_state(self.repo, st)
        outcome = evaluate_continuity_health(
            str(self.repo), TID, state.load_state(self.repo, TID))
        self.assertEqual(outcome["outcome"], "block")
        self.assertEqual(outcome["check"],
                         "continuity_transport_not_armed")
        self.assertEqual(outcome["detail"]["bridge_status"], "cancelled")

    # —— watcher 健康注记（degrade-only，永不独立 block） ——
    def test_watcher_health_reasons(self):
        # 无记录 → absent；陈旧心跳 → stale；新鲜 → None
        self.assertEqual(stop_gate._watcher_health_reason(str(self.repo)),
                         "continuity_watcher_absent")
        self.write_stale_watcher()
        self.assertEqual(stop_gate._watcher_health_reason(str(self.repo)),
                         "continuity_watcher_stale")
        self.write_fresh_watcher()
        self.assertIsNone(
            stop_gate._watcher_health_reason(str(self.repo)))

    def test_watcher_annotation_carried_on_green_trio(self):
        self.make_handoff_durable()
        st = self._resumable_state()
        st["quota_subscription"] = make_subscription()
        st["continuation"] = make_continuation(bridge_status="armed")
        state.save_state(self.repo, st)
        self.write_stale_watcher()
        outcome = evaluate_continuity_health(
            str(self.repo), TID, state.load_state(self.repo, TID))
        self.assertEqual(outcome["outcome"], "ok")
        self.assertEqual(outcome["watcher"], "continuity_watcher_stale")

    # —— fail-open：检查自身异常 → continuity_evaluation_error ——
    def test_evaluation_error_fail_open(self):
        st = self._resumable_state()
        self.make_handoff_durable()
        st["quota_subscription"] = make_subscription()
        with mock.patch("runtime.activation_transport."
                        "activation_transport_status",
                        side_effect=RuntimeError("boom")):
            outcome = evaluate_continuity_health(str(self.repo), TID, st)
        self.assertEqual(outcome["outcome"], "degraded")
        self.assertEqual(outcome["reason"], "continuity_evaluation_error")

    # —— ⑥ parent_automation_id 消费警示（C6 review P3-3 落地锚定） ——
    def test_docstring_carries_parent_automation_id_warning(self):
        doc = stop_gate.evaluate_continuity_health.__doc__ or ""
        self.assertIn("parent_automation_id", doc)
        self.assertIn("wake_bridge.automation_id", doc)
        self.assertIn("观测镜像", doc)

    # —— 触发域 / 降级词汇常量锚定 ——
    def test_trigger_and_reason_vocabularies(self):
        self.assertEqual(stop_gate.CONTINUITY_TRIGGER_STATUSES,
                         ("waiting_quota", "waiting_user"))
        self.assertEqual(stop_gate.CONTINUITY_TRIGGER_PHASES,
                         ("PRESSURE", "DRAINING"))
        self.assertEqual(stop_gate.CONTINUITY_TRIGGER_AUTO_RESUMES,
                         ("auto_once", "until_done"))
        for reason in stop_gate.CONTINUITY_DEGRADE_REASONS:
            self.assertTrue(reason.startswith("continuity_"), reason)


# —— 门冒烟层：触发域边界（子进程；零介入 = 静默放行 + 无 continuity 记账） ——

@unittest.skipUnless(shutil.which("git"), "环境无 git 可执行，跳过 git fixture 测试")
class ContinuityTriggerDomainGateTest(GitRepoFixture):
    """触发域冻结边界的端到端行为：域外零介入（无任何 continuity 事件）。"""

    def test_normal_executing_zero_intervention(self):
        # NORMAL executing 任务的普通回合结束：什么都不备也零介入
        self.add_continuity_task(status="executing")
        result = run_gate("{}", self.repo)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")
        self.assertEqual([e["event"] for e in self.journal_events()],
                         ["gate_passed"])

    def test_waiting_quota_triggers_handoff_block(self):
        self.add_continuity_task()  # waiting_quota，什么都不备
        result = run_gate("{}", self.repo)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.count("\n"), 1)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["decision"], "block")
        reason = payload["reason"]
        self.assertIn("continuity handoff is not durable (task %s)." % TID,
                      reason)
        self.assertIn("Write the checkpoint and refresh the resume manifest",
                      reason)
        events = self.journal_events()
        self.assertEqual([e["event"] for e in events], ["gate_blocked"])
        self.assertEqual(events[0]["check"], "continuity_handoff_missing")
        self.assertEqual(events[0]["task_id"], TID)
        self.assertEqual(events[0]["missing"],
                         ["checkpoint.md", "manifest.json"])

    def test_waiting_user_triggers_too(self):
        self.add_continuity_task(status="waiting_user")
        result = run_gate("{}", self.repo)
        self.assertEqual(json.loads(result.stdout)["decision"], "block")
        self.assertEqual(
            self.journal_events()[0]["check"], "continuity_handoff_missing")

    def test_pressure_phase_triggers_block(self):
        self.add_continuity_task(status="executing",
                                 execution_phase="PRESSURE")
        result = run_gate("{}", self.repo)
        self.assertEqual(json.loads(result.stdout)["decision"], "block")
        self.assertEqual(
            self.journal_events()[0]["check"], "continuity_handoff_missing")

    def test_draining_phase_triggers_block(self):
        self.add_continuity_task(status="executing",
                                 execution_phase="DRAINING")
        result = run_gate("{}", self.repo)
        self.assertEqual(json.loads(result.stdout)["decision"], "block")
        self.assertEqual(
            self.journal_events()[0]["check"], "continuity_handoff_missing")

    def test_blocked_phase_zero_intervention(self):
        # BLOCKED 不在触发相词汇（仅 PRESSURE / DRAINING）→ 零介入
        self.add_continuity_task(status="executing",
                                 execution_phase="BLOCKED")
        result = run_gate("{}", self.repo)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")
        self.assertEqual([e["event"] for e in self.journal_events()],
                         ["gate_passed"])

    def test_manual_auto_resume_zero_intervention(self):
        # §32.2 warm-only：manual 零介入（waiting_quota 也不拦）
        self.add_continuity_task(auto_resume="manual")
        result = run_gate("{}", self.repo)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")
        self.assertEqual([e["event"] for e in self.journal_events()],
                         ["gate_passed"])

    def test_notify_auto_resume_zero_intervention(self):
        self.add_continuity_task(auto_resume="notify")
        result = run_gate("{}", self.repo)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")
        self.assertEqual([e["event"] for e in self.journal_events()],
                         ["gate_passed"])

    def test_foreground_route_zero_intervention(self):
        # route 非 resumable → 零介入（即使 auto_resume 属 auto 家族）
        self.add_continuity_task(route=ROUTE_FOREGROUND)
        result = run_gate("{}", self.repo)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")
        self.assertEqual([e["event"] for e in self.journal_events()],
                         ["gate_passed"])


# —— 门冒烟层：trio 矩阵 / obligation 短路 / gate_exhausted 复用 ——

@unittest.skipUnless(shutil.which("git"), "环境无 git 可执行，跳过 git fixture 测试")
class ContinuityTrioMatrixGateTest(GitRepoFixture):
    """三检查矩阵各分支的端到端行为（block / degraded / 放行）。"""

    def _add_green_ready_task(self, task_id=TID, continuation=None):
        """trio 全绿的基座任务：handoff durable + 订阅 + armed 桥 +
        新鲜 watcher（obligation 缺省 none；continuation 可覆盖）。"""
        self.add_continuity_task(
            task_id=task_id,
            continuation=continuation
            if continuation is not None
            else make_continuation(bridge_status="armed"),
            subscription=make_subscription())
        self.make_handoff_durable(task_id)
        self.write_fresh_watcher()

    def test_trio_green_passes_silently(self):
        self._add_green_ready_task()
        result = run_gate("{}", self.repo)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")
        self.assertEqual([e["event"] for e in self.journal_events()],
                         ["gate_passed"])

    def test_checkpoint_missing_blocks_with_actionable_message(self):
        self.add_continuity_task(
            continuation=make_continuation(bridge_status="armed"),
            subscription=make_subscription())
        self.write_manifest()
        self.write_fresh_watcher()
        result = run_gate("{}", self.repo)
        self.assertEqual(result.stdout.count("\n"), 1)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["decision"], "block")
        reason = payload["reason"]
        self.assertIn("continuity handoff is not durable", reason)
        self.assertIn("- checkpoint.md", reason)
        self.assertNotIn("- manifest.json", reason)
        self.assertIn("Write the checkpoint and refresh the resume"
                      " manifest", reason)
        events = self.journal_events()
        self.assertEqual(events[0]["check"], "continuity_handoff_missing")
        self.assertEqual(events[0]["missing"], ["checkpoint.md"])
        self.assertNotIn("watcher", events[0])  # 新鲜 watcher 无注记

    def test_manifest_missing_blocks(self):
        self.add_continuity_task()
        self.write_checkpoint()
        result = run_gate("{}", self.repo)
        payload = json.loads(result.stdout)
        self.assertIn("- manifest.json", payload["reason"])
        self.assertEqual(
            self.journal_events()[0]["missing"], ["manifest.json"])

    def test_subscription_missing_blocks_with_actionable_message(self):
        self.add_continuity_task(
            continuation=make_continuation(bridge_status="armed"))
        self.make_handoff_durable()
        self.write_fresh_watcher()
        result = run_gate("{}", self.repo)
        self.assertEqual(result.stdout.count("\n"), 1)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["decision"], "block")
        reason = payload["reason"]
        self.assertIn("quota subscription is not registered", reason)
        self.assertIn("enabled: False", reason)
        self.assertIn("registered_epoch_id: none", reason)
        self.assertIn("Register the quota subscription", reason)
        events = self.journal_events()
        self.assertEqual(events[0]["check"],
                         "continuity_subscription_missing")
        self.assertIsNone(events[0]["registered_epoch_id"])

    def test_interactive_disarmed_blocks_with_arm_guidance(self):
        self.add_continuity_task(
            continuation=make_continuation(
                scheduler={"origin": "interactive"}),
            subscription=make_subscription())
        self.make_handoff_durable()
        self.write_fresh_watcher()
        result = run_gate("{}", self.repo)
        payload = json.loads(result.stdout)
        reason = payload["reason"]
        self.assertIn("activation transport is not armed", reason)
        self.assertIn("wake_bridge status: none", reason)
        self.assertIn("Arm the wake bridge first", reason)
        events = self.journal_events()
        self.assertEqual(events[0]["check"],
                         "continuity_transport_not_armed")
        self.assertEqual(events[0]["bridge_status"], "none")
        self.assertEqual(events[0]["scheduler_origin"], "interactive")

    def test_forbidden_disarmed_degrades_allow_never_blocks(self):
        # INV-22-PB-07：create forbidden → 降级放行；连跑 3 次 Stop
        # 也绝不出现 gate_blocked（不无限 block）
        self.add_continuity_task(
            continuation=make_continuation(
                scheduler={"create": "forbidden"}),
            subscription=make_subscription())
        self.make_handoff_durable()
        self.write_fresh_watcher()
        for round_index in range(3):
            result = run_gate("{}", self.repo)
            self.assertEqual(result.returncode, 0, round_index)
            self.assertEqual(result.stdout, "", round_index)
            self.assertIn("ENFORCEMENT DEGRADED", result.stderr)
            self.assertIn("continuity_scheduler_create_forbidden",
                          result.stderr)
        events = self.journal_events()
        self.assertEqual(
            [e["event"] for e in events],
            ["gate_degraded", "gate_passed"] * 3)
        for event in events:
            if event["event"] == "gate_degraded":
                self.assertEqual(event["reason"],
                                 "continuity_scheduler_create_forbidden")
        self.assertNotIn("gate_blocked", [e["event"] for e in events])

    def test_scheduled_origin_disarmed_degrades_allow(self):
        # §22.5：scheduled-owned → degraded / fallback（SessionStart 兜底）
        self.add_continuity_task(
            continuation=make_continuation(
                scheduler={"origin": "scheduled_task"}),
            subscription=make_subscription())
        self.make_handoff_durable()
        self.write_fresh_watcher()
        result = run_gate("{}", self.repo)
        self.assertEqual(result.stdout, "")
        self.assertEqual(
            self.journal_events()[0]["reason"],
            "continuity_scheduler_create_forbidden")

    def test_double_unknown_blocks_then_gate_exhausted(self):
        # 双 unknown → block（可行动：尝试一次 CronCreate 记录能力 /
        # 跑 wake-plan）→ 连续 2 次 block 后第 3 次 Stop 经既有
        # gate_exhausted 机器降级放行（复用不重建，防死锁）
        self.add_continuity_task(
            subscription=make_subscription())  # bridge none + 双 unknown
        self.make_handoff_durable()
        self.write_fresh_watcher()
        first = run_gate("{}", self.repo)
        second = run_gate("{}", self.repo)
        self.assertEqual(json.loads(first.stdout)["decision"], "block")
        self.assertEqual(json.loads(second.stdout)["decision"], "block")
        self.assertIn("Probe the host scheduler capability once",
                      json.loads(first.stdout)["reason"])
        self.assertIn("CronCreate", json.loads(first.stdout)["reason"])
        self.assertEqual(
            [e["event"] for e in self.journal_events()],
            ["gate_blocked", "gate_blocked"])
        third = run_gate("{}", self.repo)
        self.assertEqual(third.returncode, 0)
        self.assertEqual(third.stdout, "")
        self.assertIn("ENFORCEMENT GATE EXHAUSTED", third.stderr)
        events = self.journal_events()
        self.assertEqual(
            [e["event"] for e in events],
            ["gate_blocked", "gate_blocked", "gate_exhausted"])
        self.assertEqual(events[-1]["check"], "continuity_transport_unknown")

    def test_continuity_degrade_interleaved_still_exhausts(self):
        # wu-22-C8a 回归：continuity 降级注记（gate_degraded，
        # reason=continuity_*）是 gate 侧每次 Stop 求值时无条件写的
        # 观测面事件，不是模型在两次 block 之间做的真实工作——不得
        # 打断 trailing gate_blocked 计数链。域内 obligation=degraded
        # 短路降级 + 持续四重违规（ownership 越界）：前两次 Stop 各记
        # [gate_degraded(continuity_*), gate_blocked]，第 3 次 Stop
        # 必须经既有 gate_exhausted 释放阀放行（恢复 C8 之前语义），
        # 而不是被注记破链后无限 block。
        self.add_continuity_task(
            continuation=make_continuation(obligation="degraded"))
        self.write("src/rogue.ts", b"rogue\n")
        first = run_gate("{}", self.repo)
        second = run_gate("{}", self.repo)
        self.assertEqual(json.loads(first.stdout)["decision"], "block")
        self.assertEqual(json.loads(second.stdout)["decision"], "block")
        self.assertIn("ENFORCEMENT DEGRADED", first.stderr)
        self.assertEqual(
            [e["event"] for e in self.journal_events()],
            ["gate_degraded", "gate_blocked",
             "gate_degraded", "gate_blocked"])
        events = self.journal_events()
        self.assertEqual(events[0]["reason"],
                         "continuity_obligation_degraded")
        self.assertEqual(events[1]["check"], "ownership")
        third = run_gate("{}", self.repo)
        self.assertEqual(third.returncode, 0)
        self.assertEqual(third.stdout, "")  # 释放阀放行，不再 block
        self.assertIn("ENFORCEMENT GATE EXHAUSTED", third.stderr)
        events = self.journal_events()
        # 第 3 次 Stop 先经降级注记（求值期写），再经释放阀记
        # gate_exhausted——注记夹在链中不再破链
        self.assertEqual(
            [e["event"] for e in events],
            ["gate_degraded", "gate_blocked",
             "gate_degraded", "gate_blocked",
             "gate_degraded", "gate_exhausted"])
        self.assertEqual(events[-1]["check"], "ownership")

    def test_non_continuity_degrade_still_breaks_exhaustion_chain(self):
        # 对照（carve-out 不泛化）：两次 Stop 之间夹一条非 continuity_
        # 前缀的 gate_degraded（模型侧 / 既有降级语义，如
        # orphaned_task）照旧破链——第 3 次 Stop 不得触发
        # gate_exhausted，释放阀不被降级注记泛化绕过。
        self.add_continuity_task(
            continuation=make_continuation(obligation="degraded"))
        self.write("src/rogue.ts", b"rogue\n")
        first = run_gate("{}", self.repo)
        self.assertEqual(json.loads(first.stdout)["decision"], "block")
        # 在两次 Stop 之间向任务 journal 手工注入非 continuity 降级事件
        journal_mod.append_event(self.repo, TID, {
            "event": "gate_degraded", "reason": "orphaned_task"})
        second = run_gate("{}", self.repo)
        third = run_gate("{}", self.repo)
        self.assertEqual(json.loads(second.stdout)["decision"], "block")
        self.assertEqual(json.loads(third.stdout)["decision"], "block")
        self.assertNotIn("ENFORCEMENT GATE EXHAUSTED", third.stderr)
        self.assertNotIn("gate_exhausted",
                         [e["event"] for e in self.journal_events()])

    def test_obligation_degraded_short_circuit_at_gate(self):
        # obligation=degraded → 直接降级放行（handoff 缺失也不重审为 block）
        self.add_continuity_task(
            continuation=make_continuation(obligation="degraded"))
        result = run_gate("{}", self.repo)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("continuity_obligation_degraded", result.stderr)
        events = self.journal_events()
        self.assertEqual(
            [e["event"] for e in events], ["gate_degraded", "gate_passed"])
        self.assertEqual(events[0]["reason"],
                         "continuity_obligation_degraded")

    def test_obligation_armed_cancelled_bridge_blocks(self):
        # §22.5：armed 记账 + cancelled 桥 → 未 armed 矩阵（interactive
        # → block 可行动），armed 声明不被短路放行
        self._add_green_ready_task(
            continuation=make_continuation(
                obligation="armed", bridge_status="cancelled",
                scheduler={"origin": "interactive"}))
        result = run_gate("{}", self.repo)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["decision"], "block")
        self.assertIn("activation transport is not armed",
                      payload["reason"])
        events = self.journal_events()
        self.assertEqual(events[0]["check"],
                         "continuity_transport_not_armed")
        self.assertEqual(events[0]["bridge_status"], "cancelled")

    def test_obligation_armed_with_live_bridge_passes(self):
        self._add_green_ready_task(
            continuation=make_continuation(
                obligation="armed", bridge_status="armed"))
        result = run_gate("{}", self.repo)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")
        self.assertEqual([e["event"] for e in self.journal_events()],
                         ["gate_passed"])

    def test_continuity_degrade_does_not_mask_four_fold_violation(self):
        # 降级是放行方向：obligation=degraded 短路后四重检查照常运行，
        # ownership 越界仍然 block（新检查绝不掩盖既有违规）
        self.add_continuity_task(
            continuation=make_continuation(obligation="degraded"))
        self.write("src/rogue.ts", b"rogue\n")
        result = run_gate("{}", self.repo)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["decision"], "block")
        self.assertIn("ownership violation (task %s)." % TID,
                      payload["reason"])
        events = self.journal_events()
        self.assertEqual(
            [e["event"] for e in events], ["gate_degraded", "gate_blocked"])
        self.assertEqual(events[0]["reason"],
                         "continuity_obligation_degraded")
        self.assertEqual(events[1]["check"], "ownership")


# —— 门冒烟层：watcher 健康注记（degrade-only，永不独立 block） ——

@unittest.skipUnless(shutil.which("git"), "环境无 git 可执行，跳过 git fixture 测试")
class ContinuityWatcherAnnotationGateTest(GitRepoFixture):
    """watcher 注记语义（§26-14）：附注 + journal 词汇，永不独立 block。"""

    def _add_green_without_watcher(self):
        self.add_continuity_task(
            continuation=make_continuation(bridge_status="armed"),
            subscription=make_subscription())
        self.make_handoff_durable()

    def test_watcher_absent_annotates_degrade_not_block(self):
        self._add_green_without_watcher()
        result = run_gate("{}", self.repo)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")  # 不 block
        self.assertIn("ENFORCEMENT DEGRADED", result.stderr)
        self.assertIn("continuity_watcher_absent", result.stderr)
        events = self.journal_events()
        self.assertEqual(
            [e["event"] for e in events], ["gate_degraded", "gate_passed"])
        self.assertEqual(events[0]["reason"], "continuity_watcher_absent")

    def test_watcher_stale_annotates_degrade_not_block(self):
        self._add_green_without_watcher()
        self.write_stale_watcher()
        result = run_gate("{}", self.repo)
        self.assertEqual(result.stdout, "")
        self.assertIn("continuity_watcher_stale", result.stderr)
        events = self.journal_events()
        self.assertEqual(
            [e["event"] for e in events], ["gate_degraded", "gate_passed"])
        self.assertEqual(events[0]["reason"], "continuity_watcher_stale")

    def test_watcher_annotation_never_blocks_across_repeats(self):
        # 连跑 3 次 Stop：注记重复可见，但永不产生 gate_blocked
        self._add_green_without_watcher()
        self.write_stale_watcher()
        for round_index in range(3):
            result = run_gate("{}", self.repo)
            self.assertEqual(result.returncode, 0, round_index)
            self.assertEqual(result.stdout, "", round_index)
        names = [e["event"] for e in self.journal_events()]
        self.assertEqual(names, ["gate_degraded", "gate_passed"] * 3)
        self.assertNotIn("gate_blocked", names)

    def test_watcher_annotation_rides_on_block(self):
        # trio 本身 block 时注记只随报文 / journal 附带，不改变 block 方向
        self.add_continuity_task()  # 什么都不备：handoff 缺失 + 无 watcher
        result = run_gate("{}", self.repo)
        self.assertEqual(json.loads(result.stdout)["decision"], "block")
        reason = json.loads(result.stdout)["reason"]
        self.assertIn("quota watcher health: continuity_watcher_absent",
                      reason)
        events = self.journal_events()
        self.assertEqual(events[0]["event"], "gate_blocked")
        self.assertEqual(events[0]["check"], "continuity_handoff_missing")
        self.assertEqual(events[0]["watcher"], "continuity_watcher_absent")


if __name__ == "__main__":
    unittest.main()
