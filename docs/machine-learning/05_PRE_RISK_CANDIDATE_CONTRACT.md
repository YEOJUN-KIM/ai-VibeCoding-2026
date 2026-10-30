# Pre-Risk Candidate Contract v1

구현일: 2026-10-30. 모델 학습·모델 inference·Dataset 재생성은 이번 단계에 포함하지 않습니다.

## 목적과 정의

First ML Experiment v1은 실제 Dataset의 hash와 MA 계산을 검증했지만
`CANDIDATE_RECONSTRUCTION_NOT_READY`로 학습 전에 중단했습니다. 완료봉 종가와
실제 판단 현재가가 달랐고, 기존 decision의 상태는 체결 등 부수 효과 이후였으며,
분봉 RAW에는 정확한 호출·초기화·exit suppression 이력이 없었습니다.
과거 flat/close replay는 진단용이며 새 authenticated evidence와 혼합하지 않습니다.

이 계약의 정의는 다음과 같습니다.

```text
baseline_buy_candidate = 실제 strategy.generate_signal(...) 결과의 action == BUY
```

기존 전략 자체가 반영하는 보유 수량·arming·confirmation·quote 조건은 포함됩니다.
뒤에 실행되는 cash·sizing·risk·cooldown·일일 한도·주문·체결은 candidate를 바꾸지
않습니다. BUY가 차단되더라도 candidate는 true입니다. HOLD/SELL 결과는 false입니다.

## 실제 호출 순서와 훅 위치

현재 실제 구현은 `auto_trader/trading/engine.py:TradingEngine.step`입니다.

```text
_manage_exits(candles)                         기존 선행 exit 처리
  → symbol별 quote/history 확보
  → new_bar 확인, 필요시 gap >2분 상태 초기화
  → exit suppression / history 부족이면 skip evidence (새 봉에 한함)
  → capture_before: 실제 입력·pre-signal 상태를 분리 복사
  → strategy.generate_signal(SignalContext(...))   기존 호출 그대로
  → capture_after: 결과 추가, immutable copy를 recorder 큐에 전달
  → 기존 snapshot/entry_armed/confirmation 상태 반영
  → _buy / _sell                              기존 risk/order/fill 그대로
  → _record_ml_decision                       기존 execution context
```

기록 훅은 해당 **entry signal의** risk/order보다 앞에 있습니다. `_manage_exits`가
먼저 하는 보호 청산까지 앞당기거나 변경한 것은 아닙니다. 청산 때문에 entry가
억제된 행은 `entry_evaluation_performed=false`, `skip_reason=EXIT_SUPPRESSION`으로
남기며, 존재하지 않는 HOLD/SELL entry signal을 만들어내지 않습니다.
이 행의 position_before는 선행 exit 처리 **후**, entry 평가가 생략된 시점의 상태입니다.
보호 청산의 세부 fill/lot 기록은 기존 decision/execution 기록에 남습니다.

MovingAverageStrategy, entry/exit 조건, RiskManager, sizing, cooldown, 한도,
cash/fill 처리는 변경하지 않았습니다. 관측은 broker의 순수 quantity 조회를
한 번 추가하지만 기존 SignalContext의 lazy getter와 호출은 유지합니다.
관측 예외는 잡아서 명시적 error log를 남기며 기존 signal/order 경로를 계속합니다.
관측에 사용하는 wall clock은 engine의 주입된 매매 clock을 추가 소비하지 않습니다.

## 저장 구조와 필드

기존 `initialize()`가 실행하는 idempotent `auto_trader/schema.sql`에 별도
`ml_strategy_candidates` 테이블을 추가했습니다. 기존 decision 테이블은 유지합니다.
전략/계좌 삭제가 audit evidence를 지우지 않도록 운영 객체에 대한 cascading FK는
추가하지 않았습니다.

