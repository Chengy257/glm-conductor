#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GLM Conductor v2 Stop 完成门钩子——Layer A 强制（touched ⊆ owned 校验）。

职责：
    以 ZCode 插件钩子（hooks/hooks.json 声明）挂在 Stop 事件上，对每个
    声明了 ownership.files 的活动任务校验「当前 git 工作区的实际改动
    文件是否全部落在声明范围内」（touched ⊆ owned?）。存在越界改动时
    拒绝完成（输出 block 请求模型续跑），无越界 / 无声明时静默放行——
    任务不能在有越界改动时通过完成门。

与骨架（v2 alpha1 过渡态）的差异：
      - 废止 ENFORCEMENT SKELETON 观测报文：通过时完全静默（stdout 与
        stderr 均无输出），普通会话与合规任务零干预；
      - 新增 Layer A 强制：git_touched_files + classify_paths 做
        「touched ⊆ owned」判定，违规即 block（stdout 单行
        {"decision":"block","reason":...}，运行时据此让模型续跑）；
      - 新增 journal 记账：gate_blocked（拦截）/ gate_passed（校验通过
        放行，断链 + 审计）/ gate_exhausted（续行上限到顶放行）/
        gate_degraded（git 不可用降级跳过）。

fail-open 策略：
    降级路径（绝不拦会话，stderr 报 ENFORCEMENT DEGRADED，exit 0）：
      - 钩子自身任何异常（import 失败、状态损坏等）→ 兜底放行；
      - git 失败 / 非 git 仓库（OwnershipError）→ 跳过 ownership 校验，
        并向全部参与校验的任务记 gate_degraded（无人声明时记第一个活动
        任务留运行痕迹）——git 短暂故障不卡会话。
    强制路径（唯一会 block 的情形）：
      - 任务声明了 ownership.files 且当前 diff 存在越界改动。这是有意
        决策，不算异常；block 走 stdout JSON，退出码仍为 0。
    stdout 纪律：运行时对钩子 stdout 做 Zod 严格校验，除 block 的单行
    JSON 外任何路径都不得写 stdout；一切报告只走 stderr。

续行上限机制（无额外状态文件，用任务 journal 做跨调用计数）：
    运行时对 Stop block 续行内建上限：每 turn 最多 3 次（钩子无法关闭）。
    续行循环中的 Stop（stop_hook_active=true）**照常校验**——运行时的
    3 次续行额度正是靠每次 Stop 都校验来消费的（第 1/2 次 block、第 3
    次由本钩子放行）；提前放行会让续行中的完成声明免检。
    本钩子从第一个违规任务的 journal 尾部向前数连续 gate_blocked 条数 N：
      - N >= 2 → 已达上限：stderr 报 ENFORCEMENT GATE EXHAUSTED 后放行，
        并记 gate_exhausted（第 3 次 Stop 不再 block）；
      - 否则 block（本次 block 后链上再加一条，下次 N+1）。
    「连续」= 逐条向前直到遇到任一非 gate_blocked 事件或耗尽——模型在
    两次 block 之间完成了真实工作（journal 出现其他事件，含 gate_passed）
    → 连续链断开 → 重新计数。

来源：
    docs/glm-conductor-v2-upgrade-guide-final.md §9（Ownership Gate
    Layer A）/ §15（Stop Completion Gate，block 报文可行动）/ §16（Stop
    Hook loop safety，有界续行）+ docs/glm-conductor-v2-implementation-plan.md
    §3.1（fail-open 降级可见契约）/ §3.5（Layer A 完成门）。
"""

import json
import os
import sys
from pathlib import Path

# 接线插件根以复用 runtime（钩子脚本与 runtime/ 同插件）
PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

# 续行上限：journal 尾部连续 gate_blocked 达到该条数即不再 block
# （第 1、2 次 block，第 3 次 Stop 放行——对齐运行时 3 次续行内建上限）
GATE_BLOCK_LIMIT = 2


def repo_root():
    """返回被检查的仓库根：优先 ZCODE_PROJECT_DIR（ZCode 钩子进程注入，
    Phase 0 实测），缺失时回退当前工作目录。"""
    return os.environ.get("ZCODE_PROJECT_DIR") or os.getcwd()


def read_stop_event():
    """读取并解析 stdin 的 Stop 事件输入，返回 dict（任何输入问题按 {} 处理）。

    输入为 Claude 兼容 JSON（含 session_id / stop_hook_active 等字段），
    可能为空串或非 JSON——不因输入问题降级，一律容错为空对象。
    本函数只读不写：stdout 是运行时严格校验的通道，不得污染。
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


