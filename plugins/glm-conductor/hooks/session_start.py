#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GLM Conductor v2.1 M3 SessionStart 钩子——纯本地恢复上下文注入（wu-21-05）。

职责：
    以 ZCode 插件钩子（hooks/hooks.json 声明，matcher
    "startup|resume|clear|compact"——v2.7.0 W3 补 resume 源：官方
    源值域 startup/resume/clear/compact，应用重开会话恢复（resume）
    也触发恢复注入；改动为纯放宽（加法），stdin 源值本不消费）挂在
    SessionStart 事件上：新会话启动 / resume / compact / clear 时，
    查 runtime.recovery 的恢复发现（纯本地：
    state.json 只读；v2.4 P2-E 起为任务级最小恢复摘要——每任务仅
    task id / goal / repository / status / route / workflow run id /
    quota 等待态 / 下一安全动作，v2.3 遗留任务一行式，无任何单元级
    对账），把存在未完成任务时的 resume context 文本（头部逐字
    "GLM CONDUCTOR RESUME CONTEXT"）以 additionalContext 注入新会话，
    使主会话无需依赖模型记忆即可续作——连续性缺口 R2 的机械修复。

    无 active 任务 → stdout 恒空（完全安静，零噪音）；有 → stdout
    单行 JSON hookSpecificOutput（hookEventName="SessionStart"，
    additionalContext=恢复文本；ZCode 对钩子 stdout 做 Zod 严格校验，
    只接受标准形态，绝不夹带多余顶层键）。

§8.2 纯本地红线：
    本钩子不调模型、不联网、不执行 implementation、不消耗 quota、
    无 git subprocess——只调 recovery.build_recovery_summary /
    render_resume_context 两个本地读取面函数（timeoutMs 5000 内完成；
    v2.4 P2-E：两函数签名与 v2.3 一致，恢复渲染细节归 runtime.recovery
    的 v2.4 重写，本钩子只换注入文本内容、不动调用形状）。
    git 操作不做：恢复建议里提示由模型后续执行。

stdin 纪律：
    SessionStart 事件输入（source matcher 值等）不消费——事件发生即
    工作。stdin 读取只做容错（空串 / 非法 JSON / 读取异常一律不影响
    流程，与 v2.3 钩子外壳的 read_payload 同口径）。

会话见证（v2.7.0 W1 L1）：
    hooks.json 本条目 args 在脚本路径后追加 "${CLAUDE_SESSION_ID}"
    模板参数：宿主派发钩子进程时把该占位展开为当前会话 id；宿主未
    展开（老宿主 / 语义缺失）时脚本侧收到占位原文，作兜底跳过（见
    下）。main 读 sys.argv[1]（可能缺省 / 空串 / 占位原文）：仅当值
    形如 "sess_" 前缀 + 非空余量、且不含 "{"/"}" 占位残留时，把
    {"session_id": <id>, "updated_at": <UTC ISO>} 原子写
    <repo_root>/.glm-conductor/session-witness.json（json.dump 单行
    JSON；目录不存在即建；临时文件 + os.replace 原子替换）。
    W1 L1 依据：OS 载体（L1）唤醒命令需要会话 id（--resume <sess_id>），
    而本插件现无任何 session id 留存面——该见证文件是 OS 载体唤醒
    命令取会话 id 的唯一可靠来源。fail-open 同口径：任何异常 / 形态
    不符 → 跳过见证写、主流程恢复注入照常（绝不因见证写失败影响
    注入；timeoutMs 5000 内一次小文件写，不额外耗时面）。

fail-open 契约（与既有钩子外壳一致）：
    本钩子属恢复提示层，全路径 fail-open：任何内部异常（含 runtime
    模块不可用、stdin 异常）都不得阻塞会话启动——一律向 stderr 输出
    一行 "ENFORCEMENT DEGRADED: session_start failed: ..." 后 exit 0。

repo_root 口径：
    ZCODE_PROJECT_DIR 优先（ZCode 钩子进程注入），缺失回退当前工作
    目录——与 stop_gate 同口径。

来源：
    v2.1 设计（已蒸馏入 docs/architecture.md）；v2.4 Phase 2 P2-E
    （W5 恢复简化：恢复渲染路径随 runtime.recovery 重写换面）；
    v2.4 Phase 3 P3-D（Q4 收口：v2.3.1 起挂载的只读额度时钟 advisory
    渲染块随其后端整体删除——本钩子回归纯本地恢复上下文注入单一
    职责，主流程即 build_recovery_summary → render_resume_context →
    注入）。
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# 接线插件根以复用 runtime.recovery（钩子脚本与 runtime/ 同插件，
# 与 stop_gate 一致）
PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

# 降级报文模板（fail-open 契约的 stderr 一行；W3a 集中、W3b 语言重基线
# 核定英文值，ENFORCEMENT DEGRADED 前缀不变）
DEGRADED_FAILURE_MESSAGE_TEMPLATE = (
    "ENFORCEMENT DEGRADED: session_start failed: %r\n")


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


def write_session_witness(raw_session_id):
    """argv[1] 会话见证写（v2.7.0 W1 L1；fail-open：形态不符 / 任何
    异常都静默跳过，绝不影响主流程恢复注入）。

    仅当 raw_session_id 形如 "sess_" 前缀 + 非空余量、且不含 "{"/"}"
    占位残留（宿主未展开 "${CLAUDE_SESSION_ID}" 模板变量时脚本收到
    占位原文，兜底跳过）才写；载荷 {"session_id": <id>, "updated_at":
    <UTC ISO>}（json.dump 单行 JSON）原子写 <repo_root>/.glm-conductor/
    session-witness.json——目录不存在即建，临时文件 + os.replace 原子
    替换（读者只见完整旧文或完整新文）。任何异常吞掉返回：见证写是
    纯增量面，OS 载体唤醒命令的会话 id 来源，缺它只降级载体创建，
    与恢复注入零耦合。
    """
    try:
        raw = raw_session_id if isinstance(raw_session_id, str) else ""
        if not raw or raw == "sess_":
            return
        if "{" in raw or "}" in raw:
            return
        if not raw.startswith("sess_"):
            return
        witness_dir = Path(repo_root()) / ".glm-conductor"
        witness_dir.mkdir(parents=True, exist_ok=True)
        witness_path = witness_dir / "session-witness.json"
        tmp_path = witness_dir / "session-witness.json.tmp"
        payload = {
            "session_id": raw,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        with open(tmp_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        os.replace(tmp_path, witness_path)
    except Exception:
        return


def main():
    """主流程：读 stdin（容错，不消费）→ 会话见证写（fail-open）→
    本地恢复发现 → 有内容即注入。

    - sys.argv[1] 为 hooks.json 追加的 "${CLAUDE_SESSION_ID}" 模板
      参数（宿主展开语义，未展开时占位原文）——仅作会话见证写，
      形态不符 / 异常静默跳过；
    - 恢复文本以 hookSpecificOutput 形态单行 JSON 注入；无 active
      任务 → 静默 exit 0（stdout 恒空）；
    - 任何异常由模块入口 fail-open 兜底（stderr 降级标记 + exit 0，
      绝不阻塞会话启动）。
    """
    read_stdin_tolerant()

    # 会话见证写（v2.7.0 W1 L1）：argv[1] 形态合规才落盘；自吞异常，
    # 见证写失败绝不影响后续恢复注入
    raw_session_id = sys.argv[1] if len(sys.argv) > 1 else ""
    write_session_witness(raw_session_id)

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
        sys.stderr.write(DEGRADED_FAILURE_MESSAGE_TEMPLATE % (exc,))
        sys.exit(0)
