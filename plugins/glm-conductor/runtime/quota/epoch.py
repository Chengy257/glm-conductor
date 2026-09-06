#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GLM Conductor v2.2 quota epoch / boundary 纯模型层（v2.2 修正计划 C2，wu-22-C2）。

职责（修正计划 §10/§10.2）：
    消费 §27 snapshot 的 windows 列表（+ provider 四态），产出三类
    纯值，供 C1b（Resume Controller 的 resume-time consumption）与
    C3（Quota Watcher / 控制面）消费：
      - epoch 身份：canonical_windows / epoch_fingerprint / epoch_id /
        same_epoch——窗口多重集的确定性指纹；
      - QuotaIdentity 双形态比较：quota_identity_matches（v2.2.1
        WU-221-B2）——(provider_identity_hash, epoch_id) 复合身份的
        统一判定（记录侧指纹缺席 = legacy = 信任），供六个记账面
        （订阅 / 激活 / 消费 / primer / 观察者 / 恢复对账）共用；
      - probe_boundary_at(windows)：probe / observation boundary——
        Watcher 何时收紧 polling、何时提前进入 boundary surveillance；
      - executable_boundary_at(windows)：executable boundary——是否
        允许 Task Resume、quota-window budget consumption；
      - evaluate_epoch(...)：上述三者的单次快照折算（§10.1 六键形状）。

§10 核心语义（窗口语义核心的替换）：
    真正有意义的事件不再是 "Scheduled Task fired"，而是
    "a new executable quota epoch became available"。
    Automation Fire != Quota Epoch（四边界公理之一）：定时器触发只是
    观察机会，本身不构成新 epoch；epoch 只由窗口身份推进。

epoch 身份（冻结口径）：
    排序后的 (kind, reset_at|"unknown") 多重集——不含 status /
    percent。provider 状态翻转不推进 epoch：同一窗口耗尽/恢复是消费
    状态变化，不是新 epoch；窗口滚动 = reset_at 变化 = 新 epoch；
    reset 不可知的窗口以字面 "unknown" 参与身份（同 kind 双 unknown
    快照仍是同一 epoch）。

输入契约（v2.2 C1b 注记，C2 reviewer P2；仅注记不改行为）：
    输入 windows 为 §27 snapshot 的 windows 列表：逐窗 dict，语义字段
    kind / status / reset_at（used_percent / remaining_percent 等其余
    字段允许存在，但不参与任何输出——身份与边界只读上述三字段）；
    非 list 按空列表处理（fail-open），逐窗非 dict 条目跳过。
    部分可解析 reset_at 的语义：解析以「窗」为单位——可解析窗归一为
    Z 形式串参与一切计算；缺失 / 不可解析窗归一为 None（身份序列中
    记字面 "unknown"）：
      - epoch 身份：unknown 窗完整参与（unknown 是身份占位符，同
        kind 双 unknown 仍是同一 epoch；混合清单中 unknown 窗的存在
        本身改变多重集、即改变 epoch_id）；
      - probe / executable boundary：unknown 窗被排除（不虚构时刻，
        §31）——probe = 其余可解析窗的 min(reset)+grace；executable =
        阻塞 EXHAUSTED 窗中可解析者的 max(reset)+grace。混合清单里
        只有可解析子集参与边界数学；阻塞窗 reset 全不可解析 →
        executable None（此时 probe 仍可由非阻塞可解析窗给出）。

双 boundary 分工（§10.2，2026-09-02 冻结；禁止再混成一个 wake_at）：
    - probe boundary = min(全部可解析 reset_at) + grace——只服务观察
      收紧 / surveillance；Resume Controller 不得把它当 executable
      boundary 消费；
    - executable boundary = max(阻塞 EXHAUSTED 窗的可解析 reset_at)
      + grace（§30 最晚多窗语义，与 probe 的 min 相反——这正是
      §10.2 拆分的意义）；无阻塞窗 → None；有阻塞窗但 reset 全
      不可解析 → None（§31 绝不虚构）。

