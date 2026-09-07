#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GLM Conductor v2.2.1 quota 窗口/边界数学单一规范落点（v2.2.1 WU-221-C2）。

职责：
    承载窗口边界确定性时间数学（reset 锚定 + grace 相加的纯函数）的
    单一规范落点（canonical home）。v2.2.1 WU-221-C2 行为保持抽取：
    _earliest_reset_plus 自 runtime.quota.control 逐字移入；control
    经 re-import 保持既有解析点零变化（含 runtime.quota.epoch 既有的
    `from runtime.quota.control import _earliest_reset_plus`——该
    import 行不动，解析到的仍是同一函数对象，相关 docstring 记述
    保持为真）。本模块不新增任何策略语义。

    同域近亲（本单元逐实现比对后判定为「非重复、不合并」，各自
    留驻原模块）：
      - scheduler.plan_resume 的 max(reset)+grace 内联段与
        epoch._executable_boundary_of_canonical：executable 边界
        （§30 最晚多窗）的两处实现——§31 fail 语义不同（scheduler
        逐窗 fail-fast 且 reason 带 kind 标注；epoch 静默跳过不可
        解析窗），非逐字重复，不合并；
      - epoch._validated_grace / _normalize_reset：epoch 单模块
        使用，不满足「2+ 模块共用」门槛，留驻；
      - continuity.wake_bridge._bridge_boundary：boundary-id 构造
        （"<kind>:<reset_at>"，D10）——continuity.resume 经 import
        复用同一对象（单一规范落点已成立），无重复副本可抽取。

依赖：
    仅 Python 3.7 标准库 + runtime.quota.time_utils（时间原语）；
    导入方向 window_math → time_utils（time_utils 不反向依赖，
    无循环导入）。纯函数：零 I/O、零网络、不读时钟。
"""

from runtime.quota.time_utils import _format_iso_z, _parse_iso_utc


def _earliest_reset_plus(windows, grace_delta):
    """相关窗中最早可解析 reset_at 加 grace 的 Z 形式 ISO 串；无 → None。

    不可解析 / 缺失的 reset_at 逐窗跳过；全部不可解析 → None
    （不虚构唤醒时刻，纪律同 scheduler §31）。
    """
    moments = []
    for window in windows:
        moment = _parse_iso_utc(window.get("reset_at"))
        if moment is not None:
            moments.append(moment)
    if not moments:
        return None
    return _format_iso_z(min(moments) + grace_delta)
