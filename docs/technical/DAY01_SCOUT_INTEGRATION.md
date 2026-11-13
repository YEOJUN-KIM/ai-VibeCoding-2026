# DAY01 Scout ↔ DAY-01 통합 계약 (합성 검증 전용)

상태: 구현 및 합성 테스트만. 실제 순위 수집·운영 저장·PAPER 주문 연동은 범위 밖이다.
모듈 `auto_trader/day01_scout_link.py`(순수), 테스트 `tests/test_day01_scout_link.py`.

## 1. 연결 구조

Scout는 WATCH 후보와 보유 모니터만 만들고, 매매 규칙은 DAY-01이 소유한다. 연결은 다음 세 가지뿐이다.

1. `scout_config_for(day01_config)` / `portfolio_limits_for(day01_config)`: 수량, 수수료율, 슬리피지, 최대 포지션, 최소 현금비율, 일일 손실 한도를 DAY-01 `Config`에서 그대로 가져온다. `Config` 자체를 Scout `risk_config`로 넘기면 주문·종목·총 투자 한도도 같은 숫자다. `limit_mismatches()`가 불일치를 이름으로 보고한다.
2. `WatchTimeline(results).entry_allowed(symbol, at)`: `replay(..., entry_allowed=...)`에 연결하는 신규 **진입 거부권**. `at` 시점까지 알려진 가장 최근 Scout 결과가 OK이고 해당 종목이 후보이며 `at < entry_expires_at`일 때만 참이다. 더 새로운 결과가 종목을 빼거나 STALE이거나 만료되면 오래된 결과로 되돌아가지 않는다(fail-closed).
3. `WatchTimeline.universe()`: 타임라인 전체의 후보 ∪ 보유 종목. 데이터 필터일 뿐 선정 신호가 아니다. 진입은 항상 2번 게이트를 거치므로 거래·신호에는 미래 누출이 없다. 단 replay 보고서(자산곡선 시각, reasons)는 universe 전체 종목의 데이터를 반영하므로(예: 지연 도착 캔들), 실행 간 prefix 불변성을 비교하려면 **동일하게 사전 선언한 universe**를 써야 한다.

DAY-01 변경은 `replay`의 선택 인자 `entry_allowed=None` 하나다. None이면 동작이 이전과 동일하고(기존 테스트로 확인), 값이 있으면 신규 진입 신호만 `SCOUT_NOT_WATCHED`로 거부한다. 청산·평가·손절·세션 청산은 이 함수를 참조하지 않으므로 후보에서 빠진 보유 종목도 계속 감시된다.

## 2. 시각 계약 매핑

| 개념 | Scout | DAY-01 |
|---|---|---|
| 공급자 기준 시각 | `provider_ranked_at` (`rankedAt`) | 해당 없음 |
| 최초 수신 시각 | `received_at` (재스탬프 금지) | `Candle.available_at` |
| 신호 가용 시각 | `as_of`, 후보 `entry_expires_at` | 결정 시각 `at` (5분 경계, 완료 캔들 기준) |

`received_at > as_of`인 스냅샷은 Scout가 무시하고, `WatchTimeline`은 `as_of <= at`인 결과만 본다. 따라서 결정 시각 이후에 수신된 순위는 그 결정에 영향을 주지 못한다(테스트: 수신 09:20:30 / 결정 09:20:00 → NO_SNAPSHOT, 진입 거부).

## 3. 한도 일관성 관찰 (코드 대조)

