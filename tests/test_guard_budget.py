#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hooks.stop_gate 守卫总预算定向测试（v2.6.0 Phase C，WP-R3 / P1-2）。

运行：
    cd <repo_root> && PYTHONUTF8=1 python3 -m unittest tests.test_guard_budget -v

覆盖（docs/roadmap/V2_6_0_IMPLEMENTATION_PLAN.md §3 Phase C 验收门 R3）：
    - 预算窗口配置：默认 = HOOK_STOP_TIMEOUT_MS（5000 的进程内镜像）−
      安全余量；GLM_CONDUCTOR_GUARD_BUDGET_MS 只能收紧（上限钳制在钩子
      预算内），非法值 / 非正数回退默认；
    - 单次 git 超时钳制：GLM_CONDUCTOR_GIT_TIMEOUT 调大（如 30s）时
      change_id / ownership 两处 effective_git_timeout() 都被剩余总预算
      钳制（min(env, 剩余)）；env 更小时 env 胜；未注册截止期恒等于
      env 配置值；预算耗尽（截止期已过）不发起子进程、直接抛结构性
      错误（ChangeIdError / OwnershipError，无界重试不存在）；
    - 受控假慢 git（monkeypatch subprocess.run 使每次调用 sleep 到超时
      再抛 TimeoutExpired——模拟 git 挂起被自身超时先于宿主硬杀击穿）
      下驱动 stop_gate.main()：5s 预算内返回、退出码 0、stderr 通道
      含 ENFORCEMENT DEGRADED 正确短语、无误 block（stdout 零决策
      JSON）、任务状态不被误改；ownership（status）与 change_id
      （rev-parse）两个观察点都验证；
    - status 输出复用：守卫回合外同一仓库根两次需要 = 两次串行
      spawn（对照）；守卫回合内（main 注册 begin_status_sharing）
      查 2 与查 3（relevant_changed_files）两次需要恰 spawn 一次
      git status——3s+3s+3s 串行叠加禁止的回归锚；
    - 正常 block 路径不受预算机制扰动：子进程驱动 stop_gate.py，
      有活跃未验证任务 → stdout 恰单行 block JSON（退出码 0、状态
      不变），且 stderr 无 ENFORCEMENT DEGRADED（正常拦截不是降级）。

