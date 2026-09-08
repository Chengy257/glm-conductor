#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GLM Conductor v2.3.0 Global Quota Clock 用户级状态存储（v2.3 计划
§7，unit v23-w2a）。

职责：
    clock automation 绑定关系与最近一次 tick 观测的持久化：每
    provider 身份一个 state 文件，落 <home>/quota-clocks/
    <provider_identity_hash>.json。bind（绑定 clock automation）/
    load（容错读）/ update（锁保护 RMW）三入口，读写一律委托
    runtime.durable_io 的原子原语（本模块绝不自实现 tempfile /
    os.replace）。

路径解析（clock_state_path；优先级即列表序）：
    home 参数 > 环境变量 GLM_CONDUCTOR_HOME（空串视为未设）>
    默认 <~>/.glm-conductor

state 形状（v1，键集冻结为 CLOCK_STATE_KEYS 十五键）：
    schema_version / provider_identity_hash / automation_id /
    owner_repository / zcode_db_path / runtime_path / automation_model
    / fallback_interval_minutes / grace_seconds / retry_delay_seconds /
    last_tick_at / last_reset_at / next_target_at / last_retime_at /
    status；status ∈ CLOCK_STATUSES = ("bound", "parked_weekly",
    "rebound", "stale")。

架构裁决（2026-09-08，不可违背）：
    clock 是极低成本后台常驻机制——无窗口预算、无限期；额度窗口
    预算 N 是 task 侧概念（v2.2 continuity.max_quota_windows）。本
    state 的键集绝不含任何 budget / consumed 语义字段（tests 机械
    锚定）；存储层也不知道任何 quota 事实。

纪律：
    - 不 import runtime.host（adapter）/ runtime.quota.resolver /
      runtime.quota.clock——存储层只认键与值，不理解额度语义；
      provider_identity_hash 在此是不透明字符串（仅做文件名净化），
      bind 缺省 grace/retry/fallback 以本模块常量承载（数值与
      clock.DEFAULT_* 对齐，漂移由 tests 锚定）；
    - 容忍为无状态：文件缺失 / 非 dict / schema_version 不认识
      （legacy / 未来版本 / malformed）→ load 返回 None；bind 会
      重写干净态（不阻塞重绑）；
    - 冲突面唯一：已存在 status=="bound" 且 automation_id 不同 →
      ClockStateConflictError（冲突检查在 durable_io 独占锁内进行，
      原 state 原样不动）；automation_id 相同 → 幂等覆盖；
    - 不存任何凭证材料；
    - docstring 中文；stdlib only；Python 3.7 兼容语法。

