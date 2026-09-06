# GLM Conductor v2.2.1 Hardening & Repository Release Cleanup Implementation Plan

> **Target branch baseline:** `v2-dev` at `173fed410989fc2880571af77c018bc4e30dcc51`  
> **Released stable baseline:** `main` at merge commit `b4f4d3fb6482370d520f1d1abcd52a718fc0dc01` (`v2.2.0`)  
> **Target release:** `v2.2.1`  
> **Nature of release:** maintenance / hardening / repository cleanup, **no feature expansion**  
> **Primary goal:** preserve the v2.2 behavioral contract while reducing cross-process correctness risk, repository/documentation noise, and future maintenance cost.

---

## 1. Executive Summary

GLM Conductor v2.2.0 has completed the transition from a prompt/skill-oriented orchestration helper into a deterministic runtime policy and continuity layer for ZCode GLM coding agents. The v2.2 release has a credible functional closure: selective routing, runtime enforcement, durable task state, evidence-bound completion, quota epoch/subscription/consumption, persistent recurring bridge, watcher, primer authorization, crash-safe accounting, and continuity-aware Stop gating are all implemented in runtime code and covered by CI.

The next release should **not** continue adding transport types, controller modes, or new orchestration features. The most valuable work is now engineering hardening and release-surface cleanup.

v2.2.1 therefore has two equal-priority tracks:

1. **Runtime Hardening**
   - close cross-process race windows introduced by the resident quota watcher;
   - centralize durable JSON/file I/O primitives;
   - bind quota state/accounting more explicitly to the active provider identity;
   - add multiprocessing/integration regressions instead of increasing contract-marker test count;
   - start behavior-preserving decomposition of oversized runtime modules.

2. **Repository Release Cleanup**
   - make `main` the unambiguous stable truth source;
   - simplify `README.md` / `README.en.md` into release-quality entry documents;
   - define the project through three stable core pillars rather than development chronology;
   - move implementation plans, phase experiments, dogfood records, correction plans, and stable-gate checklists out of the top-level documentation surface;
   - keep historical evidence available, but under an explicit history/archive area;
   - reduce duplicated descriptions across README, architecture, skills, CHANGELOG, and implementation-plan documents;
   - normalize GitHub repository metadata and release presentation.

The release should be considered successful when a new user can understand the project from the README and current docs **without reading development-process documents**, while the runtime preserves all existing v2.2 public behavior and gains demonstrably safer cross-process persistence.

---

# 2. Current Baseline and Release Interpretation

## 2.1 Branch status

The v2.2.0 implementation was merged from `v2-dev` into `main` through PR #2. The current `v2-dev` branch contains one additional post-release documentation-only closure commit (`173fed4`) after the merge point.

Therefore the working interpretation for v2.2.1 is:

- `main` = released v2.2.0 stable implementation truth;
- `v2-dev@173fed4` = v2.2.0 implementation plus post-release stable-gate record;
- the extra `v2-dev` commit contains no new runtime behavior;
- v2.2.1 must start by reconciling this post-release documentation change into the stable line and then creating a clean maintenance branch.

### Required branch cleanup

Preferred sequence:

1. Preserve/merge the post-release stable-gate documentation change into `main`.
2. Create a dedicated maintenance branch from the reconciled `main`, recommended:
   - `v2.2.1-hardening`, or
   - `release/v2.2.1`.
3. Stop using the old `v2-dev` branch as the active development truth after v2.2.1 kickoff.
4. After v2.2.1 release, future feature work should start from clean `main` as `v2.3-dev`.

Do **not** force-push or rewrite published release history.

---

# 3. Product Positioning to Freeze in v2.2.1

The repository should no longer present GLM Conductor primarily as “GLM-5.3 calls GLM-5.3-Flash”.

That remains an implementation strategy, but it is not the project’s strongest differentiator.

The stable project definition should converge on three pillars:

## 3.1 Pillar 1 — Selective Orchestration

GLM-5.3 remains the reasoning/controller layer. Bounded implementation can be delegated to GLM-5.3-Flash, while high-assurance work can request independent review.

Core concepts:

- Delegability × Assurance routing;
- `solo / delegate / audit / full`;
- bounded Work Units;
- explicit dispatch decisions;
- limited worker concurrency;
- no delegation merely for the sake of multi-agent appearance.

## 3.2 Pillar 2 — Mechanical Execution Assurance

Critical contracts are enforced by runtime state and hooks rather than only by prompts.

Core concepts:

- durable task state;
- work-unit lifecycle;
- ownership scope;
- lease / permit / wave dispatch transactions;
- runtime-observed implementation lifecycle;
- verification receipts;
- review receipts;
- git/worktree fingerprint freshness;
- four-fold completion gate.

