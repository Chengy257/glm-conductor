#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""runtime.quota.watcher 单元测试（v2.2 修正计划 C3，wu-22-C3）。

锚定对象：修正计划 §7（ACTIVE / PASSIVE 模式判定，冻结）/ §6.1（最小
骨架范围）/ §10（epoch 观察）/ §6.2（工程约束：subprocess 一律
sys.executable）/ §C3（复用 observer / resolver / epoch，不复制额度
逻辑；observer.py 纯决策层零改动）。

运行：
    cd <repo_root> && python3 -m unittest tests.test_watcher -v

覆盖映射：
    1  determine_mode：waiting_quota → ACTIVE / executing+until_done /
       auto_once → ACTIVE（executing 族含 joining）/ executing+manual /
       notify → PASSIVE / 无任务 → PASSIVE / 坏 state 文件容错跳过
    2  next_poll_interval：PASSIVE 固定（900 / 自定）/ ACTIVE 复用
       observer 自适应表（NORMAL 1800、PRESSURE+reset 收紧）/ 相不可
       解析 fail-open 退化固定 / 参数校验
    3  run()：tick 注入式 fetch 成功（epoch_id / probe_boundary_at /
       per-window reset_at 摘要落 watcher.json）/ fetch 网络失败容错
       不崩（error 只记类型名）/ heartbeat 逐 wake 推进 + should_refresh
       到期再抓 / ACTIVE 间隔复用自适应表 / stop_requested 优雅退出
       （写 stopped 终态 pid=None）/ acquire 冲突零抓取
    4  run_once：无记录 → ran=True generation=1 / 活锁新鲜 → 冲突
       ran=False 零写盘 / stale（过期 heartbeat）→ 放行且 generation
       不 +1（不走锁接管）
    5  provider identity hash：同凭证稳定 / 异凭证不同 / 16 位十六进制
       （只落哈希不落凭证 §37）
    6  CLI quota-watcher：status / stop / once / 用法错 / start 派生
       argv 用 sys.executable（mock Popen，不真实派生子进程）
"""

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "glm-conductor"))
from runtime import cli
from runtime.quota import epoch as quota_epoch
from runtime.quota import watcher, watcher_store
from runtime.quota.scheduler import _format_iso_z

NOW = datetime(2026, 9, 2, 12, 0, 0, tzinfo=timezone.utc)
NOW_ISO = "2026-09-02T12:00:00Z"
RESET_ISO = "2026-09-03T00:00:00Z"
IDENTITY = "a1b2c3d4e5f60718"


def win(kind, remaining=None, reset_at=None):
    """构造 §27 形状的单窗口 dict（对齐 test_quota_observer 装置）。"""
    return {"kind": kind, "used_percent": None,
            "remaining_percent": remaining, "reset_at": reset_at}


def fake_detail(status, windows, source="provider"):
    """构造 resolver.resolve_quota_detail 的注入返回（provider 层形态）。"""
    return {"source": source, "status": status,
            "snapshot": {"provider": "fake", "windows": list(windows)},
            "fetched_at": "2026-09-02T11:59:59.000Z"}


class FakeClock:
    """注入时钟：每次调用推进固定步长（首个调用返回起始时刻）。"""

    def __init__(self, start, step_seconds=1.0):
        self.now = start
        self.step = timedelta(seconds=step_seconds)

    def __call__(self):
        current = self.now
        self.now = self.now + self.step
        return current


def noop_sleep(_seconds):
    """注入休眠：零真实等待。"""


class RepoFixture(unittest.TestCase):
    """tempdir scratch 仓库基座（绝不触碰仓库内真实账本）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = self._tmp.name

    def write_task_state(self, task_id, status, auto_resume=None):
        """手写最小 state.json（只读判定的输入形状；不走 save_state
        全量校验——模式判定只消费 status 与 continuity.auto_resume）。"""
        task_dir = Path(self.repo) / ".glm-conductor" / "tasks" / task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        data = {"task_id": task_id, "status": status}
        if auto_resume is not None:
            # 真实 state 模型键：execution_policy.continuity.auto_resume
            # （execution_policy.POLICY_SUB_BLOCKS 冻结名，非 continuation）
            data["execution_policy"] = {"continuity":
                                        {"auto_resume": auto_resume}}
        with open(task_dir / "state.json", "w", encoding="utf-8") as handle:
            json.dump(data, handle)

    def write_raw_state(self, payload):
        os.makedirs(Path(self.repo) / ".glm-conductor" / "quota",
                    exist_ok=True)
        watcher_store.write_watcher_state(self.repo, payload)

    def live_record(self, **overrides):
        """活进程持有的记录底形：pid = 测试进程（活到用例结束），
        heartbeat 以**真实当前时刻**写入（「新鲜」判定相对真实时钟——
        固定过去时刻会被判 stale 而走接管路径，令真实默认循环无法
        收敛；这对 once/serve 的冲突闸至关重要）。"""
        moment = datetime.now(timezone.utc)
        record = {
            "schema_version": 1,
            "provider_identity_hash": IDENTITY,
            "mode": "PASSIVE",
            "pid": os.getpid(),
            "started_at": _format_iso_z(moment),
            "heartbeat_at": _format_iso_z(moment),
            "generation": 1,
            "stop_requested": False,
            "last_observation": None,
        }
        record.update(overrides)
        return record

    def run_watcher(self, fetch, max_ticks=1, clock=None, **kwargs):
        return watcher.run(
            self.repo, fetch=fetch, clock=clock or FakeClock(NOW, 1.0),
            sleep=noop_sleep, max_ticks=max_ticks,
            provider_identity_hash=IDENTITY, **kwargs)


