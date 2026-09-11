#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""quota-clock 四个 CLI 子命令测试（v2.3.0 计划 §8，unit v23-w2b）。

锚定对象：runtime/cli.py 的 quota-clock-plan / bind / tick / status
（resolver + identity + zcode_schedule adapter + clock_store 的接线薄
壳层）。测试类映射：

    ClockPlacementPureTest  v2.3.1（w1-clock-ux）Placement UX 纯函数：
                            plan/bind/status 的会话放置引导 / 成本
                            提示 / needs_replacement 两极判定（决策
                            c）与恢复指引（未执行自动会话迁移声明）
    PlanCliTest             plan：输出键集与 first_target 数学
                            （reset+120s）、force-refresh 透传、相对
                            repo_root 归一、five_hour 缺失 / 不可解析
                            → not_plannable 非零、纯规划零写盘、用法 2
    BindCliTest             bind：全流程（discover/inspect/retime 以
                            正确参数调用、state 落盘、model_is_flash
                            判定）、inspect None / 非 recurring /
                            retime 失败 → 非零、--db 透传、用法 2
    TickCliTest             tick：正常路径（retime 收到 decision
                            target、state 更新 last_tick_at /
                            next_target_at / last_reset_at / status）、
                            weekly_park → parked_weekly、retime 抛错 →
                            retimed:false 且 next_target_at 未变退出 0、
                            state 缺失 → 非零且零 provider 解析、
                            runtime_path §10.5 自愈、state.last_reset_at
                            向决策层传递（reconfirm / window_advanced
                            各验一次）、用法 2
    StatusCliTest           status：unbound 三键形态、healthy 全绿
                            （零 provider / 零 retime）、行缺失 /
                            target 不一致 / tick 陈旧（含界内不判陈旧
                            边界）/ 路径悬空 / inspect 异常各 unhealthy
                            分支、用法 2

全部离线：resolver / identity / discover / inspect / retime 一律
mock.patch（零网络，绝不触碰真实 ~/.zcode DB 与真实 ~）；clock state
经 GLM_CONDUCTOR_HOME 注入 tempfile 临时目录（手法同
tests/test_quota_clock.py）。CLI 调用照 tests/test_cli_extensions.py
先例：cli.main(list) + contextlib.redirect_stdout 捕获单行 JSON。
unittest + unittest.mock，零 pytest。

运行：
    cd <repo_root> && python3 -S -m unittest tests.test_quota_clock_cli -v
