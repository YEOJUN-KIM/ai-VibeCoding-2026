import gzip
import io
import json
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch
from urllib.error import HTTPError

from auto_trader.toss import TossApiError, TossClient


class FakeResponse:
    def __init__(self, payload, *, compressed=False):
        self.payload = payload
        self.compressed = compressed
        self.headers = {"Content-Encoding": "gzip"} if compressed else {}
    def __enter__(self):
        return self
    def __exit__(self, *_):
        return False
    def read(self):
        raw = json.dumps(self.payload).encode()
        return gzip.compress(raw) if self.compressed else raw


class TossClientTests(unittest.TestCase):
    @patch("auto_trader.toss.urlopen")
    def test_rate_limit_blocks_follow_up_requests_during_backoff(self, mocked):
        error = HTTPError("https://example.test", 429, "Too Many Requests", {"Retry-After": "20"}, None)
        error.read = lambda: json.dumps({
            "error": {"code": "rate-limit-exceeded", "message": "요청 한도 초과"}
        }).encode()
        mocked.side_effect = error
        client = TossClient(client_id="id", client_secret="secret")

        with self.assertRaisesRegex(TossApiError, "rate-limit-exceeded"):
            client.access_token()
        with self.assertRaisesRegex(TossApiError, "요청 한도 대기 중"):
            client.access_token()

        self.assertEqual(mocked.call_count, 1)

    @patch("auto_trader.toss.urlopen")
    def test_rate_limit_uses_server_reset_for_only_that_group(self, mocked):
        def limited(*_args, **_kwargs):
            error = HTTPError("https://example.test", 429, "Too Many Requests", {"X-RateLimit-Reset": "2"}, None)
            error.read = lambda: b'{"error":{"code":"rate-limit-exceeded"}}'
            raise error

        mocked.side_effect = limited
        client = TossClient(client_id="id", client_secret="secret")
        with self.assertRaises(TossApiError):
            client.access_token()
        remaining = client._group_rate_limit_until["AUTH"] - __import__("time").monotonic()
        self.assertGreater(remaining, 2)
        self.assertLessEqual(remaining, 2.1)
        self.assertNotIn("ACCOUNT", client._group_rate_limit_until)

    def test_get_rate_limit_retries_after_shared_cooldown(self):
        from urllib.request import Request
        client = TossClient(client_id="id", client_secret="secret")
        client._group_rate_limit_until["MARKET_DATA_CHART"] = 12
        error = TossApiError("limited", status_code=429)
        with patch.object(client, "_json_request_once", side_effect=[error, {"ok": True}]) as request, \
             patch("auto_trader.toss.monotonic", return_value=10), \
             patch.object(client._request_waiter, "wait") as wait:
            result = client._json_request(Request("https://x/api/v1/candles"))
        self.assertEqual(result, {"ok": True})
        self.assertEqual(request.call_count, 2)
        wait.assert_called_once_with(2)

    def test_order_submission_is_never_retried_on_rate_limit(self):
        from urllib.request import Request
        client = TossClient(client_id="id", client_secret="secret")
        with patch.object(client, "_json_request_once", side_effect=TossApiError("limited", status_code=429)) as request:
            with self.assertRaises(TossApiError):
                client._json_request(Request("https://x/api/v1/orders", method="POST"))
        self.assertEqual(request.call_count, 1)

    def test_get_rate_limit_retries_are_bounded(self):
        from urllib.request import Request
        client = TossClient(client_id="id", client_secret="secret")
        with patch.object(client, "_json_request_once", side_effect=TossApiError("limited", status_code=429)) as request, \
             patch.object(client._request_waiter, "wait"):
            with self.assertRaises(TossApiError):
                client._json_request(Request("https://x/api/v1/candles"))
        self.assertEqual(request.call_count, 3)

    def test_concurrent_candle_requests_share_one_upstream_call(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from time import sleep
        client = TossClient(client_id="id", client_secret="secret")
        barrier = Barrier(4)
        rows = [{"timestamp": "2026-10-01T09:00:00+09:00", "openPrice": "100", "highPrice": "110", "lowPrice": "90", "closePrice": "105", "volume": "10"}]
        def response(*_):
            sleep(0.03)
            return {"result": {"candles": rows}}
        def read(_):
            barrier.wait(timeout=5)
            return client._domestic_candles("005930", "1d", 1)
        with patch.object(client, "_authorized_json_request", side_effect=response) as upstream:
            with ThreadPoolExecutor(max_workers=4) as pool:
                results = list(pool.map(read, range(4)))
        self.assertEqual(upstream.call_count, 1)
        self.assertTrue(all(result[0].close_price == Decimal(105) for result in results))

    def test_requests_are_classified_by_rate_limit_group(self):
        from urllib.request import Request

        self.assertEqual(TossClient._request_group(Request("https://x/api/v1/accounts")), "ACCOUNT")
        self.assertEqual(TossClient._request_group(Request("https://x/api/v1/holdings")), "ASSET")
        self.assertEqual(TossClient._request_group(Request("https://x/api/v1/buying-power")), "ORDER_INFO")
        self.assertEqual(TossClient._request_group(Request("https://x/api/v1/prices")), "MARKET_DATA")

    @patch("auto_trader.toss.urlopen")
    def test_token_is_cached_and_accounts_are_read(self, mocked):
        mocked.side_effect = [FakeResponse({"access_token": "secret-token", "expires_in": 3600}),
                              FakeResponse({"result": [{"accountSeq": 1}]})]
        client = TossClient(client_id="id", client_secret="secret")
        self.assertEqual(client.accounts(), [{"accountSeq": 1}])
        self.assertEqual(client.accounts(), [{"accountSeq": 1}])
        self.assertEqual(client.access_token(), "secret-token")
        self.assertEqual(mocked.call_count, 2)

    @patch("auto_trader.toss.urlopen")
    def test_current_prices_batches_symbols_in_one_market_data_request(self, mocked):
        mocked.side_effect = [
            FakeResponse({"access_token": "token", "expires_in": 3600}),
            FakeResponse({"result": [
                {"symbol": "005930", "lastPrice": "71500", "currency": "KRW"},
                {"symbol": "005380", "lastPrice": "211000", "currency": "KRW"},
            ]}),
        ]
        client = TossClient(client_id="id", client_secret="secret")

        prices = client.current_prices(["005930", "005380", "005930"])

        self.assertEqual(prices, {"005930": Decimal("71500"), "005380": Decimal("211000")})
        self.assertEqual(mocked.call_count, 2)
        self.assertIn("symbols=005930%2C005380", mocked.call_args.args[0].full_url)

    def test_stock_directory_warmup_populates_universe_and_rankings(self):
        client = TossClient(client_id="id", client_secret="secret")
        with patch.object(client, "_domestic_stock_universe", return_value=[{"symbol": "005930"}]), \
             patch.object(client, "_domestic_trading_amount_rankings", return_value=(None, [{"rank": 1, "symbol": "005930"}])), \
             patch.object(client, "domestic_sparklines", return_value={"005930": []}) as sparklines:
            self.assertEqual(client.warm_domestic_stock_directory(), (1, 1))
            sparklines.assert_called_once_with(["005930"], period="1D")

    def test_missing_configuration(self):
        with self.assertRaises(TossApiError):
            TossClient(client_id="", client_secret="").access_token()

    def test_live_order_is_blocked_by_default(self):
        client = TossClient(client_id="id", client_secret="secret", live_trading_enabled=False)
        with self.assertRaisesRegex(TossApiError, "안전 잠금"):
            client.create_limit_order(
                symbol="005930", side="BUY", quantity=Decimal("1"),
                price=Decimal("70000"), client_order_id="live-order-001",
            )

    @patch("auto_trader.toss.urlopen")
    def test_live_limit_order_uses_fixed_safe_shape(self, mocked):
        mocked.side_effect = [
            FakeResponse({"access_token": "token", "expires_in": 3600}),
            FakeResponse({"result": [{"accountSeq": 7}]}),
            FakeResponse({"result": {"orderId": "order-1", "clientOrderId": "live-order-001"}}),
        ]
        client = TossClient(
            client_id="id", client_secret="secret", live_trading_enabled=True
        )
        result = client.create_limit_order(
            symbol="005930", side="BUY", quantity=Decimal("1"),
            price=Decimal("70000"), client_order_id="live-order-001",
        )
        self.assertEqual(result["orderId"], "order-1")
        request = mocked.call_args_list[2].args[0]
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("X-tossinvest-account"), "7")
        self.assertEqual(json.loads(request.data), {
            "symbol": "005930", "side": "BUY", "orderType": "LIMIT",
            "quantity": "1", "price": "70000", "timeInForce": "DAY",
            "clientOrderId": "live-order-001", "confirmHighValueOrder": False,
        })

    @patch("auto_trader.toss.urlopen")
    def test_order_detail_is_read_only_and_cancel_needs_unlock(self, mocked):
        mocked.side_effect = [
            FakeResponse({"access_token": "token", "expires_in": 3600}),
            FakeResponse({"result": [{"accountSeq": 7}]}),
            FakeResponse({"result": {"orderId": "order-1", "status": "OPEN"}}),
        ]
        client = TossClient(client_id="id", client_secret="secret", live_trading_enabled=False)
        self.assertEqual(client.order_detail("order-1")["status"], "OPEN")
        with self.assertRaisesRegex(TossApiError, "안전 잠금"):
            client.cancel_order("order-1")

    @patch("auto_trader.toss.urlopen")
    def test_order_history_group_is_read_only(self, mocked):
        mocked.side_effect = [
            FakeResponse({"access_token": "token", "expires_in": 3600}),
            FakeResponse({"result": [{"accountSeq": 7}]}),
            FakeResponse({"result": {"orders": [{"orderId": "order-1", "status": "CANCELED"}]}}),
        ]
        client = TossClient(client_id="id", client_secret="secret")
        orders = client.orders("CLOSED", from_date="2026-09-29", to_date="2026-09-29")
        self.assertEqual(orders[0]["status"], "CANCELED")
        request = mocked.call_args_list[2].args[0]
        self.assertEqual(request.get_method(), "GET")
        self.assertIn("status=CLOSED", request.full_url)

    @patch("auto_trader.toss.urlopen")
    def test_api_error_is_sanitized(self, mocked):
        mocked.side_effect = HTTPError("url", 403, "Forbidden", {},
                                       io.BytesIO(b'{"code":"forbidden","message":"not allowed"}'))
        with self.assertRaisesRegex(TossApiError, "forbidden"):
            TossClient(client_id="id", client_secret="secret").access_token()

    @patch("auto_trader.toss.urlopen")
    def test_nested_compressed_api_error_is_parsed(self, mocked):
        payload = gzip.compress(json.dumps({
            "error": {"code": "invalid-token", "message": "invalid token"}
        }).encode())
        mocked.side_effect = HTTPError(
            "url", 401, "Unauthorized", {"Content-Encoding": "gzip"}, io.BytesIO(payload)
        )
        with self.assertRaisesRegex(TossApiError, "invalid-token") as caught:
            TossClient(client_id="id", client_secret="secret").access_token()
        self.assertEqual(caught.exception.error_code, "invalid-token")

    @patch("auto_trader.toss.urlopen")
    def test_invalid_cached_token_is_refreshed_once(self, mocked):
        invalid_token = HTTPError(
            "url", 401, "Unauthorized", {},
            io.BytesIO(b'{"error":{"code":"invalid-token","message":"invalid token"}}'),
        )
        mocked.side_effect = [
            FakeResponse({"access_token": "old-token", "expires_in": 3600}),
            invalid_token,
            FakeResponse({"access_token": "new-token", "expires_in": 3600}),
            FakeResponse({"result": [{"accountSeq": 1}]}),
        ]
        client = TossClient(client_id="id", client_secret="secret")
        self.assertEqual(client.accounts(), [{"accountSeq": 1}])
        self.assertEqual(client.access_token(), "new-token")
        self.assertEqual(mocked.call_count, 4)

    @patch("auto_trader.toss.urlopen")
    def test_invalid_token_retry_is_not_repeated(self, mocked):
        def invalid_token():
            return HTTPError(
                "url", 401, "Unauthorized", {},
                io.BytesIO(b'{"error":{"code":"invalid-token","message":"invalid token"}}'),
            )

        mocked.side_effect = [
            FakeResponse({"access_token": "old-token", "expires_in": 3600}),
            invalid_token(),
            FakeResponse({"access_token": "new-token", "expires_in": 3600}),
            invalid_token(),
        ]
        with self.assertRaises(TossApiError):
            TossClient(client_id="id", client_secret="secret").accounts()
        self.assertEqual(mocked.call_count, 4)

    @patch("auto_trader.toss.urlopen")
    def test_portfolio_is_normalized_and_account_is_masked(self, mocked):
        mocked.side_effect = [
            FakeResponse({"access_token": "token", "expires_in": 3600}),
            FakeResponse({"result": [{"accountSeq": 1, "accountNo": "12345678", "accountType": "GENERAL"}]}),
            FakeResponse({"result": {"totalPurchaseAmount": {"krw": "1000", "usd": "0"},
                "marketValue": {"amount": {"krw": "1100", "usd": None}, "amountAfterCost": {"krw": "1090", "usd": None}},
                "profitLoss": {"amount": {"krw": "100", "usd": None}, "rate": "0.10"},
                "dailyProfitLoss": {"amount": {"krw": "20", "usd": None}, "rate": "0.02"},
                "items": [{"symbol": "005930", "name": "삼성전자", "marketCountry": "KR",
                    "currency": "KRW", "quantity": "1", "averagePurchasePrice": "1000",
                    "lastPrice": "1100", "marketValue": {"purchaseAmount": "1000", "amount": "1100"},
                    "profitLoss": {"amount": "100", "rate": "0.10"},
                    "dailyProfitLoss": {"amount": "20", "rate": "0.02"}, "cost": {}}]}}),
            FakeResponse({"result": {"today": {
                "date": "2026-09-25", "integrated": {"regularMarket": {}}
            }}}),
        ]
        client = TossClient(client_id="id", client_secret="secret")
        portfolio = client.portfolio()
        self.assertEqual(portfolio.account_label, "****5678")
        self.assertEqual(portfolio.market_value, 1100)
        self.assertEqual(portfolio.profit_rate, 10)
        self.assertEqual(portfolio.holdings[0].profit_rate, 10)
        self.assertEqual(portfolio.holdings[0].symbol, "005930")
        self.assertIs(portfolio, client.portfolio())
        self.assertEqual(portfolio.daily_profit_loss, 20)
        self.assertTrue(portfolio.market_open_today)
        self.assertEqual(mocked.call_count, 4)

    @patch("auto_trader.toss.urlopen")
    def test_portfolio_uses_previous_business_day_label_on_kr_market_holiday(self, mocked):
        mocked.side_effect = [
            FakeResponse({"access_token": "token", "expires_in": 3600}),
            FakeResponse({"result": [{"accountSeq": 1, "accountNo": "12345678"}]}),
            FakeResponse({"result": {
                "totalPurchaseAmount": {"krw": "1000"},
                "marketValue": {"amount": {"krw": "1100"}},
                "profitLoss": {"amount": {"krw": "100"}, "rate": "0.10"},
                "dailyProfitLoss": {"amount": {"krw": "20"}, "rate": "0.02"},
                "items": [{
                    "symbol": "005930", "name": "삼성전자", "marketCountry": "KR",
                    "currency": "KRW", "quantity": "1", "averagePurchasePrice": "1000",
                    "lastPrice": "1100", "marketValue": {"purchaseAmount": "1000", "amount": "1100"},
                    "profitLoss": {"amount": "100", "rate": "0.10"},
                    "dailyProfitLoss": {"amount": "20", "rate": "0.02"},
                }],
            }}),
            FakeResponse({"result": {
                "today": {"date": "2026-09-25", "integrated": None},
                "previousBusinessDay": {"date": "2026-09-23", "integrated": {}},
            }}),
        ]
        portfolio = TossClient(client_id="id", client_secret="secret").portfolio()
        self.assertFalse(portfolio.market_open_today)
        self.assertEqual(portfolio.daily_profit_reference_date, "2026-09-23")
        self.assertEqual(portfolio.daily_profit_loss, 20)
        self.assertEqual(portfolio.daily_profit_rate, 2)
        self.assertEqual(portfolio.holdings[0].daily_profit_loss, 20)

    @patch("auto_trader.toss.urlopen")
    def test_domestic_trading_amount_watchlist(self, mocked):
        mocked.side_effect = [
            FakeResponse({"access_token": "token", "expires_in": 3600}),
            FakeResponse({"result": {"rankedAt": "now", "rankings": [
                {"rank": 2, "symbol": "000660", "price": {"lastPrice": "180000"}},
                {"rank": 1, "symbol": "005930", "price": {"lastPrice": "70000"}}]}}),
            FakeResponse({"result": [{"symbol": "005930", "name": "삼성전자"},
                                      {"symbol": "000660", "name": "SK하이닉스"}]})]
        stocks, prices = TossClient(client_id="id", client_secret="secret").domestic_trading_amount_top(2)
        self.assertEqual([stock.symbol for stock in stocks], ["005930", "000660"])
        self.assertEqual(prices["005930"], 70000)

    @patch("auto_trader.toss.urlopen")
    def test_buying_power_is_normalized_and_cached(self, mocked):
        mocked.side_effect = [
            FakeResponse({"access_token": "token", "expires_in": 3600}),
            FakeResponse({"result": [{"accountSeq": 1, "accountNo": "12345678"}]}),
            FakeResponse({"result": {"currency": "KRW", "cashBuyingPower": "5000000"}}),
            FakeResponse({"result": {"currency": "USD", "cashBuyingPower": "3500.5"}}),
        ]
        client = TossClient(client_id="id", client_secret="secret")
        buying_power = client.buying_power()
        self.assertEqual(buying_power.account_label, "****5678")
        self.assertEqual(buying_power.krw_cash_buying_power, 5000000)
        self.assertEqual(buying_power.usd_cash_buying_power, 3500.5)
        self.assertIs(buying_power, client.buying_power())
        self.assertEqual(mocked.call_count, 4)

    @patch("auto_trader.toss.urlopen")
    def test_affordable_candidates_use_rank_and_cash_limit(self, mocked):
        mocked.side_effect = [
            FakeResponse({"access_token": "token", "expires_in": 3600}),
            FakeResponse({"result": [{"accountSeq": 1, "accountNo": "12345678"}]}),
            FakeResponse({"result": {"currency": "KRW", "cashBuyingPower": "500000"}}),
            FakeResponse({"result": {"currency": "USD", "cashBuyingPower": "0"}}),
            FakeResponse({"result": {"rankedAt": "2026-09-24T10:00:00+09:00", "rankings": [
                {"rank": 1, "symbol": "EXPENSIVE", "price": {"lastPrice": "600000", "changeRate": "0.01"}},
                {"rank": 2, "symbol": "005930", "price": {"lastPrice": "70000", "changeRate": "0.02"}},
                {"rank": 3, "symbol": "000660", "price": {"lastPrice": "200000", "changeRate": "-0.01"}},
            ]}}),
            FakeResponse({"result": [
                {"symbol": "005930", "name": "삼성전자", "securityType": "STOCK",
                 "isCommonShare": True, "status": "ACTIVE", "koreanMarketDetail": {}},
                {"symbol": "000660", "name": "SK하이닉스", "securityType": "STOCK",
                 "isCommonShare": True, "status": "ACTIVE", "koreanMarketDetail": {}},
            ]}),
        ]
        client = TossClient(client_id="id", client_secret="secret")
        result = client.affordable_domestic_candidates()
        self.assertEqual([item.symbol for item in result.candidates], ["005930", "000660"])
        self.assertEqual(result.candidates[0].max_quantity, 7)
        self.assertEqual(result.candidates[0].change_rate_percent, 2)
        self.assertIs(result, client.affordable_domestic_candidates())
        self.assertEqual(mocked.call_count, 6)

    @patch("auto_trader.toss.sleep")
    @patch("auto_trader.toss.urlopen")
    def test_domestic_stock_search_matches_partial_name(self, mocked, mocked_sleep):
        mocked.side_effect = [
            FakeResponse({"access_token": "token", "expires_in": 3600}),
            FakeResponse({"result": [
                {"symbol": "005930", "name": "삼성전자", "securityType": "STOCK", "isCommonShare": True},
                {"symbol": "009150", "name": "삼성전기", "securityType": "STOCK", "isCommonShare": True},
            ]}),
            FakeResponse({"result": [
                {"symbol": "OTHER", "name": "다른종목", "securityType": "STOCK", "isCommonShare": True},
            ]}),
            FakeResponse({"result": {"rankedAt": "2026-09-24T10:00:00+09:00", "rankings": [
                {"rank": 7, "symbol": "009150", "price": {"lastPrice": "150000", "basePrice": "150000"},
                 "tradingVolume": "100", "tradingAmount": "15000000"},
                {"rank": 12, "symbol": "005930", "price": {"lastPrice": "70000"},
                 "tradingVolume": "100", "tradingAmount": "7000000"},
            ]}}),
            FakeResponse({"result": [
                {"symbol": "009150", "lastPrice": "150000", "currency": "KRW"},
                {"symbol": "005930", "lastPrice": "70000", "currency": "KRW"},
            ]}),
            FakeResponse({"result": [
                {"symbol": "009150", "sharesOutstanding": "1000000"},
                {"symbol": "005930", "sharesOutstanding": "5900000000"},
            ]}),
        ]
        page = TossClient(client_id="id", client_secret="secret").search_domestic_stocks("삼성")
        self.assertEqual([item.name for item in page.results], ["삼성전기", "삼성전자"])
        self.assertEqual(page.results[0].price, 150000)
        self.assertEqual(page.results[0].change_rate_percent, 0)
        self.assertEqual(page.results[0].trading_amount_rank, 7)
        self.assertEqual(page.results[0].trading_amount, 15000000)
        self.assertEqual(page.results[0].market_cap, 150000000000)
        self.assertEqual(page.results[0].market, "KOSPI")
        self.assertEqual(page.total, 2)
        self.assertEqual(page.total_pages, 1)
        mocked_sleep.assert_called_once()

    @patch("auto_trader.toss.sleep")
    @patch("auto_trader.toss.urlopen")
    def test_domestic_stock_list_filters_market_and_type(self, mocked, mocked_sleep):
        mocked.side_effect = [
            FakeResponse({"access_token": "token", "expires_in": 3600}),
            FakeResponse({"result": [
                {"symbol": "005930", "name": "삼성전자", "securityType": "STOCK", "isCommonShare": True},
                {"symbol": "069500", "name": "KODEX 200", "securityType": "ETF", "isCommonShare": False},
            ]}),
            FakeResponse({"result": [
                {"symbol": "247540", "name": "에코프로비엠", "securityType": "STOCK", "isCommonShare": True},
            ]}),
            FakeResponse({"result": {"rankedAt": "2026-09-25T10:00:00+09:00", "rankings": [
                {"rank": 1, "symbol": "247540", "price": {"basePrice": "175000", "changeRate": "0.03"}, "tradingAmount": "9000000"},
            ]}}),
            FakeResponse({"result": [
                {"symbol": "247540", "lastPrice": "180000", "currency": "KRW"},
            ]}),
            FakeResponse({"result": [
                {"symbol": "247540", "sharesOutstanding": "50000000"},
            ]}),
        ]
        page = TossClient(client_id="id", client_secret="secret").list_domestic_stocks(
            market="KOSDAQ", security_type="COMMON", sort="NAME"
        )
        self.assertEqual(page.total, 1)
        self.assertEqual(page.results[0].symbol, "247540")
        self.assertEqual(page.results[0].market, "KOSDAQ")
        self.assertEqual(page.results[0].previous_close, 175000)
        self.assertAlmostEqual(float(page.results[0].change_rate_percent), 2.857142857)
        self.assertEqual(page.results[0].market_cap, 9000000000000)
        mocked_sleep.assert_called_once()

    @patch("auto_trader.toss.sleep")
    @patch("auto_trader.toss.urlopen")
    def test_domestic_stock_detail_and_sparkline_share_candle_cache(self, mocked, mocked_sleep):
        mocked.side_effect = [
            FakeResponse({"access_token": "token", "expires_in": 3600}),
            FakeResponse({"result": [
                {"symbol": "005930", "name": "삼성전자", "securityType": "STOCK", "isCommonShare": True},
            ]}),
            FakeResponse({"result": []}),
            FakeResponse({"result": [
                {"symbol": "005930", "lastPrice": "72000", "currency": "KRW"},
            ]}),
            FakeResponse({"result": [
                {"symbol": "005930", "sharesOutstanding": "5900000000",
                 "englishName": "SamsungElec", "isinCode": "KR7005930003",
                 "listDate": "1975-06-11", "status": "ACTIVE",
                 "koreanMarketDetail": {"nxtSupported": True,
                                         "krxTradingSuspended": False,
                                         "nxtTradingSuspended": False}},
            ]}),
            FakeResponse({"result": {"candles": [
                {"timestamp": "2026-09-24T00:00:00+09:00", "openPrice": "71000", "highPrice": "73000", "lowPrice": "70500", "closePrice": "72000", "volume": "200"},
                {"timestamp": "2026-09-23T00:00:00+09:00", "openPrice": "70000", "highPrice": "71500", "lowPrice": "69500", "closePrice": "71000", "volume": "100"},
            ]}}),
            FakeResponse({"result": {"rankedAt": "2026-09-24T10:00:00+09:00", "rankings": [
                {"rank": 2, "symbol": "005930", "price": {"lastPrice": "72000"}},
            ]}}),
        ]
        client = TossClient(client_id="id", client_secret="secret")
        detail = client.domestic_stock_detail("005930", period="3M")
        line = client.domestic_sparklines(["005930"], count=15, period="3M")
        self.assertEqual(detail.name, "삼성전자")
        self.assertEqual(detail.change_rate_percent.quantize(Decimal("0.01")), Decimal("1.41"))
        self.assertEqual(detail.market_cap, Decimal("424800000000000"))
        self.assertEqual(detail.english_name, "SamsungElec")
        self.assertEqual(detail.isin_code, "KR7005930003")
        self.assertEqual(detail.list_date.isoformat(), "1975-06-11")
        self.assertTrue(detail.nxt_supported)
        self.assertFalse(detail.krx_trading_suspended)
        self.assertEqual(line["005930"], [Decimal("71000"), Decimal("72000")])
        self.assertEqual(mocked.call_count, 7)
        mocked_sleep.assert_called_once()

    @patch("auto_trader.toss.urlopen")
    def test_intraday_sparkline_requests_one_minute_candles(self, mocked):
        mocked.side_effect = [
            FakeResponse({"access_token": "token", "expires_in": 3600}),
            FakeResponse({"result": {"candles": [
                {"timestamp": "2026-09-24T10:01:00+09:00", "openPrice": "71000", "highPrice": "71100", "lowPrice": "70900", "closePrice": "71050", "volume": "10"},
                {"timestamp": "2026-09-24T10:00:00+09:00", "openPrice": "70900", "highPrice": "71000", "lowPrice": "70800", "closePrice": "70950", "volume": "8"},
            ]}}),
        ]
        result = TossClient(client_id="id", client_secret="secret").domestic_sparklines(
            ["005930"], period="1D"
        )
        request_url = mocked.call_args_list[1].args[0].full_url
        self.assertIn("interval=1m", request_url)
        self.assertIn("count=200", request_url)
        self.assertEqual(result["005930"], [Decimal("70950"), Decimal("71050")])

    @patch("auto_trader.toss.sleep")
    @patch("auto_trader.toss.urlopen")
    def test_intraday_candles_follow_pagination_cursor(self, mocked, mocked_sleep):
        mocked.side_effect = [
            FakeResponse({"access_token": "token", "expires_in": 3600}),
            FakeResponse({"result": {"nextBefore": "2026-09-24T10:00:00+09:00", "candles": [
                {"timestamp": "2026-09-24T10:02:00+09:00", "openPrice": "102", "highPrice": "103", "lowPrice": "101", "closePrice": "102", "volume": "3"},
                {"timestamp": "2026-09-24T10:01:00+09:00", "openPrice": "101", "highPrice": "102", "lowPrice": "100", "closePrice": "101", "volume": "2"},
            ]}}),
            FakeResponse({"result": {"candles": [
                {"timestamp": "2026-09-24T10:00:00+09:00", "openPrice": "100", "highPrice": "101", "lowPrice": "99", "closePrice": "100", "volume": "1"},
            ]}}),
        ]
        candles = TossClient(client_id="id", client_secret="secret")._domestic_candles(
            "005930", "1m", 201
        )
        second_page_url = mocked.call_args_list[2].args[0].full_url
        self.assertIn("before=2026-09-24T10%3A00%3A00%2B09%3A00", second_page_url)
        self.assertIn("count=200", second_page_url)
        self.assertIn("count=200", mocked.call_args_list[1].args[0].full_url)
        self.assertEqual([item.close_price for item in candles], [Decimal("100"), Decimal("101"), Decimal("102")])
        mocked_sleep.assert_called_once_with(0.08)

    @patch("auto_trader.toss.urlopen")
    def test_favorite_snapshots_include_price_change_and_rank(self, mocked):
        mocked.side_effect = [
            FakeResponse({"access_token": "token", "expires_in": 3600}),
            FakeResponse({"result": [
                {"symbol": "005930", "lastPrice": "71000", "currency": "KRW"},
            ]}),
            FakeResponse({"result": {"rankedAt": "2026-09-24T10:00:00+09:00", "rankings": [
                {"rank": 3, "symbol": "005930", "price": {"changeRate": "0.015"},
                 "tradingVolume": "100", "tradingAmount": "7100000"},
            ]}}),
        ]
        favorite = {
            "symbol": "005930", "name": "삼성전자", "market": "KOSPI",
            "security_type": "STOCK", "is_common_share": True,
            "created_at": datetime.now(timezone.utc),
        }
        result = TossClient(client_id="id", client_secret="secret").favorite_stock_snapshots([favorite])
        self.assertEqual(result[0].price, 71000)
        self.assertEqual(result[0].change_rate_percent, 1.5)
        self.assertEqual(result[0].trading_amount_rank, 3)
