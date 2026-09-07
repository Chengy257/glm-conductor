# GLM Conductor v2 — Upgrade & Implementation Guide

> Repository: `https://github.com/Chengy257/glm-conductor`  
> Target version: **v2**  
> Product direction: **From Prompt Contract to Enforced Contract**  
> Purpose: Guide the next major implementation phase without turning GLM Conductor into a heavyweight generic workflow engine.  
>
> This document assumes the v1.x architecture is already stable:
>
> - Delegability × Assurance routing
> - `solo / delegate / audit / full`
> - GLM-5.3 architect
> - GLM-5.3-Flash standard / visual execution
> - fresh-context read-only review
> - `foreground / resumable / idle` continuity
>
> v2 should preserve these foundations and add **runtime enforcement, evidence integrity, context preparation, and bounded parallelism**.
>
> **Phase 0 amendments (2026-08-28).** Runtime verification on ZCode 3.9.2 (see `docs/glm-conductor-v2-phase0-runtime-verification.md`) confirmed the hook mechanism and closed all spike unknowns, with two design consequences folded into this guide:
>
> 1. **Subagent tool calls do not fire hooks** (subagent sub-sessions are constructed without a hook runner; the PreToolUse emission point no-ops). Write-side pre-blocking of subagent edits is therefore impossible on the current runtime — the Ownership Gate is redefined as **completion-side verification + dispatch-time injection** (§8-12 amended).
> 2. Confirmed runtime contracts: Stop continuation capped at 3 per turn; PreToolUse supports `permissionDecision` (allow/ask/deny) and `updatedInput`; hooks must run via `python3` (`node` is not on the hook PATH on Windows); hooks load per-session only (plugin hooks auto-enable the runner; no hot reload); `run_in_background` parallel dispatch verified working; quota monitoring endpoints accept Coding Plan API keys directly (verified end-to-end on this machine).
>
> Identity decision (confirmed): **TASK_ID fully replaces CONTINUITY_ID in v2** (§6).

---

# 1. Why v2

GLM Conductor v1.x already provides a coherent orchestration policy:

```text
GLM-5.3
    ↓
Delegability × Assurance
    ↓
solo / delegate / audit / full
    ↓
executor selection
    ↓
parent verification
    ↓
optional independent review
    ↓
continuity
```

However, most safety and workflow rules are still enforced primarily through prompts and role contracts.

Examples:

```text
"Only edit owned files."
"Do not claim completion without verification."
"Any repair invalidates the previous review."
"Reviewer must remain read-only."
```

These are good contracts, but they remain model-followed conventions.

The primary objective of v2 is:

> Convert the highest-value orchestration contracts into deterministic runtime invariants.

Target architecture:

```text
GLM Conductor v2
        │
        ├── Judgment Layer
        │     Delegability
        │     Assurance
        │     Route
        │     Executor
        │     Review need
        │
        └── Enforcement Layer
              Ownership Gate
              Permission Gate
              Verification Gate
              Review Freshness Gate
              Completion Gate
```

---

# 2. v2 design principles

## 2.1 Preserve the four-route model

Do NOT add new route modes.

Keep:

```text
solo
delegate
audit
full
```

v2 is not a routing redesign.

---

## 2.2 Enforcement should target invariants, not judgment

Do not move ambiguous architectural decisions into deterministic scripts.

Good deterministic targets:

- whether a file is owned;
- whether verification evidence exists;
- whether current diff matches reviewed diff;
- whether a destructive command is allowed;
- whether completion conditions are satisfied.

Keep these in the LLM judgment layer:

- architecture choice;
- requirement interpretation;
- delegability;
- assurance;
- semantic correctness;
- review reasoning.

---

## 2.3 Prefer ZCode-native mechanisms

Use native:

- Plugin Hooks
- Subagents
- Skills
- Goal
- Scheduled Tasks
- Idle-time Tasks
- existing Browser / Computer behavior

Avoid inventing:

- custom agent daemon;
- quota polling server;
- external orchestration service;
- custom workflow runtime unless later justified.

---

## 2.4 Keep GLM Conductor lightweight

Do not turn v2 into:

- a generic CI platform;
- a generic project manager;
- a memory database;
- an arbitrary workflow DSL;
- an autonomous multi-agent social network.

The project should remain:

> A lightweight GLM-native orchestration and assurance layer for ZCode.

---

# 3. External projects and ideas worth borrowing

The following classes of projects motivate v2.

## 3.1 ZOdyssey-style deterministic gates

Most useful ideas:

- PreToolUse gates
- scope locks
- plan/review gates
- write restrictions
- bounded parallelism
- phase-state enforcement

Primary lesson:

> Do not merely tell the model what it must not do; block invalid actions at runtime where possible.

---

## 3.2 Morning Star / harness-engine pattern

Useful concept:

```text
Skills = judgment
Engine = deterministic policy
```

GLM Conductor should borrow the separation, but not the full heavyweight workflow engine.

---

## 3.3 open-dynamic-workflows

Useful ideas:

- run journal
- resumable node execution
- deterministic parallel / pipeline control

Do not add a workflow DSL in v2.

---

## 3.4 Aider repo-map concept

Useful idea:

> Build a compact task context pack instead of sending broad repository context to the implementation model.

---

## 3.5 OpenCode / permission-profile design

Useful idea:

```text
action × resource → allow / ask / deny
```

This maps well to ZCode Hook enforcement.

---

# 4. Recommended v2 scope

## P0 — Core v2

1. Runtime Task State
2. Ownership Gate
3. Completion / Verification Gate
4. Evidence Fingerprint
5. Review Freshness Gate

## P1 — Strongly recommended

6. **Quota-Aware Continuity**
7. Explore Routing Preflight
8. Task Context Pack
9. Execution Journal
10. Route-aware Permission Policy

## P1.5 — Task execution management

11. **Task & Work-Unit Management**
12. Dependency Graph / Ready Queue
13. Quota-aware Dispatch Control
14. Join / Parent Verification Semantics

## P2 — Optional after P0/P1.5 stabilize

15. File Lease
16. Bounded Parallel Delegation

## Explicitly deferred

- generic persistent memory;
- workflow DSL;
- arbitrary agent teams;
- learned routing;
- benchmark-driven routing;
- cost-aware routing;
- multi-provider model marketplace.

---

# 5. New v2 runtime state layer

Introduce structured runtime state.

Recommended layout:

```text
.glm-conductor/
└── tasks/
    └── <task-id>/
        ├── state.json
        ├── checkpoint.md
        ├── events.jsonl
        └── visual-evidence/
```

For normal short tasks, `checkpoint.md` may be absent.

For resumable / idle tasks, keep the existing checkpoint behavior.

---

# 6. Task identity

Use a single stable runtime identifier:

```text
TASK_ID
```

Recommended format:

```text
<semantic-slug>-<unique-suffix>
```

Example:

```text
refactor-auth-7f3a2c
redesign-settings-a92c1d
```

For long continuity tasks:

```text
CONTINUITY_ID == TASK_ID
```

This reduces duplicated identity concepts.

**Confirmed decision (Phase 0 review): TASK_ID fully replaces CONTINUITY_ID in v2.** The checkpoint field is renamed to `TASK_ID`; `CONTINUITY_ID` is retired across contracts, docs, and the validator in the Phase 1 rename block (v1.x checkpoints remain readable as legacy input, normalized on resume).

---

# 7. `state.json`

Recommended schema:

