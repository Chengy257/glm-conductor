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
      3. review：review.required 为 True 或 route 推导要求审查（mode 为
         audit / full，或 assurance 为 high——手写 state.json 无法靠漏写
         review.required 跳过审查检查）时，verdict 为 ship 且绑定当前
         指纹才放行——None / missing / not-required → review_missing，
         词汇外取值（手写 state.json 的拼写偏差，如 "Ship"）同样按
         review_missing 处理（不静默放宽为 ship 路径），fix-first /
         rethink → review_rejected，审查后文件又变化或 verdict 已标记
         stale → review_stale；
      4. visual：visual_evidence 记录的视觉证据按文件自身字节哈希比对，
         记录后发生变化 → visual_stale（§22）。
    参与校验判定：ownership 声明非空 / verification.required 非空 /
    review.required 为 True（或 route 推导要求审查）/ visual_evidence
    非空——四者任一成立即参与；
    全部不成立 → 跳过（普通会话与合规任务零干预）。gate_passed /
    gate_degraded 的记账对象即参与集合。

与 Layer A 单检版（B2.1）的差异：
      - violation 三元组从 (task_id, out_of_scope, patterns) 改为
        (task_id, check, detail)：check ∈ {"ownership",
        "verification_missing", "verification_stale", "review_missing",
        "review_rejected", "review_stale", "visual_stale",
        "corrupt_state"}，detail 为逐 check 的结构化字段 dict；
      - block 报文按 check 分发（英文、可行动、逐行列出），其余违规
        任务以 "Also failing in task <id>: <check>" 附带列出；
      - 消费证据指纹层（runtime.fingerprint）：指纹每任务至多计算一次，
        verification 与 review 共用同一 current 值；
      - evaluate 阶段的结构性错误（指纹 rev-parse 失败 / 声明模式非法
        等 OwnershipError / FingerprintError）并入降级路径
        （gate_degraded，reason=evaluation_error）。

per-task 仓库求值（RB-2，release hardening）：
    账本根（ledger_root：ZCODE_PROJECT_DIR 或 cwd，语义 = 任务账本根）
    不再同时充当 git 根：每个参与校验的任务按 state.repository.root
    解析其专属仓库根（绑定优先；无绑定 → legacy 回退账本根，行为与
    单仓时代完全一致），ownership / 指纹 / 视觉检查全部在该根上求值；
    账本（state.json / events.jsonl / gate 记账）恒在账本根，绝不换根。
    同一仓库根的 touched + base 单次 Stop 内至多取一次（repo_cache，
    每个不同仓库根恒为 2 次 git 子调用：1 次 status + 1 次 rev-parse），
    同根多任务不重复 git。dogfood 动机：workspace 非 git 仓库而真实
    仓库是其子目录时，旧实现的全局 git 早退使整个完成门 fail-open；
    现按任务绑定求值，单任务仓库故障的爆炸半径从整次 Stop 缩为单 task。

fail-open 策略（RB-2 起按任务隔离）：
    降级路径（绝不拦会话，stderr 报 ENFORCEMENT DEGRADED，exit 0）：
      - 钩子自身任何异常（import 失败、状态损坏等）→ 兜底放行；
      - 仓库解析 / git 失败 → 只降级该任务（stderr 报警 + 该任务
        journal gate_degraded），其余任务照常求值。降级 reason 结构化
        词汇（gate_degraded 事件的 reason 字段，可精确断言）：
          repository_unavailable——任务绑定 repository.root 但该根的
            git 操作失败（非 git 目录 / git 故障）；
          repository_root_missing——legacy 任务（无绑定）且账本根非
            git 根、一级子目录扫描找不到任何 .git 候选；
          repository_ambiguous——legacy 任务且账本根非 git 根、一级
            子目录存在 ≥1 个 .git 候选（stderr 列出候选目录名；即使
            只有 1 个也不自动猜、不自动绑定——绑定必须由 state.json
            repository.root 显式声明）；
          git_unavailable——legacy 任务且账本根本身是 git 根，但 git
            操作瞬时失败（touched / rev-parse 不可得）；
          evaluation_error——求值期结构性错误（指纹 rev-parse 失败 /
            ownership 声明模式非法等 OwnershipError / FingerprintError，
            原全局路径改按任务隔离）。
      被降级任务不参与本轮 gate_passed 记账；全绿放行路径的完成提交
      按任务各自仓库根取指纹——被降级任务的指纹同样取不到，天然保持
      finalizing（H1：completed 只能来自门内全绿提交）。corrupt 高保障
      任务的 fail-closed 证据只依赖 journal，不依赖任何 git 根，不再被
      其他任务的仓库故障牵连（原全局早退拆除后的自然结果）。
    强制路径（唯一会 block 的情形）：
      - 参与校验的任务在四重检查中有任一失败（八种 check）。这是有意
        决策，不算异常；block 走 stdout JSON，退出码仍为 0。
    stdout 纪律：运行时对钩子 stdout 做 Zod 严格校验，除 block 的单行
    JSON 外任何路径都不得写 stdout；一切报告只走 stderr。

