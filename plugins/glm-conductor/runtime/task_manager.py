#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GLM Conductor v2 派发生命周期事务边界层（v2.0.1 加固工作包 H4，审查项 P1-3）。

职责：
    把「plan → lease → 状态转换 → dispatch.active 记账 → save → journal
    事件」的分步手工拼接固化为四个高层 API。在此之前，真实生命周期要求
    主会话按顺序完成 plan_dispatch → acquire_lease → transition ready→
    running → dispatch.active 记账 → save_state → Agent 调用——中间任一
    点中断都会留下「计划已批准但未加锁」「已加锁但未记 running」
    「running 已落盘但 Agent 未真正启动」等半状态，恢复语义散落在约定
    里。本模块把固定顺序固化进代码：主会话只调这四个入口，不再亲手拼
    接细粒度状态一致性。

    本模块是唯一做 I/O 编排的派发事务层；dispatcher 保持纯决策器地位
    不变（零 I/O，只产出决策 dict），lease 只提供 owner map 原语。

四 API 时序（文字图，固定 journal → lease → state → save 的拼接顺序）：

    prepare_dispatch(u)            commit_dispatch(u)
      1 load_state                   1 load_state
      2 单元须 ready                 2 单元须 ready
      3 plan_dispatch（纯决策）      3 租约在位校验（归一路径 ∧ owner==u）
      4 落选 → TaskManagerError     4 ready→running（§62 表内转换）
      5 acquire_lease（§78 幂等）    5 active 记账（幂等防御）+
      6 journal dispatch_prepared      任务级非执行态顺手转 executing
      7 返回决策快照（不落盘）       6 save_state（单次）
        ↓ 不改单元状态、不 save      7 journal implementation_started
        ↓                              ↓ 返回提交后的 state dict
    ──────────── [Agent 实施] ────────────
        ↓                              ↓
    abort_dispatch(u)              finish_unit(u, outcome=…)
      1 load_state                   1 outcome ∈ {completed,failed,cancelled}
      2 running → 拒绝（已提交，    2 load_state；单元须存在
        不得静默回退）               3 状态须 ∈ {running, verifying}
      3 release_lease（全部释放）    4 completed → RB-1 完成证据门：
      4 active 残留 → 移除 + save      fresh_unit_verification 须全部
      5 journal dispatch_aborted       命中（missing/stale/partial →
      6 返回 state dict                拒绝且零副作用；git/指纹失败
                                       fail-closed 同拒）
                                     5 running+completed → 先 verifying 再
                                       completed（§70 父验证语义 = 两次
                                       表内转换）；其余单步直达
                                     6 release_lease + active 移除
                                     7 save_state（单次）
                                     8 journal unit_finished
                                       ↓ 返回 state dict

崩溃窗口恢复映射（确定性，文档化契约）：
    prepare 后崩   → 单元仍 ready + 租约在位（§78 同 owner 幂等，自有
                     租约不挡后续 plan）→ 可 commit_dispatch 完成提交，
                     可 abort_dispatch 回退释放——两条路径都安全；
    commit 后崩    → 单元 running + 无验证证据 → 由既有 reconcile 按
                     §69 证据三分恢复（ownership 内无残留 → ready；
                     残留 + 绑定当前改动的新鲜验证 → completed；残留
                     无新鲜证据 → verifying）——本模块不重造恢复逻辑。
    finish 单次 save 原子落盘：崩在 save 前单元仍在 running/verifying
    （同 commit 后窗口，reconcile 接管）；崩在 save 后即终态，reconcile
    对终态零建议（§68 completed 不重跑）。

租约生命周期闭环（v2.0.1 加固 H5，审查项 P1-4/P1-5）：
    prepare 以 LEASE_DEFAULT_TTL_SECONDS（1800 秒）保守 TTL 落盘
    expires_at（session_id/generation/heartbeat_at 同记录落盘），长期
    实施由 runtime.lease.renew_lease 心跳续约；崩溃后 recover_leases
    对租约做 stale / active / expired_running 三分对账——stale（owner
    已不在活跃写相）自动释放 + lease_recovered 事件（零释放不落事件），
    expired_running（活跃写相但已过期——worker 可能仍在写）仅上报、
    裁决归主会话——崩溃后无需人工删除 leases.json。

