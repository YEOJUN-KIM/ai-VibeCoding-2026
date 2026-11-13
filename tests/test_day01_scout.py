"""Synthetic tests for auto_trader.day01_scout. No network, DB, files, clock or broker."""

import ast
import dataclasses
import json
import random
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from auto_trader import day01_scout as sc
from auto_trader.day01_scout import (
    Check, DayOneFacts, FetchResult, InstrumentFacts, LiquidityFacts, Portfolio, Position,
    ScoutConfig, ScoutState, ingest_fetch, refresh, select_watchlist, snapshot_from_payload,
    snapshot_from_rankings, to_dict,
)

UTC = timezone.utc
RANKED = datetime(2026, 11, 13, 1, 0, 0, tzinfo=UTC)      # 10:00:00 KST
RECEIVED = RANKED + timedelta(seconds=10)
AS_OF = RECEIVED + timedelta(seconds=20)
OBSERVED = AS_OF - timedelta(seconds=10)
RISK = SimpleNamespace(max_order_amount=Decimal(1_000_000), max_symbol_amount=Decimal(2_000_000),
                       max_total_investment=Decimal(5_000_000))


def sym(rank):
    return f"S{rank:03d}"


def raw_rows(count=60, price=10000):
    return [{"rank": r, "symbol": sym(r), "tradingAmount": "20000000000", "tradingVolume": "100000",
             "price": {"lastPrice": str(price), "changeRate": "1.5"}} for r in range(1, count + 1)]


def snap(rows=None, *, ranked=RANKED, received=RECEIVED, sid=None, **kw):
    return snapshot_from_rankings(ranked, raw_rows() if rows is None else rows, received_at=received,
                                  snapshot_id=sid, **kw)


def instruments(count=60, observed=OBSERVED, **override):
    base = dict(market="KOSPI", currency="KRW", security_type="STOCK", is_common_share=True,
                status="ACTIVE", krx_trading_suspended=False, liquidation_trading=False)
    base.update(override)
    return [InstrumentFacts(sym(r), observed, name=f"N{r}", **base) for r in range(1, count + 1)]


def liquidity(count=60, observed=OBSERVED, **override):
    base = dict(turnover="20000000000", volume="100000", spread_pct="0.5")
    base.update(override)
    return [LiquidityFacts(sym(r), observed, **base) for r in range(1, count + 1)]


def dayone(count=60, observed=OBSERVED, **override):
    base = dict(completed_1m_bars=20, opening_range_ready=True, vwap_ready=True)
    base.update(override)
    return [DayOneFacts(sym(r), observed, **base) for r in range(1, count + 1)]


def portfolio(**kw):
    base = dict(observed_at=OBSERVED, cash="10000000")
    base.update(kw)
    return Portfolio(**base)


def run(snaps=None, *, as_of=AS_OF, inst=None, liq=None, day=None, pf=None, config=None, risk=RISK):
    return select_watchlist(
        as_of, [snap()] if snaps is None else snaps,
        instruments=instruments() if inst is None else inst,
        liquidity=liquidity() if liq is None else liq,
        dayone=dayone() if day is None else day,
        portfolio=portfolio() if pf is None else pf,
        config=config or ScoutConfig(), risk_config=risk)


def reasons(result, symbol):
    return next(e.reasons for e in result.exclusions if e.symbol == symbol)


class NormalSelectionTests(unittest.TestCase):
    def test_top50_selects_target_in_rank_order_with_provenance(self):
        result = run()
        self.assertEqual(result.status, sc.OK)
        self.assertEqual([c.symbol for c in result.candidates], [sym(r) for r in range(1, 13)])
        first = result.candidates[0]
        self.assertEqual(first.purpose, "WATCH_ONLY")
        self.assertFalse(first.joint_orders_guaranteed)
        self.assertTrue(first.fully_eligible)
        self.assertEqual(first.provenance.received_at, RECEIVED)
        self.assertEqual(first.provenance.provider_ranked_at, RANKED)
        self.assertNotEqual(first.provenance.received_at, first.provenance.provider_ranked_at)
        self.assertEqual(first.as_of, AS_OF)
        self.assertEqual(result.coverage.basis, sc.RANK_BASIS)
        self.assertIn("excludeInvestmentCaution", result.snapshot.source)
        self.assertEqual({e.kind for e in first.evidence},
                         {"RANKING", "INSTRUMENT", "LIQUIDITY", "DAY01_READINESS", "PORTFOLIO"})

    def test_target_count_is_configurable_and_capped(self):
        self.assertEqual(len(run(config=ScoutConfig(target_count=15)).candidates), 15)
        self.assertEqual(len(run(config=ScoutConfig(target_count=10)).candidates), 10)
        cut = run(config=ScoutConfig(target_count=10))
        self.assertIn("BELOW_TARGET_COUNT", reasons(cut, sym(11)))

    def test_serializable_and_deterministic_under_input_order(self):
        base = run()
        encoded = json.dumps(to_dict(base))
        self.assertIn("day01-scout-1", encoded)
        rng = random.Random(7)
        inst, liq, day = instruments(), liquidity(), dayone()
        for part in (inst, liq, day):
            rng.shuffle(part)
        rows = raw_rows()
        rng.shuffle(rows)
        shuffled = run([snap(rows)], inst=inst, liq=liq, day=day)
        self.assertEqual(to_dict(base), to_dict(shuffled))

    def test_score_vs_rank_sort_modes_are_transparent(self):
        day = dayone()
        day[0] = DayOneFacts(sym(1), OBSERVED, completed_1m_bars=3, opening_range_ready=False, vwap_ready=False)
        relaxed = dict(require_dayone_readiness=False)
        by_score = run(day=day, config=ScoutConfig(**relaxed))
        by_rank = run(day=day, config=ScoutConfig(sort_mode="RANK", **relaxed))
        self.assertEqual(by_rank.candidates[0].symbol, sym(1))
        self.assertNotEqual(by_score.candidates[0].symbol, sym(1))
        self.assertEqual({n for n, _ in by_score.candidates[0].score_components},
                         {"RANK", "COMPLETED_1M_BARS", "OPENING_RANGE", "VWAP"})


