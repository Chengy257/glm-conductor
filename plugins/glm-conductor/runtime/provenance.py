#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GLM Conductor v2.1 验证 / 审查溯源层（v2.1 M5/M6，wu-21-12/wu-21-13，
§16.3/§16.4/§16.5）。

职责：
    把「evidence 是 runtime 实际观察到的」从约定升级为机械事实（D4
    锁定：runtime 受限主动执行）。此前 record_unit_verification /
    record_verification 只登记主会话口头声称的验证结果——命令是否真
    跑过、exit code 是多少、跑完之后仓库是什么状态，全部无法机械证明。
    本模块提供三个 runtime API，把「执行验证命令 / 申报审查裁决 +
    观察结果 + 落 durable 凭证 + 接线既有证据流」固化为一次调用：
      - verify_unit()：单元作用域——只执行该单元 state 中已声明的
        required verification command，产出 receipt 并在 exit 0 时写
        单元级验证证据（task_manager.record_unit_verification——RB-1
        完成证据门直接消费，verify_unit 之后 finish_unit 应能直接
        通过）；
      - verify_task()：任务作用域——白名单 = state.verification.required
        （完成门口径），指纹与完成门同一入口
        （fingerprint.task_fingerprint），exit 0 时经
        state.record_verification + save_state 落账；
      - run_review()（wu-21-13，§16.5）：审查作用域——主会话把 reviewer
        对已发生审查的裁决（verdict + reviewer + tool_use_id）申报进来，
        runtime 绑定终指纹（与完成门同一入口）落 durable review
        receipt + journal review_receipt 事件 + state.review 同步
        （完成门快速路径字段与 receipt 恒一致）。Stop 完成门的审查
        检查（wu-21-13 升级）只认 fresh ship review receipt——receipt
        是审查裁决的唯一权威，state.review 只是同一调用的镜像。

四道确定性保障（D4）：
      1. 白名单闸：runtime 只执行 state 中已声明的 required 验证命令
         ——显式传入的 command 必须逐字 ∈ required 清单，否则
         ProvenanceError（runtime 绝不执行账本之外的任何命令）；
      2. policy 闸：每条命令执行前先过 runtime.policy.classify_bash
         ——命中 ("deny", _) / ("ask", _) 一律 ProvenanceError 拒绝
         执行；None（无规则命中）与 ("allow", _) 放行。policy 优先于
         白名单指的是两道闸都必须通过（命令在白名单里但被 policy 拒
         绝，同样不执行），不是白名单可以豁免 policy；
      3. 零 TOCTOU 同刻指纹：进程退出后、任何其他仓库操作前，立即
         计算证据指纹（单元作用域口径与 RB-1 完成证据门完全一致，
         经共享谓词 reconcile.fresh_unit_verification；任务作用域与
         完成门同一入口 fingerprint.task_fingerprint）——exit code 与
         指纹绑定同一刻的仓库状态，之间不存在被插队改写的窗口；
      4. durable receipt：每次观察落一份 JSON 凭证（tmp + os.replace
         原子写）+ 一条 journal verification_receipt 事件（同字段），
         runner 恒为 "glm-conductor-runtime"——「runtime 亲眼所见」
         成为可审计的落盘事实，而不是会话记忆。

receipt 契约（键名冻结，wu-21-12 设计决策；review receipt 由 wu-21-13
同构落定）：
    {"kind": "verification", "scope": "unit" | "task",
     "unit": <uid>（unit 作用域）| "task": <task_id>（task 作用域）,
     "command": <str>, "exit_code": <int | None>,
     "fingerprint": <str | None>, "observed_at": <ISO毫秒Z>,
     "runner": "glm-conductor-runtime", "duration_ms": <int>,
     "stdout_excerpt": <str>, "stderr_excerpt": <str>}
    超时凭证追加 "timeout": true 且 exit_code 置 None；非超时凭证不
    含该键。凭证文件名：
    <task_dir>/receipts/verification-<observed_at紧凑串>-<hash8>.json
    （hash8 = 凭证规范化 JSON 内容的 sha256 前 8 位；同毫秒多条凭证
    靠它区分）。stdout/stderr 截断保留前 4096 字符入 excerpt（可空
    串；超时凭证的 excerpt 恒为空串——TimeoutExpired 的部分输出不
    采信）。

