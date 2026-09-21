# v2.4 Phase 1 — Native Semantic Core Implementation Spec

> **Authority:** `docs/reviews/V2_4_NATIVE_WORKFLOW_REBASELINE_SPEC.md`  
> **Prerequisite:** Phase 0 exit gate closed in `ZCODE_3_14_NATIVE_WORKFLOW_RESULTS.md §13`.  
> **Goal:** establish the new delegated text path end-to-end: static DAG → ownership-safe compilation → ZCode Native Workflow → structured result, without using legacy dispatcher/wave/permit/lease mechanics.

## 1. Frozen outcomes

Phase 1 must end with these facts true:

1. ordinary text `delegate/full` work has a Native Workflow execution path;
2. Work Unit is a static semantic DAG node, not a durable runtime mini-task;
3. dependency code validates/compiles DAG structure but does not maintain readiness state;
4. parallel nodes are checked for ownership conflicts before launch;
5. one repository can have at most one active Conductor write Workflow;
6. Native Workflow run state is not mirrored into per-unit Conductor statuses;
7. reviewer Custom Subagents can launch on the current host without stale provider-qualified model bindings;
8. the legacy dispatcher path may still exist temporarily for rollback during this phase, but **new Phase-1 tests and code must not depend on it**.

## 2. Work package P1-A — Reviewer model-binding remediation

W0 found both retained reviewer agents fail on the current single-model host because their frontmatter pins unavailable provider-qualified model ids.

### Required change

Update:

- `plugins/glm-conductor/agents/glm-reviewer.md`
- `plugins/glm-conductor/agents/visual-reviewer.md`

Target behavior:

- reviewer definitions must not hard-code a provider-qualified model id that can make the role unstartable when that account/provider is not configured;
- prefer host/session model inheritance unless ZCode exposes a stable portable model alias that is already verified on the host;
- preserve the read-only tool allowlists and fresh-context role contract;
- do not silently claim that a stronger model was used when the actual host only exposes Flash;
- reviewer output may record the actual runtime model when useful for diagnostics, but model identity is not a new durable task-state axis.

### Acceptance

- both reviewer agent types can be launched on the W0 single-model host;
- their write/edit/Bash tools remain unavailable;
- `glm-reviewer` still emits the existing `GLM REVIEW` contract;
- `visual-reviewer` still emits the existing `VISUAL REVIEW` contract.

This is a portability fix, not a routing redesign.

## 3. Work package P1-B — Static Work Unit and DAG model

Rewrite `runtime/work_unit.py` around a static node shape:

```text
id
objective
depends_on[]
ownership[]
interfaces[]?
constraints[]?
local_check[]?
```

### Delete from the new public contract

- unit `status`;
- transition tables;
- attempts;
- results history;
- mandatory verification arrays;
- verification lifecycle;
- retry counters.

### Validation invariants

- `id`: non-empty stable string, unique within one DAG;
- `objective`: non-empty string;
- `depends_on`: list of node ids, no self-reference;
- `ownership`: non-empty normalized repository-relative scope list;
- optional arrays must contain strings and default to empty lists;
- `local_check` is optional and may be empty.

Do not retain compatibility setters that mutate removed runtime fields.

### `runtime/dependency.py`

Retain only pure DAG semantics:

- missing dependency detection;
- duplicate node id detection;
- cycle detection;
- deterministic topological order;
- downstream/ancestor helper only if the compiler uses it;
- deterministic level/stage derivation if useful for code generation.

Remove status-dependent readiness functions such as “deps completed → ready”.

## 4. Work package P1-C — Ownership-safe stage compilation

Use `runtime/ownership.py` as the semantic basis, but change its role from runtime lease enforcement to compile-time/final-scope semantics.

For every set of nodes eligible for the same parallel stage:

1. compare ownership scopes;
2. if disjoint, keep parallel;
3. if overlap is provable, serialize deterministically by adding/deriving an ordering edge;
4. if patterns are ambiguous enough that safe overlap cannot be decided, fail compilation with a clear ownership-conflict error rather than guessing.

### Important

Do **not** create a new per-node lock or lease to solve this.

Test at minimum:

- exact same file;
- parent/child path overlap;
- disjoint directories;
- glob overlap;
- ambiguous/invalid ownership pattern.

## 5. Work package P1-D — Native Workflow compiler

Create a dedicated package:

```text
plugins/glm-conductor/runtime/workflow/
    __init__.py
    compiler.py
    persona.py
    adapter.py
```

