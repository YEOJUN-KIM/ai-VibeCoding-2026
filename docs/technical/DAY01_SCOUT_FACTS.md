# DAY01 Scout 종목 사실(InstrumentFacts) 어댑터

상태: 구현·합성 테스트 통과(AI 구현 완료) + **실제 응답 1회 검증(2026-11-13 10:48 KST, §6)**. 사용자 동작 검증·운영 적용 없음. §1~§3의 필드 목록은 최초에 기존 코드에서 추론한 것이며 §6의 실제 응답으로 정정했다(정정 사항은 §6에 명시).
코드: `auto_trader/day01_scout_facts.py`, 테스트 `tests/test_day01_scout_facts.py`. 운영 DB·워커·주문·PAPER 연동 없음.

## 1. 구현 전 위험 체크리스트

| 항목 | 위험 | 대응(구현에서 지킬 것) |
|---|---|---|
| 인증정보 노출 | 소스/로그/예외 메시지에 토큰·키 | 어댑터는 클라이언트를 직접 받지 않고 호출 가능 객체만 받는다. 로그는 기존 `ResearchLog`(키 마스킹, 오류는 유형·상태·코드만). `.env`·환경변수 접근 코드 없음 |
| 요청 메서드·엔드포인트 | 종목 조회가 주문·계좌 호출로 번질 위험 | 소스 래퍼는 `GET /api/v1/stocks`, `GET /api/v1/stocks/all`만 호출. 기존 울타리 `guard_toss_client`는 기본값이 순위만 허용이며, 종목 조회는 호출자가 명시적으로 허용해야 한다(기본 동작 불변) |
| 응답 시각 vs 수신 시각 | `/stocks`는 공급자 기준시각이 없음(코드상) | 공급자 시각을 만들지 않는다. 사실의 `observed_at`은 **우리 최초 수신 시각**(가용 시각)이다 |
| 캐시 재사용 | 클라이언트 `_stock_info_cache`·종목 목록은 12시간 캐시 | 시간 민감 정보(상태·거래정지)는 12시간 캐시를 거치지 않는 새 조회만 사용. 목록(시장 구분)은 정적 속성으로만 쓰고 원래 수신 시각이 알려져 같은 KST 날짜일 때만 인정. 객체 단위 최초 수신 시각 보존 |
| 날짜 변경·시계 역행 | 어제 사실을 오늘 적용, 역행한 시계로 과거 시각 도장 | `ReceiptLedger`(순위 어댑터와 같은 규칙: naive·역행·단조 시계 불일치 시 fail-closed). 목록은 날짜가 다르면 무시. Scout의 `observed_at <= as_of`·최대 나이 규칙을 그대로 사용 |
| 누락·중복·충돌 | 같은 종목이 두 번, 값이 다름 / 응답에 없음 | 충돌은 모든 필드를 UNKNOWN(None)으로 만들고 사유 보고. 누락은 사실을 만들지 않음(Scout가 MISSING으로 제외). 요청하지 않은 종목은 무시 |
| 저장 경로·원본 보호 | 기록이 보호 디렉터리·링크로 | 기존 `ResearchLog`(대소문자·링크 해석·조상 링크 검사, 쓰기 직전 재검사) 재사용 |
| 정보 부족 시 신규 진입 금지 | 불명 값을 "정상"으로 해석 | 값은 엄격한 타입만 인정(bool은 bool만, 문자열은 문자열만). 불명은 None → Scout가 `NOT_STOCK/NOT_COMMON_SHARE/SUSPENDED_OR_UNKNOWN/LIQUIDATION_OR_UNKNOWN`으로 제외. 어댑터는 Scout 정책을 완화하지 않음 |
| 소급 적용 | 오늘 조회한 사실로 과거 날짜를 판단 | 사실은 `observed_at` 이후에만 유효(Scout가 미래 사실 무시). 과거 `as_of` 재현에는 쓸 수 없음(테스트로 확인) |

## 2. 종목 정보 API 조사 (코드 읽기 전용, 실응답 미확인)

