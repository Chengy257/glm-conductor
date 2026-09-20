# v2.4 Phase 3 Workflow Execution Plan — Unit Specs Q1-Q4

> **Status:** auto-progression per user mandate 2026-09-21; Phase 2 exit gate PASSED.
> **Authority chain:** REBASELINE_SPEC → IMPLEMENTATION_PLAN §6 → `V2_4_PHASE_3_QUOTA_RESUME_EXTRACTION_SPEC.md` → **this document**.
> **Mode:** one native Workflow, all implementers GLM-5.3-Flash, main session analyzes/plans/accepts, local commits only, targeted tests only.

## 1. Phase-3-specific facts (main-session analysis, 2026-09-21)

- Quota package inventory (23 modules): KEEP observation core — `provider.py`, `_http.py`,
  `credentials.py`, `parser.py`, `resolver.py`, `report.py`, `zai.py`, `bigmodel.py`,
  `time_utils.py`, `window_math.py`. DELETE — `control.py`, `observer.py`, `watcher.py`,
  `watcher_store.py`, `epoch.py`, `primer.py`, `accounting.py`, `scheduler.py` (reset-time
  math survives only as a minimal helper if the resume decision needs it), `clock.py`,
  `clock_store.py`, `identity.py`.
- Runtime deletions: `continuity/` (resume/subscription/wake_bridge), `execution_policy.py`,
  `scheduler_facts.py`, `activation_transport.py`, `host/`, `commands/wake.py`,
  `commands/quota_clock.py`, `commands/host.py` (subject dies with clock CLI).
- Keep-side tests: `test_quota_adapters/credentials/parser/provider/report/resolver`.
  Delete-side tests: `test_activation_transport`, `test_execution_policy`,
  `test_execution_policy_quota_control`, `test_primer`, `test_quota_clock`,
  `test_quota_clock_cli`, `test_quota_control`, `test_quota_epoch`, `test_quota_observer`,
  `test_quota_scheduler`, `test_wake_retime`, `test_watcher`, `test_watcher_store`,
  `test_authorized_resume`, `test_resume_consumption`, `test_host_check` (verify subject
  first). Adjust: `test_session_start_advisory` (clock advisory block removed).
- External quota consumers after this phase: `cli.py` (quota-resolve/report only),
  `session_start.py` (advisory removed → no quota import), `task.py` (resolver only).
- `report.py` must keep running standalone (`python3 runtime/quota/report.py [--json]`).
- The inventory document (Q2) MUST be written BEFORE any deletion (spec §7: extraction
  inventory precedes Clock-code removal) — hence Q2 runs in the first parallel phase.

## 2. Unit decomposition

| Unit | Work package | Primary files (ownership) | Depends on |
| --- | --- | --- | --- |
| Q1 | P3-B/C quota-resume finalize | `runtime/state.py` (quota_resume validation), `runtime/task.py` (waiting/resume lifecycle + scheduled-activation decision), `tests/test_quota_resume_v24.py` | — |
| Q2 | P3-F Clock extraction inventory | `docs/roadmap/GLOBAL_QUOTA_CLOCK_EXTRACTION_INVENTORY.md` (new, docs only) | — |
| Q3 | P3-A quota observation core shrink | `runtime/quota/*` | Q2 |
| Q4 | P3-D/E control-plane + continuity + clock deletion sweep | deletions + `runtime/cli.py` + `hooks/session_start.py` + validator/smoke sync + test deletions | Q3 |

## 3. Unit specs

### Q1 — Quota-resume authorization and wait/resume lifecycle (P3-B/C)

`runtime/state.py`: finalize `quota_resume` validation — mode ∈ {manual, auto};
`max_resumes` non-negative int; `resume_count` ≤ `max_resumes`; `automation_id` nullable
string; default remains manual/0/0/null; auto requires no extra field (authorization is
recorded by the explicit `authorize` call, not inferred).

`runtime/task.py` additions:
- `authorize_quota_resume(repo_root, task_id, max_resumes)` — sets mode=auto and the
  user-authorized budget (explicit user action; requires task in waiting_quota or active);
- `enter_waiting_quota` already exists — extend to record latest quota observation
  (status/reset_at/observed_at) into `quota_resume` for diagnostics;
- `scheduled_activation_decision(repo_root, task_id, quota_view)` — the idempotent
  decision primitive for scheduled turns. `quota_view` is the normalized observation
  dict {status: AVAILABLE|PRESSURE|EXHAUSTED|UNKNOWN, reset_at, source, observed_at}
  (main session obtains it from report/resolver). Returns one of:
  {action: no-op, reason} when status is not waiting_quota (safe repeated wake);
  {action: remain-waiting, reason} when EXHAUSTED/UNKNOWN (do not invent availability);
  {action: waiting-user, reason} when mode is manual or budget exhausted (task moves to
  waiting_user exactly once when budget exhausted — idempotent);
  {action: resume-authorized, workflow_run_id, resume_count_after} when usable AND
  authorized AND budget remains — the increment is STAGED but applied only by
  `confirm_resume_started(repo_root, task_id)` which the caller invokes after the host
  resume call is actually accepted; a decision that is never confirmed consumes no budget.
  Repeated decisions before confirmation are idempotent. No epoch/subscription/accounting.
