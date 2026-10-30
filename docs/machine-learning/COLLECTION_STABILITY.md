# 시장 데이터 수집 안정화 V1 / 품질·날짜 선택 V2

최신 확인일: **2026-10-30**, 한국시간. 수집 파이프라인은 운영·안정화 및 다일 누적 중입니다. V1은 유지하고 V2 품질 의미·백테스트 데이터 선택 보수를 완료했습니다. ML Dataset·실제 모델 학습은 아직 시작 전입니다.

V2.1: 품질 보고서는 evidence 강도(확정 final → 완전한 positive → 불완전/opening → 조회 실패)를 우선하고 같은 강도는 실제 수신 시각으로 선택합니다. 후속 실패가 확정 provider absence를 덮지 않으며, `finished_at`·`received_at`의 미래 증거 차단을 유지합니다. 수집 구조·전략·주문 로직과 DB schema는 변경하지 않습니다.

## V2 최종 검증 (2026-10-30)

### 유지한 V1과 수정 범위

09:05·09:10 opening reconciliation, 15:35 final reconciliation, bounded chart GET retry, 429 cooldown, POST 비재전송, 오류 분류, 실제 응답 수신 시각, immutable observation과 unique conflict 정책을 유지했습니다. 정기 20개 중첩 조회도 줄이지 않았습니다.

V2는 다음을 보완합니다.

- 391 timestamp 진단 grid를 유지하며 raw missing과 actionable missing을 분리합니다.
- 마감 후 충분한 원천 범위에서 확인한 provider absence만 completeness PASS에 허용합니다. opening 응답의 부재는 아직 unverified입니다.
- 과거 날짜의 재검사 증거는 실행일이 아닌 `diagnostics.report_date`로 조회합니다. 증거의 완료·수신 시각은 보고서 `generated_at` 이후일 수 없습니다.
- `before` 시작 cursor를 선택적으로 넘겨 과거 정규장 끝부터 기존 pagination을 재사용합니다. 현재 조회와 과거 조회의 cache key를 분리하며 기본 조회는 이전 동작입니다.
- 백테스트 시작·종료일은 KST `event_at`으로 선택하고, replay는 계속 `available_at`을 사용합니다.

### 품질 필드와 판정

`missing_bars`·`missing_timestamps`의 기존 raw grid 의미는 유지합니다. timestamp 목록은 시계열 항목별로, 누락 원인별 건수는 항목·종목/지수 summary·최상위 `gap_summary`에 제공합니다. 정규장/시간외 행 수는 항목과 종목/지수 summary에서 확인합니다.

| 필드 | 의미 |
|---|---|
| `raw_missing_bars` / `raw_missing_timestamps` | 진단 grid와 DB의 전체 차이 |
| `provider_absent_bars` / `provider_absent_timestamps` | 마감 후 충분한 범위의 원천 응답에도 없는 봉 |
| `collection_gap_bars` / `collection_gap_timestamps` | 원천에 존재하지만 DB에 없는 봉 |
| `unverified_bars` / `unverified_timestamps` | 원천 조회 실패·범위 부족·최종 확인 전 gap |
| `effective_missing_bars` | collection gap + unverified |
| `regular_session_rows` | 정규장 관측 행 수 |
| `outside_regular_session_rows` | 같은 KST 날짜의 정규장 밖 관측 행 수 |

데이터가 없는 시계열은 FAIL입니다. 그 외에 effective missing이 있으면 WARN, 남은 raw missing이 모두 최종 provider absence로 확인됐거나 raw gap이 없으면 PASS입니다. 공급사가 그 분에 봉을 반환하지 않았다는 사실만 기록하며 무거래·정상 봉·거래소 의미를 추론하지 않습니다. `provider_semantics=UNCONFIRMED`를 유지하고 synthetic candle을 만들지 않습니다.

