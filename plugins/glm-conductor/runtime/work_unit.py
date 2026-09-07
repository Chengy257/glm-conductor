#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GLM Conductor v2 Work Unit 数据层（v2 工作块 B8.1）。

职责：
    Work Unit 是可独立派发的最小有界实施单元（升级指南 §60/§61），
    必须有界到能装下一份正常五段式实施规格。本模块是纯数据层
    （零 I/O、零网络、零第三方依赖）：
      - 构造：new_work_unit() 构造 §61 形状的 work unit dict；
      - 校验：validate_work_unit() 返回中文错误列表（空列表 = 合法），
        供 runtime.state.validate_state 以 work_units[i] 前缀聚合调用；
      - 转换：transition_work_unit() 按 §62 状态转换表（逐字锁定，
        见 WU_TRANSITIONS）做合法状态迁移，非法转换抛 ValueError；
      - 重试记账：record_attempt() 递增 attempt 并累积 outcome
        （§73 有界重试语义：新 worker 调用不抹除失败历史）。
    后续 dispatcher（B8.3）与恢复对账（B8.4）建立在本模块与
    runtime.dependency（B8.2）之上。

状态词汇（§62 原文逐字收录）：
    pending / waiting_dependency / ready / running / waiting_quota /
    blocked / verifying / completed / failed / cancelled
    其中 completed / failed / cancelled 为终态（WU_TERMINAL_STATUSES），
    终态不接受任何转换。running → completed 仅恢复对账（§69）可用，
    正常流程必须经 verifying（§70：worker 报告只是 claim，
    须父会话亲自验证后才算 completed）。

构造与校验的分工：
    new_work_unit 只做形状校验（uid / executor / 各字符串列表项），
    status / attempt / result 等语义校验交给 validate_work_unit
    （与 runtime.state.new_task_state 的「构造不校验」风格一致）。

依赖：
    零导入（连标准库都不需要）。executor 词汇与 state.EXECUTORS
    取值一致（WU_EXECUTORS 独立声明，避免反向依赖：state 单向导入
    本模块做 work_units 校验，本模块不导入 state，无循环导入）。

来源：
    docs/history/v2.0/glm-conductor-v2-upgrade-guide-final.md §61（contract）/ §62
    （status model）/ §70（verification）/ §73（retry）+ 工作块 B8.1。
