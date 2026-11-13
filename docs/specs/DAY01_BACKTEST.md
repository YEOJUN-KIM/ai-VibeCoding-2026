# DAY-01 합성 백테스트 사양

구현 모듈: `auto_trader/ml/day01.py`. 기본 실행 모드는 별도 시가 관측 증거를 사용하는 `next_open`이다. 이전 종가 합성 모드는 명시적 `legacy_close`로 유지한다. 별도 `research_estimated_open`은 과거 실제 체결이 아닌 **관측 시점 정산의 불확실한 연구 결과**다. 구현 및 합성 자동 테스트 검증 단계이며 사용자 동작 검증·운영 적용은 하지 않았다. 수익성 검증이나 실거래 가능한 전략이라는 뜻이 아니다. DB, API, PAPER 엔진, 주문 전송, 파일 입출력, 환경 설정 로딩은 연결하지 않는다.

## API와 재현

Python 표준 라이브러리와 순수 `auto_trader.risk_policy.RiskPolicy`만 사용한다. 신규 설치가 필요하지 않다.

```python
from datetime import datetime, timedelta
from decimal import Decimal as D
from auto_trader.ml.day01 import Candle, Config, KST, OpenQuote, replay

start = datetime(2026, 11, 12, 9, tzinfo=KST)
rows = []
for i in list(range(21)) + [350]:
    price = D('100') if i < 15 else D('102')
    volume = D('200') if 15 <= i < 20 else D('100')
    event = start + timedelta(minutes=i)
    rows.append(Candle('SYNTHETIC', event, event + timedelta(minutes=1),
                       price, price, price, price, volume, price * volume))
opens = [OpenQuote('SYNTHETIC', start + timedelta(minutes=i),
                   start + timedelta(minutes=i), D('102'), D('100')) for i in (21, 351)]
result = replay(rows, Config(), universe=['SYNTHETIC'], open_quotes=opens)
assert [t['side'] for t in result['trades']] == ['BUY', 'SELL']
assert result['summary']['status'] == 'FLAT'
```

공개 진입점:

| 함수/타입 | 계약 |
|---|---|
| `Candle(symbol,event_at,available_at,open_price,high_price,low_price,close_price,volume,turnover=None)` | frozen dataclass. 금액·거래량은 유한 Decimal. 시간은 timezone-aware, event_at은 1분 시작 시각. turnover는 해당 1분의 실제 거래대금 |
| `OpenQuote(symbol,event_at,available_at,price,executable_volume)` | frozen dataclass. 시가 순간 수신한 가격·실행 가능 용량의 합성 증거. 완성 분봉에서 추출해 소급 생성하면 안 됨 |
| `Config()` | frozen dataclass. 설정 목록과 기본값은 아래 표 참조. `validate()`로 검증 |
| `select_universe(symbols)` | 사전 고정 문자열 목록을 정렬·중복 제거한 tuple. 가격·미래 성과로 종목 선정하지 않음 |
| `aggregate_five_minutes(rows, as_of)` | `available_at <= as_of`만 포함. 정각 5분 경계에 맞는 5개 분봉 모두 존재할 때만 `FiveMinuteBar` 목록 반환 |
| `evaluate_entry(history, at, config)` | 단일 종목·당일 이력으로 순수 `Decision` 반환. 외부 상태/주문 변경 없음 |
| `execution_costs(reference, quantity, side, config)` | 내부 검증을 통과한 양수 기준가·정수 수량의 BUY/SELL 비용 계산. 주문 제출 기능 없음 |
| `replay(rows, config=Config(), *, universe, open_quotes=())` | 순수 결정적 dict 반환. 입력 순서 독립, 입력 객체 변경 없음. universe 밖 봉/시가 관측은 검증/시계에도 영향 없음 |

`Decision`: `enter, reason, at, range_high, vwap, previous_vwap`.
`FiveMinuteBar`: `symbol, event_at, end_at, available_at, open_price, high_price, low_price, close_price, volume, turnover`.
반환 값은 Decimal·datetime·date·Config를 유지한다. JSON 저장 기능은 없으며 필요한 별도 소비자가 명시적으로 직렬화해야 한다.

## 설정