## 3.3 Pillar 3 — Durable Quota-Aware Continuity

Long-running Coding Plan work can pause and recover across quota windows without treating scheduler fires as quota truth.

Core concepts:

- provider quota state × execution phase;
- quota epoch;
- quota subscription;
- persistent recurring bridge;
- optional resident quota watcher;
- fail-closed primer authorization;
- exactly-once activation/consumption accounting;
- durable checkpoint/manifest recovery;
- SessionStart fallback.

### Stable product statement

Recommended English form:

> **Deterministic orchestration, execution assurance, and quota-aware continuity for GLM coding agents in ZCode.**

Recommended Chinese form:

> **面向 ZCode GLM Coding Agent 的确定性编排、执行保障与额度感知长任务连续性层。**

These statements should become the primary description in README, GitHub repository metadata, and release-facing documentation.

---

# 4. Scope

## 4.1 In scope

- cross-process durable I/O hardening;
- watcher single-instance acquisition hardening;
- quota cache/provider-identity binding;
- compatibility-safe quota ledger identity improvements;
- multiprocessing and crash-window integration tests;
- internal runtime decomposition with stable public API;
- README CN/EN rewrite;
- repository description/topics cleanup;
- docs information architecture cleanup;
- history/archive organization;
- CHANGELOG release-surface cleanup;
- architecture truth-source cleanup;
- branch/release hygiene;
- optional branch-protection configuration proposal.

## 4.2 Explicitly out of scope

Do **not** implement the following in v2.2.1:

- `probe_then_hold`;
- `self_retiming`;
- `session_injector`;
- multi-controller support;
- distributed locking across machines;
- arbitrary dynamic worker scaling beyond the existing hard limit;
- a second scheduler implementation;
- new model-routing dimensions;
- new provider quota semantics unless required for compatibility;
- state-schema redesign;
- public CLI redesign;
- new external dependencies merely for architectural elegance.

Reserved activation transports remain reserved.

---

# 5. Compatibility Rules

v2.2.1 is a maintenance release. The following contracts are frozen unless a demonstrable correctness bug requires a narrowly compatible extension.

## 5.1 Must remain compatible

- plugin name and plugin installation path;
- public CLI command names;
- documented exit-code semantics;
- existing task state files;
- existing task journals;
- v2.2 quota events;
- existing `epoch_id` format;
- existing `task_manager` public entry points;
- existing stable activation transport: `recurring_bridge`;
- existing default worker limit and concurrency semantics;
- primer default = off;
- single-controller supported model;
- SessionStart fallback semantics.

## 5.2 Preferred compatibility technique

For new identity or persistence metadata:

- add optional fields;
- readers accept old and new records;
- writers emit the new stable form;
- do not rewrite old journals in place;
- migrations must be idempotent;
- missing new metadata must degrade conservatively, not crash legacy tasks.

---

# 6. Workstream A — Cross-Process Durable I/O Hardening

## WU-221-A1 — Create a shared durable I/O primitive layer

### Problem

v2.2 uses several independently implemented patterns:

- write JSON to `<path>.tmp`;
- `os.replace(tmp, path)`;
- bounded PermissionError retry;
- read-modify-write;
- append-only journal writes;
- process-local assumptions.

These are individually reasonable in a single-writer model, but v2.2 now has at least two legitimate processes:

- foreground/main ZCode session;
- resident quota watcher.

A fixed `.tmp` filename is not sufficient for multi-writer safety.

### Required implementation

Introduce a small internal module, recommended:

```text
plugins/glm-conductor/runtime/durable_io.py
```

or:

```text
plugins/glm-conductor/runtime/durable/
    __init__.py
    io.py
    lock.py
```

Keep it stdlib-only.

Provide narrowly scoped primitives such as:

```python
atomic_write_json(path, payload, ...)
atomic_read_json(path, ...)
atomic_update_json(path, updater, ...)
exclusive_lock(lock_path, ...)
```

Exact API names may differ, but the responsibilities must be centralized.

### Required properties

`atomic_write_json` must:

- create the temp file in the same directory as the target;
- use a **unique** temp filename, e.g. PID + random suffix or `tempfile.NamedTemporaryFile(delete=False, dir=...)`;
- flush and close before replace;
- preferably `fsync()` the file before replace where practical;
- use `os.replace`;
- clean abandoned temp files best-effort;
- preserve current Windows PermissionError bounded-retry behavior;
- never use one fixed `<target>.tmp` path for all writers.

### Migration targets

At minimum inspect and migrate shared durability code in:

- quota resolver/cache;
- watcher store;
- primer store;
- scheduler facts;
- any other runtime file that can be written from more than one process.

