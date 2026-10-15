# Role contracts

This file is the complete role-contract reference of the glm-conductor orchestration system; SKILL.md points here for the criteria and template details.

## Dual-axis routing contract

### Axis A — Delegability

The judgment question: is the remaining implementation work bounded and specification-complete enough to delegate?

**delegability: high** — typically all of:

- objective and observable outcome are explicit
- target files and ownership boundaries are explicit
- interfaces are explicit
- constraints are explicit
- verification is explicit
- the architecture is decided; the remaining task is mostly implementation
- implementation will not need frequent redesign

Typical tasks: function implementation with a settled interface, test/fixture additions, repetitive refactors, CLI wiring, configuration pipelines, serialization, bug fixes with a known root cause, bulk code migrations with explicit rules.

**delegability: low** — judge low first when any of:

- the architecture is unsettled
- the root cause is unknown
- the requirements carry substantive ambiguity
- heavy cross-module judgment
- implementation is itself the main reasoning problem
- hidden complexity such as concurrency / distributed state
- sensitive decisions unresolved
- the design may change frequently during implementation

At delegability: low the main session implements (the main model inherits the host main session and is not programmatically switchable).

### Axis B — Assurance

The judgment question: after implementation passes main-session validation, does one independent final review from a fresh context have substantive value?

**assurance: standard** — ordinary implementation with a limited blast radius, sufficiently explicit verification, and controlled regression risk.

**assurance: high** — prefer high when any of:

- wide blast radius
- high regression risk
- authentication / permissions
- destructive behavior
- data migration
- cross-module semantic changes
- verification cannot cover every important failure mode
- large-scale user-visible changes
- the user explicitly asks for strict review

### Routing matrix

| Delegability | Assurance | Route | Implementation | Independent review |
| --- | --- | --- | --- | --- |
| low | standard | solo | main session (host model) | no |
| high | standard | delegate | native Workflow (text worker) | no |
| low | high | audit | main session (host model) | yes |
| high | high | full | native Workflow (text worker) | yes |

Semantic points:

- delegate is not a risk escalation of solo: solo = main-session implementation (the main model inherits the host main session), delegate = native-Workflow text-worker implementation; delegate only states "this implementation is bounded enough"
- audit = main-session implementation + reviewer final review (judgment-dense, sensitive, architecture-heavy work)
- full = delegated implementation + reviewer final review (large but bounded: mechanical API migrations, UI revamps with clear specs, repetitive multi-file conversions)
- visual/interactive tasks are a capability choice of implementation modality (the visual-implementer subagent channel), not a fifth route

### SELECTIVE ROUTE declaration

Output once by the main session before any implementation delegation (only the two keys mode and assurance enter state):

```
SELECTIVE ROUTE
mode: solo | delegate | audit | full
delegability: low | high
assurance: standard | high
reason: <concise, evidence-based reason>
```

### ROUTE REASSESSMENT

A route change must come from newly observed evidence and may be reassessed in both directions — never changed on a hunch or for convenience:

```
ROUTE REASSESSMENT

delegability: <old> -> <new>
assurance: <old> -> <new>
mode: <old> -> <new>

evidence:
<newly observed evidence>
```

- Downgrade example: delegate execution keeps returning node results with reassessment requests, exposing hidden cross-module state coupling, architecture ambiguity, or scope beyond ownership -> delegability high→low, assurance standard→high, mode delegate→audit, and the main session takes over implementation
- Upgrade example: after solo investigation the root cause, architecture, files and verification are all settled and the remaining work is purely mechanical -> delegability low→high, mode solo→delegate (precondition: the main session has not itself repeatedly completed the same implementation)
- A reassessment request in a result's `reassessment` field is valid evidence
- A reviewer's rethink, or multiple fix-first findings, is valid evidence

## Five-part implementation spec (the semantic frame of node declarations)

The five-part spec is the thinking frame for implementation declarations: the required/optional fields of static DAG nodes are exactly its landing points (objective ↔ OBJECTIVE, ownership ↔ FILES AND OWNERSHIP, interfaces ↔ INTERFACES, constraints ↔ CONSTRAINTS, local_check ↔ VERIFICATION); the visual channel's subagent deliveries still use this template text directly. The template is copy-ready:

```
OBJECTIVE:
<the observable outcome and why it matters>

FILES AND OWNERSHIP:
- <only the implementer's own files>
(note: the codebase may be edited concurrently — never revert others'
changes, never modify outside scope)

INTERFACES:
<signatures, types, patterns, commands that must stay compatible>

CONSTRAINTS:
<repo conventions, safety boundaries, exclusions, settled decisions>

VERIFICATION:
<exact commands and the specific expected results / evidence>

RETURN:
A completion claim without evidence is invalid
```

Purpose of each section:

