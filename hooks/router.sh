#!/usr/bin/env bash
# fablize UserPromptSubmit router (Claude Code hook). The logic lives in router.py
# so Kiro CLI, whose hook runner is PowerShell or cmd.exe, can run it without bash.
# stdin: JSON {"prompt": "..."}. stdout: extra context (only when a signal matches). Always exits 0.
# Git's usr/bin/bash.exe started by path from a PATH without Git does not add
# /usr/bin, so neither `bash` nor `dirname` is found there (issue #28): fall back to
# "$BASH" and ${0%/*}. dirname stays first: it also splits D:\...\router.sh, which
# Cygwin bash's ${0%[/\\]*} does not.
d=$(dirname "$0" 2>/dev/null) || d="${0%/*}"
"${BASH:-bash}" "$d/py.sh" "$d/router.py" || true
exit 0