class SnapshotContractTests(unittest.TestCase):
    def test_payload_and_tuple_forms_match_existing_contract(self):
        rows = raw_rows(5)
        a = snapshot_from_payload({"result": {"rankedAt": "2026-11-13T01:00:00Z", "rankings": rows}},
                                  received_at=RECEIVED)
        b = snapshot_from_payload(("2026-11-13T10:00:00+09:00", rows), received_at=RECEIVED)
        self.assertEqual(a.content_digest, b.content_digest)
        self.assertEqual(a.provider_ranked_at, RANKED)
        self.assertEqual(a.received_at, RECEIVED)
        self.assertEqual(a.rows[0].price, Decimal(10000))

    def test_digest_is_independent_of_id_receipt_and_source_container(self):
        rows = raw_rows(5)
        one = snap(rows, sid="a")
        rows[0]["price"]["lastPrice"] = "1"  # mutating the source never changes the stored snapshot
        two = snap(raw_rows(5), sid="b", received=RECEIVED + timedelta(seconds=5))
        self.assertEqual(one.content_digest, two.content_digest)
        self.assertEqual(one.rows[0].price, Decimal(10000))
        with self.assertRaises(dataclasses.FrozenInstanceError):
            one.snapshot_id = "x"
        self.assertIsInstance(one.rows, tuple)

    def test_unknown_or_malformed_provider_time_is_never_fabricated(self):
        for ranked in (None, "garbage", "2026-11-13T01:00:00"):
            s = snap(ranked=ranked)
            self.assertIsNone(s.provider_ranked_at)
            result = run([s])
            self.assertEqual(result.status, sc.UNKNOWN_PROVIDER_TIME)
            self.assertEqual(result.candidates, ())
        self.assertIn("BAD_RANKED_AT", snap(ranked="garbage").defects)

    def test_invalid_rows_are_dropped_with_defects_and_floats_rejected(self):
        rows = raw_rows(3)
        rows[0]["rank"] = True
        rows[1]["price"]["lastPrice"] = 10000.5          # float
        rows[2]["tradingAmount"] = "NaN"
        rows.append("junk")
        s = snap(rows)
        self.assertEqual(len(s.rows), 2)
        self.assertIsNone(s.rows[0].price)
        self.assertIsNone(s.rows[1].turnover)
        self.assertTrue({"row[0]:BAD_RANK", "row[1]:BAD_PRICE", "row[2]:BAD_TURNOVER",
                         "row[3]:NOT_A_MAPPING"} <= set(s.defects))

    def test_naive_receipt_and_bad_inputs_raise(self):
        with self.assertRaises(ValueError):
            snapshot_from_rankings(RANKED, [], received_at=datetime(2026, 11, 13, 1, 0, 10))
        with self.assertRaises(ValueError):
            snapshot_from_payload({"nope": 1}, received_at=RECEIVED)
        with self.assertRaises(ValueError):
            select_watchlist(datetime(2026, 11, 13), [])


class TemporalTests(unittest.TestCase):
    def test_future_snapshot_is_ignored_entirely(self):
        base = run()
        future = snap(raw_rows(5), received=AS_OF + timedelta(seconds=1), ranked=AS_OF,
                      sid="future")
        self.assertEqual(to_dict(base), to_dict(run([snap(), future])))
        only_future = run([future])
        self.assertEqual(only_future.status, sc.NO_SNAPSHOT)

    def test_invalid_latest_blocks_and_does_not_fall_back(self):
        older_valid = snap(sid="old", received=RECEIVED - timedelta(seconds=5), ranked=RANKED - timedelta(seconds=5))
        bad = snap(sid="bad", ranked=RECEIVED + timedelta(seconds=1))  # provider time after receipt
        result = run([older_valid, bad])
        self.assertEqual(result.status, sc.INVALID_PROVIDER_TIME)
        self.assertEqual(result.candidates, ())
        unknown_latest = snap(sid="unk", ranked=None, received=RECEIVED + timedelta(seconds=1))
        self.assertEqual(run([snap(), unknown_latest]).status, sc.UNKNOWN_PROVIDER_TIME)

    def test_stale_boundaries_are_exclusive(self):
        age = ScoutConfig().max_snapshot_age
        self.assertEqual(run(as_of=RECEIVED + age - timedelta(seconds=1)).status, sc.OK)
        stale = run(as_of=RECEIVED + age)
        self.assertEqual(stale.status, sc.STALE)
        self.assertEqual(stale.candidates, ())
        self.assertIn("NO_NEW_CANDIDATES:STALE", stale.shortage_reasons)

    def test_fresh_receipt_cannot_revive_old_provider_basis(self):
        s = snap(received=RANKED + timedelta(minutes=5))
        result = run([s], as_of=RANKED + timedelta(minutes=5, seconds=1))
        self.assertEqual(result.status, sc.STALE)
        self.assertIn((s.snapshot_id, "PROVIDER_BASIS_STALE"), result.snapshot_rejections)

    def test_day_rollover_uses_kst_session_date(self):
        # 14:59:50Z is 23:59:50 KST on 11-13; next as_of is 00:00:20 KST on 11-14.
        late = datetime(2026, 11, 13, 14, 59, 50, tzinfo=UTC)
        s = snap(ranked=late, received=late + timedelta(seconds=5))
        same_day = run([s], as_of=late + timedelta(seconds=8), inst=instruments(observed=late),
                       liq=liquidity(observed=late), day=dayone(observed=late),
                       pf=portfolio(observed_at=late))
        self.assertEqual(same_day.status, sc.OK)
        next_day = run([s], as_of=late + timedelta(seconds=30))
        self.assertEqual(next_day.status, sc.DAY_ROLLOVER)

    def test_provider_cross_mixing_is_rejected_explicitly(self):
        other = snap(provider="other", sid="o", received=RECEIVED + timedelta(seconds=1))
        result = run([snap(), other])
        self.assertEqual(result.status, sc.OK)
        self.assertIn(("o", "PROVIDER_MISMATCH"), result.snapshot_rejections)
        self.assertEqual(run([other]).status, sc.NO_SNAPSHOT)

    def test_snapshot_id_conflict_blocks(self):
        a = snap(sid="same")
        b = snap(raw_rows(3), sid="same")
        self.assertEqual(run([a, b]).status, sc.INVALID_SNAPSHOT)

    def test_fact_times_are_causal(self):
        base = run()
        future_facts = instruments(observed=AS_OF + timedelta(seconds=1), status="HALT")
        self.assertEqual(to_dict(base), to_dict(run(inst=instruments() + future_facts)))
        self.assertEqual(run(inst=future_facts).candidates, ())
        stale = run(inst=instruments(observed=AS_OF - ScoutConfig().max_fact_age))
        self.assertEqual(reasons(stale, sym(1)), ("INSTRUMENT_STALE",))
        for kind, kw in (("LIQUIDITY", dict(liq=liquidity(observed=AS_OF - timedelta(hours=1)))),
                         ("DAY01_READINESS", dict(day=dayone(observed=AS_OF - timedelta(hours=1))))):
            self.assertIn(f"{kind}_STALE", reasons(run(**kw), sym(1)))

    def test_conflicting_facts_at_same_time_fail_closed_regardless_of_order(self):
        good = instruments()
        bad = instruments(status="HALT")
        for order in (good + bad, bad + good):
            result = run(inst=order)
            self.assertEqual(result.candidates, ())
            self.assertEqual(reasons(result, sym(1)), ("INSTRUMENT_CONFLICT",))
        identical = run(inst=good + instruments())
        self.assertEqual(len(identical.candidates), 12)
        # A newer fact resolves an older conflict.
        newer = instruments(observed=OBSERVED + timedelta(seconds=5))
        self.assertEqual(len(run(inst=good + bad + newer).candidates), 12)

    def test_entry_expiry_is_earliest_bound_and_exclusive(self):
        result = run(pf=portfolio(observed_at=AS_OF - timedelta(seconds=50)))
        expected = min(AS_OF - timedelta(seconds=50) + ScoutConfig().max_portfolio_age,
                       RECEIVED + ScoutConfig().max_snapshot_age,
                       OBSERVED + ScoutConfig().max_fact_age)
        self.assertEqual(result.candidates[0].entry_expires_at, expected)
        # At expiry the same inputs are already stale (exclusive boundary).
        self.assertEqual(run(pf=portfolio(observed_at=AS_OF - timedelta(seconds=50)),
                             as_of=expected).candidates, ())


