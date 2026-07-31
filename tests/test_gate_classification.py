#!/usr/bin/env python3
"""Classification must key on what a call IS, not on words that appear in it.

Five defects, all confirmed by driving the real modules:

  1. classify_path_kind put a `docs` path segment ahead of the extension, so
     docs/conf.py and packages/docs/src/index.ts were "docs" and
     verify_state.docs_only() short-circuited the deep gate for real code.
  2. VERIFY_RE matched bare build/check/verify/curl anywhere, so `cat build.log`
     was logged as a verification run and satisfied the deep gate.
  3. load_ledger's corrupt-file path reset task_mode to "quick", disarming the
     gate for the rest of a deep turn after one unreadable read.
  4. MUTATING_BASH_RE matched bare rm/cp/mv anywhere, so `grep -rn "rm -rf" docs/`
     counted as a file change and blocked a turn that changed nothing.
  5. DEEP_RE ran before QUICK_RE, so a topic keyword overrode an explicit
     explain-only request.

Exit non-zero on any mismatch.
"""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "gate"))

import ledger as L  # noqa: E402
from classify_task import classify_prompt  # noqa: E402
from parse_tool_result import changed_kinds, is_verification_command  # noqa: E402


def bash(command):
    return {"tool_name": "Bash", "tool_input": {"command": command}, "tool_response": {"stdout": ""}}


CHECKS = []


def check(label, got, want):
    CHECKS.append((label, got, want))


# 1. path kind — extension outranks a `docs` directory segment
for path, want in [
    ("docs/conf.py", "code"),
    ("packages/docs/src/index.ts", "code"),
    ("docs/guide.md", "docs"),
    ("docs/architecture.rst", "docs"),
    ("docs/settings.json", "config"),
    ("docs/diagram.png", "assets"),
    ("docs/NOTES", "docs"),          # no extension: the directory hint still applies
    ("src/app.py", "code"),
    ("README.md", "docs"),
]:
    check(f"path kind {path}", L.classify_path_kind(path), want)

# 2. verification command — generic verbs only count in command position
for command, want in [
    ("pytest tests/", True),
    ("npm test", True),
    ("npm run build", True),
    ("curl -s http://localhost/health", True),
    ("make check", True),
    ("cat build.log", False),
    ("grep -rn build src/", False),
    ("ls build/", False),
    ("rm -rf build", False),
    ("git log --oneline -1", False),
    ("echo hi && pytest tests/", True),
    ("/usr/bin/curl -s url", True),
]:
    check(f"verify? {command!r}", is_verification_command(command), want)

# 4. mutation — same command-position rule
for command, want in [
    ("rm -rf dist", ["other"]),
    ("mkdir -p out", ["other"]),
    ("cp a b", ["other"]),
    ('grep -rn "rm -rf" docs/', []),
    ("ls /tmp/cp-backup", []),
    ("echo 'touch me'", []),
    ("cat notes.md", []),
]:
    check(f"mutating? {command!r}", changed_kinds(bash(command)), want)

# 5. explicit explain-only beats topic keywords
for prompt, want in [
    ("배포 절차만 간단히 설명해줘", "quick"),
    ("briefly explain the auth flow, review only", "quick"),
    ("배포 스크립트 간단히 고쳐줘", "deep"),        # action verb present -> not quick
    ("thoroughly implement the auth module", "deep"),
    ("fix the parser", "normal"),
]:
    check(f"mode {prompt!r}", classify_prompt(prompt)[0], want)

# 3. corrupt ledger must not fall back to the gate-disabling mode
with tempfile.TemporaryDirectory() as tmp:
    import os

    os.environ["FABLIZE_DATA"] = tmp
    payload = {"session_id": "S", "cwd": "/w"}
    L.ledger_path(payload).parent.mkdir(parents=True, exist_ok=True)
    L.ledger_path(payload).write_text("{not json", encoding="utf-8")
    recovered = L.load_ledger(payload)
    check("corrupt ledger task_mode", recovered["task_mode"], "normal")
    check("corrupt ledger records why", bool(recovered["failures"]), True)


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
    print(f"RESULT: all {len(CHECKS)} checks match.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
