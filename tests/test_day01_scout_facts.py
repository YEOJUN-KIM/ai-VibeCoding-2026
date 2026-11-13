"""Synthetic tests for the instrument-facts adapter. Row shapes follow the keys the existing client
code reads from /api/v1/stocks, /api/v1/stocks/all and the ranking rows. No network, DB, credentials,
clock or broker. Real response contents were NOT verified (no external call was made)."""
import ast
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from auto_trader import day01_scout as sc
from auto_trader import day01_scout_facts as facts_mod
from auto_trader import day01_scout_link as link
from auto_trader.day01_scout_facts import (
    InstrumentFactsSource, ReceiptLedger, build_instrument_facts, toss_directory_source, toss_stocks_source)
from auto_trader.day01_scout_source import ClockRegression, ResearchLog, guard_toss_client
from auto_trader.ml.day01 import Config

T0 = datetime(2026, 11, 13, 0, 10, 0, tzinfo=timezone.utc)      # 09:10 KST
RANKED = T0 - timedelta(seconds=9)
CFG = Config(execution='legacy_close')
CONFIG10 = link.scout_config_for(CFG, target_count=10)


def stock(symbol, *, kind="STOCK", common=True, status="ACTIVE", suspended=False, liquidation=False, **extra):
    row = {"symbol": symbol, "name": f"N{symbol}", "securityType": kind, "isCommonShare": common, "status": status,
           "koreanMarketDetail": {"krxTradingSuspended": suspended, "liquidationTrading": liquidation}}
    row.update(extra)
    return row


def listed(symbol, *, kind="STOCK", common=True, market="KOSPI"):
    return {"symbol": symbol, "name": f"N{symbol}", "securityType": kind, "isCommonShare": common, "market": market}


def ranked(symbol, rank, price="5000", currency="KRW"):
    row = {"rank": rank, "symbol": symbol, "tradingAmount": "20000000000", "tradingVolume": "100000",
           "price": {"lastPrice": price, "basePrice": price, "changeRate": "1.5"}}
    if currency is not None:
        row["currency"] = currency
    return row


class Clock:
    def __init__(self, *times):
        self.times, self.calls = list(times), 0

    def __call__(self):
        self.calls += 1
        return self.times[min(self.calls - 1, len(self.times) - 1)]


def build(rows, *, symbols=None, dir_rows=None, dir_at=T0 - timedelta(hours=1), ranking=None,
          batch_at=T0 + timedelta(seconds=2), ranking_at=T0, **kw):
    symbols = symbols or [r["symbol"] for r in rows if isinstance(r, dict) and r.get("symbol")]
    dir_rows = [listed(s) for s in symbols] if dir_rows is None else dir_rows
    ranking = [ranked(s, i) for i, s in enumerate(symbols, 1)] if ranking is None else ranking
    return build_instrument_facts(stocks_batches=[(tuple(symbols), rows, batch_at)], directory=(dir_rows, dir_at),
                                  ranking_rows=ranking, ranking_received_at=ranking_at, **kw)


def scout(rows_ranking, facts, as_of=T0 + timedelta(seconds=10), *, positions=(), cfg=CONFIG10,
          ranking_received=T0):
    snap = sc.snapshot_from_rankings(ranking_received - timedelta(seconds=9), rows_ranking,
                                     received_at=ranking_received)
    symbols = sorted({r["symbol"] for r in rows_ranking})
    obs = as_of - timedelta(seconds=2)
    # LIQUIDITY / DAY01 READINESS / PORTFOLIO are SYNTHETIC; instrument facts are the adapter output.
    return sc.select_watchlist(
        as_of, [snap], instruments=facts, config=cfg, risk_config=CFG,
        liquidity=[sc.LiquidityFacts(s, obs, turnover="20000000000", volume="100000", spread_pct="0.5") for s in symbols],
        dayone=[sc.DayOneFacts(s, obs, completed_1m_bars=20, opening_range_ready=True, vwap_ready=True) for s in symbols],
        portfolio=sc.Portfolio(obs, cash="1000000", total_asset="1000000", positions=positions,
                               **link.portfolio_limits_for(CFG)))


