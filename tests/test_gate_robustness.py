#!/usr/bin/env python3
"""Robustness / safety checks for the fablize observation gate before wiring it global.

Covers the failure modes the gate-comparison doc warned about:
  - fail-open on bad/empty input (never crash, never block on our own bug)
  - cannot trap the agent forever (MAX_STOP_BLOCKS then allow)
  - respects the stop_hook_active loop guard
  - precision spot-checks: typecheck-success passes; config-edit-unverified reminds
"""

import json
import os
import subprocess
import sys
import tempfile

HOOKS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hooks")
PY = sys.executable


def run(script, payload, data_dir, raw=False):
    env = dict(os.environ)
    env["FABLIZE_DATA"] = data_dir
    stdin = payload if raw else json.dumps(payload)
    p = subprocess.run([PY, os.path.join(HOOKS, script)], input=stdin,
                       capture_output=True, text=True, env=env)
    return p


def as_json(p):
    try:
        return json.loads(p.stdout or "{}")
    except json.JSONDecodeError:
        return {"_raw": p.stdout}


def blocks(p):
    return as_json(p).get("decision") == "block"


checks = []


def check(name, cond):
    checks.append((name, bool(cond)))


# --- A. fail-open: empty + invalid stdin on every hook -> exit 0, never block ---
for script in ("gate_prompt.py", "gate_post_tool.py", "gate_stop.py"):
    dd = tempfile.mkdtemp(prefix="fz_")
    pe = run(script, "", dd, raw=True)
    pi = run(script, "}{not json", dd, raw=True)
    check(f"{script} empty-stdin exit0+noblock", pe.returncode == 0 and not blocks(pe))
    check(f"{script} bad-json exit0+noblock", pi.returncode == 0 and not blocks(pi))

# --- B. stop_hook_active is NOT a waiver: the flag is set after ANY Stop hook blocks
# (finish-the-work.sh included), so honouring it let one promise-no-act reminder
# skip the evidence gate. The loop bound is the ledger cap, checked in C. ---
dd = tempfile.mkdtemp(prefix="fz_")
run("gate_prompt.py", {"prompt": "implement X production-ready", "session_id": "B", "cwd": "/w"}, dd)
run("gate_post_tool.py", {"tool_name": "Edit", "tool_input": {"file_path": "src/x.py", "old_string": "a", "new_string": "b"}, "session_id": "B", "cwd": "/w"}, dd)
p_guard = run("gate_stop.py", {"session_id": "B", "cwd": "/w", "stop_hook_active": True}, dd)
check("stop_hook_active=true from another hook -> still blocks", blocks(p_guard))

# --- C. cannot trap forever: same session blocks at most MAX_STOP_BLOCKS(2), then allows ---
dd = tempfile.mkdtemp(prefix="fz_")
run("gate_prompt.py", {"prompt": "implement X production-ready", "session_id": "C", "cwd": "/w"}, dd)
run("gate_post_tool.py", {"tool_name": "Edit", "tool_input": {"file_path": "src/x.py", "old_string": "a", "new_string": "b"}, "session_id": "C", "cwd": "/w"}, dd)
d1 = blocks(run("gate_stop.py", {"session_id": "C", "cwd": "/w", "stop_hook_active": False}, dd))
d2 = blocks(run("gate_stop.py", {"session_id": "C", "cwd": "/w", "stop_hook_active": False}, dd))
p3 = run("gate_stop.py", {"session_id": "C", "cwd": "/w", "stop_hook_active": True}, dd)
d3 = blocks(p3)
check("blocks first 2 then allows (no infinite trap)", d1 and d2 and not d3)
# The gap note goes to the user as systemMessage only: Stop has no
# hookSpecificOutput, and the model's turn is already over.
check("after the cap: gap disclosed via systemMessage", "unverified" in str(as_json(p3).get("systemMessage", "")))
check("after the cap: no hookSpecificOutput on Stop", "hookSpecificOutput" not in as_json(p3))

# --- D. precision: deep task + code edit + typecheck SUCCESS -> allow ---
dd = tempfile.mkdtemp(prefix="fz_")
run("gate_prompt.py", {"prompt": "refactor the parser thoroughly", "session_id": "D", "cwd": "/w"}, dd)
run("gate_post_tool.py", {"tool_name": "Edit", "tool_input": {"file_path": "src/p.ts", "old_string": "a", "new_string": "b"}, "session_id": "D", "cwd": "/w"}, dd)
run("gate_post_tool.py", {"tool_name": "Bash", "tool_input": {"command": "tsc --noEmit"}, "tool_response": {"exit_code": 0, "stdout": "done"}, "session_id": "D", "cwd": "/w"}, dd)
p_ts = run("gate_stop.py", {"session_id": "D", "cwd": "/w", "stop_hook_active": False}, dd)
check("deep + code edit + tsc success -> allow", not blocks(p_ts))

