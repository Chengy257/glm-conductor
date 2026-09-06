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

订阅接线（v2.2 C5b，wu-22-C5b；C5a 三 API 的消费面）：
    把订阅资格接进 EXHAUSTED 生命周期的两个转态点——
      - handle_quota_exhausted：转态成功后 best-effort 注册订阅（epoch
        身份用转态时点缓存 snapshot 的零网络折算；折算失败不注册零副
        作用——legacy 路径不变；注册失败不阻断转态，订阅是增强面）；
      - resume_from_quota：已注册且启用的任务在恢复裁决前过订阅资格
        门（epoch 折算 → 崩溃窗口对账 → evaluate_subscription_
        eligibility）；不 eligible（或 epoch 无法折算）→ 不转态保守
        等待；资格过 → 既有转态照走 + mark_activation_epoch。**转态-
        记账顺序冻结为 evaluate → 转态 → mark**（先 mark 后转态会在
        崩溃时卡死同 epoch 恢复，理由见 resume_from_quota docstring）；
        未注册 / 未启用任务输出零变化（不加 subscription 键）。
    消费零接线不变：resume_from_quota 绝不调用
    record_quota_boundary_consumed（消费接线归 Resume Controller）。

QuotaIdentity 复合身份（v2.2.1 WU-221-B2，六个记账面的统一升级）：
    epoch 身份从裸 epoch_id 升级为 QuotaIdentity =
    (provider_identity_hash, epoch_id)——同名 epoch 在不同 provider
    身份下互不相认（A 的 epoch E 不授权 / 不幂等拦截 / 不代替 B 的
    E）。判定的唯一共享落点 =
    runtime.quota.epoch.quota_identity_matches（双形态：记录侧指纹
    缺席 = v2.2 legacy = 保守信任，present-and-different = 异身份 =
    按各面既有的「无先前记录」路径处理）。本模块各面落点：
      - 订阅注册（register_quota_subscription）：块与事件可选携带
        provider_identity_hash（调用点身份可得才写，None = legacy
        形态省略，绝不为此解析凭证）；幂等比较仍只比五规范键；
      - 订阅资格（evaluate_subscription_eligibility）：块的注册指纹
        异于当前身份 → 不 eligible（条件 2b）；QC-07 同 epoch 判定
        升为双形态（quota_identity_matches）；
      - 激活记账（mark_activation_epoch）：QC-07 幂等闸双形态——
        同 epoch 同身份零写零事件；异身份同名 epoch 是新事件（B 的
        首次激活恰一条新控制面事件，事件与块注记携带当前指纹）；
      - 崩溃对账（_reconcile_activation_journal）：journal 证据匹配
        双形态——异身份的 quota_epoch_advanced 不作 QC-07 证据；
      - 消费记账（record_quota_boundary_consumed）：幂等 / RH-03
        pending 扫描双形态——A 对 E 的消费不满足也不拦截 B 的 E，
        B 按自身身份记账；新事件携带当前指纹；
      - 直接读者身份闸（_cache_identity_usable + b2-review
        carryover 收口）：_execution_phase_decision /
        _evaluation_from_refreshed_cache / _epoch_context_at_
        transition 三个绕过 resolver 层级的 _load_cache 消费点补上
        resolver（WU-221-B1）同款身份检查——异身份缓存视同无缓存
        （各走既有 no-cache 路径），legacy 无指纹缓存保守信任。
    epoch_id 格式零变化（"glm:"+16hex，§10.1 冻结口径）；指纹为
    非秘密 16-hex（runtime.quota.identity 共享派生，不含凭证材料
    ——可落 journal / state，绝不落日志的是凭证本体，§37）。

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
# v2.2.1 WU-221-C1（行为保持抽取）：quota 消费/迁移记账域的规范定义
# 已移至 runtime.quota.accounting（依赖方向冻结：task_manager →
# accounting，绝不反向）；以下 re-import 保证既有 task_manager.<名字>
# 解析点（测试 / hooks / runtime 内部调用方）零变化。
from runtime.quota.accounting import (
    TaskManagerError,
    _continuity_view,
    _current_provider_identity_hash,
    _require_state,
    _utc_now_iso,
    migrate_quota_window_accounting,
    record_quota_boundary_consumed,
)