class RankIntegrityTests(unittest.TestCase):
    def test_duplicate_rank_and_symbol_exclude_all_involved_rows(self):
        rows = raw_rows(60)
        rows[4]["rank"] = 4                      # rank 4 twice (S004, S005)
        rows[9]["symbol"] = sym(9)               # no-op
        rows[19]["symbol"] = sym(18)             # S018 twice (ranks 18, 20)
        result = run([snap(rows)], config=ScoutConfig(target_count=15))
        self.assertIn("CONFLICT_RANK", reasons(result, sym(4)))
        self.assertIn("CONFLICT_RANK", reasons(result, sym(5)))
        self.assertTrue(result.coverage.excluded_conflict_rows >= 2)
        self.assertIn(5, result.coverage.missing_ranks)
        self.assertFalse(result.coverage.complete)
        symbols = {c.symbol for c in result.candidates}
        self.assertFalse({sym(4), sym(5)} & symbols)
        rows = raw_rows(60)
        rows[19]["symbol"] = sym(18)
        res2 = run([snap(rows)], config=ScoutConfig(target_count=15))
        self.assertNotIn(sym(18), {c.symbol for c in res2.candidates})
        self.assertIn("CONFLICT_SYMBOL", reasons(res2, sym(18)))

    def test_missing_ranks_reported_and_rank_51_never_promoted(self):
        rows = [r for r in raw_rows(60) if r["rank"] not in (3, 7)]
        result = run([snap(rows)], config=ScoutConfig(target_count=15))
        self.assertEqual(result.coverage.missing_ranks, (3, 7))
        self.assertEqual(result.coverage.present, 48)
        self.assertFalse(result.coverage.complete)
        self.assertFalse(result.coverage.marketwide_complete)
        self.assertTrue(all(c.rank <= 50 for c in result.candidates))
        self.assertIn("PARTIAL_RANK_COVERAGE:48/50", result.shortage_reasons)
        # Only 1..10 provided: rank is never backfilled from 51+ or invented.
        tiny = run([snap(raw_rows(10))])
        self.assertEqual(len(tiny.candidates), 10)
        self.assertEqual(tiny.coverage.present, 10)

    def test_even_complete_coverage_is_not_marketwide_and_partial_request_is_partial(self):
        self.assertFalse(run().coverage.marketwide_complete)
        self.assertTrue(run().coverage.complete)
        self.assertFalse(run([snap(requested_count=30)]).coverage.complete)

    def test_ranking_depth_is_bounded_to_top50(self):
        for depth in (0, 51, True, 50.0):
            with self.assertRaises(ValueError):
                ScoutConfig(ranking_depth=depth)
        result = run(config=ScoutConfig(ranking_depth=20, target_count=15))
        self.assertTrue(all(c.rank <= 20 for c in result.candidates))


class QualityLiquidityReadinessTests(unittest.TestCase):
    def test_quality_gates_and_unknown_fail_closed(self):
        cases = {
            "NOT_KOSPI_KOSDAQ": dict(market="NYSE"), "NOT_KRW": dict(currency="USD"),
            "NOT_STOCK": dict(security_type="ETF"), "NOT_COMMON_SHARE": dict(is_common_share=False),
            "NOT_ACTIVE": dict(status="HALT"), "SUSPENDED_OR_UNKNOWN": dict(krx_trading_suspended=True),
            "LIQUIDATION_OR_UNKNOWN": dict(liquidation_trading=True),
        }
        for reason, override in cases.items():
            result = run(inst=instruments(**override))
            self.assertEqual(result.candidates, (), reason)
            self.assertIn(reason, reasons(result, sym(1)))
        for unknown in (dict(market=None), dict(is_common_share=None), dict(krx_trading_suspended=None),
                        dict(liquidation_trading=None), dict(status=None)):
            self.assertEqual(run(inst=instruments(**unknown)).candidates, ())
        self.assertEqual(run(inst=[]).candidates, ())
        self.assertEqual(reasons(run(inst=[]), sym(1)), ("INSTRUMENT_MISSING",))
        # A KOSDAQ stock is fine.
        self.assertEqual(len(run(inst=instruments(market="KOSDAQ")).candidates), 12)

    def test_liquidity_is_required_by_default(self):
        self.assertTrue(ScoutConfig().require_liquidity)
        none = run(liq=[])
        self.assertEqual(none.candidates, ())
        self.assertEqual(reasons(none, sym(1)), ("LIQUIDITY_MISSING",))
        empty = run(liq=liquidity(turnover=None, volume=None, spread_pct=None))
        self.assertEqual(empty.candidates, ())
        self.assertTrue({"LIQUIDITY_TURNOVER_UNKNOWN", "LIQUIDITY_VOLUME_UNKNOWN",
                         "LIQUIDITY_SPREAD_UNKNOWN"} <= set(reasons(empty, sym(1))))
        for override, why in ((dict(turnover="1"), "LIQUIDITY_TURNOVER_BELOW_MIN"),
                              (dict(volume="0"), "LIQUIDITY_VOLUME_BELOW_MIN"),
                              (dict(spread_pct="5"), "LIQUIDITY_SPREAD_TOO_WIDE")):
            self.assertIn(why, reasons(run(liq=liquidity(**override)), sym(1)))
        # Thresholds exactly at the limit pass.
        self.assertEqual(len(run(liq=liquidity(turnover="10000000000", volume="1", spread_pct="1")).candidates), 12)

    def test_liquidity_config_cannot_be_hollow(self):
        for kw in (dict(min_turnover=None), dict(min_volume=None), dict(max_spread_pct=None),
                   dict(min_turnover=0), dict(min_volume=0)):
            with self.assertRaises(ValueError):
                ScoutConfig(**kw)

    def test_explicit_liquidity_opt_out_is_marked_unverified(self):
        cfg = ScoutConfig(require_liquidity=False, min_turnover=None, min_volume=None, max_spread_pct=None)
        result = run(liq=[], config=cfg)
        self.assertEqual(len(result.candidates), 12)
        for cand in result.candidates:
            self.assertFalse(cand.fully_eligible)
            self.assertIn("LIQUIDITY_UNVERIFIED", cand.verification_gaps)

    def test_dayone_readiness_is_required_and_explained(self):
        for override, why in ((dict(completed_1m_bars=3), "DAY01_BARS_INSUFFICIENT"),
                              (dict(opening_range_ready=False), "DAY01_OPENING_RANGE_NOT_READY"),
                              (dict(vwap_ready=False), "DAY01_VWAP_NOT_READY"),
                              (dict(vwap_ready=None), "DAY01_READINESS_UNKNOWN"),
                              (dict(completed_1m_bars=None), "DAY01_READINESS_UNKNOWN")):
            self.assertIn(why, reasons(run(day=dayone(**override)), sym(1)))
        self.assertIn("DAY01_READINESS_MISSING", reasons(run(day=[]), sym(1)))
        unverified = run(day=[], config=ScoutConfig(require_dayone_readiness=False))
        self.assertIn("DAY01_READINESS_UNVERIFIED", unverified.candidates[0].verification_gaps)


