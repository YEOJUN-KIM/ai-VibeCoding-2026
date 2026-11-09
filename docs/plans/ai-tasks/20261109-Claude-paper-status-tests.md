# 20261109-Claude-paper-status-tests

## 작업 정의

| 항목 | 기록 |
|---|---|
| 고유 작업 ID | 20261109-Claude-paper-status-tests |
| 목표 / 구현 이유 | GitHub Issue #3(PAPER-01)에서 Codex가 PAPER 상태 안내 문구를 다듬기 전에, 현재 label과 상태 판정 우선순위를 합성 데이터 회귀 테스트로 고정한다. 문구 변경이 우선순위를 바꾸지 않았는지 이후 검토할 수 있게 한다. |
| 담당 AI | Claude Code |
| GitHub 저장소 | YEOJUN-KIM/ai-VibeCoding-2026 |
| 브랜치 | claude/paper-status-tests-20261109 |
| Worktree 절대 경로 | C:\Users\User\orca\workspaces\ai-VibeCoding-2026\paper01-claude-tests |
| 기준 커밋 전체 SHA | 7029a108bc0459bcf0e13ddc6aa9dae824baa053 |
| 수정 허용 파일 | tests/test_paper_status.js, docs/plans/ai-tasks/20261109-Claude-paper-status-tests.md |
| 선행 작업 / commit / PR | FOLIO-COLLAB-1.2 도입 PR #1, 안전 테스트 후보 PR #2 (둘 다 main 병합 완료). Issue #3 |
| 공통 파일 담당자 / 작업 순서 | 없음. auto_trader/static/app.js는 Codex 담당(codex/paper-status-guidance-20261109)이며 이 작업은 읽기만 한다. 테스트 파일은 Claude 단독 수정 |
| 완료 조건 | 허용된 두 파일만 변경. 우선순위·경계·reason/next 검증이 정적 추적으로 현재 app.js와 일치. 담당 브랜치 push와 Draft PR 생성. 테스트 실행 결과는 주장하지 않음 |
| 승인된 검증 범위 / 정확한 명령 | 허용 소스와 Git 메타데이터의 정적 검토만. `git status/diff/log/ls-remote/fetch`, `git diff --check`, `gh issue/pr` 조회. 테스트 실행(node 포함) 금지 |
| 실행 환경 격리 확인 | 실행 검증을 하지 않으므로 해당 없음. DB·증권 연결·포트·워커 격리는 미확인이며 접근하지 않음 |
| 미실행 / 금지 작업 | `node tests/test_paper_status.js` 포함 모든 테스트 실행, 서버·DB·증권 API·워커, 비밀정보 열람, 설치, app.js 수정, 다른 Worktree 변경, main 수정·병합 |
| 승인 상태 | 승인 |
| 사용자 승인 근거 | 2026-11-09 사용자 구현 지시: 지정 두 파일 수정, 안전 검사 후 담당 브랜치 commit·push·Draft PR 승인. 교차 리뷰 요청 포함 |
| 진행 상태 | 검토 대기 |

## 착수 확인

- 확인 시각과 로컬 브랜치·HEAD·원격 상태: 2026-11-09. 브랜치 claude/paper-status-tests-20261109, HEAD가 기준 SHA와 일치. `git fetch --prune` 후 원격 main도 같은 SHA.
- 기존 staged·unstaged·미추적 변경 및 타인 작업 여부: 착수 시 변경 없음.
- GitHub 활성 작업 브랜치·열린 PR(Draft 포함): 원격 브랜치는 main, codex/ai-collab-pilot-safe-tests, docs/ai-collab-setup. 열린 PR 없음. Codex 담당 브랜치는 원격에 없고 로컬 Worktree(paper01-codex-ui)에만 있으며 읽기 조회상 변경 없음.
- 관련 작업 기록·변경 예정 파일 대조 결과: Codex 예정 파일은 auto_trader/static/app.js 이고 이 작업과 겹치지 않음. Codex의 작업 기록은 아직 확인되지 않음.
- 중복 수정 가능성 / 확인 불확실 / 중단 사유: 원격에 드러나지 않은 로컬 작업의 부재는 보장하지 않음. 현재까지 중단 사유 없음.

