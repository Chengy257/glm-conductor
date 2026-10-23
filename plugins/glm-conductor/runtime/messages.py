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
    "v2.3 legacy task: wrap up or explicitly abandon it under 2.3.x"
    " (v2.4 does not auto-migrate and never fabricates v2.4"
    " completion evidence)")

# 盘上 state 结构性 ValueError 的稳定消息前缀（cli.
# _is_structural_state_rejection 的分类锚——state 层消息即契约文案，
# 改动须同步本表；锚定 state.load_state 的损坏解析拒绝与
# _transaction_state / _transaction_commit 的严格读写闸）。
STRUCTURAL_STATE_ERROR_PREFIXES = (
    "state.json is corrupt and cannot be parsed",
    "state transaction: on-disk state.json normalized task id",
    "state transaction: on-disk state.json schema is invalid",
    "state is invalid and cannot be saved",
    "state transaction: new state.task_id",
)

# ============================================================
# —— runtime/cli.py：USAGE 与用户可见消息 ——
# ============================================================

# USAGE 帮助块（缺子命令 / 未知子命令 / 参数个数错误统一追加）
CLI_USAGE = (
    "Usage: python3 plugins/glm-conductor/runtime/cli.py "
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
    "[legacy low-level shell] v24-record-run <task_ref> <run-id> "
    "[--artifact <path>] | "
    "workflow-model-select <models-json> [--model <exact-id>] "
    "[--task-ref <task_id>] [--repo-root <path>] | "
    "worker-model-pin <repo_root> <task_id> <exact-id> | "
    "quota-automation-bind <repo_root> <task_id> <automation_id> | "
    "quota-automation-clear <repo_root> <task_id> [automation_id] | "
    "quota-continuity-preflight <repo_root> <task_id> | "
    "task-create <repo_root> <task_id> <goal> <mode> <assurance> "
    "[--dag <path>] [--auto-approve-workflow] | "
    "record-validation <repo_root> <task_id> <passed|failed> "
    "[--summary <s>] [--commands <s>] ... | "
    "task-complete <repo_root> <task_id> | "
    "task-fail <repo_root> <task_id> | "
    "task-cancel <repo_root> <task_id> | "
    "exit-waiting <repo_root> <task_id> | "
    "task-record-workflow-run <repo_root> <task_id> <run-id>")

# —— 用法错误（_dispatch；调用点统一 + CLI_USAGE）——

CLI_USAGE_MISSING_SUBCOMMAND = "Missing subcommand."
CLI_USAGE_REVIEW_RECORD = (
    "review-record requires <repo_root> <task_id> <reviewer> "
    "<verdict> [note]: four or five arguments.")
CLI_USAGE_QUOTA_RESOLVE = (
    "quota-resolve requires <repo_root> [--force-refresh]: "
    "one or two arguments.")
CLI_USAGE_QUOTA_RESOLVE_FLAG = (
    "quota-resolve accepts only --force-refresh as an optional "
    "argument.")
CLI_USAGE_QUOTA_WAIT = (
    "quota-wait requires <repo_root> <task_id> "
    "[--force-refresh]: two or three arguments.")
CLI_USAGE_QUOTA_WAIT_FLAG = (
    "quota-wait accepts only --force-refresh as an optional "
    "argument.")
CLI_USAGE_QUOTA_RESUME_AUTHORIZE = (
    "quota-resume-authorize requires <repo_root> <task_id> "
    "<max_resumes>: three arguments.")
CLI_USAGE_QUOTA_RESUME_DECISION = (
    "quota-resume-decision requires <repo_root> <task_id> "
    "[--force-refresh]: two or three arguments.")
CLI_USAGE_QUOTA_RESUME_DECISION_FLAG = (
    "quota-resume-decision accepts only "
    "--force-refresh as an optional argument.")
CLI_USAGE_QUOTA_RESUME_CONFIRM = (
    "quota-resume-confirm requires <repo_root> <task_id>: "
    "two arguments.")
CLI_USAGE_V24_COMPILE = (
    "v24-compile requires <dag-json> [--task-ref <id>] "
    "[--out <path>]: one, three, or five arguments.")
