#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GLM Conductor v2.4 任务生命周期层（task lifecycle，Phase 2 W3 + Phase 3 单元 Q1）。

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
        记录委派 run 关联：delegate/full 模式前置要求当前任务持有本
        仓库写者守卫（预约缺失 / 属其它任务即 ValueError——委派注册
        必须先持有本仓库写预约，AF-04）；既有落盘（state.workflow_run_id
        + 镜像 runtime.workflow.adapter.record_run 的 run-id 关联单据 +
        workflow_started 事件）成功后把 run id 幂等补挂进守卫记录
        （同任务 acquire 重入更新语义；补挂冲突宁可拒绝注册）。
        solo/audit 不持有写 Workflow，无此前置。
    record_validation(repo_root, task_id, status, summary=None, commands=None)
        任务级验证记录（status ∈ passed/failed）；change_id 经
        runtime.change_id.compute_change_id 现算现存（相关改动文件集 =
        ownership scope 并集 ∩ git 工作区实际改动，见
        relevant_changed_files()）。
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
    enter_waiting_quota(repo_root, task_id, quota_view=None) /
    exit_waiting_quota(...)
        额度等待进出：迁移到 waiting_quota / active，各落一条
        waiting_quota 事件（direction: enter / exit）；无实际迁移
        （原状态即目标）时不落事件、不写盘。enter 可选携带 quota_view
        （归一化配额观测 dict）——实际进入等待时把最近一次观测
        （status/reset_at/observed_at）记入 quota_resume 供诊断。
    authorize_quota_resume(repo_root, task_id, max_resumes)
        显式用户授权自动恢复（P3-B）：置 quota_resume.mode=auto 并落
        预算 max_resumes；要求任务处于 waiting_quota 或 active；
        max_resumes 不得小于已用 resume_count。授权由本调用的落盘
        记录，不做任何推断。
    scheduled_activation_decision(repo_root, task_id, quota_view)
        定时轮次的幂等决策原语（P3-C）：四action 封闭词汇
        （no-op / remain-waiting / waiting-user / resume-authorized），
        绝不虚构可用性；resume-authorized 只暂存计数，绝不落账。
    confirm_resume_started(repo_root, task_id)
        暂存计数落账：宿主 resume 调用真正被接受后由调用方调用——
        resume_count +1、任务转回 active（workflow 阶段）、落
        quota_resume_confirmed 事件。未确认的决策绝不消耗预算。
    complete(repo_root, task_id) / fail(...) / cancel(...)
        终态收尾：迁移到 completed / failed / cancelled + 任务级
        journal 事件（task_completed / task_failed / task_cancelled）
        + 经 runtime.writer_guard.release 释放仓库写者守卫（幂等；
        守卫属其它任务时不误删）。同终态重放放行（事件与释放幂等），
        跨终态迁移一律拒绝。

journal 词汇（TASK_JOURNAL_EVENTS，恰十名，绝不多不少）：
    route_selected / workflow_started / workflow_reassessed /
    validation_recorded / review_recorded / waiting_quota /
    quota_resume_confirmed / task_completed / task_failed /
    task_cancelled。
    workflow_reassessed 是声明的词汇名（路由重估由主会话记录），
    本模块 API 面无生产者。quota_resume_confirmed 是 P3-C 唯一新增
    名（暂存恢复计数落账 + 转回 active 的事实记录）。本模块追加的
    事件名绝不出此表；也绝不经由 state.transition_task_status 迁移
    状态（其 status_changed 事件不在任务级词汇内）——状态门用
    state.TASK_TRANSITIONS + state.save_state 自己走。

变更标识的相关改动文件集（W4 完成守卫共用的同一派生）：
    ownership_scopes(task_state) = dag 全节点 ownership scope 并集
    （去重排序；DAG 声明的 glob 语言，绝不当作字面文件路径哈希）。
    relevant_changed_files(task_state, repo_root) = scope 并集拥有的
    当前 git 实际改动文件（ownership.git_touched_files +
    ownership.classify_paths，在 resolve_repository_root 生效根上
    求值）。record_validation / record_review 与 W4 的新鲜度比对必须
    同调 relevant_changed_files，change_id 才可比。owned 文件的
    增 / 删 / 改 / 改名 / 文件集变化都会改变标识；scope 外改动仍由
    ownership 完成检查拦截，与新鲜度无关；.glm-conductor/ 记账在
    touched 与 change_id 两层各自剔除。

