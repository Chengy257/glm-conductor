#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GLM Conductor v2.1 PostToolUse / PostToolUseFailure 钩子（M2 后半）。

职责：
    Agent|Task 工具调用的运行时生命周期记账。hooks.json 的
    PostToolUse 与 PostToolUseFailure 两条 matcher "Agent|Task" 条目
    均指向本脚本，按 stdin 载荷的 hook_event_name 分发：

      - PostToolUse（派发成功，tool_response 为 launch 确认）：两条
        记账分支——
        a) permit 分支：从 tool_input 的 prompt/description 解析 marker
           （GLM_CONDUCTOR_DISPATCH=<permit_id>，§6.4）→ 定位持有该
           permit 的活动任务 → consume_permit（原子 rename，重放机械
           拒绝）→ agent_run.record_agent_launch 记 agent_launched 事件
           （runtime-observed 生命周期，与手写 implementation_started
           互不替代）。permit 已消费/不存在 → record_launch_replay_
           skipped 幂等容错（不报错）。
        b) reviewer 分支（RB-21-02）：tool_input.subagent_type ∈
           agent_run.REVIEWER_PROFILES 且 prompt/description 解析出
           GLM_CONDUCTOR_REVIEW=<task_id> marker → agent_run.record_
           reviewer_invocation 记 reviewer_invoked 事件（reviewer 类型
           按 D1 裁定免 permit，此前零记账；该事件是 provenance.
           run_review 落 receipt 前回验调用真实性的唯一账本依据）。
           marker 指向的任务不存在 → record_reviewer_invocation_skipped
           警告事件（fail-loud 不阻断）。reviewer 类型无 review marker
           → 落回 permit 分支原逻辑（无 dispatch marker → 静默），两条
           分支互斥、permit 分支零改动。
      - PostToolUseFailure（派发失败）：invalidate_permit（作废，
        不得重复使用）+ record_agent_failure（agent_dispatch_failed，
        error 摘要自载荷提取）。

    消费时点（※DR R3）：PreToolUse 校验只读不写；本钩子在 launch
    成为事实后消费——两次重放派发时第二次在 PreToolUse 即被拒，
    本层的 replay 容错仅覆盖极端竞态。

职责二（v2.2 C6，wu-22-C6）：Cron* 工具的 scheduler 能力观测记账。
    hooks.json 的 PostToolUse / PostToolUseFailure 另各有 matcher
    "CronCreate|CronUpdate|CronDelete" 条目指向本脚本，按载荷
    tool_name ∈ CRON_TOOLS 分发（先于 Agent|Task 既有分支）：

      - PostToolUse（宿主 scheduler 工具调用成功）：tool_response
        深度有界容错提取 automationId/automation_id +
        nextRunAt/next_run_at → runtime.scheduler_facts 按载荷
        session_id 记能力证据（CronCreate → create=allowed +
        origin=interactive；CronUpdate → update=allowed；CronDelete →
        delete=allowed；automation_id 追加去重）→ 对每个活动任务调
        task_manager.observe_scheduler_context 镜像（**journal 唯一
        写点在该 API 内，本钩子绝不直接 append_event**；无活动任务
        只记 facts 零 journal——C4 伪任务教训）；
      - PostToolUseFailure：**仅 CronCreate** 且错误文本命中冻结
        关键词分类（is_nested_rejection，lowercase containment：
        "nested"，或 "schedul" 且任一限定词 forbid / not allowed /
        cannot / denied / reject）→ 记 origin=scheduled_task +
        create=forbidden（嵌套拒绝是会话 scheduler 能力的决定性
        证据）+ 任务镜像；未命中（瞬时错误：quota / 网络 / 参数）
        → 零写不升格（瞬时失败不是能力事实）；CronUpdate /
        CronDelete 失败恒零写（无嵌套语义）。