CLI_USAGE_V24_COMPILE_FLAG_VALUE = "v24-compile: flag %r is missing a value."
CLI_USAGE_V24_COMPILE_FLAGS = (
    "v24-compile accepts only --task-ref <id> and "
    "--out <path> as optional arguments (each at most once).")
CLI_USAGE_WRITER_ACQUIRE = (
    "writer-acquire requires <repo_root> <task_id> "
    "[--run-id <id>]: two or four arguments.")
CLI_USAGE_WRITER_ACQUIRE_FLAG = (
    "writer-acquire accepts only --run-id <id> as an optional "
    "argument.")
CLI_USAGE_WRITER_RELEASE = (
    "writer-release requires <repo_root> <task_id> "
    "[--run-id <id>] [--force]: two to five arguments.")
CLI_USAGE_WRITER_RELEASE_FORCE_ONCE = "writer-release accepts --force at most once."
CLI_USAGE_WRITER_RELEASE_RUN_ID_VALUE = "writer-release: --run-id requires exactly one value."
CLI_USAGE_WRITER_RELEASE_RUN_ID_ONCE = "writer-release accepts --run-id at most once."
CLI_USAGE_WRITER_RELEASE_FLAGS = (
    "writer-release accepts only --run-id <id> and "
    "--force as optional arguments.")
CLI_USAGE_WRITER_SHOW = "writer-show requires exactly one argument: <repo_root>."
CLI_USAGE_V24_RECORD_RUN = (
    "v24-record-run requires <task_ref> <run-id> "
    "[--artifact <path>]: two or four arguments.")
CLI_USAGE_V24_RECORD_RUN_FLAG = (
    "v24-record-run accepts only --artifact <path> as an "
    "optional argument.")
CLI_USAGE_WORKFLOW_MODEL_SELECT = (
    "workflow-model-select requires <models-json> "
    "[--model <exact-id>] [--task-ref <task_id>] "
    "[--repo-root <path>]: one, three, five, or seven arguments.")
CLI_USAGE_WORKFLOW_MODEL_SELECT_FLAG_VALUE = (
    "workflow-model-select: flag %r is missing a value.")
CLI_USAGE_WORKFLOW_MODEL_SELECT_FLAGS = (
    "workflow-model-select accepts only "
    "--model <exact-id>, --task-ref <task_id>, and "
    "--repo-root <path> as optional arguments (each at most "
    "once).")
CLI_USAGE_WORKER_MODEL_PIN = (
    "worker-model-pin requires <repo_root> <task_id> "
    "<exact-id>: three arguments.")
CLI_USAGE_QUOTA_AUTOMATION_BIND = (
    "quota-automation-bind requires <repo_root> <task_id> "
    "<automation_id>: three arguments.")
CLI_USAGE_QUOTA_AUTOMATION_CLEAR = (
    "quota-automation-clear requires <repo_root> <task_id> "
    "[automation_id]: two or three arguments.")
CLI_USAGE_QUOTA_CONTINUITY_PREFLIGHT = (
    "quota-continuity-preflight requires <repo_root> <task_id>: "
    "two arguments.")
CLI_USAGE_TASK_CREATE = (
    "task-create requires <repo_root> <task_id> <goal> <mode> "
    "<assurance> [--dag <path>] [--auto-approve-workflow]: five, six, "
    "seven, or eight arguments.")
CLI_USAGE_TASK_CREATE_FLAG = (
    "task-create accepts only --dag <path> and "
    "--auto-approve-workflow as optional arguments.")
CLI_USAGE_TASK_CREATE_AUTO_APPROVE_ONCE = (
    "task-create accepts --auto-approve-workflow at most once.")
CLI_USAGE_RECORD_VALIDATION = (
    "record-validation requires <repo_root> <task_id> "
    "<passed|failed> [--summary <s>] [--commands <s>] ..."
    " (flags come in pairs).")
CLI_USAGE_RECORD_VALIDATION_FLAGS = (
    "record-validation accepts only --summary <s>"
    " (at most once) and --commands <s> (repeatable, exactly one "
    "command each time) as optional arguments.")
