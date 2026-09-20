# v2.4 Phase 1 Workflow Execution Plan — Session Discipline, Unit Specs, First Workflow

> **Status:** specified 2026-09-21, **awaiting user confirmation before implementation**.
> **Authority chain:** `docs/reviews/V2_4_NATIVE_WORKFLOW_REBASELINE_SPEC.md` →
> `docs/roadmap/V2_4_NATIVE_WORKFLOW_IMPLEMENTATION_PLAN.md` →
> `docs/roadmap/V2_4_PHASE_1_NATIVE_SEMANTIC_CORE_SPEC.md` → **this document**
> (unit-level decomposition, workflow topology, and gates for the first implementation workflow).
> **Executor model:** every unit implementer runs on GLM-5.3-Flash inside one native Workflow.
> **Main session (GLM-5.3):** task analysis, unit spec authoring, gate evaluation, final acceptance review, local commits.

## 1. Session discipline (user-mandated 2026-09-21, binding for all phases)

1. **Cross-quota-window continuation (until_done).** Native Scheduled Task
   `automation-68c12efd-cfdc-4b85-ab2b-efb4f5bfadac` (hourly at :07) wakes this session:
   resume stopped phase-workflow runs (`ResumeWorkflowRun`, same run id — never a duplicate
   workflow) or continue from the recorded breakpoint when the quota window is usable again.
   It self-deletes (single `CronDelete` attempt, no retry) once all four phases are complete.
   This activation is explicitly bound to this implementation session by user request.
2. **No glm-conductor accounting.** Phase implementation does NOT create conductor tasks,
   does not use the dispatch/permit/lease CLI, and is not registered in `.glm-conductor/tasks/`.
   Each phase's implementation is orchestrated as **one native Workflow per phase**
   (CreateWorkflow), which by W0 evidence bypasses the plugin hook surface entirely.
3. **Role split.** Main session performs analysis, unit implementation planning, and final
   acceptance review. Unit implementers only execute bounded five-part specs.
4. **Flash-only implementers.** Workflow `subagent_model =
   account:bigmodel-individual-coding-plan/GLM-5.3-Flash` (host-verified via ListModels).
5. **Local commits only.** No `git push` at any point during implementation. Push happens
   only when the user explicitly requests it after campaign completion.
6. **Targeted tests only.** Each unit gate runs its focused unittest targets plus the plugin
   validator/smoke-load. The full regression runs exactly once, at the Phase 4 final gate.
7. Additional standing rules: no OS power operations; no new automations/schedulers beyond the
   until_done wake; each phase reports at its exit gate and waits for user confirmation
   before the next phase's workflow is submitted.

## 2. Environment facts (verified 2026-09-21 on this host)

- Branch `review/zcode-3.14-native-workflow` (local = origin; local `main` synced to `2e8a593`).
- Working interpreter: `python3` on PATH (WindowsApps shim, 3.7.9) executes the suite:
  `PYTHONUTF8=1 python3 -m unittest tests.test_agent_model_binding` → 2 tests OK.
  New code must stay 3.7-compatible on the annotation level (the repo already quotes
  `list[str]`-style annotations); CI remains the authoritative gate for newer Pythons.
- Quota diagnostic (for the wake turn):
  `python3 "$(ls -d ~/.zcode/cli/plugins/cache/glm-conductor/glm-conductor/*/runtime/quota/report.py | sort -V | tail -1)" --json`
  → per-window `status: AVAILABLE | PRESSURE | EXHAUSTED | UNKNOWN`.
- Loaded plugin = cache 2.3.2; hook scripts execute from the **cache** copy, not the repo
  tree — editing repo runtime files mid-session cannot break the running session's hooks.
- Hooks import runtime modules **lazily inside functions** (`from runtime import state` …
  after `sys.path.insert`), so any import-time failure in `runtime/state.py`'s dependency
  chain would kill hook execution for every future event. Import safety is a hard gate.
