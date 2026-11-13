# DAY01 SCOUT — 집중 WATCH 후보 탐색 사양 (제안 v1)

상태: **구현됨(AI 구현 완료) · 합성 단위 테스트 통과(로컬) · Codex 독립 검토 대기 · 실제 API/운영 미연결**. 모듈 `auto_trader/day01_scout.py`, 스키마 `day01-scout-1`.
이 기능은 **WATCH 후보 선정**이다. 매수 신호, 신규 전략, 주문 실행, 수익 예측이 아니며 어떤 주문 경로에도 연결되지 않는다.
실제 API·DB·UI·worker 연결은 포함하지 않는다(향후 production adapter는 아래 §9).

## 1. 범위와 비범위

- 입력: 호출자가 주입한 국내 거래대금 순위 스냅샷(기존 `_domestic_trading_amount_rankings` 응답 계약), 종목 상태·유동성·DAY01 준비도 사실, 포트폴리오.
- 출력: 불변 `ScoutResult` — 신규 진입 WATCH 후보 0..target개, 보유 종목 모니터, 합집합, 사유 목록, 제공된 순위 커버리지.
- 모듈은 표준 라이브러리와 순수 `RiskPolicy`만 import한다. settings/database/broker/app import 없음, I/O·스레드·전역 상태 없음, 시계(`datetime.now`) 호출 없음 — 모든 시각은 `as_of` 인자.

## 2. 재사용하는 기존 계약 (읽기 전용 확인)

`TossMarketData._domestic_trading_amount_rankings()`: `GET /api/v1/rankings` `type=MARKET_TRADING_AMOUNT, marketCountry=KR, duration=1d, count=100`, 응답 `result.rankedAt`(제공자 시각)·`result.rankings[]`(`rank`, `symbol`, `tradingAmount`, `tradingVolume`, `price.lastPrice`, `price.changeRate`), 30초 캐시, `(rankedAt, rankings)` 튜플 반환.
종목 사실은 `/api/v1/stocks`의 `securityType`, `isCommonShare`, `status`, `koreanMarketDetail.{liquidationTrading,krxTradingSuspended}`, `strategy_presets.build_preset`의 보통주·ACTIVE·정지/정리매매 제외·예산 필터와 같은 의미를 따른다.

**중요한 계약 공백:** 기존 캐시는 `rankedAt`만 보관하고 **실제 수신 시각(received_at)** 이 없다. `rankedAt`(제공자 기준)과 `received_at`(우리가 받은 시각)은 별개이며, selector는 둘을 모두 요구한다. 캐시된 스냅샷을 재사용할 때 `received_at`을 현재 시각으로 다시 찍지 않고 원래 수신 시각을 보존해야 한다.

## 3. 타입 (모두 `@dataclass(frozen=True)`, 컬렉션은 tuple)