| 필드 | 기본값 | 의미 |
|---|---:|---|
| execution | next_open | `next_open`, `legacy_close`, `research_estimated_open` |
| initial_cash | 1000000 | 초기 KRW 현금 |
| order_quantity | 10 | 매수 주문 수량. `sizing='FIXED'`(기본)이면 신호 시 고정 수량, `AFFORDABLE_UP_TO_CAP`이면 **상한**(최대 1000) |
| sizing | FIXED | `FIXED`: 기존 동작(기본, 기존 결과 재현). `AFFORDABLE_UP_TO_CAP`: 위험 한도 안에서 가능한 최대 정수 수량(아래 "동적 수량") |
| volume_multiplier | 1.2 | 현재 완성 5분 거래량 / 직전 완성 5분 거래량 하한 |
| stop_rate / target_rate | 0.01 / 0.02 | 체결 매수가 기준 손절·목표 비율 |
| min_reward_risk | 2 | target_rate / stop_rate 하한; 수수료 차감 전 설정 비율 |
| vwap_mode | exact | `exact` 또는 `typical_approximation` |
| fee_rate / sell_tax_rate / slippage_rate | 0.00015 / 0.002 / 0.0005 | 매수·매도 수수료, 매도세, 불리한 슬리피지 |
| max_order_amount | 100000 | 매수 수수료 포함 주문금액 한도 |
| max_symbol_amount / max_total_investment | 200000 / 500000 | 종목·전체 평가 노출 한도 |
| max_positions | 3 | 동시 보유 종목 수 |
| min_cash_ratio | 10 | 비용 포함 순청산자산 대비 잔여현금 %, RiskPolicy와 동일 단위 |
| daily_loss_limit | 20000 | 당일 시작 순청산자산 대비 손실 KRW; 손실이 한도와 같아도 진입 차단 |
| daily_order_limit | 10 | 당일 승인된 신규 ENTRY 주문 수 한도; 아래 청산 정책 참조 |
| volume_participation | 1 | 후속 실행 분봉 거래량 중 사용할 최대 비율. 정수 내림 수량 |
| max_signal_to_fill_minutes | 2 | strict/legacy 관측 체결 나이, research 추정 open 나이. 유한 양수 Decimal, 최대130분, 경계 만료. research 관측 grace는 별도 |
| research_max_observation_lag_minutes | 2 | 연구 모드에서 분 끝 이후 관측 허용 지연. 유한 Decimal 0~30분, 원래 available_at 보존 |

정수 설정은 bool·0·음수를 거절한다. Decimal 설정은 음수·NaN·Infinity·잘못된 타입을 거절한다. stop/slippage <1, participation <=1, cash ratio <=100, fee+tax <=1, RR 하한을 강제한다. 주문 제한 해제 스위치는 없다.

strict/legacy ENTRY 수명은 `execution_at - signal_at < max_signal_to_fill_minutes`다. 09:20 신호의 기본 만료는 09:22이며 09:21 체결은 가능하고 09:22 체결은 불가능하다. 데이터가 없어도 명시적 만료 타이머로 `ENTRY_SIGNAL_TOO_OLD`를 남기고 주문을 제거한다. 11:30 진입창 종료가 더 빠르면 `ENTRY_EXPIRED`다. 승인 주문수는 만료되어도 차감하지 않는다. 설정값은 timedelta의 마이크로초 정밀도로 해석하며 너무 짧아 실행 봉을 기다릴 수 없는 설정은 체결되지 않는다. 연구 모드는 아래의 추정 open 나이와 관측 grace 계약을 따로 적용한다.

## 시각과 전략 판단

- KST 고정 UTC+9. 다른 aware 시간대 입력은 KST로 정규화한다. 거래소 휴장일 달력은 포함하지 않으며 입력 세션 날짜가 합성 거래일이다.
- 1분 봉은 `[event_at, event_at+1m)` 구간이다. `available_at >= event_at+1m`가 필요하다. 일반 세션 `[09:00,15:30)`만 입력 허용한다. 중복 symbol/event는 동일 데이터라도 거절한다. 데이터 출처 선택은 호출자 책임이다.
- 개장 범위는 `[09:00,09:15)` 15개 분봉의 최고가이다. 09:00을 포함해 전부 있어야 한다. 09:15 분봉은 범위 계산에서 제외한다.
- 5분 봉은 `[09:15,09:20)`처럼 정각에 정렬한다. availability는 구성 봉의 최대 availability다. 5개 전부 있어야 하며 부분 봉을 보간하지 않는다.
- 진입은 `[09:20,11:30)`의 신호 및 체결에만 허용한다. 첫 신호는 09:20, 체결은 빨라도 09:21이다. 11:30 체결은 거절한다.
- 새 5분 봉의 `end_at == available_at == 판단 at`일 때만 판단한다. 당일 09:00부터 현재 끝까지 분봉 전부가 알려져 있어야 한다. 늦게 도착한 과거 봉은 도착 이후의 이력을 채울 수 있으나 과거 신호·체결을 재생성하지 않는다.
- 현재 5분 종가 > 개장 범위 최고가, 현재 5분 거래량 >= 직전 5분 거래량 × multiplier, 현재 종가 > 현재 누적 VWAP > 직전 5분 끝의 누적 VWAP가 동시에 필요하다. 거래량 양쪽 모두 양수여야 한다.
- exact VWAP는 분봉별 실제 turnover 합 / volume 합이다. 하나라도 turnover가 없으면 `MISSING_EXACT_TURNOVER`로 거절한다. approximation은 명시적으로 `(high+low+close)/3 * volume`을 누적하며 결과에도 모드를 표시한다. 두 방식을 조용히 섞지 않는다.
- 종목당 하루 승인 ENTRY 1회. universe 선택과 판단 함수는 분리되어 있다. 여러 종목은 동일 시각 SELL을 먼저, 이후 종목 코드 순서로 처리한다.