# —— 1：§7 模式判定 ——

class DetermineModeTest(RepoFixture):

    def test_waiting_quota_task_activates(self):
        """§7 冻结：waiting_quota 任务存在 → ACTIVE（auto_resume 无关）。"""
        self.write_task_state("t-wait", "waiting_quota")
        self.assertEqual(watcher.determine_mode(self.repo), "ACTIVE")

    def test_executing_until_done_activates(self):
        """§7：executing + auto_resume=until_done → ACTIVE。"""
        self.write_task_state("t-until", "executing", auto_resume="until_done")
        self.assertEqual(watcher.determine_mode(self.repo), "ACTIVE")

    def test_executing_auto_once_activates(self):
        """§7：executing + auto_resume=auto_once → ACTIVE。"""
        self.write_task_state("t-once", "joining", auto_resume="auto_once")
        self.assertEqual(watcher.determine_mode(self.repo), "ACTIVE")

    def test_executing_manual_stays_passive(self):
        """§7：manual 默认 PASSIVE（可观察但不 prime）。"""
        self.write_task_state("t-manual", "executing", auto_resume="manual")
        self.assertEqual(watcher.determine_mode(self.repo), "PASSIVE")

    def test_executing_notify_stays_passive(self):
        """§7：notify 默认 PASSIVE。"""
        self.write_task_state("t-notify", "verifying", auto_resume="notify")
        self.assertEqual(watcher.determine_mode(self.repo), "PASSIVE")

    def test_no_tasks_dir_is_passive(self):
        """无任务 → PASSIVE（无 active long-horizon continuity demand）。"""
        self.assertEqual(watcher.determine_mode(self.repo), "PASSIVE")

    def test_corrupt_state_files_skipped_defensively(self):
        """坏 state 文件（半份 JSON / 非 dict）跳过不影响判定；
        全坏 → PASSIVE，坏 + 合法 waiting_quota → ACTIVE。"""
        tasks_root = Path(self.repo) / ".glm-conductor" / "tasks"
        (tasks_root / "t-broken").mkdir(parents=True)
        (tasks_root / "t-broken" / "state.json").write_text(
            '{"status": ', encoding="utf-8")
        self.assertEqual(watcher.determine_mode(self.repo), "PASSIVE")
        self.write_task_state("t-good", "waiting_quota")
        self.assertEqual(watcher.determine_mode(self.repo), "ACTIVE")


# —— 2：轮询间隔选择（ACTIVE 复用 observer / PASSIVE 固定） ——

