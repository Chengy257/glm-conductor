---
name: continuity
description: GLM long-task continuity: same-run-id resume, waiting_quota parking, bounded manual/auto resume, arm-before-work dual-channel wake, rolling re-arm, terminal cleanup. Use when resuming work across sessions or quota windows, or unattended re-runs.
---

# GLM Continuity: Workflow Recovery and Bounded Re-runs

## Purpose

The routing decision (solo / delegate / audit / full) belongs to the orchestration skill; continuity answers one question — how unfinished work resumes. The two skills compose; neither replaces the other.

Continuity is built from four mechanisms, all centered on task state (state.json):

1. **Workflow recovery** — an interrupted delegated run resumes under the same run id.
2. **waiting_quota** — a deterministic parking point when quota is exhausted.
3. **manual / auto bounded resume** — resuming is a budgeted action under user authorization, never a model improvisation.
4. **arm-before-work native scheduling arming** — auto continuity arms before work: authorize → establish the floor carrier (kind-aware per the wake-carrier four-level matrix — L0 host CronCreate / L1 OS timer / L2 sleep-only witness) → bind → preflight PASS means armed; only then CreateWorkflow and any automatic resume.

Wake authorization, the worker model and the floor interval are decided by the mandatory launch interview contracted in the orchestration skill section 'Task launch interview'; the complete wake runbook lives in 'Launch sequence (runbook, arm-before-work)' below.

## Task state and runtime files

An active task (delegate/full, audit, and any long task needing completion-guard protection) maintains machine-readable state in a dedicated directory:

```
.glm-conductor/tasks/<task-id>/
├── state.json        # deterministic task state (the completion guard checks completion against it)
├── events.jsonl      # execution provenance (append-only, one event per line)
└── checkpoint.md     # navigation state (model-readable recovery narrative, for long tasks)
```

**state.json** (schema authority: `runtime/state.py`) top-level concepts: `task_id / goal / repository / route (mode + assurance) / dag / status / phase (optional descriptive label) / workflow_run_id / validation / review / quota_resume`.

- **status is exactly the seven states**: `active / waiting_quota / waiting_user / blocked / completed / cancelled / failed`; terminal = completed / cancelled / failed, and a terminal state is never silently reopened (explicit reopen goes only through `state.reopen_task`, with a journal record)
- Transitions follow the `state.TASK_TRANSITIONS` matrix exactly (e.g. waiting_quota → active / waiting_user / blocked / failed / cancelled); `completed` is committed only by the completion guard after all four checks pass, via `task.complete`
- Repository files remain the source of truth for code state; state.json is only runtime task state
- Atomic writes; writes go through the runtime.task lifecycle APIs, and the skill layer always uses runtime CLI named subcommands (`task-create` / `record-validation` / `review-record` / `quota-*` / `exit-waiting` / `task-complete` / `task-fail` / `task-cancel` — the authoritative command list is the USAGE in `runtime/cli.py`); never hand-build JSON write-backs and never use `python3 -c` inline scripts

**events.jsonl**: the append-only execution log. The task-level event vocabulary is exactly eleven names (`runtime.task.TASK_JOURNAL_EVENTS`):

| Event | When |
| --- | --- |
| route_selected | create_task creates the task (with mode / assurance) |
| workflow_started | record_workflow_run records a delegated run association |
| chain_block_delegated | chain-block-record ledgers a direct-dispatch chain block delegation (v2.8.0 D-C; block id / role / ownership summary; duplicate block ids are refused) |
| workflow_reassessed | after a route reassessment (recorded when the main session declares ROUTE REASSESSMENT) |
| validation_recorded | record_validation persists the main validation record |
| review_recorded | review-record persists a review verdict |
| waiting_quota | enter/exit_waiting_quota entering or leaving quota waiting (direction field) |
| quota_resume_confirmed | confirm_resume_started ledgers the staged resume count |
| task_completed / task_failed / task_cancelled | terminal closure (with from/to) |

Constraints: no secret values (keys, Authorization headers), no full prompts or full source; it is not telemetry.

**TASK_ID rule**: `<semantic prefix>-<random hex suffix>` (e.g. `redesign-settings-page-7f3a2c`), mechanically unique at generation and stable across recovery; the task directory, checkpoint, scheduled wake prompt and automation association all use the same exact ID. Full contract in references/long-horizon.md.

## Workflow recovery (same run id resume)

At delegated-task launch the main session persisted the run id into `state.workflow_run_id` (`record_workflow_run`). Conductor **never mirrors host execution state** — workflow progress is a host-owned fact (GetWorkflowRun can query it), and the records under `.glm-conductor/workflow-runs/` are only id association documents.

Recovery procedure (new session or after a wake):

