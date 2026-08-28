# ZCode Feature Request 文稿（B0.2，待提交）

> 目标仓库：ZCode 官方反馈渠道（zcode.z.ai/docs 或 GitHub issue，以官方指引为准）
> 提交人：用户；状态：文稿就绪，未提交
> 背景：glm-conductor v2 Enforcement 层（Ownership/Permission Gate）依赖

---

**标题：**

Subagent tool calls should optionally fire plugin hooks (per-subagent hook runner)

**正文：**

## Summary

Plugin hooks (`PreToolUse` / `PermissionRequest` / `PostToolUse` / `Stop`) fire only for main-session tool calls. Tool calls made inside subagents (Agent/Task tool → subagent child sessions) do not pass through any hook runner, so a plugin cannot enforce per-write policies on subagent execution.

## Observed behavior (ZCode 3.9.2, Windows)

1. A plugin `PreToolUse` hook with matcher `Bash|Write|Edit` fires for every main-session tool call (verified with a working python hook; payload schema captured).
2. The same hook fires **zero** times during a subagent's flight, while the subagent performs Write/Edit/Bash calls that match the matcher (verified with a foreground probe agent: 8/8 calls, 0 hook invocations; also consistent with `~/.zcode/cli/log/zcode-<date>.jsonl` `core.hooks` events).
3. Code-level: the subagent executor constructs child sessions without a `hookRunner` (e.g. `runExploreAgent` → `new Ah(...)` with a config that carries no `hooks` key), so the shared emission point (`runPreToolUseHooks` → `e.hookRunner ? run(...) : no-op`) skips silently.

## Why it matters

Orchestration plugins (e.g. GLM Conductor, sol-advisor-style selective-routing setups) delegate bounded implementation to subagents and need deterministic guardrails:

- block writes to files outside a declared ownership set;
- deny destructive shell commands regardless of which agent issues them;
- audit all tool activity per task.

Today these can only be enforced prompt-level for subagents, which is exactly the gap between "policy" and "guarantee".

## Proposal

An **opt-in, backward-compatible** mechanism, e.g. either of:

1. **Agent-definition level**: allow agent frontmatter to declare hook inheritance, e.g. `hooks: inherit` or `hooks: [PreToolUse]` in `agents/*.md`; child sessions then receive a hook runner scoped to the declared events.
2. **Hook-manifest level**: allow plugin `hooks.json` entries to declare scope, e.g. `"scope": "main+subagents"` (default `main`), so existing plugins are unaffected.

Suggested payload addition for subagent-context invocations: include `agent_type` (and/or the agent definition name) so hooks can apply role-specific policy — the field already exists in the stdin schema but is currently omitted (undefined) in main-session payloads.

## Notes

- Default-off keeps current plugin behavior identical (no plugin breaks on upgrade).
- Hooks-in-subagents must still respect the documented hook rules (fast, local, no model calls).
- Happy to provide the full verification report (payload captures, log forensics, code references) if useful.

---
（附：本机验证证据见 `glm-conductor-v2-phase0-runtime-verification.md` §2.1 与本轮 B0.1 复测记录。）