依赖：
    仅 Python 3 标准库 + runtime.state（W1）/ runtime.change_id（W2）/
    runtime.ownership（P1-C，touched 清单与 scope 匹配）/
    runtime.journal / runtime.writer_guard（P1-E）/
    runtime.workflow.adapter（P1-D，只写关联单据，绝不启动任何
    Workflow）；零第三方依赖，3.7 兼容（注解引号形式）。
    结构性拒绝一律 ValueError（与 runtime.state 的错误口径一致）；
    change_id 求值的结构性错误（非 git 仓库等）以 change_id.
    ChangeIdError 原样上抛；touched 清单 / scope 匹配的结构性错误以
    ownership.OwnershipError 原样上抛。

来源：
    docs/roadmap/V2_4_PHASE_2_WORKFLOW_EXECUTION_PLAN.md W3（单元规格）
    + docs/roadmap/V2_4_PHASE_2_STATE_ASSURANCE_RETIREMENT_SPEC.md
    §3（单一 change_id 新鲜度）与 §4（最小验证 / 审查记录）
    + docs/roadmap/V2_4_PHASE_3_WORKFLOW_EXECUTION_PLAN.md Q1（授权
    与等待/恢复生命周期单元规格）
    + docs/roadmap/V2_4_PHASE_3_QUOTA_RESUME_EXTRACTION_SPEC.md
    §3（P3-B 最小恢复授权）与 §4（P3-C 等待/恢复生命周期）。
"""

from runtime import change_id, journal, ownership, state, writer_guard
from runtime.workflow import adapter as workflow_adapter

# —— 任务级 journal 事件词汇（恰十名；本模块追加的事件名绝不出此表） ——

# 词汇精确口径（W3 规格 + P3-C 恰一新增）：route_selected /
# workflow_started / workflow_reassessed / validation_recorded /
# review_recorded / waiting_quota / quota_resume_confirmed /
# task_completed / task_failed / task_cancelled。
# 无宿主子代理生命周期词汇（implementation_started / dispatch_* /
# lease_* / reviewer_invoked 等一律不入表）。
TASK_JOURNAL_EVENTS = (
    "route_selected",
    "workflow_started",
    "workflow_reassessed",
    "validation_recorded",
    "review_recorded",
    "waiting_quota",
    "quota_resume_confirmed",
    "task_completed",
    "task_failed",
    "task_cancelled",
)

# quota_view 归一化观测的 status 词汇（P3-A 定稿的归一化答案恰此
# 四态；与 quota.parser.QUOTA_STATUSES 同词汇，但本模块自带常量、
# 不导入 quota 包——观测核心收缩后任务层不依赖其内部模块）。
# AVAILABLE / PRESSURE 视为可用；EXHAUSTED 视为耗尽；UNKNOWN 绝不
# 虚构可用性。
QUOTA_VIEW_STATUSES = ("AVAILABLE", "PRESSURE", "EXHAUSTED", "UNKNOWN")

# scheduled_activation_decision 的 action 封闭词汇（恰四值）
SCHEDULED_ACTIVATION_ACTIONS = (
    "no-op", "remain-waiting", "waiting-user", "resume-authorized")


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


# —— 相关路径派生与相关改动文件集（W4 完成守卫共用的同一派生） ——

def ownership_scopes(task_state) -> "list[str]":
    """派生任务 ownership scope 并集：dag 全节点 ownership 非空字符串并集。

    这是 DAG 声明层（glob 语言）的 scope 汇总原语，供 W4 完成守卫查 2
    （touched ⊆ scopes?）与 relevant_changed_files（文件集解析）共用：
      - 遍历 task_state["dag"]（缺键 / 非数组 / 条目非 dict 一律跳过
        ——形状纠错归 state.validate_state，本函数是容错派生）；
      - 收集每节点 ownership 内全部非空字符串 scope；
      - 去重并按 str 排序返回。
    空 dag → 空列表。返回值是 scope 字符串（ownership 模式语言），
    绝不当作字面文件路径哈希——实际改动文件集由 relevant_changed_files
    经 ownership 模块解析。
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


