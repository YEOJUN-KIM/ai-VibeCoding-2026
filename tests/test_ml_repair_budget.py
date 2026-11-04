"""No live API calls: bounded transport and deterministic scheduler simulation."""
import asyncio
from datetime import datetime, timedelta
from threading import Lock
from time import monotonic
import unittest
from unittest.mock import AsyncMock, Mock, patch
from urllib.request import Request
from urllib.parse import parse_qs, urlparse

from auto_trader.brokers.toss.request_budget import RequestBudget, RequestBudgetExceeded, current_budget
from auto_trader.ml import collection_worker
from auto_trader.toss import TossClient, TossApiError


class RepairTransportBudgetTests(unittest.TestCase):
    def setUp(self):
        self.client = TossClient(client_id='test', client_secret='test')
        self.request = Request('https://example.invalid/api/v1/candles')

    def test_socket_timeout_clamped_and_regular_policy_restored(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b'{}'
        response.headers = {}
        with patch('auto_trader.brokers.toss.transport.urlopen', return_value=response) as send:
            with RequestBudget(0.5, 1).activate():
                self.client._json_request(self.request)
            self.assertLessEqual(send.call_args.kwargs['timeout'], 0.5)
            self.assertIsNone(current_budget.get())
            with patch.object(self.client._request_waiter, 'wait'):
                self.client._json_request(self.request)
            self.assertEqual(send.call_args.kwargs['timeout'], self.client.timeout)

    def test_budget_cannot_shorten_required_rate_interval(self):
        self.client._group_last_request_at['MARKET_DATA_CHART'] = monotonic()
        self.client._group_intervals['MARKET_DATA_CHART'] = 2
        with patch('auto_trader.brokers.toss.transport.urlopen') as send, RequestBudget(0.1, 1).activate():
            with self.assertRaises(RequestBudgetExceeded):
                self.client._json_request(self.request)
        send.assert_not_called()

    def test_timeout_and_429_retry_yield_when_wait_does_not_fit(self):
        for error in (TossApiError('timeout', error_code='timeout'), TossApiError('429', status_code=429)):
            budget = RequestBudget(0.05, 1)
            with self.subTest(error=error), budget.activate(), patch.object(
                self.client, '_json_request_once', side_effect=error
            ) as send, patch.object(self.client._request_waiter, 'wait') as wait:
                with self.assertRaises(RequestBudgetExceeded):
                    self.client._json_request(self.request)
                send.assert_called_once()
                wait.assert_not_called()
                self.assertIs(budget.last_error, error)

    def test_group_lock_wait_is_bounded(self):
        lock = Lock()
        lock.acquire()
        try:
            started = monotonic()
            with patch.object(self.client, '_group_lock', return_value=lock), RequestBudget(0.02, 1).activate():
                with self.assertRaises(RequestBudgetExceeded):
                    self.client._json_request(self.request)
            self.assertLess(monotonic() - started, 0.5)
        finally:
            lock.release()

    def test_small_bounded_stock_window_uses_one_small_page(self):
        at = datetime.fromisoformat('2026-11-04T09:00:00+09:00')
        row = {'timestamp': at.isoformat(), 'openPrice': '100', 'highPrice': '100',
               'lowPrice': '100', 'closePrice': '100', 'volume': '1'}
        with patch.object(self.client, '_authorized_json_request', return_value={
            'result': {'candles': [row] * 11, 'nextBefore': 'unused'}
        }) as send, RequestBudget(5, 2).activate():
            self.client._domestic_candles('005930', '1m', 11, cache_seconds=0,
                                          before='2026-11-04T09:10:00+09:00')
        send.assert_called_once()
        self.assertEqual(parse_qs(urlparse(send.call_args.args[0]).query)['count'], ['11'])


class RepairPriorityTests(unittest.IsolatedAsyncioTestCase):
    async def test_regular_collection_runs_between_deferred_slices(self):
        now = [datetime.fromisoformat('2026-11-04T09:05:00+09:00')]
        client = Mock(configured=True)
        client.kr_market_open_today.return_value = ('2026-11-04', True)
        worker = collection_worker.CandleCollectionWorker(client, ('005930',))
        events = []
        class Finished(Exception):
            pass
        async def sleep(seconds):
            now[0] += timedelta(seconds=seconds)
            if now[0].minute >= 8:
                raise Finished()
        async def regular(*args):
            events.append(('regular', now[0]))
            now[0] += timedelta(seconds=2)
        def repair(*args, **kwargs):
            events.append(('repair', now[0]))
            self.assertLessEqual(kwargs['budget_seconds'], 5)
            self.assertEqual(kwargs['max_pages'], 2)
            now[0] += timedelta(seconds=kwargs['budget_seconds'])
            return {'status': 'COMPLETED', 'inserted_rows': 0, 'resume': kwargs['resume']}
        with patch.object(collection_worker, 'datetime', wraps=datetime) as clock, patch.object(
            collection_worker.asyncio, 'sleep', side_effect=sleep
        ), patch.object(worker, 'run_once', side_effect=regular), patch.object(
            collection_worker, 'reconcile_session', side_effect=repair
        ), patch.object(collection_worker, 'pending_opening_reconciliations', return_value={}):
            clock.now.side_effect = lambda *args: now[0]
            with self.assertRaises(Finished):
                await worker._run()
        self.assertEqual([kind for kind, _ in events], ['regular', 'repair'] * 3)
        self.assertEqual([at.strftime('%H:%M:%S') for kind, at in events if kind == 'regular'],
                         ['09:05:02', '09:06:02', '09:07:02'])

    async def test_pending_cursor_can_resume_after_opening_cutoff_and_regular_wins_tie(self):
        now = datetime.fromisoformat('2026-11-04T09:20:00+09:00')
        due = now.replace(second=2)
        self.assertEqual(collection_worker.next_worker_event(now, set(), {'opening_0905': due}), (due, 'regular'))
        self.assertEqual(collection_worker.next_worker_event(due.replace(second=4), set(),
                         {'opening_0905': due})[1], 'opening_0905')
        # Both pending scans get turns; final retains its original 15:35 slot.
        self.assertEqual(collection_worker.next_worker_event(now, set(),
            {'opening_0905': now, 'opening_0910': now}, 'opening_0905')[1], 'opening_0910')
        self.assertEqual(collection_worker.next_worker_event(now.replace(hour=15, minute=32), set(),
            {'opening_0905': now})[1], 'final')

    async def test_restart_restores_committed_cursor_after_0912(self):
        now = datetime.fromisoformat('2026-11-04T09:20:00+09:00')
        resume = {'report_date': now.date().isoformat(), 'kind': 'opening_0905',
                  'as_of': '2026-11-04T09:05:10+09:00', 'targets': [['stock', '005930']]}
        client = Mock(configured=True)
        client.kr_market_open_today.return_value = ('2026-11-04', True)
        worker = collection_worker.CandleCollectionWorker(client, ('005930',))
        class Finished(Exception):
            pass
        calls = []
        async def repair(kind, day):
            calls.append((kind, worker._repair_resume[kind]))
            raise Finished()
        with patch.object(collection_worker, 'datetime', wraps=datetime) as clock, patch.object(
            collection_worker, 'pending_opening_reconciliations', return_value={'opening_0905': resume}
        ), patch.object(collection_worker.asyncio, 'sleep', new=AsyncMock()), patch.object(
            worker, 'run_reconciliation_once', side_effect=repair
        ):
            clock.now.return_value = now
            with self.assertRaises(Finished):
                await worker._run()
        self.assertEqual(calls, [('opening_0905', resume)])


if __name__ == '__main__':
    unittest.main()