# --- E. lecture-style Korean doc prompt defaults to quick -> never blocks ---
dd = tempfile.mkdtemp(prefix="fz_")
run("gate_prompt.py", {"prompt": "윤자동 2회차 강의 준비해줘", "session_id": "E", "cwd": "/w"}, dd)
run("gate_post_tool.py", {"tool_name": "Edit", "tool_input": {"file_path": "course.md", "old_string": "a", "new_string": "b"}, "session_id": "E", "cwd": "/w"}, dd)
p_kr = run("gate_stop.py", {"session_id": "E", "cwd": "/w", "stop_hook_active": False}, dd)
check("KO lecture prompt (quick default) -> allow", not blocks(p_kr))

# --- F. deep-only: a NORMAL task + code edit + no verification -> allow (no hard block) ---
dd = tempfile.mkdtemp(prefix="fz_")
run("gate_prompt.py", {"prompt": "fix the login bug in the parser", "session_id": "F", "cwd": "/w"}, dd)
run("gate_post_tool.py", {"tool_name": "Edit", "tool_input": {"file_path": "src/login.py", "old_string": "a", "new_string": "b"}, "session_id": "F", "cwd": "/w"}, dd)
p_normal = run("gate_stop.py", {"session_id": "F", "cwd": "/w", "stop_hook_active": False}, dd)
check("deep-only: normal task changed+unverified -> allow (no block)", not blocks(p_normal))

# --- G. deep turn that changed NOTHING (analysis/audit) -> allow (no "add observable" nag) ---
dd = tempfile.mkdtemp(prefix="fz_")
run("gate_prompt.py", {"prompt": "thoroughly audit the security of this module", "session_id": "G", "cwd": "/w"}, dd)
p_analysis = run("gate_stop.py", {"session_id": "G", "cwd": "/w", "stop_hook_active": False}, dd)
check("deep analysis, no change -> allow (no false-positive nag)", not blocks(p_analysis))

# --- I. a pytest crash / empty run is a FAILED verification, not an unknown one ---
dd = tempfile.mkdtemp(prefix="fz_")
run("gate_prompt.py", {"prompt": "implement X production-ready", "session_id": "I", "cwd": "/w"}, dd)
run("gate_post_tool.py", {"tool_name": "Edit", "tool_input": {"file_path": "src/x.py", "old_string": "a", "new_string": "b"}, "session_id": "I", "cwd": "/w"}, dd)
run("gate_post_tool.py", {"tool_name": "Bash", "tool_input": {"command": "python -m pytest tests -q"},
                          "tool_response": {"stdout": "INTERNALERROR> SystemExit: 0\n\nno tests ran in 1.74s"},
                          "session_id": "I", "cwd": "/w"}, dd)
check("pytest 'no tests ran' does not satisfy the gate",
      blocks(run("gate_stop.py", {"session_id": "I", "cwd": "/w", "stop_hook_active": False}, dd)))


# --- J. PostToolUseFailure: a non-zero exit fires ONLY this event. An earlier pass
# must not outvote the later failure, and the failure must be disclosed. ---
EDIT_J = {"tool_name": "Edit", "tool_input": {"file_path": "src/x.py", "old_string": "a", "new_string": "b"}}
PASS_J = {"tool_name": "Bash", "tool_input": {"command": "pytest -q"}, "tool_response": {"stdout": "4 passed in 0.1s"}}
FAIL_J = {"hook_event_name": "PostToolUseFailure", "tool_name": "Bash", "tool_input": {"command": "pytest -q"},
          "error": "Exit code 1\n1 failed, 3 passed", "is_interrupt": False}
dd = tempfile.mkdtemp(prefix="fz_")
run("gate_prompt.py", {"prompt": "implement X production-ready", "session_id": "J", "cwd": "/w"}, dd)
for ev in (PASS_J, EDIT_J):
    run("gate_post_tool.py", {**ev, "session_id": "J", "cwd": "/w"}, dd)
pf = as_json(run("gate_post_tool.py", {**FAIL_J, "session_id": "J", "cwd": "/w"}, dd))
check("PostToolUseFailure: failure disclosed under its own event name",
      pf.get("hookSpecificOutput", {}).get("hookEventName") == "PostToolUseFailure")
check("pass, edit, FAILED run -> blocks (latest result decides)",
      blocks(run("gate_stop.py", {"session_id": "J", "cwd": "/w", "stop_hook_active": False}, dd)))

# --- K. interrupted verification and failed Edit record nothing ---
dd = tempfile.mkdtemp(prefix="fz_")
run("gate_prompt.py", {"prompt": "implement X production-ready", "session_id": "K", "cwd": "/w"}, dd)
run("gate_post_tool.py", {**EDIT_J, "hook_event_name": "PostToolUseFailure", "error": "old_string not found",
                          "session_id": "K", "cwd": "/w"}, dd)