def relevant_changed_files(task_state, repo_root) -> "list[str]":
    """解析任务相关改动文件集：scope 并集拥有的当前 git 实际改动文件。

    这是 record_validation / record_review 存 change_id 与 W4 完成守卫
    新鲜度比对共用的唯一文件集派生（两侧同调此函数，change_id 才可比）。
    责任分离：ownership scopes（DAG 声明）→ ownership 模块解析仓库
    实际 touched 文件 → 任务级相关改动文件集 → change_id 哈希精确
    文件 + 基线修订（scope 的 glob 语言绝不教给 change_id）：
      - scopes = ownership_scopes(task_state)（DAG 声明的并集）；
      - effective_root = state.resolve_repository_root(task_state,
        repo_root)（RB-2：绑定优先、账本根回退）；
      - touched = ownership.git_touched_files(effective_root)（当前
        git 工作区全部实际改动文件；.glm-conductor/ 记账目录已豁免）；
      - owned, _out = ownership.classify_paths(touched, scopes)
        （scope 并集过滤；scope 外改动不入集——那是 ownership 完成
        检查的失败面，不是新鲜度面）；
      - 返回 sorted(set(owned))。
    结构性错误（OwnershipError：git 失败 / 非法 scope 模式）原样上抛；
    空 scope → 空列表（无 scope 即无相关文件，compute_change_id 对空
    相关集同样给出稳定标识）。
    """
    scopes = ownership_scopes(task_state)
    if not scopes:
        return []
    effective_root = state.resolve_repository_root(task_state, repo_root)
    touched = ownership.git_touched_files(effective_root)
    owned, _out = ownership.classify_paths(touched, scopes)
    return sorted(set(owned))


def _compute_change_id(st, repo_root) -> str:
    """按任务绑定仓库根（RB-2）现算 change_id（文件集见
    relevant_changed_files）。

    绑定优先、账本根回退（state.resolve_repository_root），与 W2 的
    求值口径一致；非 git 仓库等结构性错误以 ChangeIdError 原样上抛
    （touched 清单 / scope 匹配失败则是 OwnershipError 原样上抛）。
    """
    effective_root = state.resolve_repository_root(st, repo_root)
    return change_id.compute_change_id(
        effective_root, relevant_changed_files(st, repo_root))


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

    四步（顺序固定）：
      0. 写者守卫前置（仅 delegate / full，AF-04）：在生效仓库根
         （resolve_repository_root：绑定优先、账本根回退）上
         writer_guard.inspect——预约缺失 → ValueError（delegated run
         注册必须先持有本仓库写者守卫——missing writer reservation，
         请先经宿主侧 writer-acquire 取得写预约）；预约属其它任务 →
         ValueError（报出持有者 task_id / workflow_run_id）。
         solo/audit 不持有写 Workflow，无此前置；
      1. state.workflow_run_id 落盘（非空 str 强制；终态任务冻结）；
      2. 镜像到 runtime.workflow.adapter.record_run（run-id 关联单据，
         同任务重复调用整条替换——「一任务一活跃 run」口径）；
      3. 落 workflow_started 事件（含 workflow_run_id）；
      4. 幂等补挂（仅 delegate / full）：writer_guard.acquire 以本
         run id 更新本任务既有预约（同任务重入更新语义，writer-show /
         inspect 由此可见 run id）；补挂冲突（步骤 0 与本步之间守卫
         被其它任务抢注的竞态）→ ValueError 报冲突方——宁可拒绝注册。
    workflow_run_id 结构非法 → ValueError（adapter 侧 WorkflowRunError
    口径与本模块 ValueError 口径在此对齐，先校验后写盘零副作用）。
    """
    if not isinstance(workflow_run_id, str) or workflow_run_id == "":
        raise ValueError(
            "record_workflow_run：workflow_run_id 必须是非空字符串，得到 %r"
            % (workflow_run_id,))
    st = _load_v24_state(repo_root, task_id)
    _ensure_not_terminal(st, "record_workflow_run")
    route = st.get("route")
    mode = route.get("mode") if isinstance(route, dict) else None
    effective_root = None
    if mode in ("delegate", "full"):
        # AF-04 前置：委派 run 注册必须由持有本仓库写者守卫的任务发起
        effective_root = state.resolve_repository_root(st, repo_root)
        holder = writer_guard.inspect(effective_root)
        if holder is None:
            raise ValueError(
                "record_workflow_run：委派 run 注册必须先持有本仓库写者"
                "守卫（missing writer reservation）——任务 %s 当前无写"
                "预约，请先经宿主侧 writer-acquire 取得写预约再注册"
                % task_id)
        if holder.get("task_id") != task_id:
            raise ValueError(
                "record_workflow_run：本仓库写者守卫正被其他任务持有"
                "（task_id=%r，workflow_run_id=%r），任务 %s 不得注册"
                "委派 run"
                % (holder.get("task_id"),
                   holder.get("workflow_run_id"), task_id))
    st["workflow_run_id"] = workflow_run_id
    state.save_state(repo_root, st)
    workflow_adapter.record_run(task_id, workflow_run_id)
    journal.append_event(
        repo_root, task_id,
        {"event": "workflow_started", "workflow_run_id": workflow_run_id})
    if effective_root is not None:
        # 幂等补挂：把本 run id 更新进本任务既有预约（同任务 acquire
        # 重入更新语义）；竞态冲突 → 宁可拒绝注册
        outcome = writer_guard.acquire(
            effective_root, task_id, workflow_run_id=workflow_run_id)
        if not outcome.get("ok"):
            conflict = outcome.get("conflict") or {}
            raise ValueError(
                "record_workflow_run：补挂 run id 时本仓库写者守卫已被"
                "其他任务持有（task_id=%r，workflow_run_id=%r），拒绝"
                "本次注册"
                % (conflict.get("task_id"),
                   conflict.get("workflow_run_id")))
    return st


# —— 任务级最小验证 / 审查记录（P2-C） ——

def record_validation(repo_root, task_id, status, summary=None,
                      commands=None) -> dict:
    """记录任务级验证结果（passed / failed），返回更新后的状态 dict。

    - status ∈ state.VALIDATION_STATUSES（passed / failed），词汇外
      拒绝；终态任务冻结（_ensure_not_terminal）；
    - change_id 经 compute_change_id 现算现存（相关改动文件集 =
      relevant_changed_files(st, repo_root)：ownership scope 并集 ∩
      git 工作区实际改动）——W4 完成守卫的新鲜度比对同调此派生与
      同一实现，任何 owned 文件增 / 删 / 改 / 改名 / 文件集变化都会
      使既有记录过时（scope 外改动由 ownership 检查拦截，与新鲜度
      无关；.glm-conductor/ 记账两层各自剔除）；
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