- `scripts/validate_plugin.py` (15 checks) and `scripts/smoke_plugin_load.py` exist at repo root.
- Legacy consumers of `runtime/work_unit.py`: `state.py`, `task_manager.py`,
  `provenance.py`, `reconcile.py`, `recovery.py`, `resume_manifest.py`,
  `continuity/resume.py`, `dependency.py` (8 modules, plus `dispatcher.py` via dependency).
  Legacy consumers of `runtime/dependency.py`: `dispatcher.py`, `task_manager.py`.
  23 test files import work_unit/dependency (legacy-behavior coverage).

## 3. Import-safety engineering decision (Phase 1 only)

The Phase 1 spec rewrites `runtime/work_unit.py` (static node) and shrinks
`runtime/dependency.py` (pure DAG). Doing that in place would break the lazy imports of
`state.py` → hooks chain and kill the plugin. Resolution — **legacy twin relocation**:

- `git mv runtime/work_unit.py runtime/legacy_unit.py` — full v2.3 content preserved.
- `git mv runtime/dependency.py runtime/legacy_dependency.py` — full v2.3 content preserved.
- New `runtime/work_unit.py` = static node spec; new `runtime/dependency.py` = pure DAG
  semantics (Phase-1 target API).
- Mechanical one-line import repointing in the 8 legacy consumers (+ dispatcher/task_manager
  for dependency), and in the legacy-behavior tests that import them. No behavior change.