- journal: waiting_quota event on enter; resume decision/confirmation events reuse the
  task-level vocabulary (add `quota_resume_confirmed` if journal constants need it).

New tests `tests/test_quota_resume_v24.py`: manual default never authorizes; authorize
then decision usable → resume-authorized with correct staged count; decision without
confirmation repeated ×3 → still zero consumed; confirm → count 1; budget exhausted →
waiting_user (once, idempotent); status not waiting_quota → no-op; EXHAUSTED/UNKNOWN →
remain-waiting; validation errors (negative max, count>max, bad mode).

Gate: `python3 -X utf8 -m unittest tests.test_quota_resume_v24 tests.test_task_lifecycle
tests.test_state_v24` + validator.

### Q2 — Global Quota Clock extraction inventory (P3-F, docs-only)

Create `docs/roadmap/GLOBAL_QUOTA_CLOCK_EXTRACTION_INVENTORY.md`. Read the LIVE tree
(deletions have not happened yet — this unit runs first for exactly that reason) and
classify:
- Candidate to migrate/reuse conceptually: provider quota adapter behavior
  (`quota/provider.py`, `zai.py`, `bigmodel.py`), five-hour reset calculation
  (`window_math.py`, `time_utils.py`), Global Clock target-time decision logic
  (`quota/clock.py`), host scheduler observations/constraints (`scheduler_facts.py`,
  `host/zcode_schedule.py` — cite the W0 finding that scheduled turns are mid-turn
  continuations and closed-app firing is unverified), Window Primer behavior
  (`quota/primer.py`), clock CLI UX worth keeping (`commands/quota_clock.py`,
  `runtime/cli.py` clock subcommands).
- Must NOT migrate: Conductor task ids, Work Unit/DAG state, task subscription,
  task wake bridge, task resume budget, review/validation state, repository ownership,
  task journal semantics.
- Required warning: the new project must independently reassess whether direct SQLite
  retiming is still justified; it must not inherit that implementation merely because
  v2.3 used it (cite `host/zcode_schedule.py`).
- One paragraph of observed host facts from the v2.3 dogfood record
  (docs/reviews + memory of clock re-homing) if present in repo docs; otherwise mark
  "not in repo, held in project memory".

No code changes. Gate: file exists, ≥60 lines, all three sections present (validator).

### Q3 — Quota observation core shrink (P3-A)

`runtime/quota/`: keep `provider.py`, `_http.py`, `credentials.py`, `parser.py`,
`resolver.py`, `report.py`, `zai.py`, `bigmodel.py`, `time_utils.py`, `window_math.py`,
`__init__.py`. Delete `control.py`, `observer.py`, `watcher.py`, `watcher_store.py`,
`epoch.py`, `primer.py`, `accounting.py`, `scheduler.py`. If Q1's decision helper or
`report.py` needs reset-time math currently living in `scheduler.py`, move the minimal
pure helper into `time_utils.py` first (behavior-preserving), then delete the source
module. `__init__.py` and kept modules: remove imports of deleted modules; execution-phase
semantics (NORMAL/PRESSURE/DRAINING/BLOCKED, dispatch_budget, allow_new_wave,
allow_finish_current) must not survive anywhere in the package. `report.py` must still run
standalone (`python3 runtime/quota/report.py --json` produces the normalized window
report; consumers must not require epoch/subscription fields). No normalized-answer shape
change beyond removing control-plane fields.

Gate: `python3 -X utf8 -m unittest tests.test_quota_parser tests.test_quota_provider
tests.test_quota_adapters tests.test_quota_credentials tests.test_quota_resolver
tests.test_quota_report` + standalone report run + import chain
(quota kept modules + runtime.task + runtime.cli) + validator.

### Q4 — Control-plane, continuity, and clock deletion sweep (P3-D/E)

