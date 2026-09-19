# ZCode 3.14 Native Workflow Compatibility Spike & Re-baseline Review Plan

> **Status:** active implementation/review plan; **non-authoritative** with respect to the current runtime architecture.
>
> **Host target:** ZCode 3.14.0 (released 2026-09-19).
>
> **Repository baseline:** `main` at/after v2.3.2; planning baseline commit `2795c0cef2f5484716c54238721248998b63bc34`.
>
> **Purpose:** characterize the actual ZCode 3.14 native workflow capability boundary, then use observed evidence to decide which GLM Conductor execution-orchestration responsibilities can be delegated to the host in a future v2.4 re-baseline.
>
> This document is an implementation handoff for a compatibility spike. It does **not** change the frozen architecture in `docs/architecture.md`, does **not** authorize removal of legacy runtime mechanisms, and does **not** begin v2.4 implementation.

## 1. Why this spike exists

ZCode 3.14.0 introduces native dynamic workflows: a script can orchestrate multiple subagents, and workflows can be launched from the composer or `/workflow`. That capability overlaps with a significant part of GLM Conductor's current execution-orchestration layer.

The overlap is potentially concentrated in:

- multi-unit dispatch;
- bounded parallel fan-out;
- staged agent execution;
- background execution;
- result collection / join;
- interruption and resume behavior;
- agent-run identity and lifecycle observation.

The current GLM Conductor implementation provides these behaviors through its own Work Unit, dispatcher, dispatch-wave, permit, lease, agent-run, reconciliation, and runtime bookkeeping mechanisms. Before changing that architecture, we need host facts rather than assumptions.

The desired long-term boundary, if the evidence supports it, is:

```text
GLM Conductor
  routing policy
  task / role contracts
  deterministic evidence and acceptance
  quota-aware continuity
        |
        v
thin native-workflow adapter
        |
        v
ZCode workflow runtime
  subagent launch
  parallel / staged execution
  background workflow lifecycle
  host-native run management
```

This spike must determine whether that boundary is technically safe.

## 2. Scope and non-goals

### 2.1 In scope

The spike SHALL characterize:

1. the ZCode 3.14 workflow definition and invocation surface;
2. plugin custom-subagent addressability from workflows;
3. model and thought-level binding behavior;
4. tool and MCP inheritance;
5. plugin hook visibility for workflow-originated execution;
6. repository/workspace semantics for parallel writers;
7. workflow and child-run identity;
8. cancellation, failure, and resume semantics;
9. foreground/background behavior;
10. interaction with permission prompts;
11. interaction with session lifecycle;
12. behavior when the model provider becomes quota-blocked;
13. observability available to GLM Conductor without modifying ZCode internals;
14. exact overlap with existing GLM Conductor runtime modules.

### 2.2 Explicit non-goals

This spike SHALL NOT:

- implement the future `NativeWorkflowAdapter`;
- remove or disable `dispatch_wave`, permits, leases, reconciliation, or agent-run bookkeeping;
- alter the `solo/delegate/audit/full` routing model;
- alter Delegability × Assurance semantics;
- alter the five-part implementation contract;
- alter review semantics or Stop-gate evidence requirements;
- redesign quota epoch, subscription, consumption accounting, Global Quota Clock, or Task Wake Bridge;
- introduce a new workflow DSL into GLM Conductor;
- make production writes to undocumented ZCode databases for workflow control;
- treat a successful workflow status as equivalent to verified repository correctness;
- update `docs/architecture.md` with speculative conclusions.

Any architecture change belongs to the subsequent re-baseline decision, after this spike is complete and reviewed.

## 3. Truth and evidence rules

Every conclusion must be assigned one of these evidence classes:

| Class | Meaning | Acceptable evidence |
| --- | --- | --- |
| `DOCUMENTED` | current ZCode documentation or release notes explicitly state the behavior | URL + retrieval date + concise quotation/paraphrase |
| `OBSERVED` | behavior reproduced locally on ZCode 3.14.0 | exact probe, inputs, timestamps, logs/output, observed result |
| `INFERRED` | interpretation derived from documented/observed facts | explicit supporting fact IDs and explanation |
| `UNKNOWN` | not established | reason test could not establish it; do not guess |

Rules:

