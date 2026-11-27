"""PAPER-SAFETY-EMERGENCY-01: the emergency stop is a durable latch (per PAPER account) until an explicit, confirmed reset.

Isolated like test_paper_mvp.py: throwaway schema in a TEST database only, synthetic market, no network.
"""
import asyncio
import threading
import unittest
from decimal import Decimal
from unittest.mock import patch

from pydantic import ValidationError

from auto_trader import paper, risk
from auto_trader.models import OrderRequest, OrderSide
from auto_trader.paper import EmergencyLatchError
from auto_trader.paper_mvp import MvpConflict, PaperMvp
from auto_trader.strategy import MovingAverageEngine
from tests.test_paper_mvp import MvpBase, run


class EmergencyBase(MvpBase):
    def reconstruct(self, name="mvp-test"):
        """What a server restart does: a fresh broker/engine/controller restored from the database only."""
        broker = paper.PaperBroker(self.market, self.CASH, account_name=name)
        broker.initialize()
        broker.set_risk_manager(risk.RiskManager(self.market))
        engine = MovingAverageEngine(self.market, broker, interval_seconds=1, clock=self.clock)
        return broker, engine, PaperMvp(broker, engine, source=self.source, clock=self.clock)

    def emergency(self):
        async def go():
            await self.mvp.start()
            await self.mvp.stop(emergency=True)
        run(go())

    def request(self):
        return OrderRequest(symbol=self.market.stocks()[0].symbol, side=OrderSide.BUY, quantity=1)


class LatchDurabilityTests(EmergencyBase):
    def test_latch_survives_reconstruction_and_blocks_start(self):
        self.emergency()
        self.assertTrue(self.broker.emergency_active())
        broker, engine, mvp = self.reconstruct()
        self.assertTrue(broker.emergency_active())
        status = mvp.status()
        self.assertEqual(status["run"]["state"], "emergency")
        self.assertFalse(status["run"]["running"])
        self.assertTrue(status["emergency"])
        with self.assertRaises(MvpConflict):
            run(mvp.start())
        self.assertFalse(engine.running)
        self.assertFalse(run(engine.start()))           # engine-level defense in depth
        self.assertFalse(engine.running)
        self.assertEqual(mvp.status()["run"]["state"], "emergency")

    def test_ordinary_stop_never_downgrades_and_nothing_resumes(self):
        self.emergency()
        for _ in range(3):
            run(self.mvp.stop())                          # also what POST /auth/logout does: engine.stop()
            run(self.engine.stop())
        self.assertEqual(self.mvp.status()["run"]["state"], "emergency")
        self.assertTrue(self.engine.status().emergency_stopped)
        broker, engine, mvp = self.reconstruct()          # logout + relogin + restart
        self.assertEqual(mvp.status()["run"]["state"], "emergency")
        self.assertFalse(engine.running)

    def test_repeated_emergency_stop_is_idempotent(self):
        self.emergency()
        first = self.broker.emergency_info()["at"]
        self.clock.advance(minutes=5)
        run(self.mvp.stop(emergency=True))
        run(self.mvp.stop(emergency=True))
        self.assertEqual(self.broker.emergency_info()["at"], first)
        self.assertEqual(self.mvp.status()["run"]["state"], "emergency")

    def test_latch_before_any_run_and_startup_never_auto_resumes(self):
        run(self.mvp.stop(emergency=True))                # nothing was running
        broker, engine, mvp = self.reconstruct()
        self.assertTrue(broker.emergency_active())
        self.assertFalse(engine.running)

    def test_latch_is_per_paper_account(self):
        self.emergency()
        other, engine, mvp = self.reconstruct(name="mvp-other")
        self.assertFalse(other.emergency_active())
        self.assertNotEqual(mvp.status()["run"]["state"], "emergency")

    def test_settings_practice_reset_and_snapshot_do_not_clear_latch(self):
        self.emergency()
        self.mvp.apply(self.balanced(budget=4000000), activate=False)
        self.assertTrue(self.broker.emergency_active())
        self.broker.reset_practice()
        self.assertTrue(self.broker.emergency_active())
        self.broker.load_snapshot(cash=Decimal(5000000), positions=[])
        self.assertTrue(self.broker.emergency_active())
        broker, _, _ = self.reconstruct()
        self.assertTrue(broker.emergency_active())


