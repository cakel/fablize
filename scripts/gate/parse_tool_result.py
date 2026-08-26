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
# A suite that runs as a plain script — `python tests/test_x.py`, exiting non-zero
# on failure — is invisible to both of the above: no pytest/unittest token for
# VERIFY_TOOL_RE, and the command word is `python`, not a verb. fablize's own suite
# is exactly that shape, as is any stdlib-only project's, so a deep turn that ran
# its tests still recorded zero verifications and got blocked at Stop.
#
# Matched in COMMAND POSITION only: the segment's command word must be python and
# one of its arguments must look like a test entry point. `cat test_x.py`,
# `grep "python test_x.py" docs/` and `rm test_x.py` therefore stay negative — the
# false-positive class this module exists to avoid.
_PYTHON_CMD_RE = re.compile(r"(?i)^python[0-9.]*$")
_TEST_ENTRY_RE = re.compile(r"(?i)(?:^|/)(?:test[_-][^/]+|[^/]+[_-]test)\.py$")
# A project's own checker — `python tools/check_doc.py --strict`, `verify_gates.py`,
# `lint_diagram.py` — is the same blind spot one step over. It runs a real check and
# exits non-zero on failure, but `check`/`verify`/`lint` sit in an ARGUMENT, and
# VERIFY_VERBS only counts them in command position. So a deep turn that ran its
# document checker recorded zero verifications and the Stop gate blocked it.
#
# The `[_-]` is load-bearing, exactly as it is for test entry points: it keeps
# `checkout.py`, `checker.py`, `verifier.py` and `linter.py` negative. Matching a
# bare `check` prefix would count any script whose name merely starts that way.
_CHECK_ENTRY_RE = re.compile(
    r"(?i)(?:^|/)(?:(?:check|verify|validate|lint)[_-][^/]+"
    r"|[^/]+[_-](?:check|verify|validate|lint))\.py$"
)
_CMD_PREFIXES = {"sudo", "command", "exec", "time", "env"}
# Same rule for mutation: an unanchored match makes `grep -rn "rm -rf" docs/`
# look like a file change and blocks a turn that only read files.
#
# `mkdir` is deliberately absent. Creating an empty directory changes no file and
# leaves nothing whose behaviour could be verified, but it set changed_files_seen
# and pinned change_kinds to ["other"] — and change_kinds ACCUMULATES across a
# turn, so one `mkdir -p` for a scratch directory disarmed docs_only() for every
# later edit in that turn. Observed twice in one session, both times on a turn
# that touched nothing but Markdown.
MUTATING_CMDS = {
    "apply_patch", "chmod", "mv", "cp", "rm", "touch",
    # PowerShell's own verbs. cp/mv/rm are aliases there and already covered, but a
    # script written in cmdlet form is invisible without these. `New-Item` is listed
    # for the file case; the directory case is the `mkdir` argument above, and both
    # go through the same operand classification.
    "copy-item", "move-item", "remove-item", "new-item",
    "set-content", "add-content", "out-file",
}
MUTATING_TOOL_RE = re.compile(
    r"(?i)\b(python\s+.*\s+-m\s+compileall|npm\s+run\s+build|pnpm\s+build|yarn\s+build)\b"
)
# Tools whose output text may encode a failure status. Only tools that RUN
# something qualify. Edit/Write/Read/Grep/Glob merely move content — a file that
# talks about errors, a commit message quoting a traceback, or a CI log being
# read are all data, not failed tool calls.
TEXT_INFERENCE_TOOLS = {"Bash", "PowerShell", "BashOutput"}
# Tools that RUN a shell line, so their command can be classified as a mutation.
# BashOutput is excluded: it reads the output of a call that already happened, and
# counting it would record the same change twice.
SHELL_TOOLS = {"Bash", "PowerShell"}

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
    """The command a call actually ran. Never the prose describing it.

    This used to fall back to `tool_input["description"]`, a human sentence that
    every classifier here then read as a shell line. The fallback dates to the
    gate's first commit with no recorded rationale, and it cuts both ways:

        description="check the build output"     -> is_verification_command True
        description="mkdir the output directory" -> changed_kinds ["other"]

    The first is the dangerous one. A false CHANGE only makes the gate noisy; a
    false VERIFICATION makes it pass silently, which is the one failure this gate
    exists to prevent. No command ran, so there is no command to classify.
    """
    tool_input = input_data.get("tool_input")
    if isinstance(tool_input, dict):
        return str(tool_input.get("command") or "")
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


