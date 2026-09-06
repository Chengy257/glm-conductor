#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GLM Conductor v2.2 会话 scheduler 能力事实缓存（修正计划 C6，wu-22-C6）。

职责（修正计划 §C6 / §22.5；hooks 主战场的会话侧事实面）：
    以 hook 载荷的 session_id 为键，把宿主 scheduler 能力探针的**已证
    事实**缓存到独立 durable 文件，供 PreToolUse CronCreate 嵌套创建
    fail-fast 门（职责四）零探针重放裁决——单探针纪律的数据基础：
    create=forbidden 一旦在案，后续 CronCreate 无需再放行重探测。

    存储文件：<repo_root>/.glm-conductor/scheduler/session_facts.json
    （沿用 .glm-conductor 账本目录惯例；scheduler/ 子目录与 quota/、
    tasks/ 同层不同目录，互不越界）：

        {
          "schema_version": 1,
          "sessions": {
            "<session_id>": {<FACT_FIELDS 七键>}
          }
        }

证据只进不退（merge 语义，冻结）：
    - None 不覆盖既有值（None = 本次观察未携带该维度证据）；
    - 四能力（create/update/pause/delete）一旦落为 allowed / forbidden
      即冻结——不回退 unknown、不允许翻转（单探针纪律：forbidden 在
      案后不再放行重探测，allowed 在案后拒绝嵌套拒绝升格）；
    - origin 只允许 unknown（缺记录）→ interactive / scheduled_task
      单向升级，落定后恒定（D15-e：被 Scheduled Task 触发过的会话
      禁止再创建 automation——该事实绝不被后续观察冲掉）；
    - automation_id 追加去重（追加序保持首次观察序）；
    - 全部观察均无新证据 → 零写幂等返回（updated_at 不空转）。

容量与淘汰：
    MAX_SESSIONS = 32——按 updated_at 淘汰最旧会话（不可解析的
    updated_at 视为最旧）。会话级事实是滚动工作集：陈旧会话的探测
    结论随宿主 / 会话生命周期自然失效，容量闸防止文件无限增长。

写入纪律（复制自 watcher_store 的既有规范，常量独立不共享）：
    - 原子写：v2.2.1 WU-221-A2 起委托 runtime.durable_io.
      atomic_write_json（唯一同目录临时名 + os.replace——多宿主会话
      hook 并发落账绝不在固定 tmp 名相撞），读方永不见撕裂文件；
    - PermissionError（Windows AV / 目录锁，本机已知现象）→ 有界
      重试（默认 3 次、0.2 秒间隔；常量本模块独立声明，与
      watcher_store / primer 数值一致但互不 import），耗尽 → 抛出
      明确的 SchedulerFactsError——绝不无限重试、绝不静默吞掉；
    - 读方 fail-open（观测面纪律，与写面的有界报错方向相反）：文件
      缺失 / JSON 损坏 / 形状异常 → None（= 无证据 = hook 放行），
      坏内容绝不炸消费方。

依赖：
    仅 Python 3 标准库（json / os / time / datetime）+ runtime.
    durable_io（v2.2.1 WU-221-A2 起原子写委托），零第三方依赖；
    不 import runtime.state / runtime.task_manager / quota 包（会话
    事实缓存是独立 durable 面，与任务账本零耦合）。

来源：
    docs/GLM-Conductor-v2.2-Quota-Control-Plane-Architecture-Correction-
    and-Agent-Implementation-Plan.md §C6（Activation Transport &
    Scheduler Capability Adapter）/ §22.5（观测不制造 armed）+ 工作单元
    wu-22-C6 ③。
