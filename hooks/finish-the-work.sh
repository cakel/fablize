#!/bin/bash
# finish-the-work.sh — Stop hook
# Detects early termination where the agent only PROMISES work without doing it, and re-engages it.
# Deterministic (regex). Infinite loops are guarded by stop_hook_active.
# stdin: JSON { transcript_path, stop_hook_active, ... }
# stdout: {"decision":"block","reason":"..."} to continue, otherwise empty (exit 0)

set -e
input=$(cat)
PY="$(dirname "$0")/py.sh"

# Loop guard: if this hook already forced one continuation, do not block again.
active=$(printf '%s' "$input" | bash "$PY" -c "import sys,json; print(json.load(sys.stdin).get('stop_hook_active', False))" 2>/dev/null || echo "False")
if [ "$active" = "True" ]; then exit 0; fi

# Current Claude Code puts the final text in the Stop input itself; reading the
# transcript races the write of that very message. Fall back to the transcript.
FTW_LAST=$(printf '%s' "$input" | bash "$PY" -c "import sys,json; print(json.load(sys.stdin).get('last_assistant_message') or '')" 2>/dev/null || echo "")
export FTW_LAST
FTW_SID=$(printf '%s' "$input" | bash "$PY" -c "import sys,json; print(json.load(sys.stdin).get('session_id') or '')" 2>/dev/null || echo "")
export FTW_SID
tpath=$(printf '%s' "$input" | bash "$PY" -c "import sys,json; print(json.load(sys.stdin).get('transcript_path',''))" 2>/dev/null || echo "")
if [ -z "$FTW_LAST" ] && { [ -z "$tpath" ] || [ ! -f "$tpath" ]; }; then exit 0; fi

# Extract the last assistant message's text and whether it ended with a tool call.
bash "$PY" - "$tpath" <<'PY'
import os, sys, json, re, tempfile
from pathlib import Path

path = sys.argv[1]


def transcript_tail(path):
    """(last assistant text, whether it ended with a tool call) from the transcript,
    or None when the path is unusable."""
    # Reject paths outside the temp dir or HOME to block path-traversal via crafted
    # hook input. Done here, not in bash: on Windows the transcript arrives as
    # `C:\Users\...` while bash's $HOME is `/c/Users/...`, so a shell prefix match
    # never matched and the hook silently did nothing on every stop.
    try:
        p = Path(path).resolve()
        roots = [Path.home().resolve(), Path(tempfile.gettempdir()).resolve()]
        # Transcripts live under $CLAUDE_CONFIG_DIR/projects when it is set, and
        # that may be outside HOME (D:\claude-cfg) — the hook went silent there.
        if os.environ.get("CLAUDE_CONFIG_DIR"):
            roots.append(Path(os.environ["CLAUDE_CONFIG_DIR"]).expanduser().resolve())
        if not any(p == r or r in p.parents for r in roots):
            return None
        text, had_tool = "", False
        # encoding= explicitly: the default is cp949 on a Korean Windows host, which
        # raises on the first non-cp949 character.
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                msg = obj.get("message", obj)
                if obj.get("type") == "assistant" or msg.get("role") == "assistant":
                    content = msg.get("content", [])
                    if isinstance(content, list):
                        texts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
                        tools = [b for b in content if isinstance(b, dict) and b.get("type") == "tool_use"]
                        if texts or tools:
                            text, had_tool = "\n".join(texts).strip(), bool(tools)
        return text, had_tool
    except Exception:
        return None


# Current Claude Code passes the final message in the Stop input (FTW_LAST);
# reading the transcript instead races the write of that very message. A final
# message is text by definition — a turn that ends on a tool call does not stop.
last_text = os.environ.get("FTW_LAST", "").strip()
last_had_tool = False
if not last_text:
    tail = transcript_tail(path)
    if tail is None:
        sys.exit(0)
    last_text, last_had_tool = tail

# Ended with a tool call (still working) or with no text -> not an early stop.
if last_had_tool or not last_text:
    sys.exit(0)

# Inspect only the closing paragraph, not the whole report.
tail = last_text[-400:]

# Unfulfilled-promise patterns (English + Korean). Future/intent only; past tense is excluded.
promise = re.search(
    r"\b(I'?ll|I will|let me|next,? I|now I'?ll)\b[^.]{0,60}\b(now|next|then|implement|create|write|add|run|fix|save|build|start|proceed)\b",
    tail, re.IGNORECASE)
# Korean: an action verb in first-person future/intent form ("구현하겠습니다",
# "수정할게요", "고치겠습니다", "돌려볼게요"). Bare "겠습니다" is not enough:
# 알겠습니다 / 감사하겠습니다 / 좋겠습니다 are not promises of work.
if not promise:
    promise = re.search(
        r"(구현|작성|수정|실행|진행|추가|생성|시작|적용|처리|저장|정리|확인|테스트|검증|커밋|분석|조사)"
        r"(을|를)?\s?(하겠|할게|해\s?보겠|해\s?볼게)"
        r"|(고치|고칠|만들|돌리|돌릴|돌려\s?보|바꾸|바꿀|살펴보|살펴볼)(겠|게)",
        tail)

# A legitimate stop that ends by asking the user passes through.
asks_user = re.search(
    # "I'll commit once you approve" is waiting on the user, not a dropped promise;
    # blocking it ordered the model to commit/deploy BEFORE approval.
    r"(\?|shall i|would you like|do you want|let me know|which option|if you want|if you'd like"
    r"|if you prefer|once you (?:confirm|approve|agree)|when you'?re ready|after you (?:confirm|approve)"
    # "…once you give the go-ahead" / "pending your approval" / "until you say so":
    # still waiting on the user. Blocking those pushed a destructive step ahead of consent.
    r"|go[- ]ahead|your (?:approval|confirmation|ok|okay|sign-?off|decision)|until you|awaiting|pending your"
    r"|once you (?:give|say|ok)|after you (?:give|say|ok)|with your (?:ok|approval)"
    r"|승인|허락|결정해\s?주|확인해\s?주|지시해\s?주|오케이|OK\s?주"
    r"|주시면|하시면|확인되면"
    r"|원하시면|원하면|원하신다면|필요하시면|필요하면|필요하다면|필요시|할까요|드릴까요|괜찮을까요"
    r"|알려\s?주세요|말씀해\s?주세요|말씀하시면)",
    tail, re.IGNORECASE)

if promise and not asks_user:
    out = {
        "decision": "block",
        "reason": "Your previous response ended by stating an intent to do work without actually doing it. "
                  "Do that work now with tool calls. End the turn only when the task is complete or you are "
                  "blocked on input that only the user can provide."
    }
    # Same out-of-band decision log as the gate (docs/DECISIONS.md D4). Inline:
    # this hook is copied alone to ~/.fablize/lib/hooks, without scripts/shadow.
    if os.environ.get("FABLIZE_EVENTS") != "0":
        try:
            from datetime import datetime, timezone
            data = Path(os.environ.get("FABLIZE_DATA") or Path.home() / ".fablize").expanduser()
            (data / "history").mkdir(parents=True, exist_ok=True)
            with open(data / "history" / "events.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps({"ts": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
                                    "session_id": os.environ.get("FTW_SID") or "no-session",
                                    "event_type": "ftw_block", "payload": {"matched": promise.group(0)[:60]}},
                                   sort_keys=True) + "\n")
        except Exception:
            pass
    print(json.dumps(out, ensure_ascii=False))
sys.exit(0)
PY
