#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GLM Conductor v2.1 runtime CLI（M1 policy + M2 permit + M3 agent-runs）。

职责：
    以单行 JSON stdout 提供 execution_policy 三个子命令（M1）、
    dispatch permit 三个子命令（M2 前半，runtime.dispatch_wave 的人工
    操作面）与 agent run 账本只读查询（M3 前半，runtime.agent_run 纯读
    账本面）。M1 只落数据层与 CLI 骨架——policy 部分不接线任何 hook /
    task_manager 消费方（那是 M2+ 的事）；permit 部分是原语层的薄壳
    （签发仍归 task_manager.prepare_dispatch，这里只做 list / show /
    consume）；agent-runs 是账本/档案的纯读薄壳（零写副作用，任务不
    存在时账本为空数组——journal 是唯一真相源，不做任务存在闸）。

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
    permits <repo_root> <task_id>
        列出任务全部活跃 dispatch permit（v2.1 M2，runtime.
        dispatch_wave.list_permits——按 permit_id 排序；无 permit 输出
        空列表形态）。只读。
    permit-show <repo_root> <task_id> <permit_id>
        展示单张 permit（load_permit 原样 dict）；不存在 / 已消费 /
        已失效 → 退出码 1。
    permit-consume <repo_root> <task_id> <permit_id>
        消费 permit（原子 rename 防重放）；成功输出 consumed=true，
        无活跃 permit 可消费 → 退出码 1（fail-closed，不做静默空操作）。
    agent-runs <repo_root> <task_id> [unit]
        agent run 账本只读查询（v2.1 M3 前半，runtime.agent_run）。
        缺省 unit → 输出 list_agent_runs 的 run 记录数组（journal 聚合，
        文件时间序；无 agent_launched/agent_dispatch_failed 事件输出
        空数组）；给定 unit → 输出 run_lifecycle 单元视角汇总 dict
        （launch/失败计数 + 最后已知 agent_id 的原生档案观察 +
        possibly_zombie/archived_terminal 僵尸语义二标注，§7.3：
        status=="running" 绝不解读为存活）。纯读：不写 journal /
        state / 档案；任务不存在同空账本（退出码仍 0）。
    wave-prepare <repo_root> <task_id> [quota_status] [max_workers]
        批量派发准备（v2.1 M4 wu-21-08，task_manager.
        prepare_dispatch_wave 薄壳）：一次调用完成「决策 → 全量租约 →
        wave 记录 → 批量 permit → journal」。成功输出 wave_id / units /
        worker_budget / permits（permit dict 列表）/ markers
        （GLM_CONDUCTOR_DISPATCH=<permit_id>，可直接放进 Agent prompt）/
        deferred / waiting_quota；任务缺失或决策未批准（TaskManagerError）
        → 退出码 1；quota_status / max_workers 非法 → 退出码 2。
    wave-show <repo_root> <task_id> [wave_id]
        wave 记录只读查询（v2.1 M4 wu-21-08）。缺省 wave_id → 输出
        {"task_id", "waves": [...]}（无 waves 键输出空数组）；给定
        wave_id → 输出该 wave 记录原样 dict；wave 不存在或任务不存在
        → 退出码 1。

输出与退出码契约：
    stdout 恒为单行 JSON（json.dumps(..., ensure_ascii=True)，中文以
    \\uXXXX 转义——管道 / Windows 控制台零编码依赖）；stderr 不承载
    结构化输出。退出码：
      0 = 成功；
      2 = 校验拒绝（用法错误 / 参数值非法 / setter 抛 ValueError /
          save_state 校验闸或状态转换门拒绝——含盘上 state.json 损坏
          的解析拒绝）；
      1 = 异常（任务不存在 / 意外错误；错误 JSON 只含异常类型名与
          消息，供操作者排查）。

