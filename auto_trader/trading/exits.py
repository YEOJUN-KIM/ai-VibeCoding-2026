"""Existing exits calculations, shared by execution strategies."""

from datetime import datetime, timedelta
from decimal import Decimal
from . import KST
from ..models import StrategySnapshot
from .strategies.base import ExitSignalContext


def exit_reason(self, symbol: str, price: Decimal) -> str | None:
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


def manage_exits(self, candles):
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
            reason = self.strategy.generate_exit_signal(ExitSignalContext(closes, short, long))
        if reason:
            sold = self._sell(symbol, f"{context.get('strategy_name') or '현재 전략'} · {reason}", lot_id=lot['id'])
            closes = [bar.close_price for bar in candles[symbol]] if candles is not None else []
            short = int(context.get('short_period', self.short_period))
            long = int(context.get('long_period', self.long_period))
            short_average = sum(closes[-short:], Decimal(0)) / short if len(closes) >= short else None
            long_average = sum(closes[-long:], Decimal(0)) / long if len(closes) >= long else None
            snapshot = StrategySnapshot(
                symbol=symbol,
                name=self.market.quote(symbol).name,
                price=price,
                collected_prices=len(closes),
                data_at=candles[symbol][-1].timestamp if closes else None,
                short_average=short_average,
                long_average=long_average,
                trend=(
                    "ABOVE" if short_average is not None and long_average is not None
                    and short_average > long_average else "BELOW"
                ),
                decision=f"{context.get('strategy_name') or '현재 전략'} · {reason}",
            )
            self._record_ml_decision(
                snapshot,
                "SELL_FILLED" if sold else "SELL_BLOCKED",
                metadata={"lot_id": lot["id"], "return_rate": str(rate)},
            )
            exited.add(symbol)
        if symbol not in self.target_symbols:
            self._snapshots[symbol] = StrategySnapshot(symbol=symbol, name=self.market.quote(symbol).name,
                price=price, collected_prices=0, data_at=candles[symbol][-1].timestamp if candles else None,
                decision=f"청산 감시 · {context.get('strategy_name') or '현재 전략'}")
    return exited
