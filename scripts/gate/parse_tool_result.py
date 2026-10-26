#!/usr/bin/env python3
"""Parse tool inputs/outputs into compact ledger facts (fablize observation gate).

Ported from fable-ish. Detects (a) which files changed and their kind, and
(b) whether a verification command ran and observably succeeded or failed.
"""

from __future__ import annotations

import re
import shlex
from typing import Any

from ledger import classify_path_kind, redact


# A verification runner, matched at the START of a segment's command (see
# _segment_heads), never anywhere in the line. An unanchored match counted
# `pip install pytest`, `cat jest.config.js`, `git commit -m "fix lint"` and
# `rg pytest README.md` as verifications — success=None, which satisfies the
# deep Stop gate, so an unverified change went through.
VERIFY_HEAD_RE = re.compile(
    r"(?i)^(?:"
    r"pytest|py\.test|unittest|tox|nox|rspec|vitest|jest|mocha|ava|playwright|cypress|"
    r"phpunit|pest|ctest|eslint|ruff|flake8|pylint|mypy|pyright|tsc|biome|shellcheck|"
    r"golangci-lint|stylelint|typecheck|"
    r"(?:go|cargo|swift|dotnet|deno|zig)(?:\s+-\S+)*\s+(?:test|build|check|vet|lint|clippy)|"
    r"bun\s+test|"
    r"(?:npm|pnpm|yarn|bun)(?:\s+run)?\s+(?:test|lint|build|typecheck|type-check|check|e2e)[\w:.-]*|"
    r"(?:mvn|mvnw|gradle|gradlew)(?:\s+-\S+)*(?:\s+clean)?\s+(?:test|verify|package|build|check)|"
    r"node\s+--test|rake\s+(?:test|spec)|invoke-pester|"
    r"pre-commit\s+run|cmake\s+--build|ninja|meson\s+(?:test|compile)|"
    r"bitbake(?!\s+(?:-\S+\s+)*-c\s*clean)|"
    r"python(?:\s+-[XW]\s+\S+|\s+-[^m\s]\S*)*\s+-m\s+(?:pytest|unittest|json\.tool|py_compile|compileall|"
    r"mypy|ruff|tox|nox|pylint|flake8)"
    r")(?:\s|$)"
)
# A runner may sit behind any wrapper — `coverage run -m pytest`, `npx --yes jest`,
# `nice -n 10 pytest`, `xvfb-run -a npm test`, `bash -c "pytest -q"` — and listing
# wrappers one by one kept missing them. So VERIFY_HEAD_RE may match at ANY token
# of a segment, unless the segment's command word only reads, installs or
# records text (`cat pytest.ini`, `pip install pytest`, `git commit -m "lint"`).
_NON_RUNNER_WORDS = {
    "cd", "pushd", "popd", "cat", "less", "more", "head", "tail", "bat", "type", "rg", "grep", "egrep", "fgrep", "ag",
    "ack", "find", "fd", "ls", "dir", "tree", "echo", "printf", "print", "write-host",
    "write-output", "get-content", "select-string", "get-childitem", "wc", "diff", "sort",
    "uniq", "awk", "sed", "cut", "tr", "jq", "yq", "git", "gh", "glab", "hg", "svn", "pip", "pip3", "pipx",
    "conda", "brew", "apt", "apt-get", "yum", "dnf", "choco", "winget", "scoop", "cp", "mv", "rm",
    "touch", "mkdir", "chmod", "chown", "ln", "code", "vim", "vi", "nano", "emacs", "open", "start",
    "man", "which", "where", "command", "type", "alias", "export", "set", "unset", "source",
    "copy-item", "move-item", "remove-item", "new-item", "set-content", "add-content", "out-file",
}
_INSTALL_WORDS = {"install", "i", "add", "remove", "rm", "uninstall", "update", "upgrade", "ci",
                  "init", "create", "info", "view", "show", "list", "ls", "outdated", "why", "audit"}
# VERIFY_HEAD_RE without its `^`, for `.match(text, pos)` at a token offset.
_VERIFY_AT_RE = re.compile(VERIFY_HEAD_RE.pattern.replace("(?i)^(?:", "(?i)(?:", 1))
# Wrappers that run the NEXT words as the command: `npx jest`, `uv run pytest`.
_RUNNER_PREFIXES = {"npx", "bunx", "pnpx"}
_RUNNER_PAIRS = {("uv", "run"), ("poetry", "run"), ("pipenv", "run"), ("bundle", "exec"),
                 ("pnpm", "exec"), ("yarn", "dlx"), ("hatch", "run"), ("pdm", "run"), ("rye", "run")}
# Words that are ALSO ordinary English and common path segments, so they are
# evidence only in command position. `cat build.log`, `grep -rn build src/` and
# `ls build/` are reads, not verification runs — counting them satisfied the deep
# Stop gate without any check having run.
# `make` is not here: `make clean` / `make install` verify nothing. VERIFY_HEAD_RE
# matches bare `make` and `make test|check|lint|build|all`.
VERIFY_VERBS = {"build", "check", "validate", "verify", "curl", "cmp"}
# A suite that runs as a plain script — `python tests/test_x.py`, exiting non-zero
# on failure — is invisible to both of the above: no pytest/unittest head for
# VERIFY_HEAD_RE, and the command word is `python`, not a verb. fablize's own suite
# is exactly that shape, as is any stdlib-only project's, so a deep turn that ran
# its tests still recorded zero verifications and got blocked at Stop.
#
# Matched in COMMAND POSITION only: the segment's command word must be python and
# one of its arguments must look like a test entry point. `cat test_x.py`,
# `grep "python test_x.py" docs/` and `rm test_x.py` therefore stay negative — the
# false-positive class this module exists to avoid.
# `py` (the Windows launcher) and `python.exe` (a venv's Scripts/python.exe) are
# python too; `bash .../py.sh x.py` is this plugin's own way to run one.
_PYTHON_CMD_RE = re.compile(r"(?i)^(?:python[0-9.]*|py)(?:\.exe)?$")
_TEST_ENTRY_RE = re.compile(
    r"(?i)(?:^|/)(?:test[_-][^/]+|[^/]+[_-]tests?|tests?|run[_-]?tests?|runtests)\.(?:py|sh|ps1)$")