单元验证证据归属绑定（v2.0.1 加固 H6，审查项 P1-7）+ RB-1 完成证据门
（release hardening WU-P2，计划 §2 RB-1）：
    record_unit_verification 是单元级验证证据的唯一推荐写入口——主
    会话亲自跑完单元验证命令后调用（时序：commit_dispatch → [Agent
    实施] → record_unit_verification → finish_unit）。事件显式携带
    unit 字段，reconcile 恢复对账按 unit 逐字精确匹配：相同 command /
    重叠 ownership 的单元之间不存在错误复用证据的空间。任务级（完成
    门口径）证据仍走 runtime.state.record_verification，两者口径正交。

    H6 起 evidence 不只是恢复口径，还是完成口径：finish_unit(outcome=
    "completed") 进入转换前必须先过 reconcile.fresh_unit_verification
    完成证据门——全部 required command 各存在一条绑定当前指纹的新鲜
    pass 事件（all-match）才允许 completed；missing / stale / partial /
    wrong-unit / legacy 无 unit 字段证据一律 TaskManagerError 且零副作用
    （不写任何 journal 事件，含拒绝事件——计划 §2.3.4）；git / 指纹
    读取失败同样 fail-closed 拒绝（无法判定新鲜即不能完成）。failed /
    cancelled 收尾不需要证据。注意 record 与 finish 之间的任何 git 提交
    都会改变基线修订使证据失效，须重验。空 required 单元经谓词快路径
    零 git 放行（生产单元受「无验证命令的单元不可派发」的 validate_state
    约束不会出现该形状）。RB-1 的动机：prepare→commit→finish 此前可
    完全绕过 record_unit_verification，验证缺失会经 refresh_readiness
    传播为错误解锁下游 DAG（release blocker RB-1）。

多单元就绪流转（v2.0.1 收尾，dogfood 缺口补齐）：
    finish_unit 使某单元到达 completed 后调用 refresh_readiness——
    依赖全部 completed 的 pending / waiting_dependency 单元提升为
    ready（§62 表内合法边；就绪是可推导的状态提升，刻意不落
    journal），随后 prepare_dispatch 即可直接准入下一单元——主会话
    不再手工转态。

分层关系：
    runtime.dispatcher —— 纯决策器：plan_dispatch 零 I/O，只产出「谁可
        派发 / 谁挂起及理由」的决策 dict；本层在 prepare 中消费它；
    runtime.lease —— 落盘 owner map：acquire/release 原语，获取时点
        （prepare）与释放时点（finish/abort）由本层锚定；
    runtime.state / runtime.journal —— 状态与事件持久化；save_state 的
        任务级转换门照常生效（本层只动 work_units/dispatch 与合法的
        executing 直达边，不触碰终态任务级状态）；
    runtime.reconcile —— 恢复对账：commit 之后的中断裁决归它（本层只
        负责把崩溃窗口收敛到它已认识的状态）。

依赖：
    runtime.state（load/save）、runtime.dispatcher（纯决策）、
    runtime.lease（租约原语）、runtime.work_unit（§62 表内转换）、
    runtime.ownership（路径归一）、runtime.journal（事件追加）。
    仅 Python 3 标准库，`python3 -S` 可运行。

来源：
    docs/GLM-Conductor-v2.0.0-全面审查与v2.0.1加固建议.md §4.1 / P1-3
    + docs/glm-conductor-v2-upgrade-guide-final.md §62（状态转换表）/
    §64-§67（准入）/ §70（父验证）/ §78（租约时点）/ §69（恢复对账）
    + docs/GLM-Conductor-v2.0.1-Release-Hardening-Patch-Agent-Implementation-Plan.md
    （RB-1 / WU-P2：finish_unit 完成证据门，缺证据零副作用拒绝）。