发现完整性（H3/P0-3）：
    活动任务发现改用 state.discover_tasks 对 tasks_root 下全部子目录
    四分类，state.json 损坏不再被解释成「没有任务需要强制」：
      - active（state.json 可读且非终态）→ 照常走四重检查；
      - terminal → 与完成门无关；
      - orphaned（目录存在但无 state.json）→ 永不拦截：stderr 报警 +
        向该目录 journal 记 gate_degraded（reason=orphaned_task）；
      - corrupt（state.json 存在但解析 / 任务标识归一失败或读取
        OSError）→ 读该目录 journal 找高保障证据（route_selected 的
        mode ∈ (audit, full) 或 assurance=high，或 status_changed 的
        to=finalizing）：有 → fail-closed 并入违规清单（check=
        corrupt_state，走统一 block / exhaustion 机器，报文含恢复指引），
        且在发现阶段即 stderr 报警（deferred for enforcement）；RB-2 起
        corrupt 证据只依赖 journal、不依赖任何 git 根，恒进统一 block
        机器，不再被其他任务的仓库故障降级早退牵连；
        无 → 降级放行（stderr 报警 + journal 记 gate_degraded，
        reason=corrupt_state）。
    由此区分 no task（静默零干预）与 unreadable task（结构化报警，
    高保障时拦截）。

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
    → 连续链断开 → 重新计数。上限对全部八种 check 一视同仁。

来源：
    docs/glm-conductor-v2-upgrade-guide-final.md §9（Ownership Gate
    Layer A）/ §15（Stop Completion Gate，四重检查 + block 报文可行动）/
    §16（Stop Hook loop safety，有界续行）/ §17-§20（证据指纹与验证 /
    审查 stale 拦截）/ §22（视觉证据）+ docs/glm-conductor-v2-implementation-
    plan.md §3.1（fail-open 降级可见契约）/ §3.5（Layer A 完成门）
    + v2 升级计划工作块 B2.1 / B3.1 / B3.2 / B4.1
    + docs/GLM-Conductor-v2.0.1-Release-Hardening-Patch-Agent-Implementation-Plan.md
    （RB-2 / WU-P3：任务绑定专属仓库根，per-task 仓库求值 + 按任务隔离
    降级，三 + 二个结构化 reason 词汇，歧义不猜）。
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


def ledger_root():
    """返回任务账本根：优先 ZCODE_PROJECT_DIR（ZCode 钩子进程注入，
    Phase 0 实测），缺失时回退当前工作目录。

    RB-2 起语义收窄为「账本根」（.glm-conductor/tasks 发现与 state /
    journal I/O 恒在此根，绝不换根）；git 求值根按任务绑定解析
    （state.resolve_repository_root：绑定 repository.root 优先，legacy
    回退本根），不再默认两者同根。
    """
    return os.environ.get("ZCODE_PROJECT_DIR") or os.getcwd()


