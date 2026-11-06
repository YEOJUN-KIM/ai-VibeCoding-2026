# 운영 접근 없이 실행할 테스트 후보 — 정적 조사

조사일: 2026-11-06 KST. 기준 main: `8f02cd758c8310335f635db63e5b44956373b066`.
담당: Codex. [작업 기록](../plans/ai-tasks/20261106-Codex-safe-tests.md).

## 결론과 적용 범위

우선 후보는 **Python 2개 파일과 JavaScript 3개 파일**이다. 아래에서 읽은 저장소 코드 경로에서는 운영 DB·증권 API·HTTP 서버·운영 워커에 접근할 필요가 없다. 이는 **실행 없이 소스를 읽고 내린 제한적인 판단**이며 테스트 통과, 실행 환경 격리 또는 무조건적인 안전 보장이 아니다.

이번에는 어떤 테스트·앱 import·서버도 실행하지 않았다. 패키지 설치, DB 연결, 증권 API, 비밀정보 열람, 운영 기록 접근도 하지 않았다. GitHub의 브랜치·PR 조회와 승인된 담당 브랜치 push·Draft PR은 협업 절차 범위이며 증권 API 접근과 구분한다.

`tests/test_*.py` 51개와 `tests/test_*.js` 22개, 총 **73개 테스트 파일**의 import 및 위험 호출 패턴을 조사했다. `tests/refactor_scenarios.py`는 별도 보조 모듈이며 73개에 포함하지 않는다. 테스트 메서드 수가 아니다. 5개 우선 후보는 테스트 본문과 프로젝트 내부 의존 경로를 추가로 읽었다. 나머지 68개는 보류 분류이며 모든 분기·외부 패키지까지 감사했다는 뜻이 아니다.

조사 방법은 파일 목록·텍스트 읽기와 import, DB 연결, 네트워크, 파일 쓰기, 프로세스·비동기 작업 관련 패턴 검색이다. 검색에 안 잡힌 동적 호출까지 안전하다고 간주하지 않는다. 앱 import나 테스트 수집을 통해 확인하지 않았다.

## 우선 후보 5개

모든 경로는 저장소 루트 기준이다. 근거는 위 기준 커밋에 고정되며 이후 코드 변경 시 재검토한다.

| 후보 | 직접 확인한 근거와 내부 의존 경로 | 예상 의존성 | 미확인 위험 |
|---|---|---|---|
| [test_chart_candles.py](../../tests/test_chart_candles.py) 전체 | 1–5행 import, 10행 이후 합성 봉으로 시간별 OHLCV·KST 경계·분봉 유지 검사. `auto_trader.__init__`는 설명과 버전만 선언. `chart_candles.py`는 datetime과 `models.py`만 import하며 입력 목록을 계산. DB·settings·Toss·파일 쓰기 호출 없음 | Python 3.11 이상(`StrEnum`), Pydantic v2. requirements의 선언은 2.13.5. 설치 상태는 미확인 | Pydantic 및 Python 시작 hook의 내부 부수 효과는 감사하지 않음. 기본 Python은 bytecode를 쓸 수 있으므로 후속 실행안은 `-B` 사용 |
| [test_long_term.py](../../tests/test_long_term.py) 전체 | 5–10행은 `long_term`과 `models` import. 13행 이후 직접 만든 기업·봉 모델로 점수·추천 제외·시간 계산. `long_term.py` 3–6행은 datetime·decimal·models만 import. `next_daily_scan_at`은 시각 계산일 뿐 워커를 예약하지 않음 | Python 3.11 이상, Pydantic v2, 표준 라이브러리 | `generated_at`은 현재 시각을 사용하지만 외부 접근은 아님. 라이브러리·시작 hook과 실제 통과 여부 미검증 |
| [test_chart_viewport.js](../../tests/test_chart_viewport.js) 전체 | 1–3행은 Node 내장 fs/vm/assert와 `chart-viewport.js` 읽기. 전체 소스는 차트 구간 계산 함수. 합성 봉과 가짜 document만 사용 | 승인된 Node.js 실행 파일과 내장 모듈, 해당 정적 JS | CWD가 Worktree 루트여야 함. Node 시작 옵션·preload·버전은 미확인. VM은 보안 샌드박스로 보장하지 않음 |
| [test_settings_summary.js](../../tests/test_settings_summary.js) 전체 | 1–14행 내장 모듈과 가짜 document·MutationObserver·strategyPayload 주입. `settings-summary.js` 전체는 문자열·필드 검증과 DOM 변경이며 fetch·DB·주문 실행 없음 | Node.js 내장 fs/vm/assert, 해당 정적 JS | 가짜 DOM 동작만 검증하므로 실제 브라우저 폼·주문 안전성 검증을 대신하지 않음. Node 환경 미확인 |
| [test_market_hours.js](../../tests/test_market_hours.js) 전체 | 17–27행 scope의 `fetch`는 합성 payload 반환 함수, `setInterval`은 callback 보관만 수행. `market-hours.js`의 즉시 `update()`도 이 가짜 fetch를 사용. 28행의 실제 `setImmediate`는 메모리 내 비동기 결과 대기. HTTP 서버·지속 polling 시작 없음 | Node.js 내장 assert/fs/vm, Date·Intl의 한국어/시간대 지원, 해당 정적 JS | 실제 `/market-hours`·브로커 캘린더를 검증하지 않음. Intl/ICU 차이, 향후 scope 변경, Node preload 미확인 |

