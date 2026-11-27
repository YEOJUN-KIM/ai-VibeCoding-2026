# PAPER-SAFETY-EMERGENCY-01 — 지속되는 긴급정지(latch)

상태: 구현·자동 테스트·격리 HTTP/브라우저 검증 완료(아래). 사용자 직접 동작 검증과 운영 적용은 미완료.

## 문제
`POST /auth/logout`이 `engine.stop()`(일반 정지)을 호출하고, 엔진은 `emergency_stopped = emergency`로 덮어써서 긴급정지가 일반 일시정지로 내려갔다. 긴급정지는 메모리 플래그뿐이라 새로고침/재로그인/서버 재시작에서 유지되지 않았다.

## 동작 (승인된 범위)
- 긴급정지는 해당 **PAPER 계좌**의 DB 상태 `paper_account_state.state.emergency`에 저장되는 latch다. 명시적 해제 전까지 유지된다(새로고침, 로그아웃/재로그인, 서버 재시작, 일반 정지, 설정 저장, 모의계좌 전환 왕복, 연습 초기화, 실제 자산 복사 후에도).
- 일반 정지는 latch를 내리지 않는다. 서버 시작은 자동 재개하지 않는다(엔진은 항상 정지 상태로 만들어진다).
- latch 중에는 서버/API에서 자동 시작(`/paper/mvp/start`, `engine.start()`)과 자동 주문(`PaperBroker.submit(source != "MANUAL")`)이 거부된다. UI 비활성화에만 의존하지 않는다.
- 해제: `POST /paper/mvp/emergency-reset` 본문 `{"confirm": true}` — 로그인 + CSRF 필수. 결과는 일반 정지(`stopped`)뿐이며 시작하지 않는다. 시작은 별도 `/paper/mvp/start`. 실행 중이거나 latch가 없으면 409.
- 강제 청산 없음. 수동 주문은 기존 검사·LIVE 잠금·PIN·CSRF·계좌 제약 그대로이며 latch의 영향을 받지 않는다. 보유분·lot·현금·주문 기록은 보존된다.
- UI: 영구 긴급정지 표시, 시작 버튼 비활성, `긴급정지 해제` 버튼(확인 대화상자, 해제는 시작이 아님을 안내).

## 범위
latch는 **계좌(paper_account_state 행) 단위**다. 한 사용자의 LIVE_COPY/EXPERIMENT 두 모의계좌는 각각 별도 latch를 가진다. 긴급정지 API는 선택된 계좌의 엔진만 멈춘다(기존 동작과 동일). 로그아웃은 기존대로 현재 엔진만 일반 정지한다.

## 저장 / 마이그레이션 안전성
- 새 테이블·컬럼·파괴적 마이그레이션 없음. 기존 JSONB `state`에 최상위 키 `emergency`(`{at, source}`)만 추가한다. `version`은 1 유지.
- 키가 없는 기존 상태는 latch 없음(정상)으로 읽힌다. 값이 비어 있지 않으면 형태와 무관하게 latch로 본다(fail closed).
- 저장은 기존 `@persisted`(커밋 후에만 성공, 실패 시 메모리 롤백) 경로를 쓴다. 롤백 시 이전 코드가 이 키를 무시하고 다시 저장하면 키가 사라질 수 있다(구버전으로 되돌릴 때만 해당).

## 동시성 / 경쟁 / fail-closed
- `latch_emergency`는 broker 락 안에서 메모리 차단을 먼저 세운 뒤 저장한다. `submit`은 같은 락에서 latch를 확인하므로 latch 반환 이후 **새로 시작되는** 자동 주문은 어떤 스레드에서도 성립하지 않는다(테스트: 다중 스레드). 엔진은 latch 후 루프 task를 취소한다.
- 이미 진행 중이던 체결을 취소한다고 약속하지 않는다. 단일 이벤트 루프에서 `step`은 동기 실행이라 정지 전 마지막 주문은 완료될 수 있다.
- 저장 실패: 자동매매는 정지·메모리 차단 상태를 유지하고(`emergency_unsaved`) API는 **503**을 반환한다(성공 보고 안 함). 같은 프로세스에서는 재시작되지 않는다. 서버 재시작 후에는 유지되지 않을 수 있으므로 긴급정지를 다시 실행해야 한다(재시도 시 저장). 해제 저장 실패 시 latch는 유지된다.

## 확인한 생명주기 경로
`/paper/mvp/{start,stop,emergency-stop,emergency-reset,settings}`, 레거시 `/paper/strategy/{stop,emergency-stop}`, `/auth/logout`, `/paper/reset`(연습 초기화), `/paper/snapshot/live`, `/paper/accounts/{mode}/select`, 서버 재구성(`initialize`). `/strategy/start`는 SIMPLIFY-02에서 이미 제거되어 자동 시작 경로는 MVP start뿐이다.

## 계약 변경
추가: `POST /paper/mvp/emergency-reset`(본문 `{confirm:true}`), `/paper/mvp/status`에 `emergency` 필드. 동작 변경: 긴급정지 후 start는 409, 긴급정지 저장 실패는 503. OpenAPI 고정 해시(`tests/test_architecture_regression.py`)를 이에 맞춰 갱신했다.

## 한계
기존 문구대로 긴급정지의 실제 보장 범위는 일시정지와 같고 강제 매도는 없다. 여러 서버 프로세스가 같은 DB를 쓰는 경우 다른 프로세스 메모리의 엔진은 재구성 전까지 latch를 모를 수 있다(단일 프로세스 가정). 실데이터·LIVE 경로 검증은 하지 않았다.