# A project's own checker — `python tools/check_doc.py --strict`, `verify_gates.py`,
# `lint_diagram.py` — is the same blind spot one step over. It runs a real check and
# exits non-zero on failure, but `check`/`verify`/`lint` sit in an ARGUMENT, and
# VERIFY_VERBS only counts them in command position. So a deep turn that ran its
# document checker recorded zero verifications and the Stop gate blocked it.
#
# The `[_-]` is load-bearing, exactly as it is for test entry points: it keeps
# `checkout.py`, `checker.py`, `verifier.py` and `linter.py` negative. Matching a
# bare `check` prefix would count any script whose name merely starts that way.
# Exact names (`gate.py`, `check.py`, `lint.sh`) and `*_gate` count too: a project
# checker named `tools/gate.py` ran a real check the gate never saw. `.sh`/`.ps1`
# checkers count when run through bash/pwsh (a PowerShell Word-COM page check
# can live in `check_pages.ps1`).
_CHECK_ENTRY_RE = re.compile(
    # `gate` only as a whole name or suffix: `gate_prompt.py` is a hook, not a check.
    r"(?i)(?:^|/)(?:(?:check|verify|validate|lint)[_-][^/]+"
    r"|[^/]+[_-](?:check|verify|validate|lint|gate)"
    r"|check|verify|validate|lint|gate|selftest|self_test)\.(?:py|sh|ps1)$"
)
_SHELL_RUNNERS = {"bash", "sh", "zsh", "pwsh", "powershell", "source", "."}
_CMD_PREFIXES = {"sudo", "command", "exec", "time", "env"}
# `ssh host 'cd x && cp .env .env.bak && ...'` runs on ANOTHER machine, but
# _SEGMENT_RE ignores quotes and splits inside the payload, so the remote `cp`
# looked like a local file change. Every deploy turn then armed the deep Stop gate
# with a change nothing local could verify. Drop the quoted payload before
# looking for mutations; verification still sees the full command.
_SSH_PAYLOAD_RE = re.compile(r"""\bssh\b[^;&|\n'"]*?(['"]).*?\1""", re.S)
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
# Two tiers. A runner's own verdict is decisive wherever it appears in the tail.
VERDICT_FAILURE_RE = re.compile(
    r"(?i)(\b[1-9][0-9]*\s+(?:tests?\s+)?fail(?:ed|ures?)\b|"
    r"tests? failed|build failed|lint failed|"
    # pytest's own crash and empty-run markers. `pytest tests` over script-style
    # suites hit INTERNALERROR / "no tests ran" and was ledgered as success=None,
    # which satisfies the Stop gate as if a check had passed.
    r"\binternalerror\b|\bno tests ran\b)"
)
# Runner-specific failure markers, case-sensitive so prose ("fail to …") is safe.
# A piped check (`go test ./... | tail`) exits 0, so its verdict is only in the text.
VERDICT_FAILURE_CS_RE = re.compile(
    r"--- FAIL:|\bFAIL\t|\bFAIL\s+\S+\s+[\d.]+s\b|\berror TS\d+|\u2716 \d+ problems?|"
    r"\bFound [1-9]\d* errors?\b|test result: FAILED|\berror\[E\d{4}\]|\bFAILED tests?/|"
    r"\b[1-9]\d* problems? \([1-9]\d* errors?"
)
# Generic shell errors can come from any segment of a chained line, so they
# only count when no success summary follows them (see exit_success).
FAILURE_RE = re.compile(
    r"(?i)(command not found|no such file or directory|differ: (?:byte|char)|"
    r"exit code [1-9]|exited with code [1-9]|exit status [1-9]|"
    r"\b[1-9][0-9]*\s+errors?\b|"
    r"traceback \(most recent call last\))"
)
SUCCESS_RE = re.compile(r"(?i)\b(passed|success|succeeded|0 failed|build completed|done|valid)\b")

_HEREDOC_OPEN_RE = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_]\w*)\1")


def _heredoc_parts(text: str) -> tuple[str, list[tuple[str, str]]]:
    """(text with heredoc bodies and closers removed, [(opener line, body)]).

    Line-based and linear. The old regex was O(n^2) when `<<` had no terminator
    (C++ `cout << x` written through a PowerShell here-string): 40s at 1,000
    lines against a 30s hook timeout. A `<<` whose delimiter never appears on a
    later line of its own is not a heredoc and is left alone.
    """
    from bisect import bisect_right

    lines = (text or "").split("\n")
    closers: dict[str, list[int]] = {}
    for i, ln in enumerate(lines):
        closers.setdefault(ln.strip(), []).append(i)
    kept: list[str] = []
    bodies: list[tuple[str, str]] = []
    i = 0
    while i < len(lines):
        ln = lines[i]
        kept.append(ln)
        m = _HEREDOC_OPEN_RE.search(ln) if "<<" in ln else None
        if m and ln[m.start():m.start() + 3] != "<<<":
            idxs = closers.get(m.group(2), [])
            k = bisect_right(idxs, i)
            if k < len(idxs):
                end = idxs[k]
                bodies.append((ln, "\n".join(lines[i + 1:end])))
                i = end + 1
                continue
        i += 1
    return "\n".join(kept), bodies


def _strip_heredocs(text: str) -> str:
    return _heredoc_parts(text)[0]
_SEGMENT_RE = re.compile(r"[;&|\n]+")


def _segments(command: str) -> list[str]:
    """Shell segments, with heredoc bodies dropped and separators inside quotes
    masked. Splitting the raw line made `git commit -m "wip; pytest failing"` and
    a heredoc'd Makefile line `\tpytest -q` look like verification runs — and,
    as the latest result, satisfy the gate right after a real failing run.
    """
    # A line continuation joins its lines: `cd x && \<newline>python tools/check.py`
    # is one segment whose command is `python`, not a token starting with a space.
    text = _strip_heredocs(command).replace("\\\r\n", " ").replace("\\\n", " ").replace("`\n", " ")
    out, quote, esc = [], "", False
    for ch in text:
        if esc:  # `\"` inside double quotes is a literal quote, not the end
            esc = False
            out.append(" " if ch in ";&|\n" else ch)
            continue
        if quote == "#":
            if ch != "\n":
                continue
            quote = ""
        elif quote:
            if quote == '"' and ch == "\\":
                esc = True
            elif ch == quote:
                quote = ""
            elif ch in ";&|\n":
                ch = " "
        elif ch == "\\":
            esc = True  # outside quotes too: `\"` is a literal quote, not an opening one
        elif ch in "'\"":
            quote = ch
        elif ch == "#" and (not out or out[-1] in " \t\n;&|("):
            # A comment runs to end of line. Unskipped, the apostrophe in
            # `# don't use -x` opened a "quote" that swallowed every later line.
            quote = "#"
            continue
        out.append(ch)
    return _SEGMENT_RE.split("".join(out))
_ENV_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def _segment_heads(command: str) -> list[list[str]]:
    """Each segment's tokens from its real command word on, normalised.

    Skips env assignments, sudo/time/env/exec, `timeout N`, and runner wrappers
    (`npx`, `uv run`, `bundle exec`). The command word is reduced to a lowercase
    basename without .exe/.cmd/.bat, and any python spelling (`py`, `python3.12`,
    `.venv/Scripts/python.exe`, `bash hooks/py.sh`) becomes `python`.
    """
    return [h for h in (_head(segment) for segment in _segments(command)) if h]


def _tokens(segment: str) -> list[str]:
    """Quote-aware tokens: `S="C:/a b"` is one token, not two. Backslashes become
    slashes first so a Windows path survives posix shlex."""
    try:
        return [t for t in shlex.split(segment.replace("\\", "/")) if t.strip("()")]
    except ValueError:  # unbalanced quotes
        return [t for t in (t.strip("'\"()") for t in segment.split()) if t]


