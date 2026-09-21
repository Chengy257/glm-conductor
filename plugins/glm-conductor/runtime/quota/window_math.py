#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GLM Conductor v2.2.1 quota 窗口/边界数学单一规范落点（v2.2.1 WU-221-C2）。

职责：
    承载窗口边界确定性时间数学（reset 锚定 + grace 相加的纯函数）的
    单一规范落点（canonical home）。v2.2.1 WU-221-C2 行为保持抽取：
    _earliest_reset_plus 自 runtime.quota.control 逐字移入；v2.4
    Phase 3 P3-A 起 control / epoch 等控制面模块已删除，本函数保留
    为窗口边界时间数学的规范实现（当前包内存活消费方为 resolver /
    report 的调用链，历史解析点随宿主模块删除一并消亡）。本模块不
    新增任何策略语义。

    同域近亲（v2.2.1 逐实现比对后判定为「非重复、不合并」）：
      - parser.plan_resume 的 max(reset)+grace 内联段（原
        scheduler.plan_resume，P3-A 行为保持移入 parser）：§30 最晚
        多窗边界——§31 fail 语义不同（plan_resume 逐窗 fail-fast 且
        reason 带 kind 标注；_earliest_reset_plus 静默跳过不可解析
        窗），非逐字重复，不合并。

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
