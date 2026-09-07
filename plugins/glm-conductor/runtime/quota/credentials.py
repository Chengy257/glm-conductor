#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GLM Conductor v2 quota 凭证解析层（v2 工作块 B5.4，升级指南 §36）。

职责：
    为 provider-api 模式（§36 三模式之一）解析 GLM Coding Plan API
    凭证，返回 (source, key) 二元组，供只读诊断面（report.py，B5.5）
    与后续编排层消费：
      - env 优先：环境变量 GLM_CONDUCTOR_QUOTA_API_KEY（非空 str）
        命中 → ("env", 值)；空串按未设置处理；
      - ZCode provider 配置回退：读取 ~/.zcode/v2/config.json（实测
        结构：{"provider": {"builtin:bigmodel-coding-plan":
        {"options": {"apiKey": "<49 字符点分格式>"}}}}），结构遍历
        顶层 provider dict → PROVIDER_KEY 条目 → options dict →
        apiKey 非空 str → ("zcode-provider", 值)；
      - (None, None)：两路都不可得 → unavailable 模式（§36）。
        诊断面自行报告 unavailable，解析层绝不因此抛错。
    v2.2 M9（wu-22-10）追加 provider 家族发现纯查询
    discover_families()：白名单枚举 resolver 探测链实际装配的家族
    （zai → bigmodel，与 runtime/quota/resolver.py 的
    _PROVIDER_ORDER / report.py 的 _PROVIDER_ORDER 装配顺序一致），
    返回家族名 + 凭据材料可用性布尔 + 凭据来源模式描述——零秘密
    泄露（只有名字与布尔/模式，绝不返回 key 本体），零网络（纯
    本地凭据材料存在性判定，绝不构造 provider、绝不发请求），
    供观测面回答「当前环境有哪些可用 provider 家族」。

凭证纪律（§37 凭证安全政策在本层的落地；tests/test_quota_credentials.py
逐条锚定）：
    - 本模块绝不 print / log / raise key 值：任何路径不调用 print，
      不把 key 拼进任何异常消息，返回值之外零副作用；
    - 不修改配置文件（只读打开，不写不删）；
    - 文件级失败（OSError / JSON 解析失败 / 结构不符）一律静默降级
      为 (None, None)——解析层不报告原因，原因归诊断面解释。

依赖：
    仅 Python 3.7 标准库（json / os），零第三方依赖，
    `python3 -S` 可运行。风格对齐 runtime/quota/parser.py。

来源：
    docs/glm-conductor-v2-upgrade-guide-final.md §36（三模式与凭证
    来源）、§37（凭证安全政策）+ v2 升级计划工作块 B5.4；实测知识：
    ZCode provider 配置路径 ~/.zcode/v2/config.json；
    docs/history/v2.2/GLM-Conductor-v2.2-Quota-Continuity-Control-Loop-Implementation-
    Plan.md WU-22-10（Credential Provider Discovery）。