## 실행·청산 모델

### 연구 모드와 입력 준비

`auto_trader/ml/day01_research.py`의 순수 `adapt_rows`, `coverage`, `run_input`은 caller 매핑·고정 universe·명시적 expected_session_dates만 사용한다. 실제 저장 범위는 UNKNOWN이다. 391봉 계약의15:30은 replay390분과 분리하여 원본·사유·provenance를 반환한다. 수신시각·거래대금을 만들거나 정확 VWAP를 조용히 근사하지 않는다. 현재 데이터 증거·한계·전체 필드와 시간/예약 정책은 [DAY01 데이터 준비](../technical/DAY01_DATA_READINESS.md)에 정의한다.

`execution='research_estimated_open'`은 OpenQuote를 받거나 생성하지 않는다. 신호보다 이후 분봉의 open을 **관측한 가격 결과**로 사용하고 volume은0 거래량의 hindsight veto에만 사용한다. 수량은 signal order_quantity 가정으로 고정하며 participation×대상 volume을 실행 가능 수량으로 주장하지 않는다. mode/uncertain/physical_fill_claim=False, price_source=ESTIMATED_CANDLE_OPEN, hindsight_fields를 명시한다.

이 모드의 `estimated_execution_at`은 반사실적 시가 시각이며 `execution_at/settlement_at`은 실제 관측 정산 시각이다. observation_at은 원래 available_at이다. 이전 후보가 미해결이면 뒤 후보를 먼저 선택하지 않아 settlement가 observation보다 늦을 수도 있다. 과거 곡선을 고치거나 해당 시가 시점에 실제 PnL가 생긴 것으로 기록하지 않는다. 허용 지연 안에서 새로 알려진5분 입력만 실제 시각에 결정하고 historical backfill은 과거 신호를 만들지 않는다.

run_input의 최상위 run_status도 INVALID_RESOURCE_OVERRUN 또는 기타 미결 연구 상태를 전달한다. 정상 경로 RAN 및 무입력 NO_REPLAY_ROWS는 유지한다. 마감 후 특별 관측 실패는 동결 주문의 남은 후보 범위·원래 세션/신호/진입 조건에 속하면서 **관측 지연이 설정 이내이고 거래량>0**인 봉에만 연결하며 주문 신호·후보 event·동결 cursor를 근거로 남긴다. 지연초과/0거래량은 각각 일반 사유로 보고하고 특별 사유를 붙이지 않는다. 현재 지연 상한30분으로 실제 익일 수신은 부적격이다. 특별 사유가 없어도 기존 미청산 잔량·동결 평가·손익은 계속 보존한다.

BUY 신호의 현금·노출·slot을 만료+관측 grace 내내 예약한다. 대상 open 이후에 알려진 급락/종가/손실로 손실 거래를 선택적으로 거절하지 않는다. 엄격히 open 이전의 알려진 위험 block만 증거 시각과 함께 반영한다. 가격 갭으로 신호 시 자원이 부족하면 반사실적 거래와 손실을 기록한 채 run_status=INVALID_RESOURCE_OVERRUN으로 무효 표시하고, 음수 현금 대신 **실제 차입이 아닌** non_funded_liability를 equity에서 차감한다. 위험 한도 준수나 실제 실행 가능성을 주장하지 않으며 추가 진입을 차단한다.

정산 뒤 이미 수신된 진입 분봉/이후 범위를 모두 따라잡고 지금 STOP/target을 결정한다. dual-hit은STOP 우선이고 후속 추정 open 결과를 기다리며 소급 청산하지 않는다. 15:10 관측 마감이 우선이므로15:09 추정 open이15:10에 가용하면 정산하지 않고 UNRESOLVED_OBSERVED_AFTER_1510와 잔량/평가/익일동결을 남긴다. 비용 수식은 다른 모드와 같으며 실제 데이터 백테스트는 아직 수행하지 않았다.

### 기본 next_open: 시가 관측이 실제로 가용한 순간만 실행

완성 `Candle`만으로는 1분 전 시가에서의 주문 접수 가능성이나 당시 용량을 알 수 없다. 따라서 별도 `OpenQuote`가 없으면 기본 모드는 체결하지 않는다. `execution_evidence.missing_open_quotes=True`, source/capacity source/counters 및 대기 신호 만료의 `NO_EXECUTABLE_OPEN_QUOTE_BEFORE_EXPIRY`로 입력 한계를 보고한다. 실제 시장 시가 증거의 수집·연결 어댑터는 없으며 **현재 기본 모드는 합성 시가 관측 스트림이 있어야 재현 가능하고 실제시장 데이터에 바로 연결 가능한 상태가 아니다.**

