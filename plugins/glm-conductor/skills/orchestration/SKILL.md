---
name: orchestration
description: GLM orchestration: dual-axis routing (solo/delegate/audit/full), mandatory launch interview (wake, worker model, floor interval, workflow submission approval), DAG-to-Workflow compilation, validation, high-assurance review. Use for implementation, visual work, or code review.
---

# GLM Orchestration: Dual-Axis Selective Routing + Native Workflow

## Role and prerequisites

The main session is the architect. The main model inherits the host main session and is not programmatically switchable — Conductor never requires and never inspects the main session's concrete model name, and never judges model strength or modality capability from a name or version alone. The following duties stay in the main session and are never delegated:

- requirements and ambiguity resolution
- architecture and routing judgment
- task decomposition (static DAG authoring)
- implementation specs and node declarations
- full-diff inspection and verification re-runs (main validation)
- route reassessment decisions
- final acceptance and completion declaration

Subagents may only implement or review; they never become a new orchestrator.

Division of labor: GLM Conductor owns semantic orchestration and acceptance (routing, DAG, validation, review, completion guard); ZCode owns execution orchestration (workflow runs, subagent lifecycle, scheduling). Conductor never mirrors host execution state.

## Two-axis routing

Do not compress all situation state into a single risk level. Answer two questions independently, then consult the routing matrix:

**Axis A — Delegability**: is the remaining implementation bounded and specification-complete enough to delegate?

**Axis B — Assurance**: once implementation passes main-session validation, does the task need one independent final review from a fresh context?

The two axes are judged independently; neither derives from the other. The full criteria table lives in references/role-contracts.md.

## ROUTING PREFLIGHT

PREFLIGHT is not routing — it is evidence preparation for routing: when key facts are unknown before the dual-axis judgment, close them with read-only reconnaissance first; never fix the route early on weak evidence.

**Triggers** (any unknown item triggers it): root cause / implementation boundary / ownership set / interface contracts / verification plan / hidden coupling risk. Skip when the task is already explicit (all items known) — never reconnoiter for its own sake.

**Method**: prefer ZCode's built-in read-only Explore capability; the main session reads code itself when needed. No custom reconnaissance agents are added. Reconnaissance is read-only and never modifies the repository.

**Output** (after reconnaissance, before the SELECTIVE ROUTE declaration; template verbatim; every entry comes from actual observation — file/symbol/call-graph evidence, never speculative facts; an empty OPEN AMBIGUITY means no open ambiguity):