Delete: `runtime/continuity/` (whole), `runtime/execution_policy.py`,
`runtime/scheduler_facts.py`, `runtime/activation_transport.py`, `runtime/host/`,
`runtime/commands/wake.py`, `runtime/commands/quota_clock.py`,
`runtime/commands/host.py` (verify its subject dies with the clock CLI; if it has
surviving generic content, minimize instead of deleting and report),
`runtime/quota/clock.py`, `runtime/quota/clock_store.py`, `runtime/quota/identity.py`.
`runtime/cli.py`: remove subcommands whose backing died — policy-show, policy-set-parallel,
policy-set-resume, quota-observe, quota-phase, quota-exhausted, quota-resume, wake-record,
wake-arm, wake-prompt, wake-plan, wake-status, wake-retime, wake-reconcile,
transport-status, quota-watcher, quota-clock-plan, quota-clock-bind, quota-clock-tick
(+any other clock/wake subcommand found by grep). Keep: quota-resolve, quota-report
(standalone runner), v24-*, writer-*, review-record. Usage text sync.
`hooks/session_start.py`: remove the quota clock advisory block entirely (recovery
rendering stays). Tests: delete the Phase-3 delete-side list from §1 (verify
`test_host_check`'s subject first — if it tests host-check beyond the clock, trim instead
and report); adjust `test_session_start_advisory` for the removed advisory; keep-side
quota tests untouched. Validator/smoke sync per rule 12 (clock/policy/continuity anchors).
Zero-residue grep for deleted module names across code/tests/skills/commands (docs and
docs/reviews historical material exempt).

Gate: survivor set
(`test_state_v24 test_change_id test_task_lifecycle test_quota_resume_v24
test_completion_guard test_recovery_v24 test_workflow_compiler test_writer_guard
test_cli_v24 test_ownership_stages test_static_node test_dag_semantics
test_agent_model_binding test_ownership test_quota_parser test_quota_provider
test_quota_adapters test_quota_credentials test_quota_resolver test_quota_report
test_session_start_advisory`) + validator + smoke load + import chain (all survivors) +
py_compile session_start/stop_gate.

## 4. Workflow topology

phase 1: `Q1 配额恢复语义 ∥ Q2 Clock 拆离清单`（Q2 必须在任何删除前完成——文档先行）;
phase 2: `Q3 观测核收缩`; phase 3: `Q4 控制面与连续性删除收口` + final gate.
Same gate/fix-loop discipline; board `phase3-units`; markdown report `phase3-report`.

## 5. Main-session acceptance (post-workflow)

1. Diff review; independent final-gate re-run; zero-residue grep; report.py standalone.
2. Live smoke: waiting_quota decision chain — create task, enter_waiting_quota, decision
   no-op/remain-waiting/resume-authorized/confirm round-trip via task API (no host Cron
   needed for the API-level proof; the real scheduled-task wake path is the already-active
   until_done automation pattern).
3. Per-unit local commits; Phase 3 exit-gate record appended here; proceed to Phase 4.

## 6. Phase 3 exit record (2026-09-21, main-session acceptance)

Implementation: workflow `dwfrun-b83e5466` superseded mid-run by `dwfrun-e7f693c2`
(main-session gate-path typo in the report.py standalone gate fixed via AmendWorkflow —
finished Q1/Q2/Q3 work imported as cache, zero re-payment; the rejected alternative was a
repo-root forwarding shim, refused as production pollution). 4/4 units done, all gates
round 1 on the revised run; two Q3 escalations adjudicated (validator scheduler-anchor
sync per rule 12; word-check semantics: json mode asserts five_hour, text mode asserts
5-hour:, weekly never fabricated on lite plans).

Independent re-verification (all green): 21-module survivor suite; report.py standalone
JSON run from plugin root; validator 15/15; smoke load 0 failures; post-deletion import
chain; hooks py_compile; zero-residue grep (remaining hits are verbatim-migration
provenance comments, docstring history notes, and skills/continuity content — the latter
is Phase 4 rewrite scope). Q2 inventory reviewed: written before deletions with line-
number citations and the anchor-provider-reset-at principle captured.

Live decision-chain smoke (task API, no host Cron needed for the API-level proof):
EXHAUSTED → remain-waiting; AVAILABLE+authorized → resume-authorized (staged count 1,
correct run id); 3× unconfirmed decisions → resume_count still 0; confirm → count 1 +
status active; budget exhausted → waiting_user exactly once; repeat → no-op; cancel clean.

Commits (local only): `dc5281a` Q1, `86e95a0` Q2, `8b0e9de` Q3, `fea2652` Q4.
Net this phase: +~1k/−~34k lines. Runtime reduced to 15 modules; quota package to 11
observation modules; hooks unchanged at SessionStart+Stop (advisory block removed).

Exit gate checklist (spec §9): no resident quota process — PASS; no epoch/subscription/
accounting — PASS; no Global Clock in dependency graph — PASS; no scheduler SQLite write —
PASS (host/ deleted); waiting task resumable via native scheduled path when authorized —
PASS (decision primitive live-proven; host-side resume pattern exercised by this campaign's
until_done automation); auto resume bounded and user-authorized — PASS; extraction
inventory complete — PASS.

**Phase 3 exit gate: PASSED. Phase 4 authorized to proceed.**