공통 내부 의존 확인: `auto_trader/models.py`는 표준 라이브러리와 Pydantic 모델·필드 선언이며 자체 settings·DB·통신 import나 파일 입출력 경로를 발견하지 못했다. 설치된 외부 라이브러리의 전체 구현을 검사한 것은 아니다.

### 부수 효과 구분

| 후보 | import/로딩 | DB·증권·서버 | 파일 쓰기 | 워커·타이머 |
|---|---|---|---|---|
| Python 2개 | 패키지 init → 계산 모듈·models → Pydantic | 확인한 코드 경로에 없음 | 테스트 로직에는 없음, `-B`로 bytecode 억제 제안 | 없음 |
| chart_viewport JS | 로컬 정적 파일 읽기·VM 평가 | 없음 | 확인한 로직에 없음 | 없음 |
| settings_summary JS | 로컬 정적 파일 읽기·VM 평가 | 없음 | 확인한 로직에 없음 | 가짜 MutationObserver만 사용 |
| market_hours JS | 로컬 정적 파일 읽기·VM 평가 | 가짜 fetch 사용 | 확인한 로직에 없음 | 가짜 interval, 실제 setImmediate 한 번 대기 |

‘없음’은 읽은 기준 커밋의 저장소 코드 경로에 관한 판단이다. 프로세스 시작 hook, 외부 패키지와 운영체제의 파일·캐시 동작까지 증명하지 않는다.

## 별도 격리가 필요한 테스트

### 실제 DB를 사용하는 파일 16개

이 파일들에는 실제 연결을 통해 임시 스키마를 만들고 초기화·읽기·쓰기를 수행하는 테스트가 포함되어 있다. 일부 클래스가 순수 계산이어도 **파일 전체를 우선 후보로 승인하지 않는다.**

대표 근거: `test_auth.py` 14–16행은 `database.connect`를 보관하고 연결한 뒤 `CREATE SCHEMA`를 실행한다. 27행은 `database.initialize()`, 32–33행은 `DROP SCHEMA ... CASCADE`다. `database.py`의 `connect()`는 설정값으로 실제 psycopg 연결을 연다. `initialize()`는 `schema.sql`을 실행한다. `test_ml_candidate_contract.py` 268행 이후에도 같은 종류의 DB 통합 클래스가 있다.

임시 스키마는 운영 DB와 다른 DB라는 뜻이 아니다. 기존 설정을 사용하면 운영 PostgreSQL에 연결·DDL을 수행할 수 있으므로 별도 테스트 DB·전용 최소 권한 계정·비운영 설정이 확인되기 전 실행하지 않는다.

