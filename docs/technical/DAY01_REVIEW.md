# DAY-01 독립 검증 문서 (Claude Code)

작업 기록: [20261112-Claude-DAY01-review](../plans/ai-tasks/20261112-Claude-DAY01-review.md)

상태: **7절 = 엄격 `next_open`(Codex `348f342`) 최종 검토 결과(유효). 9절 = 연구용 `research_estimated_open`·입력 어댑터·커버리지(Codex `e412840`)와 수정 2건(`ba765e1`, `9908454`)의 독립 검토 결과이며 현재 기준이다. 발견한 결함 D-R1(중간)·D-R2(낮음)·D-R3(낮음) 3건은 모두 수정·재검증 완료되었고 알려진 실패·expected failure는 0이다.** 5절은 `legacy_close`, 8절은 구현 인계 전 조사·요구사항(8.1 증거 조사는 유효, 8.3 계획은 9절로 대체)이다. 실제 시장 데이터·PAPER/LIVE·사용자 동작 검증·운영 적용은 하지 않았고 수익성에 대한 어떤 주장도 하지 않는다. **실제 시세 피드에 바로 쓸 수 있는 상태가 아니다**: 엄격 기본 모드는 명시적 `OpenQuote` 증거가 있어야만 체결하며 저장소에는 그런 증거가 없고, 연구용 모드는 사후(hindsight) 추정이며 실제 체결이 아니다.

> **1~4절은 구현 전(사전) 요구사항·시나리오 설계 기록이며 역사적 문서다.** 당시의 가설(예: 5분봉 직접 입력, 기존 `RawCandle` 재사용, 초기 체결 모델 가정)은 최종 계약이 아니다. 현재 사양 `docs/specs/DAY01_BACKTEST.md`는 1분봉 입력과 완결 5분 집계를 쓰고, 기본 실행 모드는 `next_open`(명시적 `OpenQuote` 시가 증거가 있을 때만 체결)이며 `legacy_close`(이후 on-time 1분봉 종가 기반 체결)는 명시적으로 선택하는 이전 모드다. **5절은 `legacy_close` 시기의 이전 결과이고, 현재 기준은 7절이다.** 1~4절·5절과 충돌하면 **현재 사양 문서와 7절이 우선**한다.


## 1. 기존 코드 조사 결과 (읽기 전용, 구현 전 기록)

대상: `auto_trader/ml/backtest.py`, `ml/data_pipeline.py`, `risk.py`, `risk_policy.py`, `paper.py`, `market_hours.py`, `strategy.py`/`trading/engine.py`.

| 항목 | 확인 내용 | DAY01 검증에 주는 의미 |
|---|---|---|
| 캔들 검증 | `RawCandle.validate()`는 tz 필수, `event_at <= available_at <= collected_at`, OHLC>0·유한, 고가/저가 정합, 거래량>=0 | 손상·tz-naive·역전 시각은 거부해야 한다. 단, 5분봉 완결(`available_at >= event_at+5분`)은 검증하지 않는다 |
| 기존 리플레이 | 1분봉만 허용, `available_at >= event_at+1분` 요구, 중복 `(symbol,event_at)`은 `ValueError`, `available_at` 순으로 처리 | DAY01은 5분봉 전용 동등 규칙(미완결·이른 `available_at` 거부)을 가져야 한다 |
| 기존 체결 | `next_open`: 신호 가용 시각 이후의 첫 양(+)거래량 봉 시가, 같은 날, 2분 이내. 3분 초과 시 대기 신호 폐기 | "다음 체결 가능 봉"이 지연·결측·거래량 0일 때 어떻게 되는지 DAY01에서 명시적으로 고정해야 한다 |
| 비용 | `PaperBroker.submit`: 매수가=`price*(1+slip)`, 매도가=`price*(1-slip)`, 수수료=`floor(amount*fee_rate)`(원 단위 ROUND_DOWN), 세금=매도에만 `floor(amount*tax)`, 매수 평균가에 수수료 포함, `realized=amount-fee-tax-avg*qty` | 기대값을 독립적으로 Decimal로 계산해 비교 가능. 호가단위 라운딩은 없음(구현 문서에 명시되어야 함) |
| 위험 | `RiskPolicy`: 1회 금액은 `amount+fee<=max_order`, 종목·전체 한도는 `fee` 제외, 현금비율은 `(cash-amount-fee)/total_asset*100`. `global_block_reason`: `daily_profit <= -loss_limit`(경계 포함 차단), `>= profit_target` 차단, 주문 횟수 | 경계값(정확히 한도, 한도 −1원) 테스트로 `<` vs `<=` 구현 오류를 잡을 수 있다 |
| 위험 부작용 | `risk.py`는 모듈 import 시 DB에 접속하지 않지만 `RiskManager.initialize/settings/update`는 `connect()`로 DB 접속 | DAY01 테스트·구현은 이들을 호출하면 안 된다 (`ReplayRisk`처럼 `settings()` 재정의 필요) |
| import 부작용 | `auto_trader.settings` import 시 `PROJECT_ROOT/.env`를 읽어 `os.environ`에 설정. `database.py`는 `psycopg` import. `backtest.py`/`paper.py`/`risk.py`는 모두 이 체인에 속함 | 본 worktree에는 `.env`가 없다(`.env.example`만 존재, 미열람). **`day01.py`는 `settings`·`database`·`paper`·`backtest` 등을 import하지 않는 순수 모듈이어야 한다.** 테스트는 정적 import 목록으로 이를 강제한다 |
| 시간대 | 기존 코드는 `ZoneInfo('Asia/Seoul')`·`timezone(+9)` 혼용, `market_hours`는 증권사 달력 기반(API 필요) | DAY01은 달력 API 없이 합성 달력/KST 고정 규칙으로만 동작해야 한다 |
| 일일 기준선 | `ReplayRisk._metrics`는 KST 날짜 변경 시 시초 자산 재설정 | 다일 테스트에서 손실 한도·주문 횟수가 날짜별로 리셋되어야 한다 |

기존 동작 보존 확인(회귀)은 `tests/test_ml_backtest.py`, `tests/test_ml_backtest_selection.py`, `tests/test_strategy_engine.py` 중 **정적 import 확인을 마친 파일만 개별 지정**하여 실행한다. 전체 탐색은 하지 않는다.

## 2. 코디네이터·Codex에 대한 사전 리뷰 요구사항 (구현 전 기록)

구현 전에 합의되어야 하는 계약이다. 모호하면 테스트가 구현을 따라 쓰이게 되므로 아래를 명시해 달라.

