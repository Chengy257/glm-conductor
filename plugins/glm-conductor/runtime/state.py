#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GLM Conductor v2.4 任务运行时状态层（state.json，Phase 2 P2-A 重基线）。

职责：
    管理 v2.4 任务的确定性状态文件 `.glm-conductor/tasks/<task-id>/state.json`：
      - 定位：tasks_root / task_dir / state_path 三个纯路径函数（签名
        与 v2.3 完全一致，构成 20 个导入者的导入安全边界）；
      - 构造：new_task_state() 构造 v2.4 完整状态 dict（只构造不校验，
        调用方负责 validate_state）；
      - 校验：validate_state() 返回中文错误列表（空列表 = 合法，不抛
        异常）；dag 数组逐节点经 runtime.work_unit.validate_node 校验
        （错误前缀 dag[i].），整图经 runtime.dependency.graph_errors
        校验（nodes[i] 路径统一改写为 dag[i] 路径）；
      - 保存 / 读取：save_state() 先校验再经 runtime.durable_io 原子写
        （每次调用唯一临时名 + os.replace + PermissionError 有界重试），
        并按 v2.4 顶层状态转换表（TASK_TRANSITIONS）拒绝非法迁移——
        终态不得静默重开；load_state() 读取并归一任务标识，JSON 损坏
        抛 ValueError 不静默；
      - 迁移 / 重开：transition_task_status() 公共迁移入口（记
        status_changed 事件）；reopen_task() 显式重开通道——终态重开
        只能走它，必须落 task_reopened 事件，绝不静默改写；
      - 发现：discover_tasks() 对 tasks_root 全部子目录四分类
        （active / terminal / corrupt / orphaned）；find_active_tasks()
        保留为兼容 helper；
      - 仓库根绑定（RB-2）：bind_repository_root() 写入 /
        new_task_state(repository_root=...) 构造期绑定 /
        bound_repository_root() 容错读 / resolve_repository_root()
        解析生效根（绑定优先，缺省回退账本根）——签名与 v2.3 一致；
      - 遗留检测：is_legacy_state() / detect_legacy_task() 只读识别
        v2.3 遗留任务。

v2.4 顶层概念（schema 权威清单）：
    task_id / goal / repository / route / dag / status / phase（可选
    描述性标注）/ workflow_run_id（可空，委派运行启动前为 null）/
    validation / review / quota_resume（P3-B 定稿的配额恢复授权块）。
    validation 与 review 是任务级唯一记录（无逐单元证据）。

durable status 恰为七态：
    active / waiting_quota / waiting_user / blocked / completed /
    cancelled / failed；终态 = completed / cancelled / failed，终态
    不得静默重开（显式重开只能走 reopen_task，落 journal 记录）。
    phase 仅是描述性标注（planning / workflow / validating /
    reviewing），无转换矩阵——推进语义归任务生命周期层，不在本层
    做矩阵约束。

v2.3 遗留面处置（本模块 v2.4 重基线明确退役的形态，只识别不兼容）：
    v2.3 state.json 的 work_units 单元账本、permit / lease / receipt
    运行时字段，record_verification / record_review / visual_evidence
    逐命令证据面，以及 execution_policy / continuation /
    quota_subscription 授权块，全部不再是 v2.4 schema 概念（validate
    按缺键报错，绝不自动迁移、绝不伪造 v2.4 完成证据）。载入带这些
    标记的 state 时 is_legacy_state() 返回 True，消费方按
    LEGACY_STATE_GUIDANCE 报告「v2.3 任务：请在 2.3.x 下收尾或显式
    放弃」。（Phase 3 收口后 quota/continuity 等旧模块及其消费面
    ——含曾为本模块保留的 QUOTA_SUBSCRIPTION_MINIMUM_STATES 词汇
    常量——已整体删除，本模块不再为任何已删模块保留词汇。）

路径布局：
    <repo_root>/.glm-conductor/tasks/<task-id>/state.json
    与 runtime.journal 管理的 events.jsonl 同处一个任务目录，各管各的
    文件。

schema 来源：
    docs/roadmap/V2_4_PHASE_2_STATE_ASSURANCE_RETIREMENT_SPEC.md §2
    （任务形态 / 七态 / 终态重开 / 遗留处置）
    + docs/roadmap/V2_4_PHASE_2_WORKFLOW_EXECUTION_PLAN.md W1
    （单元规格：签名冻结 / dag 校验 / quota_resume 占位）。
    repository 文件仍是代码状态真相源，本文件只是运行时任务状态。

