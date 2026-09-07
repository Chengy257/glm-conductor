#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GLM Coding Plan 额度只读诊断 CLI（v2 工作块 B5.5，升级指南 §43）。

职责：
    §43 `glm-conductor quota` 的等价只读诊断面。解析凭证
    （runtime.quota.credentials，§36 provider-api 模式）→ 逐 provider
    探测（zai → bigmodel，fetch(force=True)，任一成功即用该 snapshot）
    → 调度评估（§28 evaluate）与恢复规划（§29-§33 plan_resume）→
    文本（人读）或 --json（机器可读）双输出。

诊断三态（退出码恒 0——诊断面不是闸门，不做任何拦截决策）：
    1. unavailable：凭证解析为 (None, None) → 输出配置指引
       （设置 GLM_CONDUCTOR_QUOTA_API_KEY，或依赖已登录 ZCode 的
       ~/.zcode/v2/config.json provider 配置）；
    2. provider 探测全失败：每 provider 一行 kind + 中文说明（不含
       key、不含 URL 查询串、不含响应体）；
    3. 成功：额度窗口明细 + status + plan。

零凭证输出纪律（§37；tests/test_quota_report.py 锚定）：
    本脚本任何路径的 stdout 都不含 key / Authorization 值 / 响应体；
    失败摘要只含 provider 名与错误分类（ERROR_KINDS 词汇）；意外
    异常的兜底输出只含异常类型名，不回显异常文本（防御性排除）。

测试注入纪律（全部测试零真实网络）：
    provider 构造经模块级工厂 _build_providers(api_key)（测试
    monkeypatch 本函数注入 fake provider）；凭证解析经模块级
    resolve_credential 名字（测试 monkeypatch 注入）。

依赖：
    仅 Python 3.7 标准库（argparse / json / math / datetime），
    零第三方依赖。风格对齐 runtime/quota/scheduler.py。

运行：
    python3 report.py [--json]

来源：
    docs/history/v2.0/glm-conductor-v2-upgrade-guide-final.md §36（凭证三模式）、
    §27（snapshot）、§28-§33（评估与恢复规划）、§37（凭证安全）、
    §43（quota 诊断面）+ v2 升级计划工作块 B5.5。