- **OBJECTIVE** — the observable outcome and why it matters
- **FILES AND OWNERSHIP** — only the implementer's own files; note that the codebase may be edited concurrently and that reverting others' changes or modifying outside scope is forbidden
- **INTERFACES** — signatures, types, patterns, commands that must stay compatible
- **CONSTRAINTS** — repo conventions, safety boundaries, exclusions, settled decisions
- **VERIFICATION** — exact commands and the specific expected results / evidence (in node form this is local_check)
- **RETURN** — the return requirement appended at the end of the spec: "A completion claim without evidence is invalid"

Node authoring discipline (DAG-side constraints in the SKILL.md section 'Task state and static DAG authoring'): every node must still stay small enough to receive one complete bounded objective — needing a further split means decomposition was insufficient.

## TASK CONTEXT PACK (the context pack prepended to the implementation declaration)

For delegations into complex or unfamiliar code domains, prepare a context pack **before** the implementation declaration — bounded context the main session compressed with its expensive reasoning and reconnaissance, handed to the implementer for bounded execution (this is exactly the value point of heterogeneous division of labor: the strong main model compresses, the executor executes). When ROUTING PREFLIGHT was done, the pack's entries are taken directly from the PREFLIGHT REPORT — no repeated reconnaissance.

Template (prepended at the very front of the implementation declaration; title line verbatim):

```
TASK CONTEXT PACK

TASK:
<one-sentence task positioning>

ROOT CAUSE:
<why the change is needed — the actually observed root cause, not a guess>

RELEVANT FILES:
- <file paths (+ key line numbers / symbols)>

KEY SYMBOLS:
- <functions / classes / constants and their responsibilities>

CALL / DATA FLOW:
<how the changed data or calls flow, two or three sentences>

INTERFACES:
<contracts that must stay compatible>

OWNERSHIP:
<the implementation scope this pack authorizes (consistent with FILES AND OWNERSHIP)>

TEST ENTRYPOINTS:
- <verification entrypoint commands>

KNOWN RISKS:
- <identified pitfalls (concurrent-edit points / platform differences / implicit dependencies)>

EXCLUDED AREAS:
- <areas explicitly not touched>
```

Rules:

- **Evidence-based and compact**: every entry comes from the main session's actual observation (files / symbols / call chains); never write speculative facts in as settled constraints; never dump the whole repository, never copy whole large files, never re-paste full diffs — a path + line reference beats pasted code
- Bounded: typically 15-40 lines; exceeding that means decomposition was insufficient — split the nodes before delegating
- Simple tasks (clear boundaries, self-evident context) may omit the whole pack and go straight to the implementation declaration

## Visual tasks and the VISUAL ACCEPTANCE extension

Visual tasks append a VISUAL ACCEPTANCE section on top of the five-part spec (placed after VERIFICATION; the other five sections unchanged).

**Tasks suited to the visual channel**:

- React/Vue/Svelte frontends, CSS / layout, responsive design
- dashboards, browser workflows, form interactions
- GUI, Canvas/WebGL, game UI
- visual regressions, screenshot-based acceptance, user-visible page revamps

**Not suited (the native Workflow text channel)**:

- ordinary backend functions, CLI, parsers, pure data transformations, server-side code with no visual acceptance

VISUAL ACCEPTANCE template (copy-ready):

```
VISUAL ACCEPTANCE:
- target page / component: <target page or component>
- viewport: <desktop 1440×900 / mobile 390×844 etc.>
- required interaction: <the interaction to perform>
- expected visible state: <the expected visible state>
- responsive behavior: <responsive requirements>
- clipping / overflow constraints: <clipping and overflow constraints>
- important user-visible content: <important user-visible content>
```

Example:

```
VISUAL ACCEPTANCE:
- target page / component: the API Key form of the settings page
- viewport: desktop 1440×900
- required interaction: enter an invalid key and click save
- expected visible state: a red error hint appears below the input, with no page jump
- responsive behavior: at 390×844 the form stacks single-column and the button stays clickable
- clipping / overflow constraints: the error hint fully visible, no horizontal scrollbar
- important user-visible content: the error text explains the cause and the fix
```

## IMPLEMENTATION REPORT template (subagent channel)

Mandatory for the visual channel (visual-implementer) on return; the structured contract for ordinary implementation results is NodeResult in the next section:

```
IMPLEMENTATION REPORT
STATUS: complete | partial | blocked
OBJECTIVE: <one-sentence restatement of the goal>
CHANGES:
- <list changes file by file>
VERIFIED:
- <command>: <actual output>
JUDGMENT CALLS:
- <judgment decisions made during implementation> ("none" when none)
GAPS:
- <uncovered items> ("none" when none)
```

- STATUS is one of: complete / partial / blocked
- VERIFIED must be command-and-actual-output pairs; vague descriptions are not accepted
- The report is only a claim (an implementation claim); evidence exists only in the diff and command output the main session personally observed