CLI_USAGE_TASK_COMPLETE = "task-complete requires <repo_root> <task_id>: two arguments."
CLI_USAGE_TASK_FAIL = "task-fail requires <repo_root> <task_id>: two arguments."
CLI_USAGE_TASK_CANCEL = "task-cancel requires <repo_root> <task_id>: two arguments."
CLI_USAGE_EXIT_WAITING = "exit-waiting requires <repo_root> <task_id>: two arguments."
CLI_USAGE_TASK_RECORD_WORKFLOW_RUN = (
    "task-record-workflow-run requires <repo_root> <task_id> "
    "<run-id>: three arguments.")
CLI_USAGE_UNKNOWN_SUBCOMMAND = "Unknown subcommand %r."

# —— review-record / quota 生命周期薄壳 ——

CLI_TASK_MISSING_REVIEW = (
    "Task %s does not exist (no state.json under %s); "
    "cannot record the review")
CLI_VERDICT_NOT_IN_REVIEW_VERDICTS = (
    "%s: verdict %r is not in state.REVIEW_VERDICTS (%s)")
CLI_QUOTA_RESUME_AUTHORIZE_MAX_RESUMES = (
    "quota-resume-authorize: max_resumes must be an integer "
    ">= 0, got %r")

# —— v24-compile ——

CLI_V24_COMPILE_READ_ERROR = "v24-compile: cannot read DAG JSON %r: %s"
CLI_V24_COMPILE_PARSE_ERROR = "v24-compile: DAG JSON parse failed (%s): %s"
CLI_V24_COMPILE_TOP_NOT_OBJECT = (
    "v24-compile: DAG JSON top level must be a JSON object, "
    "got %s")
CLI_V24_COMPILE_NODES_NOT_ARRAY = "v24-compile: nodes must be an array, got %s"
CLI_V24_COMPILE_OWNERSHIP_REJECTED = "v24-compile: ownership stage planning rejected: %s"
CLI_V24_COMPILE_REJECTED = "v24-compile: %s"

# —— writer-* / v24-record-run 包装 ——

CLI_WRITER_ACQUIRE_REJECTED = "writer-acquire: %s"
CLI_WRITER_RELEASE_REJECTED = "writer-release: %s"
CLI_V24_RECORD_RUN_REJECTED = "v24-record-run: %s"

# —— workflow-model-select ——

CLI_WORKFLOW_MODEL_SELECT_PARAM_NOT_JSON = (
    "workflow-model-select: argument %r is neither an inline JSON "
    "array nor readable as a JSON file path: %s")
CLI_WORKFLOW_MODEL_SELECT_FILE_NOT_JSON = (
    "workflow-model-select: model manifest file %r is not valid "
    "JSON: %s")
CLI_WORKFLOW_MODEL_SELECT_NOT_ARRAY = (
    "workflow-model-select: model manifest must be a JSON array "
    "(a set of model id strings); top level of %r is %s")
CLI_WORKFLOW_MODEL_SELECT_TASK_MISSING = (
    "workflow-model-select: task %s does not exist (no "
    "state.json under %s); cannot consume the task pin")
CLI_WORKFLOW_MODEL_SELECT_TASK_LEGACY = (
    "workflow-model-select: task %s is a v2.3 legacy task; "
    "cannot consume the task pin: %s")
CLI_WORKFLOW_MODEL_SELECT_PIN_DRIFT = (
    "workflow-model-select: the worker_model pin of task %s has "
    "an invalid shape (ledger drift: %r)"
    " — the task pin is void; re-run the launch interview for "
    "authorization")
CLI_WORKFLOW_MODEL_SELECT_PIN_INVALID = (
    "workflow-model-select: the worker_model "
    "pin of task %s has an invalid shape — the task pin is void; "
    "re-run the launch interview for authorization"
    " (%s)")
CLI_WORKFLOW_MODEL_SELECT_PIN_NOT_CONFIGURED = (
    "workflow-model-select: the worker_model "
    "pin of task %s, %r, is not in this run's configured set "
    "(%d models in total)"
    " — the task pin is void; re-run the launch interview for "
    "authorization; never fall back to automatic selection, "
    "never switch models silently")
