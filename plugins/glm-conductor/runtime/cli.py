#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GLM Conductor runtime CLI（v2.4 Phase 3 收敛后的人工操作面）。

职责：
    以单行 JSON stdout 薄壳提供数族子命令：任务操作面（review-record）、
    额度只读解析（quota-resolve）、v2.4 audit-fix AF-03 额度等待/恢复
    生命周期操作面（quota-wait / quota-resume-authorize /
    quota-resume-decision / quota-resume-confirm）、v2.4 新路径
    操作面（v24-compile / writer-* / v24-record-run——record-run 自
    v2.6.0 WP-R4 起标注 legacy 低层薄壳，规范 run 注册入口为
    task-record-workflow-run）、v2.4.1
    continuity hotfix 操作面（workflow-model-select /
    quota-automation-bind / quota-automation-clear /
    quota-continuity-preflight），以及 v2.5 任务级 worker 模型 pin
    操作面（worker-model-pin 写入 + workflow-model-select --task-ref
    消费）。技能层 runtime 调用一律走本 CLI（禁止 python3 -c 内联）。

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
    quota-wait <repo_root> <task_id> [--force-refresh]
        额度等待显式停靠（v2.4 audit-fix AF-03；task.enter_waiting_quota
        薄壳）：经 quota.resolver.resolve_quota_detail 取观测（detail
        含 snapshot），组装归一化诊断 view {status, source,
        observed_at, reset_at}——observed_at 取 detail.fetched_at
        （provider 层即 evaluated_at；none 层 None 绝不虚构）、
        reset_at 为已识别窗口中最早的有效未来观测 reset_at
        （v2.6.0 单一观测口径，规格 §2.2：snapshot 经 parser.evaluate
        求逐窗态后由 parser.earliest_future_reset_at 以 UTC instant
        显式比较选出，与窗口状态无关；已过期 / 无效 / 陈旧观测
        force refresh 恰一次，仍不可得 → None 如实披露 floor-only
        降级，绝不虚构；纯诊断字段，不参与任何判定）——再以
        quota_view 进入 waiting_quota（幂等语义
        不变；workflow_run_id 不动）。语义：任务已遭遇额度相关执行
        停止后的显式停靠命令，不是自动额度失败探测器。任务层拒绝 →
        退出码 1。输出单行 JSON {task_id, status, quota_observation}。
    quota-resume-authorize <repo_root> <task_id> <max_resumes>
        显式恢复授权（task.authorize_quota_resume 薄壳，零模型 /
        零网络调用）：max_resumes 解析为 >= 0 整数（int() 失败 /
        负数 → 参数值非法，退出码 2）；任务层拒绝（任务缺失 / 非
        waiting_quota 或 active / max_resumes 小于已用 resume_count）
        → 退出码 1；授权仍由技能 / 政策层在用户显式决定后才执行。
        输出单行 JSON {task_id, quota_resume}。
    quota-resume-decision <repo_root> <task_id> [--force-refresh]
        定时唤醒幂等决策（task.scheduled_activation_decision 薄壳）：
        同 quota-wait 方式取观测 view，输出单行 JSON {task_id,
        decision, quota_observation}（decision 为任务层原样决策
        dict——action / reason，resume-authorized 时另含
        workflow_run_id / resume_count_after 暂存计数）。v2.6.0 单
        reset_at 无条件透传（规格 §2.3，向后兼容附加键）：对所有
        action（no-op / remain-waiting / waiting-user /
        resume-authorized），decision 恒只读透传本次观测的 reset_at
        （quota_view.reset_at，ISO 字符串或 null，单一观测口径——
        已识别窗口最早有效未来 reset，与额度状态无关）——纯诊断
        透传，供主会话按睡眠模板换算精度层睡眠目标，不参与任何
        判定；既有键一律不动。
        绝不调用宿主 ResumeWorkflowRun（宿主动作归主会话）。
    quota-resume-confirm <repo_root> <task_id>
        恢复落账确认（task.confirm_resume_started 薄壳；仅在宿主
        resume 调用真正被接受后使用）：resume_count +1（暂存计数
        唯一落账点）、任务转回 active（workflow 阶段）、落
        quota_resume_confirmed 事件。任务层拒绝（非 waiting_quota
        / 未授权 / 预算已耗尽）→ 退出码 1。输出单行 JSON
        {task_id, status, phase, quota_resume}。
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
        v2.6.0 WP-R4 起降级为 **legacy 低层薄壳**（保留、行为不变——
        只落 adapter 关联单据，不写 state、不落事件、不动守卫）；
        规范任务生命周期入口是 task-record-workflow-run，编排 runbook
        一律走规范命令，本命令仅供明确只要关联单据的低层调用方使用。
    workflow-model-select <models-json> [--model <exact-id>]
            [--task-ref <task_id>] [--repo-root <path>]
        v2.4.1 Workflow 提交 worker 模型选型（H1；runtime.workflow.
        submission.select_worker_model / build_submission_contract 薄壳；
        绝不写宿主 API 客户端——模型发现面归宿主，id 集合由调用方供给
        ）。models-json 参数：能按 JSON 解析为数组则直接采用（支持内联
        '["..."]' 形态），否则按 JSON 文件路径读取数组（UTF-8；推荐路
        径：ListModels 输出写临时 JSON 文件后传文件路径）。从已配置 id
        集合选定用户显式授权的 provider 限定精确 worker 模型（v2.6.0
        WP-M1 重基线：任意已配置精确 id 合法，不限 Flash 后缀；Flash
        档降为软推荐）：--model <exact-id> 校验之并要求精确成员；不带
        显式值时恰一 Flash 档软推荐候选自动选定（便捷路径，非静默指
        派）、零候选 / 多候选歧义拒绝交回启动问答（绝不静默取第一个）。
        v2.5 起增可选 --task-ref <task_id>（I1 任务级 worker 模型 pin
        消费；账本根按 --repo-root <path> 解析，v2.5.2 W3 起提供；缺
        省当前工作目录保持兼容，v24-record-run 同款约定）。v2.6.0 起
        --task-ref 给出即读任务顶层 worker_model pin 且 pin 必检：pin
        形状非法 / pin 不在本次 configured 集合 → 专门拒绝（「任务
        pin 已失效，请重新问答授权」语义，绝不回退自动选型、绝不静默
        换模型）；任务有 pin 时 --model 显式值必须与 pin 一致，不一致
        即拒绝（规格 §5.3 第 4 条：普通 --model 不再能绕过任务 pin；
        更换授权必经交互问答重新授权并以 worker-model-pin 更新任务
        pin）；--task-ref 且无 pin 且无 --model → 退出码 2 转启动问答
        （绝不隐式授权单候选，恰一 Flash 候选也拒）；无 --task-ref 的
        裸调用无显式值才走软推荐自动选型。任务层拒绝（任务缺失 /
        v2.3 遗留）→ 退出码 1。成功输出单行 JSON 提交契约
        {subagent_model, model_policy}（model_policy 恒为
        "user-authorized-explicit-selection"，v2.6.0 WP-M1 起语义为
        用户授权显式选型）——CreateWorkflow 的 subagent_model 必须取
        此值。校验 / 配置拒绝（清单不可读 / 非法 JSON / 顶层非数组 /
        WorkflowSubmissionError / pin 失效或形状非法 / --model 与
        pin 不一致）→ 退出码 2。
    worker-model-pin <repo_root> <task_id> <exact-id>
        任务级 worker 模型 pin 写入（v2.5 I1，task.pin_worker_model
        薄壳）：把经形状预检的 exact id 写入任务顶层 worker_model 键
        （授权问答答后落盘动作；任务全生命周期默认使用）。形状预检
        采用 submission 形态约定（v2.6.0 WP-M1 起角色中性：account:
        前缀 / 两段非空 / 无空白——任意已配置精确 id 合法，不再要求
        Flash 后缀；裸别名 / 空值仍拒）但绝不查宿主模型面——写入不查
        宿主面，对账留给消费点。非终态任务才可写；同值幂等成功（零写
        盘）；异值显式覆盖（返回值带 prev 与 new——更换授权的规范落
        盘动作）。id 形状非法 → 参数值非法，退出码 2；任务层拒绝
        （任务缺失 / v2.3 遗留 / 终态冻结）→ 退出码 1。绝不新增
        journal 事件（审计由本输出与 state 落盘承载）。输出单行 JSON
        {task_id, prev, new}。
    quota-automation-bind <repo_root> <task_id> <automation_id>
        auto 连续性武装（H2，task.bind_quota_automation 薄壳）：把
        未来原生 Scheduled Task 见证 id 绑入
        quota_resume.automation_id（同 id 幂等成功、异 id 拒绝——绝不
        静默替换；绑定绝不推断 mode=auto）。任务层拒绝（任务缺失 /
        v2.3 遗留 / 状态词汇外 / 空串 id / 异 id 冲突）→ 退出码 1。
        输出单行 JSON {task_id, quota_resume}。
    quota-automation-clear <repo_root> <task_id> [automation_id]
        清除 automation_id 绑定（task.clear_quota_automation 薄壳；
        预期在宿主侧删除确认或操作者显式对账后使用）：给定 id 与存量
        不一致 → 拒绝；存量 None 幂等成功；只清 automation_id，绝不
        改 mode / max_resumes / resume_count；终态任务允许清理。
        任务层拒绝 → 退出码 1。输出单行 JSON {task_id, quota_resume}。
    quota-continuity-preflight <repo_root> <task_id>
        auto 连续性预检（task.quota_continuity_preflight 薄壳，纯读
        绝不改状态）：manual → ready=true；auto + 非空 automation_id
        → ready=true（armed）；auto + 空 → ready=false（unarmed）→
        退出码 2——主会话机械视作 launch/resume 阻断（本 CLI 惟一的
        「非校验类」退出码 2 面，判定本身即阻塞信号）。只证明持久化
        绑定存在，绝不宣称宿主侧激活仍存活。任务层拒绝（任务缺失 /
        v2.3 遗留）→ 退出码 1。输出单行 JSON {task_id, preflight}。
    task-create <repo_root> <task_id> <goal> <mode> <assurance>
            [--dag <path>]
        任务创建（v2.5.2 W2，task.create_task 薄壳）：CLI 侧先闸
        goal 非空 / mode ∈ ROUTE_MODES / assurance ∈
        ASSURANCE_LEVELS（违规 → 退出码 2）；--dag 读 DAG JSON 文件
        （顶层节点数组或 {nodes: [...]} 对象，兼容 v24-compile 同款
        格式；文件不可读 / 非法 JSON / 节点形状 / 依赖图错误聚合
        报出 → 退出码 2）。runtime.task 拒绝（任务已存在等）→
        退出码 1。成功输出 {task_id, status, route} 并落
        route_selected 事件。
    record-validation <repo_root> <task_id> <passed|failed>
            [--summary <s>] [--commands <s>] ...
        任务级验证记录（v2.5.2 W2，task.record_validation 薄壳）：
        status 词汇 CLI 侧先闸（退出码 2）；--summary 至多一次；
        --commands 可重复、每次恰一条命令（空串 → 退出码 2）。
        change_id 现算现存（见 runtime.task.record_validation）。
        runtime.task 拒绝（任务缺失 / v2.3 遗留 / 终态冻结）→
        退出码 1。输出 {task_id, validation}。
    task-complete | task-fail | task-cancel <repo_root> <task_id>
        终态收尾（v2.5.2 W2，task.complete / fail / cancel 薄壳）：
        终态迁移 + 专属 journal 事件 + 写者守卫幂等释放；同终态重放
        放行、跨终态迁移拒绝（退出码 1，任务缺失 / v2.3 遗留同）。
        输出 {task_id, status}。set_status / set_phase 保持内部原语
        不上 CLI——生命周期迁移一律走具名命令。
    exit-waiting <repo_root> <task_id>
        手动恢复出等待（v2.5.2 W2，task.exit_waiting_quota 薄壳）：
        迁回 active 并落 waiting_quota 事件（direction: exit）——
        TASK_TRANSITIONS 中「→ active」合法的全部来源均放行：
        waiting_quota / waiting_user / blocked（waiting_user 亦合法
        ——矩阵事实：手动恢复通道对自动决策已转 waiting_user 的任务
        同样适用）；原状态 active 幂等（零事件）。自动恢复路径的
        落账走既有 quota-resume-confirm（本命令是 manual 恢复与运维
        对账通道）。runtime.task 拒绝（→ active 的矩阵外来源——
        终态 completed / cancelled / failed 无转换表项）→ 退出码
        1。输出 {task_id, status}。
    task-record-workflow-run <repo_root> <task_id> <run-id>
        委派 run 注册规范入口（v2.6.0 WP-R4，task.record_workflow_run
        薄壳）：一条调用完成 state.workflow_run_id 落盘 + 账本根
        .glm-conductor/workflow-runs/ 关联单据 + journal
        workflow_started 事件 + 写者守卫 run id 幂等补挂（全经任务层
        既有事务原语；单据与进程 CWD 无关——任意 CWD 调用皆落
        repo_root 账本）。AF-04 前置：delegate/full 注册必须先持有
        本仓库写预约——预约缺失 / 属其它任务 → 拒绝且绝不落假
        workflow_started。run-id 结构非法（空串）→ 退出码 2；任务层
        拒绝（任务缺失 / v2.3 遗留 / 终态冻结 / 守卫前置失配）→
        退出码 1。输出单行 JSON {task_id, workflow_run_id, status}。