"""

import json
import os
import time
from datetime import datetime, timezone

from runtime import durable_io

# —— 词汇表常量（独立声明，不与 watcher_store / primer 共享 import） ——

# 存储文件相对布局（<repo_root>/ 下，.glm-conductor 账本目录惯例）
SCHEDULER_DIR_PARTS = (".glm-conductor", "scheduler")
SESSION_FACTS_FILE_NAME = "session_facts.json"
FACT_SCHEMA_VERSION = 1

# 单会话事实记录的冻结字段（形状即契约：七键恰一；FACT_FIELDS 之外的
# 键不允许——事实缓存是 hook 主战场的机器面，不是开放 schema）
FACT_FIELDS = ("origin", "create", "update", "pause", "delete",
               "automation_ids", "updated_at")

# origin 的证据值词汇（unknown 不入证据词汇：缺记录即 unknown，D15-e
# 的 SCHEDULER_ORIGINS 三值中只有两个是「已证事实」）
FACT_ORIGINS = ("interactive", "scheduled_task")

# 四能力的证据值词汇（unknown 同样不入——缺记录即 unknown）
FACT_CAPABILITY_VALUES = ("allowed", "forbidden")

# 会话工作集容量（按 updated_at 淘汰最旧）
MAX_SESSIONS = 32

# PermissionError bounded retry（§6.2 门 4 纪律复制；常量独立声明）
FACT_PERMISSION_RETRY_ATTEMPTS = 3
FACT_PERMISSION_RETRY_INTERVAL_SECONDS = 0.2

# 读方容错的有界重读次数（极端竞争窗口瞬态；同 watcher_store 读侧纪律）
FACT_READ_RETRY_ATTEMPTS = 3


class SchedulerFactsError(RuntimeError):
    """session_facts.json 写入在 PermissionError 有界重试耗尽后仍失败
    （明确异常——绝不静默吞掉）；__cause__ 保留原始 PermissionError。"""


# —— 路径与时间助手 ——

def session_facts_path(repo_root) -> str:
    """会话事实缓存文件路径 <repo_root>/.glm-conductor/scheduler/
    session_facts.json。"""
    return os.path.join(str(repo_root), *SCHEDULER_DIR_PARTS,
                        SESSION_FACTS_FILE_NAME)


def _utc_now_iso() -> str:
    """当前 UTC 时刻（ISO8601，毫秒精度，Z 后缀——updated_at 落盘统一
    形态，词法序 == 时刻序）。"""
    return datetime.now(timezone.utc).isoformat(
        timespec="milliseconds").replace("+00:00", "Z")


def _parse_iso_utc(value):
    """解析 ISO8601 时刻 → aware datetime；非 str / 空 / 不可解析 →
    None（容错，淘汰排序按最旧处理）。兼容 Z/z 后缀（归一 +00:00
    后 fromisoformat——Python 3.11 之前不认 Z）。"""
    if not isinstance(value, str) or value == "":
        return None
    probe = value[:-1] + "+00:00" if value.endswith(("Z", "z")) else value
    try:
        moment = datetime.fromisoformat(probe)
    except ValueError:
        return None
    if moment.tzinfo is None:
        return None
    return moment


# —— 读方容错（fail-open：读失败 = 无证据 = hook 放行） ——

def read_session_facts(repo_root, *, attempts=FACT_READ_RETRY_ATTEMPTS):
    """读 session_facts.json → dict 或 None（本函数绝不因坏内容抛）。

    文件不存在 → None（无证据，非错误）；JSON decode / OSError → 有界
    重读（同参数，默认 FACT_READ_RETRY_ATTEMPTS 次、立即重试——极端
    竞争窗口是瞬态的），仍失败 → None（fail-open）；顶层非 dict /
    sessions 非 dict → None（形状容错）。
    """
    path = session_facts_path(repo_root)
    for _attempt in range(max(1, int(attempts))):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            continue  # 瞬态竞争窗口：有界重读
        if not isinstance(data, dict) or not isinstance(
                data.get("sessions"), dict):
            return None
        return data
    return None


def read_session_record(repo_root, session_id):
    """按 session_id 读单条会话事实 → dict 或 None（fail-open 容错）。

    账本缺失 / 损坏 / 记录非 dict / session_id 非非空 str → None
    （= 无证据；PreToolUse 门按「静默放行」处理，绝不预判）。"""
    if not isinstance(session_id, str) or session_id == "":
        return None
    data = read_session_facts(repo_root)
    if data is None:
        return None
    record = data["sessions"].get(session_id)
    return record if isinstance(record, dict) else None


# —— 原子写（v2.2.1 WU-221-A2 起委托 durable_io + PermissionError 有界重试） ——

def _write_session_facts(repo_root, data, *,
                         retry_attempts=FACT_PERMISSION_RETRY_ATTEMPTS,
                         retry_interval=FACT_PERMISSION_RETRY_INTERVAL_SECONDS,
                         sleep=time.sleep):
    """原子写 session_facts.json（v2.2.1 WU-221-A2 起委托共享原语
    runtime.durable_io.atomic_write_json：唯一同目录临时名 +
    os.replace——落盘字节与既有手写实现逐字节一致；父目录缺失由原语
    自动创建），返回路径。

    纪律复制自 watcher_store.write_watcher_state（常量独立）：data 非
    dict → ValueError（参数校验先于 I/O）；PermissionError → 有界重试
    （次数 / 间隔 / sleep 均可注入，测试零真实等待），耗尽仍失败 →
    SchedulerFactsError（__cause__ = 原始 PermissionError）；其余异常
    由原语清理临时文件后原样上抛。读方永不见撕裂文件。
    """
    if not isinstance(data, dict):
        raise ValueError(
            "scheduler_facts 写入失败：data 必须是 dict，得到 %r"
            % (type(data).__name__,))
    if retry_interval < 0:
        raise ValueError(
            "scheduler_facts 写入失败：retry_interval 必须 >= 0，得到 %r"
            % (retry_interval,))
    attempts = max(1, int(retry_attempts))
    path = session_facts_path(repo_root)
    try:
        durable_io.atomic_write_json(path, data,
                                     retry_attempts=retry_attempts,
                                     retry_interval=retry_interval,
                                     sleep=sleep)
    except PermissionError as last_permission_error:
        raise SchedulerFactsError(
            "session_facts.json 原子写在 PermissionError 有界重试 %d 次（间隔 "
            "%.2f 秒）后仍失败（Windows AV / 目录锁？）：%s" % (
                attempts, float(retry_interval), last_permission_error)
        ) from last_permission_error
    return path


# —— 证据只进不退的 merge 原语（纯函数，供 record_observation 复用） ——

def _merge_record(existing, *, origin, create, update, pause, delete,
                  automation_id):
    """按「证据只进不退」语义把一次观察合并进单条记录。

    existing 非 dict 按空记录（全 unknown）起底；返回 (record, changed)：
    record 恰含 FACT_FIELDS 七键（全新 dict，不改入参），changed 为
    实际发生变化的维度名列表（空 = 零新证据）。origin 只允许
    unknown→证据值单向升级；能力值 None 不覆盖、已证值冻结不翻转；
    automation_id 追加去重。updated_at 由调用方统一落。
    """
    record = {
        "origin": None, "create": None, "update": None, "pause": None,
        "delete": None, "automation_ids": [], "updated_at": None,
    }
    if isinstance(existing, dict):
        # 既有值只继承合法证据值（脏数据防御：词汇外值视同 unknown）
        if existing.get("origin") in FACT_ORIGINS:
            record["origin"] = existing["origin"]
        for key in ("create", "update", "pause", "delete"):
            if existing.get(key) in FACT_CAPABILITY_VALUES:
                record[key] = existing[key]
        ids = existing.get("automation_ids")
        if isinstance(ids, list):
            record["automation_ids"] = [
                item for item in ids
                if isinstance(item, str) and item]
    changed = []
    if origin in FACT_ORIGINS and record["origin"] != origin:
        if record["origin"] is None:  # 单向升级：unknown → 证据值
            record["origin"] = origin
            changed.append("origin")
    for name, value in (("create", create), ("update", update),
                        ("pause", pause), ("delete", delete)):
        if value in FACT_CAPABILITY_VALUES and record[name] is None:
            record[name] = value
            changed.append(name)
    if isinstance(automation_id, str) and automation_id != "" \
            and automation_id not in record["automation_ids"]:
        record["automation_ids"].append(automation_id)
        changed.append("automation_ids")
    return record, changed


def _prune_oldest(sessions):
    """容量闸：sessions 超 MAX_SESSIONS 时按 updated_at 淘汰最旧
    （不可解析 / 缺失视为最旧），原地修改并返回被淘汰的 session_id
    列表（淘汰数 = 超容量数，一次裁到位）。"""
    overflow = len(sessions) - MAX_SESSIONS
    if overflow <= 0:
        return []
    def sort_key(item):
        moment = _parse_iso_utc(item[1].get("updated_at")) \
            if isinstance(item[1], dict) else None
        return moment is not None, moment or datetime.min.replace(
            tzinfo=timezone.utc)
    evicted = []
    for session_id, _record in sorted(
            sessions.items(), key=sort_key)[:overflow]:
        evicted.append(session_id)
    for session_id in evicted:
        sessions.pop(session_id, None)
    return evicted


# —— 唯一写入口：观察落账 ——

def record_observation(repo_root, session_id, *, origin=None, create=None,
                       update=None, pause=None, delete=None,
                       automation_id=None, observed_at=None,
                       retry_attempts=FACT_PERMISSION_RETRY_ATTEMPTS,
                       retry_interval=FACT_PERMISSION_RETRY_INTERVAL_SECONDS,
                       sleep=time.sleep):
    """把一次 scheduler 能力观察落进会话事实缓存，返回更新后的记录。

    证据只进不退（见模块 docstring「证据只进不退」节）：None 不覆盖、
    能力值不回退不翻转、origin 单向升级、automation_id 追加去重；
    全部观察均无新证据 → 零写幂等（updated_at 不空转），返回既有
    记录 + 记录 dict 增标 "idempotent": True。有新证据 → 单次原子写
    （tmp + os.replace + PermissionError 有界重试）+ updated_at 刷新
    （observed_at 显式传入优先，须可解析 ISO8601）+ 容量闸淘汰。

    参数校验（先于任何 I/O，中文 ValueError）：
      - session_id 必须是非空 str（载荷缺 session_id 的调用方应跳过
        落账——会话事实按 session_id 键控，无键不写）；
      - origin ∈ FACT_ORIGINS（interactive / scheduled_task），None =
        未观察；
      - create / update / pause / delete ∈ FACT_CAPABILITY_VALUES
        （allowed / forbidden），None = 未观察；
      - automation_id 非 None 时须是非空 str；
      - observed_at 非 None 时须可解析 ISO8601。
    返回恰含 FACT_FIELDS 七键的记录 dict（幂等命中另加 "idempotent"）。
    """
    if not isinstance(session_id, str) or session_id == "":
        raise ValueError(
            "record_observation：session_id 必须是非空字符串，得到 %r"
            % (session_id,))
    if origin is not None and origin not in FACT_ORIGINS:
        raise ValueError(
            "record_observation：origin %r 不在合法取值内（%s）"
            % (origin, ", ".join(FACT_ORIGINS)))
    for name, value in (("create", create), ("update", update),
                        ("pause", pause), ("delete", delete)):
        if value is not None and value not in FACT_CAPABILITY_VALUES:
            raise ValueError(
                "record_observation：%s %r 不在合法取值内（%s）"
                % (name, value, ", ".join(FACT_CAPABILITY_VALUES)))
    if automation_id is not None \
            and (not isinstance(automation_id, str) or automation_id == ""):
        raise ValueError(
            "record_observation：automation_id 必须是 None 或非空字符串，"
            "得到 %r" % (automation_id,))
    if observed_at is not None and _parse_iso_utc(observed_at) is None:
        raise ValueError(
            "record_observation：observed_at 必须是 None 或可解析的 "
            "ISO8601 字符串，得到 %r" % (observed_at,))

    data = read_session_facts(repo_root)
    if data is None:
        data = {"schema_version": FACT_SCHEMA_VERSION, "sessions": {}}
    sessions = data["sessions"]
    existing = sessions.get(session_id)
    record, changed = _merge_record(
        existing, origin=origin, create=create, update=update, pause=pause,
        delete=delete, automation_id=automation_id)
    if not changed:
        result = dict(existing) if isinstance(existing, dict) \
            else dict(record)
        if isinstance(existing, dict):
            result["idempotent"] = True
        return result
    record["updated_at"] = observed_at if observed_at is not None \
        else _utc_now_iso()
    sessions[session_id] = record
    _prune_oldest(sessions)
    _write_session_facts(repo_root, data, retry_attempts=retry_attempts,
                         retry_interval=retry_interval, sleep=sleep)
    return record
