# v2.4.1 Continuity Hotfix Plan

> **Status:** PLANNED / implementation handoff ready  
> **Target:** GLM Conductor v2.4.1  
> **Baseline:** main @ 550df5c45b13425bde6ffe704c02725e6d6c4b26  
> **Scope:** two production defects observed during real use after v2.4.0 closeout  
> **Companion implementation spec:** V2_4_1_CONTINUITY_HOTFIX_IMPLEMENTATION_SPEC.md

## 1. Why v2.4.1 exists

v2.4.0 successfully moved execution orchestration to ZCode Native Workflow and reduced the old Conductor runtime. Real use exposed two narrow but high-impact continuity defects that were not caught by the release gates.

### Defect A — Workflow worker model can silently inherit the main-session model

The current generated Workflow script contains agent/persona calls but no model identity. ZCode model choice is run-scoped at CreateWorkflow submission time. Repository evidence from WF-03 already established that omitting subagent_model causes the Workflow run to inherit the session model.

The current orchestration skill says the main session chooses model/thought-level policy, but it does not make explicit Flash binding a launch invariant. When the main session is GLM-5.3, an omitted subagent_model therefore launches all Workflow workers on GLM-5.3 rather than GLM-5.3-Flash.

This is a protocol defect, not a compiler defect: the generated TypeScript cannot itself select the run-scoped model.

### Defect B — auto quota resume has a decision primitive but no guaranteed future activation

v2.4.0 retains:

- authorize_quota_resume;
- enter_waiting_quota;
- scheduled_activation_decision;
- confirm_resume_started;
- quota_resume.automation_id.

However, Conductor does not currently make creation/binding of a future native Scheduled Task a mandatory prerequisite for auto continuity. The host scheduling action is described as a main-session responsibility, and automation_id is not part of a mechanically enforced launch/resume gate.

The result is a liveness gap:

~~~text
long Workflow starts
    -> quota becomes exhausted
    -> model can no longer create a wake task
    -> task is waiting_quota but nothing is guaranteed to wake it
~~~

This defeats the intended meaning of user-authorized unattended continuation.

## 2. v2.4.1 design decision

v2.4.1 adds two explicit invariants without rebuilding the old v2.3 control plane.

### I1 — Explicit Worker Model Invariant

Every delegate/full Native Workflow submission MUST carry an explicit provider-qualified GLM-5.3-Flash subagent_model.

Rules:

1. omitted model is invalid for the Conductor path;
2. bare GLM-5.3-Flash aliases are not sufficient;
3. the provider/account prefix is never hard-coded in the compiler;
4. the main session discovers configured models from the host;
5. exactly one matching provider-qualified GLM-5.3-Flash may be selected automatically;
6. zero matches fails closed;
7. multiple matches require an explicit choice and must not be guessed;
8. GLM-5.3 is never accepted as a text-worker fallback for delegate/full;
9. visual custom-agent exceptions remain unchanged.

### I2 — Auto Continuity Must Be Armed Before Work

For a task whose quota_resume.mode is auto, a valid future native Scheduled Task must already be created and its automation id bound to task state before the main session launches or resumes quota-consuming Workflow work.

Derived predicate:

~~~text
auto_continuity_armed =
    quota_resume.mode == "auto"
    AND quota_resume.automation_id is a non-empty string
~~~

Manual mode does not require a scheduled activation.

The order is therefore:

~~~text
authorize auto
    -> create future native Scheduled Task
    -> bind automation_id
    -> continuity preflight PASS
    -> launch/resume Workflow
~~~

Never:

~~~text
launch/resume
    -> hope to create a wake task after quota exhaustion
~~~

## 3. Scheduling strategy

v2.4.1 does not reintroduce Global Quota Clock, quota epochs, subscriptions, resident watchers, scheduler-database writes, or an exact five-hour clock.

### Preferred path — recurring native Scheduled Task

A single recurring task is preferred because it already guarantees a future activation before each quota-consuming execution interval.

The recurring wake prompt is idempotent:

- terminal task -> clean up the associated automation once;
- active task -> cheap no-op;
- waiting_quota -> refresh quota and evaluate resume;
- waiting_user -> no resume;
- missing/corrupt task -> report degraded state, do not guess.

The recurring automation remains armed across successful resumes and is removed only at terminal cleanup or explicit user cancellation.

### Fallback path — one-shot successor chain

Use only if the host cannot provide a suitable recurring schedule.

At each one-shot wake:

1. load exact task state;
2. refresh provider quota/reset evidence;
3. if further unattended continuity may still be needed, create the successor future activation;
4. bind the successor automation id;
5. verify continuity preflight;
6. only then call ResumeWorkflowRun;
7. after host acceptance call quota-resume-confirm.

If successor creation/binding fails, do not consume the next quota window by launching a long automatic resume. Keep the task recoverable and report continuity degradation.

This is the **renew liveness before work** rule.

## 4. until_done semantics

v2.4.1 does not restore the old v2.3 four-mode state machine.

User wording such as **until_done** is treated as UX authorization for unattended continuation, implemented by the existing auto mode plus an explicit bounded max_resumes circuit breaker.

The durable state remains conceptually:

~~~json
{
  "mode": "manual | auto",
  "max_resumes": 0,
  "resume_count": 0,
  "automation_id": null
}
~~~

No unbounded automatic resume is introduced. User authorization remains explicit and bounded.

## 5. Host/runtime responsibility boundary

ZCode owns:

- ListModels / configured model discovery;
- CreateWorkflow;
- Scheduled Task create/delete;
- ResumeWorkflowRun;
- Workflow execution lifecycle.

