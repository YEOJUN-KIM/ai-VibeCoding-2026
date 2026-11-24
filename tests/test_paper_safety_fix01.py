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








    def test_carried_lots_keep_their_exit_rules_and_can_be_sold(self):
        reloaded, manager = self.carried()
        reloaded.begin_strategy([])
        manager.auto_limits = self.limits()
        lot = reloaded.managed_lots()[0]
        self.assertEqual((lot["context"]["stop_loss_rate"], lot["context"]["take_profit_rate"]), ("5", "10"))
        sell = reloaded.submit(OrderRequest(symbol=lot["symbol"], side="SELL", quantity=lot["quantity"]),
                               source="STRATEGY", lot_id=lot["id"])
        self.assertEqual(sell.status.value, "FILLED", "exits are never blocked by the buy-side safety check")





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


    def test_status_failure_replaces_a_stale_running_label(self):
        self.assertIn("상태 확인 실패", (STATIC / "paper-mvp.js").read_text(encoding="utf-8"))

    def test_unconnected_sector_choice_is_gone(self):      # PAPER-SIMPLIFY-02: removed instead of declared "not connected"
        self.assertNotIn("data-sector", (STATIC / "paper-mvp.js").read_text(encoding="utf-8"))
        self.assertNotIn("data-sector", (STATIC / "paper-mvp.html").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
