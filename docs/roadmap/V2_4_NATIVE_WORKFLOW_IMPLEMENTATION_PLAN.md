# GLM Conductor v2.4 Native-First Implementation Plan

> **Authority:** implements `docs/reviews/V2_4_NATIVE_WORKFLOW_REBASELINE_SPEC.md`.  
> **Audience:** local coding agent / GPT-5.6 Luna implementation handoff.  
> **Testing rule:** targeted tests at phase boundaries; one full regression at the final gate. Do not repeatedly run the complete legacy suite.

## 0. Current implementation status

- **Phase 0:** COMPLETE — host evidence closure recorded in `docs/reviews/ZCODE_3_14_NATIVE_WORKFLOW_RESULTS.md §13`.
- **Phase 1:** READY — execute `V2_4_PHASE_1_NATIVE_SEMANTIC_CORE_SPEC.md`.
- **Phase 2:** planned — execute only after the Phase 1 exit gate.
- **Phase 3:** planned — execute only after the Phase 2 exit gate.
- **Phase 4:** planned — final integration/release gate after Phase 3.

Phase-specific authoritative implementation specs:

- `docs/roadmap/V2_4_PHASE_1_NATIVE_SEMANTIC_CORE_SPEC.md`
- `docs/roadmap/V2_4_PHASE_2_STATE_ASSURANCE_RETIREMENT_SPEC.md`
- `docs/roadmap/V2_4_PHASE_3_QUOTA_RESUME_EXTRACTION_SPEC.md`
- `docs/roadmap/V2_4_PHASE_4_INTEGRATION_RELEASE_SPEC.md`

The functioning-hook fresh-session follow-up noted by W0 is **non-blocking** for Phase 1. The W0 reviewer model-binding failure **is** a Phase-1 implementation requirement and is handled in P1-A.

## 1. Objective

Implement the v2.4 complexity re-baseline by replacing legacy Conductor execution-runtime mechanics with ZCode native Workflow/Scheduled Task capabilities while preserving only Conductor-specific semantics.

The implementation must optimize for **code deletion, responsibility clarity, and one source of runtime truth**.

It must not preserve a legacy subsystem solely to keep old tests green.

## 2. Global rules for every phase

1. `docs/architecture.md` describes released behavior until the new runtime is integrated; update it only at the final documentation phase.
2. Do not introduce a second Workflow abstraction behind the old dispatcher.
3. Do not recreate legacy permits, leases, receipts, or per-unit states with new names.
4. Native Workflow run state is the only execution-lifecycle truth.
5. Repository ownership is Conductor semantic truth.
6. One Git repository permits one active Conductor write Workflow.
7. Worker-local tests are focused checks, not durable provenance.
8. Main-session validation is task-level and limited to relevant integrated checks.
9. High assurance still requires a fresh-context native reviewer.
10. Automatic quota resume requires user authorization and a bounded `max_resumes`.
11. Global Quota Clock code is outside the v2.4 Conductor target. Preserve history; do not build a Clock dependency into the new runtime.
12. If an old test enforces a deliberately removed architecture, replace/remove that test together with the architecture. Do not add compatibility code just to satisfy it.

## 3. Phase 0 — Close the two host evidence gaps

### Goal

Close only the host questions that materially affect implementation. Do not repeat WF-00–WF-25.

### P0-A Hook visibility

Environment:

- ZCode 3.14 current build;
- glm-conductor 2.3.2 actually loaded;
- fresh session;
- functioning Python hook command.

Re-run only WF-06/WF-07-equivalent probes.

Record:

- whether Workflow start emits any relevant plugin hook;
- whether Workflow child creation emits Agent|Task Pre/PostToolUse;
- whether marker-bearing child instructions become hook-visible;
- whether any existing hook can deny or mutate Workflow child launch.

Expected architecture if prior structural observation holds:

- Workflow child launch bypasses Agent|Task hooks;
- v2.4 removes those hooks rather than adapting permits.

If contrary evidence appears, document it before changing the target design.