def reasons(result, symbol):
    return next(e.reasons for e in result.exclusions if e.symbol == symbol)


class ClassificationTests(unittest.TestCase):
    def test_common_stock_passes_and_etf_etn_preferred_other_types_are_excluded(self):
        rows = [stock("AAA"), stock("ETF1", kind="ETF", common=False), stock("ETN1", kind="ETN", common=False),
                stock("PREF", common=False), stock("ETFC", kind="ETF", common=True)]
        symbols = [r["symbol"] for r in rows]
        dir_rows = [listed("AAA"), listed("ETF1", kind="ETF", common=False), listed("ETN1", kind="ETN", common=False),
                    listed("PREF", common=False), listed("ETFC", kind="ETF", common=True)]
        facts, notes = build(rows, dir_rows=dir_rows)
        self.assertEqual(notes, ())
        result = scout([ranked(s, i) for i, s in enumerate(symbols, 1)], facts)
        self.assertEqual([c.symbol for c in result.candidates], ["AAA"])
        self.assertIn("NOT_STOCK", reasons(result, "ETF1"))
        self.assertIn("NOT_COMMON_SHARE", reasons(result, "ETF1"))
        self.assertIn("NOT_STOCK", reasons(result, "ETN1"))
        self.assertIn("NOT_COMMON_SHARE", reasons(result, "PREF"))
        self.assertIn("NOT_STOCK", reasons(result, "ETFC"))          # ETF mislabeled common is still not STOCK
        self.assertTrue(result.candidates[0].fully_eligible)

    def test_unknown_or_malformed_fields_stay_unknown_and_block(self):
        base = stock("AAA")
        cases = {
            "no_type": {k: v for k, v in base.items() if k != "securityType"},
            "no_common": {k: v for k, v in base.items() if k != "isCommonShare"},
            "string_common": dict(base, isCommonShare="true"),
            "int_common": dict(base, isCommonShare=1),
            "no_status": {k: v for k, v in base.items() if k != "status"},
            "bad_status": dict(base, status="HALTED"),
            "no_detail": {k: v for k, v in base.items() if k != "koreanMarketDetail"},
            "suspended": stock("AAA", suspended=True),
            "no_suspended_key": dict(base, koreanMarketDetail={"liquidationTrading": False}),
            "liquidation": stock("AAA", liquidation=True),
            "no_liquidation_key": dict(base, koreanMarketDetail={"krxTradingSuspended": False}),
            "suspended_string": dict(base, koreanMarketDetail={"krxTradingSuspended": "false", "liquidationTrading": False}),
        }
        expected = {"no_type": "NOT_STOCK", "no_common": "NOT_COMMON_SHARE", "string_common": "NOT_COMMON_SHARE",
                    "int_common": "NOT_COMMON_SHARE", "no_status": "NOT_ACTIVE", "bad_status": "NOT_ACTIVE",
                    "no_detail": "SUSPENDED_OR_UNKNOWN", "suspended": "SUSPENDED_OR_UNKNOWN",
                    "no_suspended_key": "SUSPENDED_OR_UNKNOWN", "liquidation": "LIQUIDATION_OR_UNKNOWN",
                    "no_liquidation_key": "LIQUIDATION_OR_UNKNOWN", "suspended_string": "SUSPENDED_OR_UNKNOWN"}
        bare_directory = [{"symbol": "AAA", "market": "KOSPI"}]       # directory knows nothing else
        for name, row in cases.items():
            facts, _ = build([row], dir_rows=bare_directory)
            result = scout([ranked("AAA", 1)], facts)
            self.assertEqual(result.candidates, (), name)
            self.assertIn(expected[name], reasons(result, "AAA"), name)

    def test_directory_may_fill_a_missing_static_attribute_but_never_time_sensitive_ones(self):
        no_type = {k: v for k, v in stock("AAA").items() if k not in ("securityType", "isCommonShare")}
        facts, notes = build([no_type])                                   # directory says STOCK / common
        self.assertEqual((facts[0].security_type, facts[0].is_common_share), ("STOCK", True))
        no_status = {k: v for k, v in stock("AAA").items() if k != "status"}
        facts, _ = build([no_status], dir_rows=[dict(listed("AAA"), status="ACTIVE")])
        self.assertIsNone(facts[0].status)                                # directory status is never trusted
        facts, _ = build([{k: v for k, v in stock("AAA").items() if k != "koreanMarketDetail"}],
                         dir_rows=[dict(listed("AAA"), koreanMarketDetail={"krxTradingSuspended": False,
                                                                           "liquidationTrading": False})])
        self.assertIsNone(facts[0].krx_trading_suspended)

    def test_market_and_currency_must_be_known_and_valid(self):
        no_dir, notes = build([stock("AAA")], dir_rows=[])
        self.assertIn(("AAA", "NOT_IN_DIRECTORY"), notes)
        self.assertIn("NOT_KOSPI_KOSDAQ", reasons(scout([ranked("AAA", 1)], no_dir), "AAA"))
        konex, _ = build([stock("AAA")], dir_rows=[listed("AAA", market="KONEX")])
        self.assertIn("NOT_KOSPI_KOSDAQ", reasons(scout([ranked("AAA", 1)], konex), "AAA"))
        for currency in (None, "USD"):
            facts, _ = build([stock("AAA")], ranking=[ranked("AAA", 1, currency=currency)])
            self.assertIn("NOT_KRW", reasons(scout([ranked("AAA", 1, currency=currency)], facts), "AAA"))

    def test_investment_limits_still_apply_to_a_fully_classified_stock(self):
        facts, _ = build([stock("AAA")], ranking=[ranked("AAA", 1, price="12000")])
        result = scout([ranked("AAA", 1, price="12000")], facts)
        self.assertEqual(result.candidates, ())
        self.assertEqual(reasons(result, "AAA"), ("CAPITAL_ORDER_LIMIT_FAILED",))


