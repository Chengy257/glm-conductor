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
    零第三方依赖。风格对齐 runtime/quota/parser.py。

运行：
    python3 report.py [--json]

来源：
    v2.0 设计（已蒸馏入 docs/architecture.md：凭证三模式 / snapshot /
    评估与恢复规划 / 凭证安全 / quota 诊断面）+ v2 升级计划工作块 B5.5。
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
# v2.4 Phase 3 P3-A：evaluate / plan_resume（§28 / §29-§33）的规范
# 落点为 parser（自 scheduler.py 行为保持移入，scheduler 已删除）。
from runtime.quota.parser import evaluate, plan_resume
from runtime.quota.provider import QuotaProviderError
# v2.2.1 WU-221-C2（行为保持抽取）：本地 _parse_iso_utc 副本与
# runtime.quota.time_utils 的规范实现逐字相同（逐实现比对），改经
# 共享落点 import；_normalize_now 为本模块专属变体（错误文案锚定
# format_duration_delta），原地保留；_is_number 与 parser.evaluate
# 的实现逐字相同（真副本，非专属变体），作为接缝外的数值域通用
# 守卫原地保留。
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


def _fmt_number(value):
    """数值 → 紧凑文本（300.0 → "300"，12.34 → "12.34"）；中文 reason
    渲染专用，与 parser._fmt_number 同口径的接缝外副本（保留理由同
    _is_number：渲染器独立于 parser 的英文 reason 面）。"""
    value = round(float(value), 2)
    if value == int(value):
        return str(int(value))
    return str(value)


# —— 中文 reason 渲染端口（v2.6.1 W5b 解耦） ——
# parser.evaluate / plan_resume 的 reason 面已英文化（模型执行面 =
# 简洁英文）；本诊断 CLI 的人读 TEXT 面保持中文（owner ruling 9，
# tests/test_quota_report.py 与 docs/troubleshooting.md 锚定）。因此
# status / plan 两行不再直印 evaluation['reason'] / plan['reason']，
# 改由下方两个渲染器按结构化字段（status / windows / action /
# resume_at）重建中文 reason——口径与 parser 英文化前的中文文案逐字
# 一致；渲染器不识别的形状一律原样透出英文 reason（诚实降级，绝不
# 虚构中文文案）。--json 机器面则继续内嵌 parser 原产 reason（英文）。

def _kind_names_zh(windows):
    """窗口 kind 的中文顿号清单（如 "five_hour、weekly"；空清单回退
    "窗口"）——parser._kind_names 英文化前的中文口径。"""
    names = [str(w.get("kind")) for w in windows
             if isinstance(w, dict) and w.get("kind")]
    return "、".join(names) if names else "窗口"


def _status_reason_zh(evaluation):
    """由 evaluation 的结构化字段重建中文 status reason（parser 英文化
    前 _overall_reason 的中文口径逐字移植；windows 缺失 / 为空 →
    「无可用窗口数据，状态未知」——对应 evaluate() 的 no-window 形态）。"""
    windows = evaluation.get("windows") if isinstance(evaluation, dict) \
        else None
    if not isinstance(windows, list) or not windows:
        return "无可用窗口数据，状态未知"
    status = evaluation.get("status")
    exhausted = [w for w in windows if w["status"] == "EXHAUSTED"]
    pressure = [w for w in windows if w["status"] == "PRESSURE"]
    unknown = [w for w in windows if w["status"] == "UNKNOWN"]
    parts = []
    if exhausted:
        parts.append("%s 窗口已耗尽" % _kind_names_zh(exhausted))
    if pressure:
        segments = []
        for w in pressure:
            segment = "%s 接近阈值" % (
                w["kind"] if w.get("kind") else "窗口")
            if _is_number(w["remaining_percent"]):
                segment += "（剩余 %s%%）" % _fmt_number(
                    w["remaining_percent"])
            segments.append(segment)
        parts.append("，".join(segments))
    if status == "EXHAUSTED":
        return "，".join(parts)
    if status == "PRESSURE":
        return parts[0] if parts else "额度接近阈值"
    if status == "UNKNOWN":
        if unknown:
            return "窗口数据不完整（%s 已用/剩余数据缺失）" % _kind_names_zh(
                unknown)
        return "窗口数据不完整"
    return "窗口余量充足"


