#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GLM Conductor v2.1 Resume Manifest（M3 收尾，wu-21-07）。

职责：
    恢复上下文压缩层（§10.1 定位冻结）：**不是第二份 task truth**，
    而是「崩溃后可重建的恢复上下文快照」——从 state / journal /
    agent_run 现算并原子落盘到任务目录 manifest.json，供恢复入口
    （SessionStart / 模型）读取，免去全量扫描。

    - write_resume_manifest(repo_root, task_id)：构建冻结形状的快照
      并 tmp+os.replace 原子写（模式照抄 lease/state），返回写盘 dict；
    - read_resume_manifest(repo_root, task_id)：读取快照，缺失 /
      损坏 / 非 dict 一律 None（绝不抛）；
    - resume_manifest_path：路径派生（.glm-conductor/tasks/<task-id>/
      manifest.json，与 state.json/events.jsonl 同目录各管各的文件）。

内容形状（§10.2 冻结）：
    task_id / written_at / task_status / last_event_seq /
    active_units / verification_due / agent_runs（≤20 条 + truncated）/
    next_ready_candidates / quota_snapshot（state.quota 精简，缺块
    UNKNOWN）/ resume_authorization（execution_policy.continuity 精简，
    legacy 缺块按默认块）/ quota_control（v2.2 M1a D15-e：continuation
    的 Persistent Bridge 只读快照——scheduler_origin /
    scheduler_create_capability / wake_bridge_mode / wake_bridge_status /
    automation_id / generation / boundary_id / next_wake_at /
    bridge_interval_minutes，legacy 缺块按 default_continuation 默认
    解释，所有键恒存在）。

失败语义（§10.3 冻结——本模块的调用方契约）：
    写 manifest 失败不得覆盖 state truth、不得让正常事务半提交——
    task_manager 的三个挂点（commit_dispatch / abort_dispatch /
    finish_unit）在主事务完成之后以 try/except 调用本模块，异常只
    落一条 manifest_write_failed journal 警告事件。manifest 是
    派生物：state 变了 manifest 可以旧（下次写入点自动刷新），读方
    按「可重建压缩层」对待，不当 truth。

依赖：
    仅 Python 3 标准库 + runtime.state / runtime.journal /
    runtime.agent_run / runtime.execution_policy（只读消费）。
    不 import task_manager（避免循环：task_manager 单向挂接本模块）。
