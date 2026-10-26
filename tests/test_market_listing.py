"""Exercise the listing HTTP route through the actual broker filtering logic."""

import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.parse import urlencode

from auto_trader import main, runtime
from auto_trader.api import market
from auto_trader.auth import require_user
from auto_trader.toss import TossClient


class MarketListingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = TossClient(client_id="test", client_secret="test")
        self.client._domestic_stock_universe = Mock(return_value=[
            {"symbol": "005930", "name": "삼성전자", "market": "KOSPI",
             "securityType": "STOCK", "isCommonShare": True},
            {"symbol": "247540", "name": "에코프로비엠", "market": "KOSDAQ",
             "securityType": "STOCK", "isCommonShare": True},
            {"symbol": "069500", "name": "KODEX 200", "market": "KOSPI",
             "securityType": "ETF", "isCommonShare": False},
        ])
        self.client._authorized_json_request = Mock(side_effect=AssertionError("Unexpected external request"))
        for p in (patch.object(runtime, "toss_client", self.client),
                  patch.object(market, "favorite_symbols", return_value={"005930"})):
            p.start()
            self.addCleanup(p.stop)
        previous = main.app.dependency_overrides.copy()
        main.app.dependency_overrides[require_user] = lambda: SimpleNamespace(id=11)
        self.addCleanup(self.restore_overrides, previous)

    @staticmethod
    def restore_overrides(previous):
        main.app.dependency_overrides.clear()
        main.app.dependency_overrides.update(previous)

    async def request(self, **filters):
        messages = []
        async def receive():
            await asyncio.Event().wait()
        async def send(message):
            messages.append(message)
        path = "/live/stocks/list"
        scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
                 "http_version": "1.1", "method": "GET", "scheme": "http", "path": path,
                 "raw_path": path.encode(), "query_string": urlencode({"include_quotes": "false", **filters}).encode(),
                 "root_path": "", "headers": [(b"host", b"localhost")],
                 "server": ("localhost", 80), "client": ("127.0.0.1", 1234)}
        await main.app(scope, receive, send)
        self.assertEqual(next(m["status"] for m in messages if m["type"] == "http.response.start"), 200)
        self.client._authorized_json_request.assert_not_called()
        return json.loads(b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body"))

    async def test_default_market_returns_stocks_and_favorites(self):
        data = await self.request()
        self.assertEqual(data["total"], 3)
        results = {item["symbol"]: item for item in data["results"]}
        self.assertEqual(set(results), {"005930", "247540", "069500"})
        self.assertTrue(results["005930"]["is_favorite"])
        self.assertFalse(results["247540"]["is_favorite"])

    async def test_all_kospi_kosdaq_filters_reach_broker(self):
        expected = {"ALL": {"005930", "247540", "069500"},
                    "KOSPI": {"005930", "069500"}, "KOSDAQ": {"247540"}}
        for selected, symbols in expected.items():
            with self.subTest(market=selected):
                data = await self.request(market=selected)
                self.assertEqual({item["symbol"] for item in data["results"]}, symbols)

    async def test_market_combines_with_query_and_security_filters(self):
        data = await self.request(market="KOSPI", security_type="COMMON", q="삼성")
        self.assertEqual([item["symbol"] for item in data["results"]], ["005930"])
        data = await self.request(market="KOSPI", security_type="ETF")
        self.assertEqual([item["symbol"] for item in data["results"]], ["069500"])
        data = await self.request(market="KOSDAQ", security_type="ETF")
        self.assertEqual(data["results"], [])
