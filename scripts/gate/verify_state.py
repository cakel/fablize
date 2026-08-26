#!/usr/bin/env python3
"""Stop-time decision for the fablize observation gate.

The decision is made purely from observed ledger state — never from the
assistant's claim text — so it is language-agnostic. Deep-only: it blocks a
DEEP, non-docs task that changed files but has no OBSERVED successful
verification. This catches "I changed code and tests pass" when no test was
ever run, or ran and failed. Normal mode no longer hard-blocks (deep-only —
measured noise with no proven benefit). Complementary to finish-the-work.sh
(which catches promise-no-act).
"""

from __future__ import annotations

from typing import Any


MAX_STOP_BLOCKS = 2


def has_successful_verification(ledger: dict[str, Any]) -> bool:
    """A verification ran and nothing observed says it failed.

    `success` is tri-state, and the third state is the common one. True is an
    observed pass; False is an observed failure; None means the command ran, was
    recognised as a verification, and its output carried no status-shaped signal
    for `exit_success()` to read. Measured across 3,347 real ledgers: of 136
    recorded verifications, 97 are None, 25 True, 14 False. **Seventy-one percent.**

    Requiring `is True` therefore told the model it had skipped verification for
    most checks that actually ran and passed — `check_doc: ERROR 0 / WARN 0` is
    matched by neither FAILURE_RE nor SUCCESS_RE, and the hook payload usually
    carries no exit code to short-circuit the text inference.

    That is the same mistake as the false positives fixed in 2.1.5: asserting
    something about evidence never seen. None is not proof of a pass, but it is
    not grounds to claim nothing was verified either. An observed failure still
    does not satisfy the gate — a turn whose only check returned False blocks,
    and the failure is disclosed separately through the ledger's `failures`.
    """
    results = ledger.get("verification_results", [])
    return any(result.get("success") is not False for result in results)


def docs_only(ledger: dict[str, Any]) -> bool:
    kinds = set(ledger.get("change_kinds", []))
    return bool(ledger.get("changed_files_seen")) and bool(kinds) and kinds <= {"docs"}


def should_block_stop(ledger: dict[str, Any]) -> tuple[bool, str]:
    mode = ledger.get("task_mode") or "quick"
    stop_blocks = int(ledger.get("stop_blocks") or 0)
    changed = bool(ledger.get("changed_files_seen"))
    verified = has_successful_verification(ledger)

    if stop_blocks >= MAX_STOP_BLOCKS:
        return False, ""
    if mode == "quick":
        return False, ""
    if docs_only(ledger):
        return False, ""
    # Block only when a DEEP turn actually changed something and ran no observed
    # verification. A deep turn that changed nothing (analysis/planning/reading)
    # has nothing to verify, so it is NOT blocked — the old "add observable proof"
    # nag was a false-positive on ~1/3 of deep firings (docs/MEASUREMENT_PROTOCOL.md).
    if mode == "deep" and changed and not verified:
        return True, "fablize gate: run the narrowest verification command for the changed behavior before final response, or record why none applies."
    # deep-only: normal mode no longer hard-blocks; it keeps an advisory prompt nudge.
    return False, ""


def warning_after_max_blocks(ledger: dict[str, Any]) -> str:
    if int(ledger.get("stop_blocks") or 0) >= MAX_STOP_BLOCKS and not has_successful_verification(ledger):
        return "fablize gate: verification evidence is still missing — include that gap in the final report."
    return ""