`OpenQuote`는 정규 세션의 분 시작 `event_at`과 그 가격·가용 실행 용량을 처음 안 `available_at`을 갖는다. 금액은 유한 양수 Decimal, executable_volume은 유한 0 이상 Decimal, 시각은 aware이며 event는 분 정렬/KST `[09:00,15:30)`이어야 한다. `available_at < event_at`은 거절하고, 중복 symbol/event는 동일값/충돌값 모두 거절한다. universe 밖 관측은 검증 전에 제외한다. `available_at > event_at`인 지연 시가는 도착 시 이력·위험에만 반영하고 `LATE_OPEN_QUOTE_NOT_EXECUTABLE`로 기록한다. 과거 시가에 소급 체결하지 않는다. legacy 모드에 선택된 시가 관측을 주면 혼합 모델 오류로 거절한다.

기본 모드의 실제 체결은 `quote.available_at == quote.event_at`이고 `signal_at < quote.event_at`일 때다. 09:19–09:20 분봉을 09:20에 수신해 생성한 신호는 이미 그 순간의 09:20 시가를 놓쳤으며 **첫 가능한 후속 분 시가는 09:21**이다. 물리적 `execution_at`, `observation_at` 및 호환 필드 `observed_at`은 이때 모두 09:21이다. `signal_at/decision_at`은 09:20으로 별도 기록한다. 지연 없는 시가 증거라는 엄격한 가정이며 실제 통신 지연·호가 우선순위·체결 보장을 모델링하지 않는다.

동일 시각 T의 처리 순서는 (1) ENTRY 만료·14:50 청산 신호·15:10 동결 타이머, (2) T에 도착한 시가 관측 전체 평가 및 이미 대기하던 주문의 체결, (3) 새 시가 갭에 따른 후속 청산 신호, (4) T에 도착한 완성 분봉의 지표·위험·신규 결정이다. 새 갭이나 완성 봉에서 T에 발견한 위험 신호는 T의 시가에 체결할 수 없고 엄격히 이후 시가를 기다린다. T에 도착한 완성 봉 저가·거래량·종가·위험 판단은 (2)의 이미 처리된 체결을 거절하거나 다시 계산하는 근거로 사용하지 않는다. 분봉 체결 후 그 진입 분봉이 완성되어 도착하면 `event_at == entry_at` 구간 전체의 손절/목표를 감시한다.

용량은 **그 시가 순간에 알려진** `OpenQuote.executable_volume × volume_participation`의 정수 내림이다. 미래의 완성 1분 거래량은 사용하지 않는다. 실제 호가 기반 체결 보장이 아닌 합성 snapshot 용량 가정이다. 0/1주 미만이면 체결하지 않고 대기한다. 매수는 가능한 부분 수량 체결 후 나머지를 취소하고 매도는 나머지 잔량이 후속 시가를 기다린다. 같은 시각 모든 시가를 먼저 평가한 후 SELL 우선·종목 정렬로 실행한다. 실제 매수 위험 검사는 이전에 알려진 데이터와 이 시가 관측들 및 앞선 체결 후 현금만 사용한다.

체결 기준가는 관측 시가 `quote.price`이며 수수료·매도세·불리한 슬리피지는 기존 수식 그대로 적용한다. 신호 후 갭 하락은 실제 관측한 낮은 시가로 체결하며 손절가로 끌어올리지 않는다. 이전에 생성된 TARGET 시장청산 주문이 갭 상승 시가에서 실행되면 관측한 시가를 사용한다. 아직 주문이 없는 시가 갭이 손절/목표를 새로 알리면 그 순간에는 체결하지 않는다. dual-hit 완성 봉은 STOP 우선으로 후속 시가 청산을 요청한다. 후속 종가·저가 또는 synthetic 손절 penalty로 시가를 바꾸지 않는다. `price_source='OPEN_QUOTE'`, `evidence_close=None`을 명시한다.

시가와 같은 event의 완성 봉이 나중에 도착하면 그 completed close는 같은 event의 open mark를 대체한다. 더 최신 event의 시가 mark가 있으면 이전 분봉으로 덮어쓰지 않는다. 나중에 Candle.open_price와 OpenQuote.price 불일치를 발견하면 발견 시각의 `OPEN_CANDLE_PRICE_MISMATCH`를 남기고 과거 체결을 수정하지 않는다. 기본 모드 valuation_marks에는 `price_source`도 기록한다.