"""

# —— 词汇表常量（枚举校验用） ——

# Work Unit 全生命周期状态（§62 原文顺序）
WORK_UNIT_STATUSES = ("pending", "waiting_dependency", "ready", "running",
                      "waiting_quota", "blocked", "verifying",
                      "completed", "failed", "cancelled")
# 终态：不接受任何转换（§62「any nonterminal → cancelled」中的
# nonterminal 不含这三个；终态的取消/失败早已落定）
WU_TERMINAL_STATUSES = ("completed", "failed", "cancelled")
# 必填键（§61 Required properties）
WU_REQUIRED_KEYS = ("id", "objective", "status", "depends_on", "executor",
                    "ownership", "verification")
# 可选键（§61 Optional properties；lease 由后续派发层引入，暂不收录）。
# 词汇表外还有一个历史可选键 "small"（v2.0.1 压力放行语义）：v2.1
# （wu-21-09，§12 预算接线）起 dispatcher 不再消费——PRESSURE 的预算
# 收缩移交 execution_policy.effective_worker_budget（PRESSURE→1）。
# 键不删除、校验不拒绝（未知键向前兼容），旧 state 中的 small 原样
# 保留但无人读取。
# "runtime" 由 RB-21-01（§62 演进）收录：额度中断来源等运行时元数据
# （handle_quota_exhausted 写 runtime.quota_interrupted_from，恢复对账
# 按它区分 ready / running 落点）——内部形状自由，校验只查「存在时
# 必须是 JSON 对象」。
WU_OPTIONAL_KEYS = ("interfaces", "constraints", "attempt", "result",
                    "runtime")
# executor 词汇（与 state.EXECUTORS 取值一致；见模块 docstring 依赖说明）
WU_EXECUTORS = ("main", "flash-implementer", "visual-implementer")

# §62 状态转换表（逐字锁定，含推荐链与实际回退/恢复边）：
#   - pending → waiting_dependency → ready → running → verifying →
#     completed 为推荐主链；
#   - ready/running → waiting_quota 为配额抑制边；→ blocked 为阻断边；
#   - waiting_quota → ready 为额度恢复边；→ verifying 是 RB-21-01
#     新增的恢复对账边（§62 演进：额度中断的 running 单元恢复时经
#     reconcile 对账为 reuse_result——agent 成果可复用时直达验证，
#     不再盲目重派；推荐主链不经此边）；
#   - blocked → failed 允许彻底判死；
#   - running → verifying 是主验证边（worker 报告后进入验证）；
#     running → ready / blocked 是 §69 恢复对账边；running → completed
#     仅恢复对账用——正常流程应走 verifying（docstring 见模块头）；
#   - cancelled 从任何非终态可达（各表项的公共尾巴）；
#   - 终态（completed/failed/cancelled）不出现在任何目标清单中，
#     也不作为转换起点（无表项）。
WU_TRANSITIONS = {
    "pending": ("waiting_dependency", "ready", "cancelled"),
    "waiting_dependency": ("ready", "blocked", "cancelled"),
    "ready": ("running", "waiting_quota", "blocked", "cancelled"),
    "waiting_quota": ("ready", "verifying", "blocked", "cancelled"),
    "blocked": ("ready", "failed", "cancelled"),
    "running": ("verifying", "ready", "blocked", "waiting_quota",
                "failed", "completed", "cancelled"),
    "verifying": ("completed", "failed", "cancelled"),
}


# —— 内部消息助手（与 runtime.state 的错误消息风格一致） ——

def _enum_error(path, value, allowed):
    """构造枚举取值错误的中文消息（含字段路径与合法取值清单）。"""
    return "%s %r 不在合法取值内（%s）" % (path, value, ", ".join(allowed))


def _str_list_errors(path, value):
    """校验「字符串数组」：值必须是 list 且每项为非空 str。"""
    if not isinstance(value, list):
        return ["%s 必须是数组" % path]
    errors = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or item == "":
            errors.append("%s[%d] 必须是非空字符串" % (path, index))
    return errors


def _required_str_list_errors(path, value, why):
    """校验「必须非空的字符串数组」（ownership / verification 专用）。"""
    if not isinstance(value, list):
        return ["%s 必须是数组" % path]
    if not value:
        return ["%s 不能为空数组（%s）" % (path, why)]
    return _str_list_errors(path, value)


# —— 构造 ——

def new_work_unit(uid, objective, *, executor, ownership, verification,
                  depends_on=(), status="pending", interfaces=(),
                  constraints=(), attempt=0, result=None) -> dict:
    """构造 §61 形状的 work unit dict（只构造 + 形状校验，不查语义词汇）。

    形状校验（非法抛 ValueError / TypeError，中文消息）：
      - uid / executor：必须是非空 str（非 str → TypeError，空串 →
        ValueError）；executor 还必须在 WU_EXECUTORS 词汇内；
      - depends_on / ownership / verification / interfaces / constraints：
        必须是 list 或 tuple（否则 TypeError），各项必须是非空 str
        （否则 ValueError）；入参一律复制为新的 list，不被调用方
        后续可变操作波及。

    不校验项（交给 validate_work_unit）：
      - status 不查词汇、attempt 不查非负整数、objective 不查非空
        （构造从宽，校验从严，与 new_task_state 风格一致）；
      - ownership / verification 允许空数组构造——「必填非空数组」
        是派发语义约束，由 validate_work_unit 落地。
    """
    if not isinstance(uid, str):
        raise TypeError(
            "new_work_unit：uid 必须是字符串，得到 %s" % type(uid).__name__)
    if uid == "":
        raise ValueError("new_work_unit：uid 必须是非空字符串")
    if not isinstance(executor, str):
        raise TypeError(
            "new_work_unit：executor 必须是字符串，得到 %s"
            % type(executor).__name__)
    if executor == "":
        raise ValueError("new_work_unit：executor 必须是非空字符串")
    if executor not in WU_EXECUTORS:
        raise ValueError(_enum_error("new_work_unit：executor", executor,
                                     WU_EXECUTORS))
    str_lists = {}
    for name, value in (("depends_on", depends_on),
                        ("ownership", ownership),
                        ("verification", verification),
                        ("interfaces", interfaces),
                        ("constraints", constraints)):
        if not isinstance(value, (list, tuple)):
            raise TypeError(
                "new_work_unit：%s 必须是 list 或 tuple，得到 %s"
                % (name, type(value).__name__))
        for item in value:
            if not isinstance(item, str) or item == "":
                raise ValueError(
                    "new_work_unit：%s 的各项必须是非空字符串" % name)
        str_lists[name] = list(value)
    return {
        "id": uid,
        "objective": objective,
        "status": status,
        "depends_on": str_lists["depends_on"],
        "executor": executor,
        "ownership": str_lists["ownership"],
        "interfaces": str_lists["interfaces"],
        "constraints": str_lists["constraints"],
        "verification": str_lists["verification"],
        "attempt": attempt,
        "result": result,
    }


# —— 校验 ——

def validate_work_unit(wu) -> "list[str]":
    """校验 work unit dict，返回错误消息列表（中文，含字段路径）。

    空列表 = 合法。不抛异常；wu 非 dict → ["work_unit 必须是 JSON 对象"]。
    规则（§61 + 本工作块锁定的派发语义）：
      - 必填键齐全（WU_REQUIRED_KEYS），缺一报「缺少必填键 %s」；
      - id / objective / executor / status：非空 str（status 另查
        WORK_UNIT_STATUSES 词汇，executor 另查 WU_EXECUTORS 词汇）；
      - depends_on：字符串数组，允许空数组（无依赖的单元合法）；
      - ownership / verification：字符串数组且必须非空——无文件范围
        或无验证的单元不可派发（空数组报错）；
      - attempt：存在时必须是 >= 0 的 int（bool 是 int 子类，拒绝）；
      - result：任意 JSON 值或 None，不校验内部形状（自由 JSON）；
      - runtime：存在时必须是 JSON 对象（dict），内部形状自由不校验
        （运行时元数据——如额度中断来源 quota_interrupted_from——
        由写入方负责；RB-21-01 起收录为可选键）；
      - interfaces / constraints：自由形状，不校验（向前兼容）；
      - 未知键忽略（向前兼容），不报错。
    多处非法时聚合全部错误，不短路。
    """
    if not isinstance(wu, dict):
        return ["work_unit 必须是 JSON 对象"]
    errors = []
    for key in WU_REQUIRED_KEYS:
        if key not in wu:
            errors.append("缺少必填键 %s" % key)
    for key in ("id", "objective"):
        if key in wu:
            value = wu[key]
            if not isinstance(value, str) or value == "":
                errors.append("%s 必须是非空字符串" % key)
    if "status" in wu and wu["status"] not in WORK_UNIT_STATUSES:
        errors.append(_enum_error("status", wu["status"], WORK_UNIT_STATUSES))
    if "executor" in wu:
        executor = wu["executor"]
        if not isinstance(executor, str) or executor == "":
            errors.append("executor 必须是非空字符串")
        elif executor not in WU_EXECUTORS:
            errors.append(_enum_error("executor", executor, WU_EXECUTORS))
    if "depends_on" in wu:
        errors.extend(_str_list_errors("depends_on", wu["depends_on"]))
    for key, why in (("ownership", "无文件范围的单元不可派发"),
                     ("verification", "无验证命令的单元不可派发")):
        if key in wu:
            errors.extend(_required_str_list_errors(key, wu[key], why))
    if "attempt" in wu:
        attempt = wu["attempt"]
        # bool 是 int 的子类，但 True/False 不应充当 attempt
        if isinstance(attempt, bool) or not isinstance(attempt, int) \
                or attempt < 0:
            errors.append("attempt 必须是 >= 0 的整数")
    if "runtime" in wu and not isinstance(wu["runtime"], dict):
        errors.append("runtime 必须是 JSON 对象")
    return errors


# —— 状态转换 ——

def transition_work_unit(wu, new_status) -> dict:
    """按 WU_TRANSITIONS 转换表迁移状态，就地更新并返回同一 dict。

    - wu 非 dict、当前状态 / 目标状态不在 WORK_UNIT_STATUSES 内、
      当前状态是终态、或 (当前 → 目标) 不在转换表内 → ValueError
      （中文消息含 from → to 与合法目标清单；校验先于修改，
      失败不产生副作用）；
    - 终态（completed/failed/cancelled）不接受任何转换；
    - running → completed 仅 §69 恢复对账可用，正常流程应走
      verifying（§70）；转换函数只查表，不区分这两条路径。
    note / journal 等副作用不归本函数（journal 是任务级，B8.1 锁定不做）。
    """
    if not isinstance(wu, dict):
        raise ValueError("transition_work_unit：输入必须是 JSON 对象")
    current = wu.get("status")
    if current not in WORK_UNIT_STATUSES:
        raise ValueError(
            "transition_work_unit：当前状态 %r 不在合法取值内（%s）"
            % (current, ", ".join(WORK_UNIT_STATUSES)))
    if new_status not in WORK_UNIT_STATUSES:
        raise ValueError(
            "transition_work_unit：目标状态 %r 不在合法取值内（%s）"
            % (new_status, ", ".join(WORK_UNIT_STATUSES)))
    if current in WU_TERMINAL_STATUSES:
        raise ValueError(
            "transition_work_unit：状态转换非法：%s → %s"
            "（%s 是终态，无合法目标——终态不接受任何转换）"
            % (current, new_status, current))
    legal = WU_TRANSITIONS[current]
    if new_status not in legal:
        raise ValueError(
            "transition_work_unit：状态转换非法：%s → %s"
            "（%s 的合法目标：%s）"
            % (current, new_status, current, ", ".join(legal)))
    wu["status"] = new_status
    return wu


# —— 有界重试记账 ——

def record_attempt(wu, *, outcome=None) -> dict:
    """递增 attempt 并（可选）累积 outcome，就地修改并返回同一 dict。

    §73 有界重试语义：重试必须有界（新 worker 调用不抹除失败历史），
    本函数只做「加一 + 追加」，绝不重置 attempt 或 result。
      - attempt += 1（attempt 缺失按 0 起算；存在但非 >= 0 整数 /
        bool → ValueError，校验先于修改，失败不产生副作用）；
      - outcome 非 None 时并入 result["attempt_outcomes"] 列表：
          * result 为 None → 以 {"attempt_outcomes": [outcome]} 起始；
          * result 为 dict → 追加到既有 attempt_outcomes（缺失则创建；
            存在但非 list（自由 JSON 值，如字符串报告）→ 以
            [旧值, outcome] 起始，旧值不丢），其他键原样保留；
          * result 为其他 JSON 值（自由历史，如字符串报告）→ 以
            {"attempt_outcomes": [旧 result, outcome]} 起始，旧值不丢；
      - outcome 为 None 时只递增 attempt，不触碰 result。
    """
    if not isinstance(wu, dict):
        raise ValueError("record_attempt：输入必须是 JSON 对象")
    attempt = wu.get("attempt", 0)
    if isinstance(attempt, bool) or not isinstance(attempt, int) \
            or attempt < 0:
        raise ValueError("record_attempt：attempt 必须是 >= 0 的整数")
    wu["attempt"] = attempt + 1
    if outcome is not None:
        result = wu.get("result")
        if result is None:
            wu["result"] = {"attempt_outcomes": [outcome]}
        elif isinstance(result, dict):
            outcomes = result.get("attempt_outcomes")
            if not isinstance(outcomes, list):
                # 键缺失 → 以空列表起始；键存在但非 list（自由 JSON
                # 值）→ 旧值不静默丢弃，保留为失败历史首条再追加
                # （与下方「旧 result 非 dict」的起始路径同构）
                outcomes = [outcomes] if "attempt_outcomes" in result \
                    else []
                result["attempt_outcomes"] = outcomes
            outcomes.append(outcome)
        else:
            # 旧 result 是自由 JSON 值：作为失败历史的第一条保留
            wu["result"] = {"attempt_outcomes": [result, outcome]}
    return wu
