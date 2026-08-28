#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""runtime.quota.report CLI 测试（v2 工作块 B5.5，§43 只读诊断面）。

【零真实网络纪律】本文件全部探测经 monkeypatch 模块级工厂
_build_providers 注入 fake provider（fetch 返回固定 snapshot 或抛
QuotaProviderError），不发任何真实网络请求、不读真实 ~/.zcode 配置
（resolve_credential 同步 monkeypatch / 环境注入隔离）；真实脚本仅以
子进程跑「unavailable」路径（无凭证、无网络访问）验证入口自举、
退出码与编码。

覆盖：
  - 三态 ×（文本 / JSON）双输出：unavailable（含配置指引两行）/
    成功（5-hour/used/status/plan + weekly 缺席 "not present"）/
    双 provider 全失败（失败摘要 + ok:false）；
  - 退出码恒 0（三态全验；诊断面不是闸门）；
  - 零凭证输出锚定：成功/失败/unavailable 全部输出不含假 key
    "sk-test-SECRET"，成功 JSON 无 Authorization/apiKey 类键；
  - 探测顺序契约：zai 在前，任一成功即用（后者不再探测），fetch
    恒以 force=True 调用（§34 诊断面强制刷新）；
  - format_duration_delta：未来 "1h 42m"/"3d 4h"/"45m"/"0m"、
    过去 → "due now"、None/垃圾 → "unknown"。

运行：
    cd <repo_root> && python3 -m unittest tests.test_quota_report -v