全部离线：任务账本在 tempfile.TemporaryDirectory 内；假 git 用
mock.patch("subprocess.run") 整替换（change_id / ownership 同调
subprocess.run，一处替换两处生效），真实 git 仅在 status 复用与正常
block 用例中真实执行（unborn 基线，仅 git init，绝不触碰仓库内真实
账本）。仅 Python 3 标准库（unittest + tempfile + threading），零第
三方依赖，3.7 兼容语法。
"""

import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "glm-conductor"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "glm-conductor" / "hooks"))

from runtime import change_id, journal, ownership, state, task, work_unit  # noqa: E402
import stop_gate  # noqa: E402

# v2.6.1 W3b 英文化：降级短语与理由文案断言与 stop_gate 模板单源对齐。
ALLOWING_TASK_PHRASE = (
    stop_gate.DEGRADED_TASK_SKIP_MESSAGE_TEMPLATE.split("; ")[1])

# 被测钩子脚本：仓库根 plugins/glm-conductor/hooks/stop_gate.py
STOP_GATE = (Path(__file__).resolve().parents[1]
             / "plugins/glm-conductor/hooks/stop_gate.py")

# 守卫预算收紧档：1200ms 总预算 − 600ms 安全余量 = 600ms git 观察窗口
# （远小于默认 4.4s，让「慢 git 挂到超时」的用例在亚秒级完成）
TIGHT_BUDGET_MS = "1200"


# —— 测试夹具 ——

class GuardBudgetCase(unittest.TestCase):
    """每测试隔离的临时仓库根 + 假 stdout / stderr / stdin 通道。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._force_cleanup)
        self.repo = self._tmp.name
        self.tid = "budget-task-a1b2c"
        # stdout 决策 JSON 捕获（emit_block_json 是唯一 stdout 写入面）
        self.blocks = []
        emit_patcher = mock.patch.object(
            stop_gate, "emit_block_json",
            side_effect=lambda reason: self.blocks.append(reason))
        emit_patcher.start()
        self.addCleanup(emit_patcher.stop)
        # stderr 通道捕获（warn_stderr 是全部提示的唯一 stderr 写入面）
        self.stderr_lines = []
        warn_patcher = mock.patch.object(
            stop_gate, "warn_stderr",
            side_effect=lambda message: self.stderr_lines.append(message))
        warn_patcher.start()
        self.addCleanup(warn_patcher.stop)
        # stdin：钩子容错读 Stop 事件（空对象即可）
        stdin_patcher = mock.patch.object(sys, "stdin", io.StringIO("{}"))
        stdin_patcher.start()
        self.addCleanup(stdin_patcher.stop)

    def _force_cleanup(self):
        """解除 git 只读对象后清理临时目录（tests/test_task_lifecycle 手法）。"""
        git_dir = os.path.join(self._tmp.name, ".git")
        if os.path.isdir(git_dir):
            for dirpath, _dirnames, filenames in os.walk(git_dir):
                for name in filenames:
                    try:
                        os.chmod(os.path.join(dirpath, name), stat.S_IWRITE)
                    except OSError:
                        pass
        self._tmp.cleanup()

    # —— 装置助手 ——

    def node(self, node_id="build"):
        """构造合法静态节点（经 work_unit.new_node 正门）。"""
        return work_unit.new_node(
            node_id, "实现 %s 节点" % node_id,
            ownership=("src/%s.py" % node_id,))

    def make_task(self, task_id=None):
        """创建缺省 delegate/standard 活动任务，返回状态 dict。"""
        return task.create_task(
            self.repo, task_id or self.tid, "守卫预算定向测试",
            {"mode": "delegate", "assurance": "standard"},
            [self.node()])

    def tighten_budget(self, value=TIGHT_BUDGET_MS, git_timeout="30"):
        """注入守卫预算环境（预算收紧档 + env 单次超时调大到 30s）。"""
        values = {"ZCODE_PROJECT_DIR": self.repo,
                  stop_gate.GUARD_BUDGET_ENV_VAR: value}
        if git_timeout is not None:
            values[change_id.GIT_TIMEOUT_ENV_VAR] = git_timeout
        return mock.patch.dict(os.environ, values)

    def budget_window(self):
        """当前预算窗口（秒）——假 git 超时钳制断言的上界基准。"""
        return stop_gate._git_observation_budget_seconds()

    def status_text(self, task_id=None):
        return state.load_state(self.repo, task_id or self.tid)["status"]


# —— 1. 预算窗口配置 ——

class TestBudgetWindowConfig(GuardBudgetCase):
    """GLM_CONDUCTOR_GUARD_BUDGET_MS 只能收紧；非法值回退默认。"""

    DEFAULT_WINDOW = stop_gate.HOOK_STOP_TIMEOUT_MS / 1000.0 \
        - stop_gate.GUARD_SAFETY_MARGIN_SECONDS

    def test_default_window_without_env(self):
        """未设置环境变量 → 默认窗口 = 钩子预算 − 安全余量（4.4s）。"""
        with mock.patch.dict(os.environ,
                             {stop_gate.GUARD_BUDGET_ENV_VAR: ""}):
            os.environ.pop(stop_gate.GUARD_BUDGET_ENV_VAR, None)
            self.assertEqual(self.budget_window(), self.DEFAULT_WINDOW)

    def test_env_can_only_tighten(self):
        """正数环境变量收紧窗口：1200ms → 0.6s。"""
        with mock.patch.dict(os.environ,
                             {stop_gate.GUARD_BUDGET_ENV_VAR: "1200"}):
            self.assertEqual(self.budget_window(), 0.6)

    def test_env_above_hook_budget_clamped(self):
        """环境变量大于钩子预算 → 钳制在 5000ms 内（不得击穿宿主硬杀）。"""
        with mock.patch.dict(os.environ,
                             {stop_gate.GUARD_BUDGET_ENV_VAR: "999999"}):
            self.assertEqual(self.budget_window(), self.DEFAULT_WINDOW)
            self.assertLessEqual(self.budget_window(),
                                 stop_gate.HOOK_STOP_TIMEOUT_MS / 1000.0)

    def test_invalid_values_fall_back_to_default(self):
        """非法值（非数字 / 空 / 零 / 负数）→ 一律回退默认窗口。"""
        for raw in ("abc", "", "0", "-10", "12.00ms"):
            with self.subTest(raw=raw):
                with mock.patch.dict(
                        os.environ, {stop_gate.GUARD_BUDGET_ENV_VAR: raw}):
                    self.assertEqual(self.budget_window(),
                                     self.DEFAULT_WINDOW)


