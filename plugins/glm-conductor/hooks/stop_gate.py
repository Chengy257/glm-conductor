#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GLM Conductor v2 Stop 完成门钩子——升级指南 §15 四重检查强制流水线。

职责：
    以 ZCode 插件钩子（hooks/hooks.json 声明）挂在 Stop 事件上，对每个
    「参与校验」的活动任务按 §15 顺序做四重检查，首个失败即拒绝完成
    （stdout 单行 block JSON 请求模型续跑），全部通过时静默放行——
    任务不能带着未完成的验证 / 未通过的审查 / 过期的证据通过完成门：
      1. ownership（Layer A，touched ⊆ owned）：声明了 ownership.files 的
         任务，git 工作区实际改动文件必须全部落在声明范围内
         （git_touched_files + classify_paths 分桶，越界即 block）；
      2. verification：verification.required 列出的验证命令必须全部出现
         在 completed（缺失 → verification_missing），且 verification.
         fingerprint 与当前证据指纹（fingerprint.task_fingerprint，B3.2）
         一致——验证后文件又变化 → verification_stale；
      3. review：review.required 为 True 时，verdict 为 ship 且绑定当前
         指纹才放行——None / missing / not-required → review_missing，
         词汇外取值（手写 state.json 的拼写偏差，如 "Ship"）同样按
         review_missing 处理（不静默放宽为 ship 路径），fix-first /
         rethink → review_rejected，审查后文件又变化或 verdict 已标记
         stale → review_stale；
      4. visual：visual_evidence 记录的视觉证据按文件自身字节哈希比对，
         记录后发生变化 → visual_stale（§22）。
    参与校验判定：ownership 声明非空 / verification.required 非空 /
    review.required 为 True / visual_evidence 非空——四者任一成立即参与；
    全部不成立 → 跳过（普通会话与合规任务零干预）。gate_passed /
    gate_degraded 的记账对象即参与集合。

与 Layer A 单检版（B2.1）的差异：
      - violation 三元组从 (task_id, out_of_scope, patterns) 改为
        (task_id, check, detail)：check ∈ {"ownership",
        "verification_missing", "verification_stale", "review_missing",
        "review_rejected", "review_stale", "visual_stale"}，detail 为
        逐 check 的结构化字段 dict；
      - block 报文按 check 分发（英文、可行动、逐行列出），其余违规
        任务以 "Also failing in task <id>: <check>" 附带列出；
      - 消费证据指纹层（runtime.fingerprint）：指纹每任务至多计算一次，
        verification 与 review 共用同一 current 值；
      - evaluate 阶段的结构性错误（指纹 rev-parse 失败 / 声明模式非法
        等 OwnershipError / FingerprintError）并入降级路径
        （gate_degraded，reason=evaluation_error）。

fail-open 策略：
    降级路径（绝不拦会话，stderr 报 ENFORCEMENT DEGRADED，exit 0）：
      - 钩子自身任何异常（import 失败、状态损坏等）→ 兜底放行；
      - git 失败 / 非 git 仓库（touched 清单不可得）→ 跳过本轮校验，
        并向全部参与校验的任务记 gate_degraded（reason=git_unavailable；
        无人参与时记第一个活动任务留运行痕迹）——git 短暂故障不卡会话；
      - evaluate 阶段的结构性错误（指纹 rev-parse 失败 / ownership 声明
        模式非法等 OwnershipError / FingerprintError）→ 同样降级放行，
        记 gate_degraded（reason=evaluation_error）。
    强制路径（唯一会 block 的情形）：
      - 参与校验的任务在四重检查中有任一失败（七种 check）。这是有意
        决策，不算异常；block 走 stdout JSON，退出码仍为 0。
    stdout 纪律：运行时对钩子 stdout 做 Zod 严格校验，除 block 的单行
    JSON 外任何路径都不得写 stdout；一切报告只走 stderr。

