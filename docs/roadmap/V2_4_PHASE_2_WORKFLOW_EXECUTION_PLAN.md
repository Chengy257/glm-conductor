# v2.4 Phase 2 Workflow Execution Plan — Unit Specs W1-W6

> **Status:** auto-progression per user mandate 2026-09-21; Phase 1 exit gate PASSED.
> **Authority chain:** REBASELINE_SPEC → IMPLEMENTATION_PLAN §5 → `V2_4_PHASE_2_STATE_ASSURANCE_RETIREMENT_SPEC.md` → **this document**.
> **Mode:** same as Phase 1 — one native Workflow, all implementers GLM-5.3-Flash, main session analyzes/plans/accepts, local commits only, targeted tests only.

## 1. Phase-2-specific engineering constraints (from main-session analysis, 2026-09-21)

- `runtime/state.py` has 20 importers (quota ×7, continuity ×3, hooks ×2, scheduler_facts, execution_policy, commands/wake, legacy execution ×7). All access fields inside function bodies. W1 must therefore keep the module's public function signatures (loader/saver/path helpers) stable so the import graph stays safe; the schema constants + validation rules are rewritten to v2.4. Runtime degradation of Phase-3-doomed modules (quota control, continuity) against v2.4 states is accepted — they are never invoked by the v2.4 path and die in Phase 3.
- `runtime/fingerprint.py` consumers (reconcile, provenance, task_manager, stop_gate) are ALL rewritten or deleted in this phase. W2 therefore creates a NEW `runtime/change_id.py` and leaves fingerprint.py untouched until W6 deletes it — no bridge, no dual API.
- The loaded plugin in this session is still cache 2.3.2 and hooks execute from the cache copy — repo edits to hooks/hooks.json and hook scripts carry zero risk to the running session. The cache is refreshed at Phase 4 release.
- Keep-until-Phase-3 modules (NOT to delete in this phase): `quota/*`, `continuity/*`, `scheduler_facts.py`, `activation_transport.py`, `execution_policy.py`, `host/`, `runtime/commands/wake.py`, journal.py (kept as generic primitive, vocabulary shrunk in W6).
- Reviewer live-launch proof remains deferred to Phase 4 P4-E (documented default).

## 2. Unit decomposition

| Unit | Work package | Primary files (ownership) | Depends on |
| --- | --- | --- | --- |
| W1 | P2-A task-state re-baseline | `runtime/state.py` (rewrite), `tests/test_state_v24.py` | — |
| W2 | P2-B task-level change identity | `runtime/change_id.py` (new), `tests/test_change_id.py` | — |
| W3 | P2-A/C task lifecycle + minimal records | `runtime/task.py` (new), `tests/test_task_lifecycle.py` | W1, W2 |
| W4 | P2-D completion guard | `hooks/stop_gate.py` (rewrite), `tests/test_completion_guard.py` | W3 |
| W5 | P2-E recovery simplification | `runtime/recovery.py` (rewrite), `hooks/session_start.py`, `tests/test_recovery_v24.py` | W1 |
| W6 | P2-F legacy runtime retirement | deletions + `hooks/hooks.json` + `runtime/cli.py` pruning + `journal.py` vocabulary + `scripts/validate_plugin.py` sync + test deletions | W3, W4, W5 |

W1 ∥ W2 run parallel; W5 after W1; W3 after W1+W2; W4 after W3; W6 last.

## 3. Unit specs

### W1 — Task state re-baseline (P2-A)

Rewrite `runtime/state.py` around the v2.4 task shape. Required top-level concepts:
`task_id`, `goal`, `repository`, `route` (vocabulary solo/delegate/audit/full, exact), `dag`
(nodes validated via `runtime.work_unit.validate_node` + `runtime.dependency.graph_errors`),
`status`, optional descriptive `phase` (planning/workflow/validating/reviewing — no transition
matrix), `workflow_run_id` (nullable), `validation`, `review`, `quota_resume` placeholder
(`{"mode":"manual","max_resumes":0,"resume_count":0,"automation_id":null}` — Phase 3 finalizes).
Durable statuses exactly: active / waiting_quota / waiting_user / blocked / completed /
cancelled / failed. Terminal statuses cannot silently reopen (reopen requires explicit flag
that records the reopen in journal terms — no hidden mutation).

Compat rules:
- keep public loader/saver/path helper signatures unchanged (load/save/state dir/tasks dir
  naming) so all 20 importers stay import-safe;
- legacy detection: if a loaded state.json carries v2.3 markers (work_units key, permit/lease/
  receipt fields), `is_legacy_state()` returns True and load reports it as a v2.3 task with a
  short guidance string (finish/retire under 2.3.x or explicitly abandon); never fake-convert;
