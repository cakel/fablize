#!/usr/bin/env python3
"""goals.py must never report completion while a story is blocked or failed, and a
blocked/failed story must be recoverable without `create --force`."""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

GOALS = Path(__file__).resolve().parent.parent / "scripts" / "goals.py"
CHECKS = []


def check(label, got, want):
    CHECKS.append((label, got, want))


def g(cwd, *args):
    # Host encoding vars stripped: a cp949 console must not crash on ✓ / —.
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONIOENCODING", "PYTHONUTF8")}
    p = subprocess.run([sys.executable, str(GOALS), *args], cwd=cwd, capture_output=True,
                       env=env, timeout=60)
    return p.returncode, p.stdout.decode("utf-8", "replace") + p.stderr.decode("utf-8", "replace")


with tempfile.TemporaryDirectory() as w:
    check("create", g(w, "create", "--brief", "b", "--goal", "a::x", "--goal", "b::y")[0], 0)
    g(w, "next")
    g(w, "checkpoint", "--id", "G001", "--status", "blocked", "--evidence", "dep missing")
    g(w, "next")
    rc, out = g(w, "checkpoint", "--id", "G002", "--status", "failed", "--evidence", "boom")
    check("checkpoint runs without a UTF-8 console", rc, 0)
    check("blocked+failed is NOT reported complete", "all stories complete" in out, False)
    check("... and says so", "NOT complete" in out, True)
    check("next agrees", "NOT complete" in g(w, "next")[1], True)
    rc, out = g(w, "next", "--reopen", "G001")
    check("reopen a blocked story", (rc, "handoff — G001" in out), (0, True))
    check("reopen refuses a non-blocked story", g(w, "next", "--reopen", "G001")[0] != 0, True)
    g(w, "checkpoint", "--id", "G001", "--status", "complete", "--evidence", "fixed")
    g(w, "next", "--reopen", "G002")
    # The engine runs the verify command itself; a self-reported result is not enough.
    rc, out = g(w, "checkpoint", "--id", "G002", "--status", "complete", "--evidence", "ok",
                "--verify-cmd", "false", "--verify-evidence", "trust me")
    check("a failing verify command blocks completion", (rc != 0, "all stories complete" in out), (True, False))
    rc, out = g(w, "checkpoint", "--id", "G002", "--status", "complete", "--evidence", "ok",
                "--verify-cmd", "true")
    check("a no-op verify command is refused", (rc != 0, "proves nothing" in out), (True, True))
    py = Path(sys.executable).as_posix()
    rc, out = g(w, "checkpoint", "--id", "G002", "--status", "complete", "--evidence", "ok",
                "--verify-cmd", f'"{py}" -c "print(3, chr(112) + chr(97) + chr(115) * 2 + chr(101) + chr(100))"')
    check("all complete only when all are", "all stories complete" in out, True)
    check("observed output is recorded", "3 passed" in (Path(w) / ".fablize" / "ledger.jsonl").read_text(encoding="utf-8"), True)


def main():
    bad = 0
    for label, got, want in CHECKS:
        ok = got == want
        bad += 0 if ok else 1
        print(f"{'OK  ' if ok else 'FAIL'} {str(got)[:40]:<42} {label}")
    print("-" * 78)
    print(f"RESULT: {'all ' + str(len(CHECKS)) + ' checks match.' if not bad else str(bad) + ' mismatched.'}")
    return 1 if bad else 0


def test_script():
    """pytest entry."""
    assert main() == 0


if __name__ == "__main__":
    raise SystemExit(main())