class NextPollIntervalTest(unittest.TestCase):

    def test_passive_fixed_default_900(self):
        """PASSIVE 低频固定间隔：默认 900 秒（§6.1 规格）。"""
        self.assertEqual(watcher.next_poll_interval(mode="PASSIVE", now=NOW),
                         900)
        self.assertEqual(watcher.PASSIVE_INTERVAL_SECONDS, 900)
        self.assertEqual(
            watcher.next_poll_interval(mode="PASSIVE", now=NOW,
                                       passive_interval_seconds=1200), 1200)

    def test_active_reuses_observer_adaptive_table(self):
        """ACTIVE 复用 observer.next_check_interval（observer.py 零改动
        的复用锚）：NORMAL 1800；PRESSURE + reset 收紧到边界前 grace。"""
        self.assertEqual(
            watcher.next_poll_interval(mode="ACTIVE",
                                       execution_phase="NORMAL", now=NOW),
            1800)
        # reset = now+700s → deadline = now+700-300(grace) → 400
        self.assertEqual(
            watcher.next_poll_interval(
                mode="ACTIVE", execution_phase="PRESSURE",
                reset_at=_format_iso_z(NOW + timedelta(seconds=700)),
                now=NOW),
            400)

    def test_active_unresolvable_phase_falls_back_to_fixed(self):
        """抓取失败 / 相不可解析（None）→ ACTIVE 退化固定间隔，
        绝不虚构执行相（§31 同源纪律）。"""
        self.assertEqual(
            watcher.next_poll_interval(mode="ACTIVE", execution_phase=None,
                                       now=NOW,
                                       passive_interval_seconds=777), 777)

    def test_parameter_validation(self):
        """mode 词汇外 / 间隔非正整数 → ValueError。"""
        with self.assertRaises(ValueError):
            watcher.next_poll_interval(mode="Hyper", now=NOW)
        with self.assertRaises(ValueError):
            watcher.next_poll_interval(mode="PASSIVE", now=NOW,
                                       passive_interval_seconds=0)


# —— 3：run() 长运行循环（注入时钟 / fetch / sleep） ——

