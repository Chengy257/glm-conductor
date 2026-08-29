#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GLM Conductor v2 文件租约层（leases.json，v2 工作块 B9.1）。

职责：
    §78 的文件租约 owner map：Work Unit 派发前由 Task Manager 以单元 id
    为 owner 获取其 ownership 声明覆盖的路径租约；单元离开活跃写相后
    释放。租约是安全并行派发（§81；B10.1 dispatcher 租约闸）的两道
    准入闸之一（另一道是 ownership 闸，§66；两闸均须通过）——
    它把「谁正在写哪些路径」从内存约定升级为落盘事实，使 plan_dispatch
    能拦下 active 清单之外的占用（跨批次 / 跨上下文）。

§78 owner map 语义（本模块逐条锁定）：
      - 同 owner 放行：owner 重复获取已持有的 path → 幂等跳过（不报
        错、不更新 acquired_at、不产生重复记录）——派发重试安全；
      - 异 owner 拒绝：请求的任一 path 已被其他 owner 持有 →
        LeaseConflictError（列出全部冲突项，零写入）；
      - 获取时点：Work Unit 派发之前（plan_dispatch 准入通过后、真实
        派发子代理之前）；
      - 释放时点：单元离开活跃写相之后——completed / failed /
        cancelled；转 verifying 的单元不再写文件，但可能被裁决返工，
        故其释放时机由主会话裁决（本模块只提供 release_lease 原语）；
      - 全有或全无理由：部分获取会造成「半把锁」——单元自认持有完整
        ownership 实际只锁住一半，且重试时无法区分新申请与残留锁；
        因此任一冲突即整体失败、保持锁状态完全不变，调用方可安全重试。

存储：
    <repo_root>/.glm-conductor/tasks/<task-id>/leases.json
    （与 runtime.state 管理的 state.json 同目录，各管各的文件），
    形状 {"<归一 path/pattern>": record}，record：
      {"owner": str, "acquired_at": ISO-8601 UTC 毫秒,
       "session_id": str | None, "generation": >= 1 整数,
       "heartbeat_at": ISO-8601（新记录与 acquired_at 同值）,
       "expires_at": ISO-8601（可选——无此键 = 永久租约）}
    key 逐项 ownership.normalize_path 归一（反斜杠统一为
    "/"）；写入一律原子写（同目录 tmp + os.replace，模式照抄
    runtime/state.save_state——崩溃时读者要么看到旧文件要么看到新
    文件，绝不看到半截 JSON）。
    读-改-写无文件锁——**单写者前提**：主会话是唯一编排者（星型
    拓扑），多会话并行操作同一任务目录不在支持面内；前提被破坏时
    存在丢失更新窗口（后写者可能覆盖先写者的租约条目，理论上可
    放行重叠写），按保守限制登记。

TTL / generation / 崩溃恢复（v2.0.1 加固 H5，审查项 P1-4）：
    - TTL：派发路径的保守默认 LEASE_DEFAULT_TTL_SECONDS = 1800 秒，
      覆盖单个 work unit 一次有界实施的正常写相并留足余量；长期实施
      由 renew_lease 心跳续约；ttl_seconds=None 表示永久（不写
      expires_at——恢复语义上等价旧格式）；
    - generation：同 owner 重取「已过期」的记录 → generation + 1 且
      acquired_at / heartbeat_at / expires_at 按本次调用重写（幂等
      跳过则原记录一字不动）——区分「同一次持有的续期」与「新一轮
      持有」；
    - 向后兼容：2.0.0 旧格式 {"owner", "acquired_at"} 可读可续——无
      expires_at 永不过期（expired_leases 永不入选），陈旧归属由
      runtime.reconcile.reconcile_leases 的 stale 判定兜底、
      task_manager.recover_leases 闭环清理——崩溃后无需人工删除
      leases.json；
    - 保守容错：expires_at 解析失败一律按「未过期」处理（不抛、
      不误清——损坏时间戳绝不触发自动清理）。

依赖：
    runtime.ownership（normalize_path——key 归一）、runtime.state
    （task_dir——「与 state.json 同目录」的唯一事实源）。仅 Python 3
    标准库，`python3 -S` 可运行。

