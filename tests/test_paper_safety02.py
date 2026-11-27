"""PAPER-SAFETY-02: restart / recovery / daily-limit hazards left after PAPER-SETTINGS-01.

A  engine parameter restore failure must not leave MVP values live for another run
B  the daily buy limit must survive a restart (derived from the persisted order ledger, KST day)
C  the daily loss baseline must be trustworthy or new automatic buys are held

Isolated like test_paper_mvp.py (throwaway schema in a TEST database, synthetic market, no network).
"""
import asyncio
import unittest
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from auto_trader import paper, risk
from auto_trader.models import OrderRequest
from auto_trader.paper_mvp import MvpError, PaperMvp
from auto_trader.paper_mvp_synthetic import DAY
from auto_trader.strategy import MovingAverageEngine
from tests.test_paper_mvp import MvpBase, run

PROBE = "005930"      # a symbol that exists in the simulated market, used for limit probes


class Safety02Base(MvpBase):
    def setUp(self):
        super().setUp()
        self.broker.clock = self.clock            # order timestamps follow the virtual clock (production: real clock)

    def direct(self, **changes):
        return self.balanced(profile="직접 설정", **changes)

    def reboot(self):
        """A restarted app: new broker/engine/controller reading the same persisted account."""
        broker = paper.PaperBroker(self.market, self.CASH, account_name="mvp-test")
        broker.initialize()
        broker.clock = self.clock
        broker.set_risk_manager(risk.RiskManager(self.market))
        engine = MovingAverageEngine(self.market, broker, interval_seconds=1, clock=self.clock)
        return broker, engine, PaperMvp(broker, engine, source=self.source, clock=self.clock)

    def filled(self, source="STRATEGY", side="BUY", broker=None):
        return [o for o in (broker or self.broker).orders()
                if o.status.value == "FILLED" and o.source == source and o.side.value == side]

    def set_price(self, symbol, factor):
        stock = next(s for s in self.market.stocks() if s.symbol == symbol)
        self.market.upsert_stock(stock, self.market.quote(symbol).price * Decimal(factor))

    def close_checkpoint(self):
        """An engine tick that priced every held symbol (what the engine passes to RiskManager.checkpoint)."""
        self.broker.risk_manager.checkpoint(set(self.broker.strategy_symbols()))

    def hold_auto_lots(self, close=True, **changes):
        """Automatic holdings at the end of day one. With `close` the day ends with the engine's end-of-tick checkpoint
        (a closing evaluation), as a continuously running app would leave it."""
        self.mvp.apply(self.direct(**changes))
        self.engine.running = True
        self.poll(8)                                  # first entries happen near minute 31; exits come later
        self.engine.running = False
        self.assertTrue(self.broker.strategy_symbols(), "expected automatic holdings")
        if close:
            self.clock.now = DAY.replace(hour=15, minute=15)
            self.close_checkpoint()


class EngineRestoreTests(Safety02Base):                                  # --- A
    def legacy_setup(self):
        self.engine.configure(interval_seconds=1, short_period=3, long_period=7, order_quantity=5, target_symbols=["100001"],
                              take_profit_rate=Decimal(3), stop_loss_rate=Decimal(2), max_holding_days=4, cooldown_minutes=9,
                              daily_order_limit=11, sizing_mode="QUANTITY", strategy_name="legacy-ma")
        self.engine.candidate_provider = None
        self.legacy_context = dict(self.broker._strategy_context)
        self.legacy_values = (self.engine.take_profit_rate, self.engine.stop_loss_rate, self.engine.strategy_name,
                              self.engine.sizing_mode, self.engine.daily_order_limit)

    def values(self):
        e = self.engine
        return (e.take_profit_rate, e.stop_loss_rate, e.strategy_name, e.sizing_mode, e.daily_order_limit)

    def test_failed_baseline_write_during_restore_still_restores_engine_and_context(self):
        self.legacy_setup()

        async def scenario():
            await self.mvp.start()
            with patch.object(self.broker, "begin_strategy", side_effect=RuntimeError("db down")):
                await self.mvp.stop()
        run(scenario())
        self.assertEqual(self.values(), self.legacy_values)
        self.assertEqual(self.broker._strategy_context, self.legacy_context,
                         "new lots of a later run must not inherit the MVP's stop/take rules")

    def test_restore_failure_blocks_every_new_automatic_run_until_it_succeeds(self):
        self.legacy_setup()

        async def scenario():
            await self.mvp.start()
            with patch.object(self.engine, "restore_parameters", side_effect=RuntimeError("boom")):
                await self.mvp.stop()
                blocked = self.engine.start_block
                started = await self.engine.start()                      # any other starter
                self.assertFalse(self.engine.running)
                with self.assertRaises(MvpError):
                    self.mvp.limit_legacy_run()                          # the legacy /strategy/start path (retry fails too)
                with self.assertRaises(MvpError):
                    await self.mvp.start()
                self.assertEqual(self.mvp.status()["run"]["state"], "blocked")
                manual = self.broker.submit(OrderRequest(symbol=PROBE, side="BUY", quantity=1))   # manual PAPER stays usable
            await self.mvp.start()                                        # retry: restore succeeds, run starts
            ok = self.engine.running
            await self.mvp.stop()
            return blocked, started, manual.status.value, ok
        blocked, started, manual, ok = run(scenario())
        self.assertTrue(blocked)
        self.assertFalse(started)
        self.assertEqual(manual, "FILLED")
        self.assertTrue(ok)
        self.assertIsNone(self.engine.start_block)
        self.assertEqual(self.values(), self.legacy_values)

    def test_failed_restore_never_substitutes_default_values(self):
        self.legacy_setup()

        async def scenario():
            await self.mvp.start()
            with patch.object(self.engine, "restore_parameters", side_effect=RuntimeError("boom")):
                await self.mvp.stop()
        run(scenario())
        self.assertIsNotNone(self.mvp._legacy, "the snapshot must be kept for the retry")
        self.assertEqual(self.mvp._legacy["engine"]["take_profit_rate"], Decimal(3))

    def test_repeated_start_stop_cycles_keep_legacy_values_and_no_block(self):
        self.legacy_setup()

        async def scenario():
            for _ in range(4):
                await asyncio.gather(self.mvp.start(), self.mvp.start())
                await asyncio.gather(self.mvp.stop(), self.mvp.stop())
        run(scenario())
        self.assertEqual(self.values(), self.legacy_values)
        self.assertEqual(self.broker._strategy_context, self.legacy_context)
        self.assertIsNone(self.engine.start_block)
        self.assertIsNone(self.broker.risk_manager.auto_limits)






if __name__ == "__main__":
    unittest.main()
