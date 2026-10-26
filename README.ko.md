[English](README.md) | 한국어

# fablize — Opus를 Fable처럼

[![GitHub stars](https://img.shields.io/github/stars/cakel/fablize?style=flat&color=yellow)](https://github.com/cakel/fablize/stargazers)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/licenses/MIT)

Opus(또는 임의 Claude 모델)가 **작업을 끝까지 — 증거와 검증을 거쳐 — 운(運)이 아니라 절차로 완주하도록** 만드는 Claude Code 플러그인.

## 왜 만들었나

Fable 5가 나온 뒤, Fable 5와 Opus 4.8을 같은 과제로 통제 비교했다(A/B 19런 + 실작업 26세션, 도구 호출 약 1,500회). 결과는 이렇다.

- 정답이 있는 닫힌 작업(코드·논리·빌드)에서는 두 모델이 사실상 동률이었다.
- 차이는 정답이 없는 열린 작업에서만 났고, 그 정체는 "발견을 한 단계 더 따라가는 깊이"였다.
- 이 깊이는 **모델 능력**이라 지시문이나 하네스로 옮길 수 없다. 주입 실험으로 확인했다 — Opus는 Fable이 스스로 찾은 결함을 재현하지 못했다.
- 반면 **일하는 절차**(만든 걸 실제로 돌려보기·끝까지 가기·체계적으로 조사하기)는 옮길 수 있다.

fablize는 그 절차를 적용한다. 모델의 천장을 올리지 않고, 모델이 자기 천장까지 가게 하는 것이 목표다. 효과가 측정된 것은 검증 접지뿐이고(upstream A/B), 나머지 구성요소는 효과를 측정하는 중에 적용하고 있다 — [무엇이 들어있나](#무엇이-들어있나)와 [docs/MEASUREMENT_PROTOCOL.md](docs/MEASUREMENT_PROTOCOL.md) 참고.

## 옮겨지는 것과 안 옮겨지는 것

| 특성 | 옮겨지나 | 이유 |
|---|:--:|---|
| 검증 접지 (산출물을 실제로 구동·관측) | ✅ 탑재 | Opus가 "못 해서가 아니라 안 해서" 빠뜨린 절차. 주입하니 렌더 검증 행동이 측정 가능하게 상승 |
| 멀티스토리 완주 + 증거 게이트 | ✅ 탑재 | 절차 — 분해·체크포인트·증거 없는 완료 거부 |
| 체계적 조사 (재현 → 가설 → 인과사슬) | ✅ 탑재 | 절차 — 강한 모델이 이미 하는 것과 동급 |
| 조기 종료 방지 | ⚙️ 선택 | 결정론적 훅 — "하겠다"만 하고 안 하는 것 차단. 함께 배포되지만 직접 등록해야 한다 |
| 정답지 밖 결함 발견 | ❌ 불가 | 능력. 주입 반증됨 — 모델이 찾거나 못 찾거나 |
| 정답 없는 창작 디테일 | ❌ 불가 | 능력. 정답이 없는 곳에서만 드러남 |
| 자발적 전파 깊이 | ❌ 불가 | 능력. 지시한 전파는 옮겨지나 스스로 시작하는 깊이는 안 됨 |

안 옮겨지는 행들은 하네스가 아니라 모델(또는 사람)의 몫이다. 거기 닿으면 fablize는 흉내 내지 않고 에스컬레이션하라고 알린다.

## 무엇이 들어있나

| 구성요소 | 실행 방식 | 효과 |
|---|---|---|
| **검증 접지** — 렌더/실행 산출물(HTML·SVG·게임·차트)은 완료 전에 실제로 구동·관측한다 | 팩, 신호로 라우팅 | 측정됨 (upstream A/B) |
| **멀티스토리 검증 게이트** — `goals.py`가 작업을 분해하고, 마지막 스토리는 `goals.py`가 직접 실행한 검증 명령이 exit 0일 때만 완료된다 | always-on 블록 / 스킬 | 미측정 |
| **조사 프로토콜** — 재현·경쟁 가설·인과사슬 끝까지, 형제 호출부 확인, 회귀 테스트 | 팩, 신호로 라우팅 | 미측정 |
| **작업별 라우터** — 디버깅·렌더 신호에 맞는 팩을 주입한다 | `UserPromptSubmit` 훅, 자동 | 미측정 (HOLD) |
| **관찰 게이트** — 프롬프트를 quick/normal/deep으로 분류하고, deep 턴에서 문서가 아닌 파일을 바꿨는데 성공한 검증이 관찰되지 않으면 **종료(Stop)를 막는다**. normal은 안내만 한다 (프롬프트당 최대 2회, 이후 경고만) | `UserPromptSubmit` / `PostToolUse` / `Stop` 훅, 자동 | 미측정 (HOLD) |
| **복구 가드** — 검증이 실패하면 완료 보고를 하지 말라고 알리고, 같은 종류의 실패가 두 번 나면 조용히 재시도하지 말고 알리라고 한다 | `PostToolUse(Failure)` 훅, 자동 | 미측정 (HOLD) |
| **조기 종료 훅** (`finish-the-work.sh`) — 약속만 하고 끝나는 마지막 메시지를 막는다 | `Stop` 훅, **선택** (셋업이 등록 방법을 안내) | 미측정 |

"미측정"은 적용은 하되 효과가 입증되지 않았다는 뜻이다. 2026-10-24 점검(원인을 심어 둔 버그 저장소 2개, Sonnet·Opus, 스킬 유무, 숨은 테스트)에서는 양쪽 모두 6/6 통과해 차이를 가르지 못했다 — 과제가 너무 쉬웠다. 탑재하지 않은 것: 말투 모방·넓은 추론 전파·리뷰 리콜 스캔. 미결 설계 결정과 문제가 생겼을 때 볼 것: [docs/DECISIONS.md](docs/DECISIONS.md).

> [`fivetaku/fablize`](https://github.com/fivetaku/fablize)(upstream 2.1.1)의 fork이며 자체 수정을 포함합니다. 버전은 `2.1.1+cakel.<n>` 형식입니다. [CHANGELOG](CHANGELOG.md) 참고.

## 설치

```
/plugin marketplace add cakel/fablize
/plugin install fablize@cakel
```

라우터와 관찰 게이트(`UserPromptSubmit`, `PostToolUse`, `PostToolUseFailure`, `Stop` 훅)는 자동 등록된다. 게이트는 턴 종료를 프롬프트당 최대 2회 막을 수 있다([무엇이 들어있나](#무엇이-들어있나) 참고). 조기 종료 훅은 선택 사항이다.

**Windows:** 훅이 `bash`로 실행되므로 Git for Windows(Git Bash)가 설치되어 있어야 한다. Python 3.8 이상을 `python3`, `python`, `py` 순으로 찾는다.

운영 규칙을 컨텍스트에 상주시키는 **always-on**까지 원하면 한 번 실행한다:

```
bash "${CLAUDE_PLUGIN_ROOT}/setup/setup.sh" local    # 또는 global
```

해제: `bash "${CLAUDE_PLUGIN_ROOT}/setup/uninstall.sh" local` (또는 `global`)

> fablize가 도움이 됐다면 레포에 ⭐ 하나가 큰 힘이 됩니다 — 셋업이 딱 한 번 질문으로 물어보고, 명시적으로 동의할 때만 별을 누릅니다.

## 기록하는 데이터

모두 로컬 `~/.fablize/` 아래에 남고(`FABLIZE_DATA`로 위치 변경), 외부로 보내지 않는다.

- `ledgers/<session>.json` — 게이트의 세션별 상태: 작업 모드, 바뀐 파일 종류, 이번 턴에 본 검증 명령.
- `history/events.jsonl` — 훅 결정(라우터 신호, Stop 차단, 한도 경고, 조기 종료 차단)의 추가 전용 로그. 라벨과 매칭된 짧은 단어만 남기고 프롬프트 원문은 남기지 않는다. [docs/DECISIONS.md](docs/DECISIONS.md)의 미결 결정을 판단하는 근거로 쓴다. `FABLIZE_EVENTS=0`으로 끈다.
- 저장소의 `./.fablize/` — `goals.py` 계획과 그 원장(검증 명령 출력 끝부분 포함).

## 동작

- `/fablize` 또는 "끝까지 해줘" 같은 어구로 발동, always-on 설치 시 자동.
- 2+ 스토리 → 분해 + 검증 게이트. 디버깅 → 조사 프로토콜. 렌더 산출물 → 검증 접지. 어려운 작업 → adaptive thinking + `/effort xhigh` 권고.
- 능력 천장에 닿으면 더 강한 모델이나 사람으로 에스컬레이션.

## 정직한 한계

- 모델 능력은 못 올린다. 정답 없는 창작의 완성도·자발적 발견은 닿지 못한다 — 하네스가 아니라 모델 선택의 영역이다.
- 효과 수치는 작은 표본·Claude 단일 가족의 자기 측정(A/B 19런)이다. 방향은 견고하나 소수점은 단정하지 않는다. 이 수치가 다루는 것은 검증 접지뿐이다.
- 관찰 게이트는 "이번 턴에 검증했는가"를 셸 명령을 휴리스틱으로 해석해 판단한다. 가끔 잘못 막거나(추가 턴 1~2회) 잘못 통과시킬 수 있고, 내부 오류가 나면 통과시킨다.
- 조기 종료 훅(등록한 경우)은 평서문 제안("원하시면 보고서를 작성하겠습니다")을 약속으로 오인할 수 있다 — 제안을 물음표로 끝내면 걸리지 않는다.

## 라이선스

MIT — [LICENSE](LICENSE) 참조.