依赖：
    仅 Python 3 标准库（json / os / pathlib / re）
    + runtime.work_unit（静态节点 validate_node，Phase 1 P1-B；
      本模块单向导入它，它不导入本模块，无循环导入）
    + runtime.dependency（图校验 graph_errors，Phase 1 P1-B；同上）
    + runtime.durable_io（共享原子写原语 atomic_write_json；它不导入
      runtime 包内任何模块，无循环导入），
    零第三方依赖，`python3 -S` 可运行。
"""

import json
import os
import pathlib
import re

from runtime.dependency import graph_errors
from runtime.durable_io import atomic_write_json
from runtime.work_unit import validate_node

# —— 路径常量与定位 ——

# 仓库根下运行时目录名
TASKS_DIRNAME = ".glm-conductor"
# 任务状态文件名（位于 tasks/<task-id>/ 之下）
STATE_FILENAME = "state.json"

# —— 词汇表常量（枚举校验用）——

# SELECTIVE ROUTE 四模式（v2.4 route 词汇恰为此四值）
ROUTE_MODES = ("solo", "delegate", "audit", "full")
# route.assurance 两级（可选字段；completion 守卫据此决定独立审查义务）
ASSURANCE_LEVELS = ("standard", "high")
# 任务全生命周期状态（v2.4 恰为七态；不再有 created/preflight/... 等
# v2.3 阶段态，也不再需要 finalizing 完成请求态）
TASK_STATUSES = (
    "active", "waiting_quota", "waiting_user", "blocked",
    "completed", "cancelled", "failed")
# 终态：discover_tasks 归入 terminal 桶（find_active_tasks 不返回）；
# 终态不得静默重开（显式重开走 reopen_task，落 journal 记录）
TERMINAL_STATUSES = ("completed", "cancelled", "failed")
# 可选描述性 phase 词汇（无转换矩阵——推进语义归任务生命周期层）
TASK_PHASES = ("planning", "workflow", "validating", "reviewing")
# 任务级 validation.status 词汇（P2-C 最小验证记录）
VALIDATION_STATUSES = ("passed", "failed")
# 任务级 review.verdict 词汇（P2-C 最小审查记录；v2.3 的
# not-required / missing / stale 等裁决词汇随之退役）
REVIEW_VERDICTS = ("ship", "fix-first", "rethink")
# quota_resume.mode 词汇（P3-B 定稿：manual 默认 / auto 显式授权）。
# auto 不需要附加字段——授权由显式 authorize_quota_resume 调用记录，
# 不做推断；词汇外值（v2.3 的 manual/notify/auto_once/until_done
# 四模式词）一律拒绝
QUOTA_RESUME_MODES = ("manual", "auto")

# 顶层状态转换表：键 = 旧 status，值 = 允许的直接后继（终态无表项 =
# 不接受任何转换——终态不得静默重开，显式重开只能走 reopen_task）。
# save_state 与 transition_task_status 据此拒绝非法迁移；旧 == 新
# （无转换）放行。
TASK_TRANSITIONS = {
    "active": ("waiting_quota", "waiting_user", "blocked",
               "completed", "cancelled", "failed"),
    "waiting_quota": ("active", "waiting_user", "blocked",
                      "failed", "cancelled"),
    "waiting_user": ("active", "blocked", "failed", "cancelled"),
    "blocked": ("active", "waiting_quota", "waiting_user",
                "failed", "cancelled"),
}

# legacy 标识归一：v1.x checkpoint/状态用 CONTINUITY_ID，v2 起统一为
# task_id。v2.4 保留本归一（读取盘上既有 state.json 的容错面，签名不变）
TASK_ID_KEYS = ("task_id", "TASK_ID", "CONTINUITY_ID", "continuity_id")

# task_id 合法格式：字母数字开头，仅含字母数字与连字符
# （「语义前缀+随机后缀」机械唯一格式的落点约束）
_TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]*$")

# 必填顶层键（缺一即非法；未知顶层键忽略，向前兼容）。phase 是唯一
# 可选顶层键（缺省即不写键——描述性标注无 null 空档）。
_REQUIRED_TOP_KEYS = ("task_id", "goal", "repository", "route", "dag",
                      "status", "workflow_run_id", "validation", "review",
                      "quota_resume")

# —— v2.3 遗留标记（is_legacy_state 的检测面） ——

# v2.3 顶层集合键：work_units 单元账本 + permit/lease/receipt 三类
# 授权/凭证集合（出现任一即 v2.3 遗留 state）
_LEGACY_COLLECTION_KEYS = ("work_units", "permits", "leases", "receipts")
# v2.3 work_units 条目内的运行时字段（permit/lease/receipt 字段）
_LEGACY_UNIT_FIELD_KEYS = ("permit", "lease", "receipt")

# v2.3 遗留任务的一行处置指引（消费方原样向用户报告）
LEGACY_STATE_GUIDANCE = (
    "v2.3 遗留任务：请在 2.3.x 下收尾或显式放弃"
    "（v2.4 不自动迁移、不伪造 v2.4 完成证据）")


# —— quota_resume 块（P3-B 定稿语义） ——

# v2.4 顶层 quota_resume 块的冻结初始形状（恰为四键；P3-B 定稿额度
# 续跑授权语义。模块常量只读，default_quota_resume() 每次返回全新
# 拷贝，防止调用方改动波及本常量。auto 不需要附加字段；诊断性的
# 最近观测（enter_waiting_quota 记入）不在本常量内——缺省即无观测）
DEFAULT_QUOTA_RESUME = {
    "mode": "manual",
    "max_resumes": 0,
    "resume_count": 0,
    "automation_id": None,
}


def default_quota_resume() -> dict:
    """返回 quota_resume 块的全新拷贝。

    每次调用构造新 dict，调用方改写返回值不影响模块常量
    DEFAULT_QUOTA_RESUME；形状恰为 mode="manual" / max_resumes=0 /
    resume_count=0 / automation_id=None（P3-B 定稿初始态：未授权、
    零预算——恢复只能走显式授权 + 调用方确认通道）。
    """
    return dict(DEFAULT_QUOTA_RESUME)


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


# —— 遗留检测（只读，绝不迁移） ——

def detect_legacy_markers(raw) -> "list[str]":
    """返回 raw 携带的 v2.3 标记清单（空列表 = 无标记，即 v2.4 形态）。

    检测面（v2.4 重基线点名 markers）：
      - 顶层集合键：work_units / permits / leases / receipts 任一出现
        （记作 "key:<键名>"）；
      - work_units 条目内的 permit / lease / receipt 运行时字段
        （记作 "work_units[i].<字段>"）；
      - status 为非空字符串但不在 v2.4 七态词汇内（记作
        "status:<值>"——v2.3 阶段态 created/executing/... 或手写
        坏值均归遗留面，由消费方按遗留任务报告，validate_state 仍
        独立报枚举错误）。
    raw 非 dict → 空列表（归一失败归 normalize_task_id / validate_state）。
    """
    if not isinstance(raw, dict):
        return []
    markers = []
    for key in _LEGACY_COLLECTION_KEYS:
        if key in raw:
            markers.append("key:" + key)
    work_units = raw.get("work_units")
    if isinstance(work_units, list):
        for index, unit in enumerate(work_units):
            if not isinstance(unit, dict):
                continue
            for key in _LEGACY_UNIT_FIELD_KEYS:
                if key in unit:
                    markers.append("work_units[%d].%s" % (index, key))
    status = raw.get("status")
    if isinstance(status, str) and status != "" \
            and status not in TASK_STATUSES:
        markers.append("status:%s" % status)
    return markers


def is_legacy_state(raw) -> bool:
    """判断 state dict 是否携带 v2.3 标记（True = v2.3 遗留任务）。

    只读判定：work_units 键、permit/lease/receipt 字段或 v2.3 status
    词汇任一命中即为 True。本函数绝不迁移、绝不改写入参；载入侧
    （load_state）不做此判定——消费方（恢复渲染 / 完成守卫）载入后
    自行调用并在 True 时按 LEGACY_STATE_GUIDANCE 报告。
    """
    return bool(detect_legacy_markers(raw))


def detect_legacy_task(repo_root, task_id) -> "dict | None":
    """只读检测任务是否 v2.3 遗留（不写盘、不迁移、不改 state.json）。

    - 无 state.json → None；
    - JSON 损坏 → ValueError 原样上抛（load_state 不静默）；
    - 其余返回检测记录 dict（legacy 与否均返回）：
        {"task_id": 归一标识, "status": 状态值,
         "legacy": 是否 v2.3 遗留, "markers": v2.3 标记清单,
         "guidance": 遗留时的一行处置指引（LEGACY_STATE_GUIDANCE），
                     v2.4 形态为 None}
    """
    raw = load_state(repo_root, task_id)
    if raw is None:
        return None
    markers = detect_legacy_markers(raw)
    legacy = bool(markers)
    return {
        "task_id": raw.get("task_id"),
        "status": raw.get("status"),
        "legacy": legacy,
        "markers": markers,
        "guidance": LEGACY_STATE_GUIDANCE if legacy else None,
    }


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
    """校验 route 子对象（v2.4：mode 四模式必填；assurance 可选两级）。

    未知键忽略（向前兼容）；v2.3 的 delegability/executor/continuity
    路由矩阵字段不再是 schema 概念——带它们的 state 先经遗留检测
    识别，本函数不再为其维护矩阵不变量。
    """
    if not isinstance(route, dict):
        return ["route 必须是 JSON 对象"]
    errors = []
    mode = route.get("mode")
    if mode not in ROUTE_MODES:
        errors.append(_enum_error("route.mode", mode, ROUTE_MODES))
    if "assurance" in route and route["assurance"] is not None \
            and route["assurance"] not in ASSURANCE_LEVELS:
        errors.append(_enum_error("route.assurance", route["assurance"],
                                  ASSURANCE_LEVELS))
    return errors


def _validate_repository(repository):
    """校验必填顶层 repository 块（任务绑定的仓库根）。

    v2.4 中 repository 是必填概念：root 为唯一必填子键（非空 str）；
    未知子键忽略（向前兼容）。
    """
    if not isinstance(repository, dict):
        return ["repository 必须是 JSON 对象"]
    root = repository.get("root")
    if not isinstance(root, str) or root == "":
        return ["repository.root 必须是非空字符串"]
    return []


def _validate_dag(dag) -> "list[str]":
    """校验 dag 数组（静态节点经 Phase 1 校验器，错误带节点路径）。

    两层校验（聚合全部，不短路）：
      - 逐节点：runtime.work_unit.validate_node，错误统一加
        "dag[i]." 节点路径前缀（如 "dag[0].node.objective 必须是
        非空字符串"）；
      - 整图：runtime.dependency.graph_errors（重复 id / 缺失依赖 /
        自依赖 / 环），消息内 nodes[i] 路径统一改写为 dag[i]；不含
        节点下标的整图消息（如环成员清单）加 "dag." 前缀。
    dag 允许为空数组（solo 任务可暂无节点；是否可编译归编译器判定）。
    """
    if not isinstance(dag, list):
        return ["dag 必须是数组"]
    errors = []
    for index, node in enumerate(dag):
        errors.extend(
            "dag[%d].%s" % (index, node_error)
            for node_error in validate_node(node))
    for graph_error in graph_errors(dag):
        rewritten = graph_error.replace("nodes[", "dag[")
        if not rewritten.startswith("dag["):
            rewritten = "dag." + rewritten
        errors.append(rewritten)
    return errors


def _validate_validation(validation):
    """校验任务级 validation 记录（P2-C 最小验证记录）。

    键（存在才校验，缺键按「未记录」解释；未知键忽略）：
      - status：null 或 ∈ VALIDATION_STATUSES（passed/failed）；
      - change_id / summary：null 或非空 str；
      - commands：null 或非空字符串数组（允许空数组——诊断性命令
        摘要无「必填非空」语义）。
    """
    if not isinstance(validation, dict):
        return ["validation 必须是 JSON 对象"]
    errors = []
    if "status" in validation and validation["status"] is not None \
            and validation["status"] not in VALIDATION_STATUSES:
        errors.append(_enum_error("validation.status", validation["status"],
                                  VALIDATION_STATUSES))
    for key in ("change_id", "summary"):
        if key in validation:
            value = validation[key]
            if value is not None and (
                    not isinstance(value, str) or value == ""):
                errors.append("validation.%s 必须是非空字符串或 null" % key)
    if "commands" in validation and validation["commands"] is not None:
        errors.extend(
            _str_list_errors("validation.commands", validation["commands"]))
    return errors


def _validate_review(review):
    """校验任务级 review 记录（P2-C 最小审查记录）。

    键（存在才校验，缺键按「未记录」解释；未知键忽略）：
      - verdict：null 或 ∈ REVIEW_VERDICTS（ship/fix-first/rethink）；
      - reviewer / change_id / findings：null 或非空 str。
    """
    if not isinstance(review, dict):
        return ["review 必须是 JSON 对象"]
    errors = []
    if "verdict" in review and review["verdict"] is not None \
            and review["verdict"] not in REVIEW_VERDICTS:
        errors.append(_enum_error("review.verdict", review["verdict"],
                                  REVIEW_VERDICTS))
    for key in ("reviewer", "change_id", "findings"):
        if key in review:
            value = review[key]
            if value is not None and (
                    not isinstance(value, str) or value == ""):
                errors.append("review.%s 必须是非空字符串或 null" % key)
    return errors


def _validate_quota_resume(block):
    """校验 quota_resume 块（P3-B 定稿授权语义的收口闸）。

    键（存在才校验，缺键按 DEFAULT_QUOTA_RESUME 默认解释；未知键
    忽略——enter_waiting_quota 记入的诊断观测键照此透传）：
      - mode ∈ QUOTA_RESUME_MODES（manual / auto）；
      - max_resumes / resume_count 为 >= 0 的整数（bool 拒绝——bool
        是 int 子类，True/False 不得充当计数）；
      - 交叉不变量：resume_count 不得大于 max_resumes（两侧均为合法
        非负整数时才比对，形状错误已各自单独报告，不重复报）；
      - automation_id 为 null 或非空 str。
    """
    if not isinstance(block, dict):
        return ["quota_resume 必须是 JSON 对象"]
    errors = []
    if "mode" in block and block["mode"] not in QUOTA_RESUME_MODES:
        errors.append(_enum_error("quota_resume.mode", block["mode"],
                                  QUOTA_RESUME_MODES))

    def _valid_count(value):
        return (not isinstance(value, bool) and isinstance(value, int)
                and value >= 0)

    for key in ("max_resumes", "resume_count"):
        if key in block:
            value = block[key]
            if not _valid_count(value):
                errors.append("quota_resume.%s 必须是 >= 0 的整数" % key)
    resume_count = block.get("resume_count")
    max_resumes = block.get("max_resumes")
    if _valid_count(resume_count) and _valid_count(max_resumes) \
            and resume_count > max_resumes:
        errors.append(
            "quota_resume.resume_count %d 不得大于 quota_resume."
            "max_resumes %d（预算不变量：resume_count <= max_resumes）"
            % (resume_count, max_resumes))
    if "automation_id" in block and block["automation_id"] is not None:
        value = block["automation_id"]
        if not isinstance(value, str) or value == "":
            errors.append("quota_resume.automation_id 必须是非空字符串或 null")
    return errors


def validate_state(state) -> "list[str]":
    """校验 v2.4 状态 dict，返回错误消息列表（中文，含字段路径）。

    空列表 = 合法；不抛异常；state 非 dict → ["state 必须是 JSON 对象"]。
    必填顶层键（_REQUIRED_TOP_KEYS）：task_id / goal / repository /
    route / dag / status / workflow_run_id / validation / review /
    quota_resume；phase 可选（缺省即不写键）。未知顶层键忽略（向前
    兼容），不报错。
    带 v2.3 标记的遗留 state 在本函数无豁免：缺 v2.4 必填键照常报错
    （绝不自动迁移）——消费方应先经 is_legacy_state / detect_legacy_
    task 识别并按 LEGACY_STATE_GUIDANCE 报告为 v2.3 任务。
    """
    if not isinstance(state, dict):
        return ["state 必须是 JSON 对象"]
    errors = []

    # 必填顶层键
    for key in _REQUIRED_TOP_KEYS:
        if key not in state:
            errors.append("缺少必填顶层键 %s" % key)

    # task_id 非空 str 且匹配格式
    if "task_id" in state:
        task_id = state["task_id"]
        if not isinstance(task_id, str) or task_id == "":
            errors.append("task_id 必须是非空字符串")
        elif not _TASK_ID_RE.match(task_id):
            errors.append(
                "task_id %r 不匹配格式 ^[A-Za-z0-9][A-Za-z0-9-]*$"
                "（字母数字开头，仅含字母数字与连字符）" % task_id)

    # goal 非空 str
    if "goal" in state:
        goal = state["goal"]
        if not isinstance(goal, str) or goal == "":
            errors.append("goal 必须是非空字符串")

    # route（mode 四模式 + 可选 assurance 两级）
    if "route" in state:
        errors.extend(_validate_route(state["route"]))

    # repository（必填仓库根绑定块）
    if "repository" in state:
        errors.extend(_validate_repository(state["repository"]))

    # dag（静态节点逐项 + 整图，错误带 dag[i] 节点路径）
    if "dag" in state:
        errors.extend(_validate_dag(state["dag"]))

    # status ∈ 七态
    if "status" in state:
        status = state["status"]
        if status not in TASK_STATUSES:
            errors.append(_enum_error("status", status, TASK_STATUSES))

    # phase 可选描述性标注（缺省即不写键；写入则必须 ∈ TASK_PHASES）
    if "phase" in state and state["phase"] not in TASK_PHASES:
        errors.append(_enum_error("phase", state["phase"], TASK_PHASES))

    # workflow_run_id：null 或非空 str（委派运行启动前为 null）
    if "workflow_run_id" in state:
        workflow_run_id = state["workflow_run_id"]
        if workflow_run_id is not None and (
                not isinstance(workflow_run_id, str)
                or workflow_run_id == ""):
            errors.append("workflow_run_id 必须是非空字符串或 null")

    # validation / review（任务级最小记录）
    if "validation" in state:
        errors.extend(_validate_validation(state["validation"]))
    if "review" in state:
        errors.extend(_validate_review(state["review"]))

    # quota_resume（P3-B 定稿授权块：manual/auto + 预算不变量）
    if "quota_resume" in state:
        errors.extend(_validate_quota_resume(state["quota_resume"]))

    return errors


# —— 构造 ——

def new_task_state(task_id, goal, route, *, repository_root, dag_nodes=(),
                   workflow_run_id=None, status="active",
                   phase=None) -> dict:
    """构造带默认值的 v2.4 完整状态 dict。

    只做构造不做语义校验（调用方负责 validate_state）。

    参数：
      - task_id / goal：任务标识与目标（原样写入，合法性归
        validate_state）；
      - route：dict（v2.3 同款「mode 必填」口径）——构造只摘取
        mode 与 assurance（非 None 时）两键；route 非 dict 抛
        TypeError；
      - repository_root（关键字专用，必填）：v2.4 中 repository 是
        必填概念——经 bind_repository_root 归一为绝对路径写入顶层
        "repository" 块；空串 / 非路径类型抛 ValueError；
      - dag_nodes：静态节点 dict 列表（runtime.work_unit.new_node
        产物），浅拷贝为 list；非 list/tuple 抛 TypeError；
      - workflow_run_id：委派运行 id，缺省 None（启动前可空）；
      - status：缺省 "active"（v2.4 无 created 阶段态）；
      - phase：可选描述性标注（None = 不写键——枚举无 null 空档）。

    构造结果恒含 validation / review（全 null 未记录态）与
    quota_resume（default_quota_resume 占位块）三个任务级块。
    v2.3 构造面的 ownership_files / verification_required /
    review_required / reviewer 等关键字已随逐单元证据面退役，不再
    接受（旧调用方在调用时 TypeError——按遗留面处置，不做兼容垫片）。
    """
    if not isinstance(route, dict):
        raise TypeError(
            "new_task_state：route 必须是 dict（v2.4 route 含 mode/"
            "assurance），得到 %s" % type(route).__name__)
    if not isinstance(dag_nodes, (list, tuple)):
        raise TypeError(
            "new_task_state：dag_nodes 必须是 list 或 tuple，得到 %s"
            % type(dag_nodes).__name__)
    route_block = {"mode": route.get("mode")}
    if route.get("assurance") is not None:
        route_block["assurance"] = route.get("assurance")
    st = {
        "task_id": task_id,
        "goal": goal,
        "repository": {},
        "route": route_block,
        "dag": list(dag_nodes),
        "status": status,
        "workflow_run_id": workflow_run_id,
        # 任务级最小验证记录（P2-C）：全 null = 未记录
        "validation": {
            "status": None,
            "change_id": None,
            "summary": None,
            "commands": None,
        },
        # 任务级最小审查记录（P2-C）：全 null = 未记录
        "review": {
            "reviewer": None,
            "verdict": None,
            "change_id": None,
            "findings": None,
        },
        # quota_resume 块（P3-B 定稿初始形状：manual 未授权零预算）
        "quota_resume": default_quota_resume(),
    }
    if phase is not None:
        st["phase"] = phase
    bind_repository_root(st, repository_root)
    return st


# —— 任务仓库根绑定（RB-2，签名与 v2.3 一致） ——

def bind_repository_root(st, repo_root) -> dict:
    """为任务绑定仓库根（RB-2），就地写入并返回同一 dict。

    - 归一口径：root 恒存为 str(Path(repo_root).resolve())（相对路径
      → 绝对路径；Windows 反斜杠原样保留，JSON 转义由落盘层负责）。
      先 os.path.abspath 后 resolve：Python 3.7 的 Windows resolve()
      对不存在的相对路径不做绝对化（3.8 起 bpo-37834 才修复），abspath
      前置保证「相对 → 绝对」在 3.7/3.8+ 行为一致；对已存在的绝对路径
      两者完全等价（resolve 仍做符号链接归一）。
    - repository 块缺失 / 形状异常时按需重建；
    - 非法输入（空串、非 str/Path 路径类型）→ ValueError（先校验后
      修改，失败零副作用）；
    - 调用方负责 save_state（本函数不触碰磁盘）。

    v2.4 中 repository 是必填概念：root 即任务全部 git 操作（touched
    清单 / change_id 求值）的仓库根；账本（state.json / events.jsonl）
    恒在账本根，两根分离由调用方按 resolve_repository_root 消费。
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
    """读取任务绑定的仓库根（RB-2），返回字符串或 None。

    合法绑定（repository.root 为非空 str）→ 返回该字符串；任务未绑定 /
    repository 缺失或非 dict / root 缺失或形状非法 → None。本函数是
    容错读（手写 state.json 的形状异常不炸消费方），形状纠错归
    validate_state。
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

    fallback_root 通常是账本根（任务账本所在目录）——未绑定任务在其上
    求值 git，行为与单仓时代完全一致；绑定任务返回其归一后的绑定根
    （原字符串，不做二次解析）。
    """
    bound = bound_repository_root(task_state)
    return fallback_root if bound is None else bound