# —— 2. 单次 git 超时钳制（change_id 与 ownership 同口径） ——

class TestEffectiveTimeoutClamp(GuardBudgetCase):
    """effective_git_timeout() = min(env 配置值, 剩余总预算)。"""

    def tearDown(self):
        change_id.clear_git_deadline()
        ownership.clear_git_deadline()

    def test_no_deadline_equals_env(self):
        """未注册截止期（CLI / 直接调用）→ 恒等于 env 配置值。"""
        with mock.patch.dict(os.environ,
                             {change_id.GIT_TIMEOUT_ENV_VAR: "30"}):
            self.assertEqual(change_id.effective_git_timeout(), 30.0)
            self.assertEqual(ownership.effective_git_timeout(), 30.0)

    def test_raised_env_clamped_by_remaining_budget(self):
        """env 调大（30s）+ 截止期 0.5s → 单次超时被剩余预算钳制（≤0.5s）。"""
        with mock.patch.dict(os.environ,
                             {change_id.GIT_TIMEOUT_ENV_VAR: "30"}):
            change_id.set_git_deadline(time.monotonic() + 0.5)
            ownership.set_git_deadline(time.monotonic() + 0.5)
            self.addCleanup(change_id.clear_git_deadline)
            self.addCleanup(ownership.clear_git_deadline)
            for effective in (change_id.effective_git_timeout(),
                              ownership.effective_git_timeout()):
                self.assertGreater(effective, 0.0)
                self.assertLessEqual(effective, 0.5)
                self.assertLess(effective, 30.0)

    def test_smaller_env_wins_over_far_deadline(self):
        """env（1.0s）小于剩余预算（60s）→ env 值胜（min 口径）。"""
        with mock.patch.dict(os.environ,
                             {change_id.GIT_TIMEOUT_ENV_VAR: "1.0"}):
            change_id.set_git_deadline(time.monotonic() + 60.0)
            ownership.set_git_deadline(time.monotonic() + 60.0)
            self.addCleanup(change_id.clear_git_deadline)
            self.addCleanup(ownership.clear_git_deadline)
            self.assertEqual(change_id.effective_git_timeout(), 1.0)
            self.assertEqual(ownership.effective_git_timeout(), 1.0)

    def test_exhausted_budget_refuses_to_spawn(self):
        """预算耗尽（截止期已过）→ 不发起子进程直接抛结构性错误。

        ownership.git_touched_files → OwnershipError（英文消息点明
        budget exhausted）、change_id.resolve_base → ChangeIdError，
        消息点名「总预算已耗尽」；假 subprocess
        记录器零调用（无界重试不存在）。
        """
        spawned = []

        def recorder(cmd, **kwargs):
            spawned.append(list(cmd))
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout=b"", stderr=b"")

        change_id.set_git_deadline(time.monotonic() - 1.0)
        ownership.set_git_deadline(time.monotonic() - 1.0)
        self.addCleanup(change_id.clear_git_deadline)
        self.addCleanup(ownership.clear_git_deadline)
        with mock.patch("subprocess.run", new=recorder):
            with self.assertRaises(ownership.OwnershipError) as ctx_own:
                ownership.git_touched_files(self.repo)
            with self.assertRaises(change_id.ChangeIdError) as ctx_chg:
                change_id.resolve_base(self.repo)
        self.assertEqual(spawned, [], "预算耗尽后仍发起了 git 子进程")
        self.assertIn("budget exhausted", str(ctx_own.exception))
        self.assertIn("预算已耗尽", str(ctx_chg.exception))