完成提交（P0-1：completed 的唯一提交点）：
    任务收尾时模型把 status 写为 finalizing（= 请求完成；公共状态 API
    拒绝直达 completed，见 runtime.state.TASK_TRANSITIONS）。Stop 事件
    触发本钩子四重检查：有违规照常 block（状态保持 finalizing，修复后
    重新 Stop）；全部通过时本钩子在放行路径上代表 runtime 对**全部**
    status == finalizing 的活动任务（不限于参与校验集合）调用
    state.commit_completion 原子提交 completed，并向任务 journal 追加
    completed 事件（via=completion_gate，绑定当前证据指纹）。单任务提交
    失败（任何异常）仅 stderr 报警并继续下一任务——记账异常绝不崩放行
    路径（fail-open）；exhausted / degraded / block 路径一律不提交。

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
    → 连续链断开 → 重新计数。上限对全部七种 check 一视同仁。

来源：
    docs/glm-conductor-v2-upgrade-guide-final.md §9（Ownership Gate
    Layer A）/ §15（Stop Completion Gate，四重检查 + block 报文可行动）/
    §16（Stop Hook loop safety，有界续行）/ §17-§20（证据指纹与验证 /
    审查 stale 拦截）/ §22（视觉证据）+ docs/glm-conductor-v2-implementation-
    plan.md §3.1（fail-open 降级可见契约）/ §3.5（Layer A 完成门）
    + v2 升级计划工作块 B2.1 / B3.1 / B3.2 / B4.1。
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


# —— block 报文构造（§15：英文、可行动、逐行列出） ——

def _fingerprint_short(value):
    """视觉证据报文里的指纹呈现：前 12 字符 + "..."；缺失（None）→ "missing"。"""
    if not isinstance(value, str) or value == "":
        return "missing"
    return value[:12] + "..."


def _recorded_display(value):
    """stale 报文里的 recorded 呈现：指纹串原样；None / 非法 → "none"。"""
    if not isinstance(value, str) or value == "":
        return "none"
    return value


def build_block_reason(violation, other_violations):
    """构造 block 报文 reason（英文、可行动，指南 §15 风格；逐行可执行）。

    violation 为 (task_id, check, detail) 三元组，按 check 分发到对应
    报文模板（七种 check，模板逐字锁定）；其余违规任务以
    "Also failing in task <id>: <check>" 附带列出（存在时）。
    """
    task_id, check, detail = violation
    if check == "ownership":
        lines = [
            "Completion blocked: ownership violation (task %s)." % task_id,
            "Out-of-scope files in current diff:",
        ]
        for path in detail["out_of_scope"]:
            lines.append("- %s" % path)
        lines.append("Declared ownership:")
        for pattern in detail["patterns"]:
            lines.append("- %s" % pattern)
        lines.append(
            "Either extend state.json ownership.files with the intentional paths,")
        lines.append("or revert the out-of-scope changes. Then finish again.")
    elif check == "verification_missing":
        lines = [
            "Completion blocked: required parent verification is incomplete"
            " (task %s)." % task_id,
            "",
            "Missing:",
        ]
        for command in detail["missing"]:
            lines.append("- %s" % command)
        lines.append("")
        lines.append(
            "Run each command yourself in the repo, then record the result and the")
        lines.append(
            "current evidence fingerprint via runtime.state.record_verification.")
    elif check == "verification_stale":
        lines = [
            "Completion blocked: verification evidence is stale (task %s)."
            % task_id,
            "",
            "Files changed after verification: current fingerprint %s != "
            "recorded %s." % (detail["current"],
                              _recorded_display(detail["recorded"])),
            "",
            "Re-run the required verification commands and record the new "
            "fingerprint",
            "via runtime.state.record_verification.",
        ]
    elif check == "review_missing":
        lines = [
            "Completion blocked: review required but not completed (task %s)."
            % task_id,
            "",
            "Reviewer: %s" % (detail["reviewer"] or "unassigned"),
            "",
            "Dispatch the reviewer, then record the verdict and fingerprint via",
            "runtime.state.record_review.",
        ]
    elif check == "review_rejected":
        lines = [
            "Completion blocked: review verdict is '%s' (task %s)."
            % (detail["verdict"], task_id),
            "",
            "Repair per the review findings, then obtain a fresh review and record",
            "it via runtime.state.record_review.",
        ]
    elif check == "review_stale":
        lines = [
            "Completion blocked: review evidence is stale (task %s)." % task_id,
            "",
            "Files changed after review: current fingerprint %s != review "
            "fingerprint %s." % (detail["current"],
                                 _recorded_display(detail["recorded"])),
            "",
            "Re-review the current change set and record the fresh verdict via",
            "runtime.state.record_review.",
        ]
    elif check == "visual_stale":
        lines = [
            "Completion blocked: visual evidence changed after review (task %s)."
            % task_id,
            "",
            "Stale evidence:",
        ]
        for item in detail["stale"]:
            lines.append("- %s (recorded %s, current %s)" % (
                item["path"], _fingerprint_short(item["recorded"]),
                _fingerprint_short(item["current"])))
        lines.append("")
        lines.append(
            "Re-capture the visual evidence, re-run visual review, then record via")
        lines.append("runtime.state.record_visual_evidence.")
    else:  # 防御：词汇表外的新 check 落到通用报文（词汇封闭后不可达）
        lines = ["Completion blocked: %s (task %s)." % (check, task_id)]
    for other_id, other_check, _other_detail in other_violations:
        lines.append("Also failing in task %s: %s" % (other_id, other_check))
    return "\n".join(lines)


