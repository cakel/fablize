# Changelog

All notable changes to fablize are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/); versioning is [SemVer](https://semver.org/).

## [2.1.1+cakel.8] — 2026-10-26

### Changed

- **The Stop gate yields to an explicit "don't run it".** When the prompt forbids running
  checks ("테스트는 돌리지 마", "어떤 명령도 돌리지 말고 파일만 수정해", "don't run the tests",
  "do not run any commands"), a deep turn that changed files is not blocked: the model is
  told not to run anything and to say the change is unverified, and the user gets the
  unverified warning (`stop_user_forbade` in `events.jsonl`). A bare follow-up ("응 계속")
  keeps the prohibition; one that asks for a check lifts it. In the cakel.7 smoke test the
  gate blocked twice a model that was obeying the user — on a weaker model, pressure to run
  what the user forbade. (docs/DECISIONS.md D2)
  Imperatives only ("검증하지 않았다" is a report) and not build tools ("do NOT run build
  tools" still allows read-only checks): 0 of 5,721 real prompts flagged (the first count of
  0 was wrong — "같은 명령을 다시 실행하지 마세요" was flagged; a repeat ban is now excluded).
  Not a ban either: a qualified rule ("never run anything destructive", "commands that
  modify git"), a complaint or bug report ("왜 아무 명령도 실행 안 했어?", "아무것도 실행이
  안 돼"), or scope ("이 파일만 수정해"). Under a ban the normal/deep "run a check" line is
  dropped from the context so the two do not contradict (second PR #18 review).
  A demand phrased with a negative is not a ban ("don't skip the tests", "테스트를 통과하지
  않고는 끝내지 마", "don't finish without running the tests"), and only an imperative
  ("now run the tests", "테스트 돌려줘") lifts a ban — "확인했어, 계속" keeps it (PR #18 review).
- **Docs split: short README, full guide.** README / README.ko are now idea, install and
  usage only, with an SVG of one turn (`docs/assets/turn-flow*.svg`). Components and their
  measured status, task modes, the stop check (with a decision SVG), goals.py, data written,
  setup details and limits moved to `docs/GUIDE.md` / `docs/GUIDE.ko.md`.
- **Block reason no longer offers a way out it does not honour.** "…or, if none applies,
  say so" never released the gate; the text now says the reminder repeats at most twice and
  then ends the turn with an unverified warning.

## [2.1.1+cakel.7] — 2026-10-23

Review pass for running fablize under Sonnet/Opus on a Windows host: close the paths that
surface a hook ERROR or silently do nothing, and make the one Fable-specific skill step
(effort escalation) usable on any model.

### Fixed

- **Skill-verification fixes (2026-10-26).** From the skeptic audit of whether the skill
  actually steers Sonnet/Opus:
  - **Router and task-mode classifier share one signal list** (`classify_task.DEBUG_RE` /
    `RENDER_RE`). "근본 원인 찾아서 고쳐줘", "Find the root cause…", "figure out why",
    "빌드가 깨졌어" routed nothing and classified as quick ("no forced verification") — on
    the task fablize exists for. They now route the investigation pack and classify as
    normal; "Add error handling" / "오류 처리" no longer counts as a bug report.
  - **finish-the-work.sh** passes messages waiting on the user ("…once you give the
    go-ahead", "pending your approval", "승인 후 …하겠습니다"); blocking them pushed a
    destructive step ahead of consent.
  - **goals.py runs the final `--verify-cmd` itself** and completes only on exit 0, logging
    the observed output; no-op commands are refused. `--verify-cmd false --verify-evidence
    "trust me"` used to complete the plan.
  - **Investigation pack** adds checking every sibling caller of the changed code (in
    scope: same defect) and a regression test that fails before the fix.
- **An echoed exit status is the verdict.** `pytest; echo "exit=$?"` exits 0 whatever
  pytest did; an echoed nonzero status (`exit=1`, `EXIT_CODE: 2`, a bare `1`) is now a
  failure instead of "unknown", which the Stop gate counted as not-failed. Found in the
  live smoke test.
- **README claims match the evidence.** "Only procedures whose effect was verified" and
  "What's included (verified only)" are replaced by a per-component table (how it runs,
  measured or not). The observation gate — which blocks Stop and was not mentioned at all —
  is described, the shipped recovery guard is no longer listed as "not shipped", the
  early-stop hook is marked opt-in, and a "Data it writes" section lists `~/.fablize/`
  ledgers, `events.jsonl` and `./.fablize/`. The injected CLAUDE.md block's header no longer
  says "Verified procedures only" (seen in the smoke test's installed block).
- **Decision log.** `docs/DECISIONS.md` records the open decisions (README effect claims,
  blocking vs advisory gate, re-measurement, finish-the-work registration, goals.py
  command execution) and what to inspect when one is questioned. Router signals, Stop-gate
  blocks / cap warnings and finish-the-work blocks are appended out-of-band to
  `~/.fablize/history/events.jsonl` (labels only, no prompt text; `FABLIZE_EVENTS=0` off).

- **Hooks no longer hardcode `python3`.** New `hooks/py.sh` picks `python3`, `python` or `py`
  and fails open when none exists. Entries under `WindowsApps` are probed, not skipped: that
  directory holds both the Store stub and the real Store Python's alias. `hooks.json`,
  `router.sh`, `finish-the-work.sh`, `setup.sh` and `uninstall.sh` all go through it (one copy
  of the rule), and so do the `goals.py` commands in `SKILL.md` and the CLAUDE.md block.
  `setup.sh` copies `py.sh` to `~/.fablize/lib/hooks/` for the block.
- **`router.sh` is invoked as `bash "<path>"`**, like its siblings. A plugin root with a space
  split the bare command; a host that cannot exec `.sh` by shebang dropped routing silently.
- **`router.sh` routes Korean signals** (버그/에러/오류/디버그/크래시/작동하지 않…, 차트/게임/
  렌더/웹페이지…). `classify_task.py` was already bilingual; the router was not. Bare 안 돼 and
  그래프 are excluded as too ambiguous.
- **Stop gate no longer treats `stop_hook_active` as a waiver.** The flag is set after ANY Stop
  hook blocks, so one `finish-the-work.sh` reminder disabled the evidence gate for the rest of
  the turn, skipped the holdout check, and made `MAX_STOP_BLOCKS` unreachable. The loop bound is
  now the ledger's per-prompt `stop_blocks` cap alone. After the cap the gap note goes to the
  user as `systemMessage` only; Stop has no `hookSpecificOutput`, and the model is done.
- **A pytest crash or empty run counts as a failed verification.** `INTERNALERROR` and
  `no tests ran` are in `FAILURE_RE`; before, `pytest tests` over script-style suites was
  ledgered as `success=None`, which satisfied the Stop gate.
- **`finish-the-work.sh` works on Windows.** The HOME/temp containment check moved into Python
  (`C:\Users\…` never matched bash's `$HOME/*`) and also admits `$CLAUDE_CONFIG_DIR`, where
  transcripts live when it is set. The transcript is read as UTF-8 (the cp949 default raised on
  Korean text and was swallowed).
- **`finish-the-work.sh` catches Korean promises** ("구현하겠습니다", "고칠게요") and lets Korean
  offers through ("원하시면 …", "할까요"). The comment said English + Korean; the regex was English.
- **`setup.sh` points at a stable `finish-the-work.sh`.** It used to name the version-pinned cache
  path, which dangles after the next upgrade; the copy in `~/.fablize/lib/hooks/` (beside `py.sh`,
  which it now needs) is refreshed by every setup run.
- **`setup.sh` locates itself from its own path**, not `$CLAUDE_PLUGIN_ROOT`, which a session
  opened before an upgrade can still hold for the old root.
- **`setup.sh global` honours `CLAUDE_CONFIG_DIR`**, the directory Claude Code actually reads
  `CLAUDE.md` from. `uninstall.sh` too.
- **`setup.sh` without a scope and without a terminal** now exits with a message instead of a
  silent exit 1 from `read` under `set -e`. `SKILL.md` names the scope argument.
- **Stale-setup notice is no longer an order, and fires once per session.** It said "Run this
  exact command" on every prompt, so a compliant model rewrote the user's global `CLAUDE.md`
  mid-task. It now asks to run only with the user's agreement, and the ledger records it as shown.
  Local setup records its project, and the notice `cd`s there instead of planting a second block
  in whatever project the user is in.
- **`SKILL.md` and READMEs give `uninstall.sh` its scope** (it exits without a terminal otherwise)
  and quote every `${CLAUDE_PLUGIN_ROOT}` path, so a backslash or space survives bash.

- **Python I/O is UTF-8 on Windows.** `py.sh` exports `PYTHONUTF8=1`. Without it hook stdin and
  stdout used the locale codepage: `setup.sh` died on its own `✓` under cp949 after injecting the
  block but before writing `progress.json`, `goals.py` raised after saving state, a Korean deep
  prompt classified as quick, and a smart quote under cp1252 made every gate hook fail open.
- **Failed tool calls reach the gate.** Claude Code fires `PostToolUseFailure`, not `PostToolUse`,
  when a tool fails (a non-zero Bash exit included), and nothing was registered for it. A failing
  `pytest` left no trace and an earlier pass still satisfied the Stop gate. It is now registered;
  the failed run is recorded as `success=False` and disclosed under its own event name. A failed
  Edit/Write counts as no change and an interrupt records nothing.
- **The latest verification decides.** "pytest passed, edit, pytest failed, stop" used to pass
  because any earlier non-failing result satisfied the gate.
- **The ledger is keyed by session, not session+cwd.** `cwd` follows the Bash tool, so one `cd`
  mid-turn sent the Edit and the Stop to a fresh quick-mode ledger and disarmed the gate.
- **A shell error before a passing summary is a pass.** `ls build/ 2>&1; pytest -q` printing
  `No such file or directory … 4 passed` was recorded as a failure and blocked the turn. A test
  runner's own verdict (`1 failed, 3 passed`) still wins wherever it appears.
- **Legacy fork releases 2.1.2 .. 2.1.8 rank below `2.1.1+cakel.1`.** The fork renumbered after
  2.1.8, so anyone set up on one of those was refused setup forever by the downgrade guard and
  told their session was the old one.
- **macOS `/usr/bin/python3` without Command Line Tools** (an xcrun shim) is probed and skipped.
  Not run on a Mac.

- **Concurrent hooks no longer lose ledger writes or print errors.** Parallel tool calls run their
  PostToolUse hooks at once; a shared `<key>.tmp` and an unlocked load→save kept 5–10 of 12 records
  and showed `Permission denied` / `WinError 32`. The ledger now takes an `O_EXCL` lock file (stale
  after 10s, never waits past 5s), writes a per-process temp file, and retries Windows replace.
- **A verification counts only in command position.** `pip install pytest`, `cat jest.config.js` and
  `git commit -m "fix lint"` were verifications. Runners are now matched at the head of a segment,
  after env assignments and wrappers (`npx`, `uv run`, `bundle exec`, `timeout N`), and the list
  covers `npm run test`, `dotnet test`, `./gradlew test`, `node --test`, `deno test`, `phpunit`,
  `Invoke-Pester`, `py …`, `.venv/Scripts/python.exe …` and `bash hooks/py.sh …`.
- **Shell edits are changes.** Redirects, heredocs into a file, `tee`, `sed -i`, `perl -pi`,
  `git apply`, `patch` and `git checkout -- <path>` armed nothing, so a deep turn could edit code
  that way and stop unchecked. Scratch targets (`/dev/null`, temp dirs, `*.log`, `2>&1`) and a
  quoted `>` are not.
- **A background launch is not a verification.** `run_in_background` (or a timeout that
  backgrounds the call) returned an empty stdout that was recorded as `success=None`, which passed
  the gate before the tests had run.
- **`goals.py` never says "all stories complete" while one is blocked or failed**, and
  `next --reopen <id>` retries one without `create --force`. Its output is UTF-8 even when run
  without `py.sh`.
- **`uninstall.sh` clears the recorded version**, so the next upgrade's notice no longer offers to
  re-inject the block the user removed.
- **Windows requires Git Bash** for the hooks; the READMEs now say so.

- **Quoted text and heredoc bodies are not commands.** Segments used to split inside quotes, so
  `git commit -m "wip; pytest still failing"` or a heredoc'd Makefile line `	pytest -q` became the
  latest verification and passed the gate right after a real failing run.
- **A bare follow-up keeps the task's mode.** "continue", "yes go ahead", "응 진행해" have no task
  words and fell to quick, so a deep task's next turn could edit and stop unverified. Only an
  explicit quick ask ("briefly", "설명만") drops the mode now.
- **A change after a passing check needs a new check.** `unverified_change` is set by every
  non-docs change and cleared by every non-failing verification.
- **Scratch targets are not changes from any tool.** A Write into the `%TEMP%` scratchpad,
  `rm -rf /tmp/x`, `rm "$TMPDIR/…"` and `cp report.json /tmp/` blocked report-only deep turns. A
  copy now counts only its destination, and a PowerShell cmdlet only its `-Path`, not `-Value` text.
- **`make clean` is not a verification**; bare `make` and `make test|check|lint|build|all` are.
- **Unrelated output-less failures are not "the same failure repeated."** The failure summary now
  leads with the command, so two different `Exit code 1` calls no longer trip the notice.
- **Python 3.8+** is now stated in the READMEs (`from __future__ import annotations`).

- **Round-4 regressions in the rewrites above, fixed before release.** A runner may now sit behind
  any wrapper (`coverage run -m`, `npx --yes`, `python -X utf8 -m`, `nice -n 10`, `xvfb-run -a`,
  `bash -c "…"`) unless the segment's command only reads, installs or records text. `make`
  verifies by target (`make test 2>&1 | tail`, `make -C build test`; not `make clean`). A redirect
  target after a space (`2> /dev/null`) is not a `cp` destination. `Copy-Item`/`Move-Item` count
  `-Destination` (and a move its source); PowerShell switches no longer swallow the next path;
  `$env:TEMP` is scratch. A `# comment` with an apostrophe no longer opens a "quote" over the
  rest of the command. Mode is inherited only by a real go-ahead ("continue", "진행해"), not by
  any keyword-free request. `finish-the-work.sh` treats "필요하시면 …" as an offer.

- **Round-5 fixes.** A python command no longer scans its arguments for a runner name — fablize's
  own `goals.py checkpoint --verify-cmd "pytest -q"` (a claim) was recorded as a verification.
  Installs are not runs in any spelling (`python -m pip install`, `uv pip install`, `npx playwright
  install`). `make` verifies for any target except clean/install/deploy/serve-style ones
  (`make verify`, `make ci`). npm-family script names count after workspace flags
  (`pnpm --filter web lint`). `python -X utf8 …` / `-W error` take a value. More go-ahead phrasings
  keep the mode ("진행해 주세요", "go on", "let's continue"). A failing probe (`which python3`) no
  longer draws "do not report completion"; only a failing check does. `finish-the-work.sh` lets
  approval-gated promises through ("승인해 주시면 커밋하겠습니다", "once you confirm"), which it used to
  order the model to carry out before approval. `tee out < in` no longer counts `in` as written.

- **Round-6 fixes (differential test over 62,083 real commands and 4,826 prompts).** An inline
  python program that writes a literal project path (`open("src/a.py","w")`, `p.write_text`) is a
  change; an inline program that only asserts is a verification, but an edit script's `assert` is
  not. Quoted multi-line arguments no longer read as redirects. Scratch is recognised through
  variables (`S=…/scratchpad; cat > "$S/x"`), `cd` into scratch, and the macOS
  `/var/folders/…/scratchpad`. `git stash`/`pull`/`merge`/`rebase`/`reset` are not changes, and a
  run with the work stashed is not the latest verification (before/after proofs pass). Quoted
  payloads a wrapper runs count (`ssh host "make"`, `rtk proxy "pytest"`); quoted data does not
  (`--description "… node --test …"`). Tokens are quote-aware. Approvals and picks keep the mode
  ("승인합니다", "Approved.", "go ahead with option 2", "A"): 2 → 156 of the corpus's follow-ups.
  `CMakeLists.txt`/`requirements*.txt` are config.
- **From another session's gate incident:** `gate.py`, `check.py`, `*_gate.py` and `.sh`/`.ps1`
  checkers run through bash/pwsh count; `cmp` counts and its `differ:` is a failure. SKILL.md now
  says how to make a custom check visible (§3-2).

- **Round-7 fixes.** Heredoc handling is line-based and linear (the regex took 40s on 1,000
  unterminated `<<`, past the 30s hook timeout), and heredoc bodies are never scanned for runnable
  payloads: an edit script's `out_dir = "build"` or a commit message quoting `"pytest …"` made the
  edit verify itself. A check run before a `git stash` proof in the same call is recorded as
  ran-unknown instead of dropped, and a stash baseline is never a disclosed failure. Continuation
  lines and inline-shell flags (`sh -euc`, `bash -lc`, `pwsh -Command`, `IMG -c '…'`) feed the
  wrapper recursion. A `cd` affects only the segments after it. Explicit quick asks ("설명만",
  "no edits") never inherit. `bash -n`, `cmake --build`, `ninja`, `meson test`, `bitbake` count.
  `str.replace(…)` is not a write; a bare `raise SystemExit` is not an assertion.
  `finish-the-work.sh` reads `last_assistant_message` from the Stop input before the transcript.

- **Round-8 fixes (3-lens workflow review, every finding independently re-verified; 20 confirmed).**
  - **Machine-injected prompts** (`source: system / loop_wakeup / schedule_wakeup / poll_event` —
    task notifications, auto-continuation) continue the current task instead of resetting the
    ledger and re-classifying the notification text. **Stop with `background_tasks` in flight**
    does not block: the session is paused, not done.
  - An approval that names the action ("ok fix it", "네, 수정해주세요") keeps a deep mode.
  - `cd` state is positional: inline python / `node -e` / PowerShell `[IO.File]::Write*` writes
    take the state where they ran; a relative `cd sub` / `cd -` keeps it. `tmp`/`temp` is scratch
    only in an absolute path outside the project cwd (a project under `C:\temp` is gated again;
    `src/tmp/x.py` is code). Deleting generated artifacts (`__pycache__`, `.pytest_cache`, `dist`,
    `build`, `*.egg-info`, `.coverage` …) is not a change.
  - Inline python writes: `open(os.path.join(…), "w")` counts; a literal under a variable root is
    reported as `$ROOT/tail` and resolved through the line's own assignments (tempfile/TEMP roots
    are scratch, unknown roots count); CSS-like literals are not paths.
  - Quote handling: backslash escapes inside and outside double quotes, payloads unescaped as the
    shell would (`ssh h "… sh -c '… \"…\" … pytest'"` is a verification), line continuations
    joined; the inline-shell `-c` rule applies only under shells/docker/ssh; the wrapper scan and
    the any-position runner match are linear (a 3,000-quote line took >3s).
  - Runners: `for t in tests/test_*.py; do python "$t"`, `python manage.py test`,
    `run_tests.sh`/`runtests.py`, `just|bazel|php artisan|hatch run test`, `pwsh … -File x.ps1`
    past option values. Piped verdicts: go `--- FAIL`, `error TS…`, eslint `✖ N problems`, mypy
    `Found N errors`, cargo `test result: FAILED` / `error[E…]`.
  - A check after `git stash pop` in the same call is recorded; failure classes use the end of
    the output, not the runner header; progress.json is also found under bash `$HOME`.

### Changed

- **Escalation step §4(2) targets the Agent tool's `effort: "max"`** (a skill instruction is the
  explicit ask that parameter requires) instead of a Workflow that only exists after the user
  opts into multi-agent orchestration. The CLAUDE.md block names the same path.
- **Tests are pytest-collectable.** Each script-style suite exposes `test_script()`; the script
  form stays canonical. `pytest tests` used to crash with `INTERNALERROR: SystemExit`.
  `tests/conftest.py` restores `os.environ` per test, so one suite's `FABLIZE_HOLDOUT=1` no
  longer leaks into the next under pytest.
- New `tests/test_router.py` drives the bash hooks the way Claude Code does.

## [2.1.1+cakel.6] — 2026-10-23

### Fixed

- **Version compare now counts the fork build number.** `setup.sh`'s downgrade guard and
  `gate_prompt._version_key` kept only the first three numbers, so `2.1.1+cakel.3` and
  `2.1.1+cakel.4` compared equal. An old `cakel.3` setup then overwrote a `cakel.4` setup with
  exit 0, and an old session was told to run setup instead of "Do NOT run setup". Observed
  2026-10-23. Both now compare every number in the version.

## [2.1.1+cakel.5] — 2026-10-23

### Changed

- **Plugin author is now `fivetaku+cakel <cakel@hanmail.net>`.** The marketplace `owner` stays
  `fivetaku`.

## [2.1.1+cakel.4] — 2026-10-23

### Fixed

- **A checker run after another python call now counts as verification.** `runs_script_test()`
  returned on the first python segment of a line, so `python tools/sync_part.py …; python
  tools/run_ci_lint.py` recorded no verification while the reverse order did, and the Stop gate
  blocked the turn twice. It now leaves only that segment and keeps scanning the rest.

## [2.1.1+cakel.3] — 2026-10-22

### Fixed

- **Hook `timeout` raised 10s -> 30s.** `router.sh` hit `UserPromptSubmit hook … timed out after 10s`
  (output discarded, so no routing context that turn). Cause: intermittent cold-start spikes of
  `python3` (~5s observed once, ~0.1s otherwise; cause not isolated) with two Python-launching
  hooks on the same event. 30s leaves headroom; hooks still fail open.

## [2.1.1+cakel.2] — 2026-10-22

### Changed

- **Marketplace name is now `cakel`** (was `fablize`, identical to upstream). A marketplace
  name is the unique key in a user's `known_marketplaces.json`, so the fork and
  `fivetaku/fablize` could not be added side by side. The plugin ID is now `fablize@cakel`;
  install with `/plugin install fablize@cakel`. Existing `fablize@fablize` installs must be
  removed and reinstalled.

### Fixed

- **Star target is now `cakel/fablize`.** `setup.sh` `REPO`/`REPO_URL` and the README star badges
  pointed at upstream, so `setup.sh star yes` starred `fivetaku/fablize`.

## [2.1.1+cakel.1] — 2026-10-21

Version scheme change. This fork is upstream 2.1.1 (`fivetaku/fablize`) plus its own
commits, so the fork now reads `<upstream version>+cakel.<n>` (SemVer build metadata)
instead of continuing upstream's numbers. The 2.1.2–2.1.8 entries below are the fork's
earlier releases under the old scheme and are kept as history; no tags were ever
published for them.

### Fixed

- **A command run over ssh no longer counts as a local file change.** `_SEGMENT_RE` ignores
  quotes, so `ssh host 'cd x && cp .env .env.bak && …'` was split inside the payload and the
  remote `cp` set `changed_files_seen`. Every deploy turn (deep via 배포/deploy) then armed the
  Stop gate on a change nothing local could verify — 4 of 4 blocked turns in one session had
  this as their only change. `changed_kinds` now drops the quoted ssh payload; verification
  detection still sees the full command. Known ceiling: ssh-wrapped checks are still not
  recognised as verification.
- **The Stop-gate message no longer promises a waiver that does not exist.** "…or record why
  none applies" had no effect (the decision reads only mode, stop_blocks, changed, verified).
  It now says to state it in the final response and that the gate relents after 2 reminders.

## [2.1.8] — 2026-08-26

Fork release (`cakel/fablize`), continuing the fork's own lane from 2.1.7. Upstream
(`fivetaku/fablize`) is at 2.1.1; everything from 2.1.2 on is fork-only.

### Fixed

- **The staleness notice now names the command to run, and knows which session is old.**
  It said "Re-run `/fablize:setup`" — the very thing that had caused the staleness. That
  command carries `${CLAUDE_PLUGIN_ROOT}` expanded when a **session loaded it**, so in a
  session opened before the upgrade it runs the previous version's `setup.sh`, which is
  still on disk, succeeds, and records the old version again. The notice now prints the
  concrete `bash <running-root>/setup/setup.sh <scope>`, using the scope from the record,
  and says why the slash command is not the same thing.

  The comparison is also two-way now. `recorded` newer than `running` is not a downgrade
  that happened — it means **this session is the old one**. `hooks.json`'s
  `${CLAUDE_PLUGIN_ROOT}` is resolved when the hooks are registered, so a session opened
  before an upgrade keeps running the old `gate_prompt.py` for its whole life. Reported
  live on 2026-08-26 by a second session showing `(2.1.7 -> 2.1.3)`, which the one-way
  wording called an upgrade. That direction now tells the user **not** to run setup there
  and offers no command at all, because running one is exactly what must not happen.

- **2.1.7's downgrade guard does less than its changelog claimed.** It said an older copy
  of `setup.sh` "can no longer roll a newer setup back". A guard shipped in a new version
  cannot run inside an old copy: every pre-2.1.7 root already in the plugin cache still
  overwrites freely. Verified live — with 2.1.7 recorded, a real 2.1.3 copy ran to
  completion, exit 0, and recorded 2.1.3. The guard only stops a downgrade between roots
  that both carry it, which is a forward-looking property, not a fix for what was
  observed. `tests/test_setup_paths.py` did not catch this because `fake_plugin_root()`
  copies the current `setup.sh` into every fake version, so its "old" root had the guard.
  The same over-generosity as 2.1.6's payloads. Both the test and the claim now say so.

  Covered by `tests/test_setup_paths.py` (26 → 36 checks): the notice names the running
  root, follows the recorded scope, defaults a missing or invalid one, warns about the
  slash command, and produces the opposite message with no runnable command when this
  session's plugin is the older one.

## [2.1.7] — 2026-08-26

Fork release (`cakel/fablize`), continuing the fork's own lane from 2.1.6. Upstream
(`fivetaku/fablize`) is at 2.1.1; everything from 2.1.2 on is fork-only.

### Fixed

- **An older copy of `setup.sh` can no longer roll a newer setup back.** `/fablize:setup`
  is written with `${CLAUDE_PLUGIN_ROOT}`, and the harness expands that **when a session
  loads the command** — so a session opened before an upgrade still holds the previous
  version's path. The plugin cache keeps every installed version on disk, so running it
  succeeds: no error, no missing directory, just `~/.fablize/lib` and `progress.json`
  quietly reverted. The only trace is the staleness notice reappearing, which points at
  `/fablize:setup` — the very thing that caused it.

  Observed live on 2026-08-26. `2.1.5` was applied at 16:44:39; at 16:51:02 a second
  session holding the `2.1.3` path overwrote the packs and recorded `2.1.3`. It reported
  success, and the user was told they were applying 2.1.5. Three versions were resident
  in the cache at the time. The backup filenames setup.sh leaves behind
  (`CLAUDE.md.fablize-bak.<ts>`) were what made the second run attributable at all.

  Setup now compares the manifest version it is about to apply against the recorded one
  and refuses to go backwards, naming both versions and the path it was invoked from.
  The check runs **before** anything is written, so a refused run leaves the assets, the
  record and the CLAUDE.md backups untouched. Re-applying the same version is not a
  downgrade. `FABLIZE_ALLOW_DOWNGRADE=1` is the deliberate escape hatch, and a missing or
  unreadable manifest or record never blocks setup — first runs must always work.

  This is the same class 2.1.3 closed for the injected CLAUDE.md block: a version-pinned
  path outliving the version. That fix could live in the block because the block is ours.
  The command file is not — the harness expands the path, not this repo — so the defence
  has to sit in the thing that actually runs. Covered by `tests/test_setup_paths.py`
  (17 → 26 checks), driving the real `setup.sh` through upgrade, refused downgrade,
  same-version re-apply, and the escape hatch.

## [2.1.6] — 2026-08-26

Fork release (`cakel/fablize`), continuing the fork's own lane from 2.1.5. Upstream
(`fivetaku/fablize`) is at 2.1.1; everything from 2.1.2 on is fork-only.

### Fixed

- **A verification whose result is unknown no longer counts as no verification at all.**
  `has_successful_verification()` required `success is True`, and `success` is tri-state:
  True is an observed pass, False an observed failure, None a command that ran and
  printed nothing `FAILURE_RE` or `SUCCESS_RE` recognises. 2.1.5 documented None as a
  known ceiling on the assumption it was a corner case. It is not.

  Measured across the 3,347 ledgers on this machine: of 136 recorded verifications,
  **97 are None, 25 True, 14 False — 71% unknown.** The hook payload usually carries no
  exit code, so `exit_success()` falls back to the output tail, and a great deal of real
  output says neither. `check_doc: ERROR 0 / WARN 0 [strict]` is the case that started
  this: `ERROR 0` is not the `[1-9]… errors` shape `FAILURE_RE` looks for, and nothing in
  it matches `SUCCESS_RE` either. The check ran, passed, and was scored as if it had
  never happened.

  The rule is now "a verification ran and nothing observed says it failed": `any(success
  is not False)`. An observed failure still does not satisfy the gate — a turn whose only
  check returned False blocks exactly as before, and the failure is disclosed separately
  through the ledger's `failures`. This is the same correction as 2.1.5's, one layer up:
  the gate was asserting something about evidence it never saw.

  Widening `SUCCESS_RE` instead would have re-opened the false-positive class
  `test_gate_false_positive.py` exists to hold shut, so the tri-state is read where it is
  interpreted rather than where it is produced. `exit_success()` is untouched.

  Verified live rather than only in tests. Installing 2.1.5 and replaying the original
  blocked turn produced `change_kinds ['docs']` and a recorded checker — with
  `success: None`, which is how the size of this was discovered. That turn had passed on
  the docs exemption, not on its verification; a turn that changes code and runs the same
  checker was still blocked. Covered by `tests/test_gate_observed_blocks.py` (22 → 27
  checks), which also gains a `silent()` payload helper: the previous tests supplied an
  exit code the real harness does not send, which made them more generous than production.

### Known ceiling

- **A fetch whose body reports a failure now satisfies the gate.** With no exit code in
  the payload, `curl … -o out.json` returning a 404 page and the same call returning
  clean JSON are both recorded as None, and None now counts. Distinguishing them needs
  the HTTP status apart from the process status, which the payload does not carry. This
  is the deliberate cost of the change above: the gate errs toward believing a check that
  ran, because 71% of the checks it was disbelieving had in fact passed. Pinned as a
  `CEILING:` check.

## [2.1.5] — 2026-08-26

Fork release (`cakel/fablize`), continuing the fork's own lane from 2.1.4. Upstream
(`fivetaku/fablize`) is at 2.1.1; everything from 2.1.2 on is fork-only.

### Fixed

- **A mutating shell line is now classified by its operands, not by its verb alone.**
  `changed_kinds` read only the command word, so every mutation reported `["other"]` —
  a claim about files it never looked at. Moving one Markdown file with `cp` said a
  non-docs thing had changed, which is exactly what `verify_state.docs_only()` reads to
  decide whether a docs-only turn needs verification. Observed live: a turn that rewrote
  one `.md` file and ran the repository's own checker to `ERROR 0 / WARN 0` was blocked
  at Stop for "no observed verification", twice in a row.

  `mutated_paths()` walks the same segments as `runs_script_test`, confirms the command
  word is a mutating one, and collects the operands for `classify_path_kind`. Flags,
  redirection debris left by `_SEGMENT_RE` splitting `2>&1`, and bare numbers are
  skipped; when nothing can be named the result stays `["other"]`, as before.
  `grep -rn "rm -rf" docs/` and `ls /tmp/cp-backup` remain negative — the command-position
  rule is unchanged.

- **`mkdir` no longer counts as a file change.** Creating an empty directory alters no
  file and leaves nothing whose behaviour could be verified, but it set
  `changed_files_seen` and pushed `"other"` into `change_kinds` — which **accumulates**
  across a turn, so a single `mkdir -p` for a scratch directory disarmed `docs_only()`
  for every edit that followed. One observed block came from nothing but a scratch
  `mkdir` and a `curl -o` into it. `chmod`, `mv`, `cp`, `rm`, `touch` and `apply_patch`
  are untouched.

- **A project's own checker counts as verification.** `python tools/check_doc.py --strict`
  was invisible to all three rules: no `pytest` token for `VERIFY_TOOL_RE`, command word
  `python` rather than a verb for `VERIFY_VERBS`, and `_TEST_ENTRY_RE` accepts only
  `test_x.py` / `x_test.py`. It runs a real check and exits non-zero on failure, which is
  all the gate needs. `_CHECK_ENTRY_RE` adds `check`/`verify`/`validate`/`lint` entry
  points in the same command position, and the `[_-]` is load-bearing exactly as it is
  for tests: `checkout.py`, `checker.py`, `verifier.py`, `linter.py`, `validation.py` and
  `pylint_config.py` all stay negative, as do `cat tools/check_doc.py`,
  `grep -rn "python tools/check_doc.py" docs/` and `python manage.py check_perms.py`.

- **Prose is no longer read as a command.** `command_from_input` fell back to
  `tool_input["description"]` — a human sentence — whenever no command was present, and
  every classifier then parsed it as a shell line. `description="check the build output"`
  returned `is_verification_command` **True**, and `description="mkdir the output
  directory"` returned `changed_kinds ["other"]`. The first is the dangerous direction:
  a false change only makes the gate noisy, but a false verification makes it pass in
  silence, which is the single failure it exists to prevent. The fallback dates to the
  gate's first commit (`50463ff`) with no recorded rationale; it is removed rather than
  narrowed, because a call that ran no command has no command to classify.

- **`PostToolUse` now fires for the `PowerShell` tool** (`hooks/hooks.json`). The matcher
  was `^(Bash|Edit|Write|NotebookEdit|MultiEdit)$`, so nothing done through PowerShell
  ever reached the ledger — while `parse_tool_result.TEXT_INFERENCE_TOOLS` already listed
  `PowerShell`, code the hook never delivered a payload to. On Windows, where it is the
  default shell, a session that changed files with Bash and verified with PowerShell
  recorded the change and none of the verification, which blocks with certainty; a
  session that used PowerShell throughout left the gate inert. `MUTATING_CMDS` gains the
  cmdlet spellings (`Copy-Item`, `Move-Item`, `Remove-Item`, `New-Item`, `Set-Content`,
  `Add-Content`, `Out-File`); `cp`/`mv`/`rm` are PowerShell aliases and were already
  covered. `BashOutput` is deliberately still excluded — it reads a call that already
  happened, and counting it would record the same change twice.

  Covered by a new `tests/test_gate_observed_blocks.py` (22 checks — both blocked turns
  replayed end to end through the real ledger, plus two control turns proving the gate
  still fires on unverified code and is not satisfied by a check that failed, plus the
  matcher itself), `tests/test_gate_classification.py` (35 → 61 checks) and
  `tests/test_gate_script_tests.py` (8 → 16 runs detected, 15 → 25 non-runs ignored).

### Known ceiling

- **A verification that succeeded but printed nothing status-shaped is recorded as
  unknown, and unknown does not count.** When the hook payload carries an explicit exit
  code this never arises. When it does not, `exit_success()` falls back to the output
  tail, and plenty of real output matches neither `FAILURE_RE` nor `SUCCESS_RE` — a JSON
  body, an HTTP page, a tool that prints only its result. `verification_record` then
  stores `success: None`, and `has_successful_verification()` accepts only `is True`, so
  a check that ran and passed leaves the deep gate armed. Measured: `curl … -o out.json`
  returns `True` with `exit_code: 0` present and `None` without it, for both a 404 body
  and a clean one. Widening `SUCCESS_RE` to close this would re-open the false-positive
  class `test_gate_false_positive.py` exists to hold shut, so it stays open and pinned
  as `CEILING:` checks instead.

## [2.1.4] — 2026-08-26

Fork release (`cakel/fablize`), continuing the fork's own lane from 2.1.3. Upstream
(`fivetaku/fablize`) is at 2.1.1; everything from 2.1.2 on is fork-only.

### Fixed

- **A suite run as a plain script now counts as verification.** `is_verification_command`
  recognised `pytest`, `unittest`, `npm test` and friends, plus the verbs `build`/`check`/
  `make` in command position — but not `python tests/test_x.py`, whose only command word is
  `python`. Any stdlib-only project verifies that way, fablize's own suite included, so a
  deep turn that ran its tests recorded zero verifications and the Stop gate blocked it for
  "no observed verification". Observed in a real session: eight deep, file-changing turns
  in a row logged `verifications=0` while the suite was in fact run six times, and the only
  command the gate did record was the one investigating this bug — the "fires on reading
  itself" failure mode `test_gate_false_positive.py` exists to prevent, in reverse.

  Detected in command position only, and only on the script python actually executes: the
  segment's command word must be `python`/`python3`, and its first non-flag argument must be
  a `test_x.py` / `x_test.py` entry point. `cat test_x.py`, `grep "python test_x.py" docs/`,
  `rm test_x.py` and `python manage.py test_import.py` all stay negative. Covered by
  `tests/test_gate_script_tests.py` (8 runs detected, 15 non-runs ignored, plus the ledger
  record end to end); mutation-checked five ways.

## [2.1.3] — 2026-08-03

Fork release (`cakel/fablize`).

### Fixed

- **The injected CLAUDE.md block no longer bakes a version-pinned path**
  (`setup/setup.sh`). It substituted `$CLAUDE_PLUGIN_ROOT`, which is
  `.../cache/<marketplace>/<plugin>/<version>`, so after a plugin upgrade every
  path in the block pointed at a directory that no longer exists — with nothing
  to notice or repair it. Observed live: the block still pointed at `2.1.1`
  after `2.1.2` was installed. Setup now copies `scripts/goals.py` and
  `packs/*.txt` to `~/.fablize/lib/` and injects that stable path. The hooks
  were never affected; they resolve `${CLAUDE_PLUGIN_ROOT}` at load time.
- **The recorded setup version is read from the manifest** (`setup/setup.sh`),
  not a string literal that silently disagreed with the installed plugin after
  every release.

### Added

- **Upgrade staleness notice** (`hooks/gate_prompt.py`). `progress.json`'s
  `version` was written and never read. `stale_setup_notice()` now compares it
  against the running plugin's manifest and prepends one line when they differ,
  pointing at `/fablize:setup` — the only thing that can refresh the copies and
  the CLAUDE.md block. Silent when they match or anything is unreadable.
- `tests/test_setup_paths.py` — 17 checks driving the real `setup.sh` against a
  temp HOME and a fake versioned plugin root: no version in the injected block,
  every referenced asset exists, assets refresh on re-run after an upgrade, the
  block is not duplicated, and the notice fires only on a mismatch.

## [2.1.2] — 2026-08-03

Fork release (`cakel/fablize`). Gate-accuracy fixes only; no behaviour added.

### Fixed

Seven confirmed defects in the observation gate, all one shape: a substring, a
path segment, or a mid-run slice was treated as evidence about the call itself.
Both directions of failure were live — the gate waved through the "I changed
code and it works" turn it exists to catch, and blocked turns that had verified
or changed nothing.

- **Failure detection keyed on content, not status** (`parse_tool_result.py`).
  `detect_failure` matched `FAILURE_RE` against the whole output text when no
  exit status was present, so an `Edit` quoting `SyntaxError:`, a `git log` of a
  commit message, or a `grep` of the detector's own source were all recorded as
  failed calls. Text inference is now limited to tools that run something, to
  verification commands, and to status-shaped patterns.
- **Status window was the front of the output, not the tail**
  (`parse_tool_result.py`). `response_text()` truncates from the front, so a
  4820-char `pytest` run ending in `1 failed, 120 passed` was recorded as
  `success: true` — scenario S3 in `tests/test_gate.py`, the case the gate
  exists to catch. `status_text()` now walks the raw response for the true tail.
- **`docs/` path segment outranked the file extension** (`ledger.py`), so
  `docs/conf.py` and `packages/docs/src/index.ts` classified as docs and
  `docs_only()` exempted real code edits from the deep gate.
- **`VERIFY_RE` matched bare `build`/`check`/`verify`/`curl` anywhere**
  (`parse_tool_result.py`), so `cat build.log` counted as a verification run and
  satisfied the deep gate with no check having run.
- **Corrupt-ledger recovery reset `task_mode` to `quick`** (`ledger.py`),
  disarming the gate for the rest of a deep turn after one unreadable read. It
  now falls back to `normal` and records why.
- **`MUTATING_BASH_RE` matched bare `rm`/`cp`/`mv` anywhere**
  (`parse_tool_result.py`), so `grep -rn "rm -rf" docs/` counted as a file
  change and blocked a read-only turn.
- **Topic keywords overrode explicit scope** (`classify_task.py`), so
  "배포 절차만 간단히 설명해줘" got the deep block. `NO_EDIT_RE` now stands alone;
  risk flags are still recorded and surfaced.

`command_words()` is the shared helper for the two command-position rules: it
takes the command word of each `;`/`&&`/`|` segment, skips leading env
assignments and `sudo`, and reduces a path to its basename.

### Added

- `tests/test_gate_false_positive.py` — 15 cases pinning observed false
  positives, real failures, and the long-output truncation boundary.
- `tests/test_gate_classification.py` — 35 checks across path kind, verification
  detection, mutation detection, task mode, and a ledger corrupted on disk.

### Known ceiling

`ledger.redact()` flattens newlines, so the detector cannot distinguish "failure
word early, clean summary later" from a real failure; failure wins. Pinned as a
`CEILING:` case in the test.

## [2.1.1] — 2026-07-06

동의 우선(consent-first). 셋업의 무고지 자동 star를 제거하고, 커뮤니티가 보고한 보안·라이선스 이슈를 정리했다.

### Fixed
- **셋업의 무고지 자동 star 제거** (#4) — `setup.sh`는 더 이상 스스로 star하지 않는다. `setup.sh ask`가 마커(`~/.fablize/star.json`) 기반으로 딱 한 번 `STAR_ASK <lang>`를 내보내고, 커맨드/스킬 플로우가 AskUserQuestion으로 물어본 뒤 명시적 `setup.sh star yes`일 때만 star한다. "asked" 마커를 bash가 기록하므로 질문은 최대 1회만 노출된다.
- **Stop 훅 transcript_path 검증** (PR #8, @xiaolai) — hook 입력의 `transcript_path`가 HOME/TMPDIR 밖이면 열지 않는다 (조작된 경로로 임의 파일이 읽히는 것 차단).

### Added
- **LICENSE 파일** (#9) — README의 MIT 배지와 실제 라이선스가 일치하도록 MIT LICENSE 추가.

## [2.1.0] — 2026-06-18

측정 우선(measure-first). 검증되지 않은 성능 기능은 켜지 않고, 게이트의 노이즈를 줄이고, 효과를 실제 작업에서 잴 out-of-band 측정 인프라를 추가했다.

### Added
- **관측 게이트(observation gate)** — Stop 훅이 `deep + 파일변경 + 검증 미관찰` 턴의 완료를 차단한다(quick/normal/docs/변경0은 통과). 모델의 주장이 아니라 관측된 ledger 증거로만 판단(언어 무관). MAX 2회까지 막고 그 뒤 통과(무한 트랩 방지).
- **작업 분류기** — 프롬프트를 quick/normal/deep로 분류 + risk flags(production/database/secret-or-auth/remote-write) 추출.
- **out-of-band 측정 인프라(shadow)** — `events.jsonl` 로거/수집기, env-gated holdout 토글(`FABLIZE_HOLDOUT`, 기본 OFF), 결과신호 수집기(revert/rework/재지시), 층화 분석 + sunset. **기본 OFF, 모델 컨텍스트 밖**에서만 기록(관측자 효과 차단). 설계는 `docs/MEASUREMENT_PROTOCOL.md`.
- **reactive effort-delegation rung** (`SKILL.md §4`) — 막힌 *bounded slice*만 백그라운드 Workflow(`effort:max`)로 위임 후 결과로 재개. 일반 세션의 유일한 per-task effort 노브. **opt-in·효과 미입증**, risk/deep만으로는 발화 금지(false-escalate 가드).
- **silent-recovery 가드** — 일회성 실패는 조용히 복구, 같은 계열 실패가 2회+ 반복되면 짧게 공개(정규화 signature로 같은 계열 판정).
- **테스트** — 게이트(6) + robustness(12) + shadow/M3/M4/recovery.

### Changed
- **게이트 deep-only + 변경 있을 때만** — normal 모드 hard-block 제거, deep도 *변경 없는* 턴(분석/문서/리딩)엔 발화 안 함. 실측상 발화 노이즈 ~2/3 감소. 진짜 위험("deep에서 코드 바꾸고 검증 안 함")만 차단.
- **deep 프롬프트 넛지** — 증거/gap 노트는 *실체가 있을 때만*(검증했거나 주장이 tool result에 근거) 한 줄, 사소한 턴엔 생략. 의례용 보일러플레이트 제거.
- **setup** — setup 흐름 정리(단일 up-front 질문, 내부 헬퍼를 setup.sh에 인라인).

### Removed
- 내부 setup 헬퍼 스크립트 — setup.sh에 인라인.
- `has_any_verification()` — 게이트 축소 후 미사용.

### Notes (정직성)
- **효과는 미입증.** 새로 켠 성능 기능은 없다. shadow 측정기로 실작업에서 게이트·effort 위임의 효과를 사후에 측정하기 위한 릴리스다. 토이 A/B 3라운드는 천장으로 무력했다(자세한 분석은 내부 문서).
- 내부 R&D 문서(개인경로·PII 포함)는 `.gitignore`로 배포 제외.

## [2.0.0] — 2026-05

- 초기 fablize 하네스 — Opus를 Fable처럼(완결·증거·검증을 절차로 강제) 동작하게 하는 스킬 + setup.

[2.1.0]: https://github.com/fivetaku/fablize/compare/v2.0.0...v2.1.0
[2.0.0]: https://github.com/fivetaku/fablize/releases/tag/v2.0.0
