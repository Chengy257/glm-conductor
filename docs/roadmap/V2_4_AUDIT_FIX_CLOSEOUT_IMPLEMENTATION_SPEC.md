# GLM Conductor v2.4 Audit-Fix Closeout — Detailed Implementation Spec

> **Parent plan:** `docs/roadmap/V2_4_AUDIT_FIX_CLOSEOUT_PLAN.md`  
> **Target branch:** `review/zcode-3.14-native-workflow`  
> **Purpose:** close the post-implementation audit findings without reopening v2.4 architecture.

## 0. Implementation discipline

This closeout is a bounded hardening pass.

General rules:

1. Preserve the native-first v2.4 boundary.
2. Prefer small changes to existing v2.4 modules over new abstractions.
3. Do not re-create retired v2.3 machinery under new names.
4. Keep domain logic in runtime modules and CLI as a thin shell.
5. Update tests with the behavior change; do not preserve a wrong behavior because an existing test expects it.
6. No full regression until the final release gate.
7. Every work unit must record exact tests run and observed result.

---

# Wave A — Correctness and capability blockers

## A1 — Fix change_id freshness for ownership scopes

### Problem

Current code uses:

- the pre-fix task-level "relevant paths" helper (`runtime.task`) → union of ownership **scope strings**;
- `runtime.change_id.compute_change_id(repo_root, ...)` → treats each item as a literal file path.

A scope such as `src/parser/**` therefore does not bind the digest to files under `src/parser/`.

### Required design

Maintain responsibility separation:

```text
ownership scopes
      ↓
ownership module resolves actual repository touched files
      ↓
task-level relevant changed file set
      ↓
change_id hashes exact files + base revision
```

Do **not** teach `change_id.py` the ownership glob language.

### A1.1 New task helper

Replace or split the then-ambiguous task-level "relevant paths" concept.

Recommended names:

- `ownership_scopes(task_state)` → declared DAG scope strings;
- `relevant_changed_files(task_state, repo_root)` → exact repository-relative files currently touched and owned by those scopes.

Exact naming may follow repository style, but scope strings and actual files must no longer share one API name.

### A1.2 Resolution algorithm

For one task:

1. derive normalized scope union from DAG ownership;
2. call `ownership.git_touched_files(effective_repo_root)`;
3. classify with `ownership.classify_paths(touched, scopes)`;
4. if any touched file is out of scope, return/raise enough information for the completion guard to block as it already does;
5. exact owned touched files become the file list supplied to `change_id.compute_change_id`.

The same helper must be used by:

- `task.record_validation`;
- `task.record_review`;
- completion guard current `change_id` calculation.

This preserves the single-implementation freshness rule.

### A1.3 Required freshness behavior

The digest must change when, after a recorded validation/review:

- an existing owned file under `src/**` changes;
- a new owned file appears under the scope;
- an owned file is deleted;
- an owned file is renamed (old/new paths handled from git touched output);
- the set of owned touched files changes.

Changes outside ownership continue to be caught by the ownership completion check, not by freshness.

`.glm-conductor/` bookkeeping stays excluded.

### A1.4 Baseline behavior

Keep the current base revision anchoring. A changed HEAD must still change the digest.

Do not introduce a second fingerprint or receipt layer.

### A1.5 Tests

Add/update at minimum:

- exact file scope stale-after-edit;
- directory prefix scope stale-after-edit;
- `src/**` stale-after-edit;
- nested glob stale-after-edit;
- new file under glob changes id;
- delete under glob changes id;
- rename under glob changes id;
- unrelated out-of-scope change remains ownership failure;
- bookkeeping-only change does not stale;
- validation record then owned-glob edit → completion guard blocks stale validation;
- high-assurance review then owned-glob edit → completion guard blocks stale review after revalidation as appropriate.

Primary files expected:

- `runtime/task.py`
- `runtime/change_id.py` only if exact-file API/docs need clarification
- `hooks/stop_gate.py`
- `tests/test_change_id.py`
- `tests/test_task_lifecycle.py`
- `tests/test_completion_guard.py`
- docs/skills statements that currently say ownership scopes are directly hashed.

### A1 exit criterion

A task declared with `ownership: ["src/**"]` must become stale when any owned touched file under `src/` changes after validation/review.

---

## A2 — Restore a real multimodal contract for visual-reviewer

### Problem

