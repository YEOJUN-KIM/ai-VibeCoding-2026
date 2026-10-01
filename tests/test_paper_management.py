import unittest
from datetime import datetime
from decimal import Decimal as D
from zoneinfo import ZoneInfo
from unittest.mock import patch

from auto_trader.models import OrderRequest
from auto_trader.paper import PaperBroker
from auto_trader.simulator import MarketSimulator
from auto_trader.strategy import MovingAverageEngine


class ManagementTests(unittest.TestCase):
    def setUp(self):
        self.market = MarketSimulator(symbols=('005930', '000660'))
        self.broker = PaperBroker(self.market)
        self.now = datetime(2026, 10, 1, 10, tzinfo=ZoneInfo('Asia/Seoul'))
        self.engine = MovingAverageEngine(self.market, self.broker, clock=lambda: self.now)
        self.configure('005930', 'A', stop_loss_rate=D(3))

    def configure(self, symbol, name, **kwargs):
        self.engine.configure(interval_seconds=10, short_period=2, long_period=3,
                              order_quantity=1, target_symbols=[symbol], strategy_name=name, **kwargs)

    def buy(self, symbol, source='STRATEGY', quantity=1):
        return self.broker.submit(OrderRequest(symbol=symbol, side='BUY', quantity=quantity), source=source)

    def test_switch_retains_old_rules_and_does_not_adopt_manual(self):
        self.buy('005930', quantity=2)
        self.buy('005930', source='MANUAL')
        self.configure('000660', 'B', stop_loss_rate=D(50))
        self.assertEqual(self.broker.strategy_quantity('005930'), 2)
        self.assertEqual(self.broker.managed_lots()[0]['context']['strategy_name'], 'A')
        self.market._prices['005930'] *= D('.9')
        self.engine._manage_exits(None)
        self.assertEqual(self.broker.holding_quantity('005930'), 1)
        self.assertEqual(self.broker.managed_lots(), [])

    def test_scope_current_auto_all_and_no_extra_buy(self):
        self.buy('005930')
        self.buy('000660', source='MANUAL')
        self.configure('000660', 'B')
        self.engine.set_management_scope('CURRENT')
        self.assertEqual(self.broker.strategy_quantity('005930'), 0)
        self.assertEqual(self.broker.strategy_quantity('000660'), 1)
        self.engine.set_management_scope('AUTO')
        self.assertEqual(self.broker.strategy_quantity('005930'), 1)
        self.assertEqual(self.broker.strategy_quantity('000660'), 0)
        self.engine.set_management_scope('ALL')
        self.assertEqual(self.broker.strategy_quantity('000660'), 1)
        self.assertEqual(self.engine.target_symbols, ['000660'])

    def test_same_symbol_separate_lots_use_own_rules(self):
        self.buy('005930')
        self.configure('005930', 'B', stop_loss_rate=D(50))
        self.buy('005930')
        self.market._prices['005930'] *= D('.9')
        self.engine._manage_exits(None)
        self.assertEqual(self.broker.holding_quantity('005930'), 1)
        self.assertEqual(self.broker.managed_lots()[0]['context']['strategy_name'], 'B')

    def test_restart_restores_ownership_and_scope_without_running(self):
        self.buy('005930')
        self.engine.set_management_scope('CURRENT')
        restored = PaperBroker(MarketSimulator())
        restored._restore_state(self.broker._state())
        self.assertEqual(restored.management_scope, 'CURRENT')
        restored.begin_strategy(['000660'], context={})
        self.assertEqual(restored.strategy_quantity('005930'), 0)
        restored.begin_strategy(['000660'], scope='AUTO', context={})
        self.assertEqual(restored.strategy_quantity('005930'), 1)
        self.assertEqual(restored.managed_lots()[0]['context']['strategy_name'], 'A')

    def test_failed_sale_rolls_back_ownership_then_can_retry(self):
        self.buy('005930', quantity=2)
        lot = self.broker.managed_lots()[0]
        request = OrderRequest(symbol='005930', side='SELL', quantity=1)
        with patch.object(self.broker, '_save_state', side_effect=RuntimeError('storage')):
            with self.assertRaises(RuntimeError):
                self.broker.submit(request, source='STRATEGY', lot_id=lot['id'])
        self.broker.submit(request, source='STRATEGY', lot_id=lot['id'])
        self.assertEqual(self.broker._auto_lots[0]['quantity'], 1)
        self.assertEqual(self.broker.managed_lots()[0]['quantity'], 1)

    def test_running_scope_change_rejected(self):
        self.engine.running = True
        with self.assertRaises(ValueError):
            self.engine.set_management_scope('ALL')
        self.assertEqual(self.broker.management_scope, 'AUTO')

    def test_missing_fresh_candle_does_not_sell_old_lot(self):
        self.buy('005930')
        self.configure('000660', 'B')
        self.market._prices['005930'] *= D('.9')
        self.engine._manage_exits({})
        self.assertEqual(self.broker.holding_quantity('005930'), 1)

    def test_all_scope_includes_subsequent_manual_purchase(self):
        self.engine.set_management_scope('ALL')
        self.buy('000660', source='MANUAL')
        self.assertEqual(self.broker.strategy_quantity('000660'), 1)
        self.assertEqual(self.broker.managed_lots()[0]['origin'], 'MANUAL')
        self.assertEqual(self.engine.target_symbols, ['005930'])

    def test_ownership_display_includes_excluded_auto_and_manual_quantities(self):
        self.buy('005930', quantity=2)
        self.buy('005930', source='MANUAL')
        self.configure('000660', 'B')
        self.engine.set_management_scope('CURRENT')
        rows = self.broker.holding_management()
        self.assertEqual(sum(row['quantity'] for row in rows), 3)
        self.assertTrue(all(not row['managed'] for row in rows))
        self.assertEqual(next(row for row in rows if row['origin']=='AUTO')['context']['strategy_name'], 'A')
        self.engine.set_management_scope('ALL')
        rows = self.broker.holding_management()
        self.assertEqual(sum(row['quantity'] for row in rows), 3)
        self.assertTrue(all(row['managed'] for row in rows))
