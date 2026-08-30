#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GLM Conductor v2.1 M3 SessionStart 恢复发现层（recovery，wu-21-05）。

职责（计划 §8，连续性缺口 R2）：
    把「主会话崩溃 / 换会话后恢复靠模型记忆手动完成」变成机械发现：
    新会话（SessionStart，matcher "startup|clear|compact"）自动知道——
      - 存在什么未完成任务（discover_tasks 四分类的 active 桶）；
      - 哪些 work unit 未完成（含被中断的 running/waiting_quota/blocked
        单元）；
      - 哪些 agent run 需要 reconcile（agent_run.run_lifecycle 僵尸
        感知——possibly_zombie 标注，§7.3：metadata running 不是 truth）；
      - 下一步建议（Recommended 1-4，§8.3 模板尾部）。
    两个对外函数：
      - build_recovery_summary(repo_root)：聚合恢复摘要 dict（冻结形状，
        见函数 docstring）；
      - render_resume_context(summary)：把摘要渲染为英文 resume context
        文本（§8.3 形态）；无内容时返回 ""（钩子据此完全静默，零噪音）。

§8.2 纯本地红线：
    本模块不调模型、不联网、不执行 implementation、不消耗 quota、
    无 git subprocess——只用 runtime.state / runtime.agent_run 的本地
    文件读取面（state.json / events.jsonl / 原生档案只读 adapter），
    钩子 timeoutMs 5000 内可完成。git 操作不做：恢复建议里提示由
    模型后续执行（inspect repository residue）。

边界（与其他单元的分工）：
    - reconcile 四分决策（reuse_result / resume_with_progress /
      redispatch_clean / manual_ruling）归 wu-21-06（runtime/reconcile.py），
      本模块只呈现「需要 reconcile 的信号」（interrupted + 僵尸标注）；
    - resume context 的自动 checkpoint 压缩归 wu-21-07（resume_manifest）；
    - corrupt 任务的 fail-closed 处置归后续单元 / 模型——本模块只在
      summary["corrupt"] 列出 + render 末尾单行警告（不展开）。

依赖：
    runtime.state（discover_tasks / load_state / resolve_repository_root）
    + runtime.agent_run（run_lifecycle）。仅 Python 3 标准库，
    `python3 -S` 可运行（无 site-packages）。