# —— §15 四重检查求值 ——

def _section(task_state, key):
    """读取 task_state 的子 dict（缺失 / 非 dict → 空 dict，容忍形状异常）。"""
    value = task_state.get(key)
    return value if isinstance(value, dict) else {}


def _nonempty_strs(value):
    """list 中非空 str 项（非 list → 空列表；形状异常容忍，不静默放宽语义）。"""
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item != ""]


def task_participates(task_state):
    """§15 参与判定：ownership / verification / review / visual 任一声明
    非空即参与校验；全部不成立 → 跳过（不参与 Layer A 也不参与新检查）。"""
    if _nonempty_strs(_section(task_state, "ownership").get("files")):
        return True
    if _nonempty_strs(_section(task_state, "verification").get("required")):
        return True
    if _section(task_state, "review").get("required") is True:
        return True
    visual = task_state.get("visual_evidence")
    if isinstance(visual, list) and visual:
        return True
    return False


def evaluate_task(task_id, task_state, repo, touched, base):
    """对单个参与任务按 §15 顺序做四重检查，首个失败即返回。

    返回 None（全部通过，或四项声明全空不参与）或 (check, detail)；
    task_id 仅供调用方关联（violation 组装与报错上下文），本函数不在
    返回值中重复携带。

    证据指纹惰性求值：current 在 verification 指纹比对或 review 指纹
    比对首次需要时才算，每任务至多一次，两处共用——ownership / 命令
    清单层面的失败不触发 git 指纹计算。

    参数 touched / base 为调用方取好的 git 改动清单与基线修订号（单次
    Stop 内各取一次、跨任务复用——每次 Stop 恒为 2 次 git 子调用），
    透传给 fingerprint.task_fingerprint（与主会话记录证据同一入口，
    属指纹层自身契约；主会话侧用缺省调用自取，两者语义等价）。

    classify_paths 的模式非法（OwnershipError）与 task_fingerprint 的
    结构性错误（OwnershipError / FingerprintError）自然向上抛，由调用
    方并入降级路径（不静默放宽）。
    """
    from runtime import fingerprint, ownership

    # 1) ownership：touched ⊆ owned？
    patterns = _nonempty_strs(_section(task_state, "ownership").get("files"))
    if patterns:
        _owned_hits, out_of_scope = ownership.classify_paths(touched, patterns)
        if out_of_scope:
            return ("ownership",
                    {"out_of_scope": out_of_scope, "patterns": patterns})

    # current 指纹惰性缓存：verification 与 review 共用，每任务至多算一次
    current_cache = {}

    def current_fingerprint():
        if "value" not in current_cache:
            current_cache["value"] = fingerprint.task_fingerprint(
                repo, task_state, touched=touched, base=base)
        return current_cache["value"]

    # 2) verification：required 全部完成，且证据指纹新鲜
    verification = _section(task_state, "verification")
    required = _nonempty_strs(verification.get("required"))
    if required:
        completed = _nonempty_strs(verification.get("completed"))
        missing = [command for command in required if command not in completed]
        if missing:
            return ("verification_missing", {"missing": missing})
        current = current_fingerprint()
        recorded = verification.get("fingerprint")
        if recorded is None or recorded != current:
            return ("verification_stale",
                    {"recorded": recorded, "current": current})

    # 3) review：required 为 True 才检查（非 True / 缺键跳过）
    review = _section(task_state, "review")
    if review.get("required") is True:
        verdict = review.get("verdict")
        if verdict is None or verdict in ("missing", "not-required"):
            return ("review_missing", {"reviewer": review.get("reviewer")})
        if verdict not in ("fix-first", "rethink", "stale", "ship"):
            # 词汇外取值（手写 state.json 的拼写偏差，如 "Ship"/"ship "）
            # 按 review_missing 处理，不静默放宽为 ship 路径（detail
            # 携带 verdict 原值；报文模板不变）
            return ("review_missing",
                    {"reviewer": review.get("reviewer"), "verdict": verdict})
        if verdict in ("fix-first", "rethink"):
            return ("review_rejected", {"verdict": verdict})
        # "stale"（审查者标记失效）与 "ship" 都要指纹比对（此处才惰性计算）
        current = current_fingerprint()
        recorded = review.get("fingerprint")
        if verdict == "stale":
            return ("review_stale", {"recorded": recorded, "current": current})
        # verdict == "ship"（词汇外已在上方按 review_missing 处理）
        if recorded is None or recorded != current:
            return ("review_stale", {"recorded": recorded, "current": current})

    # 4) visual：visual_evidence 非空时逐项按文件字节哈希比对（§22）
    visual = task_state.get("visual_evidence")
    if isinstance(visual, list) and visual:
        statuses = fingerprint.visual_evidence_status(repo, visual)
        stale = [
            {"path": item["path"], "recorded": item["recorded"],
             "current": item["current"]}
            for item in statuses if item["stale"]]
        if stale:
            return ("visual_stale", {"stale": stale})

    return None


