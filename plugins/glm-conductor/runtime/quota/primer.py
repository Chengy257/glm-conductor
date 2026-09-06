#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GLM Conductor v2.2 Window Primer（修正计划 C4，wu-22-C4，分支 B 全量）。

职责（修正计划 §8.1 / §8.2 / §8.3 / §C4，P0-QP 实验门已由用户机制裁决
关闭——见 docs/GLM-Conductor-v2.2-Phase0-Primer-Experiments.md 门总览）：
    用一次最小、独立、可审计的模型调用使新 quota window materialize，
    并随后强制 quota refresh 二次确认。本模块是 v2.2 运行时里唯一的
    control-plane 模型调用面（P0-QP-08 结构审计的落点），由三部分构成：

      1. authorize_prime(policy_view)——§8.3 三重授权闸（机械强制，
         非文档约定）：
             primer.enabled == true
             AND auto_resume ∈ {auto_once, until_done}
             AND authorization.source == "user"
         三条全过才 authorized；任一不过 → 结构化拒绝（零副作用：无
         网络、无事件、无状态写）。manual / notify 永不 prime。

      2. prime_authorized(repo_root, task_id, ...)——唯一 public 执行
         API（RH-02，v2.2 Release Hardening，方案 A：机械闸内嵌）：
         json 容错读任务 state 文件（<repo_root>/.glm-conductor/tasks/
         <task_id>/state.json）的 execution_policy 块 → authorize_prime
         三重闸 → 未授权（含任务 / policy 缺失坏块）返回冻结九键结构化
         拒绝（PRIMER_RESULT_KEYS 八键 + reason；零 transport、零 quota
         网络、零 primer.json 写、零 window_primed journal、零窗口消费
         ——拒绝路径不制造任何 durable 痕迹）→ 授权通过才透传
         _prime_once_unchecked 执行（八键返回原样）。

      3. _prime_once_unchecked(...)（原 prime_once，RH-02 起私有名）
         ——单飞幂等执行（幂等闸 → 一次最小模型调用
         （single-attempt：绝不自动重发——重发即是 P0-QP-07 要防的
         「同旧 boundary 连发多个 prime」；歧义超时以 post-refresh
         确认代替重发）→ 强制 quota refresh 物化确认 → durable 落账
         + journal 事件 → 冻结键返回）。仅供单元测试与内部已授权
         wrapper（public 面只有 prime_authorized——绕过授权闸必须
         显式使用私有名，调用面即审计面）。
         v2.2 C5a 起 window_primed 事件落在控制面 journal
         .glm-conductor/quota/events.jsonl（journal.append_control_
         plane_event，与 watcher.json / primer.json 同层）——不再借用
         tasks/<伪任务>/events.jsonl 目录（C4 reviewer P2 正解：伪任务
         目录会被 discover_tasks 判为 orphaned 噪声）。

      4. primer.json durable 存取——独立文件独立常量（不复用任务
         state / journal / watcher.json），原子写纪律照抄
         watcher_store（PermissionError 有界重试——Windows AV / 目录
         锁是本机已知现象；v2.2.1 WU-221-A2 起机制委托
         runtime.durable_io.atomic_write_json：唯一同目录临时名 +
         os.replace，绝不占用固定 <path>.tmp 名）。

物化确认红线（§C4，P0-QP-03 粒度告警的落地）：
    - HTTP 200 不是证据（不得因为 prime call 成功返回 HTTP 200 就直接
      AVAILABLE）；
    - 百分比下降不是证据（监控端点整百分点粒度，57-token 调用不可见
      delta——「百分比自证」被红线封死）；
    - 唯一证据 = prime 前后两次强制 refresh 的窗口身份变化（epoch /
      reset_at 推进，epoch.same_epoch 判定——§10「provider 状态翻转不
      推进 epoch」同源复用，不复制逻辑）；
    - refresh 后仍有 EXHAUSTED 阻塞窗（weekly 优先语义）→ materialized
      但 executable=False、无 ActivationReady 概念产生（归 C5）；
      executable 复用 epoch.evaluate_epoch 的既有判定，不复制映射。

fail-closed 不对称的理由（本模块与 quota 观察面的方向差异，纪律原文）：
    quota 解析面（resolver）与观察面（control D7 第 4 条）是 best-effort
    观测，缺数据按 UNKNOWN / PRESSURE fail-open 不阻塞派发；primer 是
    **模型调用授权**——一次调用消耗真实额度且改变 provider 侧窗口状态，
    policy 缺块 / 坏块 / 形状非法一律按保守拒绝解释，绝不 fail-open 放
    行 prime。授权方向的错误代价不对称：误拒只损失一次物化时机（下个
    观察周期可再判），误放行则是无授权的模型调用。

幂等语义（P0-QP-07，本实现随代码取证关闭该设计门）：
    - 幂等键 = (provider_identity_hash, boundary_id)（identity 为来源+
      凭证 sha256 前 16 位，派生口径即 runtime.quota.identity 的
      compute_provider_identity_hash（v2.2.1 WU-221-B1 起自 watcher
      抽取的共享落点）；boundary_id 为 prime 时点的旧 boundary /
      epoch_id）；
    - v2.2.1 WU-221-B2（QuotaIdentity）注记：本面自诞生起即按复合
      身份 (provider_identity_hash, epoch/boundary) 记账——幂等键的
      第一维就是身份指纹，A 身份的 primed 记录在结构上不可能拦截
      B 身份的同 boundary prime（各按自身身份记账，cross-account
      回归天然成立）；window_primed 事件同样携带
      provider_identity_hash。本单元零行为变化，仅确认口径同源
      （quota_identity_matches 双形态判定的 trusted/foreign 语义与
      本面「异身份 = 键不命中」语义一致）；
    - 键命中 → 直接返回既有 durable 结果（idempotent=True），零模型
      调用、零 quota 网络、零 journal 事件（同键重入零网络零事件）；
    - 失败尝试同样落账（error_kind 非空）——超时表示「结果未知」而非
      「provider 一定没有执行」（响应可能已丢失），重发即是 P0-QP-07
      要防的「同旧 boundary 连发多个 prime」，因此本层绝不自动重发
      （single-attempt）：歧义超时改以 prime 后强制 refresh 的窗口
      身份推进做事后确认（推进 → materialized=True，timeout +
      materialized=True 是合法组合；未确认 → materialized=False）；
      失败后的重试编排归 C5 凭账本判断，本层机械保证每键至多一次
      执行；
    - primer.json 损坏 / 缺失 → 按「无记录」处理继续执行（fail-open for
      execution）：重复 prime 的代价是少量 token（对已物化窗口重复触碰
      不产生新 epoch），而拒绝执行的代价是恢复链永久卡死——又一次代价
      不对称；原子写纪律使撕裂文件实际不可见。

