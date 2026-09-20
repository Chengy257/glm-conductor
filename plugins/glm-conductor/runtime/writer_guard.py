#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GLM Conductor v2.4 Phase 1 仓库写者守卫（工作包 P1-E，单元 U5）。

职责：
    粗粒度仓库级写预约——不变式：一个 Git 仓库至多一个活跃 Conductor
    写 Workflow。预约是仓库内单条持久记录
    `.glm-conductor/writer_guard.json`（恰四字段，见 RECORD_FIELDS），
    acquire / release / inspect 三个函数即全部公共面。

永不自动过期（规格红线）：
    全代码无任何 TTL / generation / heartbeat / 按时间过期的逻辑。
    created_at 仅为诊断信息（供 inspect 与冲突报告人工判读残留的新旧），
    守卫判定绝不读取它——残留记录无论多旧、created_at 哪怕不可解析，
    acquire 一律冲突；恢复只经两条路：关联任务/Workflow 到达终态后由
    主会话显式对账释放，或用户/操作者在 inspect 之后显式清除（U6 CLI
    --force 的接线在 U6，本模块不设旁路）。

接口（id 一律由调用方供给——U6 CLI 或主会话——本模块不生成 id）：
    acquire(repo_root, task_id, workflow_run_id=None) -> dict
        成功：{"ok": True, "conflict": None}；
        冲突：{"ok": False, "conflict": {task_id, workflow_run_id,
               created_at}}——报出冲突方（当前持有者）的 id；
        同 task 重复 acquire 幂等成功：workflow_run_id 非 None 时更新
        记录，传 None 时保留记录原值（避免无参重入意外清掉已知 run
        id）；created_at 保持首次 acquire 时刻不变（预约创建时刻语义：
        重入不改创建时刻）。
    release(repo_root, task_id, workflow_run_id=None) -> dict
        {"ok": ..., "released": bool, "conflict": ...}；
        幂等：无预约（缺失 / 归零 / 不可解析）→ ok 且 released=False；
        预约属其它 task → 拒绝（ok=False）且 conflict 报当前持有者；
        释放权威键是 task_id——workflow_run_id 仅诊断信息，不参与比对
        （规格只要求按 task 拒绝非持有者）。
    inspect(repo_root) -> dict | None
        有有效预约 → 归一后的四字段 dict（缺失字段以 None 补位）；
        无预约 → None。只读、零锁、零写入。

持久记录（.glm-conductor/writer_guard.json，sort_keys JSON）：
    {"repo_identity": 绝对仓库根（os.path.abspath）, "task_id": ...,
     "workflow_run_id": ... | None, "created_at": ISO8601 UTC 毫秒 Z}
    释放以「写回空 dict」归零记录（读侧一律经 _holder_record 归一，{}
    即无预约），不物理删除文件——规避 Windows 并发读句柄的删除竞态，
    语义与删除等价（inspect / acquire / release 对 {} 均视同无预约）。
    记录损坏（JSON 不可解析 / 非 dict / 缺非空 task_id）按 durable_io
    读侧惯例 fail-open 视同无预约——这是不可读容错，不是按时间过期。

并发与原子性：
    acquire / release 的读-改-写全程走 runtime.durable_io 的
    atomic_update_json（<记录名>.lock 独占锁 + 原子读 + os.replace
    原子写）：两个并发 acquire 必有一方看到另一方落盘的持有者，单
    仓库不变式在并发下成立；读侧永不见撕裂文件。

依赖：
    仅 Python 3 标准库 + runtime.durable_io；3.7 兼容语法（注解引号
    形式）；无 per-unit 语义、无 scheduler / epoch 概念。

来源：
    v2.4 Phase 1 工作包 P1-E（docs/roadmap/V2_4_PHASE_1_*.md，单元 U5）。