| 항목 | 기존 코드에서 확인한 사실 |
|---|---|
| 종목 상세 | `GET /api/v1/stocks?symbols=A,B,...` → `result` 배열. 코드가 읽는 키(실제 응답에는 `market`, `currency`, `leverageFactor`, `delistDate`도 있었음, §6): `symbol, name, englishName, isinCode, listDate, status, sharesOutstanding, securityType, isCommonShare, koreanMarketDetail.{nxtSupported, krxTradingSuspended, nxtTradingSuspended, liquidationTrading}`. 기존 프리셋 경로가 50개 단위로 요청하며(API 상한은 미확인), 기존 기본정보 캐시는 종목별 12시간 |
| 종목 목록 | `GET /api/v1/stocks/all?market=KOSPI\|KOSDAQ&status=ACTIVE` → 배열. 클라이언트가 `market`을 요청 인자로 붙여 저장하며 12시간 캐시(두 호출 사이 1.05초 대기). 항목은 `securityType`, `isCommonShare`, `name`을 가짐 |
| 유형 값 | `securityType`에 `STOCK`, `ETF`, `ETN`이 쓰인다(API 쿼리 패턴 `ALL\|COMMON\|STOCK\|ETF\|ETN`, ETF 화면의 `security_type === 'ETF'`). 우선주 등은 `isCommonShare=false`로 구분되는 것으로 보인다(코드 추론) |
| 통화 | 종목 응답이 아니라 순위 행의 `currency`(실제 조회에서 100/100 `KRW` 확인) 또는 현재가 응답 |
| 공급자 기준시각 | `/stocks`·`/stocks/all` 코드에는 기준시각 필드가 없다. 만들지 않는다 |
| 한도·오류 | 그룹 `STOCK` 0.21초, `STOCK_ALL` 1.05초 최소 간격, 429 재시도 규칙은 순위와 동일, 오류는 `TossApiError(status_code, error_code)` |
| 기존 필터 | 기존 후보/프리셋 코드는 `STOCK`+보통주+`ACTIVE`+거래정지 아님+정리매매 아님을 요구. 단 `liquidationTrading`이 **없으면** 통과(느슨), 프리셋은 `krxTradingSuspended is False`를 요구(엄격) |

## 3. 어댑터 동작

- `build_instrument_facts`(순수) / `InstrumentFactsSource.collect`: 순위 종목을 50개씩 나눠 새 `/stocks` 조회 → `InstrumentFacts`. 소스는 호출 가능 객체이며 실제 래퍼는 `toss_stocks_source`, `toss_directory_source`.
- 필드 매핑: `securityType`→`security_type`, `isCommonShare`→`is_common_share`(bool만), `status`, `koreanMarketDetail.krxTradingSuspended/liquidationTrading`(bool만), `market`(목록, 같은 KST 날짜일 때만), `currency`(순위 행). 키가 **없으면** `None`(미상). 키가 **있는데** 타입이 다르거나 null·빈 문자열이면 MALFORMED로 보고 `None`으로 만들며(`MALFORMED:<field>`) 목록 값으로 **채우지 않는다**.
- 심볼은 요청·`/stocks`·목록·순위 모두 `strip().upper()`(기존 클라이언트 규약)로 비교한다. 대소문자·공백 별칭이 다른 값을 가지면 충돌로 차단한다.
- **Scout 정책은 그대로**: `None`은 `NOT_STOCK / NOT_COMMON_SHARE / NOT_ACTIVE / SUSPENDED_OR_UNKNOWN / LIQUIDATION_OR_UNKNOWN / NOT_KOSPI_KOSDAQ / NOT_KRW`로 제외된다. 따라서 기존 코드와 달리 `liquidationTrading` 키가 없으면 제외한다(보수적). 실제 응답이 정상 종목에서 이 키를 생략하면 후보가 모두 사라질 수 있으며, 이는 실호출로만 확인할 수 있다.
- 충돌: 같은 소스가 같은 종목(별칭 포함)을 다르게 반복하면 그 종목의 모든 필드가 `None`(`DUPLICATE_CONFLICT`). `/stocks`와 목록이 정적 속성에서 다르면 해당 필드만 `None`(`CONFLICT:<field>`). 목록은 **키가 없는** 정적 속성(유형·보통주·시장·이름)만 채우며 상태·거래정지·정리매매는 절대 채우지 않는다.
- 누락: `/stocks` 응답에 없는 종목은 사실을 만들지 않는다(`NO_STOCKS_ROW` → Scout `INSTRUMENT_MISSING`). 요청하지 않은 종목은 무시. 조회 실패·잘못된 응답 형태·시계 이상은 모두 사실 없음으로 수렴하고 오류 유형·상태·코드만 기록한다.
- 시각: `observed_at`=`/stocks` 최초 수신과 순위 수신 중 늦은 쪽. 둘의 차이가 60초 초과면 사실 없음(`SOURCE_SKEW`). 객체 단위 최초 수신 시각을 보존하며 시계 역행은 거부한다. 원본 행 기록(`stocks_fetch_ok`)이 성공한 뒤에만 객체를 기억하므로, 기록 실패는 그 배치를 사실 없음으로 만들고 재시도 때 원본 행이 기록된다.
- 목록 수집 순서와 날짜: 목록을 **먼저** 수집한다(첫 수집에서 목록이 사실보다 늦게 도착해 시장 정보가 사라지는 문제 방지). 목록 소스는 `(rows, 원래 수신 시각)`을 돌려줘야 하고, 수신 시각을 모르면 목록을 쓰지 않는다. 래퍼 `toss_directory_source`는 기존 클라이언트의 캐시 만료 시각(`_stock_universe_expires_at`, 사설 속성)에서 원래 수신 시각을 역산한다(실제 수신보다 이르지 않음). 속성이 없거나 값이 비정상이면 수신 시각 미상으로 처리한다. Scout가 `observed_at <= as_of`·나이 300초를 적용하므로 새 사실은 과거 `as_of`에 쓰이지 않고, 더 새로운 거래정지 사실은 이전의 정상 사실을 대체한다.
- 울타리: `guard_toss_client(client, also_allow=[("/api/v1/stocks","GET"),("/api/v1/stocks/all","GET")])`처럼 명시한 GET만 추가된다. 기본값은 이전과 같이 토큰 POST와 순위 GET만 허용하며 POST 추가는 거부한다.

