"""Pre-flight for approved read-only stocks calls: runs the REAL TossClient request path with a fake
network (client._json_request_once replaced) behind the allowlist fence. Fake credentials only; no
socket, no .env, no database."""
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

from auto_trader.day01_scout_facts import InstrumentFactsSource, toss_directory_source, toss_stocks_source
from auto_trader.day01_scout_source import ResearchLog, guard_toss_client

T0 = datetime(2026, 11, 13, 1, 50, tzinfo=timezone.utc)
STOCKS = [("/api/v1/stocks", "GET"), ("/api/v1/stocks/all", "GET")]
FAKE_TOKEN = "FAKE-ACCESS-TOKEN-0123456789"


def stock_row(symbol):
    return {"symbol": symbol, "name": f"N{symbol}", "securityType": "STOCK", "isCommonShare": True, "status": "ACTIVE",
            "koreanMarketDetail": {"krxTradingSuspended": False, "liquidationTrading": False}}


class PreflightTests(unittest.TestCase):
    def setUp(self):
        before = set(sys.modules)

        def cleanup():
            for name in set(sys.modules) - before:
                if name.startswith("auto_trader."):
                    del sys.modules[name]
        self.addCleanup(cleanup)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def client(self, handler, *, max_requests=6):
        from auto_trader.toss import TossClient
        client = TossClient(base_url="https://example.invalid", client_id="fake-id", client_secret="fake-secret",
                            live_trading_enabled=False)
        sent = []

        def fake_network(request):
            path = urlparse(request.full_url).path
            sent.append((request.get_method(), path))
            if path == "/oauth2/token":
                return {"access_token": FAKE_TOKEN, "expires_in": 3600}
            return handler(path, request)

        client._json_request_once = fake_network
        counts = guard_toss_client(client, max_requests=max_requests, also_allow=STOCKS, rankings=False)
        return client, counts, sent

    def test_happy_path_uses_only_token_and_the_two_approved_gets_within_the_cap(self):
        def handler(path, request):
            if path == "/api/v1/stocks/all":
                market = "KOSPI" if "KOSPI" in request.full_url else "KOSDAQ"
                return {"result": [{"symbol": "A1" if market == "KOSPI" else "B2", "securityType": "STOCK",
                                    "isCommonShare": True, "name": "n"}]}
            return {"result": [stock_row("A1"), stock_row("B2")]}

        client, counts, sent = self.client(handler)
        directory = toss_directory_source(client)()
        rows = toss_stocks_source(client)(("A1", "B2"))
        self.assertEqual(sorted(r["market"] for r in directory[0]), ["KOSDAQ", "KOSPI"])
        self.assertEqual(len(rows), 2)
        self.assertEqual(counts["total"], 4)
        self.assertEqual(counts["paths"], {"/oauth2/token": 1, "/api/v1/stocks/all": 2, "/api/v1/stocks": 1})
        self.assertEqual({m for m, _ in sent}, {"POST", "GET"})
        self.assertTrue(all(p in {"/oauth2/token", "/api/v1/stocks", "/api/v1/stocks/all"} for _, p in sent))

    def test_unapproved_endpoints_and_orders_are_blocked_before_the_network(self):
        client, counts, sent = self.client(lambda path, request: {"result": []})
        base = client.base_url
        for url, method, body in ((f"{base}/api/v1/orders", "POST", {"symbol": "A1"}),
                                  (f"{base}/api/v1/accounts", "GET", None),
                                  (f"{base}/api/v1/holdings", "GET", None),
                                  (f"{base}/api/v1/prices?symbols=A1", "GET", None),
                                  (f"{base}/api/v1/rankings?type=X", "GET", None),
                                  (f"{base}/api/v1/stocks", "POST", {"x": 1})):
            with self.assertRaises(PermissionError, msg=url):
                client._authorized_json_request(url, method=method, body=body)
        self.assertEqual([p for _, p in sent if p != "/oauth2/token"], [])     # nothing blocked reached the network

    def test_rankings_can_be_removed_from_the_allowlist_and_default_is_unchanged(self):
        from auto_trader.toss import TossClient
        for rankings, expect_blocked in ((False, True), (True, False)):
            client = TossClient(base_url="https://example.invalid", client_id="i", client_secret="s")
            client._json_request_once = lambda request: {"access_token": "T", "expires_in": 3600,
                                                         "result": {"rankedAt": None, "rankings": []}}
            guard_toss_client(client, also_allow=STOCKS, rankings=rankings)
            if expect_blocked:
                with self.assertRaises(PermissionError):
                    client._domestic_trading_amount_rankings()
            else:
                self.assertEqual(client._domestic_trading_amount_rankings()[1], [])

    def test_retries_and_reauthentication_cannot_exceed_the_cap(self):
        from auto_trader.brokers.toss.types import TossApiError

        def always_429(path, request):
            raise TossApiError("rate limited", status_code=429, error_code="rate-limit-exceeded")

        client, counts, sent = self.client(always_429, max_requests=3)
        with self.assertRaises((PermissionError, TossApiError)):
            toss_stocks_source(client)(("A1",))
        self.assertLessEqual(counts["total"], 3)
        self.assertLessEqual(len(sent), 3)

        calls = []

        def unauthorized_once(path, request):
            calls.append(path)
            if len(calls) == 1:
                raise TossApiError("bad token", status_code=401, error_code="invalid-token")
            return {"result": [stock_row("A1")]}

        client, counts, sent = self.client(unauthorized_once, max_requests=6)
        self.assertEqual(len(toss_stocks_source(client)(("A1",))), 1)
        self.assertEqual(counts["paths"].get("/oauth2/token"), 2)               # re-auth is counted
        self.assertLessEqual(counts["total"], 6)

    def test_timeout_degrades_to_no_facts_and_no_secret_reaches_the_log(self):
        from auto_trader.brokers.toss.types import TossApiError

        def timeout(path, request):
            raise TossApiError(f"timeout Bearer {FAKE_TOKEN} fake-secret", error_code="timeout")

        client, counts, sent = self.client(timeout)
        path = Path(self.tmp.name) / "facts.jsonl"
        clock = lambda: datetime.now(timezone.utc)
        source = InstrumentFactsSource(toss_stocks_source(client), clock=clock,
                                       directory_source=toss_directory_source(client, clock=clock),
                                       log=ResearchLog(path, clock=clock))
        facts, notes = source.collect(["A1"], ranking_rows=[], ranking_received_at=datetime.now(timezone.utc))
        self.assertEqual(facts, ())
        self.assertIn(("A1", "STOCKS_FETCH_FAILED"), notes)
        text = path.read_text(encoding="utf-8")
        for secret in (FAKE_TOKEN, "fake-secret", "fake-id", "Bearer", "Authorization"):
            self.assertNotIn(secret, text)

    def test_directory_cache_reuse_sends_no_request_and_keeps_the_original_receipt_time(self):
        def handler(path, request):
            return {"result": [{"symbol": "A1", "securityType": "STOCK", "isCommonShare": True, "name": "n"}]}

        client, counts, sent = self.client(handler)
        source = toss_directory_source(client)
        first = source()
        after_first = counts["total"]
        second = source()
        self.assertEqual(counts["total"], after_first)                          # served from the client cache
        self.assertLess(abs(second[1] - first[1]), timedelta(milliseconds=50))  # receipt time not renewed
        self.assertLessEqual(first[1], datetime.now(timezone.utc) + timedelta(milliseconds=50))


if __name__ == "__main__":
    unittest.main()