依赖方向：
    本模块是薄壳：校验与变换都在 runtime.execution_policy /
    runtime.state 内，这里只做 argv 解析、JSON 输出与退出码映射。
    插件根经 sys.path 引导（runtime/quota/report.py 同款），因此
    `python3 plugins/glm-conductor/runtime/cli.py ...` 可在仓库根
    直接运行。

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

from runtime import agent_run, dispatch_wave, execution_policy, state  # noqa: E402
from runtime.quota.parser import QUOTA_STATUSES  # noqa: E402

USAGE = (
    "用法: python3 plugins/glm-conductor/runtime/cli.py "
    "policy-show <repo_root> <task_id> | "
    "policy-set-parallel <repo_root> <task_id> <max_workers> | "
    "policy-set-resume <repo_root> <task_id> <auto_resume> "
    "[max_quota_windows] | "
    "permits <repo_root> <task_id> | "
    "permit-show <repo_root> <task_id> <permit_id> | "
    "permit-consume <repo_root> <task_id> <permit_id> | "
    "agent-runs <repo_root> <task_id> [unit] | "
    "wave-prepare <repo_root> <task_id> [quota_status] [max_workers] | "
    "wave-show <repo_root> <task_id> [wave_id]")

# policy-set-resume 的 max_quota_windows 缺省推导表（§5.4 耦合的
# 最小合法值：until_done 取下界 1，保守不放大）
AUTO_RESUME_DEFAULT_WINDOWS = {
    "manual": 0, "notify": 0, "auto_once": 1, "until_done": 1}


class _UsageError(ValueError):
    """用法 / 子命令 / 参数个数错误——校验拒绝类，退出码 2。"""


class _TaskMissing(Exception):
    """任务不存在（无 state.json）——运行期异常，退出码 1。"""


class _PermitMissing(Exception):
    """permit 不存在（或已消费 / 已失效，load 按不存在处理）——运行期
    异常，退出码 1（fail-closed：consume 空操作显式报错而非静默成功）。"""


class _WaveMissing(Exception):
    """wave 记录不存在——运行期异常，退出码 1。"""


class _WaveRejected(Exception):
    """wave 准备被派发事务层拒绝（任务缺失 / 决策未批准 /
    TaskManagerError）——运行期拒绝，退出码 1（区别于参数值非法的
    退出码 2）。"""


def _emit(payload):
    """向 stdout 写单行 JSON（ensure_ascii=True，契约锚点）。"""
    sys.stdout.write(json.dumps(payload, ensure_ascii=True) + "\n")


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


def _require_task_for_permit(repo_root, task_id) -> dict:
    """permit 子命令共用的任务存在闸：不存在 → _TaskMissing（退出码 1）；
    JSON 损坏由 load_state 抛 ValueError（退出码 2）。先于任何 permit
    I/O 检查——拼错 task_id 得到「任务不存在」而非空列表假象。"""
    st = state.load_state(repo_root, task_id)
    if st is None:
        raise _TaskMissing(
            "任务 %s 不存在（%s 下无 state.json），无法操作 dispatch "
            "permit" % (task_id, repo_root))
    return st


def _permits(repo_root, task_id) -> int:
    """permits：列出任务全部活跃 permit（list_permits 排序确定性）。"""
    _require_task_for_permit(repo_root, task_id)
    _emit({"task_id": task_id,
           "permits": dispatch_wave.list_permits(repo_root, task_id)})
    return 0


def _permit_show(repo_root, task_id, permit_id) -> int:
    """permit-show：展示单张活跃 permit（原样 dict）。"""
    _require_task_for_permit(repo_root, task_id)
    permit = dispatch_wave.load_permit(repo_root, task_id, permit_id)
    if permit is None:
        raise _PermitMissing(
            "permit %s 不存在（或已消费 / 已失效），无法展示" % (permit_id,))
    _emit(permit)
    return 0


