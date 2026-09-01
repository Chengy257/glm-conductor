# GLM Conductor

**English** | [简体中文](./README.md)

![Version](https://img.shields.io/badge/version-2.1.0-blue.svg)
![License](https://img.shields.io/badge/license-MIT-blue.svg)
![ZCode Plugin](https://img.shields.io/badge/ZCode-plugin-green.svg)
![Models](https://img.shields.io/badge/models-GLM--5.3%20%2F%20GLM--5.3--Flash-orange.svg)
![CI](https://github.com/Chengy257/glm-conductor/actions/workflows/validate.yml/badge.svg)

> **Selective orchestration for GLM coding agents in ZCode.**
> GLM-5.3 conducts, GLM-5.3-Flash implements, independent read-only reviewers audit — and the key contracts are enforced deterministically by runtime hooks, not left to model self-discipline.

## Introduction

GLM Conductor is a ZCode plugin providing **selective routing** and **runtime enforcement** for coding agents on the GLM Coding Plan. The flagship model (GLM-5.3) acts as the architect — planning, verification, acceptance — while the cost-effective GLM-5.3-Flash executes bounded implementation specs, and fresh-context read-only reviewers deliver independent final audits.

The motivation is straightforward: GLM-5.3 and GLM-5.3-Flash differ only slightly in intelligence, but Flash costs 10–20× less on API pricing and roughly ⅓ of the quota consumption under the Coding Plan. Instead of running everything on the flagship, work is routed along two independent axes — **is the remaining implementation bounded enough to delegate** (Delegability) and **does completion need an independent audit** (Assurance).

Since v2, the plugin upgrades its key contracts from prompt text to **runtime enforcement**: out-of-scope changes can never silently pass the completion gate, completion claims without evidence are blocked, stale verification/review evidence expires automatically, and quota-exhausted tasks schedule wake-ups against exact reset times. All of this runs deterministically via plugin-distributed hooks and stdlib-only runtime modules.

## Key Features

**Routing & execution**

- **Two-axis selective routing** — a machine-auditable `SELECTIVE ROUTE` declaration (solo / delegate / audit / full) before the first delegation; routes can be reassessed in either direction on new evidence
- **Tiered execution** — judgment-dense work stays on the flagship; fully-specified high-throughput implementation goes to Flash (standard & visual channels), optimizing both cost and quota
- **Routing preflight** — read-only recon (ROUTING PREFLIGHT) when key facts are unknown, instead of routing on weak evidence
- **Task Context Pack** — a bounded, compressed context pack handed from flagship to executor: GLM-5.3 compresses, Flash executes

**Quality & review**

- **Independent read-only final audit** — high-assurance tasks get a `ship` / `fix-first` / `rethink` verdict from a fresh-context reviewer; any repair invalidates the prior verdict (automatically enforced via evidence fingerprints since v2)
- **Visual channel** — visual-implementer (multimodal Flash) implements, visual-reviewer reads the screenshots; explicitly bridging the flagship's text-only boundary
- **Structured contracts** — five-part implementation specs + IMPLEMENTATION REPORTs; a completion claim without evidence is invalid

**Runtime enforcement (v2)**

- **Four-check completion gate** — ① ownership: touched ⊆ declared; ② verification: every required command actually run by the parent session and recorded as fresh evidence via `record_unit_verification` / `record_verification`; work-unit completion is subject to the same RB-1 evidence gate; ③ review: fresh `ship` verdict for high-assurance tasks; ④ evidence freshness: verification/review evidence is bound to the recorded repository state via `task_fingerprint`; any later edit makes it stale and blocks completion
- **Multi-repository workspaces** — a task can bind its own Git repository root via state's `repository.root`; the Stop completion gate is evaluated against each task's repository, and the workspace root does not need to be a Git repository; the ledger (state / journal / lease) stays where it is
- **Bash policy gate** — table-driven allow/ask/deny: destructive commands (rm -rf, git reset --hard, force push) are always denied; pushes, migrations, releases, and permission changes escalate to *ask* under high assurance
- **Verification / review provenance (v2.1 M6)** — `verify-unit` / `verify-task` have the runtime actively execute verification commands under restrictions (whitelist + policy gates, zero-TOCTOU same-instant fingerprint) and drop durable receipts; review verdicts are declared via `review-record`, binding the final fingerprint into a fresh ship receipt — the completion gate's review check accepts only the receipt; hand-written state fields are no longer trusted
- **Visible degradation** — enforcement failures never block the session (fail-open) and report `ENFORCEMENT DEGRADED` on stderr

**Long-horizon & quota**

- **Task & work-unit management** — Work Unit dependency graphs (ten-state lifecycle, cycle rejection, deterministic topological order), five-gate dispatch admission, evidence-based resume reconciliation (completed units never replayed), and explicit joins that always require task-level global verification
- **File leases & bounded parallelism** (experimental) — all-or-nothing acquisition, foreign-owner conflict rejection; **default concurrency 2, cap of 4** (since v2.1), quota-adjusted budget (AVAILABLE → policy value, PRESSURE/UNKNOWN → 1, EXHAUSTED → 0); `prepare_dispatch_wave` issues the whole batch of permits and markers in one call — wave members must be dispatched concurrently in the same turn
- **Runtime quota resolution (v2.1 M5)** — the pre-dispatch quota status is resolved through a four-level hierarchy (fresh cache → provider → stale cache → UNKNOWN) and **never defaults to AVAILABLE**; UNKNOWN degrades fail-open to a budget of 1 without blocking dispatch
- **Quota-aware continuity** — query Coding Plan usage (zero credential persistence), four-state evaluation, EXHAUSTED schedules wake-ups against the latest blocking reset; falls back to periodic liveness probing when unavailable; `/glm-conductor:quota` for on-demand diagnostics
- **Authorized resume (v2.1 M5)** — quota EXHAUSTED runs a deterministic transition chain: four-mode authorization (manual / notify / auto_once / until_done, escalations require user authorization recorded in state) + window budget (`consumed_quota_windows`) + self-sufficient one-shot wake prompt (host-verified: a wake continues the same session); budget exhaustion flips the task to `waiting_user` and no further automation may be created — the resume entry point is `quota-resume`
- **Cross-session resume** — per-task checkpoints with an eight-step recovery check; repository truth always wins over recorded state

**v2.0.1 runtime integrity hardening** — closing out H1-H8 from the v2.0.0 comprehensive review: a gated completion lifecycle (`finalizing` + a status transition table, with `completed` committable only through the Stop gate), cross-field route invariants, and four-way fail-closed task discovery (a corrupt state.json is no longer treated as "no task"). The `task_manager` transaction boundary fuses the dispatch lifecycle into four APIs (plan → lease → transition → bookkeeping → save → journal) with deterministic crash-window recovery. Leases gain TTL/generation/heartbeat renewal, and `recover_leases` automatically cleans up stale leases — no manual leases.json deletion after a crash. Verification evidence is explicitly bound to a work unit id (legacy events without a `unit` field are no longer trusted by resume reconciliation — an intentional breaking change; the main session re-verifies). The release-hardening patch (RB-1/RB-2) closes the last two gaps: work-unit completion (`completed`) is now gated on fresh unit-bound verification evidence up front (all-match, zero-side-effect rejection, fail-closed on git/fingerprint read failures), and tasks can bind their own Git repository root via state's `repository.root` — the Stop gate is evaluated per task (per-root snapshot cache, a single task's repository failure degrades only that task), so multi-repo workspaces no longer degrade the gate wholesale. CI covers ubuntu + windows × Python 3.8/3.13.

**v2.1-alpha1 Control-Plane Closure (M1-M3)** — turning mechanisms that existed but could be bypassed into machine-checked facts: dispatch permits (a PreToolUse deny gate — implementation-executor subagents without a valid `GLM_CONDUCTOR_DISPATCH` marker are denied; replay/expiry/forgery mechanically rejected); an execution-policy authorization source of truth (hard concurrency cap of 4, cross-quota-window auto-resume off by default, escalations require explicit user authorization recorded in state); runtime-observed agent lifecycle (PostToolUse journals `agent_launched`/`agent_dispatch_failed`, non-substitutable by hand-written events); a crash-recovery trio — automatic SessionStart resume-context injection, four-way reconcile (reuse result / resume with progress / clean redispatch / manual ruling instead of re-dispatching everything), and a transaction-refreshed resume manifest; plus a runtime CLI entrypoint replacing inline invocations. See CHANGELOG 2.1.0-alpha1.

**v2.1-alpha2 Batch-Two Closure (M4-M6)** — bringing bounded parallelism, quota decisions, and evidence provenance into deterministic runtime facts: the dispatch wave transaction (`prepare_dispatch_wave` issues the whole batch of permits and markers in one call; wave members dispatch concurrently in the same turn — "wait for the first to return" is forbidden; default concurrency 2 / cap 4 with a quota-adjusted budget; a wave-membership ring blocks stale permit replays); runtime quota resolution (`quota-resolve`, four-level hierarchy, never defaults to AVAILABLE) and the authorized-resume chain (`quota-exhausted` four-mode authorization + window budget + self-sufficient one-shot wake, budget exhaustion flips to `waiting_user`); verification / review provenance receipts (`verify-unit` / `verify-task` runtime-observed execution + durable receipts, `review-record` producing the fresh ship receipt the completion gate solely accepts). See CHANGELOG 2.1.0-alpha2.

**v2.1-alpha3 Runtime Integrity Closure** — hardening the batch-two machinery against the review findings one by one: the quota-resume control plane closes (a post-wake `quota-resume` forces a provider refresh and never trusts the sleep-period fresh cache, EXHAUSTED wake-ups are planned uniformly by `scheduler.plan_resume` on the latest-reset basis, per-unit interruption origins (`quota_interrupted_from`) are preserved, and reconcile runs before redispatch — running-interrupted units are no longer blindly re-readied); review provenance binds to a real reviewer invocation (a PostToolUse marker journals the `reviewer_invoked` ledger fact, anchoring the re-validation chain — reviewer whitelist + replay idempotence — and the completion gate's receipt provenance check); wave transaction compensation (permits land before the wave record, with full rollback on permit/save failure so a wave record never carries partial leases); permit integrity fails closed (`validate_permit` enforces the full id/mode/reason/timestamps/consumed chain, including the `expires_at <= now` expiry boundary); multi-repo reconcile splits its dual roots (`reconcile_agent_run` reads ledger facts from the ledger root and evaluates git evidence against the task's bound git root); plus the SH trio — `wake-record` idempotence with a pre-write authorization re-check, documented release-metadata discipline, and verified Python version consistency. Verified live by dogfood scenarios R1-R5 (multi-window quota exhaustion, crash-during-worker quota recovery, wave partial-permit fault injection, fake-review rejection + real-reviewer chain, ledger-root ≠ git-root reconcile); the full suite runs 1328 tests green and `validate_plugin` 15/15. See CHANGELOG 2.1.0-alpha3.

## Routing Matrix

| Delegability | Assurance | Route | Implementation | Independent review |
| --- | --- | --- | --- | --- |
| low | standard | `solo` | GLM-5.3 main session | No |
| high | standard | `delegate` | executor subagent | No |
| low | high | `audit` | GLM-5.3 main session | Yes |
| high | high | `full` | executor subagent | Yes |

`executor` (standard / visual) and `continuity` (foreground / resumable / idle) are declared as independent dimensions. See the [architecture doc](./docs/architecture.md) for the full workflow.

## Roles

| Role | Model | Tools | Responsibility |
| --- | --- | --- | --- |
| Main session (architect) | GLM-5.3 | All | Requirement clarification, architecture & routing, decomposition, specs, verification reruns, acceptance |
| flash-implementer | GLM-5.3-Flash | Read/write | Executes bounded five-part implementation specs |
| visual-implementer | GLM-5.3-Flash | Read/write + vision | Visual implementation; returns `VISUAL_CAPTURE_REQUEST` when screenshots are needed, parent captures and re-invokes |
| visual-reviewer | GLM-5.3-Flash | Read-only + vision | Independent visual final audit (`VISUAL REVIEW`) |
| glm-reviewer | GLM-5.3 | Read-only whitelist | Independent text final audit (`GLM REVIEW`) |

## Prerequisites

- [ZCode](https://zcode.z.ai) client (tested with 3.9.2; earlier versions may lack multimodal, scheduled-task, or custom-subagent capabilities)
- GLM Coding Plan (or a Z.ai account) with both GLM-5.3 and GLM-5.3-Flash connected
- **Python 3.8+** (`python3` on PATH) — the runtime for v2 enforcement hooks. The official Windows installer adds it to PATH by default; self-check: `python3 --version` in a fresh terminal

## Installation

Open a workspace in ZCode first (the plugin management page requires one). After installing or updating, **create a new session** for subagents, skills, and hooks to load.

### From the GitHub marketplace (recommended)

1. Open **Settings → Plugins**, click **Create → Add plugin marketplace**
2. Choose the GitHub repository source and enter:

   ```
   https://github.com/Chengy257/glm-conductor
   ```

3. Install glm-conductor from the **Personal** section

### From a local directory (dev/testing)

1. `git clone https://github.com/Chengy257/glm-conductor.git`
2. **Settings → Plugins → Create → Add plugin marketplace**, choose "local manifest file or directory", point at the repository root (where `marketplace.json` lives), or drag the folder in
3. Install from the **Personal** section

> ⚠️ Do not select `plugins/glm-conductor/.zcode-plugin/plugin.json` — that is the *plugin* manifest, not the *marketplace* manifest. Selecting it yields an empty marketplace with 0 plugins.

### Verifying the installation

- The marketplace source panel shows glm-conductor with 1 plugin (0 means the wrong manifest was selected)
- In a new session: role subagents appear under Settings → Subagents; `/orchestration`, `/continuity`, and `/glm-conductor:quota` are visible in the `/` menu
- Hook self-check: `echo '{}' | python3 ~/.zcode/cli/plugins/cache/glm-conductor/glm-conductor/<version>/hooks/stop_gate.py` exits 0 (list the version directory with `ls ~/.zcode/cli/plugins/cache/glm-conductor/glm-conductor/`)

### Updating & uninstalling

- Update: refresh the glm-conductor marketplace in the marketplace-sources panel, then update the plugin; takes effect in new sessions
- Uninstall: from the plugin detail page; remove the whole marketplace in the sources panel

## Quick Start

In a new session:

```
Plan and implement this feature with glm-conductor:orchestration — declare the route and complete verification
```

The main session emits the route declaration first, then executes accordingly:

```
SELECTIVE ROUTE
mode: delegate
delegability: high
assurance: standard
executor: flash-implementer
continuity: foreground
reason: implementation is bounded by explicit interfaces, owned files, and deterministic verification
```

Other examples:

```
# Visual task (UI rework needing independent visual review)
SELECTIVE ROUTE
mode: full / executor: visual-implementer / assurance: high
reason: bounded UI implementation with broad user-facing impact requires independent visual review

# Long task expected to cross quota windows
SELECTIVE ROUTE
mode: delegate / continuity: resumable
reason: multi-hour migration may be interrupted by availability windows; checkpoint enables safe resume
```

Check Coding Plan usage and the four-state evaluation:

```
/glm-conductor:quota          # text report (window usage, reset countdown, scheduling advice)
/glm-conductor:quota --json   # machine-readable output
```

## Commands & Skills

| Command / skill | Purpose |
| --- | --- |
| `/orchestration` | Route declaration, delegation contracts, work-unit management, review flow |
| `/continuity` | Long-horizon continuity: checkpoints, eight-step recovery, quota-aware scheduling |
| `/glm-conductor:quota` | Quota diagnostics (text + JSON; credentials resolved automatically, never persisted) |
| `enforcement` skill | User-facing enforcement reference: environment self-check, message meanings, recovery when blocked |

## Runtime Limitations

GLM Conductor builds on ZCode's native local session lifecycle — it is not a cloud scheduler or background daemon. Registered limitations:

- **Local desktop**: the client must stay running and the machine awake; remote/headless workspaces are unsupported
- **Enforcement scope**: hooks fire in the main session only (ZCode sub-sessions do not trigger hooks) — enforcement sits at the completion boundary, not at write time; the Bash policy gate likewise covers main-session calls only; a missing `python3` degrades fail-open with `ENFORCEMENT DEGRADED` on stderr
- **Registered conservative false positives**: strings quoting destructive text and `git rm -r --cached` are conservatively denied by the Bash policy
- **Bounded parallelism is experimental**: cap of 4; leases assume a single orchestrating main session — multiple sessions operating on the same task directory in parallel are unsupported
- **Multi-repo workspace boundary**: per-task `repository.root` binding is supported (the completion gate evaluates against each task's repository); diff attribution for multiple top-level active tasks inside the same Git repository remains informally unsupported — keep one active top-level task per repository (bounded parallelism of multiple Work Units within a task works as before)
- **Quota awareness**: uses verified provider-api monitoring endpoints (zero credential persistence); no fabricated native quota interfaces, no hardcoded 5-hour resets; endpoint failures fall back to periodic liveness probing
- **Visual topology**: Browser/Computer Use are main-session-only — screenshots are captured by the parent and judged by Flash-based roles
- **Subagents**: cannot spawn further subagents; only MCP services connected at session start are visible
- **Scheduled/idle tasks**: constrained by ZCode automation mechanics and account capabilities

See the [architecture doc](./docs/architecture.md) for the complete registered-limit list.

## Release discipline

GitHub Release metadata is bound to version semantics: `alpha` / `rc` pre-releases must be marked **prerelease** ("Set as a pre-release"); only `stable` versions are published as the latest stable release — marketplace users rely on this flag to decide whether updates are offered automatically.

## References & Acknowledgments

This project stands on the shoulders of the following projects/ecosystems (ordered by depth of reference):

| Project | What was referenced |
| --- | --- |
| [sol-advisor](https://github.com/DannyMac180/sol-advisor) (MIT) | The original inspiration: selective routing orchestration. glm-conductor formalized its one-dimensional risk ladder into the Delegability × Assurance two-axis model (under a two-tier model lineup, the "high-risk lane" equals delegability:low, implemented by the flagship), and uses subagents' naturally fresh contexts as the "fresh reviewer" semantics |
| [zai-org/zai-coding-plugins](https://github.com/zai-org/zai-coding-plugins) (glm-plan-usage plugin) | Official reference for the quota monitoring endpoint (`/api/monitor/usage/quota/limit`) and auth format (`Authorization: <API Key>`, no Bearer prefix) — the v2 credential discipline (§37) was designed against it and verified live |
| ZCode official plugin ecosystem (example-plugin, zcode-plugins-official) | Plugin manifest and `hooks/hooks.json` conventions (`${ZCODE_PLUGIN_ROOT}`, process hooks), Claude-compatible hook payloads and the `permissionDecision` output contract, skills/agents directory conventions |
| [Claude Code](https://claude.com/claude-code) (Anthropic) | Contract reference for the hooks event model (PreToolUse/Stop, `stop_hook_active` continuation caps) and subagent orchestration patterns |
| CodexBar and community quota monitors | One of the cross-validation sources for Coding Plan usage-endpoint behavior |

## Contributing

Issues and pull requests are welcome. Development notes: plugin files (subagent definitions, skills, manifests) only take effect in new sessions; run `python3 scripts/validate_plugin.py` and `python3 -m unittest discover -s tests` before committing, and keep `plugin.json` / `marketplace.json` valid JSON.

## License

[MIT](./LICENSE) © 2026 glm-conductor contributors
