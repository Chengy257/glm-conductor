#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GLM Conductor v2 Stop 完成门钩子——骨架（v2 alpha1 过渡态）。

职责：
    以 ZCode 插件钩子（hooks/hooks.json 声明）挂在 Stop 事件上，作为 v2
    强制层的第一个落点。现阶段只做观测：
      - 无活动任务（无 .glm-conductor/tasks/*/state.json 或全部终态）时
        静默放行——普通会话零干预；
      - 发现活动任务时向 stderr 输出一行观测报告后放行；
      - Layer A 完成门校验（touched ⊆ owned）在下一阶段填入。

fail-open 契约：
    本钩子属「非关键路径降级可见」（计划 §3.1）——任何内部异常都不得阻断
    会话：一律 exit 0，降级时向 stderr 报告 ENFORCEMENT DEGRADED。本钩子
    绝不 block（永不输出 {"decision": "block"}），stdout 恒为空——ZCode
    对钩子 stdout 做 Zod 严格校验，一切报告只走 stderr。
"""

import json
import os
import sys
from pathlib import Path

# 接线插件根以复用 runtime.state（钩子脚本与 runtime/ 同插件）
PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))


def repo_root():
    """返回被检查的仓库根：优先 ZCODE_PROJECT_DIR（ZCode 钩子进程注入，
    Phase 0 实测），缺失时回退当前工作目录。"""
    return os.environ.get("ZCODE_PROJECT_DIR") or os.getcwd()


def read_stop_event():
    """读取并解析 stdin 的 Stop 事件输入，返回 dict（任何输入问题按 {} 处理）。

    输入为 Claude 兼容 JSON（含 session_id / tool_name / stop_hook_active
    等字段），可能为空串或非 JSON——骨架不因输入问题降级，一律容错为空
    对象。stdout 恒为空，因此只读不写。
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


def main():
    """骨架主流程：stop_hook_active 提前放行 → 发现活动任务 → stderr 观测报告。

    任何路径都 return 0（放行），绝不向 stdout 写任何内容。
    """
    # 1) 读 stdin；解析结果只在 stop_hook_active 为 true 时用于提前放行
    #    （防续跑循环）。该检查放在活动任务发现之前（省一次扫描，语义相同）
    payload = read_stop_event()
    if payload.get("stop_hook_active"):
        return 0

    # 2) 复用 runtime.state 作为活动任务的确定性状态源；import 放在函数内
    #    （其上的 sys.path 接线已就绪），import 失败会被外层 fail-open 捕获
    from runtime import state

    # 3) 发现活动任务：空 → 静默放行；非空 → stderr 一行观测报告后放行
    #    （绝不 block，无 stdout 输出）
    active = state.find_active_tasks(repo_root())
    if active:
        sys.stderr.write(
            "ENFORCEMENT SKELETON: %d active task(s) [%s]; "
            "completion gate not yet enforcing (v2 alpha1)\n"
            % (len(active), ", ".join(active)))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as exc:  # fail-open：降级可见
        sys.stderr.write("ENFORCEMENT DEGRADED: stop_gate skeleton failed: %r\n" % (exc,))
        sys.exit(0)
