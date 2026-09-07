#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GLM Conductor v2 Persistent Wake Bridge 规划/记账域（v2.2.1 WU-221-C2，行为保持抽取）。

职责：
    Persistent Wake Bridge（v2.2 M5，wu-22-05；决策记录 D15-a/b/c/d）
    的「规划 / 记账 / prompt 生成」规范定义落点：arm 裁决
    （plan_wake_bridge）、Universal Wake prompt（universal_wake_
    prompt）、武化 / 触发 / 重定目标记账（arm_wake_bridge /
    record_bridge_fired / retarget_wake_bridge）、§22.5 纯账本对账
    （reconcile_wake_bridge_from_host）、退役墓碑与连续性降级记账
    （write_completion_tombstone / degrade_continuity）及其私有
    助手（_ensure_continuation / _bridge_view / _scheduler_context_
    view / _bridge_boundary）。v2.2.1 WU-221-C2 自 runtime.task_
    manager 原文抽取（行为保持：同 journal 事件形状与写序、同幂等
    语义、同错误口径，函数体逐字未改，仅 import 适配）。本模块只
    产出文本与记账，绝不调用宿主 CronCreate / CronList 等（宿主
    动作归主会话）。

依赖方向（冻结，防循环）：
    task_manager → 本模块，绝不反向——本模块禁止 import
    runtime.task_manager；上层经 re-import 使既有 task_manager.<名字>
    解析点（activation_transport / cli / hooks / tests）全部解析到
    同一对象。与抽取域共用、原属 task_manager 的 TaskManagerError /
    _utc_now_iso / _require_state / _continuity_view 以
    runtime.quota.accounting（v2.2.1 WU-221-C1 规范落点）为家，经
    import 复用同一对象（单一规范落点，不复制）。

    仅标准库依赖（datetime）；其余为 runtime 一方模块（journal /
    state / execution_policy / quota.accounting；quota.scheduler /
    quota.control 保持函数内 import——monkeypatch 友好，与抽取前
    一致）。Python 3.7 兼容语法（仓库下限）。