```
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

The report feeds the dual-axis judgment ('Two-axis routing'), the TASK CONTEXT PACK, and the static node declarations ('Task state and static DAG authoring' / references/role-contracts.md).

## SELECTIVE ROUTE declaration

Output once before any implementation delegation (a CreateWorkflow submission or a subagent invocation); keep the code block verbatim:

```
SELECTIVE ROUTE
mode: solo | delegate | audit | full
delegability: low | high
assurance: standard | high
reason: <concise, evidence-based reason>
```

Example:

```
SELECTIVE ROUTE
mode: delegate
delegability: high
assurance: standard
reason: implementation is bounded by explicit interfaces, owned files, and deterministic verification
```

Never delegate any implementation before the declaration. `delegability` records the Axis-A judgment; only the two keys mode and assurance enter task state (state.json) — `runtime.task.create_task` extracts exactly those two keys, and the task schema does not persist the other axis.

## Task state and static DAG authoring

### Task creation

Tasks needing completion-guard protection (delegate/full, audit, and any cross-turn long task) create task state after the SELECTIVE ROUTE declaration and through the 'Task launch interview':

- Entry (named CLI command): `cli.py task-create <repo_root> <task_id> <goal> <mode> <assurance> [--dag <path>]` (after validation writes `.glm-conductor/tasks/<task-id>/state.json` + a journal `route_selected` event; the route stores only mode and assurance; `--dag` reads a DAG JSON file in the same format v24-compile accepts)
- TASK_ID uses the mechanically unique "semantic prefix + random hex suffix" format (rules in the continuity skill section 'Task state and runtime files')
- The 'Task launch interview' is mandatory for every implementation task — pure small in-session tasks are asked too (the accepted cost of zero judgment); tasks the interview confirms as guard-free need no state.json (the natural outcome of answering "not enable" with a solo route) — with no state file the completion guard does zero intervention.

### Static nodes (Work Unit = compile-time declaration)

When delegate/full work needs multiple stages, decompose it into a static node DAG. Nodes declare compile-time facts only, zero runtime state (`runtime/work_unit.py` is the authoritative shape):

| Node field | Required | Meaning |
| --- | --- | --- |
| `id` | yes | node identifier, unique within one DAG |
| `objective` | yes | bounded implementation goal (maps to the five-part OBJECTIVE) |
| `depends_on` | yes | array of predecessor node ids — pure DAG: cycles, self-dependencies and missing references are forbidden |
| `ownership` | yes | non-empty array of repo-relative path scopes (maps to FILES AND OWNERSHIP) |
| `interfaces` | optional | interface surfaces that must stay compatible |
| `constraints` | optional | extra constraints (repo conventions, safety boundaries, exclusions) |
| `local_check` | optional | local check commands (the worker executes them honestly; empty means none are invented) |

Authoring discipline:

- **Every node must stay small enough to receive one complete bounded objective** — needing a further split means decomposition was insufficient; a node with no file scope (empty ownership) is not compilable.
- Dependencies express compile-time order facts only; the compiler merges nodes without conflicting dependencies into the same stage in parallel and runs the rest serially (`ownership.plan_stages`: candidates with clearly overlapping scopes go deterministically serial; ambiguous conflicts are rejected outright).
- **Two concurrently implementing nodes must not overlap ownership** — this is the only parallel-safety declaration required; mutual write exclusion is backstopped by the repository writer guard ('Execution procedure for delegate/full').
- The five-part implementation spec template (OBJECTIVE / FILES AND OWNERSHIP / INTERFACES / CONSTRAINTS / VERIFICATION) remains the thinking frame for node declarations and the delivery format of the visual channel; see references/role-contracts.md.
- **Nodes map to deliverables, not implementation steps (authoring anti-pattern)**: an objective may only be phrased as "what gets delivered"; if no deliverable can be stated and only a step sequence (first / then / next) can be listed, it is a step-type node and must be merged upward; in-node implementation order is the worker's own decision.

## Execution procedure for delegate/full

The only implementation substrate for delegate/full is a ZCode Native Workflow. The main session follows the fixed order below. Runtime calls always go through the runtime CLI named subcommands: `python3 plugins/glm-conductor/runtime/cli.py <subcommand>` — exit codes 0 = success / 2 = validation rejection (including a preflight ready=false block) / 1 = runtime rejection. The authoritative command list is the USAGE at the top of cli.py: never hunt for commands yourself, never invent `python3 -c` inline scripts, and never use command forms outside USAGE.

### Task launch interview

**The trigger point precedes create_task, with zero model judgment**: every task about to start implementation is asked exactly once — all implementation tasks are asked, **there is no exemption** (the interview is the mandatory entry to implementation start, not optional politeness; the accepted cost of zero judgment is that even minimal tasks get asked once). Wake turns / resume turns never interview (the boundary rule is at the end of this section).

**Single round of four questions (merged into one AskUserQuestion round, never split into multiple rounds)**:

1. **Cross-quota auto wake**: enable / not enable. If enabled, the same round also selects a **bounded max_resumes budget option** (the budget must be explicitly bounded, never an unbounded authorization); not enabled means manual continuity (stop at quota exhaustion and wait for the user to return).
2. **Worker model** (mandatory for every task with implementation delegation; solo and other no-delegation tasks are not asked the irrelevant worker question): before asking, make a live call to the host model list (ListModels) and put **all configured candidates on the list** — no provider restriction (not only bigmodel) and not only Flash. ListModels `levels` are reasoning tiers (disabled / low / high / max — disabled is the reasoning-off option and does not affect that model's availability); only a genuine row-level disabled flag excludes a candidate, and it is confirmed with the user on the spot. Display and convergence (single round, never multiple rounds): with ≤4 candidates, use them directly as AskUserQuestion options; with >4 candidates, list the full set in the question text (each candidate with its full precise provider-qualified id plus a one-line domain note — known plan/cost/capability remarks are written honestly; anything unverifiable is marked unknown and never fabricated), and the options converge to "the soft recommendation + Other free-text exact id". **The Flash tier is a soft-recommendation slot** (the cost/speed-friendly tier for bounded implementation), not an admission condition; **the selected model defaults to the max reasoning tier (the host default reasoning tier), and the tier is not stored in the pin** — the pin stores the model id only. **A single candidate also requires explicit user confirmation**, never silent auto-assignment; with zero suitable candidates, explain why and block that role — never silently inherit the main-session model and never pick a different provider on your own. The main-session model gets soft advice in the same question: strong-tier models are recommended for architecture/planning/validation — advice only: choosing another configured model triggers no blocking, no refusal, and no main-session model-name check.
3. **Floor carrier interval**: when cross-quota wake is enabled, confirm the interval of the floor recurring carrier — default **1h** (recommended; mean fallback lateness ~30 minutes), adjustable (e.g. 1h for important tasks, 4h for economical ones; **it must not equal the window length of a full 5h**, which races the reset at second granularity). When question 1 is "not enable", this question does not appear.
4. **Workflow submission auto-approval** (asked only for tasks with CreateWorkflow submissions — delegate/full): confirmation-free workflow submission for this task — **path-type submissions are auto-approved on authorized tasks**, and only path-type submissions (referencing the compiled workflow file via `path:`); **inline `script:` submissions always keep manual confirmation** — enable / not enable, soft recommendation **enable** (an authorized task's unattended wake/resume re-submission is no longer blocked on the confirmation ask). Independent of question 1: a manual-continuity task (question 1 "not enable") can still enable confirmation-free submission.

**After the answers (four independent writes)**:

- Question 1 "enable" → `quota-resume-authorize <repo_root> <task_id> <max_resumes>`, then immediately the dual-channel arming sequence (auto branch of 'Launch sequence (branch overview)' + the continuity skill section 'Launch sequence (runbook, arm-before-work)'; right after the authorization, `quota-resume-decision --force-refresh` obtains the observed reset_at — any quota status is observable under the single observation rule: the earliest valid future observed reset, regardless of quota status; the floor carrier is established in the same batch, and when a valid future reset exists the first precision sleep is issued in the same batch — all before CreateWorkflow, INV-CONT-01)
- Question 2 selected model → `worker-model-pin <repo_root> <task_id> <exact-id>` — writes the task-level worker model pin (state top-level key `worker_model`). **The pin is a user-authorized task-level preference, not task identity** (the distinction from rule 8 of 'Worker model selection gate' is stated there); it is the default for the whole task lifetime (including new run submissions after wake resumes); **the pin stores the model id only, no reasoning tier** (the selected model defaults to the max reasoning tier); the write does shape validation only (role-neutral — a configured non-Flash exact id is equally legal) and never checks the host surface; consumption always checks the host surface ('Worker model selection gate').
- Question 3 confirmed value → the `interval` parameter of the floor CronCreate (integer hours; default 1) — the interview-confirmed value is the carrier's lifetime interval and is never changed after creation.
- Question 4 "enable" → `task-create` carries `--auto-approve-workflow` — writes the state top-level key `auto_approve_workflow: true` (absent by default = false; schema authority `runtime/state.py`; the task layer passes the flag through). The authorization is consumed by the PermissionRequest confirmation-free hook, which checks live task state per request under a path restriction (path-type submissions only — inline always keeps manual confirmation). Honest multi-task semantics: the authorization is task-level, its effect window is repository-level — while any non-terminal authorized task is alive, path-type submissions in that repository are confirmation-free.

**Interaction-turn boundary**: the interview happens only in interactive turns; **wake turns / resume turns never interview** — an unattended turn that hits a blocker only an interview could clear (e.g. a stale pin) fails closed: block the submission and hand over to waiting-for-user; never create a deadlock by asking.

### Worker model selection gate

INV-MODEL-01, user-authorized explicit selection: before every CreateWorkflow submission, a user-authorized explicit worker model selection must be complete. This is a mandatory sequence, not optional advice — a failed step stops, and the next step is never entered in an impaired state. The Flash tier is a **soft recommendation**, not an admission gate: a configured non-Flash exact id authorized by the user (interview confirmation, task pin, or an explicit `--model` value) is equally legal.

1. **Call the host model list**: ListModels or an equivalent host model-discovery surface, taking the full set of configured models (model discovery belongs to the host; the runtime never builds its own list). **Recommended path: write the list to a temporary JSON file and pass the file path** (an inline array is only the convenience form for short lists).
2. **Extract configured exact ids**: keep every provider-qualified exact id of the form `<provider>/<model>` — the `account:<account>/<model>` scheme namespace and non-`account` provider ids (e.g. host-bridged providers such as opencode) are equally selectable; exclude only genuinely bare aliases (ids with no `/` qualifier, which cannot address a provider and silently fall back to the main-session model).
3. **Run workflow-model-select**: `cli.py workflow-model-select <models-json> [--model <exact-id>] [--task-ref <task-id>] [--repo-root <path>]` (models-json takes a file path or an inline array; --repo-root resolves the task ledger root explicitly and defaults to the current working directory). Value priority: **task pin > explicit `--model` value (must match the pin) > Flash soft-recommendation auto-selection (bare invocations without --task-ref only)** — the task pin is read by `--task-ref` from the `worker_model` persisted by the 'Task launch interview', and a hit is injected as an explicit selection. **For a task with a pin, an explicit `--model` value must match the pin**, and a mismatch is rejected (a plain `--model` must not bypass a task pin; changes go through interactive re-authorization). **`--task-ref` with no pin on the task and no `--model` → rejected and routed back to the 'Task launch interview'** (an interviewed task always has a pin; reaching this state means selection was never authorized — a legacy resume without a pin is never implicitly authorized for a single candidate). A bare invocation without --task-ref and without an explicit value takes the Flash soft-recommendation convenience path: exactly one Flash-tier candidate is auto-selected (**the Flash-tier criterion is version-independent**: the model segment ends with `-Flash`, so future-generation models such as GLM-6-Flash match out of the box — a convenience path, not an admission gate); zero candidates → reject (never a silent fallback); multiple candidates → ambiguous, reject and list all of them (never silently take the first); in an interactive turn, continue at question 2 of the 'Task launch interview': **interview (a single candidate is also confirmed) → persist with `worker-model-pin` → later submissions carry `--task-ref` and auto-read the pin**. **A configured non-Flash exact id is legal**: authorized via an explicit `--model` or injected via a task pin, it is selected (role-neutral shape validation, no tier restriction); bare aliases and unconfigured ids are always rejected.
4. **Selection failure never proceeds to CreateWorkflow**: a validation rejection (exit code 2) is the hard gate — report the reason and stop this delegation; never guess a fallback and never silently switch to another model.
5. **Submit with the returned exact subagent_model**: CreateWorkflow's `subagent_model` takes the selection return value verbatim (provider-qualified exact id — user-authorized explicit selection, no tier restriction).
6. **Never omit subagent_model** — omitting it inherits the main-session model.
7. **Never select a model through an unauthorized path** — any configured exact id (including non-Flash text models) is legally selected only by explicit user authorization ('Task launch interview' confirmation, a task pin, or an explicit `--model` value); bare aliases and unconfigured ids are always rejected; never fabricate a model id; worker selection is never judged from a model name or version alone.
8. **Diagnostics may report; identity must not**: diagnostic output may report the actually selected model id (a successful selection returns `{subagent_model, model_policy: "user-authorized-explicit-selection"}` as the submission contract), but model selection is not task identity — never write the selected model into state.json / the journal as a task identifier.

**Pin consumption semantics (stale pin rejected)**: a pin is written without checking the host surface, but consumption always checks the host surface — every `workflow-model-select --task-ref` reconciles against the current host model list, and a pin missing from the list is **specifically rejected** (exit code 2: the task pin is stale, re-authorize via the launch interview), never falling back to auto-selection and never silently swapping models. **For a task with a pin, an explicit `--model` value must match the pin** (mismatch exits 2) — a plain `--model` must not bypass a task pin; changing the authorization always goes through an interactive interview re-authorization plus a `worker-model-pin` update (returns a prev/new pair, both visible). Candidate drift is thereby naturally safe: new candidates are never auto-adopted, and a stale pin is never auto-replaced. A stale pin is a selection failure and falls under rule 4's hard gate; interactive turns may re-interview, wake turns / resume turns never ask — block the submission and wait for the user; **an unattended wake turn never re-pins automatically and never silently swaps models**. The pin is evidence of an interview decision under the Conductor interaction contract, not a cryptographic proof of user consent (no overclaiming).

**Rule 8 versus the task pin**: what rule 8 forbids is treating a model selection result as **task identity** (written into state/journal as the task identifier); the `worker_model` pin is not task identity — it is the **task-level model preference** the user explicitly authorized in the 'Task launch interview' (the task-lifetime worker model default), and the task's identity is always task_id. The two coexist: the pin upgrades the selection decision from a per-submission temporary action to "one authorization, the task-lifetime default", and rule 8 "diagnostics may report; identity must not" holds unchanged.

**The compiler is TypeScript model-agnostic (host binding fact)**: the v24-compile generated TS source neither carries nor sets the worker model — `subagent_model` is a run-level submission property the host binds at run submission (CreateWorkflow) time. Model choice can therefore happen only at the submission boundary: the compiled output is valid for any worker model, and running the wrong model is the submission action's responsibility, not the compiler's.

### Launch sequence (branch overview)

Both branches share one mandatory entry — the 'Task launch interview' — and one compact call order (the full runbook lives in the continuity skill section 'Launch sequence (runbook, arm-before-work)'):

```
manual: task-create [--auto-approve-workflow] -> v24-compile -> ListModels -> workflow-model-select -> writer-acquire -> CreateWorkflow (subagent_model=selection result) -> task-record-workflow-run
auto:    task-create [--auto-approve-workflow] -> quota-resume-authorize -> quota-resume-decision --force-refresh (observed reset_at = the earliest valid future observed reset, regardless of quota status) -> host CronCreate floor carrier -> quota-automation-bind -> quota-continuity-preflight (must PASS) -> first precision sleep (only when a valid future reset exists; reset_at null -> not issued, report the floor-only downgrade honestly) -> worker-model-pin -> v24-compile -> ListModels -> workflow-model-select --task-ref -> writer-acquire -> CreateWorkflow (subagent_model=selection result) -> task-record-workflow-run
```

The complete runbook — both branches with every step, the arm-before-work (INV-CONT-01) requirements, the three-part success statement, and the floor-carrier failure prohibitions — lives in the continuity skill section 'Launch sequence (runbook, arm-before-work)'.

### Launch details (both branches)

- **Write the DAG JSON**: `{"task_context": {"task_ref": <task-id>, "goal": ..., "repository": ...}, "nodes": [...]}` (nodes in the 'Task state and static DAG authoring' shape)
- **Compile**: `cli.py v24-compile <dag-json> [--task-ref <id>] [--out <path>]` — per-node shape validation + whole-graph validation + ownership stage planning + deterministic TypeScript source. A validation failure exits 2 and reports all errors at once; **the generated source is never executed automatically**.
- **Take the repository write reservation**: `cli.py writer-acquire <repo_root> <task_id>` — at most one active Conductor write Workflow per Git repository; on conflict it exits 1 and reports the current holder (reconcile before retrying, never force parallel writes).
- **Path-only submission rule**: every CreateWorkflow submission references the file v24-compile wrote to disk via `path:` (under `gen/`) — inline `script:` is forbidden (a single pass criterion for the submission auto-approval hook + closes the heredoc-escaping minefield + a typecheck retry reuses the same file without re-pasting).

### Post-submission procedure

1. **Record the run association (canonical command)**: as soon as the run id exists, run `cli.py task-record-workflow-run <repo_root> <task_id> <run-id>` — one call persists state.workflow_run_id + an association document under the ledger root `.glm-conductor/workflow-runs/` (independent of the process CWD; any CWD lands in the repo_root ledger) + a journal `workflow_started` event + an idempotent writer-guard run-id backfill (all through the task layer's existing transactional primitives). The AF-04 writer-guard precondition (delegate/full must first hold this repository's write reservation via `writer-acquire`) rejects on failure (exit code 1) and **never writes a fake `workflow_started`**. `cli.py v24-record-run <task_ref> <run-id>` is a **legacy low-level thin shell** (kept, behavior unchanged: it only writes the adapter association document, resolves directories relative to the current working directory, writes no state, emits no event, and touches no guard) — it is not a task lifecycle entry; the orchestration runbook always uses the canonical command.
2. **Wait for the structured result**: the completion notification is delivered by the host (GetWorkflowRun shows progress). Each node returns one NodeResult: `node_id / status (complete | partial | blocked) / changes / local_checks / reassessment / gaps` — all results are aggregated by node id.
3. **Closure**: after acceptance passes, `cli.py task-complete <repo_root> <task_id>` (terminal transition + `task_completed` event + automatic writer-guard release); failure/cancellation go through `cli.py task-fail` / `cli.py task-cancel <repo_root> <task_id>` (both also release the guard).

Interruption and recovery (workflow stall, quota wait, auto wake) live in the continuity skill; completion-guard semantics live in the enforcement skill.

## Main-session validation

- Workflow results and worker reports are only **claims** (implementation claim): the main session must personally inspect the full diff, check that the changed scope ⊆ the union of DAG ownership, and re-run the verification commands before validation evidence exists.
- After validation, persist the task-level validation record: `cli.py record-validation <repo_root> <task_id> <passed|failed> [--summary <s>] [--commands <s>] ...` (--commands is repeatable, exactly one command each; change_id is computed and stored at write time; the relevant path set = the union of all DAG node ownership)
- **change_id freshness**: any later change to relevant files invalidates the validation (and the subsequent review) record — after a fix, re-validate and re-record; writing an old value back to extend its life is forbidden.
- After aggregating multi-node results, the main session still performs task-level global validation (cross-node integration/build/lint) — node-level local_check never automatically substitutes for global validation.

## High-assurance review

Choose a fresh-context read-only reviewer by task modality:

- Text tasks → `glm-reviewer` (read-only whitelist; the model inherits the host/session — no cross-model independence is claimed; independence comes from the fresh context and read-only isolation)
- Visual tasks → `visual-reviewer` (multimodal read-only, GLM-5.3-Flash frontmatter binding, primary duty independent visual acceptance: personally read the screenshots against VISUAL ACCEPTANCE and check user-visible regressions; the diff is context only). When that binding is unavailable on the host (startup errors such as account-connection-unavailable), the visual high-assurance route fails closed: report the binding as unavailable to the user and never fall back to a text model (glm-reviewer or the main session) as the visual final review.

Discipline:

- The review is invoked only after main-session validation; the input elements (STATED GOAL / ACCUMULATED CHANGE SET / INTERFACES AND CONSTRAINTS / VERIFICATION EVIDENCE — visual tasks add VISUAL EVIDENCE) are provided by the main session; a missing element is requested, never guessed.
- There are exactly three verdicts: `ship` / `fix-first` / `rethink`. fix-first: on the audit route the main session fixes, on the full route the implementing party fixes; after the fix the main session re-validates and a **new** reviewer re-reviews. rethink: revise the architecture; completion must not be reported. fix-first / rethink never satisfy the completion guard.
- Verdicts are recorded through the CLI: `cli.py review-record <repo_root> <task_id> <reviewer> <verdict> [note]` — **validate first, review second** (rejected when no validation record exists), with change_id computed and bound; any later change to relevant files invalidates the verdict, requiring re-review and re-record.
- The completion guard (assurance: high) accepts only review records with verdict=ship and a fresh change_id.

## Visual exception path

Visual/interactive tasks (frontend UI, layout, dashboards, form interactions, visual regressions) bypass the native Workflow text channel and dispatch the `visual-implementer` subagent directly:

- The main session captures screenshots to disk with its dedicated Browser/Computer Use capability (the subagent has no capture right); the implementer ends its invocation with a `VISUAL_CAPTURE_REQUEST` to request evidence, and the main session starts a new invocation carrying the full state to continue (VISUAL_ROUND 1-3, at most 3 rounds).
- Visual judgment is made by the multimodal roles (visual-implementer / visual-reviewer personally Read the screenshots); the main session only drives and captures (Conductor never asserts its own multimodal capability from the main model's name) and must not claim it made visual judgments.
- Visual tasks with assurance: high get an independent final review by `visual-reviewer` (verdict and review-record flow as in 'High-assurance review').

Criteria, templates and protocol details live in references/role-contracts.md and references/operations.md.

## Reviewer and implementer preflight

- The visual channel requires `glm-conductor:visual-implementer` in the Agent tool's available agent types; for assurance: high, check `glm-conductor:glm-reviewer` / `glm-conductor:visual-reviewer` by modality.
- delegate/full text implementation runs through the native Workflow (the worker persona is embedded in the compiled source by the compiler) and needs no subagent preflight; the worker model selection gate ('Worker model selection gate') is also fail-closed but is not this section's subagent preflight; solo and assurance: standard need no preflight at all.
- Models and reasoning tiers are fixed in the subagent definitions; never attach model overrides at invocation time.
- For assurance: high visual work the final review must be the multimodal `visual-reviewer`; if its GLM-5.3-Flash binding is unavailable on the host (startup errors such as account-connection-unavailable), treat it as fail closed per 'High-assurance review' — the route stops rather than falling back to a text model.
- Extra requirement of the visual channel: confirm screenshot evidence can be written to disk and read by the implementer; if unobtainable, stop the visual channel.
- When any required role is missing, unavailable, or name-mismatched: stop that channel, tell the user to check plugin installation (Settings → Plugin Management), and never silently substitute another subagent type.

## ROUTE REASSESSMENT

Routing is not a one-way ladder. A route change must come from **newly observed evidence** and may be reassessed in both directions:

```
ROUTE REASSESSMENT

delegability: <old> -> <new>
assurance: <old> -> <new>
mode: <old> -> <new>

evidence:
<newly observed evidence>
```

Example A (delegability down): delegate execution keeps returning node results with `reassessment` requests, exposing hidden cross-module coupling →

```
delegability: high -> low
assurance: standard -> high
mode: delegate -> audit
evidence: node results kept returning reassessment requests exposing hidden cross-module coupling
```

The main session takes over: implementation → validation → reviewer final review.

Example B (delegability up): after solo investigation the root cause, architecture, files and verification are all determined and the remaining work is purely mechanical →

```
delegability: low -> high
mode: solo -> delegate
evidence: root cause, architecture, owned files and verification are now fully determined; remaining work is mechanical
```

Precondition: the main session has not itself repeatedly completed the same implementation. With no new evidence the route must not change; it is never changed for convenience or on a hunch. After a reassessment, append a `workflow_reassessed` event to the task journal.

## Completion hand-off

Task closure (complete/fail/cancel) and the completion judgment are gated by the Stop completion guard: repository writer guard, ownership overrun, main-session validation, and independent review — all four checks must pass before completed is committed. Guard semantics, change_id freshness and degraded behavior live in the enforcement skill.
