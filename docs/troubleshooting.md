# GLM Conductor Troubleshooting (v2.6.2)

> A user-facing troubleshooting manual: organized as "symptom → verdict → disposition", stating only conclusive behavioral facts; every fact cites the implementing module path for verification. For the full architecture see [architecture.md](architecture.md); for the concept primer see [core-concepts.md](core-concepts.md).
>
> The single entry of the runtime CLI is `plugins/glm-conductor/runtime/cli.py` (this document writes `<cli>` for it; after installation use the actual plugin cache path). Run it with an available Python 3 interpreter (use `python` on Windows when no `python3` launcher exists). The v2.6.2 CLI subcommands are exactly (the authoritative command face, matching the USAGE at the top of `runtime/cli.py`): `review-record` / `quota-resolve` / `quota-wait` / `quota-resume-authorize` / `quota-resume-decision` / `quota-resume-confirm` / `v24-compile` / `writer-acquire` / `writer-release` / `writer-show` / `task-record-workflow-run` / `v24-record-run` (legacy low-level thin shell) / `workflow-model-select` / `worker-model-pin` / `quota-automation-bind` / `quota-automation-clear` / `quota-continuity-preflight` / `task-create` / `record-validation` / `task-complete` / `task-fail` / `task-cancel` / `exit-waiting`; quota diagnostics additionally have the standalone script `runtime/quota/report.py`. Asking about a subcommand not listed in this document or in USAGE (permit / lease / wave / clock / bridge / policy / verify-* / host-check etc.) is meaningless — those faces no longer exist, nor do their backends.

## 1. Quota diagnostics: report.py dual modes and plan caliber

**When to use**: a task is stuck at `waiting_quota` / `waiting_user` making no progress; resume decisions keep returning `remain-waiting`; you want to confirm the current quota windows and reset times.

**How to run** (read-only diagnostics; the exit code is always 0 — this is not a gate, do not intercept based on it):

```
python <plugin-root>/runtime/quota/report.py            # text: human-readable
python <plugin-root>/runtime/quota/report.py --json     # machine-readable (for orchestration decisions)
```

**Output interpretation**: for each window (`5-hour` / `weekly`) the used / remaining / reset values, `status:` (`AVAILABLE` / `PRESSURE` / `EXHAUSTED` / `UNKNOWN`) and the `plan:` recovery advice (continue / checkpoint / resume_at / periodic_fallback). Three diagnostic modes.

Sample of the current human-readable output (normal state). **Language note (owner ruling): report.py's human-readable output is intentionally kept in Chinese — the `/glm-conductor:quota` contract presents it to the user as-is — so it is quoted verbatim below:**

```
GLM CODING PLAN (provider: zai, source: zcode-provider)

5-hour:
  used: 40%
  remaining: 60%
  reset: 2h 14m

weekly: not present

status: AVAILABLE — 窗口余量充足
plan: continue（额度余量充足，继续当前工作）
```

1. **Normal report** — window details + status + plan;
2. **Probe failure** — one error-classification line per provider (auth / unavailable / malformed / network / unknown; no key and no response body);
3. **unavailable** — no credentials found; follow the configuration guidance in the output (environment variable `GLM_CONDUCTOR_QUOTA_API_KEY` first; the provider configuration of a logged-in ZCode `~/.zcode/v2/config.json` is the documented fallback).

**lite plan caliber**: the weekly window is an **optional window** — lite plans in practice have only the 5-hour window; the parsing layer tolerates a missing weekly and never assumes both windows necessarily exist (`runtime/quota/parser.py`; windows are identified by semantic fields: unit=3/number=5 → five_hour, unit=6/number=1 → weekly). A lite user seeing "only the 5-hour line" is the normal shape, not a fault.

**Runtime resolution**: `python <cli> quota-resolve <repo_root> [--force-refresh]` outputs the four-level hierarchy resolution (fresh cache ≤300s → provider → stale cache → `UNKNOWN`; never defaults to AVAILABLE; `--force-refresh` skips the cache and forces a provider pass) — `runtime/quota/resolver.py`. The resume semantics of the four states (the completion guard consumes no quota; the four states only feed the quota_resume decision chain):

