#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GLM Conductor v2 quota 消费/迁移记账域（v2.2.1 WU-221-C1，行为保持抽取）。

职责：
    §15.1 resume-time 窗口消费记账（record_quota_boundary_consumed）与
    §22.6 存量窗口记账保守迁移（migrate_quota_window_accounting）的规范
    定义落点。v2.2.1 WU-221-C1 自 runtime.task_manager 原文抽取（行为
    保持：同 journal 事件形状与写序、同幂等语义、同错误口径，函数体
    逐字未改，仅 import 适配）；本模块只承载记账域，不做派发事务编排。

依赖方向（冻结，防循环）：
    task_manager → 本模块，绝不反向——本模块禁止 import
    runtime.task_manager。故与抽取域共用、原属 task_manager 的以下名字
    按「单一规范落点 + 上层 re-export」以本模块为家（上层经顶部
    re-import 使既有 task_manager.<名字> 解析点全部解析到同一对象）：
      - TaskManagerError：事务边界异常（抽取域须抛出；全仓既有 import
        方继续经 runtime.task_manager 取到同一类对象）；
      - _utc_now_iso / _require_state / _continuity_view /
        _current_provider_identity_hash：纯容错读助手（零 task_manager
        内部依赖，仅依赖 runtime.state / runtime.execution_policy /
        runtime.quota.identity）。

    仅标准库依赖（datetime）；其余为 runtime 一方模块（journal /
    state / execution_policy / quota.epoch / quota.identity），与抽取
    前一致。Python 3.7 兼容语法（仓库下限）。
