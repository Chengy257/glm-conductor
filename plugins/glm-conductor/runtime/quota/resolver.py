#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GLM Conductor v2.1 运行时额度解析层（v2.1 §13 Runtime Quota，wu-21-10）。

职责：
    把「派发前到底信哪个额度状态」固化为一个纯入口
    resolve_quota_status()，供 task_manager 的 prepare API（v2.1 起
    quota_status 缺省 None 时触发——**绝不默认 AVAILABLE**）与后续
    恢复链（wu-21-11）消费。v2.2 M3（wu-22-03）追加明细入口
    resolve_quota_detail()（status 之外透出 §27 snapshot 与
    fetched_at，供 observer 观测面消费）——两入口共享同一份层级
    实现（_resolve_quota），禁止复制出第二份抓取流程。
    四级层级（顺序即优先级，逐级降级）：

        1. fresh cache    本地缓存存在且距上次抓取 ≤
                          CACHE_TTL_FRESH_SECONDS（300 秒）→ 直接采信
                          缓存 status，绝不发网络；
        2. provider fetch 缓存缺失 / 过期 / force_refresh → 解析凭证并
                          经 provider 抓取 → scheduler.evaluate 四态 →
                          原子写缓存 → 采信 provider 结果；
        3. stale cache    provider 链路任何一环失败（无凭证 / 网络 /
                          解析 / 写盘）→ 回退本地缓存（不论多旧）；
        4. UNKNOWN        无缓存可用 → status="UNKNOWN"（fail-open，
                          上层按 §12 折预算 1，不阻塞派发）。

纪律（本模块的硬边界）：
    - 绝不重试网络：一次 fetch 失败即降级（quota 是 best-effort 观测
      面，重试只会拖慢派发路径）；provider 探测按 report.py 的装配
      顺序（zai → bigmodel，任一成功即用）逐个尝试一轮后立即放弃，
      不对同一 provider 二次尝试；
    - 异常绝不外泄：QuotaProviderError 只取 kind 分类、其余异常只取
      类型名进 reason——绝不透传异常文本（可能携带 URL / 响应体）；
    - credential safety（§37）：返回 dict 与缓存文件绝不含凭证材料
      （key 只在 provider 构造内部用作 Authorization 头，本模块不落
      盘、不拼进任何字符串）；缓存只存 §27 标准化 snapshot（非秘密）；
    - 绝不虚构数据：无缓存且 provider 不可用 → UNKNOWN，宁缺勿造
      （§28/§31/§33 fail-open 语义在解析层的对应）；
    - now 注入（None → 当前 UTC）供测试；timeout_seconds 透传
      provider 构造（None → provider 默认 5.0 秒，§37 条款 3）。

测试注入纪律（全部测试零真实网络，对齐 report.py）：
    provider 构造经模块级工厂 _build_providers(api_key, timeout_seconds)
    （测试 monkeypatch 本函数注入 fake provider）；凭证解析经模块级
    resolve_credential 名字（测试 monkeypatch 注入，或注入
    (None, None) 模拟无凭证）。

依赖：
    仅 Python 3.7 标准库（json / os / datetime）+ runtime.durable_io
    （v2.2.1 WU-221-A2 起缓存原子写委托其 atomic_write_json），零第
    三方依赖，`python3 -S` 可运行。provider 装配照抄 runtime/quota/
    report.py（_PROVIDER_ORDER + 逐个探测、任一成功即用；report.py
    本身不改，允许少量重复，见 _PROVIDER_ORDER 处注释）。风格对齐
    runtime/quota/scheduler.py。

来源：
    docs/GLM-Conductor-v2.1-Architecture-Agent-Implementation-Plan.md
    §13（Runtime Quota 集成，主会话 wu-21-10 实施规格）+
    docs/glm-conductor-v2-upgrade-guide-final.md §27（snapshot）、
    §28（四态评估）、§34（缓存）、§37（凭证安全）+ wu-21-09 已落地
    的预算语义（UNKNOWN → 预算 1，不挂起）。