# —— 3. 受控假慢 git：守卫回合在预算内可见降级 ——

def git_subcommand(cmd):
    """提取 git 子命令词（status / rev-parse）：跳过 "git" 与
    "-c <key>" 配置对（status 命令携带 -c core.quotepath=false）。"""
    rest = list(cmd)[1:]
    while rest and rest[0] == "-c":
        rest = rest[2:]
    return rest[0] if rest else ""


def slow_git_factory(calls, hang_commands=None):
    """构造假 subprocess.run：每次调用记录 (cmd, timeout) 后挂到超时。

    - hang_commands 为 None → 全部命令挂起（sleep(timeout) 后抛
      TimeoutExpired——模拟 git 挂起被单次超时击穿，挂起时长恰等于
      守卫发放的超时，即「每次调用 sleep 到超时」）；
    - hang_commands 给定（如 {"rev-parse"}）→ 命中者挂起，其余命令
      立即返回空输出的成功 CompletedProcess（status 秒回、基线观测
      挂起的分点场景）。
    timeout 由调用方（effective_git_timeout）供给并原样记录——它就是
    「钳制断言」的证据面。
    """
    hang_commands = hang_commands if hang_commands is not None else "*"

    def fake_run(cmd, **kwargs):
        timeout = kwargs.get("timeout")
        calls.append((list(cmd), timeout))
        matches = (hang_commands == "*"
                   or any(word in cmd for word in hang_commands))
        if not matches:
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout=b"", stderr=b"")
        time.sleep(timeout)
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=timeout)

    return fake_run


