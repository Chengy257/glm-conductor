# GLM Conductor Core Concepts (v2.6.2)

> **GLM Conductor is a lightweight semantic orchestration layer for the ZCode GLM Coding Agent: GLM Conductor owns semantic orchestration and acceptance; ZCode owns execution orchestration.**
>
> This document is a concept dictionary: it defines the core concepts of v2.6.2 one by one — what each is, what it looks like, and where its authoritative landing point lives. Every entry can be checked against [architecture.md](architecture.md) (the single source of architectural truth) and the code under `plugins/glm-conductor/runtime/`; the operational contracts of the runtime face live in the plugin skill documents. It narrates no evolution history and includes no retired v2.3 concepts (retirement cross-reference: architecture.md §21).

Two truth domains are the coordinate system of all concepts:

- **Static semantic truth** (held by Conductor): task / DAG / ownership / change_id / authorization — the eight main entries of this dictionary;
- **Execution runtime truth** (held by ZCode): the Workflow run's lifecycle, parallelism, retries, recovery — Conductor never mirrors it and only stores the run id association.

---

## 1. Task

**What it is**: one orchestration unit with a goal and acceptance — the only stateful concept Conductor has.

- **Identity**: `task_id` (TASK_ID) is mechanically unique — semantic prefix + random hex suffix (e.g. `redesign-settings-page-7f3a2c`), format constraint `^[A-Za-z0-9][A-Za-z0-9-]*$`; unique at generation and never regenerated during recovery
- **Shape**: exactly two files under `<ledger root>/.glm-conductor/tasks/<task-id>/` — `state.json` (the state source of truth) + `events.jsonl` (append-only provenance)
- **Status (seven states)**: `active / waiting_quota / waiting_user / blocked / completed / cancelled / failed`; the last three are terminal and **a terminal state is never silently reopened** (explicit reopen goes only through `reopen_task`, with a journal record); every transition is validated against the `TASK_TRANSITIONS` matrix each time, and off-matrix transitions are rejected
- **phase** (optional): `planning / workflow / validating / reviewing` — a purely descriptive label, no transition matrix, participates in no enforcement
- **route**: `mode` (four modes) required + `assurance` (standard / high) optional — assurance=high adds an independent-review obligation to the completion guard
- **Authoritative landing points**: `runtime/state.py` (schema / transition table / discovery four-bucket classification / legacy detection); `runtime/task.py` (lifecycle API)

**Neighbors**: task discovery classifies directories into active / terminal / corrupt / orphaned (a corrupt state.json is never interpreted as "no tasks"). v2.3 legacy tasks are only detected, never migrated — reported per `LEGACY_STATE_GUIDANCE`, for the user to close out under 2.3.x or explicitly abandon.

The four routing modes (the `route.mode` vocabulary, looked up on Delegability × Assurance):

| Delegability | Assurance | mode | Implementation | Independent review |
| --- | --- | --- | --- | --- |
| low | standard | `solo` | GLM-5.3 main session | no |
| high | standard | `delegate` | Native Workflow | no |
| low | high | `audit` | GLM-5.3 main session | yes |
| high | high | `full` | Native Workflow | yes |

events.jsonl illustration (one event per line; the task-level vocabulary is exactly ten names):

```
{"event": "route_selected", "mode": "full", "assurance": "high"}
{"event": "workflow_started", "workflow_run_id": "<run id>"}
{"event": "validation_recorded", "status": "passed", "change_id": "sha256:…"}
```

## 2. DAG (the canonical implementation DAG)

**What it is**: the implementation blueprint of a delegated task — it decomposes "the remaining implementation" into one static directed acyclic graph, and a single Native Workflow run executes the whole graph.

