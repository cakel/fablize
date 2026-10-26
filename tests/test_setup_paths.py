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

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _bash import BASH  # noqa: E402

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
    (root / "hooks").mkdir()
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
    # setup.sh runs python through hooks/py.sh and copies it, with
    # finish-the-work.sh, to the stable lib dir.
    for hook in ("py.sh", "finish-the-work.sh"):
        shutil.copy(REPO / "hooks" / hook, root / "hooks" / hook)
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
        [BASH, str(root / "setup" / "setup.sh"), "global"],
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
    check("py.sh copied", (lib / "hooks" / "py.sh").exists(), True)
    check("finish-the-work.sh copied beside py.sh", (lib / "hooks" / "finish-the-work.sh").exists(), True)
    check("block runs goals.py through py.sh, not python3",
          ("hooks/py.sh" in claude_md, "python3 " in claude_md), (True, False))
    # bash prints its own path form (/c/Users/... on Git Bash), so match the tail.
    check("finish-the-work hint names the stable copy",
          ".fablize/lib/hooks/finish-the-work.sh" in proc.stdout and "cache" not in proc.stdout.split("finish-the-work.sh")[-2][-80:], True)
    check("copied goals.py is this version",
          (lib / "scripts" / "goals.py").read_text(encoding="utf-8").strip(), "# goals 9.9.9")

    progress = json.loads((home / ".fablize" / "progress.json").read_text(encoding="utf-8"))
    check("recorded version comes from manifest", progress.get("version"), "9.9.9")

    # Every path the block names must exist — the whole point of the fix.
    referenced = re.findall(re.escape(lib_fwd) + r"[^\s`\"]*", claude_md)
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
    # What this does NOT cover: fake_plugin_root() copies the CURRENT setup.sh
    # into every fake version, so the "old" root here has the guard in it. A real
    # pre-2.1.7 root on disk does not, and nothing in the new version can run
    # inside it — verified live on 2026-08-26, where a real 2.1.3 copy overwrote
    # a 2.1.7 setup with exit 0. The guard only stops a downgrade between roots
    # that both carry it. The defence that works against older copies is the
    # staleness notice in gate_prompt.py, which always runs from the active
    # plugin; it is checked further down.
    #
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
        [BASH, str(root / "setup" / "setup.sh"), "global"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env={**env_down, "HOME": str(home), "USERPROFILE": str(home),
             "CLAUDE_PLUGIN_ROOT": str(root), "CLAUDE_CONFIG_DIR": str(home / ".claude")},
        cwd=str(root), timeout=120,
    )
    check("explicit downgrade allowed", proc5.returncode, 0)
    check("explicit downgrade records the old version",
          json.loads((home / ".fablize" / "progress.json").read_text(encoding="utf-8"))["version"],
          "9.9.9")

    # The staleness notice reaches the model once per session, not every prompt:
    # re-injected each turn, it was re-acted on each turn. Real hook, real ledger.
    def prompt_hook(session):
        env = {**os.environ, "HOME": str(home), "USERPROFILE": str(home),
               "FABLIZE_DATA": str(tmp / "ledgers_notice")}
        out = subprocess.run([sys.executable, str(REPO / "hooks" / "gate_prompt.py")],
                             input=json.dumps({"prompt": "hi", "session_id": session, "cwd": "/w"}),
                             capture_output=True, text=True, encoding="utf-8", env=env, timeout=60).stdout
        return "setup has not re-run" in out or "older than the" in out
    check("notice on the first prompt of a session", prompt_hook("N1"), True)
    check("no notice on the second prompt", prompt_hook("N1"), False)
    check("notice again in a new session", prompt_hook("N2"), True)

    # Local setup records the project, so the notice re-runs it THERE.
    proj = tmp / "proj"
    proj.mkdir()
    home_l = tmp / "home_l"
    (home_l / ".claude").mkdir(parents=True)
    proc_l = subprocess.run(
        [BASH, str(root2 / "setup" / "setup.sh"), "local"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env={**os.environ, "HOME": str(home_l), "USERPROFILE": str(home_l),
             "CLAUDE_CONFIG_DIR": str(home_l / ".claude")},
        cwd=str(proj), timeout=120,
    )
    rec_l = json.loads((home_l / ".fablize" / "progress.json").read_text(encoding="utf-8"))
    check("local setup exit code", proc_l.returncode, 0)
    check("local setup records the project",
          Path(rec_l.get("project", "")).resolve() == proj.resolve()
          or rec_l.get("project", "").rstrip("/").endswith("/proj"), True)
    check("local setup writes ./CLAUDE.md", (proj / "CLAUDE.md").exists(), True)

    # A fork build number is part of the version. Keeping only three numbers
    # made `+cakel.3` equal to `+cakel.4`, so the old copy overwrote the newer
    # setup with exit 0. Observed 2026-10-23. Fresh home: the record above is 9.9.9.
    home_b = tmp / "home_b"
    home_b.mkdir()
    check("fork build applies", run_setup(fake_plugin_root(tmp, "2.1.1+cakel.4"), home_b).returncode, 0)
    check("older fork build refused",
          run_setup(fake_plugin_root(tmp, "2.1.1+cakel.3"), home_b).returncode, 1)
    check("older fork build leaves the record",
          json.loads((home_b / ".fablize" / "progress.json").read_text(encoding="utf-8"))["version"],
          "2.1.1+cakel.4")

    # Global scope must follow CLAUDE_CONFIG_DIR: that is where Claude Code reads
    # CLAUDE.md from, so a block written to ~/.claude is never seen when it is set.
    home_c = tmp / "home_c"
    (home_c / "cfg").mkdir(parents=True)
    proc_c = subprocess.run(
        [BASH, str(root2 / "setup" / "setup.sh"), "global"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env={**os.environ, "HOME": str(home_c), "USERPROFILE": str(home_c),
             "CLAUDE_PLUGIN_ROOT": str(root2), "CLAUDE_CONFIG_DIR": str(home_c / "cfg")},
        cwd=str(root2), timeout=120,
    )
    check("CLAUDE_CONFIG_DIR setup exit code", proc_c.returncode, 0)
    check("block lands in CLAUDE_CONFIG_DIR", (home_c / "cfg" / "CLAUDE.md").exists(), True)
    check("nothing written to ~/.claude when CLAUDE_CONFIG_DIR is set",
          (home_c / ".claude" / "CLAUDE.md").exists(), False)

    # From a tool call stdin is not a terminal: no scope must be a clear error, not
    # a silent exit 1 from `read` under set -e.
    proc_n = subprocess.run(
        [BASH, str(root2 / "setup" / "setup.sh")],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        stdin=subprocess.DEVNULL,
        env={**os.environ, "HOME": str(home_c), "USERPROFILE": str(home_c),
             "CLAUDE_PLUGIN_ROOT": str(root2)},
        cwd=str(root2), timeout=120,
    )
    check("no scope + no terminal exits 1", proc_n.returncode, 1)
    check("no scope + no terminal says why", "not a terminal" in proc_n.stdout, True)

    # The fork shipped 2.1.2 .. 2.1.8 before renumbering to 2.1.1+cakel.N. A user
    # set up on 2.1.7 must be able to apply a cakel build, not be refused forever.
    home_v = tmp / "home_v"
    (home_v / ".fablize").mkdir(parents=True)
    (home_v / ".fablize" / "progress.json").write_text(json.dumps({"version": "2.1.7"}), encoding="utf-8")
    check("cakel build applies over legacy 2.1.7",
          run_setup(fake_plugin_root(tmp, "2.1.1+cakel.7"), home_v).returncode, 0)

    # Windows locale codepage: without PYTHONUTF8 setup.sh died on its own check
    # mark under cp949, after the block but before progress.json. Strip the host's
    # PYTHONIOENCODING/PYTHONUTF8 so this host's own setting cannot mask it.
    home_e = tmp / "home_e"
    (home_e / ".claude").mkdir(parents=True)
    env_e = {k: v for k, v in os.environ.items() if k not in ("PYTHONIOENCODING", "PYTHONUTF8")}
    env_e.update(HOME=str(home_e), USERPROFILE=str(home_e), CLAUDE_CONFIG_DIR=str(home_e / ".claude"))
    proc_e = subprocess.run([BASH, str(root2 / "setup" / "setup.sh"), "global"],
                            capture_output=True, text=True, encoding="utf-8", errors="replace",
                            env=env_e, cwd=str(root2), timeout=120)
    check("setup without PYTHONIOENCODING exits 0", proc_e.returncode, 0)
    check("... and records progress.json", (home_e / ".fablize" / "progress.json").exists(), True)

    # Uninstall clears the recorded version: otherwise the next upgrade's notice
    # offered to re-run setup and re-inject the block the user just removed.
    home_u = tmp / "home_u"
    (home_u / ".fablize").mkdir(parents=True)
    (home_u / ".fablize" / "progress.json").write_text(
        json.dumps({"version": "0.0.1", "scope": "global", "setup_done": True}), encoding="utf-8")
    subprocess.run([BASH, str(REPO / "setup" / "uninstall.sh"), "global"], capture_output=True,
                   env={**os.environ, "HOME": str(home_u), "USERPROFILE": str(home_u),
                        "CLAUDE_CONFIG_DIR": str(home_u / ".claude")}, timeout=120)
    rec_u = json.loads((home_u / ".fablize" / "progress.json").read_text(encoding="utf-8"))
    check("uninstall clears the recorded version", ("version" in rec_u, rec_u.get("uninstalled")), (False, True))

    # staleness notice: recorded version behind the running plugin
    import gate_prompt

    key = gate_prompt._version_key
    check("key orders fork builds", key("2.1.1+cakel.3") < key("2.1.1+cakel.4"), True)
    check("key puts a fork build after its base", key("2.1.1") < key("2.1.1+cakel.1"), True)
    check("key puts the next release after a fork build",
          key("2.1.1+cakel.9") < key("2.1.2+cakel.1") < key("2.2.0"), True)
    check("key orders two-digit parts", key("9.9.9") < key("9.9.10"), True)
    check("legacy fork release ranks below the first cakel build",
          key("2.1.8") < key("2.1.1+cakel.1") and key("2.1.1") < key("2.1.2") < key("2.1.8"), True)

    progress_path = home / ".fablize" / "progress.json"
    real_expanduser = os.path.expanduser
    os.path.expanduser = lambda p: (str(progress_path) if p == "~/.fablize/progress.json"
                                    else real_expanduser(p))
    try:
        running = json.loads((REPO / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["version"]
        progress_path.write_text(json.dumps({"version": "0.0.1"}), encoding="utf-8")
        check("notice when versions differ", "0.0.1" in gate_prompt.stale_setup_notice(), True)

        # The notice has to name the command, not `/fablize:setup`. That command
        # carries a plugin path expanded when the SESSION loaded it, so in a
        # session opened before the upgrade it runs the old setup.sh and records
        # the old version again — which is how this fired twice on 2026-08-26.
        progress_path.write_text(json.dumps({"version": "0.0.1", "scope": "global"}),
                                 encoding="utf-8")
        notice = gate_prompt.stale_setup_notice()
        check("notice names the running plugin's setup.sh",
              REPO.as_posix() + "/setup/setup.sh" in notice, True)
        check("notice carries the recorded scope", 'setup.sh" global' in notice, True)
        check("notice warns the slash command runs the old copy",
              "OLD setup.sh" in notice, True)
        progress_path.write_text(json.dumps({"version": "0.0.1", "scope": "local"}),
                                 encoding="utf-8")
        check("notice follows a local scope", 'setup.sh" local' in gate_prompt.stale_setup_notice(), True)
        progress_path.write_text(json.dumps({"version": "0.0.1", "scope": "local",
                                             "project": "D:/proj/한글 dir"}), encoding="utf-8")
        check("local notice cds into the recorded project",
              'cd "D:/proj/한글 dir" && bash' in gate_prompt.stale_setup_notice(), True)
        # A missing or nonsense scope must not produce a broken command.
        progress_path.write_text(json.dumps({"version": "0.0.1"}), encoding="utf-8")
        check("notice defaults the scope", 'setup.sh" global' in gate_prompt.stale_setup_notice(), True)
        progress_path.write_text(json.dumps({"version": "0.0.1", "scope": "weird"}),
                                 encoding="utf-8")
        check("notice ignores a bad scope", 'setup.sh" global' in gate_prompt.stale_setup_notice(), True)

        # The other direction is not a downgrade that happened — it means THIS
        # session is the old one. hooks.json's ${CLAUDE_PLUGIN_ROOT} is resolved
        # when the hooks are registered, so a session opened before an upgrade
        # keeps running the old gate_prompt.py for its whole life. Reported live
        # on 2026-08-26 by a second session showing `(2.1.7 -> 2.1.3)`, which the
        # one-way wording called an upgrade. Running setup there is the thing to
        # prevent, so the notice must say the opposite of the upgrade case.
        progress_path.write_text(json.dumps({"version": "99.0.0", "scope": "global"}),
                                 encoding="utf-8")
        older = gate_prompt.stale_setup_notice()
        check("older-plugin notice fires", bool(older), True)
        check("older-plugin notice forbids running setup", "Do NOT run setup" in older, True)
        check("older-plugin notice names both versions",
              running in older and "99.0.0" in older, True)
        check("older-plugin notice offers no command to run",
              'setup.sh" global' in older, False)

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


def test_script():
    """pytest entry. The script form (`python3 tests/test_setup_paths.py`) stays canonical."""
    assert main() == 0
