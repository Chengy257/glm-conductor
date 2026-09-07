#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GLM Conductor v2 quota SUBSCRIPTION 订阅域 —— v2.2.1 WU-221-C1（行为保持抽取）。

职责：
    「Task 只订阅 quota，不再拥有 quota clock」（v2.2 C5a，wu-22-C5a；
    主计划 §14 规范 adapted）的规范定义落点：订阅三事务 API（注册
    register_quota_subscription / 纯资格判定 evaluate_subscription_
    eligibility / 激活记账 mark_activation_epoch）＋ C5b 接线助手
    （订阅接线段：_epoch_context_from_snapshot / _epoch_context_at_
    transition / _epoch_context_after_refresh 的零网络 epoch 折算、
    _reconcile_activation_journal 崩溃窗口对账、_subscription_active
    订阅态谓词）＋ 三方共用的直接读者缓存身份闸
    _cache_identity_usable（v2.2.1 WU-221-B2 QuotaIdentity）。v2.2.1
    WU-221-C1（行为保持抽取）自 runtime.task_manager 原文抽取（行为
    保持：同 journal 事件形状与写序、同幂等语义、同错误口径，函数体
    逐字未改，仅 import 适配）。转态编排（handle_quota_exhausted）与
    resume 事务编排（resume_from_quota / _resume_consumption）仍留在
    runtime.task_manager——其内部调用须经 task_manager 模块名字空间
    解析（测试 monkeypatch 面契约），本模块只承载订阅域本身。

依赖方向（冻结，防循环）：
    task_manager / continuity.resume → 本模块，绝不反向——本模块禁止
    import runtime.task_manager（continuity.resume 单向 import 本模块，
    故本模块亦不得 import continuity.resume）。与抽取域共用、原属
    task_manager 的 TaskManagerError / _require_state / _continuity_view
    / _current_provider_identity_hash 以 runtime.quota.accounting
    （v2.2.1 WU-221-C1 规范落点）为家，经 import 复用同一对象（单一
    规范落点，不复制）；上层经 re-import 使既有 task_manager.<名字>
    解析点（cli / hooks / tests）全部解析到同一对象。

    仅标准库依赖；其余为 runtime 一方模块（journal / state /
    quota.accounting；quota.epoch / quota.resolver 保持函数内 import
    ——monkeypatch 友好，与抽取前一致）。Python 3.7 兼容语法（仓库
    下限）。
