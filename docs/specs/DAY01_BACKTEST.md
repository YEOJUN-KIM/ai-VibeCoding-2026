# DAY-01 합성 백테스트 사양

구현 모듈: `auto_trader/ml/day01.py`. 구현 및 합성 자동 테스트 검증 단계이며 사용자 동작 검증·운영 적용은 하지 않았다. 수익성 검증이나 실거래 가능한 전략이라는 뜻이 아니다. DB, API, PAPER 엔진, 주문 전송, 파일 입출력, 환경 설정 로딩은 연결하지 않는다.

## API와 재현

Python 표준 라이브러리와 순수 `auto_trader.risk_policy.RiskPolicy`만 사용한다. 신규 설치가 필요하지 않다.

```python
from datetime import datetime, timedelta
from decimal import Decimal as D
from auto_trader.ml.day01 import Candle, Config, KST, replay

start = datetime(2026, 11, 12, 9, tzinfo=KST)
rows = []
for i in list(range(21)) + [350]:
    price = D('100') if i < 15 else D('102')
    volume = D('200') if 15 <= i < 20 else D('100')
    event = start + timedelta(minutes=i)
    rows.append(Candle('SYNTHETIC', event, event + timedelta(minutes=1),
                       price, price, price, price, volume, price * volume))
result = replay(rows, Config(), universe=['SYNTHETIC'])
assert [t['side'] for t in result['trades']] == ['BUY', 'SELL']
assert result['summary']['status'] == 'FLAT'
```

공개 진입점:

| 함수/타입 | 계약 |
|---|---|
| `Candle(symbol,event_at,available_at,open_price,high_price,low_price,close_price,volume,turnover=None)` | frozen dataclass. 금액·거래량은 유한 Decimal. 시간은 timezone-aware, event_at은 1분 시작 시각. turnover는 해당 1분의 실제 거래대금 |
| `Config()` | frozen dataclass. 설정 목록과 기본값은 아래 표 참조. `validate()`로 검증 |
| `select_universe(symbols)` | 사전 고정 문자열 목록을 정렬·중복 제거한 tuple. 가격·미래 성과로 종목 선정하지 않음 |
| `aggregate_five_minutes(rows, as_of)` | `available_at <= as_of`만 포함. 정각 5분 경계에 맞는 5개 분봉 모두 존재할 때만 `FiveMinuteBar` 목록 반환 |
| `evaluate_entry(history, at, config)` | 단일 종목·당일 이력으로 순수 `Decision` 반환. 외부 상태/주문 변경 없음 |
| `execution_costs(reference, quantity, side, config)` | 내부 검증을 통과한 양수 기준가·정수 수량의 BUY/SELL 비용 계산. 주문 제출 기능 없음 |
| `replay(rows, config=Config(), *, universe)` | 순수 결정적 dict 반환. 입력 순서 독립, 입력 객체 변경 없음. universe 밖 봉은 검증/시계에도 영향 없음 |

`Decision`: `enter, reason, at, range_high, vwap, previous_vwap`.
`FiveMinuteBar`: `symbol, event_at, end_at, available_at, open_price, high_price, low_price, close_price, volume, turnover`.
반환 값은 Decimal·datetime·date·Config를 유지한다. JSON 저장 기능은 없으며 필요한 별도 소비자가 명시적으로 직렬화해야 한다.

## 설정

| 필드 | 기본값 | 의미 |
|---|---:|---|
| initial_cash | 1000000 | 초기 KRW 현금 |
| order_quantity | 10 | 매수 주문 수량; 신호 시 고정 |
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
| max_signal_to_fill_minutes | 2 | 신호부터 실제 관측 체결 시각까지의 최대 ENTRY 대기 분. 유한 양수 Decimal, 최대 130분; 경계는 만료 |

정수 설정은 bool·0·음수를 거절한다. Decimal 설정은 음수·NaN·Infinity·잘못된 타입을 거절한다. stop/slippage <1, participation <=1, cash ratio <=100, fee+tax <=1, RR 하한을 강제한다. 주문 제한 해제 스위치는 없다.

