# Post-v2.5.2 Reliability Fix & Model Selection Rebaseline — Implementation Specification

- **Status:** IMPLEMENTATION SPECIFICATION — OWNER-ALIGNED DESIGN / NOT IMPLEMENTED
- **Date:** 2026-10-13
- **Repository:** Chengy257/glm-conductor
- **Baseline:** main @ 1035fad6ed9260f7efeef9b9c6237ebd0677a120 (v2.5.2)
- **Delivery:** local Codex implementation; this commit is documentation only, **not** implementation, verification, or a new release
- **Input:** owner-provided "v2.5.2 发布后全量审查报告（2026-10-13）"; existing v2.5.2 architecture, runtime, and host-probe evidence

## 0. Authority, scope, and decisions

This specification defines the **next implementation after v2.5.2**, without rewriting the historical completed-release document:
- docs/roadmap/V2_5_2_PRECISE_WAKE_AND_MODEL_ADAPTATION_PLAN.md (v4.1 terminal design)
- docs/reviews/WAKE_TOPOLOGY_PROBE_RESULTS.md
- docs/reviews/ZCODE_3_14_NATIVE_WORKFLOW_RESULTS.md
- plugins/glm-conductor/skills/continuity/SKILL.md and references/long-horizon.md
- plugins/glm-conductor/skills/orchestration/SKILL.md and references/role-contracts.md

**Owner decisions controlling this implementation:**
1. **One outward-facing reset_at, not two newly designed reset concepts.** Quota status (AVAILABLE/PRESSURE/EXHAUSTED, plus UNKNOWN defense) describes available capacity, **not** permission to surface reset_at or arm precision sleep.
2. Preserve the **original dual-channel continuity**: native ZCode recurring Scheduled Task (floor; user-chosen interval, default 1h) **plus** one background terminal sleep to the observed reset_at + 300s (precision). Both are armed **immediately after auto-continuity authorization and before CreateWorkflow**. The floor is the restart-resilient fallback; precision handles silent exhaustion between model turns.
3. Do **not** wait for EXHAUSTED to arm; by that time the model may not be executable. Observed reset may be rechecked and corrected during later operating/waiting/wake turns when possible, never assumed to be available at the exhaustion edge.
4. Keep quota status evaluation, manual/auto authorization, bounded max_resumes, same-run resume, confirmation-before-counting, no questioning in unattended turns, one-automation-per-session host limitation, DAG/compiler, route/assurance, and host/runtime separation.
5. Main model inherits the **host main conversation**; recommend a strong planning/review model, never require a particular version. Subagent model IDs are **discovered live from host ListModels**, all role-capable configured candidates must be shown in the single pre-implementation interview, and **the user explicitly selects/authorizes** even with one candidate. Prefer Flash as advice, **never** as the hard admission rule; no model family, version, account name, or suffix may be an authorization hard gate.
6. User-authorized per-task model selection survives resume. Changed/unavailable/incompatible model → fail closed and await interactive reauthorization; an unattended wake turn must not ask or switch silently.

**Priority:** correctness of durable state + arm-before-work > model flexibility > documentation hygiene. Changes are narrowly targeted; **no** new quota state machine, standalone scheduler, provider account registry, cloud executor, workflow DAG redesign, automatic model switching, or per-node model binding.

### 0.1 Conflict / precedence reconciliation

The v2.5.2 plan contains a concrete implementation contradiction: D0/F8/§2.2 require launch-armed precision sleep even when capacity is available, but §2.5 and currently deployed CLI filter reset_at through EXHAUSTED-only windows. In this follow-up specification **the owner-reconfirmed D0/F8/arm-before-work requirement takes precedence** over that filter and related narration. Implementers must revise current operational contracts, NOT erase the historical v2.5.2 plan or claim it was implemented correctly.

The existing parser.plan_resume behavior for **post-exhaustion resume authorization**, including a conservative latest blocking reset and its existing resume_at field, is **not** being replaced by a second precision reset field. It remains a separate existing decision result; it does **not** control whether the one observed reset_at is surfaced or a preventive sleep is armed.

