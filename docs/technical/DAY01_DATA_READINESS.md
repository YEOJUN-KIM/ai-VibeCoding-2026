# DAY01 데이터 준비와 연구 결과의 해석

상태: 코드·문서 조사와 synthetic 입력 검증만 수행했다. **실제 저장 날짜 범위 UNKNOWN, 실제 현재 대상22종목 UNKNOWN, 실제 데이터 백테스트 미실행**이다. 운영 DB·RAW 파일·설정·환경 파일을 읽거나 API를 호출하지 않았다. 기존 문서의 과거 감사 수치를 현재 저장 상태로 재사용하지 않는다.

## 코드가 제공하는 증거와 한계

| 경로 / 계약 | 확인한 내용 | 증명할 수 없는 것 |
|---|---|---|
| `auto_trader/schema.sql`의 `ml_raw_candles` | source/symbol/interval/event_at/available_at/collected_at, OHLCV, collection_run_id, raw_payload; PK(source,symbol,interval,event_at) | 현재 저장 범위·현재 수량, 분별 turnover, 당시 주문 가능 잔량 |
| `auto_trader/ml/data_pipeline.py:RawCandle` | from_live_candle에서 available_at=실제 collected_at; 원래 수신 경계를 저장 | 과거 event의 가격을 event 당시 알았다는 증거. 과거 봉 백필은 실제 재수신 시각 전에는 이용 불가 |
| `auto_trader/ml/collect_candles.py:collect_candles` | 종목 응답별 received_at 생성, candle.timestamp+1분<=received_at인 완성 봉만 RAW 변환 | 장중 모든 분봉의 정시 수신 보장, 시가 순간의 실행 가용성 |
| `auto_trader/models.py:LiveStockCandle`, `brokers/toss/market_data.py` 차트 변환 | timestamp/open/high/low/close/volume | 분별 거래대금. 가격×거래량으로 정확 VWAP를 조용히 대체할 수 없음 |
| `auto_trader/models.py:LiveStockDetail` | trading_amount/volume/rank 등 종목 상세·순위 집계 | 그 값이 각 1분의 거래대금이라는 계약 |
| `auto_trader/models.py:Quote` | symbol/name/price/timestamp | bid/ask depth 및 해당 가격에 실제 주문 가능한 수량 |
| `auto_trader/quote_stream.py` | trade:kr frame을 listener에 전달, maxsize1 queue에서 오래된 frame 제거 | 로컬 수신시각을 붙인 불변 장기 이력·호가 잔량 보존. provider frame에 임의 필드가 있어도 그 필드의 실행 계약은 미확인 |
| `auto_trader/paper_feed.py` | 차트 뒤 현재가 요청, prices_requested_at/received_at runtime 시각과 freshness 검사 | 모든 분의 시가 수신 이력·실행 잔량·장기 증거 저장 |
| `schema.sql:ml_strategy_candidates`, `ml/candidate_pipeline.py:capture_before` | quote_observed_at/decision_at/available_at 등 후보 관측·결정 PIT 정보 | quote_observed_at은 후보 캡처 프로세스 관측 clock이며 시가 순간의 호가 잔량 증거가 아님 |
| `brokers/toss/transport.py`의 orderbook 라우팅 | orderbook API 경로 분류는 코드에 존재 | 현재 RAW에 역사적 depth/수신 증거가 저장되어 있다는 뜻이 아님. 이번에 API 호출하지 않음 |
| `ml/gap_repair.py`, `ml/quality_report.py` | FULL_SESSION_BARS=391, LAST_BAR=15:30 | DAY01의 `[09:00,15:30)` 390분과 동일한 구간이라는 주장 |
| `settings.py`의 WATCH_SYMBOLS 및 `schema.sql:live_strategy_symbols` | 코드 기본 WATCH_SYMBOLS는10개, 전략별 대상은 별도 테이블/설정에 존재 | 현재 실제22개 목록. settings 모듈을 import하지 않고 소스만 읽었음 |
| `docs/machine-learning/CURRENT_PROGRESS.md`, `COLLECTION_STABILITY.md` | 과거 전략17·22종목 수집·불변 감사 이력 | 현재 설정/데이터 범위. 실시간 잠금 또는 현재 보유 목록이라는 주장 |

고정 universe는 호출자가 사전에 제공해야 한다. 과거 시점 top50 재구성, 데이터에서 성과/거래량으로 종목 선택, 현재 설정 자동 조회는 없다. 실제 데이터 검증 전에 별도 승인된 read-only export와 날짜·종목 목록·가용시각 근거가 필요하며 이번 작업에서 수행하지 않는다.

## 순수 입력 계약

`auto_trader/ml/day01_research.py`는 표준 라이브러리와 순수 day01만 import한다. 파일/DB/네트워크/설정/현재시각 접근은 없다.

