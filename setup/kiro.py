#!/usr/bin/env python3
"""fablize for Kiro CLI (issue #21). Python only, so it runs the same from bash,
PowerShell or cmd.exe:  python setup/kiro.py global|local|uninstall

Kiro has no Claude Code plugin system: hooks/hooks.json never registers there and
${CLAUDE_PLUGIN_ROOT} never exists. This copies the runtime to ~/.fablize/kiro and
writes:
  ~/.kiro/agents/fablize.json      an agent whose hooks run hooks/kiro.py (router
                                   + observation gate), for the v2 and v3 engines
  ~/.kiro/skills/fablize/SKILL.md  the skill with absolute paths and no Claude
                                   onboarding step (no AskUserQuestion on Kiro)
  <steering>/fablize.md            the always-on operating block; steering is what
                                   Kiro loads in full every session, like CLAUDE.md
                                   (global: ~/.kiro/steering, local: ./.kiro/steering)

Commands are written with bash when bash works here, Python otherwise:
  bash    `bash "<rt>/hooks/py.sh" "<script>" …`  py.sh picks python3/python/py at run
          time, so a later Python reinstall does not break the hooks
  python  `<python> "<script>" …`                  for hosts without a usable bash
"Works" means a real bash (not Windows' WSL launcher or a Store stub) on the PATH
Kiro will have (on Windows the saved PATH, not this shell's) that runs py.sh and
gets an answer from Python. Either way the first word is a
bare name and the arguments are quoted, which PowerShell (Kiro v2's hook runner
and both engines' shell tool on Windows), cmd.exe (v3's hook runner) and sh all
run unchanged. A quoted first word does not: PowerShell prints it instead.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Path.home(), not $HOME: on Windows Kiro reads the profile dir (USERPROFILE), and
# a HOME set by Git Bash/MSYS/Emacs can point elsewhere, so the agent would land
# where Kiro never looks. ledger.py and shadow_logger.py use Path.home() too.
HOME = Path.home()
KIRO = HOME / ".kiro"
RT = HOME / ".fablize" / "kiro"
AGENT = KIRO / "agents" / "fablize.json"
SKILL = KIRO / "skills" / "fablize" / "SKILL.md"
MARKER = "FABLIZE-KIRO"  # in every file this script writes; uninstall removes only those
STEERINGS = HOME / ".fablize" / "kiro-steering.txt"  # steering files written (global and every local)
GOALS_CMD = re.compile(r'bash "(?:\$\{CLAUDE_PLUGIN_ROOT\}|__PLUGIN_ROOT__)/hooks/py\.sh" '
                       r'"(?:\$\{CLAUDE_PLUGIN_ROOT\}|__PLUGIN_ROOT__)/scripts/goals\.py"')


def _runs(path: str) -> bool:
    try:  # also rejects the WindowsApps python3 stub, which exits non-zero
        return subprocess.run([path, "-c", "import sys; sys.exit(sys.version_info < (3, 8))"],
                              capture_output=True, timeout=30).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def interpreter() -> str:
    """A Python 3.8+ command usable as the first word of a command line."""
    names = ("python", "py", "python3") if os.name == "nt" else ("python3", "python")
    path = host_path()  # the name must resolve where Kiro runs the hooks, not just here
    for name in names:
        found = shutil.which(name, path=path)
        if found and _runs(found):
            return name
    exe = Path(sys.executable).as_posix()
    if " " not in exe:
        return exe
    # ponytail: no PATH entry and a path with spaces; a PowerShell/cmd-specific quoting variant would lift this
    sys.exit("fablize: put Python 3.8+ on PATH (python, py or python3) and re-run.")


def host_path() -> str:
    """The PATH Kiro will run hooks with, which need not be this process's.

    On Windows a new PowerShell/cmd (and Kiro started from it) gets the PATH saved
    in the registry. Run from Git Bash, this script sees Git's usr/bin too: it found
    bash, wrote `bash ...` hooks, and Kiro started from PowerShell could not run one
    of them (observed: Git for Windows puts only Git\\cmd on the saved PATH).
    FABLIZE_HOST_PATH overrides (tests)."""
    if os.environ.get("FABLIZE_HOST_PATH") is not None:
        return os.environ["FABLIZE_HOST_PATH"]
    if os.name != "nt":
        return os.environ.get("PATH", "")
    import winreg
    parts = []
    for root, key in ((winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
                      (winreg.HKEY_CURRENT_USER, "Environment")):
        try:
            with winreg.OpenKey(root, key) as k:
                parts.append(os.path.expandvars(winreg.QueryValueEx(k, "Path")[0]))
        except OSError:
            pass
    return os.pathsep.join(parts) if parts else os.environ.get("PATH", "")


def real_bash(path: str) -> str | None:
    """The bash a shell on `path` would run, unless it is Windows' WSL launcher or a
    Store stub (a different filesystem). Tests use this too, so they agree with it."""
    found = shutil.which("bash", path=path)
    if not found or (os.name == "nt" and any(s in found.lower() for s in ("system32", "windowsapps"))):
        return None
    return found


def bash_works(py_sh: Path) -> bool:
    """A real bash on the host PATH that runs py.sh and gets an answer from Python,
    both found on that same PATH: a Python only this shell (a venv, Git Bash) sees
    would pass here and leave every hook silently doing nothing under Kiro."""
    path = host_path()
    found = real_bash(path)
    if not found:
        return False
    try:
        out = subprocess.run([found, py_sh.as_posix(), "-c", "print(1)"], capture_output=True, timeout=60,
                             env=dict(os.environ, PATH=path))
    except (OSError, subprocess.SubprocessError):
        return False
    return out.stdout.strip() == b"1"


def write(path: Path, text: str, ts: int) -> None:
    if path.exists():
        ours = MARKER in path.read_text(encoding="utf-8", errors="replace")
        if not ours:  # a hand-made agent / steering / skill: keep it beside ours
            backup = path.with_name(f"{path.name}.fablize-bak.{ts}")
            path.replace(backup)
            print(f"  backup: {backup}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:  # write_text(newline=) is 3.10+
        fh.write(text)
    print(f"  ✓ {path}")


def install(steering: Path) -> None:
    rt = RT.as_posix()
    ts = int(time.time())

    # A fixed copy, not this checkout: the checkout can move or be replaced, and
    # every path below would dangle. Re-run after `git pull` to refresh it.
    # Staged, then swapped: deleting first left no runtime at all when a copy or a
    # delete failed (a Kiro session holding a file open on Windows), while the agent
    # file still pointed at it.
    stage, old = RT.with_name("kiro.new"), RT.with_name("kiro.old")
    try:  # a leftover kiro.new (a run that died mid-copy, a file held open) made copytree raise
        for leftover in (stage, old):
            if leftover.exists():
                shutil.rmtree(leftover)
        skip = shutil.ignore_patterns("__pycache__", "*.pyc")
        for part in ("hooks", "scripts", "packs"):
            shutil.copytree(ROOT / part, stage / part, ignore=skip)
    except OSError as exc:
        sys.exit(f"fablize: could not stage the runtime in {stage} ({exc}). Close running Kiro sessions and re-run.")
    try:
        if RT.exists():
            RT.rename(old)
        stage.rename(RT)
    except OSError as exc:
        if old.exists() and not RT.exists():
            old.rename(RT)  # put the previous runtime back
        sys.exit(f"fablize: could not replace {RT} ({exc}). Close running Kiro sessions and re-run.")
    shutil.rmtree(old, ignore_errors=True)
    # No .claude-plugin/plugin.json in the copy: gate_prompt's upgrade notice reads
    # it and would tell a Kiro user to re-run the Claude Code setup.
    print(f"  ✓ runtime: {RT}")

    if bash_works(RT / "hooks" / "py.sh"):
        run, how = f'bash "{rt}/hooks/py.sh"', "bash (py.sh picks the Python interpreter at run time)"
    else:
        run = interpreter()
        how = f"python (`{run}`; no usable bash here; re-run this after installing or changing Python)"
    print(f"  ✓ commands: {how}")

    def hook(event: str) -> list:
        return [{"command": f'{run} "{rt}/hooks/kiro.py" {event}', "timeout_ms": 30000}]

    # One "*" entry, not one per tool name: Kiro matches aliases (v2 `shell` is also
    # `execute_bash`), so per-name entries ran the hook 2-3 times per call (observed),
    # and one failed check counted as "the same failure repeated". kiro.py ignores
    # tools the gate does not read.
    # ponytail: every tool call starts one Python (~0.1-0.3 s on Windows); dedupe by tool name if that shows up.
    post = [dict(hook("post")[0], matcher="*")]
    config = {
        "name": "fablize",
        "description": f"Kiro agent with fablize hooks ({MARKER}): routes debugging/render prompts to "
                       "their checklist, records file changes and checks, and blocks stopping after an "
                       "unverified code change (at most twice per prompt).",
        "tools": ["*"],
        "includeMcpJson": True,
        "hooks": {"userPromptSubmit": hook("prompt"), "postToolUse": post, "stop": hook("stop")},
    }
    write(AGENT, json.dumps(config, indent=2) + "\n", ts)

    goals = f'{run} "{rt}/scripts/goals.py"'
    text = (ROOT / "skills" / "fablize" / "SKILL.md").read_text(encoding="utf-8")
    zero = (f"## 0. Kiro CLI\n\nInstalled by `setup/kiro.py` ({MARKER}); there is no onboarding step. "
            "Run every command below as written from your shell tool: PowerShell, cmd and bash all "
            f"accept it. Commands run through {how.split(' (')[0]}.\n\n")
    text = re.sub(r"## 0\. .*?(?=## 1\. )", lambda m: zero, text, flags=re.S)
    text = re.sub(r"## Install .*\Z", lambda m: "## Uninstall\n\n`python <fablize checkout>/setup/kiro.py uninstall`\n",
                  text, flags=re.S)
    text = GOALS_CMD.sub(lambda m: goals, text).replace("${CLAUDE_PLUGIN_ROOT}", rt)
    write(SKILL, text, ts)

    block = (ROOT / "setup" / "fablize-block.md").read_text(encoding="utf-8").strip()
    block = GOALS_CMD.sub(lambda m: goals, block).replace("__PLUGIN_ROOT__", rt)
    block = block.replace(" with the Bash tool (py.sh picks python3/python/py)", " from your shell tool")
    write(steering, f"<!-- {MARKER} -->\n{block}\n", ts)
    # Every steering file written, so uninstall can remove the ones in other projects
    # too: left behind, they point at the runtime uninstall deletes.
    known = STEERINGS.read_text(encoding="utf-8").splitlines() if STEERINGS.exists() else []
    if str(steering) not in known:
        with open(STEERINGS, "a", encoding="utf-8") as fh:
            fh.write(f"{steering}\n")

    kiro_cli = shutil.which("kiro-cli")
    if kiro_cli:
        ok = subprocess.run([kiro_cli, "agent", "validate", "--path", str(AGENT)], capture_output=True).returncode == 0
        print("  ✓ kiro-cli agent validate" if ok else "  ! kiro-cli agent validate failed")
    print("fablize for Kiro installed. Start it with: kiro-cli chat --agent fablize")
    print("  (or: kiro-cli agent set-default fablize)")


def uninstall() -> None:
    listed = [Path(p) for p in STEERINGS.read_text(encoding="utf-8").splitlines() if p] if STEERINGS.exists() else []
    candidates = [AGENT, SKILL, KIRO / "steering" / "fablize.md", Path.cwd() / ".kiro" / "steering" / "fablize.md"]
    for path in dict.fromkeys(candidates + listed):
        if not path.exists():
            continue
        if MARKER in path.read_text(encoding="utf-8", errors="replace"):
            path.unlink()
            print(f"  removed {path}")
        else:
            print(f"  kept    {path} (not written by kiro.py)")
    try:
        SKILL.parent.rmdir()
    except OSError:
        pass
    # kiro.old / kiro.new: left by a reinstall that could not delete a locked file.
    for runtime in (RT, RT.with_name("kiro.old"), RT.with_name("kiro.new")):
        if runtime.exists():
            shutil.rmtree(runtime, ignore_errors=True)
            print(f"  removed {runtime}" if not runtime.exists() else f"  could not fully remove {runtime} (files in use?)")
    if STEERINGS.exists():
        STEERINGS.unlink()
    print("fablize for Kiro uninstalled.")


def main(argv: list) -> int:
    mode = argv[0] if argv else ""
    if mode == "uninstall":
        uninstall()
    elif mode in ("global", "local"):
        install(KIRO / "steering" / "fablize.md" if mode == "global" else Path.cwd() / ".kiro" / "steering" / "fablize.md")
    else:
        print("usage: python setup/kiro.py global|local|uninstall")
        return 1
    return 0


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # "✓" on a cp949 console
        except (AttributeError, ValueError):
            pass
    raise SystemExit(main(sys.argv[1:]))
