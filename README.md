# GLM Conductor

**English** | [简体中文](./README.zh-CN.md)

![Version](https://img.shields.io/badge/version-2.4.0-blue.svg)
![License](https://img.shields.io/badge/license-MIT-blue.svg)
![ZCode Plugin](https://img.shields.io/badge/ZCode-plugin-green.svg)
![Models](https://img.shields.io/badge/models-GLM--5.3%20%2F%20GLM--5.3--Flash-orange.svg)
![CI](https://github.com/Chengy257/glm-conductor/actions/workflows/validate.yml/badge.svg)

**Selective routing, Native Workflow execution, minimal deterministic assurance, and optional bounded quota resume for GLM coding agents in ZCode.**

GLM Conductor keeps the strong model focused on planning, judgment, and acceptance; delegates bounded implementation to ZCode Native Workflows; and adds independent review when the change warrants it. It owns semantic orchestration and acceptance; ZCode owns execution orchestration.

> **Design goal:** lightweight semantic orchestration for personal coding workflows — decide where work should run, compile bounded work into a canonical DAG, and keep only the deterministic checks prompts alone cannot guarantee. v2.4 is not a second task runtime, workflow engine, permission engine, provenance system, scheduler platform, or quota control plane.

## Why GLM Conductor

- **Spend strong-model reasoning where it has the highest value.** GLM-5.3 handles ambiguity, architecture, routing, validation, and final acceptance; bounded implementation moves to workflow workers.
- **Delegate with explicit contracts.** Delegated work is compiled from a canonical DAG that defines node objectives, dependencies, owned files, interfaces, and constraints before any work starts.
- **Separate implementation from independent review.** High-assurance changes can receive a fresh-context, read-only final review after the main session has validated the implementation.
- **Keep quota waits recoverable.** A task can wait for provider quota and resume through a native ZCode Scheduled Task — only when you have authorized it, within a bounded resume budget.

## How it works

```text
                     Main session — GLM-5.3
               plan · route · validate · accept
                     /                 \
                    /                   \
       solo / audit (main session)   delegate / full
                ↓                          ↓
        main-session work        Native Workflow run
                                 (compiled canonical DAG)

              Minimal deterministic assurance
   change_id · ownership · completion guard · writer guard

              Optional bounded quota resume
       waiting_quota → native Scheduled Task wake
```

ZCode remains the underlying coding harness and owns execution orchestration: workflow run lifecycle, child actors, parallel scheduling, retries, background execution, stop/resume mechanics, and run observability. GLM Conductor persists only the semantics the host does not know.

## Selective routing

Before delegation, the main session evaluates two independent questions:

- **Delegability** — Is the remaining implementation sufficiently bounded and specified to hand off?
- **Assurance** — After main-session validation, would an independent fresh-context review materially reduce risk?

| Delegability | Assurance | Route | Implementation | Independent review |
| --- | --- | --- | --- | --- |
| low | standard | `solo` | main session | no |
| high | standard | `delegate` | Native Workflow | no |
| low | high | `audit` | main session | yes |
| high | high | `full` | Native Workflow | yes |

There is no separate executor axis for ordinary text work: `solo`/`audit` mean main-session implementation, `delegate`/`full` mean Native Workflow implementation. Routing may be reassessed in either direction when new evidence changes the task boundary or risk.

## Model roles

The current recommended assignment is intentionally simple:

| Role | Default model | Responsibility |
| --- | --- | --- |
| Main session | **GLM-5.3** | planning, architecture, routing, validation, acceptance |
| Workflow workers | **session model** | bounded text implementation inside Native Workflows |
| Visual implementer | **GLM-5.3-Flash** | bounded multimodal implementation (Custom Subagent exception) |
| Text reviewer | **host/session model** | fresh-context, read-only final review |
| Visual reviewer | **GLM-5.3-Flash** | fresh-context visual review (pinned multimodal binding; fails closed if unavailable) |

Text workers and the text reviewer inherit the host/session model — the text reviewer deliberately carries no pinned model, which keeps it startable across hosts and plans; the two visual roles are pinned to the provider-qualified multimodal GLM-5.3-Flash binding, and the visual high-assurance route fails closed when that binding is unavailable. These are current assignments, not permanent architectural identities.

## Native Workflow execution

`delegate` and `full` routes share one execution substrate:

```text
Canonical Conductor DAG → Workflow Compiler → ZCode Native Workflow
```

- A Work Unit is a **static DAG node**: `id`, `objective`, `depends_on`, `ownership`, plus optional `interfaces` / `constraints` / `local_check`. It describes what must be done, not where execution currently is — Conductor persists no per-node runtime state.
- The compiler validates the DAG, detects ownership overlaps between nodes that could run concurrently (conflicts are serialized or rejected before launch), generates worker instructions from one canonical persona, and emits a deterministic TypeScript workflow. It never auto-executes — launching the run is the main session's host action.
- ZCode owns everything about the run: parallelism, retries, stop/resume, background execution, and observability. Conductor records only the task ↔ run-id association.

## Minimal deterministic assurance

Delegation is not accepted on model claims alone, and v2.4 keeps the enforcement surface deliberately small:

- **change_id** — one deterministic task-level change identity (hash of the base revision plus the exact owned files currently changed, resolved from the ownership scopes — never the scope strings themselves). Validation and review records bind to it; if the repository changes afterwards, the validation or review is stale and must be redone.
- **Completion guard** — a Stop-hook guard checks four invariants before a task may complete: no active write workflow remains, changed paths are inside the union of DAG ownership, required main validation is fresh (change_id matches), and for high-assurance routes a fresh `ship` review exists.
- **One active write workflow per repository** — a coarse repository writer guard replaces per-unit leases: a second Conductor write workflow cannot acquire the repo until the holder releases it, and delegated run registration and completion refuse a task that skipped acquisition (a Conductor lifecycle invariant; raw host Workflow calls outside the Conductor protocol are outside this guarantee). Workflow-internal parallelism is allowed after compile-time ownership validation.
- **Small task state** — seven statuses (`active` / `waiting_quota` / `waiting_user` / `blocked` / `completed` / `cancelled` / `failed`). There are no per-unit runtime states, dispatch permits, leases, or verification receipts to reconcile.

The main session still owns final acceptance: worker reports are claims, while repository state, diffs, and reproduced validation are evidence. If a guard cannot evaluate, it degrades visibly rather than silently blocking the session.

## Optional bounded quota resume

Quota handling is not a routing axis. A delegated task that runs out of provider quota can enter `waiting_quota` and be woken by a native ZCode Scheduled Task:

- `manual` (default): you resume when you choose. `auto`: a scheduled turn refreshes provider quota; if it is usable, and you have explicitly authorized auto resume within `max_resumes`, the same workflow run is resumed.
- Each actual resume consumes one unit of the bounded budget; once the budget is exhausted, the task transitions to `waiting_user`.
- No quota epochs, subscriptions, watcher processes, or window bookkeeping exist in v2.4. Quota diagnostics are read-only (`/glm-conductor:quota` or `quota-resolve`); the resume lifecycle runs through stable CLI commands (`quota-wait` / `quota-resume-authorize` / `quota-resume-decision` / `quota-resume-confirm`).

> The account-level **Global Quota Clock** is no longer part of GLM Conductor. It is planned as a separate future companion project; see its extraction inventory at [`docs/roadmap/GLOBAL_QUOTA_CLOCK_EXTRACTION_INVENTORY.md`](./docs/roadmap/GLOBAL_QUOTA_CLOCK_EXTRACTION_INVENTORY.md).

## Host requirements and limitations

Requirements:

- **ZCode 3.14+** — v2.4 depends on Native Workflow and native Scheduled Task capabilities (verified against ZCode 3.14.0)
- A GLM Coding Plan or Z.ai account with GLM-5.3 and GLM-5.3-Flash available
- Python 3.8+ for the runtime hooks and CLI

Known limitations, stated honestly:

- **Scheduled firing while the ZCode app is closed is unverified.** Scheduled turns observed on the tested host behave as mid-turn continuations of the owning session; the plugin does not claim wake behavior with the app closed.
- **Reviewer roles are verified on a genuinely fresh session with the final bindings.** After refreshing the plugin cache from the v2.4 tree, a newly opened ZCode session launched both reviewer roles: the text reviewer (no pinned model; inherits the host/session model) produced a conforming `GLM REVIEW` verdict in read-only mode, and the visual reviewer — pinned to the provider-qualified multimodal GLM-5.3-Flash binding — actually read a test image whose contents were never described in its prompt and reported them accurately (blind read, `VISUAL REVIEW` verdict). The visual high-assurance route still fails closed if that binding is unavailable.
- `visual-implementer` remains a Custom Subagent capability exception, not a Native Workflow worker; the visual feedback topology requires the main session to capture screenshots.
- Hooks are limited to SessionStart (resume context) and Stop (completion guard). There are no dispatch-permit, ownership-injection, or Bash policy hooks; use ZCode's native permission facilities plus worker constraints for tool policy.

## Installation

### Requirements

- [ZCode](https://zcode.z.ai) 3.14 or newer
- A GLM Coding Plan or Z.ai account with GLM-5.3 and GLM-5.3-Flash available
- Python 3.8+ for the runtime hooks

### Install from the ZCode plugin marketplace

1. Open a workspace in ZCode.
2. Go to **Settings → Plugins → Create → Add plugin marketplace**.
3. Choose the GitHub repository source and enter `https://github.com/Chengy257/glm-conductor`.
4. Install **glm-conductor** from the **Personal** section.
5. Create a **new session** after installation or update so agents, skills, and hooks are reloaded.

For a local installation, clone the repository and add the repository root — the directory containing `marketplace.json` — as a local marketplace.

> Use the repository-root marketplace manifest. Do not select `plugins/glm-conductor/.zcode-plugin/plugin.json`; that is the plugin manifest, not the marketplace manifest.

## Quick start

For normal orchestration, start a new session and ask GLM Conductor to plan and execute the task:

```text
Use glm-conductor:orchestration to plan and implement this feature. Declare the route first, then validate the result.
```

A route declaration looks like:

```text
SELECTIVE ROUTE
mode: delegate
delegability: high
assurance: standard
reason: implementation is bounded by explicit interfaces, owned files, and deterministic verification
```

For `delegate`/`full`, the main session then builds the canonical DAG, compiles it with `v24-compile`, acquires the repository writer guard, launches the Native Workflow, and after the run finishes records the run association and validates the result.

Useful entry points:

- `glm-conductor:orchestration` — routing, DAG contracts, and review flow
- `glm-conductor:continuity` — checkpoints, `waiting_quota`, and bounded resume
- `glm-conductor:enforcement` — completion guard semantics, diagnostics, and blocked-task recovery
- `/glm-conductor:quota` — read-only quota diagnostics (`--json` for machine-readable output)

## Runtime CLI command surface

All runtime operations go through one CLI (`python <plugin-root>/runtime/cli.py <subcommand>`; single-line JSON output):

| Command | Purpose |
| --- | --- |
| `quota-resolve <repo> [--force-refresh]` · `quota-wait` / `quota-resume-authorize` / `quota-resume-decision` / `quota-resume-confirm` | four-state provider quota resolution (read-only) and the bounded resume lifecycle (wait / authorize / decision / confirm) |
| `v24-compile <dag.json> [--task-ref <id>] [--out <path>]` | compile a canonical DAG into Native Workflow source (generation only, never auto-executes) |
| `writer-acquire <repo> <task_id> [--run-id <id>]` | acquire the repository write reservation |
| `writer-release <repo> <task_id> [--force]` | release the reservation (`--force` after explicit inspection) |
| `writer-show <repo>` | show the current holder |
| `v24-record-run <task_ref> <run-id> [--artifact <path>]` | record the task ↔ workflow run-id association |
| `review-record <repo> <task_id> <reviewer> <verdict> [note]` | record a task-level review verdict (validation-first, change_id-fresh) |

Quota diagnostics additionally ship as `runtime/quota/report.py` (text and `--json` output), surfaced by `/glm-conductor:quota`.

## Documentation

- [Core concepts](./docs/core-concepts.md) — conceptual overview of routing, workflow execution, assurance, and quota resume
- [Architecture](./docs/architecture.md) — authoritative technical reference for the current runtime design
- [Troubleshooting](./docs/troubleshooting.md) — quota diagnostics, legacy v2.3 task detection, writer-guard stale release, and recovery
- [Global Quota Clock extraction inventory](./docs/roadmap/GLOBAL_QUOTA_CLOCK_EXTRACTION_INVENTORY.md) — boundary of the future companion project
- [Changelog](./CHANGELOG.md) — release-level changes

## Project status and migration

**v2.4.0 is the current line** — a complexity re-baseline on ZCode Native Workflow, not an incremental release over the v2.3 runtime. The v2.3 execution runtime (dispatcher, dispatch permits, per-unit leases, receipts, quota control plane, Global Quota Clock) has been removed. **v2.3 task states are not migrated**: v2.4 detects legacy task directories and refuses them with recovery guidance instead of converting them. Finish or explicitly retire active v2.3 tasks before upgrading; released v2.3.x remains recoverable through Git history and tags. See [Troubleshooting](./docs/troubleshooting.md) for legacy detection guidance.

## License

[MIT](./LICENSE) © 2026 glm-conductor contributors
