#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GLM Conductor v2 任务执行日志层（events.jsonl）。

职责：
    管理 v2 任务的本地执行日志 `.glm-conductor/tasks/<task-id>/events.jsonl`：
    append-only 追加、容错读取、尾部查询。用途是本地执行溯源
    （调试 / 恢复 / 审计），不是遥测。
    v2.2 C5a（wu-22-C5a）起增设控制面 journal
    `.glm-conductor/quota/events.jsonl`（与 watcher.json / primer.json
    同层）：无任务上下文的控制面事件（quota_epoch_advanced、
    window_primed 等）统一落此处，不再借用伪任务目录。

约束（§56）：
    - append-only：append_event 是本模块唯一写入口，只以追加模式（"a"）
      打开文件，绝不重写 / 截断 / 清空已有内容；
    - 任务隔离：日志按任务目录存放，与 runtime.state 管理的 state.json
      同处一个任务目录（<repo_root>/.glm-conductor/tasks/<task-id>/），
      各管各的文件，互不越界；
    - 本地：纯本地文件操作，不做任何网络行为；
    - 无秘密值：调用方不得把凭证 / Authorization 头等秘密值写入事件；
    - 无完整 prompt：只记录结构化事实（事件名 + 少量字段），不落完整
      prompt、完整模型响应或完整源码。

并发限制：
    本模块不做文件锁。有界并行下的并发追加是 v2 后续阶段的工作；
    当前假设同一任务的 journal 写入是串行的。

时间戳：
    每条事件的 "ts" 由 append_event 统一管理（UTC ISO8601，毫秒精度），
    调用方不得在事件中自带 "ts"；测试 / 回放可通过 ts 参数显式传入。

依赖：
    仅 Python 3 标准库（datetime / json / pathlib），零第三方依赖，
    `python3 -S` 可运行。