class RunLoopTest(RepoFixture):

    def test_first_tick_fetches_and_records_epoch_observation(self):
        """tick 注入 fetch 成功：watcher.json 记录 heartbeat / mode /
        last_observation（epoch_id、probe_boundary_at 与 epoch 纯模型
        直调逐字一致——不复制 epoch 数学的锚；windows 为 per-window
        reset_at 摘要）。"""
        windows = [win("five_hour", 50, RESET_ISO),
                   win("weekly", None, None)]
        self.run_watcher(lambda repo: fake_detail("AVAILABLE", windows),
                         max_ticks=1)
        record = watcher_store.read_watcher_state(self.repo)
        self.assertIsNotNone(record)
        self.assertEqual(record["mode"], "PASSIVE")  # 无任务 → PASSIVE
        self.assertEqual(record["generation"], 1)
        self.assertEqual(record["pid"], os.getpid())
        self.assertEqual(record["heartbeat_at"],
                         _format_iso_z(NOW + timedelta(seconds=1)))
        observation = record["last_observation"]
        self.assertEqual(observation["status"], "AVAILABLE")
        self.assertEqual(observation["source"], "provider")
        self.assertIsNone(observation["error"])
        self.assertEqual(observation["epoch_id"],
                         quota_epoch.epoch_id(windows))
        self.assertEqual(observation["probe_boundary_at"],
                         quota_epoch.probe_boundary_at(windows))
        self.assertEqual(observation["windows"],
                         quota_epoch.canonical_windows(windows))
        # PASSIVE 固定间隔 → next_poll_at = heartbeat 时刻 + 900
        self.assertEqual(record["next_poll_at"],
                         _format_iso_z(NOW + timedelta(seconds=901)))

    def test_fetch_failure_tolerated_no_crash(self):
        """fetch 抛异常（模拟网络失败）→ 循环不崩；error 只记类型名
        （绝不透传异常文本），epoch 键全部 None 不虚构，heartbeat 照写。"""
        def exploding_fetch(repo_root):
            raise OSError("network unreachable: http://secret")

        self.run_watcher(exploding_fetch, max_ticks=2)
        record = watcher_store.read_watcher_state(self.repo)
        observation = record["last_observation"]
        self.assertEqual(observation["error"], "OSError")
        self.assertIsNone(observation["status"])
        self.assertIsNone(observation["epoch_id"])
        self.assertIsNone(observation["probe_boundary_at"])
        self.assertEqual(record["heartbeat_at"],
                         _format_iso_z(NOW + timedelta(seconds=2)))

    def test_heartbeat_advances_each_wake_and_poll_respects_should_refresh(self):
        """heartbeat 逐 wake 推进；未到 next_poll_at 的 wake 只续
        heartbeat 绝不空转打 provider（should_refresh 的生产用途）；
        到期才二次抓取。"""
        fetches = {"n": 0}

        def counting_fetch(repo_root):
            fetches["n"] += 1
            return fake_detail("AVAILABLE", [win("five_hour", 50, RESET_ISO)])

        # passive_interval=2s + 时钟步 1s：tick1 抓取（next=+3s），
        # tick2 未到期仅心跳，tick3 到期二次抓取
        result = self.run_watcher(counting_fetch, max_ticks=3,
                                  passive_interval_seconds=2)
        self.assertEqual(result["fetches"], 2)
        self.assertEqual(result["wakes"], 3)
        self.assertEqual(result["last_interval"], 2)
        record = watcher_store.read_watcher_state(self.repo)
        self.assertEqual(record["heartbeat_at"],
                         _format_iso_z(NOW + timedelta(seconds=3)))

    def test_active_mode_uses_adaptive_interval(self):
        """ACTIVE（waiting_quota 任务）+ NORMAL 窗 → 间隔复用 observer
        自适应表（1800），next_poll_at 据此推进。"""
        self.write_task_state("t-active", "waiting_quota")
        windows = [win("five_hour", 90, None)]  # NORMAL（reset 未知）
        result = self.run_watcher(
            lambda repo: fake_detail("AVAILABLE", windows), max_ticks=1)
        self.assertEqual(result["mode"], "ACTIVE")
        self.assertEqual(result["last_interval"], 1800)
        record = watcher_store.read_watcher_state(self.repo)
        self.assertEqual(record["mode"], "ACTIVE")
        self.assertEqual(record["next_poll_at"],
                         _format_iso_z(NOW + timedelta(seconds=1801)))

    def test_stop_requested_graceful_exit_writes_stopped_state(self):
        """stop_requested 旗标 → 循环写 stopped 终态（pid=None，冻结
        字段集内表达）并退出；不再发起抓取。"""
        def stopper_fetch(repo_root):
            watcher_store.request_stop(repo_root)
            return fake_detail("AVAILABLE", [])

        result = self.run_watcher(stopper_fetch, max_ticks=10)
        self.assertTrue(result["stopped"])
        self.assertEqual(result["fetches"], 1)  # 第二 wake 见旗标即退
        self.assertEqual(result["wakes"], 2)
        record = watcher_store.read_watcher_state(self.repo)
        self.assertTrue(record["stop_requested"])
        self.assertIsNone(record["pid"])  # stopped 表达：无存活持有者
        self.assertEqual(record["generation"], 1)

    def test_acquire_conflict_returns_without_fetching(self):
        """锁被活进程新鲜持有（§6）→ run 不抓取、不改写状态文件。"""
        self.write_raw_state(self.live_record(generation=3))
        fetches = {"n": 0}

        def counting_fetch(repo_root):
            fetches["n"] += 1
            return fake_detail("AVAILABLE", [])

        result = self.run_watcher(counting_fetch, max_ticks=5)
        self.assertFalse(result["acquired"])
        self.assertEqual(result["conflict"]["pid"], os.getpid())
        self.assertEqual(fetches["n"], 0)
        self.assertEqual(
            watcher_store.read_watcher_state(self.repo)["generation"], 3)

    def test_heartbeat_step_must_be_below_stale_threshold(self):
        """休眠步长必须 < heartbeat 新鲜阈值（heartbeat 永不在休眠中
        过期——锁不被误接管的守护闸）。"""
        with self.assertRaises(ValueError):
            self.run_watcher(lambda repo: fake_detail("AVAILABLE", []),
                             heartbeat_step_seconds=180)


# —— 4：run_once（once 语义） ——

class RunOnceTest(RepoFixture):

    def test_once_without_record_runs_and_writes_generation_1(self):
        windows = [win("five_hour", 50, RESET_ISO)]
        result = watcher.run_once(
            self.repo, fetch=lambda repo: fake_detail("AVAILABLE", windows),
            clock=FakeClock(NOW, 1.0), provider_identity_hash=IDENTITY)
        self.assertTrue(result["ran"])
        record = result["record"]
        self.assertEqual(record["generation"], 1)
        self.assertEqual(record["mode"], "PASSIVE")
        self.assertEqual(record["pid"], os.getpid())
        self.assertEqual(record["last_observation"]["epoch_id"],
                         quota_epoch.epoch_id(windows))
        self.assertEqual(
            watcher_store.read_watcher_state(self.repo)["generation"], 1)

    def test_once_conflict_with_live_fresh_lock(self):
        """once 不走锁接管：活 pid + 新鲜 heartbeat → 冲突（ran=False）、
        零抓取、状态文件零改写。"""
        self.write_raw_state(self.live_record(generation=4))
        fetches = {"n": 0}

        def counting_fetch(repo_root):
            fetches["n"] += 1
            return fake_detail("AVAILABLE", [])

        result = watcher.run_once(self.repo, fetch=counting_fetch,
                                  clock=FakeClock(NOW, 1.0),
                                  provider_identity_hash=IDENTITY)
        self.assertFalse(result["ran"])
        self.assertEqual(result["conflict"]["pid"], os.getpid())
        self.assertEqual(fetches["n"], 0)
        self.assertEqual(
            watcher_store.read_watcher_state(self.repo)["generation"], 4)

    def test_once_proceeds_on_stale_heartbeat_without_takeover(self):
        """stale（heartbeat 过期）→ once 放行执行，generation 不 +1
        （不是接管；pid 写为 once 进程，退出即死）。"""
        self.write_raw_state(self.live_record(
            generation=4,
            heartbeat_at=_format_iso_z(NOW - timedelta(seconds=1000))))
        result = watcher.run_once(
            self.repo, fetch=lambda repo: fake_detail("AVAILABLE", []),
            clock=FakeClock(NOW, 1.0), provider_identity_hash=IDENTITY)
        self.assertTrue(result["ran"])
        self.assertEqual(result["record"]["generation"], 4)  # 不 +1
        self.assertEqual(result["record"]["pid"], os.getpid())


