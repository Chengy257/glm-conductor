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
      6 create_permit（v2.1 M2）       任务级非执行态顺手转 executing
      7 journal dispatch_prepared   6 save_state（单次）
        + dispatch_permit_created   7 journal implementation_started
      8 返回决策快照 + permit          ↓ 返回提交后的 state dict
        ↓ 不改单元状态、不 save
        ↓
    ──────────── [Agent 实施] ────────────
        ↓                              ↓
    abort_dispatch(u)              finish_unit(u, outcome=…)
      1 load_state                   1 outcome ∈ {completed,failed,cancelled}
      2 running → 拒绝（已提交，    2 load_state；单元须存在
        不得静默回退）               3 状态须 ∈ {running, verifying}
      3 release_lease（全部释放）    4 completed → RB-1 完成证据门：
      4 active 残留 → 移除 + save      fresh_unit_verification 须全部
      5 journal dispatch_aborted       命中（missing/stale/partial →
      6 invalidate 未消费 permit +     拒绝且零副作用；git/指纹失败
        journal dispatch_permit_       fail-closed 同拒）
        invalidated                  5 running+completed → 先 verifying 再
      7 返回 state dict                completed（§70 父验证语义 = 两次
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

派发 permit 接线（v2.1 M2 前半，wu-21-02，计划 §6.3/§6.6）：
    真实 dogfood 中主会话曾完全绕过 prepare/commit 手工派发 Agent；
    v2.1 用「无 permit 即 deny」的 PreToolUse 门（wu-21-03 实施 hook
    侧）堵住 bypass，本模块是该门的数据层接线：prepare_dispatch 在
    租约获取之后为该单元签发一张落盘 permit（runtime.dispatch_wave
    的每 permit 一文件 + rename 消费防重放原语），mode 取 state
    execution_policy.worker_execution.default_mode（缺块 / 坏形状按
    default_execution_policy() 兜底），返回决策快照新增 "permit" 键
    （向后兼容增量，现有键全部保留）并落 journal
    dispatch_permit_created；主会话用 dispatch_wave.marker_for(
    permit_id) 构造 marker 放进 Agent prompt 派发，hook 侧 consume
    （本层不代消费——commit_dispatch / finish_unit 行为不变，未消费
    permit 随 DEFAULT_TTL_SECONDS 自然过期兜底）；abort_dispatch 在
    现有回退逻辑之后对该单元全部未消费 permit invalidate 并落
    dispatch_permit_invalidated（零失效不落事件）。

派发 wave 批量事务（v2.1 M4，wu-21-08）：
    prepare_dispatch_wave 把「决策 → 全量租约 → wave 记录 → 批量 permit
    → journal」固化为一次调用的事务入口，与 prepare_dispatch（单单元）
    并存；事务性 all-or-safe-degrade：wave 记录落盘时其全部成员租约
    已在位——绝不出现「wave 记录 2 单元但只有 1 张租约」。要点：
      - plan_dispatch 全量决策（带租约闸），批准集按 state.work_units
        原序（plan 内部是 topo 序，这里重排回账面原序）；
      - 批准集逐单元 acquire_lease；任一 LeaseConflictError → 释放本轮
        已获取的全部租约、冲突单元入 excluded 集合、剔除后重新 plan
        重试——excluded 单调增长保证有界终止；批准集空 →
        TaskManagerError（消息口径照抄 prepare_dispatch，零租约残留）；
      - 全部租约到位 → wave 记录（dispatch.waves[]，status="active"）
        随 save_state 一次落盘 → 逐成员 create_permit（携带 wave_id）→
        journal 单条 dispatch_wave_prepared（不逐单元重复
        dispatch_prepared）；单元 wave 合法（只批 1 个也成 wave）；
      - wave 关闭归 finish_unit：收尾使某 active wave 的 units 全部进入
        completed/failed/cancelled/verifying → status="closed" +
        closed_at + journal wave_closed；无 waves 键零行为；
      - hook 侧成员资格校验（dispatch_wave.validate_wave_membership，
        wu-21-03 的 permit 门追加一环）使 closed / 重组后的旧 wave
        permit 不再放行；agent_launched 事件携带 wave_id。