CLI_WORKFLOW_MODEL_SELECT_MODEL_PIN_MISMATCH = (
    "workflow-model-select: --model %r does not match the "
    "worker_model pin of task %s, %r"
    " — for pinned tasks the explicit --model value must equal "
    "the pin"
    " (a plain --model must not bypass the task pin); to change "
    "the authorization, re-authorize through the interactive "
    "interview and update the task pin with worker-model-pin")
CLI_WORKFLOW_MODEL_SELECT_NO_PIN_NO_MODEL = (
    "workflow-model-select: task %s has no worker_model pin and "
    "no explicit --model value was given — task-level worker "
    "selection must be explicitly authorized through the launch "
    "interview (orchestration §6.0, question 2) and persisted "
    "with worker-model-pin; "
    "never select automatically, never treat a single candidate "
    "as implicit authorization")

# —— worker-model-pin / task-create / record-validation ——

CLI_WORKER_MODEL_PIN_REJECTED = "worker-model-pin: %s"
CLI_TASK_MISSING_WORKER_MODEL_PIN = (
    "Task %s does not exist (no state.json under %s); cannot "
    "write the worker model pin")
CLI_TASK_CREATE_GOAL_EMPTY = "task-create: goal must be a non-empty string, got %r"
CLI_TASK_CREATE_MODE_INVALID = "task-create: mode %r is not in the allowed values (%s)"
CLI_TASK_CREATE_ASSURANCE_INVALID = "task-create: assurance %r is not in the allowed values (%s)"
CLI_TASK_CREATE_DAG_READ_ERROR = "task-create: cannot read DAG JSON %r: %s"
CLI_TASK_CREATE_DAG_PARSE_ERROR = "task-create: DAG JSON parse failed (%s): %s"
CLI_TASK_CREATE_DAG_TOP_INVALID = (
    "task-create: DAG JSON top level must be a node array or a "
    "JSON object containing a nodes array; top level of %r is %s")
CLI_TASK_CREATE_DAG_ERRORS = "task-create: %s"
CLI_RECORD_VALIDATION_STATUS_INVALID = (
    "record-validation: status %r is not in the allowed values (%s)")
CLI_RECORD_VALIDATION_SUMMARY_EMPTY = (
    "record-validation: summary must be a non-empty string "
    "(omit for None)")
CLI_RECORD_VALIDATION_COMMANDS_EMPTY = (
    "record-validation: every item of commands must be a "
    "non-empty string"
    " (--commands takes exactly one command each time)")

# —— 终态薄壳包装与意外兜底 / 规范 run 注册 ——

CLI_TASK_REJECTED_WRAP = "%s: %s"
CLI_TASK_RECORD_WORKFLOW_RUN_RUN_ID_EMPTY = (
    "task-record-workflow-run: run-id must be a non-empty "
    "string, got %r")
CLI_UNEXPECTED_ERROR = "%s: %s"

# ============================================================
# —— runtime/task.py：生命周期拒绝与决策 reason ——
# ============================================================

# —— 公共助手 ——

TASK_MISSING = (
    "Task %s does not exist (no state.json); cannot perform the "
    "lifecycle operation")
TASK_LEGACY_LIFECYCLE = (
    "Task %s is a v2.3 legacy task; the v2.4 lifecycle layer "
    "refuses to operate on it: %s")
TASK_TERMINAL_FROZEN = (
    "%s: task %s is already in terminal state %r; the ledger is "
    "frozen (to reopen, go through state.reopen_task)")
TASK_TRANSITION_NO_TARGETS = (
    "none (terminal states accept no transitions; to reopen "
    "explicitly, go through state.reopen_task)")
TASK_TRANSITION_DISALLOWED = (
    "%s: status transition %r → %r is not allowed (allowed "
    "targets of %s: %s; "
    "see state.TASK_TRANSITIONS for the full transition table)")

# —— 创建 / 读取 ——

TASK_CREATE_ALREADY_EXISTS = (
    "create_task: task %s already exists (state.json already on "
    "disk); refusing to recreate (an existing ledger is never "
    "overwritten)")
TASK_CREATE_STATE_INVALID = "create_task: task state is invalid; refusing to persist: %s"
TASK_LOAD_LEGACY = "Task %s is a v2.3 legacy task: %s"

