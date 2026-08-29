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

依赖：
    runtime.ownership / runtime.fingerprint / runtime.journal（缺省
    证据来源）、runtime.lease（H5 租约对读——expired_leases 过期集
    + lease_state 明细）。不导入 runtime.work_unit（建议层不依赖转
    换层——应用示例里的 transition_work_unit 由调用方导入，无循环
    导入）。仅 Python 3 标准库，`python3 -S` 可运行。

来源：
    docs/glm-conductor-v2-upgrade-guide-final.md §68（恢复后 completed
    不重跑）/ §69（中断单元恢复证据四步）+ v2 升级计划工作块 B8.4
    + v2.0.1 加固工作包 H5（P1-4/P1-5，租约对账）与 H6（P1-7，
    verification 证据归属绑定 unit）+ release hardening 补丁计划
    docs/GLM-Conductor-v2.0.1-Release-Hardening-Patch-Agent-Implementation-Plan.md
    （RB-1 / WU-P1：共享证据谓词 fresh_unit_verification + all-match
    收紧，D2 决策）。
"""

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
