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
        self.state = main.UserWorkspace(None, self.broker, self.engine, main.legacy_workspace.client, main.legacy_workspace.quote_stream)
        token = main.current_workspace.set(self.state)
        self.addCleanup(main.current_workspace.reset, token)
        for name in ("initialize", "set_risk_manager"):
            p = patch.object(PaperBroker, name)
            p.start()
            self.addCleanup(p.stop)

    async def test_restore_last_strategy_configures_but_does_not_start(self):
        from types import SimpleNamespace
        symbol = main.market.stocks()[0].symbol
        saved = SimpleNamespace(id=12, execution_mode='DRY_RUN', targets=[SimpleNamespace(symbol=symbol)])
        self.broker.remember_strategy(7, 12, [symbol])
        with patch.object(main, 'list_strategies', return_value=[saved]) as lookup, patch.object(main, '_configure_paper_strategy') as configure:
            self.assertIs(main._restore_paper_strategy(self.broker, self.engine), saved)
            lookup.assert_called_once_with(7)
            configure.assert_called_once_with(self.engine, saved)
            self.assertFalse(self.engine.running)
        for available in ([], [SimpleNamespace(id=12, execution_mode='LIVE')]):
            with patch.object(main, 'list_strategies', return_value=available), patch.object(main, '_configure_paper_strategy') as configure:
                self.assertIsNone(main._restore_paper_strategy(self.broker, self.engine))
                configure.assert_not_called()

    async def test_accounts_preserve_separate_cash_positions_and_orders(self):
        symbol = main.market.stocks()[0].symbol
        self.broker.submit(OrderRequest(symbol=symbol, side=OrderSide.BUY, quantity=1))
        old_cash = self.broker.account().cash
        result = await main.select_paper_account("EXPERIMENT", None)
        self.assertEqual(result.account.cash, D(10000000))
        self.assertEqual(result.account.positions, [])
        self.assertTrue(result.snapshot_ready)
        self.assertEqual(main.broker.orders(), [])
        experiment = self.state.broker
        experiment.submit(OrderRequest(symbol=symbol, side=OrderSide.BUY, quantity=2))
        experiment_cash = experiment.account().cash
        await main.select_paper_account("LIVE_COPY", None)
        self.assertIs(self.state.broker, self.broker)
        self.assertEqual(main.broker.account().cash, old_cash)
        self.assertEqual(len(main.broker.orders()), 1)
        await main.select_paper_account("EXPERIMENT", None)
        self.assertIs(self.state.broker, experiment)
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
        same = self.state.broker
        await main.select_paper_account("EXPERIMENT", None)
        self.assertIs(self.state.broker, same)

    async def test_switch_keeps_background_worker_running_and_stop_is_account_specific(self):
        self.engine.interval_seconds = 1
        await self.engine.start()
        try:
            await main.select_paper_account('EXPERIMENT', None)
            ticks = self.engine.tick_count
            await asyncio.sleep(1.03)
            self.assertTrue(self.engine.running)
            self.assertGreater(self.engine.tick_count, ticks)
            status = main.paper_workspace(None)
            self.assertEqual(status.background_runs[0]['account_mode'], 'LIVE_COPY')
            summaries = {item['account_mode']: item for item in status.account_summaries}
            self.assertTrue(summaries['EXPERIMENT']['selected'])
            self.assertFalse(summaries['LIVE_COPY']['selected'])
            self.assertTrue(summaries['LIVE_COPY']['running'])
            self.assertEqual(summaries['EXPERIMENT']['position_count'], 0)
            self.assertEqual(D(summaries['LIVE_COPY']['total_profit']), self.broker.account().total_profit)

            await main.strategy_stop(None)
            self.assertTrue(self.engine.running)
            self.engine.interval_seconds = 1
            await main.select_paper_account('LIVE_COPY', None)
            self.assertIs(self.state.engine, self.engine)
            self.assertTrue(main.engine.running)
            await main.strategy_stop(None)
            self.assertFalse(self.engine.running)
        finally:
            await main._stop_all_paper_engines()

    async def test_shutdown_stops_both_account_workers(self):
        await self.engine.start()
        await main.select_paper_account('EXPERIMENT', None)
        other = self.state.engine
        await other.start()
        await main._stop_all_paper_engines()
        self.assertFalse(other.running)
        self.assertFalse(self.engine.running)

