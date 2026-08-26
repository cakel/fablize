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
  6. changed_kinds never read the operands, so every mutating shell line reported
     "other" — a claim about files it never looked at. `cp notes.md docs/notes.md`
     said a non-docs thing changed, and `mkdir -p out` said a file changed at all.
     change_kinds accumulates across a turn, so either one disarmed docs_only()
     for the rest of it.

Exit non-zero on any mismatch.
"""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "gate"))

import ledger as L  # noqa: E402
from classify_task import classify_prompt  # noqa: E402
from parse_tool_result import (  # noqa: E402
    changed_kinds,
    command_from_input,
    is_verification_command,
)


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
    ("mkdir -p out", []),             # a directory is not a file: nothing to verify
    ("cp a b", ["other"]),
    ('grep -rn "rm -rf" docs/', []),
    ("ls /tmp/cp-backup", []),
    ("echo 'touch me'", []),
    ("cat notes.md", []),
]:
    check(f"mutating? {command!r}", changed_kinds(bash(command)), want)

# 6. mutation kind comes from the operands, not from the verb alone
for command, want in [
    ("cp notes.md docs/notes.md", ["docs"]),
    ("mv src/a.py src/b.py", ["code"]),
    ("cp settings.json backup.json", ["config"]),
    ("cp logo.png assets/logo.png", ["assets"]),
    ("cp -a --preserve=all src dst", ["other"]),   # flags skipped, operands unnamed
    ("cp a.md b.md 2>&1", ["docs"]),               # `2>&1` debris is not an operand
    ("rm -rf dist && cp a.md b.md", ["docs", "other"]),
    ("mkdir -p _ws && cp report.md _ws/report.md", ["docs"]),
    ("touch", ["other"]),                          # mutates something it cannot name
]:
    check(f"operand kind {command!r}", changed_kinds(bash(command)), want)

# 6b. a shell line reaches the ledger from PowerShell too — the PostToolUse matcher
# excluded it, so on Windows every change and every check made there was invisible.
for command, want in [
    ("Copy-Item notes.md docs/notes.md", ["docs"]),
    ("Remove-Item -Recurse dist", ["other"]),
    ("Get-Content notes.md", []),
]:
    check(
        f"pwsh mutating? {command!r}",
        changed_kinds({"tool_name": "PowerShell", "tool_input": {"command": command}}),
        want,
    )

# 6c. a project's own checker is a verification; a name that merely starts with the
# verb is not. Same `[_-]` rule that keeps contest.py out of the test entry points.
for command, want in [
    ("python tools/check_doc.py --strict", True),
    ("python scripts/verify_gates.py", True),
    ("python tools/lint_diagram.py", True),
    ("python src/schema_check.py", True),
    ("python checkout.py", False),
    ("python checker.py", False),
    ("cat tools/check_doc.py", False),
    ("python manage.py check_perms.py", False),    # command runs manage.py
]:
    check(f"checker? {command!r}", is_verification_command(command), want)

# 6d. prose is not a command. The description fallback let a sentence disarm the
# gate outright — a false verification, not merely a false change.
for description, want_kinds, want_verify in [
    ("check the build output", [], False),
    ("mkdir the output directory", [], False),
    ("remove stale caches", [], False),
]:
    payload = {"tool_name": "Bash", "tool_input": {"description": description}}
    check(f"description kinds {description!r}", changed_kinds(payload), want_kinds)
    check(
        f"description verify {description!r}",
        is_verification_command(command_from_input(payload)),
        want_verify,
    )

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