`visual-reviewer` currently has no `model:` field and inherits the session model.

The normal main model is GLM-5.3, while the visual reviewer contract requires direct screenshot/image inspection using a multimodal Flash model.

A reviewer that launches but cannot inspect images is a capability failure.

### Required rule

Text and visual reviewers no longer share one model-binding rule.

- `glm-reviewer`: may inherit the main/session model; remains read-only and fresh-context.
- `visual-reviewer`: must run on a **verified multimodal GLM-5.3-Flash binding** or the visual high-assurance route must fail closed.

### A2.1 Host binding probe

Before editing the final binding, inspect the actual current ZCode 3.14 model inventory in the fresh target environment.

Preferred resolution order:

1. if ZCode provides a tested portable model alias that resolves to GLM-5.3-Flash without silent fallback, use it;
2. otherwise use the provider-qualified Flash model id actually available on the target host;
3. if no verified multimodal Flash binding is available, do not silently inherit text GLM-5.3 — mark visual reviewer unavailable and make visual high-assurance preflight fail closed.

Do not speculate a provider id.

### A2.2 Keep visual roles coherent

`visual-implementer` and `visual-reviewer` should use the same verified multimodal model family/binding unless a concrete host reason requires otherwise.

Audit the current `visual-implementer` binding at the same time; it currently remains provider-qualified and may also be stale relative to the current host account.

### A2.3 Tests

Rewrite `tests/test_agent_model_binding.py` so it tests **capability intent**, not the now-invalid rule “all reviewers have no model field”.

Expected assertions:

- glm-reviewer: no hard requirement for provider-qualified binding unless host constraints demand it;
- visual-reviewer: has a verified multimodal Flash binding or an explicit fail-closed availability mechanism;
- visual-implementer: same multimodal requirement;
- visual roles must not silently resolve to the text main model;
- read-only tool list for visual-reviewer remains intact.

### A2 exit criterion

In a fresh session, visual-reviewer must launch and directly inspect a small image/screenshot fixture, then return a valid `VISUAL REVIEW` result based on what is actually visible.

---

# Wave B — Lifecycle hardening

## B1 — Add a minimal quota-resume CLI surface

### Goal

Expose the already-implemented v2.4 task-level quota lifecycle without rebuilding the old continuity control plane.

### Required commands

Use concise names consistent with the current CLI. Recommended surface:

```text
quota-wait <repo_root> <task_id> [--force-refresh]
quota-resume-authorize <repo_root> <task_id> <max_resumes>
quota-resume-decision <repo_root> <task_id> [--force-refresh]
quota-resume-confirm <repo_root> <task_id>
```

Exact spelling may vary slightly if required for consistency, but all four lifecycle actions must have a stable non-`python -c` entry point.

### B1.1 quota-wait

Behavior:

1. resolve a current quota view (force refresh when requested);
2. call `task.enter_waiting_quota` with the normalized diagnostic view;
3. preserve workflow_run_id;
4. output single-line JSON with task status and quota observation.

This command represents a task that has already encountered a quota-related execution stop. It is not an autonomous quota-failure detector.

### B1.2 quota-resume-authorize

- parse non-negative integer max_resumes;
- call `task.authorize_quota_resume`;
- no model/network call;
- output resulting quota_resume block.

Authorization must remain user-gated at the skill/policy layer.

### B1.3 quota-resume-decision

1. force-refresh or normally resolve quota;
2. call `task.scheduled_activation_decision`;
3. return action + exact workflow_run_id/budget fields required by the scheduled turn.

It must not call ResumeWorkflowRun itself because that is a host action.

### B1.4 quota-resume-confirm

Called only after host ResumeWorkflowRun has been accepted.

- call `task.confirm_resume_started`;
- increment budget exactly once according to existing domain rules;
- output updated state summary.

### B1.5 Skill update

Update continuity docs so all runtime operations use the stable CLI surface.

No direct `python3 -c` examples.

### B1.6 Tests

Add focused CLI tests for:

- manual default;
- explicit authorize;
- exhausted/unknown remain waiting;
- available + budget → resume-authorized;
- confirm consumes one budget;
- repeated decision before confirm does not consume;
- non-waiting task → no-op;
- budget exhausted → waiting_user;
- invalid max_resumes rejected.

Do not add watcher/epoch/subscription concepts.

---

