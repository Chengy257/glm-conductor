# GLM Conductor

**English** | [简体中文](./README.zh-CN.md)

![Version](https://img.shields.io/badge/version-2.5.2-blue.svg)
![License](https://img.shields.io/badge/license-MIT-blue.svg)
![ZCode Plugin](https://img.shields.io/badge/ZCode-plugin-green.svg)
![Python](https://img.shields.io/badge/python-3.8%2B-blue.svg)
![Models](https://img.shields.io/badge/models-GLM--5.3%20%2F%20GLM--5.3--Flash-orange.svg)

**Selective routing, Native Workflow execution, minimal deterministic assurance, and optional bounded quota resume for GLM coding agents in ZCode.**

GLM Conductor keeps the strong model focused on planning, architecture, and final acceptance; delegates bounded implementation to ZCode Native Workflows; and introduces independent fresh-context review when code changes warrant it. Conductor owns semantic orchestration and acceptance; ZCode owns execution orchestration.

> **Design Goal:** Lightweight semantic orchestration for coding workflows — decide where work should run, compile bounded work into a canonical DAG, and enforce only the deterministic checks that prompts alone cannot guarantee. Conductor avoids duplicating host responsibilities: it is not a second workflow engine, task scheduler, or quota control plane.

---

## Key Highlights

- **Maximize Strong-Model Leverage:** Spend GLM-5.3 reasoning on ambiguity resolution, architecture, routing, validation, and final acceptance; offload bounded implementation to cost-effective workflow workers.
- **Explicit Delegation Contracts:** Delegated work is compiled from a canonical DAG defining node objectives, dependencies, owned files, interfaces, and constraints before implementation begins.
- **Deterministic Native Workflow Execution:** Compiles static DAGs directly into deterministic TypeScript workflows executed natively by ZCode with zero runtime state duplication.
- **Model Selection Hard Gate:** Guarantees worker models use provider-qualified Flash-tier bindings (version-agnostic `-Flash` tier marker, `workflow-model-select`); prevents silent inheritance of the main-session model.
- **Minimal Deterministic Assurance:**
  - **`change_id` Freshness:** Cryptographically ties validation and reviews to exact file modifications (`git rev-parse HEAD` + normalized file content hash); any subsequent edit invalidates previous approvals.
  - **Coarse Repository Writer Guard:** Guarantees at most one active write workflow per repository without arbitrary TTL expirations.
  - **Four-Check Completion Guard:** A Stop-hook verifies repository writer reservations, strict ownership boundaries, fresh validation, and required high-assurance reviews.
- **Bounded Quota Continuity:** Tasks awaiting provider quota enter `waiting_quota` and recover via native ZCode Scheduled Tasks under an explicit, bounded user-authorized budget.
- **Zero Third-Party Dependencies:** 100% standard library Python 3.8+ runtime, thoroughly hardened for Windows and POSIX environments.

---

## Architecture & How It Works

```text
                     Main Session — GLM-5.3
                (Plan · Route · Validate · Accept)
                         /              \
                        /                \
          solo / audit (Main Session)  delegate / full (Native Workflow)
                    ↓                            ↓
            Main-Session Code            v24-compile (Canonical DAG)
                                                 ↓
                                         workflow-model-select (Flash Gate)
                                                 ↓
                                         writer-acquire (Repo Lock)
                                                 ↓
                                         ZCode Native Workflow Run
                                                 ↓
                 Minimal Deterministic Assurance
           change_id · Ownership · Writer Guard · Completion Guard
                                                 ↓
                 Main-Session Validation & Independent Review
                                                 ↓
                 Optional Bounded Quota Resume (Scheduled Task)
```

### Responsibility Split

| Domain | GLM Conductor | ZCode Host |
| --- | --- | --- |
| **Orchestration** | Routing decisions, DAG compilation, review policies | Workflow run lifecycle, child actors, parallel execution |
| **Execution** | Pure TS code generation from static DAG | Execution scheduling, background workers, stop/resume |
| **State** | Task state (`state.json`), journal (`events.jsonl`) | In-flight execution state, token accounting, actor logs |
| **Assurance** | Task change identity (`change_id`), writer guard, stop gate | Process sandboxing, permission policies |
| **Continuity** | Recovery authorization, bounded budget tracking | Scheduled task triggers, automation engine |

---

## Selective Routing Matrix

Before delegating any work, the main session assesses two orthogonal dimensions:

- **Delegability:** Is the implementation bounded, specified, and verifiable enough to hand off safely?
- **Assurance:** Would an independent, fresh-context, read-only code review materially reduce risk after main validation?

| Delegability | Assurance | Route | Implementation Substrate | Independent Review |
|:---:|:---:|:---:|:---:|:---:|
| **Low** | Standard | `solo` | GLM-5.3 Main Session | None |
| **High** | Standard | `delegate` | ZCode Native Workflow | None |
| **Low** | High | `audit` | GLM-5.3 Main Session | Read-Only Reviewer (`glm-reviewer` / `visual-reviewer`) |
| **High** | High | `full` | ZCode Native Workflow | Read-Only Reviewer (`glm-reviewer` / `visual-reviewer`) |

Routes are declared upfront via a standardized `SELECTIVE ROUTE` block. When new evidence emerges during implementation, routes can be reassessed in either direction via `ROUTE REASSESSMENT`.

---

## Model Roles & Selection Governance

| Role | Default Model Binding | Primary Responsibility |
| --- | --- | --- |
| **Main Session** | **GLM-5.3** | Architecture, routing, DAG authoring, validation, final acceptance |
| **Workflow Workers** | **GLM-5.3-Flash (explicit)** | Bounded text implementation inside compiled Native Workflows |
| **Visual Implementer** | **GLM-5.3-Flash (multimodal)** | Bounded visual implementation (Custom Subagent capability exception) |
| **Text Reviewer** | **Host / Session Model** | Fresh-context, read-only final review (`glm-reviewer`) |
| **Visual Reviewer** | **GLM-5.3-Flash (multimodal)** | Fresh-context visual review (`visual-reviewer`; fails closed if unavailable) |

### Worker Model Pinning & Hard Gate
Workflow workers never inherit the session model. Submission requires explicit resolution via `workflow-model-select`:
1. Exactly one configured provider-qualified Flash model (`account:.../<model>-Flash`) is auto-selected.
2. If ambiguous, the user chooses during the task launch interview, recorded once as the task's `worker_model` pin (`worker-model-pin`).
3. Later submissions consume the pin via `--task-ref`. If the pinned model is no longer configured on the host, submission is rejected outright (exit code 2) rather than silently falling back to the main model.

---

## Minimal Deterministic Assurance

1. **`change_id` Freshness Primitive:**
   A deterministic SHA-256 hash calculated from `git rev-parse HEAD` combined with normalized content of files in the intersection of declared DAG ownership and actual workspace modifications.
   - Normalizes line endings (`CRLF` -> `LF`), paths, and sorting.
   - Guarantees that any subsequent code modification automatically invalidates previous validation or review records.
2. **Coarse Repository Writer Guard:**
   Ensures at most one active Conductor write workflow runs per Git repository.
   - Stored in `.glm-conductor/writer_guard.json` using atomic I/O.
   - **Never auto-expires by time (No TTL):** Avoids lock theft during long tasks. Releases occur only on task completion/failure/cancellation, or via explicit operator `--force` release.
3. **Four-Check Completion Guard:**
   Enforced via ZCode's `Stop` hook (`hooks/stop_gate.py`), checking in strict order of severity:
   - **Check 1 (Writer Guard):** No foreign task holds an active write reservation; delegated runs must still hold their reservation.
   - **Check 2 (Ownership):** All actual Git modifications (`git status`) lie strictly within the union of DAG node ownership scopes.
   - **Check 3 (Validation):** Main session has recorded fresh `validation.status == "passed"` with matching `change_id`.
   - **Check 4 (Review):** High-assurance routes (`audit` / `full`) require a fresh `review.verdict == "ship"` with matching `change_id`.

---

## Optional Bounded Quota Resume

Tasks encountering provider quota exhaustion can safely pause in `waiting_quota` and recover via native ZCode Scheduled Tasks:
- **Arm-Before-Work Discipline:** When auto-resume is enabled, the future Scheduled Task must be created and bound (`quota-automation-bind`), and `quota-continuity-preflight` must pass (exit code 0) before any quota-consuming workflow begins.
- **Bounded Budget:** Every automatic resume consumes one unit of the user-authorized `max_resumes` budget. When exhausted, the task transitions to `waiting_user`.
- **Dual-Channel Quota Wake (v2.5.2):** A launch-time recurring Scheduled Task is the lifetime floor carrier (survives restarts; never mutated after creation), while a background sleep to the blocking-window `reset_at` + grace provides second-precise wakeup — both trigger sources share one unified wake protocol.

---

## Prerequisites & Installation

### Requirements

- **[ZCode](https://zcode.z.ai) 3.14+** (Native Workflow & Scheduled Task capabilities verified)
- A GLM Coding Plan or Z.ai account with GLM-5.3 and GLM-5.3-Flash models enabled
- **Python 3.8+** for runtime hooks and CLI utilities
  - *Windows Notice:* Ensure `python3.exe` is available in PATH. If only `python.exe` is present, see the [Windows Setup Guide](./docs/troubleshooting.md#8-windows-环境下-python3-钩子命令排查).

### Installation via ZCode Plugin Marketplace

1. Open your workspace in ZCode.
2. Navigate to **Settings → Plugins → Create → Add plugin marketplace**.
3. Enter the GitHub repository URL: `https://github.com/Chengy257/glm-conductor`.
4. Install **glm-conductor** from the **Personal** section.
5. Create a **new session** so skills, agents, and hooks take effect immediately.

### Local Installation for Developers

Clone the repository and add the repository root folder (containing `marketplace.json`) as a local marketplace:

```bash
git clone https://github.com/Chengy257/glm-conductor.git
```

> **Note:** Select the repository root manifest (`marketplace.json`), not `plugins/glm-conductor/.zcode-plugin/plugin.json`.

---

## Quick Start

### 1. Initiate Orchestration

In a new session, request GLM Conductor to orchestrate your goal:

```text
Use glm-conductor:orchestration to plan and implement this feature. Declare the route first, then validate the result.
```

### 2. Route Declaration & Launch Interview

The architect outputs a route declaration:

```text
SELECTIVE ROUTE
mode: delegate
delegability: high
assurance: standard
reason: Implementation is bounded by explicit interface contracts and covered by unit tests.
```

For tasks creating state, the main session performs a single-round launch interview to confirm:
- Auto-wake continuity enablement & bounded budget (`max_resumes`).
- Worker model selection if multiple candidates exist.

### 3. Compile & Execute

```bash
# Compile DAG into TypeScript Native Workflow
python plugins/glm-conductor/runtime/cli.py v24-compile dag.json --out .glm-conductor/workflow.ts

# Acquire repository write reservation
python plugins/glm-conductor/runtime/cli.py writer-acquire . <task_id>

# Submit run via ZCode CreateWorkflow with explicit subagent_model
```

### 4. Validate & Complete

After the workflow completes:
1. The main session inspects git diffs and runs integration test commands.
2. Records validation:
   ```python
   # Evaluates change_id and records passed status
   runtime.task.record_validation(repo_root, task_id, status="passed", summary="All tests pass")
   ```
3. Stop hook triggers the four-check completion guard; upon passing, the task completes and releases the writer guard automatically.

---

## Reference & Tooling

### Specialized Skills

- `glm-conductor:orchestration` — Selective routing, static DAG authoring, launch interview, workflow lifecycle, and review flow.
- `glm-conductor:continuity` — Checkpoints, `waiting_quota` handling, arm-before-work scheduling, and bounded resumes.
- `glm-conductor:enforcement` — Completion guard four-check semantics, diagnostics, and recovery from blocks.
- `/glm-conductor:quota` — Read-only quota usage and window reset diagnostics (`--json` supported).

### Runtime CLI Commands

All runtime operations execute through `python <plugin-root>/runtime/cli.py <subcommand>` (single-line JSON output):

| Command | Description |
| --- | --- |
| `v24-compile <dag-json> [--task-ref <id>] [--out <path>]` | Compiles a canonical DAG into deterministic TypeScript Native Workflow source. |
| `workflow-model-select <models-json> [--model <id>] [--task-ref <id>] [--repo-root <path>]` | Model selection preflight gate; resolves Flash worker model or consumes task pin. |
| `worker-model-pin <repo> <task_id> <exact-id>` | Persists user-authorized task-level worker model pin. |
| `writer-acquire <repo> <task_id> [--run-id <id>]` | Acquires coarse repository-level write reservation. |
| `writer-release <repo> <task_id> [--force]` | Releases write reservation (`--force` for operator cleanup). |
| `writer-show <repo>` | Inspects current repository write reservation holder. |
| `v24-record-run <task_ref> <run-id>` | Records task ↔ Native Workflow run-id association. |
| `review-record <repo> <task_id> <reviewer> <verdict>` | Records fresh task review verdict (`ship` / `fix-first` / `rethink`). |
| `quota-resolve <repo> [--force-refresh]` | Four-state provider quota resolution (read-only). |
| `quota-wait <repo> <task_id>` | Enters `waiting_quota` state with normalized observation snapshot. |
| `quota-resume-authorize <repo> <task_id> <max_resumes>` | Authorizes bounded automatic quota resume. |
| `quota-resume-decision <repo> <task_id>` | Evaluates idempotent activation decision on scheduled wake. |
| `quota-resume-confirm <repo> <task_id>` | Commits resume count increment upon accepted host resume. |
| `quota-automation-bind / clear / preflight` | Manages Scheduled Task continuity arming lifecycle. |
| `task-create <repo> <task_id> <goal> <mode> <assurance> [--dag <path>]` | Creates a task via the lifecycle CLI (thin shell over `runtime.task`). |
| `record-validation <repo> <task_id> <passed\|failed> [--summary <s>] [--commands <s>]` | Records fresh validation outcome on the task ledger. |
| `task-complete / task-fail / task-cancel <repo> <task_id>` | Terminal lifecycle transitions (success / failure / user-cancelled). |
| `exit-waiting <repo> <task_id>` | Manual exit from `waiting_quota` (the automatic path uses `quota-resume-confirm`). |

### Repository Utilities

- `scripts/archive_legacy_tasks.py` — Safely detects and archives legacy v2.3 tasks into `.glm-conductor/archived-tasks/` (default dry-run, `--confirm` to execute).
- `scripts/validate_plugin.py` — Static contract validation tool ensuring 16/16 contract anchors are strictly satisfied.
- `scripts/smoke_plugin_load.py` — Manifest, agent, skill, and hook smoke testing.

---

## Repository Structure

```text
glm-conductor/
├── marketplace.json                # Marketplace catalog manifest
├── .zcode-plugin/                  # Plugin manifest and metadata
├── plugins/glm-conductor/
│   ├── agents/                     # Specialized subagents (glm-reviewer, visual-*)
│   ├── commands/                   # Slash command definitions (/glm-conductor:quota)
│   ├── hooks/                      # ZCode hooks (session_start.py, stop_gate.py)
│   ├── skills/                     # Domain skills (orchestration, continuity, enforcement)
│   └── runtime/                    # Standard library Python runtime
│       ├── workflow/               # DAG compiler, worker persona, submission gates
│       ├── quota/                  # Read-only quota providers, parsers, resolvers
│       ├── change_id.py            # Deterministic change identity hashing
│       ├── writer_guard.py         # Coarse repository writer guard
│       ├── ownership.py            # Path glob matching and stage planning
│       ├── state.py                # 7-state task machine and schema validation
│       └── cli.py                  # Public runtime CLI interface
├── scripts/                        # Validation, smoke, and task archiving utilities
└── docs/                           # Architecture specs, core concepts, troubleshooting
```

---

## Documentation Hub

- [Core Concepts](./docs/core-concepts.md) — Comprehensive overview of routing, execution, assurance, and recovery.
- [Architecture Specification](./docs/architecture.md) — Authoritative technical reference for the v2.5 runtime design.
- [Troubleshooting & Diagnostics](./docs/troubleshooting.md) — Quota diagnostics, Windows setup, Monorepo Git tuning, and writer-guard recovery.
- [Global Quota Clock Extraction Inventory](./docs/roadmap/GLOBAL_QUOTA_CLOCK_EXTRACTION_INVENTORY.md) — Boundary definition for the companion project.
- [Changelog](./CHANGELOG.md) — Detailed version history and migration guides.

---

## Contributing & Quality Gates

Contributions are welcome! Before opening a pull request, verify all automated quality gates:

```bash
# 1. Run full unit test suite (790+ tests)
python -m unittest discover -s tests -p "test_*.py"

# 2. Run static contract validator (16/16 checks)
python scripts/validate_plugin.py

# 3. Run plugin smoke loader
python scripts/smoke_plugin_load.py

# 4. Run lint check
ruff check .
```

---

## License

[MIT](./LICENSE) © 2026 GLM Conductor Contributors