def runs_script_test(command: str) -> bool:
    """True when a segment invokes python on a test or checker entry point.

    Evidence must be the command being run, never a filename that merely appears
    somewhere in it — so this walks each segment, only inspects arguments once the
    command word is confirmed to be python, and then looks at exactly one of them:
    the script python actually executes. `python manage.py test_import.py` runs
    manage.py, so it is not a test run, however its argument is named.

    Two entry-point shapes count: a test suite (`test_x.py` / `x_test.py`) and a
    project's own checker (`check_doc.py` / `verify_gates.py` / `schema_lint.py`).
    Both run a real check and exit non-zero when it fails, which is all the gate
    needs from a verification.
    """
    for segment in _SEGMENT_RE.split(command or ""):
        command_seen = False
        for raw in segment.split():
            token = raw.strip("'\"()")
            if not token:
                continue
            if not command_seen:
                if _ENV_ASSIGN_RE.match(token) or token in _CMD_PREFIXES:
                    continue
                command_seen = True
                if not _PYTHON_CMD_RE.match(token.replace("\\", "/").rsplit("/", 1)[-1]):
                    break  # not a python invocation — nothing in this segment counts
                continue
            if token.startswith("-"):
                continue  # a flag like -B or -u
            # The first non-flag argument is what python runs — the module name under
            # -m, the inline source under -c, or the script path. Decide on it and
            # stop; `-m pytest` is VERIFY_TOOL_RE's job, and no module name matches
            # a *.py entry point anyway.
            script = token.replace("\\", "/")
            return bool(_TEST_ENTRY_RE.search(script) or _CHECK_ENTRY_RE.search(script))
    return False


def is_verification_command(command: str) -> bool:
    if VERIFY_TOOL_RE.search(command or ""):
        return True
    if runs_script_test(command):
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


def mutated_paths(command: str) -> list[str]:
    """The operands of each mutating segment — what the command actually touches.

    Same command-position walk as `runs_script_test`: a segment counts only once
    its command word is confirmed to be a mutating one, so `grep -rn "rm -rf" docs/`
    contributes nothing. Flags are skipped, and so are redirections and bare
    numbers, which `_SEGMENT_RE` leaves behind when it splits `2>&1`.

    Returns operands, not kinds — the caller classifies. An empty list means the
    command mutates something this cannot name, and the caller stays conservative.
    """
    operands: list[str] = []
    for segment in _SEGMENT_RE.split(command or ""):
        mutating = False
        for raw in segment.split():
            token = raw.strip("'\"()")
            if not token:
                continue
            if not mutating:
                if _ENV_ASSIGN_RE.match(token) or token in _CMD_PREFIXES:
                    continue
                word = token.replace("\\", "/").rsplit("/", 1)[-1].lower()
                if word not in MUTATING_CMDS:
                    break  # not a mutating invocation — nothing in this segment counts
                mutating = True
                continue
            if token.startswith("-"):
                continue  # a flag like -r, -f or --preserve=all
            if "<" in token or ">" in token or token.isdigit():
                continue  # redirection debris, not an operand
            operands.append(token)
    return operands


def changed_kinds(input_data: dict[str, Any]) -> list[str]:
    paths = changed_paths(input_data)
    if paths:
        return sorted({classify_path_kind(path.strip()) for path in paths})
    tool_name = str(input_data.get("tool_name") or "")
    command = command_from_input(input_data)
    if tool_name in SHELL_TOOLS and (
        MUTATING_TOOL_RE.search(command) or (command_words(command) & MUTATING_CMDS)
    ):
        # Classify what the command names before falling back to "other". Reporting
        # "other" for `cp notes.md docs/notes.md` is a claim about a file the gate
        # never looked at: it says a non-docs thing changed when only Markdown moved,
        # and docs_only() then keeps the deep gate armed for the rest of the turn.
        operands = mutated_paths(command)
        if operands:
            return sorted({classify_path_kind(path) for path in operands})
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
