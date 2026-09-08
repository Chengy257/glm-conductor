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

v2.3.1 增补（unit w1-session-advisory）：
    main() 在恢复发现之外追加只读 Quota Clock advisory
    （render_clock_advisory）：读用户级 Global Quota Clock state
    （clock_store.load_clock_state 单次容错 JSON 读，绝不打开
    SQLite / 任何 DB），clock 已绑定且 last_tick_at 陈旧（now -
    last_tick_at > 2×fallback_interval_minutes——与 cli 的
    quota-clock-status tick_stale 同口径；last_tick_at 缺失 / 非数值
    视为陈旧，fallback_interval_minutes 缺失 / 非法按 60 折算）时，
    在 additionalContext 末尾（与 resume text 空行分隔）注入恢复指引：
    建议在专用低成本 Flash 交互会话显式执行 quota-clock-bind
    <new_automation_id> replace，声明「未执行自动会话迁移」，并指路
    quota-clock-status 做权威诊断。红线：advisory 层只读零写、绝不
    bind / replace / 迁移、不探测会话；state 缺失（unbound 不提示）/
    tick 新鲜 / 任何内部异常 → ""（输出与既有行为逐字节一致，零噪音）。

来源：
    docs/history/v2.1/GLM-Conductor-v2.1-Architecture-Agent-Implementation-Plan.md
    §8（SessionStart Recovery，全部）+ §2.3-H2；v2.3.1 计划 P1.1/P1.2
    （SessionStart Quota Clock advisory，unit w1-session-advisory）。
