# PAPER-SIMPLIFY-02 — 설정 화면 제거·계정 메뉴·수동 주문 중립 안내·PAPER 설정 단순화

상태: 구현·로컬 자동 테스트 완료(합성 시세·격리 DB). 사용자 동작 검증·운영 적용 전. 원격 push·PR 없음.

## 무엇이 바뀌었나

| 영역 | 이전 | 이후 |
|---|---|---|
| 계정·비밀번호·PIN·증권 연결·로그아웃 | `/settings` 화면 + 사이드바 계정 박스 | **모든 화면 우측 상단 계정 아이콘 메뉴**(`static/account-menu.js`). 기존 API·인증·CSRF·세션·잠금·연결 검증은 그대로 |
| 첫 로그인 후 증권 미연결 | `/settings#account`로 이동 | `/live#account`로 이동해 계정 메뉴의 연결 블록을 연다 |
| 수동 LIVE 주문 사전 검토 | 1회 주문금액·종목별·전체 투자금액·최소 현금비율 경고 4종 + 정책 동의 | 4종 경고·동의 제거. 기존 미리보기/최종 확인창에 **주문 전후 변화**(주문가능금액·보유수량·종목/현금 비중, 매도 시 평균단가 기준 예상손익, 수수료·세금은 "확정 불가" 안내)를 중립 표시 |
| PAPER 투자 설정 | 운용 금액 + 업종 선택 + 손절·익절·최대 보유일 입력 | **운용 금액만**. 시장은 국내 고정. 청산 기준은 FOLIO 기본값(`DEFAULT_EXIT`: 손절 5%·익절 10%·20일) |
| PAPER 계좌 준비 | 구형 `/paper`에서 | `/paper-mvp`의 "가상 모의계좌 준비" 버튼(가상 1,000만원 실험계좌, `POST /paper/accounts/EXPERIMENT/select`) |
| 구형 화면 | `/settings`, `/paper`, 정적 파일 직접 접근 | `/settings`→`/live`, `/paper`→`/paper-mvp`(로그인 시 303; 비로그인은 `/login`). 정적 파일(`/static/settings.html` 등)은 삭제되어 404 |

## 변하지 않은 것 (보호)

- LIVE 실주문 안전장치: PIN 인증, `LIVE_TRADING_ENABLED` 서버 잠금, 중복 방지(`client_order_id`), 일반·DAY 지정가·정수 수량 제한, 원화 주문가능금액·보유수량 검사, 최종 확인(체크박스·"실제 주문" 문구)은 그대로다. 정책(한도) 비교만 제거했고 **잔고·수량 검사는 서버에서 계속 거부**한다.
- PAPER 매수·매도 판단, 체결, 기존 lot의 청산 규칙(매수 당시 값을 lot가 보관), 재시작 복원, `AutoLimits`(운용 금액) 로직 — 변경 없음.
- DB: 테이블·데이터 삭제 없음(`risk_settings`, `live_strategies` 등 유지). 기존 계좌의 저장된 `mvp_state.settings`에 `sectors/stop_pct/...`가 남아 있어도 읽을 때 `budget`만 사용하고 지우지 않는다. 사용자가 이전에 저장한 손절·익절·보유일 값은 **더 이상 새 매수에 쓰이지 않고** FOLIO 기본값이 쓰인다(화면에서 볼 수도 바꿀 수도 없는 값이 거래를 좌우하지 않게 하기 위함). 이미 보유한 lot는 자기 값을 유지한다.
- 공통 `RiskPolicy`(`risk_policy.py`: ML 백테스트·Day01·LIVE 현금비중 계산에서 사용), `RiskManager`와 `risk_settings` 테이블, `live_strategies.py`(DB 계층, 복원 시 사용), 토스 클라이언트의 `strategy_preset_candidates` 메서드는 **삭제하지 않았다**.

## 제거 목록(정리)과 복원

