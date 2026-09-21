# v2.4 Phase 3 — Minimal Quota Resume and Global Clock Extraction Spec

> **Authority:** `docs/reviews/V2_4_NATIVE_WORKFLOW_REBASELINE_SPEC.md`  
> **Prerequisite:** Phase 2 exit gate passed.  
> **Goal:** remove the quota control plane from glm-conductor, retain only provider observation plus bounded task-level Workflow resume, and produce a clean handoff inventory for the future standalone Global Quota Clock project.

## 1. Frozen boundary

glm-conductor keeps only what a waiting coding task needs:

- inspect provider quota;
- know whether execution is currently usable;
- retain relevant reset information when available;
- preserve the stopped Workflow run id;
- schedule a supported future activation;
- resume only when explicitly authorized;
- bound how many quota-window resumes may occur.

glm-conductor does **not** keep a provider/account-level continuous clock.

Global Quota Clock becomes a separate project and must not be a runtime dependency of Conductor.

## 2. Work package P3-A — Reduce quota observation core

Retain only modules/logic necessary for provider observation:

- `quota/provider.py`
- provider adapters such as `zai.py`, `bigmodel.py`
- `quota/_http.py`
- `quota/credentials.py`
- `quota/parser.py`
- `quota/resolver.py`
- minimal reset-time math from scheduler/time utilities
- `quota/report.py` only if the user-facing diagnostic command remains useful.

Required normalized answer:

```json
{
  "status": "AVAILABLE | PRESSURE | EXHAUSTED | UNKNOWN",
  "reset_at": "... or null",
  "source": "...",
  "observed_at": "..."
}
```

Exact output shape may align with existing normalized snapshot, but consumers must not require epoch/subscription/control-plane fields.

### Semantics

- AVAILABLE/PRESSURE → task may attempt resume;
- EXHAUSTED → remain waiting;
- UNKNOWN → do not invent availability. Prefer a later supported retry or main-session reassessment.

Remove execution-phase semantics:

- NORMAL
- PRESSURE (execution phase)
- DRAINING
- BLOCKED
- dispatch_budget
- allow_new_wave
- allow_finish_current.

Workflow scheduling owns worker concurrency.

## 3. Work package P3-B — Tiny quota-resume authorization

Finalize task state:

```json
{
  "quota_resume": {
    "mode": "manual | auto",
    "max_resumes": 0,
    "resume_count": 0,
    "automation_id": null
  }
}
```

Rules:

- default is manual / 0;
- auto requires explicit user authorization;
- `max_resumes` is a non-negative integer;
- `resume_count <= max_resumes`;
- increment only after a supported Workflow resume call actually starts/accepts;
- once budget is exhausted, task becomes `waiting_user`;
- no automatic reset of the budget on a new provider window.

Do not retain:

- manual/notify/auto_once/until_done four-mode vocabulary;
- provider identity × epoch accounting;
- consumed-window migration;
- pending consumption markers.

## 4. Work package P3-C — Quota wait/resume lifecycle

### Enter waiting state

When host evidence indicates the Workflow stopped because provider quota is unavailable:

- persist `task.status = waiting_quota`;
- preserve `workflow_run_id`;
- record latest quota observation/reset if useful for diagnostics;
- if mode=auto and budget remains, ensure one supported native Scheduled Task activation exists;
- if mode=manual, do not create an automatic resume task.

Do not infer quota exhaustion solely from elapsed time.

### Scheduled activation behavior

W0 established scheduled turns are mid-turn continuations of the owning conversation on this build.

The scheduled prompt/skill contract must therefore be idempotent and environment-independent:

1. identify the exact Conductor task;
2. load current task state;
3. if status != waiting_quota → no-op;
4. refresh quota;
5. if not usable → no-op/remain waiting;
6. if auto authorization absent or exhausted → waiting_user/no resume;
7. inspect the exact `workflow_run_id`;
8. call supported `ResumeWorkflowRun`;
9. only on accepted resume increment `resume_count` and return task to active/workflow phase;
10. repeated activation on a completed/non-resumable run must be safe and must not consume budget.

Do not assume the scheduled turn is a fresh session.

Closed-app firing remains outside Conductor's correctness claims unless separately verified.

## 5. Work package P3-D — Remove quota control plane

Remove production use and then delete Conductor-specific implementations for:

- `quota/control.py` execution phases;
- `quota/observer.py` if no retained diagnostic consumer needs it;
- `quota/watcher.py`
- `quota/watcher_store.py`
- `quota/epoch.py`
- `quota/primer.py`
- `quota/accounting.py`
- `continuity/subscription.py`
- legacy `continuity/resume.py` authorization/accounting machinery;
- legacy `continuity/wake_bridge.py`;
- `activation_transport.py`;
- Global Quota Clock modules/store;
- `host/zcode_schedule.py` as a Conductor production dependency;
- scheduler facts that only support the old bridge/clock.

If a small pure reset-time helper is useful, move/rewrite it under the retained quota core rather than retaining the old dependency graph.

## 6. Work package P3-E — Native Scheduled Task integration

Use only supported host scheduling established in W0.

Conductor may:

- generate the scheduled prompt/instructions;
- record the resulting automation id;
- provide status/recovery guidance.

Conductor must not directly edit ZCode internal SQLite scheduler state.

If exact 5h scheduling is unsupported, use a supported periodic wake appropriate for task resumption.

Exact provider-window refresh alignment is the responsibility of the future standalone Global Quota Clock project.

## 7. Work package P3-F — Global Quota Clock extraction inventory

Before deleting Clock-related production code, create:

`docs/roadmap/GLOBAL_QUOTA_CLOCK_EXTRACTION_INVENTORY.md`

It must classify existing material into:

### Candidate to migrate/reuse conceptually

- provider quota adapter behavior;
- provider identity logic if needed;
- five-hour reset calculation;
- Global Clock target-time decision logic;
- host scheduler observations/constraints;
- Window Primer/materialization observations;
- existing clock CLI UX that is still useful.

### Must NOT migrate

- Conductor task IDs;
- Work Unit/DAG state;
- task subscription;
- task wake bridge;
- task resume budget;
- review/validation state;
- repository ownership;
- task journal semantics.

### Required warning

The new project must independently reassess whether direct SQLite retiming is still justified. It must not inherit that implementation merely because v2.3 used it.

This inventory is documentation only; do not create the new repository inside Phase 3.

## 8. Targeted tests

Run:

1. provider parser/resolver tests;
2. credential/no-secret persistence tests;
3. reset-at extraction tests;
4. manual vs auto authorization tests;
5. resume budget tests;
6. waiting_quota → usable → resume accepted;
7. waiting_quota → still exhausted → no-op;
8. repeated activation → no extra budget consumption;
9. completed/non-resumable Workflow → safe no-op;
10. budget exhausted → waiting_user;
11. one live Scheduled Task resume smoke if host access is available.

Delete Watcher/Epoch/Subscription/Primer/Clock tests when their production modules are removed.

No full regression yet.

## 9. Phase 3 exit gate

- no resident quota process remains in Conductor;
- no quota epoch/subscription/accounting remains;
- no Global Clock remains in Conductor runtime dependency graph;
- no direct scheduler SQLite production write remains;
- a waiting Conductor task can still be resumed through the native Scheduled Task path when authorized;
- auto resume is bounded and user-authorized;
- Global Clock extraction inventory is complete.
