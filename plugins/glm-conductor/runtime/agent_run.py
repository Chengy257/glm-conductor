#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GLM Conductor v2.1 Agent Run 记账层（M2 后半 wu-21-03 + M3 前半账本）。

职责：
    一、journal 写入口（M2 后半，wu-21-03）——PostToolUse /
    PostToolUseFailure 钩子的唯一 journal 写入口——把「Agent tool
    调用真实发生」这一运行时观察到的生命周期事实落账：
      - record_agent_launch：派发成功（tool_response 为 launch 确认）
        → 记 agent_launched 事件（unit / permit_id / tool_use_id /
        agent_id / execution_mode）；permit 消费由调用方（hook）先行
        完成，本函数只记账，不触碰 permits 文件；
      - record_agent_failure：派发失败 → agent_dispatch_failed 事件
        （error 摘要自载荷提取或显式给定，截断 200 字符）；
      - record_launch_replay_skipped：permit 已消费/不存在时的幂等
        容错记账（agent_launch_replay_skipped 事件）。

    二、纯读账本面（M3 前半，计划 §7.1/§7.2）——把 launch 事实提升
    为可机械查询的运行账本，建立 Work Unit ↔ Dispatch Permit ↔
    Tool Use ↔ Native Agent Run ↔ Child Session 的稳定映射读取面
    （全部零写副作用；journal 事件是账本唯一真相源，本模块不引入
    第二存储、不复制任何 transcript）：
      - list_agent_runs：journal 聚合 run 记录（agent_launched →
        failed=False，agent_dispatch_failed → failed=True，
        agent_launch_replay_skipped 不产生 run）；
      - native_agent_metadata：原生档案
        ~/.zcode/cli/agents/<主会话id>/<agentId>/metadata.json 的只读
        adapter（agents_root 可参数化；白名单字段 camelCase 归一
        snake_case、未知字段丢弃；缺失/损坏/JSON 非 dict 一律优雅
        降级为 None，绝不抛）；
      - run_lifecycle：单元视角汇总（launch/失败计数 + 最后已知
        agent_id 的档案观察 + 僵尸语义标注）。

    与手写 implementation_started 事件（continuity 事件表）的关系：
    两者是不同事件且互不替代——implementation_started 表示「主会话
    走了 commit_dispatch 事务」（可能被手工绕过/代写），agent_launched
    表示「Agent tool 调用的返回被 runtime（PostToolUse 钩子）亲眼观察
    到」（钩子由宿主在工具调用点强制触发，不可绕过）。

僵尸语义（计划 §7.3 冻结）：
    原生档案 metadata.status 绝不是 truth——真实 dogfood 已发现主会话
    异常退出后 status 可能永久停在 running。因此 run_lifecycle 不猜测
    存活，只做互斥二标注：observed_status == "running" →
    possibly_zombie=True（不可信僵尸态）；observed_status ∈
    {completed, failed, stopped} → archived_terminal=True（档案可证的
    终态）；档案取不到（native=None）或状态不在两集合内 → 二者皆
    False。是否真活着的裁决归后续 reconcile 单元（证据优先级：
    repo 状态 > 验证证据 > transcript > 档案 metadata > checkpoint）。

agent_id 提取（§7.4 / §2.3-H2）：
    后台派发的 tool_response 为 launch 确认，可能含 agentId（驼峰）/
    agent_id（下划线）/task_id 等句柄键——形状自适应逐键尝试（含
    浅层嵌套 result 块），取不到为 null，绝不因 tool_response 形状
    异常抛错。§22.1 红线：不依赖 ZCode 内部 SQLite schema，此处只
    消费钩子载荷已公开的信息。

依赖：
    仅 Python 3 标准库（json / pathlib）+ runtime.journal（append-only
    唯一写入口）。写入口不读写 state / permits 文件——permit 消费归
    hook 流程，state 变更归 task_manager；读取面纯读（只 glob 档案 +
    读 journal），被 hook、CLI 与测试直接调用。
