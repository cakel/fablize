#!/usr/bin/env python3
"""detect_failure must fire on failed CALLS, not on content that mentions failure.

Every FALSE case below was observed firing the gate in a real session: the model
was told "do not report completion" after a successful edit, a successful grep,
and — twice — after reading the detector's own source. A gate that cries wolf on
its own source teaches the model to ignore it, so the misses cost less than the
noise. Exit non-zero on any mismatch.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "gate"))

from parse_tool_result import detect_failure  # noqa: E402


def bash(stdout, command="echo hi", **extra):
    return {"tool_name": "Bash", "tool_input": {"command": command},
            "tool_response": {"stdout": stdout, **extra}}


CASES = [
    # (should_flag, label, payload)
    # response_text() reads tool_response, and Edit/Write echo the file body
    # back in it, so the edited text itself reaches the detector.
    (False, "edit whose content mentions a traceback", {
        "tool_name": "Edit",
        "tool_input": {"file_path": "CHANGELOG.md", "old_string": "a", "new_string": "b"},
        "tool_response": {"filePath": "CHANGELOG.md", "structuredPatch": [],
                          "originalFile": "SyntaxError: unterminated string literal 로 죽었다"}}),
    (False, "write of a doc reporting 3 tests failed", {
        "tool_name": "Write",
        "tool_input": {"file_path": "notes.md"},
        "tool_response": {"filePath": "notes.md",
                          "content": "지난주 배포에서 3 tests failed 로 롤백했다"}}),
    (False, "grep hitting the detector's own pattern source",
     bash("FAILURE_RE = re.compile(r'(?i)(command not found|tests? failed|build failed)')",
          command="grep -n FAILURE_RE parse_tool_result.py")),
    (False, "git log whose commit message quotes a traceback",
     bash("fix: SyntaxError: unterminated string literal 로 죽던 문제",
          command="git log --oneline -1")),
    # Known ceiling, pinned so it is a decision and not a surprise: redact()
    # flattens newlines inside response_text, so "failure word earlier, clean
    # summary later" is indistinguishable from a real failure. Preserving
    # newlines through response_text is the upgrade path.
    (True, "CEILING: failure word earlier, clean summary later",
     bash("checking build failed cases...\n991 passed, 39 deselected",
          command="pytest tests/ -q")),
    (False, "explicit exit_code 0 outranks any text", bash("2 failed", exit_code=0)),

    # Real failures must still be caught.
    (True, "explicit non-zero exit code", bash("boom", exit_code=1)),
    (True, "pytest summary at the tail",
     bash("...\n1 failed, 990 passed in 13.00s", command="pytest tests/ -q")),
    (True, "missing binary",
     bash("bash: nosuchcmd: command not found", command="npm test")),
    (True, "python traceback at the tail",
     bash("Traceback (most recent call last):\n  File \"x.py\", line 1\nValueError",
          command="pytest tests/ -q")),
    # A fetched CI log saying the job failed IS worth flagging — the run really
    # did fail, and reporting completion over it is the mistake the gate exists
    # to prevent. `curl` is already in the verification vocabulary.
    (True, "curl of a CI trace reporting a failed job",
     bash("remote: ERROR: Job failed: exit status 1", command="curl -s .../trace")),
    (True, "explicit success=False outranks text", {
        "tool_name": "Edit", "tool_input": {"file_path": "x.py"},
        "tool_response": {"success": False}}),
]


def main():
    bad = 0
    for expected, label, payload in CASES:
        got = detect_failure(payload) is not None
        mark = "OK  " if got == expected else "FAIL"
        if got != expected:
            bad += 1
        print(f"{mark} flag={got!s:<5} want={expected!s:<5} {label}")
    print("-" * 78)
    if bad:
        print(f"RESULT: {bad}/{len(CASES)} mismatched.")
        return 1
    print(f"RESULT: all {len(CASES)} cases match. Content-only mentions no longer flag.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
