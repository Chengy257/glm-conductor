---
name: quota
description: Read-only GLM quota observation: report.py diagnostics + quota-resolve four-state resolution (AVAILABLE/PRESSURE/EXHAUSTED/UNKNOWN). Use for quota remaining, 5-hour/weekly windows, reset times, quota pressure, or pre-dispatch/pre-resume quota state.
---

# GLM Coding Plan quota observation (read-only)

Since v2.4, the quota surface has converged to **two read-only observation surfaces**: the diagnostics surface (report.py) and the resolution surface (quota-resolve). Neither surface performs task-state transitions, neither makes blocking decisions, and neither outputs credentials.

## Surface 1: report.py (human-readable diagnostics; the body of `/glm-conductor:quota`)

Run the diagnostic script with Bash and present its output to the user **verbatim** (no paraphrasing, no truncation, no interpretive prefixes). The human-readable output remains Chinese by owner ruling — that is expected; never translate, re-render, or "fix" it:

```
python3 ~/.zcode/cli/plugins/cache/glm-conductor/glm-conductor/<version>/runtime/quota/report.py
```

For machine-readable output (for orchestration decisions), add `--json`:

```
python3 ~/.zcode/cli/plugins/cache/glm-conductor/glm-conductor/<version>/runtime/quota/report.py --json
```

`<version>` is the actual version directory under the plugin cache (`ls ~/.zcode/cli/plugins/cache/glm-conductor/glm-conductor/` lists them).

Interpretation:

- Text output: for each quota window (`5-hour` / `weekly`) the used/remaining/reset values, `status:` (AVAILABLE / PRESSURE / EXHAUSTED / UNKNOWN), and the `plan:` recovery suggestion (continue / checkpoint / resume_at / periodic_fallback)
- **Lite plan**: only the 5-hour window exists, no weekly window — the weekly line shows `not present`; this is the normal shape, not a probe failure
- Three diagnostic modes: a normal quota report / `quota 探测失败` (one error-classification line per provider) / `unavailable` (no credentials found — follow the configuration guidance in the output)
- The exit code is always 0: this command is a read-only diagnostics surface, not a gate; never make blocking decisions from its exit code

## Surface 2: quota-resolve (orchestration four-state resolution)

For the main session to obtain the quota state before dispatch / before a resume decision:

```
python3 <plugin-root>/runtime/cli.py quota-resolve <repo_root> [--force-refresh]
```

- Emits frozen-key single-line JSON: `{status, source, evaluated_at, reason}`; status ∈ AVAILABLE / PRESSURE / EXHAUSTED / UNKNOWN
- Resolution layering: fresh cache (≤300s) → provider fetch → stale cache → UNKNOWN — **never default to AVAILABLE, never retry the network**; `--force-refresh` skips the fresh-cache layer and forces the provider path (recommended before resume decisions; do not trust sleep-period caches)
- On provider success the local quota cache is refreshed (an observation cache, not task state); **zero task-state transitions** — it only answers "what state the quota is in right now"; state transitions and resume decisions belong to the runtime.task lifecycle API (see the continuity skill)
- UNKNOWN is not an error: when credentials are unavailable / the provider is unreachable, treat it as unknowable — the orchestration side keeps waiting or asks the user; never fabricate availability
- Exit codes: 0 success / 2 usage error / 1 runtime exception (the error JSON contains only the exception type name and message)

**reset_at semantics (v2.6.0 single observation caliber)**: the `reset_at` of the `quota-wait` / `quota-resume-decision` observation views and the `reset_at` carried by the decision passthrough (**any action passes it through unconditionally, including no-op**) are all the **earliest valid future observed reset_at across the identified windows** (parsed and compared explicitly as UTC instants, regardless of quota status — the per-window AVAILABLE/PRESSURE/EXHAUSTED/UNKNOWN status does not take part in the selection; expired / invalid resets do not take part; a stale / invalid observation is force-refreshed exactly once — if still unobtainable → null with an honest floor-only disclosure, never fabricated) — quota status governs only whether one may continue / resume, never whether a time may be passed through; the next observed window boundary is the safe wake / checkpoint. Precision-layer sleep-target conversion always uses this value (+300s grace; template in the continuity skill).

## Boundaries

- Read-only observation: neither surface transitions state or blocks; the scripts output zero credentials (no keys / Authorization / response bodies)
- Credential sources: the `GLM_CONDUCTOR_QUOTA_API_KEY` environment variable (preferred), or the logged-in ZCode `~/.zcode/v2/config.json` (provider `builtin:bigmodel-coding-plan`) as fallback
- Quota observation is used as evidence only: it is not a routing axis and is never a basis for bypassing authorization; resume decisions follow the continuity skill's idempotent decision contract
- No resident observation process and no polling service — queries happen only at refresh points (task start / before dispatch / before a resume decision)
- Window mechanism caliber: at reset_at a window is restored to 100% (the weekly window takes priority); the next reset_at materializes only when model calls occur within the new window — pure queries never advance it
- If `python3` is unavailable: follow the enforcement skill's environment self-check section; never degrade to guessing or skipping
