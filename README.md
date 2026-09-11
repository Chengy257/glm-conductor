# GLM Conductor

**English** | [简体中文](./README.zh-CN.md)

![Version](https://img.shields.io/badge/version-2.3.0-blue.svg)
![License](https://img.shields.io/badge/license-MIT-blue.svg)
![ZCode Plugin](https://img.shields.io/badge/ZCode-plugin-green.svg)
![Models](https://img.shields.io/badge/models-GLM--5.3%20%2F%20GLM--5.3--Flash-orange.svg)
![CI](https://github.com/Chengy257/glm-conductor/actions/workflows/validate.yml/badge.svg)

GLM Conductor is a lightweight orchestration extension for GLM coding agents in ZCode. A strong model keeps planning and acceptance in its own hands, hands bounded implementation to low-cost workers, and brings in an independent reviewer when a change deserves one. For long tasks, it keeps work moving safely across quota windows instead of stalling when a plan runs out.

## Why GLM Conductor

- **Expensive reasoning goes where it matters.** The main model (GLM-5.3) spends its context on what deserves it — resolving ambiguity, designing, routing, checking diffs, accepting results — while well-specified implementation goes to cheaper workers.
- **Mechanical work gets delegated, with contracts enforced by the runtime.** Every delegation carries a five-part spec (objective, files and ownership, interfaces, constraints, verification), and plugin hooks check it mechanically: out-of-scope changes and completion claims without evidence cannot silently pass. When the enforcement layer itself breaks, it degrades visibly — it never silently blocks your session.
- **Long tasks survive quota windows.** When quota runs out mid-task, the task parks at a safe milestone and picks up again when quota returns — see [Continuity](#continuity-long-tasks-across-quota-windows) below.

## How it works, in one picture

```
                    Main model — GLM-5.3
          planning · routing · verification · acceptance
                  /                              \
    bounded task ──▶ implementation worker        high assurance ──▶ independent reviewer
                   (GLM-5.3-Flash,                                (fresh context,
                    fresh context)                                 read-only)

    Continuity for long tasks
        ├─ Global Quota Clock    persistent · one per provider identity
        └─ Task Wake Bridge      temporary · one per waiting task
```

ZCode remains the underlying harness — the plugin orchestrates within it and adds runtime enforcement on top.

## Routing: who implements, who reviews

Before the first delegation, the main model answers two independent questions:

- **Delegability — is the remaining implementation bounded enough to hand off?** Goal, file scope, interfaces, constraints, and verification all pinned down means high; an open architecture or judgment-dense work means low.
- **Assurance — does this change deserve an independent review from a fresh context after the main model verifies it?** Contained impact means standard; wide impact or user-visible risk means high.

The two answers pick one of four routes:

| Delegability | Assurance | Route | Implemented by | Independent review |
| --- | --- | --- | --- | --- |
| low | standard | `solo` | main model | no |
| high | standard | `delegate` | implementation worker | no |
| low | high | `audit` | main model | yes |
| high | high | `full` | implementation worker | yes |

The route is declared once, before the first delegation, and can be re-assessed in either direction on new evidence. Visual tasks use a visual implementation worker and a visual reviewer.

## Model roles

Today's recommended defaults: **GLM-5.3** as the main session (planning, routing, acceptance), **GLM-5.3-Flash** for implementation workers (standard and visual), and a **fresh-context reviewer** for independent audits — text reviews run on GLM-5.3, visual reviews on multimodal Flash. These are the currently recommended role assignments, not permanent architectural identities: the role contracts are expected to evolve toward semantic model roles (`planner_model` / `executor_model` / `reviewer_model`) so other combinations can be configured over time.

## Continuity: long tasks across quota windows

Continuity is independent of routing — it is a lifecycle layer any long task can use. Two mechanisms cooperate, and they are deliberately different species. Default behavior: a durable task is always recoverable — the next session start picks it up — while automatic cross-window wake-up only ever happens inside authorization you grant explicitly.

### Global Quota Clock — persistent, one per provider identity

- One persistent clock per provider identity (your account), owned by no single task.
- It periodically wakes and makes a small low-cost call to observe the provider's quota windows and materialize the next one, then re-times itself toward the provider's real reset — it never assumes a fixed reset schedule.
- The regular recurring schedule (hourly by default) is only a watchdog: the normal path re-times the next wake precisely, to just after the real reset.
- Quota facts always come from the provider's own monitoring endpoints — the plugin never invents quota interfaces and never hardcodes reset times.

### Task Wake Bridge — temporary, one per waiting task

- Created only when a specific task is actually waiting for quota; it wakes that task near the moment execution can resume.
- Cleaned up once the task no longer needs it. The clock serves the account; the bridge serves one task — the two are never confused.

### Dedicated clock session (recommended placement)

Host the Global Quota Clock in a small dedicated session running a low-cost Flash model, so its periodic wakes never interrupt your main coding session. ZCode does not currently open session creation to plugins: the plugin can recommend placement and help diagnose a misplaced clock, but it cannot create the session or mechanically guarantee the placement.

## Host compatibility and safety

Scheduling features adapt to your locally observed ZCode host behavior — not a contract API — so a ZCode upgrade can shift it. The adaptation is isolated behind a replaceable adapter, and its only production write is re-timing a single scheduled field (`next_run_at`) of an existing automation; everything else in the host's local store is off limits. After upgrading ZCode, run the read-only self-check:

```
python3 <plugin-root>/runtime/cli.py host-check
```

If compatibility breaks, scheduling features fail safely and recovery falls back to session-start injection. Diagnosis and recovery paths — host compatibility, clock health, where state lives — are in [docs/troubleshooting.md](./docs/troubleshooting.md).

## Install and first run

Prerequisites:

- The [ZCode](https://zcode.z.ai) client (tested with 3.9.2)
- A GLM Coding Plan (or Z.ai account) with GLM-5.3 and GLM-5.3-Flash connected — **the orchestrating main session must be GLM-5.3**; Flash powers the implementation and reviewer roles
- `python3` (Python 3.8+) on PATH — the runtime for the enforcement hooks

Install from the ZCode plugin marketplace:

1. Open a workspace in ZCode (the plugin management page requires one).
2. **Settings → Plugins → Create → Add plugin marketplace**, choose the GitHub repository source, enter `https://github.com/Chengy257/glm-conductor`, then install glm-conductor from the **Personal** section. (For local installs, clone the repository and add the repository root — the directory containing `marketplace.json` — as a local marketplace.)
3. After installing or updating, **create a new session** so subagents, skills, and hooks load.

> Select the marketplace manifest (the repository root), not `plugins/glm-conductor/.zcode-plugin/plugin.json` — that is the plugin manifest, not the marketplace manifest.

### First run: orchestration works out of the box (normal use)

In a new session, ask for orchestration before the first delegation:

```
Use glm-conductor:orchestration to plan and implement this feature: declare the route, then verify.
```

The main model declares the route first, then works accordingly:

```
SELECTIVE ROUTE
mode: delegate
delegability: high
assurance: standard
executor: flash-implementer
continuity: foreground
reason: implementation is bounded by explicit interfaces, owned files, and deterministic verification
```

That is the whole setup for everyday use — the Global Quota Clock is **not** required for basic orchestration. Useful entry points:

- `glm-conductor:orchestration` — route declaration, delegation contracts, review flow
- `glm-conductor:continuity` — checkpoints and resume for long tasks
- `glm-conductor:enforcement` — user-side enforcement reference: environment self-check, hook message meanings, recovery when blocked
- `/glm-conductor:quota` — on-demand quota diagnostics (text report; `--json` for machine-readable output)

### Optional: quota continuity setup

Only if you want tasks to continue across quota windows: plan and bind a Global Quota Clock (in the dedicated session recommended above), then let it run. The runtime CLI is the single entry point for quota and continuity subcommands — `<plugin-root>` is the installed plugin directory (`plugins/glm-conductor` in a repository checkout):

```
python3 <plugin-root>/runtime/cli.py <subcommand>
```

for example `quota-clock-plan` / `quota-clock-bind` / `quota-clock-status`, `quota-resume`, `quota-phase`, or `host-check`.

## Documentation

- [docs/core-concepts.md](./docs/core-concepts.md) — the three pillars explained conceptually
- [docs/architecture.md](./docs/architecture.md) — the authoritative technical reference: state model, routing, enforcement, quota continuity, recovery
- [docs/troubleshooting.md](./docs/troubleshooting.md) — symptom → diagnosis → fix: host compatibility, clock health, state locations
- [CHANGELOG.md](./CHANGELOG.md) — release-level changes

## License

[MIT](./LICENSE) © 2026 glm-conductor contributors