class OrderGateTests(EmergencyBase):
    def test_automatic_orders_rejected_manual_allowed(self):
        self.emergency()
        before = len(self.broker.orders())
        with self.assertRaises(ValueError):
            self.broker.submit(self.request(), source="STRATEGY")
        self.assertEqual(len(self.broker.orders()), before)
        manual = self.broker.submit(self.request())               # existing manual path, existing checks
        self.assertEqual(manual.status.value, "FILLED")

    def test_engine_places_no_automatic_order_while_latched(self):
        self.mvp.apply(self.balanced())
        self.broker.latch_emergency(self.clock())
        self.engine.running = True                                  # even a (hypothetically) running loop cannot order
        count = len(self.broker.orders())
        self.poll(12)
        self.engine.running = False
        self.assertEqual(len(self.broker.orders()), count)

    def test_no_auto_order_after_emergency_stop_returns(self):
        async def go():
            await self.mvp.start()
            await asyncio.sleep(0.3)
            await self.mvp.stop(emergency=True)
            count = len(self.broker.orders())
            self.clock.advance(minutes=30)
            await asyncio.sleep(2.2)
            return count
        count = run(go())
        self.assertEqual(len(self.broker.orders()), count)

    def test_latch_vs_concurrent_submit_threads(self):
        """Once latch_emergency returns, no automatic submit from any thread can succeed (same lock)."""
        errors, start = [], threading.Event()

        def worker():
            start.wait()
            for _ in range(40):
                try:
                    self.broker.submit(self.request(), source="STRATEGY")
                except ValueError:
                    errors.append(1)
        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        start.set()
        self.broker.latch_emergency(self.clock())
        after = len(self.broker.orders())
        for t in threads:
            t.join()
        self.assertEqual(len(self.broker.orders()), after)        # nothing was added after the latch returned
        self.assertTrue(errors)


class ResetTests(EmergencyBase):
    def test_reset_returns_to_stopped_not_running_and_start_is_separate(self):
        async def go():
            await self.mvp.start()
            await asyncio.sleep(0.2)
            await self.mvp.stop(emergency=True)
        run(go())
        cash, lots = self.broker.account().cash, self.broker.managed_lots()
        self.mvp.reset_emergency()
        self.assertEqual(self.mvp.status()["run"]["state"], "stopped")
        self.assertFalse(self.engine.running)
        self.assertEqual(self.broker.account().cash, cash)           # data and lots preserved, nothing sold
        self.assertEqual(self.broker.managed_lots(), lots)
        broker, engine, mvp = self.reconstruct()                    # reset is durable and does not auto-start
        self.assertFalse(broker.emergency_active())
        self.assertFalse(engine.running)
        self.assertEqual(mvp.status()["run"]["state"], "stopped")

        async def explicit():
            self.assertTrue(await mvp.start())
            self.assertTrue(engine.running)
            await mvp.stop()
        run(explicit())

    def test_reset_requires_latch_and_stopped_engine(self):
        with self.assertRaises(MvpConflict):
            self.mvp.reset_emergency()
        self.engine.running = True
        self.broker.latch_emergency(self.clock())
        with self.assertRaises(MvpConflict):
            self.mvp.reset_emergency()
        self.engine.running = False

    def test_reset_save_failure_keeps_latch_durably(self):
        self.emergency()
        with patch.object(self.broker, "_save_state", side_effect=RuntimeError("db down")):
            with self.assertRaises(EmergencyLatchError):
                self.mvp.reset_emergency()
        self.assertTrue(self.broker.emergency_active())
        self.assertEqual(self.mvp.status()["run"]["state"], "emergency")
        self.assertTrue(self.reconstruct()[0].emergency_active())


