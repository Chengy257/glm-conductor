# ZCode 3.14 Native Workflow Spike Probes

> **NON-PRODUCTION.** These assets exist only for the compatibility spike described in
> `docs/reviews/ZCODE_3_14_NATIVE_WORKFLOW_REBASELINE_PLAN.md`. They are not wired into the
> plugin runtime, are not covered by the production test contract, and must never be imported
> by `plugins/glm-conductor/` code.

## What is here

- `probes/*.ts` — standalone dynamic-workflow scripts (ZCode 3.14 facade: `agent()`, `ask<T>()`,
  `phase()`, `Promise.all`). Each file maps to one or more WF cases from the plan and is
  runnable independently by submitting it to `CreateWorkflow` with `path` pointing at the file:
  - `probe_wf01_minimal.ts` — WF-01 minimal deterministic single-child workflow
  - `probe_wf02_roles.ts` — WF-02 plugin custom-subagent addressability
  - `probe_wf03_model.ts` — WF-03 model/thought-level binding self-report (run twice: default
    and with an explicit out-of-plan `subagent_model` to observe submission validation)
  - `probe_wf04_05_tools.ts` — WF-04 tool permission preservation + WF-05 MCP visibility
  - `probe_wf07_hooks.ts` — WF-06/WF-07/WF-21 marker-bearing child launches (hook trace is
    observed by diffing `.glm-conductor/` before/after the run, from the main session)
  - `probe_wf08_nested.ts` — WF-08 nested-subagent boundary
  - `probe_wf09_10_parallel.ts` — WF-09 parallel fan-out timing + WF-10 disjoint writes
  - `probe_wf11_conflict.ts` — WF-11 conflicting writes to one fixture file
  - `probe_wf12_13_staged.ts` — WF-12 staged dependency + WF-13 result handoff
  - `probe_wf14_15_cancel.ts` — WF-14 background behavior + WF-15/17 cancel→inspect→resume
    lifecycle (staggered sleeps 5s/20s/40s; the main session stops the run mid-flight)

- `fixtures/` — canonical fixture content for the write experiments. Probes write into the
  git-ignored evidence tree (below), never into production source files.

## Evidence policy

- Raw run output (child answers, snapshots, run records) goes to
  `.glm-conductor/spikes/zcode-3.14-workflow/<run-id>/`, which is git-ignored via the existing
  `.glm-conductor/` rule. Commit only sanitized summaries inside
  `docs/reviews/ZCODE_3_14_NATIVE_WORKFLOW_RESULTS.md` and the two TSV matrices.
- No probe depends on private credentials, deletes user data, writes to ZCode internal
  databases, or mutates production GLM Conductor runtime state under `.glm-conductor/tasks/`.
  Probe files live under `.glm-conductor/spikes/` only, which no production hook reads.
- Hooks visibility (WF-06/07) relies on the fact that `.glm-conductor/` is empty at spike start
  on a clean checkout; any file appearing under `.glm-conductor/` during a probe run is
  attributable to plugin hooks or the probe itself (probes only write under
  `.glm-conductor/spikes/`).

## Safety notes

- `probe_wf11_conflict.ts` intentionally creates a write conflict, but only on a disposable
  fixture file inside the ignored evidence tree.
- `probe_wf14_15_cancel.ts` is designed to be cancelled mid-run; stopping a workflow run is a
  supported host action and leaves no repository state beyond the ignored evidence files.
- Quota usage per probe is intentionally small (short child tasks, single-model host).