## B2 — Strengthen writer-guard lifecycle enforcement

### Goal

Keep the coarse writer guard, but make the normal Conductor lifecycle mechanically refuse to register/complete a delegated run that skipped acquisition.

### B2.1 record_workflow_run precondition

For `route.mode in ("delegate", "full")`:

1. resolve the task repository root;
2. `writer_guard.inspect`;
3. require a holder;
4. require `holder.task_id == task_id`;
5. otherwise reject with a clear ValueError.

After a valid host run id is obtained, update the existing same-task guard record with the workflow_run_id using the existing idempotent acquire/update behavior.

Then persist task `workflow_run_id`, adapter record and journal event.

For solo/audit tasks, this precondition is not required because they do not use a write Workflow.

### B2.2 completion guard precondition

For an active delegated task with a non-null workflow_run_id:

- holder is another task → block (existing);
- holder is missing → **block** as missing writer reservation;
- holder is this task → continue.

For solo/audit tasks without a delegated workflow, holder absent remains valid.

Do not add host hook interception or per-node locks.

### B2.3 Documentation wording

Use precise guarantee language:

> writer guard is a Conductor lifecycle invariant enforced at run registration and completion; arbitrary raw host Workflow calls outside the Conductor protocol are outside this guarantee.

Do not claim Conductor intercepts every CreateWorkflow invocation.

### B2.4 Tests

Add:

- delegate/full record_workflow_run without guard → reject;
- guard held by another task → reject;
- same task guard → record succeeds and holder gains run id;
- solo/audit record without guard allowed if their API legitimately records no Workflow run;
- delegated completion with missing holder → block;
- delegated completion with own holder → allowed to proceed to next checks;
- other-task holder → block.

---

# Wave C — Fresh-host integration proof

## C1 — Hook launcher verification/fix

### Problem

`hooks/hooks.json` currently invokes `python3`.

The original Windows spike showed `python3` may resolve to a Microsoft Store stub rather than the actual Python used by local tests.

### C1.1 Probe first

After syncing the final plugin into the actual ZCode plugin cache, from the same environment used to launch ZCode determine:

- what executable `python3` resolves to;
- what executable `python` resolves to;
- Python major/minor version for each real interpreter;
- whether ZCode hook process PATH matches the interactive shell assumption.

Use a tiny diagnostic hook only if necessary. Remove any temporary diagnostic registration after the probe.

### C1.2 Minimal fix

Choose the smallest target-host-correct solution.

Acceptable examples:

- keep `python3` if the fresh host now resolves it correctly;
- use `python` if that is the verified Python 3 interpreter on the supported target host;
- use another stable launcher only if already present and verified.

Do not create a multi-platform launcher framework without evidence it is needed.

### C1.3 Live hook proof

Fresh cache + fresh session:

- SessionStart emits/loads resume context for a controlled unfinished task;
- Stop blocks a controlled stale-validation task;
- after correction, Stop allows/completes a valid task.

The proof must show the **v2.4 hook files** actually executed, not cached v2.3 hooks.

Record plugin cache path/version and evidence.

---

## C2 — Fresh-session reviewer proof

After A2 and cache refresh:

### Text reviewer

Launch `glm-reviewer` in a fresh session:

- confirm role is available;
- confirm it is fresh-context;
- confirm write/edit/Bash tools are unavailable;
- provide a small controlled diff + validation evidence;
- obtain valid `GLM REVIEW` output.

### Visual reviewer

Launch `visual-reviewer` in a fresh session:

- confirm actual runtime model/binding is the verified multimodal Flash model;
- confirm write/edit/Bash tools are unavailable;
- provide a small local image fixture containing an obvious visual fact;
- require reviewer to read the image and report that fact;
- obtain valid `VISUAL REVIEW` output.

A text-only reviewer that merely repeats the prompt is not a pass.

### C2 exit criterion

Both retained reviewer paths have live evidence on the final cached v2.4 plugin.

---

# Wave D — Final release gate

## D1 — Documentation and consistency cleanup

Update only documents affected by audit fixes:

- `docs/architecture.md`
- `docs/core-concepts.md`
- `docs/troubleshooting.md`
- orchestration / enforcement / continuity skills
- agent model-binding comments/tests
- `V2_4_CLOSEOUT_REPORT.md` if it currently states release-ready without the audit-fix qualification.

Required wording corrections:

- change_id binds to **actual owned touched files**, not literal ownership scope strings;
- visual-reviewer requires verified multimodal capability;
- writer guard guarantee is lifecycle-level, not universal host interception;
- quota-resume operations use stable CLI commands;
- hook launcher is whatever was live-verified.

Do not rewrite unrelated sections.

## D2 — Targeted audit-fix bundle

Run one focused bundle covering:

- change_id + task lifecycle + completion guard;
- agent model binding;
- writer guard;
- CLI v2.4 + quota resume;
- hook unit tests;
- validator;
- smoke plugin load.

Document exact test count/result.

## D3 — Final full regression

Run the full current v2.4 suite once.

If failures occur:

1. classify;
2. run only affected subsets during repair;
3. rerun full suite once after fixes are complete.

Also run:

- validator 15/15 (or updated count if validator legitimately changes);
- plugin smoke;
- import/py_compile checks.

## D4 — Closeout report

Create:

`docs/reviews/V2_4_AUDIT_FIX_CLOSEOUT_REPORT.md`

Required sections:

1. branch/head commit;
2. AF-01–AF-06 disposition;
3. code changes;
4. targeted test evidence;
5. fresh-host hook evidence;
6. fresh-session reviewer evidence;
7. final full regression;
8. remaining known host limitations;
9. final verdict exactly one of:
   - `MERGE_READY`
   - `NOT_READY`

Do not declare release-ready if either visual-reviewer image proof or v2.4 hook live proof is still deferred.

---

# Expected file-impact map

Likely code files:

- `plugins/glm-conductor/runtime/task.py`
- `plugins/glm-conductor/runtime/change_id.py` (clarification/minimal API change only)
- `plugins/glm-conductor/runtime/ownership.py` (reuse preferred; avoid duplicate matcher)
- `plugins/glm-conductor/runtime/cli.py`
- `plugins/glm-conductor/runtime/writer_guard.py` only if a tiny helper is useful
- `plugins/glm-conductor/hooks/stop_gate.py`
- `plugins/glm-conductor/hooks/hooks.json` only if C1 proves launcher change is needed
- `plugins/glm-conductor/agents/visual-reviewer.md`
- possibly `plugins/glm-conductor/agents/visual-implementer.md`
- `plugins/glm-conductor/agents/glm-reviewer.md` only if final host evidence requires a small clarification.

Likely tests:

- `tests/test_change_id.py`
- `tests/test_task_lifecycle.py`
- `tests/test_completion_guard.py`
- `tests/test_agent_model_binding.py`
- `tests/test_writer_guard.py`
- `tests/test_cli_v24.py`
- `tests/test_quota_resume_v24.py`
- hook tests/smokes.

Avoid touching compiler/DAG modules unless a failing test proves a real dependency.

# Commit strategy

Prefer 4–6 coherent commits, not one commit per tiny assertion.

Suggested grouping:

1. `fix(v2.4): bind change freshness to owned touched files`
2. `fix(v2.4): restore multimodal visual reviewer binding`
3. `fix(v2.4): expose bounded quota resume cli`
4. `fix(v2.4): enforce writer guard in delegated lifecycle`
5. `fix(v2.4): verify/fix fresh-cache hook launcher` (only if code changes)
6. `docs(v2.4): close audit fixes and release evidence`

# Final acceptance checklist

- [ ] glob/directory ownership freshness bug fixed
- [ ] stale validation detected after owned glob file edit
- [ ] stale review detected after owned glob file edit
- [ ] visual-reviewer uses verified multimodal capability
- [ ] visual-implementer binding audited against same host
- [ ] quota-wait CLI works
- [ ] quota-resume-authorize CLI works
- [ ] quota-resume-decision CLI works
- [ ] quota-resume-confirm CLI works
- [ ] delegate/full run registration requires own writer guard
- [ ] delegated completion blocks missing writer guard
- [ ] fresh-cache SessionStart proves v2.4 hook execution
- [ ] fresh-cache Stop proves v2.4 completion guard execution
- [ ] fresh-session glm-reviewer proof passes
- [ ] fresh-session visual-reviewer image proof passes
- [ ] targeted audit-fix bundle passes
- [ ] validator/smoke/import gates pass
- [ ] final full regression passes
- [ ] `V2_4_AUDIT_FIX_CLOSEOUT_REPORT.md` says MERGE_READY
