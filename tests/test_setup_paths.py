#!/usr/bin/env python3
"""setup.sh must not bake a version-pinned path into CLAUDE.md.

The injected block used to carry $CLAUDE_PLUGIN_ROOT, which is
.../cache/<marketplace>/<plugin>/<version>. After an upgrade that directory is
gone and every path in the block dangles, with nothing to notice or repair it —
observed live: the block pointed at 2.1.1 while 2.1.2 was installed.

Drives the real setup.sh against a temp HOME and a fake plugin root, then checks
the injected block, the copied assets, and the recorded version. Also exercises
gate_prompt.stale_setup_notice() both ways. Exit non-zero on any mismatch.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "hooks"))

CHECKS = []


def check(label, got, want):
    CHECKS.append((label, got, want))


def fake_plugin_root(base: Path, version: str) -> Path:
    """A plugin tree laid out like the real versioned cache directory."""
    root = base / "cache" / "fablize" / "fablize" / version
    (root / "scripts").mkdir(parents=True)
    (root / "packs").mkdir()
    (root / "setup").mkdir()
    (root / ".claude-plugin").mkdir()
    (root / "scripts" / "goals.py").write_text(f"# goals {version}\n", encoding="utf-8")
    # Mirror the real pack set so "every referenced asset exists" is meaningful.
    for pack in (REPO / "packs").glob("*.txt"):
        (root / "packs" / pack.name).write_text(f"{pack.name} {version}\n", encoding="utf-8")
    (root / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "fablize", "version": version}), encoding="utf-8"
    )
    shutil.copy(REPO / "setup" / "setup.sh", root / "setup" / "setup.sh")
    shutil.copy(REPO / "setup" / "fablize-block.md", root / "setup" / "fablize-block.md")
    return root


def run_setup(root: Path, home: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    # Redirect every home-ish variable: bash reads HOME, but Python's
    # expanduser() on Windows prefers USERPROFILE, then HOMEDRIVE+HOMEPATH.
    env.update(HOME=str(home), USERPROFILE=str(home),
               CLAUDE_PLUGIN_ROOT=str(root), CLAUDE_CONFIG_DIR=str(home / ".claude"))
    for key in ("HOMEDRIVE", "HOMEPATH"):
        env.pop(key, None)
    # encoding= explicitly: the console codepage (cp949 here) cannot decode the
    # script's ✓ output, and the default would raise instead of running.
    return subprocess.run(
        ["bash", str(root / "setup" / "setup.sh"), "global"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env, cwd=str(root), timeout=120,
    )


with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    home = tmp / "home"
    (home / ".claude").mkdir(parents=True)
    root = fake_plugin_root(tmp, "9.9.9")

    proc = run_setup(root, home)
    check("setup.sh exit code", proc.returncode, 0)
    if proc.returncode != 0:
        print("--- setup.sh stdout ---\n" + proc.stdout)
        print("--- setup.sh stderr ---\n" + proc.stderr)
        raise SystemExit(1)

    claude_md = (home / ".claude" / "CLAUDE.md").read_text(encoding="utf-8")
    lib = home / ".fablize" / "lib"

    # bash normalizes to forward slashes; compare in that form.
    lib_fwd = str(lib).replace("\\", "/")

    check("block injected", "FABLIZE:BEGIN" in claude_md, True)
    check("no versioned cache path in block",
          bool(re.search(r"cache[/\\]fablize[/\\]fablize[/\\]\d", claude_md)), False)
    check("block points at stable lib dir", lib_fwd in claude_md, True)
    check("goals.py copied", (lib / "scripts" / "goals.py").exists(), True)
    check("packs copied", (lib / "packs" / "investigation-protocol.txt").exists(), True)
    check("copied goals.py is this version",
          (lib / "scripts" / "goals.py").read_text(encoding="utf-8").strip(), "# goals 9.9.9")

    progress = json.loads((home / ".fablize" / "progress.json").read_text(encoding="utf-8"))
    check("recorded version comes from manifest", progress.get("version"), "9.9.9")

    # Every path the block names must exist — the whole point of the fix.
    referenced = re.findall(re.escape(lib_fwd) + r"[^\s`]*", claude_md)
    check("block names at least one asset", len(referenced) > 0, True)
    check("every referenced asset exists",
          sorted({p for p in referenced if not Path(p).exists()}), [])

    # Re-running after an upgrade must refresh the copies in place.
    root2 = fake_plugin_root(tmp, "9.9.10")
    proc2 = run_setup(root2, home)
    check("re-run exit code", proc2.returncode, 0)
    check("assets refreshed on upgrade",
          (lib / "scripts" / "goals.py").read_text(encoding="utf-8").strip(), "# goals 9.9.10")
    claude_md2 = (home / ".claude" / "CLAUDE.md").read_text(encoding="utf-8")
    check("block still version-free",
          bool(re.search(r"cache[/\\]fablize[/\\]fablize[/\\]\d", claude_md2)), False)
    check("block not duplicated", claude_md2.count("FABLIZE:BEGIN"), 1)

    # An older copy of setup.sh must refuse to overwrite a newer setup. The
    # /fablize:setup command has its plugin path expanded when a session loads
    # it, so a session opened before an upgrade still runs the previous version
    # — and the plugin cache keeps that version on disk, so it works. Observed
    # live: 2.1.5 applied at 16:44, silently rolled back to 2.1.3 at 16:51 by a
    # session that had been open since before the upgrade.
    # Count backups before and after rather than asserting a total: the backup
    # name carries a whole-second timestamp, so two runs inside one second share
    # a filename and an absolute count is flaky.
    backups_before = len(list((home / ".claude").glob("CLAUDE.md.fablize-bak.*")))
    proc3 = run_setup(root, home)          # root is 9.9.9; 9.9.10 is recorded
    check("downgrade refused", proc3.returncode, 1)
    check("downgrade says both versions",
          "9.9.9" in proc3.stdout and "9.9.10" in proc3.stdout, True)
    check("downgrade names the escape hatch",
          "FABLIZE_ALLOW_DOWNGRADE" in proc3.stdout, True)
    # Nothing may be touched on the way out — the old assets stay unwritten.
    check("assets untouched by refusal",
          (lib / "scripts" / "goals.py").read_text(encoding="utf-8").strip(), "# goals 9.9.10")
    check("record untouched by refusal",
          json.loads((home / ".fablize" / "progress.json").read_text(encoding="utf-8"))["version"],
          "9.9.10")
    check("no extra CLAUDE.md backup from the refusal",
          len(list((home / ".claude").glob("CLAUDE.md.fablize-bak.*"))), backups_before)

    # Re-applying the SAME version is not a downgrade, and the escape hatch works.
    check("same version still allowed", run_setup(root2, home).returncode, 0)
    env_down = dict(os.environ, FABLIZE_ALLOW_DOWNGRADE="1")
    proc5 = subprocess.run(
        ["bash", str(root / "setup" / "setup.sh"), "global"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env={**env_down, "HOME": str(home), "USERPROFILE": str(home),
             "CLAUDE_PLUGIN_ROOT": str(root), "CLAUDE_CONFIG_DIR": str(home / ".claude")},
        cwd=str(root), timeout=120,
    )
    check("explicit downgrade allowed", proc5.returncode, 0)
    check("explicit downgrade records the old version",
          json.loads((home / ".fablize" / "progress.json").read_text(encoding="utf-8"))["version"],
          "9.9.9")

    # staleness notice: recorded version behind the running plugin
    import gate_prompt

    progress_path = home / ".fablize" / "progress.json"
    real_expanduser = os.path.expanduser
    os.path.expanduser = lambda p: (str(progress_path) if p == "~/.fablize/progress.json"
                                    else real_expanduser(p))
    try:
        running = json.loads((REPO / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["version"]
        progress_path.write_text(json.dumps({"version": "0.0.1"}), encoding="utf-8")
        check("notice when versions differ", "0.0.1" in gate_prompt.stale_setup_notice(), True)
        progress_path.write_text(json.dumps({"version": running}), encoding="utf-8")
        check("silent when versions match", gate_prompt.stale_setup_notice(), "")
        progress_path.write_text("{not json", encoding="utf-8")
        check("silent when progress unreadable", gate_prompt.stale_setup_notice(), "")
    finally:
        os.path.expanduser = real_expanduser


def main():
    bad = 0
    for label, got, want in CHECKS:
        ok = got == want
        if not ok:
            bad += 1
        print(f"{'OK  ' if ok else 'FAIL'} {str(got)[:40]:<42} want={str(want)[:24]:<26} {label}")
    print("-" * 78)
    if bad:
        print(f"RESULT: {bad}/{len(CHECKS)} mismatched.")
        return 1
    print(f"RESULT: all {len(CHECKS)} checks match.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