ENTRY 수명은 `execution_at - signal_at < max_signal_to_fill_minutes`다. 09:20 신호의 기본 만료는 09:22이며 09:21 체결은 가능하고 09:22 체결은 불가능하다. 데이터가 없어도 명시적 만료 타이머로 `ENTRY_SIGNAL_TOO_OLD`를 남기고 주문을 제거한다. 11:30 진입창 종료가 더 빠르면 `ENTRY_EXPIRED`다. 승인 주문수는 만료되어도 차감하지 않는다. 설정값은 timedelta의 마이크로초 정밀도로 해석하며 너무 짧아 실행 봉을 기다릴 수 없는 설정은 체결되지 않는다.

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

이 모델은 next-open 모델이 아니다. 신호 이후 분봉 시작이 `event_at >= signal_at`이며 `available_at == event_at+1m`인 새 양수 거래량 봉의 종가를 후속 실행 증거로 사용한다. `execution_at = observed_at = available_at`이며 과거 open 시각으로 체결시각을 되돌리지 않는다. 지연 봉의 종가를 실제 현재 체결가로 간주하지 않는다. 지연이 있는 현실 피드는 이 엄격한 조건 때문에 체결이 상당수 거절될 수 있다.

완성 봉의 마지막 가격은 종가 주문 체결 가능성을 보장하지 않는다. synthetic last-price proxy이며 호가·틱·지연 주문 접수·거래 우선순위를 재현하지 않는다. 참여율에 따른 수량 상한은 단순 분봉 거래량 모형으로 실제 유동성 보장이 아니다.

**평가·위험 관측과 실행 증거는 별도다.** 양수 거래량의 당일 지연 봉도 실제 `available_at`에 이력·평가·손절/목표 감시에 반영한다. 평가가격에는 마지막 수신 순서 대신 가장 최신 `event_at`을 사용하고 `valuation_marks`에 사건 시각과 수신 시각을 따로 남긴다. 09:22 급락 봉이 10:00에 왔고 더 최신 봉이 없다면 10:00부터 급락 종가로 평가한다. 이미 09:30의 더 최신 봉을 알면 평가가격은 유지하며 `OLDER_OBSERVATION_MARK_PRESERVED`를 기록한다. 과거 equity curve를 재작성하지 않는다.

보유 시작 이후 구간의 지연 봉에서 손절/목표를 발견하면 도착 시점의 SELL 신호와 `LATE_RISK_OBSERVATION`을 기록한다. 이미 더 최신 가격으로 반등을 알더라도 과거 위험 누락을 조용히 무시하지 않는 보수적 정책이다. 해당 잔량이 모두 청산될 때까지 계좌 신규 매수를 `LATE_RISK_EXIT_PENDING`으로 차단한다. 손실 한도를 이미 넘었으면 `DAILY_LOSS_LIMIT` 사유가 우선한다. 손절이 기존 TARGET/세션 청산보다 불리한 정보를 제공하면 도착 시각으로 SELL을 교체하고 `EXIT_REPLACED_BY_STOP`을 기록하며 총 주문수에 포함한다. 지연 데이터 자체나 같은 도착 batch의 과거 분봉으로 체결하지 않고 이후 온타임 실행 증거를 기다린다. 보유 이전 구간의 wick은 보유 포지션 손절로 해석하지 않는다. 0거래량은 평가·위험 증거에서 제외하고 15:10 확정 동결 이후에는 재평가·추가 거래하지 않는다.

매수 체결 봉 자체의 고저가로 손절·목표를 소급 판단하지 않는다. 보유 시점 이후의 후속 온타임 봉에서 low <= stop, high >= target을 관측하면 새로운 SELL 신호를 만들고 그 다음 실행 가능한 분봉을 기다린다. 한 봉에서 양쪽이 닿으면 `AMBIGUOUS_STOP_FIRST`를 선택한다.

손절 관측 이후 반등으로 손익이 유리해지지 않도록 매도 기준가는 `min(후속 close, 후속 open, 관측 stop 또는 트리거 봉의 더 낮은 open)`이다. 목표는 `min(후속 close, target)`으로 상한을 둔다. 이 불리한 trigger proxy는 후속 봉 범위 밖의 더 낮은 가격일 수도 있다. 해당 가격에서 실제 거래가 있었다고 주장하지 않으며 `evidence_close`, `reference_price`, `price`를 각각 기록한다. 갭과 슬리피지로 손실 한도를 초과할 수 있으며 현금흐름을 한도로 잘라서 숨기지 않는다. 일별 손실 초과는 equity curve에 기록한다.

