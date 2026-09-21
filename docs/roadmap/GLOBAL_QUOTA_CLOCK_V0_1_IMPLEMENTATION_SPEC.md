# GlobalQuotaClock v0.1 — New Repository Bootstrap and Implementation Spec

> **Target repository:** `Chengy257/ZcodeGlobalQuotaClock`  
> **Working package/CLI:** `gqclock`  
> **Parent handoff:** `glm-conductor/docs/roadmap/GLOBAL_QUOTA_CLOCK_EXTRACTION_INVENTORY.md`  
> **Authority:** `GLOBAL_QUOTA_CLOCK_STANDALONE_PROJECT_PLAN.md`.

## 1. Repository bootstrap

The standalone repository now exists. Implement in `Chengy257/ZcodeGlobalQuotaClock`; do not add it as a subdirectory or git submodule of glm-conductor.

Initial tree:

```text
ZcodeGlobalQuotaClock/
├── README.md
├── LICENSE
├── pyproject.toml
├── .gitignore
├── docs/
│   ├── ARCHITECTURE.md
│   ├── SECURITY.md
│   ├── SCHEDULING.md
│   ├── PROVIDER_CONTRACT.md
│   └── MIGRATION_FROM_GLM_CONDUCTOR.md
├── src/gqclock/
│   ├── __init__.py
│   ├── cli.py
│   ├── config.py
│   ├── state.py
│   ├── identity.py
│   ├── clock.py
│   ├── materialize.py
│   ├── history.py
│   ├── quota/
│   │   ├── __init__.py
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
│       ├── __init__.py
│       ├── common.py
│       ├── windows.py
│       ├── systemd.py
│       └── cron.py
└── tests/
```

Use a `src/` layout and installable console script:

```toml
[project.scripts]
gqclock = "gqclock.cli:main"
```

Do not import code from the glm-conductor repository at runtime.

## 2. Stage 0 — bootstrap and live-assumption closure

### S0-A Documentation baseline

Before implementation, freeze:

- project positioning;
- state schema;
- credential boundary;
- provider contract;
- clock decision table;
- materialization transaction;
- scheduler strategy;
- ZCode integration limitations.

`docs/ARCHITECTURE.md` is the implementation truth source once Stage 1 starts.

### S0-B Provider observation probe

For each initially supported provider:

- verify current quota endpoint host/path;
- verify auth header shape;
- capture a redacted fixture;
- confirm parser can distinguish five-hour and weekly windows;
- confirm reset timestamps are provider-supplied;
- confirm forced refresh behavior.

Store only redacted fixtures.

Never commit credentials or raw user payloads.

### S0-C Materialization probe

Verify the current minimum-cost eligible API call:

- endpoint;
- model id;
- auth form;
- minimum viable request;
- timeout behavior;
- whether a successful request is reflected by a subsequent quota observation.

The probe does **not** authorize production automation.

Record uncertainty explicitly.

### S0-D Scheduler fact review

Record current host facts:

- ZCode Scheduled Tasks are optional only;
- current official behavior requires the machine awake and ZCode running;
- no internal SQLite adapter is permitted;
- primary v1 production recommendation is an OS/local scheduler invoking the single-shot CLI.

### Stage 0 exit

Proceed only when provider observation and one candidate materialization call are independently understood well enough to implement without copying stale assumptions.

## 3. Stage 1 — observation/core

### S1-A Provider package

Adapt/copy the proven glm-conductor quota observation logic into the new namespace.

Required contracts:

```python
class QuotaProvider:
    def fetch(self, force=False) -> dict: ...
```

Normalized snapshot:

```json
{
  "provider": "zai",
  "plan_generation": null,
  "plan_level": null,
  "fetched_at": "...",
  "status": "AVAILABLE",
  "windows": [
    {
      "kind": "five_hour",
      "used_percent": 10,
      "remaining_percent": 90,
      "reset_at": "..."
    }
  ]
}
```

Do not persist raw transport responses.

### S1-B Identity

Define a stable non-secret `provider_identity_hash`.

Inputs may include:

- provider;
- account/profile identity when safely available;
- endpoint family.

Never hash the API key as the only account identity if a safer provider account identifier exists. If no stable non-secret account identity is available, use a local user-configured profile name plus provider.

### S1-C State/history

Implement atomic writes and a simple exclusive process lock.

State must support multiple provider profiles.

History events should be compact:

```text
observed
target_planned
materialization_authorized
materialization_attempted
materialization_confirmed
materialization_unconfirmed
provider_error
```

No task/workflow vocabulary.

### S1-D Pure clock

Implement a pure decision function.

Conceptual input:

```python
next_clock_target(
    snapshot,
    now,
    last_boundary_id=None,
    grace_seconds=120,
    retry_delay_seconds=300,
)
```

Conceptual output:

```json
{
  "decision": "reset_target | short_retry | weekly_park",
  "target_at": "...",
  "reason": "...",
  "boundary_id": "...",
  "window_kind": "five_hour | weekly | null"
}
```

No I/O inside this module.

### S1-E CLI observation commands

Implement:

- `status`;
- `plan`;
- `doctor`;
- `history`.

All support deterministic JSON where appropriate.

### S1 tests

Focus on:

- parser fixtures;
- provider error classes;
- credentials non-persistence;
- clock table;
- duplicate/multiple windows;
- invalid reset timestamps;
- weekly suppression;
- atomic state;
- process-lock behavior.

No real model call in standard tests.

## 4. Stage 2 — materialization transaction

### S2-A Configuration