def _plan_reason_zh(evaluation, plan):
    """由 plan['action'] 重建中文 plan reason（parser 英文化前
    plan_resume 的中文口径移植；按 action 分派）。

    - 'continue' / 'checkpoint' / UNKNOWN 降级：固定文案；
    - EXHAUSTED 降级：按 evaluation['windows'] 的 EXHAUSTED 窗重建
      parser 的分派文案（无阻塞窗 → 总括文案；个别 reset 不可解析 →
      逐窗文案，kind 缺失回退「阻塞窗口」）；
    - 'resume_at'：grace 秒数由 resume_at − 最晚阻塞窗 reset 推导
      （parser 的 resume_at 恰为后者加 grace，故推导可还原）；
    - 其余 action / 形状（含推导不可行）：原样透出英文
      plan['reason']——诚实降级，绝不虚构中文文案。"""
    if not isinstance(plan, dict):
        return plan
    action = plan.get("action")
    if action == "continue":
        return "额度余量充足，继续当前工作"
    if action == "checkpoint":
        return ("额度接近阈值：当前原子步骤收尾后在安全里程碑落"
                " checkpoint，不开新的大实施阶段")
    if action == "periodic_fallback":
        status = evaluation.get("status") if isinstance(evaluation, dict) \
            else None
        if status == "UNKNOWN":
            return "额度状态未知（fail-open）：回退周期存活探针，不阻塞任务"
        if status == "EXHAUSTED":
            windows = evaluation.get("windows")
            blocked = [w for w in windows
                       if isinstance(w, dict)
                       and w.get("status") == "EXHAUSTED"] \
                if isinstance(windows, list) else []
            if not blocked:
                return ("窗口已耗尽但无法确定任何阻塞窗口的 reset 时间，"
                        "不虚构调度：回退周期存活探针")
            for window in blocked:
                if _parse_iso_utc(window.get("reset_at")) is None:
                    kind = window.get("kind")
                    label = kind if kind else "阻塞窗口"
                    return ("%s reset 时间未知，不虚构调度：回退周期存活探针"
                            % label)
            # 全部 reset 可解析却给出 periodic_fallback：渲染器不识别
            # 的形状 → 英文 reason 原样透出
            return plan.get("reason")
        return plan.get("reason")
    if action == "resume_at":
        resume_at = plan.get("resume_at")
        end = _parse_iso_utc(resume_at)
        windows = evaluation.get("windows") if isinstance(evaluation, dict) \
            else None
        blocked = [w for w in windows
                   if isinstance(w, dict) and w.get("status") == "EXHAUSTED"] \
            if isinstance(windows, list) else []
        moments = []
        derivable = end is not None and bool(blocked)
        for window in blocked:
            moment = _parse_iso_utc(window.get("reset_at"))
            if moment is None:
                derivable = False
                break
            moments.append(moment)
        if derivable:
            grace_seconds = (end - max(moments)).total_seconds()
            return ("窗口已耗尽：等到 %s 之后再恢复（最晚 reset 加 %s 秒"
                    "宽限），唤醒后必须强制刷新额度"
                    % (resume_at, _fmt_number(grace_seconds)))
        # 推导不可行（resume_at / reset 不可解析等）：英文 reason 透出
        return plan.get("reason")
    return plan.get("reason")


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
    # 中文 reason 经渲染端口重建（parser 原产 reason 已英文化；
    # 人读 TEXT 面保持中文，owner ruling 9）——两行格式逐字不变
    lines.append("status: %s — %s" % (evaluation.get("status"),
                                      _status_reason_zh(evaluation)))
    lines.append("plan: %s（%s）" % (plan.get("action"),
                                     _plan_reason_zh(evaluation, plan)))
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
