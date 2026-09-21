#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GLM Conductor v2.4 SessionStart 恢复发现层（recovery，Phase 2 P2-E 重写）。

职责（V2_4_PHASE_2_WORKFLOW_EXECUTION_PLAN.md W5）：
    把恢复面从 v2.1-v2.3 的「逐单元证据对账全景」收敛为 v2.4 的任务级
    最小恢复摘要：每个未完成任务只显示——task id、goal、repository、
    status、route、workflow run id（如有）、quota 等待态（如有）、
    下一步安全动作。v2.3 遗留任务渲染为一行：检测到 v2.3 任务 + 处置
    指引（state.LEGACY_STATE_GUIDANCE 逐字）。
    两个对外函数（签名与 v2.3 一致，钩子调用形状不变）：
      - build_recovery_summary(repo_root)：聚合恢复摘要 dict（冻结形状，
        见函数 docstring）；
      - render_resume_context(summary)：把摘要渲染为 resume context
        文本；无内容时返回 ""（钩子据此完全静默，零噪音）。

下一安全动作词汇（恰七个，NEXT_ACTIONS 冻结，测试锚定）：
    inspect workflow / resume workflow / reassess repository /
    run main validation / run required review / wait for quota or user /
    explicitly retire legacy task。
    映射见 next_safe_action()（纯函数，优先级表在其 docstring）。

v2.4 重写删除面（本模块不再存在这些概念，测试 test_recovery_v24 锚定
「无单元级字段出现」与导入面零残留）：
    - 单元就绪（completed/incomplete/interrupted 单元三分）；
    - 僵尸分类（run 生命周期 possibly_zombie 标注）；
    - agent-run 对账信号展示；
    - 租约对账 / verification receipt 对账；
    - 恢复清单依赖（本模块绝不 import 该已删模块）；
    - v2.2 M9 桥接对账（wake bridge stale / fired-unresolved 判定与
      四行 continuity 展示块——Phase 3 连续性面收口时统一处置）。

§8.2 纯本地红线（沿袭）：
    本模块不调模型、不联网、不执行 implementation、不消耗 quota、
    无 git subprocess——只用 runtime.state 的本地文件读取面
    （state.json 发现 / 读取 / 遗留检测 / 仓库根解析），钩子
    timeoutMs 5000 内可完成。

依赖：
    仅 runtime.state（W1 v2.4 状态层：discover_tasks / load_state /
    is_legacy_state / LEGACY_STATE_GUIDANCE / resolve_repository_root），
    零第三方依赖，`python3 -S` 可运行，Python 3.7 兼容。
