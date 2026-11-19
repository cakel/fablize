#!/usr/bin/env python3
"""Put the FABLIZE operating block into a CLAUDE.md, or take it out, without losing
the user's own text.

Usage:
  block_merge.py inject <CLAUDE.md> <block template> <lib dir>
  block_merge.py remove <CLAUDE.md>

setup.sh used to delete everything between the markers and append a fresh block at
the end of the file. Anything a user (or another tool) had written between the
markers went with it — seen 2026-11-19: a "touch.exe blocked" section that had been
added right after FABLIZE:BEGIN vanished on upgrade. uninstall.sh had the same hole.

Now:
  - The block is replaced IN PLACE (the user's ordering of sections is kept). With no
    block yet, it is appended as before.
  - Lines inside the old block that no fablize template wrote are kept: they are moved
    just above the new block (inject) or left where the block was (remove), and the
    count is reported. fablize-owned lines are the markers, the "## Operating mode"
    heading, its intro paragraph and the "- **[…]**" routing bullets — the shape every
    template version has had (setup/fablize-block.md history).
  - A second, duplicated block is dropped the same way (its foreign lines kept).
"""
import pathlib
import re
import sys

BLOCK_RE = re.compile(r"<!-- FABLIZE:BEGIN.*?FABLIZE:END -->[ \t]*\n?", re.S)
_OWNED_RE = re.compile(
    r"^(?:<!-- FABLIZE:(?:BEGIN|END)\b|## Operating mode\b|Apply what the task signals\b|- \*\*\[)")


def foreign_text(block: str) -> str:
    """The lines of one FABLIZE block that no fablize template wrote. Blank lines
    between them are kept; fablize lines in between are dropped. '' when none."""
    lines = block.split("\n")
    foreign = [i for i, ln in enumerate(lines) if ln.strip() and not _OWNED_RE.match(ln)]
    if not foreign:
        return ""
    run = lines[foreign[0]:foreign[-1] + 1]
    return "\n".join(ln for ln in run if not ln.strip() or not _OWNED_RE.match(ln)).strip("\n")


def _count(text: str) -> int:
    return sum(1 for ln in text.split("\n") if ln.strip())


def inject(cur: str, block: str) -> tuple[str, int]:
    """(new text, number of user lines kept). `block` is the rendered template."""
    block = block.strip()
    kept = 0
    if not BLOCK_RE.search(cur):
        cur = cur.rstrip()
        return ((cur + "\n\n" + block + "\n") if cur else (block + "\n")), 0
    first = True

    def repl(m):
        nonlocal first, kept
        own = foreign_text(m.group(0))
        kept += _count(own)
        prefix = (own + "\n\n") if own else ""
        if first:
            first = False
            return prefix + block + "\n"
        return prefix  # a duplicated block: keep only the user's lines

    new = BLOCK_RE.sub(repl, cur)
    return new.rstrip("\n") + "\n", kept


def remove(cur: str) -> tuple[str, int, bool]:
    """(new text, user lines kept, whether a block was found)."""
    kept = 0
    found = bool(BLOCK_RE.search(cur))

    def repl(m):
        nonlocal kept
        own = foreign_text(m.group(0))
        kept += _count(own)
        return (own + "\n") if own else ""

    new = BLOCK_RE.sub(repl, cur)
    new = re.sub(r"\n{3,}", "\n\n", new)
    return (new.rstrip("\n") + "\n") if new.strip() else "", kept, found


def main(argv: list) -> int:
    if len(argv) >= 2 and argv[0] == "inject" and len(argv) == 4:
        p = pathlib.Path(argv[1])
        cur = p.read_text(encoding="utf-8") if p.exists() else ""
        block = pathlib.Path(argv[2]).read_text(encoding="utf-8").replace("__PLUGIN_ROOT__", argv[3])
        new, kept = inject(cur, block)
        p.write_text(new, encoding="utf-8")
        print("  ✓ CLAUDE.md: FABLIZE operating block injected (idempotent)")
        if kept:
            print(f"  ✓ kept {kept} line(s) of your own text that sat inside the FABLIZE block — now just above it")
        return 0
    if len(argv) == 2 and argv[0] == "remove":
        p = pathlib.Path(argv[1])
        new, kept, found = remove(p.read_text(encoding="utf-8"))
        if found:
            p.write_text(new, encoding="utf-8")
        print("  ✓ FABLIZE block removed" if found else "  = no FABLIZE block (already removed)")
        if kept:
            print(f"  ✓ kept {kept} line(s) of your own text that sat inside the FABLIZE block")
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