def warn_stderr(message):
    """向 stderr 写一行报警（英文报文，前缀 ENFORCEMENT 风格）。

    走 sys.stderr.buffer 显式 UTF-8 字节写：Windows 管道默认 locale 编码
    对异常摘要里的非 ASCII 字符可能编码失败（UnicodeEncodeError），那会把
    一次降级报告整个吞掉。ASCII 前缀在任何解码端都稳定可读。写入本身也
    容错——报警失败绝不能反过来让钩子崩溃（fail-open）。
    """
    text = message.rstrip("\n") + "\n"
    try:
        stream = getattr(sys.stderr, "buffer", None)
        if stream is not None:
            stream.write(text.encode("utf-8", "replace"))
            stream.flush()
        else:  # 防御：无 buffer（如被重载的文本流）时退回文本写
            sys.stderr.write(text)
    except Exception:
        pass


def emit_block_json(reason):
    """stdout 精确输出一行 block JSON（UTF-8；除此之外无任何输出）。

    reason 内的换行由 json.dumps 转义为 \\n，物理上仍是单行。走
    sys.stdout.buffer 显式 UTF-8 编码：避免 Windows 管道默认 locale
    编码对非 ASCII 路径编码失败（那会把一次有意 block 变成异常降级）。
    """
    line = json.dumps({"decision": "block", "reason": reason},
                      ensure_ascii=False)
    data = (line + "\n").encode("utf-8")
    stream = getattr(sys.stdout, "buffer", None)
    if stream is not None:
        stream.write(data)
        stream.flush()
    else:  # 防御：无 buffer（如被重载的文本流）时退回文本写
        sys.stdout.write(line + "\n")
        sys.stdout.flush()


def count_trailing_gate_blocks(repo, task_id):
    """从任务 journal 尾部向前数连续 gate_blocked 条数（无文件 → 0）。

    连续 = 逐条向前直到遇到任一非 gate_blocked 事件或耗尽。模型在两次
    block 之间完成真实工作（journal 出现其他事件）→ 链断 → 重新计数。
    read_events 的容错语义（坏行跳过）天然适配部分写入场景。
    """
    from runtime import journal

    count = 0
    for item in reversed(journal.read_events(repo, task_id)):
        if item.get("event") == "gate_blocked":
            count += 1
        else:
            break
    return count


def build_block_reason(violation, other_violations):
    """构造 block 报文 reason（英文、可行动，指南 §15 风格；路径逐行列出）。

    violation 为（task_id, out_of_scope, patterns）三元组；其余违规任务
    以 "Also out of scope in task <id>: <path>, ..." 附带列出（存在时）。
    """
    task_id, out_of_scope, patterns = violation
    lines = [
        "Completion blocked: ownership violation (task %s)." % task_id,
        "Out-of-scope files in current diff:",
    ]
    for path in out_of_scope:
        lines.append("- %s" % path)
    lines.append("Declared ownership:")
    for pattern in patterns:
        lines.append("- %s" % pattern)
    lines.append(
        "Either extend state.json ownership.files with the intentional paths,")
    lines.append("or revert the out-of-scope changes. Then finish again.")
    for other_id, other_oos, _other_patterns in other_violations:
        lines.append("Also out of scope in task %s: %s"
                     % (other_id, ", ".join(other_oos)))
    return "\n".join(lines)


def collect_violations(state, ownership, repo, touched, active):
    """逐任务做 Layer A 校验。

    返回 (violations, declared)：
      - violations：违规列表 [(task_id, out_of_scope, patterns)]；
      - declared：声明了 ownership.files（非空）且实际参与校验的任务 ID 列表
        ——gate_passed 的记账对象（断链只对被校验的任务有意义，未声明
        任务无链可断不记）；gate_degraded 同记 declared，无人声明时
        由调用方兜底记 active[0]（保留降级运行痕迹）。

    - state.json 在发现后消失（load_state → None）同样按未声明跳过；
    - 声明模式非法时 classify_paths 抛 OwnershipError（结构性错误），
      自然向上抛，由外层 fail-open 兜底（不静默放宽）。
    """
    violations = []
    declared = []
    for task_id in active:
        task_state = state.load_state(repo, task_id)
        if task_state is None:
            continue
        patterns = (task_state.get("ownership") or {}).get("files") or []
        if not patterns:
            continue
        declared.append(task_id)
        _owned_hits, out_of_scope = ownership.classify_paths(touched, patterns)
        if out_of_scope:
            violations.append((task_id, out_of_scope, patterns))
    return violations, declared