Conductor owns:

- the model-selection/preflight contract;
- model ambiguity/fallback rejection;
- durable task ↔ workflow-run association;
- durable task ↔ automation-id association;
- auto-continuity armed predicate;
- bounded resume authorization/counting;
- wake prompt contract;
- recovery guidance and completion cleanup semantics.

Conductor MUST NOT:

- edit ZCode internal scheduler SQLite state;
- run a resident quota watcher;
- infer a provider identity not returned by the host;
- hard-code a provider account id;
- hard-code a five-hour reset period;
- silently downgrade auto continuity to manual while claiming it remains armed.

## 6. Intended implementation surface

Expected production changes are intentionally narrow:

- plugins/glm-conductor/runtime/workflow/submission.py — new pure submission/model preflight helpers;
- plugins/glm-conductor/runtime/task.py — automation binding/clearing and continuity preflight;
- plugins/glm-conductor/runtime/state.py — validation/documentation for automation binding if needed;
- plugins/glm-conductor/runtime/cli.py — stable preflight/bind/clear commands;
- plugins/glm-conductor/skills/orchestration/SKILL.md — mandatory explicit Flash submission flow;
- plugins/glm-conductor/skills/continuity/SKILL.md — arm-before-work and recurring/one-shot wake contract;
- plugins/glm-conductor/skills/continuity/references/long-horizon.md — durable recovery sequence;
- README.md / README.zh-CN.md / docs/architecture.md / docs/core-concepts.md / docs/troubleshooting.md as final truth-sync only;
- targeted unit tests plus one final full regression.

No changes are planned to compiler DAG semantics, ownership planning, change_id, writer guard, completion guard, reviewer semantics, or Global Quota Clock separation.

## 7. Work packages

### H1 — Explicit Workflow submission model contract

Build a pure, testable model selector/preflight surface.

Acceptance:

- omission is rejected;
- exact provider-qualified Flash id is accepted;
- GLM-5.3 is rejected;
- zero configured Flash matches fails closed;
- multiple configured Flash matches fail ambiguous unless explicitly selected;
- no provider/account id is hard-coded.

### H2 — Auto-continuity arming state and CLI

Add task-level operations for:

- bind automation id;
- clear automation id after confirmed host deletion;
- check auto continuity armed state;
- continuity preflight before launch/resume.

Acceptance:

- manual tasks pass without automation;
- auto tasks fail preflight while automation_id is null;
- bind then passes;
- clear returns to unarmed;
- binding is task-local and idempotent for the same id;
- conflicting replacement requires an explicit operation, never silent overwrite.

### H3 — Orchestration/continuity protocol rewrite

Make the launch sequence normative:

1. create task and select route;
2. for auto/until_done authorization, record bounded authorization;
3. create native recurring Scheduled Task (preferred) or one-shot fallback;
4. bind automation id;
5. resolve explicit Flash worker model;
6. run Conductor preflight;
7. acquire writer guard;
8. CreateWorkflow with explicit subagent_model;
9. record workflow run id.

Wake sequence must enforce liveness before resume.

### H4 — Integration proof and release truth sync

Run targeted tests during H1-H3, then exactly one full regression at the end.

Live proofs on a suitable ZCode 3.14+ host:

- CreateWorkflow submitted from a GLM-5.3 main session but workers actually run with explicit GLM-5.3-Flash;
- omission/GLM-5.3 preflight rejected before submission;
- auto task without automation id rejected by continuity preflight;
- recurring wake or one-shot successor exists before Workflow launch;
- stopped waiting_quota run resumes via same run id;
- terminal cleanup deletes/clears only the associated automation;
- repeated wake after completion is a safe no-op.

## 8. Test economy

Do not repeat the full project suite after each work package.

Recommended gates:

- H1: new submission/model tests + workflow compiler/CLI targeted tests;
- H2: quota-resume/state/task/CLI targeted tests;
- H3: skill validator + focused contract tests + smoke plugin load;
- H4: integrated targeted bundle, live host smokes, then one full regression.

Any implementation agent that runs the full regression after every unit is deviating from this plan.

## 9. Release gates

v2.4.1 is releasable only when all are true:

- delegate/full cannot follow the documented Conductor path without an explicit provider-qualified GLM-5.3-Flash selection;
- main-session GLM-5.3 inheritance is no longer an accepted path;
- auto continuity cannot pass preflight without a bound future automation;
- a future activation is established before the first quota-consuming Workflow launch;
- one-shot fallback renews the successor before automatic resume;
- recurring wake is idempotent;
- resume_count increments only after accepted ResumeWorkflowRun;
- bounded max_resumes semantics remain intact;
- Global Quota Clock remains outside the runtime dependency graph;
- no scheduler SQLite production write is reintroduced;
- final docs match actual code and live behavior;
- final full regression passes.

## 10. Non-goals

v2.4.1 does not:

- redesign the route matrix;
- add a generic agent framework;
- implement an account-level Global Quota Clock;
- make quota handling a routing axis;
- add indefinite/unbounded auto resume;
- change Workflow DAG compilation or worker personas except where needed to surface submission requirements;
- redesign reviewer/visual roles;
- guarantee Scheduled Task firing while the host application is closed unless that is separately verified.

## 11. Handoff

Implementation MUST follow the companion detailed specification. If implementation discovers that a host capability assumption is false, stop that work package and record the exact host evidence; do not invent a compatibility subsystem.

The target is a small v2.4.1 correctness hotfix: **explicit Flash execution + pre-armed continuity**, not a new orchestration architecture.