def _head(segment: str) -> list[str] | None:
    """One segment's tokens from its real command word on, normalised (see
    _segment_heads), or None when the segment runs nothing (`S=…`)."""
    tokens = [t.strip("()") for t in _tokens(segment)]
    tokens = [t for t in tokens if t]
    i = 0
    while i < len(tokens):
        low = tokens[i].lower()
        if _ENV_ASSIGN_RE.match(tokens[i]) or low in _CMD_PREFIXES or low in _RUNNER_PREFIXES:
            i += 1
        elif low == "timeout":
            i += 2 if i + 1 < len(tokens) and tokens[i + 1][:1].isdigit() else 1
        elif i + 1 < len(tokens) and (low, tokens[i + 1].lower()) in _RUNNER_PAIRS:
            i += 2
        else:
            break
    rest = tokens[i:]
    if not rest:
        return None
    word = re.sub(r"(?i)\.(?:exe|cmd|bat)$", "",
                  rest[0].replace("\\", "/").rsplit("/", 1)[-1].lower())
    if word in {"bash", "sh"} and len(rest) > 1 and rest[1].replace("\\", "/").endswith("py.sh"):
        rest, word = rest[1:], "python"
    if _PYTHON_CMD_RE.match(word):
        word = "python"
    return [word] + rest[1:]


def command_words(command: str) -> set[str]:
    """The command word of each segment of a shell line.

    `a && b | c` -> {a, b, c}. Leading env assignments and sudo are skipped, and
    a path is reduced to its basename, so `/usr/bin/curl` counts as `curl`.
    """
    words: set[str] = set()
    for segment in _segments(command):
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


FAILURE_EVENT = "PostToolUseFailure"


def failed_event(input_data: dict[str, Any]) -> bool:
    """Claude Code fires PostToolUseFailure, NOT PostToolUse, when a tool fails —
    a Bash call that exits non-zero included. Without it the gate never saw a
    real failure: `pytest` exiting 1 left no trace, and an earlier passing run
    still satisfied the Stop gate."""
    return input_data.get("hook_event_name") == FAILURE_EVENT


def interrupted(input_data: dict[str, Any]) -> bool:
    """A user interrupt is neither a pass nor a failure: nothing to record."""
    return failed_event(input_data) and bool(input_data.get("is_interrupt"))


def backgrounded(input_data: dict[str, Any]) -> bool:
    """The command was started in the background (explicitly, or after a timeout)."""
    tool_input = input_data.get("tool_input")
    response = input_data.get("tool_response")
    return bool((isinstance(tool_input, dict) and tool_input.get("run_in_background"))
                or (isinstance(response, dict) and response.get("backgroundTaskId")))


def exit_success(input_data: dict[str, Any], text: str) -> bool | None:
    if failed_event(input_data):
        return None if interrupted(input_data) else False
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
    # A test-runner verdict ("1 failed, 990 passed", "no tests ran") always wins.
    if VERDICT_FAILURE_RE.search(tail) or VERDICT_FAILURE_CS_RE.search(tail):
        return False
    # A generic shell error only wins when no success summary comes AFTER it.
    # PostToolUse fires for exit 0 only, so `ls build/ 2>&1; pytest -q` printing
    # "No such file or directory ... 4 passed" is a passed run whose first
    # command complained — it was recorded as a failure and blocked the turn.
    # `pytest | tail` (exit 0 on failure) still fails on its own verdict above.
    generic = [m.end() for m in FAILURE_RE.finditer(tail)]
    success = [m.end() for m in SUCCESS_RE.finditer(tail)]
    if generic and (not success or generic[-1] > success[-1]):
        return False
    if success:
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
    for head in _segment_heads(command):
        if head[0] != "python":
            continue  # not a python invocation — nothing in this segment counts
        skip = False
        for token in head[1:]:
            if skip:
                skip = False
                continue
            if token in {"-X", "-W"}:
                skip = True  # `-X utf8`, `-W error`: the next token is the option's value
                continue
            if token.startswith("-"):
                continue  # a flag like -B or -u
            # The first non-flag argument is what python runs — the module name under
            # -m, the inline source under -c, or the script path. Decide on it and
            # leave this segment, not the command: `python sync.py; python
            # run_ci_lint.py` must still count the second call.
            script = token.replace("\\", "/")
            if _TEST_ENTRY_RE.search(script) or _CHECK_ENTRY_RE.search(script):
                return True
            rest = head[head.index(token) + 1:]
            if script.rsplit("/", 1)[-1] == "manage.py" and rest[:1] == ["test"]:
                return True  # Django: `python manage.py test`
            break
    return False


def _make_verifies(args: list[str]) -> bool:
    """`make` verifies when it builds the default target or a check-ish one.
    `make clean` / `make install` do not. Options (`-C dir`, `-j 4`, `V=1`) and
    redirect debris (`2>`, `>`, `/tmp/out.txt` after one) are not targets."""
    targets, skip = [], False
    for i, a in enumerate(args):
        if skip:
            skip = False
            continue
        if a in {"-C", "-f", "-o", "-W", "-I"} or re.fullmatch(r"\d*[<>]+&?\d*", a):
            skip = True  # the next token is this option's value or the redirect target
            continue
        if a == "-j" and i + 1 < len(args) and args[i + 1].isdigit():
            skip = True
            continue
        if a.startswith("-") or "=" in a or "<" in a or ">" in a:
            continue
        targets.append(a)
    # Any target counts except ones that only clean, install, ship or serve:
    # projects name their checks freely (`make verify`, `make ci`, `make e2e`).
    return not targets or any(t.lower() not in _MAKE_NON_CHECK for t in targets)


_MAKE_NON_CHECK = {"clean", "distclean", "mrproper", "install", "uninstall", "deploy", "release",
                   "publish", "push", "run", "serve", "dev", "start", "stop", "watch", "help",
                   "deps", "setup", "bootstrap", "init", "fmt", "format", "tidy", "docker-push"}


_PKG_MANAGERS = {"npm", "pnpm", "yarn", "bun", "uv", "poetry", "pipenv", "pdm", "hatch", "cargo",
                 "go", "dotnet", "gem", "bundle", "composer", "mvn", "gradle", "deno"}
_NPM_SCRIPT_RE = re.compile(r"(?i)^(?:test|lint|build|typecheck|type-check|check|e2e|verify|ci)[\w:.-]*$")


_QUOTED_RE = re.compile(r"'[^']*'" + r'|"(?:[^"\\]|\\.)*"', re.S)
_INLINE_SHELLS = {"bash", "sh", "zsh", "dash", "ksh", "pwsh", "powershell", "cmd", "docker", "podman",
                  "nerdctl", "kubectl", "ssh", "su", "sudo", "wsl"}