- Runtime behavior that affects architecture MUST be backed by `OBSERVED` evidence, even if documentation suggests it.
- Absence of evidence is `UNKNOWN`, not `NO`.
- A workflow reporting success is not proof that hooks ran, the intended model was used, or repository changes are correct.
- Model binding must be verified from an observable host/runtime record when possible; do not trust only the workflow script or agent frontmatter.
- Destructive host/database probing is prohibited.
- Raw evidence containing credentials or private prompt content must not be committed.

## 4. Required deliverables

The implementation agent must create the following before this review can close:

### D1 — Host fact sheet

Create:

`docs/reviews/ZCODE_3_14_NATIVE_WORKFLOW_RESULTS.md`

It must record:

- exact ZCode version/build;
- OS / execution environment;
- repository commit under test;
- active provider identity type, without secrets;
- workflow definition location and invocation mechanism observed;
- relevant ZCode configuration affecting subagents/workflows;
- whether the run was a fresh session after plugin/agent changes;
- all test-case results from Section 7;
- known unknowns and blocked experiments.

### D2 — Capability matrix

Create:

`docs/reviews/ZCODE_3_14_NATIVE_WORKFLOW_CAPABILITY_MATRIX.tsv`

Use the exact columns:

```text
capability_id	capability	required_for_target_architecture	evidence_class	status	host_surface	observed_behavior	conductor_dependency	decision_impact	evidence_ref	notes
```

`status` must be one of:

```text
SUPPORTED
SUPPORTED_WITH_CONSTRAINTS
NOT_SUPPORTED
UNKNOWN
NOT_APPLICABLE
```

### D3 — Existing-module disposition matrix

Create:

`docs/reviews/ZCODE_3_14_NATIVE_WORKFLOW_MODULE_DISPOSITION.tsv`

Use the exact columns:

```text
module_or_contract	current_responsibility	workflow_overlap	evidence_refs	proposed_disposition	confidence	blockers	v2_4_action
```

`proposed_disposition` must be one of:

```text
KEEP
KEEP_AND_ADAPT
REPLACE_BEHIND_ADAPTER
DEPRECATE_AFTER_FALLBACK_WINDOW
REMOVE_CANDIDATE
UNDECIDED
```

No module may be marked `REMOVE_CANDIDATE` from documentation alone; it requires direct observed evidence.

### D4 — Re-baseline recommendation

At the end of `ZCODE_3_14_NATIVE_WORKFLOW_RESULTS.md`, provide one evidence-based architecture recommendation using only these outcomes:

- **A — Native workflow is suitable as the primary execution substrate**
- **B — Native workflow is suitable only for a constrained subset**
- **C — Native workflow is not yet suitable; retain legacy orchestration**
- **D — Evidence is insufficient; additional host characterization is required**

The recommendation must identify the facts that drive the outcome. It must not begin implementation.

### D5 — Reproducible probe assets

Place spike-only scripts and fixtures under:

`scripts/spikes/zcode_3_14_workflow/`

Requirements:

- probe code must be clearly labeled non-production;
- no dependency on private credentials;
- no destructive file operations;
- no modification of ZCode's internal database;
- each probe should be runnable independently when practical;
- generated raw output goes to a local ignored evidence directory, not tracked by default.

Recommended local raw-evidence location:

`.glm-conductor/spikes/zcode-3.14-workflow/<run-id>/`

Commit only sanitized summaries and small fixtures needed for reproducibility.

## 5. Current GLM Conductor surfaces to review

The re-baseline must explicitly inspect at least the following current surfaces.

### 5.1 Likely high-overlap execution surfaces

- `runtime/work_unit.py`
- `runtime/dispatcher.py`
- `runtime/dispatch_wave.py`
- `runtime/task_manager.py`
- dispatch permit handling
- `runtime/agent_run.py`
- `runtime/reconcile.py`
- lease handling
- wave state / wave close lifecycle
- implementation-agent launch hooks
- background dispatch bookkeeping

These are candidates for host delegation, but **none are presumed obsolete**.

### 5.2 Likely retained policy / assurance surfaces

- Delegability × Assurance routing;
- `solo/delegate/audit/full`;
- executor/reviewer role selection;
- five-part implementation specification;
- TASK CONTEXT PACK;
- ownership contract;
- required verification contract;
- task/unit fingerprinting;
- verification receipts;
- review receipts;
- Stop completion gate;
- route reassessment;
- final acceptance by the main session.

The spike may identify adapter changes, but should not propose removing these responsibilities merely because workflows exist.

### 5.3 Continuity surfaces requiring separate judgment

