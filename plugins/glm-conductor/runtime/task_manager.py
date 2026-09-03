#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GLM Conductor v2 派发生命周期事务边界层（v2.0.1 加固工作包 H4，审查项 P1-3）。

职责：
    把「plan → lease → 状态转换 → dispatch.active 记账 → save → journal
    事件」的分步手工拼接固化为四个高层 API。在此之前，真实生命周期要求
    主会话按顺序完成 plan_dispatch → acquire_lease → transition ready→
    running → dispatch.active 记账 → save_state → Agent 调用——中间任一
    点中断都会留下「计划已批准但未加锁」「已加锁但未记 running」
    「running 已落盘但 Agent 未真正启动」等半状态，恢复语义散落在约定
    里。本模块把固定顺序固化进代码：主会话只调这四个入口，不再亲手拼
    接细粒度状态一致性。

    本模块是唯一做 I/O 编排的派发事务层；dispatcher 保持纯决策器地位
    不变（零 I/O，只产出决策 dict），lease 只提供 owner map 原语。

四 API 时序（文字图，固定 journal → lease → state → save 的拼接顺序）：

    prepare_dispatch(u)            commit_dispatch(u)
      1 load_state                   1 load_state
      2 单元须 ready                 2 单元须 ready
      3 plan_dispatch（纯决策）      3 租约在位校验（归一路径 ∧ owner==u）
      4 落选 → TaskManagerError     4 ready→running（§62 表内转换）
      5 acquire_lease（§78 幂等）    5 active 记账（幂等防御）+
      6 create_permit（v2.1 M2）       任务级非执行态顺手转 executing
      7 journal dispatch_prepared   6 save_state（单次）
        + dispatch_permit_created   7 journal implementation_started
      8 返回决策快照 + permit          ↓ 返回提交后的 state dict
        ↓ 不改单元状态、不 save
        ↓
    ──────────── [Agent 实施] ────────────
        ↓                              ↓
    abort_dispatch(u)              finish_unit(u, outcome=…)
      1 load_state                   1 outcome ∈ {completed,failed,cancelled}
      2 running → 拒绝（已提交，    2 load_state；单元须存在
        不得静默回退）               3 状态须 ∈ {running, verifying}
      3 release_lease（全部释放）    4 completed → RB-1 完成证据门：
      4 active 残留 → 移除 + save      fresh_unit_verification 须全部
      5 journal dispatch_aborted       命中（missing/stale/partial →
      6 invalidate 未消费 permit +     拒绝且零副作用；git/指纹失败
        journal dispatch_permit_       fail-closed 同拒）
        invalidated                  5 running+completed → 先 verifying 再
      7 返回 state dict                completed（§70 父验证语义 = 两次
                                       表内转换）；其余单步直达
                                     6 release_lease + active 移除
                                     7 save_state（单次）
                                     8 journal unit_finished
                                       ↓ 返回 state dict

崩溃窗口恢复映射（确定性，文档化契约）：
    prepare 后崩   → 单元仍 ready + 租约在位（§78 同 owner 幂等，自有
                     租约不挡后续 plan）→ 可 commit_dispatch 完成提交，
                     可 abort_dispatch 回退释放——两条路径都安全；
    commit 后崩    → 单元 running + 无验证证据 → 由既有 reconcile 按
                     §69 证据三分恢复（ownership 内无残留 → ready；
                     残留 + 绑定当前改动的新鲜验证 → completed；残留
                     无新鲜证据 → verifying）——本模块不重造恢复逻辑。
    finish 单次 save 原子落盘：崩在 save 前单元仍在 running/verifying
    （同 commit 后窗口，reconcile 接管）；崩在 save 后即终态，reconcile
    对终态零建议（§68 completed 不重跑）。

租约生命周期闭环（v2.0.1 加固 H5，审查项 P1-4/P1-5）：
    prepare 以 LEASE_DEFAULT_TTL_SECONDS（1800 秒）保守 TTL 落盘
    expires_at（session_id/generation/heartbeat_at 同记录落盘），长期
    实施由 runtime.lease.renew_lease 心跳续约；崩溃后 recover_leases
    对租约做 stale / active / expired_running 三分对账——stale（owner
    已不在活跃写相）自动释放 + lease_recovered 事件（零释放不落事件），
    expired_running（活跃写相但已过期——worker 可能仍在写）仅上报、
    裁决归主会话——崩溃后无需人工删除 leases.json。

派发 permit 接线（v2.1 M2 前半，wu-21-02，计划 §6.3/§6.6）：
    真实 dogfood 中主会话曾完全绕过 prepare/commit 手工派发 Agent；
    v2.1 用「无 permit 即 deny」的 PreToolUse 门（wu-21-03 实施 hook
    侧）堵住 bypass，本模块是该门的数据层接线：prepare_dispatch 在
    租约获取之后为该单元签发一张落盘 permit（runtime.dispatch_wave
    的每 permit 一文件 + rename 消费防重放原语），mode 取 state
    execution_policy.worker_execution.default_mode（缺块 / 坏形状按
    default_execution_policy() 兜底），返回决策快照新增 "permit" 键
    （向后兼容增量，现有键全部保留）并落 journal
    dispatch_permit_created；主会话用 dispatch_wave.marker_for(
    permit_id) 构造 marker 放进 Agent prompt 派发，hook 侧 consume
    （本层不代消费——commit_dispatch / finish_unit 行为不变，未消费
    permit 随 DEFAULT_TTL_SECONDS 自然过期兜底）；abort_dispatch 在
    现有回退逻辑之后对该单元全部未消费 permit invalidate 并落
    dispatch_permit_invalidated（零失效不落事件）。

派发 wave 批量事务（v2.1 M4，wu-21-08；RB-21-03 事务补偿）：
    prepare_dispatch_wave 把「决策 → 全量租约 → 批量 permit → wave 记录
    → journal」固化为一次调用的事务入口，与 prepare_dispatch（单单元）
    并存；事务性 all-or-safe-degrade：wave 记录落盘时其全部成员租约与
    permit 已在位——绝不出现「wave 记录 2 单元但只有 1 张租约」。要点：
      - plan_dispatch 全量决策（带租约闸），批准集按 state.work_units
        原序（plan 内部是 topo 序，这里重排回账面原序）；
      - 批准集逐单元 acquire_lease；任一 LeaseConflictError → 释放本轮
        已获取的全部租约、冲突单元入 excluded 集合、剔除后重新 plan
        重试——excluded 单调增长保证有界终止；批准集空 →
        TaskManagerError（消息口径照抄 prepare_dispatch，零租约残留）；
      - 全部租约到位 → 逐成员 create_permit（携带 wave_id，临时持有，
        尚不落 wave）→ wave 记录（dispatch.waves[]，status="active"）
        随 save_state 一次落盘 → journal 单条 dispatch_wave_prepared
        （不逐单元重复 dispatch_prepared）；单元 wave 合法（只批 1 个
        也成 wave）；
      - 事务补偿（_compensate_failed_prepare）：permit 创建或
        save_state 失败 → 作废全部已建 permit + 释放本轮全部租约 +
        防御性移除盘上可能的半完成 wave 记录，journal 落
        transaction_aborted（补偿自身失败带 compensation_error，
        fail-closed 不静默），原始异常上抛——失败时无 wave、无本轮
        租约、无活跃 permit、无 prepared 事件；单单元 prepare_dispatch
        的 permit 落盘失败同样自动释放该租约（无需人工 abort_dispatch）；
      - wave 关闭归 finish_unit：收尾使某 active wave 的 units 全部进入
        completed/failed/cancelled/verifying → status="closed" +
        closed_at + journal wave_closed；无 waves 键零行为；
      - hook 侧成员资格校验（dispatch_wave.validate_wave_membership，
        wu-21-03 的 permit 门追加一环）使 closed / 重组后的旧 wave
        permit 不再放行；agent_launched 事件携带 wave_id。

有界并行预算接线（v2.1 §11.5/§12，wu-21-09）：
    prepare_dispatch / prepare_dispatch_wave 共享 _effective_worker_cap
    预算折算：cap_base 优先级「显式 max_workers 参数 →
    execution_policy.parallelism.max_workers（合法 1..4 int 时）→
    dispatch.max_workers（legacy 任务）→ 2」，有效预算
    eff = min(cap_base, execution_policy.effective_worker_budget(
    policy, quota_status))——§12 表：AVAILABLE→策略 max_workers /
    PRESSURE→1 / UNKNOWN→1 / EXHAUSTED→0。据此 dispatcher 不再整批
    挂起 UNKNOWN / PRESSURE（旧的 unknown_suppressed /
    pressure_suppressed / small_only 分支与 allow_small_under_pressure
    参数已删除）——预算收缩职责从决策器移交本层；prepare_dispatch 的
    dispatch_prepared 事件新增 effective_max_workers 字段（wave 事件
    已有 worker_budget，不加重复键）；wave 的 worker_budget 自然成为
    quota 调节后的值。并发默认 2：dispatcher.DEFAULT_MAX_WORKERS、
    state.new_task_state 的 dispatch.max_workers 默认、execution_policy
    parallelism 默认块三处口径一致；policy hard_limit=4 上限不变。

运行时额度解析接线（v2.1 §13，wu-21-10）：
    prepare_dispatch / prepare_dispatch_wave 的 quota_status 缺省值从
    caller-supplied "AVAILABLE" 改为 None——**绝不默认 AVAILABLE**：
    None 触发 _resolve_quota → runtime.quota.resolver.resolve_quota_status
    （层级：fresh cache → provider fetch → stale cache → UNKNOWN，
    绝不重试网络、异常不外泄、凭证零落盘），journal 落一条
    quota_resolved {"status", "source", "evaluated_at"} 后把解析出的
    status 送进既有 §12 预算折算与 plan_dispatch 决策链（wave 记录的
    quota_status 字段记解析后的实际值）；显式字符串原样直通——零解析、
    零网络、零事件，生产行为语义不变（显式声明优先；CLI 缺省同样走
    None→resolver，wu-21-15 起 wave-prepare 不再有 AVAILABLE 兜底）。
    UNKNOWN 在预算侧按 wu-21-09 已落地的
    语义折算为 1（不挂起），因此缺省调用在有凭证环境下更准、无凭证
    环境下更保守，都不改变「可派发」这一基本事实。

DRAINING 派发闸（v2.2 M4，wu-22-04；决策记录 D6/D7/D13）：
    v2.1 派发闸只认 provider 四态——provider 报 AVAILABLE 但剩余
    24% 时仍继续扩大任务（dogfood 实录）。本单元把 M2 纯决策器
    runtime.quota.control.evaluate_task_quota_phase 的 execution
    phase 决策接进 prepare_dispatch / prepare_dispatch_wave，成为
    既有额度四态闸之后的新一层（prepare 层硬拒绝，D6）：
      - 零网络：只读 <repo_root>/.glm-conductor/quota-cache.json
        （resolver._load_cache / _cache_path 容错原语——缺失 /
        坏 JSON / status 词汇陈旧 / fetched_at 不可解析一律视为
        无缓存），绝不触发 provider 抓取；
      - fail-open 先于闸门：无有效缓存 → 闸不激活，预算折算与
        state 落盘零改动（缓存缺失/陈旧绝不阻塞或降级既有派发）；
      - 闸激活（有效缓存）：DRAINING / BLOCKED → 在任何租约 /
        permit / wave 副作用之前硬拒（TaskManagerError 消息以
        "execution phase" 开头：注明 execution_phase、驱动窗口
        剩余、§18.1 收尾白名单与 reset/wake 建议；拒绝路径先按
        D13 落回填再 raise）；PRESSURE → dispatch_budget=1 并入
        _effective_worker_cap 的单一 min 折算（与既有 provider
        维度 PRESSURE→1 合流，不产生第二预算源）；NORMAL → 照旧；
      - D13 回填：state.quota.execution_phase 在 prepare 时点写入
        （放行与拒绝两路径都落盘；validator 对 quota 未知键宽松；
        单次 save 纪律——单单元入口此为调用唯一 save_state，wave
        入口随 wave 记录的既有落盘点，不新增写）；
      - 白名单不加闸（D6）：plan_dispatch 底层零改动（仍只认
        provider 四态）；running 单元不强杀；finish/verify/review/
        checkpoint/wake 路径零接触。

用户授权续跑（v2.1 §14/§22.7，wu-21-11 Authorized Quota Resume）：
    把「EXHAUSTED 之后怎么办」从模型即兴变成四态授权矩阵 + window
    预算记账。prepare 两个 API 解析出 EXHAUSTED 时只抛 waiting_quota
    口径错误、不自动转态——转态是编排决策，由主会话显式调用本模块
    的四个入口完成：
      - handle_quota_exhausted：EXHAUSTED 转态链（任务与未终态单元转
        waiting_quota + dispatch.active 清空 + manifest 刷新 +
        recommended_resume_at 计算 + 授权矩阵裁决 wake 指令）；
      - quota_wake_prompt：自足唤醒 prompt（宿主实测：automation wake
        是同会话续行、SessionStart 不重放，prompt 必须自带 task_id /
        恢复步骤 / 红线）；
      - record_quota_wake：v2.1 legacy arm-time 窗口扣减记账（C1a 起
        标记 legacy——v2.2 persistent path 禁止调用：automation
        arm/fire/create 一律不消费窗口预算（D15-g），消费点唯一合法
        位置 = Resume Controller 成功接受 epoch 的 resume commit
        point（修正计划 §15.1，C1b 落新 API）；行为仍为写
        continuity.consumed_quota_windows、与 automation 存活解耦、
        绝不回滚——仅限 v2.1 one-shot 兼容入口）；
      - resume_from_quota：唤醒会话 / SessionStart 的恢复首步（RB-21-01
        恢复对账语义：status 缺省强制刷新额度；AVAILABLE/PRESSURE 恢复
        时逐 waiting_quota 单元按中断来源 quota_interrupted_from 分类
        落点——running 来源先 reconcile_agent_run 四分对账，禁止盲目
        回 ready；返回增量 unit_recovery map；EXHAUSTED/UNKNOWN 保守
        等待，EXHAUSTED 经强刷缓存 snapshot 按 scheduler.plan_resume
        统一口径给建议恢复时刻）。
    授权矩阵（execution_policy.continuity.auto_resume，§14.2-§14.5）：
      - manual → 不建自动化，未来 SessionStart 恢复注入提示用户；
      - notify → 只允许提醒类 automation（runtime 只给 prompt 文本，
        "required" 语义是「授权允许自动恢复执行」，notify 不允许）；
      - auto_once / until_done → 必须 authorization.source == "user"
        且剩余窗口预算（max_quota_windows - consumed_quota_windows）
        > 0 才产出 wake.required=True；预算耗尽 → 任务转 waiting_user
        + journal auto_resume_authorization_exhausted（§14.5，不得再
        创建任何自动化唤醒）。
    until_done 惰性逐窗：任一时刻最多 1 个活跃 wake 由主会话保证
    （runtime 在 handle_quota_exhausted 返回里带 wake 指令即此模式）；
    每次成功恢复后再次 EXHAUSTED 时主会话重走 handle_quota_exhausted。
    wake 红线（宿主实测锁定）：一次性 automation（maxRuns=1）自完成、
    绝不重试 CronDelete/CronUpdate（本机已知 glitch）、清理失败容忍、
    发布类动作征询用户。

单元验证证据归属绑定（v2.0.1 加固 H6，审查项 P1-7）+ RB-1 完成证据门
（release hardening WU-P2，计划 §2 RB-1）：
    record_unit_verification 是单元级验证证据的唯一推荐写入口——主
    会话亲自跑完单元验证命令后调用（时序：commit_dispatch → [Agent
    实施] → record_unit_verification → finish_unit）。事件显式携带
    unit 字段，reconcile 恢复对账按 unit 逐字精确匹配：相同 command /
    重叠 ownership 的单元之间不存在错误复用证据的空间。任务级（完成
    门口径）证据仍走 runtime.state.record_verification，两者口径正交。

    H6 起 evidence 不只是恢复口径，还是完成口径：finish_unit(outcome=
    "completed") 进入转换前必须先过 reconcile.fresh_unit_verification
    完成证据门——全部 required command 各存在一条绑定当前指纹的新鲜
    pass 事件（all-match）才允许 completed；missing / stale / partial /
    wrong-unit / legacy 无 unit 字段证据一律 TaskManagerError 且零副作用
    （不写任何 journal 事件，含拒绝事件——计划 §2.3.4）；git / 指纹
    读取失败同样 fail-closed 拒绝（无法判定新鲜即不能完成）。failed /
    cancelled 收尾不需要证据。注意 record 与 finish 之间的任何 git 提交
    都会改变基线修订使证据失效，须重验。空 required 单元经谓词快路径
    零 git 放行（生产单元受「无验证命令的单元不可派发」的 validate_state
    约束不会出现该形状）。RB-1 的动机：prepare→commit→finish 此前可
    完全绕过 record_unit_verification，验证缺失会经 refresh_readiness
    传播为错误解锁下游 DAG（release blocker RB-1）。

    根解析（RB-2，release hardening WU-P3）：证据门的 git 求值根按任务
    绑定解析——state.resolve_repository_root(st, repo_root)（绑定
    repository.root 优先，legacy 回退账本根）；touched 清单与证据指纹按
    git 根计算，验证事件（events）恒从账本根 repo_root 注入——
    fresh_unit_verification 缺省会从 git 根读 journal，多仓场景（账本根
    ≠ git 根）时会读错地方。账本（state.json / events.jsonl / 租约）
    不随绑定迁移，纪律红线：state/journal/lease I/O 恒在账本根，只有
    git 操作切任务仓库根。

多单元就绪流转（v2.0.1 收尾，dogfood 缺口补齐）：
    finish_unit 使某单元到达 completed 后调用 refresh_readiness——
    依赖全部 completed 的 pending / waiting_dependency 单元提升为
    ready（§62 表内合法边；就绪是可推导的状态提升，刻意不落
    journal），随后 prepare_dispatch 即可直接准入下一单元——主会话
    不再手工转态。

Quota Subscription 三 API（v2.2 C5a，wu-22-C5a，主计划 §14 adapted）：
    「Task 只订阅 quota，不再拥有 quota clock」的执行面落点——订阅
    事实记在任务 state 顶层 quota_subscription 块（形状真相源
    state.DEFAULT_QUOTA_SUBSCRIPTION / 规则 8.9 validator；epoch 身份
    用 §10.1 epoch_id 字符串等值比较，不引入 §14 草图的 int 序数）：
      - register_quota_subscription：幂等注册（同参重注册零写零事件，
        冻结口径见其 docstring），continuation_mode 缺省镜像
        execution_policy.continuity.auto_resume，任务 journal 记
        quota_subscription_registered；
      - evaluate_subscription_eligibility：纯资格判定（零转态零写零
        事件；不做 authorization / budget / reconcile，归 C7）；
      - mark_activation_epoch：激活记账（QC-07 每 epoch 恰一次——同
        epoch_id 重标零写零事件；控制面 quota_epoch_advanced 事件经
        journal.append_control_plane_event 落
        .glm-conductor/quota/events.jsonl）。

分层关系：
    runtime.dispatcher —— 纯决策器：plan_dispatch 零 I/O，只产出「谁可
        派发 / 谁挂起及理由」的决策 dict；本层在 prepare 中消费它；
    runtime.lease —— 落盘 owner map：acquire/release 原语，获取时点
        （prepare）与释放时点（finish/abort）由本层锚定；
    runtime.dispatch_wave —— 落盘派发许可（v2.1 M2）：create/invalidate
        原语，签发时点（prepare）与作废时点（abort）由本层锚定；消费
        留给 wu-21-03 的 hook 侧（未消费 permit 随 TTL 自然过期兜底）；
    runtime.state / runtime.journal —— 状态与事件持久化；save_state 的
        任务级转换门照常生效（本层只动 work_units/dispatch 与合法的
        executing 直达边，不触碰终态任务级状态）；
    runtime.reconcile —— 恢复对账：commit 之后的中断裁决归它（本层只
        负责把崩溃窗口收敛到它已认识的状态）。

依赖：
    runtime.state（load/save）、runtime.dispatcher（纯决策）、
    runtime.lease（租约原语）、runtime.dispatch_wave（permit 原语）、
    runtime.execution_policy（default_execution_policy——permit mode
    策略兜底；effective_worker_budget——§12 quota 四态预算折算）、
    runtime.work_unit（§62 表内转换）、
    runtime.ownership（路径归一）、runtime.journal（事件追加）。
    仅 Python 3 标准库，`python3 -S` 可运行。

来源：
    docs/GLM-Conductor-v2.0.0-全面审查与v2.0.1加固建议.md §4.1 / P1-3
    + docs/glm-conductor-v2-upgrade-guide-final.md §62（状态转换表）/
    §64-§67（准入）/ §70（父验证）/ §78（租约时点）/ §69（恢复对账）
    + docs/GLM-Conductor-v2.0.1-Release-Hardening-Patch-Agent-Implementation-Plan.md
    （RB-1 / WU-P2：finish_unit 完成证据门，缺证据零副作用拒绝；
    RB-2 / WU-P3：证据门双根分离，git 根按任务绑定解析、events 恒从
    账本根注入）。
