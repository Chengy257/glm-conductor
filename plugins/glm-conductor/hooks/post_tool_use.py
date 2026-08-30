#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GLM Conductor v2.1 PostToolUse / PostToolUseFailure 钩子（M2 后半）。

职责：
    Agent|Task 工具调用的运行时生命周期记账。hooks.json 的
    PostToolUse 与 PostToolUseFailure 两条 matcher "Agent|Task" 条目
    均指向本脚本，按 stdin 载荷的 hook_event_name 分发：

      - PostToolUse（派发成功，tool_response 为 launch 确认）：
        从 tool_input 的 prompt/description 解析 marker
        （GLM_CONDUCTOR_DISPATCH=<permit_id>，§6.4）→ 定位持有该
        permit 的活动任务 → consume_permit（原子 rename，重放机械
        拒绝）→ agent_run.record_agent_launch 记 agent_launched 事件
        （runtime-observed 生命周期，与手写 implementation_started
        互不替代）。permit 已消费/不存在 → record_launch_replay_
        skipped 幂等容错（不报错）。
      - PostToolUseFailure（派发失败）：invalidate_permit（作废，
        不得重复使用）+ record_agent_failure（agent_dispatch_failed，
        error 摘要自载荷提取）。

    消费时点（※DR R3）：PreToolUse 校验只读不写；本钩子在 launch
    成为事实后消费——两次重放派发时第二次在 PreToolUse 即被拒，
    本层的 replay 容错仅覆盖极端竞态。

静默纪律：
    本钩子不 deny、不改写输入（PostToolUse 输出只能 additionalContext
    ——本钩子恒不输出，stdout 恒空）；无 marker / 无归属任务 /
    事件名不识别的调用一律静默放行（只读类委派与无关工具不记账）。

fail-open 契约（※DR D6，与 pre_tool_use 同）：
    任何内部异常（含 runtime 模块不可用、stdin 异常、permits 文件
    异常）→ stderr 一行 "ENFORCEMENT DEGRADED: post_tool_use failed:
    ..." + exit 0——记账层降级可见，绝不阻断主流程（完成面
    fail-closed 由 Stop 门承担）。

依赖：
    Python 3 标准库 + runtime.dispatch_wave / runtime.agent_run /
    runtime.state（延迟 import，失败走 fail-open）。
"""

import json
import sys
from pathlib import Path

# 接线插件根以复用 runtime（钩子脚本与 runtime/ 同插件，与
# pre_tool_use 一致）
PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

# 本脚本服务的事件名（其余 hook_event_name 一律静默——防误挂到
# 别的 matcher 上时产生副作用）
SERVED_EVENTS = ("PostToolUse", "PostToolUseFailure")


def repo_root():
    """返回被检查的仓库根：优先 ZCODE_PROJECT_DIR（ZCode 钩子进程注入），
    缺失时回退当前工作目录（与 pre_tool_use 同口径）。"""
    import os
    return os.environ.get("ZCODE_PROJECT_DIR") or os.getcwd()


def read_payload():
    """读取并解析 stdin 的事件载荷，返回 dict（任何输入问题按 {} 处理）。"""
    try:
        raw = sys.stdin.read()
    except Exception:
        return {}
    if not raw or not raw.strip():
        return {}
    try:
        payload = json.loads(raw)
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _marker_text(tool_input):
    """把 tool_input 中的 prompt / description 文本拼为 marker 检索面。

    tool_input 非 dict / 字段非 str 一律按空串容错；两字段都可能
    携带 marker（§6.4：主会话把它放进 prompt 或 description）。
    """
    parts = []
    for key in ("prompt", "description"):
        value = tool_input.get(key) if isinstance(tool_input, dict) else None
        if isinstance(value, str) and value:
            parts.append(value)
    return "\n".join(parts)


def _owning_task(repo, active_tasks, permit_id):
    """在活动任务中定位持有该 permit 的任务，返回 (task_id, permit)。

    先试活跃 permit（load_permit；consumed/invalidated 视同不存在）；
    未命中再按 retired 文件名（<id>.consumed.json / .invalidated.json）
    检索——replay_skipped 与「无 permit 失败记账」仍需归属任务才能
    落 journal，但 permit 返回 None（retired 不是可用许可，只提供
    归属线索）。两者皆未命中返回 (None, None)。只扫活动任务：终态
    任务的 permit 生命周期已结束，记账无意义。
    """
    from runtime import dispatch_wave

    for task_id in active_tasks:
        permit = dispatch_wave.load_permit(repo, task_id, permit_id)
        if permit is not None:
            return task_id, permit
    permits_root = dispatch_wave.permits_dir
    for task_id in active_tasks:
        directory = permits_root(repo, task_id)
        for suffix in (".consumed.json", ".invalidated.json"):
            if (directory / (permit_id + suffix)).is_file():
                return task_id, None
    return None, None


def main():
    """主流程：读 stdin → 按 hook_event_name 分发两条记账路径。

    - 非 PostToolUse / PostToolUseFailure → 静默（exit 0）；
    - 无 marker / 无归属活动任务 → 静默（无 permit 的 Agent 调用
      是只读类或与 conductor 无关，不记账）；
    - PostToolUse → consume + agent_launched（permit 缺失时
      replay_skipped 容错）；
    - PostToolUseFailure → invalidate + agent_dispatch_failed
      （permit 缺失时仅记失败事件）。
    """
    from runtime import agent_run, dispatch_wave, state

    payload = read_payload()
    event_name = payload.get("hook_event_name")
    if not isinstance(event_name, str):
        event_name = payload.get("hookEventName")
    if event_name not in SERVED_EVENTS:
        return 0

    permit_id = dispatch_wave.parse_marker(
        _marker_text(payload.get("tool_input")))
    if permit_id is None:
        return 0

    repo = repo_root()
    task_id, permit = _owning_task(repo, state.find_active_tasks(repo),
                                   permit_id)
    if task_id is None:
        return 0

    if event_name == "PostToolUse":
        if permit is None:
            # 极端竞态：PreToolUse 放行后、PostToolUse 前被并发消费/
            # 过期清理——launch 事实仍在，容错记账不报错
            agent_run.record_launch_replay_skipped(
                repo, task_id, payload, permit_id=permit_id)
            return 0
        dispatch_wave.consume_permit(repo, task_id, permit_id)
        agent_run.record_agent_launch(repo, task_id, payload, permit=permit)
        return 0

    # PostToolUseFailure：作废 permit（不得重复使用）+ 失败记账；
    # permit 已不在（过期/已消费）则只记失败事件
    if permit is not None:
        dispatch_wave.invalidate_permit(repo, task_id, permit_id)
    agent_run.record_agent_failure(repo, task_id, payload, permit=permit)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as exc:  # fail-open：记账层降级可见，绝不阻断
        sys.stderr.write(
            "ENFORCEMENT DEGRADED: post_tool_use failed: %r\n" % (exc,))
        sys.exit(0)
