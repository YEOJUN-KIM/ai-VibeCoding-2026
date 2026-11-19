"""PAPER-SAFETY-01 regression tests: defects found by reviewing PAPER-MVP-01 (each failed before its fix).

Isolated like test_paper_mvp.py: throwaway schema in a TEST database only, synthetic market, no network.
"""
import asyncio
import unittest
from decimal import Decimal

from auto_trader.models import RiskPreset, RiskSettingsUpdate
from auto_trader.paper_mvp import MvpError, PaperMvp
from auto_trader.trading import KST
from tests.test_paper_mvp import MvpBase, run


class SafetyRegressionTests(MvpBase):
    def legacy_setup(self):
        """A user's own (non-MVP) PAPER strategy and risk configuration on the shared engine/broker."""
        self.broker.risk_manager.update(RiskSettingsUpdate(preset=RiskPreset.CONSERVATIVE))
        self.engine.configure(interval_seconds=1, short_period=3, long_period=7, order_quantity=5, target_symbols=["100001"],
                              take_profit_rate=Decimal(3), stop_loss_rate=Decimal(2), max_holding_days=4, cooldown_minutes=9,
                              daily_order_limit=11, sizing_mode="QUANTITY", strategy_name="legacy-ma")
        self.legacy_feed = object()
        self.engine.price_feed = self.legacy_feed
        self.engine.candidate_provider = None               # the test controller binds on construction; a legacy engine is unbound
        self.legacy_values = self.engine_values()

    def engine_values(self):
        e = self.engine
        return dict(take=e.take_profit_rate, stop=e.stop_loss_rate, qty=e.order_quantity, targets=list(e.target_symbols),
                    name=e.strategy_name, sizing=e.sizing_mode, limit=e.daily_order_limit, cooldown=e.cooldown_minutes,
                    days=e.max_holding_days, short=e.short_period, long=e.long_period)

    def assert_legacy_restored(self):
        self.assertEqual(self.engine_values(), self.legacy_values)
        self.assertIs(self.engine.price_feed, self.legacy_feed)
        self.assertIsNone(self.engine.candidate_provider)
        risk = self.broker.risk_manager.settings()
        self.assertEqual(risk.preset.value, "CONSERVATIVE")
        self.assertEqual(risk.max_total_investment, Decimal(3000000))

    # --- B. shared settings -------------------------------------------------------------------
    def test_saving_settings_does_not_touch_shared_engine_or_risk(self):
        self.legacy_setup()
        self.mvp.apply(self.balanced(), activate=False)
        self.assert_legacy_restored()
        self.assertEqual(self.broker.mvp_state()["settings"]["budget"], 5000000)

    def test_mvp_run_restores_legacy_settings_when_it_ends(self):
        self.legacy_setup()

        async def scenario():
            await self.mvp.start()
            self.assertEqual(self.engine.stop_loss_rate, Decimal(5))       # MVP values are active during the run
            self.assertIsNotNone(self.broker.risk_manager.auto_limits)           # run-scoped, in memory
            self.assertEqual(self.broker.risk_manager.settings().preset.value, "CONSERVATIVE")
            await self.mvp.stop()
            self.assertIsNone(self.broker.risk_manager.auto_limits)
        run(scenario())
        self.assert_legacy_restored()

    def test_emergency_stop_also_restores(self):
        self.legacy_setup()

        async def scenario():
            await self.mvp.start()
            await self.mvp.stop(emergency=True)
        run(scenario())
        self.assert_legacy_restored()

    def test_restart_keeps_cooldown_instead_of_clearing_it(self):
        async def scenario():
            await self.mvp.start()
            self.engine._last_order_at["100001"] = self.clock()
            await self.mvp.stop()
            await self.mvp.start()
            kept = "100001" in self.engine._last_order_at
            await self.mvp.stop()
            return kept
        self.assertTrue(run(scenario()))

    # --- C/D. another strategy running, ownership ---------------------------------------------
    def test_start_refused_while_another_paper_strategy_runs(self):
        self.engine.candidate_provider = None
        self.engine.running = True                          # a legacy all-market strategy is running
        with self.assertRaises(MvpError):
            run(self.mvp.start())
        self.assertIsNone(self.engine.candidate_provider)
        self.assertEqual(self.mvp.status()["run"]["state"], "other")

    def test_normal_stop_does_not_stop_a_foreign_strategy_but_emergency_does(self):
        async def scenario():
            self.engine.candidate_provider = None
            self.engine.running = True
            with self.assertRaises(MvpError):
                await self.mvp.stop()
            still_running = self.engine.running
            await self.mvp.stop(emergency=True)
            return still_running
        self.assertTrue(run(scenario()))
        self.assertFalse(self.engine.running)

    def test_start_refused_when_manual_holdings_would_be_adopted(self):
        self.broker.begin_strategy([], scope="ALL")
        with self.assertRaises(MvpError):
            run(self.mvp.start())
        self.assertFalse(self.engine.running)

    def test_concurrent_start_clicks_start_one_run(self):
        async def scenario():
            results = await asyncio.gather(self.mvp.start(), self.mvp.start(), self.mvp.start())
            await self.mvp.stop()
            return results
        results = run(scenario())
        self.assertEqual(sum(1 for r in results if r), 1)

    # --- A. daily limit counts only buys; exits are never blocked -----------------------------
    def test_daily_buy_limit_counts_buys_only(self):
        self.mvp.apply(self.balanced(daily_buys=3, profile="직접 설정"))
        self.assertFalse(self.engine.count_sells_in_daily_limit)
        self.engine.running = True
        self.poll(45)                                       # full scenario: buys, then stop-out / take-profit sells
        filled = [o for o in self.broker.orders() if o.status.value == "FILLED"]
        buys = [o for o in filled if o.side.value == "BUY"]
        sells = [o for o in filled if o.side.value == "SELL"]
        today = self.clock().astimezone(KST).date()
        self.assertTrue(sells, "exits must happen")
        self.assertEqual(self.engine._daily_orders.get(today, 0), len(buys), "sells must not use up the buy allowance")

    def test_exits_proceed_when_buy_limit_is_reached(self):
        self.mvp.apply(self.balanced(daily_buys=1, profile="직접 설정"))
        self.engine.running = True
        self.poll(45)
        filled = [o for o in self.broker.orders() if o.status.value == "FILLED"]
        buys = [o for o in filled if o.side.value == "BUY"]
        sells = [o for o in filled if o.side.value == "SELL"]
        self.assertEqual(len(buys), 1)
        self.assertTrue(sells, "the single position must still be exited by its own rules")
        self.assertEqual(self.broker.account().positions, [])

    # --- Codex review findings ------------------------------------------------------------------
    def test_superseded_run_task_does_not_clear_the_newer_run(self):
        """Legacy stop sets running=False and awaits the old task; a new start in that window must keep its state."""
        async def scenario():
            await self.mvp.start()
            old = self.engine._task
            self.engine.running = False                 # what stop() does before awaiting the old task
            await self.engine.start()                   # a new run takes over (task B)
            old.cancel()
            try:
                await old
            except asyncio.CancelledError:
                pass
            state = (self.engine.running, self.engine.candidate_provider is not None, self.engine.run_release is not None)
            await self.mvp.stop()
            return state
        self.assertEqual(run(scenario()), (True, True, True))
        self.assertFalse(self.engine.running)

    def test_start_refused_while_previous_run_task_is_unwinding(self):
        async def scenario():
            await self.mvp.start()
            self.engine.running = False                 # stop requested, old task not finished yet
            try:
                await self.mvp.start()
            finally:
                self.engine.running = True
                await self.mvp.stop()
        with self.assertRaises(MvpError):
            run(scenario())

    def test_failed_engine_restore_keeps_snapshot_and_cooldown(self):
        self.legacy_setup()

        async def scenario():
            await self.mvp.start()
            self.engine._last_order_at["100001"] = self.clock()
            real = self.engine.configure
            calls = []

            def flaky(**kw):
                calls.append(1)
                if len(calls) <= 2:          # the run-end release and stop()'s retry both fail
                    raise RuntimeError("db down")
                return real(**kw)
            self.engine.configure = flaky
            await self.mvp.stop()
            kept = (self.mvp._legacy is not None, "100001" in self.engine._last_order_at)
            self.mvp._release()                         # next release succeeds
            return kept
        self.assertEqual(run(scenario()), (True, True))
        self.assertIsNone(self.mvp._legacy)
        self.assert_legacy_restored()


if __name__ == "__main__":
    unittest.main()
