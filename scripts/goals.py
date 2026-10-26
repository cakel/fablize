#!/usr/bin/env python3
"""fablize goal engine — a self-contained, stdlib-only multi-story loop with a verification gate.

Design (behavior only):
  - Decompose a task into sequential stories, persisted to a ledger (.fablize/) — survives session death.
  - A story can be checkpointed only after `next` activates it.
  - A `complete` checkpoint requires non-empty evidence.
  - The final story cannot complete without a verify command, which goals.py RUNS itself:
    exit 0 is required, and the observed exit code + output tail are recorded as evidence.
    A self-reported result ("--verify-cmd false --verify-evidence 'trust me'") used to pass.

Usage:
  goals.py create --brief "..." --goal "title::objective" [--goal ...]
  goals.py next [--reopen G002]       # activate the next story (or reopen a blocked/failed one)
  goals.py checkpoint --id G001 --status complete|failed|blocked --evidence "..."
                      [--verify-cmd "<command>" [--verify-evidence "<note>"]]   # final story: goals.py runs it
  goals.py status
State directory: ./.fablize/ (run from the repo root)
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

DIR = Path(".fablize")
GOALS = DIR / "goals.json"
LEDGER = DIR / "ledger.jsonl"


def now():
    return datetime.now(timezone.utc).isoformat()


def log(event, **kw):
    DIR.mkdir(exist_ok=True)
    with open(LEDGER, "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": now(), "event": event, **kw}, ensure_ascii=False) + "\n")


def load():
    if not GOALS.exists():
        sys.exit("fablize: no plan — run `create` from the repo root first.")
    return json.loads(GOALS.read_text(encoding="utf-8"))


def save(plan):
    DIR.mkdir(exist_ok=True)
    GOALS.write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")


def progress_line(plan):
    """Never "all complete" while a story is blocked or failed.

    That line used to print whenever nothing was pending, so a plan whose stories
    were all blocked/failed announced success — and a model relays that verbatim.
    """
    counts = {}
    for g in plan["goals"]:
        counts[g["status"]] = counts.get(g["status"], 0) + 1
    left = counts.get("pending", 0) + counts.get("in_progress", 0)
    if left:
        return f"fablize: {left} stories left — continue with `next`."
    stuck = counts.get("blocked", 0) + counts.get("failed", 0)
    if stuck:
        return (f"fablize: NOT complete — {counts.get('blocked', 0)} blocked, "
                f"{counts.get('failed', 0)} failed. Report them, or retry one with "
                f"`next --reopen <id>`.")
    return "fablize: all stories complete ✓"


def cmd_create(a):
    if GOALS.exists() and not a.force:
        sys.exit("fablize: a plan already exists. Check it with `status`, or replace it with --force.")
    goals = []
    for i, g in enumerate(a.goal, 1):
        if "::" not in g:
            sys.exit(f"fablize: --goal format is 'title::objective' — invalid: {g}")
        title, obj = g.split("::", 1)
        goals.append({"id": f"G{i:03d}", "title": title.strip(), "objective": obj.strip(),
                      "status": "pending", "evidence": None})
    if not goals:
        sys.exit("fablize: at least one --goal is required.")
    save({"brief": a.brief, "created": now(), "goals": goals})
    log("plan_created", brief=a.brief, count=len(goals))
    print(f"fablize: plan created — {len(goals)} stories")
    for g in goals:
        print(f"  {g['id']} {g['title']}: {g['objective']}")


def cmd_next(a):
    plan = load()
    active = [g for g in plan["goals"] if g["status"] == "in_progress"]
    if a.reopen:
        # The only way back from blocked/failed used to be `create --force`,
        # which throws away every completed story's evidence.
        g = next((x for x in plan["goals"] if x["id"] == a.reopen), None)
        if not g:
            sys.exit(f"fablize: {a.reopen} not found.")
        if g["status"] not in ("blocked", "failed"):
            sys.exit(f"fablize: {a.reopen} is {g['status']} — only blocked/failed stories reopen.")
        if active:
            sys.exit(f"fablize: {active[0]['id']} is still in progress — checkpoint it first.")
        g["status"] = "pending"
        save(plan); log("story_reopened", id=g["id"])
        active = []
    if active:
        g = active[0]
    else:
        pending = [g for g in plan["goals"] if g["status"] == "pending"]
        if not pending:
            print(progress_line(plan)); return
        g = pending[0]
        g["status"] = "in_progress"
        save(plan); log("story_started", id=g["id"], title=g["title"])
    is_final = g["id"] == plan["goals"][-1]["id"]
    print(f"=== fablize handoff — {g['id']} {g['title']}")
    print(f"Objective: {g['objective']}")
    print("Rule: work this story only. Produce evidence as you go.")
    if is_final:
        print("★ Final story — the complete checkpoint requires --verify-cmd; goals.py runs it and needs exit 0.")
    print(f"On completion: goals.py checkpoint --id {g['id']} --status complete --evidence \"<evidence>\""
          + (" --verify-cmd \"<test/build command>\"" if is_final else ""))


# Commands that prove nothing whatever they exit with.
_NO_OP = {"true", ":", "echo", "printf", "exit", "cat", "ls", "pwd", "rem", "type", "dir"}
VERIFY_TIMEOUT = 1800


def _shell():
    """bash when it is a real POSIX bash; the OS shell otherwise. On Windows,
    System32\\bash.exe is the WSL launcher — a different filesystem and toolchain."""
    b = shutil.which("bash")
    if b and not (os.name == "nt" and ("system32" in b.lower() or "windowsapps" in b.lower())):
        return [b, "-c"]
    return None


def run_verify(cmd):
    """Run the verify command from the current dir. Returns (exit code, output tail)."""
    sh = _shell()
    try:
        p = subprocess.run(sh + [cmd] if sh else cmd, shell=sh is None, capture_output=True,
                           timeout=VERIFY_TIMEOUT)
    except subprocess.TimeoutExpired:
        return None, f"timed out after {VERIFY_TIMEOUT}s"
    except OSError as exc:
        return None, f"could not run: {exc}"
    out = (p.stdout + p.stderr).decode("utf-8", "replace").strip().splitlines()
    return p.returncode, "\n".join(out[-15:])


def cmd_checkpoint(a):
    plan = load()
    g = next((x for x in plan["goals"] if x["id"] == a.id), None)
    if not g:
        sys.exit(f"fablize: {a.id} not found.")
    if g["status"] != "in_progress":
        sys.exit(f"fablize: {a.id} is not active ({g['status']}) — activate it with `next` first.")
    if a.status == "complete":
        if not (a.evidence and a.evidence.strip()):
            sys.exit("fablize: a complete checkpoint requires non-empty --evidence.")
        if g["id"] == plan["goals"][-1]["id"]:
            cmd = (a.verify_cmd or "").strip()
            if not cmd:
                sys.exit("fablize: the final story cannot complete without --verify-cmd (verification gate).")
            if cmd.split()[0].lower() in _NO_OP:
                sys.exit(f"fablize: `{cmd.split()[0]}` proves nothing — pass the test/build/check command itself.")
            print(f"fablize: running verify command: {cmd}")
            rc, tail = run_verify(cmd)
            print(tail)
            if rc != 0:
                log("verify_failed", id=g["id"], verify_cmd=cmd, exit=rc, output_tail=tail)
                sys.exit(f"fablize: verify command exited {rc} — the final story stays in progress.")
            a.verify_evidence = ((a.verify_evidence or "").strip() + f"\n[observed] exit 0\n{tail}").strip()
    g["status"] = a.status
    g["evidence"] = a.evidence
    save(plan)
    log("checkpoint", id=g["id"], status=a.status, evidence=a.evidence,
        verify_cmd=a.verify_cmd, verify_evidence=a.verify_evidence)
    print(f"fablize: {g['id']} → {a.status}")
    print(progress_line(plan))


def cmd_status(a):
    plan = load()
    done = sum(1 for g in plan["goals"] if g["status"] == "complete")
    print(f"fablize: {done}/{len(plan['goals'])} complete — {plan['brief']}")
    mark = {"complete": "✓", "in_progress": "▶", "pending": "·", "failed": "✗", "blocked": "■"}
    for g in plan["goals"]:
        print(f"  {mark.get(g['status'],'?')} {g['id']} [{g['status']}] {g['title']}")


def main():
    # Run directly as `python goals.py` (not through hooks/py.sh) on a cp949 or
    # cp1252 console, the ✓ / — in these messages raised UnicodeEncodeError —
    # after state was already saved.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    p = argparse.ArgumentParser(prog="goals.py")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create"); c.add_argument("--brief", required=True)
    c.add_argument("--goal", action="append", default=[]); c.add_argument("--force", action="store_true")
    n = sub.add_parser("next"); n.add_argument("--reopen", default="", metavar="ID")
    k = sub.add_parser("checkpoint"); k.add_argument("--id", required=True)
    k.add_argument("--status", required=True, choices=["complete", "failed", "blocked"])
    k.add_argument("--evidence", default=""); k.add_argument("--verify-cmd", dest="verify_cmd", default="")
    k.add_argument("--verify-evidence", dest="verify_evidence", default="")
    sub.add_parser("status")
    a = p.parse_args()
    {"create": cmd_create, "next": cmd_next, "checkpoint": cmd_checkpoint, "status": cmd_status}[a.cmd](a)


if __name__ == "__main__":
    main()