| status | Meaning | `scheduled_activation_decision` behavior |
| --- | --- | --- |
| `AVAILABLE` / `PRESSURE` | usable (PRESSURE is remaining-margin pressure; a resume is still allowed) | authorized, with budget **and armed** (automation_id non-empty) → `resume-authorized`; auto but unarmed → `remain-waiting` (the reason contains `continuity-unarmed`) |
| `EXHAUSTED` | exhausted | `remain-waiting` |
| `UNKNOWN` | data unobtainable (no credentials / network failure / no cache) | `remain-waiting` — availability is never fabricated |

**Resume operation entry points** (since AF-03): the stable entries for the four steps wait / authorize / decide / confirm are the CLI `quota-wait` / `quota-resume-authorize` / `quota-resume-decision` / `quota-resume-confirm` (single-line JSON output; see `runtime/cli.py`) — quota-resume runtime operations go through these four subcommands, not `python3 -c` direct calls into the runtime API (authorization itself is still gated by the user at the skill / policy layer; the CLI does not self-authorize). **Auto continuity arming** (v2.4.1) has three more commands: `quota-automation-bind` / `quota-automation-clear` / `quota-continuity-preflight` — a mode=auto task must complete "create the Scheduled Task → bind → preflight PASS" before launch / automatic resume (arm-before-work); when `quota-continuity-preflight` returns `ready=false` it reports with **exit code 2** (that is the launch/resume block signal, not a command fault).

**Window-mechanics caliber**: at the reset_at instant the window restores to 100% (weekly takes precedence); the next reset_at materializes only when a model call happens inside the new window — a pure query never advances it (`runtime/quota/time_utils.py` — the canonical landing point of these time primitives for parser / resolver / report; the former window_math dead code was deleted in v2.6.0).

## 2. v2.3 legacy tasks: detection and disposition

**Symptom**: the recovery summary / completion guard reports "v2.3 legacy task: wrap up or explicitly abandon it under 2.3.x (v2.4 does not auto-migrate and never fabricates v2.4 completion evidence)"; or `load_task` / a lifecycle operation is refused carrying the same guidance. In the recovery injection a legacy task renders as one line (`runtime/recovery.py`):

```
Legacy task refactor-auth-9c1d2e: v2.3 task detected — v2.3 legacy task: wrap up or explicitly abandon it under 2.3.x (v2.4 does not auto-migrate and never fabricates v2.4 completion evidence); next action: explicitly retire legacy task
```

**Verdict**: v2.4 only detects, never migrates (`detect_legacy_task` / `is_legacy_state` in `runtime/state.py`). Any of the following markers makes it a v2.3 legacy task:

- the top-level collection keys `work_units` / `permits` / `leases` / `receipts`;
- the `permit` / `lease` / `receipt` runtime fields inside `work_units[i]`;
- `status` is a non-empty string outside the v2.4 seven-state vocabulary (a v2.3 phase state, e.g. `finalizing`).

**Disposition**:

1. If the task should still be completed → switch back to the v2.3.x plugin version to close it out (v2.3.x can always be recovered from Git history / tags);
2. If no longer needed → explicitly abandon: archive / delete the `.glm-conductor/tasks/<task-id>/` task directory (real repository changes are what git says; the task ledger is unrelated to them);
3. Afterwards rebuild the task under v2.4 as usual — do **not** hand-edit a legacy state.json into v2.4 shape (`validate_state` reports an error as usual for files missing v2.4 required keys, and fabricated completion evidence is never accepted by the completion guard).

## 3. Writer-guard leftovers: inspect with `writer-show`, release with `--force`

**Symptom**: completion-guard check 1 blocks with "Completion blocked: this repository's writer guard is held by another task (task …)", but the reported holding task has long since died (session crash, task directory deleted by hand, etc.); a new delegated `writer-acquire` conflicts; or (since AF-04) a delegated task's completion is blocked: "Completion blocked: task <id> has a registered delegated run, but the repository writer guard reservation is missing (missing writer reservation …)" — a delegated task with a run id is required, at both registration (`runtime.task.record_workflow_run`; the canonical CLI is `task-record-workflow-run <repo_root> <task_id> <run-id>` — writes state + the guard precondition + the event + the repo_root ledger document; `v24-record-run` is a legacy low-level thin shell: it writes no state and carries no guard precondition) and at completion, to still hold this repository's write reservation. This is a lifecycle invariant and does not cover bare host Workflow invocations outside the Conductor protocol.