- `adapt_rows(mappings, *, universe) -> ResearchInput(candles, provenance, excluded_rows)`.
- 매핑 필수: source, symbol, interval='1m', event_at, available_at, open_price/high_price/low_price/close_price/volume. 시간은 aware datetime 또는 offset 포함 ISO 문자열이다. 원래 offset·가용시각을 보존하며 `available_at=event_end`나 `now`를 만들어 넣지 않는다.
- 금액·거래량은 Decimal/정수/십진 문자열만 받는다. float/bool·비유한·손상 OHLC·미완결 봉을 거절한다. optional turnover는 명시적으로 받은 경우만 유지하며 OHLC×volume 경계를 검사한다. optional collected_at은 원래 값을 보존하고 available_at 이후인지 검사한다. collection_run_id도 제공된 경우만 보존한다.
- source/interval 및 종목·사건·가용시각·선택적 수집/실행 ID가 provenance에 남는다. RAW payload나 임의 필드를 복사하지 않는다. 동일 symbol/event 중복은 출처가 달라도 거절하며 호출자가 먼저 출처를 명시적으로 선택해야 한다.
- universe 밖은 검증 전에 제외한다. DAY01 정규 입력은 `[09:00,15:30)`이며 15:30은 `CLOSING_1530`, 그 외 시간외는 `OUTSIDE_REPLAY_SESSION`으로 **원본 Candle와 provenance를 별도 반환**한다. 조용히 버리거나 시각을09:00/15:29로 바꾸지 않는다. core replay의 정규 세션 제한은 유지한다.

`coverage(data, *, universe, expected_session_dates)`는 호출자가 명시한 날짜마다390분×고정종목 grid를 진단한다. 휴장일·주말을 추측하지 않는다. expected 날짜가 비어 있으면 거절하며 datetime을 날짜로 묵시 변환하지 않는다.

| 결과 | 의미 |
|---|---|
| scope / actual_stored_date_range | CALLER_PROVIDED_ROWS_ONLY / UNKNOWN. DB의 실제 범위를 뜻하지 않음 |
| input_count/replay_count/excluded_count | caller가 제공한 선택 종목 행 수, 정규390분 대상 수, 분리 행 수 |
| closing_1530_count/outside_session_count | 경계·시간외 분리 수, 입력391개가 정상390개+closing1개인지 확인 가능 |
| event_min/max, available_min/max, observed_days | 받은 정규 입력의 KST 범위; 저장소 전체 범위가 아님 |
| missing_days/symbols, unexpected_days | 명시한 기대치와 제공 행만 비교 |
| cells | day/symbol별 count, expected_minutes390, missing_minutes 및 count, COMPLETE/MISSING/NO_ROWS |
| availability | on_time/late count, max lag seconds(분 끝 기준), historical_backfill_count |
| missing_turnover_count/zero_volume_count | 정확 VWAP·무거래량 해석을 위한 별도 지표 |

UNKNOWN은 실제 저장 상태를 조사하지 않았다는 뜻이고 MISSING/NO_ROWS는 **제공 입력과 기대 grid**의 상태다. COMPLETE도 시가 체결 준비나 수익성 준비를 뜻하지 않는다. strict OpenQuote는 Candle에서 추론할 수 없다. 정확 VWAP는 turnover가 필요하고 부족하면 명시적으로 typical_approximation을 선택하거나 무신호로 남겨야 한다.

`run_input(data, config, *, universe, expected_session_dates, open_quotes=())`는 위 coverage·provenance·분리 행과 core 결과를 묶는다. 정규 행이 없으면 NO_REPLAY_ROWS/result=None이다. 기대 날짜는 진단용이며 replay를 조용히 필터하지 않는다. 호출자가 데이터 날짜 범위를 사전에 선택해야 한다. 기본 Config는 여전히 strict next_open이므로 별도 시가 증거 없이는 체결하지 않는다.

## 연구용 추정 모드: 과거 실제 체결이 아님

명시적 `Config(execution='research_estimated_open')`만 활성화한다. strict OpenQuote를 생성하거나 섞지 않는다. 기본 strict/legacy의 수치 동작은 유지한다. **연구 모드의 장부·PnL는 실제 관측 시점에만 바뀌며, 과거 시가 시각에 실제 체결되었다는 주장이나 그 시점의 실제 손익 곡선이 아니다.**

| 시각/필드 | 계약 |
|---|---|
| signal_at / decision_at | 완성 입력이 실제 available_at에 도착하고 판단한 시각 |
| estimated_execution_at | 신호보다 엄격히 이후인 분봉 시가의 반사실적 추정 시각 |
| observation_at / observed_at | 가격 결과로 쓰는 해당 Candle의 원래 available_at |
| execution_at / settlement_at | 관측 정산 시각. 앞선 후보를 기다린 경우 원래 observation보다 늦을 수 있음 |
| price_source | ESTIMATED_CANDLE_OPEN |
| physical_fill_claim / uncertain | False / True |
| hindsight_fields | open_price, bar_exists, positive_volume_veto, availability_lag |

