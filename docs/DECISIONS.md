# Open decisions log

Decisions that were deliberately **not** made yet, with the evidence on hand and
what to look at when a problem shows up. Each entry says what the current
behaviour is, so "nothing decided" is never ambiguous.

The hooks record their decisions out-of-band in `~/.fablize/history/events.jsonl`
(`FABLIZE_DATA` moves it, `FABLIZE_EVENTS=0` turns it off). No prompt text is
written, only labels and short matched terms.

| event_type | written by | payload |
|---|---|---|
| `router_signal` | `hooks/router.sh` | `signals: [{pack, matched}]` (matched term ≤ 40 chars) |
| `stop_block` | `hooks/gate_stop.py` | `mode, change_kinds, stop_blocks, verifications, unverified_change` |
| `stop_cap_warning` | `hooks/gate_stop.py` | same as `stop_block`, written when the cap lets the turn end |
| `ftw_block` | `hooks/finish-the-work.sh` (only if registered) | `matched` (promise phrase ≤ 60 chars) |
| `holdout_suppress` | `hooks/gate_stop.py` (only with `FABLIZE_HOLDOUT=1`) | `would_block_reason` |

Quick queries (bash, from any directory):

```bash
E=~/.fablize/history/events.jsonl
rg -c '"stop_block"' "$E"; rg -c '"stop_cap_warning"' "$E"   # blocks vs blocks the cap let through
rg '"router_signal"' "$E" | rg -o '"matched": "[^"]*"' | sort | uniq -c | sort -rn | head -30
rg '"session_id": "<id>"' "$E"                                 # one session's decisions, in order
```

Match a session id against its transcript under `~/.claude/projects/<project>/<session_id>.jsonl`
to see what the model was doing when the gate fired.

---

## D1. README effect claims vs. measurement status

- **Opened:** 2026-10-26 (cakel.7 review).
- **Conflict:** `README.md` says "fablize applies only the procedures whose effect was
  verified" and lists the per-task router under "What's included (verified only)", and
  says a silent-recovery guard is "not shipped". `docs/MEASUREMENT_PROTOCOL.md` lists
  pack-router, silent-recovery and the Stop gate as HOLD (unproven), and the recovery
  guard (`detect_failure` / `repeated_failure`) does ship in `gate_post_tool.py`.
- **Evidence so far:** 2026-10-24 A/B (2 planted-root-cause repos with hidden tests,
  Sonnet ×2 + Opus ×1, with/without the skill) — 6/6 passed either way, i.e. no
  measurable outcome difference; the tasks hit the model ceiling, and the baseline arm
  still had the global CLAUDE.md block. Process-only deltas: fablize runs added
  regression tests more often (3/3 vs 2/3) and listed more rejected hypotheses.
- **Decided 2026-10-26 (cakel.7):** option (a). README now has a per-component table
  (measured: verification grounding only; everything else "not measured"), describes the
  observation gate and recovery guard, marks finish-the-work opt-in, and lists the data
  written under `~/.fablize/`. Rejected: (b) keep the claims until D3 — shipping a claim
  the repo's own protocol calls unproven.
- **Revisit when:** D3 produces a result — then a component may move to "measured".

## D2. Stop gate: blocking vs. advisory

- **Opened:** 2026-10-26.
- **Current state:** blocking, capped at `MAX_STOP_BLOCKS = 2` per prompt, then a
  `systemMessage` for the user. Whether a turn "verified" is decided by heuristic shell
  parsing (`scripts/gate/parse_tool_result.py`); eight review rounds kept finding new
  edge cases (no ROI plateau), so false blocks and false passes are both expected.
- **Known costs:** a false block costs one or two extra model turns; the gate also
  rewards ceremonial checks (SKILL.md §3-2 tells the model to make checks visible to
  the gate, which a model can satisfy with a trivial command).
- **Options:** (a) keep blocking; (b) advisory only — `systemMessage`/`additionalContext`
  with no `decision: block`; (c) block only for `deep` mode, advise for `normal`.
- **Partly decided 2026-10-26 (cakel.8):** the user's instruction outranks the gate. A
  prompt that forbids running checks turns a would-be block into the user-facing
  unverified warning (`stop_user_forbade`), and the block text no longer offers "say so" as
  a way out. Blocking vs advisory in general is still open (deep already blocks, normal
  already only advises — option (c) is the current behaviour).