### P0-B Scheduled Task → Workflow resume

Verify on the same host:

- create a supported recurring/periodic Scheduled Task;
- scheduled turn can locate an existing stopped/interrupted Workflow run;
- scheduled turn can call the supported resume surface;
- repeated scheduled turns are safe when task is no longer waiting.

Do not test Global Quota Clock exact five-hour window maintenance here.

### Deliverables

Update:

- `docs/reviews/ZCODE_3_14_NATIVE_WORKFLOW_RESULTS.md`
- capability matrix rows for hooks/scheduled resume;
- a short W0 closure section in the v2.4 spec if host behavior changes any assumption.

### Testing

Only probe-specific validation. **No full project regression.**

### Exit gate

Proceed when:

- Workflow execution substrate remains usable;
- reviewer path remains available outside Workflow;
- a supported future activation path for quota resume exists, or a documented supported periodic fallback is identified.

---

## 4. Phase 1 — Build the native semantic core

### Goal

Create the new v2.4 execution path without yet deleting all legacy modules.

### 4.1 Static DAG node model

Rewrite `runtime/work_unit.py` into a static node specification.

Required:

- `id`
- `objective`
- `depends_on`
- `ownership`

Optional:

- `interfaces`
- `constraints`
- `local_check`

Remove from the new API:

- unit status transitions;
- attempts;
- results history;
- mandatory verification;
- unit runtime metadata.

Update `runtime/dependency.py` to retain only:

- duplicate/missing dependency validation;
- self-dependency/cycle validation;
- deterministic topological ordering;
- helper(s) needed by the compiler.

Do not keep runtime readiness queues.

### 4.2 Workflow compiler

Add a dedicated Workflow package, e.g.:

~~~text
runtime/workflow/
  compiler.py
  persona.py
  adapter.py
~~~

Responsibilities:

- compile the canonical DAG into TypeScript Workflow;
- create one stable worker persona/template source;
- map DAG dependencies to native await/Promise.all structure;
- use structured child results;
- run optional local checks;
- expose the Workflow run ID to task state;
- keep Conductor node IDs visible in generated instructions/results.

No permit/lease/event emulation inside the script.

### 4.3 Ownership compiler check

Before generating a parallel stage:

- compare ownership scopes of concurrently eligible nodes;
- if overlap exists, serialize deterministically or fail compilation with a clear semantic conflict;
- include tests for disjoint and overlapping path patterns.

### 4.4 Repository writer guard

Add a coarse repository-level active writer reservation.

Requirements:

- at most one active Conductor write Workflow per Git repository;
- no per-unit TTL/generation/heartbeat design;
- recovery must be simple: correlate reservation to task/workflow run and clear only when terminal or explicitly recovered;
- failure must report the conflicting task/workflow ID.

### 4.5 Routing integration

Retain `solo/delegate/audit/full`.

Text routes:

- `solo/audit` → main implementation;
- `delegate/full` → Workflow compiler.

Remove ordinary-text executor selection from the durable route.

Visual route keeps its explicit capability exception.

### Phase 1 tests

Run only:

- Work Unit schema tests;
- DAG validation tests;
- compiler snapshot/structural tests;
- ownership parallelization tests;
- repository writer guard tests;
- one small live Native Workflow smoke test if host access is available.

Do **not** run the full 2.3 suite.

### Exit gate

A bounded text implementation DAG can execute through Native Workflow and return structured results without using dispatcher/wave/permit/lease machinery.

---

## 5. Phase 2 — Replace state, assurance, and recovery; retire legacy execution runtime

### Goal

Make the new path authoritative and delete the proof-heavy legacy task runtime.

### 5.1 Task state re-baseline

Rewrite task state around:

- task ID;
- goal;
- repository;
- route;
- canonical DAG;
- task-level status;
- descriptive phase;
- Workflow run ID;
- validation;
- review;
- quota resume.

Durable statuses:

- active
- waiting_quota
- waiting_user
- blocked
- completed
- cancelled
- failed