기존 전체 `status`와 `collection_health_status`는 별도로 유지합니다. `quality_semantics_version=2`와 새 상세는 기존 JSONB summary에 저장하므로 migration은 없습니다. 예전 V1 보고서는 재생성 전까지 당시 판정으로 남으며, 새 워커는 장 마감 뒤 기존 당일 V1 보고서를 발견하면 V2로 재생성합니다.

### 10/28 보고서 재생성

22종목의 09:01~15:30은 **8,580개(22×390)**, KOSPI·KOSDAQ도 각각 390개로 모두 존재합니다. 09:00이 실제 있는 18종목을 포함한 정규장 종목 행 수는 8,598개입니다. 당일 시간외 종목 RAW 290개는 삭제하지 않았습니다.

| 지표 | 종목 | 시장지표 | 합계 |
|---|---:|---:|---:|
| raw missing | 4 | 2 | 6 |
| provider absent | 4 | 2 | 6 |
| collection gap | 0 | 0 | 0 |
| unverified | 0 | 0 | 0 |
| effective missing | 0 | 0 | 0 |

최종 상태는 **data completeness PASS / collection health WARN / 기존 status WARN**입니다. 원천 부재는 `035720`, `036930`, `080220`, `403870`, `KOSPI`, `KOSDAQ`의 09:00이며 원천 과거 전체 조회로 다시 확인했습니다. `FULL_SESSION_BARS=391`과 원본 데이터는 판정을 맞추기 위해 바꾸지 않았습니다.

### 과거 3일 실제 재검사·백필

| 날짜 | 수정 전 raw missing | 실제 백필 | 최종 raw missing | provider absent | collection gap | unverified | completeness |
|---|---:|---:|---:|---:|---:|---:|---|
| 2026-10-15 | 2,352 | 2,346 | 6 | 6 | 0 | 0 | PASS |
| 2026-10-16 | 266 | 260 | 6 | 6 | 0 | 0 | PASS |
| 2026-10-21 | 347 | 341 | 6 | 6 | 0 | 0 | PASS |

합계 **2,947개**를 원천 실제 반환값으로 추가했습니다. 10/21 첫 조회는 두 종목의 token-revoked 오류로 PARTIAL이었고, 해당 두 대상만 한 번 더 확인해 남은 30개를 복구했습니다. 첫 오류 기록은 그대로 보존했습니다. 세 날짜의 health는 지연·수집 기록을 반영해 WARN입니다. 각 날짜의 남은 여섯 raw gap은 모두 위 여섯 시계열의 09:00 provider absence입니다.

기존 RAW **35,234개**의 모든 열은 체크섬이 동일하며 UPDATE·DELETE하지 않았습니다. 신규 2,947개의 `available_at=collected_at`은 모두 실제 재수신한 10/30 시각입니다. 과거 event 시각으로 소급하지 않았습니다. 전략 17 설정·대상 22종목도 전후 체크섬이 같습니다.

### 백테스트 날짜 선택 전후

동일 DB·저장 전략 17·`--start 2026-10-28 --end 2026-10-28` 기준입니다.

| 항목 | V1 | V2 |
|---|---|---|
| 입력 행 | 8,955 | 8,888 |
| observed_days | 10/27, 10/28 | 10/28 |
| 날짜 선택 | available_at | KST event_at |
| replay 정보 사용 | available_at | available_at 유지 |

전날 event 67개가 선택에서 제외됐습니다. 당일의 시간외 290개는 선택에 남습니다. 향후 Dataset의 session 선택은 별도 설계합니다. 과거 봉을 다음날 받았어도 실제 수신 전에는 history에 들어가지 않고 과거 가격 주문을 만들지 않습니다. `replay`, `Config`, `ReplayBroker`, `ReplayRisk`의 AST가 수정 전과 동일함을 확인했습니다.

### 중복·요청량 측정과 운영 적용

