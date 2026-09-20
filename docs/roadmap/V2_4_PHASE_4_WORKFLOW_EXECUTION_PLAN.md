# v2.4 Phase 4 Workflow Execution Plan — Integration, Documentation, Release Closeout

> **Status:** auto-progression per user mandate 2026-09-21; Phases 1-3 exit gates PASSED.
> **Authority chain:** REBASELINE_SPEC → IMPLEMENTATION_PLAN §7 → `V2_4_PHASE_4_INTEGRATION_RELEASE_SPEC.md` → **this document**.
> **Mode:** one native Workflow (units R1-R5, Flash), main session does live smokes + closeout (R6), local commits only, **one** full regression.

## 1. Unit decomposition

| Unit | Work package | Primary files (ownership) | Depends on |
| --- | --- | --- | --- |
| R1 | P4-A retired-surface audit | `docs/reviews/V2_4_RETIRED_SURFACE_AUDIT.md` (new) + any code/test stale hits it must fix | — |
| R2 | P4-B user docs + metadata | `README.md`, `README.zh-CN.md`, `CHANGELOG.md`, `plugins/glm-conductor/.zcode-plugin/plugin.json`, `marketplace.json`, `docs/README.md` | R1 |
| R3 | P4-B technical docs | `docs/architecture.md`, `docs/core-concepts.md`, `docs/troubleshooting.md` | R1 |
| R4 | P4-C skills + command help | `skills/orchestration/SKILL.md`, `skills/enforcement/SKILL.md`, `skills/continuity/SKILL.md` (+ their references/ if present), `commands/quota.md` | R1 |
| R5 | P4-D/E test re-baseline + programmatic final gate | test pruning if the full run reveals dead subjects; runs targeted set + validator + smoke load + **one full regression** | R2, R3, R4 |

Main session (R6, after workflow): live smokes — minimal delegate chain e2e, full-route
e2e WITH final allow transition (closing the Phase 2 gap), native reviewer smoke,
scheduled-task resume smoke (one-shot CronCreate resuming a deliberately stopped
workflow); then closeout report `docs/reviews/V2_4_CLOSEOUT_REPORT.md`, release-readiness
audit, final commits.

## 2. Unit specs (condensed; full context in the authority docs)

### R1 — Retired-surface audit (P4-A)

Grep the whole repo for the 21 retired concepts (flash-implementer as text executor,
dispatcher, dispatch wave, permit, per-unit lease, Work Unit runtime status, unit
verification receipt, review receipt authority, agent-run ledger, reconcile mini-task
state, resume manifest, Bash policy, finalizing, DRAINING/BLOCKED execution phases, quota
watcher, quota epoch, subscription/accounting, Window Primer, Global Quota Clock in
Conductor, Activation Transport, Task Wake Bridge, direct scheduler SQLite write).
Classify EVERY hit: (1) active production reference → fix it yourself (code/tests only);
(2) historical changelog/review evidence → keep, cite in the audit doc; (3) migration/
extraction note → keep. Produce `docs/reviews/V2_4_RETIRED_SURFACE_AUDIT.md` with the
full hit classification table. Docs/skills stale content is NOT yours to rewrite (R2/R4)
— list those hits as assigned. Gate: audit doc exists with ≥1 classified row per concept;
survivor targeted suite green; validator.

### R2 — User docs + metadata (P4-B)

New positioning: selective routing + Native Workflow execution + minimal deterministic
assurance + optional bounded quota resume; Global Quota Clock only as a separate future
companion project (point to the extraction inventory). Remove claims of runtime dispatch
permit enforcement, per-unit leases/receipts, Global Clock as a Conductor pillar, wake
bridge, Bash policy coverage, flash-implementer as text substrate. README EN + zh
mirror structure (existing mirror discipline), badges/version → 2.4.0; CHANGELOG gains a
2.4.0 entry summarizing the rebaseline (deletion posture, retained semantics, migration
note for v2.3 task states); plugin.json version 2.4.0 (description updated to English
release-surface conventions); marketplace.json description synced; docs/README.md index
reflects v2.4 docs. Gate: validator (15/15 includes manifest JSON checks), README pair
line-count mirror ±5, survivor suite.