Do **not** mechanically migrate append-only journal files unless the new primitive is demonstrably appropriate; append semantics are a different durability problem.

### Acceptance

- no shared JSON writer uses a globally fixed `.tmp` filename;
- existing unit tests remain green;
- new concurrent writers cannot corrupt the final JSON file;
- temp files are same-directory and unique;
- Windows and Linux tests both pass.

---

## WU-221-A2 — Make watcher acquisition truly single-instance

### Problem

The current watcher acquisition path is conceptually:

```text
read current watcher state
→ inspect pid/heartbeat
→ decide whether acquisition is allowed
→ overwrite watcher state
```

Two simultaneous starters can both read “no live watcher” before either writes.

This means “single active watcher” is not a mechanically atomic guarantee.

### Required implementation

Separate **lock ownership** from **watcher status**.

Recommended shape:

```text
.glm-conductor/quota/
    watcher.lock
    watcher.json
    watcher.log
```

`watcher.lock` should be acquired atomically using an OS/filesystem primitive suitable for one-machine single-controller operation.

Preferred stdlib-compatible approach:

- atomic create with `O_CREAT | O_EXCL`;
- lock file stores PID, generation, provider identity, created_at;
- stale lock recovery validates PID/heartbeat before takeover;
- takeover must itself be race-safe.

Alternative platform-specific file locks are acceptable if implemented carefully and kept dependency-free.

### Requirements

- simultaneous start attempts result in exactly one acquired watcher;
- the loser gets a deterministic conflict result;
- stale/dead lock can still be recovered;
- normal graceful stop remains supported;
- `watcher.json` remains observation/status state, not the sole mutual-exclusion primitive;
- no infinite wait;
- no daemon-wide global lock outside the repository;
- one repository/provider identity remains the intended scope.

### New tests

Add real multiprocessing tests:

```text
start N=2..4 watcher-acquire processes simultaneously
→ exactly one acquired=True
→ all others conflict
→ no corrupt watcher.json
→ no duplicate active generation
```

Also test:

- stale PID recovery;
- stale heartbeat recovery;
- process dies immediately after lock acquisition;
- stop/start race;
- Windows-safe behavior.

---

## WU-221-A3 — Fix read-modify-write lost-update paths

Audit files that are updated by:

```text
read JSON
modify in memory
write full JSON
```

Examples to inspect:

- watcher stop flag;
- watcher heartbeat;
- scheduler session facts;
- any quota state shared across foreground/watcher processes.

### Required ruling

For each shared file, explicitly classify:

1. **single writer** — no lock required;
2. **multi writer, replace-only** — unique temp is enough;
3. **multi writer, read-modify-write** — requires exclusive update lock/CAS-equivalent;
4. **append-only ledger** — use append discipline, not replace semantics.

Document this classification in code comments or a compact architecture table.

### Acceptance

Add at least one regression proving a concurrent stop request cannot be silently lost by a heartbeat writeback.

---

# 7. Workstream B — Provider / Quota Identity Hardening

## WU-221-B1 — Bind quota cache to provider identity

### Problem

The quota cache currently records provider/status/snapshot/fetched_at but does not strongly prove that a fresh cache entry belongs to the currently active credential identity.

A credential/account switch can therefore temporarily reuse a previous account’s quota state.

### Required design

Introduce a non-secret:

```text
provider_identity_hash
```

using the same conceptual identity already used by watcher/primer.

Do not persist API keys.

Recommended identity material:

```text
provider family/source + credential fingerprint
```

The hash must be deterministic for one credential identity and must not expose the credential.

### Cache behavior

A cache entry is “fresh and reusable” only when:

```text
cache.provider_identity_hash == current_provider_identity_hash
```

If identity differs:

- do not treat the old cache as fresh;
- do not use it as authoritative stale fallback for the new identity;
- fetch or return UNKNOWN according to existing fail-open rules;
- retain backwards compatibility for old cache files by treating missing identity conservatively.

### Security

Never log:

- raw API key;
- Authorization header;
- complete credential source containing secrets;
- response body on provider failure.

### Tests

- account A writes cache → account B resolves → A cache not reused;
- missing identity in legacy cache → conservative behavior;
- same identity → fresh cache fast path unchanged;
- provider fetch failure after identity switch → does not fall back to foreign identity cache.

---

## WU-221-B2 — Introduce a composite quota identity concept without breaking `epoch_id`

Do **not** change the existing:

```text
epoch_id = glm:<16hex>
```

Instead define the higher-level conceptual identity:

```text
QuotaIdentity = (provider_identity_hash, epoch_id)
```

### Audit the following

- quota subscription;
- activation mark;
- window consumption;
- primer idempotency;
- watcher observation;
- recovery reconciliation.

