import ast
import asyncio
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from auto_trader.toss import TossApiError


class CandidateRefreshTests(unittest.IsolatedAsyncioTestCase):
    async def run_scan(self, failures, *, count=1, watched=None):
        # 실행 서버/DB 없이 스캔의 진행·재시도 흐름만 검증한다.
        source = ast.parse((Path(__file__).resolve().parents[1] / 'auto_trader/main.py').read_text(encoding='utf-8'))
        function = next(n for n in source.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'refresh_long_term_candidates')
        analysis = SimpleNamespace(overall_score=76, generated_at=datetime.now(timezone.utc), symbol='460860')
        client = Mock()
        client.list_domestic_stocks.return_value = SimpleNamespace(results=[SimpleNamespace(symbol=str(460860+i), name='후보'+str(i), security_type='STOCK', is_common_share=True) for i in range(count)])
        client.domestic_stock_detail.side_effect = failures + [object()] * count
        sleep = AsyncMock()
        status = {}
        replace = Mock()
        scope = dict(asyncio=SimpleNamespace(to_thread=asyncio.to_thread, gather=asyncio.gather, sleep=sleep, CancelledError=asyncio.CancelledError),
                     datetime=datetime, timezone=timezone, _long_term_scan_status=status, _long_term_scan_lock=asyncio.Lock(),
                     toss_client=client, dart_client=Mock(), long_term_watched_symbols=lambda: watched or set(),
                     analyze_long_term=lambda *_: analysis, save_long_term_analysis=Mock(),
                     long_term_recommendation_assessment=lambda _: (True, []), replace_long_term_recommendations=replace,
                     logger=Mock(), TossApiError=TossApiError)
        exec(compile(ast.Module(body=[function], type_ignores=[]), '<scan>', 'exec'), scope)
        await scope['refresh_long_term_candidates'](force=True)
        return status, sleep, replace, client

    async def test_success_has_no_fixed_delay(self):
        status, sleep, replace, _ = await self.run_scan([])
        sleep.assert_not_awaited()
        replace.assert_called_once_with(['460860'])
        self.assertFalse(status['running'])

    async def test_rate_limit_retries_same_candidate(self):
        status, sleep, replace, client = await self.run_scan([TossApiError('limited', status_code=429)])
        sleep.assert_awaited_once_with(15)
        self.assertEqual(client.domestic_stock_detail.call_count, 2)
        self.assertEqual(status['failed'], 0)
        replace.assert_called_once_with(['460860'])

    async def test_persistent_failure_keeps_existing_recommendations(self):
        status, sleep, replace, _ = await self.run_scan([TossApiError('limited', status_code=429)] * 3)
        self.assertEqual(sleep.await_count, 2)
        self.assertEqual(status['failed'], 1)
        self.assertFalse(status['running'])
        self.assertIn('조회 실패 1개', status['message'])
        replace.assert_not_called()

    async def test_all_candidates_are_processed_even_after_five_pass(self):
        status, _, _, client = await self.run_scan([], count=20)
        self.assertEqual(client.domestic_stock_detail.call_count, 20)
        self.assertEqual(status['target'], 20)
        self.assertEqual(status['completed'], 20)
        self.assertEqual(status['phase'], 'complete')

    async def test_watched_candidates_count_as_reviewed_without_analysis(self):
        status, _, _, client = await self.run_scan([], count=3, watched={'460860'})
        self.assertEqual(status['target'], 3)
        self.assertEqual(status['completed'], 3)
        self.assertEqual(status['skipped'], 1)
        self.assertEqual(client.domestic_stock_detail.call_count, 2)


class WatchRefreshTests(unittest.IsolatedAsyncioTestCase):
    def load_function(self, name, scope):
        source = ast.parse((Path(__file__).resolve().parents[1] / 'auto_trader/main.py').read_text(encoding='utf-8'))
        function = next(n for n in source.body if isinstance(n, ast.AsyncFunctionDef) and n.name == name)
        exec(compile(ast.Module(body=[function], type_ignores=[]), '<watch>', 'exec'), scope)
        return scope[name]

    async def test_watch_refresh_retries_and_continues_after_failure(self):
        status = {}
        analyze = AsyncMock(side_effect=[TossApiError('limited', status_code=429), object(), ValueError('bad data'), object()])
        sleep = AsyncMock()
        scope = dict(asyncio=SimpleNamespace(to_thread=asyncio.to_thread, sleep=sleep, CancelledError=asyncio.CancelledError),
                     datetime=datetime, timezone=timezone, _long_term_watch_status=status,
                     long_term_watched_symbols=lambda: {'1','2','3'}, build_and_save_long_term_analysis=analyze,
                     logger=Mock(), TossApiError=TossApiError)
        await self.load_function('refresh_long_term_watch_candidates', scope)()
        self.assertEqual(status['completed'], 3)
        self.assertEqual(status['failed'], 1)
        self.assertFalse(status['running'])
        self.assertEqual(analyze.await_count, 4)
        sleep.assert_awaited_once_with(15)

    async def test_daily_worker_refreshes_watches_then_forces_recommendations(self):
        events = []
        async def watch(): events.append('watch')
        async def recommend(**kwargs): events.append(('recommend', kwargs))
        sleep = AsyncMock(side_effect=[None, asyncio.CancelledError()])
        status = {}
        now = datetime.now(timezone.utc)
        scope = dict(asyncio=SimpleNamespace(sleep=sleep, CancelledError=asyncio.CancelledError, Task=object),
                     datetime=datetime, timezone=timezone, _long_term_scan_status=status,
                     settings=SimpleNamespace(long_term_scan_hour=9, long_term_scan_minute=10),
                     next_daily_scan_at=Mock(return_value=now), refresh_long_term_watch_candidates=watch,
                     refresh_long_term_candidates=recommend)
        with self.assertRaises(asyncio.CancelledError):
            await self.load_function('long_term_candidate_worker', scope)(None)
        self.assertEqual(events, ['watch', ('recommend', {'force': True})])
        self.assertEqual(scope['next_daily_scan_at'].call_args.args[1:], (9,10))
