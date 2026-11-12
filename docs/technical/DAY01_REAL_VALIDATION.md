# DAY-01 첫 실제 데이터 연구 백테스트 독립 검증 (Claude Code)

작업 기록: [20261112-Claude-DAY01-real-validation-review](../plans/ai-tasks/20261112-Claude-DAY01-real-validation-review.md)

상태: **증거 감사 진행 중 — 테스트 로그·입력 파일 출처 메타데이터 감사는 완료했고(6절), Codex의 실제 연구 백테스트 결과 인계를 기다리는 중이다.** 결과 재계산(2절 프로토콜)은 결과 인계 후 수행하며 아직 하지 않았다. 아래 1~5절은 소스 감사와 프로토콜, 6절은 현재까지의 증거 감사다. 결과 항목은 증거가 없으면 **0이 아니라 N/A**로 표기한다. 연구 결과는 물리 체결·top50·수익성에 대한 주장이 아니다.

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
| 1 | 데이터 출처·적격성(실제/합성, 인벤토리, 제외 사유) | 6.2절 출처 메타데이터 감사 완료, 내용 재검증은 결과 인계 후 |
| 2 | 기간·고정 universe·입력 SHA·행 수·커버리지 | 인계 대기 (N/A) |
| 3 | 실행 모델·수수료/세금/슬리피지 모델·설정 | 인계 대기 (N/A) |
| 4 | 결과(종료 거래 수·순손익·수수료·세금·슬리피지·ROI·MDD·승률) | 인계 대기 (N/A, 0 아님) |
| 5 | 불확실성·한계(비물리 체결, 동결·무효·결측, 비교 불가 항목) | 소스 감사 완료(1절), 결과는 인계 대기 |
| 6 | MA 비교(동일 조건 여부·비교 가능성) | 인계 대기 (N/A) |
| 7 | 검증·테스트(신선한 259개 로그 점검, 독립 재계산, SHA) | 6.1절 로그 감사 완료(259 통과), 독립 재계산은 결과 인계 후 |
| 8 | 안전·git 상태·미검증·다음 단계 | 준비 단계 점검 완료: 작업 트리 clean, 소스 변경 없음 |

## 5. 준비 단계 안전 점검

- 변경한 파일: 이 문서와 작업 기록 2개(신규)뿐이다. 코드·테스트·전략·산출물·데이터는 변경·commit하지 않았다.
- DB·`settings`·앱·외부 API·`.env`·비밀정보 접근 없음. 시장 데이터·백테스트 실행 없음.
- 테스트 로그는 Codex의 최종 259개 재실행의 신선한 로그를 인계 후 점검한다(과거 결과를 이번 결과로 재사용하지 않는다). 안전 회귀 55개는 정당한 이유가 없으면 요구하지 않는다.

## 6. 인계 증거 감사 (진행 중)

### 6.1 신선한 259개 테스트 로그 (Codex 실행, 읽기 전용 점검)

- 위치: Codex 작업 공간 `.../day01-real-validation/codex/task_f8c51bfed1a5`(저장소 밖, commit 대상 아님).
- `test-manifest.json`(SHA-256 `df554bd0…`)의 후보 commit은 `293cc09360671e64327e8081b179eaceb8351190`, 체크아웃 commit `7d72c09c…`(Codex worktree), 시작 시 작업 트리 clean(`working_state_before` 빈 값). `auto_trader` 트리 `7292df68b2f2e6523d7751923b29110841daa203`, `tests` 트리 `f4e140a196aafd227024ad928e583ffc579784d7`이 후보와 체크아웃에서 같고, **이 검토 브랜치(HEAD `e64e4233`)의 `git rev-parse HEAD:auto_trader`·`HEAD:tests` 값과도 동일하다**(소스·테스트 불변, 이후 변경은 docs뿐).
- 실행기 `validate_day01.py` SHA-256이 manifest의 `driver_sha256`(`8d9099df…`)과 일치한다. Python 3.14.7, `-B -m unittest discover -s tests -p <파일>` 4개 명령.
- 원시 로그를 직접 읽고 SHA-256을 재계산해 manifest 값과 비교했다.

