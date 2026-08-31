#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GLM Conductor v2.1 派发许可层（dispatch permit，v2.1 M2 前半，计划 §6.3/§6.6）。

职责：
    「无 permit 即 deny」的 PreToolUse 派发门（wu-21-03 实施 hook 侧）的
    数据层：为每次真实派发意图签发一张落盘 permit，使「谁被批准派发
    哪个单元、以什么模式、到什么时候为止」从内存约定升级为可机械校验
    的落盘事实。本模块提供 permit 生命周期原语：
      - create_permit：签发（校验 + 落盘 + 返回 permit dict）；
      - load_permit / validate_permit：读取与门校验（存在 / 未消费 /
        未过期 / 任务匹配 / 单元匹配 / wave 匹配 / mode 匹配）；
      - consume_permit：消费（原子 rename，防重放）；
      - invalidate_permit：失效（abort 回退时作废未消费 permit）；
      - list_permits：任务活跃 permit 清单（审计面）；
      - marker_for / parse_marker：机器可验证 marker 的构造与解析
        （计划 §6.4，marker 格式冻结：GLM_CONDUCTOR_DISPATCH=<permit_id>）；
      - new_wave_id：wave 标识生成（"wave-" + 12 hex，v2.1 M4 wu-21-08）；
      - validate_wave_membership：wave permit 的成员资格校验（permit
        须指向 active wave 且 unit 仍在成员清单内；wu-21-08，hook
        PreToolUse 门在 validate_permit 通过后追加调用）。

存储（计划 §6.3 ※DR R3 锁定，照抄 leases 模式）：
    <repo_root>/.glm-conductor/tasks/<task-id>/permits/<permit_id>.json
    ——每 permit 一文件、文件名 = permit_id、任务目录内独立 permits/
    子目录（与 state.json / leases.json 同任务目录、同卷，rename 原子
    性成立）。permit dict 字段冻结（§6.3）：
      {"permit_id": "dp-" + 12 hex（os.urandom）,
       "task_id": str, "unit_id": str, "wave_id": str | None,
       "mode": "background" | "foreground",
       "reason": foreground 必填且 ∈ FOREGROUND_REASONS，background 恒 null,
       "created_at" / "expires_at": ISO-8601 UTC 毫秒,
       "consumed": bool}
    - 创建：同目录 <name>.json.tmp 先写（UTF-8、ensure_ascii=False、
      缩进 2、sort_keys）再 os.replace——崩溃时读者要么看到旧文件要么
      看到新文件，绝不看到半截 JSON（模式照抄 lease._save_lease_state）；
    - 消费 = os.rename 为 <permit_id>.consumed.json（保留审计轨迹）；
    - 失效 = os.rename 为 <permit_id>.invalidated.json（同上）；
    - load / list 时 consumed / invalidated 文件视同不存在——
      「不存在」与「已消费」在门校验里同义（fail-closed deny），而
      消费历史仍可从改名后的文件审计；
    - 防重放：消费即改名——同一 permit_id 第二次 consume 因原文件已
      不在而返回 False，无需文件锁；hook 进程与主会话双写者竞态被
      原子 rename 的「至多一方成功」性质消除（R3 锁定的动机本身）；
    - 损坏 / 非 dict 的 permit 文件一律按不存在处理（返回 None /
      跳过）：门校验宁可 deny 也不让脏文件放行，也绝不因单个坏文件
      炸掉 list / hook 路径。

TTL / 崩溃恢复：
    - TTL：DEFAULT_TTL_SECONDS = 1800 秒（与租约层 LEASE_DEFAULT_TTL
      同一保守口径——覆盖单单元一次有界实施并留足余量）；过期判定
      expires_at <= now 即过期（RB-21-04 用户锁定决策：恰好到达过期
      时刻即失效，严格大于才有效）；created_at / expires_at 解析失败
      按 "permit malformed" fail-closed deny——authorization permit
      与 lease 的口径差异：lease 的损坏时间戳按未过期保守处理是
      「绝不因坏数据自动释放」（活性安全侧），而 permit 是授权凭证，
      「无法证明有效即拒绝」（授权安全侧），二者方向相反、各自按语义
      锁定（RB-21-04）；过期 permit 不被删除——validate 拒绝（
      "permit expired"）、list 仍可见，人工审计后可清理；
    - 单写者前提（与 leases 相同）：主会话是唯一编排者（星型拓扑），
      多会话并行操作同一任务目录不在支持面内；rename 消费是唯一例外
      ——wu-21-03 的 hook 进程会并发 consume，原子 rename 保证至多
      一方成功，其余写入仍限主会话；
    - 崩溃窗口：创建崩在 os.replace 前 → 无 permit 文件（hook deny，
      派发被挡，安全侧）；消费崩在 rename 前 → permit 仍活跃（可重放
      消费一次，幂等）；崩在 rename 后 → 已消费态（重放 deny）——
      两个方向都收敛到确定语义，无需恢复程序。