## Text-worker contract (native Workflow implementation)

delegate/full text implementation is executed by the text worker of a native Workflow run:

- **Worker model authorization**: the worker model is the exact choice the user explicitly authorized in the launch interview ('Task launch interview') — all host-configured candidates go on the list (no provider restriction, not only Flash), the Flash tier is a soft-recommendation slot, and **a single candidate also requires explicit user confirmation**; **the selected model defaults to the max reasoning tier (the host default reasoning tier), and the tier is not stored in the pin** — the task pin stores the model id only. The submission goes through `workflow-model-select`, producing the `user-authorized-explicit-selection` submission contract, and `subagent_model` is never omitted — omitting it inherits the main-session model. For a pinned task an explicit `--model` value must match the pin; changing the authorization always goes through the interactive interview re-authorization; an unattended wake turn never re-pins automatically
- **Source**: the persona is defined solely in `runtime/workflow/persona.py`, and the compiler (v24-compile) embeds it verbatim into the generated Workflow source — the host runtime does not consume plugin agent definitions; role simulation relies on the persona text embedded in the script
- **Positioning**: the precise executor of a bounded objective — execute only the delivered objective, never redesign the architecture; no architecture decisions beyond the objective, no scope expansion, no drive-by refactors
- **Scope discipline**: modify only the repo-relative paths covered by the node's ownership; honor the declared interfaces and constraints; the same repository may be edited concurrently by other agents — never revert others' unrelated changes
- **Ambiguity handling**: on substantive ambiguity, scope conflicts, or spec gaps, never decide unilaterally — return an explicit reassessment request in the result's `reassessment` field for the main session to adjudicate
- **Honest local_check execution**: when non-empty, execute every item and record results and failures faithfully; when empty, invent no checks; never fabricate output, never omit failures
- **Result contract (NodeResult, exactly six fields)**: `node_id` / `status` (complete | partial | blocked) / `changes` / `local_checks` / `reassessment` / `gaps` — a completion claim without evidence is invalid; a result whose status contradicts the actual evidence is rejected at main-session validation
- **Main-session-side meaning**: the text worker has no invocable subagent definition and no dispatch credentials or marker mechanism — every constraint on it comes from the node declaration (embedded in the ask instruction) and the completion guard's post-hoc checks

## visual-implementer contract (visual-task implementer)

