# v2.4.1 Continuity Hotfix — Detailed Implementation Specification

> **Status:** READY FOR IMPLEMENTATION  
> **Parent plan:** V2_4_1_CONTINUITY_HOTFIX_PLAN.md  
> **Target baseline:** main @ 550df5c45b13425bde6ffe704c02725e6d6c4b26  
> **Implementation posture:** narrow hotfix; preserve v2.4 architecture; targeted tests per unit; one final full regression only.

## 0. Implementer contract

Implement this specification against the live repository. Do not redesign the architecture while implementing.

Required discipline:

1. read the parent plan and current files before editing;
2. preserve Python 3.8+ compatibility;
3. preserve current v2.4 task-state and journal semantics unless this spec explicitly changes them;
4. no direct scheduler SQLite writes;
5. no resident quota watcher/process;
6. no Global Quota Clock dependency;
7. no hard-coded provider/account id;
8. no hard-coded five-hour period;
9. no unbounded auto resume;
10. no full regression until the final integration gate;
11. do not weaken tests to preserve incorrect v2.4 behavior;
12. commit by work package if the implementation environment expects staged commits, but do not push unless authorized by the operator.

## 1. Baseline facts to preserve

The following repository facts are authoritative for this hotfix:

- ZCode Native Workflow uses one run-scoped subagent_model for all Workflow workers.
- Omitting subagent_model inherits the main/session model.
- The Workflow TypeScript compiler cannot set this run-scoped submission property.
- The current text worker compiler/persona and DAG semantics are otherwise retained.
- Native Scheduled Task -> same-run Workflow resume was live-proven in the v2.4 evidence.
- Scheduled turns on the tested build behave as continuation turns of the owning conversation.
- quota_resume currently has manual/auto mode, max_resumes, resume_count, automation_id.
- resume_count is committed only after ResumeWorkflowRun is accepted.
- Global Quota Clock has been intentionally removed from the Conductor runtime.

## 2. New invariants

### INV-MODEL-01 — Explicit Flash worker model

Every Conductor delegate/full Workflow launch must use an explicit provider-qualified model id ending in:

~~~text
/GLM-5.3-Flash
~~~

The following are invalid:

- omitted/None/empty model;
- bare GLM-5.3-Flash;
- any id ending in /GLM-5.3;
- any other model family;
- an id not present in the host-provided configured-model list.

If the configured-model list contains exactly one provider-qualified GLM-5.3-Flash, it may be selected automatically.

If it contains more than one, selection is ambiguous and must fail unless an explicit exact id is supplied.

### INV-CONT-01 — Auto continuity armed before quota-consuming work

For mode=auto:

~~~text
automation_id MUST be non-empty
before CreateWorkflow launch
and before automatic ResumeWorkflowRun.
~~~

For mode=manual, no automation is required.

### INV-CONT-02 — Liveness renewal precedes one-shot resume

If a one-shot activation is used, a future successor activation must be created and bound before calling ResumeWorkflowRun.

### INV-CONT-03 — Recurring activation stays armed

If recurring scheduling is used, do not delete/recreate the activation on every successful wake. Keep it until terminal cleanup, explicit cancellation, or an operator-approved strategy change.

### INV-CONT-04 — Host acceptance precedes resume accounting

Preserve existing rule:

~~~text
scheduled_activation_decision
    -> host ResumeWorkflowRun accepted
    -> confirm_resume_started
~~~

Never increment resume_count merely because a wake fired or a resume was attempted.

## 3. Work package H1 — Workflow submission/model preflight

### 3.1 New module

Create:

~~~text
plugins/glm-conductor/runtime/workflow/submission.py
~~~

This module is pure Python/stdlib and has no host/network calls.

Required constants:

~~~text
FLASH_MODEL_SUFFIX = "/GLM-5.3-Flash"
TEXT_MODEL_SUFFIX = "/GLM-5.3"
~~~

Required public APIs:

~~~text
class WorkflowSubmissionError(ValueError):
    pass

def validate_worker_model(model_id, configured_model_ids=None) -> str:
    ...