**Verdict**: first inspect the current holder:

```
python <cli> writer-show <repo_root>
```

The output is `{holder: {repo_identity, task_id, workflow_run_id, created_at} | null}`. A `writer-acquire` conflict returns the same-caliber holder information (`{"ok": false, "conflict": {…the three holder keys…}}`, zero writes). The guard **never expires automatically** — no TTL / generation / heartbeat (the `runtime/writer_guard.py` spec); `created_at` is diagnostic information only, and a leftover record conflicts no matter how old it is.

**Disposition** (by priority):

1. A delegated task blocked by "reservation missing" (the guard record was deleted by hand etc.; the task itself is still alive) → re-acquire the reservation under this task's identity to re-attach the run id: `writer-acquire <repo_root> <task_id> --run-id <run_id>` (a repeated acquire by the same task is an idempotent success); if the reservation is already held by another task at that point, go to 2 / 3 below;
2. The holding task is still alive → wait for it to wrap up (terminal closure `task.complete` / `fail` / `cancel` releases the guard automatically), or walk that task through the completion guard;
3. The holding task is confirmed dead (directory deleted / the user confirmed abandonment) → explicit release:

```
python <cli> writer-release <repo_root> <holder_task_id> --force
```

`--force` is the operations channel of "explicit release by the operator after inspection": the release is executed with **the holder's own task_id** (the guard module itself provides no bypass around the holder), and stdout reports the cleared holder's four-field information. A normal (non-leftover) release does not carry `--force`.

## 4. The reviewer model-binding split: which model a reviewer runs on

**Background fact** (text/visual split since v2.4 AF-02): the frontmatter of `agents/glm-reviewer.md` has **no `model` field** — host testing showed that an unresolvable fixed model id on a single-model host makes the reviewer deterministically unable to start (the plain Agent face hard-fails outright); without the model field the text reviewer **inherits the host / current session model**, which satisfies the text review contract (`scripts/validate_plugin.py` check 2 mechanically anchors this: glm-reviewer carrying a model field is a FAIL). `agents/visual-reviewer.md` is the opposite: the visual review contract requires reading images directly, and inheriting a text-only main-session model would mean "starting successfully but losing the contract capability" — its frontmatter **must** be a provider-qualified multimodal Flash binding (`account:…/GLM-5.3-Flash`, the same as visual-implementer; validate check 2 anchors it in sync: a missing model, a bare id, or a non-Flash suffix is a FAIL).

**Meaning and troubleshooting**:

- A text reviewer (`glm-reviewer`) running on the same model as the main session is **expected behavior**, not a fallback fault; independence comes from the **fresh context + read-only tool whitelist**, not from a cross-model setup;
- The visual review (`visual-reviewer`) runs on the verified multimodal GLM-5.3-Flash binding; when that binding is unavailable on the host (startup errors such as account-connection-unavailable), stop the visual high-assurance route per the visual exception's fail-closed discipline and tell the user — never fall back to a text model (glm-reviewer or the main session) as the visual final review, and never infer from text that the interface is fine;
- Contrast: `visual-implementer` (an implementer, not a reviewer) likewise **keeps** the provider-qualified `model: "account:…/GLM-5.3-Flash"` field — the two visual roles share the same binding, and the text reviewer does not fall under that rule;
- Agent definitions are snapshotted only at session start: after modifying an agent definition you must **start a new session** before dispatching for the change to take effect.

| agent | model field | actual model | channel |
| --- | --- | --- | --- |
| glm-reviewer | none | inherits the host / session model | native Custom Subagent |
| visual-reviewer | `account:…/GLM-5.3-Flash` (fully qualified) | fixed Flash multimodal (fail closed when unavailable) | native Custom Subagent |
| visual-implementer | `account:…/GLM-5.3-Flash` (fully qualified) | fixed Flash multimodal | native Custom Subagent (the visual exception) |
| text worker (delegate/full) | no agent definition | the persona is embedded into the generated source | Native Workflow |

## 5. Workflow worker model selection: handling `workflow-model-select` rejections