接线边界（约束 1，C1b unwired 先例；RH-02 收口）：本单元不把 primer
接到任何自动调用路径——watcher 循环 / resume 链 / CLI 均不动，调用
编排归 C5/C6。授权闸机械收口（RH-02，方案 A）：三重授权不再依赖
「调用方先过 authorize_prime 再调执行」的纪律，而是内嵌在唯一 public
执行 API prime_authorized 内（读任务 execution_policy → authorize_
prime → 通过才执行）；_prime_once_unchecked（原 prime_once）是私有
名，仅供单元测试与内部已授权 wrapper——绕过授权必须显式使用私有名
（调用面可见、可审计）。执行层自身不重复授权（返回值中的 authorized
恒 True，仅作记录字段，见冻结键表）。prime_authorized 拒绝路径返回
冻结九键 = PRIMER_RESULT_KEYS 八键 + reason（新增 public API 的返回
形状，不触碰 PRIMER_RESULT_KEYS 冻结键表）。primer_enabled 配置默认
恒 False（§8.3：Phase0 未完成前结构性关闭），词汇落点 =
runtime/execution_policy.py 的 quota_control.primer_enabled
（validator bool + 容错读，缺省 False）。

凭证与安全纪律（§37 沿用；P0-QP-00 实测形态）：
    - 凭证经 runtime.quota.credentials.resolve_credential() 只进内存，
      绝不落盘、不进日志 / 异常文本 / journal；
    - 端点 = <baseURL>/v1/messages，baseURL 容错读自 zcode provider
      配置（options.baseURL，P0-QP-00 实测：与 quota 查询同源
      https://open.bigmodel.cn/api/anthropic）；host 必须在
      provider.ALLOWED_HOSTS 且 https，否则回退内置默认——绝不向
      allowlist 外 host 发送凭证；
    - 异常只取类型名（HTTP 状态码 / bounded 超限令牌除外），绝不透传
      异常文本 / URL / 响应体（urllib 异常文本可能携带 URL）；
    - 传输层全注入（transport 注入 urlopen 等价物）；生产传输 stdlib
      urllib + 禁重定向 opener（复用 _http.build_default_opener）+
      短超时 + 限长读取；测试零真实网络零真实模型调用。

