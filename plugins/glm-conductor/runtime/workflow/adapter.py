#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GLM Conductor v2.4 Workflow run-id 关联 adapter（Phase 1 工作包 P1-D，单元 U4）。

职责：
    薄宿主关联层：把「任务 ↔ 原生 Workflow run」的对应关系持久化为
    .glm-conductor/workflow-runs/ 下单一 JSON 记录，供主会话在宿主
    Workflow 完成通知与 Conductor 任务之间做 id 关联。公共面只有两个
    函数：
      - record_run(task_ref, workflow_run_id, artifact_path=None,
        node_map=None) -> dict：写 / 更新关联记录（同 task_ref 重复
        调用整条替换——关联口径是「一任务一活跃 run」），返回落盘的
        记录 dict；
      - load_run(task_ref) -> dict | None：读关联记录。

记录形状（白名单字段，除此之外一律不落盘）：
      - task_ref（必填，非空字符串）；
      - workflow_run_id（必填，非空字符串，由调用方供给——本模块不造 id）；
      - created_at（写入时刻，ISO8601 UTC 毫秒 Z——纯诊断信息）；
      - artifact_path（可选：编译产物的落盘 / 引用路径，非 None 才落盘）；
      - node_map（可选：Conductor 节点 id → Workflow 站点 / ask id 映射，
        仅在宿主可观测且有用时由调用方提供，非 None 才落盘）。

绝不镜像（v2.4 冻结结果 F6：Workflow run 状态不镜像进 Conductor 账本）：
    子代理 running / 完成状态、重试计数、token 账目、后台任务状态、宿主
    健康度一律不持久化——那些是宿主自有状态（GetWorkflowRun 可查），
    本记录只做 id 关联。

原子写约定（复用 runtime.durable_io）：
    写走 atomic_write_json（唯一临时名 + os.replace，读方永不见撕裂
    文件，父目录缺失自动创建）；读走 atomic_read_json（缺失 / 损坏
    fail-open 返回 None，绝不抛）。

存储位置与文件名：
    RUNS_DIR 为模块级常量，默认 ".glm-conductor/workflow-runs"（相对
    当前工作目录 = 仓库根）；测试可整替换该常量注入临时目录（本单元
    测试即如此，绝不触碰真实账本）。记录文件名形如
    run-<task_ref 的百分号编码>.json：编码单射，特殊字符 task_ref 既
    不逃逸记录目录、也不互相碰撞；"run-" 前缀避开 Windows 保留设备名，
    超长名截断 + 摘要后缀保唯一。

依赖：
    仅 Python 3 标准库 + runtime.durable_io；不导入 legacy twins 与
    dispatcher 家族；不启动任何 Workflow。

来源：
    docs/roadmap/V2_4_PHASE_1_NATIVE_SEMANTIC_CORE_SPEC.md §5（adapter.py）
    + docs/roadmap/V2_4_PHASE_1_WORKFLOW_EXECUTION_PLAN.md U4。