수수료·세금은 기존 `paper.py`의 계산과 동일하게 불리한 슬리피지 적용 거래대금 × 요율을 매 체결마다 KRW 정수 `ROUND_DOWN`한다. 매수세는 0이다. 매수 원가에는 수수료를 포함하고 매도 실현이익은 매도 순대금 - 비례 배분한 매수 원가다. 기존 broker를 import하지 않고 수식을 재현한다.

14:50에 데이터 유무와 관계없이 별도 타이머 이벤트가 SELL 신호를 만든다. 14:50에 시작한 후속 봉의 14:51 관측이 첫 체결 가능 시점이다. 앞선 14:49 봉, 0거래량, 누락 봉, 지연 봉으로 가짜 청산하지 않는다. 정수 참여수량만큼 부분 매도하고 나머지는 같은 주문으로 다음 분봉을 기다린다. 매수 부분 체결 후 나머지는 취소한다. 15:10은 보수적인 exclusive 실행 마감으로 15:10 관측조차 체결하지 않고 남은 수량을 `UNLIQUIDATED_AT_1510`으로 확정한다.

미청산 수량·매수원가·마지막 인과적 평가가격을 보존한다. 다음날 봉으로 강제 청산하거나 가격을 조용히 바꾸지 않는다. `UNLIQUIDATED` 상태에서는 전 계좌 신규 노출을 막는다. 평가금액은 동결된 마지막 가격의 가상 비용 차감 순청산가이며 실현이익과 구분한다. 실제 다음날 갭 손실/수익은 미검증·미모델링이다. 다음날 정상 재개는 미청산 잔량이 없는 경우에만 가능하며 범위·VWAP·한도는 KST 날짜별 초기화한다.

summary의 `overnight_gap_exposure_unmodeled=True`와 `frozen_valuation_symbols` 목록은 미청산 종목의 익일 갭 노출을 모델링하지 않고 있음을 명시한다. 다음날 `net_change=0`은 동결 평가 결과이며 실제 손실·수익이 0이라는 뜻이 아니다. 미청산 상태의 진입 거절은 동일 종목 재진입 검사보다 우선하여 `UNRESOLVED_EXPOSURE`로 보고한다.

## 위험 정책과 기록

현금·주문금액·종목금액·전체 노출·보유수·잔여현금비율·일 손실을 신호와 실제 매수 체결 모두에서 검사한다. 신호 시 수량은 고정하지만 부분 체결시 사용 가능한 수량에 대해 다시 검사한다. 모든 동시 관측 가격을 먼저 반영해 종목 순서로 오래된 평가가격을 사용하는 문제를 피한다. 신호 때 미래 현금을 예약하지는 않으며 후속 체결에서 재검사하여 초과 주문을 거절한다.

`daily_order_limit`은 ENTRY 승인 수 한도이다. 거절된 신호는 카운트하지 않고, 승인 후 미체결/체결거절된 ENTRY는 카운트한다. 위험 감소 SELL은 신규 노출 한도와 별도 정책으로 항상 허용하며 총 주문수에는 포함한다. 부분 체결은 같은 주문으로 주문수를 추가하지 않는다. `filled_orders`는 실제로는 체결 레코드 수라 부분 체결도 각각 센다. 위험 한도를 우회하는 설정이나 외부 주문 경로는 없다.

반환 최상위 키:

| 키 | 내용 |
|---|---|
| config, universe, vwap_mode, execution_model | 설정과 가정 |
| summary | cash, net_liquidation_equity, net_profit, realized_profit, fees, taxes, slippage, filled_orders, open_positions, unresolved_quantities, overnight_gap_exposure_unmodeled, frozen_valuation_symbols, entry_orders, total_orders, status |
| positions | symbol별 quantity, entry_price, cost_basis, day, entry_at, stop, target, unresolved |
| valuation_marks | 보유 symbol별 price, event_at(최신 평가 사건), observed_at(해당 가격 실제 도착), frozen |
| signals | symbol, side, at, reason, quantity, trigger. 제출 시각과 당시 수량의 복사본 |
| trades | symbol, side, quantity, signal_at, event_at, execution_at, observed_at, evidence_close, reference_price, reason, realized_profit, price, amount, fee, tax, slippage, cash_delta |
| reasons | at, symbol, reason 및 해당 항목의 phase·quantity·VWAP 등 상세 |
| failures | 15:10 at, symbol, `UNLIQUIDATED_AT_1510`, quantity |
| equity_curve | at, cash, net_liquidation_equity, daily_net_change, loss_limit_exceeded, unresolved |
| daily_results | day, closing_equity, net_change, entry_orders, total_orders, unresolved_quantities |