check("failed Edit is not a change -> no block",
      not blocks(run("gate_stop.py", {"session_id": "K", "cwd": "/w", "stop_hook_active": False}, dd)))
run("gate_post_tool.py", {**EDIT_J, "session_id": "K", "cwd": "/w"}, dd)
run("gate_post_tool.py", {**FAIL_J, "is_interrupt": True, "session_id": "K", "cwd": "/w"}, dd)
check("interrupted pytest is not a verification -> still blocks",
      blocks(run("gate_stop.py", {"session_id": "K", "cwd": "/w", "stop_hook_active": False}, dd)))

# --- L. a chained shell error before a passing summary is a pass ---
dd = tempfile.mkdtemp(prefix="fz_")
run("gate_prompt.py", {"prompt": "implement X production-ready", "session_id": "L", "cwd": "/w"}, dd)
run("gate_post_tool.py", {**EDIT_J, "session_id": "L", "cwd": "/w"}, dd)
pl = as_json(run("gate_post_tool.py", {"tool_name": "Bash", "tool_input": {"command": "ls build/ 2>&1; python -m pytest -q"},
                                       "tool_response": {"stdout": "ls: cannot access build/: No such file or directory\n....\n4 passed in 0.2s"},
                                       "session_id": "L", "cwd": "/w"}, dd))
check("'No such file' then '4 passed' -> not a failure", "hookSpecificOutput" not in pl)
check("... and satisfies the gate",
      not blocks(run("gate_stop.py", {"session_id": "L", "cwd": "/w", "stop_hook_active": False}, dd)))
pv = as_json(run("gate_post_tool.py", {"tool_name": "Bash", "tool_input": {"command": "pytest -q | tail -3"},
                                       "tool_response": {"stdout": "1 failed, 3 passed in 0.2s"},
                                       "session_id": "L", "cwd": "/w"}, dd))
check("piped pytest verdict '1 failed, 3 passed' still a failure", "hookSpecificOutput" in pv)

# --- M. a `cd` mid-turn changes the hook's cwd; the turn's ledger must follow ---
dd = tempfile.mkdtemp(prefix="fz_")
run("gate_prompt.py", {"prompt": "implement X production-ready", "session_id": "M", "cwd": "D:/proj"}, dd)
run("gate_post_tool.py", {**EDIT_J, "session_id": "M", "cwd": "D:/proj/sub"}, dd)
check("cwd change mid-turn does not disarm the gate",
      blocks(run("gate_stop.py", {"session_id": "M", "cwd": "D:/proj/sub", "stop_hook_active": False}, dd)))


# --- N. machine-injected prompts continue the task; background work pauses the gate ---
dd = tempfile.mkdtemp(prefix="fz_")
run("gate_prompt.py", {"prompt": "refactor the auth module thoroughly", "session_id": "N", "cwd": "/w"}, dd)
run("gate_post_tool.py", {"tool_name": "Edit", "tool_input": {"file_path": "src/x.py", "old_string": "a", "new_string": "b"}, "session_id": "N", "cwd": "/w"}, dd)
run("gate_prompt.py", {"prompt": "<task-notification>done</task-notification>", "source": "system", "session_id": "N", "cwd": "/w"}, dd)
check("a task notification does not reset the turn (edit stays armed)",
      blocks(run("gate_stop.py", {"session_id": "N", "cwd": "/w"}, dd)))
check("background work in flight -> no block",
      not blocks(run("gate_stop.py", {"session_id": "N", "cwd": "/w", "background_tasks": [{"id": "b1"}]}, dd)))
for p in ["Yes, go ahead and implement it", "네, 수정해주세요", "ok fix it"]:
    dd = tempfile.mkdtemp(prefix="fz_")
    run("gate_prompt.py", {"prompt": "refactor the auth module thoroughly", "session_id": "O", "cwd": "/w"}, dd)
    out = as_json(run("gate_prompt.py", {"prompt": p, "session_id": "O", "cwd": "/w"}, dd))
    check(f"an action-naming approval keeps deep: {p}",
          "task mode: deep" in out.get("hookSpecificOutput", {}).get("additionalContext", ""))


def main():
    print("=" * 80)
    print("fablize observation gate — robustness / safety checks")
    print("=" * 80)
    fails = 0
    for name, ok in checks:
        fails += 0 if ok else 1
        print(f"  [{'OK' if ok else 'FAIL'}] {name}")
    print("-" * 80)
    print(f"RESULT: {'all pass' if not fails else str(fails) + ' FAILED'} ({len(checks)} checks)")
    return 1 if fails else 0


def test_script():
    """pytest entry. The script form (`python3 tests/test_gate_robustness.py`) stays canonical."""
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
