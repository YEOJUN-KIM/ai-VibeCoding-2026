"""PAPER-SAFETY-FIX: carried AUTO holdings after a restart, the holdings cap on every automatic path, isolated clock, UI text.
Isolated like test_paper_mvp.py (throwaway schema in a TEST database, synthetic market, no network)."""
import unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from auto_trader import paper, risk
from auto_trader.models import OrderRequest
from auto_trader.trading import KST
from tests.test_paper_mvp import MvpBase

STATIC = Path(__file__).resolve().parent.parent / "auto_trader" / "static"


class CarryBase(MvpBase):
    def setUp(self):
        super().setUp()
        self.broker.clock = self.clock          # order times follow the virtual clock (as the isolated app now does)

    def carried(self, **changes):
        """Run to get automatic lots, stop, then 'restart' on the next day with a brand-new broker + risk manager that have
        NOT started a run (strategy positions are empty, exactly like right after a server restart)."""
        self.mvp.apply(self.balanced(**changes))
        self.engine.running = True
        self.poll(8)
        self.engine.running = False
        self.assertTrue(self.broker.managed_lots(), "expected carried automatic lots")
        self.clock.advance(days=1)
        reloaded = self.restart()
        self.assertEqual(reloaded.strategy_symbols(), [], "precondition: run-scoped positions are empty after a restart")
        return reloaded, reloaded.risk_manager

    def restart(self):
        reloaded = paper.PaperBroker(self.market, self.CASH, account_name="mvp-test")
        reloaded.initialize()
        reloaded.clock = self.clock
        reloaded.set_risk_manager(risk.RiskManager(self.market))
        return reloaded

    def limits(self, **kw):
        return self.mvp.auto_limits(self.balanced(**kw))