| 제거한 것 | 근거 |
|---|---|
| `static/settings.html`·`settings.js`·`settings-account.js`·`settings-presets.js`·`settings-summary.js` | 화면 제거. 계정 기능은 `account-menu.js`로 이전 |
| `static/index.html`·`app.js`(구형 PAPER) | 소비자는 구형 `/paper`뿐. 계좌 준비는 `/paper-mvp`로 이전 |
| API `/settings/strategies*`(CRUD)·`/settings/strategy-preset*`, `strategy_presets.py` | 소비자는 제거한 설정 화면뿐 |
| API `PUT /risk/settings`, `GET /risk`, `PUT /strategy/settings`(이미 409), `POST /strategy/start`, `POST /paper/strategies/{id}/select` | 설정 화면/구형 PAPER만 호출. `/strategy/start`는 저장 전략 선택을 요구하던 구형 실행 경로 — 제거로 "정책이 구형 경로에서 부활"할 수 없다. MVP는 `/paper/mvp/start`를 사용 |
| 테스트 `test_strategy_presets.py`, `test_settings_presets.js`, `test_settings_summary.js`, `test_paper_records.js`, `test_paper_status.js`, `test_order_pagination.js`(삭제한 파일을 읽던 것) | 대상 코드 삭제 |

**유지한 호환 소비자**: `GET/PUT /settings/account…`, `/settings/connection`(계정 메뉴 사용), `POST /orders`·`GET /orders`·`/account`(UI 없는 PAPER 수동 API, 외부 호환), `POST /paper/snapshot/live`(실제 자산 복사 API, UI 없음 — 후속 결정 필요), `/strategy/stop`·`/strategy/emergency-stop`.

**복원 방법**: 제거 전 소스는 보존 브랜치에 있다. `archive/folio-legacy-risk-settings`(= `8c3d66b`, 한도 로직·구형 화면 전체)와 `preserve/paper-simplify-01-9a4eb0a`(= SIMPLIFY-01 완료 상태, 구형 화면 포함). 예: `git show preserve/paper-simplify-01-9a4eb0a:auto_trader/static/settings.html`. 전체 되돌리기는 새 브랜치에서 이 작업의 커밋을 `git revert`.

## 후속 작업(기록만, 이번에 구현하지 않음)

- FOLIO 자체 판단 고도화, 전체 종목 확대, 업종별 학습(관심 업종은 후보 선정에 한 번도 연결된 적이 없어 입력만 제거했다).
- 사용하지 않게 된 구형 PAPER 전용 CSS(`styles.css`의 `paper-*` 규칙)와 `live_strategies` 모듈·테이블 정리, 실제 자산 복사 기능의 새 화면 편입 여부.
- LIVE 주문의 수수료·세금: 토스 `commissions` 응답을 연결하면 사전 표시가 가능하다(현재는 계약 근거가 없어 "확정 불가"로 표시).

## API 계약 승인 delta 검증 (tests/test_architecture_regression.py)

각 커밋을 추출해 라우트와 OpenAPI 해시를 `tests/fixtures/api_before_refactor.json`(기준선)과 비교했다.

| 커밋 | 라우트 | OpenAPI 해시 | 내용 |
|---|---|---|---|
| 8c3d66b | 기준선과 일치 | 일치 | — |
| 9a4eb0a | 일치 | 불일치 | `MvpSettings`에서 구 위험 한도 필드 제거(승인된 SIMPLIFY-01 변경) |
| 39759a8 | 11개 제거 | 불일치 | 아래 11개 API 제거, `LiveOrderImpactItem`·`LiveOrderPreview.impact` 추가, `MvpSettings`는 운용금액 전용 |

제거된 11개: `POST /paper/strategies/{id}/select`, `GET /risk`, `PUT /risk/settings`, `GET·POST /settings/strategies`, `PUT·DELETE /settings/strategies/{id}`, `GET /settings/strategy-preset-status`, `GET /settings/strategy-presets/{kind}`, `PUT /strategy/settings`, `POST /strategy/start`.
테스트는 기준선 픽스처를 그대로 두고 이 승인된 delta만 명시적으로 빼며, 그 외 라우트·스키마 변화는 계속 실패한다.
