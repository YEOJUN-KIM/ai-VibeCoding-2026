import unittest
from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest.mock import Mock, patch
from uuid import uuid4

from psycopg import sql

from auto_trader import database
from auto_trader.ml import quality_report
from auto_trader.ml.quality_report import KST
from auto_trader.ml.quality_worker import QualityReportWorker, next_quality_report_at


class QualityReportRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.schema = "test_ml_quality_" + uuid4().hex
        self.original_connect = database.connect
        with self.original_connect() as conn:
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
        self.addCleanup(self._cleanup_schema)

        def isolated():
            conn = self.original_connect()
            conn.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(self.schema)))
            return conn

        for module in (database, quality_report):
            patcher = patch.object(module, "connect", isolated)
            patcher.start()
            self.addCleanup(patcher.stop)
        database.initialize()

    def _cleanup_schema(self):
        with self.original_connect() as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)))

    def test_report_counts_missing_rows_and_upserts(self):
        report_date = date(2026, 10, 2)
        start = datetime(2026, 10, 2, 9, 0, tzinfo=KST)
        with quality_report.connect() as conn:
            for minute in range(3):
                event_at = start + timedelta(minutes=minute)
                conn.execute(
                    """
                    INSERT INTO ml_raw_candles(
                        source,symbol,interval,event_at,available_at,collected_at,
                        open_price,high_price,low_price,close_price,volume,raw_payload
                    ) VALUES ('TOSS','005930','1m',%s,%s,%s,100,101,99,100,10,'{}')
                    """,
                    (event_at, event_at + timedelta(minutes=1), event_at + timedelta(minutes=1)),
                )
            for minute in range(2):
                event_at = start + timedelta(minutes=minute)
                conn.execute(
                    """
                    INSERT INTO ml_market_indicator_candles(
                        source,indicator,interval,event_at,available_at,collected_at,
                        open_price,high_price,low_price,close_price,volume,raw_payload
                    ) VALUES ('TOSS_MARKET_INDICATOR','KOSPI','1m',%s,%s,%s,3000,3001,2999,3000,10,'{}')
                    """,
                    (event_at, event_at + timedelta(minutes=1), event_at + timedelta(minutes=1)),
                )

        generated_at = datetime(2026, 10, 2, 9, 3, 30, tzinfo=KST)
        first = quality_report.generate_quality_report(
            report_date,
            symbols=("005930",),
            indicators=("KOSPI",),
            generated_at=generated_at,
        )
        second = quality_report.generate_quality_report(
            report_date,
            symbols=("005930",),
            indicators=("KOSPI",),
            generated_at=generated_at,
        )

        self.assertEqual(first["expected_bars"], 3)
        self.assertEqual(first["stock_summary"]["missing_bars"], 0)
        self.assertEqual(first["indicator_summary"]["missing_bars"], 1)
        self.assertEqual(first["status"], "WARN")
        self.assertEqual(second["report_date"], report_date)
        with quality_report.connect() as conn:
            count = conn.execute("SELECT count(*) AS count FROM ml_data_quality_reports").fetchone()
        self.assertEqual(count["count"], 1)


class QualityReportScheduleTests(unittest.TestCase):
    def test_next_report_skips_weekend(self):
        now = datetime.fromisoformat("2026-10-02T16:00:00+09:00")
        self.assertEqual(
            next_quality_report_at(now, 15, 40).isoformat(),
            "2026-10-05T15:40:00+09:00",
        )


class QualityReportWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_once_updates_status(self):
        client = Mock(configured=True)
        client.kr_market_open_today.return_value = ("2026-10-02", True)
        worker = QualityReportWorker(client, ("005930",), ("KOSPI",))
        report = {"report_date": date(2026, 10, 2), "status": "WARN", "issues": ["missing"]}
        with patch("auto_trader.ml.quality_worker.generate_quality_report", return_value=report):
            result = await worker.run_once(date(2026, 10, 2))

        self.assertEqual(result, report)
        self.assertEqual(worker.status()["last_status"], "WARN")
        self.assertFalse(worker.status()["running"])


if __name__ == "__main__":
    unittest.main()
