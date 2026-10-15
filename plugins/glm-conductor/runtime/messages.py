#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GLM Conductor runtime 用户可见消息常量（v2.6.1 W3a 单源集中面）。

职责：
    CLI 面用户可见字符串的单一常量源：runtime/cli.py 的 USAGE 与全部
    用户可见消息（缺子命令 / 未知子命令 / 用法错误 / 退出码映射消息 /
    模型选型拒绝）、runtime/task.py 的生命周期拒绝与决策 reason 文案、
    runtime/state.py 的盘面拒绝消息（含 LEGACY_STATE_GUIDANCE）、
    runtime/writer_guard.py 的持有人 / 冲突结构校验消息。

纪律（v2.6.1 W3a，行为零变）：
    本模块只做搬移不做改写——每个常量值与抽取自原模块的字符串字面量
    逐字节一致（同一中文文案、同一 %s/%r/%d 占位符、同一标点）；格式
    化仍在原调用点原样执行，消费模块仅经 import 接线。结构性消息
    （STATE_TX_* / STATE_SAVE_* / STATE_LOAD_CORRUPT）与分类锚
    STRUCTURAL_STATE_ERROR_PREFIXES（cli._is_structural_state_rejection
    的识别前缀表）同置本模块——后者必须与 state 层消息逐字同步，W3b
    英文化时同文件一并翻转，单源防漂移。

依赖：
    零 import（叶子模块，仅常量）；3.7 兼容语法；仅 Python 3 标准库。
