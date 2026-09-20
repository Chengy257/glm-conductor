# v2.4 Phase 2 — Task State, Minimal Assurance, and Legacy Runtime Retirement Spec

> **Authority:** `docs/reviews/V2_4_NATIVE_WORKFLOW_REBASELINE_SPEC.md`  
> **Prerequisite:** Phase 1 exit gate passed.  
> **Goal:** make the Native Workflow path authoritative, replace proof-heavy state/assurance/recovery with task-level semantics, then remove the legacy execution runtime rather than preserving dual paths.

## 1. Frozen outcomes

Phase 2 must end with:

1. one small task-level state schema;
2. no durable per-unit runtime states;
3. one task-level `change_id` freshness mechanism;
4. main-session validation recorded minimally;
5. high-assurance review recorded minimally;
6. a small completion guard;
7. a small SessionStart recovery summary;
8. ordinary text execution no longer depends on dispatcher, permits, leases, receipts, agent-run ledger, or per-unit reconcile;
9. legacy execution modules/tests removed or clearly historical, not production fallbacks.

## 2. Work package P2-A — Task-state re-baseline

Rewrite `runtime/state.py` around the v2.4 task shape.

Required top-level concepts:

- `task_id`
- `goal`
- `repository`
- `route`
- `dag`
- `status`
- optional descriptive `phase`
- `workflow_run_id`
- `validation`
- `review`
- `quota_resume` placeholder (Phase 3 finalizes semantics)

Allowed durable statuses:

- active
- waiting_quota
- waiting_user
- blocked
- completed
- cancelled
- failed

Suggested descriptive phases:

- planning
- workflow
- validating
- reviewing

Do not implement a large transition matrix for phases.

### State invariants

- route vocabulary remains exact;
- DAG validates through the Phase-1 DAG validator;
- `workflow_run_id` is nullable until a delegated run starts;
- terminal tasks cannot be silently reopened;
- `validation` and `review` are task-level only;
- no Work Unit contains runtime status.

### Legacy state handling

Do not auto-migrate v2.3 unit/receipt/lease state.

If legacy active state is detected:

- report that it is a v2.3 task;
- instruct finish/retire/recover under the old release or explicitly abandon it;
- do not generate fake v2.4 completion evidence.

A small read-only legacy detector is acceptable. A compatibility runtime is not.

## 3. Work package P2-B — Task-level change identity

Shrink `runtime/fingerprint.py` into the single freshness primitive. Rename to `change_id.py` if doing so simplifies the architecture; otherwise keep the file name but delete legacy APIs.

Required API concept:

```text
compute_change_id(repository, relevant_paths/base) -> sha256:...
```

The same implementation must be used by:

- main validation recording;
- review recording;
- completion guard freshness check.

It must change when task-relevant repository changes change.

It must not expose per-unit receipt/fingerprint APIs.

### Scope

Prefer task-relevant changed paths derived from the union of DAG ownership plus repository baseline.

Do not include Conductor runtime bookkeeping files.

## 4. Work package P2-C — Minimal validation and review records

### Validation

Record only:

- status: passed | failed
- change_id
- optional summary
- optional commands summary for human diagnostics

Do not create verification receipt files.

Do not require stdout/stderr capture, runner identity, duration, or tool-use id for correctness.

### Review

For assurance=high, record:

- required=true
- reviewer
- verdict: ship | fix-first | rethink
- change_id
- optional findings summary

Rules:

- review happens after main validation;
- any repository change makes previous validation and review stale through change_id mismatch;
- fix-first/rethink never satisfies completion;
- after a fix, main validation must be refreshed before a new final review.

No receipt directory and no invocation-authenticity chain.

## 5. Work package P2-D — Completion guard

Rewrite the Stop/completion enforcement around four checks:

1. the task has no active write Workflow;
2. actual changed paths are within union(DAG ownership);
3. main validation is passed and `validation.change_id == current_change_id`;
4. if assurance=high, `review.verdict == ship` and `review.change_id == current_change_id`.