class RestartCarryTests(CarryBase):
    def test_carried_lots_are_visible_to_the_risk_book_before_any_run(self):
        reloaded, _ = self.carried()
        self.assertTrue(reloaded.risk_symbols())
        self.assertGreater(reloaded.risk_value(), 0)

    def test_restart_with_carried_lots_never_gets_a_trusted_flat_start(self):
        reloaded, manager = self.carried()
        status = manager.auto_status(self.limits())          # first evaluation after the restart, BEFORE begin_strategy
        self.assertFalse(status["new_buys_allowed"])
        self.assertIn("기준", status["block_reason"])
        self.assertGreater(status["invested_amount"], 0)     # carried holdings count as invested
        record = reloaded.mvp_state()["auto_open"]
        self.assertNotEqual(record.get("source"), "flat-start")
        self.assertFalse(record["trusted"])
        reloaded.begin_strategy([])                          # the run later adopts the lots: still untrusted
        self.assertFalse(manager.auto_status(self.limits())["new_buys_allowed"])

    def test_buy_is_refused_after_restart_with_carried_lots(self):
        reloaded, manager = self.carried()
        manager.auto_limits = self.limits()
        other = next(s.symbol for s in self.market.stocks() if s.symbol not in reloaded.risk_symbols())
        order = reloaded.submit(OrderRequest(symbol=other, side="BUY", quantity=1), source="STRATEGY")
        self.assertEqual(order.status.value, "REJECTED")

    def test_flat_start_record_saved_by_the_earlier_implementation_is_not_trusted(self):
        reloaded, manager = self.carried()
        today = reloaded.now().astimezone(KST).date().isoformat()
        reloaded.update_mvp(auto_open=dict(v=risk.BASELINE_VERSION, date=today, unrealized="0", trusted=True,
                                           at=reloaded.now().isoformat(), source="flat-start"))
        self.assertFalse(manager.auto_status(self.limits())["new_buys_allowed"])

    def _legacy_flat_record(self, reloaded):
        today = reloaded.now().astimezone(KST).date().isoformat()
        reloaded.update_mvp(auto_open=dict(v=risk.BASELINE_VERSION, date=today, unrealized="0", trusted=True,
                                           at=reloaded.now().isoformat(), source="flat-start"))

    def _partial_sell(self, reloaded):
        reloaded.begin_strategy([])
        lot = reloaded.managed_lots()[0]
        self.assertGreater(lot["quantity"], 1)
        sell = reloaded.submit(OrderRequest(symbol=lot["symbol"], side="SELL", quantity=1), source="STRATEGY", lot_id=lot["id"])
        self.assertEqual(sell.status.value, "FILLED")
        return lot

    def test_partial_sell_does_not_rehabilitate_a_legacy_flat_start(self):
        reloaded, manager = self.carried()
        manager.auto_limits = self.limits()
        self._legacy_flat_record(reloaded)
        self.assertFalse(manager.auto_status(self.limits())["new_buys_allowed"])   # before any fill
        self._partial_sell(reloaded)                                                # a fill today
        self.assertTrue(reloaded.risk_symbols(), "remaining lots still held")
        self.assertFalse(manager.auto_status(self.limits())["new_buys_allowed"])
        other = next(s.symbol for s in self.market.stocks() if s.symbol not in reloaded.risk_symbols())
        self.assertEqual(reloaded.submit(OrderRequest(symbol=other, side="BUY", quantity=1), source="STRATEGY").status.value, "REJECTED")

    def test_partial_sell_before_any_evaluation_with_a_legacy_record_stays_untrusted_after_restart(self):
        reloaded, manager = self.carried()
        manager.auto_limits = self.limits()
        self._legacy_flat_record(reloaded)
        self._partial_sell(reloaded)                      # no auto_status call before the sell: nothing persisted yet
        again = self.restart()
        self.assertFalse(again.risk_manager.auto_status(self.limits())["new_buys_allowed"])
        self.assertFalse(again.mvp_state()["auto_open"]["trusted"])

    def test_demotion_is_persisted_and_does_not_pollute_day_pnl(self):
        reloaded, manager = self.carried()
        self._legacy_flat_record(reloaded)
        profit_before = manager.auto_profit(self.limits())
        record = reloaded.mvp_state()["auto_open"]
        self.assertFalse(record["trusted"])
        self.assertEqual(record["unrealized"], "0")        # baseline value untouched
        self.assertEqual(manager.auto_profit(self.limits()), profit_before)
        self.assertEqual(self.restart().mvp_state()["auto_open"]["trusted"], False)

    def test_full_sell_of_carried_lots_keeps_the_day_untrusted_and_sells_work(self):
        reloaded, manager = self.carried()
        manager.auto_limits = self.limits()
        self._legacy_flat_record(reloaded)
        reloaded.begin_strategy([])
        for lot in reloaded.managed_lots():
            self.assertEqual(reloaded.submit(OrderRequest(symbol=lot["symbol"], side="SELL", quantity=lot["quantity"]),
                                             source="STRATEGY", lot_id=lot["id"]).status.value, "FILLED")
        self.assertFalse(reloaded.risk_symbols())
        self.assertFalse(manager.auto_status(self.limits())["new_buys_allowed"])

    def test_verified_flat_start_stays_trusted_after_buys_and_partial_sell_today(self):
        status = self.broker.risk_manager.auto_status(self.limits())
        self.assertTrue(status["new_buys_allowed"], status["block_reason"])
        self.assertTrue(self.broker.mvp_state()["auto_open"].get("flat_verified"))
        self.mvp.apply(self.balanced())
        self.engine.running = True
        self.poll(8)
        self.engine.running = False
        self.assertTrue(self.broker.managed_lots())
        lot = self.broker.managed_lots()[0]
        if lot["quantity"] > 1:
            self.broker.submit(OrderRequest(symbol=lot["symbol"], side="SELL", quantity=1), source="STRATEGY", lot_id=lot["id"])
        self.assertTrue(self.broker.risk_manager.auto_status(self.limits())["new_buys_allowed"])

    def test_practice_reset_allows_a_fresh_verified_flat_start(self):
        reloaded, manager = self.carried()
        self._legacy_flat_record(reloaded)
        manager.auto_status(self.limits())
        reloaded.reset_practice()
        status = manager.auto_status(self.limits())
        self.assertTrue(status["new_buys_allowed"], status["block_reason"])

    def test_truly_flat_account_still_gets_a_trusted_flat_start(self):
        status = self.broker.risk_manager.auto_status(self.limits())
        self.assertTrue(status["new_buys_allowed"], status["block_reason"])

    def test_carried_lots_keep_their_exit_rules_and_can_be_sold(self):
        reloaded, manager = self.carried()
        reloaded.begin_strategy([])
        manager.auto_limits = self.limits()
        lot = reloaded.managed_lots()[0]
        self.assertEqual((lot["context"]["stop_loss_rate"], lot["context"]["take_profit_rate"]), ("5", "10"))
        sell = reloaded.submit(OrderRequest(symbol=lot["symbol"], side="SELL", quantity=lot["quantity"]),
                               source="STRATEGY", lot_id=lot["id"])
        self.assertEqual(sell.status.value, "FILLED", "exits are never blocked by the buy-side safety check")

    def test_same_day_pnl_after_restart_still_counts_todays_sells(self):
        self.mvp.apply(self.balanced())
        self.engine.running = True
        self.poll(45)
        self.engine.running = False
        before = self.broker.risk_manager.auto_profit(self.limits())
        reloaded = self.restart()                              # same day: baseline persisted by the first evaluation
        self.assertEqual(reloaded.risk_manager.auto_profit(self.limits()), before)