| 구분 | 필드와 의미 |
|---|---|
| Identity | `id`: DB identity PK. `candidate_key`: enqueue 전에 발급한 UUID correlation/retry key. `contract_version=v1`. `strategy_id`, `strategy_name`, `strategy_version`: 실제 signal 구현 식별자. `execution_mode`: 실제 실행 환경 PAPER 또는 LIVE이며 저장 전략 설정의 DRY_RUN과 구분합니다. |
| Invocation | `engine_instance_id`: 엔진 객체 수명 동안 유지되는 UUID. `invocation_sequence`: start/configure가 초기화하지 않는 step 순번. `run_id`: 실제 broker run 식별자. |
| Candle/quote | `symbol`, `candle_event_at`: 실제 history anchor. `quote_price`: 실제 SignalContext.price와 같은 값. `quote_source`: TOSS_CURRENT_PRICE 또는 MARKET_SNAPSHOT. `quote_observed_at`: 엔진이 quote를 로컬 관측한 시각. |
| Signal 입력 | `short_period`, `long_period`, `history_count`, `history_first_close`, `history_last_close`, `previous_trend`, `entry_armed_before`, `confirmation_count_before`, `position_quantity_before`. |
| Signal 출력 | `short_average`, `long_average`, `trend`, `signal_action`, `signal_reason`, `signal_decision`, `entry_armed_after_signal`, `confirmation_count_after_signal`, `rising_condition`, `baseline_buy_candidate`. after_signal은 체결 이후 상태가 아닙니다. |
| 평가 여부 | `entry_evaluation_performed`. exit suppression/history 부족은 false이며 action/candidate/after_signal은 NULL입니다. unknown/not-evaluated를 false HOLD로 해석하지 않습니다. |
| metadata | 전체 `history_closes`를 Decimal 문자열로 보존, `new_bar`, 이전 candle 시각, gap reset 여부, `skip_reason`, `exit_signal_present`, 실제 data_source, engine tick_count, signal 설정 snapshot(`short_period`, `long_period`), quote 시간 정책. |

first/last와 MA만으로 전체 history를 복구할 수 없으므로, 실제 입력 close 배열도
작은 JSONB로 저장합니다. 이를 통해 저장한 입력·quote·pre-state로 production
signal 함수를 다시 호출해 결과를 검증할 수 있습니다. execution outcome이나
미래 label은 candidate 행에 넣지 않습니다.

### 시간 의미

모든 시간은 timezone-aware TIMESTAMPTZ입니다.

* `candle_event_at`: 기존 엔진이 처리한 완료 candle의 event timestamp.
* `quote_observed_at`: snapshot 준비 시 엔진 quote를 읽은 로컬 wall-clock 경계.
  공급자의 quote event/HTTP 수신 시각을 알고 있다고 주장하지 않습니다.
* `decision_at`: pre-state를 확보한 뒤 실제 signal 호출을 시작하는 경계 시각.
  평가 생략 행에서는 생략을 관측한 시각입니다.
* `available_at`: signal 결과까지 snapshot이 완성된 시각. risk/order 전에 설정합니다.
  **DB durable commit 시각과 다릅니다.**
* `collected_at`: 비동기 batch writer의 DB 저장 시도 시각입니다. 기존 recorder와
  동일하게 `max(writer_clock, available_at)`로 순서를 보존하며 commit 완료 시각은 아닙니다.

검증/DB CHECK로 `candle_event_at <= decision_at <= available_at <= collected_at`과
`quote_observed_at <= decision_at`을 확인합니다. 시계 역행 등 잘못된 snapshot은
명시적으로 drop하며 trading은 계속합니다. SIMULATED 입력은 candle이 NULL일 수
있으며 data_source로 구분합니다. Dataset helper는 이를 사용할 수 없습니다.

## Append-only와 중복 정책

writer는 `INSERT ... ON CONFLICT(candidate_key) DO NOTHING`만 수행합니다.
candidate를 execution 결과로 UPDATE하지 않습니다. 이 원칙은 application writer
수준이며 DBA 권한으로 직접 수정하는 것까지 막는 DB 불변 trigger는 도입하지 않았습니다.
동일 key 재시도는 기존 행을 바꾸지 않습니다.

