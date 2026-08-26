#!/usr/bin/env python3
"""A suite or a checker run as a plain script must count as verification.

`python tests/test_x.py`, exiting non-zero on failure, matches neither
VERIFY_TOOL_RE (no pytest/unittest token) nor VERIFY_VERBS (the command word is
`python`). Observed consequence: a deep turn that ran its tests six times still
recorded zero verifications, so the Stop gate blocked it for "no observed
verification" — twice in one session. fablize's own suite is that shape.

A project's own checker is the same miss one step over. `python tools/check_doc.py
--strict` reported ERROR 0 / WARN 0 and was still not counted, because `check` sat
in an argument and VERIFY_VERBS only reads command position. That block was
observed too, on a turn whose only change was one Markdown file.

The NEGATIVE cases are the reason this is matched in command position rather than
by searching the whole command: a filename appearing in a grep pattern, a `cat`,
or an `rm` must not be read as a test run. Exit non-zero on any mismatch.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "gate"))

from parse_tool_result import (  # noqa: E402
    is_verification_command,
    runs_script_test,
    verification_record,
)

# Running a script-based suite — the miss this covers.
POSITIVE = [
    "python tests/test_gate.py",
    "python3 tests/test_gate.py",
    "PYTHONIOENCODING=utf-8 python src/test_idp_api.py",
    "PYTHONDONTWRITEBYTECODE=1 PYTHONIOENCODING=utf-8 python -B src/test_idp_api.py 2>&1 | tail -1",
    "cd repo && python src/test_x.py",
    "python src/idp_api_test.py",          # the x_test.py convention
    "env python tests/test_gate.py",
    "/usr/bin/python3 tests/test_gate.py",
    # A project's own checker: runs a real check, exits non-zero when it fails.
    "python tools/check_doc.py --strict",
    "python tools/check_doc.py --strict 2>&1 | tail -1",
    "python scripts/verify_gates.py --before a.txt --after b.md",
    "python tools/lint_diagram.py",
    "python tools/validate_manifest.py",
    "python src/schema_check.py",
    "python src/config-lint.py",           # the dash spelling of the same convention
    "cd repo && python3 tools/check_doc.py",
]

# A test filename that is being read, moved, matched or deleted — not run.
NEGATIVE = [
    'grep -rn "python src/test_idp_api.py" docs/',
    "cat src/test_idp_api.py",
    "ls tests/test_gate.py",
    "git log --oneline -- tests/test_gate.py",
    "sed -i 's/x/y/' tests/test_gate.py",
    "rm tests/test_gate.py",
    "cp tests/test_gate.py /tmp/",
    'echo "run python test_foo.py later"',
    "python -c 'import test_idp_api'",      # no file argument
    "python src/idp_api.py rag_stats '{}'",  # a python run, but not of a test
    "python manage.py test_data_import.py",  # command word is python, arg is not a test entry
    # Scripts whose names merely CONTAIN "test". Running one is a python run, not a
    # test run: the convention is test_x.py / x_test.py, and matching bare "test"
    # would count every one of these as verification.
    "python contest.py",
    "python testing_utils.py",
    "python latest_snapshot.py",
    "python protest.py",
    # Same rule for the checker names. The `[_-]` is what separates a checker from
    # a script whose name happens to begin with the verb.
    "python checkout.py",
    "python checker.py",
    "python verifier.py",
    "python linter.py",
    "python validation.py",
    "python pylint_config.py",             # neither `lint_x` nor `x_lint`
    "cat tools/check_doc.py",
    'grep -rn "python tools/check_doc.py" docs/',
    "rm tools/check_doc.py",
    "python manage.py check_perms.py",     # command runs manage.py
]


def main() -> int:
    failures = []

    for command in POSITIVE:
        if not runs_script_test(command):
            failures.append(f"missed a script test run: {command}")
        if not is_verification_command(command):
            failures.append(f"not treated as verification: {command}")

    for command in NEGATIVE:
        if runs_script_test(command):
            failures.append(f"false positive — not a test run: {command}")

    # End to end: a green script run must land in the ledger as a success, which is
    # what should_block_stop reads. Bash carries an explicit exit code, so this does
    # not depend on the text heuristics.
    record = verification_record(
        {
            "tool_name": "Bash",
            "tool_input": {"command": "python tests/test_gate.py"},
            "tool_response": {"stdout": "OK - all checks passed", "exit_code": 0},
        }
    )
    if not record:
        failures.append("verification_record returned None for a green script test run")
    elif record.get("success") is not True:
        failures.append(f"green script test run not recorded as success: {record.get('success')}")

    failed = verification_record(
        {
            "tool_name": "Bash",
            "tool_input": {"command": "python tests/test_gate.py"},
            "tool_response": {"stdout": "AssertionError", "exit_code": 1},
        }
    )
    if not failed or failed.get("success") is not False:
        failures.append("a failing script test run must record success=False")

    for line in failures:
        print(f"FAIL: {line}")
    if failures:
        print(f"\n{len(failures)} failure(s)")
        return 1
    print(f"OK - {len(POSITIVE)} runs detected, {len(NEGATIVE)} non-runs ignored, ledger record correct")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