静默纪律：
    本钩子不 deny、不改写输入（PostToolUse 输出只能 additionalContext
    ——本钩子恒不输出，stdout 恒空）；无 marker / 无归属任务 /
    事件名不识别的调用一律静默放行（只读类委派与无关工具不记账）；
    Cron* 观测路径同样恒零输出（记账即静默，观测绝不阻断主流程）。

fail-open 契约（※DR D6，与 pre_tool_use 同）：
    任何内部异常（含 runtime 模块不可用、stdin 异常、permits 文件
    异常）→ stderr 一行 "ENFORCEMENT DEGRADED: post_tool_use failed:
    ..." + exit 0——记账层降级可见，绝不阻断主流程（完成面
    fail-closed 由 Stop 门承担）。

依赖：
    Python 3 标准库 + runtime.dispatch_wave / runtime.agent_run /
    runtime.state / runtime.scheduler_facts / runtime.task_manager
    （延迟 import，失败走 fail-open）。
"""

import json
import sys
from pathlib import Path

# 接线插件根以复用 runtime（钩子脚本与 runtime/ 同插件，与
# pre_tool_use 一致）
PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

# 本脚本服务的事件名（其余 hook_event_name 一律静默——防误挂到
# 别的 matcher 上时产生副作用）
SERVED_EVENTS = ("PostToolUse", "PostToolUseFailure")

# —— v2.2 C6（wu-22-C6）：Cron* 观测路径常量 ——

# 宿主 scheduler 工具词汇（hooks.json matcher 同步；载荷 tool_name
# 严格等值分发）
CRON_TOOLS = ("CronCreate", "CronUpdate", "CronDelete")

# 工具 → scheduler_context 能力维度（scheduler_facts 观察同键）
CRON_CAPABILITY_KEYS = {"CronCreate": "create", "CronUpdate": "update",
                        "CronDelete": "delete"}

# 嵌套拒绝分类的冻结关键词集（PostToolUseFailure 仅 CronCreate 分类用；
# lowercase containment）：命中规则 = 文本含主标记 "nested"，或含宿主
# 标记 "schedul" 且含任一限定词（NESTED_REJECTION_QUALIFIER_MARKERS）。
# 纪律：未命中 = 瞬时错误，零写不升格能力事实。
NESTED_REJECTION_NESTED_MARKERS = ("nested",)
NESTED_REJECTION_HOST_MARKERS = ("schedul",)
NESTED_REJECTION_QUALIFIER_MARKERS = ("forbid", "not allowed", "cannot",
                                      "denied", "reject")
NESTED_REJECTION_MARKERS = (NESTED_REJECTION_NESTED_MARKERS
                            + NESTED_REJECTION_HOST_MARKERS
                            + NESTED_REJECTION_QUALIFIER_MARKERS)

# tool_response 深度容错提取的有界参数（深度 / 节点预算双闸——宿主
# 响应形状不受本钩子控制，防御性封顶）
MAX_EXTRACTION_DEPTH = 6
MAX_EXTRACTION_NODES = 200


def repo_root():
    """返回被检查的仓库根：优先 ZCODE_PROJECT_DIR（ZCode 钩子进程注入），
    缺失时回退当前工作目录（与 pre_tool_use 同口径）。"""
    import os
    return os.environ.get("ZCODE_PROJECT_DIR") or os.getcwd()


def read_payload():
    """读取并解析 stdin 的事件载荷，返回 dict（任何输入问题按 {} 处理）。"""
    try:
        raw = sys.stdin.read()
    except Exception:
        return {}
    if not raw or not raw.strip():
        return {}
    try:
        payload = json.loads(raw)
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _marker_text(tool_input):
    """把 tool_input 中的 prompt / description 文本拼为 marker 检索面。

    tool_input 非 dict / 字段非 str 一律按空串容错；两字段都可能
    携带 marker（§6.4：主会话把它放进 prompt 或 description）。
    """
    parts = []
    for key in ("prompt", "description"):
        value = tool_input.get(key) if isinstance(tool_input, dict) else None
        if isinstance(value, str) and value:
            parts.append(value)
    return "\n".join(parts)


def _owning_task(repo, active_tasks, permit_id):
    """在活动任务中定位持有该 permit 的任务，返回 (task_id, permit)。

    先试活跃 permit（load_permit；consumed/invalidated 视同不存在）；
    未命中再按 retired 文件名（<id>.consumed.json / .invalidated.json）
    检索——replay_skipped 与「无 permit 失败记账」仍需归属任务才能
    落 journal，但 permit 返回 None（retired 不是可用许可，只提供
    归属线索）。两者皆未命中返回 (None, None)。只扫活动任务：终态
    任务的 permit 生命周期已结束，记账无意义。
    """
    from runtime import dispatch_wave

    for task_id in active_tasks:
        permit = dispatch_wave.load_permit(repo, task_id, permit_id)
        if permit is not None:
            return task_id, permit
    permits_root = dispatch_wave.permits_dir
    for task_id in active_tasks:
        directory = permits_root(repo, task_id)
        for suffix in (".consumed.json", ".invalidated.json"):
            if (directory / (permit_id + suffix)).is_file():
                return task_id, None
    return None, None


# —— v2.2 C6（wu-22-C6）：Cron* 观测路径 ——

def _bounded_find_strings(node, wanted_keys):
    """有界深度优先容错提取：收集 node 内 wanted_keys 键下的非空 str 值。

    深度闸 MAX_EXTRACTION_DEPTH + 节点预算 MAX_EXTRACTION_NODES 双闸
    （宿主响应形状不受本钩子控制——嵌套再深也止步，绝不递归失控）；
    任何形状（None / 标量 / dict / list 混套）一律容错。
    """
    found = []
    budget = [MAX_EXTRACTION_NODES]

    def walk(item, depth):
        if depth < 0 or budget[0] <= 0:
            return
        budget[0] -= 1
        if isinstance(item, dict):
            for key, value in item.items():
                if isinstance(key, str) and key in wanted_keys \
                        and isinstance(value, str) and value:
                    found.append(value)
                else:
                    walk(value, depth - 1)
        elif isinstance(item, list):
            for element in item:
                walk(element, depth - 1)

    walk(node, MAX_EXTRACTION_DEPTH)
    return found


def _extract_cron_facts(tool_response):
    """tool_response → (automation_id | None, next_run_at | None)。

    深度有界容错提取 automationId / automation_id 与 nextRunAt /
    next_run_at（两种命名形态都认，首个命中生效）；非 dict/list 形状
    → (None, None)（容错，不虚构）。
    """
    if not isinstance(tool_response, (dict, list)):
        return None, None
    automation = _bounded_find_strings(
        tool_response, ("automationId", "automation_id"))
    next_run = _bounded_find_strings(
        tool_response, ("nextRunAt", "next_run_at"))
    return (automation[0] if automation else None,
            next_run[0] if next_run else None)


def _failure_text(payload):
    """PostToolUseFailure 的错误文本分类面（str 优先，结构化容错）。

    payload.error 为非空 str → 原样；为 dict / list → 对 error /
    message 键做同款有界容错提取并按行拼接；其余 → 空串（未命中
    分类 → 零写）。
    """
    error = payload.get("error")
    if isinstance(error, str) and error:
        return error
    if isinstance(error, (dict, list)):
        return "\n".join(_bounded_find_strings(error, ("error", "message")))
    return ""


def is_nested_rejection(text):
    """NESTED_REJECTION_MARKERS 冻结分类（lowercase containment）。

    命中 = 文本含 "nested"，或含 "schedul" 且含任一限定词
    （forbid / not allowed / cannot / denied / reject）。非 str / 空
    文本 → False。瞬时错误（quota / 网络超时等）恒不命中。
    """
    if not isinstance(text, str) or text == "":
        return False
    lowered = text.lower()
    if any(marker in lowered
           for marker in NESTED_REJECTION_NESTED_MARKERS):
        return True
    return (any(marker in lowered
                for marker in NESTED_REJECTION_HOST_MARKERS)
            and any(marker in lowered
                    for marker in NESTED_REJECTION_QUALIFIER_MARKERS))


def cron_observation_path(payload, event_name, repo):
    """Cron* 工具的 scheduler 能力观测路径（v2.2 C6）。

    流程（PostToolUse 成功 / PostToolUseFailure 嵌套拒绝分类）：
      1. 提取：tool_response 深度有界容错提取 automation 身份 +
         next_run_at；session_id（无 session_id 只跳过 facts，不阻断
         任务镜像）；
      2. session facts：按 session_id 经 scheduler_facts.record_
         observation 落已证事实（证据只进不退；零网络零模型调用）；
      3. 任务镜像：对每个活动任务调 task_manager.observe_scheduler_
         context（journal 唯一写点在该 API 内——本钩子绝不直接
         append_event；无活动任务零 journal；§22.5：观测面绝不碰
         wake_bridge.status）。

    PostToolUseFailure 只对 CronCreate 分类（嵌套创建被拒才升格
    origin=scheduled_task + create=forbidden）；未命中冻结关键词 /
    CronUpdate / CronDelete 失败 → 零写。单任务镜像失败 → stderr
    降级一行 + 继续其余任务（fail-open 纪律，降级可见不阻断）。
    恒 return 0、恒零 stdout。
    """
    from runtime import scheduler_facts, state, task_manager

    tool_name = payload.get("tool_name")
    capability_key = CRON_CAPABILITY_KEYS.get(tool_name)
    automation_id, _next_run_at = _extract_cron_facts(
        payload.get("tool_response"))
    session_id = payload.get("session_id")
    session_ok = isinstance(session_id, str) and session_id != ""

    if event_name == "PostToolUse":
        # 成功 = 能力证据（CronCreate 成功同时证明交互会话身份）
        origin = "interactive" if tool_name == "CronCreate" else None
        facts_obs = {"origin": origin, capability_key: "allowed"}
        mirror = {"origin": origin, capability_key: "allowed"}
        if automation_id is not None:
            facts_obs["automation_id"] = automation_id
            mirror["parent_automation_id"] = automation_id
        if session_ok:
            try:
                scheduler_facts.record_observation(repo, session_id,
                                                   **facts_obs)
            except Exception as exc:  # noqa: BLE001 —— 降级可见不阻断
                sys.stderr.write(
                    "ENFORCEMENT DEGRADED: post_tool_use failed: %r\n"
                    % (exc,))
    else:
        # 失败路径：仅 CronCreate 且命中冻结嵌套拒绝分类才升格
        if tool_name != "CronCreate":
            return 0
        if not is_nested_rejection(_failure_text(payload)):
            return 0  # 瞬时错误零写不升格
        facts_obs = {"origin": "scheduled_task", "create": "forbidden"}
        mirror = {"origin": "scheduled_task", "create": "forbidden"}
        if session_ok:
            try:
                scheduler_facts.record_observation(repo, session_id,
                                                   **facts_obs)
            except Exception as exc:  # noqa: BLE001 —— 降级可见不阻断
                sys.stderr.write(
                    "ENFORCEMENT DEGRADED: post_tool_use failed: %r\n"
                    % (exc,))

    if session_ok:
        mirror["source_session"] = session_id
    for task_id in state.find_active_tasks(repo):
        try:
            task_manager.observe_scheduler_context(repo, task_id, **mirror)
        except Exception as exc:  # noqa: BLE001 —— 单任务失败不阻断其余
            sys.stderr.write(
                "ENFORCEMENT DEGRADED: post_tool_use failed: %r\n" % (exc,))
    return 0


def main():
    """主流程：读 stdin → 按 hook_event_name + tool_name 分发记账路径。

    - 非 PostToolUse / PostToolUseFailure → 静默（exit 0）；
    - tool_name ∈ CRON_TOOLS → C6 scheduler 能力观测路径
      （cron_observation_path：session facts + 活动任务镜像；
      PostToolUseFailure 仅 CronCreate 嵌套拒绝分类记账）；
    - PostToolUse：reviewer 分支先行（subagent_type ∈
      REVIEWER_PROFILES + GLM_CONDUCTOR_REVIEW=<task_id> marker →
      reviewer_invoked 记账后返回）；无 marker / 非 reviewer 类型落回
      既有 permit 分支；
    - 无 marker / 无归属活动任务 → 静默（无 permit 的 Agent 调用
      是只读类或与 conductor 无关，不记账）；
    - PostToolUse → consume + agent_launched（permit 缺失时
      replay_skipped 容错）；
    - PostToolUseFailure → invalidate + agent_dispatch_failed
      （permit 缺失时仅记失败事件）。
    """
    from runtime import agent_run, dispatch_wave, state

    payload = read_payload()
    event_name = payload.get("hook_event_name")
    if not isinstance(event_name, str):
        event_name = payload.get("hookEventName")
    if event_name not in SERVED_EVENTS:
        return 0

    repo = repo_root()
    tool_name = payload.get("tool_name")
    if isinstance(tool_name, str) and tool_name in CRON_TOOLS:
        # C6 scheduler 观测路径先于 Agent|Task 既有分支（Cron* 载荷
        # 没有 marker / subagent_type 语义，落回旧分支只会静默）
        return cron_observation_path(payload, event_name, repo)
    tool_input = payload.get("tool_input")

    # RB-21-02 reviewer 分支（PostToolUse 专属；失败路径不记账——
    # reviewer_invoked 只证明「调用真实发生过」）：reviewer 类型 +
    # review marker → 落账后返回；无 marker 落回 permit 分支原逻辑
    # （reviewer 免 permit → 无 dispatch marker → 静默，行为不回退）
    if event_name == "PostToolUse":
        subagent_type = (tool_input.get("subagent_type")
                         if isinstance(tool_input, dict) else None)
        if isinstance(subagent_type, str) \
                and subagent_type in agent_run.REVIEWER_PROFILES:
            review_task_id = agent_run.parse_review_marker(
                _marker_text(tool_input))
            if review_task_id is not None:
                if state.load_state(repo, review_task_id) is None:
                    # fail-loud 不阻断：marker 指向的任务不存在 →
                    # 警告记账（该 task_id 下无 receipt 可申报）
                    agent_run.record_reviewer_invocation_skipped(
                        repo, review_task_id, payload,
                        reason="task_missing")
                else:
                    agent_run.record_reviewer_invocation(
                        repo, review_task_id, payload)
                return 0

    permit_id = dispatch_wave.parse_marker(
        _marker_text(tool_input))
    if permit_id is None:
        return 0

    task_id, permit = _owning_task(repo, state.find_active_tasks(repo),
                                   permit_id)
    if task_id is None:
        return 0

    if event_name == "PostToolUse":
        if permit is None:
            # 极端竞态：PreToolUse 放行后、PostToolUse 前被并发消费/
            # 过期清理——launch 事实仍在，容错记账不报错
            agent_run.record_launch_replay_skipped(
                repo, task_id, payload, permit_id=permit_id)
            return 0
        dispatch_wave.consume_permit(repo, task_id, permit_id)
        agent_run.record_agent_launch(repo, task_id, payload, permit=permit)
        return 0

    # PostToolUseFailure：作废 permit（不得重复使用）+ 失败记账；
    # permit 已不在（过期/已消费）则只记失败事件
    if permit is not None:
        dispatch_wave.invalidate_permit(repo, task_id, permit_id)
    agent_run.record_agent_failure(repo, task_id, payload, permit=permit)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as exc:  # fail-open：记账层降级可见，绝不阻断
        sys.stderr.write(
            "ENFORCEMENT DEGRADED: post_tool_use failed: %r\n" % (exc,))
        sys.exit(0)