def _repo_snapshot(repo_cache, root):
    """取仓库根的改动快照（touched 清单 + 基线修订号），单 Stop 内每个
    不同仓库根至多 1 次 git status + 1 次 git rev-parse（RB-2 per-repo
    缓存）。

    repo_cache 形态 {root_str: {"touched": [...], "base": str} 或异常
    实例}：命中快照直接返回；命中异常原样重抛（同根重复失败不重复调
    git）；未命中才真正取 git 对（ownership.git_touched_files +
    fingerprint.resolve_base，OwnershipError / FingerprintError 自然向上
    抛，由调用方按任务降级并写入缓存）。
    """
    from runtime import fingerprint, ownership

    key = str(root)
    if key in repo_cache:
        cached = repo_cache[key]
        if isinstance(cached, Exception):
            raise cached
        return cached
    try:
        touched = ownership.git_touched_files(root)
        base = fingerprint.resolve_base(root)
    except (ownership.OwnershipError, fingerprint.FingerprintError) as exc:
        repo_cache[key] = exc
        raise
    snapshot = {"touched": touched, "base": base}
    repo_cache[key] = snapshot
    return snapshot


def _nested_repo_candidates(ledger, scan_cache):
    """账本根一级子目录中的 .git 候选扫描（legacy 歧义判定用，RB-2）。

    只扫一层：账本根的直接子目录中含 .git 条目者（目录或 worktree /
    submodule 指针文件均算），返回候选目录名的排序列表。结果按账本根
    缓存（scan_cache）——同一次 Stop 内多个 legacy 任务只扫一次；
    listdir 失败容错为空列表（扫描不可得与无候选同语义：不猜仓库）。
    """
    key = str(ledger)
    if key in scan_cache:
        return scan_cache[key]
    candidates = []
    try:
        names = sorted(os.listdir(ledger))
    except OSError:
        names = []
    for name in names:
        directory = os.path.join(ledger, name)
        if os.path.isdir(directory) \
                and os.path.exists(os.path.join(directory, ".git")):
            candidates.append(name)
    scan_cache[key] = candidates
    return candidates


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

    首参语义（RB-2）：任务账本根——journal 恒在账本根，与 git 求值根
    无关（参数名保留 repo 仅为签名兼容）。
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


def _corrupt_requires_fail_closed(repo, task_id):
    """corrupt 任务的 fail-closed 证据判定（H3/P0-3），返回布尔值。

    首参语义（RB-2）：任务账本根——journal 恒在账本根，与 git 求值根
    无关（参数名保留 repo 仅为签名兼容）。
    state.json 不可读时从任务 journal 找高保障证据（read_events 容错读，
    坏行跳过）：任一 route_selected 事件 mode ∈ ("audit", "full") 或
    assurance == "high"，或任一 status_changed 事件 to == "finalizing"
    → True——该任务此前处于需要完成门强制的高保障 / 完成请求路径上，
    状态损坏不得成为静默放行通道。journal 缺失 / 为空 / 无此类证据
    → False（走降级放行：stderr 报警 + gate_degraded 记账）。
    """
    from runtime import journal

    for item in journal.read_events(repo, task_id):
        name = item.get("event")
        if name == "route_selected":
            if item.get("mode") in ("audit", "full") \
                    or item.get("assurance") == "high":
                return True
        elif name == "status_changed":
            if item.get("to") == "finalizing":
                return True
    return False


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
    报文模板（八种 check，模板逐字锁定）；其余违规任务以
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
    elif check == "corrupt_state":
        lines = [
            "Completion blocked: unreadable task state (task %s)." % task_id,
            "- %s" % detail["reason"],
            "The task directory was kept. Recovery options:",
            "- rebuild state.json from events.jsonl / checkpoint evidence"
            " (repository state is authoritative), then re-run Stop;",
            "- or archive the task directory (rename or remove) once the"
            " user confirms it is obsolete.",
        ]
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