- 수수료: DAY-01은 `ROUND_DOWN` 원 단위, Scout는 반올림 없는 값 → Scout가 1원 미만 보수적. 경계에서 Scout가 DAY-01이 받아들일 후보를 거부할 수 있으나 반대는 없다. 허용.
- 종목 한도: DAY-01은 기존 종목 가치 0 기준(종목당 1회 진입), Scout는 보유 가치+예약 노출 포함 → Scout가 더 엄격. 허용.
- 일일 손실: DAY-01은 순청산자산 변화 `<= -한도`로 차단, Scout는 호출자가 준 `daily_loss`(양수 손실액) `< 한도`일 때만 허용. 어댑터가 **같은 정의**(순청산자산 기준 손실액)로 채워야 한다. 필수 조건.
- 현금 비율: DAY-01은 매도 비용 포함 청산 후 자산 분모, Scout는 호출자가 준 `total_asset`과 매수 후 현금. 근사이며 DAY-01이 신호 시점에 다시 검사한다. Scout 통과가 주문 가능을 보장하지 않는다(`joint_orders_guaranteed=False`; 테스트: max_positions=1에서 두 후보 중 하나만 진입).
- Scout 가격은 순위 행 가격, DAY-01 한도 검사는 신호 캔들 종가: 경계 부근에서는 둘이 다를 수 있다. DAY-01이 최종 결정한다.

## 4. 기존 순위 응답과 Scout 입력 계약 (읽기 전용 확인)

`TossMarketData._domestic_trading_amount_rankings()`는 `excludeInvestmentCaution=true`, `count=100` 요청 후 `(rankedAt, 정렬된 rankings)`를 30초 캐시로 반환한다. Scout 입력은 `RankingSnapshot`(수신 시각 필수)이다. 차이:

- 기존 캐시에는 **`received_at`이 없다**. 캐시 적중 시 원래 수신 시각을 보존해야 한다(`FetchResult`).
- 필드(`rank`, `symbol`, `tradingAmount`, `tradingVolume`, `price.lastPrice`, `price.changeRate`)는 `snapshot_from_payload`가 그대로 받는다.
- `/api/v1/stocks`의 종목 상태와 호가·유동성·스프레드, 1분봉 준비도는 순위 응답에 없다. 별도 어댑터가 필요하다.
- Scout 사양 §2의 요청 설명에는 `excludeInvestmentCaution`이 빠져 있다. 코드(`SOURCE`)는 포함하므로 문서 표현만 어긋난다(범위 밖).

## 5. 실제 수집 연결을 위한 어댑터·기록 계약

순위 fetch와 기록은 [DAY01_SCOUT_LIVE_SOURCE.md](DAY01_SCOUT_LIVE_SOURCE.md)에서 구현·검증했다. 아래 2~4번은 여전히 미구현이다.

어댑터 4종(각각 별도 작업·승인·격리 환경 필요):

1. 순위 fetch: 비차단·시간 제한 호출, 반환 `FetchResult(payload, received_at)`. 캐시 적중 시 최초 수신 시각 유지.
2. 종목 사실 변환: `/api/v1/stocks` → `InstrumentFacts`, 변환 시각 `observed_at`.
3. 유동성/준비도: DAY-01이 쓰는 1분봉 저장소와 같은 원천에서 `LiquidityFacts`, `DayOneFacts` 생성(Candle의 `available_at` 기준).
4. 포트폴리오: 포지션, 예약 노출, 일일 손실(§3 정의)을 `Portfolio`로, 가용 시각 기록.

추가 전용(append-only) 기록 최소 필드: 스냅샷 `snapshot_id`, `provider`, `source`, `requested_count`, `received_at`, `provider_ranked_at`, 원본 행(원문 보존), `content_digest`; Scout 결과 `as_of`, 상태, 후보·보유 목록과 `entry_expires_at`; DAY-01 입력 캔들 `event_at`, `available_at`. 어느 필드도 덮어쓰지 않는다.

## 6. 한계

- 합성 데이터 검증이다. 실제 응답 형식, 휴장일, 호가 깊이는 미검증이다.
- `WatchTimeline`은 Scout 결과를 신뢰한다. 결과 자체의 정당성은 Scout 검증 범위다.
- 운영 연결(수집 워커, DB, PAPER 주문)은 연결되지 않았다.
