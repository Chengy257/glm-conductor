#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GLM Conductor v2.1 M3 SessionStart 恢复发现层（recovery，wu-21-05）。

职责（计划 §8，连续性缺口 R2）：
    把「主会话崩溃 / 换会话后恢复靠模型记忆手动完成」变成机械发现：
    新会话（SessionStart，matcher "startup|clear|compact"）自动知道——
      - 存在什么未完成任务（discover_tasks 四分类的 active 桶）；
      - 哪些 work unit 未完成（含被中断的 running/waiting_quota/blocked
        单元）；
      - 哪些 agent run 需要 reconcile（agent_run.run_lifecycle 僵尸
        感知——possibly_zombie 标注，§7.3：metadata running 不是 truth）；
      - 下一步建议（Recommended 1-4，§8.3 模板尾部）。
    两个对外函数：
      - build_recovery_summary(repo_root)：聚合恢复摘要 dict（冻结形状，
        见函数 docstring）；
      - render_resume_context(summary)：把摘要渲染为英文 resume context
        文本（§8.3 形态）；无内容时返回 ""（钩子据此完全静默，零噪音）。

§8.2 纯本地红线：
    本模块不调模型、不联网、不执行 implementation、不消耗 quota、
    无 git subprocess——只用 runtime.state / runtime.agent_run 的本地
    文件读取面（state.json / events.jsonl / 原生档案只读 adapter），
    钩子 timeoutMs 5000 内可完成。git 操作不做：恢复建议里提示由
    模型后续执行（inspect repository residue）。

边界（与其他单元的分工）：
    - reconcile 四分决策（reuse_result / resume_with_progress /
      redispatch_clean / manual_ruling）归 wu-21-06（runtime/reconcile.py），
      本模块只呈现「需要 reconcile 的信号」（interrupted + 僵尸标注）；
    - resume context 的自动 checkpoint 压缩归 wu-21-07（resume_manifest）；
    - corrupt 任务的 fail-closed 处置归后续单元 / 模型——本模块只在
      summary["corrupt"] 列出 + render 末尾单行警告（不展开）；
    - v2.2.1 WU-221-B2（QuotaIdentity）注记：本模块是纯呈现层，不
      消费 / 不比较任何 epoch 记账事实（epoch 身份不在摘要十个字段
      内）——quota/epoch 记录的跨身份（A 注册 B 恢复）裁决在消费面
      完成（task_manager 的订阅资格门 / 崩溃对账 / 消费幂等按
      quota_identity_matches 双形态判定），恢复摘要照实呈现、不重复
      裁决，也不因身份切换新增或隐藏任何条目。

v2.2 M9 增补（wu-22-09，SessionStart 桥接对账展示——只增块不删行）：
    恢复面此前看不到 v2.2 控制回路的关键状态（execution phase / wake
    bridge 存活 / auto_resume 授权 / 下一步安全动作）。本单元追加：
      - reconcile_wake_bridge(task_state, *, now=None)：桥接对账纯
        函数——stale 判定（bridge status ∈ {armed, fired} 且
        next_wake_at 已过 bridge_interval_minutes 的
        STALE_BRIDGE_INTERVAL_MULTIPLIER 倍——「已过 next_wake_at 且
        >2×interval」；判定仅在活动任务上执行，build_recovery_summary
        只扫 active 桶即「仍非终态」由扫描口径保证）与
        fired-but-unresolved 判定（fired 但 obligation 仍为
        wake_required / degraded）；只读不写——状态推进归 M5/M8，
        本层只展示信号；
      - 摘要条目条件性第十键 "continuity"（沿用 wu-21-11 quota_wait
        第九键的「条件增补、既有八字段冻结形状逐字不变」模式）：仅当
        任务携带控制回路事实（quota.execution_phase 已回填 /
        obligation 非 none / bridge status 非 none / 任务处于
        waiting_quota / waiting_user）时携带——legacy v2.1 任务（无
        continuation / quota 块）零增键零增行（优雅降级为安静）；
      - 渲染尾部四行展示块（逐字键冻结）：WAKE BRIDGE / QUOTA PHASE /
        AUTO RESUME / NEXT SAFE ACTION，值各一行中文短语，stale /
        fired-unresolved 判定注入 WAKE BRIDGE 与 NEXT SAFE ACTION 值。

依赖：
    runtime.state（discover_tasks / load_state / resolve_repository_root）
    + runtime.agent_run（run_lifecycle）+ runtime.journal（quota_waiting
    事件读取，v2.1 §14 wu-21-11：waiting_quota / waiting_user 任务的
    recommended_resume_at 恒从 journal 事实源读，不虚构）+
    runtime.execution_policy（consumed_quota_windows 容错读，剩余窗口
    预算渲染；default_quota_control 容错读 bridge_interval_minutes
    缺省 60——stale 判定的间隔回退单一真相源）。全部纯本地读取，仅
    Python 3 标准库，`python3 -S` 可运行（无 site-packages）。
