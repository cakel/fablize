#!/usr/bin/env bash
# fablize UserPromptSubmit router (Claude Code hook). The logic lives in router.py
# so Kiro CLI, whose hook runner is PowerShell or cmd.exe, can run it without bash.
# stdin: JSON {"prompt": "..."}. stdout: extra context (only when a signal matches). Always exits 0.
bash "$(dirname "$0")/py.sh" "$(dirname "$0")/router.py" || true
exit 0