- quota resolver and provider facts;
- quota phase;
- Quota Epoch;
- quota subscription;
- quota consumption accounting;
- Global Quota Clock;
- Task Wake Bridge;
- SessionStart recovery;
- workflow pause/resume, if present.

The central question is whether native workflow resume is only **execution lifecycle recovery** or also provides the **provider-quota-aware reactivation** semantics GLM Conductor needs. Do not conflate the two.

## 6. Execution discipline

### 6.1 Branch / working state

The spike should be performed from a clean branch based on current `main`.

Recommended branch name:

`review/zcode-3.14-native-workflow`

Do not mix unrelated feature work into the spike branch.

### 6.2 Test repository / fixture discipline

Use a disposable fixture area for workflow write experiments. Do not use production source files as concurrency test targets.

At minimum create fixtures that allow:

- two independent agents to edit disjoint files;
- two agents to attempt edits to the same file;
- one agent to fail deliberately;
- one agent to run longer than another;
- a staged second phase that consumes outputs from phase one;
- a read-only reviewer role;
- a probe that reports environment/run identity.

### 6.3 Minimal-test policy

This spike is host characterization, not a feature implementation.

During individual cases:

- run only the targeted probe and the smallest relevant existing test(s);
- do not run the full 2385-test regression after each case;
- do not add large golden-output suites for undocumented host behavior;
- prefer compact evidence logs over brittle text snapshots.

At final closeout only:

1. run the repository validator;
2. run focused tests for any spike-support code added under `scripts/spikes`;
3. run the existing full regression **once** to prove the spike did not alter production behavior.

Production runtime should remain unchanged, so any broad regression failure is a stop-and-investigate event.

## 7. Compatibility test matrix

All cases below are required unless marked conditional. Use the IDs unchanged in the results and TSV.

### WF-00 — Environment and feature discovery

**Question:** What exactly is the installed workflow surface?

Record:

- ZCode version/build;
- workflow UI entry points visible;
- workflow file/script location(s), if exposed;
- supported script/runtime language or schema;
- help text or generated starter template;
- whether project-local definitions are supported;
- whether user-global definitions are supported;
- whether workflow definitions hot-reload or require a new session;
- any workflow-specific logs/state visible through supported UI or files.

**Pass condition:** enough information exists to reproduce a trivial workflow from a clean session.

### WF-01 — Minimal deterministic workflow

Create the smallest workflow that:

1. launches one child agent;
2. performs a harmless deterministic task;
3. returns a result to the parent/workflow.

Record:

- invocation method;
- child agent type;
- start/end ordering;
- result object/text available;
- workflow status surface;
- run identifiers.

**Required conclusion:** establish the base callable model of the workflow runtime.

### WF-02 — Plugin custom-subagent addressability

**Question:** Can a native workflow explicitly invoke plugin-provided GLM Conductor agents?

Test at least:

- `glm-conductor:flash-implementer`;
- `glm-conductor:glm-reviewer`.

Record exact identifier syntax and failure behavior for an invalid identifier.

**Architecture-critical:** YES.

### WF-03 — Model binding and thought-level preservation

For each workflow-launched GLM Conductor role, verify the **actual** model/provider used.

Test:

- flash implementer → expected Flash-family binding;
- text reviewer → expected flagship binding.

Record:

- requested role;
- definition model field;
- actual model id;
- actual provider id;
- evidence source;
- whether workflow can override the role model;
- whether main-session model leaks through as a fallback.

**Architecture-critical:** YES.

Any silent fallback must be treated as a blocker until understood.

### WF-04 — Tool permission preservation

Test a read/write implementer and a read-only reviewer.

Verify:

- implementer can use intended write tools;
- reviewer cannot write;
- tool restrictions are preserved when launched from workflow;
- denied operations fail visibly.

**Architecture-critical:** YES for assurance topology.

### WF-05 — MCP visibility and session snapshot behavior

Using a harmless MCP/tool visibility check, establish:

- whether workflow children see MCP servers available at session start;
- whether mid-session connector changes become visible;
- whether behavior matches ordinary subagents;
- whether workflow execution creates a new capability snapshot.

Do not expose credentials.

### WF-06 — Hook coverage: workflow-level invocation

Instrument existing GLM Conductor hooks or a temporary spike-only hook marker.

Determine whether starting a workflow triggers any plugin hook surface and record exact hook names/events observed.

Do not infer child behavior from workflow-start behavior.

### WF-07 — Hook coverage: workflow-spawned child agent

This is one of the most important cases.