| 파일 | 개수 | returncode | 로그 SHA-256 일치 | `... ok` 줄 | 실패·오류·건너뜀·expected failure |
|---|---|---|---|---|---|
| `test_day01.py` | 67 | 0 | 일치 (`09353bac…`) | 67 | 0 |
| `test_day01_review.py` | 108 | 0 | 일치 (`5831037e…`) | 108 | 0 (문자열 `skipped`는 테스트 이름 `test_late_candidate_bar_skipped_...`) |
| `test_day01_research.py` | 33 | 0 | 일치 (`ffdf14a9…`) | 33 | 0 |
| `test_day01_research_review.py` | 51 | 0 | 일치 (`7fb03ae3…`) | 51 | 0 |

합계 **259개 실행·259 통과**, 실행 전후 작업 트리 상태 기록 있음(`working_state_after` 포함). 이 로그는 과거 결과의 재사용이 아니라 후보 트리에서 새로 실행된 것이다. 안전 회귀 55개는 이번 검증 범위에 요구되지 않아 재실행하지 않았다.

### 6.2 선택된 입력 파일 출처 메타데이터 (내용 해석 전)

- 선택 경로(Codex가 결과를 보기 전에 선택·문서화): `D:/SourceBank/ai-VibeCoding-2026/artifacts/ml/generated/backtest-20261103-170024-aac70351/input.json`. 읽기 전용, 저장소로 복사하지 않았다.
- 크기 2,781,046 바이트(Codex 보고와 일치), 수정 시각 2026-11-03 17:00:28 KST(실행 전에 생성됨), **SHA-256 `4dfe33de48f858b804a679dab34d572ccbe46dba035c1666deb3b1dc99fbe030`**(바이트 해시만 계산, 내용 파싱·해석 없음).
- 출처 근거 문서: `docs/ml-drive-preservation.md` 7~10줄이 이 폴더를 당일(2026-11-03) 실제 백테스트 입력(8,832봉·22종목, 관측일 2026-11-03)으로 기록한다. 기존 내보내기는 `asdict(RawCandle)` 11개 시장·시간·출처 필드만 저장하고 계좌·주문 필드는 쓰지 않는다는 Codex 감사가 있으며, 이 검토에서는 해당 필드 화이트리스트 외 내용을 읽지 않았다. 같은 폴더에 기존 MA 결과 CSV(`moving_average-*.csv` 등)가 있으나 읽지 않았고 DAY01 연구 모드와 체결 모델이 달라 비교하지 않는다.
- 한계: 이 파일은 **단일 거래일(2026-11-03) 스냅샷**이며 정규 일중 데이터의 하루치일 뿐이다. 수익성·일반화의 근거가 아니다. 다른 36개 후보 문서와 나머지 경로는 읽지 않았고 최적화 선택은 없다. 하이퍼파라미터(연구 모드, `typical_approximation`, `order_quantity=1`)는 결과를 보기 전에 정해졌다고 Codex가 기록했다(기본 10주와 달라 공정성 비교 시 명시 필요).

### 6.3 결과 재계산 시 추가 요구사항 (결과 인계 후)

- **MDD는 관측 시각 기준**: 에쿼티 곡선 `equity_curve.at`은 재생 시계(입력 `available_at`·타이머)이므로 이 곡선으로 계산한 MDD가 관측 시각 MDD이다. 반사실적 `estimated_execution_at` 기준 곡선은 만들지 않는다(체결은 관측 시각에 정산되며 이전 시점에 소급 반영하지 않는다).
- 입력 SHA(위), 기간·고정 universe(2026-11-03 22종목 스냅샷), `Config` 전체(`research_estimated_open`, `typical_approximation`, `order_quantity=1`, 기본 비용 0.015%/0.2%/0.05%), 입력 전/후 해시와 행 수·`coverage` 보고, 제외된 15:30·시간외 행 수를 결과와 대조한다.
- 거래 0건이면 승률·ROI는 N/A(0 아님). 1주 가정에서는 수수료·세금이 KRW 정수 내림이라 거래 비용이 0이 될 수 있으므로 비용 0과 N/A를 구분한다.
- 결과가 `INVALID_RESOURCE_OVERRUN`이거나 미청산·동결이면 유효한 백테스트로 해석하지 않고 그대로 보고한다.
