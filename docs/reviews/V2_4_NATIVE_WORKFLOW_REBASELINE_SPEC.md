# GLM Conductor v2.4 Native-First Re-baseline Specification

> **Status:** target architecture frozen for v2.4 implementation, subject only to the narrow W0 host revalidation in `13.  
> **Current runtime truth:** `docs/architecture.md` continues to describe the released 2.3.x implementation until v2.4 lands.  
> **Purpose:** replace legacy orchestration/runtime duplication with ZCode 3.14 native Workflow and native Scheduled Task capabilities while retaining only Conductor-specific semantics.

## 1. Re-baseline decision

v2.4 is **not** an incremental adapter release over the v2.3 runtime.

It is a complexity re-baseline governed by one rule:

> **If ZCode owns an execution fact, Conductor must not model the same fact again. Conductor persists only semantics that ZCode does not know.**

The target split is:

~~~text
Conductor owns                         ZCode owns
-------------------------------       --------------------------------
routing policy                        workflow execution lifecycle
canonical implementation DAG          child actor lifecycle
ownership semantics                   dependency execution mechanics
task goal / constraints               parallel scheduling
task-level acceptance                 retry / stop / resume mechanics
high-assurance review policy          workflow run observability
final change freshness                background execution
quota auto-resume authorization       scheduled activation substrate
~~~

This specification supersedes the conservative module disposition produced immediately after the ZCode 3.14 spike. The spike remains evidence; this document is the v2.4 architecture decision.

## 2. Project positioning

GLM Conductor v2.4 is a **lightweight semantic orchestration layer for GLM coding workflows in ZCode**.

Its core value is:

1. decide whether work should remain with the main model or be delegated;
2. compile bounded implementation work into a canonical DAG;
3. project that DAG into ZCode Native Workflow execution;
4. preserve ownership and acceptance semantics that the host does not provide;
5. add independent read-only review when assurance is high;
6. optionally resume a suspended workflow after provider quota becomes usable, only when the user has authorized it.

v2.4 is explicitly **not** a second task runtime, workflow engine, permission engine, provenance system, scheduler platform, or quota control plane.

## 3. Global Quota Clock boundary

The Global Quota Clock has independent value but is removed from the glm-conductor responsibility boundary.

Its provider/account-level purpose is to keep five-hour quota windows timely by observing real reset information and performing a low-cost refresh/materialization action after each window boundary.

That functionality should become a separate companion project.

### 3.1 Hard separation

The future Global Quota Clock project may own:

- provider identity;
- provider quota snapshot parsing;
- five-hour reset observation;
- clock state;
- periodic low-cost refresh/materialization;
- its own scheduler integration.

It must not know about:

- Conductor task IDs;
- Work Units;
- routing modes;
- implementation DAGs;
- ownership;
- reviewer policy;
- task-level resume accounting.

Conversely, **glm-conductor v2.4 must not depend on Global Quota Clock for correctness**.

A Conductor task waiting for quota must remain resumable through its own native Scheduled Task path even if no Global Clock is installed.

## 4. Selective routing remains canonical

The two independent axes remain:

- **Delegability:** `low | high`
- **Assurance:** `standard | high`

The matrix remains:

| Delegability | Assurance | Route | Implementation | Independent review |
| --- | --- | --- | --- | --- |
| low | standard | `solo` | main session | no |
| high | standard | `delegate` | Native Workflow | no |
| low | high | `audit` | main session | yes |
| high | high | `full` | Native Workflow | yes |

### 4.1 Removed executor axis

`executor` is no longer an independent durable routing dimension for ordinary text work.

For text tasks:

- `solo/audit` imply main-session implementation;
- `delegate/full` imply Native Workflow implementation.

Visual implementation remains a capability exception (`10), not a second general orchestration substrate.

Routing may still be reassessed when new evidence changes delegability or assurance.

## 5. Canonical implementation DAG

A Work Unit is demoted from a durable mini-task into a **static DAG node specification**.

Target shape:

~~~yaml
id: parser
objective: implement parser behavior
depends_on: []
ownership:
  - src/parser/**
interfaces:
  - public parse() signature unchanged
constraints:
  - no schema migration
local_check:
  - pytest tests/parser -q   # optional
~~~

Required semantic fields:

- `id`
- `objective`
- `depends_on`
- `ownership`

Optional semantic fields:

- `interfaces`
- `constraints`
- `local_check`

### 5.1 Removed Work Unit runtime semantics

The following are removed from the canonical Work Unit model:

- unit `status`;
- attempt counters;
- result history;
- unit verification state;
- unit receipts;
- unit fingerprint freshness;
- unit leases;
- dispatch permits;
- unit lifecycle journal events;
- unit recovery state machine.

A DAG node describes **what must be done**, not **where execution currently is**.

### 5.2 Local checks are optional

A local check is a cheap node-health gate used only when a downstream dependency would be unsafe without it.

It is not durable provenance.

A documentation-only or trivially independent node may have no local check.

Worker-local checks establish only:

> “this node is healthy enough for dependent implementation to proceed.”

They do not replace main-session task-level acceptance.

## 6. Native Workflow compiler

The primary delegated implementation path is:

~~~text
Canonical Conductor DAG
        ↓
Workflow Compiler
        ↓
Generated ZCode Native Workflow
        ↓
Native execution / parallelism / background / retry / resume
~~~

The compiler is the primary new v2.4 runtime component.

It must:

- validate the DAG;
- preserve stable node IDs;
- generate worker personas/instructions from one canonical template;
- express dependency ordering with native Workflow control flow;
- use native parallelism for independent nodes;
- run optional local checks where specified;
- return structured aggregate results;
- preserve enough correlation to map Conductor node IDs to workflow ask/site records when diagnostics require it.

It must **not** implement its own:

- ready queue;
- dispatch waves;
- concurrency scheduler;
- background manager;
- retry manager;
- child lifecycle ledger;
- ask replay;
- workflow resume engine.

### 6.1 Whole eligible DAG per Workflow

The default unit of delegated execution is one eligible implementation DAG per Native Workflow run, not one Workflow per legacy dispatch wave.

A typical projection is:

~~~text
stage 1: A || B
stage 2: C || D
stage 3: E
~~~

The main session should not repeatedly launch individual workflow waves unless a concrete task boundary requires a new run.

## 7. Runtime truth and task state

v2.4 has two distinct truth domains:

~~~text
Static semantic truth   → Conductor task/DAG state
Execution runtime truth → ZCode Workflow run
~~~

Conductor must not persist per-node runtime states such as `running`, `verifying`, or `completed`.

The durable task state should be small and task-level. Conceptually:

~~~json
{
  "task_id": "...",
  "goal": "...",
  "repository": "...",
  "route": {
    "delegability": "high",
    "assurance": "high",
    "mode": "full"
  },
  "dag": [],
  "status": "active",
  "phase": "workflow",
  "workflow_run_id": "...",
  "validation": {},
  "review": {},
  "quota_resume": {}
}
~~~

Target durable statuses:

- `active`
- `waiting_quota`
- `waiting_user`
- `blocked`
- `completed`
- `cancelled`
- `failed`

`phase` may describe `planning | workflow | validating | reviewing`, but it is descriptive rather than a second strict transition machine.

Legacy states such as `preflight`, `routed`, `decomposed`, `joining`, `verifying`, `reviewing`, and `finalizing` are not retained as durable lifecycle states.

## 8. Ownership and repository concurrency

WF-11 established that concurrent Workflow children can silently overwrite the same file. Conductor therefore retains ownership semantics.

### 8.1 Compile-time ownership

Before Workflow launch:

- validate ownership patterns;
- detect ownership overlap between nodes that may execute concurrently;
- serialize conflicting nodes or reject the invalid parallel plan.

The compiler may convert an unsafe parallel relationship into an explicit dependency.

### 8.2 Final task-level scope check

Before acceptance:

~~~text
actual changed paths ⊆ union(DAG ownership)
~~~

This is a task-level check, not a per-node completion gate.

### 8.3 One active write Workflow per repository

v2.4 deliberately chooses a coarse repository writer invariant:

> **A Git repository may have at most one active Conductor write Workflow at a time.**

This replaces the legacy per-unit lease system.

Workflow-internal parallelism is allowed after compile-time ownership validation.

Separate independent Conductor write Workflows targeting the same repository do not run concurrently.

The guard should be a small repository-level writer lock/reservation. It must not reintroduce per-unit TTL/generation/heartbeat lease machinery.

## 9. Minimal assurance model

v2.4 retains task-level acceptance but removes the proof-heavy receipt system.

### 9.1 Worker-local evidence

Workers may run optional `local_check` commands.

Their reports are useful implementation output, but not authoritative final acceptance.

### 9.2 Main-session validation

After the Workflow finishes, the GLM-5.3 main session:

- inspects the accumulated diff;
- checks task scope and interfaces;
- runs limited, targeted integration/build/test commands appropriate to the full task;
- decides whether the user's goal is satisfied.

It must not mechanically rerun every worker-local check.

### 9.3 Single task-level change identity

Retain one deterministic freshness mechanism:

~~~text
change_id = hash(base revision + current changed-file content/state)
~~~

Main validation records:

~~~json
{
  "status": "passed",
  "change_id": "sha256:..."
}
~~~

If the repository changes afterwards, validation is stale.

### 9.4 High-assurance review

For `assurance=high`, invoke a fresh-context native Custom Subagent reviewer after main validation.

Persist only the minimal outcome:

~~~json
{
  "required": true,
  "reviewer": "glm-reviewer",
  "verdict": "ship",
  "change_id": "sha256:..."
}
~~~

If the repository changes after review, the review is stale.

Removed review machinery:

- invocation authenticity chain;
- tool-use ID proof;
- review receipts directory;
- runtime runner identity;
- duplicate journal/state receipt mirrors.

## 10. Model roles and visual exception

### 10.1 Text implementation

The legacy `flash-implementer` Custom Subagent is not a v2.4 execution path.

Text `delegate/full` implementation uses Native Workflow workers generated from one canonical worker persona/template.

This avoids maintaining two text implementation substrates.

### 10.2 Reviewers

Retain:

- `glm-reviewer`
- `visual-reviewer`

Reason: high assurance needs fresh context plus host-enforced role-specific model/tool isolation. The spike showed that Workflow actor labels do not consume existing Custom Subagent model/tool contracts.

### 10.3 Visual implementation

Retain `visual-implementer` as a capability exception until Native Workflow is separately proven to preserve the required multimodal/screenshot feedback contract.

This exception must not become a second general text implementation path.

## 11. Hook and enforcement surface

Remove orchestration hooks whose purpose was to enforce the legacy Agent dispatch runtime:

- Agent|Task PreToolUse permit gate;
- background forcing;
- Agent launch ownership advisory injection;
- PostToolUse permit consumption;
- agent launch/failure lifecycle bookkeeping;
- reviewer invocation bookkeeping;
- Bash regex permission policy.

The Bash policy is removed because the primary Workflow child path does not pass through the main-session Bash hook; keeping it would imply safety coverage that the primary execution path does not actually receive.

Use ZCode native permission facilities plus Conductor worker constraints instead.

### 11.1 Lightweight hooks that remain

- **SessionStart:** small unfinished-task recovery context.
- **Stop:** small task completion guard.
- scheduler/activation hooks only if strictly required by the final native Scheduled Task implementation after W0.

### 11.2 Task Completion Guard

The completion guard checks only high-value invariants:

1. no active write Workflow remains for a task being completed;
2. final changed paths are within task ownership;
3. required main validation exists and its `change_id` matches current change state;
4. for `assurance=high`, a `ship` review exists and its `change_id` matches current change state.

It does not scan per-unit receipts, permits, leases, tool-use IDs, or verification journals.

`finalizing` is removed; completion is an explicit guarded task-level operation.

## 12. Quota-aware task resume

Quota continuity is reduced to the smallest task-level capability not provided by Workflow itself.

Target state:

~~~json
{
  "quota_resume": {
    "mode": "manual",
    "max_resumes": 0,
    "resume_count": 0,
    "automation_id": null
  }
}
~~~

Supported modes:

- `manual`
- `auto`

`max_resumes=1` replaces the old `auto_once` concept.  
`max_resumes=N` provides bounded multi-window continuation.

Automatic resume always requires explicit user authorization.

### 12.1 Waiting path

On provider quota exhaustion:

~~~text
task.status = waiting_quota
workflow_run_id preserved
~~~

A native ZCode Scheduled Task provides the future execution opportunity.

On a scheduled turn:

1. load the task;
2. if task is not `waiting_quota`, no-op;
3. refresh provider quota;
4. if still exhausted/unusable, remain waiting;
5. if usable and authorization/budget permit, resume the same Workflow run;
6. increment `resume_count` only when the resume actually starts;
7. once `max_resumes` is exhausted, transition to `waiting_user`.

No quota epoch, task subscription, activation accounting, or boundary-consumption proof is required.

### 12.2 Quota modules retained

Retain only the parts needed to answer:

- current provider quota status;
- relevant reset time for an exhausted task;
- whether a resume attempt is currently reasonable.

Provider/credential/HTTP hardening remains valuable.

The execution-phase system (`NORMAL/PRESSURE/DRAINING/BLOCKED`) is removed.

## 13. W0 host revalidation gate

Implementation must not begin by assuming two still-unverified host facts.

W0 is deliberately narrow.

### W0-A — WF-06/WF-07 clean revalidation

Re-run only the hook visibility cases with:

- current glm-conductor 2.3.2 loaded;
- a functioning Python hook runner;
- a fresh ZCode 3.14 session.

Goal: distinguish structural Workflow bypass from the contaminated original environment.

This does not reopen the full Workflow spike.

### W0-B — Native Scheduled Task resume path

On current ZCode 3.14 verify:

1. a suitable periodic Scheduled Task can be created using supported host capability;
2. a scheduled turn can inspect and resume an existing Workflow run.

Exact five-hour quota-window maintenance is **not** a Conductor W0 requirement; it belongs to the separate Global Quota Clock project.

If native scheduling cannot express an exact five-hour interval, Conductor should prefer a supported shorter periodic wake over reintroducing direct writes to ZCode internal SQLite state.

## 14. Legacy compatibility policy

v2.4 must not preserve the v2.3 runtime merely to migrate old internal state.

Rules:

- released v2.3.x remains recoverable through Git history/tags;
- v2.4 may detect legacy active task state and provide a clear upgrade/recovery message;
- do not maintain dual legacy/new dispatch engines;
- do not maintain dual unit state machines;
- do not automatically convert per-unit receipts/leases/permits into v2.4 state unless a concrete real task demonstrates that migration is necessary.

Prefer finishing or explicitly retiring active v2.3 tasks before switching the runtime to v2.4.

## 15. Module disposition

| Current component | v2.4 disposition |
| --- | --- |
| routing matrix | **KEEP / simplify** |
| `work_unit.py` | **REWRITE as static DAG node** |
| `dependency.py` | **KEEP small: DAG validation/compiler helper** |
| `dispatcher.py` | **REMOVE** |
| `dispatch_wave.py` / permits | **REMOVE** |
| `task_manager.py` | **REPLACE with small task/workflow lifecycle layer** |
| `agent_run.py` | **REMOVE** |
| `lease.py` | **REMOVE** |
| `ownership.py` | **KEEP / task-level + compile-time use** |
| `state.py` | **REWRITE small task-level schema** |
| `journal.py` | **KEEP primitive / drastically shrink event vocabulary** |
| `provenance.py` | **REMOVE** |
| verification/review receipts | **REMOVE** |
| `fingerprint.py` | **SHRINK to task-level change_id** |
| `reconcile.py` | **REMOVE** |
| `resume_manifest.py` | **REMOVE** |
| `recovery.py` | **REWRITE small** |
| `policy.py` | **REMOVE** |
| Agent lifecycle hooks | **REMOVE** |
| Bash policy hook | **REMOVE** |
| Stop gate | **REWRITE as small completion guard** |
| SessionStart recovery | **KEEP / rewrite small** |
| `flash-implementer` agent | **REMOVE** |
| `visual-implementer` | **KEEP as exception** |
| `glm-reviewer` / `visual-reviewer` | **KEEP** |
| provider/credentials/HTTP quota layer | **KEEP** |
| quota execution phases | **REMOVE** |
| Watcher / watcher store | **REMOVE from Conductor** |
| quota epoch | **REMOVE from Conductor** |
| quota subscription/accounting | **REMOVE** |
| Window Primer | **REMOVE from Conductor; concept belongs with Global Clock** |
| Global Quota Clock | **EXTRACT to separate project** |
| Activation Transport abstraction | **REMOVE** |
| current Task Wake Bridge | **REPLACE with tiny quota-resume scheduling state** |
| `execution_policy.py` | **REWRITE to quota-resume authorization only** |
| internal ZCode SQLite scheduler adapter | **REMOVE from Conductor production path** |

## 16. Testing discipline

v2.4 implementation must avoid the repeated full-regression pattern seen in earlier development.

Rules:

- use focused unit/integration tests during each implementation phase;
- test changed contracts and their immediate consumers;
- run targeted Workflow host probes only where host behavior matters;
- do not run the entire legacy suite after every deletion batch;
- perform one migration-level/full regression at the final integration gate;
- tests for components deliberately removed should be removed or rewritten with the component, not kept as obligations that force the legacy design to survive.

## 17. Freeze statement

The v2.4 design is frozen around this principle:

> **GLM Conductor owns semantic orchestration and acceptance; ZCode owns execution orchestration.**

The target implementation should favor deleting or collapsing legacy runtime code over wrapping it behind compatibility adapters.

New architecture discussion should reopen only if W0 reveals a host capability contradiction that directly invalidates this target.