有界并行预算接线（v2.1 §11.5/§12，wu-21-09）：
    prepare_dispatch / prepare_dispatch_wave 共享 _effective_worker_cap
    预算折算：cap_base 优先级「显式 max_workers 参数 →
    execution_policy.parallelism.max_workers（合法 1..4 int 时）→
    dispatch.max_workers（legacy 任务）→ 2」，有效预算
    eff = min(cap_base, execution_policy.effective_worker_budget(
    policy, quota_status))——§12 表：AVAILABLE→策略 max_workers /
    PRESSURE→1 / UNKNOWN→1 / EXHAUSTED→0。据此 dispatcher 不再整批
    挂起 UNKNOWN / PRESSURE（旧的 unknown_suppressed /
    pressure_suppressed / small_only 分支与 allow_small_under_pressure
    参数已删除）——预算收缩职责从决策器移交本层；prepare_dispatch 的
    dispatch_prepared 事件新增 effective_max_workers 字段（wave 事件
    已有 worker_budget，不加重复键）；wave 的 worker_budget 自然成为
    quota 调节后的值。并发默认 2：dispatcher.DEFAULT_MAX_WORKERS、
    state.new_task_state 的 dispatch.max_workers 默认、execution_policy
    parallelism 默认块三处口径一致；policy hard_limit=4 上限不变。

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

    根解析（RB-2，release hardening WU-P3）：证据门的 git 求值根按任务
    绑定解析——state.resolve_repository_root(st, repo_root)（绑定
    repository.root 优先，legacy 回退账本根）；touched 清单与证据指纹按
    git 根计算，验证事件（events）恒从账本根 repo_root 注入——
    fresh_unit_verification 缺省会从 git 根读 journal，多仓场景（账本根
    ≠ git 根）时会读错地方。账本（state.json / events.jsonl / 租约）
    不随绑定迁移，纪律红线：state/journal/lease I/O 恒在账本根，只有
    git 操作切任务仓库根。

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
    runtime.dispatch_wave —— 落盘派发许可（v2.1 M2）：create/invalidate
        原语，签发时点（prepare）与作废时点（abort）由本层锚定；消费
        留给 wu-21-03 的 hook 侧（未消费 permit 随 TTL 自然过期兜底）；
    runtime.state / runtime.journal —— 状态与事件持久化；save_state 的
        任务级转换门照常生效（本层只动 work_units/dispatch 与合法的
        executing 直达边，不触碰终态任务级状态）；
    runtime.reconcile —— 恢复对账：commit 之后的中断裁决归它（本层只
        负责把崩溃窗口收敛到它已认识的状态）。

依赖：
    runtime.state（load/save）、runtime.dispatcher（纯决策）、
    runtime.lease（租约原语）、runtime.dispatch_wave（permit 原语）、
    runtime.execution_policy（default_execution_policy——permit mode
    策略兜底；effective_worker_budget——§12 quota 四态预算折算）、
    runtime.work_unit（§62 表内转换）、
    runtime.ownership（路径归一）、runtime.journal（事件追加）。
    仅 Python 3 标准库，`python3 -S` 可运行。

来源：
    docs/GLM-Conductor-v2.0.0-全面审查与v2.0.1加固建议.md §4.1 / P1-3
    + docs/glm-conductor-v2-upgrade-guide-final.md §62（状态转换表）/
    §64-§67（准入）/ §70（父验证）/ §78（租约时点）/ §69（恢复对账）
    + docs/GLM-Conductor-v2.0.1-Release-Hardening-Patch-Agent-Implementation-Plan.md
    （RB-1 / WU-P2：finish_unit 完成证据门，缺证据零副作用拒绝；
    RB-2 / WU-P3：证据门双根分离，git 根按任务绑定解析、events 恒从
    账本根注入）。
