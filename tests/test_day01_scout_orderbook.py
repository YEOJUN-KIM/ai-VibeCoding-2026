"""Synthetic tests for the order-book converter and the strict request fence.

No external network, DB, credentials, clock or broker. The fence tests run the REAL Toss transport code
against a loopback fake server (127.0.0.1) with dummy credentials. The response shape is the documented
`result.{timestamp,currency,asks[],bids[]}` structure; values are made up (the docs give no example)."""
import ast
import json
import sys
import tempfile
import threading
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Event, Lock
from urllib.parse import parse_qs, urlsplit

from auto_trader import day01_scout as sc
from auto_trader import day01_scout_orderbook as ob
from auto_trader import day01_scout_source as src
from auto_trader.day01_scout_orderbook import (
    ANOMALY, OK, UNKNOWN, OrderbookResult, fetch_orderbook_results, parse_orderbook, validate_symbols, with_spread)
from auto_trader.day01_scout_source import ResearchLog, guard_toss_orderbook_client
from auto_trader.ml.day01 import KST
from tests.test_day01_scout_market import AS_OF, ScoutEndToEndTests, bars, snapshot

NOW = datetime(2026, 11, 13, 1, 11, 5, tzinfo=timezone.utc)          # 10:11:05 KST
STAMP = "2026-11-13T10:11:00+09:00"                                   # 5 s before NOW


def book(*, asks=(("70100", "1200"), ("70200", "800")), bids=(("70000", "1500"), ("69900", "900")),
         timestamp=STAMP, currency="KRW"):
    return {"result": {"timestamp": timestamp, "currency": currency,
                       "asks": [{"price": p, "volume": v} for p, v in asks],
                       "bids": [{"price": p, "volume": v} for p, v in bids]}}


def parse(payload, **kw):
    return parse_orderbook("005930", payload, received_at=NOW, **kw)


