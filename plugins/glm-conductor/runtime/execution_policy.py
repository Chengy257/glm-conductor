#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GLM Conductor v2.1 Execution Policy 数据层（M1，计划 §3/§5/§12）。

职责：
    execution_policy 是 v2.1 的「自动化强度授权事实源」——v2.0.1 里
    并发预算 / 续跑授权只存在于技能文本（主会话可绕过），v2.1 把它
    变为 state 顶层可选块中的运行时事实。本模块是该事实源的纯数据层
    （零 I/O、零网络、零第三方依赖）：
      - 构造：default_execution_policy() 返回 §3 冻结 schema 的保守
        默认块（全新拷贝，调用方可自由改写不影响模块常量）；
      - 校验：validate_execution_policy() 返回中文错误列表（空列表 =
        合法），聚合全部错误不短路；供 runtime.state.validate_state
        规则 8.7 以 execution_policy. 前缀聚合调用；
      - 授权写入：set_parallel_authorization() /
        set_resume_authorization() 两个纯 dict 变换（返回新 dict，
        不改入参、不触碰磁盘，调用方负责 save_state）；非法输入抛
        ValueError（中文消息含字段名），先全量校验后修改，失败零副作用；
      - 预算求值：effective_worker_budget() 按 quota 四态把策略
        max_workers 折算为有效并发预算（计划 §12 表）；
      - 阈值配置（v2.2 M1，决策记录 D5）：可选子块 quota_control——
        pressure_percent / draining_percent 两个百分比阈值（默认 35/20
        冻结供测试），default_quota_control() 容错读供 v2.2 控制回路
        （wu-22-02 control.py）消费。
    本模块不接线任何 hook / task_manager 消费方（那是 M2+ 的事）。

冻结 schema（计划 §3，逐字段；新增字段走版本演进，不在本层放宽）：
    {
      "worker_execution": {"default_mode": "background"},
      "parallelism": {"mode": "standard", "default_workers": 2,
                       "max_workers": 2, "hard_limit": 4},
      "continuity": {"mode": "resumable", "auto_resume": "manual",
                      "max_quota_windows": 0},
      "authorization": {"source": "default", "confirmed_at": null,
                         "scope": "task"}
    }
    hard_limit == 4 且不可变（>4 v2.1 直接拒绝）；legacy state 缺
    execution_policy 顶层键完全合法（R7，按本默认块解释）。

quota_control 可选子块（v2.2 M1，决策记录 D5；不在 POLICY_SUB_BLOCKS
四必填内；v2.2 M1a D15-d 增补 bridge_interval_minutes；v2.2 C4 增补
primer_enabled——§8.3 Window Primer 特性闸）：
    {"pressure_percent": 35.0, "draining_percent": 20.0,
     "bridge_interval_minutes": 60}——execution phase 阈值配置
    （pressure / draining 触发线，百分比）+ Persistent Wake Bridge 的
    native recurring watchdog 间隔分钟数（v2.3.0 W3 §10.2 语义重标：
    fallback 兜底节拍——正常 wake 时刻由动态 next_run_at 决定，本值
    不再是 normal wake interval；D15-d 保守默认 60 不变，校验不变）；
    另有可选键 primer_enabled（bool；§8.3 的 "primer.enabled" 语义落点
    ——True 才允许 Window Primer 的 control-plane 模型调用，缺省恒
    False 即结构性关闭，消费方经 primer_enabled() 容错读）。注意
    primer_enabled 与 consumed_quota_windows 同款「可选键」处理：不在
    默认块内（默认块形状由既有冻结测试逐字段锚定，缺键按 False 解释
    形状不变）；约束 0 <= draining_percent < pressure_percent <= 100
    （对合并默认后的生效配置判定）；百分比逐键有限数值（bool 拒绝）；
    bridge_interval_minutes 非 bool int 且 5 <= 值 <= 1440；未知键忽略
    （向前兼容）。

activation_transport 可选顶层键（v2.2 C6，修正计划 §16/§C6）：
    execution_policy.activation_transport 声明任务的 Activation
    Transport 身份（recurring_bridge 唯一 stable，probe_then_hold /
    self_retiming / session_injector 实验预留）。与 primer_enabled 同款
    可选键模式：DEFAULT_EXECUTION_POLICY 冻结默认块不含该键（缺键
    完全合法，legacy / 新任务形状不变，按 stable 缺省解释）；validator
    词汇闸四值；消费方（runtime.activation_transport）经
    activation_transport() 容错读——缺 / 坏形状一律缺省
    recurring_bridge，绝不抛。授权 setter（set_parallel_authorization /
    set_resume_authorization）经 _copy_policy 原样保留该键——授权写入
    不得重置用户的 transport 选择（quota_control 子块同款保留纪律）。