"""

import datetime
import os

from runtime import journal
from runtime import state
from runtime.execution_policy import default_quota_control
from runtime.quota.accounting import (
    TaskManagerError,
    _continuity_view,
    _require_state,
    _utc_now_iso,
)

# v2.3.0 W3（§10）：Universal Wake prompt 内的 runtime 路径解析——
# 对齐 cli._CLOCK_RUNTIME_DIR 的 W2b 同款做法
# （os.path.dirname(os.path.abspath(__file__))，即
# …/plugins/glm-conductor/runtime），供 fire 后桥重定时步骤的
# wake-retime 命令行取绝对路径（prompt 纯函数，模块级常量零 I/O）。
_RUNTIME_DIR = os.path.dirname(os.path.abspath(__file__))

# —— Persistent Wake Bridge（v2.2 M5，wu-22-05；决策记录 D15-a/b/c/d） ——

# D15-a 拓扑冻结：one tracked task → prefer one persistent automation
# identity（跨 quota window 复用同一条 automation；废除 per-window
# chained wake）。本节全部是规划 / 记账 / prompt 生成——runtime 绝不
# 调用 CronCreate/CronDelete/CronUpdate（宿主动作归主会话，Phase 0 #13
# 的会话级禁令由 M6 hooks 记账）。

# wake plan 认可「任务已启动且未终态」的任务状态族（task_active 判定；
# 比 QUOTA_WAIT_TASK_STATUSES 宽——含 waiting_quota / waiting_user 恢复
# 面：用户重新授权后的 eager-arm 正落在 waiting_user 任务上）。created
# 等前置态与终态一律不裁决建桥。
WAKE_PLAN_TASK_STATUSES = ("executing", "joining", "verifying", "reviewing",
                           "waiting_quota", "waiting_user")

# WB-04 幂等认可的「活桥」状态——同一条持久 automation 身份仍在服役
# （fired 非终态：recurring bridge 触发后继续按间隔服役）
REUSABLE_BRIDGE_STATUSES = ("armed", "fired")

# v2.2 C1b（修正计划 §22.5）：reconcile_wake_bridge_from_host 的
# host_status 词汇——宿主事实由调用方显式传入，本层绝不调用 CronList /
# 任何宿主探针（真正的 host adapter 归 C6）。active = 当前宿主证据确认
# automation 在役；deleted = 当前宿主证据确认 automation 已删除；
# completed = 调用方已知 automation / 任务已完结；unknown = 无宿主事实
# （绝不能据此改动账本——§22.5：journal / history alone MUST NOT
# manufacture an active bridge）。
WAKE_BRIDGE_HOST_FACTS = ("active", "deleted", "completed", "unknown")

# bridge_interval_minutes 的缺省值镜像（单一真相源是
# execution_policy.DEFAULT_QUOTA_CONTROL["bridge_interval_minutes"]；
# 经 default_quota_control 键级合并取得，本常量仅供 docstring 引用）
# ——D15-d：保守默认 60 分钟，overlap 未验证前不收紧到 30。


def _ensure_continuation(st) -> dict:
    """容错取/建 continuation 块：legacy 缺块按 default_continuation()
    补齐并写回 st（完整形状过 validate_state 闸）；已存在原样返回。
    只改内存 dict，落盘归调用方的 save_state。"""
    block = st.get("continuation")
    if not isinstance(block, dict):
        block = state.default_continuation()
        st["continuation"] = block
    return block


def _bridge_view(st) -> dict:
    """容错读 continuation.wake_bridge（legacy 缺块按默认块兜底，
    非法形状同样兜底——纠错归 validate_state）。"""
    bridge = _ensure_continuation(st).get("wake_bridge")
    return bridge if isinstance(bridge, dict) \
        else state.default_continuation()["wake_bridge"]


def _scheduler_context_view(st) -> dict:
    """容错读 continuation.scheduler_context（缺省 unknown，D15-c 分层
    的缺省解释入口）。"""
    ctx = _ensure_continuation(st).get("scheduler_context")
    return ctx if isinstance(ctx, dict) \
        else state.default_continuation()["scheduler_context"]


def _bridge_boundary(windows, grace_seconds):
    """D10 boundary 数学（纯函数）：多窗取最早可解析 reset。

    - windows：§27 窗口清单（list of dict；非 list / 坏窗逐项跳过）；
    - 逐窗 _parse_iso_utc(reset_at)，取最早时刻（并列取先出现者）；
    - boundary_id = "<kind>:<reset_at>"——reset 用 Z 形式归一串（幂等
      比较稳定：provider 换拼写不影响 WB-04 的同 boundary 判定）；
      kind 缺失按 "unknown"；
    - wake_at = reset + grace_seconds 的 Z 形式串（DEFAULT_GRACE_
      SECONDS=300 同源 scheduler，D10）。
    无可解析窗 → (None, None, None)——绝不虚构（§31）。
    """
    from runtime.quota import scheduler  # 函数内 import：monkeypatch 友好
    best = None  # (moment, kind)
    for item in (windows if isinstance(windows, list) else []):
        if not isinstance(item, dict):
            continue
        moment = scheduler._parse_iso_utc(item.get("reset_at"))
        if moment is None:
            continue
        if best is None or moment < best[0]:
            best = (moment, item.get("kind"))
    if best is None:
        return None, None, None
    moment, kind = best
    reset_z = scheduler._format_iso_z(moment)
    kind_text = kind if isinstance(kind, str) and kind != "" else "unknown"
    delta = datetime.timedelta(seconds=float(grace_seconds))
    return ("%s:%s" % (kind_text, reset_z), reset_z,
            scheduler._format_iso_z(moment + delta))


def universal_wake_prompt(task_id, *, ledger_root, repository_root=None,
                          boundary_id=None, reset_at=None, wake_at=None,
                          mode="recurring",
                          bridge_interval_minutes=None,
                          automation_id=None) -> str:
    """生成 Universal Wake prompt（§10 统一模板 + 今晚活桥逐字硬红线
    风格；纯函数，零 I/O 零凭证）。

    输入 task_id + 边界参数 → 文本；模板含 force-refresh 强刷、v2.3 W3
    的 fire 后桥重定时步骤（wake-retime，§10.3/§10.4：可执行 → park
    本桥；不可执行 → 下一边界或 5 分钟重试——本步骤只管节拍，resume
    仍由下方 quota-resume 步骤按授权进行）、三分支决策（goal 已达成 →
    tombstone + 单次清理；manual/notify → warm-only；auto 家族 →
    quota-resume 续作）、额度不可执行 no-op（不消费窗口预算，D15-g）、
    同桥复用语义（只复用/retarget 本 bridge，绝不新建）与硬红线
    （严禁创建任何新的 Scheduled Task / 零凭证 / 单次清理绝不重试）。

    主会话把它放进 automation 的 prompt 字段即可；automation 的创建 /
    暂停 / 删除本身归主会话的宿主工具（runtime 只产出文本与记账）。
    """
    work_root = repository_root if repository_root else ledger_root
    interval_text = ("%d 分钟" % bridge_interval_minutes
                     if isinstance(bridge_interval_minutes, int)
                     and not isinstance(bridge_interval_minutes, bool)
                     else "未配置")
    lines = [
        "GLM CONDUCTOR QUOTA WINDOW WAKE",
        "",
        "TASK_ID: %s" % task_id,
        "LEDGER_ROOT: %s" % ledger_root,
        "REPOSITORY_ROOT: %s" % work_root,
        "BOUNDARY_ID: %s" % (boundary_id if boundary_id else "unknown"),
        "EXPECTED_RESET_AT: %s" % (reset_at if reset_at else "unknown"),
        "WAKE_AT: %s" % (wake_at if wake_at else "按固定间隔触发"),
        "BRIDGE: %s（间隔 %s；复用同一 automation 身份%s，绝不新建）"
        % (mode, interval_text,
           " %s" % automation_id if automation_id else ""),
        "",
        "硬红线：本会话属于 scheduled task，严禁创建任何新的 Scheduled "
        "Task（宿主会拒绝且污染状态）；只允许复用本 bridge。本 prompt "
        "不含任何凭证。",
        "",
        "1. cd %s 后执行：" % ledger_root,
        "   python3 plugins/glm-conductor/runtime/cli.py quota-resolve "
        ". --force-refresh",
        "2. 紧随强刷、任何分叉之前，立即重定时本桥（一步完成「判定 + "
        "retime」——v2.3 起正常 wake 时刻由动态 next_run_at 决定，不再"
        "等 60 分钟 watchdog 网格）：",
        "   python3 %s/cli.py wake-retime %s %s"
        % (_RUNTIME_DIR, ledger_root, task_id),
        "   （额度可执行 → 停摆本桥至一年后，任务下次休眠再走既有 arm "
        "流程重新建置；仍不可执行 → 重排到下一额度边界，无已知边界则 "
        "5 分钟后重试）",
        "3. 读 .glm-conductor/tasks/%s/state.json 与 manifest.json；"
        % task_id,
        "   以仓库为真相源（repository > checkpoint > 记忆）",
        "4. 任务 goal 已达成（全部单元 completed）？",
        "   → 写 continuation.tombstone（task_id/status=completed/"
        "completed_at/bridge_should_noop=true）",
        "   → 单次尝试 CronDelete 本 automation（绝不重试，失败即容忍）",
        "   → 简短汇报后结束（不实施任何工作）",
        "5. 额度不可执行（仍耗尽 / weekly 阻塞 / provider 拒绝）？",
        "   → 什么都不做，简短说明后结束本轮（不消费窗口预算——D15-g）",
        "6. auto_resume=manual/notify（任务活跃且额度可执行）？",
        "   → warm-only：刷新额度状态、保持任务可恢复，绝不实施新工作，",
        "     汇报后结束",
        "7. 额度可执行 且 execution_policy.continuity.auto_resume ∈ "
        "{auto_once, until_done} 且 authorization.source=user？",
        "   → python3 plugins/glm-conductor/runtime/cli.py quota-resume "
        ". %s" % task_id,
        "   → 对账 reconcile（running 中断单元四分：clean→ready / "
        "result→verifying / progress→ready / manual_ruling→保持，"
        "勿盲目重派）",
        "   → 按依赖续派下一安全单元（不重放已完成工作）",
        "   → journal 记 wake_bridge_fired",
        "8. consumed_quota_windows 已达 max_quota_windows？",
        "   → 转 waiting_user，告知用户后结束",
        "9. 本轮一切 git 提交遵守：record（验证收据）与 finish_unit "
        "之间零提交",
        "",
        "恒久保护（Always preserve）：ownership / verification / "
        "review / lease / permit / reconcile / quota-window budget。",
        "不得为同一 reset boundary 创建第二座 wake——除非旧桥已确认"
        "失效；只复用或 retarget 本 bridge（reuse or retarget the "
        "existing bridge only）。",
        "",
        "HARD RED LINE (Phase 0 #13 / D15): DO NOT CREATE A NEW "
        "SCHEDULED TASK FROM THIS WAKE SESSION.",
    ]
    return "\n".join(lines)


def plan_wake_bridge(repo_root, task_id, *, provider_status=None,
                     windows=None, long_horizon=False,
                     cross_window_predicted=False,
                     grace_seconds=None) -> dict:
    """Persistent Wake Bridge 的 arm 裁决（v2.2 M5，wu-22-05；§7.2 /
    §22.2 / D15-a/b/c，纯决策——零写副作用、零 journal、零网络、绝不
    创建 automation）。

    入参形状（冻结）：
      - repo_root / task_id：账本根 + 任务 id——本函数只读
        state.json（缺失 TaskManagerError；损坏 ValueError 上抛）；
      - provider_status：provider 四态字符串；None → "UNKNOWN" 保守
        （绝不默认 AVAILABLE；与 M4 闸同源——调用方喂本地 quota-cache
        的 status，词汇校验归 control）；
      - windows：§27 窗口清单（调用方喂缓存 snapshot.windows）；None /
        缺失按空窗口 fail-open（control 收敛 PRESSURE、不虚构时刻）；
      - long_horizon / cross_window_predicted：D15-c auto_once 风险
        触发器中 runtime 无法机械观测的两项（long-horizon 任务 / 运行
        时预测当前窗口无法安全完成任务），由调用方显式声明，缺省
        False——M5 的机械触发面是 PRESSURE 相与 scheduler_context；
      - grace_seconds：wake_at 宽限秒数，None → DEFAULT_GRACE_SECONDS
        （300，scheduler 单一真相源，D10）。

    裁决（按序短路；reason 为中文一句话）：
      0. 任务 status ∉ WAKE_PLAN_TASK_STATUSES → required=False；
      1. auto 家族窗口预算耗尽（remaining <= 0）→ required=False
         （§14.5：不得再创建任何自动化唤醒，转 waiting_user）；
      2. WB-04 幂等：bridge.status ∈ ("armed","fired") → required=
         False——同 boundary 复用同桥；boundary 已推进则在 reason 注明
         「调用 retarget 记账（不新建 automation）」；
      3. scheduler_context.create=forbidden 且无 reusable bridge →
         required=False（Phase 0 #13；调用方走 degrade_continuity，
         reason=scheduler_create_forbidden）；
      4. D15-c 分层（create=forbidden 已被规则 3 拦下）：
         a. until_done + authorization.source=user → required=True、
            eager=True（MUST eager-arm：授权完成即建，不等 PRESSURE，
            无相位条件——BLOCKED 相的 interactive 会话救援建桥同此）；
            until_done + source != user → required=False（未授权）；
         b. BLOCKED → required=False（§7.2：bridge 必须早已 armed，
            不得依赖现场再建；未建走 degrade_continuity）；
         c. auto_once + source=user：DRAINING + create=allowed →
            MUST（required=True eager=False）；DRAINING + create=
            unknown → SHOULD（能力未探明保守不升 MUST）；任一风险
            触发（PRESSURE 相 / cross_window_predicted / long_horizon /
            会话已关联 Scheduled Task——scheduler_context.
            parent_automation_id 在位）→ SHOULD（required=True
            eager=False）；否则 False；source != user → False；
         d. manual/notify：DRAINING + create=allowed → MUST；
            DRAINING + create=unknown → SHOULD（能力未探明保守不升
            MUST）；PRESSURE → SHOULD；否则 False；
         e. 其余（NORMAL 无触发）→ required=False。
      5. required=True 时 prompt=universal_wake_prompt(...)（§10），
         否则 prompt=None。

    boundary 数学（D10）：多窗取最早可解析 reset，boundary_id =
    "<kind>:<reset_at>"（Z 形式归一）；wake_at = reset + 300 秒宽限；
    无可解析窗 → boundary_id / wake_at 双 None（不虚构）。mode 恒
    "recurring"（D15-b 主路径；self_retiming 仅 schema 常量占位）。
    bridge_interval_minutes 取 execution_policy.quota_control 键级
    合并（default_quota_control，默认 60）。

    返回（§22.2 冻结九键，键名逐字）：{"required", "mode",
    "boundary_id", "current_boundary_id", "wake_at",
    "bridge_interval_minutes", "eager", "prompt", "reason"}。

    时序（与宿主动作的分工）：本函数裁决 → 主会话宿主 CronCreate
    （runtime 绝不调用）→ arm_wake_bridge 记账即止——arm/fire/create
    一律不消费窗口预算（C1a，D15-g：automation lifecycle ≠ quota
    epoch consumption；消费点 = resume commit point §15.1，C1b 落地）。
    """
    api = "plan_wake_bridge"
    st = _require_state(repo_root, task_id, api)
    from runtime.quota import control, scheduler  # 函数内 import：monkeypatch 友好
    grace = (scheduler.DEFAULT_GRACE_SECONDS if grace_seconds is None
             else grace_seconds)
    if not scheduler._is_number(grace) or grace < 0:
        raise ValueError(
            "%s：grace_seconds 必须是 >= 0 的有限数值或 None，得到 %r"
            % (api, grace_seconds))
    bridge = _bridge_view(st)
    ctx = _scheduler_context_view(st)
    view = _continuity_view(st)
    policy = st.get("execution_policy")
    interval = default_quota_control(policy)["bridge_interval_minutes"]
    create = ctx.get("create")
    parent_automation = ctx.get("parent_automation_id")
    bridge_status = (bridge.get("status")
                     if bridge.get("status") in state.WAKE_BRIDGE_STATUSES
                     else "none")
    reusable = bridge_status in REUSABLE_BRIDGE_STATUSES
    boundary_id, reset_z, wake_at = _bridge_boundary(windows, grace)
    current_boundary_id = (bridge.get("current_boundary_id")
                           if isinstance(bridge.get("current_boundary_id"),
                                         str)
                           and bridge.get("current_boundary_id") != ""
                           else None)
    mode = "recurring"  # D15-b：主路径恒 recurring（D15-a 冻结拓扑）

    # execution phase（与 M4 闸同源的 control 决策器；provider 缺省
    # UNKNOWN 保守，绝不默认 AVAILABLE）
    phase_decision = control.evaluate_task_quota_phase(
        provider_status=(provider_status if provider_status is not None
                         else "UNKNOWN"),
        windows=windows,
        quota_control=default_quota_control(policy),
        max_workers=None, task_active=True,
        wake_bridge_status=bridge_status)
    phase = phase_decision["execution_phase"]

    def _plan(required, eager, reason):
        """组装 §22.2 冻结九键输出（prompt 仅在 required=True 时生成）。"""
        prompt = None
        if required:
            prompt = universal_wake_prompt(
                task_id, ledger_root=str(repo_root),
                repository_root=state.resolve_repository_root(
                    st, str(repo_root)),
                boundary_id=boundary_id, reset_at=reset_z, wake_at=wake_at,
                mode=mode, bridge_interval_minutes=interval,
                automation_id=bridge.get("automation_id"))
        return {
            "required": required,
            "mode": mode,
            "boundary_id": boundary_id,
            "current_boundary_id": current_boundary_id,
            "wake_at": wake_at,
            "bridge_interval_minutes": interval,
            "eager": eager,
            "prompt": prompt,
            "reason": reason,
        }

    # —— 规则 0：任务状态闸 ——
    if st.get("status") not in WAKE_PLAN_TASK_STATUSES:
        return _plan(
            False, False,
            "任务状态为 %r，不在 wake plan 的存活状态族（%s）内——不裁决"
            "建桥" % (st.get("status"),
                      ", ".join(WAKE_PLAN_TASK_STATUSES)))

    # —— 规则 1：auto 家族窗口预算闸（§14.5） ——
    if view["auto_resume"] in ("auto_once", "until_done") \
            and view["remaining"] <= 0:
        return _plan(
            False, False,
            "auto_resume=%r 的窗口预算已耗尽（consumed %d / max %d）——"
            "不得再创建任何自动化唤醒，转 waiting_user 等待用户重新授权"
            "（§14.5）" % (view["auto_resume"],
                           view["consumed_quota_windows"],
                           view["max_quota_windows"]))

    # —— 规则 2：WB-04 幂等（活桥复用 / retarget 记账，不新建） ——
    if reusable:
        if boundary_id is not None and current_boundary_id is not None \
                and boundary_id != current_boundary_id:
            return _plan(
                False, False,
                "wake bridge 已 armed（automation_id=%s）而 boundary 已"
                "推进（current=%s → next=%s）——recurring 同桥按间隔继续"
                "触发；调用 retarget_wake_bridge 记账更新 "
                "current_boundary_id，绝不新建 automation（WB-04 / "
                "D15-a）" % (bridge.get("automation_id"),
                             current_boundary_id, boundary_id))
        return _plan(
            False, False,
            "wake bridge 已 armed 且 boundary 未变（%s）——WB-04 幂等"
            "复用同桥（one tracked task → one persistent automation "
            "identity，D15-a），不新建" % (current_boundary_id or "未知"))

    # —— 规则 3：create 能力机械阻断（Phase 0 #13） ——
    if create == "forbidden":
        return _plan(
            False, False,
            "scheduler_context.create=forbidden 且无 reusable bridge"
            "（Phase 0 #13：本会话不得再创建 Scheduled Task）——调用 "
            "degrade_continuity 记账（reason=scheduler_create_forbidden），"
            "不裁决建桥")

    # —— 规则 4：D15-c 分层 ——
    auto_resume = view["auto_resume"]
    if auto_resume == "until_done" and view["source"] == "user":
        return _plan(
            True, True,
            "auto_resume=until_done 且 authorization.source=user——"
            "MUST eager-arm（授权完成即建 bridge，不等 PRESSURE；"
            "D15-c / §7.2）")
    if auto_resume == "until_done":
        return _plan(
            False, False,
            "auto_resume=until_done 未获得用户授权（authorization."
            "source != \"user\"），不自动建桥（与 _quota_wake_decision "
            "口径一致）")
    # —— 规则 4.5：BLOCKED 相不现场建桥（§7.2：bridge 必须早已 armed，
    # 不得依赖现场再建）——until_done 已在上方按无相位条件的 MUST
    # eager-arm 先行裁决（授权完成即建），此处覆盖 auto_once 与
    # manual/notify
    if phase == "BLOCKED":
        return _plan(
            False, False,
            "execution phase BLOCKED——bridge 必须早已 armed（不得依赖"
            "现场再建，§7.2）；未建则调用 degrade_continuity 记账降级，"
            "不无限阻塞")
    if auto_resume == "auto_once":
        if view["source"] != "user":
            return _plan(
                False, False,
                "auto_resume=auto_once 未获得用户授权（authorization."
                "source != \"user\"），不自动建桥（与 _quota_wake_decision"
                " 口径一致）")
        if phase == "DRAINING":
            if create == "allowed":
                return _plan(
                    True, False,
                    "auto_resume=auto_once 且 DRAINING 且 scheduler "
                    "create=allowed——MUST arm（D15-c / §7.2）")
            return _plan(
                True, False,
                "auto_resume=auto_once 且 DRAINING（create 能力未探明）"
                "——SHOULD arm（保守不升 MUST，arm 失败由 "
                "wake_bridge_failed 记账兜底）")
        triggers = []
        if phase == "PRESSURE":
            triggers.append("PRESSURE 相（执行压力带）")
        if cross_window_predicted:
            triggers.append("运行时预测当前窗口无法安全完成任务（跨窗）")
        if long_horizon:
            triggers.append("long-horizon 任务")
        if isinstance(parent_automation, str) and parent_automation != "":
            triggers.append("会话已关联 Scheduled Task（"
                            "parent_automation_id 在位）")
        if triggers:
            return _plan(
                True, False,
                "auto_resume=auto_once 风险触发（%s）——SHOULD arm"
                "（D15-c risk-triggered eager-arm）" % "；".join(triggers))
        return _plan(
            False, False,
            "auto_resume=auto_once 且无风险触发（PRESSURE / 跨窗预测 / "
            "long-horizon / 会话已关联 Scheduled Task 均未命中）——暂不"
            "建桥（D15-c）")
    if auto_resume in ("manual", "notify"):
        if phase == "DRAINING":
            if create == "allowed":
                return _plan(
                    True, False,
                    "auto_resume=%s 且 DRAINING 且 scheduler "
                    "create=allowed——MUST arm（mechanically possible，"
                    "D15-c / §7.2）" % auto_resume)
            return _plan(
                True, False,
                "auto_resume=%s 且 DRAINING（create 能力未探明）——SHOULD "
                "arm（保守不升 MUST）" % auto_resume)
        if phase == "PRESSURE":
            return _plan(
                True, False,
                "auto_resume=%s 且 PRESSURE——SHOULD arm（D15-c；触发后"
                "仅 warm-only，绑定的是任务跨 boundary 的可达性，§7.3）"
                % auto_resume)
        return _plan(
            False, False,
            "auto_resume=%s 且 execution phase %s 无建桥触发——暂不建桥"
            "（manual/notify 仅 PRESSURE→SHOULD / DRAINING+create="
            "allowed→MUST 建桥，D15-c）" % (auto_resume, phase))
    # 兜底（理论不可达：_continuity_view 把 auto_resume 归一为四值
    # 词汇，上面已全部覆盖；防御保留位）
    return _plan(
        False, False,
        "auto_resume=%r 不在授权矩阵动作面内且 execution phase %s 无"
        "触发——暂不建桥" % (auto_resume, phase))


def arm_wake_bridge(repo_root, task_id, *, automation_id, boundary_id,
                    reset_at, wake_at, next_wake_at=None,
                    bridge_interval_minutes=None, mode="recurring") -> dict:
    """bridge 武化记账（requested→armed；主会话宿主 CronCreate **成功
    后**调用，v2.2 M5 / D15-a / §9 生命周期）。

    时序（冻结）：plan_wake_bridge 裁决（required=True）→ 主会话宿主
    CronCreate（宿主动作，runtime 绝不调用）→ 本函数记账即止——
    arm/fire/create 一律不消费窗口预算（C1a，D15-g：automation
    lifecycle ≠ quota epoch consumption；消费点 = resume commit
    point §15.1，C1b 落地）。

    写入（continuation.wake_bridge 十字段 + obligation）：
      status="armed"、boundary_id / current_boundary_id=boundary_id、
      automation_id、reset_at / wake_at、armed_at=当前时刻、
      next_wake_at、mode、generation=0（recurring 恒 0，D15-b）、
      bridge_interval_minutes；continuation.obligation="armed"。

    冲突语义（先于任何 I/O 校验参数，失败零副作用）：
      - 已有活桥（status ∈ REUSABLE_BRIDGE_STATUSES）且同 automation_id
        同 boundary_id → 幂等 no-op（返回既有记账 + "idempotent": True，
        零写零事件）；
      - 已有活桥且同 boundary 不同 automation_id → TaskManagerError
        （D15-a：one tracked task → one persistent automation identity，
        拒绝第二 automation 身份）；
      - 已有活桥且 boundary 不同 → TaskManagerError（用
        retarget_wake_bridge 记账，不新建 automation，WB-04）。

    单次 save_state + journal wake_bridge_armed {automation_id,
    boundary_id, mode, bridge_interval_minutes, next_wake_at, wake_at,
    armed_at, recurring}。返回记账后的 bridge 关键字段 dict。

    参数校验：automation_id / boundary_id / reset_at / wake_at 必须是
    非空 str（reset_at / wake_at 须可解析 ISO8601），next_wake_at 为
    None 或可解析 ISO8601，bridge_interval_minutes 为 None 或 5-1440
    int（bool 拒绝），mode ∈ state.WAKE_BRIDGE_MODES——违例 ValueError
    （中文消息）。任务缺失 TaskManagerError。
    """
    api = "arm_wake_bridge"
    if not isinstance(automation_id, str) or automation_id == "":
        raise ValueError(
            "%s：automation_id 必须是非空字符串，得到 %r"
            % (api, automation_id))
    if not isinstance(boundary_id, str) or boundary_id == "":
        raise ValueError(
            "%s：boundary_id 必须是非空字符串（<kind>:<reset_at>，D10），"
            "得到 %r" % (api, boundary_id))
    from runtime.quota import scheduler  # 函数内 import：monkeypatch 友好
    if scheduler._parse_iso_utc(reset_at) is None:
        raise ValueError(
            "%s：reset_at 必须是可解析的 ISO8601 字符串，得到 %r"
            % (api, reset_at))
    if scheduler._parse_iso_utc(wake_at) is None:
        raise ValueError(
            "%s：wake_at 必须是可解析的 ISO8601 字符串，得到 %r"
            % (api, wake_at))
    if next_wake_at is not None \
            and scheduler._parse_iso_utc(next_wake_at) is None:
        raise ValueError(
            "%s：next_wake_at 必须是 None 或可解析的 ISO8601 字符串，"
            "得到 %r" % (api, next_wake_at))
    if bridge_interval_minutes is not None \
            and (isinstance(bridge_interval_minutes, bool)
                 or not isinstance(bridge_interval_minutes, int)
                 or not 5 <= bridge_interval_minutes <= 1440):
        raise ValueError(
            "%s：bridge_interval_minutes 必须是 None 或 5-1440 的整数，"
            "得到 %r" % (api, bridge_interval_minutes))
    if mode not in state.WAKE_BRIDGE_MODES:
        raise ValueError(
            "%s：mode %r 不在合法取值内（%s）"
            % (api, mode, ", ".join(state.WAKE_BRIDGE_MODES)))
    st = _require_state(repo_root, task_id, api)
    continuation = _ensure_continuation(st)
    bridge = continuation.get("wake_bridge")
    if not isinstance(bridge, dict):
        bridge = state.default_continuation()["wake_bridge"]
        continuation["wake_bridge"] = bridge
    if bridge.get("status") in REUSABLE_BRIDGE_STATUSES:
        if bridge.get("automation_id") == automation_id \
                and bridge.get("current_boundary_id") == boundary_id:
            result = {key: bridge.get(key) for key in
                      ("automation_id", "boundary_id", "current_boundary_id",
                       "reset_at", "wake_at", "next_wake_at", "mode",
                       "generation", "bridge_interval_minutes", "status",
                       "armed_at")}
            result["idempotent"] = True
            return result
        if bridge.get("current_boundary_id") == boundary_id:
            raise TaskManagerError(
                "%s：任务 %s 已有活桥指向同一 boundary（%s）但 automation "
                "身份不同（在位 %s，传入 %s）——D15-a：one tracked task → "
                "one persistent automation identity，拒绝第二 automation "
                "身份" % (api, task_id, boundary_id,
                          bridge.get("automation_id"), automation_id))
        raise TaskManagerError(
            "%s：任务 %s 已有活桥（automation_id=%s，current_boundary_id="
            "%s）指向不同 boundary（传入 %s）——用 retarget_wake_bridge "
            "记账更新 current_boundary_id，绝不新建 automation（WB-04）"
            % (api, task_id, bridge.get("automation_id"),
               bridge.get("current_boundary_id"), boundary_id))
    armed_at = _utc_now_iso()
    bridge["status"] = "armed"
    bridge["boundary_id"] = boundary_id
    bridge["current_boundary_id"] = boundary_id
    bridge["automation_id"] = automation_id
    bridge["reset_at"] = reset_at
    bridge["wake_at"] = wake_at
    bridge["armed_at"] = armed_at
    bridge["next_wake_at"] = next_wake_at
    bridge["mode"] = mode
    bridge["generation"] = 0  # recurring 恒 0（D15-b；世代推进非 M5 范围）
    bridge["bridge_interval_minutes"] = bridge_interval_minutes
    continuation["obligation"] = "armed"
    continuation["reason"] = "wake bridge armed（automation_id=%s）" \
        % automation_id
    state.save_state(repo_root, st)
    journal.append_event(repo_root, task_id, {
        "event": "wake_bridge_armed", "automation_id": automation_id,
        "boundary_id": boundary_id, "mode": mode,
        "bridge_interval_minutes": bridge_interval_minutes,
        "next_wake_at": next_wake_at, "wake_at": wake_at,
        "armed_at": armed_at, "recurring": mode == "recurring"})
    return {key: bridge.get(key) for key in
            ("automation_id", "boundary_id", "current_boundary_id",
             "reset_at", "wake_at", "next_wake_at", "mode", "generation",
             "bridge_interval_minutes", "status", "armed_at")}


def record_bridge_fired(repo_root, task_id, *, fired_at=None) -> dict:
    """bridge 触发记账（armed→fired；recurring 重复触发合法——每次触发
    各落一条事件并刷新 fired_at，v2.2 M5 / §9 生命周期）。

    - bridge.status 须 ∈ ("armed","fired")，否则 TaskManagerError
      （无桥 / 已取消 / 已失效的触发记录被拒）；
    - fired_at：None → 当前时刻（ISO8601）；显式传入须可解析；
    - 写 wake_bridge.status="fired" + fired_at（recurring 桥身份继续
      服役，不改 automation 记账）；obligation 不动（恢复裁决归
      resume controller，M8）；
    - 单次 save_state + journal wake_bridge_fired {automation_id,
      fired_at, boundary_id}。返回 bridge 关键字段 dict。
    """
    api = "record_bridge_fired"
    from runtime.quota import scheduler  # 函数内 import：monkeypatch 友好
    if fired_at is not None and scheduler._parse_iso_utc(fired_at) is None:
        raise ValueError(
            "%s：fired_at 必须是 None 或可解析的 ISO8601 字符串，得到 %r"
            % (api, fired_at))
    st = _require_state(repo_root, task_id, api)
    bridge = _bridge_view(st)
    if bridge.get("status") not in REUSABLE_BRIDGE_STATUSES:
        raise TaskManagerError(
            "%s：任务 %s 的 wake_bridge.status 为 %r，须为 armed（或 "
            "fired——recurring 重复触发合法）才能记录触发"
            % (api, task_id, bridge.get("status")))
    moment = fired_at if fired_at is not None else _utc_now_iso()
    bridge["status"] = "fired"
    bridge["fired_at"] = moment
    state.save_state(repo_root, st)
    journal.append_event(repo_root, task_id, {
        "event": "wake_bridge_fired", "automation_id":
            bridge.get("automation_id"), "fired_at": moment,
        "boundary_id": bridge.get("current_boundary_id")})
    return {key: bridge.get(key) for key in
            ("automation_id", "status", "fired_at", "boundary_id",
             "mode", "generation")}


def retarget_wake_bridge(repo_root, task_id, *, boundary_id,
                         reset_at=None, wake_at=None,
                         next_wake_at=None) -> dict:
    """bridge 重定目标记账（boundary 变更 / 时刻重排；v2.2 M5 / WB-04：
    只更新同一条 automation 的记账，绝不新建）。

    - bridge.status 须 ∈ ("armed","fired")（无活桥不可 retarget）；
    - boundary_id 更新的是 current_boundary_id（当前服役 boundary）；
      既有 boundary_id（armed 时的原始目标）保留不动；
    - reset_at / wake_at / next_wake_at：None = 不更新；显式传入须
      可解析 ISO8601；
    - generation 不增不减（recurring 模式恒 0，D15-b；self_retiming
      的世代推进不在 M5 范围）；
    - 幂等：boundary 未变且三个时刻参数全 None → 零写零事件，返回
      既有记账 + "idempotent": True；
    - 单次 save_state + journal wake_bridge_retargeted {automation_id,
      from_boundary_id, to_boundary_id, mode, generation}。
    """
    api = "retarget_wake_bridge"
    if not isinstance(boundary_id, str) or boundary_id == "":
        raise ValueError(
            "%s：boundary_id 必须是非空字符串（<kind>:<reset_at>，D10），"
            "得到 %r" % (api, boundary_id))
    from runtime.quota import scheduler  # 函数内 import：monkeypatch 友好
    for field, value in (("reset_at", reset_at), ("wake_at", wake_at),
                         ("next_wake_at", next_wake_at)):
        if value is not None and scheduler._parse_iso_utc(value) is None:
            raise ValueError(
                "%s：%s 必须是 None 或可解析的 ISO8601 字符串，得到 %r"
                % (api, field, value))
    st = _require_state(repo_root, task_id, api)
    bridge = _bridge_view(st)
    if bridge.get("status") not in REUSABLE_BRIDGE_STATUSES:
        raise TaskManagerError(
            "%s：任务 %s 的 wake_bridge.status 为 %r——无活桥可 retarget"
            "（先 arm_wake_bridge 建置）" % (api, task_id,
                                            bridge.get("status")))
    old_current = bridge.get("current_boundary_id")
    touched = []
    if boundary_id != old_current:
        bridge["current_boundary_id"] = boundary_id
        touched.append("current_boundary_id")
    for field, value in (("reset_at", reset_at), ("wake_at", wake_at),
                         ("next_wake_at", next_wake_at)):
        if value is not None:
            bridge[field] = value
            touched.append(field)
    result = {key: bridge.get(key) for key in
              ("automation_id", "boundary_id", "current_boundary_id",
               "reset_at", "wake_at", "next_wake_at", "mode", "generation",
               "bridge_interval_minutes", "status")}
    if not touched:
        result["idempotent"] = True
        return result
    state.save_state(repo_root, st)
    journal.append_event(repo_root, task_id, {
        "event": "wake_bridge_retargeted",
        "automation_id": bridge.get("automation_id"),
        "from_boundary_id": old_current, "to_boundary_id": boundary_id,
        "mode": bridge.get("mode"), "generation": bridge.get("generation")})
    return result


def reconcile_wake_bridge_from_host(repo_root, task_id, *, host_status,
                                    observed_at=None) -> dict:
    """手动 / 历史 bridge 对账（v2.2 C1b；修正计划 §22.5，2026-09-02
    锁定）——纯账本 helper。

    硬边界：
      - 零宿主调用：host_status 由调用方显式传入（真正的 host
        adapter / CronList 探针归 C6）——本函数不 import 任何宿主 /
        CronList 概念、零网络；
      - 对账只能降级 / 确认，绝不制造 armed：§22.5「journal / history
        alone MUST NOT manufacture an active bridge」——既有记账已是
        armed / fired 且宿主事实为 active 时仅原地确认（零写）；任何
        非 armed 记账一律不升级（重新 arm 走 arm_wake_bridge，归当前
        session capability 裁决——clean interactive session 才可）。

    host_status ∈ WAKE_BRIDGE_HOST_FACTS：
      - "active"：宿主证据确认 automation 在役——armed / fired 记账
        确认（零写零事件）；其余状态零写上报（不升级）；
      - "deleted"：宿主证据确认 automation 已删除——armed / fired 记
        账降级 status="cancelled"（§22.5：已知已删除的 bridge 记
        cancelled——保留 automation_id / fired_at / 既有 boundary 记
        账，不得显示为 active transport）；
      - "completed"：automation / 任务已完结——armed / fired 记账降
        级 status="stale"（§22.5：completed 的 bridge 记 stale）；
      - "unknown"：无宿主事实——零写上报（不能确认也不能改动）。

    仅对 active transport 记账（status ∈ REUSABLE_BRIDGE_STATUSES，
    即 armed / fired）降级；requested / paused / degraded 等意愿态与
    中间态不归本 helper 处置（各自生命周期另有入口）。降级幂等：记账
    已是目标态（cancelled 对 deleted / stale 对 completed）→ 零写返
    回并增标 "idempotent": True。obligation / tombstone 不动——
    transport missing 之后「重新 arm 还是降级 SessionStart 兜底」由
    当前 session capability 裁决（§22.5 实施分工，C6 落地）。

    落盘：仅实际降级路径单次 save_state + journal wake_bridge_reconciled
    {host_status, from_status, to_status, automation_id, observed_at}
    （host 事实与 last known state 随事件留痕）。确认 / no-op /
    unknown 路径零写零事件。返回 {"task_id", "host_status",
    "bridge_status_before", "bridge_status", "automation_id",
    "activation_transport", "reconciled", "observed_at"}——
    activation_transport ∈ {"armed", "missing"}（按降级后 status 是否
    ∈ REUSABLE_BRIDGE_STATUSES；§22.5：bridge 已删而 task 仍 active →
    missing）。

    参数校验（先于任何 I/O，失败零副作用）：host_status ∈
    WAKE_BRIDGE_HOST_FACTS；observed_at 为 None 或可解析 ISO8601——
    违例 ValueError（中文消息）。任务缺失 TaskManagerError。
    """
    api = "reconcile_wake_bridge_from_host"
    if host_status not in WAKE_BRIDGE_HOST_FACTS:
        raise ValueError(
            "%s：host_status %r 不在合法取值内（%s）"
            % (api, host_status, ", ".join(WAKE_BRIDGE_HOST_FACTS)))
    from runtime.quota import scheduler  # 函数内 import：monkeypatch 友好
    if observed_at is not None \
            and scheduler._parse_iso_utc(observed_at) is None:
        raise ValueError(
            "%s：observed_at 必须是 None 或可解析的 ISO8601 字符串，"
            "得到 %r" % (api, observed_at))
    st = _require_state(repo_root, task_id, api)
    bridge = _bridge_view(st)
    before = bridge.get("status")
    automation_id = bridge.get("automation_id")
    moment = observed_at if observed_at is not None else _utc_now_iso()
    target = None
    if host_status == "deleted":
        target = "cancelled"
    elif host_status == "completed":
        target = "stale"
    reconciled = False
    idempotent = False
    if target is not None and before in REUSABLE_BRIDGE_STATUSES:
        # 降级路径：只改 status，automation_id / fired_at / boundary
        # 记账全保留（§22.5：last fired / last known host state 留痕）
        bridge["status"] = target
        state.save_state(repo_root, st)
        journal.append_event(repo_root, task_id, {
            "event": "wake_bridge_reconciled", "host_status": host_status,
            "from_status": before, "to_status": target,
            "automation_id": automation_id, "observed_at": moment})
        reconciled = True
    elif target is not None and before == target:
        idempotent = True  # 目标态已达成（重复对账）
    return {
        "task_id": task_id,
        "host_status": host_status,
        "bridge_status_before": before,
        "bridge_status": bridge.get("status"),
        "automation_id": automation_id,
        "activation_transport": ("armed" if bridge.get("status")
                                 in REUSABLE_BRIDGE_STATUSES
                                 else "missing"),
        "reconciled": reconciled,
        "observed_at": moment,
        **({"idempotent": True} if idempotent else {}),
    }


def write_completion_tombstone(repo_root, task_id, *,
                               completed_at=None) -> dict:
    """写 bridge 退役墓碑（D15-d 冻结四键；goal 已达成时由完成门 /
    wake 会话调用，ghost bridge 据此转 cheap no-op）。

    - continuation.tombstone = {"task_id", "status": "completed",
      "completed_at", "bridge_should_noop": True}——四键形状由
      state.validate_state 闸背书；首块墓碑恒胜：已存在 → 幂等 no-op
      （返回既有墓碑 + "idempotent": True，零写）；
    - completed_at：None → 当前时刻；显式传入须可解析 ISO8601；
    - 单次 save_state；**不落 journal**——词汇表中无 tombstone 事件，
      墓碑本身（state.json continuation.tombstone）即持久记录，wake
      会话按 bridge_should_noop=true 直接 no-op（D15-d 第 3/4 层缓解；
      单次 CronDelete 清理尝试归主会话宿主动作，绝不重试）。
    返回墓碑 dict（含 idempotent 标记当命中幂等）。
    """
    api = "write_completion_tombstone"
    from runtime.quota import scheduler  # 函数内 import：monkeypatch 友好
    if completed_at is not None \
            and scheduler._parse_iso_utc(completed_at) is None:
        raise ValueError(
            "%s：completed_at 必须是 None 或可解析的 ISO8601 字符串，"
            "得到 %r" % (api, completed_at))
    st = _require_state(repo_root, task_id, api)
    continuation = _ensure_continuation(st)
    existing = continuation.get("tombstone")
    if isinstance(existing, dict):
        result = dict(existing)
        result["idempotent"] = True
        return result
    moment = completed_at if completed_at is not None else _utc_now_iso()
    tombstone = {
        "task_id": task_id,
        "status": "completed",
        "completed_at": moment,
        "bridge_should_noop": True,
    }
    continuation["tombstone"] = tombstone
    state.save_state(repo_root, st)
    return dict(tombstone)


def degrade_continuity(repo_root, task_id, *,
                       reason="scheduler_create_forbidden") -> dict:
    """连续性降级记账（create 不可用且无 reusable bridge →
    obligation=degraded；D15-c / §12.3：不得无限 Stop block）。

    - reason 缺省 "scheduler_create_forbidden"（Phase 0 #13 的机械
      降级原因）；非空 str 校验；
    - 写 continuation.obligation="degraded" + continuation.reason=
      reason；wake_bridge.status 不动（降级的是连续性，不是桥本身）；
    - 幂等：obligation 已为 degraded → 零写零事件，返回
      "idempotent": True；
    - 单次 save_state + journal continuity_degraded {reason}。
    """
    api = "degrade_continuity"
    if not isinstance(reason, str) or reason == "":
        raise ValueError(
            "%s：reason 必须是非空字符串，得到 %r" % (api, reason))
    st = _require_state(repo_root, task_id, api)
    continuation = _ensure_continuation(st)
    if continuation.get("obligation") == "degraded":
        return {"obligation": "degraded",
                "reason": continuation.get("reason"), "idempotent": True}
    continuation["obligation"] = "degraded"
    continuation["reason"] = reason
    state.save_state(repo_root, st)
    journal.append_event(repo_root, task_id, {
        "event": "continuity_degraded", "reason": reason})
    return {"obligation": "degraded", "reason": reason}