- `tests/test_auth.py`
- `tests/test_decision_history.py`
- `tests/test_favorites.py`
- `tests/test_live_orders.py`
- `tests/test_live_strategies.py`
- `tests/test_ml_backtest_selection.py`
- `tests/test_ml_candidate_contract.py`
- `tests/test_ml_data_pipeline.py`
- `tests/test_ml_decision_pipeline.py`
- `tests/test_ml_gap_repair.py`
- `tests/test_ml_macro_pipeline.py`
- `tests/test_ml_quality_report.py`
- `tests/test_paper.py`
- `tests/test_strategy_presets.py`
- `tests/test_user_workspaces.py`
- `tests/test_watch_notes.py`

### 파일·모델·프로세스 관련 격리 검토 5개

| 파일 | 실제 확인한 이유 | 다음 검토 조건 |
|---|---|---|
| `test_archive_ml_results.py` | 8–10행 importlib로 archive 스크립트 실행. 15–26행은 generated 아래 폴더·JSON·ZIP 쓰기. 업로드 테스트의 대역 사용만으로 전체 스크립트 경로를 승인하지 않음 | 별도 산출물 공간·실제 OAuth 미사용·모든 업로드 호출 대역과 스크립트 import 부수 효과 확인 |
| `test_launcher.py` | `launcher` import, Popen·서비스 시작을 다루는 분기와 TemporaryDirectory/launch lock 테스트 포함 | 시작·중지·포트 확인·DB·브라우저·subprocess의 모든 대역과 임시 경로 검증 |
| `test_ml_data_readiness.py` | 136행 이후 `.tmp` 생성과 CSV·manifest 등 산출물 생성, 일부 CLI 테스트에서 snapshot을 patch | 운영 데이터·기존 artifacts를 읽지 않는 합성 입력, 출력·CLI·DB import 경로 검토 |
| `test_ml_environment.py` | 고정 `artifacts/ml/generated/test_environment_check.joblib`에 모델 저장 후 cleanup 삭제. verify_environment는 fit·joblib.dump 수행 | 고정 경로 충돌·기존 파일 삭제 위험 제거/격리 승인, ML 패키지 캐시와 파일 쓰기 검토 |
| `test_ml_first_experiment.py` | make_models().fit 실행과 106행 이후 임시 manifest·CSV 쓰기. first_experiment는 database를 import | DB import·증거 자료 읽기·학습·파일 출력·외부 패키지 부수 효과 검토 |

### 설정·통신·앱 조립·비동기 경로의 추가 조사 28개

아래는 **현재 후보에서 제외**한 파일이다. 모두 실제 외부 접근을 한다고 단정하는 분류가 아니다. mock과 메모리 계산을 사용하는 파일도 있으나 전체 호출 경로·import 순서·대역 적용 시점을 더 확인해야 한다.

근거와 위험 경로:

- `settings.py` 94행의 `_load_env_file(PROJECT_ROOT / ".env")`와 171행의 `settings = load_settings()` 때문에 import만 해도 로컬 설정을 읽을 수 있다. 이는 `.env` 내용이 아니라 코드에서 확인한 사실이다.
- `toss.py` → `brokers/toss/client.py` → settings, `paper.py` → database → settings, API/main → runtime 경로를 확인했다. 테스트 본문의 patch는 모듈 최상단 import 이후 적용될 수 있다.
- `test_application_composition.py` 40–61행은 DB 초기화·워커를 대역으로 바꾸고 lifespan을 실행한다. 소켓 서버 실행과 같지는 않지만 앱 조립·초기화 경로이므로 우선 후보에서 제외한다. 실제 `lifecycle.py`는 DB 초기화·주문 대조·수집 워커 시작 경로를 가진다.
- `test_strategy_engine.py`는 PaperBroker를 사용한다. `paper.py` 256행 이후는 journal_path가 있으면 디렉터리를 만들고 append한다. 메모리 PAPER라고 모든 테스트가 파일 쓰기 없이 끝난다고 가정하지 않는다.
- `test_ml_collection_worker.py`는 disabled worker의 start와 run_once 등을 검사한다. 실제 worker 시작/운영 수집과 대역 호출을 분리해 재검토한다.
- `test_quote_stream.py`는 hub.run을 idle coroutine으로 대체하지만 subscribe 시 asyncio task를 만든다. websockets 의존성·모든 연결 대체·task 종료를 후속 확인한다.
- `test_market_hours.py`처럼 순수 함수 중심인 추가 후보도 있다. 이번 5개와 같은 깊이로 외부 의존·전체 호출을 검토하지 않았으므로 보류한다.