class MalformedAndAliasTests(unittest.TestCase):
    def test_malformed_present_fields_are_never_filled_in_from_the_directory(self):
        good_dir = [listed("AAA")]                                       # directory says STOCK / common / KOSPI
        base = stock("AAA")
        cases = {
            "type_int": (dict(base, securityType=42), "NOT_STOCK", "MALFORMED:security_type"),
            "common_str": (dict(base, isCommonShare="false"), "NOT_COMMON_SHARE", "MALFORMED:is_common_share"),
            "common_null": (dict(base, isCommonShare=None), "NOT_COMMON_SHARE", "MALFORMED:is_common_share"),
            "type_empty": (dict(base, securityType="  "), "NOT_STOCK", "MALFORMED:security_type"),
            "status_int": (dict(base, status=5), "NOT_ACTIVE", "MALFORMED:status"),
            "detail_list": (dict(base, koreanMarketDetail=[]), "SUSPENDED_OR_UNKNOWN", "MALFORMED:krx_trading_suspended"),
            "suspended_null": (dict(base, koreanMarketDetail={"krxTradingSuspended": None, "liquidationTrading": False}),
                               "SUSPENDED_OR_UNKNOWN", "MALFORMED:krx_trading_suspended"),
        }
        for name, (row, reason, note) in cases.items():
            facts, notes = build([row], dir_rows=good_dir)
            self.assertIn(("AAA", note), notes, name)
            result = scout([ranked("AAA", 1)], facts)
            self.assertEqual(result.candidates, (), name)
            self.assertIn(reason, reasons(result, "AAA"), name)
        # malformed on the directory side blocks too (it must not silently defer to the stocks row)
        facts, notes = build([base], dir_rows=[dict(listed("AAA"), securityType=7)])
        self.assertIn(("AAA", "MALFORMED:security_type"), notes)
        self.assertIsNone(facts[0].security_type)

    def test_case_and_whitespace_aliases_cannot_hide_an_adverse_row(self):
        facts, notes = build([stock("AAA"), stock("aaa", suspended=True)], symbols=["AAA"], dir_rows=[listed("AAA")])
        self.assertIn(("AAA", "DUPLICATE_CONFLICT"), notes)
        self.assertEqual(scout([ranked("AAA", 1)], facts).candidates, ())
        facts, notes = build([stock("AAA")], symbols=["AAA"], dir_rows=[listed("AAA", market="KOSPI"),
                                                                    listed(" aaa ", market="KOSDAQ")])
        self.assertIn(("AAA", "DUPLICATE_CONFLICT_DIRECTORY"), notes)
        facts, notes = build([stock("AAA")], symbols=["AAA"], ranking=[ranked("AAA", 1), ranked("aaa", 2, currency="USD")])
        self.assertIn(("AAA", "DUPLICATE_CONFLICT"), notes)
        # identical aliases are fine, and a request spelled differently still matches its row
        facts, notes = build([stock("AAA"), dict(stock("AAA"), symbol=" aaa ")], symbols=["AAA"], dir_rows=[listed("AAA")])
        self.assertEqual((notes, len(facts)), ((), 1))
        facts, _ = build([dict(stock("AAA"), symbol="aaa")], symbols=[" AAA "], dir_rows=[listed("AAA")], ranking=[ranked("AAA", 1)])
        self.assertEqual([f.symbol for f in facts], ["AAA"])


