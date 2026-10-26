#!/usr/bin/env python3
"""Small JSON ledger for the fablize observation gate.

Ported from fable-ish (gate-comparison experiment, 2026-06-14). Only the
observed-evidence subsystem is carried over; danger-blocking stays with the
host harness. Ledger state lives under ~/.fablize/ledgers/ (override with
FABLIZE_DATA) so it survives reboots, unlike the original /tmp default.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


DEFAULT_LEDGER: dict[str, Any] = {
    "task_mode": "quick",
    "risk_flags": [],
    "changed_files_seen": False,
    "change_kinds": [],
    "verification_commands": [],
    "verification_results": [],
    "failures": [],
    "stop_blocks": 0,
    # A non-docs change has happened since the latest non-failing verification.
    "unverified_change": False,
    "verify_forbidden": False,
    # The staleness notice last shown in this session+cwd. Kept across prompts
    # (gate_prompt resets only the per-turn keys) so the notice is said once.
    "notice_shown": "",
    "last_updated": "",
}

SECRET_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*['\"]?[^'\"\s]+"),
    re.compile(r"sk-[A-Za-z0-9_-]{12,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9_]{12,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{12,}"),
]

CODE_EXTS = {
    ".c", ".cc", ".cpp", ".cs", ".css", ".go", ".java", ".js", ".jsx", ".kt",
    ".mjs", ".php", ".py", ".rb", ".rs", ".scss", ".sh", ".sql", ".swift",
    ".ts", ".tsx",
}
DOC_EXTS = {".md", ".mdx", ".rst", ".txt", ".adoc"}
CONFIG_EXTS = {".json", ".jsonc", ".toml", ".yaml", ".yml", ".ini", ".cfg", ".conf", ".lock"}
ASSET_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico", ".pdf", ".mp3", ".mp4"}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def redact(text: Any, limit: int = 500) -> str:
    value = "" if text is None else str(text)
    value = value.replace("\r", " ").replace("\n", " ").strip()
    for pattern in SECRET_PATTERNS:
        value = pattern.sub("[REDACTED]", value)
    if len(value) > limit:
        return value[: limit - 3] + "..."
    return value


def data_root() -> Path:
    env_data = os.environ.get("FABLIZE_DATA")
    base = Path(env_data).expanduser() if env_data else Path.home() / ".fablize"
    return base.resolve()


def ledger_key(input_data: dict[str, Any]) -> str:
    # Session alone when there is one. `cwd` follows the Bash tool's working
    # directory, so keying on it split one turn across ledgers: a `cd sub` after
    # the deep prompt sent the Edit and the Stop to a fresh quick-mode ledger and
    # the gate let an unverified change through. cwd only disambiguates the
    # no-session case.
    session_id = input_data.get("session_id")
    raw = session_id if session_id else f"no-session|{input_data.get('cwd') or os.getcwd()}"
    return hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()[:24]


def ledger_path(input_data: dict[str, Any]) -> Path:
    return data_root() / "ledgers" / f"{ledger_key(input_data)}.json"


def default_ledger() -> dict[str, Any]:
    return copy.deepcopy(DEFAULT_LEDGER)


def load_ledger(input_data: dict[str, Any]) -> dict[str, Any]:
    path = ledger_path(input_data)
    if not path.exists():
        return default_ledger()
    try:
        data = json.loads(_retry(lambda: path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        data = default_ledger()
        # Fail toward keeping the gate armed. default_ledger()'s task_mode is
        # "quick", which asks for no verification at all — so a single unreadable
        # read (torn concurrent write, AV/OneDrive lock) would silently disarm the
        # gate for the rest of a deep turn. The classified mode is unrecoverable
        # here, so take the proportionate middle rather than the permissive end.
        data["task_mode"] = "normal"
        data["failures"].append(
            {"kind": "ledger",
             "summary": "Ledger could not be read; continuing fresh at task_mode=normal."}
        )
        return data

    ledger = default_ledger()
    if isinstance(data, dict):
        ledger.update({key: data.get(key, value) for key, value in ledger.items()})
    for key in ("risk_flags", "change_kinds", "verification_commands", "verification_results", "failures"):
        if not isinstance(ledger.get(key), list):
            ledger[key] = []
    return ledger


def save_ledger(input_data: dict[str, Any], ledger: dict[str, Any]) -> Path:
    path = ledger_path(input_data)
    path.parent.mkdir(parents=True, exist_ok=True)
    ledger["last_updated"] = utc_now()
    # Per-process temp name: a shared `<key>.tmp` let two concurrent hooks
    # (parallel tool calls) write and rename the same file, surfacing
    # "Permission denied" / WinError 32 to the user.
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(ledger, indent=2, sort_keys=True), encoding="utf-8")
    try:
        # Windows refuses to replace a file another process has open; retry briefly.
        _retry(lambda: tmp.replace(path))
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    return path


def _retry(fn: Callable[[], Any], attempts: int = 50, delay: float = 0.02) -> Any:
    for _ in range(attempts - 1):
        try:
            return fn()
        except PermissionError:
            time.sleep(delay)
    return fn()


@contextmanager
def _locked(path: Path):
    """Serialise load -> modify -> save across hook processes.

    Without it concurrent PostToolUse hooks each loaded the same ledger and the
    last writer won: 12 parallel calls kept 5-10 of 12 records, so a lost Edit
    disarmed the gate and a lost failure hid it. An O_EXCL lock file works on
    every OS with the stdlib. Never hangs a hook: a lock older than 10s is
    treated as stale, and after 5s the update proceeds unlocked (fail open).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_name(path.name + ".lock")
    deadline = time.monotonic() + 5
    fd = None
    while fd is None:
        try:
            fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except (FileExistsError, PermissionError):  # Windows: lock mid-delete
            try:
                if time.time() - lock.stat().st_mtime > 10:
                    lock.unlink()
                    continue
            except OSError:
                pass
            if time.monotonic() > deadline:
                break
            time.sleep(0.01)
    try:
        yield
    finally:
        if fd is not None:
            os.close(fd)
            try:
                lock.unlink()
            except OSError:
                pass


