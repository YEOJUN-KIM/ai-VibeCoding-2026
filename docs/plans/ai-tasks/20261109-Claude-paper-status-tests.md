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
- 이것이 의도된 안전 동작이라고 확정하지 않는다. (3차 후속에서 사용자가 Codex에 충돌 안내 분기를 추가하기로 승인했고, 테스트는 그 계약을 고정하도록 강화되었다. 아래 "3차 후속" 참고.)
- 추가 정정: `/strategy/status`는 동기 `def`(스레드풀)이고 `status()`가 `running`(engine.py:652)과 `emergency_stopped`(:653)를 락 없이 따로 읽는다. 변경 쪽 API(start/stop/emergency-stop)는 `_serialize_paper_change` 락이 있지만 조회는 락을 공유하지 않으므로 일시적으로 섞인 응답 가능성은 코드만으로 배제할 수 없다. 발생 여부는 미검증이다.

### 기타 관찰 (정정됨)

- 최초 기록은 "`data_message`가 비어 있는 running 상태에서 가이드는 '정상 감시 중', 요약 칩은 '시세 수신 대기'"라고 적었다. 이후 정적 조사에서 backend가 `data_message`를 빈 문자열로 대입하는 경로와 기본값이 없음을 확인했다. 정확히 빈 메시지는 합성 입력 방어 항목이며 운영 재현 사례로 쓰지 않는다.
- 더 현실적인 경로는 `configure()`(engine.py:617–619)와 `set_management_scope()`(:541–543)가 비어 있지 않은 준비 문구와 `last_data_at=None`, `last_decision_at=None`을 두는 경우다. `start()`가 이를 지우지 않으므로 첫 poll 전까지 가이드가 '정상 감시 중'이 될 수 있다. 이 경로는 코드 추적상의 추론이며 실행 재현은 아니다. 재설정 없는 재시작 시 옛 시각·메시지 잔재는 이번 UI 변경으로 해결되지 않는다.

### 3차 후속: Codex UI 계약 반영 (Issue #3 댓글 6730735539, 사용자 명시 승인)

