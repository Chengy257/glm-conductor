#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GLM Conductor v2 任务运行时状态层（state.json）。

职责：
    管理 v2 任务的确定性状态文件 `.glm-conductor/tasks/<task-id>/state.json`：
      - 定位：tasks_root / task_dir / state_path 三个纯路径函数；
      - 创建：new_task_state() 构造带默认值的完整状态 dict（只构造不校验）；
      - 校验：validate_state() 返回中文错误列表（空列表 = 合法），不抛异常；
      - 保存：save_state() 先校验再原子写（同目录 tmp + os.replace）；
      - 读取：load_state() 读取并归一 v1.x legacy 标识（CONTINUITY_ID 等）；
      - 发现：find_active_tasks() 扫描全部非终态活动任务（健壮性优先）。
    本文件是 Stop 完成门钩子等强制状态源的确定性来源。

路径布局：
    <repo_root>/.glm-conductor/tasks/<task-id>/state.json
    与 runtime.journal 管理的 events.jsonl 同处一个任务目录，各管各的文件。

schema 来源：
    docs/glm-conductor-v2-upgrade-guide-final.md §7（推荐 schema 的权威定义）。
    repository 文件仍是代码状态真相源，本文件只是运行时任务状态。

legacy 标识归一：
    v1.x checkpoint/状态用 CONTINUITY_ID 标识任务；v2 统一为 task_id
    （TASK_ID 全量替代 CONTINUITY_ID）。读取时按 TASK_ID_KEYS 顺序归一。

依赖：
    仅 Python 3 标准库（json / os / pathlib / re），零第三方依赖，
    `python3 -S` 可运行（无 site-packages）。
