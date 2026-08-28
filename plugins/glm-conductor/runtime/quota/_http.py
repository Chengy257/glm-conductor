#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GLM Conductor v2 quota HTTP 适配器共享实现（v2 工作块 B5.2）。

职责：
    Z.ai 与 BigModel 两个 quota 监控适配器（zai.py / bigmodel.py）的
    共享 HTTP 传输层与 fetch 流程（§25/§34/§37/§38）；两个适配器
    模块只是薄封装，仅绑定 provider 标识，host 经本模块注册表从
    provider.ALLOWED_HOSTS 解析（host 字符串只在本文出现并逐项
    断言在 allowlist 内——§25「不自行散布 host」）。

安全条款（§37 逐条声明；测试锚定见 tests/test_quota_adapters.py）：
    1. HTTPS only：URL 恒为 https://<host>/api/monitor/usage/quota/limit，
       构造时断言 scheme 为 https，否则 QuotaProviderError(network)；
    2. 严格 host allowlist：host 必须 ∈ provider.ALLOWED_HOSTS（初始
       仅 api.z.ai / open.bigmodel.cn），不在清单内 → network，且
       绝不发起请求；
    3. 短超时：默认 timeout=5.0 秒，透传到传输层，不得无限等待；
    4. 限长响应：默认传输只读前 max_bytes+1 字节（默认 65536），
       超限 → QuotaProviderError(malformed)，消息注明 bounded
       response exceeded，且不落任何响应内容；
    5. 不向任意用户提供的 host 转发凭证：Authorization 头只随请求
       发往条款 2 校验过的 host（allowlist 校验在构造期完成，任何
       请求路径都不存在清单外 host）；
    6. 重定向禁用：默认 opener 挂 _NoRedirectHandler
       （redirect_request 恒返回 None → urllib 抛 HTTPError，任何
       3xx 都不跟随、不发起第二次请求）；3xx 一律归 network。注入
       transport 时由注入方负责同等纪律，fetch 对返回的 3xx 状态
       同样归 network 兜底；
    7. 原始响应默认不持久化：本模块任何路径都不写文件、不写
       stdout/stderr；错误消息只含 host / path / HTTP 状态码 /
       异常类名，绝不拼接响应体内容；
    8. 鉴权头格式遵循已验证的官方 provider 实现（实测：
       Authorization: <API Key>，无 Bearer 前缀），不做猜测式
       自创格式。

凭证纪律（§37 / §24）：
    - API key 只存实例属性 _api_key，仅用于每次请求的 Authorization
      头构造；绝不写入日志 / 异常消息 / 文件 / stdout/stderr；
    - 所有错误消息模板只含 host / path / 状态码 / 异常类型名，
      绝不拼入 key，也绝不拼入 str(exc)（防御性排除：urllib 异常
      文本可能携带 URL 等外部输入）；
    - 成功 snapshot 由 parser 产出（§27 形状），不含 Authorization
      与任何原始响应字段；缓存只存 snapshot（非秘密，§34）。

fetch 流程（§38）：
    缓存检查（§34：force=False 且距上次成功 < ttl_seconds → 返回
    缓存 snapshot 浅拷贝；失败永不写缓存）→ 传输层 → 状态分类
    （401/403 → auth；3xx → network（重定向被禁）；其余非 2xx →
    unavailable；URLError / timeout / OSError → network）→ 限长
    检查（malformed）→ JSON 解析（malformed）→ parse_quota_body
    （§27 标准化）→ 写缓存 → 返回浅拷贝。

传输层注入点（测试锚定；全部测试走注入，零真实网络）：
    transport(url, headers, timeout) -> (status_code:int, body:bytes)
    默认实现由 make_default_transport(max_bytes) 构造（urllib +
    禁重定向 opener + 限长读取）。

依赖：
    仅 Python 3.7 标准库（urllib.request / urllib.error / json /
    socket / datetime），零第三方依赖，`python3 -S` 可运行。

来源：
    docs/glm-conductor-v2-upgrade-guide-final.md §24/§25/§27/§34/
    §37/§38 + v2 升级计划工作块 B5.2；实测知识见
    docs/glm-conductor-v2-phase0-runtime-verification.md。
