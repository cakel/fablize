#!/usr/bin/env python3
"""The bash hooks, driven as Claude Code drives them: `bash <hook>` with JSON on stdin.

Covers what the Python-only suites cannot: hooks/py.sh picking an interpreter,
router.sh matching Korean signals, and finish-the-work.sh accepting a Windows
transcript path. All three were silent no-ops on a Windows host — `python3`
missing, English-only patterns, and a `$HOME/*` prefix that never matches
`C:\\Users\\...` — and none of them surfaced an error.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HOOKS = Path(__file__).resolve().parent.parent / "hooks"
CHECKS = []


def check(label, got, want):
    CHECKS.append((label, got, want))


_DATA = tempfile.mkdtemp(prefix="fz_rt_")


def hook(name, payload):
    # FABLIZE_DATA isolated: router.sh logs matched signals to events.jsonl.
    env = dict(os.environ, CLAUDE_PLUGIN_ROOT=str(HOOKS.parent), FABLIZE_DATA=_DATA)
    p = subprocess.run(["bash", str(HOOKS / name)], input=json.dumps(payload),
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       env=env, timeout=60)
    return p.returncode, p.stdout


def route(prompt):
    return hook("router.sh", {"prompt": prompt})[1]


# --- py.sh: runs a script with whatever interpreter exists; stdin passes through ---
out = subprocess.run(["bash", str(HOOKS / "py.sh"), "-c", "import sys; print(sys.stdin.read().strip())"],
                     input="ping", capture_output=True, text=True).stdout.strip()
check("py.sh runs python and forwards stdin", out, "ping")

# WindowsApps holds both the Store STUB (no Python; exits non-zero) and the real
# Store Python's alias. Skipping the directory wholesale dropped the real one, so
# a Store-only host ran no hook at all. Simulate both with wrapper scripts.
real_py = sys.executable.replace("\\", "/")
with tempfile.TemporaryDirectory() as td:
    wa, fb = Path(td) / "WindowsApps", Path(td) / "bin"
    wa.mkdir(); fb.mkdir()

    def wrapper(path, body):
        path.write_text("#!/bin/bash\n" + body + "\n", encoding="utf-8", newline="\n")
        path.chmod(0o755)

    def py_sh(path_dirs):
        env = dict(os.environ, PATH=":".join(path_dirs + ["/usr/bin", "/bin"]))
        return subprocess.run(["bash", str(HOOKS / "py.sh"), "-c", "print(42)"],
                              capture_output=True, text=True, env=env)

    to_posix = lambda p: subprocess.run(["bash", "-c", f'cd "{p.as_posix()}" && pwd'],
                                        capture_output=True, text=True).stdout.strip()
    wrapper(fb / "python", f'echo FALLBACK >&2; exec "{real_py}" "$@"')
    wrapper(wa / "python3", 'echo "Python was not found" >&2; exit 9009')
    p = py_sh([to_posix(wa), to_posix(fb)])
    check("py.sh: Store stub skipped, falls back", (p.stdout.strip(), "FALLBACK" in p.stderr), ("42", True))
    wrapper(wa / "python3", f'echo ALIAS >&2; exec "{real_py}" "$@"')
    p = py_sh([to_posix(wa), to_posix(fb)])
    check("py.sh: real Store alias is used", (p.stdout.strip(), "ALIAS" in p.stderr), ("42", True))
    p = py_sh([to_posix(Path(td) / "empty")])
    check("py.sh: no interpreter -> silent exit 0", (p.returncode, p.stdout), (0, ""))

# --- py.sh forces UTF-8: with the host's encoding vars stripped, Windows Python
# reads hook stdin in the locale codepage and a Korean deep prompt classified quick.
env_nu = {k: v for k, v in os.environ.items() if k not in ("PYTHONIOENCODING", "PYTHONUTF8")}
with tempfile.TemporaryDirectory() as dd:
    env_nu["FABLIZE_DATA"] = dd
    p = subprocess.run(["bash", str(HOOKS / "py.sh"), str(HOOKS / "gate_prompt.py")],
                       input=json.dumps({"prompt": "배포 스크립트 끝까지 수정해줘 \u201cquoted\u201d",
                                         "session_id": "u", "cwd": "/w"}, ensure_ascii=False).encode("utf-8"),
                       capture_output=True, env=env_nu, timeout=60)
    out_u = p.stdout.decode("utf-8", "replace")
check("py.sh: Korean prompt via raw UTF-8 stdin classified deep", "task mode: deep" in out_u, True)
check("py.sh: no fail-open message", "failed open" in out_u, False)

# --- router.sh: same pack for the same signal in either language ---
check("router: english debug signal", "[fablize:investigation]" in route("fix this error in the parser"), True)
check("router: korean debug signal", "[fablize:investigation]" in route("이 에러 원인 찾아서 고쳐줘"), True)
check("router: korean 버그 signal", "[fablize:investigation]" in route("로그인 버그 수정"), True)
check("router: korean render signal", "[fablize:grounding]" in route("매출 차트 그려줘"), True)
check("router: english render signal", "[fablize:grounding]" in route("build an html dashboard"), True)
check("router: no signal -> silent", route("README 오타 고쳐줘"), "")
check("router: '안 돼' instruction is not a bug signal", route("그렇게 바꾸면 안 돼"), "")
check("router: exit 0 on empty stdin", hook("router.sh", {})[0], 0)
# Router and task-mode classifier share one signal list (classify_task.DEBUG_RE):
# root-cause asks with no bug noun were silent here and "quick" there.
for p_ in ["근본 원인 찾아서 고쳐줘", "Find the root cause of the timeout", "figure out why login fails",
           "빌드가 깨졌어", "why does the build hang"]:
    check(f"router: debug signal without a bug noun: {p_}", "[fablize:investigation]" in route(p_), True)
for p_ in ["Add error handling to the parser", "오류 처리 추가해줘", "add exception handling"]:
    check(f"router: error-handling feature is not a bug: {p_}", "[fablize:investigation]" in route(p_), False)
sys.path.insert(0, str(HOOKS.parent / "scripts" / "gate"))
from classify_task import classify_prompt  # noqa: E402
check("classifier: root-cause asks are not quick",
      [classify_prompt(p_)[0] for p_ in ["근본 원인 찾아서 고쳐줘", "Find the root cause of the timeout",
                                         "빌드가 깨졌어", "간단히 이 버그 고쳐줘"]], ["normal"] * 4)
check("classifier: explicit no-edit still wins", classify_prompt("설명만 해줘 이 버그")[0], "quick")
hook("router.sh", {"prompt": "figure out why login fails", "session_id": "RT"})
_ev = Path(_DATA) / "history" / "events.jsonl"
check("router: matched signal logged without the prompt text",
      (_ev.exists() and "router_signal" in _ev.read_text(encoding="utf-8"),
       _ev.exists() and "login fails" in _ev.read_text(encoding="utf-8")), (True, False))


# --- finish-the-work.sh: transcript in the OS temp dir, native path form ---
def transcript(blocks):
    fd, path = tempfile.mkstemp(prefix="fz_tr_", suffix=".jsonl")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(json.dumps({"type": "user", "message": {"role": "user", "content": "해줘"}}) + "\n")
        f.write(json.dumps({"type": "assistant", "message": {"role": "assistant", "content": blocks}},
                           ensure_ascii=False) + "\n")
    return path


promise = transcript([{"type": "text", "text": "분석 완료. I'll implement the parser now."}])
rc, out = hook("finish-the-work.sh", {"transcript_path": promise, "stop_hook_active": False})
check("finish-the-work: promise-no-act is blocked (native temp path)", '"block"' in out, True)

working = transcript([{"type": "text", "text": "Running tests."},
                      {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "pytest"}}])
rc, out = hook("finish-the-work.sh", {"transcript_path": working, "stop_hook_active": False})
check("finish-the-work: ended with a tool call -> pass", out.strip(), "")

rc, out = hook("finish-the-work.sh", {"transcript_path": promise, "stop_hook_active": True})
check("finish-the-work: loop guard honoured", out.strip(), "")

def stop_with(text):
    return hook("finish-the-work.sh", {"last_assistant_message": text, "stop_hook_active": False})[1]


check("finish-the-work: waiting on the user's go-ahead is not a dropped promise",
      [stop_with(t_).strip() for t_ in [
          "Migration is ready. I'll drop the legacy table once you give the go-ahead.",
          "I will run the deploy pending your approval.",
          "마이그레이션 준비됐습니다. 승인 후 테이블을 삭제하겠습니다.",
          "결정해 주시면 해당 방식으로 수정하겠습니다."]], [""] * 4)
hook("finish-the-work.sh", {"last_assistant_message": "Done reading. I'll implement the parser now.",
                            "stop_hook_active": False, "session_id": "FTW"})
check("finish-the-work: a block is logged out-of-band",
      '"ftw_block"' in (Path(_DATA) / "history" / "events.jsonl").read_text(encoding="utf-8"), True)
check("finish-the-work: reads last_assistant_message when given",
      ('"block"' in stop_with("분석 끝. 이제 파서를 구현하겠습니다."), stop_with("테스트 12개 통과했습니다.").strip()), (True, ""))


def _unlink(path):
    # Windows can still hold the transcript for a moment after the hook's python
    # exits (WinError 32); a leftover temp file is not a test failure.
    try:
        os.unlink(path)
    except PermissionError:
        pass


def says(text):
    path = transcript([{"type": "text", "text": text}])
    try:
        return '"block"' in hook("finish-the-work.sh", {"transcript_path": path, "stop_hook_active": False})[1]
    finally:
        _unlink(path)


check("finish-the-work: korean promise blocked", says("원인 파악했습니다. 이제 파서를 구현하겠습니다."), True)
check("finish-the-work: korean 고칠게요 blocked", says("다음으로 테스트를 고칠게요."), True)
check("finish-the-work: korean offer passes", says("원하시면 PR 도 작성하겠습니다."), False)
check("finish-the-work: 알겠습니다 is not a promise", says("모두 반영했습니다. 알겠습니다."), False)
check("finish-the-work: approval-gated promise is waiting, not dropped",
      (says("변경 사항을 정리했습니다. 승인해 주시면 커밋하겠습니다."),
       says("Once you confirm, I will run it against production.")), (False, False))
check("finish-the-work: '필요하시면 …하겠습니다' is an offer",
      says("테스트 12개 통과했습니다. 필요하시면 README도 수정하겠습니다."), False)
check("finish-the-work: korean past tense passes", says("수정했고 테스트 13개 통과했습니다."), False)

for p in (promise, working):
    _unlink(p)

# Path guard, with HOME / temp / CLAUDE_CONFIG_DIR pointed into a sandbox so the
# file really exists and only the guard can reject it. A missing file would exit
# at bash's `[ -f ]` and never reach the guard.
with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    home, tmpd, cfg, out_dir = td / "home", td / "tmp", td / "cfg" / "projects", td / "outside"
    for d in (home, tmpd, cfg, out_dir):
        d.mkdir(parents=True)
    body = json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "text", "text": "I'll implement the parser now."}]}}) + "\n"

    def guarded(where, cfg_env):
        t = where / "t.jsonl"
        t.write_text(body, encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if k != "CLAUDE_CONFIG_DIR"}
        env.update(HOME=str(home), USERPROFILE=str(home), TMPDIR=str(tmpd), TEMP=str(tmpd), TMP=str(tmpd))
        if cfg_env:
            env["CLAUDE_CONFIG_DIR"] = str(td / "cfg")
        p = subprocess.run(["bash", str(HOOKS / "finish-the-work.sh")],
                           input=json.dumps({"transcript_path": str(t), "stop_hook_active": False}),
                           capture_output=True, text=True, env=env, timeout=60)
        return p.returncode, '"block"' in p.stdout

    check("guard: transcript under HOME accepted", guarded(home, False), (0, True))
    check("guard: transcript outside HOME/temp rejected", guarded(out_dir, False), (0, False))
    check("guard: outside CLAUDE_CONFIG_DIR unset -> rejected", guarded(cfg, False), (0, False))
    check("guard: under CLAUDE_CONFIG_DIR accepted", guarded(cfg, True), (0, True))


def main():
    bad = 0
    for label, got, want in CHECKS:
        ok = got == want
        bad += 0 if ok else 1
        print(f"{'OK  ' if ok else 'FAIL'} {str(got)[:40]:<42} want={str(want)[:24]:<26} {label}")
    print("-" * 78)
    print(f"RESULT: {'all ' + str(len(CHECKS)) + ' checks match.' if not bad else str(bad) + ' mismatched.'}")
    return 1 if bad else 0


def test_script():
    """pytest entry. The script form (`python3 tests/test_router.py`) stays canonical."""
    assert main() == 0


if __name__ == "__main__":
    raise SystemExit(main())
