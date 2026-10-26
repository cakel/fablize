#!/usr/bin/env python3
"""What counts as a verification, what counts as a change, and that concurrent
hooks do not lose either. Each case is a way the deep Stop gate was silently
disarmed (or wrongly armed) before 2.1.1+cakel.7."""

import json
import os
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts" / "gate"))
from parse_tool_result import changed_kinds, is_verification_command, verification_record  # noqa: E402

CHECKS = []


def check(label, got, want):
    CHECKS.append((label, got, want))


# --- a runner counts only in command position ---
VERIFY = ["pytest -q", "py -m pytest", "uv run pytest", "npx jest", "npm run test", "bun run test",
          "yarn test:unit", "dotnet test", "./gradlew test", "mvn -q test", "node --test", "deno test",
          "swift test", "phpunit", "bundle exec rake test", "Invoke-Pester", "py tests/test_gate.py",
          ".venv/Scripts/python.exe tests/test_x.py", "bash hooks/py.sh tests/test_gate.py",
          "cd x && PYTHONUTF8=1 pytest", "timeout 60 cargo test", "python sync.py; python run_ci_lint.py"]
NOT_VERIFY = ["cat pytest.ini", "cat jest.config.js", "git add eslint.config.mjs",
              'git commit -m "fix lint warnings"', "rg -n pytest README.md", "pip install pytest",
              "npm install --save-dev jest", "python manage.py test_import.py", "echo pytest"]
check("runners in command position are verifications", [c for c in VERIFY if not is_verification_command(c)], [])
check("tool names in arguments are not", [c for c in NOT_VERIFY if is_verification_command(c)], [])

# --- shell writes are changes; scratch writes and quoted '>' are not ---
WRITES = {'sed -i "s/old/new/" src/auth.py': ["code"], "perl -pi -e 's/a/b/' lib/x.py": ["code"],
          "echo hi > src/a.py": ["code"], "cat >> notes.md <<'EOF'\nline > arrow\nEOF": ["docs"],
          "printf x | tee config.yaml": ["config"], "git apply fix.patch": ["other"],
          "git checkout -- src/a.py": ["code"], 'echo x > "src/my file.py"': ["code"]}
NOT_WRITES = ["pytest > /tmp/out.log 2>&1", "python3 t.py >/dev/null 2>&1", 'rg -n ">" src/',
              'echo "a > b"', "sed -n '1,5p' src/a.py", "git stash list", "cmd > $TMP/x.txt",
              "ssh u@h 'echo x > /etc/y'"]
bash = lambda c: changed_kinds({"tool_name": "Bash", "tool_input": {"command": c}})
check("shell writes classified", {c: bash(c) for c in WRITES if bash(c) != WRITES[c]}, {})
check("reads and scratch writes are not changes", {c: bash(c) for c in NOT_WRITES if bash(c)}, {})

# --- a background launch is not an observed verification ---
check("run_in_background pytest not recorded",
      verification_record({"tool_name": "Bash", "tool_input": {"command": "pytest -q", "run_in_background": True},
                           "tool_response": {"stdout": "", "backgroundTaskId": "b1"}}), None)
check("auto-backgrounded (timeout) pytest not recorded",
      verification_record({"tool_name": "Bash", "tool_input": {"command": "pytest -q"},
                           "tool_response": {"stdout": "", "backgroundTaskId": "b2"}}), None)

# --- end to end: a sed edit arms the deep gate ---
HOOKS = REPO / "hooks"


def hook(script, payload, data):
    p = subprocess.run([sys.executable, str(HOOKS / script)], input=json.dumps(payload),
                       capture_output=True, text=True, env=dict(os.environ, FABLIZE_DATA=data), timeout=60)
    return p.stdout.strip()


with tempfile.TemporaryDirectory() as data:
    hook("gate_prompt.py", {"prompt": "refactor the auth module thoroughly", "session_id": "S", "cwd": "/w"}, data)
    hook("gate_post_tool.py", {"tool_name": "Bash", "tool_input": {"command": 'sed -i "s/a/b/" src/auth.py'},
                               "tool_response": {"stdout": ""}, "session_id": "S", "cwd": "/w"}, data)
    stop = json.loads(hook("gate_stop.py", {"session_id": "S", "cwd": "/w"}, data) or "{}")
    check("deep + sed -i edit + no test -> blocks", stop.get("decision"), "block")

