# GlobalQuotaClock — Standalone Project Plan

> **Status:** PLANNED / READY FOR NEW-REPOSITORY BOOTSTRAP  
> **Proposed repository:** `Chengy257/GlobalQuotaClock`  
> **CLI / package working name:** `gqclock`  
> **Origin:** extracted from GLM Conductor v2.3 Global Quota Clock during the v2.4 native-workflow re-baseline.  
> **Relationship to glm-conductor:** independent companion project; **no runtime dependency in either direction**.

## 1. Decision

Global Quota Clock remains scientifically/operationally useful, but it no longer belongs inside GLM Conductor.

The new project is an **account/provider-level quota-window maintainer**:

> Observe the provider-reported rolling quota window, identify the real next reset boundary, and—after that boundary—perform at most one explicitly authorized low-cost materialization request so the next quota window becomes available promptly.

It is **not**:

- a coding-task orchestrator;
- a ZCode workflow manager;
- a task-resume engine;
- a repository-aware daemon;
- a quota-budget allocator;
- a replacement for glm-conductor continuity;
- a generic background-agent framework.

The new repository must be usable without glm-conductor installed.

## 2. Why a standalone project

The Global Clock operates on a different semantic level from coding-task orchestration.

```text
GlobalQuotaClock
provider/account identity
        ↓
quota-window observation
        ↓
real reset boundary
        ↓
authorized low-cost materialization
        ↓
confirm window identity advanced
        ↓
repeat indefinitely
```

By contrast:

```text
glm-conductor
coding task
  ↓
Workflow execution
  ↓
quota exhausted
  ↓
waiting_quota
  ↓
bounded task resume
```

The two may observe the same provider quota endpoint, but they must not share task state or lifecycle semantics.

## 3. v1 product definition

v1 should answer four questions reliably:

1. **What is the current provider/account quota state?**
2. **When is the next meaningful reset boundary reported by the provider?**
3. **Has that boundary already materialized into a new window?**
4. **If not, may the project perform one low-cost materialization request, and did that request actually advance the window?**

Primary user workflow:

```text
gqclock status
gqclock plan
gqclock tick
```

A scheduler repeatedly invokes `gqclock tick`. The command itself remains single-shot and idempotent.

## 4. Hard project boundary

### GlobalQuotaClock owns

- provider/account identity;
- provider quota observation;
- normalized quota-window snapshots;
- five-hour and weekly reset semantics;
- clock-target decision logic;
- durable per-boundary attempt state;
- explicit user authorization for active materialization;
- low-cost materialization request;
- pre/post materialization observation;
- confirmation that window identity advanced;
- scheduler-facing `tick` CLI;
- user-level diagnostics and history.

### GlobalQuotaClock does not own

- glm-conductor task IDs;
- Work Units or DAGs;
- repository ownership;
- ZCode Workflow run IDs;
- code validation/review;
- task `waiting_quota` state;
- bounded task resume budgets;
- task wake bridge;
- coding-session placement;
- project-specific journals.

No such fields should appear in v1 state schemas.

## 5. Scheduling strategy

### 5.1 Primary v1 strategy — scheduler-neutral single-shot CLI

The core project must not require a resident process.

`gqclock tick` is safe to invoke repeatedly and decides whether any action is currently due.

Recommended external cadence:

- **5–10 minute polling** when timely materialization matters;
- longer cadence is acceptable when the user values fewer observations over prompt reset activation.

The scheduler may be:

- Windows Task Scheduler;
- systemd timer;
- cron;
- another trusted local scheduler.

The core correctness contract must not depend on which scheduler invokes it.

### 5.2 Optional ZCode Scheduled Task adapter

ZCode Scheduled Tasks may be documented as an optional convenience integration, not the primary clock substrate.

Current official ZCode documentation says scheduled tasks:

- support custom repeat rules based on hour/day/week/month/year intervals;
- run against a local project;
- require the computer to be awake;
- do not trigger while the app is closed;
- are skipped rather than replayed when the host was unavailable.

Therefore a ZCode Scheduled Task can periodically invoke `gqclock tick`, but GlobalQuotaClock must not promise exact reset-time activation or offline correctness through this adapter.

### 5.3 Explicitly forbidden

v1 must not:

- edit ZCode's internal SQLite automation database;
- depend on undocumented `tasks-index.sqlite` schema;
- rewrite ZCode `next_run_at`;
- inject into arbitrary coding sessions;
- assume a scheduled turn is a fresh session.

The old direct-SQLite adapter is historical evidence only.

## 6. v1 architecture

Recommended repository shape:

```text
GlobalQuotaClock/
├── README.md
├── pyproject.toml
├── src/gqclock/
│   ├── cli.py
│   ├── config.py
│   ├── state.py
│   ├── identity.py
│   ├── clock.py
│   ├── materialize.py
│   ├── history.py
│   ├── quota/
│   │   ├── provider.py
│   │   ├── _http.py
│   │   ├── credentials.py
│   │   ├── parser.py
│   │   ├── resolver.py
│   │   ├── time_utils.py
│   │   ├── window_math.py
│   │   ├── zai.py
│   │   └── bigmodel.py
│   └── scheduler/
│       ├── common.py
│       ├── windows.py
│       ├── systemd.py
│       └── cron.py
├── tests/
└── docs/
    ├── ARCHITECTURE.md
    ├── SECURITY.md
    ├── SCHEDULING.md
    └── MIGRATION_FROM_GLM_CONDUCTOR.md
```

The scheduler package may initially generate/install instructions rather than directly mutating system schedulers. Keep scheduler integration replaceable.

## 7. Core state model

State belongs at the **user/account level**, not a Git repository.

Suggested default location:

```text
~/.global-quota-clock/
  config.json
  state.json
  history.jsonl
  lock
```

Use OS-appropriate config/state directories if a small dependency-free helper can do so cleanly; otherwise document the stable path above for v1.

Conceptual state:

```json
{
  "schema_version": 1,
  "providers": {
    "<provider_identity_hash>": {
      "provider": "zai",
      "last_snapshot": {},
      "last_observed_at": "...",
      "last_five_hour_reset_at": "...",
      "next_target_at": "...",
      "last_boundary_id": "...",
      "materialization": {
        "boundary_id": "...",
        "authorized": true,
        "attempted": false,
        "attempted_at": null,
        "result": null
      }
    }
  }
}
```

Never persist:

- API keys;
- Authorization headers;
- raw provider responses;
- full model request/response bodies;
- glm-conductor task information.

## 8. Provider observation

Start with the provider adapters already proven in glm-conductor:

- Z.ai;
- BigModel.

Reuse behavior, not package coupling.

Carry forward:

- HTTPS only;
- strict host allowlist;
- no redirects;
- bounded timeout;
- bounded response body;
- normalized provider errors;
- credentials memory-only;
- raw response not persisted;
- provider snapshot normalization;
- four-state observation:
  - AVAILABLE
  - PRESSURE
  - EXHAUSTED
  - UNKNOWN

The new project may copy/adapt the proven implementation under its own package, preserving license/history attribution as appropriate. It must not import `glm-conductor.runtime`.

## 9. Window semantics

### 9.1 Never calculate “five hours from now”

The five-hour boundary comes only from the provider-reported `reset_at`.

Never use:

```text
now + 5 hours
```

as a substitute.

### 9.2 Weekly suppression

If a weekly window is blocking/exhausted:

- do not perform a five-hour materialization request;
- park/recheck relative to the relevant weekly reset;
- if reset cannot be parsed, remain conservative and retry observation later.

### 9.3 Clock decisions

Keep a pure decision engine with three broad outcomes:

```text
reset_target
short_retry
weekly_park
```

Inputs are normalized observations + current time + previous boundary identity.

The decision module must perform no network, state write, scheduler mutation, or model call.

## 10. Materialization semantics

This is the most sensitive part of the project.

### 10.1 User authorization

Active materialization is opt-in.

Default:

```text
materialization_enabled = false
```

The user must explicitly enable it for a provider/account.

Observation/status commands remain usable without materialization authorization.

### 10.2 One attempt per boundary

Use an idempotency key such as:

```text
(provider_identity_hash, five_hour_boundary_id)
```

For one boundary:

- at most one automatic materialization request;
- persist the attempt before/around the action using crash-safe ordering;
- ambiguous timeout must not automatically resend;
- repeated scheduler ticks must not create duplicate model calls.

### 10.3 Confirmation rule

A successful model HTTP response alone is **not** proof of materialization.

Proof requires a provider observation after the attempt showing the five-hour window identity/reset advanced relative to the pre-attempt boundary.

Likewise, percentage movement alone is not proof.

### 10.4 Minimal request

The materialization client should use:

- the lowest-cost verified eligible model;
- minimal output;
- bounded timeout;
- no user conversation content;
- a fixed innocuous prompt;
- no task/repository context.

The exact provider endpoint/model must be independently revalidated in the new project; do not blindly inherit the old v2.3 endpoint forever.

## 11. CLI v1 surface

Keep the user surface small.

Recommended:

```text
gqclock status [--json]
gqclock plan [--json]
gqclock tick [--json]
gqclock enable-materialization <provider>
gqclock disable-materialization <provider>
gqclock history [--limit N]
gqclock doctor
gqclock schedule-plan [--platform windows|systemd|cron|zcode]
```

Potential later command:

```text
gqclock materialize --now
```

but v1 should avoid encouraging ad-hoc repeated manual calls. If provided, it must use the same per-boundary idempotency gate.

## 12. Failure philosophy