### Goal

Ensure an epoch from account/provider A cannot accidentally authorize or consume budget for account/provider B.

### Compatibility

- existing v2.2 tasks with only `epoch_id` remain readable;
- new records may add `provider_identity_hash`;
- old records are never rewritten destructively;
- any migration is idempotent;
- no double consumption.

### Acceptance

Add cross-account regression tests for:

- subscription eligibility;
- same textual epoch_id under different provider identities;
- consumption accounting;
- recovery after account switch.

---

# 8. Workstream C — Runtime Decomposition Without Behavioral Redesign

## WU-221-C1 — Decompose `task_manager.py` behind a compatibility facade

### Problem

`task_manager.py` has become a very large module and now owns unrelated domains:

- dispatch transaction;
- work-unit lifecycle;
- wake bridge;
- quota subscription;
- quota resume;
- quota consumption;
- accounting migration;
- scheduler context;
- continuity bookkeeping.

This increases reasoning cost and regression risk.

### Rule

This is **not** a rewrite.

Do behavior-preserving extraction only.

### Recommended target

```text
runtime/
    task_manager.py              # stable public facade

    dispatch_service.py
    completion_service.py

    continuity/
        wake_bridge.py
        subscription.py
        resume.py

    quota/
        accounting.py
```

Exact names may be adapted to repository style.

### Requirements

- existing imports from `runtime.task_manager` continue to work;
- public functions keep signatures unless an internal-only function is being moved;
- no state-schema redesign;
- no semantics change bundled into extraction;
- extraction commits should be small and independently testable;
- each extracted domain gets a focused test module if not already available.

### Priority

Extract first:

1. quota accounting / consumption;
2. wake bridge / continuity;
3. subscription / resume.

Leave core task lifecycle and dispatch facade until later if moving them would create excessive churn.

---

## WU-221-C2 — Reduce sibling private imports in `runtime/quota`

Current quota modules reuse private helpers across siblings.

Do not perform a broad style rewrite.

Extract only helpers that are already shared by multiple quota modules and have stable semantics:

```text
quota/time_utils.py
quota/window_math.py
```

Candidates include:

- ISO UTC parse/format;
- reusable reset-window math;
- common boundary helpers.

### Acceptance

- no duplicated algorithms are introduced;
- circular imports do not increase;
- public behavior unchanged;
- tests remain green.

---

# 9. Workstream D — Test Strategy Shift

v2.2.1 should not optimize for a larger raw unit-test count.

The target is stronger **system assurance**.

## WU-221-D1 — Add a multiprocessing integration suite

Recommended module:

```text
tests/test_multiprocess_runtime.py
```

or a dedicated directory if needed.

Required scenarios:

1. simultaneous watcher acquisition;
2. foreground quota refresh + watcher quota refresh;
3. concurrent watcher heartbeat + stop request;
4. concurrent unique-temp JSON writers;
5. process killed between temp write and replace;
6. process killed after durable pending marker and before state projection;
7. credential switch with stale cache present.

Tests must:

- use real local processes where practical;
- use tempfile repositories;
- perform zero real provider/model calls;
- have bounded timeouts;
- avoid flaky sleeps as the primary synchronization mechanism;
- use barriers/events/pipes when deterministic interleaving is needed.

---

## WU-221-D2 — Preserve contract tests but stop expanding them casually

Existing exact-shape/marker/constant tests remain valuable.

New tests should prefer behavior-level assertions unless the shape is a documented compatibility contract.

Do not add validator checks merely because a line appears in a document.

Validator additions require one of:

- plugin load correctness;
- release/version consistency;
- security/safety invariant;
- externally consumed contract;
- known regression class.

---

## WU-221-D3 — Optional lightweight CI additions

Recommended, if they do not introduce maintenance burden:

- `ruff` for syntax/style/static defect detection;
- coverage report, optionally without a hard threshold on first adoption;
- CodeQL or GitHub-native security scanning;
- plugin-load/install smoke test.

Avoid introducing a heavy Python build toolchain.

---

# 10. Workstream E — Repository Release Cleanup

This track is mandatory for v2.2.1.

The final repository should look like a **released open-source project**, not an internal implementation diary.

---

## WU-221-E1 — Rewrite `README.md`

### Goal

README should answer, in order:

1. What is this?
2. Why does it exist?
3. What does it do that ZCode itself does not enforce?
4. What are the three core pillars?
5. How do I install/use it?
6. What are the stable boundaries?
7. Where do I read more?

A user should not need to understand M1/M5/C7/RH-06/ST-03 terminology.

### Recommended README structure