# --- concurrent hooks: no lost writes, no user-visible error ---
# Parallel tool calls run their PostToolUse hooks at the same time. A shared
# `<key>.tmp` and an unlocked load->save lost records and printed
# "Permission denied" / WinError 32 as hook output.
with tempfile.TemporaryDirectory() as data:
    def one(i):
        return hook("gate_post_tool.py", {"tool_name": "Bash", "tool_input": {"command": f"pytest t{i}"},
                                          "tool_response": {"stdout": "1 passed"}, "session_id": "C", "cwd": "/w"}, data)
    with ThreadPoolExecutor(12) as ex:
        outs = list(ex.map(one, range(12)))
    files = list(Path(data, "ledgers").glob("*.json"))
    recorded = len(json.loads(files[0].read_text(encoding="utf-8"))["verification_results"]) if files else 0
    check("12 concurrent hooks: all recorded", recorded, 12)
    check("12 concurrent hooks: no failed-open message", [o for o in outs if "failed open" in o], [])
    check("no temp or lock files left behind", sorted(p.suffix for p in Path(data, "ledgers").iterdir()), [".json"])


# --- round 3: quoted text and heredoc bodies are not commands ---
check("commit message mentioning pytest is not a verification",
      is_verification_command('git add -A && git commit -m "wip; pytest still failing"'), False)
check("heredoc'd Makefile line is not a verification",
      is_verification_command("cat > Makefile <<'EOF'\ntest:\n\tpytest -q\nEOF"), False)
check("quoted ';' does not hide a real rm", bash('git commit -m "a; b" && rm src/old.py'), ["code"])
check("make clean is not a verification", is_verification_command("make clean"), False)
check("make test / bare make are", (is_verification_command("make -j4 test"), is_verification_command("make")), (True, True))

# --- scratch targets are not changes, from any tool ---
write = lambda path: changed_kinds({"tool_name": "Write", "tool_input": {"file_path": path, "content": "x"}})
check("Write into a Windows scratchpad is no change",
      write(r"C:\Users\U\AppData\Local\Temp\claude\x\scratchpad\probe.py"), [])
check("rm -rf /tmp/x is no change", bash("rm -rf /tmp/fab-scan"), [])
check("rm $TMPDIR/out.json is no change", bash('rm -f "$TMPDIR/out.json"'), [])
check("cp into /tmp writes only the destination", bash("cp report.json /tmp/report.json"), [])
check("cp into the repo is a change", bash("cp /tmp/gen.py src/gen.py"), ["code"])
check("Set-Content -Value text is not a path",
      changed_kinds({"tool_name": "PowerShell", "tool_input": {"command":
                     'Set-Content -Path docs/deploy.md -Value "# Deploy steps"'}}), ["docs"])

# --- end to end: follow-up prompts and edits after a pass ---
EDIT = lambda f: {"tool_name": "Edit", "tool_input": {"file_path": f, "old_string": "a", "new_string": "b"}}
PASS = {"tool_name": "Bash", "tool_input": {"command": "python -m pytest"}, "tool_response": {"stdout": "12 passed"}}


def turn(data, sid, prompt, events):
    hook("gate_prompt.py", {"prompt": prompt, "session_id": sid, "cwd": "/w"}, data)
    for ev in events:
        hook("gate_post_tool.py", {**ev, "session_id": sid, "cwd": "/w"}, data)
    return json.loads(hook("gate_stop.py", {"session_id": sid, "cwd": "/w"}, data) or "{}").get("decision")


with tempfile.TemporaryDirectory() as data:
    turn(data, "F", "refactor the auth module thoroughly", [])
    check("'응 진행해' after a deep prompt keeps deep", turn(data, "F", "응 진행해", [EDIT("src/a.py")]), "block")
with tempfile.TemporaryDirectory() as data:
    turn(data, "G", "refactor the auth module thoroughly", [])
    check("'continue' keeps deep", turn(data, "G", "continue", [EDIT("src/a.py")]), "block")
with tempfile.TemporaryDirectory() as data:
    turn(data, "H", "refactor the auth module thoroughly", [])
    check("explicit 'briefly explain' still drops to quick", turn(data, "H", "briefly explain it", [EDIT("src/a.py")]), None)
with tempfile.TemporaryDirectory() as data:
    check("edit after a passing test needs a new test",
          turn(data, "I", "refactor the auth module thoroughly", [EDIT("src/a.py"), PASS, EDIT("src/a.py")]), "block")
with tempfile.TemporaryDirectory() as data:
    check("docs edit after a passing test does not",
          turn(data, "J", "refactor the auth module thoroughly", [EDIT("src/a.py"), PASS, EDIT("README.md")]), None)