**Symptom**: `workflow-model-select` exits 2 — zero candidates / multi-candidate ambiguity / an explicit id that is not configured / a bare alias (an id with no `<provider>/` qualifier) / a `--model` inconsistent with the task pin / a `--task-ref` pointing at an unpinned task with no `--model` given / when consuming a task pin: "the task pin is void; re-run the launch interview for authorization"; or the skill layer reports "a selection failure never proceeds to CreateWorkflow".

**Verdict** (the submission hard gate of delegate/full since v2.4.1, INV-MODEL-01; task-level pin consumption since v2.5; the Flash-tier criterion version-independent since v2.5.2):

```
python <cli> workflow-model-select '<JSON array or file path>' [--model <exact-id>] [--task-ref <task_id>] [--repo-root <path>]
```

- The input is the set of configured exact ids the main session extracted from the host model list (ListModels etc.; passing a temporary JSON file path is recommended; an inline `["..."]` also works); `--repo-root` (v2.5.2) resolves the task ledger root explicitly (default: the current working directory);
- **The Flash tier is a soft recommendation (v2.6.0 WP-M1)**: the soft-recommendation candidates = the configured exact ids whose model segment ends with `-Flash` (version-independent); a bare invocation without `--task-ref` with exactly one Flash candidate → auto-selected (the convenience path); multiple → ambiguous, rejected with all candidates listed; zero → rejected (check the host model configuration); **a configured non-Flash exact id authorized via an explicit `--model` or injected via a task pin is always legal** (role-neutral; rejection merely for a name/suffix is a no-go red line);
- **`--task-ref <task_id>` (v2.5; the strict caliber since v2.6.0)**: reads the task's top-level `worker_model` pin; the value priority = task pin > an explicit `--model` value (**which must match the pin**; a mismatch is rejected — a plain `--model` must not bypass the pin; changes go through the interactive interview re-authorization and an updated `worker-model-pin`) > Flash soft-recommendation auto-selection (**only for bare invocations without `--task-ref`**); **a task with no pin and no `--model` given → exit code 2 and route back to the launch interview** (a single candidate is never implicitly authorized; even exactly one Flash candidate is rejected). Consumption **always checks the current host list**: an illegal pin shape (including on-disk drift to a non-str / empty string) or a pin not in this run's configured set → exit code 2 "the task pin is void; re-run the launch interview for authorization", never falling back to auto-selection, never switching models silently (the write side, `worker-model-pin`, does shape pre-checks only and never checks the host surface — `runtime/cli.py`); a missing task / a v2.3 legacy task → exit code 1;
- A bare `GLM-5.3-Flash` (no `<provider>/` qualifier — `account:` is one provider namespace, not the only one; non-account provider ids such as host-bridged providers are legal exact ids) and unconfigured ids are always rejected — omitting `subagent_model` makes the Workflow inherit the main-session model, which is the v2.4.0 defect A and hard-forbidden by the protocol; a configured non-Flash id is legal via explicit authorization (see above).