```markdown
# GLM Conductor

one-sentence positioning
badges

## Why GLM Conductor
2–4 short paragraphs

## Core Model
three pillars:
1. Selective Orchestration
2. Mechanical Execution Assurance
3. Durable Quota-Aware Continuity

## How It Works
small diagram

## Key Features
concise grouped bullets

## Installation
minimal steps

## Typical Usage
short examples / workflow

## Stable Boundaries
what it intentionally does not do

## Documentation
links to current docs

## Release Status
v2.2.1 stable

## License
MIT
```

### Must remove from main README

Remove or heavily compress:

- M-series / C-series implementation chronology;
- individual RH-* hardening history;
- Phase0 experiment details;
- exact dogfood run chronology;
- large enumerations of internal event names;
- old release evolution narrative;
- long descriptions of how each correction was discovered;
- implementation-unit names (`wu-*`);
- references primarily useful to maintainers during v2.2 construction.

### Must retain

Retain concise descriptions of:

- routing modes;
- completion gate;
- receipts/fingerprints;
- quota execution phases;
- epoch/subscription/recurring bridge;
- watcher/primer boundary;
- single-controller + worker cap;
- SessionStart fallback;
- stable transport = recurring_bridge;
- primer default off.

README should be release-facing, not evidence-facing.

---

## WU-221-E2 — Rewrite `README.en.md` for parity

English README must not be a mechanically stale translation.

Requirements:

- same section topology as Chinese README;
- same capability claims;
- same stable boundaries;
- same version;
- same links;
- no feature present in one language and absent in the other.

A lightweight parity test may verify headings/required markers, but avoid brittle sentence-level equality.

---

## WU-221-E3 — Create a concise “Core Concepts” document

Create:

```text
docs/core-concepts.md
```

Recommended content:

### 1. Selective Orchestration
Delegability × Assurance and the four routes.

### 2. Execution Assurance
state → ownership → verification/review receipts → fingerprint freshness → completion gate.

### 3. Quota-Aware Continuity
quota snapshot → epoch → phase → subscription → recurring bridge → resume → consumption.

### 4. Stable Boundaries
single controller, recurring bridge only, watcher no session injection, primer off by default, worker hard limit.

This becomes the stable conceptual document referenced by README.

Do not include development chronology.

---

## WU-221-E4 — Clean `docs/` information architecture

### Desired top-level docs

Recommended:

```text
docs/
    README.md
    architecture.md
    core-concepts.md

    guides/
        long-horizon.md            # only if useful outside Skill docs
        quota-continuity.md        # optional concise user/operator guide

    history/
        v2.2/
            README.md
            ...
```

Do not duplicate skill references unnecessarily. If the authoritative operator guide already lives under a Skill, link to it rather than cloning it.

### Move development-process records to history

The following current v2.2 documents are examples of files that should no longer occupy the main documentation surface:

- `GLM-Conductor-v2.2-Dogfood-Records.md`
- `GLM-Conductor-v2.2-Persistent-Wake-Bridge-Architecture-Correction.md`
- `GLM-Conductor-v2.2-Phase0-Primer-Experiments.md`
- `GLM-Conductor-v2.2-Phase0-Scheduled-Wake-Runtime-Verification.md`
- `GLM-Conductor-v2.2-Quota-Continuity-Control-Loop-Implementation-Plan.md`
- `GLM-Conductor-v2.2-Quota-Control-Plane-Architecture-Correction-and-Agent-Implementation-Plan.md`
- `GLM-Conductor-v2.2-Release-Hardening-Implementation-Plan.md`
- `GLM-Conductor-v2.2-实施前缺口探查与设计决策记录.md`
- `GLM-Conductor-v2.2.0-Stable-Release-Implementation-Plan.md`

Preferred treatment:

```text
git mv docs/<file>
       docs/history/v2.2/<file>
```

Preserve them as historical evidence unless a file is proven to be a pure duplicate with no unique information.

### Add history index

`docs/history/v2.2/README.md` should explain:

- these files are development/release evidence;
- they are not current user documentation;
- current truth sources are README, architecture, core-concepts, and shipped runtime;
- historical terminology may describe intermediate designs later superseded.

This prevents old docs from becoming accidental truth sources.

---

## WU-221-E5 — Clean `docs/architecture.md`

`architecture.md` should remain the authoritative technical design document, but it should stop serving simultaneously as:

- current architecture;
- release diary;
- historical compatibility ledger;
- implementation plan;
- review evidence register.

### Keep in architecture

- design principles;
- state model;
- routing model;
- dispatch transaction model;
- completion/evidence model;
- quota/continuity architecture;
- durable storage model;
- recovery;
- stable boundaries;
- security model.

### Move/remove

Move release chronology and long historical correction notes to `docs/history`.