# —— 5：provider identity hash（§37：只落哈希不落凭证） ——

class ProviderIdentityTest(unittest.TestCase):

    def test_deterministic_per_credential_and_distinct_across(self):
        env_a = {"GLM_CONDUCTOR_QUOTA_API_KEY": "key-a"}
        env_b = {"GLM_CONDUCTOR_QUOTA_API_KEY": "key-b"}
        self.assertEqual(watcher.compute_provider_identity_hash(env_a),
                         watcher.compute_provider_identity_hash(env_a))
        self.assertNotEqual(watcher.compute_provider_identity_hash(env_a),
                            watcher.compute_provider_identity_hash(env_b))

    def test_hash_shape_and_no_key_material(self):
        identity = watcher.compute_provider_identity_hash(
            {"GLM_CONDUCTOR_QUOTA_API_KEY": "super-secret-key"})
        self.assertEqual(len(identity), 16)
        int(identity, 16)  # 十六进制（sha256 截断）
        self.assertNotIn("super-secret-key", identity)


# —— 6：CLI quota-watcher 子命令 ——

def run_cli(*args):
    """调用 cli.main 并捕获 stdout，返回 (退出码, 解析后 JSON)。"""
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        code = cli.main(list(args))
    return code, json.loads(buffer.getvalue())