- **Positioning**: the bounded implementation executor for visual/interactive tasks
- **Model**: GLM-5.3-Flash (multimodal), thinking tier high (fixed, no overrides attached) — static binding facts and maintenance operations in the 'Binding Maintenance' section of agents/visual-implementer.md
- **Use**: visual/interactive task implementation only — executes the five-part spec with the VISUAL ACCEPTANCE extension
- **Visual feedback loop (cross-call topology)**: when the implementer needs evidence it returns `VISUAL_CAPTURE_REQUEST` and ends the invocation (no waiting, no fabrication) -> the main session captures screenshots to disk with its dedicated Browser/Computer Use (main-session-only; subagents are barred from it at the ZCode policy layer) -> the main session starts a new invocation carrying the full state (canonical path: the five-part spec incl. VISUAL ACCEPTANCE, the current diff / change status, the previous round's VISUAL_CAPTURE_REQUEST, the screenshot paths, the VISUAL_ROUND round number; resuming the same implementer is an optional optimization only when the runtime has a verified stable resume mechanism — protocol correctness never depends on resume) -> the implementer personally Reads the screenshots and judges -> on mismatch it corrects and returns `VISUAL_CAPTURE_REQUEST` again -> every VISUAL ACCEPTANCE criterion is tracked with VISUAL_ROUND (1|2|3), at most 3 capture-judge rounds; unbounded visual polishing is forbidden. Protocol format in the 'VISUAL_CAPTURE_REQUEST' section of agents/visual-implementer.md
- **Reassessment signal**: return blocked when the acceptance criteria are unclear, the spec is ambiguous, or visual evidence is unobtainable, and hand back to the main session; never substitute textual speculation for visual verification
- **Report**: uses the IMPLEMENTATION REPORT template with the FUNCTIONAL VERIFIED and VISUAL VERIFIED sections (template in agents/visual-implementer.md)
- **Dispatch**: `subagent_type: glm-conductor:visual-implementer`

## visual-reviewer contract (visual-task reviewer)

- **Positioning**: a fresh-context, implementation-isolated read-only reviewer; independence comes from the clean context and the read-only tool whitelist — no cross-model independence is claimed
- **Model**: GLM-5.3-Flash (multimodal, explicit provider-qualified frontmatter binding) — personally reading the screenshots for visual acceptance is part of the role contract; on a host where the binding is unavailable this role is unavailable (fail closed), never silently falling back to a text model pretending to review visually; static binding facts and maintenance operations in the 'Binding Maintenance' section of agents/visual-reviewer.md
- **Thinking tier**: max (fixed)
- **Use**: visual-task audit/full routes only, invoked only after main-session validation; the primary duty is independent visual acceptance (screenshot evidence + VISUAL ACCEPTANCE + user-visible regressions); the code diff is read as context and never replaces the main session's code review
- **Division of duties**: the main session owns the full diff, code correctness, functional verification and scope/interface checks; visual-reviewer owns visual acceptance, interaction evidence, screenshot evidence and user-visible regressions
- **Six input elements** (provided by the main session; a missing element is requested, never guessed):
  - ROLE: declares this a read-only review
  - STATED GOAL: the user's original goal verbatim
  - ACCUMULATED CHANGE SET: allowed-file list + full diff, or base/target revisions
  - INTERFACES AND CONSTRAINTS: the interfaces and constraints that must stay compatible
  - VERIFICATION EVIDENCE: verification commands mapped to the main session's actually observed output
  - VISUAL EVIDENCE: the screenshot file path list + the corresponding VISUAL ACCEPTANCE criteria
- **Review scope**: visual conformance is the main line (personally Read every screenshot and judge the implementer's visual conclusions against VISUAL ACCEPTANCE), plus user-visible regressions and evidence-chain completeness; correctness, completeness, regression risk, scope discipline, interface preservation and test sufficiency are secondary checks (reportable, never a substitute for the main session's code review)
- **VISUAL REVIEW output format**:

  ```
  VISUAL REVIEW
  VERDICT: ship | fix-first | rethink
  REASON: <the decisive reason based on visual evidence>
  FINDINGS:
  - <screenshot path / VISUAL ACCEPTANCE criterion>: <finding> ("none" when none)
  RESIDUAL VISUAL RISK: <the most important remaining visual risk> ("none" when none)
  ```

- **Verdict invalidation rule**: after any fix the verdict is void and a fresh reviewer must re-review; verdicts are recorded through the CLI `review-record` (change_id-bound; validate first, review second)
- **Dispatch**: `subagent_type: glm-conductor:visual-reviewer`

## glm-reviewer contract (text-task reviewer)

The reviewer is chosen by task modality — text tasks use this role, visual tasks use visual-reviewer (above).

- **Positioning**: a fresh-context, implementation-isolated read-only reviewer — no cross-model independence is claimed; the model inherits the host/session (no model field in the frontmatter), thinking tier max (fixed)
- **Use**: text-task audit/full routes only, invoked only after main-session validation; fresh context = fresh reviewer
- **Five input elements** (provided by the main session):
  - ROLE: declares this a read-only review
  - STATED GOAL: the user's original goal verbatim, never paraphrased or altered
  - ACCUMULATED CHANGE SET: allowed-file list + full diff, or base/target revisions
  - INTERFACES AND CONSTRAINTS: the signatures, types, patterns, commands and constraints that must stay compatible
  - VERIFICATION EVIDENCE: verification commands mapped to the main session's actually observed output
- **Review scope, seven items**: correctness, completeness, regression risk, scope discipline (out-of-bounds changes), interface preservation, test sufficiency, substantive risk
- **GLM REVIEW output format**:

  ```
  GLM REVIEW
  VERDICT: ship | fix-first | rethink
  REASON: <the decisive reason based on evidence>
  FINDINGS:
  - <file:line> <finding> ("none" when none)
  RESIDUAL RISK: <the most important remaining risk> ("none" when none)
  ```

- **Verdict invalidation rule**: after any fix the verdict is void and a fresh reviewer must re-review; verdicts are recorded through the CLI `review-record` (change_id-bound; validate first, review second)
- **Isolation is judged by observation, not by request**: the reviewer's tools being a read-only whitelist counts as isolation enforced; if any write attempt or out-of-scope behavior is observed, terminate that channel

## Main-session (architect) contract

The main session retains the following duties (the main model inherits the host main session and is not programmatically switchable — Conductor never requires and never inspects the main session's concrete model name; interview turns give soft advice toward strong-tier models for architecture / planning / validation, never blocking other configured choices); these duties are never delegated:

- requirements and ambiguity resolution
- architecture and routing judgment
- static DAG authoring and implementation-spec writing
- personally inspecting the full diff and re-running the verification commands
- route reassessment decisions
- acceptance and the completion declaration

Auxiliary work substitutes for, and never repeats, the main session's work: after delegating implementation the main session does not write the implementation again — it validates and accepts.

**Capability boundary**: the main model inherits the host main session, and Conductor never asserts its strength or modality capability from a model name or version alone. In the visual chain the main session drives and captures (launching apps, driving the browser / desktop, saving screenshots) and must never claim it made visual judgments; visual judgment is made by the explicitly bound multimodal visual roles (visual-implementer / visual-reviewer).
