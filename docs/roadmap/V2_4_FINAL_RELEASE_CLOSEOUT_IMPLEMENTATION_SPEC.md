# GLM Conductor v2.4 Final Release Closeout — Detailed Implementation Spec

> **Parent plan:** `docs/roadmap/V2_4_FINAL_RELEASE_CLOSEOUT_PLAN.md`  
> **Target branch:** `review/zcode-3.14-native-workflow`  
> **Scope:** FR-01–FR-04 only. Earlier v2.4 phases and AF-01–AF-06 are frozen implementation baseline.

## 0. Agent execution rule

This is a release-closeout task, not an architecture task.

Implementation agent must:

1. inspect the current branch before editing;
2. change only files needed by FR-01–FR-04;
3. preserve all v2.4 native-first boundaries;
4. run targeted tests during implementation;
5. defer the full regression to the final gate;
6. update evidence with actual command outputs/counts, never predicted results.

---

# Wave F1 — FR-01 effective-root writer release

## F1.1 Fix `runtime.task._finish`

### Current incorrect behavior

`_finish(repo_root, task_id, ...)` uses `repo_root` for both:

- task ledger/state/journal operations; and
- `writer_guard.release`.

Those are not guaranteed to be the same root.

### Required implementation

After loading the v2.4 task state, resolve the effective repository root once:

```python
effective_root = state.resolve_repository_root(st, repo_root)
```

Keep task state and journal I/O on the ledger root `repo_root`.

Release the writer guard on `effective_root`:

```python
writer_guard.release(effective_root, task_id)
```

This applies to all terminal lifecycle paths because:

- `complete()`;
- `fail()`;
- `cancel()`

share `_finish()`.

### Required invariants

Do not change:

- transition legality;
- idempotent same-terminal replay;
- journal location;
- journal event vocabulary;
- non-owner guard safety;
- terminal status semantics.

Do not add a second release call to the ledger root as a compatibility fallback. The correct root is the resolved repository root.

### Docstring correction

Update `_finish` / module documentation so it explicitly states:

- state/journal live at ledger root;
- repository writer guard lives at effective bound repository root.

## F1.2 Add a real two-root regression fixture

Tests must exercise two physically different directories.

Recommended fixture:

```text
tmp/
  ledger/
    .glm-conductor/tasks/<task>/state.json
  repo/
    .git/
    .glm-conductor/writer_guard.json
```

Prepare task state such that:

```text
repository.root = <tmp>/repo
ledger API root = <tmp>/ledger
```

Do not fake this merely by mocking `writer_guard.release`; at least one test must inspect the actual guard file/holder in the bound repository.

### Minimum tests

Add tests for all terminal paths:

1. `complete(ledger_root, task_id)`
   - guard acquired at repository root;
   - result status completed;
   - repository-root guard cleared;
   - no accidental ledger-root guard created.

2. `fail(ledger_root, task_id)`
   - same root assertions.

3. `cancel(ledger_root, task_id)`
   - same root assertions.

4. non-owner protection:
   - if bound repository guard belongs to another task, terminal state behavior must remain exactly as current contract specifies;
   - the other task's guard must never be deleted.

5. same-root baseline:
   - existing behavior remains green when ledger root == repository root.

### Integration test through completion guard

Add one test using the actual completion guard:

```text
ledger root A
repository root B
own writer guard at B
valid owned change at B
fresh validation
evaluate_completion(A, task)
→ allow / completed
→ guard at B absent
```

This is the decisive regression for the discovered bug.

### Expected files

Primary:

- `plugins/glm-conductor/runtime/task.py`
- `tests/test_task_lifecycle.py`
- `tests/test_completion_guard.py`

Only touch `writer_guard.py` if a failing test exposes a real helper defect; no change is expected.

## F1.3 F1 targeted gate

Run:

```text
tests.test_task_lifecycle
tests.test_completion_guard
tests.test_writer_guard
```

plus any directly added repository-binding test module if a separate file is used.

Do not run the full suite here.

F1 exit:

- all targeted tests green;
- a real two-root test proves the guard is removed from the bound repository.

---

# Wave F2 — FR-02 fresh-session proof and FR-03 truth synchronization

