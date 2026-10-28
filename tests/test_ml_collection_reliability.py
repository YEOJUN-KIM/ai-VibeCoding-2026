import json
import unittest
from datetime import datetime
from urllib.error import URLError
from urllib.request import Request
from unittest.mock import AsyncMock, Mock, patch

from auto_trader.ml.collection_errors import error_category
from auto_trader.ml.collection_worker import CandleCollectionWorker, next_worker_event
from auto_trader.ml.gap_repair import expected_timestamps
from auto_trader.ml.quality_worker import QualityReportWorker
from auto_trader.toss import TossApiError, TossClient
from auto_trader.models import LiveStockCandle
from auto_trader.ml import collect_candles, collect_market_indicators


class ChartRetryTests(unittest.TestCase):
    def test_transport_preserves_timeout_and_invalid_response_categories(self):
        for error, category in ((URLError(TimeoutError()), "timeout"), (json.JSONDecodeError("bad", "", 0), "invalid-response")):
            with self.subTest(category=category):
                client = TossClient(client_id="test", client_secret="test")
                with patch("auto_trader.brokers.toss.transport.urlopen", side_effect=error):
                    with self.assertRaises(TossApiError) as raised:
                        client._json_request_once(Request("https://example.invalid/api/v1/candles"))
                self.assertEqual(raised.exception.error_code, category)
                self.assertIs(raised.exception.__cause__, error)

    def test_transient_get_budget_is_shared_with_429_and_cooldown(self):
        client = TossClient(client_id="test", client_secret="test")
        client._group_rate_limit_until["MARKET_DATA_CHART"] = 12
        with patch.object(client, "_json_request_once", side_effect=[
            TossApiError("busy", status_code=429), TossApiError("server", status_code=503), {"ok": True}
        ]) as request, patch("auto_trader.brokers.toss.transport.monotonic", return_value=10), patch.object(client._request_waiter, "wait") as wait:
            self.assertEqual(client._json_request(Request("https://example.invalid/api/v1/candles")), {"ok": True})
        self.assertEqual(request.call_count, 3)
        self.assertEqual([call.args[0] for call in wait.call_args_list], [2, 2])

    def test_chart_timeouts_network_and_5xx_are_bounded(self):
        for error in (TossApiError("timeout", error_code="timeout"), TossApiError("network", error_code="network"), TossApiError("server", status_code=503)):
            with self.subTest(error=error):
                client = TossClient(client_id="test", client_secret="test")
                with patch.object(client, "_json_request_once", side_effect=error) as request, patch.object(client._request_waiter, "wait") as wait:
                    with self.assertRaises(TossApiError):
                        client._json_request(Request("https://example.invalid/api/v1/market-indicators/KOSPI/candles"))
                self.assertEqual(request.call_count, 3)
                self.assertEqual([call.args[0] for call in wait.call_args_list], [0.5, 1])

    def test_post_orders_non_chart_get_and_permanent_errors_are_not_retried(self):
        for path, method, error in (
            ("/api/v1/orders", "POST", TossApiError("server", status_code=503)),
            ("/api/v1/orders/123/cancel", "POST", TossApiError("timeout", error_code="timeout")),
            ("/api/v1/accounts", "GET", TossApiError("network", error_code="network")),
            ("/api/v1/candles", "GET", TossApiError("bad request", status_code=400)),
            ("/api/v1/candles", "GET", TossApiError("bad json", error_code="invalid-response")),
        ):
            with self.subTest(path=path, method=method):
                client = TossClient(client_id="test", client_secret="test")
                with patch.object(client, "_json_request_once", side_effect=error) as request:
                    with self.assertRaises(TossApiError):
                        client._json_request(Request("https://example.invalid" + path, method=method))
                request.assert_called_once()

    def test_error_classification(self):
        for error, expected in ((URLError(TimeoutError()), "TIMEOUT"), (URLError("offline"), "NETWORK"),
                                (TossApiError("limit", status_code=429), "RATE_LIMIT"),
                                (TossApiError("server", status_code=502), "HTTP_5XX"),
                                (TossApiError("invalid", status_code=404), "HTTP_4XX"),
                                (json.JSONDecodeError("bad", "", 0), "INVALID_RESPONSE")):
            self.assertEqual(error_category(error), expected)