class CapitalTests(unittest.TestCase):
    def test_expensive_stock_is_not_bypassed_and_limits_are_reported_separately(self):
        rows = raw_rows(60, price=3_000_000)             # one share exceeds every limit and cash
        result = run([snap(rows)], pf=portfolio(cash="1000000"))
        self.assertEqual(result.candidates, ())
        why = set(reasons(result, sym(1)))
        self.assertTrue({"CAPITAL_ORDER_LIMIT_FAILED", "CAPITAL_SYMBOL_EXPOSURE_FAILED",
                         "CAPITAL_CASH_FAILED"} <= why)

    def test_fee_and_slippage_are_included(self):
        # 99_900 * 1.001 * 1.00015 = 100_014.98... just over a 100_000 order limit.
        risk = SimpleNamespace(max_order_amount=Decimal(100_000), max_symbol_amount=Decimal(10**9),
                               max_total_investment=Decimal(10**9))
        result = run([snap(raw_rows(60, price=99_900))], risk=risk)
        self.assertEqual(result.candidates, ())
        flat = ScoutConfig(fee_rate=0, buy_slippage_rate=0)
        self.assertEqual(len(run([snap(raw_rows(60, price=99_900))], risk=risk, config=flat).candidates), 12)

    def test_exposure_equal_to_limit_allowed_and_daily_loss_equal_limit_blocks(self):
        flat = ScoutConfig(fee_rate=0, buy_slippage_rate=0)
        risk = SimpleNamespace(max_order_amount=Decimal(10000), max_symbol_amount=Decimal(10000),
                               max_total_investment=Decimal(10000))
        self.assertEqual(len(run(risk=risk, config=flat).candidates), 12)
        blocked = run(pf=portfolio(daily_loss="100", daily_loss_limit="100"))
        self.assertEqual(blocked.candidates, ())
        self.assertIn("CAPITAL_DAILY_LOSS_FAILED", reasons(blocked, sym(1)))
        self.assertEqual(len(run(pf=portfolio(daily_loss="99", daily_loss_limit="100")).candidates), 12)

    def test_cash_reservations_min_cash_and_ratio(self):
        self.assertEqual(run(pf=portfolio(cash="20000", reserved_cash="15000")).candidates, ())
        self.assertEqual(run(pf=portfolio(cash="20000", min_cash="15000")).candidates, ())
        self.assertEqual(len(run(pf=portfolio(cash="20000", min_cash="5000")).candidates), 12)
        ratio_ok = portfolio(cash="1000000", total_asset="2000000", min_cash_ratio_pct="40")
        self.assertEqual(len(run(pf=ratio_ok).candidates), 12)
        ratio_bad = portfolio(cash="1000000", total_asset="2000000", min_cash_ratio_pct="60")
        self.assertIn("CAPITAL_CASH_RATIO_FAILED", reasons(run(pf=ratio_bad), sym(1)))
        no_total = portfolio(min_cash_ratio_pct="10")
        self.assertIn("CAPITAL_CASH_RATIO_FAILED:TOTAL_ASSET_UNKNOWN", reasons(run(pf=no_total), sym(1)))

    def test_pending_orders_need_explicit_reserved_exposure_and_slots(self):
        self.assertEqual(run(pf=portfolio(pending_orders_known=False)).candidates, ())
        self.assertEqual(run(pf=portfolio(max_positions=3, reserved_slots=3)).candidates, ())
        self.assertEqual(len(run(pf=portfolio(max_positions=3, reserved_slots=2)).candidates), 12)
        sym_block = run(pf=portfolio(reserved_symbol_exposure=[(sym(1), "2000000")]))
        self.assertIn("CAPITAL_SYMBOL_EXPOSURE_FAILED", reasons(sym_block, sym(1)))
        self.assertEqual(sym_block.candidates[0].symbol, sym(2))
        total_block = run(pf=portfolio(reserved_total_exposure="5000000"))
        self.assertEqual(total_block.candidates, ())

    def test_max_positions_and_unknown_held_values_never_count_as_zero(self):
        held = [Position(sym(60), 1, "100", OBSERVED, "100", OBSERVED)]
        self.assertEqual(run(pf=portfolio(positions=held, max_positions=1)).candidates, ())
        bad_values = [
            Position(sym(60), 1),                                              # missing value
            Position(sym(60), 1, "NaN", OBSERVED),                             # NaN
            Position(sym(60), 1, "100", AS_OF + timedelta(seconds=1)),         # future
            Position(sym(60), 1, "100", AS_OF - timedelta(hours=1)),           # stale
        ]
        for pos in bad_values:
            result = run(pf=portfolio(positions=[pos]))
            self.assertEqual(result.candidates, ())
            self.assertIn("CAPITAL_TOTAL_INVESTMENT_FAILED:INVESTED_UNKNOWN", reasons(result, sym(1)))
            self.assertEqual([h.symbol for h in result.held], [sym(60)])

    def test_risk_config_duck_typing_rejects_unsafe_limits(self):
        good = dict(max_order_amount=1_000_000, max_symbol_amount=2_000_000, max_total_investment=5_000_000)
        for bad in (None, SimpleNamespace(), SimpleNamespace(**{**good, "max_order_amount": 1.5}),
                    SimpleNamespace(**{**good, "max_order_amount": True}),
                    SimpleNamespace(**{**good, "max_symbol_amount": float("nan")}),
                    SimpleNamespace(**{**good, "max_total_investment": Decimal("Infinity")}),
                    SimpleNamespace(**{**good, "max_total_investment": -1}),
                    SimpleNamespace(**{**good, "max_symbol_amount": "abc"})):
            result = run(risk=bad)
            self.assertEqual(result.candidates, (), bad)
            self.assertTrue(any("LIMIT_INVALID" in r or "RISK" in r for r in reasons(result, sym(1))))
        self.assertEqual(len(run(risk=SimpleNamespace(**good)).candidates), 12)

    def test_capital_opt_out_marks_candidates_unverified(self):
        cfg = ScoutConfig(require_capital_check=False)
        no_port = select_watchlist(AS_OF, [snap()], instruments=instruments(), liquidity=liquidity(),
                                   dayone=dayone(), portfolio=None, config=cfg, risk_config=RISK)
        self.assertEqual(len(no_port.candidates), 12)
        self.assertFalse(no_port.candidates[0].fully_eligible)
        self.assertIn("CAPITAL_UNVERIFIED", no_port.candidates[0].verification_gaps)

    def test_individually_affordable_does_not_promise_joint_orders(self):
        # Cash fits ~one order: all 12 are individually affordable, flagged as not jointly guaranteed.
        result = run(pf=portfolio(cash="12000"))
        self.assertEqual(len(result.candidates), 12)
        self.assertTrue(all(not c.joint_orders_guaranteed for c in result.candidates))