"""

import io
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from contextlib import contextmanager, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "glm-conductor"))
from runtime import cli
from runtime.host import zcode_schedule
from runtime.quota import clock, clock_store

# —— 常量与装置 ——

HASH = "testidentityhash"
AID = "auto-clock-cli"
DB = "X:/mock/tasks-index.sqlite"
OTHER_DB = "X:/mock/other.sqlite"
RUNTIME_DIR = os.path.dirname(os.path.abspath(cli.__file__))
CLI_PATH = RUNTIME_DIR + "/cli.py"
BIND_NOW_MS = 1750000000000


def z_of(moment):
    """aware datetime → 整秒 Z 形串（独立期望值折算 ms_of 的输入形态）。"""
    return moment.replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def ms_of(iso_z):
    """独立的期望值计算（绝不复用被测模块解析）：整秒 Z 形串 → epoch 毫秒。"""
    moment = datetime.strptime(iso_z, "%Y-%m-%dT%H:%M:%SZ")
    return int(moment.replace(tzinfo=timezone.utc).timestamp() * 1000)


# reset 必须严格在未来（决策表第 5 行：B <= now → short_retry）——
# 相对真实 now 动态构造，避免固定日期随时间腐烂成过去时刻。
_NOW = datetime.now(timezone.utc)
RESET = z_of(_NOW + timedelta(hours=1))
WEEKLY_RESET = z_of(_NOW + timedelta(days=2))
RESET_MS = ms_of(RESET)
WEEKLY_MS = ms_of(WEEKLY_RESET)
FIRST_TARGET = RESET_MS + 120000   # reset + DEFAULT_GRACE_SECONDS=120s
WEEKLY_TARGET = WEEKLY_MS + 120000
OLD_TARGET = RESET_MS - 3600000    # 上一次成功 tick 的目标（≠本次决策）


def win(kind, status=None, reset_at=None):
    """§27 形状单窗口 dict（clock 决策层测试同款构造）。"""
    return {"kind": kind, "status": status, "reset_at": reset_at}


def detail_of(*windows):
    """resolve_quota_detail 明细形状（键冻结六键，snapshot 透出 windows）。"""
    return {"status": "EXHAUSTED", "source": "provider",
            "evaluated_at": "2026-09-02T20:59:00.000Z",
            "reason": "测试注入（零网络）",
            "snapshot": {"windows": list(windows)},
            "fetched_at": "2026-09-02T20:59:00.000Z"}


FIVE_DETAIL = detail_of(win("five_hour", "EXHAUSTED", RESET))
WEEKLY_ONLY_DETAIL = detail_of(win("weekly", "AVAILABLE", WEEKLY_RESET))
UNPARSABLE_DETAIL = detail_of(win("five_hour", "EXHAUSTED", "garbage"))
PARK_DETAIL = detail_of(win("five_hour", "AVAILABLE", RESET),
                        win("weekly", "EXHAUSTED", WEEKLY_RESET))

ROW_FLASH = {"automation_id": AID, "recurring": 1,
             "model": "GLM-5.3-Flash", "next_run_at": OLD_TARGET,
             "run_count": 2}


def run_cli(*args):
    """调用 cli.main 并捕获 stdout，返回 (退出码, 解析后 JSON)。"""
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        code = cli.main(list(args))
    return code, json.loads(buffer.getvalue())


_UNSET = object()


@contextmanager
def patched_quota_clock(detail=None, *, db=DB, row=_UNSET,
                        retime_error=None, inspect_error=None):
    """一次叠齐 resolver + adapter（discover / inspect / retime）patch
    （identity 由 ClockCliCase.setUp 统一 patch 成固定指纹）；yield
    (resolver_mock, inspect_mock, retime_mock, discover_mock)。row 为
    _UNSET → inspect 返回 None（行缺失）；inspect_error 给定时以
    side_effect 注入 inspect 异常。"""
    inspect_kwargs = ({"side_effect": inspect_error}
                      if inspect_error is not None
                      else {"return_value": None if row is _UNSET else row})
    with mock.patch(
            "runtime.quota.resolver.resolve_quota_detail",
            return_value=detail) as resolver_mock, \
            mock.patch(
                "runtime.host.zcode_schedule.discover_zcode_tasks_db",
                return_value=db) as discover_mock, \
            mock.patch(
                "runtime.host.zcode_schedule.inspect_automation",
                **inspect_kwargs) as inspect_mock, \
            mock.patch(
                "runtime.host.zcode_schedule.retime_automation",
                side_effect=retime_error) as retime_mock:
        yield resolver_mock, inspect_mock, retime_mock, discover_mock


class ClockCliCase(unittest.TestCase):
    """基座：GLM_CONDUCTOR_HOME 注入 tempfile（绝不触碰真实
    ~/.glm-conductor）+ identity 指纹 patch 成固定 16-hex（零凭证读
    取）；repo 为 tempfile scratch 目录（resolver 已 mock，零真实读）。"""

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="glm-clock-cli-home-")
        self.addCleanup(shutil.rmtree, self.home, True)
        env = mock.patch.dict(os.environ, {
            clock_store.GLM_CONDUCTOR_HOME_ENV: self.home})
        env.start()
        self.addCleanup(env.stop)
        self.repo = tempfile.mkdtemp(prefix="glm-clock-cli-repo-")
        self.addCleanup(shutil.rmtree, self.repo, True)
        identity = mock.patch(
            "runtime.quota.identity.compute_provider_identity_hash",
            return_value=HASH)
        identity.start()
        self.addCleanup(identity.stop)

    def repo_norm(self):
        """CLI 侧 repo_root 归一口径的独立期望值（state 层 RB-2 同式：
        str(Path(os.path.abspath(repo)).resolve())）。"""
        return str(Path(os.path.abspath(self.repo)).resolve())

    def state_path(self):
        return clock_store.clock_state_path(HASH)

    def load_state(self):
        return clock_store.load_clock_state(HASH)

    def bind_state(self, **overrides):
        """真实 bind_clock_state 预置 state（写入被注入的临时 home；
        adapter 全 mock，故 db 路径可为不存在的占位串）。"""
        params = {"zcode_db_path": DB, "runtime_path": RUNTIME_DIR,
                  "automation_model": "GLM-5.3-Flash",
                  "now_ms": BIND_NOW_MS}
        params.update(overrides)
        return clock_store.bind_clock_state(self.repo, HASH, AID, **params)


# —— 0. Placement UX 纯函数（v2.3.1，unit w1-clock-ux） ——

class ClockPlacementPureTest(unittest.TestCase):
    """clock.py 的 Placement UX advisory 纯函数：文案语义齐备 +
    判定两极（needs_replacement 锁定决策 c；model_is_flash 仅
    advisory——不阻断、不作专用会话证据；dedicated_session 恒为
    常量串，绝不伪造布尔）。"""

    def test_plan_placement_guidance_semantics(self):
        guidance = clock.placement_guidance_for_plan()
        self.assertEqual(set(guidance),
                         set(clock.PLACEMENT_GUIDANCE_KEYS))
        self.assertIn("recurring", guidance["automation_required"])
        steps = guidance["recommended_steps"]
        self.assertEqual(len(steps), 4)
        joined = "\n".join(steps)
        # 建议 1)-4)：独立会话 / Flash 模型 / 建议命名 / 该会话内设置
        self.assertIn("独立", joined)
        self.assertIn("Flash", joined)
        self.assertIn(clock.SUGGESTED_AUTOMATION_NAME, joined)
        self.assertIn("该专用会话", joined)
        self.assertEqual(guidance["suggested_name"],
                         clock.SUGGESTED_AUTOMATION_NAME)
        # 警示：勿在主编码会话创建（周期 tick 会注入该会话）
        self.assertIn("主编码会话", guidance["warning"])
        self.assertIn("tick", guidance["warning"])

    def test_bind_session_placement_semantics(self):
        placement = clock.bind_session_placement()
        self.assertEqual(set(placement),
                         set(clock.SESSION_PLACEMENT_KEYS))
        self.assertIn("当前 ZCode 会话", placement["attachment"])
        self.assertIn("本对话", placement["tick_effect"])
        self.assertIn("Flash", placement["recommendation"])
        self.assertIn("主编排", placement["avoid"])

    def test_cost_advisory_polarity(self):
        # Flash 类（模型名含 "Flash"）→ None；其余（含未知 None）→ 提示
        self.assertIsNone(clock.cost_advisory_for_model("GLM-5.3-Flash"))
        self.assertIsNone(clock.cost_advisory_for_model("xFlashy"))
        self.assertEqual(
            clock.cost_advisory_for_model("glm-5.3"),
            {"current_model": "glm-5.3",
             "preferred": clock.PREFERRED_MODEL_ADVICE})
        self.assertEqual(
            clock.cost_advisory_for_model(None),
            {"current_model": None,
             "preferred": clock.PREFERRED_MODEL_ADVICE})

    def test_model_is_flash_advisory_only_judgment(self):
        self.assertTrue(clock.model_is_flash("GLM-5.3-Flash"))
        self.assertFalse(clock.model_is_flash("glm-5.3"))
        self.assertFalse(clock.model_is_flash(""))
        self.assertFalse(clock.model_is_flash(None))
        self.assertFalse(clock.model_is_flash(7))  # 非 str 绝不抛

    def test_status_placement_block_polarity(self):
        active = clock.status_placement_block(
            "GLM-5.3-Flash", automation_row_available=True)
        self.assertEqual(set(active), set(clock.STATUS_PLACEMENT_KEYS))
        self.assertEqual(active, {
            "session_binding": "active", "preferred_model": True,
            "dedicated_session": "not_mechanically_verifiable"})
        unavailable = clock.status_placement_block(
            None, automation_row_available=False)
        self.assertEqual(unavailable, {
            "session_binding": "unavailable", "preferred_model": False,
            "dedicated_session": "not_mechanically_verifiable"})
        # dedicated_session 恒为常量串（机械不可验证），绝不输出布尔
        self.assertIsInstance(unavailable["dedicated_session"], str)
        self.assertEqual(unavailable["dedicated_session"],
                         clock.DEDICATED_SESSION_UNVERIFIABLE)

    def test_needs_replacement_two_pole_decision(self):
        # True ⇔ db_row_missing / db_inspect_failed；其余仅 advisory
        self.assertTrue(clock.needs_replacement_from_reasons(
            ["db_row_missing"]))
        self.assertTrue(clock.needs_replacement_from_reasons(
            ["db_inspect_failed: OSError: gone"]))
        self.assertTrue(clock.needs_replacement_from_reasons(
            ["target_mismatch", "db_row_missing"]))
        self.assertFalse(clock.needs_replacement_from_reasons([]))
        self.assertFalse(clock.needs_replacement_from_reasons(
            ["target_mismatch", "tick_stale", "runtime_path_missing"]))
        # 诊断面容错：非 list / 元素非 str 绝不抛、绝不触发
        self.assertFalse(clock.needs_replacement_from_reasons(None))
        self.assertFalse(
            clock.needs_replacement_from_reasons("db_row_missing"))
        self.assertFalse(clock.needs_replacement_from_reasons([7]))

    def test_recovery_guidance_explicit_replace_no_auto_migration(self):
        text = clock.recovery_guidance_for_replacement()
        self.assertIn("replace", text)
        self.assertIn("quota-clock-bind <new_automation_id>", text)
        self.assertIn("Flash", text)
        self.assertIn("未执行自动会话迁移", text)
        self.assertEqual(clock.NO_AUTO_MIGRATION_DECLARATION,
                         "未执行自动会话迁移")

    def test_recovery_guidance_mentions_dead_binding_auto_detect(self):
        # w35-dogfood-fix 指引同步：recovery_guidance 与新 bind 行为
        # 同口径——bind 自动检测并替换已确认失效的死绑定（宿主 DB 行
        # 缺失时放行改绑），用户仍显式提供新 automation_id
        text = clock.recovery_guidance_for_replacement()
        self.assertIn("auto-detects a verified dead binding", text)
        self.assertIn("you still provide the new automation_id", text)

    def test_status_host_block(self):
        self.assertEqual(clock.status_host_block(),
                         {"adapter": "zcode_sqlite"})
        self.assertEqual(clock.HOST_ADAPTER, "zcode_sqlite")


# —— 1. quota-clock-plan ——

class PlanCliTest(ClockCliCase):
    """plan：键集与 first_target 数学 + 归一 + not_plannable + 用法 2。"""

    def test_plan_keys_and_first_target_math(self):
        with patched_quota_clock(FIVE_DETAIL) as (resolver_mock, *_rest):
            code, payload = run_cli("quota-clock-plan", self.repo)
        self.assertEqual(code, 0)
        self.assertEqual(
            set(payload),
            {"status", "provider_identity_hash", "reset_at",
             "reset_at_epoch_ms", "first_target_epoch_ms", "grace_seconds",
             "retry_delay_seconds", "fallback_interval_minutes",
             "runtime_path", "cli_command", "suggested_prompt",
             "placement_guidance"})
        self.assertEqual(payload["status"], "planned")
        self.assertEqual(payload["provider_identity_hash"], HASH)
        self.assertEqual(payload["reset_at"], RESET)
        self.assertEqual(payload["reset_at_epoch_ms"], RESET_MS)
        # first_target 数学：B + DEFAULT_GRACE_SECONDS(120s)
        self.assertEqual(payload["first_target_epoch_ms"], FIRST_TARGET)
        self.assertEqual(payload["first_target_epoch_ms"],
                         RESET_MS + 120 * 1000)
        self.assertEqual(payload["grace_seconds"], 120)
        self.assertEqual(payload["retry_delay_seconds"], 300)
        self.assertEqual(payload["fallback_interval_minutes"], 60)
        self.assertEqual(payload["runtime_path"], RUNTIME_DIR)
        self.assertEqual(
            payload["cli_command"],
            "%s %s quota-clock-tick %s" % (sys.executable, CLI_PATH,
                                           self.repo_norm()))
        # §9 极简 prompt：逐字头部 + 代入后的 tick 命令（F-4：首跳
        # 解释器 = 当前解释器 sys.executable）+ 收尾约束
        self.assertTrue(payload["suggested_prompt"].startswith(
            "GLM CONDUCTOR QUOTA CLOCK TICK\n"
            "Run: %s %s quota-clock-tick %s\n"
            % (sys.executable, CLI_PATH, self.repo_norm())))
        self.assertIn("Never call Cron tools. Never do anything else.",
                      payload["suggested_prompt"])
        resolver_mock.assert_called_once_with(self.repo_norm(),
                                              force_refresh=True)
        # 纯规划：零写盘（quota-clocks 目录不产生）
        self.assertFalse(os.path.isdir(
            os.path.join(self.home, "quota-clocks")))

    def test_plan_emits_placement_guidance_before_any_creation(self):
        # v2.3.1：placement_guidance 在 automation 创建前即可见——纯
        # 规划零副作用语义不变（零 DB、零 state、quota-clocks 不产生）
        with patched_quota_clock(FIVE_DETAIL) as (resolver_m, *_rest):
            code, payload = run_cli("quota-clock-plan", self.repo)
        self.assertEqual(code, 0)
        guidance = payload["placement_guidance"]
        self.assertEqual(set(guidance),
                         set(clock.PLACEMENT_GUIDANCE_KEYS))
        self.assertIn("recurring", guidance["automation_required"])
        self.assertEqual(guidance["suggested_name"],
                         clock.SUGGESTED_AUTOMATION_NAME)
        self.assertIn("主编码会话", guidance["warning"])
        self.assertIn("tick", guidance["warning"])
        resolver_m.assert_called_once()
        # 纯规划：零写盘（quota-clocks 目录不产生）
        self.assertFalse(os.path.isdir(
            os.path.join(self.home, "quota-clocks")))

    def test_plan_not_plannable_when_five_hour_missing_or_unparsable(self):
        for detail, reason in ((WEEKLY_ONLY_DETAIL, "five_hour_missing"),
                               (UNPARSABLE_DETAIL,
                                "five_hour_unparsable")):
            with self.subTest(reason=reason):
                with patched_quota_clock(detail):
                    code, payload = run_cli("quota-clock-plan", self.repo)
                self.assertEqual(code, 1)
                self.assertEqual(payload["status"], "not_plannable")
                self.assertEqual(payload["reason"], reason)
                self.assertIn("error", payload)
                self.assertIsNone(self.load_state())  # 零写盘

    def test_plan_interpreter_is_current_executable(self):
        # F-4（w35-dogfood-fix）：plan 产物首跳解释器 = 当前解释器
        # （sys.executable），cli_command 与 suggested_prompt 两点
        # 同源，绝不裸 "python3"（Windows 无 python3 启动器首跳必败）
        with patched_quota_clock(FIVE_DETAIL):
            code, payload = run_cli("quota-clock-plan", self.repo)
        self.assertEqual(code, 0)
        self.assertTrue(payload["cli_command"].startswith(
            "%s " % sys.executable))
        lines = payload["suggested_prompt"].split("\n")
        self.assertEqual(lines[0], "GLM CONDUCTOR QUOTA CLOCK TICK")
        self.assertTrue(lines[1].startswith(
            "Run: %s " % sys.executable))
        self.assertNotIn("Run: python3 ", payload["suggested_prompt"])

    def test_plan_interpreter_fallback_when_executable_empty(self):
        # F-4 兜底分支：sys.executable 为空 → 回退 "python3"
        with mock.patch("sys.executable", ""), \
                patched_quota_clock(FIVE_DETAIL):
            code, payload = run_cli("quota-clock-plan", self.repo)
        self.assertEqual(code, 0)
        self.assertTrue(payload["cli_command"].startswith("python3 "))
        self.assertIn("Run: python3 ", payload["suggested_prompt"])

    def test_plan_relative_repo_root_normalized(self):
        with patched_quota_clock(FIVE_DETAIL) as (resolver_mock, *_rest):
            code, _payload = run_cli("quota-clock-plan", "some/rel/path")
        self.assertEqual(code, 0)
        expected = str(Path(os.path.abspath("some/rel/path")).resolve())
        resolver_mock.assert_called_once_with(expected, force_refresh=True)

    def test_plan_usage_error_exit_2(self):
        code, payload = run_cli("quota-clock-plan")
        self.assertEqual(code, 2)
        self.assertIn("error", payload)
        code, _payload = run_cli("quota-clock-plan", self.repo, "extra")
        self.assertEqual(code, 2)


# —— 2. quota-clock-bind ——

class BindCliTest(ClockCliCase):
    """bind：全流程 + 各失败闸 + --db 透传 + 用法 2。"""

    def test_bind_full_flow(self):
        with patched_quota_clock(FIVE_DETAIL, db=DB,
                                 row=dict(ROW_FLASH)) as (_r, inspect_m,
                                                          retime_m,
                                                          discover_m):
            code, payload = run_cli("quota-clock-bind", self.repo, AID)
        self.assertEqual(code, 0)
        self.assertEqual(
            set(payload),
            {"status", "provider_identity_hash", "automation_id",
             "automation_model", "model_is_flash",
             "first_target_epoch_ms", "retimed", "state_path",
             "session_placement", "cost_advisory"})
        self.assertEqual(payload["status"], "bound")
        self.assertEqual(payload["provider_identity_hash"], HASH)
        self.assertEqual(payload["automation_id"], AID)
        self.assertEqual(payload["automation_model"], "GLM-5.3-Flash")
        self.assertTrue(payload["model_is_flash"])  # 含 "Flash" 判定
        self.assertEqual(payload["first_target_epoch_ms"], FIRST_TARGET)
        self.assertTrue(payload["retimed"])
        self.assertEqual(payload["state_path"], self.state_path())
        # adapter 以正确参数被调用：discover 缺省 → discover(None)
        discover_m.assert_called_once_with(None)
        inspect_m.assert_called_once_with(DB, AID)
        retime_m.assert_called_once_with(DB, AID, FIRST_TARGET)
        # state 落盘（全键干净态，runtime_path 即真实 runtime 目录）
        st = self.load_state()
        self.assertEqual(st["automation_id"], AID)
        self.assertEqual(st["automation_model"], "GLM-5.3-Flash")
        self.assertEqual(st["zcode_db_path"], DB)
        self.assertEqual(st["runtime_path"], RUNTIME_DIR)
        self.assertEqual(st["owner_repository"], self.repo_norm())
        self.assertEqual(st["status"], "bound")
        self.assertTrue(os.path.isfile(self.state_path()))

    def test_bind_non_flash_model_flags_but_not_blocks(self):
        # §3.3 已知边界：model 不含 "Flash" → model_is_flash=false 仅
        # 告警，绑定与 retime 照常（不阻断）
        row = dict(ROW_FLASH, model="glm-5.3")
        with patched_quota_clock(FIVE_DETAIL, db=DB, row=row):
            code, payload = run_cli("quota-clock-bind", self.repo, AID)
        self.assertEqual(code, 0)
        self.assertFalse(payload["model_is_flash"])
        self.assertTrue(payload["retimed"])
        self.assertEqual(self.load_state()["automation_model"], "glm-5.3")

    def test_bind_flash_emits_session_placement_without_cost_advisory(self):
        # v2.3.1：Flash 模型 → session_placement 语义齐备；
        # cost_advisory 固化为 null（键恒在、值为 None）
        with patched_quota_clock(FIVE_DETAIL, db=DB,
                                 row=dict(ROW_FLASH)):
            code, payload = run_cli("quota-clock-bind", self.repo, AID)
        self.assertEqual(code, 0)
        placement = payload["session_placement"]
        self.assertEqual(set(placement),
                         set(clock.SESSION_PLACEMENT_KEYS))
        self.assertIn("当前 ZCode 会话", placement["attachment"])
        self.assertIn("本对话", placement["tick_effect"])
        self.assertIn("Flash", placement["recommendation"])
        self.assertIn("主编排", placement["avoid"])
        self.assertIsNone(payload["cost_advisory"])

    def test_bind_non_flash_succeeds_with_cost_advisory(self):
        # 无任何模型阻断路径：非 Flash 也 status=="bound"（retime 照常
        # 执行、state 落盘），cost_advisory 仅提示 preferred 档位
        row = dict(ROW_FLASH, model="glm-5.3")
        with patched_quota_clock(FIVE_DETAIL, db=DB,
                                 row=row) as (_r, _i, retime_m, _d):
            code, payload = run_cli("quota-clock-bind", self.repo, AID)
        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "bound")
        self.assertFalse(payload["model_is_flash"])
        self.assertEqual(payload["cost_advisory"],
                         {"current_model": "glm-5.3",
                          "preferred": "low-cost Flash-class model"})
        retime_m.assert_called_once_with(DB, AID, FIRST_TARGET)
        self.assertEqual(self.load_state()["status"], "bound")

    def test_bind_missing_row_exit_1_without_state_or_retime(self):
        with patched_quota_clock(FIVE_DETAIL, db=DB) as (_r, _i, retime_m,
                                                         _d):
            code, payload = run_cli("quota-clock-bind", self.repo, AID)
        self.assertEqual(code, 1)
        self.assertEqual(payload["status"], "error")
        self.assertIn(AID, payload["error"])
        retime_m.assert_not_called()
        self.assertIsNone(self.load_state())

    def test_bind_non_recurring_row_exit_1(self):
        row = dict(ROW_FLASH, recurring=0)
        with patched_quota_clock(FIVE_DETAIL, db=DB,
                                 row=row) as (_r, _i, retime_m, _d):
            code, payload = run_cli("quota-clock-bind", self.repo, AID)
        self.assertEqual(code, 1)
        self.assertEqual(payload["status"], "error")
        self.assertIn("recurring", payload["error"])
        retime_m.assert_not_called()
        self.assertIsNone(self.load_state())

    def test_bind_retime_failure_notes_state_written(self):
        busy = zcode_schedule.ZcodeScheduleBusyError("database is locked")
        with patched_quota_clock(FIVE_DETAIL, db=DB,
                                 row=dict(ROW_FLASH),
                                 retime_error=busy):
            code, payload = run_cli("quota-clock-bind", self.repo, AID)
        self.assertEqual(code, 1)
        self.assertEqual(payload["status"], "error")
        self.assertIn("next_target", payload["error"])  # 输出注明未生效
        self.assertTrue(payload["state_written"])
        self.assertFalse(payload["next_target_applied"])
        self.assertEqual(payload["automation_id"], AID)
        # state 已写（bind 生效），可被后续 status / tick 观测
        st = self.load_state()
        self.assertEqual(st["status"], "bound")
        self.assertEqual(st["automation_id"], AID)

    def test_bind_not_plannable_exit_1(self):
        with patched_quota_clock(WEEKLY_ONLY_DETAIL) as (_r, _i, retime_m,
                                                         _d):
            code, payload = run_cli("quota-clock-bind", self.repo, AID)
        self.assertEqual(code, 1)
        self.assertEqual(payload["status"], "not_plannable")
        self.assertEqual(payload["reason"], "five_hour_missing")
        retime_m.assert_not_called()
        self.assertIsNone(self.load_state())

    def test_bind_explicit_db_passthrough(self):
        with patched_quota_clock(FIVE_DETAIL, db=OTHER_DB,
                                 row=dict(ROW_FLASH)) as (_r, inspect_m,
                                                          retime_m,
                                                          discover_m):
            code, _payload = run_cli("quota-clock-bind", self.repo, AID,
                                     "--db", OTHER_DB)
        self.assertEqual(code, 0)
        discover_m.assert_called_once_with(OTHER_DB)
        inspect_m.assert_called_once_with(OTHER_DB, AID)
        retime_m.assert_called_once_with(OTHER_DB, AID, FIRST_TARGET)
        self.assertEqual(self.load_state()["zcode_db_path"], OTHER_DB)

    def test_bind_usage_errors_exit_2(self):
        for args in ((self.repo,),
                     (self.repo, AID, "--db"),
                     (self.repo, AID, "--bogus", OTHER_DB),
                     (self.repo, AID, "--db", OTHER_DB, "extra")):
            with self.subTest(args=args):
                code, payload = run_cli("quota-clock-bind", *args)
                self.assertEqual(code, 2)
                self.assertIn("error", payload)


# —— 2b. quota-clock-bind 死绑定自愈（F-1，w35-dogfood-fix） ——

class BindDeadBindingSelfHealTest(ClockCliCase):
    """F-1 bind CLI 编排四分支：死绑定自愈成功 / 活绑定仍冲突拒绝 /
    死检 inspect 异常 fail-closed（存储层锁内漂移分支锚定于
    tests/test_quota_clock.py 的 BindDeadBindingSelfHealTest）。"""

    OLD_AID = "auto-clock-dead"

    def _preset_binding(self, automation_id):
        """预置一个指向任意 automation_id 的 bound state（写进注入的
        临时 home；bind_state 助手固定 AID，故此处直调存储层）。"""
        return clock_store.bind_clock_state(
            self.repo, HASH, automation_id,
            zcode_db_path=DB, runtime_path=RUNTIME_DIR,
            automation_model="GLM-5.3-Flash", now_ms=BIND_NOW_MS)

    def _bind_cli(self, *, first_inspect, second_inspect):
        """inspect_automation 恰两次调用（第一次=新 id，第二次=旧 id
        死检），分别注入返回值或异常实例；resolver / discover /
        retime 常规 mock。返回 (code, payload, retime_mock,
        inspect_mock)。"""
        with mock.patch(
                "runtime.quota.resolver.resolve_quota_detail",
                return_value=FIVE_DETAIL), \
            mock.patch(
                "runtime.host.zcode_schedule.discover_zcode_tasks_db",
                return_value=DB), \
            mock.patch(
                "runtime.host.zcode_schedule.inspect_automation",
                side_effect=[first_inspect, second_inspect]) as inspect_m, \
            mock.patch(
                "runtime.host.zcode_schedule.retime_automation") as retime_m:
            code, payload = run_cli("quota-clock-bind", self.repo, AID)
        return code, payload, retime_m, inspect_m

    def test_dead_binding_self_heal_success(self):
        # 分支 1：旧绑定宿主行已消失（死检 inspect → None）→ 自愈成功
        self._preset_binding(self.OLD_AID)
        code, payload, retime_m, inspect_m = self._bind_cli(
            first_inspect=dict(ROW_FLASH), second_inspect=None)
        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "bound")
        self.assertEqual(payload["automation_id"], AID)
        # 自愈路径专属键：replaced_dead_binding = 旧 automation_id
        self.assertEqual(payload["replaced_dead_binding"], self.OLD_AID)
        # state 指向新 id（干净 bound 态，观测键重置）
        st = self.load_state()
        self.assertEqual(st["automation_id"], AID)
        self.assertEqual(st["status"], "bound")
        self.assertIsNone(st["last_tick_at"])
        # retime 面向新 automation；inspect 顺序 = 新 id → 旧 id 死检
        retime_m.assert_called_once_with(DB, AID, FIRST_TARGET)
        self.assertEqual(inspect_m.call_args_list,
                         [mock.call(DB, AID),
                          mock.call(DB, self.OLD_AID)])

    def test_live_binding_still_conflict_rejected(self):
        # 分支 2：旧绑定宿主行存活（活绑定）→ 冲突拒绝原样维持，
        # 原 state 原样不动，无 replaced_dead_binding 键
        self._preset_binding(self.OLD_AID)
        code, payload, retime_m, _inspect_m = self._bind_cli(
            first_inspect=dict(ROW_FLASH),
            second_inspect=dict(ROW_FLASH, automation_id=self.OLD_AID))
        self.assertEqual(code, 1)
        self.assertEqual(payload["status"], "error")
        self.assertIn("ClockStateConflictError", payload["error"])
        self.assertNotIn("replaced_dead_binding", payload)
        retime_m.assert_not_called()
        st = self.load_state()
        self.assertEqual(st["automation_id"], self.OLD_AID)
        self.assertEqual(st["status"], "bound")

    def test_dead_check_inspect_error_fails_closed(self):
        # 分支 3：死检 inspect 异常 → fail-closed（冲突拒绝维持），
        # 原 state 原样不动
        self._preset_binding(self.OLD_AID)
        code, payload, retime_m, _inspect_m = self._bind_cli(
            first_inspect=dict(ROW_FLASH),
            second_inspect=zcode_schedule.ZcodeScheduleDbMissing(
                "db gone"))
        self.assertEqual(code, 1)
        self.assertEqual(payload["status"], "error")
        self.assertIn("ZcodeScheduleDbMissing", payload["error"])
        self.assertNotIn("replaced_dead_binding", payload)
        retime_m.assert_not_called()
        st = self.load_state()
        self.assertEqual(st["automation_id"], self.OLD_AID)
        self.assertEqual(st["status"], "bound")


# —— 3. quota-clock-tick ——

class TickCliTest(ClockCliCase):
    """tick：正常路径 / weekly_park / retime 失败 / state 缺失 / 自愈 /
    last_reset_at 传递 / 用法 2。"""

    def test_tick_success_updates_state_and_retimes(self):
        self.bind_state()
        with patched_quota_clock(FIVE_DETAIL) as (_r, _i, retime_m, _d):
            code, payload = run_cli("quota-clock-tick", self.repo)
        self.assertEqual(code, 0)
        self.assertEqual(
            set(payload),
            {"status", "provider_identity_hash", "reset_at",
             "next_target_at", "automation_id", "retimed", "reason",
             "decision"})
        self.assertEqual(payload["status"], "ticked")
        self.assertEqual(payload["provider_identity_hash"], HASH)
        self.assertEqual(payload["reset_at"], RESET)
        self.assertEqual(payload["next_target_at"], FIRST_TARGET)
        self.assertEqual(payload["automation_id"], AID)
        self.assertTrue(payload["retimed"])
        self.assertEqual(payload["decision"], "reset_target")
        self.assertEqual(payload["reason"], "reconfirm")
        # retime 收到决策目标（state 的 db / automation_id）
        retime_m.assert_called_once_with(DB, AID, FIRST_TARGET)
        st = self.load_state()
        self.assertEqual(st["next_target_at"], FIRST_TARGET)
        self.assertEqual(st["last_reset_at"], RESET_MS)
        self.assertEqual(st["status"], "bound")
        self.assertIsNotNone(st["last_tick_at"])
        self.assertGreater(st["last_tick_at"], BIND_NOW_MS)
        self.assertEqual(st["last_retime_at"], st["last_tick_at"])

    def test_tick_weekly_park_parks_status(self):
        self.bind_state()
        with patched_quota_clock(PARK_DETAIL) as (_r, _i, retime_m, _d):
            code, payload = run_cli("quota-clock-tick", self.repo)
        self.assertEqual(code, 0)
        self.assertEqual(payload["decision"], "weekly_park")
        self.assertEqual(payload["reason"], "weekly_blocked")
        self.assertEqual(payload["next_target_at"], WEEKLY_TARGET)
        self.assertEqual(self.load_state()["status"], "parked_weekly")
        retime_m.assert_called_once_with(DB, AID, WEEKLY_TARGET)

    def test_tick_retime_failure_keeps_state_target_and_exits_0(self):
        self.bind_state()
        # 预置「上一次成功 tick」观测面：目标 / reset / retime 时刻均有值
        clock_store.update_clock_state(HASH, lambda s: dict(
            s, next_target_at=OLD_TARGET, last_reset_at=RESET_MS,
            last_retime_at=1000, last_tick_at=1000))
        busy = zcode_schedule.ZcodeScheduleBusyError("database is locked")
        with patched_quota_clock(FIVE_DETAIL,
                                 retime_error=busy) as (_r, _i, retime_m,
                                                        _d):
            code, payload = run_cli("quota-clock-tick", self.repo)
        # retimed:false 仍算 tick 完成：退出码 0
        self.assertEqual(code, 0)
        self.assertFalse(payload["retimed"])
        self.assertEqual(payload["decision"], "reset_target")
        # §4.2：什么都不做（native watchdog 接管）——仅 last_tick_at 更新
        st = self.load_state()
        self.assertEqual(st["next_target_at"], OLD_TARGET)
        self.assertEqual(st["last_reset_at"], RESET_MS)
        self.assertEqual(st["last_retime_at"], 1000)
        self.assertEqual(st["status"], "bound")
        self.assertGreater(st["last_tick_at"], 1000)
        retime_m.assert_called_once()

    def test_tick_state_missing_exit_1_and_no_resolve(self):
        with patched_quota_clock(FIVE_DETAIL) as (resolver_m, _i, retime_m,
                                                  _d):
            code, payload = run_cli("quota-clock-tick", self.repo)
        self.assertEqual(code, 1)
        self.assertEqual(payload["status"], "error")
        self.assertEqual(payload["reason"], "state_missing")
        self.assertIn("error", payload)
        resolver_m.assert_not_called()  # state 缺失短路：零 provider 解析
        retime_m.assert_not_called()

    def test_tick_self_heals_stale_runtime_path(self):
        # §10.5 升级自愈：state 预置失真路径 → 成功 tick 回写实际值
        self.bind_state(runtime_path="X:/nonexistent/runtime")
        self.assertNotEqual(self.load_state()["runtime_path"], RUNTIME_DIR)
        with patched_quota_clock(FIVE_DETAIL):
            code, payload = run_cli("quota-clock-tick", self.repo)
        self.assertEqual(code, 0)
        self.assertTrue(payload["retimed"])
        self.assertEqual(self.load_state()["runtime_path"], RUNTIME_DIR)

    def test_tick_feeds_state_last_reset_to_decision(self):
        # reconfirm：state.last_reset_at 与本次 five_hour reset 同刻
        self.bind_state()
        clock_store.update_clock_state(
            HASH, lambda s: dict(s, last_reset_at=RESET_MS))
        with patched_quota_clock(FIVE_DETAIL):
            code, payload = run_cli("quota-clock-tick", self.repo)
        self.assertEqual((code, payload["reason"]), (0, "reconfirm"))
        self.assertEqual(self.load_state()["last_reset_at"], RESET_MS)
        # window_advanced：state.last_reset_at 为上一窗（不同刻），
        # 决策层据此报窗口推进，tick 后推进到本次观测值
        self.bind_state()
        clock_store.update_clock_state(
            HASH, lambda s: dict(s, last_reset_at=RESET_MS - 3600000))
        with patched_quota_clock(FIVE_DETAIL):
            code, payload = run_cli("quota-clock-tick", self.repo)
        self.assertEqual((code, payload["reason"]), (0, "window_advanced"))
        self.assertEqual(self.load_state()["last_reset_at"], RESET_MS)

    def test_tick_usage_error_exit_2(self):
        code, payload = run_cli("quota-clock-tick")
        self.assertEqual(code, 2)
        self.assertIn("error", payload)
        code, _payload = run_cli("quota-clock-tick", self.repo, "x")
        self.assertEqual(code, 2)


# —— 4. quota-clock-status ——

class StatusCliTest(ClockCliCase):
    """status：unbound 三键 + healthy 全绿 + 各 unhealthy 分支 + 用法 2。"""

    UNBOUND = {"bound": False, "health": "unbound",
               "suggestion": ("run quota-clock-plan then bind from an "
                              "interactive round")}

    def test_status_unbound_exit_0(self):
        code, payload = run_cli("quota-clock-status", self.repo)
        self.assertEqual(code, 0)
        self.assertEqual(payload, self.UNBOUND)
        self.assertEqual(set(payload), {"bound", "health", "suggestion"})

    def _bound_and_ticked(self):
        """bind（真实 state）+ 一次成功 CLI tick（mock resolver/retime）
        → state 具备 fresh last_tick 与 next_target=FIRST_TARGET。"""
        self.bind_state()
        with patched_quota_clock(FIVE_DETAIL):
            code, _payload = run_cli("quota-clock-tick", self.repo)
        self.assertEqual(code, 0)
        return dict(ROW_FLASH, next_run_at=FIRST_TARGET)

    def test_status_first_bind_awaiting_first_tick_healthy(self):
        # F-3（w35-dogfood-fix）：首绑未 tick → healthy +
        # awaiting_first_tick=true + tick_stale=false——修复「新装即
        # unhealthy 与 await next tick 同框」的矛盾呈现
        self.bind_state()
        with patched_quota_clock(row=dict(ROW_FLASH)):
            code, payload = run_cli("quota-clock-status", self.repo)
        self.assertEqual(code, 0)
        self.assertTrue(payload["bound"])
        self.assertIsNone(payload["last_tick_at"])
        self.assertTrue(payload["awaiting_first_tick"])
        self.assertFalse(payload["tick_stale"])
        self.assertEqual(payload["health"], "healthy")
        self.assertEqual(payload["reasons"], [])
        self.assertIsNone(payload["suggestion"])
        self.assertFalse(payload["needs_replacement"])
        self.assertNotIn("recovery_guidance", payload)

    def test_status_non_numeric_last_tick_is_awaiting_not_stale(self):
        # F-3：last_tick_at 非数值（垃圾值 / bool）= 无合法 tick 观测 →
        # awaiting first tick（不再视为陈旧），且 DB 行存活不触发 replace
        self.bind_state()
        clock_store.update_clock_state(
            HASH, lambda s: dict(s, last_tick_at="garbage"))
        with patched_quota_clock(row=dict(ROW_FLASH)):
            code, payload = run_cli("quota-clock-status", self.repo)
        self.assertEqual(code, 0)
        self.assertTrue(payload["awaiting_first_tick"])
        self.assertFalse(payload["tick_stale"])
        self.assertEqual(payload["health"], "healthy")

    def test_status_stale_numeric_tick_still_stale(self):
        # F-3 回归：合法数值 last_tick_at 超阈值 → 仍判 tick_stale
        # （awaiting_first_tick=false，陈旧判定只对合法观测生效）
        self._bound_and_ticked()
        now_ms = int(time.time() * 1000)
        clock_store.update_clock_state(
            HASH, lambda s: dict(s, last_tick_at=now_ms - 4 * 60 * 60000))
        with patched_quota_clock(row=dict(ROW_FLASH,
                                          next_run_at=FIRST_TARGET)):
            code, payload = run_cli("quota-clock-status", self.repo)
        self.assertEqual(code, 0)
        self.assertFalse(payload["awaiting_first_tick"])
        self.assertTrue(payload["tick_stale"])
        self.assertTrue(any(reason.startswith("tick_stale")
                            for reason in payload["reasons"]))

    def test_status_healthy_after_bind_and_tick(self):
        row = self._bound_and_ticked()
        with patched_quota_clock(row=row) as (resolver_m, _i, retime_m, _d):
            code, payload = run_cli("quota-clock-status", self.repo)
        self.assertEqual(code, 0)
        self.assertEqual(
            set(payload),
            {"bound", "provider_identity_hash", "automation_id",
             "automation_model", "status", "last_reset_at",
             "next_target_at", "db_next_run_at", "target_matches_db",
             "runtime_path", "runtime_path_exists", "last_tick_at",
             "tick_stale", "awaiting_first_tick", "recent_run_count",
             "health", "reasons", "suggestion", "placement", "host",
             "needs_replacement"})
        self.assertTrue(payload["bound"])
        self.assertEqual(payload["provider_identity_hash"], HASH)
        self.assertEqual(payload["automation_id"], AID)
        self.assertEqual(payload["automation_model"], "GLM-5.3-Flash")
        self.assertEqual(payload["status"], "bound")
        self.assertEqual(payload["last_reset_at"], RESET_MS)
        self.assertEqual(payload["next_target_at"], FIRST_TARGET)
        self.assertEqual(payload["db_next_run_at"], FIRST_TARGET)
        self.assertTrue(payload["target_matches_db"])
        self.assertEqual(payload["runtime_path"], RUNTIME_DIR)
        self.assertTrue(payload["runtime_path_exists"])
        self.assertIsNotNone(payload["last_tick_at"])
        self.assertFalse(payload["tick_stale"])
        self.assertEqual(payload["recent_run_count"], 2)
        self.assertEqual(payload["health"], "healthy")
        self.assertEqual(payload["reasons"], [])
        self.assertIsNone(payload["suggestion"])
        # 纯读：零 provider 解析、零 retime、零写
        resolver_m.assert_not_called()
        retime_m.assert_not_called()
        self.assertIsNotNone(self.load_state())

    def test_status_healthy_placement_host_and_no_replacement(self):
        # v2.3.1：健康态 placement 诊断（dedicated_session 恒常量串）
        # + host 标识 + needs_replacement=false（无 recovery_guidance）
        row = self._bound_and_ticked()
        with patched_quota_clock(row=row):
            code, payload = run_cli("quota-clock-status", self.repo)
        self.assertEqual(code, 0)
        self.assertEqual(
            payload["placement"],
            {"session_binding": "active", "preferred_model": True,
             "dedicated_session": "not_mechanically_verifiable"})
        self.assertEqual(payload["host"], {"adapter": "zcode_sqlite"})
        self.assertFalse(payload["needs_replacement"])
        self.assertNotIn("recovery_guidance", payload)

    def test_status_non_flash_model_preferred_model_false(self):
        # preferred_model 只是模型档位口径（含 "Flash"），绝非专用
        # 会话证据；dedicated_session 仍为常量串
        self.bind_state(automation_model="glm-5.3")
        with patched_quota_clock(row=dict(ROW_FLASH)):
            code, payload = run_cli("quota-clock-status", self.repo)
        self.assertEqual(code, 0)
        self.assertFalse(payload["placement"]["preferred_model"])
        self.assertEqual(payload["placement"]["session_binding"],
                         "active")
        self.assertEqual(payload["placement"]["dedicated_session"],
                         "not_mechanically_verifiable")

    def test_status_row_missing_needs_replacement_with_recovery(self):
        self.bind_state()
        with patched_quota_clock(row=None):
            code, payload = run_cli("quota-clock-status", self.repo)
        self.assertEqual(code, 0)
        self.assertTrue(payload["needs_replacement"])
        self.assertIn("recovery_guidance", payload)
        self.assertIn("replace", payload["recovery_guidance"])
        self.assertIn("quota-clock-bind <new_automation_id>",
                      payload["recovery_guidance"])
        # 恢复指引必含「未执行自动会话迁移」声明（无隐式迁移）
        self.assertIn("未执行自动会话迁移",
                      payload["recovery_guidance"])
        self.assertEqual(payload["placement"]["session_binding"],
                         "unavailable")

    def test_status_inspect_failed_needs_replacement_with_recovery(self):
        self.bind_state()
        gone = zcode_schedule.ZcodeScheduleDbMissing("db gone")
        with patched_quota_clock(inspect_error=gone):
            code, payload = run_cli("quota-clock-status", self.repo)
        self.assertEqual(code, 0)
        self.assertTrue(payload["needs_replacement"])
        self.assertIn("quota-clock-bind <new_automation_id>",
                      payload["recovery_guidance"])
        self.assertIn("未执行自动会话迁移",
                      payload["recovery_guidance"])
        self.assertEqual(payload["placement"]["session_binding"],
                         "unavailable")

    def test_status_advisory_reasons_do_not_trigger_replacement(self):
        # 锁定决策 c 两极：target_mismatch / tick_stale 仅 advisory——
        # needs_replacement 恒 false 且不附 recovery_guidance
        self._bound_and_ticked()
        now_ms = int(time.time() * 1000)
        clock_store.update_clock_state(
            HASH, lambda s: dict(s,
                                 last_tick_at=now_ms - 2 * 60 * 60000
                                 - 1))
        with patched_quota_clock(
                row=dict(ROW_FLASH, next_run_at=FIRST_TARGET + 1)):
            code, payload = run_cli("quota-clock-status", self.repo)
        self.assertEqual(code, 0)
        self.assertEqual(payload["health"], "unhealthy")
        self.assertTrue(any(r.startswith("target_mismatch")
                            for r in payload["reasons"]))
        self.assertTrue(any(r.startswith("tick_stale")
                            for r in payload["reasons"]))
        self.assertFalse(payload["needs_replacement"])
        self.assertNotIn("recovery_guidance", payload)
        self.assertEqual(payload["placement"]["session_binding"],
                         "active")

    def test_status_row_missing_unhealthy(self):
        self.bind_state()
        with patched_quota_clock(row=None):
            code, payload = run_cli("quota-clock-status", self.repo)
        self.assertEqual(code, 0)
        self.assertTrue(payload["bound"])
        self.assertEqual(payload["health"], "unhealthy")
        self.assertTrue(payload["reasons"][0].startswith("db_row_missing"))
        self.assertIsNone(payload["db_next_run_at"])
        self.assertFalse(payload["target_matches_db"])
        self.assertTrue(payload["suggestion"].startswith("replace:"))

    def test_status_target_mismatch_unhealthy(self):
        self._bound_and_ticked()
        with patched_quota_clock(
                row=dict(ROW_FLASH, next_run_at=FIRST_TARGET + 1)):
            code, payload = run_cli("quota-clock-status", self.repo)
        self.assertEqual(code, 0)
        self.assertEqual(payload["health"], "unhealthy")
        self.assertTrue(any(reason.startswith("target_mismatch")
                            for reason in payload["reasons"]))
        self.assertFalse(payload["target_matches_db"])
        self.assertEqual(payload["db_next_run_at"], FIRST_TARGET + 1)
        self.assertEqual(payload["suggestion"], "await next tick")

    def test_status_tick_stale_unhealthy(self):
        self._bound_and_ticked()
        now_ms = int(time.time() * 1000)
        horizon_ms = 2 * 60 * 60000  # 缺省 fallback_interval_minutes=60
        clock_store.update_clock_state(
            HASH, lambda s: dict(s, last_tick_at=now_ms - horizon_ms - 1))
        with patched_quota_clock(row=dict(ROW_FLASH,
                                          next_run_at=FIRST_TARGET)):
            code, payload = run_cli("quota-clock-status", self.repo)
        self.assertEqual(code, 0)
        self.assertEqual(payload["health"], "unhealthy")
        self.assertTrue(payload["tick_stale"])
        self.assertTrue(any(reason.startswith("tick_stale")
                            for reason in payload["reasons"]))
        self.assertEqual(payload["suggestion"], "await next tick")

    def test_status_fresh_tick_within_horizon_not_stale(self):
        """边界锚：now - last_tick_at 恰在 2×fallback 界内 → 不判陈旧。"""
        self._bound_and_ticked()
        now_ms = int(time.time() * 1000)
        clock_store.update_clock_state(
            HASH, lambda s: dict(s,
                                 last_tick_at=now_ms - 2 * 60 * 60000
                                 + 5000))
        with patched_quota_clock(row=dict(ROW_FLASH,
                                          next_run_at=FIRST_TARGET)):
            code, payload = run_cli("quota-clock-status", self.repo)
        self.assertEqual(code, 0)
        self.assertFalse(payload["tick_stale"])
        self.assertEqual(payload["health"], "healthy")

    def test_status_runtime_path_dangling_unhealthy(self):
        self.bind_state(runtime_path="X:/nonexistent/runtime")
        with patched_quota_clock(row=dict(ROW_FLASH)):
            code, payload = run_cli("quota-clock-status", self.repo)
        self.assertEqual(code, 0)
        self.assertEqual(payload["health"], "unhealthy")
        self.assertTrue(any(reason.startswith("runtime_path_missing")
                            for reason in payload["reasons"]))
        self.assertFalse(payload["runtime_path_exists"])
        self.assertEqual(payload["runtime_path"], "X:/nonexistent/runtime")
        self.assertEqual(payload["suggestion"], "await next tick")

    def test_status_inspect_failure_maps_to_replace(self):
        self.bind_state()
        gone = zcode_schedule.ZcodeScheduleDbMissing("db gone")
        with patched_quota_clock(inspect_error=gone):
            code, payload = run_cli("quota-clock-status", self.repo)
        self.assertEqual(code, 0)
        self.assertEqual(payload["health"], "unhealthy")
        self.assertTrue(payload["reasons"][0].startswith(
            "db_inspect_failed"))
        self.assertTrue(payload["suggestion"].startswith("replace:"))

    def test_status_usage_error_exit_2(self):
        code, payload = run_cli("quota-clock-status")
        self.assertEqual(code, 2)
        self.assertIn("error", payload)
        code, _payload = run_cli("quota-clock-status", self.repo, "x")
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