# —— 委派 run 关联 ——

TASK_RECORD_RUN_ID_EMPTY = (
    "record_workflow_run: workflow_run_id must be a non-empty "
    "string, got %r")
TASK_RECORD_RUN_NO_RESERVATION = (
    "record_workflow_run: registering a delegated run requires "
    "holding this repository's writer guard first "
    "(missing writer reservation) — task %s currently holds no "
    "write reservation; acquire one via host-side writer-acquire "
    "before registering")
TASK_RECORD_RUN_GUARD_HELD_BY_OTHER = (
    "record_workflow_run: this repository's writer guard is "
    "currently held by another task "
    "(task_id=%r, workflow_run_id=%r); task %s must not register "
    "a delegated run")
TASK_RECORD_RUN_GUARD_RACE = (
    "record_workflow_run: while attaching the run id, this "
    "repository's writer guard became held by another task "
    "(task_id=%r, workflow_run_id=%r); refusing "
    "this registration")

# —— 任务级验证 / 审查 ——

TASK_REVIEW_NO_VALIDATION = (
    "record_review: task %s has no validation record yet; "
    "refusing to record the review "
    "(validate before review; run record_validation first)")
TASK_RECORD_VALIDATION_STATUS_INVALID = (
    "record_validation: status %r is not in the allowed values (%s)")
TASK_RECORD_VALIDATION_SUMMARY_INVALID = (
    "record_validation: summary must be a non-empty string or "
    "None, got %r")
TASK_RECORD_VALIDATION_COMMANDS_TYPE = (
    "record_validation: commands must be an array of strings or "
    "None, "
    "got %s")
TASK_RECORD_VALIDATION_COMMAND_ITEM_EMPTY = (
    "record_validation: every item of commands must be a "
    "non-empty string")
TASK_RECORD_REVIEW_REVIEWER_EMPTY = (
    "record_review: reviewer must be a non-empty string, got %r")
TASK_RECORD_REVIEW_VERDICT_INVALID = (
    "record_review: verdict %r is not in the allowed values (%s)")
TASK_RECORD_REVIEW_FINDINGS_INVALID = (
    "record_review: findings must be a non-empty string or None, "
    "got %r")

# —— 状态 / phase 推进 ——

TASK_SET_STATUS_INVALID = "set_status: status %r is not in the allowed values (%s)"
TASK_SET_PHASE_INVALID = "set_phase: phase %r is not in the allowed values (%s)"

# —— 额度等待 / 恢复 ——

TASK_QUOTA_VIEW_NOT_DICT = (
    "quota_view must be a normalized observation JSON object "
    "(with status/reset_at/"
    "source/observed_at), got %s")
TASK_QUOTA_VIEW_STATUS_INVALID = "quota_view.status %r is not in the allowed values (%s)"
TASK_AUTHORIZE_MAX_RESUMES_INVALID = (
    "authorize_quota_resume: max_resumes must be an integer "
    ">= 0, "
    "got %r")
TASK_AUTHORIZE_STATUS_INVALID = (
    "authorize_quota_resume: task %s is in %r; authorization "
    "requires the task "
    "to be waiting_quota or active")
TASK_AUTHORIZE_BUDGET_BELOW_COUNT = (
    "authorize_quota_resume: max_resumes %d must not be less "
    "than the used "
    "resume_count %r (budget invariant: resume_count <= "
    "max_resumes)")

# —— 定时唤醒决策（decision.reason 文案） ——

TASK_DECISION_NOT_WAITING = (
    "Task status is %r, not waiting_quota (a duplicate wake is "
    "safe; "
    "no action is taken)")
TASK_DECISION_EXHAUSTED = "Quota observation is %s; keep waiting"
TASK_DECISION_UNKNOWN = "Quota observation is UNKNOWN; do not fabricate availability, keep waiting"
TASK_DECISION_NOT_AUTHORIZED = (
    "quota_resume.mode=%r (automatic resume not authorized); "
    "waiting for the user"
    " to explicitly authorize_quota_resume")
TASK_DECISION_BUDGET_EXHAUSTED = (
    "Automatic resume budget is exhausted (resume_count=%d >= "
    "max_resumes=%d); the task moves to waiting_user for the "
    "user to handle")
