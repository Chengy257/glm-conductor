#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""W0 Phase 0 probe support: create/cleanup a synthetic active Conductor task.

Used only by the W0 scheduled-turn probes (scripts/spikes/w0_phase0/, non-production).
The synthetic task exists solely to make Agent|Task Pre/PostToolUse hook behavior
observable (Layer B advisory injection, reviewer_invoked bookkeeping); it is
removed again within the same probe turn. Raw evidence stays under the
git-ignored .glm-conductor/spikes/w0-phase0/ tree.
"""
import pathlib
import shutil
import sys

REPO = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "plugins" / "glm-conductor"))

from runtime import state  # noqa: E402

TASK_ID = "w0-phase0-probe"
ROUTE = {
    "mode": "solo",
    "delegability": "low",
    "assurance": "standard",
    "executor": "main",
    "continuity": "foreground",
}


def create() -> None:
    st = state.new_task_state(
        TASK_ID,
        "W0 Phase0 hook-visibility probe (synthetic task, removed by probe cleanup)",
        ROUTE,
        ownership_files=["docs/reviews/W0_PROBE_OWNERSHIP_NOTE.md"],
        status="executing",
    )
    path = state.save_state(REPO, st)
    print("created:", path)


def cleanup() -> None:
    d = REPO / ".glm-conductor" / "tasks" / TASK_ID
    if d.exists():
        shutil.rmtree(d)
        print("cleaned:", d)
    else:
        print("no task dir (already clean)")


def evidence() -> None:
    base = REPO / ".glm-conductor"
    task_dir = base / "tasks" / TASK_ID
    print("task_state_exists:", (task_dir / "state.json").exists())
    events = task_dir / "events.jsonl"
    print("events_exists:", events.exists())
    if events.exists():
        print(events.read_text(encoding="utf-8"))
    for rel in ("scheduler/session_facts.json",):
        p = base / rel
        print(rel, "exists:", p.exists())


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "create":
        create()
    elif cmd == "cleanup":
        cleanup()
    elif cmd == "evidence":
        evidence()
    else:
        print("usage: probe_task.py create|cleanup|evidence")
        sys.exit(2)