- `tests/test_ai_news.py`
- `tests/test_application_composition.py`
- `tests/test_architecture_regression.py`
- `tests/test_asset_history.py`
- `tests/test_candidate_refresh.py`
- `tests/test_dart.py`
- `tests/test_live_order_service.py`
- `tests/test_manual_limits.py`
- `tests/test_market_hours.py`
- `tests/test_market_listing.py`
- `tests/test_market_stream_endpoint.py`
- `tests/test_ml_backtest.py`
- `tests/test_ml_collect_market_indicators.py`
- `tests/test_ml_collection_reliability.py`
- `tests/test_ml_collection_worker.py`
- `tests/test_ml_repair_budget.py`
- `tests/test_news.py`
- `tests/test_paper_accounts.py`
- `tests/test_paper_feed.py`
- `tests/test_paper_management.py`
- `tests/test_paper_market_hours.py`
- `tests/test_quote_stream.py`
- `tests/test_settings_account.py`
- `tests/test_startup_readiness.py`
- `tests/test_stock_search.py`
- `tests/test_strategy_engine.py`
- `tests/test_toss_historical_candles.py`
- `tests/test_toss.py`

### 나머지 JavaScript 19개: VM·대역 경계 추가 확인

전 파일에서 내장 모듈 import·정적 소스 읽기·VM 실행·통신/타이머 대역 관련 패턴을 확인했다. 일부는 실제 페이지 스크립트를 문자열 slice로 추출한다. 예를 들어 `test_live_privacy.js`는 가짜 localStorage를 사용하지만 `live.js`의 일부 구간을 결합하고, `test_market_stream.js`는 EventSource를 대체한다. 다른 파일도 전체 실행 구간과 api/fetch/EventSource/localStorage/타이머 대역을 개별 검토해야 한다.

확인한 검색 결과에서 파일 쓰기·child_process·listen 호출을 발견하지 못했으나, 검색 부재나 VM 사용만으로 안전을 확정하지 않는다. 일부 파일의 입력은 테스트 안에 선언된 합성 데이터이며 실제 계좌 자료를 읽어 확인하지 않았다.

- `tests/test_chart_hover.js`
- `tests/test_chart_loading.js`
- `tests/test_detail_stream.js`
- `tests/test_etf.js`
- `tests/test_favorite_toggle.js`
- `tests/test_live_assets.js`
- `tests/test_live_privacy.js`
- `tests/test_login.js`
- `tests/test_market_stream.js`
- `tests/test_order_pagination.js`
- `tests/test_paper_records.js`
- `tests/test_paper_risk.js`
- `tests/test_paper_status.js`
- `tests/test_research_popup.js`
- `tests/test_settings_presets.js`
- `tests/test_stock_search.js`
- `tests/test_watch_list.js`
- `tests/test_watch_notes.js`
- `tests/test_watchlist.js`

보조 모듈 `tests/refactor_scenarios.py`는 paper와 simulator를 import하므로 독립 안전 테스트로 취급하지 않는다. fixtures는 경로만 조사했으며 실제 계좌·운영 데이터로 간주하여 임의 열람하지 않았다.

## 후속 실행을 검토할 때의 조건과 명령안

**아래는 이번에 실행하지 않은 제안이다. 후속 별도 승인이 필요하다.** 전체 unittest discover, Node 전체 glob, 앱 import smoke check, compileall, 패키지 설치는 이 문서로 승인되지 않는다.

