# ZCode 3.14 Native Workflow Spike — Results & Re-baseline Recommendation

> Companion to `ZCODE_3_14_NATIVE_WORKFLOW_REBASELINE_PLAN.md` (D1 deliverable).
> Evidence classes follow plan §3. PASS = the case's question was resolved with evidence
> (positive or negative); BLOCKED = could not execute; UNKNOWN = executed but inconclusive.
> Raw probe assets: `scripts/spikes/zcode_3_14_workflow/` (non-production). Raw evidence tree
> `.glm-conductor/spikes/` is git-ignored; this document carries only sanitized summaries.

## 1. Run metadata

```text
RUN_ID: spike-20260919 (15 workflow runs, dwfrun-9fc0f492 … dwfrun-f2a43d75)
DATE: 2026-09-19/20 (local)
ZCODE_VERSION: 3.14.0
ZCODE_BUILD: 3.14.0.7681 (ZCode.exe VersionInfo, C:\Program Files\ZCode)
OS: Windows 10.0.19045 x64 (win32, Git Bash shell)
WORKSPACE_TYPE: local
REPO_COMMIT: faaafdc (main base); branch review/zcode-3.14-native-workflow
PLUGIN_VERSION: repo 2.3.2; session-loaded plugin cache 2.0.1 (STALE — see §2.3)
PRIMARY_MODEL: account:bigmodel-start-plan/GLM-5.3-Flash (thought level max)
PRIMARY_PROVIDER_ID: account:bigmodel-start-plan
PRIMARY_THOUGHT_LEVEL: max
FRESH_SESSION_AFTER_PLUGIN_CHANGE: n/a (no plugin change)
FRESH_SESSION_AFTER_AGENT_CHANGE: n/a
NOTES: exactly ONE model configured on the host (ListModels); plugin agent frontmatter
       references account:bigmodel-individual-coding-plan/* which is NOT configured here.
```

## 2. Environment and configuration facts (WF-00)

### 2.1 Workflow surface

- Definition/invocation: `CreateWorkflow` accepts a TypeScript script by `path` or inline;
  compiled before launch (compile errors are diagnostics, nothing runs). Saved definitions live
  in project `.zcode/workflows/` and global `~/.zcode/workflows/` (both empty at spike start);
  inline drafts under `.zcode/workflow-drafts/`.
- Runtime language/facade: TypeScript, strict; `agent(name?, persona?)` + `ask<T>(instructions)`
  (typed result coercion), `phase()` markers, `Promise.all` joins, `world.run` (compile-time
  command literals), journaled `files.*`/`git.*` reads, `artifact.*` publishing.
- Execution: runs start in background immediately; `ResumeWorkflowRun`/`AmendWorkflow`/
  `TaskStop`/`GetWorkflowRun`/`ListWorkflowRuns`/`ResolveWorkflowQuestion` manage lifecycle;
  compiled per-run module persisted at `.zcode/workflow-runs/<run-id>.mjs` (observed-internal;
  contains lowered payload + stdio actor shim `create-actor`/`ask`; VM `maxOldSpaceSizeMb=256`).
- Hot-reload of saved definitions: UNKNOWN (not tested).

### 2.2 Models (WF-03 environment basis)

- Host model list: exactly `account:bigmodel-start-plan/GLM-5.3-Flash` [current], levels
  low/high/max. Plugin agent frontmatter models (`…individual-coding-plan/GLM-5.3-Flash`,
  `…/GLM-5.3`) are NOT configured on this host.
- `subagent_model` is run-scoped and validated at submission: requesting the unconfigured id
  fails hard — "No configured model matches …" — no silent fallback.

### 2.3 Environment limitations (recorded, not conclusions)

- **Stale plugin install**: session loaded glm-conductor **2.0.1** from the plugin cache while
  the repo is **2.3.2**. The installed `hooks.json` registers only Stop, PreToolUse
  (Agent|Task, Bash); PostToolUse*/SessionStart/CronCreate surfaces are absent from it.
- **Hook executability**: hooks invoke `python3`, but PATH `python3` is a Microsoft Store stub
  (exit 49, no output) ⇒ plugin hooks could not spawn in this session at all (fail-open).
  Consequence: hook-visibility conclusions (WF-06/07) rest on the STRUCTURAL bypass
  (§3 WF-06/07), with the observed empty `.glm-conductor/` tree as weak corroboration only.
