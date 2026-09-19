# ZCode 3.14 Native Workflow Spike — Evidence Recording Template

> Companion to `ZCODE_3_14_NATIVE_WORKFLOW_REBASELINE_PLAN.md`.
>
> This file is a recording template for the implementation agent. Copy/fill the required sections into `ZCODE_3_14_NATIVE_WORKFLOW_RESULTS.md`; do not treat empty fields as negative evidence.

## 1. Run metadata

```text
RUN_ID:
DATE:
ZCODE_VERSION:
ZCODE_BUILD:
OS:
WORKSPACE_TYPE: local | WSL | SSH | Docker | other
REPO_COMMIT:
BRANCH:
PLUGIN_VERSION:
PRIMARY_MODEL:
PRIMARY_PROVIDER_ID:
PRIMARY_THOUGHT_LEVEL:
FRESH_SESSION_AFTER_PLUGIN_CHANGE: yes | no
FRESH_SESSION_AFTER_AGENT_CHANGE: yes | no
NOTES:
```

## 2. Evidence item format

Use one block per atomic fact.

```text
EVIDENCE_ID: EV-<NNN>
CLASS: DOCUMENTED | OBSERVED | INFERRED | UNKNOWN
CASE_ID: WF-<NN>
TIMESTAMP:
QUESTION:
SETUP:
ACTION:
EXPECTED:
OBSERVED:
STATUS: PASS | FAIL | BLOCKED | UNKNOWN
ARTIFACTS:
- <path or supported run/log identifier>
HOST_RUN_IDS:
- <workflow id / child id / task id if exposed>
MODEL_EVIDENCE:
- requested_role:
- requested_model:
- actual_model_id:
- actual_provider_id:
- source:
HOOK_EVIDENCE:
- event:
- fired: yes | no | unknown
- input_identity_fields:
- decision_effect:
REPOSITORY_EVIDENCE:
- before:
- after:
- changed_files:
SECURITY/PRIVACY:
- secrets_redacted: yes | no | n/a
INTERPRETATION:
ARCHITECTURE_IMPACT:
OPEN_QUESTION:
```

Rules:

- `OBSERVED` must state the exact action performed.
- `INFERRED` must cite one or more prior evidence IDs.
- `UNKNOWN` must state what prevented resolution.
- Redact credentials and private tokens before committing.
- Do not store full model prompts unless they are intentionally small, non-secret spike fixtures.
- Prefer exact run IDs/timestamps over screenshots when both are available.

## 3. Per-case result format

Each WF case in the final results document must use:

```text
### WF-XX — <name>

STATUS: PASS | FAIL | BLOCKED | UNKNOWN

Evidence:
- EV-...
- EV-...

Observed facts:
- ...

Interpretation:
- ...

Impact on GLM Conductor:
- ...

Remaining unknowns:
- ...
```

A PASS means the case's stated pass condition was established, not merely that a command completed.

## 4. Required identity capture

Whenever a workflow launches a child agent, capture as many of these as the host exposes:

```text
workflow_run_id:
workflow_definition_id_or_name:
parent_session_id:
child_run_id:
child_agent_name:
child_agent_type:
child_model_id:
child_provider_id:
child_status:
start_time:
end_time:
background:
retry_or_resume_generation:
tool_permission_profile:
mcp_visibility:
```

For unavailable fields, write `NOT_EXPOSED` rather than inventing a value.

## 5. Required hook capture

For WF-06, WF-07, and WF-21, record every relevant event in order.

```text
sequence:
  - seq: 1
    event: <PreToolUse|PostToolUse|PostToolUseFailure|Stop|SessionStart|other>
    matcher: <if visible>
    origin: <main|workflow|child|unknown>
    target_role: <role if visible>
    tool_use_id: <if visible>
    marker_visible: <dispatch/review marker or none>
    input_mutation_supported: yes | no | unknown
    deny_supported: yes | no | unknown
    result:
```

The key architectural question is whether a deterministic enforcement boundary exists, not merely whether logging exists.

## 6. Parallel-write capture

For WF-10 and WF-11, record:

```text
children:
  A:
    owned_files:
    start:
    end:
    observed_base_state:
  B:
    owned_files:
    start:
    end:
    observed_base_state:

timing_overlap_verified: yes | no | unknown
host_isolation_model: shared_worktree | isolated_worktree | serialized | unknown
conflict_detection: explicit | implicit | none_observed | unknown
final_repository_state:
silent_overwrite_observed: yes | no | unknown
```