```json
{
  "task_id": "refactor-auth-7f3a2c",
  "goal": "Refactor authentication middleware",
  "route": {
    "mode": "full",
    "delegability": "high",
    "assurance": "high",
    "executor": "flash-implementer",
    "continuity": "foreground"
  },
  "ownership": {
    "files": [
      "src/auth.ts",
      "tests/auth.test.ts"
    ]
  },
  "verification": {
    "required": [
      "pytest tests/auth.test.ts"
    ],
    "completed": [],
    "fingerprint": null
  },
  "review": {
    "required": true,
    "reviewer": "glm-reviewer",
    "verdict": null,
    "fingerprint": null
  },
  "work_units": [
    {
      "id": "wu-auth-core",
      "objective": "Refactor authentication middleware",
      "status": "ready",
      "depends_on": [],
      "executor": "flash-implementer",
      "ownership": ["src/auth.ts"],
      "verification": ["pytest tests/auth.test.ts"],
      "attempt": 0
    }
  ],
  "dispatch": {
    "max_workers": 1,
    "active": []
  },
  "status": "active"
}
```

This file becomes the deterministic state source for Hook enforcement.

Repository files remain authoritative for code state.

---

# 8. P0 — Ownership Gate（Phase 0 修订版）

## 8.1 Goal

Turn:

```text
Only edit owned files.
```

into an enforced invariant — **verified at the completion boundary, not pre-blocked at write time**:

```text
A task cannot pass the Completion Gate while its diff
contains changes outside the declared ownership.
```

Phase 0 verified that subagent tool calls do not fire hooks on the current ZCode runtime (subagent sub-sessions carry no hook runner), so the original write-side design is not implementable. The gate moves to the two places where hooks DO run: **dispatch time** and **completion time** (both main-session contexts).

---

# 9. Ownership Gate design（Layer A + Layer B）

## Layer A — completion-side verification (canonical, deterministic)

At the Stop gate — and optionally at PostToolUse on the Agent/Task tool — verify:

```text
active TASK_ID?
    ↓
load ownership from state.json
    ↓
list files touched by current diff (git status/diff)
    ↓
touched ⊆ ownership?
   /        \
 yes         no
  │           │
proceed     BLOCK with the exact out-of-scope paths
```

Unowned changes are not prevented from happening, but they can never silently pass completion. This composes naturally with evidence fingerprints (§17-22), which are computed over owned files anyway.

## Layer B — dispatch-time injection (advisory, prompt-level)

A PreToolUse hook with matcher `Agent|Task` runs in the main session before every delegation:

```text
PreToolUse(Agent|Task)
    ↓
active TASK_ID with declared ownership?
    ↓
inject additionalContext: ownership contract reminder
(optionally updatedInput appends the ownership list
to the dispatch prompt)
```

Layer B cannot deterministically constrain the subagent, but it raises compliance and keeps ownership visible at every dispatch.

## Layer C — deferred runtime evolution

If ZCode later wires hook runners into subagent sessions (feature request to be filed), the original per-write blocking design can be restored as a hardening layer on top of A + B.

---

# 10. Ownership exceptions（Phase 0 修订版）

Main GLM-5.3 architect may need broader privileges.

Recommended rule:

```text
executor subagent
    → Layer A completion verification + Layer B dispatch injection

main architect
    → project-level policy (its own tool calls DO fire hooks,
      so per-write policy is enforceable here)
```

Reviewer read-only（Phase 0 修订）: hooks do not run inside reviewer subagent contexts either, so **hook-level write denial for reviewers is not implementable on the current runtime**. Reviewer read-only is enforced by:

```text
1. tools whitelist in the agent definition (primary, deterministic)
2. role contract (prompt layer)
3. Layer A: any reviewer write would surface as an out-of-scope
   diff change and block the completion gate (defense in depth)
```

Do not claim hook-enforced reviewer read-only until Layer C lands.

---

# 11. Ownership path rules

Support:

```text
exact file
directory prefix
glob
```

Example:

```json
{
  "ownership": {
    "allow": [
      "src/auth/**",
      "tests/auth/**"
    ]
  }
}
```

Reject implicit expansion outside the declared scope.

---

# 12. Bash write detection（Phase 0 修订版：仅主会话范围）

Subagent Bash calls do not pass through hooks (§8 amendment); this detection applies to **main-session Bash only**. Do not attempt perfect shell parsing in v2.

Start with high-value patterns.

Potential write-risk commands:

```text
rm
mv
cp
sed -i
perl -pi
tee
cat > file
truncate
git checkout --
git restore
git reset
```

Use:

```text
allow
ask
deny
```

rather than trying to understand all shell semantics.

---

# 13. P0 — Verification Gate

## 13.1 Goal

Turn:

```text
A completion claim without evidence is invalid.
```

into a runtime completion condition.

---

# 14. Verification state

When the main session reruns a required command, record:

```json
{
  "command": "pytest tests/auth",
  "status": "pass",
  "timestamp": "...",
  "fingerprint": "..."
}
```

Only parent-observed verification may satisfy the final gate.

Worker self-reported verification remains a claim.

---

# 15. Stop Completion Gate

Use the ZCode `Stop` hook.

Concept:

```text
Agent tries to finish
      ↓
active task?
      ↓
ownership: touched ⊆ owned?        (Layer A, §9)
      │
   no ├── block with out-of-scope paths
      │
     yes
      ↓
required verification complete?
      │
   no ├── block
      │
     yes
      ↓
review required?
      │
   no ├── ship
      │
     yes
      ↓
valid fresh review?
      │
   no ├── block
      │
     yes
      ↓
ship
```

Recommended block messages should be actionable.

Example:

```text
Completion blocked:
required parent verification is incomplete.

Missing:
- pytest tests/auth
- npm run lint
```

---

# 16. Stop Hook loop safety

ZCode may re-enter Stop logic when the model continues.

Maintain a bounded retry strategy.

Do not create infinite completion loops.

If required evidence cannot be produced:

```text
STATUS: blocked
```

and report the exact missing requirement to the user.

---

# 17. P0 — Evidence Fingerprint

## 17.1 Goal

Bind verification and review evidence to the actual repository state.

Current rule:

```text
Any repair invalidates the previous review.
```

v2 should enforce this automatically.

---

# 18. Worktree fingerprint

Compute a stable fingerprint from the relevant change set.

Recommended inputs:

```text
base revision
+
git diff for task-owned / reviewed files
```

Potential representation:

```text
sha256(normalized_diff)
```

Do not hash unrelated untracked files unless they affect the task.

---

# 19. Verification fingerprint

When parent verification succeeds:

```json
{
  "verification": {
    "status": "valid",
    "fingerprint": "sha256:abc123"
  }
}
```

If the worktree changes:

```text
current fingerprint != verification fingerprint
```

then:

```text
VERIFICATION STATUS: stale
```

---

# 20. Review fingerprint

Reviewer verdict:

```json
{
  "review": {
    "verdict": "ship",
    "fingerprint": "sha256:abc123"
  }
}
```

Any later modification:

```text
sha256:def456
```

automatically makes:

```text
REVIEW STATUS: stale
```

Final completion gate must reject stale review.

---

# 21. Review Freshness Gate

Before final acceptance:

```text
review.required == true
AND
review.verdict == ship
AND
review.fingerprint == current_fingerprint
```

Otherwise completion is blocked.

This is a high-value v2 invariant.

---

# 22. Visual evidence fingerprint

For visual tasks, review freshness should also include visual evidence identity.

Example:

```json
{
  "visual_evidence": [
    {
      "path": ".glm-conductor/tasks/.../visual-evidence/001.png",
      "sha256": "..."
    }
  ]
}
```

If screenshots are replaced after review, visual review becomes stale.

---

# 23. P1 — Quota-Aware Continuity

## 23.1 Goal

Upgrade `resumable` continuity from periodic retry only to a quota-aware scheduler when authenticated Coding Plan usage data is available.

```text
resumable
    ↓
quota snapshot available?
   / \\
 yes  no
 │     └────────────→ periodic liveness fallback
 ▼
evaluate 5h + weekly windows
 │
 ├── AVAILABLE → continue
 ├── PRESSURE → checkpoint at next safe milestone
 └── EXHAUSTED
        ↓
     reset known?
       /   \\
     yes    no
      │      └────→ periodic fallback
      ▼
 exact reset-aware resume
      ↓
 wake → force refresh quota → resume/re-plan
```

