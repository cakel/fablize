#!/usr/bin/env bash
# fablize on Kiro CLI, end to end, on a Linux host with a logged-in kiro-cli.
#
#   bash tests/e2e/kiro_linux_e2e.sh            # install -> hook smoke -> live Kiro -> uninstall
#   FABLIZE_E2E_LIVE=0  ...                     # skip the live kiro-cli runs (no model calls)
#   FABLIZE_E2E_KEEP=1  ...                     # leave fablize installed and keep the work dir
#   FABLIZE_E2E_TIMEOUT=900 ...                 # seconds per live kiro-cli run (default 600)
#
# From Windows/macOS, ship the working tree and run it remotely:
#   git -c core.autocrlf=false archive HEAD | \
#     ssh -p PORT user@host 'rm -rf ~/fablize-e2e && mkdir ~/fablize-e2e && tar xf - -C ~/fablize-e2e'
#   ssh -p PORT user@host 'bash ~/fablize-e2e/tests/e2e/kiro_linux_e2e.sh'
# (archive with autocrlf off: a tar of a Windows checkout ships CRLF scripts, bash cannot
#  parse py.sh, and the installer falls back to python-mode hooks.)
#
# Phases
#   1 install    python3 setup/kiro.py global: files, marker, bash-mode commands, agent validate
#   2 smoke      the hook commands exactly as the agent file holds them, run through `sh -c`
#                with Kiro v2/v3 payloads: routing, stop block, pass-through, carried gap, goals.py
#   3 live       kiro-cli chat --no-interactive --agent fablize, twice; the hooks' own records
#                (router_signal / stop_block events, the session ledger) are the evidence
#   4 uninstall  python3 setup/kiro.py uninstall removes what it wrote
# Exit 0 only when every check passed.

set -u
REPO=$(cd "$(dirname "$0")/../.." && pwd)
LIVE=${FABLIZE_E2E_LIVE:-1}
KEEP=${FABLIZE_E2E_KEEP:-0}
TIMEOUT=${FABLIZE_E2E_TIMEOUT:-600}
export PATH="$HOME/.local/bin:$PATH"   # kiro-cli's default install dir; not on a non-login PATH
PY=python3
AGENT="$HOME/.kiro/agents/fablize.json"
SKILL="$HOME/.kiro/skills/fablize/SKILL.md"
STEER="$HOME/.kiro/steering/fablize.md"
RT="$HOME/.fablize/kiro"
WORK=$(mktemp -d "${TMPDIR:-/tmp}/fz-kiro-e2e.XXXXXX")
PASS=0 FAIL=0 SKIP=0

ok()   { echo "  PASS  $*"; PASS=$((PASS + 1)); }
ng()   { echo "  FAIL  $*"; FAIL=$((FAIL + 1)); }
skip() { echo "  SKIP  $*"; SKIP=$((SKIP + 1)); }
check() { local d=$1; shift; if "$@" >/dev/null 2>&1; then ok "$d"; else ng "$d"; fi; }
has()  { case "$1" in *"$2"*) return 0 ;; *) return 1 ;; esac; }
section() { echo; echo "== $*"; }

# ---------------------------------------------------------------- 0 preflight
section "0 preflight"
echo "  repo:   $REPO"
echo "  work:   $WORK"
echo "  host:   $(uname -srm)"
echo "  bash:   $(command -v bash)  python3: $($PY --version 2>&1)"
KIRO=$(command -v kiro-cli || true)
if [ -n "$KIRO" ]; then
    echo "  kiro:   $KIRO ($("$KIRO" --version 2>&1))"
else
    echo "  kiro:   not found"
fi
HAD_FABLIZE=0; [ -e "$HOME/.fablize" ] && HAD_FABLIZE=1
echo "  ~/.fablize existed before: $HAD_FABLIZE"
if [ -f "$AGENT" ] && ! has "$(cat "$AGENT")" FABLIZE-KIRO; then
    echo "  note: $AGENT exists and was not written by kiro.py; the installer backs it up"
fi
check "python3 >= 3.8" $PY -c 'import sys; sys.exit(sys.version_info < (3, 8))'
check "checkout has LF line endings (hooks/py.sh)" $PY -c 'import sys; sys.exit(b"\r" in open(sys.argv[1], "rb").read())' "$REPO/hooks/py.sh"

# ---------------------------------------------------------------- 1 install
section "1 install (setup/kiro.py global)"
OUT=$($PY "$REPO/setup/kiro.py" global 2>&1); RC=$?
echo "$OUT" | sed 's/^/    | /'
check "installer exit 0" test "$RC" -eq 0
check "runtime copied ($RT/hooks/kiro.py)" test -f "$RT/hooks/kiro.py"
for f in "$AGENT" "$SKILL" "$STEER"; do
    check "written with marker: $f" $PY -c 'import sys; sys.exit("FABLIZE-KIRO" not in open(sys.argv[1]).read())' "$f"