def select_worker_model(configured_model_ids, explicit_model_id=None) -> str:
    ...

def build_submission_contract(model_id) -> dict:
    ...
~~~

Required semantics:

#### validate_worker_model

- model_id must be non-empty str;
- must start with account:;
- must contain provider/account prefix plus model segment;
- must end with FLASH_MODEL_SUFFIX;
- must not end with TEXT_MODEL_SUFFIX;
- when configured_model_ids is supplied, exact model_id must be present;
- configured_model_ids must be a list/tuple of non-empty strings;
- return normalized unchanged model_id on success;
- raise WorkflowSubmissionError with a human-readable reason on failure.

Do not accept bare aliases.

#### select_worker_model

Input configured_model_ids is the normalized list returned/extracted by the main session from the host model-discovery surface.

Algorithm:

1. validate collection shape;
2. collect exact ids satisfying provider-qualified Flash suffix;
3. if explicit_model_id is supplied, validate it and require exact membership;
4. otherwise:
   - one Flash match -> return it;
   - zero -> WorkflowSubmissionError;
   - more than one -> WorkflowSubmissionError naming the candidate ids and requiring explicit selection.

Never choose the first candidate silently.

#### build_submission_contract

Return a small machine-readable dict:

~~~json
{
  "subagent_model": "account:.../GLM-5.3-Flash",
  "model_policy": "explicit-flash-required"
}
~~~

No secrets, timestamps, session ids, or quota data.

### 3.2 CLI integration

Extend runtime/cli.py with:

~~~text
workflow-model-select <models-json> [--model <exact-id>]
~~~

Accepted models-json input:

- a JSON file containing an array of model-id strings; or
- follow the current CLI path/input conventions if an established JSON-file helper already exists.

Do not write a host API client.

Output on success: one-line JSON containing at least subagent_model and model_policy.

Exit codes follow existing CLI conventions:

- 0 success;
- 2 validation/configuration rejection;
- 1 unexpected runtime error.

Do not modify v24-compile output semantics.

### 3.3 Orchestration contract change

Update plugins/glm-conductor/skills/orchestration/SKILL.md.

Replace the current permissive CreateWorkflow wording with a mandatory sequence:

1. call the host model-list surface;
2. extract configured exact model ids;
3. run workflow-model-select or equivalent submission helper;
4. if selection fails, do not CreateWorkflow;
5. call CreateWorkflow with the exact returned subagent_model;
6. never omit subagent_model;
7. never substitute the main-session GLM-5.3;
8. record actual selected model in user-facing diagnostics when useful, but do not persist it as task identity.

Explicitly explain that compiler TypeScript is model-agnostic because the host binds one model at run submission.

### 3.4 H1 tests

Add:

~~~text
tests/test_workflow_submission.py
~~~

Minimum cases:

1. exact provider-qualified Flash accepted;
2. missing model rejected;
3. empty rejected;
4. bare GLM-5.3-Flash rejected;
5. provider-qualified GLM-5.3 rejected;
6. unrelated model rejected;
7. explicit configured Flash accepted;
8. explicit unconfigured Flash rejected;
9. exactly one configured Flash auto-selected;
10. zero Flash candidates rejected;
11. multiple Flash candidates rejected as ambiguous;
12. multiple candidates + exact explicit selection accepted;
13. input determinism;
14. no hard-coded account name assertion in production module.

Extend CLI tests for workflow-model-select.

H1 gate only:

~~~text
python3 -X utf8 -m unittest tests.test_workflow_submission tests.test_cli_v24 tests.test_workflow_compiler -v
python3 scripts/validate_plugin.py
~~~

No full regression.

## 4. Work package H2 — Automation binding and continuity preflight

### 4.1 task.py public APIs

Add to runtime/task.py:

~~~text
def bind_quota_automation(repo_root, task_id, automation_id) -> dict:
    ...

def clear_quota_automation(repo_root, task_id, automation_id=None) -> dict:
    ...

def quota_continuity_preflight(repo_root, task_id) -> dict:
    ...