class ReconciliationScheduleTests(unittest.TestCase):
    def test_opening_slots_catch_up_after_slow_regular_collection(self):
        now = datetime.fromisoformat("2026-10-28T09:05:30+09:00")
        at, kind = next_worker_event(now, set())
        self.assertEqual((at, kind), (now, "opening_0905"))
        at, kind = next_worker_event(now, {(now.date(), kind)})
        self.assertEqual(kind, "regular")
        now = now.replace(minute=10)
        self.assertEqual(next_worker_event(now, {(now.date(), "opening_0905")})[1], "opening_0910")

    def test_final_at_1535_and_restart_catchup(self):
        now = datetime.fromisoformat("2026-10-28T15:32:00+09:00")
        at, kind = next_worker_event(now, set())
        self.assertEqual((at.isoformat(), kind), ("2026-10-28T15:35:00+09:00", "final"))
        now = now.replace(hour=17)
        self.assertEqual(next_worker_event(now, set()), (now, "final"))
        self.assertEqual(next_worker_event(now, {(now.date(), "final")})[1], "regular")

    def test_grid_keeps_391_bars_and_excludes_uncompleted_minutes(self):
        now = datetime.fromisoformat("2026-10-28T09:01:02+09:00")
        self.assertEqual(len(expected_timestamps(now.date(), now)), 1)
        self.assertEqual(len(expected_timestamps(now.date(), now.replace(hour=16))), 391)


class CollectorReceiptTests(unittest.TestCase):
    def test_regular_collectors_stamp_response_time_instead_of_request_start(self):
        from contextlib import ExitStack
        started = datetime.fromisoformat("2026-10-28T09:01:02+09:00")
        received = started.replace(minute=10, second=0)
        bar = LiveStockCandle(timestamp=started.replace(minute=0, second=0), open_price=100,
                              high_price=101, low_price=99, close_price=100, volume=1)
        for module, method, saver, quality, key in (
            (collect_candles, "collect_candles", "save_raw_candles", "candle_quality_report", "005930"),
            (collect_market_indicators, "collect_market_indicators", "save_market_indicator_candles", "market_indicator_quality_report", "KOSPI"),
        ):
            with self.subTest(collector=method), ExitStack() as stack:
                client = Mock()
                client._domestic_candles.return_value = [bar]
                client._market_indicator_candles.return_value = [bar]
                clock = stack.enter_context(patch.object(module, "datetime", wraps=datetime))
                clock.now.side_effect = [started, received, received]
                stack.enter_context(patch.object(module, "start_collection_run", return_value=1))
                stack.enter_context(patch.object(module, "finish_collection_run", return_value="COMPLETED"))
                stack.enter_context(patch.object(module, quality, return_value={}))
                save = stack.enter_context(patch.object(module, saver, return_value=(1, 0)))
                getattr(module, method)(client, [key], count=20)
                candle = save.call_args.args[0][0]
                self.assertEqual((candle.available_at, candle.collected_at), (received, received))


class ReconciliationWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_holiday_does_not_reconcile(self):
        client = Mock()
        client.kr_market_open_today.return_value = ("2026-10-28", False)
        worker = CandleCollectionWorker(client, ("005930",))
        with patch("auto_trader.ml.collection_worker.reconcile_session") as repair:
            result = await worker.run_reconciliation_once("final", datetime.now().date())
        repair.assert_not_called()
        self.assertEqual(result["status"], "SKIPPED")

    async def test_quality_report_follows_final_reconciliation(self):
        now = datetime.fromisoformat("2026-10-28T15:40:00+09:00")
        client = Mock()
        client.kr_market_open_today.return_value = (now.date().isoformat(), True)
        worker = QualityReportWorker(client, ("005930",), ("KOSPI",))
        events = []
        with patch("auto_trader.ml.quality_worker.datetime", wraps=datetime) as clock, patch(
            "auto_trader.ml.quality_worker.reconcile_session", side_effect=lambda *a, **k: events.append("repair")
        ), patch("auto_trader.ml.quality_worker.generate_quality_report", side_effect=lambda *a, **k: events.append("report") or {"status": "WARN"}):
            clock.now.return_value = now
            await worker.run_once(now.date())
        self.assertEqual(events, ["repair", "report"])