TASK_DECISION_CONTINUITY_UNARMED = (
    "continuity-unarmed: auto continuity is not armed "
    "(quota_resume.automation_id=%r); the witness of a future "
    "scheduled "
    "activation is missing, so resume-authorized is never "
    "issued; the task stays "
    "waiting_quota with zero budget consumed — this is an "
    "operational-liveness gap, not "
    "authorization exhaustion; first create the scheduled "
    "activation host-side and "
    "bind it with bind_quota_automation, then confirm ready "
    "via quota_continuity_preflight")
TASK_DECISION_NO_RUN = (
    "scheduled_activation_decision: task %s has no associated "
    "workflow_run_id; there is no run to resume, refusing to "
    "issue resume-authorized")
TASK_DECISION_RESUME_AUTHORIZED = (
    "Quota is available (%s), authorized, with budget remaining "
    "(%d/%d); the count is only "
    "staged — after the host resume call is accepted, call "
    "confirm_resume_started to commit it")

# —— 恢复落账确认 ——

TASK_CONFIRM_NOT_WAITING = (
    "confirm_resume_started: task %s is in %r, not "
    "waiting_quota"
    " — no staged resume decision to commit (duplicate "
    "confirmation is safe: after confirmation the task has "
    "already moved to active)")
TASK_CONFIRM_NOT_AUTHORIZED = (
    "confirm_resume_started: task %s has quota_resume.mode=%r; "
    "automatic resume is not authorized, refusing to commit")
TASK_CONFIRM_COUNT_SHAPE = (
    "confirm_resume_started: the quota_resume counters of task "
    "%s have an invalid shape "
    "(resume_count=%r / max_resumes=%r); refusing to commit")
TASK_CONFIRM_BUDGET_EXHAUSTED = (
    "confirm_resume_started: the budget of task %s is exhausted "
    "(resume_count="
    "%d >= max_resumes=%d); no staged decision to commit")

# —— auto 连续性武装 / 清理 ——

TASK_BIND_STATUS_INVALID = (
    "bind_quota_automation: task %s is in %r; binding requires "
    "the task to be in "
    " %s (terminal states never gain new binding facts; terminal "
    "cleanup goes through clear_quota_"
    "automation)")
TASK_BIND_CONFLICT = (
    "bind_quota_automation: task %s is already bound to "
    "automation_id %r; "
    "refusing to silently replace it with %r (a replacement must "
    "first be reconciled through the host-side lifecycle, then "
    "follow the "
    "explicit clear_quota_automation → bind_quota_automation "
    "sequence)")
TASK_BIND_ID_EMPTY = (
    "bind_quota_automation: automation_id must be a non-empty "
    "string, "
    "got %r")
TASK_CLEAR_MISMATCH = (
    "clear_quota_automation: the given automation_id %r does not "
    "match the existing binding of task %s, %r"
    "; refusing to clear (exact reconciliation; pass None when "
    "there is no expectation to unconditionally clear the "
    "existing binding)")

# —— 连续性预检（reason 为规格 §4.1 给出的英文短句） ——

TASK_PREFLIGHT_MANUAL = "manual mode does not require scheduled activation"
TASK_PREFLIGHT_ARMED = "auto continuity armed"
TASK_PREFLIGHT_UNARMED = "auto continuity is not armed"

# —— worker 模型 pin / 终态收尾 ——

TASK_PIN_REJECTED = "pin_worker_model: %s"
TASK_FINISH_POST_COMMIT_MISMATCH = (
    "%s: after the state transaction committed, task %s is in "
    "%r, not the target terminal state %r; refusing to "
    "append events and release the guard")

# ============================================================
# —— runtime/state.py：盘面拒绝消息 ——
# ============================================================

# —— legacy 标识归一 ——

STATE_NORMALIZE_NOT_DICT = (
    "Task identity normalization failed: the input must be a "
    "JSON object")
STATE_NORMALIZE_MISSING_KEY = (
    "Task identity normalization failed: missing task identity "
    "key (%s)")
STATE_NORMALIZE_KEY_MISMATCH = (
    "Task identity normalization failed: multiple identity keys "
    "disagree (%s=%r vs %s=%r)")
