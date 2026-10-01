import unittest
from datetime import datetime
from decimal import Decimal
from auto_trader.models import LiveStockCandle
from auto_trader.chart_candles import chart_candles, bucket_start

def candle(time, price, volume):
    return LiveStockCandle(timestamp=datetime.fromisoformat(time),open_price=price,high_price=price+2,low_price=price-2,close_price=price+1,volume=volume)

class ChartCandleTests(unittest.TestCase):
    def test_hour_ohlcv_and_day_filter(self):
        rows=[candle('2026-09-30T09:00:00+09:00',90,1),candle('2026-10-01T09:01:00+09:00',100,10),candle('2026-10-01T09:59:00+09:00',105,20),candle('2026-10-01T10:00:00+09:00',99,5)]
        bars=chart_candles(rows,'1h','1D')
        self.assertEqual(len(bars),2)
        self.assertEqual((bars[0].open_price,bars[0].high_price,bars[0].low_price,bars[0].close_price,bars[0].volume),(100,107,98,106,30))
        self.assertEqual(bars[0].timestamp.hour,9)
        self.assertEqual(rows[1].volume,10)

    def test_calendar_boundaries_in_kst(self):
        rows=[candle('2026-09-30T15:00:00+09:00',100,10),candle('2026-10-01T15:00:00+09:00',105,20)]
        self.assertEqual(len(chart_candles(rows,'1mo','1Y')),2)
        self.assertEqual(len(chart_candles(rows,'1w','1Y')),1)
        self.assertEqual(chart_candles(rows,'1y','5Y')[0].volume,30)
        self.assertEqual(bucket_start(datetime.fromisoformat('2026-10-04T16:00:00+00:00'),'1w').date().isoformat(),'2026-10-05')

    def test_minute_bars_are_not_merged(self):
        rows=[candle('2026-10-01T09:01:00+09:00',100,10),candle('2026-10-01T09:02:00+09:00',105,20)]
        self.assertEqual(len(chart_candles(rows,'1m','1D')),2)