변경 전 10/15 이후 수집 실행 기록 기준입니다. 종목 2,032개 배치에서 신규 77,223·중복 717,375개, 지수 2,030개 배치에서 신규 6,850·중복 63,393개를 기록했습니다. 논리 요청량은 종목 894,080봉·지수 81,200봉이며 HTTP 재시도 수를 뜻하지 않습니다. 최근 20개 중첩 수집은 유지했고 중복 감소 최적화는 하지 않았습니다.

이번 4개 과거 날짜 재검사와 10/21 추가 대상 재검사에서 계측한 차트 GET client 시도는 **274회**입니다. 그룹 대기·재시도를 포함한 client 호출 수이며 실제 네트워크 전송 횟수와 구분합니다. 과거 조회는 날짜 cursor로 필요한 범위만 읽습니다. 현재 장중 서버·전략은 유지했으며, 자동 워커의 V2 판정은 다음 정상 재시작부터 적용됩니다. 과거 4일 보고서는 이미 V2로 수동 재생성했습니다.

### 테스트·diff 검토

전체 Python **339개**, JavaScript **22개**가 통과했습니다. provider absence PASS·원천 존재 gap WARN·조회 실패 unverified·복구 후 PASS·미생성·시간외 보존·historical 증거 시점·event-date 선택·availability replay·next-day backfill 무거래·cursor/cache 격리 검증을 추가했습니다. 컴파일·앱 import·Git 공백 검사도 확인했습니다.

diff는 수집 증거·품질 판정·과거 조회 cursor·백테스트 입력 선택·테스트·문서에 한정됩니다. V1 transport·collector·raw 저장, 전략·리스크·PAPER·LIVE 주문 소스와 DB schema는 그대로입니다. 실제 주문 전송·전략 시작·RAW 수정/삭제·DB migration은 없습니다.

## V1 점검 이력 (2026-10-28)

이하의 WARN 판정과 당시 검증 건수는 **V1 시점의 과거 기록**입니다. 현재 판정은 위 V2 검증을 기준으로 합니다. V1은 수집·누락 탐지·품질 보고서에 한정해 구현했습니다.

## 1. 확인한 09:00 누락 원인

당일 정규장 데이터를 DB와 마감 후 Toss 원천 응답으로 비교했습니다. 다음 여섯 시계열의 누락은 모두 **09:00 한 개**였습니다.

| 대상 | DB 정규장 봉 | 마감 후 API 정규장 봉 | API 09:00 |
|---|---:|---:|---|
| 035720 | 390 | 390 | 없음 |
| 036930 | 390 | 390 | 없음 |
| 080220 | 390 | 390 | 없음 |
| 403870 | 390 | 390 | 없음 |
| KOSPI | 390 | 390 | 없음 |
| KOSDAQ | 390 | 390 | 없음 |

각 API 요청은 600개 봉을 기존 pagination으로 조회했고 이전 거래일까지 포함했습니다. 비교 표본 005930·000660은 DB와 API 모두 391개이며 09:00도 있었습니다. 최종 당일 reconciliation은 22종목과 지수 두 개를 모두 검사했습니다. 실제 API에 존재하는데 DB에 없는 정규장 봉은 발견하지 못했고 추가 저장은 0건입니다.

따라서 확인한 당일 사례는 요청의 Case B입니다. **원천에 09:00이 없는 사실**과 **원천이 그 분에 봉을 만들지 않는 이유**는 구분해야 합니다. 실제 무거래인지, 봉 timestamp 규칙인지, 공급 지연인지까지 단정하지 않습니다. 점검은 마감 후 수행했으므로 과거 장 시작 직후의 전체 응답과 직접 비교할 수는 없었습니다. 향후 정기 수집과 opening reconciliation에 수신 시각·09:00 포함 여부·조회 범위의 장 시작 포함 여부를 기록해 Case A/B/C를 구분합니다.

