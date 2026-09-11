#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GLM Conductor v2.2 Activation Transport 抽象（修正计划 §16/§17/§C6，
wu-22-C6 ①）。

职责（修正计划 §16「Activation Transport：新增正式抽象」的运行时落点）：
    Quota Watcher 能知道「quota available」，但不能保证「dormant session
    receives a new turn」——产生 Session execution opportunity 的职责单独
    抽象为 Activation Transport。本模块是该抽象的 v1 正式接口面：

      transport.arm(...)      → arm_transport（stable 委托
                                task_manager.arm_wake_bridge，零改写；
                                实验 kind 一律 TransportReservedError
                                绝不落 arm——预留即预留，不半实现）
      transport.status(...)   → activation_transport_status（冻结返回键；
                                **C8 Stop 门 activation transport armed
                                检查的接口面**——本单元不改 stop_gate.py）
      transport.wait_or_fire(...)  → **v1 不由 runtime 实现**：recurring
                                bridge 的触发面是宿主 Scheduled Task 的
                                到点触发（§16 四概念映射：wait_or_fire =
                                宿主 recurring 触发，runtime 零 CronList
                                零宿主探针，宿主动作归主会话）
      transport.consume_activation(...) → **归 C7 Resume 链**：激活消费
                                是 resume commit point 的事务（§15.1），
                                本模块不触碰

    Transport 不判断 task 是否允许 resume，只负责产生 execution
    opportunity 的事实面（§16 冻结职责边界）。

四词汇（修正计划 §C6 冻结；recurring_bridge 唯一 stable）：
    recurring_bridge   [STABLE]        persistent recurring automation +
                                       dynamic next_run_at retiming +
                                       native recurrence watchdog
                                      （§17 M5 主路径保留；v2.3.0 W3
                                       §10.1 语义重标——transport 名称
                                       不变、词汇表不扩：正常 wake 时刻
                                       由动态 next_run_at 决定，recurring
                                       网格退为 watchdog 兜底节拍）
    probe_then_hold    [EXPERIMENTAL]  §18 预留
    self_retiming      [FUTURE]        §20 预留
    session_injector   [EXPERIMENTAL]  §21 预留

纪律（本模块的硬边界）：
    - 参数校验先于任何 I/O：transport 词汇闸、实验 kind 的
      TransportReservedError 都发生在任何读盘之前（缺任务 + 实验 kind →
      TransportReservedError 而非 TaskManagerError）；
    - §22.5：本模块绝不制造 armed——armed 只来自既有 arm_wake_bridge
      显式路径（stable 委托）；观测面（scheduler 观测、对账）不在此；
    - runtime 根模块可 import task_manager（与 quota 包不 import
      state/task_manager 的纪律不冲突）；
    - 零网络、零宿主 Cron* 调用。

依赖：
    runtime.task_manager / runtime.execution_policy（延迟 import 面；
    execution_policy 的可选顶层键 activation_transport 是 transport
    身份的事实源，容错缺省 recurring_bridge）。

来源：
    v2.2 设计（已蒸馏入 docs/architecture.md：Activation Transport 抽象
    与 Transport A）+ 工作单元 wu-22-C6 ①。