Quota awareness belongs to the **Continuity Layer**, not the routing layer.

Do NOT add quota as a third routing axis and do NOT automatically switch route/model because quota is low. Quota determines **when work can continue**, not **who should perform it**.

---

# 24. Quota Provider abstraction

Introduce a normalized provider boundary:

```text
Continuity
   │
   ├── Checkpoint Manager
   ├── Resume Scheduler
   └── Quota Provider
          │
          ├── Z.ai
          ├── BigModel
          └── unavailable / fallback
```

Conceptual interface:

```text
QuotaProvider.fetch()
        ↓
QuotaSnapshot

QuotaPolicy.evaluate(snapshot)
        ↓
AVAILABLE | PRESSURE | EXHAUSTED | UNKNOWN

ResumePlanner.plan(snapshot)
        ↓
continue | checkpoint | resume_at | periodic_fallback
```

Provider-specific raw response shapes must remain isolated inside adapters.

---

# 25. Monitoring endpoint compatibility

Current Coding Plan usage can be programmatically queried through authenticated monitoring endpoints used by Z.ai product/plugin code. Known host families include:

```text
https://api.z.ai
https://open.bigmodel.cn
```

Observed endpoint families include:

```text
/api/monitor/usage/quota/limit
/api/monitor/usage
/api/monitor/usage/model-usage
/api/monitor/usage/tool-usage
```

Treat these as **officially used monitoring interfaces**, not as a permanently stable public OpenAPI contract.

Therefore:

- isolate endpoints behind adapters;
- support compatibility probing/fallback only for explicitly verified paths;
- never spread endpoint strings through the orchestration code;
- retain periodic liveness fallback if monitoring breaks.

---

# 26. Coding Plan V2/V3 parser compatibility

The parser MUST tolerate both older and newer quota representations.

Observed item types may include:

```text
TOKENS_LIMIT
CREDIT_LIMIT
TIME_LIMIT
```

Do not identify windows solely by `type` or array position.

Use semantic fields when present. Current known mappings:

```text
unit = 3, number = 5
→ five_hour

unit = 6, number = 1
→ weekly
```

The parser must be fixture-driven and tolerant of additive unknown fields.

**Verified live (Phase 0, bigmodel `lite` plan): the response may contain only the five-hour TOKENS_LIMIT window plus the TIME_LIMIT MCP lane, with NO weekly entry.** Weekly windows are therefore OPTIONAL in the normalized snapshot — the parser must not assume two coding windows exist. Fields observed end-to-end: `percentage` (used %), `currentValue`/`usage`/`remaining`, `usageDetails[]`, `nextResetTime` (epoch ms), `level` (plan label).

---

# 27. Normalized QuotaSnapshot

Recommended internal representation:

```json
{
  "provider": "zai",
  "plan_generation": "v3",
  "fetched_at": "2026-08-28T05:00:00Z",
  "status": "AVAILABLE",
  "windows": [
    {
      "kind": "five_hour",
      "used_percent": 82,
      "remaining_percent": 18,
      "reset_at": "2026-08-28T06:45:02Z"
    },
    {
      "kind": "weekly",
      "used_percent": 45,
      "remaining_percent": 55,
      "reset_at": "2026-09-01T12:00:00Z"
    }
  ]
}
```

Normalize provider fields such as `usage`, `currentValue`, `remaining`, `percentage`, and `nextResetTime` before continuity logic consumes them.

---

# 28. Quota state vocabulary

Use four states:

```text
AVAILABLE
PRESSURE
EXHAUSTED
UNKNOWN
```

Suggested semantics:

```text
AVAILABLE
remaining > configured pressure threshold

PRESSURE
0 < remaining <= threshold

EXHAUSTED
remaining == 0 or used_percent >= 100

UNKNOWN
provider unsupported, query/auth failure, incompatible response,
or insufficient reset information
```

Quota monitoring is an optimization, so monitor failure should generally be **fail-open**:

```text
quota query fails
    ↓
UNKNOWN
    ↓
periodic liveness fallback
```

---

# 29. Pressure-aware checkpointing

Add configurable proactive checkpointing:

```json
{
  "quota_policy": {
    "checkpoint_threshold_percent": 10
  }
}
```

When the 5-hour window enters `PRESSURE`:

```text
finish current atomic step
      ↓
checkpoint at safe milestone
      ↓
avoid starting a large new implementation phase
```

Never interrupt an unsafe atomic operation solely because the threshold was crossed.

---

# 30. Blocking-window resume calculation

The scheduler MUST consider every exhausted blocking window.

Example:

```text
5h exhausted     → reset A
weekly exhausted → reset B
```

Correct resume planning:

```text
resume_at = max(A, B)
```

Then apply a small configurable grace period.

Do not schedule against only the 5-hour reset when weekly quota is also exhausted.

---

# 31. Missing reset time

Never fabricate reset time using assumptions such as:

```text
now + 5 hours
```

If quota is exhausted but `reset_at` is unknown:

```text
checkpoint
    ↓
periodic liveness fallback
```

Explicit unknown is preferable to a false precise schedule.

---

# 32. Exact reset-aware scheduling

When all blocking windows provide reset times:

```text
EXHAUSTED
   ↓
checkpoint
   ↓
calculate resume_at
   ↓
schedule resume_at + grace
   ↓
wake
   ↓
force quota refresh
   ↓
AVAILABLE → resume
EXHAUSTED → re-plan
UNKNOWN   → periodic fallback
```

A scheduled wake must always re-query quota before implementation resumes.

---

# 33. Periodic liveness fallback remains mandatory

Quota-aware scheduling enhances the existing continuity design; it does not replace it.

```text
Quota API available
      ↓
precise reset-aware scheduling

Quota API unavailable / incompatible
      ↓
periodic scheduled liveness probe
```

`resumable` must remain functional without quota monitoring.

---

# 34. Query timing and caching

Do NOT query quota on every tool call.

Recommended refresh points:

```text
task start
route selected
before a large delegated phase
after a substantial milestone
on recognized quota-related model failure
before scheduling resume
after scheduled wake
```

Use a short cache TTL such as 1–5 minutes when appropriate. Explicit quota-related provider failures should force refresh.

---

# 35. Quota-related model failures

When a model request fails with a confidently classified Coding Plan quota error:

```text
quota error
   ↓
QuotaProvider.fetch(force=true)
   ↓
checkpoint
   ↓
ResumePlanner
```

Do not classify generic network/provider failures as quota exhaustion without strong evidence.

---

# 36. Credential acquisition modes

Support three modes:

```text
native
provider-api
unavailable
```

## native

Preferred future path when ZCode exposes a documented plugin-safe usage/quota interface.

## provider-api

Use an explicitly configured Coding Plan/provider credential to call the monitoring adapter.

## unavailable

If the signed-in ZCode account does not expose a plugin-safe credential or native API, quota-aware scheduling is unavailable and continuity falls back to periodic reactivation.

Do NOT read undocumented private ZCode authentication stores as the normal design.

---

# 37. Credential security policy

Credentials MUST NOT be written to:

```text
.glm-conductor/
state.json
events.jsonl
checkpoint.md
logs
stdout/stderr
```

HTTP requirements:

- HTTPS only;
- strict host allowlist;
- initial allowlist: `api.z.ai`, `open.bigmodel.cn`;
- short timeout;
- bounded response size;
- no credential forwarding to arbitrary user-supplied hosts;
- redirects disabled or revalidated against the allowlist;
- raw responses not persisted by default.

Authentication formatting should follow the currently verified official provider implementation rather than guesswork.

---

# 38. Endpoint compatibility strategy

Do not hard-code one endpoint as eternally canonical.

