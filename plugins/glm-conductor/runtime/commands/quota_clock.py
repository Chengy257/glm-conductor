#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""runtime.commands.quota_clock：Global Quota Clock 四子命令（v2.3.1
Wave 3，unit w3-quota-clock-split）。

quota-clock-plan / bind / tick / status 的处理函数自 runtime/cli.py
原样迁入（纯搬运、零行为变化：stdout 字节与退出码逐路径等价；argv
参数校验与 USAGE 仍归 cli._dispatch，依赖方向恒为 cli → commands →
域模块，本模块不 import runtime.cli）。单行 JSON 输出与 cli._emit
同款契约（json.dumps(..., ensure_ascii=True) + "\\n"）。

函数名与 cli.py 时代保持一致（同名迁移）：
    _quota_clock_plan      quota-clock-plan：纯规划（零写盘、零 DB、
                           零 state）。
    _quota_clock_bind      quota-clock-bind：绑定 clock automation +
                           用户级 state + 首次 retime。
    _quota_clock_tick      quota-clock-tick：Scheduled Task 唯一主
                           入口（决策 → retime → state 观测更新）。
    _quota_clock_status    quota-clock-status：clock 绑定健康只读
                           汇总。
专属私有辅助随迁：_ClockNotPlannable / _clock_repo_root / _clock_fatal
/ _clock_five_hour_targets / _CLOCK_TICK_PROMPT / _CLOCK_RUNTIME_DIR
（本文件位于 runtime/commands/，常量取上级目录——与迁移前 cli.py 的
os.path.dirname(os.path.abspath(__file__)) 同值）。_clock_now_ms 为
wake-retime（cli.py）共用的琐碎取 now 点：按 W3 迁移规格在 cli.py 保
留原件、本模块保留同实现副本（琐碎 helper 允许务实重复，commands 模
块间互不 import）。

