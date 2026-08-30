#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GLM Conductor v2.1 Agent Run 记账层（M2 后半，wu-21-03）。

职责：
    PostToolUse / PostToolUseFailure 钩子的唯一 journal 写入口——把
    「Agent tool 调用真实发生」这一运行时观察到的生命周期事实落账：
      - record_agent_launch：派发成功（tool_response 为 launch 确认）
        → 记 agent_launched 事件（unit / permit_id / tool_use_id /
        agent_id / execution_mode）；permit 消费由调用方（hook）先行
        完成，本函数只记账，不触碰 permits 文件；
      - record_agent_failure：派发失败 → agent_dispatch_failed 事件
        （error 摘要自载荷提取或显式给定，截断 200 字符）；
      - record_launch_replay_skipped：permit 已消费/不存在时的幂等
        容错记账（agent_launch_replay_skipped 事件）。

    与手写 implementation_started 事件（continuity 事件表）的关系：
    两者是不同事件且互不替代——implementation_started 表示「主会话
    走了 commit_dispatch 事务」（可能被手工绕过/代写），agent_launched
    表示「Agent tool 调用的返回被 runtime（PostToolUse 钩子）亲眼观察
    到」（钩子由宿主在工具调用点强制触发，不可绕过）。

agent_id 提取（§7.4 / §2.3-H2）：
    后台派发的 tool_response 为 launch 确认，可能含 agentId（驼峰）/
    agent_id（下划线）/task_id 等句柄键——形状自适应逐键尝试（含
    浅层嵌套 result 块），取不到为 null，绝不因 tool_response 形状
    异常抛错。§22.1 红线：不依赖 ZCode 内部 SQLite schema，此处只
    消费钩子载荷已公开的信息。

依赖：
    仅 Python 3 标准库 + runtime.journal（append-only 唯一写入口）。
    本模块不读写 state / permits 文件——permit 消费归 hook 流程，
    state 变更归 task_manager；被 hook 与测试直接调用。
"""

from runtime import journal

# agent_id 提取的候选键（驼峰 / 下划线双命名 + 通用 task 句柄；
# 顺序即优先级）
_AGENT_ID_KEYS = ("agentId", "agent_id", "task_id", "taskId")

# error 摘要提取的候选键（PostToolUseFailure 载荷）
_ERROR_KEYS = ("error", "message")

# error 摘要截断长度（journal 事件不倾倒长文本）
_MAX_ERROR_CHARS = 200


def _extract_agent_id(tool_response):
    """从 tool_response 形状自适应提取 agent 句柄；取不到返回 None。

    顶层逐键尝试 _AGENT_ID_KEYS；未命中再浅探一层嵌套 dict（常见
    {"result": {...}} 形态）。tool_response 非 dict / 值非非空 str
    一律跳过——本函数是观察辅助，永不抛错。
    """
    if not isinstance(tool_response, dict):
        return None
    for key in _AGENT_ID_KEYS:
        value = tool_response.get(key)
        if isinstance(value, str) and value:
            return value
    inner = tool_response.get("result")
    if isinstance(inner, dict):
        for key in _AGENT_ID_KEYS:
            value = inner.get(key)
            if isinstance(value, str) and value:
                return value
    return None


def _tool_use_id(payload):
    """从钩子载荷提取 tool_use_id（snake/camel 双命名容错），缺失 None。"""
    if not isinstance(payload, dict):
        return None
    for key in ("tool_use_id", "toolUseId"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _coerce_error_text(value):
    """把任意 error 值压成非空字符串并截断到 _MAX_ERROR_CHARS。"""
    if not isinstance(value, str):
        value = "unknown" if value is None else str(value)
    if value == "":
        value = "unknown"
    return value[:_MAX_ERROR_CHARS]


def record_agent_launch(repo_root, task_id, payload, *, permit) -> dict:
    """记 agent_launched 事件（PostToolUse 成功路径），返回事件 dict。

    permit 为 dispatch_wave.load_permit 的 dict（消费前后均可——本
    函数只读其字段）；payload 为钩子 stdin dict。字段形状冻结：
    {"event": "agent_launched", "unit": ..., "permit_id": ...,
     "tool_use_id": ..., "agent_id": ...（可 null）, "execution_mode": ...}
    """
    return journal.append_event(repo_root, task_id, {
        "event": "agent_launched",
        "unit": permit.get("unit_id") if isinstance(permit, dict) else None,
        "permit_id": permit.get("permit_id") if isinstance(permit, dict)
        else None,
        "tool_use_id": _tool_use_id(payload),
        "agent_id": _extract_agent_id(
            payload.get("tool_response")
            if isinstance(payload, dict) else None),
        "execution_mode": permit.get("mode") if isinstance(permit, dict)
        else None,
    })


def record_agent_failure(repo_root, task_id, payload, *, permit,
                         error=None) -> dict:
    """记 agent_dispatch_failed 事件（PostToolUseFailure 路径）。

    error 显式给定优先；否则从 payload 的 error / message 字段提取
    （值可为任意 JSON 类型，压成字符串）；再兜底 "unknown"；一律截断
    200 字符。permit 可为 dict 或 None（permit 不存在时仍记账失败，
    unit/permit_id 记 null）。
    """
    if error is None and isinstance(payload, dict):
        for key in _ERROR_KEYS:
            if key in payload:
                error = payload.get(key)
                break
    return journal.append_event(repo_root, task_id, {
        "event": "agent_dispatch_failed",
        "unit": permit.get("unit_id") if isinstance(permit, dict) else None,
        "permit_id": permit.get("permit_id") if isinstance(permit, dict)
        else None,
        "tool_use_id": _tool_use_id(payload),
        "error": _coerce_error_text(error),
    })


def record_launch_replay_skipped(repo_root, task_id, payload,
                                 *, permit_id) -> dict:
    """记 agent_launch_replay_skipped 事件（permit 已消费/不存在时
    PostToolUse 的幂等容错记账——launch 事实仍在，只是 permit 无法
    再次消费；不报错、不阻断）。"""
    return journal.append_event(repo_root, task_id, {
        "event": "agent_launch_replay_skipped",
        "tool_use_id": _tool_use_id(payload),
        "permit_id": permit_id,
    })