"""

import json
import os
import pathlib

from runtime import agent_run, journal
from runtime.execution_policy import default_execution_policy

# manifest 文件名（任务目录内，与 state.json / events.jsonl 平级）
MANIFEST_FILENAME = "manifest.json"

# agent_runs 精简清单的最大条数（超出截断并置 truncated=true）
MAX_AGENT_RUNS = 20

# 未完成单元口径（非终态即未完成；终态 = completed/failed/cancelled）
WU_TERMINAL = ("completed", "failed", "cancelled")

# 中断态（active_units 的组成部分之一）
WU_INTERRUPTED = ("running", "waiting_quota", "blocked")


def resume_manifest_path(repo_root, task_id) -> pathlib.Path:
    """manifest 路径：<repo>/.glm-conductor/tasks/<task-id>/manifest.json。"""
    return (pathlib.Path(repo_root) / ".glm-conductor" / "tasks"
            / task_id / MANIFEST_FILENAME)


def _now_iso():
    """当前 UTC ISO8601 毫秒时间戳（与 lease/state 同格式）。"""
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.") + "%03d+00:00" % (
        datetime.datetime.now(datetime.timezone.utc).microsecond // 1000)


def _ready_candidates(units):
    """纯读计算就绪候选：状态 pending/waiting_dependency/ready 且
    depends_on 全部 completed 的单元 id（排序）。

    不触碰单元状态、不调用会 save 的入口（refresh_readiness 会写
    state，此处只读重算）。"""
    completed = {w.get("id") for w in units
                 if isinstance(w, dict) and w.get("status") == "completed"}
    candidates = []
    for w in units:
        if not isinstance(w, dict):
            continue
        if w.get("status") not in ("pending", "waiting_dependency", "ready"):
            continue
        deps = w.get("depends_on")
        deps = deps if isinstance(deps, list) else []
        if all(dep in completed for dep in deps):
            candidates.append(w.get("id"))
    return sorted(candidates)


def _quota_snapshot(st):
    """state 可选 quota 块的精简快照；缺块 → UNKNOWN。"""
    quota = st.get("quota")
    if not isinstance(quota, dict):
        return {"status": "UNKNOWN", "observed_at": None}
    return {"status": quota.get("status"),
            "observed_at": quota.get("observed_at")}


def _resume_authorization(st):
    """execution_policy.continuity 精简；legacy 缺块按默认块。"""
    policy = st.get("execution_policy")
    policy = policy if isinstance(policy, dict) else default_execution_policy()
    continuity = policy.get("continuity")
    continuity = continuity if isinstance(continuity, dict) \
        else default_execution_policy()["continuity"]
    return {"mode": continuity.get("mode"),
            "remaining_windows": continuity.get("max_quota_windows")}


def _quota_control_snapshot(st):
    """continuation 块的 Persistent Bridge 只读快照（v2.2 M1a，D15-e）。

    与 _quota_snapshot / _resume_authorization 同款容错风格：st 无
    continuation 块（legacy v2.1）、半块或形状异常一律按
    runtime.state.DEFAULT_CONTINUATION 的默认口径解释（origin/create
    = "unknown"、status="none"、mode="recurring"、generation=0、
    其余 None），未知值透传（形状纠错归 validate_state，绝不抛）。
    boundary_id 取 wake_bridge 的 current_boundary_id（D15-e 新键）
    优先；缺省或 null 回退 legacy 键 boundary_id；再缺省 None。
    纯函数：只读入参、零 I/O，所有键恒存在、形状确定，供恢复入口
    零猜测消费。

    state 经函数内延迟 import（与 write_resume_manifest 同风格，本
    模块对 state 只读消费不持顶层依赖）。
    """
    from runtime import state as state_mod

    default = state_mod.DEFAULT_CONTINUATION
    default_scheduler = default.get("scheduler_context")
    default_scheduler = default_scheduler \
        if isinstance(default_scheduler, dict) else {}
    default_bridge = default.get("wake_bridge")
    default_bridge = default_bridge if isinstance(default_bridge, dict) else {}

    continuation = st.get("continuation")
    continuation = continuation if isinstance(continuation, dict) else {}
    scheduler_context = continuation.get("scheduler_context")
    scheduler_context = scheduler_context \
        if isinstance(scheduler_context, dict) else {}
    wake_bridge = continuation.get("wake_bridge")
    wake_bridge = wake_bridge if isinstance(wake_bridge, dict) else {}

    boundary_id = wake_bridge.get("current_boundary_id")
    if boundary_id is None:
        boundary_id = wake_bridge.get("boundary_id")

    return {
        "scheduler_origin": scheduler_context.get(
            "origin", default_scheduler.get("origin")),
        "scheduler_create_capability": scheduler_context.get(
            "create", default_scheduler.get("create")),
        "wake_bridge_mode": wake_bridge.get(
            "mode", default_bridge.get("mode")),
        "wake_bridge_status": wake_bridge.get(
            "status", default_bridge.get("status")),
        "automation_id": wake_bridge.get("automation_id"),
        "generation": wake_bridge.get(
            "generation", default_bridge.get("generation")),
        "boundary_id": boundary_id,
        "next_wake_at": wake_bridge.get("next_wake_at"),
        "bridge_interval_minutes": wake_bridge.get("bridge_interval_minutes"),
    }


def build_manifest(repo_root, st, events) -> dict:
    """从已加载的 state dict 与事件清单构建 manifest dict（纯读，不落盘）。

    st 为 runtime.state.load_state 的结果；events 为
    journal.read_events 的结果（last_event_seq 取行数）。
    """
    units = st.get("work_units")
    units = units if isinstance(units, list) else []
    unit_ids = {w.get("id"): w for w in units if isinstance(w, dict)}

    active_units = set(st.get("dispatch", {}).get("active") or [])
    active_units.update(w.get("id") for w in unit_ids.values()
                        if w.get("status") in WU_INTERRUPTED)

    verification_due = sorted(
        w.get("id") for w in unit_ids.values()
        if w.get("status") not in WU_TERMINAL
        and (w.get("verification") or []))

    runs = agent_run.list_agent_runs(repo_root, st.get("task_id"))
    runs_trimmed = [{"unit": r.get("unit"), "agent_id": r.get("agent_id"),
                     "tool_use_id": r.get("tool_use_id"),
                     "failed": r.get("failed")} for r in runs]
    truncated = len(runs_trimmed) > MAX_AGENT_RUNS
    runs_trimmed = runs_trimmed[:MAX_AGENT_RUNS]

    return {
        "task_id": st.get("task_id"),
        "written_at": _now_iso(),
        "task_status": st.get("status"),
        "last_event_seq": len(events),
        "active_units": sorted(active_units),
        "verification_due": verification_due,
        "agent_runs": runs_trimmed,
        "agent_runs_truncated": truncated,
        "next_ready_candidates": _ready_candidates(units),
        "quota_snapshot": _quota_snapshot(st),
        "resume_authorization": _resume_authorization(st),
        "quota_control": _quota_control_snapshot(st),
    }


def write_resume_manifest(repo_root, task_id) -> dict:
    """构建并原子写 manifest（tmp + os.replace），返回写盘的 dict。

    state 缺失 / 损坏向上传播 ValueError / OSError（调用方挂点以
    try/except 包裹转警告事件）；写入本身原子——崩溃时读者要么看到
    旧文件要么看到新文件，绝不看到半截 JSON。
    """
    from runtime import state as state_mod

    st = state_mod.load_state(repo_root, task_id)
    events = journal.read_events(repo_root, task_id)
    manifest = build_manifest(repo_root, st, events)

    path = resume_manifest_path(repo_root, task_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    with open(str(tmp), "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=True, indent=2)
        handle.write("\n")
    os.replace(str(tmp), str(path))
    return manifest


def read_resume_manifest(repo_root, task_id):
    """读取 manifest；缺失 / 损坏 / 非 dict → None（绝不抛）。"""
    path = resume_manifest_path(repo_root, task_id)
    try:
        with open(str(path), "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None
