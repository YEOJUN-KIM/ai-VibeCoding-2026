import asyncio
import unittest
from datetime import datetime, timedelta
from unittest.mock import Mock, patch

from auto_trader.market_hours import MarketHours, trading_status
from auto_trader.paper import PaperBroker
from auto_trader.simulator import MarketSimulator
from auto_trader.strategy import MovingAverageEngine


def at(time):
    return datetime.fromisoformat(time)


def calendar(date='2026-09-30'):
    def session(start, end, **kwargs):
        return dict(startTime=f'{date}T{start}:00+09:00', endTime=f'{date}T{end}:00+09:00', **kwargs)
    return {'today': {'date': date, 'integrated': {
        'preMarket': session('08:00', '09:00', singlePriceAuctionStartTime=f'{date}T08:50:00+09:00'),
        'regularMarket': session('09:00', '15:30', singlePriceAuctionStartTime=f'{date}T15:20:00+09:00'),
        'afterMarket': session('15:30', '20:00', singlePriceAuctionEndTime=f'{date}T15:40:00+09:00')}}}


class CalendarTradingTests(unittest.TestCase):
    def test_continuous_boundaries_and_auctions(self):
        for time, expected in [('07:59', 'closed'), ('08:00', 'open'), ('08:50', 'closed'),
                               ('09:00', 'open'), ('15:20', 'closed'), ('15:39', 'closed'),
                               ('15:40', 'open'), ('20:00', 'closed')]:
            with self.subTest(time=time):
                self.assertEqual(trading_status(calendar(), at(f'2026-09-30T{time}:00+09:00'))['state'], expected)

    def test_holiday_partial_session_and_special_open(self):
        now = at('2026-09-30T10:00:00+09:00')
        closed = {'today': {'date': '2026-09-30', 'integrated': None},
                  'nextBusinessDay': calendar('2026-10-01')['today']}
        result = trading_status(closed, now)
        self.assertTrue(result['holiday'])
        self.assertEqual(result['next_open_at'], '2026-10-01T08:00:00+09:00')
        value = calendar()
        value['today']['integrated']['preMarket'] = None
        value['today']['integrated']['regularMarket']['startTime'] = '2026-09-30T10:30:00+09:00'
        self.assertEqual(trading_status(value, now)['state'], 'closed')
        self.assertEqual(trading_status(value, now)['next_open_at'], '2026-09-30T10:30:00+09:00')
        with self.assertRaises(ValueError):
            trading_status(calendar('2026-09-29'), now)

    def test_failure_backoff_expires_and_recovery(self):
        client = Mock(base_url='https://example.test')
        client._authorized_json_request.side_effect = [RuntimeError('offline'), {'result': calendar()}]
        service = MarketHours(client)
        now = at('2026-09-30T10:00:00+09:00')
        with patch('auto_trader.market_hours.monotonic') as timer:
            timer.return_value = 0
            self.assertEqual(service.status('KR', now=now, trading=True)['state'], 'unknown')
            timer.return_value = 20
            self.assertEqual(service.status('KR', now=now, trading=True)['state'], 'unknown')
            self.assertEqual(client._authorized_json_request.call_count, 1)
            timer.return_value = 31
            self.assertEqual(service.status('KR', now=now, trading=True)['state'], 'open')
            self.assertEqual(client._authorized_json_request.call_count, 2)


class EngineCalendarTests(unittest.TestCase):
    def setUp(self):
        self.now = at('2026-09-30T10:00:00+09:00')
        market = MarketSimulator(symbols=('005930',))
        self.feed = Mock()
        self.feed.read.return_value = ({}, {})
        self.records = Mock()
        self.provider = Mock(side_effect=lambda now: trading_status(calendar(), now))
        self.engine = MovingAverageEngine(market, PaperBroker(market), clock=lambda: self.now,
            price_feed=self.feed, market_hours_provider=self.provider, decision_recorder=self.records)

    def poll(self):
        asyncio.run(self.engine.poll_market())

    def test_closed_unknown_and_holiday_skip_feed_and_decisions(self):
        for result, expected in [({'state': 'unknown'}, 'MARKET_UNKNOWN'),
                                (trading_status({'today': {'date': '2026-09-30', 'integrated': None}}, self.now), 'HOLIDAY')]:
            self.provider.side_effect = None
            self.provider.return_value = result
            self.poll()
            self.engine.step(candles={})
            self.assertEqual(self.engine.status().operating_state, expected)
            self.feed.read.assert_not_called()
            self.records.assert_not_called()
            self.assertEqual(self.engine.tick_count, 0)
        self.provider.side_effect = RuntimeError('offline')
        self.poll()
        self.assertEqual(self.engine.status().operating_state, 'MARKET_UNKNOWN')

    def test_configured_hours_intersect_calendar_and_recover(self):
        self.engine.trading_start = at('2026-09-30T11:00:00+09:00').time().replace(tzinfo=None)
        self.poll()
        self.assertEqual(self.engine.status().operating_state, 'OUTSIDE_HOURS')
        self.feed.read.assert_not_called()
        self.now += timedelta(hours=1)
        self.poll()
        self.feed.read.assert_called_once()
        self.assertTrue(self.engine._within_hours())
        self.now += timedelta(seconds=90)
        self.assertFalse(self.engine._within_hours())
        self.assertEqual(self.engine.status().operating_state, 'MARKET_UNKNOWN')

    def test_market_closes_during_price_read_no_decision_or_exit(self):
        self.now = at('2026-09-30T15:19:59+09:00')
        self.engine.trading_end = at('2026-09-30T20:00:00+09:00').time().replace(tzinfo=None)
        def read(*args):
            self.now += timedelta(seconds=1)
            return {}, {'005930': []}
        self.feed.read.side_effect = read
        self.engine._manage_exits = Mock()
        self.poll()
        self.assertEqual(self.engine.status().operating_state, 'MARKET_CLOSED')
        self.engine._manage_exits.assert_not_called()
        self.records.assert_not_called()
        self.assertIsNone(self.engine.last_data_at)


if __name__ == '__main__':
    unittest.main()