- Shape: the top-level `dag` array of the state (the node list; may be empty); dependencies are expressed with `depends_on` and may only reference nodes within the same DAG
- Validation: duplicate ids / missing dependencies / self-dependencies / cycles (reporting all cycle members) are rejected in aggregate at save and before compilation
- Ordering: a deterministic topological order and layers (layer i = the nodes whose dependencies all land in earlier layers); the same graph gives the same output for any input order
- Unit of execution: **one delegatable DAG ↔ one Workflow run** (typical projection: stage 1: A‖B → stage 2: C‖D → stage 3: E); the graph is immutable during a run — to change the blueprint, re-evaluate the route and open a new run
- **Authoritative landing point**: `runtime/dependency.py` (pure graph validation and ordering, zero I/O)

Example: a DAG of three nodes in two layers (`parser` and `types` do not depend on each other → same layer, parallelizable; `ui` depends on both → the next layer):

```yaml
dag:
  - { id: parser, depends_on: [],              ownership: ["src/parser/**"] }
  - { id: types,  depends_on: [],              ownership: ["src/types/**"] }
  - { id: ui,     depends_on: [parser, types], ownership: ["src/ui/**"] }
# Stage projection: stage 1: parser, types → stage 2: ui
```

## 3. Node (static node / NodeResult)

**What it is**: a vertex of the DAG — a **static node declaration**: it describes "what is to be done" and carries no "how far execution has gotten". The runtime face of the v2.3-era Work Unit (status / attempt / retry history / unit validation / receipts / leases) does not exist in v2.4.

- **The four required keys**: `id` (unique within the DAG), `objective` (a bounded goal), `depends_on` (dependencies), `ownership` (a non-empty list of repo-relative scopes — a node with no file scope is not compilable)
- **The three optional keys**: `interfaces` (interface surfaces that must stay compatible), `constraints` (extra constraints), `local_check` (optional local checks — a cheap node-health gate: it only establishes "this node is healthy enough for dependents to continue" and never substitutes for main-session task-level acceptance; a pure documentation node may have none)
- **The result contract (NodeResult)**: the worker returns exactly six fields per node; a non-empty `reassessment` is a route re-evaluation request, handed back to the main session to adjudicate
- **Authoritative landing points**: `runtime/work_unit.py` (construction + validation, a pure data layer with zero imports); the result interface is generated into the Workflow source by `runtime/workflow/compiler.py`

```json
// NodeResult: the structured implementation result a worker returns (exactly six fields)
{ "node_id": "parser",
  "status": "complete",          // "complete" | "partial" | "blocked"
  "changes": ["src/parser/lexer.ts"],
  "local_checks": ["pytest tests/parser -q — 12 passed"],
  "reassessment": "",            // non-empty = route re-evaluation request
  "gaps": [] }
```

## 4. Ownership (path ownership)