## 1. WP-R1 — Durable state concurrency (audit P1-1)

**Finding:** runtime/state.py save_state performs load → transition-check → atomic_write_json without a lock spanning the callers' earlier load/mutation. Atomic replacement prevents torn JSON, **not lost updates**. Concurrent scheduled wake versus record-validation/task-complete may erase fields, counters, or silently restore terminal state.

**Required implementation:**
- Provide a **strict, per-task state read/modify/validate/write transaction** under the existing durable_io exclusive lock for the target state.json. Lock **before reading**; inside lock load current bytes strictly (missing is allowed only where explicitly permitted; malformed JSON/schema is an error), check task identity + terminal/transition rules, apply an operation-specific mutation to **fresh state**, validate the final state, and atomically replace it.
- Migrate **all state-changing entry points**, not just save_state: task creation, status transitions/reopen, phase, validation, review, run registration, quota authorization/wait/resume confirm/counter, automation bind/clear, worker model pin, terminal complete/fail/cancel. A stale entire-state dict must never overwrite unrelated newer fields.
- Do **not** apply durable_io.atomic_update_json naïvely: its atomic_read_json treats corrupt input as default; the state ledger currently guarantees corrupt state is not overwritten. Use exclusive_lock + strict state reader + atomic_write_json, or extend a strictly opt-in helper preserving corruption rejection.
- Preserve same-terminal idempotence; completed/cancelled/failed cannot silently reopen; only explicit reopen emits task_reopened. Serialize explicit reopen through the same lock. Avoid nested lock acquisition/deadlock across wrappers; establish one owner of each transaction.
- Existing journal and workflow-run files stay separate artifacts. Append journal only after successful state commit and preserve established event names. Document crash-between-ledger-and-journal handling honestly; do not advertise multi-file ACID.
- Keep Python 3.7 compatibility, Windows lock behavior, original CLI JSON/exit contracts where not intentionally corrected.

**Acceptance:** simultaneous writers updating independent fields retain both updates; simultaneous completion versus validation cannot revive a terminal task; concurrent quota resume confirm cannot lose budget increments; corruption is rejected without replacement; repeated same-state replay remains idempotent.

## 2. WP-R2 — Single reset_at observation, unconditional visibility, proactive two-channel arming (audit P1-3)

### 2.1 Preserve existing quota classification

Do not remove or collapse AVAILABLE/PRESSURE/EXHAUSTED/UNKNOWN evaluation, existing window evidence, thresholds, provider parsing, or scheduled_activation_decision action semantics. **The classifier never gates reset_at exposure or preventive arming.**

Source of truth is the provider's *observed* nextResetTime in the existing standard snapshot.windows[].reset_at. Never infer a new boundary by adding a hard-coded five-hour duration, never manufacture a missing timestamp, and never use “EXHAUSTED only” for reset extraction.

### 2.2 Exactly one published reset_at; deterministic window choice

Retain the already public, singular **quota_observation.reset_at** / **decision.reset_at** field. Do not introduce next_window_reset_at, blocking_resume_at, reset_time_v2, or a second external reset timestamp.

There may be multiple raw provider windows (five_hour and weekly). For the *precision sleep anchor* choose the **earliest valid future observed reset_at among recognized windows**, independent of their AVAILABLE/PRESSURE/EXHAUSTED/UNKNOWN status. The next observed window boundary is a safe wake/checkpoint; a wake does not imply the exhausted constraint has cleared. On each wake, re-observe and rearm toward the next valid future boundary. This avoids incorrectly targeting a nonexhausted weekly window days away and does not change plan_resume's existing conservative blocking-window calculation. This window-selection rule is a *proposed deterministic implementation of the owner's “current window reset_at”*, not an assertion that the provider has only one raw window.

- Normalize/compare timestamps as UTC instants, not raw lexical strings if formats vary.
- Use only recognized, parseable, **future** timestamps. On already-past/invalid/stale observations, force refresh once; if still not available set reset_at=null and disclose **floor-only degraded precision**, rather than invent a reset.
- If both recognized windows are present, one is already expired and one is future, pick the future; if both future, choose earliest. Re-observe at subsequent wake; never assume the earliest wake means quota is available.
- Keep status, source, observed_at and reset_at as the existing four-key quota view. Source age/cache semantics remain observable.