주요 사유는 코드 문자열로 고정한다: `INCOMPLETE_OPENING_RANGE`, `NO_FRESH_FIVE_MINUTE_BAR`, `INCOMPLETE_SESSION_VWAP`, `MISSING_EXACT_TURNOVER`, `NO_RANGE_BREAKOUT`, `INSUFFICIENT_VOLUME_INCREASE`, `NO_RISING_VWAP`, `LATE_OBSERVATION_NOT_EXECUTABLE`, `ENTRY_EXPIRED`, `NO_EXECUTABLE_CAPACITY`, `ENTRY_REMAINDER_CANCELLED`, `UNFILLED_ORDER_AT_1510`, `UNRESOLVED_EXPOSURE`, `DAILY_LOSS_LIMIT`, `POSITION_LIMIT`, `ORDER_AMOUNT_LIMIT`, `SYMBOL_EXPOSURE_LIMIT`, `TOTAL_EXPOSURE_LIMIT`, `INSUFFICIENT_CASH`, `MIN_CASH_RATIO`, `DAILY_ENTRY_ORDER_LIMIT` 등이다.

리뷰 수정으로 `ENTRY_SIGNAL_TOO_OLD`, `LATE_RISK_OBSERVATION`, `LATE_RISK_EXIT_PENDING`, `OLDER_OBSERVATION_MARK_PRESERVED`, `EXIT_REPLACED_BY_STOP`을 추가했다. 함수 시그니처와 기존 API/DB 계약은 유지하며 Config와 결과 dict에 위 필드를 추가했다.

## 기존 코드와 검증 경계

기존 `backtest.py`는 `RawCandle`, PAPER broker, MA engine을 사용하며 MA 설정·주문 엔진과 결합되어 있다. `data_pipeline.RawCandle`의 completed minute/availability 계약을 참조했지만 실제 거래대금이 없어 신규 Candle에 optional turnover를 둔다. 기존 API/DB 스키마 변경은 없다.

`backtest`, `data_pipeline`, `paper`, `risk`는 database/settings로 이어져 settings import가 `.env`를 읽을 수 있다. 이번 승인 범위는 비밀정보 접근을 금지하므로 해당 모듈 및 기존 백테스트/데이터 파이프라인 테스트는 실행하지 않는다. 데이터 파이프라인 테스트는 setUp에서 DB 스키마까지 생성하므로 추가로 부적합하다. 기존 파일은 모두 읽기 전용으로 유지했다. 순수 RiskPolicy만 재사용해 금융 한도 계산의 단위를 유지했다.

검증 명령: `python -B -m unittest discover -s tests -p test_day01.py -v`. 합성 예제 함수는 `tests/test_day01.py`의 `minute(index, price='100', *, day=12, symbol='AAA', volume='100', **changes)`와 `scenario(*, day=12, symbol='AAA')`다. 독립 리뷰는 Claude 소유 `tests/test_day01_review.py`에서 별도로 수행한다.

장점은 환경 접근 없는 결정성, 인과적 시각, 비용·위험의 명시성이다. 단점은 엄격한 온타임 조건, 보수적인 청산 proxy, 단순 유동성 모형, 미청산 평가가격 동결이다. 학습 개념은 정보 이용 가능 시각과 사건 시각의 분리, 전략 판단과 실행 증거의 분리다. 사용자는 운영 환경 없이 위 단일 합성 명령과 예제로 재현하고, GPT/Claude는 늦은 데이터·미청산 동결·위험 감소 주문 정책과 보수적 가격 proxy를 검토해야 한다. 실제 시장 데이터, PAPER 연결, 운영 DB, LIVE, 배포는 별도 설계·환경 분리·사용자 승인이 필요하다.
