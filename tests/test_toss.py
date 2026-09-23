import gzip
import io
import json
import unittest
from datetime import datetime, timezone
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
        ]
        client = TossClient(client_id="id", client_secret="secret")
        portfolio = client.portfolio()
        self.assertEqual(portfolio.account_label, "****5678")
        self.assertEqual(portfolio.market_value, 1100)
        self.assertEqual(portfolio.profit_rate, 10)
        self.assertEqual(portfolio.holdings[0].profit_rate, 10)
        self.assertEqual(portfolio.holdings[0].symbol, "005930")
        self.assertIs(portfolio, client.portfolio())
        self.assertEqual(mocked.call_count, 3)

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
        ]
        page = TossClient(client_id="id", client_secret="secret").search_domestic_stocks("삼성")
        self.assertEqual([item.name for item in page.results], ["삼성전기", "삼성전자"])
        self.assertEqual(page.results[0].price, 150000)
        self.assertEqual(page.results[0].change_rate_percent, 0)
        self.assertEqual(page.results[0].trading_amount_rank, 7)
        self.assertEqual(page.results[0].market, "KOSPI")
        self.assertEqual(page.total, 2)
        self.assertEqual(page.total_pages, 1)
        mocked_sleep.assert_called_once()

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