### 2.3 Runtime changes (minimal)

**plugins/glm-conductor/runtime/cli.py**
- In _quota_observation_view, remove the window.status == "EXHAUSTED" filter and “max of blocking windows” calculation. Evaluate the full normalized snapshot, retain status and source, extract one future observed reset_at by §2.2. Keep null explicit if unsupported/unknown.
- In _quota_resume_decision, copy the returned decision and **always** add decision["reset_at"] = view.get("reset_at"), for **all actions**, including no-op, remain-waiting, waiting-user and resume-authorized, irrespective of quota status. Preserve existing decision.action, reason, workflow_run_id, resume_count_after, and side effects.
- The quota-wait observation view also carries the same status-independent reset_at.
- To arm immediately after authorization, the main session must invoke an existing CLI-visible quota observation path usable before any task is waiting. Prefer reusing quota-resume-decision (now unconditional, including active/no-op) with --force-refresh; do not invent a new scheduler service. If an explicit small read-only CLI command is proven necessary, justify and document it, and leave task decision behavior unchanged.
- Do not change runtime/task.py's availability/resume state machine for this issue, and do not modify parser.plan_resume's existing conservative exhausted-case action/resume_at rule.

### 2.4 Launch runbook — authorization must arm precision before work

In orchestration §6.0 / §6.2 and continuity SKILL:

~~~text
INTERACTIVE SINGLE-ROUND INTERVIEW
  → user authorizes auto continuity and bounded max_resumes (if enabled)
  → task-create
  → quota-resume-authorize
  → IMMEDIATELY obtain fresh quota_observation.reset_at (all statuses)
  → ZCode native recurring CronCreate (user-confirmed interval, default 1h)
  → quota-automation-bind(real ID)
  → quota-continuity-preflight PASS
  → launch background terminal sleep toward observed reset_at + 300s
     (when a valid future reset is observed; record/verify host acceptance)
  → role model pins from the SAME prior interview are applied
  → v24-compile / workflow-model-select / writer-acquire
  → CreateWorkflow(subagent_model=authorized exact ID)
  → record workflow run via canonical task CLI
~~~

The **two protection channels are established in the same launch batch, before any substantive delegated implementation**. The exact internal order of obtaining the observation and creating the floor may be rearranged only if this invariant survives. A success statement must distinguish (a) auto authorization, (b) bound native floor, (c) actually launched precision sleep. An unusable reset_at or host background-process refusal must be surfaced honestly: floor-only is a visible degraded condition, not “precision armed”. When auto floor creation/preflight fails, do not claim automatic continuity or start an auto-assured Workflow; preserve existing manual/block behavior.

Use the current references/long-horizon.md Bash sleep template (the host computes the delta, not the model). Guard nonpositive deltas and observe background-process acceptance. No new native Cron one-shot or CronUpdate is introduced.

### 2.5 Wake / park / recovery protocol

- Both native recurring floor and background sleep invoke **the same** existing wake protocol, whose first action remains quota-resume-decision --force-refresh.
- Every wake decision carries reset_at, even when action=no-op. The model must check whether a suitable precision sleep is already in flight, and for nonterminal, still-authorized auto tasks arm/rearm toward the **currently observed next future** reset, before advancing work. Avoid unnecessary duplicate sleeps; do not require a new persisted sleep state machine.
- On a still-executable quota exhaustion/parking turn, inspect the already-armed sleep; update its targeting if observed reset changed or it is absent. **Do not rely on a parking turn** to establish the first sleep.
- When the process has been lost through host/app restart, native recurring floor triggers the recovery check and can rearm precision sleep. Do not claim a floor event itself was a precision-time wake.
- On successive wake turns, an observed earliest reset may have changed to a later rolling boundary: reobserve, use the new **observed** reset, arm again. A stale/past time cannot create a busy spin.
- Retain bounded auto-resume budget and confirm-before-counting. A reset timestamp is **not** an authorization to resume when decision does not allow it. waiting_user/terminal must not start unapproved new work; on completion follow existing cleanup. No user questions on unattended wake turns.
- Existing one-automation-per-main-session limitation and host post-fire modification restriction remain: do not add/change/delete the floor in the routine wake loop.