class TestSlowGitDegradation(GuardBudgetCase):
    """git 全观察点挂起 → 守卫在 5s 预算内 fail-open 降级、无误 block。"""

    def assert_degraded_turn(self, fake_run, task_id=None):
        """驱动 main() 并断言降级回合契约（预算内 / exit 0 / 短语 / 无误 block）。"""
        self.make_task(task_id)
        with self.tighten_budget(), \
                mock.patch("subprocess.run", new=fake_run):
            started = time.monotonic()
            code = stop_gate.main()
            elapsed = time.monotonic() - started
        # 退出码 0（fail-open 放行，不用退出码阻断会话）
        self.assertEqual(code, 0)
        # 5s 预算内返回（宿主 timeoutMs=5000 硬杀绝不触发）
        self.assertLess(elapsed, 5.0,
                        "守卫回合超出 5s 预算（%.2fs）" % elapsed)
        # stderr 通道含正确降级短语，且点名被降级的任务
        degraded = [line for line in self.stderr_lines
                    if "ENFORCEMENT DEGRADED" in line]
        self.assertEqual(len(degraded), 1,
                         "降级报文应恰一条：%r" % (self.stderr_lines,))
        self.assertIn(task_id or self.tid, degraded[0])
        self.assertIn(ALLOWING_TASK_PHRASE, degraded[0])
        # 无误 block：stdout 零决策 JSON
        self.assertEqual(self.blocks, [])
        # 无误完成：任务状态不被误改
        self.assertEqual(self.status_text(task_id), "active")

    def test_all_git_calls_hang_degrade_within_budget(self):
        """全部 git 调用挂起：首个观察点（status）单次超时先于宿主硬杀
        触发 → 预算内降级；发放的超时被剩余预算钳制（env 30s 不击穿）。"""
        calls = []
        self.assert_degraded_turn(slow_git_factory(calls))
        self.assertTrue(calls, "假 git 未被调用")
        window = self.budget_window()
        for cmd, timeout in calls:
            # 单次超时 = min(env=30, 剩余总预算)——被钳制在预算窗口内
            self.assertGreater(timeout, 0.0)
            self.assertLessEqual(timeout, window + 0.05,
                                 "git %r 的超时未被剩余预算钳制" % (cmd,))
            self.assertLess(timeout, 30.0)

    def test_status_fast_rev_parse_hang_degrades_within_budget(self):
        """status 秒回、rev-parse 挂起：change_id 观察点同样受同一截止期
        钳制 → 预算内降级（两个观察点同源同预算）。"""
        calls = []
        self.assert_degraded_turn(
            slow_git_factory(calls, hang_commands=("rev-parse",)))
        words = [git_subcommand(cmd) for cmd, _timeout in calls]
        self.assertIn("status", words)
        self.assertIn("rev-parse", words)
        window = self.budget_window()
        for cmd, timeout in calls:
            if git_subcommand(cmd) == "rev-parse":
                self.assertGreater(timeout, 0.0)
                self.assertLessEqual(timeout, window + 0.05)
                self.assertLess(timeout, 30.0)

    def test_degradation_is_per_task_isolated(self):
        """多任务回合：挂起 git 下逐任务隔离降级（各得一条报文），
        不因首任务降级而中断其余任务的评估，也绝不 block。"""
        other = "budget-task-d4e5f"
        self.make_task()
        self.make_task(other)
        calls = []
        with self.tighten_budget(), \
                mock.patch("subprocess.run", new=slow_git_factory(calls)):
            code = stop_gate.main()
        self.assertEqual(code, 0)
        degraded = [line for line in self.stderr_lines
                    if "ENFORCEMENT DEGRADED" in line]
        self.assertEqual(len(degraded), 2)
        self.assertIn(self.tid, degraded[0] + degraded[1])
        self.assertIn(other, degraded[0] + degraded[1])
        self.assertEqual(self.blocks, [])
        self.assertEqual(self.status_text(), "active")
        self.assertEqual(self.status_text(other), "active")


# —— 4. status 输出复用（多次需要恰一次 spawn） ——