"""

# —— 词汇表常量（§C6 冻结四值；与 execution_policy.ACTIVATION_
# TRANSPORTS 独立同序声明——反向 import 会成环，对齐由测试锚定） ——

TRANSPORT_KINDS = ("recurring_bridge", "probe_then_hold",
                   "self_retiming", "session_injector")

# 唯一 stable 传输（§17：M5 Persistent Recurring Bridge 主路径保留）
STABLE_TRANSPORT = "recurring_bridge"

# 实验预留传输（arm 一律拒绝——预留即预留，绝不半实现）
EXPERIMENTAL_TRANSPORTS = ("probe_then_hold", "self_retiming",
                           "session_injector")

# activation_transport_status 的冻结返回键（C8 接口面；形状即契约）
TRANSPORT_STATUS_KEYS = ("task_id", "transport", "stable", "armed",
                         "bridge_status", "next_wake_at",
                         "current_boundary_id", "automation_id",
                         "bridge_interval_minutes", "scheduler_origin",
                         "scheduler_create", "reasons")


class TransportReservedError(RuntimeError):
    """对实验预留 transport 调用 arm_transport（§C6：实验 kind 一律
    预留不实现——绝不落 arm、绝不半实现、绝不静默降级为 stable）。"""


def _continuation_views(st):
    """容错取 (wake_bridge 视图, scheduler_context 视图)（复用
    task_manager 的容错读原语，legacy 缺块按默认块兜底）。"""
    from runtime import task_manager
    bridge = task_manager._bridge_view(st)
    context = task_manager._scheduler_context_view(st)
    return bridge, context


def activation_transport_status(repo_root, task_id) -> dict:
    """transport.status：activation transport 事实面（冻结返回键）。

    返回恰含 TRANSPORT_STATUS_KEYS 十二键：
      - task_id：回显；
      - transport：execution_policy 顶层可选键 activation_transport 的
        容错读（缺 / 坏形状 → stable 默认 recurring_bridge，绝不抛）；
      - stable：transport == STABLE_TRANSPORT（bool）；
      - armed：bridge_status ∈ task_manager.REUSABLE_BRIDGE_STATUSES
        （armed / fired——WB-04 幂等认可的「活桥」族）；§22.5 语义：
        只认显式 arm 路径落下的记账，对账降级（cancelled / stale）
        即 missing；
      - bridge_status / next_wake_at / current_boundary_id /
        automation_id / bridge_interval_minutes：continuation.wake_bridge
        的容错视图键（legacy 缺块按默认块——status="none" 等）；
      - scheduler_origin / scheduler_create：continuation.scheduler_
        context 的 origin / create 容错视图（缺省 "unknown"）；
      - reasons：中文原因列表（armed 且 stable 时为空）——未武装 /
        实验 transport 的未满足项逐条点名，C8 / dogfood 面直接可读。

    任务缺失 → task_manager.TaskManagerError。纯读：零写、零事件、
    零宿主调用（本函数是 C8 Stop 门 armed 检查的接口面，只读事实）。
    """
    from runtime import execution_policy, task_manager
    st = task_manager._require_state(repo_root, task_id,
                                     "activation_transport_status")
    transport = execution_policy.activation_transport(
        st.get("execution_policy"))
    bridge, context = _continuation_views(st)
    bridge_status = bridge.get("status")
    reasons = []
    if bridge_status not in task_manager.REUSABLE_BRIDGE_STATUSES:
        reasons.append(
            "wake_bridge.status=%r 不在可复用桥状态（%s）——transport "
            "未武装（re-arm 只走 arm_wake_bridge 显式路径，§22.5）"
            % (bridge_status,
               "/".join(task_manager.REUSABLE_BRIDGE_STATUSES)))
    if transport != STABLE_TRANSPORT:
        reasons.append(
            "transport=%r 为实验预留传输（%s）——arm 一律 "
            "TransportReservedError，v1 无可武装实现"
            % (transport, "/".join(EXPERIMENTAL_TRANSPORTS)))
    return {
        "task_id": task_id,
        "transport": transport,
        "stable": transport == STABLE_TRANSPORT,
        "armed": bridge_status in task_manager.REUSABLE_BRIDGE_STATUSES,
        "bridge_status": bridge_status,
        "next_wake_at": bridge.get("next_wake_at"),
        "current_boundary_id": bridge.get("current_boundary_id"),
        "automation_id": bridge.get("automation_id"),
        "bridge_interval_minutes": bridge.get("bridge_interval_minutes"),
        "scheduler_origin": context.get("origin"),
        "scheduler_create": context.get("create"),
        "reasons": reasons,
    }


def arm_transport(repo_root, task_id, *, transport, automation_id,
                  boundary_id, reset_at, wake_at, next_wake_at=None,
                  bridge_interval_minutes=None, mode="recurring") -> dict:
    """transport.arm：stable 委托既有记账，实验 kind 一律预留拒绝。

    参数校验先于任何 I/O（失败零副作用）：
      1. transport 词汇闸：∈ TRANSPORT_KINDS，违例 ValueError（中文
         消息）——拼错的 kind 是调用方 bug，不是「预留」；
      2. 实验 kind 闸：∈ EXPERIMENTAL_TRANSPORTS → TransportReservedError
         （§C6：实验传输绝不落 arm——本闸先于任务存在性检查，缺任务 +
         实验 kind → TransportReservedError 而非 TaskManagerError）；
      3. stable（recurring_bridge）→ 零改写委托
         task_manager.arm_wake_bridge（§17：M5 主路径保留——记账语义、
         冲突语义、幂等语义、journal 事件全部由该 API 承担，本模块
         不复制不包装）。其 ValueError / TaskManagerError 自然上抛。

    其余参数（automation_id / boundary_id / reset_at / wake_at /
    next_wake_at / bridge_interval_minutes / mode）原样透传，校验归
    arm_wake_bridge（先于 I/O 的同款纪律）。
    """
    from runtime import task_manager
    if transport not in TRANSPORT_KINDS:
        raise ValueError(
            "arm_transport：transport %r 不在合法取值内（%s）"
            % (transport, ", ".join(TRANSPORT_KINDS)))
    if transport in EXPERIMENTAL_TRANSPORTS:
        raise TransportReservedError(
            "arm_transport：transport=%r 为实验预留传输（%s）——§C6 冻结："
            "实验 kind 绝不落 arm（v1 唯一 stable 传输是 %s；Phase0 通过前"
            "本闸恒拒，不半实现不静默降级）"
            % (transport, "/".join(EXPERIMENTAL_TRANSPORTS),
               STABLE_TRANSPORT))
    return task_manager.arm_wake_bridge(
        repo_root, task_id, automation_id=automation_id,
        boundary_id=boundary_id, reset_at=reset_at, wake_at=wake_at,
        next_wake_at=next_wake_at,
        bridge_interval_minutes=bridge_interval_minutes, mode=mode)
