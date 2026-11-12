# DAY-01 첫 실제 데이터 연구 백테스트 독립 검증 (Claude Code)

작업 기록: [20261112-Claude-DAY01-real-validation-review](../plans/ai-tasks/20261112-Claude-DAY01-real-validation-review.md)

상태: **준비 단계 — Codex의 최종 테스트 재실행 로그와 데이터 인벤토리·입력·산출물 인계를 기다리는 중이다.** 아래는 소스만 읽은 독립 감사와 검증 프로토콜이며 실제 결과가 아니다. 실제 시장 데이터를 읽거나 백테스트를 실행하지 않았다. 결과 항목은 증거가 없으면 **0이 아니라 N/A**로 표기한다. 연구 결과는 물리 체결·top50·수익성에 대한 주장이 아니다.

## 1. 소스 기반 지표 의미 감사 (읽기 전용)

대상: `auto_trader/ml/day01.py`, `day01_research.py`, `docs/technical/DAY01_DATA_READINESS.md`, 기존 `auto_trader/ml/backtest.py`(읽기만, 실행·import 금지).

| 지표 | DAY01 결과가 직접 제공하는 것 | 의미·주의 |
|---|---|---|
| 손익 | `summary.net_profit = net_liquidation_equity − initial_cash`, `realized_profit`(SELL 체결 합) | 순청산자산은 미청산 보유분을 마지막 알려진 마크에서 **가상 매도 비용(수수료·세금·슬리피지) 차감**해 평가. 미청산·동결 평가는 실현 손익이 아니다 |
| 수수료·세금·슬리피지 | `summary.fees/taxes/slippage`, 체결별 `fee/tax/slippage` | 수수료·세금은 체결금액×요율의 KRW 정수 내림, 세금은 매도만. 슬리피지는 `abs(체결가−기준가)×수량`(원 단위 반올림 없음). 요율은 `Config` 값(기본 0.015%/0.2%/0.05%)이며 **실제 중개사 요율로 검증된 값이 아니다** |
| 거래 수 | `filled_orders`(체결 레코드 수, 부분 체결 각각), `entry_orders`, `total_orders`(청산 주문 포함) | **종료된 왕복 거래 수·승률·ROI·MDD·profit factor는 제공하지 않는다**(`day01*.py`에 계산 없음, 기존 `backtest.py`와 달리). 독립 재계산이 필요하다 |
| 일별 | `daily_results`(일 종가 자산, `net_change`=일 기준선 대비) | 기준선은 그날 첫 배치(09:00 타이머)의 순청산자산. 15:10 시점 값이며 이후 도착은 반영되지 않는다 |
| 에쿼티 곡선 | `equity_curve`(배치 시각마다 cash·순청산자산·손실한도 초과·미청산), 연구용은 예약·부채·무효 표식 추가 | 표본이 이벤트·타이머 시각이므로 MDD는 **이 표본 기준**이다(틱 단위 아님). 연구용 순청산자산은 비자금 부채가 차감된 값 |
| 불확실성 표식 | `mode`, `uncertain`, `physical_fill_claim=False`, `research_assumptions`(run_status, 부채, 자원 위반), 체결별 `hindsight_fields`·`uncertainty_flags` | `INVALID_RESOURCE_OVERRUN`이면 결과를 유효한 백테스트로 해석하지 않는다. 최상위 `run_input.run_status`가 이를 전달한다 |
| 가용성 | 입력의 실제 `available_at` 보존, 연구용은 도착 시각에 판단·`execution_at`=관측(정산) 시각, `estimated_execution_at`=반사실 시가 시각 | 미래 가용 데이터 사용 여부는 신호·정산 시각과 입력 `available_at` 비교로 검증 |
| 선택 | 고정 universe·명시 날짜(호출자), top50·성과 기반 선택 없음, turnover 없음 → `vwap_mode`는 `typical_approximation` 명시 필요 | exact VWAP은 저장 행에서 신호를 만들지 않는다 |

기존 MA(`backtest.py`)는 자체 체결 모델(관측 후 첫 양(+)거래량 봉 시가 등), 설정 기반 요율, 1분봉 MA 엔진을 쓰므로 **DAY01 연구 모드와 체결·진입 규칙이 달라 직접 비교할 수 없다**. 신규 MA 전략은 구현하지 않는다.

## 2. 독립 재계산 프로토콜 (인계 후 수행, 전체 백테스트 재실행 없음)

입력: 인계된 `result`(trades, equity_curve, daily_results, summary) 직렬화본. 순수 `Decimal` 계산으로 다음을 확인한다.