"""

from runtime import journal
from runtime import state
from runtime.execution_policy import consumed_quota_windows
from runtime.execution_policy import default_execution_policy
# v2.2.1 WU-221-C2（行为保持抽取）：_utc_now_iso 的规范定义已移至
# runtime.quota.time_utils（毫秒精度 Z 形式，语义稳定的时间格式化原语）；
# 本行 re-import 保持既有解析点（task_manager / continuity.wake_bridge /
# continuity.resume / continuity.subscription 的
# `from runtime.quota.accounting import _utc_now_iso` 等）零变化。
from runtime.quota.time_utils import _utc_now_iso


class TaskManagerError(Exception):
    """task_manager 事务边界违背（单元缺失/状态不符/租约丢失/决策未批准
    /RB-1 完成证据缺失或不可判定）。"""


def _require_state(repo_root, task_id, api) -> dict:
    """load_state 并要求任务存在：缺失 → TaskManagerError；JSON 损坏的
    ValueError 自然上抛（不静默、不覆盖损坏文件）。"""
    st = state.load_state(repo_root, task_id)
    if st is None:
        raise TaskManagerError(
            "%s：任务 %s 不存在（无 state.json），无法执行派发事务"
            % (api, task_id))
    return st


def _current_provider_identity_hash():
    """当前 provider 身份指纹（v2.2.1 WU-221-B2 QuotaIdentity；非秘密
    16-hex——sha256("来源:凭证") 前缀截断，绝不含凭证材料，§37）。

    经共享落点 runtime.quota.identity.compute_provider_identity_hash
    派生（函数内 import：monkeypatch 友好）；每次调用恰派生一次，调
    用方在同流程内复用同一结果（不二次解析凭证——resolve_credential
    是纯本地读取，零网络）。
    """
    from runtime.quota.identity import compute_provider_identity_hash
    return compute_provider_identity_hash()


def _continuity_view(st) -> dict:
    """容错读取续跑授权四元组（execution_policy 事实源，§14）。

    返回 {"auto_resume", "source", "max_quota_windows",
    "consumed_quota_windows", "remaining"}：
      - auto_resume：continuity.auto_resume；缺块 / 非法词汇 → 保守
        按 "manual"（legacy 形态与 default_execution_policy 一致）；
      - source：authorization.source；缺块 / 非法词汇 → "default"；
      - max_quota_windows：continuity.max_quota_windows；非 >= 0 int
        → 0；
      - consumed_quota_windows：经 execution_policy.consumed_quota_
        windows 容错读（缺键 / 形状异常 → 0）；
      - remaining = max(0, max_quota_windows - consumed_quota_windows)
        （record_quota_wake 只增不减，跨过 max 后不再出现负数口径）。
    纯读函数：不改入参、零 I/O。
    """
    policy = st.get("execution_policy")
    continuity = (policy.get("continuity")
                  if isinstance(policy, dict) else None)
    continuity = continuity if isinstance(continuity, dict) else {}
    auto_resume = continuity.get("auto_resume")
    if auto_resume not in ("manual", "notify", "auto_once", "until_done"):
        auto_resume = "manual"
    authorization = (policy.get("authorization")
                     if isinstance(policy, dict) else None)
    source = (authorization.get("source")
              if isinstance(authorization, dict) else None)
    if source not in ("default", "user"):
        source = "default"
    max_windows = continuity.get("max_quota_windows")
    if isinstance(max_windows, bool) or not isinstance(max_windows, int) \
            or max_windows < 0:
        max_windows = 0
    consumed = consumed_quota_windows(policy)
    return {
        "auto_resume": auto_resume,
        "source": source,
        "max_quota_windows": max_windows,
        "consumed_quota_windows": consumed,
        "remaining": max(0, max_windows - consumed),
    }


def record_quota_boundary_consumed(repo_root, task_id, *, epoch_id,
                                   executable_boundary_id,
                                   resume_started_at,
                                   provider_identity_hash=None) -> dict:
    """resume-time 窗口消费记账（v2.2 C1b；修正计划 §22.1 + §C1b +
    §15.1 消费事务点冻结）。

    消费点唯一合法位置 = §15.1 冻结的 Resume Controller resume commit
    point：新 executable epoch 已确认、authorization / window budget /
    reconcile 均通过、Resume Controller 成功接受该 epoch 并准备把任务
    从 waiting_quota 恢复为执行态的那个 commit point。本 API 只提供
    记账，绝不接线 resume_from_quota（接线与调用时序归 Resume
    Controller 工作单元）；§22.1 不消费清单（bridge fire / create /
    arm / no-op、still exhausted、weekly blocked、provider unavailable、
    manual/notify warm-only、primer model call、repeated recurring
    probe）一律不得调用本 API。

    与 record_quota_wake（v2.1 legacy，C1a 已标注 deprecated）的关系：
    三查 / 幂等 / 写路径模式逐条镜像该实现，仅幂等 key 与事件形状按
    §22.1 / §C1b 冻结口径更换。

    - 幂等 key = task_id + epoch_id（§22.1：绝不能对同一 epoch 二次
      消费）：journal 已有同 epoch_id 的 quota_boundary_consumed 事件
      → 直接返回既有消耗结果（不递增、不新建事件、零写副作用），返回
      dict 既有键保留并增标 "idempotent": True——resume 重放 / 部分
      写入恢复（§15.1 幂等证据语义）不再重复消耗；不同 epoch_id 照常
      各消费一窗（epoch 推进 = 窗口身份推进）；
    - RH-03 write-ahead pending marker（v2.2 release hardening 方案 A；
      修正计划 §5）：正常写路径冻结为 append pending（新事件
      quota_consumption_pending {epoch_id, representative_boundary_id,
      target_consumed, resume_started_at}，字段逐字，target_consumed =
      本次目标值）→ save_state 投影 → append committed
      （quota_boundary_consumed）——epoch 身份先于一切可变投影存在。
      committed 缺席而同 epoch pending 在案（= 上一进程已过授权、事务
      中断在 pending 之后）→ 恢复闭合（reconcile），且此分支必须先于
      授权三查：pending 是授权已通过的持久证据，恢复闭合不重查预算
      （否则 state 已投影 + 预算恰好耗尽时该事务永远无法闭合）——
      state.consumed == target_consumed → 投影已落，只补 committed 事件
      （boundary / resume_started_at 取 pending 冻结值，remaining 按
      max 折算）；== target_consumed - 1 → 投影未落，补一次投影到
      target 再补 committed；其余形态（state 领先 / 差距 > 1 / 多条
      pending target 不一致 / pending 形状损坏）→ TaskManagerError
      （中文，零写——pending 保留待人工裁决）。恢复闭合返回标准六键
      （返回键零变化，不加新键）；
    - 写入前授权三查（口径与 _continuity_view / _quota_wake_decision
      一致——execution_policy 块缺/坏按默认块解释为 manual）：
      auto_resume ∈ {auto_once, until_done} / authorization.source ==
      "user" / 剩余窗口 > 0，任一不满足 → TaskManagerError（中文消息
      指明 violated 条件，零副作用——无事件、state.json 字节不变；
      manual / notify 任务不得经本 API 制造 consumed window）；
    - continuity.consumed_quota_windows += 1（缺键按 0 起算；legacy
      缺 execution_policy 块时以默认块补齐后写——半定义块过不了
      validate_state 的完整性闸）；
    - journal quota_boundary_consumed {epoch_id, representative_boundary_id,
      resume_started_at, consumed, remaining}（§C1b：事件同时携带
      epoch_id 与 boundary 证据；RH-04 起辅助证据字段记作
      representative_boundary_id——语义 = epoch 中用于人类审计的代表
      窗口身份（最早可解析 reset 的 D10 形态），并非 C2 executable
      boundary（阻塞窗最晚 reset + grace），旧键名 executable_boundary_id
      名不符实故改；RH-04 之前的旧事件以 legacy 键 executable_boundary_id
      在案——读侧一律双键兼容（先 new 后 legacy），绝不重写旧 journal；
      legacy 迁移核验才用 boundary_id alias 读更旧的人工证据）。

    参数校验（先于任何 I/O，失败零副作用）：epoch_id /
    executable_boundary_id / resume_started_at 必须是非空 str，否则
    ValueError（中文消息含字段名）。provider_identity_hash（v2.2.1
    WU-221-B2 QuotaIdentity）：None（缺省）→ 经共享模块派生当前指纹
    一次（derive-and-emit——消费记账是权威账，新事件必携带）；非 None
    须为非空 str。kwargs 参数名保留
    executable_boundary_id（RH-04 方案 A：调用面零扰动，_resume_
    consumption 等调用方零改动；该实参写入事件的
    representative_boundary_id 字段）。返回 {"consumed_quota_windows",
    "remaining_quota_windows", "max_quota_windows", "epoch_id",
    "executable_boundary_id", "resume_started_at"}（返回键名零变化——
    executable_boundary_id 键承载代表窗口身份值）；幂等命中路径既有
    键保留并增标 "idempotent": True。任务缺失 TaskManagerError。

    v2.2.1 WU-221-B2（QuotaIdentity）幂等 / 归属升级：幂等扫描从
    「epoch_id 等值」升为 quota_identity_matches 双形态判定（epoch_id
    等值 AND 事件指纹缺席或等于当前指纹）——
      - legacy 事件（无指纹）保守信任，幂等语义逐字不变（v2.2 记录
        保持在案可读）；
      - 异身份同名 epoch 的 committed / pending 证据按「无先前记录」
        处理：A 对 epoch E 的消费既不满足也不幂等拦截 B 的消费，B 在
        A 之后消费 E 按其自身身份记账（绝不跨身份借用账目）；
      - 新写的 pending / committed 事件携带 provider_identity_hash =
        当前指纹；恢复闭合的 committed 沿用 pending 冻结的指纹
        （legacy pending 无指纹则省略该键——绝不虚构）。
    """
    api = "record_quota_boundary_consumed"
    for field, value in (("epoch_id", epoch_id),
                         ("executable_boundary_id", executable_boundary_id),
                         ("resume_started_at", resume_started_at)):
        if not isinstance(value, str) or value == "":
            raise ValueError(
                "%s：%s 必须是非空字符串，得到 %r" % (api, field, value))
    if provider_identity_hash is not None \
            and (not isinstance(provider_identity_hash, str)
                 or provider_identity_hash == ""):
        raise ValueError(
            "%s：provider_identity_hash 必须是 None（经共享模块派生）"
            "或非空 str（16-hex 非秘密指纹），得到 %r"
            % (api, provider_identity_hash))
    identity = (provider_identity_hash
                if provider_identity_hash is not None
                else _current_provider_identity_hash())
    st = _require_state(repo_root, task_id, api)
    view = _continuity_view(st)
    from runtime.quota import epoch as quota_epoch  # 函数内 import：monkeypatch 友好
    # 幂等（§22.1 / §15.1）：同 epoch_id 已消费 → 原样返回既有消耗
    # 结果（取首条匹配——正确流程下至多一条；历史脏数据重复时首条是
    # 原始账）。同一趟扫描顺带收集同 epoch 的 RH-03 pending 证据
    # （quota_consumption_pending）——committed 命中即幂等返回（优先
    # 级最高）；pending 仅在 committed 缺席时进入恢复闭合分支。
    # WU-221-B2：匹配均为 QuotaIdentity 双形态（异身份事件不算数）。
    prior = None
    pendings = []
    for event in journal.read_events(repo_root, task_id):
        name = event.get("event")
        if name == "quota_boundary_consumed" \
                and quota_epoch.quota_identity_matches(
                    event.get("epoch_id"),
                    event.get("provider_identity_hash"),
                    epoch_id, identity):
            prior = event
            break
        if name == "quota_consumption_pending" \
                and quota_epoch.quota_identity_matches(
                    event.get("epoch_id"),
                    event.get("provider_identity_hash"),
                    epoch_id, identity):
            pendings.append(event)
    if prior is not None:
        consumed = prior.get("consumed")
        if isinstance(consumed, bool) or not isinstance(consumed, int) \
                or consumed < 0:
            consumed = view["consumed_quota_windows"]
        remaining = prior.get("remaining")
        if isinstance(remaining, bool) or not isinstance(remaining, int) \
                or remaining < 0:
            remaining = view["remaining"]
        # RH-04 双键兼容读：prior 取 representative_boundary_id 优先，
        # legacy 键 executable_boundary_id（RH-04 前旧事件）回退，两键
        # 皆缺再回退本次实参——返回键名不变，仅取值面双键
        recorded_boundary = prior.get("representative_boundary_id")
        if not isinstance(recorded_boundary, str) or recorded_boundary == "":
            recorded_boundary = prior.get("executable_boundary_id")
        if not isinstance(recorded_boundary, str) or recorded_boundary == "":
            recorded_boundary = executable_boundary_id
        recorded_started_at = prior.get("resume_started_at")
        if not isinstance(recorded_started_at, str) \
                or recorded_started_at == "":
            recorded_started_at = resume_started_at
        return {
            "consumed_quota_windows": consumed,
            "remaining_quota_windows": remaining,
            "max_quota_windows": view["max_quota_windows"],
            "epoch_id": epoch_id,
            "executable_boundary_id": recorded_boundary,
            "resume_started_at": recorded_started_at,
            "idempotent": True,
        }
    # RH-03 恢复闭合（reconcile，先于授权三查）：committed 缺席而同
    # epoch pending 在案 → 上一进程已过授权、事务中断在 pending 之后
    # （write-ahead 的持久证据）——按 pending 冻结的 target_consumed 与
    # state 投影对账，不重查预算、绝不二次 +1。pending 证据自身矛盾
    # （多条且 target 不一致 / 形状损坏）按异常分支零写；同 epoch 不
    # 同 target 的第二条 pending 不应发生，落入同一异常分支。
    if pendings:
        targets = [p.get("target_consumed") for p in pendings]
        if len(set(targets)) > 1 \
                or any(isinstance(t, bool) or not isinstance(t, int)
                       or t < 1 for t in targets):
            raise TaskManagerError(
                "%s：同 epoch 的 quota_consumption_pending 证据不一致或"
                "形状损坏（target_consumed=%r）——pending 与 state 矛盾，"
                "恢复闭合中止，零写，pending 保留待人工裁决"
                % (api, targets))
        target = targets[0]
        pending = pendings[0]
        # RH-04 双键兼容读：pending 冻结值同样先 new 后 legacy（旧
        # pending 以 executable_boundary_id 在案）——闭合补 committed 时
        # 以新键 representative_boundary_id 落盘
        pending_boundary = pending.get("representative_boundary_id")
        if not isinstance(pending_boundary, str) or pending_boundary == "":
            pending_boundary = pending.get("executable_boundary_id")
        if not isinstance(pending_boundary, str) or pending_boundary == "":
            pending_boundary = executable_boundary_id
        pending_started_at = pending.get("resume_started_at")
        if not isinstance(pending_started_at, str) \
                or pending_started_at == "":
            pending_started_at = resume_started_at
        state_consumed = view["consumed_quota_windows"]
        if state_consumed != target and state_consumed != target - 1:
            raise TaskManagerError(
                "%s：pending 与 state 不一致（quota_consumption_pending "
                "冻结 target_consumed=%d，state.consumed_quota_windows="
                "%d）——恢复闭合中止，零写，pending 保留待人工裁决"
                % (api, target, state_consumed))
        remaining = max(0, view["max_quota_windows"] - target)
        if state_consumed != target:
            # 投影未落：补一次投影到 pending 冻结的 target（不重算、
            # 不再 +1；legacy 缺块按默认块补齐，与正常路径同款）
            policy = st.get("execution_policy")
            if not isinstance(policy, dict):
                policy = default_execution_policy()
                st["execution_policy"] = policy
            continuity = policy.get("continuity")
            if not isinstance(continuity, dict):
                continuity = default_execution_policy()["continuity"]
                policy["continuity"] = continuity
            continuity["consumed_quota_windows"] = target
            state.save_state(repo_root, st)
        # 投影已落或刚补齐 → 只补 committed 事件（消费记账按 pending
        # 冻结值闭合，boundary / resume_started_at / 身份指纹取冻结值
        # ——legacy pending 无指纹则省略该键，绝不虚构）
        close_event = {
            "event": "quota_boundary_consumed", "epoch_id": epoch_id,
            "representative_boundary_id": pending_boundary,
            "resume_started_at": pending_started_at,
            "consumed": target, "remaining": remaining}
        pending_identity = pending.get("provider_identity_hash")
        if isinstance(pending_identity, str) and pending_identity:
            close_event["provider_identity_hash"] = pending_identity
        journal.append_event(repo_root, task_id, close_event)
        return {
            "consumed_quota_windows": target,
            "remaining_quota_windows": remaining,
            "max_quota_windows": view["max_quota_windows"],
            "epoch_id": epoch_id,
            "executable_boundary_id": pending_boundary,
            "resume_started_at": pending_started_at,
        }
    # 写入前授权三查（逐条指明 violated 条件；在任何 mutation / 落盘
    # 之前——拒绝路径零副作用；口径逐条镜像 record_quota_wake）
    if view["auto_resume"] not in ("auto_once", "until_done"):
        raise TaskManagerError(
            "%s：auto_resume=%r 不在自动续跑授权族（auto_once / "
            "until_done）内——manual / notify 任务不得消费窗口预算"
            "（额度恢复一律走 SessionStart 恢复注入 / 用户手动续跑）"
            % (api, view["auto_resume"]))
    if view["source"] != "user":
        raise TaskManagerError(
            "%s：authorization.source=%r 非 \"user\"——跨额度窗口自动"
            "续跑必须用户明确授权，拒绝消费窗口（纵深防御，与 "
            "_quota_wake_decision 口径一致）" % (api, view["source"]))
    if view["remaining"] <= 0:
        raise TaskManagerError(
            "%s：窗口预算已耗尽（consumed %d / max %d，剩余 %d）——"
            "不得再消费新 epoch，转 waiting_user 等待用户重新授权"
            "（§14.5）" % (api, view["consumed_quota_windows"],
                           view["max_quota_windows"], view["remaining"]))
    policy = st.get("execution_policy")
    if not isinstance(policy, dict):
        # legacy 任务无授权事实源块：以默认块补齐（与 record_quota_wake
        # 同款——完整四子块形状，否则 validate_state 拒绝落盘）
        policy = default_execution_policy()
        st["execution_policy"] = policy
    continuity = policy.get("continuity")
    if not isinstance(continuity, dict):
        continuity = default_execution_policy()["continuity"]
        policy["continuity"] = continuity
    consumed = consumed_quota_windows(policy) + 1
    continuity["consumed_quota_windows"] = consumed
    # RH-03 write-ahead：epoch 身份先于一切可变投影——pending 先于
    # save_state 落盘（崩溃后重入按 pending 恢复闭合，最多只消费一次），
    # committed 最后闭合
    journal.append_event(repo_root, task_id, {
        "event": "quota_consumption_pending", "epoch_id": epoch_id,
        "representative_boundary_id": executable_boundary_id,
        "target_consumed": consumed,
        "resume_started_at": resume_started_at,
        "provider_identity_hash": identity})
    state.save_state(repo_root, st)
    view = _continuity_view(st)
    journal.append_event(repo_root, task_id, {
        "event": "quota_boundary_consumed", "epoch_id": epoch_id,
        "representative_boundary_id": executable_boundary_id,
        "resume_started_at": resume_started_at, "consumed": consumed,
        "remaining": view["remaining"],
        "provider_identity_hash": identity})
    return {
        "consumed_quota_windows": consumed,
        "remaining_quota_windows": view["remaining"],
        "max_quota_windows": view["max_quota_windows"],
        "epoch_id": epoch_id,
        "executable_boundary_id": executable_boundary_id,
        "resume_started_at": resume_started_at,
    }


def migrate_quota_window_accounting(repo_root, task_id) -> dict:
    """存量窗口记账保守迁移（v2.2 C1b；修正计划 §22.6，2026-09-02
    锁定）。

    语义冻结：
      - 不自动退款：new_consumed >= legacy_consumed 恒成立（max 语义）
        ——legacy arm-time debit 即使是错账，也不得因 C1 上线自动下调
        （自动退款 = 无用户重新授权地增加未来自动续跑预算）；以后扩
        大预算只能走正常 authorization 路径，不得偷偷修历史数字；
      - 用 journal 做「归属」，不是退款：
        verified_consumed = journal 中可证明真实跨 boundary resume 的
        unique quota_boundary_consumed 事件数，
        new_consumed = max(legacy_consumed, verified_consumed)；
      - 数字相同也要写事件：new_consumed == legacy_consumed 时仍落一
        次 quota_accounting_migrated（迁移事实本身是账本记录）；
      - 一次性：journal 已有 quota_accounting_migrated → 幂等 no-op
        （零写零事件），返回既有记录 + "idempotent": True。

    verified 证据口径（v2.2 C7 双键联合去重，wu-22-C7 ③；RH-04 扩为
    双 boundary 键联合）：逐条 quota_boundary_consumed 事件取其全部
    在案身份键（epoch_id / boundary_id / representative_boundary_id /
    executable_boundary_id，每个存在的非空 str；legacy 人工证据（如
    §22.6 所引 2026-09-01T22:20Z boundary
    five_hour:2026-09-01T21:59:00Z 条目）以 boundary_id alias 在案，
    RH-04 起新形态证据以 epoch_id + representative_boundary_id 在案，
    RH-04 之前的新形态证据以 epoch_id + legacy 键
    executable_boundary_id 在案（旧 journal 不重写）；全缺视为不可
    核验、跳过）。事件仅当其全部身份键均未见时计入，计入即把全部
    身份键登入 seen——同 epoch 重放、「同窗 legacy boundary-only
    证据 + 新形态 epoch 证据」、以及「同窗新旧键名混用证据」不再双计
    （与 record_quota_boundary_consumed 的 task_id + epoch_id 幂等
    key 同构，并向 boundary 证据面扩展；distinct 窗口照常各计一次）。

    归属拆分（§22.6 冻结口径）：
      attributed_consumed = min(legacy_consumed, verified_consumed)
      legacy_unattributed_consumed
        = legacy_consumed − attributed_consumed
    （未归属部分仍然算已消费，不自动返还。）

    写路径（RH-03 同款 write-ahead pending marker，修正计划 §5.4）：
    重算五数后先落 journal quota_accounting_migration_pending
    {legacy_consumed, verified_boundary_ids, attributed_consumed,
    legacy_unattributed_consumed, new_consumed}（六字段字段名逐字——
    五数在 state 动写之前冻结）→ new_consumed != legacy_consumed 时
    更新 continuity.consumed_quota_windows 并 save_state（legacy 缺
    execution_policy / continuity 块按默认块补齐）；数字不变则零
    state 写。随后无论数字是否变化都落 journal
    quota_accounting_migrated {legacy_consumed, verified_boundary_ids,
    attributed_consumed, legacy_unattributed_consumed, migrated_at}
    （§22.6 五字段，字段名逐字；四数取 pending 冻结值）。marker 缺失
    而 pending 在案（崩溃重跑）→ 恢复闭合（先于重算）：按 pending
    冻结五数直接补 marker，绝不从可能已更新的 state 重算 legacy（否则
    legacy_consumed / 归属拆分失真）——state 已达 new_consumed 或本就
    无 state 变化 → 零 state 写只补 marker；new > legacy 且 state 仍为
    legacy → 补一次投影到 new_consumed 再补 marker；state 与 pending
    冻结值矛盾（或 pending 形状损坏 / 多条冻结值不一致）→
    TaskManagerError 零写（pending 保留待人工裁决）。单次调用至多
    一次 save_state + 两条事件（pending + marker）。

    返回 {"legacy_consumed", "verified_boundary_ids",
    "attributed_consumed", "legacy_unattributed_consumed",
    "new_consumed", "migrated_at"}（new_consumed 仅供调用方核验 max
    不变量，不进事件——事件字段以 §22.6 五字段冻结）。任务缺失
    TaskManagerError。纯账本操作：零宿主调用、零网络。
    """
    api = "migrate_quota_window_accounting"
    st = _require_state(repo_root, task_id, api)
    view = _continuity_view(st)
    events = journal.read_events(repo_root, task_id)
    # 一次性闸（§22.6「迁移写一次」）：已有迁移事件 → no-op（消费方
    # 重复触发 / 重放不再产生第二条迁移记录）
    for event in events:
        if event.get("event") == "quota_accounting_migrated":
            return {
                "legacy_consumed": event.get("legacy_consumed",
                                             view["consumed_quota_windows"]),
                "verified_boundary_ids": event.get(
                    "verified_boundary_ids", []),
                "attributed_consumed": event.get("attributed_consumed"),
                "legacy_unattributed_consumed": event.get(
                    "legacy_unattributed_consumed"),
                "new_consumed": view["consumed_quota_windows"],
                "migrated_at": event.get("migrated_at"),
                "idempotent": True,
            }
    # RH-03 pending 恢复闭合（先于重算，修正计划 §5.4）：marker 缺失而
    # pending 在案 → 上一进程已冻结五数、事务中断——绝不从可能已更新
    # 的 state 重算 legacy（否则迁移事件的 legacy_consumed / 归属拆分
    # 失真），按 pending 冻结值直接补 marker：
    #   state 已达 new_consumed，或本就无 state 变化（new == legacy 且
    #     state 未动）→ 零 state 写，只补 marker；
    #   new > legacy 且 state 仍为 legacy → 投影未落：补一次投影到
    #     new_consumed 再补 marker；
    #   其余（state 与 pending 冻结值矛盾 / pending 形状损坏 / 多条
    #     pending 冻结值不一致）→ TaskManagerError 零写，pending 保留
    #     待人工裁决。
    pendings = [event for event in events
                if event.get("event")
                == "quota_accounting_migration_pending"]
    if pendings:
        frozen = pendings[0]
        legacy_frozen = frozen.get("legacy_consumed")
        new_frozen = frozen.get("new_consumed")
        attributed_frozen = frozen.get("attributed_consumed")
        unattributed_frozen = frozen.get("legacy_unattributed_consumed")
        verified_frozen = frozen.get("verified_boundary_ids")
        well_shaped = (
            not isinstance(legacy_frozen, bool)
            and isinstance(legacy_frozen, int) and legacy_frozen >= 0
            and not isinstance(new_frozen, bool)
            and isinstance(new_frozen, int) and new_frozen >= 0
            and new_frozen >= legacy_frozen
            and not isinstance(attributed_frozen, bool)
            and isinstance(attributed_frozen, int)
            and attributed_frozen >= 0
            and not isinstance(unattributed_frozen, bool)
            and isinstance(unattributed_frozen, int)
            and unattributed_frozen >= 0
            and isinstance(verified_frozen, list)
            and all(
                (p.get("legacy_consumed"), p.get("new_consumed"),
                 p.get("attributed_consumed"),
                 p.get("legacy_unattributed_consumed"),
                 p.get("verified_boundary_ids"))
                == (legacy_frozen, new_frozen, attributed_frozen,
                    unattributed_frozen, verified_frozen)
                for p in pendings[1:]))
        if not well_shaped:
            raise TaskManagerError(
                "%s：quota_accounting_migration_pending 形状损坏或多条"
                " pending 冻结值互相矛盾（legacy=%r new=%r attributed=%r"
                " unattributed=%r）——恢复闭合中止，零写，pending 保留"
                "待人工裁决" % (api, legacy_frozen, new_frozen,
                                attributed_frozen, unattributed_frozen))
        state_consumed = view["consumed_quota_windows"]
        if state_consumed == new_frozen:
            projection_needed = False  # 投影已落（或本就无 state 变化）
        elif (state_consumed == legacy_frozen
                and new_frozen > legacy_frozen):
            projection_needed = True   # 投影未落：补一次投影到 new
        else:
            raise TaskManagerError(
                "%s：state 与 pending 冻结值矛盾（pending 冻结 "
                "legacy_consumed=%d / new_consumed=%d，state."
                "consumed_quota_windows=%d）——恢复闭合中止，零写，"
                "pending 保留待人工裁决"
                % (api, legacy_frozen, new_frozen, state_consumed))
        if projection_needed:
            policy = st.get("execution_policy")
            if not isinstance(policy, dict):
                policy = default_execution_policy()
                st["execution_policy"] = policy
            continuity = policy.get("continuity")
            if not isinstance(continuity, dict):
                continuity = default_execution_policy()["continuity"]
                policy["continuity"] = continuity
            continuity["consumed_quota_windows"] = new_frozen
            state.save_state(repo_root, st)
        migrated_at = _utc_now_iso()
        journal.append_event(repo_root, task_id, {
            "event": "quota_accounting_migrated",
            "legacy_consumed": legacy_frozen,
            "verified_boundary_ids": verified_frozen,
            "attributed_consumed": attributed_frozen,
            "legacy_unattributed_consumed": unattributed_frozen,
            "migrated_at": migrated_at})
        return {
            "legacy_consumed": legacy_frozen,
            "verified_boundary_ids": verified_frozen,
            "attributed_consumed": attributed_frozen,
            "legacy_unattributed_consumed": unattributed_frozen,
            "new_consumed": new_frozen,
            "migrated_at": migrated_at,
        }
    legacy_consumed = view["consumed_quota_windows"]
    verified_ids = []
    seen = set()
    for event in events:
        if event.get("event") != "quota_boundary_consumed":
            continue
        # 双键联合身份去重（v2.2 C7 ③，wu-22-C7；RH-04 扩为双 boundary
        # 键联合）：每条事件的全部在案身份键（epoch_id / boundary_id /
        # representative_boundary_id / legacy executable_boundary_id，
        # 每个存在的非空 str）联合登记——事件仅当全部身份键均未见时
        # 计入，计入即全部登入 seen。同 epoch 重放 / 同窗 legacy
        # boundary-only 证据 + 新形态 epoch 证据 / 新旧键名混用只计
        # 一次，distinct 窗口照常各计一次；五字段事件形状 / one-shot /
        # max 不退款语义零变化。
        identities = []
        for field in ("epoch_id", "boundary_id",
                      "representative_boundary_id",
                      "executable_boundary_id"):
            value = event.get(field)
            if isinstance(value, str) and value != "":
                identities.append(value)
        if not identities:
            continue  # 无身份键的条目不可核验，不计入
        if any(identity in seen for identity in identities):
            continue  # 任一身份键已见 → 同窗证据只计一次
        seen.update(identities)
        verified_ids.append(identities[0])  # 归属键：epoch_id 优先
    verified_consumed = len(verified_ids)
    new_consumed = max(legacy_consumed, verified_consumed)
    attributed = min(legacy_consumed, verified_consumed)
    unattributed = legacy_consumed - attributed
    migrated_at = _utc_now_iso()
    # RH-03 write-ahead：五数先冻结在 state 动写之前（new != legacy 与
    # new == legacy 都落 pending）——崩溃重跑按 pending 补 marker，绝不
    # 从已更新 state 重算 legacy
    journal.append_event(repo_root, task_id, {
        "event": "quota_accounting_migration_pending",
        "legacy_consumed": legacy_consumed,
        "verified_boundary_ids": verified_ids,
        "attributed_consumed": attributed,
        "legacy_unattributed_consumed": unattributed,
        "new_consumed": new_consumed})
    if new_consumed != legacy_consumed:
        policy = st.get("execution_policy")
        if not isinstance(policy, dict):
            policy = default_execution_policy()
            st["execution_policy"] = policy
        continuity = policy.get("continuity")
        if not isinstance(continuity, dict):
            continuity = default_execution_policy()["continuity"]
            policy["continuity"] = continuity
        continuity["consumed_quota_windows"] = new_consumed
        state.save_state(repo_root, st)
    journal.append_event(repo_root, task_id, {
        "event": "quota_accounting_migrated",
        "legacy_consumed": legacy_consumed,
        "verified_boundary_ids": verified_ids,
        "attributed_consumed": attributed,
        "legacy_unattributed_consumed": unattributed,
        "migrated_at": migrated_at})
    return {
        "legacy_consumed": legacy_consumed,
        "verified_boundary_ids": verified_ids,
        "attributed_consumed": attributed,
        "legacy_unattributed_consumed": unattributed,
        "new_consumed": new_consumed,
        "migrated_at": migrated_at,
    }
