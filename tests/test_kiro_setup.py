#!/usr/bin/env python3
"""setup/kiro.py against a temp HOME: what it writes, that the hook commands it
writes actually run, with bash and without it, and that uninstall removes only
its own files."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import importlib.util

import pytest

REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("kiro_setup", REPO / "setup" / "kiro.py")
kiro_setup = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(kiro_setup)

# The installer's own rule, so the test and the installer agree on "has bash"
# (a WSL bash.exe first on PATH means python mode, not a failing bash test).
HAS_BASH = kiro_setup.real_bash(os.environ.get("PATH", "")) is not None
# PATH without any real bash: what a host with no bash looks like to the installer.
# System32 stays (cmd.exe lives there; its WSL bash.exe is rejected anyway).
NO_BASH_PATH = os.pathsep.join(d for d in os.environ.get("PATH", "").split(os.pathsep)
                               if not (d and kiro_setup.real_bash(d)))


def env_for(home, path):
    # Path.home() reads USERPROFILE on Windows and HOME elsewhere.
    env = dict(os.environ, HOME=str(home), USERPROFILE=str(home), FABLIZE_DATA=str(Path(home) / "data"), PATH=path,
               FABLIZE_HOST_PATH=path)  # the installer judges bash by the PATH Kiro will have
    env.pop("CLAUDE_PLUGIN_ROOT", None)
    return env


def installer(home, path, *args, cwd=None):
    p = subprocess.run([sys.executable, str(REPO / "setup" / "kiro.py"), *args], capture_output=True,
                       env=env_for(home, path), cwd=cwd or home, timeout=180)
    return p.returncode, (p.stdout + p.stderr).decode("utf-8", "replace")


def run_hook(home, path, command, payload):
    # Run the line as Kiro's hook runner does: through the OS shell (cmd.exe on
    # Windows, like v3; sh elsewhere).
    # cmd /d: skip AutoRun, which on a dev box may load files from the (redirected)
    # USERPROFILE and fail with exit 1 even though the hook itself succeeded.
    # A string, not a list: list2cmdline would escape the quotes as \" for cmd.
    argv = "cmd /d /c " + command if os.name == "nt" else command
    p = subprocess.run(argv, shell=os.name != "nt", input=json.dumps(payload).encode("utf-8"),
                       capture_output=True, env=env_for(home, path), cwd=home, timeout=120)
    assert p.returncode == 0, p.stderr
    return p.stdout.decode("utf-8", "replace")


@pytest.mark.parametrize("mode", ["bash", "python"])
def test_install_run_uninstall(mode):
    if mode == "bash" and not HAS_BASH:
        pytest.skip("no bash on this host")
    path = os.environ.get("PATH", "") if mode == "bash" else NO_BASH_PATH
    home = Path(tempfile.mkdtemp(prefix="fz_kiro_home_"))
    steering = home / ".kiro" / "steering" / "fablize.md"
    steering.parent.mkdir(parents=True)
    steering.write_text("hand-made\n", encoding="utf-8")  # not ours: must be backed up, not lost

    for _ in range(2):  # the second run swaps in a fresh runtime over the first
        code, out = installer(home, path, "global")
        assert code == 0, out
    assert f"commands: {mode}" in out, out
    assert not (home / ".fablize" / "kiro.new").exists() and not (home / ".fablize" / "kiro.old").exists()

    agent = json.loads((home / ".kiro" / "agents" / "fablize.json").read_text(encoding="utf-8"))
    skill = (home / ".kiro" / "skills" / "fablize" / "SKILL.md").read_text(encoding="utf-8")
    block = steering.read_text(encoding="utf-8")
    for text in (skill, block):
        assert ('bash "' in text) == (mode == "bash"), text  # bash commands only when bash works
        assert "${CLAUDE_PLUGIN_ROOT}" not in text and "__PLUGIN_ROOT__" not in text
        assert '/.fablize/kiro/scripts/goals.py"' in text
    assert "AskUserQuestion" not in skill and "## 1. Multi-story loop" in skill
    # The installer rewrites by exact text; if the source wording changes, these catch it.
    assert "## 0. Kiro CLI" in skill and "## Uninstall" in skill and "## Install" not in skill
    assert "FABLIZE:BEGIN" in block and "Bash tool" not in block
    assert [p.read_text(encoding="utf-8") for p in steering.parent.glob("fablize.md.fablize-bak.*")] == ["hand-made\n"]
    rt = home / ".fablize" / "kiro"
    assert not (rt / ".claude-plugin").exists()  # no Claude upgrade notice on Kiro

    commands = [e["command"] for hooks in agent["hooks"].values() for e in hooks]
    assert all(c.startswith("bash ") == (mode == "bash") for c in commands), commands
    assert all(not c.startswith('"') for c in commands), commands  # PowerShell runs no quoted first word
    assert [e["matcher"] for e in agent["hooks"]["postToolUse"]] == ["*"]  # per-name entries double-fire on aliases

    # One turn, driven through the commands exactly as written in the agent file.
    sid, cwd = "s1", str(home)
    ctx = run_hook(home, path, agent["hooks"]["userPromptSubmit"][0]["command"],
                   {"hook_event_name": "userPromptSubmit", "cwd": cwd, "session_id": sid,
                    "prompt": "Find out why the login test fails and fix it thoroughly"})
    assert "[fablize:investigation]" in ctx and "/.fablize/kiro/packs/" in ctx and not ctx.lstrip().startswith("{")
    run_hook(home, path, agent["hooks"]["postToolUse"][0]["command"],
             {"hook_event_name": "PostToolUse", "cwd": cwd, "session_id": sid, "tool_name": "fs_write",
              "tool_input": {"path": os.path.join(cwd, "app.py"), "text": "x = 1\n"}, "tool_response": "Created"})
    out = run_hook(home, path, agent["hooks"]["stop"][0]["command"],
                   {"hook_event_name": "Stop", "cwd": cwd, "session_id": sid})
    assert json.loads(out)["decision"] == "block"

    (home / ".fablize" / "kiro.old").mkdir()  # as a reinstall that hit a locked file leaves it
    code, out = installer(home, path, "uninstall")
    assert code == 0, out
    assert not (home / ".fablize" / "kiro.old").exists()
    assert not (home / ".kiro" / "agents" / "fablize.json").exists()
    assert not (home / ".kiro" / "skills" / "fablize").exists()
    assert not steering.exists() and not rt.exists()
    assert list(steering.parent.glob("fablize.md.fablize-bak.*"))  # the user's own copy stays
    shutil.rmtree(home, ignore_errors=True)


def test_uninstall_removes_local_steering_in_other_projects():
    # Left behind, a project's steering points at the runtime uninstall deletes.
    home = Path(tempfile.mkdtemp(prefix="fz_kiro_home_"))
    project = home / "proj"
    project.mkdir()
    code, out = installer(home, NO_BASH_PATH, "local", cwd=project)
    assert code == 0, out
    local = project / ".kiro" / "steering" / "fablize.md"
    assert local.exists()
    code, out = installer(home, NO_BASH_PATH, "uninstall")  # from home, not from the project
    assert code == 0, out
    assert not local.exists() and not (home / ".fablize" / "kiro").exists()
    shutil.rmtree(home, ignore_errors=True)


def test_requires_scope():
    home = Path(tempfile.mkdtemp(prefix="fz_kiro_home_"))
    code, out = installer(home, os.environ.get("PATH", ""))
    assert code == 1 and "usage" in out