def _permit_consume(repo_root, task_id, permit_id) -> int:
    """permit-consume：消费 permit（原子 rename 防重放）。"""
    _require_task_for_permit(repo_root, task_id)
    if not dispatch_wave.consume_permit(repo_root, task_id, permit_id):
        raise _PermitMissing(
            "permit %s 不存在（或已消费 / 已失效），无可消费的活跃 "
            "permit" % (permit_id,))
    _emit({"task_id": task_id, "permit_id": permit_id, "consumed": True})
    return 0


def _agent_runs(repo_root, task_id, unit=None) -> int:
    """agent-runs：agent run 账本只读查询（薄壳，零写副作用）。

    unit 缺省 → 输出 list_agent_runs 数组（journal 聚合，空账本即空
    数组——任务不存在不是错误，journal 是账本唯一真相源）；unit 给定
    → 输出 run_lifecycle 汇总 dict（含 §7.3 僵尸语义二标注）。
    """
    if unit is None:
        _emit(agent_run.list_agent_runs(repo_root, task_id))
    else:
        _emit(agent_run.run_lifecycle(repo_root, task_id, unit))
    return 0


# —— v2.1 M4（wu-21-08）：dispatch wave 批量事务与只读查询 ——

def _wave_prepare(repo_root, task_id, raw_quota_status=None,
                  raw_max_workers=None) -> int:
    """wave-prepare：批量派发准备（task_manager.prepare_dispatch_wave
    薄壳）。参数值非法 → ValueError（退出码 2）；任务缺失 / 决策未
    批准（TaskManagerError）→ _WaveRejected（退出码 1）；成功输出
    wave_id / units / worker_budget / permits / markers / deferred /
    waiting_quota——markers 为 GLM_CONDUCTOR_DISPATCH=<permit_id>
    文本，操作者可直接放进 Agent prompt。"""
    from runtime import task_manager
    quota_status = ("AVAILABLE" if raw_quota_status is None
                    else raw_quota_status)
    if quota_status not in QUOTA_STATUSES:
        raise ValueError(
            "wave-prepare：quota_status %r 不在合法取值内（%s）"
            % (quota_status, ", ".join(QUOTA_STATUSES)))
    max_workers = None
    if raw_max_workers is not None:
        max_workers = _parse_int("wave-prepare", "max_workers",
                                 raw_max_workers)
    try:
        result = task_manager.prepare_dispatch_wave(
            repo_root, task_id, quota_status=quota_status,
            max_workers=max_workers)
    except task_manager.TaskManagerError as exc:
        raise _WaveRejected(str(exc)) from exc
    _emit({
        "task_id": task_id,
        "wave_id": result["wave_id"],
        "units": result["units"],
        "worker_budget": result["worker_budget"],
        "permits": result["permits"],
        "markers": [dispatch_wave.marker_for(permit["permit_id"])
                    for permit in result["permits"]],
        "deferred": result["deferred"],
        "waiting_quota": result["waiting_quota"],
    })
    return 0


def _waves_of(task_state) -> list:
    """容错读取 state dict 的 dispatch.waves（缺失/形状异常按空处理）。"""
    dispatch_block = (task_state.get("dispatch")
                      if isinstance(task_state, dict) else None)
    waves = (dispatch_block.get("waves")
             if isinstance(dispatch_block, dict) else None)
    return waves if isinstance(waves, list) else []


def _wave_show(repo_root, task_id, wave_id=None) -> int:
    """wave-show：wave 记录只读查询。缺省 wave_id → 全部 waves 数组；
    给定 wave_id → 该 wave 记录原样 dict（照 permit-show 直出风格）；
    wave 不存在 → _WaveMissing（退出码 1）。"""
    st = _require_task_for_permit(repo_root, task_id)
    waves = _waves_of(st)
    if wave_id is None:
        _emit({"task_id": task_id, "waves": waves})
        return 0
    for entry in waves:
        if isinstance(entry, dict) and entry.get("wave_id") == wave_id:
            _emit(entry)
            return 0
    raise _WaveMissing(
        "wave %s 不存在（任务 %s 无该 wave 记录）" % (wave_id, task_id))


