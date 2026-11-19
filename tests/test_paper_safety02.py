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

    def hold_auto_lots(self, **changes):
        self.mvp.apply(self.direct(**changes))
        self.engine.running = True
        self.poll(8)                                  # first entries happen near minute 31; exits come later
        self.engine.running = False
        self.assertTrue(self.broker.strategy_symbols(), "expected automatic holdings")


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


class DailyBuyLimitTests(Safety02Base):                                  # --- B
    def test_limit_survives_restart(self):
        self.hold_auto_lots(daily_buys=2)
        self.assertEqual(len(self.filled()), 2)
        broker, engine, mvp = self.reboot()
        mvp.apply(self.direct(daily_buys=2))
        reason = broker.risk_manager.check_buy(symbol=PROBE, amount=Decimal(1000), fee=Decimal(0))
        self.assertIsNotNone(reason)
        self.assertIn("하루 매수", reason)

    def test_limit_blocks_extra_buy_in_the_same_run(self):
        self.mvp.apply(self.direct(daily_buys=1))
        first = self.broker.submit(OrderRequest(symbol=PROBE, side="BUY", quantity=1), source="STRATEGY")
        second = self.broker.submit(OrderRequest(symbol=PROBE, side="BUY", quantity=1), source="STRATEGY")
        self.assertEqual((first.status.value, second.status.value), ("FILLED", "REJECTED"))
        self.assertIn("하루 매수", second.message)

    def test_duplicate_request_id_counts_once(self):
        self.mvp.apply(self.direct(daily_buys=2))
        request = OrderRequest(symbol=PROBE, side="BUY", quantity=1, request_id="same-signal")
        for _ in range(3):
            self.broker.submit(request, source="STRATEGY")
        self.assertEqual(len(self.filled()), 1)
        self.assertIsNone(self.broker.risk_manager.check_buy(symbol=PROBE, amount=Decimal(1000), fee=Decimal(0)))   # 1 of 2 used

    def test_manual_buys_and_sells_are_not_counted(self):
        self.mvp.apply(self.direct(daily_buys=1))
        for _ in range(3):
            self.assertEqual(self.broker.submit(OrderRequest(symbol=PROBE, side="BUY", quantity=1)).status.value, "FILLED")
        self.assertEqual(self.broker.submit(OrderRequest(symbol=PROBE, side="BUY", quantity=1), source="STRATEGY").status.value, "FILLED")

    def test_rejected_orders_are_not_counted(self):
        self.mvp.apply(self.direct(daily_buys=1))
        too_many = int(self.CASH / self.market.quote(PROBE).price) + 5
        self.assertEqual(self.broker.submit(OrderRequest(symbol=PROBE, side="BUY", quantity=1000), source="STRATEGY").status.value in
                         ("FILLED", "REJECTED"), True)
        self.broker._orders.clear()
        self.assertEqual(self.broker.submit(OrderRequest(symbol=PROBE, side="BUY", quantity=too_many), source="STRATEGY").status.value, "REJECTED")
        self.assertEqual(self.broker.submit(OrderRequest(symbol=PROBE, side="BUY", quantity=1), source="STRATEGY").status.value, "FILLED")

    def test_new_kst_day_starts_a_new_count(self):
        self.mvp.apply(self.direct(daily_buys=1))
        self.assertEqual(self.broker.submit(OrderRequest(symbol=PROBE, side="BUY", quantity=1), source="STRATEGY").status.value, "FILLED")
        self.assertEqual(self.broker.submit(OrderRequest(symbol=PROBE, side="BUY", quantity=1), source="STRATEGY").status.value, "REJECTED")
        self.clock.now = DAY + timedelta(days=1, minutes=5)
        self.assertEqual(self.broker.submit(OrderRequest(symbol=PROBE, side="BUY", quantity=1), source="STRATEGY").status.value, "FILLED")

    def test_late_evening_utc_order_belongs_to_the_kst_day(self):
        self.mvp.apply(self.direct(daily_buys=1))
        self.clock.now = DAY.replace(hour=0, minute=30)                 # 00:30 KST == previous day 15:30 UTC
        self.broker.submit(OrderRequest(symbol=PROBE, side="BUY", quantity=1), source="STRATEGY")
        self.clock.now = DAY.replace(hour=8, minute=0)                  # same KST day, 08:00 KST
        self.assertEqual(self.broker.submit(OrderRequest(symbol=PROBE, side="BUY", quantity=1), source="STRATEGY").status.value, "REJECTED")

    def test_unreadable_ledger_holds_new_automatic_buys(self):
        self.mvp.apply(self.direct(daily_buys=5))
        with patch.object(self.broker, "orders", side_effect=RuntimeError("ledger unreadable")):
            status = self.broker.risk_manager.auto_status(self.broker.risk_manager.auto_limits)
            reason = self.broker.risk_manager.check_buy(symbol=PROBE, amount=Decimal(1000), fee=Decimal(0))
        self.assertFalse(status["new_buys_allowed"])
        self.assertIn("보류", reason)

    def test_zero_means_unlimited_like_the_engine(self):
        self.mvp.apply(self.direct(daily_buys=0))
        for _ in range(3):
            self.assertEqual(self.broker.submit(OrderRequest(symbol=PROBE, side="BUY", quantity=1), source="STRATEGY").status.value, "FILLED")


