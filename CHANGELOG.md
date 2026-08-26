# Changelog

All notable changes to fablize are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/); versioning is [SemVer](https://semver.org/).

## [Unreleased]

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