"""

import datetime

from runtime import dependency
from runtime import dispatch_wave
from runtime import dispatcher
from runtime import journal
from runtime import lease
from runtime import ownership
from runtime import reconcile
from runtime import resume_manifest
from runtime import state
from runtime import work_unit
from runtime.execution_policy import default_execution_policy
from runtime.execution_policy import effective_worker_budget
from runtime.execution_policy import HARD_WORKER_LIMIT
from runtime.lease import LEASE_DEFAULT_TTL_SECONDS


def _write_manifest_safe(repo_root, task_id):
    """Resume Manifest 安全挂点（v2.1 M3 wu-21-07，§10.3 失败语义）。

    在主事务（save_state + journal 事件）完成之后调用：写 manifest
    失败不得覆盖 state truth、不得让事务半提交——任何异常只落一条
    manifest_write_failed journal 警告事件（错误文本截断 200 字符），
    绝不向上传播。延迟 import 的 resume_manifest 在模块顶部已静态
    导入（无循环：resume_manifest 不 import 本模块）。
    """
    try:
        resume_manifest.write_resume_manifest(repo_root, task_id)
    except Exception as exc:  # 派生物写入失败只降级不阻断（§10.3）
        try:
            journal.append_event(repo_root, task_id, {
                "event": "manifest_write_failed",
                "error": str(exc)[:200]})
        except Exception:
            pass  # journal 也写不进（如盘满）：静默，主事务已落

# finish_unit 的合法结局词汇（work unit 终态全集，§62）
FINISH_OUTCOMES = ("completed", "failed", "cancelled")

# commit_dispatch 中视为「非执行态」、顺手直达 executing 的任务级状态
# （TASK_TRANSITIONS 内的合法边；executing 及之后的执行态不重复翻转）
PRE_DISPATCH_TASK_STATUSES = ("created", "preflight", "routed", "decomposed")

# wave 收口的单元状态闭集（v2.1 M4 wu-21-08 设计决策 3）：verifying 计入
# ——已停止写文件、等待验证裁决的单元不再阻挡 wave 关闭
WAVE_CLOSED_UNIT_STATUSES = ("completed", "failed", "cancelled", "verifying")


class TaskManagerError(Exception):
    """task_manager 事务边界违背（单元缺失/状态不符/租约丢失/决策未批准
    /RB-1 完成证据缺失或不可判定）。"""


# —— 内部助手（容错读取，均不改入参语义） ——

def _utc_now_iso() -> str:
    """当前 UTC 时刻的 ISO-8601 字符串（毫秒精度 Z 形态，与 permit /
    租约层时间字段同格式）——wave 记录 created_at / closed_at 落盘口径。"""
    moment = datetime.datetime.now(datetime.timezone.utc)
    return (moment.strftime("%Y-%m-%dT%H:%M:%S")
            + ".%03dZ" % (moment.microsecond // 1000))


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


def _default_dispatch_mode(st) -> str:
    """派发 permit 的 mode 默认值：state execution_policy 的
    worker_execution.default_mode（v2.1 M2 接线）；legacy 缺块 / 块形
    状坏 / 叶子值非法时按 execution_policy.default_execution_policy()
    的保守默认（"background"）兜底——mode 永远是 PERMIT_MODES 内的
    合法值，create_permit 不会因策略形状炸。"""
    policy = st.get("execution_policy")
    worker_execution = (
        policy.get("worker_execution")
        if isinstance(policy, dict) else None)
    mode = (worker_execution.get("default_mode")
            if isinstance(worker_execution, dict) else None)
    if mode in dispatch_wave.PERMIT_MODES:
        return mode
    return default_execution_policy()["worker_execution"]["default_mode"]


def _is_worker_cap(value) -> bool:
    """value 是否可充当并发上限：int（bool 拒绝——int 子类不充当槽位
    数）且 1 ≤ n ≤ execution_policy 冻结 hard_limit（4）。"""
    return (not isinstance(value, bool) and isinstance(value, int)
            and 1 <= value <= HARD_WORKER_LIMIT)


def _effective_worker_cap(st, quota_status, max_workers) -> int:
    """wu-21-09 预算接线（计划 §11.5/§12）：并发上限 × quota 四态折算。

    cap_base 优先级（前者缺席 / 形状坏才落后者）：
      1. 显式 max_workers 参数（非 None 即采纳）；
      2. state execution_policy.parallelism.max_workers（合法
         1..hard_limit 的 int 时）；
      3. state dispatch.max_workers（legacy 任务的账面口径，同样
         1..hard_limit 校验）；
      4. dispatcher.DEFAULT_MAX_WORKERS（=2，v2.1 起与
         new_task_state 默认、execution_policy 默认块三处口径一致）。
    有效预算 eff = min(cap_base,
    execution_policy.effective_worker_budget(policy, quota_status))：
    §12 表——AVAILABLE→策略 max_workers（policy 缺块 / 坏形状按
    default_execution_policy 的 2 兜底，effective_worker_budget 既有
    行为）/ PRESSURE→1 / UNKNOWN→1 / EXHAUSTED→0（quota_status 非法
    同样保守折 0，词汇校验仍由 plan_dispatch 的 ValueError 兜底）。

    返回 int：eff ≥ 1 时即调用方应传给 plan_dispatch 的
    max_workers；eff == 0（EXHAUSTED / 非法词汇折 0）不能直接传入
    plan_dispatch（其校验 1..hard_limit）——调用方传 max(eff, 1)，
    plan 的 quota 闸自然把候选全转 waiting_quota（预算 0 的可观察面
    就是「零派发 + waiting_quota」，错误口径与旧实现逐字一致）。
    纯函数：只读入参、零 I/O。
    """
    policy = st.get("execution_policy")
    cap_base = max_workers
    if cap_base is None:
        parallelism = (policy.get("parallelism")
                       if isinstance(policy, dict) else None)
        cap_base = (parallelism.get("max_workers")
                    if isinstance(parallelism, dict) else None)
        if not _is_worker_cap(cap_base):
            dispatch_block = st.get("dispatch")
            cap_base = (dispatch_block.get("max_workers")
                        if isinstance(dispatch_block, dict) else None)
            if not _is_worker_cap(cap_base):
                cap_base = dispatcher.DEFAULT_MAX_WORKERS
    eff = min(cap_base, effective_worker_budget(policy, quota_status))
    return eff if eff >= 1 else 0


def _validate_permit_mode(api, st, mode, reason) -> "tuple":
    """permit mode/reason 全量校验（prepare_dispatch 与
    prepare_dispatch_wave 共享，wu-21-08 抽取；消息逐字保持原口径）。

    - 显式 mode 参数优先，缺省取 state execution_policy 的
      worker_execution.default_mode（缺块/坏形状按默认块兜底，见
      _default_dispatch_mode）；
    - mode 不在 PERMIT_MODES → ValueError；
    - mode="foreground" 必须显式给出 reason ∈
      dispatch_wave.FOREGROUND_REASONS（§6.6），缺给出
      TaskManagerError、值非法 ValueError；background 恒 reason null
      （显式传入的 reason 静默归 null——接线层宽松，原语层
      create_permit 才严格拒绝）。
    返回 (effective_mode, permit_reason)。调用方必须在任何写副作用
    （租约 / wave / permit / journal）之前调用——非法参数不得留下
    半张租约或半个 wave。
    """
    effective_mode = mode if mode is not None else _default_dispatch_mode(st)
    if effective_mode not in dispatch_wave.PERMIT_MODES:
        raise ValueError(
            "%s：mode %r 不在合法取值内（%s）"
            % (api, effective_mode, ", ".join(dispatch_wave.PERMIT_MODES)))
    permit_reason = reason
    if effective_mode == "foreground":
        if permit_reason is None:
            raise TaskManagerError(
                "%s：mode=\"foreground\" 必须显式给出 reason（%s）——"
                "foreground 派发须由主会话声明原因（§6.6）"
                % (api, ", ".join(dispatch_wave.FOREGROUND_REASONS)))
        if permit_reason not in dispatch_wave.FOREGROUND_REASONS:
            raise ValueError(
                "%s：reason %r 不在合法取值内（%s）"
                % (api, permit_reason,
                   ", ".join(dispatch_wave.FOREGROUND_REASONS)))
    else:
        permit_reason = None  # background permit 的 reason 恒 null
    return effective_mode, permit_reason


# —— prepare：plan 决策 + 租约 + permit + 事件（无 state 副作用） ——

def prepare_dispatch(repo_root, task_id, uid, *, quota_status="AVAILABLE",
                     max_workers=None, mode=None, reason=None) -> dict:
    """派发准备：准入决策 → 租约 → permit 签发 → 事件；不改单元状态、
    不 save state——prepare 对 state.json 零副作用（可安全重试）。

    流程：
      1. load_state（任务缺失 TaskManagerError；损坏 ValueError 上抛），
         找不到 uid → TaskManagerError；
      2. 单元 status 必须为 "ready"，否则 TaskManagerError（消息含当前
         状态）；
      3. permit mode/reason 先于任何副作用全量校验（与「落选零副作用」
         同口径——非法 mode/reason 不得留下半张租约）：显式 mode 参数
         优先，缺省取 state execution_policy 的 worker_execution.
         default_mode（缺块/坏形状按默认块兜底，见
         _default_dispatch_mode）；mode="foreground" 必须显式给出
         reason ∈ dispatch_wave.FOREGROUND_REASONS（§6.6），缺给出
         TaskManagerError、值非法 ValueError；background 恒 reason
         null（显式传了也拒绝）；
      4. max_workers 预算接线（wu-21-09，§11.5/§12，见
         _effective_worker_cap）：cap_base 优先级为「显式参数 →
         execution_policy.parallelism.max_workers（合法 1..4 int）→
         dispatch.max_workers（legacy）→ 2」，有效预算
         eff = min(cap_base, execution_policy.effective_worker_budget(
         policy, quota_status))——AVAILABLE→策略 max_workers /
         PRESSURE→1 / UNKNOWN→1 / EXHAUSTED→0；eff 作为 max_workers
         传给 plan_dispatch（eff=0 时传 1，quota 闸自然全转
         waiting_quota——dispatcher 不再整批挂起 UNKNOWN/PRESSURE）；
      5. 读出已落盘租约（lease.lease_state，损坏 ValueError 上抛）交给
         plan_dispatch 的租约闸——自有租约不挡自己（§78 同 owner 放行），
         因此 prepare 后崩溃重试安全；
      6. plan = dispatcher.plan_dispatch(...)（纯决策器，零 I/O）；
      7. uid 不在 plan["dispatch"] → TaskManagerError，消息含落选原因
         （waiting_quota 组 → quota EXHAUSTED；deferred 组 → 对应
         reason；未进候选 → 依赖未满足等）；
      8. lease.acquire_lease（全有或全无，同 owner 幂等；ownership 非
         list 容错为 []；按 LEASE_DEFAULT_TTL_SECONDS 保守 TTL 落盘
         expires_at，长期实施由 runtime.lease.renew_lease 心跳续约）；
      9. dispatch_wave.create_permit 签发派发许可（§6.3 每 permit 一
         文件 + tmp/os.replace 原子写，ttl 取 dispatch_wave.
         DEFAULT_TTL_SECONDS；wave_id 缺省 None，M4 wave 事务接线）；
      10. journal dispatch_prepared（unit + leased=持有中的归一路径 +
          ttl_seconds + effective_max_workers=步 4 的有效预算）与
          dispatch_permit_created（unit + permit_id + mode），先后各
          一条；
      11. 返回 plan 决策快照，新增 "permit" 键（permit dict；主会话用
          dispatch_wave.marker_for(permit_id) 构造 marker 派发 Agent，
          消费归 wu-21-03 的 hook 侧）。

    失败零副作用锚定：决策未批准（步骤 7）发生在获取租约（步骤 8）与
    journal（步骤 10）之前——落选的 prepare 不写租约、不写 permit、
    不写事件。permit 落盘失败（OSError）在租约之后上抛：主会话可
    abort_dispatch 回退半状态（崩溃窗口语义见模块 docstring permit
    接线一节）。
    """
    api = "prepare_dispatch"
    st = _require_state(repo_root, task_id, api)
    unit = _require_unit(st, task_id, uid, api)
    current = unit.get("status")
    if current != "ready":
        raise TaskManagerError(
            "%s：单元 %s 当前状态为 %r 而非 ready，不能准备派发"
            % (api, uid, current))
    # permit 参数先于任何 I/O 副作用全量校验（§6.3/§6.6 冻结不变量；
    # wu-21-08 起内联逻辑抽为 _validate_permit_mode 与 wave 批量事务共享）
    effective_mode, permit_reason = _validate_permit_mode(api, st, mode,
                                                          reason)
    dispatch_block = st.get("dispatch")
    if not isinstance(dispatch_block, dict):
        dispatch_block = {}
    # wu-21-09 预算接线：cap_base（显式 > policy > legacy dispatch > 2）
    # × quota 四态折算（§12）→ eff；eff=0（EXHAUSTED）时传 1 进 plan，
    # 由 quota 闸自然全转 waiting_quota（plan 校验 1..4 不能收 0）
    eff = _effective_worker_cap(st, quota_status, max_workers)
    active = dispatch_block.get("active")
    if not isinstance(active, list):
        active = []
    # 租约事实交给决策器的租约闸（record dict 形状由 plan_dispatch 容错）
    leases = lease.lease_state(repo_root, task_id)
    plan = dispatcher.plan_dispatch(
        st.get("work_units"), max_workers=max(eff, 1), active=active,
        quota_status=quota_status, leases=leases)
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
    # 派发 permit（§6.3）：租约在位后签发——「无 permit 即 deny」门的
    # 数据基础；wave_id 缺省 None（M4 wave 事务接线）
    permit = dispatch_wave.create_permit(
        repo_root, task_id, uid, mode=effective_mode, reason=permit_reason)
    # leased 记持有事实（归一路径、排序、同 owner 幂等重入不虚报）
    held = lease.held_by(repo_root, task_id, uid)
    journal.append_event(repo_root, task_id, {
        "event": "dispatch_prepared", "unit": uid, "leased": held,
        "ttl_seconds": LEASE_DEFAULT_TTL_SECONDS,
        "effective_max_workers": eff})
    journal.append_event(repo_root, task_id, {
        "event": "dispatch_permit_created", "unit": uid,
        "permit_id": permit["permit_id"], "mode": effective_mode})
    plan["permit"] = permit
    return plan


# —— wave：批量 plan 决策 + 全量租约 + wave 记录 + 批量 permit ——

def prepare_dispatch_wave(repo_root, task_id, *, quota_status="AVAILABLE",
                          max_workers=None, mode=None, reason=None) -> dict:
    """批量派发准备（v2.1 M4 wu-21-08）：一次调用完成「决策 → 全量租约
    → wave 记录 → 批量 permit → journal」，事务性 all-or-safe-degrade
    ——wave 记录落盘时其全部成员租约已在位，绝不出现「wave 记录
    2 单元但只有 1 张租约」。

    流程：
      1. load_state（任务缺失 TaskManagerError；损坏 ValueError 上抛）；
      2. permit mode/reason 先于任何写副作用全量校验（与 prepare_dispatch
         共享 _validate_permit_mode，消息口径逐字一致）；
      3. max_workers 预算接线（wu-21-09，与 prepare_dispatch 共享
         _effective_worker_cap，见其 docstring）：cap_base 优先级「显式
         参数 → execution_policy.parallelism.max_workers（合法 1..4
         int）→ dispatch.max_workers（legacy）→ 2」，eff =
         min(cap_base, effective_worker_budget(policy, quota_status))
         ——AVAILABLE→策略 max_workers / PRESSURE→1 / UNKNOWN→1 /
         EXHAUSTED→0；eff 作为 max_workers 传给 plan_dispatch（eff=0
         时传 1，quota 闸自然全转 waiting_quota——dispatcher 不再整批
         挂起 UNKNOWN/PRESSURE）；
      4. 决策-租约循环（安全降级）：
           a. 候选 = 未被剔除的 work_units，plan_dispatch 全量决策（带
              落盘租约闸，纯决策器）；
           b. 批准集按 state.work_units 原序重排（plan 内部是 topo 序）；
           c. 批准集空 → TaskManagerError（消息口径照抄
              prepare_dispatch：waiting_quota → quota EXHAUSTED；
              deferred → 对应 reason；未进候选 → 依赖未满足），零租约
              残留；
           d. 批准集逐单元 acquire_lease（owner=uid，§78 全有或全无）；
              任一 LeaseConflictError → 释放本轮已获取的全部租约（零
              残留）、冲突单元加入 excluded 集合、回到 a 剔除后重试
              ——excluded 单调增长保证有界终止；
      5. 全部租约到位 → worker_budget = min(eff, len(批准集))（wu-21-09
         起 max_workers 已是 quota 调节后的 eff，worker_budget 自然是
         quota 调节后的值），wave_id = dispatch_wave.new_wave_id()，
         wave 记录（冻结键：wave_id/units/worker_budget/quota_status/
         created_at/status/closed_at，status="active"）追加进
         dispatch.waves，save_state 一次落盘（单元 wave 合法：只批
         1 个也成 wave）；
      6. 逐成员 create_permit（携带 wave_id 与 mode/reason）；
      7. journal 单条 dispatch_wave_prepared {wave_id, units,
         worker_budget, permits: [permit_id...]}——不逐单元重复
         dispatch_prepared；
      8. 返回 {"wave_id", "units", "permits", "worker_budget",
         "deferred", "waiting_quota"}（permits 为 permit dict 列表；
         deferred/waiting_quota 为最终决策的落选面透传）。

    wave 关闭不归本 API：成员全部终态/verifying 时由 finish_unit 收口
    （见 _close_finished_waves）；hook 侧经
    dispatch_wave.validate_wave_membership 校验成员资格。
    """
    api = "prepare_dispatch_wave"
    st = _require_state(repo_root, task_id, api)
    # mode/reason 校验先于任何写副作用（与「落选零副作用」同口径）
    effective_mode, permit_reason = _validate_permit_mode(api, st, mode,
                                                          reason)
    dispatch_block = st.get("dispatch")
    if not isinstance(dispatch_block, dict):
        dispatch_block = {}
    # wu-21-09 预算接线（与 prepare_dispatch 同口径，见
    # _effective_worker_cap）：eff=0（EXHAUSTED）时传 1 进 plan，
    # 由 quota 闸自然全转 waiting_quota
    eff = _effective_worker_cap(st, quota_status, max_workers)
    active = dispatch_block.get("active")
    if not isinstance(active, list):
        active = []
    work_units = st.get("work_units")
    if not isinstance(work_units, list):
        work_units = []
    # 决策-租约循环（安全降级）：冲突单元入 excluded，剔除后重 plan；
    # excluded 每轮至少新增一个 uid，单调增长保证有界终止
    excluded = set()
    while True:
        candidates = [
            unit for unit in work_units
            if isinstance(unit, dict) and isinstance(unit.get("id"), str)
            and unit.get("id") not in excluded]
        # 租约事实交给决策器的租约闸（§78 同 owner 幂等，自有租约不挡）
        leases = lease.lease_state(repo_root, task_id)
        plan = dispatcher.plan_dispatch(
            candidates, max_workers=max(eff, 1), active=active,
            quota_status=quota_status, leases=leases)
        by_id = {unit["id"]: unit for unit in candidates}
        approved_set = set(plan["dispatch"])
        # 批准集按 state.work_units 原序（plan 内部是 topo 序）
        approved = [unit["id"] for unit in candidates
                    if unit["id"] in approved_set]
        if not approved:
            if plan["waiting_quota"]:
                why = "quota EXHAUSTED（配额耗尽，决策建议转 waiting_quota）"
            elif plan["deferred"]:
                why = "挂起（deferred）：%s" % "；".join(
                    "%s（%s）" % (item.get("id"), item.get("reason"))
                    for item in plan["deferred"])
            else:
                why = ("未进入派发候选（依赖未全部 completed 或状态未提升"
                       "为 ready）")
            raise TaskManagerError(
                "%s：plan_dispatch 未批准任何单元（零租约残留）——%s"
                % (api, why))
        acquired = []
        conflicted = False
        for uid in approved:
            owned = by_id[uid].get("ownership")
            if not isinstance(owned, (list, tuple)):
                owned = []
            try:
                lease.acquire_lease(repo_root, task_id, uid, owned,
                                    ttl_seconds=LEASE_DEFAULT_TTL_SECONDS)
            except lease.LeaseConflictError:
                # 安全降级：释放本轮已获取的全部租约（零残留），冲突
                # 单元剔除后重 plan——绝不带残缺租约进 wave 记录
                for done in acquired:
                    lease.release_lease(repo_root, task_id, done)
                excluded.add(uid)
                conflicted = True
                break
            acquired.append(uid)
        if conflicted:
            continue
        break
    worker_budget = min(max(eff, 1), len(approved))
    wave_id = dispatch_wave.new_wave_id()
    wave = {
        "wave_id": wave_id,
        "units": list(approved),
        "worker_budget": worker_budget,
        "quota_status": quota_status,
        "created_at": _utc_now_iso(),
        "status": "active",
        "closed_at": None,
    }
    if not isinstance(st.get("dispatch"), dict):
        st["dispatch"] = {}
    waves = st["dispatch"].get("waves")
    if not isinstance(waves, list):
        waves = []
        st["dispatch"]["waves"] = waves
    waves.append(wave)
    # 一次落盘：此处起 wave.units 与在位租约一一对应（all-or-safe 界）
    state.save_state(repo_root, st)
    permits = [
        dispatch_wave.create_permit(
            repo_root, task_id, uid, wave_id=wave_id,
            mode=effective_mode, reason=permit_reason)
        for uid in approved]
    journal.append_event(repo_root, task_id, {
        "event": "dispatch_wave_prepared", "wave_id": wave_id,
        "units": list(approved), "worker_budget": worker_budget,
        "permits": [permit["permit_id"] for permit in permits]})
    return {
        "wave_id": wave_id,
        "units": list(approved),
        "permits": permits,
        "worker_budget": worker_budget,
        "deferred": plan["deferred"],
        "waiting_quota": plan["waiting_quota"],
    }


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
    _write_manifest_safe(repo_root, task_id)
    return st


# —— abort：prepare 之后、commit 之前的回退 ——

def abort_dispatch(repo_root, task_id, uid) -> dict:
    """回退未提交的派发准备：释放租约 + permit 失效 + dispatch_aborted
    事件；返回 state dict。

    流程：
      1. load_state；找不到 uid → TaskManagerError；
      2. 单元 status == "running" → TaskManagerError（已提交，不得静默
         回退——用 finish_unit 收尾或按 §69 走 reconcile）；
      3. lease.release_lease 全部释放该 owner 的租约；
      4. uid 残留在 dispatch.active（异常残留）→ 移除并 save_state；
         无残留则不落盘（abort 对 state.json 零写入是常态）；
      5. journal dispatch_aborted；
      6. 该单元全部未消费 permit invalidate（dispatch_wave.
         invalidate_permit，rename 为 .invalidated.json 保留审计轨迹），
         每张实际失效的 permit 落一条 dispatch_permit_invalidated
         （unit + permit_id；零失效不落事件——重复 abort / 无 permit
         的 abort 不产生噪声行）；
      7. 返回 state dict。

    不改单元状态：prepare 对 state 零副作用，abort 也不需要补偿单元
    状态——回退后单元仍是 ready，可重新 prepare（新 prepare 签发新
    permit，旧 permit 已 invalidated 不会复活）。
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
    # 未消费 permit 一并作废（§6.3）：先于返回值完成——回退后的任务
    # 目录里不得残留可被 hook 放行的活跃 permit
    for permit in dispatch_wave.list_permits(repo_root, task_id):
        if permit.get("unit_id") != uid:
            continue
        permit_id = permit.get("permit_id")
        if dispatch_wave.invalidate_permit(repo_root, task_id, permit_id):
            journal.append_event(repo_root, task_id, {
                "event": "dispatch_permit_invalidated", "unit": uid,
                "permit_id": permit_id})
    _write_manifest_safe(repo_root, task_id)
    return st