"""

import json
import os
from datetime import datetime, timezone

from runtime import durable_io
from runtime.quota.bigmodel import BigModelQuotaProvider
from runtime.quota.credentials import resolve_credential
from runtime.quota.parser import QUOTA_STATUSES
from runtime.quota.provider import QuotaProviderError
from runtime.quota.scheduler import evaluate
from runtime.quota.zai import ZaiQuotaProvider

# —— 词汇表常量 ——

# 解析来源词汇（返回 dict 的 source 取值，与四级层级一一对应；
# "none" 表示无缓存也无 provider 结果）
QUOTA_SOURCES = ("cache_fresh", "provider", "cache_stale", "none")

# 本地缓存新鲜期（秒）：距上次成功抓取不超过该值即直接采信缓存，
# 绝不发网络（层级 1；过期后走 provider，失败再回退 stale 缓存）
CACHE_TTL_FRESH_SECONDS = 300

# 缓存文件名（恒在 <repo_root>/.glm-conductor/ 下——与账本同目录，
# 不新增顶层散落文件）
CACHE_FILE_NAME = "quota-cache.json"

# provider 探测顺序（照抄 runtime/quota/report.py 的 _PROVIDER_ORDER：
# zai 在前 bigmodel 在后，任一成功即用——provider 类与凭证形态的
# 对应关系与 report.py 保持一致，report.py 本身不改）
_PROVIDER_ORDER = (("zai", ZaiQuotaProvider), ("bigmodel", BigModelQuotaProvider))


# —— 测试注入点：模块级 provider 工厂（对齐 report.py._build_providers） ——

def _build_providers(api_key, timeout_seconds=None):
    """按探测顺序构造 [(name, provider)]（测试 monkeypatch 本函数）。

    照抄 report.py 的装配，仅追加 timeout_seconds 透传（None →
    provider 构造默认 5.0 秒；非 None 时以关键字参数传入，provider
    签名的其余参数保持默认）。
    """
    if timeout_seconds is None:
        return [(name, cls(api_key)) for name, cls in _PROVIDER_ORDER]
    return [(name, cls(api_key, timeout=timeout_seconds))
            for name, cls in _PROVIDER_ORDER]


# —— 时间助手（对齐 scheduler.py / report.py 的口径） ——

def _parse_iso_utc(text):
    """ISO8601 时刻文本 → aware datetime（UTC）；非 str / 不可解析 → None。

    统一 Z 形式（本模块缓存 fetched_at 的落盘形式，毫秒精度）；
    Python 3.7 的 fromisoformat 不认 Z 后缀，先改写为 +00:00；
    naive 时刻按 UTC 处理。
    """
    if not isinstance(text, str) or text == "":
        return None
    raw = text.strip()
    if raw.endswith("Z") or raw.endswith("z"):
        raw = raw[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


def _normalize_now(now):
    """归一 now 注入参数：None → 当前 UTC；datetime / ISO 串 → 时刻。

    now 仅供测试注入当前时刻使用；非法输入 → ValueError（中文，
    对齐 scheduler.plan_resume 的参数校验口径）。
    """
    if now is None:
        return datetime.now(timezone.utc)
    if isinstance(now, datetime):
        if now.tzinfo is None:
            return now.replace(tzinfo=timezone.utc)
        return now
    moment = _parse_iso_utc(now)
    if moment is None:
        raise ValueError(
            "resolve_quota_status：now 必须是 datetime 或 ISO8601 字符串，"
            "得到 %r" % (now,))
    return moment


def _format_iso_ms_z(moment):
    """aware datetime → UTC ISO8601 毫秒精度 Z 形式字符串（
    "2026-08-31T05:00:00.123Z"，与 lease / permit 层时间字段同格式），
    作为 evaluated_at 与缓存 fetched_at 的落盘形式。"""
    moment = moment.astimezone(timezone.utc)
    return "%s.%03dZ" % (moment.strftime("%Y-%m-%dT%H:%M:%S"),
                         moment.microsecond // 1000)


# —— 缓存原语（损坏 / 非法一律视为无缓存，绝不因坏缓存炸调用方） ——

def _cache_path(repo_root) -> str:
    """<repo_root>/.glm-conductor/quota-cache.json 的路径。"""
    return os.path.join(str(repo_root), ".glm-conductor", CACHE_FILE_NAME)


def _load_cache(path):
    """读缓存文件 → dict 或 None。

    文件不存在 / OSError / JSON 损坏 / 非 dict / status 不在
    QUOTA_STATUSES / fetched_at 不可解析 → 一律 None（视为无缓存，
    层级判定按「无缓存」继续）；本函数绝不抛错。
    """
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        # OSError：不存在 / 权限等；ValueError：JSONDecodeError /
        # UnicodeDecodeError 的共同基类
        return None
    if not isinstance(data, dict):
        return None
    if data.get("status") not in QUOTA_STATUSES:
        return None
    if _parse_iso_utc(data.get("fetched_at")) is None:
        return None
    return data


def _save_cache(path, payload) -> None:
    """原子写缓存文件（v2.2.1 WU-221-A2 起委托共享原语 runtime.
    durable_io.atomic_write_json：唯一同目录临时名 + os.replace，
    UTF-8、ensure_ascii=False、缩进 2、sort_keys、固定 \\n 换行——
    落盘字节与既有手写实现逐字节一致；POSIX 注记：最终文件现继承原
    语唯一临时文件的 0o600 权限位（先前 umask 缺省约 0644——JSON 字
    节一致，仅文件权限位更收紧，对状态文件更安全）；父目录缺失由原
    语自动创建）。
    多进程写者（主会话 resolve + watcher 强制刷新）经唯一临时名
    绝不在固定 <path>.tmp 相撞；PermissionError（Windows AV / 目录
    锁瞬态）按原语默认有界重试（5 次 × 0.1 秒——对本模块原「零重
    试」是严格改进，WU-221-A1 许可的既有有界重试行为保留），耗尽
    原样上抛——由调用方统一兜底降级（异常不外泄给
    resolve_quota_status 的调用方）。"""
    durable_io.atomic_write_json(path, payload)


# —— 主入口：四级层级解析（两个公开入口共享同一份实现路径） ——

def _resolve_quota(repo_root, *, now, force_refresh, timeout_seconds) -> dict:
    """四级层级核心（v2.2 M3 起为 resolve_quota_status 与
    resolve_quota_detail 的唯一实现路径——禁止复制出第二份抓取流程）。

    返回六键 {"status", "source", "evaluated_at", "reason", "snapshot",
    "fetched_at"}：前四键即 resolve_quota_status 的冻结输出；snapshot
    （§27 形状）与 fetched_at（底层数据抓取时刻）供 resolve_quota_detail
    透出（无数据 → None）。层级流程 / reason 口径与本模块 docstring
    逐字一致。
    """
    moment = _normalize_now(now)
    evaluated_at = _format_iso_ms_z(moment)
    path = _cache_path(repo_root)
    cache = _load_cache(path)
    cached_status = cache.get("status") if cache else None
    cached_at = _parse_iso_utc(cache.get("fetched_at")) if cache else None

    # —— 层级 1：fresh cache（缓存命中即返回，绝不发网络） ——
    if cache is not None and not force_refresh:
        age_seconds = (moment - cached_at).total_seconds()
        if age_seconds <= CACHE_TTL_FRESH_SECONDS:
            return {
                "status": cached_status,
                "source": "cache_fresh",
                "evaluated_at": evaluated_at,
                "reason": "命中新鲜额度缓存（抓取于 %s，%d 秒前，%d 秒"
                          "新鲜期内，未发起网络查询）"
                          % (cache.get("fetched_at"), int(age_seconds),
                             CACHE_TTL_FRESH_SECONDS),
                "snapshot": cache.get("snapshot"),
                "fetched_at": cache.get("fetched_at"),
            }

    # —— 层级 2：provider fetch（一轮探测，任何异常降级不外泄） ——
    cause = "unknown"
    try:
        _credential_source, api_key = resolve_credential()
        if not isinstance(api_key, str) or api_key == "":
            cause = "no-credential"  # 无凭证：不构造 provider，直接降级
        else:
            snapshot = None
            provider_name = None
            for name, provider in _build_providers(api_key,
                                                   timeout_seconds):
                try:
                    # force=True 照抄 report.py 的探测口径（本模块每次
                    # 新建 provider 实例，实例缓存本就不可能命中）
                    snapshot = provider.fetch(force=True)
                    provider_name = name
                    break
                except QuotaProviderError as exc:
                    cause = exc.kind  # 只取 kind 分类，不透传消息
                except Exception as exc:
                    # 契约外异常只取类型名进 reason（绝不透传异常文本，
                    # 文本可能携带 URL / 响应体）
                    cause = type(exc).__name__
            if snapshot is not None:
                cause = None  # 抓取已成功：此后的异常（如写盘）另立原因
                status = evaluate(snapshot)["status"]
                _save_cache(path, {
                    "provider": provider_name,
                    "fetched_at": evaluated_at,
                    "snapshot": snapshot,
                    "status": status,
                })
                return {
                    "status": status,
                    "source": "provider",
                    "evaluated_at": evaluated_at,
                    "reason": "provider %s 抓取成功，额度已评估并刷新"
                              "本地缓存" % provider_name,
                    "snapshot": snapshot,
                    "fetched_at": evaluated_at,
                }
    except Exception as exc:  # OSError / 超时 / 写盘失败等一律降级
        if cause is None or cause == "unknown":
            cause = type(exc).__name__

    # —— 层级 3 / 4：stale cache → UNKNOWN（fail-open，绝不虚构） ——
    if cache is not None:
        age_seconds = int((moment - cached_at).total_seconds())
        if age_seconds > CACHE_TTL_FRESH_SECONDS:
            staleness = "数据抓取于 %s，已过期 %d 秒（超过 %d 秒新鲜期）" % (
                cache.get("fetched_at"), age_seconds,
                CACHE_TTL_FRESH_SECONDS)
        else:
            staleness = "数据抓取于 %s（force_refresh 跳过新鲜期）" % (
                cache.get("fetched_at"),)
        return {
            "status": cached_status,
            "source": "cache_stale",
            "evaluated_at": evaluated_at,
            "reason": "provider 额度查询不可用（%s），回退本地缓存：%s"
                      % (cause, staleness),
            "snapshot": cache.get("snapshot"),
            "fetched_at": cache.get("fetched_at"),
        }
    return {
        "status": "UNKNOWN",
        "source": "none",
        "evaluated_at": evaluated_at,
        "reason": "无额度缓存且 provider 额度查询不可用（%s），"
                  "按 UNKNOWN fail-open（不阻塞派发，预算按 1 折算）"
                  % cause,
        "snapshot": None,
        "fetched_at": None,
    }


def resolve_quota_status(repo_root, *, now=None, force_refresh=False,
                         timeout_seconds=None) -> dict:
    """解析当前额度状态（v2.1 §13 四级层级），返回键冻结的决策 dict。

    参数：
      - repo_root：仓库 / 账本根（缓存恒读写
        <repo_root>/.glm-conductor/quota-cache.json）；
      - now：参考时刻注入（None → 当前 UTC；datetime / ISO 串），
        仅供测试；同时决定 evaluated_at 与缓存 fetched_at 的口径；
      - force_refresh：True 时跳过层级 1（新鲜缓存也强制走 provider，
        §32 唤醒强制刷新语义的对应入口）；
      - timeout_seconds：透传 provider 构造（None → provider 默认）。

    返回（键冻结，四键；绝不含凭证材料，§37）：
      {"status": <QUOTA_STATUSES 四态之一>,
       "source": <QUOTA_SOURCES 之一>，
       "evaluated_at": <ISO8601 毫秒精度 Z 形式>,
       "reason": <中文一句话>}

    v2.2 M3（wu-22-03）：本函数与 resolve_quota_detail 共享内部实现
    _resolve_quota（签名 / 语义 / reason 逐字零变化，输出仍恰为四键）。

    层级流程（顺序即优先级，逐级降级；模块 docstring 有全文）：
      1. fresh cache：缓存有效且 age ≤ 300 秒且非 force_refresh →
         ("cache_fresh", 缓存 status)，零网络；
      2. provider fetch：resolve_credential() → _build_providers 按
         report.py 装配逐个探测（一轮，绝不重试）→ 成功即
         scheduler.evaluate(snapshot)["status"] → 原子写缓存
         {"provider", "fetched_at", "snapshot", "status"} →
         ("provider", status)；任何异常（无凭证 / QuotaProviderError /
         OSError / 超时 / 解析失败）不外泄，落到 3/4；
      3. stale cache：缓存存在（不论多旧）→ ("cache_stale", 缓存
         status)，reason 注明数据抓取时间与过期秒数；
      4. none：无缓存 → ("none", "UNKNOWN")，fail-open 不阻塞派发
         （上层 wu-21-09 预算语义：UNKNOWN → 预算 1）。
    """
    core = _resolve_quota(repo_root, now=now, force_refresh=force_refresh,
                          timeout_seconds=timeout_seconds)
    return {
        "status": core["status"],
        "source": core["source"],
        "evaluated_at": core["evaluated_at"],
        "reason": core["reason"],
    }


def resolve_quota_detail(repo_root, *, now=None, force_refresh=False) -> dict:
    """明细入口（v2.2 M3 wu-22-03）：四级层级解析 + §27 snapshot 透出。

    与 resolve_quota_status 共享同一内部实现（_resolve_quota），四级
    层级语义 / reason 口径逐字一致，仅输出键不同——供观测面（CLI
    quota-observe / quota-phase → observer.observe）拿 snapshot 的
    windows 去喂 M2 决策器，避免「状态有了、明细没了」的二次读盘。

    参数：
      - repo_root / now / force_refresh：同 resolve_quota_status
        （timeout 不暴露——观测面恒用 provider 默认超时）。

    返回（键冻结，四键；绝不含凭证材料，§37）：
      {"source": <QUOTA_SOURCES 四值之一>，
       "status": <QUOTA_STATUSES 四态之一（none 层为 UNKNOWN）>，
       "snapshot": <§27 形状 snapshot 或 None（none 层）>，
       "fetched_at": <底层数据抓取时刻，ISO8601 毫秒精度 Z 形式或
                      None>}
    fetched_at 语义：provider 层 = 本次抓取时刻（= evaluated_at）；
    cache 层（fresh / stale）= 缓存记录的原始抓取时刻；none 层 =
    None（无数据，绝不虚构）。
    """
    core = _resolve_quota(repo_root, now=now, force_refresh=force_refresh,
                          timeout_seconds=None)
    return {
        "source": core["source"],
        "status": core["status"],
        "snapshot": core["snapshot"],
        "fetched_at": core["fetched_at"],
    }
