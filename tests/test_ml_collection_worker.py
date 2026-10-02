import asyncio
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch

from auto_trader.ml.collection_worker import CandleCollectionWorker, next_collection_at


class CollectionScheduleTests(unittest.TestCase):
    def test_before_open_schedules_first_completed_minute(self):
        result = next_collection_at(datetime.fromisoformat("2026-10-02T08:00:00+09:00"))
        self.assertEqual(result.isoformat(), "2026-10-02T09:01:02+09:00")

    def test_during_market_aligns_to_next_minute(self):
        result = next_collection_at(datetime.fromisoformat("2026-10-02T11:34:30+09:00"))
        self.assertEqual(result.isoformat(), "2026-10-02T11:35:02+09:00")

    def test_after_friday_close_skips_weekend(self):
        result = next_collection_at(datetime.fromisoformat("2026-10-02T16:00:00+09:00"))
        self.assertEqual(result.isoformat(), "2026-10-05T09:01:02+09:00")


class CollectionWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_once_updates_status(self):
        client = Mock(configured=True)
        client.kr_market_open_today.return_value = ("2026-10-02", True)
        worker = CandleCollectionWorker(client, ("005930",), count=20)
        stock_result = {
            "run_id": 7,
            "status": "COMPLETED",
            "inserted_rows": 3,
            "duplicate_rows": 17,
            "errors": {},
        }
        indicator_result = {
            "run_id": 8,
            "status": "COMPLETED",
            "inserted_rows": 2,
            "duplicate_rows": 38,
            "errors": {},
        }
        with patch("auto_trader.ml.collection_worker.collect_candles", return_value=stock_result), patch(
            "auto_trader.ml.collection_worker.collect_market_indicators",
            return_value=indicator_result,
        ):
            returned = await worker.run_once(
                datetime(2026, 10, 2, tzinfo=timezone.utc)
            )

        self.assertEqual(returned["inserted_rows"], 5)
        self.assertEqual(returned["market_indicators"]["run_id"], 8)
        self.assertEqual(worker.status()["total_inserted_rows"], 5)
        self.assertEqual(worker.status()["last_market_indicator_run_id"], 8)
        self.assertFalse(worker.status()["running"])

    async def test_holiday_skips_candle_requests(self):
        client = Mock(configured=True)
        client.kr_market_open_today.return_value = ("2026-10-03", False)
        worker = CandleCollectionWorker(client, ("005930",), count=20)
        with patch("auto_trader.ml.collection_worker.collect_candles") as collect, patch(
            "auto_trader.ml.collection_worker.collect_market_indicators"
        ) as collect_indicators:
            result = await worker.run_once()

        collect.assert_not_called()
        collect_indicators.assert_not_called()
        self.assertEqual(result["status"], "SKIPPED")
        self.assertEqual(worker.status()["last_status"], "SKIPPED")

    async def test_disabled_worker_does_not_create_task(self):
        worker = CandleCollectionWorker(Mock(configured=True), ("005930",), enabled=False)
        with patch.object(asyncio, "create_task", new=Mock()) as create_task:
            await worker.start()
        create_task.assert_not_called()
        self.assertEqual(worker.status()["phase"], "disabled")

    async def test_stop_handles_completed_task(self):
        worker = CandleCollectionWorker(Mock(configured=True), ("005930",))
        worker._task = Mock()
        worker._task.done.return_value = True
        await worker.stop()
        self.assertEqual(worker.status()["phase"], "stopped")


if __name__ == "__main__":
    unittest.main()