def update_ledger(input_data: dict[str, Any], updater: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    with _locked(ledger_path(input_data)):
        ledger = load_ledger(input_data)
        updater(ledger)
        trim_ledger(ledger)
        save_ledger(input_data, ledger)
    return ledger


def trim_ledger(ledger: dict[str, Any]) -> None:
    for key in ("risk_flags", "change_kinds"):
        values: list[Any] = []
        for value in ledger.get(key, []):
            if value not in values:
                values.append(value)
        ledger[key] = values[:20]
    for key in ("verification_commands", "verification_results", "failures"):
        ledger[key] = ledger.get(key, [])[-40:]


def add_unique(ledger: dict[str, Any], key: str, values: list[str]) -> None:
    existing = list(ledger.get(key, []))
    for value in values:
        if value and value not in existing:
            existing.append(value)
    ledger[key] = existing


def classify_path_kind(path_value: str) -> str:
    path = Path(path_value)
    name = path.name.lower()
    suffix = path.suffix.lower()
    parts = {part.lower() for part in path.parts}
    # Extension first. A `docs/` segment is only a hint about the file's ROLE and
    # must not outrank what the file actually IS: docs/conf.py and
    # packages/docs/src/index.ts are code, and classifying them "docs" lets
    # verify_state.docs_only() short-circuit the deep gate for real code changes.
    if suffix in CODE_EXTS:
        return "code"
    # Build/dependency manifests that happen to end in .txt are config, not docs:
    # classifying CMakeLists.txt as docs let docs_only() disarm the deep gate.
    if name == "cmakelists.txt" or (suffix == ".txt" and re.match(r"(?:requirements|constraints)", name)):
        return "config"
    if suffix in DOC_EXTS or name in {"readme", "readme.md", "agents.md"}:
        return "docs"
    if suffix in CONFIG_EXTS or name.startswith(".env"):
        return "config"
    if suffix in ASSET_EXTS:
        return "assets"
    if "docs" in parts:
        return "docs"
    return "other"


def read_stdin_json() -> dict[str, Any]:
    import sys

    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {"_parse_error": "invalid stdin json"}
    return data if isinstance(data, dict) else {"_input": data}


def emit_json(payload: dict[str, Any]) -> None:
    import sys

    sys.stdout.write(json.dumps(payload, ensure_ascii=True) + "\n")