- Session permission mode auto-approved all 15 workflow submissions (no interactive prompt).

## 3. Per-case results (WF-00 … WF-25)

### WF-00 — Environment and feature discovery — PASS

Evidence: §1, §2; run ledger. Observed: version/build captured; entry points = CreateWorkflow
tool (programmatic), composer/`/workflow` documented in plan; project+global definition dirs
supported; runs observable via tool surface, UI cards, `/tasks`, `.zcode/workflow-runs/`.
Reproducible-from-clean-session condition met: all probes are standalone `.ts` files.

### WF-01 — Minimal deterministic workflow — PASS

- EV-001 (OBSERVED, run dwfrun-9fc0f492): one child (`agent("wf01-probe", persona)`), task
  21×2, typed result `{"product":42,"formula":"21*2","note":…}` returned with full fidelity.
- Conclusion: base callable model established — script → actor → typed ask result → final
  return delivered to the main session as a completion notification.

### WF-02 — Plugin custom-subagent addressability — PASS (answer: NOT addressable) — ARCHITECTURE-CRITICAL

- EV-002 (OBSERVED, run dwfrun-22761c18): children created as `agent("glm-conductor:flash-implementer")`,
  `agent("glm-conductor:glm-reviewer")`, `agent("bogus-plugin:ghost-role")` — all with NO
  persona — reported the SAME generic system prompt head: "You are a subagent inside a dynamic
  workflow run, named X". The invalid id produced no error. All three self-reported full
  toolsets (Bash/Write/Edit/… + TaskOutput/TaskStop + submit_result/escalate + workflow tools
  + 5 MCP tools) and session model.
- Interpretation: `agent()` names are identity labels; plugin agent definitions (system prompt,
  model field, tools field) are not consumed by the workflow runtime. Role emulation requires
  copying persona text into the script.

### WF-03 — Model binding and thought-level preservation — PASS (answer: per-role binding NOT expressible) — ARCHITECTURE-CRITICAL

