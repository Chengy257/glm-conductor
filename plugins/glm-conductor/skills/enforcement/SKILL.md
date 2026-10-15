---
name: enforcement
description: Completion-guard contract — Stop-hook four checks, change_id freshness, ENFORCEMENT DEGRADED fail-open, recovery. Use when asked why a finish was blocked, what change_id staleness or a degradation message means, or whether the guard is active.
---

# GLM Completion Guard: Deterministic Acceptance Contract

This skill only **explains** the guard's runtime contract; it implements no enforcement logic — enforcement is executed deterministically by the plugin hooks (`hooks/`) and the runtime modules (`runtime/`).

Division of responsibilities (frozen as of v2.4): GLM Conductor owns semantic orchestration and acceptance; ZCode owns execution orchestration. Only two deterministic enforcement points remain on the Conductor side:

1. **Stop completion guard** (hook `hooks/stop_gate.py`) — the only legitimate channel for task completion;
2. **Repository writer guard** (repository-level write reservation in `runtime/writer_guard.py`, operated via the CLI `writer-acquire/release/show`) — at most one active Conductor write Workflow per Git repository. The writer guard is a Conductor lifecycle invariant, enforced at two points: delegated run registration (the `record_workflow_run` precondition) and completion (completion-guard check 1). Bare host Workflow invocations outside the protocol (CreateWorkflow that bypasses the Conductor lifecycle) are outside this guarantee.

Dispatch-surface hooks (implementer-dispatch interception, Bash command classification, dispatch-time injection) were fully retired along with the v2.3 execution surface: delegated implementation no longer has hook-based pre-interception, and the completion guard is the only deterministic line of defense. Tasks that bypass the process (out-of-scope changes, unvalidated, unreviewed) are stopped by the guard at finish time.

## Environment self-check (run first)

Before answering "is the guard active", self-check:

1. `python3 --version` produces output (the hook interpreter is on PATH; on Windows, if only `python` exists, create a `python3.exe` hard-link alias)
2. `echo '{}' | python3 ~/.zcode/cli/plugins/cache/glm-conductor/glm-conductor/<version>/hooks/stop_gate.py` exits 0 (the hook runs standalone; with no active task it prints nothing)
3. Hooks **take effect only in a new session after the plugin is installed/updated** — if the current session predates the plugin update, answer "not mounted in this session; effective from the next new session"

## Which hooks run

`hooks/hooks.json` (shipped with the plugin, enabled automatically after installation):

| Event | Script | Timeout | Nature |
| --- | --- | --- | --- |
| SessionStart (matcher `startup\|clear\|compact`) | `hooks/session_start.py` | 5s | Resume-context injection (fail-open, see the continuity skill) |
| Stop | `hooks/stop_gate.py` | 5s | Completion-guard four checks (block decision) |

Both are `python3` process entry points.

## Completion-guard trigger domain

When Stop fires, the guard evaluates only tasks **inside the trigger domain**; outside the domain it does not intervene at all:

- **In scope**: among the active tasks discovered from the task-ledger root (`.glm-conductor/tasks/`), the v2.4 tasks with `status == "active"`
- **Parking states pass**: `waiting_quota` / `waiting_user` / `blocked` are intentional stopping points — no finish happens on this turn, so always pass
- **Terminal tasks**: completed / cancelled / failed have nothing to evaluate — pass
- **Legacy v2.3 tasks**: pass with one line of disposition guidance (`LEGACY_STATE_GUIDANCE`: "v2.3 legacy task: wrap up or explicitly abandon it under 2.3.x (v2.4 does not auto-migrate and never fabricates v2.4 completion evidence)") — never pretend completion can be judged
- **Corrupt / orphaned task directories**: do not participate in interception; only a one-line stderr notice
- **No active tasks**: pass silently; zero intervention in ordinary sessions

## Four-check completion guard (in order, first failure returns)

The guard runs four checks in a fixed order and commits completion only when all four pass; any failure blocks with a single most-severe, actionable reason (check order is severity order):

