#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GLM Conductor v2.3.0 ZCode Scheduled Task Host Adapter（v2.3 计划 §6，
unit v23-w1）。

职责（account 级 Global Quota Clock 的宿主接触面）：
    ZCode 内部库 tasks-index.sqlite 的唯一适配器。四个公开函数：

      discover_zcode_tasks_db(explicit_path=None)
          DB 路径解析：explicit_path > 环境变量 GLM_CONDUCTOR_ZCODE_DB >
          默认 <~>/<DEFAULT_ZCODE_V2_DIRNAME>/<ZCODE_TASKS_DB_FILENAME>；
          只解析路径，不检查存在性。
      inspect_automation(db_path, automation_id)
          只读检查（mode=ro URI 打开，零写句柄）：单行冻结键集视图 +
          最近 20 条 runs 摘要；无此行 → None。原始值透传，不做时间
          转换，不解析 schedule_rule。
      retime_automation(db_path, automation_id, next_run_at, ...)
          全模块唯一生产写：事务内动态重定时（BEGIN IMMEDIATE → 行数 /
          状态校验 → 单列改写 → 读回验证 → COMMIT；任一失败必回滚）。
      probe_host_compatibility(db_path)
          只读兼容性探针（v2.3.1，host-check 子命令域逻辑）：表 / 必
          需列 / next_run_at 存储类型 / runs 表可达性汇总 + 缺失项明
          细；全程零写（四类纯读语句，绝不触碰 retime 路径）。

硬安全边界（§6.3；tests/test_zcode_schedule.py ForbiddenSqlTest 机械
锚定）：
    生产写操作全模块只有常量 _UPDATE_NEXT_RUN_AT_SQL 一条（仅改写
    next_run_at 单列）。其余一切变更（增删行、表结构变更、改 prompt /
    target_task_id / recurring / max_runs / scheduleRule）一律禁止——
    这不是风格偏好，是硬边界；相关语句 token 在本模块源码文本中机械性
    不存在（大小写不敏感逐 token 断言），唯一写语句短语恰出现一次。

纪律：
    - 与 quota / domain 逻辑零耦合：不 import runtime 包内任何模块，
      未来替换为官方 API adapter 时算法层不动；
    - 纯库：零 journal / 零 log / 零 print；
    - stdlib only（os / sqlite3），Python 3.7 兼容语法。

宿主 schema（2026-09-08 实测冻结，勿猜）：DB 默认 ~/.zcode/v2/
tasks-index.sqlite，WAL 模式；automations 表 33 列、automation_runs 表
12 列；next_run_at 为 INTEGER epoch 毫秒（可 NULL）。

