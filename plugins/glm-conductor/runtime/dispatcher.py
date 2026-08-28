#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GLM Conductor v2 Work Unit 有界派发准入决策层（v2 工作块 B8.3）。

职责：
    升级指南 §64/§65/§67 的派发准入决策——主会话仍是唯一编排者
    （§65），本模块只产出「决策 dict」（谁派发 / 谁转 waiting_quota /
    谁挂起及理由），绝不发起真实子代理调用、零 I/O、零网络。
    两个纯函数：
      - patterns_conflict()：两组 ownership 声明是否可能命中同一文件
        （§66 不相交判定的保守近似，见函数 docstring）；
      - plan_dispatch()：候选 = dependency.ready_units() 按 topo_order
        排列，逐候选过四道闸门（配额 → 压力 → ownership 冲突 →
        并发余量），产出确定性决策 dict。

闸门顺序（§64/§67，逐候选，先到先裁决）：
      1. quota：EXHAUSTED → waiting_quota 组（§67 建议转态，图不
         腐化）；UNKNOWN → deferred("unknown_suppressed")（§67 保守：
         配额不可知时宁可整批挂起，也不把 ready 单元转成
         waiting_quota 之外的状态——图词汇不腐化）；
      2. 压力：PRESSURE 且未放开 small → 全部
         deferred("pressure_suppressed")；allow_small_under_pressure=
         True 时仅 "small": True 的候选放行过闸（work_unit 可选未知
         键，向前兼容），其余 deferred("small_only")——压力下只放行
         small 时，非 small 落选的理由是「仅限 small」而非压力本身；
      3. ownership 冲突：候选 ownership 与（active 单元 ownership ∪
         已批准候选 ownership）任一 patterns_conflict →
         deferred("ownership_conflict")。§66：无租约模型时 ownership
         不相交才可并行——本闸是保守近似（宁可少并行不可越界并行），
         B9 租约落地后此闸可放宽（docstring 锚定，届时改此处）；
      4. 并发余量：余量 = max_workers - len(active) - 已批准数，
         耗尽 → deferred("concurrency")；
      5. 通过 → dispatch 组，其 ownership 并入已批准集合。

active 容错语义（本工作块锁定）：
    active 中的 id 在 units 中找到对应单元就用其 ownership（不核验
    其状态是否 running——容错优先）；找不到按空 ownership 处理
    （不参与冲突判定）。len(active) 始终按入参原样计数扣减并发余量。

确定性 / 纯函数纪律：
    候选顺序恒为 topo_order 过滤序（依赖先于被依赖者、同层按 id
    升序）；同输入两次调用结果相等（含 dict 键序与列表顺序）；
    不修改入参 units / active（输出列表均为新建）。

依赖：
    runtime.dependency（就绪推导 / 拓扑排序）、runtime.ownership
    （compile_pattern——冲突近似的语义锚点）、runtime.quota.parser
    （仅 QUOTA_STATUSES 词汇；不触碰 quota 网络层 provider 抓取，
    无循环导入）。仅 Python 3 标准库，`python3 -S` 可运行。

来源：
    docs/glm-conductor-v2-upgrade-guide-final.md §61（contract）/
    §64（ready queue）/ §65（主会话唯一编排者）/ §66（并行判定）/
    §67（quota 四态行为）+ v2 升级计划工作块 B8.3。