14:50 타이머의 신규 청산 신호는 strict signal<open 때문에 14:50 시가에서 체결하지 않고 14:51 이후를 기다린다. 15:09의 실제 시가 증거는 가능하지만 15:10 시가는 동결 타이머가 우선하므로 불가능하다. 무데이터·지연·0용량에서는 잔량을 숨기지 않고 15:10 미청산 상태와 비용 차감 평가/PnL를 보존한다. ENTRY 기본2분 exclusive 만료는 시가의 실제 관측 체결 시각에 적용한다.

### 명시적 legacy_close: 이전 합성 회귀용

`Config(execution='legacy_close')`는 이전 결과 재현 전용이다. 신호 이후 분봉 시작이 `event_at >= signal_at`이며 `available_at == event_at+1m`인 새 양수 거래량 봉의 종가를 후속 실행 증거로 사용한다. `execution_at = observed_at = available_at`이며 과거 open 시각으로 체결시각을 되돌리지 않는다. 지연 봉의 종가를 실제 현재 체결가로 간주하지 않는다. 다음 단락의 종가/trigger 가격 proxy와 완성 봉 거래량 용량 가정은 이 모드에만 해당한다.

완성 봉의 마지막 가격은 종가 주문 체결 가능성을 보장하지 않는다. synthetic last-price proxy이며 호가·틱·지연 주문 접수·거래 우선순위를 재현하지 않는다. 참여율에 따른 수량 상한은 단순 분봉 거래량 모형으로 실제 유동성 보장이 아니다.

**두 모드 모두 평가·위험 관측과 실행 증거는 별도다.** 양수 거래량의 당일 지연 봉도 실제 `available_at`에 이력·평가·손절/목표 감시에 반영한다. 평가가격에는 마지막 수신 순서 대신 가장 최신 `event_at`을 사용하고 `valuation_marks`에 사건 시각과 수신 시각을 따로 남긴다. 09:22 급락 봉이 10:00에 왔고 더 최신 봉이 없다면 10:00부터 급락 종가로 평가한다. 이미 09:30의 더 최신 봉을 알면 평가가격은 유지하며 `OLDER_OBSERVATION_MARK_PRESERVED`를 기록한다. 과거 equity curve를 재작성하지 않는다. 기본 모드에서는 같은 T의 open 체결이 완성 봉 수신보다 먼저이며, legacy에서는 기존처럼 완성 봉의 위험 정보를 먼저 반영한다.

보유 시작 이후 구간의 지연 봉에서 손절/목표를 발견하면 도착 시점의 SELL 신호와 `LATE_RISK_OBSERVATION`을 기록한다. 이미 더 최신 가격으로 반등을 알더라도 과거 위험 누락을 조용히 무시하지 않는 보수적 정책이다. 해당 잔량이 모두 청산될 때까지 계좌 신규 매수를 `LATE_RISK_EXIT_PENDING`으로 차단한다. 손실 한도를 이미 넘었으면 `DAILY_LOSS_LIMIT` 사유가 우선한다. 손절이 기존 TARGET/세션 청산보다 불리한 정보를 제공하면 도착 시각으로 SELL을 교체하고 `EXIT_REPLACED_BY_STOP`을 기록하며 총 주문수에 포함한다. 지연 데이터 자체나 같은 도착 batch의 과거 분봉으로 체결하지 않고 이후 온타임 실행 증거를 기다린다. 보유 이전 구간의 wick은 보유 포지션 손절로 해석하지 않는다. 0거래량은 평가·위험 증거에서 제외하고 15:10 확정 동결 이후에는 재평가·추가 거래하지 않는다.

매수 체결 봉 자체의 고저가로 손절·목표를 소급 판단하지 않는다. 보유 시점 이후의 후속 온타임 봉에서 low <= stop, high >= target을 관측하면 새로운 SELL 신호를 만들고 그 다음 실행 가능한 분봉을 기다린다. 한 봉에서 양쪽이 닿으면 `AMBIGUOUS_STOP_FIRST`를 선택한다.

손절 관측 이후 반등으로 손익이 유리해지지 않도록 매도 기준가는 `min(후속 close, 후속 open, 관측 stop 또는 트리거 봉의 더 낮은 open)`이다. 목표는 `min(후속 close, target)`으로 상한을 둔다. 이 불리한 trigger proxy는 후속 봉 범위 밖의 더 낮은 가격일 수도 있다. 해당 가격에서 실제 거래가 있었다고 주장하지 않으며 `evidence_close`, `reference_price`, `price`를 각각 기록한다. 갭과 슬리피지로 손실 한도를 초과할 수 있으며 현금흐름을 한도로 잘라서 숨기지 않는다. 일별 손실 초과는 equity curve에 기록한다.

수수료·세금은 기존 `paper.py`의 계산과 동일하게 불리한 슬리피지 적용 거래대금 × 요율을 매 체결마다 KRW 정수 `ROUND_DOWN`한다. 매수세는 0이다. 매수 원가에는 수수료를 포함하고 매도 실현이익은 매도 순대금 - 비례 배분한 매수 원가다. 기존 broker를 import하지 않고 수식을 재현한다.