- 승인 범위: 이 두 파일만 수정. `app.js`는 읽기만 하며 수정하지 않는다. 신규 PR 없음, 기존 Draft PR #4에 기록.
- 상호 계약(Codex가 PR #5에 추가할 예정인 UI. **이 브랜치의 app.js에는 아직 없음**):
  1. `!running` 분기 앞에 `running && emergency_stopped`이면 label `긴급 정지 확인 필요`, reason "긴급 정지 표시와 실행 표시가 서로 맞지 않습니다.", next "현재 상태를 다시 조회하고 실행 상태를 확인하세요."를 반환한다. 모든 다른 분기보다 우선한다.
  2. 기존 모든 우선순위 뒤, 정상 반환 직전에 `!last_data_at && !last_decision_at && (!message || 정확히 두 준비 문구 중 하나)`이면 label `시세 대기`를 반환한다. 준비 문구는 "전략 적용 완료 · 시작하면 새 종목의 시세를 조회합니다."와 "관리 범위 적용 완료 · 시작하면 시세를 조회합니다."이다.
- 테스트 변경:
  - 충돌: label은 정확히 `긴급 정지 확인 필요`로 고정하고, 정상 감시·HOLIDAY·MARKET_UNKNOWN·API 한도·신규 매수 제한·0/22+지연·준비 문구보다 우선하는지 확인한다. reason/next는 전체 문장 일치를 피하고 의미(긴급 정지 지시 + 서로 맞지 않음, 다시 조회 + 확인)만 정규식으로 검사한다. 주문 중단·청산 감시·정상 감시·전략 시작 안내·보장 표현이 들어가면 실패하도록 하여 UI가 실제 주문 정지를 보장하는 것처럼 보이지 않게 한다.
  - 정상 정지(`전략 정지`/`긴급 정지`)와 충돌 reason이 다른지 확인한다.
  - 준비: message 생략·null·빈 문자열·정확한 문구 2개가 시각 없이 `시세 대기`가 되고, reason/next가 정상 감시 문구와 다르며 정상 감시·오류 표현을 포함하지 않는지 확인한다. HOLIDAY, MARKET_UNKNOWN, API 한도, 신규 매수 제한, 0/22, 7/22, 22/22, 90초·91초 경계가 기존 우선순위를 유지하는지 확인한다. 다른 메시지(근접 문구), 한 시각만 존재, 두 시각 존재는 준비가 아니다.
  - 렌더: 충돌과 준비 상태에서 `#strategy-pill`, `#paper-operation-label`, 요약 칩, `수신 전/확인 전`, 정상 감시 문구 부재를 가짜 DOM으로 확인한다. 기존 함수 추출 경계 방어, label 기대, 가짜 DOM, escapeHtml 검사는 유지했다.
  - 기존 단언 2건을 새 계약에 맞게 조정: `guide({running:true}).next.includes('추가 확인')`은 메시지 없는 입력이 이제 준비 상태가 되므로 정상 메시지(`OK_MSG`)를 넣도록 바꿨고, 우선순위 표의 `stale check needs last_data_at`에도 같은 이유로 `data_message: OK_MSG`를 추가했다. 두 변경 모두 의도(추가 확인 안내, checked_at만으로는 지연 아님)를 유지한다.
- **중요한 통합 전제:** 위 충돌·준비 단언은 새 UI를 가정한다. 이 브랜치의 현재 `app.js`(기준 main)에는 해당 분기가 없으므로 현재 코드로는 일부 단언이 실패하게 설계되어 있다. 새 UI를 갖춘 Codex 브랜치와 합친 뒤에야 의미가 있다. 이 상태를 "통과"로 주장하지 않으며 실행은 하지 않았다.
- 경로상 위험: 충돌 분기를 추가해도 `step()`/`_buy()`/`_sell()`/`broker.submit`에는 emergency 최종 차단 검사가 없다(주문 차단은 `_run` 루프와 `stop()`의 task 취소에 의존). UI 문구는 주문 정지를 보장한다고 단정하지 않는다. 주문·위험 로직 변경은 이번 범위가 아니다.
- 재시작 시 옛 `last_data_at`·메시지가 남는 문제는 `start()` 초기화(backend)가 필요하며 별도 승인 대상이다. 이번 테스트는 그 문제를 해결하지도 고정하지도 않는다.

### 검증 상태

- 자동 테스트: 미실행(지시상 금지). 단언은 app.js 128–145행과 146–169행 소스를 손으로 추적해 확인했다. 오탈자나 추적 오류가 남아 있을 수 있다.
- 작업 중 Node 설치 확인용으로 `node --version`을 한 번 호출했다. 테스트·앱 코드는 실행하지 않았으나 "node 포함 실행 금지" 지시 문구에 비춰 여기 기록한다.
- 3차 후속에서도 Node·테스트·문법 검사·Docker·브라우저·서버·DB를 실행하지 않았다. 사용한 것은 파일 읽기·편집, git·gh 조회, `git diff --check`, commit·push뿐이다. 새 단언은 현재 app.js와 Codex가 설명한 계약을 손으로 추적했을 뿐이며 문법 오류가 남아 있을 수 있다. Codex 변경과의 통합 실행 검증은 하지 않았다.
- Codex 변경과의 통합 검증: 하지 않음. Codex PR이 label을 바꾸면 이 테스트의 label 단언이 실패할 수 있다.
- 미검증 항목·남은 위험·GPT 검토 사항: 실제 실행 시 통과 여부, Node/ICU 환경에서의 `toLocaleString` 동작(기존 테스트도 의존), running+emergency 모순 상태의 표시 정책, Codex가 label을 바꾸는 경우의 기대값 합의, 교차 리뷰.