- The legacy twins are consumers-only until Phase 2 deletes them together with the
  dispatcher. New Phase-1 code must not import the twins. This is a bridge, not a
  compatibility API: the new modules expose no legacy surface (spec §2, "no compatibility
  setters").

`runtime/ownership.py` needs no relocation: its current surface is already pure path
semantics (`normalize_path`, `compile_pattern`, `match_path`, `classify_paths`,
`git_touched_files`); lease enforcement lives in `lease.py`. U3 extends it in place.

## 4. Unit decomposition (maps P1-A…P1-F → U1…U6)

| Unit | Work package | Primary files (ownership) | Depends on |
| --- | --- | --- | --- |
| U1 | P1-A reviewer model binding | `agents/glm-reviewer.md`, `agents/visual-reviewer.md`, `tests/test_agent_model_binding.py` | — |
| U2 | P1-B static node + DAG (+ legacy twins) | `runtime/work_unit.py`, `runtime/dependency.py`, `runtime/legacy_unit.py`, `runtime/legacy_dependency.py`, 8 legacy consumer import lines, repointed tests, `tests/test_static_node.py`, `tests/test_dag_semantics.py` | — |
| U3 | P1-C compile-time ownership stages | `runtime/ownership.py` (extend), `tests/test_ownership_stages.py` | U2 |
| U4 | P1-D Workflow compiler package | `runtime/workflow/__init__.py`, `persona.py`, `compiler.py`, `adapter.py`, `tests/test_workflow_compiler.py` | U2, U3 |
| U5 | P1-E repository writer guard | `runtime/writer_guard.py`, `tests/test_writer_guard.py` | — |
| U6 | P1-F new-path CLI + guard wiring | `runtime/cli.py` (append subcommands), `tests/test_cli_v24.py` | U4, U5 |

Ownership scopes are disjoint between units → safe parallelism inside the workflow.

## 5. Unit specs (five-part; embedded verbatim into workflow asks)

### U1 — Reviewer model-binding portability (P1-A)

- **Objective.** Make both reviewer agent definitions startable on any host configuration by
  removing the hard-coded provider-qualified `model:` from their frontmatter (host/session
  model inheritance), while preserving read-only tool allowlists, `thoughtLevel`, and both
  review output contracts.
- **Context.** W0 §13.1: both reviewers deterministically fail with
  `account-connection-unavailable` on this single-model host because
  `model: "account:bigmodel-individual-coding-plan/GLM-5.3"` (and `-Flash` for
  visual-reviewer) are not host-configured ids on some hosts; the ordinary Agent surface
  hard-fails instead of falling back. Spec §2 prefers inheritance over any pinned alias.
- **Interfaces.** No code interfaces. Frontmatter after change: same keys as now minus
  `model`. Body text unchanged except any sentence that asserts a specific model identity —
  reword to "runs on the session-inherited model; record actual runtime model in diagnostics
  when useful, never as a durable claim".
- **Constraints.** Do NOT touch `tools:` lines, `flash-implementer.md`, or
  `visual-implementer.md` (visual exception keeps its binding; text flash-implementer stays
  until Phase 2). Rewrite `tests/test_agent_model_binding.py`: reviewers must have **no**
  `model` field; the two implementers must keep provider-qualified `account:…/<model>` with
  their role suffixes. Check `git grep -ln "agents/" tests/` for other frontmatter-coupled
  tests and keep them green.
- **Local checks.** `PYTHONUTF8=1 python3 -m unittest tests.test_agent_model_binding` plus
  every test file touched by the grep above.
- **Report.** Files changed, frontmatter diff summary, tests run with results.

### U2 — Static Work Unit node + pure DAG semantics + legacy twins (P1-B)

- **Objective.** Establish the v2.4 static node model and pure DAG module as the public
  `runtime/work_unit.py` / `runtime/dependency.py`, relocate legacy v2.3 implementations to
  twin modules, repoint legacy consumers and legacy tests mechanically, keep the plugin fully
  importable.
- **Context.** §2/§3 of this plan: 8 legacy consumers, lazy hook imports, 23 test files.
  Repo idiom: dict-shaped records + module-level validator functions + quoted annotations,
  Chinese docstrings/comments are the norm in these files.
- **Interfaces.**
  - `runtime/work_unit.py` (new): `NODE_REQUIRED_KEYS = ("id", "objective", "depends_on",
    "ownership")`; `NODE_OPTIONAL_KEYS = ("interfaces", "constraints", "local_check")`;
    `new_node(node_id, objective, *, depends_on, ownership, interfaces=None,
    constraints=None, local_check=None) -> dict`; `validate_node(node) -> list[str]`
    (aggregate all errors, path-prefixed `node.<field>`): id non-empty unique-caller-checked
    string; objective non-empty string; depends_on string list without self-reference;
    ownership non-empty normalized repo-relative scope list; optional arrays default `[]`,
    all strings. **No** status/transition/attempt/result/verification API.
  - `runtime/dependency.py` (new): `graph_errors(nodes) -> list[str]` (missing dep, duplicate
    id, self-dependency, cycle), `topo_order(nodes) -> list[str]` (deterministic: sorted ids
    within ready set), `stage_levels(nodes) -> list[list[str]]` (deterministic levels for
    code generation), `downstream(node_id, nodes)` only if U4 will use it (default: include,
    U4 may ignore). **No** `ready_units`/`deps_satisfied` runtime readiness functions.
  - `runtime/legacy_unit.py`, `runtime/legacy_dependency.py`: byte-preserved v2.3 content
    (internal cross-imports repointed to each other), module docstring prepended noting the
    Phase-2 deletion intent.
- **Constraints.** Legacy consumer edits are import-line-only (`from runtime.work_unit
  import X` → `from runtime.legacy_unit import X`; `from runtime import dependency` →
  `from runtime import legacy_dependency as dependency`). No behavior edits, no test
  assertion changes beyond import paths. Plugin import smoke must pass:
  `PYTHONUTF8=1 python3 -c "import sys; sys.path.insert(0, 'plugins/glm-conductor');
  import runtime.state, runtime.task_manager, runtime.dispatcher, runtime.dispatch_wave,
  runtime.provenance, runtime.reconcile, runtime.recovery, runtime.resume_manifest,
  runtime.ownership, runtime.work_unit, runtime.dependency, runtime.workflow"` (last term
  after U4; without it for the U2 gate). Also `python3 -m py_compile` on the four hook files.
- **Local checks.** `PYTHONUTF8=1 python3 -m unittest tests.test_static_node
  tests.test_dag_semantics` + import smoke + a representative legacy slice
  (`tests.test_dependency` repointed, `tests.test_state` if it exists under that name —
  otherwise the two largest repointed legacy test modules) + `python3 scripts/validate_plugin.py`.
- **Report.** Twin relocation confirmation, list of repointed consumers/tests, new API
  summary, all gate outputs.

### U3 — Compile-time ownership stage planning (P1-C)

- **Objective.** Add deterministic stage planning with ownership-conflict serialization to
  `runtime/ownership.py`: given validated nodes, produce parallel stages whose members have
  provably disjoint ownership; serialize overlapping candidates by a deterministic ordering
  edge; fail with a clear error on ambiguous patterns.
- **Context.** Existing pure path helpers stay untouched (stop_gate and lease.py depend on
  them). W0 F9: host gives silent last-writer-wins, so this check is the only conflict
  barrier before launch.
- **Interfaces.** `class OwnershipConflictError(Exception)` with message naming the two node
  ids and the offending scopes; `scopes_overlap(scope_a, scope_b) -> bool` (exact file, file
  vs dir prefix, dir vs dir prefix, glob overlap via `compile_pattern`); `plan_stages(nodes)
  -> list[list[str]]`: start from `dependency.stage_levels`, then within each level greedily
  group into maximal parallel groups: sort candidates by node id; a node that overlaps any
  already-placed member of its group is deferred to the next group of the same level
  (deterministic serialization by derived ordering); defer only re-orders within the level —
  cross-level dependencies remain authoritative; if a scope pattern is invalid/undecidable
  (`compile_pattern` fails or matches everything, e.g. `**`), raise `OwnershipConflictError`
  rather than guessing.
- **Constraints.** No locks, no leases, no runtime reservation (that is U5's separate,
  repo-level guard). Do not modify existing functions' signatures. 3.7-compatible annotations.
- **Local checks.** New `tests/test_ownership_stages.py` covering at minimum: exact same
  file → serialized; parent/child path overlap → serialized; disjoint directories → same
  stage; glob overlap → serialized; ambiguous/invalid pattern → `OwnershipConflictError`;
  determinism (same input → identical output across calls). Plus existing
  `tests/test_ownership*.py` (must stay green) and validator.
- **Report.** API summary, stage-planning algorithm in three sentences, test results.

### U4 — Native Workflow compiler package (P1-D)

- **Objective.** Create `runtime/workflow/` package: single-source text worker persona,
  deterministic DAG→TypeScript compiler, thin run-id correlation adapter.
- **Context.** W0 facts: one model per run (host-validated), full toolset per child, no
  nesting, typed results reach the main session, phase markers required by the host facade,
  `agent()` names are labels (must still be unique and stable). The generated script targets
  the dynamic-workflow facade (`agent`, `ask<T>`, `phase`, `Promise.all`, `return`).
- **Interfaces.**
  - `persona.py`: `TEXT_WORKER_PERSONA` (string, migrated semantics from
    `agents/flash-implementer.md`: bounded-objective execution only; respect
    ownership/interfaces/constraints; no redesign; surface ambiguity as a reassessment
    request; run optional local checks honestly; structured result; **no** permit/receipt
    language) + `build_ask(node, task_context) -> str` (objective, ownership, interfaces,
    constraints, local_check, result contract).
  - `compiler.py`: `compile_workflow(nodes, task_context) -> str` (TypeScript source).
    Deterministic: iterate stages from `ownership.plan_stages`, nodes sorted by id; one
    `phase("阶段 <i>: <node ids>")` per stage; one `agent("node-<id>", TEXT_WORKER_PERSONA)`
    per node; each node's ask returns a `NodeResult` interface
    `{node_id, status: complete|partial|blocked, changes, local_checks, reassessment, gaps}`;
    within a stage `Promise.all`; final `return` aggregates all results; Conductor node ids
    verbatim in actor names, instructions, and results; never embed credentials/secrets;
    never emit permit/lease identifiers.
  - `adapter.py`: `record_run(task_ref, workflow_run_id, artifact_path=None)` → persists
    `{task_ref, workflow_run_id, node_map?}` as one JSON under
    `.glm-conductor/workflow-runs/` following `durable_io` atomic-write conventions;
    `load_run(task_ref)`. Persists **nothing else** — no child status, retry count, token or
    health mirroring.
- **Constraints.** Pure stdlib; no imports from legacy twins or dispatcher family; no
  timestamps/randomness in generated source (determinism gate); package must import cleanly
  standalone. Do not launch anything — execution is main-session's job.
- **Local checks.** New `tests/test_workflow_compiler.py`: byte-identical double compile;
  all node ids present (actor names + result mapping); stage structure (Promise.all count ==
  stage count, sequential await between stages); NodeResult fields present; forbidden-token
  scan (permit, lease, credential, api_key, secret) over generated source; `adapter` record/
  load roundtrip + atomic-write behavior; import smoke including `runtime.workflow`.
- **Report.** Package layout, generated-sample excerpt (first ~40 lines), test results.

### U5 — Repository writer guard (P1-E)

- **Objective.** Coarse repository-level reservation: at most one active Conductor write
  Workflow per Git repository; recovery only via terminal state or explicit release.
- **Context.** Spec forbids TTL/generation/heartbeat designs. Durable state convention:
  `.glm-conductor/` inside the repository (see `durable_io.py` helpers — reuse its
  atomic-write and locking utilities if present).
- **Interfaces.** `runtime/writer_guard.py`:
  `acquire(repo_root, task_id, workflow_run_id=None) -> dict` →
  `{ok: bool, conflict: {task_id, workflow_run_id, created_at} | null}` (failure must
  report the conflicting task/workflow id);
  `release(repo_root, task_id, workflow_run_id=None) -> dict` (idempotent; refuses to
  release a reservation owned by a different task);
  `inspect(repo_root) -> dict | null`. Single durable record
  `.glm-conductor/writer_guard.json`: `{repo_identity, task_id, workflow_run_id, created_at}`;
  `repo_identity` = absolute repo root. **No** time-based expiry anywhere.
- **Constraints.** No per-unit semantics; no scheduler/epoch concepts; the acquiring side
  (U6 CLI / main session procedure) supplies ids. 3.7-compatible; pure stdlib + durable_io.
- **Local checks.** New `tests/test_writer_guard.py`: first acquire ok; second acquire by
  different task reports conflict with full ids; same-task re-acquire idempotent-ok; release
  by owner ok + idempotent; release by non-owner refused; inspect empty/occupied; crash-
  simulation (record left behind → still conflicts, no auto-expiry); validator.
- **Report.** API + record format, test results.

### U6 — New-path CLI and guard wiring (P1-F)

- **Objective.** Expose the Phase-1 path for smoke use: CLI subcommands that validate a DAG,
  plan ownership stages, emit the compiled Workflow source, and manage the writer guard.
- **Context.** `runtime/cli.py` uses hand-rolled `if cmd == "…"` dispatch (~line 951+);
  append-only integration. Routing vocabulary itself is main-session/skill-level (Phase 4
  rewrites skills) — Phase 1 needs no skill edits; this CLI is the smoke-time entry.
- **Interfaces.** New subcommands (arg style follows existing neighbors):
  - `v24-compile <dag-json> [--task-ref <id>] [--out <path>]` → validate nodes
    (`work_unit.validate_node`, `dependency.graph_errors`), `ownership.plan_stages`,
    `compiler.compile_workflow`; prints source to stdout or `--out` file; exit 0/2.
  - `writer-acquire <repo-root> <task-id> [--run-id <id>]`,
    `writer-release <repo-root> <task-id> [--run-id <id>] [--force]` (`--force` = the
    explicit operator clear path after inspection), `writer-show <repo-root>` → JSON.
  - `v24-record-run <task-ref> <run-id> [--artifact <path>]` → adapter record.
- **Constraints.** Do not touch existing subcommands or their tests. Generated source must
  never be auto-executed by the CLI. No state.json/task-manager integration (Phase 2).
- **Local checks.** New `tests/test_cli_v24.py`: v24-compile golden output + invalid DAG
  exit codes; writer acquire/release/show roundtrip incl. conflict reporting; --force path;
  plus `tests.test_writer_guard tests.test_workflow_compiler` re-run and validator +
  `scripts/smoke_plugin_load.py`.
- **Report.** Subcommand help text, gate outputs.

## 6. First workflow — topology and gates (submitted via CreateWorkflow after user confirmation)

- **Name:** `v2.4 Phase 1 原生语义核心实施`.
- **Model:** `subagent_model = account:bigmodel-individual-coding-plan/GLM-5.3-Flash`.
- **Actors (one per unit, names are user-facing):** `U1 评审者绑定修复`,
  `U2 静态节点与DAG重置`, `U3 ownership编译期阶段规划`, `U4 Workflow编译器包`,
  `U5 仓库写者守卫`, `U6 新路径CLI与守卫接线`.
- **Phase graph:**
  1. `phase("并行基础单元：评审者绑定 + 静态DAG + 写者守卫")` — U1 ∥ U2 ∥ U5.
  2. `phase("ownership 编译期阶段规划")` — U3 (after U2 gate).
  3. `phase("Workflow 编译器包")` — U4 (after U3 gate).
  4. `phase("新路径 CLI 与定向测试收口")` — U6 (after U4 + U5 gates), then the phase gate.
- **Gate discipline (model writes, code gates).** After each unit ask, the script runs that
  unit's §5 local-check commands via `world.run` (`PYTHONUTF8=1 python3 -m unittest …` from
  repo root; validator via `python3 scripts/validate_plugin.py`). On nonzero exit the
  failure output goes back to the same actor for a bounded fix round (max 2 retries per
  unit); a unit still failing after its rounds is reported failed — no silent pass, no gate
  loosening, no edit of tests to make them green without a spec basis.