分层关系（分工锚定，照 lease 原语层不写 journal 的先例）：
    本模块是纯原语层：零 journal 写入、零 state 写入、模块顶层不 import
    runtime.state / runtime.journal（journal 事件 dispatch_permit_created /
    dispatch_permit_invalidated 与 mode 策略读取由 task_manager 接线层
    负责）；hook 侧（wu-21-03）只消费 validate / consume 只读面。
    wu-21-08 的 validate_wave_membership 是唯一例外：它只读 state.json
    （惰性 import runtime.state，函数内 import，模块顶层依赖面不变），
    供 hook 做 wave 成员资格校验；本模块自身仍零 state 写入、零 journal。

依赖：
    仅 Python 3 标准库（datetime / json / os / pathlib），零第三方，
    `python3 -S` 可运行。

来源：
    docs/GLM-Conductor-v2.1-Architecture-Agent-Implementation-Plan.md
    §6.3（Dispatch Permit + ※DR R3 存储锁定）/ §6.4（marker 绑定）/
    §6.6（foreground override + reason 词汇）/ §20.2（回归清单数据层
    全集）+ docs/GLM-Conductor-v2.1-实施前缺口探查与设计决策记录.md
    D1 / D6 / R3。
"""

import datetime
import json
import os
import pathlib

# —— 词汇表 / 常量（§6.3 冻结） ——

# 任务目录下独立 permits/ 子目录（与 state.json / leases.json 同任务目录）
PERMIT_DIRNAME = "permits"

# permit_id 形状（冻结）："dp-" + 12 hex（os.urandom）
PERMIT_ID_PREFIX = "dp-"
PERMIT_ID_HEX_CHARS = 12
_PERMIT_ID_RANDOM_BYTES = PERMIT_ID_HEX_CHARS // 2

# 执行模式（§3.1 / §6.6）：background 默认；foreground 须带 reason
PERMIT_MODES = ("background", "foreground")
DEFAULT_MODE = "background"

# §6.6 foreground permit 的原因词汇（冻结三值）
FOREGROUND_REASONS = ("synchronous_dependency", "decision_blocking",
                      "short_diagnostic")

# TTL 保守默认（与 lease.LEASE_DEFAULT_TTL_SECONDS 同口径）：1800 秒
# 覆盖单单元一次有界实施并留足余量；过期由 validate 自然拒绝兜底
DEFAULT_TTL_SECONDS = 1800

# 消费 / 失效的改名后缀（审计轨迹；load / list 视同不存在）
CONSUMED_SUFFIX = ".consumed.json"
INVALIDATED_SUFFIX = ".invalidated.json"

# 机器可验证 marker（§6.4 冻结格式；主会话在 Agent prompt/description
# 文本中携带，hook 按本前缀查找）
MARKER_PREFIX = "GLM_CONDUCTOR_DISPATCH="


# —— 路径定位 ——

def permits_dir(repo_root, task_id) -> pathlib.Path:
    """返回 permit 目录 <repo_root>/.glm-conductor/tasks/<task-id>/permits。

    与 state.json / leases.json 同任务目录（同卷，rename 原子性成立）；
    路径形态与 journal.journal_path 同口径（不 import state——本模块
    保持零 state 依赖）。
    """
    return (pathlib.Path(repo_root) / ".glm-conductor" / "tasks"
            / str(task_id) / PERMIT_DIRNAME)


def _permit_file(repo_root, task_id, permit_id) -> pathlib.Path:
    """返回活跃 permit 文件路径 .../permits/<permit_id>.json。"""
    return permits_dir(repo_root, task_id) / ("%s.json" % permit_id)


def _valid_permit_id(permit_id) -> bool:
    """permit_id 形状闸：必须是非空 str 且不含路径分隔符（marker 文本
    是不可信输入——防 "../x" 之类的路径逃逸；不合法按不存在处理，
    fail-closed deny，不抛）。"""
    if not isinstance(permit_id, str) or permit_id == "":
        return False
    if permit_id in (".", ".."):
        return False
    return not any(ch in permit_id for ch in ("/", "\\", "\x00"))


def _valid_permit_id_shape(permit_id) -> bool:
    """permit_id 冻结形状闸："dp-" + 12 hex（§6.3，与 _new_permit_id
    同一形状）。marker 是不可信输入——授权判定前先卡完整形状（仅含
    路径安全的宽口径闸归 _valid_permit_id / load 层兜底）；不合法按
    不存在处理（fail-closed deny，不抛）。"""
    if not isinstance(permit_id, str) \
            or not permit_id.startswith(PERMIT_ID_PREFIX):
        return False
    body = permit_id[len(PERMIT_ID_PREFIX):]
    return (len(body) == PERMIT_ID_HEX_CHARS
            and all(ch in "0123456789abcdef" for ch in body))


# —— 时间归一（口径照抄 lease._format_iso / _coerce_now / _parse_iso） ——

def _format_iso(moment) -> str:
    """aware datetime → ISO-8601 UTC 字符串（毫秒精度，
    2026-08-28T12:34:56.789Z 形态——本模块时间字段统一格式）。"""
    return (moment.strftime("%Y-%m-%dT%H:%M:%S")
            + ".%03dZ" % (moment.microsecond // 1000))


def _parse_iso(value):
    """ISO-8601 时间戳 → aware UTC datetime（"Z" 后缀容错；naive 视为
    UTC）。非字符串 / 空串 / 解析失败 → None——None 的语义归调用方：
    validate_permit 的授权闸把 None 按 "permit malformed" fail-closed
    拒绝（RB-21-04：损坏时间戳绝不当作有效放行），_coerce_now 对显式
    传入的非法 now 抛 ValueError（调用方错误不静默）。"""
    if not isinstance(value, str) or value == "":
        return None
    try:
        parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed


def _coerce_now(now):
    """过期判定的 now 归一为 aware UTC datetime：None → 当前 UTC；
    datetime 原样（naive 视为 UTC）；字符串按 ISO 解析（解析失败
    ValueError——显式传入的非法 now 属调用方错误，不静默）。"""
    if now is None:
        return datetime.datetime.now(datetime.timezone.utc)
    if isinstance(now, datetime.datetime):
        if now.tzinfo is None:
            return now.replace(tzinfo=datetime.timezone.utc)
        return now
    parsed = _parse_iso(now)
    if parsed is None:
        raise ValueError("now %r 不是可解析的 ISO-8601 时间" % (now,))
    return parsed


def _new_permit_id() -> str:
    """生成新 permit_id："dp-" + 12 hex（os.urandom，§6.3 冻结形状）。"""
    return PERMIT_ID_PREFIX + os.urandom(_PERMIT_ID_RANDOM_BYTES).hex()


# —— 校验（先全量后副作用：任一非法即抛，零写入） ——

def _validate_create_args(task_id, unit_id, mode, reason, ttl_seconds):
    """create_permit 入参全量校验：非法抛 ValueError（中文消息含字段
    名，§6.3 冻结不变量）。

    - task_id / unit_id：非空 str（路径定位与门校验依赖精确比较）；
    - mode：∈ PERMIT_MODES；
    - mode="foreground" → reason 必填且 ∈ FOREGROUND_REASONS（§6.6）；
      mode="background" → reason 恒 null（传了也拒，保字段不变量）；
    - ttl_seconds：> 0 数值（bool 是 int 子类但拒绝充当时长）。
    """
    if not isinstance(task_id, str) or task_id == "":
        raise ValueError(
            "create_permit：task_id 必须是非空字符串，得到 %r" % (task_id,))
    if not isinstance(unit_id, str) or unit_id == "":
        raise ValueError(
            "create_permit：unit_id 必须是非空字符串，得到 %r" % (unit_id,))
    if mode not in PERMIT_MODES:
        raise ValueError(
            "create_permit：mode %r 不在合法取值内（%s）"
            % (mode, ", ".join(PERMIT_MODES)))
    if mode == "foreground":
        if reason is None:
            raise ValueError(
                'create_permit：mode="foreground" 要求 reason 非空'
                "（foreground 派发必须记录原因，§6.6）")
        if reason not in FOREGROUND_REASONS:
            raise ValueError(
                "create_permit：reason %r 不在合法取值内（%s）"
                % (reason, ", ".join(FOREGROUND_REASONS)))
    elif reason is not None:
        raise ValueError(
            'create_permit：mode="background" 的 permit 不接受 reason'
            "（background 时 reason 恒为 null），得到 %r" % (reason,))
    if isinstance(ttl_seconds, bool) \
            or not isinstance(ttl_seconds, (int, float)) or ttl_seconds <= 0:
        raise ValueError(
            "create_permit：ttl_seconds 必须是 > 0 的秒数，得到 %r"
            % (ttl_seconds,))


# —— 原子写（模式照抄 lease._save_lease_state） ——

def _save_permit_file(repo_root, task_id, permit) -> pathlib.Path:
    """原子保存 permit 文件，返回最终路径。

    同目录 <name>.json.tmp 先写（UTF-8、ensure_ascii=False、缩进 2、
    sort_keys），再 os.replace 覆盖；任何失败路径清理 tmp，成功后确保
    tmp 不残留（R3：崩溃时读者要么看不到文件要么看到完整 permit）。
    """
    path = _permit_file(repo_root, task_id, permit["permit_id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.parent / (path.name + ".tmp")
    try:
        # newline="\n"：固定 \n 换行，避免 Windows 文本模式写出 \r\n
        with open(tmp_path, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(permit, fh, ensure_ascii=False, indent=2,
                      sort_keys=True)
        os.replace(tmp_path, path)
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink()
        raise
    # os.replace 成功后 tmp 已不存在；防御性兜底，确保 tmp 不残留
    if tmp_path.exists():
        tmp_path.unlink()
    return path


# —— 签发 ——

def create_permit(repo_root, task_id, unit_id, *, wave_id=None,
                  mode=DEFAULT_MODE, reason=None,
                  ttl_seconds=DEFAULT_TTL_SECONDS, now=None) -> dict:
    """签发一张派发 permit：校验 → 落盘（原子写）→ 返回 permit dict。

    流程：
      1. 入参全量校验（_validate_create_args；非法 ValueError 中文消息
         含字段名，失败零写入）；
      2. now 归一（缺省当前 UTC；datetime / ISO 字符串均可——测试与
         过期场景注入用）；
      3. 构造 §6.3 冻结形状 permit dict（permit_id = "dp-" + 12 hex
         随机；created_at = now；expires_at = now + ttl；consumed 恒
         以 False 落盘——消费态由文件改名表达，不走字段重写）；
      4. 原子落盘并返回。

    波次 / TTL 语义：wave_id 缺省 None（M4 wave 事务接线时传入）；
    ttl_seconds 缺省 DEFAULT_TTL_SECONDS，过期由 validate_permit 以
    "permit expired" 自然拒绝——不删文件（审计可见，list 仍列出）。
    """
    _validate_create_args(task_id, unit_id, mode, reason, ttl_seconds)
    now_dt = _coerce_now(now)
    permit = {
        "permit_id": _new_permit_id(),
        "task_id": task_id,
        "unit_id": unit_id,
        "wave_id": wave_id,
        "mode": mode,
        "reason": reason,
        "created_at": _format_iso(now_dt),
        "expires_at": _format_iso(
            now_dt + datetime.timedelta(seconds=ttl_seconds)),
        "consumed": False,
    }
    _save_permit_file(repo_root, task_id, permit)
    return permit


# —— 读 ——

def load_permit(repo_root, task_id, permit_id):
    """读取活跃 permit，返回 permit dict；不存在 / 已消费 / 已失效 /
    permit_id 形状非法 / 文件损坏 → None（fail-closed：门校验一律按
    「permit not found」deny，脏状态绝不放行）。

    consumed / invalidated 改名文件视同不存在——原文件名处已无文件，
    这是 rename 防重放的直接推论；消费历史从改名后文件审计。
    """
    if not _valid_permit_id(permit_id):
        return None
    path = _permit_file(repo_root, task_id, permit_id)
    if not path.is_file():
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except ValueError:
        return None  # 损坏 / 半截 JSON（不应发生，os.replace 保证）→ deny
    return raw if isinstance(raw, dict) else None


def list_permits(repo_root, task_id) -> "list[dict]":
    """列出任务全部活跃 permit（按 permit_id 排序，确定性）。

    - 目录不存在 → []；
    - consumed / invalidated / .tmp 残影文件不列（活跃集只认
      <permit_id>.json 原名文件）；
    - 损坏 / 非 dict 文件跳过（单坏文件不炸审计面）；
    - 过期 permit 仍列出（过期是校验时点的拒绝理由，不是删除理由——
      "TTL 过期后 list 仍可见但 validate 拒绝"）。
    """
    directory = permits_dir(repo_root, task_id)
    if not directory.is_dir():
        return []
    permits = []
    for entry in directory.iterdir():
        name = entry.name
        if not entry.is_file() or not name.endswith(".json"):
            continue
        if name.endswith(CONSUMED_SUFFIX) \
                or name.endswith(INVALIDATED_SUFFIX) \
                or name.endswith(".tmp"):
            continue
        try:
            with open(entry, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
        except ValueError:
            continue
        if isinstance(raw, dict):
            permits.append(raw)
    return sorted(permits,
                  key=lambda item: str(item.get("permit_id", "")))


# —— 门校验（§6.4 校验链的数据层；reason 英文，hook 直接透出） ——

def validate_permit(repo_root, task_id, permit_id, *, unit_id=None,
                    wave_id=None, mode=None, now=None) -> "tuple":
    """校验 permit 可用性，返回 (ok: bool, reason: str)。

    RB-21-04 授权语义 fail-closed：无法证明有效即拒绝（cannot prove
    valid → deny）——损坏的时间戳 / 载荷绝不再「当作有效放行」。

    校验顺序冻结（先命中先返回；reason 为英文短语，hook 侧直接透出
    给主会话）：
      1. permit_id 形状非 "dp-" + 12 hex → "permit not found"（按
         不存在处理——marker 是不可信输入，形状不合法没有 lookup 价值）；
      2. 不存在 / 已消费改名 / 已失效改名 / 文件损坏 / 非 dict →
         "permit not found"（load 视同名下全部按不存在处理）；
      3. payload.permit_id 与请求 permit_id（= 文件名主体，load 按
         其定位打开）不一致 → "permit id mismatch"（伪造文件的兜底闸）；
      4. 文件内容 task_id 与请求任务不符 → "permit task mismatch"
         （手工挪动 / 伪造文件的兜底闸）；
      5. 载荷结构完整性（任一不满足即拒——fail-closed）：
           - unit_id 非非空 str / wave_id 非 None 且非非空 str →
             "permit malformed"；
           - mode 不在 PERMIT_MODES → "permit mode mismatch"；
           - reason invariant 不成立（mode="background" 而 reason 非
             None；mode="foreground" 而 reason 不在 FOREGROUND_REASONS）
             → "permit reason mismatch"；
           - created_at / expires_at 不可解析 → "permit malformed"
             （损坏时间戳绝不按未过期放行——与 lease 口径差异见模块
             docstring TTL 节）；
           - expires_at < created_at → "permit malformed"；
           - consumed 缺失 / 非 bool → "permit malformed"；为 true →
             "permit consumed"（活跃 .json 内写 consumed=true 同样拒）；
      6. expires_at <= now → "permit expired"（严格大于才有效；恰好
         到达过期时刻即失效——RB-21-04 用户锁定决策）；
      7. unit_id 给定且不符 → "permit unit mismatch"；
      8. wave_id 给定且不符 → "permit wave mismatch"；
      9. mode 给定且不符 → "permit mode mismatch"；
     10. 全过 → (True, "ok")。
    unit_id / wave_id / mode 传 None 表示该维不校验（hook 按 §6.4
    校验链按需给值）。reason 词汇有限集（全集，无其他取值）：ok /
    permit not found / permit id mismatch / permit task mismatch /
    permit malformed / permit mode mismatch / permit reason mismatch /
    permit consumed / permit expired / permit unit mismatch / permit
    wave mismatch。本函数只读不写——消费由 consume_permit 显式进行
    （PreToolUse 校验零写副作用）。
    """
    if not _valid_permit_id_shape(permit_id):
        return False, "permit not found"
    permit = load_permit(repo_root, task_id, permit_id)
    if permit is None:
        return False, "permit not found"
    if permit.get("permit_id") != permit_id:
        return False, "permit id mismatch"
    if permit.get("task_id") != task_id:
        return False, "permit task mismatch"
    # —— 载荷结构完整性（fail-closed：任何一处无法证明即拒绝） ——
    unit = permit.get("unit_id")
    if not isinstance(unit, str) or unit == "":
        return False, "permit malformed"
    payload_wave = permit.get("wave_id")
    if payload_wave is not None \
            and (not isinstance(payload_wave, str) or payload_wave == ""):
        return False, "permit malformed"
    payload_mode = permit.get("mode")
    if payload_mode not in PERMIT_MODES:
        return False, "permit mode mismatch"
    payload_reason = permit.get("reason")
    if payload_mode == "background":
        if payload_reason is not None:
            return False, "permit reason mismatch"
    elif payload_reason not in FOREGROUND_REASONS:
        return False, "permit reason mismatch"
    created_at = _parse_iso(permit.get("created_at"))
    if created_at is None:
        return False, "permit malformed"
    expires_at = _parse_iso(permit.get("expires_at"))
    if expires_at is None:
        return False, "permit malformed"
    if expires_at < created_at:
        return False, "permit malformed"
    consumed = permit.get("consumed")
    if not isinstance(consumed, bool):
        return False, "permit malformed"
    if consumed:
        return False, "permit consumed"
    # —— 过期（RB-21-04：expires_at <= now 即过期，严格大于才有效） ——
    if expires_at <= _coerce_now(now):
        return False, "permit expired"
    if unit_id is not None and permit.get("unit_id") != unit_id:
        return False, "permit unit mismatch"
    if wave_id is not None and permit.get("wave_id") != wave_id:
        return False, "permit wave mismatch"
    if mode is not None and permit.get("mode") != mode:
        return False, "permit mode mismatch"
    return True, "ok"


# —— 消费 / 失效（原子 rename；防重放的唯一写路径） ——

def _retire_permit(repo_root, task_id, permit_id, suffix) -> bool:
    """把活跃 permit 原子改名为 <permit_id><suffix>（消费 / 失效共用）。

    - 原文件不在（不存在 / 已消费 / 已失效）→ False（重放拒绝的
      实现点：第二次消费永不成功）；
    - rename 抛 OSError（并发竞态另一方先改成功 / 目标已存在 / 真实
      I/O 故障）→ False：fail-closed，调用方（hook）按未消费 deny，
      绝不因改名失败放行；
    - 成功 → True（审计轨迹以改名后文件保留，内容一字不改——消费态
      由文件名承载，不走内容重写，规避双写者竞态）。
    """
    if not _valid_permit_id(permit_id):
        return False
    path = _permit_file(repo_root, task_id, permit_id)
    if not path.is_file():
        return False
    try:
        os.rename(str(path), str(path.parent / (permit_id + suffix)))
    except OSError:
        return False
    return True


def consume_permit(repo_root, task_id, permit_id) -> bool:
    """消费 permit（一次性）：活跃文件原子改名为 <permit_id>.consumed.json。

    返回 True = 本次调用完成消费；False = 无活跃 permit 可消费（不
    存在 / 已消费 / 已失效 / 竞态落败）——同一 permit_id 第二次消费
    恒返回 False，重放被机械拒绝（§6.3 R3）。消费后 validate_permit
    即按 "permit not found" 拒绝。
    """
    return _retire_permit(repo_root, task_id, permit_id, CONSUMED_SUFFIX)


def invalidate_permit(repo_root, task_id, permit_id) -> bool:
    """失效 permit（abort 回退）：活跃文件原子改名为
    <permit_id>.invalidated.json。

    语义与 consume 同构（一次性、rename、False = 无活跃可失效），
    区别只在后缀——审计上区分「派发消费掉了」与「回制作废掉了」。
    """
    return _retire_permit(repo_root, task_id, permit_id,
                          INVALIDATED_SUFFIX)


# —— marker（§6.4 机器可验证绑定，格式冻结） ——

def marker_for(permit_id) -> str:
    """构造派发 marker："GLM_CONDUCTOR_DISPATCH=<permit_id>"（§6.4
    冻结格式）。主会话把返回值放进 Agent 调用的 prompt/description
    文本；hook 在文本中查找本前缀提取 permit_id。permit_id 非非空
    str 抛 ValueError（marker 是 lookup key，构造端不给垃圾）。"""
    if not isinstance(permit_id, str) or permit_id == "":
        raise ValueError(
            "marker_for：permit_id 必须是非空字符串，得到 %r" % (permit_id,))
    return MARKER_PREFIX + permit_id


def parse_marker(text):
    """从文本中解析 marker 携带的 permit_id；无 marker → None。

    规则（§6.4）：查找 MARKER_PREFIX 首次出现处，取其后到首个空白
    字符（空格 / 换行 / 制表）之间的 token 为 permit_id——多行
    prompt/description 文本中按行定位即由此保证；文本在 prefix 后
    直接结束（空 token）→ None。返回的是原始 token（不校验形状），
    形状与存在性交 validate_permit 门校验（marker 只是 lookup key，
    手写不存在的 permit 必须 reject）。
    """
    if not isinstance(text, str):
        return None
    index = text.find(MARKER_PREFIX)
    if index < 0:
        return None
    rest = text[index + len(MARKER_PREFIX):]
    token = rest.split()[0] if rest.split() else ""
    return token or None


# —— wave 标识与成员资格校验（v2.1 M4 wu-21-08） ——

# wave_id 形状（冻结）："wave-" + 12 hex（os.urandom，仿 permit_id）
WAVE_ID_PREFIX = "wave-"
WAVE_ID_HEX_CHARS = 12
_WAVE_ID_RANDOM_BYTES = WAVE_ID_HEX_CHARS // 2


def new_wave_id() -> str:
    """生成新 wave_id："wave-" + 12 hex（os.urandom，仿 _new_permit_id
    的冻结形状）。调用方（task_manager.prepare_dispatch_wave）负责
    唯一性落盘——wave 记录的 wave_id 在 dispatch.waves 列表内唯一由
    state._validate_dispatch 兜底校验。"""
    return WAVE_ID_PREFIX + os.urandom(_WAVE_ID_RANDOM_BYTES).hex()


def validate_wave_membership(repo_root, task_id, permit) -> "tuple":
    """wave permit 的成员资格校验，返回 (ok: bool, reason: str | None)。

    校验链（v2.1 M4 wu-21-08，hook 在 validate_permit 通过后追加调用；
    reason 为英文短语，hook 侧直接透出）：
      - permit 无 wave_id（缺失 / None；含 permit 非 dict 的容错）→
        (True, None)：单单元派发（prepare_dispatch 直签）不受 wave
        语义约束，恒放行；
      - wave permit → 惰性 import runtime.state 只读 state.json
        （函数内 import，模块顶层零 state 依赖的分层关系保持成立）：
          * state 缺失 / JSON 损坏 → (False, "wave state unavailable")
            （fail-closed：无法判定成员资格即不放行）；
          * dispatch.waves 无该 wave_id → (False, "wave not found")；
          * wave status != "active" → (False, "wave not active")；
          * permit.unit_id 不在 wave.units → (False, "wave member
            mismatch")；
          * 全过 → (True, None)。
    本函数只读不写——wave 关闭归 task_manager.finish_unit，这里绝不
    就地改 wave 状态。
    """
    wave_id = permit.get("wave_id") if isinstance(permit, dict) else None
    if wave_id is None:
        return True, None
    from runtime import state  # 惰性 import：保持模块顶层零 state 依赖
    try:
        st = state.load_state(repo_root, task_id)
    except ValueError:
        return False, "wave state unavailable"
    if st is None:
        return False, "wave state unavailable"
    dispatch_block = st.get("dispatch")
    waves = (dispatch_block.get("waves")
             if isinstance(dispatch_block, dict) else None)
    wave = None
    if isinstance(waves, list):
        for entry in waves:
            if isinstance(entry, dict) and entry.get("wave_id") == wave_id:
                wave = entry
                break
    if wave is None:
        return False, "wave not found"
    if wave.get("status") != "active":
        return False, "wave not active"
    members = wave.get("units")
    unit_id = permit.get("unit_id") if isinstance(permit, dict) else None
    if not isinstance(members, list) or unit_id not in members:
        return False, "wave member mismatch"
    return True, None
