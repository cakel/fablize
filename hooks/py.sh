#!/usr/bin/env bash
# fablize: run Python with whichever interpreter this host has.
# python.org's Windows installer ships python.exe and the `py` launcher but no
# python3.exe, so a hardcoded `python3` printed "command not found" on every
# prompt and tool call. Under WindowsApps there are two kinds of python3.exe: the
# Store-alias STUB (no Python installed; prints "Python was not found" and exits
# non-zero) and the real Store Python's execution alias. Only a probe tells them
# apart, so WindowsApps entries are probed; everything else execs directly (no
# extra process on the common path). No interpreter -> exit 0 silently: hooks
# fail open. setup.sh/uninstall.sh call this file too; it is the only copy of
# this rule.
#
# PYTHONUTF8=1: Windows Python otherwise reads stdin and writes stdout in the
# locale codepage (cp949, cp1252). The hooks then crashed on a Korean prompt or a
# smart quote, setup.sh died on its own "✓" before writing progress.json, and
# goals.py raised AFTER saving state. Claude Code does not set it for hooks.
export PYTHONUTF8=1
for p in python3 python py; do
  bin="$(command -v "$p" 2>/dev/null)" || continue
  case "$bin" in
    *WindowsApps*) "$bin" -c "import sys" >/dev/null 2>&1 </dev/null || continue ;;
    # macOS without Command Line Tools: /usr/bin/python3 is an xcrun shim that
    # errors instead of running. Probe it there only ($OSTYPE costs no spawn).
    /usr/bin/python3) case "${OSTYPE:-}" in darwin*)
        "$bin" -c "import sys" >/dev/null 2>&1 </dev/null || continue ;; esac ;;
  esac
  exec "$bin" "$@"
done
exit 0
