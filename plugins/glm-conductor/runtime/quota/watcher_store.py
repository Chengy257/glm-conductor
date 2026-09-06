#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GLM Conductor v2.2 quota watcher durable 状态存取层（修正计划 C3，
§6 / §6.1 / §6.2，wu-22-C3）。

职责（修正计划 §5 图：watcher_store.py = NEW: durable state + atomic
write + lock）：
    独立长运行 Watcher 进程（runtime/quota/watcher.py）与只读观察方
    （CLI quota-watcher status / stop）之间的唯一 durable 状态面：
      - 状态文件：<repo_root>/.glm-conductor/quota/watcher.json
        （沿用仓库 .glm-conductor 布局惯例，位于账本目录下、
        resolver 缓存 quota-cache.json 的 quota/ 子目录同层）；
      - 单实例锁（§6 冻结）：**锁即状态文件本身**——acquire 时若现存
        记录 pid 活着且 heartbeat 新鲜 → 拒绝（返回冲突信息）；pid 已
        死或 heartbeat 过期 → stale lock recovery（接管，generation+1，
        并在返回值记录 takeover 事实）；无记录 → 全新 acquire
        （generation=1）。第一阶段冻结语义：**一个 provider identity
        同时最多一个 active watcher process**；
      - 原子写：v2.2.1 WU-221-A2 起委托 runtime.durable_io.
        atomic_write_json（唯一同目录临时名 + os.replace——多进程
        写者绝不在固定 tmp 名相撞），读方永不见撕裂文件
        （R3：崩溃时读者要么看不到文件要么看到完整记录）；
      - PermissionError bounded handling（§6.2 P0-WATCH-00 门 4，
        Windows AV / 目录锁是本机已知现象）：写 tmp 或 os.replace 遇
        PermissionError → 有界重试（默认 3 次、0.2 秒间隔，常量可配），
        仍失败 → 抛出明确的 WatcherStoreError——绝不无限重试、绝不
        静默吞掉；
      - 读方容错：读到 JSON decode 错误（极端竞争窗口）→ 有界重读
        （同参数）后仍失败 → 返回 None 不抛（观察方 fail-open）。

状态记录字段（§6 冻结锁字段 + 观察字段；键名逐字）：

    {
      "schema_version": 1,
      "provider_identity_hash": "<sha256 前 16 位（来源+凭证指纹，
                                 绝不含凭证材料 §37）>",
      "mode": "ACTIVE" | "PASSIVE",
      "pid": <int 或 None（None = 已停止 / 无存活持有者）>,
      "started_at": "<ISO8601 Z>",
      "heartbeat_at": "<ISO8601 Z>",
      "generation": <int，从 1 起；每次 takeover +1>,
      "stop_requested": <bool>,
      "last_observation": {...}   # watcher.py 写入的最近一次观察
    }

graceful stop 的表达（冻结字段集内表达，不新增 status 字段）：stopped
= pid 置 None + stop_requested 保持 True——读方据 pid is None 判「无存
活持有者」，下一次 acquire 走接管路径（generation+1）。

纪律（本模块的硬边界）：
    - 本模块只写 watcher.json 自身，绝不写任务 state / journal /
      quota-cache（task 面接线归 C5+）；
    - pid 存活判定零外部依赖：Windows 用 ctypes kernel32
      OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION) +
      GetExitCodeProcess 判 STILL_ACTIVE（P0-WATCH-00 工程约束）；非
      Windows 回退 os.kill(pid, 0)；**不引入 psutil**；
    - 时间助手复用 runtime.quota.scheduler 的 _parse_iso_utc /
      _format_iso_z / _normalize_now（跨模块私有 import 是 quota 包
      既定惯例），不复制解析；
    - PermissionError 的重试间隔经参数注入（测试零真实等待）；
    - 无 daemon、无轮询循环：循环归 watcher.py，本模块只提供原子
      原语（§6.1 skeleton-first）。

