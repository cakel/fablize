#!/usr/bin/env python3
"""Parse tool inputs/outputs into compact ledger facts (fablize observation gate).

Ported from fable-ish. Detects (a) which files changed and their kind, and
(b) whether a verification command ran and observably succeeded or failed.
"""

from __future__ import annotations

import re
from typing import Any

from ledger import classify_path_kind, redact


# Tool names distinctive enough that seeing them anywhere in the command is
# evidence on its own.
VERIFY_TOOL_RE = re.compile(
    r"(?i)\b("
    r"pytest|unittest|go\s+test|cargo\s+test|npm\s+test|pnpm\s+test|yarn\s+test|bun\s+test|"
    r"mvn\s+(?:test|verify|package)|gradle\s+test|rspec|vitest|jest|playwright|cypress|"
    r"lint|eslint|ruff|flake8|mypy|pyright|tsc|typecheck|"
    r"npm\s+run\s+build|pnpm\s+build|yarn\s+build|cargo\s+build|go\s+build|gradle\s+build|"
    r"json\.tool|py_compile"
    r")\b"
)
# Words that are ALSO ordinary English and common path segments, so they are
# evidence only in command position. `cat build.log`, `grep -rn build src/` and
# `ls build/` are reads, not verification runs — counting them satisfied the deep
# Stop gate without any check having run.
VERIFY_VERBS = {"build", "check", "validate", "verify", "curl", "make"}
# Same rule for mutation: an unanchored match makes `grep -rn "rm -rf" docs/`
# look like a file change and blocks a turn that only read files.
MUTATING_CMDS = {"apply_patch", "chmod", "mkdir", "mv", "cp", "rm", "touch"}
MUTATING_TOOL_RE = re.compile(
    r"(?i)\b(python\s+.*\s+-m\s+compileall|npm\s+run\s+build|pnpm\s+build|yarn\s+build)\b"
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

_SEGMENT_RE = re.compile(r"[;&|\n]+")
_ENV_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def command_words(command: str) -> set[str]:
    """The command word of each segment of a shell line.

    `a && b | c` -> {a, b, c}. Leading env assignments and sudo are skipped, and
    a path is reduced to its basename, so `/usr/bin/curl` counts as `curl`.
    """
    words: set[str] = set()
    for segment in _SEGMENT_RE.split(command or ""):
        for token in segment.split():
            token = token.strip("'\"()")
            if not token or _ENV_ASSIGN_RE.match(token):
                continue
            if token in {"sudo", "command", "exec", "time", "env"}:
                continue
            words.add(token.replace("\\", "/").rsplit("/", 1)[-1].lower())
            break
    return words


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


def status_text(input_data: dict[str, Any], chars: int = 400) -> str:
    """True tail of the tool output, for status inference.

    Must not be derived from response_text(): that truncates from the FRONT
    (its cap stops the walk early, and redact keeps `value[:limit-3]`), so for a
    long run it returns the START of the output — the opposite end from the
    status line. Slicing that result yields a mid-run window, which reads a
    failing `pytest` as green.
    """
    parts: list[str] = []
    total = 0

    def walk(item: Any) -> None:
        nonlocal total
        if total > 100_000:  # ponytail: pathological payload guard, not a real cap
            return
        if isinstance(item, str):
            parts.append(item)
            total += len(item)
        elif isinstance(item, dict):
            named = [k for k in ("stdout", "stderr", "output", "message", "text",
                                 "content", "error", "summary") if k in item]
            for key in named or list(item):
                walk(item[key])
        elif isinstance(item, list):
            for child in item[:20]:
                walk(child)

    walk(input_data.get("tool_response", input_data))
    joined = " ".join(parts)[-(chars * 4):]
    return redact(joined, chars * 4)[-chars:]


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
    tail = status_text(input_data)
    # Failure wins over a co-occurring success token: pytest's "1 failed, 990
    # passed" is a failed run. Known ceiling — `redact()` flattens newlines, so
    # this cannot tell "failure earlier, clean summary later" from a real
    # failure. Preserving newlines through redact is the upgrade path.
    if FAILURE_RE.search(tail):
        return False
    if SUCCESS_RE.search(tail):
        return True
    return None


def is_verification_command(command: str) -> bool:
    if VERIFY_TOOL_RE.search(command or ""):
        return True
    return bool(command_words(command) & VERIFY_VERBS)


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
    if tool_name == "Bash" and (
        MUTATING_TOOL_RE.search(command) or (command_words(command) & MUTATING_CMDS)
    ):
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
