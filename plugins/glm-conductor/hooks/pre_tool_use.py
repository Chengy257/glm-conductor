#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GLM Conductor v2 PreToolUse 钩子——双职责（Layer B 注入 + Bash 策略门控）。

职责一（advisory，提示级）：Ownership Gate Layer B 派发时注入。
    以 ZCode 插件钩子（hooks/hooks.json 声明，matcher "Agent|Task"）挂在
    PreToolUse 事件上，在主会话每次派发子代理（Agent / Task 工具）前，
    查询 runtime.state 中声明了 ownership.files（非空）的活动任务，并把
    ownership 契约提醒以 additionalContext 注入派发上下文，使实施者在
    开工前再次看到自己的文件边界。

职责二（policy，ask/deny）：主会话 Bash 策略门控（B7.2）。
    hooks/hooks.json 另声明 matcher "Bash" 的同脚本条目：tool_name 为
    "Bash" 时走 runtime.policy 策略引擎（§57-§59 Route-aware Permission
    Policy）——无活动任务零干预；活动任务期间 deny 规则恒拒、ask 规则
    仅在任一活动任务 route.assurance == "high" 时升级为 ask。ask / deny
    向 stdout 输出单行 JSON permissionDecision（含 permissionDecision-
    Reason，英文，含 rule 名与截断 60 字符的命令片段）；allow 路径完全
    静默（stdout 恒空）。本层只覆盖 Bash 主会话调用；角色级 deny
    （reviewer 等）由 agent 工具白名单负责，不在本钩子。

职责三（deny，v2.1 M2 dispatch permit 门，wu-21-03）：Agent|Task 路径
    按 ※DR D1 分级升级——活动任务存在且 tool_input.subagent_type 是
    实施者类型（IMPLEMENTATION_EXECUTORS）时，必须携带有效 dispatch
    marker（GLM_CONDUCTOR_DISPATCH=<permit_id>，§6.4）且 permit 通过
    runtime.dispatch_wave.validate_permit（存在/未消费/未过期/任务匹配）
    才放行；background permit 经 updatedInput 全量改写强制后台（§6.5）。
    无 marker / permit 无效 → permissionDecision deny（可行动英文报文）。
    v2.1 M4（wu-21-08）追加 wave 成员资格环：wave permit（permit 带
    wave_id）须指向 active wave 且 unit 仍在成员清单内，否则 deny
    （报文含 wave_id 与 re-prepare wave 指引）；单单元 permit 零影响。
    非实施者类型（只读类：Explore / reviewer 等）保留既有 ownership
    advisory 注入路径原样（D1 裁定落定，wu-21-13：reviewer 最终维持
    permit 豁免，审查溯源由 review receipt 绑定承担——run_review +
    Stop 完成门 receipt 检查）；无活动任务时零干预（v2.0.1 行为不
    回退）。

职责四（deny，v2.2 C6 scheduler 嵌套创建 fail-fast，wu-22-C6）：
    tool_name == "CronCreate" 的嵌套创建门。按载荷 session_id 读
    runtime.scheduler_facts 会话事实缓存（hook PostToolUse Cron* 成功
    路径与 PostToolUseFailure 嵌套拒绝分类路径写入的**已证事实**）：
    create==forbidden 或 origin==scheduled_task 任一在案 → deny（可
    行动英文报文，Phase 0 #13 硬门：被 Scheduled Task 触发 / 拥有的
    会话禁止再创建 automation）；无证据（无 session_id / 无记录 /
    记录无禁止事实）→ 静默放行——单探针纪律：第一次尝试必须被允许
    才能产生证据，门绝不预判。本门只读事实缓存，零写零事件。

与 Layer A 的分工：
    - Layer B（本钩子）：提示级注入，只能「提高合规率」，无法确定性约束
      子代理行为——advisory only，绝不 deny、绝不阻断派发；
    - Layer A（hooks/stop_gate.py）：Stop 完成门，对最终 diff 做
      touched ⊆ owned 的确定性校验，越界改动无法静默通过完成门。
    B 提高合规、A 兜底强制，两层互补（指南 §9）。