with tempfile.TemporaryDirectory() as data:
    check("edit then test passes", turn(data, "K", "refactor the auth module thoroughly", [EDIT("src/a.py"), PASS]), None)


# --- unrelated output-less failures are different classes ---
from parse_tool_result import detect_failure, repeated_failure  # noqa: E402
fail = lambda c: detect_failure({"hook_event_name": "PostToolUseFailure", "tool_name": "Bash",
                                 "tool_input": {"command": c}, "error": "Exit code 1"})
check("two unrelated failing checks are not a repeat",
      repeated_failure([fail("npm run lint"), fail("pytest -q tests/unit")]), None)
check("a failing probe (not a check) is not disclosed", fail("which python3"), None)
check("the same command failing twice is", bool(repeated_failure([fail("npm test"), fail("npm test")])), True)

# --- round 4: regressions from the command-position rewrite ---
for c in ["coverage run -m pytest", "npx --yes jest", "python -X utf8 -m pytest", "uv run --with pytest pytest",
          "nice -n 10 pytest", "xvfb-run -a npm test", 'bash -c "pytest -q"', "make test 2>&1 | tail -20",
          "make check V=1", "make -C build test", "# Run it (don't use -x)\npytest -q"]:
    check(f"wrapped runner counts: {c[:40]}", is_verification_command(c), True)
for c in ["make clean", "make install", "uv add pytest", "npm install --save-dev jest"]:
    check(f"not a run: {c}", is_verification_command(c), False)
check("cp then '2> /dev/null' still a change", bash("cp build/gen/parser.py src/parser.py 2> /dev/null || true"), ["code"])
check("comment apostrophe does not hide a cp",
      bash("# Restore it (don't edit by hand)\ncp build/gen/parser.py src/parser.py"), ["code"])
ps = lambda c: changed_kinds({"tool_name": "PowerShell", "tool_input": {"command": c}})
check("Copy-Item counts its -Destination",
      ps(r"Copy-Item -Path C:\Users\U\AppData\Local\Temp\claude\s\parser.py -Destination src\parser.py"), ["code"])
check("Copy-Item backup into $env:TEMP is no change", ps(r"Copy-Item src\settings.json $env:TEMP\x.json"), [])
check("Remove-Item -Force <scratch> is no change",
      ps(r"Remove-Item -Force C:\Users\U\AppData\Local\Temp\claude\s\x.py"), [])

with tempfile.TemporaryDirectory() as data:
    turn(data, "N", "refactor the auth module thoroughly", [])
    hook("gate_prompt.py", {"prompt": "Add a docstring to parse_token() in utils.py", "session_id": "N", "cwd": "/w"}, data)
    led = json.loads(next(Path(data, "ledgers").glob("*.json")).read_text(encoding="utf-8"))
    check("a new request without keywords does not inherit deep", led["task_mode"] != "deep", True)


# --- round 5 ---
check("goals.py checkpoint quoting a --verify-cmd is a claim, not a run",
      is_verification_command('bash "C:/x/hooks/py.sh" "C:/x/scripts/goals.py" checkpoint --id G003 '
                              '--status complete --evidence "ok" --verify-cmd "pytest -q" --verify-evidence "12 passed"'), False)
for c in ["python -m pip install pytest", "uv pip install pytest ruff", "npx playwright install chromium", "npm ci"]:
    check(f"install is not a run: {c}", is_verification_command(c), False)
for c in ["make verify", "make ci", "make unit-test", "make e2e", "pnpm --filter web lint",
          "python -X utf8 tests/test_gate.py", "python -X utf8 -m pytest", "pytest -k list"]:
    check(f"check counts: {c}", is_verification_command(c), True)
check("tee < in.txt: the input is read, not written", bash("tee src/a.py < in.txt"), ["code"])
sys.path.insert(0, str(REPO / "hooks"))
from gate_prompt import is_continuation  # noqa: E402
for c in ["진행해 주세요", "계속해 주세요", "네, 계속해 주세요", "go on", "let's continue", "continue with the next story",
          "승인합니다.", "네, 승인합니다.", "진행합니다.", "진행합시다.", "이어서, 계속해봅시다.", "권고대로 합니다.", "A",
          "Approved.", "yes, proceed with the plan", "go ahead with option 2", "looks good, proceed"]:
    check(f"go-ahead keeps the mode: {c}", is_continuation(c), True)