依赖：
    仅 Python 3 标准库 + runtime（durable_io，v2.2.1 WU-221-A2 起）/
    runtime.execution_policy / runtime.journal /
    runtime.quota.{_http, credentials, epoch, provider, resolver,
    scheduler}；quota/* 包纪律：不 import runtime.state /
    runtime.task_manager。跨模块私有 import（scheduler 的
    _format_iso_z / _normalize_now）是 quota 包既定惯例。

来源：
    docs/GLM-Conductor-v2.2-Quota-Control-Plane-Architecture-Correction-
    and-Agent-Implementation-Plan.md §8（Window Primer 严格隔离）/
    §8.1（物化语义）/ §8.2（control-plane call 不属 Task Resume）/
    §8.3（Feature-Gate + 三重授权）/ §9 / §C4（红线：200 与百分比都不
    是证据，必须 refresh 二次确认）/ §QC-04..QC-06、QC-12
    + docs/GLM-Conductor-v2.2-Phase0-Primer-Experiments.md（P0-QP-00
    实测端点形态 / P0-QP-04 成本 / P0-QP-05 anchor 行为 / P0-QP-07
    幂等设计门）+ 修正计划工作单元 wu-22-C4。
"""

import json
import os
import socket
import time
import urllib.error
import urllib.parse
import urllib.request

from runtime import durable_io
from runtime.execution_policy import primer_enabled as _policy_primer_enabled
from runtime.journal import append_control_plane_event as _append_control_plane_event
from runtime.quota import resolver
from runtime.quota._http import build_default_opener
from runtime.quota.credentials import PROVIDER_KEY, resolve_credential
from runtime.quota.epoch import evaluate_epoch, same_epoch
from runtime.quota.identity import compute_provider_identity_hash
from runtime.quota.provider import ALLOWED_HOSTS, ERROR_KINDS
from runtime.quota.scheduler import _format_iso_z, _normalize_now

# —— durable 存储常量（独立文件独立常量；纪律照抄 watcher_store 但
#    不复用其常量——primer.json 与 watcher.json 互不越界） ——

# 状态文件相对布局（<repo_root>/ 下；与 watcher.json 同层不同文件）
PRIMER_DIR_PARTS = (".glm-conductor", "quota")
PRIMER_STATE_FILE_NAME = "primer.json"
PRIMER_SCHEMA_VERSION = 1

# durable 单笔记录的冻结字段（键名逐字；extra 键不允许——本文件只由
# 本模块读写，形状即契约）
PRIMER_RECORD_FIELDS = (
    "primed_at", "materialized", "executable",
    "tokens_in", "tokens_out", "latency_ms", "error_kind")

# PermissionError bounded retry（Windows AV / 目录锁下 os.replace 偶发
# 拒绝是本机已知现象）：默认 3 次、0.2 秒间隔——有界，绝不无限重试
PRIMER_PERMISSION_RETRY_ATTEMPTS = 3
PRIMER_PERMISSION_RETRY_INTERVAL_SECONDS = 0.2

# —— 返回键冻结（wu-22-C4 规格：{authorized 隐含、primed, materialized,
#    executable, tokens, latency_ms, idempotent, error}） ——

PRIMER_RESULT_KEYS = (
    "authorized", "primed", "materialized", "executable",
    "tokens", "latency_ms", "idempotent", "error")

# prime_authorized 拒绝路径返回的冻结九键（PRIMER_RESULT_KEYS 八键
# + reason；授权通过路径透传 _prime_once_unchecked 的八键返回——九键
# 仅在拒绝路径出现。这是新增 public API 的返回形状，不触碰
# PRIMER_RESULT_KEYS 冻结键表）
PRIME_AUTHORIZED_RESULT_KEYS = PRIMER_RESULT_KEYS + ("reason",)

# 任务 state 文件相对布局（prime_authorized 的 policy 读取面；与
# runtime.state / task_manager 的 <repo_root>/.glm-conductor/tasks/
# <task_id>/state.json 布局同源命名。quota 包纪律禁止 import
# runtime.state / runtime.task_manager（模块 docstring「依赖」节），
# 故按 watcher.determine_mode 同款惯例本地声明常量 + json 直读）
TASK_STATE_DIR_PARTS = (".glm-conductor", "tasks")
TASK_STATE_FILE_NAME = "state.json"

# authorize_prime 返回冻结三键
_AUTHORIZATION_KEYS = ("authorized", "checks", "reason")

# —— §8.3 三重授权词汇 ——

# primer.enabled 的语义落点：execution_policy.quota_control.primer_enabled
# （默认恒 False——§8.3「在真实 provider Phase0 未完成前 primer.enabled =
# false」；容错读与 validator 在 execution_policy.py，本模块复用）
PRIMER_AUTO_RESUME_MODES = ("auto_once", "until_done")
_USER_SOURCE = "user"

# —— 错误分类词汇（provider.ERROR_KINDS 之前加 no-credential：无凭证
#    时零传输直接结构化失败，与 resolver 的 cause 同词） ——

PRIMER_ERROR_KINDS = ("no-credential",) + ERROR_KINDS

# —— 模型调用冻结形态（P0-QP-00 实测，§1.2 逐字；§37 条款 8：鉴权头
#    遵循已验证格式，不做猜测式自创） ——

PRIMER_MODEL = "GLM-5.3-Flash"
PRIMER_MAX_TOKENS = 64
PRIMER_MESSAGE = "Reply with a single word: pong"
ANTHROPIC_VERSION = "2023-06-01"

# 默认端点（P0-QP-00 实测：与 quota 监控同源的 Anthropic 兼容路径）；
# host 必须在 §37 allowlist 内——导入期断言（对齐 _http 的注册表纪律，
# 防漂移）。§25：host 字符串只在常量与断言处出现，不自行散布。
_DEFAULT_BASE_URL = "https://open.bigmodel.cn/api/anthropic"
_MESSAGES_PATH = "/v1/messages"
if urllib.parse.urlsplit(_DEFAULT_BASE_URL).hostname not in ALLOWED_HOSTS:
    raise AssertionError(
        "primer 默认端点 host 漂移：%s 不在 ALLOWED_HOSTS 内"
        % (_DEFAULT_BASE_URL,))

# zcode provider 配置路径（baseURL 容错读；None 语义 = 调用时 expanduser
# 展开；测试 monkeypatch 本常量隔离真实配置）
_ZCODE_CONFIG_PATH = "~/.zcode/v2/config.json"

# 请求超时（秒）：P0-QP-04 实测最小调用延迟 6.15s（GLM-5.3-Flash 默认
# 开思考），_http 的 5.0s quota 超时对模型调用必超——取 30s 有界上限，
# 绝不无限等待（§37 条款 3 的 primer 对应值）
DEFAULT_PRIME_TIMEOUT_SECONDS = 30.0

# 限长响应（字节）：max_tokens=64 的最小响应远小于此；超限归 malformed
_DEFAULT_MAX_BYTES = 65536

# 模型调用不设重试常量：single-attempt（RH-01，v2.2 Release Hardening）
# ——一个幂等键下模型调用至多发一次（P0-QP-07：同旧 boundary 绝不连发），
# 歧义超时（请求可能已到达 provider、结果未知）由 _prime_once_unchecked
# 以 post-refresh 确认代替重发，绝不回环重试。

# —— journal 词汇 ——

PRIMER_JOURNAL_EVENT = "window_primed"
# 控制面 journal 落点（v2.2 C5a，wu-22-C5a，正解 C4 reviewer P2）：
# window_primed 是无任务上下文的控制面事件，经
# journal.append_control_plane_event 落
# .glm-conductor/quota/events.jsonl（与 watcher.json / primer.json
# 同层）——不再借用 tasks/<伪任务>/events.jsonl 目录（伪任务目录会被
# discover_tasks 判为 orphaned 噪声、污染任务枚举）。事件字段零变化。


class PrimerStoreError(RuntimeError):
    """primer.json 写入在 PermissionError 有界重试耗尽后仍失败
    （对齐 watcher_store.WatcherStoreError——绝不静默吞掉）；
    __cause__ 保留原始 PermissionError 供排查。"""


# —— durable 存取（独立文件；读容错 / 写原子 + bounded retry） ——

def primer_state_path(repo_root) -> str:
    """primer durable 状态文件路径 <repo_root>/.glm-conductor/quota/
    primer.json（与 watcher.json 同层不同文件，互不越界）。"""
    return os.path.join(str(repo_root), *PRIMER_DIR_PARTS,
                        PRIMER_STATE_FILE_NAME)


def read_primer_state(repo_root):
    """读 primer.json → dict 或 None（本函数绝不抛）。

    文件不存在 / JSON 损坏 / 顶层非 dict → None（按「无记录」处理——
    代价不对称见模块 docstring「幂等语义」节：重复 prime 害处小于
    恢复链卡死，故存储面 fail-open for execution）。"""
    path = primer_state_path(repo_root)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        # OSError：不存在 / 权限等；ValueError：JSONDecodeError /
        # UnicodeDecodeError 的共同基类
        return None
    return data if isinstance(data, dict) else None


def _write_primer_state(repo_root, record, *,
                        retry_attempts=PRIMER_PERMISSION_RETRY_ATTEMPTS,
                        retry_interval=PRIMER_PERMISSION_RETRY_INTERVAL_SECONDS,
                        sleep=time.sleep):
    """原子写 primer.json（v2.2.1 WU-221-A2 起委托共享原语
    runtime.durable_io.atomic_write_json：唯一同目录临时名 +
    os.replace——落盘字节与既有手写实现逐字节一致；POSIX 注记：最终
    文件现继承原语唯一临时文件的 0o600 权限位（先前 umask 缺省约
    0644——JSON 字节一致，仅文件权限位更收紧，对状态文件更安全）；
    父目录缺失由原语自动创建），返回最终路径。

    纪律照抄 watcher_store.write_watcher_state：record 非 dict →
    ValueError（参数校验先于 I/O）；PermissionError → 有界重试（次数 /
    间隔 / sleep 均可注入，测试零真实等待），耗尽仍失败 →
    PrimerStoreError（__cause__ = 原始 PermissionError）；其余异常由
    原语清理临时文件后原样上抛。读方永不见撕裂文件。
    """
    if not isinstance(record, dict):
        raise ValueError(
            "primer 写入失败：record 必须是 dict，得到 %r"
            % (type(record).__name__,))
    if retry_interval < 0:
        raise ValueError(
            "primer 写入失败：retry_interval 必须 >= 0，得到 %r"
            % (retry_interval,))
    attempts = max(1, int(retry_attempts))
    path = primer_state_path(repo_root)
    try:
        durable_io.atomic_write_json(path, record,
                                     retry_attempts=retry_attempts,
                                     retry_interval=retry_interval,
                                     sleep=sleep)
    except PermissionError as last_permission_error:
        raise PrimerStoreError(
            "primer.json 原子写在 PermissionError 有界重试 %d 次（间隔 %.2f "
            "秒）后仍失败（Windows AV / 目录锁？）：%s" % (
                attempts, float(retry_interval), last_permission_error)
        ) from last_permission_error
    return path


def _fresh_state():
    """全新空账本骨架（read 容错失败时的重建底形）。"""
    return {"schema_version": PRIMER_SCHEMA_VERSION, "primes": {}}


def _read_record(repo_root, provider_identity_hash, boundary_id):
    """按 (identity, boundary_id) 幂等键读单笔记录 → dict 或 None。

    账本缺失 / 损坏 / 任一层非 dict / 记录非 dict → None（按「无记录
    处理」继续执行——理由见模块 docstring；read-prime-modify-write 的
    并发窗口归 C5 编排层串行化，本层提供机械闸）。"""
    state = read_primer_state(repo_root)
    if not isinstance(state, dict):
        return None
    primes = state.get("primes")
    if not isinstance(primes, dict):
        return None
    by_identity = primes.get(provider_identity_hash)
    if not isinstance(by_identity, dict):
        return None
    record = by_identity.get(boundary_id)
    return record if isinstance(record, dict) else None


def _put_record(repo_root, provider_identity_hash, boundary_id, record, *,
                retry_attempts=PRIMER_PERMISSION_RETRY_ATTEMPTS,
                retry_interval=PRIMER_PERMISSION_RETRY_INTERVAL_SECONDS,
                sleep=time.sleep):
    """按幂等键写入单笔记录（读-改-写整体一次原子替换），返回最终路径。

    入参 record 必须恰含 PRIMER_RECORD_FIELDS 七键（形状即契约，多余 /
    缺失键 ValueError——先于任何 I/O 拦截）。"""
    if set(record.keys()) != set(PRIMER_RECORD_FIELDS):
        raise ValueError(
            "primer 记录键必须恰为 %s，得到 %r"
            % (", ".join(PRIMER_RECORD_FIELDS), sorted(record.keys())))
    state = read_primer_state(repo_root)
    if not isinstance(state, dict):
        state = _fresh_state()
    primes = state.get("primes")
    if not isinstance(primes, dict):
        primes = {}
        state["primes"] = primes
    by_identity = primes.get(provider_identity_hash)
    if not isinstance(by_identity, dict):
        by_identity = {}
        primes[provider_identity_hash] = by_identity
    by_identity[boundary_id] = dict(record)
    return _write_primer_state(
        repo_root, state, retry_attempts=retry_attempts,
        retry_interval=retry_interval, sleep=sleep)


# —— §8.3 三重授权闸（零副作用纯函数；fail-closed） ——

def authorize_prime(policy_view) -> dict:
    """§8.3 三重授权闸：primer.enabled AND auto_resume ∈ {auto_once,
    until_done} AND authorization.source == "user"，三条全过才 authorized。

    参数：
      - policy_view：execution_policy 形状的 dict（容错读模式参照
        task_manager._continuity_view：缺块 / 坏块 / 非 dict 一律按保守
        拒绝解释，绝不抛错、绝不 fail-open——理由见模块 docstring
        「fail-closed 不对称」节）。读取口径：
          * primer.enabled ← quota_control.primer_enabled
            （经 execution_policy.primer_enabled 容错读，缺省 False）；
          * auto_resume ← continuity.auto_resume（词汇外一律不合格）；
          * source ← authorization.source（须逐字 == "user"）。

    返回（冻结 3 键）：
      {"authorized": bool,
       "checks": {"primer_enabled": bool, "auto_resume_eligible": bool,
                  "user_authorized": bool},
       "reason": 中文一句话（拒绝时逐条列出未过条件）}

    纪律：纯函数零副作用——无网络、无事件、无状态写（连 repo_root 都
    不入参，结构上不可触盘）。manual / notify 永不 prime（§8.3）。
    """
    checks = {
        "primer_enabled": _policy_primer_enabled(policy_view),
        "auto_resume_eligible": False,
        "user_authorized": False,
    }
    continuity = (policy_view.get("continuity")
                  if isinstance(policy_view, dict) else None)
    auto_resume = (continuity.get("auto_resume")
                   if isinstance(continuity, dict) else None)
    checks["auto_resume_eligible"] = auto_resume in PRIMER_AUTO_RESUME_MODES
    authorization = (policy_view.get("authorization")
                     if isinstance(policy_view, dict) else None)
    source = (authorization.get("source")
              if isinstance(authorization, dict) else None)
    checks["user_authorized"] = source == _USER_SOURCE

    failures = []
    if not checks["primer_enabled"]:
        failures.append("primer_enabled 未启用（默认恒 false——§8.3 结构性"
                        "关闭，须用户显式开启）")
    if not checks["auto_resume_eligible"]:
        failures.append("auto_resume=%r 不在 %s（manual / notify 永不 "
                        "prime）" % (auto_resume,
                                    ", ".join(PRIMER_AUTO_RESUME_MODES)))
    if not checks["user_authorized"]:
        failures.append('authorization.source=%r 不是 "user"（模型调用'
                        "授权必须用户明确授权）" % (source,))
    if failures:
        reason = "三重授权未通过，拒绝 prime：" + "；".join(failures)
    else:
        reason = "三重授权全部通过（primer_enabled + auto_resume + user " \
                 "source），允许 prime"
    return {"authorized": not failures, "checks": checks, "reason": reason}


# —— 传输层（默认 stdlib urllib；注入面 = urlopen 等价物） ——

def make_default_transport(max_bytes=_DEFAULT_MAX_BYTES):
    """构造默认 transport（stdlib urllib，§37 条款 3/4/6 的 primer 对应）。

    返回 transport(url, body, headers, timeout) -> (status_code:int,
    body:bytes)：
      - POST 请求（body 原样透传，headers 含 x-api-key——本函数不接触
        key 值，凭证纪律由 _prime_once_unchecked 侧承担）；
      - opener 复用 _http.build_default_opener（重定向禁用：任何 3xx
        都不跟随、不发起第二次请求）；
      - 响应体最多读 max_bytes+1 字节（§37 条款 4）；
      - timeout 透传给 opener（§37 条款 3）。
    """
    opener = build_default_opener()

    def _transport(url, body, headers, timeout):
        request = urllib.request.Request(url, data=body, headers=headers,
                                         method="POST")
        response = opener.open(request, timeout=timeout)
        try:
            payload = response.read(max_bytes + 1)
            status = getattr(response, "status", None)
            if status is None:
                status = response.getcode()
        finally:
            response.close()
        return status, payload

    return _transport


def _resolve_base_url(config_path=None):
    """zcode provider 配置容错读 baseURL（P0-QP-00 实测形态）。

    读取口径与 credentials._from_zcode_config 同构（provider →
    PROVIDER_KEY → options → baseURL），任何失败（文件缺失 / OSError /
    非 JSON / 结构不符 / 值非 https / host 不在 ALLOWED_HOSTS）→ 回退
    内置默认端点——绝不向 allowlist 外 host 发送凭证（§37 条款 5）。
    纯本地只读，零网络。
    """
    if config_path is None:
        config_path = os.path.expanduser(_ZCODE_CONFIG_PATH)
    try:
        with open(config_path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        data = None
    base = None
    if isinstance(data, dict):
        providers = data.get("provider")
        if isinstance(providers, dict):
            entry = providers.get(PROVIDER_KEY)
            if isinstance(entry, dict):
                options = entry.get("options")
                if isinstance(options, dict):
                    candidate = options.get("baseURL")
                    if isinstance(candidate, str) and candidate != "":
                        base = candidate
    if isinstance(base, str) and base.startswith("https://") \
            and urllib.parse.urlsplit(base).hostname in ALLOWED_HOSTS:
        return base.rstrip("/")
    return _DEFAULT_BASE_URL


def _build_request_body():
    """一次最小模型调用的请求体（P0-QP-00 §1.2 实测形态，冻结字面）。"""
    payload = {
        "model": PRIMER_MODEL,
        "max_tokens": PRIMER_MAX_TOKENS,
        "messages": [{"role": "user", "content": PRIMER_MESSAGE}],
    }
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


# —— 单次尝试与错误分类（异常只取类型名，§37 纪律） ——

def _classify_status(status):
    """HTTP 状态 → 错误分类（对齐 _http._status_error 口径）：
    401/403 → auth；3xx → network（重定向被禁）；其余非 2xx →
    unavailable。"""
    if status in (401, 403):
        return "auth"
    if isinstance(status, int) and 300 <= status < 400:
        return "network"
    return "unavailable"


def _int_or_none(value):
    """usage 计数容错提取：非 bool int → int；其余 → None（不虚构）。"""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _attempt_once(transport, url, headers, body, timeout):
    """单次传输尝试 → 结构化 outcome dict（single-attempt 的唯一执行面）。

    返回 {"ok", "ambiguous_timeout", "kind", "detail", "tokens_in",
    "tokens_out"}：ok=True 时 kind/detail 为 None 且 tokens 已提取；
    ok=False 时 kind ∈ PRIMER_ERROR_KINDS，detail 只含安全令牌——异常
    类型名（如 "socket.timeout" / "URLError"）、"HTTP <状态码>"、
    "bounded_response_exceeded" / "JSONDecodeError" /
    "response_not_object" / "invalid_status"，绝不透传异常文本 / URL /
    响应体。ambiguous_timeout=True 仅限网络超时类（socket.timeout 及
    URLError 包裹的超时 reason）——语义是「请求可能已到达 provider、
    结果未知」，调用方（_prime_once_unchecked）据此以 post-refresh 确
    认代替重发；
    其余失败（非超时 URLError / OSError、HTTP 状态码、解析错误）均为
    明确未到达 provider 或结果明确无效，绝不是歧义超时。
    """
    outcome = {"ok": False, "ambiguous_timeout": False, "kind": None,
               "detail": None, "tokens_in": None, "tokens_out": None}
    try:
        status, raw = transport(url, body, headers, timeout)
    except socket.timeout:
        # 超时：provider 侧可能已执行（响应丢失）——结果未知（歧义），
        # 绝不自动重发（P0-QP-07），由 _prime_once_unchecked 做
        # post-refresh 确认
        outcome["kind"], outcome["detail"] = "network", "socket.timeout"
        outcome["ambiguous_timeout"] = True
        return outcome
    except urllib.error.HTTPError as exc:
        # 非 2xx / 被禁重定向（默认传输路径）；状态码分类（非歧义失败）
        code = getattr(exc, "code", None)
        outcome["kind"] = _classify_status(code)
        outcome["detail"] = "HTTPError"
        return outcome
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", None)
        if isinstance(reason, socket.timeout):
            # URLError 包裹的超时：同为歧义超时（请求可能已到达 provider）
            outcome["kind"], outcome["detail"] = "network", "URLError"
            outcome["ambiguous_timeout"] = True
        else:
            outcome["kind"], outcome["detail"] = "network", "URLError"
        return outcome
    except OSError as exc:
        outcome["kind"] = "network"
        outcome["detail"] = type(exc).__name__
        return outcome
    except Exception as exc:  # noqa: BLE001 —— 契约外异常：类型名即全部
        outcome["kind"] = "unknown"
        outcome["detail"] = type(exc).__name__
        return outcome

    if not isinstance(status, int):
        outcome["kind"], outcome["detail"] = "malformed", "invalid_status"
        return outcome
    if not 200 <= status < 300:
        outcome["kind"] = _classify_status(status)
        outcome["detail"] = "HTTP %d" % (status,)
        return outcome
    if len(raw) > _DEFAULT_MAX_BYTES:
        outcome["kind"] = "malformed"
        outcome["detail"] = "bounded_response_exceeded"
        return outcome
    try:
        parsed = json.loads(raw)
    except ValueError:
        outcome["kind"], outcome["detail"] = "malformed", "JSONDecodeError"
        return outcome
    if not isinstance(parsed, dict):
        outcome["kind"], outcome["detail"] = "malformed", \
            "response_not_object"
        return outcome
    usage = parsed.get("usage")
    if isinstance(usage, dict):
        outcome["tokens_in"] = _int_or_none(usage.get("input_tokens"))
        outcome["tokens_out"] = _int_or_none(usage.get("output_tokens"))
    outcome["ok"] = True
    return outcome


# —— 快照容错提取（观察面读法：缺 / 坏按「不可确认」处理） ——

def _windows_of(detail):
    """resolve_quota_detail 形状的 detail → windows 列表或 None。

    detail 非 dict / snapshot 非 dict / windows 非 list → None（基线或
    确认面不可用——物化判定按「无法确认」保守处理，绝不虚构）。"""
    if not isinstance(detail, dict):
        return None
    snapshot = detail.get("snapshot")
    if not isinstance(snapshot, dict):
        return None
    windows = snapshot.get("windows")
    return windows if isinstance(windows, list) else None


def _executable_confirmed(post_detail, post_windows):
    """post refresh 可用时的 executable 复评（成功路径与歧义超时确认
    路径共用；复用 epoch.evaluate_epoch——weekly 优先语义不复制逻辑）。

    post_detail / post_windows 不可用 → False；词汇外 provider 状态
    （evaluate_epoch 抛 ValueError）→ 保守不可执行。"""
    if post_detail is None or post_windows is None:
        return False
    try:
        evaluation = evaluate_epoch(provider_status=post_detail.get("status"),
                                    windows=post_windows)
        return bool(evaluation["executable"])
    except ValueError:
        return False


def _safe_fetch(fetch_refresh, repo_root):
    """fetch_refresh 容错包装（对齐 watcher._safe_fetch）：异常只取类型
    名，返回 (detail|None, error_type_name|None)。"""
    try:
        return fetch_refresh(repo_root), None
    except Exception as exc:  # noqa: BLE001 —— 容错面：类型名即全部信息
        return None, type(exc).__name__


def _default_fetch_refresh(repo_root):
    """生产抓取入口：quota 强制刷新（规格缺省——prime 后的确认与 prime
    前的基线都必须绕过新鲜缓存，见 resolver.resolve_quota_detail）。"""
    return resolver.resolve_quota_detail(repo_root, force_refresh=True)


# —— 结果装配（冻结键；authorized 恒 True——调用方契约见 docstring） ——

def _result(*, primed, materialized, executable, tokens_in, tokens_out,
            latency_ms, idempotent, error):
    """按 PRIMER_RESULT_KEYS 冻结顺序装配返回 dict。

    authorized 恒 True：_prime_once_unchecked 的调用契约是「已过
    prime_authorized 的机械授权闸」（RH-02 起闸内嵌在 public API；
    私有名仅供单元测试与内部已授权 wrapper）——凡进入本函数的路径都
    已隐含授权通过，字段仅作记录（规格「authorized 隐含」）。
    """
    return {
        "authorized": True,
        "primed": primed,
        "materialized": materialized,
        "executable": executable,
        "tokens": {"input": tokens_in, "output": tokens_out},
        "latency_ms": latency_ms,
        "idempotent": idempotent,
        "error": error,
    }


def _result_from_record(record):
    """durable 记录 → 冻结键返回（幂等命中路径；零网络零事件）。

    primed = error_kind 为空（调用完成）；error.detail 恒 None——异常
    类型名等细节不跨进程留存（durable 记录冻结七键只存 error_kind），
    重入方拿到分类即可决策，细节看当次执行的 journal / 日志。"""
    error_kind = record.get("error_kind")
    failed = isinstance(error_kind, str) and error_kind != ""
    return _result(
        primed=not failed,
        materialized=bool(record.get("materialized")),
        executable=bool(record.get("executable")),
        tokens_in=record.get("tokens_in"),
        tokens_out=record.get("tokens_out"),
        latency_ms=record.get("latency_ms"),
        idempotent=True,
        error=None if not failed else {"kind": error_kind, "detail": None})


def _journal_prime(repo_root, *, boundary_id, provider_identity_hash,
                   materialized, executable, tokens_in, tokens_out,
                   latency_ms, idempotent):
    """best-effort 追加 window_primed 事件（二级审计面）。

    事件字段（冻结）：boundary_id / provider_identity_hash /
    materialized / executable / tokens{input,output} / latency_ms /
    idempotent。durable primer.json 已是幂等真相源，journal 失败
    （OSError，如 Windows AV 目录锁）降级不抛——绝不遮蔽 prime 本身的
    结果（模块 docstring「接线边界」节）；事件不含任何凭证材料（§37）。

    落点（v2.2 C5a 起）：控制面 journal
    .glm-conductor/quota/events.jsonl（journal.append_control_plane_
    event；与 watcher.json / primer.json 同层）——window_primed 无任务
    上下文，不再写 tasks/<伪任务>/events.jsonl 目录（C4 reviewer P2
    的 orphaned 噪声正解）。事件字段零变化。
    """
    event = {
        "event": PRIMER_JOURNAL_EVENT,
        "boundary_id": boundary_id,
        "provider_identity_hash": provider_identity_hash,
        "materialized": materialized,
        "executable": executable,
        "tokens": {"input": tokens_in, "output": tokens_out},
        "latency_ms": latency_ms,
        "idempotent": idempotent,
    }
    try:
        _append_control_plane_event(repo_root, event)
    except OSError:
        pass


# —— public 执行 API：机械授权闸内嵌（RH-02，方案 A） ——

def _load_task_policy(repo_root, task_id):
    """json 容错读任务 state 的 execution_policy 块 → (policy, None)。

    任何结构性缺失（任务目录 / state.json 不存在、坏 JSON、顶层非
    dict、缺 execution_policy 块 / 块非 dict）→ (None, 中文 reason)
    ——授权方向 fail-closed（理由同模块 docstring「fail-closed 不对称」
    节）：policy 缺块 / 坏块一律按保守拒绝解释，绝不 fail-open 放行。

    纪律：quota 包不 import runtime.state / runtime.task_manager——
    布局常量本地声明（TASK_STATE_DIR_PARTS / TASK_STATE_FILE_NAME，
    与任务侧布局同源命名），json 直读（watcher.determine_mode 同款
    惯例）。只读、零写入。
    """
    path = os.path.join(str(repo_root), *TASK_STATE_DIR_PARTS,
                        task_id, TASK_STATE_FILE_NAME)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except OSError:
        return None, ("任务 %s 不存在（无 state.json），保守拒绝 prime"
                      % (task_id,))
    except ValueError:
        return None, ("任务 %s 的 state.json 损坏（非合法 JSON），保守"
                      "拒绝 prime" % (task_id,))
    if not isinstance(data, dict):
        return None, ("任务 %s 的 state.json 顶层不是 JSON 对象，保守"
                      "拒绝 prime" % (task_id,))
    policy = data.get("execution_policy")
    if not isinstance(policy, dict):
        return None, ("任务 %s 的 state 缺少合法 execution_policy 块，"
                      "保守拒绝 prime" % (task_id,))
    return policy, None


def _denied_prime(reason):
    """prime_authorized 拒绝路径的冻结九键返回（零副作用形状）。

    冻结键 = PRIME_AUTHORIZED_RESULT_KEYS（PRIMER_RESULT_KEYS 八键 +
    reason）：authorized / primed / materialized / executable 全
    False、tokens 双 None、latency_ms None、idempotent False、
    error None。拒绝路径零 transport、零 quota 网络、零 primer.json
    写、零 window_primed journal、零窗口消费——不制造任何 durable
    痕迹。不抛裸 ValueError 表达未授权（结构化拒绝，§4.4）。"""
    result = _result(primed=False, materialized=False, executable=False,
                     tokens_in=None, tokens_out=None, latency_ms=None,
                     idempotent=False, error=None)
    result["authorized"] = False
    result["reason"] = reason
    return result


def prime_authorized(repo_root, task_id, *, boundary_id,
                     provider_identity_hash, transport=None, clock=None,
                     fetch_refresh=None) -> dict:
    """Window Primer 唯一 public 执行 API：机械授权闸内嵌（RH-02）。

    流程（顺序冻结）：
      0. task_id 校验（非空 str，否则 ValueError——定位 state 文件的
         结构参数，先于一切 I/O）；
      1. 授权闸（未授权零副作用优先——先于其余参数校验）：json 容错
         读任务 state 的 execution_policy 块 → authorize_prime 三重闸；
         任务 / policy 缺失坏块或三闸任一不过 → 冻结九键结构化拒绝
         （零 transport、零 quota 网络、零 primer.json 写、零
         window_primed journal、零窗口消费）；
      2. 授权通过 → 透传 _prime_once_unchecked（boundary_id /
         provider_identity_hash / transport / clock / fetch_refresh
         的校验与执行语义全部沿用——参数非法仍 ValueError，八键返回
         原样，authorized 恒 True）。

    参数：repo_root / boundary_id / provider_identity_hash /
    transport / clock / fetch_refresh 语义与 _prime_once_unchecked
    逐字一致；task_id 为任务标识（定位 <repo_root>/.glm-conductor/
    tasks/<task_id>/state.json）。

    返回（键随路径二态，docstring 冻结）：
      - 拒绝路径：PRIME_AUTHORIZED_RESULT_KEYS 冻结九键 =
        {"authorized": False, "primed": False, "materialized": False,
         "executable": False, "tokens": {"input": None, "output": None},
         "latency_ms": None, "idempotent": False, "error": None,
         "reason": <authorize_prime 的 reason，或任务 / policy 缺失
         坏块的中文 reason>}；
      - 授权路径：PRIMER_RESULT_KEYS 冻结八键（_prime_once_unchecked
         返回原样透传，无 reason 键）。

    异常：task_id 非法 ValueError；授权通过后的参数非法沿用
    _prime_once_unchecked 的 ValueError；PrimerStoreError 同其契约。
    """
    # —— 0. 结构参数校验（先于一切 I/O，中文 ValueError） ——
    if not isinstance(task_id, str) or task_id == "":
        raise ValueError(
            "prime_authorized：task_id 必须是非空 str，得到 %r"
            % (task_id,))
    # —— 1. 授权闸（未授权零副作用优先：即使其余参数非法，未授权也
    #         必须落结构化拒绝而非参数异常——拒绝路径绝不进入 transport） ——
    policy, load_reason = _load_task_policy(repo_root, task_id)
    if policy is None:
        return _denied_prime(load_reason)
    verdict = authorize_prime(policy)
    if not verdict["authorized"]:
        return _denied_prime(verdict["reason"])
    # —— 2. 已授权执行（参数校验与执行语义沿用私有执行面） ——
    return _prime_once_unchecked(
        repo_root, boundary_id=boundary_id,
        provider_identity_hash=provider_identity_hash,
        transport=transport, clock=clock, fetch_refresh=fetch_refresh)


# —— 私有执行面：单飞幂等执行（原 prime_once，RH-02 起仅供单元测试
#    与内部已授权 wrapper；public 面是 prime_authorized） ——

def _prime_once_unchecked(repo_root, *, boundary_id,
                          provider_identity_hash,
                          transport=None, clock=None,
                          fetch_refresh=None) -> dict:
    """Window Primer 单飞幂等执行（§8.1 职责的机械化；RH-02 起私有名
    ——仅供单元测试与内部已授权 wrapper，行为与 RH-01 后形态零变化）。

    参数：
      - repo_root：仓库 / 账本根（primer.json 与 quota 缓存的根）；
      - boundary_id：prime 时点的旧 boundary / epoch_id（幂等键之一；
        非空 str，否则 ValueError）；
      - provider_identity_hash：provider identity 指纹（幂等键之二；
        派生口径同 runtime.quota.identity.compute_provider_identity_hash
        ——来源+凭证 sha256 前 16 位；非空 str，否则 ValueError）；
      - transport：注入传输层 transport(url, body, headers, timeout) ->
        (status:int, body:bytes)；None → make_default_transport()（测试
        必注入——零真实网络零真实模型调用）；
      - clock：注入时钟（零参可调用，返回 datetime 或 ISO 串，仅决定
        primed_at）；None → 当前 UTC；
      - fetch_refresh：注入 quota 强制刷新 fetch_refresh(repo_root) →
        resolve_quota_detail 形状 detail；None →
        resolver.resolve_quota_detail(force_refresh=True)（成功与歧义
        超时路径：prime 前基线与 prime 后确认各调一次；非歧义失败仅
        prime 前基线一次，零确认刷新）。

    流程（顺序冻结）：
      1. 幂等闸：durable 记录查 (identity, boundary_id)——命中 → 直接
         返回既有结果（idempotent=True，零模型调用零 quota 网络、零
         journal 事件——同键重入零网络零事件）；
      2. 凭证与端点（本地只读）：resolve_credential() 只进内存；无凭证
         → 结构化失败（no-credential，零传输）；baseURL 容错读 zcode
         provider 配置，host 过 ALLOWED_HOSTS 闸；
      3. prime 前基线快照（强制刷新；失败容忍 → 基线 None）；
      4. 一次最小模型调用（single-attempt：绝不自动重发——重发即是
         P0-QP-07 要防的「同旧 boundary 连发多个 prime」）；
      5. 分支收尾：
           成功 → 物化确认：prime 后强制 quota refresh，只看 prime
             前后窗口身份变化（epoch.same_epoch）——HTTP 200 不是证据、
             百分比下降不是证据（§C4 红线）；
           歧义超时（ambiguous_timeout：请求可能已到达 provider、结果
             未知）→ 绝不重发，同样执行 prime 后强制 refresh：epoch
             推进 → materialized=True（timeout + materialized=True 是
             合法组合），未确认（同 epoch / 刷新不可用）→
             materialized=False；随后按失败落账（error_kind 保持
             "network"）；
           其余失败（no-credential / auth / HTTP 5xx / malformed /
             非歧义 network / unknown）→ 零确认面直接失败落账；
         executable = materialized 且 epoch.evaluate_epoch（provider
         AVAILABLE 且无阻塞 EXHAUSTED 窗——weekly 优先语义复用既有
         判定，不复制逻辑）；
      6. durable 落账（primer.json 七键记录）+ best-effort journal
         window_primed 事件；失败尝试同样落账（error_kind 非空——
         P0-QP-07：同旧 boundary 绝不连发）。

    返回（PRIMER_RESULT_KEYS 冻结 8 键；authorized 恒 True——调用契约
    是仅由 prime_authorized 授权后调用（或单元测试直调），本函数不
    重复授权）：
      {"authorized": True,
       "primed": bool（模型调用完成，即 error 为 None——primed 语义与
                  materialized 正交，timeout + materialized=True 是合法
                  组合）,
       "materialized": bool（且仅当窗口身份推进被二次 refresh 确认）,
       "executable": bool（materialized 且 epoch 复评可执行——
                        refresh 后仍有阻塞窗 → False，无 ActivationReady
                        概念产生，归 C5）,
       "tokens": {"input": int|None, "output": int|None},
       "latency_ms": int|None（模型调用全程耗时）,
       "idempotent": bool（命中既有 durable 结果时 True）,
       "error": None | {"kind": <PRIMER_ERROR_KINDS>,
                        "detail": <异常类型名 / "HTTP <code>" / 安全令牌>}}

    异常：参数非法 ValueError；primer.json 写入在 PermissionError 有界
    重试耗尽后仍失败 → PrimerStoreError（绝不静默吞——账本失写意味着
    幂等闸失效，必须让调用方看见）。
    """
    # —— 参数校验（先于一切 I/O，中文 ValueError） ——
    if not isinstance(boundary_id, str) or boundary_id == "":
        raise ValueError(
            "_prime_once_unchecked：boundary_id 必须是非空 str，得到 %r"
            % (boundary_id,))
    if not isinstance(provider_identity_hash, str) \
            or provider_identity_hash == "":
        raise ValueError(
            "_prime_once_unchecked：provider_identity_hash 必须是非空 "
            "str，得到 %r" % (provider_identity_hash,))
    for name, callable_param in (("transport", transport),
                                 ("clock", clock),
                                 ("fetch_refresh", fetch_refresh)):
        if callable_param is not None and not callable(callable_param):
            raise ValueError(
                "_prime_once_unchecked：%s 必须是可调用对象或 None，"
                "得到 %r" % (name, type(callable_param).__name__))
    fetch = fetch_refresh if fetch_refresh is not None \
        else _default_fetch_refresh
    send = transport if transport is not None else make_default_transport()

    # —— 1. 幂等闸（零网络零事件路径） ——
    existing = _read_record(repo_root, provider_identity_hash, boundary_id)
    if existing is not None:
        return _result_from_record(existing)

    moment = _normalize_now(clock() if clock is not None else None)
    primed_at = _format_iso_z(moment)

    # —— 2. 凭证与端点（本地只读；无凭证 → 结构化失败，零传输） ——
    def _fail(kind, detail, *, materialized=False, executable=False,
              tokens_in=None, tokens_out=None):
        """失败收尾：七键落账 + best-effort journal + 冻结键返回。

        默认 materialized/executable=False（明确未到达 provider 的失败，
        零确认面）；歧义超时路径传入 post-refresh 确认结果（RH-01：
        超时绝不重发，落账后同键重入即命中幂等闸——零传输零刷新）。"""
        record = {
            "primed_at": primed_at,
            "materialized": materialized,
            "executable": executable,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "latency_ms": latency_ms,
            "error_kind": kind,
        }
        _put_record(repo_root, provider_identity_hash, boundary_id, record)
        _journal_prime(repo_root, boundary_id=boundary_id,
                       provider_identity_hash=provider_identity_hash,
                       materialized=materialized, executable=executable,
                       tokens_in=tokens_in, tokens_out=tokens_out,
                       latency_ms=latency_ms, idempotent=False)
        return _result(primed=False, materialized=materialized,
                       executable=executable, tokens_in=tokens_in,
                       tokens_out=tokens_out, latency_ms=latency_ms,
                       idempotent=False,
                       error={"kind": kind, "detail": detail})

    _credential_source, api_key = resolve_credential()
    if not isinstance(api_key, str) or api_key == "":
        latency_ms = 0  # 未发起任何传输：零耗时
        return _fail("no-credential", None)
    url = _resolve_base_url() + _MESSAGES_PATH
    headers = {
        # P0-QP-00 实测鉴权格式：x-api-key（Anthropic 原生），首发即 200
        "x-api-key": api_key,
        "anthropic-version": ANTHROPIC_VERSION,
        "content-type": "application/json",
        "accept": "application/json",
    }
    body = _build_request_body()

    # —— 3. prime 前基线快照（强制刷新；失败容忍 → 基线 None） ——
    pre_detail, _pre_error = _safe_fetch(fetch, repo_root)
    pre_windows = _windows_of(pre_detail)

    # —— 4. 一次最小模型调用（single-attempt：绝不自动重发——P0-QP-07） ——
    started = time.perf_counter()
    outcome = _attempt_once(send, url, headers, body,
                            DEFAULT_PRIME_TIMEOUT_SECONDS)
    latency_ms = int(round((time.perf_counter() - started) * 1000))

    if not outcome["ok"]:
        if not outcome["ambiguous_timeout"]:
            # 非歧义失败（明确未到达 provider / 结果明确无效）→ 零确认面
            return _fail(outcome["kind"], outcome["detail"])
        # —— 5a. 歧义超时确认（RH-01）：绝不重发，post-refresh 代替 ——
        # 超时表示「结果未知」而非「provider 一定没有执行」：epoch 推进
        # → materialized=True（timeout + materialized=True 是合法组合）；
        # 同 epoch / 刷新不可用 → materialized=False（宁可 False 不虚构）
        post_detail, _post_error = _safe_fetch(fetch, repo_root)
        post_windows = _windows_of(post_detail)
        materialized = (pre_windows is not None
                        and post_windows is not None
                        and not same_epoch(pre_windows, post_windows))
        executable = materialized and _executable_confirmed(post_detail,
                                                            post_windows)
        return _fail(outcome["kind"], outcome["detail"],
                     materialized=materialized, executable=executable,
                     tokens_in=outcome["tokens_in"],
                     tokens_out=outcome["tokens_out"])

    # —— 5. 物化确认：prime 后强制 quota refresh，只看窗口身份变化 ——
    post_detail, _post_error = _safe_fetch(fetch, repo_root)
    post_windows = _windows_of(post_detail)
    materialized = (pre_windows is not None and post_windows is not None
                    and not same_epoch(pre_windows, post_windows))

    # executable：复用 epoch.evaluate_epoch（weekly 优先语义不复制）；
    # 且必须以物化确认为前提——未经确认的 AVAILABLE 不构成新 executable
    # epoch（§C4 红线：200 与百分比都不是证据）
    executable = materialized and _executable_confirmed(post_detail,
                                                        post_windows)

    # —— 6. durable 落账 + best-effort journal ——
    record = {
        "primed_at": primed_at,
        "materialized": materialized,
        "executable": executable,
        "tokens_in": outcome["tokens_in"],
        "tokens_out": outcome["tokens_out"],
        "latency_ms": latency_ms,
        "error_kind": None,
    }
    _put_record(repo_root, provider_identity_hash, boundary_id, record)
    _journal_prime(repo_root, boundary_id=boundary_id,
                   provider_identity_hash=provider_identity_hash,
                   materialized=materialized, executable=executable,
                   tokens_in=record["tokens_in"],
                   tokens_out=record["tokens_out"],
                   latency_ms=latency_ms, idempotent=False)
    return _result(primed=True, materialized=materialized,
                   executable=executable, tokens_in=record["tokens_in"],
                   tokens_out=record["tokens_out"], latency_ms=latency_ms,
                   idempotent=False, error=None)
