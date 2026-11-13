# DAY01 Scout 시장 데이터 어댑터 (순수, 합성 검증)

상태: 구현·합성 테스트만. **어디에도 배선하지 않았고** 실제 API·운영 DB·수집 워커·설정은 건드리지 않았다. 코드 `auto_trader/day01_scout_market.py`, 테스트 `tests/test_day01_scout_market.py`.
새 수집기·워커·API 클라이언트는 없다. 입력은 기존 자료의 계약만 따른다.

## 1. 입력 계약 (기존 자료)

| 입력 | 기존 계약 | 어댑터가 쓰는 필드 |
|---|---|---|
| 순위 스냅샷 | `RankingSnapshot`(Scout) — `received_at`은 우리의 최초 수신, `provider_ranked_at`은 공급자 시각 | 행의 `symbol`, `turnover`(원), `volume`. 값은 공급자의 **당일 누적**이며 `rankedAt` 시점 |
| 1분봉 행 | `ml_raw_candles` 열 / `available_candles()` 반환 형식: `source, symbol, interval='1m', event_at, available_at, collected_at, open/high/low/close_price, volume` (+ 선택 `turnover`) | 위 열 전부. DB에는 분별 거래대금 열이 없다 |

호출자가 `available_candles(... as_of)` 같은 방식으로 행을 읽어 전달한다(어댑터는 DB를 읽지 않는다). 어댑터도 자체적으로 `as_of` 이후 가용 행을 다시 걸러낸다.

## 2. 유동성 (`liquidity_from_snapshot`)

- `LiquidityFacts(symbol, observed_at=snapshot.received_at, turnover, volume, spread_pct=None)`. `observed_at`은 우리 수신 시각이며 공급자 시각을 쓰지 않는다.
- **스프레드는 항상 `None`(UNKNOWN)**: 거래대금·거래량으로 추정하지 않는다. 호가(order book)가 연결되기 전에는 Scout 기본 설정(`require_liquidity=True`)이 모든 종목을 `LIQUIDITY_SPREAD_UNKNOWN`으로 제외한다. 우회하지 않았다.
- 참고(Scout 동작): `require_liquidity=False`만으로는 스프레드 검사가 꺼지지 않는다. 유동성 사실이 있으면 임계값이 계속 적용되고, `max_spread_pct=None`까지 명시해야 검사가 생략되며 그 경우에도 후보는 `fully_eligible=False`, `LIQUIDITY_UNVERIFIED` 갭이 붙는다.
- 같은 종목의 순위 행이 서로 다른 값이면 사실을 만들지 않는다(`DUPLICATE_RANKING_ROW_CONFLICT`). `received_at > as_of`이면 사실 없음(`RANKING_NOT_YET_RECEIVED`).

## 3. 준비도 (`readiness_from_rows`)

기준: `as_of`(aware)의 KST 날짜 세션 `[09:00, 15:30)`. `required_bars` = 분 끝이 `as_of - grace`(기본 5초, 수집기가 분 끝 약 2초 뒤에 저장하기 때문) 이전인 분의 수. DAY-01은 다음 5분 경계에서 따로 판단하며 계속 fail-closed다.

| 항목 | 정의 |
|---|---|
| `completed_1m_bars` | 09:00부터 **연속**으로 알려진 봉 수(공백이 있으면 거기서 멈춤) |
| `opening_range_ready` | 09:00~09:14 15개 모두 알려짐(충돌 없음) |
| `vwap_ready` | `required_bars`까지 연속이며 VWAP 방식이 가능 |
| `observed_at` | 알려진 사용 가능 봉 중 가장 늦은 가용 시각(사실의 신선도). 공백이 있어도 피드가 살아 있으면 신선, 피드가 멈추면 오래됨(Scout `STALE`) |
| `ready` | opening AND vwap AND (엄격 모드면 최신 필요 봉이 정시 도착) |

**봉 선택(미래정보 차단)**: 가용 시각 = `max(available_at, collected_at)`가 `as_of` 이후인 행은 사용하지 않는다(그 분이 나중 행으로만 존재하면 `LATE_NOT_YET_AVAILABLE`로 보고하고 내용은 쓰지 않음). 이후 행을 덧붙여도 결과가 같다(테스트). `event_at`(시장), `available_at`·`collected_at`(수신)은 바꾸지 않는다.