依赖：
    仅 Python 3 标准库（json / os / time / ctypes / datetime）+
    runtime（durable_io，v2.2.1 WU-221-A2 起）+ runtime.quota.
    scheduler；quota/* 包纪律：不 import runtime.state /
    runtime.task_manager。

来源：
    docs/GLM-Conductor-v2.2-Quota-Control-Plane-Architecture-Correction-
    and-Agent-Implementation-Plan.md §6（Watcher 运行模式 / 状态锁字段）
    / §6.1（第一阶段范围）/ §6.2（P0-WATCH-00 五项 Windows 前置硬门）
    / §11（状态模型修正，schema_version / next_poll_at 形状参照）
    + 修正计划工作单元 wu-22-C3。
"""

import ctypes
import json
import os
import time

from runtime import durable_io
from runtime.quota.scheduler import (
    _format_iso_z,
    _is_number,
    _normalize_now,
    _parse_iso_utc,
)

# —— 词汇表常量 ——

# §6 冻结：watcher 运行模式二值（大写，与修正计划正文逐字一致）
WATCHER_MODES = ("ACTIVE", "PASSIVE")

# 状态文件相对布局（<repo_root>/ 下；沿用 .glm-conductor 账本目录惯例，
# quota/ 子目录与 resolver 缓存同层但不共用文件）
_WATCHER_DIR_PARTS = (".glm-conductor", "quota")
WATCHER_STATE_FILE_NAME = "watcher.json"
WATCHER_LOG_FILE_NAME = "watcher.log"

# §6 冻结锁字段 + 观察字段（键名逐字；extra 键允许——next_poll_at /
# schema_version 即镜像 §11 状态模型的补充键，锁语义不消费它们）
WATCHER_STATE_FIELDS = (
    "provider_identity_hash", "mode", "pid", "started_at",
    "heartbeat_at", "generation", "stop_requested", "last_observation")

# heartbeat 新鲜阈值（秒，§6.2 规格默认 180）：acquire 判「现存持有者
# 是否可信」的第二半条件（pid 活着 且 heartbeat 新鲜 → 拒绝）；
# heartbeat 过期 → stale lock recovery 可接管
DEFAULT_HEARTBEAT_STALE_SECONDS = 180

# PermissionError bounded retry（§6.2 门 4；Windows AV / 目录锁下
# os.replace 偶发拒绝是本机已知现象）：默认 3 次、0.2 秒间隔——有界，
# 绝不无限重试
PERMISSION_RETRY_ATTEMPTS = 3
PERMISSION_RETRY_INTERVAL_SECONDS = 0.2

# 读方容错的有界重读次数（§6.2 门 3：极端竞争窗口下读到 decode 错误 →
# 同参数重读；仍失败 → None 不抛）
READ_RETRY_ATTEMPTS = 3


class WatcherStoreError(RuntimeError):
    """watcher.json 写入在 PermissionError 有界重试耗尽后仍失败
    （§6.2 门 4 的明确异常——绝不静默吞掉）；__cause__ 保留原始
    PermissionError 供排查。"""


# —— 路径与时间助手 ——

def watcher_state_path(repo_root) -> str:
    """watcher durable 状态文件路径 <repo_root>/.glm-conductor/quota/
    watcher.json。"""
    return os.path.join(str(repo_root), *_WATCHER_DIR_PARTS,
                        WATCHER_STATE_FILE_NAME)


def watcher_log_path(repo_root) -> str:
    """watcher 子进程日志路径 <repo_root>/.glm-conductor/quota/
    watcher.log（CLI start 分离派生时 stdout/stderr 落点）。"""
    return os.path.join(str(repo_root), *_WATCHER_DIR_PARTS,
                        WATCHER_LOG_FILE_NAME)


def heartbeat_age_seconds(record, *, now=None):
    """现存记录 heartbeat_at 距参考时刻的秒数（float）；heartbeat 缺失
    / 不可解析 / record 非 dict → None（调用方按「未知」处理，不虚构）。
    """
    if not isinstance(record, dict):
        return None
    moment = _normalize_now(now)
    heartbeat = _parse_iso_utc(record.get("heartbeat_at"))
    if heartbeat is None:
        return None
    return (moment - heartbeat).total_seconds()


# —— pid 存活判定（零外部依赖；P0-WATCH-00 工程约束） ——

def _pid_alive(pid):
    """pid 对应进程是否存活（仅用于锁仲裁，不做任何其他事）。

    Windows：ctypes kernel32 OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION)
    + GetExitCodeProcess 判 STILL_ACTIVE（259）——零外部依赖，不引入
    psutil；OpenProcess 失败（进程不存在 / 已退出 / 无权访问）→ 视为
    已死（fail-closed on lock：宁可放行接管，不无限拒绝第二进程）。
    非 Windows：os.kill(pid, 0)——ESRCH → 死；EPERM（存在但无权发信
    号）→ 活。
    """
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == "nt":
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False  # 不存在 / 已退出 / 无权访问 → 一律按已死
        try:
            exit_code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle,
                                               ctypes.byref(exit_code)):
                return False
            return exit_code.value == STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # 进程存在但无权发信号
    except OSError:
        return False
    return True


# —— 读方容错（门 3 的读侧半区） ——

def read_watcher_state(repo_root, *, attempts=READ_RETRY_ATTEMPTS):
    """读 watcher.json → dict 或 None（本函数绝不抛）。

    文件不存在 → None（无记录，非错误）；JSON decode / OSError → 有界
    重读（同参数，默认 READ_RETRY_ATTEMPTS 次、立即重试——极端竞争窗口
    是瞬态的），仍失败 → None（fail-open，观察方按「无记录」继续）；
    顶层非 dict → None（形状容错，与 resolver._load_cache 同口径）。
    """
    path = watcher_state_path(repo_root)
    for _attempt in range(max(1, int(attempts))):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            continue  # 瞬态竞争窗口：有界重读
        return data if isinstance(data, dict) else None
    return None


# —— 原子写（门 2 的写侧原语 + 门 4 的 bounded retry） ——

def write_watcher_state(repo_root, record, *,
                        retry_attempts=PERMISSION_RETRY_ATTEMPTS,
                        retry_interval=PERMISSION_RETRY_INTERVAL_SECONDS,
                        sleep=time.sleep):
    """原子写 watcher.json（v2.2.1 WU-221-A2 起委托共享原语
    runtime.durable_io.atomic_write_json：唯一同目录临时名 +
    os.replace——多进程写者（watcher 进程心跳 + 主会话 stop 路径）
    绝不在固定 <path>.tmp 相撞；落盘字节与既有手写实现逐字节一致；
    父目录缺失由原语自动创建），返回最终路径。

    参数：
      - record：dict（非 dict → ValueError——参数校验先于 I/O）；
      - retry_attempts / retry_interval：PermissionError 有界重试的
        次数与间隔秒数（默认常量 PERMISSION_RETRY_*；测试可注入 0 /
        0 实现零等待）；
      - sleep：重试间隔的休眠实现（缺省 time.sleep；测试注入 no-op）。

    PermissionError（Windows AV / 目录锁，本机已知现象）→ 有界重试
    （原语内按次重试、sleep 注入下发）；耗尽仍失败 → WatcherStoreError
    （__cause__ = 原始 PermissionError）——绝不无限重试、绝不静默吞。
    其余异常：原语清理临时文件后原样上抛（调用方自行兜底）。读方永
    不见撕裂文件（os.replace 原子性 + 失败路径临时清理）。
    """
    if not isinstance(record, dict):
        raise ValueError(
            "write_watcher_state：record 必须是 dict，得到 %r"
            % (type(record).__name__,))
    attempts = max(1, int(retry_attempts))
    if retry_interval < 0:
        raise ValueError(
            "write_watcher_state：retry_interval 必须 >= 0，得到 %r"
            % (retry_interval,))
    path = watcher_state_path(repo_root)
    try:
        durable_io.atomic_write_json(path, record,
                                     retry_attempts=retry_attempts,
                                     retry_interval=retry_interval,
                                     sleep=sleep)
    except PermissionError as last_permission_error:
        raise WatcherStoreError(
            "watcher.json 原子写在 PermissionError 有界重试 %d 次（间隔 %.2f "
            "秒）后仍失败（Windows AV / 目录锁？）：%s" % (
                attempts, float(retry_interval), last_permission_error)
        ) from last_permission_error
    return path


# —— 单实例锁（§6 冻结：锁即状态文件本身） ——

def acquire_watcher_lock(repo_root, *, provider_identity_hash, mode,
                         pid=None, now=None,
                         heartbeat_stale_seconds=
                         DEFAULT_HEARTBEAT_STALE_SECONDS,
                         retry_attempts=PERMISSION_RETRY_ATTEMPTS,
                         retry_interval=PERMISSION_RETRY_INTERVAL_SECONDS,
                         sleep=time.sleep):
    """单实例锁 acquire（§6 冻结：一个 provider identity 同时最多一个
    active watcher process；第一阶段实现为「锁即状态文件本身」）。

    参数：
      - provider_identity_hash：非空 str（watcher.py 用来源+凭证指纹
        sha256 截断生成；本层只做词汇闸不复制派生逻辑）；
      - mode：WATCHER_MODES 之一；
      - pid：持锁进程 pid（缺省 os.getpid()）；
      - now：参考时刻注入（None → 当前 UTC）；
      - heartbeat_stale_seconds：heartbeat 新鲜阈值（默认 180 秒）；
      - retry_* / sleep：透传 write_watcher_state（bounded retry）。

    判定（按序）：
      1. 无记录 → 全新 acquire：generation=1，写记录；
      2. 现存记录 pid 活着 且 heartbeat 新鲜（age ≤ 阈值）→ 拒绝：
         返回 acquired=False + conflict 信息（**不写盘**）；
      3. pid 已死 或 heartbeat 过期 → stale lock recovery：接管，
         generation = 现存+1，takeover=True，写记录（保留现存
         last_observation 作观察连续性参考）；started_at = 本次时刻
         （接管 = 新进程纪元）。

    返回（键冻结）：
      {"acquired": bool, "takeover": bool, "generation": int|None,
       "record": dict|None,
       "conflict": None | {"pid", "heartbeat_at",
                           "heartbeat_age_seconds",
                           "provider_identity_hash"}}
    """
    if not isinstance(provider_identity_hash, str) \
            or provider_identity_hash == "":
        raise ValueError(
            "acquire_watcher_lock：provider_identity_hash 必须是非空 str")
    if mode not in WATCHER_MODES:
        raise ValueError(
            "acquire_watcher_lock：mode %r 不在合法取值内（%s）"
            % (mode, ", ".join(WATCHER_MODES)))
    if pid is None:
        pid = os.getpid()
    moment = _normalize_now(now)
    if not _is_number(heartbeat_stale_seconds) \
            or heartbeat_stale_seconds < 0:
        raise ValueError(
            "acquire_watcher_lock：heartbeat_stale_seconds 必须是 >= 0 "
            "的数值，得到 %r" % (heartbeat_stale_seconds,))
    now_iso = _format_iso_z(moment)

    existing = read_watcher_state(repo_root)
    takeover = False
    generation = 1
    if existing is not None:
        holder_pid = existing.get("pid")
        age = heartbeat_age_seconds(existing, now=moment)
        fresh = (age is not None
                 and age <= float(heartbeat_stale_seconds))
        if _pid_alive(holder_pid) and fresh:
            return {"acquired": False, "takeover": False,
                    "generation": None, "record": None,
                    "conflict": {
                        "pid": holder_pid,
                        "heartbeat_at": existing.get("heartbeat_at"),
                        "heartbeat_age_seconds": age,
                        "provider_identity_hash":
                            existing.get("provider_identity_hash"),
                    }}
        # stale lock recovery：pid 已死（含 pid 缺失 / 形状非法）或
        # heartbeat 过期 → 接管，generation +1，takeover 事实入返回值
        takeover = True
        try:
            generation = int(existing.get("generation")) + 1
        except (TypeError, ValueError):
            generation = 1
    record = {
        "schema_version": 1,
        "provider_identity_hash": provider_identity_hash,
        "mode": mode,
        "pid": pid,
        "started_at": now_iso,
        "heartbeat_at": now_iso,
        "generation": generation,
        "stop_requested": False,
        "last_observation": (existing.get("last_observation")
                             if existing is not None else None),
    }
    write_watcher_state(repo_root, record, retry_attempts=retry_attempts,
                        retry_interval=retry_interval, sleep=sleep)
    return {"acquired": True, "takeover": takeover, "generation": generation,
            "record": record, "conflict": None}


def request_stop(repo_root, *,
                 retry_attempts=PERMISSION_RETRY_ATTEMPTS,
                 retry_interval=PERMISSION_RETRY_INTERVAL_SECONDS,
                 sleep=time.sleep):
    """置 stop_requested 旗标（原子写回）；单次操作，绝不轮询等待退出。

    无记录 → None（无可停止对象，幂等非错误）；有记录 → 写回
    stop_requested=True 并返回更新后的记录 dict。其余字段原样保留
    （是否已被 watcher 消费由 watcher 循环在下一 wake 判定）。
    """
    record = read_watcher_state(repo_root)
    if record is None:
        return None
    record["stop_requested"] = True
    write_watcher_state(repo_root, record, retry_attempts=retry_attempts,
                        retry_interval=retry_interval, sleep=sleep)
    return record