# —— 保存 / 读取 / 迁移 / 重开 ——

def _transition_errors(previous, new_status):
    """按 TASK_TRANSITIONS 校验一次 status 迁移，非法时抛 ValueError。

    规则（save_state 与 transition_task_status 共用）：
      - previous 为 None（首存，无盘上状态）→ 无转换可言，放行；
      - 旧 == 新（无转换）→ 放行；
      - 终态（completed / cancelled / failed）无表项 → 任何变化拒绝
        ——终态不得静默重开；显式重开只能走 reopen_task（唯一落
        task_reopened 事件的通道）；
      - 其余：新 status 必须在 TASK_TRANSITIONS.get(旧 status, ()) 内。
    previous 为盘上已加载的状态 dict（不存在为 None）；其 JSON 损坏由
    load_state 抛 ValueError，自然向上传播，不覆盖损坏文件。
    """
    if previous is None:
        return  # 首存：无盘上旧状态，无转换可言
    old_status = previous.get("status")
    if old_status == new_status:
        return  # 无转换，放行
    allowed = TASK_TRANSITIONS.get(old_status, ())
    if new_status not in allowed:
        targets = ", ".join(allowed) if allowed \
            else "无（终态不接受任何转换；显式重开请走 reopen_task）"
        raise ValueError(
            "status 转换被拒绝：%r → %r 不在合法转换内"
            "（%s 的合法目标：%s；完整转换表见 TASK_TRANSITIONS）"
            % (old_status, new_status, old_status, targets))