Determine whether a workflow-originated child dispatch passes through the same observable `Agent|Task` PreToolUse/PostToolUse surfaces currently used by:

- dispatch permit enforcement;
- implementation launch bookkeeping;
- review marker bookkeeping.

Test both implementer and reviewer child roles.

Record:

- whether PreToolUse fires;
- whether PostToolUse/PostToolUseFailure fires;
- whether tool input contains enough identity to map back to workflow/run/unit;
- whether `updatedInput` mutations are honored;
- whether deny decisions actually prevent the workflow child launch.

**Architecture-critical:** YES.

### WF-08 — Nested-subagent boundary

Confirm whether a workflow child itself can launch another subagent.

Expected ordinary-subagent baseline is “no nested subagents”; workflow behavior must be observed rather than assumed.

Record failure mode and whether workflow composition provides an alternative staged mechanism.

### WF-09 — Foreground parallel fan-out

Launch at least three independent children in one stage.

Record:

- whether they genuinely overlap in time;
- maximum observed concurrency;
- whether the parent waits for all;
- result ordering;
- failure semantics when one child fails;
- whether successful siblings are preserved.

**Purpose:** compare directly with current dispatch-wave semantics.

### WF-10 — Parallel repository writes: disjoint ownership

Two children edit disjoint fixture files.

Record:

- workspace view seen by each child;
- resulting working tree;
- whether writes interleave safely;
- whether the workflow serializes writes;
- whether any implicit checkout/worktree isolation exists.

This establishes whether current ownership + lease rules remain necessary.

### WF-11 — Parallel repository writes: conflicting ownership

Two children intentionally target the same fixture file with incompatible changes.

This is a controlled fixture-only conflict test.

Record:

- final file content;
- whether host detects/conflicts/serializes;
- whether one write silently wins;
- what information is available to detect the conflict.

**Safety purpose:** determine whether GLM Conductor's ownership/lease enforcement can ever be reduced.

### WF-12 — Staged dependency execution

Create a two-stage workflow:

- stage A produces deterministic artifacts/results;
- stage B starts only after A and consumes its output.

Record dependency syntax, status propagation, and available structured outputs.

**Purpose:** evaluate replacement potential for explicit Work Unit dependency orchestration.

### WF-13 — Main-session / workflow result handoff

Determine exactly what returns to the initiating session after completion:

- per-child result;
- aggregate result;
- stdout/stderr;
- changed-file metadata;
- run ids;
- timing;
- failure details.

Record what can be consumed mechanically versus only as model-written prose.

### WF-14 — Background workflow behavior

Run an eligible longer workflow in background.

Record:

- how background mode is selected;
- whether the parent turn can continue/finish;
- where progress is visible;
- how completion is delivered;
- whether repository writes continue;
- whether plugin hooks remain observable;
- what happens if the initiating session becomes idle.

### WF-15 — Cancellation semantics

Cancel:

1. before a child starts;
2. while one child is running;
3. while parallel children are running.

Record:

- child termination behavior;
- partial repository changes;
- run status;
- whether cancellation is idempotent;
- whether cleanup/recovery metadata exists.

### WF-16 — Failure and partial-success semantics

Construct a workflow where one child fails deterministically.

Record:

- fail-fast vs continue behavior;
- sibling behavior;
- workflow status;
- partial outputs;
- retry/resume options;
- ability to identify exactly which unit failed.

### WF-17 — Resume / retry identity semantics

If ZCode exposes workflow resume/retry, test it.

Record whether:

- the same workflow run id continues or a new run is created;
- completed children rerun;
- failed children rerun;
- repository state is re-read or replayed from cached context;
- child run ids are stable;
- parent session identity changes.

**Architecture-critical:** YES for replacing `reconcile.py` / agent-run recovery.

If no supported resume exists, record `NOT_SUPPORTED`, not an inferred workaround.

### WF-18 — App/session restart boundary

Conditional but strongly preferred.

With a non-destructive long/background workflow:

- close/reopen ZCode or otherwise reproduce the supported restart boundary;
- determine whether workflow state survives;
- determine whether execution resumes, fails, or becomes inspectable only.

Do not perform OS sleep/power experiments; that remains outside project scope.

### WF-19 — Permission prompt interaction

Trigger a harmless operation that requires permission.

Record:

- whether the whole workflow pauses;
- whether only one child pauses;
- whether siblings continue;
- how the pending approval is represented;
- behavior in background mode;
- behavior after approve/reject.

