#!/usr/bin/env python3
"""hooks/kiro.py driven with the payloads kiro-cli 2.33 actually sends (captured
2026-11-02 with a hook that dumped stdin), through the real gates.

Kiro's shapes differ from Claude Code's: tool names `write` / `shell`,
`tool_input.path`, the shell result nested in `tool_response.items[].Json` with
`exit_status: "exit code: N"`, and `assistant_response` on stop. Without the
adapter the gates saw no change and no check, so the Stop gate never fired.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ADAPTER = Path(__file__).resolve().parent.parent / "hooks" / "kiro.py"
CWD = tempfile.mkdtemp(prefix="fz_kiro_cwd_")


def run(event, payload, data_dir):
    env = dict(os.environ, FABLIZE_DATA=data_dir, PYTHONUTF8="1")
    env.pop("FABLIZE_HOLDOUT", None)
    p = subprocess.run([sys.executable, str(ADAPTER), event], input=json.dumps(payload).encode("utf-8"),
                       capture_output=True, env=env, timeout=60)
    assert p.returncode == 0, p.stderr
    return p.stdout.decode("utf-8").strip()


def prompt(sid, text):
    return {"hook_event_name": "userPromptSubmit", "cwd": CWD, "session_id": sid, "prompt": text}


def write(sid, path, command="create"):
    return {"hook_event_name": "postToolUse", "cwd": CWD, "session_id": sid, "tool_name": "write",
            "tool_input": {"command": command, "path": path, "content": "x = 1\n"},
            "tool_response": {"items": [{"Text": "Successfully created " + path}]}}


def shell(sid, command, code=0, stdout=""):
    return {"hook_event_name": "postToolUse", "cwd": CWD, "session_id": sid, "tool_name": "shell",
            "tool_input": {"command": command, "working_dir": CWD},
            "tool_response": {"items": [{"Json": {"exit_status": f"exit code: {code}",
                                                  "stdout": stdout, "stderr": ""}}]}}


def stop(sid):
    return {"hook_event_name": "stop", "cwd": CWD, "session_id": sid, "assistant_response": "Done."}


DEEP = "Refactor the auth module thoroughly and make it production-ready"


def turn(sid, *posts):
    data = tempfile.mkdtemp(prefix="fz_kiro_data_")
    context = run("prompt", prompt(sid, DEEP), data)
    for p in posts:
        assert run("post", p, data) == ""  # Kiro does not show postToolUse output
    return context, run("stop", stop(sid), data)


def test_prompt_context_is_plain_text():
    context, _ = turn("k1")
    assert context and not context.startswith("{"), context  # Kiro injects stdout verbatim
    assert "fablize" in context


def test_unverified_code_change_blocks_stop():
    _, out = turn("k2", write("k2", "src/auth.py"))
    assert json.loads(out)["decision"] == "block"


def test_passing_check_lets_stop_through():
    _, out = turn("k3", write("k3", "src/auth.py", "strReplace"),
                  shell("k3", "python -m pytest -q", 0, "3 passed in 0.1s"))
    assert out == ""


def test_failing_check_still_blocks():
    _, out = turn("k4", write("k4", "src/auth.py"), shell("k4", "python -m pytest -q", 1, "1 failed"))
    assert json.loads(out)["decision"] == "block"


def test_crashed_check_is_not_a_pass():
    # Windows crash codes come as hex ("exit code: 0xc0000005"); reading the first
    # digit run made that exit 0, a passing check.
    _, out = turn("k6", write("k6", "src/auth.py"), shell_status("k6", "python -m pytest -q", "exit code: 0xc0000005"))
    assert json.loads(out)["decision"] == "block"


def shell_status(sid, command, status):
    p = shell(sid, command)
    p["tool_response"]["items"][0]["Json"]["exit_status"] = status
    return p


def test_docs_only_change_does_not_block():
    _, out = turn("k5", write("k5", "README.md"))
    assert out == ""


# v3 engine (KiroCLI/2.37.1): other tool names, string results, no assistant_response.
def v3_write(sid, path, tool="fs_write"):
    return {"hook_event_name": "PostToolUse", "cwd": CWD, "session_id": sid, "tool_name": tool,
            "tool_input": {"path": os.path.join(CWD, path), "text": "x = 1\n"},
            "tool_response": "Created the file."}


def v3_shell(sid, command, code=0, out=""):
    return {"hook_event_name": "PostToolUse", "cwd": CWD, "session_id": sid, "tool_name": "execute_pwsh",
            "tool_input": {"command": command, "description": "", "cwd": None, "run_in_background": False},
            "tool_response": f"Output:\n{out}\r\n\n\nExit Code: {code}"}


def test_v3_unverified_change_blocks():
    _, out = turn("v1", v3_write("v1", "src/auth.py", "str_replace"))
    assert json.loads(out)["decision"] == "block"


def test_v3_passing_check_lets_stop_through():
    _, out = turn("v2", v3_write("v2", "src/auth.py"), v3_shell("v2", "python -m pytest -q", 0, "3 passed in 0.1s"))
    assert out == ""


def test_v3_failing_check_still_blocks():
    _, out = turn("v3", v3_write("v3", "src/auth.py", "fs_append"), v3_shell("v3", "python -m pytest -q", 1, "1 failed"))
    assert json.loads(out)["decision"] == "block"


def test_prompt_carries_router_signal():
    data = tempfile.mkdtemp(prefix="fz_kiro_data_")
    context = run("prompt", prompt("r1", "Find out why the login test fails"), data)
    assert "[fablize:investigation]" in context and "fablize gate" in context
    assert "follow /" not in context or os.name != "nt"  # Windows tools need C:/..., not /c/...


def test_garbage_input_fails_open():
    data = tempfile.mkdtemp(prefix="fz_kiro_data_")
    for event in ("prompt", "post", "stop", "unknown"):
        env = dict(os.environ, FABLIZE_DATA=data)
        p = subprocess.run([sys.executable, str(ADAPTER), event], input=b"not json",
                           capture_output=True, env=env, timeout=60)
        assert p.returncode == 0 and p.stdout == b""


# Round 3 review: each of these let an unverified change through the stop check.
def test_piped_failing_check_blocks():
    # `pytest | tail` exits 0 with "1 failed": Claude Code's payload carries no exit
    # code on success, so the gate reads the verdict from the text. A forced
    # exit_code=0 skipped that and recorded a pass.
    _, out = turn("r1", write("r1", "src/auth.py"), shell("r1", "python -m pytest -q | tail -5", 0, "1 failed, 3 passed"))
    assert json.loads(out)["decision"] == "block"


def test_v3_background_run_is_not_a_check():
    p = v3_shell("r2", "python -m pytest -q")
    p["tool_input"]["run_in_background"] = True
    p["tool_response"] = "Started background process 1234"
    _, out = turn("r2", v3_write("r2", "src/auth.py"), p)
    assert json.loads(out)["decision"] == "block"


def test_v3_result_without_exit_code_is_not_a_pass():
    p = v3_shell("r3", "python -m pytest -q")
    p["tool_response"] = "Output:\n5 passed\n\nCommand timed out after 30s"
    _, out = turn("r3", v3_write("r3", "src/auth.py"), p)
    assert json.loads(out)["decision"] == "block"


def test_v2_text_item_result_is_read():
    p = shell("r4", "python -m pytest -q")
    p["tool_response"] = {"items": [{"Text": "exit code: 1\n1 failed, 3 passed"}]}
    _, out = turn("r4", write("r4", "src/auth.py"), p)
    assert json.loads(out)["decision"] == "block"


# v3 runs the stop hook once per prompt (2.37.1 probe: v2 3 calls, v3 1). Its one
# block says so, and an unverified end is carried to the next prompt.
def v3_stop(sid):
    return {"hook_event_name": "Stop", "cwd": CWD, "session_id": sid}


def test_v3_block_says_it_comes_once_v2_does_not():
    data = tempfile.mkdtemp(prefix="fz_kiro_data_")
    run("prompt", prompt("o1", DEEP), data)
    run("post", v3_write("o1", "src/auth.py"), data)
    v3, v2 = json.loads(run("stop", v3_stop("o1"), data)), json.loads(run("stop", stop("o1"), data))
    assert v3["decision"] == v2["decision"] == "block"  # both within MAX_STOP_BLOCKS
    v3, v2 = v3["reason"], v2["reason"]
    assert "comes once, not twice" in v3 and "comes once" not in v2
    assert "repeats at most twice" not in v3 and "repeats at most twice" in v2  # replaced, not appended


def test_unverified_end_is_carried_to_the_next_prompt_once():
    data = tempfile.mkdtemp(prefix="fz_kiro_data_")
    run("prompt", prompt("c1", DEEP), data)
    run("post", v3_write("c1", "src/auth.py"), data)
    assert json.loads(run("stop", v3_stop("c1"), data))["decision"] == "block"  # the one v3 block
    nxt = run("prompt", prompt("c1", "thanks"), data)  # v3 ended the turn there
    assert "previous turn ended" in nxt and "src/auth.py" in nxt
    assert "previous turn ended" not in run("prompt", prompt("c1", "next"), data)  # said once


def test_verified_or_other_session_end_carries_nothing():
    data = tempfile.mkdtemp(prefix="fz_kiro_data_")
    run("prompt", prompt("c2", DEEP), data)
    run("post", v3_write("c2", "src/auth.py"), data)
    run("post", v3_shell("c2", "python -m pytest -q", 0, "3 passed in 0.1s"), data)
    assert run("stop", v3_stop("c2"), data) == ""
    assert "previous turn ended" not in run("prompt", prompt("c2", "next"), data)
    run("prompt", prompt("c3", DEEP), data)
    run("post", v3_write("c3", "src/auth.py"), data)
    assert "previous turn ended" not in run("prompt", prompt("c4", "hello"), data)  # another session


def test_v2_capped_end_is_carried_too():
    data = tempfile.mkdtemp(prefix="fz_kiro_data_")
    run("prompt", prompt("c5", DEEP), data)
    run("post", write("c5", "src/auth.py"), data)
    for _ in range(3):  # block, block, then the cap lets it through with a warning
        run("stop", stop("c5"), data)
    assert "previous turn ended" in run("prompt", prompt("c5", "next"), data)


def test_carried_note_never_pushes_a_check_the_user_ruled_out():
    data = tempfile.mkdtemp(prefix="fz_kiro_data_")
    run("prompt", prompt("f1", DEEP + ". Do not run any tests."), data)
    run("post", v3_write("f1", "src/auth.py"), data)
    run("stop", v3_stop("f1"), data)
    nxt = run("prompt", prompt("f1", "ok, go on"), data)
    assert "previous turn ended" in nxt and "verify it" not in nxt


def test_no_carried_note_without_a_session_or_in_the_holdout_off_arm():
    data = tempfile.mkdtemp(prefix="fz_kiro_data_")
    run("prompt", prompt("", DEEP), data)  # no session id: the ledger is keyed on cwd
    run("post", v3_write("", "src/auth.py"), data)
    assert "previous turn ended" not in run("prompt", prompt("", "hello"), data)
    sys.path.insert(0, str(ADAPTER.parent.parent / "scripts" / "shadow"))
    from shadow_logger import holdout_arm
    sid = next(f"h{i}" for i in range(500) if holdout_arm(f"h{i}") == "off")
    env = dict(os.environ, FABLIZE_DATA=data, PYTHONUTF8="1", FABLIZE_HOLDOUT="1")
    def held(event, payload):
        return subprocess.run([sys.executable, str(ADAPTER), event], input=json.dumps(payload).encode(),
                              capture_output=True, env=env, timeout=60).stdout.decode("utf-8")
    held("prompt", prompt(sid, DEEP))
    held("post", v3_write(sid, "src/auth.py"))
    assert held("stop", v3_stop(sid)).strip() == ""  # the off arm: no gate output
    assert "previous turn ended" not in held("prompt", prompt(sid, "next"))


def test_carried_note_is_logged_for_measurement():
    data = tempfile.mkdtemp(prefix="fz_kiro_data_")
    run("prompt", prompt("e1", DEEP), data)
    run("post", v3_write("e1", "src/auth.py"), data)
    run("stop", v3_stop("e1"), data)
    run("prompt", prompt("e1", "next"), data)
    events = Path(data, "history", "events.jsonl").read_text(encoding="utf-8")
    assert '"kiro_carried_gap"' in events and "auth.py" not in events