"""

import json
import os

# —— 词汇表常量 ——

# 凭证来源词汇（resolve_credential 返回的 source 取值）
CREDENTIAL_SOURCES = ("env", "zcode-provider")

# 环境变量名（env 优先策略的唯一事实源）
ENV_VAR = "GLM_CONDUCTOR_QUOTA_API_KEY"

# ZCode provider 配置中的 provider 键（实测，§36）
PROVIDER_KEY = "builtin:bigmodel-coding-plan"

# provider 家族白名单（v2.2 M9 wu-22-10）：家族名与顺序照抄
# runtime/quota/resolver.py._PROVIDER_ORDER 的探测装配（zai 在前
# bigmodel 在后；同源 report.py._PROVIDER_ORDER）。credentials 不
# import resolver——resolver 单向 import 本模块（凭证解析），反向
# 引用成环；字面量一致性由 tests.test_credentials_family 对
# resolver._PROVIDER_ORDER 的同步测试锚定（同 control.py 词汇纪律）。
PROVIDER_FAMILIES = ("zai", "bigmodel")

# 家族不可用时的模式描述词（与 describe_modes 返回值的 "unavailable"
# 键同词汇：未解析到任何凭据材料）
FAMILY_MODE_UNAVAILABLE = "unavailable"

# 缺省配置路径标记：None = 调用时不显式传 config_path 的展开语义
# （运行时按 ~/.zcode/v2/config.json 展开，见 _default_config_path）
DEFAULT_CONFIG_PATH = None

# 固定回退路径（仅供 config_path=None 时 expanduser 展开；§36 实测）
_DEFAULT_ZCODE_CONFIG = "~/.zcode/v2/config.json"


def _default_config_path():
    """config_path 缺省时的展开路径：~/.zcode/v2/config.json。"""
    return os.path.expanduser(_DEFAULT_ZCODE_CONFIG)


def _is_nonempty_str(value):
    """值是否为非空 str（空串与一切非 str 一律视为「未设置」）。"""
    return isinstance(value, str) and value != ""


def resolve_credential(config_path=None, *, environ=None):
    """解析 GLM Coding Plan 凭证 → (source, key) 二元组（§36）。

    参数：
      - config_path：ZCode provider 配置文件路径；None（缺省）→
        ~/.zcode/v2/config.json（expanduser 展开）；
      - environ：env 查找用的映射（如 os.environ）；None（缺省）→
        os.environ；测试可注入 dict 隔离真实环境。

    返回（key 为非空 str 或 None；绝不 print / raise key 值）：
      - ("env", 值)：环境变量命中（非空 str；空串按未设置）——
        env 优先，命中即返回，不读配置文件；
      - ("zcode-provider", 值)：env 未命中且配置文件结构遍历命中
        （provider dict → PROVIDER_KEY → options → apiKey 非空 str）；
      - (None, None)：两路都不可得（unavailable 模式）。文件不存在 /
        OSError / 非 JSON / 顶层或任一层结构不符 → 静默降级到此，
        不抛错、不打印、不写任何文件。
    """
    if environ is None:
        environ = os.environ
    env_value = environ.get(ENV_VAR)
    if _is_nonempty_str(env_value):
        return ("env", env_value)
    return _from_zcode_config(config_path)


def _from_zcode_config(config_path):
    """ZCode provider 配置回退路径（只读；任何失败静默 (None, None)）。"""
    if config_path is None:
        config_path = _default_config_path()
    try:
        with open(config_path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        # OSError：文件不存在 / 权限 / 路径是目录等；
        # ValueError：JSONDecodeError / UnicodeDecodeError 的共同基类。
        return (None, None)
    if not isinstance(data, dict):
        return (None, None)
    providers = data.get("provider")
    if not isinstance(providers, dict):
        return (None, None)
    entry = providers.get(PROVIDER_KEY)
    if not isinstance(entry, dict):
        return (None, None)
    options = entry.get("options")
    if not isinstance(options, dict):
        return (None, None)
    api_key = options.get("apiKey")
    if _is_nonempty_str(api_key):
        return ("zcode-provider", api_key)
    return (None, None)


def describe_modes():
    """§36 三模式说明词汇（report 与文档共用，不出现 host 字符串）。

    返回 dict：native（未来）/ provider-api（当前）/ unavailable
    三键，值为中文一句话说明。
    """
    return {
        "native": "native（未来）：由 GLM 官方原生入口直接读取套餐额度"
                  "（尚未启用）",
        "provider-api": "provider-api（当前）：经监控端点查询套餐额度"
                        "（凭证只用于 Authorization 头）",
        "unavailable": "unavailable：未解析到任何凭证，无法查询额度",
    }


def discover_families(config_path=None, *, environ=None):
    """provider 家族白名单发现（v2.2 M9 wu-22-10）——纯本地零网络。

    按白名单 PROVIDER_FAMILIES（= resolver._PROVIDER_ORDER 的家族名
    与顺序）逐家族枚举，回答「当前环境有哪些可用 provider 家族」：
    resolver 探测链的家族共享同一份凭据材料（resolve_credential 的
    单键按装配顺序喂给每个 provider），故可用性对全家族一致，按家族
    逐项给出。

    参数（与 resolve_credential 同款注入面）：
      - config_path：ZCode provider 配置文件路径；None（缺省）→
        ~/.zcode/v2/config.json（expanduser 展开）；
      - environ：env 查找用的映射；None（缺省）→ os.environ；测试可
        注入 dict 隔离真实环境。

    返回（list[dict]，顺序恒为白名单序，逐项键冻结三键）：
      [{"family": <家族名（PROVIDER_FAMILIES 之一）>,
        "available": <bool——凭据材料是否在位>,
        "mode": <凭据来源模式——available 时为 CREDENTIAL_SOURCES
                 之一（"env" / "zcode-provider"），否则
                 FAMILY_MODE_UNAVAILABLE>} ...]

    纪律（§37 凭证安全政策 + 观测面纯查询语义）：
      - 零秘密泄露：返回值只含家族名、布尔与模式描述词，绝不返回
        key 本体（key 在 resolve_credential 内部解析后即丢弃）；
      - 零网络零墙钟：纯凭据材料存在性判定（本地 env / 配置文件
        只读），绝不构造 provider、绝不发起请求、不读时钟；
      - 文件级失败静默降级（同 resolve_credential：缺失 / OSError /
        非 JSON → available=False / mode=unavailable，绝不抛）。
    """
    _source, key = resolve_credential(config_path, environ=environ)
    available = _is_nonempty_str(key)
    mode = _source if available else FAMILY_MODE_UNAVAILABLE
    return [{"family": family, "available": available, "mode": mode}
            for family in PROVIDER_FAMILIES]
