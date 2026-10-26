# Post-v2.8.0 Model-Binding Hotfix: Retire the Rotted Plan-Tier Literal Pins (2026-10-26)

Owner ruling, issued in session after live-use reports. Scope: the model-binding
design that v2.8.0 hardened into exact literals. No version bump is performed by
this hotfix itself; release/cache refresh stay with the owner.

## 1. Live-use problem reports (verbatim themes)

1. An executing session reasoned: "the Agent tool's built-in roles each carry
   their own fixed model bindings and cannot be rewritten, so I will route all
   delegated work (including the fresh-context independent review) through the
   CreateWorkflow `subagent_model` channel."
2. The user asked why `glm-reviewer` is bound to a model on the personal
   subscription's start-plan tier, and ruled: model-related values must not be
   dead-fixed; keep them as suggested defaults that remain modifiable.

## 2. Root cause (verified 2026-10-26)

- **The pinned provider left the host.** `ListModels` on 2026-10-26 lists
  `account:bigmodel-individual-coding-plan`, `account:bigmodel-offpeak-idle-plan`,
  `account:bigmodel-highspeed-card`, and `opencode-go-messages` — the
  `account:bigmodel-start-plan` provider targeted by all four v2.8.0 literal
  pins (conductor-scout / conductor-runner / conductor-impl-flash /
  glm-reviewer) is absent.
- **Live probe (independent reproduction).** Direct Agent dispatches of
  `glm-reviewer` and `conductor-scout` (both pinned to the absent provider)
  both launched and answered normally on 2026-10-26. So on this host an
  unresolvable pin does not hard-fail; the role launches through **silent
  routing whose target cannot be verified from the session**. The v2.8.0 pin's
  stated purpose — removing silent-routing ambiguity — is therefore defeated by
  the pin's own rot: a dead literal plus silent fallback is strictly worse than
  the inherit form it replaced. (Historical counter-evidence stands: an
  unresolvable pin has also been observed to hard-fail with
  `account-connection-unavailable` on other hosts/versions — behavior varies;
  either way the binding is unverifiable once the provider is gone.)
- **Why the executing session drifted to the workflow channel.** The conductor
  doctrine exposes exactly one model-choice surface — the
  `workflow-model-select` → `subagent_model` gate — and repeatedly states that
  role bindings are fixed and never overridden at invocation. A session trying
  to honor "explicit model selection" found no model knob on the Agent channel
  and concluded the workflow channel is the legitimate way to control models,
  routing everything (including review, which is a direct-dispatch role) there.
  Nothing in the doctrine stated the missing counter-rule: **model choice is
  never a reason to change the delegation channel.**
- **Enforcement welded the rot in place.** Validator check 2 required
  `glm-reviewer`'s `model` to equal the plan-tier literal **verbatim**, and 16p
  banned the wording of the previous inherit rule — so the repo's own gates
  made the necessary fix a FAIL.

## 3. Ruling (owner, 2026-10-26)

Model-related values are **defaults, not dead literals**; they stay modifiable:

1. `glm-reviewer` returns to the **no-model-key inherit form** (as
   `conductor-impl`): it runs on whatever model the session itself runs on, on
   any host — always launchable, never pinned to a subscription tier. Its
   independence claim is unchanged: fresh context + read-only whitelist, no
   cross-model independence claimed.
2. The three text Flash roles keep an explicit Flash pin (the cost tier is the
   design), but the **shape rule** (provider-qualified id ending in
   `/GLM-5.3-Flash`) is the contract; the provider segment is an owner-maintained
   default. Current default rebound to
   `account:bigmodel-individual-coding-plan/GLM-5.3-Flash` (host-configured
   today; same series as the visual pair's 2026-10-14 ruling, which stays
   untouched). No exact-literal anchoring survives anywhere — validator and
   tests anchor shape only, so future rebinds are a one-file edit.
3. The provider-qualification shape check widens from `^account:` to any
   `<provider>/<model>` compound id, consistent with the 2026-10-19 owner
   ruling that relaxed the `account:`-only admission (bare ids remain
   rejected).
4. New **channel discipline** doctrine: CreateWorkflow stays reserved for
   user-requested workflow runs (host rule); a model-choice need, a missing
   role, or a binding whose provider is absent from the host list is never by
   itself a reason to reroute direct-dispatch work — or a fresh-context review
   — into a workflow submission. The sanctioned response is: stop that route,
   surface to the owner, rebind (procedure in troubleshooting §4).

## 4. Change set

| Face | Change |
| --- | --- |
| `agents/glm-reviewer.md` | drop the `model:` line; inherit comment (conductor-impl form) |
| `agents/conductor-{scout,runner,impl-flash}.md` | pin → `account:bigmodel-individual-coding-plan/GLM-5.3-Flash` |
| `scripts/validate_plugin.py` | check 2: glm-reviewer joins the no-model-key inherit group; Flash roles shape-only; RE widened and renamed (`PINNED_FLASH_MODEL_RE`); module docstring; 16p token list: retire the three now-contradicted bans, add the rotted-literal class (`account:bigmodel-start-plan`, `value-identical`, 同值 phrasings) |
| `tests/test_agent_model_binding.py` | reviewer test flips to no-model-key; trio test flips to shape-only; literal constants removed |
| `tests/test_model_selection_rebaseline.py` | static-binding whitelist: six → five roles; docstrings |
| `docs/architecture.md`, `docs/core-concepts.md`, `docs/troubleshooting.md` | binding paragraphs rewritten; troubleshooting §4 gains the rebind procedure + channel discipline + the 2026-10-26 soft-routing observation |
| `plugins/.../skills/orchestration/SKILL.md`, `references/operations.md`, `references/role-contracts.md` | reviewer line, preflight bullet, contract Model bullets rewritten to defaults + rebind + channel discipline |
| `README.md` / `README.zh-CN.md` | Text Reviewer model-table row |

Not changed: the visual pair's bindings and their fail-closed contract
(2026-10-14 ruling), the workflow-side `workflow-model-select` gate, journal
vocabulary, CLI surfaces, test method counts (full-suite count stays 1102).

## 5. Verification

- `python scripts/validate_plugin.py` → 16/16 (check 2 and 16p reshaped in the
  same change).
- Full local suite → 1102/1102 (`PYTHONUTF8=1`, stdlib only).
- glm-reviewer independent final review (ship / fix-first / rethink) on the
  completed diff before commit.

## 6. Post-hotfix obligations (owner side)

- Version bump / CHANGELOG entry / tag / Release / plugin-cache refresh at the
  next release ceremony (cache refresh is what makes new sessions see the
  rebound agent files).
- After the cache refresh, future sessions get the restored inherit reviewer
  and the rebound Flash defaults; until then, live 2.8.0-cache sessions keep
  silently routing the four roles (recorded here as known interim state).