`MARKET_OPEN=09:00`, `FULL_SESSION_BARS=391`은 그대로 유지합니다. 원천 미제공 여섯 봉을 임의 생성하거나 정상 수집으로 간주하지 않았습니다.

## 2. 중간 오류가 대부분 최종 공백이 되지 않았던 이유

기존 워커는 09:01:02~15:31:02에 매분 최근 20개 봉을 겹쳐 수집합니다. 잠깐 실패해도 다음 요청에 그 봉이 포함되면 `ON CONFLICT DO NOTHING`으로 중복 없이 복구됩니다. 기존 completed 필터에서 09:00 봉은 09:01:02 첫 요청에 반환되었다면 저장 가능한 상태입니다. 필터가 09:00만 별도로 제외하는 결함은 발견하지 못했습니다.

다만 최근 창보다 오래된 누락이나 계속 원천에 없는 봉은 이 방식으로 해결하지 못하므로 별도 재검사를 추가했습니다. 모든 일시 오류가 항상 복구된다고 보장하지는 않습니다.

## 3. 정확한 gap 탐지

`ml/gap_repair.py`에서 정규장 09:00~15:30의 1분 timestamp 집합을 만들고 DB의 실제 `event_at` 집합과 차이를 계산합니다. 장중에는 완료된 분까지만 포함합니다. 이 집합은 검사 기준이며 OHLCV 데이터를 생성하는 데 사용하지 않습니다.

품질 검사는 원천·종목/지표·interval·정규장 범위와 `available_at <= generated_at`을 제한합니다. 다른 source나 분 경계 밖 행이 있어도 실제 누락 분을 덮어 숨길 수 없습니다. 개별 시계열에 다음 정보를 추가합니다.

- `missing_bars`, `missing_timestamps`
- `provider_absent_timestamps`: 당일 전체 범위를 확인한 원천 응답에도 없는 분
- `collection_gap_timestamps`: 원천 응답에 있으나 최종 DB에 없는 분
- `unverified_timestamps`: 원천 조회 실패·범위 부족 등으로 판단할 수 없는 분
- `gap_origin`, `provider_checked_at`, `provider_semantics`

`provider_semantics=UNCONFIRMED`는 무거래 여부나 공급사 timestamp 의미를 확인하지 못했다는 뜻입니다. 거래량 0인 실제 봉도 정상 원본으로 유지하며, 봉 자체가 없는 분을 거래량 0의 봉으로 바꾸지 않습니다.

## 4. Opening reconciliation

매 거래일 09:05:10과 09:10:10에 09:00~검사 시작 당시 완료 봉까지 다시 확인합니다. 정기 수집이 길어져 예정 시각을 넘겼다면 09:12까지 해당 검사를 따라 실행합니다. 첫 정기 수집 시각은 늦추지 않았습니다.

이미 DB가 완전한 대상은 불필요한 API 요청을 하지 않습니다. 누락이 있으면 캐시를 사용하지 않고 기존 차트 client를 호출해 실제 반환 timestamp와 DB 누락 timestamp의 교집합만 저장합니다. 정기 수집과 경합해도 기존 unique key가 중복 삽입을 막습니다.

## 5. End-of-day reconciliation

15:31:02 마지막 정기 수집 → 15:35 당일 전체 재검사 → 기본 설정 15:40 품질 보고서 순서입니다. 늦게 서버를 켜면 당일 최종 검사를 따라 실행합니다. 품질 워커도 보고서 생성 전에 최종 검사 완료 여부를 확인합니다.

동일 대상 전체에 대해 15:35 이후 완료한 full-session 최종 검사가 있으면 중복 호출을 건너뜁니다. 검사 중 오류·조회 범위 부족이 있으면 완료로 간주하지 않아 보고서 직전 다시 시도할 수 있습니다. 수집 워커와 품질 워커의 재검사는 프로세스 내 잠금으로 직렬화합니다. 휴장일에는 워커가 API 조회를 건너뜁니다.