1. **공개 API 시그니처**: 입력 타입(`RawCandle` 재사용 여부, 5분봉 `interval` 값 `'5m'`), 설정 객체, 반환 구조(체결·거부·미체결·일자별 결과·사유 코드)를 문서로 확정.
2. **순수성**: DB·네트워크·파일·시계·난수 없음. `settings`/`database`/`paper`/`psycopg` 미import (정적 검증 대상).
3. **시간 모델**: 의사결정 시각 = `available_at`(5분봉이 완결되어 *도착한* 시각). 신호는 `event_at+5분`이 아니라 **`available_at` 이후**에만 존재. 체결은 신호 가용 시각보다 **엄격히 이후**에 가용한 "다음 체결 가능 봉"의 시가(또는 문서화된 규칙)로만 결정. 지연 도착 봉이 있으면 체결도 그만큼 지연되며, 그 시각 이전 데이터를 보고 체결가를 정하면 안 된다.
4. **미완결·정렬**: 5분 경계(분 % 5 == 0, 초 = 0)가 아닌 `event_at`, `available_at < event_at + 5분` 인 봉은 거부(또는 명시적으로 제외하고 사유 기록). 임의 묵시적 보정 금지.
5. **결측·중복·손상 정책**: 09:00 봉 결측, 중간 결측, 동일 `(symbol,event_at)` 중복(동일값/상이값), tz-naive, NaN/음수/0 가격, 고가<저가, 거래량 음수 각각에 대해 *거부·제외·무거래* 중 무엇인지 사유와 함께 고정. 조용히 값 보간 금지.
6. **전략 정의 (고정 유니버스, top50 재구성 금지)**:
   - 레인지: 09:00–09:15 봉 3개(09:00, 09:05, 09:10 봉; 09:10 봉은 `available_at`≈09:15). 레인지 고가·저가·거래량 기준 정의 필수. 레인지 확정은 09:10 봉 가용 이후.
   - 진입 창: 09:20–11:30. 창 경계 봉(09:15, 09:20, 11:30, 11:35)을 포함/제외 규칙으로 명시(`event_at` 기준인지 `available_at` 기준인지).
   - 거래량 돌파 + VWAP: **정확 VWAP**(전체 1분 거래대금/거래량이 아닌 5분봉만 있으므로 `Σ(typical_price×volume)/Σvolume`인지 `Σ(close×volume)`인지 정의)과 **근사 VWAP**의 차이를 문서화하고, 어느 쪽을 쓰는지 라벨링. 정확/근사를 같은 이름으로 섞지 않는다. 거래량 0 구간의 VWAP 정의(분모 0).
   - 유니버스는 호출자가 고정 리스트로 전달. 일자별로 거래량 상위 N을 계산해 바꾸는 코드가 있으면 결함(미래 정보).
7. **청산·위험**: 같은 봉에서 손절·익절 동시 충족 시 보수적으로 손절 우선. 갭 하락/상승 시 체결가는 손절·익절 가격이 아니라 **갭 시가**(불리한 쪽). 14:50 이후 신규 진입 금지·청산 시도, 15:10 시점 미체결 수량·실패 사유 기록. 일 손실·주문 횟수·현금·노출 한도 위반 시 거부 사유 코드.
8. **비용**: 수수료·세금·슬리피지 정의(매도세 매도만, 슬리피지는 불리한 방향, 수수료 반올림 규칙)를 PAPER와 같거나 다른 점을 명시.
9. **다일 독립성**: 일자 간 포지션 이월 금지 또는 명시. 일자·종목 하나를 추가/제거해도 다른 일자·종목 결과가 불변이어야 한다(단, 자본·노출 한도 공유 종목 간 상호작용은 문서화).
10. **결정성**: 같은 입력(입력 순서만 셔플) → 같은 출력. 입력 리스트를 변형(mutate)하지 않음.
11. **수익성 주장 금지**: 결과 문구에 합성·엔지니어링 검증임을 명시.

## 3. 적대적 합성 시나리오 설계 (구현 전 기록; 실제 테스트는 5절·`tests/test_day01_review.py`)

모든 테스트는 손으로 계산한 기대값(원 단위 Decimal)과 **시간 불변식**을 단정한다. 구현 함수를 재사용해 기대값을 만들지 않는다. 합성 5분봉 생성기는 테스트 안에서 독립적으로 구현한다.

| ID | 시나리오 | 단정 (구현 비의존) |
|---|---|---|
| T-LA1 | 신호 봉의 종가 급등을 *다음 봉 시가*와 크게 다르게 설정 | 체결가는 신호 봉 종가·고가가 아니며 다음 체결 가능 봉 시가(+슬리피지)와 일치 |
| T-LA2 | 신호 봉 `available_at`을 `event_at+5분`보다 7분 늦게 | 체결 시각 > 신호 `available_at`, 지연 전에 체결되지 않음 |
| T-LA3 | 신호 이후 봉이 지연 도착(체결 후보 봉이 2개 봉 뒤에 가용) | 체결은 그 봉 이후, 중간 봉 시가 사용 금지, 지연 한도 초과 시 미체결 사유 |
| T-LA4 | 동일 입력에서 마지막 봉(미래)만 극단값으로 교체 | 그 봉 `available_at` 이전의 모든 결정·체결·지표 불변 |
| T-AV1 | `available_at < event_at+5m` 미완결 봉 | 거부 또는 명시적 제외 (결과에 반영되지 않음) |
| T-AV2 | `event_at`이 5분 경계가 아님(09:03), 초 ≠ 0 | 거부 |
| T-DQ1 | 09:00 봉 결측 | 레인지 미확정 → 해당 일·종목 무거래, 사유 기록, 다른 종목 영향 없음 |
| T-DQ2 | 레인지 중간(09:05)·진입창 중간 봉 결측 | 보간 없이 정의된 정책대로, 결측 봉 시각에 체결 없음 |
| T-DQ3 | 중복(동일/상이 값) | 동일 `(symbol,event_at)` 상이값은 거부, 어느 값을 쓰는지 순서 의존 없음(입력 셔플 동일 결과) |
| T-DQ4 | tz-naive, 비 KST 오프셋(UTC) 같은 순간, 자정 경계 | naive 거부, UTC로 표현된 동일 순간은 KST와 동일 결과, 날짜 분할은 KST 기준 |
| T-DQ5 | NaN/0/음수 가격, high<low, 음수 거래량 | 모두 거부(예외 또는 사유), 부분 오염 결과 없음 |
| T-ST1 | 레인지 상단 돌파 + 거래량 배수 경계 | 정확히 임계값 = 신호/비신호 경계(`>` vs `>=`) 문서와 일치 |
| T-ST2 | 09:15–09:20 사이 돌파 | 진입창 이전이므로 진입 없음 |
| T-ST3 | 11:30 경계 | 문서화한 포함/제외 규칙대로 |
| T-ST4 | 돌파는 있으나 VWAP 아래 | 진입 없음. VWAP 값을 손계산으로 검증(정확 vs 근사가 다르게 되는 데이터 선택) |
| T-ST5 | 하루 중 여러 번 돌파 | 종목·일 최대 진입 횟수 규칙 |
| T-ST6 | 유니버스에 없는 종목의 거래량이 폭증 | 유니버스 밖 종목은 어떤 결과에도 영향 없음, 유니버스 재구성 흔적 없음 |
| T-EX1 | 같은 봉에서 저가≤손절, 고가≥익절 | 손절 가격(보수적) 체결, 익절 체결 아님 |
| T-EX2 | 다음 봉 시가가 손절가 아래로 갭 | 체결가 = 갭 시가(불리), 손절가 아님. 익절 갭 상승은 유리하게 가정하지 않음(정의 확인) |
| T-EX3 | 14:50 청산 시도 시 다음 체결 가능 봉 없음/거래량 0 | 미체결 수량·사유 기록, 15:10에 미체결 수량 보고, 포지션을 장부상 임의 청산 금지 |
| T-EX4 | 진입 신호 14:50 이후 | 신규 진입 금지 |
| T-CO1 | 단일 왕복, 수수료·세금·슬리피지 | 손계산: 매수 `P*(1+s)`, 수수료 floor, 매도세 floor 등 원 단위 일치, 순손익 = 합계 정합(현금 보존) |
| T-CO2 | 비용 0 vs 비용>0 | 비용>0 순손익은 항상 더 작음, 차이 = 수수료+세금+슬리피지 합 |
| T-CO3 | 수수료 반올림 경계(금액×율이 .999) | floor 규칙 |
| T-RK1 | 일 손실 한도 도달(정확히 −한도 / −한도+1원) | 경계 규칙대로 신규 진입 차단, 기존 포지션 청산은 허용 |
| T-RK2 | 일 주문 횟수 한도 | 초과 주문 거부 사유 코드, 다음 날 리셋 |
| T-RK3 | 현금 부족/최소 현금 비율/종목·전체 노출 한도 | 거부 사유, 체결 수량 축소 정책(있다면) 정확 |
| T-RK4 | 다종목 동시 신호 시 한도 소진 순서 | 순서가 결정적(종목 정렬), 입력 순서 비의존 |
| T-MD1 | 다일(3일), 하루 손실 후 다음 날 | 일 단위 한도 리셋, 포지션 이월 규칙, 일자별 결과 합 = 총 결과 |
| T-MD2 | 일자/종목 추가·삭제 | 다른 일자·종목 결과 불변(독립성 속성) |
| T-RG1 | 기존 `replay`(MA) 회귀 | 기존 `tests.test_ml_backtest` 결과·`backtest.py` 파일 해시 불변 |
| T-IM1 | `day01.py` 정적 AST 검사 | `settings/database/paper/psycopg/requests/os.environ/open/socket/subprocess` 비import, `datetime.now`·`random` 비사용 |
| T-DT1 | 입력 셔플·재실행 | 결과 동일, 입력 불변 |