### WF-20 — Reviewer isolation topology

Run implementation followed by a fresh reviewer in workflow form.

Establish:

- reviewer receives fresh context rather than implementation conversation state;
- reviewer remains read-only;
- reviewer can be given explicit diff/evidence inputs;
- fix-first can lead to a new reviewer rather than reusing stale review context.

**Purpose:** determine whether `full` / `audit` topology can use workflow without weakening assurance semantics.

### WF-21 — Existing dispatch-marker compatibility

Attempt to preserve current GLM Conductor dispatch/review markers through a workflow child invocation.

Record:

- whether prompts can carry deterministic marker text;
- whether hook-visible inputs retain that marker;
- whether marker placement is stable enough for enforcement;
- whether workflow rewrites or abstracts the child prompt.

This case decides whether current permit/review-receipt plumbing can be adapted or must be redesigned behind a workflow adapter.

### WF-22 — Workflow run observability

Inventory supported, non-destructive observability:

- UI run list;
- run ids;
- child ids;
- statuses;
- timestamps;
- logs;
- structured files/state exposed by ZCode;
- any documented API/tool surface.

Classify every source as public/documented vs observed internal implementation.

Do not add production coupling to an internal store during this spike.

### WF-23 — Quota exhaustion during workflow execution

This case may require natural quota timing; do not intentionally waste large quota.

When a real quota-blocked condition is available, record:

- workflow status;
- child status;
- parent status;
- retry/resume affordance;
- whether host schedules future reactivation;
- whether a provider reset is understood by the workflow runtime;
- whether completed work is preserved.

If the condition cannot be reached responsibly, mark `UNKNOWN` and provide all non-destructive evidence available.

**Architecture-critical:** YES for continuity boundary, but an UNKNOWN result does not block execution-substrate conclusions.

### WF-24 — Workflow versus Global Quota Clock / Task Wake Bridge boundary

Using results from WF-17, WF-18, and WF-23, answer separately:

1. Does workflow resume recover execution state?
2. Does it know provider quota epoch/reset facts?
3. Can it reactivate at/after a provider reset without a model already running?
4. Does it provide the authorization/accounting semantics currently enforced by GLM Conductor?

This is an analysis case, not a new experiment unless evidence is missing.

### WF-25 — User-facing invocation and ergonomics

Record how a real user starts and observes a workflow:

- composer + menu;
- `/workflow`;
- any named workflow selection;
- visibility of child roles;
- progress/cancel/resume UI;
- whether generated workflows are inspectable/editable.

Purpose: determine whether future GLM Conductor should expose workflows directly or keep workflow generation as an internal implementation detail.

## 8. Re-baseline analysis

After all available WF cases are complete, perform a module-by-module review.

### 8.1 Questions for each current module

For every item in D3, answer:

1. What invariant does this module currently protect?
2. Does native workflow provide the same behavior, merely similar behavior, or no equivalent?
3. Is the host behavior documented, observed only, or unknown?
4. If the module were removed, what failure mode would become unprotected?
5. Can the invariant be kept while replacing only the execution mechanism?
6. Is a one-version fallback required?

### 8.2 Specific likely outcomes to evaluate

Do not preselect them, but explicitly evaluate:

- `dispatch_wave.py` → possible replacement behind adapter if WF-09/12/13 are sufficient;
- direct implementation-agent launch plumbing → possible adapter target if WF-02/07 are sufficient;
- dispatch permits → keep/adapt unless workflow exposes an equally strong deterministic launch boundary;
- leases/ownership → likely keep unless WF-10/11 prove equivalent conflict protection;
- `agent_run.py` → simplify only if WF-13/17/22 provide stable run identity and lifecycle;
- `reconcile.py` → simplify only if WF-15/16/17/18 prove safe repository-aware recovery;
- verification/review receipts → expected KEEP;
- Stop gate → expected KEEP;
- routing policy → KEEP;
- quota continuity → KEEP unless WF-23/24 establish genuinely equivalent provider-aware semantics.

“Expected KEEP” is a starting hypothesis, not permission to skip evidence review.

## 9. Decision gates

### Gate G1 — Workflow callable for our roles

Required:

- WF-02 supported;
- WF-03 no silent model fallback;
- WF-04 preserves permission boundaries.

If G1 fails, outcome cannot be A.

### Gate G2 — Deterministic enforcement remains possible

Required evidence from WF-06/07/21 must show either:

- existing hooks/markers remain enforceable; **or**
- a stable alternative workflow boundary exists where equivalent deterministic checks can be attached.

If neither exists, native workflow must not replace protected dispatch paths.

### Gate G3 — Parallel execution is repository-safe under our contracts

WF-09/10/11 must show that:

- disjoint work can execute safely enough for bounded parallelism;
- conflicting ownership is detectable/preventable by host or retainable GLM Conductor enforcement.

If conflict behavior is silent last-writer-wins and no deterministic guard can be retained, parallel workflow writing cannot become the default substrate.

### Gate G4 — Lifecycle is observable enough to recover

WF-13/15/16/17/22 must provide sufficient identity/status evidence to distinguish:

- never started;
- running/interrupted;
- failed;
- completed with repository effects;
- cancelled.

If not, legacy reconciliation remains required.

### Gate G5 — Assurance topology remains valid

WF-20 must show a fresh-context read-only reviewer can still be constructed and evidenced.

If not, audit/full routes cannot migrate wholesale.

### Gate G6 — Continuity boundary is explicit

WF-23/24 must produce either:

- observed provider-aware host continuity; or
- an explicit conclusion that workflow lifecycle recovery and quota continuity are separate layers.

Do not leave this ambiguous.

## 10. Required final recommendation structure

The results document must end with these sections.

### 10.1 Confirmed host facts

Only DOCUMENTED or OBSERVED facts.

### 10.2 Architecture-relevant constraints

For each constraint:

- fact ID;
- affected current module;
- consequence.

### 10.3 Native-workflow adoption boundary

Describe which of these layers the evidence supports:

```text
routing policy
task contract
execution orchestration
parallel scheduling
agent lifecycle
verification
review
acceptance
quota continuity
```

Use `HOST`, `CONDUCTOR`, or `SHARED VIA ADAPTER` for each.

### 10.4 Module disposition summary

Summarize D3 and list any modules that should enter a future deprecation window.

### 10.5 Recommended v2.4 architecture direction

Choose A/B/C/D from D4.

### 10.6 Required v2.4 implementation prerequisites

Only list work justified by the evidence. Do not implement it in the spike.

## 11. Stop conditions

Pause the specific experiment and record it as blocked/unknown if any of these occur:

- the probe would require deleting user data;
- the probe would require mutating ZCode database schema or unsupported internal rows;
- the probe would require exposing credentials;
- the probe would consume unreasonable quota solely to manufacture a quota-exhaustion case;
- workflow semantics appear version/build-dependent and the exact build cannot be identified;
- production GLM Conductor runtime must be changed merely to observe the host.

A blocked individual experiment does not invalidate the whole spike. Continue independent cases.

## 12. Closeout checklist

The spike is ready for architectural review only when all items below are true:

- [ ] exact ZCode 3.14.0 build/environment recorded;
- [ ] WF-00 through WF-25 each has PASS / FAIL / BLOCKED / UNKNOWN plus evidence refs;
- [ ] capability matrix complete;
- [ ] module disposition matrix complete;
- [ ] no speculative `SUPPORTED` claims;
- [ ] no production runtime behavior changed;
- [ ] no secrets committed;
- [ ] validator passes;
- [ ] focused spike tests pass;
- [ ] existing full regression run once at final closeout;
- [ ] results document selects A/B/C/D;
- [ ] `docs/architecture.md` remains unchanged unless a separate post-review decision explicitly authorizes re-baselining.

## 13. Handoff instruction for the implementation agent

Use the following execution order:

```text
1. establish clean baseline and exact host version
2. create spike fixtures/probe assets
3. execute WF-00..WF-08 (identity, roles, model, permissions, hooks)
4. checkpoint results
5. execute WF-09..WF-17 (parallelism, writes, failure, cancel, resume)
6. checkpoint results
7. execute WF-18..WF-25 as available (restart, permissions, assurance, quota, UX)
8. complete capability matrix
9. complete module disposition matrix
10. write A/B/C/D re-baseline recommendation
11. run final validator + focused tests + one full regression
12. commit results without modifying production architecture/runtime
```

If a later test disproves an earlier interpretation, update the earlier row and preserve the raw fact references; do not keep mutually inconsistent conclusions.

The implementation return to the architectural reviewer must include:

- commit SHA;
- ZCode exact build;
- D1/D2/D3 paths;
- one-paragraph summary of the A/B/C/D recommendation;
- list of architecture-critical UNKNOWN items, if any;
- final validator/full-regression outcome.