def _manifest_show(repo_root, task_id) -> int:
    """manifest-show：Resume Manifest 只读查询（薄壳）。

    输出 {"manifest": <dict|null>}——manifest 缺失 / 损坏均输出 null
    （read 侧永不抛）；任务是否存在不设闸（manifest 是派生物，缺失
    即无快照，不是错误）。
    """
    from runtime import resume_manifest
    _emit({"manifest": resume_manifest.read_resume_manifest(
        repo_root, task_id)})
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
    if cmd == "permits":
        if len(rest) != 2:
            raise _UsageError(
                "permits 需要 <repo_root> <task_id> 两个参数。" + USAGE)
        return _permits(rest[0], rest[1])
    if cmd == "permit-show":
        if len(rest) != 3:
            raise _UsageError(
                "permit-show 需要 <repo_root> <task_id> <permit_id> "
                "三个参数。" + USAGE)
        return _permit_show(rest[0], rest[1], rest[2])
    if cmd == "permit-consume":
        if len(rest) != 3:
            raise _UsageError(
                "permit-consume 需要 <repo_root> <task_id> <permit_id> "
                "三个参数。" + USAGE)
        return _permit_consume(rest[0], rest[1], rest[2])
    if cmd == "agent-runs":
        if len(rest) not in (2, 3):
            raise _UsageError(
                "agent-runs 需要 <repo_root> <task_id> [unit] 两或三个"
                "参数。" + USAGE)
        return _agent_runs(
            rest[0], rest[1], rest[2] if len(rest) == 3 else None)
    if cmd == "wave-prepare":
        if len(rest) not in (2, 3, 4):
            raise _UsageError(
                "wave-prepare 需要 <repo_root> <task_id> [quota_status] "
                "[max_workers] 两到四个参数。" + USAGE)
        return _wave_prepare(
            rest[0], rest[1],
            rest[2] if len(rest) >= 3 else None,
            rest[3] if len(rest) >= 4 else None)
    if cmd == "wave-show":
        if len(rest) not in (2, 3):
            raise _UsageError(
                "wave-show 需要 <repo_root> <task_id> [wave_id] 两或三个"
                "参数。" + USAGE)
        return _wave_show(
            rest[0], rest[1], rest[2] if len(rest) == 3 else None)
    if cmd == "manifest-show":
        if len(rest) != 2:
            raise _UsageError(
                "manifest-show 需要 <repo_root> <task_id> 两个参数。"
                + USAGE)
        return _manifest_show(rest[0], rest[1])
    raise _UsageError("未知子命令 %r。" % cmd + USAGE)


def main(argv=None) -> int:
    """CLI 入口：返回退出码（0 成功 / 2 校验拒绝 / 1 异常）。

    argv 缺省取 sys.argv[1:]；测试可直接传列表调用。异常映射：
    ValueError（用法 / 参数值 / setter / save_state 校验栈）→ 2；
    _TaskMissing（任务不存在）/ _PermitMissing（permit 不存在或已
    消费 / 已失效）/ _WaveMissing（wave 记录不存在）/ _WaveRejected
    （wave 准备被派发事务层拒绝）→ 1；其余意外异常 → 1（错误 JSON 含
    异常类型名，stdout 契约不破）。
    """
    args = list(sys.argv[1:]) if argv is None else list(argv)
    try:
        return _dispatch(args)
    except ValueError as exc:  # 含 _UsageError：校验拒绝类
        _emit({"error": str(exc)})
        return 2
    except (_TaskMissing, _PermitMissing, _WaveMissing,
            _WaveRejected) as exc:
        _emit({"error": str(exc)})
        return 1
    except Exception as exc:  # 意外异常兜底：stdout 契约不破
        _emit({"error": "%s: %s" % (type(exc).__name__, exc)})
        return 1


if __name__ == "__main__":
    sys.exit(main())