STATE_NORMALIZE_EMPTY_VALUE = (
    "Task identity normalization failed: the value of %s must be "
    "a non-empty string")

# —— 校验（validate_state 及其子校验器） ——

STATE_ENUM_ERROR = "%s %r is not in the allowed values (%s)"
STATE_STR_LIST_NOT_ARRAY = "%s must be an array"
STATE_STR_LIST_ITEM_EMPTY = "%s[%d] must be a non-empty string"
STATE_ROUTE_NOT_OBJECT = "route must be a JSON object"
STATE_REPOSITORY_NOT_OBJECT = "repository must be a JSON object"
STATE_REPOSITORY_ROOT_EMPTY = "repository.root must be a non-empty string"
STATE_DAG_NOT_ARRAY = "dag must be an array"
STATE_VALIDATION_NOT_OBJECT = "validation must be a JSON object"
STATE_VALIDATION_FIELD_INVALID = "validation.%s must be a non-empty string or null"
STATE_REVIEW_NOT_OBJECT = "review must be a JSON object"
STATE_REVIEW_FIELD_INVALID = "review.%s must be a non-empty string or null"
STATE_QUOTA_RESUME_NOT_OBJECT = "quota_resume must be a JSON object"
STATE_QUOTA_RESUME_COUNT_INVALID = "quota_resume.%s must be an integer >= 0"
STATE_QUOTA_RESUME_BUDGET_INVARIANT = (
    "quota_resume.resume_count %d must not exceed quota_resume."
    "max_resumes %d (budget invariant: resume_count <= "
    "max_resumes)")
STATE_QUOTA_RESUME_AUTOMATION_ID_INVALID = (
    "quota_resume.automation_id must be a non-empty string or null")
STATE_WORKER_MODEL_INVALID = "worker_model must be a non-empty string or null"
STATE_AUTO_APPROVE_INVALID = (
    "auto_approve_workflow must be a boolean (true or false)")
STATE_NOT_OBJECT = "state must be a JSON object"
STATE_MISSING_REQUIRED_KEY = "Missing required top-level key %s"
STATE_TASK_ID_EMPTY = "task_id must be a non-empty string"
STATE_TASK_ID_FORMAT = (
    "task_id %r does not match format ^[A-Za-z0-9][A-Za-z0-9-]*$"
    " (must start with an alphanumeric and contain only "
    "alphanumerics and hyphens)")
STATE_GOAL_EMPTY = "goal must be a non-empty string"
STATE_WORKFLOW_RUN_ID_INVALID = "workflow_run_id must be a non-empty string or null"

# —— 状态迁移门 ——

STATE_TRANSITION_NO_TARGETS = (
    "none (terminal states accept no transitions; to reopen "
    "explicitly, go through reopen_task)")
STATE_TRANSITION_DISALLOWED = (
    "status transition refused: %r → %r is not allowed "
    "(allowed targets of %s: %s; "
    "see TASK_TRANSITIONS for the full transition table)")

# —— per-task 状态事务（结构性拒绝——分类锚见
#     STRUCTURAL_STATE_ERROR_PREFIXES，两处必须逐字同步） ——

STATE_TX_TASK_MISSING = (
    "state transaction: task %s does not exist (no state.json); "
    "refusing to read or write")
STATE_TX_ID_MISMATCH = (
    "state transaction: on-disk state.json normalized task id "
    "%r does not match target %r"
    "; refusing to write to a misaligned ledger")
STATE_TX_SCHEMA_INVALID = (
    "state transaction: on-disk state.json schema is invalid; "
    "refusing to write on top of it "
    "(corruption is never overwritten): %s")
STATE_SAVE_INVALID = "state is invalid and cannot be saved: %s"
STATE_TX_NEW_TASK_ID_MISMATCH = (
    "state transaction: new state.task_id %r does not match the "
    "transaction target %r; refusing "
    "to commit (P1-9: directory and content task ids must agree)")
STATE_TX_MUTATE_RETURN_INVALID = (
    "update_task_state: mutate must return the complete state "
    "dict, got %s")