# v2.2.1 WU-221-C2（行为保持抽取）：Persistent Wake Bridge 规划/记账域
# 的规范定义已移至 runtime.continuity.wake_bridge（依赖方向冻结：
# task_manager → wake_bridge，绝不反向）；以下 re-import 保证既有
# task_manager.<名字> 解析点（activation_transport / cli / hooks /
# tests）零变化。
from runtime.continuity.wake_bridge import (
    REUSABLE_BRIDGE_STATUSES,
    WAKE_BRIDGE_HOST_FACTS,
    WAKE_PLAN_TASK_STATUSES,
    _bridge_boundary,
    _bridge_view,
    _ensure_continuation,
    _scheduler_context_view,
    arm_wake_bridge,
    degrade_continuity,
    plan_wake_bridge,
    reconcile_wake_bridge_from_host,
    record_bridge_fired,
    retarget_wake_bridge,
    universal_wake_prompt,
    write_completion_tombstone,
)

# v2.2.1 WU-221-C1（行为保持抽取）：quota SUBSCRIPTION 订阅域的规范定义
# 已移至 runtime.continuity.subscription（依赖方向冻结：task_manager →
# continuity.resume → continuity.subscription，绝不反向）；以下 re-import
# 保证既有 task_manager.<名字> 解析点（handle_quota_exhausted /
# resume_from_quota / _resume_consumption 留守调用点 + cli / hooks /
# tests）零变化。
from runtime.continuity.subscription import (
    QUOTA_SUBSCRIPTION_RESULT_KEYS,
    _QUOTA_SUBSCRIPTION_STATE_RANK,
    _cache_identity_usable,
    _epoch_context_after_refresh,
    _epoch_context_at_transition,
    _epoch_context_from_snapshot,
    _quota_subscription_view,
    _reconcile_activation_journal,
    _subscription_active,
    _subscription_state_satisfied,
    evaluate_subscription_eligibility,
    mark_activation_epoch,
    register_quota_subscription,
)

# v2.2.1 WU-221-C1（行为保持抽取）：用户授权续跑（RESUME）域的词汇常量与
# 规划/对账/资格门/边界派生助手已移至 runtime.continuity.resume
# （resume_from_quota / _resume_consumption / handle_quota_exhausted 三个
# 事务编排体留守本模块——其内部调用须经本模块名字空间解析，测试
# monkeypatch 面契约）；以下 re-import 保证全部解析点零变化。
from runtime.continuity.resume import (
    QUOTA_RESUME_GRACE_SECONDS,
    QUOTA_RESUME_STATUSES,
    QUOTA_WAIT_TASK_STATUSES,
    QUOTA_WAIT_UNIT_STATUSES,
    _clear_quota_interrupt_origin,
    _consumption_boundary_id,
    _evaluation_from_refreshed_cache,
    _quota_wake_decision,
    _recommended_resume_at,
    _reconcile_running_unit,
    _resume_subscription_gate,
    _wake_budget_remaining,
    quota_wake_prompt,
    record_quota_wake,
)


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


# TaskManagerError / _utc_now_iso / _require_state（v2.2.1 WU-221-C1
# 行为保持抽取）：规范定义移至 runtime.quota.accounting（依赖方向冻结
# 为 task_manager → accounting，accounting 绝不反向 import 本模块，故
# 共用落点必须在本模块之下）——经顶部 re-import 保持全部
# task_manager.<名字> 解析点零变化。

# —— 内部助手（容错读取，均不改入参语义） ——

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


