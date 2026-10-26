#!/usr/bin/env bash
# fablize always-on setup — inject the operating block into CLAUDE.md (idempotent, with backup).
# The UserPromptSubmit router hook is registered automatically by hooks.json on plugin install,
# so this script does NOT touch settings.json (no risk of clobbering existing hooks/settings).
# Usage:
#   setup.sh [global|local]  -> inject the block (no arg = interactive; default local). Never stars.
#   setup.sh ask             -> iff no star decision is on record, atomically record an "asked"
#                               marker AND print "STAR_ASK <lang>". The command/skill flow then
#                               asks via AskUserQuestion (Claude-only; cannot be issued from bash).
#                               Recording the marker HERE guarantees the question is shown at most
#                               once, even if the caller never reports the answer back.
#   setup.sh star yes|no     -> record the decision; star the repo ONLY on an explicit "yes".
# This script never stars the repo without an explicit "star yes" (issue #4).
set -euo pipefail

# This script's own location, NOT $CLAUDE_PLUGIN_ROOT. A session opened before an
# upgrade can carry the OLD root in that variable, so `bash <new>/setup/setup.sh`
# would silently apply the old copy's assets — the downgrade the guard below
# exists to stop, triggered by the very command the staleness notice prints.
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BLOCK_TPL="$ROOT/setup/fablize-block.md"
REPO="cakel/fablize"
REPO_URL="https://github.com/cakel/fablize"
CONFIG_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
STATE_DIR="$HOME/.fablize"
STAR_MARKER="$STATE_DIR/star.json"

# Interpreter: hooks/py.sh is the single copy of the python3/python/py rule.
pyrun() { bash "$ROOT/hooks/py.sh" "$@"; }
# py.sh exits 0 silently when no interpreter exists (hooks fail open), so probe
# for output rather than trusting the exit code.
HAVE_PY=0; [ "$(pyrun -c 'print(1)' 2>/dev/null)" = 1 ] && HAVE_PY=1

# --- detect a fallback UI language from past Claude session transcripts (best-effort) ---
# Counts Hangul / Kana / Latin letters in HUMAN-typed user text only (skips tool results,
# assistant turns and JSON structure, which are ASCII-heavy and would skew to English).
detect_lang() {
  [ "$HAVE_PY" = 1 ] || { echo en; return; }
  pyrun - "$CONFIG_DIR/projects" 2>/dev/null <<'PY' || echo en
import sys, os, glob, json
base = sys.argv[1]
try:
    files = sorted(glob.glob(os.path.join(base, "**", "*.jsonl"), recursive=True),
                   key=os.path.getmtime, reverse=True)[:20]
except Exception:
    files = []
# Vote per message (presence of script), not per char — so a few large ASCII
# pastes (code, logs, specs) don't drown out many short typed Korean turns.
ko = ja = en = 0
msgs = 0
def vote(s):
    global ko, ja, en, msgs
    hk = hj = hl = False
    for ch in s:
        o = ord(ch)
        if 0xAC00 <= o <= 0xD7A3: hk = True
        elif 0x3040 <= o <= 0x30FF: hj = True
        elif 65 <= o <= 90 or 97 <= o <= 122: hl = True
    if hk: ko += 1
    elif hj: ja += 1
    elif hl: en += 1
    if hk or hj or hl: msgs += 1
for f in files:
    if msgs >= 400: break
    try:
        fh = open(f, encoding="utf-8", errors="ignore")
    except Exception:
        continue
    for line in fh:
        if msgs >= 400: break
        try:
            m = json.loads(line).get("message")
        except Exception:
            continue
        if not isinstance(m, dict) or m.get("role") != "user":
            continue
        c = m.get("content")
        if isinstance(c, str):
            vote(c)
        elif isinstance(c, list):
            for part in c:
                if isinstance(part, dict) and part.get("type") == "text":
                    vote(part.get("text", ""))
    fh.close()
if ko and ko >= ja and ko >= en: print("ko")
elif ja and ja >= ko and ja >= en: print("ja")
else: print("en")
PY
}

# --- record the star decision (and star the repo on an explicit "yes") ---
write_star() {  # $1 = decision (yes|no|asked)
  mkdir -p "$STATE_DIR"
  ts=$(date +%s 2>/dev/null || echo 0)
  printf '{"star_decision":"%s","ts":%s}\n' "$1" "$ts" > "$STAR_MARKER"
}