```text
provider adapter
      ↓
preferred verified endpoint
      ↓
success?
 /        \\
yes       known compatibility failure
 │                 │
parse       verified fallback endpoint
```

Fallback endpoints must be explicit and tested. Do not probe arbitrary paths or retry authentication failures indefinitely.

---

# 39. Quota state in `state.json`

Extend task runtime state with normalized, non-secret quota data:

```json
{
  "quota": {
    "source": "provider-api",
    "provider": "zai",
    "status": "PRESSURE",
    "last_checked": "2026-08-28T05:00:00Z",
    "five_hour": {
      "used_percent": 94,
      "remaining_percent": 6,
      "reset_at": "2026-08-28T06:45:02Z"
    },
    "weekly": {
      "used_percent": 61,
      "remaining_percent": 39,
      "reset_at": "2026-09-01T12:00:00Z"
    }
  }
}
```

Never store authorization headers or credentials.

---

# 40. Quota journal events

Allowed normalized events:

```json
{"event":"quota_status","status":"PRESSURE","five_hour_remaining":6}
{"event":"quota_checkpoint","reason":"pressure"}
{"event":"quota_exhausted","resume_at":"2026-08-28T06:47:02Z"}
{"event":"quota_resume_check","status":"AVAILABLE"}
```

Do not journal complete provider responses.

---

# 41. Idle remains preferred for unattended work

Quota-aware resumable scheduling does not replace `idle`.

```text
Long task
   │
   ├── unattended acceptable
   │       ↓
   │      idle
   │
   └── foreground/session continuity required
           ↓
       resumable
           ↓
      quota-aware scheduler
```

---

# 42. Reset cards are out of scope

Do not automate UI reset-card redemption or browser clicks as part of quota-aware continuity.

Only integrate reset-card behavior if ZCode later exposes a documented plugin API.

---

# 43. Quota diagnostic surface

Before scheduling integration, provide a read-only diagnostic surface conceptually equivalent to:

```text
glm-conductor quota
```

Example output:

```text
GLM CODING PLAN

5-hour:
  used: 82%
  remaining: 18%
  reset: 1h 42m

weekly:
  used: 45%
  remaining: 55%
  reset: 3d 4h

source:
  provider-api
```

A JSON form should be available for tests/automation. This may be implemented as a ZCode skill/command rather than a standalone binary.

---

# 44. Quota parser fixtures

Required fixtures:

```text
legacy TOKENS_LIMIT
V3 CREDIT_LIMIT
TIME_LIMIT entries
five_hour only
weekly only
both exhausted
missing nextResetTime
401 / auth failure
endpoint unavailable
malformed response
unknown additive fields
```

Parser tests must run offline.

---

# 45. Quota scheduler smoke tests

Required scenarios:

1. AVAILABLE → continue.
2. PRESSURE → checkpoint at safe milestone.
3. 5-hour exhausted + weekly available → schedule 5-hour reset.
4. weekly exhausted → schedule weekly reset.
5. both exhausted → schedule later reset.
6. exhausted + missing reset → periodic fallback.
7. quota API unavailable → periodic fallback.
8. wake after reset → force refresh before resume.
9. still exhausted after wake → re-plan.
10. credentials never appear in task state, checkpoint, journal, or logs.

---

# 46. Quota-Aware Continuity acceptance criteria

- [ ] Quota is not a routing axis.
- [ ] `foreground / resumable / idle` remains intact.
- [ ] Quota monitoring is optional.
- [ ] Provider responses are normalized behind adapters.
- [ ] Both TOKENS_LIMIT and CREDIT_LIMIT fixtures are supported.
- [ ] Five-hour and weekly windows are independently tracked.
- [ ] Multiple exhausted windows use the latest blocking reset.
- [ ] Missing reset time never causes fabricated scheduling.
- [ ] Periodic liveness fallback remains operational.
- [ ] Monitor/API failure does not break normal orchestration.
- [ ] Credentials are never persisted.
- [ ] Scheduled wake rechecks quota before resuming implementation.
- [ ] Pressure-aware checkpointing occurs only at safe milestones.
- [ ] Idle remains preferred for suitable unattended work.

---

# 47. P1 — Explore Routing Preflight

## 47.1 Problem

Delegability is sometimes judged before enough repository evidence exists.

Examples:

- root cause unknown;
- relevant files unknown;
- test entrypoints unknown;
- interface boundaries uncertain.

Do not force an early route decision with weak evidence.

---

# 48. New preflight concept

Add:

```text
ROUTING PREFLIGHT
```

This is NOT a route.

Logic:

```text
Enough evidence to judge?
       │
   yes │ no
       │
       ▼
   Explore
       │
 root cause
 relevant files
 interfaces
 tests
 dependencies
       │
       ▼
Delegability × Assurance
```

---

# 49. Explore agent

Prefer the ZCode built-in read-only Explore capability if available.

Do not add another custom discovery agent unless necessary.

Preflight output:

```text
ROUTING PREFLIGHT REPORT

ROOT CAUSE:
...

RELEVANT FILES:
- ...

INTERFACES:
- ...

TEST ENTRYPOINTS:
- ...

HIDDEN COUPLING:
- ...

OPEN AMBIGUITY:
- ...
```

---

# 50. Preflight trigger

Use preflight when one or more are unknown:

- root cause;
- implementation boundary;
- ownership set;
- interface contract;
- verification plan;
- hidden coupling risk.

Skip it when the task is already explicit.

---

# 51. P1 — Task Context Pack

The parent should package bounded context for the executor.

Recommended format:

```text
TASK CONTEXT PACK

TASK:
...

ROOT CAUSE:
...

RELEVANT FILES:
- ...

KEY SYMBOLS:
- ...

CALL / DATA FLOW:
...

INTERFACES:
...

OWNERSHIP:
...

TEST ENTRYPOINTS:
...

KNOWN RISKS:
...

EXCLUDED AREAS:
...
```

This complements the five-part implementation contract.

---

# 52. Why Context Pack matters

The architecture becomes:

```text
GLM-5.3
  expensive reasoning / exploration
          ↓
compressed context pack
          ↓
GLM-5.3-Flash
  bounded execution
```

This is a strong fit for the project's heterogeneous model design.

---

# 53. Context Pack rules

Do not:

- dump the whole repository;
- copy huge files;
- duplicate full diffs unnecessarily;
- include speculative facts as settled constraints.

Keep it evidence-based and compact.

---

# 54. P1 — Execution Journal

Add:

```text
events.jsonl
```

Example:

```json
{"event":"route_selected","mode":"full","task_id":"auth-7f3a2c"}
{"event":"implementation_started","agent":"flash-implementer"}
{"event":"verification","command":"pytest","status":"pass","fingerprint":"abc"}
{"event":"review","verdict":"ship","fingerprint":"abc"}
{"event":"completed"}
```

---

# 55. Journal purpose

The journal is NOT telemetry.

It is local execution provenance.

Uses:

- debugging;
- resume;
- audit;
- stale evidence detection;
- future visualization;
- reconstructing phase history.

---

# 56. Journal constraints

- append-only;
- task-scoped;
- local;
- no secret values;
- no full prompts;
- no full source code;
- no unnecessary token/accounting data.

---

# 57. P1 — Route-aware Permission Policy

Introduce a policy layer.

Example:

```text
action × resource → allow / ask / deny
```

---

# 58. Suggested policies

## flash-implementer

Allow:

```text
read owned/relevant files
edit owned files
run bounded verification
```

Ask or deny:

```text
git push
git reset --hard
rm -rf
modify secrets
modify CI/CD outside ownership
```

## visual-implementer

Same as Flash plus visual evidence access.

## reviewer

Deny:

```text
Edit
Write
destructive Bash
```

## high-assurance task

Require `ask` for:

```text
destructive operations
schema migrations
permission/auth changes
release operations
```