~~~

#### bind_quota_automation

Rules:

- task must exist and be v2.4 state;
- automation_id must be non-empty str;
- task status may be active, waiting_quota, or waiting_user;
- if current automation_id is None -> bind;
- if current automation_id equals supplied id -> idempotent success;
- if current automation_id differs -> reject; no silent replacement;
- replacement must be an explicit clear-then-bind sequence after host-side lifecycle is reconciled;
- do not infer mode=auto; binding an id does not itself authorize auto resume;
- save through state.save_state;
- no secret/prompt persistence.

#### clear_quota_automation

Rules:

- intended to be called only after host deletion is confirmed or operator explicitly reconciles a stale id;
- if automation_id argument is supplied and does not match the stored id -> reject;
- None stored -> idempotent success;
- clear to None;
- do not change mode/max_resumes/resume_count;
- terminal states are allowed so cleanup can occur after completion.

#### quota_continuity_preflight

Return a machine-readable dict, never mutate state.

For manual:

~~~json
{
  "ready": true,
  "mode": "manual",
  "automation_id": null,
  "reason": "manual mode does not require scheduled activation"
}
~~~

For auto with bound automation:

~~~json
{
  "ready": true,
  "mode": "auto",
  "automation_id": "...",
  "reason": "auto continuity armed"
}
~~~

For auto without automation:

~~~json
{
  "ready": false,
  "mode": "auto",
  "automation_id": null,
  "reason": "auto continuity is not armed"
}
~~~

Do not claim that the host automation still exists; this API proves durable binding, not host liveness. Host-side inspection remains required at recovery boundaries.

### 4.2 state.py

Keep the existing quota_resume top-level shape. Do not create a second continuity state machine.

Retain automation_id validation as null or non-empty string. Update docstrings/comments to make its meaning explicit:

> automation_id is the currently associated future native Scheduled Task witness for auto continuity; its presence is required by Conductor preflight but does not independently prove host existence.

No epoch/subscription fields.

### 4.3 CLI integration

Add stable commands:

~~~text
quota-automation-bind <repo_root> <task_id> <automation_id>
quota-automation-clear <repo_root> <task_id> [automation_id]
quota-continuity-preflight <repo_root> <task_id>
~~~

All emit one-line JSON.

Preflight returning ready=false should use exit code 2 so the main session can mechanically treat it as a launch/resume blocker.

### 4.4 Interaction with existing quota APIs

Do not change authorize_quota_resume to fabricate an automation id.

Authorization and arming are separate facts:

~~~text
authorize -> user permission
bind      -> future activation association
preflight -> permission + liveness witness checked
~~~

scheduled_activation_decision may continue to return resume-authorized based on quota/budget state, but the host procedure MUST run quota-continuity-preflight before actual automatic ResumeWorkflowRun.

Optional strengthening is allowed: scheduled_activation_decision may itself refuse resume-authorized when mode=auto but automation_id is absent. If implemented, update all tests and docs consistently. Preferred implementation is to strengthen it because it removes a second path around INV-CONT-01.

If strengthened:

- auto + no automation_id + usable quota -> waiting-user or a new error is NOT allowed without spec change;
- prefer returning remain-waiting with a clear "continuity-unarmed" reason while leaving status waiting_quota, because lack of automation is an operational liveness fault rather than exhausted authorization;
- do not consume budget.

### 4.5 H2 tests

Extend tests/test_quota_resume_v24.py and relevant state/CLI tests.

Minimum cases:

1. manual preflight ready without automation;
2. auto authorization alone -> preflight not ready;
3. bind -> ready;
4. same-id rebind idempotent;
5. different-id rebind rejected;
6. clear exact id -> unarmed;
7. clear None when already clear idempotent;
8. clear wrong expected id rejected;
9. terminal task cleanup allowed;
10. invalid automation ids rejected;
11. bind does not alter mode or budget;
12. clear does not alter mode or budget;
13. auto scheduled decision cannot lead to host resume path without armed preflight;
14. no budget consumption on unarmed wake.