Use short “Since v2.x” notes only where compatibility requires context.

### Preferred architecture rule

Every section should describe:

> **what the system is now**

not:

> the sequence of events by which it became that system.

---

## WU-221-E6 — Simplify `CHANGELOG.md`

CHANGELOG should describe released user-visible and maintainer-relevant changes.

It should not reproduce implementation-plan detail.

For `2.2.0`, condense the release entry into grouped categories:

```markdown
## 2.2.0

### Added
- quota epoch/subscription
- watcher
- persistent recurring bridge
- activation transport abstraction
- continuity Stop gate

### Changed
- dual-layer quota control
- resume accounting
- runtime enforcement

### Hardened
- primer single-attempt behavior
- mechanical primer authorization
- crash-safe quota accounting

### Known boundaries
- recurring_bridge only
- watcher no session injection
- primer off by default
```

Detailed RH/C/M records remain in `docs/history/v2.2`.

Add a concise `2.2.1` entry when implementation finishes.

---

## WU-221-E7 — Add `docs/README.md` documentation index

This should be short.

Recommended groups:

```text
Start here
- README
- Core Concepts

Technical Reference
- Architecture
- Skill/runtime references

History
- v2.2 development and release evidence
```

The documentation index explicitly labels history as non-authoritative for current behavior.

---

# 11. Workstream F — Repository Metadata and Release Hygiene

## WU-221-F1 — Normalize GitHub repository description

Recommended concise description:

> Deterministic orchestration, execution assurance, and quota-aware continuity for GLM coding agents in ZCode.

If bilingual metadata is preferred:

> ZCode GLM Coding Agent 的确定性编排、执行保障与额度感知连续性 | Deterministic orchestration and quota-aware continuity for GLM coding agents.

Avoid a description that enumerates every agent role or implementation detail.

---

## WU-221-F2 — Normalize repository topics

Recommended topics:

```text
zcode
glm
coding-agent
agent-orchestration
multi-agent
llm-agent
quota-aware
long-running-agent
agent-runtime
```

Keep the topic list focused; do not mirror all plugin features.

---

## WU-221-F3 — Main branch protection proposal

Because this repository can be modified by coding agents, mechanical repository protection is aligned with the project philosophy.

Recommended `main` rules:

- pull request required;
- `validate-plugin` required;
- force push disabled;
- branch deletion disabled;
- optional owner/admin bypass for emergency recovery.

**This is a repository-admin configuration change and should only be applied with explicit user approval at implementation time.**

The implementation agent may prepare the exact settings/change plan, but must not silently change repository protection if the execution environment treats it as a privileged repository setting.

---

## WU-221-F4 — Release branch lifecycle

After v2.2.1 is released:

```text
main          current stable truth
v2.2.1 tag    immutable release reference
v2.3-dev      future feature development
```

The old `v2-dev` branch should no longer remain the ambiguous active branch.

Whether to delete or retain the old branch is a repository-maintenance decision. Prefer retaining it temporarily until v2.2.1 release verification is complete, then delete/archive only with explicit user approval if required by the execution policy.

---

# 12. Workstream G — Repository Surface Cleanup

## WU-221-G1 — Root directory review

The root should ideally contain only release-relevant items:

```text
.github/
docs/
plugins/
scripts/
tests/
.gitattributes
.gitignore
CHANGELOG.md
LICENSE
README.md
README.en.md
marketplace.json
```

Do not add unnecessary governance files purely to imitate large organizations.

Optional files such as `CONTRIBUTING.md`, `SECURITY.md`, or `CODE_OF_CONDUCT.md` should only be added if the project actually needs them.

For a primarily personal/open-source technical project, a clean README + LICENSE + issue/PR workflow is sufficient.

---

## WU-221-G2 — Remove stale terminology and duplicated claims

Audit:

- README CN/EN;
- architecture;
- Skill docs;
- command docs;
- plugin description;
- GitHub description;
- CHANGELOG.

Rules:

- one canonical phrase for project positioning;
- one canonical stable-boundary list;
- one canonical explanation of the three core pillars;
- no old “exact reset timer = resume truth” language;
- no claim that watcher itself injects turns;
- no claim that reserved transports are supported;
- no claim that Primer is default-on;
- no duplicate long-form quota mechanism explanation in README and architecture.

The runtime and architecture are the technical truth; README is a concise projection of that truth.

---

# 13. Implementation Sequence

The agent should implement in the following order.

## ST-00 — Baseline and branch reconciliation

- confirm `main`, `v2-dev`, tags, current CI;
- reconcile the post-release documentation commit into the stable line;
- create `v2.2.1-hardening`;
- record baseline test count;
- run validator + full suite before code changes.