for c in ["Add a docstring to parse_token() in utils.py", "X 추가해줘", "다음 함수도 리팩터링해줘"]:
    check(f"new request is classified on its own: {c}", is_continuation(c), False)


# --- round 6 + the other session's gate incident ---
SP = "C:/Users/U/AppData/Local/Temp/claude/D--x/abc/scratchpad"
check("python heredoc edit of a project file is a change",
      bash("python - <<'PY'\nfrom pathlib import Path\np = Path('src/auth.py')\np.write_text(p.read_text().replace('a', 'b'))\nPY"), ["code"])
check("python heredoc that only reads changes nothing", bash("python - <<'PY'\nprint(open('src/auth.py').read())\nPY"), [])
check("a read .xlsx is not a written file",
      bash('python -c "\nimport openpyxl\nwb = openpyxl.load_workbook(\'data/in.xlsx\')\nopen(\'D:/tmp/out.txt\',\'w\').write(\'x\')\n"'), [])
edit_with_assert = "python - <<'PY'\np='src/a.py'; s=open(p).read()\nassert 'old' in s\nopen(p,'w').write(s.replace('old','new'))\nPY"
check("an edit script's assert is not a verification", is_verification_command(edit_with_assert), False)
check("a read-only inline assert IS a verification",
      is_verification_command("python - <<'EOF'\nimport docx\nd = docx.Document('out.docx')\nassert len(d.paragraphs) > 10\nEOF"), True)
check("multi-line commit message arrow is not a redirect",
      bash('git commit -q -m "refactor: split\n\n- parse() -> tokenize()"'), [])
check("scratch via a variable is no change", bash(f'S="{SP}"; cat > "$S/probe.py" <<EOF\nprint(1)\nEOF'), [])
check("scratch via cd is no change", bash(f'cd "{SP}" && cat > probe.py <<EOF\nprint(1)\nEOF'), [])
check("cd scratch, absolute repo write still counts", bash(f'cd "{SP}" && cp out.py D:/proj/src/out.py'), ["code"])
check("macOS scratchpad is scratch",
      changed_kinds({"tool_name": "Write", "tool_input": {"file_path":
                     "/private/var/folders/ab/xyz/T/claude/-Users-u-p/s1/scratchpad/probe.py"}}), [])
check("git stash / pull are not the model's change",
      (bash("git stash -q && pytest -q; git stash pop -q"), bash("git checkout main && git pull --ff-only")), ([], []))
check("a stashed baseline run is not the latest verification",
      verification_record({"tool_name": "Bash", "tool_input": {"command": "git stash -q && pytest -q; git stash pop -q"},
                           "tool_response": {"stdout": "1 failed"}}), None)
for c, want in [('ssh host "cd build && make -j8 | tail"', True), ("ssh host 'curl -sf localhost:8080/health'", True),
                ('rtk proxy "python -m pytest tests/ -q"', True),
                ('S="C:/x y"; R="$S/a"; cd "$R" && rtk proxy python -m pytest -q', True),
                ('glab mr create --description "run node --test test/*.test.js"', False),
                ("python tools/gate.py", True), ("cmp out/a.docx expected/a.docx", True),
                ("pwsh -File tools/check_pages.ps1", True), ("bash tools/verify_build.sh", True),
                ("python hooks/gate_prompt.py", False), ("python tools/checkout.py", False)]:
    check(f"verification? {c[:45]}", is_verification_command(c), want)
from parse_tool_result import exit_success  # noqa: E402
check("cmp 'differ:' is a failed check",
      exit_success({"tool_name": "Bash", "tool_input": {"command": "cmp a b"},
                    "tool_response": {"stdout": "a b differ: byte 5, line 1"}}, ""), False)
from ledger import classify_path_kind  # noqa: E402
check("CMakeLists.txt / requirements.txt are config",
      (classify_path_kind("CMakeLists.txt"), classify_path_kind("requirements-dev.txt")), ("config", "config"))


# --- round 7 ---
edit_build = "python - <<'PY'\nfrom pathlib import Path\np = Path('src/pipeline.py')\nout_dir = \"build\"\np.write_text(p.read_text().replace('x', out_dir))\nPY"
check("an edit script's quoted 'build' is not a check (heredoc body is data)", is_verification_command(edit_build), False)
check("a commit message quoting pytest is not a check",
      is_verification_command("git commit -F - <<'EOF'\nfix\n\nVerified with \"pytest tests/test_auth.py\"\nEOF"), False)