research_max_observation_lag_minutes는 유한 Decimal `[0,30]`, 기본2분이며 **분 끝 이후** 수신 지연 허용치다. 완성5분/개장범위가 이 제한 안에서 새로 알려지면 실제 수신시각에만 결정한다. historical backfill은 가용시각을 되돌리지 않으며 늦은 데이터로 과거 신호를 만들지 않는다. 11:30 이후 실제 판단은 새 매수를 만들지 않는다.

신호 직후 다음 분부터 사건 순서대로 가격 결과를 기다린다. 더 빠르게 수신한 뒤쪽 분봉을 앞 후보보다 먼저 선택하지 않는다. 앞 후보가 관측되거나 `event+1분+lag`까지 없을 때만 다음 후보로 전진한다. 누락/과도한 지연/0거래량은 각각 사유를 남긴다. 0거래량 배제는 **완성 결과를 본 hindsight veto**이며 실행 가능 잔량이 아니다. 실제 수량은 signaled order_quantity 가정으로 고정하며 대상 분봉 volume이나 volume_participation으로 실제 유동성을 주장하지 않는다.

추정 open 나이 `< max_signal_to_fill_minutes`(기본2분, exclusive)가 필요하다. 정산 대기·자원 예약은 가격 관측을 위해 추가 `1분+lag` grace를 갖는다. default09:20 신호의 eligible09:21 추정 open은09:22~09:24에 관측 정산할 수 있고09:22 추정 open은 나이2분이므로 배제한다. 무결과 대기 주문은09:25에 만료·예약 해제한다. 분초 지연 신호는 그 실제시각을 기준으로 타이머를 만든다.

예약 정책은 손실 거래 선택 편향을 막는다. 신호 시 수수료 포함 현금·노출·포지션 slot을 예약하고 다른 신호는 그 예약도 한도에 포함한다. 정산 시 대상 분봉 종가·고저가·관측 지연 중의 나중 손실로 BUY를 거절하지 않는다. 추정 open **엄격히 이전**에 실제로 알려진 risk block만 정보시각 증거와 함께 취소할 수 있다. open 이후 새 손실은 이미 예약한 반사실적 거래를 지워 이익만 남기는 근거가 될 수 없다.

시가 갭으로 실제 비용이 예약·신호 시 자원/노출·현금비율을 넘으면 거래를 유리하게 없애지 않는다. 반사실적 거래·비용·손실을 남기고 `INVALID_RESOURCE_OVERRUN`과 breach flags를 기록한다. 부족 현금은 cash를 음수로 숨기는 대신 **실제 조달이 아닌 연구 전용 non_funded_liability**로 분리하고 equity에서 차감한다. 매도 순대금은 이 가상 부족액부터 줄인다. 실제 차입·주문 기능은 없고 위험 한도 준수를 주장하지 않는다. 무효 상태는 새 진입을 차단하되 청산과 손실 보고는 유지한다. run_status가 무효이면 전략 성과로 해석할 수 없다.

정산 후 이미 도착한 `event>=estimated_entry` 봉 모두를 따라잡고 최신 알려진 mark와 손절/목표를 적용한다. 진입 분봉 자체도 포함하며 dual-hit은STOP 우선이다. STOP을 발견한 현재 시각에 신호를 내고 엄격히 이후 추정 open 결과를 기다리며 소급 청산하지 않는다. T 처리 순서는 만료/마감 → 연구 정산 SELL/BUY 정렬 → 현재 수신과 기존 이력 catch-up/mark/위험 → 새 결정이다. 연구 정산으로 나중에 알려진 자본 경합·손익을 실제 과거 체결 시퀀스와 동일시하지 않는다.

14:50 청산도 같은 방식이며15:10 이상 관측은 먼저 동결된다. 15:09 추정 open이15:10에야 관측되면 정산하지 않고 잔량/UNRESOLVED_OBSERVED_AFTER_1510를 보고한다. 마지막 알려진 가격의 비용 차감 평가를 보존하고 익일 gap 미모델링/동결 플래그를 유지한다. missing/late/uncertain/unfilled 수량·mode·비용·슬리피지·가정은 결과/거래에 기록한다.

## 검증과 다음 단계

모든 신규 입력은 synthetic이다. 기존DAY01 175개, 신규 준비/연구27개 및 기존 guarded 최소55개를 실행했다. 실제 데이터 백테스트는 하지 않았다. 사용자 재현 명령은 [새 작업 기록](../plans/ai-tasks/20261112-Codex-DAY01-data-prep.md)을 따른다.

실제 검증에는 별도 승인된 export의 날짜 범위·고정 universe·원본 수신시각·391/390 경계·정확 turnover 유무 확인이 먼저 필요하다. strict 체결에는 별도 시가 가용가격/용량 증거가 필요하다. 이 문서는 계획·코드·합성 검증을 현재 운영 적용과 구분하며 수익성을 주장하지 않는다.
