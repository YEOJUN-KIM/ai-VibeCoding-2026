import unittest
import asyncio
from decimal import Decimal as D
from unittest.mock import patch
from auto_trader import main
from auto_trader.paper import PaperBroker
from auto_trader.strategy import MovingAverageEngine
from auto_trader.models import OrderRequest, OrderSide
from fastapi import HTTPException


class PaperAccountTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.broker = PaperBroker(main.market, initial_cash=D(2000000))
        self.engine = MovingAverageEngine(main.market, self.broker)
        for key, value in {"broker": self.broker, "engine": self.engine,
                           "paper_account_mode": "LIVE_COPY", "paper_snapshot_at": None,
                           "paper_source_account_label": None, "paper_selected_strategy": None,
                           "_paper_sessions": {}}.items():
            p = patch.object(main, key, value)
            p.start()
            self.addCleanup(p.stop)
        for name in ("initialize", "set_risk_manager"):
            p = patch.object(PaperBroker, name)
            p.start()
            self.addCleanup(p.stop)

    async def test_accounts_preserve_separate_cash_positions_and_orders(self):
        symbol = main.market.stocks()[0].symbol
        self.broker.submit(OrderRequest(symbol=symbol, side=OrderSide.BUY, quantity=1))
        old_cash = self.broker.account().cash
        result = await main.select_paper_account("EXPERIMENT", None)
        self.assertEqual(result.account.cash, D(10000000))
        self.assertEqual(result.account.positions, [])
        self.assertTrue(result.snapshot_ready)
        self.assertEqual(main.broker.orders(), [])
        experiment = main.broker
        experiment.submit(OrderRequest(symbol=symbol, side=OrderSide.BUY, quantity=2))
        experiment_cash = experiment.account().cash
        await main.select_paper_account("LIVE_COPY", None)
        self.assertIs(main.broker, self.broker)
        self.assertEqual(main.broker.account().cash, old_cash)
        self.assertEqual(len(main.broker.orders()), 1)
        await main.select_paper_account("EXPERIMENT", None)
        self.assertIs(main.broker, experiment)
        self.assertEqual(main.broker.account().cash, experiment_cash)
        main.broker.reset_practice()
        self.assertEqual(main.broker.account().cash, D(10000000))
        self.assertEqual(self.broker.account().cash, old_cash)

    async def test_invalid_mode_and_live_copy_into_experiment_are_rejected(self):
        with self.assertRaises(HTTPException):
            await main.select_paper_account("UNKNOWN", None)
        await main.select_paper_account("EXPERIMENT", None)
        with self.assertRaises(HTTPException):
            await main.snapshot_live_account(None)
        same = main.broker
        await main.select_paper_account("EXPERIMENT", None)
        self.assertIs(main.broker, same)

    async def test_switch_keeps_background_worker_running_and_stop_is_account_specific(self):
        self.engine.interval_seconds = .01
        await self.engine.start()
        try:
            await main.select_paper_account('EXPERIMENT', None)
            ticks = self.engine.tick_count
            await asyncio.sleep(.03)
            self.assertTrue(self.engine.running)
            self.assertGreater(self.engine.tick_count, ticks)
            status = main.paper_workspace(None)
            self.assertEqual(status.background_runs[0]['account_mode'], 'LIVE_COPY')
            await main.strategy_stop(None)
            self.assertTrue(self.engine.running)
            self.engine.interval_seconds = 1
            await main.select_paper_account('LIVE_COPY', None)
            self.assertIs(main.engine, self.engine)
            self.assertTrue(main.engine.running)
            await main.strategy_stop(None)
            self.assertFalse(self.engine.running)
        finally:
            await main._stop_all_paper_engines()

    async def test_shutdown_stops_both_account_workers(self):
        await self.engine.start()
        await main.select_paper_account('EXPERIMENT', None)
        other = main.engine
        await other.start()
        await main._stop_all_paper_engines()
        self.assertFalse(other.running)
        self.assertFalse(self.engine.running)