class QuotaWatcherCliTest(RepoFixture):

    def test_status_without_record_exit_0(self):
        """status：无记录不是错误 → active=false，退出码 0。"""
        code, payload = run_cli("quota-watcher", self.repo, "status")
        self.assertEqual(code, 0)
        self.assertFalse(payload["active"])
        self.assertIsNone(payload["record"])
        self.assertIn("watcher.json", payload["state"])

    def test_status_summary_with_record(self):
        """status：读 watcher.json 输出 mode/pid/generation/heartbeat/
        staleness/last_observation 摘要（heartbeat 以真实当前时刻写入，
        staleness 断言与 age 一致——不与墙钟硬编码耦合）。"""
        moment = datetime.now(timezone.utc)
        self.write_raw_state(self.live_record(
            mode="ACTIVE", generation=2,
            started_at=_format_iso_z(moment),
            heartbeat_at=_format_iso_z(moment)))
        code, payload = run_cli("quota-watcher", self.repo, "status")
        self.assertEqual(code, 0)
        self.assertTrue(payload["active"])
        self.assertEqual(payload["mode"], "ACTIVE")
        self.assertEqual(payload["pid"], os.getpid())
        self.assertEqual(payload["generation"], 2)
        self.assertFalse(payload["heartbeat_stale"])
        self.assertLess(payload["heartbeat_age_seconds"], 180)
        self.assertFalse(payload["stop_requested"])

    def test_stop_sets_flag_single_shot_idempotent(self):
        """stop：置旗标（原子写回）即返回；无记录幂等成功（退出码 0）。"""
        code, payload = run_cli("quota-watcher", self.repo, "stop")
        self.assertEqual(code, 0)
        self.assertFalse(payload["stop_requested"])  # 无记录幂等
        self.write_raw_state(self.live_record(generation=5))
        code, payload = run_cli("quota-watcher", self.repo, "stop")
        self.assertEqual(code, 0)
        self.assertTrue(payload["stop_requested"])
        self.assertTrue(watcher_store.read_watcher_state(
            self.repo)["stop_requested"])

    def test_once_conflict_exit_1(self):
        """once：锁被活进程新鲜持有 → 错误 JSON + 退出码 1。"""
        self.write_raw_state(self.live_record())
        code, payload = run_cli("quota-watcher", self.repo, "once")
        self.assertEqual(code, 1)
        self.assertIn("单实例锁", payload["error"])

    def test_once_success_shape(self):
        """once：成功输出观察摘要（run_once 注入返回——CLI 薄壳只做
        形状与退出码，API 自身归 RunOnceTest）。"""
        detail = fake_detail("AVAILABLE", [win("five_hour", 50, RESET_ISO)])
        record = self.live_record(mode="PASSIVE", generation=1)
        record["last_observation"] = watcher._build_observation(detail, None,
                                                                NOW)
        with mock.patch.object(watcher, "run_once",
                               return_value={"ran": True, "record": record,
                                             "conflict": None}):
            code, payload = run_cli("quota-watcher", self.repo, "once")
        self.assertEqual(code, 0)
        self.assertTrue(payload["ran"])
        self.assertEqual(payload["epoch_id"], quota_epoch.epoch_id(
            [win("five_hour", 50, RESET_ISO)]))
        self.assertEqual(payload["status"], "AVAILABLE")

    def test_usage_errors_exit_2(self):
        """用法错：缺参数 / 未知动作 / 未知子命令 → 退出码 2。"""
        for argv in (
                ("quota-watcher",),
                ("quota-watcher", self.repo),
                ("quota-watcher", self.repo, "explode"),
                ("quota-watcher", self.repo, "status", "extra")):
            code, payload = run_cli(*argv)
            self.assertEqual(code, 2, argv)
            self.assertIn("error", payload)

    def test_start_spawns_detached_serve_with_sys_executable(self):
        """start（§6.2 工程约束）：subprocess 一律 sys.executable；argv
        为本 CLI 的 `quota-watcher <repo> --serve` 内部隐藏形态；Windows
        分离派生带 DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP；日志落
        watcher.log。mock Popen——不真实派生子进程。"""
        import subprocess
        with mock.patch.object(subprocess, "Popen") as popen:
            popen.return_value.pid = 424242
            code, payload = run_cli("quota-watcher", self.repo, "start")
        self.assertEqual(code, 0)
        self.assertTrue(payload["started"])
        self.assertEqual(payload["pid"], 424242)
        argv = popen.call_args[0][0]  # Python 3.7：call_args 无 .args 属性
        self.assertEqual(argv[0], sys.executable)
        self.assertEqual(argv[3], self.repo)
        self.assertEqual(argv[-1], "--serve")
        self.assertTrue(argv[1].endswith("cli.py"))
        kwargs = popen.call_args[1]  # Python 3.7：call_args 无 .kwargs 属性
        if os.name == "nt":
            self.assertEqual(
                kwargs["creationflags"],
                subprocess.DETACHED_PROCESS
                | subprocess.CREATE_NEW_PROCESS_GROUP)
        else:
            self.assertTrue(kwargs["start_new_session"])
        self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
        self.assertIn("watcher.log", payload["log"])
        self.assertTrue(os.path.isfile(payload["log"]))  # 日志文件已建

    def test_serve_conflict_returns_1_without_network(self):
        """--serve 内部隐藏形态：锁冲突 → 退出码 1（冲突发生在抓取前，
        零网络）；死 pid 记录被接管后 graceful 路径（注入 max_ticks +
        fetch）→ 退出码 0。"""
        self.write_raw_state(self.live_record())
        with redirect_stderr(io.StringIO()):  # 冲突详情落 stderr（模拟日志）
            code = cli.main(["quota-watcher", self.repo, "--serve"])
        self.assertEqual(code, 1)
        # 接管路径：pid=0（死）→ serve acquire 接管 → max_ticks=1 收敛
        self.write_raw_state(self.live_record(pid=0))
        code = watcher.serve(
            self.repo, fetch=lambda repo: fake_detail("AVAILABLE", []),
            clock=FakeClock(datetime.now(timezone.utc), 1.0),
            sleep=noop_sleep, max_ticks=1,
            provider_identity_hash=IDENTITY)
        self.assertEqual(code, 0)
        record = watcher_store.read_watcher_state(self.repo)
        self.assertEqual(record["generation"], 2)  # 接管 +1


if __name__ == "__main__":
    unittest.main()
