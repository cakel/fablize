#!/usr/bin/env bash
# fablize uninstall — remove the FABLIZE block from CLAUDE.md (idempotent).
# The plugin hooks (router + observation gate) are removed when the plugin is uninstalled.
# Usage: uninstall.sh [global|local]
set -euo pipefail

CONFIG_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
pyrun() { bash "$ROOT/hooks/py.sh" "$@"; }
[ "$(pyrun -c 'print(1)' 2>/dev/null)" = 1 ] || { echo "fablize: Python 3 is required (no python3, python or py on PATH)."; exit 1; }

scope="${1:-}"
if [ -z "$scope" ]; then
  [ -t 0 ] || { echo "fablize: no scope given and stdin is not a terminal — run: uninstall.sh local|global"; exit 1; }
  printf "fablize — remove the operating block from: [l]ocal / [g]lobal: "
  read -r ans
  case "$ans" in g*|G*) scope=global;; *) scope=local;; esac
fi
case "$scope" in
  global) CLAUDE_MD="$CONFIG_DIR/CLAUDE.md";;
  local)  CLAUDE_MD="$PWD/CLAUDE.md";;
  *) echo "fablize: scope must be global or local"; exit 1;;
esac
# Clear the recorded version. Left in place, the next plugin upgrade made the
# staleness notice say the (removed) block "still points at the previous
# release" and offer to re-run setup — re-injecting what the user just removed.
# The file stays, so the skill's first-run onboarding does not ask again.
pyrun - "$HOME/.fablize/progress.json" "$scope" <<'PY'
import json, os, sys
path, scope = sys.argv[1], sys.argv[2]
if os.path.exists(path):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"setup_done": False, "uninstalled": True, "scope": scope}, fh)
PY

[ -f "$CLAUDE_MD" ] || { echo "fablize: $CLAUDE_MD not found — nothing to remove."; exit 0; }

pyrun - "$CLAUDE_MD" <<'PY'
import sys, re, pathlib
p = pathlib.Path(sys.argv[1])
cur = p.read_text(encoding="utf-8")
new = re.sub(r"\n*<!-- FABLIZE:BEGIN.*?FABLIZE:END -->\n?", "\n", cur, flags=re.S)
p.write_text(new, encoding="utf-8")
print("  ✓ FABLIZE block removed" if new != cur else "  = no FABLIZE block (already removed)")
PY

echo "fablize uninstall complete ($scope). The plugin hooks (router + observation gate) stay active until you /plugin uninstall."
echo "  Backups ($CLAUDE_MD.fablize-bak.*) can be deleted manually if no longer needed."
