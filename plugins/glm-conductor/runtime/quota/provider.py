#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GLM Conductor v2 quota provider 抽象边界（v2 工作块 B5.1）。

职责：
    定义 quota 查询的 provider 抽象与统一错误类型，是后续适配器
    （B5.2，Z.ai / BigModel）与调度器（B5.3）之间的契约层：
      - QuotaProviderError：quota 查询的结构性 / 传输性错误，
        kind ∈ ERROR_KINDS（auth / unavailable / malformed /
        network / unknown），错误分类供上层 fail-open 决策使用
        （升级指南 §28）；
      - QuotaProvider：provider 基类，声明 fetch() 的实现契约
        （§24 抽象边界 + §37 HTTP / 凭证安全条款）；
      - ALLOWED_HOSTS：§37 初始 host allowlist——适配器引用本常量，
        不得自行散布 host 字符串（§25）。

实现契约（后续适配器必须逐条满足，§24/§25/§37）：
    fetch() 返回标准化 snapshot dict（§27 形状：provider /
    plan_generation / plan_level / fetched_at / status / windows，
    windows 每项含 kind / used_percent / remaining_percent /
    reset_at）；失败一律抛 QuotaProviderError，不返回 None、不抛
    裸异常。实现类必须：
      1. 仅使用 HTTPS；
      2. 严格 host allowlist（仅 ALLOWED_HOSTS 内的 host，初始为
         api.z.ai 与 open.bigmodel.cn）；
      3. 短超时（不得无限等待）；
      4. 限长响应（读取设上限，防超大响应体）；
      5. 不向任意用户提供的 host 转发凭证；
      6. 重定向禁用，或重定向目标重新过 allowlist 校验；
      7. 原始响应默认不持久化（provider 特有的响应形状必须隔离在
         适配器内部，§24）；
      8. 鉴权头格式遵循已验证的官方 provider 实现（实测：
         Authorization: <API Key>，无 Bearer 前缀），不做猜测式
         自创格式；
      9. 凭证零落盘：凭证绝不写入 .glm-conductor/ 目录、state.json、
         events.jsonl、checkpoint.md、日志或 stdout/stderr（§37）。

错误 str 纪律（§37 的代码级锚点）：
    QuotaProviderError.__str__ 只包含调用方给定的 message 与 kind，
    本类自身绝不附加任何凭证或 Authorization 值；调用方构造 message
    时同样不得把凭证拼进错误文本。凭证零落盘由两侧共同保证。

依赖：
    仅 Python 3 标准库，零第三方依赖，`python3 -S` 可运行。
    风格对齐 runtime/state.py / runtime/fingerprint.py。

来源：
    docs/history/v2.0/glm-conductor-v2-upgrade-guide-final.md §24（provider 抽象）、
    §25（端点兼容）、§27（snapshot 形状）、§28（状态词汇）、
    §37（凭证安全政策）+ v2 升级计划工作块 B5.1。
"""

# —— host allowlist（§37 初始清单；适配器引用本常量，不自行散布 host） ——

ALLOWED_HOSTS = ("api.z.ai", "open.bigmodel.cn")

# —— 错误分类词汇（供上层 fail-open 决策，§28） ——

ERROR_KINDS = ("auth", "unavailable", "malformed", "network", "unknown")


class QuotaProviderError(Exception):
    """quota 查询结构性/传输性错误。kind ∈ ERROR_KINDS。

    - 构造：QuotaProviderError(message, kind)；kind 不在 ERROR_KINDS
      内 → ValueError（先校验再构造，失败不产生半成品对象）；
    - self.kind 属性记录错误分类；self.message 保留原始消息；
    - __str__ 只含 message 与 kind，绝不含任何凭证或 Authorization
      值——本类自身不持有、不附加凭证（§37 纪律的代码级锚点）；
      调用方构造 message 时同样不得把凭证拼进错误文本，凭证零落盘
      由两侧共同保证。
    """

    def __init__(self, message, kind):
        if kind not in ERROR_KINDS:
            raise ValueError(
                "QuotaProviderError：kind %r 不在合法取值内（%s）"
                % (kind, ", ".join(ERROR_KINDS)))
        super().__init__(message)
        self.message = message
        self.kind = kind

    def __str__(self):
        return "%s（kind=%s）" % (self.message, self.kind)


class QuotaProvider(object):
    """quota 查询 provider 基类（§24 抽象边界）。

    属性：
      - name：provider 标识（非空 str，如 "zai" / "bigmodel"），
        原样进入标准化 snapshot 的 provider 字段；
      - host：provider 监控端点 host（非空 str），适配器实现必须
        校验其 ∈ ALLOWED_HOSTS（§37 严格 allowlist）。

    契约（实现类必须满足，逐条见模块 docstring「实现契约」）：
      - fetch(force=False) 返回标准化 snapshot dict（§27 形状，
        解析见 runtime.quota.parser.parse_quota_body）；
      - 失败一律抛 QuotaProviderError（kind 标明分类），不返回
        None、不抛裸异常；
      - 仅 HTTPS + ALLOWED_HOSTS 严格校验 + 短超时 + 限长响应；
      - 凭证零落盘（§37：不写入 .glm-conductor/、state.json、
        events.jsonl、checkpoint.md、日志、stdout/stderr）。
    """

    def __init__(self, name, host):
        if not isinstance(name, str) or name == "":
            raise ValueError(
                "QuotaProvider：name 必须是非空字符串，得到 %r" % (name,))
        if not isinstance(host, str) or host == "":
            raise ValueError(
                "QuotaProvider：host 必须是非空字符串，得到 %r" % (host,))
        self.name = name
        self.host = host

    def fetch(self, force=False):
        """抓取并返回标准化 quota snapshot dict（§27 形状）。

        基类不实现——返回 QuotaSnapshot 是适配器的职责（B5.2）：
          - force=True 时跳过缓存强制刷新（§34：显式 quota 相关
            失败后必须 force refresh）；
          - 失败抛 QuotaProviderError，错误分类见 ERROR_KINDS。
        """
        raise NotImplementedError(
            "QuotaProvider.fetch 是抽象方法：适配器必须实现"
            "（契约见 runtime.quota.provider 模块 docstring）")