```
ScoutConfig(provider="toss", target_count=12 (10..15), ranking_depth=50 (1..50), quantity=1,
            fee_rate, buy_slippage_rate (Decimal), max_snapshot_age=120s, max_receive_lag=60s,
            max_fact_age=300s, max_portfolio_age=60s,
            require_liquidity=True, min_turnover=100억, min_volume=1, max_spread_pct=1,   # 기본값 = 필수
            require_dayone_readiness=True, min_completed_1m_bars=15, require_capital_check=True,
            sort_mode="SCORE"|"RANK", score_weights=(rank, bars, opening_range, vwap))
RankedRow(rank, symbol, turnover, volume, price, change_rate)
RankingSnapshot(snapshot_id, provider, source, received_at, provider_ranked_at|None, requested_count,
                rows(순위·symbol 정렬), defects, content_digest)   # digest는 id·수신시각과 무관
FetchResult(payload, received_at)                                   # 원래 수신 시각 필수
InstrumentFacts / LiquidityFacts / DayOneFacts (symbol, observed_at, ...)
Position(symbol, quantity, market_value, market_value_observed_at, price, price_observed_at, defects)
Portfolio(observed_at, cash, reserved_cash, positions, daily_loss, max_positions, min_cash,
          min_cash_ratio_pct, total_asset, daily_loss_limit, reserved_symbol_exposure,
          reserved_total_exposure, reserved_slots, pending_orders_known)
```
`observed_at`은 **우리에게 사용 가능해진 시각**(availability)이다. 제공자 이벤트 시각이 아니다. 순위 행의 가격은 원래 수신 시각에만 사용 가능했던 값으로 취급한다.
금액 한도는 기존 `RiskPolicy`가 읽는 config 객체(`max_order_amount`, `max_symbol_amount`, `max_total_investment`)를 `risk_config`로 주입한다. `RankedRow`·`RankingSnapshot`·`ScoutState`는 모든 생성 경로에서 엄격 검증하며(bool 순위 거부, 행/결함은 tuple로 복사, `content_digest`는 내용과 일치해야 함) 가변 컨테이너가 내부로 새지 않는다.
각 한도는 검증된 유한·비음수 Decimal(int/Decimal/str)이어야 하며 없음·float·bool·NaN/Inf·음수는 `LIMIT_INVALID`로 거부한다.

검증: 숫자는 `int`/`Decimal`/`str`만 허용(float·bool 거부), 유한·비음수, 수량·순위는 `type(x) is int`, datetime은 tz-aware만(UTC로 정규화). 설정/포트폴리오 구조 오류는 `ValueError`, 순위 행 오류는 행을 버리고 `defects`에 기록. `Position`의 값·가격 필드가 잘못되면 `None` + `defects`가 되어 **보유 모니터는 유지**된다(0으로 취급하지 않음).

## 4. 공개 함수

```
snapshot_from_rankings(ranked_at, rankings, *, received_at, snapshot_id=None, provider="toss", requested_count=100)
snapshot_from_payload(payload, *, received_at, ...)      # {"result":{"rankedAt","rankings"}} 또는 (rankedAt, rankings)
select_watchlist(as_of, snapshots, *, instruments, liquidity, dayone, portfolio, config, risk_config) -> ScoutResult
monitor_positions(as_of, portfolio, snapshot, snapshot_status, config) -> tuple[HeldMonitor]
ScoutState.empty(max_history=8); refresh(state, as_of, *, new_snapshots, facts..., portfolio, config, risk_config) -> (ScoutState, RefreshReport)
ingest_fetch(state, fetch, *, clock) -> (ScoutState, FetchOutcome)
to_dict(obj)   # JSON 직렬화(Decimal=str, datetime=ISO UTC), 파일/DB 저장 없음
```
`portfolio`는 `Portfolio` 또는 그 iterable이다(최신 `observed_at <= as_of` 선택, 같은 시각의 서로 다른 값은 CONFLICT: 보유 종목은 합집합으로 모니터링하되 수량은 최댓값, 값이 다른 필드는 UNKNOWN + `*_CONFLICT` 플래그로 두어 입력 순서가 가격·평가금액을 고르지 못하게 함).
`ingest_fetch`의 `fetch()`는 반드시 `FetchResult`(원래 수신 시각 포함)를 돌려줘야 한다. 단순 payload/튜플은 `FETCH_RESULT_REQUIRED`로 거부된다(캐시를 now()로 재스탬프하지 않기 위함). **타임아웃은 fetch 책임**이며 production은 비차단·제한시간 있는 호출을 주입해야 한다. 모든 예외는 `FETCH_FAILED`(예외 타입 이름만 기록)이고 상태 불변이며 보유 모니터에는 영향이 없다. 이번 작업은 production fetch를 제공하지 않는다.

## 5. 시간 계약 (`as_of` 결정성)

