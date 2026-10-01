"""이동평균 교차 전략을 실행하는 연습용 자동매매 워커."""

import asyncio
import logging
from collections import deque
from datetime import datetime, time, timedelta
from decimal import Decimal
from typing import Callable
from zoneinfo import ZoneInfo

from .models import (
    OrderRequest,
    OrderSide,
    SignalEvent,
    StrategySnapshot,
    StrategyStatus,
)
from .paper import PaperBroker
from .simulator import MarketSimulator
from .paper_feed import PaperPriceFeed
from .toss import TossApiError


KST = ZoneInfo("Asia/Seoul")


class MovingAverageEngine:
    def __init__(
        self,
        market: MarketSimulator,
        broker: PaperBroker,
        *,
        interval_seconds: int = 2,
        short_period: int = 5,
        long_period: int = 20,
        order_quantity: int = 1,
        take_profit_rate: Decimal = Decimal("5"),
        stop_loss_rate: Decimal = Decimal("3"),
        max_holding_days: int = 20,
        trading_start: str = "09:00",
        trading_end: str = "15:20",
        cooldown_minutes: int = 30,
        clock: Callable[[], datetime] | None = None,
        price_feed: PaperPriceFeed | None = None,
    ) -> None:
        self.market = market
        self.broker = broker
        self.interval_seconds = interval_seconds
        self.short_period = short_period
        self.long_period = long_period
        self.order_quantity = order_quantity
        self.sizing_mode = "QUANTITY"
        self.order_amount = Decimal("100000")
        self.take_profit_rate = Decimal(take_profit_rate)
        self.stop_loss_rate = Decimal(stop_loss_rate)
        self.max_holding_days = max_holding_days
        self.trading_start = self._parse_time(trading_start)
        self.trading_end = self._parse_time(trading_end)
        self.cooldown_minutes = cooldown_minutes
        self._clock = clock or (lambda: datetime.now().astimezone())
        self.price_feed = price_feed
        self.last_data_at = None
        self.data_message = "시세 수신 대기"
        self._last_bar_at = {}
        self._entry_armed = set()
        self._above_count = {}
        self._realized_profit = Decimal(0)
        self._trading_costs = Decimal(0)
        self._completed_trades = 0
        self._winning_trades = 0
        self._peak_profit = Decimal(0)
        self._max_drawdown = Decimal(0)
        self.daily_order_limit = 0
        self._daily_orders = {}
        self.running = False
        self.emergency_stopped = False
        self.tick_count = 0
        self._task: asyncio.Task[None] | None = None
        self._history = {
            stock.symbol: deque(maxlen=long_period) for stock in market.stocks()
        }
        self._previous_trend: dict[str, str] = {}
        self._snapshots: dict[str, StrategySnapshot] = {}
        self._signals: list[SignalEvent] = []
        self.target_symbols: list[str] = []
        self.strategy_id = None
        self.strategy_name = None
        self._position_opened_at: dict[str, datetime] = {}
        self._last_order_at: dict[str, datetime] = {}
        self._strategy_baseline_value = Decimal("0")
        self._strategy_cash_flow = Decimal("0")
        self._strategy_buy_amount = Decimal("0")

    @staticmethod
    def _parse_time(value: str) -> time:
        return datetime.strptime(value, "%H:%M").time()

    async def start(self) -> bool:
        if self.running:
            return False
        self.emergency_stopped = False
        self.running = True
        self.tick_count = 0
        for history in self._history.values():
            history.clear()
        self._previous_trend.clear()
        self._snapshots.clear()
        self._last_bar_at.clear()
        self._entry_armed.clear()
        self._above_count.clear()
        self.broker._journal({"event": "STRATEGY_START", "run_id": self.broker.run_id,
                              "created_at": self._clock().isoformat(), "symbols": self.target_symbols})
        self._task = asyncio.create_task(self._run())
        return True

    async def stop(self, *, emergency: bool = False) -> bool:
        was_running = self.running
        self.running = False
        self.emergency_stopped = emergency
        if self._task and self._task is not asyncio.current_task():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        if was_running:
            try:
                self.broker._journal({"event": "STRATEGY_STOP", "run_id": self.broker.run_id,
                                      "created_at": self._clock().isoformat(),
                                      "emergency": emergency, "ticks": self.tick_count})
            except OSError:
                logging.getLogger(__name__).exception("Could not write paper strategy stop event")
        return was_running

    async def _run(self) -> None:
        try:
            while self.running:
                if self.price_feed:
                    await self.poll_market()
                else:
                    self.step()
                await asyncio.sleep(self.interval_seconds)
        except asyncio.CancelledError:
            raise
        except Exception:
            self.data_message = "실행 오류로 중지됨: 서버 로그와 주문 기록을 확인하세요."
            logging.getLogger(__name__).exception("Paper strategy stopped")
        finally:
            self.running = False

    async def poll_market(self) -> None:
        if not self._within_hours():
            self.data_message = "운영 시간 외 대기 · 실제 시세 기반 모의매매"
            return
        now = self._clock()
        symbols = list(dict.fromkeys((self.target_symbols or [stock.symbol for stock in self.market.stocks()]) + [lot["symbol"] for lot in self.broker.managed_lots()]))
        valuation_symbols = [p.symbol for p in self.broker.account().positions]
        try:
            prices, candles = await asyncio.to_thread(
                self.price_feed.read, symbols, valuation_symbols, max([self.long_period] + [int(lot["context"].get("long_period", self.long_period)) + 1 for lot in self.broker.managed_lots()]), now)
        except TossApiError as exc:
            self.data_message = ("API 요청 한도 대기: 다음 시세 조회에서 자동으로 다시 시도합니다."
                                 if exc.status_code == 429 else "시세 오류·지연 또는 휴장: 신규 판단과 체결을 대기합니다.")
            logging.getLogger(__name__).warning("Paper market data unavailable: %s", exc)
            return
        except Exception:
            self.data_message = "시세 오류·지연 또는 휴장: 신규 판단과 체결을 대기합니다."
            logging.getLogger(__name__).warning("Paper market data unavailable", exc_info=True)
            return
        # 중지/설정 변경 중 끝난 조회나 지나치게 오래 걸린 조회를 체결에 사용하지 않는다.
        if not self._within_hours() or self._clock() - now > timedelta(seconds=30):
            self.data_message = "시세 조회 지연 또는 운영 종료: 다음 시세를 기다립니다."
            return
        for symbol, price in prices.items():
            if self.market.has_symbol(symbol) and price.is_finite() and price > 0:
                stock = next(s for s in self.market.stocks() if s.symbol == symbol)
                self.market.upsert_stock(stock, price)
        self.last_data_at = self._clock()
        self.data_message = "실제 현재가 · 완료된 1분봉 · 모의 체결"
        unavailable = getattr(self.price_feed, "unavailable", {})
        if not isinstance(unavailable, dict):
            unavailable = {}
        if unavailable:
            stocks = {stock.symbol: stock.name for stock in self.market.stocks()}
            reasons = " / ".join(f"{stocks.get(symbol, symbol)}: {reason}" for symbol, reason in unavailable.items())
            self.data_message = f"{len(candles)}/{len(symbols)}종목 판단 가능 · 대기 {reasons}"
            for symbol, reason in unavailable.items():
                self._snapshots[symbol] = StrategySnapshot(
                    symbol=symbol, name=stocks.get(symbol, symbol),
                    price=self.market.quote(symbol).price, collected_prices=0, decision=f"시세 대기 · {reason}")
        if not candles:
            return
        self.step(candles=candles)

    def step(self, *, candles=None) -> None:
        if self.price_feed is not None and candles is None:
            raise ValueError("실제 시세 모드에는 최신 봉 데이터가 필요합니다.")
        self.tick_count += 1
        exited = self._manage_exits(candles)
        quotes = ([self.market.quote(symbol, move=candles is None) for symbol in self.target_symbols]
                  if self.target_symbols else self.market.quotes(move=candles is None))
        for quote in quotes:
            if candles is not None and quote.symbol not in candles:
                continue
            history = self._history[quote.symbol]
            new_bar = True
            if candles is None:
                history.append(quote.price)
            else:
                bars = candles[quote.symbol]
                previous_bar = self._last_bar_at.get(quote.symbol)
                new_bar = bool(bars) and (previous_bar is None or bars[-1].timestamp > previous_bar)
                if new_bar:
                    if previous_bar and bars[-1].timestamp - previous_bar > timedelta(minutes=2):
                        self._previous_trend.pop(quote.symbol, None)
                        self._above_count.pop(quote.symbol, None)
                        self._entry_armed.discard(quote.symbol)
                    history.clear()
                    history.extend(bar.close_price for bar in bars[-self.long_period:])
                    self._last_bar_at[quote.symbol] = bars[-1].timestamp
            stock = next(item for item in self.market.stocks() if item.symbol == quote.symbol)

            if quote.symbol in exited:
                continue

            if len(history) < self.long_period:
                self._snapshots[quote.symbol] = StrategySnapshot(
                    symbol=quote.symbol,
                    name=stock.name,
                    price=quote.price,
                    collected_prices=len(history),
                    data_at=self._last_bar_at.get(quote.symbol),
                    decision=f"가격 수집 중 {len(history)}/{self.long_period}",
                )
                continue

            short_average = sum(list(history)[-self.short_period :], Decimal("0")) / self.short_period
            long_average = sum(history, Decimal("0")) / self.long_period
            trend = "ABOVE" if short_average > long_average else "BELOW"
            previous_trend = self._previous_trend.get(quote.symbol)

            self._snapshots[quote.symbol] = StrategySnapshot(
                symbol=quote.symbol,
                name=stock.name,
                price=quote.price,
                collected_prices=len(history),
                data_at=self._last_bar_at.get(quote.symbol),
                short_average=short_average,
                long_average=long_average,
                trend=trend,
                decision="판단 중",
            )
            if not new_bar:
                self._snapshots[quote.symbol].decision = (
                    "보유 중 · 청산 조건 감시" if self.broker.strategy_quantity(quote.symbol)
                    else "다음 완료 1분봉 대기"
                )
                continue
            if trend == "BELOW":
                self._entry_armed.discard(quote.symbol)
                self._above_count[quote.symbol] = 0
                self._snapshots[quote.symbol].decision = "단기선 열세 · 상향 교차 대기"
            else:
                self._above_count[quote.symbol] = self._above_count.get(quote.symbol, 0) + 1
                if previous_trend == "BELOW":
                    self._entry_armed.add(quote.symbol)
                self._snapshots[quote.symbol].decision = (
                    f"상향 교차 확인 {min(self._above_count[quote.symbol], 2)}/2"
                    if quote.symbol in self._entry_armed else "상승 중 · 새 상향 교차 대기"
                )

            # 교차 강도를 거래비용과 직접 비교하면 짧은 이동평균에서 정상 신호까지
            # 거의 모두 제거된다. 비용은 체결 손익에 반영하고 진입은 2봉·추세로 확인한다.
            rising = history[-1] > history[0] and quote.price >= short_average
            if (quote.symbol in self._entry_armed and self._above_count.get(quote.symbol, 0) >= 2
                    and rising and not self.broker.strategy_quantity(quote.symbol)):
                if self._buy(quote.symbol, quote.price):
                    self._entry_armed.discard(quote.symbol)
                    self._snapshots[quote.symbol].decision = "매수 체결 · 청산 조건 감시"
                else:
                    self._snapshots[quote.symbol].decision = "매수 조건 충족 · 주문 제한/잔액 확인"
            elif quote.symbol in self._entry_armed and self._above_count.get(quote.symbol, 0) >= 2:
                self._snapshots[quote.symbol].decision = "2봉 확인 · 가격 추세 확인 대기"
            elif previous_trend == "ABOVE" and trend == "BELOW":
                self._sell(quote.symbol, "단기 이동평균이 장기 이동평균을 하향 돌파")

            self._previous_trend[quote.symbol] = trend
        self._update_drawdown()

    def _buy_quantity(self, symbol: str) -> int:
        if self.sizing_mode == "QUANTITY":
            return self.order_quantity
        # Use the same price, slippage and rounded fee as the broker. The broker
        # rechecks all limits at submission if the account changes meanwhile.
        from decimal import ROUND_DOWN
        price = self.market.quote(symbol).price * (1 + self.broker.slippage_rate)
        if price <= 0:
            return 0
        cash = self.broker.account().cash
        maximum = min(1000, int(min(self.order_amount, cash) / price))
        low, high = 0, maximum
        while low < high:
            quantity = (low + high + 1) // 2
            amount = price * quantity
            fee = (amount * self.broker.fee_rate).quantize(Decimal("1"), rounding=ROUND_DOWN)
            allowed = amount + fee <= min(self.order_amount, cash)
            if allowed and self.broker.risk_manager:
                allowed = self.broker.risk_manager.check_buy(
                    symbol=symbol, amount=amount, fee=fee,
                    ignore_min_cash_ratio=self.broker.ignore_min_cash_ratio,
                    ignore_daily_order_limit=self.broker.ignore_daily_order_limit) is None
            if allowed:
                low = quantity
            else:
                high = quantity - 1
        return low

    def _buy(self, symbol: str, price: Decimal) -> bool:
        if not self._can_order(symbol):
            return False
        today = self._clock().astimezone(KST).date()
        if (not self.broker.ignore_daily_order_limit and self.daily_order_limit > 0
                and self._daily_orders.get(today, 0) >= self.daily_order_limit):
            return False
        quantity = self._buy_quantity(symbol)
        if quantity < 1:
            return False
        reason = "상향 교차 후 2봉 확인 · 가격 추세 확인"
        self._record_signal(symbol, OrderSide.BUY, reason)
        before = self.broker.strategy_quantity(symbol)
        order = self.broker.submit(OrderRequest(symbol=symbol, side=OrderSide.BUY, quantity=quantity),
                                   source="STRATEGY", reason=reason, acquired_at=self._clock())
        if order.status.value == "FILLED":
            now = self._clock()
            cash_change = -(order.price * order.quantity + order.fee)
            self._strategy_cash_flow += cash_change
            self._strategy_buy_amount = max(self._strategy_buy_amount,
                                            -self._strategy_cash_flow)
            self._trading_costs += order.fee + order.tax + order.slippage
            self._daily_orders[today] = self._daily_orders.get(today, 0) + 1
            self._last_order_at[symbol] = now
            if before == 0:
                self._position_opened_at[symbol] = now
            return True
        return False

    def _sell(self, symbol: str, reason: str, *, lot_id=None) -> None:
        if not self._within_hours():
            return
        quantity = next((lot["quantity"] for lot in self.broker.managed_lots() if lot["id"] == lot_id), 0) if lot_id else self.broker.strategy_quantity(symbol)
        if not quantity:
            return
        self._record_signal(symbol, OrderSide.SELL, reason)
        trade_profit = Decimal(0)
        # 요청 모델의 1회 1,000주 범위 안에서 관리 보유분 전체를 청산한다.
        while quantity:
            order = self.broker.submit(OrderRequest(symbol=symbol, side=OrderSide.SELL, quantity=min(quantity, 1000)),
                                       source="STRATEGY", reason=reason, lot_id=lot_id)
            if order.status.value != "FILLED":
                break
            self._strategy_cash_flow += order.price * order.quantity - order.fee - order.tax
            self._trading_costs += order.fee + order.tax + order.slippage
            self._realized_profit += order.realized_profit or Decimal(0)
            trade_profit += order.realized_profit or Decimal(0)
            today = self._clock().astimezone(KST).date()
            self._daily_orders[today] = self._daily_orders.get(today, 0) + 1
            self._last_order_at[symbol] = self._clock()
            quantity = next((lot["quantity"] for lot in self.broker.managed_lots() if lot["id"] == lot_id), 0) if lot_id else self.broker.strategy_quantity(symbol)
            if not quantity:
                self._position_opened_at.pop(symbol, None)
                self._completed_trades += 1
                self._winning_trades += int(trade_profit > 0)

    def _can_order(self, symbol: str) -> bool:
        now = self._clock()
        if not self._within_hours():
            return False
        last_order = self._last_order_at.get(symbol)
        return not last_order or now - last_order >= timedelta(minutes=self.cooldown_minutes)

    def _within_hours(self) -> bool:
        now = self._clock().astimezone(KST)
        return now.weekday() < 5 and self.trading_start <= now.time().replace(tzinfo=None) <= self.trading_end

    def _exit_reason(self, symbol: str, price: Decimal) -> str | None:
        quantity = self.broker.strategy_quantity(symbol)
        if not quantity:
            return None
        average = self.broker.strategy_average(symbol)
        if average is None or average <= 0:
            return None
        return_rate = (self.broker.exit_proceeds(symbol, quantity, price) / (average * quantity) - 1) * 100
        if return_rate >= self.take_profit_rate:
            return f"익절률 {self.take_profit_rate}% 도달"
        if return_rate <= -self.stop_loss_rate:
            return f"손절률 {self.stop_loss_rate}% 도달"
        opened_at = self._position_opened_at.get(symbol)
        if opened_at and self._clock() - opened_at >= timedelta(days=self.max_holding_days):
            return f"최대 보유일 {self.max_holding_days}일 도달"
        return None

    def _management_context(self):
        return dict(strategy_id=self.strategy_id, strategy_name=self.strategy_name, adopted_at=self._clock().isoformat(),
                    short_period=self.short_period, long_period=self.long_period,
                    take_profit_rate=str(self.take_profit_rate), stop_loss_rate=str(self.stop_loss_rate),
                    max_holding_days=self.max_holding_days,
                    trading_start=self.trading_start.strftime('%H:%M'), trading_end=self.trading_end.strftime('%H:%M'))

    def _manage_exits(self, candles):
        exited = set()
        now = self._clock()
        for lot in self.broker.managed_lots():
            symbol, context = lot['symbol'], lot['context']
            if candles is not None and symbol not in candles:
                continue
            local = now.astimezone(KST)
            if local.weekday() >= 5 or not self._parse_time(context.get('trading_start', '09:00')) <= local.time().replace(tzinfo=None) <= self._parse_time(context.get('trading_end', '18:00')):
                continue
            price = self.market.quote(symbol).price
            basis = Decimal(lot['average_price']) * lot['quantity']
            if basis <= 0:
                continue
            rate = (self.broker.exit_proceeds(symbol, lot['quantity'], price) / basis - 1) * 100
            reason = None
            if rate >= Decimal(context.get('take_profit_rate', str(self.take_profit_rate))):
                reason = '익절률 도달'
            elif rate <= -Decimal(context.get('stop_loss_rate', str(self.stop_loss_rate))):
                reason = '손절률 도달'
            elif now - datetime.fromisoformat(lot['opened_at']) >= timedelta(days=int(context.get('max_holding_days', self.max_holding_days))):
                reason = '최대 보유일 도달'
            elif candles is not None:
                closes = [bar.close_price for bar in candles[symbol]]
                short, long = int(context.get('short_period', self.short_period)), int(context.get('long_period', self.long_period))
                if len(closes) >= long + 1:
                    previous = sum(closes[-short-1:-1]) / short - sum(closes[-long-1:-1]) / long
                    current = sum(closes[-short:]) / short - sum(closes[-long:]) / long
                    if previous > 0 and current <= 0:
                        reason = '이동평균 하향 교차'
            if reason:
                self._sell(symbol, f"{context.get('strategy_name') or '현재 전략'} · {reason}", lot_id=lot['id'])
                exited.add(symbol)
            if symbol not in self.target_symbols:
                self._snapshots[symbol] = StrategySnapshot(symbol=symbol, name=self.market.quote(symbol).name,
                    price=price, collected_prices=0, data_at=candles[symbol][-1].timestamp if candles else None,
                    decision=f"청산 감시 · {context.get('strategy_name') or '현재 전략'}")
        return exited

    def set_management_scope(self, scope):
        if self.running:
            raise ValueError('자동매매를 일시정지한 뒤 관리 범위를 변경하세요.')
        self.reset_performance_baseline(scope=scope)
        self._snapshots.clear()
        self.last_data_at = None
        self.data_message = '관리 범위 적용 완료 · 시작하면 시세를 조회합니다.'

    def _record_signal(self, symbol: str, side: OrderSide, reason: str) -> None:
        self._signals.append(
            SignalEvent(
                symbol=symbol,
                side=side,
                reason=reason,
                created_at=datetime.now().astimezone(),
            )
        )
        self._signals = self._signals[-20:]

    def reset_performance_baseline(self, *, scope=None) -> None:
        self._strategy_baseline_value = self.broker.begin_strategy(self.target_symbols, context=self._management_context(), scope=scope)
        self._strategy_cash_flow = Decimal("0")
        self._strategy_buy_amount = Decimal("0")
        self._realized_profit = Decimal(0)
        self._trading_costs = Decimal(0)
        self._completed_trades = self._winning_trades = 0
        self._peak_profit = self._max_drawdown = Decimal(0)
        self._position_opened_at = {s: self._clock() for s in self.target_symbols if self.broker.strategy_quantity(s)}
        self._last_order_at.clear()
        self._signals.clear()
        self._last_bar_at.clear()
        self._entry_armed.clear()
        self._above_count.clear()

    def configure(self, *, interval_seconds: int, short_period: int, long_period: int,
                  order_quantity: int, target_symbol: str | None = None,
                  target_symbols: list[str] | None = None,
                  take_profit_rate: Decimal | None = None,
                  stop_loss_rate: Decimal | None = None,
                  max_holding_days: int | None = None,
                  trading_start: str | None = None,
                  trading_end: str | None = None,
                  cooldown_minutes: int | None = None,
                  daily_order_limit: int = 0, sizing_mode: str = "QUANTITY",
                  order_amount: Decimal = Decimal("100000"), strategy_id=None, strategy_name=None) -> None:
        if self.running:
            raise ValueError("자동매매를 중지한 뒤 전략을 변경하세요.")
        if short_period >= long_period:
            raise ValueError("단기 이동평균은 장기 이동평균보다 작아야 합니다.")
        if not 1 <= order_quantity <= 1000:
            raise ValueError("1회 주문 수량은 1~1,000주입니다. 하루 주문 횟수 한도와는 별개입니다.")
        self.interval_seconds = interval_seconds
        self.short_period = short_period
        self.long_period = long_period
        self.order_quantity = order_quantity
        if sizing_mode not in {"QUANTITY", "AMOUNT"} or not Decimal(order_amount).is_finite() or Decimal(order_amount) <= 0:
            raise ValueError("매수 방식과 1회 매수 금액을 확인하세요.")
        self.sizing_mode = sizing_mode
        self.order_amount = Decimal(order_amount)
        self.daily_order_limit = daily_order_limit
        if take_profit_rate is not None:
            self.take_profit_rate = Decimal(take_profit_rate)
        if stop_loss_rate is not None:
            self.stop_loss_rate = Decimal(stop_loss_rate)
        if max_holding_days is not None:
            self.max_holding_days = max_holding_days
        if trading_start is not None:
            self.trading_start = self._parse_time(trading_start)
        if trading_end is not None:
            self.trading_end = self._parse_time(trading_end)
        if self.trading_start >= self.trading_end:
            raise ValueError("운영 종료 시간은 시작 시간보다 늦어야 합니다.")
        if cooldown_minutes is not None:
            self.cooldown_minutes = cooldown_minutes
        self.strategy_id, self.strategy_name = strategy_id, strategy_name
        self.target_symbols = list(dict.fromkeys(target_symbols or ([target_symbol] if target_symbol else [])))
        self._history = {stock.symbol: deque(maxlen=long_period) for stock in self.market.stocks()}
        self._previous_trend.clear()
        self._snapshots.clear()
        self.last_data_at = None
        self.data_message = "전략 적용 완료 · 시작하면 새 종목의 시세를 조회합니다."
        now = self._clock()
        self._position_opened_at = {
            symbol: now for symbol in self.target_symbols if self.broker.holding_quantity(symbol) > 0
        }
        self._last_order_at.clear()
        self.reset_performance_baseline()
        self.broker._journal({"event": "STRATEGY_SETTINGS", "run_id": self.broker.run_id,
                              "symbols": self.target_symbols, "short_period": short_period,
                              "long_period": long_period, "bar_interval": "1m", "order_quantity": order_quantity,
                              "sizing_mode": self.sizing_mode, "order_amount": str(self.order_amount),
                              "cooldown_minutes": self.cooldown_minutes, "daily_order_limit": self.daily_order_limit,
                              "take_profit_rate": str(self.take_profit_rate), "stop_loss_rate": str(self.stop_loss_rate),
                              "fee_rate": str(self.broker.fee_rate), "sell_tax_rate": str(self.broker.sell_tax_rate),
                              "slippage_rate": str(self.broker.slippage_rate)})
        self.tick_count = 0

    def status(self) -> StrategyStatus:
        current_strategy_value = self.broker.strategy_value()
        strategy_profit = current_strategy_value + self._strategy_cash_flow - self._strategy_baseline_value - self.broker._management_external_flow
        strategy_basis = self._strategy_baseline_value + self._strategy_buy_amount + self.broker._management_external_flow
        return StrategyStatus(
            running=self.running,
            emergency_stopped=self.emergency_stopped,
            tick_count=self.tick_count,
            interval_seconds=self.interval_seconds,
            short_period=self.short_period,
            long_period=self.long_period,
            order_quantity=self.order_quantity, sizing_mode=self.sizing_mode, order_amount=self.order_amount,
            strategy_basis_amount=strategy_basis,
            strategy_profit=strategy_profit,
            strategy_return_percent=(strategy_profit / strategy_basis * 100 if strategy_basis else None),
            data_source="TOSS" if self.price_feed else "SIMULATED",
            last_data_at=self.last_data_at, data_message=self.data_message,
            realized_profit=self._realized_profit, trading_costs=self._trading_costs,
            completed_trades=self._completed_trades, winning_trades=self._winning_trades,
            max_drawdown_percent=self._max_drawdown, run_id=self.broker.run_id,
            snapshots=list(self._snapshots.values()),
            recent_signals=list(reversed(self._signals[-20:])),
        )

    def _update_drawdown(self) -> None:
        profit = self.broker.strategy_value() + self._strategy_cash_flow - self._strategy_baseline_value - self.broker._management_external_flow
        self._peak_profit = max(self._peak_profit, profit)
        basis = self._strategy_baseline_value + self._strategy_buy_amount + self.broker._management_external_flow
        if basis + self._peak_profit > 0:
            self._max_drawdown = max(self._max_drawdown, (self._peak_profit - profit) / (basis + self._peak_profit) * 100)