Do not retain a strict per-unit state machine.

### 5.2 Task-level change ID

Shrink fingerprint logic to a single task-level `change_id`.

Required behavior:

- deterministic over task-relevant repository change state;
- changes after validation/review invalidate the recorded freshness;
- same calculation used by validation, review, and completion guard.

Remove unit fingerprint APIs and visual-evidence stale machinery unless the retained visual path has a concrete current consumer that requires it.

### 5.3 Main validation state

Record only minimal task-level validation outcome:

- status;
- change_id;
- optional human-readable summary.

Do not create verification receipt files.

Do not execute validation commands through a Conductor subprocess wrapper solely to prove execution.

### 5.4 Review state

Keep native `glm-reviewer` and `visual-reviewer`.

Record:

- reviewer;
- verdict;
- change_id;
- optional note/findings summary.

Remove:

- reviewer invocation markers;
- tool-use authenticity proof;
- review receipt files;
- receipt runner identity.

### 5.5 Completion guard

Replace the current Stop completion machinery with a small task-level completion guard.

Check:

- no active Workflow;
- final changed paths are within DAG ownership union;
- current change_id matches passed main validation;
- assurance=high requires ship review with matching change_id.

Completion releases the repository writer guard.

Do not keep `finalizing`.

### 5.6 Recovery

Rewrite SessionStart recovery to show only:

- unfinished task;
- goal;
- repository;
- task status;
- Workflow run ID;
- quota-wait state;
- next safe action: inspect/resume/reassess.

Remove unit zombie/lease/receipt reconciliation.

Native Workflow Resume is used for execution recovery; the main session reassesses repository truth before final acceptance rather than maintaining legacy per-unit reconcile state.

### 5.7 Delete legacy execution runtime

Once the new path is authoritative, remove or collapse:

- dispatcher;
- dispatch wave / permits;
- per-unit leases;
- legacy task dispatch transactions;
- agent-run ledger;
- per-unit reconcile;
- resume manifest;
- provenance receipts;
- Agent|Task lifecycle hooks;
- Bash regex policy hook;
- text `flash-implementer` custom agent.

Do not keep a hidden compatibility path.

### Phase 2 tests

Run targeted tests for:

- new state schema;
- change_id freshness;
- completion guard;
- high-assurance review freshness;
- recovery summary;
- repository ownership final check;
- visual exception regression where touched.

Remove/rewrite tests that only enforce deleted v2.3 internals.

No full regression yet.

### Exit gate

No normal text implementation task depends on dispatcher, permits, leases, receipts, agent-run ledger, or per-unit recovery state.

---

## 6. Phase 3 — Simplify quota resume and separate the Global Clock

### Goal

Retain only task-level provider-aware resume authorization in Conductor.

### 6.1 Keep the provider observation core

Retain and simplify as needed:

- credentials;
- hardened HTTP;
- provider adapters;
- snapshot parser;
- quota resolver;
- reset-time planning needed for waiting tasks;
- quota diagnostics command if still useful.

The minimal public question is:

> Is the provider currently usable, and if not, what reset information is available?

### 6.2 Remove Conductor quota control plane

Remove from the Conductor architecture/runtime:

- NORMAL/PRESSURE/DRAINING/BLOCKED execution phase;
- threshold-driven worker budgets;
- resident Watcher and watcher store;
- quota Epoch identity;
- task Subscription;
- activation/accounting journal;
- Window Primer;
- Global Quota Clock and clock store;
- Activation Transport abstraction;
- legacy multi-state Wake Bridge;
- internal ZCode SQLite retiming as a production Conductor dependency.

Do not move task-specific state into the future Global Clock project.

### 6.3 Tiny quota-resume authorization

Replace execution policy with a minimal task-level block:

~~~json
{
  "mode": "manual | auto",
  "max_resumes": 0,
  "resume_count": 0,
  "automation_id": null
}
~~~

Rules:

