import unittest
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import Mock, patch

from auto_trader.ml.collect_market_indicators import collect_market_indicators
from auto_trader.models import LiveStockCandle


class MarketIndicatorCollectorTests(unittest.TestCase):
    def test_only_completed_bars_are_saved(self):
        collected_at = datetime.fromisoformat("2026-10-02T09:02:30+09:00")
        client = Mock()
        client._market_indicator_candles.return_value = [
            LiveStockCandle(
                timestamp="2026-10-02T09:01:00+09:00",
                open_price=Decimal("3400"), high_price=Decimal("3410"),
                low_price=Decimal("3390"), close_price=Decimal("3405"), volume=Decimal("100"),
            ),
            LiveStockCandle(
                timestamp="2026-10-02T09:02:00+09:00",
                open_price=Decimal("3405"), high_price=Decimal("3411"),
                low_price=Decimal("3400"), close_price=Decimal("3408"), volume=Decimal("50"),
            ),
        ]
        with patch("auto_trader.ml.collect_market_indicators.start_collection_run", return_value=12), patch(
            "auto_trader.ml.collect_market_indicators.save_market_indicator_candles",
            return_value=(1, 0),
        ) as save, patch(
            "auto_trader.ml.collect_market_indicators.finish_collection_run",
            return_value="COMPLETED",
        ), patch(
            "auto_trader.ml.collect_market_indicators.market_indicator_quality_report",
            return_value={"row_count": 1},
        ):
            result = collect_market_indicators(
                client, ["kospi"], count=20, collected_at=collected_at
            )

        saved = list(save.call_args.args[0])
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0].indicator, "KOSPI")
        self.assertEqual(result["inserted_rows"], 1)


if __name__ == "__main__":
    unittest.main()
