#!/usr/bin/env python3
"""A command run on a remote host over ssh is not a local file change.

Observed 2026-10-21: four deploy turns were blocked by the deep Stop gate. Their
only "change" was `ssh host 'cd x && cp .env .env.bak && ...'` — _SEGMENT_RE split
inside the quotes and the remote `cp` read as a local config change.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "gate"))
from parse_tool_result import changed_kinds


def kinds(cmd):
    return changed_kinds({"tool_name": "Bash", "tool_input": {"command": cmd}})


# remote mutation: not a local change
assert kinds("ssh -p 20000 u@h 'cd ~/x && cp .env .env.bak && git pull'") == []
assert kinds('ssh u@h "rm -f /tmp/x; sed -i s/a/b/ .env"') == []
# controls: local mutation still counts, before/after/alongside an ssh call
assert kinds("cp .env .env.bak") == ["config"]
assert kinds("ssh u@h 'ls'; cp .env .env.bak") == ["config"]
assert kinds("cp .env .env.bak && ssh u@h 'uptime'") == ["config"]
assert kinds("ssh u@h 'ls' && rm -f notes.md") == ["docs"]
print("ok")