review receipt 契约（键名冻结，wu-21-13 §16.5；note 恒入键）：
    {"kind": "review", "reviewer": <str>, "route": <None | str>,
     "verdict": <state.REVIEW_VERDICTS 之一>, "fingerprint": <str>,
     "tool_use_id": <str>, "observed_at": <ISO毫秒Z>,
     "runner": "glm-conductor-runtime", "note": <str | None>}
    凭证文件名：<task_dir>/receipts/review-<observed_at紧凑串>-<hash8>
    .json（与验证凭证同目录、同原子写、同 hash8 区分规则）。

证据接线（与既有口径正交互补）：
    - verify_unit exit_code == 0 → 惰性 import task_manager 调
      record_unit_verification(repo_root, task_id, uid, cmd, fp)（写
      {"event": "verification", "unit", "command", "status": "pass",
      "fingerprint"}——H6 归属绑定 + RB-1 完成证据门的推荐写入口）；
      exit != 0 / 超时不写任何证据，只落 receipt——失败本身也是有
      价值的观察，但绝不是「通过」证据；
    - verify_task exit_code == 0 → state.record_verification(st, cmd,
      fp) 后 state.save_state（任务级完成门口径证据；直接 import——
      state 无循环导入）；
    - run_review → journal review_receipt 事件 + state.record_review +
      save_state（同一调用维护 receipt 与 state.review 快速路径字段，
      两处恒一致）；Stop 完成门的审查检查自 wu-21-13 起只消费
      receipts/ 内的 review receipt（receipt 唯一权威），不再单独
      采信 state.review 手写字段；
    - verification receipt / verification_receipt 事件为 RB-1 完成证
      据门的增强层。

RB-2 双根分离（与 task_manager 同口径）：
    账本（state.json / events.jsonl / receipts/）恒在账本根 repo_root
    ；git 求值（touched 清单 / 证据指纹）按任务绑定解析——
    git_root = state.resolve_repository_root(st, repo_root)（绑定
    repository.root 优先，legacy 回退账本根）。多仓场景（账本根 ≠
    git 根）下证据指纹绑定的是任务仓库的真实状态。

失败语义：
    - 任务缺失 / 单元缺失 / 白名单拒绝 / policy 拒绝 →
      ProvenanceError（中文可行动消息），且零副作用（无 receipt、
      无事件、无证据、state.json 字节不变）；全部待执行命令的闸门
      校验先于任何执行发生——多命令批次中任一命令被拒，整批不执行
      （不留半批凭证）；
    - state.json JSON 损坏的 ValueError、git 求值失败（OwnershipError
      / FingerprintError）自然上抛（结构性错误不静默）；
    - 命令超时（subprocess.TimeoutExpired）不是错误：凭证照落
      （timeout: true、exit_code: None），只是绝不构成通过证据。

依赖：
    runtime.state / runtime.journal / runtime.policy / runtime.reconcile
    / runtime.fingerprint（均无循环导入；reconcile 只被读消费）+
    标准库 datetime / hashlib / json / os / subprocess / time。
    task_manager 仅在单元验证成功路径上惰性导入（它导入本模块不
    存在——延迟是为把重依赖留在真正需要的分支）。仅 Python 3 标准
    库，`python3 -S` 可运行。

