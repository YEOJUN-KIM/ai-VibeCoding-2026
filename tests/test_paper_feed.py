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
        feed = PaperPriceFeed(client)
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