域模块经函数内 import（monkeypatch 友好先例，迁移后同形态保留）：
resolver / clock / clock_store / identity / host.zcode_schedule。
"""

import json
import os
import pathlib
import sys
import time


def _emit(payload):
    """向 stdout 写单行 JSON（ensure_ascii=True，与 cli._emit 同款契
    约锚点；本模块自含，绝不回 import cli）。"""
    sys.stdout.write(json.dumps(payload, ensure_ascii=True) + "\n")


# runtime 目录（runtime_path 口径唯一来源）：plan 输出、bind 落盘与
# tick §10.5 升级自愈三处共用同一常量。迁移前该常量在 cli.py 以
# os.path.dirname(os.path.abspath(__file__)) 取本 CLI 文件所在
# runtime 目录；本文件位于 runtime/commands/，故取上级目录——与迁移
# 前同值（…/plugins/glm-conductor/runtime），输出逐字节不变。
_CLOCK_RUNTIME_DIR = os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))

# §9 极简 tick prompt 模板（逐字冻结；v2.3.1 w35-dogfood-fix 经裁决
# 修订冻结版：原硬编码 "python3" → 第三代入槽 {python}——plan 时以
# sys.executable 代入（空则回退 "python3"），消除 Windows 无 python3
# 启动器的首跳必失败；其余文本逐字不变，仅 {cli} / {repo} / {python}
# 三处代入）。
_CLOCK_TICK_PROMPT = (
    "GLM CONDUCTOR QUOTA CLOCK TICK\n"
    "Run: {python} {cli} quota-clock-tick {repo}\n"
    "If that path is missing, locate cli.py under the current glm-conductor\n"
    "plugin cache runtime directory and run it there.\n"
    "Reply with the tick JSON in one line, then end the turn.\n"
    "Never call Cron tools. Never do anything else.")


class _ClockNotPlannable(Exception):
    """five_hour 窗口缺失 / reset 不可解析——quota-clock 无法规划确定性
    目标时刻（plan / bind 共用；reason 取 clock 决策层同名词汇）。"""


def _clock_repo_root(raw):
    """repo_root 归一化：相对路径 → 绝对（口径复刻 state 层 RB-2 的
    str(Path(os.path.abspath(repo)).resolve())）。"""
    return str(pathlib.Path(os.path.abspath(str(raw))).resolve())


def _clock_now_ms():
    """当前 epoch 毫秒（clock 子命令接线层取 now 点；wake-retime 于
    cli.py 保留同实现副本，两处互不 import）。"""
    return int(time.time() * 1000)


def _clock_fatal(message, *, status="error", **extra):
    """clock 子命令失败路径统一 JSON 面：status 与 error 键恒在，其余
    上下文键 additive；恒返回退出码 1。"""
    payload = {"status": status, "error": message}
    payload.update(extra)
    _emit(payload)
    return 1


def _clock_five_hour_targets(detail):
    """resolver 明细 → five_hour reset 三元组
    (reset_at, reset_epoch_ms, first_target_epoch_ms)。

    first_target = B + DEFAULT_GRACE_SECONDS*1000（plan / bind 共用
    数学）；five_hour 缺失 / reset 不可解析 → _ClockNotPlannable
    （reason 复用 clock 决策层同名词汇；ISO→epoch 毫秒折算复用决策层
    clock._parse_reset_at_ms 同一解析口径，绝不另立解析器）。"""
    from runtime.quota import clock  # 函数内 import：monkeypatch 友好
    snapshot = detail.get("snapshot") if isinstance(detail, dict) else None
    window = clock.select_five_hour_window(snapshot)
    if window is None:
        raise _ClockNotPlannable(clock.REASON_FIVE_HOUR_MISSING)
    reset_at = window.get("reset_at")
    reset_ms = clock._parse_reset_at_ms(reset_at)
    if reset_ms is None:
        raise _ClockNotPlannable(clock.REASON_FIVE_HOUR_UNPARSABLE)
    return reset_at, reset_ms, reset_ms + int(
        clock.DEFAULT_GRACE_SECONDS) * 1000


def _quota_clock_plan(raw_repo_root) -> int:
    """quota-clock-plan：纯规划（零写盘、零 DB、零 state）。

    force-refresh 解析 → identity → five_hour reset B → first_target =
    B + 120s。输出冻结键集 {status:"planned", provider_identity_hash,
    reset_at, reset_at_epoch_ms, first_target_epoch_ms, grace_seconds,
    retry_delay_seconds, fallback_interval_minutes:60, runtime_path,
    cli_command, suggested_prompt, placement_guidance}（v2.3.1 追加
    placement_guidance：创建 automation 前的专用会话放置引导，纯函数
    clock.placement_guidance_for_plan——advisory 文案，引导用户在独立
    低成本 Flash 会话创建，勿在主编码会话创建 Global Quota Clock）；
    five_hour 缺失 / 不可解析 → {"status": "not_plannable", "reason",
    "error"} 退出码 1。"""
    from runtime.quota import clock, resolver  # 函数内 import：monkeypatch 友好
    from runtime.quota.identity import (  # 函数内 import：monkeypatch 友好
        compute_provider_identity_hash)
    repo_root = _clock_repo_root(raw_repo_root)
    try:
        detail = resolver.resolve_quota_detail(repo_root,
                                               force_refresh=True)
        identity = compute_provider_identity_hash()
        reset_at, reset_ms, first_target = _clock_five_hour_targets(detail)
    except _ClockNotPlannable as exc:
        return _clock_fatal(
            "quota-clock-plan：无法规划确定性目标时刻（%s）" % exc,
            status="not_plannable", reason=str(exc))
    except Exception as exc:
        return _clock_fatal("%s: %s" % (type(exc).__name__, exc))
    # F-4（v2.3.1 w35-dogfood-fix）：plan 产物以当前解释器生成——
    # sys.executable（空则回退 "python3"），cli_command 与
    # suggested_prompt（{python} 槽）两点同源代入，本机解释器一跳
    # 可执行（Windows 无 python3 启动器的首跳必失败就此消除）
    python = sys.executable or "python3"
    cli_path = _CLOCK_RUNTIME_DIR + "/cli.py"
    tick_command = "%s %s quota-clock-tick %s" % (python, cli_path,
                                                  repo_root)
    _emit({
        "status": "planned",
        "provider_identity_hash": identity,
        "reset_at": reset_at,
        "reset_at_epoch_ms": reset_ms,
        "first_target_epoch_ms": first_target,
        "grace_seconds": clock.DEFAULT_GRACE_SECONDS,
        "retry_delay_seconds": clock.DEFAULT_RETRY_DELAY_SECONDS,
        "fallback_interval_minutes": 60,
        "runtime_path": _CLOCK_RUNTIME_DIR,
        "cli_command": tick_command,
        "suggested_prompt": _CLOCK_TICK_PROMPT.format(python=python,
                                                      cli=cli_path,
                                                      repo=repo_root),
        # v2.3.1（w1-clock-ux）：创建前的会话放置引导（纯 advisory，
        # 判定与文案在 clock 纯函数层，本处仅呈现）
        "placement_guidance": clock.placement_guidance_for_plan(),
    })
    return 0


def _quota_clock_bind(raw_repo_root, automation_id, db_path=None) -> int:
    """quota-clock-bind：绑定 clock automation + 用户级 state + 首次
    retime（v2.3.0 §8）。

    须在普通交互回合执行（推荐 Flash 会话——automation 的 model 即发
    起会话的模型；非 Flash 属 §3.3 已知边界，model_is_flash=false 仅
    告警不阻断）。--db 缺省走
    zcode_schedule.discover_zcode_tasks_db(None)（环境变量 / 默认路径
    优先级见 adapter）。流程：force-refresh 解析 → identity →
    first_target（不可规划 → 非零）→ discover → inspect（行缺失 /
    recurring 假值 → 非零）→ bind_clock_state（grace / retry / fallback
    取存储层缺省）→ retime_automation(first_target)；retime 失败 →
    非零退出并注明 state 已写但 next_target 未生效（automation 仍按旧
    节奏运行，可重试 bind）。

    F-1 死绑定自愈（v2.3.1 w35-dogfood-fix）：bind_clock_state 报
    ClockStateConflictError（已绑定其它 automation）时，只读 load 提取
    旧 automation_id → inspect_automation(db, old_id) 复核宿主 DB 行：
    行确认缺失（row is None）→ 以 verified_dead_automation_id=old_id
    重试 bind_clock_state（存储层锁内 TOCTOU 复核通过才放行覆盖，本层
    负责失效核实、存储层零 DB I/O）；inspect 异常 / 行存活 / 锁内身份
    不符 → ClockStateConflictError 原样维持（fail-closed——活绑定保护
    初心不变，本修复非自动迁移，用户仍显式提供新 automation_id）。走
    自愈成功时输出追加键 replaced_dead_binding=<old_automation_id>
    （只增不删，仅此路径出现）。

    输出冻结键集 {status:"bound",
    provider_identity_hash, automation_id, automation_model,
    model_is_flash, first_target_epoch_ms, retimed, state_path} +
    v2.3.1 追加（只增不删）：session_placement（SESSION PLACEMENT
    说明：automation 保持附着当前会话、周期 tick 将出现在本对话中、
    建议专用低成本 Flash 会话、避免绑定主编排/编码会话；纯函数
    clock.bind_session_placement）与 cost_advisory（模型名不含
    "Flash" 时为 {current_model, preferred: "low-cost Flash-class
    model"}，Flash 类为 null——纯 advisory 成本提示，绝不阻断绑定；
    纯函数 clock.cost_advisory_for_model）与 replaced_dead_binding
    （F-1 死绑定自愈路径专属，见上）。model_is_flash 判定口径
    为模型名含 "Flash"（纯函数 clock.model_is_flash，仅 advisory，
    不作阻断、不作专用会话证据）。"""
    from runtime.quota import clock, clock_store, resolver  # 函数内 import：monkeypatch 友好
    from runtime.quota.identity import (  # 函数内 import：monkeypatch 友好
        compute_provider_identity_hash)
    from runtime.host import zcode_schedule  # 函数内 import：monkeypatch 友好
    repo_root = _clock_repo_root(raw_repo_root)
    replaced_dead_binding = None
    try:
        detail = resolver.resolve_quota_detail(repo_root,
                                               force_refresh=True)
        identity = compute_provider_identity_hash()
        (_reset_at, _reset_ms,
         first_target) = _clock_five_hour_targets(detail)
        db = zcode_schedule.discover_zcode_tasks_db(db_path)
        row = zcode_schedule.inspect_automation(db, automation_id)
        if row is None:
            return _clock_fatal(
                "quota-clock-bind：automation %r 不存在（db=%s）——请在"
                " ZCode 交互会话内先创建 recurring automation 再绑定"
                % (automation_id, db))
        if not row.get("recurring"):
            return _clock_fatal(
                "quota-clock-bind：automation %r recurring=%r 非 "
                "recurring 行，拒绝绑定" % (automation_id,
                                           row.get("recurring")))
        try:
            clock_store.bind_clock_state(
                repo_root, identity, automation_id,
                zcode_db_path=db, runtime_path=_CLOCK_RUNTIME_DIR,
                automation_model=row.get("model"), now_ms=_clock_now_ms())
        except clock_store.ClockStateConflictError:
            # F-1 死绑定自愈（唯一放行路径，fail-closed）：冲突 → 只读
            # load 提取旧 automation_id → DB 行复核（异常 / 行存活一律
            # 原样维持冲突拒绝）→ 行确认缺失才带 verified_dead_automation_id
            # 重试（存储层锁内 TOCTOU 身份复核后才真正放行）。
            current = clock_store.load_clock_state(identity)
            old_id = (current.get("automation_id")
                      if isinstance(current, dict) else None)
            if not isinstance(old_id, str) or not old_id \
                    or old_id == automation_id:
                raise
            if zcode_schedule.inspect_automation(db, old_id) is not None:
                raise  # 宿主行存活 = 活绑定：保护初心不变
            clock_store.bind_clock_state(
                repo_root, identity, automation_id,
                zcode_db_path=db, runtime_path=_CLOCK_RUNTIME_DIR,
                automation_model=row.get("model"),
                now_ms=_clock_now_ms(),
                verified_dead_automation_id=old_id)
            replaced_dead_binding = old_id
    except _ClockNotPlannable as exc:
        return _clock_fatal(
            "quota-clock-bind：无法规划确定性目标时刻（%s）" % exc,
            status="not_plannable", reason=str(exc))
    except Exception as exc:
        return _clock_fatal("%s: %s" % (type(exc).__name__, exc))
    try:
        zcode_schedule.retime_automation(db, automation_id, first_target)
    except Exception as exc:
        return _clock_fatal(
            "quota-clock-bind：retime 失败（%s: %s）；state 已写入但 "
            "next_target 未生效——automation 仍按旧节奏运行，可重试 "
            "bind 或依赖 native watchdog" % (type(exc).__name__, exc),
            automation_id=automation_id, state_written=True,
            next_target_applied=False,
            first_target_epoch_ms=first_target)
    model = row.get("model")
    payload = {
        "status": "bound",
        "provider_identity_hash": identity,
        "automation_id": automation_id,
        "automation_model": model,
        "model_is_flash": clock.model_is_flash(model),
        "first_target_epoch_ms": first_target,
        "retimed": True,
        "state_path": clock_store.clock_state_path(identity),
        # v2.3.1（w1-clock-ux）：会话放置说明 + 非 Flash 成本提示
        # （纯 advisory——绑定永不因模型档位被阻断）
        "session_placement": clock.bind_session_placement(),
        "cost_advisory": clock.cost_advisory_for_model(model),
    }
    if replaced_dead_binding is not None:
        # F-1（w35-dogfood-fix）死绑定自愈路径专属键（只增不删）
        payload["replaced_dead_binding"] = replaced_dead_binding
    _emit(payload)
    return 0


def _quota_clock_tick(raw_repo_root) -> int:
    """quota-clock-tick：Scheduled Task 唯一主入口（v2.3.0 §8）。

    automation 的 prompt 即 quota-clock-plan 的 suggested_prompt（§9）：
    本命令在一个调度回合内完成「决策 → retime → state 观测更新」并单行
    JSON 汇报后结束回合（prompt 约束：不调 Cron 工具、不做其它事）。
    流程：identity → load_clock_state（缺失 → {"status": "error",
    "reason": "state_missing"} 非零）→ force-refresh 解析 →
    clock.next_clock_target（state 供给 last_reset_at / grace_seconds /
    retry_delay_seconds）→ retime_automation(state.zcode_db_path,
    state.automation_id, decision.target_epoch_ms)。retime 失败（任何
    异常，含 ZcodeScheduleBusyError）→ 什么都不做（native watchdog 接
    管，§4.2），仅更新 last_tick_at，输出 retimed=false——tick 仍算完
    成，退出码 0。成功路径 update_clock_state：last_tick_at=now、
    last_reset_at=本次观测（缺失则保留旧值）、next_target_at=target、
    last_retime_at=now、status 决策映射（weekly_park → parked_weekly；
    bound/rebound → bound；其余保留）+ §10.5 升级自愈（实际 runtime
    目录 ≠ state 记录 → 回写实际值）。输出冻结键集 {status:"ticked",
    provider_identity_hash, reset_at, next_target_at, automation_id,
    retimed, reason, decision}。"""
    from runtime.quota import clock, clock_store, resolver  # monkeypatch 友好
    from runtime.quota.identity import (  # 函数内 import：monkeypatch 友好
        compute_provider_identity_hash)
    from runtime.host import zcode_schedule  # 函数内 import：monkeypatch 友好
    repo_root = _clock_repo_root(raw_repo_root)
    try:
        identity = compute_provider_identity_hash()
        clock_state = clock_store.load_clock_state(identity)
        if clock_state is None:
            return _clock_fatal(
                "quota-clock-tick：clock state 缺失（provider identity "
                "%s 未绑定）——请先在普通交互回合运行 quota-clock-bind"
                % identity, reason="state_missing")
        detail = resolver.resolve_quota_detail(repo_root,
                                               force_refresh=True)
        now_ms = _clock_now_ms()
        decision = clock.next_clock_target(
            detail.get("snapshot") if isinstance(detail, dict) else None,
            now_ms=now_ms,
            last_reset_at=clock_state.get("last_reset_at"),
            grace_seconds=clock_state.get("grace_seconds"),
            retry_delay_seconds=clock_state.get("retry_delay_seconds"))
        target = int(decision["target_epoch_ms"])
        try:
            zcode_schedule.retime_automation(
                clock_state.get("zcode_db_path"),
                clock_state.get("automation_id"), target)
            retimed = True
        except Exception:  # §4.2：native watchdog 接管，本侧零补偿动作
            retimed = False
        observed_reset = decision.get("reset_at_epoch_ms")
        current_status = clock_state.get("status")
        if decision.get("decision") == "weekly_park":
            new_status = "parked_weekly"
        elif current_status in ("bound", "rebound"):
            new_status = "bound"
        else:
            new_status = current_status

        def _tick_updater(current):
            updated = dict(current)
            updated["last_tick_at"] = now_ms
            if not retimed:
                return updated  # 失败路径仅观测 last_tick_at（§4.2）
            updated["last_reset_at"] = (observed_reset
                                        if observed_reset is not None
                                        else current.get("last_reset_at"))
            updated["next_target_at"] = target
            updated["last_retime_at"] = now_ms
            updated["status"] = new_status
            if current.get("runtime_path") != _CLOCK_RUNTIME_DIR:
                # §10.5 升级自愈：插件缓存升级后旧路径失真，回写实际值
                updated["runtime_path"] = _CLOCK_RUNTIME_DIR
            return updated

        clock_store.update_clock_state(identity, _tick_updater)
    except Exception as exc:
        return _clock_fatal("%s: %s" % (type(exc).__name__, exc))
    _emit({
        "status": "ticked",
        "provider_identity_hash": identity,
        "reset_at": decision.get("reset_at"),
        "next_target_at": target,
        "automation_id": clock_state.get("automation_id"),
        "retimed": retimed,
        "reason": decision.get("reason"),
        "decision": decision.get("decision"),
    })
    return 0


def _quota_clock_status(raw_repo_root) -> int:
    """quota-clock-status：clock 绑定健康只读汇总（v2.3.0 §8；纯读：
    零 provider 解析、零写盘、零 DB 写；repo_root 仅保持四命令签名
    一致——clock state 按 provider identity 寻址）。

    identity → load_clock_state → 缺失 → {"bound": false,
    "health": "unbound", "suggestion": "run quota-clock-plan then bind
    from an interactive round"} 退出码 0。已绑定：inspect automation 行
    （异常 → unhealthy + db_inspect_failed 原因）并汇总冻结键集
    {bound, provider_identity_hash, automation_id, automation_model,
    status, last_reset_at, next_target_at, db_next_run_at,
    target_matches_db, runtime_path, runtime_path_exists, last_tick_at,
    tick_stale, recent_run_count, health, reasons, suggestion} +
    v2.3.1 追加诊断键（只增不删，unit w1-clock-ux）：placement
    {session_binding: "active"|"unavailable"（automation 行可查 /
    缺失或不可查）, preferred_model: <bool>（automation_model 含
    "Flash"——advisory 口径，绝非专用会话证据）, dedicated_session:
    "not_mechanically_verifiable"（恒为常量串——插件无会话探测能力，
    绝不伪造布尔）}；host: {adapter: "zcode_sqlite"}；w35-dogfood-fix
    追加 awaiting_first_tick: <bool>（last_tick_at 缺失 / 非数值 =
    首绑未 tick 的 awaiting first tick 语义——与 hooks/session_start.py
    的 _clock_tick_stale 同口径镜像，单一真相源互指）；
    needs_replacement: <bool>（True ⇔ reasons 含 db_row_missing /
    db_inspect_failed；target_mismatch / tick_stale /
    runtime_path_missing 仅 advisory 不触发——锁定决策 c 两极）；
    needs_replacement=true 时附 recovery_guidance（在专用低成本
    Flash 会话中显式执行 replace/rebind：quota-clock-bind
    <new_automation_id>——bind 对已确认失效的死绑定自动检测并替换
    （宿主 DB 行缺失时放行改绑，用户仍显式提供新 automation_id），
    并声明「未执行自动会话迁移」——本插件不做隐式迁移 / 自动
    rebind）。判定全部落 clock 纯函数
    （status_placement_block / status_host_block /
    needs_replacement_from_reasons / recovery_guidance_for_replacement），
    本函数只做呈现。

    health 判定（任一命中 → "unhealthy"，reasons 为 snake token）：
      db_row_missing（DB 行缺失）；target_mismatch（DB next_run_at 与
      state.next_target_at 不一致——next_target_at 为 None（bind 后首
      tick 前）不判不一致，避免对刚绑定者误报 replace 建议）；
      runtime_path_missing（state.runtime_path 目录不存在，tick 将
      §10.5 自愈）；tick_stale（last_tick_at 为合法数值且
      now - last_tick_at > 2×fallback_interval_minutes；w35-dogfood-fix
      起 last_tick_at 缺失 / 非数值改判 awaiting_first_tick=true——
      不再视为陈旧（首绑未 tick 的 healthy + awaiting_first_tick
      自洽呈现，镜像 hooks/session_start.py 的 _clock_tick_stale），
      只触发「await next tick」级提示，不触发 replace）；
      db_inspect_failed（inspect 异常，token 后附异常摘要）。
    suggestion：db_row_missing / db_inspect_failed → "replace: run
      quota-clock-bind <new_automation_id> from a fresh interactive
      session"；其余原因 → "await next tick"；healthy → null。"""
    from runtime.quota import clock, clock_store  # 函数内 import：monkeypatch 友好
    from runtime.host import zcode_schedule  # 函数内 import：monkeypatch 友好
    from runtime.quota.identity import (  # 函数内 import：monkeypatch 友好
        compute_provider_identity_hash)
    try:
        identity = compute_provider_identity_hash()
        clock_state = clock_store.load_clock_state(identity)
    except Exception as exc:
        return _clock_fatal("%s: %s" % (type(exc).__name__, exc))
    if clock_state is None:
        _emit({"bound": False, "health": "unbound",
               "suggestion": ("run quota-clock-plan then bind from an "
                              "interactive round")})
        return 0
    try:
        reasons = []
        row = None
        try:
            row = zcode_schedule.inspect_automation(
                clock_state.get("zcode_db_path"),
                clock_state.get("automation_id"))
        except Exception as exc:
            reasons.append("db_inspect_failed: %s: %s"
                           % (type(exc).__name__, exc))
        db_next_run_at = None
        if row is None:
            reasons.append("db_row_missing")
        else:
            db_next_run_at = row.get("next_run_at")
        expected_target = clock_state.get("next_target_at")
        if row is None:
            target_matches_db = False
        elif expected_target is None:
            # bind 后首 tick 前 state 尚无已生效目标记录：不判不一致
            target_matches_db = True
        else:
            target_matches_db = db_next_run_at == expected_target
            if not target_matches_db:
                reasons.append("target_mismatch")
        runtime_path = clock_state.get("runtime_path")
        runtime_path_exists = bool(runtime_path) \
            and os.path.isdir(runtime_path)
        if not runtime_path_exists:
            reasons.append("runtime_path_missing")
        now_ms = _clock_now_ms()
        last_tick_at = clock_state.get("last_tick_at")
        fallback_minutes = clock_state.get("fallback_interval_minutes")
        if isinstance(fallback_minutes, bool) \
                or not isinstance(fallback_minutes, (int, float)):
            fallback_minutes = 60  # state 半块容错：按缺省 60 分钟折算
        # F-3（v2.3.1 w35-dogfood-fix）：last_tick_at 缺失 / 非数值 =
        # 首绑未 tick 的 awaiting first tick 语义，不再判 tick_stale
        #（修复「新装即 unhealthy 与 await next tick 同框矛盾」；
        # hooks/session_start.py 的 _clock_tick_stale 与此同口径镜像，
        # 单一真相源互指）。
        awaiting_first_tick = isinstance(last_tick_at, bool) or \
            not isinstance(last_tick_at, (int, float))
        tick_stale = (not awaiting_first_tick) and (
            now_ms - last_tick_at > 2 * fallback_minutes * 60000)
        if tick_stale:
            reasons.append("tick_stale")
        if "db_row_missing" in reasons \
                or any(item.startswith("db_inspect_failed")
                       for item in reasons):
            suggestion = ("replace: run quota-clock-bind "
                          "<new_automation_id> from a fresh interactive "
                          "session")
        elif reasons:
            suggestion = "await next tick"
        else:
            suggestion = None
        # v2.3.1（w1-clock-ux）：placement 诊断 / host 标识 /
        # needs_replacement 两极判定——全部经 clock 纯函数，本处只呈现
        needs_replacement = clock.needs_replacement_from_reasons(reasons)
        payload = {
            "bound": True,
            "provider_identity_hash": identity,
            "automation_id": clock_state.get("automation_id"),
            "automation_model": clock_state.get("automation_model"),
            "status": clock_state.get("status"),
            "last_reset_at": clock_state.get("last_reset_at"),
            "next_target_at": expected_target,
            "db_next_run_at": db_next_run_at,
            "target_matches_db": target_matches_db,
            "runtime_path": runtime_path,
            "runtime_path_exists": runtime_path_exists,
            "last_tick_at": last_tick_at,
            "tick_stale": tick_stale,
            "awaiting_first_tick": awaiting_first_tick,
            "recent_run_count": (row.get("run_count")
                                 if isinstance(row, dict) else 0),
            "health": "unhealthy" if reasons else "healthy",
            "reasons": reasons,
            "suggestion": suggestion,
            "placement": clock.status_placement_block(
                clock_state.get("automation_model"),
                automation_row_available=row is not None),
            "host": clock.status_host_block(),
            "needs_replacement": needs_replacement,
        }
        if needs_replacement:
            payload["recovery_guidance"] = \
                clock.recovery_guidance_for_replacement()
        _emit(payload)
        return 0
    except Exception as exc:
        return _clock_fatal("%s: %s" % (type(exc).__name__, exc))