"""

from runtime import dependency
from runtime import dispatcher
from runtime import journal
from runtime import lease
from runtime import ownership
from runtime import reconcile
from runtime import state
from runtime import work_unit
from runtime.lease import LEASE_DEFAULT_TTL_SECONDS

# finish_unit 的合法结局词汇（work unit 终态全集，§62）
FINISH_OUTCOMES = ("completed", "failed", "cancelled")

# commit_dispatch 中视为「非执行态」、顺手直达 executing 的任务级状态
# （TASK_TRANSITIONS 内的合法边；executing 及之后的执行态不重复翻转）
PRE_DISPATCH_TASK_STATUSES = ("created", "preflight", "routed", "decomposed")


class TaskManagerError(Exception):
    """task_manager 事务边界违背（单元缺失/状态不符/租约丢失/决策未批准
    /RB-1 完成证据缺失或不可判定）。"""


# —— 内部助手（容错读取，均不改入参语义） ——

def _require_state(repo_root, task_id, api) -> dict:
    """load_state 并要求任务存在：缺失 → TaskManagerError；JSON 损坏的
    ValueError 自然上抛（不静默、不覆盖损坏文件）。"""
    st = state.load_state(repo_root, task_id)
    if st is None:
        raise TaskManagerError(
            "%s：任务 %s 不存在（无 state.json），无法执行派发事务"
            % (api, task_id))
    return st


def _require_unit(st, task_id, uid, api) -> dict:
    """按 id 查找单元：找不到 → TaskManagerError（消息含 uid 与任务 id）。"""
    units = st.get("work_units")
    if isinstance(units, list):
        for unit in units:
            if isinstance(unit, dict) and unit.get("id") == uid:
                return unit
    raise TaskManagerError(
        "%s：任务 %s 的 work_units 中找不到单元 %s" % (api, task_id, uid))


def _normalized_ownership(unit) -> "list[str]":
    """单元 ownership 归一为路径列表（commit 租约校验用）：非 list/tuple
    容错为 []（与 prepare 的 acquire 容错同口径）；非字符串路径项交
    ownership.normalize_path 抛结构性错误（不静默转换）。"""
    owned = unit.get("ownership")
    if not isinstance(owned, (list, tuple)):
        return []
    return [ownership.normalize_path(path) for path in owned]


def _active_ids(st) -> "list[str]":
    """容错读取 dispatch.active（缺失/形状异常按空处理）。"""
    dispatch_block = st.get("dispatch")
    if not isinstance(dispatch_block, dict):
        return []
    active = dispatch_block.get("active")
    return list(active) if isinstance(active, list) else []


def _set_active(st, active) -> None:
    """就地写回 dispatch.active（dispatch 块缺失时按需重建）。"""
    dispatch_block = st.get("dispatch")
    if not isinstance(dispatch_block, dict):
        dispatch_block = {}
        st["dispatch"] = dispatch_block
    dispatch_block["active"] = list(active)


def _remove_active(st, uid) -> bool:
    """从 dispatch.active 移除 uid（在则删）；发生移除时返回 True。
    只改内存 dict，落盘归调用方（save 时点由各 API 的顺序表固定）。"""
    active = _active_ids(st)
    if uid not in active:
        return False
    active.remove(uid)
    _set_active(st, active)
    return True


# —— prepare：plan 决策 + 租约 + 事件（无 state 副作用） ——

def prepare_dispatch(repo_root, task_id, uid, *, quota_status="AVAILABLE",
                     allow_small_under_pressure=False, max_workers=None) -> dict:
    """派发准备：准入决策 → 租约 → dispatch_prepared 事件；不改单元状态、
    不 save——prepare 对 state.json 零副作用（可安全重试）。

    流程：
      1. load_state（任务缺失 TaskManagerError；损坏 ValueError 上抛），
         找不到 uid → TaskManagerError；
      2. 单元 status 必须为 "ready"，否则 TaskManagerError（消息含当前
         状态）；
      3. max_workers 缺省取 state["dispatch"]["max_workers"]（缺失按 1；
         显式传参覆盖）；
      4. 读出已落盘租约（lease.lease_state，损坏 ValueError 上抛）交给
         plan_dispatch 的租约闸——自有租约不挡自己（§78 同 owner 放行），
         因此 prepare 后崩溃重试安全；
      5. plan = dispatcher.plan_dispatch(...)（纯决策器，零 I/O）；
      6. uid 不在 plan["dispatch"] → TaskManagerError，消息含落选原因
         （waiting_quota 组 → quota EXHAUSTED；deferred 组 → 对应
         reason；未进候选 → 依赖未满足等）；
      7. lease.acquire_lease（全有或全无，同 owner 幂等；ownership 非
         list 容错为 []；按 LEASE_DEFAULT_TTL_SECONDS 保守 TTL 落盘
         expires_at，长期实施由 runtime.lease.renew_lease 心跳续约）；
      8. journal dispatch_prepared（unit + leased=持有中的归一路径 +
         ttl_seconds，排序确定）；
      9. 返回 plan 决策快照（供调用方参考，不落盘）。

    失败零副作用锚定：决策未批准（步骤 6）发生在获取租约（步骤 7）与
    journal（步骤 8）之前——落选的 prepare 不写租约、不写事件。
    """
    api = "prepare_dispatch"
    st = _require_state(repo_root, task_id, api)
    unit = _require_unit(st, task_id, uid, api)
    current = unit.get("status")
    if current != "ready":
        raise TaskManagerError(
            "%s：单元 %s 当前状态为 %r 而非 ready，不能准备派发"
            % (api, uid, current))
    dispatch_block = st.get("dispatch")
    if not isinstance(dispatch_block, dict):
        dispatch_block = {}
    if max_workers is None:
        max_workers = dispatch_block.get("max_workers", 1)
    active = dispatch_block.get("active")
    if not isinstance(active, list):
        active = []
    # 租约事实交给决策器的租约闸（record dict 形状由 plan_dispatch 容错）
    leases = lease.lease_state(repo_root, task_id)
    plan = dispatcher.plan_dispatch(
        st.get("work_units"), max_workers=max_workers, active=active,
        quota_status=quota_status,
        allow_small_under_pressure=allow_small_under_pressure,
        leases=leases)
    if uid not in plan["dispatch"]:
        if uid in plan["waiting_quota"]:
            why = "quota EXHAUSTED（配额耗尽，决策建议转 waiting_quota）"
        else:
            entry = None
            for item in plan["deferred"]:
                if isinstance(item, dict) and item.get("id") == uid:
                    entry = item
                    break
            if entry is not None:
                why = "挂起（deferred）：%s" % entry.get("reason")
            else:
                why = ("未进入派发候选（依赖未全部 completed 或状态未提升"
                       "为 ready）")
        raise TaskManagerError(
            "%s：plan_dispatch 未批准单元 %s（当前状态 %r）——%s"
            % (api, uid, current, why))
    owned = unit.get("ownership")
    if not isinstance(owned, (list, tuple)):
        owned = []
    lease.acquire_lease(repo_root, task_id, uid, owned,
                        ttl_seconds=LEASE_DEFAULT_TTL_SECONDS)
    # leased 记持有事实（归一路径、排序、同 owner 幂等重入不虚报）
    held = lease.held_by(repo_root, task_id, uid)
    journal.append_event(repo_root, task_id, {
        "event": "dispatch_prepared", "unit": uid, "leased": held,
        "ttl_seconds": LEASE_DEFAULT_TTL_SECONDS})
    return plan


# —— commit：ready→running + active 记账 + 单次 save ———

def commit_dispatch(repo_root, task_id, uid) -> dict:
    """提交派发：租约在位校验 → ready→running → active 记账 → 单次 save
    → implementation_started 事件；返回提交后的 state dict。

    流程：
      1. load_state；找不到 uid → TaskManagerError；
      2. 单元 status 必须为 "ready"——重复 commit（已 running）/状态已
         漂移都会在此拒绝；
      3. 租约在位校验：ownership 每个归一路径都在 lease_state 中且
         owner == uid，任一缺失/异主 → TaskManagerError（提示先
         prepare_dispatch；租约已丢失时 abort_dispatch 后重新准备）；
      4. transition_work_unit(unit, "running")（§62 表内转换，非法
         ValueError 自然上抛）；
      5. dispatch.active 追加 uid（幂等防御：已在则不重复）；任务级
         status 为非执行态（created/preflight/routed/decomposed）时
         顺手转 "executing"（TASK_TRANSITIONS 合法边，简化调用方）；
      6. save_state 一次（任务级转换门照常生效）；
      7. journal implementation_started（unit + executor）。

    commit 之后单元进入 running：此后回退不归本模块——收尾用
    finish_unit，中断恢复按 §69 走 reconcile。
    """
    api = "commit_dispatch"
    st = _require_state(repo_root, task_id, api)
    unit = _require_unit(st, task_id, uid, api)
    current = unit.get("status")
    if current != "ready":
        raise TaskManagerError(
            "%s：单元 %s 当前状态为 %r 而非 ready（重复 commit 或状态已"
            "漂移都会在此拒绝——如需回退未提交的准备请用 abort_dispatch）"
            % (api, uid, current))
    owned = _normalized_ownership(unit)
    leases = lease.lease_state(repo_root, task_id)
    broken = []
    for path in owned:
        record = leases.get(path)
        holder = record.get("owner") if isinstance(record, dict) else None
        if holder != uid:
            broken.append("%s（持有者 %s）" % (path, holder))
    if broken:
        raise TaskManagerError(
            "%s：单元 %s 的租约不在位（%s）——先 prepare_dispatch 获取"
            "租约；若租约已丢失请 abort_dispatch 后重新准备"
            % (api, uid, "；".join(broken)))
    work_unit.transition_work_unit(unit, "running")
    active = _active_ids(st)
    if uid not in active:
        active.append(uid)
    _set_active(st, active)
    if st.get("status") in PRE_DISPATCH_TASK_STATUSES:
        st["status"] = "executing"
    state.save_state(repo_root, st)
    journal.append_event(repo_root, task_id, {
        "event": "implementation_started", "unit": uid,
        "executor": unit.get("executor")})
    return st


# —— abort：prepare 之后、commit 之前的回退 ——

def abort_dispatch(repo_root, task_id, uid) -> dict:
    """回退未提交的派发准备：释放租约 + dispatch_aborted 事件；返回
    state dict。

    流程：
      1. load_state；找不到 uid → TaskManagerError；
      2. 单元 status == "running" → TaskManagerError（已提交，不得静默
         回退——用 finish_unit 收尾或按 §69 走 reconcile）；
      3. lease.release_lease 全部释放该 owner 的租约；
      4. uid 残留在 dispatch.active（异常残留）→ 移除并 save_state；
         无残留则不落盘（abort 对 state.json 零写入是常态）；
      5. journal dispatch_aborted。

    不改单元状态：prepare 对 state 零副作用，abort 也不需要补偿单元
    状态——回退后单元仍是 ready，可重新 prepare。
    """
    api = "abort_dispatch"
    st = _require_state(repo_root, task_id, api)
    unit = _require_unit(st, task_id, uid, api)
    if unit.get("status") == "running":
        raise TaskManagerError(
            "%s：单元 %s 已提交（状态 running）——不得静默回退，用 "
            "finish_unit 收尾或按 §69 走 reconcile 恢复对账" % (api, uid))
    lease.release_lease(repo_root, task_id, uid)
    if _remove_active(st, uid):
        state.save_state(repo_root, st)
    journal.append_event(repo_root, task_id, {
        "event": "dispatch_aborted", "unit": uid})
    return st


# —— finish：running/verifying → 终态 + 释放 + 单次 save ——

def _require_completion_evidence(repo_root, task_id, uid, unit) -> None:
    """RB-1 完成证据门（release hardening WU-P2）：completed 收尾前校验
    全部 required 验证命令的新鲜单元证据，不满足即 TaskManagerError。

    零副作用由调用顺序保证——本助手只在 finish_unit 的任何转换 /
    release / save / journal 之前调用，拒绝路径不写任何 journal 事件
    （含拒绝事件，计划 §2.3.4：不扩大事件面）。

    - 复用 reconcile.fresh_unit_verification（WU-P1 共享证据谓词，缺省
      自取 touched / events；与恢复对账同一口径）；空 required 单元经
      谓词快路径零 git 放行（生产单元受「无验证命令的单元不可派发」
      的 validate_state 约束不会出现该形状，此性质是谓词共用口径）；
    - evidence["ok"] 为 False（missing / stale / partial / wrong-unit /
      legacy 无 unit 字段证据）→ TaskManagerError，消息含 api 名、uid、
      missing 命令清单（逐条）与补证指引；
    - ownership.OwnershipError / fingerprint.FingerprintError → 包装为
      TaskManagerError（fail-closed：无法判定证据新鲜性即不能完成）。
    """
    from runtime import fingerprint  # 局部导入：不新增模块级 import
    api = "finish_unit"
    try:
        evidence = reconcile.fresh_unit_verification(repo_root, task_id,
                                                     unit)
    except (ownership.OwnershipError, fingerprint.FingerprintError) as exc:
        raise TaskManagerError(
            "%s：单元 %s 完成被拒：无法判定证据新鲜性，fail-closed 拒绝"
            "完成（原因：%s）" % (api, uid, exc)) from exc
    if not evidence["ok"]:
        raise TaskManagerError(
            "%s：单元 %s 完成被拒（RB-1 完成证据门）：required 验证命令"
            "缺少新鲜 pass 证据（missing：%s）——亲自运行单元验证命令后"
            "用 record_unit_verification 记录（时序：record → "
            "finish_unit → commit，record 与 finish 之间的任何 git 提交"
            "都会使证据失效须重验）"
            % (api, uid, "；".join(evidence["missing"])))


def finish_unit(repo_root, task_id, uid, *, outcome="completed") -> dict:
    """单元收尾：RB-1 完成证据门（completed 时）→ 终态转换 → 释放租约
    → active 移除 → 单次 save → unit_finished 事件；返回 state dict。

    流程：
      1. outcome ∈ ("completed", "failed", "cancelled")，否则 ValueError
        （先于任何 I/O 校验，失败零副作用）；
      2. load_state；找不到 uid → TaskManagerError；
      3. 单元 status 须 ∈ ("running", "verifying")，否则 TaskManagerError
         （消息含当前状态）；
      4. 【RB-1 完成证据门，WU-P2】仅 outcome == "completed" 时：调
         reconcile.fresh_unit_verification(repo_root, task_id, unit)
         （缺省自取 touched / events）——全部 required command 须各存在
         一条绑定当前指纹的新鲜 pass 证据（all-match），才允许进入
         转换；missing / stale / partial / wrong-unit / legacy 无 unit
         字段证据一律 TaskManagerError（消息含 missing 清单与
         record_unit_verification 补证指引），此时零副作用——state.json
         字节、leases.json、dispatch.active、journal 全部不变；git /
         指纹读取失败（OwnershipError / FingerprintError）同样拒绝
         （fail-closed 包装为 TaskManagerError）。failed / cancelled
         收尾不需要证据。空 required 单元经谓词快路径零 git 直接放行；
      5. 转换：running + completed → 先 verifying 再 completed（§70 父
         验证语义：worker 报告只是 claim，编码为两次表内转换）；其余
         单步直达（running→failed/cancelled、verifying→completed/
         failed/cancelled，均在 §62 表内）；
      6. lease.release_lease + dispatch.active 移除 uid（在则删）；
      7. save_state 一次；
      8. journal unit_finished（unit + outcome）。

    证据门先于转换是刻意的：拒绝发生在任何变更之前——被拒的收尾可
    安全重试（补证后重调即可）。转 verifying 不落盘也是刻意的：它只是
    running+completed 双跳的中间步，落盘与否不影响恢复语义——崩在双跳
    之间，盘上仍是 running，reconcile 按 §69 对账的结果一致。
    """
    api = "finish_unit"
    if outcome not in FINISH_OUTCOMES:
        raise ValueError(
            "%s：outcome %r 不在合法取值内（%s）"
            % (api, outcome, ", ".join(FINISH_OUTCOMES)))
    st = _require_state(repo_root, task_id, api)
    unit = _require_unit(st, task_id, uid, api)
    current = unit.get("status")
    if current not in ("running", "verifying"):
        raise TaskManagerError(
            "%s：单元 %s 当前状态为 %r，须为 running 或 verifying 才能"
            "收尾" % (api, uid, current))
    if outcome == "completed":
        # RB-1 完成证据门（WU-P2）：先验证据后转换——拒绝发生在任何
        # 变更之前（转换 / release / save / journal 均未发生）
        _require_completion_evidence(repo_root, task_id, uid, unit)
    if current == "running" and outcome == "completed":
        # §70：正常完成必须经 verifying（worker 报告只是 claim，父会话
        # 验证后才算 completed）——两次表内转换，不越表直跳
        work_unit.transition_work_unit(unit, "verifying")
    work_unit.transition_work_unit(unit, outcome)
    lease.release_lease(repo_root, task_id, uid)
    _remove_active(st, uid)
    state.save_state(repo_root, st)
    journal.append_event(repo_root, task_id, {
        "event": "unit_finished", "unit": uid, "outcome": outcome})
    return st


# —— readiness：就绪推导态提升（v2.0.1 收尾，dogfood 缺口补齐） ——

# 依赖满足后可被提升为 ready 的单元状态（§62 表内合法边：
# pending → ready 与 waiting_dependency → ready 均在转换表内）
PROMOTABLE_STATUSES = ("pending", "waiting_dependency")


def refresh_readiness(repo_root, task_id) -> "list[str]":
    """把「依赖已全部 completed」的 pending / waiting_dependency 单元
    提升为 ready，返回本次提升的 uid 列表（按 units 出现序）。

    动机（dogfood 发现）：单元依赖满足后会停在 pending——
    prepare_dispatch 只接受 ready 单元（§64 就绪集推导），此前依赖
    满足后的状态提升靠调用方手工 transition_work_unit。本 API 把
    这一步固化为显式入口。

    流程：
      1. load_state（任务缺失 TaskManagerError；损坏 ValueError 上抛）；
      2. 逐单元：status ∈ PROMOTABLE_STATUSES 且
         dependency.deps_satisfied（每个依赖都存在且 completed）→
         transition_work_unit(unit, "ready")（§62 表内合法边）；
      3. 有提升才 save_state（单次）；无提升零写入（零落盘、零
         半状态，可安全重复调用）；
      4. 不写 journal——就绪是可从依赖图重新推导的状态提升，不是
         生命周期事实；journal 只记事实（派发 / 终态 / 租约），
         不记推导（刻意裁量，非遗漏）。

    调用时机：commit_dispatch / finish_unit 使某单元到达 completed
    之后、prepare_dispatch 派发下一单元之前——下游单元即可直接过
    准入，无需任何手工转态。
    """
    api = "refresh_readiness"
    st = _require_state(repo_root, task_id, api)
    units = st.get("work_units")
    if not isinstance(units, list):
        return []
    by_id = {unit.get("id"): unit for unit in units
             if isinstance(unit, dict) and isinstance(unit.get("id"), str)}
    promoted = []
    for unit in units:
        if not isinstance(unit, dict):
            continue
        if unit.get("status") not in PROMOTABLE_STATUSES:
            continue
        if not dependency.deps_satisfied(unit, by_id):
            continue
        work_unit.transition_work_unit(unit, "ready")
        promoted.append(unit.get("id"))
    if promoted:
        state.save_state(repo_root, st)
    return promoted


# —— 单元验证证据写入口（v2.0.1 加固 H6，审查项 P1-7） ——

# 单元级 verification 事件的状态词汇（与 reconcile 证据匹配条件一致：
# 只认 "pass"；"fail" 允许写入以留痕，但不构成完成证据）
UNIT_VERIFICATION_STATUSES = ("pass", "fail")


def record_unit_verification(repo_root, task_id, uid, command, fingerprint,
                             *, status="pass") -> dict:
    """单元级验证证据的唯一推荐写入口，返回写入的事件 dict。

    主会话亲自跑完单元验证命令后调用（§70：worker 报告只是 claim）；
    事件形状 {"event": "verification", "unit": uid, "command": command,
    "status": status, "fingerprint": fingerprint}——unit 字段使
    reconcile 恢复对账按单元精确匹配（H6）：相同 command / 重叠
    ownership 的单元之间不再存在错误复用证据的空间；无 unit 字段的
    旧格式事件自 H6 起不再被 reconcile 采信（保守按无证据处理）。

    参数校验（先于任何 I/O，失败零副作用）：uid / command /
    fingerprint 必须是非空 str，status ∈ UNIT_VERIFICATION_STATUSES
    ("pass", "fail")，否则 ValueError（中文消息含字段名）。

    分工锚定：本函数只写 journal（单元级恢复证据），不触碰
    state.json——任务级（完成门口径）验证证据仍走
    runtime.state.record_verification（写入 verification.completed /
    fingerprint，供 Stop 完成门比对），两者口径正交、互不替代。
    """
    api = "record_unit_verification"
    if not isinstance(uid, str) or uid == "":
        raise ValueError(
            "%s：uid 必须是非空字符串，得到 %r" % (api, uid))
    if not isinstance(command, str) or command == "":
        raise ValueError(
            "%s：command 必须是非空字符串，得到 %r" % (api, command))
    if not isinstance(fingerprint, str) or fingerprint == "":
        raise ValueError(
            "%s：fingerprint 必须是非空字符串，得到 %r"
            % (api, fingerprint))
    if status not in UNIT_VERIFICATION_STATUSES:
        raise ValueError(
            "%s：status %r 不在合法取值内（%s）"
            % (api, status, ", ".join(UNIT_VERIFICATION_STATUSES)))
    return journal.append_event(repo_root, task_id, {
        "event": "verification", "unit": uid, "command": command,
        "status": status, "fingerprint": fingerprint})


# —— recover：崩溃后 stale 租约闭环清理（v2.0.1 加固 H5，P1-4/P1-5） ——

def recover_leases(repo_root, task_id, *, now=None) -> dict:
    """崩溃恢复的租约清理入口：reconcile_leases 三分对账 → 仅释放
    stale 组 → lease_recovered 事件；返回对账结果 + released 清单。

    流程：
      1. load_state（任务缺失 TaskManagerError；units 非列表按空图
         容错）；
      2. reconcile.reconcile_leases 纯建议三分（stale / active /
         expired_running，各桶按 path 排序）；
      3. 仅对 stale 组逐路径 release_lease（owner 精确、不代删他人）；
         形状异常记录（owner 非非空字符串）无精确持有者可删，保守
         跳过——仍在返回值的 stale 组中可见；
      4. released 非空时 journal {"event": "lease_recovered",
         "released": [...], "kept_expired_running": [...]}；零释放不
         落事件（对账是只读惯例动作，不留噪声行）；
      5. expired_running（活跃写相但已过期——worker 可能仍在写）不
         动，仅在返回值中上报，裁决归主会话。

    返回：reconcile_leases 的结果 dict + "released" 键（实际释放的
    归一路径，排序）。崩溃后无需人工删除 leases.json——recover 与
    后续 prepare（§78 同 owner 幂等、自有租约不挡 plan）共同闭环。
    """
    api = "recover_leases"
    st = _require_state(repo_root, task_id, api)
    units = st.get("work_units")
    if not isinstance(units, list):
        units = []
    report = reconcile.reconcile_leases(repo_root, task_id, units, now=now)
    released = []
    for entry in report["stale"]:
        holder = entry["owner"]
        if not isinstance(holder, str) or holder == "":
            continue  # 无精确持有者可删，保守不动（stale 组仍上报）
        if lease.release_lease(repo_root, task_id, holder, [entry["path"]]):
            released.append(entry["path"])
    released = sorted(released)
    if released:
        journal.append_event(repo_root, task_id, {
            "event": "lease_recovered", "released": released,
            "kept_expired_running": sorted(
                item["path"] for item in report["expired_running"])})
    report["released"] = released
    return report
