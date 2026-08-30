#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GLM Conductor v2 任务图恢复对账辅助层（v2 工作块 B8.4，§68-§69）。

职责：
    中断（会话结束 / 崩溃 / 额度耗尽）后，任务图中 running / verifying
    状态的单元既不能假设「完成」（worker 报告只是 claim，§70），也不能
    盲目假设「须重跑」（改动可能已在仓库且已验证）。本模块按 §69 的
    证据顺序给出裁决输出——纯建议、零落盘、不修改传入 units：
      - suggestions：可直接 transition_work_unit 应用的转换建议
        （每条都落在 §62 转换表内）；
      - advisories：无需转换的裁决提示（只有 reason、无 to——没有
        可机械应用的转换，裁决归主会话）。
    转换的应用归调用方（work_unit.transition_work_unit，见应用示例）。

completed 不重跑锚定（§68）：
    本模块只对 status ∈ ("running", "verifying") 的单元产出建议；
    completed / failed / cancelled 等其他状态零建议。completed 单元
    不重跑——除非仓库证据否定（其 owned 文件随后又被改动），而该
    否定判断是主会话的职责，不属于本模块：reconcile 不判断 completed
    是否失效，也不对非中断状态出任何建议。

证据顺序（§69 四步证据 → 分状态裁决，逐中断单元）：
    1. 仓库证据：ownership.classify_paths(touched, unit["ownership"])
       得 owned_hits——单元 ownership 声明范围内的残留改动清单；
    2. 无残留改动（owned_hits 为空）→ running 建议 ready（干净重派：
       工作区无该单元范围内的任何残留，重派成本最低）；verifying 无
       表内合法目标（§62 转换表：verifying 仅可达 completed / failed /
       cancelled，verifying→ready 非法）→ 只出 advisory 裁决提示
       （重跑验证或按失败处理，归主会话裁决）；
    3. 有残留改动 → journal 指纹证据：fingerprint.compute_fingerprint
       对 owned_hits 真算当前指纹，再经共享谓词 fresh_unit_verification
       （RB-1 / WU-P1）校验——unit["verification"] 内每个 required
       command 须各存在至少一条满足全部条件的 verification 事件
       （all-match，v2.0.1 有意收紧：多 command 单元须全部有新鲜证据
       才建议 completed，部分证据按无证据处理；release hardening
       计划 D2 决策）：
         event == "verification" 且 unit == 该单元 id（逐字精确）且
         fingerprint == 当前指纹 且 status == "pass" 且 command ==
         该 required command
       - 全部命中 → 建议 completed（每个 required command 都有绑定
         当前改动的新鲜验证证据——parent-observed 恢复解释，最终
         裁决待主会话确认）；running 与 verifying 同此（verifying→
         completed 在转换表内）；
       - 任一缺失 → running 建议 verifying（残留改动无新鲜验证证据：
         主会话必须亲自检查 diff 并运行单元验证——§70 claim 不算
         完成）；verifying 保持现状属自转换（verifying→verifying
         非法）→ 只出 advisory 裁决提示（主会话直接运行单元验证
         后完成或失败）；
    4. blocked 不在本模块建议词汇内——「检查失败」的裁决（failed /
       blocked 等）是主会话的职责，模块只依据仓库 + journal 证据出
       建议，绝不替主会话判死。

证据归属绑定（v2.0.1 加固 H6，审查项 P1-7）：
    verification 事件必须显式携带 unit 字段（work unit id），且与被
    对账单元逐字精确相等——相同 command / 重叠 ownership 的单元之间
    不存在错误复用证据的空间（A 单元的证据不能证明 B 单元的改动）。
    单元级证据的推荐写入口是 task_manager.record_unit_verification
    （事件形状 {"event": "verification", "unit", "command", "status",
    "fingerprint"}）。**不含 unit 字段的旧格式事件不再匹配**——保守：
    证据无法证明归属时按无证据处理，主会话重新验证；这是 v2.0.1
    有意的破坏性变更，已在 continuity SKILL 与架构文档明示。

返回（确定性，suggestions / advisories / reconciled 键序均按 units
出现序）：
    {"suggestions": {uid: {"to": <str>, "reason": ...}},
     "advisories": {uid: {"reason": ...}},
     "reconciled": [uid...], "touched": [...]}
      - suggestions：只含 to 非 None 的可应用建议（可对
        transition_work_unit 直接应用）；键为单元 id，插入序 =
        units 出现序（Python 3.7+ dict 保序）；
      - advisories：无需转换的裁决提示（只有 reason、无 to——没有
        可机械应用的转换，裁决归主会话）；
      - reconciled：全部被评估单元 id 列表（suggestions ∪ advisories
        的键全集，同出现序）；
      - touched：实际使用的改动清单（注入原样 / 缺省 git_touched_files
        结果），供调用方日志与复算。

