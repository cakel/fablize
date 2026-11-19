#!/usr/bin/env python3
"""setup/block_merge.py keeps the user's text when the FABLIZE block is replaced or
removed. Exit non-zero on any mismatch (script form is canonical, pytest runs it too)."""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "setup"))
import block_merge as bm  # noqa: E402

CHECKS = []


def check(label, got, want):
    CHECKS.append((label, got, want))


TPL = (REPO / "setup" / "fablize-block.md").read_text(encoding="utf-8")
NEW = TPL.replace("__PLUGIN_ROOT__", "C:/lib").strip()
OLD = TPL.replace("__PLUGIN_ROOT__", "C:/old").strip()  # what an earlier setup wrote
BEGIN, rest = OLD.split("\n", 1)
USER = ("# touch 차단 (touch.exe blocked)\n\n회사 보안 SW가 touch.exe 를 막는다.\n"
        "- 셸에서 맨 `touch`는 써도 된다.\n- 시각 지정은 지원하지 않는다.")
# The real 2026-11-19 shape: a user section pasted right after FABLIZE:BEGIN.
INSIDE = f"# Before\n\nmine\n\n{BEGIN}\n{USER}\n\n{rest}\n\n# After\n\nalso mine\n"

new, kept = bm.inject(INSIDE, NEW)
check("user section inside the markers is kept", USER in new, True)
check("kept section moves just above the new block", new.index(USER) < new.index("FABLIZE:BEGIN"), True)
check("old block content is replaced", ("C:/old" in new, "C:/lib" in new), (False, True))
check("block stays in place, not moved to the end", new.index("FABLIZE:END") < new.index("# After"), True)
check("text before and after the block is untouched", ("# Before\n\nmine" in new, "# After\n\nalso mine" in new), (True, True))
check("kept line count (non-blank)", kept, 4)
check("one block only", new.count("FABLIZE:BEGIN"), 1)
again, kept2 = bm.inject(new, NEW)
check("re-inject is idempotent", (again, kept2), (new, 0))

plain, k = bm.inject(f"# Mine\n\n{OLD}\n", NEW)
check("a pure fablize block keeps nothing extra", (k, plain), (0, f"# Mine\n\n{NEW}\n"))
check("no block yet: appended after the user's text", bm.inject("# Mine\n", NEW), (f"# Mine\n\n{NEW}\n", 0))
check("empty file: just the block", bm.inject("", NEW), (NEW + "\n", 0))

user_end = f"{OLD.rsplit(chr(10), 1)[0]}\n\nmy note at the end\n<!-- FABLIZE:END -->\n"
check("user text just before FABLIZE:END is kept too", "my note at the end" in bm.inject(user_end, NEW)[0], True)
dup = f"{OLD}\n\nmiddle\n\n{BEGIN}\nsecond copy note\n{rest}\n"
d, dk = bm.inject(dup, NEW)
check("a duplicated block is dropped, its user line kept",
      (d.count("FABLIZE:BEGIN"), "second copy note" in d, "middle" in d, dk), (1, True, True, 1))

removed, rk, found = bm.remove(INSIDE)
check("uninstall keeps the user's section where the block was",
      (found, rk, "FABLIZE" in removed, USER in removed,
       removed.index("mine") < removed.index(USER) < removed.index("also mine")), (True, 4, False, True, True))
check("uninstall of a pure block leaves the rest", bm.remove(f"# Mine\n\n{OLD}\n\n# Tail\n")[0], "# Mine\n\n# Tail\n")
check("uninstall with no block changes nothing", bm.remove("# Mine\n"), ("# Mine\n", 0, False))
fence = "# Notes\n\n```\nline 1\n\n\n\nline 2\n```\n"
check("uninstall leaves blank-line runs elsewhere alone (PR #39 review)",
      bm.remove(f"{fence}\n{OLD}\n\n# Tail\n")[0], f"{fence}\n# Tail\n")
check("uninstall of a block at the top of the file", bm.remove(f"{OLD}\n\n# Tail\n")[0], "# Tail\n")

# The CLI the shell scripts call.
with tempfile.TemporaryDirectory() as d:
    md, tpl = Path(d, "CLAUDE.md"), Path(d, "tpl.md")
    md.write_text(INSIDE, encoding="utf-8")
    tpl.write_text(TPL, encoding="utf-8")
    # A cp1252 child stdout is what the Windows CI runner gives a pipe; the ✓ must still print.
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONIOENCODING", "PYTHONUTF8")}
    env["PYTHONIOENCODING"] = "cp1252"
    out = subprocess.run([sys.executable, str(REPO / "setup" / "block_merge.py"), "inject", str(md), str(tpl), "C:/lib"],
                         capture_output=True, text=True, encoding="utf-8", env=env).stdout
    check("CLI inject reports the kept lines", "kept 4 line(s)" in out, True)
    check("CLI inject writes the merged file", USER in md.read_text(encoding="utf-8"), True)
    out = subprocess.run([sys.executable, str(REPO / "setup" / "block_merge.py"), "remove", str(md)],
                         capture_output=True, text=True, encoding="utf-8", env=env).stdout
    text = md.read_text(encoding="utf-8")
    check("CLI remove: block gone, user text stays", ("FABLIZE" in text, USER in text, "removed" in out), (False, True, True))


def main():
    bad = 0
    for label, got, want in CHECKS:
        ok = got == want
        bad += 0 if ok else 1
        print(f"{'OK  ' if ok else 'FAIL'} {str(got)[:50]:<52} {label}")
    print("-" * 78)
    print(f"RESULT: {'all ' + str(len(CHECKS)) + ' checks match.' if not bad else str(bad) + ' mismatched.'}")
    return 1 if bad else 0


def test_script():
    assert main() == 0


if __name__ == "__main__":
    raise SystemExit(main())