def save_state(repo_root, state, *, task_id=None) -> pathlib.Path:
    """校验并原子保存状态文件，返回最终路径。

    - validate_state 有错误 → ValueError（错误清单拼接）；
    - 写入路径的 task_id 取参数 task_id，否则取 state["task_id"]；
    - 目录/内容一致性（P1-9）：显式 task_id 参数非 None 时必须等于
      state["task_id"]，不一致 → ValueError（目录与内容的任务标识
      必须一致；如需迁移请以内容 ID 为准重建目录）；
    - 状态转换门：写盘前读盘上现有 state.json（不存在视为首存），
      status 变化必须落在 TASK_TRANSITIONS 内；终态一律不得静默重开
      （显式重开请走 reopen_task）；盘上文件损坏（load_state 抛
      ValueError）自然向上传播，不覆盖损坏文件；
    - 目录不存在自动创建（durable_io 负责）；
    - 原子写：复用 runtime.durable_io.atomic_write_json——每次调用
      唯一临时名（<目标名>.durable-tmp.<pid>.<uuid>.tmp）+ os.replace，
      UTF-8、ensure_ascii=False、缩进 2、固定 \n 换行，PermissionError
      有界重试；读方永不见撕裂文件。

    （历史注：v2.3 曾有 finalizing→completed 的完成门内部提交通道
    （save_state 的 _gate_commit 私有形参）；v2.4 已无该通道——
    completed 由完成门 API 直接落盘，终态守卫转向「不得静默重开」，
    该形参随 Phase 4 退役面审计一并移除。）
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
    _transition_errors(load_state(repo_root, tid), state.get("status"))
    atomic_write_json(path, state, sort_keys=False)
    return path


def load_state(repo_root, task_id) -> "dict | None":
    """读取任务状态文件；文件不存在 → None；JSON 损坏 → ValueError（不静默）。

    读取后按 TASK_ID_KEYS 顺序归一 task_id 写回 raw["task_id"]，并 pop 掉
    值与归一结果一致的 legacy 键（TASK_ID / CONTINUITY_ID / continuity_id）。

    本函数不做 schema 校验、不做遗留判定（读原样返回）——消费方载入后
    经 validate_state 校验、经 is_legacy_state / detect_legacy_task
    识别 v2.3 遗留任务（绝不自动迁移）。
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


