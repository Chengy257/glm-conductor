#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GLM Conductor v2.7.0 W2 PermissionRequest 钩子——CreateWorkflow 任务级免确认。

职责：
    以 ZCode 插件钩子（hooks/hooks.json 声明，matcher "CreateWorkflow"）
    挂在 PermissionRequest 事件上：宿主权限请求层对 CreateWorkflow 提交
    弹确认卡时，本钩子与 UI 卡并发竞速应答——启动 interview Q4 显式授权
    （任务 state 顶层 auto_approve_workflow: true）且本次提交是 path 型
    （脚本路径归一后落在 gen/ 或 .zcode/workflow-drafts/ 之下）时，输出
    decision:{behavior:"allow"} 直接应答放行；其余一律弃权（stdout 空，
    默认人工确认行为不变）。

机制依据（B0 P-1 改案，源码证据
docs/reviews/V270_B0_SPIKE_RESULTS.md）：
    机制一 PreToolUse permissionDecision=allow 对 CreateWorkflow 是
    死路——宿主 applyPreToolPermissionDecision 将 ask 升级为 allow 的
    条件含 !e.alwaysAsk，而 CreateWorkflow 工具定义
    permission.alwaysAsk:true，PreToolUse allow 被该闸显式免疫，无任何
    输入形态可绕过；机制二 PermissionRequest
    decision:{behavior:"allow"} 成立——权限主链 racePermissionResponders
    让 UI 授权卡（permissionBroker.requestPermission）与
    PermissionRequest 钩子链在 ask 请求层并发竞速、先到先得，钩子应答
    直接满足请求，alwaysAsk 只挡 PreToolUse 升级层、不挡请求层应答。

decision-only 纪律（零持久化设计）：
    放行输出只带 decision.allow，绝不携带 permissionUpdates /
    updatedPermissions / updatedInput——钩子给的 updates 会持久化到
    session/project store（跨会话存活），造成无任务授权状态下的历史性
    过度授权；本钩子逐请求现查任务态 + path 限制，权限零持久化。