**What it is**: who may change which files — the only mechanism for v2.4 concurrency safety (the host's concurrent writes to the same file are a silent last-writer-wins; the compile-time check is the only conflict barrier; there is no runtime lock).

- **Scope syntax**: exact files (`src/auth.ts`); directory prefixes (the bare path `src/auth` equals `src/auth/**`); segment-level globs (`**` crosses segments, `*` / `?` do not). Implicit expansion is rejected: `src/auth` does not cover `src/authentication.ts`
- **Compile time (before Workflow launch)**: `plan_stages` plans the nodes into parallel stages with "pairwise disjoint scopes within a group" — greedy bin-packing within a layer; candidates with **clearly overlapping** scopes go **deterministically serial** (scheduled into later groups of the same layer) — that is not an error; an illegal or **ambiguous** pattern (covering the repository root / every path, e.g. `**`) is **rejected at compile time** with an `OwnershipConflictError` — a conflict is a rejection, never a guess
- **Task-level endgame (before acceptance)**: `actually changed paths ⊆ union(DAG ownership)` — enforced by completion-guard check 2; out-of-scope paths are named one by one
- **Repository level (the coarse backstop)**: **at most one active Conductor write Workflow per repository** — the `writer_guard` single persistent reservation (`.glm-conductor/writer_guard.json`, exactly four fields) never expires automatically (no TTL / heartbeat); release has exactly two paths: automatic release at task terminal closure, or an explicit clear via `writer-show` inspection followed by `writer-release --force`
- **Authoritative landing points**: `runtime/ownership.py` (matching / `git_touched_files` / `plan_stages`); `runtime/writer_guard.py`

```json
// .glm-conductor/writer_guard.json: the repository-level write reservation (exactly four fields)
{ "repo_identity": "C:\\work\\demo",
  "task_id": "redesign-settings-page-7f3a2c",
  "workflow_run_id": "<run id>",
  "created_at": "2026-09-21T08:30:00.000Z" }
```

## 5. change_id (the task-level change identifier)

**What it is**: v2.4's only freshness primitive — a deterministic digest of "the baseline revision + the normalized content state of the relevant path set" (`"sha256:" + 64 hex characters`).

- **Semantics**: any relevant file added / deleted / content-changed, a change in the relevant file set, or a baseline change alters the identifier; CRLF→LF normalization; `.glm-conductor/` bookkeeping paths excluded (the orchestrator writing its own ledger must not stale itself); independent of input order
- **The relevant path set**: `task.relevant_changed_files` = the union of all DAG nodes' ownership scopes (`task.ownership_scopes`) ∩ actual git working-tree changes (`ownership.git_touched_files` + `classify_paths`) — the recording side and the guard side must **call the same derivation, the same implementation** for the identifier to be comparable (the single-implementation red line; bypass recomputation is forbidden); adding / deleting / modifying / renaming owned files or a change in the file set alters the identifier; changes outside the scopes are still intercepted by the ownership check and are irrelevant to freshness; `.glm-conductor/` bookkeeping is excluded at both layers
- **Three consumers**: the validation record (`validation.change_id`), the review record (`review.change_id`), and the completion guard's freshness comparison. "Any fix invalidates prior validation / review" holds automatically because of this; the only recovery path is to redo and record the new value — writing an old value back to "extend its life" is forbidden
- **Authoritative landing point**: `runtime/change_id.py` (`compute_change_id`)

The three-line structure of the digest input (domain-separated, to prevent cross-domain digest collisions):

```
glm-conductor/change-id/1        ← the domain-separation prefix
base <git rev-parse HEAD>        ← the baseline revision ("-" for an unborn repository)
file <normalized path>\0<sha256:…|missing>   ← one line per relevant path (sorted and deduplicated)
```

## 6. Completion guard (four checks)

**What it is**: the Stop hook (`hooks/stop_gate.py`) that turns "completion" into an explicitly guarded operation — the same verdicts are importable via `evaluate_completion(repo_root, task_id)`. A `finalizing` completion-request state does not exist: closure happens on the guard's all-green path.

- **Trigger domain**: only v2.4 tasks with `status == "active"`; parked states (waiting_quota / waiting_user / blocked) are intentional stop points and always pass; v2.3 legacy tasks pass with disposition guidance attached; corrupt / orphaned get a stderr notice only; no active task passes silently
- **The four checks (in order; first failure returns)**:
  1. **writer_guard**: the repository has no active write reservation held by another task (the holder being this task passes — the completion gate is itself the release point);
  2. **ownership**: actual git changes ⊆ the union of the DAG ownership scopes;
  3. **validation**: a `passed` record exists and its change_id equals the current value (otherwise "missing" or "stale");
  4. **review**: when assurance=high, a `ship` verdict must exist and its change_id must equal the current value (fix-first / rethink never satisfy completion).
- **Output**: failure → a single-line block JSON on stdout (exactly one, most-severe, actionable English reason); all pass → `task.complete` in place (`completed` + release of the writer guard + a `task_completed` event) and allow
- **Failure semantics**: structural errors during evaluation (not a git repository etc.) degrade per task and pass (visible on stderr); a crash of the hook itself fails open. Out-of-scope changes are not prevented from happening (Workflow sub actors do not trigger hooks — a host fact), but they can never silently pass the completion guard
- **Authoritative landing point**: `hooks/stop_gate.py`

```json
// the block decision: single-line JSON on stdout; ZCode asks the model to keep working (the reason is the continuation instruction)
{"decision": "block", "reason": "Completion blocked: the validation record is stale — task-relevant files changed again after validation (task redesign-settings-page-7f3a2c).\n- Recorded change_id: sha256:3fa9…\n- Current change_id: sha256:8c2d…\nRe-validate and refresh the record via runtime.task.record_validation, then request completion."}
```

## 7. quota_resume (bounded quota-resume authorization)

**What it is**: the minimal authorization and bookkeeping block for a task to resume after the provider's quota is exhausted — bounded, requires authorization, and counts only "resumes that actually started". No epoch / subscription / activation bookkeeping / boundary-consumption proof (the v2.3 concepts are gone).

- **Shape**: the state top-level block has exactly four keys `{mode, max_resumes, resume_count, automation_id}`, initialized to `{manual, 0, 0, null}`; the budget invariant `resume_count <= max_resumes` is enforced at save time
- **mode**: `manual` (default) / `auto` — auto can only be authorized onto disk by an explicit `authorize_quota_resume(max_resumes)`, never inferred; `max_resumes=1` is a one-shot resume, `N` is bounded multi-window
- **automation_id** (v2.4.1 semantics): the witness of the currently associated future native Scheduled Task — the persisted arming fact of auto continuity; its presence is a preflight requirement, but it never independently proves the host-side activation still exists
- **Arm-before-work** (v2.4.1): `auto_continuity_armed = mode==auto AND automation_id is a non-empty str` — a mode=auto task must complete "create the Scheduled Task → `quota-automation-bind` → `quota-continuity-preflight` PASS" before the first quota-consuming Workflow launch and before any automatic resume; the carrier (recurring) is created at launch and never touched for its lifetime (v2.5.2 dual channel: the one-shot successor chain was deleted wholesale; precision is carried by the background sleep layer)
- **Dual-channel wake** (v2.5.2; the reset caliber became single-observation in v2.6.0): **the floor** = the recurring armed carrier (created right after the launch interview; hourly with a default of 1h, the interval confirmed by interview question 3; survives application restarts; three-state regularity — after the carrier's first trigger, every Cron change (change/delete/create) from that session silently degrades — hence it is never touched after creation, INV-CONT-03 as-is: after a successful wake, no delete and no rebuild); **the precision layer** = a background sleep until the earliest valid future `reset_at` observed across the identified windows + 300s grace (the v2.6.0 single-observation caliber, independent of quota status — preventive arming: established preferentially no matter how much quota remains; armed at launch + the rolling re-arm at the end of every wake turn; the establish/re-arm action always precedes work advancement; it exits the auto-wake session with measured second-level precision; it does not survive application restarts — the floor covers that, and the first floor wake re-arms, closing the self-healing loop). `quota-resume-decision` unconditionally attaches a decision `reset_at` to **every action** (the earliest future observed reset instant, an ISO string or null; the observation face and the decision face are same-source and same-batch; when only a cache source is involved and it holds no future reset, it force-refreshes exactly once, and if there is still none the floor-only downgrade is disclosed honestly) — it feeds the sleep template's conversion into the background sleep target (the bash template computes it dynamically; the model does zero arithmetic) and takes no part in the verdict; every wake makes a fresh observation and never trusts a stale `reset_at`. The one-automation-per-session constraint (measured 2026-10-03) ⇒ the main session runs at most one auto task at a time (D8); the terminal-state carrier delete attempt is expected to fail → report the automation id → the user deletes it manually (the expected operation, D7) → after confirmation, clear the binding
- **Waiting**: quota exhausted → `enter_waiting_quota` (waiting_quota among the seven states; `workflow_run_id` is kept; the latest observation is recorded into `last_observation` for diagnostics)
- **Scheduled wake decision** (`scheduled_activation_decision`, idempotent six steps): non-waiting_quota → `no-op`; observation EXHAUSTED/UNKNOWN → `remain-waiting` (never fabricate availability); unauthorized → `waiting-user`; budget exhausted → `waiting-user` with exactly one transition to `waiting_user`; auto with an empty automation_id → `remain-waiting` (the reason contains `continuity-unarmed`; zero disk writes and zero budget consumption); available + authorized + budget remaining + armed → `resume-authorized` (the count is only staged)
- **Confirmation is what counts**: `confirm_resume_started` is called only after the host resume call is **truly accepted** — `resume_count +1` + transition back to active + a `quota_resume_confirmed` event; an unconfirmed decision never consumes budget
- **Activation substrate**: the native ZCode Scheduled Task (a host capability); repeated wakes are a safe no-op; with no external clock installed everything works as usual
- **Authoritative landing points**: `runtime/task.py` (including the three arming APIs bind/clear/preflight); the quota observation face `runtime/quota/` (read-only: the four states AVAILABLE / PRESSURE / EXHAUSTED / UNKNOWN; a lite plan without a weekly window is a legal shape)

```
(the decision timeline of an auto authorization with max_resumes=1)
user authorize_quota_resume(1) → mode=auto
create the native Scheduled Task → bind → preflight PASS (armed) → only then may work start (arm-before-work)
quota exhausted → waiting_quota
wake → observation EXHAUSTED          → remain-waiting (the precision layer sleeps and re-arms to the next window, v2.5.2)
wake → observation AVAILABLE, manual  → waiting-user (unauthorized)
user authorize_quota_resume(1) → mode=auto
wake → available, auto, budget remaining → resume-authorized (count staged 1)
host resume accepted → confirm_resume_started → resume_count=1, active
wake → observation EXHAUSTED          → budget 1/1 exhausted → waiting-user
terminal → one carrier delete attempt (expected to fail under the three-state regularity) → report the automation id → the user deletes it manually → clear the binding after confirmation
```

## 8. Task-level authorization preference (worker model pin)

**What it is**: the **task-level worker model preference** explicitly authorized by the user in the task launch interview (orchestration §6.0) — authorized once, the default for the whole task lifetime (including new run submissions after wake resumes); it is a preference, not the task identity (the task identity is always `task_id`).

- **Shape**: the optional state top-level key `worker_model` — validated only when present (null or a non-empty str); absent by default (no interview authorization means no pin), zero migration when old tasks load (`runtime/state.py`)
- **The write never checks the host surface**: `task.pin_worker_model` (CLI `worker-model-pin`) lands on disk after a role-neutral shape pre-check of the submission form (a provider-qualified `<provider>/<model>` id — the `account:` namespace or any non-account provider such as a host-bridged provider — with two non-empty segments and no whitespace; not limited to Flash suffixes); the host surface at write time and at submission time may differ, and reconciliation is left to the consumption point; writable only in non-terminal states, idempotent for the same value (zero disk writes), an explicit overwrite for a different value (the output carries prev/new); no new journal event
- **Consumption always checks the host surface**: `workflow-model-select --task-ref <task_id>` reconciles against the current configured set every time; the selection priority is fixed: task pin > an explicit `--model` value (which must match the pin) > Flash soft-recommendation auto-selection (bare invocations only); a task with no pin and no `--model` is rejected and routed back to the launch interview (a single candidate is never implicitly authorized); an illegal pin shape (including on-disk drift) or a pin not in the list is specifically rejected (exit code 2: "the task pin is void; re-run the launch interview for authorization") — never falling back to auto-selection, never switching models silently; candidate drift is thereby naturally safe (new candidates are never auto-adopted, an old pin is never auto-replaced)
- **Interactive-turn boundary**: the interview and re-authorization happen only in interactive turns; wake turns / resume turns never ask — an unattended turn hitting a stale pin always blocks the submission and moves to waiting-for-user (fail closed)
- **Authoritative landing points**: `runtime/state.py` (key validation); `runtime/task.py` (`pin_worker_model`); `runtime/cli.py` (`worker-model-pin` write / `workflow-model-select --task-ref` consumption)

---

## Supporting entries (quick reference)

| Entry | One-line definition | Landing point |
| --- | --- | --- |
| Native Workflow | ZCode's native workflow: the execution substrate for parallelism / background / retries / recovery | host CreateWorkflow / GetWorkflowRun |
| Workflow compiler | the deterministic translation of a static DAG into TS Workflow source; it only produces code, never executes | `runtime/workflow/compiler.py` (CLI `v24-compile`) |
| persona | the single authoritative persona of the text worker; the host does not read the plugin's agent definitions, so it must be embedded into the generated source | `runtime/workflow/persona.py` |
| run association document | the id association of task ↔ Workflow run; zero state mirroring | `runtime/workflow/adapter.py` |
| reviewers (glm-reviewer / visual-reviewer) | fresh-context read-only Custom Subagents; assurance=high gets an independent final review after main validation; the model binding splits text/visual — glm-reviewer has no model field (it inherits the host/session model), visual-reviewer is explicitly bound to the multimodal GLM-5.3-Flash (an unavailable binding fails closed) | `plugins/glm-conductor/agents/` |
| the visual exception | visual-implementer keeps the Custom Subagent channel (the multimodal screenshot-feedback contract), not a second text implementation path | agents/visual-implementer.md |
| main-session validation | after the Workflow ends, the main session personally inspects the diff, runs a bounded set of integration commands, and records passed/failed | `runtime/task.record_validation` |
| journal vocabulary | exactly ten task-level events: route_selected / workflow_started / workflow_reassessed / validation_recorded / review_recorded / waiting_quota / quota_resume_confirmed / task_completed / task_failed / task_cancelled | `runtime/journal.py` |
| legacy task detection | read-only recognition of v2.3 state (work_units / permits / leases / receipts / phase states), never migrated | `runtime/state.py` |
| SessionStart injection | on discovering unfinished tasks, a new session gets a minimal recovery summary injected (purely local; silent when no task exists) | `hooks/session_start.py` + `runtime/recovery.py` |

## Stable boundaries (honest boundaries)

- The main session is the only orchestrator; no second task runtime / Workflow engine / permission engine / provenance system / scheduling platform / quota control plane is introduced
- Quota only decides "when to resume", never "who does it"; it is not a routing axis; a native plugin-level quota API does not exist and must never be invented
- The Global Quota Clock is an independent companion project: Conductor's correctness does not depend on it; a task waiting on quota recovers by itself through the native Scheduled Task path
- Workflow sub actors do not trigger hooks (a host fact): pre-write interception is impossible — deterministic enforcement lands at the two ends, compile time and the completion guard
- `.glm-conductor/` is the local runtime ledger and does not count as repository changes; prefer writing it into `.git/info/exclude`

## Common misreadings (tied to the eight entries)

- "delegate = automatically dispatching multiple subagents to run units" — no: `delegate/full` is one Native Workflow run executing the whole static DAG; dispatch scheduling belongs to the host, and Conductor issues no unit-level dispatch instructions; "completed can be written directly into state.json" — it cannot: the public write path cannot pass the terminal-transition gate, and the only commit point of completion is the completion guard's all-green path;
- "hand-filling a change_id into the validation record makes it pass" — it does not: the guard recomputes with the same implementation and compares; a forged value necessarily mismatches (the stale report gives both sides' values); "after an auto authorization the budget refreshes with a new window" — it does not refresh: `resume_count` only increases and never decreases; raising the budget requires another explicit `authorize_quota_resume` and it must not be lower than the used count.

## Further reading

- [architecture.md](architecture.md) — the single source of architectural truth: the authoritative technical description of fourteen topics (the routing matrix, the static DAG, the compiler boundary, host execution state, ownership conflicts, the writer guard, the seven states, change_id, main validation, the reviewer paths, the completion guard, the visual exception, minimal quota resume, and the Global Clock split).
- The skill documents (the operational contracts of the runtime face): `plugins/glm-conductor/skills/orchestration/SKILL.md`, `skills/enforcement/SKILL.md`, `skills/continuity/SKILL.md`.
- Troubleshooting manual: [troubleshooting.md](troubleshooting.md); documentation index: [docs/README.md](README.md); project entry: [the repository root README](../README.md).
