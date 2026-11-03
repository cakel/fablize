#!/usr/bin/env python3
"""fablize on Kiro CLI: one adapter for the agent hooks setup/kiro.py writes.

Kiro hooks send their own payloads (captured from kiro-cli 2.33's v2 engine and
the v3 engine, KiroCLI/2.37.1, Windows, 2026-11-02):

  prompt  {prompt, session_id, cwd}      stdout goes into the context verbatim
  post    v2: write {command, path, ...} / shell, result in tool_response.items[].Json
          v3: fs_write / fs_append {path, text}, str_replace {path, oldStr, newStr},
              execute_pwsh / execute_bash {command}, result a string "...Exit Code: N"
          stdout is not shown to the model (v2)
  stop    {"decision": "block", "reason"} on stdout is honoured by both; exit 2 is not.
          v3 runs it once per prompt, so its block says so and the next prompt
          carries an unverified end (carried_gap)

This rewrites each payload into the Claude Code shape the gates already parse,
runs the same gate scripts, and turns their output back into what Kiro reads.
The gate logic itself is unchanged. No bash anywhere: Kiro runs hook commands in
PowerShell (v2) or cmd.exe (v3) on Windows.

Usage: kiro.py prompt|post|stop   (JSON on stdin). Fails open: any error -> no output.
"""

from __future__ import annotations

import importlib
import io
import json
import os
import re
import sys
from pathlib import Path

HOOKS = Path(__file__).resolve().parent
sys.path.insert(0, str(HOOKS))
WRITE_TOOLS = {"write", "fs_write", "fs_append", "str_replace"}
# v2 `shell` is PowerShell on Windows, bash elsewhere; v3 names the shell. The gate
# parses both labels the same way; the label only keeps the record honest.
SHELL_TOOLS = {"shell": None, "execute_bash": "Bash", "execute_pwsh": "PowerShell", "execute_cmd": "PowerShell"}
# Windows crash codes are printed in hex ("exit code: 0xc0000005"). Reading only
# the first digit run turned that into 0: a crashed check counted as a pass.
_CODE = r"(0[xX][0-9a-fA-F]+|-?\d+)"
_EXIT_RE = re.compile(_CODE)
_V3_EXIT_RE = re.compile(r"Exit Code:\s*" + _CODE + r"\s*$")
_TEXT_EXIT_RE = re.compile(r"exit code:\s*" + _CODE, re.I)


def _int(text: str) -> int:
    return int(text, 16) if text.lower().startswith("0x") else int(text)


def gate(module: str, payload: dict) -> dict:
    """Run a gate's main() in this process with the payload as its stdin.

    In-process, not a child Python: every hook already costs one interpreter start
    (two in bash mode), and on Windows each start is 0.1-0.3 s per tool call. The
    hook's own Kiro timeout bounds a hung gate. Any error -> {} (fail open), so a
    failed gate never takes the router's text down with it."""
    saved = sys.stdin, sys.stdout
    out = io.StringIO()
    try:
        sys.stdin, sys.stdout = io.StringIO(json.dumps(payload)), out
        importlib.import_module(module).main()
    except BaseException:  # noqa: BLE001 — SystemExit included
        return {}
    finally:
        sys.stdin, sys.stdout = saved
    lines = out.getvalue().strip().splitlines()
    try:
        result = json.loads(lines[-1]) if lines else {}
    except ValueError:
        return {}
    return result if isinstance(result, dict) else {}


def carried_gap(payload: dict) -> str:
    """The previous turn's unverified end, said at the next prompt.

    v3 runs the stop hook once per prompt (KiroCLI 2.37.1: a probe hook that blocks
    every time ran 3 times on v2, once on v3; acp-server.js skips agentStop hooks
    once `onAgentStopHooksExecuted` is set). After that one block nothing reaches the
    model or the user: postToolUse stdout and hook stderr are shown to neither, and
    SessionEnd is session teardown, not a turn end. So a turn can end with a change
    no check covered and no warning. The prompt hook's stdout does reach the model:
    read the ledger before gate_prompt resets it, and have the model say so first.
    Any error -> "" (fail open)."""
    try:
        sys.path.insert(0, str(HOOKS.parent / "scripts" / "gate"))
        from ledger import load_ledger
        from verify_state import should_block_stop, warning_after_max_blocks
        ledger = load_ledger(payload)
        if not (should_block_stop(ledger)[0] or warning_after_max_blocks(ledger)):
            return ""
        latest = str(ledger.get("last_change") or "") or "a change"
        return (f"fablize gate: the previous turn ended with a change that no observed check covered "
                f"(latest: `{latest}`). Tell the user that in one line before anything else, and "
                "verify it if this prompt continues the same work.")
    except Exception:  # noqa: BLE001
        return ""


# Said only where the stop hook runs once per prompt. The v3 Stop payload has no
# assistant_response (v2 sends it on every call), which is how that is told apart.
ONE_SHOT = (" On this host this reminder comes once, not twice: the next time you stop, the "
            "turn ends, unverified unless a check ran.")


def _base(data: dict, event: str) -> dict:
    return {"hook_event_name": event, "session_id": data.get("session_id") or "",
            "cwd": data.get("cwd") or ""}


