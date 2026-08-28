#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Z.ai quota 监控适配器（v2 工作块 B5.2，升级指南 §25/§37/§38）。

薄封装：HTTP 传输 / 安全条款 / 缓存 / 错误分类全部在共享基类
runtime.quota._http.HttpQuotaProvider（§37 八条安全条款在该模块
docstring 逐条声明，测试锚定见 tests/test_quota_adapters.py）。
本模块只绑定 provider 标识 "zai"；监控 host 经 _PROVIDER_HOSTS
注册表解析并受 ALLOWED_HOSTS 严格校验，代码路径不出现 host 字符串（仅本 docstring 说明端点）
（§25「不自行散布 host」）。

端点（实测，§25）：GET https://api.z.ai/api/monitor/usage/quota/limit；
鉴权头 Authorization: <API Key>（无 Bearer 前缀，与官方
glm-plan-usage 插件一致的已验证格式，§37 条款 8）。

安全条款摘要（§37，逐条声明见 runtime.quota._http）：
    HTTPS only / 严格 host allowlist / 短超时 / 限长响应 /
    不向 allowlist 外 host 转发凭证 / 重定向禁用 / 原始响应不落盘 /
    鉴权头遵循实测格式；凭证只存实例属性 _api_key，绝不进入日志、
    异常消息、文件或 stdout/stderr。

用法：
    from runtime.quota.zai import ZaiQuotaProvider
    snapshot = ZaiQuotaProvider(api_key).fetch()   # 失败抛 QuotaProviderError

依赖：
    仅 Python 3.7 标准库，零第三方依赖，`python3 -S` 可运行。
"""

from runtime.quota._http import (DEFAULT_MAX_BYTES, DEFAULT_TIMEOUT_SECONDS,
                                 DEFAULT_TTL_SECONDS, HttpQuotaProvider)


class ZaiQuotaProvider(HttpQuotaProvider):
    """Z.ai quota provider（§24 抽象边界的一个实现）。

    fetch(force=False) → 标准化 snapshot dict（§27 形状）；
    失败抛 QuotaProviderError（kind ∈ auth/unavailable/malformed/
    network/unknown）。
    """

    name = "zai"

    def __init__(self, api_key, *, timeout=DEFAULT_TIMEOUT_SECONDS,
                 max_bytes=DEFAULT_MAX_BYTES, transport=None,
                 ttl_seconds=DEFAULT_TTL_SECONDS):
        super().__init__(api_key, name=self.name, timeout=timeout,
                         max_bytes=max_bytes, transport=transport,
                         ttl_seconds=ttl_seconds)