기존 `_domestic_candles`·`_market_indicator_candles`의 `before` cursor pagination을 재사용합니다. 요청 수는 정규장과 연장 시간대를 포함하도록 정하며 최대 10,000개로 제한합니다. 오래된 날짜가 이 범위에 들어오지 않으면 전체 범위를 확인했다고 주장하지 않고 미확인으로 기록합니다.

## 6. Retry 정책

추가 재시도는 **candle GET** 요청의 timeout·일시 네트워크 오류·HTTP 5xx에만 적용합니다. 기본 대기는 0.5초, 1초이며 429와 같은 총 최대 3회 시도 예산을 사용합니다. collector에 별도 중첩 재시도를 추가하지 않았습니다.

영구 4xx와 잘못된 응답 형식은 반복 요청하지 않습니다. 기존 토큰 재발급 정책은 유지합니다. POST 주문 생성·취소에 새로운 자동 재전송은 없습니다. 주문·계좌 조회 GET의 네트워크 재시도 범위도 확대하지 않았습니다.

## 7. Rate-limit 보호와 오류 분류

기존 그룹별 lock·최소 호출 간격·429 `Retry-After`/reset cooldown을 유지합니다. 차트 재시도 대기도 남은 그룹 cooldown보다 짧아지지 않습니다. 기존 20초 재시도 대기 예산 검사와 각 요청의 timeout을 유지합니다. 이는 실행 중인 단일 HTTP 요청까지 20초 안에 강제로 종료한다는 뜻은 아닙니다.

기존 `errors[target]` 문자열과 실행 status는 유지합니다. 새 오류 상세와 조회 증거는 기존 JSONB `errors._diagnostics`에 저장하며, `_diagnostics` 자체를 오류로 세지 않습니다. 상태 판정도 실제 오류 map만 사용하므로 진단 정보가 있다는 이유로 정상 수집이 PARTIAL이 되지 않습니다.

새 상세의 분류는 `RATE_LIMIT`, `TIMEOUT`, `NETWORK`, `HTTP_5XX`, `HTTP_4XX`, `INVALID_RESPONSE`, `OTHER`입니다. 거시 지표 수집도 오류 상세만 추가했으며 원본 계산·저장 방식은 변경하지 않았습니다. 과거 문자열 오류는 가능한 범위에서 추론해 집계하므로 새 구조화 상세보다 분류 근거가 제한됩니다. `error_counts`는 실패 실행 수가 아닌 **대상별 오류 항목 수**입니다.

## 8. Quality Report 상태

기존 `status`·`issues`·요약 구조를 유지하고 다음 필드를 추가했습니다.

- `data_completeness_status`: 실제 누락 여부. 전체 데이터가 없으면 FAIL, 일부 누락이면 WARN, 모두 있으면 PASS.
- `collection_health_status`: 당일 FAILED/PARTIAL 실행이나 5분 초과 수신 지연이 있으면 WARN.
- `reconciliation_summary`: 검사 실행 수, 복구된 행 수, 검사 종류별 복구량, 최종 미복구량.
- `collection_summary.error_counts`: 오류 종류별 항목 수.

장중 PARTIAL이 있었어도 최종 봉이 모두 있으면 completeness=PASS, health=WARN이 됩니다. 최종 여섯 봉이 원천에도 없는 당일 실데이터는 completeness=WARN·health=WARN을 유지했습니다. 복구가 없는데 PASS로 바꾸지 않았습니다. 전략 판단·거시 지표 부족 등 기존 사유로 전체 `status`가 WARN인 동작도 유지합니다.

당일 최종 보고서에서 PARTIAL 17회, FAILED 0회, 대상별 오류 항목은 TIMEOUT 10건·RATE_LIMIT 18건으로 집계했습니다. 한 PARTIAL 실행에 여러 대상의 오류가 들어갈 수 있어 실행 횟수와 오류 항목 수는 다릅니다. 기존 문자열 기록의 분류는 메시지에 근거한 추론입니다.