# _cache_identity_usable（v2.2.1 WU-221-C1（行为保持抽取））：直接读者缓存身份
# 闸的规范定义移至 runtime.continuity.subscription（转态点订阅折算 / resume
# 缓存回读 / 本模块 execution phase 闸三方共用；经顶部 re-import 保持解析点
# 零变化）。


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
        # v2.2.1 WU-221-B2（QuotaIdentity）直接读者身份闸：异身份缓存
        # 视同无缓存（fail-open 返回 None，闸不激活——D7 第 4 条语义
        # 先于闸门）；legacy 无指纹缓存保守信任，行为逐字不变。
        if not _cache_identity_usable(
                cache, _current_provider_identity_hash()):
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
#
# v2.2.1 WU-221-C1（行为保持抽取）：本域词汇常量（QUOTA_WAIT_TASK_STATUSES /
# QUOTA_WAIT_UNIT_STATUSES / QUOTA_RESUME_GRACE_SECONDS /
# QUOTA_RESUME_STATUSES）与 _recommended_resume_at /
# _evaluation_from_refreshed_cache / _quota_wake_decision 的规范定义移至
# runtime.continuity.resume（依赖方向冻结：task_manager → continuity.resume
# → continuity.subscription，绝不反向），经顶部 re-import 保持解析点零变化。
# handle_quota_exhausted 留守本模块：它是额度耗尽的「转态编排」（单元/任务
# 转态 + dispatch.active 清空 + manifest 刷新），且其 register/授权矩阵/
# prompt 调用点须经本模块名字空间解析（测试 monkeypatch 面契约）。


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
          "wake": {"required", "prompt", "budget_exhausted"}, "reason"}；
      8. C5b 转态点注册订阅（best-effort 增强面，位于 manifest 刷新
         之前）：epoch 身份用转态时点缓存 snapshot 的零网络折算
         （_epoch_context_at_transition）；折算失败（无缓存 / 无
         windows / ValueError）→ 不注册零副作用（legacy 路径不变）；
         已注册同参 → register 自身幂等（C5a 保证，零写零事件）；注册
         失败（预期外异常）只降级为「任务未订阅」，绝不阻断 / 回滚
         已完成的转态——订阅是 resume 面的增强事实，不是转态事务的
         组成部份。返回键不含订阅信息（冻结七键不变）。

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
    # —— v2.2 C5b：转态点注册 quota subscription（best-effort 增强面） ——
    # 零网络折算（缓存缺位即折算失败 → 不注册零副作用，legacy 不变）；
    # 注册失败（预期外异常）不阻断 / 不回滚已完成的转态——订阅是
    # resume 面的增强事实，与 manifest 同类的 best-effort 挂点（连警告
    # 事件都不强求，避免 except 路径二次 I/O 失败遮蔽主事务结果）。
    try:
        epoch_context = _epoch_context_at_transition(repo_root)
        if epoch_context is not None:
            # v2.2.1 WU-221-B2（QuotaIdentity）：转态点的身份上下文取
            # 自幸存缓存的指纹（缓存无指纹 = legacy 形态 → 传 None，
            # 块与事件按既有形状省略该键——绝不虚构、绝不为此解析凭证）
            register_quota_subscription(
                repo_root, task_id, epoch_id=epoch_context["epoch_id"],
                provider_identity_hash=epoch_context.get(
                    "provider_identity_hash"))
    except Exception:
        pass
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


# quota_wake_prompt / record_quota_wake（v2.2.1 WU-221-C1（行为保持抽取））：
# v2.1 one-shot wake 的自足 prompt 生成与 arm-time 窗口扣减记账（LEGACY /
# DEPRECATED 兼容面），规范定义移至 runtime.continuity.resume，经顶部
# re-import 保持 cli / tests 解析点零变化。


# —— §15.1 消费记账（record_quota_boundary_consumed）/ §22.6 迁移
# 记账（migrate_quota_window_accounting）（v2.2.1 WU-221-C1 行为保持
# 抽取）：规范定义移至 runtime.quota.accounting，本模块经顶部
# re-import 保持 resume 消费段调用点与全部 task_manager.<名字> 解析点
# 零变化 ——

# v2.2.1 WU-221-C1（行为保持抽取）：
#   - _wake_budget_remaining / _clear_quota_interrupt_origin /
#     _reconcile_running_unit / _resume_subscription_gate /
#     _consumption_boundary_id 规范定义移至 runtime.continuity.resume；
#   - 订阅接线段（_epoch_context_from_snapshot / _epoch_context_at_transition
#     / _epoch_context_after_refresh / _reconcile_activation_journal /
#     _subscription_active）规范定义移至 runtime.continuity.subscription。
# 均经顶部 re-import 保持 resume_from_quota / _resume_consumption /
# handle_quota_exhausted（留守本模块）及 cli / hooks / tests 的解析点零变化。


# —— 消费接线（v2.2 C7，wu-22-C7 ①；修正计划 §15.1 消费事务点冻结） ——



