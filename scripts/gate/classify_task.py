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


# The user forbids running checks this turn ("테스트는 돌리지 마", "don't run any
# commands, just edit the file"). The Stop gate then warns instead of blocking:
# a block told the model to run what the user had just forbidden, and in the
# 2026-10-26 smoke test cost two wasted turns on a model that obeyed the user.
# ponytail: high-precision, low-recall regex. Both misreads are cheap by design —
# a false ban is only a hint the model can overrule (context_for_mode), and a
# missed ban meets a block reason that itself accepts "the user said not to run"
# (verify_state). So only a bare order counts, and the filters in
# forbids_verification drop whole classes (alternatives, questions, quotes,
# complaints, specified behavior) rather than single phrasings. Known misses:
# "테스트 돌릴 필요 없어", "no need to run the tests", "검증하지 마" (검증 is
# usually input validation: "프론트에서 검증하지 말고 서버에서").
_KO_STOP = r"마(?:세요|십시오|시고|요|라)?(?![가-힣])|말고|말\s*것|말아"
_KO_BAN = r"(?:지\s*(?:" + _KO_STOP + r")|면\s*안\s*(?:돼|됩|된다|되(?!는)))"
_EN_NOT = r"(?:don['’]?t|do not|never|without)"
_EN_OBJ = (r"(?:the\s+|any\s+)?(?:(?:unit|integration|e2e|shell)\s+)?"
           r"(?:tests?|commands?|anything|checks?|pytest|npm test|them)\b")
FORBID_VERIFY_RE = re.compile(
    # A qualified rule ("never run anything destructive", "commands that modify
    # git", "tests in parallel", "again", "as root") still allows a check.
    r"(?i)\b" + _EN_NOT + r"\s+(?:run(?:ning)?|execut\w*|bother\s+running)\s+" + _EN_OBJ +
    r"(?!\s+(?:destructive|dangerous|risky|irreversible|that|which|without|against|again|with|as|in\s+parallel|on)\b)|"
    r"\b(?:don['’]?t|do not)\s+test\s+(?:it|this|that|anything)\b|"
    r"\bskip\s+(?:running\s+)?(?:the\s+|any\s+)?(?:tests?|testing)\b|"
    # No 않고 form: "테스트 돌리지 않고 끝내지 마" is a demand, "…않고 커밋했어?" a complaint.
    r"(?:테스트|명령|커맨드|pytest|\btests?(?![a-z]))[^\n.?!]{0,15}?(?:돌리|실행\s*(?:은|는|도)?\s*하)" + _KO_BAN + "|"
    r"(?:테스트|명령|커맨드)\s*(?:은|는|을|를|도)?\s*하" + _KO_BAN + "|"
    r"테스트\s*(?:은|는|를|도)?\s*(?:단계\s*(?:은|는|를)?\s*)?"
    r"(?:(?:생략|스킵|skip)\s*(?:해|하고|하세요)|건너뛰(?:어|고|세요)(?!\s*있))|"
    r"(?:테스트|명령)\s*(?:실행\s*)?금지(?!\s*(?:목록|규칙|리스트))|"
    # A ban verb is required: "왜 아무 명령도 실행 안 했어?" is a complaint and
    # "아무것도 실행이 안 돼" a bug report. "파일만 수정해" is scope, not a ban.
    r"(?:아무|어떤)\s*(?:명령|커맨드)도\s*(?:실행하|돌리|쓰)" + _KO_BAN + "|"
    r"아무\s*것도\s*(?:실행하|돌리)" + _KO_BAN
)
# A negation right before skip/without makes a demand: "don't skip the tests",
# "don't finish without running the tests". Adjacent only: "No worries, skip the tests".
_NEG_BEFORE_RE = re.compile(r"(?i)\b(?:don['’]?t|do not|never|not|\w+n['’]t)\s+(?:\w+\s+){0,2}$")
# An English order sits at the head of a clause. "Configure pytest to skip tests",
# "You never run the tests", "devs just don't run them" describe, not order.
_IMPERATIVE_HEAD_RE = re.compile(
    r"(?i)(?:^|[,;:.!?\n—-]\s*|\b(?:and|but|please|pls|just|so|then|ok|okay|also|now)\s+)$")
# "같은 명령을 다시 실행하지 마세요" bans a repeat, not checking (seen in a real prompt).
_KO_REPEAT_RE = re.compile(r"같은|다시|\b또\b")
# "위험한 명령은…" / "배포 명령은…" / "docker 명령은…" / "운영 서버에서 명령…" ban one
# kind or place of command, not checks.
_KO_QUALIFIED_RE = re.compile(r"(?:[가-힣]+(?:인|한|운|는|에서)|배포|마이그레이션|삭제|빌드|설치|셸|쉘|시스템|외부|[A-Za-z][\w-]*)\s*$")
# A KO subject before the noun describes something else doing it: "CI가 테스트
# 건너뛰고 초록불 띄워", "make check가 테스트는 실행하지 말고 린트만 돌리는 것 같아".
_KO_SUBJECT_BEFORE_RE = re.compile(r"[가-힣A-Za-z]+(?:가|이)\s*$")
# Specified behavior, not an order: "In dry-run mode, don't execute commands",
# "플래그가 있으면 명령 실행하지 말고", "…린트만 하게 설정해줘", "…돌게 해줘".
_CONDITION_RE = re.compile(r"(?i)dry[- ]?run|\bmode\b|모드|\bwhen\b|\bif\b|일\s*때|경우|면\s*$")
_MODE_RE = re.compile(r"(?i)dry[- ]?run|\bmode\b|모드")
_BEHAVIOR_AFTER_RE = re.compile(r"도록|하게|게\s*(?:해|바꿔|설정|만들|수정)")
_CONFIG_RE = re.compile(r"(?i)설정|config|\bya?ml\b|\bini\b|hook|훅|marker|work-?flow|pipeline|파이프라인")
# A question without "?": "테스트 돌리면 안 되나요", "how do I build without running tests".
_QUESTION_RE = re.compile(
    r"(?i)\b(?:how|why|what|is it|can i|should i|is that)\b|왜|어떻게|이유|되나요|될까|할까|뭐(?:야|였)")
