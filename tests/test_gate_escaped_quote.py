#!/usr/bin/env python3
r"""An escaped quote inside a double-quoted argument is not the end of it.

Observed 2026-11-03: a commit message holding `\\"Only\\"` and an e-mail
`<noreply@…>`, followed by `rg -q "pending"`, was read as a redirect into a file
named ` && git push … rg -q `. That counted as a change after the passing tests,
and the Stop gate asked for a check the turn already had. shlex(posix=False)
ignores backslashes, so `\\"` closed the quote and `<…>` landed outside it.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "gate"))

import parse_tool_result as p  # noqa: E402

B, Q = "\\", '"'
CWD = "D:\Work\proj"


def kinds(command, tool="Bash"):
    return p.changed_kinds({"tool_name": tool, "cwd": CWD, "tool_input": {"command": command}})


def test_escaped_quote_in_a_commit_message_is_no_write():
    msg = "says " + B + Q + "Only" + B + Q + " the /tmp/ forms\n\nCo-Authored-By: C <noreply@anthropic.com>"
    cmd = 'git add -A && git commit -q -m "' + msg + '" && git push -q && until gh pr checks 23 2>&1 | rg -q "pending"; do sleep 3; done'
    assert kinds(cmd) == []


def test_escaped_backslash_then_quote_still_closes():
    # `"a\\"` is a backslash and the end of the string: the redirect after it is real.
    assert kinds('echo "a' + B + B + '" > src/out.py') == ["code"]


def test_escaped_quote_outside_quotes_does_not_hide_a_write():
    assert kinds("echo it" + B + "'s > src/out.py") == ["code"]


def test_redirect_after_an_escaped_quote_is_still_seen():
    assert kinds('echo "say ' + B + Q + 'hi' + B + Q + '" > src/out.py') == ["code"]


def test_powershell_backslash_before_a_quote_is_a_path_end():
    # In PowerShell `\` is not an escape: "C:\out\" ends there, and the write after counts.
    assert kinds('Get-ChildItem "C:' + B + 'out' + B + '"; echo x > src/out.py', tool="PowerShell") == ["code"]