"""

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

# 接线插件根以复用 runtime（诊断脚本与 runtime/ 同插件；stop_gate 同款）
PLUGIN_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PLUGIN_ROOT))

from runtime.quota.bigmodel import BigModelQuotaProvider
from runtime.quota.credentials import (ENV_VAR, describe_modes,
                                       resolve_credential)
from runtime.quota.provider import QuotaProviderError
from runtime.quota.scheduler import evaluate, plan_resume
# v2.2.1 WU-221-C2（行为保持抽取）：本地 _parse_iso_utc 副本与
# runtime.quota.time_utils 的规范实现逐字相同（逐实现比对），改经
# 共享落点 import；_normalize_now / _is_number 为本模块专属变体
# （错误文案锚定 format_duration_delta / 数值域通用判别），原地保留。
from runtime.quota.time_utils import _parse_iso_utc
from runtime.quota.zai import ZaiQuotaProvider

# —— 探测顺序与展示词汇 ——

# provider 探测顺序（§43：zai 在前 bigmodel 在后，任一成功即用）
_PROVIDER_ORDER = (("zai", ZaiQuotaProvider), ("bigmodel", BigModelQuotaProvider))

# 标准化窗口 kind → 展示标题（§27：five_hour 在前 weekly 在后）
_WINDOW_LABELS = {"five_hour": "5-hour", "weekly": "weekly"}
_STANDARD_KINDS = ("five_hour", "weekly")

# 错误分类 → 中文说明（kind 词汇 = provider.ERROR_KINDS，不透传原始消息）
_ERROR_KIND_TEXT = {
    "auth": "凭证无效或未授权",
    "unavailable": "服务端不可用（非 2xx）",
    "malformed": "响应格式异常",
    "network": "网络/传输失败",
    "unknown": "未知错误",
}


# —— 测试注入点：模块级 provider 工厂 ——

def _build_providers(api_key):
    """按探测顺序构造 [(name, provider)]（测试 monkeypatch 本函数）。"""
    return [(name, cls(api_key)) for name, cls in _PROVIDER_ORDER]


# —— 探测 ——

def _probe(api_key):
    """逐 provider fetch(force=True)；任一成功即返回其 snapshot。

    返回 (snapshot, provider_name, failures)：全失败时
    (None, None, failures)，failures 每项 {"name", "error_kind"}
    （kind = QuotaProviderError.kind；契约外意外异常防御性归
    "unknown"——诊断面绝不因单个 provider 中断）。
    """
    failures = []
    for name, provider in _build_providers(api_key):
        try:
            snapshot = provider.fetch(force=True)
        except QuotaProviderError as exc:
            failures.append({"name": name, "error_kind": exc.kind})
        except Exception:  # 防御性兜底：非契约异常不中断诊断面
            failures.append({"name": name, "error_kind": "unknown"})
        else:
            return snapshot, name, failures
    return None, None, failures


# —— 相对时长格式化 ——

def _normalize_now(now):
    """归一参考时刻：None → 当前 UTC；datetime / ISO 串 → 时刻。"""
    if now is None:
        return datetime.now(timezone.utc)
    if isinstance(now, datetime):
        if now.tzinfo is None:
            return now.replace(tzinfo=timezone.utc)
        return now
    moment = _parse_iso_utc(now)
    if moment is None:
        raise ValueError(
            "format_duration_delta：now 必须是 datetime 或 ISO8601 "
            "字符串，得到 %r" % (now,))
    return moment


def format_duration_delta(iso_or_none, now=None):
    """reset 时刻 → 相对 now 的紧凑时长文本（§43 诊断面统一形式）。

    - 不可解析 / None → "unknown"；
    - 时刻在过去（含恰好现在）→ "due now"；
    - 未来：< 1 分钟 → "0m"；< 1 小时 → "45m"；< 1 天 → "1h 42m"；
      ≥ 1 天 → "3d 4h"（天 + 小时，分钟不显示）。
    now：测试注入参考时刻（datetime 或 ISO 串），缺省当前 UTC。
    """
    moment = _parse_iso_utc(iso_or_none)
    if moment is None:
        return "unknown"
    reference = _normalize_now(now)
    delta_seconds = (moment - reference).total_seconds()
    if delta_seconds <= 0:
        return "due now"
    total_minutes = int(delta_seconds // 60)
    if total_minutes < 1:
        return "0m"
    days, remainder = divmod(total_minutes, 1440)
    hours, minutes = divmod(remainder, 60)
    if days >= 1:
        return "%dd %dh" % (days, hours)
    if hours >= 1:
        return "%dh %dm" % (hours, minutes)
    return "%dm" % minutes


# —— 文本渲染（§43 风格，全中文/ASCII，零凭证） ——

def _is_number(value):
    """值是否为可信数值：int/float（bool 除外）且有限。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(value)


def _fmt_percent(value):
    """百分比数值 → 紧凑文本（82.0 → "82%"，12.34 → "12.34%"）；
    不可信输入 → "unknown"（不造数）。"""
    if not _is_number(value):
        return "unknown"
    value = round(float(value), 2)
    if value == int(value):
        return "%d%%" % int(value)
    return "%s%%" % value


def _window_lines(window):
    """单窗口 → ["  used: ..", "  remaining: ..", "  reset: .."]。"""
    used = window.get("used_percent")
    remaining = window.get("remaining_percent")
    if not _is_number(remaining) and _is_number(used):
        remaining = 100.0 - float(used)  # 快照缺 remaining 时推导
    return ["  used: %s" % _fmt_percent(used),
            "  remaining: %s" % _fmt_percent(remaining),
            "  reset: %s" % format_duration_delta(window.get("reset_at"))]


def _render_unavailable_text():
    """unavailable 文本：模式说明 + 配置指引两行（§36）。"""
    return "\n".join([
        "GLM CODING PLAN quota 诊断不可用（mode: unavailable）",
        "",
        "未找到可用的 GLM Coding Plan 凭证。配置指引：",
        "  1. 设置环境变量 %s（优先）；" % ENV_VAR,
        "  2. 或依赖已登录 ZCode 的 ~/.zcode/v2/config.json 中"
        " provider builtin:bigmodel-coding-plan 的 apiKey。",
        "",
        "配置后重新运行本诊断（只读，不校验凭证有效性之外的任何状态）。",
    ])


def _render_failure_text(source, failures):
    """全失败文本：每 provider 一行 kind + 中文说明（零凭证/零响应体）。"""
    lines = ["GLM CODING PLAN quota 探测失败 (source: %s)" % source, ""]
    for item in failures:
        kind = item["error_kind"]
        lines.append("  %s: %s — %s" % (item["name"], kind,
                                        _ERROR_KIND_TEXT.get(kind, "未知错误")))
    lines.append("")
    lines.append("（只读诊断：以上不含凭证与响应体；"
                 "可稍后重试，或检查 %s 与 provider 配置）" % ENV_VAR)
    return "\n".join(lines)


