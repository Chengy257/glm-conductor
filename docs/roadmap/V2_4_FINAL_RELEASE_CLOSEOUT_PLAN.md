# GLM Conductor v2.4 Final Release Closeout Plan

> **Status:** COMPLETE — FR-01–FR-04 closed with evidence; final verdict `MERGE_READY` in `docs/reviews/V2_4_AUDIT_FIX_CLOSEOUT_REPORT.md` (Final Release Closeout Addendum §A1–§A5).  
> **Branch:** `review/zcode-3.14-native-workflow`  
> **Purpose:** close the final merge-blocking correctness and release-evidence gaps found after the v2.4 audit-fix implementation.  
> **Important:** this is **not Phase 5**, not a new architecture review, and not a repeat of AF-01–AF-06.

## 1. Current baseline

The v2.4 native-first implementation and the audit-fix campaign are materially complete.

Already accepted and **not to be reimplemented**:

- AF-01: change freshness derives from exact owned touched files;
- AF-02: visual reviewer has an explicit multimodal GLM-5.3-Flash binding and fail-closed semantics;
- AF-03: bounded quota resume has a stable CLI surface;
- AF-04: delegated run registration and completion enforce repository writer-guard ownership;
- AF-05: the target host executes the current `python3` SessionStart/Stop hook launcher;
- AF-06: text and visual reviewer roles have live functional evidence, including a visual blind read.

The post-closeout repository review found one additional correctness bug and two release-evidence consistency gaps. These are the only subjects of this plan.

## 2. Final findings

### FR-01 — terminal task cleanup releases writer guard at the wrong root — **MERGE BLOCKER**

v2.4 explicitly supports a task ledger root and a bound `repository.root` being different.

The runtime correctly uses:

```text
state.resolve_repository_root(task_state, ledger_root)
```

for:

- touched-file discovery;
- change_id evaluation;
- delegated run writer-guard inspection/acquire;
- completion-guard writer inspection.

However `runtime.task._finish()` currently calls:

```python
writer_guard.release(repo_root, task_id)
```

where `repo_root` is the task ledger root passed into the lifecycle API.

If:

```text
ledger root      = A
repository.root  = B
writer guard     = B/.glm-conductor/writer_guard.json
```

then `complete/fail/cancel(A, task_id)` can transition the task terminal while attempting to release a non-existent guard under A, leaving the real guard under B stale.

This violates the repository-binding contract and the one-active-writer lifecycle invariant.

### FR-02 — literal fresh-session reviewer proof is still missing — **RELEASE GATE**

The existing audit evidence is strong:

- both reviewer roles launched;
- visual-reviewer ran on the intended multimodal binding;
- a blind image-read test proved real multimodal capability.

But the authoritative audit-fix merge gate explicitly required:

> refreshed plugin cache + **fresh ZCode session** + reviewer launch.

The recorded proof was performed in an already-running orchestration session whose agent snapshot predated the cache refresh.

Do not weaken the original gate. Close it with a very small fresh-session smoke.

### FR-03 — current-truth/public release documents disagree with the closeout state — **RELEASE GATE**

At the present branch head:

- the audit closeout report says `MERGE_READY`;
- `CHANGELOG.md` still says AF-05/AF-06 are open/pending;
- README still says fresh-session reviewer proof is pending;
- `docs/README.md` still describes Phase 4 as in progress and does not index the final audit closeout;
- `V2_4_NATIVE_WORKFLOW_IMPLEMENTATION_PLAN.md` still says post-implementation audit is ACTIVE.

After FR-01/FR-02 close, all current-truth surfaces must converge on one final state.

### FR-04 — final regression / CI evidence must cover the final code, not the pre-FR-01 code — **RELEASE GATE**

The previous 677-test full regression was valid for the audit-fix head at that time.

FR-01 changes task terminal lifecycle behavior, so a final targeted test pass and one final full regression are required on the actual merge candidate.

## 3. Execution sequence

Use three compact waves only:

```text
Wave F1 — Correctness
  FR-01 effective repository-root writer release
  targeted lifecycle regression

Wave F2 — Host proof + truth synchronization
  FR-02 fresh-session text/visual reviewer smoke
  FR-03 README/CHANGELOG/docs status convergence

Wave F3 — Final gate
  targeted final-closeout bundle
  validator + smoke + compile/lint
  one full regression
  remote CI after push when available
  final closeout report update
```

Do not dispatch new architecture work.

## 4. Testing discipline

### F1

Run only:

- task lifecycle tests;
- completion guard tests;
- writer guard tests;
- new repository-root separation regression.

No full suite.

### F2

Do not rerun Workflow spike cases.

Only perform:

- a fresh-session `glm-reviewer` smoke;
- a fresh-session `visual-reviewer` image-read smoke.

Documentation edits require validator/smoke only if they affect validator anchors.

### F3

Run in this order:

1. targeted final-closeout tests;
2. plugin validator;
3. plugin smoke loader;
4. compile/import checks and current lint command;
5. **one full project regression**;
6. push;
7. inspect GitHub Actions for the pushed merge-candidate commit if CI is configured.

If the full regression reveals a defect, fix it and rerun only the affected subset during repair. Run the full suite once more only after all repairs are complete.

## 5. Freeze constraints

The implementation must not:

- modify route semantics;
- alter Native Workflow compilation;
- restore retired dispatcher/wave/permit/lease/provenance machinery;
- introduce a writer-guard lease/TTL/heartbeat;
- revisit quota architecture or Global Quota Clock extraction;
- weaken the fresh-session evidence requirement;
- broaden model-binding abstractions;
- create a Phase 5.

## 6. Merge gate

`review/zcode-3.14-native-workflow` may be merged to `main` only when:

- FR-01 fixed: complete/fail/cancel release the guard from the effective bound repository root;
- a regression proves ledger root != repository root is handled correctly;
- FR-02 fresh-session `glm-reviewer` launch passes;
- FR-02 fresh-session `visual-reviewer` launches on the verified Flash binding and actually reads a test image;
- FR-03 README/README.zh-CN/CHANGELOG/docs index/master implementation plan agree on final status;
- the audit closeout report contains a final addendum for FR-01–FR-04 and no longer relies on a contradictory pending limitation;
- targeted final-closeout tests pass;
- validator/smoke/compile/lint gates pass;
- the final full regression passes;
- remote CI is green when available.

The final verdict must be exactly:

```text
MERGE_READY
```

or:

```text
NOT_READY
```

## 7. Required deliverables

- code fix and tests for FR-01;
- recorded fresh-session evidence for FR-02;
- synchronized current-truth documentation for FR-03;
- final test/CI evidence for FR-04;
- an updated `docs/reviews/V2_4_AUDIT_FIX_CLOSEOUT_REPORT.md` final addendum, or an equivalent final release section in that report;
- no additional architecture document beyond this final-closeout plan/spec.

Detailed implementation instructions:

`docs/roadmap/V2_4_FINAL_RELEASE_CLOSEOUT_IMPLEMENTATION_SPEC.md`.