## F2.1 Fresh plugin cache

Before the fresh-session reviewer proof:

1. ensure the installed/cached plugin payload matches the final repository payload after F1;
2. ensure `plugin.json` reports 2.4.0;
3. remove any temporary compatibility/no-op hook stubs used only to keep an older session alive, or verify the new session does not reference them;
4. start a genuinely new ZCode conversation/session after refresh.

Record:

- plugin cache/version identifier;
- repository commit under test;
- host ZCode version;
- available reviewer types/model binding as observed in the new session.

Do not reuse the already-running implementation session as proof.

## F2.2 Fresh-session `glm-reviewer` smoke

In the new session:

- invoke `glm-conductor:glm-reviewer`;
- give it a tiny controlled diff or fixture plus validation evidence;
- confirm it starts successfully;
- confirm its tools are read-only;
- confirm output follows `GLM REVIEW`;
- record verdict and actual runtime model if observable.

This is a launch/capability smoke, not another full review of v2.4.

## F2.3 Fresh-session `visual-reviewer` smoke

In the same new session or another fresh session:

- invoke `glm-conductor:visual-reviewer`;
- confirm the actual role binding resolves to the verified multimodal GLM-5.3-Flash provider binding;
- provide a small image whose content is **not described in the prompt**;
- ask for a bare description / visual acceptance;
- confirm the result reports the actual visible content;
- confirm output follows `VISUAL REVIEW`;
- confirm the role remains read-only.

A launch that does not prove image reading is not sufficient.

If the visual binding is unavailable:

- fail closed;
- record `NOT_READY`;
- do not substitute glm-reviewer or the main session.

## F2.4 Synchronize public/current-truth documents

Only after F1 and fresh-session F2 proofs pass, update these surfaces together.

### `README.md` and `README.zh-CN.md`

Replace the pending reviewer limitation with final verified wording.

Do not remove legitimate remaining limitations such as:

- scheduled firing while app closed remains unverified, if still true;
- visual roles require the verified Flash binding.

Ensure EN/ZH remain semantically aligned.

### `CHANGELOG.md`

In 2.4.0:

- change AF-05/AF-06 from open/pending to closed with concise evidence;
- remove stale migration/limitation wording saying fresh-session proof is pending;
- add FR-01 as the final repository-binding writer-guard correctness fix;
- mention the final fresh-session proof without turning CHANGELOG into a long test log.

### `docs/README.md`

Update status from:

```text
Phases 1–3 complete / Phase 4 closing
```

to the final state:

```text
Phase 0–4 complete
audit-fix complete
final release closeout complete/pending final regression as appropriate
```

Add direct links to:

- `V2_4_AUDIT_FIX_CLOSEOUT_PLAN.md`
- `V2_4_AUDIT_FIX_CLOSEOUT_IMPLEMENTATION_SPEC.md`
- `V2_4_FINAL_RELEASE_CLOSEOUT_PLAN.md`
- `V2_4_FINAL_RELEASE_CLOSEOUT_IMPLEMENTATION_SPEC.md`
- `V2_4_AUDIT_FIX_CLOSEOUT_REPORT.md`

Replace the old “Phase 4 closeout report placeholder” language with actual evidence links.

### `V2_4_NATIVE_WORKFLOW_IMPLEMENTATION_PLAN.md`

Update `Current implementation status` so it no longer says the post-implementation audit is ACTIVE.

Before F3 final regression it may say:

```text
Post-implementation audit: COMPLETE
Final release closeout: ACTIVE
```

After F3 success it must say:

```text
Post-implementation audit: COMPLETE
Final release closeout: COMPLETE
Merge candidate: READY
```

### `docs/reviews/V2_4_AUDIT_FIX_CLOSEOUT_REPORT.md`

Do not erase the historical AF-01–AF-06 evidence.

Add a clearly dated **Final Release Closeout Addendum** covering:

- FR-01 finding and fix;
- fresh-session reviewer proof;
- document truth synchronization;
- final test/CI evidence;
- revised final head/commit;
- final verdict.

The addendum must supersede the old §8 statement that a literal fresh-session test is still pending.

## F2.5 F2 gate