纯建议纪律：
    reconcile 零落盘（不写 state / journal / 任何文件）、不修改传入
    units。应用示例（主会话侧——只对 suggestions 应用 transition，
    advisories 仅提示、不做转换）：
        report = reconcile_interrupted(repo, task_id, st["work_units"])
        by_id = {u["id"]: u for u in st["work_units"]}
        for uid, advice in report["suggestions"].items():
            work_unit.transition_work_unit(by_id[uid], advice["to"])
        for uid, hint in report["advisories"].items():
            ...  # 仅向主会话提示裁决（重跑验证 / 判失败），不转换
        state.save_state(repo, st)
    suggestions 全部落在 §62 转换表内（verifying 态表外情形一律改走
    advisories，不出表外建议）；应用侧转换闸门仍归 work_unit。

租约对账（v2.0.1 加固 H5，审查项 P1-4/P1-5）：
    reconcile_leases() 把任务图与落盘租约（runtime.lease）对读，把
    每条租约三分——
      - stale：owner 不是图中任何 running/verifying 单元（图中无此
        单元 / 单元在 completed / failed / cancelled / ready 等非活跃
        写相）→ 可被 task_manager.recover_leases 自动释放；
      - active：owner 为 running/verifying 单元且未过期 → 保留；
      - expired_running：owner 为 running/verifying 单元但已过期 →
        不建议自动释放（worker 可能仍在写，裁决归主会话）。
    纪律同 reconcile_interrupted：纯建议、零落盘、不修改入参。

错误上抛（调用方处理，可降级或转人工）：
    - touched 缺省时 git 失败 → ownership.OwnershipError 自然上抛；
    - 指纹计算失败 → fingerprint.FingerprintError 自然上抛；
    - ownership 声明模式非法 → classify_paths 的 OwnershipError 同样
      自然上抛（结构性错误不静默转换）；
    - events 显式注入非 list → 按 [] 容错（缺省通道 read_events 本就
      容错；注入通道是测试 / 回放后门，坏形状不炸对账）。
    （例外：reconcile_agent_run 是决策 API——求值异常不向调用方上抛，
    一律归入 manual_ruling，见下节。）

Agent Run 四分对账（v2.1 M3，计划 §9 / §7.3）：
    reconcile_agent_run() 是单单元四分分类纯读决策 API：基于机械证据
    （repo residue + 新鲜验证证据 + 原生档案 metadata + agent run
    账本）输出 reuse_result / resume_with_progress / redispatch_clean
    / manual_ruling 之一，附 rationale 与 evidence 证据视图——崩溃后
    主会话据此决定「捞结果不重派 / 进度包续作 / 全新派发 / 人工裁决」，
    而不是一律重派（dogfood 实录：崩溃后无 reconcile 消费原生记录，
    续作会话把已完成单元全部重做）。两层分工（※DR）：本 API 只产出
    「分类 + 证据句柄」；resume_with_progress 的进度包组装（旧
    transcript 摘要、owned diff 内容级重建）由模型侧经
    ReadSessionContext 读取子会话 transcript 完成——本模块绝不复制
    transcript（§7.2）。僵尸语义（§7.3）：档案 metadata.status 绝不是
    truth——observed_status == "running" 只触发 rationale 的僵尸语义
    句，绝不据此判存活。证据优先级（§7.3）体现为判定树顺序：
    repository state（residue）最先分流，其次 verification evidence，
    再次 native 档案。单元定位：只读解析任务 state.json 的 work_units
    （目录经 journal.journal_path 派生，不导入 runtime.state /
    task_manager）；state 缺失 / 损坏 / 无此单元与 git 求值异常
    （OwnershipError / FingerprintError）一律 manual_ruling（rationale
    注明求值失败，residue 按无 residue 处理）——决策 API 恒返回冻结
    形状。纯读纪律同上：零 journal / state / 档案写入，零状态转换，
    零网络。

依赖：
    runtime.ownership / runtime.fingerprint / runtime.journal（缺省
    证据来源）、runtime.lease（H5 租约对读——expired_leases 过期集
    + lease_state 明细）、runtime.agent_run（v2.1 M3：agent run 账本
    list_agent_runs 与原生档案只读 adapter native_agent_metadata）
    + 标准库 json（v2.1 M3：任务 state.json 只读解析）。不导入
    runtime.work_unit（建议层不依赖转换层——应用示例里的
    transition_work_unit 由调用方导入）、不导入 runtime.state /
    runtime.task_manager（单元定位经 journal 任务目录只读解析
    state.json；task_manager 反向导入本模块，无循环导入）。
    仅 Python 3 标准库，`python3 -S` 可运行。