"""

import datetime

from runtime import agent_run, journal, state
from runtime.execution_policy import (consumed_quota_windows,
                                      default_quota_control)

# 摘要 goal 字段的截断长度（冻结：120 字符，控制注入文本体积）
GOAL_TRUNCATE = 120

# 「被中断」单元状态词汇（冻结：仅这三个状态算 interrupted——
# running 是被中断的主形态；waiting_quota / blocked 是任务账本里
# 显式挂起的形态。verifying / pending / failed 等只进 incomplete）
INTERRUPTED_UNIT_STATUSES = ("running", "waiting_quota", "blocked")

# legacy 任务的 auto_resume 兜底值（无 execution_policy 块即 legacy，
# 按保守默认 manual 解释，与 default_execution_policy 一致）
LEGACY_AUTO_RESUME = "manual"

# 额度等待任务状态词汇（v2.1 §14 wu-21-11）：处于这两个状态的任务在
# 摘要条目增补第九键 "quota_wait"（recommended_resume_at + 剩余窗口
# 预算），渲染层据此追加 Quota wait / resume_from_quota 指引两行；
# 其他状态不增补（既有八字段冻结形状不变）
QUOTA_WAIT_TASK_STATUSES = ("waiting_quota", "waiting_user")

# —— render 层有界性常量（additionalContext 有 32KB stdout cap） ——

# 模板头部（逐字冻结，测试断言）
RESUME_HEADER = "GLM CONDUCTOR RESUME CONTEXT"

# 最多逐段列出的任务数（超出只列前 3 + omitted 行）
MAX_RENDERED_TASKS = 3

# 每个单元列表（completed / interrupted / waiting）最多列出的条目数
# （超出截断 + omitted 计数）
MAX_RENDERED_UNITS = 8

# 僵尸标注（逐字冻结）：run_lifecycle.possibly_zombie=True 时该单元
# 追加此行——§7.3 metadata running 不是 truth，真相裁决归 reconcile
ZOMBIE_NOTE = "agent run possibly zombie (metadata running is not truth)"

# corrupt 警告行里最多列出的 task_id 数（超出以 +N more 截断，保持单行）
MAX_CORRUPT_IDS_SHOWN = 5


# —— v2.2 M9 桥接对账常量（wu-22-09） ——

# stale 判定阈值（WU-22-09 冻结，测试锚定）：bridge status ∈
# {armed, fired} 且 next_wake_at 已过 bridge_interval_minutes 的本
# 倍数（「已过 next_wake_at 且 >2×interval」即判 stale——只错过一拍
# 不算失联，错过两拍以上视为 bridge 存活性可疑）。判定仅在活动任务
# 上执行（build_recovery_summary 只扫 active 桶）——「仍非终态」由
# 扫描口径保证；本层只判定只展示，状态推进归 M5/M8。
STALE_BRIDGE_INTERVAL_MULTIPLIER = 2

# 参与 stale 判定的 bridge status 子集（WU-22-09：「status ∈
# {armed, fired} 且 next_wake_at 远过期」；其余状态——none/requested/
# retarget_required/degraded/paused/cancelled/stale/failed——不参与
# 时间数学。字面量词汇与 runtime.state.WAKE_BRIDGE_STATUSES 十值一致，
# 一致性由 tests.test_manifest_quota_control 锚定）
_STALE_CANDIDATE_BRIDGES = ("armed", "fired")

# fired-but-unresolved 判定的 obligation 子集（WU-22-09：「fired 但
# obligation 仍 wake_required/degraded」——唤醒已触发但控制回路义务
# 未被对账消费。词汇与 runtime.state.CONTINUATION_OBLIGATIONS 一致，
# 一致性由 tests.test_manifest_quota_control 锚定）
_FIRED_UNRESOLVED_OBLIGATIONS = ("wake_required", "degraded")

# 摘要条目条件增补键 "continuity" 的触发状态（沿用 quota_wait 第九键
# 的条件模式：waiting_quota / waiting_user 任务恒携带——恢复这类任务
# 恰恰最需要桥接对账与安全动作指引）
_CONTINUITY_BLOCK_TASK_STATUSES = QUOTA_WAIT_TASK_STATUSES

# wake_bridge.status → 中文短语（渲染 WAKE BRIDGE 行；缺省项之外的
# 未知值原样透串显示——形状纠错归 validate_state，展示不猜测）
_WAKE_BRIDGE_STATUS_TEXT = {
    "none": "未建置",
    "requested": "已请求（requested）",
    "armed": "已布防（armed）",
    "fired": "已触发（fired）",
    "retarget_required": "需改期（retarget_required）",
    "degraded": "已降级（degraded）",
    "paused": "已暂停（paused）",
    "cancelled": "已取消（cancelled）",
    "stale": "已失联（stale）",
    "failed": "已失败（failed）",
}

# 四行展示块逐字键（WU-22-09 冻结，测试锚定）
CONTINUITY_BLOCK_KEYS = ("WAKE BRIDGE", "QUOTA PHASE", "AUTO RESUME",
                         "NEXT SAFE ACTION")


# —— 摘要构建（纯本地只读） ——

def _latest_recommended_resume_at(repo_root, task_id):
    """读最近一条 quota_waiting 事件的 recommended_resume_at（无 → None）。

    wu-21-11：recommended_resume_at 是 handle_quota_exhausted 落进
    journal 的事实（quota_waiting 事件），本函数按文件序倒序取第一条
    该事件；值缺失 / 非非空 str（当初 reset 不可解析不虚构为 None）→
    None。纯本地只读。
    """
    for event in reversed(journal.read_events(repo_root, task_id)):
        if isinstance(event, dict) and event.get("event") == "quota_waiting":
            value = event.get("recommended_resume_at")
            return value if isinstance(value, str) and value else None
    return None


# —— v2.2 M9 桥接对账（wu-22-09，纯函数只读） ——

def _parse_iso_utc(text):
    """ISO8601 时刻文本 → aware datetime（UTC）；非 str / 不可解析 → None。

    与 runtime.quota.scheduler._parse_iso_utc 同款判定（本地复制，不在
    模块间引用私有函数——同 runtime.state._is_iso8601 的本地化纪律）：
    兼容结尾 Z/z 后缀（先归一为 +00:00），naive 时刻按 UTC 处理。
    """
    if not isinstance(text, str) or text == "":
        return None
    raw = text.strip()
    if raw.endswith("Z") or raw.endswith("z"):
        raw = raw[:-1] + "+00:00"
    try:
        moment = datetime.datetime.fromisoformat(raw)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=datetime.timezone.utc)
    return moment


def _normalize_now(now):
    """归一 now 注入参数：None → 当前 UTC；datetime / ISO 串 → 时刻。

    now 仅供测试注入当前时刻（桥接对账的时间数学可锚定）；ISO 串
    不可解析 → None 时刻不可知（调用方按「无法判定」处理，不虚构）。
    """
    if now is None:
        return datetime.datetime.now(datetime.timezone.utc)
    if isinstance(now, datetime.datetime):
        if now.tzinfo is None:
            return now.replace(tzinfo=datetime.timezone.utc)
        return now
    return _parse_iso_utc(now)


def _effective_bridge_interval(task_state, wake_bridge):
    """桥接对账用的生效间隔分钟数（容错读，恒返回 5-1440 的 int）。

    wake_bridge.bridge_interval_minutes（D15-e 记账键，桥建置时写入的
    实际间隔）合法（5-1440 非 bool int）→ 原值；缺失 / 形状异常 →
    回退 execution_policy.quota_control.bridge_interval_minutes 容错读
    （default_quota_control，缺省 60——D15-d 保守默认单一真相源）。
    """
    interval = wake_bridge.get("bridge_interval_minutes") \
        if isinstance(wake_bridge, dict) else None
    if not isinstance(interval, bool) and isinstance(interval, int) \
            and 5 <= interval <= 1440:
        return interval
    policy = task_state.get("execution_policy") \
        if isinstance(task_state, dict) else None
    return default_quota_control(policy)["bridge_interval_minutes"]


def reconcile_wake_bridge(task_state, *, now=None):
    """对账 continuation.wake_bridge（v2.2 M9 wu-22-09）——纯函数只读。

    从任务状态 dict 容错投影桥接事实并产出两个机械判定（stale /
    fired-but-unresolved）；不写 state、不触碰 automation、零网络
    （判定即展示信号，状态推进归 M5/M8——本层绝不越权改账）。

    参数：
      - task_state：runtime.state.load_state 的结果 dict（legacy 缺
        continuation 块 / 半块 / 形状异常一律按默认块解释，绝不抛）；
      - now：参考时刻注入（None → 当前 UTC；datetime / ISO8601 串），
        仅供测试锚定 stale 时间数学。

    返回（键冻结，七键）：
      {"bridge_status": <wake_bridge.status（缺省 "none"，未知值透传）>,
       "obligation": <continuation.obligation（缺省 "none"）>,
       "automation_id": <str 或 None>,
       "next_wake_at": <str 或 None>,
       "interval_minutes": <生效间隔分钟数（5-1440，见
                           _effective_bridge_interval 回退口径）>,
       "stale": <bool>——bridge status ∈ {armed, fired} 且
                next_wake_at 可解析且 now 已过
                next_wake_at + STALE_BRIDGE_INTERVAL_MULTIPLIER ×
                interval_minutes（错过两拍以上仍未触发 → 存活性可疑；
                next_wake_at 缺失 / 不可解析 → False，不虚构时刻），
       "fired_unresolved": <bool>——bridge status == "fired" 且
                obligation ∈ {wake_required, degraded}（唤醒已触发但
                义务未被对账消费）}
    """
    continuation = task_state.get("continuation") \
        if isinstance(task_state, dict) else None
    continuation = continuation if isinstance(continuation, dict) else {}
    wake_bridge = continuation.get("wake_bridge")
    wake_bridge = wake_bridge if isinstance(wake_bridge, dict) else {}

    bridge_status = wake_bridge.get("status")
    if bridge_status is None:
        bridge_status = "none"
    obligation = continuation.get("obligation")
    if obligation is None:
        obligation = "none"
    automation_id = wake_bridge.get("automation_id")
    automation_id = automation_id \
        if isinstance(automation_id, str) and automation_id else None
    next_wake_at = wake_bridge.get("next_wake_at")
    next_wake_at = next_wake_at \
        if isinstance(next_wake_at, str) and next_wake_at else None

    interval = _effective_bridge_interval(task_state, wake_bridge)

    stale = False
    if bridge_status in _STALE_CANDIDATE_BRIDGES:
        moment = _normalize_now(now)
        wake_moment = _parse_iso_utc(next_wake_at)
        if moment is not None and wake_moment is not None:
            threshold = wake_moment + datetime.timedelta(
                minutes=interval * STALE_BRIDGE_INTERVAL_MULTIPLIER)
            stale = moment > threshold

    fired_unresolved = (bridge_status == "fired"
                        and obligation in _FIRED_UNRESOLVED_OBLIGATIONS)

    return {
        "bridge_status": bridge_status,
        "obligation": obligation,
        "automation_id": automation_id,
        "next_wake_at": next_wake_at,
        "interval_minutes": interval,
        "stale": stale,
        "fired_unresolved": fired_unresolved,
    }


def _has_continuity_facts(loaded, status):
    """任务是否携带控制回路事实（条件增补键 "continuity" 的触发判定）。

    四种触发（任一即真）：
      - state.quota.execution_phase 已回填（M4 D13 prepare 时点写入的
        非空 str）；
      - continuation.obligation 非 none / 缺省（存在任何义务态）；
      - continuation.wake_bridge.status 非 none / 缺省（桥已有事实）；
      - 任务 status ∈ waiting_quota / waiting_user（恢复这类任务恰
        最需要桥接对账与安全动作指引）。
    legacy v2.1 任务（无 continuation / quota 块）→ False：零增键
    零增行，优雅降级为安静（既有八字段冻结形状逐字不变）。
    """
    quota = loaded.get("quota")
    phase = quota.get("execution_phase") if isinstance(quota, dict) else None
    if isinstance(phase, str) and phase:
        return True
    continuation = loaded.get("continuation")
    if isinstance(continuation, dict):
        if continuation.get("obligation") not in (None, "none"):
            return True
        bridge = continuation.get("wake_bridge")
        if isinstance(bridge, dict) \
                and bridge.get("status") not in (None, "none"):
            return True
    return status in _CONTINUITY_BLOCK_TASK_STATUSES


def _continuity_view(loaded, *, now):
    """组装摘要条目的 "continuity" 增补键（键冻结，十键恒存在）。

    reconcile_wake_bridge 的七键全量并入（桥接事实 + stale /
    fired_unresolved 判定）+ 恢复面缺口三键：execution_phase（
    state.quota.execution_phase 容错读，缺 → None）与
    consumed_quota_windows / max_quota_windows（窗口预算，容错读，
    非法形状按 0 保守解释）。纯函数：只读入参、零 I/O。
    """
    verdict = reconcile_wake_bridge(loaded, now=now)
    quota = loaded.get("quota")
    execution_phase = (quota.get("execution_phase")
                       if isinstance(quota, dict) else None)
    policy = loaded.get("execution_policy")
    continuity = (policy.get("continuity")
                  if isinstance(policy, dict) else None)
    max_windows = (continuity.get("max_quota_windows")
                   if isinstance(continuity, dict) else None)
    if isinstance(max_windows, bool) or not isinstance(max_windows, int) \
            or max_windows < 0:
        max_windows = 0
    view = dict(verdict)
    view["execution_phase"] = execution_phase
    view["consumed_quota_windows"] = consumed_quota_windows(policy)
    view["max_quota_windows"] = max_windows
    return view


def _summarize_task(repo_root, task_id, loaded, now=None):
    """把单个活动任务的状态 dict 聚合为摘要条目（冻结八字段）。

    单元三分（互不排斥：interrupted 是 incomplete 的子集）：
      - completed_units：status == "completed" 的单元 id；
      - incomplete_units：其余全部非 completed 单元 id（含 failed /
        blocked / waiting_quota / running / verifying / pending /
        cancelled 等——恢复视角下「未 completed 即未收口」）；
      - interrupted_units：status ∈ INTERRUPTED_UNIT_STATUSES 的单元，
        每项 {"id", "run_lifecycle"}；run_lifecycle 为
        agent_run.run_lifecycle 结果，该单元无任何 agent run 记录时
        置 None（空账本不值得展开）。

    auto_resume：execution_policy.continuity.auto_resume（授权事实源）；
    块缺失 / 形状异常 / 值非法（非非空 str）→ legacy 兜底 "manual"。
    repo_root：resolve_repository_root(loaded, None)——绑定优先，
    无绑定（legacy 形态）为 None。

    quota_wait（第九键，wu-21-11 增补）：仅 status ∈
    QUOTA_WAIT_TASK_STATUSES（waiting_quota / waiting_user）的任务
    携带 {"recommended_resume_at", "remaining_windows"}——前者取最近
    一条 quota_waiting 事件（journal 事实源，无事件 / 当初不虚构为
    None），后者 = max(0, continuity.max_quota_windows -
    consumed_quota_windows) 容错折算；其余状态整键省略（既有八字段
    冻结形状逐字不变——test_recovery 的冻结形状契约依赖于此）。

    continuity（第十键，v2.2 M9 wu-22-09 增补）：仅携带控制回路事实
    的任务携带（_has_continuity_facts 四触发之一：execution_phase
    已回填 / obligation 非 none / bridge status 非 none / 任务在
    waiting_quota / waiting_user），值为 _continuity_view 十键 dict
    （reconcile_wake_bridge 七键 + execution_phase + 窗口预算两键）；
    legacy v2.1 任务与无事实任务整键省略（冻结形状契约同上）。
    now：桥接对账参考时刻注入（None → 当前 UTC），仅供测试。
    """
    work_units = loaded.get("work_units")
    units = work_units if isinstance(work_units, list) else []
    completed_units = []
    incomplete_units = []
    interrupted_units = []
    for unit in units:
        if not isinstance(unit, dict):
            continue
        uid = unit.get("id")
        if not isinstance(uid, str) or uid == "":
            continue
        unit_status = unit.get("status")
        if unit_status == "completed":
            completed_units.append(uid)
        else:
            incomplete_units.append(uid)
        if unit_status in INTERRUPTED_UNIT_STATUSES:
            lifecycle = agent_run.run_lifecycle(repo_root, task_id, uid)
            interrupted_units.append({
                "id": uid,
                "run_lifecycle": lifecycle if lifecycle["runs"] else None,
            })

    policy = loaded.get("execution_policy")
    continuity = policy.get("continuity") \
        if isinstance(policy, dict) else None
    auto_resume = continuity.get("auto_resume") \
        if isinstance(continuity, dict) else None
    if not isinstance(auto_resume, str) or auto_resume == "":
        auto_resume = LEGACY_AUTO_RESUME

    goal = loaded.get("goal")
    if not isinstance(goal, str):
        goal = ""

    task_id_normalized = loaded.get("task_id")
    if not isinstance(task_id_normalized, str) or task_id_normalized == "":
        task_id_normalized = task_id

    # quota_wait 增补（wu-21-11）：仅额度等待态任务携带该键（其余状态
    # 整键省略——既有八字段冻结形状不变）；剩余窗口预算 =
    # max(0, max_quota_windows - consumed_quota_windows)（容错读，
    # legacy / 形状异常按 0 折算）
    entry = {
        "task_id": task_id_normalized,
        "goal": goal[:GOAL_TRUNCATE],
        "status": loaded.get("status"),
        "repo_root": state.resolve_repository_root(loaded, None),
        "completed_units": completed_units,
        "incomplete_units": incomplete_units,
        "interrupted_units": interrupted_units,
        "auto_resume": auto_resume,
    }
    if loaded.get("status") in QUOTA_WAIT_TASK_STATUSES:
        policy_block = loaded.get("execution_policy")
        continuity = (policy_block.get("continuity")
                      if isinstance(policy_block, dict) else None)
        max_windows = (continuity.get("max_quota_windows")
                       if isinstance(continuity, dict) else None)
        if isinstance(max_windows, bool) or not isinstance(max_windows, int) \
                or max_windows < 0:
            max_windows = 0
        entry["quota_wait"] = {
            "recommended_resume_at":
                _latest_recommended_resume_at(repo_root, task_id),
            "remaining_windows":
                max(0, max_windows - consumed_quota_windows(policy_block)),
        }
    # continuity 增补（v2.2 M9 wu-22-09）：仅携带控制回路事实的任务
    # 携带第十键（条件模式与 quota_wait 一致——既有冻结形状不受扰）
    if _has_continuity_facts(loaded, loaded.get("status")):
        entry["continuity"] = _continuity_view(loaded, now=now)
    return entry


def build_recovery_summary(repo_root, *, now=None):
    """扫描账本根并聚合 SessionStart 恢复摘要（纯本地只读，零写副作用）。

    返回冻结形状：
      {"tasks": [ 摘要条目（_summarize_task 八字段，条件增补
                  quota_wait 第九键 / continuity 第十键——见
                  _summarize_task docstring）...按 task_id 排序，
                  多任务全列（render 层负责截断） ],
       "corrupt": [task_id...]（discover corrupt 桶目录名，按名排序）}

    now：桥接对账参考时刻注入（None → 当前 UTC），仅供测试锚定
    stale 判定的时间数学。

    无任何任务（tasks_root 不存在 / 全为终态）→ {"tasks": [], "corrupt": []}
    （render 据此返回 ""，钩子完全静默）。单任务在扫描与读取之间损坏 /
    被移除（load_state 抛 ValueError / OSError）→ 跳过该任务，不中断
    整体发现（宁可少报也不炸恢复路径）；corrupt 桶由 discover_tasks
    显式报告，不在本函数二次补救。
    """
    discovery = state.discover_tasks(repo_root)
    tasks = []
    for dir_task_id, _status in discovery["active"]:
        try:
            loaded = state.load_state(repo_root, dir_task_id)
        except (ValueError, OSError):
            # 状态文件在扫描与读取之间损坏 / 被移除 → 跳过该任务
            continue
        if not isinstance(loaded, dict):
            continue
        tasks.append(_summarize_task(repo_root, dir_task_id, loaded, now=now))
    tasks.sort(key=lambda entry: entry["task_id"])
    return {
        "tasks": tasks,
        "corrupt": sorted(name for name, _reason in discovery["corrupt"]),
    }


# —— resume context 渲染（英文模板，§8.3 形态） ——

def _render_unit_list(label, unit_ids):
    """渲染一个单元列表段（"Label:" + 缩进条目），超 MAX_RENDERED_UNITS
    截断并追加 omitted 计数行；空列表渲染 "none"。"""
    unit_ids = [uid for uid in unit_ids if isinstance(uid, str) and uid] \
        if isinstance(unit_ids, list) else []
    lines = ["%s:" % label]
    if not unit_ids:
        lines.append("  none")
        return lines
    shown = unit_ids[:MAX_RENDERED_UNITS]
    for uid in shown:
        lines.append("  %s" % uid)
    omitted = len(unit_ids) - len(shown)
    if omitted > 0:
        lines.append("  (%d more %s omitted)"
                     % (omitted, label.lower()))
    return lines


def _render_task(entry):
    """渲染单个任务的段落（§8.3 逐段形态，英文）。

    段序冻结：Task / Repository / Goal / Status / Completed units /
    Interrupted units / Waiting / Quota-resume authorization。
    Waiting = incomplete_units 去掉 interrupted 的 id（挂起等依赖 /
    待派发的单元）；interrupted 单元逐个列出，possibly_zombie=True 时
    追加逐字僵尸标注行。
    wu-21-11 增补（仅 quota_wait 携带任务，即 waiting_quota /
    waiting_user）：其后追加 Quota wait（recommended_resume_at + 剩余
    窗口预算）与 Resume first step（resume_from_quota 指引）两行。
    v2.2 M9 增补（wu-22-09，仅 continuity 键携带任务）：段末追加四行
    展示块（WAKE BRIDGE / QUOTA PHASE / AUTO RESUME / NEXT SAFE
    ACTION，逐字键冻结，值各一行中文短语）——stale /
    fired-but-unresolved 判定注入对应行值；无 continuity 键的任务
    零增行（既有渲染逐字不变，只增块不删行）。
    """
    lines = ["", "Task: %s" % entry.get("task_id")]
    repo = entry.get("repo_root")
    lines.append("Repository: %s"
                 % (repo if isinstance(repo, str) and repo else "not bound"))
    goal = entry.get("goal")
    lines.append("Goal: %s"
                 % (goal if isinstance(goal, str) and goal else "(no goal)"))
    lines.append("Status: %s" % entry.get("status"))
    lines.extend(_render_unit_list("Completed units",
                                   entry.get("completed_units")))

    interrupted = entry.get("interrupted_units")
    interrupted = interrupted if isinstance(interrupted, list) else []
    interrupted = [item for item in interrupted if isinstance(item, dict)]
    lines.append("Interrupted units:")
    if not interrupted:
        lines.append("  none")
    shown_interrupted = interrupted[:MAX_RENDERED_UNITS]
    for item in shown_interrupted:
        lines.append("  %s" % item.get("id"))
        lifecycle = item.get("run_lifecycle")
        if isinstance(lifecycle, dict) and lifecycle.get("possibly_zombie"):
            lines.append("    %s" % ZOMBIE_NOTE)
    omitted = len(interrupted) - len(shown_interrupted)
    if omitted > 0:
        lines.append("  (%d more interrupted units omitted)" % omitted)

    interrupted_ids = {item.get("id") for item in interrupted}
    waiting = [uid for uid in entry.get("incomplete_units") or []
               if uid not in interrupted_ids]
    lines.extend(_render_unit_list("Waiting", waiting))
    lines.append("Quota-resume authorization: %s" % entry.get("auto_resume"))

    # 额度等待任务增补（wu-21-11）：recommended_resume_at + 剩余窗口
    # 预算 + resume_from_quota 恢复指引一句；非等待态任务零增行
    # （既有渲染逐字不变）
    quota_wait = entry.get("quota_wait")
    if isinstance(quota_wait, dict):
        recommended = quota_wait.get("recommended_resume_at")
        remaining = quota_wait.get("remaining_windows")
        lines.append(
            "Quota wait: recommended_resume_at=%s; remaining wake budget: "
            "%s window(s)" % (recommended if isinstance(recommended, str)
                              and recommended else "unknown",
                              remaining if isinstance(remaining, int)
                              and not isinstance(remaining, bool)
                              else 0))
        lines.append(
            "Resume first step: resume_from_quota "
            "(task_manager.resume_from_quota) after quota re-check; "
            "AVAILABLE/PRESSURE resume, EXHAUSTED/UNKNOWN wait")

    # continuity 四行展示块（v2.2 M9 wu-22-09）：仅携带控制回路事实的
    # 任务追加（逐字键 WAKE BRIDGE / QUOTA PHASE / AUTO RESUME /
    # NEXT SAFE ACTION，值各一行中文短语）；无 continuity 键零增行
    lines.extend(_render_continuity_block(entry))
    return lines


def _next_safe_action(view, entry):
    """NEXT SAFE ACTION 行值（中文一句话，按对账信号优先级取舍）。

    优先级（先处置异常信号，再按额度相与任务态给常规指引）：
      1 stale bridge——bridge 存活性可疑，先人工核验，勿依赖自动唤醒；
      2 fired-but-unresolved——唤醒已触发但义务未消，先对账再续作；
      3 execution_phase BLOCKED / DRAINING——额度冻结 / 排水纪律
        （词汇与 runtime.quota.control.EXECUTION_PHASES 一致，一致性
        由 tests.test_manifest_quota_control 锚定）；
      4 waiting_quota / waiting_user——等额度 / 等授权的恢复入口；
      5 bridge armed（健康）——等待唤醒或按授权续派；
      6 缺省——按恢复清单推进。
    """
    if view.get("stale"):
        return ("唤醒桥疑似失联：先核验 automation 存活（CronList），"
                "失效则记录 stale 并重建/改期，勿直接依赖自动唤醒")
    if view.get("fired_unresolved"):
        return ("唤醒已触发但续跑义务未消：先强刷额度（quota-resolve "
                "--force-refresh）并 reconcile 中断单元，再续派")
    phase = view.get("execution_phase")
    if phase == "BLOCKED":
        return ("额度已冻结（BLOCKED）：派发与收尾一律暂停，等待额度"
                "重置并强刷确认后再恢复")
    if phase == "DRAINING":
        return ("额度排水中（DRAINING）：勿派新实施单元，仅允许收尾"
                "（join/verify/review/checkpoint/wake）")
    if entry.get("status") in QUOTA_WAIT_TASK_STATUSES:
        return ("任务在等额度/等授权：额度窗口重置后经 resume_from_quota "
                "恢复（先强刷额度并核对授权）")
    if view.get("bridge_status") == "armed":
        return ("唤醒桥已布防：等待下次唤醒，或按授权在额度允许时经 "
                "prepare_dispatch 续派")
    return "按恢复清单推进：reconcile 后经 prepare_dispatch 续派下一安全单元"


def _render_continuity_block(entry):
    """渲染四行 continuity 展示块（v2.2 M9 wu-22-09，逐字键冻结）。

    entry 无 "continuity" 键（legacy 任务 / 无控制回路事实）→ 空列表
    （零增行）；有 → 恰四行，每行 "逐字键: 中文短语"，stale /
    fired_unresolved 判定注入 WAKE BRIDGE 行值并联动 NEXT SAFE ACTION。
    纯函数：只读入参（手工构造摘要 dict 的测试同享此路径）。
    """
    view = entry.get("continuity")
    if not isinstance(view, dict):
        return []

    # —— WAKE BRIDGE 行：状态短语 + automation + 下次唤醒 + 判定标注 ——
    bridge_status = view.get("bridge_status")
    segment = _WAKE_BRIDGE_STATUS_TEXT.get(
        bridge_status, str(bridge_status) if bridge_status else "未建置")
    automation_id = view.get("automation_id")
    if isinstance(automation_id, str) and automation_id:
        segment += "，automation=%s" % automation_id
    next_wake_at = view.get("next_wake_at")
    if isinstance(next_wake_at, str) and next_wake_at:
        segment += "，下次唤醒 %s" % next_wake_at
    if view.get("stale"):
        segment += ("——疑似失联：已过下次唤醒超过 %d×间隔（%s 分钟）"
                    "仍未触发"
                    % (STALE_BRIDGE_INTERVAL_MULTIPLIER,
                       view.get("interval_minutes")))
    elif view.get("fired_unresolved"):
        segment += ("——已触发但续跑义务未消（obligation=%s），唤醒"
                    "未被对账消费" % view.get("obligation"))

    # —— QUOTA PHASE 行：M4 prepare 回填的执行相；无记录不虚构 ——
    phase = view.get("execution_phase")
    phase_text = phase if isinstance(phase, str) and phase \
        else "未知（prepare 时点回填，尚无记录）"

    # —— AUTO RESUME 行：授权词汇 + 窗口预算（max>0 才展示 0/0 之外的
    # 记账，manual 零预算只给授权短语）——
    auto_resume = entry.get("auto_resume")
    if not isinstance(auto_resume, str) or auto_resume == "":
        auto_resume = LEGACY_AUTO_RESUME
    consumed = view.get("consumed_quota_windows")
    max_windows = view.get("max_quota_windows")
    if isinstance(max_windows, int) and not isinstance(max_windows, bool) \
            and max_windows > 0:
        auto_text = "%s（窗口预算已用 %s/%s）" % (
            auto_resume,
            consumed if isinstance(consumed, int)
            and not isinstance(consumed, bool) else 0,
            max_windows)
    else:
        auto_text = "%s（不自动跨额度窗口续跑）" % auto_resume

    return [
        "WAKE BRIDGE: %s" % segment,
        "QUOTA PHASE: %s" % phase_text,
        "AUTO RESUME: %s" % auto_text,
        "NEXT SAFE ACTION: %s" % _next_safe_action(view, entry),
    ]


def render_resume_context(summary):
    """把恢复摘要渲染为注入文本（英文，§8.3 模板形态）；无任务返回 ""。

    有界性（additionalContext 32KB stdout cap）：
      - 任务超过 MAX_RENDERED_TASKS(3) → 只列前 3 + "(N more task(s)
        omitted)" 行；
      - 每个单元列表超过 MAX_RENDERED_UNITS(8) → 截断 + omitted 计数；
      - corrupt 警告恒为单行（id 列表超 5 个以 +N more 截断）。
    尾部 Recommended 编号 1-4 逐字冻结（reconcile interrupted agent
    runs → inspect repository residue → retrieve prior progress if
    needed → continue via prepare_dispatch）。summary 无任务（含空
    仓库 / 全终态）→ 返回 ""（钩子据此完全静默，零噪音）。
    """
    tasks = summary.get("tasks") if isinstance(summary, dict) else None
    tasks = [task for task in tasks if isinstance(task, dict)] \
        if isinstance(tasks, list) else []
    if not tasks:
        return ""

    lines = [RESUME_HEADER]
    shown_tasks = tasks[:MAX_RENDERED_TASKS]
    for entry in shown_tasks:
        lines.extend(_render_task(entry))
    omitted_tasks = len(tasks) - len(shown_tasks)
    if omitted_tasks > 0:
        lines.append("(%d more task(s) omitted)" % omitted_tasks)

    lines.append("")
    lines.append("Recommended:")
    lines.append("  1. reconcile interrupted agent runs")
    lines.append("  2. inspect repository residue")
    lines.append("  3. retrieve prior progress if needed")
    lines.append("  4. continue via prepare_dispatch")

    corrupt = summary.get("corrupt") if isinstance(summary, dict) else None
    corrupt = [name for name in corrupt if isinstance(name, str) and name] \
        if isinstance(corrupt, list) else []
    if corrupt:
        shown_ids = corrupt[:MAX_CORRUPT_IDS_SHOWN]
        suffix = ""
        if len(corrupt) > len(shown_ids):
            suffix = ", +%d more" % (len(corrupt) - len(shown_ids))
        lines.append(
            "WARNING: %d corrupt task state file(s) detected (%s%s); "
            "inspect .glm-conductor/tasks/ before relying on recovery."
            % (len(corrupt), ", ".join(shown_ids), suffix))

    return "\n".join(lines)