"""

# ============================================================
# —— 共享：legacy 指引与结构性分类锚 ——
# ============================================================

# v2.3 遗留任务的一行处置指引（消费方原样向用户报告；原 state.py
# 模块常量，经 state.LEGACY_STATE_GUIDANCE 再导出保持既有导入面）
LEGACY_STATE_GUIDANCE = (
    "v2.3 遗留任务：请在 2.3.x 下收尾或显式放弃"
    "（v2.4 不自动迁移、不伪造 v2.4 完成证据）")

# 盘上 state 结构性 ValueError 的稳定消息前缀（cli.
# _is_structural_state_rejection 的分类锚——state 层消息即契约文案，
# 改动须同步本表；锚定 state.load_state 的损坏解析拒绝与
# _transaction_state / _transaction_commit 的严格读写闸）。
STRUCTURAL_STATE_ERROR_PREFIXES = (
    "state.json 损坏，无法解析",
    "state 事务：盘上 state.json 归一任务标识",
    "state 事务：盘上 state.json schema 非法",
    "state 非法，无法保存",
    "state 事务：新 state.task_id",
)

# ============================================================
# —— runtime/cli.py：USAGE 与用户可见消息 ——
# ============================================================

# USAGE 帮助块（缺子命令 / 未知子命令 / 参数个数错误统一追加）
CLI_USAGE = (
    "用法: python3 plugins/glm-conductor/runtime/cli.py "
    "review-record <repo_root> <task_id> <reviewer> <verdict> [note] | "
    "quota-resolve <repo_root> [--force-refresh] | "
    "quota-wait <repo_root> <task_id> [--force-refresh] | "
    "quota-resume-authorize <repo_root> <task_id> <max_resumes> | "
    "quota-resume-decision <repo_root> <task_id> [--force-refresh] | "
    "quota-resume-confirm <repo_root> <task_id> | "
    "v24-compile <dag-json> [--task-ref <id>] [--out <path>] | "
    "writer-acquire <repo_root> <task_id> [--run-id <id>] | "
    "writer-release <repo_root> <task_id> [--run-id <id>] [--force] | "
    "writer-show <repo_root> | "
    "[legacy 低层薄壳] v24-record-run <task_ref> <run-id> "
    "[--artifact <path>] | "
    "workflow-model-select <models-json> [--model <exact-id>] "
    "[--task-ref <task_id>] [--repo-root <path>] | "
    "worker-model-pin <repo_root> <task_id> <exact-id> | "
    "quota-automation-bind <repo_root> <task_id> <automation_id> | "
    "quota-automation-clear <repo_root> <task_id> [automation_id] | "
    "quota-continuity-preflight <repo_root> <task_id> | "
    "task-create <repo_root> <task_id> <goal> <mode> <assurance> "
    "[--dag <path>] | "
    "record-validation <repo_root> <task_id> <passed|failed> "
    "[--summary <s>] [--commands <s>] ... | "
    "task-complete <repo_root> <task_id> | "
    "task-fail <repo_root> <task_id> | "
    "task-cancel <repo_root> <task_id> | "
    "exit-waiting <repo_root> <task_id> | "
    "task-record-workflow-run <repo_root> <task_id> <run-id>")

# —— 用法错误（_dispatch；调用点统一 + CLI_USAGE）——

CLI_USAGE_MISSING_SUBCOMMAND = "缺少子命令。"
CLI_USAGE_REVIEW_RECORD = (
    "review-record 需要 <repo_root> <task_id> <reviewer> "
    "<verdict> [note] 四或五个参数。")
CLI_USAGE_QUOTA_RESOLVE = (
    "quota-resolve 需要 <repo_root> [--force-refresh] 一或"
    "两个参数。")
CLI_USAGE_QUOTA_RESOLVE_FLAG = (
    "quota-resolve 的可选参数只接受 --force-refresh。")
CLI_USAGE_QUOTA_WAIT = (
    "quota-wait 需要 <repo_root> <task_id> "
    "[--force-refresh] 两或三个参数。")
CLI_USAGE_QUOTA_WAIT_FLAG = (
    "quota-wait 的可选参数只接受 --force-refresh。")
CLI_USAGE_QUOTA_RESUME_AUTHORIZE = (
    "quota-resume-authorize 需要 <repo_root> <task_id> "
    "<max_resumes> 三个参数。")
CLI_USAGE_QUOTA_RESUME_DECISION = (
    "quota-resume-decision 需要 <repo_root> <task_id> "
    "[--force-refresh] 两或三个参数。")
CLI_USAGE_QUOTA_RESUME_DECISION_FLAG = (
    "quota-resume-decision 的可选参数只接受 "
    "--force-refresh。")
CLI_USAGE_QUOTA_RESUME_CONFIRM = (
    "quota-resume-confirm 需要 <repo_root> <task_id> 两个"
    "参数。")
CLI_USAGE_V24_COMPILE = (
    "v24-compile 需要 <dag-json> [--task-ref <id>] "
    "[--out <path>] 一、三或五个参数。")
CLI_USAGE_V24_COMPILE_FLAG_VALUE = "v24-compile 的旗标 %r 缺少取值。"
CLI_USAGE_V24_COMPILE_FLAGS = (
    "v24-compile 的可选参数只接受 --task-ref <id> 与 "
    "--out <path>（各至多一次）。")
CLI_USAGE_WRITER_ACQUIRE = (
    "writer-acquire 需要 <repo_root> <task_id> "
    "[--run-id <id>] 两或四个参数。")
CLI_USAGE_WRITER_ACQUIRE_FLAG = (
    "writer-acquire 的可选参数只接受 --run-id <id>。")
CLI_USAGE_WRITER_RELEASE = (
    "writer-release 需要 <repo_root> <task_id> "
    "[--run-id <id>] [--force] 两到五个参数。")
CLI_USAGE_WRITER_RELEASE_FORCE_ONCE = "writer-release 的 --force 至多出现一次。"
CLI_USAGE_WRITER_RELEASE_RUN_ID_VALUE = "writer-release 的 --run-id 需要恰一个取值。"
CLI_USAGE_WRITER_RELEASE_RUN_ID_ONCE = "writer-release 的 --run-id 至多出现一次。"
CLI_USAGE_WRITER_RELEASE_FLAGS = (
    "writer-release 的可选参数只接受 --run-id <id> 与 "
    "--force。")
CLI_USAGE_WRITER_SHOW = "writer-show 需要 <repo_root> 一个参数。"
CLI_USAGE_V24_RECORD_RUN = (
    "v24-record-run 需要 <task_ref> <run-id> "
    "[--artifact <path>] 两或四个参数。")
CLI_USAGE_V24_RECORD_RUN_FLAG = (
    "v24-record-run 的可选参数只接受 --artifact <path>。")
CLI_USAGE_WORKFLOW_MODEL_SELECT = (
    "workflow-model-select 需要 <models-json> "
    "[--model <exact-id>] [--task-ref <task_id>] "
    "[--repo-root <path>] 一、三、五或七个参数。")
CLI_USAGE_WORKFLOW_MODEL_SELECT_FLAG_VALUE = (
    "workflow-model-select 的旗标 %r 缺少取值。")
CLI_USAGE_WORKFLOW_MODEL_SELECT_FLAGS = (
    "workflow-model-select 的可选参数只接受 "
    "--model <exact-id>、--task-ref <task_id> 与 "
    "--repo-root <path>（各至多一次）。")
CLI_USAGE_WORKER_MODEL_PIN = (
    "worker-model-pin 需要 <repo_root> <task_id> "
    "<exact-id> 三个参数。")
CLI_USAGE_QUOTA_AUTOMATION_BIND = (
    "quota-automation-bind 需要 <repo_root> <task_id> "
    "<automation_id> 三个参数。")
CLI_USAGE_QUOTA_AUTOMATION_CLEAR = (
    "quota-automation-clear 需要 <repo_root> <task_id> "
    "[automation_id] 两或三个参数。")
CLI_USAGE_QUOTA_CONTINUITY_PREFLIGHT = (
    "quota-continuity-preflight 需要 <repo_root> <task_id> "
    "两个参数。")
CLI_USAGE_TASK_CREATE = (
    "task-create 需要 <repo_root> <task_id> <goal> <mode> "
    "<assurance> [--dag <path>] 五或七个参数。")
CLI_USAGE_TASK_CREATE_FLAG = "task-create 的可选参数只接受 --dag <path>。"
CLI_USAGE_RECORD_VALIDATION = (
    "record-validation 需要 <repo_root> <task_id> "
    "<passed|failed> [--summary <s>] [--commands <s>] ..."
    "（旗标成对出现）。")
CLI_USAGE_RECORD_VALIDATION_FLAGS = (
    "record-validation 的可选参数只接受 --summary <s>"
    "（至多一次）与 --commands <s>（可重复，每次恰一条"
    "命令）。")
CLI_USAGE_TASK_COMPLETE = "task-complete 需要 <repo_root> <task_id> 两个参数。"
CLI_USAGE_TASK_FAIL = "task-fail 需要 <repo_root> <task_id> 两个参数。"
CLI_USAGE_TASK_CANCEL = "task-cancel 需要 <repo_root> <task_id> 两个参数。"
CLI_USAGE_EXIT_WAITING = "exit-waiting 需要 <repo_root> <task_id> 两个参数。"
CLI_USAGE_TASK_RECORD_WORKFLOW_RUN = (
    "task-record-workflow-run 需要 <repo_root> <task_id> "
    "<run-id> 三个参数。")
CLI_USAGE_UNKNOWN_SUBCOMMAND = "未知子命令 %r。"

# —— review-record / quota 生命周期薄壳 ——

CLI_TASK_MISSING_REVIEW = (
    "任务 %s 不存在（%s 下无 state.json），无法记录审查")
CLI_VERDICT_NOT_IN_REVIEW_VERDICTS = (
    "%s：verdict %r 不在 state.REVIEW_VERDICTS 内（%s）")
CLI_QUOTA_RESUME_AUTHORIZE_MAX_RESUMES = (
    "quota-resume-authorize：max_resumes 必须是 >= 0 的整数，"
    "得到 %r")

# —— v24-compile ——

CLI_V24_COMPILE_READ_ERROR = "v24-compile：无法读取 DAG JSON %r：%s"
CLI_V24_COMPILE_PARSE_ERROR = "v24-compile：DAG JSON 解析失败（%s）：%s"
CLI_V24_COMPILE_TOP_NOT_OBJECT = (
    "v24-compile：DAG JSON 顶层必须是 JSON 对象，"
    "得到 %s")
CLI_V24_COMPILE_NODES_NOT_ARRAY = "v24-compile：nodes 必须是数组，得到 %s"
CLI_V24_COMPILE_OWNERSHIP_REJECTED = "v24-compile：ownership 阶段规划拒绝：%s"
CLI_V24_COMPILE_REJECTED = "v24-compile：%s"

# —— writer-* / v24-record-run 包装 ——

CLI_WRITER_ACQUIRE_REJECTED = "writer-acquire：%s"
CLI_WRITER_RELEASE_REJECTED = "writer-release：%s"
CLI_V24_RECORD_RUN_REJECTED = "v24-record-run：%s"

# —— workflow-model-select ——

CLI_WORKFLOW_MODEL_SELECT_PARAM_NOT_JSON = (
    "workflow-model-select：参数 %r 既不是内联 JSON 数组，也无法"
    "作为 JSON 文件路径读取：%s")
CLI_WORKFLOW_MODEL_SELECT_FILE_NOT_JSON = (
    "workflow-model-select：模型清单文件 %r 不是合法 JSON：%s")
CLI_WORKFLOW_MODEL_SELECT_NOT_ARRAY = (
    "workflow-model-select：模型清单必须是 JSON 数组（模型 id "
    "字符串集合），%r 顶层为 %s")
CLI_WORKFLOW_MODEL_SELECT_TASK_MISSING = (
    "workflow-model-select：任务 %s 不存在（%s 下无 "
    "state.json），无法消费任务 pin")
CLI_WORKFLOW_MODEL_SELECT_TASK_LEGACY = (
    "workflow-model-select：任务 %s 是 v2.3 遗留任务，无法"
    "消费任务 pin：%s")
CLI_WORKFLOW_MODEL_SELECT_PIN_DRIFT = (
    "workflow-model-select：任务 %s 的 worker_model pin 形状非法"
    "（盘面漂移：%r）——任务 pin 已失效，请重新问答授权")
CLI_WORKFLOW_MODEL_SELECT_PIN_INVALID = (
    "workflow-model-select：任务 %s 的 worker_model "
    "pin 形状非法——任务 pin 已失效，请重新问答授权"
    "（%s）")
CLI_WORKFLOW_MODEL_SELECT_PIN_NOT_CONFIGURED = (
    "workflow-model-select：任务 %s 的 worker_model "
    "pin %r 不在本次 configured 集合（共 %d 个模型）"
    "——任务 pin 已失效，请重新问答授权；绝不回退自动"
    "选型、绝不静默换模型")
CLI_WORKFLOW_MODEL_SELECT_MODEL_PIN_MISMATCH = (
    "workflow-model-select：--model %r 与任务 %s 的 worker_model "
    "pin %r 不一致——有 pin 任务的 --model 显式值必须与 pin 一致"
    "（普通 --model 不得绕过任务 pin）；如需更换授权，请走交互"
    "问答重新授权并以 worker-model-pin 更新任务 pin")
CLI_WORKFLOW_MODEL_SELECT_NO_PIN_NO_MODEL = (
    "workflow-model-select：任务 %s 无 worker_model pin 且未给 "
    "--model 显式值——任务级 worker 选型必须经启动问答显式授权"
    "（orchestration §6.0 第二题）并以 worker-model-pin 落盘，"
    "绝不自动选型、绝不隐式授权单候选")

# —— worker-model-pin / task-create / record-validation ——

CLI_WORKER_MODEL_PIN_REJECTED = "worker-model-pin：%s"
CLI_TASK_MISSING_WORKER_MODEL_PIN = (
    "任务 %s 不存在（%s 下无 state.json），无法写入 worker "
    "模型 pin")
CLI_TASK_CREATE_GOAL_EMPTY = "task-create：goal 必须是非空字符串，得到 %r"
CLI_TASK_CREATE_MODE_INVALID = "task-create：mode %r 不在合法取值内（%s）"
CLI_TASK_CREATE_ASSURANCE_INVALID = "task-create：assurance %r 不在合法取值内（%s）"
CLI_TASK_CREATE_DAG_READ_ERROR = "task-create：无法读取 DAG JSON %r：%s"
CLI_TASK_CREATE_DAG_PARSE_ERROR = "task-create：DAG JSON 解析失败（%s）：%s"
CLI_TASK_CREATE_DAG_TOP_INVALID = (
    "task-create：DAG JSON 顶层必须是节点数组或含 nodes 数组的"
    " JSON 对象，%r 顶层为 %s")
CLI_TASK_CREATE_DAG_ERRORS = "task-create：%s"
CLI_RECORD_VALIDATION_STATUS_INVALID = (
    "record-validation：status %r 不在合法取值内（%s）")
CLI_RECORD_VALIDATION_SUMMARY_EMPTY = (
    "record-validation：summary 必须是非空字符串（省略即 None）")
CLI_RECORD_VALIDATION_COMMANDS_EMPTY = (
    "record-validation：commands 的各项必须是非空字符串"
    "（--commands 每次恰一条命令）")

# —— 终态薄壳包装与意外兜底 / 规范 run 注册 ——

CLI_TASK_REJECTED_WRAP = "%s：%s"
CLI_TASK_RECORD_WORKFLOW_RUN_RUN_ID_EMPTY = (
    "task-record-workflow-run：run-id 必须是非空字符串，得到 %r")
CLI_UNEXPECTED_ERROR = "%s: %s"

# ============================================================
# —— runtime/task.py：生命周期拒绝与决策 reason ——
# ============================================================

# —— 公共助手 ——

TASK_MISSING = "任务 %s 不存在（无 state.json），无法执行生命周期操作"
TASK_LEGACY_LIFECYCLE = (
    "任务 %s 是 v2.3 遗留任务，v2.4 生命周期层拒绝操作：%s")
TASK_TERMINAL_FROZEN = (
    "%s：任务 %s 已是终态 %r，账本冻结（重开请走 state.reopen_task）")
TASK_TRANSITION_NO_TARGETS = (
    "无（终态不接受任何转换；显式重开请走 state.reopen_task）")
TASK_TRANSITION_DISALLOWED = (
    "%s：status 转换 %r → %r 不在合法转换内（%s 的合法目标：%s；"
    "完整转换表见 state.TASK_TRANSITIONS）")

# —— 创建 / 读取 ——

TASK_CREATE_ALREADY_EXISTS = (
    "create_task：任务 %s 已存在（盘上已有 state.json），"
    "拒绝重建（绝不覆盖既有账本）")
TASK_CREATE_STATE_INVALID = "create_task：任务状态非法，拒绝持久化：%s"
TASK_LOAD_LEGACY = "任务 %s 是 v2.3 遗留任务：%s"

# —— 委派 run 关联 ——

TASK_RECORD_RUN_ID_EMPTY = (
    "record_workflow_run：workflow_run_id 必须是非空字符串，得到 %r")
TASK_RECORD_RUN_NO_RESERVATION = (
    "record_workflow_run：委派 run 注册必须先持有本仓库写者"
    "守卫（missing writer reservation）——任务 %s 当前无写"
    "预约，请先经宿主侧 writer-acquire 取得写预约再注册")
TASK_RECORD_RUN_GUARD_HELD_BY_OTHER = (
    "record_workflow_run：本仓库写者守卫正被其他任务持有"
    "（task_id=%r，workflow_run_id=%r），任务 %s 不得注册"
    "委派 run")
TASK_RECORD_RUN_GUARD_RACE = (
    "record_workflow_run：补挂 run id 时本仓库写者守卫已被"
    "其他任务持有（task_id=%r，workflow_run_id=%r），拒绝"
    "本次注册")

# —— 任务级验证 / 审查 ——

TASK_REVIEW_NO_VALIDATION = (
    "record_review：任务 %s 尚无 validation 记录，拒绝记录 review"
    "（先验证后评审；请先 record_validation）")
TASK_RECORD_VALIDATION_STATUS_INVALID = (
    "record_validation：status %r 不在合法取值内（%s）")
TASK_RECORD_VALIDATION_SUMMARY_INVALID = (
    "record_validation：summary 必须是非空字符串或 None，得到 %r")
TASK_RECORD_VALIDATION_COMMANDS_TYPE = (
    "record_validation：commands 必须是字符串数组或 None，"
    "得到 %s")
TASK_RECORD_VALIDATION_COMMAND_ITEM_EMPTY = (
    "record_validation：commands 的各项必须是非空字符串")
TASK_RECORD_REVIEW_REVIEWER_EMPTY = (
    "record_review：reviewer 必须是非空字符串，得到 %r")
TASK_RECORD_REVIEW_VERDICT_INVALID = (
    "record_review：verdict %r 不在合法取值内（%s）")
TASK_RECORD_REVIEW_FINDINGS_INVALID = (
    "record_review：findings 必须是非空字符串或 None，得到 %r")

# —— 状态 / phase 推进 ——

TASK_SET_STATUS_INVALID = "set_status：status %r 不在合法取值内（%s）"
TASK_SET_PHASE_INVALID = "set_phase：phase %r 不在合法取值内（%s）"

# —— 额度等待 / 恢复 ——

TASK_QUOTA_VIEW_NOT_DICT = (
    "quota_view 必须是归一化观测 JSON 对象（含 status/reset_at/"
    "source/observed_at），得到 %s")
TASK_QUOTA_VIEW_STATUS_INVALID = "quota_view.status %r 不在合法取值内（%s）"
TASK_AUTHORIZE_MAX_RESUMES_INVALID = (
    "authorize_quota_resume：max_resumes 必须是 >= 0 的整数，"
    "得到 %r")
TASK_AUTHORIZE_STATUS_INVALID = (
    "authorize_quota_resume：任务 %s 处于 %r，授权要求任务"
    "处于 waiting_quota 或 active")
TASK_AUTHORIZE_BUDGET_BELOW_COUNT = (
    "authorize_quota_resume：max_resumes %d 不得小于已用 "
    "resume_count %r（预算不变量：resume_count <= "
    "max_resumes）")

# —— 定时唤醒决策（decision.reason 文案） ——

TASK_DECISION_NOT_WAITING = (
    "任务状态为 %r，非 waiting_quota（重复唤醒安全，"
    "不采取任何动作）")
TASK_DECISION_EXHAUSTED = "额度观测为 %s，继续等待"
TASK_DECISION_UNKNOWN = "额度观测为 UNKNOWN，不虚构可用性，继续等待"
TASK_DECISION_NOT_AUTHORIZED = (
    "quota_resume.mode=%r（未授权自动恢复），等用户"
    "显式 authorize_quota_resume")
TASK_DECISION_BUDGET_EXHAUSTED = (
    "自动恢复预算已耗尽（resume_count=%d >= "
    "max_resumes=%d），任务转 waiting_user 等用户处理")
TASK_DECISION_CONTINUITY_UNARMED = (
    "continuity-unarmed：auto 连续性未武装"
    "（quota_resume.automation_id=%r），缺少未来定时"
    "激活见证，绝不给出 resume-authorized；任务保持 "
    "waiting_quota、零预算消耗——这是运维活性缺失而非"
    "授权耗尽，请先经宿主侧创建定时激活并 "
    "bind_quota_automation 绑定，再经 "
    "quota_continuity_preflight 确认 ready")
TASK_DECISION_NO_RUN = (
    "scheduled_activation_decision：任务 %s 无关联 "
    "workflow_run_id，无 run 可恢复，拒绝给出 resume-authorized")
TASK_DECISION_RESUME_AUTHORIZED = (
    "额度可用（%s）且已授权、预算有余（%d/%d）；计数仅"
    "暂存，宿主 resume 调用被接受后请调 "
    "confirm_resume_started 落账")

# —— 恢复落账确认 ——

TASK_CONFIRM_NOT_WAITING = (
    "confirm_resume_started：任务 %s 处于 %r，非 waiting_quota"
    "——无暂存恢复决策可落账（重复确认安全：确认后任务已转"
    " active）")
TASK_CONFIRM_NOT_AUTHORIZED = (
    "confirm_resume_started：任务 %s 的 quota_resume.mode=%r，"
    "未授权自动恢复，拒绝落账")
TASK_CONFIRM_COUNT_SHAPE = (
    "confirm_resume_started：任务 %s 的 quota_resume 计数形状"
    "非法（resume_count=%r / max_resumes=%r），拒绝落账")
TASK_CONFIRM_BUDGET_EXHAUSTED = (
    "confirm_resume_started：任务 %s 预算已耗尽（resume_count="
    "%d >= max_resumes=%d），无暂存决策可落账")

# —— auto 连续性武装 / 清理 ——

TASK_BIND_STATUS_INVALID = (
    "bind_quota_automation：任务 %s 处于 %r，绑定要求任务处于"
    " %s（终态不得新增绑定事实；终态清理走 clear_quota_"
    "automation）")
TASK_BIND_CONFLICT = (
    "bind_quota_automation：任务 %s 已绑定 automation_id %r，"
    "拒绝静默替换为 %r（替换必须先经宿主侧生命周期对账，再走"
    "显式 clear_quota_automation → bind_quota_automation 序列）")
TASK_BIND_ID_EMPTY = (
    "bind_quota_automation：automation_id 必须是非空字符串，"
    "得到 %r")
TASK_CLEAR_MISMATCH = (
    "clear_quota_automation：给定 automation_id %r 与任务 %s "
    "的存量绑定 %r 不一致，拒绝清理（精确对账口径；无预期时"
    "传 None 表示无条件清除现存绑定）")

# —— 连续性预检（reason 为规格 §4.1 给出的英文短句） ——

TASK_PREFLIGHT_MANUAL = "manual mode does not require scheduled activation"
TASK_PREFLIGHT_ARMED = "auto continuity armed"
TASK_PREFLIGHT_UNARMED = "auto continuity is not armed"

# —— worker 模型 pin / 终态收尾 ——

TASK_PIN_REJECTED = "pin_worker_model：%s"
TASK_FINISH_POST_COMMIT_MISMATCH = (
    "%s：状态事务提交后任务 %s 处于 %r，非目标终态 %r，拒绝"
    "追加事件与释放守卫")

# ============================================================
# —— runtime/state.py：盘面拒绝消息 ——
# ============================================================

# —— legacy 标识归一 ——

STATE_NORMALIZE_NOT_DICT = "任务标识归一失败：输入必须是 JSON 对象"
STATE_NORMALIZE_MISSING_KEY = "任务标识归一失败：缺少任务标识键（%s）"
STATE_NORMALIZE_KEY_MISMATCH = (
    "任务标识归一失败：多个标识键取值不一致（%s=%r 与 %s=%r）")
STATE_NORMALIZE_EMPTY_VALUE = "任务标识归一失败：%s 的值必须是非空字符串"

# —— 校验（validate_state 及其子校验器） ——

STATE_ENUM_ERROR = "%s %r 不在合法取值内（%s）"
STATE_STR_LIST_NOT_ARRAY = "%s 必须是数组"
STATE_STR_LIST_ITEM_EMPTY = "%s[%d] 必须是非空字符串"
STATE_ROUTE_NOT_OBJECT = "route 必须是 JSON 对象"
STATE_REPOSITORY_NOT_OBJECT = "repository 必须是 JSON 对象"
STATE_REPOSITORY_ROOT_EMPTY = "repository.root 必须是非空字符串"
STATE_DAG_NOT_ARRAY = "dag 必须是数组"
STATE_VALIDATION_NOT_OBJECT = "validation 必须是 JSON 对象"
STATE_VALIDATION_FIELD_INVALID = "validation.%s 必须是非空字符串或 null"
STATE_REVIEW_NOT_OBJECT = "review 必须是 JSON 对象"
STATE_REVIEW_FIELD_INVALID = "review.%s 必须是非空字符串或 null"
STATE_QUOTA_RESUME_NOT_OBJECT = "quota_resume 必须是 JSON 对象"
STATE_QUOTA_RESUME_COUNT_INVALID = "quota_resume.%s 必须是 >= 0 的整数"
STATE_QUOTA_RESUME_BUDGET_INVARIANT = (
    "quota_resume.resume_count %d 不得大于 quota_resume."
    "max_resumes %d（预算不变量：resume_count <= max_resumes）")
STATE_QUOTA_RESUME_AUTOMATION_ID_INVALID = (
    "quota_resume.automation_id 必须是非空字符串或 null")
STATE_WORKER_MODEL_INVALID = "worker_model 必须是非空字符串或 null"
STATE_NOT_OBJECT = "state 必须是 JSON 对象"
STATE_MISSING_REQUIRED_KEY = "缺少必填顶层键 %s"
STATE_TASK_ID_EMPTY = "task_id 必须是非空字符串"
STATE_TASK_ID_FORMAT = (
    "task_id %r 不匹配格式 ^[A-Za-z0-9][A-Za-z0-9-]*$"
    "（字母数字开头，仅含字母数字与连字符）")
STATE_GOAL_EMPTY = "goal 必须是非空字符串"
STATE_WORKFLOW_RUN_ID_INVALID = "workflow_run_id 必须是非空字符串或 null"

# —— 状态迁移门 ——

STATE_TRANSITION_NO_TARGETS = (
    "无（终态不接受任何转换；显式重开请走 reopen_task）")
STATE_TRANSITION_DISALLOWED = (
    "status 转换被拒绝：%r → %r 不在合法转换内"
    "（%s 的合法目标：%s；完整转换表见 TASK_TRANSITIONS）")

# —— per-task 状态事务（结构性拒绝——分类锚见
#     STRUCTURAL_STATE_ERROR_PREFIXES，两处必须逐字同步） ——

STATE_TX_TASK_MISSING = (
    "state 事务：任务 %s 不存在（无 state.json），拒绝读写")
STATE_TX_ID_MISMATCH = (
    "state 事务：盘上 state.json 归一任务标识 %r 与目标 %r 不一致"
    "，拒绝在错位账本上写入")
STATE_TX_SCHEMA_INVALID = (
    "state 事务：盘上 state.json schema 非法，拒绝在其上写入"
    "（损坏不被覆盖）：%s")
STATE_SAVE_INVALID = "state 非法，无法保存：%s"
STATE_TX_NEW_TASK_ID_MISMATCH = (
    "state 事务：新 state.task_id %r 与事务目标 %r 不一致，拒绝"
    "提交（P1-9：目录与内容的任务标识必须一致）")
STATE_TX_MUTATE_RETURN_INVALID = (
    "update_task_state：mutate 必须返回完整 state dict，得到 %s")
STATE_SAVE_DIR_ID_MISMATCH = (
    "save_state：目录 task_id %r 与 state.task_id %r 不一致"
    "（P1-9：目录与内容的任务标识必须一致；如需迁移请以内容 ID "
    "为准重建目录）")
STATE_LOAD_CORRUPT = "state.json 损坏，无法解析（%s）：%s"

# —— 重开 / 仓库根绑定 / 构造 ——

STATE_REOPEN_NOT_TERMINAL = (
    "reopen_task：任务 %s 的 status 为 %r，非终态任务无需重开"
    "（迁移请走 transition_task_status）")
STATE_BIND_REPO_ROOT_INVALID = (
    "bind_repository_root：repo_root 必须是非空字符串路径，得到 %r")
STATE_NEW_TASK_ROUTE_TYPE = (
    "new_task_state：route 必须是 dict（v2.4 route 含 mode/"
    "assurance），得到 %s")
STATE_NEW_TASK_DAG_NODES_TYPE = (
    "new_task_state：dag_nodes 必须是 list 或 tuple，得到 %s")

# —— 发现（corrupt / orphaned 桶 reason） ——

STATE_DISCOVER_CORRUPT = "state.json 损坏：%s"
STATE_DISCOVER_UNREADABLE = "state.json 不可读：%s"
STATE_DISCOVER_NO_STATE = "无 state.json"

# ============================================================
# —— runtime/writer_guard.py：结构校验消息 ——
# ============================================================

WRITER_GUARD_TASK_ID_INVALID = (
    "writer_guard：task_id 必须是非空字符串（由调用方供给），得到 %r")
WRITER_GUARD_RUN_ID_INVALID = (
    "writer_guard：workflow_run_id 必须是 None 或非空字符串，得到 %r")