def collect_violations(state, repo, touched, base, active):
    """逐任务做 §15 四重检查（ownership → verification → review → visual）。

    返回 (violations, declared)：
      - violations：违规列表 [(task_id, check, detail)]（每任务首失败即
        记，§15 顺序）；
      - declared：参与校验的任务 ID 列表（§15 参与判定，见
        task_participates）——gate_passed 的记账对象（断链只对被校验的
        任务有意义）；gate_degraded 同记 declared，无人参与时由调用方
        兜底记 active[0]（保留降级运行痕迹）。

    - state.json 在发现后消失（load_state → None）同样按未参与跳过；
    - touched / base 为调用方取好的单份清单与基线（跨任务复用，见
      evaluate_task docstring），原样透传；
    - classify_paths / task_fingerprint 的结构性错误自然向上抛，由调用方
      并入降级路径（不静默放宽）。
    """
    violations = []
    declared = []
    for task_id in active:
        task_state = state.load_state(repo, task_id)
        if task_state is None:
            continue
        if not task_participates(task_state):
            continue
        declared.append(task_id)
        result = evaluate_task(task_id, task_state, repo, touched, base)
        if result is not None:
            check, detail = result
            violations.append((task_id, check, detail))
    return violations, declared


def participating_ids(state, repo, active):
    """返回参与完成门校验的活动任务 ID（§15 参与判定；降级路径记账用）。"""
    ids = []
    for task_id in active:
        task_state = state.load_state(repo, task_id)
        if task_state is None:
            continue
        if task_participates(task_state):
            ids.append(task_id)
    return ids


def record_for_tasks(journal, repo, task_ids, event):
    """向多个任务 journal 记同一事件（gate_passed / gate_degraded 用）。"""
    for task_id in task_ids:
        journal.append_event(repo, task_id, dict(event))


def commit_finalizing_completions(state, fingerprint, journal, repo,
                                  active, touched, base):
    """完成提交：把全部 status == finalizing 的活动任务原子提交 completed。

    仅在四重检查全绿的放行路径调用（block / exhausted / degraded 一律
    不到这里）——completed 的唯一提交点。遍历**全部**活动任务而非仅参与
    校验集合：finalizing 本身就是完成请求，无声明的 finalizing 任务同样
    要在放行时被提交。逐任务流程：重算当前证据指纹（与门同一入口）→
    state.commit_completion（内部经 _gate_commit 通道落盘 completed）→
    journal 追加 completed 事件（via=completion_gate，绑定指纹）。单任务
    失败（任何 Exception）warn_stderr 报警后继续下一任务：状态保持
    finalizing，绝不因记账异常崩掉放行路径（fail-open）。
    """
    for task_id in active:
        try:
            task_state = state.load_state(repo, task_id)
            if task_state is None or task_state.get("status") != "finalizing":
                continue
            fp = fingerprint.task_fingerprint(
                repo, task_state, touched=touched, base=base)
            state.commit_completion(repo, task_id)
            journal.append_event(
                repo, task_id,
                {"event": "completed", "task_id": task_id,
                 "via": "completion_gate", "fingerprint": fp})
        except Exception as exc:
            warn_stderr(
                "ENFORCEMENT DEGRADED: cannot commit completion for task "
                "%s (%r); task left in finalizing" % (task_id, exc))