"""

from runtime import dependency
from runtime import ownership
from runtime.quota.parser import QUOTA_STATUSES

# 缺省并发上限（保守：无显式配置时只跑一个 worker）
DEFAULT_MAX_WORKERS = 1

# deferred 理由词汇（§64/§66/§67；五个理由全部可达，覆盖映射见测试）
DEFER_REASONS = ("concurrency", "ownership_conflict", "pressure_suppressed",
                 "unknown_suppressed", "small_only")


# —— ownership 冲突保守近似（§66） ——

def _as_pattern_group(patterns) -> "list[str]":
    """把入参归一为模式列表：None → 空；裸字符串按单模式组容错
    （否则 list("src/**") 会按字符拆散，语义尽失）；其余可迭代原样
    转列表。"""
    if patterns is None:
        return []
    if isinstance(patterns, str):
        return [patterns]
    return list(patterns)


def _literal_prefix(pattern) -> tuple:
    """模式的字面量前缀段元组：首个 "**" 或首个通配段之前的全字面量段。

    - pattern 先 ownership.normalize_path 归一，再按 "/" 切段；
    - 遇 "**" 段停止（其后内容不可字面比较，如 "a/**/b" → ("a",)）；
    - 遇含 * 或 ? 的段停止（该段视为通配，不可字面比较）——纯通配
      首段（如 "*.py"）的字面量前缀即空元组 ()；
    - 纯字面量模式返回全部段（如 "src/auth" → ("src", "auth")），
      与 compile_pattern 对纯字面量模式的 `^lit(?:/.*)?$` 目录前缀
      双语义对齐。
    """
    normalized = ownership.normalize_path(pattern)
    literal = []
    for seg in normalized.split("/"):
        if seg == "**" or "*" in seg or "?" in seg:
            break
        literal.append(seg)
    return tuple(literal)


def _is_tuple_prefix(front, back) -> bool:
    """front 是否为 back 的前缀（含相等）；空元组是任何元组的前缀。"""
    if len(front) > len(back):
        return False
    return back[:len(front)] == front


def _patterns_overlap(pattern_a, pattern_b) -> bool:
    """单个模式对的保守冲突判定（前缀元组互为前缀即冲突）。"""
    prefix_a = _literal_prefix(pattern_a)
    prefix_b = _literal_prefix(pattern_b)
    return (_is_tuple_prefix(prefix_a, prefix_b)
            or _is_tuple_prefix(prefix_b, prefix_a))


def patterns_conflict(patterns_a, patterns_b) -> bool:
    """两组 ownership 声明是否可能命中同一文件（§66 保守近似）。

    【这是保守近似，不是精确相交判定】ownership 并行纪律是
    「宁可少并行、不可越界并行」：判定不确定（通配段、** 跨段等）
    时一律判冲突——§66 不确定即不并行。宁可误伤并行度，绝不放行
    可能同时写同一文件的两组 worker。

    判定规则（对两侧全部模式两两组合，任一对冲突即 True）：
      - 相等 → 冲突；
      - 字面量前缀段元组（_literal_prefix）一方是另一方的前缀
        （含相等）→ 冲突。空前缀（纯通配首段如 "*.py"、单独 "**"）
        是所有前缀的前缀 → 与任何模式冲突（保守）；纯字面量目录
        形式（如 "src/auth"）天然获得目录前缀语义（与 compile_pattern
        的 `^lit(?:/.*)?$` 对齐）；跨段通配（如 "a/**/b"）按 "**" 前
        字面量段 ("a",) 参与比较；
      - 其余（两侧字面量前缀互不为前缀）→ 不冲突（如 "src/a/**" 与
        "src/b/**"、"tests/**" 与 "src/**"；段边界对齐——"src/auth"
        与 "src/authentication.ts" 不冲突，与 compile_pattern 语义
        一致，不覆盖声明之外的路径）。

    边界：
      - 任一侧为空（None 容错同空）→ False（无声明即无冲突）；
      - 裸字符串按单模式组容错；
      - 任一侧模式非法（空路径段、非字符串等）→ OwnershipError
        向上抛（结构性错误优先于空侧短路：先编译校验全部模式）。
    """
    left = _as_pattern_group(patterns_a)
    right = _as_pattern_group(patterns_b)
    # 结构性校验先行：任一模式非法在此抛 OwnershipError
    for pattern in left:
        ownership.compile_pattern(pattern)
    for pattern in right:
        ownership.compile_pattern(pattern)
    if not left or not right:
        return False
    for pattern_a in left:
        for pattern_b in right:
            if _patterns_overlap(pattern_a, pattern_b):
                return True
    return False


# —— 派发准入决策（§64/§65/§67） ——

def _ownership_of(unit) -> "list[str]":
    """容错读取单元 ownership：仅保留非空字符串项（容错优先）。

    ownership 缺失 / 非 list/tuple → 空列表（不参与冲突判定，
    与 active 容错语义一致）；非法字符串项（如 "a//b"）不过滤，
    交给 patterns_conflict 的 compile_pattern 抛结构性错误。
    """
    owned = unit.get("ownership") if isinstance(unit, dict) else None
    if not isinstance(owned, (list, tuple)):
        return []
    return [item for item in owned
            if isinstance(item, str) and item != ""]


def _units_by_id(units) -> dict:
    """id → unit 映射（首次出现优先，非 dict 项跳过；容错与
    runtime.dependency 同风格，但本模块自持一份，不复用私有函数）。"""
    by_id = {}
    for unit in (units if isinstance(units, list) else []):
        if not isinstance(unit, dict):
            continue
        uid = unit.get("id")
        if isinstance(uid, str) and uid != "" and uid not in by_id:
            by_id[uid] = unit
    return by_id


def plan_dispatch(units, *, max_workers=DEFAULT_MAX_WORKERS, active=(),
                  quota_status="AVAILABLE",
                  allow_small_under_pressure=False) -> dict:
    """§64/§65/§67 派发准入决策：产出「谁可派发」的确定性决策 dict。

    本函数是纯决策器——主会话仍是唯一编排者（§65），它只调用本函数
    拿决策再亲自派发；这里绝不发起子代理调用、零 I/O、不修改入参。

    参数：
      - units：work unit dict 列表（§61 形状；非列表按空图处理，
        容错与 dependency 层一致）；
      - max_workers：并发上限，int 且 >= 1（bool 拒绝——它是 int
        子类但不充当槽位数），非法抛 ValueError；
      - active：当前 running 单元 id 列表（缺省空）。容错锁定：
        id 在 units 中找到单元就用其 ownership（不核验状态），
        找不到按空 ownership（不参与冲突）；len(active) 原样计数；
      - quota_status：QUOTA_STATUSES 之一（§28 词汇），非法抛
        ValueError；
      - allow_small_under_pressure：PRESSURE 下是否放行
        "small": True 的候选（work_unit 可选未知键，向前兼容）。

    决策流程（候选 = ready_units ∩ topo_order，逐候选四道闸门，
    顺序与细节见模块 docstring；ownership 闸的放宽时点见其锚定
    注释——B9 租约落地后可放宽）。

    返回：
        {"dispatch": [id...], "waiting_quota": [id...],
         "deferred": [{"id", "reason"}...],
         "max_workers": n, "active": [...], "quota_status": s}
    三个决策组均按候选（topo）顺序排列；deferred 理由词汇见
    DEFER_REASONS。同输入两次调用结果相等。
    """
    if isinstance(max_workers, bool) or not isinstance(max_workers, int) \
            or max_workers < 1:
        raise ValueError(
            "plan_dispatch：max_workers 必须是 >= 1 的整数，得到 %r"
            % (max_workers,))
    if quota_status not in QUOTA_STATUSES:
        raise ValueError(
            "plan_dispatch：quota_status %r 不在合法取值内（%s）"
            % (quota_status, ", ".join(QUOTA_STATUSES)))

    by_id = _units_by_id(units)
    active_ids = list(active)
    ready_set = set(dependency.ready_units(units))
    # 候选 = 就绪集 ∩ 确定性拓扑序（依赖先于被依赖者、同层按 id 升序）
    candidates = [uid for uid in dependency.topo_order(units)
                  if uid in ready_set]

    # active 单元的 ownership 并入初始已占集合（找不到 id → 空，跳过）
    taken_patterns = []
    for active_id in active_ids:
        unit = by_id.get(active_id)
        if isinstance(unit, dict):
            taken_patterns.extend(_ownership_of(unit))

    dispatch = []
    waiting_quota = []
    deferred = []

    for uid in candidates:
        unit = by_id[uid]
        owned = _ownership_of(unit)
        # 闸门 1：配额（§67。EXHAUSTED 建议转 waiting_quota——状态
        # 词汇内转态，图不腐化；UNKNOWN 保守挂起，绝不猜）
        if quota_status == "EXHAUSTED":
            waiting_quota.append(uid)
            continue
        if quota_status == "UNKNOWN":
            deferred.append({"id": uid, "reason": "unknown_suppressed"})
            continue
        # 闸门 2：压力（§67 保守：默认整批抑制；放开 small 时仅
        # "small": True 过闸，非 small 落选理由记 small_only）
        if quota_status == "PRESSURE":
            if not allow_small_under_pressure:
                deferred.append(
                    {"id": uid, "reason": "pressure_suppressed"})
                continue
            if unit.get("small") is not True:
                deferred.append({"id": uid, "reason": "small_only"})
                continue
        # 闸门 3：ownership 冲突（§66 保守近似；§66 无租约模型时
        # 不相交才可并行——B9 租约落地后此闸可放宽）
        if owned and patterns_conflict(owned, taken_patterns):
            deferred.append({"id": uid, "reason": "ownership_conflict"})
            continue
        # 闸门 4：并发余量 = max_workers - len(active) - 已批准数
        if len(dispatch) >= max_workers - len(active_ids):
            deferred.append({"id": uid, "reason": "concurrency"})
            continue
        # 闸门 5：全部通过 → 派发，ownership 并入已占集合
        dispatch.append(uid)
        taken_patterns.extend(owned)

    return {
        "dispatch": dispatch,
        "waiting_quota": waiting_quota,
        "deferred": deferred,
        "max_workers": max_workers,
        "active": active_ids,
        "quota_status": quota_status,
    }
