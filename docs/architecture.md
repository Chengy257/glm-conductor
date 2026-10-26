# GLM Conductor Architecture (authoritative reference, v2.6.2)

> This document is the **single source of architectural truth** for GLM Conductor: it describes the **actual current** (v2.6.2) runtime behavior — every section answers "what the system is now" and does not narrate development history; removed modules are never described as "deprecated run paths" — they no longer exist (see §21 for the retirement map).
> One-line positioning: **GLM Conductor owns semantic orchestration and acceptance; ZCode owns execution orchestration.**
> Contract details defer to the plugin directory (the agents and skills under `plugins/glm-conductor/`); this document stays consistent with it, and on conflict the resolution is to fix back into consistency. For the conceptual introduction see [core-concepts.md](core-concepts.md); for troubleshooting see [troubleshooting.md](troubleshooting.md).

## 1. Positioning and responsibility split

GLM Conductor is a ZCode plugin that provides a lightweight semantic orchestration layer for the GLM dual-model setup (GLM-5.3 main session + GLM-5.3-Flash subagents). v2.4 drew the boundary with one rebaseline rule:

> **If ZCode already owns an execution fact, the Conductor must not model that same fact again. The Conductor persists only semantics the host does not know.**

| Conductor owns | ZCode owns |
| --- | --- |
| Routing policy | Workflow execution lifecycle |
| Normative implementation DAG (static nodes) | Sub-actor lifecycle |
| ownership semantics | Dependency execution machinery |
| Task objective / constraints | Parallel scheduling |
| Task-level acceptance | Retry / stop / resume machinery |
| High-assurance independent review | Workflow run observability |
| Terminal change freshness (change_id) | Background execution |
| Quota auto-resume authorization (bounded) | Scheduled Task activation substrate |

v2.4 is explicitly **not**: a second task runtime, a Workflow engine, a permission engine, a provenance system, a scheduling platform, or a quota control plane. The main session (GLM-5.3) is always the sole orchestrator: requirement disambiguation, routing judgment, DAG spec authoring, validation and acceptance, review dispatch; delegated implementation goes through the ZCode Native Workflow — the main session never re-implements any execution machinery.

## 2. Routing matrix

Routing keeps two independent axes: answer each first, then consult the matrix; `executor` is no longer a separate routing dimension for ordinary text work — `solo/audit` means main-session-carried implementation (v2.8.0: split by task length — small tasks directly in the main session, long low-delegability tasks as a direct-dispatch chain of bounded blocks to conductor-impl-flash / conductor-impl, one `chain_block_delegated` journal event per block), `delegate/full` means Native Workflow implementation (the visual exception is §13).

- **Delegability**: is the remaining implementation sufficiently bounded and the spec sufficiently complete? objective / file boundaries / interfaces / constraints / verification all explicit and the architecture settled → high; unsettled architecture, unknown root cause, substantive ambiguity, judgment-heavy → low
- **Assurance**: after main-session validation, does a fresh-context independent final review add substantive value? limited blast radius, contained regression risk → standard; wide blast radius, high regression risk, destructive behavior, large user-visible change → high

| Delegability | Assurance | Route | Implementation | Independent review |
| --- | --- | --- | --- | --- |
| low | standard | `solo` | GLM-5.3 main session | no |
| high | standard | `delegate` | Native Workflow | no |
| low | high | `audit` | GLM-5.3 main session | yes |
| high | high | `full` | Native Workflow | yes |

The main session outputs one SELECTIVE ROUTE declaration before any orchestration action (four fields + an evidence-based reason):

```
SELECTIVE ROUTE
mode: solo | delegate | audit | full
delegability: low | high
assurance: standard | high
reason: <concise, evidence-based reason>
```

A route change must come from newly observed evidence and may be reassessed in both directions (ROUTE REASSESSMENT) — a reassessment request returned by an implementer in NodeResult.reassessment and a reviewer's rethink verdict are both valid reassessment evidence; without new evidence the route must not change, and no route may be bumped up or down for convenience:

```
ROUTE REASSESSMENT
delegability: <old> -> <new>
assurance: <old> -> <new>
mode: <old> -> <new>
evidence: <newly observed evidence>
```

## 3. Static DAG

In v2.4 the Work Unit degenerated into a **static DAG node declaration** (`runtime/work_unit.py` as the pure data layer + `runtime/dependency.py` as the pure graph validation layer): a node describes "what to do" and carries no "how far execution has gotten". Per-unit runtime semantics — status / attempt / result history / unit validation / unit receipts / unit leases / dispatch permits / unit lifecycle events — do not exist at all.

Node shape (required: `id` / `objective` / `depends_on` / `ownership`; optional: `interfaces` / `constraints` / `local_check`):

```yaml
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
```

- **Dependency graph**: `depends_on` references within the same DAG; `graph_errors` aggregates duplicate ids / missing dependencies / self-dependencies / cycles (reporting every cycle member); `topo_order` / `stage_levels` give a deterministic topological order and level assignment (same input, same output, zero runtime-state reads)
- **local_check is optional**: merely a cheap node-health gate for when downstream dependencies would otherwise be unsafe, not durable provenance; pure-documentation or dependency-free nodes may omit it. A worker's local checks only establish "this node is healthy enough for its dependents to keep implementing" and never replace main-session task-level acceptance
- The default unit of delegated execution is **one fully delegatable DAG per Native Workflow run** (typical projection: stage 1: A‖B → stage 2: C‖D → stage 3: E), not repeated ad-hoc launches in "waves":

```
(DAG) A ─┬─→ C ─┐             (generated Workflow stage projection)
       B ─┴─→ D ─┴─→ E        Stage 1: A, B (parallel)
                              Stage 2: C, D (parallel)
                              Stage 3: E
```

## 4. Workflow compiler boundary

The main implementation path: spec DAG → Workflow compiler (`runtime/workflow/compiler.py`) → generated ZCode Native Workflow source → host-native execution (parallelism / background / retry / resume all belong to the host).

The compiler's fixed pipeline, `compile_workflow(nodes, task_context)`:

1. Validation: per-node `work_unit.validate_node` + whole-graph `dependency.graph_errors` + `task_context` (at least the three non-empty strings `task_ref` / `goal` / `repository`); errors aggregate into a raised `WorkflowCompileError`;
2. Stage planning: `ownership.plan_stages` (§6) — ownership ambiguity / illegal patterns propagate verbatim as `OwnershipConflictError` (conflict means refusal, never guessing); candidates with explicitly overlapping scopes are deterministically serialized into later groups;
3. Per-group generation: each group gets one `phase(name)` marker + one `await Promise.all` (parallel within the group, exactly one `agent("node-<id>", persona).ask<NodeResult>(instruction)` per node); between groups are sequential top-level awaits;
4. A trailing `return` aggregates all node results in ascending node id order.

Generated-source contract: deterministic (no timestamps, no randomness; two compilations of the same input are byte-identical); node ids appear verbatim in actor labels, ask instructions, and the results map; the single authoritative source of the persona is `runtime/workflow/persona.py` (the host runtime does not consume plugin agent definitions, so role simulation must inline the persona text into the script); the result interface `NodeResult` has exactly six fields `node_id / status("complete"|"partial"|"blocked") / changes / local_checks / reassessment / gaps`; the generated source is free of permit / lease vocabulary (anchored by the forbidden-word scan).

