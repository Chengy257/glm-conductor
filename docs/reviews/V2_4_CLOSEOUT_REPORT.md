# v2.4 Implementation Closeout Report

> **Campaign:** v2.4 native-first re-baseline, executed 2026-09-21 on branch
> `review/zcode-3.14-native-workflow` (local commits only; push pending user instruction).
> **Mode:** four phase-workflows (one native Workflow per phase, all unit implementers
> GLM-5.3-Flash), main session (GLM-5.3) as analyst/planner/acceptor; cross-quota-window
> continuation via native scheduled wake (until_done, automation-68c12efd).
> **Authority:** `docs/reviews/V2_4_NATIVE_WORKFLOW_REBASELINE_SPEC.md` and the four
> phase specs + workflow execution plans under `docs/roadmap/`.

## 1. Removed subsystems (net ≈ −63k production lines across Phases 2-3)

- Execution runtime: dispatcher, dispatch_wave, permit machinery, per-unit leases,
  agent_run ledger, reconcile, resume_manifest, provenance receipts, task_manager
  (replaced by the small `runtime/task.py` lifecycle module), legacy WorkUnit runtime
  (twin-relocated in Phase 1, deleted in Phase 2), text flash-implementer agent.
- Hooks: Agent|Task Pre/PostToolUse and Bash-policy hooks removed; `hooks.json` retains
  only SessionStart (recovery summary) + Stop (completion guard).
- Quota control plane: execution phases (NORMAL/PRESSURE/DRAINING/BLOCKED), watcher +
  watcher_store, epoch, primer, accounting, scheduler, control, observer; Global Quota
  Clock (clock/clock_store/identity) with its CLI; continuity layer (resume authorization
  machinery, subscription, wake bridge); activation_transport; scheduler_facts; host/
  (direct ZCode scheduler SQLite retiming) — all removed from the Conductor runtime graph.
- v2.3 task states are NOT migrated: v2.4 detects legacy task directories and refuses
  them with recovery guidance (finish/retire under 2.3.x or explicitly abandon).

## 2. Retained semantics (the v2.4 core)

- Selective routing vocabulary (solo/delegate/audit/full; assurance standard/high).
- Static DAG: `work_unit` (node spec) + `dependency` (pure graph) + `ownership.plan_stages`
  (compile-time conflict serialization; ambiguous patterns fail closed).
- Native Workflow compiler (`runtime/workflow/`): deterministic DAG→TS, single text-worker
  persona, six-field NodeResult, run-id correlation only (no child-state mirroring).
- Repository writer guard: one active write Workflow per repo; conflict reports holder
  ids; release on completion or explicit `--force`; never auto-expires.
- Task state: seven durable statuses; task-level `change_id` as the single freshness
  primitive for validation/review/completion; four-check completion guard (no active
  workflow / paths within ownership union / fresh validation / high-assurance ship review).
- Review: native fresh-context read-only reviewers (glm-reviewer, visual-reviewer;
  host-portable model inheritance; visual-implementer stays the Custom Subagent exception).
- Quota: observation core only (provider/_http/credentials/parser/resolver/report +
  adapters/time math) + bounded task-level resume (manual default; auto requires explicit
  authorization; budget consumed only after a confirmed host resume).

## 3. Test evidence

- Full regression (the campaign's single run, Phase 4 final gate):
  `Ran 630 tests in 23.256s — OK` (v2.3's 2385-test suite shrank with the deleted
  architecture; deleted-subject suites were removed per rule 12, survivors rewritten).
- Validator 15/15 and plugin smoke-load 0 failures re-verified independently at every
  phase gate; import-chain and py_compile gates on the reduced module set.
- Live evidence: three Phase-1 workflow smokes; Phase-2 delegate e2e (two correct guard
  blocks observed: out-of-scope paths listed, stale change_id) and full/high chain through
  a native reviewer ship verdict; Phase-3 decision-chain six-semantics proof; Phase-4
  full-route e2e with the final **allow → completed → guard release** transition and a
  second native reviewer ship; stopped→ResumeWorkflowRun→running lifecycle exercised on
  the final build (scheduled-fire half of the wake path is covered by frozen W0 §13.2
  host evidence plus this campaign's hourly wake automation, not re-proven this round).

## 4. Residual host limitations (documented, not hidden)

- Reviewer model-portability is guaranteed statically (no pinned model field); the
  fresh-session launch proof requires a plugin-cache refresh + new session (deferred to
  release time; the cached 2.3.2 definitions still run this session's hooks, including
  the Bash policy gate which denied `rm` during acceptance — expected until refresh).
- Closed-app scheduled-task firing remains unverified (W0); scheduled turns are mid-turn
  continuations of the owning session on this build.
- CI remains the authoritative gate for lint/platform Python once pushed; local runs used
  Windows python 3.7.9 with `-X utf8`.
- The accidental one-shot resume smoke was replaced by the direct resume proof above after
  repeated CronCreate dispatch failures; the mechanism gap is documented rather than
  papered over.

## 5. Release readiness audit (spec §7)

| Check | Result |
| --- | --- |
| delegate/full single execution substrate (Native Workflow) | PASS |
| no duplicate per-node runtime truth | PASS (grep-verified zero residue) |
| reviewer model binding host-portable | PASS (static; live proof deferred to cache refresh) |
| high-assurance reviewer fresh-context, read-only | PASS (two live ship verdicts) |
| ownership overlap cannot silently parallelize | PASS (unit + live compile rejection) |
| second active write Workflow prevented | PASS (live conflict + force-release) |
| main validation required + change_id freshness | PASS (live stale detection) |
| quota resume bounded + user-authorized | PASS (live decision-chain) |
| Global Clock absent from Conductor graph | PASS (deleted; extraction inventory delivered) |
| internal scheduler SQLite writes absent | PASS (host/ deleted) |
| docs/skills/metadata describe v2.4 | PASS (README 200/200 mirror, architecture 14 subjects, skills rewritten, 2.4.0) |
| repository-wide consistency audit | PASS (V2_4_RETIRED_SURFACE_AUDIT.md, every hit classified) |

**v2.4.0 is release-ready pending: user-gated push, CI run, plugin cache refresh, and the
deferred fresh-session reviewer launch check.**