## 범위 변경과 인계

- 변경 시각·내용·승인 근거: 범위 변경 없음.
- 다음 담당자·선행 commit/PR·남은 일: Codex PR과 통합 후 두 변경을 함께 정적 검토. 사용자가 승인한 격리 환경에서 실제 실행 필요. 서로의 PR에 대한 교차 리뷰 요청.

## 결과

- PR 번호·URL / 최종 SHA 확인 위치: PR 본문과 완료 보고에 기록(자기 SHA를 위한 반복 commit 없음).
- 구현 / 자동 테스트 / 실제 동작 / 사용자 확인 / 운영 적용 상태: 구현 완료(테스트 코드 추가) / **자동 테스트 미실행** / 실제 동작 미검증 / 사용자 확인 미확인 / 운영 적용 없음.

### 추가한 회귀 테스트

1. `paperStatusGuide`, `renderDataSummary`, `renderStrategyReturn` 경계 인덱스가 -1이거나 순서가 바뀌면 즉시 실패하는 방어 단언. 기존에는 `slice(-1, ...)`가 조용히 잘못된 코드를 실행할 수 있었음.
2. 합성 시나리오 40여 개의 label 우선순위 표: 정지·긴급 정지 최우선, 운영 상태(HOLIDAY·WEEKEND·MARKET_CLOSED·MARKET_UNKNOWN·OUTSIDE_HOURS)가 메시지보다 우선, 운영 시간 외 메시지, API 요청 한도, 시세 지연, 0/22, 7/22, 21/22, 22/22, 신규 매수 제한 조합.
3. 시세 지연 임계값 경계: 기본 90초 초과, interval_seconds 40과 60일 때 임계값 확대, last_data_at 또는 checked_at 누락.
4. 모든 시나리오에서 label·reason·next가 비어 있지 않은 문자열인지, 원시값(undefined·NaN·null)이나 수익 보장 표현이 없는지, 대기성 label이 오류·실패·장애로 읽히지 않는지 확인.
5. 정지 상태의 next가 전략 미선택, 계좌 미준비, 시작 가능 세 경우에 서로 다른지 확인.
6. `renderDataSummary`에 대기 종목이 있을 때의 출력: 0/22 칩, 3개 초과 시 접기("나머지 N개"), 정지 시 요약 숨김, escapeHtml 적용, 대기 종목 없을 때 안내 문구 없음. 보강한 대역은 `recentSnapshots`와 `decisionDisplay`뿐이며 `decisionDisplay`는 app.js 슬라이스 밖에 있는 함수라서 추가했다.

문구 자체(reason·next 세부 표현)는 Codex가 바꿀 수 있으므로 정확히 일치시키지 않았다. 고정한 것은 label, 우선순위, 신규 매수 제한의 차단 사유(`risk.block_reason`을 그대로 표시), 0/N reason의 의미(전체 종목 수 포함, 대기·기다림 표현, 오류 표현 없음), 일부 종목 reason의 숫자 포함 여부다. 기존 단언은 그대로 두었다.

### 후속 수정 (GPT 교차 검토 반영)

