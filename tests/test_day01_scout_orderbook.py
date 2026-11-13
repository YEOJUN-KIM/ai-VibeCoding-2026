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
        self.assertEqual(r.spread_pct, D("100") / (D("140100") / 2) * 100)         # (ask-bid)/mid*100, percent
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
        self.assertEqual(parse(book(timestamp="2026-11-13T10:11:06+09:00")).status, OK)           # 1 s ahead: tolerated
        self.assertEqual(parse(book(timestamp="2026-11-13T10:11:09+09:00")).reasons, ("TIMESTAMP_IN_FUTURE",))
        with self.assertRaises(ValueError):
            parse_orderbook("005930", book(), received_at=datetime(2026, 11, 13, 10, 11, 5))     # naive receipt time
        with self.assertRaises(ValueError):
            parse(book(), max_age_seconds=0)

    def test_anomalous_spread_is_separate_from_unknown_and_not_passed_on(self):
        exactly = parse(book(asks=(("5", "1"),), bids=(("3", "1"),)))               # 2*(2)/(8) = 50 % exactly
        self.assertEqual((exactly.status, exactly.spread_pct), (OK, D("50")))
        wide = parse(book(asks=(("5.01", "1"),), bids=(("3", "1"),)))
        self.assertEqual((wide.status, wide.spread_pct, wide.reasons), (ANOMALY, None, ("ANOMALOUS_SPREAD",)))
        self.assertGreater(wide.observed_spread_pct, 50)

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
            return self._send(200, {"access_token": "DUMMY-TOKEN-xyz", "expires_in": 3600})
        if parts.path == "/api/v1/orderbook":
            symbol = (parse_qs(parts.query).get("symbol") or [""])[0]
            mode = server.mode
            if mode == "429":
                return self._send(429, {"error": {"code": "rate-limit-exceeded", "message": "slow"}}, {"Retry-After": "0"})
            if mode == "401":
                return self._send(401, {"error": {"code": "invalid-token", "message": "bad"}})
            if mode == "302":
                return self._send(302, {}, {"Location": "/api/v1/accounts"})
            if mode == "400-echo" or (mode == "bad-symbol" and symbol == "000000"):
                echo = f"Bearer DUMMY-TOKEN-xyz dummy-secret http://127.0.0.1:{server.server_port}{self.path}"
                return self._send(400, {"error": {"code": server.error_code, "message": echo}})
            return self._send(200, book(timestamp=datetime.now(timezone.utc).isoformat()))
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
                outcomes.append(type(exc).__name__)
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
        self.assertEqual(sorted(set(errors)), ["PermissionError"])
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

    def test_clock_regression_stops_the_run(self):
        client, _ = self.fenced()
        stamps = iter([NOW, NOW - timedelta(seconds=1)])
        results, stopped = fetch_orderbook_results(client, ["005930", "000660", "035420"],
                                                   clock=lambda: next(stamps), max_age_seconds=10**9)
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
        facts = tuple(with_spread(f, result) for f in inputs.liquidity)
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
        self.assertIsNotNone(fact.spread_pct)
        unknown = self.result({})
        self.assertIsNone(with_spread(fact, unknown).spread_pct)                                # UNKNOWN overrides
        self.assertEqual((with_spread(fact, unknown).turnover, with_spread(fact, unknown).volume), (fact.turnover, fact.volume))
        with self.assertRaises(ValueError):
            with_spread(fact, replace(unknown, symbol="000660"))
        self.assertLessEqual(fact.observed_at, snap.received_at)                               # earlier of the two stamps


class BoundaryTests(unittest.TestCase):
    def test_orderbook_module_imports_no_settings_db_broker_or_order_code(self):
        tree = ast.parse(Path(ob.__file__).read_text(encoding="utf-8"))
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                names.add(("." * node.level) + (node.module or ""))
        allowed = {"__future__", "re", "dataclasses", "datetime", "decimal", "typing", "urllib.parse",
                   ".day01_scout", ".day01_scout_source", ".brokers.toss.request_budget"}
        self.assertEqual(names - allowed, set())


if __name__ == "__main__":
    unittest.main()