- `received_at > as_of`인 스냅샷은 **완전히 무시**한다(뒤에 미래 스냅샷을 덧붙여도 결과 불변, prefix invariance).
- 정규화(`_canonical_snapshots`): 미래 제거 → 다른 provider 거부 → **같은 내용(provider+digest)은 id와 무관하게 가장 이른 수신 하나로 합침**(재전달·캐시 재스탬프가 나이를 갱신하지 못함). 같은 id 또는 같은 제공자 기준(`rankedAt`)에 **다른 내용**이면 분쟁이며 입력 순서·이력/신규 구분과 무관하게 first-wins 없이 처리한다.
- 정규화된 스냅샷 중 `(received_at, provider_ranked_at(None 최소), digest, id)` 최대가 latest다. latest가 분쟁에 속하면 `INVALID_SNAPSHOT`(`SNAPSHOT_CONFLICT`)로 차단. latest가 무효이면 **차단**하며 더 오래된 스냅샷으로 대체하지 않는다. 다른 provider의 스냅샷은 사전에 명시적 거부(`PROVIDER_MISMATCH`)한다.
- latest 판정 순서: 분쟁 → `INVALID_SNAPSHOT`; `provider_ranked_at` 없음/형식 오류 → `UNKNOWN_PROVIDER_TIME`; `provider_ranked_at > received_at` → `INVALID_PROVIDER_TIME`; ranked의 KST 날짜 != as_of KST 날짜 → `DAY_ROLLOVER`; `received_at - ranked_at >= max_receive_lag` → `STALE`(새 수신이 오래된 제공자 기준을 되살리지 못함); `as_of - received_at >= max_snapshot_age` → `STALE`. 모든 경계는 **배타적**(나이 == 한도이면 stale). 실패 시 신규 후보 0개.
- 사실(상태·유동성·준비도)은 `observed_at <= as_of` 중 최신만 사용하며 미래 사실은 무시. 같은 최신 시각에 서로 다른 사실이 둘 이상이면 `*_CONFLICT`(입력 순서 무관, fail-closed). 나이 >= `max_fact_age`이면 `*_STALE`.
- 포트폴리오: 최신 `observed_at <= as_of`만 사용, 나이 >= `max_portfolio_age`이면 `STALE`, 없으면 `MISSING`, 같은 시각에 다른 값이면 `CONFLICT`, `observed_at > as_of`인 포트폴리오는 **모든 출력에서 완전히 무시**한다(보유 모니터·합집합 포함, 예외 없음). 인과적으로 사용 가능한 포트폴리오가 없으면 입력이 없는 경우(`MISSING`)와 출력이 동일하다. FRESH가 아니면 `require_capital_check`에서 신규 후보 0개.
- `entry_expires_at` = 스냅샷 수신+max_snapshot_age, 제공자 기준 시각+lag+age, 사용한 모든 사실과 포트폴리오의 observed_at+한도, 다음 KST 자정 중 **최솟값**. `as_of < entry_expires_at`일 때만 유효(배타적). 보유 모니터에는 만료가 없다.

## 6. 선정 파이프라인 (각 단계 사유 기록)