class MaxHoldingsTests(MvpBase):
    def setUp(self):
        super().setUp()
        self.broker.clock = self.clock          # same virtual clock as the reloaded broker, so the day baseline is shared

    def symbols(self, n):
        return [s.symbol for s in self.market.stocks()][:n]

    def auto_buy(self, symbol, quantity=1):
        return self.broker.submit(OrderRequest(symbol=symbol, side="BUY", quantity=quantity), source="STRATEGY")

    def test_legacy_strategy_start_path_enforces_max_holdings(self):
        self.engine.candidate_provider = None                         # legacy /strategy/start flow, no Scout
        self.mvp.apply(self.balanced(max_holdings=2), activate=False)
        self.mvp.limit_legacy_run()
        first, second, third = self.symbols(3)
        self.assertEqual(self.auto_buy(first).status.value, "FILLED")
        self.assertEqual(self.auto_buy(second).status.value, "FILLED")
        refused = self.auto_buy(third)
        self.assertEqual(refused.status.value, "REJECTED")
        self.assertIn("최대 보유 종목 수", refused.message)

    def test_adding_to_an_already_held_symbol_is_not_a_new_holding(self):
        self.mvp.apply(self.balanced(max_holdings=1), activate=False)
        self.mvp.limit_legacy_run()
        first, second = self.symbols(2)
        self.assertEqual(self.auto_buy(first).status.value, "FILLED")
        self.assertEqual(self.auto_buy(first).status.value, "FILLED")
        self.assertEqual(self.auto_buy(second).status.value, "REJECTED")

    def test_mvp_start_path_enforces_max_holdings_in_the_common_check(self):
        self.mvp.apply(self.balanced(max_holdings=1))
        first, second = self.symbols(2)
        self.assertEqual(self.auto_buy(first).status.value, "FILLED")
        self.assertEqual(self.auto_buy(second).status.value, "REJECTED")

    def test_engine_entry_reason_reports_the_cap(self):
        self.mvp.apply(self.balanced(max_holdings=1))
        first, second = self.symbols(2)
        self.auto_buy(first)
        self.assertIn("최대 보유 종목 수", self.engine._entry_block_reason(second) or "")

    def test_manual_paper_orders_ignore_the_automatic_cap(self):
        self.mvp.apply(self.balanced(max_holdings=1))
        first, second = self.symbols(2)
        self.auto_buy(first)
        self.assertEqual(self.broker.submit(OrderRequest(symbol=second, side="BUY", quantity=1)).status.value, "FILLED")

    def test_manual_holdings_do_not_count_toward_the_cap(self):
        self.mvp.apply(self.balanced(max_holdings=1))
        first, second = self.symbols(2)
        self.broker.submit(OrderRequest(symbol=second, side="BUY", quantity=1))
        self.assertEqual(self.auto_buy(first).status.value, "FILLED")

    def test_cap_counts_carried_lots_after_restart(self):
        self.mvp.apply(self.balanced(max_holdings=1))
        first, second = self.symbols(2)
        self.assertTrue(self.broker.risk_manager.auto_status(self.mvp.auto_limits())["new_buys_allowed"])   # verified flat start first
        self.auto_buy(first)
        reloaded = paper.PaperBroker(self.market, self.CASH, account_name="mvp-test")
        reloaded.initialize()
        reloaded.clock = self.clock
        reloaded.set_risk_manager(risk.RiskManager(self.market))
        reloaded.risk_manager.auto_limits = self.mvp.auto_limits()
        status = reloaded.risk_manager.auto_status(self.mvp.auto_limits())
        self.assertTrue(status["new_buys_allowed"], "baseline is trusted (same day), so only the cap can refuse")
        reason = reloaded.risk_manager.check_buy(symbol=second, amount=Decimal(1000), fee=Decimal(0))
        self.assertIn("최대 보유 종목 수", reason or "")

    def test_selling_is_never_blocked_by_the_cap(self):
        self.mvp.apply(self.balanced(max_holdings=1))
        first = self.symbols(1)[0]
        self.auto_buy(first)
        self.broker.begin_strategy([])
        lot = self.broker.managed_lots()[0]
        sell = self.broker.submit(OrderRequest(symbol=first, side="SELL", quantity=lot["quantity"]), source="STRATEGY", lot_id=lot["id"])
        self.assertEqual(sell.status.value, "FILLED")


