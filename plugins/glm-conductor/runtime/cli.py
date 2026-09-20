#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GLM Conductor runtime CLI（v2.4 Phase 2 收敛后的人工操作面）。

职责：
    以单行 JSON stdout 薄壳提供三类子命令：execution_policy 授权三
    子命令（policy-show / policy-set-parallel / policy-set-resume）、
    quota 连续性族（quota-resolve / quota-observe / quota-phase /
    quota-exhausted / quota-resume / wake-* / transport-status /
    quota-watcher / quota-clock-* / host-check）与 v2.4 新路径操作面
    （v24-compile / writer-* / v24-record-run / review-record）。技能
    层 runtime 调用一律走本 CLI（禁止 python3 -c 内联）。

v2.4 Phase 2（W6，P2-F）退役面：
    v2.3 执行运行时（派发决策 / permit / 租约 / run 账本 / 恢复清单 /
    溯源凭证 / 派发事务编排）已删除，其 CLI 子命令同步退役：permits /
    permit-show / permit-consume / agent-runs / wave-prepare /
    wave-show / manifest-show / verify-unit / verify-task 整体移除；
    review-record 改接 runtime.task.record_review（任务级审查记录，
    先验证后评审）；quota-exhausted / quota-resume 的恢复编排体已死，
    保留注册但调用即以 RuntimeError 明示退役（quota/continuity 族
    整体在 Phase 3 收口）。