### 2.6 Required precision regression tests

1. AVAILABLE, valid five_hour reset → quota view and **no-op decision** expose reset_at and launch path arms sleep before CreateWorkflow.
2. PRESSURE with valid reset → same unconditional exposure and launch behavior.
3. EXHAUSTED waiting → same reset exposure; remain-waiting decision unchanged.
4. UNKNOWN with valid observed window reset → reset remains visible but UNKNOWN does not authorize resume.
5. Weekly reset later than five_hour → select earlier future five_hour; weekly earlier → select earlier weekly; repeated wake reselects based on fresh snapshot, not hard-coded +5h.
6. Invalid/missing/stale reset, all-past timestamps → refresh once, no fabricated reset, floor-only degradation reported, no negative sleep/spin.
7. Exhaustion happens between turns with no parking call → first sleep already armed; both wake channels converge to one idempotent decision.
8. Host restart drops sleep → floor wake recovers/rearms. Duplicate floor and sleep wake events do not double-confirm a resume.
9. Fresh wake returning AVAILABLE/no-op still exposes next reset and can rearm; recovering when weekly window remains blocking does not falsely resume.
10. No new CLI reset field, duplicate scheduler service, or quota state machine appears.

## 3. WP-R3 — Stop guard integrity and bounded deadline (audit P1-2, P2-1)

- hooks/stop_gate.py: structural failure/fail-open stderr consistently includes the **ENFORCEMENT DEGRADED** phrase contracted by skills/enforcement/SKILL.md and troubleshooting. Keep successful Stop hook exit 0, stdout block single-line JSON, and no host-blocking exception.
- Guard's complete git observation budget must fit **within** the configured hooks.json Stop timeoutMs=5000, including process startup, reads, error emission, and margin. Enforce a shared monotonic deadline; reuse git status/base results where safe instead of sequential 3s + 3s + 3s calls. Per-call configurable git timeout may never override the **remaining overall hook budget**.
- In a hanging git process, return a visible degradation warning **before** the host's hard 5s kill (rather than relying on kill-time output). Preserve conservative fail-open behavior and avoid unbounded retries.

**Acceptance:** slow/stuck git with controlled fake subprocess returns within hook budget, exits 0, emits correct degraded phrase and no false completion block; normal blocking path still emits one valid JSON line.

## 4. WP-R4 — Canonical Workflow run registration (audit P2-2)

- runtime/task.py already has record_workflow_run(repo_root, task_id, workflow_run_id) with reservation/ownership check, state.workflow_run_id, journal workflow_started, and writer-guard run-id linkage. Make this the **canonical CLI path** instead of invoking adapter.record_run directly from v24-record-run.
- Add a stable CLI entry (e.g. task-record-workflow-run <repo_root> <task_id> <run-id>) and update orchestration runbooks to call it. Keep v24-record-run as a documented legacy low-level adapter CLI only if needed for compatibility; never present it as the canonical task lifecycle entry.
- runtime/workflow/adapter.py: accept explicit repo_root/base_dir for record and load paths (compatibly, where possible). Store artifact in the task's **ledger repo_root**, not process CWD. Preserve original record schema and original lookup behavior for a caller explicitly using legacy CWD-only mode.
- Check the writer reservation prior to registering a delegated Workflow; failure must not record a false workflow_started event. Check multiple CWDs and separated effective repository root versus ledger root.

**Acceptance:** canonical command records run_id in state and repo-root adapter file, emits workflow_started and binds writer guard; invocation from arbitrary CWD succeeds using repo_root; missing/mismatched reservation rejects without a falsely successful registration.

## 5. WP-M1 — Model selection: user authorization and host-list binding

### 5.1 Main session model (soft advice)