proof = "pytest tests -q | tail -2; git stash push -q src/auth.py && pytest tests/test_auth.py -q; git stash pop -q"
check("suite run before a stash proof is recorded as ran",
      (verification_record({"tool_name": "Bash", "tool_input": {"command": proof},
                            "tool_response": {"stdout": "1196 passed\n1 failed"}}) or {}).get("success", "MISSING"), None)
for c in ['for cmd in \\\n  "python tools/check_doc.py --strict" \\\n  "python tools/check_xref.py"; do eval "$cmd"; done',
          "ssh h \"docker run --rm --entrypoint sh img:1 -c 'cd /w && python -m pytest -q'\"",
          'bash -lc "pytest -q"', 'sh -euc "make test"', "bash -n hooks/router.sh", "cmake --build build -j8",
          "bitbake core-image-minimal"]:
    check(f"verification: {c[:40]}", is_verification_command(c), True)
check("bitbake clean is not", is_verification_command("bitbake -c cleansstate foo"), False)
check("a later cd /tmp does not hide an earlier project write", bash('sed -i "s/a/b/" src/app.py; cd /tmp'), ["code"])
check("a cd inside a heredoc body does not hide its target", bash("cat > scripts/install.sh <<'EOF'\ncd /tmp\nEOF"), ["code"])
check("cd into scratch after mkdir still applies",
      bash('mkdir -p "$J/tmp/shots" && cd "$J/tmp/shots" && ssh h "cat /tmp/s.jpg" > shot.jpg'), [])
check("rm inside a scratch cd is no change (not 'other')", bash("cd /d/tmp/bsrc && rm -rf wrapper"), [])
check("str.replace in a read script is not a write",
      bash("python - <<'PY'\ns = open('src/a.py').read()\nprint(s.replace('src/old.py', 'x'))\nPY"), [])
check("bare raise SystemExit is not an assert", is_verification_command("python - <<'PY'\nraise SystemExit\nPY"), False)
import time  # noqa: E402
_t = time.time()
bash("@'\n" + "\n".join(f"std::cout << v{i} << std::endl;" for i in range(1000)) + "\n'@ | Set-Content src/x.cpp")
check("1,000 unterminated '<<' stay fast (< 3s)", time.time() - _t < 3, True)
from gate_prompt import NO_EDIT_RE, QUICK_RE  # noqa: E402
for p in ["네, 설명만 해주세요", "ok just explain, no edits", "네, 간단히 오타만 고쳐주세요"]:
    check(f"explicit quick ask does not inherit: {p}",
          (not (QUICK_RE.search(p) or NO_EDIT_RE.search(p))) and is_continuation(p), False)


# --- round 8 (workflow review: corpus / lifecycle / adversary lenses) ---
P8 = lambda c, cwd="D:/Work/proj": changed_kinds({"tool_name": "Bash", "tool_input": {"command": c}, "cwd": cwd})
SP8 = '"$TEMP/claude/D--Work-proj/0abc/scratchpad"'
check("python writes take the cd state at their own position",
      (P8(f"cd {SP8} && python - <<'EOF'\nopen(\"probe.py\",\"w\").write(\"x\")\nEOF\ncd /d/Work/proj && git status"),
       P8("cd /d/Work/proj && python - <<'PY'\nopen(\"src/app.py\",\"w\").write(\"x\")\nPY\ncd \"$TEMP\" && ls")), ([], ["code"]))
check("a relative cd inside scratch stays in scratch",
      P8("cd \"$TEMP\" && mkdir -p jt && cd jt && cat > T.java <<'EOF'\nclass T{}\nEOF"), [])
check("an escaped quote does not swallow the next segment", is_verification_command('rg -n "open\\(.*[\'\\"]w" src/ ; pytest -q'), True)
check("a check after `git stash pop` is recorded as ran",
      (verification_record({"tool_name": "Bash", "tool_input": {"command": "git stash -q && pytest -q; git stash pop -q && pytest -q"},
                            "tool_response": {"stdout": "1 failed\n5 passed"}}) or {}).get("success", "MISSING"), None)
check("`head -c` / `timeout python -c` are not shell payloads",
      (is_verification_command('head -c 300 "pytest -q"'), is_verification_command('timeout 10 python -c "print(\'pytest\')"')), (False, False))
check("open(os.path.join('src','a.py'),'w') is a write",
      P8("python - <<'PY'\nimport os\nopen(os.path.join('src', 'a.py'), 'w').write('x')\nPY"), ["code"])
