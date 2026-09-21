# GLM Conductor v2.4 Audit-Fix Closeout Plan

> **Status:** implementation-complete → post-implementation audit hardening.  
> **Branch:** `review/zcode-3.14-native-workflow`.  
> **Authority:** `docs/reviews/V2_4_NATIVE_WORKFLOW_REBASELINE_SPEC.md` plus the completed Phase 1–4 specs.  
> **Important:** this is **not Phase 5** and does not reopen v2.4 architecture. It is a bounded release-hardening closeout before merge to `main`.

## 1. Why this closeout exists

Phase 1–4 delivered the intended native-first re-baseline:

- ordinary text delegation uses ZCode Native Workflow;
- Work Units are static DAG nodes;
- dispatcher / wave / permit / per-unit lease / provenance / reconcile / agent-run ledger are retired;
- task state, validation/review freshness and recovery are task-level;
- quota control-plane machinery is removed;
- Global Quota Clock is extracted from the Conductor boundary;
- final v2.4 docs/skills/tests were rewritten around the new architecture.

The post-implementation audit found a small number of release-hardening issues. They do **not** justify another architecture phase, but two are correctness/capability merge blockers.

## 2. Findings and severity

### AF-01 — change_id does not expand ownership scopes to actual files — **MERGE BLOCKER**

Current task freshness derives:

```text
task relevant-paths state = union(DAG ownership scopes)
                          ↓
change_id.compute_change_id(...)
```

but `compute_change_id` treats every input as a literal repository-relative file path.

Therefore a valid ownership scope such as:

```text
src/parser/**
docs/
tests/**/*.py
```

is hashed as a literal path (usually `missing`) rather than as the actual files currently changed inside that scope.

Consequence: a file modified after validation/review may not change `change_id`, so stale validation/review can survive.

This violates the core v2.4 freshness invariant and must be fixed before merge.

### AF-02 — visual-reviewer may inherit a non-multimodal main model — **MERGE BLOCKER**

P1-A removed `model:` from both reviewer definitions to avoid provider-qualified binding failures.

That is acceptable for `glm-reviewer`, whose intended capability can match the GLM-5.3 main/session model.

It is not automatically valid for `visual-reviewer`:

- v2.4 defines the visual reviewer as a multimodal role that must directly inspect screenshots;
- the normal main session is GLM-5.3 and is treated by this project as text-only;
- inheriting the main/session model can make `visual-reviewer` start successfully while silently losing the capability required by its contract.

Visual review must fail closed if a verified multimodal Flash binding is unavailable.

### AF-03 — quota-resume domain logic lacks a stable callable plugin surface — **HARDENING**

`runtime.task` contains the correct v2.4 primitives:

- `enter_waiting_quota`
- `authorize_quota_resume`
- `scheduled_activation_decision`
- `confirm_resume_started`

but the final CLI exposes only `quota-resolve`.

The continuity skill therefore describes direct Python API calls while orchestration policy otherwise says runtime actions should use stable CLI entry points rather than ad-hoc `python3 -c` snippets.

Add a very small CLI surface; do not rebuild a continuity control plane.

### AF-04 — writer guard acquisition is protocol-enforced, not lifecycle-enforced — **HARDENING**

The writer guard implementation itself is concurrency-safe, but the invariant:

```text
writer-acquire → CreateWorkflow → record_workflow_run
```

is currently enforced by orchestration instructions.

`record_workflow_run` does not require the current task to own the writer guard, and the completion guard currently allows a delegated task when the holder record is absent.

Strengthen the normal Conductor lifecycle without inventing host hooks:

- delegated run registration must require guard ownership;
- delegated completion must reject a missing guard as well as another task's guard.

This does not pretend to intercept arbitrary raw CreateWorkflow calls outside Conductor.

### AF-05 — fresh-cache hook launcher is not yet proven on the target host — **RELEASE BLOCKER**

Current `hooks.json` launches SessionStart and Stop with `python3`.