子命令：
    policy-show <repo_root> <task_id>
        展示任务 execution_policy。state 存在 execution_policy 块 →
        原样展示（policy_source="state"）；缺该键（legacy 形态）→
        展示默认块（policy_source="default"），不写盘。只读，不改任何
        文件。
    policy-set-parallel <repo_root> <task_id> <max_workers>
        以用户身份写入并发授权：authorization.source="user"、
        confirmed_at=当前 UTC 时刻（本命令即用户确认动作的落笔）；
        max_workers=1 → serial，2-4 → standard；default_workers 超过
        新上限时由 setter 同步下调。写盘经 state.save_state（全量
        校验闸，含状态转换门），不绕过校验。
    policy-set-resume <repo_root> <task_id> <auto_resume>
                      [max_quota_windows]
        以用户身份写入续跑授权（同上 source/confirmed_at 口径）；
        max_quota_windows 缺省按 auto_resume 推导（manual/notify → 0，
        auto_once → 1，until_done → 1），显式给出时照传。
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
    quota-observe <repo_root> [task_id]
        自适应额度观测（v2.2 M3 wu-22-03，runtime.quota.observer.
        observe 薄壳）：resolver.resolve_quota_detail（四级层级，
        lazy heartbeat 的「进 runtime 先刷新」入口）+ state.json
        只读装配 task 侧输入 → §16.1 观测 dict 八键
        {provider_status, execution_phase, remaining_percent,
        reset_at, next_check_at, reason, wake_recommended,
        wake_required}。task_id 缺省按无任务保守默认（task_active=
        True、bridge=none、阈值/预算全默认）；给定 task_id 时经 v2.3
        执行态族折算 task_active——该折算随执行面退役（RuntimeError
        退出码 1），无任务形态不受影响。零 state 写、零 automation。
    quota-phase <repo_root> <task_id>
        执行相决策（v2.2 M3 wu-22-03，runtime.quota.control.
        evaluate_task_quota_phase 薄壳）：与 quota-observe 同源输入
        → §17.1 冻结 8 键决策 dict 原样直出；task 侧输入折算同
        quota-observe（给定 task_id → RuntimeError 退出码 1）。
    quota-exhausted <repo_root> <task_id>
        【已退役】v2.1 M5 的 EXHAUSTED 转态链（handle_quota_exhausted
        薄壳）随 v2.3 执行面退役——本子命令
        保留注册（Phase 3 与 quota/continuity 族一并收口），调用即
        RuntimeError：v2.3 执行面已退役 → 退出码 1（错误 JSON）。
    quota-resume <repo_root> <task_id> [status]
        【已退役】v2.1 M5 的恢复编排（resume_from_quota 薄壳）
        随 v2.3 执行面退役——保留注册，status 词汇闸仍在
        （非法 → 退出码 2），其余调用即 RuntimeError → 退出码 1
        （错误 JSON）。
    wake-record / wake-arm / wake-prompt / wake-plan / wake-status /
            wake-retime / wake-reconcile / transport-status
            <repo_root> <task_id> [...]
        Persistent Wake Bridge / Activation Transport 八子命令（v2.1
        M5 §14.4 起分批建设至 v2.3.0 W3）。实现已迁
        runtime/commands/wake.py（v2.3.1 Wave 3，unit w3-wake-split）
        ——cli 仅存参数校验分支 + 函数内 import 调用；stdout 形态
        （单行 JSON；wake-prompt 成功路径为纯文本；wake-plan /
        wake-status / transport-status 走 ensure_ascii=False 观测面）、
        失败 JSON 面 {"error": ...} 与退出码（0 成功 / 1 任务缺失·
        事务被拒 / 2 用法或参数值非法；wake-retime 无桥与决策异常
        fail-open 恒 0）键集与语义明细见该模块 docstring。其中直接
        以 v2.3 执行面为后端的路径（wake-record / wake-arm /
        wake-prompt / wake-plan / wake-reconcile 的记账与编排、
        wake-retime 的恢复态判定）已随 v2.3 执行面退役——调用即
        RuntimeError（错误 JSON / fail-open 面，退出码语义不变）。
    quota-watcher <repo_root> start|status|stop|once
        Real-Time Quota Watcher 操作面（v2.2 修正计划 C3 wu-22-C3，
        §6/§6.1，runtime.quota.watcher / watcher_store 薄壳）：
        start 以 **sys.executable** 分离派生子进程运行本 CLI 的内部
        serve 形态 `quota-watcher <repo_root> --serve`（隐藏形态，仅
        由 start 派生使用；stdout/stderr 落
        .glm-conductor/quota/watcher.log），派生成功即返回 pid（子
        进程内的单实例锁冲突只落日志，不在 start 同步上报）；status
        读 watcher.json 输出摘要（mode/pid/generation/heartbeat/
        staleness/last_observation）；stop 置 stop_requested 旗标
        （原子写回，单次操作绝不轮询等待退出）；once 前台单次抓取
        （测试/诊断用；锁被活进程新鲜持有时报冲突退出码 1）。watcher
        只写自身状态文件 .glm-conductor/quota/watcher.json，绝不写
        任务 state/journal；第一阶段零模型调用、不 prime、不发
        activation（§6.1 skeleton-first）。
    quota-clock-plan / quota-clock-bind / quota-clock-tick /
            quota-clock-status <repo_root> [...]
        Global Quota Clock 四子命令（v2.3.0 §8，unit v23-w2b）：
        纯规划（零写盘、零 DB、零 state）/ 绑定 automation +
        用户级 state + 首次 retime / Scheduled Task 唯一主入口 /
        绑定健康只读汇总（判定落 clock 纯函数层）。实现已迁
        runtime/commands/quota_clock.py（v2.3.1 Wave 3，unit
        w3-quota-clock-split）——cli 仅存参数校验分支 + 函数内
        import 调用；输出冻结键集、失败 JSON 面 {"status":
        "error" | "not_plannable", ...} 与退出码逐字节不变，
        键集与语义明细见该模块 docstring。
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
        wake-prompt 纯文本同款的单行 JSON 契约显式例外；显式 UTF-8）；
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
    \\uXXXX 转义——管道 / Windows 控制台零编码依赖；例外：wake-prompt
    的成功路径为纯文本 prompt；v2.2 M3 起 quota-observe / quota-phase
    为 ensure_ascii=False（wu-22-03 规格冻结：中文 reason 面向主会话
    直接阅读，显式 UTF-8 落 stdout）；v2.2 M5 起 wake-plan / wake-
    status 同走 ensure_ascii=False（§22.2/§22.3 中文 reason / prompt
    面向主会话直接阅读，同一观测面口径））；stderr 不承载结构化输出。
    v2.3 quota-clock 四子命令的失败路径为自绘 JSON 面 {"status":
    "error" | "not_plannable", "error", ...}（status 键恒在，退出码
    1；参数个数错误仍走 _UsageError → {"error": ...} / 退出码 2 的
    既有惯例）。
    退出码：
      0 = 成功；
      2 = 校验拒绝（用法错误 / 参数值非法 / setter 抛 ValueError /
          save_state 校验闸或状态转换门拒绝——含盘上 state.json 损坏
          的解析拒绝）；
      1 = 异常（任务不存在 / 审查记录被拒 / 已退役执行面的
          RuntimeError / 意外错误；错误 JSON 只含异常类型名与消息，
          供操作者排查）。

依赖方向：
    本模块是薄壳：校验与变换都在 runtime.execution_policy /
    runtime.state / runtime.task 等域模块内，这里只做 argv 解析、
    JSON 输出与退出码映射。插件根经 sys.path 引导（runtime/quota/
    report.py 同款），因此 `python3 plugins/glm-conductor/runtime/
    cli.py ...` 可在仓库根直接运行。

依赖：
    仅 Python 3 标准库（datetime / json / pathlib / sys），零第三方
    依赖。