done
if has "$OUT" "commands: bash"; then ok "commands use bash + py.sh (real bash found)"; else ng "commands use bash + py.sh"; fi
if [ -n "$KIRO" ]; then
    if has "$OUT" "✓ kiro-cli agent validate"; then ok "kiro-cli agent validate"; else ng "kiro-cli agent validate"; fi
else
    skip "kiro-cli agent validate (no kiro-cli)"
fi
check "no unresolved \${CLAUDE_PLUGIN_ROOT}/__PLUGIN_ROOT__ in skill/steering" \
    $PY -c 'import sys; t="".join(open(p).read() for p in sys.argv[1:]); sys.exit("CLAUDE_PLUGIN_ROOT" in t or "__PLUGIN_ROOT__" in t)' "$SKILL" "$STEER"

hookcmd() {  # the command line the agent file holds for one hook event
    $PY -c 'import json,sys; print(json.load(open(sys.argv[1]))["hooks"][sys.argv[2]][0]["command"])' "$AGENT" "$1"
}
CMD_PROMPT=$(hookcmd userPromptSubmit) CMD_POST=$(hookcmd postToolUse) CMD_STOP=$(hookcmd stop)
echo "  hook command: $CMD_PROMPT"

# ---------------------------------------------------------------- 2 smoke
section "2 hook smoke (agent hook commands via sh -c, isolated FABLIZE_DATA)"
SMOKE_DATA="$WORK/smoke-data"
P="$WORK/proj"; mkdir -p "$P/src"
hook() {  # hook <command> <json>  -> stdout; exit status must be 0 (hooks fail open)
    printf '%s' "$2" | FABLIZE_DATA="$SMOKE_DATA" FABLIZE_HOLDOUT= sh -c "$1"
}
prompt_json() { printf '{"hook_event_name":"userPromptSubmit","cwd":"%s","session_id":"%s","prompt":"%s"}' "$P" "$1" "$2"; }
DEEP="Refactor the auth module thoroughly and make it production-ready"

out=$(hook "$CMD_PROMPT" "$(prompt_json s-dbg 'test_calc.py fails. Find the root cause and fix it.')")
if has "$out" "[fablize:investigation]"; then ok "debug prompt routes to investigation pack"; else ng "debug prompt routes to investigation pack: $out"; fi
pack=$(echo "$out" | $PY -c 'import re,sys; m=re.search(r"follow (\S+investigation-protocol\.txt)", sys.stdin.read()); print(m.group(1) if m else "")')
check "routed pack path exists ($pack)" test -f "$pack"

out=$(hook "$CMD_PROMPT" "$(prompt_json s-rnd 'Build an HTML page with an SVG chart of 1..5')")
if has "$out" "[fablize:grounding]"; then ok "render prompt routes to grounding pack"; else ng "render prompt routes to grounding pack: $out"; fi

# v3 engine: fs_write, then Stop (hook_event_name "Stop") with no check -> one block, says "once"
hook "$CMD_PROMPT" "$(prompt_json s-v3 "$DEEP")" >/dev/null
out=$(hook "$CMD_POST" "$(printf '{"hook_event_name":"PostToolUse","cwd":"%s","session_id":"s-v3","tool_name":"fs_write","tool_input":{"path":"%s/src/auth.py","text":"x = 1\\n"},"tool_response":"Created the file."}' "$P" "$P")")
check "postToolUse prints nothing" test -z "$out"
out=$(hook "$CMD_STOP" "$(printf '{"hook_event_name":"Stop","cwd":"%s","session_id":"s-v3"}' "$P")")
if echo "$out" | $PY -c 'import json,sys; d=json.loads(sys.stdin.read()); sys.exit(not (d["decision"]=="block" and "comes once" in d["reason"]))' 2>/dev/null; then
    ok "v3: unverified code change blocks stop once"
else
    ng "v3: unverified code change blocks stop once: $out"
fi
# next prompt in that session carries the unverified end
out=$(hook "$CMD_PROMPT" "$(prompt_json s-v3 'ok, what next?')")
if has "$out" "the previous turn ended"; then ok "v3: next prompt carries the unverified end"; else ng "v3: carried gap: $out"; fi