1. 문서 기준 SHA와 테스트·의존 파일 변경 여부를 다시 대조한다. 사용자 전달에 따르면 Claude 독립 리뷰와 GPT 주요 판단 검토가 이루어졌으며, 아래 실행 안내만 보완한다. 리뷰 반영을 실제 실행 검증 완료로 해석하지 않는다.
2. 운영 `.env`·키·계좌·DB 덤프·RAW·백업이 없는 독립 공간에서 신뢰할 수 있는 기존 런타임을 선택한다. 운영 `.venv`나 ignored 파일을 복사하지 않는다.
3. Python의 sitecustomize/usercustomize/.pth 등 시작 hook, Node의 NODE_OPTIONS/preload 및 도구 wrapper가 앱·DB·통신·파일 쓰기를 추가하지 않는지 별도로 검토한다. 비밀 환경변수 값은 출력하지 않는다.
4. 네트워크·운영 DB·증권 접근을 환경 권한으로 차단하고, 쓰기는 승인된 임시 산출물 경로만 허용한다. Worktree만으로 이 조건이 충족되지는 않는다.
5. 실제 Python·Node 실행 파일의 절대 경로와 버전, Python 3.11 이상/Pydantic v2, Node의 Intl·ICU 한국어/시간대 지원 여부를 확인한다. 아래 `python`, `node`는 명령 형태를 보여 주는 표기이며 이 PC의 실행 파일을 확인한 결과가 아니다. 이번에 경로·버전 조회·import·설치는 하지 않았다. 부족한 의존성은 별도 설치 승인 대상이다.
6. 실행 CWD는 `D:\SourceBank\ai-VibeCoding-2026-codex-pilot`로 명시하고 아래처럼 한 파일씩만 실행한다. 프로젝트가 올바르게 import되는 경로도 별도 확인한다. 출력 저장은 별도 승인된 경로에 한정한다.

### Python: 예상 개수와 0개 실행 방지

소스의 테스트 정의를 읽어 확인한 예상 개수다. 실제 실행 결과가 아니다.

| 파일 | 예상 unittest 테스트 개수 | 후속 실행 결과에서 확인할 사항 |
|---|---:|---|
| `tests/test_chart_candles.py` | 3개 | 대상 메서드와 `Ran 3 tests`, 실패·오류·skip 없음 |
| `tests/test_long_term.py` | 4개 | 대상 메서드와 `Ran 4 tests`, 실패·오류·skip 없음 |

```powershell
# 후속 승인용 예시 — 이번 작업에서는 미실행
python -B -m unittest discover -s tests -p test_chart_candles.py -v
python -B -m unittest discover -s tests -p test_long_term.py -v
```

`unittest discover`는 검색 결과가 0개여도 종료 코드 0과 `OK`를 표시할 수 있다. 종료 코드나 `OK`만으로 성공 처리하지 않는다. 예상 테스트 이름·실행 개수·오류·실패·skip 여부를 함께 확인한다. 0개 또는 예상 개수와 다르면 **미검증/수집 이상**으로 보고하고, CWD·패턴·대상 파일·import 경로를 확인하기 전 통과로 처리하지 않는다. 이 확인을 자동으로 강제하는 스크립트는 이번에 추가하지 않는다.

### JavaScript: 일반 assert 스크립트 직접 실행

후보 3개는 현재 `node:test`의 `test()`로 케이스를 등록하는 구조가 아니라, 내장 assert와 가짜 DOM/VM을 사용하는 일반 스크립트다. 따라서 후속 명령안은 직접 실행 형태로 제시한다.

```powershell
# 후속 승인용 예시 — 이번 작업에서는 미실행
node tests/test_chart_viewport.js
node tests/test_settings_summary.js
node tests/test_market_hours.js
```

`node 파일.js`는 스크립트를 직접 실행하고 assertion 실패·미처리 오류 또는 설정된 실패 종료 코드로 실패를 알린다. `node --test 파일.js`는 Node 테스트 러너를 통해 실행하며 파일 단위 결과도 보고할 수 있다. 일반 assert 스크립트에서 러너가 표시한 테스트 수를 assertion 수나 개별 기능 검증 수로 해석하지 않는다. 직접 실행도 종료 코드와 해당 스크립트의 완료 로그를 함께 확인해야 하며, 실제 통과 여부는 아직 미검증이다.