if [ "${1:-}" = "star" ]; then
  DECISION="${2:-no}"
  write_star "$DECISION"
  if [ "$DECISION" = "yes" ] && command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
    gh api "user/starred/$REPO" >/dev/null 2>&1 \
      || gh api -X PUT "user/starred/$REPO" >/dev/null 2>&1 || true
  fi
  exit 0
fi

# ask mode: emit the star prompt EXACTLY ONCE, recording it deterministically in bash.
if [ "${1:-}" = "ask" ]; then
  if [ ! -f "$STAR_MARKER" ]; then
    write_star "asked"
    echo "STAR_ASK $(detect_lang)"
  fi
  exit 0
fi

[ "$HAVE_PY" = 1 ] || { echo "fablize: Python 3 is required (no python3, python or py on PATH)."; exit 1; }
[ -f "$BLOCK_TPL" ] || { echo "fablize: block template not found ($BLOCK_TPL)"; exit 1; }

scope="${1:-}"
if [ -z "$scope" ]; then
  # From a tool call stdin is not a terminal, so `read` fails and `set -e` exits
  # with no message. Say what was missing instead.
  [ -t 0 ] || { echo "fablize: no scope given and stdin is not a terminal — run: setup.sh local|global"; exit 1; }
  printf "fablize — inject the operating block into: [l]ocal (this project, recommended) / [g]lobal (all projects): "
  read -r ans
  case "$ans" in g*|G*) scope=global;; l*|L*|"") scope=local;; *) echo "cancelled"; exit 1;; esac
fi
case "$scope" in
  # CONFIG_DIR, not ~/.claude: with CLAUDE_CONFIG_DIR set, Claude Code reads
  # CLAUDE.md from there and a block written to ~/.claude is never seen.
  global) CLAUDE_MD="$CONFIG_DIR/CLAUDE.md";;
  local)  CLAUDE_MD="$PWD/CLAUDE.md";;
  *) echo "fablize: scope must be global or local"; exit 1;;
esac

# --- refuse to apply an older copy over a newer one ------------------------------
# /fablize:setup has ${CLAUDE_PLUGIN_ROOT} expanded when a SESSION LOADS it, so a
# session opened before an upgrade still holds the previous version's path. That
# copy is still on disk — the plugin cache keeps every installed version — so the
# old setup.sh runs, succeeds, and silently rolls ~/.fablize/lib and progress.json
# back. Nothing errors, and the only trace is the staleness notice reappearing.
#
# Observed live on 2026-08-26: 2.1.5 was applied at 16:44:39, and at 16:51:02 a
# second session holding the 2.1.3 path overwrote the packs and recorded 2.1.3.
# The user was told they were applying 2.1.5.
#
# 2.1.3 closed this class for the injected CLAUDE.md block. The command file is
# outside this repo's control — the harness expands the path, not us — so the
# defence has to live here, in the thing that actually runs.
if [ "${FABLIZE_ALLOW_DOWNGRADE:-}" != "1" ]; then
  pyrun - "$ROOT" "$STATE_DIR" <<'PY' || exit 1
import json, os, re, sys

def parts(value):
    # Every number, not the first three: `2.1.1+cakel.3` and `+cakel.4` must not
    # compare equal. Same key as gate_prompt._version_key, including the fork's
    # pre-renumber releases 2.1.2 .. 2.1.8, which rank just below 2.1.1+cakel.1.
    text = str(value)
    if re.fullmatch(r"2\.1\.[2-8]", text):
        return (2, 1, 1, 0, int(text.rsplit(".", 1)[1]))
    return tuple(int(n) for n in re.findall(r"\d+", text))

root, state_dir = sys.argv[1], sys.argv[2]
try:
    running = json.load(open(os.path.join(root, ".claude-plugin", "plugin.json"),
                             encoding="utf-8"))["version"]
except Exception:
    sys.exit(0)          # no manifest to compare — never block setup on that
try:
    recorded = json.load(open(os.path.join(state_dir, "progress.json"),
                              encoding="utf-8")).get("version")
except Exception:
    sys.exit(0)          # first run
if not recorded or recorded == "unknown" or parts(running) >= parts(recorded):
    sys.exit(0)
print("fablize: refusing to apply %s over %s." % (running, recorded))
print("  This copy of setup.sh is older than the version already set up:")
print("    %s" % root)
print("  /fablize:setup has its plugin path expanded when a session loads it, so a")
print("  session started before the upgrade still runs the previous version.")
print("  Run it from a session started after the upgrade (or after /reload-plugins).")
print("  To downgrade deliberately: FABLIZE_ALLOW_DOWNGRADE=1 setup.sh <local|global>")
sys.exit(1)
PY
fi

