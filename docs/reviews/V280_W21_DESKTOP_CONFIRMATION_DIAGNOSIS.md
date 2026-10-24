# V2.8.0 W-A2 Desktop Confirmation Live Diagnosis (Dual-Arm)

> **Date:** 2026-10-25 (session started 2026-10-25, after the v2.7.0 cache refresh of 2026-10-24)
> **Diagnosing session:** the implementation session itself (a Desktop session), per spec §2 W-A2.
> **Spec:** `docs/roadmap/V2_8_0_DISPATCH_CHAIN_AND_DESKTOP_CONFIRMATION_IMPLEMENTATION_SPEC.md` §2
> **Baseline hook under test:** cached v2.7.0 `hooks/permission_request_workflow.py`
> (session hook face = startup snapshot; script body executed from disk per event — host facts).

## 0. Pre-flight self-check (written record)

1. **Plugin cache is at 2.7.0** — evidence: this session loads skills from
   `C:\Users\user\.zcode\cli\plugins\cache\glm-conductor\glm-conductor\2.7.0\skills\`.
2. **The diagnosing session started after the cache refresh** — the v2.7.0 release chain
   (tag v2.7.0 + cache robocopy mirror) completed 2026-10-24; this session started
   2026-10-25. Therefore **H1 (session hook-face snapshot predating the cache refresh) is
   a-priori excluded for THIS session**.
3. **Deadlock guardrail declared before submission** (spec protocol): if a confirmation card
   appeared with no human answer for 30 minutes, the round would be recorded as a
   hook-fail datapoint; the owner was present and answered cards where they appeared.

## 1. Arm 1 — authorized arm (PASS: no card, no human answer, direct run)

Full launch sequence, minimal one-node DAG (no-op evidence file under
`.glm-conductor/tmp/`):

| Step | Command / action | Observed |
| --- | --- | --- |
| task-create | `task-create . v280-arm1-5e8a31 "<goal>" delegate standard --auto-approve-workflow` | `auto_approve_workflow: true` in created state; status active |
| compile | `v24-compile .glm-conductor/tmp/v280_arm1_dag.json --out gen/v280-arm1-5e8a31.mjs` | `{ok, stages: 1, nodes: 1}` |
| model gate | `workflow-model-select <models.json> --model account:bigmodel-start-plan/GLM-5.3-Flash` (owner-authorized in the v2.8.0 launch interview) | `{subagent_model, model_policy: "user-authorized-explicit-selection"}` |
| writer guard | `writer-acquire . v280-arm1-5e8a31` | `{ok, conflict: null}` |
| **submission** | `CreateWorkflow` (path: `C:\Users\user\ZCodeProject\glm-conductor\gen\v280-arm1-5e8a31.mjs`, subagent_model as selected) | **No confirmation card; no human answer; run started immediately** — `dwfrun-41cf8c72-b6e7-4f8a-88dc-dbd36d72a03a`; the submitting turn was never blocked |
| run result | host completion notification | node `noop-evidence` complete; evidence file `.glm-conductor/tmp/v280-arm1-noop.txt` (27 bytes, byte-verified by the main session: exactly `v2.8.0 arm1 no-op evidence\n`) |
| closure | `record-validation ... passed` → `task-complete` | validation recorded; task completed; writer guard released |

**Datapoint:** the PermissionRequest hook answered the ask-layer race with
`decision.allow` for a path-type submission on a live authorized task — zero manual
confirmation.

## 2. Arm 2 — control arm (PASS: manual card appeared as expected)

Identical shape, WITHOUT task-level authorization:

| Step | Command / action | Observed |
| --- | --- | --- |
| task-create | `task-create . v280-arm2-7c1d94 "<goal>" delegate standard` (no `--auto-approve-workflow`) | state has **no** `auto_approve_workflow` key; status active |
| compile | `v24-compile ... --out gen/v280-arm2-7c1d94.mjs` | `{ok, stages: 1, nodes: 1}` |
| model gate | same explicit selection | selected as above |
| writer guard | **omitted for observation** — the hook's decision chain never reads the guard; recorded here honestly | — |
| **submission** | `CreateWorkflow` (path: `...\gen\v280-arm2-7c1d94.mjs`) | **The manual confirmation card appeared.** The owner answered it manually (allow) — owner's own words after the fact: "I just manually clicked the confirmation". Run `dwfrun-9bef3d80-34aa-4ada-aa4e-bf20c5fc5fd8` then started. |

**Observation-caliber note (calibration correction):** from the submitting session's
in-process viewpoint both arms "returned and started a run" — the arm-2 card was answered
by the owner during the tool call, so only the owner's report distinguishes "no card"
(arm 1) from "card answered by human" (arm 2). Arm 2's datapoint rests on the owner's
direct observation, recorded here verbatim.

**Ledger scan (pollution exclusion):** a full scan of `.glm-conductor/tasks/*/state.json`
(28 tasks incl. all v2.0–v2.7 legacy tasks) shows every pre-existing task is terminal
(completed/cancelled) with no `auto_approve_workflow` key; at arm-2 submission time the
only flagged task (`v280-arm1-5e8a31`) was already `completed` (naturally de-authorized).
The arm-2 card was therefore a genuine hook abstention, not leftover-authorization
pollution.

## 3. Decision-chain readings (per-step, observed)

| Chain step | Reading |
| --- | --- |
| matcher face (PermissionRequest / CreateWorkflow hook registered) | **live in this session** — arm 1 was auto-answered (hook participated in the ask race) and arm 2 surfaced the default card; a dead matcher would have made arm 1 block on a card too |
| (1)–(3) payload / event / tool shape | passed (arm 1 reached step 5 and allowed) |
| (4) path-type under `gen/`, absolute path | passed both arms (identical shapes) |
| (5) authorized-task scan | arm 1: live flagged task → allow; arm 2: unflagged active task, no other authorized task on disk → abstain → manual card |
| H5 probe (Desktop shell honoring ask-layer race) | **refuted**: the hook's `decision.allow` closed arm 1's request without any human answer — the PermissionRequest race layer works in the Desktop shell |

## 4. Root-cause conclusion

- **This session: dual-arm PASS.** H2 (repo_root mismatch), H3 (relative-path base),
  H4 (flag not persisted / not passed) and H5 (Desktop shell bypasses the ask race) are
  all **refuted by observed behavior** on this host/session.
- **The v2.7.0 post-release FAIL datapoint (first confirmation card still blocked) did
  NOT reproduce** in this session. With H2–H5 refuted here and H1 excluded for this
  session by the pre-flight self-check, the most probable root cause of the historical
  datapoint is **H1: the verification session's hook face was snapshotted before the
  2.7.0 cache refresh** (host fact: session matcher = startup snapshot — a session
  started before the refresh never had the PermissionRequest hook registered, so its
  card could only be answered by a human). That session's startup time was not logged,
  so H1 is the best-supported explanation, not a directly confirmed one; the honest
  statement is "not reproduced; H1 most probable; H2–H5 refuted on this host".

## 5. stderr observability note

The host did not surface hook stderr for either submission (both observations rest on
host UI behavior and return timing, not on `PERMISSION_HOOK ...` lines). No spike
file-logging variant was needed: behavioral evidence was sufficient. The W-A1
reason-code matrix (ship face of this release) equips future diagnoses; spec's
`.glm-conductor/tmp/` spike allowance went unused.

## 6. W-A3 disposition (per the root-cause table)

Root cause → **H1** → fix is environment-face guidance only, zero code:

- Troubleshooting entry stating the host fact and the operational rule: **the session
  hook face is a startup snapshot — after a plugin cache refresh, confirmation-free
  behavior is only observable in a session started after the refresh** (see the
  troubleshooting doc updated in this release).

## 7. W-A4 acceptance reading

Dual-arm acceptance on this host: authorized arm runs card-free, control arm surfaces
the manual card — **PASS**. Since the root cause is environmental (H1) with zero code
change to the decision chain, this same dual-arm run doubles as the post-fix
re-verification (nothing to re-test after an environment-face fix). Hook unit tests
(reason-code matrix / multi-source readiness / relative-path caliber / behavior
regression): 23/23 green (`tests.test_auto_approve_workflow`).