1. **Discovery**: the SessionStart hook (matcher `startup|resume|clear|compact` — v2.7.0 W3 adds `resume`, so a Desktop reopen/session resume re-injects too) automatically injects `GLM CONDUCTOR RESUME CONTEXT` — one minimal line per unfinished task (task id / goal / repository / status / route / workflow run id / quota waiting state / next safe action). Purely local, zero model calls, fully silent when no task is active; after a crash no user reminder is needed.
2. **Reconcile**: first look at the repository's real state (git diff / worktree), then at state.json and the checkpoint — **repository > checkpoint > session memory**. Legacy tasks are reported per the injected one-line guidance (LEGACY_STATE_GUIDANCE: close out under it or explicitly abandon); never migrate them and never fabricate completion evidence.
3. **Resume**: if the task has a `workflow_run_id` and is not terminal → resume the Workflow through the host under the **same run id** (the host owns the execution state and resumes its own run); after recovery, wait for the structured result → main-session validation → closure as usual. The auto-wake path (mode=auto) adds two hard gates: decision `resume-authorized` **and** `quota-continuity-preflight` ready before a resume is allowed (see 'Unified wake protocol'); the writer guard is still held by this task (no expiry logic) — that is the same-run resume credential, and no re-acquire is needed.
4. **Workflow dead and unresumable** (the host reports the run missing / unrecoverable): rebuild understanding from the repository's current state — recompile and resubmit the parts of the DAG not yet implemented (reconcile and release the writer guard first, then the launch procedure of orchestration 'Execution procedure for delegate/full'); **never blindly replay completed node results**.
5. **Chain-position rebuild (direct-dispatch chain tasks, v2.8.0)**: a task running as a direct-dispatch chain recovers its position from the chain ledger, not from subagent state (there is none — block recovery semantics are re-dispatch from repository facts): tail-read the journal's `chain_block_delegated` events → load the task state (mode / assurance / non-terminal check) → reconcile against git/repository facts to determine which blocks actually landed → re-dispatch the next block per the chain doctrine (orchestration 'Direct-dispatch chain (low-delegability long tasks)'). This step composes with, and never bypasses, the quota decision gates: a chained task crossing a quota window still walks the same waiting_quota / quota-resume decision sequence first ('Unified wake protocol'); only after the decision authorizes continuation does the chain-position rebuild run.
6. **Route recovery**: before resuming execution, output the SELECTIVE ROUTE declaration again (same route, or reassessed on new evidence); if the evidence shows the dual-axis judgment changed, do ROUTE REASSESSMENT first.

## waiting_quota

The four-state quota observation (AVAILABLE / PRESSURE / EXHAUSTED / UNKNOWN) comes from the read-only surface `quota-resolve` / `report.py` (see 'Quota observation surface'). When a running task observes that quota cannot continue:

- **enter**: `quota-wait <repo_root> <task_id> [--force-refresh]` (runtime CLI; underlying `task.enter_waiting_quota`) — the CLI performs one quota observation (resolver.resolve_quota_detail) and passes the normalized observation into the task layer; the task transitions to `waiting_quota` (journal `waiting_quota` event, direction=enter) and the latest observation (status / reset_at / observed_at) is recorded into `quota_resume.last_observation` for diagnostics. The semantics are an **explicit parking command** after the task has hit a quota-related execution stop, not an automatic quota-failure prober.
- **Parking semantics**: waiting_quota is an intentional stop point — the completion guard lets a parked task pass; the turn does not close out and does not dispatch.
- **Never fabricate availability**: on EXHAUSTED or UNKNOWN, keep waiting; never resume from a stale cache or a guess.
- **exit**: manual resume uses the CLI `exit-waiting <repo_root> <task_id>` (underlying `task.exit_waiting_quota`) to return to active (direction=exit); the automatic path transitions directly to active when `quota-resume-confirm` ledgers (the `quota_resume_confirmed` event).

## manual / auto bounded resume

Resume authorization lives in the state top-level `quota_resume` block (exactly five keys: `mode / max_resumes / resume_count / automation_id / automation_kind`; defaults `manual / 0 / 0 / null / native` — unauthorized, zero budget; `automation_kind` labels the wake-carrier form of the bound witness — `native / os_timer / sleep_only`, and a legacy four-key block without the key reads as `native`):

| mode | Authorization | Behavior |
| --- | --- | --- |
| manual (default) | — | no automation is created: after quota recovers, wait for the user to return, or for the SessionStart resume injection |
| auto | explicit user `quota-resume-authorize` | automatic resume allowed, strictly bounded by the max_resumes budget |

The three disciplines of authorization and counting (mechanical semantics; runtime operations always go through the four runtime CLI commands; `runtime/task.py` is the underlying implementation):