def _route_requires_review(task_state):
    """route 推导的审查义务：mode ∈ (audit, full) 或 assurance == "high"
    → True（H2：手写 state.json 无法靠漏写 review.required 跳过审查检查；
    推导与 runtime.state.derive_review_required 同源）。

    route 缺失 / 形状异常 → False（与既有参与判定同口径：仅按显式声明
    参与，不因形状异常扩大拦截面；推导不确定（None）按 False 处理）。
    """
    from runtime import state
    route = task_state.get("route") if isinstance(task_state, dict) else None
    return state.derive_review_required(route) is True


def task_participates(task_state):
    """§15 参与判定：ownership / verification / review / visual 任一声明
    非空即参与校验（review 条件含 route 推导——audit/full 或
    assurance:high 任务即使漏写 review.required 也参与）；全部不成立 →
    跳过（不参与 Layer A 也不参与新检查）。"""
    if _nonempty_strs(_section(task_state, "ownership").get("files")):
        return True
    if _nonempty_strs(_section(task_state, "verification").get("required")):
        return True
    if _section(task_state, "review").get("required") is True \
            or _route_requires_review(task_state):
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

    参数 repo / touched / base 的语义（RB-2 起）为「该任务自己的仓库
    根」及其改动清单与基线修订号：调用方按任务的 state.repository.root
    绑定解析求值根，touched / base 由 per-repo 缓存取好（单次 Stop 内
    每个不同仓库根恒为 2 次 git 子调用：1 次 status + 1 次 rev-parse，
    同根多任务复用不叠加），原样透传给 fingerprint.task_fingerprint
    （与主会话记录证据同一入口，属指纹层自身契约；主会话侧用缺省调用
    自取，两者语义等价）。

    review 检查条件：review.required 为 True 或 route 推导要求审查
    （_route_requires_review：audit/full 或 assurance:high）——不依赖
    「模型记得把 review.required 写对」。verdict 词汇处理与指纹比对
    分支不变。

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

    # 3) review：required 为 True 或 route 推导要求审查（audit/full 或
    #    assurance:high——手写 state 漏写 review.required 也逃不过检查）
    review = _section(task_state, "review")
    if review.get("required") is True or _route_requires_review(task_state):
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


