import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

from psycopg import sql

from auto_trader import database
from auto_trader.ml import data_pipeline
from auto_trader.ml.data_pipeline import RawCandle


class MlDataPipelineTests(unittest.TestCase):
    def setUp(self):
        self.schema = "test_ml_data_" + uuid4().hex
        self.original_connect = database.connect
        with self.original_connect() as conn:
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
        self.addCleanup(self._cleanup_schema)

        def isolated():
            conn = self.original_connect()
            conn.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(self.schema)))
            return conn

        for module in (database, data_pipeline):
            patcher = patch.object(module, "connect", isolated)
            patcher.start()
            self.addCleanup(patcher.stop)
        database.initialize()
        self.base_at = datetime(2026, 10, 2, 0, 0, tzinfo=timezone.utc)

    def _cleanup_schema(self):
        with self.original_connect() as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)))

    def candle(self, minute=0, available_delay=1, **changes):
        values = {
            "source": "TOSS",
            "symbol": "005930",
            "interval": "1m",
            "event_at": self.base_at + timedelta(minutes=minute),
            "available_at": self.base_at + timedelta(minutes=minute + available_delay),
            "collected_at": self.base_at + timedelta(minutes=minute + available_delay),
            "open_price": Decimal("70000"),
            "high_price": Decimal("70100"),
            "low_price": Decimal("69900"),
            "close_price": Decimal("70050"),
            "volume": Decimal("100"),
        }
        values.update(changes)
        return RawCandle(**values)

    def test_same_candles_are_idempotent(self):
        candles = [self.candle(0), self.candle(1)]
        self.assertEqual(data_pipeline.save_raw_candles(candles), (2, 0))
        self.assertEqual(data_pipeline.save_raw_candles(candles), (0, 2))

        with data_pipeline.connect() as conn:
            count = conn.execute("SELECT count(*) AS count FROM ml_raw_candles").fetchone()["count"]
        self.assertEqual(count, 2)

    def test_as_of_query_excludes_data_not_yet_available(self):
        data_pipeline.save_raw_candles([self.candle(0), self.candle(1, available_delay=10)])

        rows = data_pipeline.available_candles(
            "005930",
            start_at=self.base_at,
            end_at=self.base_at + timedelta(minutes=20),
            as_of=self.base_at + timedelta(minutes=5),
        )
        self.assertEqual([row["event_at"] for row in rows], [self.base_at])

    def test_quality_report_counts_gaps_delays_and_zero_volume(self):
        data_pipeline.save_raw_candles(
            [self.candle(0), self.candle(2, available_delay=6, volume=Decimal("0"))]
        )
        report = data_pipeline.candle_quality_report("005930")

        self.assertEqual(report["row_count"], 2)
        self.assertEqual(report["missing_intervals_between_rows"], 1)
        self.assertEqual(report["delayed_over_5m"], 1)
        self.assertEqual(report["zero_volume_rows"], 1)

    def test_invalid_ohlc_is_rejected_before_insert(self):
        with self.assertRaisesRegex(ValueError, "고가"):
            data_pipeline.save_raw_candles([self.candle(high_price=Decimal("69000"))])


if __name__ == "__main__":
    unittest.main()
