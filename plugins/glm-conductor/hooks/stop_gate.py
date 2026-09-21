#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GLM Conductor v2.4 Phase 2 完成守卫（工作包 P2-D，单元 W4）。

职责：
    以 ZCode 插件钩子（hooks/hooks.json 声明）挂在 Stop 事件上，对
    触发域内的 v2.4 任务按序做四查，全部通过时执行 runtime.task.complete
    （置 completed + 释放仓库写者守卫 + journal task_completed 事件）
    并放行；任一失败则以 stdout 单行 block JSON 请求模型续跑，reason
    为恰好一条最严重、可操作的中文理由（四查顺序即严重序）。本钩子
    只是最后防线——同一套判定经 evaluate_completion(repo_root, task_id)
    暴露为可导入函数，生命周期 API 侧（主会话）可显式调用。

四查（按序，首败即返）：
      1. writer_guard：仓库无活跃写 workflow——
         runtime.writer_guard.inspect(生效仓库根) 为空即过；持有者是
         本任务同样放行（本任务 Workflow 的预约要到终态收尾才释放，
         完成门自身就是释放点）；被其他任务持有 → 拦截并逐字报出
         持有者 task_id 与 workflow_run_id；已注册委派 run
         （state.workflow_run_id 非空）而守卫缺失 → 拦截（missing
         writer reservation——委派任务完成前必须仍持有守卫，AF-04）；
         无 run id（solo/audit 或未注册委派 run）守卫缺失仍放行；
      2. ownership：实际改动路径（ownership.git_touched_files）全部
         落在 dag 全节点 ownership scope 并集内
         （task.ownership_scopes + ownership.classify_paths）；越界路径
         逐条列出，并附参与比对的 dag 节点 id；
      3. validation：validation.status == "passed" 且
         validation.change_id 等于当前 change_id.compute_change_id
         （相关改动文件集 = task.relevant_changed_files：ownership
         scope 并集 ∩ git 工作区实际改动，与 W3 记录侧同一派生与
         实现——任何 owned 文件变化都会使既有记录过期）；
      4. review：route.assurance == "high" 时 review.verdict 必须为
         "ship" 且 review.change_id 等于当前 change_id。
    standard 保障任务无独立审查义务（查 4 跳过）；fix-first / rethink
    永不满足完成；验证 / 评审的新鲜度统一锚定任务级 change_id。

触发域（v2.4 收窄）：
    账本根 discover_tasks 的 active 桶中 status == "active" 的 v2.4
    任务。停泊态（waiting_quota / waiting_user / blocked）是有意的
    停止点，收尾不发生在本回合，一律放行；终态任务无可评估；v2.3
    遗留任务放行并附一行迁移指引（LEGACY_STATE_GUIDANCE，绝不假装
    能判完成）；无活跃 conductor 任务静默放行（与 v2.3 外壳一致）。
    corrupt / orphaned 任务目录不参与拦截，仅 stderr 一行提示。

v2.3 外壳保留面（进程 I/O）：
    读 Stop 事件 JSON（read_stop_event，容错）、stdout 单行决策 JSON
    （emit_block_json，block 之外零输出）、stderr 提示（warn_stderr，
    UTF-8 字节写）、账本根定位（ledger_root）、钩子自身崩溃的
    fail-open 兜底（__main__ 包装：任何异常 stderr 报告后 exit 0，
    绝不阻断会话）。求值期结构性错误（非 git 仓库 / git 故障的
    ChangeIdError、非法 scope 模式的 OwnershipError 等）按任务隔离
    降级：stderr 一行报告后放行该任务（基础设施故障不是完成证据，
    沿用 v2.3 外壳的 fail-open 契约）。

v2.3 完成门拆除面（v2.4 无对应物，见
docs/roadmap/V2_4_PHASE_2_STATE_ASSURANCE_RETIREMENT_SPEC.md §5）：
    finalizing 完成请求态、receipts/ 扫描与 runner 真实性链、
    许可 / 租约检查、逐命令 verification 证据
    （verification_stale / verification_missing 等逐命令 check 词汇）、
    visual_evidence 比对、continuity 第 0 位检查、v2.3 的
    evaluate_task / collect_violations 逐任务求值管线、
    gate_blocked / gate_exhausted / gate_degraded 记账机器（本钩子
    不再向 journal 写任何事件——唯一的 journal 写入来自
    runtime.task.complete 的 task_completed）、gate_passed 记账、
    多任务违规清单与 "Also failing" 附注。