纪律（本模块的硬边界）：
    - 纯函数：零 I/O、零网络、零时间依赖（不调用 now()——全部
      boundary 是由 reset_at + grace 静态折算的绝对时刻）；
    - 单一真相源：reset 解析/格式化复用 runtime.quota.scheduler 的
      _parse_iso_utc / _format_iso_z，probe 数学复用
      runtime.quota.control 的 _earliest_reset_plus，
      DEFAULT_GRACE_SECONDS 从 scheduler 导入；不复制解析与数学；
    - fail-open：windows 非 list → 按空列表处理（与 control 一致）；
      逐窗非 dict 跳过；不虚构任何时刻；
    - 无 CLI、无 journal、无 state 写入：消费接线归 C1b/C5，控制面
      事件与 quota-control.json 归 C3（本模块只提供纯函数）。

依赖：
    仅 Python 3 标准库（hashlib / datetime）+ runtime.quota.scheduler
    / runtime.quota.control / runtime.quota.parser；导入方向
    epoch → control → execution_policy → parser（均不反向依赖，
    无循环导入）。跨模块私有 import 是 quota 包既定惯例
    （control 亦 import scheduler 的 _format_iso_z/_parse_iso_utc）。

来源：
    docs/GLM-Conductor-v2.2-Quota-Control-Plane-Architecture-Correction-
    and-Agent-Implementation-Plan.md §10（Quota Epoch 替代 automation
    fire）/ §10.1（epoch_id 形状）/ §10.2（probe vs executable 双
    boundary，2026-09-02 冻结）/ §30（max(reset)+grace）/ §31（绝不
    虚构 reset）+ 修正计划工作单元 wu-22-C2。