授权不变量（计划 §5.4 全表，全部强制，validate_execution_policy 逐条落）：
    hard_limit == 4；1 <= max_workers <= 4；
    parallelism.mode ∈ {serial, standard}，serial → max_workers == 1，
    standard → max_workers ∈ [2, 4]；
    default_workers 同受 1..4 约束且 <= max_workers；
    worker_execution.default_mode ∈ {background, foreground}；
    continuity.mode ∈ {foreground, resumable, idle}；
    auto_resume ∈ {manual, notify, auto_once, until_done}；
    auto_resume == manual/notify → max_quota_windows == 0；
    auto_resume == auto_once → max_quota_windows == 1；
    auto_resume == until_done → max_quota_windows >= 1；
    auto_resume ∈ {auto_once, until_done} → authorization.source == "user"；
    max_workers > 2 → authorization.source == "user"；
    authorization.source ∈ {default, user}；
    confirmed_at 为 ISO8601 字符串或 null（source=="user" 时必须非 null）；
    authorization.scope == "task"。

window 预算记账（v2.1 §14，wu-21-11）：
    continuity.consumed_quota_windows 是可选键（已消耗的自动续跑窗口
    预算计数，task_manager.record_quota_wake 在主会话 CronCreate 成功
    后递增——C1a 起该入口为 v2.1 legacy：v2.2 persistent path 禁止
    调用，arm/fire/create 不消费窗口预算，消费点 = resume commit
    point，C1b 落地）：缺键完全合法（按 0 解释——validate 容错缺省，
    默认块不含该键，legacy / 新任务形状不变）；存在时必须是 >= 0 的
    int（bool 拒绝）。消费方经 consumed_quota_windows() 容错读，与
    max_quota_windows 的差即剩余窗口预算。

有效并发预算（计划 §12 表）：
    AVAILABLE → 策略 max_workers；PRESSURE → 1；UNKNOWN → 1；
    EXHAUSTED → 0；quota_status 非法 / 未映射 → 0（保守）。
    policy 形状非法 / 缺失（legacy）→ 按默认策略的 max_workers 求值。