- no auto-migration; a read-only `detect_legacy_task()` helper is allowed.

New tests `tests/test_state_v24.py`: schema happy path; every invalid-status rejection;
route vocabulary enforcement; DAG embedded-node validation errors surface with node paths;
terminal reopen refusal; legacy detector positive/negative; save/load roundtrip atomicity
(reuse durable_io conventions already used).

Gate: `python3 -X utf8 -m unittest tests.test_state_v24` + import-chain smoke (all 20
importers still import) + validator.

### W2 — Task-level change identity (P2-B)

New `runtime/change_id.py`: `compute_change_id(repo_root, relevant_paths, base=None) ->
"sha256:..."`. Deterministic over task-relevant repository change state; ignores Conductor
bookkeeping files (`.glm-conductor/**`); implementation MAY adapt the normalization/digest
internals of `runtime/fingerprint.py` (read it, port what is needed, do not import it).
Same primitive is the ONLY freshness source for validation/review/completion (consumed via
W3/W4). No per-unit fingerprint APIs.

New tests `tests/test_change_id.py`: determinism (same tree twice → equal); changes when a
tracked path changes; unchanged when only `.glm-conductor/**` changes; base anchoring; path
list ordering does not affect the result (sorted internally).

Gate: `tests.test_change_id` + import smoke + validator.

### W3 — Task lifecycle + minimal validation/review records (P2-A/C)

New `runtime/task.py` (small task/workflow lifecycle module — the P2-F task_manager
replacement, landing early so W4/W6 build on it):
- `create_task(repo_root, task_id, goal, route, dag_nodes)` → validates + persists v2.4 state
  (writer-guard correlation happens at launch, not here);
- `load_task(repo_root, task_id)` (legacy detector pass-through);
- `record_workflow_run(repo_root, task_id, workflow_run_id)` (also mirrors into
  `runtime/workflow/adapter.record_run`);
- `record_validation(repo_root, task_id, status: passed|failed, summary=None,
  commands=None)` → computes and stores `change_id` via W2;
- `record_review(repo_root, task_id, reviewer, verdict: ship|fix-first|rethink,
  findings=None)` → stores reviewer/verdict/change_id; requires validation recorded first;
- `set_status/set_phase`, `enter_waiting_quota`, `exit_waiting_quota`, `complete`, `fail`,
  `cancel` (complete/fail/cancel emit journal events and release the writer guard via
  `runtime/writer_guard.release`);
- journal events via `runtime/journal` with the task-level vocabulary only (route_selected,
  workflow_started, workflow_reassessed, validation_recorded, review_recorded,
  waiting_quota, task_completed, task_failed, task_cancelled).
No receipts, no stdout capture requirements, no runner identity, no invocation markers.

New tests `tests/test_task_lifecycle.py`: create/load roundtrip; record_validation stores
change_id; record_review ordering rule (validation first) enforced; status transitions legal
set; complete releases writer guard; journal vocabulary emitted exactly (no host-child
lifecycle mirroring).

Gate: `tests.test_task_lifecycle` + import smoke + validator.

### W4 — Completion guard (P2-D)

Rewrite `hooks/stop_gate.py` keeping only the process I/O shell (read Stop event JSON,
emit decision JSON, stderr advisory) with gate logic replaced by four checks against the
current task (task id from stop event context as today):
1. no active write workflow: writer guard for the repo is empty, or holds a terminal-run
   correlation (guard record with workflow_run_id whose run is known terminal — treat
   guard-empty as the pass condition; non-empty → actionable message with holder ids);
2. actual changed paths (ownership.git_touched_files) ⊆ union(dag ownership scopes)
   (ownership.classify_paths); violations listed with node ids;
3. validation.status == passed AND validation.change_id == compute_change_id(now);
4. if route.assurance == high (state carries it): review.verdict == ship AND
   review.change_id == current.
Success → task.complete (status completed + guard release + journal) and pass; failure →
block with ONE concise actionable reason (most severe first). No finalizing state, no
receipt scan, no permit/lease checks, no tool-use authenticity. Also expose
`evaluate_completion(repo_root, task_id)` as an importable function the lifecycle API can
call explicitly (hook remains last-chance guard).

New tests `tests/test_completion_guard.py` (drive the importable function + hook shell):
each check failing alone produces its specific reason; all pass → completed + guard
released; stale change_id after edit → blocked; high-assurance without ship review →
blocked; guard held by other task → blocked with holder ids.

Gate: `tests.test_completion_guard` + py_compile stop_gate + validator.

### W5 — Recovery simplification (P2-E)