# —— finish：running/verifying → 终态 + 释放 + 单次 save ——

def _require_completion_evidence(repo_root, task_id, uid, unit, st) -> None:
    """RB-1 完成证据门（release hardening WU-P2）：completed 收尾前校验
    全部 required 验证命令的新鲜单元证据，不满足即 TaskManagerError。

    零副作用由调用顺序保证——本助手只在 finish_unit 的任何转换 /
    release / save / journal 之前调用，拒绝路径不写任何 journal 事件
    （含拒绝事件，计划 §2.3.4：不扩大事件面）。

    双根分离（RB-2）：git 求值根按任务绑定解析——
    git_root = state.resolve_repository_root(st, repo_root)（绑定
    repository.root 优先，legacy 回退账本根 repo_root）；touched 清单
    与证据指纹按 git_root 计算，而验证事件（events）**必须从账本根
    repo_root 注入**——fresh_unit_verification 缺省会从 git 根读
    journal，多仓场景（账本根 ≠ git 根）时会读错地方。

    - 复用 reconcile.fresh_unit_verification（WU-P1 共享证据谓词；
      与恢复对账同一口径）；空 required 单元经谓词快路径零 git 放行
      （生产单元受「无验证命令的单元不可派发」的 validate_state 约束
      不会出现该形状，此性质是谓词共用口径）；
    - evidence["ok"] 为 False（missing / stale / partial / wrong-unit /
      legacy 无 unit 字段证据）→ TaskManagerError，消息含 api 名、uid、
      missing 命令清单（逐条）与补证指引；
    - ownership.OwnershipError / fingerprint.FingerprintError → 包装为
      TaskManagerError（fail-closed：无法判定证据新鲜性即不能完成）。
    """
    from runtime import fingerprint  # 局部导入：不新增模块级 import
    api = "finish_unit"
    git_root = state.resolve_repository_root(st, repo_root)
    try:
        evidence = reconcile.fresh_unit_verification(
            git_root, task_id, unit,
            events=journal.read_events(repo_root, task_id))
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