class PortfolioAndHeldTests(unittest.TestCase):
    def test_missing_stale_future_portfolio_gives_no_new_candidates(self):
        stale = run(pf=portfolio(observed_at=AS_OF - ScoutConfig().max_portfolio_age))
        self.assertEqual(stale.portfolio_status, "STALE")
        self.assertEqual(stale.candidates, ())
        self.assertEqual(reasons(stale, sym(1)), ("PORTFOLIO_STALE",))
        future = run(pf=portfolio(observed_at=AS_OF + timedelta(seconds=1)))
        self.assertEqual((future.portfolio_status, future.candidates), ("MISSING", ()))
        missing = select_watchlist(AS_OF, [snap()], instruments=instruments(), liquidity=liquidity(),
                                   dayone=dayone(), portfolio=None, risk_config=RISK)
        self.assertEqual((missing.portfolio_status, missing.candidates), ("MISSING", ()))

    def test_future_only_portfolio_is_indistinguishable_from_none(self):
        future = portfolio(observed_at=AS_OF + timedelta(seconds=1), cash=0,
                           positions=[Position("FUTURE_ONLY", 3)])
        none = select_watchlist(AS_OF, [snap()], instruments=instruments(), liquidity=liquidity(),
                                dayone=dayone(), portfolio=None, risk_config=RISK)
        for pf in (future, [future]):
            leaked = select_watchlist(AS_OF, [snap()], instruments=instruments(), liquidity=liquidity(),
                                      dayone=dayone(), portfolio=pf, risk_config=RISK)
            self.assertEqual(to_dict(leaked), to_dict(none))
            self.assertEqual((leaked.held, leaked.watch_union), ((), ()))
            self.assertNotIn("FUTURE_ONLY", json.dumps(to_dict(leaked)))
        # Past-known stale portfolio keeps monitoring its own positions; a future one adds nothing.
        stale = portfolio(observed_at=AS_OF - timedelta(hours=1), positions=[Position("OLD001", 2)])
        both = run(pf=[stale, future])
        self.assertEqual(to_dict(both), to_dict(run(pf=stale)))
        self.assertEqual([h.symbol for h in both.held], ["OLD001"])

    def test_later_portfolio_ignored_when_earlier_known(self):
        early = portfolio()
        late = portfolio(observed_at=AS_OF + timedelta(seconds=5), cash="1")
        self.assertEqual(to_dict(run(pf=early)), to_dict(run(pf=[early, late])))

    def test_conflicting_portfolios_fail_closed_but_keep_monitoring(self):
        a = portfolio(positions=[Position(sym(55), 3)])
        b = portfolio(positions=[Position(sym(56), 4)], cash="5")
        for order in ([a, b], [b, a]):
            result = run(pf=order)
            self.assertEqual(result.portfolio_status, "CONFLICT")
            self.assertEqual(result.candidates, ())
            self.assertEqual([h.symbol for h in result.held], [sym(55), sym(56)])

    def test_held_positions_are_always_monitored_independent_of_everything(self):
        positions = [Position("OUT001", 10, "1000", OBSERVED, "100", OBSERVED),    # not in ranking
                     Position("ETF001", 5),                                         # unknown values
                     Position(sym(2), 7, "1000", OBSERVED, "100", AS_OF - timedelta(hours=2))]
        # Stale portfolio + no usable ranking snapshot + unaffordable: still monitored.
        result = run([], pf=portfolio(positions=positions, observed_at=AS_OF - timedelta(hours=1), cash="0"))
        self.assertEqual([h.symbol for h in result.held], ["ETF001", "OUT001", sym(2)])
        flags = {h.symbol: set(h.flags) for h in result.held}
        self.assertTrue({"PORTFOLIO_STALE", "SNAPSHOT_NO_SNAPSHOT", "PRICE_UNKNOWN",
                         "MARKET_VALUE_UNKNOWN"} <= flags["ETF001"])
        self.assertIn("PRICE_STALE", flags[sym(2)])
        self.assertTrue(all(h.monitor_only for h in result.held))
        self.assertFalse(hasattr(result.held[0], "sell") or hasattr(result.held[0], "action"))

    def test_held_rank_exit_and_budget_failure_never_drop_the_position(self):
        positions = [Position(sym(r), 1, "100", OBSERVED, "100", OBSERVED) for r in (1, 40)]
        in_rank = run(pf=portfolio(positions=positions))
        self.assertEqual([h.rank for h in in_rank.held], [1, 40])
        rows = [r for r in raw_rows(60) if r["rank"] not in (1, 40)]
        gone = run([snap(rows)], pf=portfolio(positions=positions, cash="1"))
        self.assertEqual([h.symbol for h in gone.held], [sym(1), sym(40)])
        self.assertTrue(all(h.rank is None for h in gone.held))
        self.assertTrue(all("RANK_UNKNOWN_OR_OUTSIDE_PROVIDED_RANKING" in h.flags for h in gone.held))

    def test_future_held_price_is_not_used(self):
        pos = Position(sym(1), 1, "100", OBSERVED, "123", AS_OF + timedelta(seconds=1))
        held = run(pf=portfolio(positions=[pos])).held[0]
        self.assertEqual((held.price, held.price_status), (None, "FUTURE"))

    def test_more_than_15_and_beyond_top50_holdings_and_union_has_no_duplicates(self):
        positions = [Position(f"H{i:03d}", 1, "100", OBSERVED, "100", OBSERVED) for i in range(30)]
        positions.append(Position(sym(2), 1, "100", OBSERVED, "100", OBSERVED))   # also top-ranked
        result = run(pf=portfolio(positions=positions))
        self.assertEqual(len(result.held), 31)
        self.assertNotIn(sym(2), [c.symbol for c in result.candidates])
        self.assertEqual(reasons(result, sym(2)), ("HELD_MONITOR_ONLY",))
        union = [w.symbol for w in result.watch_union]
        self.assertEqual(len(union), len(set(union)))
        self.assertEqual(len(union), len(result.candidates) + 31)
        self.assertTrue(all(w.sources in (("CANDIDATE",), ("HELD",)) for w in result.watch_union))
        self.assertFalse(any("signal" in f.name or "buy" in f.name for f in dataclasses.fields(sc.WatchEntry)))

    def test_position_validation(self):
        with self.assertRaises(ValueError):
            Position(sym(1), True)
        with self.assertRaises(ValueError):
            Position("", 1)
        with self.assertRaises(ValueError):
            portfolio(positions=[Position(sym(1), 1), Position(sym(1), 2)])
        with self.assertRaises(ValueError):
            portfolio(cash=1.5)
        with self.assertRaises(ValueError):
            portfolio(cash=True)
        self.assertEqual(Position(sym(1), 1, 1.5).market_value, None)
        self.assertIn("MARKET_VALUE_INVALID", Position(sym(1), 1, 1.5).defects)


class ShortageTests(unittest.TestCase):
    def test_shortage_is_reported_without_unsafe_padding(self):
        liq = liquidity(count=5)                 # only ranks 1..5 have liquidity facts
        result = run(liq=liq)
        self.assertEqual(len(result.candidates), 5)
        self.assertEqual(result.shortage_reasons[0], "SHORTAGE:5/12")
        self.assertIn("LIQUIDITY_MISSING=45", result.shortage_reasons)
        self.assertEqual(run(liq=[]).candidates, ())