**Disposition** (by rejection reason; after a successful selection, CreateWorkflow's `subagent_model` always takes the returned contract value verbatim):

| Rejection reason | Disposition |
| --- | --- |
| Zero candidates | check whether the account has a Flash model configured / fix the configuration and retry; alternatively authorize a configured non-Flash id explicitly via `--model` (zero soft-recommendation candidates does not mean zero legal choices) |
| Multi-candidate ambiguity (no explicit value and no task pin) | in an interactive turn, re-authorize via question 2 of the task launch interview (orchestration §6.0) → persist with `worker-model-pin` → later submissions carry `--task-ref`; or pass `--model <exact-id>` directly for this submission |
| **Task pin void** (exit code 2, since v2.5) | candidate-set drift (a plan expiring / a new plan joining) is expected safe behavior — new candidates are never auto-adopted, an old pin is never auto-replaced. In an interactive turn, re-run interview question 2 to re-authorize; `worker-model-pin` explicitly overwrites a different value (the output carries prev/new) and work continues; **unattended wake / resume turns never ask** — block the submission, move to waiting-for-user, and re-authorize when the user returns |
| Explicit id not configured / bare alias | switch to a provider-qualified configured exact id (not limited to Flash); never guess a fallback, never silently switch to another model, never submit with the parameter omitted |

## 6. "Hooks do not intercept Workflow subagents": the host fact and its meaning

**Host fact** (W0 re-verification, confirmed on the current build 2026-09-20): the sub actors of a ZCode Native Workflow **do not trigger plugin hooks** — a workflow subagent leaves zero hook-visible trace while an active task exists. This is a structural host fact, not a configuration problem, and it is not fixable on the plugin side.

**Meaning (why v2.4 is designed this way)**:

- **Pre-write interception is impossible on the main implementation path** — the v2.3-style dispatch permit gates / agent injection / Bash policy hooks were deleted (keeping a policy hook that only covered main-session Bash would imply a coverage the main path never had);
- Deterministic enforcement converged onto **the two ends**: compile time (ownership stage planning + one writer guard per repository) and the endgame (the completion guard's four checks). Out-of-scope changes **do happen**, but they can never silently pass the completion guard — check 2 names the out-of-scope paths one by one;
- The correct disposition for "a worker changed files it should not have" is therefore: fix / revert the changes → re-run main validation (the diff changed so the change_id necessarily changed; the old validation is automatically stale) → pass the completion guard again;
- The hooks (SessionStart / Stop) take effect only in the **main session** and depend on `python3` being on PATH; a failure of the hooks themselves fails open (stderr reports `ENFORCEMENT DEGRADED` and then allows — the session is never deadlocked); seeing that message means the guard did not do its work this round: it is a visible degradation, not a permission to pass;
- When a permission constraint is needed, use the host's native permission facilities + worker constraints (the node's `interfaces` / `constraints` declarations and persona discipline); do not try to restore dispatch-surface hooks.

## 7. State file locations at a glance (find the right file first)

| State | Location | Notes |
| --- | --- | --- |
| task status | `<repo>/.glm-conductor/tasks/<task-id>/state.json` | the seven-state schema source of truth (`runtime/state.py`) |
| task events | `<repo>/.glm-conductor/tasks/<task-id>/events.jsonl` | append-only; the task-level ten-event vocabulary (`runtime/journal.py`) |
| writer guard | `<repo>/.glm-conductor/writer_guard.json` | the single repository-level reservation (§3) |
| run association | `<repo>/.glm-conductor/workflow-runs/run-<percent-encoded task_ref>.json` | the id association of task ↔ Workflow run (`runtime/workflow/adapter.py`) |
| quota cache | `<repo>/.glm-conductor/quota-cache.json` | a normalized snapshot, bound to the provider identity fingerprint, containing no credentials (`runtime/quota/resolver.py`) |
| hook manifest | `plugins/glm-conductor/hooks/hooks.json` | only the two hooks SessionStart + Stop (effective in a new session after install / update) |

All of them live under the **repository-level** `.glm-conductor/` (prefer adding it to `.git/info/exclude` for local exclusion); that directory is the orchestrator's own ledger — it does not count as repository changes and never participates in change_id. How the ledger root is located: the hook process prefers `ZCODE_PROJECT_DIR`, falling back to the current working directory when missing — for manual troubleshooting, use the directory containing `.glm-conductor/tasks/`. The in-progress state of a Workflow run is not in the table above: that is a host-owned fact; query it through ZCode's run observation face — Conductor only stores the run id.

## 8. Troubleshooting the `python3` hook command on Windows

**Symptom**: on Windows the host reports that it cannot run the hook scripts, or errors with `python3: command not found` / `The system cannot find the file specified`; the completion guard or the recovery injection does not take effect.

**Verdict**:
1. `hooks/hooks.json` declares, per the ZCode spec, that scripts are launched with `python3`;
2. Some plain standard Python installations on Windows (Python Launcher unchecked or no alias configured) have only `python.exe`, with `python3.exe` missing from PATH.

**Disposition**:
- **Self-check**: run in PowerShell:
  ```powershell
  Get-Command python3
  ```
  If it errors that the command was not found, only `python` exists.
- **Quickly create a hard-link alias** (admin or user-level PowerShell):
  ```powershell
  $pyPath = (Get-Command python).Source
  $pyDir = Split-Path $pyPath
  New-Item -ItemType HardLink -Path "$pyDir\python3.exe" -Value $pyPath
  ```
  After creating it, run `python3 --version` again to verify the output; a newly started ZCode session can then invoke the hooks normally.

## 9. Monorepo Git timeouts and `GLM_CONDUCTOR_GIT_TIMEOUT`

**Symptom**: the completion guard or the change_id computation raises:
`git call failed: status timed out (>3.0 s, repo=…; …)` or `git call failed: rev-parse HEAD timed out (>3.0 s, repo=…; …)` — both messages continue: "if the repository is very large or has many dirty files, adjust the threshold via the GLM_CONDUCTOR_GIT_TIMEOUT environment variable" (the git-failure message texts of `runtime/ownership.py` / `runtime/change_id.py`).

**Verdict**:
- To return feedback in time within the Stop hook's 5-second hard-timeout budget, `runtime/ownership.py` and `runtime/change_id.py` default the git subprocess timeout to 3.0 seconds;
- In a monorepo with tens of thousands of files or a network-mounted worktree, a large number of untracked dirty files in the worktree can push `git status --porcelain=v1 -z -uall` transiently past 3.0 seconds.

**Disposition**:
1. **Dynamic adjustment via the environment variable**: set `GLM_CONDUCTOR_GIT_TIMEOUT` (supported range `1.0` – `30.0` seconds, a float):
   - PowerShell: `$env:GLM_CONDUCTOR_GIT_TIMEOUT = "6.0"`
   - Bash: `export GLM_CONDUCTOR_GIT_TIMEOUT=6.0`
2. **Clean up or ignore redundant untracked files**: check that `.gitignore` effectively excludes build artifacts and cache directories (e.g. node_modules, build, tmp) to reduce `git status` scan time.

## 10. One-shot bulk archiving for v2.3 legacy tasks

**Symptom**: on a repository long upgraded from v2.3, `.glm-conductor/tasks/` retains many old-version task directories; the recovery summary or a listing reports many legacy tasks.

**Disposition**: use the repository's dedicated archive script [`scripts/archive_legacy_tasks.py`](../scripts/archive_legacy_tasks.py):
- **Preview the legacy tasks to archive (dry-run; the default safe mode, zero disk writes)**:
  ```bash
  python scripts/archive_legacy_tasks.py
  ```
- **Confirm and execute the physical archive**:
  ```bash
  python scripts/archive_legacy_tasks.py --confirm
  ```
- **How it works**: it reuses `runtime.state.detect_legacy_task` precisely to identify v2.3 tasks and moves them safely to `.glm-conductor/archived-tasks/<task-id>/`. It **never touches** active or completed v2.4+ tasks, and **never touches** corrupt or non-task directories.

## 11. visual-reviewer binding fails at startup: an honest record of a host limitation (v2.5.2 W6)

**Symptom**: dispatching `glm-conductor:visual-reviewer` for a visual task with assurance: high, the subagent fails at startup with an `account-connection-unavailable` connection error and the visual final review cannot proceed.

**Verdict** (a measured host limitation, not a plugin configuration problem): the frontmatter of `agents/visual-reviewer.md` must, per contract (v2.4 AF-02), bind a provider-qualified multimodal Flash model (`account:…/GLM-5.3-Flash`) — inheriting a text-only main-session model would mean "starting successfully but losing the image-reading contract capability", so an unavailable binding is safer than silent degradation. On the current host that binding's account connection is unavailable (the account connection face is not yet open for that model tier) — a **host-side limitation**; zero plugin-side code can fix it (v2.5.2 W6 ruling: documentation honesty only).

**Disposition (the fail-closed status quo)**:

- The visual high-assurance route stops per the fail-closed discipline: report the binding as unavailable to the user honestly, **never fall back to a text model** (glm-reviewer or the main session) as the visual final review, and never infer from text that the interface is fine;
- **Manual acceptance instead**: the acceptance of visual tasks is done by the user personally viewing the screenshots / the running result, or downgraded to assurance: standard (main-session text validation + the user's own visual check);
- Once the host fixes that account connection, the visual-reviewer binding becomes usable again automatically (no agent-definition change is needed — the fix is on the host's connection face); agent definitions are snapshotted only at session start, so after the host fix you must **start a new session** before dispatching.
