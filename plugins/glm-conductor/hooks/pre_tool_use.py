#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GLM Conductor v2 Ownership Gate Layer B——派发时注入钩子（PreToolUse）。

职责（advisory，提示级）：
    以 ZCode 插件钩子（hooks/hooks.json 声明，matcher "Agent|Task"）挂在
    PreToolUse 事件上，在主会话每次派发子代理（Agent / Task 工具）前，
    查询 runtime.state 中声明了 ownership.files（非空）的活动任务，并把
    ownership 契约提醒以 additionalContext 注入派发上下文，使实施者在
    开工前再次看到自己的文件边界。

与 Layer A 的分工：
    - Layer B（本钩子）：提示级注入，只能「提高合规率」，无法确定性约束
      子代理行为——advisory only，绝不 deny、绝不阻断派发；
    - Layer A（hooks/stop_gate.py）：Stop 完成门，对最终 diff 做
      touched ⊆ owned 的确定性校验，越界改动无法静默通过完成门。
    B 提高合规、A 兜底强制，两层互补（指南 §9）。

fail-open 契约：
    本钩子属 advisory 层，全路径 fail-open：任何内部异常（含 runtime.state
    不可用、stdin 异常）都不得影响派发——一律向 stderr 输出一行
    "ENFORCEMENT DEGRADED: pre_tool_use failed: ..." 后 exit 0 放行。
    stdout 只在成功注入路径写一行 JSON（ZCode 对钩子 stdout 做 Zod 严格
    校验，只接受 hookSpecificOutput 标准形态，绝不夹带多余顶层键），
    其余任何路径 stdout 恒为空。

来源：
    docs/glm-conductor-v2-upgrade-guide-final.md §9
    （Ownership Gate —— Layer B: dispatch-time injection）+ 实施计划 B2.3。
"""

import json
import os
import sys
from pathlib import Path

# 接线插件根以复用 runtime.state（钩子脚本与 runtime/ 同插件，与 stop_gate 一致）
PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

# 注入提醒最多列出的任务数（超出部分注明省略，避免提醒文本膨胀）
MAX_INJECTED_TASKS = 3


def repo_root():
    """返回被检查的仓库根：优先 ZCODE_PROJECT_DIR（ZCode 钩子进程注入，
    Phase 0 实测），缺失时回退当前工作目录。"""
    return os.environ.get("ZCODE_PROJECT_DIR") or os.getcwd()


def read_payload():
    """读取并解析 stdin 的 PreToolUse 事件输入，返回 dict（任何输入问题按 {} 处理）。

    输入为 Claude 兼容 JSON（含 session_id / tool_name / tool_input 等字段），
    可能为空串或非 JSON。本钩子的注入决策只依赖活动任务状态，payload 仅
    为事件形态兼容而读取，因此空 / 非法 JSON / 非 dict 一律容错为空对象，
    不构成降级理由。
    """
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


def declared_ownership_tasks(repo_root):
    """返回 [(task_id, [pattern, ...])]：声明了非空 ownership.files 的活动任务。

    以 state.find_active_tasks() 为活动任务的确定性来源（终态任务不返回），
    再逐个 load_state 读取 ownership.files；未声明（键缺失 / 空数组 /
    结构异常 / 非字符串项）的任务跳过——advisory 层宁可少注入也不误报。
    顺序沿用 find_active_tasks 的目录名排序。
    """
    # import 放在函数内（模块顶部 sys.path 接线已就绪），
    # import 失败会被外层 fail-open 捕获
    from runtime import state

    pairs = []
    for task_id in state.find_active_tasks(repo_root):
        try:
            loaded = state.load_state(repo_root, task_id)
        except (ValueError, OSError):
            # 状态文件在扫描与读取之间损坏 / 被移除 → 跳过该任务
            continue
        if not isinstance(loaded, dict):
            continue
        ownership = loaded.get("ownership")
        files = ownership.get("files") if isinstance(ownership, dict) else None
        # 仅保留非空字符串 pattern（脏数据防御），过滤后为空视同未声明
        patterns = [item for item in files
                    if isinstance(item, str) and item] \
            if isinstance(files, list) else []
        if not patterns:
            continue
        pairs.append((task_id, patterns))
    return pairs


def build_reminder(ownership_pairs):
    """把 [(task_id, patterns)] 渲染为注入提醒文本（英文，逐任务一段）。

    超过 MAX_INJECTED_TASKS 个任务时只列前 MAX_INJECTED_TASKS 个
    （目录名序），并追加一行省略说明；末尾两行固定（派发提示必须约束
    实施者到这些路径 + Layer A 完成门兜底警告）。
    """
    lines = ["GLM CONDUCTOR OWNERSHIP REMINDER (Layer B):"]
    shown = ownership_pairs[:MAX_INJECTED_TASKS]
    omitted = len(ownership_pairs) - len(shown)
    for task_id, patterns in shown:
        # 每个活动任务独立一段（空行分隔），段内逐条列出 ownership 路径
        lines.append("")
        lines.append("active task %s declares ownership:" % task_id)
        for pattern in patterns:
            lines.append("- %s" % pattern)
    if omitted > 0:
        lines.append("")
        lines.append("(%d more active task(s) with declared ownership omitted)"
                     % omitted)
    lines.append("")
    lines.append(
        "Dispatch prompts MUST constrain the implementer to these paths.")
    lines.append(
        "Out-of-scope changes will block task completion (Stop gate Layer A).")
    return "\n".join(lines)


def main():
    """主流程：读 stdin（容错）→ 发现声明 ownership 的活动任务 → 注入提醒。

    无任何声明 ownership 的活动任务 → 静默放行（stdout 恒空）；有 → 向
    stdout 输出单行 JSON hookSpecificOutput（PreToolUse / additionalContext）
    后放行。任何路径 return 0；绝不 deny、绝不 block。
    """
    # 1) 读 stdin；payload 仅保持事件形态兼容，注入决策只看活动任务状态
    read_payload()

    # 2) 发现声明了非空 ownership.files 的活动任务；无 → 静默放行
    ownership_pairs = declared_ownership_tasks(repo_root())
    if not ownership_pairs:
        return 0

    # 3) 唯一 stdout 写点：单行 JSON（ensure_ascii=True 保证 Windows 任意
    #    控制台编码下可安全写出，非 ASCII 字符以 \uXXXX 转义，仍为合法
    #    JSON；形态与 Zod 严格校验一致，无多余顶层键）
    reminder = build_reminder(ownership_pairs)
    output = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": reminder,
        }
    }
    sys.stdout.write(json.dumps(output) + "\n")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as exc:  # fail-open：advisory 层降级可见，绝不阻断派发
        sys.stderr.write(
            "ENFORCEMENT DEGRADED: pre_tool_use failed: %r\n" % (exc,))
        sys.exit(0)