1. **원장**: `cash == initial_cash + Σ cash_delta`(연구용은 `cash − liability` 정합), 체결별 `amount=price×qty`, `fee=floor(amount×fee_rate)`, `tax`(매도만, floor), `price=reference×(1±slippage)`, `slippage=|price−reference|×qty`, `cash_delta` 부호. 합계가 summary의 fees/taxes/slippage와 같은지.
2. **손익**: 종료 왕복(매수→매도 수량 일치)별 `realized_profit`을 매수 원가(수수료 포함)에 대한 비례 배분으로 재계산, `Σ realized_profit == summary.realized_profit`. 미청산 평가 = 마지막 알려진 마크의 가상 매도 순대금 − 잔여 원가. `net_profit == 실현 + 미실현`.
3. **ROI·MDD·승률**(DAY01이 제공하지 않으므로 정의를 명시): ROI = `net_profit / initial_cash`(실현 ROI와 구분), MDD = 에쿼티 곡선 표본의 최대 고점 대비 하락률(`(peak−equity)/peak`), 승률 = 종료 왕복 중 `realized_profit>0` 비율(부분 체결은 주문 단위로 합산, 거래 0건이면 **N/A**), 필요 시 평균 손익·profit factor. 분모 0은 N/A.
4. **인과성**: 모든 신호·정산이 해당 입력 행의 `available_at` 이후인지, 연구용이면 `estimated_execution_at > signal_at`, `execution_at ≥ observation_at`, 15:10 이후 정산 없음, 늦은 행의 소급 체결 없음.
5. **상태**: `run_status`(INVALID/NO_REPLAY_ROWS), 동결·미청산 수량·`overnight_gap_exposure_unmodeled`, 결측·`NO_ROWS` 날짜 처리, 입력에서 제외된 15:30·시간외 행 수가 보고서에 있는지.
6. **입력 증거**: 실제(비합성) 출처, 입력 SHA-256과 행 수, 고정 universe 목록, 명시 날짜 범위, 수수료 모델, 설정(`Config`) 전체, 코드 commit SHA. 해시는 인계 파일에서 독립 계산한다.
7. **MA 비교(있는 경우)**: 동일 기간·universe·비용인지, 체결 모델 차이 경고, 지표 정의가 같은지. 비교 불가이면 비교하지 않고 사유를 기록한다.

## 3. 필요한 자료(인계 요구사항) — 데이터 없음 시 보고 형식

데이터가 인계되지 않으면 Codex의 검색 인벤토리(범위·방법·제외 사유)만 감사하고 **전역 부재를 가정하지 않으며 추가 광범위 검색을 하지 않는다**. 이 경우 필요한 자료는 다음과 같다:

- 시장 데이터 전용 로컬 파일 또는 승인된 읽기 전용 내보내기: 완성 1분 OHLCV, **종목별·분별 `event_at`과 실제 수신 시각 `available_at`**(없으면 정시 가용으로 가정하지 않고 사용 불가 처리), 출처(source) 표기, 간격 `1m`.
- 사전 지정된 고정 종목 목록과 명시적 거래일 목록(호출자 제공), 가능하면 호출자가 정한 기대 분 격자(390/391).
- 계좌·주문 이력·백업·비밀정보·출처 불명 혼합 파일은 제외. turnover가 없으면 `typical_approximation`을 명시.
- 엄격 체결 근거(시가 시점 가격·체결가능수량 수신 이력)는 현재 저장소에 없으므로 **엄격 모드 결과는 불가**이고 연구용 추정만 가능함을 명시.

## 4. 최종 보고 항목 (사용자 보고 8개 항목, 인계 후 채움)

| # | 항목 | 현재 |
|---|---|---|
| 1 | 데이터 출처·적격성(실제/합성, 인벤토리, 제외 사유) | 인계 대기 (N/A) |
| 2 | 기간·고정 universe·입력 SHA·행 수·커버리지 | 인계 대기 (N/A) |
| 3 | 실행 모델·수수료/세금/슬리피지 모델·설정 | 인계 대기 (N/A) |
| 4 | 결과(종료 거래 수·순손익·수수료·세금·슬리피지·ROI·MDD·승률) | 인계 대기 (N/A, 0 아님) |
| 5 | 불확실성·한계(비물리 체결, 동결·무효·결측, 비교 불가 항목) | 소스 감사 완료(1절), 결과는 인계 대기 |
| 6 | MA 비교(동일 조건 여부·비교 가능성) | 인계 대기 (N/A) |
| 7 | 검증·테스트(신선한 259개 로그 점검, 독립 재계산, SHA) | 인계 대기 |
| 8 | 안전·git 상태·미검증·다음 단계 | 준비 단계 점검 완료: 작업 트리 clean, 소스 변경 없음 |

## 5. 준비 단계 안전 점검

- 변경한 파일: 이 문서와 작업 기록 2개(신규)뿐이다. 코드·테스트·전략·산출물·데이터는 변경·commit하지 않았다.
- DB·`settings`·앱·외부 API·`.env`·비밀정보 접근 없음. 시장 데이터·백테스트 실행 없음.
- 테스트 로그는 Codex의 최종 259개 재실행의 신선한 로그를 인계 후 점검한다(과거 결과를 이번 결과로 재사용하지 않는다). 안전 회귀 55개는 정당한 이유가 없으면 요구하지 않는다.