"""

import json
import os
import sys
import time
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


# —— v2.3.1 Quota Clock advisory（unit w1-session-advisory） ——

# advisory 标识头（逐字冻结，测试锚定）
CLOCK_ADVISORY_HEADER = "GLM CONDUCTOR QUOTA CLOCK ADVISORY"

# fallback_interval_minutes 缺失 / 非法时的折算缺省（与 cli.py
# quota-clock-status 的 tick_stale 容错同值——阈值口径镜像彼处）
_CLOCK_FALLBACK_MINUTES_FALLBACK = 60


def _clock_fallback_minutes(clock_state):
    """clock state → fallback_interval_minutes 容错读（缺失 / 非法 →
    60；bool 拒绝）——与 quota-clock-status（runtime/commands/
    quota_clock.py）的同款容错一致。"""
    minutes = clock_state.get("fallback_interval_minutes")
    if isinstance(minutes, bool) or not isinstance(minutes, (int, float)):
        return _CLOCK_FALLBACK_MINUTES_FALLBACK
    return minutes


def _clock_tick_stale(clock_state, *, now_ms):
    """clock state → tick 是否陈旧（quota-clock-status 同口径，纯函数）。

    now_ms - last_tick_at > 2 × fallback_interval_minutes × 60000 即
    陈旧；last_tick_at 缺失 / 非数值（含 bool）同样视为陈旧；
    fallback_interval_minutes 缺失 / 非法按 60 折算——逐条镜像
    quota-clock-status（runtime/commands/quota_clock.py）的 tick_stale
    （阈值与容错的单一真相源在彼处，此处只做 state-file-only 启发式
    的同口径镜像）。"""
    last_tick_at = clock_state.get("last_tick_at")
    fallback_minutes = _clock_fallback_minutes(clock_state)
    return (isinstance(last_tick_at, bool)
            or not isinstance(last_tick_at, (int, float))
            or now_ms - last_tick_at > 2 * fallback_minutes * 60000)


def render_clock_advisory(*, now_ms=None):
    """只读 clock state → 陈旧 tick 时的 advisory 文本（无则 ""）。

    触发（启发式，state-file-only）：clock 已绑定（用户级
    quota-clocks/<identity_hash>.json 存在且为可识别 v1 state）且
    last_tick_at 陈旧（_clock_tick_stale，与 quota-clock-status 的
    tick_stale 同口径）。返回文本要素：标识头 + 陈旧事实 + 恢复指引
    （专用低成本 Flash 交互会话内显式 quota-clock-bind
    <new_automation_id> replace）+ 「未执行自动会话迁移」声明
    （clock.NO_AUTO_MIGRATION_DECLARATION 逐字）+ 会话专用性不可
    机械验证声明（clock.DEDICATED_SESSION_UNVERIFIABLE）+ 指路
    quota-clock-status 权威诊断（DB 级 needs_replacement 彼处判定，
    本处绝不越权）。

    红线：只读（唯一 I/O = load_clock_state 的一次容错 JSON 读，
    绝不打开 SQLite / 任何 DB，state / env / DB 零写）、绝不执行
    bind / replace / 迁移、不探测会话、不做主会话推断。state 缺失
    （unbound 不提示未绑定者）/ tick 新鲜 / 任何内部异常（identity
    不可得、import 失败、读盘异常）→ 一律返回 ""：advisory 层
    fail-open 静默，绝不波及既有 resume context 路径，也绝不阻塞
    会话启动（零噪音红线：无陈旧 clock 时输出与无本函数完全一致）。

    now_ms：仅供测试注入当前时刻（epoch 毫秒）；None → time.time()。
    """
    try:
        from runtime.quota import clock, clock_store
        from runtime.quota.identity import compute_provider_identity_hash
        identity = compute_provider_identity_hash()
        # 唯一 I/O：clock state JSON 的一次容错读（损坏 / 缺失 → None）
        clock_state = clock_store.load_clock_state(identity)
        if not isinstance(clock_state, dict):
            return ""  # unbound / 损坏 → 零输出（不提示未绑定者）
        if now_ms is None:
            now_ms = int(time.time() * 1000)
        fallback_minutes = _clock_fallback_minutes(clock_state)
        if not _clock_tick_stale(clock_state, now_ms=now_ms):
            return ""  # tick 新鲜 → 零输出（零噪音红线）
        automation_id = clock_state.get("automation_id")
        if not isinstance(automation_id, str) or automation_id == "":
            automation_id = "unknown"
        lines = [
            CLOCK_ADVISORY_HEADER,
            "Quota clock state (automation_id=%s): last_tick_at is more "
            "than %d minutes behind (threshold: 2 x fallback_interval_"
            "minutes) - the clock automation likely stopped ticking "
            "(stale or needs replacement)."
            % (automation_id, int(2 * fallback_minutes)),
            "To restore: from a fresh dedicated low-cost Flash interactive "
            "session, explicitly run quota-clock-bind <new_automation_id> "
            "to replace the dead binding.",
            "This hook is advisory only: %s（本插件不做隐式迁移或自动 "
            "rebind）；本钩子未执行任何 bind / replace / state 写入。"
            % clock.NO_AUTO_MIGRATION_DECLARATION,
            "Dedicated session placement is %s by this plugin - verify "
            "manually." % clock.DEDICATED_SESSION_UNVERIFIABLE,
            "Authoritative diagnosis: run quota-clock-status (DB-level "
            "needs_replacement / placement / recovery_guidance).",
        ]
        return "\n".join(lines)
    except Exception:
        return ""  # advisory 层 fail-open：静默降级，绝不波及 resume 路径


def main():
    """主流程：读 stdin（容错，不消费）→ 本地恢复发现 → clock advisory
    → 有内容即注入。

    - resume 文本与 clock advisory 拼接注入（resume 在前、advisory 在
      后，空行分隔）；仅 advisory 有内容时同样以 hookSpecificOutput
      形态输出；两者皆空 → 静默 exit 0（stdout 恒空）；
    - render_clock_advisory 内部自 fail-open（异常 → ""，不波及
      resume 路径）；其余任何异常由模块入口 fail-open 兜底（stderr
      降级标记 + exit 0，绝不阻塞会话启动）。
    """
    read_stdin_tolerant()

    # import 放在函数内（模块顶部 sys.path 接线已就绪），
    # import 失败会被外层 fail-open 捕获
    from runtime import recovery

    text = recovery.render_resume_context(
        recovery.build_recovery_summary(repo_root()))
    advisory = render_clock_advisory()
    if advisory:
        if text:
            text = text + "\n\n" + advisory
        else:
            text = advisory
    if not text:
        return 0  # 无 active 任务且无 advisory → 完全安静（零噪音）
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