def _wrapped_payloads(command: str) -> list[str]:
    """Quoted arguments that a wrapper will RUN: not a flag's value, and not under a
    command whose arguments are data (git/echo/python …). `rtk proxy "pytest"`
    yes; `git commit -m "pytest …"`, `--verify-cmd "pytest -q"` no. A `-c` /
    `-Command` value counts only under a shell-like command (`sh -euc "…"`,
    `docker run … IMG -c '…'`), never under `head -c 300` or `git -c x`."""
    out: list[str] = []
    # Heredoc bodies are data (an edit script's `out_dir = "build"`, a commit
    # message quoting `"pytest …"`); continuation lines belong to their command.
    text = _strip_heredocs(command).replace("\\\n", " ").replace("`\n", " ")
    seg_start, prev, words_of = 0, 0, {}
    for m in _QUOTED_RE.finditer(text):
        # Linear: only the text since the previous quote is searched for a new
        # separator (re-scanning the whole prefix per quote was quadratic).
        chunk = text[prev:m.start()]
        k = max(chunk.rfind(c) for c in ";&|\n")
        if k >= 0:
            seg_start = prev + k + 1
        prev = m.end()
        # The command word once per segment (from its first few hundred chars), and
        # the token right before this quote — both bounded, so a 3,000-quote line
        # stays linear.
        if seg_start not in words_of:
            first = _head(_QUOTED_RE.sub("_q_", text[seg_start:seg_start + 400]))
            words_of[seg_start] = first[0] if first else ""
        word = words_of[seg_start]
        before = text[max(seg_start, m.start() - 200):m.start()].split()
        if not word or not before:
            continue
        last = before[-1].lower()
        if word == "python":
            continue  # python's quoted arguments are data, `-c` source included
        payload = m.group(0)[1:-1]
        if m.group(0).startswith('"'):
            # What the shell hands the wrapper: inside "…", \" \\ \$ \` lose their
            # backslash. Left escaped, the next level read \" as an opening quote
            # and masked the `python -m pytest` behind it.
            payload = re.sub(r'\\([\\"$`])', r"\1", payload)
        if re.fullmatch(r"-[a-z]*c|-command|/c", last):
            if word in _INLINE_SHELLS:
                out.append(payload)
            continue
        if last.startswith("-") or word in _NON_RUNNER_WORDS or word in _SHELL_RUNNERS:
            continue
        out.append(payload)  # `rtk proxy "…"`, `ssh host "…"`, `for c in "…"`
    return out


_FOR_RE = re.compile(r"\bfor\s+(\w+)\s+in\s+([^;\n]+?)\s*;?\s*(?:\n\s*)?do\b(.*?)\bdone\b", re.S)
_RUN_SCRIPT_RE = re.compile(
    r"(?i)(?:[A-Za-z_]\w*=\S*\s+)*(?:timeout\s+\S+\s+)?(?:(?:hatch|pdm|poetry|rye|uv|pipenv)\s+run|just|task|bazel|bazelisk|"
    r"php\s+artisan|mix|dune|stack|cabal|sbt|lein|tox\s+-e)\s+(?:test|tests|lint|check|typecheck|verify)\b")
# Options of bash/pwsh that take a value: the value is not the script.
_RUNNER_VALUE_OPTS = {"-o", "+o", "-O", "-executionpolicy", "-windowstyle", "-configurationname",
                      "-workingdirectory", "-inputformat", "-outputformat", "-encodedcommand"}


def _runner_script(args: list[str]) -> str:
    skip = False
    for i, t in enumerate(args):
        if skip:
            skip = False
            continue
        low = t.lower()
        if low == "-file":
            return args[i + 1] if i + 1 < len(args) else ""
        if t in _RUNNER_VALUE_OPTS or low in _RUNNER_VALUE_OPTS:
            skip = True
            continue
        if t.startswith("-") or t.startswith("+"):
            continue
        return t
    return ""


def is_verification_command(command: str, _depth: int = 0) -> bool:
    # A quoted payload that is itself a shell line — `ssh host "cd build && make"`,
    # `bash -c "pytest -q"` — is judged as one. Every OTHER quoted string is data
    # (`--description "… node --test …"`, `--verify-cmd "pytest -q"`) and is
    # removed before the any-position scan below.
    if _depth < 3:
        if any(is_verification_command(p, _depth + 1) for p in _wrapped_payloads(command)):
            return True  # `rtk proxy "python -m pytest -q"`, `watch "make test"`
    # `python - <<EOF … assert … EOF` is an inline check that fails loudly — but
    # only if it changes nothing. An edit script's `assert old in s` is a
    # precondition, and counting it let every python-heredoc edit verify itself.
    if any(_PY_ASSERT_RE.search(body) and not _PY_WRITE_RE.search(body)
           for body in inline_python(command)):
        return True
    stripped = _strip_heredocs(command)
    # `hatch run test`, `just test`, `bazel test //…`, `php artisan test` — at the
    # START of a segment (after env/timeout), never inside an echo'd string.
    if any(_RUN_SCRIPT_RE.match(_QUOTED_RE.sub("_q_", seg).strip()) for seg in _segments(command)):
        return True
    for m in _FOR_RE.finditer(stripped):
        # `for t in tests/test_*.py; do python "$t"; done` — this repo's own way to
        # run its suites. The script is a variable; the test paths are in the list.
        var, items, body = m.group(1), m.group(2).split(), m.group(3)
        if any(_TEST_ENTRY_RE.search(i.strip("'\"").replace("\\", "/")) or
               _CHECK_ENTRY_RE.search(i.strip("'\"").replace("\\", "/")) for i in items) and re.search(
                r"(?i)(?:python[0-9.]*|py|py\.sh\"?|bash|sh|pwsh)(?:\.exe)?\s+(?:-\S+\s+)*[\"']?\$\{?" + var + r"\b",
                body):
            return True
    for segment in _segments(command):
        head = _head(segment)
        if not head:
            continue
        # The same segment with every quoted string reduced to a placeholder: the
        # any-position scan must not find a runner name inside data. Computed per
        # segment — zipping two head lists drifted out of step after `S="…"`.
        bare = _head(_QUOTED_RE.sub("_q_", segment)) or head
        word = head[0]
        if word == "make":
            if _make_verifies(head[1:]):
                return True
            continue
        if word in {"bash", "sh", "zsh"} and "-n" in head[1:]:
            return True  # `bash -n script.sh`: a syntax check, like py_compile
        if word in _SHELL_RUNNERS or word.endswith((".sh", ".ps1")):
            # `bash tools/check_x.sh`, `pwsh -File tools/verify_pages.ps1`, `./gate.sh`
            script = word if word.endswith((".sh", ".ps1")) else _runner_script(head[1:])
            if _TEST_ENTRY_RE.search(script.replace("\\", "/")) or _CHECK_ENTRY_RE.search(script.replace("\\", "/")):
                return True
        if word in _NON_RUNNER_WORDS:
            continue
        if word in _PKG_MANAGERS and any(t.lower() in _INSTALL_WORDS for t in head[1:4]):
            continue
        if "install" in (t.lower() for t in head[1:5]):
            continue
        if word == "python":
            # Python's arguments are scripts, flags and DATA. Only `python -m
            # <runner>` counts here; scripts go to runs_script_test.
            if VERIFY_HEAD_RE.match(" ".join(head)):
                return True
            continue
        if word in {"npm", "pnpm", "yarn", "bun"} and any(_NPM_SCRIPT_RE.match(t) for t in bare[1:]):
            return True  # `pnpm --filter web lint`, `yarn workspace api test`
        if VERIFY_HEAD_RE.match(" ".join(head)):
            return True
        # Any later token may start the runner (`coverage run -m pytest`); match at
        # each token offset of ONE joined string instead of re-joining per token.
        joined, pos = " ".join(bare), 0
        for tok in bare[:-1]:
            pos += len(tok) + 1
            if _VERIFY_AT_RE.match(joined, pos):
                return True
    if runs_script_test(command):
        return True
    return bool(command_words(command) & VERIFY_VERBS)


