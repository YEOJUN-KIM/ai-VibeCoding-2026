import unittest
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from uuid import uuid4
from unittest.mock import patch

from psycopg import sql
from auto_trader import database, paper, risk
from auto_trader.models import OrderRequest, RiskPreset, RiskSettingsUpdate
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

    def test_strategy_configuration(self):
        engine = MovingAverageEngine(self.market, self.broker)
        engine.configure(interval_seconds=3, short_period=7, long_period=30, order_quantity=2)
        status = engine.status()
        self.assertEqual((status.interval_seconds, status.short_period, status.long_period, status.order_quantity),
                         (3, 7, 30, 2))
        with self.assertRaises(ValueError):
            engine.configure(interval_seconds=2, short_period=20, long_period=5, order_quantity=1)

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