class TestStatusSharing(GuardBudgetCase):
    """守卫回合内同仓库根复用同一次 git status 输出。"""

    def counting_runner(self, counter):
        """包装真实 subprocess.run：只计数 status 子进程，其余透传。"""
        real_run = subprocess.run

        def counting(cmd, **kwargs):
            if "status" in cmd:
                counter["status"] += 1
            return real_run(cmd, **kwargs)

        return counting

    def _ready_repo_with_fresh_validation(self):
        """可过门基座：git init + owned 文件 + passed 验证（unborn 基线）。"""
        try:
            proc = subprocess.run(
                ["git", "init", "-q"], cwd=self.repo,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except FileNotFoundError:
            self.skipTest("环境无 git 可执行")
        if proc.returncode != 0:
            raise AssertionError("测试装置 git init 失败")
        target = Path(self.repo) / "src" / "build.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("内容\n", encoding="utf-8")
        self.make_task()
        task.record_validation(self.repo, self.tid, "passed")

    def test_guard_turn_spawns_status_exactly_once(self):
        """守卫回合内查 2 与查 3 两次需要 status → 恰一次真实 spawn。

        对照先证明「多次需要」真实存在：回合外同根连需两次 = 两次
        spawn（无共享的历史行为）。随后守卫回合（main 注册
        begin_status_sharing）内恰一次，且全过任务就地完成——证明
        第二个消费方（relevant_changed_files）读到的是同一次非空输出
        （若是空缓存，查 3 的 change_id 会与记录失配而 block）。
        """
        self._ready_repo_with_fresh_validation()
        counter = {"status": 0}
        # 对照：回合外两次直接需要 → 两次串行 spawn（共享未开启）
        with mock.patch("subprocess.run",
                        new=self.counting_runner(counter)):
            ownership.git_touched_files(self.repo)
            ownership.git_touched_files(self.repo)
        self.assertEqual(counter["status"], 2)
        # 守卫回合：同样两次需要（查 2 + 查 3 的 relevant_changed_files）
        # → 共享生效，恰一次 spawn；全过任务就地完成放行
        counter["status"] = 0
        with self.tighten_budget(git_timeout=None), \
                mock.patch("subprocess.run",
                           new=self.counting_runner(counter)):
            code = stop_gate.main()
        self.assertEqual(code, 0)
        self.assertEqual(self.blocks, [])
        self.assertEqual(
            counter["status"], 1,
            "守卫回合内 git status 应恰 spawn 一次（实测 %d 次）"
            % counter["status"])
        self.assertEqual(self.status_text(), "completed")
        self.assertEqual(
            [item.get("event") for item in journal.read_events(
                self.repo, self.tid)][-1],
            "task_completed")

    def test_sharing_state_not_leaked_after_turn(self):
        """守卫回合结束（finally 注销）后共享状态不残留：回合外再连需
        两次仍spawn 两次；共享缓存清空（end_status_sharing 幂等）。"""
        self._ready_repo_with_fresh_validation()
        with self.tighten_budget(git_timeout=None), \
                mock.patch("subprocess.run",
                           new=self.counting_runner({"status": 0})):
            stop_gate.main()
        counter = {"status": 0}
        with mock.patch("subprocess.run",
                        new=self.counting_runner(counter)):
            ownership.git_touched_files(self.repo)
            ownership.git_touched_files(self.repo)
        self.assertEqual(counter["status"], 2)
        self.assertEqual(ownership._SHARED_STATUS, {})


# —— 5. 正常 block 路径不受预算机制扰动 ——

class TestNormalBlockPathUnaffected(GuardBudgetCase):
    """预算机制只约束 git 观察，不改变 block 决策的输出契约。"""

    def test_block_path_single_line_json_via_subprocess(self):
        """子进程驱动（真实 main 预算接线）：未验证任务 → stdout 恰单行
        block JSON；stderr 无降级短语（正常拦截不是降级）；状态不变。"""
        try:
            proc = subprocess.run(
                ["git", "init", "-q"], cwd=self.repo,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except FileNotFoundError:
            self.skipTest("环境无 git 可执行")
        if proc.returncode != 0:
            raise AssertionError("测试装置 git init 失败")
        target = Path(self.repo) / "src" / "build.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("内容\n", encoding="utf-8")
        self.make_task()
        child_env = dict(os.environ,
                         ZCODE_PROJECT_DIR=self.repo,
                         **{stop_gate.GUARD_BUDGET_ENV_VAR: TIGHT_BUDGET_MS})
        proc = subprocess.run(
            [sys.executable, str(STOP_GATE)],
            input="{}", text=True, capture_output=True,
            encoding="utf-8", errors="replace", env=child_env)
        self.assertEqual(proc.returncode, 0)
        lines = proc.stdout.splitlines()
        self.assertEqual(len(lines), 1, "block 决策必须恰单行")
        decision = json.loads(lines[0])
        self.assertEqual(decision["decision"], "block")
        self.assertIn(stop_gate.VALIDATION_MISSING_REASON_TEMPLATE
                      % (self.tid, None), decision["reason"])
        # reason 内换行已被 json 转义——物理单行不因中文多行理由破坏
        self.assertNotIn("\n", json.dumps(decision, ensure_ascii=False))
        # 正常拦截不是降级：stderr 无 ENFORCEMENT DEGRADED
        self.assertNotIn("ENFORCEMENT DEGRADED", proc.stderr)
        self.assertEqual(self.status_text(), "active")


if __name__ == "__main__":
    unittest.main()