속성 기반 보강: 무작위(시드 고정) 합성 일중 경로 여러 개에 대해 (a) 체결 시각 > 신호 `available_at`, (b) 현금 ≥ 0, (c) 일별 순손익 합 = 총 순손익, (d) 체결가가 해당 봉의 [저가×(1−s), 고가×(1+s)] 범위 내 — 불변식을 단정한다. 무작위 값은 `random.Random(seed)`로 고정하고 기대값은 불변식으로만 확인한다.

## 4. 리뷰 방법 (구현 전 계획)

1. 전달된 commit의 diff 전체를 읽는다(최신 커밋만이 아니라 cherry-pick된 모든 커밋).
2. 위 계약 1–11의 항목별로 코드와 사양 문서를 대조하고 *근거 줄 번호*와 함께 결함을 기록한다.
3. 결함마다 최소 재현(입력 봉 목록, 기대, 실제)을 작성해 코디네이터에게 보고하고 Codex 수정 후 독립 재실행한다.
4. 리뷰 결과(발견·수정 상태·미검증)는 구현 전달 후 이 문서의 아래 섹션을 갱신한다.

## 5. 결과

상태: **합성 자동 테스트와 코드 리뷰 2회차 반영 완료. 사용자 동작 검증·운영 적용 없음. 수익성 주장 없음.** 이 브랜치는 구현(Codex)과 독립 리뷰를 함께 담은 통합 후보이며 main에는 병합하지 않았다.

### 1회차 (구현 `1595e65`, 로컬 cherry-pick `5b73ef3`)

결함 3건을 보고했고 테스트 4개를 expectedFailure로 고정했다.

| ID | 심각도 | 내용 | 2회차 상태 |
|---|---|---|---|
| D1 | 높음 | 지연 도착 봉이 평가가격·손절/목표 감시에서 무시됨 | 수정 확인 |
| D2 | 중간 | 신호 후 몇 시간 뒤 첫 on-time 봉에서도 매수 체결 | 수정 확인 (`max_signal_to_fill_minutes`) |
| D3 | 낮음 | 전날 미청산 시 사유가 `ALREADY_ENTERED_OR_PENDING`으로 기록 | 수정 확인 |

### 2회차 (수정 `bad9d2152879c512d3efa768c06287afd8a19537`, 로컬 cherry-pick `626d7bd`)

독립 소스 검토 요지:

- **D1**: 당일 양(+)거래량 지연 봉은 도착 시점에 즉시 `monitor_exit`에서 손절·목표를 판단하고(과거 저가도 위험), 평가가격은 `event_at` 최고점(high-water) 규칙으로 더 오래된 봉이 새 평가가격을 덮어쓰지 않는다. 지연 위험이 있으면 신규 신호·체결이 `LATE_RISK_EXIT_PENDING`으로 차단되고 청산은 이후 on-time 봉에서만 체결된다. 나중에 알게 된 손절은 대기 중인 목표/청산 주문을 `EXIT_REPLACED_BY_STOP`으로 대체한다. 지연 위험은 같은 시각 도착 순서와 무관하게 매수 체결보다 먼저 소비된다.
- **D2**: 만료는 배타적(`age >= 한도`)이며 타이머 배치가 있어 후속 시세가 없어도 09:22에 `ENTRY_SIGNAL_TOO_OLD`가 기록된다. 상한 130분, 11:30 만료는 `ENTRY_SIGNAL_TOO_OLD`와 일치 시 우선 기록.
- **D3**: 미청산 노출 사유가 우선 기록되고 summary에 `overnight_gap_exposure_unmodeled`, `frozen_valuation_symbols`, 결과에 `valuation_marks`(가격·event_at·observed_at·frozen)가 추가됨.
- 추가로 발견한 결함은 없다. 관찰: 손절 대체 시 청산 주문 수(`total_orders`)가 대체마다 증가한다(위험 감소 주문은 항상 허용·총계에 포함한다는 사양과 일치). 기존 테스트 3개가 새 기본 만료(2분)와 충돌해 명시적 `max_signal_to_fill_minutes`로 구성했다(20분 또는 5분). 안전 단언은 약화하지 않았다.

신규 테스트(`SecondRound` 13개 등): 만료 경계(1분 체결/정확히 2분 만료), 소수 분 수명, 수명 설정 범위, 타이머 단독 만료(09:22·11:30), 오래된 지연 급락의 STOP 신호와 최신 평가가격 보존, 더 새로운 지연 봉의 도착 시점 평가와 근거 기록, 0거래량 지연 급락 무시, 동시 도착 신규 신호 차단, 동시 도착 대기 매수 체결 차단, 전날 지연 봉 비영향, 미청산 요약 플래그·`valuation_marks`, 지연 목표 관측과 목표가 상한, 지연 손절의 대기 목표 대체.

### 실행 결과 (대상 HEAD는 작업 기록 참조, 구현 `bad9d21` 적용 후)

| 명령 | 결과 |
|---|---|
| `python -B -m unittest tests.test_day01_review` | **79개 실행, 79 통과, 실패 0, 오류 0, expected failure 0, unexpected success 0**, 약 8.8초 |
| `python -B -m unittest discover -s tests -p test_day01.py` | Codex 45개 통과 |

변이 검증(구현 사본을 임시 폴더에서 변형, 저장소 구현 미수정): 2회차 로직 8개 변이(지연 위험 차단 제거, 만료 `>=`→`>`, high-water 제거, 지연 위험 소비 제거, 미청산 사유 우선순위 제거, 만료 타이머 제거, 손절의 대기 주문 대체 제거 등) 중 7개를 탐지. 미탐지 1개는 동치 변이(지연 봉 날짜 검사는 `event_at < entry_at` 검사와 중복). 1회차 변이 12개도 계속 탐지.

### 남은 한계 / 미검증

- 다음날 갭은 모델링하지 않는다(사양, summary 플래그로 표시). 미청산 발생 후 이후 전 기간 신규 진입이 차단된다.
- 체결가는 종가 기반 last-price proxy, 손절 기준가는 후속 봉 범위 밖일 수 있다. 참여율 모델은 단순하다. 호가·틱·우선순위·휴장일 달력 없음.
- 정확 VWAP 모드에서 거래량 0 분봉의 turnover 누락은 그날 신호를 차단한다(보수적).
- 기존 MA/PAPER/백테스트 실행 회귀는 `settings`/DB import 경로 때문에 실행하지 않았다. `git diff origin/main..HEAD`의 변경 경로는 신규 7개 파일뿐이다(기존 파일 수정 없음).
- 실제 시장 데이터, PAPER/LIVE, 운영 환경, 사용자 동작 검증은 수행하지 않았다.

## 6. next-open 개선 검증 준비 (2회차 이후 신규 작업, 진행 중)

작업 기록: [20261112-Claude-DAY01-next-open-review](../plans/ai-tasks/20261112-Claude-DAY01-next-open-review.md). 이 절은 구현 인계 전의 **요구사항·회귀 설계**이며 구현 검증 결과가 아니다. 5절은 직전 close-proxy 모델의 최종 결과로 유지한다.