class DailyLossBaselineTests(Safety02Base):                              # --- C
    def status(self, broker=None):
        rm = (broker or self.broker).risk_manager
        return rm.auto_status(rm.auto_limits)

    def next_day(self, hour, minute):
        self.clock.now = (DAY + timedelta(days=1)).replace(hour=hour, minute=minute)

    def test_unrealized_includes_estimated_exit_costs(self):
        self.broker.fee_rate, self.broker.sell_tax_rate, self.broker.slippage_rate = Decimal("0.0015"), Decimal("0.002"), Decimal("0.001")
        self.hold_auto_lots()
        expected = Decimal(0)
        for symbol in self.broker.strategy_symbols():
            quantity, average = self.broker.strategy_quantity(symbol), self.broker.strategy_average(symbol)
            expected += self.broker.exit_proceeds(symbol, quantity, self.market.quote(symbol).price) - average * quantity
        self.assertEqual(self.broker.strategy_unrealized(), expected)

    def test_loss_before_the_first_evaluation_of_the_day_is_counted_from_the_previous_evaluation(self):
        self.hold_auto_lots()
        for symbol in self.broker.strategy_symbols():
            self.set_price(symbol, "0.90")                              # overnight / pre-first-evaluation loss
        self.next_day(11, 0)
        status = self.status()
        self.assertFalse(status["new_buys_allowed"])
        self.assertIn("손실", status["block_reason"])                   # measured from the previous evaluation, not "from now"

    def test_loss_between_open_and_a_late_first_evaluation_is_not_hidden(self):
        self.hold_auto_lots(daily_loss_pct=Decimal("0.5"))
        for symbol in self.broker.strategy_symbols():
            self.set_price(symbol, "0.90")                              # falls right after the open ...
        self.next_day(9, 14)                                            # ... first evaluation only at 09:14
        status = self.status()
        self.assertFalse(status["new_buys_allowed"])
        self.assertIn("손실", status["block_reason"])

    def test_carried_holdings_without_any_previous_record_hold_new_buys(self):
        self.hold_auto_lots()
        self.broker.update_mvp(auto_last=None)
        self.next_day(11, 0)
        status = self.status()
        self.assertFalse(status["new_buys_allowed"])
        self.assertIn("기준", status["block_reason"])
        self.assertIsNotNone(self.broker.risk_manager.check_buy(symbol=PROBE, amount=Decimal(1000), fee=Decimal(0)))

    def test_sell_before_the_first_evaluation_of_the_day_makes_the_baseline_untrusted(self):
        self.hold_auto_lots(daily_loss_pct=Decimal("0.5"))
        self.next_day(9, 3)
        lot = self.broker.managed_lots()[0]
        self.assertTrue(self.engine._sell(lot["symbol"], "exit before any evaluation", lot_id=lot["id"]))
        for sym in list(self.broker.strategy_symbols()):                # sell everything: the book is flat again
            for l in [x for x in self.broker.managed_lots() if x["symbol"] == sym]:
                self.engine._sell(sym, "exit", lot_id=l["id"])
        self.assertEqual(self.broker.strategy_symbols(), [])
        status = self.status()
        self.assertFalse(status["new_buys_allowed"], "a flat book is not trusted when carried lots were sold before the first evaluation")

    def test_hold_does_not_stop_exits_of_automatic_lots(self):
        self.hold_auto_lots()
        self.broker.update_mvp(auto_last=None)
        self.next_day(11, 0)
        self.assertFalse(self.status()["new_buys_allowed"])
        lot = self.broker.managed_lots()[0]
        self.assertTrue(self.engine._sell(lot["symbol"], "safety-02 exit", lot_id=lot["id"]))

    def test_account_reset_invalidates_the_baseline(self):
        self.hold_auto_lots()
        self.next_day(9, 5)
        self.status()                                                   # baseline recorded
        self.broker.reset_practice()
        self.assertIsNone(self.broker.mvp_state().get("auto_open"))
        self.mvp.apply(self.direct(daily_loss_pct=Decimal("0.5")))
        self.assertTrue(self.status()["new_buys_allowed"])
        self.broker.submit(OrderRequest(symbol=PROBE, side="BUY", quantity=20), source="STRATEGY")
        self.set_price(PROBE, "0.5")
        self.assertFalse(self.status()["new_buys_allowed"], "the old account's baseline must not offset the new account's loss")

    def test_first_evaluation_next_day_with_unchanged_holdings_is_a_valid_baseline_and_the_limit_then_works(self):
        self.hold_auto_lots(daily_loss_pct=Decimal("0.5"))             # 50,000 KRW
        self.next_day(9, 5)
        self.assertTrue(self.status()["new_buys_allowed"])
        for symbol in self.broker.strategy_symbols():
            self.set_price(symbol, "0.90")
        status = self.status()
        self.assertFalse(status["new_buys_allowed"])
        self.assertIn("손실", status["block_reason"])

    def test_baseline_and_hold_survive_restart(self):
        self.hold_auto_lots(daily_loss_pct=Decimal("0.5"))
        self.next_day(9, 5)
        self.status()
        for symbol in self.broker.strategy_symbols():
            self.set_price(symbol, "0.90")
        broker, engine, mvp = self.reboot()
        mvp.apply(self.direct(daily_loss_pct=Decimal("0.5")))
        self.assertFalse(self.status(broker)["new_buys_allowed"], "restart must not forget the day's loss")

    def test_unreliable_baseline_stays_unreliable_after_restart(self):
        self.hold_auto_lots()
        self.broker.update_mvp(auto_last=None)
        self.next_day(11, 0)
        self.assertFalse(self.status()["new_buys_allowed"])
        broker, engine, mvp = self.reboot()
        mvp.apply(self.direct())
        self.assertFalse(self.status(broker)["new_buys_allowed"])

    def test_no_holdings_at_first_evaluation_is_a_valid_baseline(self):
        self.next_day(11, 0)
        self.mvp.apply(self.direct(daily_loss_pct=Decimal("0.5")))
        self.assertTrue(self.status()["new_buys_allowed"])
        self.broker.submit(OrderRequest(symbol=PROBE, side="BUY", quantity=20), source="STRATEGY")
        self.set_price(PROBE, "0.5")
        self.assertFalse(self.status()["new_buys_allowed"])           # today's own loss counts from zero

    def test_corrupt_baseline_record_holds_buys(self):
        self.mvp.apply(self.direct())
        self.broker.update_mvp(auto_open="garbage")
        status = self.status()
        self.assertFalse(status["new_buys_allowed"])

    def test_realized_loss_today_counts_and_manual_loss_does_not(self):
        self.hold_auto_lots(daily_loss_pct=Decimal("5"))
        self.broker.submit(OrderRequest(symbol=PROBE, side="BUY", quantity=int(Decimal(3000000) / self.market.quote(PROBE).price)))
        self.set_price(PROBE, "0.5")                                    # large MANUAL loss
        self.assertTrue(self.status()["new_buys_allowed"])

    def test_settings_change_keeps_the_days_baseline(self):
        self.hold_auto_lots()
        self.next_day(9, 5)
        self.status()
        before = self.broker.mvp_state()["auto_open"]
        self.mvp.apply(self.direct(daily_loss_pct=Decimal("1")))
        self.status()
        self.assertEqual(self.broker.mvp_state()["auto_open"], before)


if __name__ == "__main__":
    unittest.main()
