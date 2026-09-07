#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GLM Conductor v2.2.1 共享 JSON 状态 durable I/O 原语层（v2.2.1 WU-221-A1）。

职责（v2.2 根因：前台主会话 + 常驻 quota watcher 等多个合法进程写共享
JSON 状态，既有写方各自使用固定 <path>.tmp 临时名——两个写方会在同一
临时路径相撞）：为下一工作单元（v221-a2 写方迁移）提供 stdlib-only 共
享原语。本单元零接线：无任何既有模块 import 本模块。冻结公共 API：

    atomic_write_json(path, payload)      每次调用唯一临时名 + os.replace 原子写
    atomic_read_json(path, default=None)  容错读（缺失 / 损坏 → default，绝不抛）
    atomic_update_json(path, updater)     独占锁保护下的读-改-写
    exclusive_lock(lock_path)             O_CREAT|O_EXCL 独占锁（上下文管理器）
    LockBusyError                         锁忙 / 冲突的确定型异常

纪律要点（细则见各函数 docstring）：
    - 原子写：临时名 <目标名>.durable-tmp.<pid>.<uuid4hex>.tmp（同目录、
      每次调用唯一，固定 .tmp 名绝不存在）；序列化先于任何 I/O；
      UTF-8、ensure_ascii=False、固定 \n 换行；flush 后尽力 fsync；
      os.replace；PermissionError（Windows AV / 目录锁瞬态）有界重试
      （默认 5 次 × 0.1 秒）耗尽原样上抛；写前顺带清理同前缀、龄期超
      TEMP_CLEANUP_MIN_AGE_SECONDS 的废弃临时（失败绝不传播）；
    - 独占锁：锁内容 JSON {"pid", "generation", "created_at", "owner"}
      （owner = uuid4 hex 持有者身份键——同进程多线程下 pid+generation
      不唯一，释放/接管比对以 pid+owner 为准）；generation 从 1 起、
      陈旧接管 +1；新鲜持有者在场按 poll_interval 轮询至
      timeout_seconds 耗尽 → LockBusyError；陈旧锁（pid 死或 created_at
      超 stale_seconds）经 claim-marker 仲裁接管：各竞争方 O_EXCL 创建
      唯一认领标记，仅字典序最小者（settle 一个 poll_interval 后复核）
      可删陈旧锁并立即重取，其余确定型落败、绝不删他人文件；释放仅当
      锁内容仍是自己 pid+owner，删除走有界重试（Windows 并发读句柄
      无 FILE_SHARE_DELETE；delete-pending 瞬态拒绝按「此刻未获取」回
      判定循环）；写一半残锁按 mtime 龄期判（未超 CORRUPT_LOCK_GRACE_
      SECONDS 视同写入中轮询；mtime 探不到一律轮询重评，瞬态误判绝不
      送进接管仲裁）；
    - pid 存活判定（仅用于锁仲裁）：POSIX os.kill(pid, 0)；Windows
      ctypes OpenProcess + GetExitCodeProcess 判 STILL_ACTIVE（与
      quota.watcher_store 同纪律，零外部依赖、不引入 psutil）；探测失
      败一律视为已死——锁面 fail-closed：宁可放行接管，不无限拒绝第二
      进程。

参数默认（关键字可覆盖）：stale_seconds=300.0、poll_interval=0.05、
timeout_seconds=10.0、写重试 5 次 × 0.1 秒、读重读 3 次、废弃临时/孤儿
认领龄期闸 60 秒。

依赖：仅 Python 3 标准库，Py3.8 兼容语法；不 import runtime 包内任何模
块（独立原语层——避免 v221-a2 接线后 state → durable_io → state 循环）。