evaluate_completion(repo_root, task_id) 契约：
    repo_root 为任务账本根（.glm-conductor/tasks/<task-id>/ 所在根，
    与 v2.3 一致）；生效仓库根经 state.resolve_repository_root 解析
    （repository.root 绑定优先，账本根回退）。返回判定记录 dict：
      {"decision": "allow" | "block",
       "check":    COMPLETION_CHECKS 之一或 None（allow 时），
       "detail":   结构化字段 dict 或 None，
       "reason":   中文理由或 None（allow 时）}
    四查全过时本函数就地执行 runtime.task.complete（判过即收尾）。
    任务缺失 / v2.3 遗留 → ValueError（遗留消息携带处置指引）；
    change_id 求值 / ownership 匹配的结构性错误原样上抛
    （ChangeIdError / OwnershipError），由调用方裁决降级方式。
    钩子壳消费本函数：block → stdout 决策 JSON；异常 → stderr 降级
    放行该任务。

依赖：
    仅 Python 3 标准库 + runtime.state（W1）/ runtime.change_id（W2）/
    runtime.task（W3 生命周期）/ runtime.ownership（P1-C）/
    runtime.writer_guard（P1-E）；3.7 兼容语法（注解引号形式）。

来源：
    docs/roadmap/V2_4_PHASE_2_WORKFLOW_EXECUTION_PLAN.md W4（单元规格）
    + docs/roadmap/V2_4_PHASE_2_STATE_ASSURANCE_RETIREMENT_SPEC.md §5
    （完成守卫四查与拆除清单）。
"""

import json
import os
import sys
from pathlib import Path

# 接线插件根以复用 runtime（钩子脚本与 runtime/ 同插件；v2.3 外壳保留）
PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

# 四查词汇（按严重序排列；evaluate_completion 结果的 check 字段取值）
COMPLETION_CHECKS = ("writer_guard", "ownership", "validation", "review")


# —— 进程 I/O 外壳（v2.3 保留面，逐字沿用） ——

def ledger_root():
    """返回任务账本根：优先 ZCODE_PROJECT_DIR（ZCode 钩子进程注入），
    缺失时回退当前工作目录。

    语义与 v2.3 一致：.glm-conductor/tasks 发现与 state / journal I/O
    恒在此根；git 求值根按任务 repository.root 绑定解析
    （state.resolve_repository_root，绑定优先、本根回退）。
    """
    return os.environ.get("ZCODE_PROJECT_DIR") or os.getcwd()


def read_stop_event():
    """读取并解析 stdin 的 Stop 事件输入，返回 dict（任何输入问题按 {} 处理）。

    输入为 Claude 兼容 JSON（含 session_id / stop_hook_active 等字段），
    可能为空串或非 JSON——不因输入问题降级，一律容错为空对象。
    本函数只读不写：stdout 是运行时严格校验的通道，不得污染。
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


def warn_stderr(message):
    """向 stderr 写一行提示。

    走 sys.stderr.buffer 显式 UTF-8 字节写：Windows 管道默认 locale 编码
    对中文报文可能编码失败（UnicodeEncodeError），那会把一次提示整个
    吞掉。写入本身也容错——提示失败绝不能反过来让钩子崩溃（fail-open）。
    """
    text = message.rstrip("\n") + "\n"
    try:
        stream = getattr(sys.stderr, "buffer", None)
        if stream is not None:
            stream.write(text.encode("utf-8", "replace"))
            stream.flush()
        else:  # 防御：无 buffer（如被重载的文本流）时退回文本写
            sys.stderr.write(text)
    except Exception:
        pass


def emit_block_json(reason):
    """stdout 精确输出一行 block JSON（UTF-8；除此之外无任何输出）。

    reason 内的换行由 json.dumps 转义为 \\n，物理上仍是单行。走
    sys.stdout.buffer 显式 UTF-8 编码：避免 Windows 管道默认 locale
    编码对中文报文编码失败（那会把一次有意 block 变成异常降级）。
    """
    line = json.dumps({"decision": "block", "reason": reason},
                      ensure_ascii=False)
    data = (line + "\n").encode("utf-8")
    stream = getattr(sys.stdout, "buffer", None)
    if stream is not None:
        stream.write(data)
        stream.flush()
    else:  # 防御：无 buffer（如被重载的文本流）时退回文本写
        sys.stdout.write(line + "\n")
        sys.stdout.flush()


# —— 报文小助手 ——

def _section(value):
    """读取可能缺失的子块（缺失 / 非 dict → 空 dict，容忍形状异常）。"""
    return value if isinstance(value, dict) else {}