---

# 59. Policy evaluation

Hook input:

```text
role
route
assurance
tool
resource
```

Policy output:

```text
allow
ask
deny
```

This should remain simple and inspectable.

Avoid a complex policy DSL in v2.

---

# 60. P1.5 — Task & Work-Unit Management

## Goal

Upgrade task handling from a single task state into a minimal, explicit execution model:

```text
Parent Task
    │
    ├── Work Unit A
    ├── Work Unit B
    └── Work Unit C
```

A **Work Unit** is the smallest bounded implementation unit that GLM Conductor may dispatch independently.

This is not a generic workflow DSL.

Its purpose is to support:

- explicit task decomposition;
- dependency-aware execution;
- bounded multi-subagent dispatch;
- safe resume;
- quota-aware dispatch suppression;
- deterministic ownership / lease coordination;
- parent-controlled join and verification.

---

# 61. Work Unit contract

Recommended Work Unit representation:

```json
{
  "id": "wu-auth-tests",
  "objective": "Add regression coverage for authentication middleware",
  "status": "waiting_dependency",
  "depends_on": ["wu-auth-core"],
  "executor": "flash-implementer",
  "ownership": [
    "tests/auth/**"
  ],
  "interfaces": [
    "public auth middleware behavior remains unchanged"
  ],
  "verification": [
    "pytest tests/auth"
  ],
  "attempt": 0,
  "result": null
}
```

Required properties:

```text
id
objective
status
depends_on
executor
ownership
verification
```

Optional properties:

```text
interfaces
constraints
attempt
result
lease
```

A Work Unit MUST remain bounded enough to receive the normal five-part implementation specification.

---

# 62. Work Unit status model

Use a deliberately small state vocabulary:

```text
pending
waiting_dependency
ready
running
waiting_quota
blocked
verifying
completed
failed
cancelled
```

Recommended transitions:

```text
pending
   ↓
waiting_dependency
   ↓
ready
   ↓
running
   ↓
verifying
   ↓
completed
```

Alternative transitions:

```text
ready/running → waiting_quota
ready/running → blocked
running/verifying → failed
any nonterminal → cancelled
```

Do not create dozens of micro-states.

Task-level review remains primarily a parent-task concern; individual Work Units normally finish at `completed` after parent-observed unit verification.

---

# 63. Dependency Graph

Work Units form a small directed acyclic dependency graph.

Example:

```text
A ─────┐
       ├──→ C ───→ E
B ─────┘

D ─────────────→ E
```

Represent dependencies with:

```json
{
  "depends_on": ["wu-a", "wu-b"]
}
```

Rules:

1. Dependencies MUST reference Work Units in the same parent task.
2. Cycles are invalid.
3. A Work Unit cannot become `ready` until all dependencies are `completed`.
4. A failed or blocked dependency prevents downstream dispatch until the parent resolves the condition.
5. The graph is runtime scheduling state, not a user-facing workflow programming language.

---

# 64. Ready Queue

The Task Manager derives a `ready` set rather than allowing workers to self-select tasks.

A Work Unit is dispatchable only when all are true:

```text
status == ready
dependencies completed
executor available
ownership scope valid
required lease acquirable
concurrency slot available
quota policy permits new dispatch
task is not blocked/cancelled
```

Concept:

```text
Task Graph
    ↓
derive ready units
    ↓
policy filters
    ↓
bounded dispatch
```

Do not use “latest unfinished task” as a scheduling rule.

Always identify the exact `TASK_ID` and `WORK_UNIT_ID`.

---

# 65. Bounded Dispatcher

The main GLM-5.3 session remains the only orchestrator.

The dispatcher MAY launch multiple ready Work Units, but only within a configured bound.

Example state:

```json
{
  "dispatch": {
    "strategy": "bounded-parallel",
    "max_workers": 3,
    "active": [
      "wu-a",
      "wu-b"
    ]
  }
}
```

The dispatcher is responsible for:

- selecting ready Work Units;
- acquiring leases before dispatch;
- passing the Work Unit contract;
- tracking active workers;
- receiving implementation reports;
- transitioning units to verification;
- never exceeding `max_workers`.

Subagents do not dispatch other subagents.

---

# 66. Parallel eligibility per Work Unit

A Work Unit may run concurrently only if:

- its ownership is disjoint from other concurrently running units, or protected by a valid lease model;
- required interfaces are already fixed;
- it has no unmet dependency;
- its verification can be evaluated independently enough to detect local failure;
- it does not require another running unit's uncommitted intermediate state.

If any of these are uncertain:

```text
do not parallelize
```

Serial execution is always a valid fallback.

---

# 67. Quota-aware Dispatch Control

Quota-Aware Continuity must integrate with the dispatcher.

The quota state affects **dispatch admission**, not route selection.

## AVAILABLE

```text
normal dispatch
```

subject to `max_workers`.

## PRESSURE

Recommended behavior:

```text
allow currently running atomic Work Units to reach a safe boundary
do not start large new Work Units
checkpoint task graph
```

Small, explicitly safe units MAY still be dispatched only if policy permits.

Default should be conservative:

```text
suppress new dispatch under PRESSURE
```

## EXHAUSTED

```text
no new Work Units
running units stop at safe milestone if execution is still possible
persist state
mark remaining ready units as waiting_quota
schedule reset-aware resume
```

## UNKNOWN

Quota monitoring failure MUST NOT corrupt the graph.

Use the existing periodic liveness fallback and conservative dispatch policy.

---

# 68. Resume semantics for task graphs

On resume:

```text
load TASK_ID
    ↓
read state.json
    ↓
inspect repository
    ↓
reconcile Work Unit states
    ↓
recompute dependencies
    ↓
derive ready queue
    ↓
resume bounded dispatch
```

Never blindly replay all prior Work Units.

Rules:

- `completed` Work Units are not rerun unless repository evidence shows their result disappeared or became invalid.
- `waiting_dependency` units are reevaluated.
- `waiting_quota` units become eligible only after quota policy permits.
- units recorded as `running` at interruption require reconciliation before they are considered complete or ready again.
- repository state remains authoritative over runtime claims.

---

# 69. Interrupted running Work Units

A task may be interrupted while a Work Unit is marked `running`.

On resume, do NOT assume either:

```text
the unit completed
```

or:

```text
the unit must restart
```

Instead:

1. inspect the owned files and current diff;
2. inspect the last journal events;
3. inspect available implementation report;
4. determine whether required unit verification exists and is fresh;
5. transition to one of:

```text
verifying
ready
blocked
completed
```

This avoids duplicate edits after long-horizon resume.

---

# 70. Work Unit verification

Worker verification is still a claim.

For every Work Unit:

```text
worker implementation
      ↓
IMPLEMENTATION REPORT
      ↓
parent inspects owned diff
      ↓
parent runs unit verification
      ↓
Work Unit → completed
```

Unit completion should store an evidence fingerprint where practical.

A completed Work Unit whose relevant files later change may become stale and require re-verification depending on the changed dependency surface.

---

# 71. Join semantics

Parallel workers do not collectively declare the parent task complete.

The parent performs an explicit join:

```text
all required Work Units completed
       ↓
inspect aggregate diff
       ↓
run parent/global verification
       ↓
compute final fingerprint
       ↓
assurance review if required
       ↓
Completion Gate
```

This preserves the existing GLM Conductor rule:

> worker reports are claims; parent-observed evidence determines acceptance.

---

# 72. Global verification after join

Even when every Work Unit passes local verification, the parent MUST run task-level verification when cross-unit interaction could introduce regressions.

Example:

```text
Unit A → package A tests pass
Unit B → package B tests pass
      ↓
JOIN
      ↓
integration test / build / lint
```

Local unit verification never automatically substitutes for final integration verification.

---

# 73. Failure and retry policy

Retries must be bounded.