### 6.1 인과성 요구사항 (Codex 계약안에 대한 독립 비평 요지)

아래 A~H는 코디네이터로 전달한 원문이며, 구현 인계 후 항목별로 테스트한다.

```text
Claude critique of next-open contract (accept core; these are requirements/edge cases, I will test each):
A (top risk) ENTRY-BAR RANGE: legacy skipped the fill bar's own low/high because fill was at its close. With OPEN fill the fill bar's range happens AFTER entry. Stop/target monitoring must include the bar whose event_at == fill open (observed at its available_at), dual-hit STOP-first; exit then queues for the next later open (strictly > signal_at). Ignoring it = optimistic. Test: entry open 102.051, same bar low<stop => STOP signal at bar available_at, exit at following open.
B ABSENT QUOTES: default mode yields zero fills on completed-candle-only data: acceptable only if documented in spec/result (summary should count fills by price_source, open_quote_fills, rejected/late quotes) and never invent capacity. If an optional modeled mode is added it must be a separately named execution value (e.g. modeled_candle_open), off by default, result echoes execution_model + list of hindsight used. Causal capacity proxy there: participation x volume of already-known completed on-time bars (trailing min/avg at signal time), NOT the fill bar's volume; fill-bar existence/volume>0 is hindsight and must be named. Late candle => no fill, no backdating.
C TIE ORDER: quotes at T before candles arriving at T (agreed). Also expiry/freeze timers at T run BEFORE quotes at T (deadline exclusive: quote exactly at signal+2min must not fill; 15:10 quote must not fill; 15:09 quote may, even though its candle is observed 15:10). Define and test both. Marks: a quote is a valid same-day mark (event_at-keyed high-water); on equal event_at the completed candle (later info) should win; older candle must not overwrite newer quote.
D QUOTE VALIDATION: price finite Decimal>0, executable_volume finite Decimal>=0, aware tz, second==0, session [09:00,15:30), duplicate (symbol,event_at) ValueError, available_at<event_at ValueError, available_at>event_at => reason LATE_OPEN_QUOTE_NOT_EXECUTABLE (not exception, never fills, may still be recorded), out-of-universe ignored without validation (same as candles). available_at==event_at exact equality: state it (no tolerance) and that real feeds therefore rarely qualify.
E CAPACITY: floor(executable_volume*participation); <1 => NO_EXECUTABLE_CAPACITY, order stays queued until expiry (entry) / 15:10 (exit); partial entry remainder cancelled with reason; exit remainder keeps queue; quantity never exceeds ordered. Quote price used as reference; adverse slippage/fee/tax unchanged; no synthetic min/caps (agree). Fill risk checks use marks before T plus all same-time opens, sells before buys then symbol order; BUY at T reduces cash/exposure seen by later BUY at T.
F PRESERVE ROUND-2 SEMANTICS in new mode: late candle risk still arrives via candles (monitor_exit late), LATE_RISK_EXIT_PENDING blocks fills/signals, stop supersedes pending target/liquidation, unresolved freeze flags, valuation_marks. Trades need price_source plus signal_at/event_at(open time)/observed_at(=available_at)/execution_at; evidence_close should not be silently None-confused (use separate field or explicit None).
G LEGACY: Config.execution default next_open, legacy_close preserved with identical outputs (I will pin execution='legacy_close' in my existing helper without weakening assertions and add new default tests). Config validation: unknown execution rejected, open_quotes with legacy mode ignored or rejected (pick one, document).
H Consistency check: when the completed candle for a minute with a quote arrives and quote price lies outside [low,high], record a mismatch reason (no retroactive change to the fill).
SAFE REGRESSION (my disposable runner, outside repo; proposal): python -B <scratchpad>/safe_runner.py <worktree> tests.test_ml_backtest tests.test_strategy_engine tests.test_paper_management tests.test_paper_feed ; it blocks socket/psycopg connects (only ephemeral loopback allowed for asyncio), refuses if .env exists, strips POSTGRES_/TOSS_ env, blocks .env* reads and .paper-history, excludes 1 journal-writing test, engine logic unmocked. Verified now: 55 tests OK on this branch (12 backtest+31-1 strategy+paper_management+paper_feed). Not runnable safely: test_paper, test_ml_backtest_selection, data_pipeline etc (setUp needs real DB). Dependency: psycopg must import (paper/risk/backtest import it, no connection); settings reads .env at import (absent here, runner refuses otherwise). Your '124' count is not reproduced by this set; tell me the exact module list if you want that count.
```

### 6.2 안전한 최소 기존 회귀 (격리 실행기)

기존 `backtest`, `strategy`, `paper`는 import 시 `settings`(→ `.env` 존재 시 읽음)와 `psycopg`(import만, 연결 없음)에 닿는다. `ml/backtest.py`의 `ReplayBroker`는 저장을 비활성화하고 엔진의 `decision_recorder`는 기본 None이므로 `test_ml_backtest`와 `test_strategy_engine`은 DB 연결 없이 동작하도록 설계되어 있다. 정적 확인(import AST·`connect`·`.paper-history` 검색)으로 분류했다.

- 실행 가능(격리 실행기 사용): `tests.test_ml_backtest`, `tests.test_strategy_engine`(1개 제외), `tests.test_paper_management`, `tests.test_paper_feed`.
- 실행 불가(setUp에서 실제 DB 필요): `test_paper`, `test_ml_backtest_selection`, `test_ml_data_pipeline` 등. `test_strategy_engine.test_journal_survives_reset...`은 `.paper-history`에 쓰므로 제외.
- 실행기는 저장소 밖 임시 경로에 두고 소스를 변경하지 않는다. 엔진 로직은 스텁하지 않으며 네트워크·DB 연결(ephemeral loopback 제외), `.env*` 읽기, `.paper-history` 접근, 자격증명 환경변수를 차단한다. 한계: 차단은 Python 레벨(`socket`, `psycopg.connect`, `open`)이므로 C 확장의 직접 연결은 막지 못한다(해당 경로는 `psycopg.connect` 차단으로 도달 불가).

실행기는 저장소의 `tests/day01_safe_regression_runner.py`(검토·재현 가능한 검증 유틸리티, `test_*`가 아니어서 탐색 대상 아님, 오케스트레이션 시스템 아님)로 제공한다. 엔진 코드는 변경·스텁하지 않는다.

- `.env`: 존재 확인(`exists`)은 허용(settings가 사용)하되 실행기는 `<root>/.env`가 있으면 **실행 거부**하고, `.env*` 파일을 **여는 시도**는 OS 접근 전에 차단한다(내용 읽기 불가). 내용은 읽지 않는다.
- 민감 환경변수(POSTGRES_/TOSS_/DART_/OPENAI_/ANTHROPIC_/AWS_/…_TOKEN/_SECRET/_PASSWORD 등): **값을 출력하지 않고 이름만 표시하며 실행을 거부**한다. 하네스가 주입한 토큰 변수는 실행 시 `env -u 이름`으로 해당 프로세스에서만 제외한다(값 미열람).
- 네트워크: 모든 connect/bind/listen/accept/create_connection/getaddrinfo 차단. 예외는 CPython `socket._fallback_socketpair` 프레임 내부(asyncio 자체 파이프, loopback·임시 포트)뿐이며 서버 시작·외부 요청은 없다. psycopg는 `connect`, `Connection/AsyncConnection.connect`, pq 진입점, pool 클래스를 차단한다.
- 제외 테스트(정확한 ID와 사유): `tests.test_strategy_engine.StrategyEngineTests.test_journal_survives_reset_and_contains_costs_reason_and_owner` — 보호 경로 `.paper-history`에 저널 파일을 쓰기 때문. 실행 시 출력에 EXCLUDED로 표시된다.
- 한계: 프로세스 내 monkeypatch이며 OS 샌드박스가 아니다. 위 Python 진입점을 거치지 않는 C 확장의 직접 소켓은 가로채지 못한다.