def _close_finished_waves(st) -> "list[str]":
    """wave 收口检查（v2.1 M4 wu-21-08，finish_unit 专用内存变换）。

    对每个 active wave：若其 units 全部处于 WAVE_CLOSED_UNIT_STATUSES
    （completed/failed/cancelled/verifying）→ 就地置 status="closed" +
    closed_at=当前时刻，收集其 wave_id。返回本次关闭的 wave_id 列表
    （落盘与 journal 归调用方：save_state 已在 finish_unit 的单次落盘
    内，wave_closed 事件随其后的 journal 追加）。无 dispatch 块 / 无
    waves 键 / 无 active wave → 空列表零行为（legacy 任务零影响）。
    """
    dispatch_block = st.get("dispatch")
    if not isinstance(dispatch_block, dict):
        return []
    waves = dispatch_block.get("waves")
    if not isinstance(waves, list):
        return []
    statuses = {}
    units = st.get("work_units")
    if isinstance(units, list):
        for unit in units:
            if isinstance(unit, dict):
                statuses[unit.get("id")] = unit.get("status")
    closed = []
    for wave in waves:
        if not isinstance(wave, dict) or wave.get("status") != "active":
            continue
        members = wave.get("units")
        if not isinstance(members, list) or not members:
            continue
        if all(statuses.get(uid) in WAVE_CLOSED_UNIT_STATUSES
               for uid in members):
            wave["status"] = "closed"
            wave["closed_at"] = _utc_now_iso()
            closed.append(wave.get("wave_id"))
    return closed


