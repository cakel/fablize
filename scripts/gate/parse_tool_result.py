#!/usr/bin/env python3
"""Parse tool inputs/outputs into compact ledger facts (fablize observation gate).

Ported from fable-ish. Detects (a) which files changed and their kind, and
(b) whether a verification command ran and observably succeeded or failed.
"""

from __future__ import annotations

import re
from typing import Any

from ledger import classify_path_kind, redact


VERIFY_RE = re.compile(
    r"(?i)\b("
    r"pytest|unittest|go\s+test|cargo\s+test|npm\s+test|pnpm\s+test|yarn\s+test|bun\s+test|"
    r"mvn\s+test|gradle\s+test|rspec|vitest|jest|playwright|cypress|"
    r"lint|eslint|ruff|flake8|mypy|pyright|tsc|typecheck|"
    r"build|check|validate|verify|json\.tool|py_compile|curl"
    r")\b"
)
# Tools whose output text may encode a failure status. Only tools that RUN
# something qualify. Edit/Write/Read/Grep/Glob merely move content — a file that
# talks about errors, a commit message quoting a traceback, or a CI log being
# read are all data, not failed tool calls.
TEXT_INFERENCE_TOOLS = {"Bash", "PowerShell", "BashOutput"}

# Status-shaped signals only, used when no explicit exit status is available.
# Bare `failed` / `failure` / `error:` / `syntaxerror` were removed: they match
# ordinary prose, changelog entries, and any command that prints or greps text
# about errors — including this file. A detector that fires on reading itself
# teaches the model to ignore it, which costs more than the misses.
FAILURE_RE = re.compile(
    r"(?i)(command not found|no such file or directory|"
    r"exit code [1-9]|exited with code [1-9]|exit status [1-9]|"
    r"\b[1-9][0-9]*\s+(?:tests?\s+)?fail(?:ed|ures?)\b|"
    r"\b[1-9][0-9]*\s+errors?\b|"
    r"tests? failed|build failed|lint failed|"
    r"traceback \(most recent call last\))"
)
SUCCESS_RE = re.compile(r"(?i)\b(passed|success|succeeded|0 failed|build completed|done|valid)\b")
MUTATING_BASH_RE = re.compile(
    r"(?i)\b(apply_patch|python\s+.*\s+-m\s+compileall|chmod|mkdir|mv|cp|rm|touch|"
    r"npm\s+run\s+build|pnpm\s+build|yarn\s+build)\b"
)


def response_text(value: Any, limit: int = 4000) -> str:
    parts: list[str] = []

    def walk(item: Any) -> None:
        if len(" ".join(parts)) > limit:
            return
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, dict):
            for key in ("stdout", "stderr", "output", "message", "text", "content", "error", "summary"):
                if key in item:
                    walk(item[key])
            if not parts:
                for child in item.values():
                    walk(child)
        elif isinstance(item, list):
            for child in item[:20]:
                walk(child)

    walk(value)
    return redact(" ".join(parts), limit)


def command_from_input(input_data: dict[str, Any]) -> str:
    tool_input = input_data.get("tool_input")
    if isinstance(tool_input, dict):
        return str(tool_input.get("command") or tool_input.get("description") or "")
    if isinstance(tool_input, str):
        return tool_input
    return ""


def status_tail(text: str, chars: int = 400) -> str:
    """Last slice of output, where a real status line lands.

    Scanning the whole body makes any command that merely PRINTS the word
    'failed' — grep, cat, curl of a CI log — look like a failed call.
    """
    return (text or "")[-chars:]