Recommended rules:

- deterministic transient tool failure → one controlled retry may be allowed;
- specification error → return to parent for contract correction;
- hidden coupling / ownership expansion → `ROUTE REASSESSMENT`;
- repeated implementation failure → `blocked` or `failed`;
- do not create infinite worker replacement loops.

Record attempts:

```json
{
  "attempt": 1
}
```

A new worker invocation does not erase previous failure history.

---

# 74. Task graph mutation

The parent may change the Work Unit graph when new evidence appears.

Examples:

```text
split one oversized unit
merge two tightly coupled units
add a discovered dependency
cancel obsolete unit
```

Graph mutation must:

1. be performed only by the main orchestrator;
2. preserve completed evidence where still valid;
3. record the change in `events.jsonl`;
4. re-check ownership and leases;
5. trigger `ROUTE REASSESSMENT` when the new evidence changes Delegability or Assurance.

Workers do not redesign the graph themselves.

---

# 75. Task Manager boundaries

GLM Conductor v2 intentionally supports only a minimal execution graph.

Supported concepts:

```text
Work Unit
depends_on
ready
bounded dispatch
lease
join
```

Explicitly out of scope:

```text
arbitrary conditional expressions
matrix builds
user-authored workflow DSL
cron workflow definitions
dynamic scripting language
agent-to-agent messaging bus
general-purpose distributed scheduler
```

This boundary prevents GLM Conductor from becoming a replacement for Temporal, GitHub Actions, or a generic workflow engine.

---

# 76. Task Manager + Continuity integration

The combined long-horizon model becomes:

```text
                 Parent Task
                      │
              Work Unit Graph
                      │
             derive ready queue
                      │
             Quota Admission
                      │
               File Leases
                      │
             bounded dispatch
              /      |      \
          Flash A Flash B Flash C
              \      |      /
                      │
                unit verify
                      │
                     join
                      │
             global verification
                      │
              assurance review
                      │
              Completion Gate
```

If quota pressure/exhaustion interrupts execution:

```text
persist graph
persist completed units
persist dependency state
persist safe running-unit state
schedule resume
      ↓
reload exact TASK_ID
      ↓
reconcile repository
      ↓
continue only unfinished ready units
```

This is the target model for **long-horizon multi-agent task continuity**.

---

# 77. Task & Work-Unit acceptance criteria

- [ ] Every dispatched Work Unit has a stable `WORK_UNIT_ID`.
- [ ] Work Units declare objective, dependencies, ownership, executor, and verification.
- [ ] Dependency cycles are rejected.
- [ ] Only dependency-satisfied units become ready.
- [ ] Dispatcher respects `max_workers`.
- [ ] New dispatch is suppressed under quota exhaustion.
- [ ] Quota pressure produces safe checkpoint/dispatch behavior.
- [ ] Completed Work Units are not blindly replayed after resume.
- [ ] Interrupted `running` units are reconciled against repository state.
- [ ] Workers cannot create or dispatch child Work Units.
- [ ] Unit completion requires parent-observed verification.
- [ ] Parent task completion requires explicit join and global verification.
- [ ] High-assurance review occurs after the aggregate change set is ready.
- [ ] Task graph mutation is controlled by the main orchestrator.
- [ ] No generic workflow DSL is introduced.

---

# 78. P2 — File Lease

Only implement after Ownership Gate works reliably.

Lease state example:

```json
{
  "src/a.ts": "agent-A",
  "src/b.ts": "agent-B"
}
```

Before write:

```text
target file leased?
       │
 no    │ yes
 │     ▼
allow  same owner?
         │
      yes/no
       │   │
     allow deny
```

---

# 79. Why File Lease

This is the prerequisite for safe parallel delegation.

Without it, multiple Flash agents can edit overlapping files.

Leases are acquired by the Task Manager before a Work Unit is dispatched and released only after the unit leaves its active write phase.

Do not add parallel execution before Work-Unit state, ownership enforcement, and lease protection exist.

---

# 80. P2 — Bounded Parallel Delegation

After Task & Work-Unit Management and leases are stable:

```text
              MAIN / TASK MANAGER
                       │
                  ready queue
                       │
               bounded dispatcher
               ┌───────┼───────┐
               ▼       ▼       ▼
            Flash A Flash B Flash C
              WU-A    WU-B    WU-C
               │       │       │
               └───────┼───────┘
                       ▼
                     join
                       ▼
             parent/global verification
```

---

# 81. Parallel eligibility

Parallelize only if:

- subtasks are independently specifiable;
- ownership is disjoint;
- interfaces are already fixed;
- verification can be separated;
- no ordering dependency exists.

---

# 82. Parallelism bound

Recommended initial maximum:

```text
2–4 foreground implementation subagents
```

Do not implement unbounded fan-out.

---

# 83. Parallelism is not a new route

Do NOT add:

```text
parallel
```

as a fifth route.

Instead:

```yaml
route:
  mode: delegate

execution:
  strategy: parallel
  max_workers: 3
```

or equivalent internal state.

---

# 84. Features explicitly NOT recommended for v2

## Generic persistent memory

Do not build:

- vector database;
- semantic memory service;
- user-profile memory system.

Integrate with existing memory capabilities if available.

---

## Workflow DSL

Do not add:

```javascript
parallel(...)
pipeline(...)
agent(...)
```

or a YAML workflow language in v2.

The project does not yet need a general workflow runtime.

---

## Agent Teams

Do not simulate autonomous agent-to-agent social communication.

Keep the star topology:

```text
          MAIN
      ┌────┼────┐
      ▼    ▼    ▼
      A    B    C
```

The main session remains the only orchestrator.

---

## Learned routing

Do not train or learn a route model in v2.

Keep Delegability × Assurance explainable and inspectable.

---

# 85. Proposed v2 repository structure

Recommended additions:

```text
plugins/glm-conductor/
├── .zcode-plugin/
│   └── plugin.json
├── agents/
│   ├── flash-implementer.md
│   ├── visual-implementer.md
│   ├── glm-reviewer.md
│   └── visual-reviewer.md
├── hooks/
│   ├── pre_tool_use.py
│   └── stop_gate.py
├── runtime/
│   ├── state.py
│   ├── fingerprint.py
│   ├── policy.py
│   ├── journal.py
│   ├── task_manager.py
│   ├── work_unit.py
│   ├── dependency.py
│   ├── dispatcher.py
│   ├── lease.py
│   └── quota/
│       ├── provider.py
│       ├── zai.py
│       ├── bigmodel.py
│       ├── parser.py
│       └── scheduler.py
└── skills/
    ├── orchestration/
    ├── continuity/
    └── enforcement/
        ├── SKILL.md
        └── references/
            ├── ownership.md
            ├── verification.md
            └── permissions.md
```

Exact ZCode Hook file placement should follow the current official plugin schema.

Do not invent unsupported manifest keys.

---

# 86. New `enforcement` skill

Purpose:

> Explain deterministic runtime contracts to the main model and users.

It should document:

- what is enforced;
- which hooks enforce it;
- fail-closed behavior;
- state paths;
- how to recover when a gate blocks.

It should NOT itself implement enforcement logic.

---

# 87. Hook behavior philosophy

Hooks should be:

- deterministic;
- fast;
- local;
- side-effect-minimal;
- explainable;
- fail-closed only for high-confidence invariants.

Avoid:

- web calls;
- model calls;
- heavy repository scans on every hook;
- ambiguous semantic interpretation.

---

# 88. Hook failure handling

If enforcement code itself fails:

For safety-critical gates:

```text
deny / block
```

with a clear message.

For non-critical logging:

```text
allow
```

and report degraded enforcement.

Classify each hook path explicitly.

---

# 89. v2 route declaration extension

Keep the existing route block.

Optionally add:

```text
SELECTIVE ROUTE
mode: full
delegability: high
assurance: high
executor: flash-implementer
continuity: foreground
enforcement: active
reason: ...
```

