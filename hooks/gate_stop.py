#!/usr/bin/env python3
"""fablize observation gate — Stop.

Blocks completion when this turn's observed ledger shows a non-quick, non-docs
task that changed files but has no observed successful verification. Capped at
MAX_STOP_BLOCKS reminders, then allows with a warning. Fails open.

Runs alongside finish-the-work.sh (promise-no-act guard): the two cover
different failure modes — claim-without-observation vs intent-without-action.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent / "scripts" / "gate"))
sys.path.insert(0, str(_HERE.parent / "scripts" / "shadow"))

from ledger import emit_json, load_ledger, read_stdin_json, update_ledger
from verify_state import MAX_STOP_BLOCKS, should_block_stop, warning_after_max_blocks


def _holdout_suppresses(input_data: dict) -> bool:
    """M3 holdout: env-gated, opt-in. When FABLIZE_HOLDOUT=1, sessions in the
    'off' arm skip the gate (pure baseline for measuring whether the gate
    helps). Default OFF -> gate behaviour is identical, so the 16/16 regression
    (which tests should_block_stop directly) is unaffected. The arm is computed
    out-of-band and is never shown to the model."""
    if os.environ.get("FABLIZE_HOLDOUT") != "1":
        return False
    try:
        from shadow_logger import holdout_arm
        return holdout_arm(input_data.get("session_id") or "") == "off"
    except Exception:
        return False


def _log_holdout(input_data: dict, reason: str) -> None:
    """Record the holdout suppression out-of-band (events.jsonl). Never emits to
    the model and never raises into the gate path."""
    try:
        from shadow_logger import append_event, make_event
        append_event(make_event(input_data.get("session_id") or "", "holdout_suppress",
                                {"would_block_reason": reason}))
    except Exception:
        pass


def _log_decision(input_data: dict, ledger: dict, event: str) -> None:
    """Out-of-band record of each block / cap warning (events.jsonl), so whether
    the gate should stay blocking can be judged from data (docs/DECISIONS.md D2)."""
    try:
        from shadow_logger import log_event
        log_event(input_data.get("session_id") or "", event, {
            "mode": ledger.get("task_mode"),
            "change_kinds": list(ledger.get("change_kinds") or []),
            "stop_blocks": int(ledger.get("stop_blocks") or 0),
            "verifications": len(ledger.get("verification_results") or []),
            "unverified_change": bool(ledger.get("unverified_change")),
        })
    except Exception:
        pass


def main() -> int:
    input_data = read_stdin_json()
    # No stop_hook_active early return. That flag is set after ANY Stop hook
    # blocks — finish-the-work.sh included — so treating it as "this gate already
    # blocked" let one promise-no-act reminder waive the evidence gate for the
    # rest of the turn, made MAX_STOP_BLOCKS unreachable, and skipped the holdout
    # check. The loop bound is the ledger's own stop_blocks counter, which
    # gate_prompt resets per prompt and which caps at MAX_STOP_BLOCKS.
    # Background work still in flight (a test run, a subagent): the session is
    # paused, not done, and will be woken with the result. Blocking now told the
    # model to "run the verification" it was already waiting on.
    if input_data.get("background_tasks"):
        emit_json({})
        return 0
    ledger = load_ledger(input_data)
    block, reason = should_block_stop(ledger)
    if block:
        # M3 holdout (env-gated, default off): 'off' arm sessions skip the gate
        # so we can measure the gate's effect against a pure baseline.
        if _holdout_suppresses(input_data):
            _log_holdout(input_data, reason)
            emit_json({})
            return 0
        # Under the ledger lock, so a PostToolUse finishing at the same moment
        # cannot be overwritten by this increment.
        def bump(current):
            current["stop_blocks"] = int(current.get("stop_blocks") or 0) + 1
        update_ledger(input_data, bump)
        _log_decision(input_data, ledger, "stop_block")
        emit_json({"decision": "block", "reason": reason})
        return 0

    # systemMessage only. Stop's additionalContext is "feedback delivered to the
    # model; the conversation continues" (Claude Code's schema) — a third block
    # by another name, past the cap. The model is done; the note is for the user.
    warning = warning_after_max_blocks(ledger)
    # The holdout off arm is a pure baseline: no gate output on this path either.
    if warning and _holdout_suppresses(input_data):
        _log_holdout(input_data, warning)
        emit_json({})
        return 0
    if warning:
        capped = int(ledger.get("stop_blocks") or 0) >= MAX_STOP_BLOCKS
        _log_decision(input_data, ledger, "stop_cap_warning" if capped else "stop_user_forbade")
    emit_json({"systemMessage": warning} if warning else {})
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001 — fail open
        emit_json({"systemMessage": f"fablize gate stop hook failed open: {exc}"})
        raise SystemExit(0)