输出与退出码契约：
    stdout 恒为单行 JSON（json.dumps(..., ensure_ascii=True)，中文以
    \\uXXXX 转义——管道 / Windows 控制台零编码依赖；例外：v24-compile
    缺省输出路径时把生成 TS 源以多行纯文本打到 stdout（显式 UTF-8，
    生成源含中文））；stderr 不承载结构化输出。
    退出码：
      0 = 成功；
      2 = 校验拒绝（用法错误 / 参数值非法 / 盘上 state.json 结构性
          拒绝——JSON 损坏解析 / schema 残缺 / 任务标识错位 /
          save_state 校验闸：损坏盘面绝不静默覆盖，也绝不作为任务
          生命周期拒绝报出（v2.6.0 WP-C 显式分类：任务层 ValueError
          经各薄壳以 _is_structural_state_rejection 分类收口，本类
          不经 Rejected 包装直接归本档）；v2.5 起
          含 worker-model-pin 的 id 形状非法与 workflow-model-select
          --task-ref 的任务 pin 失效 / 形状非法；v2.6.0 起另含
          workflow-model-select 的 --model 显式值与任务 pin 不一致）；
          另含
          quota-continuity-preflight 判定 ready=false（非校验错误的
          运行期阻塞信号：主会话机械视作 launch/resume 阻断）；
      1 = 异常（任务不存在 / 审查记录被拒 / 额度等待与恢复生命周期
          操作被 runtime.task 拒绝 / auto 连续性武装操作面被
          runtime.task 拒绝 / v2.5 起 worker-model-pin 写入与任务
          pin 消费面被任务层拒绝（任务缺失 / v2.3 遗留 / 终态冻结）
          / v2.5.2 起任务生命周期操作面（task-create /
          record-validation / task-complete / task-fail /
          task-cancel / exit-waiting）与 v2.6.0 规范 run 注册
          task-record-workflow-run（含 AF-04 守卫前置失配）被
          runtime.task 拒绝——任务生命周期拒绝（含状态迁移矩阵外
          来源 / 终态冻结）恒归本档，与退出码 2 的盘面结构性拒绝
          互斥
          / 意外错误；错误 JSON 只含异常类型名与消息，供操作者排查）。

依赖方向：
    本模块是薄壳：校验与变换都在 runtime.state / runtime.task /
    runtime.writer_guard / runtime.workflow / runtime.quota.resolver
    等域模块内，这里只做 argv 解析、JSON 输出与退出码映射。插件根经
    sys.path 引导（runtime/quota/report.py 同款），因此
    `python3 plugins/glm-conductor/runtime/cli.py ...` 可在仓库根
    直接运行。

依赖：
    仅 Python 3 标准库（json / pathlib / sys）
    + runtime.messages（v2.6.1 W3a 单源：USAGE 与全部用户可见消息
      常量），零第三方依赖。