def main():
    """§15 四重检查主流程（架构师锁定设计）。

    返回值恒为 0（block 是 stdout JSON 决策，不用退出码 2）；
    除 block 的单行 JSON 外不向 stdout 写任何内容。
    注意：stop_hook_active=true（续行循环中的 Stop）**照常校验**——这正是
    运行时 3 次续行额度的工作方式（第 1/2 次 block、第 3 次由 journal
    计数放行）；提前放行会让续行中的完成声明免检。
    全绿放行路径额外做完成提交：对全部 finalizing 任务原子提交
    completed 并记 completed 事件（见模块 docstring「完成提交」）。
    """
    # 1) 读 stdin（容错；载荷当前不参与分支决策，保留解析以备扩展）
    read_stop_event()

    # runtime 模块在函数内 import：其上的 sys.path 接线已就绪，
    # import 失败会被外层 fail-open 捕获
    from runtime import fingerprint, ownership, state

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
        record_for_tasks(
            journal, repo,
            participating_ids(state, repo, active) or [active[0]],
            {"event": "gate_degraded", "reason": "git_unavailable"})
        return 0

    # 5) 基线修订号 + 逐任务 §15 四重检查：touched 与 base 单次 Stop 内
    #    各取一次、跨任务复用（每次 Stop 恒为 2 次 git 子调用：1 次
    #    status + 1 次 rev-parse，多任务不再叠加 rev-parse）；基线解析与
    #    evaluate 阶段的结构性错误（rev-parse 突然失败 / 声明模式非法
    #    等）并入同一降级路径——不静默放宽，也不卡会话
    try:
        base = fingerprint.resolve_base(repo)
        violations, declared = collect_violations(state, repo, touched, base,
                                                  active)
    except (ownership.OwnershipError, fingerprint.FingerprintError) as exc:
        warn_stderr(
            "ENFORCEMENT DEGRADED: cannot evaluate completion gate (%s); "
            "gate skipped" % (exc,))
        from runtime import journal
        record_for_tasks(
            journal, repo,
            participating_ids(state, repo, active) or [active[0]],
            {"event": "gate_degraded", "reason": "evaluation_error"})
        return 0

    # 6) 无违规 → 放行：stdout/stderr 完全静默；对全部参与校验的任务记
    #    gate_passed（断链用——否则上一轮的 gate_blocked 残留会让下轮
    #    续行计数起点错位；同时留"何时通过完成门"的审计痕迹。无人参与
    #    时不记——没有校验发生。无活动任务时已在第 3 步提前返回，普通
    #    会话零写入）。随后做完成提交：对全部 finalizing 任务原子提交
    #    completed（P0-1：completed 的唯一提交点在本门内；单任务失败
    #    逐任务降级放行，exhausted / degraded / block 路径不经过这里）
    if not violations:
        from runtime import journal
        record_for_tasks(
            journal, repo, declared, {"event": "gate_passed", "tasks": declared})
        commit_finalizing_completions(
            state, fingerprint, journal, repo, active, touched, base)
        return 0

    # 7) 有违规：block 报文 / journal 记账以第一个违规任务为准
    first_task, first_check, first_detail = violations[0]

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
            dict({"event": "gate_exhausted", "check": first_check},
                 **first_detail))
        return 0

    # 7b) 未达上限 → block：stdout 单行 JSON 请求续跑 + 记 gate_blocked
    emit_block_json(build_block_reason(violations[0], violations[1:]))
    from runtime import journal
    journal.append_event(
        repo, first_task,
        dict({"event": "gate_blocked", "check": first_check,
              "task_id": first_task}, **first_detail))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as exc:  # fail-open：钩子自身崩溃绝不阻断会话
        warn_stderr("ENFORCEMENT DEGRADED: stop_gate failed: %r" % (exc,))
        sys.exit(0)