来源：v2.3.0 计划 §7（Global Quota Clock），unit v23-w2a。
"""

import os

from runtime import durable_io

# —— 词汇表常量 ——

# home 根目录的环境变量覆盖名（测试与特殊安装用）
GLM_CONDUCTOR_HOME_ENV = "GLM_CONDUCTOR_HOME"

# 默认 home 目录段（<~>/ 展开后逐段 os.path.join，跨平台）
DEFAULT_HOME_DIRNAME = ".glm-conductor"

# state 文件所在目录段（<home>/ 下）
CLOCK_STORE_DIRNAME = "quota-clocks"

# state schema 版本（本版本只认 / 只写 v1）
CLOCK_STATE_SCHEMA_VERSION = 1

# status 词汇（冻结四元组）
CLOCK_STATUSES = ("bound", "parked_weekly", "rebound", "stale")

# state 冻结键集（键名逐字；绝不含 budget / consumed 语义字段——
# 架构裁决，tests 锚定）
CLOCK_STATE_KEYS = (
    "schema_version",
    "provider_identity_hash",
    "automation_id",
    "owner_repository",
    "zcode_db_path",
    "runtime_path",
    "automation_model",
    "fallback_interval_minutes",
    "grace_seconds",
    "retry_delay_seconds",
    "last_tick_at",
    "last_reset_at",
    "next_target_at",
    "last_retime_at",
    "status",
)

# bind 缺省参数（数值与 runtime.quota.clock 的 DEFAULT_GRACE_SECONDS
# = 120 / DEFAULT_RETRY_DELAY_SECONDS = 300 对齐；存储层不 import 决
# 策层，数值漂移由 tests/test_quota_clock.py 锚定）
_BIND_DEFAULT_GRACE_SECONDS = 120
_BIND_DEFAULT_RETRY_DELAY_SECONDS = 300
_BIND_DEFAULT_FALLBACK_INTERVAL_MINUTES = 60

# 文件名安全字符集（注意：刻意不含 "_"——"_" 恒走定长转义，保证
# 净化映射单射，见 _sanitize_filename_component）
_FILENAME_SAFE_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "0123456789.-")


class ClockStateError(Exception):
    """本模块全部异常的基类（clock state 存储失败 / 越界更新）。"""


class ClockStateConflictError(ClockStateError):
    """绑定冲突：已存在 status=="bound" 的 state 属于另一
    automation_id（原 state 原样不动）。"""


def _sanitize_filename_component(raw):
    """provider_identity_hash → 安全文件名成分（只含 [A-Za-z0-9._-]）。

    安全字符（字母数字与 "." "-"）原样保留；其余字符（路径分隔符
    "/" "\\"、":"、"_" 等）统一转义为 "_" + 6 位十六进制 ord。定长
    转义保证映射单射：两个不同 hash 净化后必不相同（绝不互踩），
    且产物不含任何路径分隔符（"../x" 一类攻击串退化为普通文件名，
    越不出 quota-clocks/ 目录）；"_" 本身不在保留集内（恒转义），
    故产物中每个 "_" 都是转义起始符、解析无歧义。"""
    parts = []
    for char in raw:
        if char in _FILENAME_SAFE_CHARS:
            parts.append(char)
        else:
            parts.append("_%06x" % ord(char))
    return "".join(parts)


def clock_state_path(provider_identity_hash, *, home=None):
    """解析单个 provider 的 clock state 文件路径（只解析，不探测）。

    优先级：home 参数 > 环境变量 GLM_CONDUCTOR_HOME（空串视为未设）
    > 默认 <~>/.glm-conductor。返回 str。provider_identity_hash 必须
    是 str 且净化后非空（如空串）→ ClockStateError（先于任何 I/O）。"""
    if not isinstance(provider_identity_hash, str):
        raise ClockStateError(
            "clock_state_path：provider_identity_hash 必须是 str，"
            "得到 %r" % (provider_identity_hash,))
    safe = _sanitize_filename_component(provider_identity_hash)
    if not safe:
        raise ClockStateError(
            "clock_state_path：provider_identity_hash 净化后为空：%r"
            % (provider_identity_hash,))
    if home:
        root = os.fspath(home)
    else:
        from_env = os.environ.get(GLM_CONDUCTOR_HOME_ENV)
        if from_env:
            root = from_env
        else:
            root = os.path.join(os.path.expanduser("~"),
                                DEFAULT_HOME_DIRNAME)
    return os.path.join(root, CLOCK_STORE_DIRNAME, safe + ".json")


def load_clock_state(provider_identity_hash, *, home=None):
    """读单个 provider 的 clock state → dict 或 None。

    文件缺失 / 非 dict / schema_version 不认识（legacy / 未来版本 /
    malformed）→ None——容忍为「无状态」，bind 会重写干净态。读走
    durable_io.atomic_read_json（容错读，本函数绝不抛）。"""
    path = clock_state_path(provider_identity_hash, home=home)
    data = durable_io.atomic_read_json(path, None)
    if not isinstance(data, dict):
        return None
    if data.get("schema_version") != CLOCK_STATE_SCHEMA_VERSION:
        return None
    return data


def bind_clock_state(repo_root, provider_identity_hash, automation_id, *,
                     zcode_db_path, runtime_path, automation_model=None,
                     grace_seconds=_BIND_DEFAULT_GRACE_SECONDS,
                     retry_delay_seconds=_BIND_DEFAULT_RETRY_DELAY_SECONDS,
                     fallback_interval_minutes=(
                         _BIND_DEFAULT_FALLBACK_INTERVAL_MINUTES),
                     now_ms):
    """绑定 clock automation：全键干净写入 / 幂等覆盖 / 冲突拒绝。

    流程（单次 durable_io.atomic_update_json 完成——锁内读当前值
    （文件不存在 / malformed → updater 收到空 dict）→ 冲突检查 →
    全键干净态写回；「不存在则先落初始文件再 RMW」由此单次 RMW
    原子覆盖，无需两次落盘）：
      1. 参数闸：provider_identity_hash 须净化为非空（否则
         ClockStateError）；now_ms 须为数值 epoch 毫秒（bool 拒绝，
         否则 ValueError——先于任何 I/O）；
      2. 锁内冲突检查（只认 schema 相同的可识别 state）：已存在
         status=="bound" 且 automation_id 不同 →
         ClockStateConflictError，原 state 原样不动（updater 抛错
         时 atomic_update_json 绝不写盘）；
      3. 全键按 CLOCK_STATE_KEYS 干净写入：last_tick_at /
         last_reset_at / next_target_at / last_retime_at 初始 None，
         grace / retry / fallback 取参数，owner_repository =
         repo_root；automation_id 相同的幂等重绑保留既有合法
         status（不改写运行态标签），其余一律置 "bound"；
      4. 写后 read-back（load_clock_state）返回落盘后的 state；
         读回失败 → ClockStateError。

    返回落盘后的 state dict（含全部 CLOCK_STATE_KEYS）。"""
    path = clock_state_path(provider_identity_hash)
    if isinstance(now_ms, bool) or not isinstance(now_ms, (int, float)):
        raise ValueError(
            "bind_clock_state：now_ms 必须是数值 epoch 毫秒，得到 %r"
            % (now_ms,))

    def _bind_updater(current):
        existing_status = None
        same_automation = False
        if isinstance(current, dict) and \
                current.get("schema_version") == CLOCK_STATE_SCHEMA_VERSION:
            if current.get("status") == "bound" and \
                    current.get("automation_id") != automation_id:
                raise ClockStateConflictError(
                    "bind_clock_state：provider %s 的 clock 已绑定到 "
                    "automation %r，拒绝改绑 %r（原 state 未动）"
                    % (provider_identity_hash,
                       current.get("automation_id"), automation_id))
            existing_status = current.get("status")
            same_automation = current.get("automation_id") == automation_id
        # 幂等覆盖：同一 automation_id 重绑保留既有合法 status（不改写
        # 运行态标签）；改绑新 automation / 其余情形一律置 "bound"
        status = existing_status \
            if (same_automation and existing_status in CLOCK_STATUSES) \
            else "bound"
        return {
            "schema_version": CLOCK_STATE_SCHEMA_VERSION,
            "provider_identity_hash": provider_identity_hash,
            "automation_id": automation_id,
            "owner_repository": repo_root,
            "zcode_db_path": zcode_db_path,
            "runtime_path": runtime_path,
            "automation_model": automation_model,
            "fallback_interval_minutes": fallback_interval_minutes,
            "grace_seconds": grace_seconds,
            "retry_delay_seconds": retry_delay_seconds,
            "last_tick_at": None,
            "last_reset_at": None,
            "next_target_at": None,
            "last_retime_at": None,
            "status": status,
        }

    durable_io.atomic_update_json(path, _bind_updater)
    written = load_clock_state(provider_identity_hash)
    if not isinstance(written, dict):
        raise ClockStateError(
            "bind_clock_state：写后读回失败：%s" % (path,))
    return written


def update_clock_state(provider_identity_hash, updater, *, home=None):
    """锁保护 RMW 更新单个 provider 的 clock state → 更新后的 dict。

    updater(当前 state dict) → 新 state dict；更新结果的键集必须是
    CLOCK_STATE_KEYS 超集且 status ∈ CLOCK_STATUSES，越界 →
    ClockStateError（目标文件绝不被触碰，异常原样传播）。文件不存
    在 / 内容不是可识别的 v1 state → ClockStateError——损坏态的出
    路是 bind_clock_state 重写干净态，而非盲目 RMW。"""
    path = clock_state_path(provider_identity_hash, home=home)
    if not os.path.isfile(path):
        raise ClockStateError(
            "update_clock_state：clock state 文件不存在：%s" % (path,))

    def _guarded(current):
        if not isinstance(current, dict) or \
                current.get("schema_version") != CLOCK_STATE_SCHEMA_VERSION:
            raise ClockStateError(
                "update_clock_state：%s 内容不是可识别的 v%d state"
                % (path, CLOCK_STATE_SCHEMA_VERSION))
        new_state = updater(current)
        if not isinstance(new_state, dict):
            raise ClockStateError(
                "update_clock_state：updater 必须返回 dict，得到 %s"
                % (type(new_state).__name__,))
        missing = [key for key in CLOCK_STATE_KEYS if key not in new_state]
        if missing:
            raise ClockStateError(
                "update_clock_state：更新后缺少必需键 %r" % (missing,))
        if new_state.get("status") not in CLOCK_STATUSES:
            raise ClockStateError(
                "update_clock_state：status %r 不在合法取值内（%s）"
                % (new_state.get("status"), ", ".join(CLOCK_STATUSES)))
        return new_state

    return durable_io.atomic_update_json(path, _guarded)