Do not expose too many internal fields to the user.

---

# 90. New verification status vocabulary

Recommended runtime states:

```text
verification:
  missing
  valid
  stale
  failed

review:
  not-required
  missing
  ship
  fix-first
  rethink
  stale
```

This will make completion logic easier.

---

# 91. Task lifecycle

Parent task lifecycle:

```text
created
   ↓
preflight
   ↓
routed
   ↓
decomposed
   ↓
executing
   ↓
joining
   ↓
verifying
   ↓
reviewing
   ↓
completed
```

Additional task states:

```text
waiting_quota
blocked
cancelled
failed
```

Work Units maintain their own smaller lifecycle:

```text
pending
→ waiting_dependency
→ ready
→ running
→ verifying
→ completed
```

Do not over-engineer more phases.

---

# 92. Example v2 full task

```text
USER GOAL
   ↓
GLM-5.3
   ↓
ROUTING PREFLIGHT
   ↓
SELECTIVE ROUTE
mode: full
   ↓
create TASK_ID
   ↓
write state.json
   ↓
ownership gate active
   ↓
Flash implementation
   ↓
parent diff inspection
   ↓
parent verification
   ↓
store verification fingerprint
   ↓
fresh reviewer
   ↓
store ship verdict + fingerprint
   ↓
Stop hook
   ↓
fingerprints current?
verification valid?
review valid?
   ↓
YES
   ↓
complete
```

---

# 93. Example stale review flow

```text
review = ship
fingerprint = abc123
      ↓
later edit
      ↓
current fingerprint = def456
      ↓
review → stale
verification → stale
      ↓
completion blocked
      ↓
reverify
      ↓
fresh review
```

This behavior should be automatic.

---

# 94. Example ownership violation

```text
Flash owns:
src/auth/**
tests/auth/**

Flash calls:
Edit src/payments/api.ts
      ↓
PreToolUse
      ↓
not owned
      ↓
DENY

Message:
Write blocked by GLM Conductor ownership policy.
Target: src/payments/api.ts
Owned scope:
- src/auth/**
- tests/auth/**
Return to the parent for ROUTE REASSESSMENT if scope is incomplete.
```

---

# 95. Example Completion Gate

```text
Task assurance: high

verification:
  pytest: valid
  lint: valid

review:
  missing
```

Agent attempts to finish.

Stop hook:

```text
BLOCK

Completion blocked:
independent review is required for assurance: high.

Required reviewer:
glm-reviewer
```

---

# 96. Implementation phases

## Phase 1 — Runtime state foundation

Implement:

- TASK_ID
- state.json
- journal
- parent task lifecycle

Acceptance:
state can be created/read/updated reliably.

---

## Phase 2 — Ownership enforcement（Phase 0 修订版：Layer A + B）

Implement:

- ownership matching module (exact file / directory prefix / glob)
- Stop-gate ownership verification: touched ⊆ owned, else block
  with out-of-scope paths (Layer A)
- PostToolUse(Agent|Task) ownership audit (optional early variant
  of Layer A at dispatch completion)
- PreToolUse(Agent|Task) dispatch-time ownership injection via
  additionalContext / updatedInput (Layer B)
- main-session Bash write-pattern policy (§12 scope)
- reviewer read-off reinforcement stays on the agent tools
  whitelist (§10 amendment)

Acceptance:
a task whose diff touches files outside the declared ownership
cannot pass the completion gate, with the exact out-of-scope paths
reported in the block message.

---

## Phase 3 — Evidence integrity

Implement:

- fingerprint
- verification state
- stale detection
- review fingerprint

Acceptance:
post-review code edits automatically invalidate old evidence.

---

## Phase 4 — Completion enforcement

Implement:

- Stop hook
- verification gate
- review gate
- stale evidence gate

Acceptance:
task cannot finish while required evidence is missing/stale.

---

## Phase 5 — Quota-Aware Continuity

Implement:

- QuotaProvider abstraction
- legacy/V3 parser fixtures
- quota state
- pressure checkpointing
- reset-aware ResumePlanner
- periodic liveness fallback

Acceptance:
resumable tasks use exact reset-aware scheduling when available and degrade safely when quota data is unavailable.

---

## Phase 6 — Context-aware routing

Implement:

- Explore preflight
- Task Context Pack

Acceptance:
unknown-root-cause tasks gather evidence before route selection.

---

## Phase 7 — Permission policy

Implement:

- route-aware allow / ask / deny
- destructive command controls

Acceptance:
dangerous actions respect role and assurance policy.

---

## Phase 8 — Task & Work-Unit Management

Implement:

- WORK_UNIT_ID
- Work Unit schema
- dependency validation
- status transitions
- ready queue derivation
- bounded dispatcher state
- join semantics
- resume reconciliation

Acceptance:
a multi-unit task can be decomposed, resumed, joined, and globally verified without requiring parallel execution.

---

## Phase 9 — Parallel preparation

Implement:

- file leases
- collision denial
- lease lifecycle

Acceptance:
two candidate Work Units cannot obtain conflicting write ownership.

---

## Phase 10 — Bounded Parallel Delegation

Only after Phases 8–9 are stable:

- enable 2–4 concurrent ready Work Units;
- enforce max_workers;
- suppress dispatch under quota pressure/exhaustion;
- join results under parent control.

Acceptance:
parallel execution is observably equivalent to safe bounded execution and cannot bypass parent verification.

Parallel delegation MAY remain experimental for the first v2 stable release.

---

# 97. Static validator upgrades

Extend the existing validator.

Check:

- hook files referenced by plugin config exist;
- runtime state schema examples remain consistent;
- reviewer is still read-only;
- no fifth route exists;
- ownership gate docs exist;
- Stop gate docs exist;
- fingerprint fields exist;
- stale-review semantics are documented;
- Task/Work-Unit schema fields are documented;
- Work Unit dependency semantics exist;
- bounded dispatch requires Task Manager + lease documentation;
- parallel delegation cannot be enabled without lease protection;
- quota-aware dispatch suppression is documented.

Do not validate runtime behavior only through static text.

Add smoke tests where possible.

---

# 98. v2 test strategy

## Unit-level

Test:

- path ownership matching;
- fingerprint stability;
- stale evidence detection;
- policy decisions;
- parent-task state transitions;
- Work Unit state transitions;
- dependency-cycle rejection;
- ready-queue derivation;
- quota-aware dispatch admission;
- join eligibility.

## Integration-level

Test:

1. unowned change present at Stop → completion blocked with out-of-scope paths (Layer A);
2. owned-only change present at Stop → gate proceeds;
3. verification missing → completion blocked;
4. review missing for high assurance → blocked;
5. post-review edit → review stale;
6. reverify + fresh review → completion allowed;
7. dependency-blocked Work Unit → not dispatched;
8. quota `PRESSURE` / `EXHAUSTED` → new dispatch suppressed;
9. resume with completed units → completed units are skipped;
10. interrupted `running` Work Unit → repository reconciliation occurs;
11. parallel file conflict → denied once lease exists;
12. all Work Units complete but global verification missing → parent task remains incomplete;
13. successful join + global verification + fresh review → completion allowed.

---

# 99. Runtime smoke scenarios

Create a small fixture repository.

Scenarios:

```text
simple delegate
high-assurance audit
full route
visual route
resumable task
ownership violation
stale review
multi-work-unit serial task
dependency graph
quota-paused task graph
resume after quota reset
parallel join
parallel collision
```

Do not benchmark model intelligence.

These are correctness smoke tests.

---

# 100. Metrics to avoid

Do not add telemetry such as:

- token tracking;
- model cost dashboards;
- success prediction;
- benchmark score collection.

v2 is about enforcement correctness, not optimization analytics.

---

# 101. Suggested v2 milestones

