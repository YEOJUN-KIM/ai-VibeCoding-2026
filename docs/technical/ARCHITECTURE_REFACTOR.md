# 백엔드 구조 리팩터링 보고서

기준일: 2026-10-16. 기능 추가 없이 책임과 의존 방향을 정리했습니다. 실제 계좌 주문 전송은 테스트하지 않았으며, 주문 경로 검증은 mock 브로커와 저장소로 수행했습니다.

## 1. 기존 구조 분석

- `main.py` 한 파일에 89개 HTTP 경로와 계좌 생성, LIVE 사전 검사·전송·취소·대조, PAPER 계좌 전환, 연결 변경, 장기 분석 반복 작업, 시작·종료, 미들웨어가 함께 있었습니다. 도메인 기능이 앱 진입점에 의존하고 테스트도 이 파일의 전역 변수를 교체했습니다.
- `strategy.py`의 실행 클래스가 polling, 완료 봉 관리, 이동평균 신호·확인 봉 상태, 수량 산정, 손절·익절·보유기간, 주문과 ML 기록까지 담당했습니다. 새 신호를 넣으려면 실행 메서드 자체를 수정해야 했습니다.
- `TossClient` 한 클래스가 인증·요청 제한·주문 재시도 정책, 계좌·자산, 종목 검색·순위·차트·시세를 모두 구현했습니다. 캐시와 잠금을 공유하므로 객체를 무작정 나누면 기존 동시성 동작을 깨뜨릴 수 있었습니다.
- PAPER와 LIVE의 투자 금액 계산이 중복되지만 정책 적용 방식은 다릅니다. PAPER는 체결 비용을 포함해 차단하며, LIVE 수동 주문의 투자 정책 한도는 경고와 명시적 동의로 처리합니다. LIVE PIN·현금·보유 수량은 계속 강제 검사해야 합니다.
- DB 접근은 `live_orders.py`, `live_strategies.py`, `long_term_repository.py`, `favorites.py`, `asset_history.py` 등에 이미 기능별로 나뉘어 있었습니다. 이를 같은 인터페이스의 다른 폴더로 옮기는 이점은 작았습니다.
- ML은 수집·판단 저장·백테스트·품질·거시 지표·워커로, 프론트엔드는 페이지와 공통 기능으로 나뉘어 있어 이번 변경 대상에서 제외했습니다. `models.py`도 외부 계약과 여러 import의 중심이므로 유지했습니다.

## 2. 실제 변경한 구조와 새 파일

아래 트리에서 `main.py`, `strategy.py`, `toss.py`는 기존 파일이고, 나머지는 새 파일입니다. `risk.py`는 공통 계산을 호출하도록 일부 수정했습니다.

```text
auto_trader/
├─ main.py
├─ runtime.py
├─ lifecycle.py
├─ middleware.py
├─ risk_policy.py
├─ api/
│  ├─ __init__.py
│  ├─ pages.py
│  ├─ auth.py
│  ├─ settings.py
│  ├─ ml.py
│  ├─ live.py
│  ├─ market.py
│  ├─ research.py
│  └─ paper.py
├─ services/
│  ├─ __init__.py
│  ├─ live_order_service.py
│  ├─ paper_workspace_service.py
│  ├─ connection_service.py
│  ├─ readiness.py
│  └─ research.py
├─ strategy.py
├─ trading/
│  ├─ __init__.py
│  ├─ engine.py
│  ├─ sizing.py
│  ├─ exits.py
│  └─ strategies/
│     ├─ __init__.py
│     ├─ base.py
│     └─ moving_average.py
├─ toss.py
└─ brokers/
   ├─ __init__.py
   └─ toss/
      ├─ __init__.py
      ├─ client.py
      ├─ transport.py
      ├─ accounts.py
      ├─ orders.py
      ├─ market_data.py
      └─ types.py
```

추가한 검증 파일은 `tests/test_architecture_regression.py`, `tests/test_live_order_service.py`, `tests/test_application_composition.py`, `tests/refactor_scenarios.py`, `tests/fixtures/api_before_refactor.json`, `tests/fixtures/paper_engine_before_refactor.json`입니다. 문서는 이 보고서와 `PROJECT_STRUCTURE.md`를 정리했습니다.

## 3. 이동한 주요 함수·클래스