1. **Authorization must be explicit**: `quota-resume-authorize <repo_root> <task_id> <max_resumes>` (underlying `task.authorize_quota_resume`) sets mode=auto and lands the budget — call it only after a user decision; the task must be in waiting_quota or active; max_resumes must not be smaller than the used resume_count. Authorization is never inferred and never auto-resets in a new window.
2. **The decision is idempotent**: every scheduled wake calls `quota-resume-decision <repo_root> <task_id> [--force-refresh]` (underlying `task.scheduled_activation_decision`) with a fresh quota observation; it returns exactly one of four closed words — `no-op` (not waiting_quota; repeated wakes are safe) / `remain-waiting` (EXHAUSTED/UNKNOWN; keep waiting) / `waiting-user` (unauthorized, or budget exhausted — the latter transitions the task to `waiting_user` exactly once so the user can re-authorize) / `resume-authorized` (available + authorized + budget remaining; returns workflow_run_id and a staged count). The decision command never calls the host ResumeWorkflowRun — resume is the main session's host action.
3. **Confirmation is what counts**: the `resume-authorized` count is only **staged** — after the host resume call is truly accepted, call `quota-resume-confirm <repo_root> <task_id>` to ledger it (underlying `task.confirm_resume_started`; resume_count +1, the task returns to active, journal `quota_resume_confirmed` event). An unconfirmed decision never consumes budget; repeated decisions before confirmation always return the same staged value; after the budget is exhausted (resume_count ≥ max_resumes) the task stays in `waiting_user` and no longer resumes automatically.

### arm-before-work

Authorization and arming are two independent facts: **authorize is user permission; bind is the future-activation witness; preflight is the mechanical confirmation that both are present**. In mode=auto, `quota_resume.automation_id` must be non-empty before the CreateWorkflow launch and before any automatic ResumeWorkflowRun — auto continuity with an empty automation_id is not armed (unarmed means blocked). Arming is never inferred from the mode: bind never infers mode=auto, and authorization never fabricates an automation id.

**Establish-first (the master principle behind arm-before-work, INV-CONT-01)**: once the launch interview (orchestration 'Task launch interview') confirms enable, establishing or re-arming the wake machinery always precedes current work: right after the authorization, observe reset_at (`quota-resume-decision --force-refresh`, any quota status; reset_at semantics in 'Quota observation surface'), the floor carrier is established in the same batch before CreateWorkflow, and when a valid future reset exists the first precision sleep is issued in the same batch — with no valid future observation no first sleep is issued, the floor-only downgrade is reported honestly, and the precision layer is never claimed as armed (the full runbook is in 'Launch sequence (runbook, arm-before-work)'). No work starts before arming PASS. A parking round only checks, re-issues and corrects — it never creates the first sleep ('Parking round'). The budget stays bounded: the max_resumes bounded semantics are unchanged; on exhaustion the task moves to `waiting_user` for the user to re-authorize — never infinite automatic revival.