def _display_id(value):
    """报文里的可空标识呈现：非空 str 原样；其余 → "（未关联）"。"""
    if isinstance(value, str) and value != "":
        return value
    return "（未关联）"


def _dag_node_ids(task_state) -> "list[str]":
    """dag 全节点的 id 列表（保持声明序、去重；形状异常容错跳过）。"""
    seen = []
    dag = task_state.get("dag")
    if not isinstance(dag, list):
        return seen
    for node in dag:
        if not isinstance(node, dict):
            continue
        node_id = node.get("id")
        if isinstance(node_id, str) and node_id != "" \
                and node_id not in seen:
            seen.append(node_id)
    return seen


# —— 四查的中文理由构造（恰好一条、可操作） ——

def _guard_reason(task_id, holder) -> str:
    """查 1 失败理由：点名持有者 task_id 与 workflow_run_id。"""
    return (
        "完成被阻断：仓库写者守卫正被其他任务持有（任务 %s）。\n"
        "- 持有者 task_id：%s\n"
        "- 持有者 workflow_run_id：%s\n"
        "请等持有任务收尾（其终态收尾会自动释放守卫），或由操作者核实"
        "残留后显式释放（runtime.cli writer-release --force），"
        "再请求完成本任务。"
        % (task_id, _display_id(holder.get("task_id")),
           _display_id(holder.get("workflow_run_id"))))


def _reservation_missing_reason(task_id, workflow_run_id) -> str:
    """查 1 失败理由（委派任务守卫缺失）：点名任务与已注册的 run id。

    已注册委派 run（workflow_run_id 非空）的任务完成前必须仍持有本
    仓库写者守卫（AF-04）；预约缺失说明生命周期不变式已破（记录被
    误清 / 损坏），绝不放行。
    """
    return (
        "完成被阻断：任务 %s 已注册委派 run，但仓库写者守卫预约缺失"
        "（missing writer reservation——委派任务完成前必须仍持有本仓库"
        "写者守卫）。\n"
        "- 任务 task_id：%s\n"
        "- 已注册 workflow_run_id：%s\n"
        "请先经 cli.py writer-show 查看预约状态，核实守卫记录是否被"
        "误清；为本任务补回写预约（cli.py writer-acquire <repo_root> "
        "<task_id> --run-id <workflow_run_id>）后再请求完成。"
        % (task_id, task_id, workflow_run_id))


def _ownership_reason(task_id, out_of_scope, node_ids) -> str:
    """查 2 失败理由：越界路径逐条列出，附参与比对的 dag 节点 id。"""
    lines = [
        "完成被阻断：以下实际改动路径不在 dag ownership 并集内"
        "（任务 %s）：" % task_id,
    ]
    for path in out_of_scope:
        lines.append("- %s" % path)
    lines.append(
        "上述路径未被任何 dag 节点的 ownership scope 覆盖"
        "（已参与比对：%s）"
        % (", ".join(node_ids) if node_ids else "dag 无 ownership 声明"))
    lines.append(
        "请把这些路径补进相应节点的 ownership scope（scope 变化会使"
        "既有验证过期，需重新记录验证），或撤销越界改动后重新验证，"
        "再请求完成。")
    return "\n".join(lines)


def _validation_missing_reason(task_id, status) -> str:
    """查 3 失败理由（未通过分支）：点名当前 validation.status。"""
    return (
        "完成被阻断：任务 %s 尚无通过的验证记录"
        "（validation.status=%r）。\n"
        "请完成主验证并经 runtime.task.record_validation 记录 passed "
        "结果，再请求完成。" % (task_id, status))


def _validation_stale_reason(task_id, recorded, current) -> str:
    """查 3 失败理由（过期分支）：change_id 不一致，两侧都报。"""
    return (
        "完成被阻断：验证记录已过期——验证后任务相关文件又有变化"
        "（任务 %s）。\n"
        "- 记录 change_id：%s\n"
        "- 当前 change_id：%s\n"
        "请重新验证并经 runtime.task.record_validation 刷新记录，"
        "再请求完成。" % (task_id, _display_id(recorded), current))


def _review_missing_reason(task_id, verdict) -> str:
    """查 4 失败理由（无 ship 分支）：high 保障必须有 ship 评审。"""
    return (
        "完成被阻断：high 保障任务必须有 ship 评审"
        "（任务 %s，review.verdict=%r）。\n"
        "请完成评审并经 runtime.task.record_review 记录 ship 结论"
        "（评审前需先有通过的验证记录），再请求完成。"
        % (task_id, verdict))


