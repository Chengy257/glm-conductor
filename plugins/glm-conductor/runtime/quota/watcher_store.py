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
      - 单实例锁（§6 冻结不变式：**一个 provider identity 同时最多一
        个 active watcher process**；v2.2.1 WU-221-A2 起实现机制更替
        为锁所有权与观察状态分离）：互斥凭据是独立锁文件
        <repo_root>/.glm-conductor/quota/watcher.lock——os.open
        O_CREAT|O_EXCL 机械原子创建（同一时刻最多一个进程创建成功，
        旧「读→判→写 watcher.json」的 TOCTOU 窗口不复存在），锁内容
        JSON {"pid", "generation", "provider_identity_hash",
        "created_at"}。watcher.json 降级为**纯观察/状态面**（§5 冻结：
        字段集不减），其 pid/heartbeat 是锁仲裁的 advisory 证据而非
        锁本体。FRESH = 持锁 pid 存活 且（记录 pid 与锁匹配且 heartbeat
        可解析时）heartbeat 未超阈值；heartbeat 证据不可得（无记录 /
        pid 不匹配 / 不可解析）→ 锁 created_at 兜底（距今 ≤
        lock_stale_seconds 视同持有中——覆盖「锁刚创建、观察记录尚未
        落盘」的启动窗口）。锁存在但 FRESH → 确定型冲突（零写盘）；
        STALE（pid 死 / heartbeat 过期 / 内容损坏超 mtime 宽限）→
        claim-marker 仲裁接管（纪律与 durable_io._takeover_stale_lock
        同形：O_EXCL 认领标记、settle 后字典序最小者胜、复核锁指纹未
        换手、generation+1、除陈旧锁本体与自己的认领标记外绝不删任何
        文件）；无锁 → O_EXCL 直取。**刻意不使用 durable_io.
        exclusive_lock**：其 created_at 龄期 stale 语义会把合法长运行
        watcher（锁 created_at 早于 300 秒但 heartbeat 持续刷新）逐出，
        watcher 锁的新鲜性必须锚定 heartbeat 证据；
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
活持有者」。v2.2.1 WU-221-A2 起，锁的释放在**持有者退出路径**：
watcher 进程退出时按释放身份纪律删 watcher.lock（release_watcher_lock：
仅当锁内容仍是自己的 pid+generation——绝不误删后继接管者的锁）；
非持有方的 stop 请求（CLI）仍只置观察旗标，由持有者自行退出并释放锁。