def _resume_consumption(repo_root, task_id, st, epoch_id,
                        provider_identity_hash=None) -> dict:
    """resume 消费段（v2.2 C7 ①；§15.1 消费事务点 = 转态 durable +
    mark 之后的 commit point）。仅由 resume_from_quota 在订阅路径实际
    转态后调用；legacy 未注册任务零进入。§22.1 不消费清单（bridge
    fire / create / arm / no-op、still exhausted、weekly blocked、
    provider unavailable、manual/notify warm-only、primer、repeated
    probe）一律到不了本函数。

    顺序即事务序（授权预闸 → boundary 派生 → 迁移 → 消费记账）：
      1. 授权预闸（_continuity_view 三查镜像；任一不过 → 零调用零副
         作用，face 给中文 reason）：auto_resume ∈ {auto_once,
         until_done} AND authorization.source == "user" AND 剩余窗口
         > 0——manual / notify 永不消费（§22.1）；
      2. representative_boundary_id 派生（_consumption_boundary_id，
         D10 形态 kind:reset_at，无宽限，即被消费的新窗口自身身份；
         RH-04 起该辅助证据记作 representative_boundary_id，旧事件为
         legacy 键 executable_boundary_id，读侧双键兼容）；无可解析
         窗口 → 跳过消费不虚构（§31）；
      3. migrate_quota_window_accounting（幂等 one-shot 先行——迁移
         先于新形态事件，防双记账；失败 OSError 自然上抛可见）；
      4. record_quota_boundary_consumed（幂等 key = task_id +
         epoch_id，resume_started_at = 当前 UTC）：内部三查是预闸后
         的机械强制（纵深防御）——竞态 TaskManagerError → face 降级
         不抛（转态已 durable 不回滚），OSError 自然上抛（QC-07 证据
         丢失必须可见，与 mark 同款）。

    返回 consumption face dict（返回面 additive 键 "consumption"）：
      {"consumed", "epoch_id", "executable_boundary_id",
       "consumed_quota_windows", "remaining_quota_windows", "idempotent",
       "reason"|"error"}——consumed=True 为已记账（幂等命中标
       idempotent=True）；False 必附中文 reason（预闸不过 / 窗口不可
       解析跳过）或 error（record 三查竞态的中文拒绝消息）。face 键
       executable_boundary_id 为 RH-04 冻结返回键名（零变化，承载代表
       窗口身份值；事件字段已改记 representative_boundary_id）。

    v2.2.1 WU-221-B2（QuotaIdentity）：provider_identity_hash 透传
    record_quota_boundary_consumed（None = record 侧经共享模块派生；
    resume_from_quota 在同流程内已派生时直传复用）——消费幂等 / 归属
    按 QuotaIdentity 双形态判定，异身份同名 epoch 的既有消费不算数。
    """
    view = _continuity_view(st)
    if view["auto_resume"] not in ("auto_once", "until_done"):
        return {"consumed": False, "epoch_id": epoch_id,
                "executable_boundary_id": None,
                "consumed_quota_windows": view["consumed_quota_windows"],
                "remaining_quota_windows": view["remaining"],
                "idempotent": False,
                "reason": ("授权预闸不过：auto_resume=%r 不在自动续跑授权"
                           "族（auto_once / until_done）——manual / notify"
                           " 任务零调用零副作用，永不消费窗口预算（§22.1）"
                           % (view["auto_resume"],))}
    if view["source"] != "user":
        return {"consumed": False, "epoch_id": epoch_id,
                "executable_boundary_id": None,
                "consumed_quota_windows": view["consumed_quota_windows"],
                "remaining_quota_windows": view["remaining"],
                "idempotent": False,
                "reason": ("授权预闸不过：authorization.source=%r 非 "
                           "\"user\"——跨额度窗口自动续跑必须用户明确授权"
                           "，拒绝消费窗口预算（§22.1）" % (view["source"],))}
    if view["remaining"] <= 0:
        return {"consumed": False, "epoch_id": epoch_id,
                "executable_boundary_id": None,
                "consumed_quota_windows": view["consumed_quota_windows"],
                "remaining_quota_windows": view["remaining"],
                "idempotent": False,
                "reason": ("授权预闸不过：窗口预算已耗尽（consumed %d / "
                           "max %d，剩余 %d）——不消费新 epoch，转 "
                           "waiting_user 等待用户重新授权（§14.5）"
                           % (view["consumed_quota_windows"],
                              view["max_quota_windows"], view["remaining"]))}
    boundary = _consumption_boundary_id(repo_root)
    if boundary is None:
        return {"consumed": False, "epoch_id": epoch_id,
                "executable_boundary_id": None,
                "consumed_quota_windows": view["consumed_quota_windows"],
                "remaining_quota_windows": view["remaining"],
                "idempotent": False,
                "reason": ("无法从当前 epoch 快照派生 representative_"
                           "boundary_id（额度明细缺失 / windows 缺失 / 无"
                           "可解析窗口）——§31 不虚构，跳过消费（epoch_id "
                           "仍是幂等与归属权威键）")}
    # 迁移先于新形态事件（§22.6 one-shot；幂等重放零写；OSError 上抛）
    migrate_quota_window_accounting(repo_root, task_id)
    try:
        record = record_quota_boundary_consumed(
            repo_root, task_id, epoch_id=epoch_id,
            executable_boundary_id=boundary,
            resume_started_at=_utc_now_iso(),
            provider_identity_hash=provider_identity_hash)
    except TaskManagerError as exc:
        # 三查竞态（预闸后预算/授权被并发改写）：转态已 durable，face
        # 降级不抛（绝不炸掉已完成的恢复）；OSError 不在此捕获
        return {"consumed": False, "epoch_id": epoch_id,
                "executable_boundary_id": boundary,
                "consumed_quota_windows": view["consumed_quota_windows"],
                "remaining_quota_windows": view["remaining"],
                "idempotent": False,
                "error": str(exc)}
    return {"consumed": True, "epoch_id": epoch_id,
            "executable_boundary_id": record["executable_boundary_id"],
            "consumed_quota_windows": record["consumed_quota_windows"],
            "remaining_quota_windows": record["remaining_quota_windows"],
            "idempotent": bool(record.get("idempotent", False))}