来源：v2.2.1 hardening 工作单元 WU-221-A1；既有写方迁移归 v221-a2。
"""

import contextlib
import ctypes
import json
import os
import time
import uuid
from datetime import datetime, timezone

try:  # Windows CRT 文本模式换行翻译防护（POSIX 无此常量）
    _O_BINARY = os.O_BINARY
except AttributeError:  # pragma: no cover - POSIX
    _O_BINARY = 0

# —— 词汇表常量（本模块独立声明，与既有写方数值互不共享） ——

TEMP_MARKER = ".durable-tmp."      # 临时文件识别前缀（<目标名> + 此标记）
LOCK_SUFFIX = ".lock"              # 独占锁固定后缀（<target>.lock，非临时文件）
CLAIM_MARKER = ".claim."           # 认领标记形态：<lock_path> + 此标记 + uuid4hex
TEMP_CLEANUP_MIN_AGE_SECONDS = 60.0   # 废弃临时文件参与清理的最低龄期闸
CLAIM_MARKER_TTL_SECONDS = 60.0       # 认领标记参与仲裁的龄期上限（孤儿忽略）
DEFAULT_STALE_SECONDS = 300.0         # 锁陈旧阈值：created_at 超龄即视为可接管
DEFAULT_POLL_INTERVAL_SECONDS = 0.05  # 忙等 / 认领 settle 的轮询小步
DEFAULT_TIMEOUT_SECONDS = 10.0        # 新鲜锁忙等上界（耗尽 → LockBusyError）
WRITE_RETRY_ATTEMPTS = 5              # PermissionError 有界重试次数
WRITE_RETRY_INTERVAL_SECONDS = 0.1    # 写重试间隔秒数
READ_RETRY_ATTEMPTS = 3               # 读侧瞬态失败的有界重读次数
UNLINK_RETRY_ATTEMPTS = 10            # 锁文件删除与并发读句柄相撞的重试次数
UNLINK_RETRY_INTERVAL_SECONDS = 0.01  # 锁文件删除重试间隔秒数（窗口微秒级）
CORRUPT_LOCK_GRACE_SECONDS = 5.0      # 写一半残锁的龄期闸：未超龄按写入中轮询

# Windows OpenProcess 探针常量（与 quota.watcher_store 数值一致、独立声明）
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_STILL_ACTIVE = 259


class LockBusyError(Exception):
    """独占锁忙 / 冲突的确定型异常（新鲜持有者在场超时、认领仲裁落败、
    接管窗口被第三者抢先）。绝不静默吞掉，也绝不无限等待后才抛。"""


# —— 时间与文件小助手（自足复制，不 import runtime 包内模块） ——

def _utc_now_iso():
    """当前 UTC 时刻（ISO8601，毫秒精度，Z 后缀——锁 created_at 统一形态）。"""
    return datetime.now(timezone.utc).isoformat(
        timespec="milliseconds").replace("+00:00", "Z")


def _parse_iso_utc(value):
    """解析 ISO8601 → aware datetime；非 str / 空 / 不可解析 → None。
    兼容 Z/z 后缀（Py3.11 之前 fromisoformat 不认 Z）。"""
    if not isinstance(value, str) or value == "":
        return None
    probe = value[:-1] + "+00:00" if value.endswith(("Z", "z")) else value
    try:
        moment = datetime.fromisoformat(probe)
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else None


def _unlink_quiet(path):
    """尽力删除；不存在 / 竞态消失 → 静默（清理路径绝不遮蔽主异常）。"""
    try:
        os.unlink(path)
    except OSError:
        pass


def _unlink_retry(path, *, attempts=UNLINK_RETRY_ATTEMPTS,
                  interval=UNLINK_RETRY_INTERVAL_SECONDS, sleep=time.sleep):
    """尽力删除；Windows 并发读句柄（无 FILE_SHARE_DELETE）的瞬态拒绝 →
    有界小步重试后仍失败 → False；FileNotFoundError 视为成功。"""
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


def _open_excl(path):
    """O_CREAT|O_EXCL 独占创建打开 → fd；已存在 → None；Windows
    delete-pending 瞬态拒绝（PermissionError）→ 一并按「此刻未获取」返
    回 None（调用方在 deadline 有界的判定循环里重评）；其余 OSError 上抛。"""
    try:
        return os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _O_BINARY,
                       0o600)
    except (FileExistsError, PermissionError):
        return None


def _pid_alive(pid):
    """pid 进程是否存活（仅用于锁仲裁）。非 int / bool / <=0 → False；
    Windows ctypes OpenProcess 探测，失败一律视为已死；POSIX os.kill
    （ESRCH → 死；EPERM = 存在但无权发信号 → 活）。"""
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == "nt":
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(
            _PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return False
            return exit_code.value == _STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


# —— 原子写：唯一临时名 + os.replace + PermissionError 有界重试 ——

def _cleanup_abandoned_temps(target, *, min_age=TEMP_CLEANUP_MIN_AGE_SECONDS):
    """尽力清理目标目录内本模块前缀、龄期超 min_age 的废弃临时文件；
    任何失败一律静默（龄期闸保护并行写方在写的临时文件）。"""
    directory = os.path.dirname(target) or "."
    prefix = os.path.basename(target) + TEMP_MARKER
    try:
        names = os.listdir(directory)
    except OSError:
        return
    for name in names:
        if not name.startswith(prefix):
            continue
        candidate = os.path.join(directory, name)
        try:
            if not os.path.isfile(candidate):
                continue
            if time.time() - os.path.getmtime(candidate) < float(min_age):
                continue
        except OSError:
            continue
        _unlink_quiet(candidate)


def atomic_write_json(path, payload, *, indent=2, sort_keys=True,
                      retry_attempts=WRITE_RETRY_ATTEMPTS,
                      retry_interval=WRITE_RETRY_INTERVAL_SECONDS,
                      sleep=time.sleep):
    """原子写 JSON 文件（唯一临时名 + os.replace），返回最终路径（str）。

    序列化（ensure_ascii=False，indent / sort_keys 透传）先于任何 I/O：
    payload 不可序列化在创建临时文件前即抛。UTF-8 + 固定 \n 换行落盘，
    flush + close 后尽力 fsync；父目录缺失自动创建。PermissionError
    （Windows AV / 目录锁瞬态）→ retry_attempts 次 × retry_interval 秒
    有界重试（sleep 为休眠注入，测试零等待），耗尽原样上抛最后的
    PermissionError；其余异常清理临时文件后上抛。读方永不见撕裂文件。
    每次调用前顺带清理同前缀废弃临时文件（见模块纪律）。"""
    text = json.dumps(payload, ensure_ascii=False, indent=indent,
                      sort_keys=sort_keys)
    if float(retry_interval) < 0:
        raise ValueError(
            "atomic_write_json：retry_interval 必须 >= 0，得到 %r"
            % (retry_interval,))
    attempts = max(1, int(retry_attempts))
    path = os.fspath(path)
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    _cleanup_abandoned_temps(path)
    prefix = os.path.basename(path) + TEMP_MARKER
    last_error = None
    for attempt in range(attempts):
        tmp_path = os.path.join(
            directory or ".",
            "%s%d.%s.tmp" % (prefix, os.getpid(), uuid.uuid4().hex))
        try:
            fd = os.open(tmp_path,
                         os.O_WRONLY | os.O_CREAT | os.O_TRUNC | _O_BINARY,
                         0o600)
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
                handle.flush()
                try:  # fsync 尽力而为（网络盘 / 特殊文件系统可能拒绝）
                    os.fsync(handle.fileno())
                except OSError:
                    pass
            os.replace(tmp_path, path)
            return path
        except PermissionError as exc:  # Windows AV / 目录锁瞬态 → 有界重试
            last_error = exc
            _unlink_quiet(tmp_path)
            if attempt + 1 < attempts:
                sleep(float(retry_interval))
        except BaseException:  # 其余失败：清理临时文件后原样上抛
            _unlink_quiet(tmp_path)
            raise
    raise last_error  # 有界重试耗尽：原样上抛最后的 PermissionError


def atomic_read_json(path, default=None, *, attempts=READ_RETRY_ATTEMPTS):
    """容错读 JSON：文件缺失或内容不可解析 → default（本函数绝不抛）。

    FileNotFoundError → 立即 default（缺失是常态非错误）；其余 OSError /
    JSON 解码失败 → 有界重读（attempts 次、立即重试——竞争窗口是瞬态
    的），仍失败 → default（读方 fail-open，坏内容绝不炸消费方）。"""
    path = os.fspath(path)
    for _attempt in range(max(1, int(attempts))):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except FileNotFoundError:
            return default
        except (OSError, ValueError):
            continue
    return default


# —— 独占锁：O_CREAT|O_EXCL + claim-marker race-safe 陈旧接管 ——

def _write_lock_content(fd, pid, generation, owner):
    """向已 O_EXCL 打开的锁 fd 写锁内容 JSON 并关闭（写失败由调用方删锁
    上抛——绝不留空锁）。owner 为本次获取的唯一身份键（uuid4 hex）。"""
    payload = json.dumps(
        {"pid": pid, "generation": generation,
         "created_at": _utc_now_iso(), "owner": owner},
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
    """锁内容变更指纹（接管复核用：created_at + generation + owner）。"""
    if not isinstance(state, dict):
        return None
    return (state.get("created_at"), state.get("generation"),
            state.get("owner"))


def _lock_is_fresh(state, *, stale_seconds, now):
    """内容完整的新鲜锁：created_at 距 now ≤ stale_seconds 且持有者 pid
    可判定存活；否则（缺失 / 不可解析 / 超龄 / pid 死）不新鲜。"""
    if not isinstance(state, dict):
        return False
    created = _parse_iso_utc(state.get("created_at"))
    if created is None:
        return False
    if (now - created).total_seconds() > float(stale_seconds):
        return False
    return _pid_alive(state.get("pid"))


def _file_age_seconds(path, now):
    """文件 mtime 距参考时刻 now（aware datetime）的秒数；不可得 → None。"""
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    return now.timestamp() - mtime


def _lock_reads_held(kind, state, lock_path, *, stale_seconds, now):
    """锁是否视同「正被新鲜持有」（应轮询而非接管）：内容完整且新鲜；
    或写一半的瞬态（corrupt——持有者 O_EXCL 后正写内容，微秒级窗口）。
    corrupt 的龄期判定：mtime 可得且未超 CORRUPT_LOCK_GRACE_SECONDS →
    持有中；已超龄 → 崩溃遗留（可接管）；mtime 探不到（读与 stat 之间
    锁刚被释放消失）→ 一律按持有中轮询重评——瞬态误判绝不送进接管仲裁
    （锁真消失则下轮 O_EXCL 直取即赢；已被合法后来者换新则轮询等待）。
    missing → False（由 O_EXCL 重试路径处理）。"""
    if kind == "ok":
        return _lock_is_fresh(state, stale_seconds=stale_seconds, now=now)
    if kind == "corrupt":
        age = _file_age_seconds(lock_path, now)
        return True if age is None else age <= CORRUPT_LOCK_GRACE_SECONDS
    return False


def _release_lock(path, pid, owner, *, sleep=time.sleep):
    """删除锁文件，但仅当锁内容仍是 (pid, owner) 自己——绝不误删后继接
    管者的锁（内容不可读 / 已易主 → 保留）。删除走有界重试（Windows 并
    发读句柄的瞬态拒绝若静默放过，会在「已释放」路径留下幽灵新鲜锁——
    同进程 pid 恒活、无人再敢接管，等于死锁）。"""
    kind, state = _read_lock_file(path)
    if kind == "ok" and state.get("pid") == pid \
            and state.get("owner") == owner:
        _unlink_retry(path, sleep=sleep)


def _recheck_after_claim(lock_path, stale_fingerprint, *, stale_seconds, now):
    """认领建立后的锁复核 → "gone"（已消失，回 O_EXCL 循环）| "busy"
    （被新鲜持有者占用或内容已换——他人已接管）| "stale"（仍是原陈旧锁，
    可继续接管；corrupt 视同 stale，由 settle 后第二次复核兜底）。"""
    kind, current = _read_lock_file(lock_path)
    if kind == "missing":
        return "gone"
    if kind == "ok":
        if _lock_is_fresh(current, stale_seconds=stale_seconds, now=now):
            return "busy"
        if _lock_fingerprint(current) != stale_fingerprint:
            return "busy"
    return "stale"


def _claim_is_winner(claim_path, lock_path):
    """认领仲裁：本认领标记是否为全部存活认领标记中字典序最小者。存活 =
    龄期 ≤ CLAIM_MARKER_TTL_SECONDS（孤儿标记只忽略、绝不代删）。"""
    directory = os.path.dirname(lock_path) or "."
    prefix = os.path.basename(lock_path) + CLAIM_MARKER
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
            if time.time() - os.path.getmtime(rival) > CLAIM_MARKER_TTL_SECONDS:
                continue
        except OSError:
            continue
        if name < mine:
            return False
    return True


def _takeover_stale_lock(lock_path, pid, owner, stale_state, *,
                         stale_seconds, poll_interval, sleep, now):
    """陈旧锁接管（claim-marker 仲裁）。返回接管后 generation；None =
    复核发现锁已消失（调用方回 O_EXCL 循环）；其余落败路径一律立即抛
    确定型 LockBusyError——除陈旧锁本体与自己的认领标记外绝不删任何文件。"""
    stale_fingerprint = _lock_fingerprint(stale_state)
    prior_generation = 1
    if isinstance(stale_state, dict):
        try:
            prior_generation = max(1, int(stale_state.get("generation")))
        except (TypeError, ValueError):
            prior_generation = 1
    claim_path = "%s%s%s" % (lock_path, CLAIM_MARKER, uuid.uuid4().hex)
    fd = _open_excl(claim_path)
    if fd is None:  # uuid4 唯一名冲突在实践不可能；保守按忙处理
        raise LockBusyError(
            "exclusive_lock：%s 认领标记创建冲突（不可能路径，保守落败）"
            % claim_path)
    try:
        os.close(fd)  # 标记内容不承载语义，存在性即仲裁凭证
        verdict = _recheck_after_claim(lock_path, stale_fingerprint,
                                       stale_seconds=stale_seconds, now=now)
        if verdict == "gone":
            return None  # 锁刚消失（持有者正常释放）→ 回 O_EXCL 循环
        if verdict == "busy":
            raise LockBusyError(
                "exclusive_lock：%s 在认领期间被他人接管或持有者复活"
                % lock_path)
        sleep(max(0.0, float(poll_interval)))  # settle：让迟到认领落盘
        if not _claim_is_winner(claim_path, lock_path):
            raise LockBusyError(
                "exclusive_lock：%s 陈旧锁接管仲裁落败（存在更小认领标记）"
                % lock_path)
        verdict = _recheck_after_claim(lock_path, stale_fingerprint,
                                       stale_seconds=stale_seconds, now=now)
        if verdict == "gone":
            return None
        if verdict == "busy":
            raise LockBusyError(
                "exclusive_lock：%s 在仲裁 settle 期间被他人接管" % lock_path)
        _unlink_retry(lock_path, sleep=sleep)  # 认领赢家独有权利：删陈旧锁
        next_fd = None
        for _attempt in range(max(1, int(UNLINK_RETRY_ATTEMPTS))):
            # 陈旧锁刚被删、等待方读句柄尚在 → delete-pending 瞬态拒绝，
            # 有界小步重取；被第三者真抢先（O_EXCL 实存在）同理耗尽落败
            next_fd = _open_excl(lock_path)
            if next_fd is not None:
                break
            sleep(float(UNLINK_RETRY_INTERVAL_SECONDS))
        if next_fd is None:  # 有界重取耗尽 → 确定落败
            raise LockBusyError(
                "exclusive_lock：%s 接管窗口被第三者抢先持有" % lock_path)
        try:
            _write_lock_content(next_fd, pid, prior_generation + 1, owner)
        except BaseException:
            _unlink_quiet(lock_path)
            raise
        return prior_generation + 1
    finally:
        _unlink_quiet(claim_path)


def _acquire_lock(lock_path, *, timeout_seconds, poll_interval,
                  stale_seconds, sleep):
    """acquire 主循环：O_EXCL 直取 → 新鲜锁有界忙等 → 陈旧锁认领接管。
    返回 (generation, owner)（owner 供释放时身份比对）。全程有界：忙等
    受 deadline 约束、仲裁路径确定型落败，无任何无限循环。"""
    deadline = time.monotonic() + max(0.0, float(timeout_seconds))
    poll = max(0.0, float(poll_interval))
    pid = os.getpid()
    owner = uuid.uuid4().hex  # 本次获取的唯一身份键（释放比对用）
    while True:
        fd = _open_excl(lock_path)
        if fd is not None:
            try:
                _write_lock_content(fd, pid, 1, owner)
            except BaseException:  # 内容写失败：不能留下空锁
                _unlink_quiet(lock_path)
                raise
            return 1, owner
        kind, state = _read_lock_file(lock_path)
        if kind == "missing":  # 持有者刚释放 / delete-pending 残名已清 →
            # 小步后再 O_EXCL（sleep 保证该路径与其他未获取路径一样受
            # deadline 约束，绝不空转）
            if time.monotonic() >= deadline:
                raise LockBusyError(
                    "exclusive_lock：%s 在获取窗口反复错过可用间隙，有界等"
                    "待 %.2f 秒后超时" % (lock_path, float(timeout_seconds)))
            sleep(poll)
            continue
        now = datetime.now(timezone.utc)
        if _lock_reads_held(kind, state, lock_path,
                            stale_seconds=stale_seconds, now=now):
            if time.monotonic() >= deadline:
                raise LockBusyError(
                    "exclusive_lock：%s 被新鲜持有者占用（pid=%r，"
                    "created_at=%r），有界等待 %.2f 秒后超时" % (
                        lock_path,
                        state.get("pid") if kind == "ok" else None,
                        state.get("created_at") if kind == "ok"
                        else "（内容写一半，mtime 龄期未超写入中闸）",
                        float(timeout_seconds)))
            sleep(poll)
            continue
        generation = _takeover_stale_lock(
            lock_path, pid, owner, state, stale_seconds=stale_seconds,
            poll_interval=poll, sleep=sleep, now=now)
        if generation is not None:
            return generation, owner
        sleep(poll)  # 锁刚消失的窄窗回退：小步后再 O_EXCL，绝不空转


@contextlib.contextmanager
def exclusive_lock(lock_path, *, timeout_seconds=DEFAULT_TIMEOUT_SECONDS,
                   poll_interval=DEFAULT_POLL_INTERVAL_SECONDS,
                   stale_seconds=DEFAULT_STALE_SECONDS, sleep=time.sleep):
    """独占锁上下文管理器，yield 本次持有的 generation（int）。

    用法：with exclusive_lock(p, timeout_seconds=5.0): ...
    参数（默认与完整纪律见模块 docstring）：timeout_seconds 新鲜锁忙等
    上界（耗尽 → LockBusyError）；poll_interval 忙等与认领 settle 轮询
    小步；stale_seconds created_at 陈旧阈值；sleep 休眠注入（测试零等
    待）。with 体正常或异常退出均经 finally 释放；释放仅当锁内容仍是自
    己的 pid + owner（绝不误删后继接管者的锁）。"""
    lock_path = os.fspath(lock_path)
    directory = os.path.dirname(lock_path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    generation, owner = _acquire_lock(
        lock_path, timeout_seconds=timeout_seconds,
        poll_interval=poll_interval, stale_seconds=stale_seconds, sleep=sleep)
    try:
        yield generation
    finally:
        _release_lock(lock_path, os.getpid(), owner, sleep=sleep)


def atomic_update_json(path, updater, *, default=None,
                       timeout_seconds=DEFAULT_TIMEOUT_SECONDS,
                       poll_interval=DEFAULT_POLL_INTERVAL_SECONDS,
                       stale_seconds=DEFAULT_STALE_SECONDS,
                       retry_attempts=WRITE_RETRY_ATTEMPTS,
                       retry_interval=WRITE_RETRY_INTERVAL_SECONDS,
                       sleep=time.sleep):
    """独占锁（<path>.lock，与目标同目录）保护下的读-改-写。

    锁内序：atomic_read_json 读当前值（缺失 → default 非 None 用
    default，否则空 dict——updater 恒收到 dict）→ updater(当前值) → 新
    payload 原子写回。返回 updater 产出的新 payload。updater 抛错 → 目
    标不被触碰、异常原样传播，锁经 finally 必释放。timeout_seconds /
    poll_interval / stale_seconds 透传 exclusive_lock；retry_* / sleep
    透传原子写与锁（测试可注入零等待）。典型用法（并发计数）：
        atomic_update_json(p, lambda d: {"count": d.get("count", 0) + 1})
    """
    path = os.fspath(path)
    initial = default if default is not None else {}
    with exclusive_lock(path + LOCK_SUFFIX, timeout_seconds=timeout_seconds,
                        poll_interval=poll_interval,
                        stale_seconds=stale_seconds, sleep=sleep):
        current = atomic_read_json(path, default=initial)
        updated = updater(current)
        atomic_write_json(path, updated, retry_attempts=retry_attempts,
                          retry_interval=retry_interval, sleep=sleep)
    return updated