来源：
    docs/glm-conductor-v2-upgrade-guide-final.md §78（文件租约）/
    §81（并行安全 = ownership 声明判定可并行且无外来活跃租约冲突）/
    §82（并行上限 2-4，实验特性 experimental）+ v2 升级计划工作块
    B9.1 + v2.0.1 加固工作包 H5（审查项 P1-4/P1-5）。
"""

import datetime
import json
import os
import pathlib

from runtime import ownership
from runtime import state

# 租约文件名（位于 tasks/<task-id>/ 之下，与 state.json 同目录）
LEASE_FILENAME = "leases.json"

# §82 并行上限推荐值（2-4 的上界）：plan_dispatch 以此校验 max_workers
# 的合法上界；超出视为实验特性未放开（无界扇出被禁止）
DEFAULT_MAX_WORKERS_LIMIT = 4

# 派发路径的保守 TTL 默认（v2.0.1 加固 H5）：1800 秒覆盖单个 work
# unit 一次有界实施的正常写相并留足余量——远短于「永久」又长于任何
# 正常单元实施；到期靠 renew_lease 心跳或同 owner 过期刷新续期。
# ttl_seconds=None 表示永久（不写 expires_at——恢复语义上等价旧格式）。
LEASE_DEFAULT_TTL_SECONDS = 1800


class LeaseConflictError(Exception):
    """租约冲突：请求的 path 中存在被其他 owner（或形状异常记录）持有的项。

    属性 conflicts 为 [{"path": 归一路径, "owner": 持有者}, ...] 列表，
    按请求顺序列出全部冲突项（供调用方精确报告，而非只报第一个）。
    """

    def __init__(self, message, conflicts=None):
        super().__init__(message)
        self.conflicts = list(conflicts) if conflicts else []


# —— 路径定位 ——

def lease_path(repo_root, task_id) -> pathlib.Path:
    """返回租约文件路径 <repo_root>/.glm-conductor/tasks/<task-id>/leases.json。"""
    return state.task_dir(repo_root, task_id) / LEASE_FILENAME


# —— 参数归一 / 校验（结构性错误优先，失败零副作用） ——

def _check_owner(owner) -> str:
    """owner 必须是非空 str，否则 ValueError（租约语义依赖 owner 精确
    比较，空串 / 非字符串一律拒绝，不静默转换）。"""
    if not isinstance(owner, str) or owner == "":
        raise ValueError("owner 必须是非空字符串，得到 %r" % (owner,))
    return owner


def _normalize_paths(paths) -> "list[str]":
    """paths 归一为字符串列表：裸字符串按单路径容错（对齐
    dispatcher._as_pattern_group 的容错口径，否则 list("src/a") 会按
    字符拆散）；逐项 ownership.normalize_path（非字符串路径抛
    OwnershipError，结构性错误不静默转换）。"""
    if paths is None:
        return []
    if isinstance(paths, str):
        paths = [paths]
    return [ownership.normalize_path(path) for path in paths]


def _record_owner(record):
    """从租约记录提取 owner；形状异常的记录（非 dict / owner 非字符串）
    返回 None——视为「未知持有者」，后续一律保守判冲突，绝不静默覆盖
    他人或损坏的记录。"""
    if isinstance(record, dict) and isinstance(record.get("owner"), str):
        return record["owner"]
    return None


def _format_iso(moment) -> str:
    """aware datetime → ISO-8601 UTC 字符串（毫秒精度，
    2026-08-28T12:34:56.789Z 形态——本模块一切时间字段的统一格式）。"""
    return (moment.strftime("%Y-%m-%dT%H:%M:%S")
            + ".%03dZ" % (moment.microsecond // 1000))


def _utc_now_iso() -> str:
    """当前 UTC 时间的 ISO-8601 字符串（毫秒精度，形如
    2026-08-28T12:34:56.789Z）。"""
    return _format_iso(datetime.datetime.now(datetime.timezone.utc))


def _parse_iso(value):
    """ISO-8601 时间戳 → aware UTC datetime（"Z" 后缀容错——本模块
    现有格式带 Z，fromisoformat 不认，先替换为 "+00:00"；naive 值
    视为 UTC）。

    非字符串 / 空串 / 解析失败 → None：调用方一律按「未过期」保守
    处理（容错不抛——损坏的时间戳绝不触发自动清理）。
    """
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


def _check_ttl(ttl_seconds):
    """ttl_seconds 校验：None（永久）放行；数值要求 > 0（bool 是 int
    子类但拒绝充当时长）；非法抛 ValueError（中文消息）。"""
    if ttl_seconds is None:
        return None
    if isinstance(ttl_seconds, bool) \
            or not isinstance(ttl_seconds, (int, float)) or ttl_seconds <= 0:
        raise ValueError(
            "ttl_seconds 必须是 None（永久）或 > 0 的秒数，得到 %r"
            % (ttl_seconds,))
    return ttl_seconds


def _expires_at_iso(now_iso, ttl_seconds) -> str:
    """now_iso + ttl_seconds 的到期时刻（与 acquired_at 同格式）。"""
    base = _parse_iso(now_iso)
    if base is None:  # 防御：now_iso 恒为本模块产物，正常可解析
        base = datetime.datetime.now(datetime.timezone.utc)
    return _format_iso(base + datetime.timedelta(seconds=ttl_seconds))


def _record_expired(record, now_dt) -> bool:
    """记录是否已过期：expires_at 可解析且严格早于 now_dt。无
    expires_at（永久记录）或解析失败 → False（保守按未过期）。"""
    parsed = _parse_iso(record.get("expires_at"))
    if parsed is None:
        return False
    return parsed < now_dt


def _record_ttl_seconds(record):
    """记录的原有 TTL 窗宽秒数（renew_lease 未给显式 ttl 时按原窗宽
    从当前时刻重开）：expires_at 与参照点（heartbeat_at，缺省退
    acquired_at）均可解析时返回差值秒数（>= 0）；否则 None（视为
    无原 TTL——旧格式记录 / 永久记录 / 时间戳损坏）。"""
    expires = _parse_iso(record.get("expires_at"))
    if expires is None:
        return None
    base = _parse_iso(record.get("heartbeat_at"))
    if base is None:
        base = _parse_iso(record.get("acquired_at"))
    if base is None:
        return None
    return max((expires - base).total_seconds(), 0.0)


def _new_record(owner, *, session_id, ttl_seconds, now_iso) -> dict:
    """新持有记录（H5 形状）：expires_at 仅在 ttl_seconds 非 None 时
    写入（None = 永久）。"""
    record = {
        "owner": owner,
        "acquired_at": now_iso,
        "session_id": session_id,
        "generation": 1,
        "heartbeat_at": now_iso,
    }
    if ttl_seconds is not None:
        record["expires_at"] = _expires_at_iso(now_iso, ttl_seconds)
    return record


# —— 读 ——

def lease_state(repo_root, task_id) -> dict:
    """读取租约 owner map（归一 path → {"owner", "acquired_at"}）。

    文件不存在 → {}（从无租约）；JSON 损坏（无法解析 / 顶层非 JSON
    对象）→ ValueError（中文消息，不静默）。
    """
    path = lease_path(repo_root, task_id)
    if not path.is_file():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except ValueError as exc:
        raise ValueError(
            "leases.json 损坏，无法解析（%s）：%s" % (path, exc)) from exc
    if not isinstance(raw, dict):
        raise ValueError(
            "leases.json 损坏：顶层必须是 JSON 对象，得到 %s（%s）"
            % (type(raw).__name__, path))
    return raw


# —— 获取（全有或全无，§78） ——

def acquire_lease(repo_root, task_id, owner, paths, *,
                  session_id=None, ttl_seconds=None) -> dict:
    """全有或全无获取一组路径租约，返回获取后的最新租约 owner map。

    流程（§78 + H5 TTL/generation）：
      1. 先全量校验参数（owner 非空 str；ttl_seconds 为 None 或 > 0
         数值；paths 逐项 normalize_path），任一非法在此抛出，零副作用；
      2. 逐 path 检查现有租约：被其他 owner（或形状异常记录）持有 →
         收集冲突。过期不豁免冲突闸——释放归 reconcile_leases /
         recover_leases 与主会话裁决，获取端保守；
      3. 存在任一冲突 → 抛 LeaseConflictError（消息列出申请方与全部
         冲突 path+持有者，conflicts 属性逐项精确），零写入；
      4. 全部可得 → 逐 path 处置：
         - 新 path → 写入 H5 形状记录（{"owner", "acquired_at",
           "session_id", "generation": 1, "heartbeat_at": 同
           acquired_at}；ttl_seconds 非 None 时另写 expires_at =
           acquired_at + ttl；None = 永久不写）；
         - 同 owner 已持有且未过期 → 幂等跳过（不更新任何字段——
           行为与 H5 之前一致，派发重试安全）；
         - 同 owner 已持有但已过期（expires_at < now）→ 刷新：
           generation + 1，acquired_at / heartbeat_at / expires_at
           按本次调用重写（ttl_seconds=None → 移除旧 expires_at 转
           为永久），session_id 参数非 None 时同步更新（None 保留
           旧值）；
      5. 实际有写入（新获取 / 刷新）才落盘；全部幂等命中或 paths 为
         空 → 不落盘。

    时点锚定：由 Task Manager 在 Work Unit 派发前调用（§78）；派发
    路径传 LEASE_DEFAULT_TTL_SECONDS 保守 TTL，长期实施由
    renew_lease 心跳续约。
    """
    _check_owner(owner)
    ttl_seconds = _check_ttl(ttl_seconds)
    wanted = _normalize_paths(paths)
    current = lease_state(repo_root, task_id)
    # 先收齐全部冲突再整体失败（§78 全有或全无：任一冲突即零写入）
    conflicts = []
    for path in wanted:
        if path in current:
            holder = _record_owner(current[path])
            if holder != owner:
                conflicts.append({"path": path, "owner": holder})
    if conflicts:
        raise LeaseConflictError(
            "租约冲突：owner %r 请求的以下路径已被其他 owner 持有——%s"
            % (owner, "；".join(
                "%s（持有者 %s）" % (item["path"], item["owner"])
                for item in conflicts)),
            conflicts)
    now_iso = _utc_now_iso()
    now_dt = _coerce_now(now_iso)
    changed = False
    for path in wanted:
        record = current.get(path)
        held_by_owner = (isinstance(record, dict)
                         and _record_owner(record) == owner)
        if held_by_owner and not _record_expired(record, now_dt):
            continue  # 同 owner 未过期 → 幂等跳过，不更新任何字段
        if held_by_owner:
            # 同 owner 已过期 → 刷新：generation +1，时间字段重写
            generation = record.get("generation")
            if isinstance(generation, bool) \
                    or not isinstance(generation, int):
                generation = 1  # 旧格式 / 形状异常按首轮起算
            refreshed = {
                "owner": owner,
                "acquired_at": now_iso,
                "session_id": (session_id if session_id is not None
                               else record.get("session_id")),
                "generation": generation + 1,
                "heartbeat_at": now_iso,
            }
            if ttl_seconds is not None:
                refreshed["expires_at"] = _expires_at_iso(now_iso,
                                                          ttl_seconds)
            current[path] = refreshed
        else:
            current[path] = _new_record(owner, session_id=session_id,
                                        ttl_seconds=ttl_seconds,
                                        now_iso=now_iso)
        changed = True
    if changed:
        _save_lease_state(repo_root, task_id, current)
    return current


# —— 释放（§78：单元离开活跃写相后） ——

def release_lease(repo_root, task_id, owner, paths=None) -> "list[str]":
    """释放租约，返回实际释放的归一 path 列表（排序，确定性）。

    - paths=None → 释放该 owner 当前持有的全部 path；
    - paths 给定 → 只释放其中「该 owner 持有」的项；被他人持有的项
      不动、静默跳过（§78 异 owner 不可代删）；无人持有的项同样跳过；
    - 文件不存在 → 返回 []；实际有释放才落盘（原子写）。
    """
    _check_owner(owner)
    targets = None if paths is None else set(_normalize_paths(paths))
    current = lease_state(repo_root, task_id)
    released = []
    for path in list(current):
        if _record_owner(current[path]) != owner:
            continue  # 他人（或形状异常记录）持有 → 不动
        if targets is None or path in targets:
            del current[path]
            released.append(path)
    if released:
        _save_lease_state(repo_root, task_id, current)
    return sorted(released)


def held_by(repo_root, task_id, owner) -> "list[str]":
    """当前 owner 持有的全部归一 path 列表（排序，确定性）。"""
    _check_owner(owner)
    current = lease_state(repo_root, task_id)
    return sorted(path for path in current
                  if _record_owner(current[path]) == owner)


# —— 心跳续约 / 过期判定（v2.0.1 加固 H5，P1-4/P1-5） ——

def renew_lease(repo_root, task_id, owner, paths=None, *,
                ttl_seconds=None) -> "list[str]":
    """心跳续约：对该 owner 持有的（指定）路径更新 heartbeat_at 与
    expires_at，返回实际续约的归一路径列表（排序，确定性）。

    - paths=None → 续约该 owner 当前持有的全部 path；paths 给定 →
      只续其中该 owner 持有的项，其余（他人持有 / 无人持有）静默
      跳过（§78 异 owner 不可代动）；
    - expires_at 推算：显式 ttl_seconds 优先（当前时刻 + ttl）；未给
      时按该记录原有 TTL 窗宽（expires_at − heartbeat_at/acquired_at）
      从当前时刻重开；记录无原 TTL（永久记录 / 旧格式 / 时间戳损坏）
      → 只更新 heartbeat_at，不产生 expires_at；
    - 实际有续约才落盘（原子写）；无续约返回 [] 且不写文件。
    """
    _check_owner(owner)
    ttl_seconds = _check_ttl(ttl_seconds)
    targets = None if paths is None else set(_normalize_paths(paths))
    current = lease_state(repo_root, task_id)
    now_iso = _utc_now_iso()
    now_dt = _coerce_now(now_iso)
    renewed = []
    for path in list(current):
        record = current[path]
        if not isinstance(record, dict) or _record_owner(record) != owner:
            continue  # 他人（或形状异常记录）持有 → 不动
        if targets is not None and path not in targets:
            continue
        # 原窗宽先于心跳更新计算（基于旧 heartbeat/acquired_at）——
        # 顺序不可反，否则窗宽被放大成「到期时刻 − 新心跳」
        original_ttl = None if ttl_seconds is not None \
            else _record_ttl_seconds(record)
        record["heartbeat_at"] = now_iso
        if ttl_seconds is not None:
            record["expires_at"] = _expires_at_iso(now_iso, ttl_seconds)
        elif original_ttl is not None:
            record["expires_at"] = _format_iso(
                now_dt + datetime.timedelta(seconds=original_ttl))
        renewed.append(path)
    if renewed:
        _save_lease_state(repo_root, task_id, current)
    return sorted(renewed)


def expired_leases(repo_root, task_id, *, now=None) -> "list[dict]":
    """列出已过期租约 [{"path", "owner", "expires_at"}...]（按 path
    排序，确定性）。

    判定：expires_at 可解析且严格早于 now（expires_at < now；恰好
    相等不算过期）。now 缺省当前 UTC，接受 aware/naive datetime
    （naive 视为 UTC）或 ISO-8601 字符串。无 expires_at 的永久记录
    （含 2.0.0 旧格式）与时间戳解析失败的记录永不入选——过期判定
    只清理明确写了 TTL 的记录，陈旧归属交 reconcile_leases 的
    stale 判定兜底。
    """
    now_dt = _coerce_now(now)
    current = lease_state(repo_root, task_id)
    expired = []
    for path, record in current.items():
        if not isinstance(record, dict):
            continue
        expires_at = record.get("expires_at")
        parsed = _parse_iso(expires_at)
        if parsed is None or parsed >= now_dt:
            continue
        expired.append({"path": path, "owner": _record_owner(record),
                        "expires_at": expires_at})
    return sorted(expired, key=lambda item: item["path"])


# —— 原子写（模式照抄 runtime/state.save_state） ——

def _save_lease_state(repo_root, task_id, lease_map) -> pathlib.Path:
    """原子保存租约文件，返回最终路径。

    同目录 <name>.tmp 先写（UTF-8、ensure_ascii=False、缩进 2、
    sort_keys——同状态落盘形态确定），再 os.replace 覆盖；任何失败
    路径清理 tmp，成功后确保 tmp 不残留。
    """
    path = lease_path(repo_root, task_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.parent / (path.name + ".tmp")
    try:
        # newline="\n"：固定 \n 换行，避免 Windows 文本模式写出 \r\n
        with open(tmp_path, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(lease_map, fh, ensure_ascii=False, indent=2,
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