分发与兼容选择：
    按 payload 的 tool_name 分发：tool_name 严格等于 "Bash" → 策略门控
    路径；tool_name 严格等于 "CronCreate" → 嵌套创建 fail-fast 门
    （v2.2 C6 职责四）；其余（Agent / Task，以及 tool_name 缺失 /
    stdin 空 / 非 JSON 的 payload {}）一律走既有注入路径——tool_name
    缺失按现状走注入路径是显式兼容选择（B7.2 之前 payload 从未被消费，
    保持该场景行为逐字节不变）。

fail-open 契约（两条路径共同遵守）：
    本钩子主体属 advisory / 策略层，全路径 fail-open：任何内部异常（含
    runtime.state / runtime.policy 不可用、stdin 异常）都不得影响工具
    调用放行——一律向 stderr 输出一行
    "ENFORCEMENT DEGRADED: pre_tool_use failed: ..." 后 exit 0 放行。
    stdout 只在成功注入 / ask / deny 路径写一行 JSON（ZCode 对钩子
    stdout 做 Zod 严格校验，只接受 hookSpecificOutput 标准形态，绝不
    夹带多余顶层键），其余任何路径（含策略 allow）stdout 恒为空。

来源：
    docs/history/v2.0/glm-conductor-v2-upgrade-guide-final.md §9
    （Ownership Gate —— Layer B: dispatch-time injection）+ 实施计划
    B2.3；§57-§59（Route-aware Permission Policy）+ 实施计划 B7.1/B7.2。
