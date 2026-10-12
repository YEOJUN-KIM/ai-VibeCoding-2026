import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import Mock

from auto_trader.models import LiveStockCandle
from auto_trader.paper_feed import PaperPriceFeed
from auto_trader.toss import TossApiError


class PaperFeedTests(unittest.TestCase):
    def test_unavailable_symbol_does_not_block_other_symbols(self):
        now = datetime(2026, 10, 1, 1, 10, tzinfo=timezone.utc)
        client = Mock()
        client.current_prices.return_value = {"005930": Decimal(70000), "486510": Decimal(10000)}
        bars = [LiveStockCandle(timestamp=now-timedelta(minutes=2), open_price=70000,
                               high_price=70000, low_price=70000, close_price=70000, volume=10)]
        client._domestic_candles.side_effect = lambda symbol, *args, **kwargs: (
            bars if symbol == "005930" else [bars[0].model_copy(update={"volume": Decimal(0)})])
        feed = PaperPriceFeed(client, clock=lambda: now)
        prices, candles = feed.read(["486510", "005930"], [], 1, now)
        self.assertEqual(set(candles), {"005930"})
        self.assertIn("486510", feed.unavailable)
        client._domestic_candles.side_effect = lambda *args, **kwargs: bars
        feed.read(["486510", "005930"], [], 1, now)
        self.assertEqual(feed.unavailable, {})

    def test_all_symbols_unavailable_do_not_produce_candles(self):
        client = Mock()
        client.current_prices.return_value = {"005930": Decimal(70000)}
        client._domestic_candles.side_effect = TossApiError("limited", status_code=429)
        feed = PaperPriceFeed(client)
        _, candles = feed.read(["005930"], [], 20, datetime.now(timezone.utc))
        self.assertEqual(candles, {})
        self.assertIn("005930", feed.unavailable)

    def test_slow_charts_are_followed_by_fresh_execution_prices(self):
        now = datetime(2026, 10, 12, 0, 40, tzinfo=timezone.utc)
        current = [now]
        calls = []
        client = Mock()
        bars = [LiveStockCandle(timestamp=now-timedelta(minutes=1), open_price=70000,
                               high_price=70000, low_price=70000, close_price=70000, volume=10)]
        def chart(*args, **kwargs):
            calls.append("chart")
            current[0] += timedelta(seconds=75)
            return bars
        def prices(*args):
            calls.append("prices")
            return {"005930": Decimal(71000)}
        client._domestic_candles.side_effect = chart
        client.current_prices.side_effect = prices
        feed = PaperPriceFeed(client, clock=lambda: current[0])
        prices, candles = feed.read(["005930"], [], 1, now)
        self.assertEqual(calls, ["chart", "prices"])
        self.assertEqual(feed.prices_requested_at, now+timedelta(seconds=75))
        self.assertEqual(prices["005930"], Decimal(71000))
        self.assertIn("005930", candles)

    def test_charts_that_age_during_loading_are_not_used(self):
        now = datetime(2026, 10, 12, 0, 40, tzinfo=timezone.utc)
        current = [now]
        client = Mock()
        bars = [LiveStockCandle(timestamp=now-timedelta(minutes=1), open_price=70000,
                               high_price=70000, low_price=70000, close_price=70000, volume=10)]
        def chart(*args, **kwargs):
            current[0] += timedelta(minutes=4)
            return bars
        client._domestic_candles.side_effect = chart
        client.current_prices.return_value = {"005930": Decimal(71000)}
        feed = PaperPriceFeed(client, clock=lambda: current[0])
        _, candles = feed.read(["005930"], [], 1, now)
        self.assertEqual(candles, {})
        self.assertIn("최신 완료 봉", feed.unavailable["005930"])
