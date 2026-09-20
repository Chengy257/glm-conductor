#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GLM Conductor runtime CLI（v2.4 Phase 3 收敛后的人工操作面）。

职责：
    以单行 JSON stdout 薄壳提供两族子命令：任务操作面（review-record）
    与额度只读解析（quota-resolve），以及 v2.4 新路径操作面（
    v24-compile / writer-* / v24-record-run）。技能层 runtime 调用
    一律走本 CLI（禁止 python3 -c 内联）。

v2.4 Phase 2（W6，P2-F）退役面：
    v2.3 执行运行时（派发决策 / permit / 租约 / run 账本 / 恢复清单 /
    溯源凭证 / 派发事务编排）已删除，其 CLI 子命令同步退役：permits /
    permit-show / permit-consume / agent-runs / wave-prepare /
    wave-show / manifest-show / verify-unit / verify-task 整体移除；
    review-record 改接 runtime.task.record_review（任务级审查记录，
    先验证后评审）。

v2.4 Phase 3（P3-D/E）收口面：
    配额控制面与连续性层整体下架，其 CLI 子命令随其后端一并移除：
    授权策略三子命令（policy-show / policy-set-parallel /
    policy-set-resume）、quota 观测 / 相决策 / 耗尽转态 / 恢复编排
    （quota-observe / quota-phase / quota-exhausted / quota-resume）、
    Persistent Wake Bridge 八子命令与 transport-status（wake-*）、
    常驻额度观测进程操作面、全局额度时钟四子命令与宿主兼容性探针
    （host-check）——对应后端模块（连续性层 /
    授权策略 / 激活运输 / 常驻观测 / 全局时钟 / 宿主调度探针）已
    全部删除，本 CLI 只保留仍有存活后端的子命令。

子命令：
    review-record <repo_root> <task_id> <reviewer> <verdict> [note]
        记录任务级审查裁决（v2.4 起改接 runtime.task.record_review；
        v2.3 的 durable review receipt 与调用真实性回验链已随执行面
        退役）：verdict 须 ∈ state.REVIEW_VERDICTS（ship / fix-first /
        rethink，词汇外 → 退出码 2）；note 缺省 None（透传 findings）。
        先验证后评审：任务尚无 validation 记录（status 与 change_id
        齐备）→ 拒绝；任务缺失 / v2.3 遗留任务 / 终态冻结 → 拒绝
        （均退出码 1，错误 JSON 含中文原因）。成功输出单行 JSON
        {task_id, review}（review 块含 reviewer / verdict / change_id /
        findings，change_id 现算——任何相关文件变化都会使记录过期）
        并落 review_recorded 事件。
    quota-resolve <repo_root> [--force-refresh]
        额度四态解析（v2.1 M5 wu-21-10，runtime.quota.resolver.
        resolve_quota_status 薄壳）：四级层级（新鲜缓存 → provider →
        陈旧缓存 → UNKNOWN）解析当前额度状态，输出键冻结 dict
        {status, source, evaluated_at, reason}（绝不含凭证材料）。
        --force-refresh 跳过层级 1 强制走 provider（§32 唤醒强制刷新
        语义）。观测面：provider 成功时写额度缓存，零任务转态。
    v24-compile <dag-json> [--task-ref <id>] [--out <path>]
        v2.4 Phase 1 原生 Workflow 编译入口（P1-F，unit U6；只生成源
        码，绝不自动执行——执行是主会话经宿主 CreateWorkflow 的事）。
        读入 DAG JSON 文件（顶层对象：task_context 含 task_ref / goal /
        repository 三个非空字符串；nodes 为静态节点数组），依次走
        runtime.work_unit.validate_node（逐节点形状）、
        runtime.dependency.graph_errors（全图：重复 id / 缺失依赖 /
        自依赖 / 环）、runtime.ownership.plan_stages（编译期阶段规划，
        歧义 / 冲突即拒）、runtime.workflow.compiler.compile_workflow
        （确定性 TS 源）。成功：缺省把 TS 源打到 stdout（多行纯文本，
        单行 JSON 契约的显式例外；显式 UTF-8）；
        --out <path> 时源码落盘（UTF-8 / LF），stdout 只打一行 JSON
        摘要 {ok, stages, nodes, out}。--task-ref <id> 覆盖 DAG JSON
        内的 task_context.task_ref。校验失败（DAG JSON 不可读 / 不可
        解析 / 非对象 / 节点形状 / 依赖图 / ownership 冲突 /
        task_context 缺键）→ 退出码 2，stdout 单行 JSON
        {"ok": false, "errors": [...]}——全部错误一次报出（错误消息
        含涉事节点 id）。
    writer-acquire <repo_root> <task_id> [--run-id <id>]
    writer-release <repo_root> <task_id> [--run-id <id>] [--force]
    writer-show <repo_root>
        v2.4 Phase 1 仓库写者守卫操作面（P1-E/F，unit U5/U6，
        runtime.writer_guard 薄壳）。acquire 为 repo_root 取仓库级写
        预约（一个 Git 仓库至多一个活跃 Conductor 写 Workflow；持久
        记录 .glm-conductor/writer_guard.json；无任何按时间过期逻辑）；
        release 幂等释放（非持有者被拒并报当前持有者）；--force 为
        运维 inspect 之后的显式清除路径——先 inspect 取当前持有者、
        再以持有者自身 task_id 释放（writer_guard 不设绕过持有者的
        旁路），stdout 输出被清除的持有者四字段信息
        {ok, forced, released, cleared_holder, conflict}；show 只读
        输出 {holder: <四字段 dict|null>}（同款包裹形态）。三个子命令
        均打单行 JSON；结果 ok=false（acquire 冲突 / release 非持有者
        拒绝）→ 退出码 1，成功 → 0。id 全由调用方供给（本 CLI 不造
        id）。
    v24-record-run <task_ref> <run-id> [--artifact <path>]
        v2.4 Phase 1 run 关联记录入口（P1-D/F，unit U4/U6，
        runtime.workflow.adapter.record_run 薄壳）：把「任务 ↔ 原生
        Workflow run」的 id 关联落为 .glm-conductor/workflow-runs/ 下
        单一 JSON 记录（目录相对当前工作目录——调用方须在目标仓库根
        运行本命令）。stdout 直出落盘记录 dict（task_ref /
        workflow_run_id / created_at + 可选 artifact_path）；结构非法
        （空 id 等，WorkflowRunError）→ 退出码 2。零状态镜像：绝不落
        任何子代理运行时状态（F6 冻结结果）。