The original ZCode 3.14 spike observed a Windows environment where `python3` resolved to the Microsoft Store stub, so hooks did not actually execute.

The Phase-4 closeout was performed while an older cached plugin definition was still active. Therefore the final v2.4 hook path must be validated after cache refresh on the real target host.

Use the smallest verified launcher fix if required; do not build a general launcher framework.

### AF-06 — fresh-session reviewer launch still needs final proof — **RELEASE BLOCKER**

Static frontmatter/tool checks passed, but release readiness still needs a fresh plugin cache + fresh session proof for:

- `glm-reviewer`;
- `visual-reviewer`, including actual screenshot/image reading.

This proof should occur after AF-02 so the test verifies the final capability contract.

## 3. Closeout sequence

Use four bounded waves. Do not create additional top-level phases.

```text
Wave A — Correctness blockers
  AF-01 change_id scope freshness
  AF-02 visual reviewer multimodal binding

Wave B — Lifecycle hardening
  AF-03 quota-resume CLI
  AF-04 writer-guard lifecycle checks

Wave C — Host integration proof
  AF-05 hook launcher + fresh plugin cache
  AF-06 fresh-session reviewer launch

Wave D — Final release gate
  docs/tests consistency cleanup
  targeted integration
  one final full regression
  merge-candidate report
```

Wave A and B may be implemented independently where files do not overlap, but Wave C must use the final Wave A/B plugin state.

## 4. Testing policy

The closeout must **not** repeat the previous over-testing pattern.

### During Waves A/B

Run only tests directly affected by the fix plus immediate lifecycle integration tests.

Do not run all 630 tests after each work item.

### Wave C

Use live host probes only:

- fresh plugin cache;
- fresh session;
- SessionStart;
- Stop guard;
- text reviewer;
- visual reviewer + a real small image fixture.

Do not rerun unrelated Workflow spike cases.

### Wave D

Run:

1. focused audit-fix test bundle;
2. plugin validator;
3. plugin smoke-load;
4. py_compile/import checks;
5. one minimal delegate/full lifecycle smoke if needed;
6. **one final full project regression**.

If the final full regression exposes a stale legacy test, fix the test suite and rerun only the affected subset first; rerun the full suite once more only for final confirmation.

## 5. Freeze constraints

The closeout must not:

- restore any v2.3 dispatcher / wave / permit / lease / receipt / reconcile subsystem;
- create a new provenance framework;
- reintroduce Global Quota Clock into glm-conductor;
- add a resident quota watcher;
- build a generic model-binding abstraction unless current ZCode requires one for AF-02;
- create per-node runtime state;
- duplicate Native Workflow lifecycle state;
- add a new Phase 5 architecture document.

## 6. Merge gate

The branch becomes a v2.4.0 merge candidate only when all are true:

- AF-01: glob/directory ownership changes reliably stale validation/review;
- AF-02: visual-reviewer is demonstrably multimodal or fails closed;
- AF-03: all documented quota-resume lifecycle actions have stable CLI entry points;
- AF-04: delegated run registration/completion cannot pass the normal Conductor lifecycle without owning the repository writer guard;
- AF-05: SessionStart and Stop hooks actually execute from a fresh plugin cache on the target host;
- AF-06: fresh-session text reviewer and visual reviewer launch tests pass;
- targeted audit-fix suite passes;
- validator and plugin smoke pass;
- final full regression passes;
- current-truth docs no longer overstate any guarantee.

## 7. Deliverables

Implementation must produce:

- code/tests for AF-01–AF-05 as needed;
- live evidence for AF-05/AF-06;
- updated architecture/skills only where behavior changed;
- `docs/reviews/V2_4_AUDIT_FIX_CLOSEOUT_REPORT.md` with exact evidence;
- a final recommendation: `MERGE_READY` or `NOT_READY`.

Detailed implementation requirements are in:

`docs/roadmap/V2_4_AUDIT_FIX_CLOSEOUT_IMPLEMENTATION_SPEC.md`.