class ConflictAndMissingTests(unittest.TestCase):
    def test_missing_row_gets_no_fact_and_unrequested_rows_are_ignored(self):
        facts, notes = build([stock("AAA"), stock("EXTRA")], symbols=["AAA", "BBB"])
        self.assertEqual([f.symbol for f in facts], ["AAA"])
        self.assertIn(("BBB", "NO_STOCKS_ROW"), notes)
        result = scout([ranked("BBB", 1)], facts)
        self.assertEqual(result.candidates, ())
        self.assertIn("INSTRUMENT_MISSING", reasons(result, "BBB"))

    def test_duplicate_rows_identical_ok_conflicting_makes_everything_unknown(self):
        same, notes = build([stock("AAA"), stock("AAA")], symbols=["AAA"])
        self.assertEqual(notes, ())
        self.assertEqual(same[0].security_type, "STOCK")
        bad, notes = build([stock("AAA"), stock("AAA", kind="ETF", common=False)], symbols=["AAA"])
        self.assertIn(("AAA", "DUPLICATE_CONFLICT"), notes)
        self.assertEqual((bad[0].security_type, bad[0].is_common_share, bad[0].status, bad[0].market), (None,) * 4)
        self.assertEqual(scout([ranked("AAA", 1)], bad).candidates, ())

    def test_stocks_and_directory_disagreement_blocks_that_field(self):
        facts, notes = build([stock("AAA")], dir_rows=[listed("AAA", kind="ETF", common=False)])
        self.assertIn(("AAA", "CONFLICT:security_type"), notes)
        self.assertIn(("AAA", "CONFLICT:is_common_share"), notes)
        result = scout([ranked("AAA", 1)], facts)
        self.assertIn("NOT_STOCK", reasons(result, "AAA"))
        facts, notes = build([stock("AAA")], dir_rows=[listed("AAA", market="KOSPI"), listed("AAA", market="KOSDAQ")])
        self.assertIn(("AAA", "DUPLICATE_CONFLICT_DIRECTORY"), notes)
        self.assertIsNone(facts[0].market)

    def test_garbage_rows_are_ignored_without_crashing(self):
        facts, _ = build(["x", None, {"symbol": ""}, {"symbol": 5}, stock("AAA")], symbols=["AAA"])
        self.assertEqual([f.symbol for f in facts], ["AAA"])


