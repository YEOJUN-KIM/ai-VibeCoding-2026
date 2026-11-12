# DAY-01 독립 검증 문서 (Claude Code)

작업 기록: [20261112-Claude-DAY01-review](../plans/ai-tasks/20261112-Claude-DAY01-review.md)

상태: **최종 — 검토 완료(합성 자동 테스트 기준).** 독립 테스트 `tests/test_day01_review.py` 79개 전체 통과(실패·오류·expected failure·unexpected success 0)와 Codex `tests/test_day01.py` 45개 통과를 확인했다. 최종 결과는 아래 **5절**이며, 실제 시장 데이터·PAPER/LIVE·사용자 동작 검증·운영 적용은 하지 않았고 수익성에 대한 어떤 주장도 하지 않는다.

> **1~4절은 구현 전(사전) 요구사항·시나리오 설계 기록이며 역사적 문서다.** 당시의 가설(예: 5분봉 직접 입력, next-open 체결, 기존 `RawCandle` 재사용 등)은 최종 계약이 아니다. 최종 API·시간 모델·체결 모델은 `docs/specs/DAY01_BACKTEST.md`(1분봉 입력, 완결 5분 집계, 이후 on-time 1분봉 종가 기반 체결)가 기준이며, 1~4절과 충돌하면 **사양 문서와 5절이 우선**한다.

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

명령(실행기 파일은 아래 소스를 임시 경로에 저장해 사용):

```text
python -B <임시경로>/safe_runner.py <worktree> tests.test_ml_backtest tests.test_strategy_engine tests.test_paper_management tests.test_paper_feed
```

2026-11-12 준비 단계 실행 결과(HEAD `6408858f`): 55개 실행, 통과 55, 실패·오류 0. 이 55개는 코디네이터가 언급한 기존 124개와 같은 집합임이 확인되지 않았다.

```python
"""Disposable regression harness: unittest modules with network/DB/.env access blocked.

usage: python -I safe_runner.py <repo_root> <module> [<module> ...]
Blocks (raises AssertionError): socket connects/resolution, psycopg.connect, reading any
'.env*' file, writing under '.paper-history'. Strips credential-like env vars. The engine code
under test is NOT stubbed; only egress/secret paths are closed.
"""
import builtins, os, pathlib, socket, sys, unittest

root = pathlib.Path(sys.argv[1]).resolve()
mods = sys.argv[2:]
sys.path.insert(0, str(root))
os.chdir(root)
if any(p.name == '.env' for p in root.iterdir()):
    sys.exit('refusing: .env present in worktree')
for k in list(os.environ):
    if k.startswith(('POSTGRES_', 'TOSS_', 'DART_', 'OPENAI_', 'ANTHROPIC_', 'AWS_')):
        del os.environ[k]
os.environ.update(APP_MODE='PAPER', POSTGRES_HOST='127.0.0.1', POSTGRES_PORT='1')

def blocked(*a, **k):
    raise AssertionError('network/DB access blocked by safe_runner')

_connect = socket.socket.connect
def loopback_only(self, address, *a, **k):
    # asyncio's self-pipe uses an ephemeral loopback port (>=49152); DB/other ports stay blocked.
    if isinstance(address, tuple) and address[0] in ('127.0.0.1', '::1') and address[1] >= 49152:
        return _connect(self, address, *a, **k)
    return blocked()
socket.socket.connect = loopback_only
socket.socket.connect_ex = blocked
socket.create_connection = blocked
socket.getaddrinfo = blocked
import psycopg  # import only; connections blocked below
psycopg.connect = blocked
psycopg.Connection.connect = classmethod(lambda cls, *a, **k: blocked())

_open = builtins.open
def guarded_open(file, mode='r', *a, **k):
    name = pathlib.Path(str(file)).name if not isinstance(file, int) else ''
    if name.startswith('.env') and name != '.env.example':
        raise AssertionError('.env read blocked')
    if '.paper-history' in str(file).replace('\\', '/'):
        raise AssertionError('.paper-history access blocked')
    return _open(file, mode, *a, **k)
builtins.open = guarded_open
import io
io.open = guarded_open

suite = unittest.TestSuite()
loader = unittest.defaultTestLoader
def flatten(s):
    for t in s:
        if isinstance(t, unittest.TestSuite):
            yield from flatten(t)
        else:
            yield t
skipped = []
for m in mods:
    for t in flatten(loader.loadTestsFromName(m)):
        if 'journal_survives_reset' in t.id():
            skipped.append(t.id()); continue
        suite.addTest(t)
print('excluded (writes .paper-history):', skipped)
r = unittest.TextTestRunner(verbosity=1).run(suite)
print('RAN', r.testsRun, 'FAIL', len(r.failures), 'ERR', len(r.errors), 'SKIP', len(r.skipped))
sys.exit(not r.wasSuccessful())
```