输出与退出码契约：
    stdout 恒为单行 JSON（json.dumps(..., ensure_ascii=True)，中文以
    \\uXXXX 转义——管道 / Windows 控制台零编码依赖；例外：v24-compile
    缺省输出路径时把生成 TS 源以多行纯文本打到 stdout（显式 UTF-8，
    生成源含中文））；stderr 不承载结构化输出。
    退出码：
      0 = 成功；
      2 = 校验拒绝（用法错误 / 参数值非法 / save_state 校验闸或状态
          转换门拒绝——含盘上 state.json 损坏的解析拒绝）；
      1 = 异常（任务不存在 / 审查记录被拒 / 意外错误；错误 JSON 只
          含异常类型名与消息，供操作者排查）。

依赖方向：
    本模块是薄壳：校验与变换都在 runtime.state / runtime.task /
    runtime.writer_guard / runtime.workflow / runtime.quota.resolver
    等域模块内，这里只做 argv 解析、JSON 输出与退出码映射。插件根经
    sys.path 引导（runtime/quota/report.py 同款），因此
    `python3 plugins/glm-conductor/runtime/cli.py ...` 可在仓库根
    直接运行。

依赖：
    仅 Python 3 标准库（json / pathlib / sys），零第三方依赖。
"""

import json
import pathlib
import sys

# 接线插件根以复用 runtime（CLI 脚本与 runtime/ 同插件；quota report 同款）
PLUGIN_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from runtime import state  # noqa: E402

USAGE = (
    "用法: python3 plugins/glm-conductor/runtime/cli.py "
    "review-record <repo_root> <task_id> <reviewer> <verdict> [note] | "
    "quota-resolve <repo_root> [--force-refresh] | "
    "v24-compile <dag-json> [--task-ref <id>] [--out <path>] | "
    "writer-acquire <repo_root> <task_id> [--run-id <id>] | "
    "writer-release <repo_root> <task_id> [--run-id <id>] [--force] | "
    "writer-show <repo_root> | "
    "v24-record-run <task_ref> <run-id> [--artifact <path>]")


class _UsageError(ValueError):
    """用法 / 子命令 / 参数个数错误——校验拒绝类，退出码 2。"""


class _TaskMissing(Exception):
    """任务不存在（无 state.json）——运行期异常，退出码 1。"""


class _ReviewRejected(Exception):
    """review-record 被 runtime.task 拒绝（任务缺失 / v2.3 遗留任务 /
    终态冻结 / 先验证后评审顺序闸；ValueError 口径）——运行期拒绝，
    退出码 1。"""


def _emit(payload):
    """向 stdout 写单行 JSON（ensure_ascii=True，契约锚点）。"""
    sys.stdout.write(json.dumps(payload, ensure_ascii=True) + "\n")


def _force_utf8_stdout():
    """把 stdout 调到 UTF-8（v24-compile 源码文本输出用；quota/report.py
    同款——非 TTY / 测试捕获（StringIO）容错跳过）。"""
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError, OSError):
        pass  # 测试捕获（StringIO）或已被重定向：保持原样


def _require_task_for_review(repo_root, task_id) -> None:
    """review-record 共用的任务存在闸：不存在 → _TaskMissing（退出码
    1）。先于任何记录 I/O——拼错 task_id 得到「任务不存在」而非顺序
    闸的「先验证后评审」假象。"""
    if state.load_state(repo_root, task_id) is None:
        raise _TaskMissing(
            "任务 %s 不存在（%s 下无 state.json），无法记录审查"
            % (task_id, repo_root))


def _review_record(repo_root, task_id, reviewer, verdict,
                   raw_note=None) -> int:
    """review-record：记录任务级审查裁决（v2.4 改接 runtime.task.
    record_review；v2.3 的 durable receipt 与调用真实性回验链已随执行
    面退役）。verdict 词汇在 CLI 侧先闸（参数值非法 → 退出码 2，与
    policy-set-resume 的 auto_resume 闸同口径）；note 缺省 None（透传
    findings）。任务缺失 → _TaskMissing（退出码 1）；runtime.task 的
    顺序与状态闸（v2.3 遗留任务 / 终态冻结 / 尚无 validation 记录的
    先验证后评审规则）→ _ReviewRejected（退出码 1）。成功输出单行
    JSON {task_id, review}（review 块与 state 落盘、journal
    review_recorded 事件同源同字段）。"""
    from runtime import task  # 函数内 import：monkeypatch 友好
    func_name = "review-record"
    if verdict not in state.REVIEW_VERDICTS:
        raise ValueError(
            "%s：verdict %r 不在 state.REVIEW_VERDICTS 内（%s）"
            % (func_name, verdict, ", ".join(state.REVIEW_VERDICTS)))
    _require_task_for_review(repo_root, task_id)
    try:
        st = task.record_review(
            repo_root, task_id, reviewer, verdict, findings=raw_note)
    except ValueError as exc:
        raise _ReviewRejected(str(exc)) from exc
    _emit({"task_id": task_id, "review": st.get("review")})
    return 0


# —— v2.1 M5（wu-21-10 / §14）：quota 连续性 ——

def _quota_resolve(repo_root, force_refresh=False) -> int:
    """quota-resolve：额度四态四级层级解析（quota.resolver.
    resolve_quota_status 薄壳，输出键冻结 dict 原样直出）。观测面
    零转态；--force-refresh 跳过新鲜缓存层强制走 provider。"""
    from runtime.quota import resolver  # 函数内 import：monkeypatch 友好
    resolved = resolver.resolve_quota_status(repo_root,
                                             force_refresh=force_refresh)
    _emit(resolved)
    return 0


# —— v2.4 Phase 1（P1-F，unit U6）：新路径 CLI 与守卫接线 ——
#
# 本节为纯追加薄壳：校验 / 规划 / 编译 / 记账语义全部在
# runtime.work_unit / runtime.dependency / runtime.ownership /
# runtime.workflow（compiler / adapter）与 runtime.writer_guard 内，
# 这里只做 DAG JSON 读入、argv 解析、JSON 输出与退出码映射。
# 生成源绝不自动执行（执行是主会话经宿主 CreateWorkflow 的事）；
# 路由词汇（技能 / 命令面）属 Phase 4。

def _v24_compile(dag_json_path, task_ref=None, out_path=None) -> int:
    """v24-compile：DAG JSON → 校验 → ownership 阶段规划 → 确定性 TS
    源（runtime.workflow.compiler.compile_workflow 薄壳）。

    管线（顺序固定，前置错误聚合一次报出）：
      1. 读入 DAG JSON 文件（顶层对象：task_context + nodes）；不可读 /
         不可解析 / 顶层非对象 → {"ok": false, "errors": [...]}，退出码 2；
      2. 逐节点 work_unit.validate_node + 全图 dependency.graph_errors，
         全部错误聚合（不短路）→ 有错退出码 2；
      3. ownership.plan_stages（歧义 / 冲突即拒）→ 拒绝退出码 2；
      4. compiler.compile_workflow 只生成源码字符串——绝不执行；
      5. 输出：--out 给出时源码落盘（UTF-8 / LF），stdout 单行 JSON
         摘要 {ok, stages, nodes, out}；缺省把 TS 源打到 stdout（多行
         纯文本，显式 UTF-8——生成源含中文）。

    --task-ref 给出时覆盖 DAG JSON 内的 task_context.task_ref
    （task_context 缺失 / 非对象时以覆盖值起底，goal / repository 缺键
    照常由编译器校验拒绝）。
    """
    from runtime import dependency, ownership, work_unit  # 函数内 import
    from runtime.workflow import compiler  # 函数内 import：monkeypatch 友好

    def rejected(errors):
        _emit({"ok": False, "errors": errors})
        return 2

    # 1) 读入 DAG JSON
    try:
        with open(dag_json_path, "r", encoding="utf-8") as handle:
            dag = json.load(handle)
    except OSError as exc:
        return rejected(["v24-compile：无法读取 DAG JSON %r：%s"
                         % (dag_json_path, exc)])
    except ValueError as exc:  # json.JSONDecodeError 是 ValueError 子类
        return rejected(["v24-compile：DAG JSON 解析失败（%s）：%s"
                         % (dag_json_path, exc)])
    if not isinstance(dag, dict):
        return rejected(["v24-compile：DAG JSON 顶层必须是 JSON 对象，"
                         "得到 %s" % type(dag).__name__])
    nodes = dag.get("nodes")
    task_context = dag.get("task_context")
    if task_ref is not None:
        if isinstance(task_context, dict):
            task_context = dict(task_context)
        else:
            task_context = {}
        task_context["task_ref"] = task_ref

    # 2) 节点形状（work_unit.validate_node）+ 全图（dependency.
    #    graph_errors）——聚合全部错误，一次报出
    errors = []
    if isinstance(nodes, list):
        for node in nodes:
            errors.extend(work_unit.validate_node(node))
        errors.extend(dependency.graph_errors(nodes))
    else:
        errors.append("v24-compile：nodes 必须是数组，得到 %s"
                      % type(nodes).__name__)
    if errors:
        return rejected(errors)

    # 3) ownership 编译期阶段规划（歧义 / 冲突即拒，绝不猜测）
    try:
        stages = ownership.plan_stages(nodes)
    except (ownership.OwnershipConflictError, ownership.OwnershipError,
            ValueError) as exc:
        return rejected(["v24-compile：ownership 阶段规划拒绝：%s" % exc])

    # 4) 编译（只生成源码字符串；形状 / 图 / task_context 错误在此聚合；
    #    绝不自动执行生成源）
    try:
        source = compiler.compile_workflow(nodes, task_context)
    except (compiler.WorkflowCompileError,
            ownership.OwnershipConflictError) as exc:
        return rejected(["v24-compile：%s" % exc])

    # 5) 输出
    if out_path is not None:
        with open(out_path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(source)
        _emit({"ok": True, "stages": len(stages), "nodes": len(nodes),
               "out": out_path})
    else:
        _force_utf8_stdout()  # 生成源含中文：显式 UTF-8（quota/report.py 同款）
        sys.stdout.write(source)
    return 0


def _writer_acquire(repo_root, task_id, run_id=None) -> int:
    """writer-acquire：为 task_id 取 repo_root 的仓库级写预约
    （writer_guard.acquire 薄壳）。冲突（ok=false，conflict 报当前
    持有者三键）→ 退出码 1；成功 → 0；空 / 非字符串 id（结构非法，
    WriterGuardError）→ 参数值非法口径，退出码 2。"""
    from runtime import writer_guard  # 函数内 import：monkeypatch 友好
    try:
        result = writer_guard.acquire(repo_root, task_id,
                                      workflow_run_id=run_id)
    except writer_guard.WriterGuardError as exc:
        raise ValueError("writer-acquire：%s" % exc) from exc
    _emit(result)
    return 0 if result["ok"] else 1


def _writer_release(repo_root, task_id, run_id=None, force=False) -> int:
    """writer-release：释放 repo_root 的写预约（writer_guard.release
    薄壳）。非持有者被拒（ok=false + conflict 报当前持有者）→ 退出码
    1；无预约幂等成功（released=false，退出码 0）。

    --force 为运维 inspect 之后的显式清除路径：writer_guard 不设绕过
    持有者的旁路——这里先 inspect 取当前持有者，再以持有者自身
    task_id 释放；stdout 输出被清除的持有者四字段信息
    {ok, forced: true, released, cleared_holder, conflict}（无预约时
    cleared_holder=null 的幂等成功，退出码 0）。"""
    from runtime import writer_guard  # 函数内 import：monkeypatch 友好
    try:
        if force:
            holder = writer_guard.inspect(repo_root)
            if holder is None:  # 幂等：无预约无可清除
                _emit({"ok": True, "forced": True, "released": False,
                       "cleared_holder": None, "conflict": None})
                return 0
            result = writer_guard.release(repo_root, holder["task_id"])
            _emit({"ok": result["ok"], "forced": True,
                   "released": result["released"],
                   "cleared_holder": holder,
                   "conflict": result["conflict"]})
            return 0 if result["ok"] else 1
        result = writer_guard.release(repo_root, task_id,
                                      workflow_run_id=run_id)
    except writer_guard.WriterGuardError as exc:
        raise ValueError("writer-release：%s" % exc) from exc
    _emit(result)
    return 0 if result["ok"] else 1


def _writer_show(repo_root) -> int:
    """writer-show：只读展示 repo_root 当前写预约（writer_guard.inspect
    薄壳）。输出 {holder: <四字段 dict|null>}（manifest-show 的
    {"manifest": ...} 同款包裹形态）；inspect 永不抛（损坏记录
    fail-open 视同无预约）——恒退出码 0。"""
    from runtime import writer_guard  # 函数内 import：monkeypatch 友好
    _emit({"holder": writer_guard.inspect(repo_root)})
    return 0


def _v24_record_run(task_ref, run_id, artifact=None) -> int:
    """v24-record-run：落「任务 ↔ 原生 Workflow run」id 关联记录
    （runtime.workflow.adapter.record_run 薄壳）。记录目录
    .glm-conductor/workflow-runs/ 相对当前工作目录（调用方须在目标
    仓库根运行本命令）；stdout 直出落盘记录 dict；零子代理状态镜像。
    结构非法（空 / 非字符串 id，WorkflowRunError）→ 参数值非法口径，
    退出码 2。"""
    from runtime.workflow import adapter  # 函数内 import：monkeypatch 友好
    try:
        record = adapter.record_run(task_ref, run_id,
                                    artifact_path=artifact)
    except adapter.WorkflowRunError as exc:
        raise ValueError("v24-record-run：%s" % exc) from exc
    _emit(record)
    return 0


def _dispatch(args) -> int:
    """argv 分发；子命令 / 参数个数错误抛 _UsageError（退出码 2）。"""
    if not args:
        raise _UsageError("缺少子命令。" + USAGE)
    cmd, rest = args[0], args[1:]
    if cmd == "review-record":
        if len(rest) not in (4, 5):
            raise _UsageError(
                "review-record 需要 <repo_root> <task_id> <reviewer> "
                "<verdict> [note] 四或五个参数。" + USAGE)
        return _review_record(
            rest[0], rest[1], rest[2], rest[3],
            rest[4] if len(rest) == 5 else None)
    if cmd == "quota-resolve":
        if len(rest) not in (1, 2):
            raise _UsageError(
                "quota-resolve 需要 <repo_root> [--force-refresh] 一或"
                "两个参数。" + USAGE)
        force_refresh = False
        if len(rest) == 2:
            if rest[1] != "--force-refresh":
                raise _UsageError(
                    "quota-resolve 的可选参数只接受 --force-refresh。"
                    + USAGE)
            force_refresh = True
        return _quota_resolve(rest[0], force_refresh=force_refresh)
    if cmd == "v24-compile":
        # 位置参数 <dag-json> + 旗标对（--task-ref <id> / --out <path>，
        # 各至多一次、顺序不限）→ 一、三或五个参数
        if len(rest) not in (1, 3, 5):
            raise _UsageError(
                "v24-compile 需要 <dag-json> [--task-ref <id>] "
                "[--out <path>] 一、三或五个参数。" + USAGE)
        task_ref = None
        out_path = None
        index = 1
        while index < len(rest):
            if index + 1 >= len(rest):
                raise _UsageError(
                    "v24-compile 的旗标 %r 缺少取值。" % rest[index]
                    + USAGE)
            flag, value = rest[index], rest[index + 1]
            if flag == "--task-ref" and task_ref is None:
                task_ref = value
            elif flag == "--out" and out_path is None:
                out_path = value
            else:
                raise _UsageError(
                    "v24-compile 的可选参数只接受 --task-ref <id> 与 "
                    "--out <path>（各至多一次）。" + USAGE)
            index += 2
        return _v24_compile(rest[0], task_ref=task_ref, out_path=out_path)
    if cmd == "writer-acquire":
        if len(rest) not in (2, 4):
            raise _UsageError(
                "writer-acquire 需要 <repo_root> <task_id> "
                "[--run-id <id>] 两或四个参数。" + USAGE)
        run_id = None
        if len(rest) == 4:
            if rest[2] != "--run-id":
                raise _UsageError(
                    "writer-acquire 的可选参数只接受 --run-id <id>。"
                    + USAGE)
            run_id = rest[3]
        return _writer_acquire(rest[0], rest[1], run_id=run_id)
    if cmd == "writer-release":
        if len(rest) < 2 or len(rest) > 5:
            raise _UsageError(
                "writer-release 需要 <repo_root> <task_id> "
                "[--run-id <id>] [--force] 两到五个参数。" + USAGE)
        run_id = None
        force = False
        index = 2
        while index < len(rest):
            flag = rest[index]
            if flag == "--force":
                if force:
                    raise _UsageError(
                        "writer-release 的 --force 至多出现一次。" + USAGE)
                force = True
                index += 1
            elif flag == "--run-id":
                if index + 1 >= len(rest):
                    raise _UsageError(
                        "writer-release 的 --run-id 需要恰一个取值。"
                        + USAGE)
                if run_id is not None:
                    raise _UsageError(
                        "writer-release 的 --run-id 至多出现一次。" + USAGE)
                run_id = rest[index + 1]
                index += 2
            else:
                raise _UsageError(
                    "writer-release 的可选参数只接受 --run-id <id> 与 "
                    "--force。" + USAGE)
        return _writer_release(rest[0], rest[1], run_id=run_id, force=force)
    if cmd == "writer-show":
        if len(rest) != 1:
            raise _UsageError(
                "writer-show 需要 <repo_root> 一个参数。" + USAGE)
        return _writer_show(rest[0])
    if cmd == "v24-record-run":
        if len(rest) not in (2, 4):
            raise _UsageError(
                "v24-record-run 需要 <task_ref> <run-id> "
                "[--artifact <path>] 两或四个参数。" + USAGE)
        artifact = None
        if len(rest) == 4:
            if rest[2] != "--artifact":
                raise _UsageError(
                    "v24-record-run 的可选参数只接受 --artifact <path>。"
                    + USAGE)
            artifact = rest[3]
        return _v24_record_run(rest[0], rest[1], artifact=artifact)
    raise _UsageError("未知子命令 %r。" % cmd + USAGE)


def main(argv=None) -> int:
    """CLI 入口：返回退出码（0 成功 / 2 校验拒绝 / 1 异常）。

    argv 缺省取 sys.argv[1:]；测试可直接传列表调用。异常映射：
    ValueError（用法 / 参数值 / save_state 校验栈）→ 2；
    _TaskMissing（任务不存在）/ _ReviewRejected（审查记录被
    runtime.task 顺序与状态闸拒绝）→ 1；其余意外异常 → 1（错误 JSON
    含异常类型名，stdout 契约不破）。
    """
    args = list(sys.argv[1:]) if argv is None else list(argv)
    try:
        return _dispatch(args)
    except ValueError as exc:  # 含 _UsageError：校验拒绝类
        _emit({"error": str(exc)})
        return 2
    except (_TaskMissing, _ReviewRejected) as exc:
        _emit({"error": str(exc)})
        return 1
    except Exception as exc:  # 意外兜底
        _emit({"error": "%s: %s" % (type(exc).__name__, exc)})
        return 1


if __name__ == "__main__":
    sys.exit(main())
