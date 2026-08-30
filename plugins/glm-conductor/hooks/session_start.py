#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GLM Conductor v2.1 M3 SessionStart 钩子——纯本地恢复上下文注入（wu-21-05）。

职责：
    以 ZCode 插件钩子（hooks/hooks.json 声明，matcher
    "startup|clear|compact"）挂在 SessionStart 事件上：新会话启动 /
    compact / clear 时，查 runtime.recovery 的恢复发现（纯本地：
    state.json + events.jsonl + 原生档案只读 adapter），把存在未完成
    任务时的 resume context 文本（§8.3 形态，头部逐字
    "GLM CONDUCTOR RESUME CONTEXT"）以 additionalContext 注入新会话，
    使主会话无需依赖模型记忆即可续作——连续性缺口 R2 的机械修复。

    无 active 任务 → stdout 恒空（完全安静，零噪音）；有 → stdout
    单行 JSON hookSpecificOutput（hookEventName="SessionStart"，
    additionalContext=恢复文本；ZCode 对钩子 stdout 做 Zod 严格校验，
    只接受标准形态，绝不夹带多余顶层键）。

§8.2 纯本地红线：
    本钩子不调模型、不联网、不执行 implementation、不消耗 quota、
    无 git subprocess——只调 recovery.build_recovery_summary /
    render_resume_context 两个本地读取面函数（timeoutMs 5000 内完成）。
    git 操作不做：恢复建议里提示由模型后续执行。

stdin 纪律：
    SessionStart 事件输入（source matcher 值等）不消费——事件发生即
    工作。stdin 读取只做容错（空串 / 非法 JSON / 读取异常一律不影响
    流程，与 pre_tool_use.read_payload 同口径）。

fail-open 契约（与 pre_tool_use 一致）：
    本钩子属恢复提示层，全路径 fail-open：任何内部异常（含 runtime
    模块不可用、stdin 异常）都不得阻塞会话启动——一律向 stderr 输出
    一行 "ENFORCEMENT DEGRADED: session_start failed: ..." 后 exit 0。

repo_root 口径：
    ZCODE_PROJECT_DIR 优先（ZCode 钩子进程注入），缺失回退当前工作
    目录——与 pre_tool_use 同口径。

来源：
    docs/GLM-Conductor-v2.1-Architecture-Agent-Implementation-Plan.md
    §8（SessionStart Recovery，全部）+ §2.3-H2。
"""

import json
import os
import sys
from pathlib import Path

# 接线插件根以复用 runtime.recovery（钩子脚本与 runtime/ 同插件，
# 与 pre_tool_use / stop_gate 一致）
PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))


def repo_root():
    """返回被检查的仓库根：优先 ZCODE_PROJECT_DIR（ZCode 钩子进程注入，
    Phase 0 实测），缺失时回退当前工作目录。"""
    return os.environ.get("ZCODE_PROJECT_DIR") or os.getcwd()


def read_stdin_tolerant():
    """容错读取 stdin 全文并返回（内容不消费，仅为事件形态兼容）。

    SessionStart 的恢复决策只依赖本地任务状态，事件输入（含 source
    matcher 值）一律不解读；读取异常 / 非文本输入返回 ""，绝不因
    stdin 形态异常降级。
    """
    try:
        return sys.stdin.read()
    except Exception:
        return ""


def main():
    """主流程：读 stdin（容错，不消费）→ 本地恢复发现 → 有内容即注入。

    - build_recovery_summary / render_resume_context 两函数无异常时：
      文本为空（无 active 任务）→ 静默 exit 0（stdout 恒空）；有文本 →
      stdout 单行 JSON hookSpecificOutput（SessionStart /
      additionalContext）后 exit 0；
    - 任何异常由模块入口 fail-open 兜底（stderr 降级标记 + exit 0，
      绝不阻塞会话启动）。
    """
    read_stdin_tolerant()

    # import 放在函数内（模块顶部 sys.path 接线已就绪），
    # import 失败会被外层 fail-open 捕获
    from runtime import recovery

    text = recovery.render_resume_context(
        recovery.build_recovery_summary(repo_root()))
    if not text:
        return 0  # 无 active 任务 → 完全安静（零噪音）
    output = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": text,
        }
    }
    sys.stdout.write(json.dumps(output) + "\n")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as exc:  # fail-open：恢复提示层降级可见，绝不阻塞启动
        sys.stderr.write(
            "ENFORCEMENT DEGRADED: session_start failed: %r\n" % (exc,))
        sys.exit(0)