"""

import os
from datetime import datetime, timezone

from runtime import durable_io


class WriterGuardError(Exception):
    """仓库写者守卫的结构性错误（非法 task_id / workflow_run_id 等）。"""


STATE_DIR_NAME = ".glm-conductor"      # 仓库内运行时状态目录（既有约定）
RECORD_FILENAME = "writer_guard.json"  # 持久预约记录（恰四字段）
RECORD_FIELDS = ("repo_identity", "task_id", "workflow_run_id", "created_at")
CONFLICT_FIELDS = ("task_id", "workflow_run_id", "created_at")


# —— 小助手（时间仅诊断用，守卫判定绝不读取） ——

def _utc_now_iso() -> str:
    """当前 UTC 时刻（ISO8601，毫秒精度，Z 后缀——与 durable_io 锁
    created_at 同一形态；仅诊断信息，不是任何过期判定的输入）。"""
    return datetime.now(timezone.utc).isoformat(
        timespec="milliseconds").replace("+00:00", "Z")


def record_path(repo_root) -> str:
    """仓库内持久预约记录路径：<repo_root>/.glm-conductor/writer_guard.json。"""
    return os.path.join(os.fspath(repo_root), STATE_DIR_NAME, RECORD_FILENAME)


def _validate_task_id(task_id) -> None:
    """task_id 结构校验：必须是非空字符串（id 由调用方供给，本模块不造）。"""
    if not isinstance(task_id, str) or task_id == "":
        raise WriterGuardError(
            "writer_guard：task_id 必须是非空字符串（由调用方供给），得到 %r"
            % (task_id,))


def _validate_workflow_run_id(workflow_run_id) -> None:
    """workflow_run_id 结构校验：None 或非空字符串。"""
    if workflow_run_id is not None and (
            not isinstance(workflow_run_id, str) or workflow_run_id == ""):
        raise WriterGuardError(
            "writer_guard：workflow_run_id 必须是 None 或非空字符串，得到 %r"
            % (workflow_run_id,))


def _holder_record(current):
    """把读到的当前内容归一为「持有记录」；无有效持有者 → None。

    非 dict（JSON 不可解析时 atomic_read_json 的 default 产物 / 手写
    垃圾）或缺非空 task_id（含 {} 归零记录 / 字段残缺垃圾）→ None。
    除 task_id 外的其余字段容忍缺失——手写残留记录照常冲突。
    """
    if not isinstance(current, dict):
        return None
    task_id = current.get("task_id")
    if not isinstance(task_id, str) or task_id == "":
        return None
    return current


def _conflict_view(record) -> dict:
    """从持有记录提炼冲突方信息（恰 CONFLICT_FIELDS 三键；缺失字段以
    None 补位，兼容手写残留记录）。"""
    return {
        "task_id": record.get("task_id"),
        "workflow_run_id": record.get("workflow_run_id"),
        "created_at": record.get("created_at"),
    }


# —— 公共面 ——

def acquire(repo_root, task_id, workflow_run_id=None) -> dict:
    """为 task_id 取得 repo_root 的仓库级写预约，返回 {"ok", "conflict"}。

    - 成功：{"ok": True, "conflict": None}，记录四字段落盘；
    - 冲突（记录属其它 task）：{"ok": False, "conflict": 冲突方三键视图}
      ——零写入（记录原样保留）；
    - 同 task 重复 acquire 幂等成功：workflow_run_id 非 None 时更新记录，
      传 None 时保留原值；created_at 保持首次 acquire 时刻不变；
    - repo_identity 记 os.path.abspath(repo_root)（绝对仓库根）；
    - task_id / workflow_run_id 结构非法 → WriterGuardError（结构性
      错误，不静默转换）。

    读-改-写在 durable_io 独占锁保护下完成；锁基础设施故障（10 秒有界
    忙等耗尽等）以 durable_io.LockBusyError 原样上抛——那是基础设施
    错误，不是预约冲突。
    """
    _validate_task_id(task_id)
    _validate_workflow_run_id(workflow_run_id)
    repo_identity = os.path.abspath(os.fspath(repo_root))
    path = record_path(repo_root)
    outcome = {}

    def _updater(current):
        holder = _holder_record(current)
        if holder is not None and holder.get("task_id") != task_id:
            outcome["ok"] = False
            outcome["conflict"] = _conflict_view(holder)
            return current  # 冲突零写入：目标记录不被触碰
        prior_created_at = holder.get("created_at") if holder is not None else None
        record = {
            "repo_identity": repo_identity,
            "task_id": task_id,
            # 同 task 重入传 None → 保留已知 run id，绝不意外清掉
            "workflow_run_id": (holder.get("workflow_run_id")
                                if holder is not None and workflow_run_id is None
                                else workflow_run_id),
            # 预约创建时刻语义：仅首次 acquire 写入，重入不改写
            "created_at": (prior_created_at
                           if isinstance(prior_created_at, str)
                           and prior_created_at != ""
                           else _utc_now_iso()),
        }
        outcome["ok"] = True
        outcome["conflict"] = None
        return record

    durable_io.atomic_update_json(path, _updater, default=None)
    return {"ok": outcome["ok"], "conflict": outcome["conflict"]}


def release(repo_root, task_id, workflow_run_id=None) -> dict:
    """释放 task_id 在 repo_root 的写预约，返回 {"ok", "released",
    "conflict"}。

    - 无预约（记录缺失 / {} 归零 / 不可解析）：{"ok": True,
      "released": False, "conflict": None}——幂等，重复释放不报错；
    - 预约属其它 task：拒绝——{"ok": False, "released": False,
      "conflict": 当前持有者三键视图}，记录零改动（绝不代删他人预约；
      操作者显式清除走「先 inspect 拿持有者 task_id，再由该 id 释放」
      的 U6 --force 接线，本函数不设绕过持有者的旁路）；
    - 属该 task：{"ok": True, "released": True, "conflict": None}，记录
      以写回空 dict 归零（读侧视同无预约，见模块 docstring）。

    释放权威键是 task_id；workflow_run_id 仅诊断信息，不参与比对。
    结构非法输入 → WriterGuardError；锁基础设施故障同 acquire。
    """
    _validate_task_id(task_id)
    _validate_workflow_run_id(workflow_run_id)
    path = record_path(repo_root)
    outcome = {}

    def _updater(current):
        holder = _holder_record(current)
        if holder is None:  # 幂等：无预约视同已释放，零写入
            outcome["ok"] = True
            outcome["released"] = False
            outcome["conflict"] = None
            return current
        if holder.get("task_id") != task_id:
            outcome["ok"] = False
            outcome["released"] = False
            outcome["conflict"] = _conflict_view(holder)
            return current  # 非 owner：记录不被触碰
        outcome["ok"] = True
        outcome["released"] = True
        outcome["conflict"] = None
        return {}  # 归零记录 = 释放（读侧 _holder_record 视同无预约）

    durable_io.atomic_update_json(path, _updater, default=None)
    return {"ok": outcome["ok"], "released": outcome["released"],
            "conflict": outcome["conflict"]}


def inspect(repo_root):
    """读 repo_root 当前写预约 → 归一四字段 dict | None。

    None = 无预约（记录缺失 / {} 归零 / 损坏不可解析 / 缺有效 task_id）。
    只读：不加锁、零写入——读侧原子性由 durable_io.atomic_write_json
    的 os.replace 保证（读方永不见撕裂文件）。
    """
    current = durable_io.atomic_read_json(record_path(repo_root), default=None)
    holder = _holder_record(current)
    if holder is None:
        return None
    return {
        "repo_identity": holder.get("repo_identity"),
        "task_id": holder.get("task_id"),
        "workflow_run_id": holder.get("workflow_run_id"),
        "created_at": holder.get("created_at"),
    }
