"""Deterministic PAPER replays used to compare the pre-refactor engine."""

import json
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import patch
from zoneinfo import ZoneInfo

from auto_trader.models import LiveStockCandle
from auto_trader.paper import PaperBroker
from auto_trader.simulator import MarketSimulator


SCENARIOS = {
    "cross_and_exit": {},
    "costs_and_profit": {"take_profit_rate": Decimal("1")},
    "stop_loss": {"stop_loss_rate": Decimal("0.5")},
    "insufficient_cash": {"initial_cash": Decimal("1")},
}


def replay(engine_class, options):
    options = dict(options)
    now = datetime(2026, 9, 30, 10, 0, tzinfo=ZoneInfo("Asia/Seoul"))
    market = MarketSimulator(symbols=("005930",))
    broker = PaperBroker(market, initial_cash=options.pop("initial_cash", Decimal("100000")),
                         fee_rate=Decimal("0.00015"), sell_tax_rate=Decimal("0.002"),
                         slippage_rate=Decimal("0.001"))
    decisions = []
    engine = engine_class(market, broker, clock=lambda: now, decision_recorder=decisions.append)
    engine.configure(interval_seconds=10, short_period=2, long_period=3, order_quantity=2,
                     target_symbol="005930", cooldown_minutes=0, **options)
    broker.run_id = "refactor-regression"
    prices = []
    observations = []
    for value in (103, 102, 101, 102, 104, 106, 108, 110, 108, 105, 102, 99):
        now += timedelta(minutes=1)
        prices.append(Decimal(value))
        market._prices["005930"] = prices[-1]
        bars = [LiveStockCandle(timestamp=now - timedelta(minutes=len(prices)-i),
                               open_price=p, high_price=p, low_price=p, close_price=p, volume=1)
                for i, p in enumerate(prices)]
        with patch("auto_trader.paper.datetime", wraps=datetime) as clock:
            clock.now.return_value = now
            engine.step(candles={"005930": bars})
        observations.append({"status": engine.status().model_dump(mode="json"),
                             "account": broker.account().model_dump(mode="json")})
    orders = [{k: v for k, v in order.model_dump(mode="json").items()
               if k not in {"request_id", "created_at"}} for order in broker.orders()]
    def stable(item):
        if isinstance(item, dict):
            # IDs and model default timestamps are generated independently of
            # the replay clock; preserve actual candle/decision timestamps.
            return {k: stable(v) for k, v in item.items() if k not in {"lot_id", "created_at"}}
        if isinstance(item, list):
            return [stable(v) for v in item]
        return item
    return json.loads(json.dumps(stable({"observations": observations, "orders": orders,
                                        "ml_decisions": decisions}), default=str, ensure_ascii=False))