H2 gate:

~~~text
python3 -X utf8 -m unittest tests.test_quota_resume_v24 tests.test_task_lifecycle tests.test_state_v24 tests.test_cli_v24 -v
python3 scripts/validate_plugin.py
~~~

No full regression.

## 5. Work package H3 — Arm-before-work orchestration and wake protocol

### 5.1 Launch protocol

Update orchestration and continuity skills so delegate/full has two branches.

#### Manual continuity

~~~text
create task
-> compile
-> explicit Flash model selection
-> writer-acquire
-> CreateWorkflow(subagent_model=explicit Flash)
-> record workflow run
~~~

No automation required.

#### Auto / user says until_done

The user statement is authorization intent only. It does not create an unbounded state.

Required order:

~~~text
create task
-> quota-resume-authorize with explicit bounded max_resumes
-> create native future Scheduled Task
-> quota-automation-bind
-> quota-continuity-preflight must PASS
-> compile
-> explicit Flash model selection
-> writer-acquire
-> CreateWorkflow(subagent_model=explicit Flash)
-> record workflow run
~~~

If Scheduled Task creation fails:

- do not claim auto continuity is enabled;
- do not bind a fake id;
- do not proceed under the auto/until_done promise;
- report degraded/manual-only continuity or require operator action according to the calling context.

### 5.2 Preferred recurring wake contract

The canonical unattended path is a recurring native Scheduled Task.

Wake prompt MUST carry enough stable identifiers to recover from repository truth:

- exact task_id;
- repository root;
- instruction to load current task state first;
- instruction to refresh quota at the wake;
- instruction to run quota-continuity-preflight before automatic resume;
- instruction to inspect exact workflow_run_id from task state;
- instruction to call ResumeWorkflowRun only when decision is resume-authorized and preflight ready;
- instruction to call quota-resume-confirm only after accepted host resume;
- terminal cleanup instruction.

Do not embed full workflow source, secrets, provider credentials, or stale quota assumptions.

Wake behavior:

~~~text
load task
if terminal:
    delete associated automation once
    clear bound id only after confirmed deletion
    stop

if active:
    no-op

if waiting_user:
    no automatic resume

if waiting_quota:
    refresh quota
    make decision
    verify continuity preflight
    if resume-authorized:
        ResumeWorkflowRun(same run id)
        if accepted:
            quota-resume-confirm
~~~

Repeated wakes must be safe.

### 5.3 One-shot fallback contract

Only if recurring scheduling is unavailable or intentionally not used.

At wake:

~~~text
load state
refresh quota/reset evidence
if task may continue automatically:
    create successor future activation
    bind successor id
    preflight PASS
only then:
    ResumeWorkflowRun
    confirm if accepted
~~~

To replace the fired one-shot automation id safely:

1. reconcile the fired/current automation;
2. clear its bound id;
3. create successor;
4. bind successor;
5. preflight;
6. resume.

Never clear first if that would leave a long-running auto continuation with no successor and no ability to create one. If the host supports creation before deleting/clearing the fired record, create successor first, then atomically move the Conductor binding through the explicit reconciliation sequence available to the host procedure.

Do not derive "next window" by adding a hard-coded 5 hours. Use provider reset evidence when available or a supported recurring/retry schedule.

### 5.4 Terminal cleanup

For completed/cancelled/failed tasks:

- attempt deletion of only the bound automation;
- on confirmed delete, clear automation_id;
- on delete failure, report stale automation association; do not pretend it is cleaned;
- no retry storm;
- never delete another task's automation.

If task directories are later deleted by existing cleanup procedures, cleanup ordering must not erase the automation id before host deletion has been attempted.

### 5.5 Recovery context

Update recovery rendering only if necessary to expose:

- auto continuity armed/unarmed;
- automation id if safe and useful;
- next safe action.

Do not make SessionStart perform host scheduling or network actions. It remains local/read-only.

### 5.6 Skill/document contract tests

Extend validator/static tests so they fail if:

- orchestration says model may be inherited for delegate/full;
- orchestration CreateWorkflow sequence omits explicit subagent_model;
- auto/until_done launch sequence does not place scheduling/bind/preflight before CreateWorkflow;
- continuity docs state that auto mode can be armed with automation_id null;
- one-shot path resumes before successor arming;
- Global Quota Clock or direct SQLite scheduling is reintroduced as production dependency.

H3 gate:

~~~text
python3 scripts/validate_plugin.py
python3 scripts/smoke_plugin_load.py
python3 -X utf8 -m unittest tests.test_workflow_submission tests.test_quota_resume_v24 tests.test_cli_v24 tests.test_recovery_v24 -v
~~~

No full regression.

## 6. Work package H4 — Integrated acceptance and release truth sync

### 6.1 Integrated targeted test bundle

Run once after H1-H3 are complete:

~~~text
python3 -X utf8 -m unittest   tests.test_workflow_submission   tests.test_workflow_compiler   tests.test_cli_v24   tests.test_quota_resume_v24   tests.test_task_lifecycle   tests.test_state_v24   tests.test_recovery_v24   tests.test_writer_guard   tests.test_completion_guard   tests.test_agent_model_binding -v

python3 scripts/validate_plugin.py
python3 scripts/smoke_plugin_load.py
~~~

Include any directly affected additional tests discovered by imports/grep.

### 6.2 Live host smoke A — main GLM-5.3, worker Flash

On a ZCode 3.14+ host where main session is GLM-5.3 and at least one GLM-5.3-Flash model is configured:

1. ListModels;
2. run the new selector/preflight;
3. compile a harmless 1-node workflow;
4. CreateWorkflow with explicit returned subagent_model;
5. have child self-report model only as corroboration;
6. inspect run metadata/tool evidence for actual submission model;
7. expected: worker is explicit GLM-5.3-Flash, not inherited GLM-5.3.

Negative smoke:

- omit model at preflight -> rejected before submission;
- explicitly pass GLM-5.3 -> rejected.

Do not intentionally submit a known-wrong model merely to spend quota if static/preflight evidence is sufficient.

### 6.3 Live host smoke B — arm before launch

Use an isolated no-op/test task:

1. create v2.4 task;
2. authorize auto with max_resumes >= 1;
3. verify preflight fails while automation_id is null;
4. create native recurring Scheduled Task;
5. bind id;
6. verify preflight passes;
7. only then start harmless Workflow;
8. record run id.

Evidence must show activation creation timestamp/order precedes Workflow launch.

### 6.4 Live host smoke C — wake/resume

Using a safely stopped test Workflow:

1. task is waiting_quota;
2. bound automation exists;
3. wake refreshes quota;
4. when usable and authorized, preflight passes;
5. ResumeWorkflowRun uses same run id;
6. accepted resume followed by quota-resume-confirm;
7. resume_count increments exactly once.

Repeated wake on completed/non-waiting task must be safe.

### 6.5 Live host smoke D — one-shot fallback, only if practical

If the host supports one-shot better than recurring in the test environment, demonstrate successor-before-resume ordering.

If recurring path is the production default and one-shot cannot be tested cheaply, record one-shot as structurally tested and do not block release solely on a second expensive host smoke. The release report must say which path was live-proven.

### 6.6 Documentation truth sync

After behavior is implemented and proven, update:

- README.md;
- README.zh-CN.md;
- docs/architecture.md;
- docs/core-concepts.md;
- docs/troubleshooting.md;
- plugins/glm-conductor/skills/orchestration/SKILL.md;
- plugins/glm-conductor/skills/continuity/SKILL.md;
- plugins/glm-conductor/skills/continuity/references/long-horizon.md;
- CHANGELOG.md with v2.4.1 entry.

Required user-visible statements:

- delegate/full requires explicit GLM-5.3-Flash worker model selection;
- auto continuity is not armed until a native future activation is bound;
- recurring Scheduled Task is preferred;
- one-shot fallback renews successor before resume;
- until_done remains bounded by max_resumes;
- app-closed Scheduled Task firing remains unverified unless new evidence closes it.

