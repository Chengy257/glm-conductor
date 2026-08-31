#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GLM Conductor v2 任务运行时状态层（state.json）。

职责：
    管理 v2 任务的确定性状态文件 `.glm-conductor/tasks/<task-id>/state.json`：
      - 定位：tasks_root / task_dir / state_path 三个纯路径函数；
      - 创建：new_task_state() 构造带默认值的完整状态 dict（只构造不校验）；
      - 写入：record_verification() / record_review() /
        record_visual_evidence() 三个纯 dict 变换助手（就地修改并返回
        同一 dict，不触碰磁盘，调用方负责 save_state）——记录验证命令 /
        审查裁决的证据指纹与视觉证据 sha256（升级指南 §19/§20/§22）；
      - 校验：validate_state() 返回中文错误列表（空列表 = 合法），不抛异常；
        除逐字段枚举外还强制 route 跨字段不变量（矩阵一致性 / executor /
        review / delegate-full 实质性绑定，validate_route_invariants，
        H2/P0-2）；
      - 保存：save_state() 先校验再原子写（同目录 tmp + os.replace），并按
        顶层状态转换表（TASK_TRANSITIONS）拒绝非法 status 迁移——
        completed 只能由完成门经内部通道（commit_completion）提交；
      - 迁移 / 提交：transition_task_status() 公共状态迁移入口（记
        status_changed 事件）；commit_completion() 完成门专用提交通道；
      - 读取：load_state() 读取并归一 v1.x legacy 标识（CONTINUITY_ID 等）；
      - 发现：discover_tasks() 对 tasks_root 全部子目录四分类
        （active / terminal / corrupt / orphaned，H3/P0-3——损坏的
        state.json 不再从发现阶段静默消失）；find_active_tasks() 保留为
        兼容 helper（实现复用 discover_tasks，输出与四分类引入前一致）；
      - 仓库根绑定（RB-2，release hardening）：可选顶层 "repository"
        块为任务绑定专属 Git 仓库根——bind_repository_root() 写入 /
        new_task_state(repository_root=...) 构造期绑定 /
        bound_repository_root() 容错读 / resolve_repository_root()
        解析生效根（绑定优先，缺省回退账本根）。绑定后任务的 git 操作
        （touched 清单 / 基线 / 证据指纹）按绑定根求值，而账本（
        state.json / events.jsonl / 租约）恒在账本根——两根分离是多仓
        隔离的基础；无 repository 键即 legacy 形态，行为与单仓时代
        完全一致。
      - 执行策略（v2.1 M1，自动化强度授权事实源）：可选顶层
        "execution_policy" 块——new_task_state() 构造默认块
        （runtime.execution_policy.default_execution_policy），
        validate_state 规则 8.7 复用 runtime.execution_policy 校验
        （错误路径前缀 execution_policy.）；无该键即 legacy 形态，
        完全合法，由消费方按保守默认块解释。
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
    仅 Python 3 标准库（json / os / pathlib / re）+ runtime.quota.parser
    （quota 状态词汇 QUOTA_STATUSES，§39；quota/* 不 import 本模块，
    无循环导入）+ runtime.work_unit（work unit 逐项校验，B8.1；
    本模块单向导入它，它不导入本模块，无循环导入）+
    runtime.execution_policy（v2.1 M1 授权事实源：默认块构造与块内
    校验；它只依赖 runtime.quota.parser，不导入本模块，无循环导入），
    零第三方依赖，`python3 -S` 可运行（无 site-packages）。
"""

import json
import os
import pathlib
import re

from runtime.execution_policy import (default_execution_policy,
                                      validate_execution_policy)
from runtime.quota.parser import QUOTA_STATUSES
from runtime.work_unit import validate_work_unit

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
# Delegability × Assurance → mode 路由矩阵（SKILL.md §5 的代码化权威定义；
# validate_route_invariants 规则 1 据此强制矩阵一致性，H2/P0-2）
ROUTE_MATRIX = {
    "low": {"standard": "solo", "high": "audit"},
    "high": {"standard": "delegate", "high": "full"},
}
# 委派实施侧的两个实施者（R2 executor 绑定的 delegate/full 合法集合）
IMPLEMENTER_EXECUTORS = ("flash-implementer", "visual-implementer")
# 连续性三模式
CONTINUITY_MODES = ("foreground", "resumable", "idle")
# 任务全生命周期状态（finalizing = 完成请求态：进入即请求完成，
# completed 只能由 Stop 完成门在其四重检查全部通过后提交；
# waiting_user = v2.1 §14.5 自动续跑授权耗尽态：auto_once / until_done
# 的窗口预算用尽后等待用户重新授权，重新授权后经 waiting_user →
# executing 回到执行态族）
TASK_STATUSES = (
    "created", "preflight", "routed", "decomposed", "executing",
    "joining", "verifying", "reviewing", "finalizing", "completed",
    "waiting_quota", "waiting_user", "blocked", "cancelled", "failed")
# 终态：discover_tasks 归入 terminal 桶（find_active_tasks 不再返回）
TERMINAL_STATUSES = ("completed", "cancelled", "failed")
# 顶层状态转换表：键 = 旧 status，值 = 允许的直接后继（终态无表项 =
# 不接受任何转换）。save_state 与 transition_task_status 据此拒绝非法
# 迁移；completed 唯一入边 finalizing→completed 只对完成门内部通道
# （save_state 的 _gate_commit=True）放行，公共 API 一律拒绝。
TASK_TRANSITIONS = {
    "created": ("preflight", "routed", "decomposed", "executing",
                "blocked", "failed", "cancelled"),
    "preflight": ("routed", "decomposed", "executing", "blocked",
                  "failed", "cancelled"),
    "routed": ("decomposed", "executing", "blocked", "failed", "cancelled"),
    "decomposed": ("executing", "blocked", "failed", "cancelled"),
    "executing": ("joining", "verifying", "reviewing", "waiting_quota",
                  "finalizing", "blocked", "failed", "cancelled"),
    "joining": ("executing", "verifying", "reviewing", "finalizing",
                "blocked", "failed", "cancelled"),
    "verifying": ("reviewing", "finalizing", "executing", "blocked",
                  "failed", "cancelled"),
    "reviewing": ("finalizing", "executing", "blocked", "failed",
                  "cancelled"),
    "waiting_quota": ("executing", "waiting_user", "blocked", "failed",
                      "cancelled"),
    # waiting_user（v2.1 §14.5）：自动续跑授权耗尽后等待用户重新授权；
    # 用户重新授权（或主会话经授权升档）后回到 executing，公共尾巴
    # （blocked/failed/cancelled）与 waiting_quota 同款
    "waiting_user": ("executing", "blocked", "failed", "cancelled"),
    "blocked": ("preflight", "routed", "decomposed", "executing",
                "joining", "verifying", "reviewing", "waiting_quota",
                "finalizing", "failed", "cancelled"),
    "finalizing": ("completed", "failed", "cancelled"),
}
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


def derive_review_required(route):
    """由 route 推导审查义务：mode ∈ (audit, full) 或 assurance == "high"
    → True；mode ∈ (solo, delegate) 且 assurance == "standard" → False；
    其余（信息不足 / route 非 dict）→ None。"""
    if not isinstance(route, dict):
        return None
    mode = route.get("mode")
    assurance = route.get("assurance")
    if mode in ("audit", "full") or assurance == "high":
        return True
    if mode in ("solo", "delegate") and assurance == "standard":
        return False
    return None


def validate_route_invariants(state) -> "list[str]":
    """校验 route 跨字段不变量（H2/P0-2：route 与 review/executor/
    ownership/verification 的关系成为确定性约束），返回中文错误列表
    （空列表 = 合法；不抛异常）。

    四条规则（均在涉及字段为合法枚举值时生效——非法值已有基线枚举
    错误，不重复报；route 非 dict 或 mode 非法 → 返回空列表）：
      - R1 矩阵一致性：mode 必须等于 ROUTE_MATRIX[delegability][assurance]；
      - R2 executor 绑定：solo/audit ↔ "main"；delegate/full ↔ 实施者；
      - R3 review 绑定：derive_review_required 为 True 时 review.required
        必须为 True（review 块缺失按非 True 处理）；
      - R4 delegate/full 实质性：ownership.files 与 verification.required
        必须为非空数组。
    """
    if not isinstance(state, dict):
        return []
    route = state.get("route")
    if not isinstance(route, dict):
        return []
    mode = route.get("mode")
    if mode not in ROUTE_MODES:
        return []
    errors = []

    delegability = route.get("delegability")
    assurance = route.get("assurance")
    # R1 矩阵一致性（delegability 与 assurance 均为合法枚举时才判）
    if delegability in DELEGABILITY_LEVELS and assurance in ASSURANCE_LEVELS:
        expected = ROUTE_MATRIX[delegability][assurance]
        if mode != expected:
            errors.append(
                "route.mode %r 与 delegability=%r / assurance=%r "
                "的矩阵组合不一致（应为 %r）"
                % (mode, delegability, assurance, expected))

    # R2 executor 绑定（executor 为合法枚举值时才判；None / 非法值跳过）
    executor = route.get("executor")
    if executor in EXECUTORS:
        if mode in ("solo", "audit"):
            if executor != "main":
                errors.append(
                    "route.executor %r 与 mode=%r 不一致"
                    "（solo/audit 要求 executor 为 \"main\"）"
                    % (executor, mode))
        elif executor not in IMPLEMENTER_EXECUTORS:
            errors.append(
                "route.executor %r 与 mode=%r 不一致"
                "（delegate/full 要求 executor 为 %s 之一）"
                % (executor, mode, " / ".join(IMPLEMENTER_EXECUTORS)))

    # R3 review 绑定：route 推导要求审查时 review.required 必须为 True
    if derive_review_required(route) is True:
        review = state.get("review")
        required = review.get("required") if isinstance(review, dict) else None
        if required is not True:
            errors.append(
                "review.required 必须为 true：route.mode=%r / "
                "route.assurance=%r 推导出独立审查义务——该路由要求 "
                "review.required=true" % (mode, assurance))

    # R4 delegate/full 实质性：必须声明非空 ownership 与 verification
    if mode in ("delegate", "full"):
        ownership = state.get("ownership")
        files = (
            ownership.get("files") if isinstance(ownership, dict) else None)
        if not isinstance(files, list) or not files:
            errors.append(
                "route.mode=%r 要求 ownership.files 为非空数组"
                "（delegate/full 任务必须声明实质文件范围）" % mode)
        verification = state.get("verification")
        required_commands = (
            verification.get("required")
            if isinstance(verification, dict) else None)
        if not isinstance(required_commands, list) or not required_commands:
            errors.append(
                "route.mode=%r 要求 verification.required 为非空数组"
                "（delegate/full 任务必须声明验证命令）" % mode)

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
    """校验 review 子对象（required 布尔 + reviewer/verdict 可空枚举 + fingerprint）。"""
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
    fingerprint = review.get("fingerprint")
    if fingerprint is not None and not isinstance(fingerprint, str):
        errors.append("review.fingerprint 必须是字符串或 null")
    return errors


def _validate_visual_evidence(entries):
    """校验 visual_evidence 顶层数组（§22 视觉证据）。

    值必须是 list；每项必须是 dict 且含非空 str 的 path 与 sha256
    两键（其他键忽略，向前兼容）。
    """
    if not isinstance(entries, list):
        return ["visual_evidence 必须是数组"]
    errors = []
    for index, item in enumerate(entries):
        if not isinstance(item, dict):
            errors.append("visual_evidence[%d] 必须是 JSON 对象" % index)
            continue
        path = item.get("path")
        if not isinstance(path, str) or path == "":
            errors.append("visual_evidence[%d].path 必须是非空字符串" % index)
        sha256 = item.get("sha256")
        if not isinstance(sha256, str) or sha256 == "":
            errors.append("visual_evidence[%d].sha256 必须是非空字符串" % index)
    return errors


def _validate_dispatch_waves(waves) -> "list[str]":
    """校验可选 dispatch.waves 数组（v2.1 M4 wu-21-08 wave 记录）。

    键名冻结：wave_id（非空 str 且列表内唯一）/ units（非空字符串
    数组）/ worker_budget（>= 1 整数）/ quota_status（四态）/
    created_at（非空 str，ISO-8601 落盘口径）/ status（active|closed）/
    closed_at（非空 str 或 None；status="closed" 时必须非 None）。
    缺 waves 键 = legacy 合法（调用方把关）；逐条聚合全部错误不短路，
    错误消息中文、前缀 dispatch.waves[i]。
    """
    if not isinstance(waves, list):
        return ["dispatch.waves 必须是数组"]
    errors = []
    seen_wave_ids = set()
    for index, wave in enumerate(waves):
        prefix = "dispatch.waves[%d]" % index
        if not isinstance(wave, dict):
            errors.append("%s 必须是 JSON 对象" % prefix)
            continue
        for key in ("wave_id", "units", "worker_budget", "quota_status",
                    "created_at", "status", "closed_at"):
            if key not in wave:
                errors.append("%s 缺少必填键 %s" % (prefix, key))
        if "wave_id" in wave:
            wave_id = wave["wave_id"]
            if not isinstance(wave_id, str) or wave_id == "":
                errors.append("%s.wave_id 必须是非空字符串" % prefix)
            elif wave_id in seen_wave_ids:
                errors.append(
                    "%s.wave_id %r 重复（wave_id 必须在列表内唯一）"
                    % (prefix, wave_id))
            else:
                seen_wave_ids.add(wave_id)
        if "units" in wave:
            units = wave["units"]
            if not isinstance(units, list) or not units:
                errors.append("%s.units 必须是非空数组" % prefix)
            else:
                errors.extend(
                    "%s.units[%d] 必须是非空字符串" % (prefix, u_index)
                    for u_index, item in enumerate(units)
                    if not isinstance(item, str) or item == "")
        if "worker_budget" in wave:
            worker_budget = wave["worker_budget"]
            # bool 是 int 的子类，但 True/False 不应充当 worker_budget
            if isinstance(worker_budget, bool) \
                    or not isinstance(worker_budget, int) or worker_budget < 1:
                errors.append("%s.worker_budget 必须是 >= 1 的整数" % prefix)
        if "quota_status" in wave:
            quota_status = wave["quota_status"]
            if quota_status not in QUOTA_STATUSES:
                errors.append(_enum_error(prefix + ".quota_status",
                                          quota_status, QUOTA_STATUSES))
        if "created_at" in wave:
            created_at = wave["created_at"]
            if not isinstance(created_at, str) or created_at == "":
                errors.append(
                    "%s.created_at 必须是非空字符串（ISO-8601）" % prefix)
        if "status" in wave:
            status = wave["status"]
            if status not in ("active", "closed"):
                errors.append(_enum_error(prefix + ".status", status,
                                          ("active", "closed")))
        if "closed_at" in wave:
            closed_at = wave["closed_at"]
            if closed_at is not None and (
                    not isinstance(closed_at, str) or closed_at == ""):
                errors.append(
                    "%s.closed_at 必须是非空字符串（ISO-8601）或 null"
                    % prefix)
            if wave.get("status") == "closed" and (
                    not isinstance(closed_at, str) or closed_at == ""):
                errors.append(
                    "%s.closed_at 与 status=\"closed\" 矛盾：closed 波必须"
                    "携带 closed_at" % prefix)
    return errors


def _validate_dispatch(dispatch):
    """校验 dispatch 子对象（max_workers 为 1-§82 上限的整数 + active 数组
    + 可选 waves 数组，wu-21-08）。"""
    if not isinstance(dispatch, dict):
        return ["dispatch 必须是 JSON 对象"]
    from runtime.lease import DEFAULT_MAX_WORKERS_LIMIT
    errors = []
    max_workers = dispatch.get("max_workers", 1)
    # bool 是 int 的子类，但 True/False 不应充当 max_workers
    if isinstance(max_workers, bool) or not isinstance(max_workers, int) \
            or max_workers < 1:
        errors.append("dispatch.max_workers 必须是 >= 1 的整数")
    elif max_workers > DEFAULT_MAX_WORKERS_LIMIT:
        errors.append(
            "dispatch.max_workers 超过并行上限 %d（§82，1-%d）"
            % (DEFAULT_MAX_WORKERS_LIMIT, DEFAULT_MAX_WORKERS_LIMIT))
    if not isinstance(dispatch.get("active", []), list):
        errors.append("dispatch.active 必须是数组")
    # 可选 waves 键（wu-21-08 wave 记录）：缺键 = legacy 合法
    if "waves" in dispatch:
        errors.extend(_validate_dispatch_waves(dispatch["waves"]))
    return errors


def _validate_quota(quota):
    """校验可选顶层 quota 块（§39，B5.3 增补；四态评估见
    runtime.quota.scheduler）。

    只做形状校验：quota 块按契约永不含凭证（§37 凭证零落盘），
    本函数因此不校验任何秘密字段的存在性，内部细粒度形状
    （five_hour / weekly 的窗口字段）也留给 quota 子系统。
      - status 存在且非 None → ∈ QUOTA_STATUSES（词汇复用
        runtime.quota.parser，与调度器共用同一词汇表）；
      - source / provider / last_checked 存在且非 None → 非空 str；
      - five_hour / weekly 存在且非 None → dict。
    """
    if not isinstance(quota, dict):
        return ["quota 必须是 JSON 对象"]
    errors = []
    status = quota.get("status")
    if status is not None and status not in QUOTA_STATUSES:
        errors.append(_enum_error("quota.status", status, QUOTA_STATUSES))
    for key in ("source", "provider", "last_checked"):
        value = quota.get(key)
        if value is not None and (not isinstance(value, str) or value == ""):
            errors.append("quota.%s 必须是非空字符串或 null" % key)
    for key in ("five_hour", "weekly"):
        value = quota.get(key)
        if value is not None and not isinstance(value, dict):
            errors.append("quota.%s 必须是 JSON 对象或 null" % key)
    return errors


def _validate_repository(repository):
    """校验可选顶层 repository 块（RB-2 任务专属仓库根绑定）。

    只做形状校验：
      - repository 非 dict → 错误；
      - root 缺失 / 非 str / 空串 → 错误（绑定后 root 是唯一必填子键）；
      - 未知子键忽略（向前兼容）。
    无 repository 键 → 完全合法（legacy 任务无绑定是合法形态，
    validate_state 不要求绑定存在）。
    """
    if not isinstance(repository, dict):
        return ["repository 必须是 JSON 对象"]
    root = repository.get("root")
    if not isinstance(root, str) or root == "":
        return ["repository.root 必须是非空字符串"]
    return []


def validate_state(state) -> "list[str]":
    """校验状态 dict，返回错误消息列表（中文，含字段路径）；空列表 = 合法。

    不抛异常；state 非 dict → ["state 必须是 JSON 对象"]。
    未知顶层键忽略（向前兼容），不报错。
    可选顶层 repository 块（RB-2）：存在时必须为 dict 且 root 为非空
    字符串；缺失时完全合法（legacy 无绑定形态）。
    可选顶层 execution_policy 块（v2.1 M1 授权事实源）：存在时必须为
    dict 且复用 validate_execution_policy（§3 冻结 schema + §5.4
    授权不变量，错误路径前缀 execution_policy.）；缺失时完全合法
    （legacy 保守默认形态，R7）。
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

    # 规则 6.5：visual_evidence（§22 视觉证据数组）
    if "visual_evidence" in state:
        errors.extend(_validate_visual_evidence(state["visual_evidence"]))

    # 规则 7：work_units 必须是 list，且逐项按 §61 契约校验
    # （runtime.work_unit.validate_work_unit；错误路径前缀
    # work_units[i]，聚合全部错误不短路）
    if "work_units" in state:
        work_units = state["work_units"]
        if not isinstance(work_units, list):
            errors.append("work_units 必须是数组")
        else:
            for index, unit in enumerate(work_units):
                errors.extend(
                    "work_units[%d].%s" % (index, unit_error)
                    for unit_error in validate_work_unit(unit))

    # 规则 8：dispatch
    if "dispatch" in state:
        errors.extend(_validate_dispatch(state["dispatch"]))

    # 规则 8.5：quota（§39 可选顶层 quota 块；永不含有凭证字段）
    if "quota" in state:
        errors.extend(_validate_quota(state["quota"]))

    # 规则 8.6：repository（RB-2 可选顶层仓库根绑定；无该键完全合法
    # ——legacy 形态，存在时 root 是唯一必填子键）
    if "repository" in state:
        errors.extend(_validate_repository(state["repository"]))

    # 规则 8.7：execution_policy（v2.1 M1 可选顶层授权事实源块；无该键
    # 完全合法——legacy 保守默认形态，消费方按 default_execution_policy
    # 解释；存在时复用 runtime.execution_policy 全量校验（§3 冻结
    # schema + §5.4 授权不变量），错误路径前缀 execution_policy.，
    # 聚合不短路——与规则 7 的 work_units[i] 前缀同风格）
    if "execution_policy" in state:
        policy_block = state["execution_policy"]
        if not isinstance(policy_block, dict):
            errors.append("execution_policy 必须是 JSON 对象")
        else:
            errors.extend(
                "execution_policy.%s" % policy_error
                for policy_error in validate_execution_policy(policy_block))

    # 规则 9：status ∈ TASK_STATUSES
    if "status" in state:
        status = state["status"]
        if status not in TASK_STATUSES:
            errors.append(_enum_error("status", status, TASK_STATUSES))

    # 规则 11：route 不变量（H2/P0-2 跨字段一致性：矩阵 / executor /
    # review / delegate-full 实质性绑定；非法组合在 save_state 即被拒）
    errors.extend(validate_route_invariants(state))

    return errors


# —— 构造 ——

def new_task_state(task_id, goal, route, *, ownership_files=(),
                   verification_required=(), review_required=None,
                   reviewer=None, status="created",
                   repository_root=None) -> dict:
    """构造带默认值的完整状态 dict。

    只做构造不做校验（调用方负责 validate_state）。route 接受 dict，
    含 mode / delegability / assurance / executor / continuity 五键，
    缺键时对应值填 None（mode 缺失会导致 validate_state 报 route.mode 错）；
    route 非 dict 时抛 TypeError。

    review_required 缺省 None 时由 route 推导审查义务
    （derive_review_required）：audit / full 或 assurance=high → True，
    solo / delegate + standard → False，信息不足落 False；显式 True/False
    照传（显式 False + 派生 True 的组合由 validate_route_invariants 规则
    R3 在保存时拒绝；显式 True 恒合法——比推导更严）。

    repository_root（RB-2，关键字专用）：非 None 时构造期绑定任务专属
    Git 仓库根（经 bind_repository_root 归一为绝对路径写入顶层
    "repository" 块）；None（缺省）→ 整键省略（legacy 无绑定形态）。
    非法输入（空串 / 非路径类型）抛 ValueError。

    v2.1 M1：构造结果恒含顶层 "execution_policy" 默认块
    （runtime.execution_policy.default_execution_policy() 的保守
    默认——授权事实源的初始形状；升档经 set_*_authorization 变换）。

    v2.1 §11.5（wu-21-09）：dispatch.max_workers 默认 1 → 2——与
    dispatcher.DEFAULT_MAX_WORKERS=2、execution_policy parallelism
    默认块（default_workers=max_workers=2）三处口径一致；真实并发
    预算另按 quota 四态经 execution_policy.effective_worker_budget
    折算（§12 表，接线在 task_manager._effective_worker_cap）。
    """
    if not isinstance(route, dict):
        raise TypeError(
            "route 必须是 dict（SELECTIVE ROUTE 五字段），得到 %s"
            % type(route).__name__)
    if review_required is None:
        derived = derive_review_required(route)
        review_required = False if derived is None else derived
    st = {
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
            "fingerprint": None,
        },
        "visual_evidence": [],
        "work_units": [],
        "dispatch": {"max_workers": 2, "active": []},
        "status": status,
        # v2.1 M1：执行策略授权事实源（§3 冻结 schema 的保守默认块；
        # 授权升档经 execution_policy.set_*_authorization 变换后写入）
        "execution_policy": default_execution_policy(),
    }
    if repository_root is not None:
        bind_repository_root(st, repository_root)
    return st


# —— 任务仓库根绑定（RB-2，release hardening） ——

def bind_repository_root(st, repo_root) -> dict:
    """为任务绑定专属 Git 仓库根（RB-2），就地写入并返回同一 dict。

    - 归一口径：root 恒存为 str(Path(repo_root).resolve())（相对路径
      → 绝对路径；Windows 反斜杠原样保留，JSON 转义由落盘层负责）。
      先 os.path.abspath 后 resolve：Python 3.7 的 Windows resolve()
      对不存在的相对路径不做绝对化（3.8 起 bpo-37834 才修复），abspath
      前置保证「相对 → 绝对」在 3.7/3.8+ 行为一致；对已存在的绝对路径
      两者完全等价（resolve 仍做符号链接归一）。
    - repository 块缺失 / 形状异常时按需重建（与 record_* 助手同风格）；
    - 非法输入（空串、非 str/Path 路径类型）→ ValueError（先校验后
      修改，失败零副作用）；
    - 调用方负责 save_state（本函数不触碰磁盘）。

    绑定语义：root 是该任务全部 git 操作（touched 清单 / 基线修订 /
    证据指纹 / 视觉证据哈希）的求值根；账本（state.json / events.jsonl
    / 租约）不跟随迁移，恒在账本根——两根分离由调用方（Stop 完成门 /
    task_manager）按 resolve_repository_root 消费。
    """
    if isinstance(repo_root, pathlib.Path):
        resolved = pathlib.Path(os.path.abspath(str(repo_root))).resolve()
    elif isinstance(repo_root, str) and repo_root != "":
        resolved = pathlib.Path(os.path.abspath(repo_root)).resolve()
    else:
        raise ValueError(
            "bind_repository_root：repo_root 必须是非空字符串路径，得到 %r"
            % (repo_root,))
    repository = st.get("repository")
    if not isinstance(repository, dict):
        repository = {}
        st["repository"] = repository
    repository["root"] = str(resolved)
    return st


def bound_repository_root(task_state):
    """读取任务绑定的专属仓库根（RB-2），返回字符串或 None。

    合法绑定（repository.root 为非空 str）→ 返回该字符串；任务未绑定 /
    repository 缺失或非 dict / root 缺失或形状非法 → None。本函数是
    容错读（手写 state.json 的形状异常不炸消费方——门 / task_manager
    据此走 legacy 回退或降级），形状纠错归 validate_state。
    """
    if not isinstance(task_state, dict):
        return None
    repository = task_state.get("repository")
    if not isinstance(repository, dict):
        return None
    root = repository.get("root")
    if isinstance(root, str) and root != "":
        return root
    return None


def resolve_repository_root(task_state, fallback_root) -> str:
    """解析任务的生效仓库根（RB-2）：绑定优先，未绑定回退 fallback_root。

    fallback_root 通常是账本根（任务账本所在目录）——legacy 任务（无
    repository 绑定）在其上求值 git，行为与单仓时代完全一致；绑定任务
    返回其归一后的绑定根（原字符串，不做二次解析）。
    """
    bound = bound_repository_root(task_state)
    return fallback_root if bound is None else bound


# —— 指纹 / 证据写入助手（纯 dict 变换，不触碰磁盘） ——

def record_verification(st, command, fingerprint=None) -> dict:
    """记录一条已执行验证命令（可选绑定证据指纹），就地修改并返回同一 dict。

    - command 必须是非空 str，fingerprint 必须是 None 或非空 str，
      否则 ValueError（先全量校验参数，再修改，失败不产生副作用）；
    - command 追加进 verification.completed（已存在则不重复追加，
      保持既有顺序）；verification 子 dict 或 completed list 缺失 /
      形状异常时按需重建；
    - fingerprint 非 None 时更新 verification.fingerprint；None 表示
      「本次不更新指纹」，保留旧值。
    调用方负责 save_state（本函数不触碰磁盘）。
    """
    if not isinstance(command, str) or command == "":
        raise ValueError("record_verification：command 必须是非空字符串")
    if fingerprint is not None and (
            not isinstance(fingerprint, str) or fingerprint == ""):
        raise ValueError(
            "record_verification：fingerprint 必须是 None 或非空字符串")
    verification = st.get("verification")
    if not isinstance(verification, dict):
        verification = {}
        st["verification"] = verification
    completed = verification.get("completed")
    if not isinstance(completed, list):
        completed = []
        verification["completed"] = completed
    if command not in completed:
        completed.append(command)
    if fingerprint is not None:
        verification["fingerprint"] = fingerprint
    return st


def record_review(st, verdict, fingerprint=None) -> dict:
    """记录审查裁决（可选绑定证据指纹），就地修改并返回同一 dict。

    - verdict 必须在 REVIEW_VERDICTS 内，fingerprint 必须是 None 或
      非空 str，否则 ValueError（先全量校验参数，再修改，失败不产生
      副作用）；
    - 更新 review.verdict；fingerprint 非 None 时更新 review.fingerprint
      （None 表示「本次不更新指纹」，保留旧值）；
    - review 子 dict 缺失 / 形状异常时按需重建，含 required=False、
      reviewer=None 默认值（此时 required 语义由调用方后续负责）。
    调用方负责 save_state（本函数不触碰磁盘）。
    """
    if verdict not in REVIEW_VERDICTS:
        raise ValueError(
            "record_review：verdict %r 不在合法取值内（%s）"
            % (verdict, ", ".join(REVIEW_VERDICTS)))
    if fingerprint is not None and (
            not isinstance(fingerprint, str) or fingerprint == ""):
        raise ValueError(
            "record_review：fingerprint 必须是 None 或非空字符串")
    review = st.get("review")
    if not isinstance(review, dict):
        review = {"required": False, "reviewer": None}
        st["review"] = review
    review["verdict"] = verdict
    if fingerprint is not None:
        review["fingerprint"] = fingerprint
    return st


def record_visual_evidence(st, path, sha256) -> dict:
    """记录一条视觉证据（§22：按文件自身 sha256），就地修改并返回同一 dict。

    - path / sha256 均必须是非空 str，否则 ValueError（先全量校验
      参数，再修改，失败不产生副作用）；
    - 向 visual_evidence 追加 {"path": ..., "sha256": ...}；同 path
      已存在 → 就地替换该项的 sha256（不追加第二条）；顶层键缺失 /
      形状异常时按需重建为空 list。
    调用方负责 save_state（本函数不触碰磁盘）。
    """
    if not isinstance(path, str) or path == "":
        raise ValueError("record_visual_evidence：path 必须是非空字符串")
    if not isinstance(sha256, str) or sha256 == "":
        raise ValueError("record_visual_evidence：sha256 必须是非空字符串")
    entries = st.get("visual_evidence")
    if not isinstance(entries, list):
        entries = []
        st["visual_evidence"] = entries
    for entry in entries:
        if isinstance(entry, dict) and entry.get("path") == path:
            entry["sha256"] = sha256
            return st
    entries.append({"path": path, "sha256": sha256})
    return st


# —— 保存 / 读取 / 发现 ——

def _transition_errors(previous, new_status, *, _gate_commit=False):
    """按 TASK_TRANSITIONS 校验一次 status 迁移，非法时抛 ValueError。

    规则（save_state 与 transition_task_status 共用）：
      - 新 status 为 completed 且非完成门内部通道（_gate_commit=False）
        → 无条件拒绝（completed 只能由 Stop 完成门提交，请将状态置为
        finalizing 请求完成）；
      - 完成门内部通道（_gate_commit=True）要求盘上旧 status 恰为
        finalizing（首存无盘上状态，同样拒绝）；
      - 盘上旧 status（previous 为 None = 首存，无转换可言）与新 status
        不同 → 新 status 必须在 TASK_TRANSITIONS.get(旧 status, ()) 内；
        终态无表项，任何变化自然拒绝；旧 == 新（无转换）放行。
    previous 为盘上已加载的状态 dict（不存在为 None）；其 JSON 损坏由
    load_state 抛 ValueError，自然向上传播，不覆盖损坏文件。
    """
    old_status = previous.get("status") if previous is not None else None
    if new_status == "completed" and not _gate_commit:
        raise ValueError(
            "status 转换被拒绝：%r 不经完成门不得写入（completed 只能由 "
            "Stop 完成门提交，请将状态置为 finalizing 请求完成）" % new_status)
    if _gate_commit and old_status != "finalizing":
        raise ValueError(
            "完成门提交被拒绝：_gate_commit 要求盘上旧 status 为 finalizing，"
            "得到 %r（completed 只能由 Stop 完成门对 finalizing 任务提交）"
            % old_status)
    if previous is None:
        return  # 首存：无盘上旧状态，无转换可言
    if old_status != new_status:
        allowed = TASK_TRANSITIONS.get(old_status, ())
        if new_status not in allowed:
            targets = ", ".join(allowed) if allowed else "无（终态不接受任何转换）"
            raise ValueError(
                "status 转换被拒绝：%r → %r 不在合法转换内"
                "（%s 的合法目标：%s；完整转换表见 TASK_TRANSITIONS）"
                % (old_status, new_status, old_status, targets))


def save_state(repo_root, state, *, task_id=None, _gate_commit=False) -> pathlib.Path:
    """校验并原子保存状态文件，返回最终路径。

    - validate_state 有错误 → ValueError（错误清单拼接）；
    - 写入路径的 task_id 取参数 task_id，否则取 state["task_id"]；
    - 目录/内容一致性（P1-9）：显式 task_id 参数非 None 时必须等于
      state["task_id"]，不一致 → ValueError（目录与内容的任务标识
      必须一致；如需迁移请以内容 ID 为准重建目录）；
    - 状态转换门（P0-1）：写盘前读盘上现有 state.json（不存在视为首存），
      status 变化必须落在 TASK_TRANSITIONS 内；completed 一律不接受公共
      写入——只能由 Stop 完成门以内部通道提交（_gate_commit=True，且仅
      对盘上 status == finalizing 的任务）；盘上文件损坏（load_state 抛
      ValueError）自然向上传播，不覆盖损坏文件；
    - 目录不存在自动创建；
    - 原子写：先写同目录 <name>.tmp（UTF-8、ensure_ascii=False、缩进 2），
      再 os.replace 覆盖；任何失败路径清理 tmp，成功后确保 tmp 不存在。

    _gate_commit 为私有参数：仅供 Stop 完成门钩子（hooks/stop_gate.py，
    经 state.commit_completion）使用，其他调用方不得传。
    """
    errors = validate_state(state)
    if errors:
        raise ValueError("state 非法，无法保存：%s" % "；".join(errors))
    tid = task_id if task_id is not None else state["task_id"]
    # P1-9：目录 task_id 与内容 task_id 必须一致——显式 task_id 与
    # state["task_id"] 不一致即拒绝，防止任务目录与 state.json 内容错位
    if task_id is not None and task_id != state.get("task_id"):
        raise ValueError(
            "save_state：目录 task_id %r 与 state.task_id %r 不一致"
            "（P1-9：目录与内容的任务标识必须一致；如需迁移请以内容 ID "
            "为准重建目录）" % (task_id, state.get("task_id")))
    path = state_path(repo_root, tid)
    # 转换门先于落盘：盘上损坏文件在此抛 ValueError，不会被覆盖
    _transition_errors(load_state(repo_root, tid), state.get("status"),
                       _gate_commit=_gate_commit)
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


def commit_completion(repo_root, task_id) -> dict:
    """完成门专用：把 finalizing 任务原子提交为 completed，返回提交后状态。

    仅 Stop 完成门钩子在四重检查全部通过后调用：
      - load_state 缺失（None）→ ValueError；JSON 损坏由 load_state 抛
        ValueError，自然向上传播；
      - 盘上 status 非 finalizing → ValueError（finalizing 是唯一完成
        请求态，completed 只能从它提交）；
      - 内部经 save_state(..., _gate_commit=True) 落盘（唯一的 completed
        写入通道）。
    本函数不写 journal——completed 审计事件由钩子在提交成功后追加。
    """
    st = load_state(repo_root, task_id)
    if st is None:
        raise ValueError(
            "commit_completion：任务 %s 不存在（无 state.json），无法提交完成"
            % task_id)
    if st.get("status") != "finalizing":
        raise ValueError(
            "commit_completion：任务 %s 的 status 为 %r 而非 finalizing，"
            "拒绝提交 completed（先进入 finalizing 请求完成）"
            % (task_id, st.get("status")))
    st["status"] = "completed"
    save_state(repo_root, st, _gate_commit=True)
    return st


def transition_task_status(repo_root, task_id, new_status) -> dict:
    """按 TASK_TRANSITIONS 把任务状态迁移到 new_status，返回迁移后的状态。

    公共运行时迁移入口（如收尾时进入 finalizing 请求完成）：
      - load_state 缺失（None）→ ValueError；JSON 损坏自然向上传播；
      - 迁移校验与 save_state 同规则（终态无表项、completed 无条件拒绝
        ——completed 只能由完成门提交）；
      - 成功后向任务 journal 追加一条 status_changed 事件
        （{"event": "status_changed", "from": 旧, "to": 新}），journal
        在函数内 import（与钩子的延迟 import 风格一致）。
    """
    from runtime import journal

    st = load_state(repo_root, task_id)
    if st is None:
        raise ValueError(
            "transition_task_status：任务 %s 不存在（无 state.json），"
            "无法迁移状态" % task_id)
    old_status = st.get("status")
    _transition_errors(st, new_status)
    st["status"] = new_status
    save_state(repo_root, st)
    journal.append_event(
        repo_root, task_id,
        {"event": "status_changed", "from": old_status, "to": new_status})
    return st


# corrupt 分类的 reason 长度上限（异常消息简写，避免超长 JSON 错误
# 撑爆 stderr / journal 记录）
_CORRUPT_REASON_LIMIT = 80


def _short_reason(text) -> str:
    """把异常消息简写为不超过 _CORRUPT_REASON_LIMIT 字符的中文原因。"""
    text = str(text)
    if len(text) > _CORRUPT_REASON_LIMIT:
        return text[:_CORRUPT_REASON_LIMIT]
    return text


def discover_tasks(repo_root) -> dict:
    """扫描 tasks_root 下全部子目录，返回任务四分类 dict（H3/P0-3）。

    返回结构（四个键恒存在，桶内条目均为 (目录名, reason) 二元组，
    按目录名排序）：
      - "active"：state.json 可读且 status 非终态（reason 为 status 字符串）；
      - "terminal"：status ∈ TERMINAL_STATUSES（reason 为 status 字符串）；
      - "corrupt"：state.json 存在但 JSON 损坏 / 任务标识归一失败
        （ValueError）或读取时 OSError——reason 为异常消息简写
        （「state.json 损坏：…」/「state.json 不可读：…」，截断到
        80 字符内）；
      - "orphaned"：目录存在但无 state.json（reason 为「无 state.json」）。

    发现完整性语义：损坏的 state.json 不再被解释成「没有任务」——
    corrupt 与 orphaned 都被显式报告，由调用方（Stop 完成门）决定
    拦截或降级。tasks_root 不存在 → 四桶全空；tasks_root 下的非目录
    项跳过；单目录解析失败不中断扫描（健壮性优先）。
    """
    discovery = {"active": [], "terminal": [], "corrupt": [], "orphaned": []}
    root = tasks_root(repo_root)
    if not root.is_dir():
        return discovery
    for entry in sorted(root.iterdir(), key=lambda item: item.name):
        if not entry.is_dir():
            continue
        try:
            loaded = load_state(repo_root, entry.name)
        except ValueError as exc:
            discovery["corrupt"].append(
                (entry.name, _short_reason("state.json 损坏：%s" % exc)))
            continue
        except OSError as exc:
            discovery["corrupt"].append(
                (entry.name, _short_reason("state.json 不可读：%s" % exc)))
            continue
        if loaded is None:
            discovery["orphaned"].append((entry.name, "无 state.json"))
            continue
        status = loaded.get("status")
        if status in TERMINAL_STATUSES:
            discovery["terminal"].append((entry.name, status))
        else:
            discovery["active"].append((entry.name, status))
    return discovery


def find_active_tasks(repo_root) -> "list[str]":
    """兼容 helper：返回全部非终态活动任务的 task_id（按目录名排序）。

    H3（P0-3）起 Stop 完成门改用 discover_tasks() 四分类发现（损坏目录
    不再静默消失，见其 docstring），本函数保留为既有调用方的兼容出口
    ——实现直接取 discover_tasks 的 active 桶目录名，输出与四分类引入前
    完全一致：单个任务目录解析失败（JSON 损坏 / 缺 task_id / 无
    state.json / 读取时 OSError）不进入 active，也不中断扫描。
    """
    return [name for name, _status in discover_tasks(repo_root)["active"]]