"""

from runtime import agent_run, state

# 摘要 goal 字段的截断长度（冻结：120 字符，控制注入文本体积）
GOAL_TRUNCATE = 120

# 「被中断」单元状态词汇（冻结：仅这三个状态算 interrupted——
# running 是被中断的主形态；waiting_quota / blocked 是任务账本里
# 显式挂起的形态。verifying / pending / failed 等只进 incomplete）
INTERRUPTED_UNIT_STATUSES = ("running", "waiting_quota", "blocked")

# legacy 任务的 auto_resume 兜底值（无 execution_policy 块即 legacy，
# 按保守默认 manual 解释，与 default_execution_policy 一致）
LEGACY_AUTO_RESUME = "manual"

# —— render 层有界性常量（additionalContext 有 32KB stdout cap） ——

# 模板头部（逐字冻结，测试断言）
RESUME_HEADER = "GLM CONDUCTOR RESUME CONTEXT"

# 最多逐段列出的任务数（超出只列前 3 + omitted 行）
MAX_RENDERED_TASKS = 3

# 每个单元列表（completed / interrupted / waiting）最多列出的条目数
# （超出截断 + omitted 计数）
MAX_RENDERED_UNITS = 8

# 僵尸标注（逐字冻结）：run_lifecycle.possibly_zombie=True 时该单元
# 追加此行——§7.3 metadata running 不是 truth，真相裁决归 reconcile
ZOMBIE_NOTE = "agent run possibly zombie (metadata running is not truth)"

# corrupt 警告行里最多列出的 task_id 数（超出以 +N more 截断，保持单行）
MAX_CORRUPT_IDS_SHOWN = 5


# —— 摘要构建（纯本地只读） ——

def _summarize_task(repo_root, task_id, loaded):
    """把单个活动任务的状态 dict 聚合为摘要条目（冻结八字段）。

    单元三分（互不排斥：interrupted 是 incomplete 的子集）：
      - completed_units：status == "completed" 的单元 id；
      - incomplete_units：其余全部非 completed 单元 id（含 failed /
        blocked / waiting_quota / running / verifying / pending /
        cancelled 等——恢复视角下「未 completed 即未收口」）；
      - interrupted_units：status ∈ INTERRUPTED_UNIT_STATUSES 的单元，
        每项 {"id", "run_lifecycle"}；run_lifecycle 为
        agent_run.run_lifecycle 结果，该单元无任何 agent run 记录时
        置 None（空账本不值得展开）。

    auto_resume：execution_policy.continuity.auto_resume（授权事实源）；
    块缺失 / 形状异常 / 值非法（非非空 str）→ legacy 兜底 "manual"。
    repo_root：resolve_repository_root(loaded, None)——绑定优先，
    无绑定（legacy 形态）为 None。
    """
    work_units = loaded.get("work_units")
    units = work_units if isinstance(work_units, list) else []
    completed_units = []
    incomplete_units = []
    interrupted_units = []
    for unit in units:
        if not isinstance(unit, dict):
            continue
        uid = unit.get("id")
        if not isinstance(uid, str) or uid == "":
            continue
        unit_status = unit.get("status")
        if unit_status == "completed":
            completed_units.append(uid)
        else:
            incomplete_units.append(uid)
        if unit_status in INTERRUPTED_UNIT_STATUSES:
            lifecycle = agent_run.run_lifecycle(repo_root, task_id, uid)
            interrupted_units.append({
                "id": uid,
                "run_lifecycle": lifecycle if lifecycle["runs"] else None,
            })

    policy = loaded.get("execution_policy")
    continuity = policy.get("continuity") \
        if isinstance(policy, dict) else None
    auto_resume = continuity.get("auto_resume") \
        if isinstance(continuity, dict) else None
    if not isinstance(auto_resume, str) or auto_resume == "":
        auto_resume = LEGACY_AUTO_RESUME

    goal = loaded.get("goal")
    if not isinstance(goal, str):
        goal = ""

    task_id_normalized = loaded.get("task_id")
    if not isinstance(task_id_normalized, str) or task_id_normalized == "":
        task_id_normalized = task_id

    return {
        "task_id": task_id_normalized,
        "goal": goal[:GOAL_TRUNCATE],
        "status": loaded.get("status"),
        "repo_root": state.resolve_repository_root(loaded, None),
        "completed_units": completed_units,
        "incomplete_units": incomplete_units,
        "interrupted_units": interrupted_units,
        "auto_resume": auto_resume,
    }


def build_recovery_summary(repo_root):
    """扫描账本根并聚合 SessionStart 恢复摘要（纯本地只读，零写副作用）。

    返回冻结形状：
      {"tasks": [ 摘要条目（_summarize_task 八字段）...按 task_id 排序，
                  多任务全列（render 层负责截断） ],
       "corrupt": [task_id...]（discover corrupt 桶目录名，按名排序）}

    无任何任务（tasks_root 不存在 / 全为终态）→ {"tasks": [], "corrupt": []}
    （render 据此返回 ""，钩子完全静默）。单任务在扫描与读取之间损坏 /
    被移除（load_state 抛 ValueError / OSError）→ 跳过该任务，不中断
    整体发现（宁可少报也不炸恢复路径）；corrupt 桶由 discover_tasks
    显式报告，不在本函数二次补救。
    """
    discovery = state.discover_tasks(repo_root)
    tasks = []
    for dir_task_id, _status in discovery["active"]:
        try:
            loaded = state.load_state(repo_root, dir_task_id)
        except (ValueError, OSError):
            # 状态文件在扫描与读取之间损坏 / 被移除 → 跳过该任务
            continue
        if not isinstance(loaded, dict):
            continue
        tasks.append(_summarize_task(repo_root, dir_task_id, loaded))
    tasks.sort(key=lambda entry: entry["task_id"])
    return {
        "tasks": tasks,
        "corrupt": sorted(name for name, _reason in discovery["corrupt"]),
    }


# —— resume context 渲染（英文模板，§8.3 形态） ——

def _render_unit_list(label, unit_ids):
    """渲染一个单元列表段（"Label:" + 缩进条目），超 MAX_RENDERED_UNITS
    截断并追加 omitted 计数行；空列表渲染 "none"。"""
    unit_ids = [uid for uid in unit_ids if isinstance(uid, str) and uid] \
        if isinstance(unit_ids, list) else []
    lines = ["%s:" % label]
    if not unit_ids:
        lines.append("  none")
        return lines
    shown = unit_ids[:MAX_RENDERED_UNITS]
    for uid in shown:
        lines.append("  %s" % uid)
    omitted = len(unit_ids) - len(shown)
    if omitted > 0:
        lines.append("  (%d more %s omitted)"
                     % (omitted, label.lower()))
    return lines


def _render_task(entry):
    """渲染单个任务的段落（§8.3 逐段形态，英文）。

    段序冻结：Task / Repository / Goal / Status / Completed units /
    Interrupted units / Waiting / Quota-resume authorization。
    Waiting = incomplete_units 去掉 interrupted 的 id（挂起等依赖 /
    待派发的单元）；interrupted 单元逐个列出，possibly_zombie=True 时
    追加逐字僵尸标注行。
    """
    lines = ["", "Task: %s" % entry.get("task_id")]
    repo = entry.get("repo_root")
    lines.append("Repository: %s"
                 % (repo if isinstance(repo, str) and repo else "not bound"))
    goal = entry.get("goal")
    lines.append("Goal: %s"
                 % (goal if isinstance(goal, str) and goal else "(no goal)"))
    lines.append("Status: %s" % entry.get("status"))
    lines.extend(_render_unit_list("Completed units",
                                   entry.get("completed_units")))

    interrupted = entry.get("interrupted_units")
    interrupted = interrupted if isinstance(interrupted, list) else []
    interrupted = [item for item in interrupted if isinstance(item, dict)]
    lines.append("Interrupted units:")
    if not interrupted:
        lines.append("  none")
    shown_interrupted = interrupted[:MAX_RENDERED_UNITS]
    for item in shown_interrupted:
        lines.append("  %s" % item.get("id"))
        lifecycle = item.get("run_lifecycle")
        if isinstance(lifecycle, dict) and lifecycle.get("possibly_zombie"):
            lines.append("    %s" % ZOMBIE_NOTE)
    omitted = len(interrupted) - len(shown_interrupted)
    if omitted > 0:
        lines.append("  (%d more interrupted units omitted)" % omitted)

    interrupted_ids = {item.get("id") for item in interrupted}
    waiting = [uid for uid in entry.get("incomplete_units") or []
               if uid not in interrupted_ids]
    lines.extend(_render_unit_list("Waiting", waiting))
    lines.append("Quota-resume authorization: %s" % entry.get("auto_resume"))
    return lines


def render_resume_context(summary):
    """把恢复摘要渲染为注入文本（英文，§8.3 模板形态）；无任务返回 ""。

    有界性（additionalContext 32KB stdout cap）：
      - 任务超过 MAX_RENDERED_TASKS(3) → 只列前 3 + "(N more task(s)
        omitted)" 行；
      - 每个单元列表超过 MAX_RENDERED_UNITS(8) → 截断 + omitted 计数；
      - corrupt 警告恒为单行（id 列表超 5 个以 +N more 截断）。
    尾部 Recommended 编号 1-4 逐字冻结（reconcile interrupted agent
    runs → inspect repository residue → retrieve prior progress if
    needed → continue via prepare_dispatch）。summary 无任务（含空
    仓库 / 全终态）→ 返回 ""（钩子据此完全静默，零噪音）。
    """
    tasks = summary.get("tasks") if isinstance(summary, dict) else None
    tasks = [task for task in tasks if isinstance(task, dict)] \
        if isinstance(tasks, list) else []
    if not tasks:
        return ""

    lines = [RESUME_HEADER]
    shown_tasks = tasks[:MAX_RENDERED_TASKS]
    for entry in shown_tasks:
        lines.extend(_render_task(entry))
    omitted_tasks = len(tasks) - len(shown_tasks)
    if omitted_tasks > 0:
        lines.append("(%d more task(s) omitted)" % omitted_tasks)

    lines.append("")
    lines.append("Recommended:")
    lines.append("  1. reconcile interrupted agent runs")
    lines.append("  2. inspect repository residue")
    lines.append("  3. retrieve prior progress if needed")
    lines.append("  4. continue via prepare_dispatch")

    corrupt = summary.get("corrupt") if isinstance(summary, dict) else None
    corrupt = [name for name in corrupt if isinstance(name, str) and name] \
        if isinstance(corrupt, list) else []
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