来源：v2.3.0 计划 §6（unit v23-w1）。
"""

import os
import sqlite3

# —— 常量 ——

ZCODE_TASKS_DB_FILENAME = "tasks-index.sqlite"

# 默认目录段（<~>/ 展开后 split 逐段 os.path.join，跨平台、零反斜杠字面量）
DEFAULT_ZCODE_V2_DIRNAME = ".zcode/v2"

# 环境变量覆盖名（完整 DB 文件路径；测试与特殊安装用）
ZCODE_DB_ENV_VAR = "GLM_CONDUCTOR_ZCODE_DB"

# retime 前置 schema 闸的必需列（PRAGMA table_info 列集须为其超集）
REQUIRED_AUTOMATIONS_COLUMNS = frozenset((
    "automation_id", "recurring", "enabled", "lifecycle_status",
    "next_run_at"))

# next_run_at 合法上界：2100-01-01T00:00:00Z epoch 毫秒（防荒谬值；
# +365d park 约 1.83e12 合法）
MAX_NEXT_RUN_AT_EPOCH_MS = 4102444800000

DEFAULT_BUSY_TIMEOUT_MS = 10000  # SQLite busy 等待默认毫秒数

# inspect_automation 返回 dict 的冻结行键集（recent_runs 另附，见函数
# docstring；表中缺对应列时该键取 None，键集本身绝不缺）
INSPECT_AUTOMATION_KEYS = (
    "automation_id", "title", "recurring", "max_runs", "enabled",
    "lifecycle_status", "next_run_at", "last_run_at", "run_count",
    "target_task_id", "model", "provider", "mode", "thought_level",
    "schedule_rule", "dispatch_status", "last_error")

RECENT_RUNS_LIMIT = 20  # recent_runs 最大条数

# —— host-check 只读探针常量（v2.3.1，unit w2-host-check）——

# inspect_automation 的 recent_runs 来源三列（探针核对同一依赖集合，
# 不臆造列名）
RECENT_RUNS_COLUMNS = ("scheduled_at", "session_id", "outcome")

# next_run_at 存储类型抽样的合法 typeof 值（INTEGER epoch 毫秒，可 NULL）
NEXT_RUN_AT_OK_TYPES = frozenset(("integer", "null"))

# typeof 抽样上限（DISTINCT 早停，探测成本有界）
NEXT_RUN_AT_TYPE_SAMPLE_LIMIT = 8

# host-check 报告常量：adapter 唯一生产写能力 / 探针绝不试写
HOST_CHECK_WRITE_CAPABILITY = "next_run_at only"
HOST_CHECK_WRITE_PROBE = "no"

# 全模块唯一生产写语句（§6.3 硬边界；ForbiddenSqlTest 断言本短语在源码
# 中恰出现一次）
_UPDATE_NEXT_RUN_AT_SQL = (
    "update automations set next_run_at = ? where automation_id = ?")


class ZcodeScheduleError(Exception):
    """本模块全部异常的基类（宿主调度库适配失败）。"""


class ZcodeScheduleDbMissing(ZcodeScheduleError):
    """DB 文件不存在（discover 只解析不探测；inspect / retime 前置闸）。"""


class ZcodeScheduleSchemaError(ZcodeScheduleError):
    """宿主库 schema 不满足（automations 表缺失或必需列缺失）。"""


class ZcodeScheduleAutomationNotFound(ZcodeScheduleError):
    """事务内按 automation_id 精确匹配 0 行。"""


class ZcodeScheduleAmbiguousMatch(ZcodeScheduleError):
    """automation_id 匹配多于一行（宿主库疑似被外部改动，拒绝写）。"""


class ZcodeScheduleStateError(ZcodeScheduleError):
    """automation 行状态与预期不符（expect_recurring=True 而行非
    recurring）。"""


class ZcodeScheduleTimestampError(ZcodeScheduleError):
    """next_run_at 非法（非 int / bool / 越界；先于任何 I/O）。"""


class ZcodeScheduleVerificationError(ZcodeScheduleError):
    """写后读回与目标值不一致（事务已回滚，DB 保持原值）。"""


class ZcodeScheduleBusyError(ZcodeScheduleError):
    """SQLite busy / 锁等待超时（busy_timeout_ms 耗尽）等
    OperationalError 的统一包装（原 message 保留于 args）。"""


def discover_zcode_tasks_db(explicit_path=None):
    """解析 tasks-index.sqlite 路径（只解析，不检查存在性）。

    优先级：explicit_path > 环境变量 GLM_CONDUCTOR_ZCODE_DB > 默认
    <~>/<DEFAULT_ZCODE_V2_DIRNAME>/<ZCODE_TASKS_DB_FILENAME>（expanduser
    + 逐段 os.path.join，跨平台）。返回 str。"""
    if explicit_path:
        return os.fspath(explicit_path)
    from_env = os.environ.get(ZCODE_DB_ENV_VAR)
    if from_env:
        return from_env
    return os.path.join(os.path.expanduser("~"),
                        *DEFAULT_ZCODE_V2_DIRNAME.split("/"),
                        ZCODE_TASKS_DB_FILENAME)


def _read_only_connect(db_path):
    """mode=ro URI 只读打开（inspect 专用；路径统一 posix 分隔符）。"""
    uri = "file:%s?mode=ro" % os.path.abspath(db_path).replace(os.sep, "/")
    return sqlite3.connect(uri, uri=True)


def inspect_automation(db_path, automation_id):
    """只读检查单个 automation（零写句柄；原始值透传，不做时间转换）。

    DB 文件不存在 → ZcodeScheduleDbMissing；automations 表缺失 →
    ZcodeScheduleSchemaError；无此行 → None。返回 dict，行键集冻结为
    INSPECT_AUTOMATION_KEYS 十七键（automation_id / title / recurring /
    max_runs / enabled / lifecycle_status / next_run_at / last_run_at /
    run_count / target_task_id / model / provider / mode /
    thought_level / schedule_rule / dispatch_status / last_error；表中
    缺对应列时该键为 None），另附 recent_runs：automation_runs 表存在
    时取该 automation 最近 RECENT_RUNS_LIMIT 条的
    [{scheduled_at, session_id, outcome}]（scheduled_at 倒序），表不
    存在 → 空列表。"""
    if not os.path.isfile(db_path):
        raise ZcodeScheduleDbMissing(
            "inspect_automation：DB 文件不存在：%s" % (db_path,))
    conn = _read_only_connect(db_path)
    try:
        tables = set(row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"))
        if "automations" not in tables:
            raise ZcodeScheduleSchemaError(
                "inspect_automation：automations 表不存在：%s"
                % (db_path,))
        record = None
        cursor = conn.execute(
            "SELECT * FROM automations WHERE automation_id = ?",
            (automation_id,))
        row = cursor.fetchone()
        if row is not None:
            available = dict(zip(
                [column[0] for column in cursor.description], row))
            record = dict((key, available.get(key))
                          for key in INSPECT_AUTOMATION_KEYS)
        runs = []
        if "automation_runs" in tables:
            runs = [{"scheduled_at": found[0], "session_id": found[1],
                     "outcome": found[2]}
                    for found in conn.execute(
                        "SELECT scheduled_at, session_id, outcome"
                        " FROM automation_runs WHERE automation_id = ?"
                        " ORDER BY scheduled_at DESC, rowid DESC LIMIT ?",
                        (automation_id, RECENT_RUNS_LIMIT))]
    finally:
        conn.close()
    if record is None:
        return None
    record["recent_runs"] = runs
    return record


def retime_automation(db_path, automation_id, next_run_at, *,
                      expect_recurring=True,
                      busy_timeout_ms=DEFAULT_BUSY_TIMEOUT_MS):
    """全模块唯一生产写：动态重定时（事务化，单语句单列改写）。

    流程（严格序，任一失败必回滚）：
      1. 参数闸：next_run_at 必须是 0 < v <= MAX_NEXT_RUN_AT_EPOCH_MS
         的 int 且非 bool，违例 ZcodeScheduleTimestampError（先于任何
         I/O，失败零副作用）；
      2. 文件闸：os.path.isfile 否则 ZcodeScheduleDbMissing；
      3. 连接：timeout=busy_timeout_ms/1000，isolation_level=None（显
         式事务）；
      4. schema 闸：PRAGMA table_info(automations) 列集为
         REQUIRED_AUTOMATIONS_COLUMNS 超集，否则 ZcodeScheduleSchemaError
         （含 automations 表不存在——PRAGMA 返回空）；
      5. BEGIN IMMEDIATE → 行数闸：0 行 → ZcodeScheduleAutomationNotFound
         （回滚）；多于一行 → ZcodeScheduleAmbiguousMatch（回滚）；
      6. recurring 状态闸：expect_recurring=True 且行 recurring 为假值
         → ZcodeScheduleStateError（回滚）；
      7. 记录 previous_next_run_at（原始值，可 NULL）；
      8. 执行 _UPDATE_NEXT_RUN_AT_SQL（全模块唯一写语句）；
      9. 读回验证：next_run_at 不等于目标 → ROLLBACK +
         ZcodeScheduleVerificationError；
      10. COMMIT。

    返回 dict：{automation_id, previous_next_run_at, next_run_at,
    verified: True}。SQLite busy / 锁等待超时等 OperationalError →
    ZcodeScheduleBusyError（原 message 保留于 args，__cause__ 链接原
    异常）。异常安全：回滚失败绝不遮蔽主异常；连接 close 兜底释放。"""
    if isinstance(next_run_at, bool) or not isinstance(next_run_at, int) \
            or not 0 < next_run_at <= MAX_NEXT_RUN_AT_EPOCH_MS:
        raise ZcodeScheduleTimestampError(
            "retime_automation：next_run_at 必须为 0 < v <= %d 的 int"
            " epoch 毫秒且非 bool，得到 %r"
            % (MAX_NEXT_RUN_AT_EPOCH_MS, next_run_at))
    if not os.path.isfile(db_path):
        raise ZcodeScheduleDbMissing(
            "retime_automation：DB 文件不存在：%s" % (db_path,))
    conn = sqlite3.connect(
        db_path, timeout=max(0.0, float(busy_timeout_ms)) / 1000.0,
        isolation_level=None)
    try:
        columns = set(row[1] for row in conn.execute(
            "PRAGMA table_info(automations)"))
        if not REQUIRED_AUTOMATIONS_COLUMNS.issubset(columns):
            raise ZcodeScheduleSchemaError(
                "retime_automation：automations 必需列缺失（现有列 %r，"
                "必需 %r）" % (sorted(columns),
                              sorted(REQUIRED_AUTOMATIONS_COLUMNS)))
        conn.execute("BEGIN IMMEDIATE")
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM automations"
                " WHERE automation_id = ?",
                (automation_id,)).fetchone()[0]
            if count == 0:
                raise ZcodeScheduleAutomationNotFound(
                    "retime_automation：automation_id=%r 无匹配行"
                    % (automation_id,))
            if count > 1:
                raise ZcodeScheduleAmbiguousMatch(
                    "retime_automation：automation_id=%r 匹配 %d 行，"
                    "拒绝写" % (automation_id, count))
            state_row = conn.execute(
                "SELECT recurring, next_run_at FROM automations"
                " WHERE automation_id = ?",
                (automation_id,)).fetchone()
            recurring, previous_next_run_at = state_row[0], state_row[1]
            if expect_recurring and not recurring:
                raise ZcodeScheduleStateError(
                    "retime_automation：automation_id=%r recurring=%r 与"
                    " expect_recurring=True 不符"
                    % (automation_id, recurring))
            conn.execute(_UPDATE_NEXT_RUN_AT_SQL,
                         (next_run_at, automation_id))
            read_back = conn.execute(
                "SELECT next_run_at FROM automations"
                " WHERE automation_id = ?",
                (automation_id,)).fetchone()[0]
            if read_back != next_run_at:
                raise ZcodeScheduleVerificationError(
                    "retime_automation：automation_id=%r 写后读回 %r 不"
                    "等于目标 %r，已回滚"
                    % (automation_id, read_back, next_run_at))
        except BaseException:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass  # 回滚自身失败绝不遮蔽主异常（close 兜底释放）
            raise
        conn.execute("COMMIT")
    except sqlite3.OperationalError as exc:
        raise ZcodeScheduleBusyError(
            "retime_automation：%s：SQLite busy / 锁等待超时"
            "（busy_timeout_ms=%r）：%s"
            % (db_path, busy_timeout_ms, exc)) from exc
    finally:
        conn.close()
    return {"automation_id": automation_id,
            "previous_next_run_at": previous_next_run_at,
            "next_run_at": next_run_at,
            "verified": True}


def probe_host_compatibility(db_path):
    """只读兼容性探针（host-check 子命令的域逻辑；v2.3.1，unit
    w2-host-check）。ZCode 升级后宿主库 schema 可能漂移——本函数给出
    一次性的自检判定与缺失项明细。

    绝对零写（与 retime 写路径互斥，绝不调用）：mode=ro URI 打开
    （_read_only_connect 同款零写句柄），语句只有四类纯读——
    sqlite_master 表清单、PRAGMA table_info(automations) 纯元数据、
    recent_runs 三列 LIMIT 0 空读、DISTINCT typeof(next_run_at) 类型
    抽样。绝不 retime、绝不建任务、绝不合成写、绝不触碰 journal mode
    （连 journal_mode 查询都不做）、绝不改宿主配置。

    db_path 经 os.path.isfile 存在性闸：DB 缺失 → 干净报告（不抛异
    常、不 traceback）。打开或读 schema 遭遇 sqlite3.Error（损坏 /
    权限 / 锁不可得）→ database_readable=False 的干净报告；其余意外
    异常向上传播（由 CLI 层映射致命退出码）。

    返回 dict（键集冻结，全键恒在）：
      backend                    恒 "zcode_sqlite"
      database                   探测的 DB 路径（调用方传入，透传）
      database_found             os.path.isfile(db_path)
      database_readable          mode=ro 打开且 sqlite_master 可读
      schema                     "compatible"（automations 表存在且
                                 REQUIRED_AUTOMATIONS_COLUMNS 全在）|
                                 "incompatible"
      missing_columns            缺失必需列名（sorted；表缺失时 PRAGMA
                                 空返回 → 列为全部必需列）
      next_run_at_type_ok        DISTINCT typeof(next_run_at) 抽样
                                 （上限 NEXT_RUN_AT_TYPE_SAMPLE_LIMIT）
                                 全部 ∈ NEXT_RUN_AT_OK_TYPES；列缺失或
                                 不可测 → False；空表（无样本）→ True
      runs_table_ok              咨询性标志：automation_runs 缺失 →
                                 True（adapter 容忍，recent_runs 记空
                                 列表）；存在则 RECENT_RUNS_COLUMNS 三
                                 列 LIMIT 0 空读可执行 → True。不闸
                                 support_status（写能力不依赖 runs 表）
      issues                     人类可读明细（如 "required column
                                 missing: next_run_at" / "database not
                                 found: <path>"）
      write_capability_required  恒 HOST_CHECK_WRITE_CAPABILITY
                                 （adapter 唯一生产写能力面）
      write_probe_performed      恒 HOST_CHECK_WRITE_PROBE（探针绝不
                                 以写试写）
      support_status             "supported" ⇔ found 且 readable 且
                                 schema compatible 且
                                 next_run_at_type_ok；否则 "unsupported"
    """
    db_path = os.fspath(db_path)
    issues = []
    found = os.path.isfile(db_path)
    readable = False
    present_columns = set()
    type_ok = False
    runs_ok = False
    if not found:
        issues.append("database not found: %s" % (db_path,))
    else:
        try:
            conn = _read_only_connect(db_path)
            try:
                tables = set(row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master"
                    " WHERE type = 'table'"))
                readable = True
                present_columns = set(row[1] for row in conn.execute(
                    "PRAGMA table_info(automations)"))
                if "automation_runs" in tables:
                    try:
                        conn.execute(
                            "SELECT %s FROM automation_runs LIMIT 0"
                            % ", ".join(RECENT_RUNS_COLUMNS)).fetchall()
                        runs_ok = True
                    except sqlite3.Error as exc:
                        issues.append(
                            "automation_runs unreadable: %s: %s"
                            % (type(exc).__name__, exc))
                else:
                    runs_ok = True  # adapter 容忍缺失（recent_runs 空表）
                    issues.append("optional table missing: automation_runs")
                if "automations" not in tables:
                    issues.append("required table missing: automations")
                if "next_run_at" in present_columns:
                    sampled = set(row[0] for row in conn.execute(
                        "SELECT DISTINCT typeof(next_run_at)"
                        " FROM automations LIMIT %d"
                        % (NEXT_RUN_AT_TYPE_SAMPLE_LIMIT,)))
                    unexpected = sorted(sampled - NEXT_RUN_AT_OK_TYPES)
                    if unexpected:
                        for type_name in unexpected:
                            issues.append(
                                "next_run_at stored type not integer:"
                                " %s" % (type_name,))
                    else:
                        type_ok = True
            finally:
                conn.close()
        except sqlite3.Error as exc:
            readable = False
            issues.append("database unreadable: %s: %s"
                          % (type(exc).__name__, exc))
    missing_columns = sorted(REQUIRED_AUTOMATIONS_COLUMNS
                             - present_columns)
    for name in missing_columns:
        issues.append("required column missing: %s" % (name,))
    schema_compatible = readable and not missing_columns
    supported = found and readable and schema_compatible and type_ok
    return {
        "backend": "zcode_sqlite",
        "database": db_path,
        "database_found": found,
        "database_readable": readable,
        "schema": "compatible" if schema_compatible else "incompatible",
        "missing_columns": missing_columns,
        "next_run_at_type_ok": type_ok,
        "runs_table_ok": runs_ok,
        "issues": issues,
        "write_capability_required": HOST_CHECK_WRITE_CAPABILITY,
        "write_probe_performed": HOST_CHECK_WRITE_PROBE,
        "support_status": "supported" if supported else "unsupported",
    }