**결함 분류(행 단위, 사용하지 않음)**: `INCOMPLETE_BAR`(분 끝 전에 찍힘), `UNALIGNED_EVENT`, `NOT_1M`, `NO_SOURCE`, `COLLECTED_BEFORE_AVAILABLE`, `INVALID_ROW`(OHLC·숫자 검증은 기존 `adapt_rows`/`Candle.validate`). 중복: 값이 같으면 하나로, **다르면 `DUPLICATE_CONFLICT`**로 그 분을 막는다(양쪽 모두 사용 안 함, 준비도 불가).

**상태 코드(종목별 `codes`)**: `NO_CANDLES`(분봉 없음), `OPENING_MISSING`(개장 구간 누락), `OPENING_NOT_DUE`(아직 09:15 전), `SESSION_GAP`(세션 중 누락), `LATE_NOT_YET_AVAILABLE`(지연되어 아직 수신 전), `LATE_ARRIVAL`(최대 지연 초과로 늦게 도착, 사용은 가능), `NOT_ON_TIME_FOR_STRICT_MODE`, `VWAP_NEEDS_TURNOVER`, `VWAP_APPROXIMATE`, `VWAP_APPROXIMATION_NOT_ALLOWED`, 위 결함 코드. 전체 노트: `CANDLE_SYMBOLS_NOT_IN_RANKING`, `ROWS_FROM_OTHER_SESSION_IGNORED`, `SYMBOL_ALIAS_AMBIGUOUS`.

종목 코드는 `strip().upper()`로 비교하고 사실의 `symbol`은 순위 스냅샷의 표기를 쓴다. 순위에 없는 종목의 분봉은 무시(노트), 분봉이 없는 순위 종목은 `NO_CANDLES`로 사실을 만들지 않는다(Scout `DAY01_READINESS_MISSING`).

## 4. VWAP 모드

- `vwap_mode='exact'`: 필요한 모든 분에 `turnover`가 있어야 한다. Toss 1분봉 행에는 없으므로 실제 자료로는 **준비 안 됨**(`VWAP_NEEDS_TURNOVER`). 가격×거래량으로 대체하지 않는다.
- `vwap_mode='typical_approximation'`: `allow_approximate_vwap=True`를 명시한 경우에만 준비 판정을 허용하고 `vwap_basis='TYPICAL_APPROXIMATION'`, 코드 `VWAP_APPROXIMATE`로 표시한다. 허용하지 않으면 `VWAP_APPROXIMATION_NOT_ALLOWED`로 준비 안 됨. `DayOneFacts`에는 근사 여부 필드가 없으므로 **Scout 입력만 보고 정확 VWAP로 읽으면 안 되며** 근사 여부는 `SymbolReadiness.vwap_basis`가 가진다.
- `config.vwap_mode`와 `vwap_mode` 인자가 다르면 거부한다.

## 5. DAY-01 실행 모드와의 정합 (실제 수집 지연의 영향)

실제 수집기는 분 끝 약 2초 뒤에 봉을 저장하므로 `available_at ≠ event_at+1분`이다. DAY-01의 `next_open`·`legacy_close`는 5분 경계에 **정시 도착**한 봉에서만 신호를 평가하므로 이런 자료로는 신호가 나오지 않는다. 그래서 `config.execution`이 엄격 모드이고 최신 필요 봉이 정시가 아니면 `ready=False`, `DayOneFacts.vwap_ready=False`로 두어 Scout가 DAY-01이 거래할 수 없는 종목을 승격하지 않게 한다. `research_estimated_open`은 지연을 허용(`research_max_observation_lag_minutes`)한다.

테스트로 확인한 정합성(`evaluate_entry`와 대조): 개장 공백 → 준비도 opening False ↔ DAY-01 `INCOMPLETE_OPENING_RANGE`; 현재 5분 구간 공백 → DAY-01 `NO_FRESH_FIVE_MINUTE_BAR`; 과거 구간 공백 → `INCOMPLETE_SESSION_VWAP`; exact + 거래대금 없음 → `MISSING_EXACT_TURNOVER`. 준비도가 True인 합성 입력에서 DAY-01은 이 네 사유로 막지 않는다.

## 6. 한계 (실제 자료 없이는 검증 불가)

- 실제 `ml_raw_candles`의 범위·결측률, 실제 수집 종목 목록(Scout 후보가 수집 대상인지), 마지막 봉의 확정 여부(첫 저장이 영구 보존), 호가 응답 구조는 미확인이다.
- 거래대금·거래량은 순위 시점(`rankedAt`)의 당일 누적이며 봉 시점의 값이 아니다.
- 이 모듈은 DB를 읽지 않는다. 행을 시점 기준으로 읽는 일(`available_candles`)은 별도 승인된 연결 작업이다.