legacy의 14:50 타이머도 데이터 유무와 관계없이 SELL 신호를 만든다. 14:50에 시작한 후속 봉의 14:51 종가 관측이 첫 체결 가능 시점이다. 앞선 14:49 봉, 0거래량, 누락 봉, 지연 봉으로 가짜 청산하지 않는다. 정수 참여수량만큼 부분 매도하고 나머지는 같은 주문으로 다음 분봉을 기다린다. 매수 부분 체결 후 나머지는 취소한다. 15:10은 보수적인 exclusive 실행 마감으로 15:10 관측조차 체결하지 않고 남은 수량을 `UNLIQUIDATED_AT_1510`으로 확정한다.

미청산 수량·매수원가·마지막 인과적 평가가격을 보존한다. 다음날 봉으로 강제 청산하거나 가격을 조용히 바꾸지 않는다. `UNLIQUIDATED` 상태에서는 전 계좌 신규 노출을 막는다. 평가금액은 동결된 마지막 가격의 가상 비용 차감 순청산가이며 실현이익과 구분한다. 실제 다음날 갭 손실/수익은 미검증·미모델링이다. 다음날 정상 재개는 미청산 잔량이 없는 경우에만 가능하며 범위·VWAP·한도는 KST 날짜별 초기화한다.

summary의 `overnight_gap_exposure_unmodeled=True`와 `frozen_valuation_symbols` 목록은 미청산 종목의 익일 갭 노출을 모델링하지 않고 있음을 명시한다. 다음날 `net_change=0`은 동결 평가 결과이며 실제 손실·수익이 0이라는 뜻이 아니다. 미청산 상태의 진입 거절은 동일 종목 재진입 검사보다 우선하여 `UNRESOLVED_EXPOSURE`로 보고한다.

## 위험 정책과 기록

현금·주문금액·종목금액·전체 노출·보유수·잔여현금비율·일 손실을 신호와 실제 매수 체결 모두에서 검사한다. 신호 시 수량은 고정하지만 부분 체결시 사용 가능한 수량에 대해 다시 검사한다. 모든 동시 관측 가격을 먼저 반영해 종목 순서로 오래된 평가가격을 사용하는 문제를 피한다. 신호 때 미래 현금을 예약하지는 않으며 후속 체결에서 재검사하여 초과 주문을 거절한다.

`daily_order_limit`은 ENTRY 승인 수 한도이다. 거절된 신호는 카운트하지 않고, 승인 후 미체결/체결거절된 ENTRY는 카운트한다. 위험 감소 SELL은 신규 노출 한도와 별도 정책으로 항상 허용하며 총 주문수에는 포함한다. 부분 체결은 같은 주문으로 주문수를 추가하지 않는다. `filled_orders`는 실제로는 체결 레코드 수라 부분 체결도 각각 센다. 위험 한도를 우회하는 설정이나 외부 주문 경로는 없다.

반환 최상위 키:

| 키 | 내용 |
|---|---|
| config, universe, vwap_mode, execution_model | 설정과 가정 |
| execution_evidence | source, supplied_open_quotes, on_time_open_quotes, late_open_quotes, zero_or_subunit_capacity_quotes, missing_open_quotes, capacity_source. 전체 입력 증거 coverage이며 미래 시가를 과거 결정에 사용하는 필드가 아님 |
| summary | cash, net_liquidation_equity, net_profit, realized_profit, fees, taxes, slippage, filled_orders, open_quote_fills, legacy_close_fills, open_positions, unresolved_quantities, overnight_gap_exposure_unmodeled, frozen_valuation_symbols, entry_orders, total_orders, status |
| positions | symbol별 quantity, entry_price, cost_basis, day, entry_at, stop, target, unresolved |
| valuation_marks | 보유 symbol별 price, event_at(최신 평가 사건), observed_at(해당 가격 실제 도착), frozen. next_open은 price_source 추가 |
| signals | symbol, side, at, reason, quantity, trigger. 제출 시각과 당시 수량의 복사본 |
| trades | 기존 시각/가격/비용 필드 + mode, uncertain, physical_fill_claim, uncertainty_flags, unfilled_quantity, fee_rate/sell_tax_rate/slippage_rate. 연구 모드는 estimated_execution_at, settlement_at/lag, hindsight_fields, resource_breaches 추가 |
| reasons | at, symbol, reason 및 해당 항목의 phase·quantity·VWAP 등 상세 |
| failures | 15:10 at, symbol, `UNLIQUIDATED_AT_1510`, quantity |
| equity_curve | at, cash, net_liquidation_equity, daily_net_change, loss_limit_exceeded, unresolved |
| daily_results | day, closing_equity, net_change, entry_orders, total_orders, unresolved_quantities |
| research_assumptions | active/uncertain/run_status, physical_fill_claim, non_funded_liability, resource_breaches, risk_limit_adherence, capacity, costs. 연구 결과의 한도 준수·유동성 주장이 아님 |