# v3 engine: change + passing execute_bash check -> stop lets through
hook "$CMD_PROMPT" "$(prompt_json s-v3ok "$DEEP")" >/dev/null
hook "$CMD_POST" "$(printf '{"hook_event_name":"PostToolUse","cwd":"%s","session_id":"s-v3ok","tool_name":"str_replace","tool_input":{"path":"%s/src/auth.py","oldStr":"a","newStr":"b"},"tool_response":"Replaced."}' "$P" "$P")" >/dev/null
hook "$CMD_POST" "$(printf '{"hook_event_name":"PostToolUse","cwd":"%s","session_id":"s-v3ok","tool_name":"execute_bash","tool_input":{"command":"python3 -m pytest -q","cwd":null,"run_in_background":false},"tool_response":"Output:\\n3 passed in 0.1s\\n\\nExit Code: 0"}' "$P")" >/dev/null
out=$(hook "$CMD_STOP" "$(printf '{"hook_event_name":"Stop","cwd":"%s","session_id":"s-v3ok"}' "$P")")
check "v3: change + passing check lets stop through" test -z "$out"

# v2 engine: write/create + stop -> block
hook "$CMD_PROMPT" "$(prompt_json s-v2 "$DEEP")" >/dev/null
hook "$CMD_POST" "$(printf '{"hook_event_name":"postToolUse","cwd":"%s","session_id":"s-v2","tool_name":"write","tool_input":{"command":"create","path":"src/auth.py","content":"x = 1\\n"},"tool_response":{"items":[{"Text":"ok"}]}}' "$P")" >/dev/null
out=$(hook "$CMD_STOP" "$(printf '{"hook_event_name":"stop","cwd":"%s","session_id":"s-v2","assistant_response":"Done."}' "$P")")
if echo "$out" | $PY -c 'import json,sys; sys.exit(json.loads(sys.stdin.read())["decision"]!="block")' 2>/dev/null; then
    ok "v2: unverified code change blocks stop"
else
    ng "v2: unverified code change blocks stop: $out"
fi

# goals.py through the exact command the steering file gives the model
GOALS=$($PY -c 'import re,sys; m=re.search(r"`([^`]*goals\.py\")`", open(sys.argv[1]).read()); print(m.group(1) if m else "")' "$STEER")
if [ -n "$GOALS" ]; then
    G="$WORK/goals"; mkdir -p "$G"
    (cd "$G" && sh -c "$GOALS create --brief e2e --goal 'one::first' --goal 'two::second'" >/dev/null 2>&1 \
        && sh -c "$GOALS next" >/dev/null 2>&1 && sh -c "$GOALS status" >/dev/null 2>&1)
    check "steering's goals.py command runs (create/next/status)" test -f "$G/.fablize/goals.json"
else
    ng "steering holds a goals.py command"
fi

# ---------------------------------------------------------------- 3 live
section "3 live kiro-cli (--agent fablize --no-interactive)"
LIVE_DATA="$WORK/live-data"
analyze() {  # analyze <since-epoch> -> one JSON line about what the hooks recorded
    $PY - "$1" "$LIVE_DATA" "$HOME/.fablize" <<'PYEOF'
import json, sys, time
from pathlib import Path
from datetime import datetime
since = float(sys.argv[1])
res = {"root": "", "events": [], "ledgers": []}
for root in map(Path, sys.argv[2:]):
    ev, led = root / "history" / "events.jsonl", root / "ledgers"
    events = []
    if ev.exists():
        for line in ev.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                e = json.loads(line)
                if datetime.fromisoformat(e["ts"]).timestamp() >= since - 1:
                    events.append({"type": e["event_type"], "payload": e.get("payload")})
            except Exception:
                pass
    ledgers = []
    if led.exists():
        for f in led.glob("*.json"):
            if f.stat().st_mtime >= since - 1:
                d = json.loads(f.read_text(encoding="utf-8"))
                ledgers.append({k: d.get(k) for k in ("task_mode", "change_kinds", "last_change",
                                "stop_blocks", "unverified_change")} |
                               {"checks": [(r.get("command", "")[:60], r.get("success")) for r in d.get("verification_results") or []]})
    if events or ledgers:
        res = {"root": str(root), "events": events, "ledgers": ledgers}
        break
print(json.dumps(res))
PYEOF
}
q() { echo "$1" | $PY -c "import json,sys; d=json.loads(sys.stdin.read()); print($2)"; }

live_run() {  # live_run <dir> <log> <prompt>
    local since; since=$(date +%s)
    (cd "$1" && FABLIZE_DATA="$LIVE_DATA" timeout "$TIMEOUT" "$KIRO" chat --no-interactive --trust-all-tools \
        --trust-workspace --agent fablize "$3" >"$2" 2>&1)
    echo "  kiro-cli exit $? (log: $2)"
    tail -n 15 "$2" | sed 's/\x1b\[[0-9;]*[A-Za-z]//g; s/^/    | /'
    LAST=$(analyze "$since")
    echo "  recorded: $LAST"
}

if [ "$LIVE" != 1 ]; then
    skip "live runs (FABLIZE_E2E_LIVE=$LIVE)"
elif [ -z "$KIRO" ]; then
    skip "live runs (no kiro-cli)"