On success:

- set task status to completed;
- release repository writer reservation.

On failure:

- remain nonterminal;
- return one concise actionable reason.

Remove:

- finalizing state;
- unit verification scan;
- receipt scan;
- permit/lease checks;
- tool-use authenticity;
- gate-exhaustion event machinery that exists only to support the old proof chain.

Stop hook may remain the last-chance guard, but completion should also be callable explicitly through the task lifecycle API.

## 6. Work package P2-E — Recovery simplification

Rewrite `runtime/recovery.py` and SessionStart behavior.

For each unfinished task, show only:

- task id;
- goal;
- repository;
- status;
- route;
- workflow run id if any;
- quota wait state if any;
- next safe action.

Expected next actions:

- inspect workflow;
- resume workflow;
- reassess repository;
- run main validation;
- run required review;
- wait for quota/user;
- explicitly retire legacy task.

Remove:

- unit readiness;
- unit zombie classification;
- agent-run reconcile;
- lease reconcile;
- verification receipt reconcile;
- resume manifest dependency.

Delete `resume_manifest.py` once no consumer remains.

## 7. Work package P2-F — Cut over and delete legacy execution runtime

After new state/assurance/recovery tests pass, remove production use and then files for:

- `dispatcher.py`
- `dispatch_wave.py`
- per-unit permit machinery
- `lease.py`
- `agent_run.py`
- `reconcile.py`
- `resume_manifest.py`
- `provenance.py`
- legacy task-manager dispatch/wave/finish-unit transactions
- Agent|Task PreToolUse dispatch permit path
- Agent|Task PostToolUse/PostToolUseFailure lifecycle bookkeeping
- Bash regex policy engine/hook
- ordinary text `flash-implementer` agent definition after all callers are gone.

`task_manager.py` should either:

- be replaced by a much smaller task/workflow lifecycle module; or
- be rewritten so completely that none of the old dispatch/unit transaction contract remains.

Prefer a clean small module over preserving the old name merely for compatibility.

### Journal

Keep `journal.py` only as a generic append/read primitive if still useful.

Shrink event vocabulary to task-level semantic events, e.g.:

- route_selected
- workflow_started
- workflow_reassessed
- validation_recorded
- review_recorded
- waiting_quota
- task_completed
- task_failed

Do not mirror host child lifecycle.

## 8. Hook cleanup

Expected retained hooks after this phase:

- SessionStart → lightweight recovery;
- Stop → lightweight completion guard.

Remove production registration for:

- Agent|Task permit/advisory hooks;
- Agent lifecycle hooks;
- Bash policy hook.

If a hook file contains both retained and retired branches, simplify it rather than leaving dead dispatch code.

## 9. Tests to rewrite/remove

Delete or rewrite tests whose only purpose is to enforce removed concepts:

- unit transition matrix;
- permit anti-replay;
- wave membership;
- per-unit lease heartbeat/generation;
- agent-run ledger;
- provenance receipt authority;
- per-unit reconciliation;
- finalizing-only completion path;
- Bash regex permission policy.

Do not keep compatibility code just to preserve these tests.

## 10. Targeted testing

Run:

1. state schema/legacy detector tests;
2. change_id determinism and stale-after-edit tests;
3. validation/review freshness tests;
4. completion guard tests;
5. writer-release-on-completion tests;
6. SessionStart recovery rendering tests;
7. high-assurance reviewer flow smoke;
8. one end-to-end delegate task through Workflow → validation → completion;
9. one end-to-end full task through Workflow → validation → reviewer → completion.

No full regression yet.

## 11. Phase 2 exit gate

- ordinary text path has exactly one execution substrate: Native Workflow;
- no per-unit runtime state is authoritative anywhere;
- no permit/lease/receipt is required for completion;
- completion freshness is task-level `change_id`;
- high assurance still requires a fresh read-only reviewer;
- legacy runtime files are removed or provably unreachable historical assets;
- only SessionStart and minimal completion enforcement remain on the core hook path.