# —— 额度等待进出 / 恢复授权与定时唤醒决策（P3-B/C） ——

def _validate_quota_view(quota_view) -> dict:
    """校验归一化配额观测 dict（结构性错误 ValueError，先于一切写盘）。

    quota_view 必须是 dict 且 status ∈ QUOTA_VIEW_STATUSES（恰四态）；
    reset_at / source / observed_at 为诊断字段，本层不校验形状、按
    原样透传（归一化口径由产生方 resolver/report 负责）。返回原 dict
    （仅经形状确认，绝不改写）。
    """
    if not isinstance(quota_view, dict):
        raise ValueError(
            "quota_view 必须是归一化观测 JSON 对象（含 status/reset_at/"
            "source/observed_at），得到 %s" % type(quota_view).__name__)
    status = quota_view.get("status")
    if status not in QUOTA_VIEW_STATUSES:
        raise ValueError(
            "quota_view.status %r 不在合法取值内（%s）"
            % (status, ", ".join(QUOTA_VIEW_STATUSES)))
    return quota_view


def enter_waiting_quota(repo_root, task_id, quota_view=None) -> dict:
    """迁移到 waiting_quota 并落 waiting_quota 事件（direction: enter）。

    原状态已是 waiting_quota（无实际迁移）时只返回现状：不重复落
    事件、也不刷新观测（重入安全）；迁移合法性归
    TASK_TRANSITIONS（终态与词汇外来源自然被拒）。

    quota_view（可选）：归一化配额观测 dict（status ∈
    QUOTA_VIEW_STATUSES + reset_at/source/observed_at，宿主从
    resolver/report 获得）。实际进入等待时把最近一次观测的
    status/reset_at/observed_at 三键记入 quota_resume.last_observation
    供诊断（P3-C：诊断字段不参与授权与预算判定；source 不落盘——
    单元规格只要求三键）。quota_view 形状非法 → ValueError（零副作用）。
    """
    if quota_view is not None:
        _validate_quota_view(quota_view)
    st = _load_v24_state(repo_root, task_id)
    old_status = st.get("status")
    st = set_status(repo_root, task_id, "waiting_quota")
    if old_status != "waiting_quota":
        if quota_view is not None:
            block = st.get("quota_resume")
            if not isinstance(block, dict):
                block = state.default_quota_resume()
                st["quota_resume"] = block
            block["last_observation"] = {
                "status": quota_view.get("status"),
                "reset_at": quota_view.get("reset_at"),
                "observed_at": quota_view.get("observed_at"),
            }
            state.save_state(repo_root, st)
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


