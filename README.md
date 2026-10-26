English | [한국어](README.ko.md)

# fablize — run Opus like Fable

[![GitHub stars](https://img.shields.io/github/stars/cakel/fablize?style=flat&color=yellow)](https://github.com/cakel/fablize/stargazers)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/licenses/MIT)

A Claude Code plugin that makes Opus (or any Claude model) **see a task through to the end — with evidence and verification — as procedure, not as luck.**

## Why

When Fable 5 shipped, I ran a controlled comparison of Fable 5 and Opus 4.8 (an A/B set of 19 runs plus 26 real working sessions, ~1,500 tool calls). The finding:

- On closed, answer-bearing work (code, logic, builds), the two models were effectively tied.
- The gap appeared only on open-ended work, and its nature was "following an implication one step further."
- That depth is **model capability** — it could not be transferred by instructions or a harness. An injection experiment confirmed it: Opus could not reproduce the defects Fable found on its own.
- But the **procedure** of good work — actually running what you build, seeing it through, investigating systematically — does transfer.

fablize applies those procedures. It does not raise the model's ceiling; it aims to make the model reach its own ceiling. Only verification grounding has a measured effect (the upstream A/B); the other components are applied while their effect is still being measured — see [What's included](#whats-included) and [docs/MEASUREMENT_PROTOCOL.md](docs/MEASUREMENT_PROTOCOL.md).

## What transfers and what doesn't

| Trait | Transferable? | Reason |
|---|:--:|---|
| Verification grounding (run & observe the artifact) | ✅ shipped | A procedure Opus skipped "not because it can't, but because it didn't." Injecting it raised render-verification behavior measurably. |
| Multi-story completion + evidence gate | ✅ shipped | A procedure — decompose, checkpoint, refuse completion without proof. |
| Systematic investigation (reproduce → hypotheses → causal chain) | ✅ shipped | A procedure — on par with what the strong model already does. |
| Early-stop prevention | ⚙️ opt-in | A deterministic hook — catches "I'll do X" without doing it. Shipped, but you register it yourself. |
| Out-of-spec defect discovery | ❌ not possible | Capability. Injection was refuted — the model finds it, or it doesn't. |
| Open-ended creative detail | ❌ not possible | Capability. Shows only where there is no fixed answer. |
| Self-driven propagation depth | ❌ not possible | Capability. Directed propagation transfers; self-started depth does not. |

The non-transferable rows are the model's job (or a human's), not a harness's. When you hit them, fablize tells you to escalate instead of pretending.

## What's included

| Component | How it runs | Effect |
|---|---|---|
| **Verification grounding** — render/executable artifacts (HTML, SVG, games, charts) are run and observed before completion | pack, routed by signal | measured (upstream A/B) |
| **Multi-story verification gate** — `goals.py` decomposes work; the final story completes only when its verify command, run by `goals.py`, exits 0 | always-on block / skill | not measured |
| **Investigation protocol** — reproduce, compete hypotheses, trace the full causal chain, check sibling callers, regression test | pack, routed by signal | not measured |
| **Per-task router** — injects the matching pack on a debugging or render signal | `UserPromptSubmit` hook, automatic | not measured (HOLD) |
| **Observation gate** — classifies each prompt (quick/normal/deep); if a deep turn changed non-doc files with no observed successful check, the **Stop is blocked**; normal mode only gets a nudge (at most 2 reminders per prompt, then a warning only; no block at all when your prompt says not to run tests/commands — you get the warning instead) | `UserPromptSubmit` / `PostToolUse` / `Stop` hooks, automatic | not measured (HOLD) |
| **Recovery guard** — after a failed check, reminds the model not to report completion; after the same class of failure twice, tells it to disclose the failure instead of retrying silently | `PostToolUse(Failure)` hook, automatic | not measured (HOLD) |
| **Early-stop hook** (`finish-the-work.sh`) — blocks a final message that only promises work | `Stop` hook, **opt-in** (setup prints how to register it) | not measured |

"Not measured" means the component is applied, not that it is proven to help. A 2026-10-24 check (two planted-root-cause bug repos, Sonnet and Opus, with and without the skill, hidden tests) passed 6/6 in both arms — the tasks were too easy to separate them. Not shipped: style mimicry, broad reasoning injection, a review-recall scan. Open design decisions and what to check when one is questioned: [docs/DECISIONS.md](docs/DECISIONS.md).

> This is a fork of [`fivetaku/fablize`](https://github.com/fivetaku/fablize) (upstream 2.1.1) with its own fixes; versions read `2.1.1+cakel.<n>`. See the [CHANGELOG](CHANGELOG.md).

## Install

```
/plugin marketplace add cakel/fablize
/plugin install fablize@cakel
```

The router and the observation gate (`UserPromptSubmit`, `PostToolUse`, `PostToolUseFailure`, `Stop` hooks) register automatically. The gate can block a turn from ending, at most twice per prompt (see [What's included](#whats-included)). The early-stop hook is opt-in.

**Windows:** the hooks run through `bash`, so Git for Windows (Git Bash) must be installed. Python 3.8+ is found as `python3`, `python` or `py`.

For always-on operation (the rules resident in context), run once:

```
bash "${CLAUDE_PLUGIN_ROOT}/setup/setup.sh" local    # or: global
```

Uninstall: `bash "${CLAUDE_PLUGIN_ROOT}/setup/uninstall.sh" local` (or `global`)

> If fablize helps, a ⭐ on the repo means a lot — setup asks once (a single opt-in question); it never stars without your explicit "yes".

## Data it writes

All local, under `~/.fablize/` (`FABLIZE_DATA` moves it). Nothing is sent anywhere.

- `ledgers/<session>.json` — the gate's per-session state: task mode, changed-file kinds, and the verification commands it saw this turn.
- `history/events.jsonl` — an append-only log of hook decisions (router signal, Stop block, cap warning, early-stop block) with labels and short matched terms only, no prompt text. Used to judge the open decisions in [docs/DECISIONS.md](docs/DECISIONS.md). Turn it off with `FABLIZE_EVENTS=0`.
- `./.fablize/` in your repo — `goals.py` plans and their ledger (including the verify command's output tail).

## How it behaves

- Trigger with `/fablize` or phrases like "see it through", or automatically when installed always-on.
- 2+ stories → decompose + verification gate. Debugging → investigation protocol. Render artifact → verification grounding. Hard task → adaptive thinking plus an `/effort xhigh` suggestion.
- At the capability ceiling, escalate to a stronger model or a human.

## Honest limits

- It cannot raise model capability. Open-ended creative quality and self-driven discovery are out of reach — that is a model-choice decision, not a harness one.
- The effect numbers come from a small, single-family (Claude) self-measurement (the 19-run A/B set). The direction is solid; the decimals are not asserted. Only verification grounding is covered by them.
- The observation gate decides "did this turn verify?" by parsing shell commands heuristically. Expect occasional false blocks (an extra turn or two) and false passes; it fails open on any internal error.
- The early-stop hook (if you register it) can misfire on a declarative offer ("I'll write the report if you want") — phrasing offers as questions avoids it.

## License

MIT — see [LICENSE](LICENSE).