"""

import os
from datetime import datetime, timezone
from hashlib import sha256
from urllib.parse import quote

from runtime import durable_io


class WorkflowRunError(Exception):
    """run 关联记录的结构性错误（非法 task_ref / workflow_run_id 等）。"""


STATE_DIR_NAME = ".glm-conductor"  # 仓库内运行时状态目录（既有约定）
RUNS_DIR_NAME = "workflow-runs"    # run 关联记录子目录
# 模块级常量：默认相对当前工作目录（仓库根）；测试可整替换注入临时目录
RUNS_DIR = os.path.join(STATE_DIR_NAME, RUNS_DIR_NAME)
FILENAME_PREFIX = "run-"   # 记录文件名前缀（避开 Windows 保留设备名）
MAX_FILENAME_CHARS = 120   # 记录文件名长度闸（超长截断 + 摘要后缀）
# 记录字段白名单（固定三键 + 可选两键；此外零字段）
RECORD_FIXED_FIELDS = ("task_ref", "workflow_run_id", "created_at")
RECORD_OPTIONAL_FIELDS = ("artifact_path", "node_map")


def _utc_now_iso() -> str:
    """当前 UTC 时刻（ISO8601，毫秒精度，Z 后缀——与 writer_guard 的
    created_at 同一形态；纯诊断信息，不参与任何判定）。"""
    return datetime.now(timezone.utc).isoformat(
        timespec="milliseconds").replace("+00:00", "Z")


def _validate_identity(kind, value) -> None:
    """id 结构校验：必须是非空字符串（id 由调用方供给，本模块不造）。"""
    if not isinstance(value, str) or value == "":
        raise WorkflowRunError(
            "adapter：%s 必须是非空字符串（由调用方供给），得到 %r"
            % (kind, value))


def run_filename(task_ref) -> str:
    """task_ref → 记录文件名（确定性单射：run-<百分号编码>.json）。

    urllib.parse.quote(safe="") 对全部保留字符做百分号编码（大写十六
    进制），"/" 与 "\\" 被编码后不可能逃逸记录目录；不同 task_ref 编码
    结果互不相同（单射）；超长编码截断并追加 sha256 摘要前 16 位保唯一。
    """
    _validate_identity("task_ref", task_ref)
    quoted = quote(task_ref, safe="")
    if len(quoted) > MAX_FILENAME_CHARS:
        digest = sha256(task_ref.encode("utf-8")).hexdigest()[:16]
        quoted = "%s-%s" % (quoted[:80], digest)
    return FILENAME_PREFIX + quoted + ".json"


def record_path(task_ref) -> str:
    """task_ref 的关联记录完整路径：<RUNS_DIR>/run-<编码>.json。"""
    return os.path.join(RUNS_DIR, run_filename(task_ref))


def record_run(task_ref, workflow_run_id, artifact_path=None,
               node_map=None) -> dict:
    """写 / 更新 task_ref 的 run 关联记录，返回落盘的记录 dict。

    - 记录固定含 task_ref / workflow_run_id / created_at 三键；
      artifact_path / node_map 仅在非 None 时落盘（可选字段语义）；
    - 同 task_ref 重复调用 = 关联更新，整条替换（created_at 取本次
      写入时刻）；
    - 结构非法输入（空 / 非 string 的 task_ref、workflow_run_id，
      空 artifact_path，非「非空字符串键 → 字符串值」的 node_map）
      → WorkflowRunError，目标文件不被触碰；
    - 落盘走 durable_io.atomic_write_json 原子写约定（见模块 docstring）。
    """
    _validate_identity("task_ref", task_ref)
    _validate_identity("workflow_run_id", workflow_run_id)
    if artifact_path is not None:
        _validate_identity("artifact_path", artifact_path)
    if node_map is not None:
        if not isinstance(node_map, dict):
            raise WorkflowRunError(
                "adapter：node_map 必须是 dict，得到 %s"
                % type(node_map).__name__)
        for key, value in node_map.items():
            if not isinstance(key, str) or key == "" \
                    or not isinstance(value, str):
                raise WorkflowRunError(
                    "adapter：node_map 必须是「非空字符串键 → 字符串值」"
                    "映射，得到键 %r 值 %r" % (key, value))
    record = {
        "task_ref": task_ref,
        "workflow_run_id": workflow_run_id,
        "created_at": _utc_now_iso(),
    }
    if artifact_path is not None:
        record["artifact_path"] = artifact_path
    if node_map is not None:
        record["node_map"] = dict(node_map)
    durable_io.atomic_write_json(record_path(task_ref), record)
    return dict(record)


def load_run(task_ref):
    """读 task_ref 的 run 关联记录 → 记录 dict | None。

    None = 无记录（缺失 / 损坏不可解析 / 内容非 dict——durable_io 读侧
    fail-open 惯例）或记录内 task_ref 与请求不符（防手挪文件错配）。
    只读：不加锁、零写入；返回值是本次 JSON 解析的新 dict，调用方改动
    不影响落盘内容。
    """
    _validate_identity("task_ref", task_ref)
    record = durable_io.atomic_read_json(record_path(task_ref), default=None)
    if not isinstance(record, dict):
        return None
    if record.get("task_ref") != task_ref:
        return None
    return record