Historical v2.4 evidence docs should not be rewritten to pretend the defect never existed.

## 7. Final regression policy

Only after H1-H4 targeted and live gates pass, run the project full regression exactly once.

Also run CI if available.

If the full suite reveals a failure caused by an intentionally changed v2.4 contract:

- update the obsolete test to the v2.4.1 contract;
- do not add compatibility code solely to satisfy the old contract.

If the failure is unrelated, fix only if required for v2.4.1 release health; otherwise document and do not expand scope.

## 8. Required implementation report

Create after implementation:

~~~text
docs/reviews/V2_4_1_CONTINUITY_HOTFIX_REPORT.md
~~~

It must record:

- baseline SHA;
- implementation branch/HEAD;
- H1-H4 commit SHAs;
- files changed;
- exact targeted tests and results;
- exact full regression result;
- model-selection live evidence;
- auto-continuity arm-before-launch live evidence;
- wake/resume evidence;
- whether recurring and/or one-shot path was live-proven;
- any host limitations;
- final release verdict: READY or HOLD with explicit blockers.

## 9. Recommended implementation order

Use four bounded work units:

~~~text
H1 submission/model contract
        |
        +------+
        |      |
H2 automation binding + preflight
        |      |
        +------+
           |
H3 skill/protocol integration
           |
H4 live acceptance + docs + one full regression
~~~

H1 and H2 may be implemented in parallel because their primary production ownership is disjoint.

H3 starts only after both APIs are stable.

H4 is sequential and owns final truth-sync.

Do not parallel-edit the same skill/docs files.

## 10. Ownership guide

Suggested ownership for implementation agents:

### H1

- runtime/workflow/submission.py
- runtime/cli.py model-selection subsection
- tests/test_workflow_submission.py
- relevant slice of tests/test_cli_v24.py

### H2

- runtime/task.py
- runtime/state.py
- runtime/cli.py continuity subsection
- tests/test_quota_resume_v24.py
- relevant state/task/CLI tests

Because H1 and H2 both touch runtime/cli.py, either:

- assign disjoint line/command ownership with careful integration; or preferably
- keep runtime/cli.py integration in a short sequential merge unit after both helpers exist.

Prefer the second option to avoid textual conflict.

### H3

- orchestration/SKILL.md
- continuity/SKILL.md
- continuity/references/long-horizon.md
- any static/validator contract tests

### H4

- README pair
- architecture/core-concepts/troubleshooting
- CHANGELOG
- final report
- integration/live evidence only

## 11. Stop conditions

Stop implementation and report HOLD if any of these is discovered:

- ZCode no longer supports explicit run-scoped subagent_model;
- configured model discovery cannot identify exact model ids;
- native Scheduled Task cannot be created before Workflow launch;
- Scheduled Task cannot invoke supported same-run resume;
- task state cannot retain an automation id safely;
- a proposed solution requires direct scheduler SQLite mutation;
- a proposed solution requires bringing Global Quota Clock back into Conductor;
- the only available design requires unbounded automatic resume without user authorization.

Do not solve a host capability regression by rebuilding a hidden second scheduler.

## 12. Definition of done

v2.4.1 is done when a real long-running Conductor task has the following guaranteed order:

~~~text
PLAN
  -> user-authorized bounded auto continuity (if requested)
  -> FUTURE WAKE ARMED
  -> explicit GLM-5.3-Flash worker model selected
  -> preflight PASS
  -> WORKFLOW LAUNCH

quota exhaustion
  -> waiting_quota
  -> scheduled wake
  -> liveness still armed / successor armed
  -> quota usable + budget available
  -> same-run ResumeWorkflowRun
  -> accepted
  -> confirm resume count

terminal
  -> delete only associated automation
  -> clear binding
  -> final validation/review/completion
~~~

The two v2.4.0 failure modes are therefore closed: **no accidental GLM-5.3 worker inheritance, and no auto-continuity task that enters quota wait without a guaranteed future activation path.**
