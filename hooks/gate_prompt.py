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


def _version_key(value: str) -> tuple[int, int, int]:
    """Numeric compare for version strings; anything unparsable sorts as 0."""
    out = []
    for chunk in str(value).split("."):
        digits = "".join(c for c in chunk if c.isdigit())
        out.append(int(digits) if digits else 0)
    return tuple((out + [0, 0, 0])[:3])  # type: ignore[return-value]


def stale_setup_notice() -> str:
    """One line when the installed plugin is newer than what setup recorded.

    setup.sh copies goals.py and the packs to ~/.fablize/lib and writes the
    version it copied. A plugin upgrade replaces the code but cannot rewrite
    CLAUDE.md or refresh those copies, so without this the block silently keeps
    pointing at last release's assets. Any error here means no notice at all.

    The notice names the command instead of `/fablize:setup`, and that is the
    whole point. `/fablize:setup` carries ${CLAUDE_PLUGIN_ROOT} expanded when a
    SESSION LOADED it, so in a session opened before the upgrade it still runs
    the previous version's setup.sh — which is on disk, works, and records the
    old version again. Telling the user to re-run the command that caused the
    staleness is how this fired twice in one day on 2026-08-26.

    2.1.7 added a downgrade guard to setup.sh, but a guard shipped in the new
    version cannot run inside an old copy: every pre-2.1.7 root already on disk
    still overwrites freely.

    Hooks are pinned the same way. ${CLAUDE_PLUGIN_ROOT} in hooks.json is
    resolved when the hooks are REGISTERED, so a session opened before an
    upgrade keeps running this file from the old root for its whole life. That
    is why the comparison is two-way. `recorded` newer than `running` does not
    mean a downgrade happened; it means THIS session is the old one, and running
    setup here is what would cause the downgrade. Reported live on 2026-08-26 by
    a second session showing `(2.1.7 -> 2.1.3)` — a message the one-way wording
    described as an upgrade.
    """
    root = Path(__file__).resolve().parent.parent
    try:
        running = json.loads(
            (root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
        )["version"]
        recorded_state = json.loads(
            Path(os.path.expanduser("~/.fablize/progress.json")).read_text(encoding="utf-8")
        )
        recorded = recorded_state.get("version")
    except Exception:  # noqa: BLE001 — never let this block a prompt
        return ""
    if not recorded or recorded == running:
        return ""
    scope = recorded_state.get("scope") if recorded_state.get("scope") in ("global", "local") else "global"
    if _version_key(running) < _version_key(recorded):
        return (
            f"fablize: this session is running plugin {running}, older than the {recorded} that "
            f"is set up. Its hooks were pinned when the session started, so they stay on "
            f"{running}. Do NOT run setup or `/fablize:setup` here — it would overwrite the "
            f"{recorded} assets and record {running}. Start a new session, or run it from the "
            f"one that installed {recorded}."
        )
    return (
        f"fablize was upgraded ({recorded} -> {running}) but setup has not re-run, so the "
        f"CLAUDE.md block still points at the previous release's copies. Run this exact "
        f"command: bash {root.as_posix()}/setup/setup.sh {scope} — "
        f"`/fablize:setup` had its plugin path expanded when this session loaded it, so in a "
        f"session started before the upgrade it runs the OLD setup.sh and records {recorded} "
        f"again."
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
