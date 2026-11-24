"""PAPER-SIMPLIFY-01: the automatic operating amount is the only user-set limit; manual PAPER orders have no
investment-policy limit (only balance / quantity / validity / duplicate checks).

Isolated like test_paper_mvp.py (throwaway schema in a TEST database on a non-default loopback port, synthetic market, no network).
"""
import unittest
from decimal import Decimal

from auto_trader import paper, risk
from auto_trader.models import OrderRequest, RiskPreset, RiskSettingsUpdate
from auto_trader.paper_mvp import MvpError
from auto_trader.risk import AutoLimits
from tests.test_paper_mvp import MvpBase, run

MANUAL_SYMBOL = "005930"


class SimplifyTests(MvpBase):
    def price(self):
        return self.market.quote(MANUAL_SYMBOL).price

    def manual_buy(self, quantity, **kw):
        return self.broker.submit(OrderRequest(symbol=MANUAL_SYMBOL, side="BUY", quantity=quantity, **kw))

    def budget(self, amount=1000000, **changes):
        return self.balanced(budget=amount, **changes)

    def auto_orders(self):
        return [o for o in self.broker.orders() if o.source == "STRATEGY" and o.status.value == "FILLED"]

    # ---- manual orders: no investment-policy limit, basic checks stay
    def test_manual_buy_over_thirty_percent_weight_is_allowed_but_over_balance_is_rejected(self):
        quantity = int(Decimal(6000000) / self.price())             # ~60% of the 10,000,000 account in one symbol
        self.assertEqual(self.manual_buy(quantity).status.value, "FILLED")
        self.assertGreater(self.broker.position_value(MANUAL_SYMBOL) / self.broker.account().total_asset, Decimal("0.3"))
        rejected = self.manual_buy(int(self.broker.cash_balance() / self.price()) + 5)   # more than the remaining cash
        self.assertEqual(rejected.status.value, "REJECTED")
        self.assertIn("주문 가능 금액", rejected.message)
        self.assertEqual(self.broker.holding_quantity(MANUAL_SYMBOL), quantity)

    def test_manual_orders_ignore_even_a_restrictive_persisted_risk_setting(self):
        self.broker.risk_manager.update(RiskSettingsUpdate(preset=RiskPreset.CONSERVATIVE))   # 500,000/order, 50% min cash ...
        quantity = int(Decimal(4000000) / self.price())
        self.assertEqual(self.manual_buy(quantity).status.value, "FILLED")
        self.assertEqual(self.broker.submit(OrderRequest(symbol=MANUAL_SYMBOL, side="SELL", quantity=quantity)).status.value, "FILLED")

    def test_manual_order_ignores_auto_operating_amount_while_auto_settings_are_active(self):
        self.mvp.apply(self.budget())
        self.assertEqual(self.manual_buy(int(Decimal(3000000) / self.price())).status.value, "FILLED")   # 3x the auto amount

    def test_manual_sell_over_holdings_and_duplicate_request_id(self):
        first = self.manual_buy(3, request_id="dup-1")
        again = self.manual_buy(3, request_id="dup-1")
        self.assertEqual(first.id, again.id)
        self.assertEqual(self.broker.holding_quantity(MANUAL_SYMBOL), 3)       # not bought twice
        self.assertEqual(self.broker.submit(OrderRequest(symbol=MANUAL_SYMBOL, side="SELL", quantity=4)).status.value, "REJECTED")

    def test_manual_order_still_needs_cash_and_holdings(self):
        self.assertEqual(self.manual_buy(int(self.CASH / self.price()) + 10).status.value, "REJECTED")
        self.assertEqual(self.broker.submit(OrderRequest(symbol=MANUAL_SYMBOL, side="SELL", quantity=1)).status.value, "REJECTED")
        self.assertEqual(self.broker.cash_balance(), self.CASH)

    # ---- automatic orders: the operating amount only
    def test_auto_buy_over_the_operating_amount_is_rejected_and_inside_is_allowed(self):
        self.mvp.apply(self.budget())                             # 1,000,000
        rm = self.broker.risk_manager
        self.assertIsNone(rm.check_buy(symbol="100001", amount=Decimal(990000), fee=Decimal(1000)))     # one order may use all of it
        self.assertIsNotNone(rm.check_buy(symbol="100001", amount=Decimal(1000000), fee=Decimal(150)))
        order = self.broker.submit(OrderRequest(symbol=MANUAL_SYMBOL, side="BUY", quantity=1_000_000 // int(self.price()) + 1), source="STRATEGY")
        self.assertEqual(order.status.value, "REJECTED")
        self.assertIn("운용 금액", order.message)

    def test_old_per_symbol_holdings_daily_buy_and_loss_limits_are_not_applied(self):
        self.assertEqual(set(AutoLimits.__dataclass_fields__), {"max_total_investment"})
        self.mvp.apply(self.budget())
        rm = self.broker.risk_manager
        for symbol in ("100001", "100002", "100003", "100004", "100005", "100006", "100007"):   # no holdings / per-symbol / per-order cap
            self.assertIsNone(rm.check_buy(symbol=symbol, amount=Decimal(100000), fee=Decimal(0)))
        self.assertEqual(set(rm.auto_status(rm.auto_limits)), {"new_buys_allowed", "block_reason", "invested_amount", "max_total_investment"})

    def test_persisted_risk_settings_never_block_an_automatic_buy(self):
        rm = self.broker.risk_manager
        rm.update(RiskSettingsUpdate(preset=RiskPreset.CUSTOM, max_order_amount=Decimal(1), max_symbol_amount=Decimal(1),
                                     max_total_investment=Decimal(1), min_cash_ratio=Decimal(99), daily_loss_limit=Decimal(1),
                                     daily_order_limit=1, profit_target=Decimal(1)))
        self.mvp.apply(self.budget())
        self.assertIsNone(rm.check_buy(symbol="100001", amount=Decimal(500000), fee=Decimal(0)))

    def test_buy_without_an_operating_amount_is_refused(self):
        rm = self.broker.risk_manager
        self.assertIsNone(rm.auto_limits)
        self.assertIn("운용 금액", rm.check_buy(symbol="100001", amount=Decimal(1000), fee=Decimal(0)))

    def test_auto_budget_counts_carried_holdings_after_restart(self):
        self.mvp.apply(self.budget())
        self.engine.running = True
        for _ in range(60):                                       # stop while the automatic lot is still open
            self.poll()
            if self.broker.risk_value() > 0:
                break
        self.engine.running = False
        self.mvp._release()
        held = self.broker.risk_value()
        self.assertGreater(held, 0, "the synthetic run must have bought something")
        reloaded = paper.PaperBroker(self.market, self.CASH, account_name="mvp-test")
        reloaded.initialize()
        reborn = risk.RiskManager(self.market)
        reloaded.set_risk_manager(reborn)
        reborn.auto_limits = AutoLimits(max_total_investment=Decimal(1000000))
        self.assertEqual(reloaded.risk_value(), held)
        room = Decimal(1000000) - held
        self.assertIsNotNone(reborn.check_buy(symbol="100001", amount=room + Decimal(1000), fee=Decimal(0)))
        self.assertIsNone(reborn.check_buy(symbol="100001", amount=max(Decimal(0), room - Decimal(1000)), fee=Decimal(0)))

    def test_manual_holdings_do_not_use_the_auto_budget_and_are_never_auto_sold(self):
        quantity = int(Decimal(2000000) / self.price())           # manual holding worth more than the whole auto budget
        self.assertEqual(self.manual_buy(quantity).status.value, "FILLED")
        self.mvp.apply(self.budget())
        self.engine.running = True
        self.poll(45)
        auto = self.auto_orders()
        self.assertTrue([o for o in auto if o.side.value == "BUY"], "auto buys must still happen within the auto budget")
        self.assertFalse([o for o in auto if o.symbol == MANUAL_SYMBOL], "auto trading must not touch the manual symbol")
        self.assertEqual(self.broker.holding_quantity(MANUAL_SYMBOL), quantity)
        self.assertEqual(self.broker.strategy_quantity(MANUAL_SYMBOL), 0)
        self.assertLessEqual(self.broker.strategy_value(), Decimal(1000000) + Decimal(30000))

    def test_mixed_manual_and_auto_keep_balances_consistent(self):
        quantity = int(Decimal(7000000) / self.price())
        self.manual_buy(quantity)
        self.mvp.apply(self.budget())
        self.engine.running = True
        for _ in range(45):
            self.poll()
            account = self.broker.account()
            self.assertGreaterEqual(account.cash, 0)
            self.assertEqual(account.total_asset, account.cash + sum((p.market_value for p in account.positions), Decimal(0)))
        self.assertEqual(self.broker.holding_quantity(MANUAL_SYMBOL), quantity)

    def test_manual_cash_use_limits_auto_buying(self):
        quantity = int((self.CASH - Decimal(60000)) / self.price() / Decimal("1.01"))
        self.assertEqual(self.manual_buy(quantity).status.value, "FILLED")
        cash_after_manual = self.broker.cash_balance()
        self.mvp.apply(self.budget())
        self.engine.running = True
        self.poll(45)
        self.assertGreaterEqual(self.broker.cash_balance(), 0)
        spent = sum(o.price * o.quantity + o.fee for o in self.auto_orders() if o.side.value == "BUY")
        self.assertLessEqual(spent, cash_after_manual + Decimal(1))

    # ---- settings storage
    def test_mvp_never_writes_the_persistent_risk_settings(self):
        self.broker.risk_manager.update(RiskSettingsUpdate(preset=RiskPreset.CONSERVATIVE))
        before = self.broker.risk_manager.settings()
        self.mvp.apply(self.budget())
        self.assertEqual(self.broker.risk_manager.settings(), before)

    def test_saved_settings_from_the_earlier_format_still_load_and_are_not_rewritten(self):
        self.broker.update_mvp(settings=dict(budget=2000000, sectors=["FOLIO 추천"], profile="균형형", per_unit="percent", per_value=25,
                                             max_holdings=6, daily_loss_pct=2, daily_buys=10, stop_pct=4, take_pct=8, max_days=12))
        s = self.mvp.settings()
        self.assertEqual(s.budget, 2000000)
        self.assertFalse({"stop_pct", "take_pct", "max_days", "sectors"} & set(s.model_dump()))   # removed inputs are not exposed any more
        self.assertEqual(self.broker.mvp_state()["settings"]["per_unit"], "percent")        # stored data is never destroyed by a load

    def test_budget_is_free_input_not_a_fixed_example(self):
        for amount in (1, 3000000, 10000000):
            self.mvp.apply(self.budget(amount))
            self.assertEqual(self.broker.risk_manager.auto_limits.max_total_investment, Decimal(amount))
            self.engine.running = False
        for bad in (0, 10000001):
            with self.assertRaises(MvpError):
                self.mvp.apply(self.budget(bad))

    # ---- state / control
    def test_concurrent_save_start_stop_leave_consistent_state(self):
        import asyncio
        legacy = dict(take=self.engine.take_profit_rate, stop=self.engine.stop_loss_rate, name=self.engine.strategy_name)

        async def scenario():
            async def save():
                async with self.mvp.lock:
                    self.mvp.apply(self.budget(), activate=False)

            async def start():
                async with self.mvp.lock:
                    await self.mvp.start()

            async def stop():
                async with self.mvp.lock:
                    await self.mvp.stop()
            await asyncio.gather(save(), start(), stop(), save(), start(), stop())
            async with self.mvp.lock:
                await self.mvp.stop()
        run(scenario())
        self.assertFalse(self.engine.running)
        self.assertIsNone(self.broker.risk_manager.auto_limits)
        self.assertEqual(dict(take=self.engine.take_profit_rate, stop=self.engine.stop_loss_rate, name=self.engine.strategy_name), legacy)

    def test_no_leftover_limits_after_restart(self):
        self.mvp.apply(self.budget())
        self.assertIsNone(getattr(risk.RiskManager(self.market), "auto_limits", None))

    def test_legacy_strategy_start_runs_under_the_operating_amount_and_releases_it(self):
        self.engine.candidate_provider = None                      # a legacy (non-MVP) strategy run
        self.mvp.apply(self.budget(), activate=False)

        async def scenario():
            self.mvp.limit_legacy_run()
            await self.engine.start()
            blocked = self.broker.risk_manager.check_buy(symbol=MANUAL_SYMBOL, amount=Decimal(1900000), fee=Decimal(0))
            with_limits = self.broker.risk_manager.auto_limits is not None
            await self.engine.stop()
            return blocked, with_limits
        blocked, with_limits = run(scenario())
        self.assertTrue(with_limits)
        self.assertIsNotNone(blocked)
        self.assertIsNone(self.broker.risk_manager.auto_limits)     # released when that run ended

    def test_legacy_start_refused_while_an_auto_run_is_active(self):
        self.engine.running = True
        with self.assertRaises(MvpError):
            self.mvp.limit_legacy_run()


if __name__ == "__main__":
    unittest.main()