elif ! "$KIRO" whoami >/dev/null 2>&1; then
    skip "live runs (kiro-cli not logged in: run kiro-cli login)"
else
    # A: debugging prompt on a real bug with a runnable test
    A="$WORK/liveA"; mkdir -p "$A"
    printf 'def add(a, b):\n    return a - b\n' >"$A/calc.py"
    printf 'from calc import add\nassert add(2, 3) == 5, add(2, 3)\nprint("ok")\n' >"$A/test_calc.py"
    echo "  [A] debug prompt on a failing test"
    live_run "$A" "$WORK/liveA.log" "python3 test_calc.py fails. Find the root cause in calc.py and fix it. Keep the answer short."
    if [ "$(q "$LAST" 'd["root"]')" != "" ]; then
        echo "  hooks wrote to: $(q "$LAST" 'd["root"]')"
    fi
    check "[A] userPromptSubmit hook ran: router_signal investigation" \
        test "$(q "$LAST" 'any(e["type"]=="router_signal" and any(s["pack"]=="investigation" for s in e["payload"]["signals"]) for e in d["events"])')" = True
    check "[A] postToolUse hook ran: ledger recorded a code change" \
        test "$(q "$LAST" 'any("code" in (l["change_kinds"] or []) for l in d["ledgers"])')" = True
    check "[A] the bug is actually fixed (python3 test_calc.py)" sh -c "cd '$A' && python3 test_calc.py"

    # B: deep task, a code change and no test in the workspace
    B="$WORK/liveB"; mkdir -p "$B"
    echo "  [B] deep prompt, code change, no tests present"
    live_run "$B" "$WORK/liveB.log" "Refactor thoroughly and make it production-ready: create stats.py with a mean(xs) function that raises ValueError on empty input. Keep the answer short."
    check "[B] ledger classified the turn deep" \
        test "$(q "$LAST" 'any(l["task_mode"]=="deep" for l in d["ledgers"])')" = True
    check "[B] postToolUse hook ran: ledger recorded a code change" \
        test "$(q "$LAST" 'any("code" in (l["change_kinds"] or []) for l in d["ledgers"])')" = True
    # The stop gate either blocked (stop_block event) or had nothing to block because
    # the model ran a check first (recorded in the ledger). Report which.
    blocked=$(q "$LAST" 'any(e["type"]=="stop_block" for e in d["events"])')
    checked=$(q "$LAST" 'any(l["checks"] for l in d["ledgers"])')
    echo "  [B] stop_block event: $blocked   checks recorded: $checked"
    check "[B] stop hook enforced verification (blocked, or a check ran)" \
        test "$blocked" = True -o "$checked" = True
    check "[B] stats.py written" test -f "$B/stats.py"

    # C: deep task nudged to skip the check, so the stop gate has something to block.
    # The model may still check first; then the block is not observable (SKIP, not FAIL).
    C="$WORK/liveC"; mkdir -p "$C"
    echo "  [C] deep prompt nudged to stop without a check"
    live_run "$C" "$WORK/liveC.log" "Refactor thoroughly and make it production-ready: create slugify.py with slugify(text). Only write the file, then stop. Reply in one line."
    blocked=$(q "$LAST" 'any(e["type"]=="stop_block" for e in d["events"])')
    after=$(q "$LAST" '[l["checks"] for l in d["ledgers"]]')
    echo "  [C] stop_block event: $blocked   checks recorded: $after"
    if [ "$blocked" = True ]; then
        ok "[C] stop hook blocked an unverified deep change in a live session"
        # Info only: what the model runs after the block is model behaviour, and whether
        # the gate counts it is the classifier's call (a print-only `python3 -c` is no check).
        echo "  INFO  [C] gate counted a check after the block: $(q "$LAST" 'any(l["checks"] for l in d["ledgers"])')"
    elif [ "$(q "$LAST" 'any(l["checks"] for l in d["ledgers"])')" = True ]; then
        skip "[C] model checked before stopping; block not observable this run"
    else
        ng "[C] unverified deep change ended with no stop block"
    fi
fi

# ---------------------------------------------------------------- 4 uninstall
section "4 uninstall"
if [ "$KEEP" = 1 ]; then
    skip "uninstall (FABLIZE_E2E_KEEP=1; work dir kept: $WORK)"
else
    $PY "$REPO/setup/kiro.py" uninstall 2>&1 | sed 's/^/    | /'
    for f in "$AGENT" "$SKILL" "$STEER" "$RT"; do check "removed: $f" test ! -e "$f"; done
    if [ "$HAD_FABLIZE" = 0 ] && [ -d "$HOME/.fablize" ]; then
        rm -rf "$HOME/.fablize"   # created by this run only
    fi
    rm -rf "$WORK"
fi

echo
echo "== result: $PASS passed, $FAIL failed, $SKIP skipped"
[ "$FAIL" -eq 0 ]