- Remove the rule "main session must be GLM-5.3", its hard-failure branch, and inaccurate capability/version assertions from orchestration SKILL, role-contracts, agent descriptions, README/zh, etc.
- Host owns the main conversation model. Conductor **inherits it** and must not programmatically switch it. The launch interview **prompts the user to prefer a strong model** for architecture/planning/verification, without blocking when a different configured model is chosen.
- Never identify strength, multimodality or allowed role **solely** from version strings or names.

### 5.2 Live discovery and recommendation policy

- **Before the single launch interview**, call host ListModels or equivalent authoritative discovery; obtain current configured *exact provider-qualified* model IDs. Do not ship an embedded allowed-model list, fixed account path, version blacklist, or hard requirement for names ending in "-Flash".
- Show **all configured models relevant to each role**, with exact IDs and known grounded capability/availability notes. “Flash recommended for bounded worker cost/speed” is an explicit **soft recommendation**; users may select a non-Flash configured model satisfying that role.
- A model is eligible for a role only if actually configured and the host supports submission/required capabilities. No guessed “multimodal” certification from names; visual read must be actually supported/verified. Missing availability metadata is marked **unknown** rather than fabricated.
- **Even exactly one suitable worker candidate requires explicit user confirmation**. Zero candidates: explain and block that role, not silently inherit main model or choose another provider.
- For review/visual role(s) that will be used, show and confirm their choice **in the same interview**. Multiple role slots may share one explicitly authorized ID, but do not assume a run-scoped Worker model independently controls host Agent invocations.

### 5.3 Single interview and durable authorization

Keep the **one-round launch interview** and the existing conditional three topic families, rather than adding separate rounds:
1. Auto continuity yes/no and bounded max_resumes (when enabled).
2. Main-session strong-model recommendation + **all active subagent role/model choices, requiring user confirmation** (not just a multiple-Flash ambiguity). For solo tasks with no subagents, no irrelevant worker choice is required.
3. Native floor interval if auto continuity enabled (default 1h, original host constraint preserved).

Resolve route/role requirements, then discover models **before** asking. After answer, persist per-task explicit model authorization: retain existing worker_model for Workflow run-level worker (backward compatible) and add only the **smallest optional durable per-role model mapping** needed for separately invoked text reviewer, visual implementer and visual reviewer. Choose a single unambiguous source of truth for each role; if implementing an optional agent_models map, document its schema, migration/legacy precedence and exact role keys. No model ID becomes task identity. Do not turn the task ledger into a general model catalog.

- Existing worker-model-pin must accept a valid configured **non-Flash** exact ID under the new role-neutral syntactic check; no suffix-only validator.
- Submission model_policy must no longer say explicit-flash-required; use a user-authorized explicit-selection meaning, and update CLI contract/tests accordingly.
- For a task with a recorded pin, **normal --model invocation cannot bypass it**. An authorized change requires an explicit **interactive reauthorization/pin-update action**, with previous and new values visible and safely recorded. An unattended wake must never auto-repin.
- A task resumed with a missing legacy pin is **not** implicitly authorized to pick the single available model; when a new delegation/review submission requires a model, interactive reauthorization is required. Preserve loading of existing v2.4/v2.5 states and allow their current work to finish without forced migration.
- At each new submission (including newly created runs after recovery), re-fetch the host list and compare exact pinned ID. Absent/revoked/role-incompatible pin → reject and park/wait for user. No silent fallback, arbitrary override, or forced main-session inheritance.
- Pin is evidence of the interview decision **under the Conductor interaction contract**, not cryptographic proof of user consent; do not overstate that guarantee.

### 5.4 Native Workflow versus host Agent constraints