명령(하네스 토큰 변수는 `env -u`로 제외, 무관한 값은 출력하지 않음):

```text
python -B tests/day01_safe_regression_runner.py --selftest
python -B tests/day01_safe_regression_runner.py tests.test_ml_backtest tests.test_strategy_engine tests.test_paper_management tests.test_paper_feed
```

2026-11-12 준비 단계 실행 결과(HEAD `4bf3f8d` 기준): `--selftest` 11개 항목 모두 PASS(차단 10 + asyncio 루프 허용 1). 회귀 55개 실행, 통과 55, 실패·오류 0, 제외 1. 구분: **기존(MA/PAPER/백테스트) 안전 회귀 55개**는 DAY01 테스트 124개(Codex 45 + Claude 79)와 별개이며, 최종 보고에서 [DAY01 legacy 124] / [DAY01 신규 기본모드] / [기존 안전 회귀 55 + 제외 1]을 각각 구분해 기록한다.

## 7. next-open 개선본 최종 검토 (구현 `348f34237a90a79fc432e2af2616c13d84b1084a`, 로컬 cherry-pick `efceb08`)

작업 기록: [20261112-Claude-DAY01-next-open-review](../plans/ai-tasks/20261112-Claude-DAY01-next-open-review.md). Codex 전체 이력을 병합하지 않고 이 commit만 clean 상태에서 cherry-pick했다(변경 4경로: `auto_trader/ml/day01.py`, `tests/test_day01.py`, `docs/specs/DAY01_BACKTEST.md`, Codex 작업 기록).

### 7.1 소스 독립 검토 요지

- **계약**: `Config.execution` 기본 `next_open`, 명시적 `legacy_close`. `OpenQuote(symbol,event_at,available_at,price,executable_volume)`와 `replay(..., open_quotes=())`. 체결은 `signal_at < open event_at`이고 `available_at == event_at`인 시가 스냅샷에서만, 가격은 스냅샷 가격(합성 캡 없음), 수량은 `floor(executable_volume × participation)`이다. 완성 분봉의 volume/OHLC/종가로 체결가·수량·적격성을 결정하지 않는다.
- **순서(요구사항 A~H 대응)**: 한 시각의 처리는 만료·14:50·15:10 타이머 → 해당 시각 시가 마크 → 늦게 도착한 시가의 위험 → 정상 시가 체결(매도 우선, 종목 순) → 새 갭 트리거(같은 시가에서 체결 불가) → 완성 분봉 도착 → 분봉 위험·신호 순이다. 소스 확인 결과 체결 위험 검사는 이전 마크와 같은 시각의 모든 시가만 사용한다.
- **진입 분봉 범위(최대 위험, A)**: 진입 시각(시가 시각) 분봉이 `event_at >= entry_at`로 손절·목표 감시에 포함되어, 시가 체결 이후 그 분봉에서 하락해도 손절 신호가 분봉 도착 시점에 생성되고 동시 터치는 STOP 우선이다. 청산은 신호 시각보다 엄격히 이후의 시가에서만 체결된다.
- **만료(C)**: 만료는 시가 물리 시각 기준이며 배타적이다(`age >= 한도`). 타이머가 먼저 실행되어 정확히 한도 시각의 시가는 체결되지 않는다. 청산 주문은 만료되지 않고 15:10까지 대기하며 이후 시가의 실제 가격으로 체결된다(보고된 한계).
- **마크 출처(C)**: 시가 마크는 `event_at` high-water 규칙을 따르고, 같은 `event_at`에서는 완성 분봉이 시가 마크를 대체한다. 더 오래된 분봉은 새 마크를 덮어쓰지 않는다. `valuation_marks`에 출처(`price_source`)가 기록된다.
- **검증(D)**: `OpenQuote.validate`는 symbol 정규화·tz-aware·분 정렬·세션 범위·유한 Decimal·`available_at >= event_at`을 요구하고 중복 `(symbol,event_at)`은 거부한다. 늦은 시가는 `LATE_OPEN_QUOTE_NOT_EXECUTABLE` 사유로 기록되고 체결되지 않는다. 유니버스 밖 시가는 검증하지 않는다.
- **출처 표기(B)**: 체결에 `price_source`(`OPEN_QUOTE`/`LEGACY_CLOSE_PROXY`), `decision_at`, `observation_at`, `evidence_close=None`이 있고, 결과에 `execution_evidence`(제출·정시·지연·용량미달 시가 수, `missing_open_quotes`)와 summary의 `open_quote_fills`/`legacy_close_fills`가 있다. 시세 없이 완성 분봉만 주면 체결 0건이며 `NO_EXECUTABLE_OPEN_QUOTE_BEFORE_EXPIRY/DEADLINE`로 명시된다(가짜 용량 없음).
- **결함**: 이 검토에서 새 결함은 발견하지 못했다. 관찰(결함 아님): (1) 늦은 **시가**의 위험은 같은 시각 체결 전에 소비되어 매수 체결을 막지만(보수적), 늦은 **분봉**의 위험은 체결 후에 소비되어 같은 시각 시가 체결을 막지 않는다(분봉은 시가 이후에 알게 되는 정보라는 계약과 일치, 테스트로 고정). (2) 청산 주문은 만료가 없어 매우 늦은 시가에서도 체결된다. (3) `executable_volume`은 호출자가 제공하는 증거이며 이 모듈은 그 진위나 시가 대비 분봉 시가 불일치 때문에 체결을 바꾸지 않는다(`OPEN_CANDLE_PRICE_MISMATCH`로 표시만).

### 7.2 독립 테스트 (`tests/test_day01_review.py`)

기존 79개는 헬퍼가 `execution='legacy_close'`를 기본으로 지정하도록만 바꿨고 단언은 약화·삭제하지 않았다. 신규 29개는 기본 모드 테스트(`NextOpen` 28 + `NextOpenProperties` 1)이다:

- 기본값·모드 라벨, 시세 없는 분봉만의 무체결, 신호 시각보다 엄격히 이후의 첫 시가와 타임스탬프·출처 필드 정확값, 만료 경계(1분 체결/정확히 2분 만료), 지연 시가 무체결·무소급, 용량의 시가 기원(분봉 거래량 무관)·부분 체결·0/미만 1 용량 대기·participation.
- 진입 분봉 범위·동시 터치 STOP 우선, 갭 시가가 신호를 만들되 같은 시가에서 체결 불가, 갭 상승 목표, 손절 경계(정확히 stop 가격 / 한 틱 위), 같은 시각 분봉이 체결가를 바꾸지 못함, 마크의 시가·분봉 동률/구 분봉 비덮어쓰기, 시가-분봉 불일치 표시(분봉 선도착·시가 지연 도착 양쪽).
- 지연 시가 위험의 도착 시점 반영과 매수 체결 차단, 늦은 분봉 위험이 같은 시각 시가 체결을 막지 않는 계약, 같은 시가에서 매도가 먼저 포지션 슬롯을 비움, 같은 시가 매수의 현금 소진(종목 순), 14:50 시가 비체결·부분 청산 잔량 대기·주문 수, 15:10 배타(15:09 체결/15:10 불체결), 청산 주문 비만료, 진입창 11:30 시가 경계, 미청산 동결 시 0이 아닌 시가 마크·출처·요약 플래그, 시가 검증 전반, 입력 셔플 결정성.
- 속성 테스트: 25개 시드 × 2종목 × 2일의 무작위 경로에서 정시 시가만 체결, 체결 시각 > 신호 시각, 수량 ≤ 시가 용량, 가격 = 시가 ± 슬리피지, 현금 ≥ 0, 종목·일 1회 진입, 진입창·15:10 경계, 출처 카운터 일치, 셔플 동일 결과(비공허: 진입·청산 각 5회 이상 요구).

