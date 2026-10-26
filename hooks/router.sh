#!/usr/bin/env bash
# fablize UserPromptSubmit router — when a task signal is detected, inject the relevant pack discipline as context.
# Routing: smallest matching pack only / overlap only when genuinely multi-category / mimic observable behavior only.
# Only verified packs are auto-routed.
# stdin: JSON {"prompt": "..."}. stdout: extra context (only when a signal matches). Always exits 0.
set -uo pipefail

# Plugin root: prefer the runtime-injected var, else fall back to this script's location.
ROOT="${CLAUDE_PLUGIN_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
PACKS="$ROOT/packs"

# Signals come from classify_task.py (DEBUG_RE / RENDER_RE), the same lists the
# task-mode classifier uses: the router used to miss "근본 원인 찾아서 고쳐줘" /
# "figure out why" while the classifier, with its own list, called them quick.
# Each matched signal is also logged out-of-band (events.jsonl) so router hits can
# be audited later; the prompt text itself is not logged.
sigs="$(bash "$ROOT/hooks/py.sh" -c 'import sys,json
sys.path.insert(0, sys.argv[1] + "/scripts/gate"); sys.path.insert(0, sys.argv[1] + "/scripts/shadow")
try:
    d = json.load(sys.stdin)
    from classify_task import signals
    found = signals(d.get("prompt") or "")
except Exception:
    sys.exit(0)
for name, _ in found:
    print(name)
if found:
    try:
        from shadow_logger import log_event
        log_event(d.get("session_id") or "", "router_signal", {"signals": [{"pack": n, "matched": m[:40]} for n, m in found]})
    except Exception:
        pass' "$ROOT" 2>/dev/null || true)"
[ -z "${sigs:-}" ] && exit 0

emit=""
add() { emit="${emit:+$emit
}$1"; }

case "$sigs" in *investigation*)
  add "[fablize:investigation] Debugging/root-cause signal — follow $PACKS/investigation-protocol.txt: reproduce first, form 3+ competing hypotheses, gather evidence per hypothesis, trace the full causal chain, check every sibling caller of the defective code, prove the fix with a test that failed before it, and report the hypotheses you rejected." ;;
esac
case "$sigs" in *grounding*)
  add "[fablize:grounding] Render/executable artifact signal — follow $PACKS/verification-grounding-pack.txt grounding loop: run it in the real renderer, observe the actual output, fix what the observation reveals, then re-run. A static check is not observation." ;;
esac

[ -n "$emit" ] && printf '%s\n' "$emit"
exit 0