"""

import datetime

from runtime import dependency
from runtime import dispatch_wave
from runtime import dispatcher
from runtime import journal
from runtime import lease
from runtime import ownership
from runtime import reconcile
from runtime import resume_manifest
from runtime import state
from runtime import work_unit
from runtime.execution_policy import consumed_quota_windows
from runtime.execution_policy import default_execution_policy
from runtime.execution_policy import default_quota_control
from runtime.execution_policy import effective_worker_budget
from runtime.execution_policy import HARD_WORKER_LIMIT
from runtime.lease import LEASE_DEFAULT_TTL_SECONDS


def _write_manifest_safe(repo_root, task_id):
    """Resume Manifest 安全挂点（v2.1 M3 wu-21-07，§10.3 失败语义）。

    在主事务（save_state + journal 事件）完成之后调用：写 manifest
    失败不得覆盖 state truth、不得让事务半提交——任何异常只落一条
    manifest_write_failed journal 警告事件（错误文本截断 200 字符），
    绝不向上传播。延迟 import 的 resume_manifest 在模块顶部已静态
    导入（无循环：resume_manifest 不 import 本模块）。
    """
    try:
        resume_manifest.write_resume_manifest(repo_root, task_id)
    except Exception as exc:  # 派生物写入失败只降级不阻断（§10.3）
        try:
            journal.append_event(repo_root, task_id, {
                "event": "manifest_write_failed",
                "error": str(exc)[:200]})
        except Exception:
            pass  # journal 也写不进（如盘满）：静默，主事务已落

# finish_unit 的合法结局词汇（work unit 终态全集，§62）
FINISH_OUTCOMES = ("completed", "failed", "cancelled")

# commit_dispatch 中视为「非执行态」、顺手直达 executing 的任务级状态
# （TASK_TRANSITIONS 内的合法边；executing 及之后的执行态不重复翻转）
PRE_DISPATCH_TASK_STATUSES = ("created", "preflight", "routed", "decomposed")

# wave 收口的单元状态闭集（v2.1 M4 wu-21-08 设计决策 3）：verifying 计入
# ——已停止写文件、等待验证裁决的单元不再阻挡 wave 关闭
WAVE_CLOSED_UNIT_STATUSES = ("completed", "failed", "cancelled", "verifying")


class TaskManagerError(Exception):
    """task_manager 事务边界违背（单元缺失/状态不符/租约丢失/决策未批准
    /RB-1 完成证据缺失或不可判定）。"""


# —— 内部助手（容错读取，均不改入参语义） ——

def _utc_now_iso() -> str:
    """当前 UTC 时刻的 ISO-8601 字符串（毫秒精度 Z 形态，与 permit /
    租约层时间字段同格式）——wave 记录 created_at / closed_at 落盘口径。"""
    moment = datetime.datetime.now(datetime.timezone.utc)
    return (moment.strftime("%Y-%m-%dT%H:%M:%S")
            + ".%03dZ" % (moment.microsecond // 1000))


# —— 内部助手（容错读取，均不改入参语义） ——

def _require_state(repo_root, task_id, api) -> dict:
    """load_state 并要求任务存在：缺失 → TaskManagerError；JSON 损坏的
    ValueError 自然上抛（不静默、不覆盖损坏文件）。"""
    st = state.load_state(repo_root, task_id)
    if st is None:
        raise TaskManagerError(
            "%s：任务 %s 不存在（无 state.json），无法执行派发事务"
            % (api, task_id))
    return st


def _require_unit(st, task_id, uid, api) -> dict:
    """按 id 查找单元：找不到 → TaskManagerError（消息含 uid 与任务 id）。"""
    units = st.get("work_units")
    if isinstance(units, list):
        for unit in units:
            if isinstance(unit, dict) and unit.get("id") == uid:
                return unit
    raise TaskManagerError(
        "%s：任务 %s 的 work_units 中找不到单元 %s" % (api, task_id, uid))


def _normalized_ownership(unit) -> "list[str]":
    """单元 ownership 归一为路径列表（commit 租约校验用）：非 list/tuple
    容错为 []（与 prepare 的 acquire 容错同口径）；非字符串路径项交
    ownership.normalize_path 抛结构性错误（不静默转换）。"""
    owned = unit.get("ownership")
    if not isinstance(owned, (list, tuple)):
        return []
    return [ownership.normalize_path(path) for path in owned]


def _active_ids(st) -> "list[str]":
    """容错读取 dispatch.active（缺失/形状异常按空处理）。"""
    dispatch_block = st.get("dispatch")
    if not isinstance(dispatch_block, dict):
        return []
    active = dispatch_block.get("active")
    return list(active) if isinstance(active, list) else []


def _set_active(st, active) -> None:
    """就地写回 dispatch.active（dispatch 块缺失时按需重建）。"""
    dispatch_block = st.get("dispatch")
    if not isinstance(dispatch_block, dict):
        dispatch_block = {}
        st["dispatch"] = dispatch_block
    dispatch_block["active"] = list(active)


def _remove_active(st, uid) -> bool:
    """从 dispatch.active 移除 uid（在则删）；发生移除时返回 True。
    只改内存 dict，落盘归调用方（save 时点由各 API 的顺序表固定）。"""
    active = _active_ids(st)
    if uid not in active:
        return False
    active.remove(uid)
    _set_active(st, active)
    return True


def _default_dispatch_mode(st) -> str:
    """派发 permit 的 mode 默认值：state execution_policy 的
    worker_execution.default_mode（v2.1 M2 接线）；legacy 缺块 / 块形
    状坏 / 叶子值非法时按 execution_policy.default_execution_policy()
    的保守默认（"background"）兜底——mode 永远是 PERMIT_MODES 内的
    合法值，create_permit 不会因策略形状炸。"""
    policy = st.get("execution_policy")
    worker_execution = (
        policy.get("worker_execution")
        if isinstance(policy, dict) else None)
    mode = (worker_execution.get("default_mode")
            if isinstance(worker_execution, dict) else None)
    if mode in dispatch_wave.PERMIT_MODES:
        return mode
    return default_execution_policy()["worker_execution"]["default_mode"]


def _is_worker_cap(value) -> bool:
    """value 是否可充当并发上限：int（bool 拒绝——int 子类不充当槽位
    数）且 1 ≤ n ≤ execution_policy 冻结 hard_limit（4）。"""
    return (not isinstance(value, bool) and isinstance(value, int)
            and 1 <= value <= HARD_WORKER_LIMIT)


def _worker_cap_base(st, max_workers) -> int:
    """wu-21-09 cap_base 优先级解析（原 _effective_worker_cap 前半，
    v2.2 M4 起 execution phase 决策器共用同一预算口径）：
      1. 显式 max_workers 参数（非 None 即采纳）；
      2. state execution_policy.parallelism.max_workers（合法
         1..hard_limit 的 int 时）；
      3. state dispatch.max_workers（legacy 任务的账面口径，同样
         1..hard_limit 校验）；
      4. dispatcher.DEFAULT_MAX_WORKERS（=2，v2.1 起与
         new_task_state 默认、execution_policy 默认块三处口径一致）。
    纯函数：只读入参、零 I/O。
    """
    policy = st.get("execution_policy")
    cap_base = max_workers
    if cap_base is None:
        parallelism = (policy.get("parallelism")
                       if isinstance(policy, dict) else None)
        cap_base = (parallelism.get("max_workers")
                    if isinstance(parallelism, dict) else None)
        if not _is_worker_cap(cap_base):
            dispatch_block = st.get("dispatch")
            cap_base = (dispatch_block.get("max_workers")
                        if isinstance(dispatch_block, dict) else None)
            if not _is_worker_cap(cap_base):
                cap_base = dispatcher.DEFAULT_MAX_WORKERS
    return cap_base


def _effective_worker_cap(st, quota_status, max_workers,
                          phase_budget=None) -> int:
    """wu-21-09 预算接线（计划 §11.5/§12）：并发上限 × quota 四态折算。

    有效预算 eff = min(cap_base（见 _worker_cap_base）,
    execution_policy.effective_worker_budget(policy, quota_status))：
    §12 表——AVAILABLE→策略 max_workers（policy 缺块 / 坏形状按
    default_execution_policy 的 2 兜底，effective_worker_budget 既有
    行为）/ PRESSURE→1 / UNKNOWN→1 / EXHAUSTED→0（quota_status 非法
    同样保守折 0，词汇校验仍由 plan_dispatch 的 ValueError 兜底）。

    v2.2 M4（wu-22-04，D7/§18）：phase_budget 为 execution phase 决策
    （control.evaluate_task_quota_phase）的 dispatch_budget，参与同一
    min 折算——NORMAL 相 budget=cap_base 折算无变化、PRESSURE 相折 1，
    与既有 provider 维度 PRESSURE→1 折算路径合流为单一预算源，不双写；
    None（闸未激活 / fail-open）零影响。DRAINING/BLOCKED 的 budget=0
    不会走到本折算（调用方在预算折算前已硬拒）。

    返回 int：eff ≥ 1 时即调用方应传给 plan_dispatch 的
    max_workers；eff == 0（EXHAUSTED / 非法词汇折 0）不能直接传入
    plan_dispatch（其校验 1..hard_limit）——调用方传 max(eff, 1)，
    plan 的 quota 闸自然把候选全转 waiting_quota（预算 0 的可观察面
    就是「零派发 + waiting_quota」，错误口径与旧实现逐字一致）。
    纯函数：只读入参、零 I/O。
    """
    policy = st.get("execution_policy")
    cap_base = _worker_cap_base(st, max_workers)
    eff = min(cap_base, effective_worker_budget(policy, quota_status))
    if phase_budget is not None:
        eff = min(eff, phase_budget)
    return eff if eff >= 1 else 0


def _resolve_quota(api, repo_root, task_id, quota_status) -> str:
    """wu-21-10 运行时额度解析（v2.1 §13）：显式字符串直通，None → resolver。

    - quota_status 为显式字符串（调用方声明）→ 原样返回：零解析、
      零网络、零事件（词汇合法性由 effective_worker_budget /
      plan_dispatch 既有校验兜底）；
    - quota_status 为 None（缺省——v2.1 起「绝不默认 AVAILABLE」）→
      runtime.quota.resolver.resolve_quota_status(repo_root)（函数内
      import + 属性访问，测试 monkeypatch 友好；resolver 层级：
      fresh cache → provider fetch → stale cache → UNKNOWN，绝不重试
      网络、异常不外泄、凭证零落盘），journal 落一条 quota_resolved
      {"status", "source", "evaluated_at"}——额度是 best-effort 观测
      面，解析事实入账供审计与诊断——随后返回其中的 status 字符串，
      进入既有 §12 预算折算与 plan_dispatch 决策链（UNKNOWN 按预算 1
      不挂起，wu-21-09 语义）。

    调用方约束：必须在任何写副作用（租约 / wave / permit / 状态转换）
    之前调用——参数校验全部通过后、预算折算前是唯一合法时点，被拒的
    prepare 不留下 quota_resolved 噪声行以外的半状态。
    """
    if quota_status is not None:
        return quota_status
    from runtime.quota import resolver  # 函数内 import：monkeypatch 友好
    resolved = resolver.resolve_quota_status(repo_root)
    status = resolved["status"]
    journal.append_event(repo_root, task_id, {
        "event": "quota_resolved", "status": status,
        "source": resolved["source"],
        "evaluated_at": resolved["evaluated_at"]})
    return status


def _execution_phase_decision(repo_root, st, max_workers):
    """v2.2 M4（wu-22-04）execution phase 决策（D6/D7/D13）——零网络。

    只读本地额度缓存 <repo_root>/.glm-conductor/quota-cache.json
    （runtime.quota.resolver.CACHE_FILE_NAME；复用 resolver._load_cache
    / _cache_path 容错原语——文件缺失 / OSError / JSON 损坏 / 非 dict /
    status 词汇陈旧（不在 QUOTA_STATUSES）/ fetched_at 不可解析一律
    视为无缓存），绝不触发 provider 抓取、绝不重试网络：

      - 无有效缓存 → 返回 None（fail-open：闸不激活，调用方预算折算
        与 state 落盘零改动——缓存缺失绝不阻塞或降级既有派发行为，
        D7 第 4 条 fail-open 语义先于闸门）；
      - 有效缓存 → 以缓存 status 为 provider_status、snapshot.windows
        为窗口清单，组装 runtime.quota.control.evaluate_task_quota_phase
        输入：quota_control 取 execution_policy.default_quota_control
        （D5 阈值单一真相源，legacy 缺块按 35/20 默认）、max_workers
        取 _worker_cap_base（与预算折算同一 cap_base 口径）、
        wake_bridge_status 容错读 continuation.wake_bridge.status
        （非法词汇按 "none"）、task_active=True（prepare 时点任务恒在
        执行态）；返回其冻结 8 键决策 dict（§17.1）；
      - 决策器任何异常（防御分支：上游输入已全量容错，理论不可达）
        → None 同 fail-open——闸自身故障绝不阻塞派发。

    纯读：不改入参 st、零写副作用；D13 回填与 D6 硬拒归调用方按各自
    事务时点执行。
    """
    try:
        from runtime.quota import control, resolver  # 函数内 import：monkeypatch 友好
        cache = resolver._load_cache(resolver._cache_path(repo_root))
        if not isinstance(cache, dict):
            return None
        snapshot = cache.get("snapshot")
        windows = (snapshot.get("windows")
                   if isinstance(snapshot, dict) else None)
        policy = st.get("execution_policy")
        continuation = st.get("continuation")
        bridge = (continuation.get("wake_bridge")
                  if isinstance(continuation, dict) else None)
        wake_bridge_status = (bridge.get("status")
                              if isinstance(bridge, dict) else None)
        if wake_bridge_status not in state.WAKE_BRIDGE_STATUSES:
            wake_bridge_status = "none"
        return control.evaluate_task_quota_phase(
            provider_status=cache.get("status"), windows=windows,
            quota_control=default_quota_control(policy),
            max_workers=_worker_cap_base(st, max_workers),
            task_active=True, wake_bridge_status=wake_bridge_status)
    except Exception:
        return None  # 闸自身故障一律 fail-open，绝不阻塞派发


def _backfill_execution_phase(st, phase) -> None:
    """D13 回填：state.quota.execution_phase = phase（prepare 时点写入，
    供 manifest 与 SessionStart 展示）。

    quota 块缺失时新建、已存在时只增写 execution_phase 键（既有键——
    如 observer 落的 status / snapshot——原样保留）；validator 对 quota
    未知键宽松，execution_phase 直接写入合法。只改内存 dict，落盘归
    调用方的单次 save（拒绝路径为 _reject_by_execution_phase 的
    save-then-raise，放行路径为调用方既有落盘点）。
    """
    quota_block = st.get("quota")
    if not isinstance(quota_block, dict):
        quota_block = {}
        st["quota"] = quota_block
    quota_block["execution_phase"] = phase


def _reject_by_execution_phase(api, repo_root, st, decision) -> None:
    """D6 硬拒新实施派发 + D13 回填落盘（拒绝路径先落回填再 raise——
    prepare 时点写入是 D13 冻结语义，被拒的 prepare 也留下观测事实）。

    单次 save 纪律：本次 save_state 是该次被拒 prepare 调用的唯一落盘
    （单单元入口原本零落盘；wave 入口尚未走到 wave 记录落盘点）；调用
    方保证此刻无任何租约 / permit / wave / journal 副作用，被拒的
    prepare 零残留、可安全重试。

    错误口径（v2.2 M4 冻结）：消息以 "execution phase" 开头（供测试与
    M5 区分），随后注明决策器 reason——含 execution_phase、驱动窗口
    剩余与最早重置时刻——以及 §18.1 收尾白名单与 reset/wake 建议。
    """
    phase = decision["execution_phase"]
    _backfill_execution_phase(st, phase)
    state.save_state(repo_root, st)
    raise TaskManagerError(
        "execution phase %s：%s 拒绝新实施单元/波次（%s）。收尾白名单"
        "动作 join/verify/review/checkpoint/wake 不受本闸限制（running "
        "单元不强杀）；建议等待额度窗口重置或创建 wake bridge 后恢复"
        "派发" % (phase, api, decision["reason"]))


def _validate_permit_mode(api, st, mode, reason) -> "tuple":
    """permit mode/reason 全量校验（prepare_dispatch 与
    prepare_dispatch_wave 共享，wu-21-08 抽取；消息逐字保持原口径）。

    - 显式 mode 参数优先，缺省取 state execution_policy 的
      worker_execution.default_mode（缺块/坏形状按默认块兜底，见
      _default_dispatch_mode）；
    - mode 不在 PERMIT_MODES → ValueError；
    - mode="foreground" 必须显式给出 reason ∈
      dispatch_wave.FOREGROUND_REASONS（§6.6），缺给出
      TaskManagerError、值非法 ValueError；background 恒 reason null
      （显式传入的 reason 静默归 null——接线层宽松，原语层
      create_permit 才严格拒绝）。
    返回 (effective_mode, permit_reason)。调用方必须在任何写副作用
    （租约 / wave / permit / journal）之前调用——非法参数不得留下
    半张租约或半个 wave。
    """
    effective_mode = mode if mode is not None else _default_dispatch_mode(st)
    if effective_mode not in dispatch_wave.PERMIT_MODES:
        raise ValueError(
            "%s：mode %r 不在合法取值内（%s）"
            % (api, effective_mode, ", ".join(dispatch_wave.PERMIT_MODES)))
    permit_reason = reason
    if effective_mode == "foreground":
        if permit_reason is None:
            raise TaskManagerError(
                "%s：mode=\"foreground\" 必须显式给出 reason（%s）——"
                "foreground 派发须由主会话声明原因（§6.6）"
                % (api, ", ".join(dispatch_wave.FOREGROUND_REASONS)))
        if permit_reason not in dispatch_wave.FOREGROUND_REASONS:
            raise ValueError(
                "%s：reason %r 不在合法取值内（%s）"
                % (api, permit_reason,
                   ", ".join(dispatch_wave.FOREGROUND_REASONS)))
    else:
        permit_reason = None  # background permit 的 reason 恒 null
    return effective_mode, permit_reason


# —— prepare 事务补偿（RB-21-03）：失败时零残留的回退面 ——

def _compensate_failed_prepare(repo_root, task_id, *, api, stage, exc,
                               permits=(), owners=(), wave_id=None) -> None:
    """prepare 事务补偿：permit 创建 / state 保存失败后，把本轮已产生的
    副作用全部回退（all-or-safe-degrade 契约的「失败时 safe」半边）——
    失败的 prepare 绝不残留可放行的 permit、在位租约或半完成 wave。

    补偿顺序固定：
      1. 先作废全部已创建 permit（invalidate 按文件 rename、只认
         permit_id，与 wave 是否落盘无关；返回 False = 未能作废，记账
         为补偿失败）；
      2. 再释放本轮全部租约（单点失败不中止——继续释放剩余并全部记账）；
      3. 防御性 wave 记录回滚：save_state 是 tmp+os.replace 原子写，
         失败即未落盘，正常无需处理；但若盘上已出现该 wave_id 的记录
         （异常窗口的半完成残留）则移除后重存；
      4. journal 落一条 transaction_aborted {api, reason, wave_id?,
         compensation_error?}（开放词汇记账，仅在补偿路径落）——补偿
         自身任何失败都记入 compensation_error 字段，fail-closed 绝不
         静默吞掉；记账自身再失败只能放弃（调用方保证原始异常继续
         上抛，补偿不掩盖根因）。
    """
    errors = []
    for permit in permits:
        permit_id = (permit.get("permit_id")
                     if isinstance(permit, dict) else None)
        if not isinstance(permit_id, str) or not permit_id:
            continue
        if not dispatch_wave.invalidate_permit(repo_root, task_id,
                                               permit_id):
            errors.append("invalidate_permit(%s) 未生效" % permit_id)
    for owner in owners:
        try:
            lease.release_lease(repo_root, task_id, owner)
        except Exception as release_exc:
            errors.append("release_lease(%s): %s" % (owner, release_exc))
    if wave_id is not None:
        try:
            disk = state.load_state(repo_root, task_id)
        except Exception as load_exc:
            errors.append("load_state: %s" % load_exc)
        else:
            block = disk.get("dispatch")
            waves = block.get("waves") if isinstance(block, dict) else None
            if isinstance(waves, list):
                kept = [w for w in waves
                        if not (isinstance(w, dict)
                                and w.get("wave_id") == wave_id)]
                if len(kept) != len(waves):
                    block["waves"] = kept
                    try:
                        state.save_state(repo_root, disk)
                    except Exception as save_exc:
                        errors.append("save_state(wave 记录回滚): %s"
                                      % save_exc)
    event = {
        "event": "transaction_aborted", "api": api,
        "reason": "%s_failed: %s" % (stage, exc)}
    if wave_id is not None:
        event["wave_id"] = wave_id
    if errors:
        event["compensation_error"] = errors
    try:
        journal.append_event(repo_root, task_id, event)
    except Exception:
        pass  # 记账自身失败不掩盖原始异常（调用方 re-raise）


# —— prepare：plan 决策 + 租约 + permit + 事件（无 state 副作用） ——

def prepare_dispatch(repo_root, task_id, uid, *, quota_status=None,
                     max_workers=None, mode=None, reason=None) -> dict:
    """派发准备：准入决策 → 租约 → permit 签发 → 事件；不改单元状态、
    不 save state——prepare 对 state.json 零副作用（可安全重试）。

    流程：
      1. load_state（任务缺失 TaskManagerError；损坏 ValueError 上抛），
         找不到 uid → TaskManagerError；
      2. 单元 status 必须为 "ready"，否则 TaskManagerError（消息含当前
         状态）；
      3. permit mode/reason 先于任何副作用全量校验（与「落选零副作用」
         同口径——非法 mode/reason 不得留下半张租约）：显式 mode 参数
         优先，缺省取 state execution_policy 的 worker_execution.
         default_mode（缺块/坏形状按默认块兜底，见
         _default_dispatch_mode）；mode="foreground" 必须显式给出
         reason ∈ dispatch_wave.FOREGROUND_REASONS（§6.6），缺给出
         TaskManagerError、值非法 ValueError；background 恒 reason
         null（显式传了也拒绝）；
      4. 运行时额度解析（wu-21-10，见 _resolve_quota）：quota_status
         缺省 None——**绝不默认 AVAILABLE**；None → resolver 四级层级
         （fresh cache → provider fetch → stale cache → UNKNOWN）+
         quota_resolved 事件；显式字符串原样直通（零解析零事件）；
      5. max_workers 预算接线（wu-21-09，§11.5/§12，见
         _effective_worker_cap）：cap_base 优先级为「显式参数 →
         execution_policy.parallelism.max_workers（合法 1..4 int）→
         dispatch.max_workers（legacy）→ 2」，有效预算
         eff = min(cap_base, execution_policy.effective_worker_budget(
         policy, quota_status))——AVAILABLE→策略 max_workers /
         PRESSURE→1 / UNKNOWN→1 / EXHAUSTED→0（quota_status 此时已是
         解析后的实际值）；eff 作为 max_workers 传给 plan_dispatch
         （eff=0 时传 1，quota 闸自然全转 waiting_quota——dispatcher
         不再整批挂起 UNKNOWN/PRESSURE）；
      6. 读出已落盘租约（lease.lease_state，损坏 ValueError 上抛）交给
         plan_dispatch 的租约闸——自有租约不挡自己（§78 同 owner 放行），
         因此 prepare 后崩溃重试安全；
      7. plan = dispatcher.plan_dispatch(...)（纯决策器，零 I/O）；
      8. uid 不在 plan["dispatch"] → TaskManagerError，消息含落选原因
         （waiting_quota 组 → quota EXHAUSTED；deferred 组 → 对应
         reason；未进候选 → 依赖未满足等）；
      9. lease.acquire_lease（全有或全无，同 owner 幂等；ownership 非
         list 容错为 []；按 LEASE_DEFAULT_TTL_SECONDS 保守 TTL 落盘
         expires_at，长期实施由 runtime.lease.renew_lease 心跳续约）；
      10. dispatch_wave.create_permit 签发派发许可（§6.3 每 permit 一
          文件 + tmp/os.replace 原子写，ttl 取 dispatch_wave.
          DEFAULT_TTL_SECONDS；wave_id 缺省 None，M4 wave 事务接线）；
      11. journal dispatch_prepared（unit + leased=持有中的归一路径 +
          ttl_seconds + effective_max_workers=步 5 的有效预算）与
          dispatch_permit_created（unit + permit_id + mode），先后各
          一条（步 4 走了 resolver 时 quota_resolved 在最前）；
      12. 返回 plan 决策快照，新增 "permit" 键（permit dict；主会话用
          dispatch_wave.marker_for(permit_id) 构造 marker 派发 Agent，
          消费归 wu-21-03 的 hook 侧）。

    失败零副作用锚定：决策未批准（步骤 8）发生在获取租约（步骤 9）与
    journal（步骤 11）之前——落选的 prepare 不写租约、不写 permit、
    不写决策事件（quota_resolved 属解析事实，见 _resolve_quota）。
    permit 落盘失败（OSError）发生在租约之后：自动补偿释放该租约
    （journal transaction_aborted 记账，见 _compensate_failed_prepare）
    后原样上抛——失败零残留，无需人工 abort_dispatch 回退（RB-21-03；
    硬崩溃窗口语义仍见模块 docstring permit 接线一节）。

    execution phase 闸（v2.2 M4 wu-22-04，D6/D7/D13）：额度解析（步 4）
    之后、预算折算（步 5）之前，零网络读取本地额度缓存做 execution
    phase 决策——无有效缓存 fail-open 跳过（预算折算与 state 落盘零
    改动，fail-open 语义先于闸门）；有效缓存时先按 D13 回填
    state.quota.execution_phase（本调用唯一一笔 save_state），再裁决：
    DRAINING/BLOCKED 在任何租约 / permit 副作用之前硬拒（TaskManagerError
    消息以 "execution phase" 开头，拒绝路径先落回填再 raise）；放行相
    （NORMAL/PRESSURE）的 dispatch_budget 并入步 5 的单一 min 折算
    （PRESSURE→1 合流既有折算路径，NORMAL 照旧）。plan_dispatch 底层
    零改动；running 单元不强杀；finish/verify/review/checkpoint/wake
    路径零接触（D6 白名单）。
    """
    api = "prepare_dispatch"
    st = _require_state(repo_root, task_id, api)
    unit = _require_unit(st, task_id, uid, api)
    current = unit.get("status")
    if current != "ready":
        raise TaskManagerError(
            "%s：单元 %s 当前状态为 %r 而非 ready，不能准备派发"
            % (api, uid, current))
    # permit 参数先于任何 I/O 副作用全量校验（§6.3/§6.6 冻结不变量；
    # wu-21-08 起内联逻辑抽为 _validate_permit_mode 与 wave 批量事务共享）
    effective_mode, permit_reason = _validate_permit_mode(api, st, mode,
                                                          reason)
    # wu-21-10 运行时额度解析：显式字符串直通；None → resolver 层级
    # 决策 + quota_resolved 事件（此后 quota_status 恒为四态实际值）
    quota_status = _resolve_quota(api, repo_root, task_id, quota_status)
    # —— v2.2 M4（wu-22-04）execution phase 闸（D6/D7/D13）：零网络读
    # 本地额度缓存 → control 决策；无有效缓存 fail-open 跳过（零改动）。
    # 闸激活时先 D13 回填（本调用唯一一笔 save_state），DRAINING/BLOCKED
    # 在任何租约 / permit 副作用之前硬拒；放行相的 dispatch_budget 并入
    # 下方 _effective_worker_cap 的单一 min 折算
    phase_decision = _execution_phase_decision(repo_root, st, max_workers)
    if phase_decision is not None:
        _backfill_execution_phase(st, phase_decision["execution_phase"])
        if not phase_decision["allow_new_wave"]:
            _reject_by_execution_phase(api, repo_root, st, phase_decision)
        state.save_state(repo_root, st)  # D13 回填落盘（放行路径）
    dispatch_block = st.get("dispatch")
    if not isinstance(dispatch_block, dict):
        dispatch_block = {}
    # wu-21-09 预算接线：cap_base（显式 > policy > legacy dispatch > 2）
    # × quota 四态折算（§12）→ eff；eff=0（EXHAUSTED）时传 1 进 plan，
    # 由 quota 闸自然全转 waiting_quota（plan 校验 1..4 不能收 0）；
    # v2.2 M4 起 execution phase 的 dispatch_budget 参与同一 min 折算
    # （PRESSURE→1，NORMAL 相 budget=cap_base 无变化）
    eff = _effective_worker_cap(
        st, quota_status, max_workers,
        phase_budget=(phase_decision["dispatch_budget"]
                      if phase_decision is not None else None))
    active = dispatch_block.get("active")
    if not isinstance(active, list):
        active = []
    # 租约事实交给决策器的租约闸（record dict 形状由 plan_dispatch 容错）
    leases = lease.lease_state(repo_root, task_id)
    plan = dispatcher.plan_dispatch(
        st.get("work_units"), max_workers=max(eff, 1), active=active,
        quota_status=quota_status, leases=leases)
    if uid not in plan["dispatch"]:
        if uid in plan["waiting_quota"]:
            why = "quota EXHAUSTED（配额耗尽，决策建议转 waiting_quota）"
        else:
            entry = None
            for item in plan["deferred"]:
                if isinstance(item, dict) and item.get("id") == uid:
                    entry = item
                    break
            if entry is not None:
                why = "挂起（deferred）：%s" % entry.get("reason")
            else:
                why = ("未进入派发候选（依赖未全部 completed 或状态未提升"
                       "为 ready）")
        raise TaskManagerError(
            "%s：plan_dispatch 未批准单元 %s（当前状态 %r）——%s"
            % (api, uid, current, why))
    owned = unit.get("ownership")
    if not isinstance(owned, (list, tuple)):
        owned = []
    lease.acquire_lease(repo_root, task_id, uid, owned,
                        ttl_seconds=LEASE_DEFAULT_TTL_SECONDS)
    # 派发 permit（§6.3）：租约在位后签发——「无 permit 即 deny」门的
    # 数据基础；wave_id 缺省 None（M4 wave 事务接线）
    try:
        permit = dispatch_wave.create_permit(
            repo_root, task_id, uid, mode=effective_mode,
            reason=permit_reason)
    except Exception as exc:
        # RB-21-03：permit 落盘失败 → 释放该租约再上抛（消除「人工
        # abort_dispatch 回退」缺口）——失败的 prepare 零残留
        _compensate_failed_prepare(
            repo_root, task_id, api=api, stage="create_permit", exc=exc,
            owners=[uid])
        raise
    # leased 记持有事实（归一路径、排序、同 owner 幂等重入不虚报）
    held = lease.held_by(repo_root, task_id, uid)
    journal.append_event(repo_root, task_id, {
        "event": "dispatch_prepared", "unit": uid, "leased": held,
        "ttl_seconds": LEASE_DEFAULT_TTL_SECONDS,
        "effective_max_workers": eff})
    journal.append_event(repo_root, task_id, {
        "event": "dispatch_permit_created", "unit": uid,
        "permit_id": permit["permit_id"], "mode": effective_mode})
    plan["permit"] = permit
    return plan


# —— wave：批量 plan 决策 + 全量租约 + wave 记录 + 批量 permit ——

def prepare_dispatch_wave(repo_root, task_id, *, quota_status=None,
                          max_workers=None, mode=None, reason=None) -> dict:
    """批量派发准备（v2.1 M4 wu-21-08）：一次调用完成「决策 → 全量租约
    → 批量 permit → wave 记录落盘 → journal」，事务性 all-or-safe-degrade
    ——wave 记录落盘时其全部成员租约与 permit 已在位，绝不出现「wave
    记录 2 单元但只有 1 张租约」；permit/state 任一写失败则整笔补偿
    （RB-21-03）：作废已建 permit + 释放本轮租约，wave 不落盘、无
    prepared 事件，异常原样上抛——失败时零残留。

    流程：
      1. load_state（任务缺失 TaskManagerError；损坏 ValueError 上抛）；
      2. permit mode/reason 先于任何写副作用全量校验（与 prepare_dispatch
         共享 _validate_permit_mode，消息口径逐字一致）；
      3. 运行时额度解析（wu-21-10，与 prepare_dispatch 同口径，见
         _resolve_quota）：quota_status 缺省 None——**绝不默认
         AVAILABLE**；None → resolver 四级层级 + quota_resolved 事件，
         显式字符串原样直通（零解析零事件）；
      4. max_workers 预算接线（wu-21-09，与 prepare_dispatch 共享
         _effective_worker_cap，见其 docstring）：cap_base 优先级「显式
         参数 → execution_policy.parallelism.max_workers（合法 1..4
         int）→ dispatch.max_workers（legacy）→ 2」，eff =
         min(cap_base, effective_worker_budget(policy, quota_status))
         ——AVAILABLE→策略 max_workers / PRESSURE→1 / UNKNOWN→1 /
         EXHAUSTED→0（quota_status 此时已是解析后的实际值）；eff 作为
         max_workers 传给 plan_dispatch（eff=0 时传 1，quota 闸自然全
         转 waiting_quota——dispatcher 不再整批挂起 UNKNOWN/PRESSURE）；
      5. 决策-租约循环（安全降级）：
           a. 候选 = 未被剔除的 work_units，plan_dispatch 全量决策（带
              落盘租约闸，纯决策器）；
           b. 批准集按 state.work_units 原序重排（plan 内部是 topo 序）；
           c. 批准集空 → TaskManagerError（消息口径照抄
              prepare_dispatch：waiting_quota → quota EXHAUSTED；
              deferred → 对应 reason；未进候选 → 依赖未满足），零租约
              残留；
           d. 批准集逐单元 acquire_lease（owner=uid，§78 全有或全无）；
              任一 LeaseConflictError → 释放本轮已获取的全部租约（零
              残留）、冲突单元加入 excluded 集合、回到 a 剔除后重试
              ——excluded 单调增长保证有界终止；
      6. 全部租约到位 → worker_budget = min(eff, len(批准集))（wu-21-09
         起 max_workers 已是 quota 调节后的 eff，worker_budget 自然是
         quota 调节后的值），wave_id = dispatch_wave.new_wave_id()，
         wave 记录（冻结键：wave_id/units/worker_budget/quota_status/
         created_at/status/closed_at，status="active"；quota_status 记
         解析后的实际值，wu-21-10）追加进内存 dispatch.waves（单元
         wave 合法：只批 1 个也成 wave）；
      7. 逐成员 create_permit（携带 wave_id 与 mode/reason）——先建
         全部 permit（临时列表持有），尚不落 wave（RB-21-03 事务顺序：
         permit 任一失败时 wave 根本不落盘，无需回滚盘上记录）；
      8. save_state 一次落盘（原子写 tmp+os.replace，失败即未落盘）；
      9. journal 单条 dispatch_wave_prepared {wave_id, units,
         worker_budget, permits: [permit_id...]}——不逐单元重复
         dispatch_prepared（步 3 走了 resolver 时 quota_resolved 在最前）；
      10. 返回 {"wave_id", "units", "permits", "worker_budget",
          "deferred", "waiting_quota"}（permits 为 permit dict 列表；
          deferred/waiting_quota 为最终决策的落选面透传）。

    事务补偿（RB-21-03）：步 7 任一 create_permit 抛异常，或步 8
    save_state 抛 OSError/ValueError → _compensate_failed_prepare
    整笔回退（作废全部已建 permit → 释放本轮全部租约 → 防御性移除
    盘上可能的半完成 wave 记录）并落一条 transaction_aborted 警告
    事件（补偿自身再失败 → compensation_error 字段，fail-closed 不
    静默），原始异常继续上抛；dispatch_wave_prepared 只在全部就绪后
    落盘，失败路径绝无 prepared 事件。

    wave 关闭不归本 API：成员全部终态/verifying 时由 finish_unit 收口
    （见 _close_finished_waves）；hook 侧经
    dispatch_wave.validate_wave_membership 校验成员资格。

    execution phase 闸（v2.2 M4 wu-22-04，D6/D7/D13）：额度解析（步 3）
    之后、预算折算（步 4）之前，零网络读取本地额度缓存做 execution
    phase 决策——无有效缓存 fail-open 跳过（预算折算与 state 落盘零
    改动，fail-open 语义先于闸门）；有效缓存时按 D13 回填
    state.quota.execution_phase（随步 8 wave 记录的既有单次 save_state
    落盘，不新增写盘点），DRAINING/BLOCKED 在决策-租约循环（步 5）之前
    硬拒（TaskManagerError 消息以 "execution phase" 开头，拒绝路径先
    落回填再 raise——零租约残留）；放行相（NORMAL/PRESSURE）的
    dispatch_budget 并入步 4 的单一 min 折算（PRESSURE→worker_budget
    收缩为 1，合流既有折算路径，NORMAL 照旧）。plan_dispatch 底层零
    改动；running 单元不强杀；finish/verify/review/checkpoint/wake
    路径零接触（D6 白名单）。
    """
    api = "prepare_dispatch_wave"
    st = _require_state(repo_root, task_id, api)
    # mode/reason 校验先于任何写副作用（与「落选零副作用」同口径）
    effective_mode, permit_reason = _validate_permit_mode(api, st, mode,
                                                          reason)
    # wu-21-10 运行时额度解析：显式字符串直通；None → resolver 层级
    # 决策 + quota_resolved 事件（此后 quota_status 恒为四态实际值，
    # wave 记录的 quota_status 字段记的正是它）
    quota_status = _resolve_quota(api, repo_root, task_id, quota_status)
    # —— v2.2 M4（wu-22-04）execution phase 闸（D6/D7/D13）：零网络读
    # 本地额度缓存 → control 决策；无有效缓存 fail-open 跳过（零改动）。
    # 闸激活时 D13 回填随下方 wave 记录的既有单次 save_state 落盘（不
    # 新增写盘点）；DRAINING/BLOCKED 在决策-租约循环之前硬拒（零租约
    # 残留）；放行相的 dispatch_budget 并入下方单一 min 折算
    phase_decision = _execution_phase_decision(repo_root, st, max_workers)
    if phase_decision is not None:
        _backfill_execution_phase(st, phase_decision["execution_phase"])
        if not phase_decision["allow_new_wave"]:
            _reject_by_execution_phase(api, repo_root, st, phase_decision)
    dispatch_block = st.get("dispatch")
    if not isinstance(dispatch_block, dict):
        dispatch_block = {}
    # wu-21-09 预算接线（与 prepare_dispatch 同口径，见
    # _effective_worker_cap）：eff=0（EXHAUSTED）时传 1 进 plan，
    # 由 quota 闸自然全转 waiting_quota；v2.2 M4 起 execution phase 的
    # dispatch_budget 参与同一 min 折算——PRESSURE 相 worker_budget
    # 自然收缩为 1（合流既有折算路径，不双写），NORMAL 相照旧
    eff = _effective_worker_cap(
        st, quota_status, max_workers,
        phase_budget=(phase_decision["dispatch_budget"]
                      if phase_decision is not None else None))
    active = dispatch_block.get("active")
    if not isinstance(active, list):
        active = []
    work_units = st.get("work_units")
    if not isinstance(work_units, list):
        work_units = []
    # 决策-租约循环（安全降级）：冲突单元入 excluded，剔除后重 plan；
    # excluded 每轮至少新增一个 uid，单调增长保证有界终止
    excluded = set()
    while True:
        candidates = [
            unit for unit in work_units
            if isinstance(unit, dict) and isinstance(unit.get("id"), str)
            and unit.get("id") not in excluded]
        # 租约事实交给决策器的租约闸（§78 同 owner 幂等，自有租约不挡）
        leases = lease.lease_state(repo_root, task_id)
        plan = dispatcher.plan_dispatch(
            candidates, max_workers=max(eff, 1), active=active,
            quota_status=quota_status, leases=leases)
        by_id = {unit["id"]: unit for unit in candidates}
        approved_set = set(plan["dispatch"])
        # 批准集按 state.work_units 原序（plan 内部是 topo 序）
        approved = [unit["id"] for unit in candidates
                    if unit["id"] in approved_set]
        if not approved:
            if plan["waiting_quota"]:
                why = "quota EXHAUSTED（配额耗尽，决策建议转 waiting_quota）"
            elif plan["deferred"]:
                why = "挂起（deferred）：%s" % "；".join(
                    "%s（%s）" % (item.get("id"), item.get("reason"))
                    for item in plan["deferred"])
            else:
                why = ("未进入派发候选（依赖未全部 completed 或状态未提升"
                       "为 ready）")
            raise TaskManagerError(
                "%s：plan_dispatch 未批准任何单元（零租约残留）——%s"
                % (api, why))
        acquired = []
        conflicted = False
        for uid in approved:
            owned = by_id[uid].get("ownership")
            if not isinstance(owned, (list, tuple)):
                owned = []
            try:
                lease.acquire_lease(repo_root, task_id, uid, owned,
                                    ttl_seconds=LEASE_DEFAULT_TTL_SECONDS)
            except lease.LeaseConflictError:
                # 安全降级：释放本轮已获取的全部租约（零残留），冲突
                # 单元剔除后重 plan——绝不带残缺租约进 wave 记录
                for done in acquired:
                    lease.release_lease(repo_root, task_id, done)
                excluded.add(uid)
                conflicted = True
                break
            acquired.append(uid)
        if conflicted:
            continue
        break
    worker_budget = min(max(eff, 1), len(approved))
    wave_id = dispatch_wave.new_wave_id()
    wave = {
        "wave_id": wave_id,
        "units": list(approved),
        "worker_budget": worker_budget,
        "quota_status": quota_status,
        "created_at": _utc_now_iso(),
        "status": "active",
        "closed_at": None,
    }
    if not isinstance(st.get("dispatch"), dict):
        st["dispatch"] = {}
    waves = st["dispatch"].get("waves")
    if not isinstance(waves, list):
        waves = []
        st["dispatch"]["waves"] = waves
    waves.append(wave)
    # RB-21-03 事务顺序（方案 A）：先建全部 permit（临时列表持有）→
    # save_state 持久化 wave → journal。permit 任一失败时 wave 根本
    # 不落盘（无需回滚盘上记录），整笔补偿后异常原样上抛
    permits = []
    try:
        for uid in approved:
            permits.append(dispatch_wave.create_permit(
                repo_root, task_id, uid, wave_id=wave_id,
                mode=effective_mode, reason=permit_reason))
    except Exception as exc:
        _compensate_failed_prepare(
            repo_root, task_id, api=api, stage="create_permit", exc=exc,
            permits=permits, owners=list(approved), wave_id=wave_id)
        raise
    try:
        # 一次落盘：此处起 wave.units 与在位租约、permit 一一对应
        # （all-or-safe 界）；原子写失败即未落盘，同样整笔补偿
        state.save_state(repo_root, st)
    except Exception as exc:
        _compensate_failed_prepare(
            repo_root, task_id, api=api, stage="save_state", exc=exc,
            permits=permits, owners=list(approved), wave_id=wave_id)
        raise
    journal.append_event(repo_root, task_id, {
        "event": "dispatch_wave_prepared", "wave_id": wave_id,
        "units": list(approved), "worker_budget": worker_budget,
        "permits": [permit["permit_id"] for permit in permits]})
    return {
        "wave_id": wave_id,
        "units": list(approved),
        "permits": permits,
        "worker_budget": worker_budget,
        "deferred": plan["deferred"],
        "waiting_quota": plan["waiting_quota"],
    }


# —— commit：ready→running + active 记账 + 单次 save ———

def commit_dispatch(repo_root, task_id, uid) -> dict:
    """提交派发：租约在位校验 → ready→running → active 记账 → 单次 save
    → implementation_started 事件；返回提交后的 state dict。

    流程：
      1. load_state；找不到 uid → TaskManagerError；
      2. 单元 status 必须为 "ready"——重复 commit（已 running）/状态已
         漂移都会在此拒绝；
      3. 租约在位校验：ownership 每个归一路径都在 lease_state 中且
         owner == uid，任一缺失/异主 → TaskManagerError（提示先
         prepare_dispatch；租约已丢失时 abort_dispatch 后重新准备）；
      4. transition_work_unit(unit, "running")（§62 表内转换，非法
         ValueError 自然上抛）；
      5. dispatch.active 追加 uid（幂等防御：已在则不重复）；任务级
         status 为非执行态（created/preflight/routed/decomposed）时
         顺手转 "executing"（TASK_TRANSITIONS 合法边，简化调用方）；
      6. save_state 一次（任务级转换门照常生效）；
      7. journal implementation_started（unit + executor）。

    commit 之后单元进入 running：此后回退不归本模块——收尾用
    finish_unit，中断恢复按 §69 走 reconcile。
    """
    api = "commit_dispatch"
    st = _require_state(repo_root, task_id, api)
    unit = _require_unit(st, task_id, uid, api)
    current = unit.get("status")
    if current != "ready":
        raise TaskManagerError(
            "%s：单元 %s 当前状态为 %r 而非 ready（重复 commit 或状态已"
            "漂移都会在此拒绝——如需回退未提交的准备请用 abort_dispatch）"
            % (api, uid, current))
    owned = _normalized_ownership(unit)
    leases = lease.lease_state(repo_root, task_id)
    broken = []
    for path in owned:
        record = leases.get(path)
        holder = record.get("owner") if isinstance(record, dict) else None
        if holder != uid:
            broken.append("%s（持有者 %s）" % (path, holder))
    if broken:
        raise TaskManagerError(
            "%s：单元 %s 的租约不在位（%s）——先 prepare_dispatch 获取"
            "租约；若租约已丢失请 abort_dispatch 后重新准备"
            % (api, uid, "；".join(broken)))
    work_unit.transition_work_unit(unit, "running")
    active = _active_ids(st)
    if uid not in active:
        active.append(uid)
    _set_active(st, active)
    if st.get("status") in PRE_DISPATCH_TASK_STATUSES:
        st["status"] = "executing"
    state.save_state(repo_root, st)
    journal.append_event(repo_root, task_id, {
        "event": "implementation_started", "unit": uid,
        "executor": unit.get("executor")})
    _write_manifest_safe(repo_root, task_id)
    return st


# —— abort：prepare 之后、commit 之前的回退 ——

def abort_dispatch(repo_root, task_id, uid) -> dict:
    """回退未提交的派发准备：释放租约 + permit 失效 + dispatch_aborted
    事件；返回 state dict。

    流程：
      1. load_state；找不到 uid → TaskManagerError；
      2. 单元 status == "running" → TaskManagerError（已提交，不得静默
         回退——用 finish_unit 收尾或按 §69 走 reconcile）；
      3. lease.release_lease 全部释放该 owner 的租约；
      4. uid 残留在 dispatch.active（异常残留）→ 移除并 save_state；
         无残留则不落盘（abort 对 state.json 零写入是常态）；
      5. journal dispatch_aborted；
      6. 该单元全部未消费 permit invalidate（dispatch_wave.
         invalidate_permit，rename 为 .invalidated.json 保留审计轨迹），
         每张实际失效的 permit 落一条 dispatch_permit_invalidated
         （unit + permit_id；零失效不落事件——重复 abort / 无 permit
         的 abort 不产生噪声行）；
      7. 返回 state dict。

    不改单元状态：prepare 对 state 零副作用，abort 也不需要补偿单元
    状态——回退后单元仍是 ready，可重新 prepare（新 prepare 签发新
    permit，旧 permit 已 invalidated 不会复活）。
    """
    api = "abort_dispatch"
    st = _require_state(repo_root, task_id, api)
    unit = _require_unit(st, task_id, uid, api)
    if unit.get("status") == "running":
        raise TaskManagerError(
            "%s：单元 %s 已提交（状态 running）——不得静默回退，用 "
            "finish_unit 收尾或按 §69 走 reconcile 恢复对账" % (api, uid))
    lease.release_lease(repo_root, task_id, uid)
    if _remove_active(st, uid):
        state.save_state(repo_root, st)
    journal.append_event(repo_root, task_id, {
        "event": "dispatch_aborted", "unit": uid})
    # 未消费 permit 一并作废（§6.3）：先于返回值完成——回退后的任务
    # 目录里不得残留可被 hook 放行的活跃 permit
    for permit in dispatch_wave.list_permits(repo_root, task_id):
        if permit.get("unit_id") != uid:
            continue
        permit_id = permit.get("permit_id")
        if dispatch_wave.invalidate_permit(repo_root, task_id, permit_id):
            journal.append_event(repo_root, task_id, {
                "event": "dispatch_permit_invalidated", "unit": uid,
                "permit_id": permit_id})
    _write_manifest_safe(repo_root, task_id)
    return st


# —— finish：running/verifying → 终态 + 释放 + 单次 save ——

def _require_completion_evidence(repo_root, task_id, uid, unit, st) -> None:
    """RB-1 完成证据门（release hardening WU-P2）：completed 收尾前校验
    全部 required 验证命令的新鲜单元证据，不满足即 TaskManagerError。

    零副作用由调用顺序保证——本助手只在 finish_unit 的任何转换 /
    release / save / journal 之前调用，拒绝路径不写任何 journal 事件
    （含拒绝事件，计划 §2.3.4：不扩大事件面）。

    双根分离（RB-2）：git 求值根按任务绑定解析——
    git_root = state.resolve_repository_root(st, repo_root)（绑定
    repository.root 优先，legacy 回退账本根 repo_root）；touched 清单
    与证据指纹按 git_root 计算，而验证事件（events）**必须从账本根
    repo_root 注入**——fresh_unit_verification 缺省会从 git 根读
    journal，多仓场景（账本根 ≠ git 根）时会读错地方。

    - 复用 reconcile.fresh_unit_verification（WU-P1 共享证据谓词；
      与恢复对账同一口径）；空 required 单元经谓词快路径零 git 放行
      （生产单元受「无验证命令的单元不可派发」的 validate_state 约束
      不会出现该形状，此性质是谓词共用口径）；
    - evidence["ok"] 为 False（missing / stale / partial / wrong-unit /
      legacy 无 unit 字段证据）→ TaskManagerError，消息含 api 名、uid、
      missing 命令清单（逐条）与补证指引；
    - ownership.OwnershipError / fingerprint.FingerprintError → 包装为
      TaskManagerError（fail-closed：无法判定证据新鲜性即不能完成）。
    """
    from runtime import fingerprint  # 局部导入：不新增模块级 import
    api = "finish_unit"
    git_root = state.resolve_repository_root(st, repo_root)
    try:
        evidence = reconcile.fresh_unit_verification(
            git_root, task_id, unit,
            events=journal.read_events(repo_root, task_id))
    except (ownership.OwnershipError, fingerprint.FingerprintError) as exc:
        raise TaskManagerError(
            "%s：单元 %s 完成被拒：无法判定证据新鲜性，fail-closed 拒绝"
            "完成（原因：%s）" % (api, uid, exc)) from exc
    if not evidence["ok"]:
        raise TaskManagerError(
            "%s：单元 %s 完成被拒（RB-1 完成证据门）：required 验证命令"
            "缺少新鲜 pass 证据（missing：%s）——亲自运行单元验证命令后"
            "用 record_unit_verification 记录（时序：record → "
            "finish_unit → commit，record 与 finish 之间的任何 git 提交"
            "都会使证据失效须重验）"
            % (api, uid, "；".join(evidence["missing"])))


def _close_finished_waves(st) -> "list[str]":
    """wave 收口检查（v2.1 M4 wu-21-08，finish_unit 专用内存变换）。

    对每个 active wave：若其 units 全部处于 WAVE_CLOSED_UNIT_STATUSES
    （completed/failed/cancelled/verifying）→ 就地置 status="closed" +
    closed_at=当前时刻，收集其 wave_id。返回本次关闭的 wave_id 列表
    （落盘与 journal 归调用方：save_state 已在 finish_unit 的单次落盘
    内，wave_closed 事件随其后的 journal 追加）。无 dispatch 块 / 无
    waves 键 / 无 active wave → 空列表零行为（legacy 任务零影响）。
    """
    dispatch_block = st.get("dispatch")
    if not isinstance(dispatch_block, dict):
        return []
    waves = dispatch_block.get("waves")
    if not isinstance(waves, list):
        return []
    statuses = {}
    units = st.get("work_units")
    if isinstance(units, list):
        for unit in units:
            if isinstance(unit, dict):
                statuses[unit.get("id")] = unit.get("status")
    closed = []
    for wave in waves:
        if not isinstance(wave, dict) or wave.get("status") != "active":
            continue
        members = wave.get("units")
        if not isinstance(members, list) or not members:
            continue
        if all(statuses.get(uid) in WAVE_CLOSED_UNIT_STATUSES
               for uid in members):
            wave["status"] = "closed"
            wave["closed_at"] = _utc_now_iso()
            closed.append(wave.get("wave_id"))
    return closed


def finish_unit(repo_root, task_id, uid, *, outcome="completed") -> dict:
    """单元收尾：RB-1 完成证据门（completed 时）→ 终态转换 → 释放租约
    → active 移除 → 单次 save → unit_finished 事件；返回 state dict。

    流程：
      1. outcome ∈ ("completed", "failed", "cancelled")，否则 ValueError
        （先于任何 I/O 校验，失败零副作用）；
      2. load_state；找不到 uid → TaskManagerError；
      3. 单元 status 须 ∈ ("running", "verifying")，否则 TaskManagerError
         （消息含当前状态）；
      4. 【RB-1 完成证据门，WU-P2；RB-2 双根分离】仅 outcome ==
         "completed" 时：git 求值根按任务绑定解析（绑定 repository.root
         优先，legacy 回退账本根），调 reconcile.fresh_unit_verification(
         git_root, task_id, unit, events=从账本根读取的事件)——全部
         required command 须各存在一条绑定当前指纹的新鲜 pass 证据
         （all-match），才允许进入转换；missing / stale / partial /
         wrong-unit / legacy 无 unit 字段证据一律 TaskManagerError
         （消息含 missing 清单与 record_unit_verification 补证指引），
         此时零副作用——state.json 字节、leases.json、dispatch.active、
         journal 全部不变；git / 指纹读取失败（OwnershipError /
         FingerprintError）同样拒绝（fail-closed 包装为
         TaskManagerError）。failed / cancelled 收尾不需要证据。空
         required 单元经谓词快路径零 git 直接放行；
      5. 转换：running + completed → 先 verifying 再 completed（§70 父
         验证语义：worker 报告只是 claim，编码为两次表内转换）；其余
         单步直达（running→failed/cancelled、verifying→completed/
         failed/cancelled，均在 §62 表内）；终态落点后兜底清除单元的
         runtime.quota_interrupted_from 中断来源标记（RB-21-01，存在
         才清）；
      6. lease.release_lease + dispatch.active 移除 uid（在则删）；
         随后 wave 收口（wu-21-08）：active wave 全成员进入终态/
         verifying → 置 status="closed"+closed_at（无 waves 键零行为）；
      7. save_state 一次（单元转换与 wave 收口同一次落盘）；
      8. journal unit_finished（unit + outcome）+ 逐个 wave_closed
         （仅实际关闭的 wave）。

    证据门先于转换是刻意的：拒绝发生在任何变更之前——被拒的收尾可
    安全重试（补证后重调即可）。转 verifying 不落盘也是刻意的：它只是
    running+completed 双跳的中间步，落盘与否不影响恢复语义——崩在双跳
    之间，盘上仍是 running，reconcile 按 §69 对账的结果一致。
    """
    api = "finish_unit"
    if outcome not in FINISH_OUTCOMES:
        raise ValueError(
            "%s：outcome %r 不在合法取值内（%s）"
            % (api, outcome, ", ".join(FINISH_OUTCOMES)))
    st = _require_state(repo_root, task_id, api)
    unit = _require_unit(st, task_id, uid, api)
    current = unit.get("status")
    if current not in ("running", "verifying"):
        raise TaskManagerError(
            "%s：单元 %s 当前状态为 %r，须为 running 或 verifying 才能"
            "收尾" % (api, uid, current))
    if outcome == "completed":
        # RB-1 完成证据门（WU-P2；RB-2 双根分离）：先验证据后转换——
        # 拒绝发生在任何变更之前（转换 / release / save / journal 均
        # 未发生）
        _require_completion_evidence(repo_root, task_id, uid, unit, st)
    if current == "running" and outcome == "completed":
        # §70：正常完成必须经 verifying（worker 报告只是 claim，父会话
        # 验证后才算 completed）——两次表内转换，不越表直跳
        work_unit.transition_work_unit(unit, "verifying")
    work_unit.transition_work_unit(unit, outcome)
    # RB-21-01 兜底：终态单元不再有「中断来源」语义（恢复落点已定），
    # 残留标记一律清除（存在才清，legacy 单元零影响）
    _clear_quota_interrupt_origin(unit)
    lease.release_lease(repo_root, task_id, uid)
    _remove_active(st, uid)
    # wave 收口（wu-21-08）：在 release+active 移除之后、save 之前检查
    # ——收尾使某 active wave 全成员进入终态/verifying 时关闭该 wave
    # （内存置 status/closed_at，随本函数唯一的 save_state 落盘）
    closed_waves = _close_finished_waves(st)
    state.save_state(repo_root, st)
    journal.append_event(repo_root, task_id, {
        "event": "unit_finished", "unit": uid, "outcome": outcome})
    for closed_wave_id in closed_waves:
        journal.append_event(repo_root, task_id, {
            "event": "wave_closed", "wave_id": closed_wave_id})
    _write_manifest_safe(repo_root, task_id)
    return st


# —— readiness：就绪推导态提升（v2.0.1 收尾，dogfood 缺口补齐） ——

# 依赖满足后可被提升为 ready 的单元状态（§62 表内合法边：
# pending → ready 与 waiting_dependency → ready 均在转换表内）
PROMOTABLE_STATUSES = ("pending", "waiting_dependency")


def refresh_readiness(repo_root, task_id) -> "list[str]":
    """把「依赖已全部 completed」的 pending / waiting_dependency 单元
    提升为 ready，返回本次提升的 uid 列表（按 units 出现序）。

    动机（dogfood 发现）：单元依赖满足后会停在 pending——
    prepare_dispatch 只接受 ready 单元（§64 就绪集推导），此前依赖
    满足后的状态提升靠调用方手工 transition_work_unit。本 API 把
    这一步固化为显式入口。

    流程：
      1. load_state（任务缺失 TaskManagerError；损坏 ValueError 上抛）；
      2. 逐单元：status ∈ PROMOTABLE_STATUSES 且
         dependency.deps_satisfied（每个依赖都存在且 completed）→
         transition_work_unit(unit, "ready")（§62 表内合法边）；
      3. 有提升才 save_state（单次）；无提升零写入（零落盘、零
         半状态，可安全重复调用）；
      4. 不写 journal——就绪是可从依赖图重新推导的状态提升，不是
         生命周期事实；journal 只记事实（派发 / 终态 / 租约），
         不记推导（刻意裁量，非遗漏）。

    调用时机：commit_dispatch / finish_unit 使某单元到达 completed
    之后、prepare_dispatch 派发下一单元之前——下游单元即可直接过
    准入，无需任何手工转态。
    """
    api = "refresh_readiness"
    st = _require_state(repo_root, task_id, api)
    units = st.get("work_units")
    if not isinstance(units, list):
        return []
    by_id = {unit.get("id"): unit for unit in units
             if isinstance(unit, dict) and isinstance(unit.get("id"), str)}
    promoted = []
    for unit in units:
        if not isinstance(unit, dict):
            continue
        if unit.get("status") not in PROMOTABLE_STATUSES:
            continue
        if not dependency.deps_satisfied(unit, by_id):
            continue
        work_unit.transition_work_unit(unit, "ready")
        promoted.append(unit.get("id"))
    if promoted:
        state.save_state(repo_root, st)
    return promoted


# —— 单元验证证据写入口（v2.0.1 加固 H6，审查项 P1-7） ——

# 单元级 verification 事件的状态词汇（与 reconcile 证据匹配条件一致：
# 只认 "pass"；"fail" 允许写入以留痕，但不构成完成证据）
UNIT_VERIFICATION_STATUSES = ("pass", "fail")


def record_unit_verification(repo_root, task_id, uid, command, fingerprint,
                             *, status="pass") -> dict:
    """单元级验证证据的唯一推荐写入口，返回写入的事件 dict。

    主会话亲自跑完单元验证命令后调用（§70：worker 报告只是 claim）；
    事件形状 {"event": "verification", "unit": uid, "command": command,
    "status": status, "fingerprint": fingerprint}——unit 字段使
    reconcile 恢复对账按单元精确匹配（H6）：相同 command / 重叠
    ownership 的单元之间不再存在错误复用证据的空间；无 unit 字段的
    旧格式事件自 H6 起不再被 reconcile 采信（保守按无证据处理）。

    参数校验（先于任何 I/O，失败零副作用）：uid / command /
    fingerprint 必须是非空 str，status ∈ UNIT_VERIFICATION_STATUSES
    ("pass", "fail")，否则 ValueError（中文消息含字段名）。

    分工锚定：本函数只写 journal（单元级恢复证据），不触碰
    state.json——任务级（完成门口径）验证证据仍走
    runtime.state.record_verification（写入 verification.completed /
    fingerprint，供 Stop 完成门比对），两者口径正交、互不替代。
    """
    api = "record_unit_verification"
    if not isinstance(uid, str) or uid == "":
        raise ValueError(
            "%s：uid 必须是非空字符串，得到 %r" % (api, uid))
    if not isinstance(command, str) or command == "":
        raise ValueError(
            "%s：command 必须是非空字符串，得到 %r" % (api, command))
    if not isinstance(fingerprint, str) or fingerprint == "":
        raise ValueError(
            "%s：fingerprint 必须是非空字符串，得到 %r"
            % (api, fingerprint))
    if status not in UNIT_VERIFICATION_STATUSES:
        raise ValueError(
            "%s：status %r 不在合法取值内（%s）"
            % (api, status, ", ".join(UNIT_VERIFICATION_STATUSES)))
    return journal.append_event(repo_root, task_id, {
        "event": "verification", "unit": uid, "command": command,
        "status": status, "fingerprint": fingerprint})


# —— recover：崩溃后 stale 租约闭环清理（v2.0.1 加固 H5，P1-4/P1-5） ——

def recover_leases(repo_root, task_id, *, now=None) -> dict:
    """崩溃恢复的租约清理入口：reconcile_leases 三分对账 → 仅释放
    stale 组 → lease_recovered 事件；返回对账结果 + released 清单。

    流程：
      1. load_state（任务缺失 TaskManagerError；units 非列表按空图
         容错）；
      2. reconcile.reconcile_leases 纯建议三分（stale / active /
         expired_running，各桶按 path 排序）；
      3. 仅对 stale 组逐路径 release_lease（owner 精确、不代删他人）；
         形状异常记录（owner 非非空字符串）无精确持有者可删，保守
         跳过——仍在返回值的 stale 组中可见；
      4. released 非空时 journal {"event": "lease_recovered",
         "released": [...], "kept_expired_running": [...]}；零释放不
         落事件（对账是只读惯例动作，不留噪声行）；
      5. expired_running（活跃写相但已过期——worker 可能仍在写）不
         动，仅在返回值中上报，裁决归主会话。

    返回：reconcile_leases 的结果 dict + "released" 键（实际释放的
    归一路径，排序）。崩溃后无需人工删除 leases.json——recover 与
    后续 prepare（§78 同 owner 幂等、自有租约不挡 plan）共同闭环。
    """
    api = "recover_leases"
    st = _require_state(repo_root, task_id, api)
    units = st.get("work_units")
    if not isinstance(units, list):
        units = []
    report = reconcile.reconcile_leases(repo_root, task_id, units, now=now)
    released = []
    for entry in report["stale"]:
        holder = entry["owner"]
        if not isinstance(holder, str) or holder == "":
            continue  # 无精确持有者可删，保守不动（stale 组仍上报）
        if lease.release_lease(repo_root, task_id, holder, [entry["path"]]):
            released.append(entry["path"])
    released = sorted(released)
    if released:
        journal.append_event(repo_root, task_id, {
            "event": "lease_recovered", "released": released,
            "kept_expired_running": sorted(
                item["path"] for item in report["expired_running"])})
    report["released"] = released
    return report


# —— 用户授权续跑（v2.1 §14/§22.7，wu-21-11 Authorized Quota Resume） ——

# handle_quota_exhausted 的合法入口任务状态（执行态族；任务不在此族
# → TaskManagerError——额度转态是执行期编排决策，created 等前置态
# 不存在「因额度耗尽而挂起」的语义）
QUOTA_WAIT_TASK_STATUSES = ("executing", "joining", "verifying", "reviewing")

# 额度耗尽时随任务一并转 waiting_quota 的单元状态（§62 表内边
# ready→waiting_quota、running→waiting_quota 均合法；waiting_quota
# 原地保持——已在等待态的单元不重复转、只计入 waiting_units 清单）
QUOTA_WAIT_UNIT_STATUSES = ("ready", "running", "waiting_quota")

# recommended_resume_at 的宽限秒数（§30 口径：reset 之后再等一等，
# 防唤醒过早；与 runtime.quota.scheduler.DEFAULT_GRACE_SECONDS 同源）
QUOTA_RESUME_GRACE_SECONDS = 300

# resume_from_quota 允许恢复执行的额度四态（AVAILABLE 直恢复；
# PRESSURE 也恢复——恢复后并发预算经既有 §12 折算自动收缩到 1；
# EXHAUSTED / UNKNOWN 保守等待，UNKNOWN 不虚构可用性）
QUOTA_RESUME_STATUSES = ("AVAILABLE", "PRESSURE")


def _recommended_resume_at(evaluation):
    """由 evaluation 计算建议恢复时刻（统一走 scheduler.plan_resume）。

    RB-21-01 起本函数降级为 runtime.quota.scheduler.plan_resume 的薄
    容错 wrapper——本模块不再维护第二套 reset 数学（历史的「最早
    EXHAUSTED 窗 reset + 宽限」min 口径已删除；统一为 §30 最晚多窗
    口径：resume_at = max(全部 EXHAUSTED 窗 reset) + 宽限，多窗同时
    EXHAUSTED 时不产生 premature wake 浪费 auto_once 的单窗预算）：
      - evaluation None / 非 dict / status 缺失或非法 / 规划异常 →
        None（不抛——调用方按「未知」处理，§31 不虚构）；
      - EXHAUSTED → plan["resume_at"]（任一阻塞窗 reset 不可解析 →
        periodic_fallback，plan["resume_at"] 为 None）；
      - 非 EXHAUSTED → None（AVAILABLE / PRESSURE 不需要调度时刻）。
    """
    if not isinstance(evaluation, dict):
        return None
    try:
        from runtime.quota import scheduler  # 函数内 import：monkeypatch 友好
        plan = scheduler.plan_resume(
            evaluation, grace_seconds=QUOTA_RESUME_GRACE_SECONDS)
    except Exception:
        return None
    if not isinstance(plan, dict):
        return None
    if evaluation.get("status") != "EXHAUSTED":
        return None
    return plan.get("resume_at")


def _evaluation_from_refreshed_cache(repo_root):
    """强刷后的额度缓存 snapshot → scheduler.evaluate 的 evaluation。

    resume_from_quota 的 EXHAUSTED 分支专用：wake 已以 force_refresh=
    True 强制刷新，provider 抓取成功时 resolver 缓存（
    <repo_root>/.glm-conductor/quota-cache.json）保存完整 snapshot
    （形状 {"provider", "fetched_at", "snapshot", "status"}）——复用
    resolver 的缓存原语（_cache_path / _load_cache，零 resolver 改动）
    读回 snapshot，经 scheduler.evaluate 产出 evaluation，交
    _recommended_resume_at（plan_resume 统一口径）计算建议恢复时刻。

    缓存缺失 / snapshot 非 dict / 求值异常 → None（§31 不虚构）。
    边界（mid-batch review P3）：provider 层在 wake 时抓取失败 →
    resolver 回退陈旧缓存 status，此处读到的 snapshot 是休眠前的
    旧账——recommended_resume_at 仍只是建议值（不虚构、主会话按
    四态重解析裁决），消费方不得将其当作已验证的可用性事实。
    """
    try:
        from runtime.quota import resolver, scheduler  # 函数内 import
        cache = resolver._load_cache(resolver._cache_path(repo_root))
        if not isinstance(cache, dict):
            return None
        snapshot = cache.get("snapshot")
        if not isinstance(snapshot, dict):
            return None
        return scheduler.evaluate(snapshot)
    except Exception:
        return None


def _continuity_view(st) -> dict:
    """容错读取续跑授权四元组（execution_policy 事实源，§14）。

    返回 {"auto_resume", "source", "max_quota_windows",
    "consumed_quota_windows", "remaining"}：
      - auto_resume：continuity.auto_resume；缺块 / 非法词汇 → 保守
        按 "manual"（legacy 形态与 default_execution_policy 一致）；
      - source：authorization.source；缺块 / 非法词汇 → "default"；
      - max_quota_windows：continuity.max_quota_windows；非 >= 0 int
        → 0；
      - consumed_quota_windows：经 execution_policy.consumed_quota_
        windows 容错读（缺键 / 形状异常 → 0）；
      - remaining = max(0, max_quota_windows - consumed_quota_windows)
        （record_quota_wake 只增不减，跨过 max 后不再出现负数口径）。
    纯读函数：不改入参、零 I/O。
    """
    policy = st.get("execution_policy")
    continuity = (policy.get("continuity")
                  if isinstance(policy, dict) else None)
    continuity = continuity if isinstance(continuity, dict) else {}
    auto_resume = continuity.get("auto_resume")
    if auto_resume not in ("manual", "notify", "auto_once", "until_done"):
        auto_resume = "manual"
    authorization = (policy.get("authorization")
                     if isinstance(policy, dict) else None)
    source = (authorization.get("source")
              if isinstance(authorization, dict) else None)
    if source not in ("default", "user"):
        source = "default"
    max_windows = continuity.get("max_quota_windows")
    if isinstance(max_windows, bool) or not isinstance(max_windows, int) \
            or max_windows < 0:
        max_windows = 0
    consumed = consumed_quota_windows(policy)
    return {
        "auto_resume": auto_resume,
        "source": source,
        "max_quota_windows": max_windows,
        "consumed_quota_windows": consumed,
        "remaining": max(0, max_windows - consumed),
    }


def _quota_wake_decision(view) -> dict:
    """授权矩阵纯决策（§14.2-§14.5；输入 _continuity_view 的输出）。

    返回 {"required", "budget_exhausted", "prompt_mode", "reason",
    "transition_waiting_user"}：
      - required：「授权允许自动恢复执行」——manual / notify 恒 False；
      - prompt_mode："wake"（auto_once / until_done 且已授权且有预算
        ——自足唤醒 prompt）；"reminder"（notify——提醒模板，任务实际
        执行前仍需用户动作）；None（不产出 prompt）；
      - transition_waiting_user：auto_once / until_done 且已授权且预算
        耗尽（remaining <= 0）→ True（§14.5 授权耗尽语义）；source
        非 "user" 的未授权分支不转（合法 state 里 auto_once/until_done
        恒已授权——execution_policy §5.4 不变量保证，该分支是纵深防御）；
      - reason：中文一句话裁决理由。
    纯函数：零 I/O。
    """
    auto_resume = view["auto_resume"]
    remaining = view["remaining"]
    if auto_resume == "manual":
        return {
            "required": False, "budget_exhausted": False,
            "prompt_mode": None, "transition_waiting_user": False,
            "reason": "auto_resume=manual：不创建自动化唤醒，未来 "
                      "SessionStart 恢复注入会提示用户手动续跑",
        }
    if auto_resume == "notify":
        return {
            "required": False, "budget_exhausted": False,
            "prompt_mode": "reminder", "transition_waiting_user": False,
            "reason": "auto_resume=notify：授权允许提醒、不允许自动恢复"
                      "执行——可自建一次性提醒 automation（maxRuns=1，"
                      "prompt 文本已随返回给出），任务实际执行前仍需用户"
                      "动作",
        }
    # —— auto_once / until_done（跨窗口自动续跑授权族） ——
    if view["source"] != "user":
        return {
            "required": False, "budget_exhausted": remaining <= 0,
            "prompt_mode": None, "transition_waiting_user": False,
            "reason": "auto_resume=%r 未获得用户授权（authorization."
                      "source != \"user\"），不自动恢复执行" % auto_resume,
        }
    if remaining <= 0:
        return {
            "required": False, "budget_exhausted": True,
            "prompt_mode": None, "transition_waiting_user": True,
            "reason": "auto_resume=%r 的窗口预算已耗尽（consumed %d / "
                      "max %d），不得再创建任何自动化唤醒——转 waiting_user"
                      "等待用户重新授权（§14.5）"
                      % (auto_resume, view["consumed_quota_windows"],
                         view["max_quota_windows"]),
        }
    return {
        "required": True, "budget_exhausted": False,
        "prompt_mode": "wake", "transition_waiting_user": False,
        "reason": "auto_resume=%r 已获用户授权且窗口预算剩余 %d：创建"
                  "一次性自动化唤醒（maxRuns=1）恢复执行"
                  % (auto_resume, remaining),
    }


def handle_quota_exhausted(repo_root, task_id, *, evaluation=None) -> dict:
    """EXHAUSTED 转态链 + 授权矩阵裁决（v2.1 §14.1，主会话显式调用）。

    触发点：prepare_dispatch / prepare_dispatch_wave 解析出 EXHAUSTED
    时由主会话显式调用——prepare 自身只抛 waiting_quota 口径错误，
    不自动转态（转态是编排决策）。

    流程：
      1. load_state（任务缺失 TaskManagerError；损坏 ValueError 上抛）；
         任务 status 须 ∈ QUOTA_WAIT_TASK_STATUSES（executing / joining /
         verifying / reviewing），否则 TaskManagerError；
      2. 未终态单元（QUOTA_WAIT_UNIT_STATUSES：ready / running /
         waiting_quota）转 waiting_quota（§62 表内边；waiting_quota
         原地保持）+ dispatch.active 清空；首次转换时在中断来源标记
         unit["runtime"]["quota_interrupted_from"] = 转换前状态
         （RB-21-01：恢复对账据此区分 ready / running——running 中断
         单元须先 reconcile 对账，禁止盲目回 ready；已是 waiting_quota
         的单元不覆盖既有标记）；
      3. 任务级转 waiting_quota：joining / verifying / reviewing 先经
         表内边回 executing 落盘一次，再 executing → waiting_quota
         （TASK_TRANSITIONS 无 joining→waiting_quota 直达边，两段转换
         全部落在表内；executing 入口单次直达）；
      4. journal quota_waiting {units, recommended_resume_at}；
      5. 授权矩阵（_quota_wake_decision，§14.2-§14.5）：budget 分支
         耗尽时任务再转 waiting_user + journal
         auto_resume_authorization_exhausted；
      6. manifest 刷新（_write_manifest_safe——写失败只降级为
         manifest_write_failed 警告事件，绝不阻断主事务）；放在全部
         转态之后，保证 manifest.task_status 反映最终状态；
      7. 返回键冻结 dict：
         {"task_status", "waiting_units", "recommended_resume_at",
          "auto_resume", "remaining_quota_windows",
          "wake": {"required", "prompt", "budget_exhausted"}, "reason"}。

    evaluation（可选）：quota 子系统的 evaluate() 输出 dict——提供
    windows 时 recommended_resume_at = scheduler.plan_resume 统一口径
    （§30 最晚多窗语义：max(全部 EXHAUSTED 窗 reset) + 300 秒宽限，
    RB-21-01 起不再用历史上的「最早窗」min 口径——多窗同时 EXHAUSTED
    时过早唤醒会浪费 auto_once 的单窗预算）；None（或无 windows /
    reset 不可解析 / status 非法）→ None（§31 不虚构）。
    wake.prompt：required 分支与 notify 提醒分支为
    quota_wake_prompt(...) 的自足文本，其余为 None。
    """
    api = "handle_quota_exhausted"
    st = _require_state(repo_root, task_id, api)
    if st.get("status") not in QUOTA_WAIT_TASK_STATUSES:
        raise TaskManagerError(
            "%s：任务 %s 当前状态为 %r，不在执行态族（%s）内——额度耗尽"
            "转态只对执行中的任务有意义"
            % (api, task_id, st.get("status"),
               ", ".join(QUOTA_WAIT_TASK_STATUSES)))
    waiting = []
    units = st.get("work_units")
    if isinstance(units, list):
        for unit in units:
            if not isinstance(unit, dict):
                continue
            if unit.get("status") not in QUOTA_WAIT_UNIT_STATUSES:
                continue
            if unit.get("status") != "waiting_quota":
                # RB-21-01：保存中断来源（恢复对账据此区分落点——
                # running 中断单元恢复须先 reconcile，禁止盲目回 ready；
                # 仅首次转换时写，已是 waiting_quota 不覆盖既有标记）
                interrupted_from = unit.get("status")
                work_unit.transition_work_unit(unit, "waiting_quota")
                runtime_meta = unit.get("runtime")
                if not isinstance(runtime_meta, dict):
                    runtime_meta = {}
                    unit["runtime"] = runtime_meta
                runtime_meta["quota_interrupted_from"] = interrupted_from
            waiting.append(unit.get("id"))
    _set_active(st, [])
    # 任务级转换：非 executing 入口先经表内边回 executing 落盘一次
    # （joining/verifying/reviewing → executing 均为合法边），再直达
    # waiting_quota——两段转换都落在 TASK_TRANSITIONS 内
    if st.get("status") != "executing":
        st["status"] = "executing"
        state.save_state(repo_root, st)
    st["status"] = "waiting_quota"
    state.save_state(repo_root, st)
    recommended = _recommended_resume_at(evaluation)
    journal.append_event(repo_root, task_id, {
        "event": "quota_waiting", "units": list(waiting),
        "recommended_resume_at": recommended})
    # 授权矩阵（§14.2-§14.5）：wake 指令与 waiting_user 耗尽语义
    view = _continuity_view(st)
    decision = _quota_wake_decision(view)
    prompt = None
    if decision["prompt_mode"] is not None:
        prompt = quota_wake_prompt(repo_root, task_id)
    if decision["transition_waiting_user"]:
        st["status"] = "waiting_user"
        state.save_state(repo_root, st)
        journal.append_event(repo_root, task_id, {
            "event": "auto_resume_authorization_exhausted",
            "auto_resume": view["auto_resume"],
            "consumed_quota_windows": view["consumed_quota_windows"],
            "max_quota_windows": view["max_quota_windows"]})
    _write_manifest_safe(repo_root, task_id)
    return {
        "task_status": st.get("status"),
        "waiting_units": list(waiting),
        "recommended_resume_at": recommended,
        "auto_resume": view["auto_resume"],
        "remaining_quota_windows": view["remaining"],
        "wake": {"required": decision["required"],
                 "prompt": prompt,
                 "budget_exhausted": decision["budget_exhausted"]},
        "reason": decision["reason"],
    }


def quota_wake_prompt(repo_root, task_id) -> str:
    """生成自足的一次性额度唤醒 prompt（v2.1 §14.4 wake 形态锁定）。

    宿主实测（2026-08-31 wake 入账本）：ZCode automation wake 是同会话
    续行——prompt 以用户 turn 注入、SessionStart 不重放，因此 prompt
    必须自足：task_id、账本根 / 任务仓库根、恢复首步（resume_from_quota
    → 按账本就绪继续）、额度检查口径、预算状态（已消耗 / 共几窗 /
    剩余）、红线（绝不重试 CronDelete/CronUpdate、发布动作征询用户、
    RB-1 完成证据门指纹口径）与一次性（maxRuns=1）语义全部内置。

    纯函数：只读 state.json（预算读 execution_policy.continuity），
    零写副作用；任务缺失 TaskManagerError。中文模板，主会话把它放进
    automation 的 prompt 字段即可（automation 创建/删除本身归主会话
    的宿主工具，runtime 只产出指令与记账）。
    """
    api = "quota_wake_prompt"
    st = _require_state(repo_root, task_id, api)
    view = _continuity_view(st)
    waiting_ids = [
        unit.get("id") for unit in st.get("work_units") or []
        if isinstance(unit, dict) and unit.get("status") == "waiting_quota"]
    work_root = state.resolve_repository_root(st, str(repo_root))
    lines = [
        "GLM CONDUCTOR 额度唤醒（一次性 automation：recurring=false、"
        "maxRuns=1，本次触发即自完成）",
        "",
        "任务 task_id：%s" % task_id,
        "账本根：%s" % repo_root,
        "任务仓库根：%s" % work_root,
        "等待恢复的单元：%s" % ("、".join(waiting_ids) if waiting_ids
                               else "无（任务级挂起）"),
        "",
        "本唤醒是一次性自动化（maxRuns=1）：触发即终结，绝不依赖 "
        "CronUpdate 修改参数或改期。",
        "",
        "第一步（必须最先执行）——额度检查与恢复：",
        "1. 解析当前额度四态（绝不重试网络；--force-refresh 强制走 "
        "provider——唤醒后不得信任休眠期间的本地缓存；provider 不可用"
        "时按陈旧缓存 → UNKNOWN 层级回退）：",
        "   python3 plugins/glm-conductor/runtime/cli.py quota-resolve "
        "--force-refresh '%s'"
        % repo_root,
        "2. 调用 runtime.task_manager.resume_from_quota(repo_root=r'%s', "
        "task_id='%s')（内部同样强制刷新额度并对中断单元做恢复对账）："
        % (repo_root, task_id),
        "   - AVAILABLE / PRESSURE → 任务转回 executing、waiting_quota "
        "单元按中断来源恢复（ready 来源直回 ready；running 来源先 "
        "reconcile 四分：干净重派回 ready、成果可复用直达 verifying、"
        "有进度回 ready 待主会话组装进度包续作、人工裁决保持等待），"
        "按账本就绪顺序经 prepare_dispatch / "
        "prepare_dispatch_wave 继续（遵守 orchestration 纪律：SELECTIVE "
        "ROUTE、permit 门与租约时序不得绕过）；",
        "   - EXHAUSTED / UNKNOWN → 零转态保守等待：不得派发、不得再建"
        "唤醒，按 recovery 摘要重排或降级 SessionStart 恢复。",
        "",
        "预算状态：已消耗 %d / 共 %d 窗（剩余 %d 窗）。本唤醒本身不消耗"
        "窗口预算（automation arm/fire/create 一律不消费，D15-g："
        "automation lifecycle ≠ quota epoch consumption）；窗口预算仅在"
        "任务成功恢复执行（resume commit point）时消耗；预算耗尽后不得"
        "再创建任何自动化唤醒，一律降级 SessionStart 恢复。"
        % (view["consumed_quota_windows"], view["max_quota_windows"],
           view["remaining"]),
        "",
        "红线（违反即事故）：",
        "- 绝不重试 CronDelete / CronUpdate（本机已知 glitch）；清理"
        "失败容忍，降级 SessionStart 恢复；",
        "- 发布类动作（git push、对外发布、删除性操作）必须先征询用户；",
        "- 单元完成必须过 RB-1 完成证据门：finish_unit 前亲自运行该"
        "单元全部 required 验证命令，并用 record_unit_verification 记录"
        "绑定当前改动的 pass 指纹证据（record 与 finish 之间不得产生 "
        "git 提交，否则证据 stale 须重验）。",
    ]
    return "\n".join(lines)


def record_quota_wake(repo_root, task_id, *, automation_id, fires_at) -> dict:
    """window 扣减记账（v2.1 legacy arm-time 记账；v2.1 §14.4：主会话
    CronCreate 成功后调用）。

    LEGACY / DEPRECATED（v2.2 C1a）：本入口仅为 v2.1 one-shot 兼容
    保留；v2.2 persistent path 禁止调用（automation arm/fire/create
    一律不消费窗口预算，D15-g：automation lifecycle ≠ quota epoch
    consumption）——消费点唯一合法位置 = resume commit point（修正
    计划 §15.1），C1b 落地新记账 API 后本入口退役。

    - 幂等（SH-21-01）：journal 已有同 automation_id 的
      quota_wake_recorded 事件 → 直接返回既有消耗结果（不递增、
      不新建事件、零写副作用），返回 dict 既有键保留并增标
      "idempotent": True——cron glitch 重放 / 误重试不再重复消耗
      窗口预算（幂等只按 automation_id 判定，不同 automation_id
      照常各消耗一窗）；
    - 写入前授权复核（SH-21-01 三查，读取口径与 _continuity_view /
      _quota_wake_decision 一致——execution_policy 块缺/坏按默认块
      解释为 manual）：auto_resume ∈ {auto_once, until_done} /
      authorization.source == "user" / 剩余窗口 > 0，任一不满足 →
      TaskManagerError（中文消息指明 violated 条件，零副作用——
      无事件、state.json 字节不变；manual/notify 任务不得经本 API
      制造 consumed window）；
    - continuity.consumed_quota_windows += 1（缺键按 0 起算；legacy
      缺 execution_policy 块时以默认块补齐后写——半定义块过不了
      validate_state 的完整性闸）；
    - journal quota_wake_recorded {automation_id, fires_at, consumed,
      remaining}；
    - 与 automation 存活解耦：wake 未触发、automation 被清理或丢失
      都不回滚（正确性底线永远是未来 SessionStart 恢复注入，automation
      只是 best-effort bridge）；无回滚 API，不同 automation_id 的
      二次调用纯递增。

    参数校验（先于任何 I/O，失败零副作用）：automation_id / fires_at
    必须是非空 str，否则 ValueError（中文消息含字段名）。返回
    {"consumed_quota_windows", "remaining_quota_windows",
    "max_quota_windows", "automation_id", "fires_at"}；幂等命中路径
    既有键保留并增标 "idempotent": True。
    """
    api = "record_quota_wake"
    if not isinstance(automation_id, str) or automation_id == "":
        raise ValueError(
            "%s：automation_id 必须是非空字符串，得到 %r"
            % (api, automation_id))
    if not isinstance(fires_at, str) or fires_at == "":
        raise ValueError(
            "%s：fires_at 必须是非空字符串（ISO8601 口径），得到 %r"
            % (api, fires_at))
    st = _require_state(repo_root, task_id, api)
    view = _continuity_view(st)
    # SH-21-01 幂等：同 automation_id 已记过账 → 原样返回既有消耗结果
    # （取首条匹配——正确流程下至多一条；历史脏数据重复时首条是原始账）
    prior = None
    for event in journal.read_events(repo_root, task_id):
        if event.get("event") == "quota_wake_recorded" \
                and event.get("automation_id") == automation_id:
            prior = event
            break
    if prior is not None:
        consumed = prior.get("consumed")
        if isinstance(consumed, bool) or not isinstance(consumed, int) \
                or consumed < 0:
            consumed = view["consumed_quota_windows"]
        remaining = prior.get("remaining")
        if isinstance(remaining, bool) or not isinstance(remaining, int) \
                or remaining < 0:
            remaining = view["remaining"]
        recorded_fires_at = prior.get("fires_at")
        if not isinstance(recorded_fires_at, str) or recorded_fires_at == "":
            recorded_fires_at = fires_at
        return {
            "consumed_quota_windows": consumed,
            "remaining_quota_windows": remaining,
            "max_quota_windows": view["max_quota_windows"],
            "automation_id": automation_id,
            "fires_at": recorded_fires_at,
            "idempotent": True,
        }
    # SH-21-01 写入前授权复核（三查逐条指明 violated 条件；在任何
    # mutation / 落盘之前——拒绝路径零副作用）
    if view["auto_resume"] not in ("auto_once", "until_done"):
        raise TaskManagerError(
            "%s：auto_resume=%r 不在自动续跑授权族（auto_once / "
            "until_done）内——manual / notify 任务不得记账消耗窗口预算"
            "（额度恢复一律走 SessionStart 恢复注入 / 用户手动续跑）"
            % (api, view["auto_resume"]))
    if view["source"] != "user":
        raise TaskManagerError(
            "%s：authorization.source=%r 非 \"user\"——跨额度窗口自动"
            "续跑必须用户明确授权，拒绝记账窗口消耗（纵深防御，与 "
            "_quota_wake_decision 口径一致）" % (api, view["source"]))
    if view["remaining"] <= 0:
        raise TaskManagerError(
            "%s：窗口预算已耗尽（consumed %d / max %d，剩余 %d）——"
            "不得再记账新的自动化唤醒，转 waiting_user 等待用户重新"
            "授权（§14.5）" % (api, view["consumed_quota_windows"],
                               view["max_quota_windows"],
                               view["remaining"]))
    policy = st.get("execution_policy")
    if not isinstance(policy, dict):
        # legacy 任务无授权事实源块：以默认块补齐（完整四子块形状，
        # 否则 validate_state 拒绝落盘）——记账语义与默认 manual/0 授权
        # 解释一致，只是把「已消耗窗口」显式落盘
        policy = default_execution_policy()
        st["execution_policy"] = policy
    continuity = policy.get("continuity")
    if not isinstance(continuity, dict):
        continuity = default_execution_policy()["continuity"]
        policy["continuity"] = continuity
    consumed = consumed_quota_windows(policy) + 1
    continuity["consumed_quota_windows"] = consumed
    state.save_state(repo_root, st)
    view = _continuity_view(st)
    journal.append_event(repo_root, task_id, {
        "event": "quota_wake_recorded", "automation_id": automation_id,
        "fires_at": fires_at, "consumed": consumed,
        "remaining": view["remaining"]})
    return {
        "consumed_quota_windows": consumed,
        "remaining_quota_windows": view["remaining"],
        "max_quota_windows": view["max_quota_windows"],
        "automation_id": automation_id,
        "fires_at": fires_at,
    }


def record_quota_boundary_consumed(repo_root, task_id, *, epoch_id,
                                   executable_boundary_id,
                                   resume_started_at) -> dict:
    """resume-time 窗口消费记账（v2.2 C1b；修正计划 §22.1 + §C1b +
    §15.1 消费事务点冻结）。

    消费点唯一合法位置 = §15.1 冻结的 Resume Controller resume commit
    point：新 executable epoch 已确认、authorization / window budget /
    reconcile 均通过、Resume Controller 成功接受该 epoch 并准备把任务
    从 waiting_quota 恢复为执行态的那个 commit point。本 API 只提供
    记账，绝不接线 resume_from_quota（接线与调用时序归 Resume
    Controller 工作单元）；§22.1 不消费清单（bridge fire / create /
    arm / no-op、still exhausted、weekly blocked、provider unavailable、
    manual/notify warm-only、primer model call、repeated recurring
    probe）一律不得调用本 API。

    与 record_quota_wake（v2.1 legacy，C1a 已标注 deprecated）的关系：
    三查 / 幂等 / 写路径模式逐条镜像该实现，仅幂等 key 与事件形状按
    §22.1 / §C1b 冻结口径更换。

    - 幂等 key = task_id + epoch_id（§22.1：绝不能对同一 epoch 二次
      消费）：journal 已有同 epoch_id 的 quota_boundary_consumed 事件
      → 直接返回既有消耗结果（不递增、不新建事件、零写副作用），返回
      dict 既有键保留并增标 "idempotent": True——resume 重放 / 部分
      写入恢复（§15.1 幂等证据语义）不再重复消耗；不同 epoch_id 照常
      各消费一窗（epoch 推进 = 窗口身份推进）；
    - 写入前授权三查（口径与 _continuity_view / _quota_wake_decision
      一致——execution_policy 块缺/坏按默认块解释为 manual）：
      auto_resume ∈ {auto_once, until_done} / authorization.source ==
      "user" / 剩余窗口 > 0，任一不满足 → TaskManagerError（中文消息
      指明 violated 条件，零副作用——无事件、state.json 字节不变；
      manual / notify 任务不得经本 API 制造 consumed window）；
    - continuity.consumed_quota_windows += 1（缺键按 0 起算；legacy
      缺 execution_policy 块时以默认块补齐后写——半定义块过不了
      validate_state 的完整性闸）；
    - journal quota_boundary_consumed {epoch_id, executable_boundary_id,
      resume_started_at, consumed, remaining}（§C1b：事件同时携带
      epoch_id 与 executable_boundary_id；legacy 迁移核验才用
      boundary_id alias 读旧人工证据）。

    参数校验（先于任何 I/O，失败零副作用）：epoch_id /
    executable_boundary_id / resume_started_at 必须是非空 str，否则
    ValueError（中文消息含字段名）。返回 {"consumed_quota_windows",
    "remaining_quota_windows", "max_quota_windows", "epoch_id",
    "executable_boundary_id", "resume_started_at"}；幂等命中路径既有
    键保留并增标 "idempotent": True。任务缺失 TaskManagerError。
    """
    api = "record_quota_boundary_consumed"
    for field, value in (("epoch_id", epoch_id),
                         ("executable_boundary_id", executable_boundary_id),
                         ("resume_started_at", resume_started_at)):
        if not isinstance(value, str) or value == "":
            raise ValueError(
                "%s：%s 必须是非空字符串，得到 %r" % (api, field, value))
    st = _require_state(repo_root, task_id, api)
    view = _continuity_view(st)
    # 幂等（§22.1 / §15.1）：同 epoch_id 已消费 → 原样返回既有消耗
    # 结果（取首条匹配——正确流程下至多一条；历史脏数据重复时首条是
    # 原始账）
    prior = None
    for event in journal.read_events(repo_root, task_id):
        if event.get("event") == "quota_boundary_consumed" \
                and event.get("epoch_id") == epoch_id:
            prior = event
            break
    if prior is not None:
        consumed = prior.get("consumed")
        if isinstance(consumed, bool) or not isinstance(consumed, int) \
                or consumed < 0:
            consumed = view["consumed_quota_windows"]
        remaining = prior.get("remaining")
        if isinstance(remaining, bool) or not isinstance(remaining, int) \
                or remaining < 0:
            remaining = view["remaining"]
        recorded_boundary = prior.get("executable_boundary_id")
        if not isinstance(recorded_boundary, str) or recorded_boundary == "":
            recorded_boundary = executable_boundary_id
        recorded_started_at = prior.get("resume_started_at")
        if not isinstance(recorded_started_at, str) \
                or recorded_started_at == "":
            recorded_started_at = resume_started_at
        return {
            "consumed_quota_windows": consumed,
            "remaining_quota_windows": remaining,
            "max_quota_windows": view["max_quota_windows"],
            "epoch_id": epoch_id,
            "executable_boundary_id": recorded_boundary,
            "resume_started_at": recorded_started_at,
            "idempotent": True,
        }
    # 写入前授权三查（逐条指明 violated 条件；在任何 mutation / 落盘
    # 之前——拒绝路径零副作用；口径逐条镜像 record_quota_wake）
    if view["auto_resume"] not in ("auto_once", "until_done"):
        raise TaskManagerError(
            "%s：auto_resume=%r 不在自动续跑授权族（auto_once / "
            "until_done）内——manual / notify 任务不得消费窗口预算"
            "（额度恢复一律走 SessionStart 恢复注入 / 用户手动续跑）"
            % (api, view["auto_resume"]))
    if view["source"] != "user":
        raise TaskManagerError(
            "%s：authorization.source=%r 非 \"user\"——跨额度窗口自动"
            "续跑必须用户明确授权，拒绝消费窗口（纵深防御，与 "
            "_quota_wake_decision 口径一致）" % (api, view["source"]))
    if view["remaining"] <= 0:
        raise TaskManagerError(
            "%s：窗口预算已耗尽（consumed %d / max %d，剩余 %d）——"
            "不得再消费新 epoch，转 waiting_user 等待用户重新授权"
            "（§14.5）" % (api, view["consumed_quota_windows"],
                           view["max_quota_windows"], view["remaining"]))
    policy = st.get("execution_policy")
    if not isinstance(policy, dict):
        # legacy 任务无授权事实源块：以默认块补齐（与 record_quota_wake
        # 同款——完整四子块形状，否则 validate_state 拒绝落盘）
        policy = default_execution_policy()
        st["execution_policy"] = policy
    continuity = policy.get("continuity")
    if not isinstance(continuity, dict):
        continuity = default_execution_policy()["continuity"]
        policy["continuity"] = continuity
    consumed = consumed_quota_windows(policy) + 1
    continuity["consumed_quota_windows"] = consumed
    state.save_state(repo_root, st)
    view = _continuity_view(st)
    journal.append_event(repo_root, task_id, {
        "event": "quota_boundary_consumed", "epoch_id": epoch_id,
        "executable_boundary_id": executable_boundary_id,
        "resume_started_at": resume_started_at, "consumed": consumed,
        "remaining": view["remaining"]})
    return {
        "consumed_quota_windows": consumed,
        "remaining_quota_windows": view["remaining"],
        "max_quota_windows": view["max_quota_windows"],
        "epoch_id": epoch_id,
        "executable_boundary_id": executable_boundary_id,
        "resume_started_at": resume_started_at,
    }


def migrate_quota_window_accounting(repo_root, task_id) -> dict:
    """存量窗口记账保守迁移（v2.2 C1b；修正计划 §22.6，2026-09-02
    锁定）。

    语义冻结：
      - 不自动退款：new_consumed >= legacy_consumed 恒成立（max 语义）
        ——legacy arm-time debit 即使是错账，也不得因 C1 上线自动下调
        （自动退款 = 无用户重新授权地增加未来自动续跑预算）；以后扩
        大预算只能走正常 authorization 路径，不得偷偷修历史数字；
      - 用 journal 做「归属」，不是退款：
        verified_consumed = journal 中可证明真实跨 boundary resume 的
        unique quota_boundary_consumed 事件数，
        new_consumed = max(legacy_consumed, verified_consumed)；
      - 数字相同也要写事件：new_consumed == legacy_consumed 时仍落一
        次 quota_accounting_migrated（迁移事实本身是账本记录）；
      - 一次性：journal 已有 quota_accounting_migrated → 幂等 no-op
        （零写零事件），返回既有记录 + "idempotent": True。

    verified 证据口径：逐条 quota_boundary_consumed 事件取身份键
    （epoch_id 优先；legacy 人工证据（如 §22.6 所引 2026-09-01T22:20Z
    boundary five_hour:2026-09-01T21:59:00Z 条目）回退 boundary_id
    alias；两者皆缺视为不可核验、跳过），按身份去重——同 epoch 重放
    / 同 boundary 旧证据只计一次（与
    record_quota_boundary_consumed 的 task_id + epoch_id 幂等 key
    同构）。

    归属拆分（§22.6 冻结口径）：
      attributed_consumed = min(legacy_consumed, verified_consumed)
      legacy_unattributed_consumed
        = legacy_consumed − attributed_consumed
    （未归属部分仍然算已消费，不自动返还。）

    写路径：new_consumed != legacy_consumed 时更新
    continuity.consumed_quota_windows 并 save_state（legacy 缺
    execution_policy / continuity 块按默认块补齐）；数字不变则零
    state 写。随后无论数字是否变化都落 journal
    quota_accounting_migrated {legacy_consumed, verified_boundary_ids,
    attributed_consumed, legacy_unattributed_consumed, migrated_at}
    （§22.6 五字段，字段名逐字）。单次调用至多一次 save_state +
    一条事件。

    返回 {"legacy_consumed", "verified_boundary_ids",
    "attributed_consumed", "legacy_unattributed_consumed",
    "new_consumed", "migrated_at"}（new_consumed 仅供调用方核验 max
    不变量，不进事件——事件字段以 §22.6 五字段冻结）。任务缺失
    TaskManagerError。纯账本操作：零宿主调用、零网络。
    """
    api = "migrate_quota_window_accounting"
    st = _require_state(repo_root, task_id, api)
    view = _continuity_view(st)
    events = journal.read_events(repo_root, task_id)
    # 一次性闸（§22.6「迁移写一次」）：已有迁移事件 → no-op（消费方
    # 重复触发 / 重放不再产生第二条迁移记录）
    for event in events:
        if event.get("event") == "quota_accounting_migrated":
            return {
                "legacy_consumed": event.get("legacy_consumed",
                                             view["consumed_quota_windows"]),
                "verified_boundary_ids": event.get(
                    "verified_boundary_ids", []),
                "attributed_consumed": event.get("attributed_consumed"),
                "legacy_unattributed_consumed": event.get(
                    "legacy_unattributed_consumed"),
                "new_consumed": view["consumed_quota_windows"],
                "migrated_at": event.get("migrated_at"),
                "idempotent": True,
            }
    legacy_consumed = view["consumed_quota_windows"]
    verified_ids = []
    seen = set()
    for event in events:
        if event.get("event") != "quota_boundary_consumed":
            continue
        identity = event.get("epoch_id")
        if not isinstance(identity, str) or identity == "":
            # legacy 人工证据只落 boundary_id alias（§22.6 归属口径）
            identity = event.get("boundary_id")
        if not isinstance(identity, str) or identity == "":
            continue  # 无身份键的条目不可核验，不计入
        if identity in seen:
            continue  # 同 epoch 重放 / 同 boundary 旧证据只计一次
        seen.add(identity)
        verified_ids.append(identity)
    verified_consumed = len(verified_ids)
    new_consumed = max(legacy_consumed, verified_consumed)
    attributed = min(legacy_consumed, verified_consumed)
    unattributed = legacy_consumed - attributed
    migrated_at = _utc_now_iso()
    if new_consumed != legacy_consumed:
        policy = st.get("execution_policy")
        if not isinstance(policy, dict):
            policy = default_execution_policy()
            st["execution_policy"] = policy
        continuity = policy.get("continuity")
        if not isinstance(continuity, dict):
            continuity = default_execution_policy()["continuity"]
            policy["continuity"] = continuity
        continuity["consumed_quota_windows"] = new_consumed
        state.save_state(repo_root, st)
    journal.append_event(repo_root, task_id, {
        "event": "quota_accounting_migrated",
        "legacy_consumed": legacy_consumed,
        "verified_boundary_ids": verified_ids,
        "attributed_consumed": attributed,
        "legacy_unattributed_consumed": unattributed,
        "migrated_at": migrated_at})
    return {
        "legacy_consumed": legacy_consumed,
        "verified_boundary_ids": verified_ids,
        "attributed_consumed": attributed,
        "legacy_unattributed_consumed": unattributed,
        "new_consumed": new_consumed,
        "migrated_at": migrated_at,
    }


def _wake_budget_remaining(st) -> int:
    """任务 state 的剩余唤醒窗口预算（max(0, max - consumed)，容错读）。"""
    policy = st.get("execution_policy")
    continuity = (policy.get("continuity")
                  if isinstance(policy, dict) else None)
    max_windows = (continuity.get("max_quota_windows")
                   if isinstance(continuity, dict) else None)
    if isinstance(max_windows, bool) or not isinstance(max_windows, int) \
            or max_windows < 0:
        max_windows = 0
    return max(0, max_windows - consumed_quota_windows(policy))


def _clear_quota_interrupt_origin(unit) -> None:
    """清除单元的中断来源标记（RB-21-01 恢复落点收尾）。

    runtime.quota_interrupted_from 成功恢复后即失义（下一轮额度中断
    会重新写入），清除避免陈旧标记误导后续对账；runtime dict 因此变
    空则整键删除（保持单元形状最简）。runtime 缺失 / 非 dict 时零操作。
    """
    runtime_meta = unit.get("runtime")
    if not isinstance(runtime_meta, dict):
        return
    runtime_meta.pop("quota_interrupted_from", None)
    if not runtime_meta:
        unit.pop("runtime", None)


def _reconcile_running_unit(repo_root, task_id, uid):
    """对 running 中断单元做四分对账 → (落点状态, unit_recovery 条目)。

    调 runtime.reconcile.reconcile_agent_run（纯读对账，RB-21-01：
    resume 链禁止盲目 waiting_quota→ready——worker 现场可能有残留），
    按 classification 决定落点：
      - redispatch_clean → "ready"（无执行内容无残留，全新派发）；
      - reuse_result → "verifying"（agent 成果可复用，经 §62 演进的
        waiting_quota→verifying 新边直达验证；条目带
        action_required="recover_agent_result"，主会话须先捞取成果）；
      - resume_with_progress → "ready"（有进度无完整证据；条目透传
        evidence 证据句柄，主会话组装 Previous Progress Package 随新
        规格续派——编排纪律，runtime 不机械阻止）；
      - manual_ruling（及未知分类，防御）→ "waiting_quota"（保持等待，
        禁止自动猜测；条目透传 rationale）。

    fail-closed：reconcile 异常（求值失败 / 环境问题）→ 按 manual_ruling
    处理（rationale 注明 reconcile error 与异常类型名），绝不让异常炸掉
    整个 resume。
    """
    try:
        from runtime import reconcile  # 函数内 import：monkeypatch 友好
        report = reconcile.reconcile_agent_run(repo_root, task_id, uid)
    except Exception as exc:
        return "waiting_quota", {
            "classification": "manual_ruling",
            "rationale": ["reconcile error: %s" % type(exc).__name__]}
    classification = (report.get("classification")
                      if isinstance(report, dict) else None)
    if classification == "redispatch_clean":
        return "ready", {"classification": classification}
    if classification == "reuse_result":
        return "verifying", {
            "classification": classification,
            "action_required": "recover_agent_result",
            "evidence": report.get("evidence")}
    if classification == "resume_with_progress":
        return "ready", {
            "classification": classification,
            "evidence": report.get("evidence")}
    # manual_ruling 与未知分类（防御保留位）：保持 waiting_quota
    entry = {"classification":
             classification if isinstance(classification, str)
             and classification else "manual_ruling"}
    rationale = (report.get("rationale")
                 if isinstance(report, dict) else None)
    if rationale is not None:
        entry["rationale"] = rationale
    return "waiting_quota", entry


def resume_from_quota(repo_root, task_id, *, status=None) -> dict:
    """额度唤醒 / SessionStart 的恢复首步（v2.1 §14 恢复入口）。

    流程：
      1. load_state（任务缺失 TaskManagerError）；
      2. status 缺省经 runtime.quota.resolver.resolve_quota_status 解析，
         **强制刷新**（RB-21-01：force_refresh=True——§32 唤醒后必须
         强刷，休眠期间的新鲜缓存可能早已失效成误导；函数内 import +
         属性访问，测试 monkeypatch 友好；绝不重试网络、异常不外泄、
         凭证零落盘）——source 记 resolved["source"]；显式 status
         （调用方声明）直通，source 记 "explicit"（零解析、零网络、
         零缓存读）；
      3. EXHAUSTED（经 resolver 解析的分支）→ 从强刷后的缓存读回
         snapshot，交 scheduler.evaluate + plan_resume 统一口径计算
         recommended_resume_at（§30 最晚 reset + 宽限；缓存不可读 /
         reset 未知 → None 不虚构）；
      4. status ∈ QUOTA_RESUME_STATUSES（AVAILABLE / PRESSURE）且任务
         处于 waiting_quota / waiting_user → 恢复对账（RB-21-01）：
         逐 waiting_quota 单元按中断来源分类落点——
           - 无 runtime.quota_interrupted_from（legacy）或来源 ready →
             转 ready（向后兼容现行为）；
           - 来源 running → _reconcile_running_unit 四分对账：
             redispatch_clean → ready / reuse_result → verifying
             （§62 演进新边）/ resume_with_progress → ready /
             manual_ruling → 保持 waiting_quota；对账异常 fail-closed
             按 manual_ruling。成功落点的单元清除中断来源标记；
         任一 manual_ruling → 任务不转 executing（保持 waiting_quota、
         resumed=False，不落 quota_resumed）；否则任务转回 executing
         （waiting_quota → executing 与 waiting_user → executing 均为
         表内边）+ save + journal quota_resumed {status, source,
         unit_recovery} + manifest 刷新，返回 {"resumed": True, ...}；
      5. 其余情形（EXHAUSTED / UNKNOWN 保守等待；任务已不在等待态——
         如重复唤醒）零转态，返回 {"resumed": False, ...}。

    返回键：既有冻结四键 {"resumed", "status", "recommended_resume_at",
    "wake_budget_remaining"} 原样保留 + 新增 "unit_recovery"（RB-21-01：
    unit id → {"classification", ...按分类透传的 action_required /
    evidence / rationale}；非恢复分支为 {}）。recommended_resume_at
    仅在 wake 强刷后 EXHAUSTED 时给出 plan_resume 口径时刻，显式
    status 恒 None（不读缓存、不虚构）。
    """
    api = "resume_from_quota"
    st = _require_state(repo_root, task_id, api)
    source = "explicit"
    recommended = None
    if status is None:
        from runtime.quota import resolver  # 函数内 import：monkeypatch 友好
        resolved = resolver.resolve_quota_status(repo_root,
                                                 force_refresh=True)
        status = resolved["status"]
        source = resolved["source"]
        if status == "EXHAUSTED":
            # 强刷后的缓存 snapshot → evaluate → plan_resume（§30/§32
            # 统一规划口径；缓存不可读 → None 不虚构）
            recommended = _recommended_resume_at(
                _evaluation_from_refreshed_cache(repo_root))
    result = {
        "resumed": False,
        "status": status,
        "recommended_resume_at": recommended,
        "wake_budget_remaining": _wake_budget_remaining(st),
        "unit_recovery": {},
    }
    if status not in QUOTA_RESUME_STATUSES:
        # EXHAUSTED / UNKNOWN：保守等待，零转态（UNKNOWN 不虚构可用性）
        return result
    if st.get("status") not in ("waiting_quota", "waiting_user"):
        # 任务已不在额度等待态（重复唤醒 / 他处已恢复）：零转态幂等
        return result
    # —— 恢复对账：逐 waiting_quota 单元按中断来源分类落点 ——
    manual_ruling = False
    for unit in st.get("work_units") or []:
        if not isinstance(unit, dict) \
                or unit.get("status") != "waiting_quota":
            continue
        uid = unit.get("id")
        runtime_meta = unit.get("runtime")
        origin = (runtime_meta.get("quota_interrupted_from")
                  if isinstance(runtime_meta, dict) else None)
        if origin == "running":
            # running 中断单元：先 reconcile 对账再落点，禁止盲目 ready
            landing, entry = _reconcile_running_unit(
                repo_root, task_id, uid)
            if landing == "waiting_quota":
                manual_ruling = True
            else:
                work_unit.transition_work_unit(unit, landing)
                _clear_quota_interrupt_origin(unit)
            result["unit_recovery"][uid] = entry
        else:
            # legacy（无中断来源标记）或来源 ready：直回 ready
            # （向后兼容现行为）
            work_unit.transition_work_unit(unit, "ready")
            _clear_quota_interrupt_origin(unit)
    if manual_ruling:
        # 人工裁决未决：任务保持 waiting_quota（不派发），已对账单元的
        # 落点照常落盘；不落 quota_resumed / 不刷 manifest（无恢复事实）
        state.save_state(repo_root, st)
        return result
    st["status"] = "executing"
    state.save_state(repo_root, st)
    journal.append_event(repo_root, task_id, {
        "event": "quota_resumed", "status": status, "source": source,
        "unit_recovery": result["unit_recovery"]})
    _write_manifest_safe(repo_root, task_id)
    result["resumed"] = True
    result["wake_budget_remaining"] = _wake_budget_remaining(st)
    return result


# —— Persistent Wake Bridge（v2.2 M5，wu-22-05；决策记录 D15-a/b/c/d） ——

# D15-a 拓扑冻结：one tracked task → prefer one persistent automation
# identity（跨 quota window 复用同一条 automation；废除 per-window
# chained wake）。本节全部是规划 / 记账 / prompt 生成——runtime 绝不
# 调用 CronCreate/CronDelete/CronUpdate（宿主动作归主会话，Phase 0 #13
# 的会话级禁令由 M6 hooks 记账）。

# wake plan 认可「任务已启动且未终态」的任务状态族（task_active 判定；
# 比 QUOTA_WAIT_TASK_STATUSES 宽——含 waiting_quota / waiting_user 恢复
# 面：用户重新授权后的 eager-arm 正落在 waiting_user 任务上）。created
# 等前置态与终态一律不裁决建桥。
WAKE_PLAN_TASK_STATUSES = ("executing", "joining", "verifying", "reviewing",
                           "waiting_quota", "waiting_user")

# WB-04 幂等认可的「活桥」状态——同一条持久 automation 身份仍在服役
# （fired 非终态：recurring bridge 触发后继续按间隔服役）
REUSABLE_BRIDGE_STATUSES = ("armed", "fired")

# v2.2 C1b（修正计划 §22.5）：reconcile_wake_bridge_from_host 的
# host_status 词汇——宿主事实由调用方显式传入，本层绝不调用 CronList /
# 任何宿主探针（真正的 host adapter 归 C6）。active = 当前宿主证据确认
# automation 在役；deleted = 当前宿主证据确认 automation 已删除；
# completed = 调用方已知 automation / 任务已完结；unknown = 无宿主事实
# （绝不能据此改动账本——§22.5：journal / history alone MUST NOT
# manufacture an active bridge）。
WAKE_BRIDGE_HOST_FACTS = ("active", "deleted", "completed", "unknown")

# bridge_interval_minutes 的缺省值镜像（单一真相源是
# execution_policy.DEFAULT_QUOTA_CONTROL["bridge_interval_minutes"]；
# 经 default_quota_control 键级合并取得，本常量仅供 docstring 引用）
# ——D15-d：保守默认 60 分钟，overlap 未验证前不收紧到 30。


def _ensure_continuation(st) -> dict:
    """容错取/建 continuation 块：legacy 缺块按 default_continuation()
    补齐并写回 st（完整形状过 validate_state 闸）；已存在原样返回。
    只改内存 dict，落盘归调用方的 save_state。"""
    block = st.get("continuation")
    if not isinstance(block, dict):
        block = state.default_continuation()
        st["continuation"] = block
    return block


def _bridge_view(st) -> dict:
    """容错读 continuation.wake_bridge（legacy 缺块按默认块兜底，
    非法形状同样兜底——纠错归 validate_state）。"""
    bridge = _ensure_continuation(st).get("wake_bridge")
    return bridge if isinstance(bridge, dict) \
        else state.default_continuation()["wake_bridge"]


def _scheduler_context_view(st) -> dict:
    """容错读 continuation.scheduler_context（缺省 unknown，D15-c 分层
    的缺省解释入口）。"""
    ctx = _ensure_continuation(st).get("scheduler_context")
    return ctx if isinstance(ctx, dict) \
        else state.default_continuation()["scheduler_context"]


def _bridge_boundary(windows, grace_seconds):
    """D10 boundary 数学（纯函数）：多窗取最早可解析 reset。

    - windows：§27 窗口清单（list of dict；非 list / 坏窗逐项跳过）；
    - 逐窗 _parse_iso_utc(reset_at)，取最早时刻（并列取先出现者）；
    - boundary_id = "<kind>:<reset_at>"——reset 用 Z 形式归一串（幂等
      比较稳定：provider 换拼写不影响 WB-04 的同 boundary 判定）；
      kind 缺失按 "unknown"；
    - wake_at = reset + grace_seconds 的 Z 形式串（DEFAULT_GRACE_
      SECONDS=300 同源 scheduler，D10）。
    无可解析窗 → (None, None, None)——绝不虚构（§31）。
    """
    from runtime.quota import scheduler  # 函数内 import：monkeypatch 友好
    best = None  # (moment, kind)
    for item in (windows if isinstance(windows, list) else []):
        if not isinstance(item, dict):
            continue
        moment = scheduler._parse_iso_utc(item.get("reset_at"))
        if moment is None:
            continue
        if best is None or moment < best[0]:
            best = (moment, item.get("kind"))
    if best is None:
        return None, None, None
    moment, kind = best
    reset_z = scheduler._format_iso_z(moment)
    kind_text = kind if isinstance(kind, str) and kind != "" else "unknown"
    delta = datetime.timedelta(seconds=float(grace_seconds))
    return ("%s:%s" % (kind_text, reset_z), reset_z,
            scheduler._format_iso_z(moment + delta))


def universal_wake_prompt(task_id, *, ledger_root, repository_root=None,
                          boundary_id=None, reset_at=None, wake_at=None,
                          mode="recurring",
                          bridge_interval_minutes=None,
                          automation_id=None) -> str:
    """生成 Universal Wake prompt（§10 统一模板 + 今晚活桥逐字硬红线
    风格；纯函数，零 I/O 零凭证）。

    输入 task_id + 边界参数 → 文本；模板含三分支决策（goal 已达成 →
    tombstone + 单次清理；manual/notify → warm-only；auto 家族 →
    quota-resume 续作）、额度不可执行 no-op（不消费窗口预算，D15-g）、
    同桥复用语义（只复用/retarget 本 bridge，绝不新建）与硬红线
    （严禁创建任何新的 Scheduled Task / 零凭证 / 单次清理绝不重试）。

    主会话把它放进 automation 的 prompt 字段即可；automation 的创建 /
    暂停 / 删除本身归主会话的宿主工具（runtime 只产出文本与记账）。
    """
    work_root = repository_root if repository_root else ledger_root
    interval_text = ("%d 分钟" % bridge_interval_minutes
                     if isinstance(bridge_interval_minutes, int)
                     and not isinstance(bridge_interval_minutes, bool)
                     else "未配置")
    lines = [
        "GLM CONDUCTOR QUOTA WINDOW WAKE",
        "",
        "TASK_ID: %s" % task_id,
        "LEDGER_ROOT: %s" % ledger_root,
        "REPOSITORY_ROOT: %s" % work_root,
        "BOUNDARY_ID: %s" % (boundary_id if boundary_id else "unknown"),
        "EXPECTED_RESET_AT: %s" % (reset_at if reset_at else "unknown"),
        "WAKE_AT: %s" % (wake_at if wake_at else "按固定间隔触发"),
        "BRIDGE: %s（间隔 %s；复用同一 automation 身份%s，绝不新建）"
        % (mode, interval_text,
           " %s" % automation_id if automation_id else ""),
        "",
        "硬红线：本会话属于 scheduled task，严禁创建任何新的 Scheduled "
        "Task（宿主会拒绝且污染状态）；只允许复用本 bridge。本 prompt "
        "不含任何凭证。",
        "",
        "1. cd %s 后执行：" % ledger_root,
        "   python3 plugins/glm-conductor/runtime/cli.py quota-resolve "
        ". --force-refresh",
        "2. 读 .glm-conductor/tasks/%s/state.json 与 manifest.json；"
        % task_id,
        "   以仓库为真相源（repository > checkpoint > 记忆）",
        "3. 任务 goal 已达成（全部单元 completed）？",
        "   → 写 continuation.tombstone（task_id/status=completed/"
        "completed_at/bridge_should_noop=true）",
        "   → 单次尝试 CronDelete 本 automation（绝不重试，失败即容忍）",
        "   → 简短汇报后结束（不实施任何工作）",
        "4. 额度不可执行（仍耗尽 / weekly 阻塞 / provider 拒绝）？",
        "   → 什么都不做，简短说明后结束本轮（不消费窗口预算——D15-g）",
        "5. auto_resume=manual/notify（任务活跃且额度可执行）？",
        "   → warm-only：刷新额度状态、保持任务可恢复，绝不实施新工作，",
        "     汇报后结束",
        "6. 额度可执行 且 execution_policy.continuity.auto_resume ∈ "
        "{auto_once, until_done} 且 authorization.source=user？",
        "   → python3 plugins/glm-conductor/runtime/cli.py quota-resume "
        ". %s" % task_id,
        "   → 对账 reconcile（running 中断单元四分：clean→ready / "
        "result→verifying / progress→ready / manual_ruling→保持，"
        "勿盲目重派）",
        "   → 按依赖续派下一安全单元（不重放已完成工作）",
        "   → journal 记 wake_bridge_fired",
        "7. consumed_quota_windows 已达 max_quota_windows？",
        "   → 转 waiting_user，告知用户后结束",
        "8. 本轮一切 git 提交遵守：record（验证收据）与 finish_unit "
        "之间零提交",
        "",
        "恒久保护（Always preserve）：ownership / verification / "
        "review / lease / permit / reconcile / quota-window budget。",
        "不得为同一 reset boundary 创建第二座 wake——除非旧桥已确认"
        "失效；只复用或 retarget 本 bridge（reuse or retarget the "
        "existing bridge only）。",
        "",
        "HARD RED LINE (Phase 0 #13 / D15): DO NOT CREATE A NEW "
        "SCHEDULED TASK FROM THIS WAKE SESSION.",
    ]
    return "\n".join(lines)


def plan_wake_bridge(repo_root, task_id, *, provider_status=None,
                     windows=None, long_horizon=False,
                     cross_window_predicted=False,
                     grace_seconds=None) -> dict:
    """Persistent Wake Bridge 的 arm 裁决（v2.2 M5，wu-22-05；§7.2 /
    §22.2 / D15-a/b/c，纯决策——零写副作用、零 journal、零网络、绝不
    创建 automation）。

    入参形状（冻结）：
      - repo_root / task_id：账本根 + 任务 id——本函数只读
        state.json（缺失 TaskManagerError；损坏 ValueError 上抛）；
      - provider_status：provider 四态字符串；None → "UNKNOWN" 保守
        （绝不默认 AVAILABLE；与 M4 闸同源——调用方喂本地 quota-cache
        的 status，词汇校验归 control）；
      - windows：§27 窗口清单（调用方喂缓存 snapshot.windows）；None /
        缺失按空窗口 fail-open（control 收敛 PRESSURE、不虚构时刻）；
      - long_horizon / cross_window_predicted：D15-c auto_once 风险
        触发器中 runtime 无法机械观测的两项（long-horizon 任务 / 运行
        时预测当前窗口无法安全完成任务），由调用方显式声明，缺省
        False——M5 的机械触发面是 PRESSURE 相与 scheduler_context；
      - grace_seconds：wake_at 宽限秒数，None → DEFAULT_GRACE_SECONDS
        （300，scheduler 单一真相源，D10）。

    裁决（按序短路；reason 为中文一句话）：
      0. 任务 status ∉ WAKE_PLAN_TASK_STATUSES → required=False；
      1. auto 家族窗口预算耗尽（remaining <= 0）→ required=False
         （§14.5：不得再创建任何自动化唤醒，转 waiting_user）；
      2. WB-04 幂等：bridge.status ∈ ("armed","fired") → required=
         False——同 boundary 复用同桥；boundary 已推进则在 reason 注明
         「调用 retarget 记账（不新建 automation）」；
      3. scheduler_context.create=forbidden 且无 reusable bridge →
         required=False（Phase 0 #13；调用方走 degrade_continuity，
         reason=scheduler_create_forbidden）；
      4. D15-c 分层（create=forbidden 已被规则 3 拦下）：
         a. until_done + authorization.source=user → required=True、
            eager=True（MUST eager-arm：授权完成即建，不等 PRESSURE，
            无相位条件——BLOCKED 相的 interactive 会话救援建桥同此）；
            until_done + source != user → required=False（未授权）；
         b. BLOCKED → required=False（§7.2：bridge 必须早已 armed，
            不得依赖现场再建；未建走 degrade_continuity）；
         c. auto_once + source=user：DRAINING + create=allowed →
            MUST（required=True eager=False）；DRAINING + create=
            unknown → SHOULD（能力未探明保守不升 MUST）；任一风险
            触发（PRESSURE 相 / cross_window_predicted / long_horizon /
            会话已关联 Scheduled Task——scheduler_context.
            parent_automation_id 在位）→ SHOULD（required=True
            eager=False）；否则 False；source != user → False；
         d. manual/notify：DRAINING + create=allowed → MUST；
            DRAINING + create=unknown → SHOULD（能力未探明保守不升
            MUST）；PRESSURE → SHOULD；否则 False；
         e. 其余（NORMAL 无触发）→ required=False。
      5. required=True 时 prompt=universal_wake_prompt(...)（§10），
         否则 prompt=None。

    boundary 数学（D10）：多窗取最早可解析 reset，boundary_id =
    "<kind>:<reset_at>"（Z 形式归一）；wake_at = reset + 300 秒宽限；
    无可解析窗 → boundary_id / wake_at 双 None（不虚构）。mode 恒
    "recurring"（D15-b 主路径；self_retiming 仅 schema 常量占位）。
    bridge_interval_minutes 取 execution_policy.quota_control 键级
    合并（default_quota_control，默认 60）。

    返回（§22.2 冻结九键，键名逐字）：{"required", "mode",
    "boundary_id", "current_boundary_id", "wake_at",
    "bridge_interval_minutes", "eager", "prompt", "reason"}。

    时序（与宿主动作的分工）：本函数裁决 → 主会话宿主 CronCreate
    （runtime 绝不调用）→ arm_wake_bridge 记账即止——arm/fire/create
    一律不消费窗口预算（C1a，D15-g：automation lifecycle ≠ quota
    epoch consumption；消费点 = resume commit point §15.1，C1b 落地）。
    """
    api = "plan_wake_bridge"
    st = _require_state(repo_root, task_id, api)
    from runtime.quota import control, scheduler  # 函数内 import：monkeypatch 友好
    grace = (scheduler.DEFAULT_GRACE_SECONDS if grace_seconds is None
             else grace_seconds)
    if not scheduler._is_number(grace) or grace < 0:
        raise ValueError(
            "%s：grace_seconds 必须是 >= 0 的有限数值或 None，得到 %r"
            % (api, grace_seconds))
    bridge = _bridge_view(st)
    ctx = _scheduler_context_view(st)
    view = _continuity_view(st)
    policy = st.get("execution_policy")
    interval = default_quota_control(policy)["bridge_interval_minutes"]
    create = ctx.get("create")
    parent_automation = ctx.get("parent_automation_id")
    bridge_status = (bridge.get("status")
                     if bridge.get("status") in state.WAKE_BRIDGE_STATUSES
                     else "none")
    reusable = bridge_status in REUSABLE_BRIDGE_STATUSES
    boundary_id, reset_z, wake_at = _bridge_boundary(windows, grace)
    current_boundary_id = (bridge.get("current_boundary_id")
                           if isinstance(bridge.get("current_boundary_id"),
                                         str)
                           and bridge.get("current_boundary_id") != ""
                           else None)
    mode = "recurring"  # D15-b：主路径恒 recurring（D15-a 冻结拓扑）

    # execution phase（与 M4 闸同源的 control 决策器；provider 缺省
    # UNKNOWN 保守，绝不默认 AVAILABLE）
    phase_decision = control.evaluate_task_quota_phase(
        provider_status=(provider_status if provider_status is not None
                         else "UNKNOWN"),
        windows=windows,
        quota_control=default_quota_control(policy),
        max_workers=None, task_active=True,
        wake_bridge_status=bridge_status)
    phase = phase_decision["execution_phase"]

    def _plan(required, eager, reason):
        """组装 §22.2 冻结九键输出（prompt 仅在 required=True 时生成）。"""
        prompt = None
        if required:
            prompt = universal_wake_prompt(
                task_id, ledger_root=str(repo_root),
                repository_root=state.resolve_repository_root(
                    st, str(repo_root)),
                boundary_id=boundary_id, reset_at=reset_z, wake_at=wake_at,
                mode=mode, bridge_interval_minutes=interval,
                automation_id=bridge.get("automation_id"))
        return {
            "required": required,
            "mode": mode,
            "boundary_id": boundary_id,
            "current_boundary_id": current_boundary_id,
            "wake_at": wake_at,
            "bridge_interval_minutes": interval,
            "eager": eager,
            "prompt": prompt,
            "reason": reason,
        }

    # —— 规则 0：任务状态闸 ——
    if st.get("status") not in WAKE_PLAN_TASK_STATUSES:
        return _plan(
            False, False,
            "任务状态为 %r，不在 wake plan 的存活状态族（%s）内——不裁决"
            "建桥" % (st.get("status"),
                      ", ".join(WAKE_PLAN_TASK_STATUSES)))

    # —— 规则 1：auto 家族窗口预算闸（§14.5） ——
    if view["auto_resume"] in ("auto_once", "until_done") \
            and view["remaining"] <= 0:
        return _plan(
            False, False,
            "auto_resume=%r 的窗口预算已耗尽（consumed %d / max %d）——"
            "不得再创建任何自动化唤醒，转 waiting_user 等待用户重新授权"
            "（§14.5）" % (view["auto_resume"],
                           view["consumed_quota_windows"],
                           view["max_quota_windows"]))

    # —— 规则 2：WB-04 幂等（活桥复用 / retarget 记账，不新建） ——
    if reusable:
        if boundary_id is not None and current_boundary_id is not None \
                and boundary_id != current_boundary_id:
            return _plan(
                False, False,
                "wake bridge 已 armed（automation_id=%s）而 boundary 已"
                "推进（current=%s → next=%s）——recurring 同桥按间隔继续"
                "触发；调用 retarget_wake_bridge 记账更新 "
                "current_boundary_id，绝不新建 automation（WB-04 / "
                "D15-a）" % (bridge.get("automation_id"),
                             current_boundary_id, boundary_id))
        return _plan(
            False, False,
            "wake bridge 已 armed 且 boundary 未变（%s）——WB-04 幂等"
            "复用同桥（one tracked task → one persistent automation "
            "identity，D15-a），不新建" % (current_boundary_id or "未知"))

    # —— 规则 3：create 能力机械阻断（Phase 0 #13） ——
    if create == "forbidden":
        return _plan(
            False, False,
            "scheduler_context.create=forbidden 且无 reusable bridge"
            "（Phase 0 #13：本会话不得再创建 Scheduled Task）——调用 "
            "degrade_continuity 记账（reason=scheduler_create_forbidden），"
            "不裁决建桥")

    # —— 规则 4：D15-c 分层 ——
    auto_resume = view["auto_resume"]
    if auto_resume == "until_done" and view["source"] == "user":
        return _plan(
            True, True,
            "auto_resume=until_done 且 authorization.source=user——"
            "MUST eager-arm（授权完成即建 bridge，不等 PRESSURE；"
            "D15-c / §7.2）")
    if auto_resume == "until_done":
        return _plan(
            False, False,
            "auto_resume=until_done 未获得用户授权（authorization."
            "source != \"user\"），不自动建桥（与 _quota_wake_decision "
            "口径一致）")
    # —— 规则 4.5：BLOCKED 相不现场建桥（§7.2：bridge 必须早已 armed，
    # 不得依赖现场再建）——until_done 已在上方按无相位条件的 MUST
    # eager-arm 先行裁决（授权完成即建），此处覆盖 auto_once 与
    # manual/notify
    if phase == "BLOCKED":
        return _plan(
            False, False,
            "execution phase BLOCKED——bridge 必须早已 armed（不得依赖"
            "现场再建，§7.2）；未建则调用 degrade_continuity 记账降级，"
            "不无限阻塞")
    if auto_resume == "auto_once":
        if view["source"] != "user":
            return _plan(
                False, False,
                "auto_resume=auto_once 未获得用户授权（authorization."
                "source != \"user\"），不自动建桥（与 _quota_wake_decision"
                " 口径一致）")
        if phase == "DRAINING":
            if create == "allowed":
                return _plan(
                    True, False,
                    "auto_resume=auto_once 且 DRAINING 且 scheduler "
                    "create=allowed——MUST arm（D15-c / §7.2）")
            return _plan(
                True, False,
                "auto_resume=auto_once 且 DRAINING（create 能力未探明）"
                "——SHOULD arm（保守不升 MUST，arm 失败由 "
                "wake_bridge_failed 记账兜底）")
        triggers = []
        if phase == "PRESSURE":
            triggers.append("PRESSURE 相（执行压力带）")
        if cross_window_predicted:
            triggers.append("运行时预测当前窗口无法安全完成任务（跨窗）")
        if long_horizon:
            triggers.append("long-horizon 任务")
        if isinstance(parent_automation, str) and parent_automation != "":
            triggers.append("会话已关联 Scheduled Task（"
                            "parent_automation_id 在位）")
        if triggers:
            return _plan(
                True, False,
                "auto_resume=auto_once 风险触发（%s）——SHOULD arm"
                "（D15-c risk-triggered eager-arm）" % "；".join(triggers))
        return _plan(
            False, False,
            "auto_resume=auto_once 且无风险触发（PRESSURE / 跨窗预测 / "
            "long-horizon / 会话已关联 Scheduled Task 均未命中）——暂不"
            "建桥（D15-c）")
    if auto_resume in ("manual", "notify"):
        if phase == "DRAINING":
            if create == "allowed":
                return _plan(
                    True, False,
                    "auto_resume=%s 且 DRAINING 且 scheduler "
                    "create=allowed——MUST arm（mechanically possible，"
                    "D15-c / §7.2）" % auto_resume)
            return _plan(
                True, False,
                "auto_resume=%s 且 DRAINING（create 能力未探明）——SHOULD "
                "arm（保守不升 MUST）" % auto_resume)
        if phase == "PRESSURE":
            return _plan(
                True, False,
                "auto_resume=%s 且 PRESSURE——SHOULD arm（D15-c；触发后"
                "仅 warm-only，绑定的是任务跨 boundary 的可达性，§7.3）"
                % auto_resume)
        return _plan(
            False, False,
            "auto_resume=%s 且 execution phase %s 无建桥触发——暂不建桥"
            "（manual/notify 仅 PRESSURE→SHOULD / DRAINING+create="
            "allowed→MUST 建桥，D15-c）" % (auto_resume, phase))
    # 兜底（理论不可达：_continuity_view 把 auto_resume 归一为四值
    # 词汇，上面已全部覆盖；防御保留位）
    return _plan(
        False, False,
        "auto_resume=%r 不在授权矩阵动作面内且 execution phase %s 无"
        "触发——暂不建桥" % (auto_resume, phase))


def arm_wake_bridge(repo_root, task_id, *, automation_id, boundary_id,
                    reset_at, wake_at, next_wake_at=None,
                    bridge_interval_minutes=None, mode="recurring") -> dict:
    """bridge 武化记账（requested→armed；主会话宿主 CronCreate **成功
    后**调用，v2.2 M5 / D15-a / §9 生命周期）。

    时序（冻结）：plan_wake_bridge 裁决（required=True）→ 主会话宿主
    CronCreate（宿主动作，runtime 绝不调用）→ 本函数记账即止——
    arm/fire/create 一律不消费窗口预算（C1a，D15-g：automation
    lifecycle ≠ quota epoch consumption；消费点 = resume commit
    point §15.1，C1b 落地）。

    写入（continuation.wake_bridge 十字段 + obligation）：
      status="armed"、boundary_id / current_boundary_id=boundary_id、
      automation_id、reset_at / wake_at、armed_at=当前时刻、
      next_wake_at、mode、generation=0（recurring 恒 0，D15-b）、
      bridge_interval_minutes；continuation.obligation="armed"。

    冲突语义（先于任何 I/O 校验参数，失败零副作用）：
      - 已有活桥（status ∈ REUSABLE_BRIDGE_STATUSES）且同 automation_id
        同 boundary_id → 幂等 no-op（返回既有记账 + "idempotent": True，
        零写零事件）；
      - 已有活桥且同 boundary 不同 automation_id → TaskManagerError
        （D15-a：one tracked task → one persistent automation identity，
        拒绝第二 automation 身份）；
      - 已有活桥且 boundary 不同 → TaskManagerError（用
        retarget_wake_bridge 记账，不新建 automation，WB-04）。

    单次 save_state + journal wake_bridge_armed {automation_id,
    boundary_id, mode, bridge_interval_minutes, next_wake_at, wake_at,
    armed_at, recurring}。返回记账后的 bridge 关键字段 dict。

    参数校验：automation_id / boundary_id / reset_at / wake_at 必须是
    非空 str（reset_at / wake_at 须可解析 ISO8601），next_wake_at 为
    None 或可解析 ISO8601，bridge_interval_minutes 为 None 或 5-1440
    int（bool 拒绝），mode ∈ state.WAKE_BRIDGE_MODES——违例 ValueError
    （中文消息）。任务缺失 TaskManagerError。
    """
    api = "arm_wake_bridge"
    if not isinstance(automation_id, str) or automation_id == "":
        raise ValueError(
            "%s：automation_id 必须是非空字符串，得到 %r"
            % (api, automation_id))
    if not isinstance(boundary_id, str) or boundary_id == "":
        raise ValueError(
            "%s：boundary_id 必须是非空字符串（<kind>:<reset_at>，D10），"
            "得到 %r" % (api, boundary_id))
    from runtime.quota import scheduler  # 函数内 import：monkeypatch 友好
    if scheduler._parse_iso_utc(reset_at) is None:
        raise ValueError(
            "%s：reset_at 必须是可解析的 ISO8601 字符串，得到 %r"
            % (api, reset_at))
    if scheduler._parse_iso_utc(wake_at) is None:
        raise ValueError(
            "%s：wake_at 必须是可解析的 ISO8601 字符串，得到 %r"
            % (api, wake_at))
    if next_wake_at is not None \
            and scheduler._parse_iso_utc(next_wake_at) is None:
        raise ValueError(
            "%s：next_wake_at 必须是 None 或可解析的 ISO8601 字符串，"
            "得到 %r" % (api, next_wake_at))
    if bridge_interval_minutes is not None \
            and (isinstance(bridge_interval_minutes, bool)
                 or not isinstance(bridge_interval_minutes, int)
                 or not 5 <= bridge_interval_minutes <= 1440):
        raise ValueError(
            "%s：bridge_interval_minutes 必须是 None 或 5-1440 的整数，"
            "得到 %r" % (api, bridge_interval_minutes))
    if mode not in state.WAKE_BRIDGE_MODES:
        raise ValueError(
            "%s：mode %r 不在合法取值内（%s）"
            % (api, mode, ", ".join(state.WAKE_BRIDGE_MODES)))
    st = _require_state(repo_root, task_id, api)
    continuation = _ensure_continuation(st)
    bridge = continuation.get("wake_bridge")
    if not isinstance(bridge, dict):
        bridge = state.default_continuation()["wake_bridge"]
        continuation["wake_bridge"] = bridge
    if bridge.get("status") in REUSABLE_BRIDGE_STATUSES:
        if bridge.get("automation_id") == automation_id \
                and bridge.get("current_boundary_id") == boundary_id:
            result = {key: bridge.get(key) for key in
                      ("automation_id", "boundary_id", "current_boundary_id",
                       "reset_at", "wake_at", "next_wake_at", "mode",
                       "generation", "bridge_interval_minutes", "status",
                       "armed_at")}
            result["idempotent"] = True
            return result
        if bridge.get("current_boundary_id") == boundary_id:
            raise TaskManagerError(
                "%s：任务 %s 已有活桥指向同一 boundary（%s）但 automation "
                "身份不同（在位 %s，传入 %s）——D15-a：one tracked task → "
                "one persistent automation identity，拒绝第二 automation "
                "身份" % (api, task_id, boundary_id,
                          bridge.get("automation_id"), automation_id))
        raise TaskManagerError(
            "%s：任务 %s 已有活桥（automation_id=%s，current_boundary_id="
            "%s）指向不同 boundary（传入 %s）——用 retarget_wake_bridge "
            "记账更新 current_boundary_id，绝不新建 automation（WB-04）"
            % (api, task_id, bridge.get("automation_id"),
               bridge.get("current_boundary_id"), boundary_id))
    armed_at = _utc_now_iso()
    bridge["status"] = "armed"
    bridge["boundary_id"] = boundary_id
    bridge["current_boundary_id"] = boundary_id
    bridge["automation_id"] = automation_id
    bridge["reset_at"] = reset_at
    bridge["wake_at"] = wake_at
    bridge["armed_at"] = armed_at
    bridge["next_wake_at"] = next_wake_at
    bridge["mode"] = mode
    bridge["generation"] = 0  # recurring 恒 0（D15-b；世代推进非 M5 范围）
    bridge["bridge_interval_minutes"] = bridge_interval_minutes
    continuation["obligation"] = "armed"
    continuation["reason"] = "wake bridge armed（automation_id=%s）" \
        % automation_id
    state.save_state(repo_root, st)
    journal.append_event(repo_root, task_id, {
        "event": "wake_bridge_armed", "automation_id": automation_id,
        "boundary_id": boundary_id, "mode": mode,
        "bridge_interval_minutes": bridge_interval_minutes,
        "next_wake_at": next_wake_at, "wake_at": wake_at,
        "armed_at": armed_at, "recurring": mode == "recurring"})
    return {key: bridge.get(key) for key in
            ("automation_id", "boundary_id", "current_boundary_id",
             "reset_at", "wake_at", "next_wake_at", "mode", "generation",
             "bridge_interval_minutes", "status", "armed_at")}


def record_bridge_fired(repo_root, task_id, *, fired_at=None) -> dict:
    """bridge 触发记账（armed→fired；recurring 重复触发合法——每次触发
    各落一条事件并刷新 fired_at，v2.2 M5 / §9 生命周期）。

    - bridge.status 须 ∈ ("armed","fired")，否则 TaskManagerError
      （无桥 / 已取消 / 已失效的触发记录被拒）；
    - fired_at：None → 当前时刻（ISO8601）；显式传入须可解析；
    - 写 wake_bridge.status="fired" + fired_at（recurring 桥身份继续
      服役，不改 automation 记账）；obligation 不动（恢复裁决归
      resume controller，M8）；
    - 单次 save_state + journal wake_bridge_fired {automation_id,
      fired_at, boundary_id}。返回 bridge 关键字段 dict。
    """
    api = "record_bridge_fired"
    from runtime.quota import scheduler  # 函数内 import：monkeypatch 友好
    if fired_at is not None and scheduler._parse_iso_utc(fired_at) is None:
        raise ValueError(
            "%s：fired_at 必须是 None 或可解析的 ISO8601 字符串，得到 %r"
            % (api, fired_at))
    st = _require_state(repo_root, task_id, api)
    bridge = _bridge_view(st)
    if bridge.get("status") not in REUSABLE_BRIDGE_STATUSES:
        raise TaskManagerError(
            "%s：任务 %s 的 wake_bridge.status 为 %r，须为 armed（或 "
            "fired——recurring 重复触发合法）才能记录触发"
            % (api, task_id, bridge.get("status")))
    moment = fired_at if fired_at is not None else _utc_now_iso()
    bridge["status"] = "fired"
    bridge["fired_at"] = moment
    state.save_state(repo_root, st)
    journal.append_event(repo_root, task_id, {
        "event": "wake_bridge_fired", "automation_id":
            bridge.get("automation_id"), "fired_at": moment,
        "boundary_id": bridge.get("current_boundary_id")})
    return {key: bridge.get(key) for key in
            ("automation_id", "status", "fired_at", "boundary_id",
             "mode", "generation")}


def retarget_wake_bridge(repo_root, task_id, *, boundary_id,
                         reset_at=None, wake_at=None,
                         next_wake_at=None) -> dict:
    """bridge 重定目标记账（boundary 变更 / 时刻重排；v2.2 M5 / WB-04：
    只更新同一条 automation 的记账，绝不新建）。

    - bridge.status 须 ∈ ("armed","fired")（无活桥不可 retarget）；
    - boundary_id 更新的是 current_boundary_id（当前服役 boundary）；
      既有 boundary_id（armed 时的原始目标）保留不动；
    - reset_at / wake_at / next_wake_at：None = 不更新；显式传入须
      可解析 ISO8601；
    - generation 不增不减（recurring 模式恒 0，D15-b；self_retiming
      的世代推进不在 M5 范围）；
    - 幂等：boundary 未变且三个时刻参数全 None → 零写零事件，返回
      既有记账 + "idempotent": True；
    - 单次 save_state + journal wake_bridge_retargeted {automation_id,
      from_boundary_id, to_boundary_id, mode, generation}。
    """
    api = "retarget_wake_bridge"
    if not isinstance(boundary_id, str) or boundary_id == "":
        raise ValueError(
            "%s：boundary_id 必须是非空字符串（<kind>:<reset_at>，D10），"
            "得到 %r" % (api, boundary_id))
    from runtime.quota import scheduler  # 函数内 import：monkeypatch 友好
    for field, value in (("reset_at", reset_at), ("wake_at", wake_at),
                         ("next_wake_at", next_wake_at)):
        if value is not None and scheduler._parse_iso_utc(value) is None:
            raise ValueError(
                "%s：%s 必须是 None 或可解析的 ISO8601 字符串，得到 %r"
                % (api, field, value))
    st = _require_state(repo_root, task_id, api)
    bridge = _bridge_view(st)
    if bridge.get("status") not in REUSABLE_BRIDGE_STATUSES:
        raise TaskManagerError(
            "%s：任务 %s 的 wake_bridge.status 为 %r——无活桥可 retarget"
            "（先 arm_wake_bridge 建置）" % (api, task_id,
                                            bridge.get("status")))
    old_current = bridge.get("current_boundary_id")
    touched = []
    if boundary_id != old_current:
        bridge["current_boundary_id"] = boundary_id
        touched.append("current_boundary_id")
    for field, value in (("reset_at", reset_at), ("wake_at", wake_at),
                         ("next_wake_at", next_wake_at)):
        if value is not None:
            bridge[field] = value
            touched.append(field)
    result = {key: bridge.get(key) for key in
              ("automation_id", "boundary_id", "current_boundary_id",
               "reset_at", "wake_at", "next_wake_at", "mode", "generation",
               "bridge_interval_minutes", "status")}
    if not touched:
        result["idempotent"] = True
        return result
    state.save_state(repo_root, st)
    journal.append_event(repo_root, task_id, {
        "event": "wake_bridge_retargeted",
        "automation_id": bridge.get("automation_id"),
        "from_boundary_id": old_current, "to_boundary_id": boundary_id,
        "mode": bridge.get("mode"), "generation": bridge.get("generation")})
    return result


def reconcile_wake_bridge_from_host(repo_root, task_id, *, host_status,
                                    observed_at=None) -> dict:
    """手动 / 历史 bridge 对账（v2.2 C1b；修正计划 §22.5，2026-09-02
    锁定）——纯账本 helper。

    硬边界：
      - 零宿主调用：host_status 由调用方显式传入（真正的 host
        adapter / CronList 探针归 C6）——本函数不 import 任何宿主 /
        CronList 概念、零网络；
      - 对账只能降级 / 确认，绝不制造 armed：§22.5「journal / history
        alone MUST NOT manufacture an active bridge」——既有记账已是
        armed / fired 且宿主事实为 active 时仅原地确认（零写）；任何
        非 armed 记账一律不升级（重新 arm 走 arm_wake_bridge，归当前
        session capability 裁决——clean interactive session 才可）。

    host_status ∈ WAKE_BRIDGE_HOST_FACTS：
      - "active"：宿主证据确认 automation 在役——armed / fired 记账
        确认（零写零事件）；其余状态零写上报（不升级）；
      - "deleted"：宿主证据确认 automation 已删除——armed / fired 记
        账降级 status="cancelled"（§22.5：已知已删除的 bridge 记
        cancelled——保留 automation_id / fired_at / 既有 boundary 记
        账，不得显示为 active transport）；
      - "completed"：automation / 任务已完结——armed / fired 记账降
        级 status="stale"（§22.5：completed 的 bridge 记 stale）；
      - "unknown"：无宿主事实——零写上报（不能确认也不能改动）。

    仅对 active transport 记账（status ∈ REUSABLE_BRIDGE_STATUSES，
    即 armed / fired）降级；requested / paused / degraded 等意愿态与
    中间态不归本 helper 处置（各自生命周期另有入口）。降级幂等：记账
    已是目标态（cancelled 对 deleted / stale 对 completed）→ 零写返
    回并增标 "idempotent": True。obligation / tombstone 不动——
    transport missing 之后「重新 arm 还是降级 SessionStart 兜底」由
    当前 session capability 裁决（§22.5 实施分工，C6 落地）。

    落盘：仅实际降级路径单次 save_state + journal wake_bridge_reconciled
    {host_status, from_status, to_status, automation_id, observed_at}
    （host 事实与 last known state 随事件留痕）。确认 / no-op /
    unknown 路径零写零事件。返回 {"task_id", "host_status",
    "bridge_status_before", "bridge_status", "automation_id",
    "activation_transport", "reconciled", "observed_at"}——
    activation_transport ∈ {"armed", "missing"}（按降级后 status 是否
    ∈ REUSABLE_BRIDGE_STATUSES；§22.5：bridge 已删而 task 仍 active →
    missing）。

    参数校验（先于任何 I/O，失败零副作用）：host_status ∈
    WAKE_BRIDGE_HOST_FACTS；observed_at 为 None 或可解析 ISO8601——
    违例 ValueError（中文消息）。任务缺失 TaskManagerError。
    """
    api = "reconcile_wake_bridge_from_host"
    if host_status not in WAKE_BRIDGE_HOST_FACTS:
        raise ValueError(
            "%s：host_status %r 不在合法取值内（%s）"
            % (api, host_status, ", ".join(WAKE_BRIDGE_HOST_FACTS)))
    from runtime.quota import scheduler  # 函数内 import：monkeypatch 友好
    if observed_at is not None \
            and scheduler._parse_iso_utc(observed_at) is None:
        raise ValueError(
            "%s：observed_at 必须是 None 或可解析的 ISO8601 字符串，"
            "得到 %r" % (api, observed_at))
    st = _require_state(repo_root, task_id, api)
    bridge = _bridge_view(st)
    before = bridge.get("status")
    automation_id = bridge.get("automation_id")
    moment = observed_at if observed_at is not None else _utc_now_iso()
    target = None
    if host_status == "deleted":
        target = "cancelled"
    elif host_status == "completed":
        target = "stale"
    reconciled = False
    idempotent = False
    if target is not None and before in REUSABLE_BRIDGE_STATUSES:
        # 降级路径：只改 status，automation_id / fired_at / boundary
        # 记账全保留（§22.5：last fired / last known host state 留痕）
        bridge["status"] = target
        state.save_state(repo_root, st)
        journal.append_event(repo_root, task_id, {
            "event": "wake_bridge_reconciled", "host_status": host_status,
            "from_status": before, "to_status": target,
            "automation_id": automation_id, "observed_at": moment})
        reconciled = True
    elif target is not None and before == target:
        idempotent = True  # 目标态已达成（重复对账）
    return {
        "task_id": task_id,
        "host_status": host_status,
        "bridge_status_before": before,
        "bridge_status": bridge.get("status"),
        "automation_id": automation_id,
        "activation_transport": ("armed" if bridge.get("status")
                                 in REUSABLE_BRIDGE_STATUSES
                                 else "missing"),
        "reconciled": reconciled,
        "observed_at": moment,
        **({"idempotent": True} if idempotent else {}),
    }


def write_completion_tombstone(repo_root, task_id, *,
                               completed_at=None) -> dict:
    """写 bridge 退役墓碑（D15-d 冻结四键；goal 已达成时由完成门 /
    wake 会话调用，ghost bridge 据此转 cheap no-op）。

    - continuation.tombstone = {"task_id", "status": "completed",
      "completed_at", "bridge_should_noop": True}——四键形状由
      state.validate_state 闸背书；首块墓碑恒胜：已存在 → 幂等 no-op
      （返回既有墓碑 + "idempotent": True，零写）；
    - completed_at：None → 当前时刻；显式传入须可解析 ISO8601；
    - 单次 save_state；**不落 journal**——词汇表中无 tombstone 事件，
      墓碑本身（state.json continuation.tombstone）即持久记录，wake
      会话按 bridge_should_noop=true 直接 no-op（D15-d 第 3/4 层缓解；
      单次 CronDelete 清理尝试归主会话宿主动作，绝不重试）。
    返回墓碑 dict（含 idempotent 标记当命中幂等）。
    """
    api = "write_completion_tombstone"
    from runtime.quota import scheduler  # 函数内 import：monkeypatch 友好
    if completed_at is not None \
            and scheduler._parse_iso_utc(completed_at) is None:
        raise ValueError(
            "%s：completed_at 必须是 None 或可解析的 ISO8601 字符串，"
            "得到 %r" % (api, completed_at))
    st = _require_state(repo_root, task_id, api)
    continuation = _ensure_continuation(st)
    existing = continuation.get("tombstone")
    if isinstance(existing, dict):
        result = dict(existing)
        result["idempotent"] = True
        return result
    moment = completed_at if completed_at is not None else _utc_now_iso()
    tombstone = {
        "task_id": task_id,
        "status": "completed",
        "completed_at": moment,
        "bridge_should_noop": True,
    }
    continuation["tombstone"] = tombstone
    state.save_state(repo_root, st)
    return dict(tombstone)


def degrade_continuity(repo_root, task_id, *,
                       reason="scheduler_create_forbidden") -> dict:
    """连续性降级记账（create 不可用且无 reusable bridge →
    obligation=degraded；D15-c / §12.3：不得无限 Stop block）。

    - reason 缺省 "scheduler_create_forbidden"（Phase 0 #13 的机械
      降级原因）；非空 str 校验；
    - 写 continuation.obligation="degraded" + continuation.reason=
      reason；wake_bridge.status 不动（降级的是连续性，不是桥本身）；
    - 幂等：obligation 已为 degraded → 零写零事件，返回
      "idempotent": True；
    - 单次 save_state + journal continuity_degraded {reason}。
    """
    api = "degrade_continuity"
    if not isinstance(reason, str) or reason == "":
        raise ValueError(
            "%s：reason 必须是非空字符串，得到 %r" % (api, reason))
    st = _require_state(repo_root, task_id, api)
    continuation = _ensure_continuation(st)
    if continuation.get("obligation") == "degraded":
        return {"obligation": "degraded",
                "reason": continuation.get("reason"), "idempotent": True}
    continuation["obligation"] = "degraded"
    continuation["reason"] = reason
    state.save_state(repo_root, st)
    journal.append_event(repo_root, task_id, {
        "event": "continuity_degraded", "reason": reason})
    return {"obligation": "degraded", "reason": reason}


# —— Quota Subscription（v2.2 C5a，wu-22-C5a；主计划 §14 规范 adapted） ——

# §14 「Task 只订阅 quota，不再拥有 quota clock」的执行面落点：订阅事实
# 记在任务 state 顶层 quota_subscription 块（形状真相源是 state.py 的
# DEFAULT_QUOTA_SUBSCRIPTION / 规则 8.9 validator），本节提供三个事务
# API（注册 / 纯资格判定 / 激活记账）。
#
# 规格适配（§14 草图 → 本实现）：§14 草图的 registered_epoch:17 是 int
# 序数示意——epoch 身份一律用 §10.1 epoch_id 字符串（"glm:"+指纹前
# 16 hex，C2 冻结：fingerprint 等值比较、与窗口顺序无关、durable 重建
# 安全），新旧判定只用字符串等值，不引入任何 int 序数。
#
# 词汇：minimum_state / continuation_mode 两枚举与「供比较的档位序」
# 的单一真相源在 state.py（QUOTA_SUBSCRIPTION_MINIMUM_STATES /
# QUOTA_SUBSCRIPTION_CONTINUATION_MODES / is_quota_epoch_id），本节只
# 消费不复制。

# minimum_state 档位序的本地冻结映射（QC-07 判定用；序见
# QUOTA_SUBSCRIPTION_MINIMUM_STATES 注释——索引越小档位越高，
# AVAILABLE > PRESSURE > DRAINING > EXHAUSTED。provider_status 达到
# minimum_state 档位及以上 = rank(provider_status) <=
# rank(minimum_state)）。
_QUOTA_SUBSCRIPTION_STATE_RANK = {
    name: rank for rank, name in enumerate(state.QUOTA_SUBSCRIPTION_MINIMUM_STATES)
}

# register_quota_subscription 返回冻结六键（块五键视图 + idempotent）
QUOTA_SUBSCRIPTION_RESULT_KEYS = (
    "enabled", "registered_epoch_id", "last_activation_epoch_id",
    "minimum_state", "continuation_mode", "idempotent")


def _quota_subscription_view(st) -> dict:
    """容错读任务的 quota_subscription 块（legacy 缺块 / 形状异常按
    default_quota_subscription() 兜底——纠错归 validate_state 规则 8.9）。
    纯读：不改入参、零 I/O。"""
    block = st.get("quota_subscription")
    if not isinstance(block, dict):
        return state.default_quota_subscription()
    return block


def _subscription_state_satisfied(provider_status, minimum_state) -> bool:
    """provider_status 是否达到 minimum_state 档位及以上（纯函数）。

    档位序本地冻结（_QUOTA_SUBSCRIPTION_STATE_RANK 注释）：任一方不在
    四档词汇内（含观测面 UNKNOWN——订阅阈值词汇与观测四态不同，绝不
    猜测折算）→ False（保守不满足，§31 不虚构）。
    """
    rank = _QUOTA_SUBSCRIPTION_STATE_RANK.get(provider_status)
    minimum = _QUOTA_SUBSCRIPTION_STATE_RANK.get(minimum_state)
    if rank is None or minimum is None:
        return False
    return rank <= minimum


def register_quota_subscription(repo_root, task_id, *, epoch_id,
                                minimum_state="AVAILABLE",
                                continuation_mode=None) -> dict:
    """注册 / 更新任务的 quota subscription（§14；幂等）。

    写顶层 quota_subscription 块：enabled=True、registered_epoch_id=
    epoch_id、minimum_state、continuation_mode（五键冻结形状，过
    validate_state 规则 8.9 闸落盘）。last_activation_epoch_id 不由本
    API 触碰（激活记账归 mark_activation_epoch）。

    幂等语义（冻结口径）：块已存在且五键目标值全部相等（含经镜像解析
    后的 continuation_mode）→ 零状态写、零 journal 事件，返回当前块
    视图 + "idempotent": True；否则（无块建块 / 任一键值变化）写块 +
    任务 journal 一条 quota_subscription_registered 事件
    {registered_epoch_id, minimum_state, continuation_mode}，返回
    "idempotent": False。

    参数（校验先于任何 I/O，中文 ValueError）：
      - epoch_id：§10.1 形状（"glm:"+16hex，state.is_quota_epoch_id）；
      - minimum_state：∈ state.QUOTA_SUBSCRIPTION_MINIMUM_STATES，缺省
        "AVAILABLE"；
      - continuation_mode：None（缺省）→ 从任务 execution_policy.
        continuity.auto_resume 容错镜像读（_continuity_view——缺块 /
        词汇外保守落 "manual"，§14.2-§14.5 同一事实源）；显式传入须
        ∈ state.QUOTA_SUBSCRIPTION_CONTINUATION_MODES。

    任务缺失 → TaskManagerError；JSON 损坏 ValueError 上抛。
    返回冻结六键 dict（QUOTA_SUBSCRIPTION_RESULT_KEYS）。
    """
    api = "register_quota_subscription"
    if not state.is_quota_epoch_id(epoch_id):
        raise ValueError(
            "%s：epoch_id 必须是 \"glm:\"+16 位十六进制的 epoch_id"
            "（§10.1 形状），得到 %r" % (api, epoch_id))
    if minimum_state not in state.QUOTA_SUBSCRIPTION_MINIMUM_STATES:
        raise ValueError(
            "%s：minimum_state %r 不在合法取值内（%s）"
            % (api, minimum_state,
               ", ".join(state.QUOTA_SUBSCRIPTION_MINIMUM_STATES)))
    if continuation_mode is not None \
            and continuation_mode \
            not in state.QUOTA_SUBSCRIPTION_CONTINUATION_MODES:
        raise ValueError(
            "%s：continuation_mode %r 不在合法取值内（%s）"
            % (api, continuation_mode,
               ", ".join(state.QUOTA_SUBSCRIPTION_CONTINUATION_MODES)))
    st = _require_state(repo_root, task_id, api)
    if continuation_mode is None:
        # §14：缺省镜像执行策略的续跑授权词汇（同一事实源，不复制默认）
        continuation_mode = _continuity_view(st)["auto_resume"]
    block = st.get("quota_subscription")
    target = {
        "enabled": True,
        "registered_epoch_id": epoch_id,
        "last_activation_epoch_id": (
            block.get("last_activation_epoch_id")
            if isinstance(block, dict) else None),
        "minimum_state": minimum_state,
        "continuation_mode": continuation_mode,
    }
    if isinstance(block, dict) and all(
            block.get(key) == value for key, value in target.items()):
        # 同参重注册：零写零事件（幂等返回；返回块视图拷贝，不改 st）
        result = _quota_subscription_view(st)
        result = dict(result)
        result["idempotent"] = True
        return result
    st["quota_subscription"] = target
    state.save_state(repo_root, st)
    journal.append_event(repo_root, task_id, {
        "event": "quota_subscription_registered",
        "registered_epoch_id": epoch_id,
        "minimum_state": minimum_state,
        "continuation_mode": continuation_mode})
    result = dict(target)
    result["idempotent"] = False
    return result


def evaluate_subscription_eligibility(repo_root, task_id, *,
                                      current_epoch_id,
                                      current_executable,
                                      provider_status) -> dict:
    """纯资格判定：本任务当前 epoch 是否有资格激活（§14 eligible）。

    纯函数纪律：只读 state.json，零转态、零写、零事件、零网络——
    派发 / 恢复的编排方在 resume 决策前调用，结果只供裁决。**不做
    authorization / budget / reconcile**（§14 执行面流水线中授权 / 预算
    / 对账各归其位，接线归 C7），本函数只回答订阅本身的资格。

    判定矩阵（全部满足才 eligible；不短路——reasons 逐条点名全部未过
    条件，审计面风格与 primer.authorize_prime 同源）：
      1. 已注册：块存在且 registered_epoch_id 非 null；
      2. 已启用：enabled 为 True；
      3. 新 epoch：current_epoch_id != last_activation_epoch_id
         （字符串等值比较——§14 adapted：epoch_id 是 §10.1 指纹身份，
         无 int 序数可比；last_activation 为 null = 从未激活 → 通过）；
      4. 可执行：current_executable 为真（§10.1 evaluate_epoch 的
         executable 判定结果，由调用方传入，本层不重复折算）；
      5. 阈值满足：provider_status 达到 minimum_state 档位及以上
         （四档序 AVAILABLE > PRESSURE > DRAINING > EXHAUSTED，
         _subscription_state_satisfied；词汇外值保守不满足）。

    返回冻结两键：{"eligible": bool, "reasons": [中文原因...]}。
    任务缺失 → TaskManagerError；current_epoch_id 形状非法 → ValueError
    （先于 I/O 校验；形状闸与注册口径一致，防调用方拿序数/任意串
    充当 epoch 身份）。
    """
    api = "evaluate_subscription_eligibility"
    if not state.is_quota_epoch_id(current_epoch_id):
        raise ValueError(
            "%s：current_epoch_id 必须是 \"glm:\"+16 位十六进制的 "
            "epoch_id（§10.1 形状），得到 %r" % (api, current_epoch_id))
    st = _require_state(repo_root, task_id, api)
    block = _quota_subscription_view(st)
    reasons = []
    if block.get("registered_epoch_id") is None:
        reasons.append("任务 %s 未注册 quota subscription"
                       "（registered_epoch_id 为空）" % task_id)
    if block.get("enabled") is not True:
        reasons.append("quota subscription 未启用（enabled != true）")
    if block.get("last_activation_epoch_id") == current_epoch_id:
        reasons.append(
            "epoch %s 已激活过（last_activation_epoch_id 相同）——QC-07 "
            "每 epoch 恰一次，同 epoch 不得重复激活" % current_epoch_id)
    if not current_executable:
        reasons.append("当前 epoch 不可执行（current_executable != true）")
    minimum_state = block.get("minimum_state")
    if not _subscription_state_satisfied(provider_status, minimum_state):
        reasons.append(
            "provider_status %r 未达到 minimum_state %r 档位及以上"
            "（%s）" % (provider_status, minimum_state,
                        " > ".join(state.QUOTA_SUBSCRIPTION_MINIMUM_STATES)))
    return {"eligible": not reasons, "reasons": reasons}


def mark_activation_epoch(repo_root, task_id, *, epoch_id) -> dict:
    """记录任务在本 epoch 已激活（QC-07：每 epoch 恰一次推进记账）。

    写 quota_subscription.last_activation_epoch_id = epoch_id + 控制
    面 journal 一条 quota_epoch_advanced 事件（经
    journal.append_control_plane_event 落
    .glm-conductor/quota/events.jsonl——epoch 推进无任务上下文也可
    发生，控制面事件不进任务 journal、不借伪任务目录）。

    幂等语义（冻结口径；QC-07 的机械保证）：
      - epoch_id 与本任务 last_activation_epoch_id 等值（同值重入；
        null 不等任何合法 epoch_id，故首标必推进）→ 零状态写、零
        控制面事件，返回 "marked": False + "idempotent": True；
      - epoch_id 不同（含首次从 null 起标）→ 视为推进（epoch_id 是
        §10.1 指纹等值身份，无序数、不分前进后退）→ 更新 + 恰一条
        事件，返回 "marked": True + "idempotent": False。

    事件字段（控制面 journal）：{"event": "quota_epoch_advanced",
    "task_id", "epoch_id", "previous_epoch_id"}。落盘顺序：先 state
    后事件；事件写入失败（OSError）自然上抛不吞——QC-07 证据丢失
    必须让调用方看见（绝不静默降级）。

    参数：epoch_id 须 §10.1 形状（先于 I/O 校验，ValueError）。
    任务缺失 → TaskManagerError。返回冻结三键
    {"marked", "last_activation_epoch_id", "idempotent"}。
    """
    api = "mark_activation_epoch"
    if not state.is_quota_epoch_id(epoch_id):
        raise ValueError(
            "%s：epoch_id 必须是 \"glm:\"+16 位十六进制的 epoch_id"
            "（§10.1 形状），得到 %r" % (api, epoch_id))
    st = _require_state(repo_root, task_id, api)
    block = _quota_subscription_view(st)
    previous = block.get("last_activation_epoch_id")
    if previous == epoch_id:
        # 同 epoch 重标：零写零事件（QC-07 每 epoch 恰一次的幂等闸）
        return {"marked": False,
                "last_activation_epoch_id": previous,
                "idempotent": True}
    if not isinstance(st.get("quota_subscription"), dict):
        # legacy 缺块：按默认块落位再记账（enabled/registered 不由本
        # API 触碰——订阅与否归 register_quota_subscription）
        st["quota_subscription"] = state.default_quota_subscription()
    st["quota_subscription"]["last_activation_epoch_id"] = epoch_id
    state.save_state(repo_root, st)
    journal.append_control_plane_event(repo_root, {
        "event": "quota_epoch_advanced",
        "task_id": task_id,
        "epoch_id": epoch_id,
        "previous_epoch_id": previous})
    return {"marked": True,
            "last_activation_epoch_id": epoch_id,
            "idempotent": False}