summary에는 estimated_open_fills, unfilled_entry_quantity, unfilled_exit_quantity도 있다. 연구 equity_curve에는 reserved_cash/reserved_position_slots/research_liability/research_run_invalid가 추가된다. 전체 결과의 mode/uncertain과 각 거래의 비용·불확실성을 함께 해석한다. NO_ROWS/MISSING 커버리지는 제공 행에 대한 진단이고 실제 저장 범위 UNKNOWN과 구분한다.

주요 사유는 코드 문자열로 고정한다: `INCOMPLETE_OPENING_RANGE`, `NO_FRESH_FIVE_MINUTE_BAR`, `INCOMPLETE_SESSION_VWAP`, `MISSING_EXACT_TURNOVER`, `NO_RANGE_BREAKOUT`, `INSUFFICIENT_VOLUME_INCREASE`, `NO_RISING_VWAP`, `LATE_OBSERVATION_NOT_EXECUTABLE`, `ENTRY_EXPIRED`, `NO_EXECUTABLE_CAPACITY`, `ENTRY_REMAINDER_CANCELLED`, `UNFILLED_ORDER_AT_1510`, `UNRESOLVED_EXPOSURE`, `DAILY_LOSS_LIMIT`, `POSITION_LIMIT`, `ORDER_AMOUNT_LIMIT`, `SYMBOL_EXPOSURE_LIMIT`, `TOTAL_EXPOSURE_LIMIT`, `INSUFFICIENT_CASH`, `MIN_CASH_RATIO`, `DAILY_ENTRY_ORDER_LIMIT` 등이다.

리뷰 수정으로 `ENTRY_SIGNAL_TOO_OLD`, `LATE_RISK_OBSERVATION`, `LATE_RISK_EXIT_PENDING`, `OLDER_OBSERVATION_MARK_PRESERVED`, `EXIT_REPLACED_BY_STOP`을 추가했다. 함수 시그니처와 기존 API/DB 계약은 유지하며 Config와 결과 dict에 위 필드를 추가했다.

## 기존 코드와 검증 경계

기존 `backtest.py`는 `RawCandle`, PAPER broker, MA engine을 사용하며 MA 설정·주문 엔진과 결합되어 있다. `data_pipeline.RawCandle`의 completed minute/availability 계약을 참조했지만 실제 거래대금이 없어 신규 Candle에 optional turnover를 둔다. 기존 API/DB 스키마 변경은 없다.

`backtest`, `data_pipeline`, `paper`, `risk`는 database/settings로 이어져 settings import가 `.env`를 읽을 수 있다. 기본 실행 경로는 이를 import하지 않는다. 이전 단계의 guarded backtest12개에 이어 이번에는 검토된 기존 `tests/day01_safe_regression_runner.py`로 허용된55개를 실행했다. guard는 민감 환경·설정 파일 존재 시 거부하고 설정/운영기록 접근·DB/socket 연결을 차단하며 엔진 계산을 스텁하지 않는다. 테스트 전용 자식 환경의 Orca runtime token 키 제거와 최초 guard 거부/재실행 결과는 새 기록에 명시했다. 데이터 파이프라인 테스트는 setUp에서 DB 스키마를 생성하므로 여전히 실행하지 않는다. 기존 파일은 모두 읽기 전용으로 유지했다. 순수 RiskPolicy만 재사용해 금융 한도 계산의 단위를 유지했다.

검증 명령: `python -B -m unittest discover -s tests -p test_day01_research.py -v`로 D-R3 관련 준비/연구33개를 이번에 재실행해 통과했다(기존31+신규2). 앞선 D-R1/D-R2 검증은 기존67+108+연구31+guarded55=261개였으며 이번 현재 결과에 재집계하지 않는다. 기본/legacy/기존 금융 엔진·실행기는 변경하지 않아 불필요한 반복 실행을 하지 않았다. Claude 최신 독립49개 수정본 재검증과 지연 상한에 어긋난 익일 기대값 수정은 별도다. Claude 소유 파일은 편집하지 않았다. 정확한 범위와 남은 검증은 작업 기록에 명시한다.

장점은 환경 접근 없는 결정성, 인과적 시각, 비용·위험의 명시성이다. 단점은 엄격한 온타임 조건, 보수적인 청산 proxy, 단순 유동성 모형, 미청산 평가가격 동결이다. 학습 개념은 정보 이용 가능 시각과 사건 시각의 분리, 전략 판단과 실행 증거의 분리다. 사용자는 운영 환경 없이 위 단일 합성 명령과 예제로 재현하고, GPT/Claude는 늦은 데이터·미청산 동결·위험 감소 주문 정책과 보수적 가격 proxy를 검토해야 한다. 실제 시장 데이터, PAPER 연결, 운영 DB, LIVE, 배포는 별도 설계·환경 분리·사용자 승인이 필요하다.

