# v2.4 Phase 4 — Integration, Documentation, and Release Closeout Spec

> **Authority:** `docs/reviews/V2_4_NATIVE_WORKFLOW_REBASELINE_SPEC.md`  
> **Prerequisite:** Phases 1–3 exit gates passed.  
> **Goal:** make implementation, tests, plugin metadata, skills and user documentation describe one coherent v2.4 architecture, then perform the single final full regression and release-readiness review.

## 1. Frozen outcomes

Phase 4 ends only when there is no production ambiguity between v2.3 and v2.4 behavior.

The final repository must communicate:

> GLM Conductor owns semantic orchestration and acceptance; ZCode owns execution orchestration.

Global Quota Clock is a separate future companion project, not a Conductor subsystem.

## 2. Work package P4-A — Repository-wide retired-surface audit

Search production code, tests, skills and docs for retired concepts:

- flash-implementer as ordinary text executor;
- dispatcher;
- dispatch wave;
- permit;
- per-unit lease;
- Work Unit runtime status;
- unit verification receipt;
- review receipt authority;
- agent-run ledger;
- reconcile mini-task state;
- resume manifest;
- Bash policy;
- finalizing;
- execution phase DRAINING/BLOCKED;
- quota watcher;
- quota epoch;
- subscription/accounting;
- Window Primer in Conductor;
- Global Quota Clock in Conductor;
- Activation Transport;
- Task Wake Bridge control plane;
- direct ZCode scheduler SQLite production write.

Classify every hit:

1. active production reference → must fix/remove;
2. historical changelog/review evidence → may remain;
3. explicit migration/extraction note → may remain.

Do not mechanically erase historical review evidence.

## 3. Work package P4-B — Rewrite the current-truth docs

Update together:

- `README.md`
- `README.zh-CN.md` if present
- `docs/architecture.md`
- `docs/core-concepts.md`
- `docs/troubleshooting.md`
- `docs/README.md`
- `CHANGELOG.md`
- marketplace/plugin descriptions
- version metadata.

### README positioning

Replace the old “orchestration + runtime assurance + quota-aware continuity / Global Clock + Wake Bridge” framing with:

- selective routing;
- Native Workflow execution;
- minimal deterministic assurance;
- optional bounded quota resume.

Mention Global Quota Clock only as a separate companion/future project if useful.

### Architecture

`docs/architecture.md` becomes the v2.4 current-truth source only after code is integrated.

It must describe:

- route matrix;
- static DAG;
- Workflow compiler boundary;
- host-owned execution state;
- ownership conflict handling;
- one active repo writer;
- task state;
- change_id;
- main validation;
- reviewer path;
- completion guard;
- visual exception;
- minimal quota resume;
- Global Clock separation.

Do not document removed modules as deprecated runtime paths.

## 4. Work package P4-C — Skill and agent contract cleanup

Update:

- orchestration skill;
- enforcement/completion-guard skill;
- continuity/quota-resume skill;
- any command help text.

### Orchestration skill

Must teach:

- route decision;
- static DAG creation;
- delegate/full → Native Workflow;
- main validation;
- high-assurance review.

### Enforcement skill

Reframe away from “proof-heavy enforcement platform”.

It should describe only:

- ownership scope;
- writer guard;
- change_id freshness;
- completion guard;
- visible degraded behavior if a guard cannot evaluate.

Rename the skill only if plugin compatibility and invocation ergonomics justify it; a rename is not required.

### Continuity skill

Describe:

- Workflow resume;
- waiting_quota;
- manual/auto bounded resume;
- native Scheduled Task wake.

Remove Global Clock and old Wake Bridge instructions from Conductor.

### Agents

Final expected custom agents:

- visual-implementer
- glm-reviewer
- visual-reviewer

No ordinary text flash-implementer.

## 5. Work package P4-D — Test-suite re-baseline

Before the full run, make the suite represent v2.4.

### Keep/add coverage for

- route matrix;
- DAG schema and cycles;
- compiler generation;
- ownership conflict serialization;
- repo writer guard;
- task state;
- change_id;
- validation/review freshness;
- completion guard;
- recovery;
- visual exception;
- provider quota resolution;
- bounded quota resume;
- plugin packaging/skill references.

### Remove tests whose subject no longer exists

Do not retain obsolete tests as skipped indefinitely.

A deleted subsystem should normally have deleted tests, with only historical review docs preserving its old behavior.

## 6. Work package P4-E — Final integration sequence

Run in this order:

1. targeted new-runtime integration suite;
2. plugin validator;
3. plugin smoke/load test;
4. live minimal delegate Workflow smoke;
5. live parallel/staged Workflow smoke;
6. live reviewer smoke;
7. live full-route smoke if practical;
8. live Scheduled Task resume smoke if practical;
9. **one full project regression**.

This is the only required full regression after the v2.4 implementation phases.

If the full regression fails:

- classify each failure as real v2.4 regression, stale legacy test, or environment issue;
- fix real regressions;
- remove/rewrite stale legacy tests;
- document genuine environment-only failures;
- rerun only the affected subset until clean, then rerun the full suite once for final confirmation.

Do not fall back into running the full suite after every individual fix.

## 7. Work package P4-F — Release-readiness audit

Confirm:

- normal text delegate/full has one execution substrate only;
- no duplicate unit runtime truth exists;
- reviewer model binding is host-portable;
- high-assurance reviewer remains read-only;
- ownership overlap cannot silently run in parallel;
- second active write Workflow in one repo is prevented;
- main validation is required;
- stale validation/review is detected by change_id;
- quota resume cannot exceed user-authorized budget;
- Global Clock code is absent from Conductor production graph;
- internal scheduler SQLite writes are absent from Conductor production graph;
- docs match code;
- package version/changelog identify v2.4.0 behavior.

## 8. Commit discipline

Prefer one coherent commit per major Phase-4 concern:

- retired-surface cleanup;
- docs/skills/metadata;
- test-suite re-baseline;
- final closeout evidence.

Do not create dozens of commits for tiny wording changes unless implementation tooling requires it.

## 9. Phase 4 exit gate

v2.4 is release-ready when:

- all Phase-1/2/3 exit gates remain true;
- repository-wide consistency audit has no unexplained production hits;
- targeted integration/validator/smoke tests pass;
- final full regression passes, except any explicitly documented external environment-only failure;
- current-truth docs and plugin metadata all describe v2.4;
- a closeout report records removed subsystems, retained semantics, test evidence and any residual host limitations.
