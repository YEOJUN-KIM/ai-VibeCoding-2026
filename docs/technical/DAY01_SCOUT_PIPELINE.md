# DAY01 Scout 통합 판단 흐름 (합성 검증 전용)

상태: 구현과 합성 테스트만. 실제 수집·DB·API·PAPER·LIVE 연결 없음.
모듈 `auto_trader/day01_scout_pipeline.py`(순수), 테스트 `tests/test_day01_scout_pipeline.py`.

## 1. 흐름

`run_scout_pipeline(as_of, snapshot, candle_rows, orderbook_results, instruments, portfolio, day01_config, clock_uncertainty, ...)`

1. 한도 검사: Scout 설정은 `scout_config_for(day01_config)`. `portfolio`/위험 한도가 DAY-01 `Config`와 다르면 `ValueError`. Scout 덮어쓰기는 `target_count`, `ranking_depth`만 허용(게이트·한도 완화 불가).
2. 분봉: `build_market_inputs`(CANDLE-01). 종목은 순위 종목 ∪ 보유 종목. 완결성·VWAP·이상치·시계 불확실성 판정은 기존 코드 그대로.
3. 호가: `with_spread`(ORDERBOOK-01)를 `as_of` 시점으로 적용. 없음/실패/`as_of` 이후 수신/`as_of`에서 오래됨/중복 충돌이면 스프레드 UNKNOWN → Scout가 제외.
4. 선정: 기존 `select_watchlist`. 결과 `ScoutResult`는 수정 없이 `WatchTimeline`에 넣어 `replay(entry_allowed=...)`에 연결.
5. 보고: 순위 행마다 `SymbolDecision`(PASS/EXCLUDED, 제외 사유, 분봉 코드, VWAP 근거, 호가 상태, 메모)과 전체 `PROCEED`/`HOLD`(+사유).

PROCEED는 통과 후보 1개 이상이고 시계 불확실성을 지정한 경우뿐이다. 그 외는 모두 HOLD.

## 2. 안전 동작 (모두 테스트로 확인)

| 상황 | 결과 |
|---|---|
| 분봉 없음 / 개장 구간 결손 / 세션 결손 | 제외(`NO_CANDLES`, `OPENING_MISSING`, `SESSION_GAP` → `DAY01_*` 사유) |
| 호가 없음 · UNKNOWN · ANOMALY · 오래됨 · 미수신 | 제외 `LIQUIDITY_SPREAD_UNKNOWN`. 순위 거래대금을 호가 깊이로 대체하지 않음 |
| VWAP 거래량 0 | 제외 `VWAP_ZERO_VOLUME` |
| 가격 이상치(±30%) | 제외 `IMPLAUSIBLE_PRICE` |
| `clock_uncertainty=None` | **HOLD**, 통과 후보 없음. Scout에도 준비도 사실을 넘기지 않음 |
| `as_of` 이후 분봉·호가·종목 사실·순위 | 무시 |
| 순위 후보 ≠ 수집 종목 | `NOT_IN_COLLECTION_UNIVERSE` 메모 + `NO_CANDLES`. 수집만 되고 순위에 없는 종목은 `CANDLE_SYMBOLS_NOT_IN_RANKING` 메모 |
| 근사 VWAP | 명시 허용 필요. 통과 시 `APPROXIMATE_VWAP` 표시, `approximate_vwap_symbols`로 분리 |

## 3. 이번 작업에서 확인한 한계 (미해결)

1. **엄격 모드와 시계 불확실성**: `legacy_close`/`next_open`은 분 종료 시각에 정확히 찍힌 봉이 필요하므로, `clock_uncertainty>0`이면 항상 막힌다(안전하지만 사용 불가). 엄격 모드로 통과하려면 시계 동기를 보증하는 명시적 `timedelta(0)`이 필요하다.
2. **30% 이상치 필터 오차단**: 신규 상장 첫날처럼 실제로 넓은 봉도 제외된다(fail-closed). 정상 데이터 오차단 가능성은 남는다.
3. **호가 4종목 한도**: ORDERBOOK-01 펜스는 요청당 4종목이다. Scout `target_count`는 10~15이므로 호가 확인 종목만 통과한다. 상위 후보 4개로 좁히는 정책이 필요하다(미결정).
4. **근사 VWAP**: Toss 분봉에 거래대금이 없으면 정확 VWAP은 불가능하다. 근사는 명시적으로 허용해야 하며 결과에 표시된다.
5. **`clock_uncertainty` 값 자체**: 어떤 숫자가 적절한지는 정책 결정이다. 이 작업은 기본값을 두지 않고 미지정을 HOLD로 처리한다.
6. 합성 데이터 검증이다. 실제 `ml_raw_candles`, 실제 호가 응답, 휴장일은 미검증이다.