"""

import json
import os
import sys
from pathlib import Path

# 接线插件根以复用 runtime.state（钩子脚本与 runtime/ 同插件，与 stop_gate 一致）
PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

# 注入提醒最多列出的任务数（超出部分注明省略，避免提醒文本膨胀）
MAX_INJECTED_TASKS = 3

# Bash 策略 ask/deny 决策理由中命令片段的截断长度（控制决策文本体积）
MAX_POLICY_SNIPPET = 60

# dispatch permit 义务的实施者类型词汇（※DR D1 分级，v2.1 冻结）：
# bare 与 "glm-conductor:" 前缀两种命名形态都认（宿主子代理类型命名
# 兼容双形态）。只读类（Explore / reviewer / general-purpose 等）不在
# 此集合——走 advisory 注入路径。D1 裁定已落定（wu-21-13，2026-08-31
# ）：reviewer 类型（glm-reviewer / visual-reviewer）最终维持 permit
# 豁免——审查溯源由 receipt 绑定承担（runtime.provenance.run_review
# + Stop 完成门 fresh ship receipt 检查）：permit 证明的是「派发被授
# 权」，receipt 证明的是「审查被实际执行且绑定终态指纹」，后者才是
# M6 要的增量；本集合自此冻结不变。
IMPLEMENTATION_EXECUTORS = frozenset((
    "flash-implementer", "visual-implementer",
    "glm-conductor:flash-implementer", "glm-conductor:visual-implementer",
))

# permit 门 allow 路径的 join 提醒（advisory，§1.4 background ≠
# fire-and-forget：background worker 必须最终 join/验证/收尾）
JOIN_REMINDER = ("GLM CONDUCTOR: worker dispatched; it MUST be joined "
                 "(collect result, verify, finish_unit).")


def repo_root():
    """返回被检查的仓库根：优先 ZCODE_PROJECT_DIR（ZCode 钩子进程注入，
    Phase 0 实测），缺失时回退当前工作目录。"""
    return os.environ.get("ZCODE_PROJECT_DIR") or os.getcwd()


def read_payload():
    """读取并解析 stdin 的 PreToolUse 事件输入，返回 dict（任何输入问题按 {} 处理）。

    输入为 Claude 兼容 JSON（含 session_id / tool_name / tool_input 等字段），
    可能为空串或非 JSON。注入路径的决策只依赖活动任务状态（payload 仅
    为事件形态兼容而读取）；策略路径消费 tool_name / tool_input。因此
    空 / 非法 JSON / 非 dict 一律容错为空对象，不构成降级理由。
    """
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


def declared_ownership_tasks(repo_root):
    """返回 [(task_id, [pattern, ...])]：声明了非空 ownership.files 的活动任务。

    以 state.find_active_tasks() 为活动任务的确定性来源（终态任务不返回），
    再逐个 load_state 读取 ownership.files；未声明（键缺失 / 空数组 /
    结构异常 / 非字符串项）的任务跳过——advisory 层宁可少注入也不误报。
    顺序沿用 find_active_tasks 的目录名排序。
    """
    # import 放在函数内（模块顶部 sys.path 接线已就绪），
    # import 失败会被外层 fail-open 捕获
    from runtime import state

    pairs = []
    for task_id in state.find_active_tasks(repo_root):
        try:
            loaded = state.load_state(repo_root, task_id)
        except (ValueError, OSError):
            # 状态文件在扫描与读取之间损坏 / 被移除 → 跳过该任务
            continue
        if not isinstance(loaded, dict):
            continue
        ownership = loaded.get("ownership")
        files = ownership.get("files") if isinstance(ownership, dict) else None
        # 仅保留非空字符串 pattern（脏数据防御），过滤后为空视同未声明
        patterns = [item for item in files
                    if isinstance(item, str) and item] \
            if isinstance(files, list) else []
        if not patterns:
            continue
        pairs.append((task_id, patterns))
    return pairs


def build_reminder(ownership_pairs):
    """把 [(task_id, patterns)] 渲染为注入提醒文本（英文，逐任务一段）。

    超过 MAX_INJECTED_TASKS 个任务时只列前 MAX_INJECTED_TASKS 个
    （目录名序），并追加一行省略说明；末尾两行固定（派发提示必须约束
    实施者到这些路径 + Layer A 完成门兜底警告）。
    """
    lines = ["GLM CONDUCTOR OWNERSHIP REMINDER (Layer B):"]
    shown = ownership_pairs[:MAX_INJECTED_TASKS]
    omitted = len(ownership_pairs) - len(shown)
    for task_id, patterns in shown:
        # 每个活动任务独立一段（空行分隔），段内逐条列出 ownership 路径
        lines.append("")
        lines.append("active task %s declares ownership:" % task_id)
        for pattern in patterns:
            lines.append("- %s" % pattern)
    if omitted > 0:
        lines.append("")
        lines.append("(%d more active task(s) with declared ownership omitted)"
                     % omitted)
    lines.append("")
    lines.append(
        "Dispatch prompts MUST constrain the implementer to these paths.")
    lines.append(
        "Out-of-scope changes will block task completion (Stop gate Layer A).")
    return "\n".join(lines)


# —— 职责二：主会话 Bash 策略门控（B7.2，runtime.policy 引擎接线） ——

def any_assurance_high(repo, task_ids):
    """任一活动任务 route.assurance == "high" → True，否则 False。

    逐个 load_state 读取 route；load 失败（状态文件在扫描与读取之间
    损坏 / 被移除等）→ 跳过该任务（策略层宁可少升级也不降级放行判断）。
    """
    from runtime import state

    for task_id in task_ids:
        try:
            loaded = state.load_state(repo, task_id)
        except (ValueError, OSError):
            continue
        if not isinstance(loaded, dict):
            continue
        route = loaded.get("route")
        if isinstance(route, dict) and route.get("assurance") == "high":
            return True
    return False


def bash_policy_gate(payload):
    """Bash 策略门控路径：allow → 完全静默；ask / deny → 单行 JSON 决策。

    command 取 tool_input.command（tool_input 非 dict 容错为 None）；
    活动任务以 state.find_active_tasks 为准，assurance_high 取任一活动
    任务 route.assurance == "high"。决策经 runtime.policy.evaluate：
      - allow → 不写任何输出（stdout 恒空，零干预）；
      - ask / deny → stdout 单行 JSON hookSpecificOutput
        （PreToolUse / permissionDecision + permissionDecisionReason，
        英文，含 rule 名与截断 MAX_POLICY_SNIPPET 字符的命令片段）。
    任何路径 return 0（放行交给 ZCode 按 permissionDecision 处理）；
    内部异常由模块入口的 fail-open 兜底（绝不阻断）。
    """
    from runtime import policy, state

    # 1) 提取命令（容错：tool_input 缺失 / 非 dict → None）
    tool_input = payload.get("tool_input")
    command = tool_input.get("command") \
        if isinstance(tool_input, dict) else None

    # 2) 活动任务与 assurance 评级（终态任务不算活动任务）
    repo = repo_root()
    active = state.find_active_tasks(repo)

    # 3) 策略评估（门控顺序见 runtime.policy.evaluate docstring）
    verdict = policy.evaluate(
        tool="Bash", command=command,
        active_task=bool(active),
        assurance_high=any_assurance_high(repo, active))
    if verdict["decision"] == "allow":
        return 0  # allow 路径零输出（stdout 纪律）

    # 4) ask / deny：唯一 stdout 写点（ensure_ascii=True 的单行 JSON，
    #    形态与 Zod 严格校验一致，无多余顶层键）
    snippet = command[:MAX_POLICY_SNIPPET] \
        if isinstance(command, str) else ""
    reason = "GLM CONDUCTOR POLICY: %s (%s). command: %s" % (
        verdict["decision"], verdict["rule"], snippet)
    output = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": verdict["decision"],
            "permissionDecisionReason": reason,
        }
    }
    sys.stdout.write(json.dumps(output) + "\n")
    return 0


# —— 职责三：Agent|Task dispatch permit 门（v2.1 M2，※DR D1 分级） ——

def _deny_dispatch(reason):
    """输出 permit 门的 deny 决策（英文可行动报文）并返回 0。

    报文冻结格式（§6.4）：指明拦截原因 + 如何获得 permit + marker
    写法——主会话照报文即可自救（prepare_dispatch → marker 入 prompt）。
    """
    text = ("Agent dispatch blocked: %s. Plan/prepare a dispatch "
            "(task_manager.prepare_dispatch) first and include the marker "
            "GLM_CONDUCTOR_DISPATCH=<permit_id> in the prompt." % reason)
    output = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": text,
        }
    }
    sys.stdout.write(json.dumps(output) + "\n")
    return 0


def _deny_wave(reason, wave_id):
    """输出 wave 成员资格 deny 决策（v2.1 M4 wu-21-08，英文可行动报文）。

    与 _deny_dispatch 同形态；报文含 wave_id 与「re-prepare wave」
    指引——wave 已关闭 / 成员变更时，主会话照报文重备 wave（
    prepare_dispatch_wave）并以新 permit marker 重新派发即可自救。
    """
    text = ("Agent dispatch blocked: %s (wave_id=%s). This wave permit is "
            "no longer valid for its unit; re-prepare the wave with "
            "task_manager.prepare_dispatch_wave and dispatch with the "
            "fresh marker GLM_CONDUCTOR_DISPATCH=<permit_id>."
            % (reason, wave_id))
    output = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": text,
        }
    }
    sys.stdout.write(json.dumps(output) + "\n")
    return 0


def _marker_text(tool_input):
    """把 tool_input 的 prompt / description 拼为 marker 检索面（容错）。"""
    parts = []
    for key in ("prompt", "description"):
        value = tool_input.get(key) if isinstance(tool_input, dict) else None
        if isinstance(value, str) and value:
            parts.append(value)
    return "\n".join(parts)


def ownership_injection(repo):
    """既有 ownership advisory 注入路径（v2.0.1 行为原样，供非实施者
    类型与 legacy 场景复用）：发现声明 ownership 的活动任务即注入
    提醒，无则静默。"""
    from runtime import state

    ownership_pairs = declared_ownership_tasks(repo)
    if not ownership_pairs:
        return 0
    reminder = build_reminder(ownership_pairs)
    output = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": reminder,
        }
    }
    sys.stdout.write(json.dumps(output) + "\n")
    return 0


def agent_permit_gate(payload):
    """Agent|Task 路径（v2.1 M2 升级）：D1 分级 permit 门 + 注入兜底。

    校验链（§6.4 修订版，任一失败即 deny）：
      1. 无活动任务 → 零干预放行（v2.0.1 行为不回退）；
      2. tool_input.subagent_type 非实施者类型 → 既有注入路径原样；
      3. 实施者类型 → marker 解析（prompt/description）→ 无 marker
         即 deny；
      4. permit 归属活动任务（逐任务 load；consumed/invalidated 视同
         不存在）→ 无归属即 deny "permit not found"；
      5. validate_permit（存在/任务匹配/未过期；unit/wave 维由 permit
         内容保证，hook 不传）→ 不过即 deny（原因透出）；
      5.5 wave 成员资格（v2.1 M4 wu-21-08）：permit 带 wave_id 时经
         dispatch_wave.validate_wave_membership 校验（wave 存在且
         active 且 unit 仍在成员清单）→ 失败 deny（报文含 wave_id 与
         re-prepare 指引）；单单元 permit（wave_id None）不受影响；
      6. mode 一致性：background 且 run_in_background 非 True →
         updatedInput 全量替换强制后台；foreground / 已后台 → 放行
         （附 join 提醒）。
    """
    from runtime import dispatch_wave, state

    repo = repo_root()
    active = state.find_active_tasks(repo)
    if not active:
        return 0

    tool_input = payload.get("tool_input")
    tool_input = tool_input if isinstance(tool_input, dict) else {}
    subagent_type = tool_input.get("subagent_type")
    if not (isinstance(subagent_type, str)
            and subagent_type in IMPLEMENTATION_EXECUTORS):
        return ownership_injection(repo)

    permit_id = dispatch_wave.parse_marker(_marker_text(tool_input))
    if permit_id is None:
        return _deny_dispatch("no dispatch marker found")

    owner_task = None
    permit = None
    for task_id in active:
        loaded = dispatch_wave.load_permit(repo, task_id, permit_id)
        if loaded is not None:
            owner_task, permit = task_id, loaded
            break
    if permit is None:
        return _deny_dispatch("permit not found")

    ok, reason = dispatch_wave.validate_permit(repo, owner_task, permit_id)
    if not ok:
        return _deny_dispatch(reason)

    # wave 成员资格校验（v2.1 M4 wu-21-08）：wave permit 须指向 active
    # wave 且 unit 仍在成员清单内——closed / 重组后的旧 wave permit 不
    # 得再放行；单单元 permit（wave_id None）恒放行，零行为变化
    wave_id = permit.get("wave_id")
    if wave_id is not None:
        wave_ok, wave_reason = dispatch_wave.validate_wave_membership(
            repo, owner_task, permit)
        if not wave_ok:
            return _deny_wave(wave_reason, wave_id)

    # mode 一致性（§6.5）：background 许可强制后台——未显式 True 即经
    # updatedInput 全量替换回写（全部原有字段 + run_in_background=True）；
    # foreground 许可同步放行不改写
    if permit.get("mode") == "background" \
            and tool_input.get("run_in_background") is not True:
        updated = dict(tool_input)
        updated["run_in_background"] = True
        output = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "updatedInput": updated,
                "additionalContext": JOIN_REMINDER,
            }
        }
    else:
        output = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "additionalContext": JOIN_REMINDER,
            }
        }
    sys.stdout.write(json.dumps(output) + "\n")
    return 0


# —— 职责四：CronCreate 嵌套创建 fail-fast 门（v2.2 C6，wu-22-C6） ——

def _deny_cron_nested(evidence):
    """输出嵌套创建门的 deny 决策（英文可行动报文）并返回 0。

    报文指明：拦截依据（在案证据）+ 硬门语义 + 自救路径（交互会话
    走 bridge 生命周期记账）——主会话照报文即可调整编排。
    """
    text = ("GLM CONDUCTOR: CronCreate blocked: nested automation "
            "creation is forbidden for this session (recorded scheduler "
            "evidence: %s). A session owned or previously rejected by "
            "the host scheduler MUST NOT create further Scheduled Tasks "
            "(Phase 0 hard gate). Do not retry CronCreate from this "
            "session; perform bridge lifecycle changes from an "
            "interactive session and record them with "
            "task_manager.arm_wake_bridge instead." % evidence)
    output = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": text,
        }
    }
    sys.stdout.write(json.dumps(output) + "\n")
    return 0


def cron_nested_gate(payload):
    """CronCreate 嵌套创建 fail-fast 路径（v2.2 C6 职责四）。

    只读 runtime.scheduler_facts 会话事实缓存（PostToolUse Cron* /
    PostToolUseFailure 分类路径写入的已证事实），按载荷 session_id
    检索：
      - create == "forbidden" 或 origin == "scheduled_task" 任一在案
        → deny（单探针纪律：禁止事实已在案，绝不再放行重探测）；
      - 无 session_id / 无记录 / 记录无禁止事实 → 静默放行（exit 0、
        stdout 恒空）——无证据不预判，第一次尝试必须被允许才能产生
        证据。
    本路径零写零事件；读取按 scheduler_facts 的 fail-open 契约
    （读失败 = 无证据 = 放行）。
    """
    from runtime import scheduler_facts

    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or session_id == "":
        return 0  # 无会话身份 = 无证据 → 静默放行
    facts = scheduler_facts.read_session_record(repo_root(), session_id)
    if not isinstance(facts, dict):
        return 0
    evidence = []
    if facts.get("origin") == "scheduled_task":
        evidence.append("origin=scheduled_task")
    if facts.get("create") == "forbidden":
        evidence.append("create=forbidden")
    if not evidence:
        return 0
    return _deny_cron_nested(", ".join(evidence))


def main():
    """主流程：读 stdin（容错）→ 按 tool_name 分发四条路径。

    - tool_name == "Bash" → 策略门控路径（bash_policy_gate）；
    - tool_name == "CronCreate" → 嵌套创建 fail-fast 门
      （cron_nested_gate，v2.2 C6）；
    - 其余（Agent / Task / tool_name 缺失 / 空 payload）→ permit 门
      路径（agent_permit_gate：无活动任务零干预、非实施者类型走
      advisory 注入、实施者类型按 permit 链 allow/deny）。任何路径
      return 0；deny 由 ZCode 按 permissionDecision 处理。
    """
    # 1) 读 stdin 并解析（容错为 {}）；按 tool_name 分发（缺失按现状
    #    走 Agent|Task 路径，见模块 docstring「分发与兼容选择」）
    payload = read_payload()
    if payload.get("tool_name") == "Bash":
        return bash_policy_gate(payload)
    if payload.get("tool_name") == "CronCreate":
        return cron_nested_gate(payload)
    return agent_permit_gate(payload)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as exc:  # fail-open：advisory 层降级可见，绝不阻断派发
        sys.stderr.write(
            "ENFORCEMENT DEGRADED: pre_tool_use failed: %r\n" % (exc,))
        sys.exit(0)
