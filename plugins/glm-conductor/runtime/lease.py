#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GLM Conductor v2 文件租约层（leases.json，v2 工作块 B9.1）。

职责：
    §78 的文件租约 owner map：Work Unit 派发前由 Task Manager 以单元 id
    为 owner 获取其 ownership 声明覆盖的路径租约；单元离开活跃写相后
    释放。租约是安全并行派发（§81；B10.1 dispatcher 租约闸）的前提——
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
    形状 {"<归一 path/pattern>": {"owner": str, "acquired_at": ISO-8601
    UTC 毫秒}}。key 逐项 ownership.normalize_path 归一（反斜杠统一为
    "/"）；写入一律原子写（同目录 tmp + os.replace，模式照抄
    runtime/state.save_state——崩溃时读者要么看到旧文件要么看到新
    文件，绝不看到半截 JSON）。

依赖：
    runtime.ownership（normalize_path——key 归一）、runtime.state
    （task_dir——「与 state.json 同目录」的唯一事实源）。仅 Python 3
    标准库，`python3 -S` 可运行。

来源：
    docs/glm-conductor-v2-upgrade-guide-final.md §78（文件租约）/
    §81（并行安全 = ownership 不相交或有效租约保护）/ §82（并行上限
    2-4，实验特性 experimental）+ v2 升级计划工作块 B9.1。
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


def _utc_now_iso() -> str:
    """当前 UTC 时间的 ISO-8601 字符串（毫秒精度，形如
    2026-08-28T12:34:56.789Z）。"""
    now = datetime.datetime.now(datetime.timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S") + ".%03dZ" % (now.microsecond // 1000)


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

def acquire_lease(repo_root, task_id, owner, paths) -> dict:
    """全有或全无获取一组路径租约，返回获取后的最新租约 owner map。

    流程（§78）：
      1. 先全量校验参数（owner 非空 str；paths 逐项 normalize_path），
         任一非法在此抛出，零副作用；
      2. 逐 path 检查现有租约：已被 owner 持有 → 幂等（不更新
         acquired_at）；被其他 owner（或形状异常记录）持有 → 收集冲突；
      3. 存在任一冲突 → 抛 LeaseConflictError（消息列出申请方与全部
         冲突 path+持有者，conflicts 属性逐项精确），零写入；
      4. 全部可得 → 为新 path 写入 {"owner", "acquired_at"}（已持有的
         跳过，保持幂等）并原子保存；无新写入（全部幂等命中 / paths
         为空）则不落盘。

    时点锚定：由 Task Manager 在 Work Unit 派发前调用（§78）。
    """
    _check_owner(owner)
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
    changed = False
    for path in wanted:
        if path in current:
            continue  # 同 owner 已持有 → 幂等跳过，不更新 acquired_at
        current[path] = {"owner": owner, "acquired_at": _utc_now_iso()}
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