Host probe (ZCODE_3_14_NATIVE_WORKFLOW_RESULTS.md WF-02/WF-03):
- CreateWorkflow has **one subagent_model per run**, not independently per DAG node. Keep generated TypeScript + persona model-agnostic. **Do not add per-node model fields, dynamic DAG subruns, or fake per-role Workflow selection.**
- Omitted subagent_model inherits main/session model; normal delegated submission must pass the **authorized exact** model explicitly.
- Plugin Agent frontmatter bindings and native Workflow actor persona are distinct paths. Existing visual-implementer.md / visual-reviewer.md frontmatter binds GLM-5.3-Flash and specific account IDs; glm-reviewer has no model binding. Fix their documentation and binding behavior, but first **probe what the present host Agent invocation/registration actually supports**.
- If the host permits per-invocation explicit model or supported configuration, use the authorized exact ID. If it only allows static frontmatter, implement only a **host-supported, narrowly scoped model-configured role instantiation** after verification, or report a **visual/model-selection capability blocker**. Do not silently delete model from frontmatter and inherit the main text model; do not claim dynamic selection works without live probe evidence.
- Visual implementer/reviewer must actually inspect images; inability to bind a suitable model is **fail closed** for that role. Maintain current screenshot request/rounds, read-only review, assurance and final verdict standards.
- Never force reviewer to use Flash, nor silently promise cross-model independent review; independence continues to mean fresh context and tool isolation.

### 5.5 Model regression tests

- Host discovery contains old/new Flash models and non-Flash models → all configured role-capable IDs appear; soft ranking only; owner-selected non-Flash is accepted.
- Single worker choice is displayed/confirmed, never silently auto-assigned.
- Unconfigured ID, bare aliases where exact account-qualified ID is required, empty/invalid ID → rejected; future model version with an unfamiliar name but valid configured ID → accepted.
- Worker pin survives wake/resume; outdated pin fails closed. CLI --model cannot silently override task pin.
- Role-specific pin (visual/reviewer) remains separate from Workflow run-scoped worker and is revalidated at each call.
- No hard-coded GLM-5.3/GLM-5.3-Flash dependency in active runtime policy, SKILL hard gates, or static agent model selection.
- Visual model can actually Read image evidence before claiming support; unsupported host binding is reported blocked, never downgraded to guessed vision.
- Python 3.7 syntax and existing single JSON CLI output remain valid.

## 6. WP-C — P3 audit hygiene (batch after functional gates)

Address all seven owner audit P3 findings without redesign:
1. CLI error classification: distinguish corrupt state/schema (structural, documented exit 2 if that is the chosen published contract) from ordinary task lifecycle rejection (exit 1); make usage/docstrings/actual exit mapping identical and test it.
2. exit-waiting CLI documentation versus TASK_TRANSITIONS["waiting_user"] behavior: document the intended real transition, or explicitly restrict if there is a proven safety invariant; do not silently change semantics.
3. workflow/compiler.py sanitize node_id inserted in generated // comments so newline/control input cannot break comment boundaries; deterministic source and compilability preserved.
4. runtime/quota/window_math.py unused implementation with contradictory earliest/min policy: remove dead code or mark retired so no accidental route into v2.5.2 latest-blocking semantics.
5. runtime/quota/_http.py include http.client.HTTPException family in classified provider failures; no bare exception leakage or credential leakage.
6. hooks/hooks.json and generated artifact comments: remove stale "v2.4" self-version where it purports to describe current version; prefer version-independent labels.
7. runtime/quota/_http.py user-agent version: synchronize with actual release version or use a stable version-agnostic label, without changing network behavior.

## 7. File/work-item map and local implementation order