check("rooted write: unknown project root counts, tempfile root does not",
      (P8("python - <<'EOF'\n(ROOT/\"tasks/MAP.md\").write_text('x')\nEOF"),
       P8("python3 - <<'EOF'\nimport tempfile, os\nd = tempfile.mkdtemp()\nopen(os.path.join(d, 'cap.jsonl'), 'w').write('x')\nEOF")),
      (["docs"], []))
check("a CSS literal on a write line is not a path",
      P8("python - <<'PY'\nopen('/tmp/o.html','w').write(\"<p style='margin:0;zoom:0.55'>\")\nPY"), [])
check("ssh → docker sh -c → pytest (escaped quotes) is a verification",
      is_verification_command('ssh -p 2 u@h "IMG=\\$(docker inspect x) && docker run --rm \\$IMG sh -c \'python -c \\"import s\\" && python -m pytest -q\'"'), True)
check("a line continuation keeps the next command word",
      is_verification_command('cd "D:/x" && \\\npython tools/check_source_refs.py --self-check'), True)
_t8 = time.time()
is_verification_command(" ".join(f'echo "a{i}"' for i in range(3000)))
check("3,000 quoted args stay fast (< 3s)", time.time() - _t8 < 3, True)
for c in ["python manage.py test", "./run_tests.sh", "python runtests.py", "just test", "bazel test //...",
          "hatch run test", "php artisan test", 'for t in tests/test_*.py; do python "$t" || exit 1; done',
          "powershell -NoProfile -ExecutionPolicy Bypass -File tools/check_pages.ps1", "bash -o pipefail tools/verify_build.sh"]:
    check(f"runner: {c[:44]}", is_verification_command(c), True)
check("a non-test loop is not a verification", is_verification_command('for f in src/*.py; do python "$f"; done'), False)
check("echo'd 'task check' text is not a runner", is_verification_command('echo "next task check later"'), False)
for out in ["--- FAIL: TestX (0.00s)\nFAIL\tgithub.com/x/y\t0.012s", "src/a.ts(3,5): error TS2322: x",
            "\u2716 3 problems (2 errors, 1 warning)", "Found 2 errors in 1 file", "error[E0308]: mismatched types"]:
    check(f"piped verdict is a failure: {out[:24]}", exit_success({"tool_name": "Bash", "tool_input": {"command": "go test ./... | tail"},
                                                                   "tool_response": {"stdout": out}}, ""), False)
fa = detect_failure({"hook_event_name": "PostToolUseFailure", "tool_name": "Bash", "tool_input": {"command": "npm test"},
                     "error": "Exit code 1\n> pkg@1 test\n> jest\n\nFAIL src/a.test.js\n  TypeError: x is undefined"})
fb = detect_failure({"hook_event_name": "PostToolUseFailure", "tool_name": "Bash", "tool_input": {"command": "npm test"},
                     "error": "Exit code 1\n> pkg@1 test\n> jest\n\nFAIL src/b.test.js\n  Expected 3 received 4"})
check("two different failures of one command are different classes", repeated_failure([fa, fb]), None)
check("deleting generated artifacts is not a change",
      (P8("rm -rf .pytest_cache __pycache__"), P8("rm -rf dist/ build/ *.egg-info"), P8("rm src/old.py")), ([], [], ["code"]))
check("node / PowerShell API writes are changes",
      (P8('node -e "require(\'fs\').writeFileSync(\'src/auth.js\', s)"'),
       changed_kinds({"tool_name": "PowerShell", "tool_input": {"command": "$p = 'src/app.cs'; [IO.File]::WriteAllText($p, $t)"}, "cwd": "D:/p"})),
      (["code"], ["code"]))
check("a project under C:/temp is not scratch",
      (changed_kinds({"tool_name": "Edit", "tool_input": {"file_path": "C:/temp/myapp/src/a.py"}, "cwd": "C:/temp/myapp"}),
       P8("cat > src/tmp/helper.py <<'EOF'\nx\nEOF")), (["code"], ["code"]))


def main():
    bad = 0
    for label, got, want in CHECKS:
        ok = got == want
        bad += 0 if ok else 1
        print(f"{'OK  ' if ok else 'FAIL'} {str(got)[:60]:<62} {label}")
    print("-" * 78)
    print(f"RESULT: {'all ' + str(len(CHECKS)) + ' checks match.' if not bad else str(bad) + ' mismatched.'}")
    return 1 if bad else 0


def test_script():
    """pytest entry. The script form (`python3 tests/test_gate_heuristics.py`) stays canonical."""
    assert main() == 0


if __name__ == "__main__":
    raise SystemExit(main())
