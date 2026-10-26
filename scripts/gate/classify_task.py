#!/usr/bin/env python3
"""Task-mode classification for the fablize observation gate (bilingual KO/EN).

Trimmed port of fable-ish: only prompt classification (quick / normal / deep)
and the per-mode context nudge are kept. Danger/secret command blocking is the
host harness's job (e.g. block_dangerous.py), so it is intentionally dropped.
"""

from __future__ import annotations

import re


QUICK_RE = re.compile(
    r"(?i)\b(quick|brief|briefly|simple|simply|just explain|explain only|review only|direction|"
    r"check only|no edits|do not edit)\b|간단히|빠르게|설명만|검토만|방향|확인만"
)
# The subset of QUICK_RE that states the user wants nothing changed. These are
# explicit scope, not intensity, so they outrank topic keywords on their own.
# Kept separate because NORMAL_RE matches the bare word `review`, which would
# otherwise swallow "review only".
NO_EDIT_RE = re.compile(
    r"(?i)\b(just explain|explain only|review only|check only|no edits|do not edit)\b|"
    r"설명만|검토만|확인만"
)
DEEP_RE = re.compile(
    r"(?i)\b(deep|thorough|thoroughly|exhaustive|end-to-end|production-ready|deploy|deployment|"
    r"migration|database|auth|security|refactor|large|complex|implement the plan)\b|"
    r"끝까지|철저|전부|전체|배포|마이그레이션|인증|보안|리팩터"
)
# Debugging / root-cause signals, shared with router.sh (one list, one place).
# "Find the root cause" / "figure out why" / "빌드가 깨졌어" carry no action verb,
# so they used to classify as quick — "no forced verification" on exactly the task
# that most needs it. "error handling" / "오류 처리" is a feature request, not a bug.
DEBUG_RE = re.compile(
    r"(?i)\b(?:debug\w*|bugs?|bugfix|traceback|stack ?trace|crash\w*|segfault|"
    r"regression|flaky|failing|fails|failed|broken|hangs?|hanging|root[- ]cause|"
    r"not working|doesn'?t work|does not work|stopped working|figure out why|why (?:does|is|do|are|did|won'?t|isn'?t|doesn'?t|can'?t))\b|"
    r"\b(?:errors?|exceptions?|failures?)\b(?!\s+(?:handling|handlers?|messages?|modes?|pages?|boundar\w*|codes? (?:list|table)))|"
    r"디버그|버그|트레이스백|크래시|예외|회귀|재현|근본\s?원인|원인|깨졌|깨져|깨진|실패|먹통|멈춰|멈췄|죽어|죽었|"
    r"(?:에러|오류)(?!\s*(?:처리|핸들링|메시지|페이지))|작동하지 않|동작하지 않|작동 안|동작 안|안 되는 이유|왜 안"
)
# Render / executable artifacts → verification-grounding pack. Bare 그래프 is left
# out: "그래프 DB" is not a render target.
RENDER_RE = re.compile(
    r"(?i)\b(?:html|svg|games?|canvas|charts?|render\w*|website|webpage|dashboard|ui)\b|"
    r"차트|게임|캔버스|렌더|웹사이트|웹페이지|대시보드"
)
NORMAL_RE = re.compile(
    r"(?i)\b(implement|fix|debug|change|edit|create|build|test|lint|review|update)\b|"
    r"구현|수정|고쳐|디버그|작성|생성|테스트|검증"
)


def classify_prompt(prompt: str) -> tuple[str, list[str]]:
    text = prompt or ""
    lowered = text.lower()
    risks: list[str] = []
    if "production" in lowered or "배포" in text:
        risks.append("production")
    if re.search(r"(?i)\b(db|database|migration|migrate|schema)\b|데이터베이스|마이그레이션", text):
        risks.append("database")
    if re.search(r"(?i)\b(auth|secret|token|api[_ -]?key|password)\b|인증|비밀|토큰", text):
        risks.append("secret-or-auth")
    if re.search(r"(?i)\b(git\s+push|release|publish)\b|릴리즈|배포", text):
        risks.append("remote-write")

    # Explicit scope beats topic keywords. "배포 절차만 간단히 설명해줘" mentions
    # 배포, so DEEP_RE and the production/remote-write flags both fire and the
    # deep block gets injected — demanding verification for a turn that changes
    # nothing. Risks are still recorded and surfaced; only the mode changes.
    # "no edits" is scope and stands alone; "briefly" is intensity and only
    # counts when no action verb asks for work.
    # A debugging signal is work even without a verb ("빌드가 깨졌어").
    asks_work = bool(NORMAL_RE.search(text) or DEBUG_RE.search(text))
    if NO_EDIT_RE.search(text) or (QUICK_RE.search(text) and not asks_work):
        return "quick", risks
    if DEEP_RE.search(text) or any(flag in risks for flag in ("production", "database", "remote-write")):
        return "deep", risks
    if QUICK_RE.search(text) and not risks and not DEBUG_RE.search(text):
        return "quick", risks
    if asks_work:
        return "normal", risks
    return "quick", risks


def signals(prompt: str) -> list[tuple[str, str]]:
    """Pack signals for router.sh: [(name, matched text), ...] in a stable order."""
    out = []
    for name, rx in (("investigation", DEBUG_RE), ("grounding", RENDER_RE)):
        m = rx.search(prompt or "")
        if m:
            out.append((name, m.group(0)))
    return out


def context_for_mode(mode: str, risk_flags: list[str]) -> str:
    lines = [f"fablize gate — task mode: {mode}."]
    if risk_flags:
        lines.append("Risk flags: " + ", ".join(risk_flags) + ".")
    if mode == "quick":
        lines.append("Keep it concise; no forced verification.")
    elif mode == "normal":
        lines.append("If files change, run one relevant verification command or state why none applies.")
    elif mode == "deep":
        lines.append(
            "Define the exit proof before completion and verify changed behavior before final. "
            "If you verified a change or your claims rest on tool results, state the evidence "
            "(and any gaps) in one line; if nothing changed and there is nothing to verify, "
            "skip the verification note."
        )
    lines.append("Never claim verification that was not actually observed in a tool result.")
    return "\n".join(lines[:10])