| 기존 위치 | 새 위치와 책임 |
|---|---|
| `main.py`의 HTTP 함수 | `api/` 도메인 라우터. URL·모델·상태 코드·인증 의존성 유지 |
| `_preview_live_order`, `submit_live_real_order`, `confirm_live_dry_run_order`, `cancel_live_real_order`, `reconcile_saved_real_orders` | `services/live_order_service.py` |
| `paper_workspace`, `paper_management`, `select_paper_account`, `snapshot_live_account`, `select_paper_strategy` | `services/paper_workspace_service.py` |
| `save_settings_connection` | `services/connection_service.py` |
| `_startup_readiness`와 준비 상태 캐시 | `services/readiness.py` |
| 장기 후보·관찰 목록 분석, 일일 워커, `build_and_save_long_term_analysis`와 작업 상태 | `services/research.py` |
| `lifespan`, 보안·사용자 범위 미들웨어 | `lifecycle.py`, `middleware.py` |
| 의존성 생성, 계좌 factory, PAPER 변경 직렬화, 전략 설정 복구 | `runtime.py` |
| `MovingAverageEngine`의 실행 책임 | `trading/engine.py`의 `TradingEngine`. 기존 이름은 alias로 유지 |
| 이동평균 진입·확인·하향 교차 청산 신호 | `MovingAverageStrategy` |
| `_buy_quantity`, `_exit_reason`, `_manage_exits`의 계산·관리 본문 | `trading/sizing.py`, `trading/exits.py`. 기존 엔진 메서드는 위임 |
| `TossClient`의 통신·계좌·주문·시장 메서드 | `brokers/toss/`의 책임별 클래스. facade가 공유 캐시·잠금을 소유 |

## 4. 통합·제거한 코드와 의존 방향

진입점의 기존 구현을 새 모듈로 이동하고 중복 원본은 제거했습니다. `main.py`는 앱 생성·정적 파일·미들웨어·라우터 등록만 담당합니다. 주문 금액·종목별 금액·전체 투자 금액·현금 비율 계산은 `RiskPolicy`로 공통화했습니다. 주문·전략 기능을 삭제하지 않았습니다.

의존 방향은 앱 조립 → 라우터 → 서비스 → 기존 저장소/브로커이며, 서비스와 라우터가 사용자 범위 의존성을 `runtime`에서 참조합니다. 서비스가 `main.py`나 라우터를 import하지 않습니다. 시작 시 필요한 PAPER 계좌 선택도 HTTP 함수가 아닌 서비스에 위임합니다.

Toss 내부는 작은 책임별 메서드 클래스를 facade가 상속하는 방식입니다. 각 클래스는 별도 세션이나 캐시를 만들지 않으며 모든 메서드가 기존 facade 상태를 사용합니다. 기존 호출부의 `TossClient(...)`, `.accounts()`, `.portfolio()`, `.create_limit_order(...)` 등의 호출 방식은 같습니다.

## 5. 기존 동작을 유지한 기준

- 89개 경로와 전체 OpenAPI 정의를 변경 전 스냅샷과 비교했습니다. 내부 `Request` 주입 추가는 외부 요청 필드를 추가하지 않습니다.
- 실제 주문의 안전 잠금 → 지원 방식·정수 수량 → PIN 포함 사전 검사 → 경고 동의 → DB 멱등성 예약 → Toss 전송 → 상세 조회 → DB 결과 저장 순서를 유지했습니다.
- 결과 불명 전송은 `UNKNOWN`·`NEEDS_REVIEW`로 저장합니다. 재전송하지 않으며, 상세 조회 실패는 접수 결과를 `SUBMITTED`로 유지합니다. 취소 실패·대조 처리도 원래 흐름입니다.
- Toss 메서드 45개는 생성자·인증·재시도·주문 본문을 포함해 이동 전후 AST가 일치함을 별도로 확인했습니다. LIVE 실제 전송·dry-run·취소·대조 본문도 의존성 참조 접두사 외에는 같음을 확인했습니다.
- PAPER 수량 계산, 슬리피지·수수료 반올림, 청산 순서, 손절·익절·보유기간·cooldown, 관리 lot의 저장 문맥과 ML 기록 필드를 유지했습니다. 이동평균 동률은 기존처럼 `BELOW`이며 확인 봉과 가격 추세 조건도 유지했습니다.
- 사용자별 `ContextVar`·`ScopedProxy`와 계좌 변경 잠금을 유지했습니다. PAPER 계좌 전환·연결 변경 서비스에 잠금이 적용되므로 HTTP 외 호출에도 같은 직렬화가 적용됩니다.
- 리스크 기본값과 환경변수, `settings.py`, `models.py`, `schema.sql`, ML 구현과 정적 자산은 변경하지 않았습니다. DB migration을 추가하지 않았습니다.

일일 손실·주문 횟수 제한을 LIVE에 새로 적용하지 않았습니다. LIVE와 PAPER의 강제 차단/경고 차이, 체결 비용 포함 여부, 현금 0·총자산 0 처리도 그대로입니다. 이는 통합 누락이 아니라 기존 동작을 지키기 위한 경계입니다.

## 6. 검증 결과