**동명 파일 구분:** `tests/test_market_hours.js`는 이번 우선 후보로, 화면 표시 코드에 가짜 fetch·interval을 주입하는 JavaScript 테스트다. `tests/test_market_hours.py`는 Python의 시장 시간 계산·MarketHours 관련 별도 테스트이며 위 5개 후보에 포함되지 않는다. Python 파일은 앞의 추가 조사 분류를 유지하고, JS 후보의 판단·명령·검증 결과를 Python 파일에 적용하지 않는다.

### Python 옵션의 한계

| 옵션 | 제한적으로 하는 일 | 보장하지 않는 것 |
|---|---|---|
| `-I` | 격리 모드. `-E`, `-s`를 포함하며 스크립트 디렉터리/현재 디렉터리 등의 import 경로를 제한 | 운영체제 수준의 격리, DB·네트워크·파일 쓰기 차단. 프로젝트 import가 실패할 수도 있으므로 그대로 덧붙이지 않음 |
| `-E` | `PYTHON*` 환경변수의 해석을 무시 | 다른 환경변수·프로그램 설정 읽기, 모든 시작 hook과 부수 효과 차단 |
| `-s` | 사용자 site-packages를 import 검색 경로에 추가하지 않음 | 시스템·가상환경의 site 초기화, `.pth`·sitecustomize 등 모든 시작 hook 차단 |
| `-B` | import 시 `.pyc` bytecode 파일 쓰기를 억제 | 일반 파일 쓰기·DB·네트워크·프로세스 실행 차단 |

이 옵션들은 단독 또는 조합으로 사용해도 실행 안전성을 완전히 보장하지 않는다. `-I`/`-E`/`-s`로 import 경로가 달라질 수 있으므로 승인된 실행 파일·프로젝트 import 경로·의존성과 시작 hook을 별도로 확인해야 한다. 위 Python 명령안에는 `-B`만 사용했으며 다른 옵션을 무조건 추가하라는 의미가 아니다. 운영 접근 차단과 실행 환경 격리는 별도 조건이다. Node VM도 가짜 브라우저 환경으로 함수를 검사하기 위한 도구이며 운영 접근을 강제 차단하는 보안 장치는 아니다.

## 상태·한계와 독립 검토 요청

- 완료: 정적 조사와 문서 작성. API·DB 계약 및 앱 코드는 변경하지 않았다.
- 미실행: 자동 테스트, 앱 동작 검증, 사용자 동작 확인, 운영 적용. 후보가 실제로 통과한다는 증거는 없다.
- 판단의 한계: 후보 5개의 읽은 프로젝트 경로에 한정. 외부 패키지·시작 hook·동적 실행 환경은 미확인. 보류 파일은 위험이 확정됐거나 추가 조사가 필요한 경우를 함께 포함한다.
- Claude 리뷰 후속: 사용자께서 전달한 독립 리뷰의 실행 안내 보완을 반영했다. 실제 실행 파일의 절대 경로·버전, Node ICU/Intl 지원, `long_term` 본문·의존 경로의 추가 정독 필요성은 미검증 항목으로 유지한다. 초기 정적 조사 결과를 유지하되 이 추가 정독이나 실제 실행까지 완료했다고 보고하지 않는다. 다른 AI와 같은 파일을 동시에 수정하지 않는다.
- GPT 요청 사항: ‘정적 후보’와 ‘실행 안전 검증 완료’가 구분되는지, 실행 승인 조건·공통 지침 준수·두 문서 범위 확인.
- 사용자 확인: Draft PR의 Files changed에서 두 문서만 추가됐는지, 후보·근거·미검증·금지 사항을 확인한다. 문서 검토에는 앱·DB 실행이 필요 없다. 이후 실제 테스트 실행과 main 병합은 각각 별도 승인 대상이다.
- 핵심 개념: import는 선언 읽기만이 아니라 모듈 최상단 코드를 실행한다. mock은 적용 대상·시점 밖의 동작까지 자동 차단하지 않는다.