class ConversionTests(unittest.TestCase):
    def test_documented_shape_gives_the_exact_formula_value(self):
        r = parse(book())
        self.assertEqual((r.status, r.reasons), (OK, ()))
        self.assertEqual((r.best_ask, r.best_bid, r.ask_volume, r.bid_volume),
                         (D("70100"), D("70000"), D("1200"), D("1500")))
        expected = D("100") / (D("140100") / 2) * 100                                  # (ask-bid)/mid*100, percent
        self.assertEqual(r.spread_pct.quantize(D("1e-20")), expected.quantize(D("1e-20")))
        self.assertEqual(r.provider_time, datetime(2026, 11, 13, 1, 11, 0, tzinfo=timezone.utc))
        self.assertEqual(r.received_at, NOW)                                          # our stamp, not the provider's
        self.assertEqual(r.age_seconds, D("5.000"))

    def test_best_prices_do_not_depend_on_list_order_or_number_type(self):
        forward = parse(book())
        reverse = parse(book(asks=(("70200", "800"), ("70100", "1200")), bids=(("69900", "900"), ("70000", "1500"))))
        shuffled = parse(book(asks=(("70300", "5"), ("70100", "1200"), ("70200", "800")),
                              bids=(("69900", "900"), ("70000", "1500"), ("69000", "1"))))
        self.assertEqual(forward.spread_pct, reverse.spread_pct)
        self.assertEqual(forward.spread_pct, shuffled.spread_pct)
        numeric = {"result": {"timestamp": STAMP, "currency": "KRW",
                              "asks": [{"price": 70100, "volume": 1200.5}], "bids": [{"price": 70000.0, "volume": 3}]}}
        self.assertEqual(parse(numeric).best_ask, D("70100"))
        self.assertEqual(parse(numeric).ask_volume, D("1200.5"))

    def test_zero_volume_or_malformed_top_level_is_skipped_conservatively(self):
        r = parse(book(asks=(("70050", "0"), ("70100", "1200")), bids=(("70010", "x"), ("70000", "1500"))))
        self.assertEqual((r.status, r.best_ask, r.best_bid), (OK, D("70100"), D("70000")))
        self.assertEqual(r.notes, ("ASK_LEVELS_SKIPPED=1", "BID_LEVELS_SKIPPED=1"))
        self.assertGreater(r.spread_pct, parse(book(asks=(("70050", "5"),), bids=(("70010", "5"),))).spread_pct)

    def test_invalid_books_stay_unknown_with_reasons(self):
        cases = {
            "NO_VALID_ASK": book(asks=()), "NO_VALID_BID": book(bids=()),
            "NO_VALID_ASK ": book(asks=(("70100", "0"),)), "NO_VALID_BID ": book(bids=(("0", "10"),)),
            "CROSSED_BOOK": book(asks=(("69900", "5"),), bids=(("70000", "5"),)),
            "LOCKED_BOOK": book(asks=(("70000", "5"),), bids=(("70000", "5"),)),
            "CURRENCY_NOT_KRW": book(currency="USD"), "CURRENCY_MISSING": book(currency=None),
        }
        for reason, payload in cases.items():
            with self.subTest(reason):
                r = parse(payload)
                self.assertEqual((r.status, r.spread_pct), (UNKNOWN, None))
                self.assertIn(reason.strip(), r.reasons)
        for payload in (None, [], {}, {"result": None}, {"result": []}, "x"):
            with self.subTest(payload):
                self.assertEqual(parse(payload).reasons, ("RESPONSE_MALFORMED",))
        broken = book()
        broken["result"]["asks"] = {"price": "1"}
        broken["result"]["bids"] = "nope"
        self.assertEqual(set(parse(broken).reasons), {"ASKS_MALFORMED", "BIDS_MALFORMED"})
        for bad in ("NaN", "Infinity", "-1", " 1", "1 ", "", True, None, [], {}):
            with self.subTest(bad):
                self.assertIn("NO_VALID_ASK", parse(book(asks=((bad, "5"),))).reasons)
                self.assertIn("NO_VALID_BID", parse(book(bids=(("70000", bad),))).reasons)

    def test_timestamp_validation_and_freshness(self):
        for value, reason in ((None, "TIMESTAMP_MISSING"), ("not a time", "TIMESTAMP_INVALID"),
                              ("2026-11-13T10:11:00", "TIMESTAMP_NO_TIMEZONE"), ("2026-11-13", "TIMESTAMP_NO_TIMEZONE"),
                              (1763000000, "TIMESTAMP_INVALID"), ("", "TIMESTAMP_INVALID")):
            with self.subTest(value):
                r = parse(book(timestamp=value))
                self.assertEqual((r.status, r.spread_pct, reason in r.reasons), (UNKNOWN, None, True))
        missing = {"result": {k: v for k, v in book()["result"].items() if k != "timestamp"}}
        self.assertIn("TIMESTAMP_MISSING", parse(missing).reasons)
        self.assertEqual(parse(book(timestamp="2026-11-13T10:10:55+09:00")).status, OK)           # exactly 10 s
        self.assertEqual(parse(book(timestamp="2026-11-13T10:10:54+09:00")).reasons, ("STALE_ORDERBOOK",))
        self.assertEqual(parse(book(timestamp="2026-11-13T10:10:54+09:00"), max_age_seconds=20).status, OK)
        self.assertEqual(parse(book(timestamp="2026-11-13T01:11:00Z")).status, OK)                # UTC 'Z' form
        self.assertEqual(parse(book(timestamp="2026-11-13T10:11:05+09:00")).status, OK)           # exactly the receipt time
        for ahead in ("2026-11-13T10:11:05.000001+09:00", "2026-11-13T10:11:06+09:00", "2026-11-13T10:11:09+09:00"):
            self.assertEqual(parse(book(timestamp=ahead)).reasons, ("TIMESTAMP_IN_FUTURE",))   # no tolerance at all
        self.assertEqual(parse(book(timestamp="2026-11-13T10:10:55.000000+09:00")).status, OK)    # exactly 10.000000 s
        self.assertEqual(parse(book(timestamp="2026-11-13T10:10:54.999600+09:00")).reasons, ("STALE_ORDERBOOK",))  # 10.0004 s
        for extreme in ("0001-01-01T00:00:00+09:00", "9999-12-31T23:59:59-23:59", "2026-13-45T99:99:99+09:00", "x" * 100):
            self.assertIn("TIMESTAMP_INVALID", parse(book(timestamp=extreme)).reasons)
        with self.assertRaises(ValueError):
            parse_orderbook("005930", book(), received_at=datetime(2026, 11, 13, 10, 11, 5))     # naive receipt time
        with self.assertRaises(ValueError):
            parse(book(), max_age_seconds=0)
        with self.assertRaises(ValueError):
            parse(book(), max_age_seconds=10**7)

    def test_anomalous_spread_is_separate_from_unknown_and_not_passed_on(self):
        exactly = parse(book(asks=(("5", "1"),), bids=(("3", "1"),)))               # 2*(2)/(8) = 50 % exactly
        self.assertEqual((exactly.status, exactly.spread_pct), (OK, D("50")))
        wide = parse(book(asks=(("5.01", "1"),), bids=(("3", "1"),)))
        self.assertEqual((wide.status, wide.spread_pct, wide.reasons), (ANOMALY, None, ("ANOMALOUS_SPREAD",)))
        self.assertGreater(wide.observed_spread_pct, 50)

    def test_extreme_numbers_are_invalid_levels_and_never_raise_or_misclassify(self):
        extreme = (("1e1000000", "9e999999"), ("2e-1000027", "1e-1000027"), ("2e-1000026", "1e-1000026"),
                   ("1" * 31, "1"), ("1e16", "1"), ("1e-10", "1"), ("0x10", "1"), ("1_0", "1"), ("\u0661\u0662", "1"),
                   ("-0", "1"), ("1E+400", "1"))
        for ask, bid in extreme:
            with self.subTest((ask, bid)):
                r = parse(book(asks=((ask, "5"),), bids=((bid, "5"),)))
                self.assertEqual((r.status, r.spread_pct), (UNKNOWN, None))
        wide = parse(book(asks=(("2e-9", "5"),), bids=(("1e-9", "5"),)))                   # real 66.7 % at the smallest scale
        self.assertEqual((wide.status, wide.reasons), (ANOMALY, ("ANOMALOUS_SPREAD",)))
        big = parse(book(asks=(("9.99999999999999e15", "1"),), bids=(("9.99999999999998e15", "1"),)))
        self.assertEqual(big.status, OK)
        self.assertEqual(parse(book(asks=((10**300, "5"),))).reasons.count("NO_VALID_ASK"), 1)

    def test_safe_log_copy_keeps_only_documented_fields(self):
        echo = "Bearer DUMMY-TOKEN-xyz dummy-secret http://127.0.0.1:1/api/v1/orderbook?symbol=005930"
        payload = {"message": echo, "result": {"timestamp": echo, "currency": echo, "requestId": echo,
                                               "asks": [{"price": "70100", "volume": "5", "note": echo}, echo, {"price": echo}],
                                               "bids": [{"price": 70000, "volume": 1.5, echo: echo}]}}
        raw = json.dumps(ob._safe_raw(payload))
        for secret in ("DUMMY", "Bearer", "dummy-secret", "127.0.0.1", "orderbook", "requestId", "note"):
            self.assertNotIn(secret, raw)
        self.assertEqual(ob._safe_raw(payload)["asks"][0], {"price": "70100", "volume": "5"})
        self.assertEqual(ob._safe_raw({"result": {"timestamp": STAMP, "currency": "KRW", "asks": [], "bids": []}})["timestamp"], STAMP)
        self.assertIsNone(ob._safe_raw("x"))

    def test_symbol_validation(self):
        self.assertEqual(validate_symbols(["005930", "0101N0"]), ("005930", "0101N0"))
        for bad in ("005930", b"005930", [], ["005930"] * 2, ["00593"], ["005930 "], ["abc123"], ["005930", 5],
                    ["A" * 6 + "7"], [f"00000{i}" for i in range(5)], None, ["AAPL.O1"]):
            with self.subTest(bad):
                with self.assertRaises(ValueError):
                    validate_symbols(bad)