def finish_unit(repo_root, task_id, uid, *, outcome="completed") -> dict:
    """单元收尾：RB-1 完成证据门（completed 时）→ 终态转换 → 释放租约
    → active 移除 → 单次 save → unit_finished 事件；返回 state dict。

    流程：
      1. outcome ∈ ("completed", "failed", "cancelled")，否则 ValueError
        （先于任何 I/O 校验，失败零副作用）；
      2. load_state；找不到 uid → TaskManagerError；
      3. 单元 status 须 ∈ ("running", "verifying")，否则 TaskManagerError
         （消息含当前状态）；
      4. 【RB-1 完成证据门，WU-P2；RB-2 双根分离】仅 outcome ==
         "completed" 时：git 求值根按任务绑定解析（绑定 repository.root
         优先，legacy 回退账本根），调 reconcile.fresh_unit_verification(
         git_root, task_id, unit, events=从账本根读取的事件)——全部
         required command 须各存在一条绑定当前指纹的新鲜 pass 证据
         （all-match），才允许进入转换；missing / stale / partial /
         wrong-unit / legacy 无 unit 字段证据一律 TaskManagerError
         （消息含 missing 清单与 record_unit_verification 补证指引），
         此时零副作用——state.json 字节、leases.json、dispatch.active、
         journal 全部不变；git / 指纹读取失败（OwnershipError /
         FingerprintError）同样拒绝（fail-closed 包装为
         TaskManagerError）。failed / cancelled 收尾不需要证据。空
         required 单元经谓词快路径零 git 直接放行；
      5. 转换：running + completed → 先 verifying 再 completed（§70 父
         验证语义：worker 报告只是 claim，编码为两次表内转换）；其余
         单步直达（running→failed/cancelled、verifying→completed/
         failed/cancelled，均在 §62 表内）；
      6. lease.release_lease + dispatch.active 移除 uid（在则删）；
         随后 wave 收口（wu-21-08）：active wave 全成员进入终态/
         verifying → 置 status="closed"+closed_at（无 waves 键零行为）；
      7. save_state 一次（单元转换与 wave 收口同一次落盘）；
      8. journal unit_finished（unit + outcome）+ 逐个 wave_closed
         （仅实际关闭的 wave）。

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
        # RB-1 完成证据门（WU-P2；RB-2 双根分离）：先验证据后转换——
        # 拒绝发生在任何变更之前（转换 / release / save / journal 均
        # 未发生）
        _require_completion_evidence(repo_root, task_id, uid, unit, st)
    if current == "running" and outcome == "completed":
        # §70：正常完成必须经 verifying（worker 报告只是 claim，父会话
        # 验证后才算 completed）——两次表内转换，不越表直跳
        work_unit.transition_work_unit(unit, "verifying")
    work_unit.transition_work_unit(unit, outcome)
    lease.release_lease(repo_root, task_id, uid)
    _remove_active(st, uid)
    # wave 收口（wu-21-08）：在 release+active 移除之后、save 之前检查
    # ——收尾使某 active wave 全成员进入终态/verifying 时关闭该 wave
    # （内存置 status/closed_at，随本函数唯一的 save_state 落盘）
    closed_waves = _close_finished_waves(st)
    state.save_state(repo_root, st)
    journal.append_event(repo_root, task_id, {
        "event": "unit_finished", "unit": uid, "outcome": outcome})
    for closed_wave_id in closed_waves:
        journal.append_event(repo_root, task_id, {
            "event": "wave_closed", "wave_id": closed_wave_id})
    _write_manifest_safe(repo_root, task_id)
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
