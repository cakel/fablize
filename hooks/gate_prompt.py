#!/usr/bin/env python3
"""fablize observation gate — UserPromptSubmit.

Classifies the new prompt's task mode and resets the per-prompt ledger so the
Stop gate judges only this turn's evidence. Fails open (emits {} on any error).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "gate"))

from ledger import add_unique, emit_json, read_stdin_json, update_ledger
from classify_task import classify_prompt, context_for_mode


def stale_setup_notice() -> str:
    """One line when the installed plugin is newer than what setup recorded.

    setup.sh copies goals.py and the packs to ~/.fablize/lib and writes the
    version it copied. A plugin upgrade replaces the code but cannot rewrite
    CLAUDE.md or refresh those copies, so without this the block silently keeps
    pointing at last release's assets. Any error here means no notice at all.
    """
    try:
        running = json.loads(
            (Path(__file__).resolve().parent.parent / ".claude-plugin" / "plugin.json")
            .read_text(encoding="utf-8")
        )["version"]
        recorded = json.loads(
            Path(os.path.expanduser("~/.fablize/progress.json")).read_text(encoding="utf-8")
        ).get("version")
    except Exception:  # noqa: BLE001 — never let this block a prompt
        return ""
    if not recorded or recorded == running:
        return ""
    return (
        f"fablize was upgraded ({recorded} -> {running}) but setup has not re-run, so the "
        "CLAUDE.md block still points at the previous release's copies. "
        "Re-run `/fablize:setup` to refresh them."
    )


def main() -> int:
    input_data = read_stdin_json()
    prompt = str(input_data.get("prompt") or input_data.get("user_prompt") or "")
    mode, risks = classify_prompt(prompt)

    def apply(ledger):
        ledger["task_mode"] = mode
        ledger["changed_files_seen"] = False
        ledger["change_kinds"] = []
        ledger["risk_flags"] = []
        ledger["verification_commands"] = []
        ledger["verification_results"] = []
        ledger["failures"] = []
        ledger["stop_blocks"] = 0
        add_unique(ledger, "risk_flags", risks)

    update_ledger(input_data, apply)

    context = context_for_mode(mode, risks)
    notice = stale_setup_notice()
    if notice:
        context = notice + "\n" + context

    emit_json(
        {
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": context,
            }
        }
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001 — fail open, never block on our own bug
        emit_json({"systemMessage": f"fablize gate prompt hook failed open: {exc}"})
        raise SystemExit(0)
