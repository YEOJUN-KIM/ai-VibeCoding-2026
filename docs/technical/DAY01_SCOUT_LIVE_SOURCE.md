# DAY01 Scout 실제 Toss 순위 연동 (읽기 전용 검증)

상태: 어댑터·기록기 구현, 합성 테스트 통과, **실제 순위 조회 1회 검증(2026-11-13 10:11 KST)**. 운영 DB·워커·주문·PAPER 연동은 없다.
코드: `auto_trader/day01_scout_source.py`, 실행 스크립트 `scripts/day01_scout_live_check.py`, 테스트 `tests/test_day01_scout_source.py`.

## 1. 기존 구현 조사 (코드로 확인한 사실)

| 항목 | 확인 내용 |
|---|---|
| 순위 함수 | `TossMarketData._domestic_trading_amount_rankings()` → `GET /api/v1/rankings` (`type=MARKET_TRADING_AMOUNT`, `marketCountry=KR`, `duration=1d`, `excludeInvestmentCaution=true`, `count=100`) |
| 반환·캐시 | `(rankedAt, rankings 정렬본)` 튜플을 30초 캐시. 캐시 적중 시 **같은 튜플 객체** 반환. 수신 시각은 저장하지 않음 |
| 인증 | `TossClient`가 `settings`(환경변수·`.env` 로더)의 `TOSS_CLIENT_ID/SECRET`으로 `/oauth2/token`(client_credentials) 발급 후 Bearer. 인증정보는 코드에서만 사용 |
| 요청 제한 | 그룹별 최소 간격(RANKING 0.21초), 429 시 `Retry-After`/`X-RateLimit-*` 반영, GET만 최대 3회 재시도(20초 상한), `RequestBudget`으로 대기·잠금·요청 시간 상한 지원 |
| 오류 | `TossApiError(status_code, error_code)`; 메시지에 공급자 문구가 섞일 수 있음 |
| 공급자 필터 | `excludeInvestmentCaution=true`만. 그 외 필터 없음 |

## 2. 실제 응답에서 확인된 것 (1회 조회, 비밀정보 제외)

- 행 키: `rank, symbol, tradingAmount, tradingVolume, currency, price.lastPrice, price.basePrice, price.changeRate`. **종목명·시장·종류(ETF/보통주)·호가·스프레드는 없다.**
- `rankedAt` 제공됨(예: `2026-11-13T10:10:56.269+09:00`, 밀리초·KST 오프셋). 수신 시각과 약 9~10초 차이.
- 행 100개, 순위 1..100 연속, 중복 종목 없음, 파싱 결함 0.
- 30초 이내 재조회는 캐시 적중이었고 원래 수신 시각이 유지됨(DUPLICATE). 31초 후 재조회는 새 `rankedAt`을 받아 새 스냅샷.
- 요청: 토큰 1회 + 순위 GET 2회. 계좌·주문·보유 엔드포인트와 DB는 호출하지 않음.

## 3. 어댑터 동작

- `RankingFetcher`는 `ingest_fetch`가 요구하는 `FetchResult(payload, received_at)`을 돌려준다.
- **수신 시각**: 소스가 새 페이로드 객체를 돌려준 직후에 한 번만 찍는다(실제 수신보다 이르지 않음 → 미래 누출 없음, 캐시에 머문 시간만큼 최대 약 30초 늦을 수 있어 보수적). 같은 객체가 다시 오면 저장된 시각을 재사용하고 `clock()`을 부르지 않는다.
- 공급자 시각이 없으면 `None` 그대로 두며 Scout가 `UNKNOWN_PROVIDER_TIME`으로 신규 진입을 막는다.
- 같은 `rankedAt`에 내용이 다르면 `conflict` 기록을 남기고 두 원본을 모두 보존한다. Scout는 `INVALID_SNAPSHOT`으로 차단한다.
- 만료·지연·과거 순위 재구성 금지는 기존 Scout 정책을 그대로 사용한다.
- `toss_ranking_source(client)`는 순위 메서드 하나만 호출하며 `RequestBudget`(기본 15초) 안에서 실행한다.

## 4. 연구용 기록 (append-only)

`ResearchLog`: 절대 경로 `.jsonl`만 허용하고 `.env/.backups/.paper-history/.local-secrets/.git` 경로는 거부. 기본 위치 없음. 한 줄씩 flush·fsync하고 덮어쓰지 않는다.
레코드: `fetch_ok`(수신 시각, 공급자 시각 원문, 원본 행, 원본 해시, 품질), `fetch_cache_hit`, `fetch_failed`(오류 유형·상태·코드만), `conflict`, `scout_result`(`fact_provenance` 라벨 포함). 비밀처럼 보이는 키는 값을 `[REDACTED]`로 바꾼다. 운영 DB·RAW 테이블·스키마는 건드리지 않는다. 실행 결과 파일은 저장소 밖 임시 연구 디렉터리에 두었고 커밋하지 않았다.

## 5. Scout 결과 해석 시 주의

이번 실행에서 **순위만 실제**이고 종목 상태·유동성·DAY-01 준비도·포트폴리오는 **합성(모두 적격)** 이다.

- DAY-01 기본 설정(수량 10, 주문 한도 100,000원): 후보 4개(부족 4/10). 상위 50 중 최대 약 9,990원 이하 종목만 통과(4개). 제외 사유 집계: 주문 한도 46, 종목 노출 40, 총 투자 29, 현금 19, 현금 비율 22(중복 집계).
- 민감도(수량 1, 한도 정책은 그대로): 후보 10개. 정책 변경이 아니라 한도와 수량의 상호작용을 보기 위한 참고치다.
- 선정된 코드 중 다수가 ETF/ETN으로 보이지만(코드 형태 기준, 이번 실행에서는 **검증하지 않음**), 합성 사실이 모두 `STOCK/보통주`로 표시돼 걸러지지 않았다. **실제 종목 사실 없이 이 후보 목록을 실제 적격 후보로 해석하면 안 된다.**
- 순위 가격은 순위 행 값이며 주문 가능성을 보장하지 않는다.

## 6. 미검증·후속 (이번 범위 밖)

1. `/api/v1/stocks` 등으로 `InstrumentFacts` 변환(ETF·보통주 판별, 거래정지·정리매매) — 가장 시급. 순위 응답에는 종류 정보가 없다.
2. 호가 기반 스프레드·유동성(`LiquidityFacts`), DAY-01 준비도(1분봉 저장소와 동일 원천), 실제 포트폴리오(일일 손실 정의 일치).
3. 연속 수집(워커·예약), 저장소 위치·보존 정책, 운영 RAW 테이블과의 관계는 별도 승인 필요.
4. 장 마감·휴장일·점검 시간대의 응답 형태는 미검증(장중 1회만 확인).
5. 수신 시각의 캐시 보수 편향(최대 약 30초)을 줄이려면 클라이언트 캐시가 수신 시각을 보관해야 한다(기존 클라이언트 수정 필요, 이번에는 하지 않음).