"""

import json
import os
import pathlib
import re

# —— 路径常量与定位 ——

# 仓库根下运行时目录名
TASKS_DIRNAME = ".glm-conductor"
# 任务状态文件名（位于 tasks/<task-id>/ 之下）
STATE_FILENAME = "state.json"

# —— 词汇表常量（枚举校验用）——

# SELECTIVE ROUTE 四模式
ROUTE_MODES = ("solo", "delegate", "audit", "full")
# Delegability 两级
DELEGABILITY_LEVELS = ("low", "high")
# Assurance 两级
ASSURANCE_LEVELS = ("standard", "high")
# 实施者（主会话自己实施 / 两个实施侧 agent）
EXECUTORS = ("main", "flash-implementer", "visual-implementer")
# 连续性三模式
CONTINUITY_MODES = ("foreground", "resumable", "idle")
# 任务全生命周期状态
TASK_STATUSES = (
    "created", "preflight", "routed", "decomposed", "executing",
    "joining", "verifying", "reviewing", "completed",
    "waiting_quota", "blocked", "cancelled", "failed")
# 终态：find_active_tasks 不再返回
TERMINAL_STATUSES = ("completed", "cancelled", "failed")
# 验证结果词汇
VERIFICATION_STATUSES = ("missing", "valid", "stale", "failed")
# 审查裁决词汇
REVIEW_VERDICTS = ("not-required", "missing", "ship", "fix-first", "rethink", "stale")

# legacy 标识归一：v1.x checkpoint/状态用 CONTINUITY_ID，v2 统一为 task_id
TASK_ID_KEYS = ("task_id", "TASK_ID", "CONTINUITY_ID", "continuity_id")

# task_id 合法格式：字母数字开头，仅含字母数字与连字符
# （「语义前缀+随机后缀」机械唯一格式的落点约束）
_TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]*$")

# 必填顶层键（缺一即非法；未知顶层键忽略，向前兼容）
_REQUIRED_TOP_KEYS = ("task_id", "goal", "route", "status")


# —— 路径定位 ——

def tasks_root(repo_root) -> pathlib.Path:
    """返回运行时任务根目录 <repo_root>/.glm-conductor/tasks。"""
    return pathlib.Path(repo_root) / TASKS_DIRNAME / "tasks"


def task_dir(repo_root, task_id) -> pathlib.Path:
    """返回单个任务目录 <repo_root>/.glm-conductor/tasks/<task-id>。"""
    return tasks_root(repo_root) / str(task_id)


def state_path(repo_root, task_id) -> pathlib.Path:
    """返回任务状态文件路径 <repo_root>/.glm-conductor/tasks/<task-id>/state.json。"""
    return task_dir(repo_root, task_id) / STATE_FILENAME


# —— legacy 标识归一 ——

def normalize_task_id(raw: dict) -> str:
    """从状态 dict 归一任务标识，返回 task_id 字符串。

    规则：
      - 按 TASK_ID_KEYS 顺序取第一个存在的键的值；
      - 多个键同时存在且值不一致 → ValueError；
      - 全部不存在、或取到的值为空 / 非 str → ValueError。
    """
    if not isinstance(raw, dict):
        raise ValueError("任务标识归一失败：输入必须是 JSON 对象")
    present = [key for key in TASK_ID_KEYS if key in raw]
    if not present:
        raise ValueError(
            "任务标识归一失败：缺少任务标识键（%s）" % ", ".join(TASK_ID_KEYS))
    first_key = present[0]
    value = raw[first_key]
    for key in present[1:]:
        if raw[key] != value:
            raise ValueError(
                "任务标识归一失败：多个标识键取值不一致（%s=%r 与 %s=%r）"
                % (first_key, value, key, raw[key]))
    if not isinstance(value, str) or value == "":
        raise ValueError("任务标识归一失败：%s 的值必须是非空字符串" % first_key)
    return value


# —— 校验 ——

def _enum_error(path, value, allowed):
    """构造枚举取值错误的中文消息（含字段路径与合法取值清单）。"""
    return "%s %r 不在合法取值内（%s）" % (path, value, ", ".join(allowed))


def _str_list_errors(path, value):
    """校验「字符串数组」：值必须是 list 且每项为非空 str。"""
    if not isinstance(value, list):
        return ["%s 必须是数组" % path]
    errors = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or item == "":
            errors.append("%s[%d] 必须是非空字符串" % (path, index))
    return errors


def _validate_route(route):
    """校验 route 子对象（mode 必填；其余键存在且非 None 时校验枚举）。"""
    if not isinstance(route, dict):
        return ["route 必须是 JSON 对象"]
    errors = []
    mode = route.get("mode")
    if mode not in ROUTE_MODES:
        errors.append(_enum_error("route.mode", mode, ROUTE_MODES))
    for key, allowed in (
            ("delegability", DELEGABILITY_LEVELS),
            ("assurance", ASSURANCE_LEVELS),
            ("executor", EXECUTORS),
            ("continuity", CONTINUITY_MODES)):
        if key in route and route[key] is not None and route[key] not in allowed:
            errors.append(_enum_error("route." + key, route[key], allowed))
    return errors


def _validate_ownership(ownership):
    """校验 ownership 子对象（files 必须是非空字符串数组）。"""
    if not isinstance(ownership, dict):
        return ["ownership 必须是 JSON 对象"]
    return _str_list_errors("ownership.files", ownership.get("files", []))


def _validate_verification(verification):
    """校验 verification 子对象（required/completed 数组 + fingerprint）。"""
    if not isinstance(verification, dict):
        return ["verification 必须是 JSON 对象"]
    errors = []
    errors.extend(
        _str_list_errors("verification.required", verification.get("required", [])))
    errors.extend(
        _str_list_errors("verification.completed", verification.get("completed", [])))
    fingerprint = verification.get("fingerprint")
    if fingerprint is not None and not isinstance(fingerprint, str):
        errors.append("verification.fingerprint 必须是字符串或 null")
    return errors


def _validate_review(review):
    """校验 review 子对象（required 布尔 + reviewer/verdict 可空枚举）。"""
    if not isinstance(review, dict):
        return ["review 必须是 JSON 对象"]
    errors = []
    if not isinstance(review.get("required"), bool):
        errors.append("review.required 必须是布尔值")
    reviewer = review.get("reviewer")
    if reviewer is not None and not isinstance(reviewer, str):
        errors.append("review.reviewer 必须是字符串或 null")
    verdict = review.get("verdict")
    if verdict is not None and verdict not in REVIEW_VERDICTS:
        errors.append(_enum_error("review.verdict", verdict, REVIEW_VERDICTS))
    return errors


def _validate_dispatch(dispatch):
    """校验 dispatch 子对象（max_workers >= 1 的整数 + active 数组）。"""
    if not isinstance(dispatch, dict):
        return ["dispatch 必须是 JSON 对象"]
    errors = []
    max_workers = dispatch.get("max_workers", 1)
    # bool 是 int 的子类，但 True/False 不应充当 max_workers
    if isinstance(max_workers, bool) or not isinstance(max_workers, int) \
            or max_workers < 1:
        errors.append("dispatch.max_workers 必须是 >= 1 的整数")
    if not isinstance(dispatch.get("active", []), list):
        errors.append("dispatch.active 必须是数组")
    return errors


def validate_state(state) -> "list[str]":
    """校验状态 dict，返回错误消息列表（中文，含字段路径）；空列表 = 合法。

    不抛异常；state 非 dict → ["state 必须是 JSON 对象"]。
    未知顶层键忽略（向前兼容），不报错。
    """
    if not isinstance(state, dict):
        return ["state 必须是 JSON 对象"]
    errors = []

    # 规则 10：必填顶层键（task_id / goal / route / status）
    for key in _REQUIRED_TOP_KEYS:
        if key not in state:
            errors.append("缺少必填顶层键 %s" % key)

    # 规则 1：task_id 非空 str 且匹配格式
    if "task_id" in state:
        task_id = state["task_id"]
        if not isinstance(task_id, str) or task_id == "":
            errors.append("task_id 必须是非空字符串")
        elif not _TASK_ID_RE.match(task_id):
            errors.append(
                "task_id %r 不匹配格式 ^[A-Za-z0-9][A-Za-z0-9-]*$"
                "（字母数字开头，仅含字母数字与连字符）" % task_id)

    # 规则 2：goal 非空 str
    if "goal" in state:
        goal = state["goal"]
        if not isinstance(goal, str) or goal == "":
            errors.append("goal 必须是非空字符串")

    # 规则 3：route
    if "route" in state:
        errors.extend(_validate_route(state["route"]))

    # 规则 4：ownership
    if "ownership" in state:
        errors.extend(_validate_ownership(state["ownership"]))

    # 规则 5：verification
    if "verification" in state:
        errors.extend(_validate_verification(state["verification"]))

    # 规则 6：review
    if "review" in state:
        errors.extend(_validate_review(state["review"]))

    # 规则 7：work_units 存在则必须是 list
    # （内部结构 v2 后续阶段才定义，本轮不校验）
    if "work_units" in state and not isinstance(state["work_units"], list):
        errors.append("work_units 必须是数组")

    # 规则 8：dispatch
    if "dispatch" in state:
        errors.extend(_validate_dispatch(state["dispatch"]))

    # 规则 9：status ∈ TASK_STATUSES
    if "status" in state:
        status = state["status"]
        if status not in TASK_STATUSES:
            errors.append(_enum_error("status", status, TASK_STATUSES))

    return errors


# —— 构造 ——

def new_task_state(task_id, goal, route, *, ownership_files=(),
                   verification_required=(), review_required=False,
                   reviewer=None, status="created") -> dict:
    """构造带默认值的完整状态 dict。

    只做构造不做校验（调用方负责 validate_state）。route 接受 dict，
    含 mode / delegability / assurance / executor / continuity 五键，
    缺键时对应值填 None（mode 缺失会导致 validate_state 报 route.mode 错）；
    route 非 dict 时抛 TypeError。
    """
    if not isinstance(route, dict):
        raise TypeError(
            "route 必须是 dict（SELECTIVE ROUTE 五字段），得到 %s"
            % type(route).__name__)
    return {
        "task_id": task_id,
        "goal": goal,
        "route": {
            "mode": route.get("mode"),
            "delegability": route.get("delegability"),
            "assurance": route.get("assurance"),
            "executor": route.get("executor"),
            "continuity": route.get("continuity"),
        },
        "ownership": {"files": list(ownership_files)},
        "verification": {
            "required": list(verification_required),
            "completed": [],
            "fingerprint": None,
        },
        "review": {
            "required": review_required,
            "reviewer": reviewer,
            "verdict": None,
        },
        "work_units": [],
        "dispatch": {"max_workers": 1, "active": []},
        "status": status,
    }


# —— 保存 / 读取 / 发现 ——

def save_state(repo_root, state, *, task_id=None) -> pathlib.Path:
    """校验并原子保存状态文件，返回最终路径。

    - validate_state 有错误 → ValueError（错误清单拼接）；
    - 写入路径的 task_id 优先取参数 task_id，否则取 state["task_id"]；
    - 目录不存在自动创建；
    - 原子写：先写同目录 <name>.tmp（UTF-8、ensure_ascii=False、缩进 2），
      再 os.replace 覆盖；任何失败路径清理 tmp，成功后确保 tmp 不存在。
    """
    errors = validate_state(state)
    if errors:
        raise ValueError("state 非法，无法保存：%s" % "；".join(errors))
    tid = task_id if task_id is not None else state["task_id"]
    path = state_path(repo_root, tid)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.parent / (path.name + ".tmp")
    try:
        # newline="\n"：固定 \n 换行，避免 Windows 文本模式写出 \r\n
        with open(tmp_path, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(state, fh, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink()
        raise
    # os.replace 成功后 tmp 已不存在；防御性兜底，确保 tmp 不残留
    if tmp_path.exists():
        tmp_path.unlink()
    return path


def load_state(repo_root, task_id) -> "dict | None":
    """读取任务状态文件；文件不存在 → None；JSON 损坏 → ValueError（不静默）。

    读取后按 TASK_ID_KEYS 顺序归一 task_id 写回 raw["task_id"]，并 pop 掉
    值与归一结果一致的 legacy 键（TASK_ID / CONTINUITY_ID / continuity_id）。
    """
    path = state_path(repo_root, task_id)
    if not path.is_file():
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except ValueError as exc:
        raise ValueError(
            "state.json 损坏，无法解析（%s）：%s" % (path, exc)) from exc
    tid = normalize_task_id(raw)
    for key in TASK_ID_KEYS[1:]:
        if key in raw and raw[key] == tid:
            del raw[key]
    raw["task_id"] = tid
    return raw


def find_active_tasks(repo_root) -> "list[str]":
    """扫描 tasks_root，返回全部非终态活动任务的 task_id（按目录名排序）。

    单个任务目录解析失败（JSON 损坏 / 缺 task_id / 无 state.json / 读取时
    OSError 等权限问题）→ 跳过该目录，不中断扫描（健壮性优先）。
    """
    root = tasks_root(repo_root)
    if not root.is_dir():
        return []
    active = []
    for entry in sorted(root.iterdir(), key=lambda item: item.name):
        if not entry.is_dir():
            continue
        try:
            loaded = load_state(repo_root, entry.name)
        except (ValueError, OSError):
            continue
        if loaded is None:
            continue
        if loaded.get("status") not in TERMINAL_STATUSES:
            active.append(entry.name)
    return active
