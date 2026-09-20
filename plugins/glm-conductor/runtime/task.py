#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GLM Conductor v2.4 任务生命周期层（task lifecycle，Phase 2 工作包 P2-A/C，单元 W3）。

职责：
    v2.4 任务生命周期的唯一编排入口：在 W1 的 v2.4 状态层
    （runtime.state）与 W2 的任务级变更标识（runtime.change_id）之上，
    提供小而全的任务/工作流生命周期 API——创建 / 读取、委派 run 关联、
    任务级验证 / 审查记录、状态与 phase 推进、额度等待进出、终态收尾
    （journal 事件 + 写者守卫释放）。本模块是 P2-F 里 task_manager 的
    替代者，提前落地供 W4（完成守卫）与 W6（退役收口）消费。

    全部记录都是任务级最小记录（P2-C）：validation / review 各一条，
    新鲜度锚定 change_id。绝不镜像宿主子代理生命周期，绝不写
    receipt，不要求 stdout 捕获或 runner 身份，无逐单元证据面。

接口：
    create_task(repo_root, task_id, goal, route, dag_nodes=())
        校验后持久化 v2.4 state（route 摘取 mode 与 assurance 两键，
        含 assurance 词汇 standard/high 时一并存入 route 块）；任务已
        存在即拒绝（绝不覆盖既有账本）；写者守卫关联发生在 Workflow
        启动时，不在本函数（本函数不 acquire）。
    load_task(repo_root, task_id)
        读取任务状态 → dict | None；v2.3 遗留任务经遗留检测透传为
        ValueError（带 LEGACY_STATE_GUIDANCE 处置指引），绝不静默当作
        v2.4 任务返回。
    record_workflow_run(repo_root, task_id, workflow_run_id)
        记录委派 run 关联：state.workflow_run_id 落盘 + 镜像到
        runtime.workflow.adapter.record_run（run-id 关联单据）+
        workflow_started 事件。
    record_validation(repo_root, task_id, status, summary=None, commands=None)
        任务级验证记录（status ∈ passed/failed）；change_id 经
        runtime.change_id.compute_change_id 现算现存（相关路径集 =
        dag 全节点 ownership scope 并集，见 relevant_paths()）。
    record_review(repo_root, task_id, reviewer, verdict, findings=None)
        任务级审查记录（verdict ∈ ship/fix-first/rethink）；要求先有
        validation 记录（status 与 change_id 齐备），否则拒绝（先验证
        后评审）；change_id 同上现算。
    set_status(repo_root, task_id, new_status)
        状态迁移（严格按 state.TASK_TRANSITIONS 矩阵；终态不得静默
        重开）。经 state.save_state 的转换门落盘，不追加 journal 事件
        （任务级词汇无通用状态名；enter_waiting_quota / complete /
        fail / cancel 各自带专属事件）。
    set_phase(repo_root, task_id, phase)
        描述性 phase 标注（∈ planning/workflow/validating/reviewing；
        None = 清除标注——phase 无 null 空档）。
    enter_waiting_quota(repo_root, task_id) / exit_waiting_quota(...)
        额度等待进出：迁移到 waiting_quota / active，各落一条
        waiting_quota 事件（direction: enter / exit）；无实际迁移
        （原状态即目标）时不落事件。
    complete(repo_root, task_id) / fail(...) / cancel(...)
        终态收尾：迁移到 completed / failed / cancelled + 任务级
        journal 事件（task_completed / task_failed / task_cancelled）
        + 经 runtime.writer_guard.release 释放仓库写者守卫（幂等；
        守卫属其它任务时不误删）。同终态重放放行（事件与释放幂等），
        跨终态迁移一律拒绝。

journal 词汇（TASK_JOURNAL_EVENTS，恰九名，绝不多不少）：
    route_selected / workflow_started / workflow_reassessed /
    validation_recorded / review_recorded / waiting_quota /
    task_completed / task_failed / task_cancelled。
    workflow_reassessed 是声明的词汇名（路由重估由主会话记录），
    本模块 API 面无生产者。本模块追加的事件名绝不出此表；也绝不
    经由 state.transition_task_status 迁移状态（其 status_changed
    事件不在任务级词汇内）——状态门用 state.TASK_TRANSITIONS +
    state.save_state 自己走。