def exit_success(input_data: dict[str, Any], text: str) -> bool | None:
    candidates = [input_data, input_data.get("tool_response")]
    for candidate in candidates:
        if isinstance(candidate, dict):
            for key in ("success", "ok"):
                if isinstance(candidate.get(key), bool):
                    return bool(candidate[key])
            for key in ("exit_code", "exitCode", "returncode", "status"):
                value = candidate.get(key)
                if isinstance(value, int):
                    return value == 0
                if isinstance(value, str) and value.isdigit():
                    return int(value) == 0
    # No explicit status. Infer from text only when the call was a tool that runs
    # something AND the command was a verification. `grep`, `cat`, `git log` move
    # text around; the words in that text say nothing about whether the call
    # worked. Only a test/build/lint run encodes its own verdict in its output.
    if str(input_data.get("tool_name") or "") not in TEXT_INFERENCE_TOOLS:
        return None
    if not is_verification_command(command_from_input(input_data)):
        return None
    tail = status_tail(text)
    # Failure wins over a co-occurring success token: pytest's "1 failed, 990
    # passed" is a failed run. Known ceiling — `redact()` flattens newlines, so
    # this cannot tell "failure earlier, clean summary later" from a real
    # failure. Preserving newlines through response_text is the upgrade path.
    if FAILURE_RE.search(tail):
        return False
    if SUCCESS_RE.search(tail):
        return True
    return None


def is_verification_command(command: str) -> bool:
    return bool(VERIFY_RE.search(command or ""))


def detect_failure(input_data: dict[str, Any]) -> dict[str, Any] | None:
    text = response_text(input_data.get("tool_response", input_data))
    # exit_success already does the text pass (tool- and tail-gated). Re-running
    # FAILURE_RE over the whole body here is what let content override "no status".
    if exit_success(input_data, text) is False:
        return {"kind": "tool-result", "summary": redact(text or command_from_input(input_data), 240)}
    return None


def changed_paths(input_data: dict[str, Any]) -> list[str]:
    tool_name = str(input_data.get("tool_name") or "")
    tool_input = input_data.get("tool_input")
    paths: list[str] = []
    if isinstance(tool_input, dict):
        file_path = tool_input.get("file_path")
        if file_path:
            paths.append(str(file_path))
    if tool_name in {"Edit", "Write", "NotebookEdit", "MultiEdit"}:
        return paths or ["edit"]
    return paths


def changed_kinds(input_data: dict[str, Any]) -> list[str]:
    paths = changed_paths(input_data)
    if paths:
        return sorted({classify_path_kind(path.strip()) for path in paths})
    tool_name = str(input_data.get("tool_name") or "")
    command = command_from_input(input_data)
    if tool_name == "Bash" and MUTATING_BASH_RE.search(command):
        return ["other"]
    return []


def verification_record(input_data: dict[str, Any]) -> dict[str, Any] | None:
    command = command_from_input(input_data)
    if not command or not is_verification_command(command):
        return None
    text = response_text(input_data.get("tool_response", input_data), 1000)
    success = exit_success(input_data, text)
    return {
        "command": redact(command, 220),
        "success": bool(success) if success is not None else None,
        "summary": redact(text, 220),
    }


def _failure_signature(summary: str) -> str:
    """Normalize a failure summary into a stable class key. Numbers and paths
    differ between occurrences of the same failure, so collapse them so that
    e.g. two 'ECONNREFUSED localhost:5432' land on the same class."""
    s = (summary or "").lower()
    s = re.sub(r"[/\\][^\s]+", " path ", s)   # paths vary
    s = re.sub(r"\d+", "#", s)                # numbers vary
    s = re.sub(r"\s+", " ", s).strip()
    return s[:80]


def repeated_failure(failures: list[dict[str, Any]], threshold: int = 2) -> tuple[str, int] | None:
    """If the most recent failure's class has occurred `threshold`+ times in the
    ledger, return (signature, count). Drives the silent-recovery guard: recover
    quietly from one-offs, but disclose a repeating failure class."""
    if not failures:
        return None
    sig = _failure_signature(failures[-1].get("summary", ""))
    if not sig:
        return None
    count = sum(1 for f in failures if _failure_signature(f.get("summary", "")) == sig)
    return (sig, count) if count >= threshold else None
