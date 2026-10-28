"""Append-only opening/final repair and quality semantics in isolated PostgreSQL."""

import unittest
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import Mock, patch
from uuid import uuid4

from psycopg import sql

from auto_trader import database
from auto_trader.ml import data_pipeline, gap_repair, quality_report
from auto_trader.ml.collect_candles import collect_candles
from auto_trader.models import LiveStockCandle
from auto_trader.toss import TossApiError, TossClient


class GapRepairRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.schema = "test_gap_repair_" + uuid4().hex
        original = database.connect
        with original() as conn:
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
        def cleanup():
            with original() as conn:
                conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)))
        self.addCleanup(cleanup)
        def isolated():
            conn = original()
            conn.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(self.schema)))
            return conn
        for module in (database, data_pipeline, gap_repair, quality_report):
            p = patch.object(module, "connect", isolated)
            p.start()
            self.addCleanup(p.stop)
        database.initialize()
        self.start = datetime.fromisoformat("2026-10-28T09:00:00+09:00")
        self.now = self.start + timedelta(hours=7)
        self.client = Mock()

    def bar(self, minute):
        return LiveStockCandle(timestamp=self.start + timedelta(minutes=minute),
                               open_price=100, high_price=101, low_price=99, close_price=100, volume=10)

    def repair(self, **changes):
        return gap_repair.reconcile_session(self.client, self.start.date(), symbols=("005930",),
                                            indicators=(), clock=lambda: self.now, **changes)

    def save(self, minutes):
        return data_pipeline.save_raw_candles([
            data_pipeline.RawCandle.from_live_candle("005930", self.bar(m), collected_at=self.start + timedelta(minutes=m + 1))
            for m in minutes])

    def test_opening_recovery_is_append_only_idempotent_and_as_of_safe(self):
        self.client._domestic_candles.return_value = [self.bar(1)]
        collect_candles(self.client, ["005930"], count=20, collected_at=self.start + timedelta(minutes=2))
        self.now = self.start + timedelta(minutes=10)
        self.client._domestic_candles.return_value = [self.bar(0), self.bar(1)]
        first = self.repair(kind="opening_0910")
        second = self.repair(kind="opening_0910")
        self.assertEqual((first["inserted_rows"], second["inserted_rows"]), (1, 0))
        with data_pipeline.connect() as conn:
            rows = conn.execute("SELECT event_at,available_at,collected_at,close_price FROM ml_raw_candles ORDER BY event_at").fetchall()
        self.assertEqual(len(rows), 2)
        self.assertEqual((rows[0]["event_at"], rows[0]["available_at"], rows[0]["collected_at"]), (self.start, self.now, self.now))
        self.assertEqual(rows[1]["available_at"], self.start + timedelta(minutes=2))
        available = data_pipeline.available_candles("005930", start_at=self.start, end_at=self.now,
                                                   as_of=self.start + timedelta(minutes=5))
        self.assertEqual([row["event_at"] for row in available], [self.start + timedelta(minutes=1)])

    def test_final_reconciliation_recovers_multiple_gaps_and_separates_health(self):
        missing = {0, 120, 390}
        self.save([m for m in range(391) if m not in missing])
        self.client._domestic_candles.return_value = [self.bar(m) for m in range(391)]
        run = data_pipeline.start_collection_run("TOSS", "1m", ["005930"], 20, self.start + timedelta(minutes=2))
        data_pipeline.finish_collection_run(run, inserted_rows=0, duplicate_rows=1,
                                            errors={"005930": "토스 API 요청 실패(429)"}, finished_at=self.now)
        result = self.repair(kind="final")
        self.assertEqual(result["inserted_rows"], 3)
        report = quality_report.generate_quality_report(self.start.date(), symbols=("005930",), indicators=(), generated_at=self.now)
        self.assertEqual((report["data_completeness_status"], report["collection_health_status"], report["status"]), ("PASS", "WARN", "WARN"))
        self.assertEqual(report["stock_summary"]["items"]["005930"]["missing_timestamps"], [])
        self.assertEqual(report["collection_summary"]["error_counts"], {"RATE_LIMIT": 1})
        self.assertEqual(report["reconciliation_summary"]["recovered_rows"], 3)
        self.assertEqual(report["reconciliation_summary"]["unrecovered_rows"], 0)
        persisted = quality_report.quality_report_for_date(self.start.date())
        self.assertEqual(persisted["data_completeness_status"], "PASS")
        self.assertEqual(persisted["collection_health_status"], "WARN")
        skipped = self.repair(kind="final", skip_completed=True)
        self.assertEqual(skipped["status"], "SKIPPED")

    def test_provider_absent_opening_bar_is_not_synthesized(self):
        self.save(range(1, 391))
        self.client._domestic_candles.return_value = [self.bar(m) for m in range(1, 391)]
        result = self.repair(kind="final")
        self.assertEqual(result["inserted_rows"], 0)
        report = quality_report.generate_quality_report(self.start.date(), symbols=("005930",), indicators=(), generated_at=self.now)
        info = report["stock_summary"]["items"]["005930"]
        self.assertEqual(info["missing_timestamps"], [self.start.isoformat()])
        self.assertEqual(info["provider_absent_timestamps"], [self.start.isoformat()])
        self.assertEqual(info["gap_origin"], "PROVIDER_ABSENT")
        self.assertEqual(report["data_completeness_status"], "WARN")
        self.assertEqual(report["expected_bars"], 391)

    def test_incomplete_provider_window_is_unverified(self):
        self.client._domestic_candles.return_value = [self.bar(390)] * 600
        result = self.repair()
        info = result["series"]["stock:005930"]
        self.assertFalse(info["provider_window_complete"])
        self.assertEqual(info["provider_absent_timestamps"], [])
        self.assertIn(self.start.isoformat(), info["unverified_timestamps"])

    def test_indicator_recovery_retains_receive_time(self):
        self.client._market_indicator_candles.return_value = [self.bar(0)]
        self.now = self.start + timedelta(minutes=10)
        result = gap_repair.reconcile_session(self.client, self.start.date(), symbols=(), indicators=("KOSPI", "KOSDAQ"), clock=lambda: self.now)
        self.assertEqual(result["inserted_rows"], 2)
        for indicator in ("KOSPI", "KOSDAQ"):
            self.assertEqual(data_pipeline.available_market_indicator_candles(
                indicator, start_at=self.start, end_at=self.now, as_of=self.start + timedelta(minutes=5)), [])
        with data_pipeline.connect() as conn:
            rows = conn.execute("SELECT available_at,collected_at FROM ml_market_indicator_candles").fetchall()
        self.assertTrue(all(row["available_at"] == row["collected_at"] == self.now for row in rows))

    def test_failed_fetch_is_reported_and_later_run_recovers(self):
        self.client._domestic_candles.side_effect = [TossApiError("timeout", error_code="timeout"), [self.bar(0)]]
        first = self.repair()
        self.assertEqual(first["status"], "FAILED")
        self.assertEqual(first["error_details"]["stock:005930"]["category"], "TIMEOUT")
        second = self.repair()
        self.assertEqual(second["inserted_rows"], 1)

    def test_transient_chart_get_recovers_before_collecting(self):
        client = TossClient(client_id="test", client_secret="test")
        payload = {"result": {"candles": [{"timestamp": self.start.isoformat(), "openPrice": "100",
                    "highPrice": "101", "lowPrice": "99", "closePrice": "100", "volume": "10"}]}}
        with patch.object(client, "access_token", return_value="token"), patch.object(
            client, "_json_request_once", side_effect=[TossApiError("timeout", error_code="timeout"), payload]
        ) as request, patch.object(client._request_waiter, "wait") as wait:
            result = collect_candles(client, ["005930"], count=20, collected_at=self.now)
        self.assertEqual(result["inserted_rows"], 1)
        self.assertEqual(result["status"], "COMPLETED")
        self.assertEqual(request.call_count, 2)
        wait.assert_called_once_with(0.5)

    def test_exact_timestamps_ignore_other_sources_and_off_minute_rows(self):
        self.save([0, 2])
        other = data_pipeline.RawCandle.from_live_candle("005930", self.bar(1), collected_at=self.start + timedelta(minutes=2), source="OTHER")
        off_minute = self.bar(1).model_copy(update={"timestamp": self.start + timedelta(minutes=1, seconds=30)})
        data_pipeline.save_raw_candles([other, data_pipeline.RawCandle.from_live_candle(
            "005930", off_minute, collected_at=self.start + timedelta(minutes=2, seconds=30))])
        report = quality_report.generate_quality_report(self.start.date(), symbols=("005930",), indicators=(),
                                                        generated_at=self.start + timedelta(minutes=3))
        self.assertEqual(report["stock_summary"]["items"]["005930"]["missing_timestamps"], [(self.start + timedelta(minutes=1)).isoformat()])

    def test_receipt_clock_is_sampled_after_provider_response(self):
        self.now = self.start + timedelta(minutes=9)
        def fetch(*args, **kwargs):
            self.now += timedelta(minutes=1)
            return [self.bar(0)]
        self.client._domestic_candles.side_effect = fetch
        self.repair()
        with data_pipeline.connect() as conn:
            row = conn.execute("SELECT available_at FROM ml_raw_candles").fetchone()
        self.assertEqual(row["available_at"], self.start + timedelta(minutes=10))
