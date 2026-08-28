#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GLM Conductor v2 任务图恢复对账辅助层（v2 工作块 B8.4，§68-§69）。

职责：
    中断（会话结束 / 崩溃 / 额度耗尽）后，任务图中 running / verifying
    状态的单元既不能假设「完成」（worker 报告只是 claim，§70），也不能
    盲目假设「须重跑」（改动可能已在仓库且已验证）。本模块按 §69 的
    证据顺序给出「转换建议」——纯建议、零落盘、不修改传入 units；
    转换的应用归调用方（work_unit.transition_work_unit，见应用示例）。

completed 不重跑锚定（§68）：
    本模块只对 status ∈ ("running", "verifying") 的单元产出建议；
    completed / failed / cancelled 等其他状态零建议。completed 单元
    不重跑——除非仓库证据否定（其 owned 文件随后又被改动），而该
    否定判断是主会话的职责，不属于本模块：reconcile 不判断 completed
    是否失效，也不对非中断状态出任何建议。

证据顺序（§69 四步证据 → 三分支建议，逐中断单元）：
    1. 仓库证据：ownership.classify_paths(touched, unit["ownership"])
       得 owned_hits——单元 ownership 声明范围内的残留改动清单；
    2. 无残留改动（owned_hits 为空）→ 建议 ready（干净重派：工作区
       无该单元范围内的任何残留，重派成本最低）；
    3. 有残留改动 → journal 指纹证据：fingerprint.compute_fingerprint
       对 owned_hits 真算当前指纹，再在 events 里从尾向头找第一条
       满足全部条件的 verification 事件：
         event == "verification" 且 fingerprint == 当前指纹 且
         status == "pass" 且 command 出现在 unit["verification"] 列表内
       - 找到 → 建议 completed（存在绑定当前改动的新鲜验证证据——
         parent-observed 恢复解释，最终裁决待主会话确认）；
       - 未找到 → 建议 verifying（残留改动无新鲜验证证据：主会话
         必须亲自检查 diff 并运行单元验证——§70 claim 不算完成）；
    4. blocked 不在本模块建议词汇内——「检查失败」的裁决（failed /
       blocked 等）是主会话的职责，模块只依据仓库 + journal 证据出
       建议，绝不替主会话判死。

返回（确定性，建议键序按 units 出现序）：
    {"suggestions": {uid: {"to": ..., "reason": ...}},
     "reconciled": [uid...], "touched": [...]}
      - suggestions：仅含中断单元；键为单元 id，插入序 = units 出现序
        （Python 3.7+ dict 保序）；
      - reconciled：有建议的单元 id 列表（同出现序）；
      - touched：实际使用的改动清单（注入原样 / 缺省 git_touched_files
        结果），供调用方日志与复算。

纯建议纪律：
    reconcile 零落盘（不写 state / journal / 任何文件）、不修改传入
    units。应用示例（主会话侧）：
        report = reconcile_interrupted(repo, task_id, st["work_units"])
        by_id = {u["id"]: u for u in st["work_units"]}
        for uid, advice in report["suggestions"].items():
            work_unit.transition_work_unit(by_id[uid], advice["to"])
        state.save_state(repo, st)
    非法转换（如 verifying → ready 不在转换表）由 transition_work_unit
    抛 ValueError 暴露——建议层不做转换表预检，应用侧闸门归 work_unit。

错误上抛（调用方处理，可降级或转人工）：
    - touched 缺省时 git 失败 → ownership.OwnershipError 自然上抛；
    - 指纹计算失败 → fingerprint.FingerprintError 自然上抛；
    - ownership 声明模式非法 → classify_paths 的 OwnershipError 同样
      自然上抛（结构性错误不静默转换）；
    - events 显式注入非 list → 按 [] 容错（缺省通道 read_events 本就
      容错；注入通道是测试 / 回放后门，坏形状不炸对账）。

依赖：
    runtime.ownership / runtime.fingerprint / runtime.journal（缺省
    证据来源）。不导入 runtime.work_unit（建议层不依赖转换层——
    应用示例里的 transition_work_unit 由调用方导入，无循环导入）。
    仅 Python 3 标准库，`python3 -S` 可运行。

来源：
    docs/glm-conductor-v2-upgrade-guide-final.md §68（恢复后 completed
    不重跑）/ §69（中断单元恢复证据四步）+ v2 升级计划工作块 B8.4。
"""

from runtime import fingerprint
from runtime import journal
from runtime import ownership

# 中断对账的目标状态集合：只有处于这两个状态的单元才产出建议（§68）
INTERRUPTED_STATUSES = ("running", "verifying")

# 三分支建议（§69；reason 逐字锁定，供调用方 / 测试锚定语义）
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


def _interrupted_advice(repo_root, unit, touched, events) -> dict:
    """单个中断单元的三分支建议（§69 四步证据；分支见模块 docstring）。"""
    patterns = unit.get("ownership")
    if not isinstance(patterns, (list, tuple)):
        patterns = []
    owned_hits, _ = ownership.classify_paths(touched, patterns)
    if not owned_hits:
        return dict(SUGGEST_READY)
    fp = fingerprint.compute_fingerprint(repo_root, owned_hits)
    command_pool = unit.get("verification")
    if not isinstance(command_pool, (list, tuple)):
        command_pool = ()
    for event in reversed(events):
        if not isinstance(event, dict):
            continue
        if (event.get("event") == "verification"
                and event.get("fingerprint") == fp
                and event.get("status") == "pass"
                and event.get("command") in command_pool):
            return dict(SUGGEST_COMPLETED)
    return dict(SUGGEST_VERIFYING)


def reconcile_interrupted(repo_root, task_id, units, *, touched=None,
                          events=None) -> dict:
    """对中断单元（running / verifying）产出恢复转换建议（§68-§69）。

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

    返回（结构见模块 docstring；suggestions 键序按 units 出现序）：
        {"suggestions": {uid: {"to", "reason"}},
         "reconciled": [uid...], "touched": [...]}

    纪律：纯建议——零落盘、不修改 units；应用示例见模块 docstring。
    completed 等非中断状态零建议（§68，见模块 docstring 锚定）。
    """
    if touched is None:
        touched = ownership.git_touched_files(repo_root)
    if events is None:
        events = journal.read_events(repo_root, task_id)
    elif not isinstance(events, list):
        events = []  # 注入通道容错：坏形状不炸对账

    suggestions = {}
    reconciled = []
    for unit in (units if isinstance(units, list) else []):
        if not isinstance(unit, dict):
            continue
        uid = unit.get("id")
        if not isinstance(uid, str) or uid == "":
            continue  # 无合法 id 即无建议键，跳过（与依赖层容错一致）
        if unit.get("status") not in INTERRUPTED_STATUSES:
            continue  # §68：completed 等非中断状态零建议，不重跑
        suggestions[uid] = _interrupted_advice(repo_root, unit, touched,
                                               events)
        reconciled.append(uid)
    return {
        "suggestions": suggestions,
        "reconciled": reconciled,
        "touched": list(touched),
    }