"""

import json
import socket
import urllib.error
import urllib.request
from datetime import datetime, timezone

from runtime.quota.parser import parse_quota_body
from runtime.quota.provider import ALLOWED_HOSTS, QuotaProvider, QuotaProviderError

# —— 默认参数（适配器签名从这里取默认值，单一事实源） ——

DEFAULT_TIMEOUT_SECONDS = 5.0
DEFAULT_MAX_BYTES = 65536
DEFAULT_TTL_SECONDS = 120

# —— 请求常量 ——
# 鉴权头格式为实测结论：Authorization: <API Key>，无 Bearer 前缀（§37 条款 8）
_USER_AGENT = "glm-conductor/2.0"
_QUOTA_PATH = "/api/monitor/usage/quota/limit"

# —— provider 名 → 监控 host 注册表（§25：host 字符串仅在此出现，
# 与 ALLOWED_HOSTS 的成员关系在导入期逐项断言，防漂移） ——

_PROVIDER_HOSTS = {
    "zai": "api.z.ai",
    "bigmodel": "open.bigmodel.cn",
}
for _registered_host in _PROVIDER_HOSTS.values():
    if _registered_host not in ALLOWED_HOSTS:
        raise AssertionError(
            "quota host 注册表漂移：%s 不在 ALLOWED_HOSTS 内"
            % (_registered_host,))
del _registered_host


# —— 禁用重定向（§37 条款 6） ——

class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """禁用重定向的 handler。

    redirect_request 恒返回 None——urllib 约定返回 None 时对 3xx
    抛 HTTPError，即重定向在任何情况下都不跟随、绝不发起第二次
    请求（重定向目标因此根本不参与 allowlist 判断）；3xx 由 fetch
    统一归类为 network。
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def build_default_opener():
    """构造默认 opener：挂 _NoRedirectHandler（重定向禁用）。"""
    return urllib.request.build_opener(_NoRedirectHandler)


_DEFAULT_OPENER = build_default_opener()


def make_default_transport(max_bytes):
    """构造默认传输层 transport（urllib 实现，§37 条款 3/4/6）。

    返回 transport(url, headers, timeout) -> (status_code, body)：
      - GET 请求，headers 原样透传（Authorization 头由 fetch 构造，
        本函数不接触 key 值）；
      - opener 为禁重定向版本：3xx / 其他非 2xx 由 urllib 抛
        HTTPError，交 fetch 分类；
      - 响应体最多读 max_bytes+1 字节（§37 条款 4，防超大响应体）；
      - timeout 透传给 opener（§37 条款 3）。
    """
    def _transport(url, headers, timeout):
        request = urllib.request.Request(url, headers=headers, method="GET")
        response = _DEFAULT_OPENER.open(request, timeout=timeout)
        try:
            body = response.read(max_bytes + 1)
            status = getattr(response, "status", None)
            if status is None:
                status = response.getcode()
        finally:
            response.close()
        return status, body

    return _transport