1. 스냅샷 선택(§5). 행 검증: 순위 `type is int >= 1`; 같은 순위/같은 symbol이 둘 이상이면 관련 **모든 행 제외**(`CONFLICT_RANK`/`CONFLICT_SYMBOL`).
2. 제공된 순위 1..`ranking_depth`(<=50)만 대상. 51위 이후를 끌어올리거나 재순위하지 않는다. `Coverage(expected, present, missing_ranks, excluded_conflict_rows, complete)`를 보고하고 `marketwide_complete`는 항상 False. **순위는 제공자 쿼리(`excludeInvestmentCaution=true`)로 필터된 목록 기준**이므로 `basis=PROVIDER_FILTERED_EXCLUDE_INVESTMENT_CAUTION`이며 필터 없는 시장 Top50이라고 주장하지 않는다.
3. 품질 게이트(모르면 탈락): KOSPI/KOSDAQ, KRW, STOCK, 보통주, ACTIVE, 거래정지·정리매매 아님.
4. 유동성 게이트는 **기본 필수**: 신선한 `LiquidityFacts`의 turnover/volume/spread가 모두 알려져 있고 임계값을 통과해야 한다(임계값이 비어 있거나 0이면 `ScoutConfig` 생성 거부). 없음/불명 → 탈락. 명시적 opt-out(`require_liquidity=False`)이면 사실이 있어도 항상 `fully_eligible=False`, `verification_gaps`에 `LIQUIDITY_UNVERIFIED`가 붙는다(DAY01 준비도·자본 opt-out도 동일). 순위 거래대금은 호가 깊이가 아니다.
5. DAY01 준비도(완료 1분봉 수·시가범위·VWAP)는 watch 준비도일 뿐 신규 매수 전략·수익 점수가 아니다. 기본 필수, opt-out 시 `DAY01_READINESS_UNVERIFIED`. 점수는 제공 필드의 투명한 합산(`score_components`).
6. 자본 게이트: `total = price*(1+slippage)*quantity*(1+fee)`. 개별 기록: `ORDER_LIMIT`, `SYMBOL_EXPOSURE`(보유가치+예약 노출 포함), `TOTAL_INVESTMENT`(보유가치+예약 총노출), `PENDING_ORDERS`, `CASH`(cash-reserved_cash-total >= min_cash), `CASH_RATIO`, `MAX_POSITIONS`(보유+예약 슬롯+신규), `DAILY_LOSS`. 노출 == 한도는 허용(`RiskPolicy`와 동일), 일일 손실 == 한도는 차단. 보유 `market_value`가 없음/NaN/미래/오래됨이면 0으로 보지 않고 `INVESTED_UNKNOWN`으로 탈락. 예약 현금만으로 대기 주문 노출을 대신하지 않으며 `reserved_symbol_exposure`/`reserved_total_exposure`/`reserved_slots`/`pending_orders_known`을 명시해야 한다. 개별 종목 적격일 뿐 **동시 주문을 보장하지 않는다**.
   `require_capital_check=False`로 opt-out하면 `CAPITAL_UNVERIFIED` 갭이 붙는다.
7. 보유 종목은 신규 후보에서 제외(`HELD_MONITOR_ONLY`).
8. 정렬 `SCORE`: `(-score, rank, symbol)`, `RANK`: `(rank, symbol)`. 상위 `target_count`개, 부족하면 `SHORTAGE:n/target`과 사유 집계, 안전하지 않은 채우기 없음.

## 7. 보유 모니터 (독립)

모든 `Position`은 순위 퇴출·종류·예산 실패와 무관하게 항상 `HeldMonitor`로 남는다: `rank`(제공 순위에 없으면 None), `in_ranking_depth`, 가격 관측 시각과 `price_status`(FRESH/STALE/UNKNOWN/FUTURE), `snapshot_status`, `flags`. 필드에는 매도/청산/제거 지시가 없다(`monitor_only=True`). 개수는 15·50 제한과 무관. 합집합 `watch_union`은 symbol 중복 없이 `sources=("CANDIDATE",)`, `("HELD",)`, 두 가지를 모두 가진 형태이며 진입 신호 필드는 없다.

## 8. 증분 refresh