- `auto` requires explicit user authorization;
- `resume_count` increments only when Workflow resume actually starts;
- when budget is exhausted, move to `waiting_user`;
- scheduled turn on a task not in `waiting_quota` is a no-op;
- repeated wake does not require epoch/subscription accounting.

### 6.4 Native Scheduled Task integration

Use supported ZCode Scheduled Task capability established in Phase 0.

Prefer official/native scheduling even if it requires a shorter supported periodic wake.

Do not reintroduce direct SQLite mutation merely to align exactly to a five-hour reset; exact provider-level clock maintenance belongs to the separate Global Quota Clock project.

### 6.5 Global Clock extraction inventory

Before removing Clock-specific files from the Conductor working tree, produce a short inventory document containing:

- reusable provider/reset logic;
- Clock-only modules;
- existing host-adapter constraints;
- historical Window Primer behavior that may inform the new project;
- explicit list of task-specific modules that must **not** migrate.

This inventory is a handoff, not a new-project implementation inside v2.4.

### Phase 3 tests

Run targeted tests for:

- provider parsing/resolver;
- manual vs auto authorization;
- resume budget;
- waiting_quota → resume → active;
- budget exhausted → waiting_user;
- repeated scheduled wake no-op;
- Scheduled Task live smoke established in P0.

Do not run removed Watcher/Epoch/Subscription test suites.

### Exit gate

Conductor quota code has no resident process, global clock, task subscription, epoch accounting, or internal scheduler-DB production write.

---

## 7. Phase 4 — Documentation cleanup, final integration, and release gate

### Goal

Make code, docs, skills, tests, and package metadata describe one v2.4 architecture.

### 7.1 Rewrite current-truth documentation

Update together:

- root README;
- Chinese README if present;
- `docs/architecture.md`;
- `docs/core-concepts.md`;
- `docs/troubleshooting.md`;
- docs index;
- orchestration skill;
- enforcement skill (likely rename/reframe around completion guard if appropriate);
- continuity skill;
- marketplace/plugin descriptions;
- changelog.

Remove claims that v2.4 still has:

- runtime dispatch permit enforcement;
- per-unit lease/runtime states;
- receipt-based provenance;
- Global Quota Clock as a Conductor pillar;
- Task Wake Bridge control plane;
- Bash policy coverage;
- `flash-implementer` as the text execution substrate.

### 7.2 Consistency audit

Search the repository for retired terms/modules and classify every hit:

- intentional history/review evidence;
- migration note;
- stale production documentation/code/test.

No stale production reference may remain unresolved.

### 7.3 Final test sequence

Run once:

1. focused new-runtime integration suite;
2. packaging/plugin validation;
3. live minimal Workflow smoke;
4. live reviewer smoke if feasible;
5. live Scheduled Task resume smoke if feasible;
6. **one full project regression** after the test suite has been rewritten for v2.4.

Do not run the old full suite before deletion/refactoring is complete.

### 7.4 Release acceptance

v2.4 is ready when:

- normal text delegate/full path is Native Workflow only;
- no duplicate per-node runtime truth exists;
- high-assurance review remains fresh-context and read-only;
- ownership prevents unsafe Workflow parallelism;
- one active repository writer invariant is enforced;
- validation/review freshness uses task-level change_id;
- quota auto-resume is bounded and user-authorized;
- Global Quota Clock is absent from Conductor runtime dependency graph;
- documentation matches implementation;
- final full regression passes.

## 8. Expected code-deletion posture

This plan intentionally authorizes deletion after replacement is proven.

Implementation agents should treat large legacy-module removal as a desired outcome, not a regression, when all current consumers have migrated.

Do not split deletion into many micro-phases solely to preserve intermediate green status. Use the phase exit gates above and targeted tests to control risk.

## 9. Out of scope

Not part of v2.4:

- implementation of the new standalone Global Quota Clock project;
- building a generic agent framework;
- adding new routing axes;
- adding new provider foundation models;
- expanding visual orchestration beyond preserving the current exception;
- inventing new provenance/receipt systems;
- optimizing Native Workflow internals that ZCode already owns.