def _review_stale_reason(task_id) -> str:
    """查 4 失败理由（过期分支）：评审后相关文件又有变化。"""
    return (
        "完成被阻断：评审记录已过期——评审后任务相关文件又有变化"
        "（任务 %s）。\n"
        "请重新评审并经 runtime.task.record_review 刷新 ship 结论，"
        "再请求完成。" % task_id)


def _block(check, detail, reason) -> dict:
    """构造 block 判定记录（detail 为结构化字段 dict）。"""
    return {"decision": "block", "check": check, "detail": detail,
            "reason": reason}


_ALLOW = {"decision": "allow", "check": None, "detail": None, "reason": None}


# —— 四查求值（可导入入口） ——

def evaluate_completion(repo_root, task_id) -> dict:
    """对单个 v2.4 任务按序做四查；全过就地完成任务并放行（W4）。

    参数与语义：
      - repo_root：任务账本根（.glm-conductor/tasks/<task-id>/ 所在根）；
      - 生效仓库根经 state.resolve_repository_root 解析（repository.root
        绑定优先，账本根回退），查 1 / 查 2 / change_id 求值全部在该根上
        进行；完成收尾（task.complete）恒在账本根；
      - 返回判定记录 dict（见模块 docstring「evaluate_completion 契约」）。

    错误：
      - 任务缺失（无 state.json）→ ValueError；
      - v2.3 遗留任务 → ValueError（消息携带 LEGACY_STATE_GUIDANCE，
        绝不假装能判完成）；
      - state.json JSON 损坏 → ValueError（load_state 不静默）；
      - change_id 求值 / ownership 匹配的结构性错误（ChangeIdError /
        OwnershipError，如非 git 仓库、非法 scope 模式）原样上抛，
        由调用方裁决降级方式（钩子壳按任务隔离降级放行）。

    四查全过 → runtime.task.complete（status=completed + 释放仓库
    写者守卫 + journal task_completed 事件）后返回 allow；任何一查
    失败 → 返回 block（不落盘、不改状态、不动守卫）。
    """
    from runtime import change_id, ownership, state, task, writer_guard

    st = state.load_state(repo_root, task_id)
    if st is None:
        raise ValueError(
            "任务 %s 不存在（无 state.json），完成门无法评估" % task_id)
    if state.is_legacy_state(st):
        raise ValueError(
            "任务 %s 是 v2.3 遗留任务：%s"
            % (task_id, state.LEGACY_STATE_GUIDANCE))

    effective_root = state.resolve_repository_root(st, repo_root)
    node_ids = _dag_node_ids(st)
    scopes = task.ownership_scopes(st)

    # 查 1：无活跃写 workflow（持有者是自己放行——完成门就是释放点）；
    # 已注册委派 run 的任务在守卫缺失时同样拦截（AF-04：missing
    # writer reservation——完成前必须仍持有守卫；无 run id 放行）
    holder = writer_guard.inspect(effective_root)
    if holder is not None and holder.get("task_id") != task_id:
        return _block(
            "writer_guard",
            {"holder_task_id": holder.get("task_id"),
             "holder_workflow_run_id": holder.get("workflow_run_id")},
            _guard_reason(task_id, holder))
    if holder is None:
        registered_run_id = st.get("workflow_run_id")
        if isinstance(registered_run_id, str) and registered_run_id != "":
            return _block(
                "writer_guard",
                {"missing_writer_reservation": True,
                 "workflow_run_id": registered_run_id},
                _reservation_missing_reason(task_id, registered_run_id))

    # 查 2：实际改动 ⊆ dag ownership 并集（越界路径逐条点名）
    touched = ownership.git_touched_files(effective_root)
    _owned_hits, out_of_scope = ownership.classify_paths(touched, scopes)
    if out_of_scope:
        return _block(
            "ownership",
            {"out_of_scope": out_of_scope, "node_ids": node_ids},
            _ownership_reason(task_id, out_of_scope, node_ids))

    # 查 3：验证通过且新鲜（change_id 与 W3 记录侧同一派生与实现：
    # 同调 task.relevant_changed_files——scope 并集 ∩ git 实际改动）
    current = change_id.compute_change_id(
        effective_root, task.relevant_changed_files(st, effective_root))
    validation = _section(st.get("validation"))
    if validation.get("status") != "passed":
        return _block(
            "validation",
            {"status": validation.get("status"),
             "recorded_change_id": validation.get("change_id"),
             "current_change_id": current},
            _validation_missing_reason(task_id, validation.get("status")))
    recorded = validation.get("change_id")
    if recorded != current:
        return _block(
            "validation",
            {"status": validation.get("status"),
             "recorded_change_id": recorded,
             "current_change_id": current},
            _validation_stale_reason(task_id, recorded, current))

    # 查 4：high 保障 → ship 评审且新鲜（standard 无独立审查义务）
    route = _section(st.get("route"))
    if route.get("assurance") == "high":
        review = _section(st.get("review"))
        if review.get("verdict") != "ship":
            return _block(
                "review",
                {"verdict": review.get("verdict"),
                 "recorded_change_id": review.get("change_id"),
                 "current_change_id": current},
                _review_missing_reason(task_id, review.get("verdict")))
        if review.get("change_id") != current:
            return _block(
                "review",
                {"verdict": review.get("verdict"),
                 "recorded_change_id": review.get("change_id"),
                 "current_change_id": current},
                _review_stale_reason(task_id))

    # 全过 → 收尾（completed + 释放守卫 + task_completed 事件）并放行
    task.complete(repo_root, task_id)
    return dict(_ALLOW)