变更标识的相关路径集（W4 完成守卫共用的同一派生）：
    relevant_paths(task_state) = dag 全节点 ownership scope 并集
    （去重排序；归一与记账剔除由 change_id.compute_change_id 内部
    负责）。record_validation / record_review 与 W4 的新鲜度比对必须
    同调此派生，change_id 才可比。

依赖：
    仅 Python 3 标准库 + runtime.state（W1）/ runtime.change_id（W2）/
    runtime.journal / runtime.writer_guard（P1-E）/
    runtime.workflow.adapter（P1-D，只写关联单据，绝不启动任何
    Workflow）；零第三方依赖，3.7 兼容（注解引号形式）。
    结构性拒绝一律 ValueError（与 runtime.state 的错误口径一致）；
    change_id 求值的结构性错误（非 git 仓库等）以 change_id.
    ChangeIdError 原样上抛。

来源：
    docs/roadmap/V2_4_PHASE_2_WORKFLOW_EXECUTION_PLAN.md W3（单元规格）
    + docs/roadmap/V2_4_PHASE_2_STATE_ASSURANCE_RETIREMENT_SPEC.md
    §3（单一 change_id 新鲜度）与 §4（最小验证 / 审查记录）。
"""

from runtime import change_id, journal, state, writer_guard
from runtime.workflow import adapter as workflow_adapter

# —— 任务级 journal 事件词汇（恰九名；本模块追加的事件名绝不出此表） ——

# 词汇精确口径（W3 规格）：route_selected / workflow_started /
# workflow_reassessed / validation_recorded / review_recorded /
# waiting_quota / task_completed / task_failed / task_cancelled。
# 无宿主子代理生命周期词汇（implementation_started / unit_finished /
# dispatch_* / lease_* / reviewer_invoked 等一律不入表）。
TASK_JOURNAL_EVENTS = (
    "route_selected",
    "workflow_started",
    "workflow_reassessed",
    "validation_recorded",
    "review_recorded",
    "waiting_quota",
    "task_completed",
    "task_failed",
    "task_cancelled",
)


# —— 内部助手 ——

def _load_v24_state(repo_root, task_id) -> dict:
    """载入可操作的 v2.4 任务状态；不可操作（缺失 / v2.3 遗留）→ ValueError。

    与 load_task 的差别：任务缺失不返回 None 而是拒绝——全部变更入口
    的目标都必须是已存在的 v2.4 任务；JSON 损坏由 state.load_state 抛
    ValueError 自然上传播（绝不覆盖损坏文件）。
    """
    st = state.load_state(repo_root, task_id)
    if st is None:
        raise ValueError(
            "任务 %s 不存在（无 state.json），无法执行生命周期操作" % task_id)
    if state.is_legacy_state(st):
        raise ValueError(
            "任务 %s 是 v2.3 遗留任务，v2.4 生命周期层拒绝操作：%s"
            % (task_id, state.LEGACY_STATE_GUIDANCE))
    return st


def _ensure_not_terminal(st, op) -> None:
    """终态冻结：status 已是终态时拒绝一切记录 / 标注改写（op 为入口名）。

    终态任务的 validation / review / workflow_run_id / phase 一律冻结
    （终态不得再有新事实落账）；重开只能走 state.reopen_task（显式
    通道，落 task_reopened 事件）。
    """
    status = st.get("status")
    if status in state.TERMINAL_STATUSES:
        raise ValueError(
            "%s：任务 %s 已是终态 %r，账本冻结（重开请走 state.reopen_task）"
            % (op, st.get("task_id"), status))


def _ensure_transition_allowed(st, new_status, op) -> None:
    """按 state.TASK_TRANSITIONS 校验一次 status 迁移；非法 → ValueError。

    与 state._transition_errors 同一矩阵、同一规则（旧 == 新放行；终态
    无表项 = 任何变化拒绝），但落盘走 state.save_state（其转换门会再
    校验一次），不触发 transition_task_status 的 status_changed 事件。
    """
    old_status = st.get("status")
    if old_status == new_status:
        return
    allowed = state.TASK_TRANSITIONS.get(old_status, ())
    if new_status not in allowed:
        targets = ", ".join(allowed) if allowed \
            else "无（终态不接受任何转换；显式重开请走 state.reopen_task）"
        raise ValueError(
            "%s：status 转换 %r → %r 不在合法转换内（%s 的合法目标：%s；"
            "完整转换表见 state.TASK_TRANSITIONS）"
            % (op, old_status, new_status, old_status, targets))


# —— 相关路径集（W4 完成守卫共用的同一派生） ——

def relevant_paths(task_state) -> "list[str]":
    """派生任务相关路径集：dag 全节点 ownership scope 并集（去重排序）。

    这是 record_validation / record_review 存 change_id 与 W4 完成守卫
    新鲜度比对共用的唯一派生（两侧同调此函数，change_id 才可比）：
      - 遍历 task_state["dag"]（缺键 / 非数组 / 条目非 dict 一律跳过
        ——形状纠错归 state.validate_state，本函数是容错派生）；
      - 收集每节点 ownership 内全部非空字符串 scope；
      - 去重并按 str 排序返回。反斜杠写法归一 / 记账目录剔除 / 非法
        路径拒绝由 change_id.compute_change_id 内部负责（ChangeIdError
        原样上抛）。
    空 dag → 空列表（compute_change_id 对空相关集同样给出稳定标识）。
    """
    paths = set()
    if not isinstance(task_state, dict):
        return []
    dag = task_state.get("dag")
    if not isinstance(dag, list):
        return []
    for node in dag:
        if not isinstance(node, dict):
            continue
        scopes = node.get("ownership")
        if not isinstance(scopes, (list, tuple)):
            continue
        for scope in scopes:
            if isinstance(scope, str) and scope != "":
                paths.add(scope)
    return sorted(paths)


def _compute_change_id(st, repo_root) -> str:
    """按任务绑定仓库根（RB-2）现算 change_id（相关路径集见 relevant_paths）。

    绑定优先、账本根回退（state.resolve_repository_root），与 W2 的
    求值口径一致；非 git 仓库等结构性错误以 ChangeIdError 原样上抛。
    """
    return change_id.compute_change_id(
        state.resolve_repository_root(st, repo_root), relevant_paths(st))


# —— 创建 / 读取 ——

def create_task(repo_root, task_id, goal, route, dag_nodes=()) -> dict:
    """校验并持久化一个新 v2.4 任务，返回构造出的完整状态 dict。

    - 持久化前显式 validate_state（聚合中文错误，任一错误即拒绝且零
      落盘）；state.save_state 的再校验与转换门（首存放行）二次把关；
    - route 摘取口径与 state.new_task_state 一致：只取 mode 与
      assurance 两键写入 route 块（assurance ∈ standard/high 时一并
      存入；词汇外值由 validate_state 拒绝）；route 非 dict 抛
      TypeError（new_task_state 口径）；
    - dag_nodes：静态节点 dict 列表（runtime.work_unit.new_node 产物），
      逐节点 + 整图校验（错误带 dag[i] 路径）；空数组合法；
    - 任务已存在（盘上有 state.json，含损坏文件——load_state 抛
      ValueError）即拒绝：绝不静默覆盖既有任务账本；
    - 仓库根绑定（RB-2）：repository.root 记为 repo_root 的归一绝对
      路径；写者守卫关联发生在 Workflow 启动时（record_workflow_run /
      宿主侧 acquire），本函数绝不 acquire；
    - 成功后落 route_selected 事件（含 mode / assurance）。
    """
    if state.load_state(repo_root, task_id) is not None:
        raise ValueError(
            "create_task：任务 %s 已存在（盘上已有 state.json），拒绝重建"
            "（绝不覆盖既有账本）" % task_id)
    st = state.new_task_state(
        task_id, goal, route, repository_root=repo_root,
        dag_nodes=dag_nodes)
    errors = state.validate_state(st)
    if errors:
        raise ValueError("create_task：任务状态非法，拒绝持久化：%s"
                         % "；".join(errors))
    state.save_state(repo_root, st)
    journal.append_event(
        repo_root, task_id,
        {"event": "route_selected",
         "mode": st["route"].get("mode"),
         "assurance": st["route"].get("assurance")})
    return st


def load_task(repo_root, task_id) -> "dict | None":
    """读取任务状态 → dict | None（文件不存在 → None）。

    v2.3 遗留检测透传：载入结果带 v2.3 标记（work_units /
    permit/lease/receipt / 阶段态 status 任一）时抛 ValueError，消息
    原样携带 state.LEGACY_STATE_GUIDANCE 处置指引——绝不静默把 v2.3
    遗留任务当作 v2.4 任务返回（消费方需要原始遗留视图请走
    state.detect_legacy_task）。JSON 损坏由 state.load_state 抛
    ValueError 自然上抛（不静默）。
    """
    st = state.load_state(repo_root, task_id)
    if st is None:
        return None
    if state.is_legacy_state(st):
        raise ValueError(
            "任务 %s 是 v2.3 遗留任务：%s" % (task_id,
                                             state.LEGACY_STATE_GUIDANCE))
    return st


# —— 委派 run 关联 ——

def record_workflow_run(repo_root, task_id, workflow_run_id) -> dict:
    """记录委派 Workflow run 关联，返回更新后的状态 dict。

    三件事（顺序固定）：
      1. state.workflow_run_id 落盘（非空 str 强制；终态任务冻结）；
      2. 镜像到 runtime.workflow.adapter.record_run（run-id 关联单据，
         同任务重复调用整条替换——「一任务一活跃 run」口径）；
      3. 落 workflow_started 事件（含 workflow_run_id）。
    workflow_run_id 结构非法 → ValueError（adapter 侧 WorkflowRunError
    口径与本模块 ValueError 口径在此对齐，先校验后写盘零副作用）。
    """
    if not isinstance(workflow_run_id, str) or workflow_run_id == "":
        raise ValueError(
            "record_workflow_run：workflow_run_id 必须是非空字符串，得到 %r"
            % (workflow_run_id,))
    st = _load_v24_state(repo_root, task_id)
    _ensure_not_terminal(st, "record_workflow_run")
    st["workflow_run_id"] = workflow_run_id
    state.save_state(repo_root, st)
    workflow_adapter.record_run(task_id, workflow_run_id)
    journal.append_event(
        repo_root, task_id,
        {"event": "workflow_started", "workflow_run_id": workflow_run_id})
    return st


# —— 任务级最小验证 / 审查记录（P2-C） ——

def record_validation(repo_root, task_id, status, summary=None,
                      commands=None) -> dict:
    """记录任务级验证结果（passed / failed），返回更新后的状态 dict。

    - status ∈ state.VALIDATION_STATUSES（passed / failed），词汇外
      拒绝；终态任务冻结（_ensure_not_terminal）；
    - change_id 经 compute_change_id 现算现存（相关路径集 =
      relevant_paths(st)：dag ownership 并集）——W4 完成守卫的新鲜度
      比对同调此派生与同一实现，任何任务相关仓库变化都会使既有记录
      过时；
    - summary：可选非空 str 或 None；commands：可选非空字符串数组
      （允许空数组）或 None——纯人读诊断摘要，不要求 stdout 捕获、
      runner 身份或任何逐命令证据（P2-C 红线）；
    - 成功后落 validation_recorded 事件（含 status 与 change_id）。
    """
    if status not in state.VALIDATION_STATUSES:
        raise ValueError(
            "record_validation：status %r 不在合法取值内（%s）"
            % (status, ", ".join(state.VALIDATION_STATUSES)))
    if summary is not None and (not isinstance(summary, str)
                                or summary == ""):
        raise ValueError(
            "record_validation：summary 必须是非空字符串或 None，得到 %r"
            % (summary,))
    if commands is not None:
        if not isinstance(commands, (list, tuple)):
            raise ValueError(
                "record_validation：commands 必须是字符串数组或 None，"
                "得到 %s" % type(commands).__name__)
        for item in commands:
            if not isinstance(item, str) or item == "":
                raise ValueError(
                    "record_validation：commands 的各项必须是非空字符串")
    st = _load_v24_state(repo_root, task_id)
    _ensure_not_terminal(st, "record_validation")
    change_id_value = _compute_change_id(st, repo_root)
    st["validation"] = {
        "status": status,
        "change_id": change_id_value,
        "summary": summary,
        "commands": list(commands) if commands is not None else None,
    }
    state.save_state(repo_root, st)
    journal.append_event(
        repo_root, task_id,
        {"event": "validation_recorded", "status": status,
         "change_id": change_id_value})
    return st


def record_review(repo_root, task_id, reviewer, verdict, findings=None) -> dict:
    """记录任务级审查裁决（ship / fix-first / rethink），返回更新后的状态 dict。

    - reviewer：非空 str；verdict ∈ state.REVIEW_VERDICTS；findings：
      可选非空 str 或 None；终态任务冻结；
    - 顺序规则（规格红线）：要求先有 validation 记录——validation.
      status 非 null 且 validation.change_id 为非空 str（完整记录，
      只经 record_validation 产生），否则拒绝（先验证后评审；
      fix-first / rethink 修复后主验证必须先刷新）；
    - change_id 现算现存（与 record_validation 同一派生与实现）；
    - 成功后落 review_recorded 事件（含 reviewer / verdict /
      change_id）。
    """
    if not isinstance(reviewer, str) or reviewer == "":
        raise ValueError(
            "record_review：reviewer 必须是非空字符串，得到 %r" % (reviewer,))
    if verdict not in state.REVIEW_VERDICTS:
        raise ValueError(
            "record_review：verdict %r 不在合法取值内（%s）"
            % (verdict, ", ".join(state.REVIEW_VERDICTS)))
    if findings is not None and (not isinstance(findings, str)
                                 or findings == ""):
        raise ValueError(
            "record_review：findings 必须是非空字符串或 None，得到 %r"
            % (findings,))
    st = _load_v24_state(repo_root, task_id)
    _ensure_not_terminal(st, "record_review")
    validation = st.get("validation")
    if not isinstance(validation, dict) \
            or validation.get("status") is None \
            or not isinstance(validation.get("change_id"), str) \
            or validation.get("change_id") == "":
        raise ValueError(
            "record_review：任务 %s 尚无 validation 记录，拒绝记录 review"
            "（先验证后评审；请先 record_validation）" % task_id)
    change_id_value = _compute_change_id(st, repo_root)
    st["review"] = {
        "reviewer": reviewer,
        "verdict": verdict,
        "change_id": change_id_value,
        "findings": findings,
    }
    state.save_state(repo_root, st)
    journal.append_event(
        repo_root, task_id,
        {"event": "review_recorded", "reviewer": reviewer,
         "verdict": verdict, "change_id": change_id_value})
    return st


# —— 状态 / phase 推进 ——

def set_status(repo_root, task_id, new_status) -> dict:
    """把任务状态迁移到 new_status（严格按 TASK_TRANSITIONS），返回迁移后的状态。

    - new_status ∈ state.TASK_STATUSES（七态词汇），词汇外拒绝；
    - 迁移门与 state.save_state 的转换门同一矩阵（旧 == 新放行；终态
      无表项——终态不得静默重开，显式重开走 state.reopen_task）；
    - 不追加 journal 事件：任务级词汇无通用状态名（enter_waiting_quota
      与 complete / fail / cancel 各自带专属事件）。
    """
    if new_status not in state.TASK_STATUSES:
        raise ValueError(
            "set_status：status %r 不在合法取值内（%s）"
            % (new_status, ", ".join(state.TASK_STATUSES)))
    st = _load_v24_state(repo_root, task_id)
    _ensure_transition_allowed(st, new_status, "set_status")
    st["status"] = new_status
    state.save_state(repo_root, st)
    return st


def set_phase(repo_root, task_id, phase) -> dict:
    """设置（或清除）任务的描述性 phase 标注，返回更新后的状态 dict。

    - phase ∈ state.TASK_PHASES（planning / workflow / validating /
      reviewing）或 None；None = 清除标注（phase 无 null 空档——缺省
      即不写键）；词汇外拒绝；终态任务冻结；
    - 纯描述性标注（无转换矩阵），不追加 journal 事件。
    """
    if phase is not None and phase not in state.TASK_PHASES:
        raise ValueError(
            "set_phase：phase %r 不在合法取值内（%s）"
            % (phase, ", ".join(state.TASK_PHASES)))
    st = _load_v24_state(repo_root, task_id)
    _ensure_not_terminal(st, "set_phase")
    if phase is None:
        st.pop("phase", None)
    else:
        st["phase"] = phase
    state.save_state(repo_root, st)
    return st


# —— 额度等待进出 ——

def enter_waiting_quota(repo_root, task_id) -> dict:
    """迁移到 waiting_quota 并落 waiting_quota 事件（direction: enter）。

    原状态已是 waiting_quota（无实际迁移）时只返回现状、不重复落事件；
    迁移合法性归 TASK_TRANSITIONS（终态与词汇外来源自然被拒）。
    """
    st = _load_v24_state(repo_root, task_id)
    old_status = st.get("status")
    st = set_status(repo_root, task_id, "waiting_quota")
    if old_status != "waiting_quota":
        journal.append_event(
            repo_root, task_id,
            {"event": "waiting_quota", "direction": "enter"})
    return st


def exit_waiting_quota(repo_root, task_id) -> dict:
    """从 waiting_quota 迁回 active 并落 waiting_quota 事件（direction: exit）。

    原状态已是 active（无实际迁移）时只返回现状、不重复落事件；
    迁移合法性归 TASK_TRANSITIONS（如 waiting_user → waiting_quota 这类
    矩阵外迁移自然被拒）。
    """
    st = _load_v24_state(repo_root, task_id)
    old_status = st.get("status")
    st = set_status(repo_root, task_id, "active")
    if old_status != "active":
        journal.append_event(
            repo_root, task_id,
            {"event": "waiting_quota", "direction": "exit"})
    return st


# —— 终态收尾 ——

def _finish(repo_root, task_id, terminal_status, event_name) -> dict:
    """终态收尾公共通道：终态迁移 + 任务级 journal 事件 + 写者守卫释放。

    - 迁移门同 set_status（旧 == 目标终态 → 同终态重放放行：不重复
      落盘，但事件与守卫释放幂等重做——hook 重入安全；跨终态迁移
      一律拒绝）；
    - 事件名限于 TASK_JOURNAL_EVENTS（task_completed / task_failed /
      task_cancelled），含 from 字段记录收尾前状态；
    - 经 runtime.writer_guard.release 释放仓库写者守卫（幂等；守卫
      属其它任务时 release 拒绝且不误删——终态事实不因此回滚，守卫
      归属对账由持有者侧或 U6 CLI --force 负责）。
    """
    st = _load_v24_state(repo_root, task_id)
    old_status = st.get("status")
    if old_status != terminal_status:
        _ensure_transition_allowed(st, terminal_status, event_name)
        st["status"] = terminal_status
        state.save_state(repo_root, st)
    journal.append_event(
        repo_root, task_id,
        {"event": event_name, "from": old_status, "to": terminal_status})
    writer_guard.release(repo_root, task_id)
    return st


def complete(repo_root, task_id) -> dict:
    """完成任务（→ completed + task_completed 事件 + 释放写者守卫）。"""
    return _finish(repo_root, task_id, "completed", "task_completed")


def fail(repo_root, task_id) -> dict:
    """失败收尾（→ failed + task_failed 事件 + 释放写者守卫）。"""
    return _finish(repo_root, task_id, "failed", "task_failed")


def cancel(repo_root, task_id) -> dict:
    """取消任务（→ cancelled + task_cancelled 事件 + 释放写者守卫）。"""
    return _finish(repo_root, task_id, "cancelled", "task_cancelled")
