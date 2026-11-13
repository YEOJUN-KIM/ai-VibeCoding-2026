"""Synthetic tests for the ranking source adapter/log. Fixtures follow the field names read from
the existing client code (rank, symbol, tradingAmount, tradingVolume, price.lastPrice/changeRate);
no network, DB, credentials, clock or broker."""
import ast
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from auto_trader import day01_scout as sc
from auto_trader import day01_scout_link as link
from auto_trader import day01_scout_source as src
from auto_trader.day01_scout import ScoutState, ingest_fetch, refresh
from auto_trader.day01_scout_source import (
    ClockRegression, RankingFetcher, ResearchLog, guard_toss_client, toss_ranking_source)
from auto_trader.ml.day01 import Config, KST, replay
from tests.test_day01_scout_link import breakout, buys, scout_result

T0 = datetime(2026, 11, 13, 0, 10, 0, tzinfo=timezone.utc)     # 09:10 KST
RANKED = T0 - timedelta(seconds=15)
CFG = Config(execution='legacy_close')
CONFIG10 = link.scout_config_for(CFG, target_count=10)


def rows(count=60, price='5000', start=1):
    return [{"rank": r, "symbol": f"S{r:03d}", "name": f"N{r}", "tradingAmount": "20000000000",
             "tradingVolume": "100000", "price": {"lastPrice": price, "changeRate": "1.5"}}
            for r in range(start, start + count)]


class Clock:
    def __init__(self, *times):
        self.times, self.calls = list(times), 0

    def __call__(self):
        self.calls += 1
        return self.times[min(self.calls - 1, len(self.times) - 1)]


def payload(ranked=RANKED, data=None):
    return (ranked.isoformat() if ranked else None, rows() if data is None else data)


def facts(symbols, obs):
    return dict(
        instruments=[sc.InstrumentFacts(s, obs, market="KOSPI", currency="KRW", security_type="STOCK",
                                        is_common_share=True, status="ACTIVE", krx_trading_suspended=False,
                                        liquidation_trading=False) for s in symbols],
        liquidity=[sc.LiquidityFacts(s, obs, turnover="20000000000", volume="100000", spread_pct="0.5")
                   for s in symbols],
        dayone=[sc.DayOneFacts(s, obs, completed_1m_bars=20, opening_range_ready=True, vwap_ready=True)
                for s in symbols])


class ReceiptTimeTests(unittest.TestCase):
    def test_cache_hit_keeps_original_receipt_and_never_restamps(self):
        cached = payload()
        clock = Clock(T0, T0 + timedelta(seconds=30), T0 + timedelta(seconds=300))
        fetcher = RankingFetcher(lambda: cached, clock=clock)
        first, second = fetcher(), fetcher()
        self.assertEqual(first.received_at, T0)
        self.assertEqual(second.received_at, T0)
        self.assertEqual(clock.calls, 1)                       # clock consulted only once
        state, out1 = ingest_fetch(ScoutState.empty(), fetcher, clock=Clock(T0 + timedelta(seconds=40)))
        state, out2 = ingest_fetch(state, fetcher, clock=Clock(T0 + timedelta(seconds=400)))
        self.assertEqual((out1.status, out2.status), ("INGESTED", "DUPLICATE"))
        self.assertEqual(len(state.snapshots), 1)
        self.assertEqual(state.snapshots[0].received_at, T0)

    def test_new_payload_object_gets_a_new_stamp_taken_after_the_source_returns(self):
        order = []
        clock = Clock(T0, T0 + timedelta(seconds=45))

        def source():
            order.append("source")
            return payload(ranked=T0 + timedelta(seconds=len(order) * 10), data=rows(55 + len(order)))

        def stamped():
            order.append("clock")
            return clock()

        fetcher = RankingFetcher(source, clock=stamped)
        a, b = fetcher(), fetcher()
        self.assertEqual(order, ["source", "clock", "source", "clock"])
        self.assertEqual((a.received_at, b.received_at), (T0, T0 + timedelta(seconds=45)))

    def test_missing_provider_time_stays_missing_and_blocks_entries(self):
        fetcher = RankingFetcher(lambda: payload(ranked=None), clock=Clock(T0))
        state, out = ingest_fetch(ScoutState.empty(), fetcher, clock=Clock(T0))
        self.assertEqual(out.status, "INGESTED")
        self.assertIsNone(state.snapshots[0].provider_ranked_at)           # never fabricated
        result = refresh(state, T0 + timedelta(seconds=5), config=CONFIG10)[1].result
        self.assertEqual(result.status, sc.UNKNOWN_PROVIDER_TIME)
        self.assertEqual(result.candidates, ())

    def test_future_receipt_is_rejected_and_stale_data_is_not_current(self):
        fetcher = RankingFetcher(lambda: payload(), clock=Clock(T0 + timedelta(hours=1)))
        _, out = ingest_fetch(ScoutState.empty(), fetcher, clock=Clock(T0))
        self.assertEqual((out.status, out.reason), ("REJECTED", "RECEIPT_IN_FUTURE"))
        ok = RankingFetcher(lambda: payload(), clock=Clock(T0))
        state, _ = ingest_fetch(ScoutState.empty(), ok, clock=Clock(T0))
        stale = refresh(state, T0 + timedelta(seconds=500), config=CONFIG10)[1].result
        self.assertEqual(stale.status, sc.STALE)
        self.assertEqual(stale.candidates, ())

    def test_no_history_is_reconstructed_from_a_current_snapshot(self):
        fetcher = RankingFetcher(lambda: payload(), clock=Clock(T0))
        state, _ = ingest_fetch(ScoutState.empty(), fetcher, clock=Clock(T0))
        before = refresh(state, T0 - timedelta(minutes=5), config=CONFIG10)[1].result
        self.assertEqual(before.status, sc.NO_SNAPSHOT)
        self.assertEqual(before.candidates, ())