依赖方向（避免循环导入，锁死）：
    本模块 → runtime.quota.parser（仅 quota 四态词汇 QUOTA_STATUSES；
    quota/* 不导入本模块）；runtime.state 单向导入本模块（规则 8.7
    与 new_task_state 默认块）；本模块绝不 import runtime.state。
    零第三方依赖，`python3 -S` 可运行。

来源：
    v2.1 设计（已蒸馏入 docs/architecture.md：schema 冻结 / M1 API 与
    不变量 / 有效预算表；legacy 兼容：缺键合法）；v2.2 设计（quota_control
    阈值配置 / state schema 迁移：缺块合法；阈值配置落点：
    execution_policy.quota_control 子块）。
"""

import datetime
import math

from runtime.quota.parser import QUOTA_STATUSES

# —— 词汇表常量（枚举校验用） ——

# worker 执行模式（§3.1）：background 默认；foreground 仅用于结论
# 决定下一步分解 / 无其他有价值动作 / 短诊断 / 明确要求立即 join
WORKER_MODES = ("background", "foreground")
# 并发策略模式（§3.2）：v2.1 暂不实现 extended
PARALLELISM_MODES = ("serial", "standard")
# 连续性模式（§3.3；与 state.CONTINUITY_MODES 取值一致——独立声明，
# 本模块不导入 state，避免反向依赖）
CONTINUITY_MODES = ("foreground", "resumable", "idle")
# 自动续跑授权词汇（§3.3）：manual 默认；notify 只提醒不自动实施；
# auto_once 跨 1 个 quota reset；until_done 在窗口预算内持续到完成
# （不存在真正无限的 unlimited）
AUTO_RESUME_MODES = ("manual", "notify", "auto_once", "until_done")
# 授权来源词汇（§3.4）：default 保守默认；user 用户明确授权
AUTH_SOURCES = ("default", "user")
# 授权范围（§3.4）：v2.1 冻结在任务级
AUTH_SCOPE = "task"

# 并发硬上限（§3.2 冻结：hard_limit == 4 且不可变；>4 直接拒绝）
HARD_WORKER_LIMIT = 4
# serial 模式的唯一合法并发数
SERIAL_MAX_WORKERS = 1

# execution_policy 必填子块（§3 冻结 schema 四块；缺一即非法——
# 授权事实源不允许半定义形状，完整性由 validate_state 在保存闸拒绝。
# v2.2 M1 的 quota_control 是可选子块，不加入本元组）
POLICY_SUB_BLOCKS = ("worker_execution", "parallelism", "continuity",
                     "authorization")

# v2.2 M1 quota_control 可选子块的冻结默认（决策记录 D5：阈值可配置，
# 默认值冻结供测试；模块常量只读——DEFAULT_EXECUTION_POLICY 与
# default_quota_control() 各持全新拷贝，调用方改写互不波及。
# v2.2 M1a D15-d：bridge_interval_minutes 为 Persistent Wake Bridge 的
# 固定间隔保守默认——overlap 行为未验证前不收紧到 30。
# v2.3.0 W3（§10.2）语义重标：bridge_interval_minutes 是 native
# recurring watchdog interval（fallback 兜底节拍）——正常 wake 时刻由
# 动态 next_run_at（外部 retime 的精确目标时刻）决定，本值不再是
# normal wake interval；默认仍 60，校验不变）
DEFAULT_QUOTA_CONTROL = {
    "pressure_percent": 35.0,
    "draining_percent": 20.0,
    "bridge_interval_minutes": 60,
}

# §3 冻结 schema 的保守默认块（模块常量只读；default_execution_policy()
# 每次返回全新拷贝，防止调用方改动波及本常量。v2.2 M1 起含可选子块
# quota_control——不改变四必填子块契约，legacy 消费方按需取用）
DEFAULT_EXECUTION_POLICY = {
    "worker_execution": {"default_mode": "background"},
    "parallelism": {"mode": "standard", "default_workers": 2,
                    "max_workers": 2, "hard_limit": HARD_WORKER_LIMIT},
    "continuity": {"mode": "resumable", "auto_resume": "manual",
                   "max_quota_windows": 0},
    "authorization": {"source": "default", "confirmed_at": None,
                      "scope": AUTH_SCOPE},
    # v2.2 M1（D5）：可选阈值子块（缺块合法按 DEFAULT_QUOTA_CONTROL
    # 解释）；dict(...) 拷贝与同名模块常量解耦
    "quota_control": dict(DEFAULT_QUOTA_CONTROL),
}

# v2.2 C6（修正计划 §16/§C6）：顶层可选键 activation_transport 的冻结
# 四值词汇（recurring_bridge 唯一 stable，其余三个实验预留）。独立声明
# 不 import runtime.activation_transport（后者 import task_manager，
# 反向依赖成环）；与 activation_transport.TRANSPORT_KINDS 的对齐由测试
# 锚定（tests.test_activation_transport）。
ACTIVATION_TRANSPORTS = ("recurring_bridge", "probe_then_hold",
                         "self_retiming", "session_injector")

# activation_transport 的缺省 stable 传输（缺键 / 坏形状容错读落点）
DEFAULT_ACTIVATION_TRANSPORT = "recurring_bridge"

# —— 构造 ——


def default_execution_policy() -> dict:
    """返回 §3 冻结 schema 保守默认块的全新拷贝。

    每次调用构造新 dict：子块同样新造，块内嵌套 dict 值（v2.2 M1 起
    预留，如 quota_control 之后的复合子块）再做二层拷贝——调用方改写
    返回值不影响模块常量 DEFAULT_EXECUTION_POLICY，也不影响其他调用方。
    """
    policy = {}
    for name, block in DEFAULT_EXECUTION_POLICY.items():
        policy[name] = {
            key: dict(value) if isinstance(value, dict) else value
            for key, value in block.items()}
    return policy


# —— 校验 ——


def _enum_error(path, value, allowed):
    """构造枚举取值错误的中文消息（含字段路径与合法取值清单）。"""
    return "%s %r 不在合法取值内（%s）" % (path, value, ", ".join(allowed))


def _is_iso8601(value) -> bool:
    """判断 value 是否为可解析的 ISO8601 时间字符串。

    兼容结尾 Z/z 后缀（先归一为 +00:00 再解析——Python 3.11 之前
    fromisoformat 不认 Z）。非字符串 / 空串 / 解析失败 → False。
    """
    if not isinstance(value, str) or value == "":
        return False
    probe = value[:-1] + "+00:00" if value.endswith(("Z", "z")) else value
    try:
        datetime.datetime.fromisoformat(probe)
    except ValueError:
        return False
    return True


def _is_count(value) -> bool:
    """判断 value 是否为可用于计数/上限的正整数语义 int（bool 排除——
    bool 是 int 子类，True/False 不得充当并发数或窗口数）。"""
    return not isinstance(value, bool) and isinstance(value, int)


def _is_finite_number(value) -> bool:
    """判断 value 是否为有限数值（int/float；bool 排除——bool 是 int
    子类，True/False 不得充当百分比阈值；NaN / inf 非有限，同样拒绝）。
    quota_control 百分比阈值与 consumed_quota_windows 不同：允许小数
    （阈值可配置到半个百分点），故类型闸是「有限数值」而非整数。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(value)


def validate_execution_policy(policy) -> "list[str]":
    """校验 execution_policy 块，返回错误消息列表（中文，含字段路径）。

    空列表 = 合法。不抛异常；policy 非 dict →
    ["execution_policy 必须是 JSON 对象"]。
    错误路径为块内路径（parallelism.max_workers ...），由调用方
    （state.validate_state 规则 8.7）聚合时统一加 execution_policy.
    前缀；本函数独立使用时路径同样可读。

    规则（§3 冻结 schema + §5.4 授权不变量全表）：
      - 四个必填子块齐全且为 dict（缺失报「缺少必填子块」，非 dict
        报「必须是 JSON 对象」；子块形状损坏时其叶子校验跳过）；
      - 叶子字段齐全（缺失报「缺少必填键 <路径>」）+ 枚举 / 类型 /
        范围校验（§5.4 全表，见模块 docstring）；
      - 可选子块 quota_control（v2.2 M1，D5 + v2.2 M1a D15-d）：缺块
        合法；存在时必须为 dict，百分比逐键有限数值（bool 拒绝，缺键
        按默认解释——与 default_quota_control 的消费口径一致），
        bridge_interval_minutes 非 bool int 且 5-1440（缺省按默认 60
        合法），合并默认后强制
        0 <= draining_percent < pressure_percent <= 100；未知键忽略；
      - 跨字段耦合（mode↔max_workers、auto_resume↔max_quota_windows、
        auto_resume↔source、max_workers↔source、user↔confirmed_at）
        仅在涉及字段均合法时判定——非法值已有基线错误，不重复报；
      - 未知键忽略（向前兼容），不报错。
    多处非法时聚合全部错误，不短路。
    """
    if not isinstance(policy, dict):
        return ["execution_policy 必须是 JSON 对象"]
    errors = []

    # 子块形状：四块齐全且为 dict
    blocks = {}
    for name in POLICY_SUB_BLOCKS:
        if name not in policy:
            errors.append("缺少必填子块 %s" % name)
        elif not isinstance(policy[name], dict):
            errors.append("%s 必须是 JSON 对象" % name)
        else:
            blocks[name] = policy[name]

    # —— worker_execution ——
    we = blocks.get("worker_execution")
    if we is not None:
        if "default_mode" not in we:
            errors.append("缺少必填键 worker_execution.default_mode")
        elif we["default_mode"] not in WORKER_MODES:
            errors.append(_enum_error(
                "worker_execution.default_mode", we["default_mode"],
                WORKER_MODES))

    # —— parallelism ——
    par = blocks.get("parallelism")
    mode = None
    max_workers = None
    mode_ok = False
    max_ok = False
    if par is not None:
        for key in ("mode", "default_workers", "max_workers", "hard_limit"):
            if key not in par:
                errors.append("缺少必填键 parallelism.%s" % key)
        mode = par.get("mode")
        mode_ok = mode in PARALLELISM_MODES
        if "mode" in par and not mode_ok:
            errors.append(_enum_error(
                "parallelism.mode", mode, PARALLELISM_MODES))
        hard_limit = par.get("hard_limit")
        if "hard_limit" in par and (
                not _is_count(hard_limit) or hard_limit != HARD_WORKER_LIMIT):
            errors.append(
                "parallelism.hard_limit 必须为 %d（v2.1 冻结上限，"
                "不可变更）" % HARD_WORKER_LIMIT)
        max_workers = par.get("max_workers")
        if "max_workers" in par:
            if not _is_count(max_workers) or not (
                    1 <= max_workers <= HARD_WORKER_LIMIT):
                errors.append(
                    "parallelism.max_workers 必须是 1-%d 的整数"
                    "（hard_limit=%d 冻结）"
                    % (HARD_WORKER_LIMIT, HARD_WORKER_LIMIT))
            else:
                max_ok = True
        default_workers = par.get("default_workers")
        if "default_workers" in par:
            if not _is_count(default_workers) or not (
                    1 <= default_workers <= HARD_WORKER_LIMIT):
                errors.append(
                    "parallelism.default_workers 必须是 1-%d 的整数"
                    % HARD_WORKER_LIMIT)
            elif max_ok and default_workers > max_workers:
                errors.append(
                    "parallelism.default_workers=%d 超过 "
                    "parallelism.max_workers=%d（default_workers 不得超过 "
                    "max_workers）" % (default_workers, max_workers))

    # —— continuity ——
    con = blocks.get("continuity")
    auto_resume = None
    windows = None
    resume_ok = False
    windows_ok = False
    if con is not None:
        for key in ("mode", "auto_resume", "max_quota_windows"):
            if key not in con:
                errors.append("缺少必填键 continuity.%s" % key)
        c_mode = con.get("mode")
        if "mode" in con and c_mode not in CONTINUITY_MODES:
            errors.append(_enum_error(
                "continuity.mode", c_mode, CONTINUITY_MODES))
        auto_resume = con.get("auto_resume")
        resume_ok = auto_resume in AUTO_RESUME_MODES
        if "auto_resume" in con and not resume_ok:
            errors.append(_enum_error(
                "continuity.auto_resume", auto_resume, AUTO_RESUME_MODES))
        windows = con.get("max_quota_windows")
        if "max_quota_windows" in con:
            if not _is_count(windows) or windows < 0:
                errors.append(
                    "continuity.max_quota_windows 必须是 >= 0 的整数")
            else:
                windows_ok = True
        # 可选键 consumed_quota_windows（v2.1 §14 wu-21-11 window 预算
        # 记账）：缺键合法（按 0 解释）；存在时必须 >= 0 int（bool 拒绝）
        if "consumed_quota_windows" in con:
            consumed = con.get("consumed_quota_windows")
            if not _is_count(consumed) or consumed < 0:
                errors.append(
                    "continuity.consumed_quota_windows 必须是 >= 0 的整数"
                    "（可选键，缺省按 0 解释）")

    # —— authorization ——
    auth = blocks.get("authorization")
    source = None
    confirmed_at = None
    source_ok = False
    if auth is not None:
        for key in ("source", "confirmed_at", "scope"):
            if key not in auth:
                errors.append("缺少必填键 authorization.%s" % key)
        source = auth.get("source")
        source_ok = source in AUTH_SOURCES
        if "source" in auth and not source_ok:
            errors.append(_enum_error(
                "authorization.source", source, AUTH_SOURCES))
        confirmed_at = auth.get("confirmed_at")
        if "confirmed_at" in auth and confirmed_at is not None \
                and not _is_iso8601(confirmed_at):
            errors.append(
                "authorization.confirmed_at 必须是 ISO8601 字符串或 null")
        scope = auth.get("scope")
        if "scope" in auth and scope != AUTH_SCOPE:
            errors.append(
                "authorization.scope %r 不在合法取值内（%s）——v2.1 授权"
                "范围冻结在任务级" % (scope, AUTH_SCOPE))
        if source_ok and source == "user" and "confirmed_at" in auth \
                and confirmed_at is None:
            errors.append(
                'authorization.source="user" 要求 '
                "authorization.confirmed_at 非 null（用户授权必须记录"
                "确认时间）")

    # —— quota_control（v2.2 M1 可选子块，D5 阈值配置 + v2.2 M1a D15-d
    # bridge_interval_minutes；不在 POLICY_SUB_BLOCKS 四必填内——缺块
    # 完全合法，legacy 按默认 35/20/60 解释；错误路径 quota_control.，
    # 由 state 规则 8.7 聚合时再加 execution_policy. 前缀）——
    qc_block = policy.get("quota_control")
    if qc_block is not None:
        if not isinstance(qc_block, dict):
            errors.append("quota_control 必须是 JSON 对象")
        else:
            # 逐键类型闸：存在才校验（缺键按默认解释，与
            # default_quota_control 的消费口径一致）；百分比有限数值、
            # bool 拒绝
            for key in ("pressure_percent", "draining_percent"):
                if key in qc_block and not _is_finite_number(qc_block[key]):
                    errors.append(
                        "quota_control.%s 必须是有限数值（bool 拒绝），"
                        "得到 %r" % (key, qc_block[key]))
            # bridge_interval_minutes（v2.2 M1a D15-d；v2.3 W3 §10.2
            # 语义重标为 native recurring watchdog fallback 间隔——
            # 正常 wake 时刻由动态 next_run_at 决定）：非 bool int 且
            # 5-1440（bool 是 int 子类，True/False 不得充当分钟数；
            # 缺省按默认 60 合法）
            if "bridge_interval_minutes" in qc_block:
                interval = qc_block["bridge_interval_minutes"]
                if isinstance(interval, bool) or not isinstance(interval, int) \
                        or not 5 <= interval <= 1440:
                    errors.append(
                        "quota_control.bridge_interval_minutes 必须是 "
                        "5-1440 的整数（bool 拒绝），得到 %r" % (interval,))
            # primer_enabled（v2.2 C4 Window Primer，§8.3 特性闸）：存在
            # 时必须 bool（True/False；缺省按 False 解释——§8.3 结构性
            # 关闭默认，True 才允许 control-plane 模型调用）
            if "primer_enabled" in qc_block \
                    and not isinstance(qc_block["primer_enabled"], bool):
                errors.append(
                    "quota_control.primer_enabled 必须是 bool（缺省按 "
                    "false 解释——§8.3 primer 结构性关闭默认），得到 %r"
                    % (qc_block["primer_enabled"],))
            # 阈值不变量（D5 冻结约束）：对合并默认后的生效配置判定——
            # 半定义形状（单键越界）同样落网，缺省键按默认参与比较
            effective = default_quota_control(policy)
            effective_draining = effective["draining_percent"]
            effective_pressure = effective["pressure_percent"]
            if not 0 <= effective_draining < effective_pressure <= 100:
                errors.append(
                    "quota_control 阈值约束被违反：要求 "
                    "0 <= draining_percent < pressure_percent <= 100"
                    "（缺省键按默认 %s / %s 解释），得到 draining_percent="
                    "%s / pressure_percent=%s"
                    % (DEFAULT_QUOTA_CONTROL["draining_percent"],
                       DEFAULT_QUOTA_CONTROL["pressure_percent"],
                       effective_draining, effective_pressure))

    # —— activation_transport（v2.2 C6 可选顶层键，修正计划 §16/§C6
    # Activation Transport 身份声明：缺键完全合法（DEFAULT_EXECUTION_
    # POLICY 冻结块不含该键，legacy / 新任务形状不变，按 stable 缺省
    # 解释）；存在时必须 ∈ ACTIVATION_TRANSPORTS 四值——实验值合法
    # （预留声明可先行落盘），词汇闸只管形状，「实验不可 arm」语义归
    # activation_transport.arm_transport）——
    if "activation_transport" in policy \
            and policy["activation_transport"] not in ACTIVATION_TRANSPORTS:
        errors.append(_enum_error("activation_transport",
                                  policy["activation_transport"],
                                  ACTIVATION_TRANSPORTS))

    # —— 跨字段耦合（涉及字段均合法时才判，不重复报基线错误）——

    # §5.4：parallelism.mode ↔ max_workers
    if mode_ok and max_ok:
        if mode == "serial" and max_workers != SERIAL_MAX_WORKERS:
            errors.append(
                "parallelism.mode=serial 要求 parallelism.max_workers == %d"
                "（串行即单 worker），得到 %d"
                % (SERIAL_MAX_WORKERS, max_workers))
        if mode == "standard" and not (2 <= max_workers <= HARD_WORKER_LIMIT):
            errors.append(
                "parallelism.mode=standard 要求 parallelism.max_workers "
                "在 2-%d 内，得到 %d" % (HARD_WORKER_LIMIT, max_workers))

    # §5.4：auto_resume ↔ max_quota_windows
    if resume_ok and windows_ok:
        if auto_resume in ("manual", "notify") and windows != 0:
            errors.append(
                "continuity.auto_resume=%r 要求 "
                "continuity.max_quota_windows == 0（%r 不自动跨额度窗口"
                "续跑），得到 %d" % (auto_resume, auto_resume, windows))
        if auto_resume == "auto_once" and windows != 1:
            errors.append(
                "continuity.auto_resume=\"auto_once\" 要求 "
                "continuity.max_quota_windows == 1（只允许自动续跑一次），"
                "得到 %d" % windows)
        if auto_resume == "until_done" and windows < 1:
            errors.append(
                "continuity.auto_resume=\"until_done\" 要求 "
                "continuity.max_quota_windows >= 1（窗口预算必须至少 1），"
                "得到 %d" % windows)

    # §5.4：auto_resume ∈ {auto_once, until_done} → source == "user"
    if resume_ok and auto_resume in ("auto_once", "until_done") \
            and source_ok and source != "user":
        errors.append(
            "continuity.auto_resume=%r 要求 authorization.source 为 "
            '"user"（跨额度窗口自动续跑必须用户明确授权）' % auto_resume)

    # §5.4：max_workers > 2 → source == "user"
    if max_ok and max_workers > 2 and source_ok and source != "user":
        errors.append(
            "parallelism.max_workers=%d 要求 authorization.source 为 "
            '"user"（超过默认 2 的并发必须用户确认）' % max_workers)

    return errors


# —— 授权写入（纯 dict 变换：不改入参、不触碰磁盘） ——


def _require_policy_dict(policy):
    """policy 非 dict 时抛 ValueError（setter 共用的入参闸）。"""
    if not isinstance(policy, dict):
        raise ValueError(
            "execution_policy 写入失败：policy 必须是 JSON 对象，得到 %s"
            % type(policy).__name__)


def _copy_policy(policy) -> dict:
    """以默认块为底、入参子块覆盖，构造全新 policy dict（入参只读）。

    入参缺子块 / 子块形状异常时按默认块补齐（setter 的职责是产出
    可校验的完整块，legacy 半块不被放大）；子块浅拷贝即可——冻结
    schema 的叶子均为标量（str / int / None），未知键原样保留
    （向前兼容）。v2.2 M1：可选子块 quota_control 同口径原样保留
    （叶子均为标量，浅拷贝足够）——授权写入不得重置用户的阈值配置。
    """
    updated = default_execution_policy()
    for name in POLICY_SUB_BLOCKS:
        block = policy.get(name)
        if isinstance(block, dict):
            updated[name] = dict(block)
    qc_block = policy.get("quota_control")
    if isinstance(qc_block, dict):
        updated["quota_control"] = dict(qc_block)
    # v2.2 C6：可选顶层键 activation_transport 原样保留（与 quota_control
    # 子块同款保留纪律——授权写入不得重置用户的 transport 声明；值
    # 形状纠错归 validate_execution_policy，不在此放大）
    if "activation_transport" in policy:
        updated["activation_transport"] = policy["activation_transport"]
    return updated


def _validate_authorization_inputs(func_name, source, confirmed_at):
    """校验两个 setter 共用的 source / confirmed_at 入参（§3.4）。"""
    if source not in AUTH_SOURCES:
        raise ValueError(
            "%s：source %r 不在合法取值内（%s）"
            % (func_name, source, ", ".join(AUTH_SOURCES)))
    if confirmed_at is not None and not _is_iso8601(confirmed_at):
        raise ValueError(
            "%s：confirmed_at 必须是 ISO8601 字符串或 null，得到 %r"
            % (func_name, confirmed_at))
    if source == "user" and confirmed_at is None:
        raise ValueError(
            '%s：source="user" 要求 confirmed_at 非 null（用户授权必须'
            "记录确认时间）" % func_name)


def set_parallel_authorization(policy, *, max_workers, source,
                               confirmed_at) -> dict:
    """写入并发授权（§3.2 + §5.4），返回更新后的全新 policy dict。

    纯变换：不改入参、不写盘（调用方负责 save_state）。
      - max_workers：1..hard_limit(4) 的 int（bool 拒绝）；写入时按
        §3.2 语义派生 parallelism.mode（1 → serial，2-4 → standard）；
        default_workers 超过新 max_workers 时同步下调到 max_workers
        （只降不升——并发预算收紧时默认值跟随，放宽时保持保守）；
      - source / confirmed_at：§3.4 授权元数据；source="user" 必须
        给非 null confirmed_at；max_workers > 2 必须 source="user"；
      - hard_limit 不在本函数可写范围（冻结 == 4，不可变）。
    非法输入抛 ValueError（中文消息含字段名）；先全量校验后构造，
    失败时入参零副作用。
    """
    _require_policy_dict(policy)
    func_name = "set_parallel_authorization"
    if not _is_count(max_workers) or not (
            1 <= max_workers <= HARD_WORKER_LIMIT):
        raise ValueError(
            "%s：max_workers=%r 不在合法范围（1-%d，hard_limit=%d 冻结"
            "不可变）" % (func_name, max_workers, HARD_WORKER_LIMIT,
                         HARD_WORKER_LIMIT))
    _validate_authorization_inputs(func_name, source, confirmed_at)
    if max_workers > 2 and source != "user":
        raise ValueError(
            '%s：max_workers=%d 要求 source="user"（超过默认 2 的并发'
            "必须用户确认），得到 %r" % (func_name, max_workers, source))
    updated = _copy_policy(policy)
    par = updated["parallelism"]
    old_default = par.get("default_workers")
    if not _is_count(old_default) or old_default < 1:
        old_default = DEFAULT_EXECUTION_POLICY["parallelism"]["default_workers"]
    par["mode"] = "serial" if max_workers == SERIAL_MAX_WORKERS else "standard"
    par["max_workers"] = max_workers
    par["default_workers"] = min(old_default, max_workers)
    updated["authorization"] = {"source": source, "confirmed_at": confirmed_at,
                                "scope": AUTH_SCOPE}
    return updated


def set_resume_authorization(policy, *, auto_resume, max_quota_windows,
                             source, confirmed_at) -> dict:
    """写入续跑授权（§3.3 + §5.4），返回更新后的全新 policy dict。

    纯变换：不改入参、不写盘（调用方负责 save_state）。
      - auto_resume ∈ {manual, notify, auto_once, until_done}；
      - max_quota_windows：>= 0 的 int（bool 拒绝），且受 §5.4 耦合：
        manual/notify → 0；auto_once → 1；until_done >= 1；
      - auto_resume ∈ {auto_once, until_done} 必须 source="user"；
      - continuity.mode 不在本函数可写范围（它属于路由连续性选择，
        由任务编排层决定）。
    非法输入抛 ValueError（中文消息含字段名）；先全量校验后构造，
    失败时入参零副作用。
    """
    _require_policy_dict(policy)
    func_name = "set_resume_authorization"
    if auto_resume not in AUTO_RESUME_MODES:
        raise ValueError(
            "%s：auto_resume %r 不在合法取值内（%s）"
            % (func_name, auto_resume, ", ".join(AUTO_RESUME_MODES)))
    if not _is_count(max_quota_windows) or max_quota_windows < 0:
        raise ValueError(
            "%s：max_quota_windows=%r 必须是 >= 0 的整数"
            % (func_name, max_quota_windows))
    _validate_authorization_inputs(func_name, source, confirmed_at)
    if auto_resume in ("manual", "notify") and max_quota_windows != 0:
        raise ValueError(
            "%s：auto_resume=%r 要求 max_quota_windows == 0（%r 不自动"
            "跨额度窗口续跑），得到 %d"
            % (func_name, auto_resume, auto_resume, max_quota_windows))
    if auto_resume == "auto_once" and max_quota_windows != 1:
        raise ValueError(
            "%s：auto_resume=\"auto_once\" 要求 max_quota_windows == 1"
            "（只允许自动续跑一次），得到 %d"
            % (func_name, max_quota_windows))
    if auto_resume == "until_done" and max_quota_windows < 1:
        raise ValueError(
            "%s：auto_resume=\"until_done\" 要求 max_quota_windows >= 1"
            "（窗口预算必须至少 1），得到 %d" % (func_name, max_quota_windows))
    if auto_resume in ("auto_once", "until_done") and source != "user":
        raise ValueError(
            '%s：auto_resume=%r 要求 source="user"（跨额度窗口自动续跑'
            "必须用户明确授权），得到 %r" % (func_name, auto_resume, source))
    updated = _copy_policy(policy)
    con = updated["continuity"]
    con["auto_resume"] = auto_resume
    con["max_quota_windows"] = max_quota_windows
    updated["authorization"] = {"source": source, "confirmed_at": confirmed_at,
                                "scope": AUTH_SCOPE}
    return updated


# —— window 预算记账读取（v2.1 §14，wu-21-11） ——


def consumed_quota_windows(policy) -> int:
    """容错读 continuity.consumed_quota_windows（已消耗自动续跑窗口数）。

    validate 容错缺省 0 的读取面对应物：policy 非 dict / continuity
    缺块 / 键缺失 / 值形状非法（bool / 负数 / 非整数）→ 一律 0（手写
    state 不炸消费方，形状纠错归 validate_execution_policy）。
    纯函数：只读入参、零 I/O。剩余窗口预算 =
    max(0, continuity.max_quota_windows - 本函数返回值)，由消费方
    （task_manager 的授权矩阵 / recovery 渲染）自行折算。
    """
    continuity = (policy.get("continuity")
                  if isinstance(policy, dict) else None)
    consumed = (continuity.get("consumed_quota_windows")
                if isinstance(continuity, dict) else None)
    if _is_count(consumed) and consumed >= 0:
        return consumed
    return 0


# —— 阈值配置容错读（v2.2 M1，决策记录 D5） ——


def default_quota_control(policy) -> dict:
    """容错读 quota_control 子块（execution phase 阈值配置 + v2.2 M1a
    D15-d bridge 固定间隔），返回全新 dict。

    消费口径（供 wu-22-02 control.py 求 execution phase，与
    validate_execution_policy 的缺省解释一致）：
      - policy 非 dict / quota_control 缺块或非 dict → 全默认
        35 / 20 / 60；
      - 键级容错：pressure_percent / draining_percent 单键缺失或值
        形状非法（bool / 非数值 / NaN / inf）→ 该键按默认解释，合法值
        原样透传（int / float 均可）；
      - bridge_interval_minutes（v2.2 M1a D15-d）：缺失或非法（bool /
        非整数 / 越出 5-1440）→ 按默认 60 解释，合法值原样透传；
      - 未知键忽略；返回值恒含三键且为全新拷贝（改写不波及入参与
        模块常量 DEFAULT_QUOTA_CONTROL）。
    纯函数：只读入参、零 I/O；形状纠错归 validate_execution_policy。
    """
    block = (policy.get("quota_control")
             if isinstance(policy, dict) else None)
    merged = dict(DEFAULT_QUOTA_CONTROL)
    if isinstance(block, dict):
        for key in ("pressure_percent", "draining_percent"):
            if key in block and _is_finite_number(block[key]):
                merged[key] = block[key]
        interval = block.get("bridge_interval_minutes")
        if _is_count(interval) and 5 <= interval <= 1440:
            merged["bridge_interval_minutes"] = interval
    return merged


def primer_enabled(policy) -> bool:
    """容错读 quota_control.primer_enabled（v2.2 C4 Window Primer 特性
    闸，§8.3 "primer.enabled" 的语义落点），返回 bool。

    消费口径（供 runtime/quota/primer.py 的 authorize_prime 三重授权闸
    使用，与 validate_execution_policy 的缺省解释一致）：
      - policy 非 dict / quota_control 缺块或非 dict / 键缺失 → False
        （§8.3 冻结：真实 provider Phase0 完成并显式启用前，primer.enabled
        恒 false——**缺省即结构性关闭**，这是模型调用授权，方向与观察面
        fail-open 相反：坏形状绝不解释为开启）；
      - 值必须逐字是 True 才返回 True（bool 之外的一切——"true"/1/非空
        串——一律 False，机械强制非文档约定）。
    纯函数：只读入参、零 I/O；形状纠错归 validate_execution_policy。
    """
    block = (policy.get("quota_control")
             if isinstance(policy, dict) else None)
    value = (block.get("primer_enabled")
             if isinstance(block, dict) else None)
    return value is True


# —— Activation Transport 身份容错读（v2.2 C6，修正计划 §16/§C6） ——


def activation_transport(policy) -> str:
    """容错读顶层可选键 activation_transport（任务的 Activation
    Transport 身份声明），返回 ACTIVATION_TRANSPORTS 四值词汇内的 str。

    消费口径（供 runtime.activation_transport.activation_transport_
    status 使用，与 validate_execution_policy 的缺省解释一致；观测面
    fail-open 纪律——缺 / 坏形状一律缺省，绝不抛）：
      - policy 非 dict / 键缺失 → DEFAULT_ACTIVATION_TRANSPORT
        （recurring_bridge——§16/§17：stable 主路径缺省，legacy 任务
        形状零变化）；
      - 值不在 ACTIVATION_TRANSPORTS 四值词汇内（含 bool / 数字 / 空
        串等坏形状）→ 同样缺省（形状纠错归 validate_execution_policy，
        手写 state 不炸消费方）；
      - 合法值（含实验预留值）原样透传。
    纯函数：只读入参、零 I/O。
    """
    value = (policy.get("activation_transport")
             if isinstance(policy, dict) else None)
    return value if value in ACTIVATION_TRANSPORTS \
        else DEFAULT_ACTIVATION_TRANSPORT


# —— 有效并发预算（计划 §12 表） ——

# quota_status → 有效预算的映射哨兵：AVAILABLE 无固定值（用策略
# max_workers 折算）；未映射的未来词汇一律 0（保守）
_BUDGET_FROM_POLICY = object()
_QUOTA_BUDGETS = {
    "AVAILABLE": _BUDGET_FROM_POLICY,
    "PRESSURE": 1,
    "UNKNOWN": 1,
    "EXHAUSTED": 0,
}


def _policy_max_workers(policy) -> int:
    """容错读策略 max_workers：形状非法 / 缺失（legacy）→ 按默认块；
    超过冻结上限时截断到 hard_limit（手写 state 不炸消费方）。"""
    parallelism = (
        policy.get("parallelism") if isinstance(policy, dict) else None)
    max_workers = (
        parallelism.get("max_workers")
        if isinstance(parallelism, dict) else None)
    if not _is_count(max_workers) or max_workers < 1:
        max_workers = DEFAULT_EXECUTION_POLICY["parallelism"]["max_workers"]
    return min(max_workers, HARD_WORKER_LIMIT)


def effective_worker_budget(policy, quota_status) -> int:
    """按 quota 四态折算有效并发预算（计划 §12 表），返回 int。

      - AVAILABLE → 策略 max_workers（policy 形状非法 / 缺失 → 默认
        块的 2；超过冻结上限截断到 hard_limit）；
      - PRESSURE → 1；UNKNOWN → 1；EXHAUSTED → 0；
      - quota_status 非法（不在 runtime.quota.parser.QUOTA_STATUSES
        词汇内）或未来未映射的新状态 → 0（保守）。
    纯函数：零 I/O、不改入参。消费方（M2+ 的 hooks / dispatcher）
    在派发前用本函数求值，不再自行解释 quota 状态。
    """
    if quota_status not in QUOTA_STATUSES:
        return 0
    budget = _QUOTA_BUDGETS.get(quota_status, 0)
    if budget is _BUDGET_FROM_POLICY:
        return _policy_max_workers(policy)
    return budget