def collect_violations(state, journal, ownership, fingerprint, ledger,
                       active, repo_cache, scan_cache):
    """逐任务做 §15 四重检查（RB-2 per-task 仓库求值）。

    返回 (violations, declared)：
      - violations：违规列表 [(task_id, check, detail)]（每任务首失败即
        记，§15 顺序）；
      - declared：本轮完成门**实际求值过**的任务 ID 列表——gate_passed
        的记账对象；仓库解析 / git / 求值失败被降级的任务不在其中
        （它们已各自记 gate_degraded，不得再领 gate_passed）。

    逐任务流程（参与判定 → 仓库根解析 → per-repo 快照 → 四重检查）：
      - state.json 在发现后消失（load_state → None）或四项声明全空
        → 跳过（与此前一致，且不触发任何 git 调用）；
      - 仓库根解析：绑定任务用 state.bound_repository_root 的绑定根；
        legacy 任务回退账本根——账本根非 git 根（无 .git 条目）时按
        一级子目录 .git 扫描结果降级：无候选 → reason=
        repository_root_missing；有候选 → reason=repository_ambiguous
        （stderr 列出候选目录名；不自动猜、不自动绑定）；
      - git 快照失败（_repo_snapshot 抛 OwnershipError /
        FingerprintError）：绑定根 → reason=repository_unavailable；
        legacy 且账本根是 git 根 → reason=git_unavailable（瞬时 git
        故障）；
      - evaluate 阶段结构性错误（classify / task_fingerprint / visual
        的 OwnershipError / FingerprintError）→ 该任务 reason=
        evaluation_error（原全局路径改按任务隔离）。
      降级只影响该任务：stderr 报警 + 该任务 journal gate_degraded
      （账本根），其余任务照常求值——单任务仓库故障的爆炸半径为单
      task。不修改 repo_cache / scan_cache 的所有权（引用透传，缓存
      归调用方 main）。
    """
    violations = []
    declared = []
    for task_id in active:
        task_state = state.load_state(ledger, task_id)
        if task_state is None:
            continue
        if not task_participates(task_state):
            continue
        bound = state.bound_repository_root(task_state)
        if bound is not None:
            root = bound
        else:
            root = ledger
            if not os.path.exists(os.path.join(root, ".git")):
                # legacy 且账本根非 git 根：不猜仓库，结构化降级
                candidates = _nested_repo_candidates(ledger, scan_cache)
                if candidates:
                    warn_stderr(
                        "ENFORCEMENT DEGRADED: ledger %s is not a git "
                        "repository root; candidate repositories: %s "
                        "(reason=repository_ambiguous). Bind the task via "
                        "state.json repository.root instead of guessing "
                        "(task %s)" % (root, ", ".join(candidates), task_id))
                    reason = "repository_ambiguous"
                else:
                    warn_stderr(
                        "ENFORCEMENT DEGRADED: ledger %s is not a git "
                        "repository and no candidate repository found in "
                        "its first-level directories "
                        "(reason=repository_root_missing); bind the task "
                        "via state.json repository.root (task %s)"
                        % (root, task_id))
                    reason = "repository_root_missing"
                journal.append_event(
                    ledger, task_id,
                    {"event": "gate_degraded", "reason": reason})
                continue
        try:
            snapshot = _repo_snapshot(repo_cache, root)
        except (ownership.OwnershipError,
                fingerprint.FingerprintError) as exc:
            if bound is not None:
                warn_stderr(
                    "ENFORCEMENT DEGRADED: bound repository %s unavailable"
                    " for task %s (reason=repository_unavailable): %s; gate"
                    " skipped for this task" % (root, task_id, exc))
                reason = "repository_unavailable"
            else:
                warn_stderr(
                    "ENFORCEMENT DEGRADED: git operations failed on ledger"
                    " %s for task %s (reason=git_unavailable): %s; gate"
                    " skipped for this task" % (root, task_id, exc))
                reason = "git_unavailable"
            journal.append_event(
                ledger, task_id,
                {"event": "gate_degraded", "reason": reason})
            continue
        try:
            result = evaluate_task(task_id, task_state, root,
                                   snapshot["touched"], snapshot["base"])
        except (ownership.OwnershipError,
                fingerprint.FingerprintError) as exc:
            warn_stderr(
                "ENFORCEMENT DEGRADED: cannot evaluate completion gate for"
                " task %s (reason=evaluation_error): %s; gate skipped for"
                " this task" % (task_id, exc))
            journal.append_event(
                ledger, task_id,
                {"event": "gate_degraded", "reason": "evaluation_error"})
            continue
        declared.append(task_id)
        if result is not None:
            check, detail = result
            violations.append((task_id, check, detail))
    return violations, declared


def record_for_tasks(journal, repo, task_ids, event):
    """向多个任务 journal 记同一事件（gate_passed / gate_degraded 用）。"""
    for task_id in task_ids:
        journal.append_event(repo, task_id, dict(event))


def commit_finalizing_completions(state, fingerprint, journal, ledger,
                                  active, repo_cache):
    """完成提交：把全部 status == finalizing 的活动任务原子提交 completed。

    仅在四重检查全绿的放行路径调用（block / exhausted / degraded 一律
    不到这里）——completed 的唯一提交点。遍历**全部**活动任务而非仅参与
    校验集合：finalizing 本身就是完成请求，无声明的 finalizing 任务同样
    要在放行时被提交。逐任务流程：按该任务生效仓库根取 per-repo 快照
    （RB-2：绑定 repository.root 优先，legacy 回退账本根；与求值阶段
    共用 repo_cache——同根不重复 git）→ 重算当前证据指纹（与门同一
    入口）→ state.commit_completion（内部经 _gate_commit 通道落盘
    completed）→ journal 追加 completed 事件（via=completion_gate，绑定
    指纹；journal 恒在账本根）。单任务失败（任何 Exception）warn_stderr
    报警后继续下一任务：状态保持 finalizing，绝不因记账异常崩掉放行
    路径（fail-open）——仓库不可用 / 求值降级任务的指纹取不到，自然
    保持 finalizing（H1：completed 只能来自门内全绿提交）。
    """
    for task_id in active:
        try:
            task_state = state.load_state(ledger, task_id)
            if task_state is None or task_state.get("status") != "finalizing":
                continue
            root = state.resolve_repository_root(task_state, ledger)
            snapshot = _repo_snapshot(repo_cache, root)
            fp = fingerprint.task_fingerprint(
                root, task_state,
                touched=snapshot["touched"], base=snapshot["base"])
            state.commit_completion(ledger, task_id)
            journal.append_event(
                ledger, task_id,
                {"event": "completed", "task_id": task_id,
                 "via": "completion_gate", "fingerprint": fp})
        except Exception as exc:
            warn_stderr(
                "ENFORCEMENT DEGRADED: cannot commit completion for task "
                "%s (%r); task left in finalizing" % (task_id, exc))