class ClockAndIdentityTests(unittest.TestCase):
    def test_wall_clock_regression_fails_closed(self):
        # Real receipt ~10:00:20 but the wall clock fell back to 10:00:05 after an earlier stamp.
        payloads = iter([payload(), payload(data=rows(61))])
        wall = Clock(T0, T0 - timedelta(seconds=15))
        mono = Clock(*[0.0, 1.0])
        fetcher = RankingFetcher(lambda: next(payloads), clock=wall, monotonic=lambda: mono())
        fetcher()
        with self.assertRaises(ClockRegression):
            fetcher()
        state, out = ingest_fetch(ScoutState.empty(), RankingFetcher(
            lambda: payload(), clock=lambda: datetime(2026, 11, 13, 0, 10, 0)), clock=Clock(T0))
        self.assertEqual((out.status, out.reason), ("FETCH_FAILED", "ClockRegression"))
        self.assertEqual(state.snapshots, ())

    def test_wall_clock_lagging_the_monotonic_clock_is_rejected(self):
        payloads = iter([payload(), payload(data=rows(61))])
        wall = Clock(T0, T0 + timedelta(seconds=1))                 # wall +1 s ...
        mono = Clock(*[0.0, 60.0])                                  # ... while monotonic +60 s
        fetcher = RankingFetcher(lambda: next(payloads), clock=wall, monotonic=lambda: mono())
        fetcher()
        with self.assertRaises(ClockRegression):
            fetcher()

    def test_object_returning_after_another_is_not_restamped(self):
        a, b = payload(), payload(data=rows(61))
        sequence = iter([a, b, a])
        clock = Clock(T0, T0 + timedelta(seconds=40), T0 + timedelta(seconds=80))
        fetcher = RankingFetcher(lambda: next(sequence), clock=clock)
        first, _, third = fetcher(), fetcher(), fetcher()
        self.assertEqual(third.received_at, first.received_at)
        self.assertEqual(clock.calls, 2)

    def test_log_failure_before_commit_is_not_remembered_and_raw_rows_are_written_on_retry(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "log.jsonl"
        log = ResearchLog(path, clock=Clock(T0))
        real_append, calls = log.append, []

        def flaky(kind, **fields):
            calls.append(kind)
            if len(calls) == 1:
                raise OSError("disk full")
            return real_append(kind, **fields)

        log.append = flaky
        cached = payload()
        fetcher = RankingFetcher(lambda: cached, clock=Clock(T0, T0 + timedelta(seconds=5)), log=log)
        with self.assertRaises(OSError):
            fetcher()
        result = fetcher()
        kinds = [json.loads(x)["kind"] for x in path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(kinds, ["fetch_ok"])                       # raw rows recorded, not just a cache hit
        self.assertEqual(result.received_at, T0 + timedelta(seconds=5))   # later stamp is only conservative


class GuardTests(unittest.TestCase):
    class Request:
        def __init__(self, url, method="GET"):
            self.full_url, self._method = url, method

        def get_method(self):
            return self._method

    class Client:
        def __init__(self):
            self.sent = []

        def _json_request_once(self, request):
            self.sent.append(request.full_url)
            return {"ok": True}

    def test_only_token_and_ranking_requests_pass_and_cap_applies(self):
        client = self.Client()
        counts = guard_toss_client(client, max_requests=3)
        R = self.Request
        client._json_request_once(R("https://x/oauth2/token", "POST"))
        client._json_request_once(R("https://x/api/v1/rankings?type=A"))
        for bad in (R("https://x/api/v1/orders", "POST"), R("https://x/api/v1/accounts"), R("https://x/api/v1/holdings"),
                    R("https://x/api/v1/rankings", "POST"), R("https://x/oauth2/token", "GET"),
                    R("https://x/api/v1/stocks/all")):
            with self.assertRaises(PermissionError):
                client._json_request_once(bad)
        client._json_request_once(R("https://x/api/v1/rankings"))
        with self.assertRaises(PermissionError):                      # cap reached
            client._json_request_once(R("https://x/api/v1/rankings"))
        self.assertEqual(counts, {"total": 3, "paths": {"/oauth2/token": 1, "/api/v1/rankings": 2}})
        self.assertEqual(len(client.sent), 3)                         # blocked requests never reached the transport


class FailureAndLogTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = Path(self.dir.name) / "scout" / "log.jsonl"

    def lines(self):
        return [json.loads(x) for x in self.path.read_text(encoding="utf-8").splitlines()]

    def test_failure_logs_type_status_code_only_and_state_is_unchanged(self):
        class Boom(Exception):
            status_code, error_code = 429, "rate-limit-exceeded"

            def __str__(self):
                return "Bearer abc.def.ghi secret message"

        def fail():
            raise Boom()

        log = ResearchLog(self.path, clock=Clock(T0))
        state = ScoutState.empty()
        new, out = ingest_fetch(state, RankingFetcher(fail, clock=Clock(T0), log=log), clock=Clock(T0))
        self.assertEqual((out.status, out.reason), ("FETCH_FAILED", "Boom"))
        self.assertIs(new, state)
        text = self.path.read_text(encoding="utf-8")
        self.assertNotIn("Bearer", text)
        self.assertNotIn("secret message", text)
        rec, = self.lines()
        self.assertEqual((rec["kind"], rec["error_type"], rec["status_code"], rec["error_code"]),
                         ("fetch_failed", "Boom", 429, "rate-limit-exceeded"))

    def test_log_is_append_only_with_raw_rows_once_and_quality_fields(self):
        log = ResearchLog(self.path, clock=Clock(T0))
        cached = payload()
        fetcher = RankingFetcher(lambda: cached, clock=Clock(T0), log=log)
        fetcher()
        first_line = self.path.read_text(encoding="utf-8").splitlines()[0]
        fetcher()
        recs = self.lines()
        self.assertEqual(self.path.read_text(encoding="utf-8").splitlines()[0], first_line)  # untouched
        self.assertEqual([r["kind"] for r in recs], ["fetch_ok", "fetch_cache_hit"])
        ok, hit = recs
        self.assertEqual(len(ok["rows"]), 60)
        self.assertNotIn("rows", hit)
        self.assertEqual(ok["received_at"], hit["received_at"])
        self.assertEqual(ok["provider_ranked_at_raw"], RANKED.isoformat())
        self.assertEqual(ok["quality"]["provider_time"], "PRESENT")
        self.assertEqual(ok["quality"]["parsed_rows"], 60)
        self.assertEqual(ok["schema"], src.LOG_SCHEMA)

    def test_same_ranked_at_with_different_content_is_logged_as_conflict_and_blocks_entries(self):
        payloads = iter([payload(), payload(data=rows(60, price='6000'))])
        log = ResearchLog(self.path, clock=Clock(T0))
        fetcher = RankingFetcher(lambda: next(payloads), clock=Clock(T0, T0 + timedelta(seconds=10)), log=log)
        state = ScoutState.empty()
        state, _ = ingest_fetch(state, fetcher, clock=Clock(T0 + timedelta(seconds=11)))
        state, _ = ingest_fetch(state, fetcher, clock=Clock(T0 + timedelta(seconds=11)))
        conflict = next(r for r in self.lines() if r["kind"] == "conflict")
        self.assertEqual(conflict["reason"], "SAME_RANKED_AT_DIFFERENT_CONTENT")
        self.assertNotEqual(conflict["first_raw_digest"], conflict["new_raw_digest"])
        self.assertEqual(len(state.snapshots), 2)                       # both originals preserved
        res = refresh(state, T0 + timedelta(seconds=12), config=CONFIG10)[1].result
        self.assertEqual(res.status, sc.INVALID_SNAPSHOT)
        self.assertEqual(res.candidates, ())

    def test_secret_like_keys_are_redacted_and_bad_paths_rejected(self):
        log = ResearchLog(self.path, clock=Clock(T0))
        dirty = rows(2)
        dirty[0]["accessToken"] = "TOP-SECRET-VALUE"
        dirty[1]["nested"] = {"Authorization": "Bearer xyz"}
        RankingFetcher(lambda: payload(data=dirty), clock=Clock(T0), log=log)()
        text = self.path.read_text(encoding="utf-8")
        self.assertNotIn("TOP-SECRET-VALUE", text)
        self.assertNotIn("Bearer xyz", text)
        self.assertIn("[REDACTED]", text)
        base = Path(self.dir.name)
        for bad in (base / ".BACKUPS" / "a.jsonl", base / ".Paper-History" / "a.jsonl", base / "x.JSON", "relative.jsonl", base / "x.txt", base / ".env" / "a.jsonl", base / ".backups" / "a.jsonl",
                    base / ".paper-history" / "a.jsonl", base / ".local-secrets" / "a.jsonl", base / ".env.jsonl"):
            with self.assertRaises(ValueError, msg=str(bad)):
                ResearchLog(bad, clock=Clock(T0))

    def test_link_into_protected_directory_is_rejected_at_creation_and_on_append(self):
        base = Path(self.dir.name)
        protected = base / ".backups"
        protected.mkdir()
        link_dir = base / "innocent"
        try:
            link_dir.symlink_to(protected, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation not permitted here")
        with self.assertRaises(ValueError):
            ResearchLog(link_dir / "a.jsonl", clock=Clock(T0))
        self.assertEqual(list(protected.iterdir()), [])

    def test_scout_result_record_serializes(self):
        result = scout_result(datetime(2026, 11, 13, 9, 19, 50, tzinfo=KST), ["AAA"])
        ResearchLog(self.path, clock=Clock(T0)).record_scout_result(result, fact_provenance="SYNTHETIC")
        rec, = self.lines()
        self.assertEqual((rec["kind"], rec["candidates"], rec["fact_provenance"]),
                         ("scout_result", ["AAA"], "SYNTHETIC"))


class ScoutOnRankingContractTests(unittest.TestCase):
    def ingest(self, data, ranked=RANKED, received=T0):
        fetcher = RankingFetcher(lambda: payload(ranked, data), clock=Clock(received))
        state, _ = ingest_fetch(ScoutState.empty(), fetcher, clock=Clock(received))
        return state

    def result(self, state, as_of=T0 + timedelta(seconds=10)):
        symbols = sorted({r.symbol for s in state.snapshots for r in s.rows})
        obs = as_of - timedelta(seconds=5)
        pf = sc.Portfolio(obs, cash="1000000", total_asset="1000000", **link.portfolio_limits_for(CFG))
        return sc.select_watchlist(as_of, state.snapshots, portfolio=pf, risk_config=CFG, config=CONFIG10,
                                   **facts(symbols, obs))

    def test_top50_only_provider_filter_basis_and_ten_candidates(self):
        res = self.result(self.ingest(rows(100)))
        self.assertEqual([c.symbol for c in res.candidates], [f"S{r:03d}" for r in range(1, 11)])
        self.assertEqual(res.coverage.basis, sc.RANK_BASIS)
        self.assertEqual(res.coverage.expected, 50)
        self.assertFalse(res.coverage.marketwide_complete)
        self.assertNotIn(51, {e.rank for e in res.exclusions if e.rank is not None})  # beyond depth: untouched

    def test_invalid_duplicate_rows_are_removed_and_defects_recorded(self):
        data = rows(20)
        data += [{"rank": 3, "symbol": "DUP3", "tradingAmount": "1", "tradingVolume": "1",
                  "price": {"lastPrice": "5000"}},
                 {"rank": "x", "symbol": "BADRANK"}, {"rank": 99, "symbol": ""}, "garbage"]
        state = self.ingest(data)
        self.assertEqual(len(state.snapshots[0].defects), 3)
        res = self.result(state)
        self.assertNotIn("S003", {c.symbol for c in res.candidates})        # rank conflict: both rows excluded
        self.assertNotIn("DUP3", {c.symbol for c in res.candidates})
        self.assertIn("CONFLICT_RANK", next(e.reasons for e in res.exclusions if e.symbol == "S003"))

    def test_investment_limits_apply_and_shortage_is_reported(self):
        res = self.result(self.ingest(rows(60, price='12000')))      # 10 sh * 12,000 > DAY-01 order limit
        self.assertEqual(res.candidates, ())
        self.assertTrue(any(r.startswith("SHORTAGE:0/10") for r in res.shortage_reasons), res.shortage_reasons)
        self.assertTrue(all("CAPITAL_ORDER_LIMIT_FAILED" in e.reasons for e in res.exclusions))
        mixed = rows(60)
        for r in mixed[:6]:
            r["price"]["lastPrice"] = "50000"
        res = self.result(self.ingest(mixed))
        self.assertEqual(len(res.candidates), 10)
        self.assertEqual(res.candidates[0].symbol, "S007")

    def test_held_monitor_survives_stale_ranking(self):
        state = self.ingest(rows(60))
        obs = T0 + timedelta(seconds=495)
        pf = sc.Portfolio(obs, cash="1000000", total_asset="1000000", **link.portfolio_limits_for(CFG),
                          positions=(sc.Position("HOLD", 10, "50000", obs, "5000", obs),))
        res = sc.select_watchlist(T0 + timedelta(seconds=500), state.snapshots, portfolio=pf, risk_config=CFG,
                                  config=CONFIG10)
        self.assertEqual(res.status, sc.STALE)
        self.assertEqual(res.candidates, ())
        self.assertEqual([h.symbol for h in res.held], ["HOLD"])
        self.assertTrue(res.held[0].monitor_only)

    def test_ranking_received_after_signal_time_cannot_authorize_the_entry(self):
        # Decision at 09:20:00 KST. The ranking was received at 09:20:30 KST (00:20:30 UTC).
        late_recv = datetime(2026, 11, 13, 0, 20, 30, tzinfo=timezone.utc)
        one = [{"rank": 1, "symbol": "AAA", "tradingAmount": "20000000000", "tradingVolume": "100000",
                "price": {"lastPrice": "5000", "changeRate": "1"}}]
        fetcher = RankingFetcher(lambda: payload(late_recv - timedelta(seconds=10), one), clock=Clock(late_recv))
        state, _ = ingest_fetch(ScoutState.empty(), fetcher, clock=Clock(late_recv))
        results = []
        for h, m, s in ((9, 19, 50), (9, 20, 0), (9, 21, 0)):
            as_of = datetime(2026, 11, 13, h, m, s, tzinfo=KST)
            obs = as_of - timedelta(seconds=5)
            results.append(sc.select_watchlist(
                as_of, state.snapshots, portfolio=sc.Portfolio(obs, cash="1000000", total_asset="1000000"),
                config=CONFIG10, risk_config=CFG, **facts(["AAA"], obs)))
        self.assertEqual([r.status for r in results[:2]], [sc.NO_SNAPSHOT, sc.NO_SNAPSHOT])
        self.assertEqual([c.symbol for c in results[2].candidates], ["AAA"])      # usable only after receipt
        timeline = link.WatchTimeline(results)
        out = replay(breakout("AAA", tail=3), CFG, universe=timeline.universe(), entry_allowed=timeline.entry_allowed)
        self.assertEqual(buys(out), [])                                  # 09:20 signal vetoed


class SourceBoundaryTests(unittest.TestCase):
    def setUp(self):
        # Importing the pure budget module loads the auto_trader.brokers package; other suites assert
        # that package is never imported, so undo only what this suite added.
        import sys
        before = set(sys.modules)

        def cleanup():
            for name in set(sys.modules) - before:
                if name.startswith("auto_trader.brokers"):
                    del sys.modules[name]
        self.addCleanup(cleanup)

    def test_toss_source_calls_only_the_ranking_method_inside_a_budget(self):
        calls = []

        class Client:
            def _domestic_trading_amount_rankings(self):
                from auto_trader.brokers.toss.request_budget import current_budget
                calls.append(current_budget.get() is not None)
                return payload()

            def __getattr__(self, name):                                  # any other access fails the test
                raise AssertionError(f"unexpected client access: {name}")

        self.assertEqual(toss_ranking_source(Client())()[1][0]["rank"], 1)
        self.assertEqual(calls, [True])

    def test_module_imports_no_settings_database_or_broker(self):
        tree = ast.parse(Path(src.__file__).read_text(encoding="utf-8"))
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                names.add(("." * node.level) + (node.module or ""))
        allowed = {"__future__", "hashlib", "json", "os", "re", "datetime", "pathlib", "typing", "threading",
                   "time", "collections", "urllib.parse", ".day01_scout", ".brokers.toss.request_budget"}
        self.assertEqual(names - allowed, set())


if __name__ == "__main__":
    unittest.main()