def authorize_quota_resume(repo_root, task_id, max_resumes) -> dict:
    """显式授权自动恢复（P3-B）：mode=auto + 预算，返回更新后的状态。

    - max_resumes 必须是 >= 0 的整数（bool 拒绝——bool 是 int 子类）；
    - 要求任务处于 waiting_quota 或 active（其余状态含终态一律拒绝
      ——授权不改变任务状态，只落授权与预算两键）；
    - max_resumes 不得小于已用 resume_count（落盘态必须满足预算
      不变量 resume_count <= max_resumes）；
    - quota_resume 缺失 / 非 dict（手写盘面异常）→ 按默认块重建后
      落两键（state.save_state 的全量校验兜底其余形状）；
    - 授权由本调用的显式落盘记录（mode=auto），绝不推断；auto 不
      需要附加字段；本函数不追加 journal 事件（授权事实在 state），
      也绝不重置已用计数（新 provider 窗口不自动重置预算）。
    """
    if isinstance(max_resumes, bool) or not isinstance(max_resumes, int) \
            or max_resumes < 0:
        raise ValueError(
            "authorize_quota_resume：max_resumes 必须是 >= 0 的整数，"
            "得到 %r" % (max_resumes,))
    st = _load_v24_state(repo_root, task_id)
    status = st.get("status")
    if status not in ("waiting_quota", "active"):
        raise ValueError(
            "authorize_quota_resume：任务 %s 处于 %r，授权要求任务处于"
            " waiting_quota 或 active" % (task_id, status))
    block = st.get("quota_resume")
    if not isinstance(block, dict):
        block = state.default_quota_resume()
        st["quota_resume"] = block
    if max_resumes < block.get("resume_count", 0):
        raise ValueError(
            "authorize_quota_resume：max_resumes %d 不得小于已用 "
            "resume_count %r（预算不变量：resume_count <= max_resumes）"
            % (max_resumes, block.get("resume_count", 0)))
    block["mode"] = "auto"
    block["max_resumes"] = max_resumes
    state.save_state(repo_root, st)
    return st


def scheduled_activation_decision(repo_root, task_id, quota_view) -> dict:
    """定时轮次的幂等恢复决策原语（P3-C），返回决策 dict（绝不含糊）。

    quota_view：归一化配额观测 dict（status ∈ QUOTA_VIEW_STATUSES +
    reset_at/source/observed_at；由调用方从 resolver/report 获得）。
    返回 dict 恒含 action（SCHEDULED_ACTIVATION_ACTIONS 恰四值）与
    reason（人读中文），判定顺序固定、每步幂等：

      1. 任务不处于 waiting_quota（含终态 / 已在 waiting_user）→
         {"action": "no-op"}——重复唤醒安全，绝不改写任何状态；
      2. 观测 EXHAUSTED 或 UNKNOWN → {"action": "remain-waiting"}——
         绝不虚构可用性，任务原地继续等待；
      3. mode 非 auto（manual 未授权）→ {"action": "waiting-user"}——
         等用户显式授权，不改状态；
      4. 预算耗尽（resume_count >= max_resumes）→ {"action":
         "waiting-user"}，且恰一次把任务转 waiting_user（迁移本身
         不落 journal——任务级词汇无通用状态名；幂等性由第 1 步保证：
         再次唤醒时任务已非 waiting_quota → no-op）；
      5. 可用（AVAILABLE/PRESSURE）且已授权且预算有余 → {"action":
         "resume-authorized", "workflow_run_id", "resume_count_after"}。
         resume_count_after = 现计数 + 1 只暂存在返回值里：本函数
         绝不写盘计数，未确认的决策绝不消耗预算，确认前重复决策
         恒返回同一暂存值——落账只能经 confirm_resume_started。

    结构性拒绝（ValueError，先于一切写盘）：任务缺失 / v2.3 遗留 /
    quota_view 形状非法；第 5 步前置条件 workflow_run_id 缺失（无
    关联 run 可恢复——虚构 resume-authorized 即虚构可用性）。
    绝不引入 epoch/subscription/accounting 概念。
    """
    _validate_quota_view(quota_view)
    st = _load_v24_state(repo_root, task_id)
    status = st.get("status")
    if status != "waiting_quota":
        return {
            "action": "no-op",
            "reason": "任务状态为 %r，非 waiting_quota（重复唤醒安全，"
                      "不采取任何动作）" % (status,)}
    view_status = quota_view.get("status")
    if view_status in ("EXHAUSTED", "UNKNOWN"):
        reason = ("额度观测为 %s，继续等待" % view_status
                  if view_status == "EXHAUSTED" else
                  "额度观测为 UNKNOWN，不虚构可用性，继续等待")
        return {"action": "remain-waiting", "reason": reason}
    block = st.get("quota_resume")
    if not isinstance(block, dict):
        block = {}
    if block.get("mode") != "auto":
        return {
            "action": "waiting-user",
            "reason": "quota_resume.mode=%r（未授权自动恢复），等用户"
                      "显式 authorize_quota_resume" % (block.get("mode"),)}
    resume_count = block.get("resume_count", 0)
    max_resumes = block.get("max_resumes", 0)
    if resume_count >= max_resumes:
        _ensure_transition_allowed(st, "waiting_user",
                                   "scheduled_activation_decision")
        st["status"] = "waiting_user"
        state.save_state(repo_root, st)
        return {
            "action": "waiting-user",
            "reason": "自动恢复预算已耗尽（resume_count=%d >= "
                      "max_resumes=%d），任务转 waiting_user 等用户处理"
                      % (resume_count, max_resumes)}
    workflow_run_id = st.get("workflow_run_id")
    if not isinstance(workflow_run_id, str) or workflow_run_id == "":
        raise ValueError(
            "scheduled_activation_decision：任务 %s 无关联 "
            "workflow_run_id，无 run 可恢复，拒绝给出 resume-authorized"
            % task_id)
    return {
        "action": "resume-authorized",
        "workflow_run_id": workflow_run_id,
        "resume_count_after": resume_count + 1,
        "reason": "额度可用（%s）且已授权、预算有余（%d/%d）；计数仅"
                  "暂存，宿主 resume 调用被接受后请调 "
                  "confirm_resume_started 落账"
                  % (view_status, resume_count, max_resumes),
    }