"""

import datetime
import json
import pathlib
import sys

# 接线插件根以复用 runtime（CLI 脚本与 runtime/ 同插件；quota report 同款）
PLUGIN_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from runtime import execution_policy, state  # noqa: E402
from runtime.quota.parser import QUOTA_STATUSES  # noqa: E402

USAGE = (
    "用法: python3 plugins/glm-conductor/runtime/cli.py "
    "policy-show <repo_root> <task_id> | "
    "policy-set-parallel <repo_root> <task_id> <max_workers> | "
    "policy-set-resume <repo_root> <task_id> <auto_resume> "
    "[max_quota_windows] | "
    "review-record <repo_root> <task_id> <reviewer> <verdict> [note] | "
    "quota-resolve <repo_root> [--force-refresh] | "
    "quota-observe <repo_root> [task_id] | "
    "quota-phase <repo_root> <task_id> | "
    "quota-exhausted <repo_root> <task_id> | "
    "quota-resume <repo_root> <task_id> [status] | "
    "wake-record <repo_root> <task_id> <automation_id> <fires_at> "
    "[--db <path>] | "
    "wake-arm <repo_root> <task_id> <automation_id> [--db <path>] | "
    "wake-prompt <repo_root> <task_id> | "
    "wake-plan <repo_root> <task_id> | "
    "wake-status <repo_root> <task_id> | "
    "wake-retime <repo_root> <task_id> | "
    "wake-reconcile <repo_root> <task_id> <host_status> [observed_at] | "
    "transport-status <repo_root> <task_id> | "
    "quota-watcher <repo_root> start|status|stop|once | "
    "quota-clock-plan <repo_root> | "
    "quota-clock-bind <repo_root> <automation_id> [--db <path>] | "
    "quota-clock-tick <repo_root> | "
    "quota-clock-status <repo_root> | "
    "host-check [--db <path>] | "
    "v24-compile <dag-json> [--task-ref <id>] [--out <path>] | "
    "writer-acquire <repo_root> <task_id> [--run-id <id>] | "
    "writer-release <repo_root> <task_id> [--run-id <id>] [--force] | "
    "writer-show <repo_root> | "
    "v24-record-run <task_ref> <run-id> [--artifact <path>]")

# policy-set-resume 的 max_quota_windows 缺省推导表（§5.4 耦合的
# 最小合法值：until_done 取下界 1，保守不放大）
AUTO_RESUME_DEFAULT_WINDOWS = {
    "manual": 0, "notify": 0, "auto_once": 1, "until_done": 1}


class _UsageError(ValueError):
    """用法 / 子命令 / 参数个数错误——校验拒绝类，退出码 2。"""


class _TaskMissing(Exception):
    """任务不存在（无 state.json）——运行期异常，退出码 1。"""


class _QuotaFlowRejected(Exception):
    """quota 连续性事务被拒（任务缺失 / 状态族不符；commands.wake 同
    名同实现副本共用此退出码语义）——运行期拒绝，退出码 1。"""


class _ReviewRejected(Exception):
    """review-record 被 runtime.task 拒绝（任务缺失 / v2.3 遗留任务 /
    终态冻结 / 先验证后评审顺序闸；ValueError 口径）——运行期拒绝，
    退出码 1。"""


class _QuotaWatcherConflict(Exception):
    """quota-watcher 单实例锁冲突（§6 冻结：现存记录 pid 活着且
    heartbeat 新鲜时，once / serve 拒绝执行）——运行期拒绝，退出码 1。"""


def _emit(payload):
    """向 stdout 写单行 JSON（ensure_ascii=True，契约锚点）。"""
    sys.stdout.write(json.dumps(payload, ensure_ascii=True) + "\n")


def _force_utf8_stdout():
    """把 stdout 调到 UTF-8（wake-prompt 纯文本输出用；quota/report.py
    同款——非 TTY / 测试捕获（StringIO）容错跳过）。"""
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError, OSError):
        pass  # 测试捕获（StringIO）或已被重定向：保持原样


def _emit_utf8(payload):
    """向 stdout 写单行 JSON（ensure_ascii=False；v2.2 M3 观测面
    quota-observe / quota-phase 专用——wu-22-03 规格冻结中文 reason
    不转义、面向主会话直接阅读；显式 UTF-8 落 stdout，Windows 管道
    / 控制台零乱码）。"""
    _force_utf8_stdout()
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _now_iso8601() -> str:
    """当前 UTC 时刻的 ISO8601 字符串（授权 confirmed_at 落笔口径）。"""
    return datetime.datetime.now(
        datetime.timezone.utc).isoformat(timespec="seconds")


def _require_task(repo_root, task_id) -> dict:
    """读取任务 state；不存在 → _TaskMissing（退出码 1）；
    JSON 损坏由 load_state 抛 ValueError（退出码 2：校验/状态层拒绝）。"""
    st = state.load_state(repo_root, task_id)
    if st is None:
        raise _TaskMissing(
            "任务 %s 不存在（%s 下无 state.json），无法操作 "
            "execution_policy" % (task_id, repo_root))
    return st


def _base_policy(task_state) -> dict:
    """取任务的 execution_policy 底块；缺键 / 形状异常（legacy）→
    保守默认块（setter 在完整底块上做局部更新，半块不被放大）。"""
    block = task_state.get("execution_policy")
    if isinstance(block, dict):
        return block
    return execution_policy.default_execution_policy()


def _parse_int(func_name, field, raw):
    """把 CLI 字符串参数解析为 int；失败抛 ValueError（退出码 2）。"""
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise ValueError(
            "%s：%s %r 不是整数" % (func_name, field, raw))


def _policy_show(repo_root, task_id) -> int:
    """policy-show：只读展示，legacy 缺键 → 默认块，不写盘。"""
    st = state.load_state(repo_root, task_id)
    if st is None:
        raise _TaskMissing(
            "任务 %s 不存在（%s 下无 state.json），无法展示 "
            "execution_policy" % (task_id, repo_root))
    block = st.get("execution_policy")
    if isinstance(block, dict):
        _emit({"task_id": st.get("task_id", task_id),
               "policy_source": "state", "execution_policy": block})
    else:
        _emit({"task_id": st.get("task_id", task_id),
               "policy_source": "default",
               "execution_policy":
                   execution_policy.default_execution_policy()})
    return 0


def _policy_set_parallel(repo_root, task_id, raw_max_workers) -> int:
    """policy-set-parallel：用户身份写入并发授权并经 save_state 落盘。"""
    max_workers = _parse_int(
        "policy-set-parallel", "max_workers", raw_max_workers)
    st = _require_task(repo_root, task_id)
    updated = execution_policy.set_parallel_authorization(
        _base_policy(st), max_workers=max_workers, source="user",
        confirmed_at=_now_iso8601())
    st["execution_policy"] = updated
    path = state.save_state(repo_root, st)
    _emit({"task_id": task_id, "policy_source": "state",
           "saved": str(path), "execution_policy": updated})
    return 0


def _policy_set_resume(repo_root, task_id, raw_auto_resume,
                       raw_windows=None) -> int:
    """policy-set-resume：用户身份写入续跑授权并经 save_state 落盘。"""
    func_name = "policy-set-resume"
    auto_resume = raw_auto_resume
    if auto_resume not in execution_policy.AUTO_RESUME_MODES:
        raise ValueError(
            "%s：auto_resume %r 不在合法取值内（%s）"
            % (func_name, auto_resume,
               ", ".join(execution_policy.AUTO_RESUME_MODES)))
    if raw_windows is None:
        max_quota_windows = AUTO_RESUME_DEFAULT_WINDOWS[auto_resume]
    else:
        max_quota_windows = _parse_int(
            func_name, "max_quota_windows", raw_windows)
    st = _require_task(repo_root, task_id)
    updated = execution_policy.set_resume_authorization(
        _base_policy(st), auto_resume=auto_resume,
        max_quota_windows=max_quota_windows, source="user",
        confirmed_at=_now_iso8601())
    st["execution_policy"] = updated
    path = state.save_state(repo_root, st)
    _emit({"task_id": task_id, "policy_source": "state",
           "saved": str(path), "execution_policy": updated})
    return 0


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


def _quota_task_inputs(repo_root, task_id) -> dict:
    """quota-observe / quota-phase 共用的 task 侧输入装配（全部只读）。

    task_id 为 None（quota-observe 无任务形态）→ 保守默认：
    task_active=True、wake_bridge_status="none"、quota_control=None
    （决策层全默认阈值）、max_workers=None（决策层按 1 保守）。

    给定 task_id → 原以 v2.3 执行态族（QUOTA_WAIT_TASK_STATUSES）折算
    task_active——该族词汇随 v2.3 执行面退役（W6），本形态即以
    RuntimeError 明示退役（quota-observe / quota-phase 的无任务形态
    不受影响；quota/continuity 族 Phase 3 收口）。
    """
    if task_id is None:
        return {"task_active": True, "wake_bridge_status": "none",
                "quota_control": None, "max_workers": None}
    raise RuntimeError(
        "v2.3 执行面已退役（执行态族折算随执行运行时删除），"
        "quota-observe / quota-phase 的 task 形态由 Phase 3 收口")


def _snapshot_windows(detail) -> "list | None":
    """从 resolve_quota_detail 的明细 dict 容错取 §27 windows
    （snapshot 缺失 / 形状异常 → None，决策层按空窗口 fail-open）。"""
    snapshot = detail.get("snapshot") if isinstance(detail, dict) else None
    windows = (snapshot.get("windows")
               if isinstance(snapshot, dict) else None)
    return windows if isinstance(windows, list) else None


def _quota_observe(repo_root, task_id=None) -> int:
    """quota-observe：§16.1 观测 dict（v2.2 M3 wu-22-03，observer.
    observe 薄壳）。I/O 全在本层：resolve_quota_detail 读 quota-cache
    （四级层级，缓存过期即走 provider——§6.4 lazy heartbeat 的「进入
    runtime 先判是否刷新」入口）+ state.json 只读装配 task 侧输入 →
    纯决策 → 单行 JSON（ensure_ascii=False）。零 state 写、零
    automation、零 journal 事件；now 由本层注入当前 UTC（observer
    层自身无墙钟依赖）。"""
    from runtime.quota import observer, resolver  # 函数内 import：monkeypatch 友好
    inputs = _quota_task_inputs(repo_root, task_id)
    detail = resolver.resolve_quota_detail(repo_root)
    result = observer.observe(
        provider_status=detail["status"],
        windows=_snapshot_windows(detail),
        quota_control=inputs["quota_control"],
        max_workers=inputs["max_workers"],
        task_active=inputs["task_active"],
        wake_bridge_status=inputs["wake_bridge_status"],
        now=datetime.datetime.now(datetime.timezone.utc))
    _emit_utf8(result)
    return 0


def _quota_phase(repo_root, task_id) -> int:
    """quota-phase：执行相决策 JSON 原样直出（v2.2 M3 wu-22-03，
    control.evaluate_task_quota_phase 薄壳）。与 quota-observe 同源
    输入（resolve_quota_detail + state.json 只读），差异只在输出层
    （§17.1 决策 dict vs §16.1 观测 dict）——观测编排归 observer，
    执行相映射归 control，本层只做 I/O。"""
    from runtime.quota import control, resolver  # 函数内 import：monkeypatch 友好
    inputs = _quota_task_inputs(repo_root, task_id)
    detail = resolver.resolve_quota_detail(repo_root)
    decision = control.evaluate_task_quota_phase(
        provider_status=detail["status"],
        windows=_snapshot_windows(detail),
        quota_control=inputs["quota_control"],
        max_workers=inputs["max_workers"],
        task_active=inputs["task_active"],
        wake_bridge_status=inputs["wake_bridge_status"])
    _emit_utf8(decision)
    return 0


def _quota_exhausted(repo_root, task_id) -> int:
    """quota-exhausted：【已退役】EXHAUSTED 转态链的后端
    （handle_quota_exhausted 编排体）随 v2.3 执行面退役（W6）——子
    命令保留注册（quota 族 Phase 3 收口），调用即 RuntimeError →
    退出码 1（错误 JSON）。"""
    raise RuntimeError(
        "v2.3 执行面已退役（quota-exhausted 的转态编排随执行运行时"
        "删除），本子命令由 Phase 3 收口")


def _quota_resume(repo_root, task_id, raw_status=None) -> int:
    """quota-resume：【已退役】恢复编排后端（resume_from_quota）随
    v2.3 执行面退役（W6）——子命令保留注册，status 词汇闸仍在
    （非法 → 退出码 2），其余调用即 RuntimeError → 退出码 1（错误
    JSON）。"""
    if raw_status is not None and raw_status not in QUOTA_STATUSES:
        raise ValueError(
            "quota-resume：status %r 不在合法取值内（%s）"
            % (raw_status, ", ".join(QUOTA_STATUSES)))
    raise RuntimeError(
        "v2.3 执行面已退役（quota-resume 的恢复编排随执行运行时删除），"
        "本子命令由 Phase 3 收口")


# —— v2.2 修正计划 C3（wu-22-C3）：Real-Time Quota Watcher ——

def _quota_watcher_start(repo_root) -> int:
    """quota-watcher start：以 sys.executable 分离派生 serve 子进程
    （§6.2 工程约束：subprocess 一律 sys.executable；用户文档仍写
    python3）。子进程运行本 CLI 的内部隐藏形态
    `quota-watcher <repo_root> --serve`，stdout/stderr 落
    .glm-conductor/quota/watcher.log；派生成功即返回 pid（子进程内的
    单实例锁冲突只落日志——start 是 fire-and-forget，状态由 status
    观测）。Windows 用 DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP，
    POSIX 用 start_new_session；不引入 Windows service / autostart
    （§6.1 冻结不做项）。"""
    import os
    import subprocess
    from runtime.quota import watcher_store
    cli_path = str(pathlib.Path(__file__).resolve())
    log_path = watcher_store.watcher_log_path(repo_root)
    state_path = watcher_store.watcher_state_path(repo_root)
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    argv = [sys.executable, cli_path, "quota-watcher", str(repo_root),
            "--serve"]
    with open(log_path, "ab") as log_handle:
        kwargs = {}
        if os.name == "nt":
            kwargs["creationflags"] = (subprocess.DETACHED_PROCESS
                                       | subprocess.CREATE_NEW_PROCESS_GROUP)
        else:
            kwargs["start_new_session"] = True
        process = subprocess.Popen(
            argv, stdin=subprocess.DEVNULL, stdout=log_handle,
            stderr=log_handle, close_fds=True, **kwargs)
    _emit({"started": True, "pid": process.pid, "log": log_path,
           "state": state_path,
           "serve": "quota-watcher <repo_root> --serve（内部隐藏形态，"
                    "由 start 派生；单实例锁冲突详情落 watcher.log）"})
    return 0


def _quota_watcher_status(repo_root) -> int:
    """quota-watcher status：读 watcher.json 输出摘要（零写副作用；
    无记录不是错误——active=false，退出码 0）。staleness =
    heartbeat_age_seconds 相对 watcher_store 默认新鲜阈值（180 秒）
    的新鲜性二标注。"""
    from runtime.quota import watcher_store
    record = watcher_store.read_watcher_state(repo_root)
    state_path = watcher_store.watcher_state_path(repo_root)
    if record is None:
        _emit_utf8({"active": False, "record": None, "state": state_path})
        return 0
    stale_seconds = watcher_store.DEFAULT_HEARTBEAT_STALE_SECONDS
    age = watcher_store.heartbeat_age_seconds(record)
    _emit_utf8({
        "active": record.get("pid") is not None,
        "mode": record.get("mode"),
        "pid": record.get("pid"),
        "generation": record.get("generation"),
        "provider_identity_hash": record.get("provider_identity_hash"),
        "started_at": record.get("started_at"),
        "heartbeat_at": record.get("heartbeat_at"),
        "heartbeat_age_seconds": age,
        "heartbeat_stale": (None if age is None
                            else age > stale_seconds),
        "stop_requested": record.get("stop_requested"),
        "next_poll_at": record.get("next_poll_at"),
        "last_observation": record.get("last_observation"),
        "state": state_path,
    })
    return 0


def _quota_watcher_stop(repo_root) -> int:
    """quota-watcher stop：置 stop_requested 旗标（原子写回）；单次
    操作，绝不轮询等待退出（消费由 watcher 循环在下一 wake 完成）。
    无记录 → stop_requested=false 幂等成功（退出码 0，非错误）。"""
    from runtime.quota import watcher_store
    record = watcher_store.request_stop(repo_root)
    if record is None:
        _emit_utf8({"stop_requested": False,
                    "reason": "无 watcher 状态记录（watcher.json 不存在），"
                              "无可停止对象",
                    "state": watcher_store.watcher_state_path(repo_root)})
        return 0
    _emit_utf8({"stop_requested": True, "pid": record.get("pid"),
                "generation": record.get("generation"),
                "note": "旗标已原子置位；watcher 将在下一 wake 优雅退出"
                        "（本命令不等待）",
                "state": watcher_store.watcher_state_path(repo_root)})
    return 0


def _quota_watcher_once(repo_root) -> int:
    """quota-watcher once：前台单次抓取（watcher.run_once 薄壳，测试/
    诊断用）。锁被活进程新鲜持有（§6）→ _QuotaWatcherConflict（退出
    码 1）；否则输出一次观察摘要（epoch_id / probe_boundary_at 来自
    epoch.evaluate_epoch 对 §27 windows 的折算）。"""
    from runtime.quota import watcher, watcher_store  # 函数内 import：monkeypatch 友好
    result = watcher.run_once(repo_root)
    if not result["ran"]:
        raise _QuotaWatcherConflict(
            "quota-watcher once：单实例锁被活进程持有（pid=%r，"
            "heartbeat_at=%r 仍新鲜），未执行抓取" % (
                (result["conflict"] or {}).get("pid"),
                (result["conflict"] or {}).get("heartbeat_at")))
    record = result["record"] or {}
    observation = record.get("last_observation") or {}
    _emit_utf8({"ran": True, "mode": record.get("mode"),
                "generation": record.get("generation"),
                "status": observation.get("status"),
                "source": observation.get("source"),
                "epoch_id": observation.get("epoch_id"),
                "probe_boundary_at": observation.get("probe_boundary_at"),
                "executable": observation.get("executable"),
                "observed_at": observation.get("observed_at"),
                "error": observation.get("error"),
                "state": watcher_store.watcher_state_path(repo_root)})
    return 0


def _quota_watcher_serve(repo_root) -> int:
    """quota-watcher <repo_root> --serve：内部隐藏形态，仅由 start 派生
    使用（文档注明；不由操作者直接调用）。acquire 冲突 → 退出码 1
    （详情落 stderr → watcher.log）；graceful stop → 退出码 0。"""
    from runtime.quota import watcher
    return watcher.serve(repo_root)


# —— v2.3.0（v23-w2b）：Global Quota Clock ——

# —— v2.3.0（v23-w3）：Task Wake Bridge fire 后锚点偏移常量 ——
# wake-retime 与 quota-clock 全部处理函数/常量已迁 runtime/commands/
# （v2.3.1 W3，unit w3-quota-clock-split / w3-wake-split；W4 测试收敛
# 将 WAKE_RETIME_* 常量锚点改指 commands.wake 后，cli.py 内的常量与
# _clock_now_ms 副本随之删除——依赖方向恒为 cli → commands）。
# 偏移常量语义（park=365 天 / retry=5 分钟）见 commands/wake.py 冻结注释。


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
        _force_utf8_stdout()  # 生成源含中文：显式 UTF-8（wake-prompt 同款）
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
    if cmd == "policy-show":
        if len(rest) != 2:
            raise _UsageError(
                "policy-show 需要 <repo_root> <task_id> 两个参数。" + USAGE)
        return _policy_show(rest[0], rest[1])
    if cmd == "policy-set-parallel":
        if len(rest) != 3:
            raise _UsageError(
                "policy-set-parallel 需要 <repo_root> <task_id> "
                "<max_workers> 三个参数。" + USAGE)
        return _policy_set_parallel(rest[0], rest[1], rest[2])
    if cmd == "policy-set-resume":
        if len(rest) not in (3, 4):
            raise _UsageError(
                "policy-set-resume 需要 <repo_root> <task_id> "
                "<auto_resume> [max_quota_windows] 三或四个参数。" + USAGE)
        return _policy_set_resume(
            rest[0], rest[1], rest[2], rest[3] if len(rest) == 4 else None)
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
    if cmd == "quota-observe":
        if len(rest) not in (1, 2):
            raise _UsageError(
                "quota-observe 需要 <repo_root> [task_id] 一或两个参数。"
                + USAGE)
        return _quota_observe(rest[0], rest[1] if len(rest) == 2 else None)
    if cmd == "quota-phase":
        if len(rest) != 2:
            raise _UsageError(
                "quota-phase 需要 <repo_root> <task_id> 两个参数。" + USAGE)
        return _quota_phase(rest[0], rest[1])
    if cmd == "quota-exhausted":
        if len(rest) != 2:
            raise _UsageError(
                "quota-exhausted 需要 <repo_root> <task_id> 两个参数。"
                + USAGE)
        return _quota_exhausted(rest[0], rest[1])
    if cmd == "quota-resume":
        if len(rest) not in (2, 3):
            raise _UsageError(
                "quota-resume 需要 <repo_root> <task_id> [status] 两或三"
                "个参数。" + USAGE)
        return _quota_resume(
            rest[0], rest[1], rest[2] if len(rest) == 3 else None)
    if cmd == "wake-record":
        if len(rest) not in (4, 6):
            raise _UsageError(
                "wake-record 需要 <repo_root> <task_id> <automation_id> "
                "<fires_at> [--db <path>] 四或六个参数。" + USAGE)
        db_path = None
        if len(rest) == 6:
            if rest[4] != "--db":
                raise _UsageError(
                    "wake-record 的可选参数只接受 --db <path>。" + USAGE)
            db_path = rest[5]
        from runtime.commands import wake  # 函数内 import：monkeypatch 友好
        return wake._wake_record(rest[0], rest[1], rest[2], rest[3],
                                 db_path=db_path)
    if cmd == "wake-arm":
        if len(rest) not in (3, 5):
            raise _UsageError(
                "wake-arm 需要 <repo_root> <task_id> <automation_id> "
                "[--db <path>] 三或五个参数。" + USAGE)
        db_path = None
        if len(rest) == 5:
            if rest[3] != "--db":
                raise _UsageError(
                    "wake-arm 的可选参数只接受 --db <path>。" + USAGE)
            db_path = rest[4]
        from runtime.commands import wake  # 函数内 import：monkeypatch 友好
        return wake._wake_arm(rest[0], rest[1], rest[2], db_path=db_path)
    if cmd == "wake-prompt":
        if len(rest) != 2:
            raise _UsageError(
                "wake-prompt 需要 <repo_root> <task_id> 两个参数。" + USAGE)
        from runtime.commands import wake  # 函数内 import：monkeypatch 友好
        return wake._wake_prompt(rest[0], rest[1])
    if cmd == "wake-plan":
        if len(rest) != 2:
            raise _UsageError(
                "wake-plan 需要 <repo_root> <task_id> 两个参数。" + USAGE)
        from runtime.commands import wake  # 函数内 import：monkeypatch 友好
        return wake._wake_plan(rest[0], rest[1])
    if cmd == "wake-status":
        if len(rest) != 2:
            raise _UsageError(
                "wake-status 需要 <repo_root> <task_id> 两个参数。" + USAGE)
        from runtime.commands import wake  # 函数内 import：monkeypatch 友好
        return wake._wake_status(rest[0], rest[1])
    if cmd == "wake-retime":
        if len(rest) != 2:
            raise _UsageError(
                "wake-retime 需要 <repo_root> <task_id> 两个参数。" + USAGE)
        from runtime.commands import wake  # 函数内 import：monkeypatch 友好
        return wake._wake_retime(rest[0], rest[1])
    if cmd == "wake-reconcile":
        if len(rest) not in (3, 4):
            raise _UsageError(
                "wake-reconcile 需要 <repo_root> <task_id> <host_status> "
                "[observed_at] 三或四个参数。" + USAGE)
        from runtime.commands import wake  # 函数内 import：monkeypatch 友好
        return wake._wake_reconcile(
            rest[0], rest[1], rest[2],
            rest[3] if len(rest) == 4 else None)
    if cmd == "transport-status":
        if len(rest) != 2:
            raise _UsageError(
                "transport-status 需要 <repo_root> <task_id> 两个参数。"
                + USAGE)
        from runtime.commands import wake  # 函数内 import：monkeypatch 友好
        return wake._transport_status(rest[0], rest[1])
    if cmd == "quota-watcher":
        if len(rest) != 2:
            raise _UsageError(
                "quota-watcher 需要 <repo_root> start|status|stop|once"
                " 两个参数（--serve 为 start 派生的内部隐藏形态）。"
                + USAGE)
        action = rest[1]
        if action == "start":
            return _quota_watcher_start(rest[0])
        if action == "status":
            return _quota_watcher_status(rest[0])
        if action == "stop":
            return _quota_watcher_stop(rest[0])
        if action == "once":
            return _quota_watcher_once(rest[0])
        if action == "--serve":  # 内部隐藏形态：仅由 start 派生使用
            return _quota_watcher_serve(rest[0])
        raise _UsageError(
            "quota-watcher 的动作只接受 start / status / stop / once"
            "（--serve 为内部隐藏形态），得到 %r。" % action + USAGE)
    if cmd == "quota-clock-plan":
        if len(rest) != 1:
            raise _UsageError(
                "quota-clock-plan 需要 <repo_root> 一个参数。" + USAGE)
        from runtime.commands import quota_clock  # 函数内 import：monkeypatch 友好
        return quota_clock._quota_clock_plan(rest[0])
    if cmd == "quota-clock-bind":
        if len(rest) not in (2, 4):
            raise _UsageError(
                "quota-clock-bind 需要 <repo_root> <automation_id> "
                "[--db <path>] 两或四个参数。" + USAGE)
        db_path = None
        if len(rest) == 4:
            if rest[2] != "--db":
                raise _UsageError(
                    "quota-clock-bind 的可选参数只接受 --db <path>。"
                    + USAGE)
            db_path = rest[3]
        from runtime.commands import quota_clock  # 函数内 import：monkeypatch 友好
        return quota_clock._quota_clock_bind(rest[0], rest[1],
                                             db_path=db_path)
    if cmd == "quota-clock-tick":
        if len(rest) != 1:
            raise _UsageError(
                "quota-clock-tick 需要 <repo_root> 一个参数。" + USAGE)
        from runtime.commands import quota_clock  # 函数内 import：monkeypatch 友好
        return quota_clock._quota_clock_tick(rest[0])
    if cmd == "quota-clock-status":
        if len(rest) != 1:
            raise _UsageError(
                "quota-clock-status 需要 <repo_root> 一个参数。" + USAGE)
        from runtime.commands import quota_clock  # 函数内 import：monkeypatch 友好
        return quota_clock._quota_clock_status(rest[0])
    if cmd == "host-check":
        if len(rest) not in (0, 2):
            raise _UsageError(
                "host-check 无位置参数，可选 [--db <path>]。" + USAGE)
        db_path = None
        if len(rest) == 2:
            if rest[0] != "--db":
                raise _UsageError(
                    "host-check 的可选参数只接受 --db <path>。" + USAGE)
            db_path = rest[1]
        from runtime.commands import host  # 函数内 import：monkeypatch 友好
        return host.host_check(db_path=db_path)
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
    ValueError（用法 / 参数值 / setter / save_state 校验栈）→ 2；
    _TaskMissing（任务不存在）/ _ReviewRejected（审查记录被
    runtime.task 顺序与状态闸拒绝）/ _QuotaFlowRejected（quota 连续
    性事务被拒）/ _QuotaWatcherConflict（quota-watcher 单实例锁冲突）
    → 1；commands.wake 的 _TaskMissing / _QuotaFlowRejected 同实现
    副本（v2.3.1 W3b wake 八子命令迁移：原件为多命令组共用留在本
    模块，commands.wake 依模板不回 import 本模块，故两处同名词一并
    捕获，输出与退出码等价）→ 1；已退役执行面的 RuntimeError 与其余
    意外异常 → 1（错误 JSON 含异常类型名，stdout 契约不破）。
    """
    args = list(sys.argv[1:]) if argv is None else list(argv)
    from runtime.commands import wake as _wake_commands  # 函数内 import
    try:
        return _dispatch(args)
    except ValueError as exc:  # 含 _UsageError：校验拒绝类
        _emit({"error": str(exc)})
        return 2
    except (_TaskMissing, _ReviewRejected, _QuotaFlowRejected,
            _QuotaWatcherConflict,
            _wake_commands._TaskMissing,        # v2.3.1 W3b：commands.
            _wake_commands._QuotaFlowRejected) as exc:  # wake 同实现副本
        _emit({"error": str(exc)})
        return 1
    except Exception as exc:  # 已退役执行面的 RuntimeError + 意外兜底
        _emit({"error": "%s: %s" % (type(exc).__name__, exc)})
        return 1


if __name__ == "__main__":
    sys.exit(main())