def detect_failure(input_data: dict[str, Any]) -> dict[str, Any] | None:
    # A failed tool call is disclosed only when it was a CHECK. With
    # PostToolUseFailure wired, `which python3` or `ls missing/` — probes whose
    # failure IS the answer — drew "do not report completion" and, run twice,
    # the repeated-failure notice. An explicit status in the payload itself
    # (exit_code / success) still counts for any command.
    if failed_event(input_data) and not is_verification_command(command_from_input(input_data)):
        return None
    if _STASH_RE.search(command_from_input(input_data)):
        return None  # a before/after proof's baseline is expected to fail
    text = response_text(input_data.get("tool_response", input_data))
    # exit_success already does the text pass (tool- and tail-gated). Re-running
    # FAILURE_RE over the whole body here is what let content override "no status".
    if exit_success(input_data, text) is False:
        # Command first: a failure with no output is just "Exit code 1", so two
        # unrelated commands collapsed to one class and tripped the
        # repeated-failure notice. The same command failing again still matches.
        command = command_from_input(input_data)
        summary = f"{command} :: {text}" if command and text else (text or command)
        return {"kind": "tool-result", "summary": redact(summary, 240)}
    return None


_ASSIGN_RE = re.compile(r"""(?:^|[\s;&|(])(?:export\s+|\$)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s;&|]+))""")
_VAR_RE = re.compile(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?|\$env:([A-Za-z_][A-Za-z0-9_]*)")
_CD_RE = re.compile(r"""(?:^|[;&|(\n]\s*)(?:cd|pushd|set-location|sl)\s+(?:"([^"]+)"|'([^']+)'|([^\s;&|]+))""", re.I)


KEEP = object()  # a relative cd: the scratch state does not change


class _Context:
    """What a shell line tells us about its own paths: the variables it assigns
    (`S=…/scratchpad; cat > "$S/probe.py"`), and — segment by segment, in order —
    whether the last `cd` went into scratch (`mkdir -p "$T" && cd "$T" && cat >
    probe.py`). A `cd` only affects the segments AFTER it; one inside a heredoc
    body affects nothing."""

    def __init__(self, command: str, project: str | None = None):
        text = _strip_heredocs(command)
        self.project = project
        self.env: dict[str, str] = {}
        for m in _ASSIGN_RE.finditer(text):
            self.env[m.group(1)] = next(g for g in m.groups()[1:] if g is not None)

    def expand(self, path: str) -> str:
        return _VAR_RE.sub(lambda m: self.env.get(m.group(1) or m.group(2), m.group(0)), path or "")

    def cd_target(self, segment: str):
        """For a `cd X` segment: whether X is scratch; KEEP for a relative cd
        (`cd sub`, `cd -`), which stays wherever the last cd went; None when the
        segment is not a cd."""
        m = _CD_RE.match(segment.strip())
        if not m:
            return None
        return self._cd_state(next(g for g in m.groups() if g))

    def _cd_state(self, target: str):
        target = self.expand(target)
        if target == "-" or not _ABS_RE.match(target):
            return KEEP  # `cd sub`, `cd -`: relative to wherever the last cd went
        return is_scratch(target, self.project)

    def cd_states(self, text: str) -> list[tuple[int, bool]]:
        """(offset, scratch?) after each cd in `text`, in order."""
        out, state = [], False
        for m in _CD_RE.finditer(text):
            new = self._cd_state(next(g for g in m.groups() if g))
            if new is not KEEP:
                state = new
            out.append((m.start(), state))
        return out

    def scratch(self, path: str, under_scratch_cd: bool = False) -> bool:
        path = self.expand(path)
        if is_scratch(path, self.project):
            return True
        relative = not re.match(r"(?i)^(?:[a-z]:|[\\/~$])", path)
        return under_scratch_cd and relative


_PY_WRITE_RE = re.compile(
    r"\.write_(?:text|bytes)\(|\bopen\(.*?,\s*(?:mode\s*=\s*)?['\"][wax]b?\+?['\"]|shutil\.(?:copy\w*|move)\(|"
    # Not `.replace("…")`: that is str.replace in nearly every read-only script.
    r"os\.(?:replace|rename)\(|\.rename\(|\.dump\([^)]*open\("
)
# A path literal: no `;:{}<>=` past an optional drive letter, so CSS such as
# 'margin:0;zoom:0.55' or a format string is not read as a file name.
_PY_PATH_LIT_RE = re.compile(r"""['"]((?:[A-Za-z]:)?[^'"\n;:{}<>=*?|]{1,200}?\.[A-Za-z][A-Za-z0-9]{0,7})['"]""")
# A bare `raise SystemExit` exits 0; one with a nonzero code or a message exits 1.
_PY_ASSERT_RE = re.compile(r"(?m)^\s*assert\b|SystemExit\(\s*(?:[1-9]|['\"f])|sys\.exit\(\s*(?:[1-9]|['\"f])")
_PY_C_RE = re.compile(r"""(?i)\b(?:python[0-9.]*|py)(?:\.exe)?\s+(?:-[A-Za-z]+\s+)*-c\s+(?:"((?:[^"\\]|\\.)*)"|'([^']*)')""", re.S)


def _python_programs(command: str) -> list[tuple[int, str]]:
    """(offset in the heredoc-stripped text, source) of every inline python
    program: heredoc bodies fed to python (`python - <<'PY'`) and `python -c "…"`
    arguments. The offset says which `cd` the program ran under."""
    out: list[tuple[int, str]] = []
    stripped, bodies = _heredoc_parts(command)
    search_from = 0
    for opener, body in bodies:
        idx = stripped.find(opener, search_from)
        if idx >= 0:
            search_from = idx + len(opener)
        head = opener.split("<<", 1)[0]
        if re.search(r"(?i)(?:^|[\s;&|(/\\])(?:python[0-9.]*|py|py\.sh\"?)(?:\.exe)?\b", head):
            # The heredoc's own position, not its line's start: `cd "$SP" && python -
            # <<'EOF'` runs under the cd that precedes it on the same line.
            out.append((max(idx, 0) + len(head), body))
    for m in _PY_C_RE.finditer(stripped):
        out.append((m.start(), m.group(1) if m.group(1) is not None else m.group(2)))
    return out


def inline_python(command: str) -> list[str]:
    """Source of every inline python program in a shell line."""
    return [body for _, body in _python_programs(command)]


_PY_ROOTED_RE = re.compile(
    r"""(?:path\.join|Path)\(\s*(?P<j>[A-Za-z_][\w.]*)\s*(?P<jt>(?:,\s*[rbu]?['"][^'"]*['"]\s*)*)"""
    r"""|(?P<d>[A-Za-z_][\w.]*)\)?\s*(?P<dt>(?:/\s*[rbu]?['"][^'"]*['"]\s*)+)"""
    r"""|\bf['"]\{(?P<f>[A-Za-z_][\w.]*)\}(?P<ft>[^'"]*)['"]""")
_ROOT_SCRATCH_EXPR = re.compile(
    r"(?i)tempfile|mkdtemp|gettempdir|scratchpad|['\"](?:/tmp|/var/folders)|environ(?:\.get)?[\[(]\s*['\"](?:TEMP|TMP|TMPDIR)")


def _root_is_scratch(body: str, name: str) -> bool:
    """The program builds `name` from a temp dir (`d = tempfile.mkdtemp()`)."""
    m = re.search(rf"(?m)^\s*{re.escape(name.split('.')[0])}\s*=\s*(.+)$", body)
    return bool(m and _ROOT_SCRATCH_EXPR.search(m.group(1)))


def _program_writes(body: str) -> list[str]:
    """Literal project paths one inline python program writes (see python_written_paths)."""
    lines = body.splitlines()
    write_lines = [ln for ln in lines if _PY_WRITE_RE.search(ln)]
    if not write_lines:
        return []
    used = set(re.findall(r"[A-Za-z_]\w*", " ".join(write_lines)))
    # A literal joined onto a VARIABLE root (`join(d, "x.py")`, `R / "ctx.md"`,
    # `f"{D}slice.md"`) is reported as `$ROOT/tail`. The caller expands $ROOT from
    # the shell line's own assignments (`D=tasks/r14`, `S=…/scratchpad`) and judges
    # scratch from that. A root the program itself builds from tempfile / TEMP is
    # scratch; any other unknown root counts as a project change — missing an edit
    # is the failure this gate exists to prevent, a wrong block costs one reply.
    # (`"\n".join(out)` is str.join, not a path.)
    paths = []
    for ln in write_lines:
        rooted = False
        for m in _PY_ROOTED_RE.finditer(ln):
            name = m.group("j") or m.group("d") or m.group("f")
            tail = "/".join(re.findall(r"""['"]([^'"]*)['"]""", m.group("jt") or m.group("dt") or "")) \
                or re.sub(r"\{[^}]*\}", "x", m.group("ft") or "")
            if not tail or "." not in tail.rsplit("/", 1)[-1]:
                continue  # `f'{m} {n}'`, `"\n".join(…)`: not a path at all
            rooted = True
            if not _root_is_scratch(body, name):
                paths.append(f"${name.split('.')[0]}/{tail.lstrip('/')}")
        for m in re.finditer(r"""\bf(['"])(.*?)\1""", ln):
            if not m.group(2).startswith("{") and "." in m.group(2):
                paths.append(re.sub(r"\{[^}]*\}", "x", m.group(2)))
        if not rooted and "environ" not in ln:
            plain = re.sub(r"""\bf(['"]).*?\1""", "", ln)  # f-strings handled above
            paths.extend(m.group(1) for m in _PY_PATH_LIT_RE.finditer(plain))
    for ln in lines:
        m = _PY_ASSIGN_PATH_RE.match(ln)
        if m and m.group(1) in used:
            paths.append(m.group(2))
    return paths


# node -e "fs.writeFileSync('src/a.js', …)" and PowerShell [IO.File]::WriteAllText($p, …)
_API_WRITE_RE = re.compile(
    r"""(?:writeFileSync|appendFileSync|WriteAllText|WriteAllLines|WriteAllBytes|AppendAllText)\(\s*"""
    r"""(?:['"]([^'"]+)['"]|\$([A-Za-z_]\w*))""")


def python_written_paths(command: str) -> list[str]:
    """Project files an inline python program writes. A script that edits code
    (`open("src/auth.py", "w").write(s)`) changed files no other rule saw —
    the commonest Opus edit outside Edit/Write. Only literal paths in a program
    that has a write call count; a write to a computed path is not guessed at."""
    # Paths on the write lines themselves, plus literals assigned to a name that a
    # write line uses (`p = Path("src/a.py")` ... `p.write_text(s)`). Every other
    # literal in the program is something it READS — counting those made a script
    # that read a .xlsx and wrote a scratch file look like a project change.
    return [p for body in inline_python(command) for p in _program_writes(body)]


_PY_ASSIGN_PATH_RE = re.compile(
    r"""^\s*([A-Za-z_]\w*)\s*=\s*(?:[\w.]*Path\(\s*)?[rbuf]?['"]([^'"\n]{1,200}?\.[A-Za-z0-9]{1,8})['"]""")


# A `tmp`/`temp` directory counts as scratch only in an ABSOLUTE path, and not when
# the project itself lives there (C:\temp\myapp): a relative `src/tmp/helper.py`
# is project code, and a project under D:\tmp had every edit treated as scratch.
_TMP_SEG_RE = re.compile(r"(?i)(?:^|[\\/])(?:tmp|temp)(?:[\\/]|$)")
_ABS_RE = re.compile(r"(?i)^(?:[a-z]:[\\/]|[\\/]|~|\$)")
# Generated artifacts: deleting them after a passing check changes no source.
_GENERATED_RE = re.compile(
    r"(?i)(?:^|[\\/])(?:__pycache__|\.pytest_cache|\.mypy_cache|\.ruff_cache|\.tox|\.nox|\.coverage|"
    r"coverage\.xml|htmlcov|dist|build|target|\.next|\.turbo|\.parcel-cache|[^\\/]*\.egg-info|"
    r"[^\\/]*\.pyc)[\\/]?$")


def _norm_path(path: str) -> str:
    p = (path or "").replace("\\", "/").rstrip("/").lower()
    m = re.match(r"^/([a-z])(/|$)", p)  # msys /d/work -> d:/work
    return f"{m.group(1)}:/{p[3:]}" if m else p


def is_scratch(path: str, project: str | None = None) -> bool:
    """Temp dirs, /dev/null, *.log, `2>&1`: writing there changes nothing whose
    behaviour could be verified. Claude Code tells the model to keep temporary
    files in a scratchpad under %TEMP%, so counting them blocked report-only
    deep turns."""
    path = path or ""
    if _SCRATCH_RE.search(path):
        return True
    if _ABS_RE.match(path) and _TMP_SEG_RE.search(path):
        if project:
            root = _norm_path(project)
            if root and (_norm_path(path) + "/").startswith(root + "/"):
                return False
        return True
    return False


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


def mutated_paths(command: str, project: str | None = None) -> list[str]:
    """The operands of each mutating segment — what the command actually touches.

    Same command-position walk as `runs_script_test`: a segment counts only once
    its command word is confirmed to be a mutating one, so `grep -rn "rm -rf" docs/`
    contributes nothing. Flags are skipped, and so are redirections and bare
    numbers, which `_SEGMENT_RE` leaves behind when it splits `2>&1`.

    Returns operands, not kinds — the caller classifies. An empty list means the
    command mutates something this cannot name, and the caller stays conservative.
    """
    operands: list[str] = []
    ctx, under = _Context(command, project), False
    for segment in _segments(command):
        cd = ctx.cd_target(segment)
        if cd is KEEP:
            continue
        if cd is not None:
            under = cd
            continue
        word, args, path_arg, dest_arg, skip, skip_redirect = "", [], None, None, False, False
        for raw in segment.split():
            token = raw.strip("'\"()")
            if not token:
                continue
            if not word:
                if _ENV_ASSIGN_RE.match(token) or token in _CMD_PREFIXES:
                    continue
                word = token.replace("\\", "/").rsplit("/", 1)[-1].lower()
                if word not in MUTATING_CMDS:
                    break  # not a mutating invocation — nothing in this segment counts
                continue
            if skip:
                skip = False
                if path_arg == "":
                    path_arg = token
                elif dest_arg == "":
                    dest_arg = token
                continue
            if word in _PS_CMDLETS and token.startswith("-"):
                name = token.lower()
                if name in _PS_VALUE_PARAMS:
                    # The next token is this parameter's value. Only -Path-like and
                    # -Destination values are files; -Value "text" is not.
                    skip = True
                    if name in {"-path", "-literalpath", "-filepath"}:
                        path_arg = ""
                    elif name == "-destination":
                        dest_arg = ""
                continue  # a switch like -Force or -Recurse takes no value
            if token.startswith("-"):
                continue  # a flag like -r, -f or --preserve=all
            if re.fullmatch(r"\d*[<>]+&?\d*|&>>?", token):
                skip_redirect = True  # `2> /dev/null`: the next token is the target
                continue
            if skip_redirect:
                skip_redirect = False
                continue
            if "<" in token or ">" in token or token.isdigit():
                continue  # redirection debris, not an operand
            args.append(token)
        if not word or word not in MUTATING_CMDS:
            continue
        if word in {"copy-item", "move-item"}:
            # Positional form is `Copy-Item <source> <destination>`; named form
            # fills either slot. A copy writes its destination; a move also
            # removes its source.
            positional = list(args)
            src = path_arg if path_arg else (positional.pop(0) if positional else None)
            dst = dest_arg if dest_arg else (positional.pop(0) if positional else None)
            args = [p for p in ([dst] if word == "copy-item" else [src, dst]) if p]
        elif word in _PS_CMDLETS:
            args = [path_arg] if path_arg else args[:1]
        elif word == "cp" and len(args) > 1:
            args = args[-1:]  # a copy writes its destination; the sources are read
        # A cd-relative scratch operand is kept as a scratch marker, not dropped: an
        # empty list means "changed something unnamed" and would read as `other`.
        operands.extend("/dev/null" if (under and ctx.scratch(a, True)) or
                        (word in {"rm", "remove-item"} and _GENERATED_RE.search(a)) else a for a in args)
    return operands


_PS_CMDLETS = {"copy-item", "move-item", "remove-item", "new-item", "set-content", "add-content", "out-file"}
# Parameters that take a value. Anything else (-Force, -Recurse, -NoNewline) is a
# switch, and treating it as value-taking swallowed the path that followed.
_PS_VALUE_PARAMS = {"-path", "-literalpath", "-filepath", "-destination", "-value", "-encoding",
                    "-itemtype", "-name", "-filter", "-include", "-exclude", "-inputobject",
                    "-stream", "-credential", "-delimiter", "-width", "-target"}


# Writes that are not a named command: redirects, `tee`, in-place editors,
# patch tools. Claude Code's own bypass-permissions guidance tells the model to
# edit "with sed, heredocs, or short scripts", and none of these set
# changed_files_seen — a deep turn could change code that way and stop unchecked.
_SCRATCH_RE = re.compile(
    r"(?i)^(?:/dev/|nul$|con$|&|\d+$|\$null$)|\.log$"
    r"|^\$\{?(?:tmp|temp)|^\$env:(?:tmp|temp)\b"
    # macOS os.tmpdir() is /var/folders/.../T (realpath /private/var/...), which has
    # no tmp/temp segment; Claude Code's scratchpad lives under it.
    r"|^(?:/private)?/var/folders/|[\\/]claude[\\/].*[\\/]scratchpad(?:[\\/]|$)"
)
_REDIRECTS = {">", ">>", ">|", "&>", "&>>"}
_SEGMENT_OPS = {";", "&&", "||", "|", "&", "|&", "(", ")"}


def _mask_quoted_newlines(text: str) -> str:
    """A newline inside quotes is part of the argument (`git commit -m "…\n- a -> b"`,
    a multi-line `python -c`). Splitting there handed shlex half a quote and read
    the `->` inside as a redirect. Same length out as in: offsets stay valid."""
    out, quote, esc = [], "", False
    for ch in text:
        if esc:
            esc = False
        elif quote:
            if quote == '"' and ch == "\\":
                esc = True
            elif ch == quote:
                quote = ""
            elif ch == "\n":
                ch = " "
        elif ch in "'\"":
            quote = ch
        out.append(ch)
    return "".join(out)


def shell_written_paths(command: str, project: str | None = None) -> list[str]:
    """Files a shell line writes without a MUTATING_CMDS verb.

    Returns paths, or [""] when something is written but cannot be named (`git
    apply`, `patch` without -i). Scratch targets — /dev/null, temp dirs, *.log,
    `2>&1`, a variable or `cd` that points into scratch — are not changes.
    Heredoc BODIES are dropped first: their text is data, and a `>` inside one is
    not a redirect.
    """
    out: list[tuple[str, bool]] = []
    ctx, under = _Context(command, project), False
    text = _strip_heredocs(command)
    for line in _mask_quoted_newlines(text).splitlines():
        try:
            # posix=False keeps quotes on tokens, so a quoted ">" (`rg ">" src/`)
            # stays an argument instead of reading as a redirect. Quotes are
            # stripped below, only from what is reported as a path.
            lex = shlex.shlex(line, posix=False, punctuation_chars=True)
            lex.whitespace_split = True
            tokens = list(lex)
        except ValueError:  # unbalanced quotes: nothing reliable to read
            continue
        segment: list[str] = []
        skip_input = False
        for i, tok in enumerate(tokens + [";"]):
            if tok in _REDIRECTS and i + 1 < len(tokens):
                out.append((_unquote(tokens[i + 1]), under))
                continue
            if tok in {"<", "<<<"}:
                skip_input = True  # `tee out.py < in.txt`: in.txt is read
                continue
            if skip_input:
                skip_input = False
                continue
            if tok not in _SEGMENT_OPS:
                segment.append(tok)
                continue
            cd = ctx.cd_target(" ".join(segment))
            if cd is KEEP:
                pass
            elif cd is not None:
                under = cd
            else:
                out.extend((w, under) for w in _segment_writes(segment))
            segment = []
    # Inline programs and API writes take the cd state at THEIR position — a later
    # `cd <project>` must not turn an earlier scratch probe into a project change.
    states = ctx.cd_states(_mask_quoted_newlines(text))

    def state_at(pos: int) -> bool:
        current = False
        for at, state in states:
            if at >= pos:
                break
            current = state
        return current

    for pos, body in _python_programs(command):
        out.extend((w, state_at(pos)) for w in _program_writes(body))
    for m in _API_WRITE_RE.finditer(text):
        target = m.group(1) if m.group(1) is not None else ctx.env.get(m.group(2), "")
        if target:
            out.append((target, state_at(m.start())))
    return [path for path, cd_scratch in out if not path or not ctx.scratch(path, cd_scratch)]


def _unquote(token: str) -> str:
    return token[1:-1] if len(token) > 1 and token[0] == token[-1] and token[0] in "'\"" else token


def _segment_writes(tokens: list[str]) -> list[str]:
    tokens = [_unquote(t) for t in tokens]
    words = [t for t in tokens if not _ENV_ASSIGN_RE.match(t) and t.lower() not in _CMD_PREFIXES]
    if not words:
        return []
    cmd = words[0].replace("\\", "/").rsplit("/", 1)[-1].lower()
    args = words[1:]
    if cmd == "tee":
        return [a for a in args if not a.startswith("-")]
    if cmd in {"sed", "perl"}:
        if not any(a == "--in-place" or (a.startswith("-") and not a.startswith("--") and "i" in a)
                   for a in args):
            return []
        files, skip_next, script_seen = [], False, False
        for a in args:
            if skip_next:
                skip_next, script_seen = False, True
                continue
            if a in {"-e", "-f", "-E", "--expression"}:
                skip_next = True
                continue
            if a.startswith("-"):
                continue
            if cmd == "sed" and not script_seen:
                script_seen = True  # the first bare argument is the sed script
                continue
            files.append(a)
        return files or [""]
    if cmd == "patch":
        return [""]
    if cmd == "git" and args:
        sub = args[0].lower()
        # pull/merge/rebase/reset/stash sync or set aside work; they are not a change
        # the model made. Counting them blocked the usual post-merge cleanup and the
        # investigation pack's own before/after (`git stash; pytest; git stash pop`).
        if sub in {"apply", "am", "revert", "cherry-pick"}:
            return [""]
        if sub in {"checkout", "restore"} and "--" in args:
            return args[args.index("--") + 1:] or [""]
    return []


def changed_kinds(input_data: dict[str, Any]) -> list[str]:
    # A failed Edit/Write changed nothing. A failed shell line may have changed
    # part of what it touched, so it still goes through the classification below.
    if failed_event(input_data) and str(input_data.get("tool_name") or "") not in SHELL_TOOLS:
        return []
    project = str(input_data.get("cwd") or "") or None
    paths = changed_paths(input_data)
    if paths:
        real = [p for p in paths if not is_scratch(p.strip(), project)]
        return sorted({classify_path_kind(p.strip()) for p in real})
    tool_name = str(input_data.get("tool_name") or "")
    if tool_name not in SHELL_TOOLS:
        return []
    command = _SSH_PAYLOAD_RE.sub("ssh", command_from_input(input_data))
    ctx = _Context(command, project)
    written = shell_written_paths(command, project)
    if MUTATING_TOOL_RE.search(command) or (command_words(command) & MUTATING_CMDS):
        # Classify what the command names before falling back to "other". Reporting
        # "other" for `cp notes.md docs/notes.md` is a claim about a file the gate
        # never looked at: it says a non-docs thing changed when only Markdown moved,
        # and docs_only() then keeps the deep gate armed for the rest of the turn.
        operands = mutated_paths(command, project) + [p for p in written if p]
        real = [p for p in operands if not ctx.scratch(p)]  # cd-relative scratch already dropped
        if real:
            return sorted({classify_path_kind(ctx.expand(path)) for path in real})
        # Every named operand was scratch (`rm -rf /tmp/x`): nothing changed.
        # Nothing named at all: something changed that cannot be classified.
        return [] if operands else ["other"]
    if written:
        named = [ctx.expand(p) for p in written if p]
        return sorted({classify_path_kind(p) for p in named} | ({"other"} if "" in written else set()))
    return []


_STASH_RE = re.compile(r"\bgit\s+stash(?!\s+(?:list|show|pop|apply|drop|clear)\b)")


def verification_record(input_data: dict[str, Any]) -> dict[str, Any] | None:
    command = command_from_input(input_data)
    if not command or not is_verification_command(command) or interrupted(input_data):
        return None
    if backgrounded(input_data):
        # Launched, not observed: the result lands in a task file this hook never
        # sees. Counting the launch (success=None) let `pytest` with
        # run_in_background satisfy the gate before it had run.
        return None
    stash = _STASH_RE.search(command)
    if stash:
        # A run with the work stashed is the BEFORE half of a before/after proof:
        # the old code failing is the point, so it never counts as a failure.
        # When the same call ran a check BEFORE stashing (the usual "full suite,
        # then the proof" line), that check ran on the current code; the shared
        # output cannot say which run printed what, so record it as ran-unknown.
        pop = list(re.finditer(r"\bgit\s+stash\s+(?:pop|apply)\b", command))
        after = command[pop[-1].end():] if pop else ""
        if not (is_verification_command(command[:stash.start()]) or is_verification_command(after)):
            return None
        return {"command": redact(command, 220), "success": None,
                "summary": "ran before a git stash before/after proof; outcome not separable"}
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
    e.g. two 'ECONNREFUSED localhost:5432' land on the same class.

    For "<command> :: <output>" summaries the key is the command plus the END of
    the output. The first 80 characters were the command and a fixed runner
    header (`> pkg@1 test > jest`), so every failure of one command was "the same
    failure repeated" — the TDD loop of fix-and-rerun drew the notice each time.
    """
    def norm(s: str) -> str:
        s = (s or "").lower()
        s = re.sub(r"[/\\][^\s]+", " path ", s)   # paths vary
        s = re.sub(r"\d+", "#", s)                # numbers vary
        return re.sub(r"\s+", " ", s).strip()

    if " :: " in (summary or ""):
        command, output = summary.split(" :: ", 1)
        return (norm(command)[:60] + " :: " + norm(output)[-60:]).strip()
    return norm(summary)[:80]


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
