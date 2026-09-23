import io
import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from auto_trader.toss import TossApiError, TossClient


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload
    def __enter__(self):
        return self
    def __exit__(self, *_):
        return False
    def read(self):
        return json.dumps(self.payload).encode()


class TossClientTests(unittest.TestCase):
    @patch("auto_trader.toss.urlopen")
    def test_token_is_cached_and_accounts_are_read(self, mocked):
        mocked.side_effect = [FakeResponse({"access_token": "secret-token", "expires_in": 3600}),
                              FakeResponse({"result": [{"accountSeq": 1}]})]
        client = TossClient(client_id="id", client_secret="secret")
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
        portfolio = TossClient(client_id="id", client_secret="secret").portfolio()
        self.assertEqual(portfolio.account_label, "****5678")
        self.assertEqual(portfolio.market_value, 1100)
        self.assertEqual(portfolio.profit_rate, 10)
        self.assertEqual(portfolio.holdings[0].profit_rate, 10)
        self.assertEqual(portfolio.holdings[0].symbol, "005930")

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
