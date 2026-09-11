# GLM Conductor

**English** | [简体中文](./README.zh-CN.md)

![Version](https://img.shields.io/badge/version-2.3.1-blue.svg)
![License](https://img.shields.io/badge/license-MIT-blue.svg)
![ZCode Plugin](https://img.shields.io/badge/ZCode-plugin-green.svg)
![Models](https://img.shields.io/badge/models-GLM--5.3%20%2F%20GLM--5.3--Flash-orange.svg)
![CI](https://github.com/Chengy257/glm-conductor/actions/workflows/validate.yml/badge.svg)

**Selective orchestration, execution assurance, and quota-aware continuity for GLM coding agents in ZCode.**

GLM Conductor keeps the strong model focused on planning, judgment, and acceptance; delegates bounded implementation to lower-cost workers; and adds independent review when the change warrants it. For long-running work, it can preserve progress across quota windows without turning quota handling into part of the routing policy.

> **Design goal:** lightweight orchestration for personal coding workflows — use stronger reasoning where it matters, cheaper execution where the work is well specified, and deterministic runtime checks where prompts alone are not enough.

## Why GLM Conductor

- **Spend strong-model reasoning where it has the highest value.** GLM-5.3 handles ambiguity, architecture, routing, verification, and final acceptance; bounded implementation can move to GLM-5.3-Flash.
- **Delegate with explicit contracts.** Each delegated unit defines its objective, owned files, interfaces, constraints, and verification. Runtime hooks enforce key boundaries and require evidence before completion can pass.
- **Separate implementation from independent review.** High-assurance changes can receive a fresh-context, read-only final audit after the main session has verified the implementation.
- **Keep long tasks recoverable.** Continuity is independent of routing: work can checkpoint, wait for quota, and resume without changing who should do the work.

## How it works

```text
                         Main session — GLM-5.3
                  plan · route · verify · accept
                         /                 \
                        /                   \
          bounded implementation       high assurance
                    ↓                       ↓
        GLM-5.3-Flash worker      fresh-context reviewer

                         Runtime assurance
             scope · evidence · completion · recovery

                         Long-task continuity
             Global Quota Clock + Task Wake Bridge
```

ZCode remains the underlying coding harness. GLM Conductor adds orchestration contracts, deterministic enforcement, and an optional continuity layer on top.

## Selective routing

Before delegation, the main session evaluates two independent questions:

- **Delegability** — Is the remaining implementation sufficiently bounded and specified to hand off?
- **Assurance** — After main-session verification, would an independent fresh-context review materially reduce risk?

| Delegability | Assurance | Route | Implementation | Independent review |
| --- | --- | --- | --- | --- |
| low | standard | `solo` | main session | no |
| high | standard | `delegate` | worker | no |
| low | high | `audit` | main session | yes |
| high | high | `full` | worker | yes |

Routing is evidence-driven rather than a fixed escalation ladder. It may be reassessed in either direction when new evidence changes the task boundary or risk.

## Model roles

The current recommended assignment is intentionally simple:

| Role | Default model | Responsibility |
| --- | --- | --- |
| Main session | **GLM-5.3** | planning, architecture, routing, verification, acceptance |
| Implementation worker | **GLM-5.3-Flash** | bounded implementation |
| Visual worker | **GLM-5.3-Flash** | bounded multimodal implementation |
| Text reviewer | **GLM-5.3** | fresh-context, read-only final audit |
| Visual reviewer | **GLM-5.3-Flash** | fresh-context visual audit |

These are current assignments, not permanent architectural identities. The contracts are designed around roles, leaving room for future `planner_model` / `executor_model` / `reviewer_model` configuration.

## Deterministic execution assurance

Delegation is not accepted on model claims alone. GLM Conductor combines prompt-level contracts with runtime checks for important invariants such as task ownership, completion evidence, and stale verification state.

The main session still owns final acceptance: worker reports are claims, while repository state, diffs, and reproduced verification are evidence. If the enforcement layer itself cannot operate, it degrades visibly rather than silently blocking the session.

## Continuity across quota windows

Continuity is a lifecycle layer, not a routing axis. Basic orchestration works without it.

### Global Quota Clock

A persistent clock is associated with a provider identity rather than an individual task. It observes the provider's real quota-window state and retimes its next wake toward the observed reset instead of assuming a fixed five-hour schedule. Its regular recurring schedule is only a watchdog fallback.

### Task Wake Bridge

A wake bridge is temporary and task-specific. It exists only while a task is waiting for quota and targets the point at which that task can continue. The clock serves the provider identity; the bridge serves one waiting task.

### Recommended placement

Run the Global Quota Clock in a small dedicated ZCode session using a low-cost Flash model so periodic clock ticks do not interrupt the main coding session. ZCode currently does not expose session creation to plugins, so GLM Conductor can guide and diagnose this placement but cannot create or mechanically guarantee the dedicated session.

## Host compatibility and safety

Quota scheduling adapts to **observed ZCode host behavior, not a contractual scheduling API**. The host-specific implementation is isolated behind a replaceable adapter. Its only production write to the ZCode task store is re-timing the `next_run_at` field of an existing automation; other host-store mutations are outside the plugin boundary.

After a ZCode upgrade, run the read-only compatibility probe:

```bash
python <plugin-root>/runtime/cli.py host-check
```

Use whichever Python 3 launcher is available on your system (`python` or `python3`). If host compatibility breaks, scheduling operations fail safely and durable recovery still falls back to session-start recovery. See [Troubleshooting](./docs/troubleshooting.md) for diagnostics and recovery.

## Installation

### Requirements

- [ZCode](https://zcode.z.ai) — currently tested with 3.9.2
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
Use glm-conductor:orchestration to plan and implement this feature. Declare the route first, then verify the result.
```

A route declaration looks like:

```text
SELECTIVE ROUTE
mode: delegate
delegability: high
assurance: standard
executor: flash-implementer
continuity: foreground
reason: implementation is bounded by explicit interfaces, owned files, and deterministic verification
```

Useful entry points:

- `glm-conductor:orchestration` — routing, delegation contracts, and review flow
- `glm-conductor:continuity` — checkpointing and recovery for long tasks
- `glm-conductor:enforcement` — runtime enforcement, diagnostics, and blocked-task recovery
- `/glm-conductor:quota` — on-demand quota diagnostics (`--json` for machine-readable output)

### Optional quota continuity

You only need this setup when you want work to continue across quota windows. Plan and bind a Global Quota Clock in the recommended dedicated session, then use the runtime CLI for diagnostics and recovery:

```bash
python <plugin-root>/runtime/cli.py <subcommand>
```

Key commands include `quota-clock-plan`, `quota-clock-bind`, `quota-clock-status`, `quota-resume`, `quota-phase`, and `host-check`.

## Documentation

- [Core concepts](./docs/core-concepts.md) — conceptual overview of orchestration, assurance, and continuity
- [Architecture](./docs/architecture.md) — authoritative technical reference for the current runtime design
- [Troubleshooting](./docs/troubleshooting.md) — host compatibility, clock health, state locations, and recovery
- [Changelog](./CHANGELOG.md) — release-level changes

## Project status

**v2.3.1 is the current stable line.** The quota-continuity subsystem is in maintenance mode: future changes should favor bug fixes, host-compatibility updates, and clear user-facing improvements over additional scheduler architecture.

## License

[MIT](./LICENSE) © 2026 glm-conductor contributors