纪律（本模块的硬边界）：
    - 本模块只写 watcher.json 与 watcher.lock 自身，绝不写任务 state /
      journal / quota-cache（task 面接线归 C5+）；
    - watcher.lock 全程有界（v2.2.1 WU-221-A2）：新鲜锁冲突是 bounded
      single check（判定即返回，无等待循环）；陈旧锁接管走 claim-marker
      仲裁的确定型落败；重试次数 / 间隔 / TTL 全为模块常量（可参数覆
      盖、sleep 可注入实现测试零真实等待）；
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
    仅 Python 3 标准库（json / os / time / ctypes / datetime / uuid）+
    runtime（durable_io，v2.2.1 WU-221-A2 起）+ runtime.quota.
    scheduler；quota/* 包纪律：不 import runtime.state /
    runtime.task_manager。

来源：
    docs/GLM-Conductor-v2.2-Quota-Control-Plane-Architecture-Correction-
    and-Agent-Implementation-Plan.md §6（Watcher 运行模式 / 状态锁字段）
    / §6.1（第一阶段范围）/ §6.2（P0-WATCH-00 五项 Windows 前置硬门）
    / §11（状态模型修正，schema_version / next_poll_at 形状参照）
    + 修正计划工作单元 wu-22-C3
    + v2.2.1 hardening 工作单元 WU-221-A2（锁所有权与观察状态分离）。
"""

import ctypes
import json
import os
import time
import uuid

try:  # Windows CRT 文本模式换行翻译防护（POSIX 无此常量）
    _O_BINARY = os.O_BINARY
except AttributeError:  # pragma: no cover - POSIX
    _O_BINARY = 0

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
# v2.2.1 WU-221-A2：互斥凭据锁文件（O_CREAT|O_EXCL 机械原子）——
# 锁所有权与观察状态分离，watcher.json 降级为纯观察面
WATCHER_LOCK_FILE_NAME = "watcher.lock"
# 认领标记形态：<watcher.lock 路径> + 此标记 + uuid4hex（接管仲裁用）
LOCK_CLAIM_MARKER = ".claim."

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

# —— watcher.lock 仲裁常量（v2.2.1 WU-221-A2；数值与 durable_io 独立
#    声明——watcher 锁的新鲜性锚定 heartbeat 证据，非 created_at 龄期） ——

# heartbeat 证据不可得时的兜底新鲜窗口（秒）：锁 created_at 距参考时刻
# ≤ 此值视同持有中（覆盖「锁刚创建、观察记录尚未落盘」的启动窗口）
DEFAULT_LOCK_STALE_SECONDS = 300.0
# 认领标记参与仲裁的龄期上限（孤儿标记只忽略、绝不代删）
LOCK_CLAIM_TTL_SECONDS = 60.0
# 认领 settle 轮询小步（让迟到的认领标记落盘后再比字典序）
LOCK_SETTLE_POLL_SECONDS = 0.05
# Windows delete-pending / 接管重取 / 外循环「锁刚消失」的有界重试次数
# 与小步间隔（sleep 可注入 → 测试零真实等待）
LOCK_TAKEOVER_RETRY_ATTEMPTS = 10
LOCK_TAKEOVER_RETRY_INTERVAL_SECONDS = 0.01
# 写一半残锁的龄期闸：mtime 未超此值视同持有中（崩溃遗留才可接管）；
# mtime 是真实时刻量，此闸用真实墙钟比对（不随注入时钟失真）
LOCK_CORRUPT_GRACE_SECONDS = 5.0


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


def watcher_lock_path(repo_root) -> str:
    """单实例互斥锁文件路径 <repo_root>/.glm-conductor/quota/
    watcher.lock（v2.2.1 WU-221-A2：锁所有权与观察状态分离——互斥
    凭据不再依附 watcher.json）。"""
    return os.path.join(str(repo_root), *_WATCHER_DIR_PARTS,
                        WATCHER_LOCK_FILE_NAME)


# —— watcher.lock 原语（v2.2.1 WU-221-A2；纪律照 durable_io 锁原语
#    同形自足实现，阈值按 watcher 语义独立——刻意不经
#    durable_io.exclusive_lock：其 created_at 龄期 stale 语义会把合法
#    长运行 watcher 逐出） ——

def _open_excl(path):
    """O_CREAT|O_EXCL 独占创建打开 → fd；已存在 → None；Windows
    delete-pending 瞬态拒绝（PermissionError）→ 一并按「此刻未获取」
    返回 None（调用方在有界判定循环里重评）；其余 OSError 上抛。"""
    try:
        return os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _O_BINARY,
                       0o600)
    except (FileExistsError, PermissionError):
        return None


def _write_lock_content(fd, pid, generation, provider_identity_hash,
                        created_at_iso):
    """向已 O_EXCL 打开的锁 fd 写锁内容 JSON 并关闭（写失败由调用方删
    锁上抛——绝不留空锁）。内容键冻结四键：pid / generation /
    provider_identity_hash / created_at。"""
    payload = json.dumps(
        {"pid": pid, "generation": generation,
         "provider_identity_hash": provider_identity_hash,
         "created_at": created_at_iso},
        ensure_ascii=False, sort_keys=True)
    with os.fdopen(fd, "wb") as handle:
        handle.write(payload.encode("utf-8"))
        handle.flush()


def _read_lock_file(path):
    """读锁文件 → ("ok", dict) | ("missing", None) | ("corrupt", None)。"""
    try:
        with open(path, "rb") as handle:
            raw = handle.read(65536)
    except FileNotFoundError:
        return "missing", None
    except OSError:
        return "corrupt", None
    try:
        state = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return "corrupt", None
    return ("ok", state) if isinstance(state, dict) else ("corrupt", None)


def _lock_fingerprint(state):
    """锁内容变更指纹（接管复核用：created_at + generation + pid +
    provider_identity_hash——四元组一致才算同一把陈旧锁）。"""
    if not isinstance(state, dict):
        return None
    return (state.get("created_at"), state.get("generation"),
            state.get("pid"), state.get("provider_identity_hash"))


def _file_age_seconds(path):
    """文件 mtime 距真实当前时刻的秒数；不可得 → None（mtime 是真实
    时刻量，用真实墙钟比对，不随注入时钟失真）。"""
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    return time.time() - mtime


def _unlink_quiet(path):
    """尽力删除；不存在 / 竞态消失 → 静默（清理路径绝不遮蔽主流程）。"""
    try:
        os.unlink(path)
    except OSError:
        pass


def _unlink_retry(path, *, attempts=LOCK_TAKEOVER_RETRY_ATTEMPTS,
                  interval=LOCK_TAKEOVER_RETRY_INTERVAL_SECONDS,
                  sleep=time.sleep):
    """尽力删除；Windows 并发读句柄（无 FILE_SHARE_DELETE）的瞬态拒绝
    → 有界小步重试后仍失败 → False；FileNotFoundError 视为成功。"""
    for attempt in range(max(1, int(attempts))):
        try:
            os.unlink(path)
            return True
        except FileNotFoundError:
            return True
        except OSError:
            if attempt + 1 < max(1, int(attempts)):
                sleep(float(interval))
    return False


def _inspect_lock(kind, state, lock_path, repo_root, *,
                  heartbeat_stale_seconds, lock_stale_seconds, now):
    """锁现状评估 → {"held": bool, ...冲突证据键...}（v2.2.1 WU-221-A2）。

    held 判定（watcher.json 的 pid/heartbeat 是 advisory 证据）：
      - corrupt（内容写一半 / 损坏）：mtime 龄期未超 LOCK_CORRUPT_GRACE_
        SECONDS → 视同持有中（持有者 O_EXCL 后正写内容的微秒级窗口）；
        超龄 → 崩溃遗留（可接管）；mtime 探不到 → 一律按持有中（瞬态
        误判绝不送进接管仲裁）。
      - ok：持锁 pid 死 → 不持有（fail-closed on lock：宁可放行接管，
        不无限拒绝第二进程）；watcher.json 记录 pid 与锁匹配且 heartbeat
        可解析 → heartbeat 龄期 ≤ 阈值即持有（含等号，与既有冻结语义
        一致）；证据不可得（无记录 / pid 不匹配 / 不可解析）→ 锁
        created_at 兜底：距今 ≤ lock_stale_seconds 即持有（覆盖「锁刚
        创建、观察记录尚未落盘」的启动窗口）；created_at 不可解析 →
        不持有（内容畸形按陈旧处理，绝不永久卡死）。
    """
    evidence = {"pid": None, "heartbeat_at": None,
                "heartbeat_age_seconds": None,
                "provider_identity_hash": None,
                "created_at": None, "lock_generation": None}
    if kind == "corrupt":
        age = _file_age_seconds(lock_path)
        evidence["held"] = True if age is None \
            else age <= float(LOCK_CORRUPT_GRACE_SECONDS)
        return evidence
    if kind != "ok":
        evidence["held"] = False
        return evidence
    holder_pid = state.get("pid")
    evidence["pid"] = holder_pid
    evidence["provider_identity_hash"] = state.get("provider_identity_hash")
    evidence["created_at"] = state.get("created_at")
    evidence["lock_generation"] = state.get("generation")
    if not _pid_alive(holder_pid):
        evidence["held"] = False
        return evidence
    record = read_watcher_state(repo_root)
    if isinstance(record, dict) and record.get("pid") == holder_pid:
        age = heartbeat_age_seconds(record, now=now)
        if age is not None:
            evidence["heartbeat_at"] = record.get("heartbeat_at")
            evidence["heartbeat_age_seconds"] = age
            evidence["held"] = age <= float(heartbeat_stale_seconds)
            return evidence
    created = _parse_iso_utc(state.get("created_at"))
    evidence["held"] = (created is not None
                        and (now - created).total_seconds()
                        <= float(lock_stale_seconds))
    return evidence


def _recheck_stale_lock(lock_path, stale_fingerprint, repo_root, *,
                        heartbeat_stale_seconds, lock_stale_seconds, now):
    """认领建立后的锁复核 → "gone"（已消失，调用方回 O_EXCL 直取）|
    "busy"（被新鲜持有者占用或内容已换手——他人已接管）| "stale"（仍
    是原陈旧锁，可继续接管）。"""
    kind, current = _read_lock_file(lock_path)
    if kind == "missing":
        return "gone"
    if _inspect_lock(kind, current, lock_path, repo_root,
                     heartbeat_stale_seconds=heartbeat_stale_seconds,
                     lock_stale_seconds=lock_stale_seconds,
                     now=now)["held"]:
        return "busy"
    if kind == "ok" and _lock_fingerprint(current) != stale_fingerprint:
        return "busy"
    return "stale"


def _claim_is_winner(claim_path, lock_path, *, claim_ttl_seconds):
    """认领仲裁：本认领标记是否为全部存活认领标记中字典序最小者。存活
    = 龄期 ≤ claim_ttl_seconds（孤儿标记只忽略、绝不代删）。"""
    directory = os.path.dirname(lock_path) or "."
    prefix = os.path.basename(lock_path) + LOCK_CLAIM_MARKER
    mine = os.path.basename(claim_path)
    try:
        names = os.listdir(directory)
    except OSError:
        return True
    for name in names:
        if name == mine or not name.startswith(prefix):
            continue
        rival = os.path.join(directory, name)
        try:
            if not os.path.isfile(rival):
                continue
            if time.time() - os.path.getmtime(rival) > float(
                    claim_ttl_seconds):
                continue
        except OSError:
            continue
        if name < mine:
            return False
    return True


def _takeover_stale_watcher_lock(lock_path, repo_root, *, stale_state,
                                 provider_identity_hash, pid, now, now_iso,
                                 heartbeat_stale_seconds, lock_stale_seconds,
                                 claim_ttl_seconds, settle_poll_seconds,
                                 sleep):
    """陈旧锁接管（claim-marker 仲裁，v2.2.1 WU-221-A2；纪律与
    durable_io._takeover_stale_lock 同形，阈值按 watcher 语义独立）。

    返回 ("acquired", generation) | ("conflict", None) | ("gone", None)：
      - gone：锁刚被持有者正常释放（调用方回 O_EXCL 直取，外循环有界）；
      - conflict：仲裁确定型落败（存在更小认领标记 / 复核见新鲜或换手
        锁 / 接管窗口被第三者抢先）——除陈旧锁本体与自己的认领标记外
        绝不删任何文件；
      - acquired：已删陈旧锁并有界 O_EXCL 重取、写入锁内容，generation
        = 陈旧锁 generation + 1（不可解析按 1 兜底再 +1，绝不放大垃圾
        值）。
    """
    stale_fingerprint = _lock_fingerprint(stale_state)
    prior_generation = 1
    if isinstance(stale_state, dict):
        try:
            prior_generation = max(1, int(stale_state.get("generation")))
        except (TypeError, ValueError):
            prior_generation = 1
    claim_path = "%s%s%s" % (lock_path, LOCK_CLAIM_MARKER, uuid.uuid4().hex)
    fd = _open_excl(claim_path)
    if fd is None:  # uuid4 唯一名冲突实践不可能；保守确定型落败
        return "conflict", None
    try:
        os.close(fd)  # 标记内容不承载语义，存在性即仲裁凭证
        verdict = _recheck_stale_lock(
            lock_path, stale_fingerprint, repo_root,
            heartbeat_stale_seconds=heartbeat_stale_seconds,
            lock_stale_seconds=lock_stale_seconds, now=now)
        if verdict == "gone":
            return "gone", None
        if verdict == "busy":
            return "conflict", None
        sleep(max(0.0, float(settle_poll_seconds)))  # settle：迟到认领落盘
        if not _claim_is_winner(claim_path, lock_path,
                                claim_ttl_seconds=claim_ttl_seconds):
            return "conflict", None
        verdict = _recheck_stale_lock(
            lock_path, stale_fingerprint, repo_root,
            heartbeat_stale_seconds=heartbeat_stale_seconds,
            lock_stale_seconds=lock_stale_seconds, now=now)
        if verdict == "gone":
            return "gone", None
        if verdict == "busy":
            return "conflict", None
        _unlink_retry(lock_path, sleep=sleep)  # 认领赢家独有权利：删陈旧锁
        next_fd = None
        for _attempt in range(max(1, LOCK_TAKEOVER_RETRY_ATTEMPTS)):
            # 陈旧锁刚被删、并发读句柄尚在 → Windows delete-pending 瞬态
            # 拒绝，有界小步重取；被第三者真抢先（O_EXCL 实存在）同理
            # 耗尽 → 确定型落败
            next_fd = _open_excl(lock_path)
            if next_fd is not None:
                break
            sleep(LOCK_TAKEOVER_RETRY_INTERVAL_SECONDS)
        if next_fd is None:
            return "conflict", None
        try:
            _write_lock_content(next_fd, pid, prior_generation + 1,
                                provider_identity_hash, now_iso)
        except BaseException:
            _unlink_quiet(lock_path)
            raise
        return "acquired", prior_generation + 1
    finally:
        _unlink_quiet(claim_path)


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


# —— 单实例锁（v2.2.1 WU-221-A2：锁本体 = watcher.lock，O_EXCL 机械
#    原子；watcher.json = 纯观察/状态面） ——

def acquire_watcher_lock(repo_root, *, provider_identity_hash, mode,
                         pid=None, now=None,
                         heartbeat_stale_seconds=
                         DEFAULT_HEARTBEAT_STALE_SECONDS,
                         lock_stale_seconds=DEFAULT_LOCK_STALE_SECONDS,
                         claim_ttl_seconds=LOCK_CLAIM_TTL_SECONDS,
                         settle_poll_seconds=LOCK_SETTLE_POLL_SECONDS,
                         retry_attempts=PERMISSION_RETRY_ATTEMPTS,
                         retry_interval=PERMISSION_RETRY_INTERVAL_SECONDS,
                         sleep=time.sleep):
    """单实例锁 acquire（§6 冻结不变式：一个 provider identity 同时最
    多一个 active watcher process；v2.2.1 WU-221-A2 起互斥凭据 =
    watcher.lock 的 O_CREAT|O_EXCL 机械原子创建——同一时刻最多一个进
    程创建成功，读-判-写的 TOCTOU 窗口不复存在；watcher.json 是纯观察
    面，其 pid/heartbeat 作为锁仲裁的 advisory 证据）。

    参数：
      - provider_identity_hash：非空 str（watcher.py 用来源+凭证指纹
        sha256 截断生成；本层只做词汇闸不复制派生逻辑）；
      - mode：WATCHER_MODES 之一；
      - pid：持锁进程 pid（缺省 os.getpid()）；
      - now：参考时刻注入（None → 当前 UTC；heartbeat 与锁 created_at
        的比对基准）；
      - heartbeat_stale_seconds：heartbeat 新鲜阈值（默认 180 秒）；
      - lock_stale_seconds：heartbeat 证据不可得时锁 created_at 的兜底
        新鲜窗口（默认 300 秒）；
      - claim_ttl_seconds / settle_poll_seconds：认领仲裁参数；
      - retry_* / sleep：透传 write_watcher_state（bounded retry）；
        sleep 同时注入接管路径全部小步等待（测试零真实等待）。

    判定（按序；全程有界、无无限等待）：
      1. watcher.lock 不存在 → O_EXCL 直取：锁 generation = 观察记录
         generation + 1（无记录 / 不可解析 → 1；§6 代次单调性——
         graceful stop 删锁后的重启同样只前进不回退），写锁内容 JSON
         {"pid", "generation", "provider_identity_hash", "created_at"}
         + 原子写观察记录（generation 与锁同步）；
      2. 锁存在且 FRESH（判定细则见 _inspect_lock）→ 确定型冲突：
         bounded single check，判定即返回，**零写盘**、零等待循环；
      3. 锁 STALE（pid 死 / heartbeat 过期 / 内容损坏超 mtime 宽限）→
         claim-marker 仲裁接管（_takeover_stale_watcher_lock）：
         generation = 陈旧锁 generation + 1，takeover=True；观察记录
         照写（保留现存 last_observation 作观察连续性参考，
         started_at = 本次时刻——接管 = 新进程纪元）。

    观察记录写失败（PermissionError 有界重试耗尽 → WatcherStoreError）
    时锁已 O_EXCL 在手：异常上抛调用方，锁由 created_at 兜底窗口
    （lock_stale_seconds）保证之后可被接管——绝不构成永久死锁。

    返回（既有键冻结；v221-a2 新增顶层 lock_generation 与冲突键
    created_at / lock_generation）：
      {"acquired": bool, "takeover": bool, "generation": int|None,
       "record": dict|None,
       "conflict": None | {"pid", "heartbeat_at",
                           "heartbeat_age_seconds",
                           "provider_identity_hash",
                           "created_at", "lock_generation"},
       "lock_generation": int|None}
    """
    if not isinstance(provider_identity_hash, str) \
            or provider_identity_hash == "":
        raise ValueError(
            "acquire_watcher_lock：provider_identity_hash 必须是非空 str")
    if mode not in WATCHER_MODES:
        raise ValueError(
            "acquire_watcher_lock：mode %r 不在合法取值内（%s）"
            % (mode, ", ".join(WATCHER_MODES)))
    if not _is_number(heartbeat_stale_seconds) \
            or heartbeat_stale_seconds < 0:
        raise ValueError(
            "acquire_watcher_lock：heartbeat_stale_seconds 必须是 >= 0 "
            "的数值，得到 %r" % (heartbeat_stale_seconds,))
    for _name, _value in (("lock_stale_seconds", lock_stale_seconds),
                          ("claim_ttl_seconds", claim_ttl_seconds),
                          ("settle_poll_seconds", settle_poll_seconds)):
        if not _is_number(_value) or _value < 0:
            raise ValueError(
                "acquire_watcher_lock：%s 必须 >= 0 的数值，得到 %r"
                % (_name, _value))
    if pid is None:
        pid = os.getpid()
    moment = _normalize_now(now)
    now_iso = _format_iso_z(moment)
    lock_path = watcher_lock_path(repo_root)
    directory = os.path.dirname(lock_path)
    if directory:
        os.makedirs(directory, exist_ok=True)

    for _attempt in range(max(1, LOCK_TAKEOVER_RETRY_ATTEMPTS)):
        fd = _open_excl(lock_path)
        if fd is not None:
            # 机械原子胜出：此刻起全仓库只有本调用者持锁
            existing = read_watcher_state(repo_root)
            generation = 1
            if isinstance(existing, dict):
                try:
                    generation = max(1, int(existing.get("generation"))) + 1
                except (TypeError, ValueError):
                    generation = 1
            try:
                _write_lock_content(fd, pid, generation,
                                    provider_identity_hash, now_iso)
            except BaseException:  # 内容写失败：不能留下空锁
                _unlink_quiet(lock_path)
                raise
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
                                     if isinstance(existing, dict)
                                     else None),
            }
            write_watcher_state(repo_root, record,
                                retry_attempts=retry_attempts,
                                retry_interval=retry_interval, sleep=sleep)
            return {"acquired": True, "takeover": False,
                    "generation": generation, "record": record,
                    "conflict": None, "lock_generation": generation}
        kind, state = _read_lock_file(lock_path)
        if kind == "missing":
            # 锁在 O_EXCL 失败与重读之间刚被释放/换手：小步后重取
            # （sleep 可注入；外循环整体有界，绝不空转到底）
            sleep(LOCK_TAKEOVER_RETRY_INTERVAL_SECONDS)
            continue
        inspection = _inspect_lock(
            kind, state, lock_path, repo_root,
            heartbeat_stale_seconds=heartbeat_stale_seconds,
            lock_stale_seconds=lock_stale_seconds, now=moment)
        if inspection.pop("held"):
            # FRESH：确定型冲突——零写盘，bounded single check
            return {"acquired": False, "takeover": False,
                    "generation": None, "record": None,
                    "conflict": inspection, "lock_generation": None}
        status, lock_generation = _takeover_stale_watcher_lock(
            lock_path, repo_root, stale_state=state,
            provider_identity_hash=provider_identity_hash, pid=pid,
            now=moment, now_iso=now_iso,
            heartbeat_stale_seconds=heartbeat_stale_seconds,
            lock_stale_seconds=lock_stale_seconds,
            claim_ttl_seconds=claim_ttl_seconds,
            settle_poll_seconds=settle_poll_seconds, sleep=sleep)
        if status == "gone":
            continue  # 锁刚被持有者正常释放：回 O_EXCL 直取（有界）
        if status == "conflict":
            # 仲裁确定型落败：以评估时的陈旧锁证据返回冲突（零写盘）
            return {"acquired": False, "takeover": False,
                    "generation": None, "record": None,
                    "conflict": inspection, "lock_generation": None}
        # acquired：接管成功——写观察记录（generation 与新锁同步）
        existing = read_watcher_state(repo_root)
        record = {
            "schema_version": 1,
            "provider_identity_hash": provider_identity_hash,
            "mode": mode,
            "pid": pid,
            "started_at": now_iso,
            "heartbeat_at": now_iso,
            "generation": lock_generation,
            "stop_requested": False,
            "last_observation": (existing.get("last_observation")
                                 if isinstance(existing, dict) else None),
        }
        write_watcher_state(repo_root, record,
                            retry_attempts=retry_attempts,
                            retry_interval=retry_interval, sleep=sleep)
        return {"acquired": True, "takeover": True,
                "generation": lock_generation, "record": record,
                "conflict": None, "lock_generation": lock_generation}
    # 有界外循环耗尽（极端竞争下反复错过可用间隙）：确定型冲突，
    # 绝不无限等待——证据不可得 → None 不虚构
    return {"acquired": False, "takeover": False, "generation": None,
            "record": None,
            "conflict": {"pid": None, "heartbeat_at": None,
                         "heartbeat_age_seconds": None,
                         "provider_identity_hash": None,
                         "created_at": None, "lock_generation": None},
            "lock_generation": None}


def release_watcher_lock(repo_root, *, pid, generation,
                         attempts=LOCK_TAKEOVER_RETRY_ATTEMPTS,
                         interval=LOCK_TAKEOVER_RETRY_INTERVAL_SECONDS,
                         sleep=time.sleep):
    """按释放身份纪律删除 watcher.lock（v2.2.1 WU-221-A2）：仅当锁内容
    可读且 pid+generation 仍与调用者声明的一致才删——绝不误删后继接管
    者的锁（内容不可读 / 已易主 → 保留并返回 False）。删除走有界重试
    （Windows 并发读句柄的瞬态拒绝若静默放过，会留下幽灵锁）。持有者
    退出路径（watcher.py 的 graceful stop / max_ticks / 状态被外部移除
    等一切退出）调用；非持有方的 stop 请求（CLI）不调用本函数——只置
    观察旗标，由持有者自行退出并释放。返回是否实际删除。"""
    lock_path = watcher_lock_path(repo_root)
    kind, state = _read_lock_file(lock_path)
    if kind != "ok" or state.get("pid") != pid \
            or state.get("generation") != generation:
        return False
    return _unlink_retry(lock_path, attempts=attempts,
                         interval=interval, sleep=sleep)


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
