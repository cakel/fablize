#!/usr/bin/env python3
"""The two turns the Stop gate actually blocked must stop blocking.

Both were false positives, observed in one session on 2026-08-26. Neither turn
changed a line of code; both touched Markdown and a scratch directory only.

  A. A Korean document was rewritten. The file moved with `cp`, so changed_kinds
     reported ["other"] instead of ["docs"], and the check that ran afterwards
     — `python tools/check_doc.py --strict`, ERROR 0 / WARN 0 — was not a shape
     is_verification_command recognised. Deep + changed + unverified -> block.

  B. A GitLab merge request was read over the API. Nothing but `mkdir -p` for a
     scratch directory and `curl -o` into it, yet `mkdir` alone set
     changed_files_seen. The curl was recognised as a verification but did not
     count as a successful one — see the CEILING checks below for why a run can
     succeed and still be recorded as unknown.

The gate is capped at MAX_STOP_BLOCKS = 2, so it relented on the third try — but
by then it had twice told the model it had skipped verification it had in fact
run. A gate that cries wolf teaches the model to ignore it, which is the cost
test_gate_false_positive.py exists to keep down.

This replays both turns through the real ledger and asserts the verdict flips.
It also pins the PostToolUse matcher, since a fix in parse_tool_result.py is
inert for any tool the hook never receives. Exit non-zero on any mismatch.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts" / "gate"))

from ledger import add_unique, load_ledger, save_ledger  # noqa: E402
from parse_tool_result import changed_kinds, verification_record  # noqa: E402
from verify_state import should_block_stop  # noqa: E402

CHECKS = []


def check(label, got, want):
    CHECKS.append((label, got, want))


def bash(command, exit_code=0, stdout=""):
    return {
        "session_id": "S",
        "cwd": "/w",
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "tool_response": {"stdout": stdout, "exit_code": exit_code},
    }


def replay(calls):
    """Drive a whole deep turn through the real ledger and return the verdict."""
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["FABLIZE_DATA"] = tmp
        payload = {"session_id": "S", "cwd": "/w"}
        led = load_ledger(payload)
        led["task_mode"] = "deep"
        for call in calls:
            kinds = changed_kinds(call)
            if kinds:
                led["changed_files_seen"] = True
                add_unique(led, "change_kinds", kinds)
            record = verification_record(call)
            if record:
                led["verification_results"].append(record)
        save_ledger(payload, led)
        blocked, _ = should_block_stop(led)
        return blocked, sorted(led["change_kinds"]), led["verification_results"]


# --- Turn A: rewrite a Markdown file, then run the repository's own checker. ---
TURN_A = [
    bash("mkdir -p _workspace/2026-08-26-001"),
    bash("cp tasks/v0_54/SPEC.md _workspace/2026-08-26-001/01_input.txt"),
    bash("cp _workspace/2026-08-26-001/body.md tasks/v0_54/SPEC.md"),
    bash("python tools/check_doc.py --strict", stdout="check_doc: ERROR 0 / WARN 0 [strict]"),
]
blocked_a, kinds_a, checks_a = replay(TURN_A)
check("A blocked", blocked_a, False)
check("A kinds", kinds_a, ["docs"])
check("A verifications", len(checks_a), 1)
check("A verification succeeded", checks_a[0]["success"] if checks_a else None, True)

# --- Turn B: read an API into a scratch directory. Nothing in the repo changed. ---
TURN_B = [
    bash('S="/tmp/scratch" && mkdir -p "$S"'),
    bash('curl -s -H "PRIVATE-TOKEN: x" http://host/api/mr/72 -o /tmp/scratch/mr72.json'),
]
blocked_b, kinds_b, _ = replay(TURN_B)
check("B blocked", blocked_b, False)
check("B kinds", kinds_b, [])

# --- The gate must still fire on the case it exists for: code changed, nothing run. ---
TURN_C = [bash("cp patch.py src/app.py"), bash("git status --short")]
blocked_c, kinds_c, _ = replay(TURN_C)
check("C still blocks (code, unverified)", blocked_c, True)
check("C kinds", kinds_c, ["code"])

# ...and must not be satisfied by a check that failed.
TURN_D = [
    bash("cp patch.py src/app.py"),
    bash("python tools/check_doc.py", exit_code=1, stdout="check_doc: ERROR 3"),
]
blocked_d, _, checks_d = replay(TURN_D)
check("D failed check does not satisfy", blocked_d, True)
check("D recorded the failure", checks_d[0]["success"] if checks_d else None, False)

# --- Known ceiling: a run that succeeded but said nothing status-shaped is recorded
# as unknown, and has_successful_verification() accepts only True. When the payload
# carries an explicit exit code this never arises; when it does not, exit_success()
# falls back to text, and output like a JSON body or an HTTP page matches neither
# FAILURE_RE nor SUCCESS_RE. The check ran and passed, and the gate cannot tell.
CURL = "curl -s http://host/api/mr/72 -o out.json"
BODY = "HTTP=404\n<html><title>404 Not Found</title>"


def no_status(command, stdout):
    return {"tool_name": "Bash", "tool_input": {"command": command},
            "tool_response": {"stdout": stdout}}


# With an exit code present the body is not misread — the 404 page does not poison it.
check("explicit exit 0 wins over body text", verification_record(bash(CURL, stdout=BODY))["success"], True)
check("explicit exit 1 is a failure", verification_record(bash(CURL, 1, BODY))["success"], False)
# Without one, both a failed fetch and a clean one land on None, which does not count.
check("CEILING: no exit code, 404 body -> unknown",
      verification_record(no_status(CURL, BODY))["success"], None)
check("CEILING: no exit code, good body -> unknown",
      verification_record(no_status(CURL, '{"iid":72}'))["success"], None)

# --- A fix in parse_tool_result.py is inert for a tool the hook never receives. ---
matcher = json.loads((REPO / "hooks" / "hooks.json").read_text(encoding="utf-8"))
post = [h for h in matcher["hooks"]["PostToolUse"]]
check("PostToolUse hook count", len(post), 1)
for tool in ("Bash", "PowerShell", "Edit", "Write", "NotebookEdit", "MultiEdit"):
    check(f"matcher covers {tool}", tool in post[0]["matcher"], True)
# BashOutput reads a call that already happened; counting it double-records.
check("matcher excludes BashOutput", "BashOutput" in post[0]["matcher"], False)


def main():
    bad = 0
    for label, got, want in CHECKS:
        ok = got == want
        if not ok:
            bad += 1
        print(f"{'OK  ' if ok else 'FAIL'} {str(got):<8} want={str(want):<8} {label}")
    print("-" * 78)
    if bad:
        print(f"RESULT: {bad}/{len(CHECKS)} mismatched.")
        return 1
    print(f"RESULT: all {len(CHECKS)} checks match. Both observed blocks are gone; C/D still fire.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