def record_for_tasks(journal, repo, task_ids, event):
    """向多个任务 journal 记同一事件（gate_passed / gate_degraded 用）。"""
    for task_id in task_ids:
        journal.append_event(repo, task_id, dict(event))


def main():
    """Layer A 主流程（架构师锁定设计）。

    返回值恒为 0（block 是 stdout JSON 决策，不用退出码 2）；
    除 block 的单行 JSON 外不向 stdout 写任何内容。
    注意：stop_hook_active=true（续行循环中的 Stop）**照常校验**——这正是
    运行时 3 次续行额度的工作方式（第 1/2 次 block、第 3 次由 journal
    计数放行）；提前放行会让续行中的完成声明免检。骨架时代的"防自锁
    提前返回"已废止（那时无 block 能力）。
    """
    # 1) 读 stdin（容错；载荷当前不参与分支决策，保留解析以备扩展）
    read_stop_event()

    # runtime 模块在函数内 import：其上的 sys.path 接线已就绪，
    # import 失败会被外层 fail-open 捕获
    from runtime import ownership, state

    # 2) 被检仓库根
    repo = repo_root()

    # 3) 发现活动任务：空 → 静默放行（普通会话零干预）
    active = state.find_active_tasks(repo)
    if not active:
        return 0

    # 4) touched 清单；git 失败 / 非 git 仓库 → 降级放行（fail-open），
    #    向全部参与校验的任务记 gate_degraded（降级可见 + 断链可溯源）
    try:
        touched = ownership.git_touched_files(repo)
    except ownership.OwnershipError as exc:
        warn_stderr(
            "ENFORCEMENT DEGRADED: cannot list touched files (%s); "
            "ownership gate skipped" % (exc,))
        from runtime import journal
        _violations, declared = collect_violations(
            state, ownership, repo, [], active)
        record_for_tasks(
            journal, repo, declared or [active[0]],
            {"event": "gate_degraded", "reason": "git_unavailable"})
        return 0

    # 5) 逐任务 Layer A 校验，收集违规任务与参与校验的任务
    violations, declared = collect_violations(
        state, ownership, repo, touched, active)

    # 6) 无违规 → 放行：stdout/stderr 完全静默；对全部参与校验的任务记
    #    gate_passed（断链用——否则上一轮的 gate_blocked 残留会让下轮
    #    续行计数起点错位；同时留"何时通过完成门"的审计痕迹。无人声明
    #    ownership 时不记——没有校验发生。无活动任务时已在第 3 步提前
    #    返回，普通会话零写入）
    if not violations:
        from runtime import journal
        record_for_tasks(
            journal, repo, declared, {"event": "gate_passed", "tasks": declared})
        return 0

    # 7) 有违规：block 报文 / journal 记账以第一个违规任务为准
    first_violation = violations[0]
    first_task = first_violation[0]

    # 7a) 续行上限：journal 尾部连续 gate_blocked 已达 GATE_BLOCK_LIMIT
    #     → 放行 + stderr 报警 + 记 gate_exhausted（第 3 次 Stop 不再 block）
    if count_trailing_gate_blocks(repo, first_task) >= GATE_BLOCK_LIMIT:
        warn_stderr(
            "ENFORCEMENT GATE EXHAUSTED: task %s blocked twice already; "
            "runtime 3-attempt limit reached, allowing stop. The model MUST "
            "report the blocked state to the user and MUST NOT claim "
            "completion." % first_task)
        from runtime import journal
        journal.append_event(
            repo, first_task,
            {"event": "gate_exhausted",
             "out_of_scope": first_violation[1]})
        return 0

    # 7b) 未达上限 → block：stdout 单行 JSON 请求续跑 + 记 gate_blocked
    emit_block_json(build_block_reason(first_violation, violations[1:]))
    from runtime import journal
    journal.append_event(
        repo, first_task,
        {"event": "gate_blocked",
         "out_of_scope": first_violation[1],
         "task_id": first_task})
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as exc:  # fail-open：钩子自身崩溃绝不阻断会话
        warn_stderr("ENFORCEMENT DEGRADED: stop_gate failed: %r" % (exc,))
        sys.exit(0)
