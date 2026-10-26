#!/usr/bin/env python3
"""fablize observation gate — UserPromptSubmit.

Classifies the new prompt's task mode and resets the per-prompt ledger so the
Stop gate judges only this turn's evidence. Fails open (emits {} on any error).
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "gate"))

from ledger import add_unique, emit_json, load_ledger, read_stdin_json, update_ledger
from classify_task import (NO_EDIT_RE, QUICK_RE, _ASKS_CHECK_RE, classify_prompt, context_for_mode,
                           forbids_verification)


def _version_key(value: str) -> tuple[int, ...]:
    """Every number in the version, in order: `2.1.1+cakel.4` -> (2, 1, 1, 4).

    This used to keep only the first three, so fork builds `2.1.1+cakel.3` and
    `+cakel.4` compared equal. An old session then got the upgrade notice instead
    of "do NOT run setup", and setup.sh's twin of this key let it roll the
    recorded version back. Observed 2026-10-23. Keep in step with setup.sh.
    """
    text = str(value)
    # This fork shipped 2.1.2 .. 2.1.8 and then renumbered to `2.1.1+cakel.N`.
    # By plain digits those legacy releases outrank every cakel build, so anyone
    # set up on one was refused setup forever and told their session was the old
    # one. Rank them just below cakel.1. Keep in step with setup.sh.
    if re.fullmatch(r"2\.1\.[2-8]", text):
        return (2, 1, 1, 0, int(text.rsplit(".", 1)[1]))
    return tuple(int(n) for n in re.findall(r"\d+", text))


# A bare go-ahead: short, and nothing but continuation words. Any other prompt
# without task keywords ("Add a docstring to parse_token()") is a NEW request and
# is classified on its own — inheriting deep for those kept a session in deep
# mode until the user happened to say "briefly".
CONTINUATION_RE = re.compile(
    r"(?i)^(?:(?:ok(?:ay)?|yes|yep|yeah|sure|go|go on|go ahead|proceed|continue|keep going|"
    r"carry on|do it|next|please|lgtm|sounds good|let'?s|let us|with the next (?:story|step|one|task)|"
    r"the next (?:story|step|one|task)|응|네|예|ㅇㅇ|좋아요?|그래요?|진행|진행해|진행하세요|계속|계속해|"
    r"이어서|다음|다음 단계|해|해줘|줘|주세요|해 주세요|해주세요|고고|ㄱㄱ|그렇게|부탁해요?|부탁합니다)"
    r"[\s,.!~]*){1,6}$"
)
# Approvals and picks seen in real follow-ups ("승인합니다", "네, 진행합니다",
# "권고대로 합니다", "Approved.", "go ahead with option 2", "A"). Matched only on a
# SHORT prompt that classified as plain quick, so a real new request still gets
# its own mode. Measured: the word list alone kept the mode for 2 of 4,826 real
# prompts.
APPROVAL_RE = re.compile(
    r"(?i)\b(?:approved?|approve it|proceed|go ahead|continue|carry on|keep going|looks good|lgtm|"
    r"sounds good|do it|ship it|yes|yep|ok(?:ay)?|sure|go on|let'?s go)\b"
    r"|승인|진행|계속|이어서|권고|권장|추천대로|제안대로|그대로\s?(?:해|가|진행|둡)|좋습니다|좋아요"
    r"|적용|반영|다음\s?단계|갑시다|합시다|^그렇게"
    r"|^(?:네|예|응|ㅇㅇ)(?=$|[\s,.!])"
)
_PICK_RE = re.compile(r"(?i)^\s*(?:option\s*)?[A-Za-z0-9]{1,2}\s*[.)]?\s*$")


def is_continuation(prompt: str) -> bool:
    text = (prompt or "").strip()
    if not text or len(text) > 60:
        return False
    return bool(CONTINUATION_RE.match(text) or APPROVAL_RE.search(text) or _PICK_RE.match(text))


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
        # expanduser first, then bash's $HOME: setup.sh writes under $HOME/.fablize, and
        # on Windows Python's expanduser ignores HOME for USERPROFILE.
        candidates = [Path(os.path.expanduser("~/.fablize/progress.json"))]
        if os.environ.get("HOME"):
            candidates.append(Path(os.environ["HOME"]) / ".fablize" / "progress.json")
        progress = next((c for c in candidates if c.exists()), candidates[0])
        recorded_state = json.loads(progress.read_text(encoding="utf-8"))
        recorded = recorded_state.get("version")
    except Exception:  # noqa: BLE001 — never let this block a prompt
        return ""
    if not recorded or recorded == running:
        return ""
    scope = recorded_state.get("scope") if recorded_state.get("scope") in ("global", "local") else "global"
    command = f'bash "{root.as_posix()}/setup/setup.sh" {scope}'
    if scope == "local":
        # Local setup writes ./CLAUDE.md, so running it from whatever project the
        # user is in now would plant a second block there and leave the real one
        # stale. setup.sh records the project since cakel.7; older records lack it.
        project = recorded_state.get("project")
        command = (f'cd "{Path(project).as_posix()}" && {command}' if project
                   else f"{command} (from the project where fablize was set up)")
    if _version_key(running) < _version_key(recorded):
        return (
            f"fablize: this session is running plugin {running}, older than the {recorded} that "
            f"is set up. Its hooks were pinned when the session started, so they stay on "
            f"{running}. Do NOT run setup or `/fablize:setup` here — it would overwrite the "
            f"{recorded} assets and record {running}. Start a new session, or run it from the "
            f"one that installed {recorded}."
        )
    # Not an order. This fires on EVERY prompt until setup re-runs, and a model
    # that obeys it rewrites the user's CLAUDE.md in the middle of an unrelated
    # task — the exact "confirm before hard-to-reverse actions" rule the block
    # itself injects. Tell the user once and let them decide.
    return (
        f"fablize was upgraded ({recorded} -> {running}) but setup has not re-run, so the "
        f"CLAUDE.md block still points at the previous release's copies. Mention this to the "
        f"user and only if they agree run: {command} — it rewrites CLAUDE.md (with a backup). "
        f"Do not use `/fablize:setup` for this: its plugin path was expanded when this "
        f"session loaded it, so in a session started before the upgrade it runs the OLD "
        f"setup.sh and records {recorded} again."
    )


# Machine-injected turns (task notifications, auto-continuation, peer messages,
# wakeups) continue the CURRENT task. Treated as new user prompts they reset the
# ledger and reclassified a notification's text — usually to quick — so a deep
# turn's change made before a background result arrived was never gated.
MACHINE_SOURCES = {"system", "loop_wakeup", "schedule_wakeup", "poll_event"}


def main() -> int:
    input_data = read_stdin_json()
    prompt = str(input_data.get("prompt") or input_data.get("user_prompt") or "")
    if str(input_data.get("source") or "") in MACHINE_SOURCES:
        ledger = load_ledger(input_data)
        mode = ledger.get("task_mode") or "quick"
        emit_json({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                          "additionalContext": context_for_mode(mode, list(ledger.get("risk_flags") or []),
                                                                                bool(ledger.get("verify_forbidden")))}})
        return 0
    mode, risks = classify_prompt(prompt)
    forbidden = forbids_verification(prompt)
    notice = stale_setup_notice()
    show_notice = False

    def apply(ledger):
        nonlocal show_notice, mode, risks, forbidden
        # "continue", "yes go ahead", "응 진행해" carry no task words, so they fell
        # through to the quick default — and a deep task's next turn could edit
        # code and stop unverified. Only an EXPLICIT quick ask ("briefly",
        # "설명만") drops the mode; a bare follow-up keeps the previous one.
        previous = ledger.get("task_mode")
        # "네, 설명만 해주세요" / "ok just explain, no edits" carry an approval word
        # AND an explicit quick ask; the explicit ask wins.
        explicit_quick = bool(QUICK_RE.search(prompt) or NO_EDIT_RE.search(prompt))
        # An approval that names the action ("ok fix it", "네, 수정해주세요") reads as
        # normal on its own; after a deep turn it is still that deep task.
        if not explicit_quick and is_continuation(prompt) and (
                (previous == "deep" and mode in ("quick", "normal")) or (previous == "normal" and mode == "quick")):
            mode = previous
            risks = list(dict.fromkeys(list(ledger.get("risk_flags") or []) + risks))
        # Once per session+cwd, tracked in the ledger. Asking the model to "say it
        # once" does not work: nothing it remembers survives compaction, so the
        # same notice was re-injected — and re-acted on — every prompt.
        if notice and ledger.get("notice_shown") != notice:
            ledger["notice_shown"] = notice
            show_notice = True
        ledger["task_mode"] = mode
        ledger["changed_files_seen"] = False
        ledger["change_kinds"] = []
        ledger["risk_flags"] = []
        ledger["verification_commands"] = []
        ledger["verification_results"] = []
        ledger["failures"] = []
        ledger["stop_blocks"] = 0
        ledger["unverified_change"] = False
        # "ok, go on" after "don't run the tests" is still under that prohibition;
        # a follow-up that asks for a check ("now run the tests") lifts it.
        if not forbidden and is_continuation(prompt) and not _ASKS_CHECK_RE.search(prompt):
            forbidden = bool(ledger.get("verify_forbidden"))
        ledger["verify_forbidden"] = forbidden
        add_unique(ledger, "risk_flags", risks)

    update_ledger(input_data, apply)

    context = context_for_mode(mode, risks, forbidden)
    if show_notice:
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
