#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""runtime.quota.provider 单元测试（v2 工作块 B5.1）。

运行：
    python3 -m unittest tests.test_quota_provider -v

全部离线：错误类型 kind 白名单 / str 纪律（不含凭证）/ 基类契约 /
ALLOWED_HOSTS 常量断言，不发任何网络请求。
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "glm-conductor"))
from runtime.quota.provider import (
    ALLOWED_HOSTS,
    ERROR_KINDS,
    QuotaProvider,
    QuotaProviderError,
)


class TestAllowedHosts(unittest.TestCase):

    def test_allowed_hosts_exact_content(self):
        """§37 初始 allowlist：恰好两个 host，顺序稳定。"""
        self.assertEqual(ALLOWED_HOSTS, ("api.z.ai", "open.bigmodel.cn"))
        self.assertEqual(len(ALLOWED_HOSTS), 2)

    def test_allowed_hosts_is_tuple(self):
        self.assertIsInstance(ALLOWED_HOSTS, tuple)


class TestErrorKinds(unittest.TestCase):

    def test_error_kinds_vocabulary(self):
        """kind 词汇表：五类，供上层 fail-open 决策（§28）。"""
        self.assertEqual(
            ERROR_KINDS,
            ("auth", "unavailable", "malformed", "network", "unknown"))


class TestQuotaProviderError(unittest.TestCase):

    def test_valid_kind_constructs_with_attributes(self):
        exc = QuotaProviderError("quota 查询失败", "auth")
        self.assertIsInstance(exc, Exception)
        self.assertEqual(exc.kind, "auth")
        self.assertEqual(exc.message, "quota 查询失败")
        self.assertEqual(exc.args, ("quota 查询失败",))

    def test_all_kinds_accepted(self):
        for kind in ERROR_KINDS:
            with self.subTest(kind=kind):
                self.assertEqual(
                    QuotaProviderError("m", kind).kind, kind)

    def test_invalid_kind_raises_value_error(self):
        """kind 不在白名单 → ValueError（先校验再构造）。"""
        for bad_kind in ("bogus", "", "AUTH", None, 42, ("auth",)):
            with self.subTest(kind=bad_kind):
                with self.assertRaises(ValueError):
                    QuotaProviderError("m", bad_kind)

    def test_str_contains_message_and_kind(self):
        exc = QuotaProviderError("quota 查询失败", "auth")
        text = str(exc)
        self.assertIn("quota 查询失败", text)
        self.assertIn("auth", text)
        self.assertEqual(text, "quota 查询失败（kind=auth）")

    def test_str_never_contains_credentials(self):
        """§37 代码级锚点：str 只含 message+kind，绝不含凭证。

        假想凭证从未进入 message——断言异常文本不会自行携带任何
        凭证形态的内容（凭证零落盘纪律：本类自身不附加、调用方
        也不得把凭证拼进 message）。
        """
        fake_key = "sk-FakeApiKey-ABCDEF0123456789"
        fake_header = "Authorization: " + fake_key
        exc = QuotaProviderError("quota 查询失败（网络超时）", "network")
        text = str(exc)
        self.assertNotIn(fake_key, text)
        self.assertNotIn(fake_header, text)
        self.assertNotIn("Authorization", text)
        self.assertEqual(exc.args, ("quota 查询失败（网络超时）",))

    def test_str_format_stable_across_kinds(self):
        for kind in ERROR_KINDS:
            with self.subTest(kind=kind):
                exc = QuotaProviderError("端点不可用", kind)
                self.assertEqual(str(exc), "端点不可用（kind=%s）" % kind)


class TestQuotaProviderBase(unittest.TestCase):

    def test_base_attributes(self):
        provider = QuotaProvider("zai", "api.z.ai")
        self.assertEqual(provider.name, "zai")
        self.assertEqual(provider.host, "api.z.ai")
        self.assertIn(provider.host, ALLOWED_HOSTS)

    def test_constructor_validates_arguments(self):
        for bad_name in ("", None, 42):
            with self.subTest(name=bad_name):
                with self.assertRaises(ValueError):
                    QuotaProvider(bad_name, "api.z.ai")
        for bad_host in ("", None, 42):
            with self.subTest(host=bad_host):
                with self.assertRaises(ValueError):
                    QuotaProvider("zai", bad_host)

    def test_fetch_not_implemented(self):
        """基类 fetch 是抽象契约：适配器（B5.2）必须实现。"""
        provider = QuotaProvider("zai", "api.z.ai")
        with self.assertRaises(NotImplementedError):
            provider.fetch()
        with self.assertRaises(NotImplementedError):
            provider.fetch(force=True)


if __name__ == "__main__":
    unittest.main()