"""

import json
from datetime import datetime, timezone
from pathlib import Path

JOURNAL_FILENAME = "events.jsonl"

# 推荐事件名词汇表（文档性常量；append_event 不强制成员资格——
# 未来阶段会新增事件名（如 quota_status 等），保持开放式）
RECOMMENDED_EVENTS = (
    "task_created",        # state.json 创建
    "route_selected",      # SELECTIVE ROUTE 声明
    "route_reassessment",  # 路由重估
    "implementation_started",  # 实施者派发
    "dispatch_prepared",   # task_manager.prepare_dispatch（决策通过 + 租约在位）
    "dispatch_aborted",    # task_manager.abort_dispatch（未提交准备的回退）
    "lease_recovered",     # task_manager.recover_leases（崩溃后 stale 租约释放）
    "verification",        # 主会话验证（含 command/status）
    "review",              # 审查者裁决（含 verdict）
    "unit_finished",       # task_manager.finish_unit（单元终态 + 租约释放）
    "checkpoint_written",  # checkpoint 落盘
    "status_changed",      # 任务状态迁移（transition_task_status 记录）
    "gate_blocked",        # Stop 完成门拦截
    "gate_passed",         # 完成门校验通过放行（断链 + 审计）
    "gate_exhausted",      # 完成门连续 block 达运行时上限后放行
    "gate_degraded",       # git 不可用等降级跳过校验
    "reviewer_invoked",    # RB-21-02：reviewer 派发的 runtime-observed 记账（PostToolUse）
    "reviewer_invocation_skipped",  # RB-21-02：marker 指向任务不存在的警告记账
    "completed",
    "cancelled",
    "failed",
    # —— v2.2 M1 控制回路事件（quota 阈值 / continuation obligation /
    # wake bridge / resume controller；词汇仍开放式，本常量只作文档性
    # 推荐，append_event 不强制成员资格）——
    "quota_heartbeat",        # quota 心跳（仅状态/阶段/阈值穿越时记，降噪）
    "quota_phase_changed",    # execution phase 变化（NORMAL/PRESSURE/DRAINING/BLOCKED）
    "continuation_obligation_changed",  # continuation obligation 推进
    "wake_bridge_requested",  # wake bridge 请求创建（DRAINING 探得 boundary）
    "wake_bridge_armed",      # wake bridge 武化（定时唤醒已建立）
    "wake_bridge_failed",     # wake bridge 建置失败
    "wake_bridge_fired",      # wake bridge 触发（唤醒已注入）
    "wake_bridge_cancelled",  # wake bridge 取消
    "wake_bridge_stale",      # wake bridge 过期失效
    "warm_only_completed",    # warm-only 恢复完成（零转态不派发）
    "resume_controller_started",  # resume controller 启动
    "resume_controller_completed",  # resume controller 完成
    "continuity_degraded",    # 连续性降级记账（如 gate_exhausted 放行仍无 bridge）
    # —— v2.2 M1a Persistent Wake Bridge 事件（D15-e：scheduler 能力
    # 观察 / bridge retarget / 暂停 / 降级 / quota 边界消费 / 嵌套创建
    # 拒绝——Phase 0 #13 会话级 cron 创建禁令的记账；词汇仍开放式，
    # 本常量只作文档性推荐，append_event 不强制成员资格）——
    "scheduler_capability_observed",      # scheduler 四能力探针结论记账（create/update/pause/delete）
    "wake_bridge_retargeted",             # wake bridge 重定目标（boundary 变更 / 自改期 / 间隔重排）
    "wake_bridge_pause_requested",        # wake bridge 暂停请求（进入暂停意愿态）
    "wake_bridge_paused",                 # wake bridge 已暂停（automation 置 disabled）
    "wake_bridge_degraded",               # wake bridge 降级（自动化不可用，回退人工唤醒）
    "quota_boundary_consumed",            # quota 边界已消费（§15.1 resume commit point：新 executable epoch 确认且授权恢复实际开始才 +1；v2.2 C1b 起消费语义冻结，arm/fire/create 一律不消费）
    "scheduled_nested_create_rejected",   # 被 Scheduled Task 触发的会话嵌套创建 automation 被拒（SCHED-04 硬门）
    # —— v2.2 C1b resume-time consumption 事件（修正计划 §C1b / §22.5 /
    # §22.6；词汇仍开放式，本常量只作文档性推荐，append_event 不强制
    # 成员资格）——
    "quota_accounting_migrated",  # §22.6 存量窗口记账保守迁移一次性事件（max 语义，不退款）
    "wake_bridge_reconciled",     # §22.5 手动/历史桥对账（纯账本；host_status 由调用方显式提供，绝不制造 armed）
    # —— v2.2 C4 Window Primer 事件（修正计划 §8.1 物化语义 / §8.3 三重
    # 授权 / §C4 红线；词汇仍开放式，本常量只作文档性推荐，append_event
    # 不强制成员资格）——
    "window_primed",  # Window Primer 单飞执行落账（§8.1：一次最小模型调用使新窗口 materialize + 强制 quota refresh 二次确认；materialized 只看 reset_at/epoch 变化——HTTP 200 与百分比下降都不是证据，§C4 红线；executable=False 时无 ActivationReady 概念，归 C5）
    # —— v2.2 C5a 控制面事件（修正计划 §13 事件词汇 / §QC-07；本常量
    # 只作文档性推荐，append_event / append_control_plane_event 均不
    # 强制成员资格）——
    "quota_epoch_advanced",  # quota epoch 推进记账（§QC-07：epoch 推进 → 恰一条；落在控制面 journal 而非任务 journal——epoch 推进无任务上下文也可发生，落点见 append_control_plane_event）
)


class JournalError(Exception):
    """journal 读取/写入的结构性错误（strict 模式坏行、非法事件等）。"""


def journal_path(repo_root, task_id):
    """返回任务执行日志路径 <repo_root>/.glm-conductor/tasks/<task_id>/events.jsonl。"""
    return (Path(repo_root) / ".glm-conductor" / "tasks" / str(task_id)
            / JOURNAL_FILENAME)


def append_event(repo_root, task_id, event, *, ts=None):
    """向任务 journal 追加一条事件，返回写入的完整事件 dict（含 ts）。

    校验（结构性错误抛 JournalError，且在任何 I/O 之前完成）：
      - event 必须是 dict 且含非空 str 的 "event" 键；
      - event 不得含 "ts" 键（时间戳由本函数管理，调用方不得伪造）。

    ts：缺省时自动生成 datetime.now(timezone.utc).isoformat(
        timespec="milliseconds")；也可显式传入 ISO8601 字符串
        （测试 / 回放用）。

    写入：单行 JSON（UTF-8、ensure_ascii=False、键序保持插入序，
    ts 为首键）+ "\\n"，目录不存在自动创建（parents=True）。
    I/O 异常（OSError 等）自然向上抛，不吞。
    """
    if not isinstance(event, dict):
        raise JournalError("event 必须是 dict，得到 %s" % type(event).__name__)
    name = event.get("event")
    if not isinstance(name, str) or not name:
        raise JournalError('event 必须含非空 str 的 "event" 键')
    if "ts" in event:
        raise JournalError('event 不得自带 "ts" 键（时间戳由 append_event 管理）')

    if ts is None:
        ts = datetime.now(timezone.utc).isoformat(timespec="milliseconds")

    # 不改动调用方传入的 dict；ts 置首，其余键保持插入序
    record = {"ts": ts}
    record.update(event)

    path = journal_path(repo_root, task_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    # newline="\n"：Windows 下也不做换行翻译，保证文件恒为 "\n" 分隔的 jsonl
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def read_events(repo_root, task_id, *, strict=False):
    """按文件顺序读取任务 journal 的全部事件，返回 dict 列表。

    容错（默认）：文件不存在返回 []；空白行跳过；坏行（JSON 解析失败
    或解析结果非 dict）跳过——截断尾行常见于写入中断，不能让整个日志
    不可读。strict=True 时坏行抛 JournalError，消息含 1 起始行号。

    读取实现：
      - 按 "\\n" 切分行而非 splitlines：事件值中的 U+0085 / U+2028 /
        U+2029 经 json.dumps(ensure_ascii=False) 原样写入（合法 JSON
        单行内容），splitlines 会在这些字符处错误断行，把一条事件拆成
        两段、后段按坏行静默丢弃；写入端 newline="\\n" 已保证文件恒为
        "\\n" 分隔，按 "\\n" 切分即正确；
      - errors="replace"：尾部撕裂的多字节 UTF-8 字符（写入中断时部分
        落盘）解码为替换符而非抛 UnicodeDecodeError——撕裂字符所在行
        解析失败，按上述坏行逻辑处理（默认跳过 / strict 报错），
        整个日志仍可读。
    """
    path = journal_path(repo_root, task_id)
    if not path.is_file():
        return []
    text = path.read_text(encoding="utf-8", errors="replace")
    events = []
    for lineno, line in enumerate(text.split("\n"), 1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except ValueError:
            item = None
        if not isinstance(item, dict):
            if strict:
                raise JournalError(
                    "%s 第 %d 行不是合法的事件 JSON 对象" % (path, lineno))
            continue
        events.append(item)
    return events


def tail_events(repo_root, task_id, n=20, *, event=None):
    """返回最近 n 条事件（保持文件顺序，取尾部不反转）。

    n <= 0 或文件不存在返回 []；event 给出时只统计该事件类型的行。
    """
    if n <= 0:
        return []
    events = read_events(repo_root, task_id)
    if event is not None:
        events = [item for item in events if item.get("event") == event]
    return events[-n:]


# —— 控制面 journal（v2.2 C5a，wu-22-C5a） ——

# 控制面 journal 相对布局：<repo_root>/.glm-conductor/quota/events.jsonl
# （与 watcher.json / primer.json 同层不同文件，互不越界）。控制面事件
# 无任务上下文（quota epoch 推进先于任何具体任务存在），落在 quota
# 目录而非 tasks/<伪任务>/——伪任务目录会被 discover_tasks 判为
# orphaned 噪声、污染任务枚举（C4 reviewer P2 的正解落点）。
CONTROL_PLANE_DIR_PARTS = (".glm-conductor", "quota")


def control_plane_journal_path(repo_root):
    """返回控制面 journal 路径 <repo_root>/.glm-conductor/quota/events.jsonl。"""
    return (Path(repo_root) / CONTROL_PLANE_DIR_PARTS[0]
            / CONTROL_PLANE_DIR_PARTS[1] / JOURNAL_FILENAME)


def append_control_plane_event(repo_root, event, *, ts=None):
    """向控制面 journal 追加一条事件，返回写入的完整事件 dict（含 ts）。

    与 append_event 逐字同构（同一校验、同一单行 JSON 落盘格式、同一
    ts 管理纪律），只是落点不同——本函数写
    `.glm-conductor/quota/events.jsonl`，不触碰任何任务 journal；
    append_event 的既有行为与签名零改动（两函数并存，互不委托）。
    quota_epoch_advanced 等 §13 控制面事件经本函数落盘（QC-07：每
    epoch 恰一条，落点 = 控制面）。

    校验（结构性错误抛 JournalError，且在任何 I/O 之前完成）：
      - event 必须是 dict 且含非空 str 的 "event" 键；
      - event 不得含 "ts" 键（时间戳由本函数管理，调用方不得伪造）。

    ts：缺省时自动生成 datetime.now(timezone.utc).isoformat(
        timespec="milliseconds")；也可显式传入 ISO8601 字符串
        （测试 / 回放用）。

    写入：单行 JSON（UTF-8、ensure_ascii=False、键序保持插入序，
    ts 为首键）+ "\\n"，目录不存在自动创建（parents=True）。
    I/O 异常（OSError 等）自然向上抛，不吞。
    """
    if not isinstance(event, dict):
        raise JournalError("event 必须是 dict，得到 %s" % type(event).__name__)
    name = event.get("event")
    if not isinstance(name, str) or not name:
        raise JournalError('event 必须含非空 str 的 "event" 键')
    if "ts" in event:
        raise JournalError('event 不得自带 "ts" 键（时间戳由 append_control_plane_event 管理）')

    if ts is None:
        ts = datetime.now(timezone.utc).isoformat(timespec="milliseconds")

    # 不改动调用方传入的 dict；ts 置首，其余键保持插入序
    record = {"ts": ts}
    record.update(event)

    path = control_plane_journal_path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    # newline="\n"：Windows 下也不做换行翻译，保证文件恒为 "\n" 分隔的 jsonl
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def read_control_plane_events(repo_root):
    """按文件顺序读取控制面 journal 的全部事件，返回 dict 列表。

    容错契约（**内容层**，与 read_events 的默认容错同风格）：文件不
    存在返回 []；空白行跳过；坏行（JSON 解析失败或解析结果非 dict）
    跳过——截断尾行常见于写入中断，不能让整个日志不可读。按 "\\n"
    切分行（理由同 read_events：事件值中的 U+0085 / U+2028 / U+2029
    是合法 JSON 单行内容，splitlines 会在这些字符处错误断行）；
    errors="replace" 容忍尾部撕裂的多字节字符。坏内容不构成错误
    （上述规则吞掉），但 **I/O 层异常不在容错面内**：OSError（权限
    被拒 / 路径是目录 / 目录锁等）自然上抛——调用方自行兜底。
    """
    path = control_plane_journal_path(repo_root)
    if not path.is_file():
        return []
    text = path.read_text(encoding="utf-8", errors="replace")
    events = []
    for line in text.split("\n"):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict):
            events.append(item)
    return events