# ---------------------------------------------------------------------------------------------- fake server
class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, code, body, headers=None):
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def handle_any(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        server = self.server
        with server.lock:
            server.hits.append((self.command, self.path, "Authorization" in self.headers))
        parts = urlsplit(self.path)
        if parts.path == "/oauth2/token":
            if server.mode == "token-bad-expires":
                echo = f"Bearer DUMMY-TOKEN-xyz dummy-secret http://127.0.0.1:{server.server_port}{self.path}"
                return self._send(200, {"access_token": "DUMMY-TOKEN-xyz", "expires_in": echo})
            if server.mode == "token-array":
                return self._send(200, ["Bearer DUMMY-TOKEN-xyz dummy-secret"])
            if server.mode == "token-400":
                echo = f"Bearer DUMMY-TOKEN-xyz dummy-secret client_secret=dummy-secret http://127.0.0.1:{server.server_port}{self.path}"
                return self._send(400, {"error": {"code": "invalid-client", "message": echo}})
            return self._send(200, {"access_token": "DUMMY-TOKEN-xyz", "expires_in": 3600})
        if parts.path == "/api/v1/orderbook":
            symbol = (parse_qs(parts.query).get("symbol") or [""])[0]
            mode = server.mode
            if mode == "429":
                return self._send(429, {"error": {"code": "rate-limit-exceeded", "message": "slow"}}, {"Retry-After": "0"})
            if mode == "401":
                return self._send(401, {"error": {"code": "invalid-token", "message": "bad"}})
            if mode == "400-array":
                return self._send(400, [f"Bearer DUMMY-TOKEN-xyz dummy-secret http://127.0.0.1:{server.server_port}{self.path}"])
            if mode == "302":
                return self._send(302, {}, {"Location": "/api/v1/accounts"})
            if mode == "400-echo" or (mode == "bad-symbol" and symbol == "000000"):
                echo = f"Bearer DUMMY-TOKEN-xyz dummy-secret http://127.0.0.1:{server.server_port}{self.path}"
                return self._send(400, {"error": {"code": server.error_code, "message": echo}})
            body = book(timestamp=datetime.now(timezone.utc).isoformat())
            if mode == "200-echo":
                echo = f"Bearer DUMMY-TOKEN-xyz dummy-secret http://127.0.0.1:{server.server_port}{self.path}"
                body["message"] = echo
                body["result"]["requestId"] = echo
                body["result"]["asks"][0]["note"] = echo
            return self._send(200, body)
        return self._send(200, {"result": "MUST-NOT-BE-REACHED"})

    do_GET = do_POST = do_PUT = do_DELETE = handle_any


_Client = None     # built in FenceTests.setUpClass: importing the broker package is undone in tearDownClass,
                    # because another test asserts that no broker module is loaded by the Scout modules


def _make_client_class(TossTransport):
    class _Client(TossTransport):
        """The real Toss transport (token handling, retries, rate-limit waits) without settings or the DB."""

        def __init__(self, base):
            self.base_url, self.client_id, self.client_secret, self.timeout = base, "dummy-id", "dummy-secret", 3
            self._access_token = self._token_expires_at = None
            self._token_lock, self._group_locks, self._group_locks_lock = Lock(), {}, Lock()
            self._request_waiter, self._group_last_request_at, self._group_rate_limit_until = Event(), {}, {}
            self._group_intervals = {"AUTH": 0.01, "MARKET_DATA": 0.01}
            self._last_request_at = 0.0
    return _Client


class FenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._modules_before = set(sys.modules)
        from auto_trader.brokers.toss.transport import TossTransport
        global _Client
        _Client = _make_client_class(TossTransport)
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        cls.server.lock = threading.Lock()
        cls.server.hits, cls.server.mode, cls.server.error_code = [], "ok", "invalid-symbol"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        for name in set(sys.modules) - cls._modules_before:
            if name == "auto_trader.brokers" or name.startswith("auto_trader.brokers."):
                del sys.modules[name]

    def setUp(self):
        self.server.hits.clear()
        self.server.mode, self.server.error_code = "ok", "invalid-symbol"

    def fenced(self, **kw):
        client = _Client(self.base)
        return client, guard_toss_orderbook_client(client, origins=(self.base,), **kw)

    def paths(self):
        return [(m, p.split("?")[0]) for m, p, _ in self.server.hits]

    def clock(self):
        return datetime.now(timezone.utc)

    def test_refused_requests_never_reach_the_network_nor_cost_an_authentication(self):
        client, counts = self.fenced()
        port = self.server.server_port
        bad = [f"{self.base}/api/v1/orderbook?symbol=abc", f"{self.base}/api/v1/orderbook?symbols=005930",
               f"{self.base}/api/v1/orderbook?symbol=005930&symbol=000660", f"{self.base}/api/v1/orderbook?symbol=005930&x=1",
               f"{self.base}/api/v1/orderbook?symbol=005930&", f"{self.base}/api/v1/orderbook?symbol=005930;x=1",
               f"{self.base}/api/v1/orderbook?symbol=005930#frag", f"{self.base}/api/v1/orderbook?symbol=%30%30%35%39%33%30",
               f"{self.base}/api/v1/orderbook?symbol=005930%20", f"{self.base}/api/v1/orderbook?symbol=", f"{self.base}/api/v1/orderbook",
               f"{self.base}/api/v1/orderbook?SYMBOL=005930", f"{self.base}/api/v1/orderbook?symbol=00593a",
               f"{self.base}/api/v1/orderbook/?symbol=005930", f"{self.base}/api/v1/orderbook;a?symbol=005930",
               f"{self.base}/api/v1/orders?symbol=005930", f"{self.base}/api/v1/accounts", f"{self.base}/api/v1/prices?symbol=005930",
               f"{self.base}/oauth2/token?symbol=005930",
               f"http://127.0.0.1:{port + 1}/api/v1/orderbook?symbol=005930", "https://openapi.tossinvest.com/api/v1/orderbook?symbol=005930",
               f"http://evil.example/api/v1/orderbook?symbol=005930", f"http://127.0.0.1:{port}@evil.example/api/v1/orderbook?symbol=005930",
               f"http://localhost:{port}/api/v1/orderbook?symbol=005930"]
        for url in bad:
            with self.subTest(url):
                with self.assertRaises(PermissionError):
                    client._authorized_json_request(url)
        for method, body, headers in (("POST", None, None), ("GET", {"a": 1}, None), ("GET", None, {"X-A": "b"})):
            with self.subTest((method, body, headers)):
                with self.assertRaises(PermissionError):
                    client._authorized_json_request(f"{self.base}/api/v1/orderbook?symbol=005930",
                                                    method=method, body=body, headers=headers)
        self.assertEqual(self.server.hits, [])                                        # not even a token request
        self.assertEqual(counts, {"total": 0, "paths": {}})

    def test_origin_must_be_https_or_loopback_and_bare(self):
        for origin in ("http://openapi.tossinvest.com", "https://openapi.tossinvest.com/x", "https://a@b.example",
                       "ftp://127.0.0.1", "https://", "https://h?q=1"):
            with self.subTest(origin):
                with self.assertRaises(ValueError):
                    guard_toss_orderbook_client(_Client(self.base), origins=(origin,))
        guard_toss_orderbook_client(_Client(self.base))                                # default: the official https origin
        with self.assertRaises(ValueError):
            guard_toss_orderbook_client(_Client(self.base), max_orderbook=7)

    def test_normal_run_uses_one_token_and_one_get_per_symbol(self):
        client, counts = self.fenced()
        results, stopped = fetch_orderbook_results(client, ["005930", "000660"], clock=self.clock)
        self.assertEqual((stopped, [r.status for r in results]), (None, [OK, OK]))
        self.assertEqual(self.paths(), [("POST", "/oauth2/token"), ("GET", "/api/v1/orderbook"), ("GET", "/api/v1/orderbook")])
        self.assertEqual(counts, {"total": 3, "paths": {"/oauth2/token": 1, "/api/v1/orderbook": 2}})
        self.assertTrue(all(a for _, _, a in self.server.hits if "orderbook" in _))

    def test_symbols_are_validated_before_any_io_and_unfenced_clients_are_refused(self):
        client, _ = self.fenced()
        for bad in (["abc"], ["005930", "005930"], [f"00000{i}" for i in range(5)], "005930", []):
            with self.assertRaises(ValueError):
                fetch_orderbook_results(client, bad, clock=self.clock)
        other = _Client(self.base)                              # a different instance: NOT covered by the fence
        with self.assertRaises(ValueError):
            fetch_orderbook_results(other, ["005930"], clock=self.clock)
        self.assertFalse(hasattr(other, "_day01_orderbook_fence"))
        self.assertEqual(self.server.hits, [])

    def test_429_is_bounded_and_stops_the_run(self):
        self.server.mode = "429"
        client, counts = self.fenced()
        results, stopped = fetch_orderbook_results(client, ["005930", "000660", "035420"], clock=self.clock)
        self.assertEqual(stopped, "FETCH_ERROR_429")
        self.assertEqual([r.reasons for r in results], [("FETCH_FAILED",), ("NOT_FETCHED",), ("NOT_FETCHED",)])
        self.assertEqual(counts["paths"], {"/oauth2/token": 1, "/api/v1/orderbook": 3})      # the client's own 3 attempts
        self.assertLessEqual(len(self.server.hits), 6)

    def test_reauthorization_counts_and_total_stays_bounded_across_many_calls(self):
        self.server.mode = "401"
        client, counts = self.fenced()
        outcomes = []
        for _ in range(10):
            try:
                client._authorized_json_request(f"{self.base}/api/v1/orderbook?symbol=005930")
            except Exception as exc:
                outcomes.append("PermissionError" if isinstance(exc, PermissionError) else type(exc).__name__)
        self.assertLessEqual(counts["paths"].get("/oauth2/token", 0), 2)
        self.assertLessEqual(counts["paths"].get("/api/v1/orderbook", 0), 4)
        self.assertLessEqual(counts["total"], 6)
        self.assertEqual(len(self.server.hits), counts["total"])                          # nothing slipped past the counter
        self.assertIn("PermissionError", outcomes)

    def test_total_cap_includes_authentication(self):
        client, counts = self.fenced(max_requests=2, max_auth=2, max_orderbook=2)
        client._authorized_json_request(f"{self.base}/api/v1/orderbook?symbol=005930")      # token + GET = 2
        with self.assertRaises(PermissionError):
            client._authorized_json_request(f"{self.base}/api/v1/orderbook?symbol=005930")
        self.assertEqual((counts["total"], len(self.server.hits)), (2, 2))
        client2, counts2 = self.fenced(max_requests=2, max_auth=2, max_orderbook=2)
        client2._access_token = None
        counts2["total"] = 1                                                                # one slot left: token alone would waste it
        with self.assertRaises(PermissionError):
            client2._authorized_json_request(f"{self.base}/api/v1/orderbook?symbol=005930")
        self.assertEqual(len(self.server.hits), 2)                                          # no new request was sent

    def expire(self, client, seconds=-1):
        client._token_expires_at = datetime.now(timezone.utc) + timedelta(seconds=seconds)

    def test_expired_token_is_a_token_need_so_a_small_cap_refuses_before_any_authentication(self):
        client, counts = self.fenced(max_requests=3, max_auth=2, max_orderbook=2)
        fetch_orderbook_results(client, ["005930"], clock=self.clock)                  # token + GET
        self.assertEqual((counts["total"], len(self.server.hits)), (2, 2))
        self.expire(client)                                                            # token present but expired
        results, stopped = fetch_orderbook_results(client, ["000660"], clock=self.clock)
        self.assertEqual((stopped, results[0].reasons), ("FENCE_REFUSED", ("FENCE_REFUSED",)))
        self.assertEqual((counts["total"], len(self.server.hits)), (2, 2))             # no token request was wasted
        client._token_expires_at = None                                                # unknown expiry: not provably usable
        with self.assertRaises(PermissionError):
            client._authorized_json_request(f"{self.base}/api/v1/orderbook?symbol=000660")
        self.assertEqual(len(self.server.hits), 2)

    def test_expired_token_is_reissued_once_when_the_caps_allow_and_caps_still_hold(self):
        client, counts = self.fenced()
        fetch_orderbook_results(client, ["005930"], clock=self.clock)
        self.expire(client)
        results, stopped = fetch_orderbook_results(client, ["000660"], clock=self.clock)
        self.assertEqual((stopped, results[0].status), (None, OK))
        self.assertEqual(counts["paths"], {"/oauth2/token": 2, "/api/v1/orderbook": 2})
        self.expire(client)
        results, stopped = fetch_orderbook_results(client, ["035420"], clock=self.clock)   # a third token: auth cap is 2
        self.assertEqual(stopped, "FENCE_REFUSED")
        self.assertEqual((counts["paths"]["/oauth2/token"], len(self.server.hits)), (2, 4))
        client2, _ = self.fenced()
        client2._access_token, client2._token_expires_at = "DUMMY", datetime.now(timezone.utc) + timedelta(hours=1)
        before = len(self.server.hits)
        fetch_orderbook_results(client2, ["005930"], clock=self.clock)                 # valid token: no authentication
        self.assertEqual([m for m, _ in self.paths()[before:]], ["GET"])

    def test_fetch_refuses_a_client_whose_transport_boundary_changed_after_fencing(self):
        from types import MethodType
        def raw_send(client):                       # the unfenced original transport entry point
            return MethodType(type(client)._json_request_once, client)
        tampering = {
            "send restored": lambda c: setattr(c, "_json_request_once", raw_send(c)),
            "send deleted (class method visible again)": lambda c: delattr(c, "_json_request_once"),
            "json_request unwrapped": lambda c: delattr(c, "_json_request"),
            "access_token unwrapped": lambda c: delattr(c, "access_token"),
            "entry point replaced": lambda c: setattr(c, "_authorized_json_request", lambda *a, **k: {"result": {}}),
            "weaker guard applied last": lambda c: src.guard_toss_client(c),
        }
        for name, tamper in tampering.items():
            with self.subTest(name):
                client, _ = self.fenced()
                self.assertTrue(src.fence_intact(client))
                tamper(client)
                self.assertFalse(src.fence_intact(client))
                with self.assertRaises(ValueError):
                    fetch_orderbook_results(client, ["005930"], clock=self.clock)
        self.assertEqual(self.server.hits, [])

    def test_fence_replaced_in_the_middle_of_a_run_stops_before_the_next_request(self):
        client, counts = self.fenced()
        def clock():
            client._json_request_once = type(client)._json_request_once.__get__(client)     # unfence after symbol 1
            return datetime.now(timezone.utc)
        results, stopped = fetch_orderbook_results(client, ["005930", "000660"], clock=clock)
        self.assertEqual((stopped, [r.status for r in results]), ("FENCE_REFUSED", [OK, UNKNOWN]))
        self.assertEqual(results[1].reasons, ("FENCE_REFUSED",))
        self.assertEqual(self.paths(), [("POST", "/oauth2/token"), ("GET", "/api/v1/orderbook")])   # symbol 2 never sent
        self.assertEqual(counts["total"], 2)

    def test_redirect_is_not_followed(self):
        self.server.mode = "302"
        client, counts = self.fenced()
        results, stopped = fetch_orderbook_results(client, ["005930"], clock=self.clock)
        self.assertEqual(stopped, "FETCH_ERROR_302")
        self.assertEqual(self.paths(), [("POST", "/oauth2/token"), ("GET", "/api/v1/orderbook")])    # /accounts never requested
        self.assertEqual(results[0].notes, ("status=302", "code=None"))

    def test_concurrent_callers_cannot_exceed_the_caps(self):
        client, counts = self.fenced()
        errors = []

        def work():
            try:
                client._authorized_json_request(f"{self.base}/api/v1/orderbook?symbol=005930")
            except Exception as exc:
                errors.append(type(exc).__name__)

        threads = [threading.Thread(target=work) for _ in range(16)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(sorted(set(errors)), ["FenceRefusal"])
        self.assertEqual(len(errors), 12)
        self.assertEqual(self.paths().count(("GET", "/api/v1/orderbook")), 4)
        self.assertEqual(self.paths().count(("POST", "/oauth2/token")), 1)
        self.assertEqual(counts, {"total": 5, "paths": {"/oauth2/token": 1, "/api/v1/orderbook": 4}})

    def test_bad_symbol_response_is_per_symbol_and_later_symbols_still_run(self):
        self.server.mode = "bad-symbol"
        client, _ = self.fenced()
        results, stopped = fetch_orderbook_results(client, ["000000", "005930"], clock=self.clock)
        self.assertEqual((stopped, [r.status for r in results]), (None, [UNKNOWN, OK]))
        self.assertEqual(results[0].reasons, ("FETCH_FAILED",))

    def test_errors_and_logs_never_carry_secrets_urls_or_provider_text(self):
        self.server.mode = "400-echo"
        client, _ = self.fenced()
        with self.assertRaises(Exception) as raised:
            client._authorized_json_request(f"{self.base}/api/v1/orderbook?symbol=005930")
        shown = f"{raised.exception!s} {raised.exception!r} {raised.exception.__cause__!r} {raised.exception.__context__!r}"
        for secret in ("DUMMY-TOKEN", "Bearer", "dummy-secret", "dummy-id", "127.0.0.1", "echo", "orderbook"):
            self.assertNotIn(secret, shown)
        self.assertEqual(raised.exception.error_code, "invalid-symbol")
        self.server.error_code = "Weird Code! Bearer DUMMY-TOKEN-xyz"
        with self.assertRaises(Exception) as raised:
            client._authorized_json_request(f"{self.base}/api/v1/orderbook?symbol=005930")
        self.assertIsNone(raised.exception.error_code)
        self.assertNotIn("DUMMY", f"{raised.exception!s}")
        with tempfile.TemporaryDirectory() as folder:
            log = ResearchLog(Path(folder) / "ob" / "log.jsonl", clock=self.clock)
            self.server.mode = "ok"
            results, _ = fetch_orderbook_results(client, ["005930"], clock=self.clock, log=log)
            self.server.mode = "400-echo"
            self.server.error_code = "invalid-symbol"
            fetch_orderbook_results(client, ["000660"], clock=self.clock, log=log)
            text = (Path(folder) / "ob" / "log.jsonl").read_text(encoding="utf-8")
        for secret in ("DUMMY-TOKEN", "Bearer", "dummy-secret", "dummy-id", "127.0.0.1", "echo"):
            self.assertNotIn(secret, text)
        lines = [json.loads(line) for line in text.splitlines()]
        self.assertEqual([line["kind"] for line in lines], ["orderbook_result"] * 2)
        self.assertEqual((lines[0]["status"], lines[1]["reasons"]), (OK, ["FETCH_FAILED"]))

    def test_request_object_tampering_cannot_change_what_is_sent(self):
        other = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        other.lock, other.hits, other.mode, other.error_code = threading.Lock(), [], "ok", "x"
        threading.Thread(target=other.serve_forever, daemon=True).start()
        self.addCleanup(other.server_close)
        self.addCleanup(other.shutdown)
        from urllib.request import Request
        client, counts = self.fenced(max_requests=20, max_orderbook=20)
        url = f"{self.base}/api/v1/orderbook?symbol=005930"
        auth = {"Authorization": "Bearer DUMMY", "Accept": "application/json"}
        other_host = f"127.0.0.1:{other.server_port}"

        def tampered(**attrs):
            request = Request(url, headers=auth)
            for name, value in attrs.items():
                setattr(request, name, value)
            return request

        sendable = [tampered(host=other_host, selector="/api/v1/accounts"), tampered(selector="/api/v1/accounts"),
                    tampered(host=other_host), tampered(type="https"), tampered(_tunnel_host=other_host)]
        proxied = tampered()
        proxied.set_proxy(other_host, "http")
        sendable.append(proxied)
        for request in sendable:
            client._json_request(request)                    # a private clone of the validated URL is what goes out
        refused = [Request(url, headers={**auth, "Host": "evil.example"}), Request(url, headers={**auth, "host": "x"}),
                   Request(url, headers={**auth, "X-Forwarded-For": "1"}), Request(url, headers={"Authorization": "Basic abc"}),
                   Request(url, headers={**auth, "Authorization": "Bearer a\r\nX: y"}), Request(url, data=b"x", headers=auth),
                   Request(url, headers=auth, method="POST")]
        for request in refused:
            with self.assertRaises(PermissionError):
                client._json_request(request)
        self.assertEqual(other.hits, [])                                                      # nothing reached host B
        self.assertEqual({p.split("?")[0] for _, p, _ in self.server.hits}, {"/api/v1/orderbook"})
        self.assertNotIn("Host: evil.example", str(self.server.hits))

    def test_tampering_between_validation_and_send_is_harmless(self):
        other = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        other.lock, other.hits, other.mode, other.error_code = threading.Lock(), [], "ok", "x"
        threading.Thread(target=other.serve_forever, daemon=True).start()
        self.addCleanup(other.server_close)
        self.addCleanup(other.shutdown)
        from urllib.request import Request
        client, counts = self.fenced()
        request = Request(f"{self.base}/api/v1/orderbook?symbol=005930",
                          headers={"Authorization": "Bearer DUMMY", "Accept": "application/json"})
        src._FENCE_LOCK.acquire()                              # another caller is "inside" the fence
        worker = threading.Thread(target=lambda: client._json_request(request))
        worker.start()
        request.host, request.selector = f"127.0.0.1:{other.server_port}", "/api/v1/accounts"   # mutate while it waits
        src._FENCE_LOCK.release()
        worker.join()
        self.assertEqual(other.hits, [])
        self.assertEqual([p for _, p, _ in self.server.hits], ["/api/v1/orderbook?symbol=005930"])

    def test_every_entry_point_is_sanitized_and_exceptions_carry_no_context(self):
        from urllib.request import Request

        def reachable(exc):
            seen, stack = [], [exc]
            while stack:
                e = stack.pop()
                if e is None or e in seen:
                    continue
                seen.append(e)
                stack += [e.__cause__, e.__context__]
            return " ".join(f"{e!s} {e!r} {getattr(e, '__notes__', '')}" for e in seen)

        secrets = ("DUMMY", "Bearer", "dummy-secret", "dummy-id", "127.0.0.1", "echo", "client_secret", "orderbook", "oauth2")
        self.server.mode = "token-400"
        client, _ = self.fenced()
        with self.assertRaises(Exception) as raised:
            client.access_token()                                             # direct token entry point
        for secret in secrets:
            self.assertNotIn(secret, reachable(raised.exception))
        self.assertEqual(raised.exception.error_code, "invalid-client")
        self.server.mode = "400-echo"
        client, _ = self.fenced()
        client._access_token, client._token_expires_at = "x", datetime.now(timezone.utc) + timedelta(hours=1)
        with self.assertRaises(Exception) as raised:
            client._json_request(Request(f"{self.base}/api/v1/orderbook?symbol=005930", method="GET"))   # transport entry point
        for secret in secrets:
            self.assertNotIn(secret, reachable(raised.exception))
        for mode, call in (("token-bad-expires", lambda c: c.access_token()), ("token-array", lambda c: c.access_token()),
                           ("token-bad-expires", lambda c: c._authorized_json_request(f"{self.base}/api/v1/orderbook?symbol=005930")),
                           ("400-array", lambda c: c._authorized_json_request(f"{self.base}/api/v1/orderbook?symbol=005930"))):
            self.server.mode = mode                                             # failures raised OUTSIDE the send boundary
            client, _ = self.fenced()
            if mode == "400-array":
                client._access_token, client._token_expires_at = "x", datetime.now(timezone.utc) + timedelta(hours=1)
            with self.assertRaises(Exception) as raised:
                call(client)
            for secret in secrets:
                self.assertNotIn(secret, reachable(raised.exception))
            self.assertIsNone(raised.exception.__context__)
            self.assertIsNone(raised.exception.__cause__)
        self.server.mode = "401"                                              # re-authorization refused by the auth cap
        client, counts = self.fenced(max_auth=1)
        with self.assertRaises(PermissionError) as raised:
            client._authorized_json_request(f"{self.base}/api/v1/orderbook?symbol=005930")
        for secret in secrets:
            self.assertNotIn(secret, reachable(raised.exception))
        self.assertIsNone(raised.exception.__context__)                       # not even the sanitized predecessor
        self.assertIsNone(raised.exception.__cause__)
        self.assertEqual(counts["paths"]["/oauth2/token"], 1)

    def test_a_200_response_with_secret_looking_text_is_not_written_to_the_log(self):
        self.server.mode = "200-echo"
        client, _ = self.fenced()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "ob" / "log.jsonl"
            results, _ = fetch_orderbook_results(client, ["005930"], clock=self.clock, log=ResearchLog(path, clock=self.clock))
            text = path.read_text(encoding="utf-8")
        self.assertEqual(results[0].status, OK)
        for secret in ("DUMMY", "Bearer", "dummy-secret", "127.0.0.1", "requestId", '"note"', '"message"'):
            self.assertNotIn(secret, text)
        self.assertIn('"price":"70100"', text)

    def test_clock_regression_stops_the_run(self):
        client, _ = self.fenced()
        stamps = iter([NOW, NOW - timedelta(seconds=1)])
        results, stopped = fetch_orderbook_results(client, ["005930", "000660", "035420"],
                                                   clock=lambda: next(stamps), max_age_seconds=10**6)
        self.assertEqual(stopped, "CLOCK_REGRESSION")
        self.assertEqual([r.reasons for r in results][1:], [("CLOCK_REGRESSION",), ("NOT_FETCHED",)])

    def test_legacy_guard_defaults_are_unchanged(self):
        client = _Client(self.base)
        counts = src.guard_toss_client(client, max_requests=3)
        self.assertFalse(hasattr(client, "_day01_orderbook_fence"))
        with self.assertRaises(PermissionError):
            client._authorized_json_request(f"{self.base}/api/v1/orderbook?symbol=005930")   # orderbook not in legacy allowlist
        self.assertEqual(counts["paths"], {"/oauth2/token": 1})


class ScoutUnchangedTests(unittest.TestCase):
    """The spread only fills LiquidityFacts; Scout's own gates decide, and UNKNOWN never becomes eligible."""

    def liquidity_with(self, result):
        helper = ScoutEndToEndTests("test_without_an_order_book_scout_still_excludes_everyone_and_nothing_is_fully_eligible")
        snap = snapshot(("005930",))
        _, inputs = helper.inputs(bars("005930"), snap)
        facts = tuple(with_spread(f, result, as_of=AS_OF) for f in inputs.liquidity)
        return helper, snap, replace(inputs, liquidity=facts)

    def result(self, payload):
        received = (AS_OF - timedelta(seconds=3)).astimezone(timezone.utc)
        return parse_orderbook("005930", payload, received_at=received)

    def reasons(self, payload):
        helper, snap, inputs = self.liquidity_with(self.result(payload))
        scouted = helper.scout(inputs, snap)
        return scouted, [r for e in scouted.exclusions for r in e.reasons]

    def stamp(self, seconds_before=5):
        return (AS_OF - timedelta(seconds=seconds_before)).astimezone(timezone.utc).isoformat()

    def test_unknown_book_stays_excluded_and_never_eligible(self):
        for payload in (book(timestamp=self.stamp()) | {"result": {**book(timestamp=self.stamp())["result"], "bids": []}},
                        book(timestamp="2026-11-13T00:00:00+09:00"), {}, book(asks=(("70000", "1"),), bids=(("70100", "1"),))):
            scouted, reasons = self.reasons(payload)
            self.assertEqual(scouted.candidates, ())
            self.assertIn("LIQUIDITY_SPREAD_UNKNOWN", reasons)

    def test_known_spread_is_judged_by_scouts_unchanged_one_percent_limit(self):
        narrow, reasons = self.reasons(book(timestamp=self.stamp()))                          # ~0.14 %
        self.assertFalse([r for r in reasons if r.startswith("LIQUIDITY_SPREAD")])
        _, reasons = self.reasons(book(asks=(("70800", "1"),), bids=(("70000", "1"),), timestamp=self.stamp()))   # ~1.14 %
        self.assertIn("LIQUIDITY_SPREAD_TOO_WIDE", reasons)
        self.assertEqual(sc.ScoutConfig().max_spread_pct, D("1"))

    def test_with_spread_rules(self):
        helper, snap, inputs = self.liquidity_with(self.result(book(timestamp=self.stamp())))
        fact = inputs.liquidity[0]
        good = self.result(book(timestamp=self.stamp()))
        self.assertIsNotNone(fact.spread_pct)
        unknown = self.result({})
        self.assertIsNone(with_spread(fact, unknown, as_of=AS_OF).spread_pct)                    # UNKNOWN overrides
        same = with_spread(fact, unknown, as_of=AS_OF)
        self.assertEqual((same.turnover, same.volume), (fact.turnover, fact.volume))
        with self.assertRaises(ValueError):
            with_spread(fact, replace(unknown, symbol="000660"), as_of=AS_OF)
        with self.assertRaises(ValueError):
            with_spread(fact, good, as_of=datetime(2026, 11, 13, 9, 20))                        # naive as_of
        base = replace(fact, spread_pct=None)
        self.assertEqual(with_spread(base, good, as_of=AS_OF).observed_at, min(base.observed_at, good.received_at))
        # a book received AFTER the decision time did not exist for that decision (no back-dating, no leak)
        late = parse_orderbook("005930", book(timestamp=self.stamp(-1)),
                               received_at=(AS_OF + timedelta(seconds=1)).astimezone(timezone.utc))
        self.assertEqual(late.status, OK)
        self.assertIsNone(with_spread(base, late, as_of=AS_OF).spread_pct)
        self.assertIsNotNone(with_spread(base, late, as_of=AS_OF + timedelta(seconds=1)).spread_pct)
        # the book must still be fresh AT the decision time (provider time), not only when it was received
        self.assertIsNotNone(with_spread(base, good, as_of=AS_OF + timedelta(seconds=5)).spread_pct)     # 10.0 s
        self.assertIsNone(with_spread(base, good, as_of=AS_OF + timedelta(seconds=6)).spread_pct)        # 11.0 s
        self.assertIsNotNone(with_spread(base, good, as_of=AS_OF + timedelta(seconds=6), max_age_seconds=20).spread_pct)
        self.assertIsNone(with_spread(replace(base, observed_at=AS_OF + timedelta(seconds=1)), good, as_of=AS_OF).spread_pct)

    def test_freshness_window_is_validated_identically_and_cannot_be_loosened(self):
        helper, snap, inputs = self.liquidity_with(self.result(book(timestamp=self.stamp())))
        base = replace(inputs.liquidity[0], spread_pct=None)
        good = self.result(book(timestamp=self.stamp()))
        bad = (0, -1, 10**6 + 1, 10**7, 10**30, float("nan"), float("inf"), D("NaN"), "abc", "", True, None, [20])
        for value in bad:
            with self.subTest(value):
                with self.assertRaises(ValueError):
                    with_spread(base, good, as_of=AS_OF, max_age_seconds=value)
                with self.assertRaises(ValueError):
                    parse(book(), max_age_seconds=value)
        self.assertIsNotNone(with_spread(base, good, as_of=AS_OF, max_age_seconds=10**6).spread_pct)   # upper bound inclusive
        for value in (10, 10.0, "10", D("10")):                              # the default stays 10 s, whatever the type
            self.assertIsNotNone(with_spread(base, good, as_of=AS_OF + timedelta(seconds=5), max_age_seconds=value).spread_pct)
            self.assertIsNone(with_spread(base, good, as_of=AS_OF + timedelta(seconds=6), max_age_seconds=value).spread_pct)
        self.assertEqual(ob.DEFAULT_MAX_AGE_SECONDS, D(10))
        old_book = self.result(book(timestamp=self.stamp(3600)))             # an hour old: never a normal spread
        self.assertEqual(old_book.status, UNKNOWN)
        self.assertIsNone(with_spread(base, old_book, as_of=AS_OF).spread_pct)
        scouted, reasons = self.reasons(book(timestamp=self.stamp(3600)))
        self.assertEqual(scouted.candidates, ())
        self.assertIn("LIQUIDITY_SPREAD_UNKNOWN", reasons)

    def test_an_old_ranking_does_not_become_fresh_by_merging_a_new_book(self):
        helper, snap, inputs = self.liquidity_with(self.result(book(timestamp=self.stamp())))
        good = self.result(book(timestamp=self.stamp()))
        max_age = sc.ScoutConfig().max_fact_age
        for age, stale in ((max_age - timedelta(seconds=1), False), (max_age, True)):          # exclusive boundary
            old = replace(inputs.liquidity[0], spread_pct=None, observed_at=(AS_OF - age).astimezone(timezone.utc))
            merged = with_spread(old, good, as_of=AS_OF)
            self.assertEqual(merged.observed_at, old.observed_at)                                # earlier stamp is kept
            scouted = helper.scout(replace(inputs, liquidity=(merged,)), snap)
            reasons = [r for e in scouted.exclusions for r in e.reasons]
            self.assertEqual("LIQUIDITY_STALE" in reasons, stale)
            if stale:
                self.assertEqual(scouted.candidates, ())

    def test_ambient_decimal_context_cannot_change_or_abort_conversion(self):
        import decimal
        expected = parse(book())
        with decimal.localcontext() as ambient:
            ambient.prec = 3
            ambient.traps[decimal.Inexact] = True
            ambient.traps[decimal.Rounded] = True
            r = parse(book(asks=(("70100", "1"), ("70100", "0.000000001")), bids=(("70000", "1500"),)))
            self.assertEqual(r.status, OK)
            self.assertEqual(r.ask_volume, D("1.000000001"))                  # exact sum, no Inexact abort
            self.assertEqual(parse(book()).spread_pct, expected.spread_pct)
            self.assertEqual(parse(book()).age_seconds, D("5.000"))
            self.assertIsNotNone(ob._safe_raw(book()))
            late = with_spread(replace(LiquidityFactsStub.fact(), spread_pct=None), parse(book()), as_of=NOW)
            self.assertEqual(late.spread_pct, expected.spread_pct)


class LiquidityFactsStub:
    @staticmethod
    def fact():
        return sc.LiquidityFacts("005930", NOW - timedelta(seconds=1), turnover="20000000000", volume="100000")


class BoundaryTests(unittest.TestCase):
    def test_orderbook_module_imports_no_settings_db_broker_or_order_code(self):
        tree = ast.parse(Path(ob.__file__).read_text(encoding="utf-8"))
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                names.add(("." * node.level) + (node.module or ""))
        allowed = {"__future__", "re", "dataclasses", "datetime", "decimal", "fractions", "typing", "urllib.parse",
                   ".day01_scout", ".day01_scout_source", ".brokers.toss.request_budget"}
        self.assertEqual(names - allowed, set())


if __name__ == "__main__":
    unittest.main()