Rewrite `runtime/recovery.py` + adjust `hooks/session_start.py` to render, per unfinished
task ONLY: task id, goal, repository, status, route, workflow run id if any, quota-wait
state if any, next safe action (inspect workflow / resume workflow / reassess repository /
run main validation / run required review / wait for quota or user / explicitly retire
legacy task). Legacy v2.3 tasks render as one line: v2.3 task detected + retire guidance.
REMOVE: unit readiness, zombie classification, agent-run reconcile, lease reconcile,
receipt reconcile, resume manifest dependency. In `hooks/session_start.py` keep the quota
clock advisory block untouched (Phase 3 removes it); replace only the recovery rendering
path. `resume_manifest` importers must be gone from recovery (module itself dies in W6).

New tests `tests/test_recovery_v24.py`: summary shape for active/waiting_quota/waiting_user
tasks; legacy task one-liner; next-action mapping; no unit-level fields appear.

Gate: `tests.test_recovery_v24` + py_compile session_start + validator.

### W6 — Legacy execution runtime retirement (P2-F)

Delete production modules: `dispatcher.py`, `dispatch_wave.py`, `legacy_unit.py`,
`legacy_dependency.py`, `lease.py`, `agent_run.py`, `reconcile.py`, `resume_manifest.py`,
`provenance.py`, `fingerprint.py`, `task_manager.py`, `policy.py`, `hooks/pre_tool_use.py`,
`hooks/post_tool_use.py`, `agents/flash-implementer.md`.
`hooks/hooks.json`: keep ONLY SessionStart + Stop registrations.
`journal.py`: keep as generic append/read primitive; shrink event vocabulary to the
task-level set (constants only; no host-child mirroring terms).
`runtime/cli.py`: remove subcommands whose backing modules died (permits, permit-show,
permit-consume, wave-prepare, wave-show, manifest-show, agent-runs, verify-unit,
verify-task, review-record rewired to `runtime/task.record_review`); keep quota/continuity/
wake/clock subcommands untouched (Phase 3 scope). Update usage text.
`scripts/validate_plugin.py`: sync removed marker checks (dispatcher/lease/provenance/policy/
legacy twins markers) — rule 12; keep everything else.
Delete tests enforcing removed concepts: `test_dispatcher`, `test_wave_dispatch`,
`test_provenance`, `test_reconcile`, `test_resume_manifest`, `test_agent_run`,
`test_pre_tool_use`, `test_post_tool_use`, `test_task_manager`, `test_task_manager_draining`,
`test_manifest_quota_control`, `test_work_unit`, `test_dependency` (legacy twin testers),
plus any test whose only subject is permits/waves/leases/receipts (verify via grep, list
every deletion in the report). Keep-all continuity/quota tests untouched (Phase 3).
Ensure NO surviving file references the deleted modules (grep clean; docs/ and
docs/reviews historical material may keep references — code/tests/skills must not; the two
skills still referencing flash-implementer/dispatcher get a minimal one-line compatibility
note only if imports break — full skill rewrite is Phase 4, do NOT rewrite skills here).

Gate: validator + `scripts/smoke_plugin_load.py` + import-chain smoke (surviving modules) +
py_compile of the two remaining hooks + targeted survivor set
(`test_state_v24 test_change_id test_task_lifecycle test_completion_guard test_recovery_v24
test_workflow_compiler test_writer_guard test_cli_v24 test_ownership_stages
test_static_node test_dag_semantics test_agent_model_binding` + largest surviving
quota/continuity slice `test_quota_resolver test_authorized_resume`).

## 4. Workflow topology

Phases: 1) `W1 状态重置 ∥ W2 change_id` parallel; 2) `W3 任务生命周期` (needs W1+W2);
3) `W4 完成守卫 ∥ W5 恢复简化` (W4 needs W3; W5 needs W1 — both schedulable together);
4) `W6 遗留退役与收口` + final gate. Same gate/fix-loop discipline as Phase 1 (max 2 fix
rounds/unit, honest failure reporting, artifact board + markdown report, no commits inside).

## 5. Main-session acceptance (post-workflow)

1. Diff review per unit; independent re-run of the final gate list.
2. E2E smoke A (delegate): create task (delegate/standard) → `v24-compile` DAG → live
   CreateWorkflow run → `record_validation` → `evaluate_completion` passes → guard released.
3. E2E smoke B (full/high): same with a recorded review (attempt native glm-reviewer
   dispatch from this session first; if the cached 2.3.2 definition is unstartable here,
   record a fresh-context reviewer run dispatched as a plain read-only Flash agent and
   document the substitution — Phase 4 restores the native reviewer smoke).
4. Per-unit local commits + Phase 2 exit-gate record appended here.
