import unittest
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal as D
from unittest.mock import patch
from zoneinfo import ZoneInfo

from auto_trader.ml.backtest import Config, replay
from auto_trader.ml.data_pipeline import RawCandle


class BacktestTests(unittest.TestCase):
    def rows(self, prices):
        start = datetime(2026, 10, 2, 10, tzinfo=ZoneInfo('Asia/Seoul'))
        return [RawCandle('TOSS', '005930', '1m', start + timedelta(minutes=i),
            start + timedelta(minutes=i+1, seconds=2), start + timedelta(minutes=i+1, seconds=2),
            D(p), D(p), D(p), D(p), D(1)) for i, p in enumerate(prices)]

    def config(self):
        return Config(short_period=2, long_period=3, sizing_mode='QUANTITY',
                      take_profit_rate=D(99), stop_loss_rate=D(99), cooldown_minutes=0, execution='close')

    def test_next_open_waits_until_after_signal_information_time(self):
        rows = self.rows([10, 9, 8, 10, 12, 14, 13, 12, 10, 9, 8, 7])
        result = replay(rows, replace(self.config(), execution='next_open'))
        buy = next(t for t in result['trades'] if t['side'] == 'BUY')
        # Signal is visible at 10:06:02, so 10:06 open is already past.
        self.assertEqual(buy['created_at'], rows[7].event_at.isoformat())
        self.assertEqual(D(buy['price']), rows[7].open_price * (1 + self.config().slippage_rate))
        self.assertEqual(buy['observed_at'], rows[7].available_at.isoformat())

    def test_final_signal_without_future_open_remains_unfilled(self):
        rows = self.rows([10, 9, 8, 10, 12, 14])
        result = replay(rows, replace(self.config(), execution='next_open'))
        self.assertEqual(result['summary']['filled_orders'], 0)
        self.assertEqual(result['summary']['unfilled_pending_signals'], 1)

    def test_default_risk_limits_reduce_amount_sizing(self):
        rows = self.rows([1000000, 900000, 800000, 1000000, 1200000, 1400000])
        config = replace(self.config(), sizing_mode='AMOUNT', order_amount=D('5000000'))
        paid = replay(rows, config)
        unlimited = replay(rows, replace(config, risk_enabled=False))
        self.assertEqual(paid['summary']['filled_orders'], 0)
        self.assertGreater(unlimited['summary']['filled_orders'], 0)

    def test_next_open_does_not_change_earlier_observed_trades(self):
        rows = self.rows([10, 9, 8, 10, 12, 14, 13, 12, 10, 9, 8, 7])
        config = replace(self.config(), execution='next_open')
        a = replay(rows, config)
        last = replace(rows[-1], open_price=D(1000), high_price=D(1000), low_price=D(1000), close_price=D(1000))
        b = replay(rows[:-1] + [last], config)
        cutoff = last.available_at.isoformat()
        self.assertEqual([t for t in a['trades'] if t['observed_at'] < cutoff],
                         [t for t in b['trades'] if t['observed_at'] < cutoff])

    def test_daily_results_reconcile_with_total_profit(self):
        rows = self.rows([10, 9, 8, 10, 12, 14])
        tomorrow = [replace(r, event_at=r.event_at+timedelta(days=3),
                     available_at=r.available_at+timedelta(days=3), collected_at=r.collected_at+timedelta(days=3)) for r in rows]
        result = replay(rows + tomorrow, self.config())
        self.assertEqual(len(result['daily_results']), 2)
        self.assertEqual(sum(D(d['net_change']) for d in result['daily_results']), D(result['summary']['net_profit']))

    def test_zero_volume_bar_cannot_execute_pending_buy(self):
        rows = self.rows([10, 9, 8, 10, 12, 14, 13, 12])
        rows[-1] = replace(rows[-1], volume=D(0))
        result = replay(rows, replace(self.config(), execution='next_open'))
        self.assertEqual(result['summary']['filled_orders'], 0)

    def test_deterministic_and_does_not_write_database(self):
        rows = self.rows([10, 9, 8, 10, 12, 14, 13, 12, 10])
        with patch('auto_trader.paper.connect', side_effect=AssertionError('DB write')):
            result = replay(rows, self.config())
            self.assertEqual(result, replay(rows, self.config()))
        self.assertGreater(result['summary']['filled_orders'], 0)
        self.assertGreater(result['summary']['closed_sell_orders'], 0)

    def test_future_prices_do_not_change_prior_orders(self):
        rows = self.rows([10, 9, 8, 10, 12, 14, 13, 12, 10])
        original = replay(rows, self.config())
        changed = replay(rows[:-1] + [replace(rows[-1], open_price=D(1000),
                        high_price=D(1000), low_price=D(1000), close_price=D(1000))], self.config())
        cutoff = rows[-1].available_at.isoformat()
        self.assertEqual([t for t in original['trades'] if t['created_at'] < cutoff],
                         [t for t in changed['trades'] if t['created_at'] < cutoff])
        self.assertEqual(original['equity_curve'][:-1], changed['equity_curve'][:-1])

    def test_backfill_does_not_trade_old_prices(self):
        rows = self.rows([10, 9, 8, 10, 12, 14, 13, 12, 10])
        late = rows[-1].available_at + timedelta(hours=1)
        rows = [replace(r, available_at=late, collected_at=late) for r in rows]
        self.assertEqual(replay(rows, self.config())['summary']['filled_orders'], 0)

    def test_incomplete_candle_and_duplicates_rejected(self):
        row = self.rows([10])[0]
        with self.assertRaises(ValueError):
            replay([replace(row, available_at=row.event_at)], self.config())
        with self.assertRaises(ValueError):
            replay([row, row], self.config())

    def test_costs_reduce_round_trip_profit(self):
        rows = self.rows([10, 9, 8, 10, 12, 14, 13, 12, 10])
        paid = replay(rows, self.config())
        free = replay(rows, replace(self.config(), fee_rate=D(0), sell_tax_rate=D(0), slippage_rate=D(0)))
        self.assertLess(D(paid['summary']['net_profit']), D(free['summary']['net_profit']))
        self.assertEqual(free['summary']['fees'], '0')

    def test_cash_baseline_and_zero_orders(self):
        result = replay(self.rows([10, 9, 8]), self.config(), 'cash')
        self.assertEqual(D(result['summary']['net_profit']), 0)
        self.assertIsNone(result['summary']['win_rate_percent'])


if __name__ == '__main__':
    unittest.main()
