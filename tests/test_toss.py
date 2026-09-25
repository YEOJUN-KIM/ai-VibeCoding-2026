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
    def test_token_is_cached_and_accounts_are_read(self, mocked):
        mocked.side_effect = [FakeResponse({"access_token": "secret-token", "expires_in": 3600}),
                              FakeResponse({"result": [{"accountSeq": 1}]})]
        client = TossClient(client_id="id", client_secret="secret")
        self.assertEqual(client.accounts(), [{"accountSeq": 1}])
        self.assertEqual(client.accounts(), [{"accountSeq": 1}])
        self.assertEqual(client.access_token(), "secret-token")
        self.assertEqual(mocked.call_count, 2)

    def test_missing_configuration(self):
        with self.assertRaises(TossApiError):
            TossClient(client_id="", client_secret="").access_token()

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
                {"rank": 7, "symbol": "009150", "price": {"lastPrice": "150000"},
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
                {"rank": 1, "symbol": "247540", "price": {"changeRate": "0.03"}, "tradingAmount": "9000000"},
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
        self.assertEqual(page.results[0].change_rate_percent, 3)
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
                {"symbol": "005930", "sharesOutstanding": "5900000000"},
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
        self.assertNotIn("count=", request_url)
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