Names may vary slightly if repository conventions require it, but keep responsibilities separated.

### `persona.py`

Single source of truth for the ordinary text Workflow worker persona.

Migrate the useful semantics from `flash-implementer.md`:

- execute only the supplied bounded objective;
- respect ownership, interfaces and constraints;
- do not redesign architecture;
- surface ambiguity as a reassessment request;
- run optional local checks honestly;
- return structured implementation result.

Do not copy obsolete permit/receipt language.

### `compiler.py`

Input: validated canonical DAG + task context.

Output: deterministic TypeScript Workflow source or equivalent host-ready representation.

Must:

- preserve stable Conductor node ids in actor labels/instructions/results;
- express dependency levels using native `await` / `Promise.all`;
- include objective, ownership, interfaces, constraints, local checks in each ask;
- require structured result with at least:
  - node_id
  - status: complete | partial | blocked
  - changes
  - local_checks
  - reassessment
  - gaps
- aggregate all node results into one final Workflow return;
- make generated source inspectable/debuggable;
- never embed credentials/secrets;
- never include legacy permit ids or lease state.

### `adapter.py`

Thin host-facing correlation layer.

It may persist only:

- generated workflow artifact/reference if needed;
- `workflow_run_id`;
- mapping from Conductor node id to workflow site/ask id **when observable and useful**.

It must not mirror:

- child running/completed status;
- retry count;
- token ledger;
- background status;
- host health state.

Those stay host-owned.

## 6. Work package P1-E — Repository writer guard

Add a coarse repository-level writer reservation.

Required invariant:

> one Git repository → at most one active Conductor write Workflow.

The implementation may use a small durable record/lock under the Conductor runtime directory.

Required fields should be minimal, e.g.:

- repository identity/root;
- task id;
- workflow run id when known;
- created/updated timestamp only if needed for diagnostics.

### Recovery rule

Do not implement TTL/generation/heartbeat.

A reservation is releasable only when:

- the associated task is terminal; or
- the associated Workflow is known terminal and the main session explicitly reconciles/releases it; or
- the user/operator explicitly clears a stale reservation after inspecting the repository.

Never auto-expire a writer just because time passed.

## 7. Work package P1-F — Routing integration and new-path smoke

Retain route vocabulary:

- solo
- delegate
- audit
- full

For ordinary text work:

- solo/audit → main implementation;
- delegate/full → Native Workflow compiler.

Do not persist a separate ordinary-text executor field.

### Visual exception

Do not migrate `visual-implementer` in Phase 1.

Visual implementation remains on the existing Custom Subagent path as an explicitly isolated exception.

### Legacy path

The old `flash-implementer` / dispatcher route is not the target path. It may remain physically present until Phase 2 deletion, but:

- no new routing code should select it for ordinary text work;
- no new tests should require it;
- no compatibility abstraction should wrap both paths.

## 8. Files expected to change

Primary:

- `plugins/glm-conductor/agents/glm-reviewer.md`
- `plugins/glm-conductor/agents/visual-reviewer.md`
- `plugins/glm-conductor/runtime/work_unit.py`
- `plugins/glm-conductor/runtime/dependency.py`
- `plugins/glm-conductor/runtime/ownership.py`
- new `plugins/glm-conductor/runtime/workflow/*`
- routing/task entry points that select delegate/full execution
- focused tests for the above.

Secondary only if required:

- CLI/skill text needed to expose the new path during smoke testing.

Do not broadly rewrite state/provenance/quota yet; those belong to Phases 2/3.

## 9. Testing discipline

During implementation run only focused tests:

1. reviewer launch/tool-whitelist smoke;
2. Work Unit schema tests;
3. DAG/cycle/topological tests;
4. ownership-stage tests;
5. compiler deterministic-output tests;
6. repository writer guard tests;
7. one live minimal text Workflow;
8. one live two-node parallel disjoint Workflow;
9. one live staged dependency Workflow.

Do **not** run the full legacy suite.

## 10. Phase 1 exit gate

All must pass:

- reviewer roles launch under the current host configuration;
- delegate/full text route reaches Native Workflow;
- generated Workflow executes and returns structured node results;
- no dispatcher/wave/permit/lease is required for that execution;
- ownership-conflicting nodes cannot run in parallel;
- second write Workflow to the same repository is rejected/held by the coarse writer guard;
- no per-unit execution state is newly persisted.

At exit, record a short Phase 1 implementation report and the exact targeted tests run.