"""

import hashlib
from datetime import timedelta

from runtime.quota.control import _earliest_reset_plus
from runtime.quota.parser import QUOTA_STATUSES
from runtime.quota.scheduler import (
    DEFAULT_GRACE_SECONDS,
    _format_iso_z,
    _is_number,
    _parse_iso_utc,
)

# —— 词汇表常量 ——

# §10.1 示例形状 "glm:<fingerprint>" 的身份前缀
EPOCH_ID_PREFIX = "glm:"
# epoch_id 取指纹前缀的十六进制字符数（16）
_EPOCH_ID_HEX_CHARS = 16
# 身份序列中「reset 未知」的字面占位（非空串，与 kind 的空串区分）
_UNKNOWN_RESET = "unknown"


# —— 内部小助手（纯函数） ——

def _validated_grace(grace_seconds, func_name):
    """probe / executable / evaluate 共用的 grace_seconds 校验。

    有限数值且 >= 0（bool 拒绝，风格照 scheduler.plan_resume /
    control），合法 → timedelta；否则中文 ValueError。
    """
    if not _is_number(grace_seconds) or grace_seconds < 0:
        raise ValueError(
            "%s：grace_seconds 必须是 >= 0 的有限数值，得到 %r"
            % (func_name, grace_seconds))
    return timedelta(seconds=float(grace_seconds))


def _executable_boundary_of_canonical(canonical, grace):
    """canonical 窗口中阻塞 EXHAUSTED 窗的 executable boundary。

    max(可解析 reset_at) + grace 的 Z 形式串（§30）；无阻塞窗或
    reset 全不可解析 → None（§31 不虚构）。probe 用 min 走
    control._earliest_reset_plus，此处用 max——两者不可混用（§10.2）。
    """
    moments = []
    for window in canonical:
        if window["status"] != "EXHAUSTED":
            continue
        moment = _parse_iso_utc(window["reset_at"])
        if moment is not None:
            moments.append(moment)
    if not moments:
        return None
    return _format_iso_z(max(moments) + grace)


def _fingerprint_of_canonical(canonical):
    """canonical 窗口序列 → sha256 hexdigest（身份序列见 _identity_lines）。"""
    return hashlib.sha256(_identity_lines(canonical).encode("utf-8")).hexdigest()


def _identity_lines(canonical):
    """canonical 窗口 → 身份序列文本：每窗一行 "kind\\treset_at\\n"。

    reset_at None → 字面 "unknown"；kind None → 空串。身份只含
    kind 与 reset_at（冻结口径：不含 status / percent），序列按
    canonical 的确定性排序拼接（与输入顺序无关）。
    """
    lines = []
    for window in canonical:
        kind = window["kind"] or ""
        reset = (window["reset_at"] if window["reset_at"] is not None
                 else _UNKNOWN_RESET)
        lines.append("%s\t%s\n" % (kind, reset))
    return "".join(lines)


# —— epoch 身份（§10 冻结口径） ——

def canonical_windows(windows):
    """把 §27 windows 列表归一为 epoch 身份视角的 canonical 窗口列表。

    参数：
      - windows：§27 snapshot 的 windows 列表；非 list → 按空列表
        处理（fail-open，与 control 一致）；逐窗非 dict 跳过。

    归一规则：
      - 每窗归一为 {"kind": str|None, "status": str|None,
        "reset_at": str|None}；kind / status 原样透传（非 str →
        None）；reset_at 经 scheduler._parse_iso_utc 解析成功 →
        _format_iso_z 归一为 Z 形式串；缺失 / 不可解析 → None；
      - 输出按 (kind or "", reset_at or "") 确定性排序——多重集
        身份与输入顺序无关。

    返回：全新 list（不波及入参），逐项为上述三键 dict。
    """
    if not isinstance(windows, list):
        return []
    canonical = []
    for item in windows:
        if not isinstance(item, dict):
            continue  # 条目级容错：非 dict 窗跳过，不影响其余条目
        kind = item.get("kind")
        status = item.get("status")
        canonical.append({
            "kind": kind if isinstance(kind, str) else None,
            "status": status if isinstance(status, str) else None,
            "reset_at": _normalize_reset(item.get("reset_at")),
        })
    canonical.sort(key=lambda window: (window["kind"] or "",
                                       window["reset_at"] or ""))
    return canonical


def _normalize_reset(reset_at):
    """reset_at 原始值 → Z 形式串；缺失 / 不可解析 → None（不虚构）。

    解析与格式化完全复用 scheduler 的 _parse_iso_utc / _format_iso_z。
    """
    moment = _parse_iso_utc(reset_at)
    if moment is None:
        return None
    return _format_iso_z(moment)


def epoch_fingerprint(windows):
    """canonical 窗口身份序列的 sha256 hexdigest（顺序无关、确定性）。

    序列由 canonical_windows 的排序结果逐窗拼接 "kind\\treset_at\\n"
    （reset_at None → "unknown"，kind None → 空串，见 _identity_lines）。
    """
    return _fingerprint_of_canonical(canonical_windows(windows))


def epoch_id(windows):
    """窗口多重集的 epoch 身份："glm:" + epoch_fingerprint(windows)[:16]。

    与输入顺序无关；status / percent 不参与（§10 冻结口径）。
    """
    return EPOCH_ID_PREFIX + epoch_fingerprint(windows)[:_EPOCH_ID_HEX_CHARS]


def same_epoch(windows_a, windows_b):
    """两份窗口快照是否属于同一 quota epoch（epoch_id 相等）。"""
    return epoch_id(windows_a) == epoch_id(windows_b)


# —— QuotaIdentity（v2.2.1 WU-221-B2） ——

def quota_identity_matches(record_epoch_id, record_identity_hash,
                           current_epoch_id, current_identity_hash):
    """QuotaIdentity = (provider_identity_hash, epoch_id) 的双形态比较
    （v2.2.1 WU-221-B2 的共享判定落点；纯函数零 I/O）。

    记账面（订阅注册 / 激活 / 消费 / primer）把「某 epoch 的既有记录
    是否属于当前 provider 身份」的比较统一为本判定——epoch_id 等值
    AND（记录侧指纹缺席 OR 指纹相等）：

      - record_epoch_id != current_epoch_id → False（epoch 不同，与
        身份无关——epoch_id 格式与推导零变化，仍是 §10.1 冻结口径）；
      - epoch_id 等值且 record_identity_hash 为 None（记录未携带指纹
        ——v2.2 legacy 记录的统一形态）→ True（缺席 = legacy = 保守
        信任——v2.2 记录保持可读可用，这是本单元的兼容性裁决）；
      - epoch_id 等值且指纹相等（同一 provider 身份）→ True；
      - epoch_id 等值但指纹不同（异身份同名 epoch）→ False（异身份
        记录按各消费面既有的「无先前记录」路径处理——不授权、不幂等
        拦截、不消费）。

    指纹口径即 runtime.quota.identity.compute_provider_identity_hash
    的非秘密 16 位十六进制（sha256("来源:凭证") 前缀截断，不含也
    不可还原凭证材料，§37）；本函数只做字符串比较，绝不接触凭证。

    参数：
      - record_epoch_id：既有记录的 epoch_id（§10.1 形状；None = 无
        先前记录，与任何 current_epoch_id 不等 → False）；
      - record_identity_hash：既有记录的可选 provider_identity_hash
        （None = legacy 缺席形态）；
      - current_epoch_id：当前 epoch_id（§10.1 形状）；
      - current_identity_hash：当前 provider 身份指纹（16-hex）。
    """
    if record_epoch_id != current_epoch_id:
        return False
    if record_identity_hash is None:
        return True  # legacy 缺席形态：保守信任（v2.2 兼容性裁决）
    return record_identity_hash == current_identity_hash


# —— 双 boundary（§10.2） ——

def probe_boundary_at(windows, *, grace_seconds=DEFAULT_GRACE_SECONDS):
    """probe / observation boundary：min(全部可解析 reset_at) + grace。

    全部窗（不限状态）中最早可解析 reset_at 加 grace_seconds 的 Z
    形式串；无可解析 → None。数学复用 control._earliest_reset_plus
    （§10.2 分工细则：该函数继续服务 probe boundary，但 Resume
    Controller 不得把它当 executable boundary 消费）。
    """
    grace = _validated_grace(grace_seconds, "probe_boundary_at")
    return _earliest_reset_plus(canonical_windows(windows), grace)


def blocking_windows(windows):
    """阻塞窗 = canonical 结果中 status == "EXHAUSTED" 的子集（保持排序）。"""
    return [window for window in canonical_windows(windows)
            if window["status"] == "EXHAUSTED"]


def executable_boundary_at(windows, *, grace_seconds=DEFAULT_GRACE_SECONDS):
    """executable boundary：max(阻塞 EXHAUSTED 窗可解析 reset_at) + grace。

    阻塞窗中最晚可解析 reset_at 加 grace_seconds 的 Z 形式串
    （§30 最晚多窗语义，与 probe 的 min 相反）；无阻塞窗 → None；
    有阻塞窗但 reset_at 全不可解析 → None（§31 绝不虚构）。
    """
    grace = _validated_grace(grace_seconds, "executable_boundary_at")
    return _executable_boundary_of_canonical(blocking_windows(windows), grace)


# —— §10.1 单次快照折算 ——

def evaluate_epoch(*, provider_status, windows,
                   grace_seconds=DEFAULT_GRACE_SECONDS):
    """把 provider 四态 + windows 折算为 §10.1 形状的 epoch 快照。

    参数（全 keyword-only）：
      - provider_status：provider 维度四态（parser.QUOTA_STATUSES，
        通常取 scheduler.evaluate(snapshot)["status"]）；非法值
        ValueError（中文，附取值清单）；
      - windows：§27 snapshot 的 windows 列表；非 list → 空列表
        处理；逐窗非 dict 跳过（见 canonical_windows）；
      - grace_seconds：boundary 宽限秒数，缺省 DEFAULT_GRACE_SECONDS
        （300）；非有限数值 / 负数 → ValueError（中文；bool 拒绝）。

    executable（消费资格，供 C1b 消费）：
        provider_status == "AVAILABLE" 且无阻塞 EXHAUSTED 窗。

    返回（冻结 6 键，键名逐字）：
      {"epoch_id": "glm:" + 指纹前 16 位,
       "provider_status": <入参回显>,
       "executable": <bool>,
       "probe_boundary_at": <Z 形式串或 None>,
       "executable_boundary_at": <Z 形式串或 None>,
       "windows": <canonical_windows 结果>}
    """
    if provider_status not in QUOTA_STATUSES:
        raise ValueError(
            "evaluate_epoch：provider_status %r 不在合法取值内（%s）"
            % (provider_status, ", ".join(QUOTA_STATUSES)))
    grace = _validated_grace(grace_seconds, "evaluate_epoch")
    canonical = canonical_windows(windows)
    blocked = [window for window in canonical
               if window["status"] == "EXHAUSTED"]
    return {
        "epoch_id": (EPOCH_ID_PREFIX
                     + _fingerprint_of_canonical(canonical)[
                         :_EPOCH_ID_HEX_CHARS]),
        "provider_status": provider_status,
        "executable": (provider_status == "AVAILABLE") and not blocked,
        "probe_boundary_at": _earliest_reset_plus(canonical, grace),
        "executable_boundary_at": _executable_boundary_of_canonical(
            blocked, grace),
        "windows": canonical,
    }