변이 검증(구현 사본을 임시 폴더에서 변형, 저장소 구현 미수정): next-open 로직 변이 11개 중 6개를 처음부터 탐지했고 3개(손절 경계, 시가-분봉 불일치 표시, 매도 우선 정렬)는 테스트를 추가해 탐지했다. 미탐지 2개는 동치 변이이다(15:10 이후 시가 필터 제거는 대기 주문 삭제·미청산 보호와 중복, 진입 시가 자체의 손절 감시 생략은 진입가 기준 손절선 구조상 발동 불가). 이전 단계 변이 검증 결과(5절)는 그대로 유효하다.

### 7.3 실행 결과 (대상 HEAD는 작업 기록·최종 보고 참조)

| 구분 | 명령 | 결과 |
|---|---|---|
| DAY01 legacy 124 (Codex 45 + Claude 79) | 아래 두 명령의 legacy_close 부분 | 통과 (아래 합계에 포함) |
| Codex (legacy 45 + 신규 22) | `python -B -m unittest discover -s tests -p test_day01.py` | **67개 실행, 67 통과** |
| Claude 독립 (legacy 고정 79 + 신규 기본 모드 29) | `python -B -m unittest tests.test_day01_review` | **108개 실행, 108 통과**, 실패·오류·expected failure 0, 약 18초 |
| 기존 MA/PAPER/백테스트 안전 회귀 (DAY01과 별개) | `env -u <하네스 토큰 변수> python -B tests/day01_safe_regression_runner.py tests.test_ml_backtest tests.test_strategy_engine tests.test_paper_management tests.test_paper_feed` | **55개 실행, 55 통과**, 제외 1(`tests.test_strategy_engine.StrategyEngineTests.test_journal_survives_reset_and_contains_costs_reason_and_owner`: `.paper-history` 파일 쓰기) |
| 실행기 자체 점검 | `... day01_safe_regression_runner.py --selftest` | 11/11 PASS |

중복 집계 주의: Codex 67과 Claude 108은 서로 다른 파일의 테스트이며 일부 시나리오가 의미상 겹칠 수 있으나 코드·파일은 중복이 없다. DAY01 합성 테스트 합계 175(= 67 + 108)이며, 55개 기존 회귀와는 겹치지 않는다. 124 = legacy 45 + legacy 79는 위 175 안의 부분집합이다.

### 7.4 미검증·한계

- 실제 시장 데이터, 실제 시가 스냅샷 피드, 호가·체결 우선순위, 휴장일, PAPER/LIVE 연동, 운영 환경, 사용자 동작 검증은 하지 않았다. 시가 증거가 없으면 기본 모드는 체결하지 않는다.
- DB가 필요한 기존 테스트(`test_paper`, `test_ml_backtest_selection`, `test_ml_data_pipeline` 등)는 실행하지 않았다.
- 격리 실행기는 Python 수준 차단이며 OS 샌드박스가 아니다.
- 다음날 갭은 모델링하지 않는다(미청산은 동결·표시). 수익성에 대한 주장 없음.

## 8. 실제 데이터 연결 준비 검토 (진행 중, 구현 인계 전)

작업 기록: [20261112-Claude-DAY01-data-prep-review](../plans/ai-tasks/20261112-Claude-DAY01-data-prep-review.md). (구현 인계 전 기록이며 최종 결과는 9절.) 아래는 소스·스키마·문서만 읽은 독립 조사다(DB·RAW 데이터·외부 API·`.env` 접근 없음). 문서에 적힌 과거 수집 수치(예: 22종목·14 usable 날짜)는 이 조사에서 **재확인하지 않았으며** 현재 보유 데이터의 커버리지를 주장하지 않는다.

### 8.1 엄격한 시가 증거가 실제로 가능한가 (결론: 현재 저장 데이터로는 불가능)

| 항목 | 소스 근거 | 의미 |
|---|---|---|
| 저장되는 것은 완성 1분봉 OHLCV뿐 | `schema.sql` `ml_raw_candles`(source,symbol,interval,event_at,available_at,collected_at,OHLC,volume,raw_payload) | 호가·체결 틱·시가 스냅샷·잔량 컬럼 없음 |
| `available_at`은 수신 시각 | `collect_candles.py`: `RawCandle.from_live_candle(..., collected_at=received_at)` → `available_at = collected_at`, 완료 봉만(`timestamp+1분 <= received_at`) 저장 | 정시(`event_at+1분`)가 아니라 폴링 수신 시각이다. 백필은 백필 수신 시각을 갖는다 |
| 거래대금(turnover) 없음 | `RawCandle`·스키마에 turnover 컬럼 없음, `raw_payload`는 timestamp/OHLC/volume만(`raw_payload()`) | 정확 VWAP(`exact`)는 저장 데이터에서 항상 `MISSING_EXACT_TURNOVER`. 연구에서는 `typical_approximation`을 명시 표기해야 한다 |
| 실시간 체결·호가는 저장되지 않음 | `quote_stream.py`는 WebSocket `trade:kr` 프레임을 브라우저 큐로 중계만 하고 영속화·수신 시각 기록 없음. `/api/v1/orderbook`·`/trades`는 요청 분류만 있고 저장 경로 없음 | 시가 시점 가격·체결가능수량의 사실 증거가 없다. 완성 봉의 전체 거래량은 시가 시점 유동성의 증거가 아니다 |
| 세션 봉 수 불일치 | `gap_repair.py`·`quality_report.py`: `FULL_SESSION_BARS = 391`(09:00~**15:30 포함**) vs 문서의 "09:01~15:30 각 390개" vs `day01.Candle.validate`: `event_at < 15:30`만 허용 | 15:30 봉이 포함된 실제 행을 `replay`에 넣으면 전체가 `ValueError`로 중단된다. 입력 어댑터가 15:30 봉(및 세션 밖 봉)을 명시적으로 분리·보고해야 한다 |

따라서 `execution='next_open'`(기본)은 합성 `OpenQuote` 없이는 체결 0건이 맞고, 완성 봉 기반 실제 데이터에 대한 모든 체결은 **추정**이어야 하며 그렇게 표시해야 한다.

### 8.2 Codex 계약안에 대한 독립 요구사항 (인계 전 비평)