- 변경 전: 기존 Python 테스트 **287개 통과**.
- 라우터·서비스·Toss 분리 후: 기존 Python 테스트 **287개 통과**.
- 추가 회귀 검증을 포함한 최종 Python 테스트: **302개 통과**.
- 새 회귀 기준은 리팩터링 전 소스로 생성했습니다. 4개 가격 재생 시나리오의 9개 주문, 매 시점 계좌·전략 스냅샷과 ML 기록을 비교합니다. 무작위 ID·모델 기본 생성 시각은 제외하며 봉·판단 시각, 비용과 손익은 비교합니다. 전체 시점·ML 기록은 해시로, 주문과 최종 상태는 읽을 수 있는 데이터로 보관합니다.
- 추가 전략 주입, 신호 경계, 리스크 경계, LIVE 전송 순서·중복 방지·결과 불명·취소, FastAPI 인증·미들웨어·시작·종료 검증 통과. LIVE 브로커와 저장 동작은 mock으로 검사했습니다.
- Python 모듈 컴파일과 앱 import, `git diff --check` 확인.
- 프론트엔드 Node 테스트: **22개 모두 통과**. 변경 전에도 실패하던 `test_watchlist.js`의 빈 목록 문구 기대값을 현재 화면 안내 문구와 일치하도록 수정한 뒤 전체 JS 테스트를 다시 실행했습니다. `static/long-term.js`의 화면 구현은 유지했습니다.

실행 명령:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m compileall -q auto_trader
$jsTests = @(Get-ChildItem tests -Filter '*.js' | ForEach-Object FullName)
node --test @jsTests
git diff --check
```

DB 테스트는 기존 방식대로 고유 테스트 스키마를 만들고 정리합니다. Windows 제한 실행 환경에서는 PostgreSQL 접근 및 asyncio 내부 소켓 생성이 차단되어, 전체 테스트는 해당 제한 밖에서 실행했습니다. 실제 계좌 주문을 보내거나 자동매매를 시작하지 않았습니다.

## 7. 남아 있는 구조적 문제

- `runtime.py`는 모듈 단위 의존성을 조립합니다. 사용자 상태는 분리되지만, 한 프로세스 안에서 완전히 독립된 앱 여러 개를 만드는 factory 구조는 아직 아닙니다.
- API 모델과 일부 서비스 오류가 FastAPI에 의존합니다. HTTP 상태·응답 호환성을 지키기 위해 이번에 별도 도메인 오류 체계를 도입하지 않았습니다.
- `TradingEngine`은 현재 PAPER의 관리 lot·성과 API와 기존 설정·판단 스키마를 사용합니다. 실제 LIVE 자동매매는 기존처럼 제공하지 않습니다. 새 브로커를 연결할 때는 필요한 동작이 확인된 후 adapter/Protocol을 정해야 합니다.
- 신호는 `TradingStrategy.generate_signal`과 `generate_exit_signal`로 교체할 수 있습니다. 현재 입력·저장 문맥은 기존 완료 봉·이동평균 설정을 중심으로 하므로, ML 고유 입력이나 전략별 영구 저장 문맥이 필요해지면 별도 설계가 필요합니다.
- Toss 시장 데이터 모듈은 여전히 큽니다. 검색·차트의 캐시 수명이나 구현을 독립적으로 바꿀 필요가 생기면 그 단위로 추가 분리할 수 있습니다.
- 기존 기능별 DB 모듈을 유지했으며 일부 단순 조회는 runtime·서비스에 남았습니다. 의미 없는 repository wrapper와 전체 모델 분리는 추가하지 않았습니다.

## 8. 다음 개발 전에 필요한 작업

현재 이동평균/PAPER·조회·수동 주문 기능을 확장하기 위해 전면 재리팩터링할 필요는 없습니다. 새 신호 구현은 `TradingStrategy`에 주입하고, HTTP 기능은 해당 라우터·서비스에 추가하면 됩니다. ML 실제 연결 시에는 입력 시점·판단 저장 계약을, 추가 브로커나 LIVE 자동매매 도입 시에는 PAPER 전용 계좌 API와 저장 lot 문맥을 먼저 설계하고 검증해야 합니다. 새 전략·모델·브로커 구현은 이번에 추가하지 않았습니다.

## 9. 자체 diff 검토

전체 파일 이동과 남은 원본 삭제, import·mock 대상 변경을 중심으로 확인했습니다. 기존 동작 검증은 유지하고 소유 모듈을 바꿨으며, 오래된 빈 목록 문구 기대값은 현재 화면 문구로 수정했습니다. 거래 조건·요청 payload·수치·SQL schema·환경변수·프론트엔드 구현을 바꾸는 변경은 포함하지 않았습니다. 보호 조건과 결과 불명 처리의 원본 AST 비교, OpenAPI 스냅샷, 원본 엔진 재생 기준으로 구조 변경이 기존 기능을 보존함을 확인했습니다.