# Reported or quoted: "…하라고 한 건", "'테스트 돌리지 마세요' 라는 문구".
_REPORTED_AFTER_RE = re.compile(r"^[^.!?\n]{0,6}?(?:라고|라는|란\s)")
_QUOTE_RE = re.compile(r"[\"“”]|(?<![A-Za-z])['‘’]|['‘’](?![A-Za-z])")
# Another run asked for elsewhere: "로컬에서 테스트 돌리지 말고 CI에서 돌려줘",
# "don't run pytest directly, use make test", "just run the unit tests, don't run anything else".
_ALTERNATIVE_RE = re.compile(
    r"(?i)돌려|실행해|돌리고|돌게|돌려도|\brun\b|\buse\b|\binstead\b|"
    r"\b(?:only|just)\s+(?:the\s+)?(?:\w+\s+)?(?:tests?|ones)\b")
# "Don't run tests. Don't run the linter." — a second ban is not an alternative run.
_NEGATED_RUN_RE = re.compile(r"(?i)\b(?:don['’]?t|do not|never|not)\s+run\b")
# Past action, a complaint: "committed without running the tests".
_PAST_BEFORE_RE = re.compile(r"(?i)\b\w+ed\s+(?:\w+\s+)?$")
_SENTENCE_END_RE = re.compile(r"[^.!?\n]*([.!?\n]|$)")
# A follow-up that asks for a check ends the prohibition instead of inheriting it.
# Imperatives only: "확인했어, 계속", "테스트 돌려봤는데 통과했어, 계속" are reports.
_ASKS_CHECK_RE = re.compile(
    r"(?i)\b(?:re-?)?run\s+(?:the\s+|a\s+|all\s+)?(?:tests?|checks?|it|them|pytest)\b|"
    r"\b(?:test|verify|check)\s+(?:it|this|that|them|again)\b|"
    r"\b(?:now|please|then|and)\s+(?:test|verify|check)\b(?!\s+out)|"
    r"테스트\s*(?:를|도)?\s*(?:돌려(?!\s*봤)|해\s*(?:줘|봐|주)|실행해|진행해)|검증\s*해|"
    r"돌려\s*(?:봐|줘|보)|실행해\s*(?:봐|줘|주)|확인해\s*(?:줘|봐|주)"
)


def _is_order(text: str, m: re.Match) -> bool:
    word = m.group(0).lower()
    before, after = text[:m.start()], text[m.end():]
    s_before = re.split(r"[.!?\n]", before)[-1]
    s_after_m = _SENTENCE_END_RE.match(after)
    s_after = s_after_m.group(0)
    return not (
        (word.startswith(("skip", "without")) and _NEG_BEFORE_RE.search(before))
        or (word.startswith(("skip", "don", "do not", "never")) and not _IMPERATIVE_HEAD_RE.search(before))
        or (word.startswith("without") and _PAST_BEFORE_RE.search(before))
        or _KO_REPEAT_RE.search(text[max(0, m.start() - 4):m.end()])
        or (word.startswith(("명령", "커맨드")) and _KO_QUALIFIED_RE.search(before))
        or _CONDITION_RE.search(s_before) or _MODE_RE.search(word)
        or _BEHAVIOR_AFTER_RE.search(s_after) or _CONFIG_RE.search(s_before + word + s_after)
        or (re.match(r"[가-힣]", word) and _KO_SUBJECT_BEFORE_RE.search(before))
        or s_after_m.group(1) == "?" or _QUESTION_RE.search(s_before + word + s_after)
        or _REPORTED_AFTER_RE.search(after)
        or len(_QUOTE_RE.findall(before)) % 2 == 1
        or _ALTERNATIVE_RE.search(_NEGATED_RUN_RE.sub("", before + " " + after))
    )


def forbids_verification(prompt: str) -> bool:
    text = prompt or ""
    return any(_is_order(text, m) for m in FORBID_VERIFY_RE.finditer(text))


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


def context_for_mode(mode: str, risk_flags: list[str], verify_forbidden: bool = False) -> str:
    lines = [f"fablize gate — task mode: {mode}."]
    if risk_flags:
        lines.append("Risk flags: " + ", ".join(risk_flags) + ".")
    if verify_forbidden:
        # A hint, not an order: the matcher is a regex and the model sees the real
        # prompt. A misread then costs only the Stop block, never a skipped check.
        lines.append("fablize read the user as forbidding tests/commands for this task. If that is "
                     "what they meant, do not run them and say plainly that the change is unverified "
                     "(the gate will not block). If they did not, verify changed behavior as usual.")
    if mode == "quick":
        lines.append("Keep it concise; no forced verification.")
    elif verify_forbidden:
        pass  # the normal/deep "run a check" line would contradict the user's ban
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