Do not generalize from disjoint-file success to conflicting-file safety.

## 7. Failure/cancel/resume capture

For WF-15, WF-16, and WF-17:

```text
initial_workflow_run_id:
operation: cancel | fail | retry | resume
operation_time:
status_before:
status_after:
completed_children_rerun:
failed_children_rerun:
new_workflow_run_id:
child_ids_preserved:
partial_repo_changes_preserved:
repo_re_read_observed:
cached_context_replay_observed:
supported_user_action:
recovery_metadata_exposed:
```

If retry and resume are distinct host actions, test and record them separately.

## 8. Quota-boundary capture

For WF-23/WF-24, when naturally observable:

```text
provider_state_before:
quota_window_kind:
reset_at_before:
workflow_status_before:
child_status_before:
quota_block_symptom:
host_message:
workflow_status_after_block:
host_retry_or_resume_affordance:
reset_at_after:
automatic_reactivation_observed:
model_call_required_to_materialize_window:
provider_epoch_awareness_evidence:
authorization_semantics:
accounting_semantics:
```

Do not deliberately burn large quota merely to fill this block.

## 9. Capability matrix status guidance

Use:

- `SUPPORTED`: direct observed behavior satisfies the required semantic.
- `SUPPORTED_WITH_CONSTRAINTS`: observed support exists but with an explicit limitation that matters.
- `NOT_SUPPORTED`: directly established absence or documented unsupported behavior.
- `UNKNOWN`: evidence cannot establish the answer.
- `NOT_APPLICABLE`: capability does not apply to this architecture question.

Examples:

```text
WF child launches plugin custom agent successfully
=> custom agent addressability = SUPPORTED

workflow launches child but actual model silently falls back to parent
=> role model preservation = NOT_SUPPORTED or SUPPORTED_WITH_CONSTRAINTS,
   depending on whether a deterministic correction exists

no natural quota exhaustion occurred
=> provider-aware workflow resume = UNKNOWN
```

## 10. Module disposition evidence threshold

Before assigning a disposition:

### KEEP

Use when the invariant remains project-specific or the host offers no equivalent.

### KEEP_AND_ADAPT

Use when the invariant remains necessary but its integration point changes under workflows.

### REPLACE_BEHIND_ADAPTER

Requires observed host behavior that supplies the execution mechanism while the Conductor-facing contract remains stable.

### DEPRECATE_AFTER_FALLBACK_WINDOW

Requires:

1. replacement path observed;
2. critical invariants preserved;
3. failure/recovery path observed;
4. fallback migration path defined.

### REMOVE_CANDIDATE

Requires all deprecation conditions plus strong evidence that no project-specific invariant remains.

### UNDECIDED

Use whenever an architecture-critical fact is UNKNOWN.

## 11. Final adoption-boundary table template

Fill this in the results report:

| Layer | Owner after re-baseline | Evidence refs | Notes |
| --- | --- | --- | --- |
| Routing policy | CONDUCTOR / HOST / SHARED VIA ADAPTER | | |
| Task decomposition contract | | | |
| Work-unit dependency execution | | | |
| Parallel scheduling | | | |
| Child-agent launch | | | |
| Child lifecycle tracking | | | |
| Ownership conflict protection | | | |
| Verification execution | | | |
| Verification receipts | | | |
| Review topology | | | |
| Review receipts | | | |
| Final acceptance | | | |
| Workflow interruption recovery | | | |
| Provider quota continuity | | | |
| Global quota clock | | | |
| Task wake bridge | | | |

## 12. Final return summary template

```text
IMPLEMENTATION RETURN

commit:
zcode_version:
zcode_build:
results:
  - docs/reviews/ZCODE_3_14_NATIVE_WORKFLOW_RESULTS.md
  - docs/reviews/ZCODE_3_14_NATIVE_WORKFLOW_CAPABILITY_MATRIX.tsv
  - docs/reviews/ZCODE_3_14_NATIVE_WORKFLOW_MODULE_DISPOSITION.tsv

recommendation: A | B | C | D

architecture_critical_unknowns:
- ...

key_host_facts:
- ...

candidate_replacements:
- ...

must_keep:
- ...

validator:
focused_tests:
full_regression:
production_runtime_modified: no
```