def resume_from_quota(repo_root, task_id, *, status=None) -> dict:
    """额度唤醒 / SessionStart 的恢复首步（v2.1 §14 恢复入口；v2.2 C5b
    增订阅资格门；v2.2 C7 增消费接线）。

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
      4. C5b 订阅资格门（仅「已注册且 enabled=True」且任务处于
         waiting_quota / waiting_user 时介入；未注册 / 未启用 → 本步
         整体跳过，输出零变化）：
           a. 折算当前 epoch：epoch.evaluate_epoch(detail.status,
              detail.snapshot.windows)——detail 经 resolver.resolve_
              quota_detail 读回（status 解析刚完成，新鲜缓存命中零网
              络）；折算失败（无 snapshot / 无 windows / ValueError）→
              无法确认新 epoch，保守不转态；
           b. 崩溃窗口对账（evaluate 之前，_reconcile_activation_
              journal）：控制面 journal 已有本任务本 epoch 的
              quota_epoch_advanced 而 state 落后 → 保守视为已激活
              （best-effort 自愈重写，QC-07 宁可少恢复不重复激活）；
           c. evaluate_subscription_eligibility 纯判定（对账证据在案
              时资格被保守推翻）；不 eligible 或四态不放行（EXHAUSTED
              / UNKNOWN）→ 零转态，返回 dict 增 "subscription" 面；
      5. status ∈ QUOTA_RESUME_STATUSES（AVAILABLE / PRESSURE）且任务
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
         resumed=False，不落 quota_resumed；订阅面照附、零 mark）；
         否则任务转回 executing（waiting_quota → executing 与
         waiting_user → executing 均为表内边）+ save + journal
         quota_resumed {status, source, unit_recovery}；
      6. C5b 订阅任务资格过且实际转态 → mark_activation_epoch(epoch_id)
         记账。**转态-记账顺序冻结为 evaluate → 转态 → mark**：若先
         mark 后转态，「mark 已落盘、转态未落盘」的崩溃窗口会把同
         epoch 的下次恢复卡死（last_activation 已等于当前 epoch，资格
         门恒判同 epoch 无资格，任务滞留 waiting_quota 直至窗口滚动出
         新 epoch）——后转态顺序下同一崩溃窗口至多损失一次记账，恢复
         面零卡死（QC-07 方向：宁可少记账不卡死恢复）；
      7. C7 消费段（仅订阅路径实际转态后，_resume_consumption；§15.1
         消费事务点 = 转态 durable + mark 之后的 commit point）：授权
         预闸（_continuity_view 三查镜像：auto_resume ∈ {auto_once,
         until_done} AND source=="user" AND remaining>0；不过 →
         consumption face {consumed:False, reason} 零调用零副作用，
         manual / notify 永不消费 §22.1）→ representative_boundary_id
         派生（当前 epoch 快照最早可解析窗口的 D10 形态 "kind:reset_at"，
         无宽限；RH-04 起辅助证据记作 representative_boundary_id，
         旧事件为 legacy 键 executable_boundary_id，读侧双键兼容；
         无可解析窗口跳过消费不虚构 §31）→ 先 migrate_quota_
         window_accounting（幂等 one-shot 先行，防双记账）再 record_
         quota_boundary_consumed（幂等 key = task_id + epoch_id；
         三查竞态 TaskManagerError → face 降级不抛——转态已 durable；
         OSError 自然上抛——QC-07 证据丢失必须可见，与 mark 同款）；
      8. manifest 刷新 + 返回 {"resumed": True, ...}；其余情形
         （EXHAUSTED / UNKNOWN 保守等待；任务已不在等待态——如重复
         唤醒）零转态，返回 {"resumed": False, ...}（走不到消费段）。

    返回键：既有冻结五键 {"resumed", "status", "recommended_resume_at",
    "wake_budget_remaining", "unit_recovery"} 原样保留；已注册且启用
    的任务（waiting 态进入本门时）另增 additive 键 "subscription"：
      {"registered": True, "eligible": <bool>, "reasons": [...],
       "epoch_id": <§10.1 形状或 None（折算失败）>}；
    资格过且实际完成转态的恢复再增 "activated_epoch_id" 与 "mark"
    （mark_activation_epoch 的冻结三键返回）。未注册 / 未启用任务不
    加该键（输出零变化——比 registered:false 更硬的 legacy 契约）。
    recommended_resume_at 仅在 wake 强刷后 EXHAUSTED 时给出 plan_resume
    口径时刻，显式 status 恒 None（不读缓存、不虚构）。
    v2.2 C7 消费面：资格过且实际完成转态（+mark）的订阅路径恢复再增
    additive 键 "consumption"（_resume_consumption 的 face dict）：
      {"consumed", "epoch_id", "executable_boundary_id",
       "consumed_quota_windows", "remaining_quota_windows", "idempotent",
       "reason"|"error"}——consumed=True 为已记账（幂等命中标
       idempotent=True）；False 必附中文 reason（授权预闸不过 / 窗口
       不可解析跳过，§31 不虚构）或 error（record 三查竞态——转态已
       durable，不回滚恢复）；face 键 executable_boundary_id 为 RH-04
       冻结返回键名（承载代表窗口身份值；journal 事件字段已改记
       representative_boundary_id）；重复唤醒（任务已 executing）与
       legacy 未注册任务零消费键。

    §C7 检查表映射（修正计划 C7：TASK_RESUME 前六查 → 本函数落点）：
      - new executable epoch    = C5b 订阅资格门（epoch 折算 +
                                  evaluate_subscription_eligibility +
                                  崩溃对账，QC-07 同 epoch 不重复激活）；
      - authorization           = 消费段授权预闸三查之 auto_resume ∈
                                  {auto_once, until_done} AND
                                  authorization.source == "user"
                                  （manual / notify 永不消费 §22.1）；
      - window budget           = 预闸三查之 remaining > 0（预闸把
                                  关，record 内部三查机械复核）；
      - task active             = waiting 族检查（waiting_quota /
                                  waiting_user 才进门与恢复对账）；
      - manifest/repo reconcile = 既有单元对账（_reconcile_running_
                                  unit 四分落点）+ 收尾 manifest 刷新；
      - permit/lease            = wave-prepare 派发域（M4 租约事务的
                                  既有职责，此处仅文档化指向——本函数
                                  不新造调度器）。
    消费事务序（预闸 → boundary 派生 → 迁移 → 记账）见 _resume_
    consumption；boundary 为 D10 形态 "kind:reset_at"（最早可解析窗口
    自身身份，无宽限），epoch_id 恒为幂等 / 归属权威键。
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
    # —— C5b 订阅资格门：已注册且启用的 waiting 任务先裁资格 ——
    # 未注册 / 未启用（含 legacy 缺块按默认块解释）零分支进入；门内
    # 不放行（不 eligible / epoch 无法折算 / 四态 EXHAUSTED·UNKNOWN）
    # → 零转态保守等待，资格面照附（QC-07：宁可少恢复不重复激活）
    # v2.2.1 WU-221-B2（QuotaIdentity）：当前 provider 身份指纹在本
    # 流程派生一次（共享模块——纯本地读取零网络），供 mark / 消费
    # 记账复用（资格门内部自派生一次——其冻结签名不携带身份参数）。
    subscription = None
    subscription_identity = None
    if _subscription_active(st) \
            and st.get("status") in ("waiting_quota", "waiting_user"):
        subscription_identity = _current_provider_identity_hash()
        subscription = _resume_subscription_gate(repo_root, task_id, st)
        if not (subscription["eligible"]
                and status in QUOTA_RESUME_STATUSES):
            result["subscription"] = subscription
            return result
        result["subscription"] = subscription
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
    if subscription is not None:
        # C5b 转态-记账顺序（evaluate → 转态 → mark）：mark 在转态落盘
        # 之后，同 epoch 重标的幂等闸由 mark 自身保证（QC-07 每 epoch
        # 恰一次）；事件写失败（OSError）按 mark 契约自然上抛不吞——
        # 转态已 durable，调用方看得见记账证据缺失
        mark = mark_activation_epoch(repo_root, task_id,
                                     epoch_id=subscription["epoch_id"],
                                     provider_identity_hash=
                                     subscription_identity)
        subscription["activated_epoch_id"] = subscription["epoch_id"]
        subscription["mark"] = mark
        # —— C7 消费段（§15.1 消费事务点冻结：转态 durable + mark 之后
        # 的 commit point；仅订阅路径附加，legacy 未注册任务零进入）——
        result["consumption"] = _resume_consumption(
            repo_root, task_id, st, subscription["epoch_id"],
            provider_identity_hash=subscription_identity)
    _write_manifest_safe(repo_root, task_id)
    result["resumed"] = True
    result["wake_budget_remaining"] = _wake_budget_remaining(st)
    return result


# —— Persistent Wake Bridge 规划/记账域（plan_wake_bridge /
# universal_wake_prompt / arm_wake_bridge / record_bridge_fired /
# retarget_wake_bridge / reconcile_wake_bridge_from_host /
# write_completion_tombstone / degrade_continuity 及其私有助手）
#（v2.2.1 WU-221-C2 行为保持抽取）：规范定义移至
# runtime.continuity.wake_bridge，本模块经顶部 re-import 保持
# activation_transport / cli / hooks / tests 的全部
# task_manager.<名字> 解析点零变化 ——


# —— Scheduler Capability 观测镜像（v2.2 C6，wu-22-C6；修正计划 §22.5） ——

def observe_scheduler_context(repo_root, task_id, *, origin=None,
                              create=None, update=None, pause=None,
                              delete=None, parent_automation_id=None,
                              source_session=None) -> dict:
    """scheduler 能力观测镜像进任务 continuation.scheduler_context
    （hooks PostToolUse / PostToolUseFailure Cron* 路径的唯一任务面
    journal 写点；修正计划 §22.5）。

    证据只进不退（merge 语义，冻结）：
      - None 不覆盖（None = 本次观察未携带该维度证据）；
      - origin ∈ state.SCHEDULER_ORIGINS；"unknown" 视同未观察（不
        覆盖）——interactive / scheduled_task 只允许 unknown→证据值
        单向升级，落定后恒定（D15-e：Phase 0 #13 硬门）；
      - create / update / pause / delete ∈ state.SCHEDULER_CAPABILITIES；
        "unknown" 视同未观察；allowed / forbidden 只落一次（首个证据
        冻结——只进不退，forbidden 在案后不被任何后续观察冲掉）；
      - parent_automation_id：None 不覆盖；非 None 覆盖为最新观察到的
        automation 身份（观测镜像；bridge 身份权威在
        continuation.wake_bridge.automation_id）。

    §22.5 硬边界：**绝不碰 wake_bridge.status**——观测不制造 armed，
    不降级、不确认任何桥状态；re-arm 只走 arm_wake_bridge 显式路径。
    本 API 是 hook 观测路径的唯一 journal 写点（C4 伪任务教训的对偶：
    hook 直接 append_event 的旁路被冻结，记账必须经本 API 过
    save_state validator 闸）。

    变更闸：全部观察均无新值（None / unknown / 既有同值）→ 零写零
    事件，返回 "idempotent": True；任一维度实际变化 → 单次
    save_state + 恰一条 scheduler_capability_observed 事件。

    事件字段（任务 journal，冻结十键）：{"event":
    "scheduler_capability_observed", origin, create, update, pause,
    delete, parent_automation_id, source_session, changed}（changed =
    实际变化维度名列表；观察值原样落账，None = 未观察）。

    参数校验（先于任何 I/O，中文 ValueError）：origin /
    create / update / pause / delete 词汇闸；parent_automation_id /
    source_session 非 None 时须非空 str。任务缺失 TaskManagerError。
    返回冻结三键 {"changed": [...], "scheduler_context": {...},
    "idempotent": bool}（scheduler_context 为落盘后视图拷贝）。
    """
    api = "observe_scheduler_context"
    if origin is not None and origin not in state.SCHEDULER_ORIGINS:
        raise ValueError(
            "%s：origin %r 不在合法取值内（%s）"
            % (api, origin, ", ".join(state.SCHEDULER_ORIGINS)))
    for name, value in (("create", create), ("update", update),
                        ("pause", pause), ("delete", delete)):
        if value is not None and value not in state.SCHEDULER_CAPABILITIES:
            raise ValueError(
                "%s：%s %r 不在合法取值内（%s）"
                % (api, name, value, ", ".join(state.SCHEDULER_CAPABILITIES)))
    for name, value in (("parent_automation_id", parent_automation_id),
                        ("source_session", source_session)):
        if value is not None \
                and (not isinstance(value, str) or value == ""):
            raise ValueError(
                "%s：%s 必须是 None 或非空字符串，得到 %r"
                % (api, name, value))
    st = _require_state(repo_root, task_id, api)
    continuation = _ensure_continuation(st)
    ctx = continuation.get("scheduler_context")
    if not isinstance(ctx, dict):
        # 形状异常（理论不可达：save_state 闸兜底）按默认块重起，
        # 既有未知形状不放大
        ctx = state.default_continuation()["scheduler_context"]
        continuation["scheduler_context"] = ctx
    changed = []
    if origin in ("interactive", "scheduled_task") \
            and ctx.get("origin", "unknown") != origin \
            and ctx.get("origin", "unknown") == "unknown":
        ctx["origin"] = origin
        changed.append("origin")
    for name, value in (("create", create), ("update", update),
                        ("pause", pause), ("delete", delete)):
        if value in ("allowed", "forbidden") \
                and ctx.get(name, "unknown") == "unknown":
            ctx[name] = value
            changed.append(name)
    if parent_automation_id is not None \
            and ctx.get("parent_automation_id") != parent_automation_id:
        ctx["parent_automation_id"] = parent_automation_id
        changed.append("parent_automation_id")
    if not changed:
        return {"changed": [], "scheduler_context": dict(ctx),
                "idempotent": True}
    state.save_state(repo_root, st)
    journal.append_event(repo_root, task_id, {
        "event": "scheduler_capability_observed",
        "origin": origin, "create": create, "update": update,
        "pause": pause, "delete": delete,
        "parent_automation_id": parent_automation_id,
        "source_session": source_session, "changed": changed})
    return {"changed": changed, "scheduler_context": dict(ctx),
            "idempotent": False}


# —— Quota Subscription（v2.2 C5a，wu-22-C5a；主计划 §14 规范 adapted） ——
#
# v2.2.1 WU-221-C1（行为保持抽取）：订阅三事务 API（register_quota_
# subscription / evaluate_subscription_eligibility / mark_activation_epoch）
# 及其私有视图与档位序（_quota_subscription_view / _subscription_state_
# satisfied / _QUOTA_SUBSCRIPTION_STATE_RANK / QUOTA_SUBSCRIPTION_RESULT_
# KEYS）的规范定义移至 runtime.continuity.subscription，经顶部 re-import
# 保持全部 task_manager.<名字> 解析点（cli / hooks / tests）零变化。