def main():
    """§15 四重检查主流程（架构师锁定设计；RB-2 起 per-task 仓库求值）。

    返回值恒为 0（block 是 stdout JSON 决策，不用退出码 2）；
    除 block 的单行 JSON 外不向 stdout 写任何内容。
    注意：stop_hook_active=true（续行循环中的 Stop）**照常校验**——这正是
    运行时 3 次续行额度的工作方式（第 1/2 次 block、第 3 次由 journal
    计数放行）；提前放行会让续行中的完成声明免检。
    发现段用 state.discover_tasks 四分类（H3/P0-3，见模块 docstring
    「发现完整性」）：orphaned 永不拦截、无证据 corrupt 降级放行（均
    stderr 报警 + journal gate_degraded 可见），有高保障证据的 corrupt
    在发现阶段即 stderr 报警（deferred for enforcement）后并入违规清单
    fail-closed——corrupt 证据只依赖 journal，不依赖任何 git 根，恒进
    统一 block 机器；无 corrupt/orphaned 目录时流程与四分类引入前完全
    一致。
    求值段（RB-2）：账本根与 git 根分离——每个参与任务按其绑定的
    repository.root 求值（legacy 回退账本根），per-repo 缓存使每个不同
    仓库根至多 2 次 git 子调用；仓库解析 / git / 求值失败只降级该任务
    （结构化 reason，见模块 docstring「fail-open 策略」），不再有全局
    git 早退。全绿放行路径额外做完成提交：对全部 finalizing 任务按其
    各自仓库根取指纹后原子提交 completed 并记 completed 事件（见模块
    docstring「完成提交」）。
    """
    # 1) 读 stdin（容错；载荷当前不参与分支决策，保留解析以备扩展）
    read_stop_event()

    # runtime 模块在函数内 import：其上的 sys.path 接线已就绪，
    # import 失败会被外层 fail-open 捕获
    from runtime import fingerprint, journal, ownership, state

    # 2) 账本根（RB-2 改名：语义 = 任务账本根，不必然是 git 根；
    #    state / journal I/O 恒在此根，git 求值根按任务绑定解析）
    ledger = ledger_root()

    # 3) 任务发现四分类（H3/P0-3）：state.json 损坏不再静默消失——
    #    orphaned（无 state.json）永不拦截：报警 + 该目录 journal 记
    #    gate_degraded(reason=orphaned_task)；corrupt 有高保障 journal
    #    证据者收集为 deferred_corrupt（稍后并入违规清单 fail-closed），
    #    无证据者降级放行：报警 + journal 记 gate_degraded(
    #    reason=corrupt_state)。active 为空且无 deferred_corrupt →
    #    静默放行（普通会话零干预语义不变）
    discovery = state.discover_tasks(ledger)
    active = [name for name, _status in discovery["active"]]
    for name, _reason in discovery["orphaned"]:
        warn_stderr(
            "ENFORCEMENT DEGRADED: task directory %s has no state.json "
            "(orphaned); completion gate cannot enforce it" % name)
        journal.append_event(
            ledger, name,
            {"event": "gate_degraded", "reason": "orphaned_task"})
    deferred_corrupt = []
    for name, reason in discovery["corrupt"]:
        if _corrupt_requires_fail_closed(ledger, name):
            deferred_corrupt.append((name, reason))
            # 发现阶段即报警：高保障 corrupt 随后必然进统一 block 机器
            # （RB-2 起无全局降级早退，不会被任何任务的仓库故障牵连）
            warn_stderr(
                "ENFORCEMENT: unreadable high-assurance task state "
                "deferred for enforcement (task %s): %s" % (name, reason))
        else:
            warn_stderr(
                "ENFORCEMENT DEGRADED: task %s state.json unreadable and "
                "no high-assurance journal evidence (%s); gate degraded "
                "for this task" % (name, reason))
            journal.append_event(
                ledger, name,
                {"event": "gate_degraded", "reason": "corrupt_state"})
    if not active and not deferred_corrupt:
        return 0

    # 4) 逐参与任务求值（RB-2 per-task 仓库求值）：每个参与任务按其
    #    绑定仓库根（legacy 回退账本根）做四重检查；per-repo 缓存
    #    （repo_cache / scan_cache 由本函数持有，与完成提交阶段共用）
    #    保证每个不同仓库根至多 1 次 status + 1 次 rev-parse。仓库解析 /
    #    git / 求值失败只降级该任务（结构化 reason + stderr + journal），
    #    其余任务照常——原「全局 touched/base + git 失败全局早退」拆除
    repo_cache = {}
    scan_cache = {}
    violations, declared = collect_violations(
        state, journal, ownership, fingerprint, ledger, active,
        repo_cache, scan_cache)

    # 5) corrupt 高保障任务并入违规清单尾部（四重检查违规优先呈现；
    #      check=corrupt_state 与其他违规同走下方 pass / block / exhausted
    #      统一机器——含续行上限）
    violations.extend(
        (name, "corrupt_state", {"reason": reason})
        for name, reason in deferred_corrupt)

    # 6) 无违规 → 放行：stdout 完全静默；对本轮实际求值过的任务记
    #    gate_passed（断链用——否则上一轮的 gate_blocked 残留会让下轮
    #    续行计数起点错位；同时留"何时通过完成门"的审计痕迹。被降级
    #    任务不在 declared 内（已各记 gate_degraded）；无人参与时不记
    #    ——没有校验发生。无任务可查时已在第 3 步提前返回，普通会话
    #    零写入）。随后做完成提交：对全部 finalizing 任务按其各自仓库
    #    根取指纹后原子提交 completed（P0-1：completed 的唯一提交点在
    #    本门内；单任务失败逐任务降级放行，exhausted / degraded / block
    #    路径不经过这里）
    if not violations:
        record_for_tasks(
            journal, ledger, declared,
            {"event": "gate_passed", "tasks": declared})
        commit_finalizing_completions(
            state, fingerprint, journal, ledger, active, repo_cache)
        return 0

    # 7) 有违规：block 报文 / journal 记账以第一个违规任务为准
    first_task, first_check, first_detail = violations[0]

    # 7a) 续行上限：journal 尾部连续 gate_blocked 已达 GATE_BLOCK_LIMIT
    #     → 放行 + stderr 报警 + 记 gate_exhausted（第 3 次 Stop 不再 block）
    if count_trailing_gate_blocks(ledger, first_task) >= GATE_BLOCK_LIMIT:
        warn_stderr(
            "ENFORCEMENT GATE EXHAUSTED: task %s blocked twice already; "
            "runtime 3-attempt limit reached, allowing stop. The model MUST "
            "report the blocked state to the user and MUST NOT claim "
            "completion." % first_task)
        journal.append_event(
            ledger, first_task,
            dict({"event": "gate_exhausted", "check": first_check},
                 **first_detail))
        return 0

    # 7b) 未达上限 → block：stdout 单行 JSON 请求续跑 + 记 gate_blocked
    emit_block_json(build_block_reason(violations[0], violations[1:]))
    journal.append_event(
        ledger, first_task,
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
