"""Existing sizing calculations, shared by execution strategies."""

from decimal import Decimal


def buy_quantity(self, symbol: str) -> int:
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