class RefreshTests(unittest.TestCase):
    def go(self, state, as_of=AS_OF, new=(), **kw):
        args = dict(instruments=instruments(), liquidity=liquidity(), dayone=dayone(),
                    portfolio=portfolio(), risk_config=RISK)
        args.update(kw)
        return refresh(state, as_of, new_snapshots=new, **args)

    def test_initial_unchanged_and_changed_ranking(self):
        s0 = ScoutState.empty()
        s1, r1 = self.go(s0, new=[snap(sid="a")])
        self.assertEqual(len(r1.added), 12)
        self.assertEqual((r1.removed, r1.unchanged), ((), ()))
        self.assertTrue(r1.snapshot_changed)
        s2, r2 = self.go(s1, as_of=AS_OF + timedelta(seconds=1))
        self.assertEqual((r2.added, r2.removed), ((), ()))
        self.assertEqual(len(r2.unchanged), 12)
        self.assertFalse(r2.snapshot_changed)
        self.assertIsNone(s0.last_as_of)                      # input state never mutated
        rows = raw_rows(60)
        rows[0], rows[12] = {**rows[12], "rank": 1}, {**rows[0], "rank": 13}
        new = snap(rows, sid="b", ranked=RANKED + timedelta(seconds=20), received=RECEIVED + timedelta(seconds=20))
        s3, r3 = self.go(s2, as_of=AS_OF + timedelta(seconds=30), new=[new],
                         instruments=instruments(observed=OBSERVED + timedelta(seconds=30)),
                         liquidity=liquidity(observed=OBSERVED + timedelta(seconds=30)),
                         dayone=dayone(observed=OBSERVED + timedelta(seconds=30)),
                         portfolio=portfolio(observed_at=OBSERVED + timedelta(seconds=30)))
        self.assertEqual(r3.added, (sym(13),))  # S013 moved into rank 1; S001 fell to rank 13
        self.assertEqual(r3.removed, (sym(1),))
        self.assertTrue(r3.snapshot_changed)

    def test_idempotent_and_id_conflict(self):
        s1, _ = self.go(ScoutState.empty(), new=[snap(sid="a")])
        s2, r2 = self.go(s1, new=[snap(sid="a")])
        self.assertEqual((r2.ingested, r2.rejected), ((), ()))
        self.assertEqual(len(s2.snapshots), 1)
        s3, r3 = self.go(s2, new=[snap(raw_rows(3), sid="a")])
        # Disputed content is stored side by side and fails closed at selection; no first-wins.
        self.assertEqual(r3.rejected, (("a", "SNAPSHOT_CONFLICT"),))
        self.assertEqual((r3.result.status, r3.result.candidates), (sc.INVALID_SNAPSHOT, ()))
        self.assertEqual(len(s3.snapshots), 2)

    def test_as_of_regression_is_rejected_and_state_unchanged(self):
        s1, _ = self.go(ScoutState.empty(), new=[snap()])
        s2, report = self.go(s1, as_of=AS_OF - timedelta(seconds=1))
        self.assertEqual(report.status, "AS_OF_REGRESSION")
        self.assertIsNone(report.result)
        self.assertIs(s2, s1)

    def test_out_of_order_older_arrival_never_becomes_latest(self):
        newer = snap(sid="n", received=RECEIVED, ranked=RANKED)
        older = snap(raw_rows(30), sid="o", received=RECEIVED - timedelta(seconds=8),
                     ranked=RANKED - timedelta(seconds=8))
        s1, _ = self.go(ScoutState.empty(), new=[newer])
        s2, r2 = self.go(s1, new=[older])
        self.assertEqual(r2.result.snapshot.snapshot_id, "n")
        self.assertFalse(r2.snapshot_changed)
        self.assertEqual(len(s2.snapshots), 2)

    def test_repeated_cached_rankedat_does_not_renew_original_receipt(self):
        first = snap(sid="a", received=RECEIVED)
        again = snap(sid="b", received=RECEIVED + timedelta(seconds=90))   # same content, newer clock
        s1, _ = self.go(ScoutState.empty(), new=[first])
        s2, r2 = self.go(s1, as_of=RECEIVED + timedelta(seconds=130), new=[again])
        self.assertEqual(len(s2.snapshots), 1)
        self.assertEqual(s2.snapshots[0].received_at, RECEIVED)
        self.assertEqual(r2.result.status, sc.STALE)
        self.assertEqual(r2.result.candidates, ())

    def test_same_rankedat_with_different_content_is_rejected(self):
        s1, _ = self.go(ScoutState.empty(), new=[snap(sid="a")])
        _, r2 = self.go(s1, new=[snap(raw_rows(7), sid="b", received=RECEIVED + timedelta(seconds=2))])
        self.assertEqual(r2.rejected, (("b", "SNAPSHOT_CONFLICT"),))
        self.assertEqual(r2.result.status, sc.INVALID_SNAPSHOT)

    def test_stale_state_never_masquerades_as_fresh_and_held_survives(self):
        pos = Position(sym(60), 1, "100", OBSERVED, "100", OBSERVED)
        s1, _ = self.go(ScoutState.empty(), new=[snap()], portfolio=portfolio(positions=[pos]))
        later = AS_OF + timedelta(minutes=10)
        s2, r2 = self.go(s1, as_of=later, portfolio=portfolio(observed_at=later, positions=[pos]),
                         instruments=instruments(observed=later), liquidity=liquidity(observed=later),
                         dayone=dayone(observed=later))
        self.assertEqual(r2.result.status, sc.STALE)
        self.assertEqual(r2.result.candidates, ())
        self.assertEqual(len(r2.removed), 12)
        self.assertEqual([h.symbol for h in r2.result.held], [sym(60)])

    def test_day_rollover_purges_old_snapshots_and_history_is_bounded(self):
        s1, _ = self.go(ScoutState.empty(max_history=2), new=[snap(sid="a")])
        tomorrow = AS_OF + timedelta(days=1)
        s2, r2 = self.go(s1, as_of=tomorrow)
        self.assertEqual(s2.snapshots, ())
        self.assertEqual(r2.result.status, sc.NO_SNAPSHOT)
        many = [snap(raw_rows(5), sid=f"s{i}", ranked=RANKED + timedelta(seconds=i),
                     received=RECEIVED + timedelta(seconds=i)) for i in range(5)]
        s3, _ = self.go(ScoutState.empty(max_history=2), new=many)
        self.assertEqual([s.snapshot_id for s in s3.snapshots], ["s3", "s4"])