def _utc_now_iso_ms():
    """当前 UTC 时刻 → ISO8601 毫秒精度字符串（如
    "2026-08-28T05:00:00.123Z"），作为 snapshot 的 fetched_at。"""
    now = datetime.now(timezone.utc)
    return "%s.%03dZ" % (now.strftime("%Y-%m-%dT%H:%M:%S"),
                         now.microsecond // 1000)


# —— 共享适配器基类 ——

class HttpQuotaProvider(QuotaProvider):
    """Z.ai / BigModel 共享 HTTP quota 适配器基类（§25/§34/§37/§38）。

    构造参数：
      - api_key：非空 str，否则 ValueError；只存 self._api_key，
        绝不进入日志 / 异常消息 / 文件（§37）；
      - name：provider 标识（"zai" / "bigmodel"），host 默认经
        _PROVIDER_HOSTS 注册表解析；显式传 host 仅供测试构造
        非法 host 场景，同样受 allowlist 严格校验；
      - timeout：请求超时秒数（正数，默认 5.0，§37 条款 3）；
      - max_bytes：响应体读取上限字节数（正整数，默认 65536，
        §37 条款 4）；
      - ttl_seconds：成功 snapshot 的缓存 TTL 秒数（非负数，
        默认 120，§34）；
      - transport：注入点，签名见模块 docstring；None → 默认
        urllib 实现（重定向禁用 + 限长读取）。

    host 不在 ALLOWED_HOSTS / URL 非 https → 构造期抛
    QuotaProviderError(kind="network")（§37 条款 1/2，且绝不发起
    任何请求）。
    """

    def __init__(self, api_key, *, name, host=None,
                 timeout=DEFAULT_TIMEOUT_SECONDS,
                 max_bytes=DEFAULT_MAX_BYTES, transport=None,
                 ttl_seconds=DEFAULT_TTL_SECONDS):
        if not isinstance(api_key, str) or api_key == "":
            # 消息不回显任何值（防御：值本身可能近似凭证，§37）
            raise ValueError("api_key 必须是非空字符串")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) \
                or timeout <= 0:
            raise ValueError("timeout 必须是正数（秒）")
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) \
                or max_bytes <= 0:
            raise ValueError("max_bytes 必须是正整数（字节）")
        if isinstance(ttl_seconds, bool) \
                or not isinstance(ttl_seconds, (int, float)) \
                or ttl_seconds < 0:
            raise ValueError("ttl_seconds 必须是非负数（秒）")
        if transport is not None and not callable(transport):
            raise ValueError("transport 必须是可调用对象或 None")

        resolved_host = host if host is not None \
            else _PROVIDER_HOSTS.get(name)

        # §37 条款 1/2：HTTPS only + 严格 host allowlist，构造期暴露
        # （在基类构造之前拦截，保证清单外 host 一律 QuotaProviderError
        # kind="network"；None 表示注册表未收录 provider 名，交由基类
        # ValueError）
        if resolved_host is not None and resolved_host not in ALLOWED_HOSTS:
            raise QuotaProviderError(
                "quota host 不在允许清单内（host=%s）" % (resolved_host,),
                "network")
        super().__init__(name, resolved_host)

        url = "https://" + resolved_host + _QUOTA_PATH
        if not url.startswith("https://"):
            raise QuotaProviderError(
                "quota URL 必须是 https（host=%s，path=%s）"
                % (resolved_host, _QUOTA_PATH), "network")

        self._api_key = api_key  # 凭证唯一存放处（§37）
        self.timeout = timeout
        self.max_bytes = max_bytes
        self.ttl_seconds = ttl_seconds
        self._url = url
        self._transport = transport if transport is not None \
            else make_default_transport(max_bytes)
        self._cache = None     # 只缓存标准化 snapshot（非秘密，§34）
        self._cache_at = None  # 上次成功抓取时刻（UTC datetime）

    # —— fetch（§38 流程） ——

    def fetch(self, force=False):
        """抓取标准化 quota snapshot（§27 形状）。

        - force=False 且距上次成功抓取不足 ttl_seconds 秒 → 返回
          缓存 snapshot 的浅拷贝，不发起请求（§34）；
        - force=True 或缓存过期 → 真实请求；
        - 任何失败都不写缓存、不清缓存；
        - 失败抛 QuotaProviderError（分类见模块 docstring），不返回
          None、不抛裸异常。
        """
        if not force and self._cache is not None:
            elapsed = (datetime.now(timezone.utc)
                       - self._cache_at).total_seconds()
            if elapsed < self.ttl_seconds:
                return dict(self._cache)  # 浅拷贝：调用方改动不污染缓存

        headers = {
            # 实测格式：Authorization: <API Key>，无 Bearer 前缀（§37 条款 8）
            "Authorization": self._api_key,
            "Accept": "application/json",
            "User-Agent": _USER_AGENT,
        }
        try:
            status, body = self._transport(self._url, headers, self.timeout)
        except urllib.error.HTTPError as exc:
            # 非 2xx / 被禁重定向（默认传输路径）；401/403 → auth，
            # 3xx → network（重定向被禁），其余 → unavailable
            raise self._status_error(exc.code) from exc
        except (urllib.error.URLError, socket.timeout, OSError) as exc:
            raise self._network_error(type(exc).__name__) from exc

        if not 200 <= status < 300:
            # 注入 transport 返回非 2xx 状态（含 3xx 兜底）
            raise self._status_error(status)

        # §37 条款 4：限长响应——只可能读到 max_bytes+1 字节
        if len(body) > self.max_bytes:
            raise QuotaProviderError(
                "bounded response exceeded：quota 响应超过 %d 字节上限"
                "（host=%s，path=%s）" % (self.max_bytes, self.host,
                                          _QUOTA_PATH),
                "malformed")
        try:
            parsed = json.loads(body)
        except ValueError:
            # JSONDecodeError / UnicodeDecodeError 都是 ValueError 子类
            raise QuotaProviderError(
                "quota 响应不是合法 JSON（host=%s，path=%s）"
                % (self.host, _QUOTA_PATH), "malformed")

        snapshot = parse_quota_body(
            parsed, provider=self.name, fetched_at=_utc_now_iso_ms())
        self._cache = snapshot          # 失败路径到不了这里（§34）
        self._cache_at = datetime.now(timezone.utc)
        return dict(snapshot)           # 浅拷贝（§34）

    # —— 错误构造（消息只含 host/path/状态码/异常类型名，绝不拼 key，§37） ——

    def _status_error(self, code):
        """按 HTTP 状态归类：401/403 → auth；3xx → network（重定向
        被禁）；其余 → unavailable。"""
        if code in (401, 403):
            kind = "auth"
        elif 300 <= code < 400:
            kind = "network"
        else:
            kind = "unavailable"
        return QuotaProviderError(
            "quota 端点返回 HTTP %d（host=%s，path=%s）"
            % (code, self.host, _QUOTA_PATH), kind)

    def _network_error(self, error_type):
        """传输层异常（URLError / timeout / OSError）→ network。"""
        return QuotaProviderError(
            "quota 请求网络失败（host=%s，path=%s，error_type=%s）"
            % (self.host, _QUOTA_PATH, error_type), "network")
