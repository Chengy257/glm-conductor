#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GLM Conductor v2 任务执行日志层（events.jsonl）。

职责：
    管理 v2 任务的本地执行日志 `.glm-conductor/tasks/<task-id>/events.jsonl`：
    append-only 追加、容错读取、尾部查询。用途是本地执行溯源
    （调试 / 恢复 / 审计），不是遥测
    （docs/glm-conductor-v2-upgrade-guide-final.md §54-§56）。

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
    "verification",        # 主会话验证（含 command/status）
    "review",              # 审查者裁决（含 verdict）
    "checkpoint_written",  # checkpoint 落盘
    "status_changed",      # 任务状态迁移（transition_task_status 记录）
    "gate_blocked",        # Stop 完成门拦截
    "gate_passed",         # 完成门校验通过放行（断链 + 审计）
    "gate_exhausted",      # 完成门连续 block 达运行时上限后放行
    "gate_degraded",       # git 不可用等降级跳过校验
    "completed",
    "cancelled",
    "failed",
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