- **Final in-workflow gate:** the full Phase-1 targeted list
  (`test_agent_model_binding test_static_node test_dag_semantics test_ownership_stages
  test_workflow_compiler test_writer_guard test_cli_v24` + two largest repointed legacy
  slices) + validator + `scripts/smoke_plugin_load.py` + hook-file `py_compile`.
- **Report surface:** `report()` items feed a small unit-status board; final artifact =
  markdown implementation report (primary) with per-unit files/tests/evidence; the workflow
  `return` is a compact structured summary for main-session review.
- **Not inside the workflow (main-session-only):** live Workflow smokes (CreateWorkflow is a
  main-session tool), git commits, acceptance review, phase exit-gate report.

## 7. Main-session acceptance procedure (after the workflow returns)

1. Review the returned summary + markdown report; read every diff hunk (unit by unit)
   against §5 specs; spot-check generated compiler output by hand.
2. Re-run the final in-workflow gate list myself (independent reproduction — implementer
   self-reports are not accepted).
3. Live smokes (three, each on Flash): compile a minimal 1-node DAG via `v24-compile`,
   submit via CreateWorkflow, expect structured NodeResult; same for a 2-node disjoint
   parallel DAG (both nodes in one stage) and a 2-node staged dependency DAG. Also verify
   `writer-acquire` blocks a second acquire on this repo during a live run.
4. Reviewer launch acceptance (open point, default proposal): U1's guarantee is static
   (no `model` field → inheritance). Live launch proof requires a plugin cache refresh
   (robocopy /MIR repo → cache 2.3.2 dir) + a **fresh session** dispatch of both reviewer
   types — user-assisted (open a new session, or defer to Phase 4 P4-E live reviewer smoke
   per the release spec). Decision recorded in the Phase 1 exit report either way.
5. Local commits: one commit per unit (message convention
   `feat(v2.4-p1): U<n> <slug> (work package P1-X)`), then a short Phase 1 implementation
   report appended to this document recording the exact targeted tests run (spec §10).
6. Evaluate the Phase 1 exit gate (spec §10 checklist) and report to the user; Phase 2
   workflow is planned only after user confirmation.

## 8. Explicitly out of scope for Phase 1

- Any state.py/provenance/quota/continuity changes (Phase 2/3).
- Skill/command rewrites beyond the U6 CLI (Phase 4); README/architecture doc updates (Phase 4).
- Deletion of dispatcher/wave/lease/hooks registrations (Phase 2) — only the twin relocation.
- `visual-implementer` migration (stays Custom Subagent exception).
- Full test-suite regression (Phase 4 only), any `git push`, any conductor-task accounting.