F2 passes only if:

- both reviewer smokes were performed in a genuinely fresh session after cache refresh;
- visual reviewer actually read an image;
- public/current-truth docs no longer contradict the final evidence state.

---

# Wave F3 — FR-04 final merge-candidate verification

## F3.1 Targeted final-closeout bundle

Run at minimum:

```text
tests.test_task_lifecycle
tests.test_completion_guard
tests.test_writer_guard
tests.test_agent_model_binding
```

Include any added repository-binding test module.

If F2 modified validator-sensitive agent/docs anchors, run the relevant validator-specific tests as well.

## F3.2 Static/plugin gates

Run the repository's current canonical commands:

- `scripts/validate_plugin.py`;
- `scripts/smoke_plugin_load.py`;
- current ruff/lint command used by the project;
- compile/import check used in the prior closeout.

Record exact results.

## F3.3 Full regression

Run once on the final working tree:

```text
python3 -X utf8 -m unittest discover -s tests -q
```

or the exact canonical equivalent for the active host.

Record the final test count and result.

If it fails:

- do not repeatedly run the full suite during debugging;
- repair using focused subsets;
- once all known failures are fixed, run the full suite one final time.

## F3.4 Push and CI

After local gates pass:

1. commit final-closeout code/docs;
2. push `review/zcode-3.14-native-workflow`;
3. inspect GitHub Actions for the pushed head if the repository workflow is configured to run on that branch/commit;
4. record matrix/job results.

If no CI run is triggered by repository policy, state that explicitly; do not fabricate remote evidence.

## F3.5 Final report verdict

Update the addendum in:

`docs/reviews/V2_4_AUDIT_FIX_CLOSEOUT_REPORT.md`

with:

- final branch head;
- F1 targeted evidence;
- F2 fresh-session evidence;
- F3 full regression count;
- validator/smoke/lint/compile evidence;
- remote CI evidence or explicit not-triggered state.

Final verdict:

```text
MERGE_READY
```

only if every mandatory gate passed.

Otherwise:

```text
NOT_READY
```

with the exact blocker.

---

# Expected change surface

Expected production code:

- `plugins/glm-conductor/runtime/task.py`

Expected tests:

- `tests/test_task_lifecycle.py`
- `tests/test_completion_guard.py`
- possibly a small dedicated repository-binding test file if cleaner.

Expected release documents:

- `README.md`
- `README.zh-CN.md`
- `CHANGELOG.md`
- `docs/README.md`
- `docs/roadmap/V2_4_NATIVE_WORKFLOW_IMPLEMENTATION_PLAN.md`
- `docs/reviews/V2_4_AUDIT_FIX_CLOSEOUT_REPORT.md`

No compiler, DAG, quota-core, model-routing, or retired-runtime module should change unless a directly observed failing test proves a separate correctness defect.

# Suggested commit grouping

Keep it small:

1. `fix(v2.4): release writer guard from bound repository root`
2. `docs(v2.4): record fresh-session reviewer release proof`
3. `docs(v2.4): finalize release truth and merge evidence`

Tests for FR-01 should normally travel with commit 1.

# Final acceptance checklist

- [ ] `task._finish` resolves effective repository root
- [ ] complete releases bound-root writer guard
- [ ] fail releases bound-root writer guard
- [ ] cancel releases bound-root writer guard
- [ ] non-owner guard is never deleted
- [ ] ledger-root/bound-repo integration regression passes
- [ ] fresh-session glm-reviewer launches read-only
- [ ] fresh-session visual-reviewer launches on multimodal Flash
- [ ] fresh-session visual-reviewer proves real image reading
- [ ] README EN/ZH no longer claim reviewer proof pending
- [ ] CHANGELOG closes AF-05/06 and records FR-01
- [ ] docs index points to final plans/report
- [ ] master v2.4 plan reports correct final status
- [ ] audit closeout report has final addendum
- [ ] targeted final-closeout tests pass
- [ ] validator passes
- [ ] smoke loader passes
- [ ] lint/compile gates pass
- [ ] final full regression passes
- [ ] remote CI green or explicitly not triggered
- [ ] report verdict is `MERGE_READY`