**Wake finds unarmed** (empty automation_id in state): make one single rebuild attempt, **branched by carrier kind per the same fixed decision order** (the session has CronCreate → L0; otherwise the probe's recommendation decides) — **L0** (native): host CronCreate floor → `quota-automation-bind` → preflight PASS (current behavior unchanged); **L1** (os_timer): `os-carrier-remove <repo_root> <task_id>` → `os-carrier-install <repo_root> <task_id>` → bind → preflight PASS (the OS delete/install round is genuinely effective — OS tools report real exit codes, none of the native three-state degradation, so remove-then-reinstall is the honest idempotent rebuild). Handle both outcomes honestly: creation rejected by session ownership (an L0 live carrier actually still exists under the session; only the ledger witness is missing) → report the situation as-is and keep relying on the host rhythm, never a retry storm; creation succeeds → bind + preflight, then continue. A failed rebuild is reported as remain-waiting and the turn closes — never ask, never resume unarmed.

The arming surface is three commands:

- `quota-automation-bind <repo_root> <task_id> <automation_id>` (underlying `task.bind_quota_automation`): binds the future Scheduled Task witness id — together with its carrier-kind label into `quota_resume.automation_id / automation_kind` (L0: the real returned host automation id, kind `native`; L1: the `os:schtasks:glm-conductor-<task-id>` form witness, kind `os_timer`; L2: the synthetic `sleep-only` witness, kind `sleep_only`); the same (id, kind) pair is an idempotent success, a different id — or the same id with a different kind — is rejected — never a silent replacement; a replacement must be an explicit clear → bind sequence after a host-side lifecycle reconciliation; only the active / waiting_quota / waiting_user states are allowed.
- `quota-automation-clear <repo_root> <task_id> [automation_id]` (underlying `task.clear_quota_automation`): expected only **after** a host-side deletion confirmation or an explicit operator reconciliation; a given id mismatching the stored one is rejected, stored None is an idempotent success; it clears automation_id (and resets `automation_kind` to `native`) only and never touches mode / max_resumes / resume_count; terminal tasks may be cleaned.
- `quota-continuity-preflight <repo_root> <task_id>` (underlying `task.quota_continuity_preflight`, pure read, changes no state): manual → ready=true; auto + non-empty binding → ready=true (armed — the armed report carries the witness's `kind`, absent key read as `native`; ready semantics are never widened by kind: at L2 the sleep-only witness is a ready=true armed fact, and arming there means the precision layer only); auto + empty → ready=false (exit code 2, mechanically treated by the main session as a launch/resume block). It proves only that a persisted binding exists and **never proves the host-side activation is still alive** — wake boundaries still require the host-side check.

The decision-layer mechanical mirror (defense in depth): `scheduled_activation_decision` with auto + an empty automation_id refuses to return resume-authorized and returns remain-waiting with a reason containing `continuity-unarmed` — zero state writes and zero budget consumption (a missing arm is an operational liveness fault, not an exhausted authorization).

## Dual-channel wake: floor carrier + precision sleep

Unattended recovery is carried by **two orthogonal channels** (host facts and live evidence: `docs/reviews/WAKE_TOPOLOGY_PROBE_RESULTS.md`). Since v2.7.0 the floor's carrier form follows the **wake-carrier four-level degradation matrix** — the precision layer is identical at every level that has it:

| Level | `automation_kind` | Floor carrier | Precision | Survives app restart | Downgrade label |
| --- | --- | --- | --- | --- | --- |
| L0 | `native` (default) | host `CronCreate` (INV-CONT-03 unchanged) | background sleep | yes | none |
| L1 | `os_timer` | schtasks/crontab task installed by `os-carrier-install` — its trigger is a headless CLI resume turn | background sleep | yes | floor-on-OS |
| L2 | `sleep_only` | none — the precision sleep only | background sleep | no (recovery = a new session's SessionStart injection) | sleep-only |
| L3 | — (no binding; the state stays unarmed) | none | none | — | current fail-closed |

**Carrier selection is a fixed decision order (the model makes zero judgment)**:

1. the session has `CronCreate` → **L0** (current behavior unchanged, INV-CONT-03);
2. otherwise run the runtime CLI `wake-carrier-probe <repo_root>` (pure local, never creates a probe task) and read `recommendation`: `os_timer` → **L1**;
3. `recommendation` `sleep_only` → **L2** — the honest sleep-only downgrade label: the precision layer proceeds exactly as usual, and the floor's absence is declared as-is. L2's only absence is the floor fallback: after an application restart nothing wakes the session at all, and recovery is a new session's SessionStart injection;
4. the scheduler and the zcode CLI are both unavailable (`zcode_cli.available=false` **and** `scheduler.available=false` in the probe output — no carrier surface left at all) → **L3**: the current fail-closed discipline unchanged.

INV-CONT-03's lifetime-immutable semantics hold at both L0 and L1: the L1 OS task is likewise never touched after creation (no schtasks change, no crontab edit, no rebuild — deletion happens only in 'Terminal cleanup').

- **Floor**: a recurring Scheduled Task created at launch — **the interval is confirmed by interview question 3 (default 1h)** and after creation it is **never touched again for its lifetime** (no delete, no change, no rebuild — INV-CONT-03). The carrier form follows the matrix: L0 the host's native CronCreate automation; L1 an OS-scheduler task whose trigger resumes the session through a headless CLI turn. Its duty is the liveness fallback: it survives application restarts (L0: the host keeps automations across restarts; L1: the OS scheduler runs independently of the app) and, when the precision layer is lost, catches recovery within at most one interval.
- **Precision sleep**: a background Bash sleep task — sleeps until **the observed reset_at + 300s grace** and wakes the session automatically on exit (measured second-precise + 8-11s wake latency). reset_at semantics in 'Quota observation surface'; with no valid future observation the precision layer is absent and the floor-only downgrade is reported honestly. It is fully orthogonal to the automation state machine (zero Cron interaction) and does not survive application restarts (the floor covers that at L0/L1; at L2 a restart leaves no wake at all — recovery is a new session's SessionStart injection). **When a valid future reset exists, the first sleep is armed at authorization confirmation, before CreateWorkflow** — silent exhaustion (quota dying between turns) is naturally covered by the armed sleep.

Design basis (host facts, all live-tested): one automation per session; after a carrier's first trigger, every Cron change (create/change/delete) from that session silently degrades (the three-state regularity); **when quota is exhausted the model turn cannot execute** — waiting-period continuity can only be carried by host-side rhythm (automation triggers or background processes), and any mechanism depending on "wake up and then do scheduling actions" fails exactly where it is needed most; the observation surface passes reset_at through **unconditionally** (the single observation rule: any quota status — AVAILABLE/PRESSURE/EXHAUSTED/UNKNOWN — passes through the earliest valid future observed reset; quota status governs whether one may continue or resume, not whether a time may be passed through), so exhaustion decides only "is there work after waking", never "when to wake".

The establish-first master principle lives in 'arm-before-work' (establish/re-arm actions always precede work; every wake turn ends by checking the armed sleep and rolling the re-arm forward to the next future window boundary of the current observation).

### Launch sequence (runbook, arm-before-work)

**manual continuity** (interview question 1 answered "not enable" — the interview is the shared mandatory entry of both branches; question 3 does not appear):

```
Interview (single round of four questions, question 1 "not enable")
-> task-create <repo_root> <task_id> <goal> <mode> <assurance> [--dag <path>]
-> v24-compile <dag-json> [--task-ref <id>] [--out <path>]
-> ListModels -> write the temporary JSON file
-> workflow-model-select <models-file> [--task-ref <task_id>] [--repo-root <repo_root>]
-> writer-acquire <repo_root> <task_id>
-> CreateWorkflow (subagent_model=selection result)
-> task-record-workflow-run <repo_root> <task_id> <run-id>
```

**auto continuity** (entry = the interview confirmed enable): question 1 "enable" lands the authorization first, and the budget is selected in the same interview; the user's statement is authorization intent, never an unbounded authorization. **Arm-before-work (INV-CONT-01) with establish-first**: the floor carrier (branched by the wake-carrier four-level matrix — fixed decision order, the model makes zero judgment) and the first precision sleep are established in the same batch before CreateWorkflow:

```
Interview (single round of four questions: 1 enable + bounded budget; 2 worker model,
   all candidates + explicit confirmation; 3 floor interval, default 1h; 4 workflow
   submission auto-approval, delegate/full only — see orchestration 'Task launch interview')
-> task-create <repo_root> <task_id> <goal> <mode> <assurance> [--dag <path>]
-> quota-resume-authorize <repo_root> <task_id> <max_resumes> (the budget confirmed in question 1)
-> immediately quota-resume-decision <repo_root> <task_id> --force-refresh to obtain the observed
   reset_at (observable in any quota status — single observation rule: the earliest valid future
   observed reset, regardless of quota status; the active/no-op branches pass reset_at through the
   same way, and the task is not required to be in waiting_quota)
-> establish the floor carrier, branched by the four-level matrix (fixed decision order,
   model zero judgment — see 'Dual-channel wake'):
     L0 (the session has CronCreate — current behavior unchanged, INV-CONT-03):
        host CronCreate floor carrier: intervalUnit=hourly, interval=<value confirmed
        in question 3, default 1>, recurring=true, prompt=the unified wake protocol
        (action first, never enumerate tool lists; template in
        references/long-horizon.md)
     L1 (otherwise run wake-carrier-probe <repo_root>, recommendation=os_timer):
        os-carrier-install <repo_root> <task_id> --interval <value confirmed in
        question 3, default 1> --prompt <the unified wake protocol> (the command reads
        .glm-conductor/session-witness.json internally as the session-id fallback; the
        installed wake command is the frozen headless form node <zcode.cjs> --resume
        <sess_id> --cwd <repo> --workflow-mode alwaysOn -p "<unified wake protocol>";
        template in references/long-horizon.md)
     L2 (probe recommendation=sleep_only): no floor establishment at all (the honest
        sleep-only downgrade — the floor's absence is declared as-is; the precision
        layer below proceeds unchanged)
-> quota-automation-bind <repo_root> <task_id> <witness id per level> (L0: the real
   returned automation id; L1: the os:schtasks:glm-conductor-<task-id> form witness,
   kind os_timer; L2: the synthetic sleep-only witness, kind sleep_only)
-> quota-continuity-preflight <repo_root> <task_id> must PASS (ready=false blocks the
   launch; at L2 the sleep-only witness is a ready=true armed fact — armed means the
   precision layer, the floor fallback is honestly absent)
-> issue the first precision sleep: a background sleep until (observed reset_at + 300s) — issued
   only when this observation contains a valid future reset; when reset_at is null do not issue,
   report the floor-only downgrade honestly (only the floor is armed; at L2, which has no floor,
   nothing is armed — say so in the same honest breath), never claim the precision
   layer is armed — copy the command template verbatim from the precision sleep template in
   references/long-horizon.md (bash computes the seconds dynamically; the model does zero
   arithmetic; the host accepting the background process is the record)
-> apply the question-2 worker model pin: worker-model-pin <repo_root> <task_id> <exact id>
   (when question 2 appeared; consumption semantics "stale pin rejected" in orchestration
   'Worker model selection gate')
-> v24-compile -> explicit worker model selection (the orchestration 'Worker model selection gate'
   hard gate) -> writer-acquire
-> CreateWorkflow (subagent_model=selection result) -> task-record-workflow-run <repo_root> <task_id> <run-id>
```

**Three-part success statement**: a successful auto launch must report three independent facts separately — (a) **the auto authorization landed** (mode=auto + bounded budget); (b) **the floor carrier is bound** (a real automation id in state + preflight PASS); (c) **the precision sleep was actually issued** (the host accepted the background process). If any is missing, state the gap honestly: missing (c) is the **visible floor-only downgrade** (only the floor fallback is armed), never "the precision layer is armed"; missing (b) is an auto block (preflight ready=false, no work starts). Matrix forms: at **L1** both (b) and (c) read true in their own shapes — (b) is the OS-timer floor (the `os:schtasks:glm-conductor-<task-id>` witness, kind `os_timer`, preflight PASS) and (c) is the background sleep exactly as at L0; at **L2** only (c) is true and (b) is the sleep-only witness binding (the synthetic `sleep-only` id, kind `sleep_only`, preflight ready=true on that witness) — the report names the sleep-only downgrade explicitly: armed means the precision layer only, and the floor fallback is absent (after an application restart nothing wakes; recovery is a new session's SessionStart injection).

The three prohibitions on floor-carrier creation failure are unchanged: **never claim auto continuity was enabled**, **never bind a fake id**, **never promise continuation under auto** (this turn degrades to manual continuity only, or hand over to the operator).

### Unified wake protocol

The wake turn's first action is always the idempotent decision; the branch tree is fixed (execute verbatim; **action first, never enumerate tool lists** — see the prompt design rules below):

```
quota-resume-decision <repo_root> <task_id> --force-refresh (first action on waking;
│ the decision passes this observation's reset_at through unconditionally in any action, incl. no-op)
├ resume-authorized and preflight PASS (quota-continuity-preflight):
│     ResumeWorkflowRun (same run id) → after the host accepts
│     quota-resume-confirm <repo_root> <task_id> → continue working
├ active and run healthy (GetWorkflowRun queryable, making progress): close the turn with no-op
├ active and run stalled (the silent-exhaustion self-healing path): treat as recovery — if the run
│     can resume, resume as above; otherwise rebuild understanding from the repository's current
│     state and redispatch (never replay completed nodes)
├ remain-waiting (EXHAUSTED/UNKNOWN): wait and close the turn (reset_at passes through as usual,
│     feeding the re-arm target arithmetic)
├ waiting-user (unauthorized / budget exhausted): no-op (wait for the user, never ask)
└ terminal: no implementation, go to 'Terminal cleanup'
Closing action (each branch, after its handling — every non-terminal task still authorized for auto):
    check the armed sleep: an armed sleep already targeting a future window is not re-issued
    (no duplicate sleeps); with no armed sleep, or a past or stale target, issue the next precision
    sleep to (this observation's reset_at + 300s — the next future window boundary of the current
    observation) — the rolling re-arm; reset_at null or already past → do not issue (no negative
    sleeps, no busy spin), report the floor-only state honestly and wait for the floor rhythm
```

**The wake prompt must carry** (self-contained stable identifiers; recoverable from repository truth; template in references/long-horizon.md): the exact task_id and repository root; load the task state (state.json + checkpoint) before acting; refresh the quota on wake (the decision takes a fresh observation); preflight first; take the exact run id for the task state and resume the same id; decision=resume-authorized **and** preflight ready before ResumeWorkflowRun; quota-resume-confirm after host acceptance; at turn end always check the armed sleep and roll the re-arm (every non-terminal task still authorized for auto; the decision's reset_at passes through incl. no-op; no sleep on a stale or past target, no spin); terminal goes to cleanup. **Never embed**: full workflow source, secrets or credentials, stale quota assumptions, tool-list enumerations.

**Wake prompt design rules (de-priming, action first)**: the wake prompt always opens with **the first action to execute** (quota-resume-decision) and **never opens with a list of available tools and never enumerates tool names throughout** — degraded host launches correlate with tool-list priming (probe records); the action-first form removes the ambiguity and the priming surface at once.

### Wake-turn boundary facts

- **Wake turns are host mid-session continuations**: on trigger the host injects the prompt as a new turn into the existing session (not an independent fresh-context agent) — a wake turn can see the session's prior context, but that is not a recovery basis: recovery still goes through repository facts and state.json (repository > checkpoint > session memory), preventing stale session memory from skipping reconciliation.
- **Wake turns / resume turns never interview**: the launch interview (orchestration 'Task launch interview') happens only in interactive turns; an unattended turn hitting any blocker only an interview could clear (a stale pin, an exhausted budget) fails closed — park in the corresponding waiting state and hand over to the user, never create a deadlock by asking.
- **Idempotent decision contract**: every wake opens with `quota-resume-decision <repo_root> <task_id> --force-refresh` — repeated wakes are safe (non-waiting_quota is always a zero-side-effect no-op; unarmed auto refuses to return resume-authorized, `continuity-unarmed`); the decision passes this observation's reset_at through unconditionally in any action (incl. no-op); resume-authorized and preflight ready → same-id resume, `quota-resume-confirm` immediately after host acceptance; an unconfirmed decision never consumes budget.
- **reset_at is not a resume authorization**: the reset_at a decision passes through is only the next future window boundary of the current observation (a safe wake / checkpoint time) — resume is still decided only by the decision's closed four words (`resume-authorized`) plus the preflight hard gate; waiting_user / terminal tasks never start new work because a reset came due.
- **A wake turn's workload stays small**: do one round of checking, recover one stretch of work; never cram the remaining work into a single wake.
- The runtime itself makes zero host scheduling calls: creating Scheduled Tasks / background sleeps and similar host actions belong to the main session; Conductor provides only the idempotent decision and arming preflight primitives.

### Parking round

(quota exhaustion observed while the turn is still in flight)

```
quota-wait <repo_root> <task_id> [--force-refresh]
-> quota-resume-decision <repo_root> <task_id> --force-refresh
-> check the precision layer: an armed sleep already targeting a future window is not re-issued;
   if the observed reset_at drifted (the armed target past / stale), re-issue a new sleep to the
   current observed reset_at + 300s
-> close the turn (waiting-period continuity is carried by the armed sleep + the floor)
```

**Parking-round boundary**: a parking round only checks, re-issues and corrects, and **never creates the first sleep** — the first sleep is armed by the launch turn (in the same batch as the authorization confirmation); when a parking round is late or missing, continuity is carried by the armed sleep + the floor, not by "making it to parking in time".

**Silent-exhaustion boundary**: if quota dies between turns (no parking round happens), the standby sleep armed at launch or at the last wake exactly covers the next window boundary — no new action needed. This is the fundamental reason precision sleeps arm at launch: establishment never depends on "making it to parking".

### Floor interval tradeoffs

The floor is pure fallback (precision comes from the sleep layer); the interval is a tradeoff between "fallback response speed" and "working-period monitoring cost":

- shorter ⇒ faster fallback recovery (lateness cap = one interval), denser working-period no-op wakes (each a small model turn);
- **it must not equal the window length (a full 5h)**: that races the reset at second granularity (the wake call is anchored to the next window → the next trigger fires a few seconds early → no response → the whole window is missed) — an interval clearly off the window length is fine (1h is far from 5h; no race);
- default 1h: mean fallback lateness ~30 minutes and one working-period no-op per hour — it catches recovery earlier than the stable lateness of longer intervals;
- the interval varies by task (1h for important tasks, 4h for economical ones are both reasonable) — hence interview question 3, confirmed by the user per task.

### Precision characteristics and self-healing

(honest ledger)

- While the precision layer is armed, window-boundary wakes are **second-precise** (end-to-end rehearsal measured zero error + 8-11s wake latency); waiting-period triggers cost zero quota (no model turns during sleep);
- working-period re-arm cost ≈ one small wake per window boundary (~5h) (no-op + re-arm);
- floor-only carriage (an application restart killed all sleeps): recovery is late by 0-1 intervals (with the default 1h interval, mean ~30 minutes); after recovery the **first floor wake re-arms the precision sleep** (the self-healing loop);
- duplicate sleeps (launch arm + parking re-issue coexisting) are naturally absorbed by decision idempotence (waking early is harmless).

### One automation per session

One automation per session ⇒ the main session runs at most one auto task at a time: the host rejects a second automation by session ownership (measured message). To run several cross-quota long tasks in parallel, put the extra tasks in separate sessions (the host's own suggested path) or serialize them one task at a time.

### Carrier lifetime (INV-CONT-03)

After creation the carrier takes **no change actions ever** — INV-CONT-03 holds at both L0 and L1: L0 means no CronUpdate, no CronDelete, no rebuild; L1 means no schtasks change, no crontab edit, no `os-carrier-install` re-run, no rebuild. The floor lives at the interview-confirmed interval until 'Terminal cleanup' (deletion happens only there); precision is carried entirely by the orthogonal sleep channel (zero dependence on the carrier surface). The three-state regularity (post-trigger changes fail) therefore can never bite: the design contains no post-trigger changes.

## Checkpoint (navigation state)

After substantial milestones (not after every tool call) a long task writes the CONTINUITY CHECKPOINT into the task directory `checkpoint.md`: TASK_ID / GOAL / ROUTE / COMPLETED / CURRENT STATE / NEXT ACTION / VERIFICATION / OPEN RISKS. Principles:

- the checkpoint is navigation state, not the repository source of truth — no full diffs, no bulk code, no claims of unverified content
- repository state always wins: when the checkpoint and the repository conflict, the repository is authoritative
- the template and the eight-step recovery procedure are in references/long-horizon.md

## Terminal cleanup

After a task reaches a terminal state (completed / cancelled / failed) and acceptance, the cleanup order is fixed (acting only on this task):

1. **Delete before clear, order fixed; the host-side delete attempt is branched by carrier kind**:
   - **L0 (native)**: make one **single** host-side delete attempt for this task's bound automation (located by the automation_id recorded in state) — **the expected result is failure**: once a carrier has triggered, that session's Cron delete launches silently degrade (the three-state regularity, live-tested) and the model side cannot complete the deletion. A failed deletion is not an error: **report the automation id honestly** (with its title); **the user manually deleting it in the Automations page is the expected operation**.
   - **L1 (os_timer)**: the host-side delete attempt is `os-carrier-remove <repo_root> <task_id>` instead of a native Cron delete attempt — **genuinely effective, no three-state degradation** (the OS tool's real exit code is the only fact). On success (`ok=true`) proceed straight to the clear step; on failure (`ok=false` — e.g. the task is already gone or the OS refuses) **report the OS task name `glm-conductor-<task-id>` honestly** for the user's manual disposition (schtasks / crontab), then clear on the user's confirmation — never a retry storm.
2. **Clear the binding only after the deletion is confirmed** (L1: `os-carrier-remove` returned ok=true) **or the user confirms the manual deletion** (L0): after confirmation, `quota-automation-clear <repo_root> <task_id> [automation_id]` clears the bound id; before confirmation the bound id is retained for reconciliation — never pretend it is already clean.
3. No retry storms: the delete attempt happens once and is never repeated; repeated wakes / repeated closures that meet an already-cleaned state skip idempotently.
4. **Never delete another task's automation**: only this task's single bound one; deleting the global scheduling surface or another task's automation is a forbidden operation.
5. Delete this task's directory `.glm-conductor/tasks/<task-id>/` (only this directory; never touch other tasks' state or visual evidence); when the existing cleanup flow deletes the task directory, its order must never erase the automation_id before the host-side delete attempt.
6. Report the final state to the user (including the final disposition of the auto-continuity arming and the automation id awaiting manual deletion).

## Quota observation surface

- `quota-resolve <repo_root> [--force-refresh]` (runtime CLI): four-state resolution with frozen output keys `{status, source, evaluated_at, reason}`; hierarchy = fresh cache → provider → stale cache → UNKNOWN (never default to AVAILABLE, never retry the network); the observation surface makes zero task transitions.
- `quota-wait / quota-resume-authorize / quota-resume-decision / quota-resume-confirm` (runtime CLI): the quota wait/resume lifecycle surface; canonical usage in 'waiting_quota' and 'manual / auto bounded resume' above; decision ledgering happens only on the task side and never calls the host ResumeWorkflowRun. **reset_at semantics (the single observation rule)**: the reset_at of the quota-wait / quota-resume-decision observation views and the reset_at the decision passes through (**unconditional pass-through in any action, incl. no-op**) are all **the earliest valid future observed reset_at among the identified windows** (parsed and compared explicitly as UTC instants, independent of the window status AVAILABLE/PRESSURE/EXHAUSTED/UNKNOWN; expired or invalid resets do not participate; no valid future observation → null, reported honestly as the floor-only downgrade, never fabricated) — quota status governs only whether one may continue or resume, never whether a time may be passed through; the next observed window boundary is the safe wake / checkpoint: wake, re-observe, re-arm, iterate to convergence. Sleep-target arithmetic always uses this value (+300s grace). plan_resume's EXHAUSTED-conservative resume_at rule is unchanged; it is a separate independent decision path and takes no part in reset_at pass-through or arming.
- `quota-automation-bind / quota-automation-clear / quota-continuity-preflight` (runtime CLI): the auto-continuity arming surface (bind / clear / preflight); canonical usage in 'arm-before-work' above; preflight ready=false is reported with exit code 2 (the launch/resume block signal).
- `runtime/quota/report.py [--json]`: human-readable diagnostics (window details + status + recovery advice), see the `/glm-conductor:quota` command.
- **Credentials**: the environment variable `GLM_CONDUCTOR_QUOTA_API_KEY` first; the logged-in ZCode `~/.zcode/v2/config.json` provider configuration is the documented fallback; credentials are never written to disk (not into state.json / events.jsonl / checkpoint / logs / any output); credentials unavailable → UNKNOWN, never fabricated.
- **lite plan**: no weekly window (only the 5-hour window) — report details are output per actually existing window; resolution and decisions do not depend on the weekly window existing.
- **Invariant boundaries**: native plugin-level quota APIs (getQuotaRemaining / getQuotaResetTime / onQuotaReset) do not exist and must never be invented; reset cycles are never hardcoded; quota observation is used only as evidence, never as a routing axis; no resident polling — queries happen only at refresh points.

## Failure Handling

- **Scheduling capability degraded or unavailable (matrix caliber)**: the native CronCreate surface missing is **not** "scheduling capability unavailable" — run the fixed decision order of 'Dual-channel wake' and degrade by matrix: the probe's `recommendation` `os_timer` → arm **L1** (the `os-carrier-install` OS-timer floor); `recommendation` `sleep_only` → arm **L2** (the sleep-only witness; the precision layer proceeds exactly as usual; the floor's absence is declared as-is). Only **L3** — no carrier surface at all — keeps the fail-closed sentence unchanged: auto continuity stays unarmed (preflight ready=false) — never claim continuity was enabled, never bind a fake id, never promise continuation under auto; keep the checkpoint and state, and report "manually recoverable" to the user with the recovery method (a new session gets the SessionStart resume injection).
- **Wake finds auto unarmed** (binding lost / host activation gone): the single kind-aware rebuild sequence of 'arm-before-work' applies (L0: CronCreate → bind → preflight; L1: `os-carrier-remove` → `os-carrier-install` → bind → preflight; on an L0 session-ownership rejection, report honestly and rely on the host's existing rhythm); never fabricate an activation and never resume unarmed.
- **Precision sleep not armed** (after an application restart / session rebuild): any wake turn (including floor-triggered) re-arms per the unified protocol at turn end — self-healing, not a fault.
- **Workflow unresumable**: rebuild from the repository's current state per step 4 of 'Workflow recovery (same run id resume)', never replaying completed work.
- **Authorization budget exhausted**: the task stays in waiting_user — wait for the user to re-authorize (`quota-resume-authorize`) or to close out explicitly; the model never raises the budget itself.
- **Legacy tasks**: report the handling guidance per `LEGACY_STATE_GUIDANCE`; no migration and no fabricated completion evidence.