Config shape should separate:

```text
observation.enabled
materialization.enabled
materialization.model
materialization.grace_seconds
materialization.retry_delay_seconds
```

Materialization defaults disabled.

### S2-B Boundary identity

Define `boundary_id` from provider profile + the observed five-hour reset boundary.

It must be stable across repeated ticks for the same boundary.

### S2-C Attempt state machine

Keep the state small:

```text
not_due
due
attempt_reserved
attempted
confirmed
unconfirmed
suppressed_weekly
```

This is **clock-boundary state**, not agent/task state.

Critical ordering:

1. observe;
2. decide due;
3. acquire process lock;
4. reload state and re-evaluate;
5. durably reserve the boundary attempt;
6. issue at most one materialization request;
7. force-refresh quota observation;
8. mark confirmed/unconfirmed;
9. release lock.

A crash after step 5 must not allow an automatic duplicate request.

### S2-D Materialization client

Use an isolated client with:

- allowlisted HTTPS host;
- minimal fixed prompt;
- bounded request;
- minimal output;
- redacted exceptions;
- no conversation/repository context;
- no raw response persistence.

Do not use the quota-observation endpoint as proof of request delivery.

### S2-E Confirmation

Compare the post-attempt observed five-hour window identity with the pre-attempt boundary.

Confirmed only when the provider reports a genuinely advanced window identity/reset.

HTTP success alone is not confirmed.

### S2-F `tick`

`gqclock tick` is the production heart.

It must be safe under:

- repeated invocation;
- concurrent invocation;
- scheduler duplicates;
- provider outage;
- weekly exhaustion;
- unknown reset;
- already advanced reset;
- ambiguous materialization timeout.

Exit codes must distinguish:

- success/no-op;
- observation unavailable;
- materialization suppressed/not authorized;
- structural/configuration error.

Do not use an exit code that invites external schedulers to blindly retry a potentially already-sent materialization request.

### S2 tests

Add failure injection around every boundary between steps 3–8.

At minimum prove:

- two concurrent ticks → ≤1 materialization call;
- crash after durable reservation → later tick does not repeat call;
- ambiguous timeout → no automatic retry;
- reset already advanced → no call;
- weekly exhausted → no five-hour call;
- materialization disabled → no call;
- malformed observation → no call;
- confirmed result requires observed boundary advance.

## 5. Stage 3 — scheduling and release

### S3-A Scheduler-neutral contract

Document the only required external contract:

```text
periodically run:
    gqclock tick --profile <name>
```

The scheduler does not need to understand quota semantics.

### S3-B Windows

Provide a Task Scheduler plan/generator that:

- uses the installed `gqclock` executable;
- runs every 5–10 minutes by default;
- uses the current user account;
- does not require admin privileges when avoidable;
- logs only sanitized stdout/stderr.

Do not silently install a scheduled task unless the user explicitly requests it.

### S3-C Linux

Provide:

- systemd user timer example;
- cron fallback example.

Prefer systemd user timers where available.

### S3-D Optional ZCode integration

Document an optional ZCode Scheduled Task profile.

It should invoke `gqclock tick` only.

State the limitations explicitly:

- host must be awake;
- ZCode must be running;
- schedule granularity/behavior is controlled by ZCode;
- missed triggers may be skipped;
- not suitable as the sole correctness substrate for exact boundary activation.

Never edit ZCode internal DB.

### S3-E Real boundary dogfood

Run one controlled real-boundary validation with materialization explicitly authorized.

Record:

- pre-boundary snapshot;
- planned target;
- scheduler/tick time;
- attempt reservation;
- materialization request outcome;
- forced post-observation;
- confirmation status;
- subsequent duplicate tick behavior.

Do not publish credentials/raw responses.

### S3-F v0.1 release gate

Run one full suite after Stage 3 is otherwise complete.

Release only if:

- unit/integration suite green;
- Windows/Linux CI green;
- packaging install/smoke green;
- credential-leak scan green;
- real-boundary dogfood completed or release explicitly remains pre-release until it does.

## 6. Migration disposition from glm-conductor

### Adapt/copy conceptually

- quota/provider.py
- quota/_http.py
- quota/credentials.py
- quota/parser.py
- quota/resolver.py
- quota/time_utils.py
- quota/window_math.py
- provider-specific adapters
- old clock decision table from extraction inventory
- Primer materialization confirmation/idempotency principles

### Rewrite

- account/profile identity;
- user-level state;
- clock decision module;
- materialization transaction;
- scheduler integration;
- CLI;
- docs/tests.

### Do not migrate

- task state;
- Work Units;
- Workflow run ids;
- continuity/resume;
- subscriptions;
- epochs as task accounting;
- wake bridge;
- activation transport;
- task journal;
- repository ownership;
- reviewer/validation semantics;
- direct ZCode scheduler SQLite writes.

## 7. Suggested first implementation handoff

After the new repository exists, give the implementation agent:

```text
Implement Stage 0 and Stage 1 only from docs/roadmap/V0_1_IMPLEMENTATION_PLAN.md.
Treat docs/ARCHITECTURE.md as the design truth source once created.
Do not implement active materialization before Stage 0 provider/materialization probes are documented and reviewed.
Do not import glm-conductor at runtime; adapt/copy only the provider observation behavior identified in MIGRATION_FROM_GLM_CONDUCTOR.md.
Run focused tests only; no real credential/network calls in the normal test suite.
```

Do not start Stage 2 automatically until Stage 0/1 review confirms the provider and materialization assumptions.