"""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "glm-conductor"))

import runtime.quota.report as report_mod
from runtime.quota.provider import QuotaProviderError

# 假 key（含 SECRET 标记：全部输出断言不含它）
FAKE_KEY = "sk-test-SECRET"

# 被测脚本（真实入口自举冒烟用）
REPORT_PATH = (Path(__file__).resolve().parents[1]
               / "plugins/glm-conductor/runtime/quota/report.py")

# 参考时刻（全部时间断言注入 now，零时钟耦合）
NOW = datetime(2026, 8, 28, 12, 0, 0, tzinfo=timezone.utc)


def iso_z(moment):
    """aware datetime → Z 形式 ISO8601（§27 reset_at 输出形式）。"""
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def make_snapshot(windows=None, provider="zai"):
    """构造 §27 形状 snapshot（five_hour 剩 8% → PRESSURE 演示态）。"""
    if windows is None:
        windows = [
            {"kind": "five_hour", "used_percent": 92.0,
             "remaining_percent": 8.0,
             "reset_at": iso_z(NOW + timedelta(hours=1, minutes=42))},
            {"kind": "weekly", "used_percent": 45.0,
             "remaining_percent": 55.0,
             "reset_at": iso_z(NOW + timedelta(days=3, hours=4))},
        ]
    return {"provider": provider, "plan_generation": "v3",
            "plan_level": "pro", "fetched_at": iso_z(NOW),
            "status": None, "windows": windows}


class FakeProvider(object):
    """fake provider：fetch 返回固定 snapshot 或抛 QuotaProviderError。"""

    def __init__(self, name, snapshot=None, error=None):
        self.name = name
        self.snapshot = snapshot
        self.error = error
        self.calls = []

    def fetch(self, force=False):
        self.calls.append(force)
        if self.error is not None:
            raise self.error
        return dict(self.snapshot)


class ReportHarness(unittest.TestCase):
    """模块导入方式跑 report.main（monkeypatch 注入，零网络零时钟）。"""

    def run_main(self, argv=(), *, resolver=None, providers=None):
        """跑 main(list(argv))，返回 (code, output, factory_keys)。

        resolver：替代 report_mod.resolve_credential（缺省 env 命中
        FAKE_KEY）；providers：[(name, provider)]，经 monkeypatch
        _build_providers 注入。stdout 捕获进 StringIO。
        """
        if resolver is None:
            resolver = lambda *args, **kwargs: ("env", FAKE_KEY)
        if providers is None:
            providers = []
        factory_keys = []

        def fake_factory(api_key):
            factory_keys.append(api_key)
            return providers

        buffer = io.StringIO()
        with contextlib.ExitStack() as stack:
            stack.enter_context(unittest.mock.patch.object(
                report_mod, "resolve_credential", resolver))
            stack.enter_context(unittest.mock.patch.object(
                report_mod, "_build_providers", fake_factory))
            stack.enter_context(contextlib.redirect_stdout(buffer))
            code = report_mod.main(list(argv))
        return code, buffer.getvalue(), factory_keys

    @staticmethod
    def success_providers(**kwargs):
        """双 provider 全成功的装置（zai 在前 bigmodel 在后）。"""
        zai = FakeProvider("zai", snapshot=make_snapshot(**kwargs))
        bigmodel = FakeProvider("bigmodel",
                                snapshot=make_snapshot(provider="bigmodel"))
        return [("zai", zai), ("bigmodel", bigmodel)], zai, bigmodel


class UnavailableTest(ReportHarness):
    """态 1：凭证解析 (None, None) → 配置指引，退出码 0。"""

    def setUp(self):
        self.resolver = lambda *args, **kwargs: (None, None)

    def test_text_contains_guidance(self):
        code, out, keys = self.run_main(resolver=self.resolver)
        self.assertEqual(code, 0)
        self.assertEqual(keys, [])  # unavailable 不构造 provider
        self.assertIn("unavailable", out)
        self.assertIn("GLM_CONDUCTOR_QUOTA_API_KEY", out)
        self.assertIn("~/.zcode/v2/config.json", out)
        self.assertNotIn("used:", out)
        self.assertNotIn(FAKE_KEY, out)

    def test_json_mode_unavailable(self):
        code, out, _ = self.run_main(["--json"], resolver=self.resolver)
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["mode"], "unavailable")
        self.assertFalse(payload["ok"])
        self.assertIsNone(payload["source"])
        self.assertNotIn(FAKE_KEY, out)


class SuccessTextTest(ReportHarness):
    """态 3 文本：窗口明细 + status + plan（§43 风格）。"""

    def test_full_text_shape(self):
        providers, zai, bigmodel = self.success_providers()
        code, out, keys = self.run_main(providers=providers)
        self.assertEqual(code, 0)
        self.assertEqual(keys, [FAKE_KEY])  # 解析到的 key 流入 provider 工厂
        self.assertIn("GLM CODING PLAN (provider: zai, source: env)", out)
        self.assertIn("5-hour:", out)
        self.assertIn("used:", out)
        self.assertIn("remaining:", out)
        self.assertIn("reset:", out)
        self.assertIn("weekly:", out)
        self.assertIn("status: PRESSURE", out)
        self.assertIn("plan: checkpoint", out)
        self.assertNotIn("not present", out)

    def test_available_plan_continue(self):
        windows = [
            {"kind": "five_hour", "used_percent": 82.0,
             "remaining_percent": 18.0,
             "reset_at": iso_z(NOW + timedelta(hours=1, minutes=42))},
            {"kind": "weekly", "used_percent": 45.0,
             "remaining_percent": 55.0,
             "reset_at": iso_z(NOW + timedelta(days=3, hours=4))},
        ]
        providers, _, _ = self.success_providers(windows=windows)
        code, out, _ = self.run_main(providers=providers)
        self.assertEqual(code, 0)
        self.assertIn("status: AVAILABLE", out)
        self.assertIn("plan: continue", out)

    def test_weekly_missing_not_present(self):
        windows = [{"kind": "five_hour", "used_percent": 92.0,
                    "remaining_percent": 8.0,
                    "reset_at": iso_z(NOW + timedelta(hours=1))}]
        providers, _, _ = self.success_providers(windows=windows)
        code, out, _ = self.run_main(providers=providers)
        self.assertEqual(code, 0)
        self.assertIn("weekly: not present", out)  # lite 套餐（§26）
        self.assertEqual(out.count("  used:"), 1)

    def test_probe_order_first_success_wins(self):
        providers, zai, bigmodel = self.success_providers()
        code, out, _ = self.run_main(providers=providers)
        self.assertEqual(code, 0)
        self.assertEqual(zai.calls, [True])    # fetch(force=True)（§34）
        self.assertEqual(bigmodel.calls, [])   # 首个成功，后者不探测
        self.assertIn("provider: zai", out)


class SuccessJsonTest(ReportHarness):
    """态 3 JSON：结构完整且全非秘密。"""

    def test_json_shape(self):
        providers, _, _ = self.success_providers()
        code, out, _ = self.run_main(["--json"], providers=providers)
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(set(payload),
                         {"mode", "source", "provider", "ok", "snapshot",
                          "evaluation", "plan"})
        self.assertEqual(payload["mode"], "provider-api")
        self.assertEqual(payload["source"], "env")
        self.assertEqual(payload["provider"], "zai")
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["snapshot"], make_snapshot())
        self.assertEqual(payload["evaluation"]["status"], "PRESSURE")
        self.assertEqual(payload["plan"]["action"], "checkpoint")
        # 零凭证锚定：无 Authorization/apiKey 类键，无假 key 值
        self.assertNotIn("Authorization", out)
        self.assertNotIn("apiKey", out)
        self.assertNotIn(FAKE_KEY, out)

    def test_fallback_to_second_provider(self):
        zai = FakeProvider("zai",
                           error=QuotaProviderError("quota 端点返回 HTTP 401",
                                                    "auth"))
        bigmodel = FakeProvider("bigmodel",
                                snapshot=make_snapshot(provider="bigmodel"))
        code, out, _ = self.run_main(
            ["--json"],
            providers=[("zai", zai), ("bigmodel", bigmodel)])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["provider"], "bigmodel")
        self.assertEqual(zai.calls, [True])
        self.assertEqual(bigmodel.calls, [True])


class AllProvidersFailTest(ReportHarness):
    """态 2：双 provider 全失败 → 失败摘要 / ok:false，退出码 0。"""

    def setUp(self):
        self.providers = [
            ("zai", FakeProvider("zai", error=QuotaProviderError(
                "quota 端点返回 HTTP 401", "auth"))),
            ("bigmodel", FakeProvider("bigmodel", error=QuotaProviderError(
                "quota 请求网络失败", "network"))),
        ]

    def test_text_failure_summary(self):
        code, out, _ = self.run_main(providers=self.providers)
        self.assertEqual(code, 0)
        self.assertEqual(out.count("zai: auth"), 1)      # 每 provider 一行
        self.assertEqual(out.count("bigmodel: network"), 1)
        self.assertNotIn("used:", out)
        self.assertNotIn(FAKE_KEY, out)                  # 零凭证输出
        self.assertNotIn("HTTP 401", out)                # 不透传原始消息

    def test_json_ok_false(self):
        code, out, _ = self.run_main(["--json"], providers=self.providers)
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["mode"], "provider-api")
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["providers"],
                         [{"name": "zai", "error_kind": "auth"},
                          {"name": "bigmodel", "error_kind": "network"}])
        self.assertNotIn(FAKE_KEY, out)


class ExitCodeTest(ReportHarness):
    """退出码恒 0：成功 / 全失败 / unavailable 三路全验。"""

    def test_three_states_all_exit_zero(self):
        success, _, _ = self.success_providers()
        failure = [
            ("zai", FakeProvider("zai", error=QuotaProviderError(
                "quota 端点返回 HTTP 500", "unavailable"))),
            ("bigmodel", FakeProvider("bigmodel", error=QuotaProviderError(
                "quota 请求网络失败", "network"))),
        ]
        for argv in ((), ["--json"]):
            self.assertEqual(self.run_main(argv)[0], 0)
            self.assertEqual(
                self.run_main(argv, providers=success)[0], 0)
            self.assertEqual(
                self.run_main(argv, providers=failure)[0], 0)
            self.assertEqual(
                self.run_main(argv, resolver=lambda *a, **k: (None, None))[0],
                0)


class FormatDurationDeltaTest(unittest.TestCase):
    """format_duration_delta：相对时长统一形式（now 注入，零时钟耦合）。"""

    def test_future_forms(self):
        self.assertEqual(
            report_mod.format_duration_delta(
                iso_z(NOW + timedelta(hours=1, minutes=42)), now=NOW),
            "1h 42m")
        self.assertEqual(
            report_mod.format_duration_delta(
                iso_z(NOW + timedelta(days=3, hours=4)), now=NOW),
            "3d 4h")
        self.assertEqual(
            report_mod.format_duration_delta(
                iso_z(NOW + timedelta(minutes=45)), now=NOW),
            "45m")
        self.assertEqual(
            report_mod.format_duration_delta(
                iso_z(NOW + timedelta(seconds=30)), now=NOW),
            "0m")

    def test_past_is_due_now(self):
        self.assertEqual(
            report_mod.format_duration_delta(
                iso_z(NOW - timedelta(hours=1)), now=NOW),
            "due now")
        self.assertEqual(
            report_mod.format_duration_delta(iso_z(NOW), now=NOW),
            "due now")

    def test_unknown_inputs(self):
        self.assertEqual(
            report_mod.format_duration_delta(None, now=NOW), "unknown")
        self.assertEqual(
            report_mod.format_duration_delta("not-a-time", now=NOW),
            "unknown")
        self.assertEqual(
            report_mod.format_duration_delta("", now=NOW), "unknown")
        self.assertEqual(
            report_mod.format_duration_delta(123, now=NOW), "unknown")

    def test_now_forms(self):
        # now 接受 ISO 串 / naive datetime（按 UTC 处理）/ 缺省时钟
        self.assertEqual(
            report_mod.format_duration_delta(iso_z(NOW + timedelta(hours=2)),
                                             now=iso_z(NOW)),
            "2h 0m")
        self.assertEqual(
            report_mod.format_duration_delta(iso_z(NOW + timedelta(hours=2)),
                                             now=datetime(2026, 8, 28, 12)),
            "2h 0m")
        moment = datetime.now(timezone.utc) + timedelta(hours=2)
        self.assertIn(report_mod.format_duration_delta(iso_z(moment)),
                      ("1h 59m", "2h 0m"))


class SubprocessSmokeTest(unittest.TestCase):
    """真实脚本入口冒烟（unavailable 路径：无凭证、零网络）。

    HOME / USERPROFILE 注入 tempdir（expanduser 回退落空），删除
    GLM_CONDUCTOR_QUOTA_API_KEY；PYTHONIOENCODING=utf-8 固定编码，
    字节捕获后按 UTF-8 解码（断言用 ASCII 子串，规避 locale 差异）。
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.env = dict(os.environ)
        self.env.pop("GLM_CONDUCTOR_QUOTA_API_KEY", None)
        self.env["HOME"] = self._tmp.name
        self.env["USERPROFILE"] = self._tmp.name
        self.env["PYTHONIOENCODING"] = "utf-8"

    def _run(self, *args):
        proc = subprocess.run([sys.executable, str(REPORT_PATH)] + list(args),
                              stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, env=self.env)
        return proc, proc.stdout.decode("utf-8")

    def test_text_entry_exit_zero(self):
        proc, out = self._run()
        self.assertEqual(proc.returncode, 0)
        self.assertIn("unavailable", out)
        self.assertIn("GLM_CONDUCTOR_QUOTA_API_KEY", out)

    def test_json_entry_exit_zero(self):
        proc, out = self._run("--json")
        self.assertEqual(proc.returncode, 0)
        payload = json.loads(out)
        self.assertEqual(payload["mode"], "unavailable")
        self.assertNotIn(FAKE_KEY, out)


if __name__ == "__main__":
    unittest.main()