- EV-003 (OBSERVED): default run (dwfrun-d8cc8ec4): both role children self-reported session
  model `account:bigmodel-start-plan/GLM-5.3-Flash`; provider hint = account segment;
  thought level not perceivable by children. Explicit variant: submission with
  `account:bigmodel-individual-coding-plan/GLM-5.3-Flash` REJECTED at validation ("No
  configured model matches … Available models: account:bigmodel-start-plan/GLM-5.3-Flash").
- Conclusion: one model per run (all children), must match host-configured models, omitted ⇒
  session model. Flash-implementer/flagship-reviewer split impossible on this host. **No
  silent fallback observed** (hard validation error instead) — the blocker the plan flagged is
  a hard constraint, not a correctness hazard.

### WF-04 — Tool permission preservation — PASS (answer: host does NOT restrict tools) — ARCHITECTURE-CRITICAL

- EV-007 (OBSERVED, run dwfrun-596e0c21): "reviewer"-labeled child with neutral persona
  attempted `Write` → SUCCEEDED (`reviewer_should_not_write.txt` = REVIEWER-WROTE on disk);
  implementer child wrote `impl.txt` = IMPL-WROTE. Both children have full toolsets.
- Conclusion: tool restrictions inside workflows are persona conventions, not host
  enforcement. No deterministic read-only child exists.

### WF-05 — MCP visibility and session snapshot — PASS (main question) 

- EV-007 (child-reported): both children listed all 5 session MCP tools. MCP servers available
  at session start are visible to workflow children. Mid-session connector changes: UNKNOWN
  (not tested). No credentials probed.

### WF-06 — Hook coverage: workflow-level invocation — PASS (answer: none)

- EV-005/EV-009 (OBSERVED absence): full-tree diff of `.glm-conductor/` across runs shows zero
  hook-produced files; no hook names/events observed for workflow start. Structural basis: §2.3
  caveat applies (hooks inoperable in this session); conclusion rests on WF-07's structural
  argument, which subsumes workflow start (also not a main-session tool call).

### WF-07 — Hook coverage: workflow-spawned child agent — PASS (answer: no hook surface) — ARCHITECTURE-CRITICAL

- EV-009 (OBSERVED + structural, run dwfrun-82a1b2dd): two marker-bearing children launched;
  before/after diff of `.glm-conductor/` = zero changes. PreToolUse(Agent|Task) did not fire;
  PostToolUse(Agent|Task) did not fire (and cannot: children are spawned by the runtime's
  internal actor protocol — `create-actor`/`ask` over stdio, observed in the `.mjs` run module
  — never via the main-session Agent|Task tool surface, so those matchers have no occasion to
  fire). Tool input identity mapping N/A; `updatedInput` mutation N/A; deny cannot gate
  workflow child launches.
- Conclusion: dispatch permit validation/consumption and agent-launch bookkeeping have NO
  purchase on workflow children. The deterministic boundary that remains is the
  **conductor-authored script itself** (it decides whether/what to launch) plus prompt-carried
  markers for in-script checks.

### WF-08 — Nested-subagent boundary — PASS (answer: no nesting)

- EV-008 (OBSERVED, run dwfrun-462d15b7): child has no Agent/Task tool; nested attempt =
  `no_tool`; TaskOutput/TaskStop are background-task helpers only. Matches the ordinary
  subagent baseline; staged workflow composition is the alternative.

### WF-09 — Foreground parallel fan-out — PASS

- EV-010 (OBSERVED, run dwfrun-e68ccf97): 3 children in one `Promise.all` genuinely overlapped
  (all pairwise epoch-interval intersections true); parent waited for all; per-ask result
  ordering preserved; all succeeded. Note: fan-out width is runtime-adaptive — a later run
  showed `concurrency=2/4 (rate_limited)` under provider rate pressure; max concurrency is not
  a script contract.

### WF-10 — Parallel writes, disjoint ownership — PASS

- EV-011 (OBSERVED, same run): both disjoint fixture writes landed correctly (DISJOINT-A /
  DISJOINT-B); children shared one workspace view (identical `git status`); no serialization,
  no worktree isolation. Safety on disjoint paths was observed, not guaranteed by the host.

### WF-11 — Parallel writes, conflicting ownership — PASS (answer: silent last-writer-wins) — ARCHITECTURE-CRITICAL

- EV-012 (OBSERVED, run dwfrun-eb564cd2): beta wrote `BETA-CONTENT-…499.8`; alpha overwrote the
  whole file at `…501.8`; final content = ALPHA. Host detected nothing: no conflict event, no
  serialization, no error. The only conflict signal was incidental (alpha happened to read
  beta's content and mentioned it in prose).
- Conclusion: host provides no conflict protection; GLM Conductor must retain an explicit
  conflict-safety semantic for parallel writes. The exact mechanism (legacy per-unit leases vs
  compile-time ownership plus a coarser repository writer guard) is a re-baseline decision, not
  established by this spike.

### WF-12 — Staged dependency execution — PASS

- EV-013 (OBSERVED, run dwfrun-b46e82ba): stage B started only after stage A resolved and
  consumed A's repository artifact (`squares.json` `[1,4,9,16,25]` → doubled `[2,8,18,32,50]`).
  Dependency syntax = plain script control flow (await/phase ordering) + typed results +
  repo-file handoff; statuses propagate through run record phases.

### WF-13 — Main-session / workflow result handoff — PASS

- EV-001/EV-013/EV-004: per-child typed results and the aggregate final return arrive with
  full structured fidelity; run record additionally exposes per-child task text, site ids
  (`actor#N@M`), ask ids, per-step token counts, phases and health. Mechanical consumption =
  typed JSON; model-written prose only where the script puts it. Children also expose
  `escalate` (question parking to the main session) — surface observed, behavior untested.

### WF-14 — Background workflow behavior — PASS

- EV-014 (OBSERVED, runs dwfrun-92423453 et al.): background is the default: the parent turn
  continued freely; progress visible via `GetWorkflowRun` (phases, subagents, health incl.
  `concurrency=2/4 (rate_limited)`); completion delivered as a notification carrying the full
  final return; runs proceed unattended (`owned_by_this_session=true`); repository writes by
  children continue while the session is otherwise idle. Hook observability during background:
  same as foreground (none — WF-06/07).

### WF-15 — Cancellation semantics — PASS (core; idempotency partial)

- EV-015 (OBSERVED, runs dwfrun-2725efe2, dwfrun-f2a43d75, dwfrun-92423453): `TaskStop`
  mid-flight → status `stopped` (stop_reason=model), SAME run id retained; in-flight child
  marked `unfinished` ("leftover of the exited process, not live work") and re-dispatched on
  resume; never-dispatched phase stayed `ahead` (cancel-before-start covered); no partial
  repository changes survived in these probes (child killed before its writes; no cleanup
  performed, none needed). `TaskStop` on an already-completed run is a safe no-op. Idempotent
  double-cancel beyond that: UNKNOWN.

### WF-16 — Failure and partial-success semantics — PASS

- EV-017 (OBSERVED, runs dwfrun-fbbfbe34, dwfrun-6d1c9053, dwfrun-5d33c152): deterministic
  child failure via output violation is NOT reachable: a child instructed to return
  non-conforming prose was auto-retried (run record shows `step 2` on the same ask) until it
  produced schema-conforming JSON (`{"n":0}`) — silently coerced, ~16k extra tokens, no
  rejection. Uncaught variant: run still completed. The real failure channel is script-level:
  a gate `throw` after the child settled → run `errored` (code DriverError, exact message
  surfaced, child step preserved as done with its token cost). Per-ask `try/catch` preserves
  sibling results. ⇒ "which unit failed" is identifiable at script/gate granularity; children
  cannot be made to fail deterministically by instruction alone; provider/context errors
  (documented) stop the run as `stopped`/`errored` respectively.

### WF-17 — Resume / retry identity semantics — PASS — ARCHITECTURE-CRITICAL

- EV-016 (OBSERVED): `ResumeWorkflowRun` continues under the SAME workflow run id; completed
  asks replay from the journal WITHOUT re-execution (controlled experiment: phase-A child's
  file mtime, content and reported stamp all unchanged after resume; result identical), while
  the in-flight ask re-dispatched LIVE (fresh execution, fresh timestamps) and never-started
  asks run for the first time. Repository state is re-read live by re-dispatched children;
  replayed children contribute cached results only. Parent session identity unchanged.
  Child ids: site ids stable per ask; re-dispatched asks got fresh step attempts.
- Conclusion: host resume is execution-lifecycle recovery at ASK granularity. It does NOT
  by itself perform repository-level acceptance or re-verification. Whether legacy per-unit
  `reconcile.py` remains necessary depends on the v2.4 task-state model; this spike establishes
  only the host boundary, not the final Conductor recovery architecture.

### WF-18 — App/session restart boundary — BLOCKED (live); DOCUMENTED proxy

- EV-018: the spike cannot close/reopen ZCode autonomously. Documented semantics (host tool
  documentation): a run whose owning process exited is stop_reason `interrupted` and is
  resumable via `ResumeWorkflowRun` — i.e. execution state is designed to survive process
  death. Live restart behavior (resumes vs fails vs inspectable-only): UNKNOWN.

### WF-19 — Permission prompt interaction — UNKNOWN (environment) + OBSERVED partial

- EV-019: in this session's permission mode all 15 submissions auto-approved (no prompt);
  mid-run permission prompts could not be manufactured. Whether the whole run pauses or only
  one child pauses, and approve/reject effects: UNKNOWN. Documented: the script's command set
  (`world.run` literals) is surfaced to the user at confirmation time.

### WF-20 — Reviewer isolation topology — PASS (with constraints)

- Evidence: EV-002 (fresh children start with generic system prompt — no implementation
  conversation inheritance; fresh context OBSERVED), EV-007 (read-only NOT host-enforced),
  EV-003 (flagship model binding impossible), EV-009 (explicit diff/evidence inputs and
  fix-first→new-reviewer both expressible: each `agent()` is a fresh actor; prompts carry
  content verbatim).
- Conclusion: a fresh-context reviewer actor can be constructed, but the tested Workflow
  path does not consume Custom Subagent tool/model contracts by role name. On this single-model
  host, the desired Flash implementer + flagship strict-reviewer split was therefore not
  expressible inside one Workflow run. This does not establish that every future host/model
  configuration lacks flagship access; it establishes that per-role Custom Subagent contracts
  were not preserved by the tested Workflow path.

### WF-21 — Existing dispatch-marker compatibility — PASS

- EV-009 (OBSERVED): `GLM_CONDUCTOR_DISPATCH=SPIKE-WF07-IMPL` and
  `GLM_CONDUCTOR_REVIEW=SPIKE-WF07-REVIEW` passed verbatim into child ask instructions and
  were echoed exactly; the workflow runtime does not rewrite or abstract child prompts.
  Hook-visible inputs retain the marker: NO — hooks never see workflow children (WF-07).
  ⇒ current permit/review-receipt PLUMBING cannot be reused as-is; markers remain usable only
  for conductor-authored in-script enforcement.

### WF-22 — Workflow run observability — PASS

- EV-004/EV-017 (OBSERVED): public/documented tool surface: `ListWorkflowRuns` (id, status,
  label, ownership, spent_tokens, timestamps), `GetWorkflowRun` (phases with settled/running
  counts, subagents with task text + site/ask ids + per-step tokens, health incl.
  consecutive_failures/stalled, error code+message, amendable/resumable guidance),
  `ResolveWorkflowQuestion`, artifacts. Observed-internal: `.zcode/workflow-runs/<id>.mjs`
  compiled modules (203 lines each; actor protocol; 256MB VM cap). UI: run cards with phase
  graph; `/tasks` task ids; Automations page for schedules. No production coupling to internal
  stores was added. Full 15-run inventory captured (≈1.41M tokens total spike cost).

### WF-23 — Quota exhaustion during workflow execution — UNKNOWN (by design)

- EV-020: no natural quota-blocked condition occurred; per plan it was not manufactured.
  Documented semantics: provider-side errors stop the run with stop_reason `provider`; the
  user resolves the cause; resume is manual. UNKNOWN does not block execution-substrate
  conclusions per plan §7.

### WF-24 — Workflow versus Global Quota Clock / Task Wake Bridge — PASS (analysis)

- (1) Does workflow resume recover execution state? YES — observed (WF-17).
- (2) Does it know provider quota epoch/reset facts? NO evidence of any; documented semantics
  give none.
- (3) Can it reactivate at/after a provider reset without a model already running? NO —
  nothing schedules future reactivation; resume is user-initiated.
- (4) Does it provide authorization/accounting semantics? NO — only per-run `spent_tokens`
  exposure; no authorization, no window accounting, no epoch identity.
- Conclusion: workflow lifecycle recovery and provider-quota continuity are SEPARATE concerns
  (G6 explicit). Native Workflow alone does not provide provider-reset-aware reactivation; the
  v2.4 re-baseline decides which minimal quota-resume semantics remain in Conductor and which
  provider-level clocking belongs in a separate companion project.

### WF-25 — User-facing invocation and ergonomics — PASS (with unknowns)

- Observed/documentable: programmatic entry (`CreateWorkflow`, `saved` runs by name), run
  cards with phase graph in UI, `/tasks`, Automations page, saved-workflow hub
  (`SaveWorkflow`/`ListSavedWorkflows`), generated scripts inspectable and editable in place
  (script path exposed; `AmendWorkflow` edits by file). NOT_EXPOSED from the main-session
  vantage: composer menu specifics, child-role visibility details in UI, resume/cancel UI
  affordances.
- Implication: GLM Conductor can keep workflow generation as an internal implementation
  detail (adapter-authored scripts); direct user exposure is optional and orthogonal.

## 4. Section 10.1 — Confirmed host facts

F1. Workflow scripts are TS compiled pre-launch; runs execute in background by default
    (EV-001, EV-014).
F2. `agent()` names are labels; plugin agent definitions are not consumed (EV-002).
F3. One model per run, validated against host-configured models; unconfigured ids rejected at
    submission; no silent fallback (EV-003).
F4. Every child has the full working toolset; no per-child tool restriction (EV-007).
F5. Children see session MCP servers (EV-007).
F6. Workflow children are spawned by a runtime-internal actor protocol and never traverse the
    main-session Agent|Task tool surface; plugin Pre/PostToolUse hooks never fire for them
    (EV-009, EV-004).
F7. Children have no Agent/Task tool; nesting is impossible (EV-008).
F8. Parallel fan-out genuinely overlaps; parent joins all; concurrency is runtime-adaptive
    under rate limits (EV-010, EV-014).
F9. Shared workspace, no isolation; disjoint writes safe in practice; same-file conflicts are
    silent last-writer-wins with no detection (EV-011, EV-012).
F10. Staged execution via script control flow; stage B consumed stage A's repo artifact
     (EV-013).
F11. Typed results and the final return reach the main session with full fidelity; run records
     expose identity, task text, tokens, health, errors (EV-001, EV-004, EV-013).
F12. TaskStop → stopped(model), same run id, in-flight ask marked unfinished; no partial writes
     survived; stop on completed run is a safe no-op (EV-015).
F13. Resume: same run id; completed asks replay without side-effect re-execution; unfinished
     asks re-dispatch live; repo re-read live (EV-016).
F14. Child output violations auto-retry to schema conformance (no deterministic child failure
     by instruction); script-level throw → run errored with exact message (EV-017).
F15. Prompt content (incl. GLM_CONDUCTOR markers) reaches children verbatim (EV-009).
F16. Provider-quota awareness, reset scheduling, authorization/accounting: absent from the
     workflow surface; provider errors stop runs for user resolution (EV-020).

## 5. Section 10.2 — Architecture-relevant constraints

| Fact | Affected current module | Consequence |
| --- | --- | --- |
| F2 plugin agents not addressable | role selection; agents/*.md frontmatter | adapter must inline role personas; plugin agent registry bypassed on the workflow path |
| F3 no per-role model binding | agent model fields; flagship reviewer route | flash/flagship split inexpressible; on this host the flagship route cannot run via workflows at all |
| F4 no per-child tool enforcement | reviewer read-only guarantee (G5) | read-only becomes persona-only; stop-gate ownership checks (Layer A) and receipts become the real guarantee |
| F6 no hook surface on workflow children | dispatch_wave permit gate/consume; agent_run journaling; policy.py Bash gate | deterministic enforcement must move into the conductor-authored script; hook plumbing stays legacy-path only |
| F9 silent last-writer-wins | lease.py, ownership.py, dispatcher gates | conflict guard CANNOT be reduced; adapter must check leases/ownership before asks |
| F13/F16 resume ≠ reconcile; no quota continuity | reconcile.py, recovery.py, quota/* | lifecycle recovery and quota continuity remain separate conductor layers |
| F14 no deterministic child failure | dispatcher failure paths, PostToolUseFailure bookkeeping | failure accounting must be gate-driven (script validation), not event-driven |

## 6. Section 10.3 — Native-workflow adoption boundary

| Layer | Owner after re-baseline | Evidence refs | Notes |
| --- | --- | --- | --- |
| Routing policy | CONDUCTOR | EV-002, EV-003 | Delegability × Assurance unchanged |
| Task decomposition contract | CONDUCTOR | EV-009 | five-part spec / TASK CONTEXT PACK via ask instructions |
| Work-unit dependency execution | SHARED VIA ADAPTER | EV-013 | host stages execute; unit readiness decided by conductor logic in-script |
| Parallel scheduling | HOST | EV-010, EV-014 | bounded fan-out + adaptive concurrency; budget supplied by conductor |
| Child-agent launch | HOST | EV-001, EV-009 | mechanics only; authorization stays in-script (conductor-authored) |
| Child lifecycle tracking | SHARED VIA ADAPTER | EV-004, EV-016 | host run records + conductor correlation into agent_run/journal |
| Ownership conflict protection | CONDUCTOR | EV-012 | host offers nothing; leases/ownership irreplaceable |
| Verification execution | CONDUCTOR | EV-017 | workflow success ≠ repo correctness |
| Verification receipts | CONDUCTOR | EV-017 | provenance receipts unchanged |
| Review topology | CONDUCTOR | EV-002, EV-003, EV-007 | fresh-context reviewer feasible; read-only persona-only; flagship model unbindable |
| Review receipts | CONDUCTOR | EV-017 | unchanged |
| Final acceptance | CONDUCTOR | EV-013 | main session consumes structured results; unchanged |
| Workflow interruption recovery | HOST (execution) / CONDUCTOR (task recovery) | EV-016, EV-018 | ask-level replay vs repo-authoritative triage |
| Provider quota continuity | CONDUCTOR | EV-020 | explicitly separate layers (G6) |
| Global quota clock | CONDUCTOR | EV-020 | unchanged |
| Task wake bridge | CONDUCTOR | EV-020 | unchanged |

## 7. Section 10.4 — Module disposition summary

Full matrix: `ZCODE_3_14_NATIVE_WORKFLOW_MODULE_DISPOSITION.tsv` (31 rows). Summary:

- **KEEP** (17): work_unit, dispatcher, lease, ownership, reconcile, execution_policy,
  fingerprint, state, journal, durable_io, recovery, resume_manifest, provenance,
  scheduler_facts, activation_transport, quota/* (22 files), stop_gate, session_start,
  routing/contracts/receipts. Every retained module protects an invariant with no host
  equivalent (verification semantics, repo-authoritative recovery, conflict protection,
  quota continuity).
- **KEEP_AND_ADAPT** (6): dispatch_wave, task_manager, agent_run, dependency, policy.py,
  pre/post_tool_use permit paths — invariants remain necessary; integration points move to
  the adapter script (permit facts checked in-script, journal events written by adapter,
  ids correlated).
- **REPLACE_BEHIND_ADAPTER** (1): implementation-agent launch plumbing (the execution
  substrate only) — host supplies launch/parallel/stage/background mechanics; blockers G1/G2
  prevent wholesale migration; legacy path retained.
- **REMOVE_CANDIDATE**: none. **DEPRECATE_AFTER_FALLBACK_WINDOW**: none proposed now; the
  hook-based permit consume path is the only future candidate if a v2.4 workflow-only launch
  window proves stable.
- No disposition relied on documentation alone; REMOVE-level claims require observed evidence
  that was not obtained (and was not sought for these modules).

## 8. Section 10.5 — Recommended v2.4 architecture direction: **B**

**Native workflow is suitable only for a constrained subset.**

Driving facts: execution mechanics are excellent and host-native — background runs, genuine
parallel fan-out with adaptive concurrency, staged dependency, typed full-fidelity handoff,
stable run identity, journal-accurate replay on resume, rich observability (F1, F8, F10, F11,
F12, F13). But four architecture-critical gaps block primary-substrate status: plugin agents
are not addressable (F2); per-role model binding is inexpressible (F3) — fatal for the
flagship reviewer on this host; per-child tool restriction is not enforced (F4); and no
deterministic enforcement boundary exists outside the conductor-authored script (F6/F9/F14).
Outcome A is impossible (G1 fails, G2 partial). Outcome C is contradicted by the mechanics
evidence (G3 satisfiable with retained conductor guards; G4 passes). Outcome D is unnecessary:
the evidence is sufficient to bound the subset precisely.

Constrained subset where a v2.4 `NativeWorkflowAdapter` is technically justified: delegated,
assurance ≤ medium, non-audit units executed by flash-family roles, with conductor-authored
scripts that (a) inline role personas, (b) enforce permit/lease/ownership gates in-script
before each ask, (c) correlate run/site ids into agent_run + journal, (d) rely on the existing
provenance/stop-gate for all verification and acceptance. `audit`/`full` routes and any
flagship-reviewer path stay on the legacy substrate.

## 9. Section 10.6 — Required v2.4 implementation prerequisites (NOT part of this spike)

1. Adapter design RFC: in-script enforcement idioms for permits/leases/ownership (G2/G3
   mitigation), including a permit-fact read path usable from workflow scripts.
2. Role persona inlining strategy with drift protection against `agents/*.md` (single source
   or generated) — resolving F2 without duplicating prompts by hand.
3. Model-binding decision: either accept flash-only execution on single-model hosts or define
   the legacy-path carve-out for flagship roles (F3).
4. agent_run/journal correlation schema for workflow run ids, site ids, and step attempts (F11,
   F13) with zombie semantics mapped to `unfinished` leftovers.
5. Gate-driven failure accounting replacing event-driven assumptions (F14): script validation
   results as the failure record.
6. Continuity boundary documentation: workflow resume adopted ONLY as execution recovery;
   Quota Epoch / Global Quota Clock / Task Wake Bridge unchanged (F16).
7. Re-installation hygiene before any live re-baseline: plugin cache staleness (2.0.1 vs
   2.3.2) and the `python3`-stub hook executability issue must be fixed, then WF-06/07 hook
   conclusions re-validated on a session where hooks actually spawn (this spike's hook
   evidence is structural, and should be re-confirmed once hooks run).

## 10. Known unknowns and blocked experiments

- WF-18 live app-restart survival: BLOCKED (needs user-assisted restart); documented
  `interrupted`→resumable semantics only.
- WF-19 mid-run permission prompts: UNKNOWN in this permission mode (15/15 auto-approved).
- WF-23 natural quota exhaustion: UNKNOWN (not manufactured, per plan).
- WF-05 mid-session MCP changes; CAP-26 hot-reload; CAP-28 live escalation; cancel
  idempotency beyond completed-run no-op: UNKNOWN (low architecture impact).
- Hook-firing observations are corroborated-absence only in this session (hooks inoperable —
  §2.3); the structural bypass argument carries the conclusion.

## 11. Closeout checklist

- [x] exact ZCode 3.14.0 build/environment recorded (3.14.0.7681, win32, single-model host)
- [x] WF-00…WF-25 each has PASS/BLOCKED/UNKNOWN + evidence refs (§3)
- [x] capability matrix complete (`…CAPABILITY_MATRIX.tsv`, 28 rows)
- [x] module disposition matrix complete (`…MODULE_DISPOSITION.tsv`, 31 rows)
- [x] no speculative SUPPORTED claims (every SUPPORTED cites OBSERVED evidence)
- [x] no production runtime behavior changed (probes + docs only; production untouched)
- [x] no secrets committed (no credentials probed; MCP servers listed by name only)
- [x] validator passes (15/15, scripts/validate_plugin.py)
- [x] smoke load passes (scripts/smoke_plugin_load.py, 0 failures)
- [x] focused spike-support checks pass (probe scripts compile via CreateWorkflow pre-launch
      compilation, 15/15 clean; spike assets touched by no production test)
- [x] full regression run once at closeout: 2385 passed + 565 subtests with a python3 shim
      (without shim: 2381 passed + the 4 environment-only python3-stub failures documented in
      §12; pre-existing on main in this environment)
- [x] results document selects A/B/C/D (§8: B)
- [x] `docs/architecture.md` unchanged

## 12. Implementation return

```text
commit: (see branch review/zcode-3.14-native-workflow; SHA recorded in the handoff message)
zcode_version: 3.14.0 (build 3.14.0.7681)
results:
  - docs/reviews/ZCODE_3_14_NATIVE_WORKFLOW_RESULTS.md
  - docs/reviews/ZCODE_3_14_NATIVE_WORKFLOW_CAPABILITY_MATRIX.tsv
  - docs/reviews/ZCODE_3_14_NATIVE_WORKFLOW_MODULE_DISPOSITION.tsv

recommendation: B

architecture_critical_unknowns:
- WF-18 live restart survival (BLOCKED; documented-interrupted resumable only)
- WF-19 mid-run permission-prompt topology (UNKNOWN in this mode)
- WF-23 natural quota exhaustion behavior (UNKNOWN; documented provider-stop semantics)
- hook firing on workflow children in a session where hooks CAN spawn (this spike: structural
  bypass; live re-confirmation pending environment fix — §9.7)

key_host_facts:
- plugin agents not addressable; names are labels (EV-002)
- one model per run; unconfigured ids hard-rejected; no silent fallback (EV-003)
- no per-child tool enforcement; reviewer read-only is persona-only (EV-007)
- no hook surface for workflow children; actor-protocol spawn bypasses Agent|Task (EV-009)
- same-file conflict = silent last-writer-wins, undetected (EV-012)
- resume = same run id; completed asks replay without side effects; unfinished re-run live (EV-016)
- child output violations auto-retry to conformance; failure is gate-driven (EV-017)
- provider quota continuity absent from workflow surface (EV-020)

candidate_replacements:
- implementation-agent launch plumbing → NativeWorkflowAdapter (constrained subset only)
- dispatch-wave staging mechanics → workflow phases (unit semantics retained)

must_keep:
- dispatcher/lease/ownership (conflict + admission policy; EV-012)
- provenance receipts + Stop gate + fingerprint (verification is conductor-side; EV-017)
- reconcile + recovery (repo-authoritative; host replay is journal-trusting; EV-016)
- quota continuity stack (G6: separate layers; EV-020)
- routing policy, five-part contract, TASK CONTEXT PACK, review receipts

validator: PASS 15/15 (scripts/validate_plugin.py, miniconda python 3.13.12)
smoke_load: PASS 0 failures (scripts/smoke_plugin_load.py)
focused_tests: PASS (probe scripts compile via CreateWorkflow pre-launch compilation, 15/15)
full_regression: closeout run once: 2381 passed + 565 subtests passed, 4 failed — all four are
  environment-only baseline failures (tests invoke `python3 -c …` via cmd.exe shell=True; PATH
  python3 is the Microsoft Store stub, exit 9009 — the same EV-006 condition). With a python3
  shim on PATH the FULL suite re-run: 2385 passed + 565 subtests passed — fully green. The
  spike added only untracked new files (docs/reviews/*, scripts/spikes/*) and touched no
  production code; the 4 failures exist on main in this environment.
production_runtime_modified: no
```