## v2.0-alpha1 — Enforcement foundation

- task state
- ownership gate
- basic journal

## v2.0-alpha2 — Evidence integrity

- fingerprints
- verification freshness
- review freshness
- Stop completion gate

## v2.0-alpha3 — Long-horizon continuity

- QuotaProvider
- Coding Plan V2/V3 normalization
- pressure checkpointing
- reset-aware resume
- periodic fallback

## v2.0-beta1 — Context and permissions

- Explore preflight
- Task Context Pack
- route-aware permissions

## v2.0-beta2 — Task Manager

- Work Unit model
- dependency graph
- ready queue
- bounded dispatcher state
- join / global verification
- task-graph resume

## v2.0-rc1 — Parallel safety

- file lease
- collision tests
- optional bounded parallel execution

## v2.0 stable

Required:

- enforcement hardening
- quota-aware continuity hardening
- Task Manager stable
- docs
- validators
- smoke tests

Bounded parallel delegation may remain marked experimental if runtime evidence is not yet sufficient.

---

# 102. Recommended v2 tagline

Current:

```text
Selective orchestration for GLM coding agents in ZCode.
```

Keep this.

Optional expanded description:

> Selective GLM orchestration with deterministic runtime enforcement for ZCode.

Do not rename the project solely because enforcement is added.

`glm-conductor` remains appropriate.

---

# 103. Definition of Done for v2

v2 should be considered successful when:

- routing remains explainable;
- a diff outside declared ownership cannot silently pass the completion gate (Layer A); dispatch-time ownership injection is active (Layer B);
- reviewer write attempts surface as out-of-scope changes and block completion (tools whitelist is the primary read-only enforcement);
- missing verification prevents completion;
- stale verification is automatically detected;
- stale review is automatically detected;
- high-assurance tasks cannot complete without a fresh valid review;
- context-poor tasks can run a read-only routing preflight;
- task state is recoverable and auditable;
- resumable continuity can use quota-aware exact scheduling when quota data is available;
- quota-monitor incompatibility safely degrades to periodic liveness probing;
- five-hour and weekly blocking windows are both respected;
- quota credentials are never persisted;
- parent tasks can be decomposed into explicit Work Units;
- Work Unit dependencies deterministically control readiness;
- completed Work Units are not blindly replayed after resume;
- quota pressure/exhaustion suppresses unsafe new dispatch;
- parent/global verification occurs after Work Unit join;
- parallel execution, if enabled, is bounded and lease-protected;
- no generic workflow engine has been introduced.

---

# 104. Direct coding-agent instruction

```text
Implement GLM Conductor v2 according to this specification.

The core v1 architecture is frozen:
- Delegability × Assurance
- solo / delegate / audit / full
- GLM-5.3 architect
- GLM-5.3-Flash implementation
- glm-reviewer / visual-reviewer
- foreground / resumable / idle

v2 is an enforcement upgrade, not a routing redesign.

Primary objective:
move critical workflow invariants from prompt-only contracts to
deterministic ZCode runtime enforcement.

Implement in this order:

1. task-scoped runtime state with TASK_ID
2. ownership state
3. ownership enforcement Layer A (Stop-gate verification) + Layer B (dispatch-time injection)
4. worktree/evidence fingerprinting
5. verification freshness
6. review freshness
7. Stop completion gate
8. execution journal
9. Quota-Aware Continuity provider/parser foundation
10. reset-aware resumable scheduler with periodic fallback
11. routing preflight
12. Task Context Pack
13. route-aware permission policy
14. Work Unit model and dependency graph
15. ready queue and bounded dispatcher state
16. join / parent-global verification semantics
17. file leases
18. bounded parallel delegation

Do not implement bounded parallel delegation until Work-Unit management,
ownership enforcement, and lease mechanisms are proven stable.

Before editing:
- inspect the current ZCode plugin/hook documentation;
- verify exact Hook configuration syntax;
- verify Hook input/output schemas;
- inspect all existing GLM Conductor v1.1 contracts;
- preserve current route semantics.

Runtime rules:

- Hooks must not call models.
- Hooks must not depend on web access.
- Hooks should be fast and deterministic.
- Repository state remains authoritative for source code.
- state.json is authoritative only for orchestration runtime state.
- verification evidence must come from parent-observed execution.
- worker reports are not final evidence.
- a reviewer verdict is valid only for the fingerprint it reviewed.
- any relevant code mutation invalidates verification/review freshness.
- completion must be blocked when required evidence is missing or stale.
- completion must be blocked while the diff contains changes outside declared ownership (Layer A).
- quota affects scheduling/continuity only, never route selection.
- quota API availability is optional.
- quota-monitor failure must fall back to periodic liveness scheduling.
- never fabricate quota reset times.
- never persist Coding Plan credentials in state, checkpoints, journal, or logs.
- only the main GLM-5.3 session may mutate the Work Unit graph.
- subagents must never dispatch child subagents.
- a Work Unit may run only when its dependencies and dispatch policies are satisfied.
- quota pressure/exhaustion may suppress new Work Unit dispatch but must not change route semantics.
- parent task completion requires join plus parent/global verification.

Explicitly do NOT add:
- a fifth route
- workflow DSL
- generic long-term memory
- agent teams
- learned routing
- cost-aware routing
- benchmarking infrastructure
- external orchestration daemon

Testing requirements:

- add unit tests for ownership, fingerprint, freshness, policy, and state;
- add integration smoke tests for ownership denial and completion gating;
- test post-review modification invalidates the review;
- test reviewer write attempts are blocked by the agent tools whitelist (subagent contexts carry no hook runner — hook-level denial is not implementable until Layer C);
- test high-assurance completion without review is blocked;
- test legacy TOKENS_LIMIT and V3 CREDIT_LIMIT quota fixtures;
- test simultaneous 5-hour/weekly exhaustion schedules the later reset;
- test missing reset time falls back to periodic reactivation;
- test quota credentials never appear in persisted runtime state;
- test dependency cycles are rejected;
- test blocked dependencies prevent dispatch;
- test quota pressure/exhaustion suppresses new dispatch;
- test completed Work Units are skipped on resume;
- test interrupted running Work Units are reconciled against repository state;
- test file leases prevent concurrent ownership collision;
- test parent task cannot complete before join/global verification.

After implementation:
- inspect the complete diff;
- run all validators and tests;
- document exact Hook behavior;
- report all modified/new files;
- report all test commands and actual results;
- explicitly list any ZCode runtime assumptions that remain unverified.

Do not report v2 complete until enforcement behavior is demonstrated by
actual smoke tests.
```

---

## v2 execution architecture synthesis

The intended runtime relationship is:

```text
                    GLM-5.3 MAIN
                         │
                 Judgment / Routing
                         │
                 Parent Task Manager
                         │
                 Work Unit Graph
                         │
        ┌────────────────┼────────────────┐
        │                │                │
   Dependency Gate   Quota Gate      Lease / Policy
        │                │                │
        └────────────────┼────────────────┘
                         │
                    Ready Queue
                         │
                 Bounded Dispatcher
                  /       |       \
             Flash A   Flash B   Visual
                  \       |       /
                         Join
                          │
                  Parent Verification
                          │
                 Assurance Review
                          │
                   Completion Gate
                          │
                 Continuity / Resume
```

This keeps the system flat:

```text
main orchestrates
workers execute
reviewers review
hooks enforce
Task Manager schedules
Continuity resumes
```

There is still no agent-to-agent orchestration layer.

---

# 105. Final architectural direction

v1.x established:

```text
Good orchestration judgment
```

v2 should establish:

```text
Good orchestration judgment
        +
Deterministic runtime enforcement
```

The intended result is:

> GLM Conductor evolves from a well-designed orchestration plugin into a lightweight ZCode GLM agent harness with enforceable execution contracts.

That is the most valuable next step for the project.