- **What to look at:** in `events.jsonl`, the share of `stop_block` events followed by a
  `stop_cap_warning` in the same session (the model never satisfied the gate — likely a
  false block or an unverifiable change), and `change_kinds` / `mode` of those events.
  Read the transcript for a sample: was the block right?
- **Revisit when:** a user reports a block loop or a block on a turn that did verify, or
  cap warnings exceed roughly a third of blocks over a few dozen sessions.
- **Observations:**
  - 2026-10-26 smoke (Opus 5.5, Windows, clean PC): "calc.py 철저히 리팩터해줘. 테스트는
    돌리지 마" → deep; the model edited, ran `python -m py_compile calc.py && echo ok`
    on its own, and said behaviour was untested. `py_compile` counts as a verification,
    so no block. The user's "no tests" was respected and the gap disclosed — but a syntax
    check satisfied a deep gate for a behaviour change (the §3-2 ceremonial-check risk).
  - Same smoke, "어떤 명령도 돌리지 말고 파일만 수정해" (deep): blocked twice, then the cap
    warning. The model refused both times citing the user, as the block text allowed ("say so"),
    yet the gate kept blocking — two wasted turns. Fixed in cakel.8 (above).
  - Same smoke: Opus ended checks with `; echo "exit=$?"`, which exits 0 whatever the test
    did; an echoed `exit=1` was read as "unknown" and would have passed the gate. Fixed
    (echoed nonzero status = failure).

## D3. Re-measure effect with harder tasks and a clean baseline

- **Opened:** 2026-10-26.
- **Why:** the 2026-10-24 A/B could not separate the arms (ceiling + contaminated baseline).
- **What a valid run needs:** tasks current Sonnet/Opus fail a meaningful share of the
  time without help (multi-file root cause, misleading symptom, sibling caller);
  baseline subagents launched without the global fablize block (`CLAUDE_CONFIG_DIR`
  pointing at a clean config, or `bash setup/uninstall.sh global` for the run);
  hidden tests as the outcome; ≥ 5 runs per arm per model.
- **Current state:** not run. Fixtures from the first attempt are throwaway (session scratchpad).
- **Revisit when:** before any README effect claim is kept or added (D1).

## D4. Register finish-the-work.sh by default?

- **Opened:** 2026-10-26.
- **Current state:** shipped and copied to `~/.fablize/lib/hooks/`, **not** registered in
  `hooks/hooks.json`; `setup.sh` prints how to register it by hand. README describes it
  as part of the package.
- **Known risk:** it blocks on a promise phrase at the end of a message. It blocked
  "…once you give the go-ahead", pushing a destructive step ahead of consent; that and
  similar approval phrasings (EN/KO) now pass (cakel.7), but the pattern list is open-ended.
- **What to look at:** `ftw_block` events (`matched` shows the phrase that triggered it)
  from users who registered it; any block whose message was actually waiting on the user
  is a false positive.
- **Options:** (a) keep manual; (b) register in hooks.json; (c) drop it.
- **Revisit when:** there are enough `ftw_block` events to estimate the false-positive share.

## D5. goals.py runs the verify command itself

- **Decided:** 2026-10-26 (cakel.7) — recorded here because it has a trade-off.
- **Change:** the final story's `--verify-cmd` is executed by `goals.py` (bash when a real
  bash is on PATH, else the OS shell; timeout 1800 s); completion requires exit 0, the
  exit code and output tail are logged in `.fablize/ledger.jsonl`. `true`/`echo`/`:` etc.
  are refused. Previously `--verify-cmd false --verify-evidence "trust me"` completed.
- **Trade-off:** the command runs inside a `goals.py` call, so a Claude Code permission
  rule that allows `…goals.py…` by prefix also allows whatever `--verify-cmd` holds.
  The command is still visible in the Bash tool call the user approves.
- **Limit:** a command that always exits 0 without testing anything (`python -c pass`)
  still passes; the gate in `gate_stop.py` does not count the goals.py call as a
  verification (it is an argument, not a runner).
- **Revisit when:** a permission-prefix setup lets something unintended run, or a final
  story is "verified" by a vacuous command.

## D6. First-run onboarding and the star question

- **Opened:** 2026-10-26 (low).
- **Current state:** the first `/fablize` use without setup asks for a scope and then may
  ask about a GitHub star (SKILL.md), before the user's task.
- **Risk:** interrupts an urgent task. **Revisit when:** a user complains, or upstream changes the flow.
- **Observation (2026-10-26 smoke):** `/fablize:fablize <task>` on a fresh PC asked the scope,
  ran setup, asked the star question, then did the task — two questions before any work.