STATE_SAVE_DIR_ID_MISMATCH = (
    "save_state: directory task_id %r does not match "
    "state.task_id %r "
    "(P1-9: directory and content task ids must agree; to "
    "migrate, rebuild the directory under the content ID "
    ")")
STATE_LOAD_CORRUPT = "state.json is corrupt and cannot be parsed (%s): %s"

# —— 重开 / 仓库根绑定 / 构造 ——

STATE_REOPEN_NOT_TERMINAL = (
    "reopen_task: the status of task %s is %r; a non-terminal "
    "task needs no reopen "
    "(for migration go through transition_task_status)")
STATE_BIND_REPO_ROOT_INVALID = (
    "bind_repository_root: repo_root must be a non-empty string "
    "path, got %r")
STATE_NEW_TASK_ROUTE_TYPE = (
    "new_task_state: route must be a dict (the v2.4 route "
    "carries mode/"
    "assurance), got %s")
STATE_NEW_TASK_DAG_NODES_TYPE = (
    "new_task_state: dag_nodes must be a list or tuple, got %s")

# —— 发现（corrupt / orphaned 桶 reason） ——

STATE_DISCOVER_CORRUPT = "state.json is corrupt: %s"
STATE_DISCOVER_UNREADABLE = "state.json is unreadable: %s"
STATE_DISCOVER_NO_STATE = "no state.json"

# ============================================================
# —— runtime/writer_guard.py：结构校验消息 ——
# ============================================================

WRITER_GUARD_TASK_ID_INVALID = (
    "writer_guard: task_id must be a non-empty string (supplied "
    "by the caller), got %r")
WRITER_GUARD_RUN_ID_INVALID = (
    "writer_guard: workflow_run_id must be None or a non-empty "
    "string, got %r")


# ============================================================
# —— v2.7.0 W1/W2：唤醒载体四级降级 + 免确认面单源常量 ——
# ============================================================

# bind 同 id 换 kind 的拒绝文案（runtime/task.py bind_quota_automation；
# 原 B4 节点暂置 task.py，批次集成按单源纪律上移至此，值逐字节不变）
TASK_BIND_KIND_CONFLICT = (
    "bind_quota_automation: task %s is already bound to automation_id"
    " %r with automation_kind %r; refusing to silently rebind the"
    " same witness with kind %r (the carrier kind is never switched"
    " silently; rebinding under another carrier kind goes through the"
    " explicit clear_quota_automation → bind_quota_automation sequence)")

# carrier 三子命令 USAGE 段（CLI_USAGE 主串缀尾，cli.py USAGE 直读单源）
CLI_USAGE_CARRIER_EXTENSION = (
    " | os-carrier-install <repo_root> <task_id> "
    "[--session <id>] [--interval <hours>] [--prompt <s>] | "
    "os-carrier-remove <repo_root> <task_id> | "
    "wake-carrier-probe <repo_root>")

CLI_USAGE_OS_CARRIER_INSTALL = (
    "os-carrier-install requires <repo_root> <task_id> [--session <id>] "
    "[--interval <hours>] [--prompt <s>]: two to eight arguments "
    "(flags come in pairs).")
CLI_USAGE_OS_CARRIER_INSTALL_FLAGS = (
    "os-carrier-install accepts only --session <id>, --interval <hours>, "
    "and --prompt <s> as optional arguments (each at most once).")
CLI_USAGE_OS_CARRIER_REMOVE = (
    "os-carrier-remove requires <repo_root> <task_id>: two arguments.")
CLI_USAGE_WAKE_CARRIER_PROBE = (
    "wake-carrier-probe requires exactly one argument: <repo_root>")
CLI_OS_CARRIER_TASK_ID_EMPTY = (
    "os-carrier: task_id must be a non-empty string, got %r")
CLI_OS_CARRIER_SESSION_EMPTY = (
    "os-carrier-install: --session must be a non-empty string, got %r")
CLI_OS_CARRIER_SESSION_MISSING = (
    "os-carrier-install: no session id available; pass --session <id> "
    "or ensure %s exists (written by the SessionStart hook)")
CLI_OS_CARRIER_INTERVAL_INVALID = (
    "os-carrier-install: interval hours must be an integer >= 1, "
    "got %r")