현재 전략은 `new_bar=false`이면 상태를 진행시키지 않고 HOLD하므로, 같은 봉의
반복 poll에 대한 추가 candidate 행은 만들지 않습니다. 새 봉에서 BUY/HOLD/SELL을
실제 평가한 행과 새 봉에서 평가를 생략한 행을 기록합니다. 따라서 모든 poll을
보관하는 invocation trace는 아닙니다. candle/source가 없는 초기 대기나 운영 시간
밖에서 step 자체가 실행되지 않은 경우에도 signal evidence는 존재하지 않습니다.

DB에는 `(engine_instance_id, invocation_sequence, symbol)` UNIQUE와 UUID key UNIQUE를
둡니다. `tick_count`는 start/configure에서 0으로 돌아가므로 unique key로 쓰지 않습니다.
재시작/configure로 같은 candle을 다시 평가하는 경우 입력/상태가 다를 수 있어
`(strategy_id,symbol,candle_event_at)`만으로 잘라내지 않습니다.

후속 Dataset에는 전략·실행 모드·엔진/세션·candle 범위를 먼저 정해야 합니다.
복수의 실제 평가가 남는 경우 첫/마지막 BUY를 임의 선택하지 않습니다.
`candidate_for_sample(...)`은 key retry를 중복 제거하고, 모호한 실제 평가가 둘
이상이면 오류를 내며, 없는 행은 None(UNKNOWN)을 반환합니다.

## Decision / execution 연결

* `ml_strategy_candidates`: pre-risk signal truth.
* `ml_strategy_decisions`: 기존 post-risk runtime/execution context.

새 entry 평가의 UUID를 기존 decision의 `metadata.candidate_key`에 추가합니다.
따라서 BUY candidate → BUY_BLOCKED 또는 BUY_FILLED를 join할 수 있습니다.
해당 key는 FK가 아니며 두 큐의 저장 순서는 보장하지 않습니다. 어느 recorder든
실패할 수 있어 link 한쪽만 남을 수 있고 이는 coverage gap으로 취급합니다.
order_id 직접 연결을 위해 주문 코드를 변경하지 않았습니다. entry 평가 전에
발생한 exit decision에는 이 entry candidate key를 억지로 붙이지 않습니다.

## Recorder, 장애 격리와 런타임

`StrategyCandidateRecorder`는 기존 decision recorder의 queue/lock/batch/status/
lifecycle 패턴을 재사용하되 검증·저장 sink와 로그는 분리합니다. 기본 최대
10,000행 queue, 200행 batch, 1초 flush입니다. signal thread에서는 메모리 snapshot과
큐 삽입만 하고 DB I/O는 `asyncio.to_thread` worker에서 수행합니다.
재시도 batch가 queue와 합쳐질 때도 최대 queue 크기를 유지하며 overflow를 명시적으로
경고합니다. validation drop, queue full, write failure는 error/warning 및
`runtime.ml_candidate_recorder.status()`의 dropped/failures/last_error에 나타납니다.

이 방식은 best-effort 관측입니다. DB 장애, queue overflow, 프로세스 강제 종료
시 evidence가 누락될 수 있습니다. durable WAL이나 trading fail-closed 정책을
추가한 것이 아닙니다. 기존 거래 결과는 로깅 성공을 기다리지 않습니다.
앱 lifecycle에서 recorder를 시작하고, 엔진을 먼저 정지한 다음 queue를 drain합니다.
기본 PAPER 엔진과 사용자별 LIVE_COPY/EXPERIMENT PAPER 엔진 모두 연결했습니다.