def _render_success_text(snapshot, evaluation, plan, source):
    """成功文本：窗口明细 + status + plan（§43 风格）。"""
    provider = snapshot.get("provider")
    if not isinstance(provider, str) or provider == "":
        provider = "unknown"
    lines = ["GLM CODING PLAN (provider: %s, source: %s)" % (provider, source),
             ""]
    by_kind = {}
    for window in evaluation.get("windows") or []:
        if isinstance(window, dict) and window.get("kind"):
            by_kind.setdefault(window["kind"], window)
    # 标准两窗：有则整节，无则 "not present" 行（§26 lite 套餐无 weekly）
    for kind in _STANDARD_KINDS:
        window = by_kind.get(kind)
        label = _WINDOW_LABELS[kind]
        if window is None:
            lines.append("%s: not present" % label)
            lines.append("")
            continue
        lines.append("%s:" % label)
        lines.extend(_window_lines(window))
        lines.append("")
    # 防御：评估结果里出现标准两类之外的窗口时原样补显（不丢信息）
    for kind, window in by_kind.items():
        if kind in _STANDARD_KINDS:
            continue
        lines.append("%s:" % kind)
        lines.extend(_window_lines(window))
        lines.append("")
    lines.append("status: %s — %s" % (evaluation.get("status"),
                                      evaluation.get("reason")))
    lines.append("plan: %s（%s）" % (plan.get("action"), plan.get("reason")))
    return "\n".join(lines)


# —— CLI 主入口 ——

def _force_utf8_stdout():
    """把 stdout 调到 UTF-8（§43 输出纪律；非 TTY/旧环境容错跳过）。"""
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError, OSError):
        pass  # 测试捕获（StringIO）或已被重定向：保持原样


def main(argv=None):
    """诊断入口：三态 ×（文本 / --json）双输出，退出码恒 0。"""
    _force_utf8_stdout()
    parser = argparse.ArgumentParser(
        prog="report.py",
        description="GLM Coding Plan 额度只读诊断（§43；退出码恒 0）")
    parser.add_argument("--json", action="store_true",
                        help="输出机器可读 JSON（全非秘密）")
    args = parser.parse_args(argv)

    try:
        source, api_key = resolve_credential()
        if api_key is None:
            # —— 态 1：unavailable ——
            if args.json:
                payload = {"mode": "unavailable", "source": None, "ok": False,
                           "env_var": ENV_VAR, "modes": describe_modes(),
                           "hint": ["设置环境变量 %s（优先）" % ENV_VAR,
                                    "或依赖已登录 ZCode 的 ~/.zcode/v2/"
                                    "config.json 中 provider "
                                    "builtin:bigmodel-coding-plan 的 apiKey"]}
                print(json.dumps(payload, ensure_ascii=False, indent=2,
                                 sort_keys=True))
            else:
                print(_render_unavailable_text())
            return 0

        snapshot, provider_name, failures = _probe(api_key)
        if snapshot is None:
            # —— 态 2：全失败（只报 provider 名 + kind，零凭证） ——
            if args.json:
                payload = {"mode": "provider-api", "source": source,
                           "ok": False, "providers": failures}
                print(json.dumps(payload, ensure_ascii=False, indent=2,
                                 sort_keys=True))
            else:
                print(_render_failure_text(source, failures))
            return 0

        # —— 态 3：成功 ——
        evaluation = evaluate(snapshot)
        plan = plan_resume(evaluation, snapshot)
        if args.json:
            payload = {"mode": "provider-api", "source": source,
                       "provider": provider_name, "ok": True,
                       "snapshot": snapshot, "evaluation": evaluation,
                       "plan": plan}
            print(json.dumps(payload, ensure_ascii=False, indent=2,
                             sort_keys=True))
        else:
            print(_render_success_text(snapshot, evaluation, plan, source))
        return 0
    except Exception as exc:  # 防御性兜底：诊断面绝非闸门，退出码恒 0
        # 只回显异常类型名，不回显异常文本（防御性排除潜在的凭证携带）
        if args.json:
            print(json.dumps({"mode": "provider-api", "ok": False,
                              "error_kind": "unknown",
                              "error_type": type(exc).__name__},
                             ensure_ascii=False, indent=2, sort_keys=True))
        else:
            print("quota 诊断内部错误（%s）：已按只读诊断容错返回，"
                  "不含任何凭证" % type(exc).__name__)
        return 0


if __name__ == "__main__":
    sys.exit(main())