class TimeSemanticsTests(unittest.TestCase):
    def test_observed_at_is_our_receipt_time_the_later_of_the_two_sources(self):
        facts, _ = build([stock("AAA")], batch_at=T0 + timedelta(seconds=2), ranking_at=T0)
        self.assertEqual(facts[0].observed_at, T0 + timedelta(seconds=2))
        facts, _ = build([stock("AAA")], batch_at=T0, ranking_at=T0 + timedelta(seconds=5))
        self.assertEqual(facts[0].observed_at, T0 + timedelta(seconds=5))

    def test_source_skew_drops_the_fact(self):
        facts, notes = build([stock("AAA")], batch_at=T0 + timedelta(seconds=61))
        self.assertEqual(facts, ())
        self.assertIn(("AAA", "SOURCE_SKEW"), notes)
        facts, _ = build([stock("AAA")], batch_at=T0 + timedelta(seconds=60))
        self.assertEqual(len(facts), 1)

    def test_directory_must_be_from_the_same_kst_day_and_not_from_the_future(self):
        yesterday = T0 - timedelta(days=1)
        facts, notes = build([stock("AAA")], dir_at=yesterday)
        self.assertIn(("AAA", "DIRECTORY_NOT_CURRENT"), notes)
        self.assertIsNone(facts[0].market)
        facts, notes = build([stock("AAA")], dir_at=T0 + timedelta(minutes=5))      # received after the fact
        self.assertIn(("AAA", "DIRECTORY_NOT_CURRENT"), notes)
        self.assertIsNone(facts[0].market)
        # 00:10Z is 09:10 KST on 11-13; a directory fetched 23:50Z on 11-12 is already 11-13 08:50 KST.
        early = datetime(2026, 11, 12, 23, 50, tzinfo=timezone.utc)
        facts, notes = build([stock("AAA")], dir_at=early)
        self.assertEqual(notes, ())
        self.assertEqual(facts[0].market, "KOSPI")

    def test_stale_facts_expire_and_future_facts_never_apply_to_the_past(self):
        facts, _ = build([stock("AAA")])
        fresh = scout([ranked("AAA", 1)], facts, as_of=T0 + timedelta(seconds=10))
        self.assertEqual([c.symbol for c in fresh.candidates], ["AAA"])
        later = T0 + timedelta(seconds=2 + 300)                         # facts 300 s old, fresh ranking
        stale = scout([ranked("AAA", 1)], facts, as_of=later, ranking_received=later - timedelta(seconds=5))
        self.assertEqual(stale.candidates, ())
        self.assertIn("INSTRUMENT_STALE", reasons(stale, "AAA"))
        # Replaying an earlier as_of with facts received later: the fact does not exist yet.
        before = scout([ranked("AAA", 1)], facts, as_of=T0 + timedelta(seconds=1))
        self.assertIn("INSTRUMENT_MISSING", reasons(before, "AAA"))

    def test_a_newer_adverse_fact_supersedes_the_earlier_good_one(self):
        good, _ = build([stock("AAA")])
        halted, _ = build([stock("AAA", suspended=True)], batch_at=T0 + timedelta(seconds=8), ranking_at=T0 + timedelta(seconds=8))
        result = scout([ranked("AAA", 1)], good + halted, as_of=T0 + timedelta(seconds=10))
        self.assertEqual(result.candidates, ())
        self.assertIn("SUSPENDED_OR_UNKNOWN", reasons(result, "AAA"))

    def test_held_monitor_survives_a_suspension_fact(self):
        halted, _ = build([stock("HOLD", suspended=True)])
        obs = T0 + timedelta(seconds=8)
        held = (sc.Position("HOLD", 10, "50000", obs, "5000", obs),)
        result = scout([ranked("HOLD", 1), ranked("BBB", 2)], halted + build([stock("BBB")])[0], positions=held)
        self.assertEqual([h.symbol for h in result.held], ["HOLD"])
        self.assertEqual([c.symbol for c in result.candidates], ["BBB"])


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.log_path = Path(self.dir.name) / "facts.jsonl"

    def make(self, stocks, directory=None, clock=None, log=True, **kw):
        log = ResearchLog(self.log_path, clock=Clock(T0)) if log else None
        return InstrumentFactsSource(stocks, clock=clock or Clock(T0 + timedelta(seconds=2)), directory_source=directory,
                                     log=log, **kw)

    def test_collects_in_batches_and_logs_raw_rows_once(self):
        symbols = [f"S{i:03d}" for i in range(120)]
        calls = []

        def stocks(chunk):
            calls.append(len(chunk))
            return [stock(s) for s in chunk]

        directory_rows = [listed(s) for s in symbols]
        source = self.make(stocks, lambda: (directory_rows, T0 - timedelta(hours=1)),
                           clock=Clock(T0 + timedelta(seconds=1)))
        facts, notes = source.collect(symbols, ranking_rows=[ranked(s, i) for i, s in enumerate(symbols, 1)],
                                      ranking_received_at=T0)
        self.assertEqual(calls, [50, 50, 20])
        self.assertEqual((len(facts), notes), (120, ()))
        kinds = [json.loads(x)["kind"] for x in self.log_path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(kinds.count("stocks_fetch_ok"), 3)
        self.assertEqual(kinds[-1], "instrument_facts")
        with self.assertRaises(ValueError):
            self.make(stocks, batch_size=51)

    def test_directory_is_collected_first_and_uses_its_supplied_original_receipt(self):
        order = []

        def directory():
            order.append("directory")
            return [listed("AAA")], T0 + timedelta(seconds=1)          # received before the stocks call

        def stocks(chunk):
            order.append("stocks")
            return [stock("AAA")]

        source = self.make(stocks, directory, clock=Clock(T0 + timedelta(seconds=2)), log=False)
        facts, notes = source.collect(["AAA"], ranking_rows=[ranked("AAA", 1)], ranking_received_at=T0)
        self.assertEqual(order, ["directory", "stocks"])
        self.assertEqual(notes, ())
        self.assertEqual(facts[0].market, "KOSPI")                       # first collect no longer loses the market
        self.assertEqual(facts[0].observed_at, T0 + timedelta(seconds=2))

    def test_directory_with_unknown_stale_or_bare_receipt_is_not_used(self):
        for name, returned in (("unknown", ([listed("AAA")], None)),
                               ("yesterday", ([listed("AAA")], T0 - timedelta(days=1))),
                               ("future", ([listed("AAA")], T0 + timedelta(minutes=5)))):
            source = self.make(lambda chunk: [stock("AAA")], lambda r=returned: r, log=False)
            facts, notes = source.collect(["AAA"], ranking_rows=[ranked("AAA", 1)], ranking_received_at=T0)
            self.assertIsNone(facts[0].market, name)
            self.assertTrue(any(code in ("DIRECTORY_RECEIPT_UNKNOWN", "DIRECTORY_NOT_CURRENT") for _, code in notes), name)
        bare = self.make(lambda chunk: [stock("AAA")], lambda: [listed("AAA")], log=False)       # bare list: rejected
        facts, notes = bare.collect(["AAA"], ranking_rows=[ranked("AAA", 1)], ranking_received_at=T0)
        self.assertIn((None, "DIRECTORY_FETCH_FAILED"), notes)
        self.assertIsNone(facts[0].market)

    def test_log_failure_before_remembering_makes_the_batch_fail_closed_then_logs_raw_rows_on_retry(self):
        log = ResearchLog(self.log_path, clock=Clock(T0))
        real, calls = log.append, []

        def flaky(kind, **fields):
            calls.append(kind)
            if kind == "stocks_fetch_ok" and calls.count("stocks_fetch_ok") == 1:
                raise OSError("disk full")
            return real(kind, **fields)

        log.append = flaky
        cached = [stock("AAA")]
        source = InstrumentFactsSource(lambda chunk: cached, clock=Clock(T0 + timedelta(seconds=2),
                                       T0 + timedelta(seconds=3)), log=log)
        facts, notes = source.collect(["AAA"], ranking_rows=[ranked("AAA", 1)], ranking_received_at=T0)
        self.assertEqual(facts, ())
        self.assertIn(("AAA", "STOCKS_FETCH_FAILED"), notes)
        facts, _ = source.collect(["AAA"], ranking_rows=[ranked("AAA", 1)], ranking_received_at=T0)
        self.assertEqual(len(facts), 1)
        recs = [json.loads(x) for x in self.log_path.read_text(encoding="utf-8").splitlines()]
        ok = [r for r in recs if r["kind"] == "stocks_fetch_ok"]
        self.assertEqual(len(ok), 1)
        self.assertFalse(ok[0]["cache_hit"])
        self.assertEqual(len(ok[0]["rows"]), 1)                          # raw rows were recorded

    def test_failures_degrade_to_fewer_facts_and_log_only_error_metadata(self):
        class Boom(Exception):
            status_code, error_code = 429, "rate-limit-exceeded"

            def __str__(self):
                return "Bearer abc.def secret detail"

        def failing(chunk):
            raise Boom()

        source = self.make(failing, lambda: (_ for _ in ()).throw(Boom()))
        facts, notes = source.collect(["AAA"], ranking_rows=[ranked("AAA", 1)], ranking_received_at=T0)
        self.assertEqual(facts, ())
        self.assertIn(("AAA", "STOCKS_FETCH_FAILED"), notes)
        self.assertIn((None, "DIRECTORY_FETCH_FAILED"), notes)
        text = self.log_path.read_text(encoding="utf-8")
        self.assertNotIn("Bearer", text)
        self.assertNotIn("secret detail", text)
        self.assertIn("rate-limit-exceeded", text)
        # Directory alone failing keeps time-sensitive facts but market becomes unknown -> excluded.
        ok = self.make(lambda chunk: [stock("AAA")], lambda: (_ for _ in ()).throw(Boom()), log=False)
        facts, _ = ok.collect(["AAA"], ranking_rows=[ranked("AAA", 1)], ranking_received_at=T0)
        self.assertIsNone(facts[0].market)
        self.assertIn("NOT_KOSPI_KOSDAQ", reasons(scout([ranked("AAA", 1)], facts), "AAA"))

    def test_bad_response_shape_and_clock_regression_fail_closed(self):
        source = self.make(lambda chunk: {"result": []}, log=False)
        facts, notes = source.collect(["AAA"], ranking_rows=[], ranking_received_at=T0)
        self.assertEqual((facts, notes), ((), (("AAA", "STOCKS_FETCH_FAILED"),)))
        ledger = ReceiptLedger(clock=Clock(T0, T0 - timedelta(seconds=15)))
        ledger.stamp([1])
        with self.assertRaises(ClockRegression):
            ledger.stamp([2])
        with self.assertRaises(ClockRegression):
            ReceiptLedger(clock=lambda: datetime(2026, 11, 13)).stamp([3])


class WrapperTests(unittest.TestCase):
    def setUp(self):
        import sys
        before = set(sys.modules)

        def cleanup():
            for name in set(sys.modules) - before:       # only what this test imported
                if name.startswith("auto_trader.brokers"):
                    del sys.modules[name]
        self.addCleanup(cleanup)

    def test_stocks_source_uses_only_the_stocks_endpoint_inside_a_budget(self):
        seen = []

        class Client:
            base_url = "https://example.invalid"

            def _authorized_json_request(self, url):
                from auto_trader.brokers.toss.request_budget import current_budget
                seen.append((url, current_budget.get() is not None))
                return {"result": [stock("A1"), stock("B2")]}

            def __getattr__(self, name):
                raise AssertionError(f"unexpected client access: {name}")

        rows = toss_stocks_source(Client())(("A1", "B2"))
        self.assertEqual(len(rows), 2)
        self.assertEqual(seen, [("https://example.invalid/api/v1/stocks?symbols=A1%2CB2", True)])

    def test_directory_source_calls_only_the_directory_method(self):
        class Client:
            def _domestic_stock_universe(self):
                return [listed("A1")]

            def __getattr__(self, name):
                if name == "_stock_universe_expires_at":
                    raise AttributeError(name)
                raise AssertionError(f"unexpected client access: {name}")

        self.assertEqual(toss_directory_source(Client())()[0][0]["symbol"], "A1")

    def test_directory_receipt_is_recovered_from_the_client_expiry_and_unknown_when_absent(self):
        now = datetime(2026, 11, 13, 3, 0, 0, tzinfo=timezone.utc)

        class Client:
            _stock_universe_expires_at = 1000.0 + 43200 - 7200        # fetched 7200 s before "now" (mono 1000)

            def _domestic_stock_universe(self):
                return [listed("A1")]

        rows, received = toss_directory_source(Client(), clock=lambda: now, monotonic=lambda: 1000.0)()
        self.assertEqual(rows[0]["symbol"], "A1")
        self.assertEqual(received, now - timedelta(seconds=7200))        # yesterday-cached data is dated correctly

        class NoAttr:
            def _domestic_stock_universe(self):
                return [listed("A1")]

        self.assertIsNone(toss_directory_source(NoAttr(), clock=lambda: now)()[1])

        class Implausible(Client):
            _stock_universe_expires_at = 1000.0 + 43200 + 500          # would mean a negative age

        self.assertIsNone(toss_directory_source(Implausible(), clock=lambda: now, monotonic=lambda: 1000.0)()[1])

    def test_guard_default_blocks_stocks_and_explicit_allow_adds_only_get_stocks(self):
        class Request:
            def __init__(self, url, method="GET"):
                self.full_url, self._m = url, method

            def get_method(self):
                return self._m

        class Client:
            def _json_request_once(self, request):
                return {"ok": True}

        default = Client()
        guard_toss_client(default)
        with self.assertRaises(PermissionError):
            default._json_request_once(Request("https://x/api/v1/stocks?symbols=A"))
        wide = Client()
        counts = guard_toss_client(wide, also_allow=[("/api/v1/stocks", "GET"), ("/api/v1/stocks/all", "GET")])
        wide._json_request_once(Request("https://x/api/v1/stocks?symbols=A"))
        wide._json_request_once(Request("https://x/api/v1/stocks/all?market=KOSPI"))
        for bad in (Request("https://x/api/v1/orders", "POST"), Request("https://x/api/v1/accounts"),
                    Request("https://x/api/v1/stocks", "POST"), Request("https://x/api/v1/prices?symbols=A")):
            with self.assertRaises(PermissionError):
                wide._json_request_once(bad)
        self.assertEqual(counts["paths"], {"/api/v1/stocks": 1, "/api/v1/stocks/all": 1})
        with self.assertRaises(ValueError):
            guard_toss_client(Client(), also_allow=[("/api/v1/orders", "POST")])

    def test_module_imports_no_settings_database_or_broker_at_import_time(self):
        tree = ast.parse(Path(facts_mod.__file__).read_text(encoding="utf-8"))
        top_level = set()
        for node in tree.body:
            if isinstance(node, ast.Import):
                top_level.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                top_level.add(("." * node.level) + (node.module or ""))
        allowed = {"__future__", "hashlib", "json", "threading", "time", "collections", "datetime", "typing",
                   ".day01_scout", ".day01_scout_source"}
        self.assertEqual(top_level - allowed, set())


if __name__ == "__main__":
    unittest.main()
