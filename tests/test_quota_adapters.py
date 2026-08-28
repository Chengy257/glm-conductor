#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""runtime.quota HTTP 适配器（zai / bigmodel）单元测试（v2 工作块 B5.2）。

运行：
    python3 -m unittest tests.test_quota_adapters -v

【零真实网络纪律】本文件全部测试通过注入 transport（可调用桩，
签名 transport(url, headers, timeout) -> (status, body)）驱动适配器，
不发任何真实网络请求；默认 urllib 传输除纯单元断言（_NoRedirectHandler
行为与 opener 组成）外，另以 127.0.0.1 本地回环 http.server 做端到端
锚定（§5.2 #3：限长读取真上限 / 真实 urllib 路径的重定向禁用），仍
不触 allowlist/https 逻辑、不访问任何外网。
覆盖：§37 八条安全条款逐条锚定 + 错误分类矩阵 + §34 缓存语义。
"""

import http.server
import json
import socket
import sys
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "glm-conductor"))
from runtime.quota import _http
from runtime.quota.bigmodel import BigModelQuotaProvider
from runtime.quota.provider import QuotaProvider, QuotaProviderError
from runtime.quota.zai import ZaiQuotaProvider

# —— 装置 ——

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "quota"

# 假 key（含 SECRET 标记：全部错误路径断言不泄漏）
FAKE_KEY = "sk-test-SECRET"

ZAI_URL = "https://api.z.ai/api/monitor/usage/quota/limit"
BIGMODEL_URL = "https://open.bigmodel.cn/api/monitor/usage/quota/limit"


def fixture_bytes(name):
    """读取 tests/fixtures/quota/<name> 的原始字节（作响应体）。"""
    with open(str(FIXTURE_DIR / name), "rb") as fh:
        return fh.read()


class StubTransport(object):
    """可计数传输桩：返回预设 (status, body) 或抛预设异常，并记录调用。"""

    def __init__(self, status=200, body=b"{}", exc=None):
        self.status = status
        self.body = body
        self.exc = exc          # 非 None 时 __call__ 抛出
        self.calls = []

    def __call__(self, url, headers, timeout):
        self.calls.append({
            "url": url,
            "headers": dict(headers),
            "timeout": timeout,
        })
        if self.exc is not None:
            raise self.exc
        return self.status, self.body

    @property
    def count(self):
        return len(self.calls)


def http_error(code):
    """构造离线 HTTPError（模拟默认传输层遇非 2xx / 被禁重定向）。"""
    return urllib.error.HTTPError(
        "https://api.z.ai/api/monitor/usage/quota/limit", code,
        "error", None, None)


def snapshot_common_asserts(test, snapshot, provider, plan_level,
                            window_kinds):
    """成功 snapshot 共有断言：§27 顶层形状 / status 恒 None / windows。"""
    test.assertIsInstance(snapshot, dict)
    test.assertEqual(
        set(snapshot.keys()),
        {"provider", "plan_generation", "plan_level", "fetched_at",
         "status", "windows"})
    test.assertEqual(snapshot["status"], None)
    test.assertEqual(snapshot["provider"], provider)
    test.assertEqual(snapshot["plan_level"], plan_level)
    test.assertEqual([w["kind"] for w in snapshot["windows"]], window_kinds)
    for window in snapshot["windows"]:
        test.assertEqual(
            set(window.keys()),
            {"kind", "used_percent", "remaining_percent", "reset_at"})
    # fetched_at 是 UTC ISO 毫秒精度（适配器生成，非 fixture 透传）
    test.assertRegex(snapshot["fetched_at"],
                     r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")


# —— 正常流（两个 provider 各一） ——

class TestHappyPath(unittest.TestCase):

    def test_zai_happy_path(self):
        """URL 精确 / 鉴权头无 Bearer / snapshot 经 parser（单窗 lite）。"""
        transport = StubTransport(status=200,
                                  body=fixture_bytes("five_hour_only.json"))
        provider = ZaiQuotaProvider(FAKE_KEY, timeout=1.5, transport=transport)
        self.assertIsInstance(provider, QuotaProvider)
        snapshot = provider.fetch()
        self.assertEqual(transport.count, 1)
        call = transport.calls[0]
        # URL 精确断言：https + 正确 host + 正确 path
        self.assertEqual(call["url"], ZAI_URL)
        self.assertTrue(call["url"].startswith("https://"))
        # 鉴权头：Authorization 原样（无 Bearer 前缀）+ Accept + UA
        self.assertEqual(set(call["headers"]),
                         {"Authorization", "Accept", "User-Agent"})
        self.assertEqual(call["headers"]["Authorization"], FAKE_KEY)
        self.assertFalse(
            call["headers"]["Authorization"].startswith("Bearer"))
        self.assertEqual(call["headers"]["Accept"], "application/json")
        self.assertEqual(call["headers"]["User-Agent"], "glm-conductor/2.0")
        self.assertEqual(call["timeout"], 1.5)
        # snapshot：fixture five_hour_only → 单窗 lite
        snapshot_common_asserts(
            self, snapshot, "zai", "lite", ["five_hour"])
        five = snapshot["windows"][0]
        self.assertEqual(five["used_percent"], 12.5)
        self.assertEqual(five["remaining_percent"], 87.5)
        self.assertEqual(five["reset_at"], "2026-08-28T06:45:02Z")

    def test_bigmodel_happy_path(self):
        """bigmodel 同构：URL 指向 open.bigmodel.cn，provider=bigmodel。"""
        transport = StubTransport(status=200,
                                  body=fixture_bytes("five_hour_only.json"))
        provider = BigModelQuotaProvider(FAKE_KEY, transport=transport)
        snapshot = provider.fetch()
        self.assertEqual(transport.count, 1)
        self.assertEqual(transport.calls[0]["url"], BIGMODEL_URL)
        self.assertEqual(
            transport.calls[0]["headers"]["Authorization"], FAKE_KEY)
        snapshot_common_asserts(
            self, snapshot, "bigmodel", "lite", ["five_hour"])


# —— 错误分类矩阵 ——

class TestErrorClassification(unittest.TestCase):

    def _fetch_raises(self, transport, factory=ZaiQuotaProvider):
        provider = factory(FAKE_KEY, transport=transport)
        with self.assertRaises(QuotaProviderError) as ctx:
            provider.fetch()
        return ctx.exception

    def test_401_403_auth(self):
        """状态 401/403 → auth。"""
        for status in (401, 403):
            with self.subTest(status=status):
                exc = self._fetch_raises(StubTransport(
                    status=status, body=fixture_bytes("auth_failure.json")))
                self.assertEqual(exc.kind, "auth")

    def test_other_4xx_5xx_unavailable(self):
        """其余非 2xx（500/503/404）→ unavailable。"""
        for status in (500, 503, 404):
            with self.subTest(status=status):
                exc = self._fetch_raises(StubTransport(
                    status=status,
                    body=fixture_bytes("endpoint_unavailable.json")))
                self.assertEqual(exc.kind, "unavailable")

    def test_302_status_network(self):
        """注入 transport 返回 302 → network（重定向被禁）。"""
        exc = self._fetch_raises(
            StubTransport(status=302, body=b"moved"))
        self.assertEqual(exc.kind, "network")

    def test_transport_exceptions_network(self):
        """URLError / socket.timeout / OSError → network。"""
        for label, exc in (
                ("URLError", urllib.error.URLError("conn refused")),
                ("timeout", socket.timeout("timed out")),
                ("OSError", OSError("boom"))):
            with self.subTest(exc=label):
                result = self._fetch_raises(StubTransport(exc=exc))
                self.assertEqual(result.kind, "network")

    def test_httperror_classification(self):
        """默认传输路径的 HTTPError：401/403→auth、302→network、
        500→unavailable。"""
        for code, kind in ((401, "auth"), (403, "auth"),
                           (302, "network"), (500, "unavailable")):
            with self.subTest(code=code):
                result = self._fetch_raises(
                    StubTransport(exc=http_error(code)))
                self.assertEqual(result.kind, kind)

    def test_non_json_body_malformed(self):
        """200 但 body 非 JSON → malformed。"""
        exc = self._fetch_raises(StubTransport(
            status=200, body=b"<html>gateway error</html>"))
        self.assertEqual(exc.kind, "malformed")

    def test_malformed_structure_fixture(self):
        """200 + 合法 JSON 但结构不完整（fixture malformed.json）→
        malformed（解析层分类透传）。"""
        exc = self._fetch_raises(StubTransport(
            status=200, body=fixture_bytes("malformed.json")))
        self.assertEqual(exc.kind, "malformed")

    def test_oversized_body_malformed(self):
        """超长 body（max_bytes+1）→ malformed；消息注明 bounded
        response exceeded 且不落任何响应内容。"""
        body = b"K" * 65537
        exc = self._fetch_raises(StubTransport(status=200, body=body))
        self.assertEqual(exc.kind, "malformed")
        self.assertIn("bounded response exceeded", exc.message)
        self.assertNotIn("KKKKK", str(exc))  # 响应内容绝不进错误消息

    def test_small_custom_max_bytes(self):
        """max_bytes 调小后，超过新上限即 malformed（限长随实例）。"""
        exc = self._fetch_raises(
            StubTransport(status=200, body=b"x" * 33),
            factory=lambda key, transport: ZaiQuotaProvider(
                key, max_bytes=32, transport=transport))
        self.assertEqual(exc.kind, "malformed")


# —— 安全断言组（§37 逐条锚定） ——

class TestSecurity(unittest.TestCase):

    _ERROR_SCENARIOS = (
        ("401", lambda: StubTransport(
            status=401, body=fixture_bytes("auth_failure.json"))),
        ("403", lambda: StubTransport(status=403, body=b"{}")),
        ("500", lambda: StubTransport(
            status=500, body=fixture_bytes("endpoint_unavailable.json"))),
        ("302-status", lambda: StubTransport(status=302, body=b"")),
        ("URLError", lambda: StubTransport(
            exc=urllib.error.URLError("dns failure"))),
        ("timeout", lambda: StubTransport(exc=socket.timeout("timed out"))),
        ("HTTPError-401", lambda: StubTransport(exc=http_error(401))),
        ("HTTPError-302", lambda: StubTransport(exc=http_error(302))),
        ("non-json", lambda: StubTransport(
            status=200, body=b"<html>oops</html>")),
        ("oversized", lambda: StubTransport(
            status=200, body=b"S" * 70000)),
    )

    def test_no_key_leak_in_all_error_paths(self):
        """注入假 key "sk-test-SECRET"：全部错误路径 str(exc) 不含
        SECRET（§37：错误消息模板不拼 key）。"""
        for label, make in self._ERROR_SCENARIOS:
            with self.subTest(scenario=label):
                provider = ZaiQuotaProvider(FAKE_KEY, transport=make())
                with self.assertRaises(QuotaProviderError) as ctx:
                    provider.fetch()
                text = str(ctx.exception)
                self.assertNotIn("SECRET", text)
                self.assertNotIn(FAKE_KEY, text)

    def test_host_not_in_allowlist_network_and_no_request(self):
        """host 不在 allowlist → 构造期 network 错误，且绝不发起
        请求（§37 条款 2/5；严格精确匹配，不接受变体）。"""
        for host in ("evil.example.com", "api.z.ai.evil.com",
                     "API.Z.AI", "open.bigmodel.cn:8443", ""):
            transport = StubTransport()
            with self.subTest(host=host):
                with self.assertRaises(QuotaProviderError) as ctx:
                    _http.HttpQuotaProvider(
                        FAKE_KEY, name="evil", host=host,
                        transport=transport)
                self.assertEqual(ctx.exception.kind, "network")
                self.assertNotIn("SECRET", str(ctx.exception))
                self.assertEqual(transport.count, 0)

    def test_api_key_validation(self):
        """api_key 空串 / 非 str → ValueError；错误消息不回显值。"""
        for bad in ("", None, 123, b"byte-key", ["key"]):
            with self.subTest(api_key=bad):
                with self.assertRaises(ValueError):
                    ZaiQuotaProvider(bad)
        with self.assertRaises(ValueError) as ctx:
            ZaiQuotaProvider(b"byte-key")
        self.assertNotIn("byte-key", str(ctx.exception))

    def test_snapshot_has_no_auth_or_raw_fields(self):
        """成功 snapshot 不含 Authorization / key / 任何原始响应字段
        （§37 条款 7 与 §27 形状；缓存同样干净）。"""
        transport = StubTransport(
            status=200, body=fixture_bytes("unknown_additive_fields.json"))
        provider = BigModelQuotaProvider(FAKE_KEY, transport=transport)
        snapshot = provider.fetch()
        for label, obj in (("returned", snapshot),
                           ("cached", provider._cache)):
            serialized = json.dumps(obj, ensure_ascii=False)
            with self.subTest(which=label):
                self.assertNotIn("SECRET", serialized)
                self.assertNotIn("Authorization", serialized)
                self.assertNotIn("usageDetails", serialized)
                self.assertNotIn("nextResetTime", serialized)
                self.assertNotIn("currentValue", serialized)

    def test_default_transport_disables_redirects(self):
        """默认传输禁用重定向（§37 条款 6，纯单元断言，零网络）：
        _NoRedirectHandler.redirect_request 恒返回 None（urllib 约定
        返回 None → 抛 HTTPError，不跟随任何 3xx），且默认 opener
        确实挂载该 handler。"""
        handler = _http._NoRedirectHandler()
        request = urllib.request.Request(ZAI_URL)
        self.assertIsNone(handler.redirect_request(
            request, None, 302, "Found", None,
            "https://evil.example.com/hook"))
        self.assertIsNone(handler.redirect_request(
            request, None, 301, "Moved Permanently", None,
            "http://api.z.ai/downgrade-to-http"))
        opener = _http.build_default_opener()
        self.assertTrue(
            any(isinstance(h, _http._NoRedirectHandler)
                for h in opener.handlers))

    def test_default_transport_reads_bounded(self):
        """默认传输只读 max_bytes+1 字节（§37 条款 4）：构造出的
        transport 是 make_default_transport(max_bytes) 的产物。"""
        provider = ZaiQuotaProvider(FAKE_KEY)  # 未注入 → 默认传输
        self.assertEqual(provider.max_bytes, _http.DEFAULT_MAX_BYTES)
        # 默认传输闭包绑定实例 max_bytes：不同实例互不影响
        small = ZaiQuotaProvider(FAKE_KEY, max_bytes=32)
        self.assertNotEqual(provider._transport, small._transport)


# —— 缓存语义（§34） ——

class TestCacheSemantics(unittest.TestCase):

    def _zai(self, transport, **kwargs):
        return ZaiQuotaProvider(FAKE_KEY, transport=transport, **kwargs)

    def test_ttl_cache_hit_skips_transport(self):
        """TTL 内第二次 fetch 不调 transport，snapshot（含 fetched_at）
        原样返回。"""
        transport = StubTransport(
            status=200, body=fixture_bytes("five_hour_only.json"))
        provider = self._zai(transport)
        first = provider.fetch()
        second = provider.fetch()
        self.assertEqual(transport.count, 1)
        self.assertEqual(first, second)
        self.assertIsNot(first, second)  # 返回的是浅拷贝，非同一对象

    def test_force_bypasses_cache(self):
        """force=True 绕过缓存重调 transport。"""
        transport = StubTransport(
            status=200, body=fixture_bytes("five_hour_only.json"))
        provider = self._zai(transport)
        provider.fetch()
        provider.fetch(force=True)
        self.assertEqual(transport.count, 2)

    def test_ttl_zero_always_refetches(self):
        """ttl_seconds=0：缓存立即过期，每次 fetch 都真实请求。"""
        transport = StubTransport(
            status=200, body=fixture_bytes("five_hour_only.json"))
        provider = self._zai(transport, ttl_seconds=0)
        provider.fetch()
        provider.fetch()
        self.assertEqual(transport.count, 2)

    def test_failure_does_not_evict_or_write_cache(self):
        """成功缓存后 force 刷新失败：缓存不被清除也不被失败污染，
        恢复后 TTL 内仍命中旧 snapshot。"""
        transport = StubTransport(
            status=200, body=fixture_bytes("five_hour_only.json"))
        provider = self._zai(transport)
        first = provider.fetch()
        transport.exc = urllib.error.URLError("endpoint down")
        with self.assertRaises(QuotaProviderError):
            provider.fetch(force=True)   # force 本就发起真实请求（第 2 次）
        transport.exc = None
        second = provider.fetch()  # 失败期间缓存原样 → 仍命中，不再发请求
        self.assertEqual(transport.count, 2)
        self.assertEqual(second, first)

    def test_first_failure_then_success_refetches(self):
        """首次失败不写缓存：恢复后下一次 fetch 是真实请求。"""
        transport = StubTransport(exc=urllib.error.URLError("down"))
        provider = self._zai(transport)
        with self.assertRaises(QuotaProviderError):
            provider.fetch()
        self.assertEqual(provider._cache, None)  # 失败未写缓存
        transport.exc = None
        transport.body = fixture_bytes("five_hour_only.json")
        snapshot = provider.fetch()
        self.assertEqual(transport.count, 2)
        self.assertEqual(snapshot["provider"], "zai")

    def test_fetch_returns_shallow_copy(self):
        """修改返回 snapshot 的顶层字段不污染下次缓存返回值。"""
        transport = StubTransport(
            status=200, body=fixture_bytes("five_hour_only.json"))
        provider = self._zai(transport)
        first = provider.fetch()
        first["status"] = "TAMPERED"
        first["provider"] = "TAMPERED"
        second = provider.fetch()
        self.assertEqual(transport.count, 1)  # 仍命中缓存
        self.assertEqual(second["status"], None)
        self.assertEqual(second["provider"], "zai")

    def test_instances_do_not_share_cache(self):
        """缓存是实例态：两个 provider 各自真实请求。"""
        transport = StubTransport(
            status=200, body=fixture_bytes("five_hour_only.json"))
        one = self._zai(transport)
        two = self._zai(transport)
        one.fetch()
        two.fetch()
        self.assertEqual(transport.count, 2)


# —— 构造参数校验 ——

class TestConstructorValidation(unittest.TestCase):

    def test_invalid_parameters_raise_value_error(self):
        """timeout / max_bytes / ttl_seconds / transport 非法 →
        ValueError（构造期暴露，不产生半成品 provider）。"""
        transport = StubTransport()
        cases = (
            ("timeout", dict(timeout=0)),
            ("timeout", dict(timeout=-1.5)),
            ("timeout", dict(timeout="5")),
            ("max_bytes", dict(max_bytes=0)),
            ("max_bytes", dict(max_bytes=-1)),
            ("max_bytes", dict(max_bytes=1.5)),
            ("ttl_seconds", dict(ttl_seconds=-1)),
            ("ttl_seconds", dict(ttl_seconds="120")),
            ("transport", dict(transport="not-callable")),
        )
        for label, bad_kwargs in cases:
            kwargs = dict(bad_kwargs)
            kwargs.setdefault("transport", transport)
            with self.subTest(param=label, value=bad_kwargs[label]):
                with self.assertRaises(ValueError):
                    ZaiQuotaProvider(FAKE_KEY, **kwargs)
                with self.assertRaises(ValueError):
                    BigModelQuotaProvider(FAKE_KEY, **kwargs)
        self.assertEqual(transport.count, 0)  # 校验路径绝不发请求


# —— 默认传输离线端到端（§5.2 #3：本地回环 http，仅测传输闭包） ——

class _BigBodyHandler(http.server.BaseHTTPRequestHandler):
    """恒返回 100KiB body 的回环 handler（限长读取上限锚定用）。"""

    def do_GET(self):
        body = b"B" * (100 * 1024)
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except ConnectionError:
            pass  # 客户端限长读取后提前关闭连接（测试设计如此）

    def log_message(self, format, *args):
        pass  # 静默访问日志（测试输出纪律）


class _RedirectHandler(http.server.BaseHTTPRequestHandler):
    """恒返回 302 的回环 handler（真实 urllib 重定向禁用锚定用）。"""

    hits = 0  # 类级请求计数：锚定 302 后绝不发起第二次请求

    def do_GET(self):
        _RedirectHandler.hits += 1
        self.send_response(302)
        self.send_header("Location", "http://127.0.0.1:9/moved")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, format, *args):
        pass


class _SmallBodyHandler(http.server.BaseHTTPRequestHandler):
    """返回 200 小 JSON body 的回环 handler（正常回读锚定用）。"""

    def do_GET(self):
        body = b'{"ok": true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


class DefaultTransportEndToEndTest(unittest.TestCase):
    """make_default_transport(max_bytes) 闭包的本地回环端到端测试。

    本地回环 http（127.0.0.1 随机端口 + http.server + threading），
    仅测传输闭包本身（限长读取真上限 / 重定向禁用的真实 urllib 路径 /
    200 正常回读），不触 allowlist/https 逻辑——https 由 provider 构造
    期模板保证（§37 条款 1，transport 本身不限 URL scheme）。
    服务线程以 try/finally 关闭（shutdown + server_close + join）。
    """

    def _start(self, handler_cls):
        """在 127.0.0.1 随机端口起回环服务，返回 (server, thread, base_url)。"""
        server = http.server.HTTPServer(("127.0.0.1", 0), handler_cls)
        thread = threading.Thread(target=server.serve_forever)
        thread.daemon = True
        thread.start()
        url = "http://127.0.0.1:%d/" % server.server_address[1]
        return server, thread, url

    def _stop(self, server, thread):
        """关闭服务与线程（try/finally 调用，保证无泄漏）。"""
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    def test_bounded_read_returns_exactly_max_bytes_plus_one(self):
        """100KiB body + max_bytes=1024 → transport 恰返回 1025 字节
        （限长读取的真上限锚定：read(max_bytes+1)）。"""
        server, thread, url = self._start(_BigBodyHandler)
        try:
            transport = _http.make_default_transport(1024)
            status, body = transport(url, {}, 5.0)
            self.assertEqual(status, 200)
            self.assertIsInstance(body, bytes)
            self.assertEqual(len(body), 1025)
        finally:
            self._stop(server, thread)

    def test_redirect_raises_httperror_without_following(self):
        """服务端 302 → transport 抛 urllib.error.HTTPError（真实 urllib
        路径的重定向禁用锚定），且全程只发一次请求（不跟随、无第二次）。"""
        _RedirectHandler.hits = 0
        server, thread, url = self._start(_RedirectHandler)
        try:
            transport = _http.make_default_transport(65536)
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                transport(url, {}, 5.0)
            self.assertEqual(ctx.exception.code, 302)
        finally:
            self._stop(server, thread)
        self.assertEqual(_RedirectHandler.hits, 1)

    def test_small_body_roundtrip(self):
        """服务端 200 小 body → transport 原样返回 (200, body)。"""
        server, thread, url = self._start(_SmallBodyHandler)
        try:
            transport = _http.make_default_transport(65536)
            status, body = transport(url, {}, 5.0)
            self.assertEqual(status, 200)
            self.assertEqual(body, b'{"ok": true}')
        finally:
            self._stop(server, thread)


if __name__ == "__main__":
    unittest.main()