class PersistenceFailureTests(EmergencyBase):
    def test_failed_save_reports_failure_but_stays_stopped_and_blocked(self):
        async def go():
            await self.mvp.start()
            with patch.object(self.broker, "_save_state", side_effect=RuntimeError("db down")):
                with self.assertRaises(EmergencyLatchError):
                    await self.mvp.stop(emergency=True)
            self.assertFalse(self.engine.running)
            self.assertTrue(self.broker.emergency_active())          # fail closed in memory
            with self.assertRaises(MvpConflict):
                await self.mvp.start()
            with self.assertRaises(ValueError):
                self.broker.submit(self.request(), source="STRATEGY")
            self.assertEqual(self.mvp.status()["run"]["state"], "emergency")
            await self.mvp.stop(emergency=True)                      # retry once the DB works persists the latch
        run(go())
        self.assertFalse(self.broker.emergency_info()["unsaved"])
        self.assertTrue(self.reconstruct()[0].emergency_active())


class UnsavedResetDeniedTests(EmergencyBase):
    def test_reset_denied_while_latch_unsaved_then_retry_persists_and_explicit_reset_stops(self):
        async def go():
            await self.mvp.start()
            with patch.object(self.broker, "_save_state", side_effect=RuntimeError("db down")):
                with self.assertRaises(EmergencyLatchError):
                    await self.mvp.stop(emergency=True)
                for _ in range(2):                                   # repeated failure: every direct reset is denied
                    with patch.object(self.mvp, "_log") as log, patch.object(self.mvp, "_record_equity") as rec:
                        with self.assertRaises(MvpConflict):
                            self.mvp.reset_emergency()
                        log.assert_not_called()
                        rec.assert_not_called()
                    self.assertTrue(self.broker.emergency_unsaved)   # latch unchanged, no state mutation
                    self.assertTrue(self.broker.emergency_active())
                    self.assertTrue(self.engine.emergency_stopped)
                    self.assertFalse(self.engine.running)
                    with self.assertRaises(MvpConflict):             # no auto start
                        await self.mvp.start()
                    self.assertEqual(self.mvp.status()["run"]["state"], "emergency")
                with self.assertRaises(EmergencyLatchError):         # repeated emergency stop still fails closed
                    await self.mvp.stop(emergency=True)
            await self.mvp.stop(emergency=True)                      # recover write path: retry persists
        run(go())
        self.assertFalse(self.broker.emergency_info()["unsaved"])
        self.assertTrue(self.reconstruct()[0].emergency_active())
        self.mvp.reset_emergency()                                   # saved latch: explicit reset -> stopped
        self.assertFalse(self.engine.running)
        self.assertEqual(self.mvp.status()["run"]["state"], "stopped")
        self.assertFalse(self.reconstruct()[0].emergency_active())

    def test_broker_release_also_refuses_unsaved_latch(self):
        self.broker.emergency_unsaved = True
        with self.assertRaises(EmergencyLatchError):
            self.broker.release_emergency()
        self.assertTrue(self.broker.emergency_unsaved)


class ApiContractTests(unittest.TestCase):
    def test_reset_route_is_csrf_protected_and_needs_confirmation(self):
        from auto_trader.api import paper_mvp as api
        route = next(r for r in api.router.routes if r.path.endswith("/emergency-reset"))
        self.assertEqual(route.methods, {"POST"})
        names = set()

        def walk(dep):
            for d in dep.dependencies:
                names.add(getattr(d.call, "__name__", ""))
                walk(d)
        walk(route.dependant)
        self.assertIn("require_csrf", names)
        with self.assertRaises(ValidationError):
            api.EmergencyReset()
        with self.assertRaises(ValidationError):
            api.EmergencyReset(confirm=False)
        with self.assertRaises(ValidationError):
            api.EmergencyReset(confirm=True, extra=1)
        api.EmergencyReset(confirm=True)


if __name__ == "__main__":
    unittest.main()
