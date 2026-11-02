#!/usr/bin/env python3
"""fablize UserPromptSubmit router: when a task signal is detected, return the
matching pack discipline as extra context. Smallest matching pack only; overlap
only when the prompt is genuinely multi-category. Only verified packs are routed.

stdin: JSON {"prompt": ..., "session_id": ...}. stdout: plain-text context, only
when a signal matches. Always exits 0. Called by router.sh (Claude Code) and
imported by kiro.py (Kiro CLI), so it needs no shell of its own.

Signals come from classify_task.signals (DEBUG_RE / RENDER_RE), the lists the
task-mode classifier uses: the router used to miss "근본 원인 찾아서 고쳐줘" /
"figure out why" while the classifier, with its own list, called them quick.
Each matched signal is logged out-of-band (events.jsonl); the prompt text is not.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# This file's own location, not $CLAUDE_PLUGIN_ROOT: set by hand (Kiro users did)
# it can name an older plugin copy. as_posix() gives C:/... on Windows, which the
# model's Windows tools (Read, PowerShell) can open; Git Bash's /c/... they cannot.
ROOT = Path(__file__).resolve().parent.parent
PACKS = ROOT.as_posix() + "/packs"
sys.path.insert(0, str(ROOT / "scripts" / "gate"))
sys.path.insert(0, str(ROOT / "scripts" / "shadow"))

LINES = {
    "investigation": "[fablize:investigation] Debugging/root-cause signal — follow " + PACKS
    + "/investigation-protocol.txt: reproduce first, form 3+ competing hypotheses, gather evidence "
    "per hypothesis, trace the full causal chain, check every sibling caller of the defective code, "
    "prove the fix with a test that failed before it, and report the hypotheses you rejected.",
    "grounding": "[fablize:grounding] Render/executable artifact signal — follow " + PACKS
    + "/verification-grounding-pack.txt grounding loop: run it in the real renderer, observe the "
    "actual output, fix what the observation reveals, then re-run. A static check is not observation.",
}


def route(prompt: str, session_id: str = "") -> str:
    try:
        from classify_task import signals
        found = signals(prompt or "")
    except Exception:  # noqa: BLE001 — fail open
        return ""
    if found:
        try:
            from shadow_logger import log_event
            log_event(session_id or "", "router_signal",
                      {"signals": [{"pack": n, "matched": m[:40]} for n, m in found]})
        except Exception:  # noqa: BLE001
            pass
    names = {name for name, _ in found}
    return "\n".join(line for key, line in LINES.items() if key in names)


if __name__ == "__main__":
    try:
        data = json.loads(sys.stdin.buffer.read().decode("utf-8", "replace") or "{}")
        text = route(str(data.get("prompt") or ""), str(data.get("session_id") or "")) if isinstance(data, dict) else ""
        if text:
            sys.stdout.buffer.write((text + "\n").encode("utf-8"))
    except Exception:  # noqa: BLE001 — fail open
        pass
    raise SystemExit(0)