세부 상태는 기존 요약 JSONB 안에도 저장하고 읽을 때 최상위에 추가합니다. 기존 보고서도 세부 상태를 파생해 반환합니다. 보고서 다시 생성 전의 과거 저장 보고서는 당시 집계 결과를 그대로 사용합니다. 품질·수집 실행 요약은 정규장에 한정하지 않고 당일 전체 범위로 집계해 마감 후 재검사와 장전 거시 수집도 포함합니다.

## 9. 테스트 결과

- 변경 전 전체 Python 테스트: **305개 통과**.
- 최종 전체 Python 테스트: **325개 통과**.
- JavaScript 테스트: **22개 통과**.
- Python 모듈 컴파일·앱 import·`git diff --check` 확인.
- 새 검증: 첫 응답에 없던 09:00 복구, 반복 검사 멱등성, 복수 중간 gap 최종 복구, 지수 복구, 원천에도 없는 gap 유지, 조회 범위 부족, 재검사 순서·휴장일·늦은 시작, 실제 응답 수신 시각, 오류 분류, 제한된 GET 재시도와 POST 비재전송, completeness/health 분리.

DB 테스트는 매번 별도 스키마를 만들고 삭제합니다. Windows 제한 환경의 PostgreSQL/asyncio 내부 소켓 차단 때문에 전체 테스트는 그 제한 밖에서 실행했습니다.

## 10. Observation·point-in-time 무결성

기존 raw 봉은 UPDATE하지 않습니다. 원래 source·symbol/indicator·interval·event_at unique key와 `ON CONFLICT DO NOTHING`을 그대로 사용합니다. 이미 저장된 봉이 다음 원천 응답에서 달라져도 덮어쓰지 않습니다.

새 백필은 실제 API 응답을 받은 직후 시각을 `available_at`·`collected_at`에 기록합니다. 정기 워커도 요청 시작 시각을 collector에 넘기지 않고 각 응답 수신 시각을 사용하도록 보완했습니다. 명시적 `collected_at` 인자는 기존 호출 호환성과 테스트 시계로 유지되지만 운영 워커와 새 백필은 과거 event 시각을 수신 시각으로 사용하지 않습니다.

09:00 봉을 09:10에 받은 경우 09:05 `as_of` 조회에서는 보이지 않음을 종목·지수 DB 테스트로 확인했습니다. 기존 backtest 조회·계산 코드는 수정하지 않았습니다.

실제 당일 정규장 기존 observation **9,378개**에 대해 전후 모든 열의 체크섬이 같았습니다. 최종 원천 대조 후 신규 저장은 0건이며, 원천에도 없는 여섯 봉은 그대로 남았습니다.

같은 최종 검사를 다시 실행하면 기존 완료 기록을 확인해 `SKIPPED`·추가 저장 0건으로 처리되며 기존 observation의 체크섬도 그대로임을 확인했습니다.

## 11. DB migration

없습니다. `schema.sql`과 raw candle 스키마를 수정하지 않았습니다. 추가 진단·상태는 기존 수집 실행의 `errors` JSONB와 품질 보고서 요약 JSONB에 저장합니다.

## 12. 매매 로직·설정 영향

전략, 이동평균·청산 조건, PAPER 체결, LIVE 주문 서비스, 리스크, ML 모델, backtest 계산과 환경변수는 변경하지 않았습니다. 실제 DB의 **전략 17 설정과 22개 대상 목록**도 전후 체크섬으로 불변임을 확인했습니다. 실제 주문을 전송하거나 전략을 시작하지 않았습니다.

구현 파일은 `gap_repair.py`, `collection_errors.py`와 기존 collector·수집/품질 worker·품질 보고서·Toss chart transport입니다. 운영 서버가 다음에 시작되면 새 자동 검사 일정이 적용됩니다.
