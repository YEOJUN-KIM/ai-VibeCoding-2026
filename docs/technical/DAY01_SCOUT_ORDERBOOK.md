# DAY01 Scout 호가 변환기와 엄격한 요청 울타리 (ORDERBOOK-01B)

상태: 구현·합성 테스트 완료. **실제 Toss API는 한 번도 호출하지 않았다.** 운영 DB·워커·주문·PAPER 연동과 Scout 정책 변경은 없다.
코드: `auto_trader/day01_scout_orderbook.py`, `auto_trader/day01_scout_source.py`(울타리 보강), 테스트 `tests/test_day01_scout_orderbook.py`.

## 1. 공식 계약 (2026-11-13에 정적 문서 페이지로 확인, 호출 아님)

출처: Toss Open API 레퍼런스 `MarketDataApi.getOrderbook`, `OrderbookResponse`, `OrderbookEntry`, 개요(`overview.md`).

| 항목 | 내용 |
|---|---|
| 요청 | `GET https://openapi.tossinvest.com/api/v1/orderbook?symbol=<종목>` (OAuth2 client credentials) |
| `symbol` | 필수 1개. KRX는 6자리 코드(숫자 또는 영문·숫자, 예 `005930`, `0101N0`). 영문 대·소문자, 숫자, `.`, `-`만 허용 |
| 응답 | `result.timestamp`(Date, "데이터 미제공 시 null"), `result.currency`, `result.asks[]`(낮은 가격순), `result.bids[]`(높은 가격순) |
| 호가 한 줄 | `price`(BigDecimal), `volume`(BigDecimal, "잔량") |
| 요청 한도 | 그룹 `MARKET_DATA`, 클라이언트×그룹당 초당 15회(문서상 예고 없이 변경 가능). 429는 `Retry-After` 대기 |
| 오류 | `{"error": {"requestId","code","message","data"}}`, 예: `invalid-token`(401), `rate-limit-exceeded`(429) |

**문서에 없어서 UNKNOWN으로 둔 것**: timestamp 문자열 형식, 숫자가 JSON 숫자인지 문자열인지, 호가 단계 수, `Currency` 값 목록, 실시간/지연 여부, 잘못된 종목의 오류 본문, 캐시 정책, 배치 조회(시그니처가 종목 1개이므로 지원한다고 가정하지 않음). 공식 예제 응답은 문서에 없어서 테스트 값은 문서 구조를 따른 합성값이다.

## 2. 요청 울타리 (`guard_toss_orderbook_client`)

기존 `guard_toss_client`를 확장했다(새 옵션은 모두 기본 꺼짐이라 기존 호출은 동일). **한 `TossClient` 인스턴스에만** 적용되며 다른 인스턴스·다른 전송 코드는 보호하지 않는다.

- 허용: `POST /oauth2/token`, `GET /api/v1/orderbook?symbol=XXXXXX`만. 고정 출처(기본 `https://openapi.tossinvest.com`, `http`는 루프백만).
- 쿼리는 `symbol=` 정확히 1개(6자리 `[0-9A-Z]`). 중복·빈 값·추가 쿼리·퍼센트 인코딩·소문자·공백/제어문자·프래그먼트·`;`·본문·추가 헤더는 거부.
- **인증 전 사전 차단**: `_authorized_json_request`를 감싸 토큰을 요청하기 전에 검사한다. 거부되는 요청은 토큰 요청도 보내지 않는다.
- 상한: 전체 6, 인증 2, 호가 4. 클라이언트의 429 재시도·401 재인증도 모두 계산된다(예약한 요청은 실패해도 계산). 동시 호출은 공용 락으로 직렬화된다.
- 리다이렉트는 따라가지 않는다(3xx는 오류). 오류는 상태·slug 코드만 남기고 공급자 문구·URL·헤더·원인 예외를 제거한다(`__cause__`/`__context__` 없음).
- `fetch_orderbook_results`는 이 울타리가 없는 클라이언트를 거부하고, 종목 형식·중복·개수(1~4)를 네트워크 전에 검사한다. 429·401·3xx·시간 초과·알 수 없는 오류·시계 역행에서는 나머지 종목을 `NOT_FETCHED`로 두고 멈춘다. 400/404만 종목 단위로 처리한다.

## 3. 변환 규칙 (`parse_orderbook`)

- 최우선 매도 = 유효 매도호가 중 최저가, 최우선 매수 = 유효 매수호가 중 최고가. 배열 순서에 의존하지 않는다. 유효: 가격>0, 수량>0, 유한한 Decimal. 같은 가격은 수량을 합산한다. 무효 호가를 건너뛰면 스프레드가 넓어지는 쪽이라 보수적이다(건너뛴 개수는 notes).
- `spread_pct = (ask - bid) / ((ask + bid) / 2) × 100` (퍼센트 단위, Scout의 `max_spread_pct` 기본 1과 같은 단위).
- 제공자 시각(`provider_time`)과 우리 수신 시각(`received_at`)은 분리한다. 수신 시각은 응답 직후 주입된 시계로 찍는다.
- 신선도: 수신−제공자 시각 > 10초면 `STALE_ORDERBOOK`(연구용 초기 기준이며 운영 정책이 아님). 제공자 시각이 수신보다 2초 넘게 앞서면 `TIMESTAMP_IN_FUTURE`.
- 실패는 모두 `UNKNOWN`(스프레드 None)이며 사유 코드를 남긴다: `RESPONSE_MALFORMED`, `TIMESTAMP_MISSING/INVALID/NO_TIMEZONE/IN_FUTURE`, `STALE_ORDERBOOK`, `CURRENCY_MISSING/NOT_KRW`, `ASKS_MALFORMED`, `BIDS_MALFORMED`, `NO_VALID_ASK`, `NO_VALID_BID`, `CROSSED_BOOK`, `LOCKED_BOOK`. 조회 단계: `FETCH_FAILED`, `FENCE_REFUSED`, `NOT_FETCHED`, `CLOCK_INVALID`, `CLOCK_REGRESSION`.
- 스프레드가 정확히 50%를 넘으면 `ANOMALY`(`ANOMALOUS_SPREAD`, 값은 `observed_spread_pct`에만). Scout에는 None이 전달된다.
- `with_spread(liquidity, result)`: OK일 때만 `LiquidityFacts.spread_pct`를 채우고 `observed_at`은 두 시각 중 이른 쪽이다. OK가 아니면 기존 값이 있어도 None. Scout의 임계값(1%)·유동성 판정·전략·주문 위험은 읽거나 바꾸지 않는다.

## 4. 남은 한계

- 실제 응답 형식(timestamp·숫자 표현·통화값)이 공식 문서와 다를 수 있다. 달라도 UNKNOWN으로 안전하게 떨어지지만, 실제로 쓸 수 있는지는 실호출 1회 전에는 모른다.
- 호가 깊이(최우선 한 단계)와 실제 체결 가능성은 별개다. 장 마감·휴장·점검 시간대의 응답은 미검증이다.
- 울타리의 `urlopen` 교체는 전송 모듈 전역이라, 보호 중인 요청 도중 같은 모듈을 쓰는 다른 클라이언트도 리다이렉트 비허용 상태가 된다(더 엄격할 뿐 느슨해지지 않음).