def confirm_resume_started(repo_root, task_id) -> dict:
    """暂存恢复计数落账并转回 active（workflow 阶段），返回更新后的状态。

    P3-C 确认通道：仅在宿主 resume 调用真正被接受后由调用方调用。
    三件事（顺序固定）：
      1. resume_count +1（暂存计数唯一落账点）；
      2. 任务 waiting_quota → active，phase 标注 workflow（恢复的
         即 workflow 执行阶段）；
      3. 落 quota_resume_confirmed 事件（含 resume_count /
         max_resumes / workflow_run_id）。
    结构性拒绝（ValueError，先于一切写盘）：任务不处于 waiting_quota
    （含重复确认——确认后任务已 active，二次确认无暂存决策可落账，
    绝不重复消耗预算）/ mode 非 auto（未授权）/ 计数形状异常 /
    预算已耗尽（resume_count >= max_resumes，落账必破预算不变量）。
    """
    st = _load_v24_state(repo_root, task_id)
    status = st.get("status")
    if status != "waiting_quota":
        raise ValueError(
            "confirm_resume_started：任务 %s 处于 %r，非 waiting_quota"
            "——无暂存恢复决策可落账（重复确认安全：确认后任务已转"
            " active）" % (task_id, status))
    block = st.get("quota_resume")
    if not isinstance(block, dict) or block.get("mode") != "auto":
        raise ValueError(
            "confirm_resume_started：任务 %s 的 quota_resume.mode=%r，"
            "未授权自动恢复，拒绝落账"
            % (task_id,
               block.get("mode") if isinstance(block, dict) else None))

    def _bad_count(value):
        return isinstance(value, bool) or not isinstance(value, int) \
            or value < 0

    resume_count = block.get("resume_count")
    max_resumes = block.get("max_resumes")
    if _bad_count(resume_count) or _bad_count(max_resumes):
        raise ValueError(
            "confirm_resume_started：任务 %s 的 quota_resume 计数形状"
            "非法（resume_count=%r / max_resumes=%r），拒绝落账"
            % (task_id, resume_count, max_resumes))
    if resume_count >= max_resumes:
        raise ValueError(
            "confirm_resume_started：任务 %s 预算已耗尽（resume_count="
            "%d >= max_resumes=%d），无暂存决策可落账"
            % (task_id, resume_count, max_resumes))
    block["resume_count"] = resume_count + 1
    st["status"] = "active"
    st["phase"] = "workflow"
    state.save_state(repo_root, st)
    journal.append_event(
        repo_root, task_id,
        {"event": "quota_resume_confirmed",
         "resume_count": resume_count + 1,
         "max_resumes": max_resumes,
         "workflow_run_id": st.get("workflow_run_id")})
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