## 4. 실제 정보와 합성 정보의 경계

| 정보 | 상태 |
|---|---|
| 거래대금 순위(순위·가격·거래대금·`currency`) | 이전 작업에서 **실제** 조회(10:11 KST, 1회). 진단 분석은 이 저장본을 사용 |
| `/stocks` 종목 상세(상위 50종목) | **실제**(10:48 KST, 1회 배치). 대표 종목 두 번째 배치(10개)는 요청 상한에 걸려 조회하지 못함 |
| `/stocks/all` 목록(KOSPI·KOSDAQ 4,327행) | **실제**(10:48 KST) |
| 유동성·DAY-01 준비도·포트폴리오 | 합성 |
| Scout 진단 | 저장된 실제 순위를 현재 시각으로 재도장한 **진단용**(실제 수신 시각 아님). 종목 사실만 실제 |

## 5. 미검증·다음 조건

1. 비정상 종목(거래정지·정리매매·상장폐지 예정·`ACTIVE` 아님)의 실제 응답 형태는 보지 못했다. 표본 50종목은 모두 정상 활성 종목이었고 키가 모두 존재했다. 비정상 상태에서 키가 생략되는지는 UNKNOWN이며, 어댑터는 생략을 정상으로 보지 않는다.
2. ETN·REIT·해외주식 등은 `/stocks` 응답으로는 보지 못했다(두 번째 배치 미조회). 목록(`/stocks/all`)에서는 유형 값이 확인됐다.
3. 목록 원본 행은 용량 때문에 기록하지 않고 해시만 남긴다(연구 표본은 저장소 밖에 별도 저장).
4. 호가 기반 유동성, DAY-01 준비도, 실제 포트폴리오는 여전히 합성이다.
5. 장 시작 전·마감 후·휴장일 응답과 거래정지 전환 시점의 지연은 미검증이다.
6. 실호출에서 요청이 정확히 상한 6회에 도달했다(토큰 2, `/stocks/all` 3, `/stocks` 1). 토큰 2회와 목록 3회는 기존 클라이언트의 재인증·재시도로 보이나 원인은 기록하지 못했다(성공한 재시도는 오류 정보를 남기지 않음). 이 모듈은 어디에도 배선되지 않았다.