来源：
    docs/GLM-Conductor-v2.1-Architecture-Agent-Implementation-Plan.md
    §16.3/§16.4（验证溯源）+ §16.5（review receipt，wu-21-13 落定）
    + v2.1 M5/M6 wu-21-12 实施规格（主会话锁定 2026-08-31：D4 runtime
    受限主动执行 + receipt 键名冻结）+ wu-21-13 实施规格（主会话锁定
    2026-08-31：run_review receipt 键冻结 + Stop 完成门「只有 fresh
    ship receipt 才能通过」+ D1 裁定落定——reviewer 维持 permit 豁免，
    溯源由 receipt 绑定承担）
    + release hardening RB-1/RB-2（完成证据门与双根分离的既有口径）。
"""

import datetime
import hashlib
import json
import os
import subprocess
import time

from runtime import fingerprint
from runtime import journal
from runtime import policy
from runtime import reconcile
from runtime import state

# receipts 目录名（位于任务目录 <repo_root>/.glm-conductor/tasks/<task-id>/ 下）
RECEIPTS_DIRNAME = "receipts"
# receipt 的 kind 取值（键名冻结契约的一部分）
RECEIPT_KIND = "verification"
# review receipt 的 kind 取值（wu-21-13，键名冻结契约的一部分）
REVIEW_RECEIPT_KIND = "review"
# runner 标识（恒定：证明该凭证由 runtime 亲自执行产生，非会话转述）
RUNNER_ID = "glm-conductor-runtime"
# run_review 的 route 合法取值（None 表示审查时不绑定派发模式）
REVIEW_ROUTES = (None, "solo", "delegate", "audit", "full")
# stdout / stderr 入 receipt 的截断上限（字符；超出部分丢弃，保留前段）
EXCERPT_LIMIT = 4096
# 缺省单命令超时（秒）
DEFAULT_TIMEOUT_SECONDS = 900


class ProvenanceError(Exception):
    """验证溯源层错误（任务/单元缺失、白名单闸拒绝、policy 闸拒绝等）。"""


# —— 内部助手 ——

def _utc_now_iso() -> str:
    """当前 UTC 时刻的 ISO-8601 字符串（毫秒精度 Z 形态，与 task_manager
    的 journal / permit 时间字段同格式）——receipt observed_at 落盘口径。"""
    moment = datetime.datetime.now(datetime.timezone.utc)
    return (moment.strftime("%Y-%m-%dT%H:%M:%S")
            + ".%03dZ" % (moment.microsecond // 1000))


def _require_state(repo_root, task_id, api) -> dict:
    """load_state 并要求任务存在：缺失 → ProvenanceError（task_manager
    同款错误口径的本模块变体）；JSON 损坏的 ValueError 自然上抛。"""
    st = state.load_state(repo_root, task_id)
    if st is None:
        raise ProvenanceError(
            "%s：任务 %s 不存在（无 state.json），无法执行验证溯源"
            % (api, task_id))
    return st


def _require_unit(st, task_id, uid, api) -> dict:
    """按 id 查找单元：找不到 → ProvenanceError（消息含 uid 与任务 id）。"""
    units = st.get("work_units")
    if isinstance(units, list):
        for unit in units:
            if isinstance(unit, dict) and unit.get("id") == uid:
                return unit
    raise ProvenanceError(
        "%s：任务 %s 的 work_units 中找不到单元 %s" % (api, task_id, uid))


def _check_common_params(api, command, timeout_seconds) -> None:
    """公共参数校验（先于任何 I/O 副作用，失败零副作用）。"""
    if command is not None and (
            not isinstance(command, str) or command == ""):
        raise ValueError(
            "%s：command 必须是 None（执行全部 required）或非空字符串，"
            "得到 %r" % (api, command))
    if isinstance(timeout_seconds, bool) or not isinstance(
            timeout_seconds, (int, float)) or timeout_seconds <= 0:
        raise ValueError(
            "%s：timeout_seconds 必须是正数（秒），得到 %r"
            % (api, timeout_seconds))


def _resolve_commands(api, declared, command, scope_desc) -> "list":
    """白名单解析：command=None → required 全部；显式 command → 逐字
    ∈ required 清单，否则 ProvenanceError（D4 白名单闸）。"""
    if isinstance(declared, (list, tuple)):
        declared_list = list(declared)
    else:
        declared_list = []
    if command is None:
        return declared_list
    if command not in declared_list:
        raise ProvenanceError(
            "%s：命令 %r 不在%s声明的 required 验证清单内（D4 白名单闸："
            "runtime 只执行 state 中已声明的验证命令）——required：%s"
            % (api, command, scope_desc,
               declared_list if declared_list else "空"))
    return [command]


def _gate_policy(api, commands) -> None:
    """policy 闸（先于任何执行发生）：命中 deny / ask → ProvenanceError。

    runtime.policy.classify_bash 返回 (decision, rule_name) 或 None：
    ("deny", _) / ("ask", _) 一律拒绝执行（ask 在主会话 evaluate 里
    还受 assurance 调节，但 runtime 主动执行没有「询问用户」通道，
    保守恒拒）；None（无规则命中）与 ("allow", _) 放行。policy 与
    白名单两道闸都必须通过——本函数不豁免白名单，白名单也不豁免本闸。
    """
    for command in commands:
        hit = policy.classify_bash(command)
        if hit is None:
            continue
        decision, rule = hit
        if decision in ("deny", "ask"):
            raise ProvenanceError(
                "%s：命令 %r 被 runtime.policy 判定为 %s（规则 %s）——"
                "D4 policy 闸拒绝执行（policy 优先于白名单：两道闸都必须"
                "通过）；请改用策略允许的验证命令或调整策略规则"
                % (api, command, decision, rule))


def _execute_command(work_root, command, timeout_seconds) -> tuple:
    """执行一条验证命令，返回 (timed_out, exit_code, stdout_excerpt,
    stderr_excerpt, duration_ms)。

    subprocess 固定形态（D4 锁定）：shell=True、cwd=work_root（任务绑定的
    Git 仓库根——RB-2 双根分离下恒与指纹求值根同源，绝不取账本根；
    终审 P1 修复）、capture_output=True、text=True、encoding="utf-8"、
    errors="replace"——Windows GBK 控制台下不显式 utf-8 会把子进程输出
    按本地代码页解码，中文输出乱码甚至 UnicodeDecodeError；
    errors="replace" 保证最坏情况也只是替换符而非异常。stdout/stderr
    截断保留前 EXCERPT_LIMIT 字符。超时（TimeoutExpired）按 timed_out=True
    归一返回（部分输出不采信，excerpt 恒空串），不向上抛。
    """
    started = time.monotonic()
    timed_out = False
    exit_code = None
    stdout_excerpt = ""
    stderr_excerpt = ""
    try:
        proc = subprocess.run(
            command, shell=True, cwd=str(work_root), capture_output=True,
            text=True, encoding="utf-8", errors="replace",
            timeout=timeout_seconds)
        exit_code = proc.returncode
        stdout_excerpt = (proc.stdout or "")[:EXCERPT_LIMIT]
        stderr_excerpt = (proc.stderr or "")[:EXCERPT_LIMIT]
    except subprocess.TimeoutExpired:
        timed_out = True
    duration_ms = int(round((time.monotonic() - started) * 1000))
    return timed_out, exit_code, stdout_excerpt, stderr_excerpt, duration_ms


def _write_receipt(repo_root, task_id, receipt) -> str:
    """durable 落盘一份凭证（tmp + os.replace 原子写），返回最终路径。

    目录：<task_dir>/receipts/；文件名：<kind>-<observed_at紧凑串>
    -<hash8>.json（紧凑串 = observed_at 去掉 - : . 分隔符；hash8 = 凭证
    规范化 JSON（UTF-8 字节）的 sha256 前 8 位——同毫秒多条凭证靠它
    区分，同内容重跑覆盖同名为幂等）。编码 UTF-8、ensure_ascii=False、
    indent 2、sort_keys；任何失败路径清理 tmp，成功后确保 tmp 不残留。
    文件名前缀恒为 receipt 的 kind（"verification-" / "review-"，
    wu-21-13 review 凭证与验证凭证同构、同目录共存）。
    """
    receipts_dir = state.task_dir(repo_root, task_id) / RECEIPTS_DIRNAME
    receipts_dir.mkdir(parents=True, exist_ok=True)
    content = json.dumps(receipt, ensure_ascii=False, indent=2,
                         sort_keys=True)
    digest8 = hashlib.sha256(content.encode("utf-8")).hexdigest()[:8]
    compact = (receipt["observed_at"].replace("-", "")
               .replace(":", "").replace(".", ""))
    name = "%s-%s-%s.json" % (receipt["kind"], compact, digest8)
    path = receipts_dir / name
    tmp_path = receipts_dir / (name + ".tmp")
    try:
        # newline="\n"：固定 \n 换行，避免 Windows 文本模式写出 \r\n
        with open(str(tmp_path), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(content)
        os.replace(str(tmp_path), str(path))
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink()
        raise
    if tmp_path.exists():
        tmp_path.unlink()
    return str(path)


def _record_receipt(repo_root, task_id, receipt) -> str:
    """落盘凭证 + 追加 journal <kind>_receipt 事件（同字段），返回凭证
    文件路径。事件名由 receipt 的 kind 派生（"verification" →
    verification_receipt；"review" → review_receipt）；事件先行校验由
    journal.append_event 承担。"""
    path = _write_receipt(repo_root, task_id, receipt)
    event = {"event": "%s_receipt" % receipt["kind"]}
    event.update(receipt)
    journal.append_event(repo_root, task_id, event)
    return path


def _require_fingerprint(api, command, fp) -> str:
    """证据接线前的指纹防御校验：空指纹不得作为通过证据落账。"""
    if not isinstance(fp, str) or fp == "":
        raise ProvenanceError(
            "%s：命令 %r 的同刻指纹不可判定（%r）——拒绝把它作为通过"
            "证据落账（fail-closed，结构性配置请检查 ownership 声明）"
            % (api, command, fp))
    return fp


# —— 单元作用域：verify_unit ——

def verify_unit(repo_root, task_id, uid, *, command=None,
                timeout_seconds=DEFAULT_TIMEOUT_SECONDS) -> dict:
    """受限执行单个 work unit 的已声明验证命令并产出溯源凭证（D4）。

    流程：
      1. load_state（任务缺失 ProvenanceError；损坏 ValueError 上抛），
         找不到 uid → ProvenanceError；
      2. 公共参数校验（command / timeout_seconds 形状）——先于任何
         I/O 副作用；
      3. 白名单解析：command=None → 依序执行 unit["verification"] 全部
         required 命令；显式 command → 必须逐字 ∈ required 清单，否则
         ProvenanceError（D4 白名单闸）；
      4. policy 闸：全部待执行命令先逐条过 runtime.policy.classify_bash
         ——命中 deny / ask → ProvenanceError（两道闸都必须通过；任一
         命令被拒则整批不执行，不留半批凭证）；
      5. 逐条命令：subprocess 执行（shell=True、cwd=repo_root、显式
         utf-8/replace、timeout_seconds）→ **同刻指纹**（进程退出后、
         任何其他仓库操作前，经共享谓词 reconcile.fresh_unit_verification
         计算单元作用域指纹——git_touched_files → ownership.classify_paths
         取 owned 命中 → fingerprint.compute_fingerprint，与 RB-1 完成
         证据门完全同一口径；RB-2 双根：git 求值按任务绑定根，events
         恒从账本根注入）→ 构建冻结键 receipt → durable 落盘 + journal
         verification_receipt 事件 → exit_code == 0 时惰性 import
         task_manager 调 record_unit_verification 写单元级通过证据
         （RB-1 完成证据门直接消费——verify_unit 之后 finish_unit 应能
         直接通过）；exit != 0 / 超时不写证据只落 receipt；
      6. 超时：receipt 增 "timeout": true、exit_code 置 None、excerpt
         恒空串，不记录证据。

    返回：
        {"unit": uid, "receipts": [receipt...],
         "exit_codes": {command: exit_code | None},
         "all_passed": bool}
      - receipts 按执行序；exit_codes 按命令执行序插入（required 原序
        或显式命令单条）；all_passed = 全部命令 exit_code == 0（空
        required 平凡成立——没有可失败的对象；显式 command 恒单条）；
      - receipt 的 "fingerprint" 即 RB-1 完成证据门随后复算的同一指纹
        （期间仓库未被再次改动时），凭证因此可直接与证据门对账。

    纪律：任务缺失 / 单元缺失 / 白名单拒绝 / policy 拒绝零副作用（无
    receipt、无事件、无证据、state.json 不变）；本函数不改单元状态、
    不动租约——收尾（finish_unit）仍归 task_manager。
    """
    api = "verify_unit"
    _check_common_params(api, command, timeout_seconds)
    st = _require_state(repo_root, task_id, api)
    unit = _require_unit(st, task_id, uid, api)
    commands = _resolve_commands(api, unit.get("verification"), command,
                                 "单元 %s" % uid)
    _gate_policy(api, commands)
    # RB-2 双根分离：git 求值按任务绑定根；账本 I/O 恒在账本根
    git_root = state.resolve_repository_root(st, repo_root)
    receipts = []
    exit_codes = {}
    for cmd in commands:
        timed_out, exit_code, stdout_excerpt, stderr_excerpt, duration_ms = \
            _execute_command(git_root, cmd, timeout_seconds)
        # 零 TOCTOU 同刻指纹：进程退出后、任何其他仓库操作前立即计算
        # （口径与 RB-1 完成证据门同一谓词；events 从账本根注入）
        events_snapshot = journal.read_events(repo_root, task_id)
        fp = reconcile.fresh_unit_verification(
            git_root, task_id, unit, events=events_snapshot)["fingerprint"]
        receipt = {
            "kind": RECEIPT_KIND,
            "scope": "unit",
            "unit": uid,
            "command": cmd,
            "exit_code": exit_code,
            "fingerprint": fp,
            "observed_at": _utc_now_iso(),
            "runner": RUNNER_ID,
            "duration_ms": duration_ms,
            "stdout_excerpt": stdout_excerpt,
            "stderr_excerpt": stderr_excerpt,
        }
        if timed_out:
            receipt["timeout"] = True
        _record_receipt(repo_root, task_id, receipt)
        if exit_code == 0:
            fp = _require_fingerprint(api, cmd, fp)
            # 惰性导入：只有成功路径需要证据写入口（避免重依赖常驻）
            from runtime import task_manager
            task_manager.record_unit_verification(
                repo_root, task_id, uid, cmd, fp)
        receipts.append(receipt)
        exit_codes[cmd] = exit_code
    return {
        "unit": uid,
        "receipts": receipts,
        "exit_codes": exit_codes,
        "all_passed": all(code == 0 for code in exit_codes.values()),
    }


# —— 任务作用域：verify_task ——

def verify_task(repo_root, task_id, *, command=None,
                timeout_seconds=DEFAULT_TIMEOUT_SECONDS) -> dict:
    """受限执行任务级已声明验证命令并产出溯源凭证（完成门口径，D4）。

    与 verify_unit 同构，差异三处：
      - 命令白名单 = state.verification.required（完成门 §19 的同一
        清单），缺块 / 形状异常按空清单处理（显式 command 恒被白名单
        闸拒绝）；
      - 同刻指纹 = fingerprint.task_fingerprint(git_root, st)——与
        Stop 完成门同一入口（任务作用域：按 state.ownership.files
        过滤 touched 后计算；RB-2 双根：git_root 经
        state.resolve_repository_root 解析，绑定优先、legacy 回退
        账本根）；
      - 证据接线：exit_code == 0 → state.record_verification(st, cmd,
        fp) 后 state.save_state（任务级完成门口径证据；直接 import，
        state 无循环导入）；exit != 0 / 超时只落 receipt，state 不动。

    receipt 的 scope 为 "task"，作用域键为 "task": task_id（unit 作用
    域的 "unit": uid 之对应位）；journal 事件同为 verification_receipt。

    返回形状与 verify_unit 同构（unit 键换 task）：
        {"task": task_id, "receipts": [...],
         "exit_codes": {command: exit_code | None},
         "all_passed": bool}
    """
    api = "verify_task"
    _check_common_params(api, command, timeout_seconds)
    st = _require_state(repo_root, task_id, api)
    verification = st.get("verification")
    declared = (verification.get("required")
                if isinstance(verification, dict) else None)
    commands = _resolve_commands(api, declared, command, "任务 %s" % task_id)
    _gate_policy(api, commands)
    git_root = state.resolve_repository_root(st, repo_root)
    receipts = []
    exit_codes = {}
    for cmd in commands:
        timed_out, exit_code, stdout_excerpt, stderr_excerpt, duration_ms = \
            _execute_command(git_root, cmd, timeout_seconds)
        # 零 TOCTOU 同刻指纹：任务作用域，与完成门同一入口
        fp = fingerprint.task_fingerprint(git_root, st)
        receipt = {
            "kind": RECEIPT_KIND,
            "scope": "task",
            "task": task_id,
            "command": cmd,
            "exit_code": exit_code,
            "fingerprint": fp,
            "observed_at": _utc_now_iso(),
            "runner": RUNNER_ID,
            "duration_ms": duration_ms,
            "stdout_excerpt": stdout_excerpt,
            "stderr_excerpt": stderr_excerpt,
        }
        if timed_out:
            receipt["timeout"] = True
        _record_receipt(repo_root, task_id, receipt)
        if exit_code == 0:
            fp = _require_fingerprint(api, cmd, fp)
            state.record_verification(st, cmd, fp)
            state.save_state(repo_root, st)
        receipts.append(receipt)
        exit_codes[cmd] = exit_code
    return {
        "task": task_id,
        "receipts": receipts,
        "exit_codes": exit_codes,
        "all_passed": all(code == 0 for code in exit_codes.values()),
    }


# —— 审查作用域：run_review（wu-21-13，§16.5） ——

def run_review(repo_root, task_id, *, reviewer, verdict, tool_use_id,
               route=None, note=None) -> dict:
    """申报一次已发生的审查并落 durable review receipt（wu-21-13 §16.5）。

    主会话在 reviewer（glm-reviewer / visual-reviewer 等只读审查者）
    返回裁决后调用本 API：runtime 把裁决与**终指纹**（与 Stop 完成门
    同一入口 fingerprint.task_fingerprint）绑定，落盘 durable receipt
    并同步既有证据流。Stop 完成门的审查检查自本单元起只认 fresh ship
    review receipt——本函数是该 receipt 的唯一产出点。

    流程：
      1. 全量参数校验（先于任何 I/O 副作用，失败零副作用）：
         reviewer / tool_use_id 必须是非空 str；verdict 必须 ∈
         state.REVIEW_VERDICTS（ship / fix-first / rethink——仓库既有
         词汇；计划 §16.5 的 "changes_requested" 是计划侧泛称，不引入
         ）；route 必须 ∈ (None, solo, delegate, audit, full)；note
         非 None 时必须是 str。任一违规 → ProvenanceError；
      2. load_state（任务缺失 → ProvenanceError；JSON 损坏的
         ValueError 自然上抛）；
      3. 终指纹：git_root = state.resolve_repository_root（RB-2 双根：
         绑定 repository.root 优先，legacy 回退账本根）→
         fingerprint.task_fingerprint(git_root, st)——与 Stop 完成门
         完成提交同一入口，receipt 因此可与完成门直接对账；
      4. 构建冻结键 receipt → durable 落盘（tmp + os.replace 原子写，
         文件名 review-<observed_at紧凑串>-<hash8>.json）+ journal
         review_receipt 事件（同字段）；
      5. state 同步：state.record_review(st, verdict, fingerprint) 后
         state.save_state——完成门的快速路径字段（review.verdict /
         review.fingerprint）由同一次调用维护，与 receipt 恒一致
         （但完成门不再单独采信 state.review——receipt 是唯一权威）。

    信任边界（设计决策，wu-21-13 锁定）：
        reviewer / tool_use_id / verdict 是主会话对**已发生审查**的申
        报输入——runtime 不回验 Agent 调用本身（不做「这个 tool_use_id
        真的跑过一次审查吗」的密码学追查）。receipt 的价值在于：
        durable 落盘（可审计、不随会话记忆消失）+ 终指纹绑定（审查后
        文件再变动即 review_stale，完成门机械拦截）+ 时间戳（多张
        receipt 取 observed_at 最新一张）。伪造申报骗过的是完成门的
        「审查已发生」记录，骗不过的是「审查对应的就是即将提交的
        diff」——后者由指纹绑定机械保证。

    返回：
        receipt dict（与落盘文件、journal 事件逐字段一致；键名冻结）。

    纪律：参数非法 / 任务缺失零副作用（无 receipt、无事件、state.json
    字节不变）；本函数不改任务状态机、不动租约；visual_reviewer 专属
    流程无需分支（receipt 同构，reviewer 字段自辨）。
    """
    api = "run_review"
    # 1) 全量参数校验（先于任何 I/O 副作用）
    if not isinstance(reviewer, str) or reviewer == "":
        raise ProvenanceError(
            "%s：reviewer 必须是非空字符串（审查者身份申报），得到 %r"
            % (api, reviewer))
    if not isinstance(tool_use_id, str) or tool_use_id == "":
        raise ProvenanceError(
            "%s：tool_use_id 必须是非空字符串（绑定审查发生的 Agent "
            "tool 调用），得到 %r" % (api, tool_use_id))
    if verdict not in state.REVIEW_VERDICTS:
        raise ProvenanceError(
            "%s：verdict %r 不在 state.REVIEW_VERDICTS 内（%s）"
            % (api, verdict, ", ".join(state.REVIEW_VERDICTS)))
    if route not in REVIEW_ROUTES:
        raise ProvenanceError(
            "%s：route %r 不在合法取值内（%s）"
            % (api, route, ", ".join(str(item) for item in REVIEW_ROUTES)))
    if note is not None and not isinstance(note, str):
        raise ProvenanceError(
            "%s：note 必须是 None 或字符串，得到 %r" % (api, note))
    # 2) 任务必须存在（损坏 ValueError 自然上抛）
    st = _require_state(repo_root, task_id, api)
    # 3) 终指纹：与 Stop 完成门同一入口（RB-2 双根：按任务绑定根求值）
    git_root = state.resolve_repository_root(st, repo_root)
    fp = fingerprint.task_fingerprint(git_root, st)
    # 4) 冻结键 receipt → durable 落盘 + journal review_receipt（同字段）
    receipt = {
        "kind": REVIEW_RECEIPT_KIND,
        "reviewer": reviewer,
        "route": route,
        "verdict": verdict,
        "fingerprint": fp,
        "tool_use_id": tool_use_id,
        "observed_at": _utc_now_iso(),
        "runner": RUNNER_ID,
        "note": note,
    }
    _record_receipt(repo_root, task_id, receipt)
    # 5) state 快速路径字段同步（同一调用维护，与 receipt 恒一致）
    state.record_review(st, verdict, fp)
    state.save_state(repo_root, st)
    return receipt