"""


from runtime import journal
from runtime import state
from runtime.quota.accounting import (
    _continuity_view,
    _current_provider_identity_hash,
    _require_state,
)


def _cache_identity_usable(cache, current_identity_hash):
    """直接读者（绕过 resolver 层级的 _load_cache 消费点）的缓存身份闸
    （v2.2.1 WU-221-B2 QuotaIdentity；b2-review carryover 的收口）。

    resolver 自 WU-221-B1 起对缓存复用做身份绑定，但直接读缓存原语的
    调用方（prepare 的 execution phase 闸 / resume 的 recommended_
    resume_at / 转态点订阅折算 / CLI wake-plan）不经过该判定——本闸
    把同一身份检查补到每个直接消费点：

      - 缓存未携带 provider_identity_hash（v2.2 legacy 形态）→ 可用
        （保守信任——本单元的兼容性裁决：legacy 缓存行为逐字不变）；
      - 缓存指纹 == 当前身份 → 可用；
      - 缓存指纹 != 当前身份（异身份缓存）→ 不可用：调用方视同无缓存，
        走各自既有的 no-cache 路径（fail-open，绝不据其做决策）。

    纯函数：只读入参、零 I/O、零派生（当前指纹由调用方传入——同一
    调用流程内只派生一次）。
    """
    stored = (cache.get("provider_identity_hash")
              if isinstance(cache, dict) else None)
    return stored is None or stored == current_identity_hash


# —— 订阅接线（v2.2 C5b，wu-22-C5b；C5a 三 API 的 resume 面消费） ——

def _epoch_context_from_snapshot(provider_status, snapshot):
    """(provider_status, §27 snapshot) → epoch 折算上下文（内部助手）。

    snapshot 缺失 / 无 windows / provider_status 词汇外 / evaluate_epoch
    任何异常 → None（§31 不虚构 epoch 身份——折算失败与「无数据」同一
    保守出口）；成功返回 {"epoch_id", "executable", "provider_status"}，
    epoch_id 恒为 §10.1 形状（evaluate_epoch 的窗口指纹折算，供订阅三
    API 的字符串等值判定直接消费）。纯折算：零网络零写。
    """
    windows = None
    if isinstance(snapshot, dict):
        raw = snapshot.get("windows")
        windows = raw if isinstance(raw, list) else None
    if windows is None:
        return None
    try:
        from runtime.quota import epoch as quota_epoch  # 函数内 import
        evaluated = quota_epoch.evaluate_epoch(
            provider_status=provider_status, windows=windows)
    except Exception:  # ValueError（status 词汇外）等一律按折算失败处理
        return None
    return {"epoch_id": evaluated["epoch_id"],
            "executable": evaluated["executable"],
            "provider_status": provider_status}


def _epoch_context_at_transition(repo_root):
    """EXHAUSTED 转态点（handle_quota_exhausted）的零网络 epoch 折算。

    读 resolver 缓存原语（_cache_path / _load_cache——本模块
    _evaluation_from_refreshed_cache 的同款先例，零 resolver 改动）：
    prepare 解析 EXHAUSTED 时缓存刚被刷新，转态点折算读同一份
    snapshot，与「resolver.resolve_quota_detail 的新鲜缓存层」等价。
    刻意不经 resolve_quota_detail：该入口在缓存缺位 / 陈旧时会走层级 2
    的 provider 网络抓取——额度转态是账本事务，保持零网络纪律（探测
    归 resolver 的调用方）；缓存不可用即折算失败 → None → 不注册零
    副作用（规格的 legacy 不变分支）。
    """
    try:
        from runtime.quota import resolver  # 函数内 import：monkeypatch 友好
        cache = resolver._load_cache(resolver._cache_path(repo_root))
    except Exception:
        return None
    if not isinstance(cache, dict):
        return None
    # v2.2.1 WU-221-B2（QuotaIdentity）直接读者身份闸：异身份缓存视同
    # 无缓存（→ None → 不注册零副作用，legacy 不变分支）；legacy 无指
    # 纹缓存保守信任。幸存缓存的指纹（若有）即转态点可得的身份上下文
    # ——注册面据此按「身份可得则携带」规则落 provider_identity_hash。
    if not _cache_identity_usable(
            cache, _current_provider_identity_hash()):
        return None
    context = _epoch_context_from_snapshot(cache.get("status"),
                                           cache.get("snapshot"))
    if context is not None:
        cache_identity = cache.get("provider_identity_hash")
        if isinstance(cache_identity, str) and cache_identity:
            context["provider_identity_hash"] = cache_identity
    return context


def _epoch_context_after_refresh(repo_root):
    """resume 面（resume_from_quota）的 epoch 折算（规格口径）。

    status 解析在此前已完成（resolver 强刷成功即缓存已刷新；显式
    status 直通则用既有缓存），此处经 resolver.resolve_quota_detail
    （force_refresh=False——新鲜缓存命中零网络；provider 降级路径由
    resolver 层级自行容错）读回明细，取 detail.status +
    detail.snapshot.windows 交 evaluate_epoch 折算。detail 非 dict /
    无 snapshot / 无 windows / 折算异常 → None（无法确认新 epoch）。
    """
    try:
        from runtime.quota import resolver  # 函数内 import：monkeypatch 友好
        detail = resolver.resolve_quota_detail(repo_root)
    except Exception:
        return None
    if not isinstance(detail, dict):
        return None
    return _epoch_context_from_snapshot(detail.get("status"),
                                        detail.get("snapshot"))


def _reconcile_activation_journal(repo_root, task_id, st, epoch_id,
                                  provider_identity_hash=None):
    """崩溃窗口对账（v2.2 C5b，C5a reviewer P3 的接线侧兜底）。

    evaluate 之前读控制面 journal（read_control_plane_events——内容层
    容错：坏行 / 缺文件按无证据处理；OSError 上抛）：
    存在本任务本 epoch 的 quota_epoch_advanced 而
    state.quota_subscription.last_activation_epoch_id 落后（≠ epoch_id）
    → journal 证据表明本 epoch 已激活过（如 mark 落盘后 state 被备份
    恢复 / 回滚覆盖），保守视为已激活：
      - best-effort 自愈：全新 load → 只改 last_activation_epoch_id →
        save（绝不借 mark_activation_epoch——mark 会追加第二条控制面
        事件，破坏 QC-07 每 epoch 恰一次）；失败只记 reason 不阻塞
        判定（自愈是记账修复，不是恢复闸门）；
      - 返回对账 reason（中文，点名「journal 证据对账：已激活」）；
    无证据或 state 已与 journal 一致 → None（资格判定照常）。
    方向冻结：宁可少恢复一次不重复激活（QC-07）。纯读 + 至多一次
    自愈写，零转态。

    v2.2.1 WU-221-B2（QuotaIdentity）：证据匹配升为双形态比较（
    epoch.quota_identity_matches）——journal 事件携带
    provider_identity_hash 时须等于当前身份才算本任务的激活证据，
    缺席（legacy 事件）保守信任，异身份事件按无证据处理（A 身份的
    激活绝不作为 B 身份的 QC-07 证据）。当前身份：入参非空 str 直用
    （同一 resume 流程只派生一次），否则经共享模块派生一次。
    """
    identity = (provider_identity_hash
                if isinstance(provider_identity_hash, str)
                and provider_identity_hash
                else _current_provider_identity_hash())
    from runtime.quota import epoch as quota_epoch  # 函数内 import：monkeypatch 友好
    evidence = None
    for event in journal.read_control_plane_events(repo_root):
        if (event.get("event") == "quota_epoch_advanced"
                and event.get("task_id") == task_id
                and quota_epoch.quota_identity_matches(
                    event.get("epoch_id"),
                    event.get("provider_identity_hash"),
                    epoch_id, identity)):
            evidence = event  # 正确流程下至多一条；取末条（最新证据）
    if evidence is None:
        return None
    block = st.get("quota_subscription")
    if isinstance(block, dict) \
            and block.get("last_activation_epoch_id") == epoch_id:
        return None  # state 已与 journal 一致：evaluate 自会判同 epoch
    healed = True
    try:
        fresh = state.load_state(repo_root, task_id)
        if fresh is None:
            healed = False
        else:
            fresh_block = fresh.get("quota_subscription")
            if not isinstance(fresh_block, dict):
                fresh_block = state.default_quota_subscription()
                fresh["quota_subscription"] = fresh_block
            fresh_block["last_activation_epoch_id"] = epoch_id
            state.save_state(repo_root, fresh)
    except Exception:
        healed = False
    return ("journal 证据对账：epoch %s 已激活（控制面 quota_epoch_advanced "
            "在案而 state 落后，%s）——保守视为已激活，本 epoch 不重复激活"
            % (epoch_id,
               "已自愈重写 last_activation_epoch_id" if healed
               else "自愈重写失败"))


def _subscription_active(st) -> bool:
    """任务是否处于「已注册且启用」的订阅态（容错读，纯函数）。

    quota_subscription 块缺省 / 形状异常按默认块解释（未订阅）；
    registered_epoch_id 非 null 且 enabled 严格为 True 才算接线对象——
    legacy 任务在本开关下零分支进入，输出零变化。
    """
    block = _quota_subscription_view(st)
    return (block.get("registered_epoch_id") is not None
            and block.get("enabled") is True)


# —— Quota Subscription（v2.2 C5a，wu-22-C5a；主计划 §14 规范 adapted） ——

# §14 「Task 只订阅 quota，不再拥有 quota clock」的执行面落点：订阅事实
# 记在任务 state 顶层 quota_subscription 块（形状真相源是 state.py 的
# DEFAULT_QUOTA_SUBSCRIPTION / 规则 8.9 validator），本节提供三个事务
# API（注册 / 纯资格判定 / 激活记账）。
#
# 规格适配（§14 草图 → 本实现）：§14 草图的 registered_epoch:17 是 int
# 序数示意——epoch 身份一律用 §10.1 epoch_id 字符串（"glm:"+指纹前
# 16 hex，C2 冻结：fingerprint 等值比较、与窗口顺序无关、durable 重建
# 安全），新旧判定只用字符串等值，不引入任何 int 序数。
#
# 词汇：minimum_state / continuation_mode 两枚举与「供比较的档位序」
# 的单一真相源在 state.py（QUOTA_SUBSCRIPTION_MINIMUM_STATES /
# QUOTA_SUBSCRIPTION_CONTINUATION_MODES / is_quota_epoch_id），本节只
# 消费不复制。

# minimum_state 档位序的本地冻结映射（QC-07 判定用；序见
# QUOTA_SUBSCRIPTION_MINIMUM_STATES 注释——索引越小档位越高，
# AVAILABLE > PRESSURE > DRAINING > EXHAUSTED。provider_status 达到
# minimum_state 档位及以上 = rank(provider_status) <=
# rank(minimum_state)）。
_QUOTA_SUBSCRIPTION_STATE_RANK = {
    name: rank for rank, name in enumerate(state.QUOTA_SUBSCRIPTION_MINIMUM_STATES)
}

# register_quota_subscription 返回冻结六键（块五键视图 + idempotent）
QUOTA_SUBSCRIPTION_RESULT_KEYS = (
    "enabled", "registered_epoch_id", "last_activation_epoch_id",
    "minimum_state", "continuation_mode", "idempotent")


def _quota_subscription_view(st) -> dict:
    """容错读任务的 quota_subscription 块（legacy 缺块 / 形状异常按
    default_quota_subscription() 兜底——纠错归 validate_state 规则 8.9）。
    纯读：不改入参、零 I/O。"""
    block = st.get("quota_subscription")
    if not isinstance(block, dict):
        return state.default_quota_subscription()
    return block


def _subscription_state_satisfied(provider_status, minimum_state) -> bool:
    """provider_status 是否达到 minimum_state 档位及以上（纯函数）。

    档位序本地冻结（_QUOTA_SUBSCRIPTION_STATE_RANK 注释）：任一方不在
    四档词汇内（含观测面 UNKNOWN——订阅阈值词汇与观测四态不同，绝不
    猜测折算）→ False（保守不满足，§31 不虚构）。
    """
    rank = _QUOTA_SUBSCRIPTION_STATE_RANK.get(provider_status)
    minimum = _QUOTA_SUBSCRIPTION_STATE_RANK.get(minimum_state)
    if rank is None or minimum is None:
        return False
    return rank <= minimum


def register_quota_subscription(repo_root, task_id, *, epoch_id,
                                minimum_state="AVAILABLE",
                                continuation_mode=None,
                                provider_identity_hash=None) -> dict:
    """注册 / 更新任务的 quota subscription（§14；幂等）。

    写顶层 quota_subscription 块：enabled=True、registered_epoch_id=
    epoch_id、minimum_state、continuation_mode（五键冻结形状，过
    validate_state 规则 8.9 闸落盘）。last_activation_epoch_id 不由本
    API 触碰（激活记账归 mark_activation_epoch）。

    幂等语义（冻结口径）：块已存在且五键目标值全部相等（含经镜像解析
    后的 continuation_mode）→ 零状态写、零 journal 事件，返回当前块
    视图 + "idempotent": True；否则（无块建块 / 任一键值变化）写块 +
    任务 journal 一条 quota_subscription_registered 事件
    {registered_epoch_id, minimum_state, continuation_mode}，返回
    "idempotent": False。非幂等重写按 merge 落盘：既有块未知键原样
    保留（v2.2 C6 reviewer 留账吸收），幂等比较仍只比五规范键。

    v2.2.1 WU-221-B2（QuotaIdentity）：provider_identity_hash（可选，
    非秘密 16-hex 身份指纹）随注册落块与事件——调用点身份可得才携带
    （转态点注册取自幸存缓存的指纹；无身份上下文的调用点传 None =
    缺省，按 legacy 形态省略该键，绝不虚构占位值），且绝不为此发起
    凭证解析。幂等比较仍只比五规范键（指纹是 additive 注记，不参与
    幂等判定）；已有块不带指纹的同参重注册绝不改写存量记录补写指纹
    （无破坏性改写纪律）。

    参数（校验先于任何 I/O，中文 ValueError）：
      - epoch_id：§10.1 形状（"glm:"+16hex，state.is_quota_epoch_id）；
      - minimum_state：∈ state.QUOTA_SUBSCRIPTION_MINIMUM_STATES，缺省
        "AVAILABLE"；
      - continuation_mode：None（缺省）→ 从任务 execution_policy.
        continuity.auto_resume 容错镜像读（_continuity_view——缺块 /
        词汇外保守落 "manual"，§14.2-§14.5 同一事实源）；显式传入须
        ∈ state.QUOTA_SUBSCRIPTION_CONTINUATION_MODES；
      - provider_identity_hash：None（缺省）→ 块与事件均省略该键
        （legacy 形态）；非 None 须为非空 str。

    任务缺失 → TaskManagerError；JSON 损坏 ValueError 上抛。
    返回冻结六键 dict（QUOTA_SUBSCRIPTION_RESULT_KEYS）。
    """
    api = "register_quota_subscription"
    if not state.is_quota_epoch_id(epoch_id):
        raise ValueError(
            "%s：epoch_id 必须是 \"glm:\"+16 位十六进制的 epoch_id"
            "（§10.1 形状），得到 %r" % (api, epoch_id))
    if minimum_state not in state.QUOTA_SUBSCRIPTION_MINIMUM_STATES:
        raise ValueError(
            "%s：minimum_state %r 不在合法取值内（%s）"
            % (api, minimum_state,
               ", ".join(state.QUOTA_SUBSCRIPTION_MINIMUM_STATES)))
    if continuation_mode is not None \
            and continuation_mode \
            not in state.QUOTA_SUBSCRIPTION_CONTINUATION_MODES:
        raise ValueError(
            "%s：continuation_mode %r 不在合法取值内（%s）"
            % (api, continuation_mode,
               ", ".join(state.QUOTA_SUBSCRIPTION_CONTINUATION_MODES)))
    if provider_identity_hash is not None \
            and (not isinstance(provider_identity_hash, str)
                 or provider_identity_hash == ""):
        raise ValueError(
            "%s：provider_identity_hash 必须是 None（legacy 形态，省略"
            "身份指纹）或非空 str（16-hex 非秘密指纹），得到 %r"
            % (api, provider_identity_hash))
    st = _require_state(repo_root, task_id, api)
    if continuation_mode is None:
        # §14：缺省镜像执行策略的续跑授权词汇（同一事实源，不复制默认）
        continuation_mode = _continuity_view(st)["auto_resume"]
    block = st.get("quota_subscription")
    target = {
        "enabled": True,
        "registered_epoch_id": epoch_id,
        "last_activation_epoch_id": (
            block.get("last_activation_epoch_id")
            if isinstance(block, dict) else None),
        "minimum_state": minimum_state,
        "continuation_mode": continuation_mode,
    }
    if isinstance(block, dict) and all(
            block.get(key) == value for key, value in target.items()):
        # 同参重注册：零写零事件（幂等返回；返回块视图拷贝，不改 st）。
        # 幂等比较只看五规范键——既有块携带的未知键（含 WU-221-B2 的
        # provider_identity_hash）不影响幂等判定（v2.2 C6 reviewer
        # 留账吸收 + B2 additive 键纪律）
        result = _quota_subscription_view(st)
        result = dict(result)
        result["idempotent"] = True
        return result
    if isinstance(block, dict):
        # 非幂等重写路径（v2.2 C6 reviewer 留账吸收）：merge 而非整块
        # 替换——既有块的未知键原样保留（向前兼容，五规范键被 target
        # 覆盖），未来版本新增的合法键不被本 API 意外抹掉
        merged = dict(block)
        merged.update(target)
    else:
        merged = dict(target)
    if provider_identity_hash is not None:
        # v2.2.1 WU-221-B2：身份可得才随本次注册写落 additive 指纹键
        #（不参与幂等比较；None 时保持既有块原样——绝不改写存量记录
        # 补写指纹）
        merged["provider_identity_hash"] = provider_identity_hash
    st["quota_subscription"] = merged
    state.save_state(repo_root, st)
    event = {
        "event": "quota_subscription_registered",
        "registered_epoch_id": epoch_id,
        "minimum_state": minimum_state,
        "continuation_mode": continuation_mode,
    }
    if provider_identity_hash is not None:
        event["provider_identity_hash"] = provider_identity_hash
    journal.append_event(repo_root, task_id, event)
    result = dict(target)
    result["idempotent"] = False
    return result


def evaluate_subscription_eligibility(repo_root, task_id, *,
                                      current_epoch_id,
                                      current_executable,
                                      provider_status,
                                      current_provider_identity_hash=None
                                      ) -> dict:
    """纯资格判定：本任务当前 epoch 是否有资格激活（§14 eligible）。

    纯函数纪律：只读 state.json，零转态、零写、零事件、零网络——
    派发 / 恢复的编排方在 resume 决策前调用，结果只供裁决。**不做
    authorization / budget / reconcile**（§14 执行面流水线中授权 / 预算
    / 对账各归其位，接线归 C7），本函数只回答订阅本身的资格。
    （v2.2.1 WU-221-B2 注：current_provider_identity_hash 缺省 None 时
    经共享模块派生当前指纹——resolve_credential 是纯本地读取，零网络
    零写，纯函数纪律不受扰。）

    判定矩阵（全部满足才 eligible；不短路——reasons 逐条点名全部未过
    条件，审计面风格与 primer.authorize_prime 同源）：
      1. 已注册：块存在且 registered_epoch_id 非 null；
      2. 已启用：enabled 为 True；
      2b. 同身份（v2.2.1 WU-221-B2 QuotaIdentity）：块的
          provider_identity_hash 缺席（v2.2 legacy 注册 = 保守信任）
          或等于当前身份指纹——异身份注册的订阅不视为本身份的订阅
          （A 名下注册的 epoch E 不授权 B 同名 epoch 的恢复）；
      3. 新 epoch：last_activation_epoch_id 与 current_epoch_id 经
         quota_identity_matches 双形态判定为不同（epoch_id 字符串
         等值 + 记录侧指纹缺席或相等——§14 adapted：epoch_id 是
         §10.1 指纹身份，无 int 序数可比；last_activation 为 null =
         从未激活 → 通过；异身份的同名激活不构成 QC-07 拦截）；
      4. 可执行：current_executable 为真（§10.1 evaluate_epoch 的
         executable 判定结果，由调用方传入，本层不重复折算）；
      5. 阈值满足：provider_status 达到 minimum_state 档位及以上
         （四档序 AVAILABLE > PRESSURE > DRAINING > EXHAUSTED，
         _subscription_state_satisfied；词汇外值保守不满足）。

    参数（全 keyword-only，除前两个位置参数）：
      - current_provider_identity_hash：当前 provider 身份指纹
        （16-hex 非秘密）；None（缺省）→ 经共享模块派生一次；调用方
        （_resume_subscription_gate）在同流程内已派生时直传复用，避免
        二次解析凭证。

    返回冻结两键：{"eligible": bool, "reasons": [中文原因...]}。
    任务缺失 → TaskManagerError；current_epoch_id 形状非法 → ValueError
    （先于 I/O 校验；形状闸与注册口径一致，防调用方拿序数/任意串
    充当 epoch 身份）。
    """
    api = "evaluate_subscription_eligibility"
    if not state.is_quota_epoch_id(current_epoch_id):
        raise ValueError(
            "%s：current_epoch_id 必须是 \"glm:\"+16 位十六进制的 "
            "epoch_id（§10.1 形状），得到 %r" % (api, current_epoch_id))
    if current_provider_identity_hash is not None \
            and (not isinstance(current_provider_identity_hash, str)
                 or current_provider_identity_hash == ""):
        raise ValueError(
            "%s：current_provider_identity_hash 必须是 None（经共享模块"
            "派生）或非空 str（16-hex 非秘密指纹），得到 %r"
            % (api, current_provider_identity_hash))
    st = _require_state(repo_root, task_id, api)
    block = _quota_subscription_view(st)
    identity = (current_provider_identity_hash
                if current_provider_identity_hash is not None
                else _current_provider_identity_hash())
    from runtime.quota import epoch as quota_epoch  # 函数内 import：monkeypatch 友好
    reasons = []
    if block.get("registered_epoch_id") is None:
        reasons.append("任务 %s 未注册 quota subscription"
                       "（registered_epoch_id 为空）" % task_id)
    if block.get("enabled") is not True:
        reasons.append("quota subscription 未启用（enabled != true）")
    block_hash = block.get("provider_identity_hash")
    if (block.get("registered_epoch_id") is not None
            and block_hash is not None and block_hash != identity):
        reasons.append(
            "quota subscription 注册于其他 provider 身份（块的 "
            "provider_identity_hash 与当前身份不一致）——QuotaIdentity "
            "不视为本身份的订阅，保守不恢复")
    if quota_epoch.quota_identity_matches(
            block.get("last_activation_epoch_id"),
            block_hash,
            current_epoch_id, identity):
        reasons.append(
            "epoch %s 已激活过（last_activation_epoch_id 相同）——QC-07 "
            "每 epoch 恰一次，同 epoch 不得重复激活" % current_epoch_id)
    if not current_executable:
        reasons.append("当前 epoch 不可执行（current_executable != true）")
    minimum_state = block.get("minimum_state")
    if not _subscription_state_satisfied(provider_status, minimum_state):
        reasons.append(
            "provider_status %r 未达到 minimum_state %r 档位及以上"
            "（%s）" % (provider_status, minimum_state,
                        " > ".join(state.QUOTA_SUBSCRIPTION_MINIMUM_STATES)))
    return {"eligible": not reasons, "reasons": reasons}


def mark_activation_epoch(repo_root, task_id, *, epoch_id,
                          provider_identity_hash=None) -> dict:
    """记录任务在本 epoch 已激活（QC-07：每 epoch 恰一次推进记账）。

    写 quota_subscription.last_activation_epoch_id = epoch_id + 控制
    面 journal 一条 quota_epoch_advanced 事件（经
    journal.append_control_plane_event 落
    .glm-conductor/quota/events.jsonl——epoch 推进无任务上下文也可
    发生，控制面事件不进任务 journal、不借伪任务目录）。

    幂等语义（冻结口径；QC-07 的机械保证）：
      - epoch_id 与本任务 last_activation_epoch_id 经 QuotaIdentity
        双形态判定为同一次激活（epoch_id 等值 AND 块指纹缺席或等于
        当前指纹——v2.2.1 WU-221-B2：缺席 = legacy = 信任；异身份的
        同名激活不构成幂等拦截，B 的首次激活是新事件）→ 零状态写、
        零控制面事件，返回 "marked": False + "idempotent": True；
      - 其余（含首次从 null 起标、异身份同名 epoch）→ 视为推进
        （epoch_id 是 §10.1 指纹等值身份，无序数、不分前进后退）→
        更新 + 恰一条事件，返回 "marked": True + "idempotent": False。

    事件字段（控制面 journal）：{"event": "quota_epoch_advanced",
    "task_id", "epoch_id", "previous_epoch_id", "provider_identity_
    hash"}（末键 v2.2.1 WU-221-B2 增补——非秘密 16-hex 身份指纹，
    legacy 事件无此键，读侧双形态兼容）。落盘顺序：先 state 后事件；
    事件写入失败（OSError）自然上抛不吞——QC-07 证据丢失必须让调用
    方看见（绝不静默降级）。

    参数：epoch_id 须 §10.1 形状（先于 I/O 校验，ValueError）；
    provider_identity_hash：None（缺省）→ 经共享模块派生当前指纹一次
    （derive-and-emit——激活记账是权威账，写入必携带）；非 None 须为
    非空 str（调用方同流程内已派生时直传复用）。state 侧块注记同步
    落 provider_identity_hash = 当前指纹（本次激活记录的写入者身份）。
    任务缺失 → TaskManagerError。返回冻结三键
    {"marked", "last_activation_epoch_id", "idempotent"}。
    """
    api = "mark_activation_epoch"
    if not state.is_quota_epoch_id(epoch_id):
        raise ValueError(
            "%s：epoch_id 必须是 \"glm:\"+16 位十六进制的 epoch_id"
            "（§10.1 形状），得到 %r" % (api, epoch_id))
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
    block = _quota_subscription_view(st)
    previous = block.get("last_activation_epoch_id")
    from runtime.quota import epoch as quota_epoch  # 函数内 import：monkeypatch 友好
    if quota_epoch.quota_identity_matches(
            previous, block.get("provider_identity_hash"),
            epoch_id, identity):
        # 同 epoch 同身份重标：零写零事件（QC-07 每 epoch 恰一次的幂
        # 等闸；legacy 无指纹块按缺席信任语义照常拦截）
        return {"marked": False,
                "last_activation_epoch_id": previous,
                "idempotent": True}
    if not isinstance(st.get("quota_subscription"), dict):
        # legacy 缺块：按默认块落位再记账（enabled/registered 不由本
        # API 触碰——订阅与否归 register_quota_subscription）
        st["quota_subscription"] = state.default_quota_subscription()
    st["quota_subscription"]["last_activation_epoch_id"] = epoch_id
    st["quota_subscription"]["provider_identity_hash"] = identity
    state.save_state(repo_root, st)
    journal.append_control_plane_event(repo_root, {
        "event": "quota_epoch_advanced",
        "task_id": task_id,
        "epoch_id": epoch_id,
        "previous_epoch_id": previous,
        "provider_identity_hash": identity})
    return {"marked": True,
            "last_activation_epoch_id": epoch_id,
            "idempotent": False}