| Phase | Work package | Primary implementation files / surfaces |
| --- | --- | --- |
| A | R1 state transactions | runtime/state.py, task.py, durable_io.py, journal.py (only where necessary) |
| B | R2 preventive reset / two-channel protocol | runtime/cli.py, quota/parser.py helpers if needed; skills/continuity/{SKILL.md,references/long-horizon.md}; skills/orchestration/SKILL.md; commands/quota.md |
| C | R3+R4 guard/run wiring | hooks/stop_gate.py, runtime/change_id.py, ownership.py, task.py, workflow/adapter.py, cli.py |
| D | M1 model rebaseline | runtime/workflow/submission.py, task.py, state.py, cli.py; orchestration skill/role-contracts; agents/*.md; host probe |
| E | P3 and docs/regression | affected modules; scripts/validate_plugin.py; README.md, README.zh-CN.md, docs/architecture.md, docs/core-concepts.md, docs/troubleshooting.md, CHANGELOG.md |

**Implementation discipline:**
- Complete one phase at a time; first state transaction safety, then precision arm-before-work, then guard/run closure, then role/model authorization, then hygiene. If local constraints demand a different order, keep functional and regression gates independent.
- Before coding, local implementer must re-read latest main/working branch and this spec; report any subsequent changes contradicting baseline. Work on a feature branch, do **not** overwrite historical release tags or inflate release version as part of drafting.
- Existing README, SKILL, CLI USAGE and tests **must match actual code** at the end of each merged functional phase. Do not declare implementation complete just because prose was changed.
- Host operations remain host-owned; pure runtime must not directly call CreateWorkflow/CronCreate/Agent, own credential selection, or implement a scheduler.
- No new gratuitous runtime abstraction, telemetry service, model registry, plan heuristics, token cost predictor, or unbounded agents. Use the smallest API/schema extensions strictly required by the accepted behavior.
- Prefer small commits per phase with exact test results, diff check, and explicit known host capability constraints.

### 7.1 Test/release acceptance

Execute the **actual local** tests/ suite if available, even though /tests/ is git-ignored by owner decision. The last uploaded audit observed **827 unittest tests OK and 16/16 validator** on v2.5.2; these are baseline *historical results*, NOT proof the new changes pass. If local tests are missing, reconstruct the minimal focused regression suite before claiming release-readiness; record reproducible test inputs/results rather than assuming those 827 still exist on a clean checkout.

Minimum gates per phase:
- Ruff/lint on modified Python, Python 3.7 compatibility (no modern syntax regressions).
- Focused tests for concurrency/corrupt ledger, precision AVAILABLE + silent exhaustion + multiwindow + floor, Stop timeout fail-open, canonical run registration, user-authorized non-Flash model and pin persistence.
- scripts/validate_plugin.py validation with updated anchors (not blindly 16/16 if anchors increased), scripts/smoke_plugin_load.py, local full suite.
- Reproduce essential live host probes for Cron floor creation + background wake capability, ListModels and selected-model CreateWorkflow, and Agent vision binding. Mark an unrun/unavailable host probe **UNVERIFIED** and do not claim it passed.
- Review diff for secrets, identity leakage, unauthorized model override, silent state fallback, stale model names, and bypass paths.
- Final report shall have: changed files and commits; tests and counts; verified host capabilities; known host blockers; explicit behavior for single-reset and two-channel launch; review verdict; release/no-release decision.

### 7.2 No-go criteria

- AVAILABLE does not expose reset_at or first precision sleep is not armed **before** task implementation when a valid fresh reset exists.
- Only EXHAUSTED permits reset exposure, or status decides whether to arm.
- A second public precision reset field/state machine is introduced instead of the existing one.
- Concurrent save overwrites validation, quotas, model pins, or silently reopens terminal state; corrupt JSON is replaced with defaults.
- Any delegated model is auto-selected without owner interview, or a non-Flash configured role-capable model is rejected only for its name/suffix.
- A missing pin is bypassed, or configured ID changes during resume without user authorization.
- Visual verification claimed without actual image-capable host binding and inspection.
- Host cron/recovery works by invented command/API or the implementation relies on a model turn *after* silent exhaustion to create the first sleep.

## 8. Local Codex delivery instruction

> Implement this document against the latest Chengy257/glm-conductor main on a dedicated feature branch. Preserve the original two-channel continuity, expose the **single** observed reset_at irrespective of quota state, arm the precise background sleep **at authorization time** before CreateWorkflow, and apply the owner-confirmed host-discovered/user-authorized model selection policy. Fix P1/P2/P3 audit findings in the ordered WPs above; do not redesign Workflow/DAG or add a quota state machine. For each WP, report concrete diffs and local test evidence. Do not claim completion without actual runtime/host verification, especially where ZCode Agent model binding is host-limited.

## 9. Sign-off boundary

**This document is the implementation specification, not evidence of implementation.** The original v2.5.2 release and historical probe results remain intact. Implementation closeout and a subsequent release require actual code changes, test evidence, and independent final review.