def transition_task_status(repo_root, task_id, new_status) -> dict:
    """按 TASK_TRANSITIONS 把任务状态迁移到 new_status，返回迁移后的状态。

    公共运行时迁移入口：
      - load_state 缺失（None）→ ValueError；JSON 损坏自然向上传播；
      - 迁移校验与 save_state 同规则（终态无表项——终态迁移含重开
        一律拒绝，显式重开走 reopen_task）；
      - 成功后向任务 journal 追加一条 status_changed 事件
        （{"event": "status_changed", "from": 旧, "to": 新}），journal
        在函数内 import（惰性 import 风格，与钩子一致）。
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


def reopen_task(repo_root, task_id, *, reason=None) -> dict:
    """显式重开终态任务（completed / cancelled / failed → active）。

    终态不得静默重开：save_state / transition_task_status 对终态迁移
    一律拒绝，本函数是唯一放行通道——重开动作必须落 journal 记录
    （{"event": "task_reopened", "from": 旧终态, "to": "active",
    "reason": reason}），绝不静默改写：
      - load_state 缺失 → ValueError；JSON 损坏自然向上传播；
      - 盘上 status 非终态 → ValueError（非终态任务无需重开）；
      - 置 active 后先 validate_state（终态 state 合法则重开态亦合法）
        再经 durable_io 原子写盘——绕开 save_state 的终态转换门正是本
        函数的存在意义，校验与原子写约定与 save_state 完全一致；
      - reason 可空（缺省 None），供调用方留重开缘由。
    """
    from runtime import journal

    st = load_state(repo_root, task_id)
    if st is None:
        raise ValueError(
            "reopen_task：任务 %s 不存在（无 state.json），无法重开" % task_id)
    old_status = st.get("status")
    if old_status not in TERMINAL_STATUSES:
        raise ValueError(
            "reopen_task：任务 %s 的 status 为 %r，非终态任务无需重开"
            "（迁移请走 transition_task_status）" % (task_id, old_status))
    st["status"] = "active"
    errors = validate_state(st)
    if errors:
        raise ValueError("state 非法，无法保存：%s" % "；".join(errors))
    atomic_write_json(state_path(repo_root, task_id), st, sort_keys=False)
    journal.append_event(
        repo_root, task_id,
        {"event": "task_reopened", "from": old_status, "to": "active",
         "reason": reason})
    return st


# —— 发现 ——

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
    """扫描 tasks_root 下全部子目录，返回任务四分类 dict。

    返回结构（四个键恒存在，桶内条目均为 (目录名, reason) 二元组，
    按目录名排序）：
      - "active"：state.json 可读且 status 非终态（reason 为 status
        字符串；v2.3 遗留任务的 status 不在 v2.4 七态内，同样落本桶
        ——遗留判定与一行处置指引由消费方经 detect_legacy_task 补）；
      - "terminal"：status ∈ TERMINAL_STATUSES（reason 为 status 字符串）；
      - "corrupt"：state.json 存在但 JSON 损坏 / 任务标识归一失败
        （ValueError）或读取时 OSError——reason 为异常消息简写
        （「state.json 损坏：…」/「state.json 不可读：…」，截断到
        80 字符内）；
      - "orphaned"：目录存在但无 state.json（reason 为「无 state.json」）。

    发现完整性语义：损坏的 state.json 不再被解释成「没有任务」——
    corrupt 与 orphaned 都被显式报告，由调用方决定拦截或降级。
    tasks_root 不存在 → 四桶全空；tasks_root 下的非目录项跳过；单
    目录解析失败不中断扫描（健壮性优先）。
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

    实现直接取 discover_tasks 的 active 桶目录名：单个任务目录解析
    失败（JSON 损坏 / 缺任务标识 / 无 state.json / 读取时 OSError）
    不进入 active，也不中断扫描。
    """
    return [name for name, _status in discover_tasks(repo_root)["active"]]
