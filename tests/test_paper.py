import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import uuid4
from unittest.mock import patch
from zoneinfo import ZoneInfo

from psycopg import sql
from auto_trader import database, paper, risk
from auto_trader.models import OrderRequest, RiskPreset, RiskSettingsUpdate, Stock
from auto_trader.simulator import MarketSimulator
from auto_trader.strategy import MovingAverageEngine


class PaperMemoryTests(unittest.TestCase):
    def setUp(self):
        self.schema = "test_paper_" + uuid4().hex
        self.original_connect = database.connect
        with self.original_connect() as conn:
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))

        def isolated():
            conn = self.original_connect()
            conn.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(self.schema)))
            return conn

        self.patchers = [patch.object(module, "connect", isolated) for module in (database, paper, risk)]
        for patcher in self.patchers:
            patcher.start()
        self.market = MarketSimulator()
        self.broker = paper.PaperBroker(self.market, Decimal("100000"))
        self.broker.initialize()
        self.risk = risk.RiskManager(self.market)
        self.broker.set_risk_manager(self.risk)

    def tearDown(self):
        for patcher in self.patchers:
            patcher.stop()
        with self.original_connect() as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)))

    def buy(self, key="first"):
        return self.broker.submit(OrderRequest(symbol="005930", side="BUY", quantity=1, request_id=key))

    def relax_risk(self):
        self.risk.update(RiskSettingsUpdate(
            preset=RiskPreset.CUSTOM, max_order_amount=Decimal("1000000"),
            max_symbol_amount=Decimal("1000000"), max_total_investment=Decimal("1000000"),
            min_cash_ratio=Decimal("0"), daily_loss_limit=Decimal("1000000"),
            daily_order_limit=100, profit_target=Decimal("1000000")))

    def test_new_broker_starts_with_fresh_cash(self):
        self.buy()
        self.assertEqual(self.broker.account().cash, Decimal("30000"))
        restarted = paper.PaperBroker(self.market, Decimal("100000"))
        restarted.initialize()
        self.assertEqual(restarted.account().cash, Decimal("100000"))
        self.assertEqual(restarted.orders(), [])
        self.assertEqual(restarted.account().positions, [])

    def test_duplicate_request_and_conflict(self):
        first = self.buy()
        self.assertEqual(self.buy().id, first.id)
        self.assertEqual(len(self.broker.orders()), 1)
        with self.assertRaises(ValueError):
            self.broker.submit(OrderRequest(symbol="005930", side="BUY", quantity=2, request_id="first"))

    def test_insufficient_cash_rejection(self):
        self.buy()
        second = self.buy("second")
        self.assertEqual(second.status.value, "REJECTED")
        self.assertEqual(self.broker.account().cash, Decimal("30000"))

    def test_concurrent_orders_are_safe(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            orders = list(pool.map(self.buy, ["a", "b"]))
        self.assertEqual(sorted(order.status.value for order in orders), ["FILLED", "REJECTED"])
        self.assertEqual(self.broker.account().cash, Decimal("30000"))

    def test_costs_and_partial_sale(self):
        broker = paper.PaperBroker(self.market, Decimal("1000000"), "cost-test",
                                   fee_rate=Decimal("0.001"), sell_tax_rate=Decimal("0.002"))
        broker.initialize()
        broker.submit(OrderRequest(symbol="005930", side="BUY", quantity=2))
        self.market._prices["005930"] = Decimal("80000")
        broker.submit(OrderRequest(symbol="005930", side="SELL", quantity=1))
        account = broker.account()
        self.assertEqual(account.total_fees, Decimal("220"))
        self.assertEqual(account.total_taxes, Decimal("160"))
        self.assertEqual(account.realized_profit, Decimal("9690"))
        self.assertEqual(account.unrealized_profit, Decimal("9930"))

    def test_fee_can_reject_buy(self):
        broker = paper.PaperBroker(self.market, Decimal("70000"), "fee-reject", fee_rate=Decimal("0.001"))
        broker.initialize()
        order = broker.submit(OrderRequest(symbol="005930", side="BUY", quantity=1))
        self.assertEqual(order.status.value, "REJECTED")
        self.assertEqual(broker.account().total_fees, 0)

    def test_risk_preset_and_buy_block(self):
        self.assertEqual(self.risk.status().settings.preset, RiskPreset.DEFAULT)
        self.risk.update(RiskSettingsUpdate(
            preset=RiskPreset.CUSTOM, max_order_amount=Decimal("1"), max_symbol_amount=Decimal("1"),
            max_total_investment=Decimal("1"), min_cash_ratio=Decimal("0"),
            daily_loss_limit=Decimal("100000"), daily_order_limit=10, profit_target=Decimal("100000")))
        order = self.buy()
        self.assertEqual(order.status.value, "REJECTED")
        self.assertIn("위험 한도", order.message)

    def test_temporary_paper_exceptions_do_not_disable_other_limits(self):
        broker = paper.PaperBroker(
            self.market, Decimal("300000"), "paper-exception-test",
            ignore_min_cash_ratio=True, ignore_daily_order_limit=True,
        )
        broker.initialize()
        manager = risk.RiskManager(self.market)
        broker.set_risk_manager(manager)
        manager.update(RiskSettingsUpdate(
            preset=RiskPreset.CUSTOM, max_order_amount=Decimal("1000000"),
            max_symbol_amount=Decimal("1000000"), max_total_investment=Decimal("1000000"),
            min_cash_ratio=Decimal("50"), daily_loss_limit=Decimal("1000000"),
            daily_order_limit=1, profit_target=Decimal("1000000")))
        first = broker.submit(OrderRequest(symbol="005930", side="BUY", quantity=1))
        second = broker.submit(OrderRequest(symbol="005930", side="BUY", quantity=1))
        self.assertEqual(first.status.value, "FILLED")
        self.assertEqual(second.status.value, "FILLED")
        manager.update(RiskSettingsUpdate(
            preset=RiskPreset.CUSTOM, max_order_amount=Decimal("1"),
            max_symbol_amount=Decimal("1000000"), max_total_investment=Decimal("1000000"),
            min_cash_ratio=Decimal("50"), daily_loss_limit=Decimal("1000000"),
            daily_order_limit=100, profit_target=Decimal("1000000")))
        rejected = broker.submit(OrderRequest(symbol="005930", side="BUY", quantity=1))
        self.assertEqual(rejected.status.value, "REJECTED")
        self.assertIn("1회 최대 주문 금액", rejected.message)

    def test_risk_blocks_buy_but_allows_sell(self):
        self.buy()
        self.risk.update(RiskSettingsUpdate(
            preset=RiskPreset.CUSTOM, max_order_amount=Decimal("1"), max_symbol_amount=Decimal("1"),
            max_total_investment=Decimal("1"), min_cash_ratio=Decimal("100"),
            daily_loss_limit=Decimal("1"), daily_order_limit=1, profit_target=Decimal("1")))
        rejected = self.broker.submit(OrderRequest(symbol="005930", side="BUY", quantity=1))
        sold = self.broker.submit(OrderRequest(symbol="005930", side="SELL", quantity=1))
        self.assertEqual(rejected.status.value, "REJECTED")
        self.assertEqual(sold.status.value, "FILLED")

    def test_reset_preserves_risk_settings(self):
        self.buy()
        before = self.risk.settings()
        account = self.broker.reset_practice()
        self.assertEqual(account.cash, Decimal("100000"))
        self.assertEqual(account.positions, [])
        self.assertEqual(self.broker.orders(), [])
        self.assertEqual(self.risk.status().daily_orders, 0)
        self.assertEqual(self.risk.settings(), before)

    def test_live_snapshot_seeds_independent_paper_baseline(self):
        self.market.upsert_stock(Stock(symbol="487240", name="KODEX AI전력핵심설비"), Decimal("36000"))
        account = self.broker.load_snapshot(
            cash=Decimal("50000"),
            positions=[{"symbol": "487240", "quantity": 2, "average_price": Decimal("35000"),
                        "current_price": Decimal("36000")}],
        )
        self.assertEqual(account.cash, Decimal("50000"))
        self.assertEqual(account.initial_cash, Decimal("122000"))
        self.assertEqual(account.positions[0].quantity, 2)
        self.market._prices["487240"] = Decimal("37000")
        self.assertEqual(self.broker.account().total_profit, Decimal("2000"))
        reset = self.broker.reset_practice()
        self.assertEqual(reset.positions[0].quantity, 2)
        self.assertEqual(reset.total_profit, Decimal("2000"))

    def test_strategy_configuration(self):
        engine = MovingAverageEngine(self.market, self.broker)
        engine.configure(interval_seconds=3, short_period=7, long_period=30, order_quantity=2)
        status = engine.status()
        self.assertEqual((status.interval_seconds, status.short_period, status.long_period, status.order_quantity),
                         (3, 7, 30, 2))
        with self.assertRaises(ValueError):
            engine.configure(interval_seconds=2, short_period=20, long_period=5, order_quantity=1)

    def test_strategy_can_be_limited_to_selected_symbol(self):
        engine = MovingAverageEngine(self.market, self.broker)
        engine.configure(interval_seconds=2, short_period=2, long_period=3, order_quantity=1,
                         target_symbol="005930")
        engine.step()
        self.assertEqual([item.symbol for item in engine.status().snapshots], ["005930"])

    def test_strategy_can_watch_multiple_selected_symbols(self):
        engine = MovingAverageEngine(self.market, self.broker)
        engine.configure(interval_seconds=2, short_period=2, long_period=3, order_quantity=1,
                         target_symbols=["005930", "000660"])
        engine.step()
        self.assertEqual(
            {item.symbol for item in engine.status().snapshots}, {"005930", "000660"},
        )

    def test_strategy_return_excludes_stocks_outside_selected_targets(self):
        self.broker.load_snapshot(cash=Decimal("100000"), positions=[
            {"symbol": "005930", "quantity": 1, "average_price": Decimal("60000"),
             "current_price": Decimal("70000")},
            {"symbol": "005380", "quantity": 1, "average_price": Decimal("200000"),
             "current_price": Decimal("240000")},
        ])
        engine = MovingAverageEngine(self.market, self.broker)
        engine.configure(interval_seconds=2, short_period=2, long_period=3, order_quantity=1,
                         target_symbol="005930")

        self.market._prices["005380"] = Decimal("200000")
        self.assertEqual(engine.status().strategy_profit, Decimal("0"))
        self.market._prices["005930"] = Decimal("71000")
        status = engine.status()
        self.assertEqual(status.strategy_profit, Decimal("1000"))
        self.assertEqual(status.strategy_return_percent, Decimal("1000") / Decimal("70000") * 100)

    def test_strategy_applies_take_profit_and_stop_loss(self):
        now = [datetime(2026, 9, 30, 10, 0, tzinfo=ZoneInfo("Asia/Seoul"))]
        self.broker.load_snapshot(cash=Decimal("100000"), positions=[{
            "symbol": "005930", "quantity": 1, "average_price": Decimal("70000"),
            "current_price": Decimal("70000"),
        }])
        engine = MovingAverageEngine(self.market, self.broker, clock=lambda: now[0])
        engine.configure(
            interval_seconds=2, short_period=2, long_period=3, order_quantity=1,
            target_symbol="005930", take_profit_rate=Decimal("5"),
            stop_loss_rate=Decimal("3"), trading_start="00:00", trading_end="23:59",
            cooldown_minutes=0,
        )
        self.market._prices["005930"] = Decimal("80000")
        engine.step()
        self.assertEqual(self.broker.holding_quantity("005930"), 0)
        self.assertIn("익절률", engine.status().recent_signals[0].reason)

        self.broker.load_snapshot(cash=Decimal("100000"), positions=[{
            "symbol": "005930", "quantity": 1, "average_price": Decimal("70000"),
            "current_price": Decimal("70000"),
        }])
        engine.configure(
            interval_seconds=2, short_period=2, long_period=3, order_quantity=1,
            target_symbol="005930", take_profit_rate=Decimal("5"),
            stop_loss_rate=Decimal("3"), trading_start="00:00", trading_end="23:59",
            cooldown_minutes=0,
        )
        self.market._prices["005930"] = Decimal("60000")
        engine.step()
        self.assertEqual(self.broker.holding_quantity("005930"), 0)
        self.assertIn("손절률", engine.status().recent_signals[0].reason)

    def test_strategy_applies_max_holding_days(self):
        now = [datetime(2026, 9, 30, 10, 0, tzinfo=ZoneInfo("Asia/Seoul"))]
        self.broker.load_snapshot(cash=Decimal("100000"), positions=[{
            "symbol": "005930", "quantity": 1, "average_price": Decimal("70000"),
            "current_price": Decimal("70000"),
        }])
        engine = MovingAverageEngine(self.market, self.broker, clock=lambda: now[0])
        engine.configure(
            interval_seconds=2, short_period=2, long_period=3, order_quantity=1,
            target_symbol="005930", take_profit_rate=Decimal("100"),
            stop_loss_rate=Decimal("100"), max_holding_days=2,
            trading_start="00:00", trading_end="23:59", cooldown_minutes=0,
        )
        now[0] += timedelta(days=2)
        engine.step()
        self.assertEqual(self.broker.holding_quantity("005930"), 0)
        self.assertIn("최대 보유일", engine.status().recent_signals[0].reason)

    def test_strategy_applies_trading_hours_and_symbol_cooldown(self):
        now = [datetime(2026, 9, 30, 8, 59, tzinfo=ZoneInfo("Asia/Seoul"))]
        self.broker.load_snapshot(cash=Decimal("300000"), positions=[])
        self.relax_risk()
        engine = MovingAverageEngine(self.market, self.broker, clock=lambda: now[0])
        engine.configure(
            interval_seconds=2, short_period=2, long_period=3, order_quantity=1,
            target_symbol="005930", trading_start="09:00", trading_end="15:20",
            cooldown_minutes=30,
        )
        engine._buy("005930", Decimal("70000"))
        self.assertEqual(self.broker.holding_quantity("005930"), 0)

        now[0] = now[0].replace(hour=10)
        engine._buy("005930", Decimal("70000"))
        engine._buy("005930", Decimal("70000"))
        self.assertEqual(self.broker.holding_quantity("005930"), 1)
        now[0] += timedelta(minutes=30)
        engine._buy("005930", Decimal("70000"))
        self.assertEqual(self.broker.holding_quantity("005930"), 2)

    def test_rounding_and_zero_capital(self):
        self.relax_risk()
        self.broker.fee_rate = Decimal("0.00015")
        self.buy()
        self.assertEqual(self.broker.account().total_fees, Decimal("10"))
        empty = paper.PaperBroker(self.market, Decimal("0"), "empty")
        empty.initialize()
        self.assertIsNone(empty.account().return_percent)


if __name__ == "__main__":
    unittest.main()