**Gate:** clean branch, clean worktree, green baseline.

---

## ST-01 — Durable I/O primitive

Implement WU-221-A1.

**Gate:**
- all migrated writers green;
- unique temp files proven;
- no behavior drift.

---

## ST-02 — Watcher lock hardening

Implement WU-221-A2/A3.

**Gate:**
- multiprocessing simultaneous-start test passes repeatedly;
- stop/heartbeat race regression passes;
- no duplicate active watcher.

---

## ST-03 — Provider identity hardening

Implement WU-221-B1/B2.

**Gate:**
- cross-account cache tests pass;
- legacy cache/task records still work;
- no state/journal destructive rewrite.

---

## ST-04 — Runtime decomposition

Implement WU-221-C1/C2 in small commits.

**Gate:**
- public API imports unchanged;
- full suite green after each extraction;
- no bundled behavior changes.

---

## ST-05 — Repository/documentation cleanup

Implement E1–E7 and G1–G2.

Suggested order:

1. create `docs/core-concepts.md`;
2. create `docs/README.md`;
3. reorganize historical docs with `git mv`;
4. clean architecture;
5. rewrite README CN;
6. mirror README EN;
7. condense CHANGELOG;
8. update plugin/repository-facing description where appropriate.

**Gate:** README describes current system without requiring history docs.

---

## ST-06 — CI/system-assurance pass

Implement D1 and optional D3.

Run:

- validator;
- full unit suite;
- multiprocessing/integration suite;
- Linux/Windows CI;
- plugin-load smoke test if added.

---

## ST-07 — Independent release review

Use a fresh context/reviewer.

Review priorities:

1. no v2.2 public behavior regression;
2. cross-process race actually closed;
3. identity-switch semantics safe;
4. historical docs do not remain current truth sources;
5. README claims are supported by runtime;
6. architecture describes current system rather than development history;
7. no accidental deletion of unique historical evidence;
8. stable boundaries remain honest.

Verdict must be:

```text
ship
fix-first
rethink
```

Only `ship` proceeds.

---

## ST-08 — Release

- bump plugin version to `2.2.1`;
- add concise CHANGELOG entry;
- ensure README badges match;
- run validator/version check;
- push maintenance branch;
- require CI green;
- merge by PR into `main`;
- verify post-merge CI;
- tag `v2.2.1`;
- publish GitHub Release;
- then begin `v2.3-dev` only if desired.

---

# 14. File-Level Touch Map

Expected primary files/modules:

```text
plugins/glm-conductor/runtime/
    durable_io.py                         NEW or equivalent
    task_manager.py                       facade/extraction
    quota/resolver.py
    quota/watcher.py
    quota/watcher_store.py
    quota/primer.py                       only if shared durable primitive adopted
    scheduler_facts.py
    activation_transport.py               likely minimal/no behavior change

tests/
    test_multiprocess_runtime.py          NEW
    test_watcher.py
    test_watcher_store.py
    test_quota_resolver.py
    test_quota_subscription.py
    test_boundary_consumption.py
    test_resume_consumption.py
    ... focused extraction tests

README.md
README.en.md
CHANGELOG.md

docs/
    README.md                              NEW
    core-concepts.md                      NEW
    architecture.md
    history/v2.2/README.md                NEW
    history/v2.2/*                        MOVED development records
```

Avoid touching unrelated visual-agent/orchestration behavior unless required by documentation links or validator consistency.

---

# 15. Required Regression Matrix

## Runtime persistence

- [ ] unique-temp JSON writer;
- [ ] simultaneous JSON writers;
- [ ] watcher single acquire;
- [ ] stale watcher takeover;
- [ ] stop/heartbeat race;
- [ ] foreground + watcher quota cache race;
- [ ] temp-file interruption;
- [ ] Windows PermissionError bounded handling.

## Provider identity

- [ ] same credential cache reuse;
- [ ] different credential cache invalidation;
- [ ] legacy cache without identity;
- [ ] subscription across provider switch;
- [ ] consumption across provider switch;
- [ ] no secret material written.

## Compatibility

- [ ] v2.2 state loads;
- [ ] v2.2 journal reads;
- [ ] old quota events read;
- [ ] old cache degrades safely;
- [ ] existing CLI keys and exit codes unchanged;
- [ ] recurring bridge behavior unchanged;
- [ ] primer default remains off;
- [ ] reserved transports still raise reserved error.

## Documentation/repository

- [ ] CN/EN README parity;
- [ ] current docs contain no development-unit terminology as required reading;
- [ ] history docs clearly labeled historical;
- [ ] architecture is current-state oriented;
- [ ] CHANGELOG is release-oriented;
- [ ] GitHub description matches project positioning;
- [ ] version surfaces agree.