"""

import json
from pathlib import Path

from runtime import journal

# agent_id 提取的候选键（驼峰 / 下划线双命名 + 通用 task 句柄；
# 顺序即优先级）
_AGENT_ID_KEYS = ("agentId", "agent_id", "task_id", "taskId")

# error 摘要提取的候选键（PostToolUseFailure 载荷）
_ERROR_KEYS = ("error", "message")

# error 摘要截断长度（journal 事件不倾倒长文本）
_MAX_ERROR_CHARS = 200

# —— 以下为 M3 前半纯读账本面的冻结词汇（§7.2/§7.3） ——

# 原生档案缺省根（dogfood 实测冻结布局；测试经 agents_root 参数注入
# tempfile 伪造树，绝不读写真实 ~/.zcode）
DEFAULT_AGENTS_ROOT_PARTS = (".zcode", "cli", "agents")

# 原生档案白名单：camelCase → snake_case 归一表（顺序即输出键序；
# 表外字段一律丢弃——不倾倒档案，§22.1 只读 adapter 红线）。tokens
# 用量块（档案 "usage" dict）单独原样透传，不进本表。
_METADATA_FIELD_MAP = (
    ("agentId", "agent_id"),
    ("childSessionId", "child_session_id"),
    ("parentSessionId", "parent_session_id"),
    ("parentToolUseId", "parent_tool_use_id"),
    ("status", "observed_status"),
)

# 档案可证的终态集合（§7.3：只有这三个值算 archived_terminal）
_TERMINAL_STATUSES = ("completed", "failed", "stopped")

# 僵尸态标记（§7.3：主会话崩溃后 metadata.status 可能永久 running——
# 绝不解读为存活，只作 possibly_zombie 标注）
_ZOMBIE_STATUS = "running"


def _extract_agent_id(tool_response):
    """从 tool_response 形状自适应提取 agent 句柄；取不到返回 None。

    顶层逐键尝试 _AGENT_ID_KEYS；未命中再浅探一层嵌套 dict（常见
    {"result": {...}} 形态）。tool_response 非 dict / 值非非空 str
    一律跳过——本函数是观察辅助，永不抛错。
    """
    if not isinstance(tool_response, dict):
        return None
    for key in _AGENT_ID_KEYS:
        value = tool_response.get(key)
        if isinstance(value, str) and value:
            return value
    inner = tool_response.get("result")
    if isinstance(inner, dict):
        for key in _AGENT_ID_KEYS:
            value = inner.get(key)
            if isinstance(value, str) and value:
                return value
    return None


def _tool_use_id(payload):
    """从钩子载荷提取 tool_use_id（snake/camel 双命名容错），缺失 None。"""
    if not isinstance(payload, dict):
        return None
    for key in ("tool_use_id", "toolUseId"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _coerce_error_text(value):
    """把任意 error 值压成非空字符串并截断到 _MAX_ERROR_CHARS。"""
    if not isinstance(value, str):
        value = "unknown" if value is None else str(value)
    if value == "":
        value = "unknown"
    return value[:_MAX_ERROR_CHARS]


def record_agent_launch(repo_root, task_id, payload, *, permit) -> dict:
    """记 agent_launched 事件（PostToolUse 成功路径），返回事件 dict。

    permit 为 dispatch_wave.load_permit 的 dict（消费前后均可——本
    函数只读其字段）；payload 为钩子 stdin dict。字段形状冻结：
    {"event": "agent_launched", "unit": ..., "permit_id": ...,
     "tool_use_id": ..., "agent_id": ...（可 null）, "execution_mode": ...}
    """
    return journal.append_event(repo_root, task_id, {
        "event": "agent_launched",
        "unit": permit.get("unit_id") if isinstance(permit, dict) else None,
        "permit_id": permit.get("permit_id") if isinstance(permit, dict)
        else None,
        "tool_use_id": _tool_use_id(payload),
        "agent_id": _extract_agent_id(
            payload.get("tool_response")
            if isinstance(payload, dict) else None),
        "execution_mode": permit.get("mode") if isinstance(permit, dict)
        else None,
    })


def record_agent_failure(repo_root, task_id, payload, *, permit,
                         error=None) -> dict:
    """记 agent_dispatch_failed 事件（PostToolUseFailure 路径）。

    error 显式给定优先；否则从 payload 的 error / message 字段提取
    （值可为任意 JSON 类型，压成字符串）；再兜底 "unknown"；一律截断
    200 字符。permit 可为 dict 或 None（permit 不存在时仍记账失败，
    unit/permit_id 记 null）。
    """
    if error is None and isinstance(payload, dict):
        for key in _ERROR_KEYS:
            if key in payload:
                error = payload.get(key)
                break
    return journal.append_event(repo_root, task_id, {
        "event": "agent_dispatch_failed",
        "unit": permit.get("unit_id") if isinstance(permit, dict) else None,
        "permit_id": permit.get("permit_id") if isinstance(permit, dict)
        else None,
        "tool_use_id": _tool_use_id(payload),
        "error": _coerce_error_text(error),
    })


def record_launch_replay_skipped(repo_root, task_id, payload,
                                 *, permit_id) -> dict:
    """记 agent_launch_replay_skipped 事件（permit 已消费/不存在时
    PostToolUse 的幂等容错记账——launch 事实仍在，只是 permit 无法
    再次消费；不报错、不阻断）。"""
    return journal.append_event(repo_root, task_id, {
        "event": "agent_launch_replay_skipped",
        "tool_use_id": _tool_use_id(payload),
        "permit_id": permit_id,
    })


# —— M3 前半：纯读账本面（零写副作用，只读 journal + 只 glob 档案） ——

def list_agent_runs(repo_root, task_id) -> list:
    """把任务 journal 聚合为 agent run 账本（按文件时间序），纯读。

    事件归类（冻结）：
      - agent_launched → 一条 run（failed=False，unit/permit_id/
        tool_use_id/agent_id/execution_mode 自事件逐键拷贝）；
      - agent_dispatch_failed → 一条 run（failed=True；该事件本无
        agent_id / execution_mode 字段 → None）；
      - agent_launch_replay_skipped → 不产生 run（launch 事实已由
        成功或失败路径记账，重放只是幂等容错标记）；
      - 其余事件（implementation_started 等）与未知事件一律跳过。

    run 记录键形状冻结（§7.2 handle，八键）：
      {"run_id": tool_use_id 或递增序号, "unit": ..., "permit_id": ...,
       "tool_use_id": ..., "agent_id": ..., "execution_mode": ...,
       "failed": bool, "ts": ...}
    run_id：tool_use_id 非空 str 时直接用它（§7.4 稳定 primary key）；
    否则用该 run 在返回列表中的 1 起始序号（int）。缺字段容错为
    None（聚合器对坏行之外再容错一层）。文件不存在 → []。
    """
    runs = []
    for event in journal.read_events(repo_root, task_id):
        name = event.get("event")
        if name == "agent_launched":
            failed = False
        elif name == "agent_dispatch_failed":
            failed = True
        else:
            continue
        tool_use_id = event.get("tool_use_id")
        run_id = (tool_use_id
                  if isinstance(tool_use_id, str) and tool_use_id
                  else len(runs) + 1)
        runs.append({
            "run_id": run_id,
            "unit": event.get("unit"),
            "permit_id": event.get("permit_id"),
            "tool_use_id": tool_use_id,
            "agent_id": event.get("agent_id"),
            "execution_mode": event.get("execution_mode"),
            "failed": failed,
            "ts": event.get("ts"),
        })
    return runs


def native_agent_metadata(agent_id, *, agents_root=None):
    """读取原生 agent 档案（只读 adapter），白名单归一后返回 dict。

    档案布局（dogfood 实测冻结）：
        <agents_root>/<主会话id>/<agentId>/metadata.json
    不依赖主会话 id：在 agents_root 下逐会话目录查找
    <agentId>/metadata.json 首个命中读取（会话目录按名排序，确定性；
    用精确子目录名匹配而非 glob 通配——agent_id 里的 glob 元字符不会
    被解释成模式，也不受主会话 id 目录影响）。

    agents_root：缺省 Path.home()/".zcode"/"cli"/"agents"；测试注入
    tempfile 伪造树，绝不读写真实 ~/.zcode。

    返回白名单六键（camelCase 归一 snake_case；observed_status 为
    档案 status 原值，不解读）：
      {"agent_id", "child_session_id", "parent_session_id",
       "parent_tool_use_id", "observed_status", "tokens"}
    tokens：档案 "usage" 用量块原样透传（dict 时），缺/非 dict →
    None；表外字段（prompt / transcriptFile / profileSnapshot 等）
    一律丢弃——不倾倒档案、绝不复制 transcript（§7.2）。

    agent_id 非非空 str、含路径分隔符或为 "."/".."（路径逃逸防护，
    口径同 dispatch_wave permit_id 闸）→ None；档案缺失 / JSON 损坏 /
    解析结果非 dict → None。任何路径都不抛错。
    """
    if not isinstance(agent_id, str) or agent_id == "":
        return None
    if agent_id in (".", "..") \
            or any(ch in agent_id for ch in ("/", "\\", "\x00")):
        return None
    root = Path(agents_root) if agents_root is not None \
        else Path.home().joinpath(*DEFAULT_AGENTS_ROOT_PARTS)
    if not root.is_dir():
        return None
    try:
        session_dirs = sorted(
            entry for entry in root.iterdir() if entry.is_dir())
    except OSError:
        return None
    for session_dir in session_dirs:
        metadata_path = session_dir / agent_id / "metadata.json"
        if not metadata_path.is_file():
            continue
        try:
            with open(str(metadata_path), "r", encoding="utf-8") as fh:
                raw = json.load(fh)
        except (OSError, ValueError):  # ValueError 含 JSON/解码错误
            return None
        if not isinstance(raw, dict):
            return None
        result = {}
        for camel, snake in _METADATA_FIELD_MAP:
            result[snake] = raw.get(camel)
        usage = raw.get("usage")
        result["tokens"] = usage if isinstance(usage, dict) else None
        return result
    return None


def run_lifecycle(repo_root, task_id, uid) -> dict:
    """单元视角的 agent run 生命周期汇总（纯读；僵尸语义 §7.3 冻结）。

    返回键形状冻结（八字）：
      {"unit": uid, "runs": <list_agent_runs 过滤 unit==uid>,
       "launch_count": <failed=False 的 run 数>,
       "failure_count": <failed=True 的 run 数>,
       "last_agent_id": <最后一个非空 agent_id，倒序扫描；全缺失 None>,
       "native": <last_agent_id 的 native_agent_metadata 结果或 None>,
       "possibly_zombie": bool, "archived_terminal": bool}
    last_agent_id 倒序扫描非空值：最后一次派发的 agent_id 提取失败
    （工具返回形状异常）时回退到更早的已知档案句柄——账本目的是
    「最后已知的 agent run」观察，而非机械取末条的字段值。

    僵尸语义（不猜测存活，二标注互斥）：
      - native 取不到（last_agent_id 缺失 / 档案缺失/损坏）→
        possibly_zombie=False 且 archived_terminal=False；
      - observed_status == "running" → possibly_zombie=True（§7.3：
        主会话崩溃后可能永久 running，绝不解读为存活）；
      - observed_status ∈ {completed, failed, stopped} →
        archived_terminal=True（档案可证终态）；
      - 其余值（缺失/未知）→ 二者皆 False。
    真相裁决归后续 reconcile 单元（repo 状态 > 验证证据 > transcript
    > 档案 metadata）。
    """
    runs = [run for run in list_agent_runs(repo_root, task_id)
            if run.get("unit") == uid]
    last_agent_id = None
    for run in reversed(runs):
        agent_id = run.get("agent_id")
        if isinstance(agent_id, str) and agent_id:
            last_agent_id = agent_id
            break
    native = None
    if last_agent_id is not None:
        native = native_agent_metadata(last_agent_id)
    observed = (native.get("observed_status")
                if isinstance(native, dict) else None)
    return {
        "unit": uid,
        "runs": runs,
        "launch_count": sum(1 for run in runs if not run["failed"]),
        "failure_count": sum(1 for run in runs if run["failed"]),
        "last_agent_id": last_agent_id,
        "native": native,
        "possibly_zombie": observed == _ZOMBIE_STATUS,
        "archived_terminal": observed in _TERMINAL_STATUSES,
    }
