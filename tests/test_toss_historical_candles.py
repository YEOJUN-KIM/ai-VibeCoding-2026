"""An optional initial cursor reuses pagination without sharing current caches."""

import unittest
from datetime import datetime, timedelta
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from auto_trader.toss import TossClient


class HistoricalCandleClientTests(unittest.TestCase):
    def row(self, at):
        return {"timestamp": at.isoformat(), "openPrice": "100", "highPrice": "101",
                "lowPrice": "99", "closePrice": "100", "volume": "0"}

    def test_initial_history_cursor_continues_using_provider_next_cursor(self):
        start = datetime.fromisoformat("2026-10-15T09:00:00+09:00")
        for name, symbol in (("_domestic_candles", "005930"), ("_market_indicator_candles", "KOSPI")):
            with self.subTest(client=name):
                client = TossClient(client_id="test", client_secret="test")
                pages = [
                    {"result": {"candles": [self.row(start + timedelta(minutes=i)) for i in range(200, 0, -1)], "nextBefore": "page-2-cursor"}},
                    {"result": {"candles": [self.row(start)]}},
                ]
                with patch.object(client, "_authorized_json_request", side_effect=pages) as request, patch("auto_trader.brokers.toss.market_data.sleep"):
                    bars = getattr(client, name)(symbol, "1m", 201, cache_seconds=0, before="2026-10-15T15:31:00+09:00")
                self.assertEqual(len(bars), 201)
                self.assertEqual((bars[0].timestamp, bars[-1].timestamp), (start, start + timedelta(minutes=200)))
                queries = [parse_qs(urlparse(call.args[0]).query) for call in request.call_args_list]
                self.assertEqual([q["before"][0] for q in queries], ["2026-10-15T15:31:00+09:00", "page-2-cursor"])
                self.assertTrue(all(bar.volume == 0 for bar in bars))

    def test_history_and_current_candle_caches_are_isolated(self):
        for name, symbol in (("_domestic_candles", "005930"), ("_market_indicator_candles", "KOSPI")):
            with self.subTest(client=name):
                client = TossClient(client_id="test", client_secret="test")
                times = [datetime.fromisoformat(f"2026-10-{day}T09:00:00+09:00") for day in (15, 16, 30)]
                with patch.object(client, "_authorized_json_request", side_effect=[{"result": {"candles": [self.row(at)]}} for at in times]) as request:
                    method = getattr(client, name)
                    a = method(symbol, "1m", 1, cache_seconds=60, before="2026-10-15T15:31:00+09:00")
                    b = method(symbol, "1m", 1, cache_seconds=60, before="2026-10-16T15:31:00+09:00")
                    current = method(symbol, "1m", 1, cache_seconds=60)
                    again = method(symbol, "1m", 1, cache_seconds=60, before="2026-10-15T15:31:00+09:00")
                self.assertEqual([a[0].timestamp, b[0].timestamp, current[0].timestamp, again[0].timestamp], [*times, times[0]])
                self.assertEqual(request.call_count, 3)
                self.assertNotIn("before", parse_qs(urlparse(request.call_args_list[-1].args[0]).query))