## 동적 수량 (`sizing='AFFORDABLE_UP_TO_CAP'`, 연구 모드)

기본값 `FIXED`는 한 줄도 바뀌지 않았다(기존 DAY-01·Scout 테스트 전체 무변경 통과). 동적 모드는 선택 사항이다. 한도 숫자(`max_order_amount` 등)는 바꾸지 않고, 수량만 한도에 맞춘다.

**신호 시점**: 진입 신호가 나면 그 시점에 알려진 가격(마지막 완성 분봉 종가)과 계좌로 `risk()`(기존 한도 검사 그대로: 주문 금액·종목 노출·전체 노출·현금·최소 현금 비율·포지션 수·일일 손실·미청산·지연 위험)를 `order_quantity`(상한)부터 1까지 내려가며 호출해 처음 통과하는 수량을 쓴다. 이 수량이 주문의 **신호 수량**이며 `ENTRY_QUANTITY_SIZED`(수량·상한·기준가)로 기록한다.
- 1주도 안 되면 주문하지 않는다. 수량에 의존하는 한도(`ORDER_AMOUNT_LIMIT`, `SYMBOL_EXPOSURE_LIMIT`, `TOTAL_EXPOSURE_LIMIT`, `INSUFFICIENT_CASH`, `MIN_CASH_RATIO`)가 원인이면 `ZERO_AFFORDABLE_QUANTITY`(`blocking`에 원인)로, 수량과 무관한 원인(`DAILY_LOSS_LIMIT`, `POSITION_LIMIT` 등)은 기존 사유 그대로 기록한다. 0주 신호는 일일 ENTRY 주문 수와 `entered`에 계산하지 않는다.
- **자원 예약**: 동적 모드는 연구 모드뿐 아니라 `next_open`·`legacy_close`에서도 대기 중인 BUY 주문의 현금·주문 금액·포지션 슬롯을 예약하고, 다음 신호의 `risk()`가 이를 차감한다(동시 신호가 같은 한도를 중복 사용하지 못함). 체결·취소·만료로 주문이 사라지면 예약도 사라진다. 동적 모드의 `equity_curve`에는 `reserved_cash`, `reserved_position_slots`가 추가된다(FIXED 비연구 모드에는 없음).

**체결 시점**(`next_open`, `legacy_close`): 체결 가격과 그 시점의 계좌로 다시 검사한다(자기 예약은 제외). 거래량 한도로 이미 줄어든 수량 이하에서 통과하는 최대 수량으로 **줄이고** `ENTRY_QUANTITY_REDUCED`(from/quantity/signal_quantity/reference_price)를 기록한다. 신호 수량보다 늘리지 않는다. 1주도 안 되면 주문을 취소하고 `ZERO_AFFORDABLE_QUANTITY`(phase `FILL`)를 기록한다. 줄어든 나머지는 기존처럼 `ENTRY_REMAINDER_CANCELLED`로 취소되고 예약이 해제된다.

**`research_estimated_open`**: 기존 신호 시점 예약 스냅샷과 초과 검증을 그대로 유지한다. 결산 시 추정 open 가격에서 예약에 맞지 않으면, 맞는 최대 수량(≥1)으로 줄이고 `ENTRY_QUANTITY_REDUCED`(phase `SETTLEMENT`)와 거래의 `QUANTITY_REDUCED_AT_OPEN_PRICE` 플래그를 남기며 실행은 무효화하지 않는다(정상적인 수량 조정). **1주도 맞지 않으면 아무것도 취소·은폐하지 않고 기존 `RESEARCH_RESOURCE_OVERRUN` → `INVALID_RESOURCE_OVERRUN`이 그대로 적용된다.** 줄인 뒤에도 초과 검사는 다시 수행되므로 실제 회계 불일치는 여전히 무효로 표시된다. 한계: 이 감소는 추정 open 가격이라는 사후 관측(`hindsight_fields`의 `open_price`)에 의존한다.

**Scout 연결**: Scout는 최종 수량을 정하지 않는다. `scout_config_for(day01_config)`는 동적 모드에서 `quantity=1`(최소 1주 가능 여부)로, FIXED에서는 `order_quantity`로 설정한다(`scout_quantity_for`, `limit_mismatches`가 이 규칙을 검사). Scout의 수수료 비반올림 때문에 경계(1원 미만)에서 Scout가 DAY-01보다 보수적일 수 있으나 반대는 없다.

**한계**: 합성 데이터 검증이며 실제 체결 가능 수량이나 호가 깊이를 보장하지 않는다. 신호 가격과 체결 가격의 차이는 위 재검증으로만 흡수된다.
