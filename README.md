# GLM Conductor

**English** | [简体中文](./README.zh-CN.md)

![Version](https://img.shields.io/badge/version-2.2.0-blue.svg)
![License](https://img.shields.io/badge/license-MIT-blue.svg)
![ZCode Plugin](https://img.shields.io/badge/ZCode-plugin-green.svg)
![Models](https://img.shields.io/badge/models-GLM--5.3%20%2F%20GLM--5.3--Flash-orange.svg)
![CI](https://github.com/Chengy257/glm-conductor/actions/workflows/validate.yml/badge.svg)

> GLM Conductor is a deterministic orchestration, execution-assurance, and quota-aware continuity runtime for GLM coding agents in ZCode. The contracts that make delegation safe are enforced by runtime hooks — not left to model self-discipline.

## What is GLM Conductor

GLM Conductor is a ZCode orchestration plugin for coding agents on the GLM Coding Plan. A GLM-5.3 main session acts as the single architect — planning, routing, verification, and acceptance — while cost-efficient GLM-5.3-Flash subagents execute bounded implementation specs and fresh-context read-only reviewers deliver independent final audits.

Its defining property is that the key contracts (ownership scopes, verification evidence, review verdicts, quota-aware resume) are enforced **mechanically** by plugin-distributed hooks and a stdlib-only Python runtime: out-of-scope changes cannot silently pass the completion gate, completion claims without evidence are blocked, stale evidence expires automatically, and quota-exhausted tasks re-activate in the same session with bounded latency.

## Why GLM Conductor

**Selective routing orchestration.** Before the first delegation, the main session declares a machine-auditable `SELECTIVE ROUTE` (five fields: mode / delegability / assurance / executor / continuity) along two independent axes — **Delegability** (is the remaining implementation bounded enough to delegate?) and **Assurance** (does completion need an independent audit?) — mapping to one of four routes: `solo` / `delegate` / `audit` / `full`. Every delegation uses a five-part implementation spec (objective / files and ownership / interfaces / constraints / verification) and returns an IMPLEMENTATION REPORT; routes are reassessed in either direction on new evidence.

**Mechanical execution assurance.** The Stop-gate completion check is four-fold: ownership (actually touched files ⊆ declared scope), verification (required commands really run, recorded as evidence), review (a fresh independent ship verdict for high-assurance work), and evidence freshness (verification/review receipts are fingerprint-bound to the repository state they were recorded against). Dispatch permits gate implementation subagents before they act, and a table-driven Bash policy always denies destructive commands. Enforcement fails open and visibly (`ENFORCEMENT DEGRADED` on stderr) — a broken enforcement layer never blocks the session.

**Quota-aware continuity.** Long tasks survive quota windows safely: the execution phase (NORMAL / PRESSURE / DRAINING / BLOCKED) mechanically decides what may run now; quota-exhausted tasks follow an authorized resume chain and are re-activated in the same session through the persistent wake bridge, with durable SessionStart recovery as the fallback when the host is unavailable. Tasks subscribe to quota epochs instead of owning quota clocks, and on resume the repository's real state always wins over recorded state.

## Install

Prerequisites:

- The [ZCode](https://zcode.z.ai) client (tested with 3.9.2)
- A GLM Coding Plan (or Z.ai account) with GLM-5.3 and GLM-5.3-Flash connected — **the orchestrating main session must be GLM-5.3**; Flash powers the implementation and reviewer roles
- `python3` (Python 3.8+) on PATH — the runtime for the enforcement hooks

Install from the ZCode plugin marketplace:

1. Open a workspace in ZCode (the plugin management page requires one).
2. **Settings → Plugins → Create → Add plugin marketplace**, choose the GitHub repository source, enter `https://github.com/Chengy257/glm-conductor`, then install glm-conductor from the **Personal** section. (For local installs, clone the repository and add the repository root — the directory containing `marketplace.json` — as a local marketplace.)
3. After installing or updating, **create a new session** so subagents, skills, and hooks load.

> Select the marketplace manifest (the repository root), not `plugins/glm-conductor/.zcode-plugin/plugin.json` — that is the plugin manifest, not the marketplace manifest.

## Quick start

In a new session, ask for orchestration before the first delegation:

```
Use glm-conductor:orchestration to plan and implement this feature: declare the route, then verify.
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

| Delegability | Assurance | Route | Implementation | Independent review |
| --- | --- | --- | --- | --- |
| low | standard | `solo` | GLM-5.3 main session | No |
| high | standard | `delegate` | executor subagent | No |
| low | high | `audit` | GLM-5.3 main session | Yes |
| high | high | `full` | executor subagent | Yes |

`executor` (standard / visual) and `continuity` (foreground / resumable / idle) are declared as independent dimensions alongside the route.

Three skills carry the workflow:

- `/orchestration` — route declaration, delegation contracts, work-unit management, review flow
- `/continuity` — long-horizon continuity: per-task checkpoints, resume, quota-aware scheduling
- `enforcement` — the user-side enforcement reference: environment self-check, hook message meanings, and how to recover when blocked

For quota diagnostics at any time: `/glm-conductor:quota` (text report, or `--json` for machine-readable output).

The runtime CLI is the single entry point for quota/continuity subcommands:

```
python3 plugins/glm-conductor/runtime/cli.py <subcommand>
```

for example `quota-resolve`, `quota-phase`, or `quota-resume`.

## Key runtime guarantees

Honest, runtime-supported claims — each enforced by hooks and the stdlib-only runtime:

- **Ownership-scoped enforcement.** The four-fold Stop-gate completion check verifies, per task and against the task's bound Git repository root, that actually touched files ⊆ the declared file scope, that required verification commands were really run, and — for high-assurance work — that a fresh independent ship review exists. Out-of-scope changes and unevidenced completion claims cannot silently pass.
- **Durable verification / review receipts.** `verify-unit` / `verify-task` have the runtime itself execute the declared commands under whitelist and policy restrictions and persist durable receipts; review verdicts are recorded as fingerprint-bound receipts. The completion gate accepts only receipts — hand-written state fields are not trusted — and `task_fingerprint` binds every receipt to the exact repository state at recording time, so any later edit makes the evidence stale and blocks completion.
- **DRAINING is finish-only.** Quota awareness is dual-layer: the provider reports four states (AVAILABLE / PRESSURE / EXHAUSTED / UNKNOWN) while the execution phase (NORMAL / PRESSURE / DRAINING / BLOCKED) is derived separately — when the smallest remaining window drops to 20% or less, the phase becomes DRAINING even if the provider still reports AVAILABLE. DRAINING forbids new implementation waves and allows only finish-up actions; BLOCKED freezes both.
- **Epoch-idempotent consumption.** Tasks subscribe to quota epochs (epoch identity is a deterministic fingerprint of the current window set) instead of owning quota clocks; activation is booked at most once per epoch, and the window budget is consumed exactly once at the resume commit point — idempotent per epoch, so crash-window reruns never double-consume.
- **The recurring bridge is the only stable transport.** Same-session re-activation of a dormant task rides the persistent wake bridge: `recurring_bridge` is the only activation transport with stable status, and reserved alternatives are refused (`TransportReservedError`) rather than half-implemented. The opt-in resident quota watcher observes only — it never injects turns into a dormant session; when the host is unavailable, durable SessionStart recovery injects the resume context into the next session automatically.
- **The primer is off by default.** Materializing a fresh quota window requires one minimal model call; the window primer is the runtime's only control-plane model-call surface and is structurally off by default (`primer_enabled=false` behind an authorization gate — manual/notify never prime).

Quota facts come from verified provider monitoring endpoints (credentials are resolved automatically and never persisted) — never from fabricated native quota interfaces or hardcoded reset schedules; when an endpoint is unavailable, quota awareness falls back to periodic probing.

## Documentation

- [docs/core-concepts.md](./docs/core-concepts.md) — the three pillars explained conceptually
- [docs/architecture.md](./docs/architecture.md) — the authoritative technical reference: state model, routing, dispatch transactions, enforcement, quota continuity, recovery
- [docs/README.md](./docs/README.md) — documentation index
- [CHANGELOG.md](./CHANGELOG.md) — release-level changes

History archives: development-process records (implementation plans, experiments, release evidence) live under [docs/history/](./docs/history/) — non-authoritative archives; current truth is the code plus the architecture doc.

## Compatibility and stable boundary

The stable boundary is frozen: the routing axes and four routes, the Stop-gate check order and its gate vocabulary, the TASK_ID format and the `.glm-conductor/tasks/<task-id>/` state layout, the transport vocabulary (`recurring_bridge` the only stable transport), and the runtime CLI subcommand surface — changes require an explicit design ruling (see [architecture §14](./docs/architecture.md)). The execution model is single-controller: one orchestrating main session, with implementation workers capped at 4 (bounded parallelism, experimental).

## License

[MIT](./LICENSE) © 2026 glm-conductor contributors