### R3 — Technical docs (P4-B)

`docs/architecture.md` becomes the v2.4 current-truth source (route matrix; static DAG;
Workflow compiler boundary; host-owned execution state; ownership conflict handling; one
active repo writer; task state; change_id; main validation; reviewer path; completion
guard; visual exception; minimal quota resume; Global Clock separation — the 14 required
subjects from the spec). `docs/core-concepts.md` and `docs/troubleshooting.md` rewritten
to v2.4 semantics (troubleshooting: quota diagnostics via report.py, legacy v2.3 task
detection guidance, writer-guard stale release via --force, no hook-permit entries).
Gate: validator + all three files mention the 14 subjects (self-check via grep, listed in
notes) + survivor suite.

### R4 — Skills + command help (P4-C)

- orchestration: route decision (Delegability × Assurance), static DAG creation, delegate/
  full → v24-compile + Native Workflow launch procedure (writer guard, record run), main
  validation, high-assurance review. Remove executor-axis persistence and dispatch CLI.
- enforcement → reframe as the completion-guard skill: ownership scope, writer guard,
  change_id freshness, four-check completion guard, visible degraded behavior when a
  guard cannot evaluate. Keep the skill name (compatibility).
- continuity → Workflow resume + waiting_quota + manual/auto bounded resume + native
  Scheduled Task wake (the idempotent decision contract; scheduled turns are mid-turn
  continuations). Remove Global Clock and wake-bridge instructions.
- commands/quota.md: final state — quota-resolve + report.py only.
- references/ subfiles under skills: update or retire stale ones; keep file inventory
  consistent.
Gate: validator (skill frontmatter checks) + py-free grep: skills contain no
flash-implementer/dispatcher/wake-bridge/Clock production instructions + survivor suite.

### R5 — Test re-baseline + programmatic final gate (P4-D/E)

1. Verify no test file's subject has vanished (list tests/ against runtime module
   inventory); prune dead-subject files with per-file justification (rule 12).
2. Run and require green: full survivor targeted set; `scripts/validate_plugin.py`;
   `scripts/smoke_plugin_load.py`; **one full regression**
   (`python3 -X utf8 -m unittest discover -s tests -p test_*.py`, timeout 600s).
   On full-run failure: classify real regression vs stale legacy test vs environment;
   fix real ones, delete stale ones (justified), document environment-only ones; rerun
   only the affected subset, then the full suite ONCE more (the spec's bounded retry).
Gate: all four green; report includes the full-regression test count line verbatim.

## 3. Workflow topology

phase 1: `R1 退役面审计`; phase 2: `R2 用户文档 ∥ R3 技术文档 ∥ R4 技能重写`;
phase 3: `R5 测试重置与终局程序门`（含唯一一次全量回归）. Board `phase4-units`;
markdown report `phase4-report`. Same fix-loop discipline (R5 gets the standard 2 fix
rounds; the full-regression rerun inside a fix round follows the spec's bounded policy).

## 4. Main-session acceptance (post-workflow)

1. Diff review of every doc/skill/metadata file against the spec's required content.
2. Independent re-run: validator, smoke load, full survivor suite, and the full
   regression ONCE more only if anything changed after R5 (otherwise accept R5's run as
   the campaign's single full-regression record, per the one-run discipline).
3. Live smokes: (a) minimal delegate e2e chain (guard→task→compile→run→validation→
   completion→release); (b) full-route e2e WITH final allow transition; (c) native
   glm-reviewer dispatch; (d) scheduled-task resume: stop a scratch workflow, one-shot
   CronCreate (+3 min) whose prompt resumes it, observe the fire→resume→complete cycle,
   then the automation self-completes (one-shot).
4. R6 closeout: `docs/reviews/V2_4_CLOSEOUT_REPORT.md` (removed subsystems, retained
   semantics, test evidence incl. the full-regression line, residual host limitations:
   reviewer fresh-session portability proof pending cache refresh, closed-app scheduled
   firing unverified, CI authoritative for lint); release-readiness audit checklist
   (spec §7); final commits; report to user; campaign memory update; until_done
   automation self-delete (single CronDelete attempt).