"""

import json
import pathlib
import sys

# 接线插件根以复用 runtime（CLI 脚本与 runtime/ 同插件；quota report 同款）
PLUGIN_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from runtime import messages, state  # noqa: E402

# v2.6.1 W3a 单源接线：USAGE 与全部用户可见消息改由 messages 模块单源
# 供给（本名再导出保持 cli.USAGE 等既有读取面）。发布面静态契约锚随
# 文案同迁——W3b 英文化后 validator 检查 16 重锚到 messages 单源英文值：
# "the task pin is void; re-run the launch interview for authorization"
# 与 "for pinned tasks the explicit --model value must equal the pin"
# 两英文锚（scripts/validate_plugin.py 的 PIN_REJECTION_CLI_MARKERS 与
# 模型选型锚；单源值在 messages.CLI_WORKFLOW_MODEL_SELECT_*，validator
# 与 messages 两处同步改动）。
USAGE = messages.CLI_USAGE


class _UsageError(ValueError):
    """用法 / 子命令 / 参数个数错误——校验拒绝类，退出码 2。"""


class _TaskMissing(Exception):
    """任务不存在（无 state.json）——运行期异常，退出码 1。"""


class _ReviewRejected(Exception):
    """review-record 被 runtime.task 拒绝（任务缺失 / v2.3 遗留任务 /
    终态冻结 / 先验证后评审顺序闸；ValueError 口径）——运行期拒绝，
    退出码 1（与 _QuotaRejected 同口径：盘上 state 结构性拒绝不经本
    类，经 _is_structural_state_rejection 分类后按 ValueError 原样
    透传为校验拒绝，退出码 2）。"""


class _QuotaRejected(Exception):
    """额度等待 / 恢复生命周期操作被 runtime.task 拒绝（任务缺失 /
    非 waiting 状态 / 预算不变量 / 观测词汇外等 ValueError 口径）
    ——运行期拒绝，退出码 1（盘上 state 结构性拒绝除外：那不经本
    类，经 _is_structural_state_rejection 分类后按 ValueError 原样
    透传为校验拒绝，退出码 2）。"""


class _AutomationRejected(Exception):
    """auto 连续性武装操作面（quota-automation-bind /
    quota-automation-clear / quota-continuity-preflight）被
    runtime.task 拒绝（任务缺失 / v2.3 遗留任务 / 状态词汇外 /
    空串 id / 异 id 冲突等 ValueError 口径）——运行期拒绝，退出码 1
    （与 _QuotaRejected 同口径；preflight 的 ready=false 阻断信号
    不走本类——那是显式退出码 2，见 _quota_continuity_preflight）。"""


class _PinRejected(Exception):
    """worker-model-pin 写入被 runtime.task 拒绝（任务缺失 / v2.3
    遗留任务 / 终态冻结等 ValueError 口径）——运行期拒绝，退出码 1
    （与 _AutomationRejected 同口径；id 形状非法是参数值非法，在
    CLI 侧先经 submission.validate_worker_model 预检走退出码 2，
    见 _worker_model_pin）。"""


class _PinTaskRejected(Exception):
    """workflow-model-select --task-ref 的任务 pin 消费面被任务层
    拒绝（任务缺失 / v2.3 遗留任务——消费对象必须是存在的 v2.4
    任务）——运行期拒绝，退出码 1（与 _TaskMissing / _PinRejected
    同口径）。"""


class _TaskRejected(Exception):
    """任务生命周期操作面（task-create / record-validation /
    task-complete / task-fail / task-cancel / exit-waiting /
    task-record-workflow-run）被 runtime.task 拒绝（任务已存在 /
    任务缺失 / v2.3 遗留任务 / 终态冻结 / 状态迁移矩阵外来源 /
    AF-04 写者守卫前置失配等 ValueError 口径）——运行期拒绝，退出码
    1（与 _QuotaRejected / _AutomationRejected 同口径；参数值非法在
    CLI 侧先闸走退出码 2，见各子命令实现；盘上 state 结构性拒绝
    亦不经本类——经 _is_structural_state_rejection 分类后按
    ValueError 原样透传为校验拒绝，退出码 2）。"""


# 盘上 state 结构性 ValueError 的稳定消息前缀（分类锚点——state 层
# 消息即契约文案，改动须同步本表；锚定 state.load_state 的损坏解析
# 拒绝与 _transaction_state / _transaction_commit 的严格读写闸）。
# 任务缺失（"state 事务：任务 … 不存在"）与生命周期拒绝（遗留 /
# 终态冻结 / 迁移矩阵外）不在此列——那仍归退出码 1。
_STRUCTURAL_STATE_ERROR_PREFIXES = messages.STRUCTURAL_STATE_ERROR_PREFIXES


def _is_structural_state_rejection(exc) -> bool:
    """任务层 ValueError 是否为盘上 state 结构性拒绝（JSON 损坏解析
    / schema 残缺 / 任务标识错位 / save_state 校验闸）——已发布契约
    （模块头退出码 2 条目）把这一类归入校验拒绝，与任务生命周期拒绝
    （退出码 1）互斥；识别锚定 _STRUCTURAL_STATE_ERROR_PREFIXES。"""
    return isinstance(exc, ValueError) and str(exc).startswith(
        _STRUCTURAL_STATE_ERROR_PREFIXES)


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
        raise _TaskMissing(messages.CLI_TASK_MISSING_REVIEW
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
            messages.CLI_VERDICT_NOT_IN_REVIEW_VERDICTS
            % (func_name, verdict, ", ".join(state.REVIEW_VERDICTS)))
    _require_task_for_review(repo_root, task_id)
    try:
        st = task.record_review(
            repo_root, task_id, reviewer, verdict, findings=raw_note)
    except ValueError as exc:
        if _is_structural_state_rejection(exc):
            raise  # 盘上 state 结构性拒绝 → 校验拒绝（退出码 2）
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


# —— v2.4 audit-fix AF-03：额度等待 / 恢复生命周期 CLI 面 ——
#
# 四个子命令全部是 runtime.task 既有生命周期原语与 quota.resolver
# 观测面的薄壳：不重建连续性控制面，不加 watcher / epoch /
# subscription 概念，不做额度失败自动探测器（quota-wait 是任务已
# 遭遇额度相关执行停止后的显式停靠命令）。校验与转态语义全在
# runtime.task 内，这里只做 argv 解析、观测组装、JSON 输出与退出
# 码映射（任务层 ValueError 拒绝 → _QuotaRejected，退出码 1；
# 盘上 state 结构性拒绝除外——经 _is_structural_state_rejection
# 分类后按 ValueError 原样透传为校验拒绝，退出码 2）。

def _observation_view_of(detail) -> dict:
    """单份 resolve_quota_detail 的 detail → 归一化诊断 view（恰四键，
    v2.6.0 单一观测口径；语义见 _quota_observation_view）。

    view 恒与这一份 detail 同源同批（status / source / observed_at /
    reset_at 永不跨观测拼装）；reset_at 语义为已识别窗口中最早的
    有效未来观测 reset_at（规格 §2.2），无候选 → None（floor-only
    降级如实披露，绝不虚构）。"""
    from runtime.quota import parser as quota_parser  # 函数内 import
    snapshot = detail.get("snapshot")
    evaluation = (quota_parser.evaluate(snapshot)
                  if isinstance(snapshot, dict) else None)
    return {
        "status": detail.get("status"),
        "source": detail.get("source"),
        "observed_at": detail.get("fetched_at"),
        "reset_at": quota_parser.earliest_future_reset_at(evaluation),
    }


def _quota_observation_view(repo_root, force_refresh=False) -> dict:
    """经 resolver.resolve_quota_detail 取观测并组装归一化诊断 view。

    view 恰四键 {status, source, observed_at, reset_at}：
      - status / source：detail 同名键原样透传（resolve_quota_detail
        的 detail 恒含此两键 + snapshot + fetched_at）；
      - observed_at：detail.fetched_at（底层数据抓取时刻——provider
        层即 evaluated_at；none 层为 None，绝不虚构）；
      - reset_at（v2.6.0 单一观测口径，规格 §2.2）：snapshot 先经
        quota.parser.evaluate 求逐窗四态（snapshot 的 §27 原始窗口
        不带逐窗 status），再由 quota.parser.earliest_future_reset_at
        取已识别窗口中最早的有效未来观测 reset_at（UTC instant 显式
        解析比较，与窗口 AVAILABLE/PRESSURE/EXHAUSTED/UNKNOWN 状态
        无关——下一个观测到的窗口边界即安全的唤醒 / 检查点；观测
        字符串原样透传）；无有效未来 reset → None。
    观测刷新纪律（规格 §2.2）：首次观测未强制刷新、来源为缓存
    （cache_fresh / cache_stale——观测数据可能已过期 / 陈旧）且窗口
    无有效未来 reset 时，force refresh 恰一次再组 view；仍不可得 →
    reset_at=None（floor-only 降级如实披露，绝不虚构）。provider /
    none 来源不二次刷新（provider 是刚取到的当前事实；none 层
    provider 已在本轮尝试过——resolver 纪律绝不重试网络；调用方
    已显式 --force-refresh 时同样恰一次、不叠加）。view 恒与最终
    一次观测的 detail 同源。纯诊断字段：view 不参与任务层任何
    授权 / 预算判定（runtime.task 的 _validate_quota_view 只闸
    status 词汇）。
    """
    from runtime.quota import resolver  # 函数内 import：monkeypatch 友好
    detail = resolver.resolve_quota_detail(repo_root,
                                           force_refresh=force_refresh)
    view = _observation_view_of(detail)
    if view["reset_at"] is None and not force_refresh \
            and view["source"] in ("cache_fresh", "cache_stale"):
        # v2.6.0 规格 §2.2：已过期 / 无效 / 陈旧观测 force refresh
        # 恰一次；仍不可得 → reset_at=None 如实披露 floor-only 降级
        view = _observation_view_of(
            resolver.resolve_quota_detail(repo_root, force_refresh=True))
    return view


def _quota_wait(repo_root, task_id, force_refresh=False) -> int:
    """quota-wait：额度等待显式停靠（task.enter_waiting_quota 薄壳）。

    语义：任务已遭遇额度相关执行停止后的显式停靠命令——先经
    resolver 取当前观测并组装诊断 view（_quota_observation_view），
    再以 quota_view 进入 waiting_quota（幂等语义不变：原状态已是
    waiting_quota 时只返回现状，不重复落事件、不刷新观测；
    workflow_run_id 不动）。本命令不是自动额度失败探测器。任务层
    ValueError（任务缺失 / 终态冻结 / 迁移矩阵外来源）→
    _QuotaRejected（退出码 1）。输出单行 JSON {task_id, status,
    quota_observation}。"""
    from runtime import task  # 函数内 import：monkeypatch 友好
    view = _quota_observation_view(repo_root, force_refresh=force_refresh)
    try:
        st = task.enter_waiting_quota(repo_root, task_id, quota_view=view)
    except ValueError as exc:
        if _is_structural_state_rejection(exc):
            raise  # 盘上 state 结构性拒绝 → 校验拒绝（退出码 2）
        raise _QuotaRejected(str(exc)) from exc
    _emit({"task_id": task_id, "status": st.get("status"),
           "quota_observation": view})
    return 0


def _quota_resume_authorize(repo_root, task_id, raw_max_resumes) -> int:
    """quota-resume-authorize：显式恢复授权（task.authorize_quota_resume
    薄壳，零模型 / 零网络调用）。

    max_resumes 在 CLI 侧解析为 >= 0 整数（int() 失败 / 负数 →
    ValueError，参数值非法口径，退出码 2）；任务层拒绝（任务缺失 /
    非 waiting_quota 或 active / max_resumes 小于已用 resume_count）
    → _QuotaRejected（退出码 1）。授权必须由技能 / 政策层在用户
    显式决定后才执行——本命令只落盘授权，绝不推断。输出单行 JSON
    {task_id, quota_resume}。"""
    from runtime import task  # 函数内 import：monkeypatch 友好
    try:
        max_resumes = int(raw_max_resumes)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            messages.CLI_QUOTA_RESUME_AUTHORIZE_MAX_RESUMES
            % (raw_max_resumes,)) from exc
    if max_resumes < 0:
        raise ValueError(
            messages.CLI_QUOTA_RESUME_AUTHORIZE_MAX_RESUMES
            % (raw_max_resumes,))
    try:
        st = task.authorize_quota_resume(repo_root, task_id, max_resumes)
    except ValueError as exc:
        if _is_structural_state_rejection(exc):
            raise  # 盘上 state 结构性拒绝 → 校验拒绝（退出码 2）
        raise _QuotaRejected(str(exc)) from exc
    _emit({"task_id": task_id, "quota_resume": st.get("quota_resume")})
    return 0


def _quota_resume_decision(repo_root, task_id, force_refresh=False) -> int:
    """quota-resume-decision：定时唤醒幂等决策
    （task.scheduled_activation_decision 薄壳）。

    同 quota-wait 方式取观测 view 传入决策原语；输出单行 JSON
    {task_id, decision, quota_observation}——decision 为任务层原样
    决策 dict（action / reason，resume-authorized 时另含
    workflow_run_id / resume_count_after 暂存计数）。v2.6.0 单
    reset_at 无条件透传（规格 §2.3，向后兼容附加键）：对所有
    action（no-op / remain-waiting / waiting-user /
    resume-authorized），decision 浅拷贝后恒附加本次观测的
    reset_at（view.get("reset_at")，ISO 字符串或 null，单一观测
    口径）——纯诊断透传，供主会话按精度层睡眠模板换算后台睡眠
    目标（reset_at + grace），不参与任何判定；额度状态只管可否
    继续 / 恢复，不管可否透传时间；既有键（action / reason /
    workflow_run_id / resume_count_after）与任务层副作用一律不动。
    绝不调用宿主 ResumeWorkflowRun（宿主动作归主会话；恢复执行与
    随后的 quota-resume-confirm 落账由唤醒轮次按连续性技能契约
    推进）。任务层 ValueError（任务缺失 / v2.3 遗留 / 观测词汇外 /
    无关联 run 等任务层拒绝）→ _QuotaRejected（退出码 1）。"""
    from runtime import task  # 函数内 import：monkeypatch 友好
    view = _quota_observation_view(repo_root, force_refresh=force_refresh)
    try:
        decision = task.scheduled_activation_decision(repo_root, task_id,
                                                      view)
    except ValueError as exc:
        if _is_structural_state_rejection(exc):
            raise  # 盘上 state 结构性拒绝 → 校验拒绝（退出码 2）
        raise _QuotaRejected(str(exc)) from exc
    # v2.6.0 单 reset_at 无条件透传（规格 §2.3）：对所有 action 浅拷
    # 贝追加附加键——纯诊断字段，绝不参与判定；任务层既有键与副作
    # 用逐字不动
    decision = dict(decision)
    decision["reset_at"] = view.get("reset_at")
    _emit({"task_id": task_id, "decision": decision,
           "quota_observation": view})
    return 0


def _quota_resume_confirm(repo_root, task_id) -> int:
    """quota-resume-confirm：恢复落账确认（task.confirm_resume_started
    薄壳）。

    仅在宿主 resume 调用真正被接受后使用——resume_count +1（暂存
    计数唯一落账点，恰消耗一预算）、任务转回 active（workflow
    阶段）、落 quota_resume_confirmed 事件。任务层 ValueError（非
    waiting_quota——含重复确认 / 未授权 / 预算已耗尽 / 计数形状
    异常）→ _QuotaRejected（退出码 1）。输出单行 JSON {task_id,
    status, phase, quota_resume}。"""
    from runtime import task  # 函数内 import：monkeypatch 友好
    try:
        st = task.confirm_resume_started(repo_root, task_id)
    except ValueError as exc:
        if _is_structural_state_rejection(exc):
            raise  # 盘上 state 结构性拒绝 → 校验拒绝（退出码 2）
        raise _QuotaRejected(str(exc)) from exc
    _emit({"task_id": task_id, "status": st.get("status"),
           "phase": st.get("phase"),
           "quota_resume": st.get("quota_resume")})
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
        return rejected([messages.CLI_V24_COMPILE_READ_ERROR
                         % (dag_json_path, exc)])
    except ValueError as exc:  # json.JSONDecodeError 是 ValueError 子类
        return rejected([messages.CLI_V24_COMPILE_PARSE_ERROR
                         % (dag_json_path, exc)])
    if not isinstance(dag, dict):
        return rejected([messages.CLI_V24_COMPILE_TOP_NOT_OBJECT
                         % type(dag).__name__])
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
        errors.append(messages.CLI_V24_COMPILE_NODES_NOT_ARRAY
                      % type(nodes).__name__)
    if errors:
        return rejected(errors)

    # 3) ownership 编译期阶段规划（歧义 / 冲突即拒，绝不猜测）
    try:
        stages = ownership.plan_stages(nodes)
    except (ownership.OwnershipConflictError, ownership.OwnershipError,
            ValueError) as exc:
        return rejected([messages.CLI_V24_COMPILE_OWNERSHIP_REJECTED
                         % exc])

    # 4) 编译（只生成源码字符串；形状 / 图 / task_context 错误在此聚合；
    #    绝不自动执行生成源）
    try:
        source = compiler.compile_workflow(nodes, task_context)
    except (compiler.WorkflowCompileError,
            ownership.OwnershipConflictError) as exc:
        return rejected([messages.CLI_V24_COMPILE_REJECTED % exc])

    # 5) 输出
    if out_path is not None:
        pathlib.Path(out_path).write_bytes(source.encode("utf-8"))
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
        raise ValueError(messages.CLI_WRITER_ACQUIRE_REJECTED % exc) from exc
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
        raise ValueError(messages.CLI_WRITER_RELEASE_REJECTED % exc) from exc
    _emit(result)
    return 0 if result["ok"] else 1


def _writer_show(repo_root) -> int:
    """writer-show：只读展示 repo_root 当前写预约（writer_guard.inspect
    薄壳）。输出 {holder: <四字段 dict|null>} 单键包裹形态；inspect
    永不抛（损坏记录 fail-open 视同无预约）——恒退出码 0。"""
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
        raise ValueError(messages.CLI_V24_RECORD_RUN_REJECTED % exc) from exc
    _emit(record)
    return 0


# —— v2.4.1 continuity hotfix（H1/H2）：提交模型选型与 auto 连续性 CLI 面 ——
#
# 本节同为纯追加薄壳：模型选型 / 提交契约语义在
# runtime.workflow.submission（纯内存计算，零 I/O），武装 / 清理 /
# 预检语义在 runtime.task；这里只做 models-json 读入、argv 解析、
# JSON 输出与退出码映射。绝不写宿主 API 客户端（模型发现面归宿主，
# 已配置 id 集合由调用方供给）。

def _load_configured_model_ids(models_json) -> list:
    """workflow-model-select 的 models-json 参数解析：内联 JSON 数组
    优先，否则按 JSON 文件路径读取数组。

    参数能被 json.loads 解析且恰为 JSON 数组 → 直接采用（支持内联
    '["account:a/x"]' 形态）；否则按文件路径读取（UTF-8）。文件不可读
    / 非法 JSON / 顶层非数组 → ValueError（校验拒绝口径，退出码 2；
    元素形状校验归 submission._validated_configured）。只解析调用方
    供给的 id 集合——绝不调用宿主模型发现面。"""
    try:
        inline = json.loads(models_json)
    except ValueError:
        inline = None
    if isinstance(inline, list):
        return inline
    try:
        with open(models_json, "r", encoding="utf-8") as handle:
            document = json.load(handle)
    except OSError as exc:
        raise ValueError(
            messages.CLI_WORKFLOW_MODEL_SELECT_PARAM_NOT_JSON
            % (models_json, exc))
    except ValueError as exc:  # json.JSONDecodeError 是 ValueError 子类
        raise ValueError(
            messages.CLI_WORKFLOW_MODEL_SELECT_FILE_NOT_JSON
            % (models_json, exc))
    if not isinstance(document, list):
        raise ValueError(
            messages.CLI_WORKFLOW_MODEL_SELECT_NOT_ARRAY
            % (models_json, type(document).__name__))
    return document


def _load_task_worker_model_pin(ledger_root, task_id):
    """workflow-model-select --task-ref 的任务 pin 读取（消费面专用）。

    在账本根 ledger_root 上读任务顶层 worker_model pin：
      - 任务缺失（无 state.json）→ _PinTaskRejected（退出码 1）；
      - v2.3 遗留任务 → _PinTaskRejected（退出码 1）——消费对象
        必须是存在的 v2.4 任务，绝不静默当作「无 pin」回退自动选型；
      - 键缺失 / null 占位 → None（无 pin：--task-ref 消费面由
        严口径拒绝转问答）；非 str / 空串盘面漂移 → 专门失效拒绝
        （与形状非法同口径，绝不静默当作无 pin）；
      - 其余原样返回 pin 字符串（形状与 configured 对账由调用方
        _workflow_model_select 做）。

    只读：本函数绝不写盘、绝不查宿主模型面（configured 集合由
    调用方经 models-json 参数供给）。
    """
    st = state.load_state(ledger_root, task_id)
    if st is None:
        raise _PinTaskRejected(
            messages.CLI_WORKFLOW_MODEL_SELECT_TASK_MISSING
            % (task_id, ledger_root))
    if state.is_legacy_state(st):
        raise _PinTaskRejected(
            messages.CLI_WORKFLOW_MODEL_SELECT_TASK_LEGACY
            % (task_id, state.LEGACY_STATE_GUIDANCE))
    pin = st.get("worker_model")
    if pin is None:
        return None  # 键缺失 / null 占位：无 pin（严口径拒由调用方处理）
    if not isinstance(pin, str) or pin == "":
        # 盘面漂移（外部直写非 str / 空串）与 str 形状非法同口径：
        # 失效即拒，绝不静默当作「无 pin」放行（消费面对账统一 fail-closed）
        raise ValueError(
            messages.CLI_WORKFLOW_MODEL_SELECT_PIN_DRIFT % (task_id, pin))
    return pin


def _workflow_model_select(models_json, model=None, task_ref=None,
                           ledger_root=None) -> int:
    """workflow-model-select：worker 模型选型 + 提交契约输出
    （submission.select_worker_model / build_submission_contract 薄壳；
    v2.6.0 WP-M1 重基线：worker 是用户显式授权的 provider 全限定精确
    id——绝不继承主会话文本模型，绝不接受裸别名；Flash 档为软推荐而
    非准入硬门）。

    models-json 解析见 _load_configured_model_ids（内联数组与文件两
    形态等价）。选型语义（v2.6.0 规格 §5.3 第 4 条：有 pin 任务
    普通 --model 不得绕过）：
      - --task-ref <task_id> 给出即读任务 worker_model pin（账本按
        ledger_root 解析，缺省当前工作目录——调用方须在目标仓库根
        运行本命令，v24-record-run 同款约定）；pin 存在时消费必查
        （语义不变）：submission.validate_worker_model 形状预检
        （角色中性）+ configured 精确成员对账——pin 形状非法 / 不在
        本次 configured 集合即专门拒绝：「任务 pin 已失效，请重新问
        答授权」，绝不回退自动选型、绝不静默换模型；
      - 任务有 pin 时 --model 显式值必须与 pin 一致，不一致即拒绝
        （校验拒绝口径）：普通 --model 不再能绕过任务 pin；更换授权
        必经交互问答重新授权并以 worker-model-pin 更新任务 pin；
      - 无 pin 时 --model 任意 configured 精确成员合法（不限 Flash
        后缀，submission 形状预检 + 精确成员对账）；不带 --task-ref
        的 --model 行为同此（完全显式授权路径）；
      - 无显式值分两径：**不带 --task-ref 的裸调用**走恰一 Flash 档
        软推荐候选自动选定（软推荐便捷路径，非静默指派）、零候选 /
        多候选歧义拒绝交回启动问答（绝不静默取第一个）；**--task-ref
        且任务无 pin** 则无显式值即拒（转 §6.0 问答——绝不隐式授权
        单候选，规格 §5.3 resume 严口径）。
    成功输出单行 JSON 提交契约 {subagent_model, model_policy}；校验
    / 配置拒绝（WorkflowSubmissionError 是 ValueError 子类、pin 失效
    / 形状非法、--model 与 pin 不一致）→ 退出码 2；任务层拒绝（任务
    缺失 / v2.3 遗留，_PinTaskRejected）→ 退出码 1；意外 → 退出码 1。"""
    from runtime.workflow import submission  # 函数内 import：monkeypatch 友好
    configured = _load_configured_model_ids(models_json)
    pin = None
    if task_ref is not None:
        root = (ledger_root if ledger_root is not None
                else pathlib.Path.cwd())
        pin = _load_task_worker_model_pin(root, task_ref)
        if pin is not None:
            # 消费必查（语义不变）：形状预检（写入点已预检，此处防盘面
            # 漂移）+ 当次 configured 精确成员对账；失效即专门拒绝，
            # 绝不回退——--model 给出时同样先过本闸（pin 不因显式值
            # 而免检）
            try:
                submission.validate_worker_model(pin)
            except submission.WorkflowSubmissionError as exc:
                raise ValueError(
                    messages.CLI_WORKFLOW_MODEL_SELECT_PIN_INVALID
                    % (task_ref, exc)) from exc
            if pin not in configured:
                raise ValueError(
                    messages.CLI_WORKFLOW_MODEL_SELECT_PIN_NOT_CONFIGURED
                    % (task_ref, pin, len(configured)))
    if model is not None and pin is not None and model != pin:
        # 规格 §5.3 第 4 条：有 pin 任务普通 --model 不得绕过——显式
        # 值必须与 pin 一致；更换授权必经交互问答 + worker-model-pin
        raise ValueError(
            messages.CLI_WORKFLOW_MODEL_SELECT_MODEL_PIN_MISMATCH
            % (model, task_ref, pin))
    if model is None and pin is None and task_ref is not None:
        # 规格 §5.3：resume 遇无 pin 任务绝不隐式授权单候选（§5.2
        # 单一候选也须用户显式确认）——经问答的任务必有 pin（§6.0
        # 第二题落盘），可达本分支的 --task-ref 调用即未授权选型
        raise ValueError(
            messages.CLI_WORKFLOW_MODEL_SELECT_NO_PIN_NO_MODEL
            % task_ref)
    if model is None:
        model = pin  # 无显式值：任务 pin 注入显式选择（优先于自动选型）
    selected = submission.select_worker_model(configured,
                                              explicit_model_id=model)
    _emit(submission.build_submission_contract(selected))
    return 0


def _worker_model_pin(repo_root, task_id, model_id) -> int:
    """worker-model-pin：任务级 worker 模型 pin 写入
    （task.pin_worker_model 薄壳，v2.5 I1）。

    把经形状预检的 exact id 写入任务顶层 worker_model 键（授权问答
    答后落盘动作；非终态才可写、同值幂等、异值显式覆盖带 prev/new；
    绝不查宿主模型面——对账留给消费点；绝不新增 journal 事件）。
    id 形状非法在 CLI 侧先经 submission.validate_worker_model 预检
    （v2.6.0 WP-M1 起角色中性形状校验：account: 前缀 / 两段非空 /
    无空白——任意已配置精确 id 合法，不再要求 Flash 后缀；裸别名 /
    空值仍拒；宿主配置对账留给消费点）（参数值非法口径 → 退出码 2，
    与 review-record 的 verdict 词汇先闸同款）；任务层拒绝（任务缺失
    / v2.3 遗留 / 终态冻结）→ _PinRejected（退出码 1）。输出单行
    JSON {task_id, prev, new}（与 task.pin_worker_model 返回值同源
    同字段）。"""
    from runtime import task  # 函数内 import：monkeypatch 友好
    from runtime.workflow import submission  # 函数内 import：monkeypatch 友好
    try:
        submission.validate_worker_model(model_id)
    except submission.WorkflowSubmissionError as exc:
        raise ValueError(messages.CLI_WORKER_MODEL_PIN_REJECTED % exc) from exc
    if state.load_state(repo_root, task_id) is None:
        raise _TaskMissing(
            messages.CLI_TASK_MISSING_WORKER_MODEL_PIN
            % (task_id, repo_root))
    try:
        outcome = task.pin_worker_model(repo_root, task_id, model_id)
    except ValueError as exc:
        if _is_structural_state_rejection(exc):
            raise  # 盘上 state 结构性拒绝 → 校验拒绝（退出码 2）
        raise _PinRejected(str(exc)) from exc
    _emit(outcome)
    return 0


def _quota_automation_bind(repo_root, task_id, automation_id) -> int:
    """quota-automation-bind：auto 连续性武装
    （task.bind_quota_automation 薄壳）：把未来原生 Scheduled Task
    见证 id 绑入 quota_resume.automation_id（同 id 幂等成功、异 id
    拒绝不静默替换；绑定绝不推断 mode=auto）。任务层 ValueError
    （任务缺失 / v2.3 遗留 / 状态词汇外 / 空串 id / 异 id 冲突）→
    _AutomationRejected（退出码 1）。输出单行 JSON
    {task_id, quota_resume}。"""
    from runtime import task  # 函数内 import：monkeypatch 友好
    try:
        st = task.bind_quota_automation(repo_root, task_id, automation_id)
    except ValueError as exc:
        if _is_structural_state_rejection(exc):
            raise  # 盘上 state 结构性拒绝 → 校验拒绝（退出码 2）
        raise _AutomationRejected(str(exc)) from exc
    _emit({"task_id": task_id, "quota_resume": st.get("quota_resume")})
    return 0


def _quota_automation_clear(repo_root, task_id, automation_id=None) -> int:
    """quota-automation-clear：清除 automation_id 绑定
    （task.clear_quota_automation 薄壳；预期在宿主侧删除确认或操作者
    显式对账后使用）。给定 id 与存量不一致 → 拒绝；存量 None 幂等
    成功；只清 automation_id，绝不改 mode / max_resumes /
    resume_count；终态任务允许清理。任务层 ValueError →
    _AutomationRejected（退出码 1）。输出单行 JSON
    {task_id, quota_resume}。"""
    from runtime import task  # 函数内 import：monkeypatch 友好
    try:
        st = task.clear_quota_automation(repo_root, task_id,
                                         automation_id=automation_id)
    except ValueError as exc:
        if _is_structural_state_rejection(exc):
            raise  # 盘上 state 结构性拒绝 → 校验拒绝（退出码 2）
        raise _AutomationRejected(str(exc)) from exc
    _emit({"task_id": task_id, "quota_resume": st.get("quota_resume")})
    return 0


def _quota_continuity_preflight(repo_root, task_id) -> int:
    """quota-continuity-preflight：auto 连续性预检
    （task.quota_continuity_preflight 薄壳，纯读绝不改状态）。

    manual → ready=true；auto + 非空 automation_id → ready=true
    （armed）；auto + 空 → ready=false（unarmed）→ 退出码 2——主会话
    机械视作 launch/resume 阻断（本 CLI 惟一的「非校验类」退出码 2
    面，判定本身即阻塞信号）。只证明持久化绑定存在，绝不宣称宿主侧
    激活仍存活。任务层 ValueError（任务缺失 / v2.3 遗留）→
    _AutomationRejected（退出码 1）。输出单行 JSON
    {task_id, preflight}。"""
    from runtime import task  # 函数内 import：monkeypatch 友好
    try:
        report = task.quota_continuity_preflight(repo_root, task_id)
    except ValueError as exc:
        if _is_structural_state_rejection(exc):
            raise  # 盘上 state 结构性拒绝 → 校验拒绝（退出码 2）
        raise _AutomationRejected(str(exc)) from exc
    _emit({"task_id": task_id, "preflight": report})
    return 0 if report.get("ready") is True else 2


# —— v2.5.2 W2（D1）：任务生命周期 CLI 面 ——
#
# 六个子命令全部是 runtime.task 既有公共 API 的薄壳（完善命令与文档，
# 模型不需要自己找、不允许冲突命令）：CLI 侧只做词汇 / 参数值 /
# --dag 文件形状的先闸（参数值非法 → 退出码 2，与 review-record 的
# verdict 先闸同口径），runtime.task 的 ValueError 拒绝 → _TaskRejected
# （退出码 1；盘上 state 结构性拒绝除外——经
# _is_structural_state_rejection 分类后按 ValueError 原样透传为校验
# 拒绝，退出码 2）。set_status / set_phase 保持内部原语不上面——生命
# 周期迁移一律走具名命令（task-complete / task-fail / task-cancel /
# exit-waiting）。

def _load_dag_nodes(dag_path):
    """task-create --dag 的 DAG JSON 读入：兼容 v24-compile 同款
    {nodes: [...]} 对象与裸节点数组两种形态，返回节点列表。

    文件不可读 / 非法 JSON / 既非对象又非数组 / nodes 非 array →
    ValueError（参数值非法口径，退出码 2）。节点形状与全图依赖校验
    （work_unit.validate_node + dependency.graph_errors，错误聚合
    一次报出）同在本函数完成——与 v24-compile 对同一文件格式的
    处理一致。"""
    from runtime import dependency, work_unit  # 函数内 import
    try:
        with open(dag_path, "r", encoding="utf-8") as handle:
            document = json.load(handle)
    except OSError as exc:
        raise ValueError(
            messages.CLI_TASK_CREATE_DAG_READ_ERROR % (dag_path, exc)
        ) from exc
    except ValueError as exc:  # json.JSONDecodeError 是 ValueError 子类
        raise ValueError(
            messages.CLI_TASK_CREATE_DAG_PARSE_ERROR % (dag_path, exc)
        ) from exc
    if isinstance(document, list):
        nodes = document
    elif isinstance(document, dict):
        nodes = document.get("nodes")
    else:
        nodes = None
    if not isinstance(nodes, list):
        raise ValueError(
            messages.CLI_TASK_CREATE_DAG_TOP_INVALID
            % (dag_path, type(document).__name__))
    errors = []
    for node in nodes:
        errors.extend(work_unit.validate_node(node))
    errors.extend(dependency.graph_errors(nodes))
    if errors:
        raise ValueError(
            messages.CLI_TASK_CREATE_DAG_ERRORS % "；".join(errors))
    return nodes


def _task_create(repo_root, task_id, goal, mode, assurance,
                 dag_path=None) -> int:
    """task-create：创建并持久化 v2.4 任务（task.create_task 薄壳）。

    CLI 侧先闸（参数值非法 → 退出码 2）：goal 非空；mode ∈
    state.ROUTE_MODES；assurance ∈ state.ASSURANCE_LEVELS；--dag
    文件形状见 _load_dag_nodes。runtime.task 拒绝（任务已存在 /
    状态非法聚合）→ _TaskRejected（退出码 1）。成功输出单行 JSON
    {task_id, status, route} 并落 route_selected 事件。"""
    from runtime import task  # 函数内 import：monkeypatch 友好
    if not isinstance(goal, str) or goal == "":
        raise ValueError(
            messages.CLI_TASK_CREATE_GOAL_EMPTY % (goal,))
    if mode not in state.ROUTE_MODES:
        raise ValueError(
            messages.CLI_TASK_CREATE_MODE_INVALID
            % (mode, ", ".join(state.ROUTE_MODES)))
    if assurance not in state.ASSURANCE_LEVELS:
        raise ValueError(
            messages.CLI_TASK_CREATE_ASSURANCE_INVALID
            % (assurance, ", ".join(state.ASSURANCE_LEVELS)))
    dag_nodes = _load_dag_nodes(dag_path) if dag_path is not None else ()
    try:
        st = task.create_task(
            repo_root, task_id, goal,
            {"mode": mode, "assurance": assurance}, dag_nodes=dag_nodes)
    except ValueError as exc:
        if _is_structural_state_rejection(exc):
            raise  # 盘上 state 结构性拒绝 → 校验拒绝（退出码 2）
        raise _TaskRejected(str(exc)) from exc
    _emit({"task_id": task_id, "status": st.get("status"),
           "route": st.get("route")})
    return 0


def _record_validation(repo_root, task_id, raw_status, summary=None,
                       commands=None) -> int:
    """record-validation：记录任务级验证结果（task.record_validation
    薄壳）。

    CLI 侧先闸（参数值非法 → 退出码 2）：status ∈
    state.VALIDATION_STATUSES；summary 给出时非空；commands 各项
    非空（--commands 可重复，每次恰一条命令）。runtime.task 拒绝
    （任务缺失 / v2.3 遗留 / 终态冻结 / change_id 结构性错误）→
    _TaskRejected（退出码 1）。成功输出单行 JSON {task_id,
    validation}（validation 块与 state 落盘、journal
    validation_recorded 事件同源同字段）。"""
    from runtime import task  # 函数内 import：monkeypatch 友好
    if raw_status not in state.VALIDATION_STATUSES:
        raise ValueError(
            messages.CLI_RECORD_VALIDATION_STATUS_INVALID
            % (raw_status, ", ".join(state.VALIDATION_STATUSES)))
    if summary is not None and summary == "":
        raise ValueError(
            messages.CLI_RECORD_VALIDATION_SUMMARY_EMPTY)
    if commands is not None:
        for item in commands:
            if not isinstance(item, str) or item == "":
                raise ValueError(
                    messages.CLI_RECORD_VALIDATION_COMMANDS_EMPTY)
    try:
        st = task.record_validation(repo_root, task_id, raw_status,
                                    summary=summary, commands=commands)
    except ValueError as exc:
        if _is_structural_state_rejection(exc):
            raise  # 盘上 state 结构性拒绝 → 校验拒绝（退出码 2）
        raise _TaskRejected(str(exc)) from exc
    _emit({"task_id": task_id, "validation": st.get("validation")})
    return 0


def _task_terminal(repo_root, task_id, finish_func, func_name) -> int:
    """task-complete / task-fail / task-cancel 的公共终态薄壳：
    调用 task.complete / fail / cancel（终态迁移 + 专属 journal 事件
    + 写者守卫幂等释放 + 同终态重放放行），runtime.task 拒绝（任务
    缺失 / v2.3 遗留 / 跨终态迁移）→ _TaskRejected（退出码 1）。
    成功输出单行 JSON {task_id, status}。"""
    try:
        st = finish_func(repo_root, task_id)
    except ValueError as exc:
        if _is_structural_state_rejection(exc):
            raise  # 盘上 state 结构性拒绝 → 校验拒绝（退出码 2）
        raise _TaskRejected(
            messages.CLI_TASK_REJECTED_WRAP % (func_name, exc)) from exc
    _emit({"task_id": task_id, "status": st.get("status")})
    return 0


def _exit_waiting(repo_root, task_id) -> int:
    """exit-waiting：手动恢复回 active（task.exit_waiting_quota 薄壳；
    自动恢复路径落账走既有 quota-resume-confirm）。TASK_TRANSITIONS
    中「→ active」合法的全部来源均放行（waiting_quota / waiting_user
    / blocked）；原状态已 active 幂等（零事件）；runtime.task 拒绝
    （任务缺失 / v2.3 遗留 / → active 的矩阵外来源——终态无转换表
    项）→ _TaskRejected（退出码 1）。成功输出单行 JSON {task_id,
    status} 并在真实迁移时落 waiting_quota 事件（direction: exit）。"""
    from runtime import task  # 函数内 import：monkeypatch 友好
    try:
        st = task.exit_waiting_quota(repo_root, task_id)
    except ValueError as exc:
        if _is_structural_state_rejection(exc):
            raise  # 盘上 state 结构性拒绝 → 校验拒绝（退出码 2）
        raise _TaskRejected(str(exc)) from exc
    _emit({"task_id": task_id, "status": st.get("status")})
    return 0


# —— v2.6.0 WP-R4：委派 run 注册规范 CLI 面 ——
#
# task-record-workflow-run 是 task.record_workflow_run 的薄壳（规范
# 任务生命周期入口）：state.workflow_run_id + 账本根关联单据 + journal
# workflow_started 事件 + 写者守卫 run id 幂等补挂一条调用完成，全部
# 经任务层既有事务原语；AF-04 守卫前置失败 → 拒绝且绝不落假
# workflow_started。CLI 只做 run-id 结构先闸（参数值非法口径，退出码
# 2，与 writer-acquire / v24-record-run 的 id 结构闸同款）与
# _TaskRejected 映射（退出码 1）。v24-record-run 保留为 legacy 低层
# 薄壳（仅 adapter 关联单据、CWD-only），编排 runbook 一律走本命令。

def _task_record_workflow_run(repo_root, task_id, run_id) -> int:
    """task-record-workflow-run：委派 run 注册规范入口
    （task.record_workflow_run 薄壳，v2.6.0 WP-R4）。

    一条调用完成 state.workflow_run_id 落盘 + 账本根 repo_root 的
    .glm-conductor/workflow-runs/ 关联单据 + journal workflow_started
    事件 + 写者守卫 run id 幂等补挂（任意 CWD 调用皆落 repo_root
    账本）。AF-04 前置：delegate/full 注册必须先持有本仓库写预约——
    预约缺失 / 属其它任务 → 任务层 ValueError → _TaskRejected
    （退出码 1），绝不落假 workflow_started。run-id 结构非法（空串）
    在 CLI 侧先闸（参数值非法口径 → 退出码 2）。成功输出单行 JSON
    {task_id, workflow_run_id, status}。"""
    from runtime import task  # 函数内 import：monkeypatch 友好
    if not isinstance(run_id, str) or run_id == "":
        raise ValueError(
            messages.CLI_TASK_RECORD_WORKFLOW_RUN_RUN_ID_EMPTY
            % (run_id,))
    try:
        st = task.record_workflow_run(repo_root, task_id, run_id)
    except ValueError as exc:
        if _is_structural_state_rejection(exc):
            raise  # 盘上 state 结构性拒绝 → 校验拒绝（退出码 2）
        raise _TaskRejected(str(exc)) from exc
    _emit({"task_id": task_id, "workflow_run_id": run_id,
           "status": st.get("status")})
    return 0


def _dispatch(args) -> int:
    """argv 分发；子命令 / 参数个数错误抛 _UsageError（退出码 2）。"""
    if not args:
        raise _UsageError(messages.CLI_USAGE_MISSING_SUBCOMMAND + USAGE)
    cmd, rest = args[0], args[1:]
    if cmd == "review-record":
        if len(rest) not in (4, 5):
            raise _UsageError(
                messages.CLI_USAGE_REVIEW_RECORD + USAGE)
        return _review_record(
            rest[0], rest[1], rest[2], rest[3],
            rest[4] if len(rest) == 5 else None)
    if cmd == "quota-resolve":
        if len(rest) not in (1, 2):
            raise _UsageError(
                messages.CLI_USAGE_QUOTA_RESOLVE + USAGE)
        force_refresh = False
        if len(rest) == 2:
            if rest[1] != "--force-refresh":
                raise _UsageError(
                    messages.CLI_USAGE_QUOTA_RESOLVE_FLAG
                    + USAGE)
            force_refresh = True
        return _quota_resolve(rest[0], force_refresh=force_refresh)
    if cmd == "quota-wait":
        if len(rest) not in (2, 3):
            raise _UsageError(
                messages.CLI_USAGE_QUOTA_WAIT + USAGE)
        force_refresh = False
        if len(rest) == 3:
            if rest[2] != "--force-refresh":
                raise _UsageError(
                    messages.CLI_USAGE_QUOTA_WAIT_FLAG
                    + USAGE)
            force_refresh = True
        return _quota_wait(rest[0], rest[1], force_refresh=force_refresh)
    if cmd == "quota-resume-authorize":
        if len(rest) != 3:
            raise _UsageError(
                messages.CLI_USAGE_QUOTA_RESUME_AUTHORIZE + USAGE)
        return _quota_resume_authorize(rest[0], rest[1], rest[2])
    if cmd == "quota-resume-decision":
        if len(rest) not in (2, 3):
            raise _UsageError(
                messages.CLI_USAGE_QUOTA_RESUME_DECISION + USAGE)
        force_refresh = False
        if len(rest) == 3:
            if rest[2] != "--force-refresh":
                raise _UsageError(
                    messages.CLI_USAGE_QUOTA_RESUME_DECISION_FLAG
                    + USAGE)
            force_refresh = True
        return _quota_resume_decision(rest[0], rest[1],
                                      force_refresh=force_refresh)
    if cmd == "quota-resume-confirm":
        if len(rest) != 2:
            raise _UsageError(
                messages.CLI_USAGE_QUOTA_RESUME_CONFIRM + USAGE)
        return _quota_resume_confirm(rest[0], rest[1])
    if cmd == "v24-compile":
        # 位置参数 <dag-json> + 旗标对（--task-ref <id> / --out <path>，
        # 各至多一次、顺序不限）→ 一、三或五个参数
        if len(rest) not in (1, 3, 5):
            raise _UsageError(
                messages.CLI_USAGE_V24_COMPILE + USAGE)
        task_ref = None
        out_path = None
        index = 1
        while index < len(rest):
            if index + 1 >= len(rest):
                raise _UsageError(
                    messages.CLI_USAGE_V24_COMPILE_FLAG_VALUE
                    % rest[index]
                    + USAGE)
            flag, value = rest[index], rest[index + 1]
            if flag == "--task-ref" and task_ref is None:
                task_ref = value
            elif flag == "--out" and out_path is None:
                out_path = value
            else:
                raise _UsageError(
                    messages.CLI_USAGE_V24_COMPILE_FLAGS + USAGE)
            index += 2
        return _v24_compile(rest[0], task_ref=task_ref, out_path=out_path)
    if cmd == "writer-acquire":
        if len(rest) not in (2, 4):
            raise _UsageError(
                messages.CLI_USAGE_WRITER_ACQUIRE + USAGE)
        run_id = None
        if len(rest) == 4:
            if rest[2] != "--run-id":
                raise _UsageError(
                    messages.CLI_USAGE_WRITER_ACQUIRE_FLAG
                    + USAGE)
            run_id = rest[3]
        return _writer_acquire(rest[0], rest[1], run_id=run_id)
    if cmd == "writer-release":
        if len(rest) < 2 or len(rest) > 5:
            raise _UsageError(
                messages.CLI_USAGE_WRITER_RELEASE + USAGE)
        run_id = None
        force = False
        index = 2
        while index < len(rest):
            flag = rest[index]
            if flag == "--force":
                if force:
                    raise _UsageError(
                        messages.CLI_USAGE_WRITER_RELEASE_FORCE_ONCE
                        + USAGE)
                force = True
                index += 1
            elif flag == "--run-id":
                if index + 1 >= len(rest):
                    raise _UsageError(
                        messages.CLI_USAGE_WRITER_RELEASE_RUN_ID_VALUE
                        + USAGE)
                if run_id is not None:
                    raise _UsageError(
                        messages.CLI_USAGE_WRITER_RELEASE_RUN_ID_ONCE
                        + USAGE)
                run_id = rest[index + 1]
                index += 2
            else:
                raise _UsageError(
                    messages.CLI_USAGE_WRITER_RELEASE_FLAGS + USAGE)
        return _writer_release(rest[0], rest[1], run_id=run_id, force=force)
    if cmd == "writer-show":
        if len(rest) != 1:
            raise _UsageError(
                messages.CLI_USAGE_WRITER_SHOW + USAGE)
        return _writer_show(rest[0])
    if cmd == "v24-record-run":
        if len(rest) not in (2, 4):
            raise _UsageError(
                messages.CLI_USAGE_V24_RECORD_RUN + USAGE)
        artifact = None
        if len(rest) == 4:
            if rest[2] != "--artifact":
                raise _UsageError(
                    messages.CLI_USAGE_V24_RECORD_RUN_FLAG
                    + USAGE)
            artifact = rest[3]
        return _v24_record_run(rest[0], rest[1], artifact=artifact)
    if cmd == "workflow-model-select":
        # 位置参数 <models-json> + 旗标对（--model <exact-id> /
        # --task-ref <task_id> / --repo-root <path>，各至多一次、
        # 顺序不限）→ 一、三、五或七个参数
        if len(rest) not in (1, 3, 5, 7):
            raise _UsageError(
                messages.CLI_USAGE_WORKFLOW_MODEL_SELECT + USAGE)
        model = None
        task_ref = None
        repo_root = None
        index = 1
        while index < len(rest):
            if index + 1 >= len(rest):
                raise _UsageError(
                    messages.CLI_USAGE_WORKFLOW_MODEL_SELECT_FLAG_VALUE
                    % rest[index] + USAGE)
            flag, value = rest[index], rest[index + 1]
            if flag == "--model" and model is None:
                model = value
            elif flag == "--task-ref" and task_ref is None:
                task_ref = value
            elif flag == "--repo-root" and repo_root is None:
                repo_root = value
            else:
                raise _UsageError(
                    messages.CLI_USAGE_WORKFLOW_MODEL_SELECT_FLAGS
                    + USAGE)
            index += 2
        return _workflow_model_select(rest[0], model=model,
                                      task_ref=task_ref,
                                      ledger_root=repo_root)
    if cmd == "worker-model-pin":
        if len(rest) != 3:
            raise _UsageError(
                messages.CLI_USAGE_WORKER_MODEL_PIN + USAGE)
        return _worker_model_pin(rest[0], rest[1], rest[2])
    if cmd == "quota-automation-bind":
        if len(rest) != 3:
            raise _UsageError(
                messages.CLI_USAGE_QUOTA_AUTOMATION_BIND + USAGE)
        return _quota_automation_bind(rest[0], rest[1], rest[2])
    if cmd == "quota-automation-clear":
        if len(rest) not in (2, 3):
            raise _UsageError(
                messages.CLI_USAGE_QUOTA_AUTOMATION_CLEAR + USAGE)
        return _quota_automation_clear(
            rest[0], rest[1], rest[2] if len(rest) == 3 else None)
    if cmd == "quota-continuity-preflight":
        if len(rest) != 2:
            raise _UsageError(
                messages.CLI_USAGE_QUOTA_CONTINUITY_PREFLIGHT + USAGE)
        return _quota_continuity_preflight(rest[0], rest[1])
    if cmd == "task-create":
        # 位置参数 <repo_root> <task_id> <goal> <mode> <assurance> +
        # 可选旗标对 --dag <path> → 五或七个参数
        if len(rest) not in (5, 7):
            raise _UsageError(
                messages.CLI_USAGE_TASK_CREATE + USAGE)
        dag_path = None
        if len(rest) == 7:
            if rest[5] != "--dag":
                raise _UsageError(
                    messages.CLI_USAGE_TASK_CREATE_FLAG + USAGE)
            dag_path = rest[6]
        return _task_create(rest[0], rest[1], rest[2], rest[3], rest[4],
                            dag_path=dag_path)
    if cmd == "record-validation":
        # 位置参数 <repo_root> <task_id> <passed|failed> + 旗标对
        # （--summary <s> 至多一次；--commands <s> 可重复，每次恰
        # 一条命令）——旗标恒成对出现在位置参数之后
        if len(rest) < 3 or (len(rest) - 3) % 2 != 0:
            raise _UsageError(
                messages.CLI_USAGE_RECORD_VALIDATION + USAGE)
        summary = None
        commands = []
        index = 3
        while index < len(rest):
            flag, value = rest[index], rest[index + 1]
            if flag == "--summary" and summary is None:
                summary = value
            elif flag == "--commands":
                commands.append(value)
            else:
                raise _UsageError(
                    messages.CLI_USAGE_RECORD_VALIDATION_FLAGS + USAGE)
            index += 2
        return _record_validation(rest[0], rest[1], rest[2],
                                  summary=summary,
                                  commands=commands if commands else None)
    if cmd == "task-complete":
        if len(rest) != 2:
            raise _UsageError(
                messages.CLI_USAGE_TASK_COMPLETE + USAGE)
        from runtime import task  # 函数内 import：monkeypatch 友好
        return _task_terminal(rest[0], rest[1], task.complete,
                              "task-complete")
    if cmd == "task-fail":
        if len(rest) != 2:
            raise _UsageError(
                messages.CLI_USAGE_TASK_FAIL + USAGE)
        from runtime import task  # 函数内 import：monkeypatch 友好
        return _task_terminal(rest[0], rest[1], task.fail, "task-fail")
    if cmd == "task-cancel":
        if len(rest) != 2:
            raise _UsageError(
                messages.CLI_USAGE_TASK_CANCEL + USAGE)
        from runtime import task  # 函数内 import：monkeypatch 友好
        return _task_terminal(rest[0], rest[1], task.cancel, "task-cancel")
    if cmd == "exit-waiting":
        if len(rest) != 2:
            raise _UsageError(
                messages.CLI_USAGE_EXIT_WAITING + USAGE)
        return _exit_waiting(rest[0], rest[1])
    if cmd == "task-record-workflow-run":
        if len(rest) != 3:
            raise _UsageError(
                messages.CLI_USAGE_TASK_RECORD_WORKFLOW_RUN + USAGE)
        return _task_record_workflow_run(rest[0], rest[1], rest[2])
    raise _UsageError(
        messages.CLI_USAGE_UNKNOWN_SUBCOMMAND % cmd + USAGE)


def main(argv=None) -> int:
    """CLI 入口：返回退出码（0 成功 / 2 校验拒绝 / 1 异常）。

    argv 缺省取 sys.argv[1:]；测试可直接传列表调用。异常映射：
    ValueError（用法 / 参数值 / save_state 校验栈 / 任务 pin 失效或
    形状非法 / --model 与任务 pin 不一致）→ 2；
    _TaskMissing（任务不存在）/ _ReviewRejected（审查记录被
    runtime.task 顺序与状态闸拒绝）/ _QuotaRejected（额度等待与
    恢复生命周期操作被 runtime.task 拒绝）/ _AutomationRejected
    （auto 连续性武装操作面被 runtime.task 拒绝）/ _PinRejected
    （worker-model-pin 写入被 runtime.task 拒绝）/ _PinTaskRejected
    （workflow-model-select --task-ref 消费面被任务层拒绝）/
    _TaskRejected（v2.5.2 起任务生命周期操作面与 v2.6.0 规范 run
    注册 task-record-workflow-run 被 runtime.task 拒绝）
    → 1；
    其余意外异常 → 1（错误 JSON 含异常类型名，stdout 契约不破）。
    盘上 state 结构性拒绝（JSON 损坏解析 / schema 残缺 / 任务标识
    错位 / save_state 校验闸）经 _is_structural_state_rejection 在
    _TaskRejected / _QuotaRejected / _AutomationRejected /
    _PinRejected / _ReviewRejected 各薄壳分类收口：不经 Rejected
    包装、按 ValueError 原样透传落本函数 ValueError 分支 → 2（已
    发布契约：损坏盘面绝不静默覆盖，也绝不作为任务生命周期拒绝
    报出）；生命周期拒绝（任务缺失 / 遗留 / 终态冻结 / 迁移矩阵外）
    恒归 Rejected 类 → 1，两档互斥。
    preflight 的 ready=false 阻断是函数内显式返回 2，不经异常路径。
    """
    args = list(sys.argv[1:]) if argv is None else list(argv)
    try:
        return _dispatch(args)
    except ValueError as exc:  # 含 _UsageError：校验拒绝类
        _emit({"error": str(exc)})
        return 2
    except (_TaskMissing, _ReviewRejected, _QuotaRejected,
            _AutomationRejected, _PinRejected, _PinTaskRejected,
            _TaskRejected) as exc:
        _emit({"error": str(exc)})
        return 1
    except Exception as exc:  # 意外兜底
        _emit({"error": messages.CLI_UNEXPECTED_ERROR
               % (type(exc).__name__, exc)})
        return 1


if __name__ == "__main__":
    sys.exit(main())