- 계기: PR #4 댓글 issuecomment-6729488296. 최초 commit(958806020414b44d05e4b009d87fd2abed2dd88f)의 `reason === NONE_READY`(0/22 원문 메시지 그대로) 단언이 Codex PR #5(head 0ee560fe7edb8f19c602d497433ea1dce3cada59)의 새 reason "22개 종목 모두 판단에 필요한 시세를 기다리고 있습니다."와 충돌할 것으로 예상됨.
- 수정: 전체 문장 정확일치를 제거하고 `0/22`, `0/10` 두 입력에서 reason이 전체 종목 수를 포함하고 `대기|기다`를 포함하며 `오류|실패|장애`를 포함하지 않는지로 바꿨다. label '시세 대기'는 우선순위 표에서 계속 고정된다. 이 의미 검사는 기준(main)의 원문 메시지 reason과 PR #5 reason 양쪽에 모두 맞도록 작성했다.
- 2차 보완(GPT 확인 반영): 0/N 입력의 label이 '시세 대기'인지 직접 확인하고, reason에 `모두|전체|전부` 또는 원래의 `0/N종목 판단 가능` 표현이 있어야 통과하도록 했다. "22개 중 일부만 대기" 같은 reason은 실패한다. main 원문(`0/22종목 판단 가능 · 대기`)과 PR #5(`22개 종목 모두 ...`)가 모두 통과하는 것은 정적 추적으로 확인했다.
- PR #5 정적 교차 대조(app.js 읽기만, 수정 없음): 모든 label과 분기 순서가 기준과 동일하다. 기존 단언이 보는 `next`의 '전략 시작', '청산 감시', '설정 운영 시간', '추가 확인'과 차단 사유 reason이 PR #5에서도 유지된다. `renderDataSummary`의 `이유 · `, `다음 행동 · ` 접두어는 `E(reason)` 포함 검사를 깨지 않는다. 비어 있음·원시값·수익 보장 표현 검사에 걸리는 새 문구는 없다. `guide=currentGuide`로 바뀐 것은 같은 함수 결과라 출력이 동일하다. 추가 불일치는 발견하지 못했다.
- 한계: 모두 손으로 추적한 정적 결과이며 두 PR을 합친 상태로 실제 실행한 것이 아니다. 이번 수정에서는 앱·테스트 코드와 Node를 실행하지 않았고 `node --version`도 호출하지 않았다(최초 구현 중 1회 호출은 위에 기록). 파일 읽기·편집과 git·gh 조회·commit·push 명령만 사용했다.

### 위험: running:true + emergency_stopped:true

- 현재 동작(정적 추적): `paperStatusGuide`는 `!strategy.running`일 때만 긴급 정지를 반환한다. running이 true이면 emergency_stopped를 보지 않으므로 운영 상태나 메시지에 따라 '정상 감시 중' 등이 표시되고, `renderDataSummary`가 `#strategy-pill`을 이 label로 덮어쓴다.
- 엔진 쪽 근거(auto_trader/trading/engine.py): `start()`가 emergency_stopped를 False로, `stop()`이 running을 False로 만든다. 읽은 범위에서는 두 값이 동시에 true가 되는 경로를 찾지 못했다. 다른 경로(저장된 상태 복원, 서비스 계층)는 확인하지 않았다.
- 이것이 의도된 안전 동작이라고 확정하지 않는다. 테스트는 label을 고정하지 않고 reason/next 형태만 확인한다. 값이 모순되는 상태를 어떻게 보여줄지는 Codex·GPT 검토 대상이다.

### 기타 관찰 (수정하지 않음)

- `data_message`가 비어 있는 running 상태에서 `paperStatusGuide`는 '정상 감시 중'인데, 같은 화면의 요약 칩은 '시세 수신 대기'로 표시된다. 현재 동작이며 테스트로 고정하지 않았다.

### 검증 상태

- 자동 테스트: 미실행(지시상 금지). 단언은 app.js 128–145행과 146–169행 소스를 손으로 추적해 확인했다. 오탈자나 추적 오류가 남아 있을 수 있다.
- 작업 중 Node 설치 확인용으로 `node --version`을 한 번 호출했다. 테스트·앱 코드는 실행하지 않았으나 "node 포함 실행 금지" 지시 문구에 비춰 여기 기록한다.
- Codex 변경과의 통합 검증: 하지 않음. Codex PR이 label을 바꾸면 이 테스트의 label 단언이 실패할 수 있다.
- 미검증 항목·남은 위험·GPT 검토 사항: 실제 실행 시 통과 여부, Node/ICU 환경에서의 `toLocaleString` 동작(기존 테스트도 의존), running+emergency 모순 상태의 표시 정책, Codex가 label을 바꾸는 경우의 기대값 합의, 교차 리뷰.