来源：
    docs/glm-conductor-v2-upgrade-guide-final.md §68（恢复后 completed
    不重跑）/ §69（中断单元恢复证据四步）+ v2 升级计划工作块 B8.4
    + v2.0.1 加固工作包 H5（P1-4/P1-5，租约对账）与 H6（P1-7，
    verification 证据归属绑定 unit）+ release hardening 补丁计划
    docs/GLM-Conductor-v2.0.1-Release-Hardening-Patch-Agent-Implementation-Plan.md
    （RB-1 / WU-P1：共享证据谓词 fresh_unit_verification + all-match
    收紧，D2 决策）+ v2.1 M3 计划
    docs/GLM-Conductor-v2.1-Architecture-Agent-Implementation-Plan.md
    §7.3（证据优先级与僵尸语义）/ §9（Agent Reconcile 四分模型，
    ※DR 拆两层修订：runtime 产分类 + 证据句柄，进度包组装归模型侧）。
"""

import json

from runtime import agent_run
from runtime import fingerprint
from runtime import journal
from runtime import lease
from runtime import ownership

# 中断对账的目标状态集合：只有处于这两个状态的单元才产出建议（§68）
INTERRUPTED_STATUSES = ("running", "verifying")

# running 态三分支建议（§69；reason 逐字锁定，供调用方 / 测试锚定语义）
SUGGEST_READY = {"to": "ready",
                 "reason": "无 ownership 内残留改动，干净重派"}
SUGGEST_COMPLETED = {
    "to": "completed",
    "reason": "存在绑定当前改动的新鲜验证证据（parent-observed），"
              "待主会话确认"}
SUGGEST_VERIFYING = {
    "to": "verifying",
    "reason": "残留改动无新鲜验证证据：主会话必须亲自检查 diff 并运行"
              "单元验证"}

# verifying 态 no-op 裁决提示（不携带转换——verifying 的 §62 表内目标
# 仅 completed / failed / cancelled；reason 逐字锁定，供测试锚定语义）
ADVISORY_VERIFYING_RULING = {
    "reason": "verifying 无残留改动且无新鲜验证证据：主会话裁决——"
              "重跑验证或按失败处理"}
ADVISORY_VERIFYING_STAY = {
    "reason": "verifying 保持现状：主会话直接运行单元验证后完成或失败"}


def fresh_unit_verification(repo_root, task_id, unit, *, touched=None,
                            events=None) -> dict:
    """单元验证证据纯判断谓词（v2.0.1 RB-1 / WU-P1，all-match 口径）。

    判断 unit["verification"] 内每个 required command 是否各存在至少
    一条五条件全满足的新鲜 pass 证据（all-match：任一缺失即不完整，
    多 command 单元不得凭部分证据过关——D2 决策，v2.0.1 有意收紧）。
    与 task_manager.record_unit_verification 的写入口径配套，供
    finish_unit 完成门与本模块恢复对账共用同一套证据谓词（本模块
    不导入 task_manager——后者已导入本模块，避免循环导入）。

    返回（确定性）：
        {"ok": bool, "fingerprint": str | None, "required": [str],
         "matched": [str], "missing": [str]}
      - ok：required 全部命中（空 required 平凡成立）；
      - fingerprint：证据比对的当前指纹（快路径未算 → None）；
      - required / matched / missing：均按 unit["verification"] 原序。

    五条件（与恢复对账同一口径，H6 起）：event == "verification"、
    unit == 单元 id（逐字精确，无 unit 字段的旧格式事件不匹配）、
    fingerprint == 当前指纹（对 owned_hits 真算——空 owned_hits 也算，
    指纹仍绑定基线修订，与 _interrupted_advice 原口径一致）、
    status == "pass"、command == 该 required command。

    快路径：unit["verification"] 非 list/tuple 视为空 required → 立即
    返回 ok=True（fingerprint=None）——不取 touched、不读 journal、
    不碰 git（下游完成门依赖此性质：零 required 的完成路径零 git）。

    参数容错（与 reconcile_interrupted 同口径）：touched 缺省 →
    ownership.git_touched_files（git 失败 OwnershipError 自然上抛）；
    events 缺省 → journal.read_events(repo_root, task_id)；注入非
    list → 按 [] 容错；ownership 声明模式非 list/tuple → []；指纹
    计算失败 FingerprintError 自然上抛。

    纯函数纪律：零落盘（不写 state / journal / 任何文件）、不修改
    传入 unit / events / touched。
    """
    uid = unit.get("id")
    required = unit.get("verification")
    if not isinstance(required, (list, tuple)):
        required = ()
    if not required:  # 快路径：零 required 平凡成立，零 git / 零读盘
        return {"ok": True, "fingerprint": None, "required": [],
                "matched": [], "missing": []}
    patterns = unit.get("ownership")
    if not isinstance(patterns, (list, tuple)):
        patterns = []
    if touched is None:
        touched = ownership.git_touched_files(repo_root)
    if events is None:
        events = journal.read_events(repo_root, task_id)
    elif not isinstance(events, list):
        events = []  # 注入通道容错：坏形状不炸判断（对账同口径）
    owned_hits, _ = ownership.classify_paths(touched, patterns)
    fp = fingerprint.compute_fingerprint(repo_root, owned_hits)
    matched = []
    # 无合法 id 的单元：任何事件都无法通过 unit 逐字匹配（None==None
    # 不得成为 legacy 无 unit 字段事件的匹配通道——H6 口径的形状防御；
    # 持久化 state 经 validate_work_unit 不会出现该形状，此处兜底
    # 谓词作为公开 API 的直接调用方）
    if isinstance(uid, str) and uid != "":
        for command in required:
            # 从尾向头找第一条五条件全满足的事件（与原对账口径同序）
            for event in reversed(events):
                if not isinstance(event, dict):
                    continue
                if (event.get("event") == "verification"
                        and event.get("unit") == uid
                        and event.get("fingerprint") == fp
                        and event.get("status") == "pass"
                        and event.get("command") == command):
                    matched.append(command)
                    break
    missing = [c for c in required if c not in matched]
    return {"ok": not missing, "fingerprint": fp,
            "required": list(required), "matched": matched,
            "missing": missing}


def _interrupted_advice(repo_root, task_id, unit, touched, events):
    """单个中断单元的裁决输出：(advice, advisory) 恰一非 None。

    running 走 §69 三分支建议（三分支见模块 docstring 证据顺序节）；
    verifying 只有全部 required command 都有新鲜验证证据才建议
    completed（verifying→completed 表内合法），无残留 / 残留证据
    不全两种表外情形改出 no-op 裁决提示（advisories，不携带转换——
    verifying→ready / verifying→verifying 均不在 §62 转换表内，
    建议层不出表外建议）。
    证据判断复用 fresh_unit_verification（注入手中已有的 touched /
    events，避免二次 git 与读盘；五条件逐字口径见该谓词 docstring）。
    """
    status = unit.get("status")
    patterns = unit.get("ownership")
    if not isinstance(patterns, (list, tuple)):
        patterns = []
    owned_hits, _ = ownership.classify_paths(touched, patterns)
    if not owned_hits:
        if status == "verifying":
            return None, dict(ADVISORY_VERIFYING_RULING)
        return dict(SUGGEST_READY), None
    evidence = fresh_unit_verification(repo_root, task_id, unit,
                                       touched=touched, events=events)
    if evidence["ok"]:
        return dict(SUGGEST_COMPLETED), None
    if status == "verifying":
        return None, dict(ADVISORY_VERIFYING_STAY)
    return dict(SUGGEST_VERIFYING), None


def reconcile_interrupted(repo_root, task_id, units, *, touched=None,
                          events=None) -> dict:
    """对中断单元（running / verifying）产出恢复裁决（§68-§69）。

    参数：
      - repo_root：仓库根（git 仓库；指纹与缺省 touched 清单基于它）；
      - task_id：任务 id（缺省 events 读取
        .glm-conductor/tasks/<task-id>/events.jsonl）；
      - units：work unit dict 列表（§61 形状；非 list 按空图处理，
        容错与 runtime.dependency 一致）；
      - touched：显式注入改动清单（git_touched_files 同口径；None →
        内部 ownership.git_touched_files(repo_root)，git 失败
        OwnershipError 上抛）；
      - events：显式注入事件清单（read_events 同口径；None → 内部
        journal.read_events(repo_root, task_id)；非 list → 按 [] 容错）。

    返回（结构见模块 docstring；suggestions / advisories / reconciled
    键序均按 units 出现序）：
        {"suggestions": {uid: {"to", "reason"}},
         "advisories": {uid: {"reason"}},
         "reconciled": [uid...], "touched": [...]}

    纪律：纯建议——零落盘、不修改 units；应用示例见模块 docstring
    （只对 suggestions 应用 transition，advisories 仅提示）。
    completed 等非中断状态零输出（§68，见模块 docstring 锚定）。
    """
    if touched is None:
        touched = ownership.git_touched_files(repo_root)
    if events is None:
        events = journal.read_events(repo_root, task_id)
    elif not isinstance(events, list):
        events = []  # 注入通道容错：坏形状不炸对账

    suggestions = {}
    advisories = {}
    reconciled = []
    for unit in (units if isinstance(units, list) else []):
        if not isinstance(unit, dict):
            continue
        uid = unit.get("id")
        if not isinstance(uid, str) or uid == "":
            continue  # 无合法 id 即无建议键，跳过（与依赖层容错一致）
        if unit.get("status") not in INTERRUPTED_STATUSES:
            continue  # §68：completed 等非中断状态零建议，不重跑
        advice, advisory = _interrupted_advice(repo_root, task_id, unit,
                                               touched, events)
        if advice is not None:
            suggestions[uid] = advice
        if advisory is not None:
            advisories[uid] = advisory
        reconciled.append(uid)
    return {
        "suggestions": suggestions,
        "advisories": advisories,
        "reconciled": reconciled,
        "touched": list(touched),
    }


def reconcile_leases(repo_root, task_id, units, *, now=None) -> dict:
    """对落盘租约做崩溃恢复三分裁决（v2.0.1 加固 H5，P1-4/P1-5）。

    把 leases.json 逐条与任务图对读，产出（各桶按 path 排序，确定）：
      - stale：[{"path", "owner", "reason"}...]——owner 不是图中任何
        running/verifying 单元（图中无此单元 / 单元在 completed /
        failed / cancelled / ready 等非活跃写相；reason 中文注明归属
        状态或「图中无此单元」）——可被 task_manager.recover_leases
        自动释放；
      - active：[{"path", "owner"}...]——owner 为 running/verifying
        单元且未过期（活跃写相的正常租约，保留）；
      - expired_running：[{"path", "owner", "expires_at"}...]——owner
        为 running/verifying 单元但已过期。**不建议自动释放**：worker
        可能仍在写（TTL 只是保守估计），释放裁决归主会话。

    参数：
      - units：work unit dict 列表（§61 形状；非 list 按空图处理）；
      - now：过期判定时刻（None → 当前 UTC；透传
        runtime.lease.expired_leases，接受 datetime / ISO 字符串）。

    纪律（与 reconcile_interrupted 同一口径）：纯建议——零落盘、不
    修改传入 units。形状异常记录（owner 非非空字符串）按 owner=None
    归入 stale 上报（可见、保守不误清）；leases.json 损坏的
    ValueError 自然上抛（不静默）。
    """
    by_id = {}
    for unit in (units if isinstance(units, list) else []):
        if not isinstance(unit, dict):
            continue
        uid = unit.get("id")
        if isinstance(uid, str) and uid != "" and uid not in by_id:
            by_id[uid] = unit
    expired_entries = lease.expired_leases(repo_root, task_id, now=now)
    expires_by_path = {entry["path"]: entry["expires_at"]
                       for entry in expired_entries}
    leases = lease.lease_state(repo_root, task_id)
    stale = []
    active = []
    expired_running = []
    for path, record in leases.items():
        holder = record.get("owner") if isinstance(record, dict) else None
        if not isinstance(holder, str) or holder == "":
            holder = None  # 形状异常记录：保守视为无主（不上报具体归属）
        unit = by_id.get(holder)
        status = unit.get("status") if isinstance(unit, dict) else None
        if status in INTERRUPTED_STATUSES:
            if path in expires_by_path:
                expired_running.append({"path": path, "owner": holder,
                                        "expires_at": expires_by_path[path]})
            else:
                active.append({"path": path, "owner": holder})
        else:
            if unit is None:
                reason = "图中无此单元"
            else:
                reason = "归属单元状态为 %s（非活跃写相）" % status
            stale.append({"path": path, "owner": holder, "reason": reason})
    return {
        "stale": sorted(stale, key=lambda item: item["path"]),
        "active": sorted(active, key=lambda item: item["path"]),
        "expired_running": sorted(expired_running,
                                  key=lambda item: item["path"]),
    }


# —— v2.1 M3：reconcile_agent_run 单单元四分分类（计划 §9，纯读） ——

# 四分分类词汇（§9 冻结；返回 dict "classification" 的全部取值）
CLASS_REUSE_RESULT = "reuse_result"
CLASS_RESUME_WITH_PROGRESS = "resume_with_progress"
CLASS_REDISPATCH_CLEAN = "redispatch_clean"
CLASS_MANUAL_RULING = "manual_ruling"

# reuse_result 认可的档案终态（判定树第 4 条：completed / stopped；
# failed 是证据冲突出口、running 是僵尸态——均不在 reuse 词汇内）
REUSE_TERMINAL_STATUSES = ("completed", "stopped")

# 僵尸态标记（§7.3：主会话崩溃后档案 status 可能永久 running，绝不
# 解读为存活——只触发 rationale 的僵尸语义句）
ZOMBIE_OBSERVED_STATUS = "running"

# rationale 冻结短句（英文；逐字锁定，供调用方 / 测试锚定语义）
RATIONALE_NO_RUNS_NO_RESIDUE = "no runs, no residue"
RATIONALE_UNATTRIBUTABLE_EDITS = "unattributable edits present"
RATIONALE_ARCHIVED_TERMINAL = "agent archived terminal + fresh evidence"
RATIONALE_TRANSCRIPT_CONFLICT = "transcript/evidence conflict"
RATIONALE_PARTIAL_WORK = "partial work present, no fresh pass evidence"
RATIONALE_ZOMBIE_AWARE = "metadata running is not truth (zombie-aware)"
RATIONALE_FRESH_EVIDENCE = "fresh pass evidence present"
RATIONALE_CONSERVATIVE = "unresolved evidence combination; manual ruling"

# 任务 state.json 文件名（与 runtime.state 的任务目录布局一致；目录经
# journal.journal_path 定位派生——不导入 runtime.state / task_manager，
# 保持本模块依赖面 = 既有依赖 + runtime.agent_run）
_STATE_FILENAME = "state.json"


def _load_unit_from_state(repo_root, task_id, uid):
    """只读解析任务 state.json，定位 uid 对应的 work unit。

    返回 (unit, state_readable)：
      - unit：命中的单元 dict（work_units 中首个 id == uid 的元素），
        找不到为 None；
      - state_readable：state.json 可读且形状可解析（顶层 dict 且
        work_units 为 list）。文件缺失 / OSError / JSON 损坏 / 顶层
        非 dict / work_units 非 list → False。

    纯读（零写入）；目录布局经 journal.journal_path 派生（state.json
    与 events.jsonl 同处任务目录），不导入 runtime.state。
    """
    path = journal.journal_path(repo_root, task_id).parent / _STATE_FILENAME
    if not path.is_file():
        return None, False
    try:
        with open(str(path), "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError):  # ValueError 含 JSON / 解码错误
        return None, False
    if not isinstance(raw, dict):
        return None, False
    units = raw.get("work_units")
    if not isinstance(units, list):
        return None, False
    for unit in units:
        if isinstance(unit, dict) and unit.get("id") == uid:
            return unit, True
    return None, True


def _last_agent_id(runs):
    """runs 中最后一个非空 agent_id（倒序扫描）；全缺失 → None。

    与 agent_run.run_lifecycle 的 last_agent_id 同一口径：最后一次
    派发的 agent_id 提取失败（工具返回形状异常）时回退更早的已知
    档案句柄。
    """
    for run in reversed(runs):
        agent_id = run.get("agent_id") if isinstance(run, dict) else None
        if isinstance(agent_id, str) and agent_id:
            return agent_id
    return None


def _classify_agent_run(runs, owned_residue, unattributable_residue,
                        verification, native):
    """四分判定树（§9.1-§9.4，冻结，逐条短路）；返回 (classification,
    rationale)（rationale 按命中顺序，冻结短句见模块常量）。

    分支 1（找不到单元 / git 求值异常）由调用方先行处理，不进入本
    函数。分支 4/5 的衔接口径：verification 为 None（单元无 required
    命令）或 ok=False 才算「无完整 pass 证据」进分支 5；verification
    ok 但档案 observed_status 非终态亦非 failed（僵尸 running / 未知
    值）时，分支 4 的两个出口与分支 5 的前提都不成立 → 分支 6 保守
    兜底（证据与档案的张力禁止自动猜测，§9.4）。
    """
    if not runs and not owned_residue and not unattributable_residue:
        # 分支 2：无执行内容、无任何残留 → 全新派发（成本最低）
        return CLASS_REDISPATCH_CLEAN, [RATIONALE_NO_RUNS_NO_RESIDUE]
    if unattributable_residue:
        # 分支 3：ownership 之外有无法归属的改动 → 人工裁决
        return CLASS_MANUAL_RULING, [RATIONALE_UNATTRIBUTABLE_EDITS]
    observed = (native.get("observed_status")
                if isinstance(native, dict) else None)
    verified = (isinstance(verification, dict)
                and verification.get("ok") is True)
    if verified:
        # 分支 4：新鲜 pass 证据在先（§7.3：证据优先级高于档案）
        if native is None or observed in REUSE_TERMINAL_STATUSES:
            return CLASS_REUSE_RESULT, [RATIONALE_ARCHIVED_TERMINAL]
        if observed == "failed":
            # 档案终态 failed 与 pass 证据冲突 → 禁止自动猜测
            return CLASS_MANUAL_RULING, [RATIONALE_TRANSCRIPT_CONFLICT]
        # 分支 6（保守兜底）：证据齐但档案非终态（僵尸 running / 未知值）
        rationale = [RATIONALE_FRESH_EVIDENCE]
        if observed == ZOMBIE_OBSERVED_STATUS:
            rationale.append(RATIONALE_ZOMBIE_AWARE)
        else:
            rationale.append(
                "native archive status %r is not terminal" % (observed,))
        rationale.append(RATIONALE_CONSERVATIVE)
        return CLASS_MANUAL_RULING, rationale
    if runs or owned_residue:
        # 分支 5：有执行内容但无完整 pass 证据 → 进度包续作
        rationale = [RATIONALE_PARTIAL_WORK]
        if observed == ZOMBIE_OBSERVED_STATUS:
            rationale.append(RATIONALE_ZOMBIE_AWARE)
        return CLASS_RESUME_WITH_PROGRESS, rationale
    # 分支 6（保守兜底）：判定树未穷举的组合（防御保留位——分支 2-5
    # 已穷尽输入空间，理论不可达；保留以锁定「其余组合 → 人工裁决」）
    return CLASS_MANUAL_RULING, [RATIONALE_CONSERVATIVE]


def reconcile_agent_run(repo_root, task_id, uid, *, events=None,
                        agents_root=None, now=None) -> dict:
    """单单元 Agent Run 四分分类决策 API（v2.1 M3，计划 §9；纯读）。

    崩溃 / 中断后对单个 work unit 的 agent run 做机械证据对账，输出
    四分分类之一，供主会话处置（而不是一律重派——dogfood 实录：崩溃
    后无 reconcile 消费原生记录，续作会话把已完成单元全部重做）：
      - reuse_result：agent 实际已完成但结果没回主会话——读 native
        transcript / result 捞成果，不重派 implementation，继续
        parent verification（§9.1；同会话另有轻量选项：SendMessage
        续接已完成 agent 直接问询）；
      - resume_with_progress：agent 做了一部分、repo 有一致 residue、
        无完整 pass evidence——本 API 只给分类与证据句柄，进度包
        （旧 transcript 摘要 / owned diff / 已定决策 / 未决问题 /
        未跑验证）由模型侧经 ReadSessionContext 读子会话 transcript
        组装后交新 agent 续作（§9.2，※DR 两层分工；本模块绝不复制
        transcript，§7.2）；
      - redispatch_clean：没有有效执行内容、没有可信 residue、没有
        可用结果——全新派发（§9.3）；
      - manual_ruling：unattributable edits / transcript 与证据冲突 /
        证据与档案张力 / 求值失败——禁止自动猜测，裁决归主会话
        （§9.4）。

    判定树（冻结，逐条短路；rationale 按命中顺序）：
      1. 找不到单元（state.json 缺失 / 损坏 / 无此 uid）或 git 求值
         异常（OwnershipError / FingerprintError）→ manual_ruling，
         rationale 注明求值失败（residue 按无 residue 处理，
         verification 记 None）；
      2. runs、owned_residue、unattributable_residue 全空 →
         redispatch_clean（rationale "no runs, no residue"）；
      3. unattributable_residue 非空（ownership 声明之外存在无法归属
         的改动）→ manual_ruling（"unattributable edits present"）；
      4. verification ok（all-match 新鲜 pass 证据，§7.3 证据优先级
         高于档案）且（native 为 None 或 observed_status ∈
         {completed, stopped}）→ reuse_result（"agent archived
         terminal + fresh evidence"）；observed_status == "failed"
         → manual_ruling（"transcript/evidence conflict"）；
      5. runs 或 owned_residue 非空且无完整 pass 证据（verification
         为 None——单元无 required 命令——或 ok=False）→
         resume_with_progress（"partial work present, no fresh pass
         evidence"）；observed_status == "running" 时 rationale 追加
         "metadata running is not truth (zombie-aware)"（§7.3 僵尸
         语义：档案 status 不是 truth，绝不解读为存活）；
      6. 其余组合（verification ok 但档案 observed_status 非终态亦非
         failed：僵尸 running / 未知值——证据与档案的张力不自动猜测）
         → manual_ruling（保守兜底）。

    参数：
      - repo_root：仓库根（git 求值根：residue 清单与证据指纹基于
        它；也是任务账本根——state.json / journal 同在
        <repo_root>/.glm-conductor/tasks/<task-id>/ 下）；
      - task_id：任务 id（单元定位与事件缺省读取都基于它）；
      - uid：目标 work unit id（逐字精确匹配 work_units[].id）；
      - events：显式注入事件清单（仅作用于验证证据谓词
        fresh_unit_verification，与既有调用同一口径；None → 缺省
        journal.read_events(repo_root, task_id)；非 list → 谓词按 []
        容错）。run 账本（evidence["runs"]）恒读任务 journal——
        agent_run.list_agent_runs 冻结签名不收事件注入；
      - agents_root：原生档案根（透传
        agent_run.native_agent_metadata；None → 缺省
        ~/.zcode/cli/agents；测试注入 tempfile 伪造树，绝不读写
        真实 ~/.zcode）；
      - now：保留参数（时间注入点）——当前证据链所有调用均不取
        时钟，透传无对象；为签名稳定保留，调用方无需传入。

    返回（冻结形状）：
        {"classification": <四分之一>,
         "rationale": [英文短句...]（按命中顺序；冻结短句见模块常量），
         "evidence": {"runs": <该单元 list_agent_runs 过滤结果>,
                      "owned_residue": [...]（归一到 / 的路径），
                      "unattributable_residue": [...]，
                      "verification": <fresh_unit_verification 结果；
                                       单元无 required 命令时 None>，
                      "native": <native_agent_metadata 结果或 None>}}
    native 取该单元 runs 的最后一个非空 agent_id（倒序扫描，与
    run_lifecycle 同口径）；无 runs → native 恒 None。

    纯读纪律：零 journal / state / 租约 / 档案写入、零状态转换、零
    网络——分类与证据全部来自只读观察，处置（状态转换 / 重派 / 进度
    包组装）归调用方。
    """
    runs = [run for run in agent_run.list_agent_runs(repo_root, task_id)
            if run.get("unit") == uid]
    last_agent_id = _last_agent_id(runs)
    native = None
    if last_agent_id is not None:
        native = agent_run.native_agent_metadata(
            last_agent_id, agents_root=agents_root)

    unit, state_readable = _load_unit_from_state(repo_root, task_id, uid)
    if unit is None:
        # 分支 1a：找不到单元 → manual_ruling（rationale 注明定位失败）
        rationale = []
        if not state_readable:
            rationale.append("task state missing or unreadable")
        rationale.append("unit %s not found in task state" % (uid,))
        return {"classification": CLASS_MANUAL_RULING,
                "rationale": rationale,
                "evidence": {"runs": runs, "owned_residue": [],
                             "unattributable_residue": [],
                             "verification": None, "native": native}}

    patterns = unit.get("ownership")
    if not isinstance(patterns, (list, tuple)):
        patterns = []
    required = unit.get("verification")
    has_required = isinstance(required, (list, tuple)) and bool(required)

    owned_residue = []
    unattributable_residue = []
    verification = None
    try:
        if events is None:
            events = journal.read_events(repo_root, task_id)
        touched = ownership.git_touched_files(repo_root)
        owned_residue, unattributable_residue = ownership.classify_paths(
            touched, patterns)
        if has_required:
            verification = fresh_unit_verification(
                repo_root, task_id, unit, touched=touched, events=events)
    except (ownership.OwnershipError, fingerprint.FingerprintError) as exc:
        # 分支 1b：git 求值异常 → manual_ruling（residue 按无 residue
        # 处理、verification 记 None，rationale 注明求值失败；
        # 返回形状照旧——决策 API 不向调用方上抛求值异常）
        rationale = ["repository evaluation failed: %s"
                     % type(exc).__name__]
        detail = str(exc).strip()
        if detail:
            rationale.append(detail[:160])
        return {"classification": CLASS_MANUAL_RULING,
                "rationale": rationale,
                "evidence": {"runs": runs, "owned_residue": [],
                             "unattributable_residue": [],
                             "verification": None, "native": native}}

    classification, rationale = _classify_agent_run(
        runs, owned_residue, unattributable_residue, verification, native)
    return {"classification": classification,
            "rationale": rationale,
            "evidence": {"runs": runs,
                         "owned_residue": owned_residue,
                         "unattributable_residue": unattributable_residue,
                         "verification": verification,
                         "native": native}}