class IsolatedClockAndUiTests(unittest.TestCase):
    def test_isolated_engine_and_broker_share_the_virtual_clock(self):
        from auto_trader import paper_mvp_isolated as iso
        engine = SimpleNamespace(_clock=None, market_hours_provider=object(), broker=SimpleNamespace(clock=None))
        iso.prepare_engine(engine)
        clock = iso.synthetic()[1]
        self.assertIs(engine._clock, clock)
        self.assertIs(engine.broker.clock, clock)

    def test_virtual_clock_advances_for_any_users_running_engine(self):
        from auto_trader.lifecycle import _all_engines
        mine, other, session_engine = (SimpleNamespace(running=r) for r in (False, True, True))
        runtime = SimpleNamespace(
            legacy_workspace=SimpleNamespace(engine=mine, sessions={}),
            workspaces=SimpleNamespace(items={7: SimpleNamespace(engine=other, sessions={"LIVE_COPY": (None, session_engine)})}))
        self.assertEqual(len(_all_engines(runtime)), 3)
        self.assertTrue(any(e.running for e in _all_engines(runtime)))

    def test_ui_text_matches_the_buy_only_daily_limit(self):
        html = (STATIC / "paper-mvp.html").read_text(encoding="utf-8")
        js = (STATIC / "paper-mvp.js").read_text(encoding="utf-8")
        self.assertNotIn("매수와 매도를 함께 셉니다", html)
        self.assertIn("신규 매수만 세며", html)
        self.assertIn("하루 매수 횟수 한도", html)
        self.assertIn("하루 매수 횟수는 0~100", js)

    def test_status_failure_replaces_a_stale_running_label(self):
        self.assertIn("상태 확인 실패", (STATIC / "paper-mvp.js").read_text(encoding="utf-8"))

    def test_sector_choice_is_still_declared_as_not_connected(self):
        self.assertIn("후보 선정에는 아직 반영되지 않습니다", (STATIC / "paper-mvp.js").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