**The compiler does not do** (these are the host Native Workflow's responsibilities; the Conductor does not rebuild them): ready queues, dispatch waves, concurrency schedulers, background managers, retry managers, sub-actor lifecycle ledgers, ask replay, Workflow resume engines. The compiler only generates source and only makes static assertions — **it never starts any Workflow**; execution is initiated by the main session through the host's CreateWorkflow (CLI entry `v24-compile`: it only produces code or writes it to disk).

**The compiler is TypeScript-model-agnostic (explicit in v2.4.1)**: the generated source neither carries nor sets a worker model — `subagent_model` is a run-level submission property bound by the host at CreateWorkflow submission time. Worker model choice can therefore only happen at the submission boundary: the main session goes through the host model list → `workflow-model-select` (`runtime/workflow/submission.py`, INV-MODEL-01; since v2.6.0 selection requires explicit user authorization — all candidates role-neutral, the Flash tier demoted to a soft recommendation, and even a single candidate requires explicit user confirmation) and then submits with CreateWorkflow; omitting `subagent_model` makes the run inherit the main-session model (the v2.4.0 defect A), which the Conductor protocol lists as hard-forbidden.

Generated source shape (excerpt for illustration; the compiler output is authoritative):

```typescript
interface NodeResult { node_id: string; status: "complete" | "partial" | "blocked";
  changes: string[]; local_checks: string[]; reassessment: string; gaps: string[]; }
const persona: string = /* TEXT_WORKER_PERSONA from runtime/workflow/persona.py, verbatim */;
const allResults: { [nodeId: string]: NodeResult } = {};

// == Stage 1: parser, types ==  (plan_stages judged the scopes disjoint: parallelizable)
phase("Stage 1: parser, types");
await Promise.all([
  agent("node-parser", persona).ask<NodeResult>(/* build_ask instruction */)
    .then((result: NodeResult): void => { allResults["parser"] = result; }),
  agent("node-types", persona).ask<NodeResult>(/* ... */)
    .then((result: NodeResult): void => { allResults["types"] = result; }),
]);
// ...subsequent stages are sequential top-level await statements...
return [allResults["parser"], allResults["types"] /* ascending node id order */];
```

## 5. Host-owned execution state

v2.4 has two truth domains: **static semantic truth** (Conductor task/DAG state) and **execution runtime truth** (the ZCode Workflow run). The Conductor persists no per-node run state (running / completed etc.) — that is the host `GetWorkflowRun`'s own fact.

The only persistent association is the run-id record in `runtime/workflow/adapter.py`: `record_run(repo_root, task_ref, workflow_run_id)` writes the "task ↔ Workflow run" association as a single JSON record under `.glm-conductor/workflow-runs/` of repo_root (an explicit repo_root parameter since v2.6.0, with the legacy CWD basis as the compatible default; re-writing the same task replaces the whole record — one active run per task; optional `artifact_path` / `node_map`), and `load_run` reads it back. The canonical CLI entry is `task-record-workflow-run` (AF-04 writer-guard precondition, repo_root ledger from any CWD, zero fake events on failure); `v24-record-run` is the legacy low-level thin shell. **Zero state mirroring red line**: sub-actor running/completed states, retry counts, token accounting, background task states, host health — none of it is ever persisted. The task-side counterpart is `state.workflow_run_id` (null before a delegated launch), persisted by `runtime.task.record_workflow_run` + mirrored to the adapter + a `workflow_started` event.

```json
// .glm-conductor/workflow-runs/run-<percent-encoded task_ref>.json
{ "task_ref": "redesign-settings-page-7f3a2c",
  "workflow_run_id": "<run id returned by the host CreateWorkflow>",
  "created_at": "2026-09-21T08:30:00.000Z" }
```

The main session's delegation launch order is fixed as: **the task launch interview** (v2.5.2 three questions; v2.7.0 W2 adds a fourth: every task that will create state must pass one mandatory single-round interview merging four questions before create_task (the third question is conditional on enabling auto; the fourth is asked only for delegate/full tasks with CreateWorkflow submissions and is independent of the first) — (1) cross-quota automatic wake and the bounded `max_resumes` budget: answering "enable" first persists `quota-resume-authorize` and then walks the arming sequence below; (2) the worker model (asked whenever there is any implementation delegation: the full host candidate list is shown with explicit confirmation, even for a single candidate; not asked for solo and other delegation-free tasks), and the answer is persisted as `worker-model-pin`; (3) the floor carrier interval; (4) workflow submission auto-approval (task-level `auto_approve_workflow` via `task-create --auto-approve-workflow`; consumed by the PermissionRequest confirmation-free hook for path-type submissions only); the interview happens only in interactive turns — wake rounds / resume rounds never pop questions; contract in orchestration §6.0) → create task → `v24-compile` produces the source → **the worker-model selection hard gate** (host model list → `workflow-model-select` selects the provider-fully-qualified exact id the user explicitly authorized (any tier; Flash is the soft recommendation); with `--task-ref <task_id>` the value resolves by fixed priority: task pin > explicit `--model` value (must equal the pin) > Flash soft-recommendation auto-selection (bare invocation only); no pin and no `--model` refuses into the interview; an invalidated pin is specifically refused — see §14; mode=auto tasks must also first complete continuity arming: create the Scheduled Task (kind-aware floor establishment since v2.7.0 W1 — the interval is common to both carriers, while the substrate follows the availability matrix: L0 host CronCreate when the host tool exists; otherwise `wake-carrier-probe` → L1 `os-carrier-install`, L2 sleep-only binding per the continuity runbook) → `quota-automation-bind` → `quota-continuity-preflight` PASS) → `writer-acquire` takes the repository write reservation → host CreateWorkflow (explicit `subagent_model`) starts the run → `record_workflow_run` (state.workflow_run_id + adapter record + journal event). A run's mid-flight states (in progress / stuck on ask / finished) are always observed through the host's ListWorkflowRuns / GetWorkflowRun and never enter the Conductor ledger.

## 6. ownership and compile-time conflict handling

Concurrent host writes to the same file are silent last-writer-wins (measured in W0); compile-time checking is the only conflict barrier; v2.4 uses two levels of ownership discipline with no runtime locks.

**Path matching semantics** (`runtime/ownership.py`, shared by the completion guard and compile time): three declaration forms — exact file (`src/auth.ts`), directory prefix (a bare path `src/auth` equals `src/auth/**`, matched by path segments), segment-level glob (`**` crosses segments; `*` / `?` do not). Implicit expansion is rejected: `src/auth` does not cover `src/authentication.ts`; `*.py` does not match `sub/x.py`.

| Declaration | Matches | Does not match |
| --- | --- | --- |
| `src/auth.ts` | `src/auth.ts` | `src/auth.ts.bak` |
| `src/auth` (= `src/auth/**`) | `src/auth/a.ts`, `src/auth/x/y.ts` | `src/authentication.ts` |
| `*.py` | `a.py` | `sub/x.py` (`*` does not cross segments) |
| `a/**/b.ts` | `a/b.ts`, `a/x/b.ts`, `a/x/y/b.ts` | — |

**Compile time (before Workflow launch)**: `plan_stages(nodes)` deterministically plans parallel stages whose in-group scopes are pairwise disjoint — static validation first, then the levels from `dependency.stage_levels` (cross-level dependencies stay authoritative); within a level, greedy bin-packing in ascending id order into the first group with no overlap, opening a new group when nothing fits (i.e. deterministic serialization). Two failure classes are rejected at compile time with `OwnershipConflictError`: an illegal pattern; an ambiguous pattern (semantically matching the repository root or all paths, such as `**` / `*` — an ownership declaration covering everything loses its discriminating meaning). Explicitly overlapping scope is not an error — it gets serialized, never guessed. No locks, no leases, no runtime reservations.

```
(two same-level candidate scopes overlap → deterministic serialization;
 the overlap test is a complete path-language intersection test)
Node authz (ownership: src/auth/**)        ┐  scopes_overlap("src/auth/**",
Node login (ownership: src/auth/login/**)  ┘   "src/auth/login/**") == True
→ planned as two groups in the same level: stage i: authz → stage i+1: login (group order is execution order)
```

**Task-level terminal check (before acceptance)**: `actually changed paths ⊆ union(DAG ownership)` — enforced by completion-guard check 2 (§12), a task-level check rather than a per-node completion gate.

## 7. One active write Workflow per repository

v2.4 deliberately chooses the coarse-grained repository-level write invariant (replacing v2.3's per-unit lease system):

> **A Git repository has at most one active Conductor write Workflow at any moment.**

The implementation is `runtime/writer_guard.py`: a single persistent reservation record `.glm-conductor/writer_guard.json` inside the repository (exactly four fields `repo_identity / task_id / workflow_run_id / created_at`); the public surface is only the three functions `acquire` / `release` / `inspect`.

- **Never auto-expires**: no TTL / generation / heartbeat / time-based expiry logic anywhere in the code. `created_at` is diagnostic only — the guard's judgment never reads it; a leftover record conflicts no matter how old. Recovery has exactly two paths: the associated task reaching a terminal state (`task.complete` / `fail` / `cancel` auto-release) or the operator explicitly running `writer-release --force` after a `writer-show` inspect
- **Concurrency-safe**: read-modify-write always goes through `durable_io.atomic_update_json` (exclusive lock + atomic replace); of two concurrent acquires, one necessarily sees the other's persisted holder; a repeated acquire by the same task is idempotently successful; on conflict, zero writes and the holder's `task_id` and `workflow_run_id` are reported
- **Lifecycle enforcement (AF-04)**: the writer guard is a Conductor lifecycle invariant, enforced at both delegated-run registration and completion — `record_workflow_run` (delegate/full) requires the current task to hold the write reservation (missing / held by another → ValueError), and after a successful persist it idempotently attaches the run id to the reservation record; completion-guard check 1 likewise blocks a task with a registered delegated run (non-empty `workflow_run_id`) when the guard is missing (missing writer reservation). A bare host Workflow call outside the protocol (a CreateWorkflow that bypasses the Conductor lifecycle) is outside this guarantee — the guard does not claim to intercept every CreateWorkflow
- The authoritative release key is `task_id` (`workflow_run_id` is diagnostic only); release by a non-holder is refused and another holder's reservation is never deleted on their behalf; parallelism inside a Workflow is allowed after compile-time ownership validation — the guard constrains a **second write Workflow on the same repository**

## 8. Task state (seven states)

The task state file is `<ledger root>/.glm-conductor/tasks/<task-id>/state.json` (`runtime/state.py`; the sibling `events.jsonl` in the same directory is an append-only journal). `task_id` is a mechanically unique identifier: semantic prefix + random hex suffix (format constraint `^[A-Za-z0-9][A-Za-z0-9-]*$`).

The durable status is exactly seven states: `active / waiting_quota / waiting_user / blocked / completed / cancelled / failed`. Terminal = the last three, and **terminal states are never silently reopened** (`TASK_TRANSITIONS` has no terminal-state entries; explicit reopen goes only through `reopen_task`, which records a `task_reopened` event). Legal transitions:

```
active          → waiting_quota | waiting_user | blocked | completed | cancelled | failed
waiting_quota   → active | waiting_user | blocked | failed | cancelled
waiting_user    → active | blocked | failed | cancelled
blocked         → active | waiting_quota | waiting_user | failed | cancelled
```

Top-level schema (enforced by `validate_state`): `task_id / goal / repository{root required} / route{mode required, assurance optional} / dag / status / workflow_run_id / validation / review / quota_resume`; exactly two optional keys: `phase` (`planning / workflow / validating / reviewing` — a purely descriptive annotation, no transition matrix) and `worker_model` (the v2.5 task-level worker model pin — validated only when present, as null or a non-empty string; absent by default, zero migration for old tasks; written by `task.pin_worker_model`, consumed by `workflow-model-select --task-ref`, see §14). `validation` / `review` each hold a single task-level record (§9 / §11), no per-unit evidence.

```json
// state.json example (delegated high-assurance task with the delegated run started)
{ "task_id": "redesign-settings-page-7f3a2c",
  "goal": "…",
  "repository": { "root": "C:\\work\\demo" },
  "route": { "mode": "full", "assurance": "high" },
  "dag": [ { "id": "parser", "objective": "…", "depends_on": [],
             "ownership": ["src/parser/**"], "interfaces": [],
             "constraints": [], "local_check": [] } ],
  "status": "active", "phase": "workflow",
  "workflow_run_id": "<run id>",
  "validation": { "status": null, "change_id": null,
                  "summary": null, "commands": null },
  "review": { "reviewer": null, "verdict": null,
              "change_id": null, "findings": null },
  "quota_resume": { "mode": "manual", "max_resumes": 0,
                    "resume_count": 0, "automation_id": null } }
```

Task-discovery completeness: `discover_tasks` classifies every subdirectory of tasks_root into four buckets (active / terminal / corrupt / orphaned) — a corrupt state.json is never interpreted as "no tasks"; corrupt and orphaned are explicitly reported (one stderr line from the completion guard, then skipped, not participating in interception). v2.3 legacy states are recognized only to be declared incompatible: `is_legacy_state` / `detect_legacy_task` detect the `work_units` / `permits` / `leases` / `receipts` set keys and markers such as the v2.3 phase-state status, and on a hit report per `LEGACY_STATE_GUIDANCE` (§18).

`repository.root` is a required repository-root binding (`bind_repository_root` normalizes it to an absolute path): all of a task's git evaluation (the touched list, change_id) happens on the effective root — binding first, ledger-root fallback (`resolve_repository_root`); the ledger always stays at the ledger root. `.glm-conductor/` is a local runtime ledger: it does not count as repository changes (both touched evaluation and change_id exempt this directory) and is preferably excluded locally via `.git/info/exclude`.

## 9. change_id: the single freshness primitive

`runtime/change_id.py`'s `compute_change_id(repo_root, relevant_files)` deterministically compresses "baseline revision + the normalized content state of the relevant file set" into `"sha256:" + 64 hex characters`. The inputs are repo-relative **literal file paths** (not ownership scope strings; this function does not expand scopes — resolving scope → the actually-changed file set is `task.relevant_changed_files`'s job). Semantics: any relevant-file addition / deletion / content change / change of the relevant file set / baseline change alters the identifier; CRLF→LF normalization (the same logical content does not change the identifier because of line-ending style); `.glm-conductor/` bookkeeping paths excluded (the orchestrator writing its own ledger must not make itself stale); normalized dedup + sorting (independent of input order); an empty relevant set still yields a stable identifier.

**Single-implementation red line**: the same function is shared by three call sites (bypass recomputation forbidden) — main-session validation recording (`validation.change_id`), review recording (`review.change_id`), and the completion guard's freshness comparison (§12). The derivation of the relevant changed file set is likewise shared by both sides: `task.relevant_changed_files(task_state, repo_root)` = the ownership scope union (`task.ownership_scopes`) ∩ actual git working-tree changes (`ownership.git_touched_files` + `classify_paths`) — the recording side and the guard side both call this derivation, which is what makes the identifiers comparable. Additions / deletions / modifications / renames / set changes of owned files all alter the identifier; out-of-scope changes are still intercepted by the ownership completion check and are irrelevant to freshness; `.glm-conductor/` bookkeeping is excluded at both the touched and the change_id layers. Any change to an owned relevant file stales the existing validation / review records: "any fix invalidates prior validation / review" holds automatically; the only recovery path is re-validating / re-reviewing and recording the new values — writing back old values to "extend life" is forbidden.

## 10. Main-session validation

After the Workflow ends, the GLM-5.3 main session must personally complete the main validation:

- Check the accumulated diff for task scope and interface preservation;
- Run a **finite, whole-task-targeted** set of integration / build / test commands — never mechanically re-running every worker's local_check (worker local checks are only node-health gates, §3);
- Judge whether the user goal is met and record `passed | failed` via `runtime.task.record_validation` — the record embeds a freshly computed change_id (§9), with the optional human-readable `summary` / `commands`.

The validation record is declaration + freshness anchoring; it requires no stdout capture, no runner identity, and no per-command evidence (v2.3's durable verification receipt system no longer exists). A report is only a declaration: without the diff and command output personally observed by the main session, a completion claim is invalid. `record_review` requires a complete validation record first (validate before review, §11).

## 11. Reviewer path

`assurance=high` (routes `audit` / `full`) adds one fresh-context independent review (a fresh-context read-only review) **after main-session validation**. The reviewers are native Custom Subagents — `glm-reviewer` (text, `GLM REVIEW` verdict) and `visual-reviewer` (visual, `VISUAL REVIEW` verdict) — and their independence is a fresh context + the host-enforced read-only tool whitelist (Read / Glob / Grep / LS / NotebookRead / WebFetch / WebSearch); reviewers never implement fixes.

- **Five input essentials**: ROLE / STATED GOAL / ACCUMULATED CHANGE SET / INTERFACES AND CONSTRAINTS / VERIFICATION EVIDENCE — if any is missing, ask for it instead of guessing
- **Verdict vocabulary**: `ship / fix-first / rethink`; after fix-first / rethink fixes, main validation must refresh first (a changed diff always changes the change_id), and re-review must use a **brand-new reviewer** — an old verdict is never revived by fixes
- **Minimal persistence**: a single task-level review record only (`runtime.task.record_review`; CLI `review-record`): `{reviewer, verdict, change_id, findings?}` + a `review_recorded` event. v2.3's review-invocation authenticity chain, tool-use ID proofs, receipts directories, runner identity, and the journal/state dual mirror all do not exist
- **Model binding**: text/visual reviewers split (v2.4 AF-02; 2026-10-26 ruling) — the `glm-reviewer` frontmatter carries **no model key**: the reviewer follows the session's own model on any host (the intervening v2.8.0 plan-tier literal pin rotted within days when its provider left the host model list while the role kept launching through unverifiable silent routing, so the literal-pin form was retired); the `visual-reviewer` contract requires reading images directly, so its frontmatter **must explicitly bind** a provider-qualified multimodal `GLM-5.3-Flash` (inheriting the text-only main model launches successfully but loses the image-reading contract capability); on hosts where the binding is unavailable, the visual high-assurance route fails closed

## 12. Completion guard (four checks)

Completion is an explicit, guarded task-level operation. `hooks/stop_gate.py` hangs on the Stop event and runs the **four checks** against tasks in the trigger domain (the same judgment is importable as `evaluate_completion(repo_root, task_id)`); on any failure it outputs one line of block JSON (exactly one, most-severe, actionable English reason); when all checks pass, the guard executes `runtime.task.complete` in place (`completed` + releasing the writer guard + a `task_completed` event) and allows the stop.

Trigger domain: v2.4 tasks with `status == "active"` in the active bucket of the ledger root's `discover_tasks`. Parked states (waiting_quota / waiting_user / blocked) are intentional stopping points and are always allowed through; terminal tasks have nothing to evaluate; v2.3 legacy tasks are allowed through with one line of handling guidance (never pretending completion can be judged); corrupt / orphaned directories do not participate in interception, only one stderr line each; with no active task the guard allows silently. v2.3's `finalizing` completion-request state does not exist — wrap-up happens on the guard's all-green path; there is no intermediate "requesting completion" state.

The four checks (in order, first failure returns; the evaluation root = the effective repository root from `resolve_repository_root`):

```
1. writer_guard : no other task's active write reservation on the repository (self-holder passes — the completion gate is the release point);
                  a registered delegated run (non-empty workflow_run_id) with a missing guard is blocked as well
                  no → block: held by another — report the holder's task_id and workflow_run_id verbatim;
                  guard missing — missing writer reservation (naming the task and the run id)
2. ownership    : actually changed git paths ⊆ the ownership union of all dag nodes
                  no → block: out-of-scope paths listed one by one + the node ids that took part in the comparison
3. validation   : validation.status == "passed" and validation.change_id == the current change_id
                  no → block: no passed record, or the record is stale (both change_ids reported)
4. review       : when route.assurance == "high", review.verdict == "ship"
                  and review.change_id == the current change_id
                  no → block: no ship verdict (fix-first / rethink never satisfies completion), or stale
```

Companion semantics: structural errors during evaluation (`ChangeIdError` for non-git repositories, `OwnershipError` for illegal scope patterns, etc.) degrade per task and allow (reported on stderr — an infrastructure failure is not completion evidence); a crash of the hook itself fails open (never blocks the session); this hook writes no journal events (the only write comes from `task.complete`). Out-of-scope changes are not prevented from happening (Workflow sub-actors do not trigger hooks, §20), but they cannot silently pass the completion guard.

The host contract for the block decision is single-line JSON on stdout (ZCode uses it to ask the model to continue; the payload is the continuation instruction):

```json
{"decision": "block", "reason": "Completion blocked: the validation record is stale — task-relevant files changed again after validation (task redesign-settings-page-7f3a2c).\n- Recorded change_id: sha256:…\n- Current change_id: sha256:…\nRe-validate and refresh the record via runtime.task.record_validation, then request completion."}
```

The task-level journal event vocabulary (exactly eleven names, `TASK_JOURNAL_EVENTS` / `journal.RECOMMENDED_EVENTS` aligned): `route_selected` (create_task) / `workflow_started` (record_workflow_run) / `chain_block_delegated` (record_chain_block — a direct-dispatch chain block delegation, the main session's semantic decomposition decision, v2.8.0) / `workflow_reassessed` (the declared vocabulary name; the main session records a route reassessment) / `validation_recorded` / `review_recorded` / `waiting_quota` (enter / exit) / `quota_resume_confirmed` (the resume count committed) / `task_completed` / `task_failed` / `task_cancelled`. Event names outside this vocabulary do not exist.

## 13. Visual exception

`visual-implementer` is a **capability exception**: it does not go through the Native Workflow worker channel and keeps using the Custom Subagent channel (keeping the host-enforced multimodal model binding and read-write tool surface) until Native Workflow is separately proven to preserve the multimodal / screenshot-feedback contract. This exception must not become a second general-purpose text implementation path — ordinary text `delegate/full` has exactly one implementation substrate, Native Workflow (v2.3's flash-implementer text executor is deleted, §21).

The visual feedback loop spans multiple invocations (Browser Use / Computer Use are main-session-only; subagents cannot drive a browser / desktop):

```
visual-implementer invocation (implements code changes)
        ↓ visual evidence needed → return VISUAL_CAPTURE_REQUEST; this invocation ends
Main session captures a screenshot to disk
        ↓
New visual-implementer invocation (carrying full state: spec, diff, last round's request, screenshot paths, VISUAL_ROUND)
        ↓ the implementer Reads the screenshots itself and judges against VISUAL ACCEPTANCE
Pass → FUNCTIONAL VERIFICATION → report; not met → fix and issue VISUAL_CAPTURE_REQUEST again
```

The canonical path is "the new invocation": each visual round is self-contained and correctness never depends on subagent context retention (resume is an optional optimization only). Every acceptance point is tracked by `VISUAL_ROUND` (1|2|3), with at most 3 capture-judge loops; beyond that it goes blocked back to the main session for reassessment; the implementer never waits, never idles, never fabricates screenshot results; when screenshots are unobtainable it fails closed (the visual channel stops; delivery must not be downgraded to text-only verification). Visual tasks with `assurance=high` get a `VISUAL REVIEW` visual final review by a fresh-context `visual-reviewer` (§11).

## 14. Minimal quota resume

Quota continuity converges to the minimal task-level capability the Workflow itself does not provide: **bounded, authorization-gated quota resume (quota_resume)**. No epochs, no subscriptions, no activation bookkeeping, no boundary consumption proofs, no resident observation processes — these v2.3 concepts do not exist in the Conductor.

The state top-level `quota_resume` block (frozen initial shape `{mode: "manual", max_resumes: 0, resume_count: 0, automation_id: null, automation_kind: "native"}` — the v2.7.0 W1 fifth key carries the wake-carrier form label (vocabulary `native / os_timer / sleep_only`, defaulting to `native`; a state file predating the fifth key loads with zero migration)):

- **mode**: `manual` (default) / `auto`. auto needs no extra fields — authorization is a persisted record from an explicit `authorize_quota_resume(repo_root, task_id, max_resumes)` call (requires the task to be waiting_quota or active; `max_resumes >= resume_count`), never inferred. `max_resumes=1` is a one-shot resume; `max_resumes=N` provides bounded multi-window continuation
- **Budget invariant**: `resume_count <= max_resumes` (enforced by `validate_state` on save)

The waiting and resume paths (the `runtime/task.py` lifecycle API):

```
Quota exhausted → enter_waiting_quota (status=waiting_quota; workflow_run_id retained;
           optionally records the latest normalized observation status/reset_at/observed_at
           into last_observation for diagnostics)
        → a native ZCode Scheduled Task provides the future execution opportunity (host action)
Scheduled rounds → scheduled_activation_decision(task_id, quota_view) idempotent decision, fixed order:
  1. not waiting_quota → no-op (a duplicate wake is safe, zero writes)
  2. observation EXHAUSTED / UNKNOWN → remain-waiting (never fabricate availability)
  3. mode not auto → waiting-user (wait for explicit user authorization)
  4. budget exhausted → waiting-user, with exactly one transition to waiting_user
     (idempotent: another wake is a no-op)
  5. v2.4.1 continuity gate (INV-CONT-01): mode=auto and automation_id empty →
     remain-waiting (reason contains continuity-unarmed) — zero state writes,
     zero budget consumed; a missing arm is an operational-liveness failure,
     not authorization exhaustion, so it does not move to waiting_user
  6. available (AVAILABLE/PRESSURE) + authorized + budget remaining + armed →
     resume-authorized (with workflow_run_id; resume_count_after is only staged
     in the return value — never written to disk)
        → after the host resume call is actually accepted → confirm_resume_started:
           resume_count +1 (the only commit point) + the task returns to active
           (phase=workflow) + a quota_resume_confirmed event — an unconfirmed
           decision never consumes budget
After the budget is exhausted the task stays at waiting_user for the user; no further automatic actions
```

**Arm before work (v2.4.1, arm-before-work)**: authorization and arming are two independent facts — `authorize` is the user's permission, `bind` is the witness of a future activation, `preflight` is the mechanical confirmation that both are in place. A mode=auto task must complete arming before the first quota-consuming Workflow launch and before any automatic ResumeWorkflowRun (`auto_continuity_armed = mode==auto AND automation_id is a non-empty str`). Three task-level APIs (`runtime/task.py`): `bind_quota_automation` (status restricted to active/waiting_quota/waiting_user; same id idempotent, a different id refused without silent replacement; binding never infers mode=auto), `clear_quota_automation` (exact reconciliation; clears only automation_id and never touches mode/budget; cleanup is allowed in terminal states), `quota_continuity_preflight` (a pure-read three-state judgment; it proves only that the persisted binding exists, never that the host-side activation is still alive — host existence must still be checked against the host surface at the resume boundary). CLI surface: `quota-automation-bind` / `quota-automation-clear` / `quota-continuity-preflight` (ready=false reported with exit code 2 = a launch/resume blocking signal). Scheduling policy (v2.5.2 dual channel): the recurring Scheduled Task is the **only arming substrate** — created in a clean session state right after launch-interview authorization (default hourly with a 1h floor; the interval is confirmed by the launch interview's third question), never touched again after creation (three-state rule: after the substrate's first fire, every Cron change in this session is void, so after a successful wake it is never deleted, modified, or rebuilt, and is kept until terminal cleanup / explicit cancellation, INV-CONT-03); precision is carried by the orthogonal **background sleep layer** (each wake round ends by re-arming at the earliest valid future `reset_at` of the identified window (the v2.6.0 single-observation basis, independent of quota status) + grace; the create/re-arm action always precedes work advancement — full runbook in the continuity skill's "dual-channel wake" section); v2.5's one-shot successor chain was deleted wholesale with the dual-channel design.

**v2.5.2 dual-channel wake and the task-level pin**: the six-step decision chain stays verbatim. Two extensions:

- **The decision output unconditionally passes through `reset_at` (I4/W3 → the v2.6.0 single-observation basis)**: `quota-resume-decision` unconditionally attaches the decision `reset_at` (ISO string or null) for **all actions** (no-op / remain-waiting / waiting-user / resume-authorized) — the earliest valid future observation-reset instant of the identified window (UTC instants parsed and compared explicitly, independent of quota status — precautionary arming: build the wake regardless of how much quota remains; the conservative EXHAUSTED basis inside `parser.plan_resume` is kept and not exposed) — for the main session to convert the precision-layer sleep target from the sleep template (`reset_at` + 300s grace; the bash template computes dynamically, zero model arithmetic); pure diagnostic pass-through that participates in no judgment; the observation face and the decision face share the same source and the same batch (`_observation_view_of`), forcing exactly one refresh when the cache is the only source and no future reset exists, and honestly disclosing the floor-only degradation otherwise — never fabricating. Wake architecture (v2.5.2 dual channel; host fact evidence in `docs/reviews/WAKE_TOPOLOGY_PROBE_RESULTS.md`): the **floor** = the recurring substrate created at launch (survives app restarts; never touched after its first fire — the three-state rule voids post-wake changes, so the no-substrate-changes ban is satisfied naturally); the **precision layer** = background sleep (exiting automatically re-invokes the session, measured second-level precision + an 8-11s delay; it does not survive app restarts — the floor backstops it and the first floor wake re-arms it — a self-healing loop). Both trigger sources follow the same unified wake protocol (the active branch includes a run health check; a stalled run is handled as a resume). The one-automation-per-session host constraint (live-tested 2026-10-03, message `Cannot create a scheduled task inside a session that already belongs to a scheduled task`): the substrate must be built in one shot while the session is clean (at launch), and afterwards there are never any substrate mutation actions; boundary written plainly (D8): the main session has at most one auto task at a time. Terminal cleanup written plainly (D7): the substrate deletion is attempted once and expected to fail (the three-state rule) → the automation id is reported honestly → the user deleting it manually is the expected operational action → after confirmation, `quota-automation-clear`.
- **The task-level worker model pin (I1) lives for the whole task lifecycle**: written by `task.pin_worker_model` (CLI `worker-model-pin`; shape pre-checked via the submission shape convention but **never queries the host surface** — the host surface at write time and at submission time may differ, and reconciliation is left to the consumption point; writable only in non-terminal states, same-value idempotent, different-value explicit overwrite with prev/new; no new journal event). New run submissions in wake/resume rounds pass the same selection hard gate: `workflow-model-select --task-ref` consumes the task top-level `worker_model` pin and **always checks** the current configured set — an invalid pin shape or a pin not in the list is specifically refused (exit code 2), never falling back to auto-selection, never silently switching models; an unattended round with an invalidated pin always blocks the submission and moves to waiting-for-user, never popping questions (fail closed; contract in orchestration §6.0 / §6.1).

The observation vocabulary (`QUOTA_VIEW_STATUSES`) is exactly the four states `AVAILABLE / PRESSURE / EXHAUSTED / UNKNOWN`; UNKNOWN is never treated as available. Quota resume does not change routing (quota decides "when", not "who does it").

Typical timeline (auto authorized + `max_resumes=1`):

```
User authorize_quota_resume(max_resumes=1) → mode=auto
Create the floor substrate (recurring) + issue the first precision sleep (same batch; the creation action goes first)
→ bind_quota_automation → preflight PASS (armed)
→ only now may CreateWorkflow launch (arm-before-work)
Quota exhausted → waiting_quota (last_observation recorded)
Scheduled wake #1 → observation EXHAUSTED → remain-waiting (zero writes)
Scheduled wake #2 → observation AVAILABLE, mode=manual → waiting-user (unauthorized)
User authorize_quota_resume(max_resumes=1) → mode=auto
Scheduled wake #3 → resume-authorized (staged resume_count_after=1)
Host resume accepted → confirm_resume_started → resume_count=1, active
Scheduled wake #4 → observation EXHAUSTED → budget 1/1 exhausted → waiting-user (exactly one transition)
Terminal → attempt one substrate deletion (expected failure under the three-state rule) → report the automation id
→ the user deletes it manually (the expected operational action) → after confirmation, clear the binding
```

## 15. Global Quota Clock separation

The Global Quota Clock is an **independent future companion project**, outside the Conductor's responsibility boundary (split out wholesale in v2.4 Phase 3; see `docs/roadmap/GLOBAL_QUOTA_CLOCK_EXTRACTION_INVENTORY.md` for the reusable-parts inventory). Its provider/account-level goals — observing five-hour quota windows from real reset information and doing low-cost materialization after window boundaries — belong to the Clock project itself.

Hard separation (both directions):

- The Clock project may own: provider identity, snapshot parsing, reset observation, clock state, periodic low-cost materialization, its own scheduling integration;
- The Clock must not know: Conductor task_id, Work Units, route modes, implementation DAGs, ownership, review policy, task-level resume bookkeeping;
- **Conductor v2.4's correctness never depends on the Global Quota Clock**: a task waiting on quota stays resumable through its own native Scheduled Task path — without the Clock installed, everything works as usual.

No Clock code exists in the Conductor production picture (no clock / clock_store / clock automation surface, no host-scheduling SQLite write path); the Conductor keeps only the read-only quota **observation** surface (§17), which answers "can we resume right now" and maintains no clock.

## 16. Runtime module layout and CLI surface

```
plugins/glm-conductor/
├── agents/        # visual-implementer / glm-reviewer / visual-reviewer + the four direct-dispatch roles (see "Direct dispatch roles" below; no flash-implementer)
├── commands/      # quota.md (the /glm-conductor:quota command drives report.py read-only diagnostics)
├── hooks/         # hooks.json: SessionStart (resume injection) + Stop (completion guard) — only these two hooks
├── skills/        # the three skills orchestration / enforcement / continuity
└── runtime/       # pure standard-library Python (python3, zero third-party dependencies, 3.7 compatible)
    ├── state.py        # seven-state task state layer (schema / transition table / four-bucket discovery / legacy detection / repository-root binding)
    ├── change_id.py    # task-level change identifier (§9, the single freshness primitive)
    ├── task.py         # task lifecycle API (create / record / wait-resume / terminal wrap-up)
    ├── ownership.py    # path matching + git_touched_files + plan_stages compile-time planning
    ├── work_unit.py    # static nodes (construction + validation, zero runtime fields)
    ├── dependency.py   # pure DAG validation and deterministic ordering
    ├── workflow/       # persona + compiler + adapter + submission (§4 / §5 / submission model selection)
    ├── writer_guard.py # repository writer guard (§7)
    ├── journal.py      # events.jsonl append-only (the eleven-event task-level vocabulary)
    ├── durable_io.py   # shared atomic-write / exclusive-lock primitives (imports nothing from inside the runtime package)
    ├── recovery.py     # SessionStart resume summary (purely local, §18)
    ├── carrier.py      # OS wake-carrier face (v2.7.0 W1, §14): exact-argv pure construction (schtasks/crontab) + install/remove execution + wake command — argv lists only, shell=False, zero shell assembly
    ├── cli.py          # the runtime CLI's single entry point (the table below)
    └── quota/          # 11 read-only observation modules (§17)
```

**Direct dispatch roles (v2.7.0; design verdicts D9 / D10 / D11)**: the quartet `conductor-scout` / `conductor-runner` / `conductor-impl-flash` / `conductor-impl` joins `agents/` as native Custom Subagents the main session can dispatch directly, outside any compiled Workflow — D9 overturns the earlier "no custom scout agent" rule (cost motivation), D11 admits all four roles, and D10 revises "sole carrier = Native Workflow" by opening exactly one lightweight exception, the single-node standard-assurance `delegate`.

| Role / agent | Model binding | Contract essentials |
| --- | --- | --- |
| scout — `conductor-scout` | GLM-5.3-Flash (frontmatter pin) | read-only investigation / fact collection; every conclusion cites `file:line` evidence; never fabricates; answers within a bounded scope |
| runner — `conductor-runner` | GLM-5.3-Flash (frontmatter pin) | verbatim execution of entrusted commands; honest exit-code + output-tail reporting; never modifies repository files; never retries a destructive command; output carries no secrets |
| impl-flash — `conductor-impl-flash` | GLM-5.3-Flash (frontmatter pin) | bounded mechanical implementation from the five-section spec (§3 node-declaration format); ownership discipline identical to the workflow worker persona; honest `local_check` |
| impl — `conductor-impl` | inherited session model (no `model` key) | complex bounded implementation needing main-model strength without a whole workflow |

- **Any route, no task prerequisite**: scout (investigation) and runner (command / test execution) are read-only / non-mutating faces and may be dispatched on any route
- **Writer roles are guard-bound**: impl-flash / impl are dispatched only in the context of an active task holding that repository's writer reservation (§7) — the guard's semantics are unchanged; the main session hands over the five-section spec, and their changes remain fully covered by the task-level completion guard (checks 2–4, §12)
- **Single-node delegate lightweight channel (D10)**: when the DAG is exactly one node and assurance is standard, the main session may dispatch one impl role directly instead of a one-node workflow; the contract prices the costs explicitly — no structured `NodeResult`, no `ResumeWorkflowRun` recovery (recovery semantics = re-dispatch from repository facts) — while the validation / review / completion-gate chain is unchanged; multi-node `delegate` / `full` keeps Native Workflow as the sole carrier
- **Model-selection gate boundary**: a direct-dispatch role's model strategy is declared statically in its frontmatter (pinned / inherited) and never enters the `workflow-model-select` pipeline (that gate governs run-level `subagent_model` only); the launch interview's model question is untouched
- **Journal events (D13 as revised by D-C, v2.8.0)**: a direct-dispatch chain block records exactly one `chain_block_delegated` event per block (the main session's semantic decomposition decision); every other direct dispatch — scout, runner, single-shot mechanical offload — adds nothing to the eleven-event task-level vocabulary; validation / review / completion bookkeeping continues as usual

runtime CLI subcommands (exit code 0 success / 2 validation refusal / 1 runtime refusal; skill-layer runtime invocations always go through this CLI):

| Subcommand | Purpose |
| --- | --- |
| `v24-compile <dag-json> [--task-ref] [--out]` | static DAG → TS Workflow source (produces code only, never executes) |
| `writer-acquire / writer-release [--force] / writer-show` | repository writer-guard operation surface (§7) |
| `task-record-workflow-run <repo_root> <task_id> <run-id>` | canonical registration of the task ↔ Workflow run association record (§5; v2.6.0 WP-R4: AF-04 writer-guard precondition, repo_root ledger from any CWD, zero fake events on failure) |
| `[legacy low-level thin shell] v24-record-run <task_ref> <run-id>` | legacy entry for the same record (defaults repo_root from CWD; the canonical surface is the row above) |
| `workflow-model-select <models-json> [--model <exact-id>] [--task-ref <task_id>]` | worker model selection hard gate (INV-MODEL-01; explicit user-authorized selection: task pin > `--model` (must equal the pin) > Flash soft-recommendation auto-selection (bare invocation only); no pin and no `--model` refuses into the interview / zero candidates refuse / multi-candidate ambiguity refuses / an invalidated pin refuses; outputs the `{subagent_model, model_policy}` submission contract) |
| `worker-model-pin <repo> <task_id> <exact-id>` | task-level worker model pin write (§14; shape pre-check without querying the host surface, writable only in non-terminal states, same value idempotent / different value overwrites with prev/new, no journal event) |
| `quota-automation-bind / quota-automation-clear / quota-continuity-preflight` | auto continuity arming operation surface (§14; preflight ready=false → exit code 2 = a launch/resume block) |
| `os-carrier-install / os-carrier-remove / wake-carrier-probe` | L1 OS wake-carrier operation surface (§14; v2.7.0 W1 via `runtime/carrier.py`: install `[--session|--interval|--prompt]` with the SessionStart session-witness fallback, remove = terminal L1 cleanup, probe = pure-local zero-write capability probe returning `{platform, zcode_cli, scheduler, recommendation: os_timer \| sleep_only}`) |
| `review-record <repo> <task> <reviewer> <verdict> [note]` | task-level review record (validate before review) |
| `quota-resolve <repo> [--force-refresh]` | quota four-state resolution (§17) |

Quota diagnostics additionally have the standalone script `runtime/quota/report.py` (driven by the `/glm-conductor:quota` command; text + `--json` dual output). v2.3's permits / wave / verify-unit / verify-task / policy-* / wake-* / quota-clock-* / host-check subcommands, along with their backends, are all deleted.

## 17. Quota observation surface (read-only)

`runtime/quota/` is exactly 11 observation modules, answering only three things: the current provider quota status, the relevant reset time of an exhausted task, and whether attempting a resume right now is reasonable.

- **Provider abstraction** (`provider.py` / `parser.py`): monitoring-endpoint responses parse into standardized snapshots — window classification by semantic fields (unit=3/number=5 → five_hour; unit=6/number=1 → weekly); **the weekly window is optional** (the lite plan was measured to have no weekly window; the parser never assumes both windows necessarily exist); `evaluate` gives the four-state assessment (AVAILABLE / PRESSURE / EXHAUSTED / UNKNOWN, fail-open) and `plan_resume` the resume recommendation (continue / checkpoint / resume_at / periodic_fallback)
- **Provider adapters and hardening** (`zai.py` / `bigmodel.py` / `_http.py`): HTTPS only, a strict host allowlist (api.z.ai / open.bigmodel.cn), short timeouts (5s), length-capped responses (64KiB), redirects disabled, raw responses never persisted
- **Credential chain** (`credentials.py`): the environment variable `GLM_CONDUCTOR_QUOTA_API_KEY` first; the logged-in ZCode `~/.zcode/v2/config.json` provider configuration as the documented fallback; if neither is available → unavailable; credentials stay in memory only, zero persistence (error messages contain no key; exceptions surface only a classification word)
- **Runtime resolution** (`resolver.py`): `resolve_quota_status()` has a four-level hierarchy — fresh cache (≤300s, no network) → provider fetch (four-state assessment + atomic cache write) → stale cache fallback → UNKNOWN (never defaults to AVAILABLE); the cache is bound to the provider identity fingerprint (a 16-char non-secret sha256 prefix; a cache from another identity counts as no cache); the network is never retried
- **Diagnostics face** (`report.py`): `python3 report.py [--json]` — bounded probing of zai → bigmodel (the first success wins), three diagnostic states (a normal report / a probe failure with one error classification line per provider / unavailable with configuration guidance), exit code always 0 (the diagnostics face is not a gate), zero credentials on stdout
- **Window math** (`time_utils.py`): the canonical landing point for reset-time computation and window boundaries (moved in verbatim from the v2.3 measured basis; the dead `window_math.py` twin was deleted in v2.6.0)

Quota observation is used only as evidence for resume decisions, never as a routing axis. Native plugin-level quota APIs (getQuotaRemaining / getQuotaResetTime / onQuotaReset etc.) do not exist and are never fabricated; the 5-hour reset is never hardcoded.

## 18. Recovery and legacy tasks

Recovery entries converge to two, both purely local:

| Entry | Mechanism | Semantics |
| --- | --- | --- |
| SessionStart injection | `hooks/session_start.py` + `runtime/recovery.py` | on new session / compact / clear, injects `GLM CONDUCTOR RESUME CONTEXT` for unfinished tasks (per task only: task id / goal / repository / status / route / workflow run id / quota wait state / next safe action; fully silent when there are no tasks) |
| Native Scheduled Task wake | host automation → scheduled rounds (the §14 decision chain) | the future execution opportunity for a quota-waiting task; a duplicate wake is a safe no-op |

The next-safe-action vocabulary (`NEXT_ACTIONS`, exactly seven): inspect workflow / resume workflow / reassess repository / run main validation / run required review / wait for quota or user / explicitly retire legacy task. The repository outranks any runtime record: recovery defers to the repository's real state. Injection shape (stdout always empty when there are no tasks; fully silent):

```
GLM CONDUCTOR RESUME CONTEXT
[1] redesign-settings-page-7f3a2c
    Goal:    …
    Repo:    C:\work\demo
    Status:  waiting_quota   Quota wait: EXHAUSTED @ 2026-09-21T15:00:00Z
    Route:   full (assurance: high)   Run: <workflow_run_id>
    Next:    wait for quota or user
```

**v2.3 legacy tasks**: v2.4 only detects; it neither migrates nor runs them compatibly. `detect_legacy_task` gives a `legacy` verdict, the marker list, and the one-line handling guidance `LEGACY_STATE_GUIDANCE` ("v2.3 legacy task: wrap up or explicitly abandon it under 2.3.x"); `load_task` / the lifecycle API / the completion guard always refuse or allow-with-guidance for legacy tasks — never silently treating them as v2.4 tasks, never fabricating v2.4 completion evidence. v2.3.x remains retrievable at any time via Git history / tags.

## 19. Security model

- **Quota network face**: HTTPS only + host allowlist + short timeouts + length-capped responses + redirects disabled (§17); credentials never persisted; exceptions surface only a classification word; the provider identity fingerprint is a sha256 prefix truncation — non-secret, irreversible
- **Authorization never fails open**: quota_resume's auto authorization must be explicitly persisted; the count is committed only after the host accepts the resume; the budget invariant is enforced on save — a false refusal only loses one resume opportunity, a false allow is execution without authorization
- **Minimal state exposure**: state.json / events.jsonl contain no secret values and no full prompts; events.jsonl is append-only and isolated per task; `.glm-conductor/` is preferably excluded locally via `.git/info/exclude`, never auto-modifying a tracked `.gitignore`
- **Generated-source safety**: Workflow compilation output contains no timestamps / randomness / credentials; the forbidden-word scan anchors zero occurrences of permit / lease vocabulary
- **Static validation as the contract gate**: `scripts/validate_plugin.py` runs 16 checks to mechanically prevent contract regression (§22)

## 20. Runtime boundaries (ZCode host facts)

The following are host facts established by the W0 host re-verification (closed 2026-09-20) and prior live testing; v2.4's design takes them as constraints:

- **Workflow sub-actors do not trigger plugin hooks** (a structural bypass, re-confirmed on the current build in W0-A): enforcement through dispatch-face hooks is impossible on the main implementation path — v2.4 therefore converges enforcement to compile time (ownership planning + the writer guard) and the terminal check (the completion guard's four checks), and deleted the policy hook that only covered main-session Bash (keeping it would imply coverage the main path does not have). Out-of-scope changes are not prevented from happening, but they cannot silently pass the completion guard
- **Scheduled rounds are the owning session's mid-turn continuation** (a W0 finding): a wake round shares the main session's environment and permission state; it is not a new session / new process; trigger behavior while the app is closed is unverified — the continuity design does not depend on it, and the recovery backstop is the SessionStart injection
- One automation per session is a host hard rule (creating another in a session that already has a scheduled task is rejected outright by the host); an actual fire executes with seconds-level delay relative to the scheduled moment
- Enforcement-layer hooks depend on `python3` being on PATH; hooks ship with the plugin and take effect only in new sessions after install / update; agent definitions are snapshotted at session start and not hot-reloaded in a running session
- A subagent gets a fresh context on every invocation and cannot spawn further subagents (a flat structure); it sees only the MCP services connected at session start; foreground invocation is the supported premise
- Plugins and agents must never perform OS power operations (sleep / wake / shutdown etc.) on the user's behalf (power operations are the user's own prerogative)

## 21. v2.3 → v2.4 retirement map

The table below is the explicit retirement record: in v2.4 production code and docs these names **are not run paths — they do not exist**. Historical evidence is kept in the CHANGELOG and docs/reviews / docs/roadmap (architecture descriptions from before v2.4 hold only for 2.3.x).

| v2.3 concept | v2.4 disposition | Replacement |
| --- | --- | --- |
| dispatcher / dispatch wave / dispatch permit | deleted (module + CLI + hook face) | one Native Workflow run executes the whole DAG |
| per-unit lease (TTL / generation / heartbeat) | deleted | compile-time ownership planning + the repository writer guard |
| Work Unit runtime state (status / attempt / result history) | deleted | static nodes: declare compile-time facts only |
| per-unit / per-task durable receipts (verify-unit / verify-task / runner identity chain) | deleted | task-level validation / review records with change_id-anchored freshness |
| agent-run ledger / reconcile / resume manifest | deleted | host GetWorkflowRun observation + the minimal task-level resume summary |
| Bash policy gate (PreToolUse Bash) | deleted | ZCode native permission facilities + worker constraints |
| finalizing completion-request state / the completion-gate bookkeeping machine | deleted | the completion guard's all-green path wraps up directly (the four checks) |
| execution phases NORMAL / PRESSURE / DRAINING / BLOCKED | deleted | the quota observation's four states serve only as resume evidence |
| quota watcher / quota epoch / subscriptions / activation bookkeeping / window consumption proofs | deleted | the `quota_resume` bounded budget (resume_count / max_resumes) |
| Window Primer / Activation Transport / Task Wake Bridge | deleted | native Scheduled Task wakes (host capability) |
| Global Quota Clock (clock / clock_store / clock automation) | split into an independent companion project | the Conductor keeps only read-only quota observation (§17) |
| host-scheduling SQLite adapter (host/ direct next_run_at writes) | deleted | only the host's supported Scheduled Task capability is used |
| flash-implementer (text executor agent) | deleted | Native Workflow text workers (persona template) |
| continuity lifecycle axis (foreground / resumable / idle) | deleted | seven-state tasks + quota_resume authorization + native wakes |
| Layer A / Layer B dispatch injection and permit-gate hooks | deleted | hooks reduced to the two: SessionStart + Stop |

## 22. Static validation and tests

- `scripts/validate_plugin.py` (pure standard library, 16 checks) + local gates (static validation + unit tests) maintain contract consistency (hosted CI was removed in v2.5.2; validation runs locally): it scans `plugins/`, `README.md`, `marketplace.json`, and this document; always skipping `__pycache__` and `*.pyc` / `*.pyo`
- Tests (`tests/`, pure standard-library unittest) cover: static nodes and DAG validation, ownership stage planning and overlap judgment, the Workflow compiler (determinism / forbidden words), the writer guard (including concurrency), the seven-state layer and its transition gate, change_id, the task lifecycle, quota_resume authorization and resume decisions, the completion guard's four checks, the resume summary, the quota observation surface; tests for deleted components are deleted with the component and never kept as skips
- Version policy: the `marketplace.json` version and the latest CHANGELOG entry stay consistent (v2.6.2)

## 23. Stable boundary and evolution discipline

Frozen boundaries (changes require an explicit design verdict and synchronization across every contract face):

- The two routing axes + four routes (`solo / delegate / audit / full`) and the seven-role subagent roster (the visual pair visual-implementer / visual-reviewer + the text reviewer glm-reviewer + the four direct-dispatch roles conductor-scout / conductor-runner / conductor-impl-flash / conductor-impl)
- The TASK_ID format (semantic prefix + random hex suffix) and the `.glm-conductor/tasks/<task-id>/` (state.json + events.jsonl) state layout
- The seven-state vocabulary and `TASK_TRANSITIONS`; the completion guard's four-check order and block-message contract
- The change_id derivation basis and the "recording / guard sides call the same implementation" red line
- The writer_guard record's four fields and the "never auto-expires" semantics; the quota_resume five-key shape (the v2.7.0 W1 fifth key `automation_kind`, defaulting to `native`) and budget invariant
- The runtime CLI's public subcommand surface and exit-code semantics; the journal's eleven-event task-level vocabulary

Evolution discipline: unless actual usage exposes a concrete capability gap, no new routing dimensions or roles; the runtime performs deterministic enforcement only for high-confidence invariants (ownership out-of-scope, stale evidence, a second writer, unauthorized resume) and no semantic-interpretation interception; an execution fact the host already owns is never modeled again.