| # | Check | Pass condition | Failure form |
| --- | --- | --- | --- |
| 1 | Repository writer guard | No repository write reservation exists, or the holder is this task itself (this task's Workflow reservation is released only at terminal finish — the completion guard itself is the release point) | Held by another task: block and report the holder's task_id and workflow_run_id verbatim; a registered delegated run (workflow_run_id non-empty) with the guard missing: block (missing writer reservation, naming the task and the run id) — with no run id (solo/audit or an unregistered delegated run), a missing guard still passes |
| 2 | ownership | All actually changed paths (git touched) fall within the union of the ownership scopes of all DAG nodes | Out-of-scope paths are listed one by one, together with the DAG node ids that participated in the comparison |
| 3 | Main-session validation | `validation.status == "passed"` and `validation.change_id` equals the current change_id | Not validated / validation record stale |
| 4 | Independent review | Checked only when `route.assurance == "high"`: `review.verdict == "ship"` and `review.change_id` equals the current change_id | Not reviewed / verdict not ship / verdict stale; standard-assurance tasks skip this check |

- **All four checks pass → the guard finishes in place**: it executes `runtime.task.complete` (status=completed + journal `task_completed` event + automatic release of the repository writer guard) and allows the stop
- **Any failure → block**: a single-line decision JSON on stdout asks the model to keep working; task state is unchanged; after fixing, Stop again
- **fix-first / rethink never satisfies completion**; `standard`-assurance tasks carry no independent-review obligation
- The same decision logic is exposed as an importable function via `hooks.stop_gate.evaluate_completion(repo_root, task_id)` — the lifecycle-API side (main session) can explicitly self-check before Stop

## change_id freshness (the core of stale detection)

Both validation and review records are bound to the repository change state at recording time; the anchor is the task-level `change_id` (`runtime/change_id.py`):

- **Computation**: `compute_change_id` = sha256 of "baseline revision + normalized content state of the relevant changed file set". The relevant changed file set = `runtime.task.relevant_changed_files` (the union of the ownership scopes of all DAG nodes ∩ actual git working-tree changes) — all three sites (record_validation / record_review / the guard comparison) share the same derivation and the same implementation, and **bypass recomputation is forbidden**: a value derived under a different definition can never pass the comparison; adding / deleting / modifying / renaming owned files, or a change in the file set, changes the identifier; changes outside the scope are intercepted by the ownership check and are irrelevant to freshness; `.glm-conductor/` bookkeeping is excluded at both layers
- **Staleness semantics**: any relevant file added / deleted / content-changed / baseline-changed alters the current change_id — existing validation or review records go stale immediately. This is the automatic enforcement of "any fix invalidates prior validation/review": after fix-first / rethink repairs, re-validation is mandatory (re-record validation), and assurance: high additionally requires re-review by a brand-new reviewer (re-record review)
- **Exactly one recovery path**: re-run validation / re-review and record fresh results; writing an old change_id back to "extend its life" is forbidden
- The Conductor's own bookkeeping directory (`.glm-conductor/`) does not participate in the change identifier — writing the ledger never invalidates itself

## Visible degradation when the guard cannot evaluate (fail-open)

Failures of the guard itself **do not block the session** (fail-closed when the hook cannot start would deadlock every session), but degradation must be visible:

| Situation | Behavior | Message |
| --- | --- | --- |
| Hook process crash / import failure | Pass + alarm | stderr `ENFORCEMENT DEGRADED: stop_gate failed: ...` |
| Structural error while evaluating a single task (ChangeIdError from a non-git repo / git failure, invalid ownership scope pattern, etc.) | Only that task degrades: one stderr line, then that task passes while the remaining tasks are evaluated as usual | stderr `ENFORCEMENT DEGRADED: ...` |
| Corrupt state.json / incomplete task directory | No interception, stderr notice only (corrupt / orphaned are outside the trigger domain) | one-line stderr notice |
| python3 not on PATH | The hook cannot start (the host reports a hook run failure) | See the environment self-check; the prerequisite is declared up front in the docs |

**Model-side obligation**: whenever you observe any `ENFORCEMENT DEGRADED`, you must honestly report to the user that "the guard is in a degraded state"; never assume enforcement is still in effect. **A degraded release is not completion**: the guard executes complete only on the all-four-checks-pass path — a degraded task stays active and produces no completion fact; an infrastructure failure must never be treated as completion evidence.

## Recovery when blocked

Act on the block message (it carries the first-failed check and the recovery guidance):

1. **The ownership violation is intentional** → revise the ownership scope declared by the DAG/task (via the `runtime.task` API, or rebuild the declaration after rewriting the DAG), then finish again
2. **The ownership violation is a false positive** → revert the out-of-scope changes, then finish again
3. **Validation missing / stale** → the main session personally re-runs the validation commands, records fresh results via `runtime.task.record_validation(...)` (change_id computed at call time), then finishes again
4. **Review missing / not ship / stale** → complete the fix per the fix-first / rethink verdict → the main session re-validates (re-records validation) → re-review with a brand-new reviewer → record the new verdict via the CLI: `python3 plugins/glm-conductor/runtime/cli.py review-record <repo_root> <task_id> <reviewer> <verdict> [note]`
5. **Writer-guard conflict** → `cli.py writer-show <repo_root>` to inspect the current holder: the reservation is released automatically when the holder task finishes normally; once the associated task/Workflow is confirmed dead with no finish path, perform an operational release via `writer-release --force` (inspect the holder first, then clear it explicitly)
6. **Repeatedly blocked and unfixable** → report the full block message to the user and wait for a human decision

## Boundaries (things the current version does not enforce)

- **Reviewers are read-only**: guaranteed by the read-only tool whitelist in the agent definition (deterministic), not hook-enforced; any out-of-scope writes by a reviewer are ultimately caught by the completion guard's ownership check (defense in depth)
- **Writes inside sub-sessions**: ZCode runtime subagents do not trigger hooks, so pre-write interception is impossible — this is exactly why the design makes the completion guard the last line of defense; native-Workflow text workers are bound by persona discipline and the ownership declarations, and out-of-scope work is still ultimately backstopped by the four checks
- **No mechanical comparison of visual evidence**: the four checks include no visual-evidence check — visual acceptance is owned by the visual channel (VISUAL ACCEPTANCE + visual-reviewer, see the orchestration skill); main-session acceptance is the gatekeeper of visual facts
- **No dispatch-surface interception**: implementer dispatch has no hook-based pre-check; semantic orchestration quality (spec completeness, bounded scope) is owned by routing discipline and review — the guard only guarantees that finish facts are complete and fresh