echo "fablize → $scope ($CLAUDE_MD)"

mkdir -p "$(dirname "$CLAUDE_MD")"; touch "$CLAUDE_MD"
ts=$(pyrun -c "import time;print(int(time.time()))")
cp "$CLAUDE_MD" "$CLAUDE_MD.fablize-bak.$ts" && echo "  backup: $CLAUDE_MD.fablize-bak.$ts"

# The block used to bake $ROOT — a VERSION-PINNED cache path like
# .../cache/fablize/fablize/2.1.1 — straight into CLAUDE.md. After a plugin
# upgrade that directory is gone and every path in the injected block dangles,
# with nothing to notice or repair it. Copy the referenced assets to a stable
# location instead and point the block there, so the paths survive upgrades.
# The hooks keep using ${CLAUDE_PLUGIN_ROOT}; only this text needed pinning.
LIB_DIR="$STATE_DIR/lib"
mkdir -p "$LIB_DIR/scripts" "$LIB_DIR/packs" "$LIB_DIR/hooks"
cp "$ROOT/scripts/goals.py" "$LIB_DIR/scripts/goals.py"
cp "$ROOT"/packs/*.txt "$LIB_DIR/packs/"
# py.sh: the block runs goals.py through it (no python3 on python.org Windows).
# finish-the-work.sh: users register it as a Stop hook by hand, and it needs
# py.sh beside it. Pointing them at $ROOT dangles after the next upgrade.
cp "$ROOT/hooks/py.sh" "$ROOT/hooks/finish-the-work.sh" "$LIB_DIR/hooks/"
echo "  ✓ assets: $LIB_DIR (refreshed from $ROOT)"

# Substitute __PLUGIN_ROOT__ -> stable path, then inject idempotently (remove old markers, re-insert).
pyrun - "$CLAUDE_MD" "$BLOCK_TPL" "$LIB_DIR" <<'PY'
import sys, re, pathlib
md, tpl, root = sys.argv[1], sys.argv[2], sys.argv[3]
p = pathlib.Path(md)
cur = p.read_text(encoding="utf-8") if p.exists() else ""
block = pathlib.Path(tpl).read_text(encoding="utf-8").strip().replace("__PLUGIN_ROOT__", root)
cur = re.sub(r"<!-- FABLIZE:BEGIN.*?FABLIZE:END -->\n?", "", cur, flags=re.S).rstrip()
p.write_text((cur + "\n\n" + block + "\n") if cur else (block + "\n"), encoding="utf-8")
print("  ✓ CLAUDE.md: FABLIZE operating block injected (idempotent)")
PY

# Record setup state so the skill won't auto-run setup again.
mkdir -p "$STATE_DIR"
pyrun - "$scope" "$ts" "$ROOT" "$STATE_DIR" "$PWD" <<'PY'
import json, sys, os
# Read the version from the manifest instead of a literal: a hardcoded string
# here silently disagrees with the installed plugin after every release, and the
# staleness check in gate_prompt.py compares against exactly this value.
root = sys.argv[3]
try:
    version = json.load(open(os.path.join(root, ".claude-plugin", "plugin.json"),
                              encoding="utf-8"))["version"]
except Exception:
    version = "unknown"
# STATE_DIR from bash, not expanduser("~"): on Windows that reads USERPROFILE,
# which need not agree with bash's $HOME.
p = os.path.join(sys.argv[4], "progress.json")
state = {"setup_done": True, "scope": sys.argv[1], "version": version, "ts": int(sys.argv[2])}
if sys.argv[1] == "local":
    # The staleness notice must re-run setup in THIS project, not wherever the
    # user happens to be when it fires.
    state["project"] = sys.argv[5]
with open(p, "w", encoding="utf-8") as fh:
    json.dump(state, fh)
print(f"  ✓ state: version {version} recorded")
PY

echo "fablize setup complete ($scope) — applies from the next session."
echo "  state: ~/.fablize/progress.json"
echo "  Uninstall: bash $ROOT/setup/uninstall.sh $scope"
echo "  Note: the router and gate hooks are auto-registered on plugin install. The early-stop hook"
echo "        (finish-the-work) is not: register it as a Stop hook only if you want it and it is not"
echo "        already registered (avoid duplicates): bash \"$LIB_DIR/hooks/finish-the-work.sh\""
echo "        (that copy survives upgrades and is refreshed by every setup run)."
