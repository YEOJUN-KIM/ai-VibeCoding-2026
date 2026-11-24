# PAPER-SIMPLIFY-01 — 모의매매 투자 한도 단순화와 이전 로직 복원 방법

상태: 구현·로컬 자동 테스트 완료(합성 시세·격리 DB). 사용자 동작 검증·운영 적용 전.

## 무엇이 바뀌었나

모의(PAPER) 자동매매의 **사용자 설정 한도는 "자동매매 운용 금액(원)" 하나**다. 초기 가상 자산(기본 1,000만원)과 운용 금액 예시(300만원 등)는 고정값이 아니다.

| 구분 | 이전 | 이후 |
|---|---|---|
| 수동 PAPER 매수·매도 | 한도 없음(PAPER-SETTINGS-01에서 이미 제거). 잔고·수량·유효성·중복 `request_id` 검사만 | 동일(변경 없음, 테스트로 고정) |
| 자동 PAPER 한도 | 운용 금액 + 종목당·주문당·최대 보유 종목 수·하루 매수 횟수·하루 손실 | **운용 금액만** (`AutoLimits.max_total_investment`) |
| 투자 성향 카드(안정/균형/적극) | 위 한도를 묶은 프리셋 | 제거. 청산 기준(손절·익절·최대 보유일)은 입력란 3개로 유지 |
| 일일 손실 기준선 장치 | `_auto_day`, `checkpoint`, `auto_open`/`auto_last` 기록 | 제거(손실 한도의 부속 장치였음). 이미 저장된 기록은 지우지 않고 읽지도 않음 |
| `/paper` 구형 화면 "투자 한도" 탭 | 저장된 공통 한도 표시 | 제거 |

자동 매수는 `자동매매 보유분(시가) + 이번 매수액(수수료 포함) ≤ 운용 금액`일 때만 허용된다. 운용 금액이 없는 상태에서 `RiskManager.check_buy`에 도달한 PAPER 매수는 거부된다(fail-closed). 잔고 초과·수량 초과·중복 주문·동시성(`paper lock`)·재시작 복원(저장된 AUTO 로트 기준)은 `PaperBroker.submit` 및 기존 장치가 그대로 담당한다.

**의도적으로 바꾸지 않은 것 (실거래 보호)**
- `/settings` 페이지, `/risk`, `/risk/settings`, `risk_settings` 테이블, `RiskManager.settings()/update()`, `RiskPolicy`, `live_order_service`: **LIVE 주문 미리보기 경고가 공유**하므로 코드는 그대로다. 설정 화면의 "투자 한도" 탭에는 "LIVE 주문 미리보기 경고 기준이며 모의매매에는 적용되지 않는다"는 안내만 추가했다. PAPER 경로는 이 값을 더 이상 읽지 않는다(테스트로 확인).
- 설정 페이지 전체 제거 여부는 계정·PIN·증권 연결(LIVE 관련)이 같은 페이지에 있어 **사용자 결정이 필요**하다(보고서 참고).

## 보존·복원

- 보존 브랜치: `archive/folio-legacy-risk-settings` = 작업 기준 커밋 `8c3d66bbd0d08539aa4f4e39cc00524bb371f87c`(PR12 head). 이전 한도 로직·테스트·UI 전체가 그대로 있다. 원격에는 push하지 않았다.
- 복원 위치(보존 브랜치 기준 파일):
  - `auto_trader/risk.py` — `AutoLimits` 전체 필드, `_auto_day`, `checkpoint`, `auto_status`, `_check_auto_buy`, `check_buy`의 저장 한도 분기
  - `auto_trader/paper_mvp.py` — `PRESETS`, `MvpSettings`(per_unit·per_value·max_holdings·daily_loss_pct·daily_buys·profile), `_limits`, `auto_limits`, `day01_config`
  - `auto_trader/paper.py` — `daily_buy_count`, `risk_unrealized`; `auto_trader/trading/engine.py` — `checkpoint` 훅
  - `auto_trader/static/paper-mvp.js`·`scripts/build_paper_mvp_page.py`(→ `paper-mvp.html`), `index.html`/`app.js`의 `renderRisk`·"투자 한도" 탭
  - 테스트: `tests/test_paper_safety02.py`, `tests/test_paper_safety_fix01.py`(한도 관련 케이스), `tests/test_paper_risk.js`, `tests/test_paper_mvp.py`의 한도 케이스
- 복원 방법(예): `git show archive/folio-legacy-risk-settings:auto_trader/risk.py > /tmp/risk_legacy.py` 로 확인 후 필요한 부분만 가져온다. 전체를 되돌리려면 새 브랜치에서 `git checkout archive/folio-legacy-risk-settings -- <경로>` 를 사용한다(현재 브랜치 이력을 덮어쓰지 않도록 항상 새 브랜치에서).
- 저장 데이터: 계좌의 `mvp_state.settings`에 예전 필드(profile, per_unit 등)가 남아 있어도 새 코드는 무시하고 읽으며 다시 쓰지 않는다(삭제 없음). `auto_open`/`auto_last`도 그대로 남는다. 롤백(보존 브랜치로 되돌림) 시 그대로 다시 읽힌다.

## 한계(다음 단계)

- 종목당·주문당 한도가 없으므로 한 번의 매수가 운용 금액 전체를 쓸 수 있다(합성 시험에서 확인). 분산 정책은 다음 단계의 전략 작업에서 정한다.
- Scout/DAY-01 설정 객체는 양수 한도를 요구해, "별도 한도 없음"을 비구속 값(보유 종목 1000, 주문 횟수 1,000,000, 일일 손실 = 총자산)으로 전달한다(`paper_mvp.py` 상단 상수). 실제 시세 연동은 이번 범위가 아니다.