class IngestFetchTests(unittest.TestCase):
    def payload(self, rows=None):
        return {"result": {"rankedAt": "2026-11-13T01:00:00Z", "rankings": rows or raw_rows(5)}}

    def test_cached_payload_keeps_original_receipt(self):
        clock = lambda: RECEIVED + timedelta(minutes=10)
        state, out = ingest_fetch(ScoutState.empty(), lambda: FetchResult(self.payload(), RECEIVED), clock=clock)
        self.assertEqual(out.status, "INGESTED")
        self.assertEqual(state.snapshots[0].received_at, RECEIVED)
        state2, out2 = ingest_fetch(state, lambda: FetchResult(self.payload(), RECEIVED + timedelta(minutes=5)),
                                    clock=clock)
        self.assertEqual(out2.status, "DUPLICATE")
        self.assertEqual(state2.snapshots[0].received_at, RECEIVED)

    def test_bare_payload_is_rejected_not_stamped_now(self):
        for bare in (self.payload(), ("2026-11-13T01:00:00Z", raw_rows(5))):
            state, out = ingest_fetch(ScoutState.empty(), lambda b=bare: b, clock=lambda: RECEIVED)
            self.assertEqual((out.status, out.reason), ("REJECTED", "FETCH_RESULT_REQUIRED"))
            self.assertEqual(state.snapshots, ())

    def test_exceptions_and_timeouts_fail_closed_with_state_unchanged(self):
        base, _ = ingest_fetch(ScoutState.empty(), lambda: FetchResult(self.payload(), RECEIVED),
                               clock=lambda: RECEIVED)

        def boom():
            raise TimeoutError("secret-token-123")

        state, out = ingest_fetch(base, boom, clock=lambda: RECEIVED)
        self.assertIs(state, base)
        self.assertEqual((out.status, out.reason), ("FETCH_FAILED", "TimeoutError"))
        self.assertNotIn("secret", repr(out))
        state, out = ingest_fetch(base, lambda: FetchResult({"bad": 1}, RECEIVED), clock=lambda: RECEIVED)
        self.assertEqual((out.status, state is base), ("FETCH_FAILED", True))

    def test_future_receipt_is_rejected(self):
        state, out = ingest_fetch(ScoutState.empty(), lambda: FetchResult(self.payload(), RECEIVED + timedelta(hours=1)),
                                  clock=lambda: RECEIVED)
        self.assertEqual((out.status, out.reason), ("REJECTED", "RECEIPT_IN_FUTURE"))
        self.assertEqual(state.snapshots, ())


class ReviewDefectRegressionTests(unittest.TestCase):
    """Own regressions for independent-review defects D-S2..D-S7 (generalized, not one-offs)."""

    def go(self, state, new=(), as_of=AS_OF):
        return refresh(state, as_of, new_snapshots=new, instruments=instruments(), liquidity=liquidity(),
                       dayone=dayone(), portfolio=portfolio(), risk_config=RISK)

    def test_d_s2_future_snapshots_never_evict_or_change_known_history(self):
        future = tuple(snap(raw_rows(5, price=101 + i), sid=f"f{i}", ranked=AS_OF + timedelta(seconds=i),
                            received=AS_OF + timedelta(seconds=i + 1)) for i in range(8))
        base_state, base = self.go(ScoutState.empty(max_history=2), [snap()])
        for batch in (future, tuple(reversed(future))):
            state, appended = self.go(ScoutState.empty(max_history=2), (snap(),) + batch)
            self.assertEqual(base.result, appended.result)
            self.assertEqual([x.snapshot_id for x in state.snapshots if x.received_at <= AS_OF],
                             [x.snapshot_id for x in base_state.snapshots])
            self.assertLessEqual(len(state.snapshots), 4)
        # Fed across refreshes, known history is still intact and future ones become known later.
        state, _ = self.go(ScoutState.empty(max_history=2), (snap(),) + future)
        # (The not-yet-known buffer is bounded by max_history, earliest first.)
        state, later = self.go(state, as_of=AS_OF + timedelta(seconds=2))
        self.assertEqual(later.result.snapshot.snapshot_id, "f1")

    def test_d_s3_conflicting_content_never_first_wins(self):
        a = snap(sid="x")
        b = snap(raw_rows(60, price=999), sid="x")
        c = snap(raw_rows(60, price=777), sid="y")   # other id, same provider basis
        for batch in ([a, b], [b, a], [a, c], [c, a]):
            _, report = self.go(ScoutState.empty(), batch)
            self.assertEqual(report.result.status, sc.INVALID_SNAPSHOT)
            self.assertEqual(report.result.candidates, ())
        r1 = self.go(ScoutState.empty(), [a, b])[1].result
        r2 = self.go(ScoutState.empty(), [b, a])[1].result
        self.assertEqual(r1, r2)
        # History versus new batch is symmetric as well.
        s_a, _ = self.go(ScoutState.empty(), [a])
        s_b, _ = self.go(ScoutState.empty(), [b])
        self.assertEqual(self.go(s_a, [b])[1].result, self.go(s_b, [a])[1].result)
        self.assertEqual(self.go(s_a, [b])[1].result.status, sc.INVALID_SNAPSHOT)
        # Held positions stay monitored through a disputed ranking.
        pos = Position("H001", 2)
        held = refresh(ScoutState.empty(), AS_OF, new_snapshots=[a, b], instruments=instruments(),
                       liquidity=liquidity(), dayone=dayone(), portfolio=portfolio(positions=[pos]),
                       risk_config=RISK)[1].result.held
        self.assertEqual([h.symbol for h in held], ["H001"])

    def test_d_s4_earliest_receipt_wins_in_state_and_direct_selection(self):
        early = snap(sid="same", received=RECEIVED)
        late = snap(sid="same", received=RECEIVED + timedelta(seconds=40))
        for batch in ([early, late], [late, early]):
            state, _ = self.go(ScoutState.empty(), batch)
            self.assertEqual([x.received_at for x in state.snapshots], [RECEIVED])
        # History already holding the LATE copy is corrected by the earlier original.
        s_late, _ = self.go(ScoutState.empty(), [late])
        s_fixed, _ = self.go(s_late, [early])
        self.assertEqual(s_fixed.snapshots[0].received_at, RECEIVED)
        # Direct selection: same content under different ids, earliest receipt survives.
        other = snap(sid="other", received=RECEIVED + timedelta(seconds=40))
        for order in ([early, other], [other, early]):
            self.assertEqual(run(order, as_of=RECEIVED + timedelta(seconds=45)).snapshot.received_at, RECEIVED)
        # A later re-delivery never freshens it: stale by the ORIGINAL receipt.
        stale_at = RECEIVED + ScoutConfig().max_snapshot_age
        self.assertEqual(run([early, other], as_of=stale_at).status, sc.STALE)
        # Future copies are dropped before canonicalization.
        future_copy = snap(sid="fc", received=AS_OF + timedelta(seconds=5))
        self.assertEqual(to_dict(run([early])), to_dict(run([future_copy, early])))

    def test_d_s5_conflicting_portfolio_fields_are_unknown_not_input_order(self):
        def pf(price, value):
            return portfolio(positions=[Position("OLD", 1, value, OBSERVED, price, OBSERVED)])
        a, b = pf("100", "100"), pf("200", "200")
        for order in ([a, b], [b, a]):
            result = run(pf=order)
            held = result.held[0]
            self.assertEqual((held.price, held.price_status), (None, "UNKNOWN"))
            self.assertIn("PRICE_CONFLICT", held.flags)
            self.assertIn("MARKET_VALUE_CONFLICT", held.flags)
            self.assertEqual(result.candidates, ())
        self.assertEqual(run(pf=[a, b]), run(pf=[b, a]))
        # Quantity conflicts keep the larger quantity and say so; agreeing fields survive.
        q1 = portfolio(positions=[Position("OLD", 1, "5", OBSERVED, "9", OBSERVED)])
        q2 = portfolio(positions=[Position("OLD", 4, "5", OBSERVED, "9", OBSERVED)])
        merged = run(pf=[q1, q2]).held[0]
        self.assertEqual((merged.quantity, merged.price), (4, Decimal(9)))
        self.assertIn("QUANTITY_CONFLICT", merged.flags)
        self.assertEqual(run(pf=[q1, q2]), run(pf=[q2, q1]))

    def test_d_s6_public_constructors_are_strict_and_immutable(self):
        for bad_rank in (True, False, 0, -1, 1.0, "1"):
            with self.assertRaises(ValueError):
                sc.RankedRow(bad_rank, "X001", price=Decimal(100))
        for bad in (dict(price=1.5), dict(price=True), dict(price=Decimal("NaN")), dict(turnover=-1),
                    dict(symbol=" ")):
            with self.assertRaises(ValueError):
                sc.RankedRow(**{"rank": 1, "symbol": "X001", **bad})
        self.assertEqual(sc.RankedRow(1, "X001", change_rate="-1.5").change_rate, Decimal("-1.5"))
        base = snap(raw_rows(3))
        with self.assertRaises(ValueError):
            dataclasses.replace(base, rows=(sc.RankedRow(True, "X001"),))
        with self.assertRaises(ValueError):
            dataclasses.replace(base, rows=(sc.RankedRow(1, "X001", price=Decimal(5)),))   # digest mismatch
        mutable = list(base.rows)
        direct = sc.RankingSnapshot(base.snapshot_id, base.provider, base.source, base.received_at,
                                    base.provider_ranked_at, base.requested_count, mutable, [],
                                    base.content_digest)
        mutable.clear()
        self.assertIsInstance(direct.rows, tuple)
        self.assertEqual(direct.rows, base.rows)
        self.assertIsInstance(direct.defects, tuple)
        with self.assertRaises(ValueError):
            sc.RankingSnapshot("", "toss", sc.SOURCE, RECEIVED, RANKED, 100, (), (), "d")
        with self.assertRaises(ValueError):
            sc.RankingSnapshot("i", "toss", sc.SOURCE, datetime(2026, 1, 1), RANKED, 100, (), (), "d")
        with self.assertRaises(ValueError):
            ScoutState(snapshots=[object()])
        listy = ScoutState(snapshots=[base], last_candidates=["A"])
        self.assertEqual((type(listy.snapshots), type(listy.last_candidates)), (tuple, tuple))

    def test_d_s7_opt_out_is_never_fully_eligible_even_with_present_facts(self):
        for flag in ("require_liquidity", "require_dayone_readiness", "require_capital_check"):
            kwargs = {flag: False}
            if flag == "require_liquidity":
                kwargs.update(min_turnover=None, min_volume=None, max_spread_pct=None)
            result = run(config=ScoutConfig(**kwargs))       # all facts and portfolio present
            self.assertTrue(result.candidates, flag)
            for cand in result.candidates:
                self.assertFalse(cand.fully_eligible, flag)
                self.assertEqual(len(cand.verification_gaps), 1, flag)
        self.assertTrue(all(c.fully_eligible and not c.verification_gaps for c in run().candidates))