## 6. 실제 응답 검증 (2026-11-13 10:48 KST, 읽기 전용)

**호출**: 울타리를 통과한 요청은 6회로 상한에 정확히 도달했다: 토큰 POST 2, `GET /api/v1/stocks/all` 3(KOSPI·KOSDAQ 각 1 + 추가 1회, 원인 미기록), `GET /api/v1/stocks` 1(성공). 7번째 요청(`/stocks` 두 번째 배치)은 울타리가 네트워크 전에 차단했다. 순위·계좌·주문 호출 없음(울타리가 순위도 차단). 비밀정보는 출력·기록하지 않았다(기록 파일 3개에서 토큰·Bearer·secret·client_id 문자열 0건).

**`/stocks` 행 키(실제)**: `symbol, name, englishName, isinCode, market, currency, securityType, isCommonShare, status, listDate, delistDate, sharesOutstanding, leverageFactor, koreanMarketDetail.{nxtSupported, krxTradingSuspended, nxtTradingSuspended, liquidationTrading}`. 표본 50종목(상위 순위): `market` KOSPI 40/KOSDAQ 10, `currency` KRW 50, `status` ACTIVE 50, `krxTradingSuspended` False 50, **`liquidationTrading` False 50(전부 존재)**, `delistDate` null 50, `leverageFactor` ETF에만 값.

**`/stocks/all` 행 키(실제)**: `symbol, name, isinCode, market, securityType, isCommonShare`. `status` 없음(요청 쿼리의 `status=ACTIVE`로 거른 목록). 4,327행, 중복 없음.

**유형 값(목록 4,327행)**: `STOCK` 2,722(그중 `isCommonShare=true` 2,613 / `false` 109), `ETF` 1,188, `ETN` 368, `REIT` 23, `FOREIGN_STOCK` 12, `DEPOSITARY_RECEIPT` 10, `INFRASTRUCTURE_FUND` 2, `STOCK_WARRANTS` 2. **ETF·ETN·REIT 등은 모두 `isCommonShare=true`**이므로 ETF 구분은 `isCommonShare`가 아니라 `securityType != "STOCK"`로만 가능하다. 우선주는 `STOCK` + `isCommonShare=false`. 두 조건을 모두 요구하는 Scout 정책이 실제 데이터에 맞다.

**정정한 가정**
- 이전 가정: `/stocks`에는 `market`·`currency`가 없어 목록과 순위 행에서 가져와야 한다. 실제: `/stocks`에 있다. 어댑터가 이를 무시하던 것을 수정했다(`/stocks` 우선, 목록·순위는 교차 검증, 불일치·malformed는 차단).
- 이전 우려: 정상 종목에서 `liquidationTrading`이 생략될 수 있다. 실제 표본 50종목에서는 전부 존재했다(생략 패턴은 관찰되지 않음). 단, 비정상 종목의 형태는 모르므로 키 누락을 정상으로 해석하지 않는 정책은 그대로 둔다.
- 목록과 상세의 정적 필드(`securityType`, `isCommonShare`, `name`)는 조회한 50종목에서 불일치 0건.
- 목록 수신 시각 역산(`toss_directory_source`)은 실제 클라이언트에서 수신 시각을 정상 복원했다(`receipt_time_known=True`).

**Scout 진단(종목 사실은 실제, 순위는 저장본 재도장, 나머지 합성)**: 상위 50 구성은 보통주 29, 우선주 2, ETF 19. 제외: `NOT_STOCK` 19(ETF), `NOT_COMMON_SHARE` 2(우선주).
- DAY-01 기본(수량 10, 주문 한도 100,000원): **후보 0개**(부족 0/10). 보통주 29종목의 가격이 11,210원~1,287,000원으로 모두 주문 한도(약 9,990원 이하만 통과)에서 탈락했다.
- 참고(수량 1, 한도 정책은 그대로): 후보 10개, 모두 `STOCK`·보통주. 한도 정책을 바꾸자는 제안이 아니라 한도와 수량·종목 가격의 상호작용을 보기 위한 수치다.
- 이전 보고의 "후보 4개/10개"에 섞여 있던 코드들은 ETF였음이 확인됐다(합성 사실이 보통주로 표시돼 걸러지지 않았던 것).