현재 코드에는 LIVE 자동 signal evaluator가 없습니다. LIVE 경로는 수동 preview/
confirm/order 서비스이며 이를 candidate로 기록하지 않습니다. 테이블/validator와
PIT join은 PAPER/LIVE 실행 모드를 같은 정의로 지원하지만, 현재 producer의 모드는
PAPER입니다. 미래 LIVE evaluator를 구현할 때 동일한 호출 경계에 hook을 연결해야
LIVE authenticated coverage가 생깁니다. 이번 작업은 LIVE 주문 코드를 변경하지 않습니다.

## Coverage 시작 시점과 Dataset v2

```text
candidate_contract_version = v1  (DB 컬럼명: contract_version)
Authenticated candidate coverage begins from:
  이 코드를 배포한 서버의 재시작 및 전략 실행 이후,
  첫 성공적으로 저장된 v1 실제 signal 평가 시각부터.

이 작업에서는 실행 중인 운영 서버를 재시작하거나 전략을 시작하지 않았습니다.
운영 coverage 시작 시각은 아직 확정하지 않았습니다.
```

스키마는 정상 서버 startup의 기존 initialize()를 통해 적용됩니다. isolated 테스트
스키마 이외에 운영 후보 행을 만들어 넣지 않았습니다. 실제 coverage는 배포 후
다음 읽기 전용 조회로 확인할 수 있습니다.

```sql
SELECT execution_mode, contract_version, MIN(decision_at) AS first_signal_at,
       MIN(collected_at) AS first_collected_at, MAX(decision_at) AS last_signal_at,
       COUNT(*) AS evaluations,
       COUNT(*) FILTER (WHERE baseline_buy_candidate) AS buy_candidates
FROM ml_strategy_candidates
WHERE entry_evaluation_performed AND metadata->>'data_source' = 'TOSS'
GROUP BY execution_mode, contract_version;
```

Dataset v1과 과거 진단 CSV는 변경하지 않았습니다. v2에서는 먼저 새 contract의
PIT coverage와 누락률을 감사해야 합니다. `available_at <= prediction_at`인 실제
평가만 결합하고, 모델 feature 자체도 실제 decision 시각에 이용 가능했는지 별도로
검증해야 합니다. 기존 분봉 sample의 prediction_at보다 몇 초 늦은 실제 candidate를
과거 sample에 소급 join해서는 안 됩니다. 필요한 경우 v2 sample clock을 실제
candidate decision/availability에 맞춰 정의하고 executable outcome도 그 시각부터
다시 계산해야 합니다. 과거 label을 그대로 이식하지 않습니다.

다음 단계는 **Authenticated candidate data accumulation → Curated Dataset v2
→ First ML Experiment v2**입니다. PAPER ML filter 연결은 별도 후속 작업입니다.

## 검증

`tests/test_ml_candidate_contract.py`는 실제 MovingAverageStrategy/TradingEngine/
PaperBroker 경로에서 다음을 확인합니다.

* BUY snapshot이 실제 risk 호출 전에 queue에 전달되고, risk reject 후에도 true 유지.
* BUY/HOLD/SELL, arming/confirmation before/after, 보유 수량 suppression, 실제 quote 보존.
* 체결 후 engine arming이 해제돼도 signal 직후 snapshot은 불변.
* 활성 로깅/실패/미연결 간 주문·현금·상태 결과 동일 및 기존 replay 시나리오 비교.
* 같은 봉 polling 제외, configure 이후 같은 봉 재평가 보존, gap reset/exit/history skip.
* timestamp와 PIT join, ambiguity 처리, deep copy, bounded queue, 재시도·장애 명시.
* PostgreSQL isolated schema에서 idempotent 초기화, retry append-only,
  저장 입력으로 실제 production signal 재생 및 timestamp CHECK.

DB integration test는 기존 테스트와 같은 임시 schema를 만들고 종료 시 제거합니다.
운영 candidate 데이터를 삽입하지 않습니다. `tests/test_application_composition.py`는
서버 lifecycle의 새 recorder start/stop과 trading 자동 시작 없음도 확인합니다.