---

# 16. Definition of Done

v2.2.1 is complete only when all of the following are true.

## 16.1 Runtime

- watcher single-instance claim is enforced atomically;
- shared JSON writers use unique temporary files;
- shared read-modify-write paths have explicit lock semantics;
- provider identity prevents foreign quota cache reuse;
- cross-provider quota activation/consumption cannot silently alias;
- legacy v2.2 state remains compatible;
- no new stable activation transport was added.

## 16.2 Maintainability

- `task_manager.py` has begun behavior-preserving decomposition;
- quota shared helpers no longer rely unnecessarily on sibling private imports;
- no large rewrite or state redesign was introduced;
- public runtime facade remains stable.

## 16.3 Testing

- full existing suite green;
- validator green;
- new multiprocessing suite green;
- Linux + Windows CI green;
- no test relies on real provider/model network calls;
- new tests focus on behavior/system races rather than test-count growth.

## 16.4 Repository

- `README.md` is concise and release-facing;
- `README.en.md` mirrors it accurately;
- project identity is centered on the three pillars;
- `docs/core-concepts.md` exists;
- `docs/README.md` exists;
- development-process v2.2 documents are moved under `docs/history/v2.2/`;
- historical docs remain available but are explicitly non-authoritative;
- `architecture.md` describes the current system;
- `CHANGELOG.md` is condensed to release-level changes;
- root directory remains clean.

## 16.5 Release

- `main` is the current stable truth;
- maintenance branch CI is green;
- independent release review says `ship`;
- version surfaces all equal `2.2.1`;
- PR merged without force/rebase history rewrite;
- post-merge CI green;
- `v2.2.1` tag and GitHub Release published.

---

# 17. Guidance for the Implementing Agent

## 17.1 General execution style

Use small, reviewable work units.

For each work unit:

1. inspect current behavior and tests;
2. state the invariant being preserved;
3. implement the narrowest change;
4. add focused regression tests;
5. run targeted tests;
6. run full validator/full suite at milestone boundaries;
7. inspect the diff for unrelated churn;
8. do not “clean up” neighboring code opportunistically.

## 17.2 Hard rule: no feature creep

If implementation reveals a desirable new feature, record it as a future v2.3 note.

Do not implement it in v2.2.1 unless it is required to close one of the hardening defects defined in this plan.

## 17.3 Hard rule: history cleanup must preserve evidence

“Repository cleanup” does not mean deleting the development record.

Prefer:

```text
git mv → docs/history/v2.2/
```

over deletion.

Delete only files proven to be exact/redundant artifacts with no unique design or evidence value.

## 17.4 Hard rule: README is not architecture

Do not move all technical details into README.

README should be readable in a few minutes.

Use:

- README = project entry;
- core-concepts = conceptual model;
- architecture = technical truth;
- Skill/command references = operational detail;
- history = implementation/release evidence.

---

# 18. Suggested Final Repository Documentation Surface

```text
README.md
README.en.md
CHANGELOG.md
LICENSE

docs/
├── README.md
├── core-concepts.md
├── architecture.md
└── history/
    └── v2.2/
        ├── README.md
        ├── GLM-Conductor-v2.2-Dogfood-Records.md
        ├── GLM-Conductor-v2.2-Persistent-Wake-Bridge-Architecture-Correction.md
        ├── GLM-Conductor-v2.2-Phase0-Primer-Experiments.md
        ├── GLM-Conductor-v2.2-Phase0-Scheduled-Wake-Runtime-Verification.md
        ├── GLM-Conductor-v2.2-Quota-Continuity-Control-Loop-Implementation-Plan.md
        ├── GLM-Conductor-v2.2-Quota-Control-Plane-Architecture-Correction-and-Agent-Implementation-Plan.md
        ├── GLM-Conductor-v2.2-Release-Hardening-Implementation-Plan.md
        ├── GLM-Conductor-v2.2-实施前缺口探查与设计决策记录.md
        └── GLM-Conductor-v2.2.0-Stable-Release-Implementation-Plan.md
```

If additional historical v1/v2.0/v2.1 development plans exist, do not move them blindly in the same commit. First classify them and build the same history structure incrementally.

---

# 19. Final Release Character

v2.2.1 should be presented as:

> **A maintenance release focused on cross-process durability, provider-identity correctness, maintainability, and a clean public repository surface.**

Not as:

> a new feature release.

The correct success signal is not “more features”.

It is:

- fewer ambiguous truth sources;
- safer persistence;
- easier code review;
- clearer project identity;
- a README suitable for a new user;
- historical evidence still available without dominating the repository;
- no regression of the v2.2 deterministic orchestration and quota-continuity contracts.