class ConfigValidationTests(unittest.TestCase):
    def test_strict_config(self):
        for kw in (dict(target_count=9), dict(target_count=16), dict(target_count=True),
                   dict(target_count=12.0), dict(quantity=0), dict(quantity=True), dict(quantity=1.0),
                   dict(fee_rate=0.001), dict(fee_rate=-1), dict(buy_slippage_rate=True),
                   dict(fee_rate="NaN"), dict(sort_mode="X"), dict(require_liquidity="yes"),
                   dict(max_snapshot_age=60), dict(min_completed_1m_bars=0), dict(provider="")):
            with self.assertRaises(ValueError, msg=str(kw)):
                ScoutConfig(**kw)
        self.assertEqual(ScoutConfig().quantity, 1)
        self.assertEqual(ScoutConfig().ranking_depth, 50)

    def test_quantity_scales_required_cash(self):
        risk = SimpleNamespace(max_order_amount=Decimal(25_000), max_symbol_amount=Decimal(10**9),
                               max_total_investment=Decimal(10**9))
        self.assertEqual(len(run(risk=risk, config=ScoutConfig(quantity=2)).candidates), 12)
        self.assertEqual(run(risk=risk, config=ScoutConfig(quantity=3)).candidates, ())

    def test_fact_validation(self):
        with self.assertRaises(ValueError):
            InstrumentFacts(sym(1), datetime(2026, 11, 13))
        with self.assertRaises(ValueError):
            InstrumentFacts(sym(1), OBSERVED, is_common_share="yes")
        with self.assertRaises(ValueError):
            LiquidityFacts(sym(1), OBSERVED, turnover=1.5)
        with self.assertRaises(ValueError):
            LiquidityFacts(sym(1), OBSERVED, volume=-1)
        with self.assertRaises(ValueError):
            DayOneFacts(sym(1), OBSERVED, completed_1m_bars=True)


class StaticContractTests(unittest.TestCase):
    ROOT = Path(__file__).resolve().parents[1]

    def test_module_imports_only_stdlib_and_risk_policy_and_has_no_side_effect_calls(self):
        tree = ast.parse((self.ROOT / "auto_trader" / "day01_scout.py").read_text(encoding="utf-8"))
        allowed_relative = {"risk_policy"}
        stdlib = {"__future__", "dataclasses", "hashlib", "json", "collections.abc", "datetime",
                  "decimal", "types", "typing"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                self.assertTrue(all(a.name in stdlib for a in node.names), ast.dump(node))
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    self.assertIn(node.module, allowed_relative)
                else:
                    self.assertIn(node.module, stdlib)
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                self.assertNotIn(node.func.attr, {"now", "utcnow", "today", "open", "connect", "urlopen"})
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                self.assertNotIn(node.func.id, {"open", "print", "exec", "eval", "input"})

    def test_existing_ranking_contract_is_still_what_the_spec_assumes(self):
        source = (self.ROOT / "auto_trader" / "brokers" / "toss" / "market_data.py").read_text(encoding="utf-8")
        for needle in ('"type": "MARKET_TRADING_AMOUNT"', '"marketCountry": "KR"', '"duration": "1d"',
                       '"excludeInvestmentCaution": "true"', '"count": 100', 'result.get("rankedAt")',
                       '"rankings"', 'tradingAmount', 'tradingVolume', '+ 30'):
            self.assertIn(needle, source)
        self.assertIn("excludeInvestmentCaution=true", sc.SOURCE)

    def test_fixture_matches_existing_response_keys(self):
        row = raw_rows(1)[0]
        self.assertEqual(set(row), {"rank", "symbol", "tradingAmount", "tradingVolume", "price"})
        self.assertEqual(set(row["price"]), {"lastPrice", "changeRate"})

    def test_check_type_is_plain_data(self):
        self.assertTrue(dataclasses.is_dataclass(Check))


if __name__ == "__main__":
    unittest.main()