"""

from runtime import state

# 摘要 goal 字段的截断长度（冻结：120 字符，控制注入文本体积）
GOAL_TRUNCATE = 120

# 额度等待任务状态词汇（v2.4 七态中的两态）：处于这两个状态的任务在
# 摘要条目携带条件键 "quota_wait"，渲染层据此追加一行 Quota wait；
# 其他状态不增补
QUOTA_WAIT_TASK_STATUSES = ("waiting_quota", "waiting_user")

# —— 下一安全动作词汇（恰七个，逐字冻结，测试锚定） ——

NEXT_ACTION_INSPECT_WORKFLOW = "inspect workflow"
NEXT_ACTION_RESUME_WORKFLOW = "resume workflow"
NEXT_ACTION_REASSESS_REPOSITORY = "reassess repository"
NEXT_ACTION_RUN_MAIN_VALIDATION = "run main validation"
NEXT_ACTION_RUN_REQUIRED_REVIEW = "run required review"
NEXT_ACTION_WAIT_QUOTA_OR_USER = "wait for quota or user"
NEXT_ACTION_RETIRE_LEGACY = "explicitly retire legacy task"

NEXT_ACTIONS = (
    NEXT_ACTION_INSPECT_WORKFLOW,
    NEXT_ACTION_RESUME_WORKFLOW,
    NEXT_ACTION_REASSESS_REPOSITORY,
    NEXT_ACTION_RUN_MAIN_VALIDATION,
    NEXT_ACTION_RUN_REQUIRED_REVIEW,
    NEXT_ACTION_WAIT_QUOTA_OR_USER,
    NEXT_ACTION_RETIRE_LEGACY,
)

# —— render 层有界性常量（additionalContext 有 32KB stdout cap） ——

# 模板头部（逐字冻结，测试锚定）
RESUME_HEADER = "GLM CONDUCTOR RESUME CONTEXT"

# v2.4 任务段落 / legacy 一行式各自最多渲染的条目数（超出截断 +
# omitted 计数行）
MAX_RENDERED_TASKS = 3

# corrupt 警告行里最多列出的目录名数（超出以 +N more 截断，保持单行）
MAX_CORRUPT_IDS_SHOWN = 5


# —— 摘要条目构造（纯本地只读，容错读不抛） ——

def _workflow_run_id(loaded):
    """读取任务的委派 run id：非空 str 原样返回，其余（null / 缺失 /
    形状异常）→ None（恢复层不虚构 run 事实）。"""
    run_id = loaded.get("workflow_run_id")
    return run_id if isinstance(run_id, str) and run_id else None


def _route_view(loaded):
    """把任务 route 块容错投影为 {"mode", "assurance"} 两键视图。

    mode / assurance 均为非空 str 时原样保留，缺失 / 形状异常 → None
    （形状纠错归 state.validate_state，恢复层照实呈现不猜测）。
    """
    route = loaded.get("route")
    route = route if isinstance(route, dict) else {}
    mode = route.get("mode")
    mode = mode if isinstance(mode, str) and mode else None
    assurance = route.get("assurance")
    assurance = assurance if isinstance(assurance, str) and assurance \
        else None
    return {"mode": mode, "assurance": assurance}


def _quota_resume_view(loaded, status):
    """组装条件键 "quota_wait"（仅额度等待态任务携带）。

    v2.4 的额度事实只有 status ∈ {waiting_quota, waiting_user} 与
    quota_resume 占位块（Phase 3 定稿语义）——容错投影为四键：
      {"status": 等待态状态值,
       "mode": quota_resume.mode（非空 str，缺失 / 异常 → None）,
       "resume_count": 已用恢复次数（>=0 int，缺失 / 异常 → 0）,
       "max_resumes": 恢复上限（>=0 int，缺失 / 异常 → 0）}
    """
    block = loaded.get("quota_resume")
    block = block if isinstance(block, dict) else {}
    mode = block.get("mode")
    mode = mode if isinstance(mode, str) and mode else None
    counts = {}
    for key in ("resume_count", "max_resumes"):
        value = block.get(key)
        if isinstance(value, bool) or not isinstance(value, int) \
                or value < 0:
            value = 0
        counts[key] = value
    return {
        "status": status,
        "mode": mode,
        "resume_count": counts["resume_count"],
        "max_resumes": counts["max_resumes"],
    }


def next_safe_action(loaded):
    """按 v2.4 任务状态 dict 判定下一步安全动作（纯函数，NEXT_ACTIONS 词汇）。

    v2.4 恢复层不做子运行时对账（无单元 / run / lease / receipt
    事实），动作判定只用任务级记录（validation / review / route /
    phase / workflow_run_id / status），优先级自上而下首中即返：

      1. v2.3 遗留 state（is_legacy_state 命中）→ explicitly retire
         legacy task（处置指引由渲染层携带 LEGACY_STATE_GUIDANCE）；
      2. status ∈ waiting_quota / waiting_user → wait for quota or
         user（等额度 / 等授权，不越权代判恢复时机）；
      3. status == blocked → reassess repository（受阻任务先对仓库
         现状重估计划 / 路由）；
      4. validation.status == "passed" 且 route.assurance == "high"
         且 review.verdict != "ship" → run required review（完成守卫
         对 high 保障级强制独立审查；先验证后评审是生命周期前置）；
      5. validation 未通过（未记录 / failed）且 phase ==
         "validating" → run main validation（phase 标注任务已进入
         验证阶段——工作流已收口，主验证是下一步）；
      6. validation.status == "passed"（审查义务已了 / 不适用）→
         委派 run 状态对恢复层不可知（v2.4 无子状态镜像）：有 run id
         → inspect workflow（收尾前先核对 run 实况）；无 run id →
         reassess repository；
      7. 其余（validation 未记录 / 失败，验证阶段未标注）→ 有 run id
         且 phase == "workflow" → resume workflow（任务标注处于执行
         阶段：会话中断后恢复执行）；有 run id（无 phase 标注）→
         inspect workflow（先核对再续作）；无 run id → reassess
         repository（尚无委派 run，先对仓库现状重估计划）。

    loaded 非 dict（不可能形状）→ reassess repository（保守兜底）。
    """
    if not isinstance(loaded, dict):
        return NEXT_ACTION_REASSESS_REPOSITORY
    if state.is_legacy_state(loaded):
        return NEXT_ACTION_RETIRE_LEGACY
    status = loaded.get("status")
    if status in QUOTA_WAIT_TASK_STATUSES:
        return NEXT_ACTION_WAIT_QUOTA_OR_USER
    if status == "blocked":
        return NEXT_ACTION_REASSESS_REPOSITORY

    validation = loaded.get("validation")
    validation_status = (validation.get("status")
                         if isinstance(validation, dict) else None)
    review = loaded.get("review")
    verdict = review.get("verdict") if isinstance(review, dict) else None
    route = loaded.get("route")
    route = route if isinstance(route, dict) else {}
    assurance = route.get("assurance")
    phase = loaded.get("phase")
    has_run = _workflow_run_id(loaded) is not None
    validation_passed = validation_status == "passed"

    if validation_passed and assurance == "high" and verdict != "ship":
        return NEXT_ACTION_RUN_REQUIRED_REVIEW
    if not validation_passed and phase == "validating":
        return NEXT_ACTION_RUN_MAIN_VALIDATION
    if validation_passed:
        return NEXT_ACTION_INSPECT_WORKFLOW if has_run \
            else NEXT_ACTION_REASSESS_REPOSITORY
    if has_run:
        if phase == "workflow":
            return NEXT_ACTION_RESUME_WORKFLOW
        return NEXT_ACTION_INSPECT_WORKFLOW
    return NEXT_ACTION_REASSESS_REPOSITORY


def _summarize_task(repo_root, dir_task_id, loaded):
    """把单个 v2.4 活动任务的状态 dict 聚合为摘要条目（冻结键形状）。

    键恰为：task_id / goal / repository / status / route /
    workflow_run_id / next_action 七键 + 条件第八键 quota_wait（仅
    status ∈ QUOTA_WAIT_TASK_STATUSES 时携带）。无任何单元级字段
    （completed_units / interrupted_units / auto_resume / continuity
    等 v2.3 概念一律不再出现——test_recovery_v24 锚定）。

    task_id：load_state 已归一（raw["task_id"]），缺失 / 异常回退目录
    名；goal 截断 GOAL_TRUNCATE；repository 经
    resolve_repository_root（绑定优先，缺省回退账本根，恒为 str）。
    """
    task_id = loaded.get("task_id")
    if not isinstance(task_id, str) or task_id == "":
        task_id = dir_task_id
    goal = loaded.get("goal")
    if not isinstance(goal, str):
        goal = ""
    status = loaded.get("status")
    entry = {
        "task_id": task_id,
        "goal": goal[:GOAL_TRUNCATE],
        "repository": state.resolve_repository_root(loaded, repo_root),
        "status": status,
        "route": _route_view(loaded),
        "workflow_run_id": _workflow_run_id(loaded),
        "next_action": next_safe_action(loaded),
    }
    if status in QUOTA_WAIT_TASK_STATUSES:
        entry["quota_wait"] = _quota_resume_view(loaded, status)
    return entry


def _summarize_legacy_task(dir_task_id, loaded):
    """把单个 v2.3 遗留任务聚合为一行式摘要条目（冻结三键）。

    键恰为：task_id / guidance（state.LEGACY_STATE_GUIDANCE 逐字）/
    next_action（恒 explicitly retire legacy task）。遗留任务不展开
    任何字段——v2.4 不解读 v2.3 账面，处置指引原样透传。
    """
    task_id = loaded.get("task_id")
    if not isinstance(task_id, str) or task_id == "":
        task_id = dir_task_id
    return {
        "task_id": task_id,
        "guidance": state.LEGACY_STATE_GUIDANCE,
        "next_action": NEXT_ACTION_RETIRE_LEGACY,
    }


def build_recovery_summary(repo_root):
    """扫描账本根并聚合 SessionStart 恢复摘要（纯本地只读，零写副作用）。

    返回冻结形状（三键恒存在）：
      {"tasks":  [ v2.4 形态未完成任务条目（_summarize_task 冻结键）...
                   按 task_id 排序，多任务全列（render 层负责截断） ],
       "legacy": [ v2.3 遗留任务一行式条目（_summarize_legacy_task
                   三键）...按 task_id 排序 ],
       "corrupt": [state.json 损坏 / 不可读的任务目录名，排序]}

    v2.3 遗留任务落 discover_tasks 的 active 桶（status 不在 v2.4
    七态 / 携带 work_units 等遗留标记均非终态），此处经
    is_legacy_state 分流入 legacy 列表——遗留与 v2.4 形态在摘要里
    永不混桶。

    无任何任务与损坏文件（tasks_root 不存在 / 全为终态）→
    {"tasks": [], "legacy": [], "corrupt": []}（render 据此返回 ""，
    钩子完全静默）。单任务在扫描与读取之间损坏 / 被移除（load_state
    抛 ValueError / OSError）→ 跳过该任务，不中断整体发现（宁可少报
    也不炸恢复路径）；corrupt 桶由 discover_tasks 显式报告，不在本
    函数二次补救。
    """
    discovery = state.discover_tasks(repo_root)
    tasks = []
    legacy = []
    for dir_task_id, _status in discovery["active"]:
        try:
            loaded = state.load_state(repo_root, dir_task_id)
        except (ValueError, OSError):
            # 状态文件在扫描与读取之间损坏 / 被移除 → 跳过该任务
            continue
        if not isinstance(loaded, dict):
            continue
        if state.is_legacy_state(loaded):
            legacy.append(_summarize_legacy_task(dir_task_id, loaded))
        else:
            tasks.append(_summarize_task(repo_root, dir_task_id, loaded))
    tasks.sort(key=lambda entry: entry["task_id"])
    legacy.sort(key=lambda entry: entry["task_id"])
    return {
        "tasks": tasks,
        "legacy": legacy,
        "corrupt": sorted(name for name, _reason in discovery["corrupt"]),
    }


# —— resume context 渲染 ——

def _render_task(entry):
    """渲染单个 v2.4 任务的段落（W5 冻结形态，每任务恰八行）。

    段序冻结：空行 / Task / Repository / Goal / Status / Route /
    Workflow run / [Quota wait（仅 quota_wait 键携带任务）] /
    Next action。Next action 值恒为 NEXT_ACTIONS 词汇逐字（测试
    锚定）；Workflow run 无 run id 时渲染 "none"。
    """
    lines = ["", "Task: %s" % entry.get("task_id")]
    repo = entry.get("repository")
    lines.append("Repository: %s"
                 % (repo if isinstance(repo, str) and repo else "not bound"))
    goal = entry.get("goal")
    lines.append("Goal: %s"
                 % (goal if isinstance(goal, str) and goal else "(no goal)"))
    status = entry.get("status")
    lines.append("Status: %s"
                 % (status if isinstance(status, str) and status
                    else "unknown"))
    route = entry.get("route")
    route = route if isinstance(route, dict) else {}
    mode = route.get("mode")
    mode_text = mode if isinstance(mode, str) and mode else "unknown"
    assurance = route.get("assurance")
    if isinstance(assurance, str) and assurance:
        mode_text += " (assurance: %s)" % assurance
    lines.append("Route: %s" % mode_text)
    run_id = entry.get("workflow_run_id")
    lines.append("Workflow run: %s"
                 % (run_id if isinstance(run_id, str) and run_id else "none"))

    # 额度等待态增补（仅 quota_wait 键携带任务）：一行 Quota wait，
    # 值为等待态状态 + quota_resume 占位块容错投影
    quota_wait = entry.get("quota_wait")
    if isinstance(quota_wait, dict):
        wait_status = quota_wait.get("status")
        wait_status = wait_status if isinstance(wait_status, str) \
            and wait_status else "waiting"
        wait_mode = quota_wait.get("mode")
        wait_mode = wait_mode if isinstance(wait_mode, str) \
            and wait_mode else "manual"
        resume_count = quota_wait.get("resume_count")
        if isinstance(resume_count, bool) or not isinstance(resume_count, int):
            resume_count = 0
        max_resumes = quota_wait.get("max_resumes")
        if isinstance(max_resumes, bool) or not isinstance(max_resumes, int):
            max_resumes = 0
        lines.append(
            "Quota wait: %s (resume mode: %s; resumes used %d/%d)"
            % (wait_status, wait_mode, resume_count, max_resumes))

    lines.append("Next action: %s" % entry.get("next_action"))
    return lines


def _render_legacy_task(entry):
    """渲染单个 v2.3 遗留任务为一行（W5 冻结形态）。

    一行 = 检测到 v2.3 任务（task id）+ 处置指引（guidance 逐字）+
    下一安全动作（explicitly retire legacy task）。绝不为遗留任务
    展开 Goal / Status 等字段行（test_recovery_v24 锚定单行形态）。
    """
    task_id = entry.get("task_id")
    task_id = task_id if isinstance(task_id, str) and task_id else "unknown"
    guidance = entry.get("guidance")
    if not isinstance(guidance, str) or guidance == "":
        guidance = state.LEGACY_STATE_GUIDANCE
    return ["Legacy task %s: v2.3 task detected — %s; next action: %s"
            % (task_id, guidance, entry.get("next_action"))]


def render_resume_context(summary):
    """把恢复摘要渲染为注入文本；无任何内容时返回 ""（钩子完全静默）。

    形态（W5 冻结）：
      - 头部逐字 RESUME_HEADER；
      - v2.4 任务段落（_render_task 八行形态），超过 MAX_RENDERED_TASKS
        只列前 3 + "(N more task(s) omitted)" 行；
      - v2.3 遗留任务一行式（_render_legacy_task），超过
        MAX_RENDERED_TASKS 只列前 3 + "(N more legacy task(s)
        omitted)" 行；
      - corrupt 非空 → 末尾单行 WARNING（目录名超 5 个以 +N more
        截断）。仅 corrupt 有内容时同样输出（损坏 state.json 不得
        在恢复面静默消失——discover_tasks 显式报告契约的消费端）；
      - 不再有 v2.1-v2.3 的单元清单 / 僵尸标注 / Recommended 编号
        尾巴 / 四行 continuity 块。
    summary 非 dict / 三列表全空 → ""。
    """
    if not isinstance(summary, dict):
        return ""

    def _str_dict_list(value):
        if not isinstance(value, list):
            return []
        return [item for item in value if isinstance(item, dict)]

    tasks = _str_dict_list(summary.get("tasks"))
    legacy = _str_dict_list(summary.get("legacy"))
    corrupt = summary.get("corrupt")
    corrupt = [name for name in corrupt if isinstance(name, str) and name] \
        if isinstance(corrupt, list) else []
    if not tasks and not legacy and not corrupt:
        return ""

    lines = [RESUME_HEADER]
    shown_tasks = tasks[:MAX_RENDERED_TASKS]
    for entry in shown_tasks:
        lines.extend(_render_task(entry))
    omitted = len(tasks) - len(shown_tasks)
    if omitted > 0:
        lines.append("(%d more task(s) omitted)" % omitted)

    shown_legacy = legacy[:MAX_RENDERED_TASKS]
    for entry in shown_legacy:
        lines.extend(_render_legacy_task(entry))
    omitted_legacy = len(legacy) - len(shown_legacy)
    if omitted_legacy > 0:
        lines.append("(%d more legacy task(s) omitted)" % omitted_legacy)

    if corrupt:
        shown_ids = corrupt[:MAX_CORRUPT_IDS_SHOWN]
        suffix = ""
        if len(corrupt) > len(shown_ids):
            suffix = ", +%d more" % (len(corrupt) - len(shown_ids))
        lines.append(
            "WARNING: %d corrupt task state file(s) detected (%s%s); "
            "inspect .glm-conductor/tasks/ before relying on recovery."
            % (len(corrupt), ", ".join(shown_ids), suffix))

    return "\n".join(lines)