# —— Stop 钩子主流程（外壳薄包装） ——

def main():
    """v2.4 完成守卫主流程。

    返回值恒为 0（block 是 stdout JSON 决策，不用退出码 2）；除 block
    的单行 JSON 外不向 stdout 写任何内容，一切提示只走 stderr。
    stop_hook_active=true（续行循环中的 Stop）照常校验（载荷不参与
    分支决策，保留解析以备扩展）。触发域见模块 docstring：仅对
    status == "active" 的 v2.4 任务四查；第一个 block 即输出并返回
    （恰好一条理由），全绿任务已随 evaluate_completion 就地完成。
    """
    # 1) 读 stdin（容错；v2.3 外壳保留面）
    read_stop_event()

    # runtime 模块在函数内 import：其上的 sys.path 接线已就绪，
    # import 失败会被外层 fail-open 捕获
    from runtime import state

    # 2) 账本根与任务发现（v2.3 外壳保留面：discover_tasks 四分类）
    ledger = ledger_root()
    discovery = state.discover_tasks(ledger)

    # 3) 非拦截桶仅 stderr 一行提示（corrupt / orphaned 不参与判定；
    #    先于无任务早退——损坏 / 孤儿目录也要可见）
    for name, _reason in discovery["orphaned"]:
        warn_stderr(
            "GLM CONDUCTOR: 任务目录 %s 无 state.json（orphaned），"
            "完成门跳过" % name)
    for name, reason in discovery["corrupt"]:
        warn_stderr(
            "GLM CONDUCTOR: 任务 %s 的 state.json 不可读（%s），"
            "完成门跳过" % (name, reason))

    active = [name for name, _status in discovery["active"]]
    if not active:
        return 0  # 无活跃 conductor 任务 → 静默放行（普通会话零干预）

    # 4) 逐任务筛选求值域（目录名排序，确定性）：v2.3 遗留 → 一行
    #    迁移指引放行；停泊态（waiting_quota / waiting_user / blocked）
    #    与形状异常 → 放行；仅 status == "active" 的 v2.4 任务进入四查
    gated = []
    for name in sorted(active):
        try:
            st = state.load_state(ledger, name)
        except (ValueError, OSError) as exc:
            warn_stderr(
                "GLM CONDUCTOR: 任务 %s 的 state.json 不可读（%s），"
                "完成门跳过" % (name, exc))
            continue
        if st is None:
            continue
        if state.is_legacy_state(st):
            warn_stderr(
                "GLM CONDUCTOR: 任务 %s 是 v2.3 遗留任务，完成门不评估"
                "：%s" % (name, state.LEGACY_STATE_GUIDANCE))
            continue
        if st.get("status") != "active":
            continue  # 停泊态：有意停止点，放行（收尾不发生在本回合）
        gated.append(name)

    # 5) 四查逐任务求值：首个 block 即 stdout 决策 JSON（恰好一条
    #    中文理由）；求值结构性错误（非 git 仓库等）按任务隔离降级
    #    放行（stderr 一行报告，沿用 v2.3 外壳 fail-open 契约）
    for name in gated:
        try:
            result = evaluate_completion(ledger, name)
        except Exception as exc:
            warn_stderr(
                "GLM CONDUCTOR: 完成门无法评估任务 %s（%s），本轮放行"
                "该任务" % (name, exc))
            continue
        if result["decision"] == "block":
            emit_block_json(result["reason"])
            return 0
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as exc:  # fail-open：钩子自身崩溃绝不阻断会话
        warn_stderr("GLM CONDUCTOR: stop_gate failed: %r" % (exc,))
        sys.exit(0)