`refresh(state, as_of, new_snapshots=…, …)`:
- `as_of`가 `state.last_as_of`보다 과거면 `AS_OF_REGRESSION`으로 거부하고 상태 불변.
- 스냅샷 id가 같고 내용이 같으면 멱등(무시), 같은 id에 다른 내용이면 `SNAPSHOT_ID_CONFLICT`로 새 것을 거부.
- 입력 순서가 뒤바뀐 스냅샷은 저장되지만 선택은 §5의 최신 규칙에 따라 단조(monotonic).
- 같은 내용이 다시 오면(같은 id든 다른 id든, 캐시된 rankedAt이든) **가장 이른 원래 수신 시각을 유지**한다(`snapshot_changed`는 내용 digest 기준). 분쟁 내용은 거부·삭제하지 않고 나란히 저장한 채 선택 시점에 fail-closed로 판정한다(도착 순서 무관).
- **분쟁 증거(claims)**: 병합 시 모든 스냅샷의 (id, 제공자 기준 `rankedAt`) 사용 기록을 `SnapshotClaim`(provider, kind, key, digest, 가장 이른 수신)으로 **중복 제거·가지치기 이전에** 기록하고 `ScoutState.claims`에 유지한다. 같은 내용의 id 별칭도 claim으로 남으므로 내용 합치기나 `max_history` 절단이 분쟁을 숨길 수 없다(D-S8/D-S9). 선택 시 `received_at <= as_of`인 claim만 사용하며(미래 충돌은 과거 결과를 바꾸지 않음), 같은 id 또는 기준에 digest가 둘 이상이면 해당 모든 내용이 분쟁이고 latest가 분쟁이면 `INVALID_SNAPSHOT`(오래된 스냅샷으로 대체 없음). `select_watchlist(..., claims=(), quarantined=False)`로 외부 증거를 받을 수 있으며 claim이 없으면 입력 스냅샷에서 직접 도출한다.
- **증거 상한**: claim은 `MAX_CLAIMS`(4096)까지 보관하고 KST 날짜가 바뀌면 이전 날짜 claim을 정리한다. 알려진 claim이 상한을 넘으면 증거를 잊지 않고 **격리(quarantine)** 상태(`claims_overflow_day`)로 전환해 같은 KST 날짜가 끝날 때까지 신규 후보 0개(`CLAIMS_OVERFLOW_QUARANTINE`)로 둔다. 보유 모니터는 영향받지 않는다. 미래 스냅샷의 claim은 보관된 미래 버퍼에 있는 것만 유지한다.
- 상태 저장·가지치기는 `as_of` 이전에 수신된 스냅샷만 `max_history`에 포함한다. 아직 수신되지 않은(미래) 스냅샷은 별도 버퍼(최대 `max_history`, 가장 이른 것 우선)에만 두며 알려진 이력을 밀어내거나 결과에 영향을 주지 않는다.
- 보존: 날짜가 지난 스냅샷은 정리(`DAY_ROLLOVER`), 상태 크기 상한 `max_history`(기본 8).
- `RefreshReport`: `added`/`removed`/`unchanged` symbol 튜플, 선택된 snapshot_id 변경 여부(`snapshot_changed`), 거부 목록, 새 `ScoutResult`.
- 새 스냅샷이 없거나 stale이면 이전 후보를 신선한 것으로 재사용하지 않는다 — 결과 상태가 STALE이며 후보 0.
- 보유 모니터는 매번 재계산되고 후보 만료와 독립이다.

## 9. 향후 production adapter (이번 범위 아님)

`TossMarketData`에 수신 시각을 보존하는 얇은 wrapper를 추가해 `fetch()`가 `FetchResult(payload, received_at)`을 돌려주게 한다(30초 캐시가 `received_at`을 다시 찍지 않도록). 종목 사실은 `/api/v1/stocks` 응답을 `InstrumentFacts`로 변환하며 변환 시점 `observed_at`을 기록한다. 두 변환은 별도 작업·승인·격리 환경이 필요하다.

## 10. 제한

- 합성 데이터만 검증. 실제 API 응답·시장 상태·휴장일은 검증하지 않음(휴장 API 없음, 같은 KST 날짜만 확인).
- 순위 거래대금은 실제 호가 깊이를 보장하지 않으며 개별 종목 자본 적격이 동시 주문 가능성을 뜻하지 않는다.
- 제공되지 않은 과거 순위는 생성하지 않는다. 가격·거래량에서 과거 순위를 추정하지 않는다.