1. **엄격 모드 불변**: `Config.execution` 기본 `next_open`과 기존 `legacy_close`·`OpenQuote` 동작·테스트(175개)를 바꾸지 않는다. 연구용은 별도 값(예: `research_estimated_open`)·별도 모듈에서만 켠다. 결과에 `execution_model`과 `hindsight` 표기를 반드시 포함하고 `open_quote_fills`와 섞지 않는다(`estimated_open_fills` 별도).
2. **추정 시가 모델 정의**: 체결 가격 = 신호 가용 시각보다 엄격히 늦은 첫 정시 완성 1분봉의 `open_price`. 그 가격과 "그 봉이 존재하고 거래량>0"이라는 적격성은 **사후(hindsight) 정보**임을 필드(`price_source='ESTIMATED_CANDLE_OPEN'`, `hindsight_fields=[...]`)로 투명하게 표시한다.
3. **용량**: 체결 봉의 전체 거래량, 또는 그 이후 봉의 거래량을 시가 시점 용량으로 쓰지 않는다. 인과적 대안만 허용: 신호 시점에 이미 알려진 직전 완성 봉들의 거래량(최소/평균)에 participation 적용 후 신호 시점에 수량을 고정. 용량 미달·0은 무체결.
4. **인과성**: 신호·위험·수량은 `available_at <= 판단 시각` 정보만. 체결은 시가 시각이지만 가격 행은 나중에 도착하므로 체결 효과(현금·노출)를 신호 시점에 예약(reserve)해 그 사이 판단이 이미 소진된 자금을 쓰지 못하게 한다. 과거 체결 시점의 위험 검사에 그 시점 이후 도착한 종가·마크를 쓰지 않는다. 체결은 `execution_at`(시가 시각)과 `observed_at`(행 `available_at`) 둘 다 기록한다.
5. **불확실·누락·지연 정책 명시**: 다음 봉 누락 → 존재하는 다음 봉 시가(만료 한도 내), 만료는 시가 시각 기준 배타적. 봉이 허용 지연(`max_observation_lag` 같은 명시 설정, 기본은 보수적)보다 늦게 도착하면 비적격이며 소급 체결하지 않는다. 늦은 도착 정책은 암묵적으로 `available_at`을 고쳐 쓰지 않는다.
6. **리스크·이벤트**: 진입 분봉 범위의 손절/목표 감시 포함(동시 터치 STOP 우선), 같은 시각 이벤트 순서, 진입 만료, 14:50 청산·15:10 배타 마감, 미청산 수량·동결 평가·`overnight_gap_exposure_unmodeled` 표시, 일 손실·주문·현금·노출 한도는 기존 엄격 모드와 같은 의미로 유지.
7. **비용 산식**: 수수료·세금·슬리피지 산식은 기존과 동일(KRW 정수 내림, 매도세 매도만, 불리한 슬리피지). 독립 테스트가 손계산으로 확인한다.
8. **입력 어댑터(최소)**: 저장 행(dict)을 `Candle`로 변환하되 `source`, `collected_at`, `available_at`을 보존·보고하고(`Candle`에 필드가 없으면 별도 provenance 반환), `available_at` 누락·`interval != '1m'`은 거부하며 값을 만들어 채우지 않는다. 15:30 이후·세션 밖 봉은 조용히 버리지 말고 개수와 함께 명시적으로 보고(또는 거부). turnover는 없으므로 `None`.
9. **커버리지 함수(순수)**: 호출자가 준 행과 **명시적 기대 날짜**·고정 종목 목록(22종목 등)만 입력받는다. 날짜별·종목별 존재 분 수, 결측 분 구간, 지연 도착 수, `NO_ROWS`(휴장인지 미수집인지 단정하지 않음)를 보고한다. 달력·DB·top50 재구성 금지. 기대 분 격자(390 vs 391)는 호출자가 명시하게 한다.
10. 신규 결함은 인계된 코드에서 발견되는 대로 코디네이터에 보고한다.

### 8.3 인계 후 검증 계획 (9절로 대체됨)

- 기준선: `tests.test_day01_review`(108), `test_day01.py`(67) = 175, 안전 회귀 55 + 실행기 자체 점검 11. 신규 연구용 모드·어댑터·커버리지 테스트는 `tests/test_day01_research_review.py`에 둔다(합성 행만, `settings`·`database` 비import).
- 결과·정확한 개수·SHA는 인계 후 이 절을 갱신한다. 현재 8절은 준비 단계이며 검증 결과가 아니다.

## 9. 연구용 추정 시가 모드·입력 어댑터·커버리지 최종 검토 (구현 `e4128405363348b9e87927d1da67d92773df8f4f`→`fa5a79c`, 수정 `ba765e1adbbabc8e65d4d3e300ba68ec449003e7`→`ee0caea`, 수정 `9908454a5176daa347f53fc39b41b04dbaf5a1ea`→`adc6e38`, 로컬 cherry-pick)

작업 기록: [20261112-Claude-DAY01-data-prep-review](../plans/ai-tasks/20261112-Claude-DAY01-data-prep-review.md). Codex 동기화 merge 없이 이 commit만 cherry-pick했다(변경 6경로: `auto_trader/ml/day01.py`, 신규 `auto_trader/ml/day01_research.py`, `tests/test_day01_research.py`, `docs/specs/DAY01_BACKTEST.md`, `docs/technical/DAY01_DATA_READINESS.md`, Codex 작업 기록).

### 9.1 소스 독립 검토 결과

- **엄격·legacy 불변**: 전략 판단·체결·위험 로직은 `research` 분기 밖에서 동일하다(검토: `evaluate_entry`의 `elapsed`는 엄격 모드에서 `five[-1].end_at == at`이므로 동일, 결정 트리거 식 동일, 만료 유예 0, `mark` 동률 규칙은 연구용 값만 확장). 결과 dict에 `mode`·`uncertain`·추가 요약 키가 더해졌고(값 동작은 불변), 기존 175개 테스트 전부 통과.
- **분리**: 연구용 모드는 별도 `execution` 값이며 `open_quotes`는 `next_open`에서만 허용된다. 결과는 `execution_model='research_estimated_open_observation_settlement'`, `estimated_open_fills`, `physical_fill_claim=False`, `capacity='configured_order_quantity_not_liquidity'`로 구분되고 가짜 `OpenQuote`는 만들지 않는다.
- **인과성**: 결정은 실제 도착 시각(`available_at`)에 `research_max_observation_lag_minutes`(기본 2, 0~30) 이내의 신선한 5분봉으로만 하며 `available_at`은 보존된다. 현금·위험·종목 수·노출은 신호 시점 스냅샷으로 예약되고 만료+1분+지연 유예 동안 다른 판단에 반영된다. 체결 결과는 관측 시각에 정산(`execution_at`)되고 `estimated_execution_at`은 반사실 시각이다. 정산 시 사후 위험 재검사는 없고, 모델 시가 이전에 알려진 차단 정보(`RESEARCH_PRE_OPEN`)만 취소 사유가 된다. 후보 봉은 이벤트 순서로 처리되며 앞선 후보가 관측되거나 봉 끝+지연 타임아웃에 이를 때까지 기다린다.
- **리스크**: 정산 후 이미 알려진 봉을 진입 봉 범위부터 STOP 우선으로 소급 적용(catch-up)하고 최신 알려진 마크로 평가한다. 15:10 동결이 정산보다 먼저이며 15:10 이후 관측은 미청산으로 남는다. 스냅샷을 초과하는 갭은 모델 거래·반사실 손익을 유지하되 `INVALID_RESOURCE_OVERRUN`, 비음수 현금과 별도 비자금 연구 부채(자산에서 차감, 매도 대금으로 상환, 신규 진입 차단·청산 허용)로 기록된다.
- **어댑터·커버리지**: `adapt_rows`는 `source`·`interval`(1m)·`available_at`·`collected_at`·`collection_run_id`를 보존하고 값을 만들지 않으며 15:30은 `CLOSING_1530`, 그 외 세션 밖은 `OUTSIDE_REPLAY_SESSION`으로 분리·보고한다(전체 replay를 중단시키지 않음). `coverage`는 호출자 행·명시 날짜·고정 종목만 사용하고 `NO_ROWS`/`MISSING`/`COMPLETE`, 결측 분 목록, 지연·백필·0거래량·turnover 준비도, `actual_stored_date_range='UNKNOWN'`을 보고한다.

### 9.2 발견 결함 (3건 모두 수정·재검증 완료)

