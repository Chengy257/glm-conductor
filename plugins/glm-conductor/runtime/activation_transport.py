#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GLM Conductor v2.2 Activation Transport 抽象（修正计划 §16/§17/§C6，
wu-22-C6 ①）。

职责（修正计划 §16「Activation Transport：新增正式抽象」的运行时落点）：
    Quota Watcher 能知道「quota available」，但不能保证「dormant session
    receives a new turn」——产生 Session execution opportunity 的职责单独
    抽象为 Activation Transport。本模块是该抽象的 v1 正式接口面：

      transport.arm(...)      → arm_transport（stable 委托执行面 arm
                                编排（已随 v2.3 执行面退役）；实验 kind
                                一律 TransportReservedError 绝不落
                                arm——预留即预留，不半实现）
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
      TransportReservedError 而非执行面异常）；
    - §22.5：本模块绝不制造 armed——armed 只来自显式 arm 记账路径
      （stable 委托）；观测面（scheduler 观测、对账）不在此；
    - 零网络、零宿主 Cron* 调用。

v2.4 Phase 2（W6，P2-F）退役面：stable 委托的执行面 arm 编排、
    armed 判定的可复用桥状态词汇与任务存在闸原语随 v2.3 执行面删除
    ——arm_transport 的 stable 路径与 activation_transport_status 的
    求值调用即 RuntimeError（v2.3 执行面已退役，Phase 3 删除本模块）。

依赖：
    runtime.execution_policy（延迟 import 面；execution_policy 的
    可选顶层键 activation_transport 是 transport 身份的事实源，容错
    缺省 recurring_bridge）。

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
    """容错取 (wake_bridge 视图, scheduler_context 视图)——原复用执行面
    的容错读原语（已随 v2.3 执行面退役），调用即 RuntimeError。"""
    raise RuntimeError(
        "v2.3 执行面已退役（continuation 容错读原语随执行运行时删除），"
        "Phase 3 删除本模块")


def activation_transport_status(repo_root, task_id) -> dict:
    """transport.status：activation transport 事实面（冻结返回键）。

    v2.4 Phase 2（W6）：求值依赖的任务存在闸原语与可复用桥状态词汇
    随 v2.3 执行面退役——调用即 RuntimeError（Phase 3 删除本模块）。
    """
    raise RuntimeError(
        "v2.3 执行面已退役（armed 判定与任务闸原语随执行运行时删除），"
        "Phase 3 删除本模块")


def arm_transport(repo_root, task_id, *, transport, automation_id,
                  boundary_id, reset_at, wake_at, next_wake_at=None,
                  bridge_interval_minutes=None, mode="recurring") -> dict:
    """transport.arm：stable 委托既有记账，实验 kind 一律预留拒绝。

    参数校验先于任何 I/O（失败零副作用）：
      1. transport 词汇闸：∈ TRANSPORT_KINDS，违例 ValueError（中文
         消息）——拼错的 kind 是调用方 bug，不是「预留」；
      2. 实验 kind 闸：∈ EXPERIMENTAL_TRANSPORTS → TransportReservedError
         （§C6：实验传输绝不落 arm——本闸先于任务存在性检查，缺任务 +
         实验 kind → TransportReservedError 而非执行面异常）；
      3. stable（recurring_bridge）→ 原为零改写委托执行面 arm 编排
         （§17：M5 主路径保留）——该编排已随 v2.3 执行面退役（W6），
         调用即 RuntimeError（Phase 3 删除本模块）。
    """
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
    raise RuntimeError(
        "v2.3 执行面已退役（arm 记账编排随执行运行时删除），"
        "Phase 3 删除本模块")
