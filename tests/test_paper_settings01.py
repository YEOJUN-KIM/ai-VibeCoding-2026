"""PAPER-SETTINGS-01: one auto-trading settings owner; manual PAPER orders are not limited by it.

Isolated like test_paper_mvp.py (throwaway schema in a TEST database, synthetic market, no network).
"""
import asyncio
import unittest
from decimal import Decimal

from auto_trader.models import OrderRequest, RiskPreset, RiskSettingsUpdate
from auto_trader.paper_mvp import MvpError
from tests.test_paper_mvp import MvpBase, run

MANUAL_SYMBOL = "005930"


class SettingsOwnershipTests(MvpBase):
    def price(self):
        return self.market.quote(MANUAL_SYMBOL).price

    def manual_buy(self, quantity):
        return self.broker.submit(OrderRequest(symbol=MANUAL_SYMBOL, side="BUY", quantity=quantity))

    def small_budget(self, **changes):
        return self.balanced(profile="직접 설정", budget=1000000, per_unit="amount", per_value=Decimal(500000), **changes)

    def auto_orders(self):
        return [o for o in self.broker.orders() if o.source == "STRATEGY" and o.status.value == "FILLED"]

    # 1. manual orders ignore the auto budget / per-symbol cap
    def test_manual_order_ignores_auto_limits_while_auto_settings_are_active(self):
        self.mvp.apply(self.small_budget())
        quantity = int(Decimal(3000000) / self.price())          # far above the 500,000 per-symbol auto cap
        order = self.manual_buy(quantity)
        self.assertEqual(order.status.value, "FILLED", order.message)

    # 1b. the basic order checks stay for manual orders
    def test_manual_order_still_needs_cash_and_holdings(self):
        too_many = int(self.CASH / self.price()) + 10
        self.assertEqual(self.manual_buy(too_many).status.value, "REJECTED")
        sell = self.broker.submit(OrderRequest(symbol=MANUAL_SYMBOL, side="SELL", quantity=1))
        self.assertEqual(sell.status.value, "REJECTED")
        self.assertEqual(self.broker.cash_balance(), self.CASH)

    # 2/3/4. auto budget counts only auto-owned holdings; manual holdings are never auto-sold
    def test_manual_holdings_do_not_use_the_auto_budget_and_are_never_auto_sold(self):
        quantity = int(Decimal(2000000) / self.price())           # manual holding worth more than the whole auto budget
        self.assertEqual(self.manual_buy(quantity).status.value, "FILLED")
        self.mvp.apply(self.small_budget())
        self.engine.running = True
        self.poll(45)
        auto = self.auto_orders()
        self.assertTrue([o for o in auto if o.side.value == "BUY"], "auto buys must still happen within the auto budget")
        self.assertFalse([o for o in auto if o.symbol == MANUAL_SYMBOL], "auto trading must not touch the manual symbol")
        self.assertEqual(self.broker.holding_quantity(MANUAL_SYMBOL), quantity)
        self.assertEqual(self.broker.strategy_quantity(MANUAL_SYMBOL), 0)
        for lot in self.broker.managed_lots():
            self.assertNotEqual(lot["symbol"], MANUAL_SYMBOL)
        self.assertLessEqual(self.broker.strategy_value(), Decimal(1000000) + Decimal(30000))

    # 4. cash is shared: manual spending immediately limits what auto can buy
    def test_manual_cash_use_limits_auto_buying(self):
        quantity = int((self.CASH - Decimal(60000)) / self.price() / Decimal("1.01"))
        self.assertEqual(self.manual_buy(quantity).status.value, "FILLED")
        cash_after_manual = self.broker.cash_balance()
        self.mvp.apply(self.small_budget())
        self.engine.running = True
        self.poll(45)
        self.assertGreaterEqual(self.broker.cash_balance(), 0)
        spent = sum(o.price * o.quantity + o.fee for o in self.auto_orders() if o.side.value == "BUY")
        self.assertLessEqual(spent, cash_after_manual + Decimal(1))

    # 5. a legacy risk update cannot overwrite the limits of a running MVP
    def test_legacy_risk_update_cannot_override_running_auto_limits(self):
        self.mvp.apply(self.small_budget())
        self.engine.running = True
        self.broker.risk_manager.update(RiskSettingsUpdate(preset=RiskPreset.DEFAULT))   # what a stale client's PUT does
        blocked = self.broker.risk_manager.check_buy(symbol="005930", amount=Decimal(900000), fee=Decimal(0))
        self.assertIsNotNone(blocked, "auto per-symbol cap (500,000) must still hold")

    def test_mvp_never_writes_the_persistent_risk_settings(self):
        self.broker.risk_manager.update(RiskSettingsUpdate(preset=RiskPreset.CONSERVATIVE))
        before = self.broker.risk_manager.settings()
        self.mvp.apply(self.small_budget())
        self.assertEqual(self.broker.risk_manager.settings(), before)

    # 8. manual losses are not counted against the auto daily loss limit
    def test_manual_loss_does_not_trip_the_auto_daily_loss_limit(self):
        quantity = int(Decimal(3000000) / self.price())
        self.manual_buy(quantity)
        stock = next(s for s in self.market.stocks() if s.symbol == MANUAL_SYMBOL)
        self.market.upsert_stock(stock, self.price() * Decimal("0.7"))                   # large unrealised manual loss
        self.mvp.apply(self.small_budget(daily_loss_pct=Decimal("0.1")))
        self.assertTrue(self.mvp.status()["risk"]["new_buys_allowed"], self.mvp.status()["risk"]["block_reason"])

    # 7. save / start / stop at the same time never leave mixed shared state
    def test_concurrent_save_start_stop_leave_consistent_state(self):
        legacy = dict(take=self.engine.take_profit_rate, stop=self.engine.stop_loss_rate, name=self.engine.strategy_name)

        async def scenario():
            async def save():
                async with self.mvp.lock:
                    self.mvp.apply(self.small_budget(), activate=False)

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

    # 6. the old strategy-settings endpoint is disabled (cannot overwrite the shared engine)
    def test_legacy_strategy_settings_endpoint_is_disabled(self):
        from fastapi import HTTPException
        from auto_trader.api import paper as paper_api
        from auto_trader.models import StrategySettingsUpdate
        payload = StrategySettingsUpdate(interval_seconds=60, short_period=5, long_period=20, order_quantity=1)
        with self.assertRaises(HTTPException) as caught:
            paper_api.strategy_settings(payload, None)
        self.assertEqual(caught.exception.status_code, 409)

    # restart: limits live only in memory, so nothing can be left behind
    def test_no_leftover_limits_after_restart(self):
        from auto_trader import risk
        self.mvp.apply(self.small_budget())
        reborn = risk.RiskManager(self.market)
        self.assertIsNone(getattr(reborn, "auto_limits", None))

    # --- Codex review: legacy start runs under the same limits; the daily baseline survives a restart -------------
    def test_legacy_strategy_start_runs_under_the_auto_limits_and_releases_them(self):
        self.engine.candidate_provider = None                      # a legacy (non-MVP) strategy run
        self.mvp.apply(self.small_budget(), activate=False)

        async def scenario():
            self.mvp.limit_legacy_run()
            await self.engine.start()
            blocked = self.broker.risk_manager.check_buy(symbol=MANUAL_SYMBOL, amount=Decimal(900000), fee=Decimal(0))
            with_limits = self.broker.risk_manager.auto_limits is not None
            await self.engine.stop()
            return blocked, with_limits
        blocked, with_limits = run(scenario())
        self.assertTrue(with_limits)
        self.assertIsNotNone(blocked)                               # 900,000 > the 500,000 auto cap, although saved risk allows 1,000,000/order
        self.assertIsNone(self.broker.risk_manager.auto_limits)     # released when that run ended

    def test_legacy_start_refused_while_an_auto_run_is_active(self):
        self.engine.running = True
        with self.assertRaises(MvpError):
            self.mvp.limit_legacy_run()

    def test_daily_loss_baseline_survives_restart(self):
        from auto_trader import paper, risk
        self.mvp.apply(self.small_budget(daily_loss_pct=Decimal("0.5")))        # 50,000 KRW
        self.engine.running = True
        self.poll(45)
        self.engine.running = False
        quantity = int(Decimal(200000) / self.price())
        self.broker.begin_strategy([])                                          # make sure lots are managed
        rm = self.broker.risk_manager
        rm.auto_profit()                                                        # day baseline is taken and persisted
        reloaded = paper.PaperBroker(self.market, self.CASH, account_name="mvp-test")
        reloaded.initialize()
        reloaded.begin_strategy([])
        reborn = risk.RiskManager(self.market)
        reloaded.set_risk_manager(reborn)
        self.assertEqual(reloaded.mvp_state()["auto_open"], self.broker.mvp_state()["auto_open"])
        self.assertEqual(reborn.auto_profit(), rm.auto_profit())               # not re-baselined to 0 after restart

    def test_unestablishable_baseline_holds_new_buys(self):
        from unittest.mock import patch
        self.mvp.apply(self.small_budget())
        rm = self.broker.risk_manager
        with patch.object(self.broker, "update_mvp", side_effect=RuntimeError("db down")):
            status = rm.auto_status(rm.auto_limits)
            reason = rm.check_buy(symbol=MANUAL_SYMBOL, amount=Decimal(1000), fee=Decimal(0))
        self.assertFalse(status["new_buys_allowed"])
        self.assertIn("보류", reason)


if __name__ == "__main__":
    unittest.main()