判定链（顺序固定，任一不过即弃权 = stdout 空、默认人工确认）：
      1. stdin 解析失败 / 非 JSON / 非 dict → 弃权；
      2. hookEventName != "PermissionRequest" 或 toolName !=
         "CreateWorkflow" → 弃权（防御性，matcher 本已过滤）；
      3. toolInput 非 dict 或其中 path 缺失/非 str → 弃权（inline
         script 提交保留人工确认）；
      4. path 经 os.path.abspath 归一后不位于 <repo_root>/gen/ 或
         <repo_root>/.zcode/workflow-drafts/ 之下 → 弃权；
      5. 扫 <repo_root>/.glm-conductor/tasks/*/state.json：存在任一
         任务 status 不属于 {completed, cancelled, failed}（非终态）
         且顶层 auto_approve_workflow is True → 放行。单个 state.json
         损坏 / 非法 JSON / 缺字段 → 跳过该任务继续扫；只读 status 与
         auto_approve_workflow 两字段，不做 schema 全校验、不做 legacy
         识别。任务进终态后钩子自然失权（只扫非终态任务）。

stderr 观测面（v2.8.0 W-A1 诊断装备化；行为零变更——stdout 契约、
exit code、fail-open、纯本地红线全部不变，只加 stderr 一行观测）：
      每个弃权分支一行 `PERMISSION_HOOK abstain: reason=<code>
      detail=<…>`，reason 码集恰六值：bad_payload / bad_event /
      bad_tool / no_path / path_outside / no_authorized_task；
      放行路径一行 `PERMISSION_HOOK allow: task=<task_id> path=<归一
      后> path_raw=<归一前> repo_root=<root> repo_root_source=<源>`。
      path_outside 与放行两行的 detail 必含归一前/归一后 path、命中
      repo_root 及其来源（ZCODE_PROJECT_DIR / cwd）——直接服务
      H2（repo_root 口径失配）/ H3（相对 path 归一基错）判别；
      no_authorized_task 行的 detail 含 repo_root 及来源（H4 判别：
      state 未落 / 旗标未带 / 任务盘面在别的 root）。

多任务仓库语义（诚实声明）：
    判定是任务级授权、仓库级生效窗口——任一非终态已授权任务存活期间，
    该仓库内 path 型 CreateWorkflow 提交均免确认。

纯本地红线（§8.2）：
    零 runtime import、零第三方依赖（仅 json/os/sys，python3 -S 可
    运行，timeoutMs 5000 内完成）；不写任何文件、不联网、不调模型、
    无 git subprocess——纯只读扫描。

fail-open 契约（与 session_start / stop_gate 同款模板）：
    全路径只放行或弃权，绝不输出 decision deny、绝不阻断权限请求；
    任何内部异常 → stdout 空 + stderr 一行 "ENFORCEMENT DEGRADED:
    permission_request_workflow failed: ..." 后 exit 0。绝不存在
    输出非空且 exit 非 0 的路径。

repo_root 口径：
    ZCODE_PROJECT_DIR 优先（ZCode 钩子进程注入），缺失回退当前工作
    目录——与 session_start / stop_gate 同源。
"""

import json
import os
import sys

# 降级报文模板（fail-open 契约的 stderr 一行；ENFORCEMENT DEGRADED
# 前缀与 session_start / stop_gate 一致，英文值）
DEGRADED_FAILURE_MESSAGE_TEMPLATE = (
    "ENFORCEMENT DEGRADED: permission_request_workflow failed: %r\n")

# stderr 观测行模板（v2.8.0 W-A1 诊断装备化）：弃权分支一行原因码、
# 放行一行任务+路径四元组——观测面独立于 stdout 决策契约（决策行为
# 零变更），仅服务 Desktop 双臂诊断的判定链走向判别
ABSTAIN_LINE_TEMPLATE = "PERMISSION_HOOK abstain: reason=%s detail=%s\n"
ALLOW_LINE_TEMPLATE = (
    "PERMISSION_HOOK allow: task=%s path=%s path_raw=%s "
    "repo_root=%s repo_root_source=%s\n")


def _abstain(reason, detail):
    """弃权观测行：stderr 一行原因码（决策仍为 stdout 空 + exit 0）。"""
    sys.stderr.write(ABSTAIN_LINE_TEMPLATE % (reason, detail))
    return 0


def _report_allow(task_id, norm_path, raw_path, root, root_source):
    """放行观测行：stderr 一行 task + 归一前/后 path + repo_root 及来源。"""
    sys.stderr.write(ALLOW_LINE_TEMPLATE % (
        task_id, norm_path, raw_path, root, root_source))

# 终态集合：判定链 (5) 只认非终态任务的授权（终态任务自然失权）
TERMINAL_STATUSES = frozenset(("completed", "cancelled", "failed"))

# 放行输出（decision-only：只答本请求，绝无 permissionUpdates/
# updatedInput——零持久化设计）；单行 JSON、紧凑分隔符逐字节对齐规格
ALLOW_OUTPUT = {
    "hookSpecificOutput": {
        "hookEventName": "PermissionRequest",
        "decision": {
            "behavior": "allow",
            "message": (
                "glm-conductor task-level auto-approve "
                "(interview-authorized, path-type submission)"),
        },
    },
    "suppressOutput": True,
}


def repo_root():
    """返回被检查的仓库根及其来源（ZCODE_PROJECT_DIR 优先——ZCode
    钩子进程注入；缺失回退当前工作目录），来源字符串恰两值：
    "ZCODE_PROJECT_DIR" / "cwd"——观测面 detail 必含（H2 判别）。"""
    env = os.environ.get("ZCODE_PROJECT_DIR")
    if env:
        return env, "ZCODE_PROJECT_DIR"
    return os.getcwd(), "cwd"


def _strictly_under(path, root):
    """path 归一后是否严格位于 root 目录之下（等于 root 本身不算）。"""
    path = os.path.abspath(path)
    root = os.path.abspath(root)
    return path.startswith(root + os.sep)


def _path_authorized(path, root):
    """判定链 (4)：path 型提交的脚本路径必须落在 gen/ 或
    .zcode/workflow-drafts/ 之下，其余形态（含仓库其他位置）弃权。"""
    return (
        _strictly_under(path, os.path.join(root, "gen"))
        or _strictly_under(
            path, os.path.join(root, ".zcode", "workflow-drafts")))


def _has_authorized_task(root):
    """判定链 (5)：扫 tasks/*/state.json，返回命中的授权任务 id
    （str；无授权任务 → None）。

    单个 state.json 缺失 / 损坏 / 非法 JSON / 形状不符 → 跳过该任务
    继续扫（损坏不是授权证据，也不是阻断理由）；只读 status 与
    auto_approve_workflow 两字段。
    """
    tasks_dir = os.path.join(root, ".glm-conductor", "tasks")
    try:
        names = os.listdir(tasks_dir)
    except OSError:
        return None  # 无账本根 → 无授权，弃权
    for name in names:
        state_path = os.path.join(tasks_dir, name, "state.json")
        try:
            with open(state_path, "r", encoding="utf-8") as fh:
                state = json.load(fh)
        except (OSError, ValueError):
            continue  # 损坏/缺失 → 跳过该任务继续扫
        if not isinstance(state, dict):
            continue
        if state.get("status") in TERMINAL_STATUSES:
            continue  # 终态任务：钩子自然失权
        if state.get("auto_approve_workflow") is True:
            return name
    return None


def emit_allow_json():
    """放行输出：stdout 单行 JSON（紧凑分隔符，形态逐字节对齐规格）。"""
    sys.stdout.write(json.dumps(ALLOW_OUTPUT, separators=(",", ":")) + "\n")


def main():
    """主流程：判定链五步，顺序固定，任一不过即弃权（stdout 空 +
    stderr 一行原因码——观测面独立于决策，行为零变更）。"""
    # (1) stdin 解析失败 / 非 JSON / 非 dict → 弃权（读取异常同口径：
    #     读不出事件就放不了行，弃权并报告）
    try:
        payload = json.loads(sys.stdin.read())
    except Exception as exc:
        return _abstain("bad_payload", "stdin is not valid JSON: %r" % (exc,))
    if not isinstance(payload, dict):
        return _abstain(
            "bad_payload", "top-level stdin JSON is %s, not an object"
            % (type(payload).__name__,))

    # (2) 事件形状防御（matcher 本已过滤，此处二次校验）
    if payload.get("hookEventName") != "PermissionRequest":
        return _abstain(
            "bad_event", "hookEventName=%r" % (payload.get("hookEventName"),))
    if payload.get("toolName") != "CreateWorkflow":
        return _abstain(
            "bad_tool", "toolName=%r" % (payload.get("toolName"),))

    # (3) toolInput 非 dict 或 path 缺失/非 str → 弃权（inline script
    #     提交保留人工确认）
    tool_input = payload.get("toolInput")
    if not isinstance(tool_input, dict):
        return _abstain(
            "no_path", "toolInput is %s, not an object"
            % (type(tool_input).__name__,))
    path = tool_input.get("path")
    if not isinstance(path, str):
        return _abstain(
            "no_path", "toolInput.path is %s (inline script submission)"
            % (type(path).__name__,))

    # (4) path 归一后不在两个许可目录之下 → 弃权（detail 四元组服务
    #     H2 / H3 判别：归一前/归一后 path、repo_root 及其来源）
    root, root_source = repo_root()
    norm_path = os.path.abspath(path)
    if not _path_authorized(path, root):
        return _abstain(
            "path_outside",
            "path_raw=%r path_norm=%r repo_root=%r repo_root_source=%s"
            % (path, norm_path, root, root_source))

    # (5) 存在非终态且授权的任务 → 放行（stderr 一行观测）；否则弃权
    authorized = _has_authorized_task(root)
    if authorized is None:
        return _abstain(
            "no_authorized_task",
            "path_raw=%r path_norm=%r repo_root=%r repo_root_source=%s"
            % (path, norm_path, root, root_source))
    emit_allow_json()
    _report_allow(authorized, norm_path, path, root, root_source)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as exc:  # fail-open：只放行或弃权，绝不阻断权限请求
        sys.stderr.write(DEGRADED_FAILURE_MESSAGE_TEMPLATE % (exc,))
        sys.exit(0)
