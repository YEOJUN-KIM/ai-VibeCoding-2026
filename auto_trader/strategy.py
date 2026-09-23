"""이동평균 교차 전략을 실행하는 연습용 자동매매 워커."""

import asyncio
from collections import deque
from datetime import datetime
from decimal import Decimal

from .models import (
    OrderRequest,
    OrderSide,
    SignalEvent,
    StrategySnapshot,
    StrategyStatus,
)
from .paper import PaperBroker
from .simulator import MarketSimulator


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
    ) -> None:
        self.market = market
        self.broker = broker
        self.interval_seconds = interval_seconds
        self.short_period = short_period
        self.long_period = long_period
        self.order_quantity = order_quantity
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
        return was_running

    async def _run(self) -> None:
        try:
            while self.running:
                self.step()
                await asyncio.sleep(self.interval_seconds)
        except asyncio.CancelledError:
            raise
        except Exception:
            raise
        finally:
            self.running = False

    def step(self) -> None:
        self.tick_count += 1
        for quote in self.market.quotes(move=True):
            history = self._history[quote.symbol]
            history.append(quote.price)
            stock = next(item for item in self.market.stocks() if item.symbol == quote.symbol)

            if len(history) < self.long_period:
                self._snapshots[quote.symbol] = StrategySnapshot(
                    symbol=quote.symbol,
                    name=stock.name,
                    price=quote.price,
                    collected_prices=len(history),
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
                short_average=short_average,
                long_average=long_average,
                trend=trend,
            )

            if previous_trend == "BELOW" and trend == "ABOVE":
                self._buy(quote.symbol, quote.price)
            elif previous_trend == "ABOVE" and trend == "BELOW":
                self._sell(quote.symbol)

            self._previous_trend[quote.symbol] = trend

    def _buy(self, symbol: str, price: Decimal) -> None:
        self._record_signal(symbol, OrderSide.BUY, "단기 이동평균이 장기 이동평균을 상향 돌파")
        self.broker.submit(OrderRequest(symbol=symbol, side=OrderSide.BUY, quantity=self.order_quantity))

    def _sell(self, symbol: str) -> None:
        self._record_signal(symbol, OrderSide.SELL, "단기 이동평균이 장기 이동평균을 하향 돌파")
        if self.broker.holding_quantity(symbol) < self.order_quantity:
            return

        self.broker.submit(OrderRequest(symbol=symbol, side=OrderSide.SELL, quantity=self.order_quantity))

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

    def configure(self, *, interval_seconds: int, short_period: int, long_period: int,
                  order_quantity: int) -> None:
        if self.running:
            raise ValueError("자동매매를 중지한 뒤 전략을 변경하세요.")
        if short_period >= long_period:
            raise ValueError("단기 이동평균은 장기 이동평균보다 작아야 합니다.")
        self.interval_seconds = interval_seconds
        self.short_period = short_period
        self.long_period = long_period
        self.order_quantity = order_quantity
        self._history = {stock.symbol: deque(maxlen=long_period) for stock in self.market.stocks()}
        self._previous_trend.clear()
        self._snapshots.clear()
        self.tick_count = 0

    def status(self) -> StrategyStatus:
        return StrategyStatus(
            running=self.running,
            emergency_stopped=self.emergency_stopped,
            tick_count=self.tick_count,
            interval_seconds=self.interval_seconds,
            short_period=self.short_period,
            long_period=self.long_period,
            order_quantity=self.order_quantity,
            snapshots=list(self._snapshots.values()),
            recent_signals=list(reversed(self._signals[-20:])),
        )