def to_claude_post(data: dict) -> dict | None:
    """A Kiro postToolUse payload as the PostToolUse/PostToolUseFailure the gate parses."""
    name = str(data.get("tool_name") or "")
    tool_input = data.get("tool_input") if isinstance(data.get("tool_input"), dict) else {}
    if name in WRITE_TOOLS:
        path = os.path.expanduser(str(tool_input.get("path") or ""))  # `~/scratch/x.py` is not <cwd>/~/...
        cwd = str(data.get("cwd") or "")
        if path and cwd and not os.path.isabs(path):
            path = os.path.join(cwd, path)
        out = _base(data, "PostToolUse")
        # v2 create / v3 fs_write -> Write; the rest edit in place. Only the path matters to the gate.
        create = tool_input.get("command") == "create" or name == "fs_write"
        out.update(tool_name="Write" if create else "Edit",
                   tool_input={"file_path": path}, tool_response={})
        return out
    if name in SHELL_TOOLS:
        response = data.get("tool_response")
        background = bool(tool_input.get("run_in_background"))
        if isinstance(response, str):  # v3: "Output:\n...\n\nExit Code: 0"
            match = _V3_EXIT_RE.search(response)
            result = {"stdout": response[:match.start()] if match else response}
            # Every finished v3 run ends with "Exit Code: N"; without it the run timed
            # out or was cancelled, and the "5 passed" printed before that is no pass.
            unfinished = not match and not background
        else:  # v2: {"items": [{"Json": {"exit_status": "exit code: 0", "stdout", "stderr"}}]}
            result = {}
            items = response.get("items") if isinstance(response, dict) else None
            for item in items or []:
                if isinstance(item, dict) and isinstance(item.get("Json"), dict):
                    result = item["Json"]
                    break
            if result:
                status = str(result.get("exit_status") or "")
                match = _EXIT_RE.search(status)
            else:  # a plain-text result (an error, truncated output): read its text
                status = "\n".join(str(i["Text"]) for i in items or [] if isinstance(i, dict) and "Text" in i)
                result = {"stdout": status}
                match = _TEXT_EXIT_RE.search(status)
            unfinished = not match and bool(status.strip())  # "terminated by signal", an error text: no pass
        code = _int(match.group(1)) if match else (-1 if unfinished else None)
        tool_response = {"stdout": str(result.get("stdout") or ""), "stderr": str(result.get("stderr") or "")}
        # Exit 0 is NOT written: Claude Code's payload carries no exit code on success,
        # and the gate then reads the verdict from the text, which is how
        # `pytest | tail` (exit 0, "1 failed") and `pytest; echo exit=$?` get caught.
        if code:
            tool_response["exit_code"] = code
        # Claude Code fires PostToolUseFailure for a non-zero exit; the gate keys
        # "a failed probe is not a failed check" off that event, so mirror it.
        out = _base(data, "PostToolUseFailure" if code else "PostToolUse")
        # The tool's own working dir (v2 working_dir, v3 cwd) decides what a
        # relative path in the command means, not the session's.
        # Only with a session id: without one the ledger is keyed on cwd, and the
        # prompt and stop hooks would read a different ledger than this one wrote.
        workdir = tool_input.get("working_dir") or tool_input.get("cwd")
        if workdir and out["session_id"]:
            out["cwd"] = str(workdir)
        # run_in_background kept: the gate does not count a launch as a check.
        out.update(tool_name=SHELL_TOOLS[name] or ("PowerShell" if os.name == "nt" else "Bash"),
                   tool_input={"command": str(tool_input.get("command") or ""), "run_in_background": background},
                   tool_response=tool_response)
        if code:
            out["error"] = tool_response["stderr"] or f"Exit code {code}"
        return out
    return None


def main(event: str, data: dict) -> str:
    """Return what to print on stdout for this Kiro event."""
    if event == "prompt":
        payload = _base(data, "UserPromptSubmit")
        payload["prompt"] = str(data.get("prompt") or "")
        carried = carried_gap(payload)  # before gate_prompt resets the ledger for this turn
        from router import route
        routed = route(payload["prompt"], payload["session_id"])
        result = gate("gate_prompt", payload)
        # Kiro injects stdout verbatim, so hand it the text, not the JSON around it.
        context = str((result.get("hookSpecificOutput") or {}).get("additionalContext") or "")
        return "\n".join(t for t in (carried, routed, context) if t)
    if event == "post":
        payload = to_claude_post(data)
        if payload:
            gate("gate_post_tool", payload)  # its notes are not shown on Kiro; the ledger is what counts
        return ""
    if event == "stop":
        payload = _base(data, "Stop")
        payload["last_assistant_message"] = str(data.get("assistant_response") or "")
        result = gate("gate_stop", payload)
        if result.get("decision") == "block":
            reason = str(result.get("reason") or "") + ("" if "assistant_response" in data else ONE_SHOT)
            return json.dumps({"decision": "block", "reason": reason}, ensure_ascii=True)
        if result.get("systemMessage"):
            sys.stderr.write(str(result["systemMessage"]) + "\n")
        return ""
    return ""


if __name__ == "__main__":
    try:
        raw = sys.stdin.buffer.read().decode("utf-8", "replace")
        data = json.loads(raw) if raw.strip() else {}
        text = main(sys.argv[1] if len(sys.argv) > 1 else "", data if isinstance(data, dict) else {})
        if text:  # bytes: run without py.sh there is no PYTHONUTF8, and cp949 cannot encode "—"
            sys.stdout.buffer.write((text + "\n").encode("utf-8"))
    except Exception:  # noqa: BLE001 — fail open, never break the user's session
        pass
    raise SystemExit(0)
