"""회귀 검증: 외부 주문/DB 없이 실제 시세 입력과 모의 체결을 검사한다."""
import asyncio
import json
import unittest
from datetime import datetime, timedelta
from decimal import Decimal as D
from pathlib import Path
from unittest.mock import Mock, patch
from uuid import uuid4
from zoneinfo import ZoneInfo

from auto_trader.models import LiveStockCandle, OrderRequest, LiveStrategyWrite, RiskSettingsUpdate
from auto_trader.paper import PaperBroker
from auto_trader.paper_feed import PaperPriceFeed
from auto_trader.simulator import MarketSimulator
from auto_trader.strategy import MovingAverageEngine
from auto_trader.toss import TossApiError


class StrategyEngineTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 30, 10, 0, tzinfo=ZoneInfo("Asia/Seoul"))
        self.market = MarketSimulator(symbols=("005930",))
        self.broker = PaperBroker(self.market, fee_rate=D("0.00015"), sell_tax_rate=D("0.002"))
        self.engine = MovingAverageEngine(self.market, self.broker, clock=lambda: self.now)
        self.configure()

    def configure(self, **kwargs):
        self.engine.configure(interval_seconds=10, short_period=2, long_period=3,
                              order_quantity=1, target_symbol="005930", **kwargs)

    def bars(self, prices, end=None):
        end = end or self.now - timedelta(minutes=1)
        return [LiveStockCandle(timestamp=end - timedelta(minutes=len(prices)-i-1),
                               open_price=D(p), high_price=D(p), low_price=D(p),
                               close_price=D(p), volume=D(1)) for i, p in enumerate(prices)]

    def test_amount_sizing_includes_fee_slippage_and_cash(self):
        self.market._prices["005930"] = D(10000)
        self.configure(sizing_mode="AMOUNT", order_amount=D(300000))
        self.assertEqual(self.engine._buy_quantity("005930"), 29)
        self.broker.slippage_rate = D("0.01")
        self.assertEqual(self.engine._buy_quantity("005930"), 29)
        self.broker._cash = D(25000)
        self.assertEqual(self.engine._buy_quantity("005930"), 2)
        self.broker._cash = D(9999)
        self.assertFalse(self.engine._buy("005930", D(10000)))
        self.assertEqual(len(self.broker.orders()), 0)

    def test_amount_sizing_respects_risk_limit_and_sells_all(self):
        self.market._prices["005930"] = D(10000)
        self.configure(sizing_mode="AMOUNT", order_amount=D(300000))
        self.broker.risk_manager = Mock()
        self.broker.risk_manager.check_buy.side_effect = lambda **kw: "limit" if kw["amount"] + kw["fee"] > D(85000) else None
        self.assertTrue(self.engine._buy("005930", D(10000)))
        self.assertEqual(self.broker.strategy_quantity("005930"), 8)
        self.engine._sell("005930", "exit")
        self.assertEqual(self.broker.strategy_quantity("005930"), 0)

    def test_rate_limit_waits_without_trading_or_stopping(self):
        self.engine.price_feed = Mock()
        self.engine.price_feed.read.side_effect = TossApiError("limited", status_code=429)
        self.engine.running = True
        with patch("auto_trader.strategy.logging.getLogger") as logger:
            asyncio.run(self.engine.poll_market())
        self.assertTrue(self.engine.running)
        self.assertEqual(self.engine.tick_count, 0)
        self.assertIn("요청 한도 대기", self.engine.data_message)
        self.assertNotIn("exc_info", logger.return_value.warning.call_args.kwargs)

    def test_stop_loss_bypasses_cooldown_and_exits_all(self):
        self.engine.order_quantity = 10
        self.engine._buy("005930", D(70000))
        self.engine.order_quantity = 1
        self.now += timedelta(minutes=1)
        self.market._prices["005930"] = D(63000)
        reason = self.engine._exit_reason("005930", D(63000))
        self.assertIn("손절", reason)
        self.engine._sell("005930", reason)
        self.assertEqual(self.broker.holding_quantity("005930"), 0)
        self.assertFalse(self.engine._buy("005930", D(63000)))

    def test_residual_smaller_than_order_quantity_is_closed(self):
        self.engine._buy("005930", D(70000))
        self.engine.order_quantity = 10
        self.market._prices["005930"] = D(60000)
        self.assertIsNotNone(self.engine._exit_reason("005930", D(60000)))
        self.engine._sell("005930", "stop")
        self.assertEqual(self.broker.strategy_quantity("005930"), 0)

    def test_manual_buy_does_not_create_strategy_profit(self):
        self.broker.submit(OrderRequest(symbol="005930", side="BUY", quantity=1))
        self.assertEqual(self.engine.status().strategy_profit, 0)
        self.assertIsNone(self.engine.status().strategy_return_percent)

    def test_manual_sale_cannot_consume_strategy_shares(self):
        self.engine._buy("005930", D(70000))
        rejected = self.broker.submit(OrderRequest(symbol="005930", side="SELL", quantity=1))
        self.assertEqual(rejected.status, "REJECTED")
        self.broker.submit(OrderRequest(symbol="005930", side="BUY", quantity=1))
        accepted = self.broker.submit(OrderRequest(symbol="005930", side="SELL", quantity=1))
        self.assertEqual(accepted.status, "FILLED")
        self.assertEqual(self.broker.strategy_quantity("005930"), 1)
        self.engine._sell("005930", "exit")
        self.assertEqual(self.broker.holding_quantity("005930"), 0)

    def test_baseline_uses_selection_value_for_copied_holdings(self):
        self.broker.load_snapshot(cash=D(100000), positions=[{
            "symbol": "005930", "quantity": 10, "average_price": D(90000), "current_price": D(70000)}])
        self.configure()
        self.assertIsNone(self.engine._exit_reason("005930", D(70000)))
        self.assertEqual(self.engine.status().strategy_profit, 0)
        self.assertEqual(self.broker.account().unrealized_profit, 0)
        self.assertEqual(self.broker.account().realized_profit, 0)

    def test_return_denominator_does_not_sum_recycled_capital(self):
        self.broker.fee_rate = self.broker.sell_tax_rate = D(0)
        self.configure(cooldown_minutes=0)
        for _ in range(5):
            self.engine._buy("005930", D(70000))
            self.engine._sell("005930", "exit")
        self.assertEqual(self.engine.status().strategy_basis_amount, D(70000))

    def test_net_exit_uses_fees_tax_and_slippage(self):
        self.broker.slippage_rate = D("0.0005")
        self.engine._buy("005930", D(70000))
        self.engine._sell("005930", "flat")
        orders = self.broker.orders()
        self.assertEqual(self.engine.status().strategy_profit, self.broker.account().total_profit)
        self.assertEqual(self.engine.status().realized_profit, self.broker.account().total_profit)
        self.assertEqual(self.engine.status().trading_costs,
                         sum((o.fee+o.tax+o.slippage for o in orders), D(0)))
        self.assertLess(self.engine.status().strategy_profit, -D(160))

    def test_zero_limit_is_unlimited_positive_limit_only_blocks_buys(self):
        self.configure(cooldown_minutes=0, daily_order_limit=0)
        for _ in range(5):
            self.assertTrue(self.engine._buy("005930", D(70000)))
            self.engine._sell("005930", "exit")
        self.engine.daily_order_limit = 1
        self.assertFalse(self.engine._buy("005930", D(70000)))
        self.broker.ignore_daily_order_limit = True
        self.assertTrue(self.engine._buy("005930", D(70000)))
        self.engine._sell("005930", "exit")
        self.assertEqual(self.broker.strategy_quantity("005930"), 0)

    def test_completed_bars_are_not_counted_twice(self):
        data = {"005930": self.bars([70000, 70010, 70020])}
        self.engine.step(candles=data)
        self.engine.step(candles=data)
        self.assertEqual(self.engine._above_count["005930"], 1)
        self.assertEqual(len(self.engine._history["005930"]), 3)

    def test_data_gap_clears_pending_entry_confirmation(self):
        self.engine.step(candles={"005930": self.bars([70020, 70010, 70000])})
        self.now += timedelta(minutes=1)
        self.engine.step(candles={"005930": self.bars([70000, 70010, 70020])})
        self.assertIn("005930", self.engine._entry_armed)
        self.now += timedelta(minutes=10)
        self.engine.step(candles={"005930": self.bars([70000, 71000, 72000])})
        self.assertNotIn("005930", self.engine._entry_armed)
        self.assertEqual(self.broker.orders(), [])

    def test_journal_failure_leaves_account_unfilled(self):
        with patch.object(self.broker, "_journal", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.engine._buy("005930", D(70000))
        self.assertEqual(self.broker.orders(), [])
        self.assertEqual(self.broker.holding_quantity("005930"), 0)
        self.assertEqual(self.broker.account().total_profit, 0)

    def test_feed_reuses_latest_completed_bars_between_polls(self):
        client = Mock()
        client.current_prices.return_value = {"005930": D(70000)}
        client._domestic_candles.return_value = self.bars([70000, 70010, 70020])
        feed = PaperPriceFeed(client)
        feed.read(["005930"], [], 3, self.now)
        feed.read(["005930"], [], 3, self.now+timedelta(seconds=10))
        self.assertEqual(client._domestic_candles.call_count, 1)
        self.assertEqual(client.current_prices.call_count, 2)

    def test_confirmed_cross_is_not_blocked_by_ma_spread_cost_comparison(self):
        def step(prices):
            self.now += timedelta(minutes=1)
            self.market._prices["005930"] = D(prices[-1])
            self.engine.step(candles={"005930": self.bars(prices)})
        step([70020, 70010, 70000])
        step([70000, 70010, 70020])
        step([70010, 70020, 70030])
        self.assertEqual(self.broker.strategy_quantity("005930"), 1)
        self.assertEqual(self.engine.status().snapshots[0].decision, "매수 체결 · 청산 조건 감시")

    def test_waiting_reason_is_exposed_per_symbol(self):
        self.engine.step(candles={"005930": self.bars([70020, 70010, 70000])})
        self.assertEqual(self.engine.status().snapshots[0].decision, "단기선 열세 · 상향 교차 대기")

    def test_no_same_step_reentry_after_stop(self):
        self.configure(cooldown_minutes=0)
        self.engine._buy("005930", D(70000))
        self.engine._entry_armed.add("005930")
        self.engine._above_count["005930"] = 5
        self.market._prices["005930"] = D(60000)
        self.engine.step(candles={"005930": self.bars([58000, 59000, 60000])})
        self.assertEqual(self.broker.strategy_quantity("005930"), 0)
        self.assertEqual(len(self.broker.orders()), 2)

    def test_strategy_change_clears_old_data_status(self):
        self.engine.last_data_at = self.now
        self.engine.data_message = "1/10종목 판단 가능 · 이전 전략 대기"
        self.engine.step(candles={"005930": self.bars([70000, 71000, 71234])})
        self.configure()
        self.assertIsNone(self.engine.last_data_at)
        self.assertEqual(self.engine.status().snapshots, [])
        self.assertNotIn("1/10", self.engine.data_message)

    def test_poll_uses_real_prices_and_failure_cannot_generate_random_prices(self):
        feed = Mock()
        feed.read.return_value = ({"005930": D(71234)}, {"005930": self.bars([70000, 71000, 71234])})
        self.engine.price_feed = feed
        asyncio.run(self.engine.poll_market())
        self.assertEqual(self.market.quote("005930").price, D(71234))
        feed.read.side_effect = TossApiError("unavailable")
        with self.assertLogs("auto_trader.strategy", level="WARNING"):
            asyncio.run(self.engine.poll_market())
        self.assertEqual(self.market.quote("005930").price, D(71234))
        self.assertEqual(self.engine.tick_count, 1)

    def test_journal_survives_reset_and_contains_costs_reason_and_owner(self):
        self.broker.journal_path = Path(__file__).resolve().parents[1] / ".paper-history" / ("test-" + uuid4().hex + ".jsonl")
        try:
            self.configure(cooldown_minutes=0)
            self.engine._buy("005930", D(70000))
            self.engine._sell("005930", "stop test")
            self.broker.reset_practice()
            rows = [json.loads(line) for line in self.broker.journal_path.read_text(encoding="utf-8").splitlines()]
            order = rows[-1]
            self.assertEqual(order["reason"], "stop test")
            self.assertEqual(order["source"], "STRATEGY")
            self.assertEqual(D(order["tax"]), D(140))
            self.assertLess(D(order["realized_profit"]), 0)
        finally:
            self.broker.journal_path.unlink(missing_ok=True)

    def test_feed_filters_incomplete_bars_and_rejects_stale_or_missing_prices(self):
        client = Mock()
        client.current_prices.return_value = {"005930": D(70000)}
        client._domestic_candles.return_value = self.bars([70000, 70010, 99999], self.now)
        feed = PaperPriceFeed(client)
        _, bars = feed.read(["005930"], [], 3, self.now)
        self.assertEqual(bars["005930"][-1].close_price, D(70010))
        client._domestic_candles.return_value = self.bars([70000], self.now-timedelta(days=1))
        self.assertEqual(feed.read(["005930"], [], 3, self.now)[1], {})
        self.assertIn("005930", feed.unavailable)
        client.current_prices.return_value = {}
        self.assertEqual(feed.read(["005930"], [], 3, self.now)[1], {})
        self.assertIn("005930", feed.unavailable)

    def test_weekend_and_out_of_hours_cannot_trade(self):
        for now in (self.now.replace(hour=8), self.now+timedelta(days=3)):
            self.now = now
            self.assertFalse(self.engine._buy("005930", D(70000)))

    def test_zero_count_limits_validate(self):
        self.assertEqual(LiveStrategyWrite(name="test", symbol="005930", daily_order_limit=0).daily_order_limit, 0)
        self.assertEqual(RiskSettingsUpdate(preset="CUSTOM", daily_order_limit=0).daily_order_limit, 0)


if __name__ == "__main__":
    unittest.main()
