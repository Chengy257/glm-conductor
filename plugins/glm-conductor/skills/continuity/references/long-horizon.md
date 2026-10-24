# Long-Horizon Task Mechanics

This file is the mechanics reference of the continuity skill; SKILL.md points here for the lifecycle, the checkpoint template and the recovery-procedure details.

## Lifecycle

```
Task start (orchestration declares SELECTIVE ROUTE, the 'Task launch interview'
   single round of four questions, task-create lands state.json)
   |
   v
[Arm] (auto/until_done only; arm-before-work with establish-first:
   establishment happens in the same batch as the interview confirmation,
   all of it before CreateWorkflow — quota-resume-authorize
   -> immediately quota-resume-decision --force-refresh to obtain the
   observed reset_at (observable in any quota status; single observation
   rule: the earliest valid future observed reset among the identified
   windows, regardless of quota status — the active/no-op branches pass
   it through the same way) -> the floor carrier, kind-aware per the
   wake-carrier four-level matrix (fixed decision order, the model makes
   zero judgment): L0 host CronCreate floor carrier (intervalUnit=hourly,
   interval=<value confirmed in interview question 3, default 1>,
   recurring=true, never changed for its lifetime) / L1 an OS-scheduler
   task installed by os-carrier-install (the same interview question-3
   interval; its trigger is a headless CLI resume turn) / L2 no floor at
   all (the sleep-only witness — the floor's absence is declared as-is)
   -> quota-automation-bind
   -> quota-continuity-preflight PASS -> when a valid future reset exists,
   issue the first precision sleep to observed reset_at+300s (sleep
   template in 'Precision sleep template' below; reset_at null -> do not
   issue, report the floor-only downgrade honestly, never claim armed;
   on creation failure never claim enabled)
   |
   v
[Execute] delegate/full -> native Workflow run (user-authorized explicit
   model selection (the orchestration 'Worker model selection gate' hard
   gate; Flash is a soft recommendation) ->
   CreateWorkflow(subagent_model=<selection result>) ->
   task-record-workflow-run registers the run id)
   |
   v (substantial milestone -> write the CONTINUITY CHECKPOINT)
   |
   v (interruption: session ends / quota exhausted -> waiting_quota /
     Workflow stall)
[Reactivation] (SessionStart resume injection / dual-channel wake —
   precision-sleep exit or floor trigger, the same unified wake protocol /
   user manual resume)
   |
   v
Eight-step recovery check (repository > checkpoint > session memory)
   |
   +-- goal complete -> terminal cleanup (the delete attempt is kind-aware:
   |     L0 expected to fail per the three-state regularity / L1
   |     os-carrier-remove genuinely effective -> report the automation id
   |     or OS task name honestly -> the user deletes it manually when the
   |     attempt failed -> clear the binding only after confirmation, then
   |     delete this task's directory)
   +-- resumable -> same run id resume (quota available + authorized +
   |     budget remaining + auto armed/preflight ready)
   +-- not resumable (quota exhausted / run dead) -> park and wait, or
         rebuild from repository reality
```

## CONTINUITY CHECKPOINT template

After every substantial milestone (not after every tool call) a long task writes to the task-specific path `.glm-conductor/tasks/<task-id>/checkpoint.md`. Repeated writes by the same task overwrite that task's own file; different tasks use separate directories — they never overwrite or delete one another. The template is copy-ready:

```
CONTINUITY CHECKPOINT

TASK_ID:
<semantic prefix>-<random hex suffix> (e.g. redesign-settings-page-7f3a2c;
generation contract below)

GOAL:
<original goal>

ROUTE:
<solo | delegate | audit | full>

ASSURANCE:
<standard | high>

WORKFLOW RUN ID:
<the delegated run id; omit this section when absent>

COMPLETED:
- ...

CURRENT STATE:
- ...

NEXT ACTION:
- ...

VERIFICATION:
- completed:
  - ...
- pending:
  - ...

OPEN RISKS:
- ...
```

If the ZCode runtime actually exposes the identifiers below, appending them is optional; when it does not, do not write them — **never fabricate**. The authoritative landing point of the automation association is `quota_resume.automation_id` in state.json (the auto-continuity arming witness; bind/clear go through the CLI); AUTOMATION_ID in the checkpoint is only a navigation hint — when the two disagree, state.json wins:

```
SESSION_ID:
<session identifier>

AUTOMATION_ID:
<scheduled-task identifier>
```

### TASK_ID rule

Format — mechanically unique at generation, independent of naming conventions:

```text
<semantic prefix>-<6-8 character random hex suffix>
```

Examples: `redesign-settings-page-7f3a2c`, `parser-refactor-a92c1d`, `migrate-auth-20260828-231502` (a timestamp suffix is acceptable, a random suffix is preferred — tasks created in parallel at the same moment do not collide).

Generation contract:

1. Generated once at task creation (create_task's task_id is it)
2. Persisted with the checkpoint
3. Never regenerated during recovery
4. The checkpoint path, the visual-evidence path, the scheduled resume prompt, the automation association and completion cleanup all use the same exact ID
5. Unique within the current workspace — a human-readable semantic prefix is an optional benefit; uniqueness is mandatory
6. Stable across recovery once generated (it never changes after being written into the checkpoint)
7. Legal-character constraint (validated in `runtime/state.py`): starts with an alphanumeric character and contains only alphanumerics and hyphens

Lookup and isolation discipline:

- The scheduled resume instruction must carry the exact ID
- Recovery and cleanup both locate the `.glm-conductor/tasks/<id>/` directory by it
- The visual-evidence directory is isolated by it too (see 'Visual-channel run details' in the orchestration skill's references/operations.md)
- Never use "the latest checkpoint" as the lookup strategy — with parallel tasks "latest" is ambiguous
- When reading a legacy (v1.x-era) checkpoint, treat the old field name `CONTINUITY_ID` as `TASK_ID` (normalized at read time; existing checkpoint files need not be rewritten)

Checkpoint principles:

- **Navigation state, not the source of truth**: the checkpoint records only "where we are and what is next"; it never replaces the repository
- **No full diffs, no bulk code**: diffs and code are compared against the repository itself
- **No claims of unverified content**: nothing outside VERIFICATION.completed may be written as completed
- **Repository first**: when the checkpoint and the repository conflict, the repository is authoritative
- **One update per substantial milestone**: not after every tool call

## Runtime state (state.json) and execution log (events.jsonl)

An active task maintains two machine-readable files alongside the checkpoint (same directory, same TASK_ID); the three divide labor as follows:

| File | Nature | Readers |
| --- | --- | --- |
| checkpoint.md | navigation state (narrative recovery basis) | model |
| state.json | deterministic task state (the completion guard checks completion against it) | guard / model |
| events.jsonl | execution provenance (append-only, one event per line) | model / audit |

state.json field overview (authoritative schema: plugin `runtime/state.py`):

| Field | Meaning |
| --- | --- |
| task_id / goal | identity and goal |
| repository | the repository root the task is bound to (`repository.root`; the single binding for evaluation and evaluation-root resolution) |
| route | the two keys mode / assurance (the only keys create_task extracts) |
| dag | the static node array (shape in `runtime/work_unit.py`; the completion guard judges ownership overrun against the union of all nodes' ownership) |
| status | seven states: active / waiting_quota / waiting_user / blocked / completed / cancelled / failed |
| phase | optional descriptive label (planning / workflow / validating / reviewing); no transition matrix |
| workflow_run_id | the delegated run association (landed by record_workflow_run at launch; recovery resumes the same run id) |
| validation | the task-level validation record (status passed/failed + change_id) |
| review | the task-level review record (reviewer + verdict + change_id) |
| quota_resume | the resume-authorization block (five keys: mode / max_resumes / resume_count / automation_id / automation_kind [+ diagnostic last_observation]); automation_id is the witness of the currently associated future Scheduled Task, kind-labeled by automation_kind (`native` / `os_timer` / `sleep_only`; an absent key reads as `native`) — in mode=auto it must be non-empty to count as armed (a preflight precondition), and its existence alone never proves the host-side activation is still alive |

Status only moves forward, never backward (enforced by the `state.TASK_TRANSITIONS` matrix); a terminal state is never silently reopened (explicit reopen goes through `state.reopen_task`); the repository remains the source of truth for code state — state.json is runtime task state only.

The events.jsonl task-level event vocabulary is exactly ten names (`runtime.task.TASK_JOURNAL_EVENTS`): route_selected / workflow_started / workflow_reassessed / validation_recorded / review_recorded / waiting_quota / quota_resume_confirmed / task_completed / task_failed / task_cancelled — per-event timing in the SKILL.md section 'Task state and runtime files'; appends always go through `runtime/journal.py` (the module manages the timestamp; callers never supply their own).

### Read order on recovery

In the eight-step recovery, step 2 reads the checkpoint and, in the same pass, state.json and the tail of events.jsonl:

1. **state.json** (machine-readable): restore awareness of status, route, workflow_run_id and validation / review — when it exists and is non-terminal, this is still the active task and remains under completion-guard tracking after recovery; the quota_resume block shows the authorization, budget and arming status (in mode=auto an empty automation_id means unarmed: rebuild the activation and bind + preflight PASS before any automatic resume is allowed)
2. **checkpoint.md** (narrative): recover NEXT ACTION and context
3. **tail of events.jsonl** (recent entries): what happened last before the interruption (the final event is often the interruption point)

When the three disagree, the repository still wins; when the state.json and checkpoint.md narratives disagree, the repository's actual diff adjudicates and both are corrected.

## Resume procedure (eight steps)

After every reactivation, execute in order:

1. **Check the current goal**: confirm the current session still pursues the same goal
2. **Read this task's checkpoint**: read the `.glm-conductor/tasks/<task-id>/checkpoint.md` carried by the resume instruction; when the instruction carries no ID, locate the matching directory under `.glm-conductor/tasks/` by goal semantics; when none exists, treat it as no-checkpoint and rebuild understanding from the goal and the repository's current state only
3. **Check the repository state**: the repository's real state outranks the checkpoint (repository > checkpoint)
4. **Check the current diff**: establish exactly which changes the worktree actually holds
5. **Judge whether the prior changes survive**: can the changes the checkpoint claims as completed still be observed in the diff and the repository
6. **Judge whether the goal is complete**: if complete, stop implementing and enter Completion Cleanup
7. **Check the latest validation status**: is the validation record still fresh (change_id matches the current state), and which validations remain undone
8. **Resume from NEXT ACTION**: re-output the SELECTIVE ROUTE declaration, then continue from NEXT ACTION (a delegated task goes through 'same run id resume' first, see SKILL.md; the auto-wake path must pass both hard gates — decision `resume-authorized` **and** `quota-continuity-preflight` ready — before ResumeWorkflowRun is allowed; close-out must check the armed sleep and roll the re-arm — for a non-terminal task still authorized for auto, issue/extend the next precision sleep to this observation's reset_at+300s, the next future window boundary of the current observation; never issue a sleep on a stale or past target, never spin)

Two prohibitions:

- Never blindly replay old instructions after a wake
- Never roll back new repository changes to restore a checkpoint — when the checkpoint and the repository disagree, analyze the source of the change, update understanding, and treat the repository as authoritative

## Structured resume prompt (floor-carrier trigger content)

The prompt below is the trigger content of the floor recurring Scheduled Task. It must be self-contained: no session context is assumed — this prompt plus the named task state plus the repository state suffice for recovery. **Placeholders must be replaced with real values when the carrier is created**. The prompt carries only stable identifiers and recovery instructions — never the full workflow source, never secrets or credentials, never stale quota assumptions (quota is observed fresh at wake), and **never an enumeration of the tool inventory** (action first, de-priming):

```
Resume GLM Conductor task. First action: run the idempotent wake decision.

TASK_ID: <id>
REPOSITORY ROOT: <repo_root>
TASK DIR: .glm-conductor/tasks/<id>/
CHECKPOINT: .glm-conductor/tasks/<id>/checkpoint.md

First load the current task state (.glm-conductor/tasks/<id>/state.json),
the specified checkpoint, and the current repository state. Load task
state before taking any action.

Run the wake decision first, with a fresh quota observation:
cli.py quota-resume-decision <repo_root> <task_id> --force-refresh

Then follow the unified wake protocol by that decision:
- resume-authorized AND quota-continuity-preflight ready: resume the
  exact workflow_run_id from task state via the host resume action,
  then record it via cli.py quota-resume-confirm <repo_root> <task_id>
  once the host accepts.
- active with a healthy run: no-op.
- active with a stalled run: treat as recovery — resume if the run is
  resumable, otherwise rebuild from repository reality; never replay
  finished work.
- remain-waiting: wait (the decision output carries this observation's
  reset_at for the re-arm target — every action, including no-op,
  carries the currently observed earliest valid future reset).
- waiting-user or terminal: no-op (terminal proceeds to cleanup).

Unless the task is terminal (and only for a still-authorized auto
task), finish this wake turn by checking whether a suitable precision
sleep is already in flight; if none is, or its target is stale or
already past, re-arm one: copy the background sleep template verbatim
from the continuity long-horizon contract, targeting the reset_at
from this turn's decision output (the earliest valid future observed
reset, independent of quota status) plus 300 seconds. If reset_at is
null or already past, skip the sleep — never spin, never fabricate a
target — and disclose floor-only degraded precision honestly instead
of claiming the precision layer is armed. The first sleep is armed at
launch, before any work; a parking or wake turn only inspects, re-arms
or corrects it, and never establishes the first sleep. After a host or
app restart drops all background sleeps, the recurring floor wake
re-arms the precision sleep (self-healing) — never present a floor
trigger as a precision-time wake. Never compute the delay yourself —
the template computes it in bash.

The recurring floor automation bound to this task stays in place for
its whole lifetime — never update, delete, or recreate it from a wake
turn.

Authorizations recorded by the launch interview hold for the whole task
lifetime: auto continuity was enabled with a bounded max_resumes budget,
and the worker model pin (worker_model in task state) is the
user-authorized task-level default for any new workflow submission — a
preference, not the task identity. Consume it via workflow-model-select
--task-ref against the current host model list; if the pin is no longer
on the list, the selection is rejected: block the submission and wait
for the user. Workflow submission auto-approval (auto_approve_workflow
in task state) is likewise a task-level authorization surface: when it
is enabled, the wake round's path-type re-submissions are
confirmation-free. Never fall back to auto-selection, never switch
models silently, and NEVER ask questions on an unattended wake/recovery turn.

Repository state is authoritative over checkpoint state.

Continue under the stored route, ownership, validation, and review
contracts; re-declare SELECTIVE ROUTE before resuming execution.

Perform ROUTE REASSESSMENT only when newly observed evidence changes
delegability or assurance.
```

## Precision sleep template (copy-verbatim — bash computes the seconds dynamically, the model does zero arithmetic)

At launch-turn arming and at every wake-turn / parking-round close-out (whenever the task is non-terminal and still authorized for auto), substitute `<reset_at>` with **this observation's reset_at** (the `decision.reset_at` output of quota-resume-decision — passed through unconditionally in any action incl. no-op; an ISO string such as `2026-10-11T14:33:42Z`; single observation rule: the earliest valid future observed reset among the identified windows, regardless of quota status) and execute verbatim with the Bash tool in **run_in_background**:

```
echo "precision-sleep until <reset_at>+300s, launched $(date -u +%Y-%m-%dT%H:%M:%SZ)"; sleep $(( $(date -u -d "<reset_at>" +%s) + 300 - $(date -u +%s) )); echo "precision-wake $(date -u +%Y-%m-%dT%H:%M:%SZ)"
```

Template discipline:

- **The model does zero arithmetic**: the target seconds are computed live by bash (`date -u -d` parses the ISO instant + 300 grace - now); the model never converts milliseconds / minutes / time zones itself
- **Run in background**: issued with run_in_background — the task exiting wakes the session automatically (measured second-precise + 8-11s wake latency); zero model turns and zero quota cost while waiting
- **reset_at null / missing** (no valid future observation) -> do not issue the sleep; report the floor-only downgrade honestly (only the floor is armed) and wait for the floor rhythm — never fabricate a target
- **Delta <= 0** (target already past) -> do not issue the sleep (no negative sleeps, no busy spin — never spin on a stale or past target); handle this round per the decision (when quota has recovered, take the resume path)
- **Duplicate sleeps are harmless**: a launch arm and a parking re-issue coexisting is absorbed by decision idempotence (waking early -> new observation -> new decision)
- **Does not survive application restarts**: when a restart kills the sleeps, the floor is the fallback (at L0/L1) and the first floor wake's close-out re-arms — self-healing, not a fault; at L2 there is no floor, so a restart leaves no wake at all — recovery is a new session's SessionStart injection

## Scheduled triggers (dual-channel: floor carrier + precision sleep)

Unattended recovery is carried by two orthogonal channels (host facts and live evidence: `docs/reviews/WAKE_TOPOLOGY_PROBE_RESULTS.md`):

- **Floor carrier**: the recurring Scheduled Task created at launch, **kind-aware** (L0 the host's native CronCreate automation / L1 an OS-scheduler task installed by `os-carrier-install` / L2 none at all — the sleep-only witness; the fixed decision order lives in the SKILL.md wake-carrier matrix) — the interval is confirmed by interview question 3 (default 1h; it must not equal the 5h window length — that races the reset at second granularity); it survives application restarts and is **never changed after creation, for its whole lifetime** (no delete, no change, no rebuild — INV-CONT-03, at L0 and L1 alike); during the working period each trigger is usually a small no-op wake (absorbed by decision idempotence). The **L1 trigger form is a headless CLI resume turn**: `node <zcode.cjs> --resume <sess_id> --cwd <repo> --workflow-mode alwaysOn -p "<unified wake protocol>"` — `alwaysOn` is the B0-proven form (onDemand does not register workflow tools in headless mode); the headless ask layer auto-allows the turn, so the guardrail is the wake prompt's tight constraints; the session id comes from `.glm-conductor/session-witness.json` (landed by the SessionStart hook), read internally by `os-carrier-install`
- **Precision sleep**: the background sleep from the template above — it sleeps until the observed reset_at + 300s (the earliest valid future observed reset instant, regardless of quota status; with no valid future observation the precision layer is absent — disclose the floor-only downgrade honestly) and wakes the session on exit; fully orthogonal to the automation state machine (zero Cron interaction); does not survive restarts (the floor covers that at L0/L1; at L2 a restart leaves no wake at all — recovery is a new session's SessionStart injection)
- **Trigger content** = the structured resume prompt above (TASK_ID, REPOSITORY ROOT and the path placeholders replaced; action first, never enumerate the tool inventory — at L1 the same prompt rides the headless CLI resume command's `-p` argument)
- **Rolling re-arm**: every wake-turn close-out (whenever non-terminal and still authorized for auto) must check the armed sleep — with none in flight, or a past/stale target, issue the next precision sleep to the **current observation's** reset_at+300s (the earliest valid future observation; wake, re-observe, re-arm, iterate to convergence) — establishing / re-arming the wake machinery always precedes work advancement; if quota dies between turns (silent exhaustion), the armed sleep exactly covers the next window boundary
- **The next window is never hardcoded**: rely only on provider reset evidence (the observed reset_at — the single observation rule: the earliest valid future observed reset, regardless of quota status); never derive the next wake as "plus 5 hours"
- **Each wake's workload stays small**: one round of checking, then one stretch of recovered work — never cram all remaining work into a single wake

**A scheduled trigger is a liveness probe**: a wake that starts proves the model execution surface is currently available; a failed or never-started wake produces no repository changes and naturally waits for the next trigger or a user resume.

**Same-session delivery fact**: the host injects the wake prompt as a new turn into the existing session (a mid-session continuation) — a wake turn sees the session's prior context, but the recovery basis is still the repository and state (repository > checkpoint > session memory); cross-session self-contained recovery (the same prompt semantics) needs no session memory, and behavior is identical in both forms.

Never hardcode the reset cycle: quota policy can change, and any fixed cycle silently breaks when it does. Wake timing depends only on the action "re-check at wake" (a fresh quota observation + the idempotent decision + the preflight hard gate) and is decoupled from any specific policy; when quota is unknowable, wait for the user or the next trigger — never fabricate availability.

## Route recovery

A recovered session must re-output the SELECTIVE ROUTE declaration before executing (detailed rules in the orchestration skill's 'Route recovery for recovered sessions', references/operations.md); when the evidence shows the dual-axis judgment (Delegability × Assurance) changed, do ROUTE REASSESSMENT first.

## Completion cleanup

The action list after the goal is complete and accepted (acting only on this task; the order is fixed — **delete before clear; the deletion is expected to fail**):

1. Make the host-side delete attempt for this task's bound automation (located by the automation_id recorded in state), **branched by carrier kind** — L0 (native): **one single session-side attempt** (never retried; no retry storm); **the expected result is failure**: once a carrier has triggered, that session's Cron delete launches silently degrade (the three-state regularity, live-tested) and the model side cannot complete the deletion; a failed deletion is not an error: **report the automation id and title honestly** — the user deleting it manually in the Automations page is **the expected operation**. L1 (os_timer): the attempt is `os-carrier-remove <repo_root> <task_id>` — **genuinely effective, no three-state degradation** (the OS tool's real exit code is the only fact); on success proceed to the clear step; on failure report the OS task name `glm-conductor-<task-id>` honestly for the user's manual disposition
2. **Only after the deletion is confirmed** (L1: `os-carrier-remove` returned ok=true) **or the user confirms the manual deletion** (L0), clear the bound id with `quota-automation-clear`; until then the bound id is retained for reconciliation — never pretend it is already clean
3. **Never delete another task's automation**: only this task's single bound one; deleting the global scheduling surface or another task's automation is a forbidden operation
4. Delete this task's directory `.glm-conductor/tasks/<task-id>/` (only this directory; never touch other tasks' state); when the existing cleanup flow deletes the task directory, its order must never erase the automation_id before the host-side delete attempt
5. Report the final state to the user (including the automation id awaiting manual deletion)

Host scheduling actions (creating / deleting scheduled tasks, issuing background sleeps) are executed by the main session; the runtime itself makes zero host scheduling calls. Window/budget semantics are decoupled from automation liveness: authorization and counting happen only at the two ledgering points `authorize_quota_resume` / `confirm_resume_started`; arming and unbinding happen only at the two binding points `bind_quota_automation` / `clear_quota_automation`.

## Failure cases

- **Scheduling capability degraded or unavailable (matrix caliber)**: the native CronCreate surface missing is **not** "scheduling capability unavailable" — degrade by the fixed decision order of the SKILL.md wake-carrier matrix (the probe's `recommendation` `os_timer` -> arm L1 via `os-carrier-install`; `sleep_only` -> arm L2, the sleep-only witness, the precision layer unchanged, the floor's absence declared as-is); only **L3** — no carrier surface at all — keeps the fail-closed sentence: auto continuity stays unarmed (`quota-continuity-preflight` ready=false) — never claim continuity was enabled, never bind a fake id, never promise continuation under auto/until_done; keep this task's checkpoint directory and report "manually recoverable" to the user with the recovery method — in a new session, enter "read .glm-conductor/tasks/<task-id>/checkpoint.md and continue with the eight-step recovery procedure" (the SessionStart resume injection will prompt automatically)
- **Wake finds auto unarmed** (binding lost / host activation gone): one single kind-aware rebuild attempt (L0: host CronCreate floor -> bind -> preflight PASS; L1: `os-carrier-remove` -> `os-carrier-install` -> bind -> preflight PASS — the OS delete/install round is genuinely effective, no three-state degradation); creation rejected by session ownership (an L0 live carrier actually still exists under the session — only the ledger witness is missing) -> report the situation as-is and rely on the host rhythm, never a retry storm; never fabricate an activation, never resume unarmed
- **Precision sleep not armed** (after an application restart / session rebuild): any wake-turn close-out re-arms per the template — self-healing, not a fault
- **Wake finds the task terminal**: go to terminal cleanup (the delete attempt is expected to fail -> report the automation id for the user to delete manually); never reopen implementation
- **Implementation channel missing** (e.g. visual-implementer unavailable): fail closed — never auto-switch to another executor; hand back to the user
- **Checkpoint severely inconsistent with the repository after a wake**: the repository wins; report the differences to the user and continue; never roll back new repository changes
- **Workflow run unresumable**: when the host reports the run missing or unrecoverable, rebuild from the repository's current state per step 4 of the SKILL.md section 'Workflow recovery (same run id resume)' (recompile and resubmit the unimplemented nodes), never replaying completed work

## ZCode limitations

- **No native quota API**: the plugin-level interfaces (getQuotaRemaining / getQuotaResetTime / onQuotaReset) do not exist and must never be invented; quota observation goes through the read-only resolution surface (the SKILL.md section 'Quota observation surface'); with credentials unavailable, treat as UNKNOWN
- **Quota observations are evidence only**: resolved snapshots, user statements and UI observations are used as evidence; never as a routing axis, never to auto-switch models, never to bypass authorization
- **A subagent cannot spawn subagents**: the orchestrator can only be the main session
- **Subagents see only the MCP services connected at session start**: after a cross-session recovery, re-confirm the required services are available
- **The runtime makes zero host scheduling writes**: creating / deleting scheduled tasks are the main session's host actions; the Conductor side has no scheduler write path
