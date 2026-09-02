#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GLM Conductor v2.2 Real-Time Quota Watcher 独立长运行 I/O 控制循环
（修正计划 C3 / §6 / §6.1 / §7，wu-22-C3 第一阶段骨架，skeleton-first）。

职责（修正计划 §5 图：watcher.py = NEW: long-running I/O control loop；
§6.1 要证明的命题：**Session dormant 时，GLM-Conductor 可以独立、持续、
可靠地维护真实 provider quota clock**）：
    独立进程内循环：判定 ACTIVE / PASSIVE → provider-only 抓取 →
    epoch 折算 → 原子更新 watcher.json（heartbeat + last_observation）
    → 按模式自适应休眠 → 检查 stop_requested 旗标优雅退出。

§7 ACTIVE / PASSIVE 模式判定（冻结；循环内每 tick 重判——continuous
activation is demand-coupled, not permanent）：
    扫描 <repo_root>/.glm-conductor/tasks/*/state.json（只读）——
      - 存在 status=waiting_quota 任务 → ACTIVE；
      - 或存在 status ∈ executing 族（executing / joining / verifying /
        reviewing）且 execution_policy.continuity.auto_resume ∈
        {auto_once, until_done} 的任务 → ACTIVE；
      - 否则 PASSIVE。manual / notify 默认 PASSIVE（§7：可观察，
        但 **不 prime、不发 activation**）。

每 tick 流程：
    1. 读 watcher.json → stop_requested=True → 写「stopped」终态
       （pid=None，冻结字段集内表达）并退出；
    2. 重判 mode；observer.should_refresh 判是否到期（未到期 → 仅写
       heartbeat，绝不空转打 provider）；
    3. 到期 → resolver provider-only 抓取（复用
       resolver.resolve_quota_detail(repo_root, force_refresh=True)
       ——与 quota-resolve --force-refresh 同源，绝不复制抓取/parser
       逻辑）→ epoch.evaluate_epoch 从 §27 snapshot windows 计算
       epoch_id / probe_boundary_at → 原子写 watcher.json；
    4. 间隔选择：ACTIVE 复用 observer.next_check_interval 自适应表
       （observer.py 纯决策层零改动、零 I/O——本循环是 I/O 侧消费
       方，不把 I/O 塞回 observer）；PASSIVE 低频固定间隔（默认
       900 秒，参数可配）。

heartbeat / 休眠步长：watcher_store 的 heartbeat 新鲜阈值默认 180 秒，
而 ACTIVE NORMAL 相自适应间隔可达 1800 秒——若一步睡满间隔，heartbeat
必然过期、锁会被第二进程误接管。故循环以 heartbeat_step_seconds（默认
60 秒，< 新鲜阈值）为步长分片休眠：每次醒来检查 stop_requested、续
heartbeat，到期才真正抓取（这是 observer.should_refresh 在生产路径的
真实用途：分片唤醒后判「是否到下一次 poll」）。

graceful stop：CLI stop 置 stop_requested（watcher_store.request_stop，
单次操作不等待）→ 循环下一 wake 检查到旗标 → 写 stopped 终态
（pid=None + stop_requested=True）并退出。

once：前台单次抓取（测试 / 诊断用）。**不走锁接管**：现存记录 pid
活着且 heartbeat 新鲜 → 报冲突退出（ran=False）；stale / 无记录 →
执行一次 tick 语义的抓取并写记录（generation 不 +1——不是接管，
once 进程退出后 pid 即死，后续 acquire 自然走接管路径）。

第一阶段明确不做（§6.1 冻结）：Window Primer / task subscription /
ActivationReady→Session 激活 / **model call（零模型调用）** / session
injector / event bus。watcher 只写自身状态文件，绝不写任务 state /
journal（task 面接线归 C5+）。

纪律（本模块的硬边界）：
    - 可注入时钟（clock）/ 可注入 fetch / 可注入 sleep / max_ticks
      上限——测试零真实等待、零真实网络；生产缺省全部接真实实现；
    - fetch 异常容错不崩：异常只取类型名进 last_observation.error
      （绝不透传异常文本，resolver 同纪律），循环继续；
    - provider identity hash = sha256("来源:凭证")[:16]——只落哈希
      不落凭证材料（runtime/quota/_http.py §37 安全条款 / 升级指南
      §36-§38；不可逆指纹，用于 §6 单实例锁身份）；
    - quota/* 包纪律：不 import runtime.state / runtime.task_manager；
      executing 族词汇以本地冻结常量镜像（tests 与
      task_manager.QUOTA_WAIT_TASK_STATUSES 对齐锚定）。

依赖：
    仅 Python 3 标准库 + runtime.quota.scheduler / observer / epoch /
    resolver / credentials / watcher_store；observer 纯决策层零改动。

来源：
    docs/GLM-Conductor-v2.2-Quota-Control-Plane-Architecture-Correction-
    and-Agent-Implementation-Plan.md §5（runtime/quota 布局）/ §6
    （Watcher 运行模式）/ §6.1（第一阶段范围）/ §7（ACTIVE / PASSIVE）
    / §10（epoch）/ §11（状态模型）/ §6.2（P0-WATCH-00）+ 工作单元
    wu-22-C3。
"""

import hashlib
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

from runtime.quota import epoch as quota_epoch
from runtime.quota import observer, resolver
from runtime.quota import watcher_store
from runtime.quota.observer import should_refresh as _should_refresh
from runtime.quota.scheduler import (
    _format_iso_z,
    _parse_iso_utc,
)
from runtime.quota.watcher_store import WATCHER_MODES

# —— 词汇表常量 ——

# §7 冻结：ACTIVE 侧的 auto_resume 取值（manual / notify 默认 PASSIVE，
# 可观察但不 prime）
ACTIVE_AUTO_RESUME_MODES = ("auto_once", "until_done")

# §7 executing 族（与 task_manager.QUOTA_WAIT_TASK_STATUSES 同词汇；
# quota 包纪律禁 import task_manager，镜像对齐由测试锚定）
EXECUTING_FAMILY_STATUSES = ("executing", "joining", "verifying",
                             "reviewing")

# PASSIVE 低频固定间隔（秒；§6.1 规格默认 900，参数可配）
PASSIVE_INTERVAL_SECONDS = 900

# 休眠步长（秒）：必须小于 watcher_store 的 heartbeat 新鲜阈值
# （默认 180）——heartbeat 永不在长间隔（ACTIVE NORMAL 1800s）休眠中
# 过期，锁不被第二进程误接管
HEARTBEAT_STEP_SECONDS = 60


# —— 内部小助手 ——

def _system_clock():
    """生产时钟：当前 UTC（aware datetime）。"""
    return datetime.now(timezone.utc)


def _default_fetch(repo_root):
    """生产抓取入口：resolver provider-only 抓取（与 quota-resolve
    --force-refresh 同源；resolver 内部任何失败自行降级，不抛）。"""
    return resolver.resolve_quota_detail(repo_root, force_refresh=True)


def _safe_fetch(fetch_fn, repo_root):
    """fetch 容错包装：异常只取类型名（不透传文本——可能携带 URL /
    响应体），返回 (detail|None, error_type_name|None)。"""
    try:
        return fetch_fn(repo_root), None
    except Exception as exc:  # noqa: BLE001 —— 容错面：类型名即全部信息
        return None, type(exc).__name__


def compute_provider_identity_hash(environ=None):
    """provider identity 指纹（§6 单实例锁身份字段）：sha256("来源:凭证")
    十六进制前 16 位。

    只落哈希绝不落凭证材料（runtime/quota/_http.py §37 安全条款 /
    升级指南 §36-§38——哈希不可逆，不含 key 本体）；无凭证
    → ("none" 来源的确定性占位哈希)——watcher 仍可 PASSIVE 运行，
    acquire 语义不受影响。
    """
    from runtime.quota.credentials import resolve_credential
    _source, api_key = resolve_credential(environ=environ)
    material = "%s:%s" % ("none" if api_key is None else "credential",
                          api_key or "")
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


# —— §7 模式判定（冻结；只读任务 state，绝不写） ——

def determine_mode(repo_root) -> str:
    """§7 模式判定：扫描 tasks/*/state.json（只读、容错——单文件损坏
    跳过不影响其余）→ "ACTIVE" | "PASSIVE"。

    规则（按序，任一命中即 ACTIVE）：
      1. 存在 status == "waiting_quota" 的任务；
      2. 存在 status ∈ EXECUTING_FAMILY_STATUSES 且
         execution_policy.continuity.auto_resume ∈
         ACTIVE_AUTO_RESUME_MODES 的任务。
    否则 PASSIVE（含 tasks 目录不存在 / 全部任务 manual / notify）。
    """
    tasks_root = os.path.join(str(repo_root), ".glm-conductor", "tasks")
    if not os.path.isdir(tasks_root):
        return "PASSIVE"
    for name in sorted(os.listdir(tasks_root)):
        state_path = os.path.join(tasks_root, name, "state.json")
        if not os.path.isfile(state_path):
            continue
        try:
            with open(state_path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            continue  # 损坏 / 半写 state：跳过（判定绝不因坏文件炸掉）
        if not isinstance(data, dict):
            continue
        status = data.get("status")
        if status == "waiting_quota":
            return "ACTIVE"
        if status in EXECUTING_FAMILY_STATUSES:
            policy = data.get("execution_policy")
            continuity = (policy.get("continuity")
                          if isinstance(policy, dict) else None)
            auto_resume = (continuity.get("auto_resume")
                           if isinstance(continuity, dict) else None)
            if auto_resume in ACTIVE_AUTO_RESUME_MODES:
                return "ACTIVE"
    return "PASSIVE"


# —— 轮询间隔选择（ACTIVE 自适应 / PASSIVE 固定） ——

def next_poll_interval(*, mode, execution_phase=None, reset_at=None, now,
                       passive_interval_seconds=PASSIVE_INTERVAL_SECONDS):
    """下一 tick 抓取距 now 的秒数（int）。

    ACTIVE → observer.next_check_interval 自适应表（复用纯决策层，
    observer.py 零改动）；PASSIVE → 固定 passive_interval_seconds
    （缺省 900）。execution_phase=None（抓取失败 / 相不可解析）时
    ACTIVE 也退化为固定间隔——**不虚构执行相**（§31 同源纪律：
    fail-open 而非编造 NORMAL）。mode / now 词汇非法、间隔非正整数
    → ValueError（参数校验先于 I/O）。
    """
    if mode not in WATCHER_MODES:
        raise ValueError(
            "next_poll_interval：mode %r 不在合法取值内（%s）"
            % (mode, ", ".join(WATCHER_MODES)))
    if not isinstance(passive_interval_seconds, int) \
            or isinstance(passive_interval_seconds, bool) \
            or passive_interval_seconds < 1:
        raise ValueError(
            "next_poll_interval：passive_interval_seconds 必须是 >= 1 "
            "的整数，得到 %r" % (passive_interval_seconds,))
    if mode == "PASSIVE" or execution_phase is None:
        return passive_interval_seconds
    return observer.next_check_interval(
        execution_phase=execution_phase, reset_at=reset_at, now=now)


def _phase_and_reset(detail):
    """抓取明细 → (execution_phase, reset_at)（复用 observer.observe
    纯决策；provider_status 词汇外（理论不可达）→ (None, None) 不虚构）。

    windows 非法形状 → None（observe 内 fail-open 按空窗口）；now 不传
    （observe 的 now=None 语义：next_check_at=None，绝不读墙钟）。
    """
    if not isinstance(detail, dict):
        return None, None
    snapshot = detail.get("snapshot")
    windows = (snapshot.get("windows")
               if isinstance(snapshot, dict) else None)
    try:
        observation = observer.observe(
            provider_status=detail.get("status"), windows=windows,
            now=None)
    except ValueError:
        return None, None
    return observation["execution_phase"], observation["reset_at"]


def _build_observation(detail, error, now):
    """抓取结果 → last_observation dict（含 §27 窗口的 per-window
    reset_at 摘要 + epoch 折算；fetch 失败 → error 字段记类型名，
    其余观察键 None 不虚构）。

    epoch 折算复用 epoch.evaluate_epoch（§10：epoch_id /
    probe_boundary_at / executable / canonical windows）；status 词汇外
    （evaluate_epoch ValueError，理论不可达——resolver 恒出四态）→
    epoch 键全部 None 不虚构。
    """
    observation = {
        "observed_at": _format_iso_z(now),
        "status": None,
        "source": None,
        "epoch_id": None,
        "probe_boundary_at": None,
        "executable": None,
        "windows": [],
        "error": error,
    }
    if error is not None or not isinstance(detail, dict):
        return observation
    status = detail.get("status")
    snapshot = detail.get("snapshot")
    windows = (snapshot.get("windows")
               if isinstance(snapshot, dict) else None)
    observation["status"] = status
    observation["source"] = detail.get("source")
    try:
        epoch_info = quota_epoch.evaluate_epoch(
            provider_status=status, windows=windows or [])
    except ValueError:
        return observation
    observation["epoch_id"] = epoch_info["epoch_id"]
    observation["probe_boundary_at"] = epoch_info["probe_boundary_at"]
    observation["executable"] = epoch_info["executable"]
    observation["windows"] = epoch_info["windows"]  # per-window reset_at 摘要
    return observation


def _remaining_seconds(now, next_poll_at):
    """距 next_poll_at（Z 形式 ISO）的剩余秒数（float，下限 0）；
    next_poll_at None / 不可解析 → 0.0（立即到期，fail-open）。"""
    if next_poll_at is None:
        return 0.0
    due = _parse_iso_utc(next_poll_at)
    if due is None:
        return 0.0
    return max(0.0, (due - now).total_seconds())


def _merge_stop_flag(repo_root, record):
    """写前合并并发的 stop_requested（防丢旗标竞争）。

    tick 开头读的 record 与 tick 末尾的写之间，CLI stop 可能已原子置位
    旗标（request_stop 读-改-写当前文件）；若直接用唤醒时的旧内存副本
    整体覆盖写，会把这枚并发旗标抹掉（测试实录：stop 在 fetch 期间
    置位即被 tick 写回冲掉，watcher 永不退出）。watcher 自身从不置
    True，故写前重读一次文件、OR 合并旗标即可；窗口收窄到「合并读
    之后」的微秒级（文件即锁、无 CAS 的固有窗口，见模块 docstring）。
    """
    fresh = watcher_store.read_watcher_state(repo_root)
    if fresh is not None and fresh.get("stop_requested"):
        record["stop_requested"] = True


# —— 主入口：长运行循环 / 单次抓取 ——

def run(repo_root, *, fetch=None, clock=None, sleep=None,
        passive_interval_seconds=PASSIVE_INTERVAL_SECONDS,
        heartbeat_step_seconds=HEARTBEAT_STEP_SECONDS,
        heartbeat_stale_seconds=
        watcher_store.DEFAULT_HEARTBEAT_STALE_SECONDS,
        provider_identity_hash=None, max_ticks=None,
        mode_reader=determine_mode):
    """独立长运行 I/O 控制循环主入口（进程主入口；CLI --serve 调用）。

    参数（除 repo_root 全 keyword-only）：
      - fetch：抓取函数 fetch(repo_root) → resolver 明细 dict（缺省
        _default_fetch = resolver.resolve_quota_detail force_refresh；
        测试注入 fake——零真实网络）；
      - clock：clock() → aware datetime（缺省 UTC 墙钟；测试注入）；
      - sleep：sleep(seconds)（缺省 time.sleep；测试注入 no-op 零等待）；
      - passive_interval_seconds：PASSIVE 固定间隔（默认 900）；
      - heartbeat_step_seconds：休眠分片步长（默认 60，须 < heartbeat
        新鲜阈值——heartbeat 永不在休眠中过期）；
      - heartbeat_stale_seconds：锁仲裁的新鲜阈值（透传 store）；
      - provider_identity_hash：注入锁身份（缺省
        compute_provider_identity_hash()）；
      - max_ticks：循环 wake 次数上限（None = 生产无限循环；测试 /
        诊断注入整数）；
      - mode_reader：模式判定注入（缺省 determine_mode 真实扫描）。

    流程：acquire（冲突即返回）→ 循环{读记录 → stop_requested → 写
    stopped 终态退出；重判 mode → should_refresh 到期则抓取+写观察，
    未到期仅续 heartbeat → 分片休眠}。

    返回（键冻结）：
      {"acquired", "takeover", "generation", "stopped", "wakes",
       "fetches", "last_interval", "conflict", "mode"}
    """
    fetch_fn = fetch if fetch is not None else _default_fetch
    clock_fn = clock if clock is not None else _system_clock
    sleep_fn = sleep if sleep is not None else time.sleep
    identity = (provider_identity_hash if provider_identity_hash is not None
                else compute_provider_identity_hash())
    if not isinstance(heartbeat_step_seconds, (int, float)) \
            or isinstance(heartbeat_step_seconds, bool) \
            or heartbeat_step_seconds <= 0 \
            or heartbeat_step_seconds >= heartbeat_stale_seconds:
        raise ValueError(
            "run：heartbeat_step_seconds 必须是 0 < step < "
            "heartbeat_stale_seconds（%r）的数值，得到 %r"
            % (heartbeat_stale_seconds, heartbeat_step_seconds))

    now = clock_fn()
    mode = mode_reader(repo_root)
    lock = watcher_store.acquire_watcher_lock(
        repo_root, provider_identity_hash=identity, mode=mode, now=now,
        heartbeat_stale_seconds=heartbeat_stale_seconds)
    if not lock["acquired"]:
        return {"acquired": False, "takeover": False, "generation": None,
                "stopped": False, "wakes": 0, "fetches": 0,
                "last_interval": None, "conflict": lock["conflict"],
                "mode": None}
    generation = lock["generation"]

    interval = passive_interval_seconds  # 保守初值；首个抓取 tick 后按相重算
    next_poll_at = None  # None → should_refresh(None) = True：首 tick 必抓取
    wakes = 0
    fetches = 0
    last_interval = None
    stopped = False
    while max_ticks is None or wakes < max_ticks:
        record = watcher_store.read_watcher_state(repo_root)
        if record is None:
            break  # 状态被外部移除：不重建锁（锁即文件），退出
        wakes += 1  # 每 wake 计数（含 stop 退出 wake；max_ticks 边界）
        if record.get("stop_requested"):
            # graceful stop：写 stopped 终态（pid=None，冻结字段集内
            # 表达，不新增 status 字段）并退出
            record["pid"] = None
            record["heartbeat_at"] = _format_iso_z(clock_fn())
            watcher_store.write_watcher_state(repo_root, record)
            stopped = True
            break
        now = clock_fn()
        mode = mode_reader(repo_root)  # 每 tick 重判（demand-coupled）
        if _should_refresh(now=now, next_check_at=next_poll_at):
            detail, error = _safe_fetch(fetch_fn, repo_root)
            observation = _build_observation(detail, error, now)
            phase, reset_at = _phase_and_reset(detail)
            interval = next_poll_interval(
                mode=mode, execution_phase=phase, reset_at=reset_at,
                now=now, passive_interval_seconds=passive_interval_seconds)
            record["mode"] = mode
            record["pid"] = os.getpid()
            record["heartbeat_at"] = _format_iso_z(now)
            record["last_observation"] = observation
            record["next_poll_at"] = _format_iso_z(
                now + timedelta(seconds=int(interval)))
            _merge_stop_flag(repo_root, record)  # 并发 stop 不被覆盖写抹掉
            watcher_store.write_watcher_state(repo_root, record)
            fetches += 1
            last_interval = interval
            next_poll_at = record["next_poll_at"]
        else:
            # 未到期：仅续 heartbeat，绝不空转打 provider
            record["heartbeat_at"] = _format_iso_z(now)
            _merge_stop_flag(repo_root, record)  # 并发 stop 不被覆盖写抹掉
            watcher_store.write_watcher_state(repo_root, record)
        if max_ticks is not None and wakes >= max_ticks:
            break
        remaining = _remaining_seconds(now, next_poll_at)
        sleep_fn(max(0.0, min(remaining, float(heartbeat_step_seconds))))
    return {"acquired": True, "takeover": lock["takeover"],
            "generation": generation, "stopped": stopped, "wakes": wakes,
            "fetches": fetches, "last_interval": last_interval,
            "conflict": None, "mode": mode}


def run_once(repo_root, *, fetch=None, clock=None,
             provider_identity_hash=None,
             heartbeat_stale_seconds=
             watcher_store.DEFAULT_HEARTBEAT_STALE_SECONDS,
             mode_reader=determine_mode):
    """前台单次抓取（测试 / 诊断用；CLI quota-watcher once）。

    **不走锁接管**：现存记录 pid 活着且 heartbeat 新鲜 → 冲突退出
    （ran=False，零写盘）；无记录 / stale → 抓取一次并写记录
    （generation 不 +1——不是接管；pid 写为本次进程，退出即死，后续
    acquire 自然走接管路径；stop_requested 保留原值，once 不消费
    既有停止请求）。

    返回（键冻结）：
      {"ran": bool, "record": dict|None,
       "conflict": None | {...同 acquire 的 conflict 形状}}
    """
    fetch_fn = fetch if fetch is not None else _default_fetch
    clock_fn = clock if clock is not None else _system_clock
    identity = (provider_identity_hash if provider_identity_hash is not None
                else compute_provider_identity_hash())
    now = clock_fn()
    existing = watcher_store.read_watcher_state(repo_root)
    if existing is not None:
        age = watcher_store.heartbeat_age_seconds(existing, now=now)
        fresh = (age is not None
                 and age <= float(heartbeat_stale_seconds))
        if watcher_store._pid_alive(existing.get("pid")) and fresh:
            return {"ran": False, "record": None, "conflict": {
                "pid": existing.get("pid"),
                "heartbeat_at": existing.get("heartbeat_at"),
                "heartbeat_age_seconds": age,
                "provider_identity_hash":
                    existing.get("provider_identity_hash"),
            }}
    detail, error = _safe_fetch(fetch_fn, repo_root)
    observation = _build_observation(detail, error, now)
    mode = mode_reader(repo_root)
    record = dict(existing) if existing is not None else {
        "schema_version": 1,
        "provider_identity_hash": identity,
        "generation": 1,
        "started_at": _format_iso_z(now),
        "stop_requested": False,
        "last_observation": None,
    }
    record["mode"] = mode
    record["pid"] = os.getpid()
    record["heartbeat_at"] = _format_iso_z(now)
    record["last_observation"] = observation
    watcher_store.write_watcher_state(repo_root, record)
    return {"ran": True, "record": record, "conflict": None}


def serve(repo_root, **kwargs):
    """分离派生子进程的 serve 入口（CLI start → `quota-watcher <repo>
    --serve`；stdout/stderr 已由父进程落 watcher.log）。

    acquire 冲突 → 返回 1（父进程的 start 只报派生 pid，冲突详情落
    日志）；graceful stop / max_ticks 退出 → 返回 0。kwargs 透传 run。
    """
    result = run(repo_root, **kwargs)
    if not result.get("acquired"):
        sys.stderr.write(
            "quota-watcher serve：单实例锁冲突（pid=%r heartbeat_at=%r "
            "仍新鲜），退出\n" % (
                (result.get("conflict") or {}).get("pid"),
                (result.get("conflict") or {}).get("heartbeat_at")))
        return 1
    return 0