| ID | 심각도 | 내용 | 상태 |
|---|---|---|---|
| D-R1 | 중간 | `run_input` 최상위 `run_status`가 연구 결과가 `INVALID_RESOURCE_OVERRUN`이어도 `'RAN'` | **수정 확인(`ba765e1`)**: 무효 상태는 부채 상환·청산 완료 후에도 최상위에 유지되고, 정상은 `RAN`, 선택 없음은 `NO_REPLAY_ROWS`, 엄격·legacy는 `RAN`으로 보존된다(테스트 `test_regression_run_input_top_level_status_surfaces_invalid_run`, `test_run_input_status_stays_invalid_after_debt_retirement_and_flat`, `test_run_input_ran_and_no_replay_rows_are_preserved`) |
| D-R2 | 낮음 | `UNRESOLVED_OBSERVED_AFTER_1510`가 무관한 늦은 봉에도 기록 | **수정 확인(`ba765e1`)**: 동일 종목·동일 세션의 후보 봉(신호 이후, 커서 이상, 15:10 이전, BUY는 나이·진입창 한도 내)에만 기록되고 `candidate_event_at`·`pending_candidate_from`·`order_signal_at`이 남는다. 신호 이전 봉·타임아웃 이전 봉·15:10 이후 봉·다른 날 봉은 제외되고 다음 날 관측된 실제 후보는 출처와 함께 기록된다(테스트 `test_regression_unrelated_late_bar_is_not_reported_as_unresolved_outcome`, `test_real_candidate_observed_after_freeze_carries_provenance`, `test_excluded_bars_are_not_attributed`). 다음 날 도착한 봉은 지연 한계(최대 30분)에 들어갈 수 없으므로 3차 수정 이후 특수 사유가 아니라 일반 지연 초과 사유와 미청산 수량·동결 증거만 남는다(테스트 `test_next_day_arrival_cannot_fit_the_lag_limit_so_only_generic_evidence_remains`; 지연 한계는 완화하지 않음) |
| D-R3 | 낮음 | 허용 지연을 초과했거나 거래량 0인 봉(결과가 될 수 없는 봉)이 `UNRESOLVED_OBSERVED_AFTER_1510`로 기록 | **수정 확인(`9908454`)**: 특수 사유는 실제 후보·거래량>0·`available_at-event_at-1분 <= research_max_observation_lag`(경계 포함)일 때만 기록된다. 지연 초과는 일반 `RESEARCH_OBSERVATION_LAG_EXCEEDED`, 거래량 0은 일반 `RESEARCH_ZERO_VOLUME_OUTCOME_VETO`로만 남고 실제 `UNLIQUIDATED`·수량 10·동결 마크·손익은 그대로이며 가짜 체결은 없다(테스트 `test_regression_vetoed_candidate_is_not_reported_as_a_valid_late_outcome`, `test_lag_boundary_is_inclusive_and_one_microsecond_over_is_generic`, `test_zero_volume_candidate_after_freeze_is_generic_veto_and_preserves_evidence`) |

### 9.3 관찰 (결함 아님, 문서화됨)

- 앞선 후보 봉이 결측이면 뒤 후보는 그 봉의 끝+지연 타임아웃까지 정산되지 않는다(계약). 오후에 봉이 연속 결측이면 청산 정산이 약 3분 늦어져 15:10 동결 전에 정산되지 못할 수 있다(테스트로 고정).
- 저장 행에는 turnover가 없으므로 기본 `exact` VWAP은 신호를 만들지 않는다. 연구에서는 `typical_approximation`을 명시해야 한다(커버리지의 `exact_vwap_turnover_ready`로 표시).
- `tests/test_day01_research.py`는 `from test_day01 import ...`를 사용하므로 `python -B -m unittest discover -s tests -p test_day01_research.py`로만 실행된다(`tests.test_day01_research` 모듈 지정은 import 오류). 결함은 아니며 아래 명령에 반영했다.

### 9.4 독립 테스트와 실행 결과

`tests/test_day01_research_review.py`(51개): 기본값·분리 라벨·보고 필드, 정산 시각·반사실 시각·역소급 없는 원장, 대상 거래량의 수량 비사용·0거래량 veto, 나이 배타·유예 만료, 지연 한계 포함/초과, 앞선 후보 대기와 결측 타임아웃, 신선한 결정(도착 시각 보존·지연 한계), 청산 대칭·14:50, 15:10 동결·늦은 정산, 신호 시점 예약 경합, 모델 시가 이후 손실의 BUY 비거절·이전 정보 취소, 정산 시 catch-up(STOP 우선·최신 마크), 같은 시각 매도 우선, 자원 초과·비자금 부채·상환·무효 후 진입 차단과 청산 허용, 어댑터 보존·거부·분리·결정성, 커버리지, `run_input` 연동·상태 전파, 수정 경계(후보 출처 필드·다음 날 관측의 일반 증거·제외 봉·지연 경계 포함/초과·거래량 0·무효 상태 영속), 25개 시드 속성 테스트, 정적 import 순도.

| 구분 | 명령 | 결과 (2026-11-12 최종 실행) |
|---|---|---|
| Codex (엄격·legacy) | `python -B -m unittest discover -s tests -p test_day01.py` | 67개 통과 (직전 단계 실행, 재실행하지 않음) |
| Codex (연구용) | `python -B -m unittest discover -s tests -p test_day01_research.py` | 33개 통과 |
| Claude (엄격·legacy·next-open) | `python -B -m unittest tests.test_day01_review` | 108개 통과 (직전 단계 실행, 이번 3차 수정은 연구용 분기 6줄만 변경하여 재실행하지 않음) |
| Claude (연구용·어댑터·커버리지·수정 경계) | `python -B -m unittest tests.test_day01_research_review` | **51개 실행, 51 통과, 실패·오류·expected failure 0** (약 40초) |
| 기존 MA/PAPER/백테스트 안전 회귀 (DAY01과 별개) | `env -u CLAUDE_CODE_MESSAGING_TOKEN -u ORCA_AGENT_SESSION_SPAWN_TOKEN python -B tests/day01_safe_regression_runner.py tests.test_ml_backtest tests.test_strategy_engine tests.test_paper_management tests.test_paper_feed` | 55개 통과(직전 단계 실행, 재실행하지 않음), 제외 1(`tests.test_strategy_engine.StrategyEngineTests.test_journal_survives_reset_and_contains_costs_reason_and_owner`: `.paper-history` 쓰기) |
| 실행기 자체 점검 | `... --selftest` (`env -u ...` 접두) | 재실행하지 않음(실행기·보호 로직 불변). 직전 결과: 2026-11-12 11/11 PASS. `env -u` 없이 실행하면 하네스가 주입한 Orca 자식 런타임 변수 이름 2개(`CLAUDE_CODE_MESSAGING_TOKEN`, `ORCA_AGENT_SESSION_SPAWN_TOKEN`) 때문에 **거부**된다(값은 읽거나 출력하지 않음, 거부는 통과로 기록하지 않음; 이번 단계에서도 확인) |

합계: DAY01 합성 통과 259(67 + 33 + 108 + 51), 알려진 실패·expected failure 0, 기존 안전 회귀 55 별도. 이번(3차 수정) 단계에서 새로 실행한 것은 연구용 Codex 33개와 Claude 51개이며 나머지는 직전 결과이고, 구현 변경(연구용 분기만)이 엄격·legacy 경로에 영향을 주지 않음을 소스 diff로 확인했다. 건너뜀·제외: 안전 회귀 1개 제외(위). 변이 검증은 이번 단계에서 수행하지 않았다.

### 9.5 미검증·실제 데이터 요건

- 실제 시장 데이터·실제 저장 행(DB/RAW)은 읽거나 실행하지 않았다. 문서상 22종목·날짜 범위는 재확인하지 않았다.
- 엄격 체결 증거(시가 시점 가격·체결가능수량)는 현재 저장소 데이터에 없다. 실제 근거가 필요하면 별도 설계·승인으로 실시간 체결/호가와 수신 시각을 영속화하는 수집기가 필요하다(이번 범위 밖).
- 연구용 모드의 체결은 사후 추정이며 실제 주문·유동성·체결이 아니다. 해당 결과를 엄격/legacy 결과와 비교하거나 수익성으로 해석하지 않는다.
- 기존 DB 필요 테스트, PAPER/LIVE, 사용자 동작 검증, 다음날 갭은 미검증·미모델링이다.