### Observation path — fail open / no active action

Provider unavailable or malformed:

```text
UNKNOWN
→ record diagnostic
→ no materialization
→ retry observation later
```

### Materialization path — fail closed

If authorization/state/boundary identity is ambiguous:

```text
do not call the model
```

A false negative costs one opportunity to materialize promptly.

A false positive consumes real quota and changes provider state.

This asymmetry is intentional.

## 13. Implementation milestones

Keep the project in four substantial stages.

### Stage 0 — Bootstrap + live assumptions

Create the standalone repository and freeze:

- package/CLI naming;
- Python baseline;
- state/config paths;
- provider endpoint/model assumptions;
- scheduler support boundary;
- security model.

Perform narrowly scoped live probes:

- quota endpoint still returns the expected reset/window semantics;
- materialization endpoint/model is available;
- pre/post forced quota refresh can observe a boundary identity;
- ZCode Scheduled Task limitations match current official behavior.

No production auto-materialization yet.

### Stage 1 — Observation and pure clock core

Implement:

- provider adapters;
- credentials/security boundary;
- parser/resolver;
- identity hash;
- time/window math;
- pure clock decision;
- state/history primitives;
- `status`, `plan`, `doctor`.

Test with deterministic fixtures; no real model call required for normal test suite.

### Stage 2 — Authorized materialization transaction

Implement:

- config authorization;
- boundary identity;
- single-attempt durable transaction;
- low-cost materialization client;
- pre/post force-refresh confirmation;
- weekly suppression;
- `tick`;
- history/audit records.

Add failure-injection tests for:

- timeout;
- network failure;
- provider unavailable;
- state write interruption;
- duplicate tick;
- process crash around attempt recording;
- reset already advanced before action.

### Stage 3 — Scheduler integration + real-window dogfood + v0.1

Add:

- scheduler-neutral docs;
- Windows Task Scheduler plan;
- systemd timer plan;
- cron plan;
- optional ZCode Scheduled Task plan;
- installation examples;
- security/operations docs.

Dogfood at at least one real five-hour boundary:

- observe old boundary;
- reach due/grace;
- execute one authorized materialization attempt;
- force-refresh;
- confirm or honestly record unconfirmed result;
- prove repeated ticks do not duplicate the attempt.

Then perform one final regression and release v0.1.0.

## 14. Testing strategy

The normal suite must not depend on waiting five hours.

Use:

- fixture snapshots;
- injected clocks;
- fake providers;
- fake materializer;
- temporary user-state directories;
- crash/failure injection.

Live tests are a separate opt-in suite and must never run from ordinary CI with real credentials.

CI should cover at least:

- Linux + Windows;
- supported minimum/current Python;
- lint;
- unit/integration suite;
- credential-leak scans;
- packaging/import smoke.

## 15. Security invariants

Mechanical tests should anchor:

1. no credential value written to disk;
2. no Authorization header in logs/errors/history;
3. no raw provider response persisted;
4. allowlisted HTTPS hosts only;
5. bounded response body;
6. redirects rejected or fully revalidated;
7. active materialization disabled by default;
8. one automatic attempt per boundary;
9. malformed/unknown quota state can never trigger materialization;
10. no ZCode internal database writes.

## 16. Relationship contract with glm-conductor

The projects may share **conceptual schema**, not runtime state.

Allowed relationship:

```text
GlobalQuotaClock
→ maintains account/provider quota-window freshness

glm-conductor
→ observes provider quota for a coding task
→ may resume its own Workflow when quota is usable
```

Not allowed:

```text
GlobalQuotaClock reads Conductor task state
GlobalQuotaClock resumes Workflow runs
Conductor waits for GlobalQuotaClock state
Conductor requires GlobalQuotaClock to be installed
```

If a future integration is useful, it should be through a read-only status API/CLI such as:

```text
gqclock status --json
```

and remain optional.

## 17. Definition of Done for v0.1

v0.1 is complete when:

- standalone install works without glm-conductor;
- Z.ai/BigModel quota observation works with normalized output;
- provider-reported reset drives scheduling decisions;
- weekly suppression works;
- active materialization is disabled by default and explicitly authorized;
- duplicate scheduler ticks cannot duplicate a boundary materialization call;
- pre/post observation is used to confirm window advancement;
- no secrets/raw responses are persisted;
- Windows + Linux scheduler guidance exists;
- optional ZCode integration contains no internal DB writes;
- a real-boundary dogfood report exists;
- full tests/CI pass.

## 18. Explicit non-goals for v0.1

- cloud-hosted service;
- multi-user server;
- web UI;
- glm-conductor plugin dependency;
- coding-task resume;
- dynamic ZCode SQLite retiming;
- generic quota budgeting across arbitrary applications;
- automatic credential discovery beyond explicitly supported safe sources;
- aggressive retry of ambiguous materialization calls.
